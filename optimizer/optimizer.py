"""
ByteMe Manufacturing Energy Scheduler - Version 3

This module implements a solar-aware production scheduler using Google OR-Tools CP-SAT.
It generates valid schedules that shift flexible manufacturing operations toward periods
of higher solar availability and lower electricity tariffs while respecting production
constraints and deadlines.

Current capabilities:
- Factory Data → OR-Tools → Valid Production Schedule
- Baseline scheduling (minimizes makespan)
- Solar-aware demand shifting for flexible processes
- Electricity tariff / energy cost optimization (Version 3)
- Respects planning horizon and production deadline
- Enforces process dependencies/precedence constraints
- Calculates solar energy vs grid energy consumption
- Calculates monetary energy cost from DEMO tariff data

Future versions may add:
- Real tariff / weather data integration
- Advanced demand shifting strategies
"""

from ortools.sat.python import cp_model
from optimizer.factory_data import DEMO_CHOCOLATE_FACTORY


# DEMO Solar Availability Profile
# Hourly solar power availability in kW (DEMO/SIMULATED values)
DEMO_SOLAR_PROFILE = {
    0: 0,    # Midnight
    1: 0,    # 1 AM
    2: 0,    # 2 AM
    3: 0,    # 3 AM
    4: 0,    # 4 AM
    5: 0,    # 5 AM
    6: 5,    # 6 AM - sunrise
    7: 15,   # 7 AM
    8: 30,   # 8 AM
    9: 45,   # 9 AM
    10: 60,  # 10 AM
    11: 70,  # 11 AM
    12: 75,  # 12 PM - peak solar
    13: 70,  # 1 PM
    14: 60,  # 2 PM
    15: 45,  # 3 PM
    16: 30,  # 4 PM
    17: 15,  # 5 PM
    18: 5,   # 6 PM - sunset
    19: 0,   # 7 PM
    20: 0,   # 8 PM
    21: 0,   # 9 PM
    22: 0,   # 10 PM
    23: 0,   # 11 PM
}


# DEMO Electricity Tariff Profile
# Hourly electricity tariff in currency units per kWh (DEMO/SIMULATED values,
# NOT real electricity prices). Generic structure: hour -> tariff per kWh.
DEMO_TARIFF_PROFILE = {
    0: 0.50,  # Midnight
    1: 0.50,  # 1 AM
    2: 0.50,  # 2 AM
    3: 0.50,  # 3 AM
    4: 0.45,  # 4 AM
    5: 0.40,  # 5 AM
    6: 0.30,  # 6 AM
    7: 0.20,  # 7 AM
    8: 0.15,  # 8 AM
    9: 0.10,  # 9 AM
    10: 0.08,  # 10 AM - cheap midday solar hours
    11: 0.06,  # 11 AM
    12: 0.05,  # 12 PM - cheapest (peak solar)
    13: 0.06,  # 1 PM
    14: 0.08,  # 2 PM
    15: 0.10,  # 3 PM
    16: 0.15,  # 4 PM
    17: 0.25,  # 5 PM
    18: 0.40,  # 6 PM - evening ramp
    19: 0.50,  # 7 PM
    20: 0.55,  # 8 PM - evening peak
    21: 0.55,  # 9 PM
    22: 0.55,  # 10 PM
    23: 0.50,  # 11 PM
}


def get_tariff(hour):
    """
    Get the DEMO electricity tariff for a specific hour.

    Args:
        hour: Hour of the day (0-23)

    Returns:
        Tariff in currency units per kWh (DEMO value, not a real price)
    """
    return DEMO_TARIFF_PROFILE.get(hour, 0)


def get_solar_availability(hour):
    """
    Get solar availability for a specific hour from the DEMO profile.

    Args:
        hour: Hour of the day (0-23)

    Returns:
        Solar power availability in kW
    """
    return DEMO_SOLAR_PROFILE.get(hour, 0)


def calculate_solar_energy(process_start, process_end, process_power, time_scale=2):
    """
    Calculate solar energy used by a process during its actual scheduled duration.

    Args:
        process_start: Process start time in hours
        process_end: Process end time in hours
        process_power: Process power consumption in kW
        time_scale: Time scaling factor for precision

    Returns:
        Tuple of (solar_energy_kwh, grid_energy_kwh, total_energy_kwh)
    """
    total_energy = 0
    solar_energy = 0
    grid_energy = 0

    # Iterate through each time slot the process occupies
    current_time = process_start
    while current_time < process_end:
        hour = int(current_time)
        hour_end = min(hour + 1, process_end)
        duration_in_hour = hour_end - current_time

        # Get solar availability for this hour
        solar_available = get_solar_availability(hour)

        # Calculate energy used in this time slot based on process power consumption
        energy_in_slot = process_power * duration_in_hour

        # Solar can only supply up to available capacity
        # Solar energy used cannot exceed the process energy requirement
        solar_in_slot = min(solar_available * duration_in_hour, energy_in_slot)
        grid_in_slot = energy_in_slot - solar_in_slot

        total_energy += energy_in_slot
        solar_energy += solar_in_slot
        grid_energy += grid_in_slot

        current_time = hour_end

    return solar_energy, grid_energy, total_energy


def create_baseline_schedule(factory_data):
    """
    Create a baseline production schedule (Version 1 - minimize makespan).

    Args:
        factory_data: Dictionary containing factory configuration and processes

    Returns:
        Dictionary with solver status and scheduled process times, or None if infeasible
    """
    # OR-Tools CP-SAT requires integer values
    # Scale time units by a factor to handle fractional hours
    TIME_SCALE = 2  # 1 unit = 0.5 hours (half-hour precision)

    # Extract factory parameters and scale to integer units
    planning_horizon = int(factory_data["planning_horizon_hours"] * TIME_SCALE)
    production_deadline = int(factory_data["production_deadline"] * TIME_SCALE)
    processes = factory_data["processes"]

    # Create CP-SAT model
    model = cp_model.CpModel()

    # Create start-time and end-time variables for each process
    start_times = {}
    end_times = {}

    for process in processes:
        process_id = process["process_id"]
        duration = int(process["duration_hours"] * TIME_SCALE)

        # Start time variable: must be within planning horizon
        start_times[process_id] = model.NewIntVar(
            0, planning_horizon, f"start_{process_id}"
        )

        # End time variable: must be within planning horizon
        end_times[process_id] = model.NewIntVar(
            0, planning_horizon, f"end_{process_id}"
        )

        # Constraint: end_time = start_time + duration
        model.Add(end_times[process_id] == start_times[process_id] + duration)

        # Constraint: process must finish before production deadline
        model.Add(end_times[process_id] <= production_deadline)

    # Add dependency/precedence constraints
    for process in processes:
        process_id = process["process_id"]
        dependencies = process["dependencies"]

        for dep_id in dependencies:
            # Dependency process must finish before this process starts
            model.Add(end_times[dep_id] <= start_times[process_id])

    # Objective: minimize the overall production completion time (makespan)
    makespan = model.NewIntVar(0, planning_horizon, "makespan")
    for process_id in start_times:
        model.Add(makespan >= end_times[process_id])
    model.Minimize(makespan)

    # Solve the model
    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    # Check if solution is feasible
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        # Build schedule result
        schedule = {
            "status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            "makespan": solver.Value(makespan) / TIME_SCALE,
            "processes": []
        }

        for process in processes:
            process_id = process["process_id"]
            start_time = solver.Value(start_times[process_id]) / TIME_SCALE
            end_time = solver.Value(end_times[process_id]) / TIME_SCALE

            # Energy and grid cost of the baseline plan under the same
            # solar/tariff conditions (used for baseline cost comparison)
            solar_energy, grid_energy, total_energy = calculate_solar_energy(
                start_time, end_time, process["power_kw"], TIME_SCALE
            )
            energy_cost = calculate_process_cost(
                start_time, end_time, process["power_kw"], TIME_SCALE
            )

            schedule["processes"].append({
                "process_id": process_id,
                "process_name": process["process_name"],
                "start_time": start_time,
                "end_time": end_time,
                "duration_hours": process["duration_hours"],
                "power_kw": process["power_kw"],
                "is_flexible": process["is_flexible"],
                "solar_energy_kwh": solar_energy,
                "grid_energy_kwh": grid_energy,
                "tariff": get_tariff(int(start_time)),
                "energy_cost": energy_cost
            })

        return schedule
    else:
        return None


def create_solar_aware_schedule(factory_data, solar_profile):
    """
    Create a solar-aware production schedule that maximizes solar utilization
    for flexible processes while respecting all production constraints.

    Args:
        factory_data: Dictionary containing factory configuration and processes
        solar_profile: Dictionary mapping hours to solar availability in kW

    Returns:
        Dictionary with solver status, scheduled process times, and energy metrics,
        or None if infeasible
    """
    # OR-Tools CP-SAT requires integer values
    # Scale time units by a factor to handle fractional hours
    TIME_SCALE = 2  # 1 unit = 0.5 hours (half-hour precision)

    # Extract factory parameters and scale to integer units
    planning_horizon = int(factory_data["planning_horizon_hours"] * TIME_SCALE)
    production_deadline = int(factory_data["production_deadline"] * TIME_SCALE)
    processes = factory_data["processes"]

    # Create CP-SAT model
    model = cp_model.CpModel()

    # Create start-time and end-time variables for each process
    start_times = {}
    end_times = {}

    for process in processes:
        process_id = process["process_id"]
        duration = int(process["duration_hours"] * TIME_SCALE)

        # Start time variable: must be within planning horizon
        start_times[process_id] = model.NewIntVar(
            0, planning_horizon, f"start_{process_id}"
        )

        # End time variable: must be within planning horizon
        end_times[process_id] = model.NewIntVar(
            0, planning_horizon, f"end_{process_id}"
        )

        # Constraint: end_time = start_time + duration
        model.Add(end_times[process_id] == start_times[process_id] + duration)

        # Constraint: process must finish before production deadline
        model.Add(end_times[process_id] <= production_deadline)

    # Add dependency/precedence constraints
    for process in processes:
        process_id = process["process_id"]
        dependencies = process["dependencies"]

        for dep_id in dependencies:
            # Dependency process must finish before this process starts
            model.Add(end_times[dep_id] <= start_times[process_id])

    # Non-flexible processes must remain non-flexible: pin their start times
    # to the baseline (normal production plan) schedule.
    baseline_schedule = create_baseline_schedule(factory_data)
    if baseline_schedule is None:
        return None
    baseline_start_units = {
        p["process_id"]: int(round(p["start_time"] * TIME_SCALE))
        for p in baseline_schedule["processes"]
    }
    for process in processes:
        if not process["is_flexible"]:
            model.Add(
                start_times[process["process_id"]]
                == baseline_start_units[process["process_id"]]
            )

    # Objective: maximize total solar energy used by flexible processes,
    # with makespan as a secondary tiebreaker.
    #
    # Energy definition (same as calculate_solar_energy()):
    #   solar energy per time slot =
    #       min(solar availability, process power consumption) * slot duration
    # Since TIME_SCALE = 2, each scheduling slot is 0.5 hour.

    slot_duration_hours = 1 / TIME_SCALE  # 0.5 hour per scheduling slot

    # One contribution variable per flexible process
    solar_contrib_vars = []

    for process in processes:
        if process["is_flexible"]:
            process_id = process["process_id"]
            duration = int(process["duration_hours"] * TIME_SCALE)
            process_power = process["power_kw"]

            # Integer-scaled variable for this process's solar energy contribution
            solar_contrib = model.NewIntVar(0, 1000000, f"solar_contrib_{process_id}")

            # Boolean for each possible start time slot of this process
            slot_bools = []

            # Calculate actual solar energy used for each possible start time slot
            # Account for process power consumption, slot duration, and actual duration
            for slot in range(0, planning_horizon - duration + 1):
                actual_solar_energy = 0
                for offset in range(duration):
                    hour = int((slot + offset) / TIME_SCALE)
                    solar_available = solar_profile.get(hour, 0)
                    # min(solar availability, process power) * slot duration
                    actual_solar_energy += (
                        min(solar_available, process_power) * slot_duration_hours
                    )

                # Scale to integers for CP-SAT (0.1 kWh resolution)
                solar_potential = int(round(actual_solar_energy * 10))

                # Create boolean for this start time
                starts_at_slot = model.NewBoolVar(f"starts_{process_id}_{slot}")
                model.Add(start_times[process_id] == slot).OnlyEnforceIf(starts_at_slot)
                model.Add(start_times[process_id] != slot).OnlyEnforceIf(starts_at_slot.Not())
                slot_bools.append(starts_at_slot)

                # Set the solar contribution based on start time
                model.Add(solar_contrib == solar_potential).OnlyEnforceIf(starts_at_slot)

            # Contribution is zero if this process starts outside all listed slots
            model.Add(solar_contrib == 0).OnlyEnforceIf([b.Not() for b in slot_bools])

            solar_contrib_vars.append(solar_contrib)

    # total_solar_score = SUM of solar contributions from ALL flexible processes
    total_solar_score = model.NewIntVar(0, 10000000, "total_solar_score")
    model.Add(total_solar_score == sum(solar_contrib_vars))

    # Secondary objective: minimize makespan (tiebreaker only)
    makespan = model.NewIntVar(0, planning_horizon, "makespan")
    model.AddMaxEquality(makespan, list(end_times.values()))

    # Combined objective: maximize (total_solar_score * SOLAR_WEIGHT - makespan)
    # total_solar_score is in 0.1 kWh units, so with SOLAR_WEIGHT = 100 a gain of
    # 0.1 kWh of solar energy outweighs any possible makespan difference.
    # The makespan term only breaks ties between schedules with equal solar energy.
    SOLAR_WEIGHT = 100
    model.Maximize(total_solar_score * SOLAR_WEIGHT - makespan)

    # Solve the model
    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    # Check if solution is feasible
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        # Build schedule result with energy calculations
        schedule = {
            "status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            "makespan": solver.Value(makespan) / TIME_SCALE,
            "processes": [],
            "total_energy_kwh": 0,
            "total_solar_kwh": 0,
            "total_grid_kwh": 0
        }

        for process in processes:
            process_id = process["process_id"]
            start_time = solver.Value(start_times[process_id]) / TIME_SCALE
            end_time = solver.Value(end_times[process_id]) / TIME_SCALE

            # Calculate solar, grid and total energy for this process
            # Solar energy is calculated only over the actual scheduled duration
            solar_energy, grid_energy, total_energy = calculate_solar_energy(
                start_time, end_time, process["power_kw"], TIME_SCALE
            )

            # Update totals
            schedule["total_energy_kwh"] += total_energy
            schedule["total_solar_kwh"] += solar_energy
            schedule["total_grid_kwh"] += grid_energy

            schedule["processes"].append({
                "process_id": process_id,
                "process_name": process["process_name"],
                "start_time": start_time,
                "end_time": end_time,
                "duration_hours": process["duration_hours"],
                "power_kw": process["power_kw"],
                "is_flexible": process["is_flexible"],
                "solar_availability": get_solar_availability(int(start_time)),
                "solar_energy_kwh": solar_energy,
                "grid_energy_kwh": grid_energy
            })

        return schedule
    else:
        return None


def calculate_process_cost(process_start, process_end, process_power, time_scale=2):
    """
    Calculate the grid electricity cost of a process over its actual scheduled
    duration. Solar energy is free (not charged); only grid energy is billed
    at the DEMO tariff of the hour in which it is consumed.

    Args:
        process_start: Process start time in hours
        process_end: Process end time in hours
        process_power: Process power consumption in kW
        time_scale: Time scaling factor for precision (unused, kept for symmetry)

    Returns:
        Total electricity cost in currency units
    """
    total_cost = 0

    # Iterate through each time slot the process occupies
    current_time = process_start
    while current_time < process_end:
        hour = int(current_time)
        hour_end = min(hour + 1, process_end)
        duration_in_hour = hour_end - current_time

        # Energy used in this slot and the solar share (same definition as
        # calculate_solar_energy)
        energy_in_slot = process_power * duration_in_hour
        solar_available = get_solar_availability(hour)
        solar_in_slot = min(solar_available * duration_in_hour, energy_in_slot)

        # Only grid energy is billed at the hourly DEMO tariff
        grid_in_slot = energy_in_slot - solar_in_slot
        total_cost += grid_in_slot * get_tariff(hour)

        current_time = hour_end

    return total_cost


def create_cost_optimized_schedule(factory_data, solar_profile, tariff_profile):
    """
    Create a solar + tariff cost-optimized production schedule (Version 3).

    Primary objective: minimize total electricity cost.
    Secondary objective: minimize makespan (tiebreaker when cost is equal).

    The in-model cost of a scheduling slot uses the exact same energy and cost
    definitions as the post-solve reporting functions:
        solar energy per slot = min(solar availability, process power) * 0.5 h
        grid energy per slot  = process power * 0.5 h - solar energy per slot
        cost per slot         = grid energy * tariff[hour]

    Args:
        factory_data: Dictionary containing factory configuration and processes
        solar_profile: Dictionary mapping hours to solar availability in kW
        tariff_profile: Dictionary mapping hours to tariff per kWh (DEMO values)

    Returns:
        Dictionary with solver status, scheduled process times, energy metrics
        and energy costs, or None if infeasible
    """
    # OR-Tools CP-SAT requires integer values
    # Scale time units by a factor to handle fractional hours
    TIME_SCALE = 2  # 1 unit = 0.5 hours (half-hour precision)

    # Extract factory parameters and scale to integer units
    planning_horizon = int(factory_data["planning_horizon_hours"] * TIME_SCALE)
    production_deadline = int(factory_data["production_deadline"] * TIME_SCALE)
    processes = factory_data["processes"]

    slot_duration_hours = 1 / TIME_SCALE  # 0.5 hour per scheduling slot

    # Integer scaling for costs: COST_SCALE * kWh * tariff = integer cost units
    # (0.001 currency resolution is plenty of precision for DEMO tariffs)
    COST_SCALE = 1000

    # Create CP-SAT model
    model = cp_model.CpModel()

    # Create start-time and end-time variables for each process
    start_times = {}
    end_times = {}

    for process in processes:
        process_id = process["process_id"]
        duration = int(process["duration_hours"] * TIME_SCALE)

        # Start time variable: must be within planning horizon
        start_times[process_id] = model.NewIntVar(
            0, planning_horizon, f"start_{process_id}"
        )

        # End time variable: must be within planning horizon
        end_times[process_id] = model.NewIntVar(
            0, planning_horizon, f"end_{process_id}"
        )

        # Constraint: end_time = start_time + duration
        model.Add(end_times[process_id] == start_times[process_id] + duration)

        # Constraint: process must finish before production deadline
        model.Add(end_times[process_id] <= production_deadline)

    # Add dependency/precedence constraints
    for process in processes:
        process_id = process["process_id"]
        dependencies = process["dependencies"]

        for dep_id in dependencies:
            # Dependency process must finish before this process starts
            model.Add(end_times[dep_id] <= start_times[process_id])

    # Objective: minimize total electricity cost of ALL processes.
    # The cost expression is built exactly from the selected start times:
    # each process contributes its slot costs via reified per-slot booleans.
    cost_exprs = []

    # Non-flexible processes must remain non-flexible: pin their start times
    # to the baseline (normal production plan) schedule. Their grid cost is
    # then constant and is reported from the actual schedule after solving.
    baseline_schedule = create_baseline_schedule(factory_data)
    if baseline_schedule is None:
        return None
    baseline_start_units = {
        p["process_id"]: int(round(p["start_time"] * TIME_SCALE))
        for p in baseline_schedule["processes"]
    }
    for process in processes:
        if not process["is_flexible"]:
            model.Add(
                start_times[process["process_id"]]
                == baseline_start_units[process["process_id"]]
            )

    for process in processes:
        # Only flexible processes contribute a variable cost term
        if not process["is_flexible"]:
            continue

        process_id = process["process_id"]
        duration = int(process["duration_hours"] * TIME_SCALE)
        process_power = process["power_kw"]

        # Integer-scaled cost variable for this process
        process_cost = model.NewIntVar(0, 100000000, f"cost_{process_id}")

        # Boolean for each possible start time slot of this process
        slot_bools = []

        for slot in range(0, planning_horizon - duration + 1):
            # Exact grid cost for a run starting at this slot (integer units),
            # using the same energy definition as calculate_solar_energy():
            #   solar per slot = min(solar availability, power) * 0.5 h
            #   grid per slot  = power * 0.5 h - solar per slot
            #   cost per slot  = grid * tariff[hour]
            actual_cost = 0
            for offset in range(duration):
                hour = int((slot + offset) / TIME_SCALE)
                solar_available = solar_profile.get(hour, 0)
                slot_energy = process_power * slot_duration_hours
                slot_solar = min(solar_available, process_power) * slot_duration_hours
                slot_grid = slot_energy - slot_solar
                actual_cost += slot_grid * tariff_profile.get(hour, 0)

            cost_potential = int(round(actual_cost * COST_SCALE))

            # Create boolean for this start time
            starts_at_slot = model.NewBoolVar(f"starts_{process_id}_{slot}")
            model.Add(start_times[process_id] == slot).OnlyEnforceIf(starts_at_slot)
            model.Add(start_times[process_id] != slot).OnlyEnforceIf(starts_at_slot.Not())
            slot_bools.append(starts_at_slot)

            # Set the cost contribution based on start time
            model.Add(process_cost == cost_potential).OnlyEnforceIf(starts_at_slot)

        # Cost is zero if this process starts outside all listed slots
        model.Add(process_cost == 0).OnlyEnforceIf([b.Not() for b in slot_bools])

        # SUM of contributions from ALL processes (exact aggregation)
        cost_exprs.append(process_cost)

    # Secondary objective: minimize makespan (tiebreaker only)
    makespan = model.NewIntVar(0, planning_horizon, "makespan")
    model.AddMaxEquality(makespan, list(end_times.values()))

    # Primary objective: minimize total electricity cost.
    # Makespan is scaled to a small value relative to costs so it only breaks
    # ties between schedules with identical cost, never overriding cost.
    MAKESPAN_TIEBREAK_SCALE = 1000
    total_cost_expr = sum(cost_exprs, 0)
    model.Minimize(total_cost_expr * MAKESPAN_TIEBREAK_SCALE + makespan)

    # Solve the model
    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    # Check if solution is feasible
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        # Build schedule result with energy and cost calculations
        schedule = {
            "status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            "makespan": solver.Value(makespan) / TIME_SCALE,
            "processes": [],
            "total_energy_kwh": 0,
            "total_solar_kwh": 0,
            "total_grid_kwh": 0,
            "total_energy_cost": 0
        }

        for process in processes:
            process_id = process["process_id"]
            start_time = solver.Value(start_times[process_id]) / TIME_SCALE
            end_time = solver.Value(end_times[process_id]) / TIME_SCALE

            # Calculate solar, grid and total energy for this process
            solar_energy, grid_energy, total_energy = calculate_solar_energy(
                start_time, end_time, process["power_kw"], TIME_SCALE
            )

            # Calculate grid electricity cost from the actual optimized schedule
            energy_cost = calculate_process_cost(
                start_time, end_time, process["power_kw"], TIME_SCALE
            )

            # Update totals
            schedule["total_energy_kwh"] += total_energy
            schedule["total_solar_kwh"] += solar_energy
            schedule["total_grid_kwh"] += grid_energy
            schedule["total_energy_cost"] += energy_cost

            schedule["processes"].append({
                "process_id": process_id,
                "process_name": process["process_name"],
                "start_time": start_time,
                "end_time": end_time,
                "duration_hours": process["duration_hours"],
                "power_kw": process["power_kw"],
                "is_flexible": process["is_flexible"],
                "solar_availability": get_solar_availability(int(start_time)),
                "solar_energy_kwh": solar_energy,
                "grid_energy_kwh": grid_energy,
                "tariff": get_tariff(int(start_time)),
                "energy_cost": energy_cost
            })

        return schedule
    else:
        return None


def print_baseline_schedule(schedule):
    """
    Print the baseline production schedule.

    Args:
        schedule: Dictionary containing solver status and scheduled process times
    """
    if schedule is None:
        print("No feasible baseline schedule found.")
        print("The production constraints cannot be satisfied with the given parameters.")
        return

    print("=" * 80)
    print("BASELINE SCHEDULE")
    print("=" * 80)
    print(f"Solver Status: {schedule['status']}")
    print(f"Overall Production Completion Time (Makespan): {schedule['makespan']} hours")
    print("=" * 80)
    print()

    # Print header
    print(f"{'Process Name':<20} {'Start':<10} {'End':<10} {'Duration':<10} {'Power (kW)':<12} {'Flexible':<10}")
    print("-" * 80)

    # Print each process
    for process in schedule["processes"]:
        flexible_status = "Yes" if process["is_flexible"] else "No"
        print(
            f"{process['process_name']:<20} "
            f"{process['start_time']:<10} "
            f"{process['end_time']:<10} "
            f"{process['duration_hours']:<10} "
            f"{process['power_kw']:<12} "
            f"{flexible_status:<10}"
        )

    print("=" * 80)
    print()


def print_solar_aware_schedule(schedule):
    """
    Print the solar + tariff optimized production schedule with energy and
    cost metrics.

    Args:
        schedule: Dictionary containing solver status, scheduled process times,
                  energy metrics, and energy costs
    """
    if schedule is None:
        print("No feasible solar-aware schedule found.")
        print("The production constraints cannot be satisfied with solar optimization.")
        return

    print("=" * 80)
    print("SOLAR + TARIFF OPTIMIZED SCHEDULE")
    print("=" * 80)
    print(f"Solver Status: {schedule['status']}")
    print(f"Overall Production Completion Time (Makespan): {schedule['makespan']} hours")
    print("=" * 80)
    print()

    # Print header
    print(f"{'Process Name':<20} {'Start':<10} {'End':<10} {'Duration':<10} {'Power (kW)':<12} {'Flexible':<10} {'Solar (kW)':<12} {'Solar (kWh)':<12} {'Grid (kWh)':<12} {'Tariff':<12} {'Cost':<12}")
    print("-" * 150)

    # Print each process
    for process in schedule["processes"]:
        flexible_status = "Yes" if process["is_flexible"] else "No"
        print(
            f"{process['process_name']:<20} "
            f"{process['start_time']:<10} "
            f"{process['end_time']:<10} "
            f"{process['duration_hours']:<10} "
            f"{process['power_kw']:<12} "
            f"{flexible_status:<10} "
            f"{process['solar_availability']:<12} "
            f"{process['solar_energy_kwh']:<12.2f} "
            f"{process['grid_energy_kwh']:<12.2f} "
            f"{process.get('tariff', 0):<12.2f} "
            f"{process.get('energy_cost', 0):<12.2f}"
        )

    print("=" * 80)
    print()


def print_summary_metrics(baseline_schedule, optimized_schedule):
    """
    Print summary metrics comparing baseline and optimized schedules.

    Args:
        baseline_schedule: Baseline schedule dictionary
        optimized_schedule: Solar-aware optimized schedule dictionary
    """
    print("=" * 80)
    print("SUMMARY METRICS")
    print("=" * 80)

    if baseline_schedule and optimized_schedule:
        baseline_makespan = baseline_schedule["makespan"]
        optimized_makespan = optimized_schedule["makespan"]

        total_energy = optimized_schedule["total_energy_kwh"]
        solar_energy = optimized_schedule["total_solar_kwh"]
        grid_energy = optimized_schedule["total_grid_kwh"]
        solar_utilization = (solar_energy / total_energy * 100) if total_energy > 0 else 0

        # Energy costs, calculated from the actual schedules
        baseline_cost = sum(
            p.get("energy_cost", 0) for p in baseline_schedule["processes"]
        )
        optimized_cost = sum(
            p.get("energy_cost", 0) for p in optimized_schedule["processes"]
        )
        cost_savings = baseline_cost - optimized_cost
        saving_percentage = (
            (cost_savings / baseline_cost * 100) if baseline_cost > 0 else 0
        )

        # Count flexible processes shifted
        flexible_shifted = 0
        baseline_start_times = {p["process_id"]: p["start_time"] for p in baseline_schedule["processes"]}
        for process in optimized_schedule["processes"]:
            if process["is_flexible"]:
                baseline_start = baseline_start_times[process["process_id"]]
                if abs(process["start_time"] - baseline_start) > 0.1:  # More than 0.1 hour difference
                    flexible_shifted += 1

        print(f"Baseline Makespan: {baseline_makespan:.2f} hours")
        print(f"Optimized Makespan: {optimized_makespan:.2f} hours")
        print(f"Total Energy Consumption: {total_energy:.2f} kWh")
        print(f"Solar Energy Used: {solar_energy:.2f} kWh")
        print(f"Grid Energy Used: {grid_energy:.2f} kWh")
        print(f"Solar Utilization: {solar_utilization:.2f}%")
        print(f"Baseline Energy Cost: {baseline_cost:.2f}")
        print(f"Optimized Energy Cost: {optimized_cost:.2f}")
        print(f"Energy Cost Savings: {cost_savings:.2f}")
        print(f"Energy Cost Saving Percentage: {saving_percentage:.2f}%")
        print(f"Number of Flexible Processes Shifted: {flexible_shifted}")
    else:
        print("Unable to calculate summary metrics - one or both schedules are infeasible.")

    print("=" * 80)


def main():
    """Main function to run the optimizer."""
    print("ByteMe Manufacturing Energy Scheduler - Version 3")
    print("Solar + Tariff Cost Optimization (Demand Shifting)")
    print()

    # Generate baseline schedule
    print("Generating baseline schedule...")
    baseline_schedule = create_baseline_schedule(DEMO_CHOCOLATE_FACTORY)
    print()

    # Generate solar + tariff cost-optimized schedule (Version 3)
    print("Generating solar + tariff optimized schedule...")
    optimized_schedule = create_cost_optimized_schedule(
        DEMO_CHOCOLATE_FACTORY, DEMO_SOLAR_PROFILE, DEMO_TARIFF_PROFILE
    )
    print()

    # Print results
    print_baseline_schedule(baseline_schedule)
    print_solar_aware_schedule(optimized_schedule)
    print_summary_metrics(baseline_schedule, optimized_schedule)


if __name__ == "__main__":
    main()
