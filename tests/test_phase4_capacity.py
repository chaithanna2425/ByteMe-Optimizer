"""
ByteMe Phase 4.2 Tests: Machine Capacity as a Real Constraint
(DEMO/SIMULATED DATA ONLY)

capacity=1  -> NoOverlap (unchanged behavior)
capacity>=2 -> up to `capacity` processes may run simultaneously
capacity<=0 / non-integer -> rejected with clear INVALID INPUT
"""

import copy
import unittest

from optimizer.public_api import (
    STATUS_INVALID_INPUT,
    STATUS_OPTIMAL,
    optimize,
)


def cap_factory(capacity, n_processes, tariff=None):
    """n_processes independent, flexible, same machine, cheap-window tariff."""
    tariff = tariff or {h: (0.1 if h < 4 else 0.9) for h in range(24)}
    processes = [
        {"process_id": f"p{i}", "process_name": f"P{i}",
         "duration_hours": 2, "power_kw": 5, "dependencies": [],
         "is_flexible": True, "machine_id": "shared"}
        for i in range(n_processes)
    ]
    return {
        "factory": {
            "factory_name": f"Capacity {capacity}",
            "planning_horizon_hours": 24,
            "production_deadline": 20,
            "processes": processes,
            "machines": [
                {"machine_id": "shared", "machine_name": "Shared Machine",
                 "capacity": capacity, "availability": f"capacity {capacity}",
                 "compatible_processes": [p["process_id"]
                                          for p in processes]},
            ],
        },
        "energy": {
            "solar_profile": {h: 0 for h in range(24)},
            "tariff_profile": tariff,
        },
    }


def weighted_capacity_factory(machine_capacity, demands):
    processes = [
        {"process_id": f"p{index}", "process_name": f"P{index}",
         "duration_hours": 2, "power_kw": 5, "dependencies": [],
         "is_flexible": True, "machine_id": "shared",
         "capacity_units": demand}
        for index, demand in enumerate(demands)
    ]
    return {
        "factory": {
            "factory_name": "Weighted Capacity Probe",
            "planning_horizon_hours": 6,
            "production_deadline": 6,
            "processes": processes,
            "machines": [{
                "machine_id": "shared", "machine_name": "Shared",
                "capacity": machine_capacity,
                "compatible_processes": [p["process_id"] for p in processes],
            }],
        },
        "energy": {
            "solar_profile": {h: 0 for h in range(24)},
            "tariff_profile": {h: 0.1 for h in range(24)},
        },
    }


def intervals_by_id(result):
    return {
        p["process_id"]: (p["start_time"], p["end_time"])
        for p in result["result"]["optimized"]["processes"]
    }


def max_concurrent(intervals):
    """Maximum number of simultaneously running processes."""
    events = []
    for start, end in intervals.values():
        events.append((start, 1))
        events.append((end, -1))
    current = peak = 0
    for _, delta in sorted(events):
        current += delta
        peak = max(peak, current)
    return peak


class CapacityConstraintTests(unittest.TestCase):
    def test_capacity_one_serializes(self):
        result = optimize(cap_factory(1, 3))
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        ivs = intervals_by_id(result)
        self.assertEqual(max_concurrent(ivs), 1)

    def test_capacity_two_allows_pairwise_parallel(self):
        # 3 processes x 2h into a 4h cheap window needs capacity >= 2
        result = optimize(cap_factory(2, 3))
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        ivs = intervals_by_id(result)
        self.assertGreaterEqual(max_concurrent(ivs), 2,
                                "capacity=2 still serialized processes")
        self.assertLessEqual(max_concurrent(ivs), 2,
                             "capacity exceeded")

    def test_capacity_three_allows_triple_parallel(self):
        result = optimize(cap_factory(3, 3))
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        ivs = intervals_by_id(result)
        # All three fit in the cheap window simultaneously
        self.assertEqual(max_concurrent(ivs), 3)

    def test_capacity_never_exceeded(self):
        # NOTE: 6 identical processes create heavy symmetry - the optimality
        # PROOF may hit the 60s limit (status FEASIBLE). The schedule is
        # valid either way; the invariant under test is capacity-respect.
        for capacity in (1, 2, 3, 5):
            with self.subTest(capacity=capacity):
                result = optimize(cap_factory(capacity, 6))
                self.assertIn(result["status"],
                              (STATUS_OPTIMAL, "FEASIBLE"))
                self.assertLessEqual(
                    max_concurrent(intervals_by_id(result)), capacity)

    def test_mixed_machine_capacities(self):
        data = cap_factory(1, 4)
        # Second machine with capacity 2; move half the processes there
        data["factory"]["machines"].append(
            {"machine_id": "big", "machine_name": "Big Cell",
             "capacity": 2, "availability": "capacity 2",
             "compatible_processes": ["p2", "p3"]})
        for p in data["factory"]["processes"][2:]:
            p["machine_id"] = "big"
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        ivs = intervals_by_id(result)
        shared = {k: v for k, v in ivs.items() if k in ("p0", "p1")}
        big = {k: v for k, v in ivs.items() if k in ("p2", "p3")}
        self.assertEqual(max_concurrent(shared), 1)
        self.assertLessEqual(max_concurrent(big), 2)

    def test_invalid_capacities_rejected(self):
        for bad in (0, -1, 2.5, "two", True):
            with self.subTest(capacity=bad):
                data = cap_factory(bad, 2)
                # bypass client-side type variety: schemas want int-like
                result = optimize(data)
                self.assertEqual(result["status"], STATUS_INVALID_INPUT,
                                 msg=f"capacity={bad!r} not rejected")
                self.assertTrue(any("capacity" in e for e in result["errors"]))

    def test_missing_capacity_defaults_to_one(self):
        data = cap_factory(1, 2)
        del data["factory"]["machines"][0]["capacity"]
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertEqual(max_concurrent(intervals_by_id(result)), 1)

    def test_capacity_eases_cost_optimization(self):
        # With expensive hours after hour 4 and 3 processes x 2h:
        # capacity 1 must spill into expensive hours; capacity 3 fits all
        # three in the cheap window -> strictly lower cost.
        tariff = {h: (0.1 if h < 4 else 0.9) for h in range(24)}
        c1 = optimize(cap_factory(1, 3, tariff))
        c3 = optimize(cap_factory(3, 3, tariff))
        self.assertEqual(c1["status"], STATUS_OPTIMAL)
        self.assertEqual(c3["status"], STATUS_OPTIMAL)
        self.assertLess(
            c3["result"]["comparison"]["cost_optimized"] + 1e-6,
            c1["result"]["comparison"]["cost_optimized"],
            msg="capacity=3 should avoid expensive hours entirely",
        )

    def test_process_capacity_units_use_cumulative_machine_demand(self):
        result = optimize(weighted_capacity_factory(4, [1, 2, 3]))
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        rows = result["result"]["optimized"]["processes"]
        boundaries = sorted({
            point for row in rows
            for point in (row["start_time"], row["end_time"])
        })
        maximum_demand = 0
        demand_by_id = {f"p{index}": demand
                        for index, demand in enumerate((1, 2, 3))}
        for left, right in zip(boundaries, boundaries[1:]):
            midpoint = (left + right) / 2
            running_demand = sum(
                demand_by_id[row["process_id"]] for row in rows
                if row["start_time"] <= midpoint < row["end_time"]
            )
            maximum_demand = max(maximum_demand, running_demand)
            self.assertLessEqual(running_demand, 4)
        self.assertEqual(maximum_demand, 4)

    def test_capacity_units_default_to_one(self):
        data = cap_factory(2, 3)
        result = optimize(data)
        self.assertEqual(result["status"], STATUS_OPTIMAL)
        self.assertEqual(len(result["result"]["optimized"]["processes"]), 3)

    def test_invalid_or_oversized_capacity_units_are_rejected(self):
        for demand in (0, -1, 1.5, True):
            with self.subTest(demand=demand):
                result = optimize(weighted_capacity_factory(4, [demand]))
                self.assertEqual(result["status"], STATUS_INVALID_INPUT)
                self.assertTrue(any("capacity_units" in error
                                    for error in result["errors"]))

            integral_float = optimize(weighted_capacity_factory(4, [1.0]))
            self.assertEqual(integral_float["status"], STATUS_OPTIMAL)

        result = optimize(weighted_capacity_factory(2, [3]))
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("capacity_units" in error
                            for error in result["errors"]))

        unassigned = cap_factory(4, 1)
        unassigned["factory"]["processes"][0]["capacity_units"] = 2
        del unassigned["factory"]["processes"][0]["machine_id"]
        result = optimize(unassigned)
        self.assertEqual(result["status"], STATUS_INVALID_INPUT)
        self.assertTrue(any("machine_id" in error
                    for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
