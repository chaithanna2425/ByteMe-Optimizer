"""
ByteMe Phase 4 Solver-Configuration Benchmark (DEMO/SIMULATED DATA ONLY)

Fair comparison harness for CP-SAT solver-level configuration experiments.
It never changes project behavior: models are built by the real engine and
only the solver configuration is swapped.

METHODOLOGY (important)
-----------------------
Non-flexible processes are pinned to their *baseline* start positions. The
baseline is itself an optimal solution that can be non-unique, so two
solver configurations can legitimately pin processes differently and thus
solve DIFFERENT optimized models. To compare configurations fairly, this
harness:

  1. solves the baseline ONCE with the shipped (reference) configuration,
  2. reuses that exact baseline for every configuration under test,
  3. therefore every configuration solves the same optimized model
     (the model SHA-256 is captured for each solve to verify it),
  4. only then are status / objective / bound / gap / time compared.

The shipped configuration is always included as the reference row, so every
number is reported relative to the current behavior.

Wall-clock limits are the product metric. Optional deterministic-time
limits make screening reproducible across machines.

Usage:
    python benchmarks/phase4_optimized_stage.py --screen
    python benchmarks/phase4_optimized_stage.py --compare current auto_lin2
    python benchmarks/phase4_optimized_stage.py --baseline-stability
"""

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ortools.sat.python import cp_model

import optimizer.optimizer as optimizer_engine
from optimizer.input_layer import load_user_input
from optimizer.optimizer import (
    SolverUnknownError,
    create_baseline_schedule,
    create_cost_optimized_schedule,
)
from benchmarks.bench_scale import build_case


# ---------------------------------------------------------------------------
# Solver configurations
# ---------------------------------------------------------------------------

def _apply_common(solver, max_time_seconds, dtime_limit):
    solver.parameters.num_workers = 1
    if max_time_seconds is not None:
        solver.parameters.max_time_in_seconds = float(max_time_seconds)
    if dtime_limit is not None:
        solver.parameters.max_deterministic_time = float(dtime_limit)
    return solver


def shipped(max_time_seconds, dtime_limit):
    """The exact configuration currently shipped."""
    solver = _apply_common(cp_model.CpSolver(), max_time_seconds, dtime_limit)
    solver.parameters.search_branching = cp_model.LP_SEARCH
    solver.parameters.linearization_level = 2
    return solver


def _factory(configure=None, workers=1):
    def build(max_time_seconds, dtime_limit):
        solver = _apply_common(cp_model.CpSolver(), max_time_seconds, dtime_limit)
        solver.parameters.num_workers = workers
        if configure is not None:
            configure(solver.parameters)
        return solver
    return build


def _auto_lin2(p):
    p.search_branching = cp_model.AUTOMATIC_SEARCH
    p.linearization_level = 2


def _auto_lin1(p):
    p.search_branching = cp_model.AUTOMATIC_SEARCH
    p.linearization_level = 1


def _auto_lin0(p):
    p.search_branching = cp_model.AUTOMATIC_SEARCH
    p.linearization_level = 0


def _lp_lin1(p):
    p.search_branching = cp_model.LP_SEARCH
    p.linearization_level = 1


def _lp_lin0(p):
    p.search_branching = cp_model.LP_SEARCH
    p.linearization_level = 0


def _pseudo_lin2(p):
    p.search_branching = cp_model.PSEUDO_COST_SEARCH
    p.linearization_level = 2


def _obj_lb(p):
    p.search_branching = cp_model.LP_SEARCH
    p.linearization_level = 2
    p.use_objective_lb_search = True


def _probing(p):
    p.search_branching = cp_model.LP_SEARCH
    p.linearization_level = 2
    p.use_probing_search = True


def _no_lp(p):
    p.subsolvers.append("no_lp")
    p.linearization_level = 2


def _default_lp(p):
    p.subsolvers.append("default_lp")
    p.linearization_level = 2


def _quick_restart(p):
    p.subsolvers.append("quick_restart")
    p.linearization_level = 2


def _no_symmetry(p):
    p.search_branching = cp_model.LP_SEARCH
    p.linearization_level = 2
    p.symmetry_level = 0


def _extra_presolve(p):
    p.search_branching = cp_model.LP_SEARCH
    p.linearization_level = 2
    p.cp_model_probing_level = 3
    p.max_presolve_iterations = 5


def _no_presolve(p):
    p.search_branching = cp_model.LP_SEARCH
    p.linearization_level = 2
    p.cp_model_presolve = False


def _optimization_hints_off(p):
    p.search_branching = cp_model.LP_SEARCH
    p.linearization_level = 2
    p.use_optimization_hints = False


def _presolve_once(p):
    p.search_branching = cp_model.LP_SEARCH
    p.linearization_level = 2
    p.max_presolve_iterations = 1


def _probing0(p):
    p.search_branching = cp_model.LP_SEARCH
    p.linearization_level = 2
    p.cp_model_probing_level = 0


def _no_bva(p):
    p.search_branching = cp_model.LP_SEARCH
    p.linearization_level = 2
    p.presolve_use_bva = False


def _no_sat_presolve(p):
    p.search_branching = cp_model.LP_SEARCH
    p.linearization_level = 2
    p.cp_model_use_sat_presolve = False


def _no_extract_encoding(p):
    p.search_branching = cp_model.LP_SEARCH
    p.linearization_level = 2
    p.presolve_extract_integer_enforcement = False
    p.presolve_substitution_level = 0


CONFIGS = {
    "shipped": shipped,
    "auto_lin2": _factory(_auto_lin2),
    "auto_lin1": _factory(_auto_lin1),
    "auto_lin0": _factory(_auto_lin0),
    "lp_lin1": _factory(_lp_lin1),
    "lp_lin0": _factory(_lp_lin0),
    "pseudo_lin2": _factory(_pseudo_lin2),
    "obj_lb": _factory(_obj_lb),
    "probing": _factory(_probing),
    "no_lp": _factory(_no_lp),
    "default_lp": _factory(_default_lp),
    "quick_restart": _factory(_quick_restart),
    "no_symmetry": _factory(_no_symmetry),
    "extra_presolve": _factory(_extra_presolve),
    "no_presolve": _factory(_no_presolve),
    "opt_hints_off": _factory(_optimization_hints_off),
    "presolve_once": _factory(_presolve_once),
    "probing0": _factory(_probing0),
    "no_bva": _factory(_no_bva),
    "no_sat_presolve": _factory(_no_sat_presolve),
    "no_substitution": _factory(_no_extract_encoding),
}

# Hard-screen subset: the large contention cases from the Phase 4 brief plus
# a classic medium case where the shipped configuration struggles.
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


def solve_with(factory, fn, *args, _dtime=None, _use_hints=True,
               _model_fingerprints=None, **kwargs):
    """Run an engine call with a specific solver configuration."""
    original = optimizer_engine._new_solver

    def patched_new_solver(max_time_seconds=None):
        solver = factory(max_time_seconds, _dtime)
        original_solve = solver.Solve

        def tracked_solve(model, *solve_args, **solve_kwargs):
            proto = model.Proto()
            if not _use_hints:
                proto.clear_solution_hint()
            if _model_fingerprints is not None:
                _model_fingerprints.append(hashlib.sha256(
                    str(proto).encode("utf-8")
                ).hexdigest())
            return original_solve(model, *solve_args, **solve_kwargs)

        solver.Solve = tracked_solve
        return solver

    optimizer_engine._new_solver = patched_new_solver
    try:
        return fn(*args, **kwargs)
    finally:
        optimizer_engine._new_solver = original


def run_case(case_name, n, horizon, contention, config_names, wall_limit,
             dtime_limit, repeats=1, use_hints=True):
    """Compare configurations on one case with a fixed baseline."""
    case = build_case(n, horizon, contention, max_time_seconds=wall_limit)
    config = load_user_input(case)
    solar_profile = getattr(config, "energy", {}).get("solar_profile")
    tariff_profile = getattr(config, "energy", {}).get("tariff_profile")

    baseline = solve_with(
        shipped, create_baseline_schedule, config,
        solar_profile=solar_profile, tariff_profile=tariff_profile,
        max_time_seconds=wall_limit,
    )
    baseline_starts = {
        p["process_id"]: int(round(p["start_time"] * 2))
        for p in baseline["processes"]
    }

    rows = []
    for name in config_names:
        factory = CONFIGS[name]
        samples = []
        for _ in range(repeats):
            started = time.perf_counter()
            fingerprints = []
            try:
                optimized = solve_with(
                    factory, create_cost_optimized_schedule, config,
                    solar_profile=solar_profile, tariff_profile=tariff_profile,
                    baseline_schedule=baseline, max_time_seconds=wall_limit,
                    _dtime=dtime_limit, _use_hints=use_hints,
                    _model_fingerprints=fingerprints,
                )
            except SolverUnknownError as err:
                elapsed = time.perf_counter() - started
                samples.append({
                    "status": "UNKNOWN",
                    "solve_seconds": getattr(err, "solve_time_seconds", None),
                    "threshold_seconds": round(elapsed, 4),
                    "model_fingerprint": fingerprints[-1] if fingerprints else None,
                })
                continue
            elapsed = time.perf_counter() - started
            if optimized is None:
                samples.append({
                    "status": "INFEASIBLE",
                    "model_fingerprint": fingerprints[-1] if fingerprints else None,
                })
                continue
            diag = optimized.get("solver_diagnostics") or {}
            samples.append({
                "status": optimized["status"],
                "objective": diag.get("best_objective"),
                "bound": diag.get("best_bound"),
                "gap": diag.get("optimality_gap"),
                "solve_seconds": optimized.get("solve_time_seconds"),
                "threshold_seconds": round(elapsed, 4),
                "cost": optimized.get("total_energy_cost"),
                "solar": optimized.get("total_solar_kwh"),
                "grid": optimized.get("total_grid_kwh"),
                "makespan": optimized.get("makespan"),
                "model_fingerprint": fingerprints[-1] if fingerprints else None,
                "starts": {p["process_id"]: p["start_time"]
                           for p in optimized["processes"]},
            })
        rows.append({
            "case": case_name,
            "config": name,
            "wall_limit": wall_limit,
            "dtime_limit": dtime_limit,
            "hints": use_hints,
            "baseline_starts": baseline_starts,
            "samples": samples,
        })
        s = samples[-1]
        print(f"{case_name:>16} {name:>16} status={s['status']:<9} "
              f"obj={str(s.get('objective')):>12} bound={str(s.get('bound')):>12} "
              f"gap={str(s.get('gap')):>9} solve={str(s.get('solve_seconds')):>8} "
              f"wall={s.get('threshold_seconds')}", flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", action="store_true")
    parser.add_argument("--compare", nargs="+", metavar="CONFIG")
    parser.add_argument("--baseline-stability", action="store_true",
                        help="check whether configs pick different baseline ties")
    parser.add_argument("--configs", nargs="+")
    parser.add_argument("--cases", nargs="+",
                        help="case names (defaults to screen subset)")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--wall-limit", type=float, default=30.0)
    parser.add_argument("--dtime-limit", type=float, default=None)
    parser.add_argument("--no-hints", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    cases_by_name = {f"{n}p/{h}h/{c}": (n, h, c) for n, h, c in FULL_CASES}
    if args.cases:
        selected_cases = [(name, *cases_by_name[name]) for name in args.cases]
    else:
        selected_cases = [(f"{n}p/{h}h/{c}", n, h, c)
                          for n, h, c in SCREEN_CASES]

    if args.baseline_stability:
        print("Baseline tie-breaking stability across solver configurations")
        print("(identical start dicts mean identical optimized pinned models)\n")
        for case_name, n, h, c in selected_cases:
            case = build_case(n, h, c, max_time_seconds=args.wall_limit)
            config = load_user_input(case)
            solar = getattr(config, "energy", {}).get("solar_profile")
            tariff = getattr(config, "energy", {}).get("tariff_profile")
            signatures = {}
            for name in (args.configs or list(CONFIGS)):
                baseline = solve_with(
                    CONFIGS[name], create_baseline_schedule, config,
                    solar_profile=solar, tariff_profile=tariff,
                    max_time_seconds=args.wall_limit,
                )
                sig = tuple(sorted(
                    (p["process_id"], p["start_time"])
                    for p in baseline["processes"]
                ))
                signatures.setdefault(hashlib.sha256(
                    repr(sig).encode()).hexdigest()[:12], []).append(name)
            print(f"  {case_name:>16}: {len(signatures)} distinct baseline "
                  f"schedule(s) -> {list(signatures.values())}")
        return

    if args.compare:
        config_names = args.compare
    elif args.configs:
        config_names = args.configs
    else:
        config_names = ["shipped"] + [c for c in CONFIGS if c != "shipped"]
    rows = []
    for case_name, n, h, c in selected_cases:
        rows += run_case(case_name, n, h, c, config_names, args.wall_limit,
                         args.dtime_limit, repeats=args.repeats,
                         use_hints=not args.no_hints)

    print("\nPer-config summary (median across cases, last sample per case)")
    print(f"{'config':>16} | {'#OPTIMAL':>8} {'#FEAS':>6} {'#UNK':>5} | "
          f"{'med solve':>9} | {'med gap':>8}")
    print("-" * 70)
    for name in config_names:
        selected = [r for r in rows if r["config"] == name]
        if not selected:
            continue
        samples = [r["samples"][-1] for r in selected]
        statuses = [s["status"] for s in samples]
        solves = [s["solve_seconds"] for s in samples
                  if s.get("solve_seconds") is not None]
        gaps = [s["gap"] for s in samples if s.get("gap") is not None]
        print(f"{name:>16} | {statuses.count('OPTIMAL'):>8} "
              f"{statuses.count('FEASIBLE'):>6} {statuses.count('UNKNOWN'):>5} | "
              f"{(statistics.median(solves) if solves else float('nan')):>9.3f} | "
              f"{(statistics.median(gaps) if gaps else float('nan')):>8.5f}")

    if args.output:
        args.output.write_text(json.dumps(rows, indent=2) + "\n",
                               encoding="utf-8")
        print(f"\nWrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
