# -*- coding: utf-8 -*-
"""
Working with ZODB databases.

"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from io import StringIO
from io import BytesIO

import ZConfig

from zope.interface import implementer
from zope.interface import alsoProvides

from .interfaces import IBenchmarkDBFactory
from .interfaces import IBenchmarkDatabase
from .interfaces import IDBFactory
from .interfaces import IDBBenchmark

from ._wrapper import AbstractWrapper

NativeStringIO = BytesIO if bytes is str else StringIO

logger = __import__('logging').getLogger(__name__)

schema_xml = """
<schema>
  <import package="ZODB"/>
  <multisection type="ZODB.database" name="*" attribute="databases" />
</schema>
"""

def get_databases_from_conf_file(conf_file):
    """
    Read the file at *conf_fn* and return the database
    factories found there.
    """
    schema = ZConfig.loadSchemaFile(NativeStringIO(schema_xml))
    config, _handler = ZConfig.loadConfigFile(schema, conf_file)
    return config.databases

def get_databases_from_string(conf_string):
    return get_databases_from_conf_file(NativeStringIO(conf_string))

@implementer(IBenchmarkDBFactory)
class BenchmarkDBFactory(object):
    """
    Uses a lower-level factory (typically, but not always, from ZConfig)
    to get a database and return an object that implements `IBenchmarkDatabase`.
    """

    def __init__(self, zodb_conf_factory, objects_per_txn, concurrency, can_zap=False):
        self.factory = zodb_conf_factory
        self.objects_per_txn = objects_per_txn
        self.concurrency = concurrency
        self.can_zap = can_zap

    def __getattr__(self, name):
        # Because of multiprocessing. See AbstractConcurrentFunction
        try:
            factory = self.__dict__['factory']
        except KeyError:
            raise AttributeError(name)
        return getattr(factory, name)

    # TODO: Handle wrappers

    def _config_is_type(self, kind):
        try:
            return isinstance(self.factory.config.storage, kind)
        except AttributeError:
            return False

    def is_filestorage(self):
        from ZODB.config import FileStorage
        return self._config_is_type(FileStorage)

    def is_ZEO(self):
        from ZODB.config import ZEOClient
        return self._config_is_type(ZEOClient)

    def open(self):
        db = self.factory.open()
        # Explicitly set the number of cached objects so we're
        # using the storage in an understandable way.
        # Set to double the number of objects we should have created
        # to account for btree nodes.
        db.setCacheSize(self.objects_per_txn * 3)
        # Prevent warnings about large concurrent shared databases.
        db.setPoolSize(self.concurrency)
        db.speedtest_log_cache_stats = lambda msg='': self._log_cache_stats(db, msg)
        db.speedtest_zap_all = self._zap_all
        alsoProvides(db, IBenchmarkDatabase)
        return db

    __call__ = open

    def _log_cache_stats(self, db, msg=''):
        storage = db.storage
        cache = getattr(storage, '_cache', None)
        stats = getattr(cache, 'stats', lambda: {})()
        if stats:
            # TODO: Get these recorded in metadata for the benchmark that just ran
            logger.debug(
                "Cache hit stats for %s (%s): Hits: %s Misses: %s Ratio: %s Stores: %s",
                self.name, msg,
                stats.get('hits'), stats.get('misses'), stats.get('ratio'), stats.get('sets')
            )
        else:
            logger.debug("No storage cache found for %s (%s)",
                         self.name, msg)

    def _zap_all(self):
        if not self.can_zap:
            logger.debug("Not asked to zap %s", self.name)
            return

        db = self.factory.open()
        obj_count = len(db.storage)
        zap = None
        if hasattr(db.storage, 'zap_all'):
            zap = db.storage.zap_all
        elif hasattr(db.storage, 'cleanup'): # FileStorage
            zap = db.storage.cleanup
            db.close()
        if zap is not None:
            logger.debug("Zapping database %s (size: %d) using %s",
                         db, obj_count, zap)
            zap()
            logger.debug("Done zapping database %s", db)
        else:
            logger.debug("No way to zap database %s", self.name)
        db.close()

    def __repr__(self):
        return "CAC(%s)" % (self.name,)

@implementer(IDBFactory)
class MappingFactory(object):

    name = 'MappingStorage'

    def __init__(self, concurrency, data):
        self.concurrency = concurrency
        self.data = data

    def open(self):
        from ZODB import DB
        # Use a DemoStorage to support conflict resolution and
        # generally provide a more realistic, but still minimal,
        # storage to compare against.
        from ZODB.DemoStorage import DemoStorage

        db = DB(DemoStorage())

        db.close = lambda: None
        self.data.populate(lambda: db, include_data=True)
        del db.close
        return db

    __call__ = open


@implementer(IDBBenchmark)
class SharedDBFunction(AbstractWrapper):
    """
    A wrapper that ensures that the inner function always gets
    the same database object, no matter how many times the
    factory is invoked.
    """

    def __init__(self, function):
        self.__wrapped__ = function

    def __getattr__(self, name):
        return getattr(self.__wrapped__, name)

    @implementer(IBenchmarkDBFactory)
    class SharedDBFactory(object):
        def __init__(self, db_factory):
            from threading import RLock
            self.lock = RLock()
            self.factory = db_factory
            self.name = self.factory.name
            self.db = None
            self.reset()

        def reset(self):
            with self.lock:
                self.db = db = self.factory()
                db.close = lambda: None
                speedtest_zap_all = db.speedtest_zap_all
                def shared_zap():
                    with self.lock:
                        self.close()
                        speedtest_zap_all()
                        self.reset()
                db.speedtest_zap_all = shared_zap

        def close(self):
            with self.lock:
                if self.db is not None:
                    db = self.db
                    self.db = None

                    del db.close
                    db.close()

        def __call__(self):
            with self.lock:
                return self.db

    def __call__(self, loops, db_factory):
        db_and_close = self.SharedDBFactory(db_factory)
        try:
            return self.__wrapped__(loops, db_and_close)
        finally:
            db_and_close.close()
