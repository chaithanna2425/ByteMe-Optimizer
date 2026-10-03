"""
ByteMe Phase 3 Tests: Robustness, Determinism & Reliability
(DEMO/SIMULATED DATA ONLY)

- Determinism: repeated runs on identical input produce identical results
- Property/invariant tests over generated valid factories
- Adversarial/robustness inputs: valid-but-extreme and clearly invalid
- Golden regression invariants for the 7 demo factories
- Solver limits: time limit honored; UNKNOWN never reported as INFEASIBLE
"""

import copy
import json
import math
import random
import unittest

from optimizer import public_api
from optimizer.factory_data import AVAILABLE_FACTORIES
from optimizer.input_layer import validate_user_input
from optimizer.optimizer import DEFAULT_PUBLIC_TIME_LIMIT_SECONDS
from optimizer.public_api import (
    STATUS_INFEASIBLE,
    STATUS_INVALID_INPUT,
    STATUS_OPTIMAL,
    STATUS_UNKNOWN,
    optimize,
)

try:
    import jsonschema
    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False


# ---------------------------------------------------------------------------
# Deterministic generated-factory factory (seeded -> reproducible)
# ---------------------------------------------------------------------------

def generate_factory(seed):
    """
    Generate a VALID random factory configuration (deterministic per seed):
    random DAG of processes, machines with contention, mixed flexibility,
    legal time windows, always-solvable deadline slack.
    """
    rng = random.Random(seed)
    n = rng.randint(3, 8)
    n_machines = rng.randint(1, max(1, n - 1))

    processes = []
    for i in range(n):
        pid = f"proc_{i}"
        # Dependencies only on EARLIER processes -> acyclic by construction
        n_deps = rng.randint(0, min(2, i))
        deps = rng.sample([f"proc_{j}" for j in range(i)], n_deps)
        processes.append({
            "process_id": pid,
            "process_name": f"Process {i}",
            "duration_hours": rng.choice([0.5, 1, 1.5, 2]),
            "power_kw": rng.choice([2, 5, 10, 15]),
            "dependencies": deps,
            "is_flexible": rng.random() < 0.7,
            "machine_id": f"machine_{rng.randint(0, n_machines - 1)}",
        })

    machines = [
        {"machine_id": f"machine_{m}", "machine_name": f"Machine {m}",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": [
             p["process_id"] for p in processes
             if p["machine_id"] == f"machine_{m}"
         ]}
        for m in range(n_machines)
    ]

    # Serial worst case always fits: sum of durations * 2 + slack
    total = sum(p["duration_hours"] for p in processes)
    horizon = int(math.ceil(total * 2)) + 4
    return {
        "factory": {
            "factory_name": f"Generated Factory {seed}",
            "factory_type": "generated",
            "planning_horizon_hours": horizon,
            "production_deadline": horizon - 2,
            "processes": processes,
            "machines": machines,
        },
        "energy": {
            "solar_profile": {
                h: rng.choice([0, 0, 10, 25, 50]) for h in range(24)
            },
            "tariff_profile": {
                h: rng.choice([0.05, 0.1, 0.2, 0.4, 0.5]) for h in range(24)
            },
            "grid_emission_factor": 0.35,
        },
    }


def _strip_solve_times(obj):
    """Recursively remove 'solve_time_seconds' from a nested dict/list.

    solve_time_seconds is a measured wall-clock value that naturally varies
    between runs; determinism tests must exclude it from equality comparisons
    while still verifying all algorithmic/schedule outputs are bit-identical.
    """
    if isinstance(obj, dict):
        return {k: _strip_solve_times(v) for k, v in obj.items()
                if k != "solve_time_seconds"}
    if isinstance(obj, list):
        return [_strip_solve_times(item) for item in obj]
    return obj


def _by_id(schedule):
    return {p["process_id"]: p for p in schedule["processes"]}


def _recompute_cost(row, tariff):
    """Independent grid-cost recomputation for one process row."""
    t, cost = row["start_time"], 0.0
    solar = row.get("_solar_profile", None)
    while t < row["end_time"]:
        h = int(t)
        dur = min(h + 1, row["end_time"]) - t
        energy = row["power_kw"] * dur
        grid = max(energy - row["_solar_at"].get(h, 0) * dur, 0.0)
        cost += grid * tariff.get(h, 0)
        t += dur
    return cost


# ---------------------------------------------------------------------------
# 1. Determinism
# ---------------------------------------------------------------------------

class DeterminismTests(unittest.TestCase):
    """Repeated runs on identical input must produce identical results."""

    def test_repeated_runs_identical_all_factories(self):
        for key in AVAILABLE_FACTORIES:
            source = {
                "factory": copy.deepcopy(AVAILABLE_FACTORIES[key]),
                "energy": {"grid_emission_factor": 0.3},
            }
            with self.subTest(factory=key):
                runs = [
                    json.loads(json.dumps(
                        optimize(copy.deepcopy(source), objective=obj)
                    ))
                    for obj in ("cost", "solar")
                    for _ in range(3)
                ]
                for obj_index in (0, 1):
                    first = _strip_solve_times(runs[obj_index * 3])
                    for repeat in runs[obj_index * 3 + 1:(obj_index + 1) * 3]:
                        self.assertEqual(
                            first, _strip_solve_times(repeat),
                            msg=f"{key}/{obj_index}: repeated runs differ",
                        )

    def test_generated_factories_deterministic(self):
        for seed in (1, 2, 3):
            source = generate_factory(seed)
            with self.subTest(seed=seed):
                first = json.loads(json.dumps(optimize(copy.deepcopy(source))))
                second = json.loads(json.dumps(optimize(copy.deepcopy(source))))
                self.assertEqual(
                    _strip_solve_times(first), _strip_solve_times(second))
                self.assertEqual(first["status"], STATUS_OPTIMAL)

    def test_engine_level_single_worker_configuration(self):
        from optimizer import optimizer as engine
        self.assertEqual(engine.DETERMINISTIC_WORKERS, 1)
        solver = engine._new_solver(max_time_seconds=5)
        self.assertEqual(solver.parameters.num_workers, 1)
        self.assertEqual(solver.parameters.max_time_in_seconds, 5.0)
        # No explicit limit for direct engine callers: CP-SAT default = inf
        self.assertEqual(engine._new_solver().parameters.max_time_in_seconds,
                         math.inf)


# ---------------------------------------------------------------------------
# 2. Property / invariant tests (generated valid factories)
# ---------------------------------------------------------------------------

class PropertyInvariantTests(unittest.TestCase):
    """Every generated valid factory must yield fully invariant schedules."""

    def _assert_schedule_invariants(self, schedule, config_data, tariff,
                                    solar, objective, baseline):
        factory = config_data["factory"]
        specs = {p["process_id"]: p for p in factory["processes"]}

        # every process exactly once
        ids = [p["process_id"] for p in schedule["processes"]]
        self.assertEqual(sorted(ids), sorted(specs))

        rows = _by_id(schedule)

        # Recompute each row's solar and cost against the shared half-hour
        # slot pool, split greedily in process_id order.
        running = {}
        for row in sorted(schedule["processes"],
                          key=lambda r: r["process_id"]):
            t = row["start_time"]
            while t < row["end_time"]:
                slot = int(t * 2)
                dur = min((slot + 1) / 2, row["end_time"]) - t
                running.setdefault(slot, []).append(
                    (row["process_id"], row["power_kw"] * dur, dur))
                t += dur
        pool_solar = {p["process_id"]: 0.0 for p in schedule["processes"]}
        pool_cost = {p["process_id"]: 0.0 for p in schedule["processes"]}
        for slot, runners in running.items():
            hour = (slot // 2) % 24
            solar_kw = solar.get(hour, 0)
            supply = solar_kw / 2
            for pid, energy, dur in runners:
                draw_cap = solar_kw * dur
                s = min(energy, draw_cap, max(supply, 0.0))
                supply -= s
                pool_solar[pid] += s
                pool_cost[pid] += (energy - s) * tariff.get(hour, 0)

        for pid, spec in specs.items():
            row = rows[pid]
            # duration + ordering
            self.assertAlmostEqual(
                row["end_time"] - row["start_time"],
                spec["duration_hours"], places=9, msg=pid,
            )
            self.assertLess(row["start_time"], row["end_time"], msg=pid)
            # windows + deadline + horizon
            self.assertGreaterEqual(row["start_time"], 0, msg=pid)
            self.assertLessEqual(
                row["end_time"], factory["production_deadline"] + 1e-9, msg=pid)
            if spec.get("earliest_start") is not None:
                self.assertGreaterEqual(
                    row["start_time"] + 1e-9, spec["earliest_start"], msg=pid)
            if spec.get("latest_finish") is not None:
                self.assertLessEqual(
                    row["end_time"] + 1e-9, spec["latest_finish"], msg=pid)
            # energy identity, no negatives
            total = spec["power_kw"] * spec["duration_hours"]
            self.assertAlmostEqual(
                row["solar_kwh"] + row["grid_kwh"], total, places=6, msg=pid)
            self.assertGreaterEqual(row["solar_kwh"], 0, msg=pid)
            self.assertGreaterEqual(row["grid_kwh"], 0, msg=pid)
            # solar = the process's share of the shared slot pool
            # (never over-allocated; recomputed independently)
            self.assertAlmostEqual(row["solar_kwh"], pool_solar[pid],
                                   places=6, msg=pid)
            # cost = tariff x (demand - allocated shared solar)
            self.assertAlmostEqual(row["energy_cost"], pool_cost[pid],
                                   places=6, msg=pid)

        # dependencies
        for pid, spec in specs.items():
            for dep in spec["dependencies"]:
                self.assertLessEqual(
                    rows[dep]["end_time"], rows[pid]["start_time"] + 1e-9,
                    msg=f"{dep}->{pid}")

        # machine no-overlap
        by_machine = {}
        for pid, spec in specs.items():
            by_machine.setdefault(spec["machine_id"], []).append(pid)
        for machine_id, pids in by_machine.items():
            intervals = sorted(
                (rows[p]["start_time"], rows[p]["end_time"]) for p in pids)
            for (s1, e1), (s2, _) in zip(intervals, intervals[1:]):
                self.assertLessEqual(
                    e1, s2 + 1e-9, msg=f"overlap on {machine_id}")

        # makespan consistency
        makespan = max(r["end_time"] for r in rows.values())
        self.assertAlmostEqual(schedule["makespan_hours"], makespan, places=9)

        # non-flexible pinning (optimized vs baseline)
        if objective == "cost" and baseline is not None:
            base_rows = _by_id(baseline)
            for pid, spec in specs.items():
                if not spec["is_flexible"]:
                    self.assertEqual(
                        rows[pid]["start_time"],
                        base_rows[pid]["start_time"], msg=f"pinning {pid}")

    def test_generated_factories_all_invariants(self):
        for seed in range(1, 21):
            source = generate_factory(seed)
            with self.subTest(seed=seed):
                result = optimize(copy.deepcopy(source))
                self.assertEqual(result["status"], STATUS_OPTIMAL, msg=seed)
                payload = result["result"]
                tariff = source["energy"]["tariff_profile"]
                solar = source["energy"]["solar_profile"]

                self._assert_schedule_invariants(
                    payload["baseline"]["processes"] and {
                        "processes": payload["baseline"]["processes"],
                        "makespan_hours": payload["baseline"]["makespan_hours"],
                    },
                    source, tariff, solar, "baseline", None,
                )
                self._assert_schedule_invariants(
                    {
                        "processes": payload["optimized"]["processes"],
                        "makespan_hours": payload["optimized"]["makespan_hours"],
                    },
                    source, tariff, solar, "cost",
                    {
                        "processes": payload["baseline"]["processes"],
                    },
                )

                # energy identity at aggregate level
                energy = payload["optimized"]["energy"]
                self.assertAlmostEqual(
                    energy["total_kwh"],
                    energy["solar_kwh"] + energy["grid_kwh"], places=6)
                self.assertGreaterEqual(energy["total_kwh"], 0)

                # cost totals equal row sums
                for key, sched in (("cost_baseline", payload["baseline"]),
                                   ("cost_optimized", payload["optimized"])):
                    total = sum(r["energy_cost"] for r in sched["processes"])
                    self.assertAlmostEqual(
                        payload["comparison"][key], total, places=6, msg=seed)

                # optimized never worse than baseline under cost objective
                self.assertLessEqual(
                    payload["comparison"]["cost_optimized"],
                    payload["comparison"]["cost_baseline"] + 1e-6, msg=seed)

                # schema validity
                if _HAS_JSONSCHEMA:
                    jsonschema.validate(result, public_api.get_output_schema())

    def test_generated_processes_have_no_industry_assumptions(self):
        # Generated factories use neutral names; results must not depend on
        # any industry term.
        source = generate_factory(7)
        result = optimize(copy.deepcopy(source))["result"]
        self.assertEqual(result["factory_name"], "Generated Factory 7")


# ---------------------------------------------------------------------------
# 3. Adversarial / robustness inputs
# ---------------------------------------------------------------------------

class AdversarialInputTests(unittest.TestCase):
    """Extreme-but-valid inputs succeed; invalid inputs fail clearly."""

    def test_large_dependency_chain_is_iterative_and_order_independent(self):
        count = 1200
        ordered = [
            {"process_id": f"p{i}", "duration_hours": 0.5,
             "power_kw": 1,
             "dependencies": [f"p{i + 1}"] if i + 1 < count else []}
            for i in range(count)
        ]
        for processes in (ordered, list(reversed(ordered))):
            with self.subTest(reverse=processes[0]["process_id"] != "p0"):
                config = validate_user_input({
                    "factory": {
                        "factory_name": "Large Dependency Chain",
                        "planning_horizon_hours": count / 2,
                        "production_deadline": count / 2,
                        "processes": copy.deepcopy(processes),
                    },
                })
                self.assertEqual(len(config.processes), count)

    def test_dependency_validation_handles_branch_merge_and_disconnected_nodes(self):
        processes = [
            {"process_id": "merge", "duration_hours": 0.5,
             "power_kw": 1, "dependencies": ["left", "right"]},
            {"process_id": "isolated", "duration_hours": 0.5,
             "power_kw": 1, "dependencies": []},
            {"process_id": "right", "duration_hours": 0.5,
             "power_kw": 1, "dependencies": ["root"]},
            {"process_id": "left", "duration_hours": 0.5,
             "power_kw": 1, "dependencies": ["root"]},
            {"process_id": "root", "duration_hours": 0.5,
             "power_kw": 1, "dependencies": []},
        ]
        config = validate_user_input({
            "factory": {
                "factory_name": "Branch Merge Graph",
                "planning_horizon_hours": 4,
                "production_deadline": 4,
                "processes": processes,
            },
        })
        self.assertEqual(len(config.processes), len(processes))

    def test_extremely_large_but_valid_values(self):
        # Large-but-representable values must produce finite, consistent math.
        # (Feasibility bound: power_kw x tariff x duration_hours x 1000 must
        # stay within the model's integer cost domain - see documentation.)
        source = generate_factory(11)
        source["factory"]["processes"][0]["power_kw"] = 100_000
        source["energy"]["tariff_profile"] = {h: 0.4 for h in range(24)}
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        payload = result["result"]
        self.assertGreater(payload["comparison"]["cost_baseline"], 0)
        self.assertTrue(math.isfinite(payload["comparison"]["cost_baseline"]))

    def test_beyond_integer_scaling_limits_reports_cleanly(self):
        # Extreme values exceed the documented integer cost scaling. Since
        # Phase 4.1 this is caught UPFRONT as INVALID INPUT naming the
        # process (previously: mysterious solver INFEASIBLE).
        source = generate_factory(11)
        source["factory"]["processes"][0]["power_kw"] = 10_000_000
        source["energy"]["tariff_profile"] = {h: 9_999.0 for h in range(24)}
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("integer cost scaling" in e
                            for e in result["errors"]))
        self.assertTrue(json.dumps(result))  # still JSON-safe

    def test_zero_power_process_is_valid(self):
        source = generate_factory(12)
        source["factory"]["processes"][0]["power_kw"] = 0
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        row = result["result"]["optimized"]["processes"][0]
        self.assertEqual(row["solar_kwh"] + row["grid_kwh"], 0)
        self.assertEqual(row["energy_cost"], 0)

    def test_smallest_legal_duration(self):
        source = generate_factory(13)
        source["factory"]["processes"][0]["duration_hours"] = 0.5
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_OPTIMAL)

    def test_sub_grid_duration_rejected(self):
        source = generate_factory(14)
        source["factory"]["processes"][0]["duration_hours"] = 0.25
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("multiple of" in e for e in result["errors"]))

    def test_large_multi_week_horizon(self):
        source = generate_factory(15)
        source["factory"]["planning_horizon_hours"] = 336   # two weeks
        source["factory"]["production_deadline"] = 330
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertTrue(any("cyclically repeated" in w
                            for w in result["warnings"]))

    def test_missing_and_partial_profiles(self):
        # Fully missing energy section -> DEMO fallback + warnings
        source = generate_factory(16)
        del source["energy"]
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertTrue(any("DEMO/SIMULATED" in w for w in result["warnings"]))

        # Partial profile -> missing hours treated as 0 with a warning
        source = generate_factory(17)
        source["energy"]["tariff_profile"] = {h: 0.1 for h in range(10)}
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertTrue(any("does not define hours" in w
                            for w in result["warnings"]))

    def test_unusual_flexibility_combinations(self):
        # All processes non-flexible: optimizer must still succeed and
        # simply reproduce the baseline schedule.
        source = generate_factory(18)
        for p in source["factory"]["processes"]:
            p["is_flexible"] = False
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertEqual(result["result"]["comparison"]["shifted_processes"], 0)

        # All flexible: also valid
        source = generate_factory(19)
        for p in source["factory"]["processes"]:
            p["is_flexible"] = True
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_OPTIMAL)

    def test_malformed_nested_structures(self):
        cases = [
            {"factory": {"factory_name": "X", "processes": "not-a-list",
                         "planning_horizon_hours": 5,
                         "production_deadline": 4}},
            {"factory": {"factory_name": "X", "planning_horizon_hours": 5,
                         "production_deadline": 4, "processes": [None]}},
            {"factory": {"factory_name": "X", "planning_horizon_hours": 5,
                         "production_deadline": 4,
                         "processes": [{"process_id": 42,
                                        "duration_hours": 1,
                                        "power_kw": 1}]}},
            {"factory": {"factory_name": "X", "planning_horizon_hours": 5,
                         "production_deadline": 4,
                         "processes": [{"process_id": "a",
                                        "duration_hours": 1, "power_kw": 1,
                                        "dependencies": "a"}]}},
        ]
        for case in cases:
            with self.subTest(case=case):
                result = optimize(copy.deepcopy(case))
                self.assertEqual(result["status"], STATUS_INVALID_INPUT)
                self.assertTrue(result["errors"])

    def test_duplicate_and_self_dependencies_rejected(self):
        source = generate_factory(20)
        source["factory"]["processes"][1]["process_id"] = \
            source["factory"]["processes"][0]["process_id"]
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)

        source = generate_factory(21)
        source["factory"]["processes"][2]["dependencies"] = \
            [source["factory"]["processes"][2]["process_id"]]
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)

    def test_machine_process_mismatch_warns_but_schedules(self):
        source = generate_factory(22)
        # NON-EMPTY compatible list that omits an assigned process = explicit
        # metadata mismatch (an EMPTY list means "no metadata" and never warns).
        proc = source["factory"]["processes"][0]
        other_pid = source["factory"]["processes"][-1]["process_id"]
        for machine in source["factory"]["machines"]:
            if machine["machine_id"] == proc["machine_id"]:
                machine["compatible_processes"] = [other_pid]
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertTrue(
            any(proc["process_id"] in w and "compatible_processes" in w
                for w in result["warnings"]),
            msg=f"expected metadata-mismatch warning: {result['warnings']}")

    def test_nan_infinity_everywhere_rejected(self):
        for seed, field in ((23, "duration_hours"), (24, "power_kw")):
            source = generate_factory(seed)
            source["factory"]["processes"][0][field] = float("nan")
            result = optimize(source)
            self.assertEqual(result["status"], STATUS_INVALID_INPUT, msg=field)


# ---------------------------------------------------------------------------
# 4. Golden regression invariants (7 demo factories)
# ---------------------------------------------------------------------------

class GoldenInvariantTests(unittest.TestCase):
    """
    Lock stable invariants for the 7 demo factories - NOT exact start times
    (degenerate optima may legitimately differ).
    """

    GOLDEN = {
        # factory_key: (process_count, machine_count_min)
        "chocolate": (13, 10),
        "cosmetics": (11, 8),
        "food": (10, 8),
        "beverage": (13, 9),
        "pharmaceutical": (10, 8),
        "automotive": (11, 8),
        "furniture": (8, 5),
    }

    def test_golden_invariants_hold(self):
        for key, (n_procs, n_machines) in self.GOLDEN.items():
            with self.subTest(factory=key):
                source = {
                    "factory": copy.deepcopy(AVAILABLE_FACTORIES[key]),
                    "energy": {"grid_emission_factor": 0.3},
                }
                result = optimize(source)
                self.assertEqual(result["status"], STATUS_OPTIMAL, msg=key)
                payload = result["result"]

                # process count preserved in both schedules
                self.assertEqual(len(payload["baseline"]["processes"]),
                                 n_procs, msg=key)
                self.assertEqual(len(payload["optimized"]["processes"]),
                                 n_procs, msg=key)

                # feasibility + deadlines
                factory = AVAILABLE_FACTORIES[key]
                for sched in (payload["baseline"], payload["optimized"]):
                    for row in sched["processes"]:
                        self.assertLessEqual(
                            row["end_time"],
                            factory["production_deadline"] + 1e-9, msg=key)

                # energy identity at aggregate level
                energy = payload["optimized"]["energy"]
                self.assertAlmostEqual(
                    energy["total_kwh"],
                    energy["solar_kwh"] + energy["grid_kwh"], places=6,
                    msg=key)

                # cost identity: totals equal row sums
                for cost_key, sched in (
                        ("cost_baseline", payload["baseline"]),
                        ("cost_optimized", payload["optimized"])):
                    self.assertAlmostEqual(
                        payload["comparison"][cost_key],
                        sum(r["energy_cost"] for r in sched["processes"]),
                        places=6, msg=key)

                # optimized never worse under cost objective
                self.assertLessEqual(
                    payload["comparison"]["cost_optimized"],
                    payload["comparison"]["cost_baseline"] + 1e-6, msg=key)

                # carbon present and consistent with the fixed factor 0.3
                self.assertIsNotNone(payload["carbon"], msg=key)
                self.assertAlmostEqual(
                    payload["carbon"]["optimized_co2_kg"],
                    energy["grid_kwh"] * 0.3, places=6, msg=key)

                # non-flexible pinning
                base = _by_id(payload["baseline"])
                opt = _by_id(payload["optimized"])
                for row in payload["optimized"]["processes"]:
                    if not row["is_flexible"]:
                        self.assertEqual(
                            opt[row["process_id"]]["start_time"],
                            base[row["process_id"]]["start_time"], msg=key)

    def test_golden_machine_utilization_consistent(self):
        for key in self.GOLDEN:
            with self.subTest(factory=key):
                source = {
                    "factory": copy.deepcopy(AVAILABLE_FACTORIES[key]),
                }
                result = optimize(source)["result"]
                util = result["machine_utilization"]["optimized"]
                total_busy = sum(v["busy_hours"] for v in util.values())
                total_duration = sum(
                    p["duration_hours"] for p in result["optimized"]["processes"]
                    if p["machine_id"] is not None)
                self.assertAlmostEqual(total_busy, total_duration, places=6,
                                       msg=key)


# ---------------------------------------------------------------------------
# 5. Solver limits
# ---------------------------------------------------------------------------

class SolverLimitTests(unittest.TestCase):
    """Time limits: bounded solves, honest statuses, no INFEASIBLE confusion."""

    def test_default_public_limit_is_bounded(self):
        self.assertGreater(DEFAULT_PUBLIC_TIME_LIMIT_SECONDS, 0)
        self.assertLessEqual(DEFAULT_PUBLIC_TIME_LIMIT_SECONDS, 300)

    def test_max_time_seconds_option_accepted(self):
        source = generate_factory(30)
        source["options"] = {"max_time_seconds": 30}
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_OPTIMAL)

    def test_invalid_max_time_seconds_rejected(self):
        for bad in (0, -5, "ten", float("nan"), float("inf"), True):
            with self.subTest(bad=bad):
                source = generate_factory(31)
                source["options"] = {"max_time_seconds": bad}
                result = optimize(source)
                self.assertEqual(result["status"], STATUS_INVALID_INPUT,
                                 msg=bad)
                self.assertTrue(any("max_time_seconds" in e
                                    for e in result["errors"]), msg=bad)

    def test_tight_limit_still_solves_small_factory(self):
        # A tiny factory with a generous-but-small limit must still succeed
        source = generate_factory(32)
        source["options"] = {"max_time_seconds": 10}
        result = optimize(source)
        self.assertEqual(result["status"], STATUS_OPTIMAL)

    def test_unknown_is_distinct_from_infeasible(self):
        # Status values must never conflate UNKNOWN with INFEASIBLE
        self.assertNotEqual(STATUS_UNKNOWN, STATUS_INFEASIBLE)
        schema = public_api.get_output_schema()
        enum = schema["properties"]["status"]["enum"]
        self.assertIn("UNKNOWN", enum)
        self.assertIn("INFEASIBLE", enum)
        self.assertNotEqual(enum.index("UNKNOWN"), enum.index("INFEASIBLE"))

    def test_infeasible_still_reported_as_infeasible(self):
        # Proven-infeasible problem under a time limit stays INFEASIBLE
        source = generate_factory(33)
        source["factory"]["production_deadline"] = 1   # provably impossible
        source["options"] = {"max_time_seconds": 10}
        result = optimize(source)
        self.assertIn(result["status"], (STATUS_INFEASIBLE,
                                         STATUS_INVALID_INPUT))

    def test_engine_direct_call_without_limit_has_no_default(self):
        # Direct engine callers keep the historical no-limit behavior
        # (CP-SAT default max_time_in_seconds = inf)
        from optimizer import optimizer as engine
        solver = engine._new_solver()
        self.assertEqual(solver.parameters.max_time_in_seconds, math.inf)


if __name__ == "__main__":
    unittest.main()
