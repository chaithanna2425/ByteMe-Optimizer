"""Synthetic tests for the normalized factory-data adapter boundary."""

import unittest

from optimizer.public_api import optimize_request


SOURCE_A = {
    "plant": {"key": "plant-a", "label": "Synthetic Plant"},
    "operations": [{
        "code": "cut",
        "order": "wo-1",
        "hours": 1,
        "kw": 2,
        "resource": "cell-a",
        "start_after": [],
        "committed_start": 1,
        "units": "piece",
    }],
}

SOURCE_B = {
    "factory": {"id": "plant-a", "name": "Synthetic Plant"},
    "work_orders": [{
        "id": "wo-1",
        "steps": [{
            "step_id": "cut",
            "duration": 1,
            "load_kw": 2,
            "machine_ref": {"id": "cell-a"},
            "predecessors": [],
            "scheduled_start_hour": 1,
            "quantity_unit": "piece",
        }],
    }],
}


def normalize_source_a(source):
    operation = source["operations"][0]
    start = operation["committed_start"]
    duration = operation["hours"]
    return {
        "factory": {
            "factory_id": source["plant"]["key"],
            "factory_name": source["plant"]["label"],
            "planning_horizon_hours": 4,
            "production_deadline": 4,
            "processes": [{
                "process_id": operation["code"],
                "work_order_id": operation["order"],
                "duration_hours": duration,
                "power_kw": operation["kw"],
                "quantity_unit": operation["units"],
                "dependencies": operation["start_after"],
                "machine_id": "machine-1",
                "is_flexible": False,
                "earliest_start": start,
                "latest_finish": start + duration,
            }],
            "machines": [{"machine_id": "machine-1", "capacity": 1}],
        },
        "energy": {
            "solar_profile": {0: 0},
            "tariff_profile": {0: 0.25},
        },
    }


def normalize_source_b(source):
    factory = source["factory"]
    order = source["work_orders"][0]
    step = order["steps"][0]
    start = step["scheduled_start_hour"]
    duration = step["duration"]
    return {
        "factory": {
            "factory_id": factory["id"],
            "factory_name": factory["name"],
            "planning_horizon_hours": 4,
            "production_deadline": 4,
            "processes": [{
                "process_id": step["step_id"],
                "work_order_id": order["id"],
                "duration_hours": duration,
                "power_kw": step["load_kw"],
                "quantity_unit": step["quantity_unit"],
                "dependencies": step["predecessors"],
                "machine_id": "machine-1",
                "is_flexible": False,
                "earliest_start": start,
                "latest_finish": start + duration,
            }],
            "machines": [{"machine_id": "machine-1", "capacity": 1}],
        },
        "energy": {
            "solar_profile": {0: 0},
            "tariff_profile": {0: 0.25},
        },
    }


class AdapterContractTests(unittest.TestCase):
    def test_distinct_source_shapes_normalize_and_schedule_identically(self):
        normalized_a = normalize_source_a(SOURCE_A)
        normalized_b = normalize_source_b(SOURCE_B)
        self.assertEqual(normalized_a, normalized_b)

        result_a = optimize_request(normalized_a)
        result_b = optimize_request(normalized_b)
        self.assertEqual(result_a["status"], "OPTIMAL")
        self.assertEqual(result_b["status"], "OPTIMAL")
        self.assertEqual(
            result_a["result"]["optimized"]["processes"],
            result_b["result"]["optimized"]["processes"],
        )
        row = result_a["result"]["optimized"]["processes"][0]
        self.assertEqual(row["start_time"], 1)
        self.assertEqual(row["end_time"], 2)


if __name__ == "__main__":
    unittest.main()