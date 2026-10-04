"""Independent validation of schedules and reported energy metrics."""

import math
from typing import Any, NoReturn

from optimizer.models import TIME_SCALE, FactoryConfig

_TOLERANCE = 1e-6


class ScheduleVerificationError(RuntimeError):
    """Raised when an internally produced schedule violates an invariant."""


def _fail(invariant: str) -> NoReturn:
    raise ScheduleVerificationError(
        f"post-solve verification failed: {invariant}"
    )


def _close(actual, expected):
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return False
    try:
        return math.isfinite(actual) and math.isclose(
            actual, expected, rel_tol=0.0, abs_tol=_TOLERANCE
        )
    except OverflowError:
        return False


def _aligned(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and math.isclose(
            value * TIME_SCALE, round(value * TIME_SCALE),
            rel_tol=0.0, abs_tol=_TOLERANCE,
        )
    )


def _number(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        _fail("numeric schedule values")
    try:
        number = float(value)
    except OverflowError:
        _fail("finite schedule values")
    if not math.isfinite(number):
        _fail("finite schedule values")
    return number


def _profile_value(profile, hour):
    return profile.get(hour % 24, 0.0)


def _allocate_reported_energy(rows, config):
    """Recompute the documented shared-solar allocation from schedule times."""
    energy_data = getattr(config, "energy", {})
    solar_profile = energy_data["solar_profile"]
    tariff_profile = energy_data["tariff_profile"]
    running_by_slot = {}
    for row in rows:
        tick = round(row["start_time"] * TIME_SCALE)
        end_tick = round(row["end_time"] * TIME_SCALE)
        while tick < end_tick:
            running_by_slot.setdefault(tick, []).append(
                (row["process_id"], row["power_kw"] / TIME_SCALE)
            )
            tick += 1

    allocation = {
        row["process_id"]: {
            "solar": 0.0,
            "grid": 0.0,
            "cost": 0.0,
        }
        for row in rows
    }
    for tick, runners in running_by_slot.items():
        hour = tick // TIME_SCALE
        solar_kw = _profile_value(solar_profile, hour)
        remaining_solar = solar_kw / TIME_SCALE
        tariff = _profile_value(tariff_profile, hour)
        for process_id, demand in sorted(runners):
            solar = min(demand, solar_kw / TIME_SCALE, max(remaining_solar, 0.0))
            grid = demand - solar
            allocation[process_id]["solar"] += solar
            allocation[process_id]["grid"] += grid
            allocation[process_id]["cost"] += grid * tariff
            remaining_solar -= solar
    return allocation


def verify_schedule(config: FactoryConfig, schedule, baseline=None):
    """Check a solved schedule against validated inputs and recomputed metrics."""
    if not isinstance(schedule, dict) or schedule.get("status") not in {
        "OPTIMAL", "FEASIBLE",
    }:
        _fail("solver status")
    diagnostics = schedule.get("solver_diagnostics")
    if (not isinstance(diagnostics, dict)
            or diagnostics.get("status") != schedule.get("status")):
        _fail("solver diagnostics status")
    objective_value = _number(diagnostics.get("best_objective"))
    best_bound = _number(diagnostics.get("best_bound"))
    gap = _number(diagnostics.get("optimality_gap"))
    expected_gap = abs(objective_value - best_bound) / max(
        abs(objective_value), 1.0
    )
    if (gap < 0 or not math.isclose(
            gap, expected_gap, rel_tol=0.0, abs_tol=_TOLERANCE)
            or (schedule["status"] == "OPTIMAL"
                and not _close(objective_value, best_bound))):
        _fail("solver objective bounds")

    expected_processes = {
        process.process_id: process for process in config.processes
    }
    rows = schedule.get("processes")
    if not isinstance(rows, list) or len(rows) != len(expected_processes):
        _fail("process coverage")
    by_id = {row.get("process_id"): row for row in rows
             if isinstance(row, dict)}
    if len(by_id) != len(rows) or set(by_id) != set(expected_processes):
        _fail("process identity")

    deadline = min(config.production_deadline, config.planning_horizon_hours)
    for process_id, process in expected_processes.items():
        row = by_id[process_id]
        start = _number(row.get("start_time"))
        end = _number(row.get("end_time"))
        if (not _aligned(start) or not _aligned(end) or start < 0
                or end > deadline + _TOLERANCE
                or not _close(end - start, process.duration_hours)):
            _fail("duration, time grid, or planning bound")
        earliest = process.earliest_start or 0
        if start + _TOLERANCE < earliest:
            _fail("earliest start")
        finish_limit = (
            process.latest_finish
            if process.latest_finish is not None
            else deadline
        )
        if end > finish_limit + _TOLERANCE:
            _fail("latest finish")
        expected_machine_capacity = (
            config.get_machine(process.machine_id).capacity
            if process.machine_id is not None else None
        )
        if (row.get("machine_id") != process.machine_id
                or type(row.get("capacity_units")) is not int
                or row.get("capacity_units") != process.capacity_units
                or (expected_machine_capacity is not None
                    and type(row.get("machine_capacity")) is not int)
                or row.get("machine_capacity") != expected_machine_capacity
                or row.get("process_name") != process.process_name
                or row.get("is_flexible") is not process.is_flexible
                or not _close(
                    row.get("duration_hours"), process.duration_hours
                )
                or not _close(row.get("power_kw"), process.power_kw)):
            _fail("process attributes")
        quantity = row.get("quantity")
        if isinstance(process.quantity, (int, float)) and not isinstance(
                process.quantity, bool):
            if not _close(quantity, process.quantity):
                _fail("process attributes")
        elif quantity != process.quantity:
            _fail("process attributes")
        if not _close(
            process.power_kw * (end - start),
            row.get("solar_energy_kwh", 0.0)
            + row.get("grid_energy_kwh", 0.0),
        ):
            _fail("energy equals duration times power")

    for process_id, process in expected_processes.items():
        start = by_id[process_id]["start_time"]
        for dependency_id in process.dependencies:
            if by_id[dependency_id]["end_time"] > start + _TOLERANCE:
                _fail("dependency precedence")

    machine_capacity = {
        machine.machine_id: machine.capacity for machine in config.machines
    }
    events: dict[str, list[tuple[float, int]]] = {}
    for row in rows:
        machine_id = row["machine_id"]
        if machine_id is None:
            continue
        events.setdefault(machine_id, []).extend((
            (row["start_time"], row["capacity_units"]),
            (row["end_time"], -row["capacity_units"]),
        ))
    for machine_id, machine_events in events.items():
        load = 0
        for _, change in sorted(machine_events):
            load += change
            if load < 0 or load > machine_capacity[machine_id]:
                _fail("machine capacity")

    if baseline is not None:
        baseline_starts = {
            row["process_id"]: row["start_time"]
            for row in baseline["processes"]
        }
        for process_id, process in expected_processes.items():
            if (not process.is_flexible
                    and not _close(
                        by_id[process_id]["start_time"],
                        baseline_starts[process_id],
                    )):
                _fail("non-flexible process pinning")

    allocation = _allocate_reported_energy(rows, config)
    tariff_profile = getattr(config, "energy", {}).get("tariff_profile", {})
    for process_id, row in by_id.items():
        expected = allocation[process_id]
        if (not _close(row.get("solar_energy_kwh"), expected["solar"])
                or not _close(row.get("grid_energy_kwh"), expected["grid"])
                or not _close(row.get("energy_cost"), expected["cost"])
                or not _close(
                    row.get("tariff"),
                    _profile_value(tariff_profile, int(row["start_time"])),
                )):
            _fail("solar, grid, or cost allocation")

    energy = sum(
        process.power_kw * process.duration_hours
        for process in config.processes
    )
    solar = sum(values["solar"] for values in allocation.values())
    grid = sum(values["grid"] for values in allocation.values())
    cost = sum(values["cost"] for values in allocation.values())
    if (not _close(schedule.get("makespan"), max(
            row["end_time"] for row in rows))
            or not _close(schedule.get("total_energy_kwh"), energy)
            or not _close(schedule.get("total_solar_kwh"), solar)
            or not _close(schedule.get("total_grid_kwh"), grid)
            or not _close(schedule.get("total_energy_cost"), cost)):
        _fail("schedule summary metrics")


def verify_result_metrics(config: FactoryConfig, baseline, optimized, results):
    """Independently check aggregates derived from the verified schedules."""
    for name, schedule in (("baseline", baseline), ("optimized", optimized)):
        summary = results.get(name, {})
        expected_cost = sum(
            row["energy_cost"] for row in schedule["processes"]
        )
        if (summary.get("status") != schedule.get("status")
                or not _close(summary.get("makespan_hours"), schedule["makespan"])
                or not _close(
                    summary.get("total_energy_kwh"),
                    schedule["total_energy_kwh"],
                )
                or not _close(summary.get("solar_kwh"),
                              schedule["total_solar_kwh"])
                or not _close(summary.get("grid_kwh"),
                              schedule["total_grid_kwh"])
                or not _close(summary.get("energy_cost"), expected_cost)):
            _fail(f"{name} result aggregates")

    baseline_cost = results["baseline"]["energy_cost"]
    optimized_cost = results["optimized"]["energy_cost"]
    comparison = results.get("comparison", {})
    savings = baseline_cost - optimized_cost
    saving_percent = savings / baseline_cost * 100 if baseline_cost > 0 else 0.0
    total_energy = optimized["total_energy_kwh"]
    solar_percent = (
        optimized["total_solar_kwh"] / total_energy * 100
        if total_energy > 0 else 0.0
    )
    if (not _close(comparison.get("cost_savings"), savings)
            or not _close(comparison.get("cost_saving_percent"),
                          saving_percent)
            or not _close(comparison.get("solar_utilization_percent"),
                          solar_percent)
            or comparison.get("shifted_processes") != sum(
                1
                for process in config.processes
                if process.is_flexible
                and abs(
                    next(
                        row["start_time"]
                        for row in optimized["processes"]
                        if row["process_id"] == process.process_id
                    )
                    - next(
                        row["start_time"]
                        for row in baseline["processes"]
                        if row["process_id"] == process.process_id
                    )
                ) > 0.1
            )):
        _fail("comparison metrics")

    reported_utilization = results.get("machine_utilization", {})
    if not isinstance(reported_utilization, dict):
        _fail("machine utilization metrics")
    for schedule_name, schedule in (
            ("baseline", baseline), ("optimized", optimized)):
        expected_utilization: dict[str, dict[str, Any]] = {}
        for row in schedule["processes"]:
            machine_id = row["machine_id"]
            if machine_id is None:
                continue
            metric = expected_utilization.setdefault(machine_id, {
                "busy_hours": 0.0,
                "capacity_unit_hours": 0.0,
                "capacity": row["machine_capacity"],
                "events": [],
            })
            duration = row["end_time"] - row["start_time"]
            demand = row["capacity_units"]
            metric["busy_hours"] += duration
            metric["capacity_unit_hours"] += demand * duration
            metric["events"].extend((
                (row["start_time"], demand),
                (row["end_time"], -demand),
            ))
        makespan = schedule["makespan"] or 1.0
        expected_machine_results = {}
        for machine_id, metric in expected_utilization.items():
            active = peak = 0
            for _, change in sorted(metric["events"]):
                active += change
                peak = max(peak, active)
            expected_machine_results[machine_id] = {
                "busy_hours": round(metric["busy_hours"], 4),
                "capacity_unit_hours": round(metric["capacity_unit_hours"], 4),
                "capacity": metric["capacity"],
                "peak_capacity_units": peak,
                "utilization_percent": round(
                    metric["capacity_unit_hours"]
                    / (metric["capacity"] * makespan) * 100,
                    2,
                ),
            }
        actual_utilization = reported_utilization.get(schedule_name)
        if (not isinstance(actual_utilization, dict)
                or set(actual_utilization) != set(expected_machine_results)):
            _fail("machine utilization metrics")
        for machine_id, expected in expected_machine_results.items():
            actual = actual_utilization[machine_id]
            if (not isinstance(actual, dict) or set(actual) != set(expected)
                    or any(
                        not _close(actual.get(key), value)
                        for key, value in expected.items()
                        if key != "capacity"
                    )
                    or actual.get("capacity") != expected["capacity"]):
                _fail("machine utilization metrics")

    energy_data = getattr(config, "energy", {})
    emission_factor = energy_data.get("grid_emission_factor")
    carbon = results.get("carbon")
    if emission_factor is None:
        if carbon is not None:
            _fail("carbon availability")
    else:
        baseline_co2 = baseline["total_grid_kwh"] * emission_factor
        optimized_co2 = optimized["total_grid_kwh"] * emission_factor
        reduction = baseline_co2 - optimized_co2
        if (not isinstance(carbon, dict)
                or not _close(
                    carbon.get("grid_emission_factor_kg_per_kwh"),
                    emission_factor,
                )
                or not _close(carbon.get("baseline_co2_kg"), baseline_co2)
                or not _close(carbon.get("optimized_co2_kg"), optimized_co2)
                or not _close(carbon.get("co2_reduction_kg"), reduction)
                or not _close(
                    carbon.get("co2_reduction_percent"),
                    reduction / baseline_co2 * 100
                    if baseline_co2 > 0 else 0.0,
                )):
            _fail("carbon metrics")


def verify_public_result(baseline, optimized, results, public_result):
    """Check the public projection still matches the independently checked data."""
    for name, schedule in (("baseline", baseline), ("optimized", optimized)):
        projected = public_result.get(name)
        if (not isinstance(projected, dict)
                or projected.get("status") != schedule.get("status")
                or not _close(
                    projected.get("makespan_hours"), schedule["makespan"]
                )
                or projected.get("solver_diagnostics")
                != schedule.get("solver_diagnostics")
                or not _close(
                    projected.get("solve_time_seconds"),
                    schedule.get("solve_time_seconds"),
                )):
            _fail(f"{name} public schedule summary")
        projected_rows = projected.get("processes")
        if not isinstance(projected_rows, list):
            _fail(f"{name} public process rows")
        internal_rows = {
            row["process_id"]: row for row in schedule["processes"]
        }
        public_rows = {
            row.get("process_id"): row for row in projected_rows
            if isinstance(row, dict)
        }
        if (len(public_rows) != len(projected_rows)
                or set(public_rows) != set(internal_rows)):
            _fail(f"{name} public process identity")
        field_map = {
            "process_id": "process_id",
            "process_name": "process_name",
            "start_time": "start_time",
            "end_time": "end_time",
            "duration_hours": "duration_hours",
            "power_kw": "power_kw",
            "is_flexible": "is_flexible",
            "machine_id": "machine_id",
            "capacity_units": "capacity_units",
            "machine_capacity": "machine_capacity",
            "quantity": "quantity",
            "solar_kwh": "solar_energy_kwh",
            "grid_kwh": "grid_energy_kwh",
            "energy_cost": "energy_cost",
            "tariff": "tariff",
        }
        for process_id, internal in internal_rows.items():
            public = public_rows[process_id]
            for public_field, internal_field in field_map.items():
                actual = public.get(public_field)
                expected = internal.get(internal_field)
                if isinstance(expected, (int, float)) and not isinstance(
                        expected, bool):
                    if not _close(actual, expected):
                        _fail(f"{name} public process metrics")
                elif actual != expected:
                    _fail(f"{name} public process attributes")
        energy = projected.get("energy")
        if (not isinstance(energy, dict)
                or not _close(
                    energy.get("total_kwh"), schedule["total_energy_kwh"]
                )
                or not _close(
                    energy.get("solar_kwh"), schedule["total_solar_kwh"]
                )
                or not _close(
                    energy.get("grid_kwh"), schedule["total_grid_kwh"]
                )):
            _fail(f"{name} public energy summary")

    comparison = public_result.get("comparison", {})
    expected_comparison = results["comparison"]
    comparison_fields = (
        "makespan_baseline_hours", "makespan_optimized_hours",
        "cost_baseline", "cost_optimized",
        "cost_savings", "cost_saving_percent",
        "solar_utilization_percent",
    )
    expected_public_values = {
        "makespan_baseline_hours": results["baseline"]["makespan_hours"],
        "makespan_optimized_hours": results["optimized"]["makespan_hours"],
        "cost_baseline": results["baseline"]["energy_cost"],
        "cost_optimized": results["optimized"]["energy_cost"],
        **expected_comparison,
    }
    if (not isinstance(comparison, dict)
            or public_result.get("status") != optimized.get("status")
            or any(
                not _close(comparison.get(field), expected_public_values[field])
                for field in comparison_fields
            )
            or comparison.get("shifted_processes")
            != expected_comparison["shifted_processes"]
            or public_result.get("machine_utilization")
            != results.get("machine_utilization")
            or public_result.get("carbon") != results.get("carbon")):
        _fail("public result metrics")
