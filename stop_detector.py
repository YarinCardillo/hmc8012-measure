"""Auto-stop of a capture: ends it once the device has run and is back at idle.

The capture may start during the deltastep peaks, so idle is not read at its
start: the reference is the lowest reading so far, which the device reaches
between the deltastep strokes and after the movement. A reading is running
when it is above that floor by the analyzer's run threshold, so the movement
itself keeps the capture going.
"""

import math

from analyzer import RUN_THRESHOLD_TOLERANCES, AnalysisConfig

# Seconds of continuous idle after a run that end the capture. Longer than the
# pauses inside one movement (under about 1 s), so a pause does not stop it.
# Still to tune on lab recordings of real movements.
STOP_HOLD_S = 3.0
# Seconds of continuous running before a return to idle can stop the capture.
MIN_RUN_S = 0.5


class StopDetector:
    """Decides, reading by reading, when a capture can stop.

    The capture stops once the current has been running for at least
    MIN_RUN_S and then stayed at idle for *hold_s*. A failed reading counts
    as running: it is often an overflowing peak.
    """

    def __init__(self, hold_s: float = STOP_HOLD_S, config: AnalysisConfig = AnalysisConfig()) -> None:
        self._hold_s = hold_s
        self._config = config
        self._floor = math.inf
        self._running_since: float | None = None
        self._last_running_s = 0.0
        self._has_run = False

    def should_stop(self, time_s: float, value: float) -> bool:
        """Record one reading; True when the capture can stop at it.

        Args:
            time_s: Reading time in seconds from capture start.
            value: Current in amperes, NaN for a failed reading.
        """
        if math.isfinite(value):
            self._floor = min(self._floor, value)
        if not math.isfinite(value) or value > self._threshold():
            self._record_running(time_s)
            return False
        self._running_since = None
        return self._has_run and time_s - self._last_running_s >= self._hold_s

    def _threshold(self) -> float:
        return self._floor + RUN_THRESHOLD_TOLERANCES * self._config.tolerance(self._floor)

    def _record_running(self, time_s: float) -> None:
        if self._running_since is None:
            self._running_since = time_s
        self._last_running_s = time_s
        if time_s - self._running_since >= MIN_RUN_S:
            self._has_run = True
