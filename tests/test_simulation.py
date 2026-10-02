"""Tests for the physical capture simulation (DUT current + HMC8012 sampling model)."""

import numpy as np
import pytest

from simulation import (
    ADC_READINGS_PER_SECOND,
    InstrumentModel,
    Phase,
    sample_capture,
    simulate_capture,
    true_current,
)


def _rng(seed: int = 0) -> np.random.Generator:
    return np.random.default_rng(seed)


class TestTrueCurrent:
    def test_flat_phase_returns_constant_level(self):
        t, i = true_current([Phase("idle", 1.0, 0.03)])
        assert t[0] == 0.0
        assert t[-1] == pytest.approx(1.0, abs=1e-3)
        assert np.allclose(i, 0.03)

    def test_start_transient_decays_from_start_level_to_level(self):
        t, i = true_current([Phase("run", 2.0, 0.3, start_level_a=0.9, tau_s=0.1)])
        assert i[0] == pytest.approx(0.9)
        assert i[-1] == pytest.approx(0.3, abs=1e-6)

    def test_phases_are_concatenated_in_order(self):
        t, i = true_current([Phase("idle", 1.0, 0.03), Phase("run", 1.0, 0.3)])
        assert np.allclose(i[t < 0.99], 0.03)
        assert np.allclose(i[t > 1.01], 0.3)

    def test_burst_load_adds_current_during_duty_cycle(self):
        phase = Phase("pwm", 1.0, 0.2, burst_a=0.1, burst_period_s=0.1, burst_duty=0.3)
        _t, i = true_current([phase])
        assert np.mean(i) == pytest.approx(0.2 + 0.1 * 0.3, abs=1e-3)


class TestSampleCapture:
    def test_slow_rate_aperture_averages_out_ripple(self):
        t, i = true_current([Phase("run", 4.0, 0.3, ripple_a=0.05, ripple_hz=100.0)])
        model = InstrumentModel(adc_rate="SLOW", noise_a=0.0)
        _ts, values, _aborted = sample_capture(t, i, model, _rng())
        assert np.ptp(values) < 0.002

    def test_fast_rate_aliases_ripple_near_multiple_of_adc_rate(self):
        # 201 Hz ripple sampled by 200 conversions/s aliases to a 1 Hz oscillation.
        t, i = true_current([Phase("run", 4.0, 0.3, ripple_a=0.05, ripple_hz=201.0)])
        model = InstrumentModel(adc_rate="FAST", noise_a=0.0)
        _ts, values, _aborted = sample_capture(t, i, model, _rng())
        assert np.ptp(values) > 0.05

    def test_latest_read_mode_returns_duplicates_when_polling_faster_than_adc(self):
        t, i = true_current([Phase("run", 3.0, 0.3)])
        model = InstrumentModel(adc_rate="SLOW", noise_a=0.001, read_mode="latest")
        _ts, values, _aborted = sample_capture(t, i, model, _rng())
        distinct_conversions = len(np.unique(values))
        assert len(values) > 5 * distinct_conversions

    def test_fresh_read_mode_waits_for_next_conversion(self):
        t, i = true_current([Phase("run", 3.0, 0.3)])
        model = InstrumentModel(adc_rate="SLOW", read_mode="fresh")
        timestamps, _values, _aborted = sample_capture(t, i, model, _rng())
        intervals = np.diff(timestamps)
        assert np.median(intervals) == pytest.approx(1 / ADC_READINGS_PER_SECOND["SLOW"], rel=0.1)

    def test_timestamps_are_strictly_increasing(self):
        t, i = true_current([Phase("run", 2.0, 0.3)])
        timestamps, _values, _aborted = sample_capture(t, i, InstrumentModel(), _rng())
        assert np.all(np.diff(timestamps) > 0)

    def test_values_are_quantized_to_range_resolution(self):
        t, i = true_current([Phase("run", 1.0, 0.123456)])
        model = InstrumentModel(adc_rate="FAST", range_a=2.0, noise_a=0.0)
        _ts, values, _aborted = sample_capture(t, i, model, _rng())
        resolution = model.resolution_a
        assert np.allclose(values / resolution, np.round(values / resolution))

    def test_over_range_readings_become_nan_markers_like_capture_loop(self):
        t, i = true_current([Phase("idle", 1.0, 0.05), Phase("spike", 0.05, 0.5), Phase("idle", 1.0, 0.05)])
        model = InstrumentModel(adc_rate="FAST", range_a=0.2, noise_a=0.0)
        _timestamps, values, _aborted = sample_capture(t, i, model, _rng())
        assert np.any(np.isnan(values))
        assert np.all(values[np.isfinite(values)] <= 0.2 * 1.2)

    def test_capture_stops_after_consecutive_over_range_polls_like_capture_loop(self):
        t, i = true_current([Phase("idle", 1.0, 0.05), Phase("spike", 0.5, 0.5), Phase("idle", 1.0, 0.05)])
        model = InstrumentModel(adc_rate="FAST", range_a=0.2, noise_a=0.0)
        timestamps, _values, _aborted = sample_capture(t, i, model, _rng())
        assert timestamps.max() < 1.1

    def test_rejects_unknown_adc_rate(self):
        with pytest.raises(ValueError):
            InstrumentModel(adc_rate="TURBO")


class TestSimulateCapture:
    def test_expected_value_is_cycle_average_of_target_phase_after_transient(self):
        phases = [
            Phase("idle", 1.0, 0.03),
            Phase("run", 3.0, 0.3, start_level_a=0.9, tau_s=0.1,
                  burst_a=0.1, burst_period_s=0.05, burst_duty=0.5, is_target=True),
            Phase("idle", 1.0, 0.03),
        ]
        capture = simulate_capture(phases, InstrumentModel(), seed=1)
        assert capture.expected_value == pytest.approx(0.35, abs=1e-3)
        assert capture.target_start_s == pytest.approx(1.0 + 5 * 0.1)
        assert capture.target_end_s == pytest.approx(4.0)

    def test_capture_aborted_by_over_range_is_flagged(self):
        phases = [Phase("idle", 1.0, 0.03), Phase("run", 3.0, 0.35, is_target=True), Phase("over", 1.0, 3.0)]
        capture = simulate_capture(phases, InstrumentModel(adc_rate="SLOW"), seed=3)
        assert capture.aborted

    def test_target_shorter_than_its_settling_time_raises(self):
        phases = [Phase("idle", 1.0, 0.03), Phase("run", 0.5, 0.3, start_level_a=0.4, tau_s=1.5, is_target=True)]
        with pytest.raises(ValueError):
            simulate_capture(phases, InstrumentModel())

    def test_phase_shorter_than_simulation_step_raises(self):
        with pytest.raises(ValueError):
            simulate_capture([Phase("run", 0.00001, 0.3, is_target=True)], InstrumentModel())

    def test_target_window_without_grid_samples_raises(self):
        phases = [
            Phase("idle", 1.0, 0.03),
            Phase("run", 0.500002, 0.3, start_level_a=0.9, tau_s=0.1000002, is_target=True),
            Phase("idle", 1.0, 0.03),
        ]
        with pytest.raises(ValueError):
            simulate_capture(phases, InstrumentModel())

    def test_requires_exactly_one_target_phase(self):
        with pytest.raises(ValueError):
            simulate_capture([Phase("idle", 1.0, 0.03)], InstrumentModel())

    def test_same_seed_is_reproducible(self):
        phases = [Phase("idle", 1.0, 0.03), Phase("run", 2.0, 0.3, is_target=True)]
        first = simulate_capture(phases, InstrumentModel(), seed=7)
        second = simulate_capture(phases, InstrumentModel(), seed=7)
        assert np.array_equal(first.values, second.values)
