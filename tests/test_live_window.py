"""Tests for the live plot window process (live_window.py)."""

import subprocess

from live_window import LIVE_WINDOW_FLAG, open_live_window, run_live_window

URL = "http://127.0.0.1:50123/"


def test_open_live_window_starts_a_detached_child_that_runs_the_window() -> None:
    launched = []
    open_live_window(URL, launch=lambda command, **options: launched.append((command, options)))
    command, options = launched[0]
    assert command[-2:] == [LIVE_WINDOW_FLAG, URL]
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL


def test_run_live_window_shows_the_page_in_a_native_window() -> None:
    shown, tabs = [], []
    run_live_window(URL, show=shown.append, open_tab=tabs.append)
    assert shown == [URL]
    assert tabs == []


def test_run_live_window_falls_back_to_a_browser_tab_without_a_web_view() -> None:
    def no_web_view(url: str) -> None:
        raise RuntimeError("WebView2 runtime not found")

    tabs = []
    run_live_window(URL, show=no_web_view, open_tab=tabs.append)
    assert tabs == [URL]
