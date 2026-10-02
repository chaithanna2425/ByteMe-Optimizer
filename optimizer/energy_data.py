"""
ByteMe Energy Data (DEMO/SIMULATED DATA ONLY)

This module is the ENERGY DATA LAYER of the ByteMe optimizer. It is fully
separated from both the factory configuration (factory_data.py) and the
optimization engine (optimizer.py). All values are DEMO/SIMULATED and not
intended to represent real weather, real solar production, or real
electricity prices.

Any scheduler run simply passes the profiles below (or any other profiles
following the same generic structure: hour -> value) into the engine.
"""

# DEMO Solar Availability Profile
# Hourly solar power availability in kW (DEMO/SIMULATED values)
DEMO_SOLAR_PROFILE = {
    0: 0,    # Midnight
    1: 0,    # 1 AM
    2: 0,    # 2 AM
    3: 0,    # 3 AM
    4: 0,    # 4 AM
    5: 0,    # 5 AM
    6: 5,    # 6 AM - sunrise
    7: 15,   # 7 AM
    8: 30,   # 8 AM
    9: 45,   # 9 AM
    10: 60,  # 10 AM
    11: 70,  # 11 AM
    12: 75,  # 12 PM - peak solar
    13: 70,  # 1 PM
    14: 60,  # 2 PM
    15: 45,  # 3 PM
    16: 30,  # 4 PM
    17: 15,  # 5 PM
    18: 5,   # 6 PM - sunset
    19: 0,   # 7 PM
    20: 0,   # 8 PM
    21: 0,   # 9 PM
    22: 0,   # 10 PM
    23: 0,   # 11 PM
}

# DEMO Electricity Tariff Profile
# Hourly electricity tariff in currency units per kWh (DEMO/SIMULATED values,
# NOT real electricity prices). Generic structure: hour -> tariff per kWh.
DEMO_TARIFF_PROFILE = {
    0: 0.50,  # Midnight
    1: 0.50,  # 1 AM
    2: 0.50,  # 2 AM
    3: 0.50,  # 3 AM
    4: 0.45,  # 4 AM
    5: 0.40,  # 5 AM
    6: 0.30,  # 6 AM
    7: 0.20,  # 7 AM
    8: 0.15,  # 8 AM
    9: 0.10,  # 9 AM
    10: 0.08,  # 10 AM - cheap midday solar hours
    11: 0.06,  # 11 AM
    12: 0.05,  # 12 PM - cheapest (peak solar)
    13: 0.06,  # 1 PM
    14: 0.08,  # 2 PM
    15: 0.10,  # 3 PM
    16: 0.15,  # 4 PM
    17: 0.25,  # 5 PM
    18: 0.40,  # 6 PM - evening ramp
    19: 0.50,  # 7 PM
    20: 0.55,  # 8 PM - evening peak
    21: 0.55,  # 9 PM
    22: 0.55,  # 10 PM
    23: 0.50,  # 11 PM
}
