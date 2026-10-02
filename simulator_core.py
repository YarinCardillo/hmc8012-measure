"""Simulator core: run the real analyzer on simulated captures and grade it.

Generates captures with the physical model (simulation.py, scenarios.py),
analyzes them with analyzer.py and grades the reported value against the
known true running current. Also loads real capture CSV files for replay.
No GUI code here (see simulator_view.py and the simulate.py CLI).
"""

import warnings
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np

from analyzer import AnalysisConfig, AnalysisError, AnalysisResult, analyze_waveform
from scenarios import SCENARIOS
from simulation import ADC_READINGS_PER_SECOND, InstrumentModel, Phase, SimulatedCapture, simulate_capture

ADC_RATES = tuple(ADC_READINGS_PER_SECOND)
CSV_COLUMNS = ("time_s", "value_A")


class Status(Enum):
    """Grade of one simulated analysis."""

    PASS = "PASS"
    FAIL = "FAIL"
    RAISE = "RAISE"


@dataclass(frozen=True)
class Outcome:
    """Analysis of one simulated capture, graded against the ground truth.

    Attributes:
        capture: The simulated capture with its ground truth.
        result: Analyzer result, None when the analyzer raised.
        status: PASS (within tolerance), FAIL (wrong value) or RAISE.
        error_a: Reported minus expected value, None when the analyzer raised.
        message: Human-readable summary or the analyzer error.
    """

    capture: SimulatedCapture
    result: AnalysisResult | None
    status: Status
    error_a: float | None
    message: str


@dataclass(frozen=True)
class MatrixRow:
    """Pass/fail counts of one scenario at one ADC rate over several seeds."""

    scenario: str
    adc_rate: str
    passed: int
    failed: int
    raised: int


def run_simulation(
    phases: list[Phase],
    instrument: InstrumentModel,
    config: AnalysisConfig,
    seed: int = 0,
    grading: AnalysisConfig | None = None,
) -> Outcome:
    """Simulate a capture, analyze it and grade the reported value.

    Args:
        phases: Device current profile (exactly one target phase).
        instrument: Acquisition model.
        config: Analyzer configuration under test.
        seed: Noise and latency seed.
        grading: Configuration whose tolerance defines PASS; defaults to *config*.

    Returns:
        :class:`Outcome`.
    """
    capture = simulate_capture(phases, instrument, seed)
    if capture.aborted:
        return Outcome(capture, None, Status.RAISE, None,
                       "Capture aborted: consecutive over-range readings (raise the DCI range)")
    try:
        result = analyze_waveform(capture.timestamps, capture.values, config)
    except AnalysisError as exc:
        return Outcome(capture, None, Status.RAISE, None, f"{type(exc).__name__}: {exc}")
    error = result.stable_value - capture.expected_value
    tolerance = (grading or config).tolerance(capture.expected_value)
    status = Status.PASS if abs(error) <= tolerance else Status.FAIL
    message = (
        f"expected {capture.expected_value:.4f} A, got {result.stable_value:.4f} A, "
        f"error {error * 1000:+.1f} mA (tolerance +/-{tolerance * 1000:.1f} mA)"
    )
    return Outcome(capture, result, status, error, message)


def grade_matrix(
    seeds: int = 10,
    adc_rates: tuple[str, ...] = ADC_RATES,
    config: AnalysisConfig = AnalysisConfig(),
) -> list[MatrixRow]:
    """Grade every scenario at every ADC rate over *seeds* seeds."""
    rows = []
    for name, scenario in SCENARIOS.items():
        for adc_rate in adc_rates:
            statuses = [
                run_simulation(scenario.phases(), InstrumentModel(adc_rate=adc_rate), config, seed).status
                for seed in range(seeds)
            ]
            rows.append(MatrixRow(name, adc_rate, statuses.count(Status.PASS),
                                  statuses.count(Status.FAIL), statuses.count(Status.RAISE)))
    return rows


def load_capture_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read a ``capture_samples_*.csv`` file written by ``measure.py capture``.

    Returns:
        ``(timestamps, values)`` arrays.

    Raises:
        ValueError: If the file has no ``time_s,value_A`` header or no rows.
    """
    # genfromtxt(names=True) would take the leading "# Measurement date" comment as header.
    lines = [line for line in Path(path).read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # empty file: reported below
        data = np.atleast_1d(np.genfromtxt(lines, delimiter=",", names=True))
    if data.dtype.names is None or not set(CSV_COLUMNS) <= set(data.dtype.names) or data.size == 0:
        raise ValueError(f"No samples in {path}: expected header '{','.join(CSV_COLUMNS)}' and data rows")
    return data[CSV_COLUMNS[0]].astype(float), data[CSV_COLUMNS[1]].astype(float)

