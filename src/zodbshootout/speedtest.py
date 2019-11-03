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

# pylint:disable=too-many-locals

# This file is imported after gevent monkey patching (if applicable)
# so it's safe to import anything.
import gc
import functools
import random

from pyperf import perf_counter

import transaction

from persistent.mapping import PersistentMapping

from zope.interface import implementer
from ZODB.Connection import TransactionMetaData
from ZODB.serialize import ObjectWriter
from ZODB.utils import u64
from ZODB.utils import z64

from .interfaces import IDBBenchmarkCollection
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

def iteritems(d):
    try:
        iv = d.iteritems
    except AttributeError:
        iv = d.items
    return iv()


random.seed(__file__) # reproducible random functions

class timer(object):
    begin = None
    end = None
    duration = None

    def __enter__(self):
        self.begin = perf_counter()
        return self

    def __exit__(self, t, v, tb):
        self.end = perf_counter()
        self.duration = self.end - self.begin

def log_timed(func):
    @functools.wraps(func)
    def f(*args, **kwargs):
        t = timer()
        with t:
            result = func(*args, **kwargs)
        logger.debug("Function %s took %s", func.__name__, t.duration)
        return result
    return f


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
                 use_blobs=False, pack_on_populate=False):
        self.objects_per_txn = objects_per_txn
        self.object_size = object_size
        self.__random_data = []
        self.concurrency = num_workers
        self.pack_on_populate = pack_on_populate

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

    with open(__file__, 'rb') as _f:
        # Just use the this module as the source of our data
        # A list of byte words
        _RANDOM_FILE_DATA = _f.read().replace(b'\n', b'').split()
    del _f

    def __data_chunk_generator(self, chunksize=1024):
        r = random.Random()
        words = self._RANDOM_FILE_DATA
        chunksize = min(chunksize, 1024)
        while True:
            sample = r.sample(words, len(words) // 10)
            yield b' '.join(sample[0:chunksize])

    def _random_data(self, size):
        """
        Create a random data of at least the given size.

        Use pseudo-random data in case compression is in play so we get a more realistic
        size and time value than a single 'x'*size would get.
        """
        datagen = self.__data_chunk_generator(size)

        while True:
            data = b''
            while len(data) < size:
                data += next(datagen) # pylint:disable=stop-iteration-return
            yield data

    def _guarantee_min_random_data(self, count):
        if len(self.__random_data) < count:
            needed = count - len(self.__random_data)
            data_size = max(10, self.object_size - pobject_base_size)
            datagen = self._random_data(data_size)
            self.__random_data.extend([next(datagen) for _ in range(needed)])

    def data_to_store(self, count=None, begin_key=0):
        """
        Return a dictionary with numeric keys from *begin_key*
        up to *count* plus *begin_key*.

        The values are persistent objects.
        """
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
        return dict((n, kind(data[n])) for n in range(begin_key, count + begin_key))

    @property
    def _configuration_name(self):
        """
        The name that uniquely describes this particular configuration,
        to be used as a destination in the database.

        This ensures that different runs don't interfere with each other
        or throw unexpected errors when the DB isn't zapped.
        """
        # Right now, this just incorporates the number of objects,
        # but it could conceivably include object size
        return 'object_count_%d' % self.objects_per_txn

    def data_for_worker(self, root, worker):
        """
        Return the mapping that *worker* should use to read or write.
        """
        return self.data_for_worker_id(root, worker.worker_number)

    def data_for_worker_id(self, root, worker_id):
        """
        Return the mapping that *worker_id* should use to read or write.
        """
        return root['speedtest'][self._configuration_name][worker_id]

    @log_timed
    def populate(self, db_factory, include_data=False):
        """
        Opens the database and a connection, creates the necessary
        tree structure, commits the transaction, and closes the database.
        """
        self._guarantee_min_random_data(self.objects_per_txn)

        db = db_factory()
        conn = db.open()
        root = conn.root()
        self._populate_into_open_database(db, conn, root, include_data)
        conn.cacheMinimize()
        # Clear transfer counts so as not to mess up any downstream
        # assertions about what they should be.
        conn.getTransferCounts(True)
        conn.close()
        db.close()

    CONFLICT_IDENTIFIER = "conflicts"

    def _populate_into_open_database(self, db, conn, root, include_data):
        """
        Commits the transaction.
        """
        # We explicitly leave the `speedtest_min` value around
        # so that it can survive packs.
        if self.pack_on_populate:
            # clear the database
            root['speedtest'] = None
            transaction.commit()
            db.pack()

        # Make sure the minimum objects are present. Not all storages
        # support __len__ to return the size of the database.
        # FileStorage, RelStorage and ClientStorage do.

        db_count = max(len(db.storage), len(conn._storage))
        if self.min_object_count:
            needed = max(self.min_object_count - db_count, 0)
            if needed:
                logger.debug("Adding %d objects to a DB of size %d",
                             needed, db_count)
                self.__install_count_objects(needed, conn)
                logger.info("Added %d objects to a DB of size %d",
                            needed, db_count)
        db_count = max(len(db.storage), len(conn._storage))
        logger.debug("Working with database %s of size %d",
                     db, db_count)

        # Put a tree in the database, being careful not to overwrite
        # anything that's already usefully there.
        # speedtest (dict)
        # |\
        # | + object_count_N (maps)
        # |  \
        # |   + 1...M (maps for each worker; possibly empty or full)
        # |   + conflicts (a shared map, full of N objects)

        def install_mapping(parent, key, kind=self.MappingType):
            if not isinstance(parent.get(key), kind):
                parent[key] = kind()
            return parent[key]

        # The top two levels are persistent mappings so we can
        # use string keys; we'll never mutate them during the course of
        # a benchmark run.
        speedtest = install_mapping(root, 'speedtest', kind=PersistentMapping)
        config = install_mapping(speedtest, self._configuration_name, kind=PersistentMapping)
        for worker_num in range(self.concurrency):
            worker_map = install_mapping(config, worker_num)
            if include_data and len(worker_map) != self.objects_per_txn:
                worker_map.update(self.data_to_store())
                assert len(worker_map) == self.objects_per_txn

        conflicts = install_mapping(config, self.CONFLICT_IDENTIFIER)
        if not conflicts:
            conflicts.update(self.data_to_store())
        assert len(conflicts) == self.objects_per_txn
        transaction.commit()
        logger.debug('Populated storage.')

    def make_pickles(self, count, conn=None):
        writer = ObjectWriter()
        writer._p_jar = conn
        self._guarantee_min_random_data(count)

        pickles = [writer.serialize(self.ObjectType(rd))
                   for rd
                   in self.__random_data[:count]]
        return pickles

    def __install_count_objects(self, needed, conn):
        # If `needed` is large, this could result in a single
        # very large transaction. We therefore split it up
        # into smaller, more manageable transactions. we use
        # the same transaction size we'll use for the regular
        # tests so the user can influence how many transactions it takes
        # (which matters in RelStorage history-preserving mode).

        # We pickle up front, because that's the slowest part, it
        # seems. However, all these objects are *not* reachable from
        # any other object, they're garbage. (TODO: Add them by OID to
        # a btree collection?)
        chunk_size = min(needed, max(self.objects_per_txn, 10000))
        pickles = self.make_pickles(chunk_size, conn)

        storage = conn._storage
        while needed > 0:
            logger.debug("Generate data")
            needed -= len(pickles)
            t = TransactionMetaData()
            storage.tpc_begin(t)
            for pickle in pickles:
                storage.store(storage.new_oid(), 0, pickle, '', t)
            storage.tpc_vote(t)
            storage.tpc_finish(t)

            logger.debug("Added %d objects; %d to go.", len(pickles), needed)


def _inner_loops(f):
    # When a function is accessed, record that it does
    # inner loops
    # Use inner loops when the functions being benchmarked are very short
    # and the overhead of more outer loops slows things down. I.e., the
    # setup cost of just entering the function is high.
    f.inner_loops = True
    return f

def _no_inner_loops(f):
    # Don't use inner loops when the functions being benchmarked
    # are relatively slow.
    f.inner_loops = False
    return f

@implementer(IDBBenchmarkCollection)
class SpeedTestWorker(object):
    # pylint:disable=too-many-public-methods
    worker_number = 0
    inner_loops = 10

    def __init__(self, worker_number, data):
        self.data = data
        self.worker_number = worker_number

    @property
    def objects_per_txn(self):
        return self.data.objects_per_txn

    def data_to_store(self):
        return self.data.data_to_store()

    def zap_database(self, db_factory):
        # Do actions before initiating the add benchmark.
        self.sync_before_zap_database()
        if self.worker_number == 0:
            logger.debug("In master, zapping database")
            # Only the master process needs to zap.
            # zapping could be a no-op.
            db = db_factory()
            db.close()
            zap = log_timed(db.speedtest_zap_all)
            zap()
            # Populate everything back
            logger.debug("In master, populating database")
            self.data.populate(db_factory)
        self.sync_after_zap_database()

    def sync_before_zap_database(self):
        self.sync('before zap')

    def sync_after_zap_database(self):
        self.sync('after zap')

    def sync(self, name):
        # Replace this with something that does something if you
        # know you need to really sync up to prevent interference.
        # This is the primitive function that all other sync operations
        # are built on.
        pass

    def should_clear_all_caches(self):
        # By default, only the master should do that.
        # But MP tasks will do different.
        return self.worker_number == 0

    def sync_before_clear_caches(self):
        # Because we're going to go over all connections in the
        # database pool, we need to sync all users of the database
        # object to be sure no one is using a connection. Note that mp
        # tasks don't need to do this.
        self.sync('before clear')

    def sync_after_clear_caches(self):
        # Don't let anyone start running until we're all done clearing
        # caches. Again, MP tasks don't need to do this.
        self.sync('after clear')

    def sync_before_timing_loop(self, name):
        # syncing here, before actually beginning our loops, ensures
        # that no client gets into a CPU intensive loop while other
        # clients have yielded to the network (released the GIL for
        # socket writes, or protocol processing, or switched to
        # another greenlet).
        self.sync(name)

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
        begin = perf_counter()
        self.sync('before clear')
        if self.should_clear_all_caches():
            db.speedtest_log_cache_stats("Clearing all caches in worker %s" % (
                self.worker_number,
            ))

            # db.pool.map went away in 98dc5f1ea0ac87205a4b545400de2049994511c7 but
            # before that the pool was not iterable, nor was its container of
            # objects (transaction.weakset.WeakSet).
            if hasattr(db.pool.all, 'as_weakref_list'):
                conns = (wr() for wr in db.pool.all.as_weakref_list())
            else:
                conns = db.pool.all
            for c in (_c for _c in conns if _c is not None):
                c.cacheMinimize()
            # In ZODB 5, db.storage is the storage object passed to the DB object.
            # If it doesn't implement IMVCCStorage, then an adapter is wrapped
            # around it to make it do so (if it does, no such adapter is needed).
            # This is placed in _mvcc_storage. RelStorage is IMVCCStorage; ZEO is not.
            # Both of them have a `_cache` that needs cleared. We do not need to open a
            # connection for this to happen. The cache is shared among all connections
            # in both cases.
            before_clear = perf_counter()
            if hasattr(db.storage, '_cache'):
                db.storage._cache.clear()

            # We probably just made a bunch of garbage. Try to get rid of it
            # before we go in earnest to eliminate the knock-on effect.
            # We're already in the master, and it's not safe to call sync()
            # again, so just do it.
            before_gc = perf_counter()
            gc.collect()
            end = perf_counter()
            logger.debug(
                "Cleared caches in %s; storage clear: %s; gc: %s",
                end - begin, before_gc - before_clear, end - before_gc
            )

        self.sync('after clear')

    def __check_access_count(self, accessed, loops=1):
        if accessed != self.objects_per_txn * loops:
            raise AssertionError('data mismatch; expected %s got %s' % (
                self.objects_per_txn, accessed // loops))

    def __conn_did_not_load(self, conn):
        loads, _ = conn.getTransferCounts(True)
        if loads != 0:
            raise AssertionError("Loaded data; expected 0, got %s" % (loads,))

    def __conn_did_not_store(self, conn):
        _, stores = conn.getTransferCounts(True)
        if stores != 0:
            raise AssertionError("Stored data; expected 0, got %s" % (stores,))

    def __conn_did_load_objects(self, conn, loops=1, data=None):
        loads, _ = conn.getTransferCounts(True)
        if loads < self.objects_per_txn * loops:
            raise AssertionError(
                "Didn't load enough data from %s; expected %s, got %s (out of %s at %s)" % (
                    conn,
                    self.objects_per_txn * loops,
                    loads,
                    len(data) if data is not None else None,
                    (u64(data._p_serial), u64(data._p_oid)) if data is not None else  None
                ))

    # Important: If you haven't done anything to join to a
    # transaction, don't even bother aborting/committing --- we're not
    # generally trying to measure the synchronizer behaviour that can
    # happen at transaction boundaries. (Differences in RelStorage
    # drivers can show up there, which is mightily confusing on what's
    # supposed to be a simple CPU bound loop --- this can be up to an
    # order of magnitude, especially with cooperative multitasking.)

    @_inner_loops
    def bench_add(self, loops, db_factory):
        self.zap_database(db_factory)

        db = db_factory()
        assert db is not None, db_factory
        duration = 0

        transaction_manager = transaction.TransactionManager(explicit=True)
        conn = db.open(transaction_manager)
        transaction_manager.begin()
        root = conn.root()
        transaction_manager.commit()

        for _ in range(loops):
            m = self.data.data_for_worker(root, self)
            for _ in range(self.inner_loops):
                # NOTE: This is measuring the time to update the
                # dictionary (non-storage related), and commit, which
                # means the time to serialize all the objects (storage
                # independent) and assign them oids, plus write them
                # to storage, and then poll for
                # invalidations/afterCompletion (which depends on
                # whether the transaction is explicit or not).
                begin = perf_counter()
                transaction_manager.begin()
                m.update(self.data_to_store())
                transaction_manager.commit()
                end = perf_counter()
                duration += (end - begin)
                # XXX: Why would we sync here? That really slows us down
                # self.sync('add loop')
        conn.close()
        db.close()

        return duration

    @_inner_loops
    def bench_store(self, loops, db_factory):
        """
        Time the minimum to store objects. Pickling and allocating OIDs
        and syncing the storage are all left out.

        Note that this leaves unreachable objects in the database.
        """
        db = db_factory()
        conn = db.open()
        pickles = self.data.make_pickles(self.objects_per_txn, conn)
        storage = conn._storage
        oids = None
        duration = 0
        prev_tid = z64
        for _ in range(loops * self.inner_loops):
            t = TransactionMetaData()
            storage.tpc_begin(t)

            try:
                if not oids:
                    oids = [storage.new_oid() for _ in pickles]

                begin = perf_counter()
                for pickle, oid in zip(pickles, oids):
                    storage.store(oid, prev_tid, pickle, '', t)

                storage.tpc_vote(t)
                prev_tid = storage.tpc_finish(t)

                end = perf_counter()
                duration += (end - begin)
            except:
                storage.tpc_abort(t)
                raise

        conn.close()
        db.close()

        return duration


    @_inner_loops
    def bench_update(
            self, loops, db_factory,
            worker_id=None,
            data_selector=itervalues,
    ):
        db = db_factory()
        transaction_manager = transaction.TransactionManager(explicit=True)
        conn = db.open(transaction_manager)

        count_updated = 0
        duration = 0.0
        for _ in range(loops * self.inner_loops):
            begin = perf_counter()
            transaction_manager.begin()
            root = conn.root()
            m = self.data.data_for_worker_id(root, worker_id or self.worker_number)
            count_updated += self.data.write_test_update_values(data_selector(m))
            transaction_manager.commit()
            end = perf_counter()
            duration += (end - begin)

        conn.close()
        if data_selector is itervalues:
            # We can only assert that we hit the right number if we're
            # not doing fancy selection.
            self.__check_access_count(count_updated, loops * self.inner_loops)
        db.close()
        return duration

    @_inner_loops
    def bench_conflicting_updates(self, loops, db_factory):
        # Select either evens or odds to write to, based on our own
        # worker number; thus each worker conflicts on half the
        # objects with half the other workers. This interleaves the
        # writes such that storages that don't lock the entire
        # database may be able to make more progress.
        selector_value = self.worker_number % 2
        def selector(m):
            for k, v in iteritems(m):
                if k % 2 == selector_value:
                    yield v
        return self.bench_update(loops, db_factory, self.data.CONFLICT_IDENTIFIER, selector)

    @_no_inner_loops
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
        for i in range(loops):
            db = db_factory()

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
            # See bench_cold_read for why we don't clear at the end.
            is_last_loop = (i == loops - 1)
            if not is_last_loop:
                self._clear_all_caches(db)
            db.close()

        self.__check_access_count(got, loops)
        return duration

    def __do_bench_cold_read(self, loops, db_factory, prefetch):
        # pylint:disable=too-many-locals
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
        total_loops = loops * self.inner_loops

        for i in range(total_loops):
            db = db_factory()

            begin = perf_counter()
            conn = db.open()
            root = conn.root()
            m = self.data.data_for_worker(root, self)
            if prefetch:
                conn.prefetch(itervalues(m))
            got += self.data.read_test_read_values(itervalues(m))
            end = perf_counter()
            duration += (end - begin)

            self.__conn_did_load_objects(conn, data=m)
            conn.close()
            # RelStorage likes to write its cache on close, so to get the best
            # persistent cache, we want to clear up front, or at least be sure
            # not to clear before we return.
            is_last_loop = (i == total_loops - 1)
            if not is_last_loop:
                self._clear_all_caches(db)

            db.close()

        self.__check_access_count(got, total_loops)
        return duration

    @_inner_loops
    def bench_cold_read(self, loops, db_factory):
        return self.__do_bench_cold_read(loops, db_factory, False)

    @_inner_loops
    def bench_cold_read_prefetch(self, loops, db_factory):
        return self.__do_bench_cold_read(loops, db_factory, True)

    def __prime_caches(self, db):
        # conn.prefetch() may or may not do anything, so
        # we actually access the data. This also ensure the objects
        # are unghostified.
        conn = db.open()
        root = conn.root()
        m = self.data.data_for_worker(root, self)
        got = self.data.read_test_read_values(itervalues(m))
        self.__check_access_count(got, 1)
        # Clear the transfer counts before continuing
        conn.getTransferCounts(True)
        return conn, root

    @_inner_loops
    def bench_steamin_read(self, loops, db_factory):
        db = db_factory()

        # First, prime the cache
        conn, root = self.__prime_caches(db)

        # All the objects should now be cached locally, in the pickle cache
        # and in the storage cache, if any. We won't need to load anything
        # from the storage, all the objects are already unghosted.
        # This serves as a test of iteration speed and the basic `persistence`
        # machinery.
        got = 0
        m = self.data.data_for_worker(root, self)
        self.sync_before_timing_loop('steamin')
        begin = perf_counter()
        for _ in range(loops):
            for _ in range(self.inner_loops):
                got += self.data.read_test_read_values(itervalues(m))
        end = perf_counter()

        self.__check_access_count(got, loops * self.inner_loops)
        self.__conn_did_not_load(conn)

        conn.close()
        db.close()
        return end - begin

    @_inner_loops
    def bench_hot_read(self, loops, db_factory):
        # In this test, we want all secondary caches to be well populated,
        # but the connection pickle cache should be empty.
        db = db_factory()

        # First, prime the cache
        conn, root = self.__prime_caches(db)

        duration = 0
        got = 0
        for i in range(loops * self.inner_loops):
            # Clear the pickle cache of unghosted objects; they'll
            # all have to be reloaded. (We don't directly check the pickle
            # cache length here, but we do verify that the connection has loaded
            # objects_per_txn * loops below.)
            conn.cacheMinimize()
            # As for 'steamin', sync before looping.
            self.sync_before_timing_loop('begin hot ' + str(i))
            m = self.data.data_for_worker(root, self)
            begin = perf_counter()
            got += self.data.read_test_read_values(itervalues(m))
            end = perf_counter()
            duration += (end - begin)

        self.__check_access_count(got, loops * self.inner_loops)
        self.__conn_did_load_objects(conn, loops)

        conn.close()
        db.close()
        return duration

    @_inner_loops
    def bench_empty_transaction_commit_explicit(self, loops, db_factory, explicit=True):
        db = db_factory()
        # When we open a connection, it adds itself to the transaction
        # manager as a synchronizer.
        # This does things when transactions are opened and closed.
        # For example, it synchronizes the storage.
        # The same set of calls happens for commit or abort.
        if explicit:
            transaction_manager = transaction.TransactionManager(explicit=True)
        else:
            transaction_manager = None

        conn = db.open(transaction_manager)
        conn.getTransferCounts(True) # clear them

        transaction_manager = conn.transaction_manager
        if explicit:
            before_commit = transaction_manager.begin
            assert conn.explicit_transactions
        else:
            before_commit = transaction_manager.get

        commit = transaction_manager.commit
        begin = perf_counter()
        for _ in range(loops * self.inner_loops):
            before_commit()
            commit()
        end = perf_counter()

        self.__conn_did_not_load(conn)
        conn.close()
        db.close()
        return end - begin

    @_inner_loops
    def bench_empty_transaction_commit_implicit(self, loops, db_factory):
        return self.bench_empty_transaction_commit_explicit(loops, db_factory, False)

    @_inner_loops
    def bench_tpc(self, loops, db_factory):
        db = db_factory()

        conn = db.open()
        storage = conn._storage

        begin = perf_counter()
        for _ in range(loops * self.inner_loops):
            tx = TransactionMetaData()
            storage.tpc_begin(tx)
            storage.tpc_vote(tx)
            storage.tpc_finish(tx)
        end = perf_counter()

        conn.close()
        db.close()
        return end - begin


    @_inner_loops
    def bench_readCurrent(self, loops, db_factory):
        """
        Tests the effect of adding calls to `Connection.readCurrent`
        for all objects, even though we do not mutate anything.
        """

        # Connection.readCurrent only interacts with the storage when
        # it is joined to a transaction; Connection only joins a
        # transaction when an object is modified or added, via a call
        # to register() or add(). We don't want to actually modify anything,
        # so we artificially register.
        #
        # Also, objects can only go into readCurrent when they have a
        # _p_serial, which they only get once unghosted.
        db = db_factory()
        conn, root = self.__prime_caches(db)

        begin = perf_counter()
        for _ in range(loops * self.inner_loops):
            transaction.begin()
            m = self.data.data_for_worker(root, self)
            conn.register(m)
            rc = conn.readCurrent
            for v in itervalues(m):
                rc(v)
            transaction.commit()
        end = perf_counter()
        self.__conn_did_not_store(conn)
        conn.close()
        db.close()
        return end - begin

    @_inner_loops
    def bench_new_oid(self, loops, db_factory):
        """
        Tests how fast new OIDs can be allocated.

        This isolates one particular part of the storage API,
        which is otherwise easily hidden in adding new objects.

        We join the transaction and commit at the end in case there is
        any locking or conflict resolution that the storage does.
        The transaction is handled manually.
        """
        duration = 0
        db = db_factory()

        conn = db.open()
        storage = conn._storage

        total_loops = range(loops * self.inner_loops)
        to_allocate = range(self.objects_per_txn)
        for _ in total_loops:
            tx = TransactionMetaData()
            storage.tpc_begin(tx)

            begin = perf_counter()
            for _ in to_allocate:
                storage.new_oid()
            storage.tpc_vote(tx)
            storage.tpc_finish(tx)
            end = perf_counter()

            duration += (end - begin)

        conn.close()
        db.close()
        return duration

class ForkedSpeedTestWorker(SpeedTestWorker):
    # Used when concurrency is achieved through multiple
    # processes, with unique DB objects.

    # Most syncs are no-ops because no objects or locks are shared,
    # but zapping the database does need synchronization because the
    # database is shared externally.

    def should_clear_all_caches(self):
        return True

    def sync_before_clear_caches(self):
        pass

    def sync_after_clear_caches(self):
        pass

    def sync_before_timing_loop(self, name):
        pass
