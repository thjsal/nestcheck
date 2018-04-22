#!/usr/bin/env python
"""
Utilities for testing, including creating dummy nested sampling run data.
"""

import os
import numpy as np
import nestcheck.ns_run_utils


def write_dummy_polychord_stats(file_root, base_dir):
    """
    Writes a dummy PolyChord format .stats file for tests functions for
    processing stats files. This is written to:

    base_dir/file_root.stats

    Also returns the data in the file as a dict for comparison.

    Parameters
    ----------
    file_root: str
        Root for run output file names (PolyChord file_root setting).
    base_dir: str
        Directory containing data (PolyChord base_dir setting).

    Returns
    -------
    output: dict
        The expected output of
        nestcheck.process_polychord_stats(file_root, base_dir)
    """
    output = {'file_root': file_root,
              'base_dir': base_dir,
              'logZ': -9.99,
              'logZerr': 1.11,
              'logZs': [-8.88, -7.77, -6.66, -5.55],
              'logZerrs': [1.88, 1.77, 1.66, 1.55],
              'nposterior': 0,
              'nequals': 0,
              'ndead': 1234,
              'nlike': 123456,
              'nlive': 0,
              'avnlike': 100.0,
              'avnlikeslice': 10.0}
    output['ncluster'] = len(output['logZs'])
    # Make a PolyChord format .stats file corresponding to output
    file_lines = [
        'Evidence estimates:',
        '===================',
        '  - The evidence Z is a log-normally distributed ...',
        '  - We denote this as log(Z) = mu +/- sigma.',
        '',
        'Global evidence:',
        '----------------',
        '',
        'log(Z)       =  {0} +/-   {1}'.format(
            output['logZ'], output['logZerr']),
        '',
        '',
        'Local evidences:',
        '----------------',
        '']
    for i, (lz, lzerr) in enumerate(zip(output['logZs'], output['logZerrs'])):
        file_lines.append('log(Z_ {0})  =  {1} +/-   {2}'.format(
            str(i + 1).rjust(2), lz, lzerr))
    file_lines += [
        '',
        '',
        'Run-time information:',
        '---------------------',
        '',
        ' ncluster:          0 /       1',
        ' nposterior:        {0}'.format(output['nposterior']),
        ' nequals:           {0}'.format(output['nequals']),
        ' ndead:          {0}'.format(output['ndead']),
        ' nlive:             {0}'.format(output['nlive']),
        ' nlike:         {0}'.format(output['nlike']),
        ' <nlike>:       {0}   (    {1} per slice )'.format(
            output['avnlike'], output['avnlikeslice'])]
    with open(os.path.join(base_dir, file_root) + '.stats', 'w') as stats_file:
        stats_file.writelines('{}\n'.format(line) for line in file_lines)
    return output


def get_dummy_ns_run(nlive, nsamples, ndim, seed=False):
    """Generate template ns runs for quick testing without loading test
    data."""
    threads = []
    if seed is not False:
        np.random.seed(seed)
    for _ in range(nlive):
        threads.append(get_dummy_ns_thread(nsamples, ndim, seed=False))
    return nestcheck.ns_run_utils.combine_ns_runs(threads)


def get_dummy_ns_thread(nsamples, ndim, seed=False, logl_start=-np.inf):
    """Generate a single ns thread for quick testing without loading test
    data."""
    if seed is not False:
        np.random.seed(seed)
    thread = {'logl': np.sort(np.random.random(nsamples)),
              'nlive_array': np.full(nsamples, 1.),
              'theta': np.random.random((nsamples, ndim)),
              'thread_labels': np.zeros(nsamples).astype(int)}
    if logl_start != -np.inf:
        thread['logl'] += logl_start
    thread['thread_min_max'] = np.asarray([[logl_start, thread['logl'][-1]]])
    return thread


def get_dummy_dead_points(ndims=2, nsamples=10, dynamic=True):
    """
    Make a dead points array of the type produced by PolyChord and MultiNest.
    Also returns the same nested sampling run as a dictionary in the standard
    nestcheck format for checking.
    """
    threads = [get_dummy_ns_thread(nsamples, ndims, seed=False,
                                   logl_start=-np.inf)]
    if dynamic:
        threads.append(get_dummy_ns_thread(nsamples, ndims, seed=False,
                                           logl_start=threads[0]['logl'][0]))
        # to make sure thread labels derived from the dead points match the
        # order in threads, we need to make sure the first point after the
        # contour where 2 points are born (threads[0]['logl'][0]) is in
        # threads[0] not threads[-1]. Hence add to threads[-1]['logl']
        threads[-1]['logl'] += 1
        threads[-1]['thread_min_max'][0, 1] += 1
    else:
        threads.append(get_dummy_ns_thread(nsamples, ndims, seed=False,
                                           logl_start=-np.inf))
        # Make threads are in order such that combine_runs will assign same
        # thread labels as data processing. I.e. we need the first thread
        # (which will be labeled 0) to also have the dead point with the lowest
        # likelihood (which starts the zeroth thread in data procesing funcs).
        threads = sorted(threads, key=lambda th: th['logl'][0])
    threads[-1]['thread_labels'] += 1
    dead_arrs = []
    for th in threads:
        dead = np.zeros((nsamples, ndims + 2))
        dead[:, :ndims] = th['theta']
        dead[:, ndims] = th['logl']
        dead[1:, ndims + 1] = th['logl'][:-1]
        if th['thread_min_max'][0, 0] == -np.inf:
            dead[0, ndims + 1] = -1e30
        else:
            dead[0, ndims + 1] = th['thread_min_max'][0, 0]
        dead_arrs.append(dead)
    dead = np.vstack(dead_arrs)
    dead = dead[np.argsort(dead[:, ndims]), :]
    run = nestcheck.ns_run_utils.combine_threads(threads)
    return dead, run