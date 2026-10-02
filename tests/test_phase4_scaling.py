"""
ByteMe Phase 4.1 Tests: Integer-Scaling Bound Validation
(DEMO/SIMULATED DATA ONLY)

The internal CP-SAT encoding has documented scaling limits:
    cost  = power_kw * tariff * duration_hours * 1000  <= 1e8 (per process)
    solar = min(solar_kw, power_kw) * duration_hours * 10 <= 1e6 (per process)

Below/at the limit: valid. Above: INVALID INPUT naming the offending
process - never a mysterious INFEASIBLE.
"""

import copy
import unittest

from optimizer.models import (
    max_supported_cost_per_process,
    max_supported_solar_per_process,
)
from optimizer.public_api import (
    STATUS_INVALID_INPUT,
    STATUS_OPTIMAL,
    optimize,
)

FLAT_TARIFF = {h: 0.1 for h in range(24)}
NO_SOLAR = {h: 0 for h in range(24)}


def factory_with(processes):
    return {
        "factory": {
            "factory_name": "Scaling Probe",
            "planning_horizon_hours": 10,
            "production_deadline": 9,
            "processes": processes,
            "machines": [
                {"machine_id": "m1", "machine_name": "M1", "capacity": 1,
                 "availability": "single unit",
                 "compatible_processes": [
                     p["process_id"] for p in processes]},
            ],
        },
        "energy": {
            "solar_profile": NO_SOLAR,
            "tariff_profile": FLAT_TARIFF,
        },
    }


def one_process(power, tariff=None, duration=2.0):
    tariff = tariff if tariff is not None else FLAT_TARIFF
    data = factory_with([
        {"process_id": "big", "process_name": "Big Process",
         "duration_hours": duration, "power_kw": power,
         "dependencies": [], "is_flexible": True,
         "machine_id": "m1"},
    ])
    data["energy"]["tariff_profile"] = dict(tariff)
    return data, tariff


class ScalingBoundTests(unittest.TestCase):
    def test_constants_are_documented_and_consistent(self):
        self.assertEqual(max_supported_cost_per_process(), 100_000)
        self.assertEqual(max_supported_solar_per_process(), 100_000)

    def test_just_below_cost_limit_is_valid(self):
        # 499,999 * 0.1 * 2 = 99,999.8 (< 100,000) -> 8e7 < 1e8
        data, _ = one_process(power=499_999)
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)

    def test_exactly_at_cost_limit_is_valid(self):
        # 500,000 * 0.1 * 2 = 100,000 (== limit) -> 1e8 == domain
        data, _ = one_process(power=500_000)
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)

    def test_just_above_cost_limit_is_invalid_input(self):
        # 500,001 * 0.1 * 2 = 100,000.2 (> 100,000) -> > 1e8
        data, _ = one_process(power=500_001)
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any(
            "integer cost scaling" in e and "'big'" in e
            for e in result["errors"]),
            msg=f"expected named scaling error, got: {result['errors']}")

    def test_high_tariff_also_triggers_bound(self):
        # power * tariff * duration product: 2,000 * 10 * 2 = 40,000 (ok) vs
        # 6,000 * 10 * 2 = 120,000 (over)
        ok, high_tariff = one_process(power=2_000,
                                      tariff={h: 10.0 for h in range(24)})
        self.assertEqual(optimize(ok)["status"], STATUS_OPTIMAL)

        over, high_tariff = one_process(power=6_000,
                                        tariff={h: 10.0 for h in range(24)})
        result = optimize(over)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("integer cost scaling" in e
                            for e in result["errors"]))

    def test_solar_scaling_bound(self):
        # min(solar, power) * duration * 10 <= 1e6 -> product <= 100,000
        solar = {h: 200_000 for h in range(24)}    # huge availability
        data, _ = one_process(power=60_000, duration=2.0)
        data["energy"]["solar_profile"] = solar
        # 60,000 * 2 * 10 = 1.2e6 > 1e6 -> INVALID INPUT
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("integer solar scaling" in e
                            for e in result["errors"]))

        # exactly at the solar bound is fine
        data2, _ = one_process(power=50_000, duration=2.0)
        data2["energy"]["solar_profile"] = solar
        self.assertEqual(optimize(data2)["status"], STATUS_OPTIMAL)

    def test_error_names_the_offending_process(self):
        # 99,999 x 10 x 2 = 1,999,980 > 100,000 -> clearly over, names 'big'
        data, _ = one_process(power=99_999,
                              tariff={h: 10.0 for h in range(24)})
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("'big'" in e for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()
