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


from pyperf import Benchmark
from pyperf import BenchmarkSuite

from zope.interface import implementer

from .interfaces import IDBBenchmark

from ._dbsupport import BenchmarkDBFactory
from ._dbsupport import MappingFactory

from .speedtest import SpeedTestData
from .speedtest import pobject_base_size

from ._profile import ProfiledFunctionFactory
from ._profile import CProfiler
from ._profile import VMProfiler


from six import PY3

logger = __import__('logging').getLogger(__name__)

def _setup_leaks(options, speedtest):
    if not options.leaks or not options.worker:
        return

    assert options.threads
    # Like profiling, do this once around all the distributions,
    # but in the workers, not the master.

    if PY3:
        from io import StringIO as SIO
    else:
        from io import BytesIO as SIO

    import objgraph
    import gc
    from ._wrapper import AbstractWrapper

    class LeakWrapperFactory(object):
        def __init__(self, inner):
            self.inner = inner

        def __call__(self, func_name):
            return LeakWrapper(self.inner(func_name))

    @implementer(IDBBenchmark)
    class LeakWrapper(AbstractWrapper):
        def __init__(self, inner):
            self.__wrapped__ = inner
            self.should_show = False

        def __getattr__(self, name):
            return getattr(self.__wrapped__, name)

        def __call__(self, loops, db_factory):
            gc.collect()
            objgraph.show_growth(file=SIO())
            try:
                return self.__wrapped__(loops, db_factory)
            finally:
                gc.collect()
                gc.collect()
                sio = SIO()
                objgraph.show_growth(file=sio)
                if not self.should_show:
                    # The first time through is a freebie. There's
                    # probably still lots of importing and caching going on.
                    self.should_show = True
                elif sio.getvalue():
                    print("    Memory Growth")
                    for line in sio.getvalue().split('\n'):
                        print("    ", line)

    speedtest.make_function_wrapper = LeakWrapperFactory(speedtest.make_function_wrapper)


def _setup_profiling(options, speedtest):
    if not options.profile_dir:
        return

    if not os.path.exists(options.profile_dir):
        os.makedirs(options.profile_dir)

    if options.profile_dir:
        factory = VMProfiler if options.profiler == 'vmprof' else CProfiler
        options.profiler_factory = factory
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
            from ._wrapper import BenchmarkCollectionWrapper
            speedtest.workers = [
                BenchmarkCollectionWrapper(w)
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

    objects_per_txn = options.objects_per_txn
    # XXX: Note this in the docs: If concurrency is high, and
    # objects_per_txn is low, especially if you're using threads or
    # gevent, you can spend all your time polling for numbers from
    # children, and not actually making much forward progress. This
    # shows up as the CPU usage being relatively low, and the sample
    # showing all the time spent in libev/libuv. An in-memory database like the
    # mapping database shows this best.
    concurrency = options.concurrency
    object_size = max(options.object_size, pobject_base_size)


    contenders = []
    for db_factory in options.databases:
        can_zap = db_factory.name in options.zap
        factory = BenchmarkDBFactory(db_factory, objects_per_txn, concurrency,
                                     can_zap=can_zap)
        contenders.append(factory)

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
        contenders.insert(0, BenchmarkDBFactory(MappingFactory(concurrency, data),
                                                objects_per_txn, concurrency))

    # In the master, go ahead and open each database; we don't want to discover
    # a problem half-way through the run. Also, this helps with leak checks.
    for factory in contenders:
        db = factory.open()
        db.close()

    for db_factory in contenders:
        _run_benchmarks_for_contender(runner, options, data, db_factory)


def _create_speedtest(options, data):
    # For concurrency of 1, or if we're using forked concurrency, we
    # want to take the times as reported by the benchmark functions as
    # accurate: There is no other timer running that could interfere.
    # For other methods, especially if we're using gevent, we may need to make adjustments;
    # see the ThreadedRunner for details.
    from ._concurrent import NonConcurrentBenchmarkCollection
    from ._concurrent import SharedConcurrentBenchmarkCollection
    from ._concurrent import ThreadedConcurrentBenchmarkCollection
    from ._concurrent import ForkedConcurrentBenchmarkCollection
    runner_kind = NonConcurrentBenchmarkCollection
    if options.concurrency > 1:
        if options.threads == 'shared':
            runner_kind = SharedConcurrentBenchmarkCollection
        elif options.threads:
            # Unique
            runner_kind = ThreadedConcurrentBenchmarkCollection
        else:
            runner_kind = ForkedConcurrentBenchmarkCollection
    speedtest = runner_kind(data, options)
    _setup_profiling(options, speedtest)
    _setup_leaks(options, speedtest)
    return speedtest

def _run_benchmarks_for_contender(runner, options, data, db_factory):
    metadata = {
        'gevent': options.gevent,
        'threads': options.threads,
        'btrees': options.btrees,
        'concurrency': options.concurrency,
        'objects_per_txn': options.objects_per_txn,
    }
    # TODO: Include the gevent loop implementation in the metadata.
    speedtest = _create_speedtest(options, data)

    db_name = db_factory.name
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

        name_args = (db_name, ) if '%d' not in bench_descr else (db_name, options.objects_per_txn)
        bench_name = bench_descr % name_args
        if bench_func.inner_loops:
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

    _combine_benchmark_results(options, db_factory, db_benchmarks)

def _combine_benchmark_results(options, db_factory, db_benchmarks):
    # Do this in the master only, after running all the benchmarks
    # for a database.
    if options.worker:
        return

    db_name = db_factory.name
    if options.output:
        # Create a file for the entire suite, using names that can
        # be compared across different database configurations.
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

        fname = os.path.join(dir_name, db_name + '_' + str(options.objects_per_txn) + '.json')
        suite.dump(fname, replace=True)

    if options.profile_dir:
        # If we used cprofile to create stats files, we should
        # now read them back in and combine them for any given benchmark to get
        # more accurate results: one file per benchmark instead of many, many
        # as pyperf distributes things.
        profiler = options.profiler_factory(options.profile_dir, None)
        profiler.combine()
