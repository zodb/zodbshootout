=====================================================
 Creating a Complete Test Environment Using Buildout
=====================================================

.. note:: Using buildout will download and compile native programs
          including databases and memcached. Most users should use
          their operating system's package manager to install the
          required databases and services and pip to install
          zodbshootout and the python dependencies.

.. caution:: The buildout configuration distributed with zodbshootout
             should be considered a sample. It has undergone limited
             testing, and is only supported on Python 2 and OS X and
             certain flavors of Linux. Other Unix platforms will have
             to tweak it.

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

Install Buildout in that environment.  (This command will create a script
named ``bin/buildout``.)::

    $ bin/pip install zc.buildout

Run Buildout. Buildout will follow the instructions specified by
``buildout.cfg`` to download, compile, and initialize versions of
MySQL and PostgreSQL (on OS X and 64-bit linux, pre-built binaries
will be downloaded). It will also install several other Python
packages. This may take a half hour or more the first time::

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

    $ bin/zodbshootout samples/sample.conf

The ``sample.conf`` test compares the performance of RelStorage with
MySQL and PostgreSQL, along with FileStorage behind ZEO, where the
client and server are located on the same computer.

See also ``remote-sample.conf``, which tests database speed over a
network link. Set up ``remote-sample.conf`` by building
``zodbshootout`` on two networked computers, then point the client at
the server by changing the ``%define host`` line at the top of
``remote-sample.conf``. The ``sample`` directory contains other sample
database configurations.
