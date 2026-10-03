"""
ByteMe Phase 4 Solver-Configuration Benchmark (DEMO/SIMULATED DATA ONLY)

Measurement harness for CP-SAT solver-level configuration experiments.
It does NOT change project behavior: it builds the real models through the
real engine and only swaps the solver configuration under test.

Design notes
------------
* Every configuration keeps ``num_workers = 1`` unless explicitly named as a
  multi-worker probe. Single-worker search is what makes ByteMe's results
  byte-identical between runs; multi-worker configs are measured only to
  quantify the determinism trade-off and are never selected by default.
* Screening uses ``max_deterministic_time`` because it is machine
  independent: the same configuration and model perform the same amount of
  solver work regardless of CPU/load, so status/objective/bound comparisons
  between configurations are reproducible.
* Validation uses the real wall-clock limit (``max_time_in_seconds``) exactly
  as the public API does.

Usage:
    python benchmarks/phase4_solver_config.py --screen
    python benchmarks/phase4_solver_config.py --validate <config> [<config>...]
    python benchmarks/phase4_solver_config.py --full

Output: a table on stdout and JSON rows written with --output PATH.
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

# Allow running this file directly (benchmarks is not an installed package).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ortools.sat.python import cp_model

import optimizer.optimizer as optimizer_engine
from benchmarks.bench_scale import build_case, measure


# ---------------------------------------------------------------------------
# Solver configurations under test
# ---------------------------------------------------------------------------
# Each builder receives (max_time_seconds, dtime_limit) and returns a
# configured CpSolver. Every builder except the multi-worker probe sets
# num_workers = 1 to preserve ByteMe's deterministic contract.

def _base_solver(max_time_seconds, dtime_limit):
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    if max_time_seconds is not None:
        solver.parameters.max_time_in_seconds = float(max_time_seconds)
    if dtime_limit is not None:
        solver.parameters.max_deterministic_time = float(dtime_limit)
    return solver


def _current(max_time_seconds, dtime_limit):
    """Exactly the shipped configuration (LP_SEARCH, linearization 2)."""
    solver = _base_solver(max_time_seconds, dtime_limit)
    solver.parameters.search_branching = cp_model.LP_SEARCH
    solver.parameters.linearization_level = 2
    return solver


def _make(configure=None, workers=1):
    def build(max_time_seconds, dtime_limit):
        solver = _base_solver(max_time_seconds, dtime_limit)
        solver.parameters.num_workers = workers
        if configure is not None:
            configure(solver.parameters)
        return solver
    return build


def _automatic_lin2(params):
    params.search_branching = cp_model.AUTOMATIC_SEARCH
    params.linearization_level = 2


def _automatic_lin1(params):
    params.search_branching = cp_model.AUTOMATIC_SEARCH
    params.linearization_level = 1


def _automatic_lin0(params):
    params.search_branching = cp_model.AUTOMATIC_SEARCH
    params.linearization_level = 0


def _lp_lin1(params):
    params.search_branching = cp_model.LP_SEARCH
    params.linearization_level = 1


def _pseudo_lin2(params):
    params.search_branching = cp_model.PSEUDO_COST_SEARCH
    params.linearization_level = 2


def _obj_lb(params):
    params.search_branching = cp_model.LP_SEARCH
    params.linearization_level = 2
    params.use_objective_lb_search = True


def _probing(params):
    params.search_branching = cp_model.LP_SEARCH
    params.linearization_level = 2
    params.use_probing_search = True


def _no_lp(params):
    params.subsolvers.append("no_lp")
    params.linearization_level = 2


def _default_lp(params):
    params.subsolvers.append("default_lp")
    params.linearization_level = 2


def _quick_restart(params):
    params.subsolvers.append("quick_restart")
    params.linearization_level = 2


def _no_symmetry(params):
    params.search_branching = cp_model.LP_SEARCH
    params.linearization_level = 2
    params.symmetry_level = 0


def _extra_presolve(params):
    params.search_branching = cp_model.LP_SEARCH
    params.linearization_level = 2
    params.cp_model_probing_level = 3
    params.max_presolve_iterations = 5


def _no_presolve(params):
    params.search_branching = cp_model.LP_SEARCH
    params.linearization_level = 2
    params.cp_model_presolve = False


def _workers4(params):
    params.search_branching = cp_model.LP_SEARCH
    params.linearization_level = 2


CONFIGS = {
    # shipped configuration (reference)
    "current": _current,
    "auto_lin2": _make(_automatic_lin2),
    "auto_lin1": _make(_automatic_lin1),
    "auto_lin0": _make(_automatic_lin0),
    "lp_lin1": _make(_lp_lin1),
    "pseudo_lin2": _make(_pseudo_lin2),
    "obj_lb": _make(_obj_lb),
    "probing": _make(_probing),
    "no_lp": _make(_no_lp),
    "default_lp": _make(_default_lp),
    "quick_restart": _make(_quick_restart),
    "no_symmetry": _make(_no_symmetry),
    "extra_presolve": _make(_extra_presolve),
    "no_presolve": _make(_no_presolve),
    # multi-worker probe: measured only to quantify the determinism cost
    "workers4_lp_lin2": _make(_workers4, workers=4),
}


# ---------------------------------------------------------------------------
# Case grid
# ---------------------------------------------------------------------------
# Representative easy / medium / hard cases. The hard screen focuses on the
# large contention cases called out in the Phase 4 brief.

SCREEN_CASES = [
    (25, 24, "high"),
    (50, 48, "high"),
    (100, 48, "high"),
    (100, 168, "high"),
]

FULL_CASES = [
    (10, 24, "low"), (10, 24, "high"),
    (25, 24, "low"), (25, 24, "high"),
    (50, 24, "high"),
    (50, 48, "low"), (50, 48, "high"),
    (100, 24, "high"),
    (100, 48, "low"), (100, 48, "high"),
    (50, 168, "low"), (50, 168, "high"),
    (100, 168, "low"), (100, 168, "high"),
]


def run_config(config_name, config_cases, repeats, wall_limit, dtime_limit,
               use_hints=True):
    """Run one solver configuration over the case grid."""
    build_solver = CONFIGS[config_name]
    original_new_solver = optimizer_engine._new_solver

    def patched_new_solver(max_time_seconds=None):
        return build_solver(max_time_seconds, dtime_limit)

    rows = []
    optimizer_engine._new_solver = patched_new_solver
    try:
        for n, horizon, contention in config_cases:
            case = build_case(n, horizon, contention, max_time_seconds=wall_limit)
            measurements = [
                measure(case, use_hints=use_hints) for _ in range(repeats)
            ]
            solver_runs = [m[3]["solver_runs"] for m in measurements]
            optimized = [run[1] for run in solver_runs if len(run) > 1]
            if not optimized:
                continue
            row = {
                "config": config_name,
                "processes": n,
                "horizon": horizon,
                "contention": contention,
                "repeats": repeats,
                "dtime_limit": dtime_limit,
                "wall_limit": wall_limit,
                "use_hints": use_hints,
                "status": sorted({run["status"] for run in optimized}),
                "objective_values": sorted(
                    {run["objective_value"] for run in optimized
                     if run["objective_value"] is not None}
                ),
                "best_bounds": sorted(
                    {run["best_bound"] for run in optimized
                     if run["best_bound"] is not None}
                ),
                "gaps": [run["optimality_gap"] for run in optimized
                         if run["optimality_gap"] is not None],
                "solve_seconds": [round(run["solve_time_seconds"], 4)
                                  for run in optimized],
                "solve_seconds_median": round(
                    statistics.median(run["solve_time_seconds"]
                                      for run in optimized), 4),
                "dtime": [round(run["deterministic_time_seconds"], 4)
                          for run in optimized],
                "dtime_median": round(
                    statistics.median(run["deterministic_time_seconds"]
                                      for run in optimized), 4),
                "branches": [run["branches"] for run in optimized],
                "conflicts": [run["conflicts"] for run in optimized],
                "model_construction_median": round(
                    statistics.median(run["model_construction_seconds"]
                                      for run in optimized), 4),
                "variables": max(run["variables"] for run in optimized),
                "constraints": max(run["constraints"] for run in optimized),
                "total_api_median": round(
                    statistics.median(m[0] for m in measurements), 4),
            }
            rows.append(row)
            print(f"{config_name:>18} {n:>4}p/{horizon:<4}h/{contention:<4} "
                  f"status={','.join(row['status']):<10} "
                  f"obj={row['objective_values']} "
                  f"bound={row['best_bounds']} "
                  f"solve_med={row['solve_seconds_median']:.3f}s "
                  f"dtime_med={row['dtime_median']:.3f} "
                  f"branches={row['branches']}", flush=True)
    finally:
        optimizer_engine._new_solver = original_new_solver
    return rows


def print_summary(rows, configs):
    """Compact comparison table by config."""
    print("\n" + "=" * 120)
    print(f"{'config':>18} | {'#optimal':>8} {'#feasible':>9} {'#unknown':>8} | "
          f"{'med solve s':>11} {'med dtime':>10} | {'worst obj':>14}")
    print("-" * 120)
    for name in configs:
        selected = [r for r in rows if r["config"] == name]
        if not selected:
            continue
        st = [s for r in selected for s in r["status"]]
        optimal = st.count("OPTIMAL")
        feasible = st.count("FEASIBLE")
        unknown = st.count("UNKNOWN")
        solve = statistics.median(r["solve_seconds_median"] for r in selected)
        dtime = statistics.median(r["dtime_median"] for r in selected)
        objs = [r["objective_values"] for r in selected if r["objective_values"]]
        worst = max((o[-1] for o in objs), default=None)
        print(f"{name:>18} | {optimal:>8} {feasible:>9} {unknown:>8} | "
              f"{solve:>11.3f} {dtime:>10.3f} | {str(worst):>14}")
    print("=" * 120)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", action="store_true",
                        help="screen all configs on the hard case subset")
    parser.add_argument("--validate", nargs="+", metavar="CONFIG",
                        help="validate listed configs on the full case grid")
    parser.add_argument("--full", action="store_true",
                        help="run all configs on the full case grid")
    parser.add_argument("--configs", nargs="+",
                        help="override the config list (screen mode)")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--wall-limit", type=float, default=60.0,
                        help="wall-clock per-stage limit (seconds)")
    parser.add_argument("--dtime-limit", type=float, default=None,
                        help="deterministic-time per-stage limit (screening)")
    parser.add_argument("--no-hints", action="store_true",
                        help="disable baseline schedule hints")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    all_rows = []
    if args.screen:
        configs = args.configs or list(CONFIGS)
        for name in configs:
            all_rows += run_config(name, SCREEN_CASES, args.repeats,
                                   args.wall_limit, args.dtime_limit,
                                   use_hints=not args.no_hints)
        print_summary(all_rows, configs)
    elif args.validate:
        for name in args.validate:
            all_rows += run_config(name, FULL_CASES, args.repeats,
                                   args.wall_limit, args.dtime_limit,
                                   use_hints=not args.no_hints)
        print_summary(all_rows, args.validate)
    elif args.full:
        configs = args.configs or list(CONFIGS)
        for name in configs:
            all_rows += run_config(name, FULL_CASES, args.repeats,
                                   args.wall_limit, args.dtime_limit,
                                   use_hints=not args.no_hints)
        print_summary(all_rows, configs)
    else:
        parser.error("choose --screen, --validate, or --full")

    if args.output:
        args.output.write_text(json.dumps(all_rows, indent=2) + "\n",
                               encoding="utf-8")
        print(f"\nWrote {len(all_rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
