"""
ByteMe Console Entry Point (Version 4)

Runs the generic optimization engine on every factory registered in
AVAILABLE_FACTORIES. This module is the only place that binds the engine
to specific DEMO factory configurations - the engine itself
(optimizer/optimizer.py) is fully factory-agnostic.

Usage:
    python -m optimizer
"""
from optimizer.factory_data import AVAILABLE_FACTORIES
from optimizer.optimizer import (
    create_baseline_schedule,
    create_cost_optimized_schedule,
    load_factory_config,
    print_baseline_schedule,
    print_solar_aware_schedule,
    print_summary_metrics,
)


def run_factory(factory_data):
    """Run the full baseline + solar + tariff optimization for one factory."""
    config = load_factory_config(factory_data)
    print(f"\n{'#' * 80}")
    print(f"# FACTORY: {config.factory_name}")
    print(f"{'#' * 80}\n")

    print("Generating baseline schedule...")
    baseline_schedule = create_baseline_schedule(config)
    print()

    print("Generating solar + tariff optimized schedule...")
    optimized_schedule = create_cost_optimized_schedule(
        config, baseline_schedule=baseline_schedule
    )
    print()

    print_baseline_schedule(baseline_schedule)
    print_solar_aware_schedule(optimized_schedule)
    print_summary_metrics(baseline_schedule, optimized_schedule)
    return baseline_schedule, optimized_schedule


def main():
    """Main function: run the optimizer for every registered demo factory."""
    print("ByteMe Manufacturing Energy Scheduler - Version 4")
    print("Generic Factory Model + Solar + Tariff Cost Optimization")
    print("(all data DEMO/SIMULATED)")

    for factory_data in AVAILABLE_FACTORIES.values():
        run_factory(factory_data)


if __name__ == "__main__":
    main()
