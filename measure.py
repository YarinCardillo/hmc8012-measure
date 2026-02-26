"""CLI entry point for HMC8012 multimeter operations.

Commands:
    python measure.py <address> <function> [delay]            Measure (READ? only)
    python measure.py <address> range <function> <value>      Configure function + range
    python measure.py <address> reset                         Reset instrument

Arguments:
    address    IP address (e.g. 192.168.0.2) or COM port (e.g. COM3)
    function   Measurement type: dcv|acv|dci|aci|res|fres|cap|temp|freq|cont|diod
    delay      Optional wait in seconds before measuring (default: 0)

Output:
    Measure writes the value (or "ERR") to result.txt in the script directory.
    Range and reset write "OK" (or "ERR") to result.txt.
"""

import sys
import time
from pathlib import Path

from hmc8012 import HMC8012

# sys.argv[0] always points to the actual script/executable being run.
# This is more reliable than __file__ when compiled with Nuitka (onefile mode),
# where __file__ resolves to a temp extraction directory instead of the exe location.
SCRIPT_DIR = Path(sys.argv[0]).resolve().parent
DEFAULT_OUTPUT = SCRIPT_DIR / "result.txt"
VALID_FUNCTIONS = sorted(HMC8012.VALID_FUNCTIONS)
VALID_RANGE_FUNCTIONS = sorted(HMC8012.RANGE_SCPI_MAP.keys())

# -- Command handlers -------------------------------------------------------

def cmd_measure(address: str, args: list[str]) -> None:
    """Handle: measure.py <address> <function> [delay]"""
    function = args[0]
    delay = 0.0

    if len(args) >= 2:
        try:
            delay = float(args[1])
        except ValueError:
            _usage_error(f"Delay must be a number, got '{args[1]}'.")
        if delay < 0:
            _usage_error(f"Delay must be >= 0, got {delay}.")

    try:
        with HMC8012(address) as dmm:
            if delay > 0:
                print(
                    f"Waiting {delay}s for device positioning...",
                    file=sys.stderr,
                )
                time.sleep(delay)

            result = dmm.measure()

        write_result(str(result))
        print(f"Result: {result}", file=sys.stderr)

    except Exception as exc:
        print(f"Measurement failed: {exc}", file=sys.stderr)
        write_result("ERR")
        sys.exit(1)


def cmd_range(address: str, args: list[str]) -> None:
    """Handle: measure.py <address> range <function> <value>"""
    if len(args) != 2:
        _usage_error(
            "range command requires: <function> <value>\n"
            f"  Functions: {', '.join(VALID_RANGE_FUNCTIONS)}"
        )

    function = args[0].lower()
    range_value = args[1]

    if function not in HMC8012.RANGE_SCPI_MAP:
        _usage_error(
            f"Function '{function}' does not support range. "
            f"Valid: {', '.join(VALID_RANGE_FUNCTIONS)}"
        )

    try:
        with HMC8012(address) as dmm:
            dmm.set_range(function, range_value)

        write_result("OK")
        print(f"Range set: {function} = {range_value}", file=sys.stderr)

    except Exception as exc:
        print(f"Set range failed: {exc}", file=sys.stderr)
        write_result("ERR")
        sys.exit(1)


def cmd_reset(address: str) -> None:
    """Handle: measure.py <address> reset"""
    try:
        with HMC8012(address) as dmm:
            dmm.reset()

        write_result("OK")
        print("Instrument reset.", file=sys.stderr)

    except Exception as exc:
        print(f"Reset failed: {exc}", file=sys.stderr)
        write_result("ERR")
        sys.exit(1)


# -- CLI dispatch ------------------------------------------------------------

def write_result(value: str, output_path: Path = DEFAULT_OUTPUT) -> None:
    """Write a single result line to the output file (overwrites)."""
    output_path.write_text(value + "\n", encoding="utf-8")


def _usage_error(message: str) -> None:
    """Print error and usage, then exit with code 1."""
    print(f"Error: {message}", file=sys.stderr)
    print(
        "Usage:\n"
        "  python measure.py <address> <function> [delay]            Measure\n"
        "  python measure.py <address> range <function> <value>      Set range\n"
        "  python measure.py <address> reset                         Reset",
        file=sys.stderr,
    )
    print(
        f"Functions: {', '.join(VALID_FUNCTIONS)}",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> None:
    """Dispatch CLI command based on arguments."""
    args = sys.argv[1:]

    if len(args) < 2:
        _usage_error(f"Expected at least 2 arguments, got {len(args)}.")

    address = args[0]
    command = args[1].lower()

    if command == "reset":
        if len(args) != 2:
            _usage_error("reset takes no additional arguments.")
        cmd_reset(address)

    elif command == "range":
        cmd_range(address, args[2:])

    elif command in HMC8012.VALID_FUNCTIONS:
        cmd_measure(address, args[1:])

    else:
        _usage_error(
            f"Unknown command '{command}'. "
            f"Expected a function ({', '.join(VALID_FUNCTIONS)}), "
            "'range', or 'reset'."
        )


if __name__ == "__main__":
    main()
