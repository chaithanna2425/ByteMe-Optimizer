"""Tests for read-only solver metrics emitted by the benchmark harness."""

import unittest

from benchmarks.bench_scale import build_case, measure


class BenchmarkMeasurementTests(unittest.TestCase):
    def test_measure_reports_each_solver_run_metrics(self):
        elapsed, result, peak_mb, solver_runs = measure(
            build_case(3, 8, "low")
        )

        self.assertGreaterEqual(elapsed, 0)
        self.assertGreaterEqual(peak_mb, 0)
        self.assertEqual(result["status"], "OPTIMAL")
        self.assertNotIn("branches", result["result"])
        self.assertNotIn("conflicts", result["result"])
        self.assertNotIn("deterministic_time_seconds", result["result"])
        self.assertEqual(len(solver_runs), 2)
        for stats in solver_runs:
            with self.subTest(status=stats["status"]):
                self.assertIn(stats["status"], ("OPTIMAL", "FEASIBLE"))
                self.assertGreaterEqual(stats["solve_time_seconds"], 0)
                self.assertGreaterEqual(stats["wall_time_seconds"], 0)
                self.assertGreaterEqual(stats["deterministic_time_seconds"], 0)
                self.assertGreaterEqual(stats["branches"], 0)
                self.assertGreaterEqual(stats["conflicts"], 0)
                self.assertGreater(stats["variables"], 0)
                self.assertGreaterEqual(stats["boolean_variables"], 0)
                self.assertGreater(stats["constraints"], 0)


if __name__ == "__main__":
    unittest.main()