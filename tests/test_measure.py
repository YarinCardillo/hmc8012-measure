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
from hmc8012 import RangeOverflowError, ScpiError
from version import __version__


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


class _FakeDmm:
    """Fake HMC8012 context manager: constant current, remembers its ADC rate.

    Records the ADC rate in force at every reading, to check what capture used.
    """

    def __init__(self, adc_rate: str = "SLOW", readings: int | None = None, fail_restore: bool = False) -> None:
        self.adc_rate = adc_rate
        self.rates_during_readings: list[str] = []
        self._readings = readings
        self._count = 0
        self._fail_restore = fail_restore
        self._rate_changes = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def set_function(self, function: str) -> None:
        pass

    def set_adc_rate(self, rate: str) -> None:
        self._rate_changes += 1
        if self._fail_restore and self._rate_changes > 1:
            raise ScpiError('-113,"Undefined header"')
        self.adc_rate = rate

    def get_function(self) -> str:
        return "CURR"

    def get_adc_rate(self) -> str:
        return self.adc_rate

    def get_range_auto(self, function: str) -> bool:
        return False

    def measure_fast(self) -> float:
        self._count += 1
        if self._readings is not None and self._count > self._readings:
            raise RangeOverflowError("overflow")
        self.rates_during_readings.append(self.adc_rate)
        time.sleep(0.001)
        return 0.03


def _use_fake(monkeypatch, dmm: _FakeDmm) -> _FakeDmm:
    monkeypatch.setattr(measure_module, "HMC8012", lambda address: dmm)
    return dmm


@pytest.mark.parametrize("previous_rate", ["FAST", "MED"])
def test_capture_reads_at_slow_and_restores_the_previous_rate(monkeypatch, previous_rate) -> None:
    dmm = _use_fake(monkeypatch, _FakeDmm(adc_rate=previous_rate))
    with pytest.raises(AnalysisError):
        measure_module._run_capture_session("192.0.2.1", 0.3, 5.0)
    assert set(dmm.rates_during_readings) == {"SLOW"}
    assert dmm.adc_rate == previous_rate


def test_capture_does_not_touch_an_instrument_already_at_slow(monkeypatch) -> None:
    dmm = _use_fake(monkeypatch, _FakeDmm(adc_rate="SLOW"))
    with pytest.raises(AnalysisError):
        measure_module._run_capture_session("192.0.2.1", 0.3, 5.0)
    assert dmm._rate_changes == 0


def test_capture_restores_the_rate_when_the_capture_stops_early(monkeypatch) -> None:
    dmm = _use_fake(monkeypatch, _FakeDmm(adc_rate="FAST", readings=30))
    with pytest.raises(CaptureAbortedError):
        measure_module._run_capture_session("192.0.2.1", 5.0, 10.0)
    assert dmm.adc_rate == "FAST"


def test_failed_rate_restore_is_reported_without_hiding_the_outcome(monkeypatch, capsys) -> None:
    _use_fake(monkeypatch, _FakeDmm(adc_rate="FAST", fail_restore=True))
    with pytest.raises(AnalysisError):
        measure_module._run_capture_session("192.0.2.1", 0.3, 5.0)
    assert "Could not restore ADC rate FAST" in capsys.readouterr().err


def test_capture_writes_no_samples_file_by_default(monkeypatch) -> None:
    written = []
    monkeypatch.setattr(measure_module, "write_capture_samples", lambda *args, **kwargs: written.append(args))
    _use_fake(monkeypatch, _FakeDmm())
    with pytest.raises(AnalysisError):
        measure_module._run_capture_session("192.0.2.1", 0.3, 5.0)
    assert written == []


@pytest.mark.parametrize("dmm, error_type", [
    (_FakeDmm(), AnalysisError),
    (_FakeDmm(readings=30), CaptureAbortedError),
    (_FakeDmm(readings=3), InsufficientSamplesError),
])
def test_saved_samples_survive_a_failed_capture(tmp_path: Path, monkeypatch, dmm, error_type) -> None:
    _use_fake(monkeypatch, dmm)
    with pytest.raises(error_type):
        measure_module._run_capture_session("192.0.2.1", 5.0 if dmm._readings else 0.3, 10.0, samples_dir=tmp_path)
    assert len(list(tmp_path.glob("capture_samples_*.csv"))) == 1


@pytest.mark.parametrize("args, expected", [
    ([], (10.0, 20.0, False)),
    (["5"], (5.0, 15.0, False)),
    (["5", "8"], (5.0, 8.0, False)),
    (["5", "--save-samples"], (5.0, 15.0, True)),
    (["--save-samples", "5", "8"], (5.0, 8.0, True)),
])
def test_capture_arguments_accept_an_optional_save_samples_flag(args, expected) -> None:
    assert measure_module._parse_capture_args(args) == expected


def test_adc_without_value_writes_the_current_rate(monkeypatch) -> None:
    written = []
    monkeypatch.setattr(measure_module, "write_result", lambda value, *args, **kwargs: written.append(value))
    _use_fake(monkeypatch, _FakeDmm(adc_rate="MED"))
    measure_module.cmd_adc("192.0.2.1", [])
    assert written == ["MED"]


def test_adc_with_value_sets_the_rate_and_writes_ok(monkeypatch) -> None:
    written = []
    monkeypatch.setattr(measure_module, "write_result", lambda value, *args, **kwargs: written.append(value))
    dmm = _use_fake(monkeypatch, _FakeDmm(adc_rate="FAST"))
    measure_module.cmd_adc("192.0.2.1", ["slow"])
    assert dmm.adc_rate == "SLOW"
    assert written == ["OK"]


@pytest.mark.parametrize("args", [["TURBO"], ["SLOW", "extra"]])
def test_adc_rejects_invalid_arguments(monkeypatch, args) -> None:
    monkeypatch.setattr(measure_module, "write_result", lambda *args, **kwargs: None)
    with pytest.raises(SystemExit):
        measure_module.cmd_adc("192.0.2.1", args)


@pytest.mark.parametrize("args", [["192.0.2.1"], ["192.0.2.1", "range", "temp", "2"], ["192.0.2.1", "capture-plot", "5"]])
def test_cli_usage_errors_replace_stale_result_with_err(args) -> None:
    script_dir = Path(__file__).resolve().parent.parent
    result_file = script_dir / "result.txt"
    result_file.write_text("0.123\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "measure", *args], capture_output=True, text=True, cwd=script_dir)
    assert proc.returncode != 0
    assert result_file.read_text(encoding="utf-8").split("\n")[0] == "ERR"


def test_cli_version_prints_the_version_without_touching_result() -> None:
    script_dir = Path(__file__).resolve().parent.parent
    result_file = script_dir / "result.txt"
    result_file.write_text("0.123\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "measure", "--version"], capture_output=True, text=True, cwd=script_dir)
    assert proc.returncode == 0
    assert proc.stdout.strip() == __version__
    assert result_file.read_text(encoding="utf-8") == "0.123\n"
