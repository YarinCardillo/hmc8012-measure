"""Device current scenarios for the simulator and the analyzer tests.

Modelled on a stepper-driven device (bipolar stepper, 12 V class, about
0.4 A per phase, geared output). The supply-current levels (idle
electronics, running, hold, standby) depend on the driver and supply
voltage: the defaults below are illustrative and meant to be tuned in the
simulator.
"""

from dataclasses import dataclass, replace
from typing import Callable

from simulation import Phase

# Example full-step rate of a 1.8 deg stepper (about 268 rpm).
DEFAULT_STEP_RATE_HZ = 894.0
PRE_HOLD_S = 1.2
POST_HOLD_S = 1.5


@dataclass(frozen=True)
class ScenarioParams:
    """Tunable parameters shared by all scenario builders (amperes, seconds, hertz)."""

    idle_a: float = 0.030
    run_a: float = 0.350
    hold_a: float = 0.550
    standby_a: float = 0.250
    inrush_a: float = 0.900
    inrush_tau_s: float = 0.08
    ripple_a: float = 0.040
    ripple_hz: float = DEFAULT_STEP_RATE_HZ
    burst_a: float = 0.0
    # Not a submultiple of any ADC period, which would be sampled stroboscopically.
    burst_period_s: float = 0.047
    burst_duty: float = 0.3
    idle_before_s: float = 1.0
    run_s: float = 5.0
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


def _run_phase(params: ScenarioParams) -> Phase:
    return Phase(
        "run", params.run_s, params.run_a,
        start_level_a=params.inrush_a, tau_s=params.inrush_tau_s,
        ripple_a=params.ripple_a, ripple_hz=params.ripple_hz,
        burst_a=params.burst_a, burst_period_s=params.burst_period_s,
        burst_duty=params.burst_duty, is_target=True,
    )


def _idle_run_idle(params: ScenarioParams) -> list[Phase]:
    return [
        Phase("idle", params.idle_before_s, params.idle_a),
        _run_phase(params),
        Phase("idle", params.idle_after_s, params.idle_a),
    ]


def _run_then_hold(params: ScenarioParams) -> list[Phase]:
    return [
        Phase("idle", params.idle_before_s, params.idle_a),
        _run_phase(params),
        Phase("hold", POST_HOLD_S, params.hold_a),
        Phase("standby", params.idle_after_s, params.standby_a),
    ]


def _hold_run_hold(params: ScenarioParams) -> list[Phase]:
    return [
        Phase("idle", params.idle_before_s, params.idle_a),
        Phase("hold", PRE_HOLD_S, params.hold_a),
        _run_phase(params),
        Phase("hold", POST_HOLD_S, params.hold_a),
        Phase("idle", params.idle_after_s, params.idle_a),
    ]


def _run_to_end(params: ScenarioParams) -> list[Phase]:
    return [Phase("idle", params.idle_before_s, params.idle_a), _run_phase(params)]


_BASE = ScenarioParams()

SCENARIOS: dict[str, Scenario] = {
    scenario.name: scenario
    for scenario in (
        Scenario("nominal", "Idle, inrush, run with step ripple, stop, idle.",
                 _idle_run_idle, _BASE),
        Scenario("long_idle_after", "Host waits after the stop longer than the run lasts.",
                 _idle_run_idle, replace(_BASE, run_s=3.0, idle_after_s=6.0)),
        Scenario("high_current", "Running current above 0.4 A (old baseline ceiling).",
                 _idle_run_idle, replace(_BASE, run_a=0.800, inrush_a=1.800)),
        Scenario("pwm_load", "Running current with fast PWM/burst load (47 ms period).",
                 _idle_run_idle, replace(_BASE, burst_a=0.150)),
        Scenario("slow_bursts", "Bursts slower than the smoothing window (0.8 s period).",
                 _idle_run_idle, replace(_BASE, burst_a=0.150, burst_period_s=0.8, run_s=6.0, idle_before_s=2.0)),
        Scenario("hold_after_stop", "Stepper keeps hold current after the stop, then standby.",
                 _run_then_hold, _BASE),
        Scenario("pre_and_post_hold", "Driver energized (hold) before and after the move.",
                 _hold_run_hold, _BASE),
        Scenario("slow_settle", "Slow mechanical/thermal settling after the start (tau 1.5 s).",
                 _idle_run_idle, replace(_BASE, inrush_a=0.455, inrush_tau_s=1.5, run_s=8.0, idle_after_s=1.0)),
        Scenario("aliasing", "Step ripple at 201 Hz: aliases to 1 Hz at FAST ADC rate.",
                 _idle_run_idle, replace(_BASE, ripple_a=0.060, ripple_hz=201.0)),
        Scenario("run_to_end", "Device still running when the capture ends.",
                 _run_to_end, replace(_BASE, run_s=6.0)),
        Scenario("near_sync_ripple", "Known limit: 200.05 Hz ripple beats slowly with FAST conversions.",
                 _idle_run_idle, replace(_BASE, ripple_a=0.080, ripple_hz=200.05)),
    )
}
