==========================
 Running ``zodbshootout``
==========================

Executable
==========

.. highlight:: shell

``zodbshootout`` can be executed in one of two ways. The first and
most common is via the ``zodbshootout`` script created by pip or
buildout::

   # In an active environment with zodbshootout on the path
   $ zodbshootout ...arguments...
   # in a non-active virtual environment
   $ path/to/venv/bin/zodbshootout ...arguments...

``zodbshootout`` can also be directly invoked as a module using the
python interpreter where it is installed:

   python -m zodbshootout

This documentation will simply refer to the ``zodbshootout`` script,
but both forms are equivalent.

.. tip::

   For the most repeatable, stable results, it is important to choose
   a fixed value for the hash seed used for Python's builtin objects
   (str, bytes, etc). On CPython 2, this means not passing the ``-R``
   argument to the interpreter, and not having the `PYTHONHASHSEED
   <https://docs.python.org/2/using/cmdline.html#envvar-PYTHONHASHSEED>`_
   environment variable set to ``random``. On CPython 3, this means
   having the `PYTHONHASHSEED
   <https://docs.python.org/2/using/cmdline.html#envvar-PYTHONHASHSEED>`_
   environment variable set to a fixed value. On a Unix-like system,
   this invocation will work for both versions::

         $ PYTHONHASHSEED=0 zodbshootout ...arguments...

Configuration File
==================


The ``zodbshootout`` script requires the name of a database
configuration file. The configuration file contains a list of
databases to test, in ZConfig format. The script then writes and reads
each of the databases while taking measurements. During this process,
the measured times are output for each test of each database; there
are a number of command-line options to control the output or save it
to files for later analysis. (See the `pyperf user guide
<https://pyperf.readthedocs.io/en/latest/user_guide.html>`_ for
information on configuring the output and adjusting the benchmark
process.)


.. highlight:: xml

An example of a configuration file testing the built-in ZODB file
storage, a few variations of ZEO, and `RelStorage <http://relstorage.readthedocs.io/en/latest/configure-application.html#configuring-repoze-zodbconn>`_
would look like this:

.. literalinclude:: ../samples/fs-sample.conf
   :language: guess

The corresponding ZEO configuration file would look like this:

.. literalinclude:: ../samples/zeo.conf
   :language: guess


.. note::

   If you'll be using RelStorage, you'll need to have the appropriate
   RDBMS processes installed, running, and properly configured. Likewise,
   if you'll be using ZEO, you'll need to have the ZEO server running.
   For pointers to more information, see :doc:`install`.


Options
=======

.. highlight:: guess

The ``zodbshootout`` script accepts the following options. A
description of each option follows the text output.

.. command-output:: zodbshootout --help

.. versionchanged:: 0.7
   You can now specify just a subset of benchmarks to run
   by giving their names as extra command line arguments after the
   configuration file.

Objects
-------

These options control the objects put in the database.

* ``--object-counts`` specifies how many persistent objects to
  write or read per transaction. The default is 1000.

  .. versionchanged:: 0.7
     The old alias of ``-n`` is no longer accepted; pyperf uses that
     to determine the number of loop iterations.

     Also, this can now only be used once.

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

* ``--zap`` recreates the tables and indexes for a RelStorage database
  or a ZODB FileStorage. *This option completely destroys any existing
  data.* You will be prompted to confirm that you want to do this for
  each database that supports it. This is handy for comparing Python 2
  and Python 3 (which can't otherwise use the same database schemas).

  .. caution:: This option destroys all data in the relevant database.

  .. versionchanged:: 0.7
     You can now specify an argument of ``force`` to disable the
     prompt and zap all databases. You can also give a comma separated
     list of database names to zap; only those databases will be
     cleared (without prompting).

  .. versionadded:: 0.6

* ``--min-objects`` ensures that at least the specified number of
  objects exist in the database independently of the objects being
  tested. If the database packs away objects or if ``--zap`` is used,
  this option will add back the necessary number of objects. If there
  are more objects, nothing will be done. This option is helpful for
  testing for scalability issues.

  .. versionadded:: 0.7

* ``--blobs`` causes zodbshootout to read and write blobs instead of
  simple persistent objects. This can be useful for testing options
  like shared blob dirs on network filesystems, or RelStorage's
  blob-chunk-size, or for diagnosing performance problems. If objects
  have to be added to meet the ``--min-objects`` count, they will also
  be blobs. Note that because of the way blobs work, there will be two
  times the number of objects stored as specified in
  ``--object-counts``. Expect this option to cause the test to be much
  slower.

  .. versionadded:: 0.7

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

  .. versionchanged:: 0.7
     This option can only be used once.

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
            results. If your driver is not compatible, you may
            experience timeouts and failures, including
            ``UnexpectedChildDeathError``. zodbshootout attempts to
            compensate for this, but may not always be successful.

  .. versionadded:: 0.6

Repetitions
-----------

These options control how many times tests are repeated.

.. versionchanged:: 0.7

   The old ``-r`` and ``--test-reps`` options were removed. Instead,
   use the ``--loops``, ``--values`` and ``--processes`` options
   provided by pyperf.


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

  .. versionchanged:: 0.7
     The old ``-l`` alias is no longer accepted.

  .. versionadded:: 0.6

Output
------

These options control the output produced.

.. versionchanged:: 0.7

   The ``--dump-json`` argument was removed in favor of pyperf's
   native output format, which enables much better analysis using
   ``pyperf show``.

   If the ``-o`` argument is specified, then in addition to creating a
   single file containing all the test runs, a file will be created
   for each database, allowing for direct comparisons using pyperf's
   ``compare_to`` command.

* ``--log`` enables logging to the console at the specified level. If
  no level is specified but this option is given, then INFO logging
  will be enabled. This is useful for details about the workings of a
  storage and the effects various options have on it.

  .. versionchanged:: 0.8
     This option can also take a path to a ZConfig logging
     configuration file.

  .. versionadded:: 0.6


You should write a configuration file that models your intended
database and network configuration. Running ``zodbshootout`` may reveal
configuration optimizations that would significantly increase your
application's performance.
