"""Continuous acquisition module for timestamped DCI sampling.

Polls an instrument via a protocol interface, collects timestamped readings
in a synchronous loop, and returns a frozen CaptureResult for Phase 1's analyzer.
"""

import logging
import math
import time
from dataclasses import dataclass
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
    ) -> None:
        self._instrument = instrument
        self._max_duration = max_duration
        self._min_samples = min_samples
        self._max_consecutive_failures = max_consecutive_failures

    def run(
        self,
        deadline: float | None = None,
        on_sample: Callable[[float, float], None] | None = None,
        should_stop: Callable[[float, float], bool] | None = None,
    ) -> CaptureResult:
        """Execute the continuous capture loop.

        Args:
            deadline: Optional absolute wall-clock time (time.monotonic()) at which
                to abort. When set, the loop exits when monotonic time >= deadline.
            on_sample: Optional callback receiving ``(time_s, value)`` for every
                reading as soon as it is taken, NaN for a failed one (live plot).
            should_stop: Optional condition receiving ``(time_s, value)`` for every
                reading; the capture ends at the first reading it returns True for
                (auto-stop). Not an abort: the capture completes normally.

        Returns:
            CaptureResult with collected timestamps, values, and metadata.

        Raises:
            CaptureConfigError: If instrument is not configured correctly.
            InsufficientSamplesError: If fewer than min_samples were collected.
        """
        self._verify_instrument_state()
        start_time = time.perf_counter()
        timestamps, values, aborted_reason = self._acquire(start_time, deadline, on_sample, should_stop)
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
        on_sample: Callable[[float, float], None] | None,
        should_stop: Callable[[float, float], bool] | None,
    ) -> tuple[list[float], list[float], str | None]:
        """Poll until the duration or deadline ends, the stop condition holds, or too many consecutive failures.

        A failed reading is kept as a NaN marker at its time, so the analysis
        knows a reading (often an overflowing peak) is missing there.
        """
        timestamps: list[float] = []
        values: list[float] = []
        consecutive_failures = 0
        while not self._is_finished(start_time, deadline):
            try:
                value, failure = self._instrument.measure_fast(), None
            except (ScpiError, RangeOverflowError) as exc:
                value, failure = math.nan, exc
            timestamps.append(time.perf_counter() - start_time)
            values.append(value)
            if on_sample is not None:
                on_sample(timestamps[-1], value)
            if should_stop is not None and should_stop(timestamps[-1], value):
                break
            if failure is None:
                consecutive_failures = 0
                continue
            consecutive_failures += 1
            logger.warning("Sample %d failed: %s", len(values) - 1, failure)
            if consecutive_failures >= self._max_consecutive_failures:
                logger.error("Aborting: %d consecutive failures", consecutive_failures)
                return timestamps, values, (
                    f"Stopped after {consecutive_failures} consecutive failed readings "
                    f"at {timestamps[-1]:.2f} s: {failure}"
                )
        return timestamps, values, None

    def _is_finished(self, start_time: float, deadline: float | None) -> bool:
        if time.perf_counter() - start_time >= self._max_duration:
            return True
        return deadline is not None and time.monotonic() >= deadline

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
