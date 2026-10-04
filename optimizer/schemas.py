"""
ByteMe JSON Schema Contracts (DEMO/SIMULATED DATA ONLY)

Machine-readable formal contracts for the public optimizer API:
INPUT_SCHEMA describes the canonical input, OUTPUT_SCHEMA the canonical
result envelope. Draft-07 compatible; used by tests to guarantee the
public contract stays stable.

These schemas describe DEMO/SIMULATED data only.
"""

INPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ByteMe Optimizer Input",
    "description": "Canonical normalized factory and energy input. The optimizer enforces process duration/power, dependencies, deadlines and time windows, assigned-machine capacity, capacity_units, profiles, and objective. Quantity and machine power/availability/compatibility are informational only.",
    "type": "object",
    "required": ["factory"],
    "properties": {
        "factory": {
            "type": "object",
            "required": [
                "factory_name", "planning_horizon_hours",
                "production_deadline", "processes",
            ],
            "properties": {
                "factory_name": {"type": "string", "minLength": 1},
                "factory_id": {"type": "string", "minLength": 1},
                "factory_type": {"type": "string", "minLength": 1},
                "planning_horizon_hours": {
                    "type": "number", "exclusiveMinimum": 0,
                    "description": "Scheduling limit; effective tick bound "
                                   "rounds down to the half-hour grid",
                },
                "production_deadline": {
                    "type": "number", "exclusiveMinimum": 0,
                    "description": "Completion limit; effective tick bound "
                                   "rounds down to the half-hour grid",
                },
                "processes": {
                    "type": "array", "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["process_id", "duration_hours", "power_kw"],
                        "properties": {
                            "process_id": {"type": "string", "minLength": 1},
                            "process_name": {"type": "string", "minLength": 1},
                            "duration_hours": {
                                "type": "number", "exclusiveMinimum": 0,
                                "multipleOf": 0.5,
                                "description": "Process duration in hours; positive multiples of 0.5 on the half-hour scheduling grid.",
                            },
                            "power_kw": {"type": "number", "minimum": 0,
                                "multipleOf": 0.2,
                                "description": "Constant power draw. Integer "
                                "energy encoding requires 0.2 kW increments "
                                "(0.1 kWh per half-hour). Integer scaling bound: "
                                "power_kw x tariff_per_kwh x "
                                "duration_hours must stay <= 100000 (per "
                                "process), else INVALID INPUT."},
                            "quantity": {
                                "type": ["number", "null"],
                                "description": "Informational only; does not "
                                               "change duration, power, or demand",
                            },
                            "quantity_unit": {
                                "type": ["string", "null"],
                                "description": "Informational unit for quantity",
                            },
                            "work_order_id": {
                                "type": ["string", "null"],
                                "description": "Informational source work-order ID",
                            },
                            "dependencies": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "is_flexible": {
                                "type": "boolean",
                                "description": "True makes this process eligible to move from its baseline start; false pins it to that baseline start. True does not guarantee a move and false does not name a caller-selected commitment time.",
                            },
                            "machine_id": {
                                "type": ["string", "null"],
                                "description": "Enforced resource assignment; "
                                               "same-machine processes share its capacity",
                            },
                            "capacity_units": {
                                "type": "integer", "minimum": 1,
                                "description": "Machine capacity consumed by "
                                               "this process; defaults to 1",
                            },
                            "earliest_start": {
                                "type": ["number", "null"],
                                "description": "Rounds up to the next "
                                               "half-hour start tick",
                            },
                            "latest_finish": {
                                "type": ["number", "null"],
                                "description": "Rounds down to the previous "
                                               "half-hour finish tick",
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "machines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["machine_id"],
                        "properties": {
                            "machine_id": {"type": "string", "minLength": 1},
                            "machine_name": {"type": "string", "minLength": 1},
                            "availability": {
                                "type": "string",
                                "description": "Informational only; not enforced "
                                               "as a calendar or shift",
                            },
                            "capacity": {
                                "type": ["number", "null"],
                                "minimum": 1,
                                "multipleOf": 1,
                                "description": "Maximum simultaneous capacity "
                                               "units; null defaults to 1",
                            },
                            "power_kw": {
                                "type": ["number", "null"],
                                "description": "Informational only; process "
                                               "power_kw drives energy calculations",
                            },
                            "compatible_processes": {
                                "description": "Informational only; assignment "
                                               "mismatches warn but are not prohibited",
                                "type": "array", "items": {"type": "string"},
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
        "energy": {
            "type": "object",
                "description": "Profiles use elapsed planning hours; key 0 covers [0, 1) from the planning origin, not a wall-clock hour. An omitted profile uses DEMO data with a warning; omitted hours in a supplied partial profile are zero with a warning. Profiles repeat every 24 hours. Currency is caller-defined, is not converted, and must be consistent within a request.",
            "properties": {
                "solar_profile": {
                    "type": "object",
                    "minProperties": 1,
                    "propertyNames": {
                        "type": "string",
                        "pattern": r"^\s*0*(?:[0-9]|1[0-9]|2[0-3])\s*$",
                    },
                    "description": "hour (0-23, int or numeric string) -> "
                                   "available site-wide kW in 0.2 increments; "
                                   "missing hours are 0; 24-hour profile repeats",
                    "additionalProperties": {
                        "type": "number", "minimum": 0, "multipleOf": 0.2,
                    },
                },
                "tariff_profile": {
                    "type": "object",
                    "minProperties": 1,
                    "propertyNames": {
                        "type": "string",
                        "pattern": r"^\s*0*(?:[0-9]|1[0-9]|2[0-3])\s*$",
                    },
                    "description": "hour (0-23, int or numeric string) -> "
                                   "currency/kWh in 0.001 increments; "
                                   "missing hours are 0",
                    "additionalProperties": {
                        "type": "number", "minimum": 0, "multipleOf": 0.001,
                    },
                },
                "grid_emission_factor": {
                    "type": ["number", "null"],
                    "description": "kg CO2 per kWh; omit for carbon = unavailable",
                    "minimum": 0,
                },
            },
            "additionalProperties": False,
        },
        "options": {
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "enum": ["cost", "solar"],
                    "description": "cost = minimize grid electricity cost "
                                   "(makespan tiebreak); solar = maximize "
                                   "solar utilization (makespan tiebreak)",
                },
                "max_time_seconds": {
                    "type": ["number", "null"],
                    "exclusiveMinimum": 0,
                    "description": "Optional CP-SAT time limit in seconds per solver stage. One request can run baseline and optimized stages, so total solver time may approach twice this value, plus parsing/model/API overhead. Default: 60 seconds per stage.",
                },
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}

OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ByteMe Optimizer Result",
    "description": "Canonical result envelope (DEMO/SIMULATED data).",
    "type": "object",
    "required": ["api_version", "status", "result", "errors", "warnings",
                 "error_category"],
    "properties": {
        "api_version": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN",
                     "INVALID INPUT", "ERROR"],
            "description": "UNKNOWN = solver hit its time limit without "
                           "finding a solution and without proving "
                           "infeasibility (never report as INFEASIBLE)",
        },
        "warnings": {
            "type": "array",
            "description": "Non-fatal notices (defaults used, cyclic profile "
                           "repeats, normalization). Empty when nothing notable.",
            "items": {"type": "string"},
        },
        "error_category": {
            "type": ["string", "null"],
            "enum": ["VALIDATION_ERROR", "INFEASIBLE", "INTERNAL_ERROR", None],
            "description": "Machine-readable failure category; null for non-failure statuses.",
        },
        "result": {
            "type": ["object", "null"],
            "required": ["status"],
            "properties": {
                "status": {"type": "string"},
                "factory_name": {"type": ["string", "null"]},
                "objective": {"type": ["string", "null"]},
                "optimization_scope": {
                    "type": "string",
                    "enum": [
                        "FULL_DECLARED_CONSTRAINTS",
                        "CONDITIONAL_ON_BASELINE_PINNED_STARTS",
                    ],
                    "description": "OPTIMAL proves the optimized-stage objective only within this scope. Non-flexible processes are fixed at their baseline start times when scope is CONDITIONAL_ON_BASELINE_PINNED_STARTS.",
                },
                "solve_time_seconds": {"type": ["number", "null"]},
                "baseline": {"$ref": "#/definitions/schedule"},
                "optimized": {"$ref": "#/definitions/schedule"},
                "comparison": {
                    "type": ["object", "null"],
                    "properties": {
                        "makespan_baseline_hours": {"type": "number"},
                        "makespan_optimized_hours": {"type": "number"},
                        "cost_baseline": {"type": "number"},
                        "cost_optimized": {"type": "number"},
                        "cost_savings": {"type": "number"},
                        "cost_saving_percent": {"type": "number"},
                        "solar_utilization_percent": {
                            "type": "number",
                            "description": "Optimized schedule solar kWh divided by optimized total kWh, multiplied by 100; 0 when total energy is zero.",
                        },
                        "shifted_processes": {"type": "integer"},
                    },
                },
                "machine_utilization": {
                    "type": ["object", "null"],
                    "properties": {
                        "baseline": {"$ref": "#/definitions/machine_utilization"},
                        "optimized": {
                            "anyOf": [
                                {"$ref": "#/definitions/machine_utilization"},
                                {"type": "null"},
                            ],
                        },
                    },
                },
                "carbon": {
                    "type": ["object", "null"],
                    "description": "null when no emission factor supplied",
                    "properties": {
                        "grid_emission_factor_kg_per_kwh": {"type": "number"},
                        "baseline_co2_kg": {"type": "number"},
                        "optimized_co2_kg": {"type": "number"},
                        "co2_reduction_kg": {"type": "number"},
                        "co2_reduction_percent": {"type": "number"},
                    },
                },
                "validation_errors": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
                "warnings": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
            },
        },
        "errors": {
            "type": ["array", "null"],
            "items": {"type": "string"},
        },
    },
    "definitions": {
        "schedule": {
            "type": ["object", "null"],
            "required": ["status", "makespan_hours", "processes", "energy",
                         "solver_diagnostics"],
            "properties": {
                "status": {"type": "string"},
                "makespan_hours": {"type": "number"},
                "solve_time_seconds": {"type": ["number", "null"]},
                "solver_diagnostics": {
                    "type": "object",
                    "required": ["status", "best_objective", "best_bound",
                                 "optimality_gap"],
                    "properties": {
                        "status": {"type": "string",
                                   "enum": ["OPTIMAL", "FEASIBLE"]},
                        "best_objective": {"type": "number"},
                        "best_bound": {"type": "number"},
                        "optimality_gap": {"type": "number", "minimum": 0},
                    },
                    "description": "CP-SAT objective and bound in the model's encoded objective units; these are not currency or kWh.",
                },
                "processes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "process_id", "start_time", "end_time",
                            "duration_hours", "is_flexible",
                            "capacity_units", "machine_capacity",
                            "solar_kwh", "grid_kwh", "energy_cost",
                        ],
                        "properties": {
                            "process_id": {"type": "string"},
                            "process_name": {"type": "string"},
                            "start_time": {"type": "number"},
                            "end_time": {"type": "number"},
                            "duration_hours": {"type": "number"},
                            "power_kw": {"type": "number"},
                            "is_flexible": {"type": "boolean"},
                            "machine_id": {"type": ["string", "null"]},
                            "capacity_units": {"type": "integer", "minimum": 1},
                            "machine_capacity": {"type": ["integer", "null"]},
                            "quantity": {"type": ["number", "null"]},
                            "solar_kwh": {"type": "number"},
                            "grid_kwh": {"type": "number"},
                            "energy_cost": {"type": "number"},
                            "tariff": {
                                "type": ["number", "null"],
                                "description": "Hourly tariff at floor(start_time) in currency/kWh; process energy_cost integrates grid energy against each overlapped hour's tariff.",
                            },
                        },
                    },
                },
                "energy": {
                    "type": "object",
                    "required": ["total_kwh", "solar_kwh", "grid_kwh"],
                    "properties": {
                        "total_kwh": {"type": "number"},
                        "solar_kwh": {"type": "number"},
                        "grid_kwh": {"type": "number"},
                    },
                },
            },
        },
        "machine_utilization": {
            "type": ["object", "null"],
            "additionalProperties": {
                "type": "object",
                "required": [
                    "busy_hours", "capacity_unit_hours", "capacity",
                    "peak_capacity_units", "utilization_percent",
                ],
                "properties": {
                    "busy_hours": {"type": "number"},
                    "capacity_unit_hours": {"type": "number"},
                    "capacity": {"type": "integer", "minimum": 1},
                    "peak_capacity_units": {"type": "integer", "minimum": 0},
                    "utilization_percent": {
                        "type": "number", "minimum": 0, "maximum": 100,
                    },
                },
            },
        },
    },
}
