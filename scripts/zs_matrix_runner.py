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

TODO:
- Make this take a configuration (ini) file for which
  options to enable (processes, concurrency levels, counts,
  virtual environments)
- Move this and related scripts into the zodbshootout core.
"""

import os
import subprocess
import sys
import time
import traceback
import tempfile

# The type of runner to enable, and the arguments needed
# to use it.
# TODO: Be intelligent about picking gevent based on the drivers
procs = {
    #'gevent':  ('--threads', 'shared', '--gevent'),
    'process': (),
    'threads': ('--threads', 'shared'),
}

# The concurrency levels.
concs = [
    1,
    5,
    20,
]

# How many objects
counts = [
    1,
    5,
    20
]

# The virtual environment to use.
# Relies on virtualenvwrapper.
envs = [
    #'relstorage38',
    'relstorage27',
    'relstorage27-rs2',
]

if 'ZS_MATRIX_ENV' in os.environ:
    envs = [os.environ['ZS_MATRIX_ENV']]

workon_home = os.environ['WORKON_HOME']
results_home = os.environ.get(
    'ZS_MATRIX_RESULTS',
    '~/Projects/GithubSources/zodbshootout-results'
)

# General configuration for the zodbshootout runs.


branch = os.environ['GIT_BRANCH']

# Set this if you restart a run after fixing a problem
# and edit out entries in the matrix that already completed.
now = os.environ.get('ZS_NOW', '') or int(time.time())

child_env = os.environ.copy()
child_env['PYTHONHASHSEED'] = '6587'
child_env['PYTHONFAULTHANDLER'] = '1'
child_env['ZS_COLLECTOR_FUNC'] = 'avg'
# We don't have the logs enabled anyway, and this shows up in
# profiling.
child_env['RS_PERF_LOG_ENABLE'] = 'off'

child_env.pop('PYTHONDEVMODE', None)
child_env.pop('ZS_NO_SMOOTH', None)

smooth_results_in_process_concurrency = True
if not smooth_results_in_process_concurrency:
    child_env['ZS_NO_SMOOTH'] = '1'


def run_one(
        env, proc, conc, count, conf,
        excluded=(),
        processes=2, # How many times the whole thing is repeated.
        # How many times does the function get to run its loops. If
        # processes * values = 1, then it can't report a standard deviation
        # or print stability warnings.
        values=4,
        warmups=0,
        min_time_ms=50.0, # Default is 100ms
        loops=3 # How many loops (* its inner loops)
):    # pylint:disable=too-many-locals
    if 'pypy' in env:
        values = 10 # Need to JIT

    if conc == 1 and count == 1:
        processes += 2
        min_time_ms = max(min_time_ms, 100.0)

    smooth = 'smoothed' if smooth_results_in_process_concurrency else 'unsmoothed'
    out_dir = os.path.expanduser(
        f"{results_home}/{env}/{branch}/{child_env['ZS_COLLECTOR_FUNC']}/"
        f"{smooth}/"
        f"{now}/{proc[0]}-c{conc}-o{count}-p{processes}-v{values}-l{loops}/"
    )

    os.makedirs(out_dir, exist_ok=True)

    # Each process (-p) runs --loops for --values times.
    # Plus the initial calibration, which is always at least two
    # values (begin at 1 loop and go up until you get two consecutive
    # runs with the same loop count > --min-time). For small counts, it can take a substantial
    # amount of time to calibrate the loop.

    print("***", env, proc, conc, count)
    output_path = os.path.join(out_dir, "output.json")
    if os.path.exists(output_path):
        print("\t", output_path, "Already exists, skipping")
        return
    cmd = [
        os.path.expanduser(f"{workon_home}/{env}/bin/zodbshootout"),
        '-q',
        '--include-mapping', "no",
        '--zap', 'force',
        '--values', str(values),
        '--warmups', str(warmups),
        '-p', str(processes),
        '-o', output_path,
        '-c', str(conc),
        '--object-counts', str(count),
    ]

    if loops and conc > 1 and count > 1:
        cmd.extend(('--loops', str(loops)))
    else:
        cmd.extend(('--min-time', str(min_time_ms / 1000.0)))

    cmd.extend(proc[1])
    cmd.append(conf)

    # Set these to only run a subset of the benchmarks.

    # cmd.extend([
    #     "add",
    #     "store",
    #     "update",
    #     "conflicts",
    #     'warm',
    #     'new_oid',
    # ])

    cmd.extend([
        'add',
        'warm',
        'cold',
    ])

    if excluded:
        cmd.append('--')
        for exc in excluded:
            cmd.append('-' + exc)

    print("\t", ' '.join(cmd))

    try:
        subprocess.check_call(cmd, env=child_env)
    except subprocess.CalledProcessError:
        traceback.print_exc()
        if os.path.exists(output_path):
            fd, path = tempfile.mkstemp('.json', 'output-failed-', out_dir)
            os.close(fd)
            os.rename(output_path, path)
    print("***")
    print()

def main():
    blacklist = set() # {(proc_name, conc)}
    if 1 in concs and 'process' in procs and 'threads' in procs:
        # This is redundant.
        blacklist.add(('process', 1))

    if len(sys.argv) > 1:
        conf = sys.argv[1]
    else:
        conf = "~/Projects/GithubSources/zodbshootout-results/zodb3.conf"
    conf = os.path.abspath(os.path.expanduser(conf))

    for env in envs:
        excluded_bmarks = set()
        for count in sorted(counts):
            for conc in sorted(concs):
                if conc == 1 and len(procs) == 1 and 'gevent' in procs:
                    # If we're only testing one concurrent connection,
                    # and we're only testing gevent by itself, then
                    # the test is unlikely to be interesting. (It might be interesting
                    # to compare gevent to thread or process to see what overhead
                    # the driver adds, but otherwise we want to see how it does
                    # concurrently).
                    continue

                for proc in sorted(procs.items()):
                    if (proc[0], conc) in blacklist:
                        continue
                    run_one(env, proc, conc, count, conf, excluded=excluded_bmarks)
                    # Once we've done these once, they don't really change.
                    # They're independent of count, they don't really even
                    # touch the storage or DB.
                    excluded_bmarks.add('ex_commit')
                    excluded_bmarks.add('im_commit')

if __name__ == '__main__':
    main()
