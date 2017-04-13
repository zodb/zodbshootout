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

from collections import namedtuple
from functools import partial
from functools import wraps
from itertools import chain
from pstats import Stats

import cProfile

import os
import random
import statistics

import time
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
    then closes the DB and returns the time.

    The wrapped function takes the root of the connection as
    its first argument and then any other arguments.

    The transaction is committed before closing the connection
    and after running the function.
    """

    # We should always include conn.open() inside our times,
    # because it talks to the storage to poll invalidations.
    # conn.close() does not talk to the storage, but it does
    # do some cache maintenance and should be excluded if possible.

    def __init__(self, db):
        self.db = db

    def _close_conn(self, conn):
        loads, stores = conn.getTransferCounts(True)
        db_name = conn.db().database_name
        logger.debug("DB %s conn %s loads %s stores %s",
                     db_name, conn, loads, stores)
        conn.close()

    def __call__(self, func):
        _close_conn = self._close_conn
        db = self.db

        @wraps(func)
        def timer(*args, **kwargs):
            start = time.time()

            conn = db.open()

            func(conn, *args, **kwargs)

            transaction.commit()
            end = time.time()

            _close_conn(conn)
            return end - start
        return timer

class SpeedTest(object):

    MappingType = PersistentMapping
    ObjectType = PObject


    individual_test_reps = 20
    min_object_count = 0

    def __init__(self, concurrency, objects_per_txn, object_size,
                 profile_dir=None,
                 mp_strategy='mp',
                 test_reps=None,
                 use_blobs=False):
        self.concurrency = concurrency
        self.objects_per_txn = objects_per_txn
        self.object_size = object_size
        self.profile_dir = profile_dir
        self.contender_name = None
        if mp_strategy in ('shared', 'unique'):
            self.mp_strategy = 'threads'
        else:
            self.mp_strategy = mp_strategy

        if test_reps:
            self.individual_test_reps = test_reps
        self.rep = 0  # repetition number

        if mp_strategy == 'shared':
            self._wait_for_master_to_do = self._threaded_wait_for_master_to_do

        if use_blobs:
            self.ObjectType = BlobObject
            if object_size == pobject_base_size:
                # This won't be big enough to actually get any data.
                self.object_size = object_size * 2

    def _wait_for_master_to_do(self, _thread_number, _sync, func, *args):
        # We are the only thing running, this object is not shared,
        # func must always be called.
        func(*args)

    def _threaded_wait_for_master_to_do(self, thread_number, sync, func, *args):
        """
        Block all threads until *func* is called by the first thread (0).
        """
        sync()
        if thread_number == 0:
            func(*args)
        sync()

    def data_to_store(self, count=None):
        # Must be fresh when accessed because could already
        # be stored in another database if we're using threads
        if count is None:
            count = self.objects_per_txn
        data_size = max(0, self.object_size - pobject_base_size)
        kind = self.ObjectType
        return dict((n, kind(_random_data(data_size))) for n in range(count))

    def populate(self, db_factory):
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


    def _clear_all_caches(self, db):
        # Clear all caches
        # No connection should be open when this is called.

        db.pool.map(lambda c: c.cacheMinimize())
        conn = db.open()
        # Account for changes between ZODB 4 and 5,
        # where there may or may not be a MVCC adapter layer,
        # depending on storage type, so we check both.
        storage = conn._storage
        if hasattr(storage, '_cache'):
            storage._cache.clear()
        conn.close()

        if hasattr(db, 'storage') and hasattr(db.storage, '_cache'):
            db.storage._cache.clear()

    def _times_of_runs(self, func, times, args=()):
        run_times = [func(*args) for _ in range(times)]
        return run_times


    def write_test(self, db_factory, thread_number, sync):
        db = db_factory()

        @_TimedDBFunction(db)
        def do_add(conn):
            root = conn.root()
            m = root['speedtest'][thread_number]
            m.update(self.data_to_store())

        db.open().close()
        sync()
        add_time = self._execute(self._times_of_runs, 'add', thread_number,
                                 do_add, self.individual_test_reps)

        @_TimedDBFunction(db)
        def do_update(conn):
            root = conn.root()
            zs_update = self.ObjectType.zs_update
            for obj in itervalues(root['speedtest'][thread_number]):
                zs_update(obj)

        sync()
        update_time = self._execute(self._times_of_runs, 'update', thread_number,
                                    do_update, self.individual_test_reps)

        time.sleep(.1)
        # In shared thread mode, db.close() doesn't actually do anything.
        db.close()
        return [WriteTimes(a, u) for a, u in zip(add_time, update_time)]

    def read_test(self, db_factory, thread_number, sync):
        db = db_factory()
        # Explicitly set the number of cached objects so we're
        # using the storage in an understandable way.
        # Set to double the number of objects we should have created
        # to account for btree nodes.
        db.setCacheSize(self.objects_per_txn * 2)

        def do_read_loop(conn):
            got = 0

            zs_read = self.ObjectType.zs_read
            for obj in itervalues(conn.root()['speedtest'][thread_number]):
                got += zs_read(obj)
            obj = None
            if got != self.objects_per_txn:
                raise AssertionError('data mismatch')

        @_TimedDBFunction(db)
        def do_steamin_read(conn):
            do_read_loop(conn)

        def do_cold_read():
            # Clear *all* the caches before opening any connections or
            # beginning any timing
            self._wait_for_master_to_do(thread_number, sync, self._clear_all_caches, db)
            return do_steamin_read() # pylint:disable=no-value-for-parameter

        @_TimedDBFunction(db)
        def do_hot_read(conn):
            # sadly we have to include this time in our
            # timings because opening the connection must
            # be included. hopefully this is too fast to have an impact.
            conn.cacheMinimize()
            do_read_loop(conn)

        db.open().close()
        sync()
        # In shared thread mode, the 'warm' test, immediately following the update test,
        # is similar to the steamin test, because we're likely to get the same
        # Connection object again (because the DB wasn't really closed.)
        # Of course, this really only applies when self.concurrency is 1; for other
        # values, we can't be sure. Also note that we only execute this once
        # (because after that caches are primed).
        warm = self._execute(do_steamin_read, 'warm', thread_number)

        sync()
        cold = self._execute(self._times_of_runs, 'cold', thread_number,
                             do_cold_read, self.individual_test_reps)

        sync()
        hot = self._execute(self._times_of_runs, 'hot', thread_number,
                            do_hot_read, self.individual_test_reps)

        sync()
        steamin = self._execute(self._times_of_runs, 'steamin', thread_number,
                                do_steamin_read, self.individual_test_reps)

        db.close()
        return [ReadTimes(w, c, h, s) for w, c, h, s
                in zip([warm] * self.individual_test_reps,
                       cold, hot, steamin)]

    def _execute(self, func, phase_name, n, *args):
        if not self.profile_dir:
            return func(*args)

        basename = '%s-%s-%d-%02d-%d' % (
            self.contender_name, phase_name, self.objects_per_txn, n, self.rep)
        txt_fn = os.path.join(self.profile_dir, basename + ".txt")
        prof_fn = os.path.join(self.profile_dir, basename + ".prof")

        profiler = cProfile.Profile()
        profiler.enable()
        try:
            res = func(*args)
        finally:
            profiler.disable()

        profiler.dump_stats(prof_fn)

        with open(txt_fn, 'w') as f:
            st = Stats(profiler, stream=f)
            st.strip_dirs()
            st.sort_stats('cumulative')
            st.print_stats()

        return res

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
