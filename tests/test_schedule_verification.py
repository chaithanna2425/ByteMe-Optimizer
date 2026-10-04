import copy
import unittest
from unittest.mock import patch

from optimizer.app import build_results
from optimizer.input_layer import load_user_input
from optimizer.public_api import (
    STATUS_ERROR,
    STATUS_INVALID_INPUT,
    OptimizerInternalError,
    _run_pipeline,
    optimize,
    optimize_request,
)
from optimizer.resource_limits import OptimizerResourceLimits
from optimizer.verification import (
    ScheduleVerificationError,
    verify_result_metrics,
    verify_schedule,
)


def _request(with_dependency=True):
    return {
        "factory": {
            "factory_name": "Verification Test",
            "planning_horizon_hours": 4,
            "production_deadline": 4,
            "processes": [
                {
                    "process_id": "first",
                    "duration_hours": 1,
                    "power_kw": 2,
                    "machine_id": "shared",
                },
                {
                    "process_id": "second",
                    "duration_hours": 1,
                    "power_kw": 2,
                    "machine_id": "shared",
                    "dependencies": ["first"] if with_dependency else [],
                },
            ],
            "machines": [{"machine_id": "shared", "capacity": 1}],
        },
        "energy": {
            "solar_profile": {hour: 2 for hour in range(24)},
            "tariff_profile": {hour: 0.1 for hour in range(24)},
            "grid_emission_factor": 0.5,
        },
        "options": {"max_time_seconds": 2},
    }


class ScheduleVerificationTests(unittest.TestCase):
    def _get_schedules(self, request=None):
        config = load_user_input(request or _request())
        _, baseline, optimized = _run_pipeline(request or _request(), "cost")
        return config, baseline, optimized

    def test_verifies_solver_schedules_and_shared_energy_metrics(self):
        config, baseline, optimized = self._get_schedules()
        verify_schedule(config, baseline)
        verify_schedule(config, optimized, baseline=baseline)

    def test_rejects_dependency_violation(self):
        config, _, optimized = self._get_schedules()
        corrupted = copy.deepcopy(optimized)
        rows = {row["process_id"]: row for row in corrupted["processes"]}
        rows["second"]["start_time"] = 0.0
        rows["second"]["end_time"] = 1.0
        with self.assertRaisesRegex(
                ScheduleVerificationError, "dependency precedence"):
            verify_schedule(config, corrupted)

    def test_rejects_machine_capacity_violation(self):
        request = _request(with_dependency=False)
        config, _, optimized = self._get_schedules(request)
        corrupted = copy.deepcopy(optimized)
        rows = {row["process_id"]: row for row in corrupted["processes"]}
        rows["second"]["start_time"] = rows["first"]["start_time"]
        rows["second"]["end_time"] = rows["first"]["end_time"]
        with self.assertRaisesRegex(
                ScheduleVerificationError, "machine capacity"):
            verify_schedule(config, corrupted)

    def test_rejects_schedule_beyond_deadline_and_horizon(self):
        config, _, optimized = self._get_schedules()
        corrupted = copy.deepcopy(optimized)
        row = next(
            row for row in corrupted["processes"]
            if row["process_id"] == "second"
        )
        row["start_time"] = 3.5
        row["end_time"] = 4.5
        with self.assertRaisesRegex(
                ScheduleVerificationError, "planning bound"):
            verify_schedule(config, corrupted)

    def test_rejects_energy_or_cost_tampering(self):
        config, _, optimized = self._get_schedules()
        corrupted = copy.deepcopy(optimized)
        corrupted["processes"][0]["grid_energy_kwh"] += 1
        with self.assertRaisesRegex(
                ScheduleVerificationError, "energy equals duration times power"):
            verify_schedule(config, corrupted)

    def test_rejects_inconsistent_solver_optimality_diagnostics(self):
        config, _, optimized = self._get_schedules()
        corrupted = copy.deepcopy(optimized)
        corrupted["solver_diagnostics"]["optimality_gap"] = 0.5
        with self.assertRaisesRegex(
                ScheduleVerificationError, "solver objective bounds"):
            verify_schedule(config, corrupted)

    def test_rejects_corrupted_carbon_result_metric(self):
        config, baseline, optimized = self._get_schedules()
        results = build_results(config, baseline, optimized)
        results["carbon"]["optimized_co2_kg"] += 1
        with self.assertRaisesRegex(
                ScheduleVerificationError, "carbon metrics"):
            verify_result_metrics(config, baseline, optimized, results)

    def test_public_api_fails_closed_on_corrupted_solver_output(self):
        request = _request()
        from optimizer.optimizer import create_cost_optimized_schedule

        def corrupted_schedule(*args, **kwargs):
            schedule = create_cost_optimized_schedule(*args, **kwargs)
            schedule["processes"][0]["grid_energy_kwh"] += 1
            return schedule

        with patch(
                "optimizer.public_api.create_cost_optimized_schedule",
                side_effect=corrupted_schedule):
            result = optimize(request)
        self.assertEqual(result["status"], STATUS_ERROR)
        self.assertIsNone(result["result"])
        self.assertIn("ScheduleVerificationError", result["errors"][0])

        with (
            patch(
                "optimizer.public_api.create_cost_optimized_schedule",
                side_effect=corrupted_schedule,
            ),
            self.assertRaises(OptimizerInternalError),
        ):
            optimize(request, strict=True)

    def test_public_api_fails_closed_on_corrupted_projection(self):
        request = _request()
        from optimizer.public_api import _project_schedule

        def corrupted_projection(schedule):
            projected = _project_schedule(schedule)
            projected["energy"]["total_kwh"] += 1
            return projected

        with patch(
                "optimizer.public_api._project_schedule",
                side_effect=corrupted_projection):
            result = optimize(request)
        self.assertEqual(result["status"], STATUS_ERROR)
        self.assertIsNone(result["result"])

    def test_opt_in_resource_limits_reject_process_and_horizon_excess(self):
        limits = OptimizerResourceLimits(
            max_processes=1,
            max_planning_horizon_hours=2,
        )
        result = optimize_request(_request(), limits=limits)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertEqual(len(result["errors"]), 2)

    def test_solver_default_is_capped_without_mutating_request(self):
        request = _request()
        request.pop("options")
        marker = {"status": "test"}
        with patch(
                "optimizer.public_api._optimize_internal",
                return_value=marker) as run_pipeline:
            result = optimize_request(
                request,
                limits=OptimizerResourceLimits(
                    max_solver_seconds_per_stage=0.25
                ),
            )
        self.assertIs(result, marker)
        prepared_request = run_pipeline.call_args.args[0]
        self.assertEqual(prepared_request["options"]["max_time_seconds"], 0.25)
        self.assertNotIn("options", request)

    def test_explicit_solver_budget_above_limit_is_rejected(self):
        result = optimize_request(
            _request(),
            limits=OptimizerResourceLimits(
                max_solver_seconds_per_stage=0.25
            ),
        )
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertIn("max_solver_seconds_per_stage", result["errors"][0])

    def test_resource_limit_configuration_rejects_invalid_values(self):
        for kwargs in (
            {"max_processes": 0},
            {"max_planning_horizon_hours": float("inf")},
            {"max_solver_seconds_per_stage": float("nan")},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                OptimizerResourceLimits(**kwargs)

    def test_completion_log_has_correlation_and_solver_metadata_only(self):
        with self.assertLogs("optimizer.public_api", level="INFO") as captured:
            result = optimize_request(_request(), correlation_id="job-42")
        self.assertEqual(result["status"], "OPTIMAL")
        record = captured.records[-1]
        self.assertEqual(record.correlation_id, "job-42")
        self.assertEqual(record.objective, "cost")
        self.assertEqual(record.process_count, 2)
        self.assertEqual(record.optimizer_status, "OPTIMAL")
        self.assertEqual(len(record.request_sha256), 64)
        self.assertEqual(record.solver_stages["baseline"]["status"], "OPTIMAL")
        self.assertNotIn("Verification Test", record.getMessage())

    def test_invalid_correlation_id_is_rejected(self):
        with self.assertRaises(ValueError):
            optimize_request(_request(), correlation_id="bad\nid")


if __name__ == "__main__":
    unittest.main()
