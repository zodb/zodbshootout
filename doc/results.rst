==========================
 ``zodbshootout`` Results
==========================

The table below shows typical output of running ``zodbshootout`` with
``etc/sample.conf`` on a dual core, 2.1 GHz laptop::

    "Transaction",                postgresql, mysql,   mysql_mc, zeo_fs
    "Add 1000 Objects",                 6529,   10027,     9248,    5212
    "Update 1000 Objects",              6754,    9012,     8064,    4393
    "Read 1000 Warm Objects",           4969,    6147,    21683,    1960
    "Read 1000 Cold Objects",           5041,   10554,     5095,    1920
    "Read 1000 Hot Objects",           38132,   37286,    37826,   37723
    "Read 1000 Steamin' Objects",    4591465, 4366792,  3339414, 4534382

``zodbshootout`` runs six kinds of tests for each database. For each
test, ``zodbshootout`` instructs all processes (or threads or
greenlets, as configured) to perform similar transactions
concurrently, computes the mean duration of the concurrent
transactions, takes the mean timing of three test runs, and derives
how many objects per second the database is capable of writing or
reading under the given conditions.

``zodbshootout`` runs these tests:

* Add objects

    ``zodbshootout`` begins a transaction, adds the specified number
    of persistent objects to a
    :class:`~persistent.mapping.PersistentMapping`, or ``BTree`` and
    commits the transaction. In the sample output above, MySQL was
    able to add 10027 objects per second to the database, almost twice
    as fast as ZEO, which was limited to 5212 objects per second.
    Also, with memcached support enabled, MySQL write performance took
    a small hit due to the time spent storing objects in memcached.

* Update objects

    In the same process, without clearing any caches, ``zodbshootout``
    makes a simple change to each of the objects just added and commits
    the transaction.  The sample output above shows that MySQL and ZEO
    typically take a little longer to update objects than to add new
    objects, while PostgreSQL is faster at updating objects in this case.
    The sample tests only history-preserving databases; you may see
    different results with history-free databases.

* Read warm objects

    In a different process, without clearing any caches,
    ``zodbshootout`` reads all of the objects just added. This test
    favors databases that use either a persistent cache or a cache
    shared by multiple processes (such as memcached). In the sample
    output above, this test with MySQL and memcached runs more than ten
    times faster than ZEO without a persistent cache. (See
    ``fs-sample.conf`` for a test configuration that includes a ZEO
    persistent cache.)

* Read cold objects

    In the same process as was used for reading warm objects,
    ``zodbshootout`` clears all ZODB caches (the pickle cache, the ZEO
    cache, and/or memcached) then reads all of the objects written by
    the update test. This test favors databases that read objects
    quickly, independently of caching. The sample output above shows
    that cold read time is currently a significant ZEO weakness.

* Read hot objects

    In the same process as was used for reading cold objects,
    ``zodbshootout`` clears the in-memory ZODB caches (the pickle
    cache), but leaves the other caches intact, then reads all of the
    objects written by the update test. This test favors databases that
    have a process-specific cache. In the sample output above, all of
    the databases have that type of cache.

* Read steamin' objects

    In the same process as was used for reading hot objects,
    ``zodbshootout`` once again reads all of the objects written by the
    update test. This test favors databases that take advantage of the
    ZODB pickle cache. As can be seen from the sample output above,
    accessing an object from the ZODB pickle cache is around 100
    times faster than any operation that requires network access or
    unpickling.
