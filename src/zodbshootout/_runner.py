##############################################################################
#
# Copyright (c) 2009 Zope Foundation and Contributors.
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
"""Compare the speed of different ZODB storages.

Opens the databases specified by a ZConfig file.

Splits into many processes to avoid contention over the global
interpreter lock.
"""
from __future__ import print_function, absolute_import


from io import StringIO
from .fork import ChildProcessError

from .speedtest import SpeedTest
from .speedtest import pobject_base_size

import os
import sys
from statistics import mean
from six import PY3

import ZConfig

try:
    from itertools import zip
except ImportError:
    zip = zip

def itervalues(d):
    try:
        iv = d.itervalues
    except AttributeError:
        iv = d.values
    return iv()

if PY3:
    ask = input
else:
    ask = raw_input # pylint:disable=undefined-variable

DEFAULT_MAX_ATTEMPTS = 20
DEFAULT_OBJ_COUNTS = (1000,)
DEFAULT_CONCURRENCIES = (2,)

schema_xml = u"""
<schema>
  <import package="ZODB"/>
  <multisection type="ZODB.database" name="*" attribute="databases" />
</schema>
"""

def _make_leak_check(options):
    if not options.leaks:
        return lambda: None, lambda: None

    if PY3:
        SIO = StringIO
    else:
        from io import BytesIO as SIO

    import objgraph
    import gc
    def prep_leaks():
        gc.collect()
        objgraph.show_growth(file=SIO())

    def show_leaks():
        gc.collect()
        gc.collect()
        sio = SIO()
        objgraph.show_growth(file=sio)
        if sio.getvalue():
            print("    Memory Growth")
            for line in sio.getvalue().split('\n'):
                print("    ", line)

    return prep_leaks, show_leaks

def _align_columns(rows):
    """Format a list of rows as CSV with aligned columns.
    """
    col_widths = []
    for col in zip(*rows):
        col_widths.append(max(len(value) for value in col))
    for row_num, row in enumerate(rows):
        line = []
        last_col = len(row) - 1
        for col_num, (width, value) in enumerate(zip(col_widths, row)):
            space = ' ' * (width - len(value))
            if row_num == 0:
                if col_num == last_col:
                    line.append(value)
                else:
                    line.append('%s, %s' % (value, space))
            elif col_num == last_col:
                if col_num == 0:
                    line.append(value)
                else:
                    line.append('%s%s' % (space, value))
            else:
                if col_num == 0:
                    line.append('%s, %s' % (value, space))
                else:
                    line.append('%s%s, ' % (space, value))
        yield ''.join(line)


def _print_results(options, contenders, object_counts, results):
    object_counts = options.counts or DEFAULT_OBJ_COUNTS
    concurrency_levels = options.concurrency or DEFAULT_CONCURRENCIES
    repetitions = options.repetitions

    txn_descs = (
        ("Add %d Objects", 0, 'add_time'),
        ("Update %d Objects", 0, 'update_time'),
        ("Read %d Warm Objects", 1, 'warm_time'),
        ("Read %d Cold Objects", 1, 'cold_time'),
        ("Read %d Hot Objects", 1, 'hot_time'),
        ("Read %d Steamin' Objects", 1, 'steamin_time'),
    )

    # show the results in CSV format
    print(file=sys.stderr)
    print(
        'Results show objects written or read per second. '
        'Mean of', repetitions, file=sys.stderr)

    for concurrency in concurrency_levels:
        print()
        print('** concurrency=%d **' % concurrency)

        rows = []
        row = ['"Transaction"']
        for contender_name, _db in contenders:
            row.append(contender_name)
        rows.append(row)

        for phase in txn_descs:
            for objects_per_txn in object_counts:
                desc = phase[0] % objects_per_txn
                row = ['"%s"' % desc]
                for contender_name, _db in contenders:
                    key = (objects_per_txn, concurrency, contender_name)
                    times = results.get(key)
                    if not times:
                        row.append("?")
                        continue

                    time = mean(getattr(t[phase[1]], phase[2]) for t in times)
                    count = (concurrency * objects_per_txn / time)
                    row.append('%d' % count)

                rows.append(row)

        for line in _align_columns(rows):
            print(line)

def _run_one_repetition(options, rep, speedtest, contender_name, db_factory, db_close):
    """
    Runs a single repetition of a contender.

    Returns a (write_times, read_times) tuple.
    """
    repetitions = options.repetitions
    for attempt in range(DEFAULT_MAX_ATTEMPTS):
        msg = '  Running %d/%d...' % (rep + 1, repetitions)
        if attempt > 0:
            msg += ' (attempt %d)' % (attempt + 1)
        print(msg, end=' ', file=sys.stderr)
        try:
            try:
                write_times, read_times = speedtest.run(
                    db_factory, contender_name, rep)
                return write_times, read_times
            finally:
                db_close()
        except ChildProcessError:
            if attempt >= DEFAULT_MAX_ATTEMPTS - 1:
                raise
            continue


def _run_one_contender(options, speedtest, contender_name, db_conf):
    """
    Runs the speed test *repetition* number of times.

    Return a list of (write_times, read_times) tuples.
    """

    def make_factory():
        _db = db_conf.open()
        return _db, _db.close, lambda: _db

    results = []
    prep_leaks, show_leaks = _make_leak_check(options)

    for rep in range(options.repetitions):
        if options.threads == 'shared':
            _db, db_close, db_factory = make_factory()
            _db.close = lambda: None
            _db.pack = lambda: None
        else:
            db_factory = db_conf.open
            db_close = lambda: None
        # After the DB is opened, so modules, etc, are imported.
        prep_leaks()
        write_times, read_times = _run_one_repetition(options, rep, speedtest, contender_name,
                                                      db_factory, db_close)
        msg = (
            'add %6.4fs, update %6.4fs, '
            'warm %6.4fs, cold %6.4fs, '
            'hot %6.4fs, steamin %6.4fs'
            % (write_times.add_time, write_times.update_time,
               read_times.warm_time, read_times.cold_time,
               read_times.hot_time, read_times.steamin_time))
        print(msg, file=sys.stderr)
        results.append((write_times, read_times))

        # Clear the things we created before checking for leaks
        del db_factory
        del db_close
        # in case it wasn't defined
        _db = None
        __db_close = None
        show_leaks()

    return results

def _zap(contenders):
    for db_name, db in contenders:
        db = db.open()
        if hasattr(db.storage, 'zap_all'):
            prompt = "Really destroy all data in %s? [yN] " % db_name
            resp = ask(prompt)
            if resp in 'yY':
                db.storage.zap_all()
        db.close()


def run_with_options(options):
    conf_fn = options.config_file

    # Do the gevent stuff ASAP
    if getattr(options, 'gevent', False):
        import gevent.monkey
        gevent.monkey.patch_all()

    if options.log:
        import logging
        lvl_map = getattr(logging, '_nameToLevel', None) or getattr(logging, '_levelNames', {})
        logging.basicConfig(level=lvl_map.get(options.log, logging.INFO),
                            format='%(asctime)s %(levelname)-5.5s [%(name)s][%(thread)d:%(process)d][%(threadName)s] %(message)s')


    object_counts = options.counts or DEFAULT_OBJ_COUNTS
    object_size = max(options.object_size, pobject_base_size)
    concurrency_levels = options.concurrency or DEFAULT_CONCURRENCIES
    profile_dir = options.profile_dir
    if profile_dir and not os.path.exists(profile_dir):
        os.makedirs(profile_dir)

    schema = ZConfig.loadSchemaFile(StringIO(schema_xml))
    config, _handler = ZConfig.loadConfigFile(schema, conf_fn)
    contenders = [(db.name, db) for db in config.databases]

    if options.zap:
        _zap(contenders)

    # results: {(objects_per_txn, concurrency, contender): [(write_times, read_times)]}}
    results = {}

    try:
        for objects_per_txn in options.counts or DEFAULT_OBJ_COUNTS:
            for concurrency in options.concurrency or DEFAULT_CONCURRENCIES:
                speedtest = SpeedTest(
                    concurrency, objects_per_txn, object_size, profile_dir,
                    'threads' if options.threads else 'mp',
                    test_reps=options.test_reps)
                if options.btrees:
                    import BTrees
                    if options.btrees == 'IO':
                        speedtest.MappingType = BTrees.family64.IO.BTree
                    else:
                        speedtest.MappingType = BTrees.family64.OO.BTree

                for contender_name, db in contenders:
                    print((
                        'Testing %s with objects_per_txn=%d, object_size=%d, '
                        'mappingtype=%s and concurrency=%d (threads? %s)'
                        % (contender_name, objects_per_txn, object_size,
                           speedtest.MappingType,
                           concurrency, options.threads)), file=sys.stderr)


                    key = (objects_per_txn, concurrency, contender_name)
                    all_times = _run_one_contender(options, speedtest, contender_name, db)
                    results[key] = all_times

    # The finally clause causes test results to print even if the tests
    # stop early.
    finally:
        _print_results(options, contenders, object_counts, results)
