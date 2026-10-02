"""Tests for the auto-stop of a capture (stop_detector.py)."""

import math
from dataclasses import replace

import numpy as np
import pytest

from analyzer import analyze_waveform
from scenarios import SCENARIOS
from simulation import InstrumentModel, simulate_capture
from stop_detector import StopDetector

IDLE_A = 0.030
RUN_A = 0.350
# Readings every 0.25 s: exact in binary, so the expected stop times are exact.
STEP_S = 0.25


def _readings(levels: list[tuple[float, float]], end_s: float = 30.0) -> list[tuple[float, float]]:
    """``(time, current)`` readings every STEP_S; *levels* are ``(from_time, current)`` steps."""
    readings = []
    for step in range(int(end_s / STEP_S)):
        time_s = step * STEP_S
        readings.append((time_s, [current for start, current in levels if start <= time_s][-1]))
    return readings


def _stop_time(readings: list[tuple[float, float]]) -> float | None:
    """Time of the first reading at which the detector stops the capture, None if it never does."""
    detector = StopDetector()
    for time_s, value in readings:
        if detector.should_stop(time_s, value):
            return time_s
    return None


def test_stops_three_seconds_after_the_current_returns_to_idle() -> None:
    readings = _readings([(0.0, IDLE_A), (1.0, RUN_A), (3.0, IDLE_A)])
    # Last running reading at 2.75 s.
    assert _stop_time(readings) == 5.75


def test_a_pause_shorter_than_the_hold_does_not_stop_the_capture() -> None:
    readings = _readings([(0.0, IDLE_A), (1.0, RUN_A), (3.0, IDLE_A), (4.0, RUN_A), (6.0, IDLE_A)])
    # Last running reading at 5.75 s, after a 1 s pause.
    assert _stop_time(readings) == 8.75


def test_never_stops_while_the_device_has_not_run() -> None:
    assert _stop_time(_readings([(0.0, IDLE_A)])) is None


def test_a_spike_shorter_than_a_run_is_not_a_run() -> None:
    readings = _readings([(0.0, IDLE_A), (2.0, RUN_A), (2.25, IDLE_A)])
    assert _stop_time(readings) is None


def test_a_failed_reading_after_the_run_restarts_the_hold() -> None:
    readings = _readings([(0.0, IDLE_A), (1.0, RUN_A), (3.0, IDLE_A), (4.0, math.nan), (4.25, IDLE_A)])
    assert _stop_time(readings) == 7.0


def test_an_auto_stopped_capture_gives_the_same_value_as_a_full_one() -> None:
    scenario = SCENARIOS["nominal"]
    capture = simulate_capture(
        scenario.phases(replace(scenario.defaults, idle_after_s=6.0)), InstrumentModel(adc_rate="SLOW"), seed=0
    )
    detector = StopDetector()
    stop = next(
        index for index, (time_s, value) in enumerate(zip(capture.timestamps, capture.values))
        if detector.should_stop(float(time_s), float(value))
    )
    assert capture.target_end_s + 3.0 <= capture.timestamps[stop] < capture.target_end_s + 3.5
    stopped = analyze_waveform(capture.timestamps[:stop + 1], capture.values[:stop + 1])
    full = analyze_waveform(capture.timestamps, capture.values)
    assert stopped.stable_value == pytest.approx(full.stable_value, abs=1e-9)
    assert np.isclose(stopped.stable_value, capture.expected_value, atol=0.002)
