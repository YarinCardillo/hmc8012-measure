"""Running-current analysis of a captured device current.

Reports the mean current the device draws over its run, in a capture shaped
idle, start, run, stop, idle. Pure computation on numpy arrays, no instrument
interaction.

Pipeline:
    1. Validate: aligned 1-D arrays, strictly increasing timestamps. NaN/inf
       readings and overflow sentinels (either sign) are invalid samples; too
       many of them reject the capture.
    2. Idle: the capture must open with steady idle current. Its level is the
       reference.
    3. Run: where the time-weighted smoothed level sits above idle by more than
       two tolerances (motors only add current), with a mean above idle beyond
       its noise, so idle fluctuations do not count as runs. Exactly one run of at least
       ``min_run_s`` is expected; its edges are refined on the raw readings.
    4. Averaging window: the run minus the shortest start and end trims (each
       up to ``max_settle_s``, for inrush, acceleration and deceleration) whose
       blocks of two smoothing windows agree on the mean within tolerance, with
       no invalid reading inside. No such window means the run is not steady:
       drift, a second level (hold, standby), or loads slower than a block.
       The window must also be precise: two standard errors (from the
       readings and from the spread of the block means) within tolerance.
    5. Report the time-weighted mean over the window, each reading held until
       the next.
"""

import logging
import math
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# Instrument overflow sentinel (matches HMC8012.OVERFLOW_SENTINEL).
OVERFLOW_SENTINEL = 9.90000000e37
# The run starts this many tolerances above idle.
RUN_THRESHOLD_TOLERANCES = 2.0
# A run's mean must exceed idle by this many standard errors (rejects idle noise excursions).
RUN_SIGNIFICANCE_SIGMAS = 3.0
# The reported mean must be known within the tolerance at this many sigma.
PRECISION_Z_SCORE = 2.0
# Steady idle required at capture start to trust the idle reference.
MIN_IDLE_S = 0.25
# Stationarity blocks last this many smoothing windows, so their sensitivity does not depend on run length.
BLOCK_SMOOTHING_WINDOWS = 2.0
MIN_BLOCKS = 2
TRIM_STEP_S = 0.1


@dataclass(frozen=True)
class AnalysisConfig:
    """Tuning of the running-current analysis.

    Attributes:
        smoothing_window_s: Moving-mean window used to find idle and the run.
            Must cover a couple of periods of any ripple or burst load.
        min_run_s: Minimum length of the run and of the averaging window.
        max_settle_s: Longest start or end trim allowed to reach a steady
            window (inrush, acceleration, deceleration).
        rel_tolerance: Relative tolerance on the running mean (0.02 = 2%).
        abs_tolerance_a: Absolute floor of the tolerance (amperes).
        max_invalid_fraction: Maximum fraction of NaN/inf/overflow samples.
    """

    smoothing_window_s: float = 0.5
    min_run_s: float = 0.5
    max_settle_s: float = 1.0
    rel_tolerance: float = 0.02
    abs_tolerance_a: float = 0.002
    max_invalid_fraction: float = 0.20

    def __post_init__(self) -> None:
        for name in ("smoothing_window_s", "min_run_s", "rel_tolerance", "abs_tolerance_a"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"AnalysisConfig.{name} must be finite and > 0, got {value}")
        if not math.isfinite(self.max_settle_s) or self.max_settle_s < 0:
            raise ValueError(f"AnalysisConfig.max_settle_s must be finite and >= 0, got {self.max_settle_s}")
        if not 0.0 <= self.max_invalid_fraction < 1.0:
            raise ValueError(
                f"AnalysisConfig.max_invalid_fraction must be in [0, 1), got {self.max_invalid_fraction}"
            )

    def tolerance(self, level: float) -> float:
        """Tolerance around *level* (amperes)."""
        return max(self.abs_tolerance_a, self.rel_tolerance * abs(level))


@dataclass(frozen=True)
class AnalysisResult:
    """Result of the running-current analysis.

    Attributes:
        stable_value: Time-weighted mean current over the averaging window.
        stable_std_dev: Standard deviation of the readings in the window.
        standard_error: Uncertainty (1 sigma) of *stable_value* (amperes).
        start_time: First reading of the averaging window (seconds).
        end_time: Last reading of the averaging window (seconds).
        start_index: First sample of the window (original arrays).
        end_index: One past its last sample (original arrays).
        samples_used: Valid samples in the window.
        idle_level: Current of the steady idle at capture start (amperes).
        run_start_time: First reading above idle (seconds).
        run_end_time: Last reading above idle (seconds).
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
    """Raised when the capture has no run, or a run that is not steady."""


class AmbiguousRunError(AnalysisError):
    """Raised when the capture holds more than one separate run."""


class InvalidCaptureError(AnalysisError):
    """Raised when the capture arrays are malformed, mostly invalid, or not idle at start."""


class ImpreciseValueError(AnalysisError):
    """Raised when the running mean is not known within tolerance (too few or too noisy readings)."""


def analyze_waveform(
    timestamps: np.ndarray,
    values: np.ndarray,
    config: AnalysisConfig = AnalysisConfig(),
) -> AnalysisResult:
    """Extract the mean running current from a captured waveform.

    Args:
        timestamps: 1-D sample times in seconds, strictly increasing.
        values: 1-D current readings in amperes, same length.
        config: Analysis tuning.

    Returns:
        :class:`AnalysisResult` for the averaging window of the run.

    Raises:
        InvalidCaptureError: Malformed arrays, too many invalid samples, or no
            steady idle at capture start.
        SignalNotSettledError: No run, or a run that is not steady.
        AmbiguousRunError: More than one separate run.
        ImpreciseValueError: The running mean is not known within tolerance.
    """
    times, currents, original_index, capture_start = _valid_samples(timestamps, values, config)
    integral = _time_integral(times, currents)
    level = _smooth(times, integral, config.smoothing_window_s)
    idle_level = _idle_level(times, integral, level, capture_start, config)
    run_start, run_end = _run_bounds(times, currents, level, idle_level, config)
    first, last = _averaging_window(times, integral, currents, original_index, (run_start, run_end), config)
    return _result(times, currents, integral, original_index, (first, last), (run_start, run_end), idle_level, config)


def smooth_level(times: np.ndarray, currents: np.ndarray, window_s: float) -> np.ndarray:
    """Time-weighted centered moving mean, each reading held until the next.

    Args:
        times: Strictly increasing sample times (seconds).
        currents: Valid current samples (no NaN/overflow), same length.
        window_s: Window length in seconds; clipped to the capture at the edges.

    Returns:
        Smoothed current, one value per sample.
    """
    return _smooth(times, _time_integral(times, currents), window_s)


def _valid_samples(
    timestamps: np.ndarray,
    values: np.ndarray,
    config: AnalysisConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Check array shape and timing, drop invalid readings.

    Returns:
        ``(times, currents, original_index, capture_start)``; *capture_start*
        is the first timestamp before invalid readings were dropped.
    """
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
        logger.warning("Ignoring %d invalid samples (NaN, inf or overflow)", invalid_count)
    original_index = np.flatnonzero(is_valid)
    if len(original_index) < 2:
        raise InvalidCaptureError(f"Need at least 2 valid samples, got {len(original_index)}")
    return times[original_index], currents[original_index], original_index, float(times[0])


def _time_integral(times: np.ndarray, currents: np.ndarray) -> np.ndarray:
    """Running integral of the current at each sample time, each reading held until the next."""
    return np.concatenate(([0.0], np.cumsum(currents[:-1] * np.diff(times))))


def _mean_between(times: np.ndarray, integral: np.ndarray, first: np.ndarray, last: np.ndarray) -> np.ndarray:
    """Time-weighted mean current between pairs of times (elementwise)."""
    return (np.interp(last, times, integral) - np.interp(first, times, integral)) / (last - first)


def _smooth(times: np.ndarray, integral: np.ndarray, window_s: float) -> np.ndarray:
    first = np.clip(times - window_s / 2.0, times[0], times[-1])
    last = np.clip(times + window_s / 2.0, times[0], times[-1])
    return _mean_between(times, integral, first, last)


def _fits_band(low: float, high: float, config: AnalysisConfig) -> bool:
    return high - low <= 2.0 * config.tolerance((high + low) / 2.0)


def _idle_level(
    times: np.ndarray,
    integral: np.ndarray,
    level: np.ndarray,
    capture_start: float,
    config: AnalysisConfig,
) -> float:
    """Level of the steady idle that must open the capture."""
    levels = level.tolist()
    end, low, high = len(levels), levels[0], levels[0]
    for index in range(1, len(levels)):
        low, high = min(low, levels[index]), max(high, levels[index])
        if not _fits_band(low, high, config):
            end = index
            break
    duration = times[end - 1] - times[0]
    if times[0] - capture_start > config.smoothing_window_s / 2.0 or duration < MIN_IDLE_S:
        raise InvalidCaptureError(
            f"Capture must start with the device idle: need {MIN_IDLE_S} s of steady current from the "
            f"start (plus half the {config.smoothing_window_s} s smoothing window), got "
            f"{max(duration, 0.0):.2f} s from {times[0] - capture_start:.2f} s. Start the capture before the device "
            f"moves; if it already does, the idle current fluctuates more than +/-{config.tolerance(float(level[0])) * 1000:.1f} mA "
            "(raise abs_tolerance_a)."
        )
    return float((integral[end - 1] - integral[0]) / duration)


def _run_bounds(
    times: np.ndarray,
    currents: np.ndarray,
    level: np.ndarray,
    idle_level: float,
    config: AnalysisConfig,
) -> tuple[int, int]:
    """Valid-array bounds ``[start, end)`` of the single run above idle."""
    threshold = idle_level + RUN_THRESHOLD_TOLERANCES * config.tolerance(idle_level)
    runs = [
        (start, end) for start, end in _merged_runs(times, level > threshold, config.smoothing_window_s)
        if times[end - 1] - times[start] >= config.min_run_s
        and _is_above_idle(currents[start:end], idle_level)
    ]
    if not runs:
        raise SignalNotSettledError(
            f"No run: the current never stayed above idle ({idle_level:.4f} A) by more than "
            f"{threshold - idle_level:.4f} A for {config.min_run_s} s. Start the capture before the device moves."
        )
    if len(runs) > 1:
        spans = ", ".join(f"{times[s]:.2f}-{times[e - 1]:.2f} s" for s, e in runs)
        raise AmbiguousRunError(f"{len(runs)} separate runs ({spans}); expected one run per capture.")
    start, end = runs[0]
    above = np.flatnonzero(currents[start:end] > threshold)
    return start + int(above[0]), start + int(above[-1]) + 1


def _is_above_idle(readings: np.ndarray, idle_level: float) -> bool:
    """True if the readings' mean exceeds idle beyond noise, not just by an idle fluctuation."""
    return float(np.mean(readings)) - idle_level > RUN_SIGNIFICANCE_SIGMAS * _standard_error(readings)


def _merged_runs(times: np.ndarray, mask: np.ndarray, max_gap_s: float) -> list[tuple[int, int]]:
    """Contiguous True runs of *mask*, joining runs separated by less than *max_gap_s*."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask.astype(np.int8), [0]))))
    merged: list[tuple[int, int]] = []
    for start, end in zip(edges[::2].tolist(), edges[1::2].tolist()):
        if merged and times[start] - times[merged[-1][1] - 1] < max_gap_s:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _averaging_window(
    times: np.ndarray,
    integral: np.ndarray,
    currents: np.ndarray,
    original_index: np.ndarray,
    run: tuple[int, int],
    config: AnalysisConfig,
) -> tuple[int, int]:
    """Valid-array bounds of the least-trimmed steady, precise and complete window inside the run."""
    best_uncertainty, has_invalid = math.inf, False
    for first, last in _candidate_windows(times, run, config):
        if original_index[last - 1] - original_index[first] + 1 != last - first:
            has_invalid = True
            continue
        if not _is_steady(times, integral, currents, first, last, config):
            continue
        mean, uncertainty = _window_estimate(times, integral, currents, first, last, config)
        if PRECISION_Z_SCORE * uncertainty <= config.tolerance(mean):
            return first, last
        best_uncertainty = min(best_uncertainty, uncertainty)
    _raise_no_window(times, integral, run, best_uncertainty, has_invalid, config)


def _candidate_windows(times: np.ndarray, run: tuple[int, int], config: AnalysisConfig):
    """Windows inside the run, smallest total trim first, at least ``min_run_s`` long."""
    trims = np.arange(0.0, config.max_settle_s + TRIM_STEP_S / 2.0, TRIM_STEP_S).tolist()
    for head, tail in sorted(((h, t) for h in trims for t in trims), key=lambda pair: (sum(pair), pair[0])):
        first = int(np.searchsorted(times, times[run[0]] + head, side="left"))
        last = int(np.searchsorted(times, times[run[1] - 1] - tail, side="right"))
        if last - first >= 2 and times[last - 1] - times[first] >= config.min_run_s:
            yield first, last


def _raise_no_window(
    times: np.ndarray,
    integral: np.ndarray,
    run: tuple[int, int],
    best_uncertainty: float,
    has_invalid: bool,
    config: AnalysisConfig,
) -> None:
    span = f"{times[run[0]]:.2f}-{times[run[1] - 1]:.2f} s"
    if math.isfinite(best_uncertainty):
        raise ImpreciseValueError(
            f"Run {span} is steady but its mean is too uncertain: "
            f"+/-{PRECISION_Z_SCORE * best_uncertainty * 1000:.1f} mA ({PRECISION_Z_SCORE:g} sigma) at best, "
            f"tolerance {config.rel_tolerance:.0%}. Use a slower ADC rate, a longer run or a looser tolerance."
        )
    if has_invalid:
        raise InvalidCaptureError(
            f"Run {span} has invalid readings (overflow, NaN) that no start/end trim up to "
            f"{config.max_settle_s} s excludes: missing peaks would bias the mean. Raise the DCI range."
        )
    raise SignalNotSettledError(_not_steady_message(times, integral, run[0], run[1], config))


def _window_estimate(
    times: np.ndarray,
    integral: np.ndarray,
    currents: np.ndarray,
    first: int,
    last: int,
    config: AnalysisConfig,
) -> tuple[float, float]:
    """Time-weighted mean of a window and its standard error.

    The error is the larger of the reading-based one and the spread of the
    block means, which also captures correlated noise and slow loads.
    """
    edges = _block_edges(times, first, last, config)
    block_means = _mean_between(times, integral, edges[:-1], edges[1:])
    mean = float(_mean_between(times, integral, edges[0], edges[-1]))
    spread = float(np.std(block_means, ddof=1)) / math.sqrt(len(block_means))
    return mean, max(_standard_error(currents[first:last]), spread)


def _block_edges(times: np.ndarray, first: int, last: int, config: AnalysisConfig) -> np.ndarray:
    duration = times[last - 1] - times[first]
    blocks = max(MIN_BLOCKS, int(duration // (BLOCK_SMOOTHING_WINDOWS * config.smoothing_window_s)))
    return np.linspace(times[first], times[last - 1], blocks + 1)


def _is_steady(
    times: np.ndarray,
    integral: np.ndarray,
    currents: np.ndarray,
    first: int,
    last: int,
    config: AnalysisConfig,
) -> bool:
    """True if every block mean matches the window mean within tolerance plus noise."""
    edges = _block_edges(times, first, last, config)
    means = _mean_between(times, integral, edges[:-1], edges[1:])
    overall = float(_mean_between(times, integral, edges[0], edges[-1]))
    bounds = np.searchsorted(times, edges)
    noise = np.array([_standard_error(currents[lo:hi]) for lo, hi in zip(bounds[:-1], bounds[1:])])
    return bool(np.all(np.abs(means - overall) <= config.tolerance(overall) + PRECISION_Z_SCORE * noise))


def _standard_error(readings: np.ndarray) -> float:
    """Standard error of the mean over readings, repeated polls counted once."""
    if len(readings) < 2:
        return 0.0
    independent = 1 + int(np.count_nonzero(np.diff(readings)))
    return float(np.std(readings)) / math.sqrt(independent)


def _result(
    times: np.ndarray,
    currents: np.ndarray,
    integral: np.ndarray,
    original_index: np.ndarray,
    window: tuple[int, int],
    run: tuple[int, int],
    idle_level: float,
    config: AnalysisConfig,
) -> AnalysisResult:
    """Result for an averaging window that already passed the precision check."""
    first, last = window
    value, standard_error = _window_estimate(times, integral, currents, first, last, config)
    return AnalysisResult(
        stable_value=value,
        stable_std_dev=float(np.std(currents[first:last])),
        standard_error=standard_error,
        start_time=float(times[first]),
        end_time=float(times[last - 1]),
        start_index=int(original_index[first]),
        end_index=int(original_index[last - 1]) + 1,
        samples_used=last - first,
        idle_level=idle_level,
        run_start_time=float(times[run[0]]),
        run_end_time=float(times[run[1] - 1]),
    )


def _not_steady_message(
    times: np.ndarray,
    integral: np.ndarray,
    run_start: int,
    run_end: int,
    config: AnalysisConfig,
) -> str:
    edges = _block_edges(times, run_start, run_end, config)
    means = ", ".join(f"{mean:.4f}" for mean in _mean_between(times, integral, edges[:-1], edges[1:]))
    return (
        f"Run {times[run_start]:.2f}-{times[run_end - 1]:.2f} s is not steady: trimming up to "
        f"{config.max_settle_s} s at either end never makes its block means agree "
        f"within +/-{config.rel_tolerance:.0%} (untrimmed blocks: {means} A). Drift, a second level "
        "(hold, standby, another speed), or loads slower than a block."
    )
