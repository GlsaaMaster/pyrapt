"""
This module encapsulates the rapt function, which runs a pitch tracker
based on David Talkin's Robust Algorithm for Pitch Tracking (RAPT).
"""

import math
import numpy
from scipy import signal
from scipy.io import wavfile

import raptparams
import nccfparams


def rapt(wavfile_path, **kwargs):
    """
    F0 estimator inspired by RAPT algorithm to determine vocal
    pitch of an audio sample.
    """
    # Process optional keyword args and build out rapt params
    raptparam = _setup_rapt_params(kwargs)

    # TODO: Flesh out docstring, describe args, expected vals in kwargs
    original_audio = _get_audio_data(wavfile_path)

    downsampled_audio = _get_downsampled_audio(original_audio,
                                               raptparam.maximum_allowed_freq)

    original_audio = (original_audio[0], original_audio[1].tolist())
    downsampled_audio = (downsampled_audio[0], downsampled_audio[1].tolist())

    raptparam.sample_rate_ratio = (float(original_audio[0]) /
                                   float(downsampled_audio[0]))

    nccf_results = _run_nccf(downsampled_audio, original_audio, raptparam)

    # Dynamic programming - determine voicing state at each period candidate
    freq_estimate = _get_freq_estimate(nccf_results, raptparam,
                                       original_audio[0])

    # return output of nccf for now
    return freq_estimate


def _setup_rapt_params(kwargs):
    # Use optional args for RAPT parameters otherwise use defaults
    params = raptparams.Raptparams()
    if kwargs is not None and isinstance(kwargs, dict):
        for key, value in kwargs.items():
            setattr(params, key, value)
    return params


def _get_audio_data(wavfile_path):
    # Read wavfile and convert to mono
    sample_rate, audio_sample = wavfile.read(wavfile_path)

    # TODO: investigate whether this type of conversion to mono is suitable:
    if len(audio_sample.shape) > 1:
        audio_sample = audio_sample[:, 0]/2.0 + audio_sample[:, 1]/2.0
        audio_sample = audio_sample.astype(int)

    return (sample_rate, audio_sample)


def _get_downsampled_audio(original_audio, maximum_allowed_freq):
    """
    Calc downsampling rate, downsample audio, return as tuple
    """
    downsample_rate = _calculate_downsampling_rate(original_audio[0],
                                                   maximum_allowed_freq)
    downsampled_audio = _downsample_audio(original_audio, downsample_rate)
    return (downsample_rate, downsampled_audio)


def _downsample_audio(original_audio, downsampling_rate):
    """
    Given the original audio sample/rate and a desired downsampling
    rate, returns a downsampled version of the audio input.
    """
    # TODO: look into applying low pass filter prior to downsampling, as
    # suggested in rapt paper.
    try:
        sample_rate_ratio = float(downsampling_rate) / float(original_audio[0])
    except ZeroDivisionError:
        raise ValueError('Input audio sampling rate is zero. Cannot determine '
                         'downsampling ratio.')
    # resample audio so it only uses a fraction of the original # of samples:
    number_of_samples = len(original_audio[1]) * sample_rate_ratio
    downsampled_audio = signal.resample(original_audio[1], number_of_samples)

    return downsampled_audio


def _calculate_downsampling_rate(initial_sampling_rate, maximum_f0):
    """
    Determines downsampling rate to apply to the audio input passed for
    RAPT processing
    """

    """
    NOTE: Using Python 2.7 so division is integer division by default
    Different default behavior in in Python 3+. That said, keeping the
    round() around the denominator of this formula as it is specified in
    the formula in David Talkin's paper:
    """
    try:
        aReturn = (initial_sampling_rate /
                   round(initial_sampling_rate / (4 * maximum_f0)))
    except ZeroDivisionError:
        raise ValueError('Ratio of sampling rate and max F0 leads to '
                         'division by zero. Cannot perform 1st pass of nccf '
                         'on downsampled audio.')
    return int(aReturn)


# NCCF Functionality:
# TODO: Consider moving nccf functions into a separate module / file?


def _run_nccf(downsampled_audio, original_audio, raptparam):
    first_pass = _first_pass_nccf(downsampled_audio, raptparam)

    # run second pass
    second_pass = _second_pass_nccf(original_audio, first_pass, raptparam)

    return second_pass


def _first_pass_nccf(audio, raptparam):
    # Runs normalized cross correlation function (NCCF) on downsampled audio,
    # outputting a set of potential F0 candidates that could be used to
    # determine the pitch at each given frame of the audio sample.

    nccfparam = _get_nccf_params(audio, raptparam, True)
    params = (raptparam, nccfparam)

    # Difference between "K-1" and starting value of "k"
    lag_range = ((params[1].longest_lag_per_frame - 1) -
                 params[1].shortest_lag_per_frame)

    # TODO: Re-read discussion of using double-precision arithmetic in rapt 3.3

    # NOTE: Because we are using max_frame_count exclusively for array size,
    # we do not run into issues with using xrange to iterate thru each frame, i

    candidates = [None] * params[1].max_frame_count

    for i in xrange(0, params[1].max_frame_count):
        candidates[i] = _get_firstpass_frame_results(
            audio, i, lag_range, params)

    return candidates


def _second_pass_nccf(original_audio, first_pass, raptparam):
    # Runs NCCF on original audio, but only for lags highlighted from first
    # pass results. Will output the finalized F0 candidates for each frame

    nccfparam = _get_nccf_params(original_audio, raptparam, False)
    params = (raptparam, nccfparam)

    # Difference between "K-1" and the starting value of "k"
    lag_range = ((params[1].longest_lag_per_frame - 1) -
                 params[1].shortest_lag_per_frame)

    candidates = [None] * params[1].max_frame_count

    for i in xrange(0, params[1].max_frame_count):
        candidates[i] = _get_secondpass_frame_results(
            original_audio, i, lag_range, params, first_pass)

    return candidates


def _get_nccf_params(audio_input, raptparams, is_firstpass):
    """
    Creates and returns nccfparams object w/ nccf-specific values
    """
    nccfparam = nccfparams.Nccfparams()
    # Value of "n" in NCCF equation:
    nccfparam.samples_correlated_per_lag = int(
        raptparams.correlation_window_size * audio_input[0])
    # Starting value of "k" in NCCF equation:
    if(is_firstpass):
        nccfparam.shortest_lag_per_frame = int(audio_input[0] /
                                               raptparams.maximum_allowed_freq)
    else:
        nccfparam.shortest_lag_per_frame = 0
    # Value of "K" in NCCF equation
    nccfparam.longest_lag_per_frame = int(audio_input[0] /
                                          raptparams.minimum_allowed_freq)
    # Value of "z" in NCCF equation
    nccfparam.samples_per_frame = int(raptparams.frame_step_size *
                                      audio_input[0])
    # Value of "M-1" in NCCF equation:
    nccfparam.max_frame_count = (int(float(len(audio_input[1])) /
                                 float(nccfparam.samples_per_frame)) - 1)
    return nccfparam


def _get_firstpass_frame_results(audio, current_frame, lag_range, params):
    # calculate correlation (theta) for all lags, and get the highest
    # correlation val (theta_max) from the calculated lags:
    all_lag_results = _get_correlations_for_all_lags(audio, current_frame,
                                                     lag_range, params)

    marked_values = _get_marked_results(all_lag_results, params, True)
    return marked_values


def _get_secondpass_frame_results(audio, current_frame, lag_range, params,
                                  first_pass):

    lag_results = _get_correlations_for_input_lags(audio, current_frame,
                                                   first_pass,  lag_range,
                                                   params)

    marked_values = _get_marked_results(lag_results, params, False)
    return marked_values


def _get_correlations_for_all_lags(audio, current_frame, lag_range, params):
    # Value of theta_max in NCCF equation, max for the current frame
    candidates = [0.0] * lag_range
    max_correlation_val = 0.0
    for k in xrange(0, lag_range):
        current_lag = k + params[1].shortest_lag_per_frame

        # determine if the current lag value causes us to go past the
        # end of the audio sample - if so - skip and set val to 0
        if ((current_lag + (params[1].samples_correlated_per_lag - 1)
             + (current_frame * params[1].samples_per_frame)) >= len(audio[1])):
            # candidates[k] = 0.0
            # TODO: Verify this behavior in unit test - no need to set val
            # since 0.0 is default
            continue

        candidates[k] = _get_correlation(audio, current_frame,
                                         current_lag, params)

        if candidates[k] > max_correlation_val:
            max_correlation_val = candidates[k]

    return (candidates, max_correlation_val)


def _get_correlations_for_input_lags(audio, current_frame, first_pass,
                                     lag_range, params):
    candidates = [0.0] * lag_range
    max_correlation_val = 0.0
    for lag_val in first_pass[current_frame]:
        # 1st pass lag value has been interpolated for original audio sample:
        lag_peak = lag_val[0]

        # for each peak check the closest 7 lags (if proposed peak is ok):
        if lag_peak > 3 and lag_peak < lag_range - 3:
            for k in xrange(lag_peak - 3, lag_peak + 4):
                # determine if the current lag value causes us to go past the
                # end of the audio sample - if so - skip and set val to 0
                sample_range = (k + (params[1].samples_correlated_per_lag - 1) +
                                (current_frame * params[1].samples_per_frame))
                if sample_range >= len(audio[1]):
                    # TODO: Verify this behavior in unit test -
                    # no need to set val
                    # since 0.0 is default
                    continue
                candidates[k] = _get_correlation(audio, current_frame, k,
                                                 params, False)
                if candidates[k] > max_correlation_val:
                    max_correlation_val = candidates[k]

    return (candidates, max_correlation_val)


# TODO: this can be used for 2nd pass - use parameter to decide 1stpass run?
def _get_marked_results(lag_results, params, is_firstpass=True):
    # values that meet certain threshold shall be marked for consideration
    min_valid_correlation = (lag_results[1] * params[0].min_acceptable_peak_val)
    max_allowed_candidates = params[0].max_hypotheses_per_frame - 1

    candidates = []
    for k, k_val in enumerate(lag_results[0]):
        if k_val >= min_valid_correlation:
            if is_firstpass:
                candidates.append(_get_peak_lag_val(lag_results[0], k, params))
            else:
                current_lag = k + params[1].shortest_lag_per_frame
                candidates.append((current_lag, k_val))

    # now check to see if selected candidates exceed max allowed:
    if len(candidates) > max_allowed_candidates:
        candidates.sort(key=lambda tup: tup[1], reverse=True)
        returned_candidates = candidates[0:max_allowed_candidates]
        # re-sort before returning so that it is in order of low to highest k
        returned_candidates.sort(key=lambda tup: tup[0])
    else:
        returned_candidates = candidates

    return returned_candidates


def _get_correlation(audio, frame, lag, params, is_firstpass=True):
    samples = 0

    # mean_for_window is the mean signal for the current analysis window
    # David Talkin suggests this in his RAPT paper as a variant to the original
    # NCCF function. This mean only needs to be calculated once per frame.
    # NOTE: summation is from m (frame start) to m+n-1 (m + samples handled
    # per lag). The -1 is implicit when summing the array between m and n
    frame_start = frame * params[1].samples_per_frame
    final_correlated_sample = frame_start + params[1].samples_correlated_per_lag
    frame_sum = sum(audio[1][frame_start:final_correlated_sample])
    mean_for_window = ((1.0 / float(params[1].samples_correlated_per_lag))
                       * frame_sum)

    # NOTE: NCCF formula has inclusive summation from 0 to n-1, but must add
    # 1 to max value here due to standard behavior of range/xrange:
    for j in xrange(0, params[1].samples_correlated_per_lag):
        correlated_samples = _get_sample(audio, frame_start, j, params[1],
                                         mean_for_window)
        samples_for_lag = _get_sample(audio, frame_start, j + lag, params[1],
                                      mean_for_window)
        samples += correlated_samples * samples_for_lag

    denominator_base = _get_nccf_denominator_val(audio, frame_start, 0,
                                                 params[1], mean_for_window)

    denominator_lag = _get_nccf_denominator_val(audio, frame_start, lag,
                                                params[1], mean_for_window)

    if is_firstpass:
        denominator = math.sqrt(denominator_base * denominator_lag)
    else:
        denominator = ((denominator_base * denominator_lag) +
                       params[0].additive_constant)
        denominator = math.sqrt(denominator)

    return float(samples) / float(denominator)


def _get_sample(audio, frame_start, correlation_index, nccfparam,
                mean_for_window):
    # value of "x_m+j" in NCCF equation
    current_sample = audio[1][frame_start + correlation_index]
    returned_signal = current_sample - mean_for_window
    return returned_signal


def _get_nccf_denominator_val(audio, frame_start, starting_val, nccfparam,
                              frame_sum):
    # Calculates the denominator value of the NCCF equation
    # (e_j in the formula)
    total_sum = 0.0
    # NOTE that I am adding 1 to the xrange to be inclusive:
    for l in xrange(starting_val,
                    starting_val + nccfparam.samples_correlated_per_lag):
        sample = float(_get_sample(audio, frame_start, l, nccfparam, frame_sum))
        total_sum += (sample ** 2)
    return total_sum


def _get_peak_lag_val(lag_results, lag_index, params):
    # current_lag = lag_index + params[1].shortest_lag_per_frame
    # extrapolated_lag = int(current_lag * params[0].sample_rate_ratio)
    # return (extrapolated_lag, lag_results[lag_index])

    # lag peak is the maxima of a given peak obtained by results
    lag_peak = lag_index + params[1].shortest_lag_per_frame
    x_vals = []
    y_vals = []

    if lag_index == 0:
        y_vals = lag_results[lag_index:lag_index + 3]
        x_vals = range(lag_peak, lag_peak+3)
    elif lag_index == (len(lag_results)-1):
        y_vals = lag_results[lag_index-2:lag_index+1]
        x_vals = range(lag_peak-2, lag_peak+1)
    else:
        y_vals = lag_results[lag_index-1:lag_index+2]
        x_vals = range(lag_peak-1, lag_peak+2)

    parabolic_func = numpy.polyfit(x_vals, y_vals, 2)
    # return maxima of the parabola, shifted to appropriate lag value
    lag_peak = -parabolic_func[1] / (2 * parabolic_func[0])
    lag_peak = round(lag_peak * params[0].sample_rate_ratio)
    lag_peak = int(lag_peak)
    return (lag_peak, lag_results[lag_index])


# Dynamic Programming / Post-Processing:

# this method will obtain best candidate per frame and calc freq est per frame
def _get_freq_estimate(nccf_results, raptparam, sample_rate):
    results = []
    candidates = _determine_state_per_frame(nccf_results, raptparam,
                                            sample_rate)
    for candidate in candidates:
        if candidate > 0:
            results.append(sample_rate/candidate)
        else:
            results.append(0.0)
    return results


# this method will prepare to call a recursive function that will determine
# the optimal voicing state / candidate per frame
def _determine_state_per_frame(nccf_results, raptparam, sample_rate):
    candidates = []
    # Add unvoiced candidate entry per frame (tuple w/ 0 lag, 0 correlation)
    for result in nccf_results:
        result.append((0, 0.0))

    # now call recursive function that will calculate cost per candidate:
    all_candidates = _process_candidates(len(nccf_results) - 1, [],
                                         nccf_results, raptparam, sample_rate)

    # with the results, take the lag of the lowest cost candidate per frame
    for result in all_candidates:
        candidates.append(result[0][1][0])
    return candidates


def _process_candidates(frame_idx, candidates, nccf_results, raptparam,
                        sample_rate):
    new_candidates = []
    # recursive step:
    if frame_idx > 0:
        new_candidates = _process_candidates(frame_idx-1, candidates,
                                             nccf_results, raptparam,
                                             sample_rate)
    frame_candidates = _calculate_costs_per_frame(frame_idx, new_candidates,
                                                  nccf_results, raptparam,
                                                  sample_rate)
    new_candidates.append(frame_candidates)
    return new_candidates


def _calculate_costs_per_frame(frame_idx, new_candidates, nccf_results, params,
                               sample_rate):
    frame_candidates = []
    max_for_frame = _select_max_correlation_for_frame(nccf_results[frame_idx])
    for candidate in nccf_results[frame_idx]:
        local_cost = _calculate_local_cost(candidate, max_for_frame, params,
                                           sample_rate)
        best_cost = _get_best_cost(candidate, local_cost, new_candidates,
                                   params, sample_rate)
        frame_candidates.append((best_cost, candidate))
    return frame_candidates


def _select_max_correlation_for_frame(nccf_results_frame):
    maxval = 0.0
    for hypothesis in nccf_results_frame:
        if hypothesis[1] > maxval:
            maxval = hypothesis[1]
    return maxval


def _calculate_local_cost(candidate, max_corr_for_frame, params, sample_rate):
    # calculate local cost of hypothesis (d_i,j in RAPT)
    lag_val = candidate[0]
    correlation_val = candidate[1]
    if lag_val == 0 and correlation_val == 0.0:
        # unvoiced hypothesis: add VO_BIAS to largest correlation val in frame
        cost = params.voicing_bias + max_corr_for_frame
    else:
        # voiced hypothesis
        lag_weight = (float(params.lag_weight) / float(sample_rate /
                      float(params.minimum_allowed_freq)))
        cost = (1.0 - correlation_val * (1.0 - float(lag_weight)
                * float(lag_val)))
    return cost


# TODO: Finish logic for this method - have it determine the delta cost of
# transition from each previous frame and pick the cheapest
def _get_best_cost(candidate, local_cost, candidate_list,  params, sample_rate):
    # need to determine best transition cost based on previous frame:
    return_cost = 0
    # first check to see if list is empty (we are at beginning and can use
    # predefined vals for transition costs
    if not candidate_list:
        return_cost = local_cost + 0
    # if prev candidates exist, then we need to check each with a transition
    # cost and see which is the lowest
    else:
        for prev_candidate in candidate_list[-1]:
            return_cost = local_cost + 0
    return return_cost


# determines cost of voiced to voice delta w/ prev entry's global cost:
def _get_voiced_to_voiced_cost(candidate, prev_entry, params):
    prev_cost = prev_entry[0]
    prev_candidate = prev_entry[1]
    # value of epsilon in voiced-to-voiced delta formula:
    freq_jump_cost = numpy.log(float(candidate[0]) / float(prev_candidate[0]))
    transition_cost = (params.freq_weight * (params.doubling_cost +
                       abs(freq_jump_cost - numpy.log(2.0))))
    final_cost = prev_cost + transition_cost
    return final_cost


# delta cost of unvoiced to unvoiced is 0, so just return previous entry's
# global cost:
def _get_unvoiced_to_unvoiced_cost(prev_entry):
    return prev_entry[0] + 0.0


def _get_voiced_to_unvoiced_cost(candidate, prev_entry, params, sample_rate):
    prev_cost = prev_entry[0]
    # prev_candidate = prev_entry[1]
    delta = (params.transition_cost + (params.spec_mod_transition_cost *
             _get_spec_stationarity()) + (params.amp_mod_transition_cost *
             _get_rms_ratio(sample_rate)))
    return prev_cost + delta


def _get_unvoiced_to_voiced_cost(candidate, prev_entry, params, sample_rate):
    prev_cost = prev_entry[0]
    # prev_candidate = prev_entry[1]
    delta = (params.transition_cost + (params.spec_mod_transition_cost *
             _get_spec_stationarity()) + (params.amp_mod_transition_cost /
             _get_rms_ratio(sample_rate)))
    return prev_cost + delta


# spectral stationarity function, denoted as S_i in the delta formulas:
def _get_spec_stationarity():
    # TODO: Figure out how to calculate this:
    itakura_distortion = 1
    return_val = 0.2 / (itakura_distortion - 0.8)
    return return_val


# RMS ratio, denoted as rr_i in the delta formulas:
def _get_rms_ratio(sample_rate):
    # TODO: Need to pass in audio input here - used when calcing rms ratio
    window_length = 0.03 * sample_rate
    hanning_window_vals = numpy.hanning(window_length)
    # use range(0,window_length) for sigma/summation (effectivey 0 to J-1)
    rms_curr = math.sqrt(sum(w**2 for w in hanning_window_vals) / window_length)
    rms_prev = math.sqrt(sum(w**2 for w in hanning_window_vals) / window_length)
    return (rms_curr / rms_prev)
