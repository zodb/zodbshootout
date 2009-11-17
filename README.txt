zodbshootout
------------

This application measures and compares the performance of various
ZODB storages and configurations. It is derived from the RelStorage
speedtest script, but this version allows more types of storages and
configurations, provides more measurements, and produces numbers that
are easier to interpret.

Although you can ``easy_install`` this package, the best way to get
started is to follow the directions below to set up a complete testing
environment with sample tests.

How to set up ``zodbshootout`` using Buildout
---------------------------------------------

First, be sure you have certain packages installed so you can compile
software. Ubuntu and Debian users should do this (tested with Ubuntu
8.04, Ubuntu 9.10, Debian Etch, and Debian Lenny)::

    $ sudo apt-get install build-essential python-dev
    $ sudo apt-get install ncurses-dev libevent-dev libreadline-dev zlib1g-dev

Download the ``zodbshootout`` tar file.  Unpack it and change to its
top level directory::

    $ tar xvzf zodbshootout-*.tar.gz
    $ cd zodbshootout-*

Set up that same directory as a partly isolated Python environment
using ``virtualenv``::

    $ virtualenv --no-site-packages .

Install Buildout in that environment, which will create a script
named ``bin/buildout``::

    $ bin/easy_install zc.buildout

Make sure you have adequate space in your temporary directory (normally
``/tmp``) to compile MySQL and PostgreSQL. You may want to switch to a
different temporary directory by setting the TMPDIR environment
variable::

    $ TMPDIR=/var/tmp
    $ export TMPDIR

Run Buildout. Buildout will follow the instructions specified by
``buildout.cfg`` to download, compile, and initialize versions of MySQL
and PostgreSQL. It will also install several other Python packages.
This may take a half hour or more the first time::

    $ bin/buildout

If that command fails, first check for missing dependencies. The
dependencies are listed above. To retry, just run ``bin/buildout``
again.

Once Buildout completes successfully, start the test environment
using Supervisord::

    $ bin/supervisord

Confirm that Supervisor started all processes::

    $ bin/supervisorctl status

If all processes are running, the test environment is now ready.  Run
a sample test::

    $ bin/zodbshootout etc/sample.conf

The ``sample.conf`` test compares the performance of RelStorage with
MySQL and PostgreSQL, along with FileStorage behind ZEO, where the
client and server are located on the same computer.

See also ``remote-sample.conf``, which tests database speed over a
network link. Set up ``remote-sample.conf`` by building
``zodbshootout`` on two networked computers, then point the client at
the server by changing the ``%define host`` line at the top of
``remote-sample.conf``. The ``etc`` directory contains other sample
database configurations.

Running ``zodbshootout``
------------------------

The ``zodbshootout`` script accepts the name of a database
configuration file. The configuration file contains a list of databases
to test, in ZConfig format. The script deletes all data from each of
the databases, then writes and reads the databases while taking
measurements. Finally, the script produces a tabular summary of
objects written or read per second in each configuration.

**Repeated Warning**: ``zodbshootout`` deletes all data from all
databases specified in the configuration file. Do not configure it to
open production databases!

The ``zodbshootout`` script accepts the following options.

* ``-n`` (``--object-counts``) specifies how many persistent objects to
  write or read per transaction. The default is 1000. An interesting
  value to use is 1, causing the test to primarily measure the speed of
  opening connections and committing transactions.

* ``-c`` (``--concurrency``) specifies how many tests to run in
  parallel. The default is 2. Each of the concurrent tests runs in a
  separate process to prevent contention over the CPython global
  interpreter lock. In single-host configurations, the performance
  measurements should increase with the concurrency level, up to the
  number of CPU cores in the computer. In more complex configurations,
  performance will be limited by other factors such as network latency.

* ``-p`` (``--profile``) enables the Python profiler while running the
  tests and outputs a profile for each test in the specified directory.
  Note that the profiler typically reduces the database speed by a lot.
  This option is intended to help developers discover performance
  bottlenecks.

You should write a configuration file that models your intended
database and network configuration. Running ``zodbshootout`` may reveal
configuration optimizations that would significantly increase your
application's performance.

Interpreting the Results
------------------------

The table below shows typical output of running ``zodbshootout`` with
``etc/sample.conf`` on a dual core, 2.1 GHz laptop::

    "Transaction",                postgresql, mysql,   mysql_mc, zeo_fs
    "Write 1000 Objects",               6346,    9441,     8229,    4965
    "Read 1000 Warm Objects",           5091,    6224,    21603,    1950
    "Read 1000 Cold Objects",           5030,   10867,     5224,    1932
    "Read 1000 Hot Objects",           36440,   38322,    38197,   38166
    "Read 1000 Steamin' Objects",    4773034, 3909861,  3490163, 4254936

``zodbshootout`` runs five kinds of tests for each database. For each
test, ``zodbshootout`` instructs all processes to perform similar
transactions concurrently, computes the average duration of the
concurrent transactions, takes the fastest timing of three test runs,
and derives how many objects per second the database is capable of
writing or reading under the given conditions.

* Write objects

    ``zodbshootout`` begins a transaction, adds the specified number of
    persistent objects to a ``PersistentMapping``, and commits the
    transaction. In the sample output above, MySQL was able to write
    9441 objects per second to the database, almost twice as fast as
    ZEO. With memcached support enabled, write performance took a small
    hit due to the time spent storing objects in memcached.

* Read warm objects

    In a different process, without clearing any caches,
    ``zodbshootout`` reads all of the objects just written. This test
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
    the write test. This test favors databases that read objects
    quickly, independently of caching. In the sample output above,
    MySQL cheats a little because it uses a query cache.

* Read hot objects

    In the same process as was used for reading cold objects,
    ``zodbshootout`` clears only the in-memory ZODB caches (the pickle
    cache) then reads all of the objects written by the write test.
    This test favors databases that have a process-specific cache. In
    the sample output above, all of the databases have that type of
    cache.

* Read steamin' objects

    In the same process as was used for reading hot objects,
    ``zodbshootout`` once again reads all of the objects written by the
    write test. This test favors databases that take advantage of the
    ZODB pickle cache. As can be seen from the sample output above,
    accessing an object from the ZODB pickle cache is much faster than
    any operation that requires network access or unpickling.

Known Issues
------------

This application seems to freeze with Python versions before 2.6, most
likely due to some issue connected with the backported version of the
``multiprocessing`` module. Assistance in finding a resolution would be
greatly appreciated.
