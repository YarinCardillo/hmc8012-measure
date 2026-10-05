"""Movement-current analysis of a captured device current.

Reports the mean current of the movement that lies between the deltastep
peaks of a capture: deltastep strokes, movement, deltastep strokes, idle.
The capture may start during the strokes; it must end at idle. Pure
computation on numpy arrays, no instrument interaction.

Pipeline:
    1. Validate: aligned 1-D arrays, strictly increasing timestamps. NaN/inf
       readings and overflow sentinels (either sign) are invalid; too many of
       them reject the capture.
    2. Conversions: the instrument answers READ? with its last conversion, so
       consecutive equal readings are one conversion.
    3. Idle: the steady level at the end of the capture. It must be the
       lowest steady level of the capture (the device returns to idle); a
       single conversion below it, as right after a deltastep stroke, is not.
    4. Peaks: conversions above the midpoint between idle and the highest
       conversion; invalid readings count as peaks.
    5. Movement: stretches of consecutive conversions between idle (plus two
       tolerances) and the peaks. The conversion that touches a peak at
       either end averages the movement with the peak and is left out;
       acceleration and braking stay in. Exactly one stretch may last at
       least MIN_MOVEMENT_S.
    6. Report the time-weighted mean of the movement's conversions.
"""

import logging
import math
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# Instrument overflow sentinel (matches HMC8012.OVERFLOW_SENTINEL).
OVERFLOW_SENTINEL = 9.90000000e37
# The movement starts this many tolerances above idle.
RUN_THRESHOLD_TOLERANCES = 2.0
# Peaks start at this fraction of the way from idle to the highest conversion.
PEAK_THRESHOLD_FRACTION = 0.5
# Steady idle required at the end of the capture.
MIN_IDLE_S = 0.25
# Shortest movement, edges left out: longer than one SLOW conversion (about 0.2 s).
MIN_MOVEMENT_S = 0.3


@dataclass(frozen=True)
class AnalysisConfig:
    """Tuning of the movement analysis.

    Attributes:
        rel_tolerance: Relative tolerance on the movement mean (0.02 = 2%).
        abs_tolerance_a: Absolute floor of the tolerance (amperes).
        max_invalid_fraction: Maximum fraction of NaN/inf/overflow samples.
    """

    rel_tolerance: float = 0.02
    abs_tolerance_a: float = 0.002
    max_invalid_fraction: float = 0.20

    def __post_init__(self) -> None:
        for name in ("rel_tolerance", "abs_tolerance_a"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"AnalysisConfig.{name} must be finite and > 0, got {value}")
        if not 0.0 <= self.max_invalid_fraction < 1.0:
            raise ValueError(
                f"AnalysisConfig.max_invalid_fraction must be in [0, 1), got {self.max_invalid_fraction}"
            )

    def tolerance(self, level: float) -> float:
        """Tolerance around *level* (amperes)."""
        return max(self.abs_tolerance_a, self.rel_tolerance * abs(level))


@dataclass(frozen=True)
class AnalysisResult:
    """Result of the movement analysis.

    Attributes:
        stable_value: Mean current of the movement's conversions (amperes).
        stable_std_dev: Standard deviation of those conversions.
        standard_error: Uncertainty (1 sigma) of *stable_value* (amperes).
        start_time: First reading of the averaged conversions (seconds).
        end_time: Last reading of the averaged conversions (seconds).
        start_index: First sample of the averaged conversions (original arrays).
        end_index: One past their last sample (original arrays).
        samples_used: Number of averaged conversions.
        idle_level: Current of the steady idle at capture end (amperes).
        run_start_time: First reading of the movement stretch, edges included (seconds).
        run_end_time: Last reading of the movement stretch, edges included (seconds).
    """

    stable_value: float
    stable_std_dev: float
    standard_error: float
    start_time: float
    end_time: float
    start_index: int
    end_index: int
    samples_used: int
    idle_level: float
    run_start_time: float
    run_end_time: float


class AnalysisError(Exception):
    """Base class for signal analysis errors."""


class SignalNotSettledError(AnalysisError):
    """Raised when the capture has no movement of at least MIN_MOVEMENT_S between the peaks."""


class AmbiguousRunError(AnalysisError):
    """Raised when the capture holds more than one movement."""


class InvalidCaptureError(AnalysisError):
    """Raised when the capture arrays are malformed, mostly invalid, or not idle at the end."""


@dataclass(frozen=True)
class _Conversions:
    """Consecutive equal readings merged into conversions; invalid readings are +inf."""

    times: np.ndarray
    values: np.ndarray
    first_sample: np.ndarray
    end_sample: np.ndarray
    end_time: np.ndarray


def analyze_waveform(
    timestamps: np.ndarray,
    values: np.ndarray,
    config: AnalysisConfig = AnalysisConfig(),
) -> AnalysisResult:
    """Extract the mean movement current from a captured waveform.

    Args:
        timestamps: 1-D sample times in seconds, strictly increasing.
        values: 1-D current readings in amperes, same length.
        config: Analysis tuning.

    Returns:
        :class:`AnalysisResult` for the movement's conversions.

    Raises:
        InvalidCaptureError: Malformed arrays, too many invalid samples, or no
            steady idle at capture end.
        SignalNotSettledError: No movement of at least MIN_MOVEMENT_S.
        AmbiguousRunError: More than one movement.
    """
    times, currents, is_valid = _checked_arrays(timestamps, values, config)
    conversions = _conversions(times, currents, is_valid)
    idle_level = _idle_level(times, currents, is_valid, conversions, config)
    run, movement = _movement(conversions, idle_level, config)
    return _result(times, conversions, run, movement, idle_level)


def _checked_arrays(
    timestamps: np.ndarray,
    values: np.ndarray,
    config: AnalysisConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Check array shape, timing and invalid fraction; return ``(times, currents, is_valid)``."""
    try:
        times = np.asarray(timestamps, dtype=float)
        currents = np.asarray(values, dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidCaptureError(f"timestamps and values must be numeric: {exc}") from exc
    if times.ndim != 1 or times.shape != currents.shape:
        raise InvalidCaptureError(
            f"timestamps and values must be 1-D and equally long, got {times.shape} and {currents.shape}"
        )
    if len(times) < 2:
        raise InvalidCaptureError(f"Need at least 2 samples, got {len(times)}")
    if not np.all(np.isfinite(times)) or np.any(np.diff(times) <= 0):
        raise InvalidCaptureError("timestamps must be finite and strictly increasing")
    is_valid = np.isfinite(currents) & (np.abs(currents) < OVERFLOW_SENTINEL)
    invalid_count = len(currents) - int(np.count_nonzero(is_valid))
    if invalid_count / len(currents) > config.max_invalid_fraction:
        raise InvalidCaptureError(
            f"{invalid_count}/{len(currents)} samples are NaN, inf or overflow "
            f"({invalid_count / len(currents):.1%} > {config.max_invalid_fraction:.0%})"
        )
    if invalid_count:
        logger.warning("Treating %d invalid samples (NaN, inf or overflow) as peaks", invalid_count)
    return times, currents, is_valid


def _conversions(times: np.ndarray, currents: np.ndarray, is_valid: np.ndarray) -> _Conversions:
    """Merge consecutive equal readings; each conversion lasts until the next one starts."""
    values = np.where(is_valid, currents, math.inf)
    first = np.flatnonzero(np.concatenate(([True], values[1:] != values[:-1])))
    end = np.append(first[1:], len(values))
    capture_end = times[-1] + (times[-1] - times[-2])
    return _Conversions(
        times=times[first],
        values=values[first],
        first_sample=first,
        end_sample=end,
        end_time=np.append(times[first[1:]], capture_end),
    )


def _idle_level(
    times: np.ndarray,
    currents: np.ndarray,
    is_valid: np.ndarray,
    conversions: _Conversions,
    config: AnalysisConfig,
) -> float:
    """Level of the steady idle that must end the capture, the lowest steady level of the capture."""
    start = len(conversions.values) - 1
    low = high = conversions.values[start]
    while start > 0 and _fits_band(min(low, conversions.values[start - 1]), max(high, conversions.values[start - 1]), config):
        start -= 1
        low, high = min(low, conversions.values[start]), max(high, conversions.values[start])
    first_sample = conversions.first_sample[start]
    duration = times[-1] - times[first_sample]
    idle = float(np.mean(currents[first_sample:][is_valid[first_sample:]])) if math.isfinite(high) else math.inf
    lowest = _lowest_steady_level(conversions)
    if duration < MIN_IDLE_S or idle - lowest > config.tolerance(lowest):
        raise InvalidCaptureError(
            f"Capture must end with the device idle: need {MIN_IDLE_S} s of steady current at the lowest steady "
            f"level of the capture ({lowest:.4f} A), got {max(duration, 0.0):.2f} s at {idle:.4f} A. "
            "Let the capture run until the device is back at idle (--auto does)."
        )
    return idle


def _lowest_steady_level(conversions: _Conversions) -> float:
    """Lowest level held for at least MIN_IDLE_S: a dip of a single conversion is not a level."""
    lowest = math.inf
    for first in range(len(conversions.values)):
        last = int(np.searchsorted(conversions.end_time, conversions.times[first] + MIN_IDLE_S))
        if last == len(conversions.values):
            break
        lowest = min(lowest, float(np.max(conversions.values[first:last + 1])))
    return lowest


def _fits_band(low: float, high: float, config: AnalysisConfig) -> bool:
    return math.isfinite(high) and high - low <= 2.0 * config.tolerance((high + low) / 2.0)


def _movement(
    conversions: _Conversions,
    idle_level: float,
    config: AnalysisConfig,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Conversion bounds ``[start, end)`` of the movement stretch and of its averaged conversions."""
    floor = idle_level + RUN_THRESHOLD_TOLERANCES * config.tolerance(idle_level)
    finite = conversions.values[np.isfinite(conversions.values)]
    ceiling = idle_level + PEAK_THRESHOLD_FRACTION * (float(np.max(finite)) - idle_level)
    is_candidate = (conversions.values > floor) & (conversions.values < ceiling)
    movements = []
    for run in _stretches(is_candidate):
        kept = _without_peak_edges(conversions.values, run, ceiling)
        if kept[1] > kept[0] and conversions.end_time[kept[1] - 1] - conversions.times[kept[0]] >= MIN_MOVEMENT_S:
            movements.append((run, kept))
    if not movements:
        raise SignalNotSettledError(
            f"No movement: no stretch of at least {MIN_MOVEMENT_S} s between idle ({idle_level:.4f} A, "
            f"movement above {floor:.4f} A) and the peaks (from {ceiling:.4f} A). A movement shorter than "
            "about 3 conversions needs a faster ADC rate (--rate MED)."
        )
    if len(movements) > 1:
        spans = ", ".join(f"{conversions.times[s]:.2f}-{conversions.end_time[e - 1]:.2f} s" for (s, e), _ in movements)
        raise AmbiguousRunError(f"{len(movements)} separate movements ({spans}); expected one movement per capture.")
    return movements[0]


def _stretches(mask: np.ndarray) -> list[tuple[int, int]]:
    """Bounds ``[start, end)`` of the runs of consecutive True values."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask.astype(np.int8), [0]))))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def _without_peak_edges(values: np.ndarray, run: tuple[int, int], ceiling: float) -> tuple[int, int]:
    """Drop the conversion at either end that touches a peak: it averages the movement with the peak."""
    start, end = run
    if start > 0 and values[start - 1] >= ceiling:
        start += 1
    if end < len(values) and values[end] >= ceiling:
        end -= 1
    return start, end


def _result(
    times: np.ndarray,
    conversions: _Conversions,
    run: tuple[int, int],
    movement: tuple[int, int],
    idle_level: float,
) -> AnalysisResult:
    """Time-weighted mean of the movement's conversions: a merged pair of equal conversions counts twice."""
    first, last = movement
    readings = conversions.values[first:last]
    durations = conversions.end_time[first:last] - conversions.times[first:last]
    mean = float(np.average(readings, weights=durations))
    standard_error = float(np.std(readings, ddof=1)) / math.sqrt(len(readings)) if len(readings) > 1 else 0.0
    return AnalysisResult(
        stable_value=mean,
        stable_std_dev=float(np.std(readings)),
        standard_error=standard_error,
        start_time=float(conversions.times[first]),
        end_time=float(times[conversions.end_sample[last - 1] - 1]),
        start_index=int(conversions.first_sample[first]),
        end_index=int(conversions.end_sample[last - 1]),
        samples_used=last - first,
        idle_level=idle_level,
        run_start_time=float(conversions.times[run[0]]),
        run_end_time=float(times[conversions.end_sample[run[1] - 1] - 1]),
    )
