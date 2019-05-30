#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function, absolute_import, division

#disable: accessing protected members, too many methods
#pylint: disable=W0212,R0904

import os
import signal
import threading
import time
import unittest

from zodbshootout import fork

def func_that_raises_exception():
    raise Exception("I failed!")

def func_that_raises_base_exception():
    raise BaseException("I basically failed")

def func_that_raises_KeyboardInterrupt():
    raise KeyboardInterrupt()

def func_that_raises_SystemExit():
    raise SystemExit()

def func_that_returns_value(value):
    return value

_should_sleep = True
_is_sleeping = False

def func_that_sleeps(*_args):
    global _is_sleeping
    _is_sleeping = True
    while _should_sleep:
        time.sleep(0.5)
    _is_sleeping = False

class ForkTestMixin(object):

    def setUp(self):
        # quiet the logging
        self._log_exception = fork.Child._log_exception
        fork.Child._log_exception = lambda s: None

    def tearDown(self):
        fork.Child._log_exception = self._log_exception

    def test_run_in_child_fails_exception(self):
        self.assertRaises(fork.ExceptionInChildError,
                          fork.run_in_child,
                          func_that_raises_exception,
                          self.strategy)

    def test_run_in_child_fails_base_exception(self):
        self.assertRaises(fork.ExceptionInChildError,
                          fork.run_in_child,
                          func_that_raises_base_exception,
                          self.strategy)

    def test_run_in_child_system_exit(self):
        self.assertRaises(fork.UnexpectedChildDeathError,
                          fork.run_in_child,
                          func_that_raises_SystemExit,
                          self.strategy)

    def test_run_in_child_keyboard_interrupt(self):
        self.assertRaises(KeyboardInterrupt,
                          fork.run_in_child,
                          func_that_raises_KeyboardInterrupt,
                          self.strategy)

    def test_run_in_child_value(self):
        self.assertEqual(42,
                         fork.run_in_child(func_that_returns_value, self.strategy, value=42))


    def test_distribute_main_proc_gets_keyboard_interrupt(self):
        global _should_sleep
        global _is_sleeping
        _is_sleeping = False
        _should_sleep = True

        def background():
            time.sleep(1)
            os.kill(os.getpid(), signal.SIGINT)

        sig_thread = threading.Thread(target=background)
        sig_thread.name = 'SignalThread'

        with self.assertRaises(KeyboardInterrupt):
            fork.distribute(func_that_sleeps,
                            [None],
                            strategy=self.strategy,
                            before_poll=sig_thread.start)

        # If we ran the thread in process, wait for it to die.
        while _is_sleeping:
            _should_sleep = False
            time.sleep(0.0001)



class TestThreadFork(ForkTestMixin, unittest.TestCase):
    strategy = 'threads'

class TestMPFork(ForkTestMixin, unittest.TestCase):
    strategy = 'mp'

def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestThreadFork))
    suite.addTest(unittest.makeSuite(TestMPFork))
    return suite

if __name__ == '__main__':
    unittest.main(defaultTest='test_suite')
