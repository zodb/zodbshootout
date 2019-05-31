# -*- coding: utf-8 -*-
"""
Helpers for profiling.

"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function


logger = __import__('logging').getLogger(__name__)

import os.path
import shutil
from cProfile import Profile as CProfile
from pstats import Stats as CStats

from zope.interface import implementer

from .interfaces import IDBBenchmark
from ._wrapper import AbstractWrapper

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

    PROF_DATA_EXT = '.prof'

    def __init__(self, profile_dir, func_name):
        self.profile_dir = profile_dir
        self.func_name = func_name
        self.db_name = 'unknown'

    def __enter__(self):
        self._do_enter()

    def __exit__(self, t, v, tb):
        self._do_exit()

    def get_thread_id_for_filename(self):
        import threading
        return threading.current_thread().ident

    def generate_file_names(self):
        tid = self.get_thread_id_for_filename()
        db_name = self.db_name
        func = self.func_name[len('bench_'):]

        # <path>/db/add/random
        db_dir = os.path.join(self.profile_dir, db_name)
        bench_dir = os.path.join(db_dir, func)

        try:
            os.makedirs(bench_dir)
        except OSError:
            pass

        basename = "%s_%s" % (os.getpid(), tid)

        txt_fn = os.path.join(bench_dir, basename + ".txt")
        prof_fn = os.path.join(bench_dir, basename + self.PROF_DATA_EXT)
        return txt_fn, prof_fn

    def _do_enter(self):
        raise NotImplementedError

    def _do_exit(self):
        raise NotImplementedError

    def combine(self):
        pass

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

    def combine(self):
        # Rewrite to combine things with a common prefix into a single
        # file.
        def dirs_only(p):
            db_dirs = os.listdir(p)
            db_dirs = [os.path.join(p, d) for d in db_dirs]
            db_dirs = [d for d in db_dirs if os.path.isdir(d)]
            return db_dirs

        db_dirs = dirs_only(self.profile_dir)
        for db_dir in db_dirs:
            bench_dirs = dirs_only(db_dir)
            for bench_dir in bench_dirs:
                prof_files = [os.path.join(bench_dir, p)
                              for p in os.listdir(bench_dir)
                              if p.endswith('.prof')]

                bench_name = os.path.basename(bench_dir)
                db_name = os.path.basename(db_dir)
                base_prof_name = db_name + '_' + bench_name

                stats = CStats(*prof_files)
                stats.dump_stats(os.path.join(
                    self.profile_dir,
                    base_prof_name + '.prof'
                ))

                txt_fn = os.path.join(
                    self.profile_dir,
                    base_prof_name + '.txt'
                )
                with open(txt_fn, 'w') as f:
                    st = CStats(*prof_files, stream=f)
                    st.strip_dirs()
                    st.sort_stats('cumulative')
                    st.print_stats()
            shutil.rmtree(db_dir)


class VMProfiler(AbstractProfiler):
    PROF_DATA_EXT = ".vmprof"
    stat_file = None

    counter = 0
    counter_lock = None

    def __init__(self, profile_dir, func_name):
        super(VMProfiler, self).__init__(profile_dir, func_name)

        if self.counter_lock is None:
            from threading import Lock
            VMProfiler.counter_lock = Lock()

    def get_thread_id_for_filename(self):
        # It's process wide, it doesn't matter.
        return 0

    def _do_enter(self):
        with self.counter_lock:
            VMProfiler.counter += 1
            if VMProfiler.counter > 1:
                # Already enabled. Skip.
                return

            import vmprof
            _, prof_fn = self.generate_file_names()
            VMProfiler.stat_file = open(prof_fn, 'a+b')
            # real_time can break time.sleep() and it seems to hang PyPy.
            vmprof.enable(VMProfiler.stat_file.fileno(), lines=True, real_time=False)

    def _do_exit(self):
        with self.counter_lock:
            VMProfiler.counter -= 1
            if VMProfiler.counter != 0:
                # Still others out there. Skip
                return

            import vmprof
            vmprof.disable()
            VMProfiler.stat_file.flush()
            VMProfiler.stat_file.close()
            VMProfiler.stat_file = None


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
class ProfiledFunction(AbstractWrapper):
    """
    A function wrapper that installs a profiler around the execution
    of the (distributed) functions.

    For cProfile, this is only done in the current thread. This works
    fine for gevent, where real threads are not actually in use. Here,
    we want to wrap it *around* the distributed function.

    For true threading, we want to wrap the distribution around *this*
    object.
    """

    def __init__(self, profile_dir, profile_kind, inner_kind, func_name):
        self.profile_dir = profile_dir
        self.__wrapped__ = inner_kind(func_name)
        self.profiler = profile_kind(profile_dir, func_name)

    def __getattr__(self, name):
        return getattr(self.__wrapped__, name)

    def __call__(self, loops, db_factory):
        # We're trying to capture profiling from all the warmup runs, etc,
        # since that all takes much longer.
        self.profiler.db_name = db_factory.name
        with self.profiler:
            return self.__wrapped__(loops, db_factory)
