=========
 Changes
=========

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
