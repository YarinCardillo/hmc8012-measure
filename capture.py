"""Continuous acquisition module for timestamped DCI sampling.

Polls an instrument via a protocol interface, collects timestamped readings
in a synchronous loop, and returns a frozen CaptureResult for Phase 1's analyzer.
"""

import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from hmc8012 import RangeOverflowError, ScpiError

logger = logging.getLogger(__name__)

# Consecutive failed readings (SCPI error or overflow) that end a capture.
DEFAULT_MAX_CONSECUTIVE_FAILURES = 5


class InstrumentProtocol(Protocol):
    """Minimal interface the capture module requires from an instrument."""

    def measure_fast(self) -> float: ...
    def get_function(self) -> str: ...
    def get_adc_rate(self) -> str: ...
    def get_range_auto(self, function: str) -> bool: ...


@dataclass(frozen=True)
class CaptureResult:
    """Timestamped capture data from continuous acquisition.

    Attributes:
        timestamps: Monotonic perf_counter values for each sample (relative to start).
        values: Measurement values (amps) for each sample; NaN marks a failed reading.
        sample_count: Number of valid (non-NaN) readings.
        actual_duration: Wall-clock duration of capture (seconds).
        sample_rate: Effective samples per second (sample_count / actual_duration).
        aborted_reason: Why the capture stopped before its duration, or None.
    """

    timestamps: np.ndarray
    values: np.ndarray
    sample_count: int
    actual_duration: float
    sample_rate: float
    aborted_reason: str | None = None

    def __post_init__(self) -> None:
        ts = object.__getattribute__(self, "timestamps")
        val = object.__getattribute__(self, "values")
        ts.flags.writeable = False
        val.flags.writeable = False


class CaptureConfigError(Exception):
    """Raised when instrument preconditions for capture are not met."""


class InsufficientSamplesError(Exception):
    """Raised when sample count is below the minimum threshold.

    Attributes:
        result: The partial capture, so its readings can still be saved.
    """

    def __init__(self, message: str, result: "CaptureResult") -> None:
        super().__init__(message)
        self.result = result


class CaptureAbortedError(Exception):
    """Raised when a capture stopped early on consecutive failed readings."""


def _build_result(
    timestamps: list[float],
    values: list[float],
    actual_duration: float,
    aborted_reason: str | None,
) -> CaptureResult:
    """Freeze the readings; *sample_count* counts valid readings only."""
    ts_array = np.array(timestamps, dtype=float)
    val_array = np.array(values, dtype=float)
    sample_count = int(np.count_nonzero(np.isfinite(val_array)))
    return CaptureResult(
        timestamps=ts_array,
        values=val_array,
        sample_count=sample_count,
        actual_duration=actual_duration,
        sample_rate=sample_count / actual_duration if actual_duration > 0 else 0.0,
        aborted_reason=aborted_reason,
    )


class ContinuousCapture:
    """Collects timestamped DCI readings in a synchronous polling loop."""

    def __init__(
        self,
        instrument: InstrumentProtocol,
        *,
        max_duration: float = 30.0,
        min_samples: int = 10,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
        sentinel_path: Path | None = None,
    ) -> None:
        self._instrument = instrument
        self._max_duration = max_duration
        self._min_samples = min_samples
        self._max_consecutive_failures = max_consecutive_failures
        self._sentinel_path = sentinel_path if sentinel_path is not None else Path(__file__).parent / "capture.stop"

    def run(
        self,
        deadline: float | None = None,
        sample_callback: Callable[[float, float], None] | None = None,
    ) -> CaptureResult:
        """Execute the continuous capture loop.

        Args:
            deadline: Optional absolute wall-clock time (time.monotonic()) at which
                to abort. When set, the loop exits when monotonic time >= deadline.
            sample_callback: Optional callback(t_rel, value) invoked after each
                successful sample for live plotting. Called from the capture thread.

        Returns:
            CaptureResult with collected timestamps, values, and metadata.

        Raises:
            CaptureConfigError: If instrument is not configured correctly.
            InsufficientSamplesError: If fewer than min_samples were collected.
        """
        self._verify_instrument_state()
        self._cleanup_sentinel()
        start_time = time.perf_counter()
        timestamps, values, aborted_reason = self._acquire(start_time, deadline, sample_callback)
        self._cleanup_sentinel()
        result = _build_result(timestamps, values, time.perf_counter() - start_time, aborted_reason)
        if result.sample_count < self._min_samples:
            raise InsufficientSamplesError(
                f"Captured {result.sample_count} samples, minimum is {self._min_samples}", result
            )
        return result

    def _acquire(
        self,
        start_time: float,
        deadline: float | None,
        sample_callback: Callable[[float, float], None] | None,
    ) -> tuple[list[float], list[float], str | None]:
        """Poll until duration, deadline, stop request or too many consecutive failures.

        A failed reading is kept as a NaN marker at its time, so the analysis
        knows a reading (often an overflowing peak) is missing there.
        """
        timestamps: list[float] = []
        values: list[float] = []
        consecutive_failures = 0
        while not self._is_finished(start_time, deadline):
            try:
                value = self._instrument.measure_fast()
                consecutive_failures = 0
            except (ScpiError, RangeOverflowError) as exc:
                timestamps.append(time.perf_counter() - start_time)
                values.append(math.nan)
                consecutive_failures += 1
                logger.warning("Sample %d failed: %s", len(values) - 1, exc)
                if consecutive_failures >= self._max_consecutive_failures:
                    logger.error("Aborting: %d consecutive failures", consecutive_failures)
                    return timestamps, values, (
                        f"Stopped after {consecutive_failures} consecutive failed readings "
                        f"at {timestamps[-1]:.2f} s: {exc}"
                    )
                continue
            timestamps.append(time.perf_counter() - start_time)
            values.append(value)
            if sample_callback is not None:
                sample_callback(timestamps[-1], value)
        return timestamps, values, None

    def _is_finished(self, start_time: float, deadline: float | None) -> bool:
        if time.perf_counter() - start_time >= self._max_duration:
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return True
        return self._should_stop()

    def _verify_instrument_state(self) -> None:
        """Verify instrument is configured for DCI capture with valid ADC rate and range locked."""
        func = self._instrument.get_function()
        if func != "CURR":
            raise CaptureConfigError(
                f"Expected DCI function (CURR), got '{func}'. "
                "Call set_function('dci') before capture."
            )
        valid_rates = ("FAST", "SLOW", "MED")
        adc_rate = self._instrument.get_adc_rate()
        if adc_rate not in valid_rates:
            raise CaptureConfigError(
                f"Expected ADC rate one of {valid_rates}, got '{adc_rate}'. "
                "Call set_adc_rate('FAST'|'SLOW'|'MED') before capture."
            )
        if self._instrument.get_range_auto("dci"):
            raise CaptureConfigError(
                "Auto-range is ON. Lock range with set_range('dci', '<value>') "
                "before capture."
            )

    def _should_stop(self) -> bool:
        """Return True if sentinel file exists, signaling stop request."""
        return self._sentinel_path.exists()

    def _cleanup_sentinel(self) -> None:
        """Delete sentinel file if it exists."""
        try:
            self._sentinel_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete sentinel file: %s", self._sentinel_path)
