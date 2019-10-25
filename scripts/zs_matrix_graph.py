#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Written in 2019 by Jason Madden <jason@nextthhought.com>
# and placed in the public domain.
"""
Given a combined JSON data file as produced by ``zs_matrix_combine``,
produces PNG images in a directory visualizing the data. Can either
compare different databases with the same RelStorage version, or
different RelStorage versions entirely.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

# pylint:disable=too-many-locals

import argparse
import json
import os
import tempfile

import pyperf

import matplotlib
import matplotlib.pyplot as plt
from pandas import DataFrame
import seaborn

def _fix_database(n, version=''):
    result = n
    if 'mysql' in n.lower():
        result = 'MySQL'
    if 'psycopg2' in n.lower():
        result = 'PostgreSQL'
    if 'zeo' in n.lower():
        result = 'ZEO'
    elif 'fs' in n.lower() or 'filestorage' in n.lower():
        result = 'FileStorage'
    if version:
        result = f'{result} ({version})'
    return result

def suite_to_benchmark_data(_args, benchmark_suite, version=''):
    """
    Return a DataFrame containing every observation.
    """
    rows = []
    for benchmark in benchmark_suite.get_benchmarks():

        # {c=1 processes, o=100} mysqlclient_hf: read 100 hot objects'
        name = benchmark.get_name()
        if '(disabled)' in name:
            continue

        # '{c=1 processes, o=100', ' mysqlclient_hf: read 100 hot objects'
        prefix, suffix = name.rsplit('}', 1)
        ConcurrencyKind = 'processes' if 'processes' in prefix else 'threads'

        prefix = prefix.replace(' processes', '').replace(' threads', '')
        prefix = prefix.replace(' greenlets', '')
        prefix += '}'

        d = json.loads(prefix.replace('c', '"c"').replace('o', '"o"').replace('=', ':'))
        Concurrency = d['c']
        Objects = d['o']

        Database, suffix = suffix.strip().split(':', 1)
        suffix = suffix.strip()
        Database = _fix_database(Database, version)
        if version and Database.startswith('ZEO'):
            # Exclude ZEO from these comparisons.
            # (It messes up our pairing)
            continue
        Action = suffix.replace(str(Objects) + ' ', '')

        for run in benchmark.get_runs():
            for value in run.values:
                row = dict(
                    concurrency_kind=ConcurrencyKind, database=Database,
                    concurrency=Concurrency,
                    action=Action, objects=Objects, duration=value,
                    version=version,
                )
                rows.append(row)

    df = DataFrame(rows)
    return df


def save_one(df, benchmark_name, outdir, palette=None):
    df = df.query('action=="%s"' % benchmark_name)
    fname = benchmark_name.replace(' ', '_').replace('/', '_') + '.png'

    fig = seaborn.catplot(
        "concurrency", "duration",
        data=df,
        #kind="swarm", # The swarm plots is also interesting
        kind="bar",
        hue="database",
        hue_order=sorted(df['database'].unique()),
        col="objects",
        row="concurrency_kind",
        palette=palette,
        sharey=False,
        legend=False,
    )
    fig.set(ylabel="ms")
    fig.add_legend(title=benchmark_name)
    fig.savefig(os.path.join(outdir, fname), transparent=True)
    fig.despine()
    plt.close(fig.fig)

def save_all(df, outdir, versions=None):
    all_bmarks = df['action'].unique()

    for bmark in all_bmarks:
        save_one(df, bmark, outdir, palette='Paired' if versions else None)


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=argparse.FileType('r'))
    parser.add_argument('compare_to', type=argparse.FileType('r'), nargs='?')
    parser.add_argument(
        '--versions',
        nargs=2,
        help="If compare_to is given, you must specify the versions to tag identical "
        "databases with."
    )
    args = parser.parse_args()

    with args.input as f:
        s = f.read()
    suite = pyperf.BenchmarkSuite.loads(s)
    if args.compare_to:
        with args.compare_to as f:
            compare_to = f.read()
        compare_to = pyperf.BenchmarkSuite.loads(compare_to)

    if args.compare_to:
        pfx1 = args.versions[0]
        pfx2 = args.versions[1]
        df = suite_to_benchmark_data(args, suite, version=pfx1)
        df2 = suite_to_benchmark_data(args, compare_to, version=pfx2)
        df = df2.append(df)
        outdir_basename = '%s_vs_%s' % (pfx1, pfx2)
    else:
        df = suite_to_benchmark_data(args, suite)
        outdir_basename = os.path.basename(args.input.name)
        outdir_basename, _ = os.path.splitext(outdir_basename)

    # Convert seconds to milliseconds
    df['duration'] = df['duration'] * 1000.0

    if args.input.name == '<stdin>':
        outdir = tempfile.mkdtemp()
    else:
        outdir_parent = os.path.dirname(args.input.name)
        outdir = os.path.join(outdir_parent, 'images', outdir_basename)
        os.makedirs(outdir, exist_ok=True)
    print("Saving images to", outdir)


    matplotlib.rcParams["figure.figsize"] = 10, 5
    seaborn.set(style="white")

    save_all(df, outdir, args.versions)


if __name__ == "__main__":
    main()
