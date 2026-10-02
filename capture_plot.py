"""Capture plot page: the readings, the run and the averaging window, drawn with uPlot.

The same page serves the saved plot (``capture --save-plot``, data embedded)
and the live window (``capture --live``, readings streamed by live_plot.py).
It is a single file that opens offline: uPlot is inlined.
"""

import html
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Sequence

from analyzer import OVERFLOW_SENTINEL, AnalysisResult

ASSETS_DIR = Path(__file__).resolve().parent / "plot_assets"
PLOT_FILE_PREFIX = "capture_plot"
LIVE_TITLE = "Cattura in corso"
MILLIAMPS_PER_AMP = 1000.0

_PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")
_LIVE_SUMMARY = '<p class="outcome is-running" id="live-status">In attesa delle letture</p>'


def write_capture_plot(
    output_dir: Path,
    timestamps: Sequence[float],
    values: Sequence[float],
    captured_at: datetime,
    analysis: AnalysisResult | None = None,
    error: Exception | None = None,
) -> Path:
    """Write the plot page to ``capture_plot_<UTC date>.html`` in *output_dir*.

    Returns:
        Path of the written file.
    """
    path = output_dir / f"{PLOT_FILE_PREFIX}_{captured_at:%Y-%m-%d_%H-%M-%S}.html"
    page = render_capture_plot(timestamps, values, captured_at, analysis, error)
    path.write_text(page, encoding="utf-8")
    return path


def render_capture_plot(
    timestamps: Sequence[float],
    values: Sequence[float],
    captured_at: datetime,
    analysis: AnalysisResult | None = None,
    error: Exception | None = None,
) -> str:
    """Return the plot page of a finished capture, with its data embedded.

    Args:
        timestamps: Reading times in seconds from the capture start.
        values: Readings in amperes; NaN, inf and overflow readings become gaps.
        captured_at: When the capture ran (UTC), shown in the title.
        analysis: Analysis result; marks the run, the averaging window and the mean.
        error: Error that ended the capture or the analysis, shown instead of a value.
    """
    payload = {
        "is_live": False,
        "time_s": [float(t) for t in timestamps],
        "current_ma": [milliamps(v) for v in values],
        **_outcome_spans(analysis),
    }
    return _render_page(capture_title(captured_at), _summary_html(values, analysis, error), payload)


def render_live_page() -> str:
    """Return the plot page in live mode: no data yet, readings arrive as server-sent events."""
    payload = {"is_live": True, "time_s": [], "current_ma": [], **_outcome_spans(None)}
    return _render_page(LIVE_TITLE, _LIVE_SUMMARY, payload)


def final_outcome(
    values: Sequence[float],
    captured_at: datetime,
    analysis: AnalysisResult | None = None,
    error: Exception | None = None,
) -> dict:
    """Outcome sent to the live page when the capture ends: title, summary and marked spans."""
    return {
        "title": capture_title(captured_at),
        "summary_html": _summary_html(values, analysis, error),
        **_outcome_spans(analysis),
    }


def capture_title(captured_at: datetime) -> str:
    """Page title of a capture, e.g. ``Cattura 2026-10-02 11:36:01 UTC``."""
    return f"Cattura {captured_at:%Y-%m-%d %H:%M:%S} UTC"


def milliamps(value: float) -> float | None:
    """Reading in milliamperes, or None (a gap in the plot) for an invalid reading."""
    is_valid = math.isfinite(value) and abs(value) < OVERFLOW_SENTINEL
    return float(value) * MILLIAMPS_PER_AMP if is_valid else None


def script_json(payload: dict) -> str:
    """JSON safe to embed in a <script> element or a server-sent event."""
    # "</" would close the <script> element that holds the data.
    return json.dumps(payload, allow_nan=False, ensure_ascii=False).replace("</", "<\\/")


def _outcome_spans(analysis: AnalysisResult | None) -> dict:
    if analysis is None:
        return {"run_s": None, "window_s": None, "value_ma": None}
    return {
        "run_s": [float(analysis.run_start_time), float(analysis.run_end_time)],
        "window_s": [float(analysis.start_time), float(analysis.end_time)],
        "value_ma": float(analysis.stable_value) * MILLIAMPS_PER_AMP,
    }


def _summary_html(
    values: Sequence[float],
    analysis: AnalysisResult | None,
    error: Exception | None,
) -> str:
    invalid_count = sum(milliamps(v) is None for v in values)
    facts = [("Letture", f"{len(values)}, di cui {invalid_count} non valide")]
    outcome = ""
    if analysis is not None:
        outcome = f'<p class="outcome">{_format_milliamps(analysis.stable_value)}</p>'
        facts = _analysis_facts(analysis) + facts
    elif error is not None:
        message = html.escape(f"ERR: {type(error).__name__}: {error}")
        outcome = f'<p class="outcome is-error">{message}</p>'
    rows = "".join(
        f"<div><dt>{html.escape(name)}</dt><dd>{html.escape(text)}</dd></div>" for name, text in facts
    )
    return f"{outcome}<dl>{rows}</dl>"


def _analysis_facts(analysis: AnalysisResult) -> list[tuple[str, str]]:
    return [
        ("result.txt", str(analysis.stable_value)),
        ("Finestra di media", f"da {analysis.start_time:.2f} s a {analysis.end_time:.2f} s, "
                              f"{analysis.samples_used} letture"),
        ("Regime", f"da {analysis.run_start_time:.2f} s a {analysis.run_end_time:.2f} s"),
        ("Riposo", _format_milliamps(analysis.idle_level)),
        ("Incertezza (1σ)", _format_milliamps(analysis.standard_error)),
    ]


def _format_milliamps(amps: float) -> str:
    return f"{amps * MILLIAMPS_PER_AMP:.2f} mA"


def _render_page(title: str, summary_html: str, payload: dict) -> str:
    fields = {
        "TITLE": html.escape(title),
        "SUMMARY": summary_html,
        "DATA_JSON": script_json(payload),
        "UPLOT_CSS": _read_asset("uPlot.min.css"),
        "UPLOT_JS": _read_asset("uPlot.iife.min.js"),
    }
    # One pass over the template, so text inserted here is never scanned for placeholders.
    return _PLACEHOLDER.sub(lambda match: fields[match.group(1)], _read_asset("capture_plot.html"))


def _read_asset(name: str) -> str:
    return (ASSETS_DIR / name).read_text(encoding="utf-8")
