##############################################################################
#
# Copyright (c) 2009 Zope Foundation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
"""Compare the speed of different ZODB storages.

Opens the databases specified by a ZConfig file.

Splits into many processes to avoid contention over the global
interpreter lock.
"""
from __future__ import print_function, absolute_import


from io import StringIO

from .speedtest import SpeedTestData
from .speedtest import SpeedTestWorker
from .speedtest import pobject_base_size

import os
from six import PY3

import ZConfig

try:
    from itertools import zip
except ImportError:
    zip = zip

def itervalues(d):
    try:
        iv = d.itervalues
    except AttributeError:
        iv = d.values
    return iv()

if PY3:
    ask = input
else:
    ask = raw_input # pylint:disable=undefined-variable

DEFAULT_MAX_ATTEMPTS = 20
DEFAULT_OBJ_COUNTS = (1000,)
DEFAULT_CONCURRENCIES = (2,)

schema_xml = u"""
<schema>
  <import package="ZODB"/>
  <multisection type="ZODB.database" name="*" attribute="databases" />
</schema>
"""

def _make_leak_check(options):
    if not options.leaks:
        return lambda: None, lambda: None

    if PY3:
        SIO = StringIO
    else:
        from io import BytesIO as SIO

    import objgraph
    import gc
    def prep_leaks():
        gc.collect()
        objgraph.show_growth(file=SIO())

    def show_leaks():
        gc.collect()
        gc.collect()
        sio = SIO()
        objgraph.show_growth(file=sio)
        if sio.getvalue():
            print("    Memory Growth")
            for line in sio.getvalue().split('\n'):
                print("    ", line)

    return prep_leaks, show_leaks

def _zap(contenders):
    for db_name, db in contenders:
        db = db.open()
        if hasattr(db.storage, 'zap_all'):
            prompt = "Really destroy all data in %s? [yN] " % db_name
            resp = ask(prompt)
            if resp in 'yY':
                db.storage.zap_all()
        db.close()


class _CacheAndConnSettingFactory(object):

    def __init__(self, zodb_conf_factory, objects_per_txn, concurrency):
        self.factory = zodb_conf_factory
        self.objects_per_txn = objects_per_txn
        self.concurrency = concurrency

    def open(self):
        db = self.factory.open()
        # Explicitly set the number of cached objects so we're
        # using the storage in an understandable way.
        # Set to double the number of objects we should have created
        # to account for btree nodes.
        db.setCacheSize(self.objects_per_txn * 3)
        # Prevent warnings about large concurrent shared databases.
        db.setPoolSize(self.concurrency)
        return db

def run_with_options(runner, options):
    conf_fn = options.config_file

    # Do the gevent stuff ASAP
    if getattr(options, 'gevent', False):
        # Because of what we import up top, this must have
        # already been done, to be sure that it's effective
        import gevent.monkey
        if not gevent.monkey.is_module_patched('threading'):
            raise AssertionError("gevent monkey-patching should have happened")


    if options.log:
        import logging
        lvl_map = getattr(logging, '_nameToLevel', None) or getattr(logging, '_levelNames', {})
        logging.basicConfig(level=lvl_map.get(options.log, logging.INFO),
                            format='%(asctime)s %(levelname)-5.5s [%(name)s][%(thread)d:%(process)d][%(threadName)s] %(message)s')
    objects_per_txn = options.counts[0] if options.counts else DEFAULT_OBJ_COUNTS[0]
    concurrency = options.concurrency[0] if options.concurrency else DEFAULT_CONCURRENCIES[0]
    object_size = max(options.object_size, pobject_base_size)
    if options.profile_dir and not os.path.exists(options.profile_dir):
        os.makedirs(options.profile_dir)

    schema = ZConfig.loadSchemaFile(StringIO(schema_xml))
    config, _handler = ZConfig.loadConfigFile(schema, conf_fn)
    contenders = [(db.name, _CacheAndConnSettingFactory(db, objects_per_txn, concurrency))
                  for db in config.databases]

    if options.zap:
        _zap(contenders)

    data = SpeedTestData(concurrency, objects_per_txn, object_size)
    data.min_object_count = data.min_object_count
    if options.btrees:
        import BTrees
        if options.btrees == 'IO':
            data.MappingType = BTrees.family64.IO.BTree
        else:
            data.MappingType = BTrees.family64.OO.BTree

    # We always include a mapping storage as the first item
    # as a ground floor.
    from ZODB import DB
    from ZODB.DemoStorage import DemoStorage

    class Factory(object):

        def open(self):
            db = DB(DemoStorage())
            import transaction
            db.close = lambda: None
            data.populate(lambda: db)
            del db.close
            mconn = db.open()
            mroot = mconn.root()
            for worker in range(concurrency):
                mroot['speedtest'][worker].update(data.data_to_store())
            transaction.commit()
            mconn.cacheMinimize()
            mconn.close()
            return db

    # XXX: Make this configurable too. This can be quite slow
    # using in-process concurrency.
    #contenders.insert(0, ('mapping', Factory()))

    if concurrency > 1:
        if options.threads == 'shared':
            speedtest = SharedThreadedRunner(data, concurrency)
        elif options.threads:
            # Unique
            speedtest = ThreadedRunner(data, concurrency)
        else:
            speedtest = ForkedRunner(data, concurrency)
    else:
        speedtest = SpeedTestWorker(
            0,
            data
        )

    if options.profile_dir:
        if options.threads == 'shared':
            if options.gevent:
                # This doesn't work for non-concurrent (concurrency=1) situations
                # TODO: Make one that does.
                # TODO: Implement this for non-gevent concurrency.
                speedtest.Function = GeventProfiledFunctionFactory(options.profile_dir,
                                                                   options.worker_task,
                                                                   speedtest.Function)

    for db_name, db_factory in contenders:
        metadata = {
            'gevent': options.gevent,
            'threads': options.threads,
            'btrees': options.btrees,
            'concurrency': concurrency
        }
        # TODO: Include the gevent loop implementation in the metadata.

        if not options.worker:
            # I'm the master process.
            data.populate(db_factory.open)

        # TODO: Where to include leak prints?
        for bench_name, bench_func in (
                ('%s: add %s objects', speedtest.bench_add),
                ('%s: update %s objects', speedtest.bench_update,),
                ('%s: read %s cold objects', speedtest.bench_cold_read,),
                ('%s: read %s warm objects', speedtest.bench_read_after_write,),
                ('%s: read %s hot objects', speedtest.bench_hot_read,),
                ('%s: read %s steamin objects', speedtest.bench_steamin_read,),
        ):
            bench_name = bench_name % (db_name, objects_per_txn)
            if getattr(bench_func, 'inner_loops', None):
                inner_loops = speedtest.inner_loops
            else:
                inner_loops = 1
            inner_loops *= concurrency
            runner.bench_time_func(
                bench_name,
                bench_func,
                db_factory.open,
                inner_loops=inner_loops,
                metadata=metadata,
            )


class DistributedFunction(object):
    def __init__(self, mp_strategy, func_name, workers):
        self.func_name = func_name
        self.workers = workers
        self.mp_strategy = mp_strategy

    def __getattr__(self, name):
        # We're a function-like object, delegate to the function
        # on the first worker (which should be identical to everything
        # else)
        return getattr(self.workers[0], name)

    def __call__(self, loops, db_factory):
        from .fork import distribute

        # Is averaging these really the best we can do?

        # If we use our own wall clock time, we include all the
        # overhead of distributing the tasks and whatever internal
        # time they didn't want included. OTOH, if we just return the
        # sum (or average) of the individual concurrent runs, some
        # things might get charged more than once (especially with
        # gevent: one can start a timer, switch away to another for an
        # unbounded amount of time while still accumulating clock time, while
        # not even running). Maybe we want to use a greenlet tracer in that
        # case to only count time while it's actually doing stuff?
        times = distribute(self.worker,
                           ((w, loops, db_factory) for w in self.workers),
                           self.mp_strategy)
        # print("Actual duration of", self.func_name, "is", end - begin,
        #       "Sum is", sum(times), "avg is", sum(times) / len(self.workers),
        #       "Real average is", (end - begin) / len(self.workers))
        return sum(times) / len(self.workers)

    def worker(self, worker_loops_db_factory, sync):
        worker, loops, db_factory = worker_loops_db_factory
        worker.sync = sync
        f = getattr(worker, self.func_name)
        time = f(loops, db_factory)
        return time


class ThreadedRunner(object):
    mp_strategy = 'threads'
    Function = DistributedFunction

    def __init__(self, data, concurrency):
        self.concurrency = concurrency
        self.workers = [SpeedTestWorker(i, data) for i in range(concurrency)]

    def __getattr__(self, name):
        if name.startswith("bench_"):
            return self.Function(self.mp_strategy, name, self.workers)
        return getattr(self.workers[0], name)

class SharedDBFunction(DistributedFunction):
    def __call__(self, loops, db_factory):
        db = db_factory()
        db.setPoolSize(len(self.workers))
        close = db.close
        db.close = lambda: None
        db_factory = lambda: db
        try:
            return super(SharedDBFunction, self).__call__(loops, db_factory)
        finally:
            close()

class SharedThreadedRunner(ThreadedRunner):
    Function = SharedDBFunction


class GeventProfiledFunctionFactory(object):
    def __init__(self, profile_dir, worker, inner):
        self.profile_dir = profile_dir
        self.inner = inner
        self.worker = worker

    def __call__(self, mp_strategy, func_name, workers):
        return GeventProfiledFunction(self.profile_dir,
                                      self.inner,
                                      mp_strategy, func_name, workers)

class GeventProfiledFunction(object):
    """
    A function wrapper that installs a profiler around the execution
    of the functions.

    This is only done in the current thread, so it's best for gevent,
    where real threads are not actually in use.
    """

    def __init__(self, profile_dir, inner, mp_strategy, func_name, workers):
        self.profile_dir = profile_dir
        self.inner = inner(mp_strategy, func_name, workers)
        import cProfile
        self.profiler = cProfile.Profile()

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __call__(self, loops, db_factory):
        basename = self.inner.func_name + '_' + str(os.getpid())

        txt_fn = os.path.join(self.profile_dir, basename + ".txt")
        prof_fn = os.path.join(self.profile_dir, basename + ".prof")
        # TODO: Make this configurable to use vmProf

        # We're trying to capture profiling from all the warmup runs, etc,
        # since that all takes much longer.
        from pstats import Stats
        self.profiler.enable()
        try:
            return self.inner(loops, db_factory)
        finally:
            self.profiler.disable()
            self.profiler.dump_stats(prof_fn)

            with open(txt_fn, 'w') as f:
                st = Stats(self.profiler, stream=f)
                st.strip_dirs()
                st.sort_stats('cumulative')
                st.print_stats()

class ForkedRunner(ThreadedRunner):
    mp_strategy = 'mp'
