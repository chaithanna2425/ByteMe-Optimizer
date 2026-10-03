"""
ByteMe Generic Factory/Process/Machine Model

This module is the MODELING LAYER of the ByteMe optimizer. It validates any
factory configuration that follows the generic ByteMe schema (see
factory_data.py) and converts it into typed model objects.

The optimization engine (optimizer.py) consumes these objects and contains
NO factory-specific or process-specific logic: adding a new factory means
adding a new configuration dict, never changing the engine.

All data handled here is DEMO/SIMULATED data only.
"""

import math

TIME_SCALE = 2  # 1 scheduling unit = 0.5 hours (half-hour precision)
MAX_CP_SAT_INTEGER = (1 << 63) - 1

# ---------------------------------------------------------------------------
# Integer-scaling bounds of the CP-SAT model (documented contract).
#
# The engine encodes cost/solar contributions as integers:
#   cost units  = power_kw * tariff * duration_hours * COST_SCALE(1000)
#   solar units = min(solar_kw, power_kw) * duration_hours * SOLAR_SCALE(10)
# and stores each process's contribution in a variable of finite domain.
# Values beyond these bounds cannot be represented and would otherwise
# surface as an unexplained INFEASIBLE result - the input layer therefore
# rejects them upfront with a clear error naming the offending process.
# ---------------------------------------------------------------------------
COST_SCALE = 1000          # cost resolution: 0.001 currency units
SOLAR_SCALE = 10           # in-model solar resolution: 0.1 kWh
COST_DOMAIN_LIMIT = 100_000_000     # per-process cost variable domain
SOLAR_DOMAIN_LIMIT = 1_000_000      # per-process solar variable domain


def time_to_grid_floor(hours):
    """Return the greatest half-hour tick not later than ``hours``."""
    return int(math.floor(hours * TIME_SCALE + 1e-9))


def time_to_grid_ceil(hours):
    """Return the least half-hour tick not earlier than ``hours``."""
    return int(math.ceil(hours * TIME_SCALE - 1e-9))


def is_integer_scaled(value, scale):
    """Whether a finite numeric input is represented exactly at ``scale``."""
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not _is_finite_real(value)):
        return False
    scaled = value * scale
    return _is_finite_real(scaled) and math.isclose(
        scaled, round(scaled), rel_tol=0.0, abs_tol=1e-8
    )


def _is_finite_real(value):
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def energy_precision_problems(processes, solar_profile, tariff_profile):
    """Return inputs that the current integer energy scales cannot encode."""
    problems = []
    per_slot_scale = SOLAR_SCALE / TIME_SCALE

    tariffs = tariff_profile.values() if isinstance(tariff_profile, dict) else ()
    for tariff in tariffs:
        if (isinstance(tariff, (int, float)) and not isinstance(tariff, bool)
                and _is_finite_real(tariff)
                and not is_integer_scaled(tariff, COST_SCALE)):
            problems.append(
                "energy.tariff_profile values must be representable at "
                f"1/{COST_SCALE} currency per kWh; got {tariff!r}"
            )
            break

    valid_solar = []
    solar_values = solar_profile.items() if isinstance(solar_profile, dict) else ()
    for hour, solar_kw in solar_values:
        if (not isinstance(solar_kw, (int, float))
                or isinstance(solar_kw, bool)
                or not _is_finite_real(solar_kw)):
            continue
        if not is_integer_scaled(solar_kw, SOLAR_SCALE):
            problems.append(
                "energy.solar_profile values must be representable at "
                f"1/{SOLAR_SCALE} kW; hour {hour!r} has {solar_kw!r}"
            )
        valid_solar.append((hour, solar_kw))

    for process in processes:
        if isinstance(process, dict):
            process_id = process.get("process_id", "?")
            power_kw = process.get("power_kw")
        elif isinstance(process, ProcessSpec):
            process_id = process.process_id
            power_kw = process.power_kw
        else:
            continue
        if (not isinstance(power_kw, (int, float))
            or isinstance(power_kw, bool)
            or not _is_finite_real(power_kw)):
            continue
        if not is_integer_scaled(power_kw, per_slot_scale):
            problems.append(
                f"process '{process_id}': power_kw must be representable at "
                f"{1 / SOLAR_SCALE:g} kWh per half-hour; got {power_kw!r}"
            )
        for hour, solar_kw in valid_solar:
            draw_kw = min(solar_kw, power_kw)
            if not is_integer_scaled(draw_kw, per_slot_scale):
                problems.append(
                    f"process '{process_id}' with solar_profile hour {hour!r} "
                    f"cannot be represented at {1 / SOLAR_SCALE:g} kWh per "
                    f"half-hour (solar={solar_kw!r} kW, power={power_kw!r} kW)"
                )
                break
    return problems


def dependency_topological_order(dependencies_by_process):
    """Return a topological order and cyclic nodes using iterative Kahn traversal."""
    process_ids = list(dependencies_by_process)
    process_id_set = set(process_ids)
    successors = {process_id: [] for process_id in process_ids}
    indegree = {process_id: 0 for process_id in process_ids}
    for process_id, dependencies in dependencies_by_process.items():
        for dependency in set(dependencies):
            if dependency in process_id_set:
                successors[dependency].append(process_id)
                indegree[process_id] += 1

    ready = [process_id for process_id in process_ids
             if indegree[process_id] == 0]
    order = []
    cursor = 0
    while cursor < len(ready):
        process_id = ready[cursor]
        cursor += 1
        order.append(process_id)
        for successor in successors[process_id]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)

    cyclic = sorted(process_id for process_id in process_ids
                    if indegree[process_id] > 0)
    return order, cyclic


def max_supported_cost_per_process():
    """Largest power_kw * tariff * duration_hours product representable."""
    return COST_DOMAIN_LIMIT / COST_SCALE


def max_supported_solar_per_process():
    """Largest min(solar_kw, power_kw) * duration_hours product representable."""
    return SOLAR_DOMAIN_LIMIT / SOLAR_SCALE


class FactoryConfigError(ValueError):
    """Raised when a factory configuration is invalid.

    Invalid configurations must produce this clear error instead of silently
    producing an incorrect schedule.
    """


def _require(condition, message):
    if not condition:
        raise FactoryConfigError(message)


def _require_number(factory_name, where, name, value, minimum=None):
    _require(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and _is_finite_real(value),
        f"{factory_name}: {where}.{name} must be a finite number, got {value!r}",
    )
    if minimum is not None:
        _require(
            value >= minimum,
            f"{factory_name}: {where}.{name} must be >= {minimum}, got {value}",
        )


def _require_grid_time(factory_name, where, name, value, minimum=None):
    _require_number(factory_name, where, name, value, minimum)
    scaled_value = value * TIME_SCALE
    _require(
        _is_finite_real(scaled_value) and scaled_value <= MAX_CP_SAT_INTEGER,
        f"{factory_name}: {where}.{name} is outside the supported "
        "CP-SAT time domain",
    )


def _require_id_value(factory_name, where, name, value):
    _require(
        isinstance(value, str) and value,
        f"{factory_name}: {where}.{name} must be a non-empty string, got {value!r}",
    )


def _is_positive_cp_sat_integer(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 1 <= value <= MAX_CP_SAT_INTEGER
    return (
        isinstance(value, float)
        and math.isfinite(value)
        and value.is_integer()
        and 1 <= value <= MAX_CP_SAT_INTEGER
    )


class ProcessSpec:
    """Generic process definition (all values DEMO/SIMULATED)."""

    def __init__(self, data, factory_name="factory"):
        _require(isinstance(data, dict), f"{factory_name}: process must be a dict")
        where = f"process {data.get('process_id', '<missing id>')!r}"

        # --- identity ---
        self.process_id = data.get("process_id")
        _require_id_value(factory_name, where, "process_id", self.process_id)

        self.process_name = data.get("process_name", self.process_id)
        _require_id_value(factory_name, where, "process_name", self.process_name)

        # --- durations and power (energy = power_kw * duration_hours) ---
        _require(
            "duration_hours" in data and "power_kw" in data,
            f"{factory_name}: {where} requires 'duration_hours' and 'power_kw'",
        )
        _require_grid_time(
            factory_name, where, "duration_hours", data["duration_hours"], minimum=0
        )
        _require_number(factory_name, where, "power_kw", data["power_kw"], minimum=0)
        self.duration_hours = data["duration_hours"]
        self.power_kw = data["power_kw"]
        _require(
            self.duration_hours > 0,
            f"{factory_name}: {where}.duration_hours must be > 0",
        )

        # --- generic scheduling constraints ---
        deps = data.get("dependencies", [])
        _require(
            isinstance(deps, list),
            f"{factory_name}: {where}.dependencies must be a list",
        )
        _require(
            all(isinstance(dependency, str) for dependency in deps),
            f"{factory_name}: {where}.dependencies entries must be strings",
        )
        self.dependencies = list(deps)

        self.is_flexible = data.get("is_flexible", True)
        _require(
            isinstance(self.is_flexible, bool),
            f"{factory_name}: {where}.is_flexible must be a boolean",
        )

        # Optional machine/resource requirement (must exist in the machine list;
        # checked in FactoryConfig after machines are known).
        self.machine_id = data.get("machine_id")

        # Optional production quantity / batch information (informational).
        self.quantity = data.get("quantity")

        # Optional time-window constraints, in hours.
        if "earliest_start" in data and data["earliest_start"] is not None:
            _require_grid_time(
                factory_name, where, "earliest_start", data["earliest_start"], minimum=0
            )
        self.earliest_start = data.get("earliest_start")

        if "latest_finish" in data and data["latest_finish"] is not None:
            _require_grid_time(
                factory_name, where, "latest_finish", data["latest_finish"], minimum=0
            )
        self.latest_finish = data.get("latest_finish")

        # Duration must be representable on the half-hour scheduling grid.
        duration_units = self.duration_hours * TIME_SCALE
        _require(
            abs(duration_units - round(duration_units)) < 1e-9,
            f"{factory_name}: {where}.duration_hours must be a multiple of "
            f"{1 / TIME_SCALE} h on the scheduling grid, got {self.duration_hours}",
        )
        self.capacity_units = data.get("capacity_units", 1)
        _require(
            _is_positive_cp_sat_integer(self.capacity_units),
            f"{factory_name}: {where}.capacity_units must be a positive "
            f"CP-SAT integer, got {self.capacity_units!r}",
        )
        self.capacity_units = int(self.capacity_units)

    def to_dict(self):
        return {
            "process_id": self.process_id,
            "process_name": self.process_name,
            "duration_hours": self.duration_hours,
            "power_kw": self.power_kw,
            "dependencies": list(self.dependencies),
            "is_flexible": self.is_flexible,
            "machine_id": self.machine_id,
            "quantity": self.quantity,
            "earliest_start": self.earliest_start,
            "latest_finish": self.latest_finish,
            "capacity_units": self.capacity_units,
        }


class MachineSpec:
    """Generic machine/resource definition (all values DEMO/SIMULATED)."""

    def __init__(self, data, factory_name="factory"):
        _require(isinstance(data, dict), f"{factory_name}: machine must be a dict")
        where = f"machine {data.get('machine_id', '<missing id>')!r}"

        self.machine_id = data.get("machine_id")
        _require_id_value(factory_name, where, "machine_id", self.machine_id)

        self.machine_name = data.get("machine_name", self.machine_id)
        _require_id_value(factory_name, where, "machine_name", self.machine_name)

        # capacity is enforced as the maximum simultaneous capacity units.
        # availability, compatible_processes, and machine power are metadata;
        # they do not change process eligibility, calendars, or energy use.
        capacity = data.get("capacity", 1)
        if capacity is not None:
            _require(
                _is_positive_cp_sat_integer(capacity),
                f"{factory_name}: {where}.capacity must be a whole number "
                f"between 1 and {MAX_CP_SAT_INTEGER} (max simultaneous "
                f"capacity units), got {capacity!r}",
            )
        self.capacity = int(capacity) if capacity is not None else 1
        self.availability = data.get("availability", "unspecified")
        compatible = data.get("compatible_processes", [])
        _require(
            isinstance(compatible, list),
            f"{factory_name}: {where}.compatible_processes must be a list",
        )
        self.compatible_processes = list(compatible)
        self.power_kw = data.get("power_kw")

    def to_dict(self):
        return {
            "machine_id": self.machine_id,
            "machine_name": self.machine_name,
            "capacity": self.capacity,
            "availability": self.availability,
            "compatible_processes": list(self.compatible_processes),
            "power_kw": self.power_kw,
        }


class FactoryConfig:
    """
    Validated generic factory configuration.

    Built from a plain configuration dict following the ByteMe schema.
    Performs full structural validation (dependencies, machines, deadlines)
    and raises FactoryConfigError with a clear message on invalid data.
    """

    def __init__(self, data):
        _require(isinstance(data, dict), "factory configuration must be a dict")
        factory_name = data.get("factory_name", "<unnamed factory>")

        self.factory_name = data.get("factory_name")
        _require_id_value(factory_name, "factory", "factory_name", self.factory_name)

        # Optional informational industry/type label (e.g. "chocolate").
        # Purely descriptive - the engine never branches on it.
        factory_type = data.get("factory_type")
        if factory_type is not None:
            _require_id_value(factory_name, "factory", "factory_type", factory_type)
        self.factory_type = factory_type

        _require(
            "planning_horizon_hours" in data and "production_deadline" in data,
            f"{self.factory_name}: requires 'planning_horizon_hours' and "
            f"'production_deadline'",
        )
        _require_grid_time(
            factory_name,
            "factory",
            "planning_horizon_hours",
            data["planning_horizon_hours"],
            minimum=0,
        )
        _require_grid_time(
            factory_name,
            "factory",
            "production_deadline",
            data["production_deadline"],
            minimum=0,
        )
        self.planning_horizon_hours = data["planning_horizon_hours"]
        self.production_deadline = data["production_deadline"]
        _require(
            self.planning_horizon_hours > 0,
            f"{self.factory_name}: factory.planning_horizon_hours must be > 0",
        )
        _require(
            self.production_deadline > 0,
            f"{self.factory_name}: factory.production_deadline must be > 0",
        )
        _require(
            self.production_deadline <= self.planning_horizon_hours,
            f"{self.factory_name}: production_deadline "
            f"({self.production_deadline}) must not exceed planning_horizon_hours "
            f"({self.planning_horizon_hours})",
        )

        # --- processes ---
        raw_processes = data.get("processes")
        _require(
            isinstance(raw_processes, list) and raw_processes,
            f"{self.factory_name}: 'processes' must be a non-empty list",
        )
        self.processes = [ProcessSpec(p, self.factory_name) for p in raw_processes]

        # --- machines (optional; omit for no resource modeling) ---
        raw_machines = data.get("machines", [])
        _require(
            isinstance(raw_machines, list),
            f"{self.factory_name}: 'machines' must be a list",
        )
        self.machines = [MachineSpec(m, self.factory_name) for m in raw_machines]

        self._validate_references()
        self._validate_acyclic_dependencies()
        self._validate_deadlines()

    # -- validation helpers -------------------------------------------------

    def _validate_references(self):
        process_ids = [p.process_id for p in self.processes]
        machine_ids = {m.machine_id for m in self.machines}

        # Unique ids
        _require(
            len(process_ids) == len(set(process_ids)),
            f"{self.factory_name}: duplicate process_id values in configuration",
        )
        _require(
            len(machine_ids) == len({m.machine_id for m in self.machines}),
            f"{self.factory_name}: duplicate machine_id values in configuration",
        )

        process_id_set = set(process_ids)
        for process in self.processes:
            for dep in process.dependencies:
                _require(
                    dep in process_id_set,
                    f"{self.factory_name}: process '{process.process_id}' depends "
                    f"on unknown process '{dep}'",
                )
                _require(
                    dep != process.process_id,
                    f"{self.factory_name}: process '{process.process_id}' depends "
                    f"on itself",
                )
            if process.machine_id is not None:
                _require(
                    process.machine_id in machine_ids,
                    f"{self.factory_name}: process '{process.process_id}' requires "
                    f"unknown machine '{process.machine_id}'",
                )
                machine = self.get_machine(process.machine_id)
                _require(
                    process.capacity_units <= machine.capacity,
                    f"{self.factory_name}: process '{process.process_id}' "
                    f"requires {process.capacity_units} capacity_units but "
                    f"machine '{machine.machine_id}' has capacity "
                    f"{machine.capacity}",
                )
            else:
                _require(
                    process.capacity_units == 1,
                    f"{self.factory_name}: process '{process.process_id}' "
                    "requires machine_id when capacity_units is greater than 1",
                )

    def _validate_acyclic_dependencies(self):
        dependencies = {
            process.process_id: process.dependencies
            for process in self.processes
        }
        _, cyclic = dependency_topological_order(dependencies)
        _require(
            not cyclic,
            f"{self.factory_name}: dependency cycle detected among processes: "
            f"{cyclic}",
        )

    def _validate_deadlines(self):
        # Lower bounds round up and finish bounds round down to the grid.
        for process in self.processes:
            start_tick = time_to_grid_ceil(process.earliest_start or 0)
            finish_limit = (process.latest_finish
                            if process.latest_finish is not None
                            else self.production_deadline)
            finish_tick = time_to_grid_floor(finish_limit)
            duration_ticks = int(round(process.duration_hours * TIME_SCALE))
            _require(
                start_tick + duration_ticks <= finish_tick,
                f"{self.factory_name}: process '{process.process_id}' cannot fit "
                f"on the half-hour grid between earliest_start "
                f"{process.earliest_start or 0} and finish limit "
                f"{finish_limit} (duration {process.duration_hours} h)",
            )

    # -- accessors -----------------------------------------------------------

    def get_process(self, process_id):
        for process in self.processes:
            if process.process_id == process_id:
                return process
        raise FactoryConfigError(
            f"{self.factory_name}: unknown process '{process_id}'"
        )

    def get_machine(self, machine_id):
        for machine in self.machines:
            if machine.machine_id == machine_id:
                return machine
        raise FactoryConfigError(
            f"{self.factory_name}: unknown machine '{machine_id}'"
        )

    @property
    def process_ids(self):
        return [p.process_id for p in self.processes]

    @property
    def processes_by_machine(self):
        """Mapping machine_id -> [process ids requiring that machine]."""
        mapping = {}
        for process in self.processes:
            if process.machine_id is not None:
                mapping.setdefault(process.machine_id, []).append(
                    process.process_id
                )
        return mapping


def validate_factory_config(data):
    """
    Validate a raw factory configuration dict.

    Returns a FactoryConfig on success; raises FactoryConfigError with a
    clear message on invalid data instead of silently producing an
    incorrect schedule.
    """
    return FactoryConfig(data)
