"""Plot utilities for HMC8012 continuous capture.

Used only in operator-facing workflows (not VBA). Imports matplotlib lazily
to avoid hard-wiring the dependency into headless flows.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from analyzer import AnalysisResult
from capture import CaptureResult

# Minimum Y axis span in amperes so at least 1 A of signal is visible
MIN_Y_SPAN_AMPS = 1.0


def show_capture_plot(
    result: CaptureResult,
    analysis: Optional[AnalysisResult] = None,
) -> None:
    """Display an annotated plot of a capture result.

    If *analysis* is provided, the stable value and stable region are
    highlighted. This function is best-effort: if matplotlib is not
    available, it logs to stderr and returns without raising.
    """
    try:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - GUI dependency
        import sys

        print(
            "[APP] Plot requested but matplotlib is unavailable:",
            exc,
            file=sys.stderr,
        )
        print(
            "[APP] Install with: pip install matplotlib",
            file=sys.stderr,
        )
        return

    ts = np.asarray(result.timestamps, dtype=float)
    vals = np.asarray(result.values, dtype=float)

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(ts, vals, label="DCI", color="tab:blue")

    if np.any(np.isfinite(vals)):
        y_min, y_max = float(np.nanmin(vals)), float(np.nanmax(vals))
        span = max(y_max - y_min, MIN_Y_SPAN_AMPS)
        mid = (y_max + y_min) / 2.0
        ax.set_ylim(mid - span / 2.0, mid + span / 2.0)
    else:
        ax.set_ylim(0.0, MIN_Y_SPAN_AMPS)

    if analysis is not None and result.sample_count >= analysis.samples_used > 0:
        stable_start_index = min(analysis.settling_sample_index, result.sample_count - 1)
        stable_end_index = min(
            analysis.settling_sample_index + analysis.samples_used,
            result.sample_count,
        )
        stable_start_t = float(ts[stable_start_index])
        stable_end_t = float(ts[stable_end_index - 1]) if stable_end_index > 0 else stable_start_t
        ax.axhline(
            analysis.stable_value,
            color="tab:green",
            linestyle="--",
            label=f"Stable = {analysis.stable_value:.4f} A",
        )
        ax.axvspan(
            stable_start_t,
            stable_end_t,
            color="tab:green",
            alpha=0.15,
            label="Stable region",
        )

    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Current [A]")
    ax.set_title("HMC8012 Continuous Capture")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    fig.tight_layout()

    plt.show()

