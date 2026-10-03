import unittest
from types import SimpleNamespace
from unittest.mock import patch

from optimizer import cli


class RunFactoryTests(unittest.TestCase):
    def test_reuses_baseline_for_cost_optimization(self):
        config = SimpleNamespace(factory_name="Test")
        baseline = {"processes": []}
        optimized = {"processes": []}

        with (
            patch.object(cli, "load_factory_config", return_value=config),
            patch.object(cli, "create_baseline_schedule", return_value=baseline) as solve_baseline,
            patch.object(
                cli, "create_cost_optimized_schedule", return_value=optimized
            ) as solve_optimized,
            patch.object(cli, "print_baseline_schedule"),
            patch.object(cli, "print_solar_aware_schedule"),
            patch.object(cli, "print_summary_metrics"),
        ):
            result = cli.run_factory({})

        solve_baseline.assert_called_once_with(config)
        solve_optimized.assert_called_once_with(
            config, baseline_schedule=baseline
        )
        self.assertEqual(result, (baseline, optimized))


if __name__ == "__main__":
    unittest.main()
