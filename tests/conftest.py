"""Shared pytest fixtures.

Realistic current waveforms come from the physical simulation
(simulation.py, scenarios.py), not from fixtures.
"""

import pytest

from hmc8012 import ScpiError


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
