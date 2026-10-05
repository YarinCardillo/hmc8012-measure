"""Device current scenarios for the simulator and the analyzer tests.

Modelled on the lab recordings in tests/data/lab: the device idles at about
167 mA; deltastep adjustments draw about 625 mA in strokes of a few tenths of
a second, with short returns to idle between strokes; the movement to
measure is a lower level between two groups of strokes, followed by idle.
The levels depend on the device and are meant to be tuned in the simulator.
"""

from dataclasses import dataclass, replace
from typing import Callable

from simulation import Phase


@dataclass(frozen=True)
class ScenarioParams:
    """Tunable parameters shared by all scenario builders (amperes, seconds)."""

    idle_a: float = 0.167
    deltastep_a: float = 0.625
    movement_a: float = 0.190
    stroke_s: float = 0.4
    stroke_pause_s: float = 0.2
    movement_s: float = 2.4
    idle_before_s: float = 0.6
    idle_after_s: float = 3.0


@dataclass(frozen=True)
class Scenario:
    """A named device behaviour: a phase builder plus its default parameters."""

    name: str
    description: str
    build: Callable[[ScenarioParams], list[Phase]]
    defaults: ScenarioParams

    def phases(self, params: ScenarioParams | None = None) -> list[Phase]:
        """Build the phase list, using the scenario defaults when *params* is None."""
        return self.build(params if params is not None else self.defaults)


def _deltastep_group(params: ScenarioParams) -> list[Phase]:
    """Two deltastep strokes with a short return to idle between them."""
    return [
        Phase("deltastep", params.stroke_s, params.deltastep_a),
        Phase("idle", params.stroke_pause_s, params.idle_a),
        Phase("deltastep", params.stroke_s, params.deltastep_a),
    ]


def _movement_between_deltasteps(params: ScenarioParams) -> list[Phase]:
    before = [Phase("idle", params.idle_before_s, params.idle_a)] if params.idle_before_s > 0 else []
    return [
        *before,
        *_deltastep_group(params),
        Phase("movement", params.movement_s, params.movement_a, is_target=True),
        *_deltastep_group(params),
        Phase("idle", params.idle_after_s, params.idle_a),
    ]


_BASE = ScenarioParams()

SCENARIOS: dict[str, Scenario] = {
    scenario.name: scenario
    for scenario in (
        Scenario("motor1", "Movement of 2.4 s at 190 mA between deltastep groups, as motor 1 in the lab.",
                 _movement_between_deltasteps, _BASE),
        Scenario("motor2", "Short movement of 0.6 s at 196 mA, as motor 2 in the lab: 3 SLOW conversions.",
                 _movement_between_deltasteps, replace(_BASE, movement_a=0.196, movement_s=0.6)),
        Scenario("starts_in_deltastep", "Capture starting during the first deltastep stroke, no idle before.",
                 _movement_between_deltasteps, replace(_BASE, idle_before_s=0.0)),
    )
}
