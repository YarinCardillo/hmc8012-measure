"""Shared pytest fixtures for synthetic motor current waveforms.

All fixtures use ``numpy.random.default_rng(seed)`` for reproducibility
(NOT the deprecated ``numpy.random.seed()`` global state).

Each factory fixture returns a callable so tests can generate waveforms
with custom parameters while keeping the default values concise.
"""

import numpy as np
import pytest

from hmc8012 import ScpiError


@pytest.fixture
def make_motor_waveform():
    """Factory fixture: single-peak motor current waveform.

    Generates a realistic waveform with:
    - Flat baseline at *stable_current*
    - Exponential-decay spike from *peak_current* at *peak_time*,
      settling over *settling_time* back to *stable_current*
    - Additive Gaussian measurement noise

    Returns:
        Callable that produces ``(timestamps, values)`` numpy arrays.
    """

    def _factory(
        *,
        duration: float = 5.0,
        sample_rate: float = 100.0,
        stable_current: float = 0.5,
        peak_current: float = 3.0,
        peak_time: float = 0.5,
        settling_time: float = 1.5,
        noise_std: float = 0.005,
        rng_seed: int = 42,
    ) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(rng_seed)
        n_samples = int(duration * sample_rate)
        timestamps = np.linspace(0, duration, n_samples)

        values = np.full(n_samples, stable_current)

        peak_idx = int(peak_time * sample_rate)
        settle_idx = int((peak_time + settling_time) * sample_rate)
        settle_idx = min(settle_idx, n_samples)

        decay_len = settle_idx - peak_idx
        if decay_len > 0:
            decay = np.exp(-np.linspace(0, 5, decay_len))
            values[peak_idx:settle_idx] = (
                stable_current + (peak_current - stable_current) * decay
            )

        values += rng.normal(0, noise_std, n_samples)
        return timestamps, values

    return _factory


@pytest.fixture
def make_multi_peak_waveform():
    """Factory fixture: multi-peak motor current waveform.

    Accepts a list of ``(peak_time, peak_current)`` tuples.  Each peak
    produces an independent exponential decay to *stable_current*.
    Later peaks overwrite earlier ones where they overlap.

    Returns:
        Callable that produces ``(timestamps, values)`` numpy arrays.
    """

    def _factory(
        *,
        peaks: list[tuple[float, float]],
        duration: float = 5.0,
        sample_rate: float = 100.0,
        stable_current: float = 0.5,
        settling_time: float = 1.0,
        noise_std: float = 0.005,
        rng_seed: int = 42,
    ) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(rng_seed)
        n_samples = int(duration * sample_rate)
        timestamps = np.linspace(0, duration, n_samples)

        values = np.full(n_samples, stable_current)

        for peak_time, peak_current in peaks:
            peak_idx = int(peak_time * sample_rate)
            settle_idx = int((peak_time + settling_time) * sample_rate)
            settle_idx = min(settle_idx, n_samples)

            decay_len = settle_idx - peak_idx
            if decay_len > 0:
                decay = np.exp(-np.linspace(0, 5, decay_len))
                values[peak_idx:settle_idx] = (
                    stable_current + (peak_current - stable_current) * decay
                )

        values += rng.normal(0, noise_std, n_samples)
        return timestamps, values

    return _factory


@pytest.fixture
def make_flat_waveform():
    """Factory fixture: flat signal with no peaks (noise only).

    Returns:
        Callable that produces ``(timestamps, values)`` numpy arrays.
    """

    def _factory(
        *,
        duration: float = 5.0,
        sample_rate: float = 100.0,
        current_level: float = 0.5,
        noise_std: float = 0.005,
        rng_seed: int = 42,
    ) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(rng_seed)
        n_samples = int(duration * sample_rate)
        timestamps = np.linspace(0, duration, n_samples)

        values = np.full(n_samples, current_level) + rng.normal(
            0, noise_std, n_samples
        )
        return timestamps, values

    return _factory


@pytest.fixture
def make_overflow_waveform():
    """Factory fixture: inject overflow sentinels into an existing waveform.

    Replaces values at *indices* (or at random positions up to a given
    *fraction*) with the overflow sentinel ``9.90000000e+37``.

    Returns:
        Callable that takes ``(timestamps, values)`` and returns a new
        ``(timestamps, values)`` tuple with sentinels injected.
    """
    SENTINEL = 9.90000000e+37

    def _factory(
        timestamps: np.ndarray,
        values: np.ndarray,
        *,
        indices: list[int] | None = None,
        fraction: float | None = None,
        rng_seed: int = 99,
    ) -> tuple[np.ndarray, np.ndarray]:
        ts = timestamps.copy()
        vals = values.copy()

        if indices is not None:
            for idx in indices:
                vals[idx] = SENTINEL
        elif fraction is not None:
            rng = np.random.default_rng(rng_seed)
            n_overflow = int(len(vals) * fraction)
            overflow_indices = rng.choice(len(vals), size=n_overflow, replace=False)
            vals[overflow_indices] = SENTINEL
        else:
            raise ValueError("Provide either 'indices' or 'fraction'")

        return ts, vals

    return _factory


@pytest.fixture
def make_fake_instrument():
    """Factory fixture: fake instrument satisfying InstrumentProtocol for capture tests."""

    class FakeInstrument:
        def __init__(
            self,
            readings: list[float],
            *,
            function: str = "CURR",
            adc_rate: str = "FAST",
            range_auto: bool = False,
        ):
            self._readings = list(readings)
            self._index = 0
            self._function = function
            self._adc_rate = adc_rate
            self._range_auto = range_auto

        def measure_fast(self) -> float:
            if self._index >= len(self._readings):
                raise ScpiError("No more readings")
            value = self._readings[self._index]
            self._index += 1
            return value

        def get_function(self) -> str:
            return self._function

        def get_adc_rate(self) -> str:
            return self._adc_rate

        def get_range_auto(self, function: str) -> bool:
            return self._range_auto

    def _factory(
        readings: list[float],
        *,
        function: str = "CURR",
        adc_rate: str = "FAST",
        range_auto: bool = False,
    ):
        return FakeInstrument(
            readings, function=function, adc_rate=adc_rate, range_auto=range_auto
        )

    return _factory
