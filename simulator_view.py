"""Matplotlib views of the simulator (imported lazily by the simulate.py CLI).

Chart legend:
    grey line      true device current (simulation only)
    blue dots      samples as recorded by the capture loop
    orange line    smoothed level used to find idle and the run
    grey band      run (current above idle)
    green band     averaging window, green dashed line = reported value
    black dotted   expected running value; vertical dotted lines bound its window
"""

import textwrap
from dataclasses import replace
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.widgets import RadioButtons, Slider

from analyzer import (
    OVERFLOW_SENTINEL,
    AnalysisConfig,
    AnalysisError,
    AnalysisResult,
    analyze_waveform,
    smooth_level,
)
from scenarios import SCENARIOS
from simulator_core import ADC_RATES, Outcome, Status, run_simulation
from simulation import InstrumentModel, SimulatedCapture

STATUS_COLORS = {Status.PASS: "#d9f2d9", Status.FAIL: "#f8d0d0", Status.RAISE: "#fbe7c6"}
RANGES_A = {"0.2 A": 0.2, "2 A": 2.0, "10 A": 10.0}
# Plot at most this many points of the 20 kHz true-current grid.
MAX_TRUE_POINTS = 40_000
# (key, label, low, high) of the scenario-parameter sliders.
PARAM_SLIDERS = (
    ("run_a", "run current [A]", 0.02, 2.0),
    ("inrush_a", "inrush peak [A]", 0.0, 3.0),
    ("ripple_a", "step ripple [A]", 0.0, 0.2),
    ("ripple_hz", "ripple freq [Hz]", 1.0, 2000.0),
    ("burst_a", "burst load [A]", 0.0, 0.5),
    ("burst_period_s", "burst period [s]", 0.01, 2.0),
    ("run_s", "run duration [s]", 0.5, 15.0),
    ("idle_after_s", "idle after stop [s]", 0.5, 15.0),
)


def draw_capture(
    ax: Axes,
    times: np.ndarray,
    values: np.ndarray,
    result: AnalysisResult | None,
    config: AnalysisConfig,
    truth: SimulatedCapture | None = None,
) -> None:
    """Draw samples, smoothed level, run, averaging window and (if given) the ground truth."""
    valid = np.isfinite(values) & (np.abs(values) < OVERFLOW_SENTINEL)
    times, values = times[valid], values[valid]
    if truth is not None:
        step = max(1, len(truth.true_time) // MAX_TRUE_POINTS)
        ax.plot(truth.true_time[::step], truth.true_current[::step], color="0.7", lw=0.5, label="true current")
    if len(times) >= 2:
        ax.plot(times, values, ".", ms=2, color="tab:blue", label="samples")
        ax.plot(times, smooth_level(times, values, config.smoothing_window_s), color="tab:orange", lw=1.3,
                label=f"smoothed ({config.smoothing_window_s:g} s)")
    if result is not None:
        _draw_run(ax, result)
    if truth is not None:
        ax.axhline(truth.expected_value, color="black", ls=":", lw=1.2, label=f"expected {truth.expected_value:.4f} A")
        for edge in (truth.target_start_s, truth.target_end_s):
            ax.axvline(edge, color="black", ls=":", lw=0.8)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Current [A]")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)


def show_outcome(outcome: Outcome, config: AnalysisConfig, title: str, save: Path | None) -> None:
    """Chart of one simulated scenario with its PASS/FAIL/RAISE verdict."""
    fig, ax = plt.subplots(figsize=(12, 5))
    capture = outcome.capture
    draw_capture(ax, capture.timestamps, capture.values, outcome.result, config, capture)
    _draw_verdict(ax, title, outcome.status, outcome.message)
    _finish(fig, save)


def show_replay(times: np.ndarray, values: np.ndarray, config: AnalysisConfig, title: str, save: Path | None) -> None:
    """Chart of a real capture analyzed with *config* (no ground truth)."""
    fig, ax = plt.subplots(figsize=(12, 5))
    try:
        result = analyze_waveform(times, values, config)
        message, status = f"stable value {result.stable_value:.4f} A, sigma {result.stable_std_dev * 1000:.2f} mA", Status.PASS
    except AnalysisError as exc:
        result, message, status = None, f"{type(exc).__name__}: {exc}", Status.RAISE
    print(message)
    draw_capture(ax, times, values, result, config)
    _draw_verdict(ax, title, status, message, label="RESULT" if status is Status.PASS else None)
    _finish(fig, save)


def save_scenario_grid(adc_rate: str, config: AnalysisConfig, seed: int, save: Path) -> None:
    """One chart per scenario at *adc_rate*, saved as a single image."""
    names = list(SCENARIOS)
    columns = 2
    fig, axes = plt.subplots((len(names) + 1) // columns, columns, figsize=(16, 3.2 * ((len(names) + 1) // columns)))
    for ax, name in zip(axes.flat, names):
        outcome = run_simulation(SCENARIOS[name].phases(), InstrumentModel(adc_rate=adc_rate), config, seed)
        draw_capture(ax, outcome.capture.timestamps, outcome.capture.values, outcome.result, config, outcome.capture)
        _draw_verdict(ax, f"{name} @ {adc_rate}", outcome.status, outcome.message, fontsize=7, width_chars=95)
        ax.get_legend().remove()
    for ax in list(axes.flat)[len(names):]:
        ax.axis("off")
    _finish(fig, save)


def run_interactive(config: AnalysisConfig) -> "_InteractiveSession":
    """Window with scenario/ADC/range selectors and parameter sliders.

    Returns the session after the window closes. Matplotlib keeps only weak
    references to widget callbacks, so the session must stay referenced
    while ``plt.show()`` runs or the controls stop responding.
    """
    fig = plt.figure(figsize=(15, 9))
    fig.canvas.manager.set_window_title("HMC8012 capture simulator")
    ax = fig.add_axes((0.25, 0.47, 0.73, 0.44))
    session = _InteractiveSession(fig, ax, _build_controls(fig, config), config)
    session.start()
    plt.show()
    return session


def _draw_run(ax: Axes, result: AnalysisResult) -> None:
    ax.axvspan(result.run_start_time, result.run_end_time, color="0.6", alpha=0.15, lw=0)
    ax.axvspan(result.start_time, result.end_time, color="tab:green", alpha=0.2, lw=0)
    ax.axhline(result.stable_value, color="tab:green", ls="--", lw=1.5, label=f"reported {result.stable_value:.4f} A")


def _draw_verdict(
    ax: Axes,
    title: str,
    status: Status,
    message: str,
    *,
    fontsize: int = 9,
    width_chars: int = 160,
    label: str | None = None,
) -> None:
    """Title box colored by status, so the verdict never hides the data."""
    verdict = textwrap.fill(f"{label or status.value}: {message}", width_chars)
    text = ax.set_title(f"{title}\n{verdict}", fontsize=fontsize, loc="left")
    text.set_bbox({"boxstyle": "round", "facecolor": STATUS_COLORS[status], "alpha": 0.95})


def _finish(fig: plt.Figure, save: Path | None) -> None:
    fig.tight_layout()
    if save is not None:
        fig.savefig(save, dpi=120)
        print(f"Chart saved to {save}")
    else:
        plt.show()


def _build_controls(fig: plt.Figure, config: AnalysisConfig) -> dict:
    """Create the selector and slider widgets (positions in figure fractions)."""
    controls = {
        "scenario": RadioButtons(fig.add_axes((0.01, 0.47, 0.18, 0.48)), list(SCENARIOS)),
        "adc": RadioButtons(fig.add_axes((0.01, 0.25, 0.08, 0.17)), ADC_RATES, active=ADC_RATES.index("SLOW")),
        "range": RadioButtons(fig.add_axes((0.10, 0.25, 0.09, 0.17)), list(RANGES_A), active=1),
    }
    defaults = SCENARIOS[next(iter(SCENARIOS))].defaults
    slider_specs = [(key, label, low, high, getattr(defaults, key)) for key, label, low, high in PARAM_SLIDERS]
    slider_specs += [
        ("noise_ma", "noise [mA]", 0.0, 10.0, InstrumentModel().noise_a * 1000.0),
        ("window_s", "smoothing window [s]", 0.1, 3.0, config.smoothing_window_s),
        ("tolerance_pct", "tolerance [%]", 0.5, 10.0, config.rel_tolerance * 100.0),
        ("max_settle_s", "max settle trim [s]", 0.0, 5.0, config.max_settle_s),
        ("seed", "seed", 0, 50, 0),
    ]
    for row, (key, label, low, high, initial) in enumerate(slider_specs):
        axes = fig.add_axes((0.36, 0.38 - row * 0.032, 0.5, 0.022))
        controls[key] = Slider(axes, label, low, high, valinit=initial, valstep=1 if key == "seed" else None)
    return controls


class _InteractiveSession:
    """Re-runs the simulation whenever a control changes."""

    def __init__(self, fig: plt.Figure, ax: Axes, controls: dict, config: AnalysisConfig) -> None:
        self._fig, self._ax, self._controls, self._config = fig, ax, controls, config

    @property
    def controls(self) -> dict:
        """Widgets by key: "scenario", "adc", "range" and one per slider."""
        return self._controls

    def start(self) -> None:
        """Wire callbacks and draw the first scenario."""
        self._controls["scenario"].on_clicked(self._on_scenario)
        for key, widget in self._controls.items():
            if key != "scenario":
                (widget.on_clicked if isinstance(widget, RadioButtons) else widget.on_changed)(self._redraw)
        self._redraw()

    def _on_scenario(self, _label: str) -> None:
        defaults = self._scenario().defaults
        for key, *_ in PARAM_SLIDERS:
            slider = self._controls[key]
            slider.eventson = False
            slider.set_val(getattr(defaults, key))
            slider.eventson = True
        self._redraw()

    def _scenario(self):
        return SCENARIOS[self._controls["scenario"].value_selected]

    def _redraw(self, _event=None) -> None:
        params, instrument, config, seed = self._inputs()
        title = f"{self._scenario().name}: {self._scenario().description}"
        self._ax.clear()
        try:
            outcome = run_simulation(self._scenario().phases(params), instrument, config, seed)
        except ValueError as exc:
            _draw_verdict(self._ax, title, Status.RAISE, f"Invalid scenario parameters: {exc}", width_chars=150)
        else:
            capture = outcome.capture
            draw_capture(self._ax, capture.timestamps, capture.values, outcome.result, config, capture)
            _draw_verdict(self._ax, title, outcome.status, outcome.message, width_chars=150)
        self._fig.canvas.draw_idle()

    def _inputs(self) -> tuple:
        """Current widget values as (scenario params, instrument, analyzer config, seed)."""
        c = self._controls
        params = replace(self._scenario().defaults, **{key: c[key].val for key, *_ in PARAM_SLIDERS})
        instrument = InstrumentModel(adc_rate=c["adc"].value_selected, range_a=RANGES_A[c["range"].value_selected],
                                     noise_a=c["noise_ma"].val / 1000.0)
        config = replace(self._config, smoothing_window_s=c["window_s"].val,
                         rel_tolerance=c["tolerance_pct"].val / 100.0, max_settle_s=c["max_settle_s"].val)
        return params, instrument, config, int(c["seed"].val)
