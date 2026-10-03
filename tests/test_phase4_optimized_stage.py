import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ortools.sat.python import cp_model

from benchmarks import phase4_optimized_stage as benchmark
import optimizer.optimizer as optimizer_engine


class OptimizedStageBenchmarkTests(unittest.TestCase):
    def test_no_hints_flag_clears_model_hints_and_records_fingerprint(self):
        baseline = {"processes": []}
        config = SimpleNamespace(energy={})
        models = []

        def fake_optimized_schedule(*args, **kwargs):
            model = cp_model.CpModel()
            variable = model.NewIntVar(0, 1, "x")
            model.AddHint(variable, 1)
            models.append(model)
            solver = optimizer_engine._new_solver()
            solver.Solve(model)
            return {
                "status": "OPTIMAL",
                "solver_diagnostics": {},
                "processes": [],
                "makespan": 0,
            }

        with (
            patch.object(benchmark, "build_case", return_value={}),
            patch.object(benchmark, "load_user_input", return_value=config),
            patch.object(
                benchmark, "create_baseline_schedule", return_value=baseline
            ),
            patch.object(
                benchmark, "create_cost_optimized_schedule",
                side_effect=fake_optimized_schedule,
            ),
        ):
            rows = benchmark.run_case(
                "small", 1, 1, "low", ["shipped"], 5, None,
                use_hints=False,
            )

        self.assertFalse(rows[0]["hints"])
        fingerprint = rows[0]["samples"][0]["model_fingerprint"]
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(len(models), 1)
        proto = models[0].Proto()
        self.assertFalse(proto.has_solution_hint())
        self.assertEqual(
            fingerprint,
            hashlib.sha256(str(proto).encode("utf-8")).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
