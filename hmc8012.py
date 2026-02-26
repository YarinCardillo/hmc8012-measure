"""Rohde & Schwarz HMC8012 instrument driver over PyVISA.

Supports LAN (TCPIP socket, port 5025) and COM (serial/VCP) connections.
Connection does not reset the instrument — function and range persist across
invocations. Use reset() explicitly to restore factory defaults.
"""

import logging
import pyvisa

logger = logging.getLogger(__name__)


class ScpiError(Exception):
    """Raised when the instrument reports a SCPI error."""


class RangeOverflowError(Exception):
    """Raised when the instrument returns the overflow sentinel."""


class HMC8012:
    """Driver for the R&S HMC8012 digital multimeter."""

    OVERFLOW_SENTINEL = 9.90000000E+37
    SCPI_PORT = 5025
    DEFAULT_TIMEOUT_MS = 5000
    MAX_ERROR_QUEUE_DEPTH = 50

    # Valid CLI function names for measurement
    VALID_FUNCTIONS = {
        "dcv", "acv", "dci", "aci", "res", "fres",
        "cap", "temp", "freq", "cont", "diod",
    }

    # Maps CLI function names to SCPI CONFigure command (used by set_range)
    FUNCTION_SCPI_MAP = {
        "dcv":  "CONF:VOLT:DC",
        "acv":  "CONF:VOLT:AC",
        "dci":  "CONF:CURR:DC",
        "aci":  "CONF:CURR:AC",
        "res":  "CONF:RES",
        "fres": "CONF:FRES",
        "cap":  "CONF:CAP",
        "temp": "CONF:TEMP",
        "freq": "CONF:FREQ",
        "cont": "CONF:CONT",
        "diod": "CONF:DIOD",
    }

    # Maps function names to SENSe SCPI prefix for standalone range control
    RANGE_SCPI_MAP = {
        "dcv":  "VOLT:DC:RANGE",
        "acv":  "VOLT:AC:RANGE",
        "dci":  "CURR:DC:RANGE",
        "aci":  "CURR:AC:RANGE",
        "res":  "RES:RANGE",
        "fres": "FRES:RANGE",
        "cap":  "CAP:RANGE",
    }

    def __init__(self, address: str, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        self._resource_string = self._build_resource_string(address)
        self._timeout_ms = timeout_ms
        self._instrument: pyvisa.resources.MessageBasedResource | None = None
        self._resource_manager: pyvisa.ResourceManager | None = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def connect(self) -> None:
        """Open VISA connection, clear errors, enable remote mode.

        Does NOT reset the instrument — call reset() explicitly to restore
        factory defaults. This preserves any previously configured function
        and range across separate invocations.
        """
        self._resource_manager = pyvisa.ResourceManager("@py")
        self._instrument = self._resource_manager.open_resource(
            self._resource_string,
            open_timeout=self._timeout_ms,
        )
        self._instrument.read_termination = "\n"
        self._instrument.write_termination = "\n"
        self._instrument.timeout = self._timeout_ms

        self._write("*CLS")
        self._write("SYSTem:REMote")
        identity = self._query("*IDN?")
        logger.info("Connected: %s", identity)

    def close(self) -> None:
        """Drain error queue, restore local control, close connection."""
        if self._instrument is None:
            return
        try:
            self._drain_error_queue()
            self._write("SYSTem:LOCal")
        finally:
            self._instrument.close()
            self._instrument = None
            if self._resource_manager is not None:
                self._resource_manager.close()
                self._resource_manager = None

    def reset(self) -> None:
        """Reset instrument to factory defaults and clear error queue."""
        self._write("*RST")
        self._write("*CLS")
        self._query("*OPC?")

    def identify(self) -> str:
        """Return instrument identification string."""
        return self._query("*IDN?")

    def measure(self) -> float:
        """Trigger a measurement and return the result.

        Uses whatever function and range are currently configured on the
        instrument. Call set_range() beforehand to configure, or reset()
        to restore defaults.

        Returns:
            The measurement value as a float.

        Raises:
            RangeOverflowError: If the instrument returns the overflow sentinel.
            ScpiError: If the instrument reports a SCPI error.
        """
        raw = self._query("READ?")
        try:
            value = float(raw)
        except ValueError as exc:
            raise ScpiError(f"Invalid measurement response: '{raw}'") from exc

        if value >= self.OVERFLOW_SENTINEL:
            raise RangeOverflowError(
                f"Range overflow (sentinel {raw}). "
                "Use a wider range or check probe connections."
            )

        self._check_errors()
        return value

    def set_range(self, function: str, range_value: str = "AUTO") -> None:
        """Configure measurement function and range without triggering.

        Sends CONFigure to select the function, then sets range via SENSe
        commands. The configuration persists until the next set_range() or
        reset() call.

        Args:
            function: One of the keys in RANGE_SCPI_MAP
                      (dcv, acv, dci, aci, res, fres, cap).
            range_value: "AUTO" to enable auto-ranging, or a numeric string
                         in SI base units (e.g. "2" for 2A, "0.4" for 400mV).

        Raises:
            ValueError: If function doesn't support range selection.
        """
        function = function.lower()
        if function not in self.RANGE_SCPI_MAP:
            valid = ", ".join(sorted(self.RANGE_SCPI_MAP.keys()))
            raise ValueError(
                f"Function '{function}' does not support range. "
                f"Valid: {valid}"
            )

        # Select the measurement function
        self._write(self.FUNCTION_SCPI_MAP[function])

        # Set range via SENSe commands (survives across READ? calls)
        prefix = self.RANGE_SCPI_MAP[function]
        if range_value.upper() == "AUTO":
            self._write(f"{prefix}:AUTO ON")
        else:
            self._write(f"{prefix}:AUTO OFF")
            self._write(f"{prefix} {range_value}")

        self._query("*OPC?")

    # -- Private helpers --

    def _check_errors(self) -> None:
        """Read SYST:ERR? and raise if the instrument reports an error."""
        response = self._query("SYST:ERR?")
        # Response format: <code>, "<description>"
        # "0" or "+0" means no error
        code_str = response.split(",")[0].strip()
        if code_str not in ("0", "+0"):
            raise ScpiError(f"Instrument error: {response}")

    def _drain_error_queue(self) -> None:
        """Read all errors from the queue until empty."""
        for _ in range(self.MAX_ERROR_QUEUE_DEPTH):
            response = self._query("SYST:ERR?")
            code_str = response.split(",")[0].strip()
            if code_str in ("0", "+0"):
                break

    def _write(self, command: str) -> None:
        """Send a SCPI command to the instrument."""
        if self._instrument is None:
            raise ConnectionError("Not connected. Call connect() first.")
        self._instrument.write(command)

    def _query(self, command: str) -> str:
        """Send a SCPI query and return the response string."""
        if self._instrument is None:
            raise ConnectionError("Not connected. Call connect() first.")
        return self._instrument.query(command).strip()

    @staticmethod
    def _build_resource_string(address: str) -> str:
        """Auto-detect connection type and build VISA resource string.

        - IP address (contains '.') → TCPIP::<ip>::5025::SOCKET
        - COM port (e.g. 'COM3')   → ASRL3::INSTR
        - Otherwise                 → pass through as raw VISA string
        """
        if "." in address:
            return f"TCPIP::{address}::{HMC8012.SCPI_PORT}::SOCKET"

        upper = address.upper()
        if upper.startswith("COM"):
            port_number = upper[3:]
            if not port_number.isdigit():
                raise ValueError(
                    f"Invalid COM port '{address}'. Expected format: COM1, COM3, etc."
                )
            return f"ASRL{port_number}::INSTR"

        raise ValueError(
            f"Invalid address '{address}'. "
            "Expected an IP address (e.g. 192.168.0.2) or COM port (e.g. COM3)."
        )
