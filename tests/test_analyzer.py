"""Unit tests for the signal analysis engine.

All tests are in RED state: they call real functions that currently raise
``NotImplementedError``.  Once Plan 01-02 implements the functions, these
tests define the acceptance criteria for each requirement.
"""

import dataclasses

import numpy as np
import pytest

from analyzer import (
    AnalysisResult,
    InvalidCaptureError,
    NoPeaksDetectedError,
    PeakInfo,
    SignalNotSettledError,
    analyze_waveform,
    detect_peaks,
    filter_overflows,
    find_settling_point,
)


# -----------------------------------------------------------------------
# ANLY-01  Peak detection
# -----------------------------------------------------------------------

class TestDetectPeaks:
    """Requirement ANLY-01: peak detection identifies the motor startup spike."""

    def test_detect_peaks_single_spike(self, make_motor_waveform):
        """Single-peak waveform: detect_peaks returns one PeakInfo with the
        correct index (near peak_time) and amplitude (near peak_current)."""
        peak_time = 0.5
        peak_current = 3.0
        sample_rate = 100.0

        timestamps, values = make_motor_waveform(
            peak_time=peak_time,
            peak_current=peak_current,
            sample_rate=sample_rate,
        )

        peaks = detect_peaks(values, prominence_sigma=3.0, min_distance=50)

        assert len(peaks) >= 1
        # The detected peak index should be near the injected peak position.
        expected_idx = int(peak_time * sample_rate)
        assert abs(peaks[0].index - expected_idx) <= 5
        # Amplitude should be close to peak_current (within noise margin).
        assert peaks[0].amplitude == pytest.approx(peak_current, abs=0.1)

    def test_detect_peaks_flat_signal_raises(self, make_flat_waveform):
        """Flat waveform (no peaks): NoPeaksDetectedError must be raised."""
        _timestamps, values = make_flat_waveform()

        with pytest.raises(NoPeaksDetectedError):
            detect_peaks(values, prominence_sigma=3.0, min_distance=50)


# -----------------------------------------------------------------------
# ANLY-02  Stable value extraction
# -----------------------------------------------------------------------

class TestStableValue:
    """Requirement ANLY-02: stable value extraction returns post-peak mean."""

    def test_stable_value_correct_mean(self, make_motor_waveform):
        """Full analyze_waveform pipeline: stable_value matches the
        known stable_current within tolerance."""
        stable_current = 0.5
        timestamps, values = make_motor_waveform(stable_current=stable_current)

        result = analyze_waveform(timestamps, values)

        assert result.stable_value == pytest.approx(stable_current, abs=0.05)
        assert result.stable_std_dev < 0.05
        assert result.samples_used > 0

    def test_never_settles_raises(self, make_motor_waveform):
        """Waveform with extremely high noise that never settles below
        the default std_threshold must raise SignalNotSettledError."""
        # Noise std (1.0) far exceeds the default settling threshold (0.01).
        timestamps, values = make_motor_waveform(noise_std=1.0)

        with pytest.raises(SignalNotSettledError):
            analyze_waveform(timestamps, values)


# -----------------------------------------------------------------------
# ANLY-03  Configurable settling parameters
# -----------------------------------------------------------------------

class TestConfigurableParams:
    """Requirement ANLY-03: settling criteria are configurable."""

    def test_configurable_settling_params(self, make_motor_waveform):
        """Changing settling_window and settling_threshold must produce
        different settling_sample_index or stable_value results."""
        timestamps, values = make_motor_waveform()

        result_tight = analyze_waveform(
            timestamps, values,
            settling_window=10,
            settling_threshold=0.02,
        )
        result_strict = analyze_waveform(
            timestamps, values,
            settling_window=40,
            settling_threshold=0.005,
        )

        # Different parameters should yield different settling points.
        assert result_tight.settling_sample_index != result_strict.settling_sample_index


# -----------------------------------------------------------------------
# ANLY-04  Multi-peak handling
# -----------------------------------------------------------------------

class TestMultiPeak:
    """Requirement ANLY-04: multiple peaks handled, anchor on last peak."""

    def test_multi_peak_anchors_last(self, make_multi_peak_waveform):
        """Two-peak waveform: anchor_peak_index must point to the last
        peak in the peaks tuple."""
        timestamps, values = make_multi_peak_waveform(
            peaks=[(0.5, 3.0), (2.5, 2.5)],
            duration=6.0,
        )

        result = analyze_waveform(timestamps, values)

        assert result.anchor_peak_index == len(result.peaks) - 1

    def test_multi_peak_reports_all(self, make_multi_peak_waveform):
        """Two-peak waveform: both peaks must appear in results with
        correct approximate amplitudes."""
        timestamps, values = make_multi_peak_waveform(
            peaks=[(0.5, 3.0), (2.5, 2.5)],
            duration=6.0,
        )

        result = analyze_waveform(timestamps, values)

        assert len(result.peaks) == 2
        assert result.peaks[0].amplitude == pytest.approx(3.0, abs=0.2)
        assert result.peaks[1].amplitude == pytest.approx(2.5, abs=0.2)


# -----------------------------------------------------------------------
# Overflow filtering
# -----------------------------------------------------------------------

class TestOverflowFiltering:
    """Overflow sentinel removal and capture validity checks."""

    def test_overflow_filtering(
        self, make_motor_waveform, make_overflow_waveform
    ):
        """A few overflow sentinels injected: filter_overflows returns
        arrays without sentinels and with reduced length."""
        timestamps, values = make_motor_waveform()
        overflow_indices = [10, 50, 100]
        ts_ovf, vals_ovf = make_overflow_waveform(
            timestamps, values, indices=overflow_indices
        )

        ts_filt, vals_filt = filter_overflows(ts_ovf, vals_ovf)

        assert len(ts_filt) == len(timestamps) - len(overflow_indices)
        assert len(vals_filt) == len(ts_filt)
        sentinel = 9.90000000e+37
        assert not np.any(vals_filt >= sentinel)

    def test_excessive_overflow_raises(
        self, make_motor_waveform, make_overflow_waveform
    ):
        """More than 20% overflow sentinels: InvalidCaptureError must
        be raised."""
        timestamps, values = make_motor_waveform()
        ts_ovf, vals_ovf = make_overflow_waveform(
            timestamps, values, fraction=0.30
        )

        with pytest.raises(InvalidCaptureError):
            filter_overflows(ts_ovf, vals_ovf)

    def test_overflow_filter_keeps_alignment(
        self, make_motor_waveform, make_overflow_waveform
    ):
        """After filtering, timestamps and values must remain aligned:
        a known non-overflow value at a given position must still
        correspond to its original timestamp."""
        timestamps, values = make_motor_waveform()
        # Inject overflows at known positions; index 200 is NOT an overflow.
        overflow_indices = [10, 50, 100]
        ts_ovf, vals_ovf = make_overflow_waveform(
            timestamps, values, indices=overflow_indices
        )

        ts_filt, vals_filt = filter_overflows(ts_ovf, vals_ovf)

        assert len(ts_filt) == len(vals_filt)
        # The original value at index 200 should survive intact.
        original_ts_200 = timestamps[200]
        original_val_200 = values[200]
        # After removing 3 items before index 200 (indices 10,50,100),
        # it should appear at position 200-3=197 in the filtered arrays.
        assert ts_filt[197] == pytest.approx(original_ts_200)
        assert vals_filt[197] == pytest.approx(original_val_200)


# -----------------------------------------------------------------------
# Edge cases
# -----------------------------------------------------------------------

class TestEdgeCases:
    """Additional edge-case tests for robustness."""

    def test_analyze_waveform_returns_frozen_dataclass(
        self, make_motor_waveform
    ):
        """The returned AnalysisResult must be frozen (immutable)."""
        timestamps, values = make_motor_waveform()

        result = analyze_waveform(timestamps, values)

        assert isinstance(result, AnalysisResult)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.stable_value = 999.0  # type: ignore[misc]

    def test_insufficient_post_peak_data_raises(self, make_motor_waveform):
        """Peak near end of capture (fewer samples than settling_window
        after the last peak): SignalNotSettledError must be raised."""
        timestamps, values = make_motor_waveform(
            duration=1.0,
            peak_time=0.9,
            settling_time=0.5,
            sample_rate=100.0,
        )

        with pytest.raises(SignalNotSettledError):
            analyze_waveform(
                timestamps, values,
                settling_window=20,
            )
