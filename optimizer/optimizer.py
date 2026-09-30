"""
ByteMe Manufacturing Energy Scheduler - Version 1

This module implements a basic production scheduler using Google OR-Tools CP-SAT.
It generates a valid feasible schedule respecting production constraints and deadlines.

Current capabilities:
- Factory Data → OR-Tools → Valid Production Schedule
- Respects planning horizon and production deadline
- Enforces process dependencies/precedence constraints
- Minimizes overall production completion time

Future versions will add:
- Solar-aware optimization
- Electricity tariff optimization
- Energy cost calculation
- Demand shifting
"""

from ortools.sat.python import cp_model
from optimizer.factory_data import DEMO_CHOCOLATE_FACTORY


def create_production_schedule(factory_data):
    """
    Create a valid production schedule using OR-Tools CP-SAT.

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

    # Create a mapping from process_id to process for easy lookup
    process_map = {p["process_id"]: p for p in processes}

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

    # Objective: minimize the overall production completion time
    # (i.e., minimize the latest end time among all processes)
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
            "makespan": solver.Value(makespan) / TIME_SCALE,  # Convert back to hours
            "processes": []
        }

        for process in processes:
            process_id = process["process_id"]
            schedule["processes"].append({
                "process_id": process_id,
                "process_name": process["process_name"],
                "start_time": solver.Value(start_times[process_id]) / TIME_SCALE,  # Convert back to hours
                "end_time": solver.Value(end_times[process_id]) / TIME_SCALE,  # Convert back to hours
                "duration_hours": process["duration_hours"],
                "power_kw": process["power_kw"],
                "is_flexible": process["is_flexible"]
            })

        return schedule
    else:
        # No feasible solution found
        return None


def print_schedule(schedule):
    """
    Print the production schedule in a readable format.

    Args:
        schedule: Dictionary containing solver status and scheduled process times
    """
    if schedule is None:
        print("No feasible schedule found.")
        print("The production constraints cannot be satisfied with the given parameters.")
        return

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


def main():
    """Main function to run the optimizer."""
    print("ByteMe Manufacturing Energy Scheduler - Version 1")
    print("Generating valid production schedule...")
    print()

    # Create schedule from factory data
    schedule = create_production_schedule(DEMO_CHOCOLATE_FACTORY)

    # Print results
    print_schedule(schedule)


if __name__ == "__main__":
    main()
