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
import functools
import threading

from pyperf import perf_counter
from pyperf import Benchmark
from pyperf import BenchmarkSuite

from zope.interface import implementer

from .interfaces import IDBBenchmark

from ._dbsupport import BenchmarkDBFactory
from ._dbsupport import MappingFactory

from .speedtest import SpeedTestData
from .speedtest import SpeedTestWorker
from .speedtest import ForkedSpeedTestWorker
from .speedtest import pobject_base_size

from ._profile import ProfiledFunctionFactory
from ._profile import CProfiler
from ._profile import VMProfiler


from six import PY3

logger = __import__('logging').getLogger(__name__)

def _make_leak_check(options):
    if not options.leaks:
        return lambda: None, lambda: None

    if PY3:
        from io import StringIO as SIO
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

def setup_profiling(options, speedtest):
    if not options.profile_dir:
        return

    if not os.path.exists(options.profile_dir):
        os.makedirs(options.profile_dir)

    if options.profile_dir:
        factory = VMProfiler if options.profiler == 'vmprof' else CProfiler
        if options.threads and options.gevent:
            # It's fine to install the profiler just once around all
            # the distributions; we're only going to be looking at a
            # single native thread anyway.
            speedtest.make_function_wrapper = ProfiledFunctionFactory(
                options.profile_dir,
                speedtest.make_function_wrapper,
                factory
            )
        else:
            # either native threads or multi-processing.
            # We need to install the profiler *inside* each distributed task,
            # (the other thread or process).
            speedtest.workers = [
                WorkerBenchmarkFunctionWrapper(w)
                for w in speedtest.workers
            ]
            for w in speedtest.workers:
                w.make_function_wrapper = ProfiledFunctionFactory(
                    options.profile_dir,
                    w.make_function_wrapper,
                    factory
                )

def run_with_options(runner, options):
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

    if options.zap and 'add' not in options.benchmarks:
        raise Exception("Cannot zap if you're not adding")

    contenders = []
    for db_factory in options.databases:
        can_zap = db_factory.name in options.zap
        factory = BenchmarkDBFactory(db_factory, objects_per_txn, concurrency,
                                     can_zap=can_zap)
        contenders.append((db_factory.name, factory))

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
                              BenchmarkDBFactory(MappingFactory(concurrency, data),
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
    setup_profiling(options, speedtest)



    for db_name, db_factory in contenders:
        metadata = {
            'gevent': options.gevent,
            'threads': options.threads,
            'btrees': options.btrees,
            'concurrency': concurrency,
            'objects_per_txn': objects_per_txn,
        }
        # TODO: Include the gevent loop implementation in the metadata.

        db_benchmarks = {}
        # TODO: Where to include leak prints?
        for bench_descr, bench_func, bench_opt_name in (
                ('%s: add %d objects', speedtest.bench_add, 'add'),
                ('%s: update %d objects', speedtest.bench_update, 'update',),
                ('%s: read %d cold objects', speedtest.bench_cold_read, 'cold',),
                ('%s: read %d warm objects', speedtest.bench_read_after_write, 'warm',),
                ('%s: read %d hot objects', speedtest.bench_hot_read, 'hot',),
                ('%s: read %d steamin objects', speedtest.bench_steamin_read, 'steamin',),
                ('%s: empty commit', speedtest.bench_empty_transaction_commit, 'commit',),
        ):
            if bench_opt_name not in options.benchmarks:
                continue

            name_args = (db_name, ) if '%d' not in bench_descr else (db_name, objects_per_txn)
            bench_name = bench_descr % name_args
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

        # TODO: If we used cprofile to create stats files, we should
        # now read them back in and combine them for any given benchmark to get
        # more accurate results: one file per benchmark instead of many, many
        # as pyperf distributes things.

@implementer(IDBBenchmark)
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
        # This is the function that's called in the worker, either
        # in a different thread, or in a different process.
        worker, loops, db_factory = worker_loops_db_factory
        assert worker is not None
        assert loops is not None
        assert callable(db_factory)
        assert callable(sync)
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

class AbstractBenchmarkFunctionWrapper(object):
    delegate = None

    def __getattr__(self, name):
        if name.startswith("bench_"):
            return self.make_function_wrapper(name)
        return getattr(self.delegate, name)

    def make_function_wrapper(self, func_name):
        raise NotImplementedError

class WorkerBenchmarkFunctionWrapper(AbstractBenchmarkFunctionWrapper):

    def __init__(self, worker):
        self.delegate = worker

    def __setattr__(self, name, value):
        if name in ('delegate', 'make_function_wrapper'):
            object.__setattr__(self, name, value)
            return

        # Everything else delegates to the worker.
        # this is important for worker.sync
        setattr(self.delegate, name, value)

    def make_function_wrapper(self, func_name):
        # We just return the function.
        # Instances may have this set directly, so save storage space for it.
        return getattr(self.delegate, func_name)

class AbstractWrappingRunner(AbstractBenchmarkFunctionWrapper):
    # pylint:disable=abstract-method
    mp_strategy = None
    WorkerClass = SpeedTestWorker

    def __init__(self, data, options):
        self.options = options
        self.concurrency = options.concurrency
        self.workers = [self.WorkerClass(i, data) for i in range(self.concurrency)]

    @property
    def delegate(self):
        return self.workers[0]

@implementer(IDBBenchmark)
class SharedDBFunction(object):

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __call__(self, loops, db_factory):

        class DbAndClose(object):
            factory = db_factory
            def __init__(self):
                from threading import RLock
                self.lock = RLock()
                self.name = self.factory.name
                self.db = None
                self.reset()

            def reset(self):
                with self.lock:
                    self.db = db = self.factory()
                    db.close = lambda: None
                    speedtest_zap_all = db.speedtest_zap_all
                    def shared_zap():
                        with self.lock:
                            self.close()
                            speedtest_zap_all()
                            self.reset()
                    db.speedtest_zap_all = shared_zap

            def close(self):
                with self.lock:
                    if self.db is not None:
                        db = self.db
                        self.db = None

                        del db.close
                        db.close()

            def __call__(self):
                with self.lock:
                    return self.db

        db_and_close = DbAndClose()
        try:
            return self.inner(loops, db_and_close)
        finally:
            db_and_close.close()


class ThreadedRunner(AbstractWrappingRunner):
    mp_strategy = 'threads'

    def make_function_wrapper(self, func_name):
        return DistributedFunction(self, func_name)


class SharedThreadedRunner(AbstractWrappingRunner):
    mp_strategy = 'threads'

    def make_function_wrapper(self, func_name):
        return SharedDBFunction(DistributedFunction(self, func_name))

class NonConcurrentRunner(AbstractWrappingRunner):

    def make_function_wrapper(self, func_name):
        # pylint:disable=no-value-for-parameter
        worker = self.workers[0]
        return functools.partial(DistributedFunction.run_worker_function, worker, func_name)

class ForkedRunner(ThreadedRunner):
    mp_strategy = 'mp'
    WorkerClass = ForkedSpeedTestWorker
