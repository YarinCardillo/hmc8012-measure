"""CLI entry point for HMC8012 multimeter operations.

Commands:
    python measure.py <address> <function> [delay]             Measure (READ? only)
    python measure.py <address> range <function> <value>       Configure function + range
    python measure.py <address> reset                          Reset instrument
    python measure.py <address> capture [duration] [timeout]   Continuous DCI capture + analysis
    python measure.py <address> capture-plot start [FAST|SLOW|MED]  Capture until stop (or 1h)
    python measure.py <address> capture-plot stop                  Stop running capture
    python measure.py <address> capture-plot [duration] [timeout]   Timed capture + plot

Arguments:
    address    IP address (e.g. 192.168.0.2) or COM port (e.g. COM3)
    function   Measurement type: dcv|acv|dci|aci|res|fres|cap|temp|freq|cont|diod
    delay      Optional wait in seconds before measuring (default: 0).
    duration   Capture window in seconds (default: 10). Used by capture / capture-plot.
    timeout    Optional. If omitted, timeout = duration + 10. Used by capture / capture-plot.

Output:
    Measure/capture write the value (or "ERR") to result.txt in the script directory.
    Range and reset write "OK" (or "ERR") to result.txt.
    result.txt is removed at script start and written atomically (temp file + replace).
    On error, a second line is written with the command context and exception message.
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pyvisa

from analyzer import AnalysisError, AnalysisResult, analyze_waveform
from capture import (
    CaptureConfigError,
    CaptureResult,
    ContinuousCapture,
    InsufficientSamplesError,
)
from hmc8012 import HMC8012, RangeOverflowError, ScpiError

# sys.argv[0] always points to the actual script/executable being run.
# This is more reliable than __file__ when compiled with Nuitka (onefile mode),
# where __file__ resolves to a temp extraction directory instead of the exe location.
SCRIPT_DIR = Path(sys.argv[0]).resolve().parent
DEFAULT_OUTPUT = SCRIPT_DIR / "result.txt"
# Extra seconds added to duration when timeout is not given (analysis + margin)
CAPTURE_TIMEOUT_MARGIN = 10.0
# Sentinel file for capture-plot start/stop (same path for start and stop commands)
CAPTURE_SENTINEL_PATH = SCRIPT_DIR / "capture.stop"
# Max duration when using "capture-plot start" (stop via sentinel or this limit)
MAX_CAPTURE_DURATION_START = 3600.0
# Live plot Y axis fixed span in amperes (at least this range visible from the start)
CAPTURE_PLOT_Y_SPAN_AMPS = 1.0
# Prefix for raw capture samples file; name will be {prefix}_YYYY-MM-DD_HH-MM-SS.csv
CAPTURE_SAMPLES_FILE_PREFIX = "capture_samples"
VALID_ADC_RATES = ("FAST", "SLOW", "MED")
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


def cmd_capture(address: str, args: list[str]) -> None:
    """Handle: measure.py <address> capture [duration] [timeout]

    Runs continuous DCI capture, analyzes waveform for stable value, writes it to
    result.txt in the same single-line format as single-shot measure. Caller must
    set range beforehand (e.g. measure.py <address> range dci 0.2).
    """
    duration, timeout = _parse_capture_args(args)

    try:
        result, analysis = _run_capture_session(address, duration, timeout)
        write_result(str(analysis.stable_value))
        samples_path = write_capture_samples(result)
        print(
            f"[APP] Capture: {result.sample_count} samples, stable value: {analysis.stable_value}",
            file=sys.stderr,
        )
        print(f"[APP] Raw samples written to: {samples_path}", file=sys.stderr)
    except pyvisa.errors.VisaIOError as exc:
        _write_error("Capture", "VISA/network", exc)
        sys.exit(1)
    except (ScpiError, RangeOverflowError) as exc:
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


def cmd_capture_plot(address: str, args: list[str]) -> None:
    """Capture with live-updating plot; after capture, annotate stable value and region.

    Modes:
      capture-plot start [FAST|SLOW|MED]  Run until capture-plot stop (or 1h limit).
      capture-plot stop                    Create sentinel file to stop a running start.
      capture-plot [duration] [timeout]   Run for duration seconds (timeout optional).
    """
    # --- stop: only create sentinel file, no instrument connection ---
    if args and args[0].lower() == "stop":
        CAPTURE_SENTINEL_PATH.touch()
        print("[APP] Stop signal sent. Capture process will exit.", file=sys.stderr)
        return

    # --- start: long-running capture until stop or max duration ---
    if args and args[0].lower() == "start":
        # Strip --ui/--UI so it is not interpreted as ADC rate
        args_start = [a for a in args if a.upper() != "--UI"]
        use_ui = any(a.upper() == "--UI" for a in args)
        adc_rate = (
            args_start[1].upper()
            if len(args_start) > 1 and args_start[1].upper() in VALID_ADC_RATES
            else "FAST"
        )
        if len(args_start) > 1 and args_start[1].upper() not in VALID_ADC_RATES:
            _usage_error(
                f"ADC rate must be one of {', '.join(VALID_ADC_RATES)}, got '{args_start[1]}'."
            )
        if use_ui:
            _run_capture_plot_ui(address, adc_rate)
            return
        duration = MAX_CAPTURE_DURATION_START
        timeout = duration + CAPTURE_TIMEOUT_MARGIN
        sentinel_path = CAPTURE_SENTINEL_PATH
        _run_capture_plot_live(
            address, duration, timeout,
            sentinel_path=sentinel_path,
            adc_rate=adc_rate,
        )
        return

    # --- duration [timeout]: classic timed capture ---
    duration, timeout = _parse_capture_args(args)
    _run_capture_plot_live(address, duration, timeout, sentinel_path=None, adc_rate="FAST")


def _run_capture_plot_ui(address: str, adc_rate: str) -> None:
    """Show a small GUI with Start/Stop; Start runs capture+plot in a subprocess, Stop touches sentinel."""
    script_path = SCRIPT_DIR / "measure.py"
    cmd = [sys.executable, str(script_path), address, "capture-plot", "start"]
    if adc_rate != "FAST":
        cmd.append(adc_rate)

    proc: subprocess.Popen | None = None

    def on_start() -> None:
        nonlocal proc
        if proc is not None and proc.poll() is None:
            return
        # Let child inherit stderr so capture/analysis errors are visible in the terminal
        proc = subprocess.Popen(
            cmd,
            cwd=SCRIPT_DIR,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=None,
        )

    def on_stop() -> None:
        CAPTURE_SENTINEL_PATH.touch()
        print("[APP] Stop signal sent.", file=sys.stderr)

    root = tk.Tk()
    root.title("HMC8012 Capture")
    root.resizable(False, False)

    def on_close() -> None:
        CAPTURE_SENTINEL_PATH.touch()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    frame = tk.Frame(root, padx=20, pady=20)
    frame.pack()
    tk.Button(frame, text="Start", command=on_start, width=10).pack(side=tk.LEFT, padx=5)
    tk.Button(frame, text="Stop", command=on_stop, width=10).pack(side=tk.LEFT, padx=5)
    root.mainloop()


def _run_capture_plot_live(
    address: str,
    duration: float,
    timeout: float,
    *,
    sentinel_path: Path | None = None,
    adc_rate: str = "FAST",
) -> None:
    """Run live capture+plot with given duration, timeout, optional sentinel path and ADC rate."""
    # Shared state: live samples for plot, final result when capture thread finishes
    max_samples = 5000
    live_deque: deque[tuple[float, float]] = deque(maxlen=max_samples)
    result_holder: list[tuple[CaptureResult, AnalysisResult] | None] = []

    def worker() -> None:
        try:
            result, analysis = _run_capture_session(
                address,
                duration,
                timeout,
                sample_callback=lambda t, v: live_deque.append((t, v)),
                sentinel_path=sentinel_path,
                adc_rate=adc_rate,
            )
            write_result(str(analysis.stable_value))
            samples_path = write_capture_samples(result)
            print(
                f"[APP] Capture+Plot: {result.sample_count} samples, stable value: {analysis.stable_value}",
                file=sys.stderr,
            )
            print(f"[APP] Raw samples written to: {samples_path}", file=sys.stderr)
            result_holder.append((result, analysis))
        except pyvisa.errors.VisaIOError as exc:
            _write_error("Capture", "VISA/network", exc)
            result_holder.append(None)
        except (ScpiError, RangeOverflowError) as exc:
            _write_error("Capture", "instrument", exc)
            result_holder.append(None)
        except CaptureConfigError as exc:
            _write_error("Capture", "instrument config", exc)
            result_holder.append(None)
        except InsufficientSamplesError as exc:
            _write_error("Capture", "insufficient samples", exc)
            result_holder.append(None)
        except AnalysisError as exc:
            _write_error("Capture", "analysis", exc)
            result_holder.append(None)
        except ValueError as exc:
            _write_error("Capture", "input sanitization", exc)
            result_holder.append(None)
        except Exception as exc:
            _write_error("Capture", "unexpected", exc)
            result_holder.append(None)

    thread = threading.Thread(target=worker)
    thread.start()

    try:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        from matplotlib.animation import FuncAnimation
    except Exception as exc:
        print(
            "[APP] Plot requested but matplotlib is unavailable:",
            exc,
            file=sys.stderr,
        )
        print("[APP] Install with: pip install matplotlib", file=sys.stderr)
        thread.join(timeout=timeout + 5)
        sys.exit(1)

    fig, ax = plt.subplots(figsize=(8, 4))
    line, = ax.plot([], [], label="DCI", color="tab:blue")
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Current [A]")
    ax.set_title("HMC8012 Continuous Capture (live)")
    ax.set_ylim(0.0, CAPTURE_PLOT_Y_SPAN_AMPS)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")

    ani_ref: list = []

    def animate(frame: int) -> None:
        if result_holder:
            payload = result_holder[0]
            if payload is None:
                if ani_ref:
                    ani_ref[0].event_source.stop()
                return
            result, analysis = payload
            line.set_data(result.timestamps, result.values)
            ax.relim()
            ax.autoscale_view(scalex=True, scaley=False)
            if analysis and result.sample_count >= analysis.samples_used > 0:
                # Green zone: from settling start to stable region end (indices in raw result; same as filtered when no overflows)
                stable_start_idx = min(analysis.settling_sample_index, result.sample_count - 1)
                stable_end_idx = min(
                    analysis.settling_sample_index + analysis.samples_used,
                    result.sample_count,
                )
                stable_start_t = float(result.timestamps[stable_start_idx])
                stable_end_t = float(result.timestamps[stable_end_idx - 1]) if stable_end_idx > 0 else stable_start_t
                ax.axhline(
                    analysis.stable_value,
                    color="tab:green",
                    linestyle="--",
                    label=f"Stable = {analysis.stable_value:.4f} A",
                )
                ax.axvspan(
                    stable_start_t,
                    stable_end_t,
                    color="tab:green",
                    alpha=0.15,
                )
                ax.legend(loc="best")
                # Post-capture analysis summary on same figure (no second window)
                summary = (
                    f"Stable = {analysis.stable_value:.4f} A\n"
                    f"σ = {analysis.stable_std_dev:.5f} A\n"
                    f"N = {analysis.samples_used}  |  "
                    f"Δt = {result.actual_duration:.2f} s  |  "
                    f"rate = {result.sample_rate:.0f} Sa/s"
                )
                ax.text(
                    0.02,
                    0.98,
                    summary,
                    transform=ax.transAxes,
                    fontsize=8,
                    verticalalignment="top",
                    bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8),
                )
            ax.set_title("HMC8012 Continuous Capture (live)")
            if ani_ref:
                ani_ref[0].event_source.stop()
            return
        data = list(live_deque)
        if not data:
            return
        ts = [d[0] for d in data]
        vs = [d[1] for d in data]
        line.set_data(ts, vs)
        ax.relim()
        ax.autoscale_view(scalex=True, scaley=False)

    ani = FuncAnimation(
        fig, animate, interval=33, cache_frame_data=False
    )  # ~30 FPS (1000/33 ms)
    ani_ref.append(ani)
    fig.tight_layout()
    plt.show()
    # After window close: allow time for worker to finish analysis and save (e.g. after Stop)
    thread.join(timeout=30.0)
    if result_holder and result_holder[0] is None:
        sys.exit(1)


# -- CLI dispatch ------------------------------------------------------------

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


def _parse_capture_args(args: list[str]) -> tuple[float, float]:
    """Parse [duration] [timeout] for capture commands.

    If only duration is given, timeout = duration + CAPTURE_TIMEOUT_MARGIN.
    """
    duration = 10.0
    timeout = 30.0
    if len(args) >= 1:
        try:
            duration = float(args[0])
        except ValueError:
            _usage_error(f"Capture duration must be a number, got '{args[0]}'.")
        if duration <= 0:
            _usage_error("Capture duration must be positive.")
    if len(args) >= 2:
        try:
            timeout = float(args[1])
        except ValueError:
            _usage_error(f"Timeout must be a number, got '{args[1]}'.")
        if timeout <= 0:
            _usage_error("Timeout must be positive.")
        if timeout < duration:
            _usage_error("Timeout must be >= capture duration.")
    else:
        timeout = duration + CAPTURE_TIMEOUT_MARGIN
    return duration, timeout


def _run_capture_session(
    address: str,
    duration: float,
    timeout: float,
    sample_callback: Callable[[float, float], None] | None = None,
    sentinel_path: Path | None = None,
    adc_rate: str = "FAST",
) -> tuple[CaptureResult, AnalysisResult]:
    """Execute one capture session and return capture + analysis results."""
    with HMC8012(address) as dmm:
        dmm.set_function("dci")
        dmm.set_adc_rate(adc_rate)
        capture = ContinuousCapture(
            dmm,
            max_duration=duration,
            sentinel_path=sentinel_path,
        )
        deadline = time.monotonic() + timeout
        result = capture.run(deadline=deadline, sample_callback=sample_callback)
    analysis = analyze_waveform(result.timestamps, result.values)
    return result, analysis


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
    """Print error and usage, then exit with code 1."""
    print(f"[APP] Error: {message}", file=sys.stderr)
    print(
        "Usage:\n"
        "  python measure.py <address> <function> [delay]             Measure\n"
        "  python measure.py <address> range <function> <value>       Set range\n"
        "  python measure.py <address> reset                          Reset\n"
        "  python measure.py <address> capture [duration] [timeout]   Continuous DCI capture\n"
        "  python measure.py <address> capture-plot start [FAST|SLOW|MED]  Capture until stop\n"
        "  python measure.py <address> capture-plot stop                  Stop running capture\n"
        "  python measure.py <address> capture-plot [duration] [timeout]  Timed capture (timeout optional)",
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

    clear_result()

    if command == "reset":
        if len(args) != 2:
            _usage_error("reset takes no additional arguments.")
        cmd_reset(address)

    elif command == "range":
        cmd_range(address, args[2:])

    elif command == "capture":
        cmd_capture(address, args[2:])

    elif command == "capture-plot":
        cmd_capture_plot(address, args[2:])

    elif command in HMC8012.VALID_FUNCTIONS:
        cmd_measure(address, [command] + args[2:])

    else:
        _usage_error(
            f"Unknown command '{command}'. "
            f"Expected a function ({', '.join(VALID_FUNCTIONS)}), "
            "'range', 'reset', or 'capture'."
        )


if __name__ == "__main__":
    main()
