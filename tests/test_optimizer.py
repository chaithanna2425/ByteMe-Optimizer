"""
ByteMe Six-Industry Validation Tests (DEMO/SIMULATED DATA ONLY)

Programmatic proof that ONE generic optimization engine works for all six
target industries (chocolate, cosmetics, food, beverage, pharmaceutical,
automotive) plus a non-industry factory, and that the engine contains no
factory-specific names or logic.

Optimization results use DEMO/SIMULATED factory and energy data only.
"""

import unittest

from optimizer import optimizer as opt
from optimizer.energy_data import DEMO_SOLAR_PROFILE, DEMO_TARIFF_PROFILE
from optimizer.factory_data import (
    AVAILABLE_FACTORIES,
    DEMO_CHOCOLATE_FACTORY,
    DEMO_FURNITURE_FACTORY,
    INDUSTRY_FACTORIES,
)
from optimizer.models import FactoryConfig, FactoryConfigError, validate_factory_config


def recompute_energy_cost(rows, solar_profile, tariff_profile):
    """
    Independent shared-pool recomputation (does NOT call optimizer helpers).

    Solar is a physically shared half-hour-slot resource: concurrent
    processes collectively draw at most slot-available solar. Split rule:
    greedy in process_id order.
    """
    running = {}
    for row in rows:
        t = row["start_time"]
        while t < row["end_time"]:
            slot = int(t * 2)
            dur = min((slot + 1) / 2, row["end_time"]) - t
            running.setdefault(slot, []).append(
                (row["process_id"], row["power_kw"] * dur, dur))
            t += dur

    result = {
        row["process_id"]: {"solar": 0.0, "grid": 0.0, "cost": 0.0}
        for row in rows
    }
    for slot, runners in running.items():
        hour = (slot // 2) % 24
        solar_kw = solar_profile.get(hour, 0)
        supply = solar_kw / 2
        for pid, energy, dur in sorted(runners):
            draw_cap = solar_kw * dur
            s = min(energy, draw_cap, max(supply, 0.0))
            supply -= s
            g = energy - s
            result[pid]["solar"] += s
            result[pid]["grid"] += g
            result[pid]["cost"] += g * tariff_profile.get(hour, 0)
    return result


def by_id(schedule):
    return {p["process_id"]: p for p in schedule["processes"]}


class AllFactoriesTests(unittest.TestCase):
    """
    The SAME battery of engine checks runs for EVERY factory configuration.
    Adding a seventh factory to AVAILABLE_FACTORIES automatically includes
    it here - no new test code and no engine changes required.
    """

    def test_registry_contains_six_industries(self):
        self.assertEqual(
            set(INDUSTRY_FACTORIES.keys()),
            {"chocolate", "cosmetics", "food", "beverage",
             "pharmaceutical", "automotive"},
        )
        self.assertGreaterEqual(len(AVAILABLE_FACTORIES), 7)

    def _for_each_factory(self):
        for key, data in AVAILABLE_FACTORIES.items():
            with self.subTest(factory=key):
                yield key, validate_factory_config(data)

    def test_all_configs_load_and_validate(self):
        for key, config in self._for_each_factory():
            self.assertIsInstance(config, FactoryConfig)
            self.assertGreaterEqual(len(config.processes), 1)

    def test_baseline_and_optimized_schedules_feasible_optimal(self):
        for key, config in self._for_each_factory():
            baseline = opt.create_baseline_schedule(config)
            optimized = opt.create_cost_optimized_schedule(config)
            self.assertIsNotNone(baseline, f"{key}: baseline infeasible")
            self.assertIsNotNone(optimized, f"{key}: optimized infeasible")
            self.assertEqual(baseline["status"], "OPTIMAL")
            self.assertEqual(optimized["status"], "OPTIMAL")

    def test_durations_respected(self):
        for key, config in self._for_each_factory():
            for schedule in (
                opt.create_baseline_schedule(config),
                opt.create_cost_optimized_schedule(config),
            ):
                rows = by_id(schedule)
                for spec in config.processes:
                    self.assertAlmostEqual(
                        rows[spec.process_id]["end_time"]
                        - rows[spec.process_id]["start_time"],
                        spec.duration_hours,
                        places=9,
                        msg=f"{key}: duration of {spec.process_id}",
                    )

    def test_dependencies_respected(self):
        for key, config in self._for_each_factory():
            for schedule in (
                opt.create_baseline_schedule(config),
                opt.create_cost_optimized_schedule(config),
            ):
                rows = by_id(schedule)
                for spec in config.processes:
                    for dep in spec.dependencies:
                        self.assertLessEqual(
                            rows[dep]["end_time"],
                            rows[spec.process_id]["start_time"] + 1e-9,
                            msg=f"{key}: {dep} -> {spec.process_id}",
                        )

    def test_deadlines_horizon_and_time_windows_respected(self):
        for key, config in self._for_each_factory():
            for schedule in (
                opt.create_baseline_schedule(config),
                opt.create_cost_optimized_schedule(config),
            ):
                rows = by_id(schedule)
                for spec in config.processes:
                    row = rows[spec.process_id]
                    self.assertGreaterEqual(row["start_time"], 0)
                    self.assertLessEqual(
                        row["end_time"],
                        config.production_deadline + 1e-9,
                        msg=f"{key}: deadline of {spec.process_id}",
                    )
                    self.assertLessEqual(
                        row["end_time"], config.planning_horizon_hours + 1e-9
                    )
                    if spec.earliest_start is not None:
                        self.assertGreaterEqual(
                            row["start_time"] + 1e-9, spec.earliest_start
                        )
                    if spec.latest_finish is not None:
                        self.assertLessEqual(
                            row["end_time"], spec.latest_finish + 1e-9
                        )

    def test_non_flexible_processes_remain_fixed(self):
        for key, config in self._for_each_factory():
            baseline = opt.create_baseline_schedule(config)
            optimized = opt.create_cost_optimized_schedule(config)
            base_rows, opt_rows = by_id(baseline), by_id(optimized)
            pinned = [p for p in config.processes if not p.is_flexible]
            self.assertGreater(
                len(pinned), 0, msg=f"{key}: expected at least one pinned process"
            )
            for spec in pinned:
                self.assertEqual(
                    opt_rows[spec.process_id]["start_time"],
                    base_rows[spec.process_id]["start_time"],
                    msg=f"{key}: pinning of {spec.process_id}",
                )

    def test_machine_resources_never_overlap(self):
        for key, config in self._for_each_factory():
            for schedule in (
                opt.create_baseline_schedule(config),
                opt.create_cost_optimized_schedule(config),
            ):
                rows = schedule["processes"]
                for machine_id, pids in config.processes_by_machine.items():
                    intervals = sorted(
                        (p["start_time"], p["end_time"])
                        for p in rows
                        if p["process_id"] in pids
                    )
                    for (s1, e1), (s2, _) in zip(intervals, intervals[1:]):
                        self.assertLessEqual(
                            e1, s2 + 1e-9,
                            msg=f"{key}: overlap on {machine_id}: "
                                f"[{s1}, {e1}] vs [{s2}]",
                        )

    def test_energy_calculations_and_identities(self):
        for key, config in self._for_each_factory():
            for schedule in (
                opt.create_baseline_schedule(config),
                opt.create_cost_optimized_schedule(config),
            ):
                recomputed = recompute_energy_cost(
                    schedule["processes"],
                    DEMO_SOLAR_PROFILE, DEMO_TARIFF_PROFILE,
                )
                for row in schedule["processes"]:
                    spec = config.get_process(row["process_id"])
                    got = recomputed[row["process_id"]]
                    total = spec.power_kw * spec.duration_hours
                    self.assertAlmostEqual(row["solar_energy_kwh"], got["solar"], places=6)
                    self.assertAlmostEqual(row["grid_energy_kwh"], got["grid"], places=6)
                    self.assertAlmostEqual(row["energy_cost"], got["cost"], places=6)
                    # solar + grid = total energy (power x duration)
                    self.assertAlmostEqual(
                        row["solar_energy_kwh"] + row["grid_energy_kwh"],
                        total, places=6,
                        msg=f"{key}: energy identity {spec.process_id}",
                    )
                    # no negative energy or cost
                    self.assertGreaterEqual(row["solar_energy_kwh"], 0)
                    self.assertGreaterEqual(row["grid_energy_kwh"], 0)
                    self.assertGreaterEqual(row["energy_cost"], 0)

    def test_optimized_cost_not_greater_than_baseline(self):
        for key, config in self._for_each_factory():
            baseline = opt.create_baseline_schedule(config)
            optimized = opt.create_cost_optimized_schedule(config)
            base_cost = sum(p["energy_cost"] for p in baseline["processes"])
            opt_cost = sum(p["energy_cost"] for p in optimized["processes"])
            self.assertLessEqual(
                opt_cost, base_cost + 1e-6,
                msg=f"{key}: optimized cost {opt_cost:.2f} > baseline {base_cost:.2f}",
            )

    def test_branching_merging_and_parallel_supported(self):
        # Configurations contain merge points (multiple dependencies) where
        # the representative flow has them. Pharmaceutical is deliberately
        # fully sequential batch manufacturing (strict dependencies), so it
        # has no merge; every other industry configuration does.
        for key, config in self._for_each_factory():
            has_merge = any(len(p.dependencies) >= 2 for p in config.processes)
            if key != "pharmaceutical":
                self.assertTrue(has_merge, msg=f"{key}: no merge point")
        pharma = validate_factory_config(AVAILABLE_FACTORIES["pharmaceutical"])
        self.assertTrue(
            all(len(p.dependencies) <= 1 for p in pharma.processes),
            msg="pharmaceutical flow should be strictly sequential",
        )

        # Genuine parallel execution (makespan < sum of durations) where the
        # structure allows it: chocolate (2 mills), food (2 wash lines),
        # automotive (2 CNC cells), furniture (independent kitting).
        # Beverage is excluded: its blend lines deliberately share ONE
        # blender (modeled machine conflict), which serializes them.
        parallel_factories = ("chocolate", "food", "automotive", "furniture")
        for key in parallel_factories:
            config = validate_factory_config(AVAILABLE_FACTORIES[key])
            baseline = opt.create_baseline_schedule(config)
            total_duration = sum(p.duration_hours for p in config.processes)
            self.assertLess(
                baseline["makespan"], total_duration - 1e-9,
                msg=f"{key}: schedule fully serialized; parallelism lost",
            )

        # Branch: chocolate grinding splits onto two different mills
        choco = validate_factory_config(DEMO_CHOCOLATE_FACTORY)
        branches = [p for p in choco.processes
                    if p.dependencies == ["winnowing"]]
        self.assertEqual(len(branches), 2)
        self.assertNotEqual(branches[0].machine_id, branches[1].machine_id)

        # Shared machines are modeled in at least five configurations
        shared_counts = {}
        for key in AVAILABLE_FACTORIES:
            cfg = validate_factory_config(AVAILABLE_FACTORIES[key])
            shared_counts[key] = sum(
                1 for pids in cfg.processes_by_machine.values() if len(pids) > 1
            )
        self.assertGreaterEqual(
            sum(1 for v in shared_counts.values() if v > 0), 5
        )


class GenericEngineTests(unittest.TestCase):
    """Prove the engine is completely factory-agnostic."""

    def test_engine_source_has_no_process_or_industry_names(self):
        import inspect
        source = inspect.getsource(opt).lower()
        forbidden = (
            # industries
            "chocolate", "cosmetic", "beverage", "pharma", "automotive",
            "furniture", "food",
            # chocolate processes
            "roasting", "winnowing", "grinding", "conching", "tempering",
            "moulding", "refining",
            # cosmetics / food / beverage processes
            "weighing", "emulsification", "homogenization", "filling",
            "pasteurization", "syrup", "capping",
            # pharma processes
            "sieving", "granulation", "tablet", "coating", "dispensing",
            # automotive processes
            "machining", "painting", "assembly",
        )
        for term in forbidden:
            self.assertNotIn(term, source,
                             msg=f"engine mentions factory-specific term {term!r}")

    def test_engine_has_no_factory_specific_conditionals(self):
        import inspect
        source = inspect.getsource(opt)
        self.assertNotRegex(
            source, r"if\s+factory\s*==", msg="factory equality check found"
        )
        self.assertNotRegex(
            source, r"if\s+process_id\s*==", msg="process equality check found"
        )

    def test_seventh_industry_possible_without_engine_changes(self):
        # A brand-new non-industry factory (proof of genericity): unknown
        # names, unknown structure, machine contention - engine just works.
        novel = {
            "factory_name": "Demo Widget Lab",
            "planning_horizon_hours": 10,
            "production_deadline": 9,
            "processes": [
                {"process_id": "step_x", "process_name": "Step X",
                 "duration_hours": 1, "power_kw": 10, "is_flexible": False,
                 "dependencies": [], "machine_id": "core"},
                {"process_id": "step_y", "process_name": "Step Y",
                 "duration_hours": 1.5, "power_kw": 8, "is_flexible": True,
                 "dependencies": ["step_x"], "machine_id": "core"},
                {"process_id": "step_z", "process_name": "Step Z",
                 "duration_hours": 1, "power_kw": 6, "is_flexible": True,
                 "dependencies": ["step_x"], "machine_id": "core"},
            ],
            "machines": [
                {"machine_id": "core", "machine_name": "Shared Core",
                 "capacity": 1, "availability": "single unit",
                 "compatible_processes": ["step_x", "step_y", "step_z"]}
            ],
        }
        baseline = opt.create_baseline_schedule(novel)
        optimized = opt.create_cost_optimized_schedule(novel)
        self.assertIsNotNone(baseline)
        self.assertIsNotNone(optimized)
        self.assertEqual(optimized["status"], "OPTIMAL")
        rows = by_id(optimized)
        self.assertLessEqual(
            rows["step_x"]["end_time"], rows["step_y"]["start_time"] + 1e-9
        )
        # y and z share "core": they must not overlap each other
        y_iv = (rows["step_y"]["start_time"], rows["step_y"]["end_time"])
        z_iv = (rows["step_z"]["start_time"], rows["step_z"]["end_time"])
        first, second = sorted([y_iv, z_iv])
        self.assertLessEqual(first[1], second[0] + 1e-9)


class ConfigValidationTests(unittest.TestCase):
    """Invalid configuration data must raise clear errors."""

    def _valid(self):
        import copy
        return copy.deepcopy(DEMO_FURNITURE_FACTORY)

    def test_valid_config_passes(self):
        self.assertIsInstance(validate_factory_config(self._valid()), FactoryConfig)

    def test_unknown_dependency_rejected(self):
        data = self._valid()
        data["processes"][2]["dependencies"].append("nonexistent")
        with self.assertRaisesRegex(FactoryConfigError, "unknown process"):
            validate_factory_config(data)

    def test_dependency_cycle_rejected(self):
        data = self._valid()
        procs = data["processes"]
        procs[2]["dependencies"].append("assembly")   # sanding -> assembly
        procs[4]["dependencies"].append("sanding")    # assembly -> sanding
        with self.assertRaisesRegex(FactoryConfigError, "cycle"):
            validate_factory_config(data)

    def test_unknown_machine_rejected(self):
        data = self._valid()
        data["processes"][0]["machine_id"] = "ghost_machine"
        with self.assertRaisesRegex(FactoryConfigError, "unknown machine"):
            validate_factory_config(data)

    def test_duplicate_process_id_rejected(self):
        data = self._valid()
        data["processes"][1]["process_id"] = data["processes"][0]["process_id"]
        with self.assertRaisesRegex(FactoryConfigError, "duplicate"):
            validate_factory_config(data)

    def test_negative_power_rejected(self):
        data = self._valid()
        data["processes"][0]["power_kw"] = -5
        with self.assertRaisesRegex(FactoryConfigError, "power_kw"):
            validate_factory_config(data)

    def test_zero_duration_rejected_by_direct_model_validation(self):
        data = self._valid()
        data["processes"][0]["duration_hours"] = 0
        with self.assertRaisesRegex(FactoryConfigError, "duration_hours must be > 0"):
            validate_factory_config(data)

    def test_non_finite_horizon_rejected_by_direct_model_validation(self):
        data = self._valid()
        data["planning_horizon_hours"] = float("inf")
        with self.assertRaisesRegex(FactoryConfigError, "finite number"):
            validate_factory_config(data)

    def test_malformed_process_and_machine_types_are_validation_errors(self):
        for field, values in (("processes", [None]), ("machines", [None])):
            with self.subTest(field=field):
                data = self._valid()
                data[field] = values
                with self.assertRaisesRegex(FactoryConfigError, "must be a dict"):
                    validate_factory_config(data)

    def test_extreme_machine_capacities_are_validation_errors(self):
        for location in ("process", "machine"):
            with self.subTest(location=location):
                data = self._valid()
                if location == "process":
                    data["processes"][0]["capacity_units"] = 10**400
                else:
                    data["machines"][0]["capacity"] = 10**400
                with self.assertRaises(FactoryConfigError):
                    validate_factory_config(data)

    def test_extreme_finite_time_values_are_validation_errors(self):
        for location in ("duration", "horizon", "deadline"):
            with self.subTest(location=location):
                data = self._valid()
                if location == "duration":
                    data["processes"][0]["duration_hours"] = 1e308
                elif location == "horizon":
                    data["planning_horizon_hours"] = 1e308
                else:
                    data["production_deadline"] = 1e308
                    data["planning_horizon_hours"] = 1e308
                with self.assertRaises(FactoryConfigError):
                    validate_factory_config(data)

    def test_bad_duration_grid_rejected(self):
        data = self._valid()
        data["processes"][0]["duration_hours"] = 0.7  # not a multiple of 0.5
        with self.assertRaisesRegex(FactoryConfigError, "multiple of"):
            validate_factory_config(data)

    def test_deadline_beyond_horizon_rejected(self):
        data = self._valid()
        data["production_deadline"] = 30  # horizon is 24
        with self.assertRaisesRegex(FactoryConfigError, "must not exceed"):
            validate_factory_config(data)

    def test_unfillable_window_rejected(self):
        data = self._valid()
        data["processes"][1]["earliest_start"] = 20   # latest finish 16
        with self.assertRaisesRegex(FactoryConfigError, "cannot fit"):
            validate_factory_config(data)

    def test_empty_processes_rejected(self):
        data = self._valid()
        data["processes"] = []
        with self.assertRaisesRegex(FactoryConfigError, "non-empty"):
            validate_factory_config(data)


class CompatibilityTests(unittest.TestCase):
    """Backward-compatible public API and DEMO-only data labeling."""

    def test_legacy_imports_still_work(self):
        self.assertTrue(callable(opt.create_baseline_schedule))
        self.assertTrue(callable(opt.create_solar_aware_schedule))
        self.assertTrue(callable(opt.create_cost_optimized_schedule))
        self.assertTrue(callable(opt.calculate_solar_energy))
        self.assertTrue(callable(opt.calculate_process_cost))
        self.assertIs(opt.DEMO_SOLAR_PROFILE, DEMO_SOLAR_PROFILE)
        self.assertIs(opt.DEMO_TARIFF_PROFILE, DEMO_TARIFF_PROFILE)

    def test_profiles_still_separated_from_engine(self):
        self.assertEqual(opt.get_tariff(12), 0.05)
        self.assertEqual(opt.get_solar_availability(12), 75)

    def test_all_demo_data_clearly_labeled(self):
        import inspect
        from optimizer import factory_data, energy_data
        for module in (factory_data, energy_data):
            source = inspect.getdoc(module) or ""
            self.assertIn("DEMO/SIMULATED", source,
                          msg=f"{module.__name__} missing DEMO/SIMULATED label")


class EntryPointTests(unittest.TestCase):
    """The CLI binds the generic engine to all registered demo factories."""

    def test_cli_main_runs_all_registered_factories(self):
        from optimizer import cli
        import io
        import contextlib
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli.main()
        output = buffer.getvalue()
        for name in (
            "Demo Chocolate Factory",
            "Demo Cosmetics Factory",
            "Demo Food Factory",
            "Demo Beverage Factory",
            "Demo Pharmaceutical Factory",
            "Demo Automotive Factory",
        ):
            self.assertIn(name, output)


if __name__ == "__main__":
    unittest.main()
