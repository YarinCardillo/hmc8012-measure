"""Headless smoke tests for the simulator charts and interactive window."""

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

import simulator_view  # noqa: E402
from analyzer import AnalysisConfig  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402
from simulation import InstrumentModel  # noqa: E402
from simulator_core import run_simulation  # noqa: E402


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    plt.close("all")


@pytest.mark.parametrize("name", ["nominal", "hold_after_stop"])
def test_show_outcome_saves_chart_for_pass_and_raise(tmp_path, name):
    outcome = run_simulation(SCENARIOS[name].phases(), InstrumentModel(), AnalysisConfig(), seed=0)
    path = tmp_path / f"{name}.png"
    simulator_view.show_outcome(outcome, AnalysisConfig(), name, path)
    assert path.stat().st_size > 0


def test_interactive_window_redraws_on_scenario_and_slider_changes(monkeypatch):
    monkeypatch.setattr(plt, "show", lambda *args, **kwargs: None)
    session = simulator_view.run_interactive(AnalysisConfig())
    session.controls["scenario"].set_active(list(SCENARIOS).index("slow_bursts"))
    session.controls["window_s"].set_val(1.6)
    titles = [ax.get_title(loc="left") for ax in plt.gcf().axes if ax.get_title(loc="left")]
    assert titles and titles[0].startswith("slow_bursts")
