"""
ByteMe Phase 2 Regression Tests (DEMO/SIMULATED DATA ONLY)

Coverage for the clean-architecture/efficiency refactor:
- the baseline is solved exactly ONCE per optimize() call (pinning reuses it)
- public_api.optimize() and the retained app.run_workflow() return
  equivalent optimization results
- both objectives still work; caller profiles, multi-day wrap and warnings
  are unaffected; schema/API contracts remain valid
"""

import copy
import json
import unittest
from unittest import mock

from optimizer import app, public_api
from optimizer.factory_data import AVAILABLE_FACTORIES
from optimizer.public_api import (
    STATUS_INFEASIBLE,
    STATUS_INVALID_INPUT,
    STATUS_OPTIMAL,
    optimize,
)

try:
    import jsonschema
    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False


VALID_INPUT = {
    "factory": {
        "factory_name": "Phase2 Probe Factory",
        "planning_horizon_hours": 18,
        "production_deadline": 16,
        "processes": [
            {"process_id": "a", "process_name": "A", "duration_hours": 1,
             "power_kw": 10, "dependencies": [], "is_flexible": False,
             "machine_id": "m1"},
            {"process_id": "b", "process_name": "B", "duration_hours": 1.5,
             "power_kw": 8, "dependencies": ["a"], "is_flexible": True,
             "machine_id": "m1"},
            {"process_id": "c", "process_name": "C", "duration_hours": 1,
             "power_kw": 6, "dependencies": ["a"], "is_flexible": True,
             "machine_id": "m2"},
        ],
        "machines": [
            {"machine_id": "m1", "machine_name": "M1", "capacity": 1,
             "availability": "single unit",
             "compatible_processes": ["a", "b"]},
            {"machine_id": "m2", "machine_name": "M2", "capacity": 1,
             "availability": "single unit",
             "compatible_processes": ["c"]},
        ],
    },
    "energy": {
        "solar_profile": {h: (20 if 9 <= h <= 15 else 0) for h in range(24)},
        "tariff_profile": {h: (0.10 if 9 <= h <= 15 else 0.50)
                           for h in range(24)},
        "grid_emission_factor": 0.4,
    },
}

SOLAR_WINDOW_PROFILE = {h: (20 if 9 <= h <= 15 else 0) for h in range(24)}
CHEAP_TARIFF = {h: (0.10 if 9 <= h <= 15 else 0.50) for h in range(24)}


def counting_baseline(counter):
    """
    Test seam: wrap create_baseline_schedule where the production code looks
    it up (public_api module namespace). No production behavior is changed.
    """
    real = public_api.create_baseline_schedule

    def _counting(*args, **kwargs):
        counter["calls"] += 1
        return real(*args, **kwargs)

    return _counting


class SingleBaselineSolveTests(unittest.TestCase):
    """(a) The baseline is solved exactly once per optimize() call."""

    def test_optimize_solves_baseline_exactly_once(self):
        counter = {"calls": 0}
        with mock.patch.object(public_api, "create_baseline_schedule",
                               counting_baseline(counter)):
            result = optimize(copy.deepcopy(VALID_INPUT))
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertEqual(
            counter["calls"], 1,
            msg=f"baseline solved {counter['calls']}x; pinning must reuse it",
        )

    def test_all_registered_factories_single_baseline_solve(self):
        from optimizer.app import review_input
        from optimizer.input_layer import validate_user_input

        real = public_api.create_baseline_schedule

        def make_counting(counter):
            def counting(*args, **kwargs):
                counter["calls"] += 1
                return real(*args, **kwargs)
            return counting

        for key, data in AVAILABLE_FACTORIES.items():
            with self.subTest(factory=key):
                source = review_input(validate_user_input({"factory": data}))
                counter = {"calls": 0}

                with mock.patch.object(public_api, "create_baseline_schedule",
                                       make_counting(counter)):
                    result = optimize(copy.deepcopy(source))
                self.assertEqual(result["status"], STATUS_OPTIMAL, msg=key)
                self.assertEqual(counter["calls"], 1, msg=key)

    def test_pinning_still_uses_baseline_positions(self):
        # Non-flexible 'a' must sit at its baseline position in the result.
        result = optimize(copy.deepcopy(VALID_INPUT))["result"]
        base_a = next(p for p in result["baseline"]["processes"]
                      if p["process_id"] == "a")
        opt_a = next(p for p in result["optimized"]["processes"]
                     if p["process_id"] == "a")
        self.assertEqual(base_a["start_time"], opt_a["start_time"])

    def test_infeasible_run_also_single_solve(self):
        infeasible = copy.deepcopy(VALID_INPUT)
        infeasible["factory"]["production_deadline"] = 2  # chain cannot fit
        counter = {"calls": 0}
        with mock.patch.object(public_api, "create_baseline_schedule",
                               counting_baseline(counter)):
            result = optimize(infeasible)
        self.assertEqual(result["status"], STATUS_INFEASIBLE)
        # Baseline itself is infeasible -> solved once, optimization skipped
        self.assertEqual(counter["calls"], 1)


class EquivalenceTests(unittest.TestCase):
    """(b) public_api.optimize() and app.run_workflow() agree."""

    def test_workflow_delegates_to_pipeline(self):
        # run_workflow must go through the same single pipeline
        self.assertIn("_run_pipeline", app.run_workflow.__code__.co_names
                      or () )
        import inspect
        source = inspect.getsource(app.run_workflow)
        self.assertIn("_run_pipeline", source)
        self.assertNotIn("create_baseline_schedule", source)
        self.assertNotIn("create_cost_optimized_schedule", source)

    def test_optimize_and_workflow_equivalent_results(self):
        for source in (copy.deepcopy(VALID_INPUT),
                       json.dumps(VALID_INPUT)):
            with self.subTest(source_type=type(source).__name__):
                api = optimize(copy.deepcopy(source))
                wf = app.run_workflow(copy.deepcopy(source))
                self.assertEqual(api["status"], wf["status"])
                self.assertAlmostEqual(
                    api["result"]["comparison"]["cost_baseline"],
                    wf["results"]["baseline"]["energy_cost"], places=6,
                )
                self.assertAlmostEqual(
                    api["result"]["comparison"]["cost_optimized"],
                    wf["results"]["optimized"]["energy_cost"], places=6,
                )
                self.assertEqual(
                    api["result"]["comparison"]["shifted_processes"],
                    wf["results"]["comparison"]["shifted_processes"],
                )
                # Same makespans, durations and process sets row-for-row.
                # NOTE: exact degenerate-tie start positions are NOT compared:
                # cost-equal schedules may legally differ across CP-SAT runs
                # (multithreaded search); determinism is a separate work item.
                for key in ("baseline", "optimized"):
                    api_rows = api["result"][key]["processes"]
                    wf_rows = wf["results"][f"{key}_schedule"]["processes"]
                    self.assertEqual(
                        [(r["process_id"], r["duration_hours"])
                         for r in api_rows],
                        [(r["process_id"], r["duration_hours"])
                         for r in wf_rows],
                    )
                    self.assertAlmostEqual(
                        api["result"][key]["makespan_hours"],
                        wf["results"][f"{key}_schedule"]["makespan"],
                        places=9,
                    )
                    # Both must be individually constraint-valid
                    for rows in (api_rows, wf_rows):
                        by_id = {r["process_id"]: r for r in rows}
                        self.assertLessEqual(by_id["a"]["end_time"],
                                             by_id["b"]["start_time"] + 1e-9)
                        self.assertLessEqual(by_id["a"]["end_time"],
                                             by_id["c"]["start_time"] + 1e-9)
                        # shared machine m1: a and b never overlap
                        self.assertTrue(
                            by_id["a"]["end_time"] <= by_id["b"]["start_time"] + 1e-9
                            or by_id["b"]["end_time"] <= by_id["a"]["start_time"] + 1e-9
                        )

    def test_workflow_single_baseline_solve_too(self):
        counter = {"calls": 0}
        with mock.patch.object(public_api, "create_baseline_schedule",
                               counting_baseline(counter)):
            result = app.run_workflow(copy.deepcopy(VALID_INPUT))
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertEqual(counter["calls"], 1)

    def test_workflow_invalid_input_shape_preserved(self):
        bad = copy.deepcopy(VALID_INPUT)
        bad["factory"]["processes"][1]["dependencies"].append("ghost")
        result = app.run_workflow(bad)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertIsNone(result["results"])
        self.assertTrue(result["validation_errors"])

    def test_workflow_infeasible_shape_preserved(self):
        infeasible = copy.deepcopy(VALID_INPUT)
        infeasible["factory"]["production_deadline"] = 2
        result = app.run_workflow(infeasible)
        self.assertEqual(result["status"], STATUS_INFEASIBLE)
        self.assertIsNone(result["results"])
        self.assertIsNone(result["validation_errors"])


class ObjectivesAndProfilesStillWorkTests(unittest.TestCase):
    """(c)-(f) Both objectives, caller profiles, multi-day, warnings."""

    def test_cost_objective_still_optimizes(self):
        result = optimize(copy.deepcopy(VALID_INPUT), objective="cost")
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertEqual(result["result"]["objective"], "cost")
        self.assertLessEqual(
            result["result"]["comparison"]["cost_optimized"],
            result["result"]["comparison"]["cost_baseline"] + 1e-6,
        )

    def test_solar_objective_still_optimizes(self):
        result = optimize(copy.deepcopy(VALID_INPUT), objective="solar")
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertEqual(result["result"]["objective"], "solar")
        # pinned 'a' cannot see solar (starts at 0); flexible b/c can shift
        solar = result["result"]["optimized"]["energy"]["solar_kwh"]
        self.assertGreaterEqual(solar, 0)
        b = next(p for p in result["result"]["optimized"]["processes"]
                 if p["process_id"] == "b")
        self.assertGreaterEqual(b["solar_kwh"], 0)

    def test_custom_profiles_still_honored(self):
        absurd = copy.deepcopy(VALID_INPUT)
        absurd["energy"]["tariff_profile"] = {h: 999.0 for h in range(24)}
        result = optimize(absurd)
        grid = result["result"]["baseline"]["energy"]["grid_kwh"]
        self.assertAlmostEqual(
            result["result"]["comparison"]["cost_baseline"], grid * 999.0,
            places=4,
        )

    def test_multi_day_cyclic_still_works(self):
        data = copy.deepcopy(VALID_INPUT)
        data["factory"]["planning_horizon_hours"] = 30
        data["factory"]["production_deadline"] = 29
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertTrue(any("cyclically repeated" in w
                            for w in result["warnings"]))

    def test_warnings_still_surface(self):
        data = copy.deepcopy(VALID_INPUT)
        del data["energy"]                      # DEMO fallback warning
        result = optimize(data)
        text = " ".join(result["warnings"])
        self.assertIn("DEMO/SIMULATED", text)

    def test_all_registered_factories_still_optimal(self):
        from optimizer.app import review_input
        from optimizer.input_layer import validate_user_input
        for key, data in AVAILABLE_FACTORIES.items():
            with self.subTest(factory=key):
                source = review_input(validate_user_input({"factory": data}))
                result = optimize(copy.deepcopy(source))
                self.assertEqual(result["status"], STATUS_OPTIMAL, msg=key)


class ContractStillValidTests(unittest.TestCase):
    """(g) Envelope, payload and JSON Schema contracts unchanged."""

    def test_envelope_shape_unchanged(self):
        result = optimize(copy.deepcopy(VALID_INPUT))
        self.assertEqual(
            set(result.keys()),
            {"api_version", "status", "result", "errors", "warnings",
             "error_category"},
        )
        self.assertEqual(result["api_version"], "2.0")

    def test_payload_fields_unchanged(self):
        payload = optimize(copy.deepcopy(VALID_INPUT))["result"]
        for key in ("status", "factory_name", "objective", "baseline",
                    "optimized", "comparison", "machine_utilization",
                    "carbon", "validation_errors", "warnings"):
            self.assertIn(key, payload)

    def test_output_schema_still_validates(self):
        if _HAS_JSONSCHEMA:
            jsonschema.validate(optimize(copy.deepcopy(VALID_INPUT)),
                                public_api.get_output_schema())

    def test_json_round_trip_still_lossless(self):
        result = optimize(copy.deepcopy(VALID_INPUT))
        again = json.loads(json.dumps(result))
        self.assertEqual(again, result)

    def test_engine_and_app_stay_industry_agnostic(self):
        import inspect
        from optimizer import optimizer as engine
        source = inspect.getsource(engine).lower()
        for term in ("chocolate", "cosmetic", "beverage", "pharma",
                     "automotive", "furniture", "widget", "food"):
            self.assertNotIn(term, source)


if __name__ == "__main__":
    unittest.main()
