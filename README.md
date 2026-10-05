# HMC8012 Measurement Layer

Command-line tool for the Rohde & Schwarz HMC8012 digital multimeter, called by a host program (a VBA macro). Each call does one job, writes its outcome to `result.txt` next to the executable, and exits. Besides single readings, a capture (`--time` or `--auto`) records the supply current of the device and reports the mean current of the movement between its deltastep peaks, leaving out the peaks, the idle and the readings that straddle the movement's start and stop. With `--auto` the capture stops by itself once the motor has stopped. On request it also draws the capture, live in a compact window or saved as a page.

Italian version: [README_ita.md](README_ita.md).

## Quick start

| Command | What it does |
|-|-|
| `hmc.exe 192.168.0.2 dci` | One DC current reading |
| `hmc.exe 192.168.0.2 dci --delay 1.5` | Waits 1.5 s, then one DC current reading |
| `hmc.exe 192.168.0.2 range dci 2` | DC current, 2 A range, kept until changed |
| `hmc.exe 192.168.0.2 dci --auto` | Capture that stops by itself 3 s after the motor stops (at most 30 s): start it, then move the motor |
| `hmc.exe 192.168.0.2 dci --time 10` | Capture of exactly 10 s |
| `hmc.exe 192.168.0.2 dci --auto --rate MED` | The same at 10 conversions per second, for movements under 1 s |
| `hmc.exe 192.168.0.2 dci --auto --live` | Capture plotted live in a small window |
| `hmc.exe --version` | Version of this executable |

The outcome is in `result.txt` next to `hmc.exe`: the value in amperes, `OK`, or `ERR`.

### Development

Python 3.11 or newer:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

On Windows, activate the environment with `venv\Scripts\activate`.

## Commands

`<address>` is an IP address (`192.168.0.2`, LAN, port 5025) or a COM port (`COM3`, USB virtual COM port, Windows only). `hmc.exe` and `python measure.py` accept the same arguments.

| Command | What it does | `result.txt` |
|-|-|-|
| `<address> <function> [--delay S]` | One reading with the current settings, after an optional wait of S seconds | value or `ERR` |
| `<address> <function> --time S [flags]` | Capture of S seconds; reports the mean movement value. See [Capture](#capture) | value or `ERR` |
| `<address> <function> --auto [flags]` | Capture that stops by itself once the motor is back at idle (at most 30 s). See [Capture](#capture) | value or `ERR` |
| `<address> range <function> <value>` | Selects the function and its range; kept until the next `range` or `reset` | `OK` or `ERR` |
| `<address> adc` | Reads the ADC rate of the active function | `SLOW`, `MED`, `FAST` or `ERR` |
| `<address> adc <SLOW\|MED\|FAST>` | Sets the ADC rate of the active function (the one selected last) | `OK` or `ERR` |
| `<address> reset` | Restores factory defaults (ADC rate SLOW, auto-range on) | `OK` or `ERR` |
| `--version` | Prints the version on the console | unchanged |

### Capture

- Captures work for `dci`, `dcv`, `aci` and `acv`; the analysis is designed for the DC supply current of the device (`dci`), see [How the capture value is computed](#how-the-capture-value-is-computed).
- With `--auto`, the capture stops 3 s after the value is back at idle, provided the device ran for at least 0.5 s first. Idle is the lowest reading so far, so the capture may start while the device is already moving. Pauses shorter than 3 s do not stop it. If the device never stops, the capture ends at 30 s.
- `--timeout S` sets the capture deadline; it defaults to the capture length plus 10 s (40 s with `--auto`).
- The capture reads at the SLOW ADC rate, or at the rate given with `--rate SLOW|MED|FAST`. If the instrument is at another rate, capture switches to it and restores the previous rate at the end, also when it fails, so later measurements keep their settings. MED (10 conversions per second) suits movements that last only a few SLOW conversions.
- Set the range of the function with `range` first: capture refuses auto-range.

Diagnostic flags of a capture, off by default, in any combination and order:

| Flag | What it adds |
|-|-|
| `--live` | Draws the readings while the capture runs, in a compact window (about 1000x640). At the end it marks the movement and the averaged conversions and shows the value or the error. The window stays open after `hmc.exe` exits, until you close it. |
| `--save-plot` | Writes the same plot to `capture_plot_<UTC date>.html` next to the executable: one file that opens offline with a double click, as a normal browser tab. Drag to zoom. |
| `--save-samples` | Writes the raw readings to `capture_samples_<UTC date>.csv` next to the executable. |

- None of the flags changes `result.txt`. A file that cannot be written or a window that cannot open is reported on the console only.
- With `--live`, `hmc.exe` waits up to 3 s after writing `result.txt`, so the window receives the outcome.
- The live window is a native window (pywebview on the WebView2 runtime, part of Windows 11 and of updated Windows 10) run by a second `hmc.exe` process. It appears once that process has started and then shows every reading taken so far. Without WebView2 the page opens in a browser tab.
- The page is served on `127.0.0.1` only, never on the network.

### Functions and ranges

| Name | Measurement | SCPI command | Range values (SI units) |
|-|-|-|-|
| `dcv` | DC voltage | `CONF:VOLT:DC` | 0.4, 4, 40, 400, 1000 V |
| `acv` | AC voltage | `CONF:VOLT:AC` | 0.4, 4, 40, 400, 750 V |
| `dci` | DC current | `CONF:CURR:DC` | 0.02, 0.2, 2, 10 A |
| `aci` | AC current | `CONF:CURR:AC` | 0.02, 0.2, 2, 10 A |
| `res` | 2-wire resistance | `CONF:RES` | 400, 4e3, 40e3, 400e3, 4e6, 40e6, 2.5e8 Ohm |
| `fres` | 4-wire resistance | `CONF:FRES` | 400, 4e3, 40e3, 400e3, 4e6 Ohm |
| `cap` | Capacitance | `CONF:CAP` | 5e-9, 50e-9, 500e-9, 5e-6, 50e-6, 500e-6 F |
| `temp` | Temperature (PT100) | `CONF:TEMP` | none |
| `freq` | Frequency | `CONF:FREQ` | none |
| `cont` | Continuity | `CONF:CONT` | none |
| `diod` | Diode test | `CONF:DIOD` | none |

`range <function> AUTO` enables auto-range.

## Contract with the host program

**Files.** `result.txt` is written next to `hmc.exe`, so it must sit in a writable folder. A LAN connection needs nothing else; a COM (USB) connection needs the HMC8012 VCP driver.

**`result.txt`:**

- It is deleted when a command starts (except `--version`) and written in one step (temporary file, then rename) when it ends: while a command runs the file does not exist, and a file that exists is always complete.
- Line 1 is the value (decimal point), `OK`, the ADC rate, or `ERR`.
- After `ERR`, line 2 is `[APP] <command> failed (<layer>).` and line 3 is `[EXC] <type>: <message>`.
- The exit code is 0 on success and 1 on any error.

| Layer | Meaning |
|-|-|
| `VISA/network` | Instrument not reached: connection or transport failure |
| `instrument SCPI` / `instrument` | Instrument reached but it reported an error, an overflow, or the capture stopped on failed readings |
| `instrument config` | Capture: wrong function or auto-range still on |
| `insufficient samples` | Capture: too few valid readings |
| `analysis` | Capture: readings taken, but no trustworthy value (see the error table below) |
| `input sanitization` | Invalid argument |
| `unexpected` | Anything else; `[EXC]` has the details |

**Timing.** Every command first pays the start-up of `hmc.exe` (the single-file executable unpacks itself; typically a few seconds, to be measured on the lab PC). A capture then takes the `--time` seconds (with `--auto`, until 3 s after the motor stops) plus the analysis (well under 0.1 s).

For an instantaneous reading, wait for the process to end, then read the file:

```vba
Dim sh As Object, rc As Long
Set sh = CreateObject("WScript.Shell")
rc = sh.Run("""C:\hmc\hmc.exe"" 192.168.0.2 dci", 0, True)
```

`True` makes `Run` wait until `hmc.exe` exits; then line 1 of `C:\hmc\result.txt` is the value or `ERR`.

For a capture the motor has to move while `hmc.exe` runs, so start it without waiting and wait for `result.txt` instead:

```vba
Const RESULT_FILE As String = "C:\hmc\result.txt"
Dim sh As Object, deadline As Date
If Dir(RESULT_FILE) <> "" Then Kill RESULT_FILE
Set sh = CreateObject("WScript.Shell")
sh.Run """C:\hmc\hmc.exe"" 192.168.0.2 dci --auto", 0, False
' Start the motor here.
deadline = Now + TimeSerial(0, 0, 45)
Do While Dir(RESULT_FILE) = "" And Now < deadline
    DoEvents
Loop
```

- Delete the old `result.txt` first. `hmc.exe` deletes it too, but only after its start-up; until then a stale value from the previous command would still be there.
- `False` makes `Run` return at once, so the motor can start while the capture runs.
- The 45 s deadline covers the longest capture (30 s), the start-up and a margin; for `dci --time 10`, 30 s are enough. No file after the deadline means the capture did not finish.
- `result.txt` is written in one step, so once it exists it is complete: line 1 is the value or `ERR`.
- Parse numbers with `Val()`, which always expects a decimal point. `CDbl` follows the Windows locale and expects a comma on Italian systems.

**One capture, one movement.** The capture may start during the deltastep peaks, but it must end with the device idle: with `--time`, let the device stop before the capture ends; with `--auto`, the capture waits for it.

## How the capture value is computed

`result.txt` holds the **mean current of the movement**: the level the device draws while moving, between two groups of deltastep peaks, in a capture shaped (idle), deltastep peaks, movement, deltastep peaks, idle. The code is `analyzer.py` (`analyze_waveform`):

1. **Validate.** Timestamps must increase. NaN/inf readings and overflow sentinels (+/-9.9E37) are invalid; more than 20% invalid readings reject the capture.
2. **Conversions.** The HMC8012 answers `READ?` with its latest conversion, so consecutive equal readings are one conversion (about 5 per second at SLOW).
3. **Idle.** The capture must end with at least 0.25 s of steady current at the lowest level of the capture.
4. **Peaks.** Conversions above the midpoint between idle and the highest conversion are deltastep peaks; invalid readings count as peaks.
5. **Movement.** Stretches of consecutive conversions above idle by more than two tolerances and below the peaks. At each end of a stretch, the conversions that do not match its level (within the tolerance, max(2 mA, 2%)) straddle the movement's start or stop and are left out. What remains must last at least 0.3 s, more than one SLOW conversion, and exactly one stretch may qualify.
6. **Result.** The mean of the movement's conversions, if two standard errors are within the tolerance.

**Errors instead of wrong numbers.** When no trustworthy value exists, `result.txt` gets `ERR`:

| Error | Meaning | What to change |
|-|-|-|
| `InvalidCaptureError` | Malformed data, too many invalid readings, or the capture does not end with steady idle at its lowest level | Let the capture run until the device is idle (`--auto` does); raise the range if the peaks overflow |
| `SignalNotSettledError` | No movement of at least 0.3 s between idle and the peaks | For a movement of only 2-3 SLOW conversions use `--rate MED` |
| `AmbiguousRunError` | More than one movement in the capture | One movement per capture |
| `ImpreciseValueError` | Movement found, but its conversions scatter too much for its mean (few conversions, edges mixed with the peaks) | `--rate MED` for short movements |

**Known limits.**

- A periodic load whose period divides the 200 ms SLOW conversion period can alias if the ADC aperture is shorter than the conversion period (not stated in the manual). Fast ripple (stepper steps, driver PWM) is averaged within each conversion.
- At SLOW a movement of 0.6 s has 3 conversions, and the ones at its ends mix with the peaks: of the four lab captures of motor 2, one gives a value and three end in `ERR`. `--rate MED` doubles the conversions.
- The deltastep peaks must be in the capture: they set the upper limit of the movement. Without them the movement itself counts as peaks and the capture ends in `SignalNotSettledError`.
- A movement less than two tolerances above idle (about 7 mA at 167 mA) is not told apart from idle.
- A pause inside the movement (current back at idle) splits it in two: if both parts last at least 0.3 s, the capture ends in `AmbiguousRunError`. With `--auto`, a pause of 3 s or more also ends the capture.
- `dcv`, `aci` and `acv` captures use the same analysis as the DC current: they work only when the value rises while the motor runs, the minimum tolerance stays 0.002 in the unit of the function, and the error messages speak of current. The `FUNC?` replies of the AC functions (`CURR:AC`, `VOLT:AC`) are not verified on the instrument; a different reply stops the capture with `instrument config`.

## Developer guide

### Architecture

```text
measure.py              CLI, result.txt
 |-- hmc8012.py         SCPI driver
 |-- capture.py         polling loop, reads through hmc8012.py
 |-- stop_detector.py   auto-stop (--auto)
 |-- analyzer.py        movement mean
 |-- capture_plot.py    plot page (uPlot)
 |-- live_plot.py       live page server, uses capture_plot.py
 `-- live_window.py     native window process

tests/                  lab captures (tests/data/lab) and the simulator
                        (simulation.py, scenarios.py) check analyzer.py
```

What a capture (`--time` or `--auto`) does:

1. `measure.py` opens the instrument (`hmc8012.py`), selects the function and switches to the capture ADC rate if needed.
2. `capture.py` polls `READ?` until the `--time` ends or, with `--auto`, until `stop_detector.py` sees the motor back at idle. Failed readings stay as NaN; five in a row stop the capture.
3. The previous ADC rate is restored.
4. `analyzer.py` computes the value and `measure.py` writes `result.txt`.

With `--live`, each reading also goes to `live_plot.py`, which streams it to the page as a server-sent event; the page redraws at the display refresh rate. At the end, the outcome or the error goes to the live page and to the files that were asked for.

| Module | Responsibility | Public entry points |
|-|-|-|
| `measure.py` | CLI dispatch, `result.txt`, error layers | `main`, `cmd_*`, `write_result`, `clear_result` |
| `hmc8012.py` | SCPI over PyVISA (LAN socket or COM); every setter checks `SYST:ERR?` | `HMC8012`, `ScpiError`, `RangeOverflowError` |
| `capture.py` | Timed polling loop, failure counting | `ContinuousCapture`, `CaptureResult` |
| `stop_detector.py` | Auto-stop: ends an `--auto` capture once the motor has run and is back at idle | `StopDetector` |
| `analyzer.py` | Idle, peaks, movement, precision; raises instead of guessing | `analyze_waveform`, `AnalysisConfig`, `AnalysisResult`, error classes |
| `capture_plot.py` | Plot page of a capture (saved or live), from `plot_assets/` | `render_capture_plot`, `write_capture_plot`, `render_live_page` |
| `live_plot.py` | Serves the live page on `127.0.0.1` and streams the readings | `LivePlot` |
| `live_window.py` | Native live window in a child process (`hmc.exe --live-window <url>`, internal) | `open_live_window`, `run_live_window` |
| `simulation.py` | Test bench: true device current and HMC8012 sampling model | `Phase`, `InstrumentModel`, `simulate_capture` |
| `scenarios.py` | Test bench: device behaviours modelled on the lab captures | `SCENARIOS`, `ScenarioParams` |
| `version.py` | Single source of the release version | `__version__` |
| `plot_assets/` | Page template and uPlot 1.6.32 (MIT), bundled into `hmc.exe` | |

Docstrings in each module are the reference for arguments, returns and raised errors.

### Where to change things

| To change | Edit |
|-|-|
| Analysis tolerance | `AnalysisConfig` defaults in `analyzer.py` |
| Movement detection (peak threshold, shortest movement, idle at the end) | `PEAK_THRESHOLD_FRACTION`, `MIN_MOVEMENT_S`, `MIN_IDLE_S` in `analyzer.py` |
| Default ADC rate of captures | `CAPTURE_ADC_RATE` in `measure.py` |
| Functions a capture supports | `CAPTURE_FUNCTION_REPLIES` in `capture.py` |
| Failed readings that stop a capture | `DEFAULT_MAX_CONSECUTIVE_FAILURES` in `capture.py` |
| Idle time that ends an `--auto` capture | `STOP_HOLD_S` in `stop_detector.py` |
| Longest `--auto` capture | `AUTO_STOP_MAX_DURATION_S` in `measure.py` |
| Look of the plot (colours, labels, layout) | `plot_assets/capture_plot.html` |
| Size and title of the live window | `WINDOW_SIZE`, `WINDOW_TITLE` in `live_window.py` |
| A new simulated behaviour for the tests | a builder and an entry in `SCENARIOS` (`scenarios.py`) |
| A new command | a `cmd_*` function and a branch in `main()` (`measure.py`) |
| The version | `version.py` (see Releases) |

### Tests

`python -m pytest -q` runs the whole suite, including tests that wait for a connection timeout on an unreachable address.

- The analyzer is tested against the lab captures (`tests/data/lab`: the real device at SLOW), the physical simulation (`simulation.py`, `scenarios.py`: the same pattern at each ADC rate) and hand-built edge cases. The rule the tests enforce: a capture yields the right value or an explicit error, never a wrong value.
- The plot tests cover the page content and the live stream over a real loopback connection.
- The live window can only be checked on a Windows PC: start a capture with `--live` and look at it.

### Design decisions

- **Movement only.** The deltastep peaks are another consumer and idle is not the movement: only the level between them counts. The conversions at the movement's ends are left out because each one averages the movement with what came before or after it.
- **An error is better than a wrong number.** Every check (idle at the end, one movement, at least 0.3 s, precision) rejects the capture with a reason instead of returning a value that may be wrong.
- **SLOW by default, without side effects.** SLOW is the only rate with specified accuracy and averages the stepper ripple within each conversion; `--rate MED` trades that for twice the conversions on short movements. The capture restores the previous rate, so single readings keep their settings.
- **One conversion, one value.** The HMC8012 answers `READ?` with its latest conversion, so polling faster than the ADC repeats values; the analysis merges them, so the result does not depend on the polling rate.
- **Failed readings are kept as NaN and count as peaks.** A missing reading next to the movement is never taken for part of it.
- **Diagnostic outputs never change the outcome.** Plot, live window and samples file get the readings and the outcome, also of a failed capture, but `result.txt` never waits for them or depends on them.

### Instrument notes

From the HMC8012 user and SCPI manuals:

- DC current gives 5 / 10 / 200 readings per second at SLOW / MED / FAST, with 5¾ / 4¾ / 4¾ digits.
- Accuracy is specified at SLOW only, and `*RST` sets SLOW.
- `ADCRate` "selects the ADC rate for the activated measurement function".

Not stated in the manuals, to verify on the instrument:

- whether `CONF:CURR:DC` without a range resets the range to auto (capture would then stop with `instrument config`);
- the ADC aperture length.

## Releases and build

The version lives in `version.py` and nowhere else: `hmc.exe --version` prints it and the build writes it into the executable's file properties. Every shipped change bumps it (semantic versioning: major for a changed host contract, minor for new commands, patch for fixes).

`hmc.exe` is built on Windows with Python 3.12 (Nuitka's bundled MinGW-w64 does not support 3.13+):

```bat
pip install -r requirements.txt nuitka
python -m pytest -q
python -m nuitka --onefile --assume-yes-for-downloads --output-filename=hmc.exe ^
  --include-package=pyvisa --include-package=pyvisa_py --include-package=serial ^
  --include-data-dir=plot_assets=plot_assets --enable-plugin=pywebview ^
  --nofollow-import-to=pyvisa.testsuite --nofollow-import-to=pyvisa_py.testsuite ^
  --noinclude-pytest-mode=nofollow ^
  --product-name=hmc8012-measure --file-description="HMC8012 measurement CLI" ^
  --file-version=4.1.0 --product-version=4.1.0 ^
  measure.py
```

Use the version from `version.py` in `--file-version` and `--product-version`.

## Dependencies

- Python 3.11 or newer (3.12 to build the executable)
- `pyvisa`, `pyvisa-py`: instrument communication without NI-VISA
- `pyserial`: COM port connections
- `numpy`: capture analysis
- `pytest`: test suite
- `pywebview`: the live window (on Windows through `pythonnet` and the WebView2 runtime)
- uPlot 1.6.32 (MIT): vendored in `plot_assets/`, nothing to install

```bash
pip install -r requirements.txt
```
