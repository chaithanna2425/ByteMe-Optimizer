"""Smoke test the public API from a separately installed wheel."""

import os
from pathlib import Path

import optimizer
from optimizer.public_api import (
    STATUS_INVALID_INPUT,
    OptimizerInputError,
    get_input_schema,
    get_output_schema,
    optimize_request,
)

expected_package_root = os.environ.get("BYTEME_WHEEL_TARGET")
if expected_package_root:
    assert Path(optimizer.__file__).resolve().is_relative_to(
        Path(expected_package_root).resolve()
    )


request = {
    "factory": {
        "factory_name": "Installed Package Smoke Test",
        "planning_horizon_hours": 4,
        "production_deadline": 4,
        "processes": [
            {
                "process_id": "prep",
                "duration_hours": 0.5,
                "power_kw": 2,
                "machine_id": "line",
                "is_flexible": False,
            },
            {
                "process_id": "pack",
                "duration_hours": 0.5,
                "power_kw": 1,
                "machine_id": "line",
                "dependencies": ["prep"],
            },
        ],
        "machines": [{"machine_id": "line", "capacity": 1}],
    },
    "energy": {
        "solar_profile": {hour: 2 for hour in range(24)},
        "tariff_profile": {hour: 0.25 for hour in range(24)},
        "grid_emission_factor": 0.4,
    },
    "options": {"max_time_seconds": 2},
}

assert get_input_schema()["type"] == "object"
assert get_output_schema()["properties"]["status"]["type"] == "string"

result = optimize_request(request)
assert result["status"] in {"OPTIMAL", "FEASIBLE"}, result
payload = result["result"]
assert payload["baseline"]["processes"]
assert payload["optimized"]["processes"]
assert payload["optimized"]["energy"]["total_kwh"] > 0
assert payload["carbon"]["grid_emission_factor_kg_per_kwh"] == 0.4

invalid = optimize_request({"factory": {}})
assert invalid["status"] == STATUS_INVALID_INPUT
try:
    optimize_request({"factory": {}}, strict=True)
except OptimizerInputError:
    pass
else:
    raise AssertionError("strict mode did not reject invalid input")
