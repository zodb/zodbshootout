# -*- coding: utf-8 -*-
"""
Helpers for wrapping speedtest objects.

"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function


from zope.interface import implementer

from .interfaces import IDBBenchmarkCollectionWrapper

class AbstractWrapper(object):
    __wrapped__ = None

    def __repr__(self):
        return "<%s at %x around %r>" % (type(self).__name__, id(self), self.__wrapped__)

@implementer(IDBBenchmarkCollectionWrapper)
class AbstractBenchmarkFunctionWrapper(object):
    """
    Applies a wrapper to each benchmark function.
    """
    delegate = None

    @property
    def __wrapped__(self):
        return self.delegate

    def __getattr__(self, name):
        if name.startswith("bench_"):
            return self.make_function_wrapper(name)
        return getattr(self.delegate, name)

    def make_function_wrapper(self, func_name):
        raise NotImplementedError

class BenchmarkCollectionWrapper(AbstractBenchmarkFunctionWrapper):

    def __init__(self, worker):
        self.delegate = worker

    def __setattr__(self, name, value):
        if name in ('delegate', 'make_function_wrapper'):
            object.__setattr__(self, name, value)
            return

        # Everything else delegates to the worker.
        # this is important for worker.sync
        setattr(self.delegate, name, value)

    def make_function_wrapper(self, func_name):
        # We just return the function.
        # Instances may have this set directly, so save storage space for it.
        return getattr(self.delegate, func_name)
