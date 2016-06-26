=========
 Changes
=========

0.6 (Unreleased)
================

- Add support for Python 3 and PyPy.
- To specify multiple concurrency levels, specify the ``-c`` option
  multiple times. Similarly, to specify multiple object counts,
  specify the ``-n`` option multiple times. (For example, ``-c 1 -c 2 -n 100
  -n 200`` would run four comparisons). The old way of separating numbers with
  commas is no longer supported.
- Add support for testing with BTrees.
- Add support for testing using threads instead of multiprocessing.
- Add support for setting the repetition count.


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
