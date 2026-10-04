"""
ByteMe Application Layer (DEMO/SIMULATED DATA ONLY)

User-facing workflow around the generic optimizer:

    SELECT FACTORY  ->  REVIEW/EDIT INPUT  ->  RUN BASELINE
        ->  RUN OPTIMIZATION  ->  DISPLAY RESULTS

The application layer is fully factory-agnostic: it never branches on
factory or process names. Results include metrics, machine utilization,
solution status (OPTIMAL / FEASIBLE / INFEASIBLE / INVALID INPUT), an
optional carbon calculation, and JSON serialization.

All data handled is DEMO/SIMULATED data only.
"""

import json

from optimizer.input_layer import InputValidationError
from optimizer.models import FactoryConfigError
from optimizer.optimizer import (
    create_baseline_schedule,
    create_cost_optimized_schedule,
    create_solar_aware_schedule,
)


# Solution status values surfaced to users
STATUS_OPTIMAL = "OPTIMAL"
STATUS_FEASIBLE = "FEASIBLE"
STATUS_INFEASIBLE = "INFEASIBLE"
STATUS_INVALID_INPUT = "INVALID INPUT"


# ---------------------------------------------------------------------------
# Workflow steps
# ---------------------------------------------------------------------------

def list_factories():
    """SELECT FACTORY: list registered demo factories (data-only registry)."""
    from optimizer.factory_data import AVAILABLE_FACTORIES
    return sorted(AVAILABLE_FACTORIES.keys())


def select_factory(name):
    """SELECT FACTORY: load a registered demo factory by registry key."""
    from optimizer.factory_data import AVAILABLE_FACTORIES
    if name not in AVAILABLE_FACTORIES:
        raise FactoryConfigError(
            f"Unknown factory '{name}'. Available: {sorted(AVAILABLE_FACTORIES)}"
        )
    return AVAILABLE_FACTORIES[name]


def review_input(factory_config):
    """
    REVIEW/EDIT INPUT: return the factory configuration as an editable
    plain-data summary (dict). Users may modify it and pass it back to
    validate_user_input() / run_workflow().
    """
    if isinstance(factory_config, dict):
        data = factory_config
    else:
        data = {
            "factory": {
                "factory_name": factory_config.factory_name,
                "factory_type": factory_config.factory_type,
                "planning_horizon_hours": factory_config.planning_horizon_hours,
                "production_deadline": factory_config.production_deadline,
                "processes": [
                    {
                        "process_id": p.process_id,
                        "process_name": p.process_name,
                        "duration_hours": p.duration_hours,
                        "power_kw": p.power_kw,
                        "quantity": p.quantity,
                        "dependencies": list(p.dependencies),
                        "is_flexible": p.is_flexible,
                        "machine_id": p.machine_id,
                        "capacity_units": p.capacity_units,
                        "earliest_start": p.earliest_start,
                        "latest_finish": p.latest_finish,
                    }
                    for p in factory_config.processes
                ],
                "machines": [m.to_dict() for m in factory_config.machines],
            },
            "energy": getattr(factory_config, "energy", {}),
        }
    return data


# ---------------------------------------------------------------------------
# Run steps
# ---------------------------------------------------------------------------

def _run_scheduler(runner, config):
    """Run one scheduler function, mapping config errors to INVALID INPUT."""
    try:
        return runner(config)
    except FactoryConfigError:
        raise


def _config_profiles(config):
    """Energy profiles attached to a validated config (DEMO as fallback)."""
    energy = getattr(config, "energy", {})
    return (
        energy.get("solar_profile"),
        energy.get("tariff_profile"),
    )


def run_baseline(config):
    """RUN BASELINE: makespan-minimizing schedule (V1 semantics)."""
    solar_profile, tariff_profile = _config_profiles(config)
    return _run_scheduler(
        lambda cfg: create_baseline_schedule(
            cfg, solar_profile=solar_profile, tariff_profile=tariff_profile
        ),
        config,
    )


def run_optimization(config, objective="cost"):
    """
    RUN OPTIMIZATION for flexible processes.

    objective="solar": maximize solar utilization (V2 semantics)
    objective="cost":  minimize grid electricity cost (V3/V4 semantics,
                       makespan as tiebreaker)

    Uses the energy profiles attached to the config (caller-supplied when
    provided, DEMO/SIMULATED profiles otherwise).
    """
    if objective not in ("cost", "solar"):
        raise InputValidationError([
            "objective must be 'cost' or 'solar', "
            f"got {objective!r}"
        ])
    solar_profile, tariff_profile = _config_profiles(config)
    if objective == "solar":
        return _run_scheduler(
            lambda cfg: create_solar_aware_schedule(
                cfg, solar_profile=solar_profile, tariff_profile=tariff_profile
            ),
            config,
        )
    return _run_scheduler(
        lambda cfg: create_cost_optimized_schedule(
            cfg, solar_profile=solar_profile, tariff_profile=tariff_profile
        ),
        config,
    )


def run_workflow(factory_input, objective="cost"):
    """
    Legacy workflow entry, kept for backward compatibility.

    Orchestration (validation -> baseline -> optimization) is delegated to
    the SINGLE pipeline in optimizer.public_api - this function no longer
    implements its own optimization workflow. It only preserves the
    historical result shape:

        {"status", "results", "validation_errors"}

    where "results" carries the application-layer metrics (build_results).
    New integrations should prefer optimizer.public_api.optimize().

    Optimization results use DEMO/SIMULATED factory and energy data.
    """
    from optimizer.public_api import _PipelineUnknown, _run_pipeline

    try:
        config, baseline, optimized = _run_pipeline(factory_input, objective)
    except InputValidationError as exc:
        return {
            "status": STATUS_INVALID_INPUT,
            "results": None,
            "validation_errors": list(exc.problems),
        }
    except _PipelineUnknown as exc:
        # UNKNOWN: limit hit without a solution; infeasibility NOT proven.
        result = {
            "status": "UNKNOWN",
            "results": None,
            "validation_errors": None,
        }
        if exc.baseline_schedule is not None:
            result["baseline"] = exc.baseline_schedule
            result["optimized"] = None
            result["solve_time_seconds"] = exc.solve_time_seconds
            result["warnings"] = list(exc.warnings) + [
                "optimized solver hit its time limit without producing an "
                "optimized schedule; the successful baseline schedule is available"
            ]
        else:
            result["warnings"] = list(exc.warnings) + [
                "baseline solver hit its time limit without finding a feasible "
                "schedule or proving infeasibility"
            ]
        return result

    if baseline is None or optimized is None:
        return {
            "status": STATUS_INFEASIBLE,
            "results": None,
            "validation_errors": None,
        }

    return {
        "status": optimized["status"],
        "results": build_results(config, baseline, optimized),
        "validation_errors": None,
    }


# ---------------------------------------------------------------------------
# Metrics + carbon
# ---------------------------------------------------------------------------

def summarize_schedule(schedule):
    """Aggregate metrics for one schedule dict (generic)."""
    total_cost = sum(p.get("energy_cost", 0.0) for p in schedule["processes"])
    return {
        "status": schedule["status"],
        "makespan_hours": schedule["makespan"],
        "total_energy_kwh": schedule["total_energy_kwh"],
        "solar_kwh": schedule["total_solar_kwh"],
        "grid_kwh": schedule["total_grid_kwh"],
        "energy_cost": total_cost,
    }


def machine_utilization(schedule):
    """
    Capacity-weighted machine utilization and peak load over the makespan.
    """
    usage = {}
    for process in schedule["processes"]:
        machine_id = process.get("machine_id")
        if machine_id is None:
            continue
        hours = process["end_time"] - process["start_time"]
        demand = process.get("capacity_units", 1)
        machine = usage.setdefault(machine_id, {
            "busy_hours": 0.0,
            "capacity_unit_hours": 0.0,
            "capacity": process.get(
                "machine_capacity", 1
            ),
            "events": [],
        })
        machine["busy_hours"] += hours
        machine["capacity_unit_hours"] += demand * hours
        machine["capacity"] = process.get(
            "machine_capacity", machine["capacity"]
        )
        machine["events"].append((process["start_time"], demand))
        machine["events"].append((process["end_time"], -demand))

    makespan = schedule["makespan"] or 1.0
    results = {}
    for machine_id, machine in sorted(usage.items()):
        active = peak = 0
        for _, change in sorted(machine["events"]):
            active += change
            peak = max(peak, active)
        capacity = machine["capacity"] or 1
        results[machine_id] = {
            "busy_hours": round(machine["busy_hours"], 4),
            "capacity_unit_hours": round(machine["capacity_unit_hours"], 4),
            "capacity": capacity,
            "peak_capacity_units": peak,
            "utilization_percent": round(
                machine["capacity_unit_hours"]
                / (capacity * makespan) * 100,
                2,
            ),
        }
    return results


def count_shifted_processes(baseline, optimized):
    """Number of flexible processes moved between baseline and optimized."""
    baseline_starts = {
        p["process_id"]: p["start_time"] for p in baseline["processes"]
    }
    return sum(
        1
        for p in optimized["processes"]
        if p["is_flexible"]
        and abs(p["start_time"] - baseline_starts[p["process_id"]]) > 0.1
    )


def calculate_carbon(baseline_grid_kwh, optimized_grid_kwh, emission_factor):
    """
    Carbon calculation (OPTIONAL).

        grid_kWh * grid_emission_factor = CO2 (kg)

    Returns None when no emission factor is supplied - carbon results are
    reported as unavailable instead of inventing a value.
    """
    if emission_factor is None:
        return None

    baseline_co2 = baseline_grid_kwh * emission_factor
    optimized_co2 = optimized_grid_kwh * emission_factor
    reduction = baseline_co2 - optimized_co2
    return {
        "grid_emission_factor_kg_per_kwh": emission_factor,
        "baseline_co2_kg": baseline_co2,
        "optimized_co2_kg": optimized_co2,
        "co2_reduction_kg": reduction,
        "co2_reduction_percent": (
            reduction / baseline_co2 * 100 if baseline_co2 > 0 else 0.0
        ),
    }


def build_results(config, baseline, optimized):
    """Build the full baseline-vs-optimized comparison result."""
    baseline_summary = summarize_schedule(baseline)
    optimized_summary = summarize_schedule(optimized)

    savings = baseline_summary["energy_cost"] - optimized_summary["energy_cost"]
    solar = optimized_summary["solar_kwh"]
    total = optimized_summary["total_energy_kwh"]
    utilization = (solar / total * 100) if total > 0 else 0.0

    emission_factor = getattr(config, "energy", {}).get("grid_emission_factor")
    carbon = calculate_carbon(
        baseline_summary["grid_kwh"], optimized_summary["grid_kwh"],
        emission_factor,
    )

    return {
        "factory_name": config.factory_name,
        "baseline": baseline_summary,
        "optimized": optimized_summary,
        "comparison": {
            "cost_savings": savings,
            "cost_saving_percent": (
                savings / baseline_summary["energy_cost"] * 100
                if baseline_summary["energy_cost"] > 0 else 0.0
            ),
            "solar_utilization_percent": utilization,
            "shifted_processes": count_shifted_processes(baseline, optimized),
        },
        "carbon": carbon,  # None when no emission factor was supplied
        "machine_utilization": {
            "baseline": machine_utilization(baseline),
            "optimized": machine_utilization(optimized),
        },
        "baseline_schedule": baseline,
        "optimized_schedule": optimized,
    }


# ---------------------------------------------------------------------------
# Serialization + display
# ---------------------------------------------------------------------------

def serialize_results(results):
    """Serialize results (including schedules) to a JSON-compatible dict."""
    return json.loads(json.dumps(results, default=str))


def display_results(results):
    """
    DISPLAY RESULTS: human-readable console report comparing BASELINE vs
    OPTIMIZED, including machine utilization and carbon (when available).
    """
    print("=" * 80)
    print(f"OPTIMIZATION RESULTS - {results['factory_name']}")
    print(f"Solution Status: {results['optimized']['status']}")
    print("(Optimization results using DEMO/SIMULATED factory and energy data)")
    print("=" * 80)

    base, optz = results["baseline"], results["optimized"]
    comparison = results["comparison"]

    def row(label, key, fmt="{:.2f}", suffix=""):
        print(f"{label:<28} {fmt.format(base[key]):>12}{suffix}"
              f"   {fmt.format(optz[key]):>12}{suffix}")

    print(f"{'Metric':<28} {'BASELINE':>14}   {'OPTIMIZED':>14}")
    print("-" * 80)
    row("Makespan (hours)", "makespan_hours")
    row("Total Energy (kWh)", "total_energy_kwh")
    row("Solar Energy (kWh)", "solar_kwh")
    row("Grid Energy (kWh)", "grid_kwh")
    row("Energy Cost", "energy_cost")
    print("-" * 80)
    print(f"{'Cost Savings':<28} {comparison['cost_savings']:>27.2f}")
    print(f"{'Cost Saving Percent':<28} {comparison['cost_saving_percent']:>26.2f}%")
    print(f"{'Solar Utilization':<28} {comparison['solar_utilization_percent']:>26.2f}%")
    print(f"{'Shifted Processes':<28} {comparison['shifted_processes']:>26d}")

    for label, key in (("BASELINE", "baseline"), ("OPTIMIZED", "optimized")):
        print(f"\nMachine Utilization ({label}):")
        for machine_id, stats in results["machine_utilization"][key].items():
            print(f"  {machine_id:<28} {stats['busy_hours']:>6.2f} h   "
                  f"{stats['utilization_percent']:>6.2f}%")

    if results.get("carbon") is None:
        print("\nCarbon results: UNAVAILABLE (no grid_emission_factor supplied)")
    else:
        carbon = results["carbon"]
        print("\nCarbon (CO2, kg):")
        print(f"  {'Baseline':<26} {carbon['baseline_co2_kg']:>12.2f}")
        print(f"  {'Optimized':<26} {carbon['optimized_co2_kg']:>12.2f}")
        print(f"  {'Reduction':<26} {carbon['co2_reduction_kg']:>12.2f}")
        print(f"  {'Reduction Percent':<26} {carbon['co2_reduction_percent']:>11.2f}%")

    print("=" * 80)


def display_validation_errors(errors):
    """DISPLAY RESULTS for INVALID INPUT: human-readable error report."""
    print("=" * 80)
    print("INVALID INPUT")
    print("=" * 80)
    for index, problem in enumerate(errors, start=1):
        print(f"  {index}. {problem}")
    print("=" * 80)
