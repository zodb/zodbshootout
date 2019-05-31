# -*- coding: utf-8 -*-
"""
Interfaces used in zodbshootout.

These are mostly for documentation on how the internals work.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from zope.interface import Interface
from zope.interface import Attribute

from ZODB.interfaces import IDatabase

# pylint:disable=inherit-non-class,no-method-argument,signature-differs
# pylint:disable=no-self-argument

class IBenchmarkDatabase(IDatabase):
    """
    An extension of the ZODB database to include methods
    that are useful for benchmarking.
    """

    def speedtest_log_cache_stats(msg=''):
        """
        Log statistics about storage cache usage in the database.

        :param str msg: Extra information added to the log.
        """

    def speedtest_zap_all():
        """
        Destroy all the data in the database (if the factory was
        configured to let that happen).

        If there's no way to do this, or the user choose not to, does
        nothing.

        Unlike most other methods, this must be called on a database
        that's closed.
        """

class IDBFactory(Interface):
    """
    A callable object that returns a
    :class:`ZODB.interfaces.IDatabase`.

    The database may be unique (fresh for each call) or it may be
    shared (the same for each call).

    You must close the database when you are done using it.
    """

    name = Attribute("The name of the database. Must be unique across factories.")

    def __call__():
        """Return the database to use."""

class IBenchmarkDBFactory(IDBFactory):
    """
    Returns `IBenchmarkDatabase` when called.
    """

class IDBBenchmark(Interface):
    """
    A callable object that benchmarks some aspect of a database.
    """

    def __call__(loops, db_factory):
        """
        Perform benchmarks on the database.

        This will be called many times: to calibrate the number of
        loops needed, to perform warmups, and then to actually
        measure.

        :param int loops: How many iterations of the benchmark to
            perform. This number may vary on each invocation of this
            object.

        :return: The number of seconds it took to perform *loops*
                 iterations of the benchmark, as measured using
                 `pyperf.perf_counter`.
        """


class IDBBenchmarkCollection(Interface):
    """
    All the attributes of this object that begin with ``bench_``
    are `IDBBenchmark` functions.
    """

class IDBBenchmarkCollectionWrapper(IDBBenchmarkCollection):
    """
    This object applies a wrapper function to each benchmark function
    when it is accessed.

    The wrapper function may be changed by assigning to ``make_function_wrapper``.
    """

    def make_function_wrapper(func_name):
        """
        Return an `IDBBenchmark` for the method of this object *func_name*.
        """
