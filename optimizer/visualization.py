"""
ByteMe Visualization (DEMO/SIMULATED DATA ONLY)

Text-based, dependency-free visualizations that make it easy to compare
BASELINE vs OPTIMIZED schedules and energy usage. All data shown is
DEMO/SIMULATED.

- render_gantt:            Gantt-style production schedule (process, machine,
                           start, end, duration, flexible status)
- render_gantt_comparison: baseline and optimized Gantts stacked for contrast
- render_energy_profile:   solar availability over time + per-hour grid usage
- render_energy_comparison: energy/cost bars, baseline vs optimized
"""

from optimizer.models import TIME_SCALE

BAR_WIDTH = 48  # character columns spanning the planning horizon


def render_gantt(schedule, title="SCHEDULE"):
    """
    Render one schedule as a text Gantt chart.

    Each row shows: process name, machine, start-end, duration, flexible
    status, and a timeline bar positioned across the planning horizon.
    """
    horizon = max(p["end_time"] for p in schedule["processes"])
    print("=" * 100)
    print(f"{title} - {schedule.get('factory_name', 'Factory')}")
    print(f"Status: {schedule['status']}   Makespan: {schedule['makespan']:.2f} h")
    print("=" * 100)

    # Timeline ruler: one character per 30 minutes, hour labels under it
    ruler_chars = int(horizon * 2)
    ruler = "".join(
        str(hour % 10) if half == 0 else "."
        for half in range(ruler_chars)
        for hour in [half // 2]
    )
    print(f"{'Process':<24} {'Machine':<18} {'Start':>5} {'End':>5} "
          f"{'Dur':>4}  {'Flex':<4} Timeline (each char = 30 min, ruler = hours)")
    print(f"{'':<24} {'':<18} {'':>5} {'':>5} {'':>4}  {'':<4} {ruler}")
    print("-" * 100)

    for p in schedule["processes"]:
        # Timeline bar
        start_col = int(round(p["start_time"] * 2))
        end_col = max(int(round(p["end_time"] * 2)), start_col + 1)
        bar = ["."] * ruler_chars
        for col in range(start_col, min(end_col, ruler_chars)):
            bar[col] = "#" if p["is_flexible"] else "="
        flex = "Y" if p["is_flexible"] else "n"
        print(
            f"{p['process_name'][:23]:<24} "
            f"{(p.get('machine_id') or '-')[:17]:<18} "
            f"{p['start_time']:>5.1f} {p['end_time']:>5.1f} "
            f"{p['end_time'] - p['start_time']:>4.1f}  {flex:<4} "
            f"{''.join(bar)}"
        )

    print("-" * 100)
    print("Legend: '#' = flexible process, '=' = non-flexible (pinned) process")
    print()


def render_gantt_comparison(baseline, optimized):
    """Render baseline and optimized Gantt charts stacked for comparison."""
    render_gantt(baseline, title="BASELINE SCHEDULE (makespan-minimized)")
    render_gantt(optimized, title="OPTIMIZED SCHEDULE (energy/cost-optimized)")


def render_energy_profile(solar_profile, schedule, title="ENERGY PROFILE"):
    """
    Render the solar availability profile and per-hour grid usage of a
    schedule. Grid usage follows the SAME shared-solar-pool allocation as
    reporting (shared slot pool never exceeded, per-process draw cap while
    running) so the chart always matches the reported numbers.
    """
    from optimizer.optimizer import _allocate_solar_by_slot

    horizon = int(max(p["end_time"] for p in schedule["processes"]))

    # Shared-pool allocation: per-process solar/grid exactly as reported
    _, grid_by_slot = _allocate_solar_by_slot(
        [{"process_id": p["process_id"], "start_time": p["start_time"],
          "end_time": p["end_time"], "power_kw": p["power_kw"]}
         for p in schedule["processes"]],
        solar_profile,
        include_grid_by_slot=True,
    )

    # Aggregate the exact half-hour grid allocation into displayed hours.
    grid_by_hour = {h: 0.0 for h in range(horizon + 1)}
    for slot, grid_kwh in grid_by_slot.items():
        grid_by_hour[slot // TIME_SCALE] += grid_kwh

    scale = max(max(grid_by_hour.values()), max(
        solar_profile.get(h % 24, 0) for h in range(horizon + 1)
    ), 1.0)

    print("=" * 100)
    print(f"{title} - {schedule.get('factory_name', 'Factory')}")
    print("=" * 100)
    print(f"{'Hour':<6} {'Solar kW':>9}  {'Solar':<34} {'Grid kWh':>9}  {'Grid':<34}")
    print("-" * 100)
    for hour in range(horizon + 1):
        solar_kw = solar_profile.get(hour % 24, 0)
        grid = grid_by_hour[hour]
        solar_bar = "#" * int(round(solar_kw / scale * 32))
        grid_bar = "#" * int(round(grid / scale * 32))
        print(f"{hour:<6} {solar_kw:>9.1f}  {solar_bar:<34} {grid:>9.2f}  {grid_bar:<34}")
    print("-" * 100)
    print("Solar availability is DEMO/SIMULATED. Grid bars show process demand "
          "not covered by solar.")
    print()


def render_energy_comparison(baseline, optimized, tariff_profile=None):
    """
    Compare energy and cost between baseline and optimized schedules with
    simple bar visuals.
    """
    def total_cost(schedule):
        return sum(p.get("energy_cost", 0.0) for p in schedule["processes"])

    metrics = {
        "Solar (kWh)": (baseline["total_solar_kwh"], optimized["total_solar_kwh"]),
        "Grid (kWh)": (baseline["total_grid_kwh"], optimized["total_grid_kwh"]),
        "Energy (kWh)": (baseline["total_energy_kwh"], optimized["total_energy_kwh"]),
        "Cost": (total_cost(baseline), total_cost(optimized)),
    }
    scale = max(v for pair in metrics.values() for v in pair) or 1.0

    print("=" * 100)
    print(f"ENERGY / COST COMPARISON - {optimized.get('factory_name', 'Factory')}")
    print("(DEMO/SIMULATED data)")
    print("=" * 100)
    label_w = 14
    print(f"{'Metric':<{label_w}} {'BASELINE':<40} {'OPTIMIZED':<40}")
    print("-" * 100)
    for name, (b_val, o_val) in metrics.items():
        b_bar = "#" * int(round(b_val / scale * 30))
        o_bar = "#" * int(round(o_val / scale * 30))
        print(f"{name:<{label_w}} {b_val:>8.2f} {b_bar:<30} "
              f"{o_val:>8.2f} {o_bar:<30}")
    print("-" * 100)
    print()
