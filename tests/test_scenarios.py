"""Tests for the scenario catalogue used by the simulator and analyzer tests."""

from dataclasses import replace

import pytest

from scenarios import SCENARIOS, ScenarioParams


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario_defaults_build_exactly_one_target_phase(name):
    phases = SCENARIOS[name].phases()
    assert sum(phase.is_target for phase in phases) == 1


def test_scenario_phases_use_given_params_over_defaults():
    params = replace(ScenarioParams(), run_a=0.123)
    target = next(p for p in SCENARIOS["nominal"].phases(params) if p.is_target)
    assert target.level_a == 0.123


def test_long_idle_after_scenario_waits_longer_than_it_runs():
    phases = SCENARIOS["long_idle_after"].phases()
    run = next(p for p in phases if p.is_target)
    assert phases[-1].duration_s > run.duration_s
