"""Tests for CLI (measure.py) and result contract (result.txt format, atomic write)."""

import subprocess
import sys
from pathlib import Path

import pytest

# Import from measure so we can test write_result/clear_result with custom path
# without running main (which uses sys.argv and DEFAULT_OUTPUT).
import measure as measure_module


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
