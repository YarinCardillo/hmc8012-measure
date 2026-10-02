"""Tests for continuous capture module (InstrumentProtocol, CaptureResult, ContinuousCapture)."""

import time

import numpy as np
import pytest

from capture import (
    CaptureConfigError,
    CaptureResult,
    ContinuousCapture,
    InsufficientSamplesError,
)
from hmc8012 import ScpiError


class TestPreconditions:
    """ACQU-03, ACQU-04: instrument state verification before capture."""

    def test_rejects_wrong_function(self, make_fake_instrument):
        instrument = make_fake_instrument([1.0], function="VOLT")
        capture = ContinuousCapture(instrument)
        with pytest.raises(CaptureConfigError):
            capture.run()

    def test_rejects_wrong_adc_rate(self, make_fake_instrument):
        instrument = make_fake_instrument([1.0], adc_rate="INVALID")
        capture = ContinuousCapture(instrument)
        with pytest.raises(CaptureConfigError):
            capture.run()

    def test_rejects_auto_range_on(self, make_fake_instrument):
        instrument = make_fake_instrument([1.0], range_auto=True)
        capture = ContinuousCapture(instrument)
        with pytest.raises(CaptureConfigError):
            capture.run()

    def test_accepts_correct_config(self, make_fake_instrument):
        instrument = make_fake_instrument([1.0], function="CURR", adc_rate="FAST", range_auto=False)
        capture = ContinuousCapture(instrument)
        with pytest.raises(InsufficientSamplesError):
            capture.run()


class TestCaptureLoop:
    """ACQU-01: capture collects readings until stop condition."""

    def test_collects_readings_until_exhausted(self, make_fake_instrument):
        readings = [float(i) for i in range(20)]
        instrument = make_fake_instrument(readings)
        capture = ContinuousCapture(instrument, max_duration=999.0)
        result = capture.run()
        assert result.sample_count == 20

    def test_respects_max_duration(self):
        # Instrument that sleeps so wall-clock exceeds max_duration before exhausting.
        readings = [1.0] * 1000

        class SlowInstrument:
            def __init__(self):
                self._readings = readings
                self._index = 0

            def measure_fast(self) -> float:
                if self._index >= len(self._readings):
                    raise ScpiError("No more readings")
                time.sleep(0.03)
                value = self._readings[self._index]
                self._index += 1
                return value

            def get_function(self) -> str:
                return "CURR"

            def get_adc_rate(self) -> str:
                return "FAST"

            def get_range_auto(self, function: str) -> bool:
                return False

        instrument = SlowInstrument()
        capture = ContinuousCapture(instrument, max_duration=0.1, min_samples=2)
        result = capture.run()
        assert result.sample_count < 1000

    def test_capture_result_fields(self, make_fake_instrument):
        instrument = make_fake_instrument([0.5, 0.6, 0.5])
        capture = ContinuousCapture(instrument, max_duration=10.0, min_samples=2)
        result = capture.run()
        assert result.sample_count == 3
        assert result.actual_duration > 0
        assert result.sample_rate > 0


class TestTimestamps:
    """ACQU-02: timestamps are monotonic and relative to start."""

    def test_timestamps_monotonically_increasing(self, make_fake_instrument):
        instrument = make_fake_instrument([0.1 * i for i in range(15)])
        capture = ContinuousCapture(instrument, max_duration=10.0)
        result = capture.run()
        for i in range(len(result.timestamps) - 1):
            assert result.timestamps[i + 1] > result.timestamps[i]

    def test_timestamps_are_relative_to_start(self, make_fake_instrument):
        instrument = make_fake_instrument([1.0, 2.0, 3.0])
        capture = ContinuousCapture(instrument, max_duration=10.0, min_samples=2)
        result = capture.run()
        assert result.timestamps[0] < 0.1

    def test_timestamps_array_length_matches_values(self, make_fake_instrument):
        instrument = make_fake_instrument([1.0] * 10)
        capture = ContinuousCapture(instrument, max_duration=10.0)
        result = capture.run()
        assert len(result.timestamps) == len(result.values)
        # The five failed readings that end the capture stay as NaN markers.
        assert result.sample_count == int(np.count_nonzero(np.isfinite(result.values))) == 10


class TestSampleCallback:
    """Every reading reaches the callback as soon as it is taken (live plot)."""

    def test_callback_receives_every_reading_including_failed_ones(self, make_fake_instrument):
        instrument = make_fake_instrument([1.0, 2.0, 3.0])
        received = []
        result = ContinuousCapture(instrument, max_duration=10.0, min_samples=2).run(
            on_sample=lambda time_s, value: received.append((time_s, value))
        )
        assert [time_s for time_s, _ in received] == list(result.timestamps)
        assert [value for _, value in received][:3] == [1.0, 2.0, 3.0]
        assert all(np.isnan(value) for _, value in received[3:])
        assert len(received) == len(result.values)


class TestErrorHandling:
    """Skip failed samples; abort on consecutive failures; insufficient samples."""

    def test_failed_readings_are_kept_as_nan_markers(self, make_fake_instrument):
        class FailingInstrument:
            def __init__(self):
                self._readings = [1.0, 2.0, 3.0, 4.0, 5.0]
                self._index = 0

            def measure_fast(self) -> float:
                if self._index >= len(self._readings):
                    raise ScpiError("No more readings")
                if self._index == 2:
                    self._index += 1
                    raise ScpiError("Simulated failure")
                value = self._readings[self._index]
                self._index += 1
                return value

            def get_function(self) -> str:
                return "CURR"

            def get_adc_rate(self) -> str:
                return "FAST"

            def get_range_auto(self, function: str) -> bool:
                return False

        instrument = FailingInstrument()
        capture = ContinuousCapture(instrument, max_duration=10.0, min_samples=2)
        result = capture.run()
        assert result.sample_count == 4
        assert np.isnan(result.values[2])
        assert list(result.values[np.isfinite(result.values)]) == [1.0, 2.0, 4.0, 5.0]
        assert len(result.timestamps) == len(result.values)

    def test_aborts_on_consecutive_failures(self, make_fake_instrument):
        class AllFailingInstrument:
            def measure_fast(self) -> float:
                raise ScpiError("Always fails")

            def get_function(self) -> str:
                return "CURR"

            def get_adc_rate(self) -> str:
                return "FAST"

            def get_range_auto(self, function: str) -> bool:
                return False

        instrument = AllFailingInstrument()
        capture = ContinuousCapture(instrument, max_duration=1.0, max_consecutive_failures=3)
        with pytest.raises(InsufficientSamplesError):
            capture.run()

    def test_abort_on_consecutive_failures_is_recorded(self, make_fake_instrument):
        instrument = make_fake_instrument([0.35] * 20)
        capture = ContinuousCapture(instrument, max_duration=5.0, max_consecutive_failures=3)
        result = capture.run()
        assert result.sample_count == 20
        assert "3 consecutive failed readings" in result.aborted_reason

    def test_completed_capture_has_no_abort_reason(self):
        class SteadyInstrument:
            def measure_fast(self) -> float:
                return 0.35

            def get_function(self) -> str:
                return "CURR"

            def get_adc_rate(self) -> str:
                return "SLOW"

            def get_range_auto(self, function: str) -> bool:
                return False

        result = ContinuousCapture(SteadyInstrument(), max_duration=0.05).run()
        assert result.aborted_reason is None

    def test_insufficient_samples_error_carries_partial_capture(self, make_fake_instrument):
        instrument = make_fake_instrument([0.35, 0.35, 0.35])
        capture = ContinuousCapture(instrument, max_duration=10.0, min_samples=10)
        with pytest.raises(InsufficientSamplesError) as excinfo:
            capture.run()
        assert excinfo.value.result.sample_count == 3

    def test_insufficient_samples_raises(self, make_fake_instrument):
        instrument = make_fake_instrument([1.0, 2.0, 3.0])
        capture = ContinuousCapture(instrument, max_duration=10.0, min_samples=10)
        with pytest.raises(InsufficientSamplesError):
            capture.run()


class TestCaptureResultImmutability:
    """CaptureResult arrays are read-only (writeable=False)."""

    def test_arrays_not_writeable(self):
        import numpy as np

        ts = np.array([0.0, 1.0])
        val = np.array([0.5, 0.6])
        result = CaptureResult(
            timestamps=ts,
            values=val,
            sample_count=2,
            actual_duration=1.0,
            sample_rate=2.0,
        )
        assert result.timestamps.flags.writeable is False
        assert result.values.flags.writeable is False
