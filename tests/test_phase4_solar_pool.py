"""
ByteMe Phase 4.3 Tests: Shared Solar Pool (DEMO/SIMULATED DATA ONLY)

Solar is a physically shared half-hour-slot resource. For every slot:

    sum(allocated_solar[p, slot] for all running processes p)
        <= solar_profile[hour] * slot_duration

A process never receives more solar than its own power demand over the
part of the slot it occupies, and never draws from a time it was not
running. The pool lives INSIDE the CP-SAT model (run[p, slot] booleans
linked to start times, integer share variables, per-slot pool constraint),
so both the cost and the solar objective optimize against physically
available solar - there is no post-solve cosmetic cap.
"""

import json
import unittest

from ortools.sat.python import cp_model

from optimizer.energy_data import DEMO_SOLAR_PROFILE, DEMO_TARIFF_PROFILE
from optimizer.optimizer import (
    _add_shared_solar_pool,
    _build_base_model,
    allocate_solar_greedy,
    load_factory_config,
)
from optimizer.public_api import (
    STATUS_INFEASIBLE,
    STATUS_INVALID_INPUT,
    STATUS_OPTIMAL,
    optimize,
)

NO_SOLAR = {h: 0 for h in range(24)}
FLAT_TARIFF = {h: 0.5 for h in range(24)}
DEMO_TARIFF_DAY = {h: (0.10 if 9 <= h <= 15 else 0.50) for h in range(24)}


class LinearExpressionAggregationEquivalence(unittest.TestCase):
    def test_linear_expr_sum_matches_python_sum_proto(self):
        def build_model(use_linear_sum):
            model = cp_model.CpModel()
            variables = [model.NewIntVar(0, 1, f"x{index}")
                         for index in range(12)]
            terms = [(index % 4 + 1) * variable
                     for index, variable in enumerate(variables)]
            aggregate = (cp_model.LinearExpr.sum(terms) if use_linear_sum
                         else sum(terms, 0))
            total = model.NewIntVar(0, 40, "total")
            empty_aggregate = (
                cp_model.LinearExpr.sum([]) if use_linear_sum else sum([], 0)
            )
            model.Add(total <= aggregate)
            model.Add(total == empty_aggregate)
            return str(model.Proto())

        self.assertEqual(build_model(True), build_model(False))


def pool_factory(processes, machines, horizon=24, deadline=20,
                 solar_profile=None, tariff_profile=None):
    solar = solar_profile if solar_profile is not None else NO_SOLAR
    tariff = tariff_profile if tariff_profile is not None else FLAT_TARIFF
    return {
        "factory": {
            "factory_name": "Solar Pool Probe",
            "planning_horizon_hours": horizon,
            "production_deadline": deadline,
            "processes": processes,
            "machines": machines,
        },
        "energy": {
            "solar_profile": solar,
            "tariff_profile": tariff,
        },
    }


def two_machines_two_processes(power=10, duration=1):
    """Two processes, one per machine - they can run concurrently."""
    processes = [
        {"process_id": "p1", "process_name": "P1",
         "duration_hours": duration, "power_kw": power,
         "dependencies": [], "is_flexible": True, "machine_id": "m1"},
        {"process_id": "p2", "process_name": "P2",
         "duration_hours": duration, "power_kw": power,
         "dependencies": [], "is_flexible": True, "machine_id": "m2"},
    ]
    machines = [
        {"machine_id": "m1", "machine_name": "M1", "capacity": 1,
         "availability": "single unit",
         "compatible_processes": ["p1"]},
        {"machine_id": "m2", "machine_name": "M2", "capacity": 1,
         "availability": "single unit",
         "compatible_processes": ["p2"]},
    ]
    return processes, machines


def rows_by_id(result, key="optimized"):
    return {
        p["process_id"]: p
        for p in result["result"][key]["processes"]
    }


def _window(row):
    return row["power_kw"] * (row["end_time"] - row["start_time"])


def recompute_pool_costs(rows, solar_profile, tariff_profile):
    """
    Independent shared-pool recomputation of per-row energy and cost.
    Deterministic greedy split of each slot pool in process_id order -
    the documented reporting rule.
    """
    running = {}
    for row in sorted(rows, key=lambda r: r["process_id"]):
        t = row["start_time"]
        while t < row["end_time"]:
            slot = int(t * 2)
            overlap = min((slot + 1) / 2, row["end_time"]) - t
            running.setdefault(slot, []).append(
                (row["process_id"], row["power_kw"] * overlap, overlap)
            )
            t += overlap

    solar_by_pid = {}
    grid_by_pid = {}
    cost_by_pid = {}
    for row in rows:
        solar_by_pid[row["process_id"]] = 0.0
        grid_by_pid[row["process_id"]] = 0.0
        cost_by_pid[row["process_id"]] = 0.0

    for slot, runners in running.items():
        hour = slot // 2
        supply = solar_profile.get(hour % 24, 0) * 0.5
        for pid, demand, overlap in sorted(runners):
            draw_cap = solar_profile.get(hour % 24, 0) * overlap
            solar = min(demand, draw_cap, max(supply, 0.0))
            supply -= solar
            grid = demand - solar
            solar_by_pid[pid] += solar
            grid_by_pid[pid] += grid
            cost_by_pid[pid] += grid * tariff_profile.get(hour % 24, 0)
    return solar_by_pid, grid_by_pid, cost_by_pid


class ReportingNeverOverallocates(unittest.TestCase):
    """The greedy allocator must respect the slot pool + draw cap."""

    def test_allocator_partial_hour_no_longer_gets_whole_hour_supply(self):
        # 10 kW process for 0.5 h inside a 4 kW-solar hour: the OLD bug gave
        # it the full 4 kWh; physically it can draw at most 2 kWh.
        rows = [{"process_id": "p", "start_time": 9.0, "end_time": 9.5,
                 "power_kw": 10}]
        alloc = allocate_solar_greedy(rows, {h: 4 for h in range(24)})
        self.assertAlmostEqual(alloc["p"]["solar_kwh"], 2.0, places=9)

    def test_allocator_respects_hourly_pool_across_processes(self):
        rows = [
            {"process_id": "a", "start_time": 9.0, "end_time": 10.0,
             "power_kw": 50},
            {"process_id": "b", "start_time": 9.0, "end_time": 10.0,
             "power_kw": 50},
        ]
        alloc = allocate_solar_greedy(rows, {h: 20 for h in range(24)})
        self.assertAlmostEqual(alloc["a"]["solar_kwh"], 20.0, places=9)
        self.assertAlmostEqual(alloc["b"]["solar_kwh"], 0.0, places=9)

    def test_allocator_shares_solar_within_the_same_half_hour(self):
        rows = [
            {"process_id": "a", "start_time": 9.0, "end_time": 9.5,
             "power_kw": 10},
            {"process_id": "b", "start_time": 9.0, "end_time": 9.5,
             "power_kw": 10},
        ]
        alloc = allocate_solar_greedy(
            rows, {h: 10 for h in range(24)}
        )
        self.assertAlmostEqual(
            sum(row["solar_kwh"] for row in alloc.values()), 5.0, places=9
        )

    def test_allocator_zero_solar_hour(self):
        rows = [{"process_id": "a", "start_time": 3.0, "end_time": 4.0,
                 "power_kw": 10}]
        alloc = allocate_solar_greedy(rows, NO_SOLAR)
        self.assertAlmostEqual(alloc["a"]["solar_kwh"], 0.0, places=9)
        self.assertAlmostEqual(alloc["a"]["grid_kwh"], 10.0, places=9)


class SharedPoolInsideModel(unittest.TestCase):
    """The pool is a constraint of the optimization, not a cosmetic cap."""

    def test_concurrent_half_hour_processes_share_one_slot_pool(self):
        processes, machines = two_machines_two_processes(
            power=10, duration=0.5
        )
        for process in processes:
            process["is_flexible"] = False
        data = pool_factory(
            processes, machines, horizon=2, deadline=2,
            solar_profile={h: 10 for h in range(24)},
            tariff_profile={h: 0.5 for h in range(24)},
        )

        result = optimize(data, objective="solar")
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        optimized = result["result"]["optimized"]
        self.assertEqual(
            {row["start_time"] for row in optimized["processes"]}, {0.0}
        )
        self.assertAlmostEqual(optimized["energy"]["solar_kwh"], 5.0)
        self.assertAlmostEqual(optimized["energy"]["grid_kwh"], 5.0)

    def test_concurrent_processes_cannot_double_claim_solar(self):
        # Two 10 kW processes, 1 h each, can run concurrently on two
        # machines. Supply is 10 kW in every hour: combined draw can never
        # exceed 10 kWh in any hour, no matter where they run.
        processes, machines = two_machines_two_processes(power=10)
        data = pool_factory(processes, machines,
                            solar_profile={h: 10 for h in range(24)},
                            tariff_profile={h: 0.10 for h in range(24)})
        result = optimize(data, objective="solar")
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        rows = list(rows_by_id(result).values())
        # Total demand is 20 kWh; with a 10 kW pool in every hour the
        # optimizer can reach all of it by staggering the two processes
        # across sunny hours - the objective must find that.
        self.assertAlmostEqual(sum(r["solar_kwh"] for r in rows),
                               20.0, places=6)
        # Pool invariant: every hourly total stays within physical supply.
        per_hour = {}
        for row in rows:
            t = row["start_time"]
            while t < row["end_time"]:
                hour = int(t)
                overlap = min(hour + 1, row["end_time"]) - t
                energy = row["power_kw"] * overlap
                # attribute solar pro rata to the hour it was drawn in
                share = row["solar_kwh"] * (
                    (row["power_kw"] * overlap) / _window(row))
                per_hour[hour] = per_hour.get(hour, 0.0) + share
                t += overlap
        for hour, total in per_hour.items():
            self.assertLessEqual(
                total, 10.0 + 1e-9,
                msg=f"hour {hour}: solar over-committed ({total} kWh)")

    def test_capacity_two_triple_share_same_pool(self):
        # One machine, capacity 3: three 10 kW processes can run
        # simultaneously. Supply 20 kW -> combined solar <= 20 kWh per hour.
        processes = [
            {"process_id": f"p{i}", "process_name": f"P{i}",
             "duration_hours": 2, "power_kw": 10, "dependencies": [],
             "is_flexible": True, "machine_id": "shared"}
            for i in range(3)
        ]
        machines = [{
            "machine_id": "shared", "machine_name": "Shared",
            "capacity": 3, "availability": "capacity 3",
            "compatible_processes": ["p0", "p1", "p2"],
        }]
        data = pool_factory(processes, machines,
                            solar_profile={h: 20 for h in range(24)},
                            tariff_profile={h: 0.10 for h in range(24)})
        result = optimize(data, objective="solar")
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        rows = list(rows_by_id(result).values())
        # Total demand is 3 x 2 h x 10 kW = 60 kWh; the 20 kW profile can
        # cover all of it if runs are spread across enough half-hour slots.
        # The objective must maximize allocation up to exactly that.
        self.assertAlmostEqual(sum(r["solar_kwh"] for r in rows),
                               60.0, places=6)
        # ...and no hour may exceed the physical 20 kWh across its two slots.
        per_hour = {}
        for row in rows:
            t = row["start_time"]
            while t < row["end_time"]:
                hour = int(t)
                overlap = min(hour + 1, row["end_time"]) - t
                share = row["solar_kwh"] * (
                    (row["power_kw"] * overlap) / _window(row))
                per_hour[hour] = per_hour.get(hour, 0.0) + share
                t += overlap
        for hour, total in per_hour.items():
            self.assertLessEqual(total, 20.0 + 1e-9,
                                 msg=f"hour {hour} over the pool")

    def test_energy_identity_holds_under_pool(self):
        processes, machines = two_machines_two_processes(power=10)
        data = pool_factory(processes, machines,
                            solar_profile={h: (8 if 9 <= h <= 15 else 0)
                                           for h in range(24)},
                            tariff_profile=DEMO_TARIFF_DAY)
        for objective in ("cost", "solar"):
            with self.subTest(objective=objective):
                result = optimize(data, objective=objective)
                self.assertEqual(result["status"], STATUS_OPTIMAL)
                for key in ("baseline", "optimized"):
                    for row in result["result"][key]["processes"]:
                        total = row["power_kw"] * (
                            row["end_time"] - row["start_time"])
                        self.assertAlmostEqual(
                            row["solar_kwh"] + row["grid_kwh"], total,
                            places=6, msg=f"{key} {row['process_id']}")
                        self.assertGreaterEqual(row["solar_kwh"], 0)
                        self.assertGreaterEqual(row["grid_kwh"], 0)
                    energy = result["result"][key]["energy"]
                    self.assertAlmostEqual(
                        energy["solar_kwh"] + energy["grid_kwh"],
                        energy["total_kwh"], places=6)

    def test_costs_recomputable_under_pool(self):
        processes, machines = two_machines_two_processes(power=10)
        data = pool_factory(processes, machines,
                            solar_profile={h: (8 if 9 <= h <= 15 else 0)
                                           for h in range(24)},
                            tariff_profile=DEMO_TARIFF_DAY)
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        for key in ("baseline", "optimized"):
            rows = result["result"][key]["processes"]
            solar, grid, cost = recompute_pool_costs(
                rows, data["energy"]["solar_profile"],
                data["energy"]["tariff_profile"])
            for row in rows:
                self.assertAlmostEqual(
                    row["solar_kwh"], solar[row["process_id"]], places=6,
                    msg=f"{key} solar {row['process_id']}")
                self.assertAlmostEqual(
                    row["grid_kwh"], grid[row["process_id"]], places=6,
                    msg=f"{key} grid {row['process_id']}")
                self.assertAlmostEqual(
                    row["energy_cost"], cost[row["process_id"]], places=6,
                    msg=f"{key} cost {row['process_id']}")


class CostObjectiveOptimizesPool(unittest.TestCase):
    """Cost optimization truly minimizes grid cost under the shared pool."""

    def test_shifts_all_processes_into_solar_hours(self):
        # Expensive night, free-grid sunny day: both processes fit fully
        # inside the solar window concurrently, so grid cost must be 0.
        processes, machines = two_machines_two_processes(power=10)
        data = pool_factory(processes, machines,
                            solar_profile={h: 10 if 9 <= h <= 15 else 0
                                           for h in range(24)},
                            tariff_profile=DEMO_TARIFF_DAY)
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertAlmostEqual(
            result["result"]["comparison"]["cost_optimized"], 0.0, places=6)
        self.assertGreater(
            result["result"]["comparison"]["cost_baseline"], 0.0)

    def test_pool_contention_optimal_split(self):
        # Decisive pool test: solar 10 kW in hours 9 AND 10 only, two
        # 10 kW x 1 h processes on separate machines, flat tariff.
        # OLD per-process model: running both at 9 claims 20 kWh solar
        # (cost 0) - a fiction the pool forbids (only 10 kWh exists there).
        # NEW shared-pool model: the optimizer must SPREAD the runs across
        # the two solar hours so each draws its own hour's 10 kWh pool:
        # cost 0 with a true 20 kWh of solar. The old model cannot express
        # this difference - it would report cost 0 for the clustered run.
        processes, machines = two_machines_two_processes(power=10)
        solar = {h: (10 if h in (9, 10) else 0) for h in range(24)}
        data = pool_factory(processes, machines,
                            solar_profile=solar,
                            tariff_profile=FLAT_TARIFF)
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        rows = list(rows_by_id(result).values())
        starts = {r["process_id"]: r["start_time"] for r in rows}
        self.assertGreaterEqual(min(starts.values()), 9)
        self.assertLessEqual(max(starts.values()) + 1, 11)
        # Both fully solar - only possible when they use different hours.
        self.assertAlmostEqual(sum(r["solar_kwh"] for r in rows),
                               20.0, places=6)
        self.assertAlmostEqual(sum(r["grid_kwh"] for r in rows), 0.0,
                               places=6)
        self.assertAlmostEqual(
            result["result"]["comparison"]["cost_optimized"], 0.0, places=6)

    def test_optimized_cost_never_exceeds_baseline(self):
        processes, machines = two_machines_two_processes(power=10)
        data = pool_factory(processes, machines,
                            solar_profile=DEMO_SOLAR_PROFILE,
                            tariff_profile=DEMO_TARIFF_PROFILE)
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertLessEqual(
            result["result"]["comparison"]["cost_optimized"],
            result["result"]["comparison"]["cost_baseline"] + 1e-6)


class SolarObjectiveMaximizesPool(unittest.TestCase):
    """Solar optimization truly maximizes allocated solar."""

    def test_maximizes_allocatable_solar(self):
        # 30 kW supply, two 10 kW processes: at most 20 kWh solar exists
        # for them per hour; the objective must achieve exactly that.
        processes, machines = two_machines_two_processes(power=10)
        data = pool_factory(processes, machines,
                            solar_profile={h: 30 for h in range(24)},
                            tariff_profile=FLAT_TARIFF)
        result = optimize(data, objective="solar")
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        rows = list(rows_by_id(result).values())
        self.assertAlmostEqual(sum(r["solar_kwh"] for r in rows),
                               20.0, places=6)

    def test_solar_objective_beats_naive_baseline(self):
        processes, machines = two_machines_two_processes(power=10)
        data = pool_factory(processes, machines,
                            solar_profile={h: 20 if h <= 11 else 0
                                           for h in range(24)},
                            tariff_profile=FLAT_TARIFF)
        result = optimize(data, objective="solar")
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        baseline_solar = result["result"]["baseline"]["energy"]["solar_kwh"]
        optimized_solar = result["result"]["optimized"]["energy"]["solar_kwh"]
        self.assertGreaterEqual(optimized_solar, baseline_solar - 1e-9)


class RobustnessAndDeterminism(unittest.TestCase):
    """Factories, determinism, and error handling stay intact."""

    def test_all_registered_factories_still_valid_both_objectives(self):
        from optimizer.factory_data import AVAILABLE_FACTORIES
        for key in AVAILABLE_FACTORIES:
            with self.subTest(factory=key):
                data = {
                    "factory": AVAILABLE_FACTORIES[key],
                    "energy": {
                        "solar_profile": DEMO_SOLAR_PROFILE,
                        "tariff_profile": DEMO_TARIFF_PROFILE,
                    },
                }
                for objective in ("cost", "solar"):
                    result = optimize(data, objective=objective)
                    self.assertEqual(result["status"], STATUS_OPTIMAL,
                                     msg=f"{key}/{objective}")
                    for row in result["result"]["optimized"]["processes"]:
                        self.assertGreaterEqual(row["solar_kwh"], -1e-9)

    def test_deterministic_repeated_runs_identical(self):
        processes, machines = two_machines_two_processes(power=10)
        data = pool_factory(processes, machines,
                            solar_profile={h: (8 if 9 <= h <= 15 else 0)
                                           for h in range(24)},
                            tariff_profile=DEMO_TARIFF_DAY)
        res_a = optimize(data)
        res_b = optimize(data)
        # solve_time_seconds is a measured wall-clock value that naturally
        # varies between runs; strip it before determinism comparison.
        def _strip(obj):
            if isinstance(obj, dict):
                return {k: _strip(v) for k, v in obj.items()
                        if k != "solve_time_seconds"}
            if isinstance(obj, list):
                return [_strip(item) for item in obj]
            return obj
        text_a = json.dumps(_strip(res_a), sort_keys=True)
        text_b = json.dumps(_strip(res_b), sort_keys=True)
        self.assertEqual(text_a, text_b)

    def test_infeasible_and_invalid_untouched(self):
        processes, machines = two_machines_two_processes()
        # Two 1 h processes serialized on ONE capacity-1 machine cannot
        # both finish by hour 1 -> INFEASIBLE.
        bad = pool_factory(processes, machines, horizon=2, deadline=1)
        bad["factory"]["machines"] = [bad["factory"]["machines"][0]]
        bad["factory"]["machines"][0]["compatible_processes"] = ["p1", "p2"]
        bad["factory"]["processes"][1]["machine_id"] = "m1"
        self.assertEqual(optimize(bad)["status"], STATUS_INFEASIBLE)
        invalid = pool_factory(processes, machines)
        invalid["factory"]["processes"][0]["power_kw"] = -5
        self.assertEqual(optimize(invalid)["status"], STATUS_INVALID_INPUT)


class DomainPruningEquivalenceTests(unittest.TestCase):
    def setUp(self):
        processes = [
            {"process_id": "root", "duration_hours": 0.5,
             "power_kw": 0.4, "dependencies": [], "is_flexible": True,
             "machine_id": "shared"},
            {"process_id": "branch_a", "duration_hours": 1,
             "power_kw": 0.6, "dependencies": ["root"],
             "is_flexible": True, "machine_id": "shared"},
            {"process_id": "branch_b", "duration_hours": 0.5,
             "power_kw": 0.2, "dependencies": ["root"],
             "is_flexible": True, "machine_id": "other",
             "earliest_start": 0.75, "latest_finish": 3.75},
            {"process_id": "parallel", "duration_hours": 0.5,
             "power_kw": 0.2, "dependencies": [], "is_flexible": True,
             "machine_id": "shared"},
        ]
        machines = [
            {"machine_id": "shared", "capacity": 2,
             "compatible_processes": ["root", "branch_a", "parallel"]},
            {"machine_id": "other", "capacity": 1,
             "compatible_processes": ["branch_b"]},
        ]
        self.data = pool_factory(
            processes,
            machines,
            horizon=4,
            deadline=4,
            solar_profile={h: (0.4 if h in (1, 2) else 0.2)
                           for h in range(24)},
            tariff_profile={h: (0.01 if h == 0 else 0.005)
                            for h in range(24)},
        )

    def _enumerate_start_tuples(self, prune_domains):
        config = load_factory_config(self.data["factory"])
        model, starts, _ = _build_base_model(
            config, prune_domains=prune_domains
        )
        process_ids = config.process_ids
        schedules = set()

        class Collector(cp_model.CpSolverSolutionCallback):
            def on_solution_callback(callback_self):
                schedules.add(tuple(
                    callback_self.Value(starts[process_id])
                    for process_id in process_ids
                ))

        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        solver.parameters.enumerate_all_solutions = True
        self.assertEqual(solver.Solve(model, Collector()), cp_model.OPTIMAL)
        return schedules

    def _metrics_for(self, start_ticks):
        factory = self.data["factory"]
        rows = []
        for process, start_tick in zip(factory["processes"], start_ticks):
            start = start_tick / 2
            rows.append({
                "process_id": process["process_id"],
                "start_time": start,
                "end_time": start + process["duration_hours"],
                "power_kw": process["power_kw"],
            })
        solar_by_id, _, cost_by_id = recompute_pool_costs(
            rows,
            self.data["energy"]["solar_profile"],
            self.data["energy"]["tariff_profile"],
        )
        makespan_ticks = max(round(row["end_time"] * 2) for row in rows)
        solar_units = round(sum(solar_by_id.values()) * 10)
        cost_units = round(sum(cost_by_id.values()) * 10_000)
        return makespan_ticks, solar_units, cost_units

    def _boolean_count(self, prune_domains):
        config = load_factory_config(self.data["factory"])
        model, starts, _ = _build_base_model(
            config, prune_domains=prune_domains
        )
        _add_shared_solar_pool(
            config, model, starts, self.data["energy"]["solar_profile"],
            prune_domains=prune_domains,
        )
        return sum(list(variable.domain) == [0, 1]
                   for variable in model.Proto().variables)

    def test_pruned_feasible_schedules_and_objective_optima_are_equivalent(self):
        unpruned = self._enumerate_start_tuples(prune_domains=False)
        pruned = self._enumerate_start_tuples(prune_domains=True)
        self.assertEqual(pruned, unpruned)
        self.assertGreater(len(pruned), 0)
        self.assertLess(self._boolean_count(True), self._boolean_count(False))

        metrics = [self._metrics_for(schedule) for schedule in pruned]
        best_makespan = min(item[0] for item in metrics)
        best_solar = max(item[1] for item in metrics)
        best_solar_makespan = min(
            item[0] for item in metrics if item[1] == best_solar
        )
        best_cost = min(item[2] for item in metrics)
        best_cost_makespan = min(
            item[0] for item in metrics if item[2] == best_cost
        )

        baseline = optimize(self.data, objective="cost")["result"]["baseline"]
        cost_result = optimize(self.data, objective="cost")["result"]
        solar_result = optimize(self.data, objective="solar")["result"]
        self.assertEqual(round(baseline["makespan_hours"] * 2), best_makespan)
        self.assertAlmostEqual(
            cost_result["comparison"]["cost_optimized"], best_cost / 10_000
        )
        self.assertEqual(
            round(cost_result["optimized"]["makespan_hours"] * 2),
            best_cost_makespan,
        )
        self.assertAlmostEqual(
            solar_result["optimized"]["energy"]["solar_kwh"], best_solar / 10
        )
        self.assertEqual(
            round(solar_result["optimized"]["makespan_hours"] * 2),
            best_solar_makespan,
        )

    def test_pinned_process_uses_one_start_boolean(self):
        data = json.loads(json.dumps(self.data))
        data["factory"]["processes"][0]["is_flexible"] = False
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        optimized = result["result"]["optimized"]
        root_start = next(row["start_time"] for row in optimized["processes"]
                          if row["process_id"] == "root")

        config = load_factory_config(data["factory"])
        model, starts, _ = _build_base_model(config)
        baseline = result["result"]["baseline"]
        baseline_times = {
            row["process_id"]: int(round(row["start_time"] * 2))
            for row in baseline["processes"]
        }
        fixed = {"root": baseline_times["root"]}
        model.Add(starts["root"] == fixed["root"])
        _add_shared_solar_pool(
            config, model, starts, data["energy"]["solar_profile"],
            fixed_start_times=fixed,
        )
        run_names = [variable.name for variable in model.Proto().variables
                     if variable.name.startswith("run_root_")]
        self.assertEqual(len(run_names), 1)
        self.assertEqual(root_start * 2, fixed["root"])


class UnreachableHourSolarPinning(unittest.TestCase):
    """Hours no process can reach must never credit phantom solar.

    Regression: the per-hour pool cap constraint was skipped whenever no
    process could draw in that hour (e.g. hours between the production
    deadline and the planning horizon). used[hour] was then free up to the
    physical pool and the objective claimed solar nobody could draw, so
    the in-model objective no longer matched the reported schedule
    metrics for inputs with a sunny deadline gap.
    """

    def _factory(self):
        return pool_factory(
            [{"process_id": "solo", "process_name": "Solo",
              "duration_hours": 1, "power_kw": 10,
              "dependencies": [], "is_flexible": True}],
            [],
            horizon=12, deadline=10,
            solar_profile={h: (5.0 if h >= 10 else 0.0) for h in range(24)},
            tariff_profile={h: 0.5 for h in range(24)},
        )

    def test_unreachable_sunny_hours_are_pinned_to_zero(self):
        data = self._factory()
        config = load_factory_config(data["factory"])
        model, starts, _ = _build_base_model(config)
        share, demand = _add_shared_solar_pool(
            config, model, starts, data["energy"]["solar_profile"],
            fixed_start_times={},
        )
        domains = {variable.name: list(variable.domain)
                   for variable in model.Proto().variables}
        for slot in (20, 21, 22, 23):
            self.assertEqual(domains[f"solar_used_{slot}"], [0, 0])

    def test_cost_objective_matches_reported_metrics_with_sunny_gap(self):
        data = self._factory()
        result = optimize(data, objective="cost")
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        optimized = result["result"]["optimized"]
        rows = [{"process_id": row["process_id"],
                 "start_time": row["start_time"],
                 "end_time": row["end_time"],
                 "power_kw": row["power_kw"]}
                for row in optimized["processes"]]
        solar_by_id, grid_by_id, cost_by_id = recompute_pool_costs(
            rows, data["energy"]["solar_profile"],
            data["energy"]["tariff_profile"])
        reported_cost = sum(row["energy_cost"]
                            for row in optimized["processes"])
        self.assertAlmostEqual(reported_cost, sum(cost_by_id.values()))
        # True optimum: the solo process pays full grid price for 10 kWh
        # (cost 5.0); it cannot run in the sunny hours 10-11, so zero
        # solar is creditable. Before the fix the model credited the
        # unreachable 5 kW pools as free solar.
        self.assertAlmostEqual(optimized["energy"]["grid_kwh"], 10.0)
        self.assertAlmostEqual(optimized["energy"]["solar_kwh"], 0.0)

    def test_solar_objective_claims_no_unreachable_solar(self):
        data = self._factory()
        result = optimize(data, objective="solar")
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        optimized = result["result"]["optimized"]
        self.assertAlmostEqual(optimized["energy"]["solar_kwh"], 0.0)


if __name__ == "__main__":
    unittest.main()
