"""Tests for HMC8012 driver SCPI methods (state queries and measure_fast)."""

import pytest

from hmc8012 import HMC8012, ScpiError, RangeOverflowError


class MockVisaResource:
    """Stubs query() and write() for testing without VISA hardware."""

    def __init__(self):
        self._query_responses = []
        self._written_commands = []

    def query(self, command: str) -> str:
        self._written_commands.append(command)
        if not self._query_responses:
            return "0,\"No error\""
        return self._query_responses.pop(0)

    def write(self, command: str) -> None:
        self._written_commands.append(command)


def _hmc8012_with_mock():
    """Build HMC8012 and inject MockVisaResource so connect() is not needed."""
    inst = HMC8012("192.168.0.1")
    inst._instrument = MockVisaResource()
    inst._resource_manager = None
    return inst


def test_get_function_returns_stripped_response():
    hmc = _hmc8012_with_mock()
    hmc._instrument._query_responses = ["CURR"]
    result = hmc.get_function()
    assert result == "CURR"
    assert "FUNC?" in hmc._instrument._written_commands


def test_get_adc_rate_returns_stripped_response():
    hmc = _hmc8012_with_mock()
    hmc._instrument._query_responses = ["FAST"]
    result = hmc.get_adc_rate()
    assert result == "FAST"
    assert "ADCRate?" in hmc._instrument._written_commands


def test_set_adc_rate_sends_correct_command():
    hmc = _hmc8012_with_mock()
    hmc._instrument._query_responses = ["0,\"No error\""]
    hmc.set_adc_rate("FAST")
    assert "ADCRate FAST" in hmc._instrument._written_commands
    assert "*OPC?" in hmc._instrument._written_commands


def test_set_adc_rate_rejects_invalid_rate():
    hmc = _hmc8012_with_mock()
    with pytest.raises(ValueError, match="Invalid ADC rate 'INVALID'"):
        hmc.set_adc_rate("INVALID")


def test_get_range_auto_returns_true_when_on():
    hmc = _hmc8012_with_mock()
    hmc._instrument._query_responses = ["1"]
    result = hmc.get_range_auto("dci")
    assert result is True
    assert "CURR:DC:RANGE:AUTO?" in hmc._instrument._written_commands


def test_get_range_auto_returns_false_when_off():
    hmc = _hmc8012_with_mock()
    hmc._instrument._query_responses = ["0"]
    result = hmc.get_range_auto("dci")
    assert result is False


def test_get_range_auto_rejects_invalid_function():
    hmc = _hmc8012_with_mock()
    with pytest.raises(ValueError, match="does not support range"):
        hmc.get_range_auto("temp")


def test_measure_fast_returns_value_without_error_check():
    hmc = _hmc8012_with_mock()
    hmc._instrument._query_responses = ["0.12345"]
    result = hmc.measure_fast()
    assert result == 0.12345
    assert "READ?" in hmc._instrument._written_commands
    assert "SYST:ERR?" not in hmc._instrument._written_commands


def test_measure_fast_raises_on_overflow():
    hmc = _hmc8012_with_mock()
    hmc._instrument._query_responses = ["9.90000000E+37"]
    with pytest.raises(RangeOverflowError, match="Range overflow"):
        hmc.measure_fast()
