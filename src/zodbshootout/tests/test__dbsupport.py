# -*- coding: utf-8 -*-
"""
Tests for _dbsupport.py

"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import unittest
import tempfile
import shutil

from hamcrest import assert_that
from nti.testing.matchers import provides
from nti.testing.matchers import verifiably_provides

from ..interfaces import IBenchmarkDBFactory
from ..interfaces import IBenchmarkDatabase
from .. import _dbsupport as dbsupport

class TestBenchmarkDBFactory(unittest.TestCase):

    def test_implements(self):
        class WithName(object):
            name = 'foo'
        factory = dbsupport.BenchmarkDBFactory(WithName, None, None, False)
        assert_that(factory, verifiably_provides(IBenchmarkDBFactory))

    def test_name_comes_from_factory(self):
        class WithName(object):
            name = 'foo'
        factory = dbsupport.BenchmarkDBFactory(WithName, None, None, False)

        self.assertEqual(factory.name, WithName.name)

    def test_zap_file_storage(self):
        tempd = tempfile.mkdtemp('.zodbshootout')
        self.addCleanup(shutil.rmtree, tempd)
        path = os.path.join(tempd, 'Data.fs')

        conf = """
        <zodb fs>
            <filestorage>
               path %s
            </filestorage>
        </zodb>
        """ % (path,)

        fs_db_factory = dbsupport.get_databases_from_string(conf)[0]
        db = fs_db_factory.open()
        db.close()

        self.assertTrue(os.path.exists(path))

        speed_db_factory = dbsupport.BenchmarkDBFactory(fs_db_factory, 1, 1, False)
        db = speed_db_factory()
        # Sadly, it doesn't verifiably provide even IDatabase!
        assert_that(db, provides(IBenchmarkDatabase))
        db.speedtest_log_cache_stats() # Coverage.
        db.close()
        db.speedtest_zap_all()
        self.assertTrue(os.path.exists(path))

        speed_db_factory = dbsupport.BenchmarkDBFactory(fs_db_factory, 1, 1, True)
        db = speed_db_factory()
        db.close()
        db.speedtest_zap_all()
        self.assertFalse(os.path.exists(path))
