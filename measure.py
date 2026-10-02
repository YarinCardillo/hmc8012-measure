"""CLI entry point for HMC8012 multimeter operations.

Commands:
    python measure.py <address> <function> [delay]             Measure (READ? only)
    python measure.py <address> range <function> <value>       Configure function + range
    python measure.py <address> adc [SLOW|MED|FAST]            Read or set the ADC rate
    python measure.py <address> reset                          Reset instrument
    python measure.py <address> capture [duration] [timeout] [--save-samples]
                                                               Mean running DC current
    python measure.py --version                                Print the version

Arguments:
    address    IP address (e.g. 192.168.0.2) or COM port (e.g. COM3)
    function   Measurement type: dcv|acv|dci|aci|res|fres|cap|temp|freq|cont|diod
    delay      Optional wait in seconds before measuring (default: 0).
    duration   Capture window in seconds (default: 10).
    timeout    Optional. If omitted, timeout = duration + 10.

Output:
    Measure/capture write the value (or "ERR") to result.txt in the script directory.
    Range, adc (set) and reset write "OK" (or "ERR"); adc without a value writes the rate.
    result.txt is removed at script start and written atomically (temp file + replace).
    On error, line 2 holds the command context ([APP]) and line 3 the exception ([EXC]).
"""

import os
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pyvisa

from analyzer import AnalysisError, AnalysisResult, analyze_waveform
from capture import (
    CaptureAbortedError,
    CaptureConfigError,
    CaptureResult,
    ContinuousCapture,
    InsufficientSamplesError,
)
from hmc8012 import HMC8012, RangeOverflowError, ScpiError
from version import __version__

# sys.argv[0], not __file__: Nuitka onefile resolves __file__ to a temp extraction directory.
SCRIPT_DIR = Path(sys.argv[0]).resolve().parent
DEFAULT_OUTPUT = SCRIPT_DIR / "result.txt"
# Extra seconds added to duration when timeout is not given (analysis + margin)
CAPTURE_TIMEOUT_MARGIN = 10.0
# Prefix for raw capture samples file; name will be {prefix}_YYYY-MM-DD_HH-MM-SS.csv
CAPTURE_SAMPLES_FILE_PREFIX = "capture_samples"
SAVE_SAMPLES_FLAG = "--save-samples"
VALID_ADC_RATES = ("SLOW", "MED", "FAST")
# Captures always read at SLOW: the only rate with specified accuracy, and it averages the stepper ripple.
CAPTURE_ADC_RATE = "SLOW"
VALID_FUNCTIONS = sorted(HMC8012.VALID_FUNCTIONS)
VALID_RANGE_FUNCTIONS = sorted(HMC8012.RANGE_SCPI_MAP.keys())

def cmd_measure(address: str, args: list[str]) -> None:
    """Handle: measure.py <address> <function> [delay]"""
    function = args[0]
    delay = 0.0

    if len(args) >= 2:
        try:
            delay = float(args[1])
        except ValueError:
            # Ignore non-numeric trailing arguments (e.g. stray quotes)
            print(
                f"[APP] Ignoring non-numeric argument '{args[1]}', using delay=0.",
                file=sys.stderr,
            )
        if delay < 0:
            _usage_error(f"Delay must be >= 0, got {delay}.")

    try:
        with HMC8012(address) as dmm:
            dmm.set_function(function)

            if delay > 0:
                print(
                    f"[APP] Waiting {delay}s for device positioning...",
                    file=sys.stderr,
                )
                time.sleep(delay)

            result = dmm.measure()

        write_result(str(result))
        print(f"[APP] Result: {result}", file=sys.stderr)

    except pyvisa.errors.VisaIOError as exc:
        _write_error("Measurement", "VISA/network", exc)
        sys.exit(1)
    except ScpiError as exc:
        _write_error("Measurement", "instrument SCPI", exc)
        sys.exit(1)
    except RangeOverflowError as exc:
        _write_error("Measurement", "instrument", exc)
        sys.exit(1)
    except ValueError as exc:
        _write_error("Measurement", "input sanitization", exc)
        sys.exit(1)
    except Exception as exc:
        _write_error("Measurement", "unexpected", exc)
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
        print(f"[APP] Range set: {function} = {range_value}", file=sys.stderr)

    except pyvisa.errors.VisaIOError as exc:
        _write_error("Range", "VISA/network", exc)
        sys.exit(1)
    except ScpiError as exc:
        _write_error("Range", "instrument SCPI", exc)
        sys.exit(1)
    except ValueError as exc:
        _write_error("Range", "input sanitization", exc)
        sys.exit(1)
    except Exception as exc:
        _write_error("Range", "unexpected", exc)
        sys.exit(1)


def cmd_reset(address: str) -> None:
    """Handle: measure.py <address> reset"""
    try:
        with HMC8012(address) as dmm:
            dmm.reset()

        write_result("OK")
        print("[APP] Instrument reset.", file=sys.stderr)

    except pyvisa.errors.VisaIOError as exc:
        _write_error("Reset", "VISA/network", exc)
        sys.exit(1)
    except ScpiError as exc:
        _write_error("Reset", "instrument SCPI", exc)
        sys.exit(1)
    except Exception as exc:
        _write_error("Reset", "unexpected", exc)
        sys.exit(1)


def cmd_adc(address: str, args: list[str]) -> None:
    """Handle: measure.py <address> adc [SLOW|MED|FAST]

    Without a value, writes the ADC rate of the active function to result.txt.
    With a value, sets it and writes OK. The rate applies to the function
    selected last (set it after ``range``) and is kept until changed or reset.
    """
    rate = args[0].upper() if args else None
    if len(args) > 1 or (rate is not None and rate not in VALID_ADC_RATES):
        _usage_error(f"adc takes no value or one of: {', '.join(VALID_ADC_RATES)}.")
    try:
        with HMC8012(address) as dmm:
            if rate is not None:
                dmm.set_adc_rate(rate)
            outcome = "OK" if rate is not None else dmm.get_adc_rate()
        write_result(outcome)
        print(f"[APP] ADC rate: {rate or outcome}", file=sys.stderr)
    except pyvisa.errors.VisaIOError as exc:
        _write_error("ADC rate", "VISA/network", exc)
        sys.exit(1)
    except ScpiError as exc:
        _write_error("ADC rate", "instrument SCPI", exc)
        sys.exit(1)
    except Exception as exc:
        _write_error("ADC rate", "unexpected", exc)
        sys.exit(1)


def cmd_capture(address: str, args: list[str]) -> None:
    """Handle: measure.py <address> capture [duration] [timeout] [--save-samples]

    Runs continuous DCI capture at the SLOW ADC rate, analyzes the waveform for
    the mean running current and writes it to result.txt in the same format as
    a single-shot measure. Caller must set the range beforehand (e.g.
    measure.py <address> range dci 2). With --save-samples the raw readings
    are also written to capture_samples_<UTC date>.csv next to the script.
    """
    duration, timeout, save_samples = _parse_capture_args(args)

    try:
        result, analysis = _run_capture_session(
            address, duration, timeout, samples_dir=SCRIPT_DIR if save_samples else None
        )
        write_result(str(analysis.stable_value))
        print(
            f"[APP] Capture: {result.sample_count} samples, stable value: {analysis.stable_value}",
            file=sys.stderr,
        )
    except pyvisa.errors.VisaIOError as exc:
        _write_error("Capture", "VISA/network", exc)
        sys.exit(1)
    except (ScpiError, RangeOverflowError, CaptureAbortedError) as exc:
        _write_error("Capture", "instrument", exc)
        sys.exit(1)
    except CaptureConfigError as exc:
        _write_error("Capture", "instrument config", exc)
        sys.exit(1)
    except InsufficientSamplesError as exc:
        _write_error("Capture", "insufficient samples", exc)
        sys.exit(1)
    except AnalysisError as exc:
        _write_error("Capture", "analysis", exc)
        sys.exit(1)
    except ValueError as exc:
        _write_error("Capture", "input sanitization", exc)
        sys.exit(1)
    except Exception as exc:
        _write_error("Capture", "unexpected", exc)
        sys.exit(1)


def _write_error(command: str, layer: str, exc: Exception) -> None:
    """Write a layered error to both stderr and result.txt.

    Args:
        command: Human-readable command label (e.g. "Measurement", "Range", "Reset").
        layer:   Origin layer label (e.g. "VISA/network", "instrument SCPI", "input sanitization").
        exc:     The caught exception.
    """
    app_msg = f"[APP] {command} failed ({layer})."
    exc_detail = f"[EXC] {type(exc).__name__}: {exc}"
    print(app_msg, file=sys.stderr)
    print(exc_detail, file=sys.stderr)
    write_result("ERR", app_msg, exc_detail)


def _parse_capture_args(args: list[str]) -> tuple[float, float, bool]:
    """Parse [duration] [timeout] [--save-samples] for the capture command.

    If only duration is given, timeout = duration + CAPTURE_TIMEOUT_MARGIN.

    Returns:
        ``(duration, timeout, save_samples)``.
    """
    save_samples = SAVE_SAMPLES_FLAG in args
    numbers = [arg for arg in args if arg != SAVE_SAMPLES_FLAG]
    if len(numbers) > 2:
        _usage_error("capture takes at most [duration] [timeout] and --save-samples.")
    duration = _positive_number(numbers[0], "Capture duration") if numbers else 10.0
    if len(numbers) < 2:
        return duration, duration + CAPTURE_TIMEOUT_MARGIN, save_samples
    timeout = _positive_number(numbers[1], "Timeout")
    if timeout < duration:
        _usage_error("Timeout must be >= capture duration.")
    return duration, timeout, save_samples


def _positive_number(text: str, name: str) -> float:
    try:
        value = float(text)
    except ValueError:
        _usage_error(f"{name} must be a number, got '{text}'.")
    if value <= 0:
        _usage_error(f"{name} must be positive.")
    return value


def _run_capture_session(
    address: str,
    duration: float,
    timeout: float,
    samples_dir: Path | None = None,
) -> tuple[CaptureResult, AnalysisResult]:
    """Execute one capture session at the SLOW ADC rate and return capture + analysis results.

    The instrument's previous ADC rate is restored afterwards, so a capture
    never changes the settings of later measurements. When *samples_dir* is
    given, the raw samples are written there before the analysis runs, so a
    failed capture can still be diagnosed.
    """
    with HMC8012(address) as dmm:
        dmm.set_function("dci")
        with _temporary_adc_rate(dmm, CAPTURE_ADC_RATE):
            capture = ContinuousCapture(dmm, max_duration=duration)
            try:
                result = capture.run(deadline=time.monotonic() + timeout)
            except InsufficientSamplesError as exc:
                _save_capture_samples(exc.result, samples_dir)
                raise
    _save_capture_samples(result, samples_dir)
    if result.aborted_reason is not None:
        raise CaptureAbortedError(result.aborted_reason)
    analysis = analyze_waveform(result.timestamps, result.values)
    return result, analysis


@contextmanager
def _temporary_adc_rate(dmm: HMC8012, rate: str):
    """Set *rate* for the block and restore the previous rate afterwards, also on error."""
    previous = dmm.get_adc_rate()
    if previous != rate:
        dmm.set_adc_rate(rate)
    try:
        yield
    finally:
        if previous != rate:
            _restore_adc_rate(dmm, previous)


def _restore_adc_rate(dmm: HMC8012, rate: str) -> None:
    """Restore an ADC rate; a failure is reported but never replaces the capture outcome."""
    try:
        dmm.set_adc_rate(rate)
    except Exception as exc:
        print(f"[APP] Could not restore ADC rate {rate}: {type(exc).__name__}: {exc}", file=sys.stderr)


def _save_capture_samples(result: CaptureResult, samples_dir: Path | None) -> None:
    if samples_dir is None or len(result.timestamps) == 0:
        return
    samples_path = write_capture_samples(result, samples_dir)
    print(f"[APP] Raw samples written to: {samples_path}", file=sys.stderr)


def write_capture_samples(
    result: CaptureResult,
    output_dir: Path = SCRIPT_DIR,
) -> Path:
    """Write all raw captured samples (no filtering) to a CSV with measurement date in the filename.

    Filename: capture_samples_YYYY-MM-DD_HH-MM-SS.csv. First line is a comment with
    the measurement date/time (UTC). Then header 'time_s,value_A' and one line per sample.
    """
    now = datetime.now(timezone.utc)
    name = f"{CAPTURE_SAMPLES_FILE_PREFIX}_{now.strftime('%Y-%m-%d_%H-%M-%S')}.csv"
    path = output_dir / name
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Measurement date (UTC): {now.isoformat()}\n")
        f.write("time_s,value_A\n")
        for t, v in zip(result.timestamps, result.values):
            f.write(f"{t:.6f},{v}\n")
    return path


def clear_result(output_path: Path = DEFAULT_OUTPUT) -> None:
    """Remove result file so VBA does not read stale data. Idempotent."""
    output_path.unlink(missing_ok=True)


def write_result(
    value: str,
    app_msg: str = "",
    exc_detail: str = "",
    output_path: Path = DEFAULT_OUTPUT,
) -> None:
    """Write result atomically (temp file + replace). Overwrites existing file.

    Line 1: value (e.g. a number, "OK", or "ERR").
    Line 2: [APP] message, if provided.
    Line 3: [EXC] exception detail, if provided.
    """
    lines = [value]
    if app_msg:
        lines.append(app_msg)
    if exc_detail:
        lines.append(exc_detail)
    content = "\n".join(lines) + "\n"
    fd, temp_path = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=output_path.name + ".",
        suffix=".tmp",
    )
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        fd = -1
        os.replace(temp_path, output_path)
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _usage_error(message: str) -> None:
    """Write ERR to result.txt, print error and usage, then exit with code 1."""
    write_result("ERR", f"[APP] Usage error: {message}")
    print(f"[APP] Error: {message}", file=sys.stderr)
    print(
        "Usage:\n"
        "  python measure.py <address> <function> [delay]             Measure\n"
        "  python measure.py <address> range <function> <value>       Set range\n"
        "  python measure.py <address> adc [SLOW|MED|FAST]            Read or set the ADC rate\n"
        "  python measure.py <address> reset                          Reset\n"
        "  python measure.py <address> capture [duration] [timeout] [--save-samples]\n"
        "                                                             Mean running DC current\n"
        f"  python measure.py --version                                Version ({__version__})",
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
    if args == ["--version"]:
        print(__version__)
        return
    clear_result()

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

    elif command == "adc":
        cmd_adc(address, args[2:])

    elif command == "capture":
        cmd_capture(address, args[2:])

    elif command in HMC8012.VALID_FUNCTIONS:
        cmd_measure(address, [command] + args[2:])

    else:
        _usage_error(
            f"Unknown command '{command}'. "
            f"Expected a function ({', '.join(VALID_FUNCTIONS)}), "
            "'range', 'adc', 'reset', or 'capture'."
        )


if __name__ == "__main__":
    main()
