"""
ByteMe Input Layer (DEMO/SIMULATED DATA ONLY)

Clean, validated way to provide factory data WITHOUT modifying Python
source code. Accepts a JSON file path, a JSON string, or a plain dict and
returns a validated FactoryConfig ready for the generic engine.

All validation problems are collected (not fail-fast) and returned as one
clear, human-readable error listing every problem found.
"""

import json
import math

from optimizer.energy_data import DEMO_SOLAR_PROFILE, DEMO_TARIFF_PROFILE
from optimizer.models import (
    COST_DOMAIN_LIMIT,
    COST_SCALE,
    SOLAR_DOMAIN_LIMIT,
    SOLAR_SCALE,
    FactoryConfig,
    FactoryConfigError,
    ProcessSpec,
    TIME_SCALE,
    energy_precision_problems,
    time_to_grid_ceil,
    time_to_grid_floor,
    validate_factory_config,
)

# Energy-profile validation bounds (profiles are 24-hour cyclic; horizons
# longer than 24 h wrap via hour % 24 in the engine)
HOURS_PER_DAY = 24


class InputValidationError(Exception):
    """Raised when user input fails validation.

    .problems is a list of human-readable problem descriptions; str(exc)
    joins them into one readable report. .warnings carries any non-fatal
    notices collected before the error was raised.
    """

    def __init__(self, problems, warnings=None):
        self.problems = list(problems)
        self.warnings = list(warnings or [])
        super().__init__("; ".join(self.problems))


def _add_problem(problems, message):
    problems.append(message)


def _add_warning(warnings, message):
    warnings.append(message)


def _is_finite_number(value):
    """True when value is a finite (non-NaN, non-infinite) real number."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _normalize_profile_hours(profile):
    """
    Normalize profile hour keys to integers.

    JSON object keys are always strings ("0", "1", ...), so user input from
    JSON files/strings uses string hours. Accept both forms and convert
    numeric strings to int so the engine sees a consistent int-keyed dict.
    Non-numeric keys are left as-is for the validator to report.
    """
    if not isinstance(profile, dict):
        return profile
    normalized = {}
    for hour, value in profile.items():
        if isinstance(hour, str) and hour.strip().lstrip("-").isdigit():
            hour = int(hour.strip())
        normalized[hour] = value
    return normalized


def _validate_energy_profiles(solar_profile, tariff_profile, emission_factor,
                              problems, warnings):
    # Solar profile: hours -> non-negative finite kW
    if not isinstance(solar_profile, dict) or not solar_profile:
        _add_problem(problems, "energy.solar_profile must be a non-empty dict "
                               "mapping hour -> kW")
    else:
        for hour, kw in solar_profile.items():
            if not isinstance(hour, int) or isinstance(hour, bool) \
                    or not (0 <= hour < HOURS_PER_DAY):
                _add_problem(
                    problems,
                    f"energy.solar_profile has invalid hour {hour!r} "
                    f"(must be integer 0-23)",
                )
            if not _is_finite_number(kw) or kw < 0:
                _add_problem(
                    problems,
                    f"energy.solar_profile[{hour!r}] must be a finite number "
                    f">= 0 (no NaN/Infinity), got {kw!r}",
                )
        missing = [h for h in range(HOURS_PER_DAY) if h not in solar_profile]
        if missing:
            _add_warning(
                warnings,
                "energy.solar_profile does not define hours "
                f"{missing}; they are treated as 0 kW",
            )

    # Tariff profile: hours -> non-negative finite price per kWh
    if not isinstance(tariff_profile, dict) or not tariff_profile:
        _add_problem(problems, "energy.tariff_profile must be a non-empty dict "
                               "mapping hour -> tariff per kWh")
    else:
        for hour, tariff in tariff_profile.items():
            if not isinstance(hour, int) or isinstance(hour, bool) \
                    or not (0 <= hour < HOURS_PER_DAY):
                _add_problem(
                    problems,
                    f"energy.tariff_profile has invalid hour {hour!r} "
                    f"(must be integer 0-23)",
                )
            if not _is_finite_number(tariff) or tariff < 0:
                _add_problem(
                    problems,
                    f"energy.tariff_profile[{hour!r}] must be a finite number "
                    f">= 0 (no NaN/Infinity), got {tariff!r}",
                )
        missing = [h for h in range(HOURS_PER_DAY) if h not in tariff_profile]
        if missing:
            _add_warning(
                warnings,
                "energy.tariff_profile does not define hours "
                f"{missing}; they are treated as 0 currency units per kWh",
            )

    # Optional emission factor: non-negative finite kg CO2 per kWh
    if emission_factor is not None:
        if not _is_finite_number(emission_factor) or emission_factor < 0:
            _add_problem(
                problems,
                "energy.grid_emission_factor must be a finite number >= 0 "
                "(kg CO2 per kWh, no NaN/Infinity), got "
                f"{emission_factor!r}",
            )


def validate_user_input(data):
    """
    Validate a raw user-input dict (factory + energy sections).

    Returns a validated FactoryConfig on success.

    Raises InputValidationError with a human-readable list of ALL problems
    found (not just the first one).
    """
    problems = []
    warnings = []

    if not isinstance(data, dict):
        raise InputValidationError([
            "Input must be a JSON object/dict with 'factory' and 'energy' "
            "sections"
        ])

    factory = data.get("factory")
    energy = data.get("energy", {})

    # ---- factory section ------------------------------------------------
    if not isinstance(factory, dict):
        _add_problem(problems, "'factory' section is missing or not an object")
        factory = {}

    factory_name = factory.get("factory_name")
    if not isinstance(factory_name, str) or not factory_name:
        _add_problem(problems, "factory.factory_name must be a non-empty string")

    horizon = factory.get("planning_horizon_hours")
    if not _is_finite_number(horizon) or horizon <= 0:
        _add_problem(
            problems,
            "factory.planning_horizon_hours must be a positive finite number "
            f"(no NaN/Infinity), got {horizon!r}",
        )

    deadline = factory.get("production_deadline")
    if not _is_finite_number(deadline) or deadline <= 0:
        _add_problem(
            problems,
            "factory.production_deadline must be a positive finite number "
            f"(no NaN/Infinity), got {deadline!r}",
        )

    processes = factory.get("processes")
    if not isinstance(processes, list) or not processes:
        _add_problem(
            problems, "factory.processes must be a non-empty list of process objects"
        )
        processes = []

    machines = factory.get("machines", [])
    if not isinstance(machines, list):
        _add_problem(problems, "factory.machines must be a list of machine objects")
        machines = []

    # ---- process-level checks (collect all problems) --------------------
    process_ids = set()
    for index, proc in enumerate(processes):
        if not isinstance(proc, dict):
            _add_problem(problems, f"processes[{index}] must be an object")
            continue

        pid = proc.get("process_id")
        if not isinstance(pid, str) or not pid:
            _add_problem(problems, f"processes[{index}]: missing process_id")
        elif pid in process_ids:
            _add_problem(problems, f"duplicate process_id '{pid}'")
        else:
            process_ids.add(pid)

        duration = proc.get("duration_hours")
        if not _is_finite_number(duration) or duration < 0:
            _add_problem(
                problems, f"process '{pid or index}': negative, non-finite "
                          f"(NaN/Infinity) or invalid duration_hours "
                          f"({duration!r})"
            )
        elif duration == 0:
            _add_problem(
                problems, f"process '{pid or index}': duration_hours must be > 0"
            )
        elif (duration * TIME_SCALE) != int(duration * TIME_SCALE):
            _add_problem(
                problems,
                f"process '{pid or index}': duration_hours {duration} is not a "
                f"multiple of {1 / TIME_SCALE} h (half-hour grid)",
            )

        power = proc.get("power_kw")
        if not _is_finite_number(power) or power < 0:
            _add_problem(
                problems,
                f"process '{pid or index}': negative, non-finite (NaN/Infinity) "
                f"or invalid power_kw ({power!r})",
            )

        deps = proc.get("dependencies", [])
        if not isinstance(deps, list):
            _add_problem(
                problems, f"process '{pid or index}': dependencies must be a list"
            )

        if "is_flexible" in proc and not isinstance(proc["is_flexible"], bool):
            _add_problem(
                problems,
                f"process '{pid or index}': is_flexible must be true or false",
            )

        capacity_units = proc.get("capacity_units", 1)
        if (not isinstance(capacity_units, (int, float))
            or isinstance(capacity_units, bool)
            or not math.isfinite(capacity_units)
            or capacity_units < 1
            or not float(capacity_units).is_integer()):
            _add_problem(
                problems,
                f"process '{pid or index}': capacity_units must be a positive "
                f"integer, got {capacity_units!r}",
            )

        for field in ("earliest_start", "latest_finish"):
            if proc.get(field) is not None:
                value = proc[field]
                if not _is_finite_number(value) or value < 0:
                    _add_problem(
                        problems,
                        f"process '{pid or index}': {field} must be a finite "
                        f"number >= 0 (no NaN/Infinity), got {value!r}",
                    )

    # ---- machine-level checks -------------------------------------------
    machine_ids = set()
    for index, machine in enumerate(machines):
        if not isinstance(machine, dict):
            _add_problem(problems, f"machines[{index}] must be an object")
            continue

        mid = machine.get("machine_id")
        if not isinstance(mid, str) or not mid:
            _add_problem(problems, f"machines[{index}]: missing machine_id")
        elif mid in machine_ids:
            _add_problem(problems, f"duplicate machine_id '{mid}'")
        else:
            machine_ids.add(mid)

        # Machine numeric metadata is informational but must still be sane
        for field in ("capacity", "power_kw"):
            value = machine.get(field)
            if value is not None and not _is_finite_number(value):
                _add_problem(
                    problems,
                    f"machine '{mid or index}': {field} must be a finite "
                    f"number (no NaN/Infinity), got {value!r}",
                )

    # ---- cross-checks ----------------------------------------------------
    for proc in processes:
        if not isinstance(proc, dict):
            continue
        pid = proc.get("process_id") or "?"
        deps = proc.get("dependencies", []) or []
        for dep in deps:
            if dep not in process_ids:
                _add_problem(
                    problems,
                    f"process '{pid}': invalid dependency on unknown process "
                    f"'{dep}'",
                )
            elif dep == pid:
                _add_problem(
                    problems,
                    f"process '{pid}': depends on itself",
                )
        mid = proc.get("machine_id")
        if mid is not None and mid not in machine_ids:
            _add_problem(
                problems,
                f"process '{pid}': invalid machine assignment - machine "
                f"'{mid}' is not defined in the machines section",
            )

    # Circular dependency detection (DFS with recursion stack)
    graph = {}
    for proc in processes:
        if isinstance(proc, dict) and isinstance(proc.get("process_id"), str):
            graph[proc["process_id"]] = list(proc.get("dependencies", []) or [])

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {pid: WHITE for pid in graph}
    cycles = []

    def visit(node, stack):
        color[node] = GRAY
        for dep in graph.get(node, []):
            if color.get(dep) == GRAY:
                cycles.append(stack + [dep, node])
            elif color.get(dep) == WHITE:
                visit(dep, stack + [dep])
        color[node] = BLACK

    for pid in graph:
        if color[pid] == WHITE:
            visit(pid, [pid])

    for cycle in cycles:
        chain = " -> ".join(cycle)
        _add_problem(problems, f"circular dependency detected: {chain}")

    # Time-window feasibility on the half-hour grid. Lower bounds round up;
    # upper bounds round down, matching the solver's integer ticks.
    for proc in processes:
        if not isinstance(proc, dict):
            continue
        pid = proc.get("process_id") or "?"
        duration = proc.get("duration_hours")
        earliest = proc.get("earliest_start")
        if earliest is None:
            earliest = 0
        latest = proc.get("latest_finish")
        if (_is_finite_number(earliest) and _is_finite_number(duration)
                and _is_finite_number(deadline)
                and _is_finite_number(horizon)
                and (latest is None or _is_finite_number(latest))):
            finish_limit = latest if latest is not None else deadline
            if (time_to_grid_ceil(earliest)
                    + int(round(duration * TIME_SCALE))
                    > time_to_grid_floor(finish_limit)):
                _add_problem(
                    problems,
                    f"process '{pid}': impossible time window on the "
                    f"half-hour grid - cannot run for {duration} h between "
                    f"earliest_start {earliest} and finish limit {finish_limit}",
                )

    # ---- energy section ---------------------------------------------------
    demo_solar_supplied = "solar_profile" in energy
    demo_tariff_supplied = "tariff_profile" in energy
    solar_profile = energy.get("solar_profile", DEMO_SOLAR_PROFILE)
    tariff_profile = energy.get("tariff_profile", DEMO_TARIFF_PROFILE)
    emission_factor = energy.get("grid_emission_factor")

    # JSON keys are strings: normalize "0" -> 0 before validating/using
    solar_profile = _normalize_profile_hours(solar_profile)
    tariff_profile = _normalize_profile_hours(tariff_profile)

    _validate_energy_profiles(
        solar_profile, tariff_profile, emission_factor, problems, warnings
    )
    problems.extend(energy_precision_problems(
        processes, solar_profile, tariff_profile
    ))

    # Non-fatal notices (never invalid input):
    if not demo_solar_supplied:
        _add_warning(warnings,
                     "no energy.solar_profile supplied; the DEMO/SIMULATED "
                     "solar profile is used (not real solar data)")
    if not demo_tariff_supplied:
        _add_warning(warnings,
                     "no energy.tariff_profile supplied; the DEMO/SIMULATED "
                     "tariff profile is used (not real electricity prices)")
    if _is_finite_number(horizon) and horizon > HOURS_PER_DAY:
        days = int(math.ceil(horizon / HOURS_PER_DAY))
        _add_warning(
            warnings,
            f"planning horizon {horizon} h exceeds 24 h; 24-hour energy "
            f"profiles are cyclically repeated for {days} days "
            "(hour wraps with hour % 24)",
        )

    # ---- integer-scaling bounds (pre-solve, clear error instead of a
    # mysterious solver INFEASIBLE) ---------------------------------------
    # Cost encoding: power * tariff * duration * COST_SCALE must fit in the
    # per-process cost variable domain. Solar encoding: min(solar, power) *
    # duration * SOLAR_SCALE must fit in the solar variable domain.
    if tariff_profile and solar_profile:
        valid_tariffs = [
            v for k, v in tariff_profile.items()
            if isinstance(k, int) and not isinstance(k, bool)
            and 0 <= k < HOURS_PER_DAY
            and isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        valid_solar = [
            v for k, v in solar_profile.items()
            if isinstance(k, int) and not isinstance(k, bool)
            and 0 <= k < HOURS_PER_DAY
            and isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        max_tariff = max(valid_tariffs) if valid_tariffs else None
        max_solar = max(valid_solar) if valid_solar else None
        cost_limit = COST_DOMAIN_LIMIT / COST_SCALE
        solar_limit = SOLAR_DOMAIN_LIMIT / SOLAR_SCALE
        for proc in processes:
            if not isinstance(proc, dict):
                continue
            pid = proc.get("process_id") or "?"
            power = proc.get("power_kw")
            duration = proc.get("duration_hours")
            if not (isinstance(power, (int, float))
                    and not isinstance(power, bool)
                    and isinstance(duration, (int, float))
                    and not isinstance(duration, bool)):
                continue
            if max_tariff is not None:
                worst_cost = power * max_tariff * duration * COST_SCALE
                if worst_cost > COST_DOMAIN_LIMIT:
                    _add_problem(
                        problems,
                        f"process '{pid}': power_kw x tariff x duration "
                        f"exceeds the supported integer cost scaling (limit: "
                        f"product of power_kw x tariff_per_kwh x "
                        f"duration_hours <= {cost_limit:.0f}); got power "
                        f"{power} x tariff {max_tariff} x duration "
                        f"{duration} = {power * max_tariff * duration:.0f}. "
                        f"Reduce values or scale units down.",
                    )
            if max_solar is not None:
                worst_solar = min(max_solar, power) * duration * SOLAR_SCALE
                if worst_solar > SOLAR_DOMAIN_LIMIT:
                    _add_problem(
                        problems,
                        f"process '{pid}': solar availability x duration "
                        f"exceeds the supported integer solar scaling (limit: "
                        f"product of min(solar_kw, power_kw) x duration_hours "
                        f"<= {solar_limit:.0f}); got {min(max_solar, power)} "
                        f"x {duration} = "
                        f"{min(max_solar, power) * duration:.0f}.",
                    )

    if problems:
        raise InputValidationError(problems, warnings)

    # ---- build final config (schema the engine understands) --------------
    config_data = {
        "factory_name": factory_name,
        "factory_type": factory.get("factory_type"),
        "planning_horizon_hours": horizon,
        "production_deadline": deadline,
        "processes": processes,
        "machines": machines,
    }

    # Deep structural validation (same rules the engine enforces)
    try:
        config = validate_factory_config(config_data)
    except FactoryConfigError as exc:
        raise InputValidationError([str(exc)]) from exc

    # Attach validated energy data and warnings as attributes on the config
    config.energy = {
        "solar_profile": solar_profile,
        "tariff_profile": tariff_profile,
        "grid_emission_factor": emission_factor,
    }
    config.warnings = list(warnings)
    return config


def load_user_input(source):
    """
    Load user input from a JSON file path, a JSON string, or a dict.

    Returns a validated FactoryConfig with attached energy data.
    Raises InputValidationError on invalid input, json.JSONDecodeError on
    malformed JSON, OSError on unreadable files.
    """
    if isinstance(source, dict):
        data = source
    elif isinstance(source, str):
        text = source.strip()
        if text.startswith("{"):
            data = json.loads(text)
        else:
            with open(text, "r", encoding="utf-8") as handle:
                data = json.load(handle)
    else:
        raise InputValidationError([
            f"Unsupported input source type: {type(source).__name__} "
            f"(use a dict, JSON string, or file path)"
        ])
    return validate_user_input(data)


def format_validation_error(exc):
    """Format an InputValidationError as a human-readable report."""
    lines = ["Input validation failed:", ""]
    for index, problem in enumerate(exc.problems, start=1):
        lines.append(f"  {index}. {problem}")
    return "\n".join(lines)
