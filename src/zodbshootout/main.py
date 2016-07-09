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

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser()
    obj_group = parser.add_argument_group("Objects", "Control the objects put in ZODB")
    con_group = parser.add_argument_group("Concurrency", "Control over concurrency")
    obj_group.add_argument(
        "-n", "--object-counts", dest="counts",
        type=int,
        action="append",
        help="Object counts to use (default 1000). Use this option as many times as you want.",
        )
    obj_group.add_argument(
        "-s", "--object-size", dest="object_size", default=pobject_base_size,
        type=int,
        help="Size of each object in bytes (estimated, default approx. %d)" % pobject_base_size,
        )
    parser.add_argument(
        '-r', '--repetitions', default=3,
        type=int,
        help="Number of repetitions of the complete test. The best values out of this many "
        "repetitions will be displayed. Default is 3.")
    con_group.add_argument(
        "-c", "--concurrency", dest="concurrency",
        type=int,
        action="append",
        help="Concurrency levels to use. Default is 2. Use this option as many times as you want."
        )
    parser.add_argument(
        "-p", "--profile", dest="profile_dir", default="",
        help="Profile all tests and output results to the specified directory",
        )
    obj_group.add_argument(
        "--btrees", nargs="?", const="IO", default=False,
        choices=['IO', 'OO'],
        help="Use BTrees. An argument, if given, is the family name to use, either IO or OO."
        " Specifying --btrees by itself will use an IO BTree; not specifying it will use PersistentMapping.")
    con_group.add_argument(
        "--threads", const="shared", default=False, nargs="?",
        choices=["shared", "unique"],
        help="Use threads instead of multiprocessing."
        " If you don't give an argument or you give the 'shared' argument,"
        " then one DB will be used by all threads. If you give the 'unique'"
        " argument, each thread will get its own DB.")
    parser.add_argument(
        "--log", nargs="?", const="INFO", default=False,
        choices=['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'],
        help="Enable logging in the root logger at the given level (INFO)")
    parser.add_argument(
        "--zap", action='store_true', default=False,
        help="Zap the entire RelStorage before running tests. This will destroy all data. ")

    try:
        import gevent
    except ImportError:
        pass
    else:
        con_group.add_argument(
            "--gevent", action="store_true", default=False,
            help="Monkey-patch the system with gevent before running. Implies --threads (if not given).")

    parser.add_argument("config_file", type=argparse.FileType())

    options = parser.parse_args(argv)

    # Do the gevent stuff ASAP
    # BEFORE other imports..and make sure we use "threads".
    # Because with MP, we use MP.Queue which would hang if we
    # monkey-patched the parent
    if getattr(options, 'gevent', False):
        import gevent.monkey
        gevent.monkey.patch_all()
        if not options.threads:
            options.threads = 'shared'


    from ._runner import run_with_options
    run_with_options(options)

if __name__ == '__main__':
    main()
