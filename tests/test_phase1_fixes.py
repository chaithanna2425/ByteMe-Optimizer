"""
ByteMe Phase 1 Regression Tests (DEMO/SIMULATED DATA ONLY)

Regression coverage for the integration-readiness fixes:
- caller-supplied energy profiles reach optimization AND reporting
- no silent fallback to DEMO profiles when profiles are supplied
- multi-day horizons use cyclic 24-hour profiles (documented policy)
- warnings channel (additive envelope key, backward compatible)
- NaN/Infinity rejected everywhere numeric input is accepted
"""

import copy
import json
import unittest

from optimizer import public_api
from optimizer.public_api import (
    STATUS_INVALID_INPUT,
    STATUS_OPTIMAL,
    optimize,
)

try:
    import jsonschema
    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False


BASE_FACTORY = {
    "factory": {
        "factory_name": "Phase1 Probe Factory",
        "planning_horizon_hours": 18,
        "production_deadline": 16,
        "processes": [
            {"process_id": "a", "process_name": "Process A",
             "duration_hours": 1, "power_kw": 10, "dependencies": [],
             "is_flexible": False, "machine_id": "m1"},
            {"process_id": "b", "process_name": "Process B",
             "duration_hours": 1.5, "power_kw": 8,
             "dependencies": ["a"], "is_flexible": True, "machine_id": "m1"},
            {"process_id": "c", "process_name": "Process C",
             "duration_hours": 1, "power_kw": 6,
             "dependencies": ["a"], "is_flexible": True, "machine_id": "m2"},
        ],
        "machines": [
            {"machine_id": "m1", "machine_name": "Machine 1",
             "capacity": 1, "availability": "single unit",
             "compatible_processes": ["a", "b"]},
            {"machine_id": "m2", "machine_name": "Machine 2",
             "capacity": 1, "availability": "single unit",
             "compatible_processes": ["c"]},
        ],
    }
}


def with_energy(solar, tariff, emission=0.4):
    data = copy.deepcopy(BASE_FACTORY)
    data["energy"] = {
        "solar_profile": solar,
        "tariff_profile": tariff,
        "grid_emission_factor": emission,
    }
    return data


DEMO_SOLAR_DAY = {h: (20 if 9 <= h <= 15 else 0) for h in range(24)}
DEMO_TARIFF_DAY = {h: (0.10 if 9 <= h <= 15 else 0.50) for h in range(24)}


class ProfilePlumbingTests(unittest.TestCase):
    """(1) Caller-supplied profiles drive optimization and reporting."""

    def test_custom_tariff_changes_reported_cost(self):
        # Identical factory; only the tariff differs. Reported baseline cost
        # must scale with the caller's tariff.
        low = optimize(with_energy(DEMO_SOLAR_DAY,
                                   {h: 0.10 for h in range(24)}))
        high = optimize(with_energy(DEMO_SOLAR_DAY,
                                    {h: 10.0 for h in range(24)}))
        self.assertEqual(low["status"], STATUS_OPTIMAL)
        self.assertEqual(high["status"], STATUS_OPTIMAL)
        low_cost = low["result"]["comparison"]["cost_baseline"]
        high_cost = high["result"]["comparison"]["cost_baseline"]
        # Same energy, 100x tariff => exactly 100x cost
        self.assertAlmostEqual(high_cost, low_cost * 100, places=4,
                               msg="custom tariff not used in reporting")
        self.assertGreater(low_cost, 0)

    def test_custom_tariff_changes_optimization_decision(self):
        # Peak tariff at the natural (earliest) slot forces flexible
        # processes to shift; flat cheap tariff keeps them early.
        peak = optimize(with_energy(
            {h: 0 for h in range(24)},                       # no solar
            {h: (5.0 if 0 <= h <= 6 else 0.05) for h in range(24)},
        ))
        flat = optimize(with_energy(
            {h: 0 for h in range(24)},
            {h: 0.05 for h in range(24)},
        ))
        self.assertEqual(peak["status"], STATUS_OPTIMAL)
        self.assertEqual(flat["status"], STATUS_OPTIMAL)
        peak_y = next(p for p in peak["result"]["optimized"]["processes"]
                      if p["process_id"] == "b")
        flat_y = next(p for p in flat["result"]["optimized"]["processes"]
                      if p["process_id"] == "b")
        self.assertGreater(peak_y["start_time"], 6.0,
                           "flexible process did not avoid expensive hours")
        self.assertEqual(flat_y["start_time"], 1.0,
                         "flat cheap tariff should not force shifting")

    def test_custom_solar_changes_schedule_and_totals(self):
        # All-day solar -> optimized schedule can be fully solar-powered
        sun = optimize(with_energy({h: 50 for h in range(24)},
                                   {h: 0.50 for h in range(24)}))
        night = optimize(with_energy({h: 0 for h in range(24)},
                                     {h: 0.50 for h in range(24)}))
        self.assertEqual(sun["status"], STATUS_OPTIMAL)
        sun_solar = sun["result"]["optimized"]["energy"]["solar_kwh"]
        night_solar = night["result"]["optimized"]["energy"]["solar_kwh"]
        self.assertGreater(sun_solar, 0, "all-day solar produced no solar kWh")
        self.assertEqual(night_solar, 0)

    def test_profiles_used_consistently_for_baseline_and_optimized(self):
        result = optimize(with_energy(DEMO_SOLAR_DAY, DEMO_TARIFF_DAY))["result"]
        # Recompute every row cost from the reported times using the SAME
        # caller tariff profile; must match reported costs exactly.
        tariff = DEMO_TARIFF_DAY
        for schedule_key in ("baseline", "optimized"):
            rows = result[schedule_key]["processes"]
            # Shared-pool recomputation (Phase 4.3): split each hourly
            # solar pool greedily in process_id order, then recompute
            # every row's cost from its share.
            expected_by_pid = {r["process_id"]: 0.0 for r in rows}
            running = {}
            for row in sorted(rows, key=lambda r: r["process_id"]):
                t = row["start_time"]
                while t < row["end_time"]:
                    h = int(t)
                    dur = min(h + 1, row["end_time"]) - t
                    running.setdefault(h, []).append(
                        (row["process_id"], row["power_kw"] * dur, dur))
                    t += dur
            for h, runners in running.items():
                supply = DEMO_SOLAR_DAY.get(h, 0) * 1.0
                for pid, energy, dur in runners:
                    draw_cap = DEMO_SOLAR_DAY.get(h, 0) * dur
                    s = min(energy, draw_cap, max(supply, 0.0))
                    supply -= s
                    expected_by_pid[pid] += (energy - s) * tariff.get(h, 0)
            for row in rows:
                self.assertAlmostEqual(
                    row["energy_cost"], expected_by_pid[row["process_id"]],
                    places=6,
                    msg=f"{schedule_key} row cost mismatch")
        # Comparison totals equal the row sums
        for key, sched_key in (("cost_baseline", "baseline"),
                               ("cost_optimized", "optimized")):
            total = sum(r["energy_cost"]
                        for r in result[sched_key]["processes"])
            self.assertAlmostEqual(result["comparison"][key], total, places=6)

    def test_no_silent_demo_fallback_when_profiles_supplied(self):
        # Absurd flat tariff: reported cost must equal reported grid kWh
        # x 999 exactly (DEMO fallback would give ~28 kWh x 0.5 = 14).
        absurd = optimize(with_energy(DEMO_SOLAR_DAY,
                                      {h: 999.0 for h in range(24)}))
        self.assertEqual(absurd["status"], STATUS_OPTIMAL)
        grid = absurd["result"]["baseline"]["energy"]["grid_kwh"]
        self.assertGreater(grid, 0)
        self.assertAlmostEqual(
            absurd["result"]["comparison"]["cost_baseline"], grid * 999.0,
            places=4, msg="DEMO tariff fallback detected",
        )

    def test_emission_factor_reaches_carbon(self):
        cheap = optimize(with_energy(DEMO_SOLAR_DAY, DEMO_TARIFF_DAY,
                                     emission=0.1))
        dear = optimize(with_energy(DEMO_SOLAR_DAY, DEMO_TARIFF_DAY,
                                    emission=2.0))
        c_cheap = cheap["result"]["carbon"]
        c_dear = dear["result"]["carbon"]
        self.assertAlmostEqual(
            c_dear["baseline_co2_kg"], c_cheap["baseline_co2_kg"] * 20, places=6,
            msg="custom emission factor not used in carbon reporting",
        )


class MultiDayHorizonTests(unittest.TestCase):
    """(2) Multi-day horizons: cyclic 24-hour profiles, explicit warning."""

    def test_multi_day_horizon_solves_with_cyclic_profiles(self):
        # 30-hour horizon: hour 25 must wrap to profile hour 1.
        solar = {h: 50 for h in range(24)}
        tariff = {h: 0.10 for h in range(24)}
        data = with_energy(solar, tariff)
        data["factory"]["planning_horizon_hours"] = 30
        data["factory"]["production_deadline"] = 29
        data["factory"]["processes"].append({
            "process_id": "late", "process_name": "Late Shift",
            "duration_hours": 2, "power_kw": 10, "dependencies": ["b"],
            "is_flexible": True,
        })
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        # Energy totals must still be complete (no silent zero-energy hours)
        total = result["result"]["optimized"]["energy"]["total_kwh"]
        expected_total = sum(
            p["duration_hours"] * p["power_kw"]
            for p in result["result"]["optimized"]["processes"]
        )
        self.assertAlmostEqual(total, expected_total, places=6)

    def test_multi_day_cyclic_window_reported_as_warning(self):
        data = with_energy(DEMO_SOLAR_DAY, DEMO_TARIFF_DAY)
        data["factory"]["planning_horizon_hours"] = 30
        data["factory"]["production_deadline"] = 29
        result = optimize(data)
        self.assertTrue(
            any("cyclically repeated" in w for w in result["warnings"]),
            msg=f"expected cyclic-profile warning, got: {result['warnings']}",
        )

    def test_profiles_still_rejected_outside_0_23(self):
        data = with_energy({h: 5 for h in range(24)}, DEMO_TARIFF_DAY)
        data["energy"]["solar_profile"][25] = 7
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("must be integer 0-23" in e
                            for e in result["errors"]))


class WarningsChannelTests(unittest.TestCase):
    """(3) Warnings channel: additive, non-fatal, backward compatible."""

    def test_envelope_always_has_warnings_list(self):
        for data in (with_energy(DEMO_SOLAR_DAY, DEMO_TARIFF_DAY),
                     {"factory": {"factory_name": "Broken"}}):
            result = optimize(copy.deepcopy(data))
            self.assertIsInstance(result["warnings"], list)

    def test_demo_fallback_surfaces_warning(self):
        data = copy.deepcopy(BASE_FACTORY)   # no energy section at all
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        text = " ".join(result["warnings"])
        self.assertIn("DEMO/SIMULATED", text)
        self.assertIn("solar", text)
        self.assertIn("tariff", text)

    def test_explicit_profiles_produce_no_fallback_warning(self):
        result = optimize(with_energy(DEMO_SOLAR_DAY, DEMO_TARIFF_DAY))
        self.assertFalse(
            any("DEMO/SIMULATED solar profile is used" in w
                for w in result["warnings"]),
            msg="fallback warning raised although profiles were supplied",
        )

    def test_invalid_input_can_carry_warnings(self):
        data = with_energy(DEMO_SOLAR_DAY, DEMO_TARIFF_DAY)
        data["energy"]["tariff_profile"] = {h: 0.1 for h in range(12)}  # partial + broken proc
        data["factory"]["processes"][1]["duration_hours"] = -1
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(result["errors"])
        # partial tariff collected a warning before the error was raised
        self.assertTrue(any("does not define hours" in w
                            for w in result["warnings"]))

    def test_result_payload_carries_warnings(self):
        result = optimize(with_energy(DEMO_SOLAR_DAY, DEMO_TARIFF_DAY))
        self.assertIn("warnings", result["result"])
        self.assertIsInstance(result["result"]["warnings"], list)

    def test_schema_includes_warnings(self):
        schema = public_api.get_output_schema()
        self.assertIn("warnings", schema["properties"])
        if _HAS_JSONSCHEMA:
            jsonschema.validate(
                optimize(with_energy(DEMO_SOLAR_DAY, DEMO_TARIFF_DAY)),
                schema,
            )


class NumericHardeningTests(unittest.TestCase):
    """(4) NaN/Infinity rejected everywhere numeric input is accepted."""

    def _expect_invalid(self, data, fragment):
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT,
                         msg=f"expected INVALID INPUT, got {result['errors']}")
        self.assertTrue(
            any(fragment in e for e in result["errors"]),
            msg=f"expected error containing {fragment!r}, got {result['errors']}",
        )

    def test_nan_horizon(self):
        data = copy.deepcopy(BASE_FACTORY)
        data["factory"]["planning_horizon_hours"] = float("nan")
        self._expect_invalid(data, "planning_horizon_hours")

    def test_infinite_deadline(self):
        data = copy.deepcopy(BASE_FACTORY)
        data["factory"]["production_deadline"] = float("inf")
        self._expect_invalid(data, "production_deadline")

    def test_nan_duration(self):
        data = copy.deepcopy(BASE_FACTORY)
        data["factory"]["processes"][0]["duration_hours"] = float("nan")
        self._expect_invalid(data, "duration_hours")

    def test_infinite_power(self):
        data = copy.deepcopy(BASE_FACTORY)
        data["factory"]["processes"][0]["power_kw"] = float("inf")
        self._expect_invalid(data, "power_kw")

    def test_nan_earliest_start(self):
        data = copy.deepcopy(BASE_FACTORY)
        data["factory"]["processes"][2]["earliest_start"] = float("nan")
        self._expect_invalid(data, "earliest_start")

    def test_infinite_machine_capacity(self):
        data = copy.deepcopy(BASE_FACTORY)
        data["factory"]["machines"][0]["capacity"] = float("inf")
        self._expect_invalid(data, "capacity")

    def test_nan_solar_value(self):
        solar = {h: 5.0 for h in range(24)}
        solar[12] = float("nan")
        self._expect_invalid(with_energy(solar, DEMO_TARIFF_DAY),
                             "solar_profile")

    def test_infinite_tariff_value(self):
        tariff = {h: 0.1 for h in range(24)}
        tariff[3] = float("inf")
        self._expect_invalid(with_energy(DEMO_SOLAR_DAY, tariff),
                              "tariff_profile")

    def test_nan_emission_factor(self):
        self._expect_invalid(
            with_energy(DEMO_SOLAR_DAY, DEMO_TARIFF_DAY, emission=float("nan")),
            "grid_emission_factor",
        )

    def test_nan_rejected_via_json_string(self):
        # json.loads accepts NaN by default; the optimizer must not
        payload = json.dumps(copy.deepcopy(BASE_FACTORY)) \
            .replace('"power_kw": 10', '"power_kw": NaN')
        self._expect_invalid(json.loads(payload), "power_kw")

    def test_negative_values_still_rejected(self):
        # Sanity: pre-existing negative-value rejection still works
        data = copy.deepcopy(BASE_FACTORY)
        data["factory"]["processes"][0]["power_kw"] = -2
        self._expect_invalid(data, "power_kw")


if __name__ == "__main__":
    unittest.main()
