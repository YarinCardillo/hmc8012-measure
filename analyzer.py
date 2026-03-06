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
    prominence_sigma: float = 3.0,
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
) -> int:
    """Find the first sample index where rolling std-dev drops below threshold.

    A sliding window of *window_size* samples is moved across *values*.
    The signal is declared "settled" at the start of the first window whose
    standard deviation is strictly below *std_threshold*.

    Args:
        values: 1-D array of post-peak measurement values.
        window_size: Number of samples in the sliding window.
        std_threshold: Standard deviation threshold for the "settled" state.

    Returns:
        Index (relative to *values*) of the first settled window.

    Raises:
        SignalNotSettledError: If the rolling std-dev never drops below
            *std_threshold*, or if *values* is shorter than *window_size*.
    """
    if len(values) < window_size:
        raise SignalNotSettledError(
            f"Post-peak data has {len(values)} samples, "
            f"need at least {window_size} for settling window"
        )

    windows = sliding_window_view(values, window_size)
    rolling_std = windows.std(axis=-1)

    settled_mask = rolling_std < std_threshold
    settled_indices = np.where(settled_mask)[0]

    if len(settled_indices) == 0:
        raise SignalNotSettledError(
            f"Signal never settled: min rolling std = {rolling_std.min():.6f}, "
            f"threshold = {std_threshold}"
        )

    return int(settled_indices[0])


def analyze_waveform(
    timestamps: np.ndarray,
    values: np.ndarray,
    *,
    prominence_sigma: float = 3.0,
    min_peak_distance: int = 50,
    settling_window: int = 20,
    settling_threshold: float = 0.01,
    max_overflow_pct: float = 0.20,
) -> AnalysisResult:
    """Run the full analysis pipeline on a captured motor-current waveform.

    Pipeline steps:
        1. Filter overflow sentinels (``filter_overflows``).
        2. Detect peaks (``detect_peaks``).
        3. Anchor on the last significant peak.
        4. Find the settling point in post-peak data (``find_settling_point``).
        5. Compute stable value (mean) and quality metric (std-dev) of the
           settled region.

    Args:
        timestamps: 1-D time-axis array (seconds).
        values: 1-D measurement-values array (same length as *timestamps*).
        prominence_sigma: Peak prominence as a multiple of signal std-dev.
        min_peak_distance: Minimum samples between peaks.
        settling_window: Sliding-window size for settling detection.
        settling_threshold: Std-dev threshold below which the signal is
            considered settled.
        max_overflow_pct: Maximum allowed fraction of overflow samples.

    Returns:
        :class:`AnalysisResult` with the extracted stable value and metadata.

    Raises:
        InvalidCaptureError: If overflow fraction exceeds *max_overflow_pct*,
            or if filtered data contains NaN values.
        NoPeaksDetectedError: If no significant peaks are found.
        SignalNotSettledError: If the signal never settles after the last peak.
    """
    # Step 1: Filter overflow sentinels
    filtered_ts, filtered_vals = filter_overflows(
        timestamps, values, max_overflow_pct=max_overflow_pct,
    )

    # Step 2: NaN guard (Pitfall 1 from RESEARCH.md)
    if np.any(np.isnan(filtered_vals)):
        raise InvalidCaptureError("NaN values in filtered data")

    # Step 3: Detect peaks
    peaks = detect_peaks(
        filtered_vals,
        prominence_sigma=prominence_sigma,
        min_distance=min_peak_distance,
    )

    # Step 4: Anchor on the last significant peak
    anchor = peaks[-1]

    # Step 5: Extract post-peak data and find settling point
    post_peak_values = filtered_vals[anchor.index:]
    settling_idx = find_settling_point(
        post_peak_values,
        window_size=settling_window,
        std_threshold=settling_threshold,
    )

    # Step 6: Compute stable region statistics
    abs_settling_idx = anchor.index + settling_idx
    stable_region = filtered_vals[abs_settling_idx:]
    stable_value = float(np.mean(stable_region))
    stable_std = float(np.std(stable_region))

    return AnalysisResult(
        stable_value=stable_value,
        stable_std_dev=stable_std,
        peaks=peaks,
        anchor_peak_index=len(peaks) - 1,
        settling_sample_index=abs_settling_idx,
        samples_used=len(stable_region),
    )
