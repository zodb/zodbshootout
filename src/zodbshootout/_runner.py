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

import os
from io import StringIO
import threading

from pyperf import perf_counter
from pyperf import Benchmark
from pyperf import BenchmarkSuite

from .speedtest import SpeedTestData
from .speedtest import SpeedTestWorker
from .speedtest import pobject_base_size

from six import PY3

import ZConfig


if PY3:
    ask = input
else:
    ask = raw_input # pylint:disable=undefined-variable

schema_xml = u"""
<schema>
  <import package="ZODB"/>
  <multisection type="ZODB.database" name="*" attribute="databases" />
</schema>
"""

logger = __import__('logging').getLogger(__name__)

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

def _zap(contenders, force=False):
    for db_name, db in contenders:
        db = db.open()
        if hasattr(db.storage, 'zap_all'):
            if force:
                resp = 'y'
            else:
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

    def __getattr__(self, name):
        return getattr(self.factory, name)

    def open(self):
        db = self.factory.open()
        # Explicitly set the number of cached objects so we're
        # using the storage in an understandable way.
        # Set to double the number of objects we should have created
        # to account for btree nodes.
        db.setCacheSize(self.objects_per_txn * 3)
        # Prevent warnings about large concurrent shared databases.
        db.setPoolSize(self.concurrency)
        db.speedtest_log_cache_stats = lambda msg='': self._log_cache_stats(db, msg)
        return db

    def _log_cache_stats(self, db, msg=''):
        storage = db.storage
        cache = getattr(storage, '_cache', None)
        stats = getattr(cache, 'stats', lambda: {})()
        if stats:
            # TODO: Get these recorded in metadata for the benchmark that just ran
            logger.debug(
                "Cache hit stats for %s (%s): Hits: %s Misses: %s Ratio: %s Stores: %s",
                self.name, msg,
                stats.get('hits'), stats.get('misses'), stats.get('ratio'), stats.get('sets')
            )
        else:
            logger.debug("No storage cache found for %s (%s)",
                         self.name, msg)

    def __call__(self):
        return self.open()

    def __repr__(self):
        return "CAC(%s)" % (self.name,)


class _MappingFactory(object):

    name = 'MappingStorage'

    def __init__(self, concurrency, data):
        self.concurrency = concurrency
        self.data = data

    def open(self):
        from ZODB import DB
        from ZODB.MappingStorage import MappingStorage

        db = DB(MappingStorage())
        import transaction
        db.close = lambda: None
        self.data.populate(lambda: db)
        del db.close
        mconn = db.open()
        mroot = mconn.root()
        for worker in range(self.concurrency):
            mroot['speedtest'][worker].update(self.data.data_to_store())
        transaction.commit()
        mconn.cacheMinimize()
        mconn.close()
        return db

    def __call__(self):
        return self.open()

def run_with_options(runner, options):
    conf_fn = options.config_file

    # Do the gevent stuff ASAP
    if getattr(options, 'gevent', False):
        # Because of what we import up top, this must have
        # already been done, to be sure that it's effective
        import gevent.monkey
        if not gevent.monkey.is_module_patched('threading'):
            raise AssertionError("gevent monkey-patching should have happened")

    objects_per_txn = options.counts
    # XXX: Note this in the docs: If concurrency is high, and
    # objects_per_txn is low, especially if you're using threads or
    # gevent, you can spend all your time polling for numbers from
    # children, and not actually making much forward progress. This
    # shows up as the CPU usage being relatively low, and the sample
    # showing all the time spent in libev/libuv. An in-memory database like the
    # mapping database shows this best.
    concurrency = options.concurrency
    object_size = max(options.object_size, pobject_base_size)
    if options.profile_dir and not os.path.exists(options.profile_dir):
        os.makedirs(options.profile_dir)

    schema = ZConfig.loadSchemaFile(StringIO(schema_xml))
    config, _handler = ZConfig.loadConfigFile(schema, conf_fn)
    contenders = [(db.name, _CacheAndConnSettingFactory(db, objects_per_txn, concurrency))
                  for db in config.databases]

    def will_run_add():
        return options.benchmarks == ['all'] or 'add' in options.benchmarks

    if options.zap:
        if not will_run_add():
            raise Exception("Cannot zap if you're not adding")
        _zap(contenders, force=options.zap == 'force')

    data = SpeedTestData(concurrency, objects_per_txn, object_size)
    data.min_object_count = data.min_object_count
    if options.btrees:
        import BTrees
        if options.btrees == 'IO':
            data.MappingType = BTrees.family64.IO.BTree
        else:
            data.MappingType = BTrees.family64.OO.BTree

    # We include a mapping storage as the first item
    # as a ground floor to set expectations.
    if options.include_mapping:
        contenders.insert(0, ('mapping',
                              _CacheAndConnSettingFactory(_MappingFactory(concurrency, data),
                                                          objects_per_txn, concurrency)))

    # For concurrency of 1, or if we're using forked concurrency, we
    # want to take the times as reported by the benchmark functions as
    # accurate: There is no other timer running that could interfere.
    # For other methods, especially if we're using gevent, we may need to make adjustments;
    # see the ThreadedRunner for details.
    runner_kind = NonConcurrentRunner
    if concurrency > 1:
        if options.threads == 'shared':
            runner_kind = SharedThreadedRunner
        elif options.threads:
            # Unique
            runner_kind = ThreadedRunner
        else:
            runner_kind = ForkedRunner
    speedtest = runner_kind(data, options)

    if options.profile_dir:
        if options.threads == 'shared':
            if options.gevent:
                # TODO: Implement this for non-gevent concurrency.
                speedtest.make_function_wrapper = GeventProfiledFunctionFactory(
                    options.profile_dir,
                    options.worker_task,
                    speedtest.make_function_wrapper
                )

    for db_name, db_factory in contenders:
        metadata = {
            'gevent': options.gevent,
            'threads': options.threads,
            'btrees': options.btrees,
            'concurrency': concurrency,
            'objects_per_txn': objects_per_txn,
        }
        # TODO: Include the gevent loop implementation in the metadata.

        if not options.worker and will_run_add():
            # I'm the master process. Only do this (which resets everything)
            # if we're going to run the add benchmark.
            data.populate(db_factory)

        db_benchmarks = {}
        # TODO: Where to include leak prints?
        for bench_descr, bench_func, bench_opt_name in (
                ('%s: add %s objects', speedtest.bench_add, 'add'),
                ('%s: update %s objects', speedtest.bench_update, 'update',),
                ('%s: read %s cold objects', speedtest.bench_cold_read, 'cold',),
                ('%s: read %s warm objects', speedtest.bench_read_after_write, 'warm',),
                ('%s: read %s hot objects', speedtest.bench_hot_read, 'hot',),
                ('%s: read %s steamin objects', speedtest.bench_steamin_read, 'steamin',),
        ):
            if options.benchmarks != ['all'] and bench_opt_name not in options.benchmarks:
                continue

            bench_name = bench_descr % (db_name, objects_per_txn)
            if getattr(bench_func, 'inner_loops', None):
                inner_loops = speedtest.inner_loops
            else:
                inner_loops = 1
            # The decision on how to account for concurrency (whether to treat
            # that as part of the inner loop and thus divide total times by it)
            # depends on the runtime behaviour. See DistributedFunction for details.
            benchmark = runner.bench_time_func(
                bench_name,
                bench_func,
                db_factory,
                inner_loops=inner_loops,
                metadata=metadata,
            )
            db_benchmarks[bench_opt_name] = benchmark

        if not options.worker and options.output:
            # Master is going to try to write out to json
            dir_name = os.path.splitext(options.output)[0] + '.d'
            if not os.path.exists(dir_name):
                os.makedirs(dir_name)
            # We're going to update the metadata, so we need to make
            # a copy.
            # Use the short name so that even runs across different object
            # counts are comparable.
            for name, benchmark in list(db_benchmarks.items()):
                benchmark = Benchmark(benchmark.get_runs())
                benchmark.update_metadata({'name': name})
                db_benchmarks[name] = benchmark
            suite = BenchmarkSuite(db_benchmarks.values())

            fname = os.path.join(dir_name, db_name + '_' + str(objects_per_txn) + '.json')
            suite.dump(fname, replace=True)

class DistributedFunction(object):
    options = None

    def __init__(self, runner, func_name):
        mp_strategy = runner.mp_strategy
        workers = runner.workers
        self.options = runner.options
        assert mp_strategy
        assert func_name
        self.func_name = func_name
        self.workers = workers
        self.mp_strategy = mp_strategy

    @property
    def concurrency(self):
        return len(self.workers)

    def __getattr__(self, name):
        # We're a function-like object, delegate to the function
        # on the first worker (which should be identical to everything
        # else)
        return getattr(self.workers[0], name)

    def __call__(self, loops, db_factory):
        from .fork import distribute

        begin = perf_counter()
        times = distribute(self.worker,
                           ((w, loops, db_factory) for w in self.workers),
                           self.mp_strategy)
        end = perf_counter()
        if self.mp_strategy == 'mp':
            # We used forking, there was no contention we need to account for.
            # But we do return, not an average, but the *worst* time. This
            # lets pyperf do the averaging and smoothing for us.
            return max(times)


        # We used in-process concurrency. There may be contention to
        # account for.

        # If we use our own wall clock time, we include all the
        # overhead of distributing the tasks and whatever internal
        # time they didn't want included. OTOH, if we just return the
        # sum (or average) of the individual concurrent runs, some
        # things might get charged more than once (especially with
        # gevent: one can start a timer, switch away to another for an
        # unbounded amount of time while still accumulating clock
        # time, while not even running; this can also happen in
        # threads if the driver releases the GIL frequently. Suppose
        # you have 10 tasks, each of which does 10 things, and each
        # thing takes 1ms, but releases the GIL; the first task runs
        # for 1ms then releases the GIL, the second does the same, and
        # so on. If scheduling is fair or FIFO, by the time the first
        # task runs again, 10ms will already have elapsed; by the time
        # it finishes, 100ms will have elapsed).

        # TODO: Would it make sense to keep these numbers and find
        # some way to add them to the pyperf Run that winds up being
        # computed from this?

        actual_duration = end - begin
        recorded_duration = sum(times)
        actual_average = actual_duration / self.concurrency
        recorded_average = recorded_duration / self.concurrency
        if recorded_duration > actual_duration:
            # We think we took longer than we actually did. This means
            # that there was a high level of actual concurrent operations
            # going on, a lot of GIL switching, or gevent switching. That's a good thing!
            # it means you have a cooperative database
            #
            # In that case, we want to treat the concurrency as essentially an extra
            # 'inner_loop' for the benchmark. To do this, we find the biggest one
            # (usually the first to start?) and divide by the concurrency.

            # This "normalization" helps when comparing different
            # types of concurrency. If you're only going to be working
            # with one type of database (fully cooperative, fully
            # non-cooperative), you may not want to do this normalization.
            if self.options.gevent:
                logger.info('(gevent-cooperative driver %s)', db_factory.name)
            result = max(times) / self.concurrency
        else:
            if self.options.gevent:
                logger.info('(gevent NON-cooperative driver %s)', db_factory.name)
            result = max(times)
        logger.debug(
            "Actual duration of %s is %s. Recorded duration is %s. "
            "Actual average is %s. Recorded average is %s. "
            "Result: %s. Times: %s",
            self.func_name, actual_duration, recorded_duration,
            actual_average, recorded_average,
            result, times
        )

        return result

    def worker(self, worker_loops_db_factory, sync):
        worker, loops, db_factory = worker_loops_db_factory
        worker.sync = sync
        return self.run_worker_function(worker, self.func_name, loops, db_factory)

    @staticmethod
    def run_worker_function(worker, func_name, loops, db_factory):
        thread = threading.current_thread()
        thread.name = "%s-%s-%s" % (func_name, db_factory.name, worker.worker_number)
        f = getattr(worker, func_name)
        begin = perf_counter()
        time = f(loops, db_factory)
        end = perf_counter()
        logger.debug("Worker %s ran for %s",
                     func_name, end - begin)
        return time


class AbstractWrappingRunner(object):
    mp_strategy = None
    Function = None
    WorkerClass = SpeedTestWorker

    def __init__(self, data, options):
        self.options = options
        self.concurrency = options.concurrency
        self.workers = [self.WorkerClass(i, data) for i in range(self.concurrency)]

    def __getattr__(self, name):
        if name.startswith("bench_"):
            wrapper = self.make_function_wrapper(self, name)
            return wrapper
        return getattr(self.workers[0], name)

    def make_function_wrapper(self, runner, func_name):
        raise NotImplementedError


class SharedDBFunction(object):

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __call__(self, loops, db_factory):
        orig_db_factory = db_factory
        db = db_factory()
        db.setPoolSize(self.inner.concurrency)
        close = db.close
        db.close = lambda: None
        db_factory = lambda: db
        db_factory.name = orig_db_factory.name
        try:
            return self.inner(loops, db_factory)
        finally:
            close()


class ThreadedRunner(AbstractWrappingRunner):
    mp_strategy = 'threads'
    make_function_wrapper = DistributedFunction


class SharedThreadedRunner(AbstractWrappingRunner):
    mp_strategy = 'threads'

    def make_function_wrapper(self, runner, func_name):
        return SharedDBFunction(DistributedFunction(runner, func_name))

class NonConcurrentRunner(AbstractWrappingRunner):

    def make_function_wrapper(self, runner, func_name):
        # pylint:disable=no-value-for-parameter
        worker = self.workers[0]
        def call(loops, db_factory):
            return DistributedFunction.run_worker_function(worker, func_name, loops, db_factory)
        return call

class GeventProfiledFunctionFactory(object):
    def __init__(self, profile_dir, worker, inner):
        self.profile_dir = profile_dir
        self.inner = inner
        self.worker = worker

    def __call__(self, *args):
        return GeventProfiledFunction(self.profile_dir,
                                      self.inner,
                                      *args)

class GeventProfiledFunction(object):
    """
    A function wrapper that installs a profiler around the execution
    of the functions.

    This is only done in the current thread, so it's best for gevent,
    where real threads are not actually in use.
    """

    def __init__(self, profile_dir, inner, runner, func_name):
        self.profile_dir = profile_dir
        self.inner = inner(runner, func_name)
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


class ForkedWorker(SpeedTestWorker):

    def should_clear_all_caches(self):
        return True

class ForkedRunner(ThreadedRunner):
    mp_strategy = 'mp'
    WorkerClass = ForkedWorker
