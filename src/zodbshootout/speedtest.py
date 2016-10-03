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

import os
import sys
import statistics
import time
import transaction
import random
import cProfile
from collections import namedtuple
from pstats import Stats
from itertools import chain

from persistent.mapping import PersistentMapping

from .fork import distribute
from .fork import run_in_child
from ._pobject import pobject_base_size
from ._pobject import PObject

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

class SpeedTest(object):

    MappingType = PersistentMapping
    debug = False

    individual_test_reps = 20

    def __init__(self, concurrency, objects_per_txn, object_size,
                 profile_dir=None,
                 mp_strategy='mp',
                 test_reps=None):
        self.concurrency = concurrency
        self.objects_per_txn = objects_per_txn
        self.object_size = object_size
        self.profile_dir = profile_dir
        self.contender_name = None
        self.mp_strategy = mp_strategy
        if test_reps:
            self.individual_test_reps = test_reps
        self.rep = 0  # repetition number

    @property
    def data_to_store(self):
        # Must be fresh when accessed because could already
        # be stored in another database if we're using threads
        data_size = max(0, self.object_size - pobject_base_size)
        return dict((n, PObject(_random_data(data_size))) for n in range(self.objects_per_txn))

    def populate(self, db_factory):
        db = db_factory()
        conn = db.open()
        root = conn.root()

        # clear the database
        root['speedtest'] = None
        transaction.commit()
        db.pack()

        # put a tree in the database
        root['speedtest'] = t = self.MappingType()
        for i in range(self.concurrency):
            t[i] = self.MappingType()
        transaction.commit()
        conn.close()
        db.close()
        if self.debug:
            print('Populated storage.', file=sys.stderr)

    def _clear_all_caches(self, db):
        # Clear all caches
        conn = db.open()
        conn.cacheMinimize()
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


    def write_test(self, db_factory, n, sync):
        db = db_factory()

        def do_add():
            start = time.time()
            conn = db.open()
            root = conn.root()
            m = root['speedtest'][n]
            m.update(self.data_to_store)
            transaction.commit()
            conn.close()
            end = time.time()
            return end - start

        db.open().close()
        sync()
        add_time = self._execute(self._times_of_runs, 'add', n,
                                 do_add, self.individual_test_reps)

        def do_update():
            start = time.time()
            conn = db.open()
            root = conn.root()
            for obj in itervalues(root['speedtest'][n]):
                obj.attr = 1
            transaction.commit()
            conn.close()
            end = time.time()
            return end - start

        sync()
        update_time = self._execute(self._times_of_runs, 'update', n,
                                    do_update, self.individual_test_reps)

        time.sleep(.1)
        db.close()
        return [WriteTimes(a, u) for a, u in zip(add_time, update_time)]

    def read_test(self, db_factory, n, sync):
        db = db_factory()
        # Explicitly set the number of cached objects so we're
        # using the storage in an understandable way.
        # Set to double the number of objects we should have created
        # to account for btree nodes.
        db.setCacheSize(self.objects_per_txn * 2)

        def do_read(clear_all=False, clear_conn=False):
            if clear_all:
                self._clear_all_caches(db)
            start = time.time()
            conn = db.open()
            if clear_conn:
                conn.cacheMinimize()
                conn.close()
                conn = db.open()

            got = 0

            for obj in itervalues(conn.root()['speedtest'][n]):
                got += obj.attr
            obj = None
            if got != self.objects_per_txn:
                raise AssertionError('data mismatch')
            conn.close()
            end = time.time()
            return end - start

        db.open().close()
        sync()
        warm = self._execute(do_read, 'warm', n)

        sync()
        cold = self._execute(self._times_of_runs, 'cold', n,
                             do_read, self.individual_test_reps, (True, True))

        sync()
        hot = self._execute(self._times_of_runs, 'hot', n,
                            do_read, self.individual_test_reps, (False, True))

        sync()
        steamin = self._execute(self._times_of_runs, 'steamin', n,
                                do_read, self.individual_test_reps)

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

        def write(n, sync):
            return self.write_test(db_factory, n, sync)
        def read(n, sync):
            return self.read_test(db_factory, n, sync)

        r = list(range(self.concurrency))
        write_times = distribute(write, r, strategy=self.mp_strategy)
        read_times = distribute(read, r, strategy=self.mp_strategy)

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
