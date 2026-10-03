"""
ByteMe Public API Integration Tests (DEMO/SIMULATED DATA ONLY)

Tests the PUBLIC ENTRY POINT optimize() rather than internal functions:
all registered factories, Widget Lab, custom factories and energy profiles,
the full error taxonomy, contract stability, and JSON round trips.
"""

import copy
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from optimizer import public_api
from optimizer.factory_data import AVAILABLE_FACTORIES
from optimizer.public_api import (
    STATUS_ERROR,
    STATUS_FEASIBLE,
    STATUS_INFEASIBLE,
    STATUS_INVALID_INPUT,
    STATUS_OPTIMAL,
    STATUS_UNKNOWN,
    OptimizerInputError,
    optimize,
    optimize_request,
)
from optimizer.optimizer import SolverModelInvalidError, SolverUnknownError

try:
    import jsonschema
    _HAS_JSONSCHEMA = True
except ImportError:  # optional dependency - contract still checked manually
    _HAS_JSONSCHEMA = False


VALID_WIDGET = {
    "factory": {
        "factory_name": "Demo Widget Lab",
        "factory_type": "widgets",
        # Deadline 16 leaves room to shift y/z into the solar window 9-15
        "planning_horizon_hours": 18,
        "production_deadline": 16,
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
        "tariff_profile": {h: (0.08 if 9 <= h <= 15 else 0.45) for h in range(24)},
        "grid_emission_factor": 0.4,
    },
}


def registered_factory_input(key):
    """Wrap a registered demo factory config in the user-input envelope."""
    from optimizer.app import review_input
    from optimizer.input_layer import validate_user_input
    config = validate_user_input({"factory": AVAILABLE_FACTORIES[key]})
    return review_input(config)


class OptimizeValidInputTests(unittest.TestCase):
    """optimize() on valid factories: happy paths."""

    def test_widget_lab_dict_input(self):
        result = optimize(copy.deepcopy(VALID_WIDGET))
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertIsNone(result["errors"])
        self.assertEqual(result["result"]["factory_name"], "Demo Widget Lab")
        self.assertEqual(result["result"]["objective"], "cost")
        for solve_time in (
            result["result"]["solve_time_seconds"],
            result["result"]["baseline"]["solve_time_seconds"],
            result["result"]["optimized"]["solve_time_seconds"]):
            self.assertIsInstance(solve_time, (int, float))
            self.assertGreaterEqual(solve_time, 0)

    def test_feasible_solver_status_keeps_wall_time(self):
        from ortools.sat.python import cp_model
        from optimizer.optimizer import _new_solver

        real_new_solver = _new_solver

        class FeasibleStatusSolver:
            def __init__(self, max_time_seconds):
                self.solver = real_new_solver(max_time_seconds)

            def Solve(self, model):
                status = self.solver.Solve(model)
                return (cp_model.FEASIBLE if status == cp_model.OPTIMAL
                        else status)

            def __getattr__(self, name):
                return getattr(self.solver, name)

        with patch("optimizer.optimizer._new_solver",
                   side_effect=FeasibleStatusSolver):
            result = optimize(copy.deepcopy(VALID_WIDGET))

        self.assertEqual(result["status"], STATUS_FEASIBLE)
        for schedule in (result["result"]["baseline"],
                         result["result"]["optimized"]):
            self.assertEqual(schedule["status"], STATUS_FEASIBLE)
            self.assertIsInstance(schedule["solve_time_seconds"], (int, float))
            self.assertGreaterEqual(schedule["solve_time_seconds"], 0)
        self.assertGreaterEqual(result["result"]["solve_time_seconds"], 0)

    def test_all_seven_registered_factories(self):
        for key in AVAILABLE_FACTORIES:
            with self.subTest(factory=key):
                result = optimize(registered_factory_input(key))
                self.assertEqual(result["status"], STATUS_OPTIMAL, msg=key)
                payload = result["result"]
                self.assertIsNotNone(payload["baseline"], msg=key)
                self.assertIsNotNone(payload["optimized"], msg=key)
                self.assertEqual(
                    len(payload["optimized"]["processes"]),
                    len(payload["baseline"]["processes"]), msg=key,
                )

    def test_widget_lab_from_json_string_and_file(self):
        as_json = json.dumps(VALID_WIDGET)
        self.assertEqual(optimize(as_json)["status"], STATUS_OPTIMAL)

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(VALID_WIDGET, fh)
            path = fh.name
        try:
            self.assertEqual(optimize(path)["status"], STATUS_OPTIMAL)
        finally:
            os.unlink(path)

    def test_custom_energy_profile(self):
        data = copy.deepcopy(VALID_WIDGET)
        data["energy"]["solar_profile"] = {h: 50 for h in range(24)}   # always sun
        data["energy"]["tariff_profile"] = {h: 0.05 for h in range(24)}  # flat cheap
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        # constant sun: optimized schedule runs fully on solar
        self.assertGreater(result["result"]["optimized"]["energy"]["solar_kwh"], 0)

    def test_solar_objective_option(self):
        result = optimize(copy.deepcopy(VALID_WIDGET), objective="solar")
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertEqual(result["result"]["objective"], "solar")

    def test_objective_option_in_input(self):
        data = copy.deepcopy(VALID_WIDGET)
        data["options"] = {"objective": "solar"}
        result = optimize(data)
        self.assertEqual(result["result"]["objective"], "solar")

    def test_optional_carbon_present_and_absent(self):
        with_carbon = optimize(copy.deepcopy(VALID_WIDGET))
        self.assertIsNotNone(with_carbon["result"]["carbon"])
        self.assertGreater(with_carbon["result"]["carbon"]["co2_reduction_kg"], 0)

        without = copy.deepcopy(VALID_WIDGET)
        without["energy"].pop("grid_emission_factor")
        result = optimize(without)
        self.assertIsNone(result["result"]["carbon"])

    def test_baseline_optimized_consistency(self):
        result = optimize(copy.deepcopy(VALID_WIDGET))["result"]
        same_ids = (
            [p["process_id"] for p in result["baseline"]["processes"]]
            == [p["process_id"] for p in result["optimized"]["processes"]]
        )
        self.assertTrue(same_ids)
        # total energy is identical (power x duration is schedule-invariant)
        self.assertAlmostEqual(
            result["baseline"]["energy"]["total_kwh"],
            result["optimized"]["energy"]["total_kwh"], places=6,
        )
        # optimized never costs more
        self.assertLessEqual(
            result["comparison"]["cost_optimized"],
            result["comparison"]["cost_baseline"] + 1e-6,
        )
        # energy identity per row: solar + grid = power x duration
        for row in result["optimized"]["processes"]:
            self.assertAlmostEqual(
                row["solar_kwh"] + row["grid_kwh"],
                row["power_kw"] * row["duration_hours"], places=6,
            )

    def test_json_serialization_round_trip(self):
        result = optimize(copy.deepcopy(VALID_WIDGET))
        text = json.dumps(result)
        restored = json.loads(text)
        self.assertEqual(restored["status"], STATUS_OPTIMAL)
        self.assertEqual(
            restored["result"]["comparison"]["cost_savings"],
            result["result"]["comparison"]["cost_savings"],
        )


class OptimizeInvalidInputTests(unittest.TestCase):
    """optimize() error handling: every failure mode returns a clean result."""

    def test_invalid_schema_missing_factory(self):
        result = optimize({"energy": {}})
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(result["errors"])
        self.assertIsNone(result["result"])

    def test_invalid_dependency(self):
        data = copy.deepcopy(VALID_WIDGET)
        data["factory"]["processes"][1]["dependencies"].append("ghost")
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("invalid dependency" in e for e in result["errors"]))

    def test_circular_dependency(self):
        data = copy.deepcopy(VALID_WIDGET)
        data["factory"]["processes"][0]["dependencies"].append("step_y")
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("circular dependency" in e for e in result["errors"]))

    def test_duplicate_process_ids(self):
        data = copy.deepcopy(VALID_WIDGET)
        data["factory"]["processes"][1]["process_id"] = "step_x"
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("duplicate" in e for e in result["errors"]))

    def test_invalid_machine_assignment(self):
        data = copy.deepcopy(VALID_WIDGET)
        data["factory"]["processes"][0]["machine_id"] = "ghost_machine"
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("machine" in e for e in result["errors"]))

    def test_impossible_deadline(self):
        data = copy.deepcopy(VALID_WIDGET)
        data["factory"]["production_deadline"] = 1   # chain needs 2.5 h
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("time window" in e or "cannot fit" in e
                            for e in result["errors"]))

    def test_infeasible_optimization(self):
        # Every process is individually valid; the dependency chain cannot
        # finish by the shared deadline -> solver reports INFEASIBLE.
        data = copy.deepcopy(VALID_WIDGET)
        data["factory"]["production_deadline"] = 2
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INFEASIBLE)
        self.assertEqual(result["error_category"], "INFEASIBLE")
        self.assertIsNone(result["result"]["optimized"])
        self.assertIsInstance(result["result"]["solve_time_seconds"], (int, float))
        self.assertGreaterEqual(result["result"]["solve_time_seconds"], 0)
        self.assertIsNone(result["errors"])
        if _HAS_JSONSCHEMA:
            jsonschema.validate(result, public_api.get_output_schema())

    def test_operational_warnings_match_their_conditions(self):
        flat = copy.deepcopy(VALID_WIDGET)
        for process in flat["factory"]["processes"]:
            process["is_flexible"] = False
        flat["energy"]["tariff_profile"] = {hour: 0.25 for hour in range(24)}
        warnings = optimize(flat)["warnings"]
        self.assertTrue(any("all processes are marked non-flexible" in w
                            for w in warnings))
        self.assertTrue(any("uniform across the planning horizon" in w
                            for w in warnings))

        partly_flexible = copy.deepcopy(flat)
        partly_flexible["factory"]["processes"][0]["is_flexible"] = True
        warnings = optimize(partly_flexible)["warnings"]
        self.assertFalse(any("all processes are marked non-flexible" in w
                             for w in warnings))
        self.assertTrue(any("uniform across the planning horizon" in w
                            for w in warnings))

        warnings = optimize(VALID_WIDGET, objective="solar")["warnings"]
        self.assertFalse(any("uniform across the planning horizon" in w
                             for w in warnings))
        warnings = optimize(VALID_WIDGET)["warnings"]
        self.assertFalse(any("uniform across the planning horizon" in w
                             for w in warnings))

    def test_operational_warnings_survive_unknown_solver_status(self):
        data = copy.deepcopy(VALID_WIDGET)
        for process in data["factory"]["processes"]:
            process["is_flexible"] = False
        data["energy"]["tariff_profile"] = {hour: 0.25 for hour in range(24)}
        with patch(
                "optimizer.public_api.create_baseline_schedule",
                side_effect=SolverUnknownError("test limit", 0.125)):
            result = optimize(data)
        self.assertEqual(result["status"], STATUS_UNKNOWN)
        self.assertEqual(result["result"]["solve_time_seconds"], 0.125)
        self.assertTrue(any("all processes are marked non-flexible" in w
                            for w in result["warnings"]))
        self.assertTrue(any("uniform across the planning horizon" in w
                            for w in result["warnings"]))

    def test_unknown_status_preserves_factory_name_for_json_input(self):
        with patch(
                "optimizer.public_api.create_baseline_schedule",
                side_effect=SolverUnknownError("test limit", 0.125)):
            result = optimize(json.dumps(VALID_WIDGET))

        self.assertEqual(result["status"], STATUS_UNKNOWN)
        self.assertEqual(
            result["result"]["factory_name"], "Demo Widget Lab"
        )

    def test_unknown_optimization_preserves_successful_baseline(self):
        with patch(
                "optimizer.public_api.create_cost_optimized_schedule",
                side_effect=SolverUnknownError("test limit", 0.25)):
            result = optimize(copy.deepcopy(VALID_WIDGET))

        self.assertEqual(result["status"], STATUS_UNKNOWN)
        payload = result["result"]
        self.assertEqual(payload["baseline"]["status"], STATUS_OPTIMAL)
        self.assertGreater(len(payload["baseline"]["processes"]), 0)
        self.assertIsNone(payload["optimized"])
        self.assertEqual(payload["solve_time_seconds"], round(
            payload["baseline"]["solve_time_seconds"] + 0.25, 4
        ))
        self.assertTrue(any("baseline schedule is available" in warning
                            for warning in payload["warnings"]))
        if _HAS_JSONSCHEMA:
            jsonschema.validate(result, public_api.get_output_schema())

    def test_model_invalid_is_reported_as_internal_error(self):
        from ortools.sat.python import cp_model

        class InvalidModelSolver:
            def Solve(self, model):
                return cp_model.MODEL_INVALID

        with patch("optimizer.optimizer._new_solver",
                   return_value=InvalidModelSolver()):
            result = optimize(copy.deepcopy(VALID_WIDGET))

        self.assertEqual(result["status"], STATUS_ERROR)
        self.assertEqual(result["error_category"], "INTERNAL_ERROR")
        self.assertIn("SolverModelInvalidError", result["errors"][0])

    def test_engine_raises_for_model_invalid_in_every_solver_stage(self):
        from ortools.sat.python import cp_model
        from optimizer.optimizer import (
            create_baseline_schedule,
            create_cost_optimized_schedule,
            create_solar_aware_schedule,
        )

        class InvalidModelSolver:
            def Solve(self, model):
                return cp_model.MODEL_INVALID

        factory = copy.deepcopy(VALID_WIDGET["factory"])
        with patch("optimizer.optimizer._new_solver",
                   return_value=InvalidModelSolver()):
            with self.assertRaises(SolverModelInvalidError):
                create_baseline_schedule(factory)

        baseline = create_baseline_schedule(factory)
        with patch("optimizer.optimizer._new_solver",
                   return_value=InvalidModelSolver()):
            for runner in (create_cost_optimized_schedule,
                           create_solar_aware_schedule):
                with self.subTest(runner=runner.__name__):
                    with self.assertRaises(SolverModelInvalidError):
                        runner(factory, baseline_schedule=baseline)

    def test_earliest_start_rounds_up_to_the_half_hour_grid(self):
        data = copy.deepcopy(VALID_WIDGET)
        process = data["factory"]["processes"][0]
        process["duration_hours"] = 0.5
        process["earliest_start"] = 1.25
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        for schedule_name in ("baseline", "optimized"):
            row = next(p for p in result["result"][schedule_name]["processes"]
                       if p["process_id"] == "step_x")
            self.assertGreaterEqual(row["start_time"], 1.25)
            self.assertEqual(row["start_time"], 1.5)

    def test_latest_finish_rounds_down_to_the_half_hour_grid(self):
        data = copy.deepcopy(VALID_WIDGET)
        process = data["factory"]["processes"][0]
        process["duration_hours"] = 0.5
        process["earliest_start"] = 0.5
        process["latest_finish"] = 1.25
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        for schedule_name in ("baseline", "optimized"):
            row = next(p for p in result["result"][schedule_name]["processes"]
                       if p["process_id"] == "step_x")
            self.assertEqual(row["end_time"], 1.0)

    def test_fractional_horizon_includes_final_partial_hour_cost(self):
        data = {
            "factory": {
                "factory_name": "Fractional Horizon Probe",
                "planning_horizon_hours": 2.5,
                "production_deadline": 2.5,
                "processes": [{
                    "process_id": "p", "duration_hours": 1,
                    "power_kw": 10, "dependencies": [],
                    "is_flexible": True,
                }],
            },
            "energy": {
                "solar_profile": {hour: 0 for hour in range(24)},
                "tariff_profile": {
                    hour: (1000 if hour == 0 else
                           10 if hour == 1 else
                           100 if hour == 2 else 0)
                    for hour in range(24)
                },
            },
        }
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        row = result["result"]["optimized"]["processes"][0]
        self.assertEqual(row["start_time"], 1.0)
        self.assertAlmostEqual(
            result["result"]["comparison"]["cost_optimized"], 100.0
        )

    def test_unrepresentable_energy_coefficients_are_rejected(self):
        data = copy.deepcopy(VALID_WIDGET)
        data["factory"]["processes"][0]["power_kw"] = 0.1
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("power_kw" in error for error in result["errors"]))

        data = copy.deepcopy(VALID_WIDGET)
        data["energy"]["tariff_profile"][0] = 0.0004
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("tariff_profile" in error
                            for error in result["errors"]))

        data = copy.deepcopy(VALID_WIDGET)
        data["energy"]["solar_profile"] = {hour: 0.1 for hour in range(24)}
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("solar_profile" in error
                            for error in result["errors"]))

    def test_small_representable_energy_coefficients_optimize_correctly(self):
        data = {
            "factory": {
                "factory_name": "Small Exact Coefficients",
                "planning_horizon_hours": 3,
                "production_deadline": 3,
                "processes": [{
                    "process_id": "p", "duration_hours": 0.5,
                    "power_kw": 0.2, "dependencies": [],
                    "is_flexible": True,
                }],
            },
            "energy": {
                "solar_profile": {hour: 0 for hour in range(24)},
                "tariff_profile": {
                    hour: (0.01 if hour == 0 else 0.001)
                    for hour in range(24)
                },
            },
        }
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        row = result["result"]["optimized"]["processes"][0]
        self.assertGreaterEqual(row["start_time"], 1.0)
        self.assertAlmostEqual(row["energy_cost"], 0.0001, places=9)

    def test_informational_fields_do_not_change_schedule(self):
        original = copy.deepcopy(VALID_WIDGET)
        changed = copy.deepcopy(VALID_WIDGET)
        for process in changed["factory"]["processes"]:
            process["quantity"] = 10_000
        for machine in changed["factory"]["machines"]:
            machine["power_kw"] = 9_999
            machine["availability"] = "unavailable all day"
            machine["compatible_processes"] = []

        baseline_result = optimize(original)
        changed_result = optimize(changed)
        self.assertEqual(baseline_result["status"], STATUS_OPTIMAL)
        self.assertEqual(changed_result["status"], STATUS_OPTIMAL)
        for schedule_name in ("baseline", "optimized"):
            fields = ("process_id", "start_time", "end_time", "energy_cost",
                      "solar_kwh", "grid_kwh")
            baseline_rows = [
                {field: row[field] for field in fields}
                for row in baseline_result["result"][schedule_name]["processes"]
            ]
            changed_rows = [
                {field: row[field] for field in fields}
                for row in changed_result["result"][schedule_name]["processes"]
            ]
            self.assertEqual(baseline_rows, changed_rows)

    def test_invalid_objective_is_invalid_input(self):
        for source, objective in (
                (copy.deepcopy(VALID_WIDGET), "banana"),
                ({**copy.deepcopy(VALID_WIDGET),
                 "options": {"objective": "banana"}}, "cost")):
            with self.subTest(objective=objective, options=source.get("options")):
                result = optimize(source, objective=objective)
                self.assertEqual(result["status"], STATUS_INVALID_INPUT)
                self.assertIsNone(result["result"])
                self.assertTrue(any("objective" in error.lower()
                                    for error in result["errors"]))

    def test_malformed_energy_containers_are_invalid_without_solving(self):
        invalid_energy = (
            None,
            [],
            {"solar_profile": None},
            {"solar_profile": []},
            {"tariff_profile": None},
            {"tariff_profile": []},
            {"unknown_profile": {}},
        )
        for energy in invalid_energy:
            with self.subTest(energy=energy):
                data = copy.deepcopy(VALID_WIDGET)
                data["energy"] = energy
                with patch("optimizer.public_api.create_baseline_schedule") as solve:
                    result = optimize(data)
                self.assertEqual(result["status"], STATUS_INVALID_INPUT)
                self.assertTrue(result["errors"])
                self.assertIsNone(result["result"])
                solve.assert_not_called()

    def test_energy_profile_keys_colliding_after_normalization_are_rejected(self):
        for profile_name in ("solar_profile", "tariff_profile"):
            with self.subTest(profile=profile_name):
                data = copy.deepcopy(VALID_WIDGET)
                data["energy"][profile_name] = {
                    "1": 0.2,
                    "01": 0.4,
                }
                with patch(
                        "optimizer.public_api.create_baseline_schedule") as solve:
                    result = optimize(json.dumps(data))
                self.assertEqual(result["status"], STATUS_INVALID_INPUT)
                self.assertTrue(any(
                    profile_name in error and "duplicate hour keys" in error
                    for error in result["errors"]
                ))
                solve.assert_not_called()

    def test_oversized_numeric_input_is_invalid_without_traceback(self):
        import contextlib
        import io

        huge_digits = "9" * 5000
        oversized_profile = copy.deepcopy(VALID_WIDGET)
        oversized_profile["energy"]["solar_profile"] = {huge_digits: 1}
        oversized_json = json.dumps(VALID_WIDGET).replace(
            '"planning_horizon_hours": 18',
            f'"planning_horizon_hours": {huge_digits}',
        )
        deeply_nested_json = (
            '{"factory":' + "[" * 1500 + "0" + "]" * 1500 + "}"
        )

        for source in (oversized_profile, oversized_json, deeply_nested_json):
            with self.subTest(source_type=type(source).__name__):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    result = optimize(source)
                self.assertEqual(result["status"], STATUS_INVALID_INPUT)
                self.assertIsNone(result["result"])
                self.assertTrue(result["errors"])
                self.assertNotIn("Traceback", stderr.getvalue())

    def test_extreme_finite_time_inputs_are_invalid(self):
        for field in ("planning_horizon_hours", "production_deadline",
                      "duration_hours", "earliest_start", "latest_finish"):
            with self.subTest(field=field):
                data = copy.deepcopy(VALID_WIDGET)
                target = (data["factory"] if field in (
                    "planning_horizon_hours", "production_deadline"
                ) else data["factory"]["processes"][0])
                target[field] = 1e308
                if field in ("planning_horizon_hours", "production_deadline"):
                    data["factory"]["planning_horizon_hours"] = 1e308
                    data["factory"]["production_deadline"] = 1e308
                with patch(
                        "optimizer.public_api.create_baseline_schedule") as solve:
                    result = optimize(data)
                self.assertEqual(result["status"], STATUS_INVALID_INPUT)
                self.assertTrue(result["errors"])
                self.assertIsNone(result["result"])
                solve.assert_not_called()

    def test_duplicate_json_properties_are_rejected(self):
        raw = json.dumps(VALID_WIDGET).replace(
            '"factory_name": "Demo Widget Lab"',
            '"factory_name": "Other", "factory_name": "Demo Widget Lab"',
            1,
        )
        with patch("optimizer.public_api.create_baseline_schedule") as solve:
            result = optimize(raw)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertIsNone(result["result"])
        self.assertTrue(any("duplicate property" in error
                            for error in result["errors"]))
        solve.assert_not_called()

    def test_invalid_utf8_json_file_is_invalid_input_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "invalid.json")
            with open(path, "wb") as handle:
                handle.write(b'{"factory":\xff}')

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = optimize(path)

        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertIsNone(result["result"])
        self.assertTrue(any("UTF-8" in error for error in result["errors"]))
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_options_are_validated_against_the_declared_contract(self):
        invalid_options = (
            None,
            [],
            {"max_time_second": 0.01},
            {"unexpected": True},
            {"objective": "banana"},
            {"max_time_seconds": "fast"},
            {"max_time_seconds": 0},
        )
        for options in invalid_options:
            with self.subTest(options=options):
                data = copy.deepcopy(VALID_WIDGET)
                data["options"] = options
                with patch("optimizer.public_api.create_baseline_schedule") as solve:
                    result = optimize(data)
                self.assertEqual(result["status"], STATUS_INVALID_INPUT)
                self.assertTrue(result["errors"])
                self.assertIsNone(result["result"])
                solve.assert_not_called()

        data = copy.deepcopy(VALID_WIDGET)
        data["options"] = {"objective": "solar", "max_time_seconds": None}
        self.assertEqual(optimize(data)["status"], STATUS_OPTIMAL)

    def test_runtime_rejects_nested_types_rejected_by_schema(self):
        changes = (
            ("factory", "factory_id", 7),
            ("process", "work_order_id", 7),
            ("process", "quantity_unit", 7),
            ("process", "process_name", ""),
            ("process", "machine_id", 7),
        )
        for scope, field, value in changes:
            data = copy.deepcopy(VALID_WIDGET)
            target = (data["factory"] if scope == "factory" else
                      data["factory"]["processes"][0])
            target[field] = value
            with self.subTest(scope=scope, field=field):
                result = optimize(data)
                self.assertEqual(result["status"], STATUS_INVALID_INPUT)
                if _HAS_JSONSCHEMA:
                    with self.assertRaises(jsonschema.ValidationError):
                        jsonschema.validate(data, public_api.get_input_schema())

    def test_partial_profiles_zero_missing_hours_and_schema_agrees(self):
        data = copy.deepcopy(VALID_WIDGET)
        data["energy"]["solar_profile"] = {0: 0}
        data["energy"]["tariff_profile"] = {0: 0.45}
        if _HAS_JSONSCHEMA:
            jsonschema.validate(data, public_api.get_input_schema())
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertTrue(any("does not define hours" in warning
                            for warning in result["warnings"]))
        for row in result["result"]["optimized"]["processes"]:
            expected = 0.45 if row["start_time"] < 1 else 0
            self.assertEqual(row["tariff"], expected)

    def test_timeout_is_per_solver_stage_and_unknown_is_not_internal_error(self):
        data = copy.deepcopy(VALID_WIDGET)
        data["options"] = {"max_time_seconds": 0.25}
        with patch("optimizer.public_api.create_cost_optimized_schedule",
                   side_effect=SolverUnknownError("time", 0.25)):
            result = optimize(data, strict=True)
        self.assertEqual(result["status"], STATUS_UNKNOWN)
        self.assertIsNone(result["error_category"])
        self.assertNotEqual(result["status"], STATUS_ERROR)

    def test_timeout_value_is_forwarded_to_each_solver_stage(self):
        data = copy.deepcopy(VALID_WIDGET)
        data["options"] = {"max_time_seconds": 0.375}
        with patch("optimizer.public_api.create_baseline_schedule",
                   wraps=public_api.create_baseline_schedule) as baseline, \
                patch("optimizer.public_api.create_cost_optimized_schedule",
                      wraps=public_api.create_cost_optimized_schedule) as optimized:
            result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertEqual(
            baseline.call_args.kwargs["max_time_seconds"], 0.375
        )
        self.assertEqual(
            optimized.call_args.kwargs["max_time_seconds"], 0.375
        )

    def test_schema_invalid_shapes_never_reach_solver(self):
        invalid_sources = []
        for energy in (None, [], "bad"):
            data = copy.deepcopy(VALID_WIDGET)
            data["energy"] = energy
            invalid_sources.append(data)
        for profile in (None, [], "bad"):
            for profile_name in ("solar_profile", "tariff_profile"):
                data = copy.deepcopy(VALID_WIDGET)
                data["energy"][profile_name] = profile
                invalid_sources.append(data)
        data = copy.deepcopy(VALID_WIDGET)
        data["energy"]["unknown_energy_field"] = True
        invalid_sources.append(data)
        data = copy.deepcopy(VALID_WIDGET)
        data["unknown_top_level_field"] = True
        invalid_sources.append(data)
        for options in (
                None, [], "bad", {"objective": "other"},
                {"max_time_seconds": 0},
                {"max_time_seconds": float("nan")},
                {"max_time_seconds": "fast"},
                {"max_time_second": 1},
                {"unknown_option": True}):
            data = copy.deepcopy(VALID_WIDGET)
            data["options"] = options
            invalid_sources.append(data)

        for source in invalid_sources:
            with self.subTest(source=source):
                with patch("optimizer.public_api.create_baseline_schedule") as solve:
                    result = optimize(source)
                self.assertEqual(result["status"], STATUS_INVALID_INPUT)
                self.assertTrue(result["errors"])
                self.assertIsNone(result["result"])
                solve.assert_not_called()

    def test_profile_hour_range_matches_json_schema(self):
        data = copy.deepcopy(VALID_WIDGET)
        data["energy"]["solar_profile"][24] = 1
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("invalid hour" in error
                            for error in result["errors"]))
        if _HAS_JSONSCHEMA:
            json_data = json.loads(json.dumps(data))
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(json_data, public_api.get_input_schema())

    def test_unknown_top_level_and_energy_fields_are_rejected(self):
        data = copy.deepcopy(VALID_WIDGET)
        data["objective"] = "cost"
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)

    def test_nested_schema_and_runtime_reject_the_same_unknown_fields(self):
        cases = []
        factory_extra = copy.deepcopy(VALID_WIDGET)
        factory_extra["factory"]["unexpected_factory_field"] = "x"
        cases.append(factory_extra)

        process_extra = copy.deepcopy(VALID_WIDGET)
        process_extra["factory"]["processes"][0]["unexpected_process_field"] = "x"
        cases.append(process_extra)

        machine_extra = copy.deepcopy(VALID_WIDGET)
        machine_extra["factory"]["machines"][0]["unexpected_machine_field"] = "x"
        cases.append(machine_extra)

        zero_duration = copy.deepcopy(VALID_WIDGET)
        zero_duration["factory"]["processes"][0]["duration_hours"] = 0
        cases.append(zero_duration)

        for data in cases:
            with self.subTest(data=data):
                result = optimize(data)
                self.assertEqual(result["status"], STATUS_INVALID_INPUT)
                self.assertIsNone(result["result"])
                if _HAS_JSONSCHEMA:
                    with self.assertRaises(jsonschema.ValidationError):
                        jsonschema.validate(data, public_api.get_input_schema())

        data = copy.deepcopy(VALID_WIDGET)
        data["energy"]["time_zone"] = "UTC"
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)

    def test_malformed_json_string(self):
        result = optimize("{not valid json")
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("Could not read input" in e for e in result["errors"]))

    def test_missing_file(self):
        result = optimize("definitely_missing_file_12345.json")
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("Could not read input" in e for e in result["errors"]))

    def test_unsupported_source_type(self):
        result = optimize(12345)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertEqual(result["error_category"], "VALIDATION_ERROR")

    def test_service_api_accepts_only_parsed_objects(self):
        for source in ("{}", "definitely_missing_service_path.json", None):
            with self.subTest(source=source):
                result = optimize_request(source)
                self.assertEqual(result["status"], STATUS_INVALID_INPUT)
                self.assertEqual(result["error_category"], "VALIDATION_ERROR")
                self.assertTrue(result["errors"])
        self.assertEqual(
            optimize_request(copy.deepcopy(VALID_WIDGET))["status"],
            STATUS_OPTIMAL,
        )

    def test_no_stack_traces_in_result(self):
        result = optimize("{broken json")
        text = json.dumps(result)
        self.assertNotIn("Traceback", text)
        self.assertNotIn(".py\"", text)

    def test_strict_mode_raises(self):
        with self.assertRaises(OptimizerInputError):
            optimize("{broken", strict=True)


class ContractStabilityTests(unittest.TestCase):
    """The public contract must be stable and machine-checkable."""

    def test_result_envelope_shape(self):
        result = optimize(copy.deepcopy(VALID_WIDGET))
        self.assertEqual(
            set(result.keys()),
            {"api_version", "status", "result", "errors", "warnings",
             "error_category"},
        )
        self.assertIsInstance(result["warnings"], list)
        self.assertEqual(result["api_version"], "2.0")
        payload = result["result"]
        for key in ("status", "factory_name", "objective", "baseline",
                    "optimized", "comparison", "machine_utilization",
                    "carbon", "validation_errors"):
            self.assertIn(key, payload)
        for key in ("makespan_baseline_hours", "cost_baseline", "cost_optimized",
                    "cost_savings", "cost_saving_percent",
                    "solar_utilization_percent", "shifted_processes"):
            self.assertIn(key, payload["comparison"])
        for key in ("status", "makespan_hours", "processes", "energy"):
            self.assertIn(key, payload["optimized"])
        for key in ("total_kwh", "solar_kwh", "grid_kwh"):
            self.assertIn(key, payload["optimized"]["energy"])
        row = payload["optimized"]["processes"][0]
        for key in ("process_id", "start_time", "end_time", "duration_hours",
                    "power_kw", "is_flexible", "machine_id", "solar_kwh",
                    "grid_kwh", "energy_cost", "tariff"):
            self.assertIn(key, row)
        diagnostics = payload["optimized"]["solver_diagnostics"]
        self.assertEqual(diagnostics["status"], payload["optimized"]["status"])
        self.assertGreaterEqual(diagnostics["optimality_gap"], 0)

    def test_schema_accessors(self):
        input_schema = public_api.get_input_schema()
        output_schema = public_api.get_output_schema()
        self.assertIn("factory", input_schema["properties"])
        self.assertIn("energy", input_schema["properties"])
        self.assertIn("options", input_schema["properties"])
        self.assertEqual(
            set(output_schema["properties"]["status"]["enum"]),
            {STATUS_OPTIMAL, STATUS_FEASIBLE, STATUS_INFEASIBLE,
             STATUS_UNKNOWN, STATUS_INVALID_INPUT, STATUS_ERROR},
        )

    def test_output_validates_against_json_schema(self):
        result = optimize(copy.deepcopy(VALID_WIDGET))
        if _HAS_JSONSCHEMA:
            jsonschema.validate(result, public_api.get_output_schema())
        else:
            # Fallback structural check when jsonschema is not installed
            self.assertIn(result["status"],
                          public_api.get_output_schema()
                          ["properties"]["status"]["enum"])

    def test_invalid_input_validates_against_json_schema(self):
        result = optimize({"factory": {"factory_name": "Broken"}})
        if _HAS_JSONSCHEMA:
            jsonschema.validate(result, public_api.get_output_schema())

    def test_input_schema_accepts_widget_lab(self):
        if _HAS_JSONSCHEMA:
            jsonschema.validate(
                json.loads(json.dumps(VALID_WIDGET)),
                public_api.get_input_schema(),
            )

    def test_engine_stays_agnostic(self):
        import inspect
        from optimizer import optimizer as engine
        source = inspect.getsource(engine).lower()
        for term in ("chocolate", "cosmetic", "beverage", "pharma",
                     "automotive", "furniture", "widget", "food"):
            self.assertNotIn(term, source, msg=f"engine mentions {term!r}")


if __name__ == "__main__":
    unittest.main()
