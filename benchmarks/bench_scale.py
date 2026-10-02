"""
ByteMe Performance Baseline Benchmark (DEMO/SIMULATED DATA ONLY)

Measurement harness - NOT part of the unit test suite. Measures current
CP-SAT solve times and model sizes for the domain-pruned start-slot encoding.

Usage:
    python benchmarks/bench_scale.py [--quick] [--repeats N] [--output PATH]

Records: processes, horizon, contention, wall time per solve, status.
Grid: processes x {10, 25, 50, 100} x horizons {24, 48, 168} x
contention {low, high}.
"""

import argparse
import json
import random
import statistics
import time
import tracemalloc
from pathlib import Path
from unittest.mock import patch

import optimizer.optimizer as optimizer_engine
from optimizer.public_api import optimize


def build_case(n_processes, horizon, contention, seed=42):
    """
    Build a valid synthetic factory with the requested size and machine
    contention. Deterministic per (size, horizon, contention, seed).

    contention: "low" = ~n/3 machines, "high" = ~n/8 machines (heavy sharing)
    """
    rng = random.Random(seed)
    if contention == "low":
        n_machines = max(2, n_processes // 3)
    else:
        n_machines = max(1, n_processes // 8)

    processes = []
    for i in range(n_processes):
        n_deps = rng.randint(0, min(2, i))
        deps = rng.sample([f"p{j}" for j in range(i)], n_deps)
        processes.append({
            "process_id": f"p{i}",
            "process_name": f"Process {i}",
            "duration_hours": rng.choice([0.5, 1, 1.5, 2, 2.5]),
            "power_kw": rng.choice([5, 10, 20, 40]),
            "dependencies": deps,
            "is_flexible": rng.random() < 0.8,
            "machine_id": f"m{rng.randint(0, n_machines - 1)}",
        })
    machines = [
        {"machine_id": f"m{m}", "machine_name": f"Machine {m}",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": [
             p["process_id"] for p in processes
             if p["machine_id"] == f"m{m}"]}
        for m in range(n_machines)
    ]
    # Generous deadline: feasibility is not the bottleneck we measure
    serial_sum = sum(p["duration_hours"] for p in processes)
    deadline = min(horizon, int(serial_sum * 1.5) + horizon // 2)

    return {
        "factory": {
            "factory_name": f"Bench {n_processes}p {horizon}h {contention}",
            "planning_horizon_hours": horizon,
            "production_deadline": deadline,
            "processes": processes,
            "machines": machines,
        },
        "energy": {
            "solar_profile": {h: (40 if 8 <= h % 24 <= 16 else 0)
                              for h in range(24)},
            "tariff_profile": {h: (0.08 if 9 <= h % 24 <= 15 else 0.45)
                               for h in range(24)},
        },
        "options": {"max_time_seconds": 120},
    }


def measure(case):
    solve_metrics = []
    original_new_solver = optimizer_engine._new_solver

    class MeasuredSolver:
        def __init__(self, solver):
            self.solver = solver

        def Solve(self, model):
            proto = model.Proto()
            variables = list(proto.variables)
            stats = {
                "variables": len(variables),
                "boolean_variables": sum(
                    list(variable.domain) == [0, 1]
                    for variable in variables
                ),
                "constraints": len(list(proto.constraints)),
            }
            status = self.solver.Solve(model)
            stats["solve_time_seconds"] = self.solver.WallTime()
            stats["status"] = self.solver.StatusName(status)
            solve_metrics.append(stats)
            return status

        def __getattr__(self, name):
            return getattr(self.solver, name)

    def measured_new_solver(*args, **kwargs):
        return MeasuredSolver(original_new_solver(*args, **kwargs))

    tracemalloc.start()
    t0 = time.perf_counter()
    with patch.object(optimizer_engine, "_new_solver", measured_new_solver):
        result = optimize(case)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return elapsed, result, peak / (1024 * 1024), solve_metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true",
                        help="run a reduced grid (small sizes only)")
    parser.add_argument("--output", type=Path,
                        help="write measured rows as JSON to this path")
    parser.add_argument("--repeats", type=int, default=1,
                        help="repeat each case and report median measurements")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    sizes = [10, 25, 50, 100]
    horizons = [24, 48, 168]
    contentions = ["low", "high"]
    if args.quick:
        sizes, horizons = [10, 25], [24]

    rows = []
    print(f"{'procs':>6} {'horizon':>8} {'content':>8} "
          f"{'base_s':>9} {'opt_s':>9} {'total_s':>9} "
          f"{'base_bool':>10} {'opt_bool':>9} {'peak_MB':>8} "
          f"{'status':<10} machines")
    print("-" * 106)

    for n in sizes:
        for horizon in horizons:
            for contention in contentions:
                case = build_case(n, horizon, contention)
                measurements = [measure(case) for _ in range(args.repeats)]
                solve_metrics = [measurement[3] for measurement in measurements]
                baseline_solve = statistics.median(
                    run[0]["solve_time_seconds"] for run in solve_metrics
                )
                optimized_samples = [
                    run[1]["solve_time_seconds"] for run in solve_metrics
                    if len(run) > 1
                ]
                optimized_solve = (statistics.median(optimized_samples)
                                   if optimized_samples else None)
                baseline_model = solve_metrics[0][0]
                optimized_model = (solve_metrics[0][1]
                                   if len(solve_metrics[0]) > 1 else None)
                total_elapsed = statistics.median(
                    measurement[0] for measurement in measurements
                )
                peak_mb = statistics.median(
                    measurement[2] for measurement in measurements
                )
                result = measurements[-1][1]
                status = result["status"]
                n_machines = len(case["factory"]["machines"])
                rows.append({
                    "processes": n, "horizon": horizon,
                    "contention": contention, "machines": n_machines,
                    "repeats": args.repeats,
                    "baseline_solve_seconds": round(baseline_solve, 4),
                    "optimized_solve_seconds": (
                        round(optimized_solve, 4)
                        if optimized_solve is not None else None
                    ),
                    "total_optimize_seconds": round(total_elapsed, 4),
                    "baseline_variables": baseline_model["variables"],
                    "baseline_boolean_variables":
                        baseline_model["boolean_variables"],
                    "baseline_constraints": baseline_model["constraints"],
                    "optimized_variables": (
                        optimized_model["variables"]
                        if optimized_model is not None else None
                    ),
                    "optimized_boolean_variables": (
                        optimized_model["boolean_variables"]
                        if optimized_model is not None else None
                    ),
                    "optimized_constraints": (
                        optimized_model["constraints"]
                        if optimized_model is not None else None
                    ),
                    "peak_memory_mb": round(peak_mb, 2),
                    "status": status,
                })
                print(f"{n:>6} {horizon:>8} {contention:>8} "
                      f"{baseline_solve:>9.3f} "
                      f"{(optimized_solve or 0):>9.3f} "
                      f"{total_elapsed:>9.3f} "
                      f"{baseline_model['boolean_variables']:>10} "
                      f"{(optimized_model['boolean_variables'] if optimized_model else 0):>9} "
                      f"{peak_mb:>8.2f} {status:<10} {n_machines}")

    results_json = json.dumps(rows, indent=2)
    if args.output:
        args.output.write_text(results_json + "\n", encoding="utf-8")
    print("\nJSON results:")
    print(results_json)


if __name__ == "__main__":
    main()
