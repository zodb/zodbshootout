#!/usr/bin/env python3
"""
Parses the output from a single zodbshootout run or the combined
results of a matrix and produces a CSV.

"""
# Written in 2019 by Jason Madden <jason@nextthhought.com>
# and placed in the public domain.

import argparse
import csv
import json

import pyperf

def _fix_database(n):
    if 'mysql' in n.lower():
        return 'MySQL'
    if 'psycopg2' in n.lower():
        return 'PostgreSQL'
    if 'zeo' in n.lower():
        return 'ZEO'
    return n

def export_csv(args, benchmark_suite):
    rows = []
    fields = ('ShortLabel', 'LongLabel',
              'Database', 'Concurrency', 'ConcurrencyKind', 'Objects',
              'Action',
              'Mean', )
    for benchmark in benchmark_suite.get_benchmarks():
        row = {}
        # {c=1 processes, o=100} mysqlclient_hf: read 100 hot objects'
        name = benchmark.get_name()
        # '{c=1 processes, o=100', ' mysqlclient_hf: read 100 hot objects'
        prefix, suffix = name.rsplit('}', 1)
        row['ConcurrencyKind'] = 'processes' if 'processes' in prefix else 'threads'

        prefix = prefix.replace(' processes', '').replace(' threads', '')
        prefix += '}'
        d = json.loads(prefix.replace('c', '"c"').replace('o', '"o"').replace('=', ':'))
        row['Concurrency'] = d['c']
        row['Objects'] = d['o']

        row['Database'], suffix = suffix.strip().split(':', 1)
        suffix = suffix.strip()
        row['Database'] = _fix_database(row['Database'])
        row['Action'] = suffix.replace(str(row['Objects']) + ' ', '')

        row['Mean'] = benchmark.mean()

        row['ShortLabel'] = f'{row["Database"]}: {row["Concurrency"]} {row["ConcurrencyKind"]}'
        row['LongLabel'] = f'{row["ShortLabel"]}: {suffix}'

        rows.append(row)

    rows.sort(key=lambda row: (row['Database'], row['Concurrency'], row['ConcurrencyKind'],
                               row['Objects']))
    with args.output:
        writer = csv.DictWriter(args.output, fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=argparse.FileType('r'))
    parser.add_argument('output', type=argparse.FileType('w'))
    return parser.parse_args()


def main():
    args = parse_args()
    with args.input as input:
        s = input.read()
    suite = pyperf.BenchmarkSuite.loads(s)
    export_csv(args, suite)


if __name__ == "__main__":
    main()
