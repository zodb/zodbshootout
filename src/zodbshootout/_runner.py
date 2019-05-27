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

    object_size = max(options.object_size, pobject_base_size)
    if options.profile_dir and not os.path.exists(options.profile_dir):
        os.makedirs(options.profile_dir)

    schema = ZConfig.loadSchemaFile(StringIO(schema_xml))
    config, _handler = ZConfig.loadConfigFile(schema, conf_fn)
    contenders = [(db.name, db) for db in config.databases]

    if options.zap:
        _zap(contenders)

    objects_per_txn = options.counts[0] if options.counts else DEFAULT_OBJ_COUNTS[0]
    concurrency = options.concurrency[0] if options.concurrency else DEFAULT_CONCURRENCIES[0]
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

    contenders.insert(0, ('mapping', Factory()))

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

    for db_name, db_factory in contenders:
        metadata = {
            'gevent': options.gevent,
            'threads': options.threads,
            'btrees': options.btrees,
            'concurrency': concurrency
        }

        if not options.worker:
            # I'm the master process.
            data.populate(db_factory.open)

        # TODO: Where to include leak prints?

        runner.bench_time_func(
            '%s: add %s objects' % (db_name, objects_per_txn),
            speedtest.bench_add,
            db_factory.open,
            inner_loops=1,
            metadata=metadata,
        )
        runner.bench_time_func(
            '%s: update %s objects' % (db_name, objects_per_txn),
            speedtest.bench_update,
            db_factory.open,
            inner_loops=1,
            metadata=metadata,# objects_per_txn
        )
        runner.bench_time_func(
            '%s: read %s cold objects' % (db_name, objects_per_txn),
            speedtest.bench_cold_read,
            db_factory.open,
            inner_loops=1,
            metadata=metadata,# objects_per_txn
        )
        runner.bench_time_func(
            '%s: read %s warm objects' % (db_name, objects_per_txn),
            speedtest.bench_read_after_write,
            db_factory.open,
            inner_loops=1,
            metadata=metadata,# objects_per_txn
        )
        runner.bench_time_func(
            '%s: read %s hot objects' % (db_name, objects_per_txn),
            speedtest.bench_hot_read,
            db_factory.open,
            inner_loops=1,
            metadata=metadata,# objects_per_txn
        )
        runner.bench_time_func(
            '%s: read %s steamin objects' % (db_name, objects_per_txn),
            speedtest.bench_steamin_read,
            db_factory.open,
            inner_loops=1,
            metadata=metadata,# objects_per_txn
        )

class DistributedTimer(object):
    def __init__(self, mp_strategy, func_name, workers):
        self.func_name = func_name
        self.workers = workers
        self.mp_strategy = mp_strategy

    def __call__(self, loops, db_factory):
        from .fork import distribute
        def doit(worker, sync):
            worker.sync = sync
            f = getattr(worker, self.func_name)
            time = f(loops, db_factory)
            return time
        # Is averaging these really the best we can do?
        times = distribute(doit, self.workers, self.mp_strategy)
        return sum(times) / len(self.workers)

class ThreadedRunner(object):

    mp_strategy = 'threads'
    Timer = DistributedTimer

    def __init__(self, data, concurrency):
        self.concurrency = concurrency
        self.workers = [SpeedTestWorker(i, data) for i in range(concurrency)]

    def __getattr__(self, name):
        if name.startswith("bench_"):
            return self.Timer(self.mp_strategy, name, self.workers)
        raise AttributeError(name)


class SharedThreadedRunner(ThreadedRunner):

    class Timer(ThreadedRunner.Timer):
        def __call__(self, loops, db_factory):
            db = db_factory()
            close = db.close
            db.close = lambda: None
            db_factory = lambda: db
            try:
                return ThreadedRunner.Timer.__call__(self, loops, db_factory)
            finally:
                close()

class ForkedRunner(ThreadedRunner):
    mp_strategy = 'mp'
