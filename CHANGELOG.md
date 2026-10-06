# Changelog

Released versions of `hmc.exe`. The version lives in `version.py`; each release is tagged `vX.Y.Z` (see "Releases and build" in the README).

## 4.2.1 (2026-10-05)

- Sturdier single readings, and the instrument session is always released at exit.

## 4.2.0 (2026-10-05)

- The capture value is the mean over the whole movement; single dips below the idle level no longer split it.

## 4.1.0 (2026-10-05)

- The capture value is measured on the movement between the deltastep peaks, leaving the peaks out.
- `--rate` sets the sampling rate of a capture.

## 4.0.0 (2026-10-02)

- Changed host contract: a capture is a flag of the measured function, `--time` or `--auto`, for `dcv`, `acv`, `dci` and `aci`.

## 3.0.0 (2026-10-02)

- Changed host contract: a capture without a duration stops by itself once the motor is back to idle.

## 2.1.0 (2026-10-02)

- Capture plots: `--save-plot` writes the capture to a page, `--live` shows it in a window while it records.

## 2.0.0 (2026-10-02)

- Changed host contract: the executable only measures and writes its outcome to `result.txt`; plots and the interactive simulator are gone.
- `adc [SLOW|MED|FAST]` reads or sets the ADC rate of the active function; a capture reads at SLOW and puts the previous rate back at the end, also on failure.
- The raw samples CSV is written only on request (`--save-samples`).
- `hmc.exe --version` and the file properties carry the version (`version.py`); the Windows build runs at every push to `master`.
