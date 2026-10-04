"""
ByteMe Public Optimizer API (DEMO/SIMULATED DATA ONLY)

STABLE PUBLIC INTERFACE for external applications.

    optimize(input) -> result

The caller provides a plain dict (or JSON string / JSON file path) in the
canonical input schema and receives a plain, JSON-safe result dict. The
caller never sees OR-Tools, solver variables, or internal model classes:
the engine may be refactored or replaced internally without breaking this
contract.

Optimization results use DEMO/SIMULATED factory and energy data only.
"""

import hashlib
import importlib.metadata
import json
import logging
import math
import time
import uuid

from optimizer.input_layer import (
    InputValidationError,
    _parse_json,
    _read_json_file,
    load_user_input,
)
from optimizer.models import TIME_SCALE, FactoryConfigError, time_to_grid_floor
from optimizer.optimizer import (
    DEFAULT_PUBLIC_TIME_LIMIT_SECONDS,
    SolverInfeasibleError,
    SolverModelInvalidError,
    SolverUnknownError,
    create_baseline_schedule,
    create_cost_optimized_schedule,
    get_tariff,
)
from optimizer.resource_limits import OptimizerResourceLimits
from optimizer.verification import (
    ScheduleVerificationError,
    verify_public_result,
    verify_result_metrics,
    verify_schedule,
)

# ---------------------------------------------------------------------------
# Public result status values (stable contract)
# ---------------------------------------------------------------------------

STATUS_OPTIMAL = "OPTIMAL"
STATUS_FEASIBLE = "FEASIBLE"
STATUS_INFEASIBLE = "INFEASIBLE"
STATUS_UNKNOWN = "UNKNOWN"          # limit hit, no solution, infeasibility NOT proven
STATUS_INVALID_INPUT = "INVALID INPUT"
STATUS_ERROR = "ERROR"

OPTIMIZATION_SCOPE_FULL = "FULL_DECLARED_CONSTRAINTS"
OPTIMIZATION_SCOPE_BASELINE_PINNED = (
    "CONDITIONAL_ON_BASELINE_PINNED_STARTS"
)

_API_VERSION = "2.0"
_LOGGER = logging.getLogger(__name__)


class OptimizerInputError(Exception):
    """Canonical error for caller-supplied problems.

    Raised by optimize_input() in strict mode; .problems is a list of
    human-readable problem strings.
    """

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class OptimizerInternalError(Exception):
    """Canonical error for unexpected internal failures (bug reports)."""

    def __init__(self, message, result=None):
        self.result = result
        super().__init__(message)


class _PipelineUnknown(Exception):
    """Internal: SolverUnknownError raised inside the pipeline; carries the
    validated config's warnings and recorded solve time so entry points can
    surface them."""

    def __init__(self, warnings, solve_time_seconds=None, baseline_schedule=None,
                 factory_name=None, optimization_scope=None):
        self.warnings = list(warnings)
        self.solve_time_seconds = solve_time_seconds
        self.baseline_schedule = baseline_schedule
        self.factory_name = factory_name
        self.optimization_scope = optimization_scope
        super().__init__("solver limit hit without a proven result")



# ---------------------------------------------------------------------------
# Internal helpers (private): build the structured result
# ---------------------------------------------------------------------------

def _error_result(status, errors, warnings=None):
    error_category = {
        STATUS_INVALID_INPUT: "VALIDATION_ERROR",
        STATUS_INFEASIBLE: "INFEASIBLE",
        STATUS_ERROR: "INTERNAL_ERROR",
    }.get(status)
    return {
        "api_version": _API_VERSION,
        "status": status,
        "result": None,
        "errors": list(errors),
        "warnings": list(warnings or []),
        "error_category": error_category,
    }


def _correlation_id(value):
    if value is None:
        return uuid.uuid4().hex
    if (not isinstance(value, str) or not value or len(value) > 128
            or any(not character.isprintable() for character in value)):
        raise ValueError(
            "correlation_id must be a non-empty printable string of at most "
            "128 characters"
        )
    return value


def _request_fingerprint(data):
    try:
        encoder = json.JSONEncoder(
            sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
        digest = hashlib.sha256()
        for chunk in encoder.iterencode(data):
            digest.update(chunk.encode("utf-8"))
    except (TypeError, ValueError, RecursionError, OverflowError):
        return None
    return digest.hexdigest()


def _solver_stage_metadata(result):
    stages = {}
    for name in ("baseline", "optimized"):
        schedule = result.get(name)
        if not isinstance(schedule, dict):
            stages[name] = None
            continue
        diagnostics = schedule.get("solver_diagnostics") or {}
        stages[name] = {
            "status": schedule.get("status"),
            "solve_time_seconds": schedule.get("solve_time_seconds"),
            "best_objective": diagnostics.get("best_objective"),
            "best_bound": diagnostics.get("best_bound"),
            "optimality_gap": diagnostics.get("optimality_gap"),
        }
    return stages


def _log_completed_request(
    result, data, objective, correlation_id, elapsed_seconds
):
    factory = data.get("factory") if isinstance(data, dict) else None
    processes = factory.get("processes") if isinstance(factory, dict) else None
    machines = factory.get("machines", []) if isinstance(factory, dict) else []
    try:
        package_version = importlib.metadata.version("ByteMe-Optimizer")
    except importlib.metadata.PackageNotFoundError:
        package_version = None
    try:
        import ortools
        ortools_version = ortools.__version__
    except (AttributeError, ImportError):
        ortools_version = None
    payload = result.get("result") or {}
    _LOGGER.info(
        "Optimizer request completed",
        extra={
            "correlation_id": correlation_id,
            "request_sha256": _request_fingerprint(data),
            "optimizer_status": result.get("status"),
            "objective": objective,
            "optimization_scope": payload.get("optimization_scope"),
            "process_count": len(processes) if isinstance(processes, list) else None,
            "machine_count": len(machines) if isinstance(machines, list) else None,
            "planning_horizon_hours": (
                factory.get("planning_horizon_hours")
                if isinstance(factory, dict) else None
            ),
            "solver_stages": _solver_stage_metadata(payload),
            "total_solve_time_seconds": payload.get("solve_time_seconds"),
            "elapsed_seconds": round(elapsed_seconds, 6),
            "api_version": _API_VERSION,
            "package_version": package_version,
            "ortools_version": ortools_version,
        },
    )


def _log_rejected_request(correlation_id, failure_class, problem_count):
    _LOGGER.info(
        "Optimizer request rejected",
        extra={
            "correlation_id": correlation_id,
            "optimizer_status": STATUS_INVALID_INPUT,
            "failure_class": failure_class,
            "problem_count": problem_count,
        },
    )


def _ok_result(result):
    error_category = (
        "INFEASIBLE" if result.get("status") == STATUS_INFEASIBLE else None
    )
    return {
        "api_version": _API_VERSION,
        "status": result["status"],
        "result": result,
        "errors": None,
        "warnings": list(result.get("warnings", [])),
        "error_category": error_category,
    }


def _project_process_rows(schedule):
    """Project a schedule into the public row schema (stable field set)."""
    rows = []
    for p in schedule["processes"]:
        rows.append({
            "process_id": p["process_id"],
            "process_name": p["process_name"],
            "start_time": p["start_time"],
            "end_time": p["end_time"],
            "duration_hours": p["duration_hours"],
            "power_kw": p["power_kw"],
            "is_flexible": p["is_flexible"],
            "machine_id": p.get("machine_id"),
            "capacity_units": p.get("capacity_units", 1),
            "machine_capacity": p.get("machine_capacity"),
            "quantity": p.get("quantity"),
            "solar_kwh": p["solar_energy_kwh"],
            "grid_kwh": p["grid_energy_kwh"],
            "energy_cost": p.get("energy_cost", 0.0),
            "tariff": p.get("tariff"),
        })
    return rows


def _project_schedule(schedule):
    """Public projection of one schedule."""
    return {
        "status": schedule["status"],
        "makespan_hours": schedule["makespan"],
        "solve_time_seconds": schedule.get("solve_time_seconds"),
        "solver_diagnostics": schedule.get("solver_diagnostics"),
        "processes": _project_process_rows(schedule),
        "energy": {
            "total_kwh": schedule["total_energy_kwh"],
            "solar_kwh": schedule["total_solar_kwh"],
            "grid_kwh": schedule["total_grid_kwh"],
        },
    }


def _optimization_scope(config):
    """Describe whether optimization can move every process from baseline."""
    if any(not process.is_flexible for process in config.processes):
        return OPTIMIZATION_SCOPE_BASELINE_PINNED
    return OPTIMIZATION_SCOPE_FULL


def _resolve_time_limit(data):
    """
    Resolve the solver time limit (seconds per solve).

    Default: DEFAULT_PUBLIC_TIME_LIMIT_SECONDS. Callers may override via
    options.max_time_seconds (additive, backward compatible). Invalid
    values raise InputValidationError.
    """
    options = data.get("options") if isinstance(data, dict) else None
    raw = options.get("max_time_seconds") if isinstance(options, dict) else None
    if raw is None:
        return DEFAULT_PUBLIC_TIME_LIMIT_SECONDS
    if (not isinstance(raw, (int, float)) or isinstance(raw, bool)
            or raw <= 0 or not _is_finite_number(raw)):
        raise InputValidationError([
            (
                f"options.max_time_seconds must be a positive finite number "
                f"of seconds, got {raw!r}"
            )
        ])
    return float(raw)


def _run_pipeline(source, objective, include_infeasible_time=False):
    """
    The SINGLE optimization pipeline used by every entry point:

        validation -> baseline (solved ONCE) -> optimization

    The baseline schedule is computed exactly once and handed to the
    optimization stage, which reuses it for non-flexible pinning instead of
    solving the baseline again. Pinning semantics are unchanged:
    non-flexible processes stay at their baseline makespan-optimal positions.

    Solver runs are deterministic (single-worker CP-SAT) and bounded by a
    time limit (default DEFAULT_PUBLIC_TIME_LIMIT_SECONDS, overridable via
    options.max_time_seconds). A solve that hits the limit without finding
    a solution and without proving infeasibility raises SolverUnknownError
    (mapped to STATUS_UNKNOWN by the entry points - never INFEASIBLE).

    Returns:
        (config, baseline, optimized) - validated FactoryConfig and schedule
        dicts (either schedule may be None when infeasible).

    Raises:
        InputValidationError / FactoryConfigError on invalid input; anything
        else propagates to the caller for error mapping.
    """
    # 1) Input validation (all problems collected, human-readable)
    # Normalize first so dict/JSON-string/path inputs behave identically and
    # the options section can be read exactly once.
    if not isinstance(source, dict):
        source = _parse_source(source)
    if objective not in ("cost", "solar"):
        raise InputValidationError([
            (
                "objective must be 'cost' or 'solar', "
                f"got {objective!r}"
            )
        ])
    config = load_user_input(source)

    # Optional solver limit (additive option; None = no limit for direct
    # engine callers, public API always applies a bounded default)
    max_time_seconds = _resolve_time_limit(source)

    # Caller-supplied energy profiles drive BOTH optimization and reported
    # results; DEMO profiles are fallback only (surfaced as warnings).
    energy = getattr(config, "energy", {})
    solar_profile = energy.get("solar_profile")
    tariff_profile = energy.get("tariff_profile")

    # Non-fatal notice: compatible_processes is DEMO metadata; a mismatch
    # with the machine assignment is reported but not an error.
    for machine in getattr(config, "machines", []):
        for pid in config.processes_by_machine.get(machine.machine_id, []):
            if machine.compatible_processes and pid not in machine.compatible_processes:
                config.warnings.append(
                    f"process '{pid}' is assigned to machine "
                    f"'{machine.machine_id}' but is not listed in its "
                    "compatible_processes (informational metadata; "
                    "scheduling proceeds)"
                )

    if (objective in ("cost", "solar") and config.processes
            and all(not p.is_flexible for p in config.processes)):
        config.warnings.append(
            "all processes are marked non-flexible (is_flexible=false); no "
            "demand shifting could be performed, and the optimized schedule "
            "is identical to the baseline schedule"
        )

    if objective == "cost":
        horizon_slots = time_to_grid_floor(config.planning_horizon_hours)
        horizon_hours = (horizon_slots + TIME_SCALE - 1) // TIME_SCALE
        if horizon_hours > 0:
            tariffs = [get_tariff(h, tariff_profile)
                       for h in range(horizon_hours)]
            if len(set(tariffs)) <= 1:
                config.warnings.append(
                    "energy.tariff_profile is uniform across the planning "
                    "horizon; tariff timing provides no cost-saving opportunity, "
                    "and shifting processes between hours cannot create "
                    "tariff savings"
                )

    # 2) Baseline: solved exactly once per optimization run
    try:
        baseline = create_baseline_schedule(
            config, solar_profile=solar_profile, tariff_profile=tariff_profile,
            max_time_seconds=max_time_seconds, raise_on_infeasible=True,
        )
    except SolverUnknownError as err:
        raise _PipelineUnknown(
            getattr(config, "warnings", []),
            getattr(err, "solve_time_seconds", None),
            factory_name=config.factory_name,
            optimization_scope=_optimization_scope(config),
        )
    except SolverInfeasibleError as err:
        if include_infeasible_time:
            return config, None, None, err.solve_time_seconds
        return config, None, None
    if baseline is None:
        if include_infeasible_time:
            return config, None, None, None
        return config, None, None
    verify_schedule(config, baseline)

    # 3) Optimization stage reuses the baseline for pinning (no re-solve)
    try:
        optimized = (
            create_cost_optimized_schedule(
                config, solar_profile=solar_profile,
                tariff_profile=tariff_profile, baseline_schedule=baseline,
                max_time_seconds=max_time_seconds,
                raise_on_infeasible=True,
            )
            if objective == "cost"
            else _run_solar(config, solar_profile, tariff_profile, baseline,
                            max_time_seconds, raise_on_infeasible=True)
        )
    except SolverUnknownError as err:
        base_t = baseline.get("solve_time_seconds") if baseline else None
        opt_t = getattr(err, "solve_time_seconds", None)
        tot_t = (
            round(base_t + opt_t, 4)
            if (base_t is not None and opt_t is not None)
            else (base_t or opt_t)
        )
        unknown_warnings = list(getattr(config, "warnings", []))
        if baseline is not None and baseline["status"] != STATUS_OPTIMAL:
            unknown_warnings.append(
                "baseline schedule is FEASIBLE (time limit reached before "
                "optimality could be proven)"
            )
        raise _PipelineUnknown(
            unknown_warnings, tot_t, baseline,
            factory_name=config.factory_name,
            optimization_scope=_optimization_scope(config),
        )
    except SolverInfeasibleError as err:
        if include_infeasible_time:
            return config, baseline, None, err.solve_time_seconds
        return config, baseline, None
    verify_schedule(config, optimized, baseline=baseline)
    if include_infeasible_time:
        return config, baseline, optimized, None
    return config, baseline, optimized


def _unknown_result(factory_name, objective, warnings, solve_time_seconds=None,
                    baseline_schedule=None, optimization_scope=None):
    """UNKNOWN status: solver limit hit, infeasibility NOT proven."""
    if baseline_schedule is None:
        solver_warning = (
            "solver hit its time limit without finding a feasible schedule "
            "and without proving infeasibility; retry with a larger "
            "options.max_time_seconds or simplify the problem"
        )
        baseline = None
        machine_utilization = None
    else:
        from optimizer.app import machine_utilization as calculate_machine_utilization
        solver_warning = (
            "optimized solver hit its time limit without producing an "
            "optimized schedule; the successful baseline schedule is available"
        )
        baseline = _project_schedule(baseline_schedule)
        machine_utilization = {
            "baseline": calculate_machine_utilization(baseline_schedule),
            "optimized": None,
        }
    return _ok_result({
        "status": STATUS_UNKNOWN,
        "factory_name": factory_name,
        "objective": objective,
        "optimization_scope": optimization_scope,
        "solve_time_seconds": solve_time_seconds,
        "baseline": baseline,
        "optimized": None,
        "comparison": None,
        "carbon": None,
        "machine_utilization": machine_utilization,
        "validation_errors": None,
        "warnings": list(warnings) + [solver_warning],
    })


def _optimize_internal(data, objective):
    """Runs the pipeline and projects the outcome into the public result."""
    config, baseline, optimized, infeasible_solve_time = _run_pipeline(
        data, objective, include_infeasible_time=True
    )
    optimization_scope = _optimization_scope(config)

    if baseline is not None and baseline["status"] != STATUS_OPTIMAL:
        # Baseline found but not proven optimal (time limit); surface it.
        config.warnings.append(
            "baseline schedule is FEASIBLE (time limit reached before "
            "optimality could be proven)"
        )

    if optimized is not None and optimized["status"] != STATUS_OPTIMAL:
        config.warnings.append(
            "optimized schedule is FEASIBLE (time limit reached before "
            "optimality could be proven); cost/solar results may be "
            "improvable"
        )

    base_time = baseline.get("solve_time_seconds") if baseline else None
    opt_time = optimized.get("solve_time_seconds") if optimized else None
    solve_times = [time for time in
                   (base_time, opt_time, infeasible_solve_time)
                   if time is not None]
    total_solve_time = round(sum(solve_times), 4) if solve_times else None

    if baseline is None or optimized is None:
        return _ok_result({
            "status": STATUS_INFEASIBLE,
            "factory_name": _factory_name(data),
            "objective": objective,
            "optimization_scope": optimization_scope,
            "solve_time_seconds": total_solve_time,
            "baseline": _project_schedule(baseline) if baseline else None,
            "optimized": None,
            "comparison": None,
            "carbon": None,
            "machine_utilization": None,
            "validation_errors": None,
            "warnings": list(getattr(config, "warnings", [])),
        })

    # 4) Structured public result (delegates metrics to the application layer)
    from optimizer.app import build_results
    results = build_results(config, baseline, optimized)
    verify_result_metrics(config, baseline, optimized, results)

    public = {
        "status": optimized["status"],
        "factory_name": results["factory_name"],
        "objective": objective,
        "optimization_scope": optimization_scope,
        "solve_time_seconds": total_solve_time,
        "baseline": _project_schedule(baseline),
        "optimized": _project_schedule(optimized),
        "comparison": {
            "makespan_baseline_hours": results["baseline"]["makespan_hours"],
            "makespan_optimized_hours": results["optimized"]["makespan_hours"],
            "cost_baseline": results["baseline"]["energy_cost"],
            "cost_optimized": results["optimized"]["energy_cost"],
            "cost_savings": results["comparison"]["cost_savings"],
            "cost_saving_percent": results["comparison"]["cost_saving_percent"],
            "solar_utilization_percent": results["comparison"][
                "solar_utilization_percent"
            ],
            "shifted_processes": results["comparison"]["shifted_processes"],
        },
        "machine_utilization": results["machine_utilization"],
        "carbon": results["carbon"],
        "validation_errors": None,
        "warnings": list(getattr(config, "warnings", [])),
    }
    verify_public_result(baseline, optimized, results, public)
    return _ok_result(public)


def _run_solar(config, solar_profile, tariff_profile, baseline_schedule=None,
               max_time_seconds=None, raise_on_infeasible=False):
    from optimizer.optimizer import create_solar_aware_schedule
    return create_solar_aware_schedule(
        config, solar_profile=solar_profile, tariff_profile=tariff_profile,
        baseline_schedule=baseline_schedule, max_time_seconds=max_time_seconds,
        raise_on_infeasible=raise_on_infeasible,
    )


def _factory_name(data):
    factory = data.get("factory", {}) if isinstance(data, dict) else {}
    if not isinstance(factory, dict):
        return "unknown"
    name = factory.get("factory_name")
    return name if isinstance(name, str) and name else "unknown"


def _is_finite_number(value):
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _parse_source(source):
    """Parse any supported source into a dict (JSON string / path / dict)."""
    if isinstance(source, dict):
        return source
    if isinstance(source, str):
        text = source.strip()
        if text.startswith("{"):
            return _parse_json(text)
        return _read_json_file(text)
    raise OptimizerInputError([
        (
            f"Unsupported input type {type(source).__name__}: provide a dict, "
            "JSON string, or JSON file path"
        )
    ])


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def optimize(source, objective="cost", strict=False, correlation_id=None):
    """
    Public optimizer entry point.

    Args:
        source: dict, JSON string, or JSON file path in the canonical input
            schema. File paths are for trusted/local callers only.
        objective: "cost" (default) or "solar".
        strict: if True, raises OptimizerInputError for invalid input and
            OptimizerInternalError for internal failures instead of
            returning an error result dict.

    Returns:
        dict (always JSON-safe):
            {
              "api_version": "2.0",
              "status": "OPTIMAL" | "FEASIBLE" | "INFEASIBLE"
                        | "UNKNOWN" | "INVALID INPUT" | "ERROR",
              "result": {...} | None,
              "errors": [str, ...] | None,
              "warnings": [str, ...],
              "error_category": str | None
            }

    Never raises for invalid input or infeasible problems in default mode;
    internal/unexpected failures return STATUS_ERROR with a generic message
    (no stack traces are exposed as normal results).
    """
    correlation_id = _correlation_id(correlation_id)
    started = time.perf_counter()
    data = None
    try:
        data = _parse_source(source)

        # Caller-level options may override the objective argument
        options = data.get("options") if isinstance(data, dict) else None
        if isinstance(options, dict):
            objective = options.get("objective", objective)

        result = _optimize_internal(data, objective)
        _log_completed_request(
            result, data, objective, correlation_id,
            time.perf_counter() - started,
        )
        return result

    except InputValidationError as exc:
        _log_rejected_request(
            correlation_id, type(exc).__name__, len(exc.problems)
        )
        if strict:
            raise OptimizerInputError(exc.problems) from exc
        return _error_result(STATUS_INVALID_INPUT, exc.problems,
                             getattr(exc, "warnings", []))

    except _PipelineUnknown as exc:
        # UNKNOWN: limit hit without a solution; infeasibility NOT proven.
        # Never reported as INFEASIBLE.
        result = _unknown_result(
            exc.factory_name or _factory_name(source), objective, exc.warnings,
            getattr(exc, "solve_time_seconds", None),
            getattr(exc, "baseline_schedule", None),
            getattr(exc, "optimization_scope", None),
        )
        if isinstance(data, dict):
            _log_completed_request(
                result, data, objective, correlation_id,
                time.perf_counter() - started,
            )
        return result

    except SolverModelInvalidError as exc:
        _LOGGER.error(
            "CP-SAT rejected an optimizer model",
            extra={
                "correlation_id": correlation_id,
                "optimizer_status": STATUS_ERROR,
                "failure_class": type(exc).__name__,
            },
        )
        if strict:
            raise OptimizerInternalError(str(exc)) from exc
        return _error_result(STATUS_ERROR, [
            (
                "Internal optimizer error. Please report this issue "
                "(error class: SolverModelInvalidError)."
            )
        ])

    except OptimizerInputError as exc:
        _log_rejected_request(
            correlation_id, type(exc).__name__, len(exc.problems)
        )
        if strict:
            raise
        return _error_result(STATUS_INVALID_INPUT, exc.problems, [])

    except FactoryConfigError as exc:
        _log_rejected_request(correlation_id, type(exc).__name__, 1)
        if strict:
            raise OptimizerInputError([str(exc)]) from exc
        return _error_result(STATUS_INVALID_INPUT, [str(exc)])

    except (OSError, json.JSONDecodeError) as exc:
        # unreadable file / malformed JSON
        message = f"Could not read input: {exc}"
        _log_rejected_request(correlation_id, type(exc).__name__, 1)
        if strict:
            raise OptimizerInputError([message]) from exc
        return _error_result(STATUS_INVALID_INPUT, [message])

    except ScheduleVerificationError as exc:
        _LOGGER.error(
            "Optimizer output failed independent verification",
            extra={
                "correlation_id": correlation_id,
                "optimizer_status": STATUS_ERROR,
                "failure_class": type(exc).__name__,
            },
        )
        if strict:
            raise OptimizerInternalError(
                "Optimizer output failed independent verification"
            ) from exc
        return _error_result(STATUS_ERROR, [
            (
                "Internal optimizer error. Please report this issue "
                "(error class: ScheduleVerificationError)."
            )
        ])

    except Exception as exc:  # unexpected internal failure
        _LOGGER.error(
            "Unexpected optimizer failure",
            extra={
                "correlation_id": correlation_id,
                "optimizer_status": STATUS_ERROR,
                "failure_class": type(exc).__name__,
            },
        )
        if strict:
            raise OptimizerInternalError(str(exc)) from exc
        # Generic message only - no stack traces in user-facing results.
        return _error_result(STATUS_ERROR, [
            (
                "Internal optimizer error. Please report this issue "
                f"(error class: {type(exc).__name__})."
            )
        ])


def optimize_request(
    request_data,
    objective="cost",
    strict=False,
    limits: OptimizerResourceLimits | None = None,
    correlation_id=None,
):
    """Optimize a parsed request object without filesystem path handling.

    Service handlers should pass the already-parsed JSON object here. The
    local ``optimize`` convenience API remains able to read trusted files.
    Optional ``limits`` let deployments bound process count, planning
    horizon, and per-stage solver time without imposing guessed defaults.
    """
    correlation_id = _correlation_id(correlation_id)
    if not isinstance(request_data, dict):
        problems = [(
            "Service requests must be parsed JSON objects; filesystem paths "
            "and JSON text are not accepted"
        )]
        _log_rejected_request(
            correlation_id, "NonObjectServiceRequest", len(problems)
        )
        if strict:
            raise OptimizerInputError(problems)
        return _error_result(STATUS_INVALID_INPUT, problems)
    if limits is not None and not isinstance(limits, OptimizerResourceLimits):
        raise TypeError("limits must be an OptimizerResourceLimits instance")

    limited_request = request_data
    problems = []
    factory = request_data.get("factory")
    if isinstance(factory, dict):
        processes = factory.get("processes")
        if (limits is not None and limits.max_processes is not None
                and isinstance(processes, list)
                and len(processes) > limits.max_processes):
            problems.append(
                "request exceeds configured max_processes "
                f"({limits.max_processes})"
            )
        horizon = factory.get("planning_horizon_hours")
        if (limits is not None
                and limits.max_planning_horizon_hours is not None
                and isinstance(horizon, (int, float))
                and not isinstance(horizon, bool)
                and _is_finite_number(horizon)
                and horizon > limits.max_planning_horizon_hours):
            problems.append(
                "request exceeds configured max_planning_horizon_hours "
                f"({limits.max_planning_horizon_hours})"
            )

    options = request_data.get("options")
    if (limits is not None
            and limits.max_solver_seconds_per_stage is not None
            and (options is None or isinstance(options, dict))):
        requested_time = (
            options.get("max_time_seconds")
            if isinstance(options, dict) else None
        )
        max_stage_time = limits.max_solver_seconds_per_stage
        if (requested_time is not None
                and isinstance(requested_time, (int, float))
                and not isinstance(requested_time, bool)
                and _is_finite_number(requested_time)
                and requested_time > max_stage_time):
            problems.append(
                "options.max_time_seconds exceeds configured "
                "max_solver_seconds_per_stage "
                f"({max_stage_time})"
            )
        elif requested_time is None:
            effective_default = min(
                DEFAULT_PUBLIC_TIME_LIMIT_SECONDS, max_stage_time
            )
            if effective_default < DEFAULT_PUBLIC_TIME_LIMIT_SECONDS:
                limited_request = dict(request_data)
                limited_options = dict(options or {})
                limited_options["max_time_seconds"] = effective_default
                limited_request["options"] = limited_options

    if problems:
        _log_rejected_request(correlation_id, "ResourceLimitExceeded", len(problems))
        if strict:
            raise OptimizerInputError(problems)
        return _error_result(STATUS_INVALID_INPUT, problems)
    return optimize(
        limited_request,
        objective=objective,
        strict=strict,
        correlation_id=correlation_id,
    )


def get_input_schema():
    """Return the formal JSON Schema for the canonical input."""
    from optimizer.schemas import INPUT_SCHEMA
    return json.loads(json.dumps(INPUT_SCHEMA))


def get_output_schema():
    """Return the formal JSON Schema for the canonical output."""
    from optimizer.schemas import OUTPUT_SCHEMA
    return json.loads(json.dumps(OUTPUT_SCHEMA))
