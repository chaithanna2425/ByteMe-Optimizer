"""
ByteMe Terminology Contract Tests (DEMO/SIMULATED DATA ONLY)

Lock in the ONE shared vocabulary agreed by the optimizer, backend and
frontend teams. The existing PUBLIC API terminology is the source of truth:

- every field name on the public surface must belong to the agreed shared
  vocabulary (or an explicitly allowed compound / factory data value)
- forbidden terms (order-management, ambiguous, misleading names) must never
  appear as public field names
- legacy internal names must never leak through the public projection
- docs and schemas must teach the same names as the code
"""

import copy
import inspect
import json
import re
import unittest

from optimizer import app, public_api
from optimizer.factory_data import AVAILABLE_FACTORIES
from optimizer.input_layer import validate_user_input
from optimizer.public_api import optimize

# ---------------------------------------------------------------------------
# THE shared vocabulary (single source of truth for these tests).
# Mirrors optimizer/schemas.py + optimizer/API.md. Change only by team agreement.
# ---------------------------------------------------------------------------

SHARED_VOCABULARY = {
    # input: factory
    "factory_name", "factory_type", "planning_horizon_hours",
    "production_deadline", "processes", "machines",
    # input: process
    "process_id", "process_name", "duration_hours", "power_kw", "quantity",
    "dependencies", "is_flexible", "machine_id", "capacity_units", "earliest_start",
    "latest_finish",
    # input: machine
    "machine_name", "capacity", "machine_capacity", "availability", "power_kw",
    "compatible_processes",
    # input: energy + options
    "solar_profile", "tariff_profile", "grid_emission_factor", "objective",
    "max_time_seconds", "factory", "energy", "options",
    # envelope
    "api_version", "status", "result", "errors", "warnings",
    # result payload
    "baseline", "optimized", "comparison", "machine_utilization", "carbon",
    "validation_errors", "solve_time_seconds",
    # comparison
    "makespan_baseline_hours", "makespan_optimized_hours", "cost_baseline",
    "cost_optimized", "cost_savings", "cost_saving_percent",
    "solar_utilization_percent", "shifted_processes",
    # schedule
    "makespan_hours", "processes", "energy",
    # energy block
    "total_kwh", "solar_kwh", "grid_kwh",
    # process row
    "start_time", "end_time", "solar_kwh", "grid_kwh", "energy_cost",
    "tariff",
    # machine utilization entries
    "busy_hours", "capacity_unit_hours", "peak_capacity_units",
    "anyOf", "maximum",
    "utilization_percent",
    # carbon block
    "grid_emission_factor_kg_per_kwh", "baseline_co2_kg", "optimized_co2_kg",
    "co2_reduction_kg", "co2_reduction_percent",
}

# Terms that must NEVER appear as public field names
FORBIDDEN_TERMS = {
    "production_order", "production_order_id", "production_target",
    "machine_available", "energy_savings", "solar_available_kw",
    "schedule_id", "optimization_status", "factory_id", "deadline",
    # legacy internal spellings
    "solar_energy_kwh", "total_solar_kwh", "total_grid_kwh",
    "total_energy_kwh",
}

# Terms that must remain INTERNAL (engine/module-level names, not public keys)
INTERNAL_ONLY_TERMS = {
    "TIME_SCALE", "COST_SCALE", "SOLAR_WEIGHT", "MAKESPAN_TIEBREAK_SCALE",
    "slot_bools", "solar_contrib", "total_solar_score",
    "FactoryConfig", "ProcessSpec", "MachineSpec", "_run_pipeline",
    "_pin_non_flexible_processes", "load_user_input",
}

RICH_ENERGY = {
    "solar_profile": {h: (20 if 9 <= h <= 15 else 0) for h in range(24)},
    "tariff_profile": {h: (0.10 if 9 <= h <= 15 else 0.50) for h in range(24)},
    "grid_emission_factor": 0.4,
}


def _rich_source(key):
    source = {
        "factory": copy.deepcopy(AVAILABLE_FACTORIES[key]),
        "energy": copy.deepcopy(RICH_ENERGY),
    }
    return source


def _walk_keys(node):
    """Collect every dict key in a JSON-like structure."""
    keys = set()
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            keys |= _walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            keys |= _walk_keys(item)
    return keys


def _public_surface_keys():
    """Union of all field names over real outputs, both objectives, all factories."""
    keys = set()
    for key in AVAILABLE_FACTORIES:
        source = _rich_source(key)
        for objective in ("cost", "solar"):
            result = optimize(source, objective=objective)
            keys |= _walk_keys(result)
            # machine ids legitimately appear as utilization-map keys (DATA)
            util = result["result"]["machine_utilization"]["optimized"]
            for machine_id in util:
                keys.discard(machine_id)
    return keys


class SharedVocabularyTests(unittest.TestCase):
    """The public surface uses exactly the agreed shared vocabulary."""

    def test_public_output_uses_only_agreed_terms(self):
        keys = _public_surface_keys()
        unknown = keys - SHARED_VOCABULARY
        self.assertEqual(
            unknown, set(),
            msg=f"public output contains non-vocabulary field names: "
                f"{sorted(unknown)}",
        )

    def test_core_terms_present_in_output(self):
        keys = _public_surface_keys()
        for term in ("api_version", "status", "baseline", "optimized",
                     "makespan_hours", "total_kwh", "solar_kwh", "grid_kwh",
                     "energy_cost", "cost_baseline", "cost_optimized",
                     "cost_savings", "solar_utilization_percent",
                     "shifted_processes", "machine_utilization", "carbon",
                     "warnings", "errors", "start_time", "end_time",
                     "duration_hours", "power_kw", "is_flexible",
                     "process_id", "machine_id"):
            self.assertIn(term, keys, msg=f"missing public term: {term}")

    def test_error_surface_uses_vocabulary(self):
        bad = {"factory": {"factory_name": "Broken"}}
        keys = _walk_keys(optimize(bad))
        self.assertEqual(keys - SHARED_VOCABULARY, set())
        for term in ("status", "errors", "warnings", "api_version"):
            self.assertIn(term, keys)


class ForbiddenTermsTests(unittest.TestCase):
    """Terms we deliberately rejected must stay off the public surface."""

    def test_no_forbidden_terms_in_public_output(self):
        keys = _public_surface_keys()
        self.assertEqual(
            keys & FORBIDDEN_TERMS, set(),
            msg=f"forbidden terms leaked into public output: "
                f"{sorted(keys & FORBIDDEN_TERMS)}",
        )

    def test_no_forbidden_terms_in_schemas(self):
        text = json.dumps(public_api.get_input_schema()).lower() + \
            json.dumps(public_api.get_output_schema()).lower()
        for term in FORBIDDEN_TERMS:
            self.assertNotIn(f'"{term}"', text,
                             msg=f"forbidden term {term!r} in JSON schemas")

    def test_no_forbidden_terms_in_api_docs(self):
        with open("optimizer/API.md", encoding="utf-8") as handle:
            doc = handle.read()
        for term in ("production_order", "production_target",
                     "machine_available", "energy_savings",
                     "solar_available_kw", "schedule_id",
                     "optimization_status"):
            self.assertNotIn(term, doc.lower().replace("_", "_"),
                             msg=f"forbidden term {term!r} in API.md")

    def test_no_legacy_names_in_public_projection(self):
        # The public projection must translate internal spellings at the
        # boundary; legacy keys must never appear as output field names.
        source = _rich_source("chocolate")
        result = optimize(source)
        text = json.dumps(result)
        for legacy in ("solar_energy_kwh", "total_solar_kwh",
                       "total_grid_kwh", "total_energy_kwh",
                       "baseline_schedule", "optimized_schedule"):
            self.assertNotIn(f'"{legacy}"', text,
                             msg=f"legacy name {legacy!r} leaked to output")

    def test_public_api_module_translates_internally_only(self):
        # Internal names may exist inside public_api.py only as engine reads
        # that get renamed by _project_*; never as output keys.
        import inspect
        source = inspect.getsource(public_api)
        self.assertIn('p["solar_energy_kwh"]', source)   # renamed to solar_kwh
        self.assertIn('"solar_kwh"', source)


class InternalTermsStayInternalTests(unittest.TestCase):
    """Implementation names must not become public vocabulary."""

    def test_internal_terms_not_in_public_output(self):
        keys = {k.lower() for k in _public_surface_keys()}
        for term in INTERNAL_ONLY_TERMS:
            self.assertNotIn(term.lower(), keys,
                             msg=f"internal term {term!r} exposed publicly")

    def test_internal_terms_not_in_schemas(self):
        text = json.dumps(public_api.get_input_schema()) + \
            json.dumps(public_api.get_output_schema())
        for term in INTERNAL_ONLY_TERMS:
            self.assertNotIn(term, text,
                             msg=f"internal term {term!r} in JSON schemas")

    def test_engine_function_names_absent_from_docs_contracts(self):
        with open("optimizer/API.md", encoding="utf-8") as handle:
            doc = handle.read()
        for name in ("create_baseline_schedule", "create_cost_optimized_schedule",
                     "_run_pipeline", "_pin_non_flexible_processes",
                     "load_user_input"):
            self.assertNotIn(name, doc.replace("`", ""),
                             msg=f"engine name {name!r} taught as public API")


class DocsSchemaConsistencyTests(unittest.TestCase):
    """schemas.py, API.md and code must teach identical field names."""

    def test_docs_reference_input_terms(self):
        with open("optimizer/API.md", encoding="utf-8") as handle:
            doc = handle.read()
        for term in ("factory_name", "planning_horizon_hours",
                     "production_deadline", "process_id", "duration_hours",
                     "power_kw", "dependencies", "is_flexible", "machine_id",
                     "earliest_start", "latest_finish", "solar_profile",
                     "tariff_profile", "grid_emission_factor", "objective",
                     "api_version"):
            self.assertIn(term, doc, msg=f"API.md missing term: {term}")

    def test_docs_reference_output_terms(self):
        with open("optimizer/API.md", encoding="utf-8") as handle:
            doc = handle.read()
        for term in ("baseline", "optimized", "makespan_hours", "total_kwh",
                     "solar_kwh", "grid_kwh", "energy_cost", "cost_baseline",
                     "cost_optimized", "cost_savings", "cost_saving_percent",
                     "solar_utilization_percent", "shifted_processes",
                     "machine_utilization", "carbon", "warnings", "errors",
                     "start_time", "end_time", "tariff"):
            self.assertIn(term, doc, msg=f"API.md missing term: {term}")

    def test_energy_time_contract_is_explicit(self):
        with open("optimizer/API.md", encoding="utf-8") as handle:
            doc = " ".join(handle.read().lower().split())
        for term in ("Time 0", "timezone", "daylight-saving",
                     "currency", "half-hour", "final hourly bucket",
                     "shared, site-wide hourly pool", "[h, h + 1)"):
            self.assertIn(term.lower(), doc,
                          msg=f"API.md missing energy/time contract term: {term}")

    def test_input_schema_property_names_match_vocabulary(self):
        schema = public_api.get_input_schema()
        keys = _walk_keys(schema)
        unknown = {
            k for k in keys
            if isinstance(k, str) and not k.startswith("$")
            and k not in SHARED_VOCABULARY
            and k not in {"type", "description", "items", "properties",
                          "required", "enum", "minimum", "exclusiveMinimum",
                          "additionalProperties", "minLength", "minItems",
                          "minProperties",
                          "multipleOf",
                          "integer",
                          "propertyNames", "pattern",
                          "title", "definitions", "$ref", "$schema", "number",
                          "object", "array", "string", "boolean", "null"}
        }
        self.assertEqual(unknown, set(),
                         msg=f"input schema uses non-vocabulary keys: "
                             f"{sorted(unknown)}")

    def test_output_schema_property_names_match_vocabulary(self):
        schema = public_api.get_output_schema()
        keys = {
            k for k in _walk_keys(schema)
            if isinstance(k, str) and not k.startswith("$")
            and k not in SHARED_VOCABULARY
            and k not in {"type", "description", "items", "properties",
                          "required", "enum", "minimum", "exclusiveMinimum",
                          "additionalProperties", "minLength", "minItems",
                          "title", "definitions", "$ref", "$schema", "number",
                          "object", "array", "string", "boolean", "null",
                          # JSON-Schema $ref definition NAME inside
                          # "definitions" - structural, not a public field
                          "schedule"}
        }
        self.assertEqual(keys, set(),
                         msg=f"output schema uses non-vocabulary keys: "
                             f"{sorted(keys)}")

    def test_agreed_but_absent_terms_are_documented_not_implemented(self):
        # quantity IS modeled (informational); the order-management terms are
        # deliberately absent from the optimizer and must stay absent.
        import inspect
        from optimizer import optimizer as engine
        engine_source = inspect.getsource(engine).lower()
        for term in ("production_order", "production_target",
                     "machine_available", "energy_savings",
                     "solar_available_kw", "schedule_id"):
            self.assertNotIn(term, engine_source,
                             msg=f"engine mentions rejected term {term!r}")


class FactoryDataVsApiTermsTests(unittest.TestCase):
    """Industry words are DATA VALUES, never API field names."""

    def test_factory_specific_words_are_values_only(self):
        industry_terms = ("roasting", "conching", "granulation",
                          "pasteurization", "milling", "tempering")
        source = _rich_source("chocolate")
        result = optimize(source)
        text = json.dumps(result)
        # They may appear as data values...
        self.assertTrue(any(t in text for t in industry_terms),
                        msg="expected industry data values in output")
        # ...but never as JSON keys
        for key in _walk_keys(result):
            self.assertNotIn(any(industry_terms), (key,),
                             msg=f"industry term {key!r} became a field name")

    def test_all_factories_share_the_same_field_names(self):
        reference = None
        for key in AVAILABLE_FACTORIES:
            result = optimize(_rich_source(key))["result"]
            keys = _walk_keys(result)
            machine_ids = set(
                result["machine_utilization"]["optimized"].keys()
            )
            keys -= machine_ids
            if reference is None:
                reference = keys
            self.assertEqual(keys, reference,
                             msg=f"{key} uses a different field-name set")


if __name__ == "__main__":
    unittest.main()
