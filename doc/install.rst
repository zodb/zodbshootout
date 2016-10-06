==============
 Installation
==============

There are two supported ways to install and use ``zodbshootout``.
Use ``pip`` if you already know what configurations you want to test
and how to set them up. Use :doc:`buildout <install-buildout>` to
create a complete testing environment.

pip
===

``zodbshootout`` can be installed using ``pip``:

  pip install zodbshootout

This will install the :doc:`zodbshootout <zodbshootout>` command along with ZODB and
ZEO. To test other storages (such as ``RelStorage``) or storage
wrappers (such as ``zc.zlibstorage``) you'll need to install those
packages as well.

RelStorage
----------

``zodbshootout`` comes with extras that install RelStorage plus an
appropriate database adapter/driver for a specific database::

  pip install "zodbshootout[mysql]"
  pip install "zodbshootout[postgresql]"
  pip install "zodbshootout[oracle]"

.. note:: This does not actually install the databases. You will
		  need to install those separately (possibly using your
		  operating system's package manager) and create user
		  accounts as described in the RelStorage documentation.

.. tip:: This does not install the packages necessary for RelStorage
		 to integrate with Memcache. See the RelStorage documentation
		 for more information on the packages needed to test
		 RelStorage and Memcache.

Buildout
========

``zodbshootout`` contains a ``zc.buildout`` configuration for
creating a complete testing environment. It is described in a
:doc:`separate document <install-buildout>`.

This is an advanced usage and may require adjustment for a particular
system.
