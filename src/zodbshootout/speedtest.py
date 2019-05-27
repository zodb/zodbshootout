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

from pstats import Stats


import cProfile

import random

from pyperf import perf_counter

import transaction

from persistent.mapping import PersistentMapping
from persistent.list import PersistentList

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

class SpeedTestData(object):

    MappingType = PersistentMapping
    ObjectType = PObject
    min_object_count = 0


    class AttributeAccessor(object):

        def write_test_update_values(self, values):
            count = 0
            for obj in values:
                obj.attr = 1
                count += 1
            return count

        def read_test_read_values(self, values):
            got = 0

            for obj in values:
                got += obj.attr
            return got

    class BlobAccessor(object):

        ObjectType = BlobObject

        def write_test_update_values(self, values):
            count = 0
            for obj in values:
                with obj.blob.open('w') as f:
                    f.write(obj._v_seen_data)
                obj.attr = 1
                count += 1
            return count

        def read_test_read_values(self, values):
            got = 0

            for obj in values:
                with obj.blob.open('r') as f:
                    obj._v_seen_data = f.read
                got += obj.attr
            return got


    def __init__(self, num_workers, objects_per_txn, object_size,
                 use_blobs=False):
        self.objects_per_txn = objects_per_txn
        self.object_size = object_size
        self.__random_data = []
        self.concurrency = num_workers

        if use_blobs:
            self.ObjectType = BlobObject

            if self.object_size == pobject_base_size:
                # This won't be big enough to actually get any data.
                self.object_size = self.object_size * 2
            accessor = self.BlobAccessor()
        else:
            accessor = self.AttributeAccessor()

        self.read_test_read_values = accessor.read_test_read_values
        self.write_test_update_values = accessor.write_test_update_values

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

    def data_for_worker(self, root, worker):
        return root['speedtest'][worker.worker_number]

    def populate(self, db_factory):
        self._guarantee_min_random_data(self.objects_per_txn)

        db = db_factory()
        conn = db.open()
        root = conn.root()
        self._populate_into_open_database(db, conn, root)
        conn.close()
        db.close()

    def _populate_into_open_database(self, db, conn, root):
        # clear the database
        root['speedtest'] = None
        # We explicitly leave the `speedtest_min` value around
        # so that it can survive packs.
        transaction.commit()
        # XXX: Why are we packing here?
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
        logger.debug('Populated storage.')


class SpeedTestWorker(object):
    worker_number = 0

    def __init__(self, worker_number, data):
        self.data = data
        self.worker_number = worker_number

    @property
    def objects_per_txn(self):
        return self.data.objects_per_txn

    def data_to_store(self):
        return self.data.data_to_store()

    def sync(self, name):
        # Replace this with something that does something if you
        # know you need to really sync up to prevent interference.
        pass

    def _clear_all_caches(self, db):
        # Clear all caches, returns how long that took.
        # No connection should be open when this is called.

        # This should be called in the master only.

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
        self.sync('before clear')
        if self.worker_number == 0:
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
            # before we go in earnest to eliminate the knock-on effect.
            # We're already in the master, and it's not safe to call sync()
            # again, so just do it.
            gc.collect()
        self.sync('after clear')

    def __check_access_count(self, accessed, loops=1):
        if accessed != self.objects_per_txn * loops:
            raise AssertionError('data mismatch; expected %s got %s' % (
                self.objects_per_txn, accessed))

    def __conn_did_not_load(self, conn):
        loads, _ = conn.getTransferCounts(True)
        if loads != 0:
            raise AssertionError("Loaded data; expected 0, got %s" % (loads,))

    def __conn_did_load_objects(self, conn, loops=1, data=None):
        loads, _ = conn.getTransferCounts(True)
        if loads < self.objects_per_txn * loops:
            raise AssertionError("Didn't load enough data from %s; expected %s, got %s (out of %s)" % (
                conn,
                self.objects_per_txn * loops,
                loads,
                len(data) if data is not None else None,
            ))

    def bench_add(self, loops, db_factory):
        db = db_factory()
        begin = perf_counter()
        conn = db.open()
        root = conn.root()

        for _ in range(loops):
            m = self.data.data_for_worker(root, self)
            m.update(self.data_to_store())
            transaction.commit()

        end = perf_counter()
        conn.close()
        db.close()
        duration = end - begin
        return duration

    def bench_update(self, loops, db_factory):
        db = db_factory()
        begin = perf_counter()
        conn = db.open()
        root = conn.root()
        got = 0
        for _ in range(loops):
            m = self.data.data_for_worker(root, self)
            got += self.data.write_test_update_values(itervalues(m))
            transaction.commit()
        end = perf_counter()

        conn.close()
        self.__check_access_count(got, loops)
        db.close()
        duration = end - begin
        return duration

    def bench_read_after_write(self, loops, db_factory):
        # This is what used to be called the 'warm' read:
        # Read back items in the next transaction that were just written in the
        # previous transaction. This benefits databases that have a shared
        # cache.

        # To capture the cache-churning effects this might have, we
        # include both read and write times. (This also means we have
        # to loop fewer times to get stable results.) The old 'warm'
        # didn't include the write time, and didn't loop at all.

        duration = 0
        got = 0
        for _ in range(loops):
            db = db_factory()
            db.setCacheSize(self.objects_per_txn * 3)

            begin = perf_counter()
            conn = db.open()
            root = conn.root()
            m = self.data.data_for_worker(root, self)
            self.data.write_test_update_values(itervalues(m))
            transaction.commit()

            got += self.data.read_test_read_values(itervalues(m))
            transaction.commit()
            end = perf_counter()
            duration += (end - begin)

            conn.close()
            self._clear_all_caches(db)
            db.close()

        self.__check_access_count(got, loops)
        return duration

    def bench_cold_read(self, loops, db_factory):
        # Because each of these is run in its own process, if we're
        # run first, then any in-memory cache is already as cold as
        # it's going to get. Of course, that's only true for our first run through,
        # so we need to carefully maintain that status as we go forward.

        # "icy" is defined as: No local storage cache, and no connection (pickle) cache;
        # we achieve this by closing the database on each run. (Of course, if we have
        # a mapping storage, we can't actually do that. So we also try to explicitly
        # clear caches.)
        duration = 0
        got = 0
        for _ in range(loops):
            db = db_factory()
            begin = perf_counter()
            conn = db.open()
            root = conn.root()

            m = self.data.data_for_worker(root, self)
            got += self.data.read_test_read_values(itervalues(m))
            transaction.commit()
            end = perf_counter()
            duration += (end - begin)

            self.__conn_did_load_objects(conn, data=m)
            conn.close()

            self._clear_all_caches(db)
            db.close()
            gc.collect()

        self.__check_access_count(got, loops)

        return duration

    def __prime_caches(self, db):
        # Explicitly set the number of cached objects so we're
        # using the storage in an understandable way.
        # Set to double the number of objects we should have created
        # to account for btree nodes.
        db.setCacheSize(self.objects_per_txn * 3)

        conn = db.open()
        root = conn.root()
        m = self.data.data_for_worker(root, self)
        got = self.data.read_test_read_values(itervalues(m))
        self.__check_access_count(got, 1)
        # Clear the transfer counts before continuing
        conn.getTransferCounts(True)
        return conn, root

    def bench_steamin_read(self, loops, db_factory):
        db = db_factory()

        # First, prime the cache
        conn, root = self.__prime_caches(db)

        # All the objects should now be cached locally, in the pickle cache
        # and in the storage cache, if any
        got = 0
        m = self.data.data_for_worker(root, self)
        begin = perf_counter()
        for _ in range(loops):
            got += self.data.read_test_read_values(itervalues(m))
            transaction.abort()
        end = perf_counter()

        self.__check_access_count(got, loops)
        self.__conn_did_not_load(conn)

        conn.close()
        db.close()
        return end - begin

    def bench_hot_read(self, loops, db_factory):
        # In this test, we want all secondary caches to be well populated,
        # but the connection pickle cache should be empty.
        db = db_factory()

        # First, prime the cache
        conn, root = self.__prime_caches(db)

        duration = 0
        got = 0
        for _ in range(loops):
            conn.cacheMinimize()
            m = self.data.data_for_worker(root, self)
            begin = perf_counter()
            got += self.data.read_test_read_values(itervalues(m))
            transaction.abort()
            end = perf_counter()
            duration += (end - begin)

        self.__check_access_count(got, loops)
        self.__conn_did_load_objects(conn, loops)

        conn.close()
        db.close()
        return duration
