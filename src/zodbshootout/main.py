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
"""
Main method.
"""
from __future__ import print_function, absolute_import

import argparse
import sys

# pylint:disable=import-outside-toplevel

if str is not bytes:
    ask = input
else:
    ask = raw_input # pylint:disable=undefined-variable

class ContainsAll(object):

    def __contains__(self, name):
        return True

class ContainsIfPrompted(object):

    def __contains__(self, name):
        prompt = "Really destroy all data in %s? [yN] " % name
        resp = ask(prompt)
        return resp in 'yY'


class ZapAction(argparse.Action):
    # Store an object that responds to "in" for the database name.
    # This will later be replaced with a list of database names
    def __call__(self, parser, namespace, values, option_string=None):
        if values == 'force':
            setattr(namespace, self.dest, ContainsAll())
        elif not values:
            # No argument given
            setattr(namespace, self.dest, ContainsIfPrompted())
        else:
            setattr(namespace, self.dest, values.split(','))

class LogAction(argparse.Action):
    # Either validate the string against the logging levels,
    # or attempt to interpret it as a path to a ZConfig file.
    def __call__(self, parser, namespace, values, option_string=None):
        if not values:
            # no argument given, default
            setattr(namespace, self.dest, 'INFO')
        elif values.upper() in ['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG']:
            setattr(namespace, self.dest, values.upper())
        else:
            as_file = argparse.FileType('r')(values)
            setattr(namespace, self.dest, as_file)


def main(argv=None): # pylint:disable=too-many-statements,too-many-locals,too-many-branches
    if argv is None:
        argv = sys.argv[1:]

    # Do the gevent stuff ASAP
    # BEFORE other imports..and make sure we use "threads".
    # Because with MP, we use MP.Queue which would hang if we
    # monkey-patched the parent
    if '--gevent' in argv:
        import gevent.monkey
        gevent.monkey.patch_all(Event=True)

    import os
    env_options = ['--inherit-environ',
                   ','.join([k for k in os.environ
                             if k.startswith(('GEVENT',
                                              'PYTHON',
                                              'ZS', # experimental zodbshootout config
                                              'RS', # relstorage config
                                              'COVERAGE'))])]
    # This is a default, so put it early
    argv[0:0] = env_options

    def worker_args(cmd, args): # pylint:disable=too-many-branches
        # Sadly, we have to manually put arguments that mean something to children
        # back on. There's no easy 'unparse' we can use. Some of the options for
        # pyperf, if we duplicate in the children, lead to errors (such as -o)
        if args.objects_per_txn:
            cmd.extend(('--object-counts', str(args.objects_per_txn)))
        if args.object_size:
            cmd.extend(('--object-size', str(args.object_size)))
        if args.btrees:
            cmd.extend(("--btrees", args.btrees))
        if args.use_blobs:
            cmd.extend(("--blobs",))
        if args.concurrency:
            cmd.extend(("--concurrency", str(args.concurrency)))
        if args.threads:
            cmd.extend(("--threads", args.threads))
        if args.gevent:
            cmd.extend(("--gevent",))
        if args.profile_dir:
            cmd.extend(("--profile", args.profile_dir))
        if not args.include_mapping:
            cmd.extend(('--include-mapping', "false"))
        if args.log:
            cmd.extend(('--log', args.log))
        if args.zap:
            cmd.extend(('--zap', ','.join(args.zap)))
        if args.leaks:
            cmd.extend(('--leaks',))
        if args.min_object_count:
            cmd.extend(('--min-objects', str(args.min_object_count)))
        if args.pack_on_populate:
            cmd.extend(("--pack",))
        cmd.extend(('--profiler', args.profiler))
        cmd.extend(env_options)
        cmd.append(args.config_file.name)
        if args.benchmarks and 'all' not in args.benchmarks:
            cmd.extend(args.benchmarks)

    # pyperf uses subprocess,s make sure it's gevent patched too.
    from pyperf import Runner
    runner = Runner(add_cmdline_args=worker_args, program_args=('-m', 'zodbshootout'))
    parser = runner.argparser
    prof_group = parser.add_argument_group("Profiling", "Control over profiling the database")
    obj_group = parser.add_argument_group("Objects", "Control the objects put in ZODB")
    con_group = parser.add_argument_group("Concurrency", "Control over concurrency")
    out_group = parser.add_argument_group("Output", "Control over the output")

    # Objects
    obj_group.add_argument(
        "--object-counts", dest="objects_per_txn",
        type=int,
        nargs="?",
        default=1000,
        action="store",
        help="Object counts to use (default %(default)d).",
    )
    obj_group.add_argument(
        "-s", "--object-size", dest="object_size",
        default=300,
        type=int,
        help="Size of each object in bytes (estimated, default approx. %(default)d)"
    )
    obj_group.add_argument(
        "--btrees", nargs="?", const="IO", default=False,
        choices=['IO', 'OO'],
        help="Use BTrees. An argument, if given, is the family name to use, either IO or OO."
        " Specifying --btrees by itself will use an IO BTree; not specifying it will use PersistentMapping."
    )
    # This becomes a list of database names to zap. Empty means zap nothing,
    # something besides a list means we need to prompt the user and replace the object
    # with the list they agreed to.
    obj_group.add_argument(
        "--zap", action=ZapAction,
        default=[],
        nargs='?',
        help="Zap the entire RelStorage before running tests. This will destroy all data. "
        "An argument of 'force' does this without prompting for all databases.  "
        "An argument that is a comma-separated list of databases will zap those database "
        "without prompting."
    )
    obj_group.add_argument(
        "--min-objects", dest="min_object_count",
        type=int, default=0, action="store",
        help="Ensure the database has at least this many objects before running tests.")

    obj_group.add_argument(
        "--blobs", dest="use_blobs", action='store_true', default=False,
        help="Use Blobs instead of pure persistent objects."
    )

    obj_group.add_argument(
        "--pack", dest="pack_on_populate", action='store_true', default=False,
        help="Pack the storage before populating it."
    )

    # Concurrency
    con_group.add_argument(
        "-c", "--concurrency", dest="concurrency",
        type=int,
        default=2,
        action="store",
        nargs="?",
        help="Concurrency level to use. Default is %(default)d."
    )
    con_group.add_argument(
        "--threads", const="shared", default=False, nargs="?",
        choices=["shared", "unique"],
        help="Use threads instead of multiprocessing."
        " If you don't give an argument or you give the 'shared' argument,"
        " then one DB will be used by all threads. If you give the 'unique'"
        " argument, each thread will get its own DB."
    )
    con_group.add_argument(
        "--fail-fast", dest="keep_going",
        action='store_false',
        default=True,
        help="Fail at the first benchmark failure instead of continuing."
    )

    try:
        import gevent
    except ImportError:
        pass
    else:
        con_group.add_argument(
            "--gevent", action="store_true", default=False,
            help="Monkey-patch the system with gevent before running. Implies --threads (if not given).")

    # Output

    out_group.add_argument(
        "--log", nargs="?", const="INFO", default='ERROR',
        action=LogAction,
        help="Enable logging in the root logger at the given level. "
        "Without an argument, the default is INFO. You may specify "
        "DEBUG, ERROR, CRITICAL, etc. Or you may give a path to a file "
        "that can be used by ZConfig to configure the loggers. See "
        "https://zconfig.readthedocs.io/en/latest/using-logging.html"
    )

    # Profiling

    prof_group.add_argument(
        "--profile", dest="profile_dir", default="",
        help="Profile all tests and output results to the specified directory",
    )

    profilers = ['cProfile']
    try:
        __import__('vmprof')
    except ImportError:
        pass
    else:
        profilers.append('vmprof')

    prof_group.add_argument(
        "--profiler", dest="profiler",
        default="cProfile",
        help="The profiler to use. Must be specified with 'profile-dir'",
        choices=profilers
    )

    prof_group.add_argument(
        "--include-mapping", dest='include_mapping', default=True,
        type=lambda s: s.lower() in ('true', 'yes', 'on'),
        nargs='?',
        help="Benchmark a MappingStorage. This serves as a floor."
        "Default is true; use any value besides 'true', 'yes' or 'on' "
        "to disable."
    )

    prof_group.add_argument(
        "--leaks", dest='leaks', action='store_true', default=False,
        help="Check for object leaks after every repetition. This only makes sense with --threads"
    )

    parser.add_argument("config_file", type=argparse.FileType())
    # TODO: Get this list dynamically.
    benchmark_names = [
        # Individual
        'add',
        'cold',
        'ex_commit',
        'im_commit',
        'conflicts',
        'hot',
        'new_oid',
        'prefetch_cold',
        'readCurrent',
        'steamin',
        'store',
        'update',
        'warm',
        'tpc',
    ]
    benchmark_choices = benchmark_names + ['-' + n for n in benchmark_names]
    parser.add_argument(
        'benchmarks',
        nargs='*',
        default='all',
        choices=['all'] + benchmark_choices,
    )
    options = runner.parse_args(argv)

    #import os
    #print("In pid", os.getpid(), "Is worker?", options.worker)
    # Do the gevent stuff ASAP
    # BEFORE other imports..and make sure we use "threads".
    # Because with MP, we use MP.Queue which would hang if we
    # monkey-patched the parent
    if not hasattr(options, 'gevent'):
        options.gevent = False
    if options.gevent:
        import gevent.monkey
        gevent.monkey.patch_all(Event=True)
        if not options.threads:
            options.threads = 'shared'

    options.benchmarks = (
        set(benchmark_names)
        if options.benchmarks == 'all'
        else set(options.benchmarks)
    )

    if 'all' in options.benchmarks:
        options.benchmarks.update(benchmark_names)

    excluded = {n[1:] for n in options.benchmarks if n.startswith('-')}
    if excluded:
        options.benchmarks = {n for n in options.benchmarks if not n.startswith('-')}
        if not options.benchmarks:
            # They *only* gave exclusions so we must begin
            # by taking away from the full set.
            options.benchmarks = set(benchmark_names)
        options.benchmarks = {n for n in options.benchmarks
                              if n not in excluded and not n.startswith('-')}

    if options.log:
        if hasattr(options.log, 'read'):
            # ZConfig file
            import ZConfig
            with options.log as f:
                conf_text = f.read()
            ZConfig.configureLoggers(conf_text)
            options.log = options.log.name
        else:
            import logging
            lvl_map = getattr(logging, '_nameToLevel', None) or getattr(logging, '_levelNames', {})
            logging.basicConfig(
                level=lvl_map.get(options.log, logging.INFO),
                format='%(asctime)s %(levelname)-5.5s [%(name)s][%(thread)d:%(process)d][%(threadName)s] %(message)s'
            )

    from ._dbsupport import get_databases_from_conf_file
    databases = get_databases_from_conf_file(options.config_file)
    # TODO: Allow filtering the list of databases on the command line.
    options.databases = databases
    if not isinstance(options.zap, list):
        zappable = [db_factory.name
                    for db_factory in databases
                    if db_factory.name in options.zap]
        options.zap = zappable

    if options.leaks and not options.threads:
        sys.exit("Can only use leak checking in threaded mode.")

    if options.zap and 'add' not in options.benchmarks:
        sys.exit("Cannot zap if you're not adding")

    from ._runner import run_with_options

    # If an output file isn't given, do go ahead and pick a temporary
    # file to save to.  This way the user doesn't necessarily lose any
    # results that later turned out to be interesting.
    if not options.output and not options.worker and options.pipe is None:
        import tempfile
        prefix = "zodbshootout-results-c{}-o{}-{}-".format(
            options.concurrency,
            options.objects_per_txn,
            'gevent' if options.gevent else ('threads' if options.threads else 'procs')
        )
        temp_output_fd, name = tempfile.mkstemp(".json", prefix=prefix)
        # Passing the pipe doesn't work? Writing the results gets a bad FD
        # in the master. Plus, it also disables normal display of the results
        # to the console.
        # options.pipe = temp_output_fd
        os.close(temp_output_fd)
        # --append accepts a filename, but it must already be valid benchmark JSON data.
        # --output verifies the file doesn't exist.
        os.unlink(name)
        options.append = name
        print("(Automatically saving output to ", name, ")",
              file=sys.stderr, sep='')


    run_with_options(runner, options)

if __name__ == '__main__':
    main()
