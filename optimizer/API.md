# ByteMe Public Optimizer API — Developer Guide

**DEMO/SIMULATED — NOT REAL INDUSTRIAL DATA**

This document targets developers integrating the ByteMe optimizer into
another backend/application. It covers the stable public interface, the
canonical input/output contracts, error handling, and integration patterns.
You do NOT need to know OR-Tools, CP-SAT, or any internal model classes.

## 1. What the optimizer does

Given a factory description (processes, machines, dependencies, deadlines)
and DEMO energy profiles (solar availability, electricity tariff, optional
grid emission factor), it:

1. **Validates** the input and reports every problem found.
2. Builds a **baseline schedule** (makespan-minimized, V1 semantics).
3. Builds an **optimized schedule** — primary objective `cost` (minimize
   grid electricity cost, V3/V4 semantics) or `solar` (maximize solar
   utilization, V2 semantics); makespan is only a tiebreaker. Flexible
   processes may shift; non-flexible processes stay at their baseline
   start times. Dependencies, machine-capacity constraints, deadlines, planning
   horizon, and per-process time windows are always respected.
4. Returns a structured comparison: schedules, makespans, energy split
   (solar/grid/total), costs, savings, shifted processes, machine
   utilization, and optional carbon (CO2) metrics.

## 2. What the optimizer does NOT do

- No real-world data: all results use DEMO/SIMULATED factory and energy
  data. Do not interpret outputs as real prices, weather, measurements,
  or industrial savings.
- No frontend, dashboard, authentication, user management, or database.
- No predictive forecasting; the solar profile is an input you supply.
- No real-time control; it is a planning-time scheduler.
- Non-preemptive scheduling on single-capacity machines; constant power
  while a process runs; half-hour scheduling grid.

## 3. Public interface

API version `2.0` closes nested input objects: unknown fields are rejected
instead of ignored, the envelope requires `error_category`, and solved
schedules include `solver_diagnostics`. This is a breaking validation/output
contract update; callers must send canonical fields and may ignore the new
diagnostics only if they already branch on `status`.

```python
from optimizer.public_api import optimize, optimize_request

result = optimize(input_source, objective="cost", strict=False)
```

- `input_source`: a **dict**, a **JSON string**, or a path to a **JSON file**
  in the canonical input schema. JSON text and paths are convenience inputs
  for trusted/local callers.
- Service handlers should call `optimize_request(parsed_object, ...)`, which
  accepts only an already-parsed JSON object. Never route untrusted request
  text or a request field through `optimize()` as a possible filesystem path.
  Parse request JSON with duplicate-property rejection before producing the
  object; duplicate keys are ambiguous and cannot be detected once collapsed
  into a Python dict.
- `objective`: `"cost"` (default) or `"solar"`. May also be set per-request
  via `input["options"]["objective"]`.
- `strict=False` (default): never raises for caller errors — always returns
  the result envelope. `strict=True`: raises `OptimizerInputError`
  (caller problems) or `OptimizerInternalError` (bugs) instead.
- Machine-readable contracts: `get_input_schema()`, `get_output_schema()`
  (Draft-07 JSON Schema dicts).

Import nothing else: `optimizer.public_api` is the only supported surface
for external callers. Everything behind it (CP-SAT engine, model classes,
scheduling encodings) may change without notice.

## 4. Input format (canonical schema)

```json
{
  "factory": {
    "factory_name": "Demo Widget Lab",
    "factory_type": "widgets",
    "planning_horizon_hours": 12,
    "production_deadline": 10,
    "processes": [
      {
        "process_id": "step_x",
        "process_name": "Step X",
        "duration_hours": 1,
        "power_kw": 10,
        "quantity": 100,
        "dependencies": [],
        "is_flexible": false,
        "machine_id": "core",
        "earliest_start": null,
        "latest_finish": null
      },
      {
        "process_id": "step_y",
        "process_name": "Step Y",
        "duration_hours": 1.5,
        "power_kw": 8,
        "dependencies": ["step_x"],
        "is_flexible": true,
        "machine_id": "core"
      }
    ],
    "machines": [
      {
        "machine_id": "core",
        "machine_name": "Shared Core Machine",
        "availability": "single unit",
        "capacity": 100,
        "power_kw": 12,
        "compatible_processes": ["step_x", "step_y"]
      }
    ]
  },
  "energy": {
    "solar_profile":  { "0": 0, "1": 0 },
    "tariff_profile": { "0": 0.45, "1": 0.45 },
    "grid_emission_factor": 0.35
  },
  "options": { "objective": "cost" }
}
```

Field semantics:

| Field | Meaning |
|---|---|
| `factory_id` / `factory_name` | stable factory identity; `factory_id` is optional and informational, while the name is required |
| `work_order_id` / `quantity_unit` | optional informational source identifier/unit; neither affects scheduling |
| `planning_horizon_hours` | scheduling window relative to time 0; effective limit rounds down to the last half-hour tick, and the final energy bucket is clipped to the actual horizon |
| `production_deadline` | hard completion deadline relative to time 0; effective limit rounds down to the last half-hour tick |
| `duration_hours` | operating duration in hours; must be a multiple of 0.5 |
| `power_kw` | constant process power while running, in kW; must be a multiple of 0.2 kW so each half-hour demand is exactly representable as 0.1 kWh |
| `quantity` | informational batch/production quantity |
| `dependencies` | process_ids that must finish before this starts; acyclic |
| `is_flexible` | `true` = eligible to move from its baseline start in optimization, though it may remain in place; `false` = pinned to its baseline start. This flag does not itself specify a caller-selected committed timestamp. |
| `machine_id` | enforced machine/resource assignment; processes sharing a machine are constrained by its capacity |
| `capacity_units` | optional positive integer demand on the assigned machine; defaults to 1 and must not exceed machine capacity |
| `machines[].capacity` | enforced maximum simultaneous capacity units; each assigned process consumes its `capacity_units` |
| `machines[].power_kw` | informational only; energy uses each process's `power_kw` |
| `machines[].availability` | informational only; no shift, calendar, or downtime constraint is applied |
| `machines[].compatible_processes` | informational only; a mismatch warns but does not prohibit assignment |
| `earliest_start` / `latest_finish` | optional per-process time window in hours relative to time 0; earliest start rounds up and latest finish rounds down to the half-hour grid |
| `energy.solar_profile` | elapsed hour index (0–23, int or numeric string) → site-wide available solar power in kW, in 0.2 kW increments; omitted hours in a supplied partial profile are 0 kW |
| `energy.tariff_profile` | elapsed hour index (0–23, int or numeric string) → tariff per consumed grid kWh, in 0.001 currency-unit increments; omitted hours in a supplied partial profile are 0 currency units/kWh |
| `energy.grid_emission_factor` | optional kg CO2/kWh; omit → carbon = `null` |
| `options.objective` | `"cost"` or `"solar"` (optional; overrides argument) |
| `options.max_time_seconds` | optional per-stage solver time limit (> 0); default 60 s per stage. One request can run baseline and optimized stages, so total solver time can approach twice the limit plus input/model/API overhead. |

Time values and capacity integers must fit the engine's supported integer
representation; extremely large finite values outside it return
`INVALID INPUT` rather than overflowing during model construction.

No industry-specific fields exist or are allowed. Omitting `energy` falls
back to the DEMO profiles. Full machine-readable contract:
`public_api.get_input_schema()` (also in `optimizer/schemas.py`).
Energy coefficients outside these exact integer-scale resolutions return
`INVALID INPUT`; they are never silently rounded into a different objective.
Changing `quantity` does not change scheduling, duration, or energy.

### Energy and Time Contract

All schedule times are relative to the beginning of the planning horizon:
time 0 is the planning origin, not a wall-clock timestamp. Start and end
times are expressed in hours and lie on the 0.5-hour grid. An earliest-start
bound rounds up to the next grid tick; a latest-finish, deadline, or horizon
bound rounds down to the preceding grid tick. The energy model includes every
hour touched by the effective horizon. If the final grid tick is at a
half-hour boundary, the final hourly bucket contains only that actual
half-hour of demand; no energy is charged beyond the scheduled interval.

Profile key `h` applies to elapsed interval `[h, h + 1)` from the planning
origin, so profile hour 0 begins at planning time 0 and is not a wall-clock
hour. An omitted profile uses DEMO data with a warning; an omitted hour in a
supplied partial profile means zero and is listed in a warning. For horizons
longer than 24 hours, each profile repeats with `h % 24`. Solar values are
available power in kW for one shared, site-wide pool. Within each half-hour
scheduling slot, available solar energy is shared among running processes
and capped by both the profile supply for that slot and each process's power
demand over its actual overlap. Solar is not stored or exported.
Tariffs are currency units per kWh of grid energy consumed; the API does not
select or convert a currency, so callers must use one consistent unit.
Reported energy is kWh, process power is kW, and carbon factors are kg
CO2/kWh. No timezone, daylight-saving, or wall-clock conversion is performed.
An integration adapter must map timestamped external profiles into these
planning-relative hourly buckets before calling the optimizer. These are
model semantics, not a requirement to supply real external data today.

## 5. Output format (canonical schema)

```json
{
  "api_version": "2.0",
  "status": "OPTIMAL",
  "error_category": null,
  "result": {
    "status": "OPTIMAL",
    "factory_name": "Demo Widget Lab",
    "objective": "cost",
    "solve_time_seconds": 0.042,
    "baseline": {
      "status": "OPTIMAL",
      "makespan_hours": 1.0,
      "solve_time_seconds": 0.015,
      "solver_diagnostics": {
        "status": "OPTIMAL",
        "best_objective": 2.0,
        "best_bound": 2.0,
        "optimality_gap": 0.0
      },
      "processes": [{
        "process_id": "step_x",
        "process_name": "Step X",
        "start_time": 0.0,
        "end_time": 1.0,
        "duration_hours": 1.0,
        "power_kw": 20.0,
        "is_flexible": true,
        "machine_id": "core",
        "capacity_units": 1,
        "machine_capacity": 1,
        "quantity": 100,
        "solar_kwh": 0.0,
        "grid_kwh": 20.0,
        "energy_cost": 14.0,
        "tariff": 0.7
      }],
      "energy": { "total_kwh": 20.0, "solar_kwh": 0.0, "grid_kwh": 20.0 }
    },
    "optimized": {
      "status": "OPTIMAL",
      "makespan_hours": 8.5,
      "solve_time_seconds": 0.027,
      "solver_diagnostics": {
        "status": "OPTIMAL",
        "best_objective": 1250017.0,
        "best_bound": 1250017.0,
        "optimality_gap": 0.0
      },
      "processes": [{
        "process_id": "step_x",
        "process_name": "Step X",
        "start_time": 7.5,
        "end_time": 8.5,
        "duration_hours": 1.0,
        "power_kw": 20.0,
        "is_flexible": true,
        "machine_id": "core",
        "capacity_units": 1,
        "machine_capacity": 1,
        "quantity": 100,
        "solar_kwh": 10.0,
        "grid_kwh": 10.0,
        "energy_cost": 5.0,
        "tariff": 0.5
      }],
      "energy": { "total_kwh": 20.0, "solar_kwh": 10.0, "grid_kwh": 10.0 }
    },
    "comparison": {
      "makespan_baseline_hours": 1.0,
      "makespan_optimized_hours": 8.5,
      "cost_baseline": 14.0,
      "cost_optimized": 5.0,
      "cost_savings": 9.0,
      "cost_saving_percent": 64.29,
      "solar_utilization_percent": 50.0,
      "shifted_processes": 1
    },
    "machine_utilization": {
      "baseline": {
        "core": {
          "busy_hours": 1.0,
          "capacity_unit_hours": 1.0,
          "capacity": 1,
          "peak_capacity_units": 1,
          "utilization_percent": 100.0
        }
      },
      "optimized": {
        "core": {
          "busy_hours": 1.0,
          "capacity_unit_hours": 1.0,
          "capacity": 1,
          "peak_capacity_units": 1,
          "utilization_percent": 11.76
        }
      }
    },
    "carbon": {
      "grid_emission_factor_kg_per_kwh": 0.4,
      "baseline_co2_kg": 8.0,
      "optimized_co2_kg": 4.0,
      "co2_reduction_kg": 4.0,
      "co2_reduction_percent": 50.0
    },
    "validation_errors": null
  },
  "errors": null,
  "warnings": []
}
```

Each schedule `processes` row contains exactly:
`process_id, process_name, start_time, end_time, duration_hours, power_kw,
is_flexible, machine_id, capacity_units, machine_capacity, quantity,
solar_kwh, grid_kwh, energy_cost, tariff`.

Machine utilization reports `busy_hours` (sum of process durations),
`capacity_unit_hours`, declared `capacity`, `peak_capacity_units`, and
capacity-normalized `utilization_percent`. Capacity-1 metrics retain their
previous interpretation.

Status values: `OPTIMAL` (proven best), `FEASIBLE` (valid, not proven
best — a warning explains why), `INFEASIBLE` (constraints cannot all
hold), `UNKNOWN` (solver hit its time limit without finding a solution
AND without proving infeasibility — never report as INFEASIBLE; retry
with a larger `options.max_time_seconds`), `INVALID INPUT` (see
`errors`), `ERROR` (internal failure; generic message, no stack trace).

If baseline solving succeeds but optimization returns `UNKNOWN`, the result
retains the baseline schedule and baseline machine utilization;
`optimized` and optimized machine utilization are `null`. If baseline
solving itself returns `UNKNOWN`, neither schedule is claimed.

`solve_time_seconds` is measured from CP-SAT `WallTime()`. Baseline and
optimized schedules report their individual solver times; the result-level
field reports the sum of the solver runs that were invoked. Timing naturally
varies between runs and is excluded from deterministic schedule comparisons.
An infeasible solve still reports its measured time; `null` means no solver
was invoked.
`carbon` is `null` when no `grid_emission_factor` is supplied — carbon is
never invented. Full contract: `get_output_schema()`.
The envelope is always JSON-safe (`json.dumps(result)` works as-is).
`error_category` is `VALIDATION_ERROR`, `INFEASIBLE`, or `INTERNAL_ERROR`
for those respective failure statuses, and `null` otherwise. `UNKNOWN` is a
solver outcome, not an internal error, including when `strict=True`.
Each solved schedule includes `solver_diagnostics` with solver status,
best objective, best bound, and relative optimality gap. Objective and bound
are in the model's encoded units, not currency or kWh. Benchmark-only
branches, conflicts, and model-size data are not included in normal results.

Each returned process-row `tariff` is the tariff in the hour containing the
process start (`floor(start_time)`). It is a start-hour indicator, not the
average tariff for the process; `energy_cost` integrates grid kWh against
the tariff of each overlapped hour. `solar_utilization_percent` is optimized
schedule solar kWh divided by optimized schedule total kWh, multiplied by
100 (zero when total energy is zero). Currency is caller-defined and is not
converted; use one consistent currency unit per kWh. Energy is kWh, power is
kW, and optional emissions are kg CO2/kWh.

### Determinism and solver limits

CP-SAT runs with a single worker to favor repeatable search, but time-limited
runs are not guaranteed to return identical incumbents or statuses across
repeated executions; wall-clock cutoffs can yield `FEASIBLE` in one run and
`UNKNOWN` in another. `solve_time_seconds` also naturally varies.
`options.max_time_seconds` (number > 0, optional) bounds each solver stage;
the default is 60 s per stage. One request may run both baseline and
optimized stages, so total solver time can approach twice that value, plus
parsing/model/API overhead. Each stage that finishes early returns its
`OPTIMAL` or `FEASIBLE` result; a solve that reaches its limit without a
solution or an infeasibility proof returns `UNKNOWN`. Determinism is not
guaranteed across OR-Tools versions or CPU platforms.

The library does not enforce a request-wide time deadline, request-body byte
limit, process-count limit, or planning-horizon limit. Parsing, validation,
and model construction happen outside the CP-SAT stage limits. A service
integration accepting untrusted requests must enforce transport-level body
limits and deployment-tested instance limits at the request boundary, and
enforce an end-to-end deadline around the optimizer call. The call is
synchronous: timing out a thread or coroutine does not stop work already in
the optimizer. If hard cancellation is required, run it in a supervised,
isolated worker process that can be terminated at the deadline, and bound
worker concurrency. There is no single safe universal model-size cap
without the deployment's memory/latency budget and workload profile.

### Integer scaling bounds (documented limit)

The model encodes cost/solar contributions as integers (cost resolution
0.001, solar resolution 0.1 kWh) with finite per-process variable
domains. The input layer therefore enforces upfront:

- `power_kw x tariff_per_kwh x duration_hours <= 100000` per process
- `min(solar_kw, power_kw) x duration_hours <= 100000` per process

Exceeding either bound returns `INVALID INPUT` naming the offending
process, before the solver is invoked.

### Warnings channel

The envelope always carries `warnings` (a list of strings, empty when
nothing notable; consumers can ignore entries they do not recognize).
Warnings cover non-fatal conditions such as: DEMO/SIMULATED solar or
tariff profile used because the caller supplied none; missing profile
hours treated as 0; planning horizons beyond 24 h (profiles cyclically
repeated, see below); all processes marked non-flexible (zero demand-shifting
degrees of freedom); and uniform/flat tariff profiles during cost optimization
(informing that timing provides no tariff-saving opportunity). Errors remain
reserved for invalid input.

### Energy profiles and multi-day horizons

Caller-supplied `energy.solar_profile` / `energy.tariff_profile` drive
BOTH the optimization objective and the reported energy/cost figures for
baseline and optimized schedules. The DEMO profiles are a fallback used
only when the caller omits them (surfaced as a warning — never silently).

Profiles are **24-hour cyclic**: horizons longer than 24 h wrap with
`hour % 24`, so day-2 hour 25 uses the hour-1 value. Multi-day use adds
an explicit warning; hours outside 0–23 in a profile remain a validation
error.

## 6. Example request / response

Request (file `examples/widget_lab.json`, abridged):

This example file is in the source repository, not in the installed wheel.
Installed-package users should provide their own JSON file or pass a request
dict.

```python
from optimizer.public_api import optimize
result = optimize("examples/widget_lab.json")
print(result["status"])                                # OPTIMAL
print(result["result"]["comparison"]["cost_savings"])  # 9.0
```

Response (values from the DEMO Widget Lab, DEMO/SIMULATED data):

```json
{
  "api_version": "2.0",
  "status": "OPTIMAL",
  "result": {
    "factory_name": "Demo Widget Lab",
    "comparison": { "cost_savings": 8.1, "cost_saving_percent": 64.29,
                    "shifted_processes": 2 },
    "carbon": { "co2_reduction_kg": 6.3, "co2_reduction_percent": 64.29 }
  },
  "errors": null,
  "warnings": [
    "energy.solar_profile does not define hours [12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]; they are treated as 0 kW",
    "energy.tariff_profile does not define hours [12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]; they are treated as 0 currency units per kWh"
  ]
}
```

Invalid input example — status `INVALID INPUT`, `result: null`, and
`errors` listing every problem, e.g.:

```json
{ "status": "INVALID INPUT", "result": null,
  "errors": ["process 'step_y': invalid dependency on unknown process 'ghost'"] }
```

## 7. How another backend can call it

The optimizer is a plain Python package — wrap it in whatever transport
your stack uses (function call, job queue, gRPC/HTTP handler you own):

```python
# Example HTTP-handler skeleton (transport owned by YOUR backend;
# ByteMe ships no web framework):
from optimizer.public_api import optimize_request

def handle_optimize_request(body: dict) -> dict:
    result = optimize_request(body)       # parsed object only
    return result                         # already JSON-safe
```

Guidance for integrators:
- Always branch on `result["status"]` first; `INVALID INPUT` carries
  `errors` (list of human-readable strings) suitable for showing users.
- `result` may be `None` (INVALID INPUT / INFEASIBLE / ERROR) — check
  before reading schedules.
- Persist `serialize_results`-style output via `json.dumps(result)`;
  the envelope round-trips losslessly.
- Treat `api_version` as the contract version; breaking changes bump it.

## 8. Normalized Factory-Data Adapter Contract

Factory-specific adapters belong outside `optimizer/` and must convert
source records into the canonical request object before calling
`optimize_request`. Source field names and source payloads must not be passed
into CP-SAT or added as arbitrary canonical-input properties.

The normalized boundary contains factory identifiers (`factory_id`,
`factory_name`), unique process/work-order identifiers, half-hour-grid
duration, constant power, optional informational quantity and units,
dependencies, machine/resource identifiers and enforced integer capacity,
flexibility, and relative time-window bounds. Energy profiles are hourly
planning-relative buckets keyed 0 through 23; partial profiles use zero for
missing hours and repeat every 24 hours. The adapter resolves timestamps to
an explicit planning origin before calling the optimizer. The canonical
request does not carry an origin or timezone, and the optimizer performs no
timezone, daylight-saving, or wall-clock conversion.

Fixed/committed starts are not a separate optimizer capability.
`is_flexible: false` pins a process to the optimizer's baseline start, not to
a source-system timestamp. An exact committed start at `t` can be represented
with `earliest_start: t` and `latest_finish: t + duration_hours`. Calendars,
alternate-machine routing, setup times, inventory, and source-system status semantics are not
supported. Machine power, availability, compatible-process lists, quantity,
and quantity units remain informational.

## 9. How to add a new factory

Data only — no engine changes:

- **One-off:** create a JSON file in the input schema (copy
  `examples/widget_lab.json` from a source checkout) and call `optimize(path)`.
  The repository example is not included in installed wheels.
- **Registered demo:** add a config dict to `AVAILABLE_FACTORIES` in
  `optimizer/factory_data.py`; the CLI (`python -m optimizer`) and the
  generic test battery pick it up automatically.

## 10. How to run tests

```bash
python -m unittest discover -s tests -v
```

Tests use synthetic or DEMO/SIMULATED data.

Run the non-gating synthetic scale benchmark separately. It covers process
counts 10/25/50/100, horizons 24/48/168 hours, and low/high contention. It
reports input validation, model construction, each solver stage, total API
time, repeated-run samples, solver diagnostics, and model size. The Phase 1
model-build optimization uses `LinearExpr.sum` for per-hour solar-cap and
demand expressions; `--legacy-python-sum` selects the equivalent previous
aggregation for before/after comparison. In a 24-case, two-repeat run with
a 1-second per-stage solver cap, mean per-case model-build time fell from
0.615 s to 0.543 s
 (11.7%) and mean total API time from 1.827 s to 1.751 s (4.2%). Median build
 time fell from 0.288 s to 0.222 s; worst build fell from 3.049 s to 2.772 s.
 All 24 cases produced identical CP-SAT model proto fingerprints and identical
 variable/constraint counts. Solver limits can still produce different
 FEASIBLE/UNKNOWN incumbents between runs; only OPTIMAL objective values are
used as exact objective comparisons. The final API status distribution was
unchanged; two individual solver stages changed between UNKNOWN and FEASIBLE
across paired repeats, as expected under the tight cap.

`--track-memory` enables `tracemalloc` and makes wall-time measurements
non-comparable; leave it off for performance comparisons. Normal CI does not
enforce wall-clock thresholds.

```bash
python benchmarks/bench_scale.py --repeats 3 --output benchmark-results.json
```

Use `--compare-hints` for a separate hinted/unhinted benchmark. Baseline
start-time hints remain hints, not constraints.

## 11. Error handling summary

| Situation | Returned status | Where details appear |
|---|---|---|
| Malformed JSON / unreadable trusted-local file | `INVALID INPUT` | `errors`; category `VALIDATION_ERROR` |
| Schema problems (missing/duplicate IDs, negative values, invalid machine refs, cycles, impossible windows, bad energy profiles) | `INVALID INPUT` | `errors` (all problems, human-readable) |
| Valid input, impossible constraints | `INFEASIBLE` | category `INFEASIBLE`; schedules `null` |
| Solver proves optimum / returns feasible solution | `OPTIMAL` / `FEASIBLE` | normal result |
| Solver cannot find a solution or prove infeasibility before its limit | `UNKNOWN` | normal solver outcome, not an internal error |
| Unexpected internal failure | `ERROR` | generic message in `errors`; category `INTERNAL_ERROR`; traceback goes to the server log only |

Raw stack traces are never part of the normal user-facing result.
