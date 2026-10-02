"""
ByteMe Factory Configurations (DEMO/SIMULATED DATA - NOT REAL INDUSTRIAL DATA)

This module is the FACTORY CONFIGURATION LAYER of the ByteMe optimizer.
Every factory is described purely by data following the generic ByteMe
schema below - the optimization engine (optimizer.py) never checks for
specific factory names or process names.

All values are DEMO/SIMULATED and not intended to represent real
industrial measurements, prices, or weather.

GENERIC FACTORY SCHEMA (all values DEMO/SIMULATED):
{
    "factory_name": str,
    "planning_horizon_hours": number,        # scheduling window
    "production_deadline": number,           # all processes must end before this
    "processes": [PROCESS, ...],
    "machines": [MACHINE, ...],              # optional; omit for no resource modeling
}

PROCESS (generic - all scheduling-relevant fields):
    process_id        unique id (referenced by dependencies)
    process_name      display name
    duration_hours    operating duration (energy = power_kw * duration_hours)
    power_kw          power consumption in kW
    dependencies      list of process_ids that must finish before this starts
    is_flexible       True if the process may be shifted by the optimizer,
                      False if it is pinned to its baseline (plan) start time
    machine_id        optional machine/resource requirement (must exist in
                      "machines"); processes sharing a machine never overlap
    quantity          optional production quantity / batch size (informational)
    earliest_start    optional earliest allowed start (hours)
    latest_finish     optional latest allowed end (hours)

MACHINE (generic resource):
    machine_id            unique id
    machine_name          display name
    capacity              REAL constraint: max processes running at once
                          (1 = never overlaps; >= 2 = allowed parallelism).
                          Batch sizes belong on processes as `quantity`.
    availability          informational availability description (DEMO)
    compatible_processes  process_ids this machine can run (DEMO metadata)
    power_kw              optional machine-level power information (DEMO)

Optimization results using DEMO/SIMULATED factory and energy data only.
"""

# ---------------------------------------------------------------------------
# 1. CHOCOLATE (process industry)
#    Raw Material Preparation -> Roasting -> Cracking/Winnowing -> Grinding
#    -> Mixing -> Refining -> Conching -> Tempering -> Moulding/Forming
#    -> Cooling -> Packaging -> Quality Inspection
#    Parallel: grinding splits across two mills (branch/merge at mixing).
#    Shared resource: cooling tunnel and packaging share the pack line.
# ---------------------------------------------------------------------------
DEMO_CHOCOLATE_FACTORY = {
    "factory_name": "Demo Chocolate Factory",
    "planning_horizon_hours": 24,   # DEMO: 24-hour planning window
    "production_deadline": 20,      # DEMO: all processes finish by hour 20
    "processes": [
        {
            "process_id": "raw_prep",
            "process_name": "Raw Material Preparation",
            "duration_hours": 1,        # DEMO
            "power_kw": 10,             # DEMO kW
            "is_flexible": False,
            "dependencies": [],
            "machine_id": "prep_station",
            "quantity": 500,            # DEMO kg
        },
        {
            "process_id": "roasting",
            "process_name": "Roasting",
            "duration_hours": 2,        # DEMO
            "power_kw": 50,             # DEMO kW
            "is_flexible": False,
            "dependencies": ["raw_prep"],
            "machine_id": "roaster",
            "quantity": 500,
        },
        {
            "process_id": "winnowing",
            "process_name": "Cracking/Winnowing",
            "duration_hours": 1,        # DEMO
            "power_kw": 20,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["roasting"],
            "machine_id": "winnower",
            "quantity": 450,
        },
        {
            # Branch A: grinding on mill 1
            "process_id": "grinding_a",
            "process_name": "Grinding/Milling Line A",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 30,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["winnowing"],
            "machine_id": "mill_a",
            "quantity": 225,
        },
        {
            # Branch B: grinding on mill 2 (parallel with A)
            "process_id": "grinding_b",
            "process_name": "Grinding/Milling Line B",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 30,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["winnowing"],
            "machine_id": "mill_b",
            "quantity": 225,
        },
        {
            # Merge point: both grinding branches finish first
            "process_id": "mixing",
            "process_name": "Mixing",
            "duration_hours": 1,        # DEMO
            "power_kw": 25,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["grinding_a", "grinding_b"],
            "machine_id": "mixer",
            "quantity": 450,
        },
        {
            "process_id": "refining",
            "process_name": "Refining",
            "duration_hours": 2,        # DEMO
            "power_kw": 40,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["mixing"],
            "machine_id": "refiner",
            "quantity": 450,
        },
        {
            "process_id": "conching",
            "process_name": "Conching",
            "duration_hours": 3,        # DEMO
            "power_kw": 25,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["refining"],
            "machine_id": "conche",
            "quantity": 450,
        },
        {
            "process_id": "tempering",
            "process_name": "Tempering",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 20,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["conching"],
            "machine_id": "temperer",
            "quantity": 450,
        },
        {
            "process_id": "moulding",
            "process_name": "Moulding/Forming",
            "duration_hours": 1,        # DEMO
            "power_kw": 15,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["tempering"],
            "machine_id": "moulding_line",
            "quantity": 400,            # DEMO bars
        },
        {
            # Shares the packaging line with the packaging process
            "process_id": "cooling",
            "process_name": "Cooling Tunnel",
            "duration_hours": 1,        # DEMO
            "power_kw": 12,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["moulding"],
            "machine_id": "pack_line",
            "quantity": 400,
        },
        {
            "process_id": "packaging",
            "process_name": "Packaging",
            "duration_hours": 1,        # DEMO
            "power_kw": 10,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["cooling"],
            "machine_id": "pack_line",  # shared with cooling
            "quantity": 400,
        },
        {
            "process_id": "quality_inspection",
            "process_name": "Quality Inspection",
            "duration_hours": 1,        # DEMO
            "power_kw": 5,              # DEMO kW
            "is_flexible": True,        # DEMO: flexible in this flow
            "dependencies": ["packaging"],
            "machine_id": "qc_bench",
            "quantity": 40,             # DEMO sampled batches
        },
    ],
    "machines": [
        {"machine_id": "prep_station", "machine_name": "Prep Station",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["raw_prep"], "power_kw": 10},
        {"machine_id": "roaster", "machine_name": "Cocoa Roaster",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["roasting"], "power_kw": 55},
        {"machine_id": "winnower", "machine_name": "Cracker/Winnower",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["winnowing"], "power_kw": 22},
        {"machine_id": "mill_a", "machine_name": "Ball Mill A",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["grinding_a"], "power_kw": 32},
        {"machine_id": "mill_b", "machine_name": "Ball Mill B",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["grinding_b"], "power_kw": 32},
        {"machine_id": "mixer", "machine_name": "Chocolate Mixer",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["mixing"], "power_kw": 26},
        {"machine_id": "refiner", "machine_name": "Five-Roll Refiner",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["refining"], "power_kw": 42},
        {"machine_id": "conche", "machine_name": "Conche",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["conching"], "power_kw": 28},
        {"machine_id": "temperer", "machine_name": "Tempering Machine",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["tempering"], "power_kw": 20},
        {"machine_id": "moulding_line", "machine_name": "Moulding Line",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["moulding"], "power_kw": 16},
        {"machine_id": "pack_line", "machine_name": "Cooling + Pack Line (shared)",
         "capacity": 1, "availability": "single unit, shared",
         "compatible_processes": ["cooling", "packaging"], "power_kw": 14},
        {"machine_id": "qc_bench", "machine_name": "QC Bench",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["quality_inspection"], "power_kw": 5},
    ],
}


# ---------------------------------------------------------------------------
# 2. COSMETICS (process industry, shared tanks/filling equipment)
#    Raw Material Weighing -> Preparation -> Mixing -> Heating/Emulsification
#    -> Homogenization -> Cooling -> Filling -> Labeling -> Packaging
#    -> Quality Inspection
#    Shared resources: two product lines share mixer tanks and the filler.
# ---------------------------------------------------------------------------
DEMO_COSMETICS_FACTORY = {
    "factory_name": "Demo Cosmetics Factory",
    "planning_horizon_hours": 24,   # DEMO
    "production_deadline": 16,      # DEMO
    "processes": [
        {
            "process_id": "weighing",
            "process_name": "Raw Material Weighing",
            "duration_hours": 1,        # DEMO
            "power_kw": 5,              # DEMO kW
            "is_flexible": False,
            "dependencies": [],
            "machine_id": "weigh_station",
            "quantity": 2000,           # DEMO units
        },
        {
            "process_id": "prep",
            "process_name": "Preparation",
            "duration_hours": 1,        # DEMO
            "power_kw": 8,              # DEMO kW
            "is_flexible": True,
            "dependencies": ["weighing"],
            "machine_id": "prep_bench",
            "quantity": 2000,
        },
        {
            # Two product lines share the mixing tanks (machine conflict)
            "process_id": "mixing_line1",
            "process_name": "Mixing - Product Line 1",
            "duration_hours": 2,        # DEMO
            "power_kw": 30,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["prep"],
            "machine_id": "mix_tank",
            "quantity": 1000,
        },
        {
            "process_id": "mixing_line2",
            "process_name": "Mixing - Product Line 2",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 25,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["prep"],
            "machine_id": "mix_tank",   # shared tank -> sequenced after line 1
            "quantity": 1000,
        },
        {
            "process_id": "emulsification",
            "process_name": "Heating/Emulsification",
            "duration_hours": 2,        # DEMO
            "power_kw": 45,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["mixing_line1", "mixing_line2"],
            "machine_id": "emulsifier",
            "quantity": 2000,
        },
        {
            "process_id": "homogenization",
            "process_name": "Homogenization",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 35,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["emulsification"],
            "machine_id": "homogenizer",
            "quantity": 2000,
        },
        {
            "process_id": "cooling",
            "process_name": "Cooling",
            "duration_hours": 2,        # DEMO
            "power_kw": 20,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["homogenization"],
            "machine_id": "cooling_jacket",
            "quantity": 2000,
        },
        {
            # Two lines share the filler (machine conflict)
            "process_id": "filling",
            "process_name": "Filling",
            "duration_hours": 2,        # DEMO
            "power_kw": 18,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["cooling"],
            "machine_id": "filler",
            "quantity": 2000,           # DEMO containers
        },
        {
            "process_id": "labeling",
            "process_name": "Labeling",
            "duration_hours": 1,        # DEMO
            "power_kw": 6,              # DEMO kW
            "is_flexible": True,
            "dependencies": ["filling"],
            "machine_id": "labeler",
            "quantity": 2000,
        },
        {
            "process_id": "packaging",
            "process_name": "Packaging",
            "duration_hours": 1,        # DEMO
            "power_kw": 8,              # DEMO kW
            "is_flexible": True,
            "dependencies": ["labeling"],
            "machine_id": "pack_station",
            "quantity": 2000,
        },
        {
            "process_id": "quality_inspection",
            "process_name": "Quality Inspection",
            "duration_hours": 1,        # DEMO
            "power_kw": 5,              # DEMO kW
            "is_flexible": False,       # quality gate is pinned
            "dependencies": ["packaging"],
            "machine_id": "qc_station",
            "quantity": 100,            # DEMO sampled batches
        },
    ],
    "machines": [
        {"machine_id": "weigh_station", "machine_name": "Weighing Station",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["weighing"], "power_kw": 5},
        {"machine_id": "prep_bench", "machine_name": "Preparation Bench",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["prep"], "power_kw": 8},
        {"machine_id": "mix_tank", "machine_name": "Mixing Tank (shared)",
         "capacity": 1, "availability": "single tank, shared by lines",
         "compatible_processes": ["mixing_line1", "mixing_line2"], "power_kw": 30},
        {"machine_id": "emulsifier", "machine_name": "Emulsifier",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["emulsification"], "power_kw": 48},
        {"machine_id": "homogenizer", "machine_name": "Homogenizer",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["homogenization"], "power_kw": 36},
        {"machine_id": "cooling_jacket", "machine_name": "Cooling Jacket",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["cooling"], "power_kw": 20},
        {"machine_id": "filler", "machine_name": "Filling Line (shared)",
         "capacity": 1, "availability": "single line, shared",
         "compatible_processes": ["filling"], "power_kw": 18},
        {"machine_id": "labeler", "machine_name": "Labeler",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["labeling"], "power_kw": 6},
        {"machine_id": "pack_station", "machine_name": "Packing Station",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["packaging"], "power_kw": 8},
        {"machine_id": "qc_station", "machine_name": "QC Station",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["quality_inspection"], "power_kw": 5},
    ],
}


# ---------------------------------------------------------------------------
# 3. FOOD (process industry, parallel preparation)
#    Raw Material Preparation -> Washing/Cleaning -> Cutting/Processing
#    -> Mixing -> Cooking/Heating -> Cooling -> Quality Inspection
#    -> Packaging -> Storage
#    Parallel: washing/cutting run per product line.
# ---------------------------------------------------------------------------
DEMO_FOOD_FACTORY = {
    "factory_name": "Demo Food Factory",
    "planning_horizon_hours": 20,   # DEMO
    "production_deadline": 18,      # DEMO
    "processes": [
        {
            "process_id": "raw_prep",
            "process_name": "Raw Material Preparation",
            "duration_hours": 1,        # DEMO
            "power_kw": 8,              # DEMO kW
            "is_flexible": False,
            "dependencies": [],
            "machine_id": "prep_area",
            "quantity": 3000,           # DEMO kg
        },
        {
            # Parallel wash lines (branch)
            "process_id": "washing_line1",
            "process_name": "Washing/Cleaning Line 1",
            "duration_hours": 1,        # DEMO
            "power_kw": 15,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["raw_prep"],
            "machine_id": "wash_line_1",
            "quantity": 1500,
        },
        {
            "process_id": "washing_line2",
            "process_name": "Washing/Cleaning Line 2",
            "duration_hours": 1,        # DEMO
            "power_kw": 15,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["raw_prep"],
            "machine_id": "wash_line_2",
            "quantity": 1500,
        },
        {
            "process_id": "cutting",
            "process_name": "Cutting/Processing",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 22,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["washing_line1", "washing_line2"],
            "machine_id": "cutter",
            "quantity": 2900,           # DEMO kg after losses
        },
        {
            "process_id": "mixing",
            "process_name": "Mixing",
            "duration_hours": 1,        # DEMO
            "power_kw": 18,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["cutting"],
            "machine_id": "food_mixer",
            "quantity": 2900,
        },
        {
            "process_id": "cooking",
            "process_name": "Cooking/Heating",
            "duration_hours": 2,        # DEMO
            "power_kw": 60,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["mixing"],
            "machine_id": "cooker",
            "quantity": 2900,
        },
        {
            "process_id": "cooling",
            "process_name": "Cooling",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 25,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["cooking"],
            "machine_id": "chiller",
            "quantity": 2900,
        },
        {
            "process_id": "quality_inspection",
            "process_name": "Quality Inspection",
            "duration_hours": 1,        # DEMO
            "power_kw": 5,              # DEMO kW
            "is_flexible": True,        # DEMO: flexible in this flow
            "dependencies": ["cooling"],
            "machine_id": "qc_bench",
            "quantity": 60,             # DEMO samples
        },
        {
            "process_id": "packaging",
            "process_name": "Packaging",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 12,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["quality_inspection"],
            "machine_id": "pack_line",
            "quantity": 2800,           # DEMO units
        },
        {
            "process_id": "storage",
            "process_name": "Storage",
            "duration_hours": 1,        # DEMO
            "power_kw": 6,              # DEMO kW (refrigerated storage)
            "is_flexible": True,
            "dependencies": ["packaging"],
            "machine_id": "cold_store",
            "quantity": 2800,
            "latest_finish": 18,        # DEMO: storage in place before deadline
        },
    ],
    "machines": [
        {"machine_id": "prep_area", "machine_name": "Prep Area",
         "capacity": 1, "availability": "single area",
         "compatible_processes": ["raw_prep"], "power_kw": 8},
        {"machine_id": "wash_line_1", "machine_name": "Wash Line 1",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["washing_line1"], "power_kw": 15},
        {"machine_id": "wash_line_2", "machine_name": "Wash Line 2",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["washing_line2"], "power_kw": 15},
        {"machine_id": "cutter", "machine_name": "Industrial Cutter",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["cutting"], "power_kw": 24},
        {"machine_id": "food_mixer", "machine_name": "Food Mixer",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["mixing"], "power_kw": 20},
        {"machine_id": "cooker", "machine_name": "Industrial Cooker",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["cooking"], "power_kw": 65},
        {"machine_id": "chiller", "machine_name": "Chiller",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["cooling"], "power_kw": 25},
        {"machine_id": "qc_bench", "machine_name": "QC Bench",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["quality_inspection"], "power_kw": 5},
        {"machine_id": "pack_line", "machine_name": "Packaging Line",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["packaging"], "power_kw": 12},
        {"machine_id": "cold_store", "machine_name": "Refrigerated Store",
         "capacity": 1, "availability": "single store",
         "compatible_processes": ["storage"], "power_kw": 6},
    ],
}


# ---------------------------------------------------------------------------
# 4. BEVERAGE (process industry, shared processing/filling equipment)
#    Water Treatment -> Ingredient Preparation -> Syrup/Ingredient Mixing
#    -> Blending -> Filtration -> Pasteurization -> Cooling -> Filling
#    -> Capping -> Labeling -> Packaging -> Quality Inspection
#    Shared resources: blender shared by two blend lines; filler+capper
#    share the filling monoblock.
# ---------------------------------------------------------------------------
DEMO_BEVERAGE_FACTORY = {
    "factory_name": "Demo Beverage Factory",
    "planning_horizon_hours": 22,   # DEMO
    "production_deadline": 20,      # DEMO
    "processes": [
        {
            "process_id": "water_treatment",
            "process_name": "Water Treatment",
            "duration_hours": 2,        # DEMO
            "power_kw": 18,             # DEMO kW
            "is_flexible": False,
            "dependencies": [],
            "machine_id": "water_plant",
            "quantity": 20000,          # DEMO liters
        },
        {
            "process_id": "ingredient_prep",
            "process_name": "Ingredient Preparation",
            "duration_hours": 1,        # DEMO
            "power_kw": 8,              # DEMO kW
            "is_flexible": True,
            "dependencies": ["water_treatment"],
            "machine_id": "prep_station",
            "quantity": 20000,
        },
        {
            "process_id": "syrup_mixing",
            "process_name": "Syrup/Ingredient Mixing",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 20,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["ingredient_prep"],
            "machine_id": "syrup_mixer",
            "quantity": 15000,
        },
        {
            # Blend line A uses the shared blender first
            "process_id": "blending_a",
            "process_name": "Blending Line A",
            "duration_hours": 1,        # DEMO
            "power_kw": 25,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["syrup_mixing"],
            "machine_id": "blender",
            "quantity": 10000,
        },
        {
            # Blend line B waits for the same blender (machine conflict)
            "process_id": "blending_b",
            "process_name": "Blending Line B",
            "duration_hours": 1,        # DEMO
            "power_kw": 25,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["syrup_mixing"],
            "machine_id": "blender",
            "quantity": 10000,
        },
        {
            "process_id": "filtration",
            "process_name": "Filtration",
            "duration_hours": 1,        # DEMO
            "power_kw": 15,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["blending_a", "blending_b"],
            "machine_id": "filter",
            "quantity": 20000,
        },
        {
            "process_id": "pasteurization",
            "process_name": "Pasteurization",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 55,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["filtration"],
            "machine_id": "pasteurizer",
            "quantity": 20000,
        },
        {
            "process_id": "cooling",
            "process_name": "Cooling",
            "duration_hours": 1,        # DEMO
            "power_kw": 22,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["pasteurization"],
            "machine_id": "chiller",
            "quantity": 20000,
        },
        {
            "process_id": "filling",
            "process_name": "Filling",
            "duration_hours": 2,        # DEMO
            "power_kw": 30,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["cooling"],
            "machine_id": "filling_monoblock",
            "quantity": 40000,          # DEMO bottles
        },
        {
            # Capping shares the filling monoblock (machine conflict)
            "process_id": "capping",
            "process_name": "Capping",
            "duration_hours": 1,        # DEMO
            "power_kw": 12,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["filling"],
            "machine_id": "filling_monoblock",
            "quantity": 40000,
        },
        {
            "process_id": "labeling",
            "process_name": "Labeling",
            "duration_hours": 1,        # DEMO
            "power_kw": 10,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["capping"],
            "machine_id": "labeler",
            "quantity": 40000,
        },
        {
            "process_id": "packaging",
            "process_name": "Packaging",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 12,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["labeling"],
            "machine_id": "pack_line",
            "quantity": 40000,
        },
        {
            "process_id": "quality_inspection",
            "process_name": "Quality Inspection",
            "duration_hours": 1,        # DEMO
            "power_kw": 5,              # DEMO kW
            "is_flexible": True,        # DEMO: flexible in this flow
            "dependencies": ["packaging"],
            "machine_id": "qc_station",
            "quantity": 200,            # DEMO samples
        },
    ],
    "machines": [
        {"machine_id": "water_plant", "machine_name": "Water Treatment Plant",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["water_treatment"], "power_kw": 18},
        {"machine_id": "prep_station", "machine_name": "Ingredient Prep Station",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["ingredient_prep"], "power_kw": 8},
        {"machine_id": "syrup_mixer", "machine_name": "Syrup Mixer",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["syrup_mixing"], "power_kw": 22},
        {"machine_id": "blender", "machine_name": "Blending Vessel (shared)",
         "capacity": 1, "availability": "single vessel, shared",
         "compatible_processes": ["blending_a", "blending_b"], "power_kw": 25},
        {"machine_id": "filter", "machine_name": "Beverage Filter",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["filtration"], "power_kw": 15},
        {"machine_id": "pasteurizer", "machine_name": "Pasteurizer",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["pasteurization"], "power_kw": 55},
        {"machine_id": "chiller", "machine_name": "Beverage Chiller",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["cooling"], "power_kw": 22},
        {"machine_id": "filling_monoblock", "machine_name": "Filling/Capping Monoblock (shared)",
         "capacity": 1, "availability": "single monoblock, shared",
         "compatible_processes": ["filling", "capping"], "power_kw": 30},
        {"machine_id": "labeler", "machine_name": "Labeler",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["labeling"], "power_kw": 10},
        {"machine_id": "pack_line", "machine_name": "Packaging Line",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["packaging"], "power_kw": 12},
        {"machine_id": "qc_station", "machine_name": "QC Station",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["quality_inspection"], "power_kw": 5},
    ],
}


# ---------------------------------------------------------------------------
# 5. PHARMACEUTICAL (batch/process manufacturing, strict dependencies,
#    non-flexible quality/testing stages)
#    Raw Material Dispensing -> Sieving -> Blending -> Granulation -> Drying
#    -> Milling -> Tablet Compression -> Coating -> Quality Testing
#    -> Packaging
# ---------------------------------------------------------------------------
DEMO_PHARMA_FACTORY = {
    "factory_name": "Demo Pharmaceutical Factory",
    "planning_horizon_hours": 24,   # DEMO
    "production_deadline": 20,      # DEMO
    "processes": [
        {
            "process_id": "dispensing",
            "process_name": "Raw Material Dispensing",
            "duration_hours": 1,        # DEMO
            "power_kw": 5,              # DEMO kW
            "is_flexible": False,
            "dependencies": [],
            "machine_id": "dispensing_booth",
            "quantity": 500000,         # DEMO tablets (batch)
        },
        {
            "process_id": "sieving",
            "process_name": "Sieving",
            "duration_hours": 1,        # DEMO
            "power_kw": 8,              # DEMO kW
            "is_flexible": True,
            "dependencies": ["dispensing"],
            "machine_id": "sieve",
            "quantity": 500000,
        },
        {
            "process_id": "blending",
            "process_name": "Blending",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 15,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["sieving"],
            "machine_id": "blender",
            "quantity": 500000,
        },
        {
            "process_id": "granulation",
            "process_name": "Granulation",
            "duration_hours": 2,        # DEMO
            "power_kw": 30,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["blending"],
            "machine_id": "granulator",
            "quantity": 500000,
        },
        {
            "process_id": "drying",
            "process_name": "Drying",
            "duration_hours": 3,        # DEMO
            "power_kw": 45,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["granulation"],
            "machine_id": "dryer",
            "quantity": 500000,
        },
        {
            "process_id": "milling",
            "process_name": "Milling",
            "duration_hours": 1,        # DEMO
            "power_kw": 18,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["drying"],
            "machine_id": "mill",
            "quantity": 500000,
        },
        {
            "process_id": "compression",
            "process_name": "Tablet Compression",
            "duration_hours": 2.5,      # DEMO
            "power_kw": 25,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["milling"],
            "machine_id": "press",
            "quantity": 500000,
        },
        {
            "process_id": "coating",
            "process_name": "Coating",
            "duration_hours": 2,        # DEMO
            "power_kw": 20,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["compression"],
            "machine_id": "coater",
            "quantity": 500000,
        },
        {
            "process_id": "quality_testing",
            "process_name": "Quality Testing",
            "duration_hours": 2,        # DEMO
            "power_kw": 8,              # DEMO kW
            "is_flexible": False,       # strict non-flexible QC stage
            "dependencies": ["coating"],
            "machine_id": "qc_lab",
            "quantity": 50,             # DEMO samples
        },
        {
            "process_id": "packaging",
            "process_name": "Packaging",
            "duration_hours": 2,        # DEMO
            "power_kw": 12,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["quality_testing"],
            "machine_id": "pack_line",
            "quantity": 500000,
            "latest_finish": 20,        # DEMO: batch must be packed by deadline
        },
    ],
    "machines": [
        {"machine_id": "dispensing_booth", "machine_name": "Dispensing Booth",
         "capacity": 1, "availability": "single booth (HEPA)",
         "compatible_processes": ["dispensing"], "power_kw": 5},
        {"machine_id": "sieve", "machine_name": "Vibratory Sieve",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["sieving"], "power_kw": 8},
        {"machine_id": "blender", "machine_name": "Pharma Blender",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["blending"], "power_kw": 15},
        {"machine_id": "granulator", "machine_name": "Granulator",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["granulation"], "power_kw": 30},
        {"machine_id": "dryer", "machine_name": "Fluid Bed Dryer",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["drying"], "power_kw": 45},
        {"machine_id": "mill", "machine_name": "Pharma Mill",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["milling"], "power_kw": 18},
        {"machine_id": "press", "machine_name": "Tablet Press",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["compression"], "power_kw": 25},
        {"machine_id": "coater", "machine_name": "Coating Pan",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["coating"], "power_kw": 20},
        {"machine_id": "qc_lab", "machine_name": "QC Laboratory",
         "capacity": 1, "availability": "single lab",
         "compatible_processes": ["quality_testing"], "power_kw": 8},
        {"machine_id": "pack_line", "machine_name": "Blister Pack Line",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["packaging"], "power_kw": 12},
    ],
}


# ---------------------------------------------------------------------------
# 6. AUTOMOTIVE (discrete manufacturing, deliberately different structure:
#    machining, joining, assembly; sub-assemblies merge into final assembly)
#    Raw Material/Part Preparation -> Cutting/Forming -> Machining
#    -> Surface Treatment -> Painting -> Component Assembly -> Sub-Assembly
#    -> Final Assembly -> Quality Inspection -> Testing
# ---------------------------------------------------------------------------
DEMO_AUTOMOTIVE_FACTORY = {
    "factory_name": "Demo Automotive Factory",
    "planning_horizon_hours": 24,   # DEMO
    "production_deadline": 18,      # DEMO
    "processes": [
        {
            "process_id": "part_prep",
            "process_name": "Raw Material/Part Preparation",
            "duration_hours": 1,        # DEMO
            "power_kw": 12,             # DEMO kW
            "is_flexible": False,
            "dependencies": [],
            "machine_id": "prep_cell",
            "quantity": 120,            # DEMO vehicle sets
        },
        {
            "process_id": "cutting_forming",
            "process_name": "Cutting/Forming",
            "duration_hours": 2,        # DEMO
            "power_kw": 40,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["part_prep"],
            "machine_id": "press_shop",
            "quantity": 120,
        },
        {
            # Machining cell 1 (parallel line)
            "process_id": "machining_line1",
            "process_name": "Machining Line 1",
            "duration_hours": 2.5,      # DEMO
            "power_kw": 35,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["cutting_forming"],
            "machine_id": "cnc_cell_1",
            "quantity": 60,
        },
        {
            # Machining cell 2 (parallel line)
            "process_id": "machining_line2",
            "process_name": "Machining Line 2",
            "duration_hours": 2.5,      # DEMO
            "power_kw": 35,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["cutting_forming"],
            "machine_id": "cnc_cell_2",
            "quantity": 60,
        },
        {
            "process_id": "surface_treatment",
            "process_name": "Surface Treatment",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 28,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["machining_line1", "machining_line2"],
            "machine_id": "treatment_line",
            "quantity": 120,
        },
        {
            "process_id": "painting",
            "process_name": "Painting",
            "duration_hours": 2,        # DEMO
            "power_kw": 50,             # DEMO kW (booth + curing)
            "is_flexible": True,
            "dependencies": ["surface_treatment"],
            "machine_id": "paint_booth",
            "quantity": 120,
        },
        {
            # Sub-assembly branch (shares the assembly cell with component
            # assembly via the same bench)
            "process_id": "component_assembly",
            "process_name": "Component Assembly",
            "duration_hours": 2,        # DEMO
            "power_kw": 15,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["cutting_forming"],
            "machine_id": "assembly_cell",
            "quantity": 120,
        },
        {
            "process_id": "sub_assembly",
            "process_name": "Sub-Assembly",
            "duration_hours": 1.5,      # DEMO
            "power_kw": 18,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["component_assembly", "painting"],
            "machine_id": "assembly_cell",   # shared assembly cell
            "quantity": 120,
        },
        {
            "process_id": "final_assembly",
            "process_name": "Final Assembly",
            "duration_hours": 3,        # DEMO
            "power_kw": 25,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["sub_assembly"],
            "machine_id": "final_line",
            "quantity": 120,
        },
        {
            "process_id": "quality_inspection",
            "process_name": "Quality Inspection",
            "duration_hours": 1,        # DEMO
            "power_kw": 6,              # DEMO kW
            "is_flexible": False,       # quality gate is pinned
            "dependencies": ["final_assembly"],
            "machine_id": "qc_gate",
            "quantity": 120,
        },
        {
            "process_id": "testing",
            "process_name": "Testing",
            "duration_hours": 1,        # DEMO
            "power_kw": 20,             # DEMO kW (roller dyno)
            "is_flexible": True,
            "dependencies": ["quality_inspection"],
            "machine_id": "test_rig",
            "quantity": 120,
            "latest_finish": 18,        # DEMO: tested units by end of shift
        },
    ],
    "machines": [
        {"machine_id": "prep_cell", "machine_name": "Preparation Cell",
         "capacity": 1, "availability": "single cell",
         "compatible_processes": ["part_prep"], "power_kw": 12},
        {"machine_id": "press_shop", "machine_name": "Press Shop",
         "capacity": 1, "availability": "single shop",
         "compatible_processes": ["cutting_forming"], "power_kw": 40},
        {"machine_id": "cnc_cell_1", "machine_name": "CNC Machining Cell 1",
         "capacity": 1, "availability": "single cell",
         "compatible_processes": ["machining_line1"], "power_kw": 35},
        {"machine_id": "cnc_cell_2", "machine_name": "CNC Machining Cell 2",
         "capacity": 1, "availability": "single cell",
         "compatible_processes": ["machining_line2"], "power_kw": 35},
        {"machine_id": "treatment_line", "machine_name": "Surface Treatment Line",
         "capacity": 1, "availability": "single line",
         "compatible_processes": ["surface_treatment"], "power_kw": 28},
        {"machine_id": "paint_booth", "machine_name": "Paint Booth + Curing",
         "capacity": 1, "availability": "single booth",
         "compatible_processes": ["painting"], "power_kw": 50},
        {"machine_id": "assembly_cell", "machine_name": "Assembly Cell (shared)",
         "capacity": 1, "availability": "single cell, shared",
         "compatible_processes": ["component_assembly", "sub_assembly"],
         "power_kw": 18},
        {"machine_id": "final_line", "machine_name": "Final Assembly Line",
         "capacity": 1, "availability": "single line",
         "compatible_processes": ["final_assembly"], "power_kw": 25},
        {"machine_id": "qc_gate", "machine_name": "Quality Gate",
         "capacity": 1, "availability": "single station",
         "compatible_processes": ["quality_inspection"], "power_kw": 6},
        {"machine_id": "test_rig", "machine_name": "End-of-Line Test Rig",
         "capacity": 1, "availability": "single rig",
         "compatible_processes": ["testing"], "power_kw": 20},
    ],
}


# ---------------------------------------------------------------------------
# NON-INDUSTRY DEMO FACTORY: kept from V4 as a generic test factory that is
# deliberately NOT one of the six industries (proves engine genericity).
# ---------------------------------------------------------------------------
DEMO_FURNITURE_FACTORY = {
    "factory_name": "Demo Furniture Workshop",
    "planning_horizon_hours": 24,   # DEMO: 24-hour planning window
    "production_deadline": 16,      # DEMO: all processes finish by hour 16
    "processes": [
        {
            "process_id": "cutting",
            "process_name": "Panel Cutting",
            "duration_hours": 2,        # DEMO
            "power_kw": 35,             # DEMO kW
            "is_flexible": False,
            "dependencies": [],
            "machine_id": "saw",
            "quantity": 40,             # DEMO panels
        },
        {
            "process_id": "hardware_kitting",
            "process_name": "Hardware Kitting",
            "duration_hours": 1,        # DEMO
            "power_kw": 5,              # DEMO kW
            "is_flexible": True,
            "dependencies": [],
            "machine_id": "kitting_bench",
            "quantity": 40,
            "earliest_start": 1,        # DEMO: not before hour 1
        },
        {
            "process_id": "sanding",
            "process_name": "Surface Sanding",
            "duration_hours": 2.5,      # DEMO
            "power_kw": 25,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["cutting"],
            "machine_id": "sander",
            "quantity": 40,
        },
        {
            "process_id": "frame_building",
            "process_name": "Frame Building",
            "duration_hours": 2,        # DEMO
            "power_kw": 15,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["cutting"],
            "machine_id": "assembly_bench",
            "quantity": 20,             # DEMO frames
        },
        {
            "process_id": "assembly",
            "process_name": "Product Assembly",
            "duration_hours": 3,        # DEMO
            "power_kw": 20,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["sanding", "frame_building", "hardware_kitting"],
            "machine_id": "assembly_bench",   # shared with frame_building
            "quantity": 20,             # DEMO units
        },
        {
            "process_id": "quality_gate",
            "process_name": "Quality Inspection",
            "duration_hours": 1,        # DEMO
            "power_kw": 5,              # DEMO kW
            "is_flexible": False,       # pinned to baseline start
            "dependencies": ["assembly"],
            "machine_id": "inspection_bench",
            "quantity": 20,
        },
        {
            "process_id": "finishing",
            "process_name": "Surface Finishing",
            "duration_hours": 2,        # DEMO
            "power_kw": 30,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["quality_gate"],
            "machine_id": "finishing_station",
            "quantity": 20,
        },
        {
            "process_id": "packaging",
            "process_name": "Packaging",
            "duration_hours": 1,        # DEMO
            "power_kw": 10,             # DEMO kW
            "is_flexible": True,
            "dependencies": ["finishing"],
            "machine_id": "finishing_station",  # shared finishing station
            "quantity": 20,             # DEMO units
            "latest_finish": 16,        # DEMO: explicit process deadline
        },
    ],
    "machines": [
        {"machine_id": "saw", "machine_name": "Panel Saw",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["cutting"], "power_kw": 38},
        {"machine_id": "kitting_bench", "machine_name": "Kitting Bench",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["hardware_kitting"], "power_kw": 5},
        {"machine_id": "sander", "machine_name": "Wide-Belt Sander",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["sanding"], "power_kw": 28},
        {"machine_id": "assembly_bench", "machine_name": "Assembly Bench (shared)",
         "capacity": 1, "availability": "single unit, shared",
         "compatible_processes": ["frame_building", "assembly"], "power_kw": 18},
        {"machine_id": "inspection_bench", "machine_name": "Inspection Bench",
         "capacity": 1, "availability": "single unit",
         "compatible_processes": ["quality_gate"], "power_kw": 5},
        {"machine_id": "finishing_station", "machine_name": "Finishing Station (shared)",
         "capacity": 1, "availability": "single unit, shared",
         "compatible_processes": ["finishing", "packaging"], "power_kw": 30},
    ],
}


# Generic factory registry: adding a new factory = adding a config dict here.
# The optimization engine never reads this registry (only the CLI does).
AVAILABLE_FACTORIES = {
    "chocolate": DEMO_CHOCOLATE_FACTORY,
    "cosmetics": DEMO_COSMETICS_FACTORY,
    "food": DEMO_FOOD_FACTORY,
    "beverage": DEMO_BEVERAGE_FACTORY,
    "pharmaceutical": DEMO_PHARMA_FACTORY,
    "automotive": DEMO_AUTOMOTIVE_FACTORY,
    # Non-industry demo kept for genericity testing:
    "furniture": DEMO_FURNITURE_FACTORY,
}

# The six target industries for the framework validation
INDUSTRY_FACTORIES = {
    "chocolate": DEMO_CHOCOLATE_FACTORY,
    "cosmetics": DEMO_COSMETICS_FACTORY,
    "food": DEMO_FOOD_FACTORY,
    "beverage": DEMO_BEVERAGE_FACTORY,
    "pharmaceutical": DEMO_PHARMA_FACTORY,
    "automotive": DEMO_AUTOMOTIVE_FACTORY,
}
