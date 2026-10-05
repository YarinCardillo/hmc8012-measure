"""Tests for HMC8012 driver SCPI methods (state queries and measure_fast)."""

import pyvisa
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


@pytest.mark.parametrize("call", [
    lambda hmc: hmc.set_range("dci", "abc"),
    lambda hmc: hmc.set_function("dci"),
    lambda hmc: hmc.set_adc_rate("SLOW"),
    lambda hmc: hmc.reset(),
])
def test_setters_raise_when_instrument_reports_error(call):
    hmc = _hmc8012_with_mock()
    hmc._instrument._query_responses = ["1", "-222,\"Data out of range\""]
    with pytest.raises(ScpiError, match="-222"):
        call(hmc)


def test_measure_raises_on_negative_overflow(monkeypatch):
    monkeypatch.setattr("hmc8012.time.sleep", lambda seconds: None)
    hmc = _hmc8012_with_mock()
    hmc._instrument._query_responses = ["-9.90000000E+37", "-9.90000000E+37"]
    with pytest.raises(RangeOverflowError):
        hmc.measure()


def test_measure_fast_raises_on_negative_overflow():
    hmc = _hmc8012_with_mock()
    hmc._instrument._query_responses = ["-9.90000000E+37"]
    with pytest.raises(RangeOverflowError):
        hmc.measure_fast()


class _TeardownFailure(MockVisaResource):
    """Answers normally, then times out on every query once closing starts."""

    def __init__(self):
        super().__init__()
        self.closed = False
        self.failing = False

    def query(self, command: str) -> str:
        if self.failing:
            raise pyvisa.errors.VisaIOError(pyvisa.constants.StatusCode.error_timeout)
        return super().query(command)

    def close(self) -> None:
        self.closed = True


def test_close_never_raises_and_still_releases_the_session():
    hmc = HMC8012("192.168.0.1")
    resource = _TeardownFailure()
    hmc._instrument, hmc._resource_manager = resource, None
    resource.failing = True
    hmc.close()
    assert resource.closed
    assert "SYSTem:LOCal" in resource._written_commands
    assert hmc._instrument is None


class _StaleReplies(MockVisaResource):
    """A reply left in the input buffer by an earlier command, then a read timeout."""

    def __init__(self, stale: list[str]):
        super().__init__()
        self._stale = list(stale)
        self.timeout = 8000

    def read(self) -> str:
        if self._stale:
            return self._stale.pop(0)
        raise pyvisa.errors.VisaIOError(pyvisa.constants.StatusCode.error_timeout)


def test_stale_replies_are_discarded_and_the_timeout_restored():
    hmc = _hmc8012_with_mock()
    hmc._instrument = _StaleReplies(["1", "+1.234E-03"])
    hmc._discard_pending_replies()
    assert hmc._instrument._stale == []
    assert hmc._instrument.timeout == 8000


def test_an_overflow_from_an_earlier_conversion_is_read_again(monkeypatch):
    monkeypatch.setattr("hmc8012.time.sleep", lambda seconds: None)
    hmc = _hmc8012_with_mock()
    hmc._instrument._query_responses = ["+9.90000000E+37", "+1.23400000E-02", "0,\"No error\""]
    assert hmc.measure() == pytest.approx(0.01234)


def test_an_overflow_on_both_readings_raises(monkeypatch):
    monkeypatch.setattr("hmc8012.time.sleep", lambda seconds: None)
    hmc = _hmc8012_with_mock()
    hmc._instrument._query_responses = ["+9.90000000E+37", "+9.90000000E+37"]
    with pytest.raises(RangeOverflowError):
        hmc.measure()
