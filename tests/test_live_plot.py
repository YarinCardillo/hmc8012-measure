"""Tests for the live capture plot server (live_plot.py)."""

import json
import math
import time
import urllib.request
from datetime import datetime, timezone

from analyzer import AnalysisResult, SignalNotSettledError
from live_plot import LivePlot

CAPTURED_AT = datetime(2026, 10, 2, 11, 36, 1, tzinfo=timezone.utc)


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        stable_value=0.4, stable_std_dev=0.001, standard_error=0.00005,
        start_time=0.2, end_time=0.4, start_index=1, end_index=3, samples_used=2,
        idle_level=0.03, run_start_time=0.1, run_end_time=0.5,
    )


def _read_events(url: str, last_event_id: str | None = None) -> list[tuple[str, dict]]:
    request = urllib.request.Request(url + "events")
    if last_event_id is not None:
        request.add_header("Last-Event-ID", last_event_id)
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.headers["Content-Type"].startswith("text/event-stream")
        body = response.read().decode("utf-8")
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def test_live_plot_listens_on_loopback_only() -> None:
    with LivePlot(final_wait_s=0) as live:
        assert live.url.startswith("http://127.0.0.1:")


def test_live_plot_serves_the_page_in_live_mode() -> None:
    with LivePlot(final_wait_s=0) as live:
        with urllib.request.urlopen(live.url, timeout=5) as response:
            page = response.read().decode("utf-8")
    assert "leeoniya/uPlot" in page
    assert '"is_live": true' in page


def test_live_plot_streams_the_readings_then_the_final_outcome() -> None:
    with LivePlot(final_wait_s=0) as live:
        for time_s, value in [(0.0, 0.03), (0.1, math.nan), (0.2, 0.4)]:
            live.add_sample(time_s, value)
        live.finish(CAPTURED_AT, analysis=_analysis())
        events = _read_events(live.url)
    samples = [payload for name, payload in events if name == "samples"]
    assert [t for batch in samples for t in batch["time_s"]] == [0.0, 0.1, 0.2]
    assert [c for batch in samples for c in batch["current_ma"]] == [30.0, None, 400.0]
    name, final = events[-1]
    assert name == "final"
    assert final["window_s"] == [0.2, 0.4]
    assert "400.00 mA" in final["summary_html"]
    assert final["title"] == "Cattura 2026-10-02 11:36:01 UTC"


def test_live_plot_resumes_after_the_last_event_id_on_reconnect() -> None:
    with LivePlot(final_wait_s=0) as live:
        for time_s in (0.0, 0.1, 0.2):
            live.add_sample(time_s, 0.03)
        live.finish(CAPTURED_AT, error=SignalNotSettledError("no run"))
        events = _read_events(live.url, last_event_id="2")
    assert [payload["time_s"] for name, payload in events if name == "samples"] == [[0.2]]
    assert "SignalNotSettledError: no run" in events[-1][1]["summary_html"]


def test_live_plot_close_waits_for_the_page_at_most_final_wait_s() -> None:
    started = time.monotonic()
    with LivePlot(final_wait_s=0.2) as live:
        live.finish(CAPTURED_AT, error=SignalNotSettledError("no run"))
    assert time.monotonic() - started < 2.0


def test_live_plot_close_returns_once_the_page_has_the_final_outcome() -> None:
    started = time.monotonic()
    with LivePlot(final_wait_s=30) as live:
        live.add_sample(0.0, 0.03)
        live.finish(CAPTURED_AT, error=SignalNotSettledError("no run"))
        _read_events(live.url)
    assert time.monotonic() - started < 5.0
