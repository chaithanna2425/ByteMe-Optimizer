"""Opt-in request limits for deployments embedding the optimizer library."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class OptimizerResourceLimits:
    """Deployment-selected upper bounds; ``None`` leaves that bound unset.

    The package intentionally supplies no production thresholds. Deployments
    must select values from their workload and service-level objectives.
    """

    max_processes: int | None = None
    max_planning_horizon_hours: float | None = None
    max_solver_seconds_per_stage: float | None = None

    def __post_init__(self):
        if (self.max_processes is not None
                and (isinstance(self.max_processes, bool)
                     or not isinstance(self.max_processes, int)
                     or self.max_processes < 1)):
            raise ValueError("max_processes must be a positive integer or None")
        if (self.max_planning_horizon_hours is not None
                and (isinstance(self.max_planning_horizon_hours, bool)
                     or not isinstance(self.max_planning_horizon_hours, (int, float))
                     or not _is_finite(self.max_planning_horizon_hours)
                     or self.max_planning_horizon_hours <= 0)):
            raise ValueError(
                "max_planning_horizon_hours must be a positive finite number "
                "or None"
            )
        if (self.max_solver_seconds_per_stage is not None
                and (isinstance(self.max_solver_seconds_per_stage, bool)
                     or not isinstance(
                         self.max_solver_seconds_per_stage, (int, float)
                     )
                     or not _is_finite(self.max_solver_seconds_per_stage)
                     or self.max_solver_seconds_per_stage <= 0)):
            raise ValueError(
                "max_solver_seconds_per_stage must be a positive finite "
                "number or None"
            )


def _is_finite(value):
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False
