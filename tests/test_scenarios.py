"""Tests for the scenario catalogue used by the simulator and analyzer tests."""

from dataclasses import replace

import pytest

from scenarios import SCENARIOS, ScenarioParams


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario_defaults_build_exactly_one_target_phase(name):
    phases = SCENARIOS[name].phases()
    assert sum(phase.is_target for phase in phases) == 1


def test_scenario_phases_use_given_params_over_defaults():
    params = replace(ScenarioParams(), movement_a=0.123)
    target = next(p for p in SCENARIOS["motor1"].phases(params) if p.is_target)
    assert target.level_a == 0.123


def test_movement_lies_between_two_deltastep_groups_and_ends_at_idle():
    names = [phase.name for phase in SCENARIOS["motor1"].phases()]
    movement = names.index("movement")
    assert "deltastep" in names[:movement] and "deltastep" in names[movement + 1:]
    assert names[-1] == "idle"


def test_starts_in_deltastep_scenario_has_no_idle_before():
    assert SCENARIOS["starts_in_deltastep"].phases()[0].name == "deltastep"
