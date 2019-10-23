#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Written in 2019 by Jason Madden <jason@nextthhought.com>
# and placed in the public domain.
"""
Run zodbshootout across a matrix of different configurations,
producing output files for comparison.

Run from a git checkout of RelStorage or set the GIT_BRANCH
environment variable. Relies on virtualenvwrapper.

Once this is done you may compare individual output directly, or
individual databases directly, but most likely the
``zs_matrix_combine`` script should be used to produce an aggregated
file for visualizing with ``zs_matrix_graph`` or exporting with
``zs_matrix_export_csv``.
"""

import os
import subprocess
import time
import traceback
import tempfile

# The type of runner to enable, and the arguments needed
# to use it.
procs = {
    'process': (),
    'gevent':  ('--threads', 'shared', '--gevent'),
    'threads': ('--threads', 'shared'),
}

# The concurrency levels.
# TODO: thread=1 and process=1 are pretty much redundant.
concs = [
    1,
    5,
    10
]

# How many objects
counts = [
    1,
    100,
    1000
]

# The virtual environment to use.
# Relies on virtualenvwrapper.
envs = [
    'relstorage38',
    'relstorage27',
]

workon_home = os.environ['WORKON_HOME']
results_home = os.environ.get(
    'ZS_MATRIX_RESULTS',
    '~/Projects/GithubSources/zodbshootout-results'
)

# General configuration for the zodbshootout runs.
values = 3
warmups = 0
min_time_ms = 50.0 # Default is 100ms

branch = os.environ['GIT_BRANCH']

# Set this if you restart a run after fixing a problem
# and edit out entries in the matrix that already completed.
now = os.environ.get('ZS_NOW', '') or int(time.time())

def run_one(env, proc, conc, count):
    out_dir = os.path.expanduser(
        f"{results_home}/{env}/{branch}/"
        f"{now}/{proc[0]}-c{conc}-o{count}/"
    )

    os.makedirs(out_dir, exist_ok=True)

    # Each process (-p) runs --loops for --values times.
    # Plus the initial calibration, which is always at least two
    # values (begin at 1 loop and go up until you get two consecutive
    # runs with the same loop count > --min-time)

    print("***", env, proc, conc, count)
    output_path = os.path.join(out_dir, "output.json")
    if os.path.exists(output_path):
        print("\t", output_path, "Already exists, skipping")
    cmd = [
        os.path.expanduser(f"{workon_home}/{env}/bin/zodbshootout"),
        '-q',
        '--include-mapping', "no",
        '--zap', 'force',
        '--min-time', str(min_time_ms / 1000.0),
        # '--loops', '5', # let it auto-calibrate using min-time
        '--values', str(values),
        '--warmups', str(warmups),
        '-p', '5',
        '-o', output_path,
        '-c', str(conc),
        '--object-counts', str(count)
    ]

    cmd.extend(proc[1])
    cmd.append(os.path.expanduser("~/Projects/GithubSources/zodbshootout-results/zodb3.conf"))

    # Set these to only run a subset of the benchmarks.

    # cmd.extend([
    #     "add",
    #     "store",
    #     "update",
    #     "cold",
    #     "conflicts",
    #     "tpc",
    #     "im_commit",
    # ])

    print("\t", ' '.join(cmd))

    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError:
        traceback.print_exc()
        if os.path.exists(output_path):
            fd, path = tempfile.mkstemp('.json', 'output-failed-', out_dir)
            os.close(fd)
            os.rename(output_path, path)
    print("***")
    print()

def main():
    if 1 in concs and 'process' and 'threads' in procs:
        # This is redundant.
        del procs['process']

    for env in envs:
        for count in counts:
            for conc in concs:
                for proc in sorted(procs.items()):
                    run_one(env, proc, conc, count)


if __name__ == '__main__':
    main()
