# ByteMe — Generic Manufacturing Energy Optimization

**DEMO/SIMULATED — NOT REAL INDUSTRIAL DATA**

One generic manufacturing energy optimization engine, applicable to any
factory that can be described by the ByteMe configuration schema — currently
demonstrated on Chocolate, Cosmetics, Food, Beverage, Pharmaceutical,
Automotive, and Furniture (non-industry test factory). A new factory is
added by providing configuration data only; the optimization algorithm is
never modified.

All results shown anywhere in this project use DEMO/SIMULATED factory and
energy data. Nothing here represents real factories, real electricity
prices, real weather, or real industrial savings.

## 1. Architecture

```
USER INPUT (JSON file / JSON string / dict)     optimizer/input_layer.py
        |  validation (all problems reported, human-readable)
        v
FACTORY CONFIGURATION + ENERGY DATA             optimizer/factory_data.py
        |  7 registered demo factories          optimizer/energy_data.py
        v
GENERIC PROCESS/MACHINE MODEL                   optimizer/models.py
        |  FactoryConfig / ProcessSpec / MachineSpec
        v
GENERIC PRODUCTION CONSTRAINTS
        |  durations, dependencies, deadlines, time windows,
        |  flexible/non-flexible pinning, machine NoOverlap
        v
OR-TOOLS CP-SAT OPTIMIZATION ENGINE             optimizer/optimizer.py
        |  V1 baseline | V2 solar-aware | V3/V4 cost-optimized
        |  (fully factory-agnostic: no industry names or branches)
        v
APPLICATION LAYER                               optimizer/app.py
        |  workflow, metrics, machine utilization, carbon, JSON
        v
VISUALIZATION                                   optimizer/visualization.py
        |  Gantt, energy profile, baseline-vs-optimized comparison
        v
VALIDATED SCHEDULE + ENERGY/COST/CARBON METRICS
```

## 2. How to define a factory

A factory is pure data following the generic schema:

```python
{
  "factory_name": "Demo Widget Lab",
  "factory_type": "widgets",            # optional informational label
  "planning_horizon_hours": 12,
  "production_deadline": 10,
  "processes": [
    {
      "process_id": "step_x",            # unique, referenced by dependencies
      "process_name": "Step X",
      "duration_hours": 1,               # multiple of 0.5 h (scheduling grid)
      "power_kw": 10,                    # constant power while running
      "quantity": 100,                   # informational only; not optimized
      "dependencies": [],                # process_ids that must finish first
      "is_flexible": false,              # False = pinned to baseline start
      "machine_id": "core",              # optional machine requirement
      "capacity_units": 1,               # optional machine demand; defaults to 1
      "earliest_start": null,            # optional time window (hours)
      "latest_finish": null              # optional time window (hours)
    }
  ],
  "machines": [
    {
      "machine_id": "core",
      "machine_name": "Shared Core Machine",
      "capacity": 100,
      "power_kw": 12,                    # informational only
      "availability": "single unit",     # informational only; not a calendar
      "compatible_processes": ["step_x", "step_y"] # informational only
    }
  ]
}
```

Rules: `duration_hours` on the half-hour grid; `dependencies` must reference
existing processes and be cycle-free; `machine_id` must exist in `machines`;
processes sharing a machine never overlap; every process must finish by
`production_deadline` (and by its own `latest_finish` if given).

## 3. How to provide user input

No Python code changes are needed. Provide a JSON file (see
`examples/widget_lab.json`):

```json
{
  "factory": { "factory_name": "...", "planning_horizon_hours": 12,
               "production_deadline": 10, "processes": [...], "machines": [...] },
  "energy": {
    "solar_profile":  { "0": 0, "1": 0, "...": 0 },
    "tariff_profile": { "0": 0.45, "1": 0.45, "...": 0.45 },
    "grid_emission_factor": 0.35
  }
}
```

- JSON hour keys may be strings (`"0"`) — they are normalized to integers.
- Omitting `energy` falls back to the DEMO solar/tariff profiles.
- `grid_emission_factor` is optional (kg CO2 per kWh); omit it and carbon
  results are reported as **UNAVAILABLE** rather than invented.

```python
from optimizer.input_layer import load_user_input
config = load_user_input("examples/widget_lab.json")   # file path
config = load_user_input(json_string)                  # JSON string
config = load_user_input(dict_object)                  # dict
```

Invalid input raises `InputValidationError` listing **every** problem found
(missing/duplicate IDs, invalid dependencies, cycles, negative duration or
power, invalid machine IDs, impossible time windows, invalid energy
profiles), formatted by `format_validation_error(exc)`.

## 4. How optimization works

The engine (Google OR-Tools CP-SAT, `TIME_SCALE = 2` → half-hour slots):

- **Variables** — integer start/end per process; `end = start + duration`.
- **Constraints** — planning horizon, production deadline, precedence
  (`end[dep] ≤ start[proc]`), per-process time windows, machine NoOverlap
  via interval variables, and non-flexible processes pinned to their
  baseline start times.
- **V1 baseline** — minimize makespan.
- **V2 solar-aware** — maximize the exact in-model solar energy of flexible
  processes (`min(solar_kW, power_kW) × 0.5 h` per slot, 0.1 kWh units),
  makespan as tiebreaker.
- **V3/V4 cost-optimized** — minimize total grid electricity cost built
  exactly from the chosen start times (grid kWh × hourly tariff), makespan
  only as a tiebreaker.

The same objectives run for every factory; the engine contains no industry
or process names (enforced by tests).

## 5. Baseline vs optimized results

`optimizer/app.py` runs baseline and optimization and compares:

| Metric | Meaning |
|---|---|
| makespan | production completion time (hours) |
| total duration | sum of process durations |
| solar / grid kWh | energy split by source |
| total energy | solar + grid = power × duration |
| energy cost | grid kWh × DEMO hourly tariff (solar is free) |
| cost savings / % | baseline cost − optimized cost, and percentage |
| shifted processes | flexible processes moved vs baseline (> 0.1 h) |
| machine utilization | busy hours and % per machine |
| status | OPTIMAL / FEASIBLE / INFEASIBLE / INVALID INPUT |

## 6. Energy calculations (per process, actual scheduled times)

```
total energy per slot = power_kw × slot_duration
solar per slot        = min(solar_availability, power_kw) × slot_duration
grid per slot         = total − solar            (≥ 0, solar ≥ 0)
cost per slot         = grid × tariff[hour]      (solar never billed)
```

Guarantees: `solar + grid = total`, no negative values, and the in-model
objective uses exactly these definitions (verified by tests against an
independent recomputation).

All schedule times are relative to planning time 0 and use half-hour ticks.
Hourly solar/tariff profile keys refer to elapsed hours from that origin and
repeat every 24 hours for longer horizons. The final bucket is clipped to
the effective horizon, including a half-hour final interval. The optimizer
does not convert time zones or daylight-saving transitions; timestamped
profiles must be mapped to planning-relative hours by an integration layer.
Tariff values use one caller-selected currency unit per kWh; power is kW,
energy is kWh, and carbon factors are kg CO2/kWh.

## 7. Carbon calculation (optional)

```
CO2 = grid_kWh × grid_emission_factor
```

Reported: baseline CO2, optimized CO2, reduction, reduction %. Without a
supplied factor the results explicitly say carbon is **UNAVAILABLE** — no
value is ever invented.

## 8. How to add an eighth factory

1. Copy `examples/widget_lab.json`, edit names, processes, machines, hours.
2. Run it: `run_workflow("my_factory.json")` — done. No optimizer changes.

To register a demo factory in the CLI, add one dict entry to
`AVAILABLE_FACTORIES` in `optimizer/factory_data.py`. Nothing else changes.

## 9. Example usage

```python
from optimizer.app import run_workflow, display_results, serialize_results

result = run_workflow("examples/widget_lab.json")
print(result["status"])            # OPTIMAL / FEASIBLE / INFEASIBLE / INVALID INPUT
if result["status"] == "INVALID INPUT":
    from optimizer.input_layer import format_validation_error
    # human-readable report of every problem:
    print(result["validation_errors"])
else:
    display_results(result["results"])           # console comparison report
    json.dumps(serialize_results(result["results"]))  # machine-readable

# Text visualizations
from optimizer.visualization import (render_gantt_comparison,
                                     render_energy_profile,
                                     render_energy_comparison)
render_gantt_comparison(result["results"]["baseline_schedule"],
                        result["results"]["optimized_schedule"])
render_energy_profile(config_energy_solar_profile,
                      result["results"]["optimized_schedule"])
render_energy_comparison(result["results"]["baseline_schedule"],
                         result["results"]["optimized_schedule"])
```

CLI (all registered demo factories): `python -m optimizer`.

## 10. Known limitations

- Non-preemptive scheduling; single-capacity machines (capacity is informational).
- No setup/changeover times; constant power while a process runs.
- Solar availability is not a shared pool: concurrent processes may each use
  the full hourly availability (harmless in the demo data, conservative in
  general).
- Half-hour grid; reified slot encoding scales O(horizon × processes).
- DEMO/SIMULATED data only — do not interpret results as real industrial
  savings, prices, weather, or company performance.
