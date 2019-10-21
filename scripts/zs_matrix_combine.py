#!/usr/bin/env python3
# Written in 2019 by Jason Madden <jason@nextthhought.com>
# and placed in the public domain.
"""
Given the path to a time-qualified directory as produced by matrix.py,
find all fully-qualified output files (those containing the database name)
across concurrency and object count levels, and combine them into a single file.

This is useful for producing the output to compare across different branches.

Example::
    combine_matrix.py relstorage38/master/157159342
"""
import subprocess
import sys
import shutil
import os

dir_name = sys.argv[1]

ds = os.listdir(dir_name)

first = os.path.join(dir_name, ds.pop())
shutil.copy(os.path.join(first, 'output.json'), '/tmp/output.json')

for f in ds:
    if not os.path.isdir(os.path.join(dir_name, f)):
        print("Skipping", os.path.join(dir_name, f))
        continue
    f = os.path.join(dir_name, f, 'output.json')
    if not os.path.exists(f):
        print("Skipping", f)
        continue
    try:
        os.unlink('/tmp/convert.json')
    except FileNotFoundError:
        pass
    subprocess.call([
        sys.executable, '-m', 'pyperf',
        'convert',
        '-o', '/tmp/convert.json',
        '--add', f,
        '/tmp/output.json'
    ])

    shutil.copy('/tmp/convert.json', '/tmp/output.json')


shutil.copy('/tmp/convert.json', os.path.join(dir_name, 'combined.json'))
