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
"""
The core speed test loop.
"""
from __future__ import print_function, absolute_import

# This file is imported after gevent monkey patching (if applicable)
# so it's safe to import anything.
import gc
from collections import namedtuple
from functools import partial
from functools import wraps
from itertools import chain
from pstats import Stats
from threading import Lock

import cProfile

import os
import random
import statistics

try:
    from time import perf_counter
except ImportError:
    from backports.time_perf_counter import perf_counter

import transaction

from persistent.mapping import PersistentMapping
from persistent.list import PersistentList

from .fork import distribute
from .fork import run_in_child
from ._pobject import pobject_base_size
from ._pobject import PObject
from ._pblobobject import BlobObject

logger = __import__('logging').getLogger(__name__)

def itervalues(d):
    try:
        iv = d.itervalues
    except AttributeError:
        iv = d.values
    return iv()



with open(__file__, 'rb') as _f:
    # Just use the this module as the source of our data
    _random_file_data = _f.read().replace(b'\n', b'').split()
del _f

random.seed(__file__) # reproducible random functions

def _random_data(size):
    """
    Create a random data of at least the given size.

    Use pseudo-random data in case compression is in play so we get a more realistic
    size and time value than a single 'x'*size would get.
    """

    def fdata():
        words = _random_file_data
        chunksize = min(size, 1024)
        while True:
            sample = random.sample(words, len(words) // 10)
            yield b' '.join(sample[0:chunksize])
    datagen = fdata()

    data = b''
    while len(data) < size:
        data += next(datagen)
    return data

_random_data(100) # call once for the sake of leak checks

WriteTimes = namedtuple('WriteTimes', ['add_time', 'update_time'])
ReadTimes = namedtuple('ReadTimes', ['warm_time', 'cold_time', 'hot_time', 'steamin_time'])
SpeedTestTimes = namedtuple('SpeedTestTimes', WriteTimes._fields + ReadTimes._fields)

class _TimedDBFunction(object):
    """
    Decorator factory for a function that
    opens a DB, does something while taking the clock time,
    then closes the DB and returns the (total_time, time_to_exclude).

    The wrapped function takes the root of the connection as
    its first argument and then any other arguments.

    The transaction is committed before closing the connection
    and after running the function.
    """

    # We should always include conn.open() inside our times,
    # because it talks to the storage to poll invalidations.
    # conn.close() does not talk to the storage, but it does
    # do some cache maintenance and should be excluded if possible.

    def __init__(self, db, excluded_time_lock):
        self.db = db
        self._excluded_time_lock = excluded_time_lock

    def _close_conn(self, conn, func_name):
        with self._excluded_time_lock:
            loads, stores = conn.getTransferCounts(True)
            db_name = conn.db().database_name
            logger.debug("DB %s conn %s func %s loads %s stores %s",
                         db_name, conn, func_name, loads, stores)
            conn.close()


    def __call__(self, func):
        _close_conn = self._close_conn
        db = self.db

        @wraps(func)
        def timer(*args, **kwargs):
            start = perf_counter()

            conn = db.open()

            func(conn, *args, **kwargs)

            transaction.commit()
            after_commit = perf_counter()
            self._close_conn(conn, func.__name__)
            end = perf_counter()
            return (end - start, end - after_commit)
        return timer

class AbstractProfiler(object):
    enabled = False
    def __init__(self, prof_fn, stat_fn):
        self.i_enabled = False
        self.profiler = None
        self.prof_fn = prof_fn
        self.stat_fn = stat_fn

    def __enter__(self):
        if type(self).enabled:
            return
        self.i_enabled = True
        type(self).enabled = True
        self._do_enter()

    def __exit__(self, t, v, tb):
        if not self.i_enabled:
            return
        self._do_exit()
        type(self).enabled = False

    def _do_enter(self):
        raise NotImplementedError

    def _do_exit(self):
        raise NotImplementedError

class useCProfile(AbstractProfiler):

    def _do_enter(self):

        self.profiler = cProfile.Profile()
        self.profiler.enable()

    def _do_exit(self):
        self.profiler.disable()

        self.profiler.dump_stats(self.prof_fn)

        with open(self.stat_fn, 'w') as f:
            st = Stats(self.profiler, stream=f)
            st.strip_dirs()
            st.sort_stats('cumulative')
            st.print_stats()


class useVMProf(AbstractProfiler):
    stat_file = None

    def _do_enter(self):
        import vmprof
        self.stat_file = open(self.prof_fn, 'w+b')
        vmprof.enable(self.stat_file.fileno(), lines=True)

    def _do_exit(self):
        import vmprof
        vmprof.disable()
        self.stat_file.flush()
        self.stat_file.close()

class AbstractSpeedTest(object):

    MappingType = PersistentMapping
    ObjectType = PObject


    individual_test_reps = 20
    min_object_count = 0

    def __new__(cls, *_args, **kwargs):
        if kwargs.pop('use_blobs', False):
            cls = BlobSpeedTest
        inst = super(AbstractSpeedTest, cls).__new__(cls)
        return inst

    def __init__(self, concurrency, objects_per_txn, object_size,
                 profile_dir=None,
                 mp_strategy='mp',
                 test_reps=None,
                 **_kwargs):
        """

        :keyword str mp_strategy: A string naming the
           concurrency strategy. If it is either 'shared' or 'unique',
           we will be running all tests in a single process using threads or greenlets.
           Otherwise it must be 'mp', meaning that we will be running each test
           in its own process.
        """
        self.concurrency = concurrency
        self.objects_per_txn = objects_per_txn
        self.object_size = object_size
        self.profile_dir = profile_dir
        self.contender_name = None
        if mp_strategy in ('shared', 'unique'):
            self.mp_strategy = 'threads'
        else:
            assert mp_strategy == 'mp'
            self.mp_strategy = mp_strategy

        if test_reps:
            self.individual_test_reps = test_reps
        self.rep = 0  # repetition number

        if mp_strategy == 'shared':
            self._wait_for_master_to_do = self._threaded_wait_for_master_to_do

        self.__random_data = []
        self.__excluded_time_lock = Lock()

    # When everybody is waiting for the master to do something,
    # that's a good time for the master to also do GC. Nobody should be timing anything
    # at that point.

    def _wait_for_master_to_do(self, _thread_number, _sync, func, *args):
        # We are the only thing running, this object is not shared,
        # func must always be called.

        # Return how long this took.
        begin = perf_counter()
        func(*args)
        end = perf_counter()
        return end - begin

    def _threaded_wait_for_master_to_do(self, thread_number, sync, func, *args):
        """
        Block all threads until *func* is called by the first thread (0).

        Return how long this took in the current thread.

        TODO: This might be a good way to handle profiling enable/disable
        so we can be sure to capture all the activity in a single phase.
        """
        begin = perf_counter()
        sync()
        if thread_number == 0:
            func(*args)
        sync()
        end = perf_counter()
        return end - begin

    def _sync_and_collect(self, thread_number, sync):
        # Even if our DB's are unique, we only want to do this in the
        # master OR if we're in mp mode
        if self.mp_strategy == 'mp':
            sync()
            gc.collect()
        else:
            self._threaded_wait_for_master_to_do(thread_number, sync, gc.collect)

    def _guarantee_min_random_data(self, count):
        if len(self.__random_data) < count:
            needed = count - len(self.__random_data)
            data_size = max(0, self.object_size - pobject_base_size)
            self.__random_data.extend([_random_data(data_size) for _ in range(needed)])

    def data_to_store(self, count=None):
        # Must be fresh when accessed because could already
        # be stored in another database if we're using threads.
        # However, the random data can be relatively expensive to create,
        # and we don't want that showing up in profiles, so it can and should
        # be cached.
        if count is None:
            count = self.objects_per_txn
        self._guarantee_min_random_data(count)
        kind = self.ObjectType
        data = self.__random_data
        return dict((n, kind(data[n])) for n in range(count))

    def populate(self, db_factory):
        self._guarantee_min_random_data(self.objects_per_txn)

        db = db_factory()
        conn = db.open()
        root = conn.root()

        # clear the database
        root['speedtest'] = None
        # We explicitly leave the `speedtest_min` value around
        # so that it can survive packs.
        transaction.commit()
        db.pack()

        # Make sure the minimum objects are present
        if self.min_object_count:
            # not all storages support __len__ to return the size of the database.
            # FileStorage, RelStorage and ClientStorage do.
            db_count = max(len(db.storage), len(conn._storage))
            needed = max(self.min_object_count - db_count, 0)
            if needed:
                logger.debug("Adding %d objects to a DB of size %d",
                             needed, db_count)
                # We append to a list the new objects. This makes sure that we
                # don't *erase* some objects we were counting on.
                l = root.get('speedtest_min')
                if l is None:
                    l = root['speedtest_min'] = PersistentList()

                # If `needed` is large, this could result in a single
                # very large transaction. Do we need to think about splitting it up?
                m = PersistentMapping()
                m.update(self.data_to_store(needed))
                l.append(m)
                transaction.commit()
                logger.debug("Added %d objects to a DB of size %d",
                             len(m), db_count)
            else:
                logger.debug("Database is already of size %s", db_count)

        # put a tree in the database
        root['speedtest'] = t = self.MappingType()
        for i in range(self.concurrency):
            t[i] = self.MappingType()
        transaction.commit()
        conn.close()
        db.close()
        logger.debug('Populated storage.')

    def _clear_all_caches(self, db, thread_number, sync):
        # Clear all caches, returns how long that took.
        # No connection should be open when this is called.

        # For gevent, we need to be as careful as possible that this
        # function doesn't switch (or at least can't be entered
        # concurrently). If it does, we can wind up with greenlets
        # double charged for the expense of clearing a cache
        # (especially in RelStorage, clearing large caches can be
        # expensive due to the memory allocations). In 'unique' mode,
        # if we opened a connection here, we would switch, to the
        # other concurrent greenlets running in this function that
        # then open their own connection; control returns to the first
        # function, who takes the expensive action of clearing his
        # storage cache and then moves on with the test. Each
        # successive greenlet waits longer and longer before starting
        # the test.
        with self.__excluded_time_lock:
            db.pool.map(lambda c: c.cacheMinimize())
            # In ZODB 5, db.storage is the storage object passed to the DB object.
            # If it doesn't implement IMVCCStorage, then an adapter is wrapped
            # around it to make it do so (if it does, no such adapter is needed).
            # This is placed in _mvcc_storage. RelStorage is IMVCCStorage; ZEO is not.
            # Both of them have a `_cache` that needs cleared. We do not need to open a
            # connection for this to happen. The cache is shared among all connections
            # in both cases.

            if hasattr(db.storage, '_cache'):
                db.storage._cache.clear()

        # We probably just made a bunch of garbage. Try to get rid of it
        # before we go in earnest to eliminate the knock-on effect
        self._sync_and_collect(thread_number, sync)

    def _times_of_runs(self, func, times, args=()):
        """
        Repeat running *func* for *times* in a row, and collect the
        amount of time it takes to do so.

        If we are running in threads, then the entire time it takes to
        do this process is added up and returned. This is because we cannot
        measure the time taken to execute *just* a single function when
        functions are executing concurrently. Doing so will vastly
        over value some executions and undervalue others. As a result, we
        will not be able to compute meaningful standard deviations.

        *func* should return (total time, time to exclude). The excluded time
        should be locked so that it cannot overlap other excluded areas.


        """
        begin = perf_counter()
        run_exc_times = [func(*args) for _ in range(times)]
        end = perf_counter()
        total = end - begin

        if self.mp_strategy == 'threads':
            exc_times = [x[1] for x in run_exc_times]
            excluded = sum(exc_times)
            total = total - excluded
            run_times = [total / times for _ in range(times)]
        else:
            run_times = [x[0] - x[1] for x in run_exc_times]
        return run_times


    def write_test(self, db_factory, thread_number, sync):
        db = db_factory()

        @_TimedDBFunction(db, self.__excluded_time_lock)
        def do_add(conn):
            root = conn.root()
            m = root['speedtest'][thread_number]
            m.update(self.data_to_store())

        db.open().close()
        sync()
        add_time = self._execute(self._times_of_runs, 'add', thread_number,
                                 do_add, self.individual_test_reps)

        @_TimedDBFunction(db, self.__excluded_time_lock)
        def do_update(conn):
            root = conn.root()
            self._write_test_update_values(itervalues(root['speedtest'][thread_number]))

        self._sync_and_collect(thread_number, sync)
        update_time = self._execute(self._times_of_runs, 'update', thread_number,
                                    do_update, self.individual_test_reps)

        # XXX: Why were we sleeping?
        # time.sleep(.1)

        # In shared thread mode, db.close() doesn't actually do anything.
        db.close()
        return [WriteTimes(a, u) for a, u in zip(add_time, update_time)]

    def _write_test_update_values(self, values):
        raise NotImplementedError()

    def read_test(self, db_factory, thread_number, sync):
        db = db_factory()
        #print('**** Created db for read', db)
        # Explicitly set the number of cached objects so we're
        # using the storage in an understandable way.
        # Set to double the number of objects we should have created
        # to account for btree nodes.
        db.setCacheSize(self.objects_per_txn * 2)

        def do_read_loop(conn):
            got = self._read_test_read_values(itervalues(conn.root()['speedtest'][thread_number]))
            if got != self.objects_per_txn:
                raise AssertionError('data mismatch')

        @_TimedDBFunction(db, self.__excluded_time_lock)
        def do_steamin_read(conn):
            do_read_loop(conn)

        def do_cold_read():
            # Clear *all* the caches before opening any connections or
            # beginning any timing.
            begin = perf_counter()
            clear_time = self._wait_for_master_to_do(thread_number,
                                                     sync,
                                                     self._clear_all_caches,
                                                     db,
                                                     thread_number,
                                                     sync)
            _, s_excluded = do_steamin_read() # pylint:disable=no-value-for-parameter
            end = perf_counter()

            total = end - begin

            total_excluded = clear_time + s_excluded

            return (total, total_excluded)

        @_TimedDBFunction(db, self.__excluded_time_lock)
        def do_hot_read(conn):
            # sadly we have to include this time in our
            # timings because opening the connection must
            # be included. hopefully this is too fast to have an impact.
            #print('**** doing hot')
            conn.cacheMinimize()
            do_read_loop(conn)

        db.open().close() # ensure we have a connection open
        self._sync_and_collect(thread_number, sync)
        sync()
        # In shared thread mode, the 'warm' test, immediately following the update test,
        # is similar to the steamin test, because we're likely to get the same
        # Connection object again (because the DB wasn't really closed.)
        # Of course, this really only applies when self.concurrency is 1; for other
        # values, we can't be sure. Also note that we only execute this once
        # (because after that caches are primed).
        warm_total, warm_excluded = self._execute(do_steamin_read, 'warm', thread_number)
        warm = warm_total - warm_excluded


        sync() # WE're about to GC, no need to do it here.
        cold = self._execute(self._times_of_runs, 'cold', thread_number,
                             do_cold_read, self.individual_test_reps)

        self._sync_and_collect(thread_number, sync)
        hot = self._execute(self._times_of_runs, 'hot', thread_number,
                            do_hot_read, self.individual_test_reps)

        self._sync_and_collect(thread_number, sync)
        steamin = self._execute(self._times_of_runs, 'steamin', thread_number,
                                do_steamin_read, self.individual_test_reps)

        db.close()

        return [ReadTimes(w, c, h, s) for w, c, h, s
                in zip([warm] * self.individual_test_reps,
                       cold, hot, steamin)]

    def _read_test_read_values(self, values):
        raise NotImplementedError()

    def _execute(self, func, phase_name, n, *args):
        if not self.profile_dir:
            return func(*args)

        basename = '%s-%s-%d-%02d-%d' % (
            self.contender_name, phase_name, self.objects_per_txn, n, self.rep)
        txt_fn = os.path.join(self.profile_dir, basename + ".txt")
        prof_fn = os.path.join(self.profile_dir, basename + ".prof")



        profile_class = useVMProf


        with profile_class(prof_fn, txt_fn):
            return func(*args)


    def run(self, db_factory, contender_name, rep):
        """Run a write and read test.

        Returns a list of SpeedTestTimes items containing the results
        for every test, as well as a WriteTime and ReadTime summary
        """
        self.contender_name = contender_name
        self.rep = rep

        run_in_child(self.populate, self.mp_strategy, db_factory)


        thread_numbers = list(range(self.concurrency))
        write_times = distribute(partial(self.write_test, db_factory),
                                 thread_numbers, strategy=self.mp_strategy)
        read_times = distribute(partial(self.read_test, db_factory),
                                thread_numbers, strategy=self.mp_strategy)

        write_times = list(chain(*write_times))
        read_times = list(chain(*read_times))

        # Return the raw data here so as to not throw away any (more) data
        times = [SpeedTestTimes(*(w + r)) for w, r in zip(write_times, read_times)]

        # These are just for summary purpose
        add_times = [t.add_time for t in write_times]
        update_times = [t.update_time for t in write_times]
        warm_times = [t.warm_time for t in read_times]
        cold_times = [t.cold_time for t in read_times]
        hot_times = [t.hot_time for t in read_times]
        steamin_times = [t.steamin_time for t in read_times]

        write_times = WriteTimes(*[statistics.mean(x) for x in (add_times, update_times)])
        read_times = ReadTimes(*[statistics.mean(x) for x in (warm_times, cold_times, hot_times, steamin_times)])

        return times, write_times, read_times

class SpeedTest(AbstractSpeedTest):

    def _write_test_update_values(self, values):
        for obj in values:
            obj.attr = 1

    def _read_test_read_values(self, values):
        got = 0

        for obj in values:
            got += obj.attr
        return got

class BlobSpeedTest(SpeedTest):

    ObjectType = BlobObject

    def __init__(self, *args, **kwargs):
        super(BlobSpeedTest, self).__init__(*args, **kwargs)

        if self.object_size == pobject_base_size:
            # This won't be big enough to actually get any data.
            self.object_size = self.object_size * 2

    def _write_test_update_values(self, values):
        for obj in values:
            with obj.blob.open('w') as f:
                f.write(obj._v_seen_data)
            obj.attr = 1

    def _read_test_read_values(self, values):
        got = 0

        for obj in values:
            with obj.blob.open('r') as f:
                obj._v_seen_data = f.read
            got += obj.attr
        return got
