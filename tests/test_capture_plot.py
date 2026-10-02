"""Tests for the opt-in HTML capture plot (capture_plot.py)."""

import json
import math
import re
from datetime import datetime, timezone

from analyzer import AnalysisResult, SignalNotSettledError
from capture_plot import render_capture_plot, write_capture_plot

CAPTURED_AT = datetime(2026, 10, 2, 11, 36, 1, tzinfo=timezone.utc)
TIMESTAMPS = [0.0, 0.2, 0.4, 0.6]


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        stable_value=0.40012,
        stable_std_dev=0.001,
        standard_error=0.00005,
        start_time=0.2,
        end_time=0.4,
        start_index=1,
        end_index=3,
        samples_used=2,
        idle_level=0.03,
        run_start_time=0.1,
        run_end_time=0.5,
    )


def _embedded_data(page: str) -> dict:
    match = re.search(r'<script id="capture-data" type="application/json">(.*?)</script>', page, re.S)
    assert match, "the page has no embedded capture data"
    return json.loads(match.group(1))


def test_render_capture_plot_inlines_uplot_so_the_page_opens_offline() -> None:
    page = render_capture_plot(TIMESTAMPS, [0.03] * 4, CAPTURED_AT, analysis=_analysis())
    assert "leeoniya/uPlot" in page
    assert "src=" not in page
    assert "<link" not in page


def test_render_capture_plot_embeds_readings_in_milliamps_with_gaps_for_invalid_ones() -> None:
    page = render_capture_plot(TIMESTAMPS, [0.03, math.nan, 9.9e37, 0.4], CAPTURED_AT)
    data = _embedded_data(page)
    assert data["time_s"] == TIMESTAMPS
    assert data["current_ma"] == [30.0, None, None, 400.0]


def test_render_capture_plot_marks_the_averaging_window_and_the_run() -> None:
    page = render_capture_plot(TIMESTAMPS, [0.03, 0.4, 0.4, 0.03], CAPTURED_AT, analysis=_analysis())
    data = _embedded_data(page)
    assert data["window_s"] == [0.2, 0.4]
    assert data["run_s"] == [0.1, 0.5]
    assert math.isclose(data["value_ma"], 400.12)
    assert "400.12 mA" in page


def test_render_capture_plot_shows_the_error_instead_of_a_value() -> None:
    error = SignalNotSettledError("no steady run <b>")
    page = render_capture_plot(TIMESTAMPS, [0.03] * 4, CAPTURED_AT, error=error)
    data = _embedded_data(page)
    assert data["window_s"] is None
    assert data["value_ma"] is None
    assert "SignalNotSettledError: no steady run &lt;b&gt;" in page
    assert "<b>" not in page


def test_write_capture_plot_names_the_file_after_the_capture_time(tmp_path) -> None:
    path = write_capture_plot(tmp_path, TIMESTAMPS, [0.03] * 4, CAPTURED_AT, analysis=_analysis())
    assert path == tmp_path / "capture_plot_2026-10-02_11-36-01.html"
    assert "400.12 mA" in path.read_text(encoding="utf-8")
