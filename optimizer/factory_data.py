"""
Demo Chocolate Factory Data Structure

This module contains a demonstration factory configuration for the ByteMe
manufacturing energy scheduling system. All values are DEMO/SIMULATED and
not intended to represent real industrial measurements.
"""

# Demo Chocolate Factory Configuration
DEMO_CHOCOLATE_FACTORY = {
    "factory_name": "Demo Chocolate Factory",
    "planning_horizon_hours": 24,  # DEMO: 24-hour planning window
    "production_deadline": 18,  # DEMO: Deadline at hour 18 (end of day)
    "processes": [
        {
            "process_id": "roasting",
            "process_name": "Roasting",
            "duration_hours": 2,  # DEMO: 2-hour duration
            "power_kw": 50,  # DEMO: 50 kW power consumption
            "is_flexible": False,  # Roasting is inflexible (time-sensitive)
            "dependencies": []  # No dependencies (first process)
        },
        {
            "process_id": "grinding",
            "process_name": "Grinding",
            "duration_hours": 1.5,  # DEMO: 1.5-hour duration
            "power_kw": 30,  # DEMO: 30 kW power consumption
            "is_flexible": True,  # Grinding can be shifted
            "dependencies": ["roasting"]  # Must complete after roasting
        },
        {
            "process_id": "mixing",
            "process_name": "Mixing",
            "duration_hours": 2,  # DEMO: 2-hour duration
            "power_kw": 40,  # DEMO: 40 kW power consumption
            "is_flexible": True,  # Mixing can be shifted
            "dependencies": ["grinding"]  # Must complete after grinding
        },
        {
            "process_id": "conching",
            "process_name": "Conching",
            "duration_hours": 3,  # DEMO: 3-hour duration
            "power_kw": 25,  # DEMO: 25 kW power consumption
            "is_flexible": True,  # Conching can be shifted
            "dependencies": ["mixing"]  # Must complete after mixing
        }
    ]
}
