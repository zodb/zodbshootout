# -*- coding: utf-8 -*-
"""
Helpers for profiling.

"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function


logger = __import__('logging').getLogger(__name__)

import os.path
from cProfile import Profile as CProfile
from pstats import Stats as CStats

from zope.interface import implementer

from .interfaces import IDBBenchmark

# The cProfile profiler needs to be enabled in each native thread
# that we want to profile. A single profile object can be enabled
# and disabled multiple times to continue accumulating stats.

# vmprof can only be enabled once, globally. This is fine for
# multi-process concurrency. For single process concurrency, the
# behaviour depends on whether you specify `real_time=True` when
# enabling it, and the behaviour of signals on the platform. When
# `real_time` is false, then only the thread that got the signal is
# profiled; if the signal is always sent to the main thread, then
# that's not helpful but if the signal is sent to the active thread
# (Linux always sends it to the active thread) then all threads will
# be sampled.
#
# If `real_time` is true, then (at least on linux) only the main
# thread gets the signal, but vmprof forwards it to all threads, so
# everything is sampled.

class AbstractProfiler(object):

    def __init__(self, profile_dir, func_name):
        self.profile_dir = profile_dir
        self.func_name = func_name
        self.db_name = ''

    def __enter__(self):
        self._do_enter()

    def __exit__(self, t, v, tb):
        self._do_exit()

    def generate_file_names(self):
        import threading
        tid = threading.current_thread().ident
        db_name = '_' + self.db_name if self.db_name else ''
        basename = "%s_%s_%s%s" % (self.func_name, os.getpid(), tid, db_name)

        txt_fn = os.path.join(self.profile_dir, basename + ".txt")
        prof_fn = os.path.join(self.profile_dir, basename + ".prof")
        return txt_fn, prof_fn

    def _do_enter(self):
        raise NotImplementedError

    def _do_exit(self):
        raise NotImplementedError

class CProfiler(AbstractProfiler):

    def __init__(self, *args):
        AbstractProfiler.__init__(self, *args)
        self.profiler = CProfile()

    def _do_enter(self):
        self.profiler.enable()

    def _do_exit(self):
        self.profiler.disable()
        txt_fn, prof_fn = self.generate_file_names()

        self.profiler.dump_stats(prof_fn)

        with open(txt_fn, 'w') as f:
            st = CStats(self.profiler, stream=f)
            st.strip_dirs()
            st.sort_stats('cumulative')
            st.print_stats()


class VMProfiler(AbstractProfiler):
    stat_file = None

    def _do_enter(self):
        import vmprof
        _, prof_fn = self.generate_file_names()
        self.stat_file = open(prof_fn, 'a+b')
        vmprof.enable(self.stat_file.fileno(), lines=True)

    def _do_exit(self):
        import vmprof
        vmprof.disable()
        self.stat_file.flush()
        self.stat_file.close()


class ProfiledFunctionFactory(object):
    def __init__(self, profile_dir, inner_kind, profile_kind=CProfiler):
        self.profile_dir = profile_dir
        self.inner_kind = inner_kind
        self.profile_kind = profile_kind

    def __call__(self, func_name):
        return ProfiledFunction(self.profile_dir,
                                self.profile_kind,
                                self.inner_kind,
                                func_name)

@implementer(IDBBenchmark)
class ProfiledFunction(object):
    """
    A function wrapper that installs a profiler around the execution
    of the (distributed) functions.

    This is only done in the current thread. This works fine for
    gevent, where real threads are not actually in use. Here, we want
    to wrap it *around* the distributed function.

    For true threading, we want to wrap the distribution around
    *this* object.
    """

    def __init__(self, profile_dir, profile_kind, inner_kind, func_name):
        self.profile_dir = profile_dir
        self.inner = inner_kind(func_name)
        self.profiler = profile_kind(profile_dir, func_name)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __call__(self, loops, db_factory):
        # We're trying to capture profiling from all the warmup runs, etc,
        # since that all takes much longer.
        self.profiler.db_name = db_factory.name
        with self.profiler:
            return self.inner(loops, db_factory)
