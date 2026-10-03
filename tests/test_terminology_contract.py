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
    "factory_id", "factory_name", "factory_type", "planning_horizon_hours",
    "production_deadline", "processes", "machines",
    # input: process
    "process_id", "work_order_id", "process_name", "duration_hours",
    "power_kw", "quantity", "quantity_unit",
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
    "error_category",
    # result payload
    "baseline", "optimized", "comparison", "machine_utilization", "carbon",
    "validation_errors", "solve_time_seconds",
    # comparison
    "makespan_baseline_hours", "makespan_optimized_hours", "cost_baseline",
    "cost_optimized", "cost_savings", "cost_saving_percent",
    "solar_utilization_percent", "shifted_processes",
    # schedule
    "makespan_hours", "processes", "energy",
    "solver_diagnostics", "best_objective", "best_bound", "optimality_gap",
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
    "schedule_id", "optimization_status", "deadline",
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

    def test_api_response_example_is_valid_json(self):
        with open("optimizer/API.md", encoding="utf-8") as handle:
            doc = handle.read()
        canonical_section = doc.split(
            "## 5. Output format (canonical schema)", 1
        )[1]
        canonical_example = canonical_section.split("```json", 1)[1].split(
            "```", 1
        )[0]
        canonical_response = json.loads(canonical_example)
        canonical_result = canonical_response["result"]
        comparison = canonical_result["comparison"]
        self.assertAlmostEqual(
            comparison["cost_savings"],
            comparison["cost_baseline"] - comparison["cost_optimized"],
        )
        self.assertAlmostEqual(
            comparison["solar_utilization_percent"],
            canonical_result["optimized"]["energy"]["solar_kwh"]
            / canonical_result["optimized"]["energy"]["total_kwh"] * 100,
        )
        carbon = canonical_result["carbon"]
        self.assertAlmostEqual(
            carbon["co2_reduction_kg"],
            carbon["baseline_co2_kg"] - carbon["optimized_co2_kg"],
        )
        input_section = doc.split("## 4. Input format (canonical schema)", 1)[1]
        input_example = input_section.split("```json", 1)[1].split(
            "```", 1
        )[0]
        validate_user_input(json.loads(input_example))
        section = doc.split(
            "Response (values from the DEMO Widget Lab, DEMO/SIMULATED data):",
            1,
        )[1]
        example = section.split("```json", 1)[1].split("```", 1)[0]
        response = json.loads(example)
        self.assertIn("warnings", response)
        self.assertEqual(response["result"]["comparison"]["cost_savings"], 8.1)

    def test_readme_visualization_example_uses_configured_solar_profile(self):
        with open("optimizer/README.md", encoding="utf-8") as handle:
            doc = handle.read()
        self.assertIn(
            'config = load_user_input("examples/widget_lab.json")', doc
        )
        self.assertIn(
            'render_energy_profile(config.energy["solar_profile"]', doc
        )

    def test_readme_describes_machine_capacity_parallelism(self):
        with open("optimizer/README.md", encoding="utf-8") as handle:
            doc = handle.read()
        self.assertIn(
            "may overlap only within its declared capacity", doc
        )
        self.assertNotIn("processes sharing a machine never overlap", doc)
        self.assertNotIn("machine NoOverlap", doc)

        with open("optimizer/API.md", encoding="utf-8") as handle:
            api_doc = handle.read()
        self.assertNotIn("machine no-overlap", api_doc)
        self.assertIn("machine-capacity constraints", api_doc)

        with open("optimizer/factory_data.py", encoding="utf-8") as handle:
            factory_doc = " ".join(handle.read().split())
        self.assertIn(
            "combined `capacity_units` do not exceed this value",
            factory_doc,
        )
        self.assertIn(
            "optional positive machine-capacity demand (default 1)",
            factory_doc,
        )
        self.assertNotIn("processes sharing a machine never overlap", factory_doc)

    def test_deployment_docs_explain_hard_cancellation_boundary(self):
        for path in ("optimizer/API.md", "optimizer/README.md"):
            with self.subTest(path=path):
                with open(path, encoding="utf-8") as handle:
                    doc = " ".join(handle.read().lower().split())
                self.assertIn("does not stop", doc)
                self.assertIn("isolated worker process", doc)

    def test_docs_identify_repository_only_example_file(self):
        for path in ("optimizer/API.md", "optimizer/README.md"):
            with self.subTest(path=path):
                with open(path, encoding="utf-8") as handle:
                    doc = " ".join(handle.read().lower().split())
                self.assertIn("not in the installed wheel", doc)

    def test_all_fenced_json_documentation_examples_parse(self):
        for path in ("optimizer/API.md", "optimizer/README.md"):
            with self.subTest(path=path):
                with open(path, encoding="utf-8") as handle:
                    doc = handle.read()
                for match in re.finditer(
                        r"```json\s*\n(.*?)\n```", doc, re.DOTALL):
                    json.loads(match.group(1))

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
                     "shared, site-wide pool", "half-hour scheduling slot",
                     "[h, h + 1)"):
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
