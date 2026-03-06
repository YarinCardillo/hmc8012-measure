"""Continuous acquisition module for timestamped DCI sampling.

Polls an instrument via a protocol interface, collects timestamped readings
in a synchronous loop, and returns a frozen CaptureResult for Phase 1's analyzer.
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from hmc8012 import RangeOverflowError, ScpiError

logger = logging.getLogger(__name__)


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
        values: Measurement values (amps) for each sample.
        sample_count: Number of successfully captured samples.
        actual_duration: Wall-clock duration of capture (seconds).
        sample_rate: Effective samples per second (sample_count / actual_duration).
    """

    timestamps: np.ndarray
    values: np.ndarray
    sample_count: int
    actual_duration: float
    sample_rate: float

    def __post_init__(self) -> None:
        ts = object.__getattribute__(self, "timestamps")
        val = object.__getattribute__(self, "values")
        ts.flags.writeable = False
        val.flags.writeable = False


class CaptureConfigError(Exception):
    """Raised when instrument preconditions for capture are not met."""


class InsufficientSamplesError(Exception):
    """Raised when sample count is below the minimum threshold."""


class ContinuousCapture:
    """Collects timestamped DCI readings in a synchronous polling loop."""

    def __init__(
        self,
        instrument: InstrumentProtocol,
        *,
        max_duration: float = 30.0,
        min_samples: int = 10,
        max_consecutive_failures: int = 5,
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

        timestamps: list[float] = []
        values: list[float] = []
        consecutive_failures = 0
        start_time = time.perf_counter()

        while True:
            elapsed = time.perf_counter() - start_time
            if elapsed >= self._max_duration:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            if self._should_stop():
                break

            try:
                value = self._instrument.measure_fast()
                t = time.perf_counter() - start_time
                timestamps.append(t)
                values.append(value)
                if sample_callback is not None:
                    sample_callback(t, value)
                consecutive_failures = 0
            except (ScpiError, RangeOverflowError) as exc:
                consecutive_failures += 1
                logger.warning("Sample %d failed: %s", len(values), exc)
                if consecutive_failures >= self._max_consecutive_failures:
                    logger.error(
                        "Aborting: %d consecutive failures", consecutive_failures
                    )
                    break

        self._cleanup_sentinel()
        actual_duration = time.perf_counter() - start_time
        sample_count = len(values)

        if sample_count < self._min_samples:
            raise InsufficientSamplesError(
                f"Captured {sample_count} samples, minimum is {self._min_samples}"
            )

        sample_rate = sample_count / actual_duration if actual_duration > 0 else 0.0
        ts_array = np.array(timestamps, dtype=float)
        val_array = np.array(values, dtype=float)
        ts_array.flags.writeable = False
        val_array.flags.writeable = False

        return CaptureResult(
            timestamps=ts_array,
            values=val_array,
            sample_count=sample_count,
            actual_duration=actual_duration,
            sample_rate=sample_rate,
        )

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
