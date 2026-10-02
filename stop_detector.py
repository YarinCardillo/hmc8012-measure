"""Auto-stop of a capture: ends it once the device has run and is back at idle.

Uses the analyzer's idle and run definitions, so a capture stops on the same
run the analysis will find: the idle reference is the steady idle the
analysis requires at capture start, and a reading is running when it is above
idle by the analyzer's run threshold.
"""

import math

from analyzer import MIN_IDLE_S, RUN_THRESHOLD_TOLERANCES, AnalysisConfig

# Seconds of continuous idle after a run that end the capture. Longer than the
# pauses inside one movement (under about 1 s), so a pause does not stop it.
# TODO(Yarin, 2026-10-02): Tune on the lab recordings of real movements.
STOP_HOLD_S = 3.0


class StopDetector:
    """Decides, reading by reading, when a capture can stop.

    The capture stops once the current has been running for at least
    ``config.min_run_s`` and then stayed at idle for *hold_s*. A failed
    reading counts as running: it is often an overflowing peak.
    """

    def __init__(self, hold_s: float = STOP_HOLD_S, config: AnalysisConfig = AnalysisConfig()) -> None:
        self._hold_s = hold_s
        self._config = config
        self._idle_end_s = MIN_IDLE_S + config.smoothing_window_s / 2.0
        self._idle_readings: list[float] = []
        self._threshold: float | None = None
        self._running_since: float | None = None
        self._last_running_s = 0.0
        self._has_run = False

    def should_stop(self, time_s: float, value: float) -> bool:
        """Record one reading; True when the capture can stop at it.

        Args:
            time_s: Reading time in seconds from capture start.
            value: Current in amperes, NaN for a failed reading.
        """
        if self._threshold is None:
            self._learn_idle(time_s, value)
            if self._threshold is None:
                return False
        if not math.isfinite(value) or value > self._threshold:
            self._record_running(time_s)
            return False
        self._running_since = None
        return self._has_run and time_s - self._last_running_s >= self._hold_s

    def _learn_idle(self, time_s: float, value: float) -> None:
        """Average the idle readings at capture start, then fix the run threshold."""
        if time_s < self._idle_end_s or not self._idle_readings:
            if math.isfinite(value):
                self._idle_readings.append(value)
            return
        idle = sum(self._idle_readings) / len(self._idle_readings)
        self._threshold = idle + RUN_THRESHOLD_TOLERANCES * self._config.tolerance(idle)

    def _record_running(self, time_s: float) -> None:
        if self._running_since is None:
            self._running_since = time_s
        self._last_running_s = time_s
        if time_s - self._running_since >= self._config.min_run_s:
            self._has_run = True
