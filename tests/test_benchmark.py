"""Tests for read-only solver metrics emitted by the benchmark harness."""

import unittest
from unittest.mock import patch

from benchmarks import bench_scale
from benchmarks.bench_scale import build_case, measure


class BenchmarkMeasurementTests(unittest.TestCase):
    def test_cli_rejects_non_finite_solver_limits_before_running_cases(self):
        for value in ("nan", "inf"):
            with self.subTest(value=value):
                with patch("sys.argv", [
                        "bench_scale.py", "--quick", "--repeats", "1",
                        "--max-time-seconds", value,
                ]), patch.object(bench_scale, "measure") as run_measurement:
                    with self.assertRaises(SystemExit) as raised:
                        bench_scale.main()

                self.assertEqual(raised.exception.code, 2)
                run_measurement.assert_not_called()

    def test_measure_reports_each_solver_run_metrics(self):
        elapsed, result, peak_mb, instrumentation = measure(
            build_case(3, 8, "low"), track_memory=True
        )

        self.assertGreaterEqual(elapsed, 0)
        self.assertGreaterEqual(peak_mb, 0)
        self.assertGreaterEqual(instrumentation["input_validation_seconds"], 0)
        self.assertGreaterEqual(instrumentation["model_construction_seconds"], 0)
        self.assertEqual(result["status"], "OPTIMAL")
        self.assertNotIn("branches", result["result"])
        self.assertNotIn("conflicts", result["result"])
        self.assertNotIn("deterministic_time_seconds", result["result"])
        self.assertEqual(len(instrumentation["solver_runs"]), 2)
        for stats in instrumentation["solver_runs"]:
            with self.subTest(status=stats["status"]):
                self.assertIn(stats["status"], ("OPTIMAL", "FEASIBLE"))
                self.assertGreaterEqual(stats["solve_time_seconds"], 0)
                self.assertGreaterEqual(stats["model_construction_seconds"], 0)
                self.assertGreaterEqual(stats["base_model_seconds"], 0)
                self.assertGreaterEqual(
                    stats["start_domain_generation_seconds"], 0)
                self.assertGreaterEqual(stats["pinning_seconds"], 0)
                self.assertGreaterEqual(stats["solar_pool_seconds"], 0)
                self.assertGreaterEqual(
                    stats["objective_and_other_build_seconds"], 0)
                self.assertGreaterEqual(stats["wall_time_seconds"], 0)
                self.assertGreaterEqual(stats["deterministic_time_seconds"], 0)
                self.assertGreaterEqual(stats["branches"], 0)
                self.assertGreaterEqual(stats["conflicts"], 0)
                self.assertGreater(stats["variables"], 0)
                self.assertGreaterEqual(stats["boolean_variables"], 0)
                self.assertGreater(stats["constraints"], 0)
                self.assertEqual(len(stats["model_proto_sha256"]), 64)
                self.assertIsInstance(stats["objective_value"], (int, float))
                self.assertIsInstance(stats["best_bound"], (int, float))
                self.assertGreaterEqual(stats["optimality_gap"], 0)

    def test_disabling_hints_preserves_feasibility_and_optimum(self):
        case = build_case(3, 8, "low", max_time_seconds=10)
        _, with_hints, _, with_metrics = measure(case, use_hints=True)
        _, without_hints, _, without_metrics = measure(case, use_hints=False)

        self.assertEqual(with_hints["status"], "OPTIMAL")
        self.assertEqual(without_hints["status"], "OPTIMAL")
        self.assertEqual(
            with_metrics["solver_runs"][1]["objective_value"],
            without_metrics["solver_runs"][1]["objective_value"],
        )
        self.assertAlmostEqual(
            with_hints["result"]["comparison"]["cost_optimized"],
            without_hints["result"]["comparison"]["cost_optimized"],
        )

    def test_linear_expr_sum_matches_previous_schedule_and_objective(self):
        cases = (
            build_case(3, 8, "low", max_time_seconds=10),
            build_case(4, 10, "high", max_time_seconds=10),
            build_case(6, 12, "low", max_time_seconds=10),
        )
        for case in cases:
            for objective in ("cost", "solar"):
                case["options"]["objective"] = objective
                with self.subTest(factory=case["factory"]["factory_name"],
                                  objective=objective):
                    _, optimized_result, _, optimized_metrics = measure(
                        case, use_linear_expr_sum=True
                    )
                    _, reference_result, _, reference_metrics = measure(
                        case, use_linear_expr_sum=False
                    )

                    self.assertEqual(optimized_result["status"], "OPTIMAL")
                    self.assertEqual(reference_result["status"], "OPTIMAL")
                    for optimized_run, reference_run in zip(
                            optimized_metrics["solver_runs"],
                            reference_metrics["solver_runs"]):
                        self.assertEqual(optimized_run["variables"],
                                         reference_run["variables"])
                        self.assertEqual(optimized_run["constraints"],
                                         reference_run["constraints"])
                        self.assertEqual(optimized_run["model_proto_sha256"],
                                         reference_run["model_proto_sha256"])
                        self.assertEqual(optimized_run["objective_value"],
                                         reference_run["objective_value"])
                        self.assertEqual(optimized_run["best_bound"],
                                         reference_run["best_bound"])
                    for field in ("cost_baseline", "cost_optimized"):
                        self.assertEqual(
                            optimized_result["result"]["comparison"][field],
                            reference_result["result"]["comparison"][field],
                        )
                    for schedule_name in ("baseline", "optimized"):
                        self.assertEqual(
                            optimized_result["result"][schedule_name]["energy"],
                            reference_result["result"][schedule_name]["energy"],
                        )
                        optimized_rows = optimized_result["result"][
                            schedule_name
                        ]["processes"]
                        reference_rows = reference_result["result"][
                            schedule_name
                        ]["processes"]
                        self.assertEqual(
                            [
                                (row["process_id"], row["start_time"],
                                 row["end_time"])
                                for row in optimized_rows
                            ],
                            [
                                (row["process_id"], row["start_time"],
                                 row["end_time"])
                                for row in reference_rows
                            ],
                        )


if __name__ == "__main__":
    unittest.main()