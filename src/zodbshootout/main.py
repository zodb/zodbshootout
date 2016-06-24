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

import argparse
import os
import sys


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


max_attempts = 20

schema_xml = u"""
<schema>
  <import package="ZODB"/>
  <multisection type="ZODB.database" name="*" attribute="databases" />
</schema>
"""


def align_columns(rows):
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


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-n", "--object-counts", dest="counts",
        type=int,
        action="append",
        help="Object counts to use (default 1000). Use this option as many times as you want.",
        )
    parser.add_argument(
        "-s", "--object-size", dest="object_size", default=pobject_base_size,
        type=int,
        help="Size of each object in bytes (estimated, default approx. %d)" % pobject_base_size,
        )
    parser.add_argument(
        '-r', '--repetitions', default=3,
        type=int,
        help="Number of repetitions of the complete test. The best values out of this many "
        "repetitions will be displayed. Default is 3.")
    parser.add_argument(
        "-c", "--concurrency", dest="concurrency",
        type=int,
        action="append",
        help="Concurrency levels to use. Default is 2. Use this option as many times as you want."
        )
    parser.add_argument(
        "-p", "--profile", dest="profile_dir", default="",
        help="Profile all tests and output results to the specified directory",
        )
    parser.add_argument(
        "--btrees", nargs="?", const="IO", default=False,
        choices=['IO', 'OO'],
        help="Use BTrees. An argument, if given, is the family name to use, either IO or OO."
        " Specifying --btrees by itself will use an IO BTree; not specifying it will use PersistentMapping.")
    parser.add_argument("config_file", type=argparse.FileType())

    options = parser.parse_args(argv)
    conf_fn = options.config_file

    object_counts = options.counts or [1000]
    object_size = max(options.object_size, pobject_base_size)
    concurrency_levels = options.concurrency or [2]
    profile_dir = options.profile_dir
    repetitions = options.repetitions
    if profile_dir and not os.path.exists(profile_dir):
        os.makedirs(profile_dir)

    schema = ZConfig.loadSchemaFile(StringIO(schema_xml))
    config, _handler = ZConfig.loadConfigFile(schema, conf_fn)
    contenders = [(db.name, db) for db in config.databases]

    txn_descs = (
        "Add %d Objects",
        "Update %d Objects",
        "Read %d Warm Objects",
        "Read %d Cold Objects",
        "Read %d Hot Objects",
        "Read %d Steamin' Objects",
        )

    # results: {(objects_per_txn, concurrency, contender, phase): [time]}}
    results = {}
    for objects_per_txn in object_counts:
        for concurrency in concurrency_levels:
            for contender_name, db in contenders:
                for phase in range(len(txn_descs)):
                    key = (objects_per_txn, concurrency,
                            contender_name, phase)
                    results[key] = []

    try:
        for objects_per_txn in object_counts:
            for concurrency in concurrency_levels:
                speedtest = SpeedTest(
                    concurrency, objects_per_txn, object_size, profile_dir)
                if options.btrees:
                    import BTrees
                    if options.btrees == 'IO':
                        speedtest.MappingType = BTrees.family64.IO.BTree
                    else:
                        speedtest.MappingType = BTrees.family64.OO.BTree

                for contender_name, db in contenders:
                    print((
                        'Testing %s with objects_per_txn=%d, object_size=%d, '
                        'mappingtype=%s and concurrency=%d'
                        % (contender_name, objects_per_txn, object_size,
                           speedtest.MappingType,
                            concurrency)), file=sys.stderr)

                    key = (objects_per_txn, concurrency, contender_name)

                    for rep in range(repetitions):
                        for attempt in range(max_attempts):
                            msg = '  Running %d/%d...' % (rep + 1, repetitions)
                            if attempt > 0:
                                msg += ' (attempt %d)' % (attempt + 1)
                            print(msg, end=' ', file=sys.stderr)
                            try:
                                times = speedtest.run(
                                    db.open, contender_name, rep)
                            except ChildProcessError:
                                if attempt >= max_attempts - 1:
                                    raise
                            else:
                                break
                        msg = (
                            'add %6.4fs, update %6.4fs, '
                            'warm %6.4fs, cold %6.4fs, '
                            'hot %6.4fs, steamin %6.4fs'
                            % times)
                        print(msg, file=sys.stderr)
                        for i in range(6):
                            results[key + (i,)].append(times[i])

    # The finally clause causes test results to print even if the tests
    # stop early.
    finally:

        # show the results in CSV format
        print(file=sys.stderr)
        print(
            'Results show objects written or read per second. '
            'Best of', repetitions, file=sys.stderr)

        for concurrency in concurrency_levels:
            print()
            print('** concurrency=%d **' % concurrency)

            rows = []
            row = ['"Transaction"']
            for contender_name, db in contenders:
                row.append(contender_name)
            rows.append(row)

            for phase in range(len(txn_descs)):
                for objects_per_txn in object_counts:
                    desc = txn_descs[phase] % objects_per_txn
                    if objects_per_txn == 1:
                        desc = desc[:-1]
                    row = ['"%s"' % desc]
                    for contender_name, db in contenders:
                        key = (objects_per_txn, concurrency,
                            contender_name, phase)
                        times = results[key]
                        if times:
                            count = (
                                concurrency * objects_per_txn / min(times))
                            row.append('%d' % count)
                        else:
                            row.append('?')
                    rows.append(row)

            for line in align_columns(rows):
                print(line)


if __name__ == '__main__':
    main()
