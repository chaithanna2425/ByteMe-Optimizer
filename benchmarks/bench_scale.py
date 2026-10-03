"""
ByteMe Performance Baseline Benchmark (DEMO/SIMULATED DATA ONLY)

Measurement harness - NOT part of the unit test suite. Measures current
CP-SAT solve times and model sizes for the domain-pruned start-slot encoding.

Usage:
    python benchmarks/bench_scale.py [--quick] [--repeats N] [--output PATH]

Records: processes, horizon, contention, stage times/statuses, model sizes,
branches, conflicts, deterministic/wall/user time, memory, and repeats.
Grid: processes x {10, 25, 50, 100} x horizons {24, 48, 168} x
contention {low, high}.
"""

import argparse
import hashlib
import json
import math
import random
import statistics
import time
import tracemalloc
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import optimizer.optimizer as optimizer_engine
import optimizer.public_api as public_api
from optimizer.public_api import optimize


def build_case(n_processes, horizon, contention, seed=42,
               max_time_seconds=120):
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
        "options": {"max_time_seconds": max_time_seconds},
    }


def measure(case, use_hints=True, track_memory=False,
            use_linear_expr_sum=True):
    solve_metrics = []
    input_validation_times = []
    model_construction_times = []
    model_build_starts = []
    model_stages = []
    active_stage = [None]
    original_new_solver = optimizer_engine._new_solver
    original_load_user_input = public_api.load_user_input
    original_build_base_model = optimizer_engine._build_base_model
    original_calculate_start_domains = optimizer_engine._calculate_start_domains
    original_pin_non_flexible = optimizer_engine._pin_non_flexible_processes
    original_add_solar_pool = optimizer_engine._add_shared_solar_pool

    def measured_load_user_input(source):
        started = time.perf_counter()
        try:
            return original_load_user_input(source)
        finally:
            input_validation_times.append(time.perf_counter() - started)

    def measured_build_base_model(*args, **kwargs):
        model_build_starts.append(time.perf_counter())
        stage = {
            "start_domain_generation_seconds": [],
            "pinning_seconds": 0.0,
            "solar_pool_seconds": 0.0,
        }
        model_stages.append(stage)
        active_stage[0] = stage
        started = time.perf_counter()
        model, start_times, end_times = original_build_base_model(*args, **kwargs)
        stage["base_model_seconds"] = time.perf_counter() - started
        if not use_hints:
            model.AddHint = lambda *hint_args, **hint_kwargs: None
        return model, start_times, end_times

    def measured_calculate_start_domains(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original_calculate_start_domains(*args, **kwargs)
        finally:
            if active_stage[0] is not None:
                active_stage[0]["start_domain_generation_seconds"].append(
                    time.perf_counter() - started
                )

    def measured_pin_non_flexible(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original_pin_non_flexible(*args, **kwargs)
        finally:
            if active_stage[0] is not None:
                active_stage[0]["pinning_seconds"] += (
                    time.perf_counter() - started
                )

    def measured_add_solar_pool(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original_add_solar_pool(*args, **kwargs)
        finally:
            if active_stage[0] is not None:
                active_stage[0]["solar_pool_seconds"] += (
                    time.perf_counter() - started
                )

    class MeasuredSolver:
        def __init__(self, solver):
            self.solver = solver

        def Solve(self, model):
            model_build_seconds = (
                time.perf_counter() - model_build_starts.pop(0)
                if model_build_starts else 0.0
            )
            model_construction_times.append(model_build_seconds)
            stage = model_stages.pop(0) if model_stages else {}
            active_stage[0] = None
            proto = model.Proto()
            variables = list(proto.variables)
            stats = {
                "variables": len(variables),
                "boolean_variables": sum(
                    list(variable.domain) == [0, 1]
                    for variable in variables
                ),
                "constraints": len(list(proto.constraints)),
                "model_proto_sha256": hashlib.sha256(
                    str(proto).encode("utf-8")
                ).hexdigest(),
                "model_construction_seconds": model_build_seconds,
                "base_model_seconds": stage.get("base_model_seconds", 0.0),
                "start_domain_generation_seconds": sum(
                    stage.get("start_domain_generation_seconds", [])
                ),
                "pinning_seconds": stage.get("pinning_seconds", 0.0),
                "solar_pool_seconds": stage.get("solar_pool_seconds", 0.0),
            }
            measured_subphases = sum((
                stats["base_model_seconds"], stats["pinning_seconds"],
                stats["solar_pool_seconds"],
            ))
            stats["objective_and_other_build_seconds"] = max(
                model_build_seconds - measured_subphases, 0.0
            )
            status = self.solver.Solve(model)
            stats["solve_time_seconds"] = self.solver.WallTime()
            stats["wall_time_seconds"] = self.solver.WallTime()
            stats["user_time_seconds"] = self.solver.UserTime()
            stats["deterministic_time_seconds"] = (
                self.solver.ResponseProto().deterministic_time
            )
            stats["branches"] = self.solver.NumBranches()
            stats["conflicts"] = self.solver.NumConflicts()
            stats["status"] = self.solver.StatusName(status)
            if status in (optimizer_engine.cp_model.OPTIMAL,
                          optimizer_engine.cp_model.FEASIBLE):
                objective_value = self.solver.ObjectiveValue()
                best_bound = self.solver.BestObjectiveBound()
                stats["objective_value"] = objective_value
                stats["best_bound"] = best_bound
                stats["optimality_gap"] = round(
                    abs(objective_value - best_bound)
                    / max(abs(objective_value), 1.0),
                    6,
                )
            else:
                stats["objective_value"] = None
                stats["best_bound"] = None
                stats["optimality_gap"] = None
            stats["hint_enabled"] = use_hints
            solve_metrics.append(stats)
            return status

        def __getattr__(self, name):
            return getattr(self.solver, name)

    def measured_new_solver(*args, **kwargs):
        return MeasuredSolver(original_new_solver(*args, **kwargs))

    if track_memory:
        tracemalloc.start()
    t0 = time.perf_counter()
    linear_sum_patch = nullcontext()
    if not use_linear_expr_sum:
        linear_sum_patch = patch.object(
            optimizer_engine, "_sum_linear_terms",
            side_effect=lambda terms: sum(terms, 0),
        )
    with patch.object(optimizer_engine, "_new_solver", measured_new_solver), \
            patch.object(public_api, "load_user_input",
                         measured_load_user_input), \
            patch.object(optimizer_engine, "_build_base_model",
                 measured_build_base_model), \
            patch.object(optimizer_engine, "_calculate_start_domains",
                 measured_calculate_start_domains), \
            patch.object(optimizer_engine, "_pin_non_flexible_processes",
                 measured_pin_non_flexible), \
            patch.object(optimizer_engine, "_add_shared_solar_pool",
                  measured_add_solar_pool), \
              linear_sum_patch:
        result = optimize(case)
    elapsed = time.perf_counter() - t0
    if track_memory:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_mb = peak / (1024 * 1024)
    else:
        peak_mb = None
    instrumentation = {
        "input_validation_seconds": sum(input_validation_times),
        "model_construction_seconds": sum(model_construction_times),
        "solver_runs": solve_metrics,
    }
    return elapsed, result, peak_mb, instrumentation


def _median_solver_metric(solver_runs, metric, digits=6):
    values = [run[metric] for run in solver_runs
              if run.get(metric) is not None]
    if not values:
        return None
    return round(statistics.median(values), digits)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true",
                        help="run a reduced grid (small sizes only)")
    parser.add_argument("--output", type=Path,
                        help="write measured rows as JSON to this path")
    parser.add_argument("--repeats", type=int, default=3,
                        help="repeat each case and report distributions")
    parser.add_argument("--max-time-seconds", type=float, default=120,
                        help="time limit applied independently to each solver stage")
    parser.add_argument("--compare-hints", action="store_true",
                        help="run a second benchmark pass with baseline hints disabled")
    parser.add_argument("--track-memory", action="store_true",
                        help="collect Python peak memory with tracemalloc (timings are not comparable)")
    parser.add_argument("--legacy-python-sum", action="store_true",
                        help="benchmark the previous Python sum aggregation as a reference")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if (not math.isfinite(args.max_time_seconds)
            or args.max_time_seconds <= 0):
        parser.error("--max-time-seconds must be a positive finite number")

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
                case = build_case(
                    n, horizon, contention,
                    max_time_seconds=args.max_time_seconds,
                )
                measurements = [
                    measure(
                        case, track_memory=args.track_memory,
                        use_linear_expr_sum=not args.legacy_python_sum,
                    )
                    for _ in range(args.repeats)
                ]
                no_hint_measurements = (
                    [measure(case, use_hints=False,
                             track_memory=args.track_memory,
                             use_linear_expr_sum=not args.legacy_python_sum)
                     for _ in range(args.repeats)]
                    if args.compare_hints else []
                )
                run_metrics = [measurement[3] for measurement in measurements]
                solve_metrics = [metrics["solver_runs"]
                                 for metrics in run_metrics]
                baseline_solver_runs = [run[0] for run in solve_metrics]
                optimized_solver_runs = [
                    run[1] for run in solve_metrics if len(run) > 1
                ]
                baseline_solve = statistics.median(
                    run["solve_time_seconds"] for run in baseline_solver_runs
                )
                optimized_solve = (
                    statistics.median(
                        run["solve_time_seconds"]
                        for run in optimized_solver_runs
                    )
                    if optimized_solver_runs else None
                )
                baseline_model = solve_metrics[0][0]
                optimized_model = (solve_metrics[0][1]
                                   if len(solve_metrics[0]) > 1 else None)
                total_elapsed = statistics.median(
                    measurement[0] for measurement in measurements
                )
                input_validation = [metrics["input_validation_seconds"]
                                    for metrics in run_metrics]
                model_construction = [metrics["model_construction_seconds"]
                                      for metrics in run_metrics]
                peak_values = [measurement[2] for measurement in measurements
                               if measurement[2] is not None]
                peak_mb = (statistics.median(peak_values)
                           if peak_values else None)
                result = measurements[-1][1]
                status = result["status"]
                n_machines = len(case["factory"]["machines"])
                rows.append({
                    "processes": n, "horizon": horizon,
                    "contention": contention, "machines": n_machines,
                    "repeats": args.repeats,
                    "linear_expr_sum_enabled": not args.legacy_python_sum,
                    "baseline_solve_seconds": round(baseline_solve, 4),
                    "baseline_objective_value": _median_solver_metric(
                        baseline_solver_runs, "objective_value"),
                    "baseline_best_bound": _median_solver_metric(
                        baseline_solver_runs, "best_bound"),
                    "baseline_optimality_gap": _median_solver_metric(
                        baseline_solver_runs, "optimality_gap"),
                    "baseline_model_construction_seconds": round(
                        statistics.median(
                            run[0]["model_construction_seconds"]
                            for run in solve_metrics
                        ), 4),
                    "baseline_wall_time_seconds": round(statistics.median(
                        run["wall_time_seconds"]
                        for run in baseline_solver_runs
                    ), 4),
                    "baseline_user_time_seconds": round(statistics.median(
                        run["user_time_seconds"]
                        for run in baseline_solver_runs
                    ), 4),
                    "baseline_deterministic_time_seconds": round(
                        statistics.median(
                            run["deterministic_time_seconds"]
                            for run in baseline_solver_runs
                        ), 6
                    ),
                    "baseline_branches": int(statistics.median(
                        run["branches"] for run in baseline_solver_runs
                    )),
                    "baseline_conflicts": int(statistics.median(
                        run["conflicts"] for run in baseline_solver_runs
                    )),
                    "baseline_solver_statuses": [
                        run["status"] for run in baseline_solver_runs
                    ],
                    "optimized_solve_seconds": (
                        round(optimized_solve, 4)
                        if optimized_solve is not None else None
                    ),
                    "optimized_objective_value": _median_solver_metric(
                        optimized_solver_runs, "objective_value"),
                    "optimized_best_bound": _median_solver_metric(
                        optimized_solver_runs, "best_bound"),
                    "optimized_optimality_gap": _median_solver_metric(
                        optimized_solver_runs, "optimality_gap"),
                    "optimized_model_construction_seconds": (
                        round(statistics.median(
                            run[1]["model_construction_seconds"]
                            for run in solve_metrics if len(run) > 1
                        ), 4) if optimized_solver_runs else None
                    ),
                    "optimized_base_model_seconds": _median_solver_metric(
                        optimized_solver_runs, "base_model_seconds"),
                    "optimized_start_domain_generation_seconds":
                        _median_solver_metric(
                            optimized_solver_runs,
                            "start_domain_generation_seconds"),
                    "optimized_pinning_seconds": _median_solver_metric(
                        optimized_solver_runs, "pinning_seconds"),
                    "optimized_solar_pool_seconds": _median_solver_metric(
                        optimized_solver_runs, "solar_pool_seconds"),
                    "optimized_objective_and_other_build_seconds":
                        _median_solver_metric(
                            optimized_solver_runs,
                            "objective_and_other_build_seconds"),
                    "optimized_wall_time_seconds": (
                        round(statistics.median(
                            run["wall_time_seconds"]
                            for run in optimized_solver_runs
                        ), 4) if optimized_solver_runs else None
                    ),
                    "optimized_user_time_seconds": (
                        round(statistics.median(
                            run["user_time_seconds"]
                            for run in optimized_solver_runs
                        ), 4) if optimized_solver_runs else None
                    ),
                    "optimized_deterministic_time_seconds": (
                        round(statistics.median(
                            run["deterministic_time_seconds"]
                            for run in optimized_solver_runs
                        ), 6) if optimized_solver_runs else None
                    ),
                    "optimized_branches": (
                        int(statistics.median(
                            run["branches"] for run in optimized_solver_runs
                        )) if optimized_solver_runs else None
                    ),
                    "optimized_conflicts": (
                        int(statistics.median(
                            run["conflicts"] for run in optimized_solver_runs
                        )) if optimized_solver_runs else None
                    ),
                    "optimized_solver_statuses": [
                        run["status"] for run in optimized_solver_runs
                    ],
                    "total_optimize_seconds": round(total_elapsed, 4),
                    "input_validation_seconds": round(
                        statistics.median(input_validation), 4),
                    "input_validation_seconds_min": round(
                        min(input_validation), 4),
                    "input_validation_seconds_max": round(
                        max(input_validation), 4),
                    "model_construction_seconds": round(
                        statistics.median(model_construction), 4),
                    "model_construction_seconds_min": round(
                        min(model_construction), 4),
                    "model_construction_seconds_max": round(
                        max(model_construction), 4),
                    "total_api_seconds_min": round(
                        min(measurement[0] for measurement in measurements), 4),
                    "total_api_seconds_max": round(
                        max(measurement[0] for measurement in measurements), 4),
                    "repeat_samples": [
                        {
                            "total_api_seconds": round(measurement[0], 4),
                            "input_validation_seconds": round(
                                metrics["input_validation_seconds"], 4),
                            "model_construction_seconds": round(
                                metrics["model_construction_seconds"], 4),
                            "solver_runs": metrics["solver_runs"],
                            "peak_memory_mb": (
                                round(measurement[2], 2)
                                if measurement[2] is not None else None
                            ),
                            "status": measurement[1]["status"],
                        }
                        for measurement, metrics in zip(measurements, run_metrics)
                    ],
                    "hint_comparison": [
                        {
                            "with_hints": {
                                "total_api_seconds": round(with_hint[0], 4),
                                "solver_runs": with_hint[3]["solver_runs"],
                                "status": with_hint[1]["status"],
                            },
                            "without_hints": {
                                "total_api_seconds": round(without_hint[0], 4),
                                "solver_runs": without_hint[3]["solver_runs"],
                                "status": without_hint[1]["status"],
                            },
                        }
                        for with_hint, without_hint in zip(
                            measurements, no_hint_measurements
                        )
                    ],
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
                    "peak_memory_mb": (
                        round(peak_mb, 2) if peak_mb is not None else None
                    ),
                    "status": status,
                })
                peak_display = f"{peak_mb:.2f}" if peak_mb is not None else "n/a"
                print(f"{n:>6} {horizon:>8} {contention:>8} "
                      f"{baseline_solve:>9.3f} "
                      f"{(optimized_solve or 0):>9.3f} "
                      f"{total_elapsed:>9.3f} "
                      f"{baseline_model['boolean_variables']:>10} "
                      f"{(optimized_model['boolean_variables'] if optimized_model else 0):>9} "
                      f"{peak_display:>8} {status:<10} {n_machines}")

    results_json = json.dumps(rows, indent=2)
    if args.output:
        args.output.write_text(results_json + "\n", encoding="utf-8")
    print("\nJSON results:")
    print(results_json)


if __name__ == "__main__":
    main()
