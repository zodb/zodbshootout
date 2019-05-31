# -*- coding: utf-8 -*-
"""
Tests for running the actual zodbshootout.

"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function


logger = __import__('logging').getLogger(__name__)

import os
import sys
import tempfile
import unittest
import shutil
import subprocess

class TestMain(unittest.TestCase):

    args = ()
    benchmark_names = ('add',)
    fast_args = (
        '--object-counts', '100',
        '--loops', '1',
        '--warmups', '0',
        '--processes', '1',
        '--values', '1',
        '--concurrency', '1',
    )

    def setUp(self):
        super(TestMain, self).setUp()

        self.tempd = tempfile.mkdtemp('.zodbshootout')
        self.addCleanup(shutil.rmtree, self.tempd)
        path = os.path.join(self.tempd, 'Data.fs')

        conf = """
        <zodb fs>
            <filestorage>
               path %s
            </filestorage>
        </zodb>
        """ % (path,)

        self.conf_file = os.path.join(self.tempd, 'zodb.conf')

        with open(self.conf_file, 'w') as f:
            f.write(conf)

    def __clear_conf(self):
        with open(self.conf_file, 'w') as f:
            f.write('\n')

    def __convert_output(self, output):
        if isinstance(output, bytes) and bytes is not str:
            output = output.decode('ascii')
        return output

    def _run(self):
        cmd = [
            sys.executable,
            '-m',
            'zodbshootout',
        ]
        # Later arguments override earlier ones;
        # go fast by default but allow changing
        cmd.extend(self.fast_args)
        cmd.extend(self.args)
        cmd.append(self.conf_file)
        cmd.extend(self.benchmark_names)

        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            return self.__convert_output(output)
        except subprocess.CalledProcessError as cpe:
            info = ' '.join(cmd)
            info += '\n'
            info += self.__convert_output(cpe.output)
            self.fail("Failed to run process " + info)


    def test_bm_names(self):
        self.benchmark_names = ('add',)

        output = self._run()
        __traceback_info__ = output
        self.assertNotIn('update', output)
        self.assertNotIn('read', output)

    def test_output(self):
        output_file = os.path.join(self.tempd, 'result.json')
        output_dir = os.path.join(self.tempd, 'result.d')
        self.args = ('--output', output_file)

        self._run()

        self.assertTrue(os.path.exists(output_file))
        self.assertTrue(os.path.exists(output_dir))
        self.assertTrue(os.path.isdir(output_dir))
        __traceback_info__ = os.listdir(output_dir)
        self.assertTrue(os.path.exists(
            os.path.join(output_dir, 'MappingStorage_100.json')
        ))
        self.assertTrue(os.path.exists(
            os.path.join(output_dir, 'fs_100.json')
        ))

    def test_gevent(self):
        # We can't run at concurrency 2 without using shared threads
        # because of the file storage
        self.args = (
            '--concurrency', '2',
            '--gevent'
        )

        self._run()

    def test_leaks(self):
        self.args = ('--threads', '--leaks')
        self._run()

    def test_forked_concurrency_with_profile(self):
        threads = '--threads' in self.args
        if not threads:
            self.__clear_conf()
        profile_dir = os.path.join(self.tempd, 'profile')
        self.args += (
            '--concurrency', '2',
            '--profile', profile_dir
        )

        self._run()
        self.assertTrue(os.path.exists(profile_dir))
        self.assertTrue(os.path.isdir(profile_dir))
        __traceback_info__ = os.listdir(profile_dir)


        self.assertTrue(os.path.exists(
            os.path.join(profile_dir, 'MappingStorage_add.prof')
        ))
        self.assertTrue(os.path.exists(
            os.path.join(profile_dir, 'MappingStorage_add.txt')
        ))

        if threads:
            self.assertTrue(os.path.exists(
                os.path.join(profile_dir, 'fs_add.prof')
            ))
            self.assertTrue(os.path.exists(
                os.path.join(profile_dir, 'fs_add.txt')
            ))

    def test_gevent_concurrency_with_profile(self):
        self.args += ('--threads', 'shared', '--gevent')
        self.test_forked_concurrency_with_profile()

    def test_thread_concurrency_with_profile(self):
        self.args += ('--threads', 'shared')
        self.test_forked_concurrency_with_profile()

    def test_gevent_cooperative(self):
        self.args += ('--gevent', '--log', 'INFO', '--concurrency', '3')

        output = self._run()
        self.assertIn('gevent NON-cooperative', output)
