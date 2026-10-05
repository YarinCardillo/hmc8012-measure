"""CLI entry point for HMC8012 multimeter operations.

Commands:
    python measure.py <address> <function> [--delay S]         One reading (READ?)
    python measure.py <address> <function> --time S [--timeout S] [capture flags]
                                                               Capture for S seconds, mean movement value
    python measure.py <address> <function> --auto [--timeout S] [capture flags]
                                                               Capture until the device is back at idle
    python measure.py <address> range <function> <value>       Configure function + range
    python measure.py <address> adc [SLOW|MED|FAST]            Read or set the ADC rate
    python measure.py <address> reset                          Reset instrument
    python measure.py --version                                Print the version

Arguments:
    address    IP address (e.g. 192.168.0.2) or COM port (e.g. COM3)
    function   Measurement type: dcv|acv|dci|aci|res|fres|cap|temp|freq|cont|diod
               (captures: dcv|acv|dci|aci)
    --delay S       Wait S seconds before the single reading (default: 0).
    --time S        Capture for S seconds.
    --auto          Capture until the device has run and is back at idle (at most 30 s).
    --timeout S     Capture deadline; default: capture length + 10.
    --rate R        ADC rate of the capture: SLOW (default), MED or FAST.
    --save-samples  Also write the raw readings to capture_samples_<UTC date>.csv.
    --save-plot     Also write the plot to capture_plot_<UTC date>.html.
    --live          Draw the capture in a small window while it runs.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, NamedTuple

import pyvisa

from analyzer import AnalysisError, AnalysisResult, analyze_waveform
from capture import (
    CAPTURE_FUNCTION_REPLIES,
    CaptureAbortedError,
    CaptureConfigError,
    CaptureResult,
    ContinuousCapture,
    InsufficientSamplesError,
)
from capture_plot import write_capture_plot
from hmc8012 import HMC8012, RangeOverflowError, ScpiError
from live_plot import LivePlot
from live_window import LIVE_WINDOW_FLAG, open_live_window, run_live_window
from stop_detector import StopDetector
from version import __version__

# sys.argv[0], not __file__: Nuitka onefile resolves __file__ to a temp extraction directory.
SCRIPT_DIR = Path(sys.argv[0]).resolve().parent
DEFAULT_OUTPUT = SCRIPT_DIR / "result.txt"
# Extra seconds added to duration when timeout is not given (analysis + margin)
CAPTURE_TIMEOUT_MARGIN = 10.0
# Longest capture without a duration, which stops by itself when the device is back at idle.
AUTO_STOP_MAX_DURATION_S = 30.0
# Prefix for raw capture samples file; name will be {prefix}_YYYY-MM-DD_HH-MM-SS.csv
CAPTURE_SAMPLES_FILE_PREFIX = "capture_samples"
DELAY_FLAG = "--delay"
TIME_FLAG = "--time"
TIMEOUT_FLAG = "--timeout"
AUTO_FLAG = "--auto"
RATE_FLAG = "--rate"
SAVE_SAMPLES_FLAG = "--save-samples"
SAVE_PLOT_FLAG = "--save-plot"
LIVE_FLAG = "--live"
VALUE_FLAGS = (DELAY_FLAG, TIME_FLAG, TIMEOUT_FLAG, RATE_FLAG)
SWITCH_FLAGS = (AUTO_FLAG, SAVE_SAMPLES_FLAG, SAVE_PLOT_FLAG, LIVE_FLAG)
CAPTURE_ONLY_FLAGS = (TIMEOUT_FLAG, RATE_FLAG, SAVE_SAMPLES_FLAG, SAVE_PLOT_FLAG, LIVE_FLAG)
VALID_ADC_RATES = ("SLOW", "MED", "FAST")
# Default ADC rate of captures: the only rate with specified accuracy. --rate MED resolves movements
# of only a few SLOW conversions; FAST gives no more conversions through READ? on the lab device.
CAPTURE_ADC_RATE = "SLOW"
VALID_FUNCTIONS = sorted(HMC8012.VALID_FUNCTIONS)
VALID_RANGE_FUNCTIONS = sorted(HMC8012.RANGE_SCPI_MAP.keys())


class CaptureOptions(NamedTuple):
    """Arguments of a capture (--time or --auto)."""

    duration: float
    timeout: float
    save_samples: bool
    save_plot: bool
    show_live: bool
    stop_on_idle: bool
    adc_rate: str


class MeasureOptions(NamedTuple):
    """Arguments after the function: a single reading after *delay*, or a capture."""

    delay: float
    capture: CaptureOptions | None


def cmd_measure(address: str, args: list[str]) -> None:
    """Handle: measure.py <address> <function> [--delay S | --time S | --auto] [capture flags]

    Without --time or --auto, one reading after the optional delay; with
    them, a capture of the function (see cmd_capture).
    """
    function = args[0]
    options = _parse_measure_args(function, args[1:])
    if options.capture is not None:
        cmd_capture(address, function, options.capture)
        return
    _measure_once(address, function, options.delay)


def _measure_once(address: str, function: str, delay: float) -> None:
    """One READ? of *function* after *delay* seconds; writes the value or ERR."""
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


def cmd_capture(address: str, function: str, options: CaptureOptions) -> None:
    """Handle: measure.py <address> <function> (--time S | --auto) [--timeout S] [capture flags]

    Captures *function* (dci, dcv, aci or acv) at the SLOW ADC rate (or
    --rate), analyzes the waveform for the mean movement value and writes it to result.txt in the
    same format as a single reading. With --auto the capture stops by itself
    once the device has run and is back at idle. Caller must set the range
    beforehand (e.g. measure.py <address> range dci 2). Next to the script,
    --save-samples writes the raw readings to capture_samples_<UTC date>.csv
    and --save-plot the plot to capture_plot_<UTC date>.html; --live draws the
    capture in a small window while it runs. None of them changes what is
    written to result.txt.
    """
    with _live_plot(options.show_live) as live:
        try:
            result, analysis = _run_capture_session(
                address,
                options.duration,
                options.timeout,
                samples_dir=SCRIPT_DIR if options.save_samples else None,
                plot_dir=SCRIPT_DIR if options.save_plot else None,
                live=live,
                stop_on_idle=options.stop_on_idle,
                function=function,
                adc_rate=options.adc_rate,
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


def _parse_measure_args(function: str, args: list[str]) -> MeasureOptions:
    """Parse the flags after the function, in any order.

    --time or --auto make it a capture; otherwise it is a single reading after
    the optional --delay. Invalid combinations are usage errors.
    """
    flags = _read_flags(args)
    if TIME_FLAG not in flags and AUTO_FLAG not in flags:
        capture_flags = [flag for flag in CAPTURE_ONLY_FLAGS if flag in flags]
        if capture_flags:
            _usage_error(f"{', '.join(capture_flags)}: only for a capture ({TIME_FLAG} or {AUTO_FLAG}).")
        return MeasureOptions(_non_negative_number(flags.get(DELAY_FLAG) or "0", DELAY_FLAG), None)
    if DELAY_FLAG in flags:
        _usage_error(f"{DELAY_FLAG} applies to a single reading, not to a capture.")
    return MeasureOptions(0.0, _parse_capture_options(function, flags))


def _parse_capture_options(function: str, flags: dict[str, str | None]) -> CaptureOptions:
    """--time S or --auto (at most AUTO_STOP_MAX_DURATION_S); timeout defaults to length + CAPTURE_TIMEOUT_MARGIN.

    --rate defaults to CAPTURE_ADC_RATE.
    """
    if TIME_FLAG in flags and AUTO_FLAG in flags:
        _usage_error(f"{TIME_FLAG} and {AUTO_FLAG} exclude each other.")
    if function not in CAPTURE_FUNCTION_REPLIES:
        _usage_error(f"Capture is available for {', '.join(CAPTURE_FUNCTION_REPLIES)} only, got '{function}'.")
    is_auto = AUTO_FLAG in flags
    duration = AUTO_STOP_MAX_DURATION_S if is_auto else _positive_number(flags[TIME_FLAG], TIME_FLAG)
    timeout = duration + CAPTURE_TIMEOUT_MARGIN
    if TIMEOUT_FLAG in flags:
        timeout = _positive_number(flags[TIMEOUT_FLAG], TIMEOUT_FLAG)
        if timeout < duration:
            _usage_error("Timeout must be >= capture duration.")
    adc_rate = (flags.get(RATE_FLAG) or CAPTURE_ADC_RATE).upper()
    if adc_rate not in VALID_ADC_RATES:
        _usage_error(f"{RATE_FLAG} must be one of {', '.join(VALID_ADC_RATES)}, got '{flags[RATE_FLAG]}'.")
    return CaptureOptions(
        duration, timeout, SAVE_SAMPLES_FLAG in flags, SAVE_PLOT_FLAG in flags, LIVE_FLAG in flags, is_auto, adc_rate
    )


def _read_flags(args: list[str]) -> dict[str, str | None]:
    """Map each flag to its value (None for a switch).

    Blank or quote-only arguments, which a host program can pass by mistake, are ignored.
    """
    flags: dict[str, str | None] = {}
    tokens = iter(arg for arg in args if arg.strip().strip('"'))
    for token in tokens:
        if token in SWITCH_FLAGS:
            flags[token] = None
        elif token in VALUE_FLAGS:
            value = next(tokens, None)
            if value is None:
                _usage_error(f"{token} needs a value.")
            flags[token] = value
        else:
            _usage_error(f"Unknown argument '{token}'. Expected {', '.join(VALUE_FLAGS + SWITCH_FLAGS)}.")
    return flags


def _positive_number(text: str, name: str) -> float:
    value = _number(text, name)
    if value <= 0:
        _usage_error(f"{name} must be positive.")
    return value


def _non_negative_number(text: str, name: str) -> float:
    value = _number(text, name)
    if value < 0:
        _usage_error(f"{name} must be >= 0, got {value}.")
    return value


def _number(text: str, name: str) -> float:
    try:
        return float(text)
    except ValueError:
        _usage_error(f"{name} must be a number, got '{text}'.")


@dataclass(frozen=True)
class _CaptureOutputs:
    """Diagnostic outputs of one capture; none of them changes its outcome."""

    started_at: datetime
    samples_dir: Path | None
    plot_dir: Path | None
    live: LivePlot | None


def _run_capture_session(
    address: str,
    duration: float,
    timeout: float,
    samples_dir: Path | None = None,
    plot_dir: Path | None = None,
    live: LivePlot | None = None,
    stop_on_idle: bool = False,
    function: str = "dci",
    adc_rate: str = CAPTURE_ADC_RATE,
) -> tuple[CaptureResult, AnalysisResult]:
    """Execute one capture session at *adc_rate* and return capture + analysis results.

    With *stop_on_idle* the capture ends once the device has run and is back
    at idle; *duration* is then only its upper bound.

    The instrument's previous ADC rate is restored afterwards, so a capture
    never changes the settings of later measurements. The diagnostic outputs
    (samples CSV in *samples_dir*, plot page in *plot_dir*, the *live* plot)
    get the readings and the outcome also when the capture fails.
    """
    outputs = _CaptureOutputs(datetime.now(timezone.utc), samples_dir, plot_dir, live)
    result = None
    try:
        result = _acquire_capture(
            address, function, adc_rate, duration, timeout, live.add_sample if live else None, stop_on_idle
        )
        analysis = _analyze_capture(result)
    except Exception as exc:
        captured = exc.result if isinstance(exc, InsufficientSamplesError) else result
        _save_capture_outputs(outputs, captured, error=exc)
        raise
    _save_capture_outputs(outputs, result, analysis=analysis)
    return result, analysis


def _acquire_capture(
    address: str,
    function: str,
    adc_rate: str,
    duration: float,
    timeout: float,
    on_sample: Callable[[float, float], None] | None,
    stop_on_idle: bool,
) -> CaptureResult:
    """Read *function* at *adc_rate* for *duration* s, or until back at idle, then restore the previous rate."""
    should_stop = StopDetector().should_stop if stop_on_idle else None
    with HMC8012(address) as dmm:
        dmm.set_function(function)
        with _temporary_adc_rate(dmm, adc_rate):
            capture = ContinuousCapture(dmm, function=function, max_duration=duration)
            return capture.run(deadline=time.monotonic() + timeout, on_sample=on_sample, should_stop=should_stop)


def _analyze_capture(result: CaptureResult) -> AnalysisResult:
    if result.aborted_reason is not None:
        raise CaptureAbortedError(result.aborted_reason)
    return analyze_waveform(result.timestamps, result.values)


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


@contextmanager
def _live_plot(is_enabled: bool) -> Iterator[LivePlot | None]:
    """Serve the live plot and open its window for the block; None when not asked for or unavailable."""
    live = _start_live_plot() if is_enabled else None
    try:
        yield live
    finally:
        if live is not None:
            live.close()


def _start_live_plot() -> LivePlot | None:
    live = LivePlot()
    try:
        live.start()
    except OSError as exc:
        print(f"[APP] Live plot unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    print(f"[APP] Live plot at {live.url}", file=sys.stderr)
    try:
        open_live_window(live.url)
    except OSError as exc:
        print(f"[APP] Could not open the live window ({type(exc).__name__}: {exc}); open the address by hand.",
              file=sys.stderr)
    return live


def _save_capture_outputs(
    outputs: _CaptureOutputs,
    result: CaptureResult | None,
    analysis: AnalysisResult | None = None,
    error: Exception | None = None,
) -> None:
    """Hand the outcome to the diagnostic outputs that were asked for."""
    if outputs.live is not None:
        outputs.live.finish(outputs.started_at, analysis=analysis, error=error)
    if result is None or len(result.timestamps) == 0:
        return
    if outputs.samples_dir is not None:
        _write_capture_output(
            "capture samples", lambda: write_capture_samples(result, outputs.samples_dir, outputs.started_at)
        )
    if outputs.plot_dir is not None:
        _write_capture_output("capture plot", lambda: write_capture_plot(
            outputs.plot_dir, result.timestamps, result.values, outputs.started_at, analysis, error
        ))


def _write_capture_output(name: str, write: Callable[[], Path]) -> None:
    """Run one diagnostic writer; a failure is reported on stderr and never replaces the capture outcome."""
    try:
        path = write()
    except Exception as exc:
        print(f"[APP] Could not write the {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    print(f"[APP] {name.capitalize()} written to: {path}", file=sys.stderr)


def write_capture_samples(
    result: CaptureResult,
    output_dir: Path = SCRIPT_DIR,
    captured_at: datetime | None = None,
) -> Path:
    """Write all raw captured samples (no filtering) to a CSV with measurement date in the filename.

    Filename: capture_samples_YYYY-MM-DD_HH-MM-SS.csv, from *captured_at* (UTC, default
    now). First line is a comment with that date/time. Then header 'time_s,value_A' and
    one line per sample.
    """
    now = captured_at or datetime.now(timezone.utc)
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
        "  python measure.py <address> <function> [--delay S]         One reading\n"
        "  python measure.py <address> <function> --time S            Capture for S seconds (dcv, acv, dci, aci)\n"
        "  python measure.py <address> <function> --auto              Capture until back at idle (at most 30 s)\n"
        "      capture flags: [--timeout S] [--rate SLOW|MED|FAST] [--save-samples] [--save-plot] [--live]\n"
        "  python measure.py <address> range <function> <value>       Set range\n"
        "  python measure.py <address> adc [SLOW|MED|FAST]            Read or set the ADC rate\n"
        "  python measure.py <address> reset                          Reset\n"
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
    # Internal: the live plot window process started by a --live capture; it never touches result.txt.
    if len(args) == 2 and args[0] == LIVE_WINDOW_FLAG:
        run_live_window(args[1])
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

    elif command in HMC8012.VALID_FUNCTIONS:
        cmd_measure(address, [command] + args[2:])

    else:
        _usage_error(
            f"Unknown command '{command}'. "
            f"Expected a function ({', '.join(VALID_FUNCTIONS)}), "
            "'range', 'adc' or 'reset'."
        )


if __name__ == "__main__":
    main()
