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
   start times. Dependencies, machine no-overlap, deadlines, planning
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

```python
from optimizer.public_api import optimize

result = optimize(input_source, objective="cost", strict=False)
```

- `input_source`: a **dict**, a **JSON string**, or a path to a **JSON file**
  in the canonical input schema.
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
    "solar_profile":  { "0": 0, "1": 0, "...": 0 },
    "tariff_profile": { "0": 0.45, "...": 0.45 },
    "grid_emission_factor": 0.35
  },
  "options": { "objective": "cost" }
}
```

Field semantics:

| Field | Meaning |
|---|---|
| `planning_horizon_hours` | scheduling window; effective limit rounds down to the last half-hour tick, and energy buckets cover every hour touched by that grid-aligned horizon |
| `production_deadline` | hard completion deadline for all processes; effective limit rounds down to the last half-hour tick |
| `duration_hours` | operating duration; must be a multiple of 0.5 |
| `power_kw` | constant power draw while running; must be a multiple of 0.2 kW so each half-hour demand is exactly representable as 0.1 kWh; integer-scaling bound: `power_kw x tariff_per_kwh x duration_hours <= 100000` per process |
| `quantity` | informational batch/production quantity |
| `dependencies` | process_ids that must finish before this starts; acyclic |
| `is_flexible` | `true` = optimizer may shift; `false` = pinned to baseline start |
| `machine_id` | machine requirement; processes sharing a machine never overlap |
| `capacity_units` | optional positive integer demand on the assigned machine; defaults to 1 and must not exceed machine capacity |
| `earliest_start` / `latest_finish` | optional per-process time window (hours); earliest start rounds up and latest finish rounds down to the half-hour grid |
| `energy.solar_profile` | hour (0–23, int or numeric string) → available kW in 0.2 kW increments; cyclically repeated for horizons > 24 h |
| `energy.tariff_profile` | hour (0–23) → currency per kWh in 0.001 increments (DEMO values only if omitted) |
| `energy.grid_emission_factor` | optional kg CO2/kWh; omit → carbon = `null` |
| `options.objective` | `"cost"` or `"solar"` (optional; overrides argument) |
| `options.max_time_seconds` | optional per-solve solver time limit (> 0); default 60 s per solve |

No industry-specific fields exist or are allowed. Omitting `energy` falls
back to the DEMO profiles. Full machine-readable contract:
`public_api.get_input_schema()` (also in `optimizer/schemas.py`).
Energy coefficients outside these exact integer-scale resolutions return
`INVALID INPUT`; they are never silently rounded into a different objective.

## 5. Output format (canonical schema)

```json
{
  "api_version": "1.0",
  "status": "OPTIMAL",
  "result": {
    "status": "OPTIMAL",
    "factory_name": "Demo Widget Lab",
    "objective": "cost",
    "solve_time_seconds": 0.042,
    "baseline":  { "status": "...", "makespan_hours": 0.0, "solve_time_seconds": 0.015,
                   "processes": [ ... ], "energy": { "total_kwh": 0.0,
                   "solar_kwh": 0.0, "grid_kwh": 0.0 } },
    "optimized": { "...same shape as baseline..." },
    "comparison": {
      "makespan_baseline_hours": 3.0,
      "makespan_optimized_hours": 8.5,
      "cost_baseline": 14.0,
      "cost_optimized": 5.0,
      "cost_savings": 9.0,
      "cost_saving_percent": 64.29,
      "solar_utilization_percent": 64.29,
      "shifted_processes": 2
    },
    "machine_utilization": {
      "baseline":  { "core": { "busy_hours": 2.5, "utilization_percent": 83.33 } },
      "optimized": { "core": { "busy_hours": 2.5, "utilization_percent": 29.41 } }
    },
    "carbon": {
      "grid_emission_factor_kg_per_kwh": 0.4,
      "baseline_co2_kg": 9.8,
      "optimized_co2_kg": 3.5,
      "co2_reduction_kg": 6.3,
      "co2_reduction_percent": 64.29
    },
    "validation_errors": null
  },
  "errors": null,
  "warnings": []
}
```

Each schedule `processes` row contains exactly:
`process_id, process_name, start_time, end_time, duration_hours, power_kw,
is_flexible, machine_id, quantity, solar_kwh, grid_kwh, energy_cost, tariff`.

Status values: `OPTIMAL` (proven best), `FEASIBLE` (valid, not proven
best — a warning explains why), `INFEASIBLE` (constraints cannot all
hold), `UNKNOWN` (solver hit its time limit without finding a solution
AND without proving infeasibility — never report as INFEASIBLE; retry
with a larger `options.max_time_seconds`), `INVALID INPUT` (see
`errors`), `ERROR` (internal failure; generic message, no stack trace).

`solve_time_seconds` is measured from CP-SAT `WallTime()`. Baseline and
optimized schedules report their individual solver times; the result-level
field reports the sum of the solver runs that were invoked. Timing naturally
varies between runs and is excluded from deterministic schedule comparisons.
An infeasible solve still reports its measured time; `null` means no solver
was invoked.
`carbon` is `null` when no `grid_emission_factor` is supplied — carbon is
never invented. Full contract: `get_output_schema()`.
The envelope is always JSON-safe (`json.dumps(result)` works as-is).

### Determinism and solver limits

Solves are **deterministic**: CP-SAT runs with a single worker, so the
same input + the same pinned OR-Tools version + the same platform
produce byte-identical results (enforced by tests). Degenerate optima
are resolved identically on repeated runs. `options.max_time_seconds`
(number > 0, optional) bounds each solve; the default is a bounded
public-API limit (60 s per solve). Each solve that finishes early keeps
its `OPTIMAL`/`FEASIBLE` result; a solve that hits the limit with no
solution and no infeasibility proof returns status `UNKNOWN`. Extreme
inputs can exceed the model's integer cost scaling (power × tariff ×
duration × 1000 must stay ≲ 1e8 per process); such inputs fail honestly
as `INFEASIBLE` rather than silently returning wrong numbers.

Determinism guarantee does NOT extend across: different OR-Tools
versions, different CPU platforms/compilers, or a future switch back to
multi-worker search.

### Integer scaling bounds (documented limit)

The model encodes cost/solar contributions as integers (cost resolution
0.001, solar resolution 0.1 kWh) with finite per-process variable
domains. The input layer therefore enforces upfront:

- `power_kw x tariff_per_kwh x duration_hours <= 100000` per process
- `min(solar_kw, power_kw) x duration_hours <= 100000` per process

Exceeding either bound returns `INVALID INPUT` naming the offending
process — never a mysterious solver INFEASIBLE.

### Warnings channel

The envelope always carries `warnings` (list of strings, empty when
nothing notable; additive since API 1.0 — consumers can ignore it).
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

```python
from optimizer.public_api import optimize
result = optimize("examples/widget_lab.json")
print(result["status"])                                # OPTIMAL
print(result["result"]["comparison"]["cost_savings"])  # 9.0
```

Response (values from the DEMO Widget Lab, DEMO/SIMULATED data):

```json
{
  "api_version": "1.0",
  "status": "OPTIMAL",
  "result": {
    "factory_name": "Demo Widget Lab",
    "comparison": { "cost_savings": 9.0, "cost_saving_percent": 64.29,
                    "shifted_processes": 2 },
    "carbon": { "co2_reduction_kg": 6.3, "co2_reduction_percent": 64.29 }
  },
  "errors": null,
  "warnings": []
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
from optimizer.public_api import optimize

def handle_optimize_request(body: dict) -> dict:
    result = optimize(body)               # never raises in default mode
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

## 8. How to add a new factory

Data only — no engine changes:

- **One-off:** create a JSON file in the input schema (copy
  `examples/widget_lab.json`) and call `optimize(path)`.
- **Registered demo:** add a config dict to `AVAILABLE_FACTORIES` in
  `optimizer/factory_data.py`; the CLI (`python -m optimizer`) and the
  generic test battery pick it up automatically.

## 9. How to run tests

```bash
python -m unittest discover -s tests -v
```

Current suite: 91 tests — engine/validation core (28), application/input
layer (36), public API integration (27). All use DEMO/SIMULATED data.

## 10. Error handling summary

| Situation | Returned status | Where details appear |
|---|---|---|
| Malformed JSON / unreadable file | `INVALID INPUT` | `errors` |
| Schema problems (missing/duplicate IDs, negative values, invalid machine refs, cycles, impossible windows, bad energy profiles) | `INVALID INPUT` | `errors` (all problems, human-readable) |
| Valid input, impossible constraints | `INFEASIBLE` | `result.validation_errors` stays `null`; schedules `null` |
| Solver returns feasible-but-unproven | `FEASIBLE` | normal result |
| Unexpected internal failure | `ERROR` | generic message in `errors`; traceback goes to the server log only |

Raw stack traces are never part of the normal user-facing result.
