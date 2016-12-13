==========================
 Running ``zodbshootout``
==========================

The ``zodbshootout`` script accepts the name of a database
configuration file. The configuration file contains a list of
databases to test, in ZConfig format. The script packs each of the
databases, then writes and reads the databases while taking
measurements. Finally, the script produces a tabular summary of
objects written or read per second in each configuration.
``zodbshootout`` uses the names of the databases defined in the
configuration file as the table column names.

.. caution:: ``zodbshootout`` packs each of the databases specified in
   the configuration file. This results in the permanent deletion of
   historical revisions, and if the database is a part of a
   multi-database (mount points) could result in
   :exc:`~ZODB.POSException.POSKeyError` and broken links. Do not
   configure it to open production databases!

The ``zodbshootout`` script accepts the following options. A
description of each option follows the text output.

.. command-output:: zodbshootout --help

Objects
-------

These options control the objects put in the database.

* ``-n`` (``--object-counts``) specifies how many persistent objects to
  write or read per transaction. The default is 1000. An interesting
  value to use is 1, causing the test to primarily measure the speed of
  opening connections and committing transactions.

  .. versionchanged:: 0.6
                      Specify this option more than once to run the
                      tests with different object counts.


* ``--btrees`` causes the data to be stored in the BTrees optimized
  for ZODB usage (without this option, a PersistentMapping will be
  used). This is an advanced option that may be useful when tuning
  particular applications and usage scenarios. This adds additional
  objects to manage the buckets that make up the BTree. However, if
  IO BTrees are used (the default when this option is specified)
  internal storage of keys as integers may reduce pickle times and
  sizes (and thus improve cache efficiency). This option can take an
  argument of either IO or OO to specify the type of BTree to use.

  This option is especially interesting on PyPy or when comparing the
  pure-Python implementation of BTrees to the C implementation.

  .. versionadded:: 0.6

* ``--zap`` recreates the tables and indexes for a RelStorage
  database. *This option completely destroys any existing data.* You
  will be prompted to confirm that you want to do this for each
  database that supports it. This is handy for comparing Python 2 and
  Python 3 (which can't otherwise use the same database schemas).

  .. caution:: This option destroys all data in the relevant database.

  .. versionadded:: 0.6

Concurrency
-----------

These options control the concurrency of the testing.

* ``-c`` (``--concurrency``) specifies how many tests to run in
  parallel. The default is 2. Each of the concurrent tests runs in a
  separate process to prevent contention over the CPython global
  interpreter lock. In single-host configurations, the performance
  measurements should increase with the concurrency level, up to the
  number of CPU cores in the computer. In more complex configurations,
  performance will be limited by other factors such as network latency.

  .. versionchanged:: 0.6
                      Specify this option more than once to run the
                      tests with different concurrency levels.

* ``--threads`` uses in-process threads for concurrency instead of
  multiprocessing. This can demonstrate how the GIL affects various
  database adapters under RelStorage, for instance. It can also have
  demonstrate the difference that warmup time makes for things like
  PyPy's JIT.

  By default or if you give the ``shared`` argument to this option,
  all threads will share one ZODB DB object and re-use Connections
  from the same pool; most threaded applications will use ZODB in this
  manner. If you specify the ``unique`` argument, then each thread
  will get its own DB object. In addition to showing how the thread
  locking strategy of the underlying storage affects things, this can
  also highlight the impact of shared caches.

  .. versionadded:: 0.6

* ``--gevent`` monkey-patches the system and uses cooperative greenlet
  concurrency in a single process (like ``--threads``, which it
  implies; you can specify ``--threads unique`` to change the database
  sharing).

  This option is only available if gevent is installed.

  .. note:: Not all storage types will work properly with this option.
            RelStorage will, but make sure you select a
            gevent-compatible driver like PyMySQL or pg8000 for best
            results.

  .. versionadded:: 0.6

Repetitions
-----------

These options control how many times tests are repeated.

* ``-r`` (``--repetitions``) determines how many iterations of the
  complete test suite will be compared together to find the best time. Higher
  values can reduce jitter. Higher values are especially useful on
  platforms that have a warmup period (like PyPy's JIT). The default
  is 3.

  .. versionadded:: 0.6

* ``--test-reps`` determines how many times each individual test (such
  as add/update/cold/warm) will be repeated.

  .. versionadded:: 0.6


Profiling
---------

* ``-p`` (``--profile``) enables the Python profiler while running the
  tests and outputs a profile for each test in the specified directory.
  Note that the profiler typically reduces the database speed by a lot.
  This option is intended to help developers isolate performance
  bottlenecks.

  .. versionadded:: 0.6


* ``--leaks`` prints a summary of possibly leaking objects after each
  test repetition. This is useful for storage and ZODB developers.

  .. versionadded:: 0.6

Output
------

These options control the output produced.

* ``--log`` enables logging to the console at the specified level. If
  no level is specified but this option is given, then INFO logging
  will be enabled. This is useful for details about the workings of a
  storage and the effects various options have on it.

  .. versionadded:: 0.6


* ``--dump-json`` writes a JSON structure containing the raw data
  collected to the file given (or if no file is given, to stdout).
  This can be useful for doing a more sophisticated analysis.

  .. note:: The JSON structure is subject to change at any time.

  .. versionadded:: 0.6


You should write a configuration file that models your intended
database and network configuration. Running ``zodbshootout`` may reveal
configuration optimizations that would significantly increase your
application's performance.
