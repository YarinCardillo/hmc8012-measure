"""Tests for CLI (measure.py) and result contract (result.txt format, atomic write)."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

# Import from measure so we can test write_result/clear_result with custom path
# without running main (which uses sys.argv and DEFAULT_OUTPUT).
import measure as measure_module
from analyzer import AnalysisError
from capture import CaptureAbortedError, InsufficientSamplesError
from hmc8012 import RangeOverflowError


def test_clear_result_removes_file(tmp_path: Path) -> None:
    out = tmp_path / "result.txt"
    out.write_text("stale\n")
    measure_module.clear_result(out)
    assert not out.exists()


def test_clear_result_missing_ok(tmp_path: Path) -> None:
    out = tmp_path / "nonexistent.txt"
    measure_module.clear_result(out)
    assert not out.exists()


def test_write_result_atomic_single_line(tmp_path: Path) -> None:
    out = tmp_path / "result.txt"
    measure_module.write_result("0.5234", output_path=out)
    assert out.read_text(encoding="utf-8") == "0.5234\n"


def test_write_result_atomic_with_error_lines(tmp_path: Path) -> None:
    out = tmp_path / "result.txt"
    measure_module.write_result(
        "ERR",
        app_msg="[APP] Capture failed (analysis).",
        exc_detail="[EXC] NoPeaksDetectedError: No significant peaks",
        output_path=out,
    )
    lines = out.read_text(encoding="utf-8").strip().split("\n")
    assert lines[0] == "ERR"
    assert "[APP]" in lines[1]
    assert "[EXC]" in lines[2]


def test_cli_unknown_command_exits_with_usage() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "measure", "192.168.0.1", "unknown"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode != 0
    assert "Usage:" in result.stderr or "Error:" in result.stderr


def test_cli_capture_rejects_timeout_less_than_duration() -> None:
    """Capture command must reject timeout < duration (INTG-04)."""
    script_dir = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "measure", "192.168.0.1", "capture", "10", "5"],
        capture_output=True,
        text=True,
        cwd=script_dir,
    )
    assert result.returncode != 0
    assert "Timeout must be >= capture duration" in result.stderr


def test_cli_capture_invalid_address_writes_err(tmp_path: Path) -> None:
    """Capture with invalid address must write ERR to result.txt (INTG-03)."""
    script_dir = Path(__file__).resolve().parent.parent
    result_file = script_dir / "result.txt"
    # Use non-routable address so connect fails quickly
    proc = subprocess.run(
        [sys.executable, "-m", "measure", "192.0.2.1", "capture", "1", "5"],
        capture_output=True,
        text=True,
        cwd=script_dir,
        timeout=15,
    )
    assert proc.returncode != 0
    assert result_file.exists()
    first_line = result_file.read_text(encoding="utf-8").split("\n")[0].strip()
    assert first_line == "ERR"


def test_cli_single_shot_invalid_address_writes_err(tmp_path: Path) -> None:
    """Single-shot with invalid address must write ERR (INTG-02 backward compat)."""
    script_dir = Path(__file__).resolve().parent.parent
    result_file = script_dir / "result.txt"
    proc = subprocess.run(
        [sys.executable, "-m", "measure", "192.0.2.1", "dci"],
        capture_output=True,
        text=True,
        cwd=script_dir,
        timeout=15,
    )
    assert proc.returncode != 0
    assert result_file.exists()
    first_line = result_file.read_text(encoding="utf-8").split("\n")[0].strip()
    assert first_line == "ERR"


class _FlatCurrentDmm:
    """Fake HMC8012 context manager returning a constant idle current."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def set_function(self, function: str) -> None:
        pass

    def set_adc_rate(self, rate: str) -> None:
        pass

    def get_function(self) -> str:
        return "CURR"

    def get_adc_rate(self) -> str:
        return "FAST"

    def get_range_auto(self, function: str) -> bool:
        return False

    def measure_fast(self) -> float:
        time.sleep(0.001)
        return 0.03


def test_capture_session_saves_raw_samples_even_when_analysis_fails(tmp_path: Path, monkeypatch) -> None:
    """A failed analysis must still leave the raw capture on disk for diagnosis."""
    monkeypatch.setattr(measure_module, "HMC8012", lambda address: _FlatCurrentDmm())
    with pytest.raises(AnalysisError):
        measure_module._run_capture_session(
            "192.0.2.1", 0.3, 5.0,
            sentinel_path=tmp_path / "capture.stop",
            samples_dir=tmp_path,
        )
    assert len(list(tmp_path.glob("capture_samples_*.csv"))) == 1


class _AbortingDmm(_FlatCurrentDmm):
    """Fake HMC8012 whose readings overflow after a few samples."""

    def __init__(self) -> None:
        self._count = 0

    def measure_fast(self) -> float:
        self._count += 1
        if self._count > 30:
            raise RangeOverflowError("overflow")
        return 0.03


def test_capture_session_aborted_by_failures_raises_after_saving_samples(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(measure_module, "HMC8012", lambda address: _AbortingDmm())
    with pytest.raises(CaptureAbortedError):
        measure_module._run_capture_session(
            "192.0.2.1", 5.0, 10.0,
            sentinel_path=tmp_path / "capture.stop",
            samples_dir=tmp_path,
        )
    assert len(list(tmp_path.glob("capture_samples_*.csv"))) == 1


@pytest.mark.parametrize("args", [["192.0.2.1"], ["192.0.2.1", "range", "temp", "2"]])
def test_cli_usage_errors_replace_stale_result_with_err(args) -> None:
    script_dir = Path(__file__).resolve().parent.parent
    result_file = script_dir / "result.txt"
    result_file.write_text("0.123\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "measure", *args], capture_output=True, text=True, cwd=script_dir)
    assert proc.returncode != 0
    assert result_file.read_text(encoding="utf-8").split("\n")[0] == "ERR"


def test_capture_start_command_runs_script_with_interpreter() -> None:
    command = measure_module._capture_start_command("COM3", "MED", is_compiled=False)
    assert command[0] == sys.executable
    assert command[1].endswith("measure.py")
    assert command[2:] == ["COM3", "capture-plot", "start", "MED"]


def test_capture_start_command_reruns_the_compiled_executable() -> None:
    command = measure_module._capture_start_command("COM3", "SLOW", is_compiled=True)
    assert command[0] == sys.argv[0]
    assert command[1:] == ["COM3", "capture-plot", "start", "SLOW"]


class _ShortLivedDmm(_FlatCurrentDmm):
    """Fake HMC8012 that answers three readings, then fails."""

    def __init__(self) -> None:
        self._count = 0

    def measure_fast(self) -> float:
        self._count += 1
        if self._count > 3:
            raise RangeOverflowError("overflow")
        return 0.03


def test_capture_session_saves_samples_when_too_few_readings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(measure_module, "HMC8012", lambda address: _ShortLivedDmm())
    with pytest.raises(InsufficientSamplesError):
        measure_module._run_capture_session(
            "192.0.2.1", 5.0, 10.0,
            sentinel_path=tmp_path / "capture.stop",
            samples_dir=tmp_path,
        )
    assert len(list(tmp_path.glob("capture_samples_*.csv"))) == 1
