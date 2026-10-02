"""Tests for the running-current analyzer.

Requirement: report the mean current the device draws over its run (from the
end of the start transient to the stop), for captures shaped idle, start,
run, stop, idle, including step ripple and PWM/burst load, at any HMC8012
ADC rate. A capture that does not allow a trustworthy value must raise, never
return a wrong number.

Realistic captures come from the physical simulation (simulation.py,
scenarios.py); edge cases use hand-built arrays.
"""

from dataclasses import replace

import numpy as np
import pytest

from analyzer import (
    AmbiguousRunError,
    AnalysisConfig,
    AnalysisError,
    ImpreciseValueError,
    InvalidCaptureError,
    SignalNotSettledError,
    analyze_waveform,
    smooth_level,
)
from scenarios import SCENARIOS
from simulation import InstrumentModel, SimulatedCapture, simulate_capture

OVERFLOW_SENTINEL = 9.90000000e37
ADC_RATES = ("SLOW", "MED", "FAST")
SCENARIOS_WITH_STEADY_RUN = ("nominal", "long_idle_after", "high_current", "pwm_load", "run_to_end")
SCENARIOS_CORRECT_OR_RAISE = ("slow_settle", "slow_bursts", "aliasing", "hold_after_stop", "pre_and_post_hold")


def _simulate(name: str, adc_rate: str, seed: int = 3) -> SimulatedCapture:
    return simulate_capture(SCENARIOS[name].phases(), InstrumentModel(adc_rate=adc_rate), seed=seed)


def _tolerance(value: float, config: AnalysisConfig = AnalysisConfig()) -> float:
    return max(config.abs_tolerance_a, config.rel_tolerance * abs(value))


def _step_capture(levels_and_durations: list[tuple[float, float]], rate_hz: float = 100.0):
    """Noise-free piecewise-constant capture sampled uniformly."""
    values = np.concatenate([np.full(round(d * rate_hz), level) for level, d in levels_and_durations])
    return np.arange(len(values)) / rate_hz, values


def _assert_correct_or_raises(timestamps, values, truth: float, config: AnalysisConfig = AnalysisConfig()) -> None:
    try:
        result = analyze_waveform(timestamps, values, config)
    except AnalysisError:
        return
    assert abs(result.stable_value - truth) <= _tolerance(truth, config)


class TestScenarios:
    @pytest.mark.parametrize("adc_rate", ADC_RATES)
    @pytest.mark.parametrize("name", SCENARIOS_WITH_STEADY_RUN)
    def test_scenario_reports_running_mean_within_tolerance(self, name, adc_rate):
        capture = _simulate(name, adc_rate)
        result = analyze_waveform(capture.timestamps, capture.values)
        assert abs(result.stable_value - capture.expected_value) <= _tolerance(capture.expected_value)

    @pytest.mark.parametrize("adc_rate", ADC_RATES)
    @pytest.mark.parametrize("name", SCENARIOS_CORRECT_OR_RAISE)
    def test_difficult_scenario_is_correct_or_raises(self, name, adc_rate):
        capture = _simulate(name, adc_rate)
        _assert_correct_or_raises(capture.timestamps, capture.values, capture.expected_value)

    @pytest.mark.parametrize("adc_rate", ("SLOW", "MED"))
    def test_near_sync_ripple_is_averaged_by_slow_adc_rates(self, adc_rate):
        # At FAST the beat is slower than the run and biases the level undetectably (documented limit).
        capture = _simulate("near_sync_ripple", adc_rate)
        result = analyze_waveform(capture.timestamps, capture.values)
        assert abs(result.stable_value - capture.expected_value) <= _tolerance(capture.expected_value)


class TestRunDetection:
    def test_idle_only_capture_raises_not_settled(self):
        timestamps, values = _step_capture([(0.03, 6.0)])
        with pytest.raises(SignalNotSettledError):
            analyze_waveform(timestamps, values)

    def test_run_interval_spans_start_to_stop(self):
        timestamps, values = _step_capture([(0.03, 2.0), (0.35, 3.0), (0.03, 2.0)])
        result = analyze_waveform(timestamps, values)
        assert result.run_start_time == pytest.approx(2.0, abs=0.02)
        assert result.run_end_time == pytest.approx(5.0, abs=0.02)
        assert result.idle_level == pytest.approx(0.03)

    @pytest.mark.parametrize("adc_rate", ADC_RATES)
    def test_one_second_run_reports_running_mean(self, adc_rate):
        sc = SCENARIOS["nominal"]
        capture = simulate_capture(sc.phases(replace(sc.defaults, run_s=1.0)), InstrumentModel(adc_rate=adc_rate), seed=0)
        result = analyze_waveform(capture.timestamps, capture.values)
        assert abs(result.stable_value - capture.expected_value) <= _tolerance(capture.expected_value)

    def test_run_until_capture_end_is_averaged(self):
        timestamps, values = _step_capture([(0.03, 1.0), (0.35, 4.0)])
        result = analyze_waveform(timestamps, values)
        assert result.stable_value == pytest.approx(0.35)

    def test_brief_glitch_is_part_of_the_running_mean(self):
        timestamps, values = _step_capture([(0.03, 1.0), (0.35, 3.0), (0.6, 0.05), (0.35, 3.0), (0.03, 1.0)])
        values = values + np.random.default_rng(0).normal(0.0, 0.0005, len(values))  # instrument noise
        result = analyze_waveform(timestamps, values)
        assert result.stable_value == pytest.approx((0.35 * 6.0 + 0.6 * 0.05) / 6.05, abs=0.001)

    def test_recurring_brief_loads_are_included_or_rejected(self):
        values = np.full(1800, 0.03)
        values[100:1600] = 0.35
        values[100:110] = 0.9
        for start in (400, 900, 1400):
            values[start:start + 10] = 1.5
        timestamps = np.arange(1800) / 100
        _assert_correct_or_raises(timestamps, values, float(np.mean(values[110:1600])))

    def test_noisy_idle_does_not_count_as_a_run(self):
        capture = simulate_capture(SCENARIOS["nominal"].phases(), InstrumentModel(adc_rate="SLOW", noise_a=0.010), seed=0)
        result = analyze_waveform(capture.timestamps, capture.values)
        assert abs(result.stable_value - capture.expected_value) <= _tolerance(capture.expected_value)

    def test_two_separate_runs_raise_ambiguous(self):
        timestamps, values = _step_capture([(0.03, 1.0), (0.35, 2.0), (0.03, 2.0), (0.35, 2.0), (0.03, 1.0)])
        with pytest.raises(AmbiguousRunError):
            analyze_waveform(timestamps, values)


class TestNotSteadyRun:
    @pytest.mark.parametrize("levels", [
        [(0.03, 1.0), (0.9, 0.1), (0.35, 2.0), (0.55, 6.0), (0.03, 2.0)],   # hold longer than run
        [(0.03, 1.0), (0.9, 0.1), (0.35, 1.4), (0.55, 6.0), (0.03, 2.0)],   # short run, then hold
        [(0.03, 1.0), (0.7, 1.8), (0.35, 6.0)],                              # two running levels
        [(0.03, 1.0), (0.35, 2.0), (0.05, 8.0), (0.08, 0.05)],               # standby after stop
    ])
    def test_two_levels_in_one_run_raise(self, levels):
        timestamps, values = _step_capture(levels)
        with pytest.raises(SignalNotSettledError):
            analyze_waveform(timestamps, values)

    @pytest.mark.parametrize("hold_s", [10.0, 20.0])
    def test_long_hold_after_short_run_raises(self, hold_s):
        timestamps, values = _step_capture([(0.03, 1.0), (0.9, 0.1), (0.35, 1.2), (0.55, hold_s), (0.03, 1.0)])
        values = values + np.random.default_rng(42).normal(0.0, 0.0005, len(values))
        with pytest.raises(SignalNotSettledError):
            analyze_waveform(timestamps, values)

    def test_slow_burst_cycles_are_averaged_or_rejected(self):
        timestamps = np.arange(0.0, 15.0, 0.01)
        running = (timestamps >= 1.0) & (timestamps < 13.0)
        values = np.where(running, 0.35 + 0.2 * (((timestamps - 1.0) % 4.0) < 1.0), 0.03)
        _assert_correct_or_raises(timestamps, values, 0.40)

    @pytest.mark.parametrize("slope", [0.005, 0.02])
    def test_continuous_drift_raises(self, slope):
        timestamps = np.arange(0.0, 14.0, 0.01)
        running = (timestamps >= 1.0) & (timestamps < 11.0)
        values = np.where(running, 0.35 + slope * (timestamps - 1.0), 0.03)
        values[100:110] = 0.9
        with pytest.raises(SignalNotSettledError):
            analyze_waveform(timestamps, values)


class TestPreconditions:
    def test_short_initial_idle_raises_invalid_capture(self):
        timestamps, values = _step_capture([(0.03, 0.1), (0.35, 4.0), (0.03, 4.0)])
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(timestamps, values)

    def test_capture_starting_during_inrush_raises_invalid_capture(self):
        timestamps, values = _step_capture([(0.9, 0.2), (0.35, 1.2), (0.03, 4.0)])
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(timestamps, values)

    def test_invalid_readings_at_capture_start_do_not_move_the_idle_check(self):
        timestamps = np.arange(1000) / 100
        values = np.repeat([np.nan, 0.35, 0.7, 0.03], [100, 200, 500, 200])
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(timestamps, values)

    def test_capture_starting_while_running_finds_no_run_above_start_level(self):
        timestamps, values = _step_capture([(0.35, 3.0), (0.03, 4.0)])
        with pytest.raises(SignalNotSettledError):
            analyze_waveform(timestamps, values)


class TestStableValue:
    def test_too_few_independent_readings_raise_imprecise(self):
        # Flat smoothed level, but only 15 independent readings of a +/-65 mA bimodal spread.
        rate_hz = 50.0
        pattern = np.repeat([0.50, 0.35, 0.35, 0.35], int(0.2 * rate_hz))
        run = np.tile(pattern, 4)[: int(3.0 * rate_hz)]
        idle = np.full(int(2 * rate_hz), 0.03)
        values = np.concatenate([idle, run, idle])
        timestamps = np.arange(len(values)) / rate_hz
        with pytest.raises(ImpreciseValueError):
            analyze_waveform(timestamps, values)

    def test_standard_error_reported_for_noisy_run(self):
        capture = _simulate("pwm_load", "FAST")
        result = analyze_waveform(capture.timestamps, capture.values)
        assert 0.0 < result.standard_error <= _tolerance(result.stable_value) / 2

    def test_smooth_level_weights_samples_by_time_not_by_count(self):
        timestamps = np.r_[np.arange(0.0, 0.25, 0.001), np.arange(0.25, 0.5, 0.1)]
        values = np.where(timestamps < 0.25, 0.3, 0.5)
        # Window [0, 0.5] clipped to the data span [0, 0.45]: 0.25 s at 0.3 A, 0.2 s at 0.5 A.
        assert smooth_level(timestamps, values, 0.5)[250] == pytest.approx((0.25 * 0.3 + 0.2 * 0.5) / 0.45, abs=1e-3)

    def test_stable_value_is_time_weighted_under_uneven_polling(self):
        dense_t = np.arange(1.0, 3.0, 0.001)
        sparse_t = np.arange(3.0, 5.0, 0.1)
        timestamps = np.concatenate([np.arange(0.0, 1.0, 0.01), dense_t, sparse_t])
        values = np.concatenate([np.full(100, 0.03), np.full(len(dense_t), 0.349), np.full(len(sparse_t), 0.351)])
        result = analyze_waveform(timestamps, values)
        assert result.stable_value == pytest.approx(0.350, abs=0.0005)

    def test_result_indices_refer_to_original_arrays_when_invalid_samples_removed(self):
        timestamps, values = _step_capture([(0.03, 1.0), (0.35, 3.0), (0.03, 1.0)])
        values = values.copy()
        values[[50, 60, 70]] = OVERFLOW_SENTINEL
        result = analyze_waveform(timestamps, values)
        assert timestamps[result.start_index] == pytest.approx(result.start_time)
        assert timestamps[result.end_index - 1] <= result.end_time
        assert result.samples_used == result.end_index - result.start_index


class TestInputValidation:
    def test_nan_samples_count_as_invalid(self):
        timestamps, values = _step_capture([(0.03, 1.0), (0.35, 3.0), (0.03, 1.0)])
        values = values.copy()
        values[::2] = np.nan
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(timestamps, values)

    def test_invalid_readings_inside_the_run_raise(self):
        # Overflowing peaks: dropping them would bias the mean low.
        timestamps = np.arange(1200) / 100
        values = np.full(1200, 0.03)
        values[100:1100] = 0.15
        for start in range(100, 1100, 20):
            values[start + 10:start + 12] = np.nan
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(timestamps, values)

    def test_negative_overflow_sentinel_counts_as_invalid(self):
        timestamps, values = _step_capture([(0.03, 1.0), (0.35, 3.0), (0.03, 1.0)])
        values = values.copy()
        values[20] = -OVERFLOW_SENTINEL
        result = analyze_waveform(timestamps, values)
        assert result.stable_value == pytest.approx(0.35)

    def test_non_increasing_timestamps_raise(self):
        timestamps, values = _step_capture([(0.03, 1.0), (0.35, 3.0)])
        timestamps = timestamps.copy()
        timestamps[50] = timestamps[49]
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(timestamps, values)

    def test_mismatched_lengths_raise(self):
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(np.arange(10.0), np.zeros(9))

    def test_empty_capture_raises(self):
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(np.array([]), np.array([]))

    @pytest.mark.parametrize("field", ["smoothing_window_s", "min_run_s", "rel_tolerance"])
    def test_config_rejects_non_positive_values(self, field):
        with pytest.raises(ValueError):
            replace(AnalysisConfig(), **{field: 0.0})

    @pytest.mark.parametrize("value", [float("nan"), float("inf")])
    def test_config_rejects_non_finite_values(self, value):
        with pytest.raises(ValueError):
            AnalysisConfig(max_settle_s=value)

    @pytest.mark.parametrize("timestamps", [[0, "bad"], [0, 10**500]])
    def test_unconvertible_timestamps_raise_invalid_capture(self, timestamps):
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(timestamps, [0.0, 1.0])
