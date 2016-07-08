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
"""Multiprocessing utilities.
"""

from __future__ import absolute_import
from multiprocessing import Process as MPProcess
from multiprocessing import Queue as MPQueue

from threading import Thread as MTProcess
from six.moves.queue import Queue as MTQueue

from six.moves.queue import Empty
import time

import logging
logger = logging.getLogger(__name__)

strategies = {
    'threads': (MTProcess, MTQueue),
    'mp': (MPProcess, MPQueue)
}

# message_delay contains the maximum expected message delay.  If a message
# takes longer than this to deliver to a child process, synchronized
# execution will fail.  OTOH, this delays test execution, so it should
# be reasonably short.
message_delay = 0.5


class ChildProcessError(Exception):
    """A child process failed"""


def run_in_child(func, strategy, *args, **kw):
    """Call a function in a child process.  Don't return anything.

    Raises an exception if the child process fails.
    """
    Process = strategies[strategy][0]

    p = Process(target=func, args=args, kwargs=kw)
    p.start()
    p.join()
    if hasattr(p, 'exitcode') and p.exitcode:
        raise ChildProcessError(
            "process running %r failed with exit code %d" % (func, p.exitcode))


class Child(object):

    def __init__(self, child_num, parent_queue, func, param, Process, Queue):
        self.child_num = child_num
        self.parent_queue = parent_queue
        self.func = func
        self.param = param
        self.process = Process(target=self.run)
        self.child_queue = Queue()

    def run(self):
        try:
            res = self.func(self.param, self.sync)
        except (SystemExit, KeyboardInterrupt):
            return
        except Exception as e:
            logger.exception("Failed to run %s", self.func)
            self.parent_queue.put((
                self.child_num, 'exception', '%s: %s' % (type(e), str(e))))
        else:
            self.parent_queue.put((self.child_num, 'ok', res))

    def sync(self):
        self.parent_queue.put((self.child_num, 'sync', None))
        resume_time = self.child_queue.get()
        now = time.time()
        if now > resume_time:
            raise AssertionError(
                "Resume time has already passed (%fs too late). Consider "
                "increasing 'message_delay', which is currently set to %f."
                % (now - resume_time, message_delay))
        # sleep until the resume time is near
        delay = resume_time - time.time() - 0.1
        if delay > 0:
            time.sleep(delay)
        # busy wait until the exact resume time
        while time.time() < resume_time:
            pass


def distribute(func, param_iter, strategy='mp'):
    """Call a function in separate processes concurrently.

    param_iter is an iterator that provides the first parameter for
    each function call. The second parameter for each call is a "sync"
    function. The sync function pauses execution, then resumes all
    processes at the same time. It is expected that all child processes
    will call the sync function the same number of times.

    The results of calling the function are appended to a list, which
    is returned once all functions have returned.  If any function
    raises an error, this raises AssertionError.
    """

    Process = strategies[strategy][0]
    Queue = strategies[strategy][1]

    children = {}
    parent_queue = Queue()
    for child_num, param in enumerate(param_iter):
        child = Child(child_num, parent_queue, func, param, Process, Queue)
        children[child_num] = child
    for child in children.values():
        child.process.start()

    try:
        results = []
        sync_waiting = set(children)

        while children:

            try:
                child_num, msg, arg = parent_queue.get(timeout=1)
            except Empty:
                # While we're waiting, see if any children have died.
                for child in children.values():
                    if not child.process.is_alive():
                        raise ChildProcessError(
                            "process running %r failed with exit code %d" % (
                                child.func, child.process.exitcode))
                continue

            if msg == 'ok':
                results.append(arg)
                child = children[child_num]
                child.process.join()
                del children[child_num]
            elif msg == 'exception':
                raise AssertionError(arg)
            elif msg == 'sync':
                sync_waiting.remove(child_num)
            else:
                raise AssertionError("unknown message: %s" % msg)

            if not sync_waiting:
                # All children have called sync(), so tell them
                # to resume shortly and set up for another sync.
                resume_time = time.time() + message_delay
                for child in children.values():
                    child.child_queue.put(resume_time)
                sync_waiting = set(children)

        return results

    finally:
        for child in children.values():
            if hasattr(child.process, 'terminate'):
                child.process.terminate()
            child.process.join()
        if hasattr(parent_queue, 'close'):
            parent_queue.close()
