"""
ByteMe Application + Input Layer Tests (DEMO/SIMULATED DATA ONLY)

Covers: valid/invalid user input, custom factories and energy profiles,
workflow, cost and carbon calculations, serialization, and display.
Existing engine tests in tests/test_optimizer.py are unaffected.
"""

import copy
import json
import os
import tempfile
import unittest

from optimizer import app
from optimizer.factory_data import AVAILABLE_FACTORIES
from optimizer.input_layer import (
    InputValidationError,
    format_validation_error,
    load_user_input,
    validate_user_input,
)
from optimizer.models import FactoryConfig
from optimizer.optimizer import SolverUnknownError
from optimizer.visualization import (
    render_energy_comparison,
    render_energy_profile,
    render_gantt_comparison,
)


VALID_INPUT = {
    "factory": {
        "factory_name": "Demo Test Lab",
        "factory_type": "test",
        "planning_horizon_hours": 12,
        "production_deadline": 10,
        "processes": [
            {
                "process_id": "step_x",
                "process_name": "Step X",
                "duration_hours": 1,
                "power_kw": 10,
                "quantity": 5,
                "dependencies": [],
                "is_flexible": False,
                "machine_id": "core",
            },
            {
                "process_id": "step_y",
                "process_name": "Step Y",
                "duration_hours": 1.5,
                "power_kw": 8,
                "dependencies": ["step_x"],
                "is_flexible": True,
                "machine_id": "core",
            },
            {
                "process_id": "step_z",
                "process_name": "Step Z",
                "duration_hours": 1,
                "power_kw": 6,
                "dependencies": ["step_x"],
                "is_flexible": True,
                "machine_id": "polisher",
                "earliest_start": 2,
                "latest_finish": 9,
            },
        ],
        "machines": [
            {
                "machine_id": "core",
                "machine_name": "Shared Core Machine",
                "availability": "single unit",
                "capacity": 100,
                "power_kw": 12,
                "compatible_processes": ["step_x", "step_y"],
            },
            {
                "machine_id": "polisher",
                "machine_name": "Polishing Bench",
                "availability": "single unit",
                "capacity": 50,
                "power_kw": 4,
                "compatible_processes": ["step_z"],
            },
        ],
    },
    "energy": {
        "solar_profile": {h: (10 if 8 <= h <= 16 else 0) for h in range(24)},
        "tariff_profile": {h: (0.10 if 8 <= h <= 16 else 0.40) for h in range(24)},
        "grid_emission_factor": 0.35,
    },
}


class ValidInputTests(unittest.TestCase):
    """Valid user input loads, validates, and runs the full workflow."""

    def setUp(self):
        self.config = validate_user_input(copy.deepcopy(VALID_INPUT))

    def test_valid_input_returns_config(self):
        self.assertIsInstance(self.config, FactoryConfig)
        self.assertEqual(self.config.factory_name, "Demo Test Lab")
        self.assertEqual(self.config.factory_type, "test")
        self.assertEqual(len(self.config.processes), 3)
        self.assertEqual(len(self.config.machines), 2)
        self.assertIn("solar_profile", self.config.energy)

    def test_json_string_and_file_input(self):
        as_json = json.dumps(VALID_INPUT)
        from_string = load_user_input(as_json)
        self.assertEqual(from_string.factory_name, "Demo Test Lab")

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(VALID_INPUT, fh)
            path = fh.name
        try:
            from_file = load_user_input(path)
            self.assertEqual(from_file.factory_name, "Demo Test Lab")
        finally:
            os.unlink(path)

    def test_demo_factories_load_through_input_layer(self):
        for key, data in AVAILABLE_FACTORIES.items():
            with self.subTest(factory=key):
                config = validate_user_input({"factory": data})
                self.assertIsInstance(config, FactoryConfig)
                self.assertEqual(config.factory_name, data["factory_name"])

    def test_custom_energy_profiles(self):
        config = validate_user_input(copy.deepcopy(VALID_INPUT))
        self.assertEqual(config.energy["solar_profile"][12], 10)
        self.assertEqual(config.energy["tariff_profile"][2], 0.40)
        self.assertEqual(config.energy["grid_emission_factor"], 0.35)

    def test_missing_optional_sections_default(self):
        data = copy.deepcopy(VALID_INPUT)
        del data["energy"]
        data["factory"]["machines"] = []
        for proc in data["factory"]["processes"]:
            proc.pop("machine_id", None)
        config = validate_user_input(data)
        self.assertEqual(config.machines, [])
        # DEMO profiles used by default
        self.assertIn("solar_profile", config.energy)


class InvalidInputTests(unittest.TestCase):
    """Invalid input must produce clear, human-readable errors."""

    def _invalid(self, mutate):
        data = copy.deepcopy(VALID_INPUT)
        mutate(data)
        with self.assertRaises(InputValidationError) as ctx:
            validate_user_input(data)
        return ctx.exception

    def test_duplicate_json_properties_are_rejected_by_input_loader(self):
        with self.assertRaisesRegex(InputValidationError, "duplicate property"):
            load_user_input('{"factory": {}, "factory": {}}')

    def test_duplicate_process_id(self):
        exc = self._invalid(lambda d: d["factory"]["processes"][1].update(
            process_id="step_x"))
        self.assertTrue(any("duplicate process_id" in p for p in exc.problems))

    def test_missing_process_id(self):
        exc = self._invalid(lambda d: d["factory"]["processes"][0].pop("process_id"))
        self.assertTrue(any("missing process_id" in p for p in exc.problems))

    def test_invalid_dependency(self):
        exc = self._invalid(lambda d: d["factory"]["processes"][1]["dependencies"].append(
            "ghost"))
        self.assertTrue(any("invalid dependency" in p for p in exc.problems))

    def test_circular_dependency(self):
        def mutate(d):
            procs = d["factory"]["processes"]
            procs[0]["dependencies"].append("step_y")  # x -> y -> x
        exc = self._invalid(mutate)
        self.assertTrue(any("circular dependency" in p for p in exc.problems))

    def test_invalid_machine_reference(self):
        exc = self._invalid(lambda d: d["factory"]["processes"][0].update(
            machine_id="ghost_machine"))
        self.assertTrue(any("invalid machine assignment" in p for p in exc.problems))

    def test_negative_duration_and_power(self):
        exc = self._invalid(lambda d: (
            d["factory"]["processes"][0].update(duration_hours=-1),
            d["factory"]["processes"][1].update(power_kw=-5),
        ))
        self.assertTrue(any("duration_hours" in p for p in exc.problems))
        self.assertTrue(any("power_kw" in p for p in exc.problems))

    def test_impossible_deadline(self):
        exc = self._invalid(lambda d: d["factory"].update(production_deadline=1))
        self.assertTrue(
            any("impossible time window" in p or "cannot fit" in p
                for p in exc.problems)
        )

    def test_invalid_time_window(self):
        exc = self._invalid(lambda d: d["factory"]["processes"][2].update(
            earliest_start=8, latest_finish=8))
        self.assertTrue(any("impossible time window" in p for p in exc.problems))

    def test_invalid_energy_profiles(self):
        exc = self._invalid(lambda d: d["energy"].update(
            solar_profile={"25": 5},          # invalid hour
            tariff_profile={"5": -1},         # negative tariff
            grid_emission_factor=-0.2,        # negative factor
        ))
        text = " ".join(exc.problems)
        self.assertIn("solar_profile", text)
        self.assertIn("tariff_profile", text)
        self.assertIn("grid_emission_factor", text)

    def test_all_problems_reported_at_once(self):
        def mutate(d):
            procs = d["factory"]["processes"]
            procs[1].update(process_id="step_x")        # duplicate id
            procs[0].update(power_kw=-3)                # negative power
        exc = self._invalid(mutate)
        self.assertGreaterEqual(len(exc.problems), 2)
        report = format_validation_error(exc)
        self.assertIn("1.", report)
        self.assertIn("2.", report)

    def test_non_dict_input_rejected(self):
        with self.assertRaises(InputValidationError):
            validate_user_input([1, 2, 3])


class WorkflowTests(unittest.TestCase):
    """Full application workflow: select -> review -> run -> results."""

    def test_baseline_generation(self):
        config = validate_user_input(copy.deepcopy(VALID_INPUT))
        baseline = app.run_baseline(config)
        self.assertEqual(baseline["status"], "OPTIMAL")
        self.assertEqual(len(baseline["processes"]), 3)

    def test_optimized_generation_cost_and_solar_objectives(self):
        config = validate_user_input(copy.deepcopy(VALID_INPUT))
        for objective in ("cost", "solar"):
            result = app.run_optimization(config, objective=objective)
            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "OPTIMAL")

    def test_optimization_rejects_unknown_objective(self):
        config = validate_user_input(copy.deepcopy(VALID_INPUT))
        with self.assertRaisesRegex(InputValidationError, "objective"):
            app.run_optimization(config, objective="solr")

    def test_workflow_valid_input_end_to_end(self):
        result = app.run_workflow(copy.deepcopy(VALID_INPUT))
        self.assertEqual(result["status"], "OPTIMAL")
        self.assertIsNone(result["validation_errors"])
        results = result["results"]
        self.assertEqual(results["factory_name"], "Demo Test Lab")
        self.assertIn("baseline", results)
        self.assertIn("optimized", results)

    def test_workflow_invalid_input_status(self):
        bad = copy.deepcopy(VALID_INPUT)
        bad["factory"]["processes"][1]["dependencies"].append("ghost")
        result = app.run_workflow(bad)
        self.assertEqual(result["status"], app.STATUS_INVALID_INPUT)
        self.assertTrue(result["validation_errors"])

    def test_workflow_infeasible_status(self):
        # Every process is individually valid (passes input validation), but
        # the x -> y dependency chain (1 h + 1.5 h) cannot finish by the
        # deadline of 2 h: the SOLVER reports the model infeasible.
        infeasible = copy.deepcopy(VALID_INPUT)
        infeasible["factory"]["production_deadline"] = 2
        result = app.run_workflow(infeasible)
        self.assertEqual(result["status"], app.STATUS_INFEASIBLE)

    def test_workflow_rejects_unknown_objective(self):
        result = app.run_workflow(copy.deepcopy(VALID_INPUT), objective="banana")
        self.assertEqual(result["status"], app.STATUS_INVALID_INPUT)
        self.assertTrue(any("objective" in error.lower()
                            for error in result["validation_errors"]))

    def test_workflow_unknown_keeps_successful_baseline(self):
        from unittest.mock import patch

        with patch(
                "optimizer.public_api.create_cost_optimized_schedule",
                side_effect=SolverUnknownError("test limit", 0.25)):
            result = app.run_workflow(copy.deepcopy(VALID_INPUT))

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["baseline"]["status"], "OPTIMAL")
        self.assertIsNone(result["optimized"])
        self.assertGreater(result["solve_time_seconds"], 0.25)
        self.assertTrue(any("optimized solver hit its time limit" in warning
                    for warning in result["warnings"]))

    def test_select_factory_registry(self):
        names = app.list_factories()
        for expected in ("chocolate", "cosmetics", "food", "beverage",
                         "pharmaceutical", "automotive", "furniture"):
            self.assertIn(expected, names)
        with self.assertRaises(Exception):
            app.select_factory("nonexistent")

    def test_review_input_round_trip(self):
        config = validate_user_input(copy.deepcopy(VALID_INPUT))
        editable = app.review_input(config)
        # Editable form is valid again after review (round trip)
        revalidated = validate_user_input(editable)
        self.assertEqual(revalidated.factory_name, "Demo Test Lab")


class MetricsAndCarbonTests(unittest.TestCase):
    """Metrics, machine utilization, cost and carbon calculations."""

    def setUp(self):
        self.result = app.run_workflow(copy.deepcopy(VALID_INPUT))["results"]

    def test_cost_calculation_matches_schedule_rows(self):
        for key in ("baseline", "optimized"):
            schedule = self.result[f"{key}_schedule"]
            row_cost = sum(p["energy_cost"] for p in schedule["processes"])
            self.assertAlmostEqual(self.result[key]["energy_cost"], row_cost, places=6)

    def test_optimized_cost_not_greater_than_baseline(self):
        self.assertLessEqual(
            self.result["optimized"]["energy_cost"],
            self.result["baseline"]["energy_cost"] + 1e-6,
        )

    def test_machine_utilization(self):
        utilization = self.result["machine_utilization"]["optimized"]
        self.assertIn("core", utilization)
        stats = utilization["core"]
        self.assertIn("busy_hours", stats)
        self.assertIn("utilization_percent", stats)
        self.assertGreater(stats["busy_hours"], 0)
        self.assertLessEqual(stats["utilization_percent"], 100.0 + 1e-9)

    def test_carbon_calculation_with_factor(self):
        carbon = app.calculate_carbon(100.0, 40.0, 0.5)
        self.assertAlmostEqual(carbon["baseline_co2_kg"], 50.0, places=6)
        self.assertAlmostEqual(carbon["optimized_co2_kg"], 20.0, places=6)
        self.assertAlmostEqual(carbon["co2_reduction_kg"], 30.0, places=6)
        self.assertAlmostEqual(carbon["co2_reduction_percent"], 60.0, places=6)

    def test_carbon_unavailable_without_factor(self):
        self.assertIsNone(app.calculate_carbon(100.0, 40.0, None))
        # Workflow without emission factor reports carbon unavailable
        data = copy.deepcopy(VALID_INPUT)
        data["energy"].pop("grid_emission_factor")
        result = app.run_workflow(data)["results"]
        self.assertIsNone(result["carbon"])

    def test_workflow_with_carbon(self):
        result = app.run_workflow(copy.deepcopy(VALID_INPUT))["results"]
        carbon = result["carbon"]
        self.assertIsNotNone(carbon)
        factor = carbon["grid_emission_factor_kg_per_kwh"]
        self.assertAlmostEqual(
            carbon["optimized_co2_kg"],
            result["optimized"]["grid_kwh"] * factor, places=6,
        )


class SerializationAndDisplayTests(unittest.TestCase):
    """Serialization and console display of results."""

    def test_result_serialization_round_trip(self):
        result = app.run_workflow(copy.deepcopy(VALID_INPUT))["results"]
        serialized = app.serialize_results(result)
        text = json.dumps(serialized)
        self.assertIn("Demo Test Lab", text)
        restored = json.loads(text)
        self.assertEqual(restored["factory_name"], "Demo Test Lab")
        self.assertAlmostEqual(
            restored["optimized"]["energy_cost"],
            result["optimized"]["energy_cost"], places=6,
        )

    def test_example_input_file_runs(self):
        example_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "examples", "widget_lab.json",
        )
        result = app.run_workflow(example_path)
        self.assertEqual(result["status"], "OPTIMAL")
        self.assertEqual(result["results"]["factory_name"], "Demo Widget Lab")
        self.assertIsNotNone(result["results"]["carbon"])

    def test_display_results_runs(self):
        import contextlib
        import io
        result = app.run_workflow(copy.deepcopy(VALID_INPUT))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            app.display_results(result["results"])
        output = buffer.getvalue()
        for expected in ("BASELINE", "OPTIMIZED", "Makespan", "Energy Cost",
                         "Cost Savings", "Shifted Processes",
                         "Machine Utilization", "Carbon", "DEMO/SIMULATED"):
            self.assertIn(expected, output)

    def test_display_invalid_input_runs(self):
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            app.display_validation_errors(["problem one", "problem two"])
        self.assertIn("problem one", buffer.getvalue())

    def test_visualizations_run(self):
        import contextlib
        import io
        result = app.run_workflow(copy.deepcopy(VALID_INPUT))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            render_gantt_comparison(
                result["results"]["baseline_schedule"],
                result["results"]["optimized_schedule"],
            )
            render_energy_profile(
                VALID_INPUT["energy"]["solar_profile"],
                result["results"]["optimized_schedule"],
            )
            render_energy_comparison(
                result["results"]["baseline_schedule"],
                result["results"]["optimized_schedule"],
            )
        output = buffer.getvalue()
        for expected in ("BASELINE SCHEDULE", "OPTIMIZED SCHEDULE",
                         "ENERGY PROFILE", "ENERGY / COST COMPARISON"):
            self.assertIn(expected, output)

    def test_energy_profile_wraps_solar_after_24_hours(self):
        import contextlib
        import io

        profile = {hour: (7 if hour == 0 else 0) for hour in range(24)}
        schedule = {
            "factory_name": "Long Horizon",
            "processes": [{
                "process_id": "overnight",
                "start_time": 24,
                "end_time": 25,
                "power_kw": 1,
            }],
        }
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            render_energy_profile(profile, schedule)

        hour_24 = next(
            line for line in buffer.getvalue().splitlines()
            if line.startswith("24 ")
        )
        self.assertIn("7.0", hour_24)

    def test_energy_profile_uses_exact_hourly_solar_allocation(self):
        import contextlib
        import io

        profile = {hour: 0 for hour in range(24)}
        profile[11] = 10
        schedule = {
            "factory_name": "Solar Transition",
            "processes": [{
                "process_id": "crossing",
                "start_time": 10.5,
                "end_time": 11.5,
                "power_kw": 10,
            }],
        }
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            render_energy_profile(profile, schedule)

        rows = {
            int(line.split()[0]): line
            for line in buffer.getvalue().splitlines()
            if line.startswith(("10 ", "11 "))
        }
        self.assertEqual(rows[10][53:62].strip(), "5.00")
        self.assertEqual(rows[11][53:62].strip(), "0.00")


class GenericityTests(unittest.TestCase):
    """The application layer must also be fully factory-agnostic."""

    def test_app_source_has_no_industry_terms(self):
        import inspect
        from optimizer import app, input_layer, visualization
        forbidden = ("chocolate", "cosmetic", "beverage", "pharma",
                     "automotive", "furniture", "roasting", "granulation",
                     "painting", "filling")
        for module in (app, input_layer, visualization):
            source = inspect.getsource(module).lower()
            for term in forbidden:
                self.assertNotIn(
                    term, source,
                    msg=f"{module.__name__} mentions industry term {term!r}",
                )

    def test_eighth_factory_from_data_only(self):
        # A brand-new arbitrary factory through the application workflow:
        # no engine, registry, or application-code changes required.
        widget_lab = {
            "factory": {
                "factory_name": "Demo Widget Lab",
                "factory_type": "widgets",
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
                     "dependencies": ["step_x"], "machine_id": "polisher"},
                ],
                "machines": [
                    {"machine_id": "core", "machine_name": "Shared Core",
                     "capacity": 1, "availability": "single unit",
                     "compatible_processes": ["step_x", "step_y"]},
                    {"machine_id": "polisher", "machine_name": "Polisher",
                     "capacity": 1, "availability": "single unit",
                     "compatible_processes": ["step_z"]},
                ],
            },
            "energy": {
                "solar_profile": {h: (12 if 9 <= h <= 15 else 0) for h in range(24)},
                "tariff_profile": {h: (0.08 if 9 <= h <= 15 else 0.45)
                                   for h in range(24)},
                "grid_emission_factor": 0.4,
            },
        }
        result = app.run_workflow(widget_lab)
        self.assertEqual(result["status"], "OPTIMAL")
        self.assertEqual(result["results"]["factory_name"], "Demo Widget Lab")


if __name__ == "__main__":
    unittest.main()
