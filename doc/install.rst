==============
 Installation
==============

``zodbshootout`` can be installed using ``pip``:

  pip install zodbshootout

This will install the :doc:`zodbshootout <zodbshootout>` script along
with ZODB and ZEO. To test other storages (such as ``RelStorage``) or
storage wrappers (such as ``zc.zlibstorage``) you'll need to install
those packages as well.

RelStorage
==========

``zodbshootout`` comes with extras that install RelStorage plus an
appropriate database adapter/driver for a specific database::

  pip install "zodbshootout[mysql]"
  pip install "zodbshootout[postgresql]"
  pip install "zodbshootout[oracle]"

.. note:: This does not actually install the databases. You will need
		  to install those separately (possibly using your operating
		  system's package manager) and create user accounts as
		  described `in the RelStorage documentation
		  <http://relstorage.readthedocs.io/en/latest/configure-database.html>`_.

.. tip:: This does not install the packages necessary for RelStorage
		 to integrate with Memcache. See the RelStorage documentation
		 `for more information
		 <http://relstorage.readthedocs.io/en/latest/install.html#memcache-integration>`_
		 on the packages needed to test RelStorage and Memcache.

ZEO
===

When ``zodbshootout`` is installed, ZEO is also installed. To test
ZEO's performance, you'll need to have the ZEO process running, as
described `in the ZEO documentation <https://pypi.python.org/pypi/ZEO/5.1.0#running-the-server>`_.
