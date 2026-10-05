"""Tests for the movement analyzer.

Requirement: report the mean current of the whole movement (acceleration,
constant speed, braking) that lies between the deltastep peaks of a capture
(deltastep strokes, movement, deltastep strokes, idle; the capture may start
during the strokes), leaving out the conversion that touches each peak. The
capture must end at idle; a single conversion below idle, as right after a
deltastep stroke, is not idle. A capture without a trustworthy value must
raise, never return a wrong number.

Real captures are the lab recordings (tests/data/lab); synthetic ones come
from the physical simulation and from hand-built conversion sequences.
"""

from dataclasses import replace

import numpy as np
import pytest

from analyzer import (
    AmbiguousRunError,
    AnalysisConfig,
    AnalysisError,
    InvalidCaptureError,
    SignalNotSettledError,
    analyze_waveform,
)
from tests.lab_captures import LAB_CAPTURES, load_lab_capture
from scenarios import SCENARIOS
from simulation import InstrumentModel, SimulatedCapture, simulate_capture

OVERFLOW_SENTINEL = 9.90000000e37
ADC_RATES = ("SLOW", "MED", "FAST")
IDLE_A = 0.167
PEAK_A = 0.625
MOVEMENT_A = 0.190
# One SLOW conversion.
PERIOD_S = 0.2
MOTOR1_SLOW = [name for name in LAB_CAPTURES if name.endswith("motor1_slow")]
MOTOR2_SLOW = [name for name in LAB_CAPTURES if name.endswith("motor2_slow")]
MOTOR1_MED_FAST = [name for name in LAB_CAPTURES if "motor1" in name and not name.endswith("_slow")]
MOTOR2_MED_FAST = [name for name in LAB_CAPTURES if "motor2" in name and not name.endswith("_slow")]


def _simulate(name: str, adc_rate: str, seed: int = 3) -> SimulatedCapture:
    return simulate_capture(SCENARIOS[name].phases(), InstrumentModel(adc_rate=adc_rate), seed=seed)


def _tolerance(value: float) -> float:
    return max(0.002, 0.02 * abs(value))


def _conversions(steps: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """One reading per SLOW conversion; *steps* are ``(current, duration)`` pairs."""
    values = np.concatenate([np.full(round(duration / PERIOD_S), level) for level, duration in steps])
    return np.arange(len(values)) * PERIOD_S, values


def _assert_correct_or_raises(timestamps, values, truth: float) -> None:
    try:
        result = analyze_waveform(timestamps, values)
    except AnalysisError:
        return
    assert abs(result.stable_value - truth) <= _tolerance(truth)


class TestLabCaptures:
    @pytest.mark.parametrize("name", MOTOR1_SLOW)
    def test_motor1_at_slow_reports_its_movement_level(self, name):
        # The movement reads 187-195 mA between the deltastep groups; at SLOW its
        # acceleration and braking fall in the conversions that touch the peaks.
        assert analyze_waveform(*load_lab_capture(name)).stable_value == pytest.approx(0.1902, abs=0.0008)

    @pytest.mark.parametrize("name", MOTOR1_SLOW + MOTOR1_MED_FAST)
    def test_motor1_movement_lies_between_the_deltastep_groups(self, name):
        result = analyze_waveform(*load_lab_capture(name))
        assert 2.7 <= result.start_time <= 3.3
        assert 4.9 <= result.end_time <= 5.6

    @pytest.mark.parametrize("name", MOTOR1_MED_FAST)
    def test_motor1_at_med_and_fast_includes_acceleration_and_braking(self, name):
        # 187-193 mA at constant speed, 196-218 mA while accelerating and braking.
        assert 0.1910 <= analyze_waveform(*load_lab_capture(name)).stable_value <= 0.1925

    @pytest.mark.parametrize("name", MOTOR2_SLOW)
    def test_motor2_at_slow_is_within_its_movement_or_an_error(self, name):
        # Its 0.6 s movement reads 196-207 mA; at SLOW that is only 3 conversions.
        try:
            value = analyze_waveform(*load_lab_capture(name)).stable_value
        except AnalysisError:
            return
        assert 0.196 <= value <= 0.207

    @pytest.mark.parametrize("name", MOTOR2_MED_FAST)
    def test_motor2_at_med_and_fast_reports_its_whole_movement(self, name):
        # Acceleration 208-216 mA, constant speed about 196 mA, braking 204-216 mA; the four
        # captures agree within the analysis tolerance around their mean, 200.3 mA.
        value = analyze_waveform(*load_lab_capture(name)).stable_value
        assert abs(value - 0.2003) <= _tolerance(0.2003)

    def test_motor2_at_slow_with_two_clean_conversions_reports_196_ma(self):
        result = analyze_waveform(*load_lab_capture("2026-10-02_16-31-35_motor2_slow"))
        assert result.stable_value == pytest.approx(0.1962, abs=0.0005)


class TestScenarios:
    @pytest.mark.parametrize("adc_rate", ADC_RATES)
    @pytest.mark.parametrize("name", ["motor1", "starts_in_deltastep"])
    def test_long_movement_is_measured_at_every_adc_rate(self, name, adc_rate):
        capture = _simulate(name, adc_rate)
        result = analyze_waveform(capture.timestamps, capture.values)
        assert abs(result.stable_value - capture.expected_value) <= _tolerance(capture.expected_value)

    @pytest.mark.parametrize("seed", [0, 1, 2])
    @pytest.mark.parametrize("adc_rate", ["MED", "FAST"])
    def test_short_movement_is_measured_at_faster_adc_rates(self, adc_rate, seed):
        capture = _simulate("motor2", adc_rate, seed)
        result = analyze_waveform(capture.timestamps, capture.values)
        assert abs(result.stable_value - capture.expected_value) <= _tolerance(capture.expected_value)

    @pytest.mark.parametrize("seed", [0, 1, 2])
    def test_short_movement_at_slow_is_correct_or_raises(self, seed):
        capture = _simulate("motor2", "SLOW", seed)
        _assert_correct_or_raises(capture.timestamps, capture.values, capture.expected_value)


class TestMovementDetection:
    def test_conversions_straddling_the_movement_edges_are_left_out(self):
        timestamps, values = _conversions(
            [(PEAK_A, 0.6), (0.35, 0.2), (MOVEMENT_A, 2.0), (0.30, 0.2), (PEAK_A, 0.6), (IDLE_A, 3.0)]
        )
        result = analyze_waveform(timestamps, values)
        assert result.stable_value == pytest.approx(MOVEMENT_A)
        assert (result.start_time, result.end_time) == pytest.approx((0.8, 2.6))
        assert (result.run_start_time, result.run_end_time) == pytest.approx((0.6, 2.8))
        assert result.idle_level == pytest.approx(IDLE_A)

    def test_acceleration_and_braking_are_part_of_the_movement(self):
        movement = [(0.208, 0.2), (0.198, 0.2), (0.197, 0.2), (0.196, 0.2), (0.200, 0.2), (0.212, 0.2)]
        timestamps, values = _conversions(
            [(PEAK_A, 0.6), (0.35, 0.2), *movement, (0.30, 0.2), (PEAK_A, 0.6), (IDLE_A, 3.0)]
        )
        assert analyze_waveform(timestamps, values).stable_value == pytest.approx(1.211 / 6)

    def test_short_stretches_inside_the_deltastep_groups_are_ignored(self):
        timestamps, values = _conversions(
            [(PEAK_A, 0.4), (0.32, 0.2), (0.25, 0.2), (PEAK_A, 0.4), (0.35, 0.2), (MOVEMENT_A, 2.0), (0.30, 0.2),
             (PEAK_A, 0.6), (IDLE_A, 3.0)]
        )
        assert analyze_waveform(timestamps, values).stable_value == pytest.approx(MOVEMENT_A)

    def test_two_movements_raise_ambiguous(self):
        timestamps, values = _conversions(
            [(PEAK_A, 0.6), (0.35, 0.2), (MOVEMENT_A, 1.0), (0.30, 0.2), (PEAK_A, 0.6), (0.35, 0.2), (0.21, 1.0),
             (0.30, 0.2), (PEAK_A, 0.6), (IDLE_A, 3.0)]
        )
        with pytest.raises(AmbiguousRunError):
            analyze_waveform(timestamps, values)

    def test_movement_of_one_conversion_raises_not_settled(self):
        timestamps, values = _conversions([(PEAK_A, 0.6), (MOVEMENT_A, 0.2), (PEAK_A, 0.6), (IDLE_A, 3.0)])
        with pytest.raises(SignalNotSettledError):
            analyze_waveform(timestamps, values)

    def test_idle_only_capture_raises_not_settled(self):
        with pytest.raises(SignalNotSettledError):
            analyze_waveform(*_conversions([(IDLE_A, 5.0)]))

    def test_capture_not_ending_at_idle_raises_invalid_capture(self):
        timestamps, values = _conversions([(PEAK_A, 0.4), (IDLE_A, 0.4), (PEAK_A, 0.4), (MOVEMENT_A, 2.0)])
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(timestamps, values)

    def test_a_single_conversion_below_idle_after_a_peak_is_not_idle(self):
        timestamps, values = _conversions(
            [(PEAK_A, 0.4), (IDLE_A - 0.005, 0.2), (PEAK_A, 0.4), (0.35, 0.2), (MOVEMENT_A, 2.0), (0.30, 0.2),
             (PEAK_A, 0.6), (IDLE_A, 3.0)]
        )
        result = analyze_waveform(timestamps, values)
        assert result.stable_value == pytest.approx(MOVEMENT_A)
        assert result.idle_level == pytest.approx(IDLE_A)

    def test_repeated_polls_of_one_conversion_count_once(self):
        # The instrument answers READ? with its last conversion: polling five times faster repeats each value.
        timestamps, values = _conversions(
            [(PEAK_A, 0.6), (0.35, 0.2), (0.189, 0.2), (0.191, 0.2), (0.30, 0.2), (PEAK_A, 0.6), (IDLE_A, 3.0)]
        )
        polled_times = (timestamps[:, None] + np.arange(5) * PERIOD_S / 5).ravel()
        result = analyze_waveform(polled_times, np.repeat(values, 5))
        assert result.stable_value == pytest.approx(0.190)
        assert result.samples_used == 2

    def test_result_indices_refer_to_original_arrays(self):
        timestamps, values = _conversions(
            [(PEAK_A, 0.6), (0.35, 0.2), (MOVEMENT_A, 2.0), (0.30, 0.2), (PEAK_A, 0.6), (IDLE_A, 3.0)]
        )
        values[1] = np.nan
        result = analyze_waveform(timestamps, values)
        assert np.all(values[result.start_index:result.end_index] == MOVEMENT_A)
        assert values[result.start_index - 1] == 0.35
        assert values[result.end_index] == 0.30


class TestInputValidation:
    def test_nan_samples_count_as_invalid(self):
        timestamps, values = _conversions([(PEAK_A, 0.6), (MOVEMENT_A, 2.0), (PEAK_A, 0.6), (IDLE_A, 3.0)])
        values[::2] = np.nan
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(timestamps, values)

    @pytest.mark.parametrize("sentinel", [OVERFLOW_SENTINEL, -OVERFLOW_SENTINEL])
    def test_overflow_sentinels_in_the_peaks_do_not_affect_the_value(self, sentinel):
        timestamps, values = _conversions(
            [(PEAK_A, 0.6), (0.35, 0.2), (MOVEMENT_A, 2.0), (0.30, 0.2), (PEAK_A, 0.6), (IDLE_A, 3.0)]
        )
        values[1] = sentinel
        assert analyze_waveform(timestamps, values).stable_value == pytest.approx(MOVEMENT_A)

    def test_non_increasing_timestamps_raise(self):
        timestamps, values = _conversions([(PEAK_A, 0.6), (MOVEMENT_A, 2.0), (IDLE_A, 3.0)])
        timestamps[5] = timestamps[4]
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(timestamps, values)

    def test_mismatched_lengths_raise(self):
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(np.arange(10.0), np.zeros(9))

    def test_empty_capture_raises(self):
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(np.array([]), np.array([]))

    @pytest.mark.parametrize("field", ["rel_tolerance", "abs_tolerance_a"])
    @pytest.mark.parametrize("value", [0.0, float("nan"), float("inf")])
    def test_config_rejects_non_positive_or_non_finite_tolerances(self, field, value):
        with pytest.raises(ValueError):
            replace(AnalysisConfig(), **{field: value})

    @pytest.mark.parametrize("timestamps", [[0, "bad"], [0, 10**500]])
    def test_unconvertible_timestamps_raise_invalid_capture(self, timestamps):
        with pytest.raises(InvalidCaptureError):
            analyze_waveform(timestamps, [0.0, 1.0])
