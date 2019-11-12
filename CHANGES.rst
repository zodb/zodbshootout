=========
 Changes
=========

0.8.0 (2019-11-12)
==================

- Fix ``--min-objects``. Previously it did nothing.

- Add ``--pack`` to pack each storage before running.

- Let the ``--log`` option take a path to a ZConfig logging
  configuration file that will be used to configure logging. This
  allows fine-grained control over log levels.

- Add a benchmark (``prefetch_cold``) to test the effect of bulk
  prefetching objects into the storage cache.

- Add a benchmark (``readCurrent``) to test the speed of using
  ``Connection.readCurrent`` (specifically, to see how well it can
  parallelize).

- Add a benchmark (``tpc``) that explicitly (and only) tests moving
  through the three phases of a successful transaction commit on a storage.

- Make pre-loading objects with ``--min-objects`` faster by using
  pre-serialized object data.

- Increase the default size of objects to 300 bytes, and make it the
  same on Python 2 and Python 3. This closely matches the measurement
  of the average object size in a large production database (30
  million objects).

- Add a benchmark for allocating new OIDs. See :issue:`47`.

- Add a benchmark for conflict resolution, designed to emphasize
  parallel commit. See :issue:`46`.

- Add a benchmark focused *just* on storing new objects, eliminating
  the pickling and OID allocation from the timing. See :issue:`49`.

- Enhance the transaction commit benchmarks to show the difference
  between implicit commit and explicit commit. ZODB makes extra
  storage calls in the implicit case.

- Add support for Python 3.8.

- Allow excluding particular benchmarks on the command line. For
  example, ``-cold``.

- When benchmarking multiple ZODB configurations, run a particular
  benchmark for all databases before moving on to the next benchmark. Previously
  all benchmarks for a database were run before moving on to the next
  database. This makes it a bit easier to eyeball results as the
  process is running.

0.7.0 (2019-05-31)
==================

- Drop support for Python 3.4.
- Add support for Python 3.7.
- The timing loops have been rewritten on top of `pyperf
  <https://pyperf.readthedocs.io/en/latest/index.html>`_. This
  produces much more reliable/stable, meaningful data with a richer set of
  statistics information captured, and the ability to do analysis and
  comparisons on data files captured after a run is complete. Some
  command line options have changed as a result of this, and the
  output no longer is in terms of "objects per second" but how long a
  particular loop operation takes. See :issue:`37` and :issue:`35`.
- The timing data for in-process concurrency (gevent and threads)
  attempts to take the concurrency into account to produce more
  accurate results. See :issue:`38`.
- Add debug logging when we think we detect gevent-cooperative and
  gevent-unaware databases in gevent mode.
- Add the ability to specify only certain subsets of benchmarks to run
  on the command line. In particular, if you've already run the
  ``add`` benchmark once, you can run other benchmarks such as the
  ``cold`` benchmark again independently as many times as you want (as
  long as you don't ``zap`` the database; that's not allowed).
- The benchmarks more carefully verify that they tested what they
  wanted to. For example, they check that their Connection's load count
  matches what it should be (0 in the case of the "steamin" test).
- Profiling in gevent mode captures the entire set of data in a single
  file. See :issue:`33`.
- The ``--zap`` option accepts a ``force`` argument to eliminate the
  prompts. See :issue:`36`.
- Multi-threaded runs handle exceptions and signals more reliably.
  Partial fix for :issue:`26`.
- Shared thread read tests clear the caches of connections and the
  database in a more controlled way, more closely modeling the
  expected behaviour. Previously the cache clearing was
  non-deterministic. See :issue:`28`.
- When using gevent, use its Event and Queue implementations for
  better cooperation with the event loop.
- Add ``--min-objects`` option to ensure that the underlying database
  has at least a set number of objects in place. This lets us test
  scaling issues and be more repeatable. This is tested with
  FileStorage, ZEO, and RelStorage (RelStorage 2.1a2 or later is
  needed for accurate results; earlier versions will add new objects
  each time, resulting in database growth).
- Remove the unmaintained buildout configuration. See :issue:`25`.
- Add an option to test the performance of blob storage. See
  :issue:`29`.
- Add support for zapping file storages. See :issue:`43`.
- When zapping, do so right before running the 'add' benchmark. This
  ensures that the databases are all the same size even when the same
  underlying storage (e.g., MySQL databas) is used multiple times in a
  configuration. Previously, the second and further uses of the same
  storage would not be zapped and so would grow with the data from the
  previous contender tests. See :issue:`42`.
- Add a benchmark for empty transaction commits. This tests the
  storage synchronization --- in RelStorage, it tests polling the
  RDBMS for invalidations. See :issue:`41`.
- Add support for using `vmprof <https://vmprof.readthedocs.io>`_ to
  profile, instead of :mod:`cProfile`. See :issue:`34`.

0.6.0 (2016-12-13)
==================

This is a major release that focuses on providing more options to fine
tune the testing process that are expected to be useful to both
deployers and storage authors.

A second major focus has been on producing more stable numeric
results. As such, the results from this version *are not directly
comparable* to results obtained from a previous version.

Platforms
---------

- Add support for Python 3 (3.4, 3.5 and 3.6) and PyPy. Remove support
  for Python 2.6 and below.
- ZODB 4 and above are the officially supported versions. ZODB 3 is no
  longer tested but may still work.

Incompatible Changes
--------------------

- Remove support for Python 2.6 and below.
- The old way of specifying concurrency levels with a comma separated
  list is no longer supported.

Command Line Tool
-----------------

The help output and command parsing has been much improved.

- To specify multiple concurrency levels, specify the ``-c`` option
  multiple times. Similarly, to specify multiple object counts,
  specify the ``-n`` option multiple times. (For example, ``-c 1 -c 2 -n 100
  -n 200`` would run four comparisons). The old way of separating numbers with
  commas is no longer supported.
- Add the ``--log`` option to enable process logging. This is useful
  when using zodbshootout to understand changes in a single storage.
- Add ``--zap`` to rebuild RelStorage schemas on startup. Useful when
  switching between Python 2 and Python 3.
- The reported numbers should be more stable, thanks to running
  individual tests more times (via the ``--test-reps`` option) and
  taking the mean instead of the min.
- Add ``--dump-json`` to write a JSON representation of more detailed
  data than is present in the default CSV results.


Test Additions
--------------

- Add support for testing with BTrees (``--btrees``). This is
  especially helpful for comparing CPython and PyPy, and is also
  useful for understanding BTree behaviour.
- Add support for testing using threads instead of multiprocessing
  (``--threads``). This is especially helpful on PyPy or when testing
  concurrency of a RelStorage database driver and/or gevent. Databases
  may be shared or unique for each thread.
- Add support for setting the repetition count (``--test-reps``). This
  is especially helpful on PyPy.
- Use randomized data for the objects instead of a constant string.
  This lets us more accurately model effects due to compression at the
  storage or network layers.
- When gevent is installed, add support for testing with the system
  monkey patched (``--gevent``). (Note: This might not be supported by all storages.)
- Add ``--leaks`` to use `objgraph <http://mg.pov.lt/objgraph/>`_ to
  show any leaking objects at the end of each test repetition. Most
  useful to storage and ZODB developers.

Other
-----

- Enable continuous integration testing on Travis-CI and coveralls.io.
- Properly clear ZEO caches on ZODB5. Thanks to Jim Fulton.
- Improve installation with pip. Extras are provided to make testing
  RelStorage as easy as testing FileStorage and ZEO.
- The documentation is now hosted at http://zodbshootout.readthedocs.io/

0.5 (2012-09-08)
================

- Updated to MySQL 5.1.65, PostgreSQL 9.1.5, memcached 1.4.15,
  and libmemcached 1.0.10.

- Moved development to github.

0.4 (2011-02-01)
================

- Added the --object-size parameter.

0.3 (2010-06-19)
================

- Updated to memcached 1.4.5, libmemcached 0.40, and pylibmc 1.1+.

- Updated to PostgreSQL 8.4.4.

- Updated to MySQL 5.1.47 and a new download url - the old was giving 401's.

0.2 (2009-11-17)
================

- Buildout now depends on a released version of RelStorage.

0.1 (2009-11-17)
================

- Initial release.
