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

from persistent import Persistent
from persistent.mapping import PersistentMapping
from StringIO import StringIO
from zodbshootout.fork import ChildProcessError
from zodbshootout.fork import distribute
from zodbshootout.fork import run_in_child
import cPickle
import optparse
import os
import sys
import time
import transaction
import warnings
import ZConfig

warnings.filterwarnings("ignore", "the sha module is deprecated",
    DeprecationWarning)

debug = False
repetitions = 3
max_attempts = 20

schema_xml = """
<schema>
  <import package="ZODB"/>
  <multisection type="ZODB.database" name="*" attribute="databases" />
</schema>
"""

class PObject(Persistent):
    """A trivial persistent object"""
    attr = 1

    def __init__(self, data):
        self.data = data


# Estimate the size of a minimal PObject stored in ZODB.
pobject_base_size = (
    len(cPickle.dumps(PObject)) + len(cPickle.dumps(PObject(''))))


class SpeedTest:

    def __init__(self, concurrency, objects_per_txn, object_size,
            profile_dir=None):
        self.concurrency = concurrency
        self.objects_per_txn = objects_per_txn
        data = 'x' * max(0, object_size - pobject_base_size)
        self.data_to_store = dict(
            (n, PObject(data)) for n in range(objects_per_txn))
        self.profile_dir = profile_dir
        self.contender_name = None
        self.rep = 0  # repetition number

    def populate(self, db_factory):
        db = db_factory()
        conn = db.open()
        root = conn.root()

        # clear the database
        root['speedtest'] = None
        transaction.commit()
        db.pack()

        # put a tree in the database
        root['speedtest'] = t = PersistentMapping()
        for i in range(self.concurrency):
            t[i] = PersistentMapping()
        transaction.commit()
        conn.close()
        db.close()
        if debug:
            print >> sys.stderr, 'Populated storage.'

    def write_test(self, db_factory, n, sync):
        db = db_factory()

        def do_add():
            start = time.time()
            conn = db.open()
            root = conn.root()
            m = root['speedtest'][n]
            m.update(self.data_to_store)
            transaction.commit()
            conn.close()
            end = time.time()
            return end - start

        db.open().close()
        sync()
        add_time = self._execute(do_add, 'add', n)

        def do_update():
            start = time.time()
            conn = db.open()
            root = conn.root()
            for obj in conn.root()['speedtest'][n].itervalues():
                obj.attr = 1
            transaction.commit()
            conn.close()
            end = time.time()
            return end - start

        sync()
        update_time = self._execute(do_update, 'update', n)

        time.sleep(.1)
        db.close()
        return add_time, update_time

    def read_test(self, db_factory, n, sync):
        db = db_factory()
        db.setCacheSize(len(self.data_to_store)+400)

        def do_read():
            start = time.time()
            conn = db.open()
            got = 0
            for obj in conn.root()['speedtest'][n].itervalues():
                got += obj.attr
            del obj
            if got != self.objects_per_txn:
                raise AssertionError('data mismatch')
            conn.close()
            end = time.time()
            return end - start

        db.open().close()
        sync()
        warm = self._execute(do_read, 'warm', n)

        # Clear all caches
        conn = db.open()
        conn.cacheMinimize()
        storage = conn._storage
        if hasattr(storage, '_cache'):
            storage._cache.clear()
        conn.close()

        sync()
        cold = self._execute(do_read, 'cold', n)

        conn = db.open()
        conn.cacheMinimize()
        conn.close()

        sync()
        hot = self._execute(do_read, 'hot', n)
        sync()
        steamin = self._execute(do_read, 'steamin', n)

        db.close()
        return warm, cold, hot, steamin

    def _execute(self, func, phase_name, n):
        if not self.profile_dir:
            return func()
        basename = '%s-%s-%d-%02d-%d' % (
            self.contender_name, phase_name, self.objects_per_txn, n, self.rep)
        txt_fn = os.path.join(self.profile_dir, basename + ".txt")
        prof_fn = os.path.join(self.profile_dir, basename + ".prof")
        import cProfile
        output = []
        d = {'_func': func, '_output': output}
        cProfile.runctx("_output.append(_func())", d, d, prof_fn)
        res = output[0]
        from pstats import Stats
        f = open(txt_fn, 'w')
        st = Stats(prof_fn, stream=f)
        st.strip_dirs()
        st.sort_stats('cumulative')
        st.print_stats()
        f.close()
        return res

    def run(self, db_factory, contender_name, rep):
        """Run a write and read test.

        Returns the mean time per transaction for 4 phases:
        write, cold read, hot read, and steamin' read.
        """
        self.contender_name = contender_name
        self.rep = rep

        run_in_child(self.populate, db_factory)

        def write(n, sync):
            return self.write_test(db_factory, n, sync)
        def read(n, sync):
            return self.read_test(db_factory, n, sync)

        r = range(self.concurrency)
        write_times = distribute(write, r)
        read_times = distribute(read, r)

        add_times = [t[0] for t in write_times]
        update_times = [t[1] for t in write_times]
        warm_times = [t[0] for t in read_times]
        cold_times = [t[1] for t in read_times]
        hot_times = [t[2] for t in read_times]
        steamin_times = [t[3] for t in read_times]

        return (
            sum(add_times) / self.concurrency,
            sum(update_times) / self.concurrency,
            sum(warm_times) / self.concurrency,
            sum(cold_times) / self.concurrency,
            sum(hot_times) / self.concurrency,
            sum(steamin_times) / self.concurrency,
            )


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

    parser = optparse.OptionParser(usage='%prog [options] config_file')
    parser.add_option(
        "-n", "--object-counts", dest="counts", default="1000",
        help="Object counts to use, separated by commas (default 1000)",
        )
    parser.add_option(
        "-s", "--object-size", dest="object_size", default="115",
        help="Size of each object in bytes (estimated, default approx. 115)",
        )
    parser.add_option(
        "-c", "--concurrency", dest="concurrency", default="2",
        help="Concurrency levels to use, separated by commas (default 2)",
        )
    parser.add_option(
        "-p", "--profile", dest="profile_dir", default="",
        help="Profile all tests and output results to the specified directory",
        )

    options, args = parser.parse_args(argv)
    if len(args) != 1:
        parser.error("exactly one database configuration file is required")
    conf_fn = args[0]

    object_counts = [int(x.strip())
                     for x in options.counts.split(',')]
    object_size = max(int(options.object_size), pobject_base_size)
    concurrency_levels = [int(x.strip())
                          for x in options.concurrency.split(',')]
    profile_dir = options.profile_dir
    if profile_dir and not os.path.exists(profile_dir):
        os.makedirs(profile_dir)

    schema = ZConfig.loadSchemaFile(StringIO(schema_xml))
    config, handler = ZConfig.loadConfig(schema, conf_fn)
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
                for contender_name, db in contenders:
                    print >> sys.stderr, (
                        'Testing %s with objects_per_txn=%d, object_size=%d, '
                        'and concurrency=%d'
                        % (contender_name, objects_per_txn, object_size,
                            concurrency))
                    db_factory = db.open
                    key = (objects_per_txn, concurrency, contender_name)

                    for rep in range(repetitions):
                        for attempt in range(max_attempts):
                            msg = '  Running %d/%d...' % (rep + 1, repetitions)
                            if attempt > 0:
                                msg += ' (attempt %d)' % (attempt + 1)
                            print >> sys.stderr, msg,
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
                        print >> sys.stderr, msg
                        for i in range(6):
                            results[key + (i,)].append(times[i])

    # The finally clause causes test results to print even if the tests
    # stop early.
    finally:

        # show the results in CSV format
        print >> sys.stderr
        print >> sys.stderr, (
            'Results show objects written or read per second. '
            'Best of 3.')

        for concurrency in concurrency_levels:
            print
            print '** concurrency=%d **' % concurrency

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
                print line


if __name__ == '__main__':
    main()
