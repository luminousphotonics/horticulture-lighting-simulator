"""Explicit control-zone-to-module power scheduling for SMD layouts."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Final, Sequence

from .positions import SmdLayout

LEGACY_DEFAULT_CONTROL_ZONE_POWER_SCHEDULE_ID: Final = (
    "smd_legacy_default_control_zone_power_schedule_v1"
)
DEFAULT_OPTIMIZED_MAX_WATTS_PER_MODULE: Final = 100.0
PROPOSED_RATED_REFERENCE_WATTS_PER_MODULE: Final = (
    DEFAULT_OPTIMIZED_MAX_WATTS_PER_MODULE
)

# Legacy watts per module indexed by the historical concentric control zone.
# Only the explicitly named legacy helper may extend this table by inheritance.
LEGACY_DEFAULT_CONTROL_ZONE_POWER_W: Final[tuple[float, ...]] = (
    31.827,
    28.284,
    33.154,
    30.241,
    25.900,
    44.723,
    3.596,
    70.760,
    70.760,
)


@dataclass(frozen=True, slots=True)
class ModulePowerSchedule:
    """Module-aligned powers and auditable schedule metadata."""

    schedule_source: str
    watts_by_module: tuple[float, ...]
    control_zone_count: int
    module_count: int
    min_watts: float
    max_watts: float
    total_watts: float
    watts_by_control_zone: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class LegacyControlZonePowerSchedule:
    """The historical fallback table, isolated from optimized scheduling."""

    schedule_id: str
    watts_by_control_zone: tuple[float, ...]

    def coefficients(self, control_zone_count: int) -> tuple[float, ...]:
        """Expand the legacy schedule, inheriting its final value when needed."""

        _validate_control_zone_count(control_zone_count)
        final_power = self.watts_by_control_zone[-1]
        return tuple(
            self.watts_by_control_zone[index]
            if index < len(self.watts_by_control_zone)
            else final_power
            for index in range(control_zone_count)
        )


def build_optimized_module_schedule(
    layout: SmdLayout,
    control_zone_coefficients: Sequence[float],
    *,
    allow_extra_coefficients: bool = False,
) -> ModulePowerSchedule:
    """Map explicit solver coefficients to every module by control-zone ID.

    Missing coefficients always fail. Extra coefficients fail unless the
    caller deliberately sets ``allow_extra_coefficients=True``; in that case
    only the leading coefficients corresponding to this layout are used. This
    path never reads or expands the legacy fallback schedule.
    """

    supplied = _coefficient_vector(control_zone_coefficients)
    expected = layout.control_zone_count
    received = len(supplied)
    if received < expected:
        raise ValueError(
            "missing control-zone coefficients: "
            f"expected {expected}, received {received}."
        )
    if received > expected and not allow_extra_coefficients:
        raise ValueError(
            "extra control-zone coefficients: "
            f"expected {expected}, received {received}; set "
            "allow_extra_coefficients=True to explicitly ignore trailing values."
        )
    coefficients = supplied[:expected]
    return _build_schedule(
        layout,
        coefficients,
        schedule_source="optimized_explicit_control_zones",
    )


def build_uniform_module_schedule(
    layout: SmdLayout,
    watts_per_module: float,
) -> ModulePowerSchedule:
    """Assign one explicit electrical wattage to every Proposed module."""

    if (
        isinstance(watts_per_module, bool)
        or not isinstance(watts_per_module, int | float)
        or not math.isfinite(float(watts_per_module))
        or float(watts_per_module) < 0.0
    ):
        raise ValueError("watts_per_module must be finite and non-negative.")
    watts = float(watts_per_module)
    return _build_schedule(
        layout,
        tuple(watts for _ in layout.control_zone_indices),
        schedule_source="uniform_module_dimming",
    )


def legacy_default_control_zone_power_schedule() -> LegacyControlZonePowerSchedule:
    """Return the preserved historical schedule and its fallback semantics."""

    return LegacyControlZonePowerSchedule(
        schedule_id=LEGACY_DEFAULT_CONTROL_ZONE_POWER_SCHEDULE_ID,
        watts_by_control_zone=LEGACY_DEFAULT_CONTROL_ZONE_POWER_W,
    )


def build_legacy_default_module_schedule(layout: SmdLayout) -> ModulePowerSchedule:
    """Build module powers using the explicitly requested legacy fallback."""

    legacy = legacy_default_control_zone_power_schedule()
    return _build_schedule(
        layout,
        legacy.coefficients(layout.control_zone_count),
        schedule_source=legacy.schedule_id,
    )


def _build_schedule(
    layout: SmdLayout,
    coefficients: tuple[float, ...],
    *,
    schedule_source: str,
) -> ModulePowerSchedule:
    watts_by_module = tuple(
        coefficients[module.control_zone_index] for module in layout.modules
    )
    if not watts_by_module:
        raise ValueError("layout must contain at least one module.")
    return ModulePowerSchedule(
        schedule_source=schedule_source,
        watts_by_module=watts_by_module,
        control_zone_count=layout.control_zone_count,
        module_count=len(layout.modules),
        min_watts=min(watts_by_module),
        max_watts=max(watts_by_module),
        total_watts=math.fsum(watts_by_module),
        watts_by_control_zone=coefficients,
    )


def _coefficient_vector(values: Sequence[float]) -> tuple[float, ...]:
    if isinstance(values, str):
        raise ValueError("control-zone coefficients must be a numeric sequence.")
    try:
        coefficients = tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "control-zone coefficients must contain only numeric wattages."
        ) from exc
    for index, value in enumerate(coefficients):
        if not math.isfinite(value):
            raise ValueError(
                f"control-zone coefficient {index} must be a finite wattage."
            )
        if value < 0.0:
            raise ValueError(
                f"control-zone coefficient {index} must not be negative."
            )
    return coefficients


def _validate_control_zone_count(control_zone_count: int) -> None:
    if (
        isinstance(control_zone_count, bool)
        or not isinstance(control_zone_count, int)
        or control_zone_count <= 0
    ):
        raise ValueError("control_zone_count must be a positive integer.")
