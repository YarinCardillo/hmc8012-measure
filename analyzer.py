"""Signal analysis engine for motor current waveforms.

Pure computation module that receives numpy arrays of timestamps and current
values, detects peaks, identifies settling behavior, and extracts the stable
post-peak current value.  No instrument interaction -- fully testable with
synthetic data.
"""

import logging
from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.signal import find_peaks

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PeakInfo:
    """A single detected peak in the waveform.

    Attributes:
        index: Sample index of the peak within the (filtered) values array.
        amplitude: Absolute value of the waveform at the peak index.
    """

    index: int
    amplitude: float


@dataclass(frozen=True)
class AnalysisResult:
    """Complete result of waveform analysis.

    Attributes:
        stable_value: Mean current in the settled region after the last peak.
        stable_std_dev: Standard deviation of the settled region (quality metric).
        peaks: All detected peaks, ordered by index.
        anchor_peak_index: Position within *peaks* of the last significant peak
            used to anchor the stable-value extraction.
        settling_sample_index: Index in the (filtered) values array where the
            signal is first considered settled.
        samples_used: Number of samples in the settled region that contributed
            to *stable_value*.
    """

    stable_value: float
    stable_std_dev: float
    peaks: tuple[PeakInfo, ...]
    anchor_peak_index: int
    settling_sample_index: int
    samples_used: int


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class AnalysisError(Exception):
    """Base class for signal analysis errors."""


class NoPeaksDetectedError(AnalysisError):
    """Raised when no significant peaks are found in the waveform."""


class SignalNotSettledError(AnalysisError):
    """Raised when the signal never settles below the std-dev threshold."""


class InvalidCaptureError(AnalysisError):
    """Raised when too many overflow sentinels invalidate the capture."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def filter_overflows(
    timestamps: np.ndarray,
    values: np.ndarray,
    *,
    sentinel: float = 9.90000000e+37,
    max_overflow_pct: float = 0.20,
) -> tuple[np.ndarray, np.ndarray]:
    """Remove overflow sentinel values from both arrays in parallel.

    Samples whose value equals or exceeds *sentinel* are stripped from both
    *timestamps* and *values* so the two arrays remain aligned.

    Args:
        timestamps: 1-D time-axis array (seconds).
        values: 1-D measurement-values array (same length as *timestamps*).
        sentinel: Overflow sentinel value emitted by the instrument
            (default matches ``HMC8012.OVERFLOW_SENTINEL``).
        max_overflow_pct: Maximum allowed fraction of overflow samples
            before the capture is declared invalid (0.0 -- 1.0).

    Returns:
        Tuple of ``(filtered_timestamps, filtered_values)`` with overflow
        samples removed.

    Raises:
        InvalidCaptureError: If the input arrays are empty, or if the
            fraction of overflow samples exceeds *max_overflow_pct*.
    """
    if len(values) == 0:
        raise InvalidCaptureError("Empty input arrays")

    mask = values < sentinel
    overflow_count = len(values) - np.count_nonzero(mask)

    if overflow_count / len(values) > max_overflow_pct:
        raise InvalidCaptureError(
            f"{overflow_count}/{len(values)} samples are overflows "
            f"({overflow_count / len(values):.1%} > {max_overflow_pct:.0%} threshold)"
        )

    return timestamps[mask], values[mask]


def detect_peaks(
    values: np.ndarray,
    *,
    prominence_sigma: float = 2.0,
    min_distance: int = 50,
) -> tuple[PeakInfo, ...]:
    """Detect peaks in *values* using prominence relative to signal std-dev.

    A peak is considered significant if its prominence exceeds
    ``prominence_sigma * np.std(values)``.  Peaks closer together than
    *min_distance* samples are filtered by scipy's distance parameter.

    Args:
        values: 1-D array of measurement values.
        prominence_sigma: Prominence threshold expressed as a multiple of
            the signal's standard deviation.
        min_distance: Minimum number of samples between consecutive peaks.

    Returns:
        Tuple of :class:`PeakInfo` instances ordered by sample index.

    Raises:
        NoPeaksDetectedError: If no peaks meet the prominence criteria.
    """
    signal_std = np.std(values)
    prominence_threshold = prominence_sigma * signal_std

    peak_indices, _properties = find_peaks(
        values,
        prominence=prominence_threshold,
        distance=min_distance,
    )

    if len(peak_indices) == 0:
        raise NoPeaksDetectedError(
            f"No peaks with prominence > {prominence_threshold:.4f} "
            f"({prominence_sigma} * std={signal_std:.4f})"
        )

    return tuple(
        PeakInfo(index=int(idx), amplitude=float(values[idx]))
        for idx in peak_indices
    )


def find_settling_point(
    values: np.ndarray,
    *,
    window_size: int = 20,
    std_threshold: float = 0.01,
    n_consecutive: int = 3,
) -> int:
    """Find the first index where the signal is stably settled (sustained low std).

    A sliding window of *window_size* samples is moved across *values*.
    The signal is declared "settled" at the start of the first run of
    *n_consecutive* consecutive windows whose standard deviation is strictly
    below *std_threshold*. This avoids starting the stable region during a
    brief quiet moment in the transient.

    Args:
        values: 1-D array of post-peak measurement values.
        window_size: Number of samples in the sliding window.
        std_threshold: Standard deviation threshold for the "settled" state.
        n_consecutive: Number of consecutive windows that must be below
            threshold before the region is considered settled.

    Returns:
        Index (relative to *values*) of the first sample of the first such run.

    Raises:
        SignalNotSettledError: If no run of *n_consecutive* windows has std
            below *std_threshold*, or if *values* is too short.
    """
    n_rolling = len(values) - window_size + 1
    if n_rolling < n_consecutive:
        raise SignalNotSettledError(
            f"Post-peak data has {len(values)} samples, "
            f"need at least {window_size + n_consecutive - 1} for settling detection"
        )

    windows = sliding_window_view(values, window_size)
    rolling_std = windows.std(axis=-1)
    settled_mask = rolling_std < std_threshold

    for i in range(n_rolling - n_consecutive + 1):
        if np.all(settled_mask[i : i + n_consecutive]):
            return i

    raise SignalNotSettledError(
        f"Signal never settled for {n_consecutive} consecutive windows: "
        f"min rolling std = {rolling_std.min():.6f}, threshold = {std_threshold}"
    )


def _find_longest_baseline_run(
    values: np.ndarray,
    *,
    window_size: int = 20,
    std_threshold: float = 0.01,
    baseline_threshold: float = 0.4,
    min_run_samples: int = 50,
) -> tuple[int, int]:
    """Find the longest contiguous run of "quiet" (low std) windows with mean current below baseline_threshold.

    Used when stable_target="baseline" to select the main baseline segment (e.g. 7–8.5 s)
    instead of the post-peak high plateau.

    Returns:
        (start_index, length) in the original values array. Length is the number of samples
        in the run (first window start through last window end).
    """
    n = len(values)
    if n < window_size:
        raise SignalNotSettledError(
            f"Need at least {window_size} samples for baseline detection, got {n}"
        )
    windows = sliding_window_view(values, window_size)
    rolling_std = windows.std(axis=-1)
    quiet = rolling_std < std_threshold
    # Runs of True: each run spans indices [i, i+window_size-1] for the first window,
    # and we extend to include full run length (last window ends at i+window_size-1 + (run_len-1))
    n_rolling = len(rolling_std)
    run_start = None
    run_len = 0
    best_start = 0
    best_len = 0
    for i in range(n_rolling):
        if quiet[i]:
            if run_start is None:
                run_start = i
                run_len = window_size
            else:
                run_len = (i - run_start) + window_size
        else:
            if run_start is not None:
                run_mean = float(np.mean(values[run_start : run_start + run_len]))
                if run_mean < baseline_threshold and run_len >= min_run_samples:
                    if run_len > best_len:
                        best_len = run_len
                        best_start = run_start
                run_start = None
    if run_start is not None:
        run_mean = float(np.mean(values[run_start : run_start + run_len]))
        if run_mean < baseline_threshold and run_len >= min_run_samples:
            if run_len > best_len:
                best_len = run_len
                best_start = run_start
    if best_len == 0:
        raise SignalNotSettledError(
            f"No baseline run found: quiet run with mean < {baseline_threshold} A "
            f"and length >= {min_run_samples} samples"
        )
    return best_start, best_len


def analyze_waveform(
    timestamps: np.ndarray,
    values: np.ndarray,
    *,
    stable_target: str = "baseline",
    prominence_sigma: float = 2.0,
    anchor_prominence_ratio: float = 0.5,
    min_peak_distance: int = 50,
    settling_window: int = 20,
    settling_threshold: float = 0.01,
    min_samples_after_peak: int = 100,
    n_settling_windows: int = 3,
    max_overflow_pct: float = 0.20,
    baseline_threshold: float = 0.4,
    min_baseline_samples: int = 50,
) -> AnalysisResult:
    """Run the full analysis pipeline on a captured motor-current waveform.

    Two modes (stable_target):
    - "baseline": Find the **longest** contiguous quiet (low std) segment with mean
      current below baseline_threshold (e.g. motor-off baseline). Reports that segment's
      mean and std. Use when the value of interest is the stable baseline (e.g. 7–8.5 s).
    - "post_peak": Anchor on the last significant peak, skip transient, find settled
      region after the peak (motor-on plateau). Use when the value of interest is the
      high current after the last pulse.

    Pipeline for "baseline":
        1. Filter overflows, NaN check.
        2. Sliding-window std; find longest run of "quiet" windows with mean < baseline_threshold.
        3. Return that run's mean, std, start index, length.

    Pipeline for "post_peak":
        1. Filter overflow sentinels (``filter_overflows``).
        2. Detect peaks (``detect_peaks``).
        3. Anchor on the last significant peak.
        4. Skip *min_samples_after_peak* samples after the peak (transient decay).
        5. Find the settling point in the remaining post-peak data.
        5b. Find the end of the stable region (rolling std rises again).
        6. Compute stable value (mean) and quality metric (std-dev) of the settled region.

    Args:
        timestamps: 1-D time-axis array (seconds).
        values: 1-D measurement-values array (same length as *timestamps*).
        stable_target: "baseline" (longest low-current stable run) or "post_peak" (settled region after last peak).
        prominence_sigma: Peak prominence as a multiple of signal std-dev (post_peak mode).
        anchor_prominence_ratio: Anchor on the last peak with prominence >= this fraction of max (post_peak).
        min_peak_distance: Minimum samples between peaks (post_peak).
        settling_window: Sliding-window size for settling detection.
        settling_threshold: Std-dev threshold below which the signal is considered settled.
        min_samples_after_peak: Skip this many samples after the last peak (post_peak).
        n_settling_windows: Number of consecutive low-std windows required to declare settled (post_peak).
        max_overflow_pct: Maximum allowed fraction of overflow samples.
        baseline_threshold: Max mean current (A) for a run to count as baseline (baseline mode).
        min_baseline_samples: Min length of baseline run in samples (baseline mode).

    Returns:
        :class:`AnalysisResult` with the extracted stable value and metadata.

    Raises:
        InvalidCaptureError: If overflow fraction exceeds *max_overflow_pct* or filtered data has NaN.
        NoPeaksDetectedError: If no significant peaks are found (post_peak only).
        SignalNotSettledError: If no baseline run or no settling found.
    """
    # Step 1: Filter overflow sentinels
    filtered_ts, filtered_vals = filter_overflows(
        timestamps, values, max_overflow_pct=max_overflow_pct,
    )

    # Step 2: NaN guard
    if np.any(np.isnan(filtered_vals)):
        raise InvalidCaptureError("NaN values in filtered data")

    if stable_target == "baseline":
        start_idx, run_len = _find_longest_baseline_run(
            filtered_vals,
            window_size=settling_window,
            std_threshold=settling_threshold,
            baseline_threshold=baseline_threshold,
            min_run_samples=min_baseline_samples,
        )
        stable_region = filtered_vals[start_idx : start_idx + run_len]
        return AnalysisResult(
            stable_value=float(np.mean(stable_region)),
            stable_std_dev=float(np.std(stable_region)),
            peaks=(),
            anchor_peak_index=0,
            settling_sample_index=start_idx,
            samples_used=run_len,
        )

    # --- post_peak pipeline ---
    # Step 3: Detect peaks
    peaks = detect_peaks(
        filtered_vals,
        prominence_sigma=prominence_sigma,
        min_distance=min_peak_distance,
    )
    # Prominences for anchor choice (same threshold as detect_peaks)
    signal_std = float(np.std(filtered_vals))
    prom_threshold = prominence_sigma * signal_std
    _peak_idx, properties = find_peaks(
        filtered_vals,
        prominence=prom_threshold,
        distance=min_peak_distance,
    )
    prominences = properties["prominences"]

    # Step 4: Anchor on the last *significant* peak (prominence >= anchor_prominence_ratio * max), so spurious small peaks after the last stable region are ignored
    max_prom = float(np.max(prominences))
    significant = [
        i for i, pr in enumerate(prominences)
        if pr >= anchor_prominence_ratio * max_prom
    ]
    anchor_peak_index = significant[-1]
    anchor = peaks[anchor_peak_index]

    # Step 5: Skip transient decay, then find settling point in post-peak tail
    post_start = anchor.index + min_samples_after_peak
    if post_start >= len(filtered_vals):
        raise SignalNotSettledError(
            f"Not enough samples after last peak: {len(filtered_vals) - anchor.index} "
            f"< min_samples_after_peak={min_samples_after_peak}"
        )
    post_peak_values = filtered_vals[post_start:]
    settling_idx = find_settling_point(
        post_peak_values,
        window_size=settling_window,
        std_threshold=settling_threshold,
        n_consecutive=n_settling_windows,
    )

    # Step 5b: Find end of stable region (where rolling std rises again, e.g. before final rise/fall when device stops)
    windows = sliding_window_view(post_peak_values, settling_window)
    rolling_std = windows.std(axis=-1)
    n_rolling = len(rolling_std)
    # First index j >= settling_idx where window is no longer "quiet"
    j = settling_idx
    while j < n_rolling and rolling_std[j] < settling_threshold:
        j += 1
    # Last sample we include is the last sample of the last quiet window (window starting at j-1)
    end_offset = (j + settling_window - 1) if j < n_rolling else len(post_peak_values)
    end_offset = min(end_offset, len(post_peak_values))

    # Step 6: Compute stable region statistics (only from settling start to detected end)
    abs_settling_idx = post_start + settling_idx
    stable_region = filtered_vals[abs_settling_idx : post_start + end_offset]
    stable_value = float(np.mean(stable_region))
    stable_std = float(np.std(stable_region))

    return AnalysisResult(
        stable_value=stable_value,
        stable_std_dev=stable_std,
        peaks=peaks,
        anchor_peak_index=anchor_peak_index,
        settling_sample_index=abs_settling_idx,
        samples_used=len(stable_region),
    )
