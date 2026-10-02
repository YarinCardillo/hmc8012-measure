"""Simulator for the steady-state analyzer: realistic captures plus a chart.

Usage:
    python simulate.py                                   Interactive window with sliders
    python simulate.py --scenario nominal [--adc FAST]   One scenario, chart
    python simulate.py --matrix [--seeds 10]             Pass/fail table, all scenarios and rates
    python simulate.py --csv capture_samples_....csv     Replay a real capture
    Common options: --window S  --tolerance PCT  --min-run S  --max-settle S
                    --seed N  --save out.png
"""

import argparse
from pathlib import Path

from analyzer import AnalysisConfig
from scenarios import SCENARIOS
from simulation import InstrumentModel
from simulator_core import ADC_RATES, MatrixRow, grade_matrix, load_capture_csv, run_simulation


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point (see module docstring)."""
    args = _parse_args(argv)
    config = AnalysisConfig(
        smoothing_window_s=args.window,
        rel_tolerance=args.tolerance / 100.0,
        min_run_s=args.min_run,
        max_settle_s=args.max_settle,
    )
    import simulator_view  # matplotlib is only needed from here on

    if args.matrix:
        rows = grade_matrix(args.seeds, config=config)
        _print_matrix(rows)
        if args.save:
            simulator_view.save_scenario_grid(args.adc, config, args.seed, args.save)
    elif args.scenario:
        outcome = run_simulation(SCENARIOS[args.scenario].phases(), InstrumentModel(adc_rate=args.adc),
                                 config, args.seed)
        print(f"[{outcome.status.value}] {args.scenario} @ {args.adc}: {outcome.message}")
        simulator_view.show_outcome(outcome, config, f"{args.scenario} @ {args.adc}", args.save)
    elif args.csv:
        times, values = load_capture_csv(args.csv)
        simulator_view.show_replay(times, values, config, args.csv.name, args.save)
    else:
        simulator_view.run_interactive(config)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    defaults = AnalysisConfig()
    parser = argparse.ArgumentParser(description="Simulate HMC8012 captures and grade the stable-value analyzer.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scenario", choices=sorted(SCENARIOS), help="Run one scenario and plot it")
    mode.add_argument("--matrix", action="store_true", help="Grade all scenarios at all ADC rates")
    mode.add_argument("--csv", type=Path, help="Replay a capture_samples_*.csv file")
    parser.add_argument("--adc", choices=ADC_RATES, default="SLOW", help="ADC rate (default SLOW, as capture)")
    parser.add_argument("--seed", type=int, default=0, help="Noise seed (default 0)")
    parser.add_argument("--seeds", type=int, default=10, help="Seeds per cell for --matrix (default 10)")
    parser.add_argument("--window", type=float, default=defaults.smoothing_window_s, help="Smoothing window [s]")
    parser.add_argument("--tolerance", type=float, default=defaults.rel_tolerance * 100.0, help="Tolerance [%%]")
    parser.add_argument("--min-run", type=float, default=defaults.min_run_s, help="Min run length [s]")
    parser.add_argument("--max-settle", type=float, default=defaults.max_settle_s,
                        help="Longest start/end trim for inrush and ramps [s]")
    parser.add_argument("--save", type=Path, help="Save the chart to this PNG instead of showing it")
    return parser.parse_args(argv)


def _print_matrix(rows: list[MatrixRow]) -> None:
    print(f"{'scenario':20s} {'adc':5s} {'pass':>5s} {'fail':>5s} {'raise':>6s}")
    for row in rows:
        flag = "  <-- WRONG VALUES" if row.failed else ""
        print(f"{row.scenario:20s} {row.adc_rate:5s} {row.passed:5d} {row.failed:5d} {row.raised:6d}{flag}")


if __name__ == "__main__":
    main()
