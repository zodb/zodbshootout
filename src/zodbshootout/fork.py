##############################################################################
#
# Copyright (c) 2008 Zope Foundation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
"""
Multiprocessing utilities.
"""

from __future__ import absolute_import, print_function
from multiprocessing import Process as MPProcess
from multiprocessing import Queue as MPQueue

from threading import Thread as MTProcess
from threading import Lock as MTLock
from threading import Event as MTEvent
from six.moves.queue import Queue as MTQueue

gevent_threads = False
try:
    import gevent.monkey
except ImportError:
    pass
else:
    if gevent.monkey.is_module_patched('threading'):
        # pylint:disable=function-redefined
        gevent_threads = True
        from gevent.queue import Queue as MTQueue
        from gevent.event import Event as MTEvent
        class MTLock(object):
            def acquire(self):
                pass
            release = acquire

from six.moves.queue import Empty
import time

import logging
logger = logging.getLogger(__name__)

strategies = {
    'threads': (MTProcess, MTQueue),
    'mp': (MPProcess, MPQueue)
}

# MESSAGE_DELAY contains the maximum expected message delay.  If a message
# takes longer than this to deliver to a child process, synchronized
# execution will fail.  OTOH, this delays test execution, so it should
# be reasonably short.
MESSAGE_DELAY = 0.5


class ChildProcessError(Exception):
    """A child process failed"""

class UnexpectedChildDeathError(ChildProcessError):
    """
    A child process quit unexpectedly.
    """

class ExceptionInChildError(ChildProcessError):
    """
    A child process raised an exception and died.
    """


class _Unwrapper(object):

    def __init__(self, func):
        self.func = func

    def __call__(self, param, sync):
        args, kwargs = param
        return self.func(*args, **kwargs)

    def __repr__(self):
        return repr(self.func)


def run_in_child(func, strategy, *args, **kwargs):
    """
    Call a function in a child process.

    Raises an exception if the child process fails.

    :return: Whatever the function returned.
    """
    return distribute(_Unwrapper(func), [(args, kwargs)], strategy)[0]


class Child(object):

    def __init__(self, child_num, parent_queue, func, param, Process, Queue):
        self.child_num = child_num
        self.parent_queue = parent_queue
        self.func = func
        self.param = param
        self.process = Process(target=self.run)
        if hasattr(self.process, 'name'):
            self.process.name = 'Child %s (%r)' % (self.child_num, self.func)

        self.child_queue = Queue()

    def _execute_func(self):
        return self.func(self.param, self.sync)

    def _log_exception(self):
        logger.exception("Failed to run %s", self.func)

    def start(self):
        if hasattr(self.process, 'daemon'):
            # Make threads not block when the main thread gets an exception
            # and wants to die.
            self.process.daemon = True
        self.process.start()

    def run(self):
        try:
            res = self._execute_func()
        except SystemExit:
            self.parent_queue.put(
                (self.child_num, "system_exit", None))
            raise
        except KeyboardInterrupt:
            self.parent_queue.put(
                (self.child_num, "keyboard_interrupt", None))
            # don't reraise, just noise on stdout
        except BaseException as e:
            self._log_exception()
            self.parent_queue.put((
                self.child_num, 'exception', '%s: %s' % (type(e), str(e))))
        else:
            self.parent_queue.put((self.child_num, 'ok', res))
        finally:
            if hasattr(self.child_queue, 'close'):
                self.child_queue.close()

    def sync(self, name):
        self.parent_queue.put((self.child_num, 'sync', name))
        self.child_queue.get()
        # Previously, we tried to wait to resume until a specific
        # time. The idea being to try to maximize the actual
        # concurrency. But now that the benchmarks are shorter and
        # under the control of pyperf, we just wind up spending lots
        # of time sleeping. And we don't really use sync in the same
        # way anymore (to control concurrency; now we just use it to
        # prevent errors for things like zapping the database and
        # otherwise making sure something only gets done once), so
        # it's fine to let children resume as soon as they get the
        # message. That's what the threaded implementation does.

    def __str__(self):
        return "%s(%s)" % (self.__class__.__name__,
                           getattr(self.process, 'name', self.child_num))


class SynclessChild(Child):

    def sync(self, name):
        pass


class ThreadedChild(Child):
    """
    A child object that uses fast thread synchronization.
    """

    event_lock = None
    events = None
    child_count = None

    def herd_init(self, event_lock, events, child_count):
        self.event_lock = event_lock
        self.events = events
        self.child_count = child_count

    def sync(self, name):
        # In gevent, we don't actually take a lock here. So this
        # method MUST NOT do anything that could cause a greenlet switch
        # until we're ready.
        self.event_lock.acquire()
        event, count = self.events.get(name, (None, None))
        if event is None:
            # I'm the first one here!
            event = MTEvent()
            count = 0

        count += 1
        self.events[name] = (event, count)
        assert len(self.events) == 1, (name, self.events)
        if count < self.child_count:
            self.event_lock.release()
            event.wait()
        else:
            # I'm the last one here! Wake everybody else up.
            del self.events[name]
            assert not self.events, (name, self.events)
            event.set()
            self.event_lock.release()
            # I'm going to keep going by returning from this function.
            # Next time I sleep others will wake up.


def _poll_children(parent_queue, children, before_poll=lambda: None):

    try:
        for child in children.values():
            child.start()

        results = []
        # Map from a string naming a sync point to the
        # set of children (numbers) that are waiting there.
        # This should only ever have at most one name in it; if there are
        # more, it means we're trying to nest sync points.
        sync_waiting = {}

        before_poll()

        while children:

            try:
                child_num, msg, arg = parent_queue.get(timeout=10)
            except Empty:
                # If we're running with gevent patches, but the driver isn't
                # cooperative, we may have timed out before the switch. But there may be
                # something now in the queue. So try to get it, but don't block.
                time.sleep(0.001) # switch greenlets if need be.
                try:
                    child_num, msg, arg = parent_queue.get_nowait()
                except Empty:
                    # While we're waiting, see if any children have died.
                    for child in children.values():
                        if not child.process.is_alive():
                            raise UnexpectedChildDeathError(
                                "process %r running %r failed with exit code %d" % (
                                    child.process,
                                    child.func, getattr(child.process, 'exitcode', -1)))
                    continue

            if msg == 'ok':
                results.append(arg)
                child = children[child_num]
                child.process.join()
                del children[child_num]
            elif msg == 'exception':
                raise ExceptionInChildError(arg)
            elif msg == 'keyboard_interrupt':
                raise KeyboardInterrupt()
            elif msg == 'system_exit':
                raise UnexpectedChildDeathError()
            elif msg == 'sync':
                if arg not in sync_waiting:
                    sync_waiting[arg] = set()
                sync_waiting[arg].add(child_num)

                if len(sync_waiting) != 1:
                    raise AssertionError("Children at different sync points!")
            else:
                raise AssertionError("unknown message: %s" % msg)

            if msg == 'sync' and len(sync_waiting[arg]) == len(children):
                # All children have called sync(), so tell them
                # to resume shortly and set up for another sync.
                del sync_waiting[arg] # = set(children)
                assert not sync_waiting
                resume_time = time.time() + MESSAGE_DELAY
                for child in children.values():
                    child.child_queue.put(resume_time)

        return results
    finally:
        if hasattr(parent_queue, 'close'):
            parent_queue.close()
        if hasattr(parent_queue, 'cancel_join_thread'):
            parent_queue.cancel_join_thread()

        for child in children.values():
            # multiprocess children can be forcibly killed,
            # threads cannot. (greenlets could)
            if hasattr(child.process, 'terminate'):
                child.process.terminate()
            child.process.join(1)

def distribute(func, param_iter, strategy='mp',
               before_poll=lambda: None):
    """
    Call a function in separate processes concurrently.

    *param_iter* is an iterable that provides the first parameter for
    each function call. A child process will execute *func* for every
    item returned from *param_iter*.

    The second parameter for each call is a "sync" function. The sync
    function pauses execution, then resumes all processes at
    approximately the same time. It is **required** that all child
    processes call the sync function the same number of times.

    The results of calling the function are appended to a list, which
    is returned once all functions have returned.  If any function
    raises an error, this raises :exc:`ChildProcessError`.

    :keyword str strategy: How to distribute the work, either in
        multiple processes ('mp', the default) or in multiple threads
        in the same process ('threads').
    """

    Process = strategies[strategy][0]
    Queue = strategies[strategy][1]

    children = {}
    parent_queue = Queue()

    events = {}
    lock = MTLock()

    child_factory = Child
    if strategy == 'threads':
        child_factory = ThreadedChild

    for child_num, param in enumerate(param_iter):
        child = child_factory(child_num, parent_queue, func, param, Process, Queue)
        children[child_num] = child

    if child_factory is ThreadedChild:
        for child in children.values():
            child.herd_init(lock, events, len(children))

    return _poll_children(parent_queue, children, before_poll)
