#!/usr/bin/env python
"""
Functions for processing output files produced by nested sampling software.
Currently compatable with MultiNest and PolyChord output.

Nestcheck's diagnostics require infomation about the steps at which points were
sampled in order to split nested sampling runs into their constituent threads
(single live point runs). See "Sampling Errors In Nested Sampling Parameter
Estimation (Higson et al. 2017) for more details. *Producing these requires
MultiNest >= v3.11 and PolyChord >= v1.13.*


**nestested sampling run format**

nestcheck stored nested sampling runs in a standard format as python
dictionaries. For a run with nsamp samples, the keys are:

    logl: 1d numpy array
        Log-likelihood values (floats) for each sample.
        Shape is (nsamp,).
    thread_labels: 1d numpy array
        Int representing which thread each point belongs to.
        For some thread label k, the thread's start (birth) log-likelihood
        and end log-likelihood are given by thread_min_max[k, :].
        Shape is (nsamp,).
    thread_min_max: 2d numpy array
        Shape is (# threads, 2).
        Each row k contains min logl (birth contour) and max logl for
        thread with thread label i.
    theta: 2d numpy array
        Parameter values for samples - each row represents a sample.
        Shape is (nsamp, d) where d is number of dimensions.
    nlive_array: 1d numpy array
        Number of live points present between the previous point and
        this point.
    output: dict (optional)
        Dict containing extra information about the run.

Samples are arranged in ascending order of logl.
"""

import os
import re
import warnings
import numpy as np
import nestcheck.io_utils
import nestcheck.parallel_utils


@nestcheck.io_utils.save_load_result
def batch_process_data(file_roots, **kwargs):
    """
    Process output from many nested sampling runs in parallel with optional
    error handling and caching.

    The result can be cached usin the 'save_name', 'save' and 'load' kwargs (by
    default this is not done). See save_load_result docstring for more details.

    Remaining kwargs passed to parallel_utils.parallel_apply (see its
    docstring for more details).

    Parameters
    ----------
    file_roots: list of strs
        file_roots for the runs to load.
    base_dir: str, optional
        path to directory containing files.
    process_func: function, optional
        function to use to process the data.
    func_kwargs: dict, optional
        additional keyword arguments for process_func.
    errors_to_handle: error or tuple of errors, optional
        which errors to catch when they occur in processing rather than
        raising.
    save_name: str or None, optional
        See nestcheck.io_utils.save_load_result.
    save: bool, optional
        See nestcheck.io_utils.save_load_result.
    load: bool, optional
        See nestcheck.io_utils.save_load_result.
    overwrite_existing: bool, optional
        See nestcheck.io_utils.save_load_result.

    Returns
    -------
    list of ns_run dicts
        List of nested sampling runs in dict format (see module docstring for
        more details).
    """
    base_dir = kwargs.pop('base_dir', 'chains')
    process_func = kwargs.pop('process_func', process_polychord_run)
    func_kwargs = kwargs.pop('func_kwargs', {})
    func_kwargs['errors_to_handle'] = kwargs.pop('errors_to_handle', ())
    data = nestcheck.parallel_utils.parallel_apply(
        process_error_helper, file_roots, func_args=(base_dir, process_func),
        func_kwargs=func_kwargs, **kwargs)
    # Sort processed runs into the same order as file_roots (as parallel_apply
    # does not preserve order)
    data = sorted(data,
                  key=lambda x: file_roots.index(x['output']['file_root']))
    # Extract error information and print
    errors = {}
    for i, run in enumerate(data):
        if 'error' in run:
            try:
                errors[run['error']].append(i)
            except KeyError:
                errors[run['error']] = [i]
    for error_name, index_list in errors.items():
        message = (error_name + ' processing ' + str(len(index_list)) + ' / '
                   + str(len(file_roots)) + ' files')
        if len(index_list) != len(file_roots):
            message += '. Roots with errors have indexes: ' + str(index_list)
        print(message)
    # Return runs which did not have errors
    return [run for run in data if 'error' not in run]


def process_error_helper(root, base_dir, process_func, errors_to_handle=(),
                         **func_kwargs):
    """
    Wrapper which applies process_func and handles some common errors so one
    bad run does not spoil the whole batch.

    Useful errors to handle include:

    OSError: if you are not sure if all the files exist
    AssertionError: if some of the many assertions fail for known reasons;
    for example is there is an occasional non-unique logl due to limited
    numerical precision.

    Parameters
    ----------
    root: str
        File root.
    base_dir: str
        Directory containing file.
    process_func: func
        Function for processing file.
    errors_to_handle: error type or tuple of error types
        Errors to catch without throwing an exception.
    func_kwargs: dict
        Kwargs to pass to process_func.

    Returns
    -------
    run: dict
        Nested sampling run dict (see module docstring for more details) or, if
        an error occured, a dict containing its type and the file root.
    """
    try:
        return process_func(root, base_dir, **func_kwargs)
    except errors_to_handle as err:
        run = {'error': type(err).__name__,
               'output': {'file_root': root}}
        return run


def process_polychord_run(file_root, base_dir, logl_warn_only=False,
                          process_stats_file=True):
    """
    Loads data from a PolyChord run into the nestcheck dictionary format for
    analysis.

    N.B. producing required output file containing information about the
    iso-likelihood contours within which points were sampled (where they were
    "born") requies PolyChord version v1.13 or later and the setting
    write_dead=True.

    Parameters
    ----------
    file_root: str
        Root for run output file names (PolyChord file_root setting).
    base_dir: str
        Directory containing data (PolyChord base_dir setting).
    logl_warn_only: bool, optional
        Whether only a warning should be given (rather than an assertion error)
        should be given if there are non-unique logls in the file.
        Passed to check_ns_run (see its docs for more details).
    process_stats_file: bool, optional
        Should PolyChord's <root>.stats file be processed? Set to False if you
        don't have the <root>.stats file (such as if PolyChord was run with
        write_stats=False).

    Returns
    -------
    ns_run: dict
        Nested sampling run dict (see module docstring for more details).
    """
    # N.B. PolyChord dead points files also contains remaining live points at
    # termination
    samples = np.loadtxt(base_dir + '/' + file_root + '_dead-birth.txt')
    ns_run = process_samples_array(samples)
    ns_run['output'] = {'base_dir': base_dir, 'file_root': file_root}
    if process_stats_file:
        try:
            ns_run['output'] = process_polychord_stats(file_root, base_dir)
        except (OSError, IOError) as err:
            warnings.warn((
                'process_polychord_stats raised ' + type(err).__name__
                + ' processing ' + base_dir + '/' + file_root + '.stats file.'
                + ' Proceeding without it.'), UserWarning)
    check_ns_run(ns_run, logl_warn_only=logl_warn_only)
    return ns_run


def process_multinest_run(file_root, base_dir, logl_warn_only=False):
    """
    Loads data from a MultiNest run into the nestcheck dictionary format for
    analysis.

    N.B. producing required output file containing information about the
    iso-likelihood contours within which points were sampled (where they were
    "born") requies MultiNest version 3.11 or later.

    Parameters
    ----------
    file_root: str
        Root name for output files. When running MultiNest, this is determined
        by the nest_root parameter.
    base_dir: str
        Directory containing output files. When running MultiNest, this is
        determined by the nest_root parameter.
    logl_warn_only: bool, optional
        Whether only a warning should be given (rather than an assertion error)
        should be given if there are non-unique logls in the file.
        Passed to check_ns_run (see its docs for more details).

    Returns
    -------
    ns_run: dict
        Nested sampling run dict (see module docstring for more details).
    """
    # Load dead and live points
    dead = np.loadtxt(base_dir + '/' + file_root + '-dead-birth.txt')
    live = np.loadtxt(base_dir + '/' + file_root + '-phys_live-birth.txt')
    # Remove unnessesary final columns
    dead = dead[:, :-2]
    live = live[:, :-1]
    assert dead[:, -2].max() < live[:, -2].min(), (
        'final live points should have greater logls than any dead point!',
        dead, live)
    ns_run = process_samples_array(np.vstack((dead, live)))
    assert np.all(ns_run['thread_min_max'][:, 0] == -np.inf), (
        'As MultiNest does not currently perform dynamic nested sampling, all '
        'threads should start by sampling the whole prior.')
    ns_run['output'] = {}
    ns_run['output']['file_root'] = file_root
    ns_run['output']['base_dir'] = base_dir
    check_ns_run(ns_run, logl_warn_only=logl_warn_only)
    return ns_run


def process_polychord_stats(file_root, base_dir):
    """
    Reads a PolyChord <root>.stats output file and returns the information
    contained in a dictionary.

    Parameters
    ----------
    file_root: str
        Root for run output file names (PolyChord file_root setting).
    base_dir: str
        Directory containing data (PolyChord base_dir setting).

    Returns
    -------
    output: dict
        See PolyChord documentation for more details.
    """
    filename = os.path.join(base_dir, file_root) + '.stats'
    output = {'base_dir': base_dir,
              'file_root': file_root}
    with open(filename, 'r') as stats_file:
        lines = stats_file.readlines()
    output['logZ'] = float(lines[8].split()[2])
    output['logZerr'] = float(lines[8].split()[4])
    output['logZs'] = []
    output['logZerrs'] = []
    for line in lines[14:]:
        if line[:5] != 'log(Z':
            break
        output['logZs'].append(float(
            re.findall(r'=(.*)', line)[0].split()[0]))
        output['logZerrs'].append(float(
            re.findall(r'=(.*)', line)[0].split()[2]))
    output['ncluster'] = len(output['logZs'])
    output['nposterior'] = int(lines[-6].split()[1])
    output['nequals'] = int(lines[-5].split()[1])
    output['ndead'] = int(lines[-4].split()[1])
    output['nlive'] = int(lines[-3].split()[1])
    output['nlike'] = int(lines[-2].split()[1])
    output['avnlike'] = float(lines[-1].split()[1])
    output['avnlikeslice'] = float(lines[-1].split()[3])
    return output


def process_samples_array(samples):
    """
    Convert an array of nested sampling dead and live points of the type
    produced by PolyChord and MultiNest into a nestcheck nested sampling run
    dictionary.

    Parameters
    ----------
    samples: 2d numpy array
        Array of dead points and any remaining live points at termination.
        Has #parameters + 2 columns:
        param_1, param_2, ... , logl, birth_logl

    Returns
    -------
    ns_run: dict
        Nested sampling run dict (see module docstring for more details). Only
        contains information in samples (not additional optional output
        key).
    """
    samples = samples[np.argsort(samples[:, -2])]
    ns_run = {}
    ns_run['logl'] = samples[:, -2]
    repeat_logls = (ns_run['logl'].shape[0] -
                    np.unique(ns_run['logl']).shape[0])
    assert repeat_logls == 0, (
        '# unique logl values is ' + str(repeat_logls) + ' less than #point')
    ns_run['theta'] = samples[:, :-2]
    birth_contours = samples[:, -1]
    ns_run['thread_labels'] = threads_given_birth_contours(
        ns_run['logl'], birth_contours)
    unique_threads = np.unique(ns_run['thread_labels'])
    assert np.array_equal(unique_threads,
                          np.asarray(range(unique_threads.shape[0])))
    # Work out nlive_array and thread_min_max logls from thread labels and
    # birth contours
    thread_min_max = np.zeros((unique_threads.shape[0], 2))
    # NB delta_nlive indexes are offset from points' indexes by 1 as we need an
    # element to represent the initial sampling of live points before any dead
    # points are created.
    # I.E. birth on step 1 corresponds to replacing dead point zero
    delta_nlive = np.zeros(samples.shape[0] + 1)
    for label in unique_threads:
        inds = np.where(ns_run['thread_labels'] == label)[0]
        # Max is final logl in thread
        thread_min_max[label, 1] = ns_run['logl'][inds[-1]]
        birth_logl = birth_contours[inds[0]]
        # delta nlive indexes are +1 from logl indexes to allow for initial
        # nlive (before first dead point)
        delta_nlive[inds[-1] + 1] -= 1
        if birth_logl == birth_contours[0]:
            # thread minimum is -inf as it starts by sampling from whole prior
            thread_min_max[label, 0] = -np.inf
            delta_nlive[0] += 1
        else:
            thread_min_max[label, 0] = birth_logl
            birth_ind = np.where(ns_run['logl'] == birth_logl)[0]
            assert birth_ind.shape == (1,)
            delta_nlive[birth_ind[0] + 1] += 1
    ns_run['thread_min_max'] = thread_min_max
    ns_run['nlive_array'] = np.cumsum(delta_nlive)[:-1]
    return ns_run


def threads_given_birth_contours(logl, birth_logl):
    """
    Divides a nested sampling run into threads, using info on the contours at
    which points were sampled. See "Sampling errors in nested sampling
    parameter estimation" (Higson et al. 2017) for more information.

    MultiNest and PolyChord use different values to identify the inital live
    points which were sampled from the whole prior (PolyChord uses -1e+30
    and MultiNest -0.179769313486231571E+309). However in each case the first
    dead point must have been sampled from the whole prior, so for either
    package we can use

    init_birth = birth_logl[0]

    Parameters
    ----------
    logl: 1d numpy array
        logl values of each point.
    birth_logl: 1d numpy array
        logl values of the iso-likelihood contour from within each point was
        sampled (on which it was born).

    Returns
    -------
    thread_labels: 1d numpy array of ints
        labels of the thread each point belongs to.
    """
    init_birth = birth_logl[0]
    for i, birth in enumerate(birth_logl):
        assert birth < logl[i], str(birth) + ' ' + str(logl[i])
        assert birth == init_birth or np.where(logl == birth)[0].shape == (1,)
    unique, counts = np.unique(birth_logl[np.where(birth_logl != init_birth)],
                               return_counts=True)
    thread_start_logls = np.concatenate((np.asarray([init_birth]),
                                         unique[np.where(counts > 1)]))
    thread_start_counts = np.concatenate(
        (np.asarray([(birth_logl == init_birth).sum()]),
         counts[np.where(counts > 1)] - 1))
    thread_labels = np.full(logl.shape, np.nan)
    thread_num = 0
    for nmulti, multi in enumerate(thread_start_logls):
        for i, start_ind in enumerate(np.where(birth_logl == multi)[0]):
            # unless nmulti=0 the first point born on the contour (i=0) is
            # already assigned to a thread
            if i != 0 or nmulti == 0:
                # check point has not already been assigned
                assert np.isnan(thread_labels[start_ind])
                thread_labels[start_ind] = thread_num
                # find the point which replaced it
                next_ind = np.where(birth_logl == logl[start_ind])[0]
                while next_ind.shape != (0,):
                    # check point has not already been assigned
                    assert np.isnan(thread_labels[next_ind[0]])
                    thread_labels[next_ind[0]] = thread_num
                    # find the point which replaced it
                    next_ind = np.where(birth_logl == logl[next_ind[0]])[0]
                thread_num += 1
    assert np.all(~np.isnan(thread_labels)), \
        ('Some points were not given a thread label! Indexes=' +
         str(np.where(np.isnan(thread_labels))[0]) +
         '\nlogls on which threads start are:' +
         str(thread_start_logls) + ' with num of threads starting on each: ' +
         str(thread_start_counts) +
         '\nthread_labels =' + str(thread_labels))
    assert np.array_equal(thread_labels, thread_labels.astype(int)), \
        'Thread labels should all be ints!'
    thread_labels = thread_labels.astype(int)
    # Check unique thread labels are a sequence from 0 to nthreads-1
    assert np.array_equal(
        np.unique(thread_labels),
        np.asarray(range(sum(thread_start_counts)))), (
            str(np.unique(thread_labels)) + ' is not equal to range('
            + str(sum(thread_start_counts)) + ')')
    return thread_labels


# Functions for checking nestcheck format nested sampling run dictionaries to
# ensure they have the expected properties.


def check_ns_run(run, logl_warn_only=False):
    """
    Checks a nestcheck format nested sampling run dictionary has the expected
    properties (see the module docstring for more details).

    Parameters
    ----------
    run: dict
        nested sampling run to check.
    logl_warn_only: bool, optional
        Whether only a warning should be given (rather than an assertion error)
        should be given if there are non-unique logls in the file.


    Raises
    ------
    AssertionError
        if run does not have expected properties.
    """
    assert isinstance(run, dict)
    check_ns_run_members(run)
    check_ns_run_logls(run, warn_only=logl_warn_only)
    check_ns_run_threads(run)


def check_ns_run_members(run):
    """
    Check nested sampling run member keys and values.

    Parameters
    ----------
    run: dict
        nested sampling run to check.

    Raises
    ------
    AssertionError
        if run does not have expected properties.
    """
    run_keys = list(run.keys())
    # Mandatory keys
    for key in ['logl', 'nlive_array', 'theta', 'thread_labels',
                'thread_min_max']:
        assert key in run_keys
        run_keys.remove(key)
    # Optional keys
    for key in ['output']:
        try:
            run_keys.remove(key)
        except ValueError:
            pass
    # Check for unexpected keys
    assert not run_keys, 'Unexpected keys in ns_run: ' + str(run_keys)
    # Check type of mandatory members
    for key in ['logl', 'nlive_array', 'theta', 'thread_labels',
                'thread_min_max']:
        assert isinstance(run[key], np.ndarray), (
            key + ' is type ' + type(run[key]).__name__)
    # check shapes of keys
    assert run['logl'].ndim == 1
    assert run['logl'].shape == run['nlive_array'].shape
    assert run['logl'].shape == run['thread_labels'].shape
    assert run['theta'].ndim == 2
    assert run['logl'].shape[0] == run['theta'].shape[0]


def check_ns_run_logls(run, warn_only=False):
    """
    Check run logls are unique and in the correct order.

    Parameters
    ----------
    run: dict
        nested sampling run to check.

    Raises
    ------
    AssertionError
        if run does not have expected properties.
    """
    assert np.array_equal(run['logl'], run['logl'][np.argsort(run['logl'])])
    logl_u, counts = np.unique(run['logl'], return_counts=True)
    repeat_logls = run['logl'].shape[0] - logl_u.shape[0]
    if repeat_logls != 0:
        msg = ('# unique logl values is ' + str(repeat_logls) +
               ' less than # points. Duplicate values: ' +
               str(logl_u[np.where(counts > 1)[0]]))
        if logl_u.shape[0] != 1:
            msg += (
                ', Counts: ' + str(counts[np.where(counts > 1)[0]]) +
                ', First point at inds ' +
                str(np.where(run['logl'] == logl_u[np.where(
                    counts > 1)[0][0]])[0])
                + ' out of ' + str(run['logl'].shape[0]))
    if not warn_only:
        assert repeat_logls == 0, msg
    else:
        if repeat_logls != 0:
            warnings.warn(msg, UserWarning)


def check_ns_run_threads(run):
    """
    Check thread labels and thread_min_max have expected properties.

    Parameters
    ----------
    run: dict
        Nested sampling run to check.

    Raises
    ------
    AssertionError
        If run does not have expected properties.
    """
    assert run['thread_labels'].dtype == int
    uniq_th = np.unique(run['thread_labels'])
    assert np.array_equal(
        np.asarray(range(run['thread_min_max'].shape[0])), uniq_th), \
        str(uniq_th)
    # Check thread_min_max
    assert np.any(run['thread_min_max'][:, 0] == -np.inf), (
        'Run should have at least one thread which starts by sampling the ' +
        'whole prior')
    for th_lab in uniq_th:
        inds = np.where(run['thread_labels'] == th_lab)[0]
        th_info = (str(th_lab) + ', ' + str(run['logl'][inds[0]]),
                   str(run['thread_min_max'][th_lab, :]))
        assert run['thread_min_max'][th_lab, 0] < run['logl'][inds[0]], (
            'First point in thread has logl less than thread min logl! ' +
            th_info)
        assert run['thread_min_max'][th_lab, 1] == run['logl'][inds[-1]], (
            'Last point in thread logl != thread end logl! ' + th_info)
