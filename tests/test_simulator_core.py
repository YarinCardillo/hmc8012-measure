"""Tests for the simulator core (no GUI): grading, scenario matrix, CSV replay."""

from dataclasses import replace

import numpy as np
import pytest

import measure as measure_module
from analyzer import AnalysisConfig
from capture import CaptureResult
from scenarios import SCENARIOS
from simulator_core import Status, grade_matrix, load_capture_csv, run_simulation
from simulation import InstrumentModel, Phase


def test_run_simulation_nominal_scenario_passes():
    outcome = run_simulation(SCENARIOS["nominal"].phases(), InstrumentModel(), AnalysisConfig(), seed=1)
    assert outcome.status is Status.PASS
    assert abs(outcome.error_a) <= AnalysisConfig().tolerance(outcome.capture.expected_value)


def test_run_simulation_reports_raise_with_message_when_analysis_fails():
    outcome = run_simulation(SCENARIOS["hold_after_stop"].phases(), InstrumentModel(), AnalysisConfig(), seed=1)
    assert outcome.status is Status.RAISE
    assert outcome.result is None
    assert "not steady" in outcome.message


def test_run_simulation_reports_fail_when_value_outside_tolerance():
    # Analyze with the default tolerance, grade with a near-zero one: any error is a FAIL.
    phases = SCENARIOS["nominal"].phases(replace(SCENARIOS["nominal"].defaults, ripple_a=0.0))
    loose = AnalysisConfig(rel_tolerance=0.02)
    strict_grading = AnalysisConfig(rel_tolerance=1e-6, abs_tolerance_a=1e-9)
    outcome = run_simulation(phases, InstrumentModel(noise_a=0.002), loose, seed=2, grading=strict_grading)
    assert outcome.status is Status.FAIL


def test_grade_matrix_counts_every_seed_for_every_scenario_and_rate():
    rows = grade_matrix(seeds=2, adc_rates=("SLOW", "FAST"))
    assert len(rows) == 2 * len(SCENARIOS)
    assert all(row.passed + row.failed + row.raised == 2 for row in rows)


def test_load_capture_csv_reads_files_written_by_capture(tmp_path):
    timestamps = np.array([0.0, 0.01, 0.02])
    values = np.array([0.03, 0.35, 0.351])
    result = CaptureResult(timestamps, values, sample_count=3, actual_duration=0.02, sample_rate=150.0)
    path = measure_module.write_capture_samples(result, tmp_path)
    loaded_times, loaded_values = load_capture_csv(path)
    assert np.allclose(loaded_times, timestamps)
    assert np.allclose(loaded_values, values)


def test_load_capture_csv_rejects_file_without_samples(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("# Measurement date (UTC): x\ntime_s,value_A\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_capture_csv(path)


def test_run_simulation_reports_raise_when_capture_aborts():
    phases = [Phase("idle", 1.0, 0.03), Phase("run", 3.0, 0.35, is_target=True), Phase("over", 1.0, 3.0)]
    outcome = run_simulation(phases, InstrumentModel(adc_rate="SLOW"), AnalysisConfig(), seed=3)
    assert outcome.status is Status.RAISE
    assert "aborted" in outcome.message
