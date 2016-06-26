=====================================================
 Creating a Complete Test Environment Using Buildout
=====================================================

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
