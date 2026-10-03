"""
ByteMe Manufacturing Energy Scheduler - Version 4
Generic Factory/Process Model + Solar + Tariff Cost Optimization

This module implements the GENERIC optimization engine of the ByteMe
manufacturing energy scheduling system using Google OR-Tools CP-SAT.

Architecture (configuration-driven, factory-agnostic):

    FACTORY CONFIGURATION (factory_data.py)
        |
    GENERIC PROCESS/MACHINE MODEL (models.py)
        |
    GENERIC PRODUCTION CONSTRAINTS
        |
    OR-TOOLS OPTIMIZATION ENGINE (this module)
        |
    ENERGY + SOLAR + TARIFF MODEL (energy_data.py + helpers below)
        |
    VALIDATED SCHEDULE + ENERGY/COST METRICS

The engine receives a generic FactoryConfig and NEVER checks for specific
factory names or process names. Adding a new factory means adding a new
configuration dict - the algorithm is untouched.

All data used is DEMO/SIMULATED data only (no real prices, weather, or
forecasting).

Version capabilities preserved:
- V1: baseline scheduling (minimize makespan)
- V2: solar-aware demand shifting (maximize ALLOCATED solar under the
  SHARED half-hour-slot solar pool, makespan as tiebreaker)
- V3: solar + tariff cost optimization (minimize grid electricity cost
  under the SHARED half-hour-slot solar pool, makespan as tiebreaker;
  non-flexible processes pinned to their baseline start times)
- V4: all of the above, for ANY factory configuration, with machine/resource
  no-overlap constraints, optional process time windows, and configuration
  validation.
"""

from ortools.sat.python import cp_model

from optimizer.energy_data import DEMO_SOLAR_PROFILE, DEMO_TARIFF_PROFILE
from optimizer.models import (
    SOLAR_SCALE,
    FactoryConfig,
    FactoryConfigError,
    TIME_SCALE,
    energy_precision_problems,
    time_to_grid_ceil,
    time_to_grid_floor,
)


# ---------------------------------------------------------------------------
# Solver configuration (determinism + optional time limit)
# ---------------------------------------------------------------------------

class SolverUnknownError(RuntimeError):
    """
    Raised when CP-SAT returns UNKNOWN: the solver hit a limit (e.g. the
    configured time limit) without finding a solution and without proving
    infeasibility. Callers must NOT interpret this as INFEASIBLE.
    """
    def __init__(self, message, solve_time_seconds=None):
        super().__init__(message)
        self.solve_time_seconds = solve_time_seconds


class SolverInfeasibleError(RuntimeError):
    """Internal signal carrying wall time for a proven-infeasible solve."""

    def __init__(self, solve_time_seconds):
        self.solve_time_seconds = solve_time_seconds


class SolverModelInvalidError(RuntimeError):
    """Raised when CP-SAT rejects a constructed model as invalid."""



# Single-worker CP-SAT search is deterministic for a given input, model,
# and pinned OR-Tools version. Multi-worker search is not: it may return
# different (equally optimal) solutions between runs.
DETERMINISTIC_WORKERS = 1

# Default time limit applied by the PUBLIC API layer (seconds per solve).
# Direct engine callers may pass max_time_seconds explicitly; None means
# no limit (previous engine behavior).
DEFAULT_PUBLIC_TIME_LIMIT_SECONDS = 60


def _new_solver(max_time_seconds=None):
    """Create a deterministic, optionally time-limited CP-SAT solver."""
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = DETERMINISTIC_WORKERS
    # Use LP_SEARCH for faster optimality proofs on linear objectives
    # LP relaxation can provide stronger bounds for proving optimality
    solver.parameters.search_branching = cp_model.LP_SEARCH
    # Increase linearization level for better LP relaxation quality
    solver.parameters.linearization_level = 2
    if max_time_seconds is not None:
        solver.parameters.max_time_in_seconds = float(max_time_seconds)
    return solver

# Re-exported for backward compatibility with code importing these from
# optimizer.optimizer (they remain DEMO/SIMULATED data).
__all__ = [
    "DEMO_SOLAR_PROFILE",
    "DEMO_TARIFF_PROFILE",
    "get_solar_availability",
    "get_tariff",
    "calculate_solar_energy",
    "calculate_process_cost",
    "create_baseline_schedule",
    "create_solar_aware_schedule",
    "create_cost_optimized_schedule",
    "load_factory_config",
    "print_baseline_schedule",
    "print_solar_aware_schedule",
    "print_summary_metrics",
]


# ---------------------------------------------------------------------------
# Energy + tariff model (DEMO/SIMULATED data only)
# ---------------------------------------------------------------------------

def get_solar_availability(hour, solar_profile=None):
    """
    Get solar availability for a specific hour.

    Profiles are 24-hour cyclic: hours beyond 24 (multi-day horizons) wrap
    with hour % 24, so day-2 hour 25 uses the day-1 hour-1 value.

    Args:
        hour: Hour offset from schedule start (>= 0)
        solar_profile: Optional profile dict (hour -> kW); defaults to DEMO

    Returns:
        Solar power availability in kW
    """
    profile = DEMO_SOLAR_PROFILE if solar_profile is None else solar_profile
    return profile.get(hour % 24, 0)


def get_tariff(hour, tariff_profile=None):
    """
    Get the electricity tariff for a specific hour.

    Profiles are 24-hour cyclic: hours beyond 24 (multi-day horizons) wrap
    with hour % 24.

    Args:
        hour: Hour offset from schedule start (>= 0)
        tariff_profile: Optional profile dict (hour -> tariff/kWh); defaults
            to DEMO

    Returns:
        Tariff in currency units per kWh
    """
    profile = DEMO_TARIFF_PROFILE if tariff_profile is None else tariff_profile
    return profile.get(hour % 24, 0)


def calculate_solar_energy(process_start, process_end, process_power,
                           time_scale=TIME_SCALE, solar_profile=None):
    """
    Calculate solar energy used by a process during its actual scheduled duration.

    Energy definition (generic):
        per hour slot: solar = min(solar availability, process power) * duration

    Args:
        process_start: Process start time in hours
        process_end: Process end time in hours
        process_power: Process power consumption in kW
        time_scale: Time scaling factor for precision
        solar_profile: Optional profile dict (hour -> kW); defaults to DEMO

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
        solar_available = get_solar_availability(hour, solar_profile)

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


def calculate_process_cost(process_start, process_end, process_power,
                           time_scale=TIME_SCALE, solar_profile=None,
                           tariff_profile=None):
    """
    Calculate the grid electricity cost of a process over its actual scheduled
    duration. Solar energy is free (not charged); only grid energy is billed
    at the DEMO tariff of the hour in which it is consumed.

    Args:
        process_start: Process start time in hours
        process_end: Process end time in hours
        process_power: Process power consumption in kW
        time_scale: Time scaling factor for precision (unused, kept for symmetry)
        solar_profile: Optional profile dict (hour -> kW); defaults to DEMO
        tariff_profile: Optional profile dict (hour -> tariff/kWh); defaults to DEMO

    Returns:
        Total electricity cost in currency units (DEMO values)
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
        solar_available = get_solar_availability(hour, solar_profile)
        solar_in_slot = min(solar_available * duration_in_hour, energy_in_slot)

        # Only grid energy is billed at the hourly DEMO tariff
        grid_in_slot = energy_in_slot - solar_in_slot
        total_cost += grid_in_slot * get_tariff(hour, tariff_profile)

        current_time = hour_end

    return total_cost


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------

def load_factory_config(factory_data):
    """
    Load and validate any factory configuration dict into a FactoryConfig.

    Invalid configurations raise FactoryConfigError with a clear message
    instead of silently producing an incorrect schedule.
    """
    if isinstance(factory_data, FactoryConfig):
        return factory_data
    return FactoryConfig(factory_data)


# ---------------------------------------------------------------------------
# Shared model builder: generic production constraints
# ---------------------------------------------------------------------------

def _calculate_start_domains(config):
    """Compute necessary start bounds from windows, deadlines, and precedence."""
    horizon = time_to_grid_floor(config.planning_horizon_hours)
    deadline = time_to_grid_floor(config.production_deadline)
    process_order = [process.process_id for process in config.processes]
    processes = {process.process_id: process for process in config.processes}
    durations = {
        process_id: int(round(processes[process_id].duration_hours * TIME_SCALE))
        for process_id in process_order
    }
    earliest = {
        process_id: time_to_grid_ceil(
            processes[process_id].earliest_start
            if processes[process_id].earliest_start is not None else 0
        )
        for process_id in process_order
    }
    latest = {}
    successors = {process_id: [] for process_id in process_order}
    pending = {}
    for process_id in process_order:
        process = processes[process_id]
        finish_limit = min(
            horizon,
            deadline,
            time_to_grid_floor(process.latest_finish)
            if process.latest_finish is not None else deadline,
        )
        latest[process_id] = finish_limit - durations[process_id]
        pending[process_id] = len(process.dependencies)
        for dependency in process.dependencies:
            successors[dependency].append(process_id)

    ready = [process_id for process_id in process_order
             if pending[process_id] == 0]
    topological_order = []
    while ready:
        process_id = ready.pop()
        topological_order.append(process_id)
        for successor in successors[process_id]:
            earliest[successor] = max(
                earliest[successor],
                earliest[process_id] + durations[process_id],
            )
            pending[successor] -= 1
            if pending[successor] == 0:
                ready.append(successor)

    for process_id in reversed(topological_order):
        for successor in successors[process_id]:
            latest[process_id] = min(
                latest[process_id],
                latest[successor] - durations[process_id],
            )

    return {
        process_id: (earliest[process_id], latest[process_id])
        for process_id in process_order
    }


def _build_base_model(config, prune_domains=True):
    """
    Build the shared CP-SAT model skeleton for a validated FactoryConfig:
    variables, durations, deadline, dependencies, time windows, and
    machine/resource no-overlap constraints.

    Returns (model, start_times, end_times).
    """
    model = cp_model.CpModel()

    # OR-Tools CP-SAT requires integer values; TIME_SCALE units = 0.5 hours
    planning_horizon = time_to_grid_floor(config.planning_horizon_hours)
    production_deadline = time_to_grid_floor(config.production_deadline)
    start_domains = _calculate_start_domains(config)

    start_times = {}
    end_times = {}
    has_empty_domain = False

    for process in config.processes:
        process_id = process.process_id
        duration = int(process.duration_hours * TIME_SCALE)
        lower_start, upper_start = start_domains[process_id]

        if prune_domains and lower_start <= upper_start:
            start_times[process_id] = model.NewIntVar(
                lower_start, upper_start, f"start_{process_id}"
            )
            end_times[process_id] = model.NewIntVar(
                lower_start + duration, upper_start + duration,
                f"end_{process_id}"
            )
        else:
            start_times[process_id] = model.NewIntVar(
                0, planning_horizon, f"start_{process_id}"
            )
            end_times[process_id] = model.NewIntVar(
                0, planning_horizon, f"end_{process_id}"
            )
            if prune_domains:
                has_empty_domain = True

        # Constraint: end_time = start_time + duration
        model.Add(end_times[process_id] == start_times[process_id] + duration)

        # Constraint: process must finish before production deadline
        model.Add(end_times[process_id] <= production_deadline)

        # Optional per-process time windows (generic constraint fields)
        if process.earliest_start is not None:
            model.Add(start_times[process_id] >= time_to_grid_ceil(
                process.earliest_start
            ))
        if process.latest_finish is not None:
            model.Add(end_times[process_id] <= time_to_grid_floor(
                process.latest_finish
            ))

    if has_empty_domain:
        model.Add(False)

    # Dependency/precedence constraints (supports sequential, parallel,
    # independent, branching and merging structures - purely data-driven)
    for process in config.processes:
        for dep_id in process.dependencies:
            model.Add(end_times[dep_id] <= start_times[process.process_id])

    # Machine/resource constraints: capacity is REAL. A machine with
    # capacity 1 never runs two processes at once (NoOverlap); a machine
    # with capacity >= 2 allows up to `capacity` simultaneous processes
    # (AddCumulative with unit demands).
    for machine_id, process_ids in config.processes_by_machine.items():
        if len(process_ids) > 1:
            machine = config.get_machine(machine_id)
            capacity = max(1, int(machine.capacity or 1))
            intervals = [
                model.NewIntervalVar(
                    start_times[pid],
                    int(config.get_process(pid).duration_hours * TIME_SCALE),
                    end_times[pid],
                    f"interval_{machine_id}_{pid}",
                )
                for pid in process_ids
            ]
            if capacity == 1:
                model.AddNoOverlap(intervals)
            else:
                model.AddCumulative(
                    intervals,
                    [config.get_process(pid).capacity_units
                     for pid in process_ids],
                    capacity,
                )

    return model, start_times, end_times


def _pin_non_flexible_processes(config, model, start_times,
                                baseline_schedule=None):
    """
    Pin non-flexible processes to their baseline start times: non-flexible
    processes must remain at their baseline schedule positions.

    Args:
        baseline_schedule: Optional precomputed baseline schedule dict. When
            provided it is reused as-is - identical pinning semantics, no
            extra solver call. Computed internally when omitted.
    """
    baseline = baseline_schedule
    if baseline is None:
        baseline = create_baseline_schedule(config)
    if baseline is None:
        return None
    baseline_start_units = {
        p["process_id"]: int(round(p["start_time"] * TIME_SCALE))
        for p in baseline["processes"]
    }
    fixed_start_units = {
        process.process_id: baseline_start_units[process.process_id]
        for process in config.processes
        if not process.is_flexible
    }
    for process in config.processes:
        if not process.is_flexible:
            model.Add(
                start_times[process.process_id]
                == baseline_start_units[process.process_id]
            )
    return baseline


def _process_allowed_slots(config, horizon_slots, prune_domains,
                           fixed_start_times, start_domains):
    """
    Allowed start-slot range per process id:
    {pid: (first_slot, last_slot, duration_slots)}.

    Shared by the run-boolean creation and the per-hour term collection so
    both walk exactly the same slot domain (and only once per process).
    """
    ranges = {}
    for process in config.processes:
        pid = process.process_id
        duration = int(process.duration_hours * TIME_SCALE)
        lower_start, upper_start = start_domains[pid]
        if prune_domains and pid in fixed_start_times:
            lower_start = upper_start = fixed_start_times[pid]
        if not prune_domains:
            lower_start, upper_start = 0, horizon_slots - duration
        first_slot = max(0, lower_start)
        last_slot = min(horizon_slots - duration, upper_start)
        ranges[pid] = (first_slot, last_slot, duration)
    return ranges


def _sum_linear_terms(terms):
    """Aggregate linear terms with OR-Tools' native n-ary sum expression."""
    return cp_model.LinearExpr.sum(terms)


def _add_shared_solar_pool(config, model, start_times, solar_profile,
                           prune_domains=True, fixed_start_times=None,
                           collect_demand=True, active_hours=None):
    """
    Encode solar as a SHARED half-hour resource INSIDE the CP-SAT model.

    For every half-hour slot s the model carries an integer variable
    used[s] (0.1 kWh units, SOLAR_SCALE), bounded by both the solar energy
    available in that slot and the sum of running-process draw caps.

    draw_cap[p, s] is a linear expression over run[p, t] booleans
    ("process p starts at slot t", exactly-one linked to the existing
    start-time variables): a running process can draw at most
    min(solar_kw, power_kw) per occupied half-hour slot, and nothing in
    hours it does not occupy.

    The booleans use ONE-directional reification: b_t => start == t, plus
    exactly-one over all allowed-slot booleans. That pair already pins the
    true boolean to the actual start slot (exactly one b is true, and the
    true b forces start == its own slot), so the previous reverse
    implication (start != t => not b_t) was logically redundant and is
    omitted - identical feasible solutions, half the reification
    constraints.

    Slots no process can ever occupy (e.g. beyond the production deadline)
    have an empty draw cap, so used[s] is pinned to 0 by its [0, 0]
    variable domain - no phantom solar can be credited to empty slots
    (previously the cap constraint was skipped for such hours, which left
    used[h] free up to the physical pool and let the objective claim solar
    nobody could draw).

    active_hours optionally restricts term construction to the hours whose
    demand/share expressions the caller's objective actually consumes
    (all hours when None, the default). Inactive hours report constant 0
    for both used[h] and demand[h]; active hours keep the exact semantics
    above. collect_demand=False skips the demand expressions entirely
    (the solar objective never consumes them).

    The aggregate form is exact: for any fixed schedule the deterministic
    greedy split used for reporting realizes exactly
    min(pool, sum of draw caps) per half-hour slot - precisely the model's
    upper bound - so optimized objective values equal reported numbers.
    The pool constraint is part of the optimization model, not a post-solve
    adjustment.

    Returns:
        (used, demand): dicts keyed by hour (int).
        used[h]   linear expression - total allocated solar in hour h,
                  summed from its half-hour slot variables
        demand[h] linear expression (or int 0) - total process energy
                  demand in hour h, 0.1 kWh units
    """
    horizon_slots = time_to_grid_floor(config.planning_horizon_hours)
    horizon_hours = (horizon_slots + TIME_SCALE - 1) // TIME_SCALE
    start_domains = _calculate_start_domains(config)
    fixed_start_times = fixed_start_times or {}
    active = (set(range(horizon_hours)) if active_hours is None
              else set(active_hours))

    allowed = _process_allowed_slots(config, horizon_slots, prune_domains,
                                     fixed_start_times, start_domains)

    # run[p, t] == 1  <=>  process p starts at slot t: forward implication
    # (b forces the start) plus exactly-one fully determines every boolean
    # from the start time (see docstring). Hot loop uses the snake_case
    # CpModel builders directly (the CamelCase names resolve through a
    # deprecation-wrapper lookup on every call).
    run = {}
    add = model.add
    new_bool_var = model.new_bool_var
    for process in config.processes:
        pid = process.process_id
        first_slot, last_slot, _ = allowed[pid]
        bools = []
        for slot in range(first_slot, last_slot + 1):
            b = new_bool_var(f"run_{pid}_{slot}")
            add(start_times[pid] == slot).only_enforce_if(b)
            bools.append(b)
            run[(pid, slot)] = b
        if bools or not prune_domains:
            model.add_exactly_one(bools)

    # Per-slot aggregate solar caps; hourly demand is kept for tariff costs.
    # Terms are collected process-major: each process visits only the
    # clock hours its allowed start slots can possibly touch, and hours
    # with no possible draw (zero solar / zero power) skip their slot
    # loop entirely.
    cap_terms = {
        tick: []
        for hour in active
        for tick in range(hour * TIME_SCALE,
                          min((hour + 1) * TIME_SCALE, horizon_slots))
    }
    demand_terms = {hour: [] for hour in active} if collect_demand else {}
    for process in config.processes:
        pid = process.process_id
        power = process.power_kw
        first_slot, last_slot, duration = allowed[pid]
        if first_slot > last_slot:
            continue  # cannot run anywhere - no draw in any hour
        per_slot_demand_u = int(round(power * 0.5 * SOLAR_SCALE))
        first_hour = max(0, first_slot // TIME_SCALE)
        last_hour = min(horizon_hours - 1,
                        (last_slot + duration - 1) // TIME_SCALE)
        for hour in range(first_hour, last_hour + 1):
            if hour not in active:
                continue
            solar_kw = solar_profile.get(hour % 24, 0)
            hour_start = hour * TIME_SCALE
            hour_end = hour_start + TIME_SCALE
            lo = max(first_slot, hour_start - duration + 1)
            hi = min(last_slot, hour_end - 1)
            if hi < lo:
                continue
            if collect_demand and per_slot_demand_u > 0:
                terms = demand_terms[hour]
                for slot in range(lo, hi + 1):
                    cnt = (min(slot + duration, hour_end)
                           - max(slot, hour_start))
                    terms.append(per_slot_demand_u * cnt * run[(pid, slot)])
            if solar_kw <= 0:
                continue
            per_tick_u = int(round(
                min(solar_kw, power) * SOLAR_SCALE / TIME_SCALE
            ))
            if per_tick_u <= 0:
                continue
            for tick in range(hour_start, min(hour_end, horizon_slots)):
                tick_lo = max(first_slot, tick - duration + 1)
                tick_hi = min(last_slot, tick)
                if tick_hi < tick_lo:
                    continue
                terms = cap_terms[tick]
                for start_slot in range(tick_lo, tick_hi + 1):
                    terms.append(per_tick_u * run[(pid, start_slot)])

    used = {}
    demand = {}
    for hour in range(horizon_hours):
        solar_kw = solar_profile.get(hour % 24, 0)
        slot_uses = []
        for tick in range(hour * TIME_SCALE,
                          min((hour + 1) * TIME_SCALE, horizon_slots)):
            terms = cap_terms.get(tick)
            pool_u = max(int(round(
                solar_kw * SOLAR_SCALE / TIME_SCALE
            )), 0)
            used_tick = model.new_int_var(
                0, pool_u if terms else 0, f"solar_used_{tick}"
            )
            if terms and pool_u > 0:
                model.add(used_tick <= _sum_linear_terms(terms))
            slot_uses.append(used_tick)
        used[hour] = _sum_linear_terms(slot_uses)
        hour_demand_terms = demand_terms.get(hour)
        demand[hour] = (_sum_linear_terms(hour_demand_terms)
                        if hour_demand_terms else 0)

    return used, demand


def _allocate_solar_by_slot(rows, solar_profile, tariff_profile=None,
                            include_grid_by_slot=False):
    """Return per-process totals and optionally grid energy per slot."""
    # Collect running processes per half-hour slot with their overlap.
    running_by_slot = {}
    for row in rows:
        t = row["start_time"]
        while t < row["end_time"]:
            slot = int(t * TIME_SCALE)
            slot_end = (slot + 1) / TIME_SCALE
            overlap = min(slot_end, row["end_time"]) - t
            running_by_slot.setdefault(slot, []).append(
                (row["process_id"], row["power_kw"] * overlap, overlap)
            )
            t += overlap

    allocation = {
        row["process_id"]: {"solar_kwh": 0.0, "grid_kwh": 0.0,
                            "energy_cost": 0.0}
        for row in rows
    }
    grid_by_slot = {}

    for slot, runners in running_by_slot.items():
        hour = slot // TIME_SCALE
        supply_kw = get_solar_availability(hour, solar_profile)
        supply_kwh = supply_kw / TIME_SCALE
        slot_grid_kwh = 0.0 if include_grid_by_slot else None
        # Deterministic order: process_id
        for pid, demand_kwh, overlap in sorted(runners):
            # Per-process draw cap: the process can only use solar while it
            # actually runs in this hour (supply_kw x overlap), and never
            # more than its own demand or the pool remaining.
            draw_cap_kwh = supply_kw * overlap
            solar_kwh = min(demand_kwh, draw_cap_kwh, max(supply_kwh, 0.0))
            supply_kwh -= solar_kwh
            grid_kwh = demand_kwh - solar_kwh
            allocation[pid]["solar_kwh"] += solar_kwh
            allocation[pid]["grid_kwh"] += grid_kwh
            if include_grid_by_slot:
                slot_grid_kwh += grid_kwh
            if tariff_profile is not None:
                allocation[pid]["energy_cost"] += (
                    grid_kwh * get_tariff(hour, tariff_profile)
                )
        if include_grid_by_slot:
            grid_by_slot[slot] = slot_grid_kwh
    return allocation, grid_by_slot


def allocate_solar_greedy(rows, solar_profile, tariff_profile=None):
    """
    Deterministic shared-solar allocation for reporting.

    Solar is shared within each half-hour slot and is allocated in
    process_id order. Returns per-process solar, grid, and optional cost
    totals without exposing the internal slot breakdown.
    """
    allocation, _ = _allocate_solar_by_slot(
        rows, solar_profile, tariff_profile
    )
    return allocation


def _extract_schedule(config, solver, status_name, start_times, end_times,
                      solar_profile, tariff_profile, with_cost):
    """Build the schedule result dict from a solved model (generic)."""
    # Pass 1: collect scheduled times (shared-solar allocation needs ALL
    # concurrent processes before splitting each slot's supply)
    times = []
    for process in config.processes:
        times.append((
            process,
            solver.Value(start_times[process.process_id]) / TIME_SCALE,
            solver.Value(end_times[process.process_id]) / TIME_SCALE,
        ))

    rows = [
        {"process_id": p.process_id, "start_time": s, "end_time": e,
         "power_kw": p.power_kw}
        for p, s, e in times
    ]
    allocation = allocate_solar_greedy(rows, solar_profile,
                                       tariff_profile if with_cost else None)

    schedule = {
        "status": status_name,
        "factory_name": config.factory_name,
        "makespan": max(e for _, _, e in times),
        "solve_time_seconds": round(solver.WallTime(), 4) if solver is not None else None,
        "solver_diagnostics": {
            "status": status_name,
            "best_objective": solver.ObjectiveValue(),
            "best_bound": solver.BestObjectiveBound(),
            "optimality_gap": round(
                abs(solver.ObjectiveValue() - solver.BestObjectiveBound())
                / max(abs(solver.ObjectiveValue()), 1.0),
                6,
            ),
        },
        "processes": [],
        "total_energy_kwh": 0,
        "total_solar_kwh": 0,
        "total_grid_kwh": 0,
    }
    if with_cost:
        schedule["total_energy_cost"] = 0

    for process, start_time, end_time in times:
        alloc = allocation[process.process_id]
        solar_energy = alloc["solar_kwh"]
        grid_energy = alloc["grid_kwh"]
        total_energy = process.power_kw * process.duration_hours

        entry = {
            "process_id": process.process_id,
            "process_name": process.process_name,
            "start_time": start_time,
            "end_time": end_time,
            "duration_hours": process.duration_hours,
            "power_kw": process.power_kw,
            "is_flexible": process.is_flexible,
            "machine_id": process.machine_id,
            "capacity_units": process.capacity_units,
            "machine_capacity": (
                config.get_machine(process.machine_id).capacity
                if process.machine_id is not None else None
            ),
            "quantity": process.quantity,
            "solar_availability": get_solar_availability(int(start_time), solar_profile),
            "solar_energy_kwh": solar_energy,
            "grid_energy_kwh": grid_energy,
        }
        if with_cost:
            energy_cost = alloc["energy_cost"]
            entry["tariff"] = get_tariff(int(start_time), tariff_profile)
            entry["energy_cost"] = energy_cost
            schedule["total_energy_cost"] += energy_cost

        schedule["total_energy_kwh"] += total_energy
        schedule["total_solar_kwh"] += solar_energy
        schedule["total_grid_kwh"] += grid_energy
        schedule["processes"].append(entry)

    return schedule


# ---------------------------------------------------------------------------
# V1: Baseline schedule (minimize makespan)
# ---------------------------------------------------------------------------

def create_baseline_schedule(factory_data, solar_profile=None,
                             tariff_profile=None, max_time_seconds=None,
                             raise_on_infeasible=False):
    """
    Create a baseline production schedule (Version 1 - minimize makespan).

    Accepts any factory configuration dict (or a validated FactoryConfig).

    Args:
        factory_data: Generic factory configuration
        solar_profile: Optional profile dict (hour -> kW); defaults to DEMO.
            Used only for reported energy/cost of the baseline plan.
        tariff_profile: Optional profile dict (hour -> tariff/kWh); defaults
            to DEMO. Used only for reported baseline cost.

    Returns:
        Dictionary with solver status and scheduled process times (including
        each process's energy/cost under the given profiles for comparison),
        or None if infeasible
    """
    config = load_factory_config(factory_data)
    model, start_times, end_times = _build_base_model(config)

    # Objective: minimize the overall production completion time (makespan)
    makespan = model.NewIntVar(
        0, time_to_grid_floor(config.planning_horizon_hours), "makespan"
    )
    for process_id in start_times:
        model.Add(makespan >= end_times[process_id])
    model.Minimize(makespan)

    # Solve the model (deterministic single-worker; optional time limit)
    solver = _new_solver(max_time_seconds)
    status = solver.Solve(model)

    # Check if solution is feasible
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        return _extract_schedule(
            config,
            solver,
            "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            start_times,
            end_times,
            DEMO_SOLAR_PROFILE if solar_profile is None else solar_profile,
            DEMO_TARIFF_PROFILE if tariff_profile is None else tariff_profile,
            with_cost=True,
        )
    elif status == cp_model.MODEL_INVALID:
        raise SolverModelInvalidError(
            "CP-SAT rejected the baseline model as MODEL_INVALID"
        )
    elif status == cp_model.UNKNOWN:
        raise SolverUnknownError(
            "baseline solve hit the time limit without finding a solution "
            "and without proving infeasibility",
            solve_time_seconds=round(solver.WallTime(), 4),
        )
    elif status == cp_model.INFEASIBLE and raise_on_infeasible:
        raise SolverInfeasibleError(round(solver.WallTime(), 4))
    else:
        return None


# ---------------------------------------------------------------------------
# V2: Solar-aware schedule (maximize solar energy, makespan tiebreaker)
# ---------------------------------------------------------------------------

def create_solar_aware_schedule(factory_data, solar_profile=None,
                                tariff_profile=None, baseline_schedule=None,
                                max_time_seconds=None,
                                raise_on_infeasible=False):
    """
    Create a solar-aware production schedule that maximizes solar utilization
    for flexible processes while respecting all production constraints.

    Objective (unchanged from V2/V3 semantics): maximize the solar energy of
    flexible processes, with makespan as a secondary tiebreaker.

    Args:
        factory_data: Generic factory configuration
        solar_profile: Optional profile dict (hour -> kW); defaults to DEMO.
            Drives BOTH the optimization objective and reported energy.
        tariff_profile: Optional profile dict (hour -> tariff/kWh); defaults
            to DEMO. Used only for reported cost.
        baseline_schedule: Optional precomputed baseline schedule dict; reused
            for non-flexible pinning instead of solving the baseline again.

    Returns:
        Dictionary with solver status, scheduled process times, and energy
        metrics, or None if infeasible
    """
    config = load_factory_config(factory_data)
    profile = DEMO_SOLAR_PROFILE if solar_profile is None else solar_profile
    precision_problems = energy_precision_problems(
        config.processes, profile,
        DEMO_TARIFF_PROFILE if tariff_profile is None else tariff_profile,
    )
    if precision_problems:
        raise FactoryConfigError("; ".join(precision_problems))
    model, start_times, end_times = _build_base_model(config)

    # Non-flexible processes must remain at their baseline positions
    baseline = _pin_non_flexible_processes(
        config, model, start_times, baseline_schedule
    )
    if baseline is None:
        return None

    # Add solution hints from baseline schedule to speed up optimality proof
    # This gives CP-SAT a high-quality starting point
    baseline_start_units = {
        p["process_id"]: int(round(p["start_time"] * TIME_SCALE))
        for p in baseline["processes"]
    }
    fixed_start_units = {
        process.process_id: baseline_start_units[process.process_id]
        for process in config.processes
        if not process.is_flexible
    }
    for process in config.processes:
        if process.process_id in baseline_start_units:
            model.AddHint(
                start_times[process.process_id],
                baseline_start_units[process.process_id]
            )

    # Objective: maximize total ALLOCATED solar energy under the SHARED
    # half-hour-slot pool (makespan as a secondary tiebreaker). The pool lives
    # inside the model, so the optimizer sees exactly the physical solar
    # that post-solve reporting allocates.
    planning_horizon = time_to_grid_floor(config.planning_horizon_hours)

    # demand expressions are never consumed by the solar objective, so
    # the pool builder skips them entirely (identical model semantics).
    share, demand = _add_shared_solar_pool(
        config, model, start_times, profile,
        fixed_start_times=fixed_start_units,
        collect_demand=False,
    )

    # total_solar_score = SUM of allocated solar over ALL hours (0.1 kWh
    # units; per-process splits are realized by the reporting allocator).
    total_solar_score = sum(share.values(), 0)

    # Secondary objective: minimize makespan (tiebreaker only)
    makespan = model.NewIntVar(0, planning_horizon, "makespan")
    model.AddMaxEquality(makespan, list(end_times.values()))

    # Combined objective: maximize (total_solar_score * SOLAR_WEIGHT - makespan)
    # total_solar_score is in 0.1 kWh units; SOLAR_WEIGHT guarantees a gain
    # of one solar unit outweighs any possible makespan difference.
    SOLAR_WEIGHT = planning_horizon + 1
    model.Maximize(total_solar_score * SOLAR_WEIGHT - makespan)

    # Solve the model (deterministic single-worker; optional time limit)
    solver = _new_solver(max_time_seconds)
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        return _extract_schedule(
            config,
            solver,
            "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            start_times,
            end_times,
            profile,
            DEMO_TARIFF_PROFILE if tariff_profile is None else tariff_profile,
            with_cost=True,
        )
    elif status == cp_model.MODEL_INVALID:
        raise SolverModelInvalidError(
            "CP-SAT rejected the solar-aware model as MODEL_INVALID"
        )
    elif status == cp_model.UNKNOWN:
        raise SolverUnknownError(
            "solar-aware solve hit the time limit without finding a solution "
            "and without proving infeasibility",
            solve_time_seconds=round(solver.WallTime(), 4),
        )
    elif status == cp_model.INFEASIBLE and raise_on_infeasible:
        raise SolverInfeasibleError(round(solver.WallTime(), 4))
    else:
        return None


# ---------------------------------------------------------------------------
# V3: Solar + tariff cost-optimized schedule (primary: cost, tiebreak: makespan)
# ---------------------------------------------------------------------------

def create_cost_optimized_schedule(factory_data, solar_profile=None,
                                   tariff_profile=None, baseline_schedule=None,
                                   max_time_seconds=None,
                                   raise_on_infeasible=False):
    """
    Create a solar + tariff cost-optimized production schedule (Version 3+).    Primary objective: minimize total electricity cost.
    Secondary objective: minimize makespan (tiebreaker when cost is equal).

    Solar is a SHARED half-hour-slot pool inside the model (see
    _add_shared_solar_pool): the in-model energy and cost definitions are
    exactly the post-solve reporting definitions applied to each slot:
        solar allocated in a slot <= min(pool supply, total draw caps)
        grid energy per hour      = total demand - allocated solar
        cost per hour         = grid energy * tariff[hour]

    Args:
        factory_data: Generic factory configuration
        solar_profile: Optional profile dict (hour -> kW); defaults to DEMO.
            Drives BOTH the optimization objective and reported energy.
        tariff_profile: Optional profile dict (hour -> tariff/kWh); defaults
            to DEMO. Drives BOTH the optimization objective and reported cost.
        baseline_schedule: Optional precomputed baseline schedule dict; reused
            for non-flexible pinning instead of solving the baseline again.

    Returns:
        Dictionary with solver status, scheduled process times, energy metrics
        and energy costs, or None if infeasible
    """
    config = load_factory_config(factory_data)
    s_profile = DEMO_SOLAR_PROFILE if solar_profile is None else solar_profile
    t_profile = DEMO_TARIFF_PROFILE if tariff_profile is None else tariff_profile
    precision_problems = energy_precision_problems(
        config.processes, s_profile, t_profile
    )
    if precision_problems:
        raise FactoryConfigError("; ".join(precision_problems))

    model, start_times, end_times = _build_base_model(config)

    # Integer scaling for costs (0.001 currency resolution for DEMO tariffs)
    COST_SCALE = 1000

    planning_horizon = time_to_grid_floor(config.planning_horizon_hours)

    # Non-flexible processes must remain at their baseline positions: their
    # grid cost is then constant and is reported from the actual schedule.
    baseline = _pin_non_flexible_processes(
        config, model, start_times, baseline_schedule
    )
    if baseline is None:
        return None

    # Add solution hints from baseline schedule to speed up optimality proof
    # This gives CP-SAT a high-quality starting point
    baseline_start_units = {
        p["process_id"]: int(round(p["start_time"] * TIME_SCALE))
        for p in baseline["processes"]
    }
    fixed_start_units = {
        process.process_id: baseline_start_units[process.process_id]
        for process in config.processes
        if not process.is_flexible
    }
    for process in config.processes:
        if process.process_id in baseline_start_units:
            model.AddHint(
                start_times[process.process_id],
                baseline_start_units[process.process_id]
            )

    # Objective: minimize total grid electricity cost. Per clock hour:
    #     grid energy = process demand - allocated (shared) solar
    #     cost        = grid energy x tariff[hour]
    # All terms are integer linear expressions (solar units = 0.1 kWh).
    horizon_hours = (planning_horizon + TIME_SCALE - 1) // TIME_SCALE
    tariff_mille = {
        hour: int(round(get_tariff(hour, t_profile) * COST_SCALE))
        for hour in range(horizon_hours)
    }
    # Hours with a zero tariff contribute exactly 0 to the objective, so
    # the pool builder only constructs demand/share expressions for the
    # hours below (identical objective, smaller model).
    active_hours = {hour for hour in range(horizon_hours)
                    if tariff_mille[hour % 24] != 0}

    # Solar is a SHARED half-hour-slot pool INSIDE the model (see helper): the cost
    # objective therefore optimizes against physically available solar, the
    # same allocation rule post-solve reporting applies.
    share, demand = _add_shared_solar_pool(
        config, model, start_times, s_profile,
        fixed_start_times=fixed_start_units,
        collect_demand=True,
        active_hours=active_hours,
    )
    cost_terms = [
        tariff_mille[hour % 24] * (demand[hour] - share[hour])
        for hour in sorted(active_hours)
    ]
    total_cost_expr = sum(cost_terms, 0)

    # Secondary objective: minimize makespan (tiebreaker only)
    makespan = model.NewIntVar(0, planning_horizon, "makespan")
    model.AddMaxEquality(makespan, list(end_times.values()))

    # Primary objective: minimize total electricity cost.
    # The tiebreak scale guarantees any positive cost difference (1 unit =
    # 0.0001 currency) outweighs any possible makespan difference.
    MAKESPAN_TIEBREAK_SCALE = planning_horizon + 1
    model.Minimize(total_cost_expr * MAKESPAN_TIEBREAK_SCALE + makespan)

    # Solve the model (deterministic single-worker; optional time limit)
    solver = _new_solver(max_time_seconds)
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        return _extract_schedule(
            config,
            solver,
            "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            start_times,
            end_times,
            s_profile,
            t_profile,
            with_cost=True,
        )
    elif status == cp_model.MODEL_INVALID:
        raise SolverModelInvalidError(
            "CP-SAT rejected the cost-optimized model as MODEL_INVALID"
        )
    elif status == cp_model.UNKNOWN:
        raise SolverUnknownError(
            "cost-optimized solve hit the time limit without finding a "
            "solution and without proving infeasibility",
            solve_time_seconds=round(solver.WallTime(), 4),
        )
    elif status == cp_model.INFEASIBLE and raise_on_infeasible:
        raise SolverInfeasibleError(round(solver.WallTime(), 4))
    else:
        return None


# ---------------------------------------------------------------------------
# Readable console output (generic - only display names are printed)
# ---------------------------------------------------------------------------

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
    print(f"BASELINE SCHEDULE - {schedule.get('factory_name', 'Factory')}")
    print("=" * 80)
    print(f"Solver Status: {schedule['status']}")
    print(f"Overall Production Completion Time (Makespan): {schedule['makespan']} hours")
    print("=" * 80)
    print()

    # Print header
    print(f"{'Process Name':<20} {'Start':<10} {'End':<10} {'Duration':<10} "
          f"{'Power (kW)':<12} {'Flexible':<10}")
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
    print(f"SOLAR + TARIFF OPTIMIZED SCHEDULE - {schedule.get('factory_name', 'Factory')}")
    print("=" * 80)
    print(f"Solver Status: {schedule['status']}")
    print(f"Overall Production Completion Time (Makespan): {schedule['makespan']} hours")
    print("=" * 80)
    print()

    # Print header
    print(f"{'Process Name':<20} {'Start':<10} {'End':<10} {'Duration':<10} "
          f"{'Power (kW)':<12} {'Flexible':<10} {'Solar (kW)':<12} "
          f"{'Solar (kWh)':<12} {'Grid (kWh)':<12} {'Tariff':<12} {'Cost':<12}")
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
        baseline_start_times = {
            p["process_id"]: p["start_time"] for p in baseline_schedule["processes"]
        }
        for process in optimized_schedule["processes"]:
            if process["is_flexible"]:
                baseline_start = baseline_start_times[process["process_id"]]
                if abs(process["start_time"] - baseline_start) > 0.1:
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


# NOTE: this engine module intentionally contains NO factory definitions and
# NO demo-data imports beyond the energy profiles. The console entry point
# that runs the registered demo factories lives in optimizer/cli.py
# (run it with `python -m optimizer`).


if __name__ == "__main__":
    # Backward compatibility: the V1-V3 demo command still runs the demo,
    # now via the separated CLI layer.
    from optimizer.cli import main as _main

    _main()
