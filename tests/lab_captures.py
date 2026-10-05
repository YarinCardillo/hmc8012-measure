"""Lab captures of the real device (tests/data/lab), as test inputs.

Each file is a capture in the --save-samples CSV format, gzipped: the
device's deltastep peaks around one movement, read at the SLOW ADC rate.
"""

from pathlib import Path

import numpy as np

LAB_CAPTURES_DIR = Path(__file__).parent / "data" / "lab"
LAB_CAPTURES = sorted(path.name.removesuffix(".csv.gz") for path in LAB_CAPTURES_DIR.glob("*.csv.gz"))


def load_lab_capture(name: str) -> tuple[np.ndarray, np.ndarray]:
    """``(timestamps, values)`` of the lab capture *name* (file name without .csv.gz)."""
    data = np.loadtxt(LAB_CAPTURES_DIR / f"{name}.csv.gz", delimiter=",", skiprows=2)
    return data[:, 0], data[:, 1]
