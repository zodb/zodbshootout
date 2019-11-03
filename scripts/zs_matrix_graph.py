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
    if 'sqlite' in n.lower():
        result = 'SQLite'
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
        if 'processes' in prefix:
            ConcurrencyKind = 'processes'
        elif 'greenlets' in prefix:
            ConcurrencyKind = 'greenlets'
        else:
            assert 'threads' in prefix
            ConcurrencyKind = 'threads'


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


def save_one(df, benchmark_name, outdir, palette=None,
             # The x and y are for an individual graph in the matrix
             x="concurrency", y="duration",
             # The col and row define the columns and rows of the matrix
             col="objects", row="concurrency_kind",
             # while hue defines the category within an individual graph.
             hue="database", hue_order=None,
             show_y_ticks=False, kind="bar",
             **kwargs):

    fname = benchmark_name.replace(' ', '_').replace('/', '_').replace(':', '_')
    fname = f'{fname}_{x}_{y}_{col}_{row}_{hue}_{kind}'

    fig = seaborn.catplot(
        x, y,
        data=df,
        #kind="swarm", # The swarm plots is also interesting, as is point
        kind=kind,
        hue=hue,
        hue_order=sorted(df[hue].unique()) if hue_order is None else hue_order,
        col=col,
        row=row,
        palette=palette,
        sharey=False,
        legend=False,
        **kwargs
    )
    if not show_y_ticks:
        fig.set(yticks=[])
    else:
        fig.set(ylabel="ms")
    fig.add_legend(title=benchmark_name)
    for ext in ('.png',):
        fig.savefig(os.path.join(outdir, fname) + ext, transparent=True)
    fig.despine()
    plt.close(fig.fig)

def save_all(df, outdir, versions=None, pref_db_order=None):
    all_bmarks = df['action'].unique()

    # The drawing functions use Cocoa and don't work on either threads
    # or processes.
    # pool = ProcessPoolExecutor()
    # def _save_one(*args, **kwargs):
    #     pool.submit(save_one, *args, **kwargs)
    _save_one = save_one

    for bmark in all_bmarks:
        action_data = df[df.action == bmark]
        action_data = action_data[action_data.concurrency_kind != "greenlets"]
        _save_one(
            action_data, bmark, outdir,
            palette='Paired' if versions or pref_db_order else None,
            hue_order=pref_db_order,
        )


    if versions:
        all_dbs_including_versions = df['database'].unique()
        all_dbs = {
            db.replace('(' + versions[0] + ')', '').replace(
                '(' + versions[1] + ')', ''
            ).strip()
            for db in all_dbs_including_versions
        }

        parent_out_dir = outdir
        for root_db in all_dbs:
            outdir = os.path.join(parent_out_dir, root_db)
            os.makedirs(outdir, exist_ok=True)
            db_v1 = f"{root_db} ({versions[0]})"
            db_v2 = f"{root_db} ({versions[1]})"
            db_df = df[df.database == db_v1]
            db_df2 = df[df.database == db_v2]
            db_df = db_df.append(db_df2)

            for bmark in all_bmarks:
                # adf: By database, by action
                adf = db_df[db_df.action == bmark]
                _save_one(
                    adf.query('concurrency > 1'),
                    f"{root_db}: {bmark}",
                    outdir,
                    x="concurrency_kind",
                    hue="database",
                    row="concurrency",
                    palette='Paired',
                )
                # This puts all three concurrencies together
                # and emphasizes the differences between them.

                _save_one(
                    adf.query('concurrency > 1'),
                    f"{root_db}: {bmark}",
                    outdir,
                    x="database",
                    hue="concurrency_kind",
                    row="concurrency",
                    palette='Accent',
                    order=sorted((db_v1, db_v2)),
                )

                cdf = adf[adf.objects == 20]
                try:
                    _save_one(
                        cdf,
                        f"{root_db}: {bmark} | objects = 20",
                        outdir,
                        palette='Paired',
                        col="concurrency_kind", row="objects",
                    )
                except ValueError:
                    continue


                for ck in adf['concurrency_kind'].unique():
                    ckf = adf[adf.concurrency_kind == ck]
                    # ckf: drilldown by database, by action, by concurrency kind.
                    for oc in ckf['objects'].unique():
                        ocf = ckf[ckf.objects == oc]
                        _save_one(
                            ocf,
                            f"{root_db}: {bmark} ck={ck} o={oc}",
                            outdir,
                            palette='Paired',
                            col="concurrency_kind", row="objects",
                            show_y_ticks=True,
                        )


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


    matplotlib.rcParams["figure.figsize"] = 20, 10
    seaborn.set(style="white")

    save_all(df, outdir, args.versions)


if __name__ == "__main__":
    main()
