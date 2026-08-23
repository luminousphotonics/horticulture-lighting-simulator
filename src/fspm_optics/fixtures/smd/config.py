"""Explicit local configuration for Proposed LED/SMD placement."""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from fspm_optics.geometry.mounting import (
    MountingGeometry,
    canonical_inches_to_meters,
)
from fspm_optics.geometry.room import DEFAULT_ROOM_HEIGHT_M
from fspm_optics.layout.mode import (
    DEFAULT_PROPOSED_LAYOUT_MODE,
    ProposedLayoutMode,
    resolve_proposed_layout_mode,
)
from fspm_optics.layout.ring import (
    DEFAULT_PROPOSED_RING_MODE,
    ProposedRingMode,
    resolve_proposed_ring_mode,
)


DEFAULT_MOUNT_Z_M = MountingGeometry.resolve(18.0).emitting_aperture_plane_z_m
STANDALONE_MODULE_FOOTPRINT_X_M = 0.150
STANDALONE_MODULE_FOOTPRINT_Y_M = 0.150
HISTORICAL_MODULE_FOOTPRINT_X_M = 0.1524
HISTORICAL_MODULE_FOOTPRINT_Y_M = 0.1650
STANDALONE_MECHANICAL_ENVELOPE_ID = "standalone_module_150x150mm_v1"
HISTORICAL_MECHANICAL_ENVELOPE_ID = "proposed_fixture_module_152p4x165mm_v1"
# Compatibility names now resolve to the production-default mode envelope.
DEFAULT_MODULE_FOOTPRINT_X_M = STANDALONE_MODULE_FOOTPRINT_X_M
DEFAULT_MODULE_FOOTPRINT_Y_M = STANDALONE_MODULE_FOOTPRINT_Y_M


@dataclass(frozen=True, slots=True)
class SmdLayoutConfig:
    """Room and module envelope values used to scale the modular topology."""

    room_length_ft: float
    room_width_ft: float
    room_height_m: float = DEFAULT_ROOM_HEIGHT_M
    mount_z_m: float = DEFAULT_MOUNT_Z_M
    wall_margin_m: float = canonical_inches_to_meters(1.0)
    fixture_clearance_m: float = 0.00635
    fixed_pitch_m: float | None = None
    align_long_axis_x: bool = True
    proposed_layout_mode: ProposedLayoutMode = DEFAULT_PROPOSED_LAYOUT_MODE
    proposed_ring_mode: ProposedRingMode = DEFAULT_PROPOSED_RING_MODE
    module_footprint_x_m: float = field(init=False)
    module_footprint_y_m: float = field(init=False)
    mechanical_envelope_id: str = field(init=False)

    def __post_init__(self) -> None:
        mode = resolve_proposed_layout_mode(self.proposed_layout_mode)
        ring_mode = resolve_proposed_ring_mode(self.proposed_ring_mode)
        object.__setattr__(self, "proposed_layout_mode", mode)
        object.__setattr__(self, "proposed_ring_mode", ring_mode)
        if (
            ring_mode is ProposedRingMode.REDUCED_ONE_RING
            and mode is not ProposedLayoutMode.STANDALONE_MODULES
        ):
            raise ValueError(
                "reduced_one_ring is supported only with the standalone_modules "
                "Proposed physical-composition mode."
            )
        envelope_id, footprint_x, footprint_y = mechanical_envelope(mode)
        object.__setattr__(self, "mechanical_envelope_id", envelope_id)
        object.__setattr__(self, "module_footprint_x_m", footprint_x)
        object.__setattr__(self, "module_footprint_y_m", footprint_y)
        for name in (
            "room_length_ft",
            "room_width_ft",
            "room_height_m",
            "module_footprint_x_m",
            "module_footprint_y_m",
        ):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        for name in ("mount_z_m", "wall_margin_m", "fixture_clearance_m"):
            object.__setattr__(self, name, _non_negative(name, getattr(self, name)))
        if self.mount_z_m >= self.room_height_m:
            raise ValueError("mount_z_m must lie below room_height_m.")
        if self.fixed_pitch_m is not None:
            object.__setattr__(
                self, "fixed_pitch_m", _positive("fixed_pitch_m", self.fixed_pitch_m)
            )


def mechanical_envelope(
    mode: ProposedLayoutMode | str,
) -> tuple[str, float, float]:
    """Return the immutable mode-owned module envelope."""

    resolved = resolve_proposed_layout_mode(mode)
    if resolved is ProposedLayoutMode.STANDALONE_MODULES:
        return (
            STANDALONE_MECHANICAL_ENVELOPE_ID,
            STANDALONE_MODULE_FOOTPRINT_X_M,
            STANDALONE_MODULE_FOOTPRINT_Y_M,
        )
    return (
        HISTORICAL_MECHANICAL_ENVELOPE_ID,
        HISTORICAL_MODULE_FOOTPRINT_X_M,
        HISTORICAL_MODULE_FOOTPRINT_Y_M,
    )


def _positive(name: str, value: float) -> float:
    number = _number(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be finite and positive.")
    return number


def _non_negative(name: str, value: float) -> float:
    number = _number(name, value)
    if number < 0.0:
        raise ValueError(f"{name} must be finite and non-negative.")
    return number


def _number(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number.")
    return number
