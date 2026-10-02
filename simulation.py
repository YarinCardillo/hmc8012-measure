"""Physical simulation of a DC current capture through the HMC8012.

Two layers, kept separate so each can be tested on its own:

- ``true_current``: the continuous supply current drawn by the device under
  test, built from a sequence of :class:`Phase` (idle, start transient,
  running with step ripple and burst/PWM load, hold, ...).
- ``sample_capture``: how the HMC8012 and the capture loop turn that current
  into samples. Each ADC conversion integrates the current over an aperture,
  is quantized to the range resolution, and is returned by ``READ?`` polls
  whose cadence is set by the transport latency, not by the ADC rate.

Instrument facts come from the HMC8012 user manual (DC current readings per
second per ADC rate). Aperture length, display counts and over-range limit are
not stated in the manual and are modelled as explicit, adjustable assumptions.
"""

import math
from dataclasses import dataclass

import numpy as np

from capture import DEFAULT_MAX_CONSECUTIVE_FAILURES

# DC current readings per second for each ADC rate (HMC8012 user manual, specs).
ADC_READINGS_PER_SECOND = {"SLOW": 5.0, "MED": 10.0, "FAST": 200.0}
# Approximate display counts: 5 3/4 digits at SLOW, 4 3/4 digits at MED/FAST.
DISPLAY_COUNTS = {"SLOW": 200_000, "MED": 20_000, "FAST": 20_000}
# Readings above range * factor overflow; the capture loop drops them.
OVER_RANGE_FACTOR = 1.2
FINE_SAMPLE_RATE_HZ = 20_000.0
MIN_POLL_LATENCY_S = 1e-3
# A start transient is considered over after this many time constants.
TRANSIENT_TIME_CONSTANTS = 5.0
READ_MODES = ("latest", "fresh")


@dataclass(frozen=True)
class Phase:
    """One segment of the device current profile.

    Attributes:
        name: Label used in error messages (e.g. "idle", "run", "hold").
        duration_s: Phase length in seconds.
        level_a: Steady current of the phase in amperes.
        start_level_a: If set, the current starts here and decays
            exponentially to *level_a* with time constant *tau_s* (inrush).
        tau_s: Time constant of the start transient.
        ripple_a: Peak amplitude of a sinusoidal ripple (e.g. step frequency).
        ripple_hz: Ripple frequency.
        burst_a: Extra current drawn during the on-time of a periodic burst.
        burst_period_s: Burst (PWM) period.
        burst_duty: Fraction of the period with the burst on (0..1).
        is_target: True for the phase whose stable value the analyzer must find.
    """

    name: str
    duration_s: float
    level_a: float
    start_level_a: float | None = None
    tau_s: float = 0.0
    ripple_a: float = 0.0
    ripple_hz: float = 0.0
    burst_a: float = 0.0
    burst_period_s: float = 0.0
    burst_duty: float = 0.0
    is_target: bool = False

    def __post_init__(self) -> None:
        if self.duration_s <= 0:
            raise ValueError(f"Phase '{self.name}': duration_s must be > 0, got {self.duration_s}")
        if not 0.0 <= self.burst_duty <= 1.0:
            raise ValueError(f"Phase '{self.name}': burst_duty must be in [0, 1], got {self.burst_duty}")


@dataclass(frozen=True)
class InstrumentModel:
    """HMC8012 DC current acquisition model.

    Attributes:
        adc_rate: "SLOW", "MED" or "FAST".
        range_a: Fixed DCI range in amperes (0.02, 0.2, 2 or 10).
        noise_a: Gaussian noise (1 sigma) added to each conversion.
        aperture_fraction: Integration time as a fraction of the conversion
            period. Not stated in the manual; < 1 lets ripple alias.
        poll_latency_s: Mean round trip of one ``READ?`` query.
        latency_jitter_s: Uniform jitter (+/-) on the round trip.
        read_mode: "latest" (AUTO trigger: ``READ?`` returns the last finished
            conversion, duplicates when polling faster than the ADC) or
            "fresh" (``READ?`` waits for the next conversion).
    """

    adc_rate: str = "FAST"
    range_a: float = 2.0
    noise_a: float = 0.0005
    aperture_fraction: float = 0.5
    poll_latency_s: float = 0.008
    latency_jitter_s: float = 0.002
    read_mode: str = "latest"

    def __post_init__(self) -> None:
        if self.adc_rate not in ADC_READINGS_PER_SECOND:
            raise ValueError(f"Unknown ADC rate '{self.adc_rate}'. Valid: {', '.join(ADC_READINGS_PER_SECOND)}")
        if self.read_mode not in READ_MODES:
            raise ValueError(f"Unknown read mode '{self.read_mode}'. Valid: {', '.join(READ_MODES)}")
        if self.range_a <= 0:
            raise ValueError(f"range_a must be > 0, got {self.range_a}")
        if not 0.0 < self.aperture_fraction <= 1.0:
            raise ValueError(f"aperture_fraction must be in (0, 1], got {self.aperture_fraction}")

    @property
    def conversion_period_s(self) -> float:
        """Time between two ADC conversions."""
        return 1.0 / ADC_READINGS_PER_SECOND[self.adc_rate]

    @property
    def resolution_a(self) -> float:
        """Smallest current step the instrument reports at this rate and range."""
        return self.range_a / DISPLAY_COUNTS[self.adc_rate]


@dataclass(frozen=True)
class SimulatedCapture:
    """Simulated capture plus the ground truth needed to grade an analysis.

    Attributes:
        timestamps: Sample times as the capture loop records them (seconds).
        values: Sampled current values (amperes).
        true_time: Fine time grid of the true device current.
        true_current: True device current on *true_time*.
        expected_value: Mean true current of the target phase after its
            start transient: the value a correct analyzer must report.
        target_start_s: Start of the window used for *expected_value*.
        target_end_s: End of the window used for *expected_value*.
        aborted: True if consecutive over-range readings ended the capture.
    """

    timestamps: np.ndarray
    values: np.ndarray
    true_time: np.ndarray
    true_current: np.ndarray
    expected_value: float
    target_start_s: float
    target_end_s: float
    aborted: bool = False


def true_current(
    phases: list[Phase],
    fine_rate_hz: float = FINE_SAMPLE_RATE_HZ,
) -> tuple[np.ndarray, np.ndarray]:
    """Build the continuous device current for a sequence of phases.

    Args:
        phases: Ordered phases of the profile.
        fine_rate_hz: Rate of the fine time grid (must resolve the ripple).

    Returns:
        ``(time, current)`` arrays on a uniform fine grid starting at 0.
    """
    times, currents = [], []
    phase_start = 0.0
    for phase in phases:
        local_time = np.arange(0.0, phase.duration_s, 1.0 / fine_rate_hz)
        if len(local_time) < 2:
            raise ValueError(f"Phase '{phase.name}' is shorter than two simulation steps ({2 / fine_rate_hz:g} s)")
        times.append(phase_start + local_time)
        currents.append(_phase_current(phase, local_time))
        phase_start += phase.duration_s
    return np.concatenate(times), np.concatenate(currents)


def sample_capture(
    time: np.ndarray,
    current: np.ndarray,
    instrument: InstrumentModel,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Sample a true current the way the HMC8012 capture loop does.

    Over-range readings become NaN markers and a run of consecutive ones ends
    the capture, mirroring ``ContinuousCapture`` and its ``RangeOverflowError``
    handling.

    Args:
        time: Uniform fine time grid of the true current.
        current: True current on *time*.
        instrument: Acquisition model.
        rng: Random generator for noise and latency jitter.

    Returns:
        ``(timestamps, values, is_aborted)`` as recorded by the capture loop.
    """
    conversions = _conversions(time, current, instrument, rng)
    duration = time[-1] + (time[1] - time[0])
    if instrument.read_mode == "latest":
        poll_times, conversion_index = _poll_latest(duration, instrument, rng)
    else:
        poll_times, conversion_index = _poll_fresh(duration, instrument, rng)
    values = np.where(
        np.abs(conversions[conversion_index]) <= instrument.range_a * OVER_RANGE_FACTOR,
        conversions[conversion_index], np.nan,
    )
    stop = _first_failure_run(np.isnan(values), DEFAULT_MAX_CONSECUTIVE_FAILURES)
    is_aborted = stop < len(values)
    end = stop + DEFAULT_MAX_CONSECUTIVE_FAILURES if is_aborted else len(values)
    return poll_times[:end], values[:end], is_aborted


def simulate_capture(
    phases: list[Phase],
    instrument: InstrumentModel,
    seed: int = 0,
) -> SimulatedCapture:
    """Simulate one full capture and compute its ground truth.

    Args:
        phases: Device current profile; exactly one phase must be the target.
        instrument: Acquisition model.
        seed: Seed for noise and latency jitter (same seed, same capture).

    Returns:
        :class:`SimulatedCapture`.

    Raises:
        ValueError: If the profile does not contain exactly one target phase,
            or the target phase ends before its start transient settles.
    """
    targets = [index for index, phase in enumerate(phases) if phase.is_target]
    if len(targets) != 1:
        raise ValueError(f"Profile needs exactly one target phase, found {len(targets)}")
    time, current = true_current(phases)
    timestamps, values, is_aborted = sample_capture(time, current, instrument, np.random.default_rng(seed))
    target_start, target_end = _target_window(phases, targets[0])
    if target_end <= target_start:
        raise ValueError(
            f"Target phase '{phases[targets[0]].name}' ends before its start transient settles "
            f"({TRANSIENT_TIME_CONSTANTS:g} x tau): lengthen it or shorten tau"
        )
    in_target = (time >= target_start) & (time < target_end)
    if not np.any(in_target):
        raise ValueError(f"Target phase '{phases[targets[0]].name}' has no settled samples to average")
    return SimulatedCapture(
        timestamps=timestamps,
        values=values,
        true_time=time,
        true_current=current,
        expected_value=float(np.mean(current[in_target])),
        target_start_s=target_start,
        target_end_s=target_end,
        aborted=is_aborted,
    )


def _phase_current(phase: Phase, local_time: np.ndarray) -> np.ndarray:
    """True current of one phase on its local time axis."""
    current = np.full_like(local_time, phase.level_a)
    if phase.start_level_a is not None and phase.tau_s > 0:
        current += (phase.start_level_a - phase.level_a) * np.exp(-local_time / phase.tau_s)
    if phase.ripple_a and phase.ripple_hz:
        current += phase.ripple_a * np.sin(2.0 * np.pi * phase.ripple_hz * local_time)
    if phase.burst_a and phase.burst_period_s > 0:
        is_on = (local_time % phase.burst_period_s) < phase.burst_duty * phase.burst_period_s
        current += phase.burst_a * is_on
    return current


def _target_window(phases: list[Phase], target_index: int) -> tuple[float, float]:
    """Time window of the target phase, excluding its start transient."""
    start = sum(phase.duration_s for phase in phases[:target_index])
    target = phases[target_index]
    settle = TRANSIENT_TIME_CONSTANTS * target.tau_s if target.start_level_a is not None else 0.0
    return start + settle, start + target.duration_s


def _conversions(
    time: np.ndarray,
    current: np.ndarray,
    instrument: InstrumentModel,
    rng: np.random.Generator,
) -> np.ndarray:
    """Value of each ADC conversion; index k is the one finishing at k periods.

    Index 0 is a placeholder and is never returned by a poll.
    """
    step = time[1] - time[0]
    edges = np.arange(len(current) + 1) * step
    integral = np.concatenate(([0.0], np.cumsum(current) * step))
    period = instrument.conversion_period_s
    aperture = instrument.aperture_fraction * period
    ends = np.arange(int(edges[-1] / period) + 1) * period
    starts = np.maximum(ends - aperture, 0.0)
    means = (np.interp(ends, edges, integral) - np.interp(starts, edges, integral)) / aperture
    noisy = means + rng.normal(0.0, instrument.noise_a, len(means)) if instrument.noise_a > 0 else means
    return np.round(noisy / instrument.resolution_a) * instrument.resolution_a


def _first_failure_run(is_failure: np.ndarray, run_length: int) -> int:
    """Index where *run_length* consecutive failures start, or the length if none."""
    count = 0
    for index, failed in enumerate(is_failure.tolist()):
        count = count + 1 if failed else 0
        if count >= run_length:
            return index - run_length + 1
    return len(is_failure)


def _latencies(count: int, instrument: InstrumentModel, rng: np.random.Generator) -> np.ndarray:
    """Round-trip time of *count* consecutive ``READ?`` queries."""
    jitter = rng.uniform(-instrument.latency_jitter_s, instrument.latency_jitter_s, count)
    return np.maximum(instrument.poll_latency_s + jitter, MIN_POLL_LATENCY_S)


def _poll_latest(
    duration: float,
    instrument: InstrumentModel,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Back-to-back polls returning the last conversion finished at issue time."""
    period = instrument.conversion_period_s
    shortest_latency = max(instrument.poll_latency_s - instrument.latency_jitter_s, MIN_POLL_LATENCY_S)
    latencies = _latencies(math.ceil(duration / shortest_latency) + 1, instrument, rng)
    issue_times = period + np.concatenate(([0.0], np.cumsum(latencies[:-1])))
    response_times = issue_times + latencies
    keep = response_times < duration
    conversion_index = np.floor(issue_times[keep] / period + 1e-9).astype(int)
    return response_times[keep], conversion_index


def _poll_fresh(
    duration: float,
    instrument: InstrumentModel,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Polls that each wait for the next conversion before answering."""
    period = instrument.conversion_period_s
    response_times, conversion_index = [], []
    issue_time = 0.0
    while True:
        index = math.floor(issue_time / period + 1e-9) + 1
        response = index * period + float(_latencies(1, instrument, rng)[0])
        if response >= duration:
            break
        response_times.append(response)
        conversion_index.append(index)
        issue_time = response
    return np.array(response_times), np.array(conversion_index, dtype=int)
