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

from ._pobject import pobject_base_size

class ZapAction(argparse.Action):

    def __call__(self, parser, namespace, values, option_string=None):
        if values == 'force':
            setattr(namespace, self.dest, 'force')
        else:
            setattr(namespace, self.dest, True)

def main(argv=None): # pylint:disable=too-many-statements
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
                                              'COVERAGE'))])]
    argv.extend(env_options)


    def worker_args(cmd, args):
        # Sadly, we have to manually put arguments that mean something to children
        # back on. There's no easy 'unparse' we can use. Some of the options for
        # pyperf, if we duplicate in the children, lead to errors (such as -o)
        if args.counts:
            cmd.extend(('--object-counts', str(args.counts)))
        if args.object_size:
            cmd.extend(('--object-size', str(args.object_size)))
        if args.btrees:
            cmd.extend("--btrees", args.btrees)
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
            cmd.extend(('--log', options.log))
        cmd.extend(env_options)
        cmd.append(args.config_file.name)
        cmd.extend(args.benchmarks)

    # pyperf uses subprocess,s make sure it's gevent patched too.
    from pyperf import Runner
    runner = Runner(add_cmdline_args=worker_args)
    parser = runner.argparser
    prof_group = parser.add_argument_group("Profiling", "Control over profiling the database")
    obj_group = parser.add_argument_group("Objects", "Control the objects put in ZODB")
    con_group = parser.add_argument_group("Concurrency", "Control over concurrency")
    out_group = parser.add_argument_group("Output", "Control over the output")

    # Objects
    obj_group.add_argument(
        "--object-counts", dest="counts",
        type=int,
        nargs="?",
        default=1000,
        action="store",
        help="Object counts to use (default %(default)d).",
    )
    obj_group.add_argument(
        "-s", "--object-size", dest="object_size", default=pobject_base_size,
        type=int,
        help="Size of each object in bytes (estimated, default approx. %(default)d)"
    )
    obj_group.add_argument(
        "--btrees", nargs="?", const="IO", default=False,
        choices=['IO', 'OO'],
        help="Use BTrees. An argument, if given, is the family name to use, either IO or OO."
        " Specifying --btrees by itself will use an IO BTree; not specifying it will use PersistentMapping."
    )
    obj_group.add_argument(
        "--zap", action=ZapAction,
        default=False,
        nargs='?',
        help="Zap the entire RelStorage before running tests. This will destroy all data. "
    )
    obj_group.add_argument(
        "--min-objects", dest="min_object_count",
        type=int, default=0, action="store",
        help="Ensure the database has at least this many objects before running tests.")

    obj_group.add_argument(
        "--blobs", dest="use_blobs", action='store_true', default=False,
        help="Use Blobs instead of pure persistent objects."
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
        "--log", nargs="?", const="INFO", default=False,
        choices=['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'],
        help="Enable logging in the root logger at the given level (INFO)"
    )

    # Profiling

    prof_group.add_argument(
        "--profile", dest="profile_dir", default="",
        help="Profile all tests and output results to the specified directory",
    )

    prof_group.add_argument(
        "--include-mapping", dest='include_mapping', default=True,
        type=lambda s: s.lower() in ('true', 'yes', 'on'),
        nargs='?',
        help="Benchmark a MappingStorage. This serves as a floor."
        "Default is true; use any value besides 'true', 'yes' or 'on' "
        "to disable."
    )
    # prof_group.add_argument(
    #     "-l", "--leaks", dest='leaks', action='store_true', default=False,
    #     help="Check for object leaks after every repetition. This only makes sense with --threads"
    # )

    parser.add_argument("config_file", type=argparse.FileType())
    parser.add_argument('benchmarks',
                        nargs='*',
                        default='all',
                        choices=['add', 'update', 'warm', 'cold', 'hot', 'steamin', 'all'])
    options = runner.parse_args(argv)

    #import os
    #print("In pid", os.getpid(), "Is worker?", options.worker)
    # Do the gevent stuff ASAP
    # BEFORE other imports..and make sure we use "threads".
    # Because with MP, we use MP.Queue which would hang if we
    # monkey-patched the parent
    if getattr(options, 'gevent', False):
        import gevent.monkey
        gevent.monkey.patch_all(Event=True)
        if not options.threads:
            options.threads = 'shared'

    if 'all' in options.benchmarks or options.benchmarks == 'all':
        options.benchmarks = ['all']

    if options.log:
        import logging
        lvl_map = getattr(logging, '_nameToLevel', None) or getattr(logging, '_levelNames', {})
        logging.basicConfig(level=lvl_map.get(options.log, logging.INFO),
                            format='%(asctime)s %(levelname)-5.5s [%(name)s][%(thread)d:%(process)d][%(threadName)s] %(message)s')

    from ._runner import run_with_options
    run_with_options(runner, options)

if __name__ == '__main__':
    main()
