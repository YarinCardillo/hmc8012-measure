"""CLI entry point for HMC8012 multimeter measurements.

Usage:
    python measure.py <address> <function> [delay_seconds]

Arguments:
    address        IP address (e.g. 192.168.0.2) or COM port (e.g. COM3)
    function       Measurement type: dcv|acv|dci|aci|res|fres|cap|temp|freq|cont|diod
    delay_seconds  Optional wait before measuring (default: 0)

Output:
    Writes the measurement value (or "ERR") to result.txt in the script directory.
"""

import sys
import time
from pathlib import Path

from hmc8012 import HMC8012

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = SCRIPT_DIR / "result.txt"
VALID_FUNCTIONS = sorted(HMC8012.FUNCTION_MAP.keys())


def parse_args(argv: list[str]) -> tuple[str, str, float]:
    """Parse positional CLI arguments.

    Args:
        argv: sys.argv (program name + arguments).

    Returns:
        Tuple of (address, function, delay_seconds).

    Raises:
        SystemExit: On invalid arguments (prints usage to stderr).
    """
    args = argv[1:]

    if len(args) < 2 or len(args) > 3:
        _usage_error(
            f"Expected 2-3 arguments, got {len(args)}."
        )

    address = args[0]
    function = args[1].lower()
    delay = 0.0

    if function not in HMC8012.FUNCTION_MAP:
        _usage_error(
            f"Unknown function '{function}'. "
            f"Valid: {', '.join(VALID_FUNCTIONS)}"
        )

    if len(args) == 3:
        try:
            delay = float(args[2])
        except ValueError:
            _usage_error(f"Delay must be a number, got '{args[2]}'.")
        if delay < 0:
            _usage_error(f"Delay must be >= 0, got {delay}.")

    return address, function, delay


def write_result(value: str, output_path: Path = DEFAULT_OUTPUT) -> None:
    """Write a single result line to the output file (overwrites)."""
    output_path.write_text(value + "\n", encoding="utf-8")


def _usage_error(message: str) -> None:
    """Print error and usage, then exit with code 1."""
    print(f"Error: {message}", file=sys.stderr)
    print(
        "Usage: python measure.py <address> <function> [delay_seconds]",
        file=sys.stderr,
    )
    print(
        f"Functions: {', '.join(VALID_FUNCTIONS)}",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> None:
    """Parse args, connect to instrument, measure, write result."""
    address, function, delay = parse_args(sys.argv)

    try:
        with HMC8012(address) as dmm:
            if delay > 0:
                print(
                    f"Waiting {delay}s for device positioning...",
                    file=sys.stderr,
                )
                time.sleep(delay)

            result = dmm.measure(function)

        write_result(str(result))
        print(f"Result: {result}", file=sys.stderr)

    except Exception as exc:
        print(f"Measurement failed: {exc}", file=sys.stderr)
        write_result("ERR")
        sys.exit(1)


if __name__ == "__main__":
    main()
