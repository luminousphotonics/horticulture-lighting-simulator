"""Canonical mounting-height conversion and scientific provenance."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math

from .room import DEFAULT_ROOM_HEIGHT_M
from .sensor_grid import BASELINE_REFERENCE_PLANE_Z_M

INCH_TO_METERS = 0.0254
_INCH_TO_METERS_DECIMAL = Decimal("0.0254")
MOUNTING_HEIGHT_DEFINITION = (
    "emitting_aperture_plane_to_receiver_reference_plane"
)
MOUNTING_HEIGHT_SCHEMA_ID = "fspm-optics.mounting-height-provenance"
MOUNTING_HEIGHT_SCHEMA_VERSION = 1


def canonical_inches_to_meters(value: object) -> float:
    """Convert a finite numeric inch value through exact decimal arithmetic."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("inches must be a finite number.")
    inches = float(value)
    if not math.isfinite(inches):
        raise ValueError("inches must be a finite number.")
    return float(Decimal(str(inches)) * _INCH_TO_METERS_DECIMAL)


@dataclass(frozen=True, slots=True)
class MountingGeometry:
    """One resolved inch input and its authoritative physical planes."""

    mounting_height_in: float
    input_unit: str
    meters_per_inch: float
    definition: str
    reference_plane_z_m: float
    mounting_height_m: float
    emitting_aperture_plane_z_m: float
    room_ceiling_z_m: float

    @classmethod
    def resolve(cls, mounting_height_in: object) -> "MountingGeometry":
        if (
            isinstance(mounting_height_in, bool)
            or not isinstance(mounting_height_in, int | float)
        ):
            raise ValueError("mounting_height_in must be a finite positive number.")
        height_in = float(mounting_height_in)
        if not math.isfinite(height_in) or height_in <= 0.0:
            raise ValueError("mounting_height_in must be a finite positive number.")
        mounting_height_m = canonical_inches_to_meters(height_in)
        aperture_decimal_z_m = (
            Decimal(str(BASELINE_REFERENCE_PLANE_Z_M))
            + Decimal(str(mounting_height_m))
        )
        if aperture_decimal_z_m >= Decimal(str(DEFAULT_ROOM_HEIGHT_M)):
            raise ValueError(
                "the emitting-aperture plane must lie below the fixed room ceiling."
            )
        aperture_z_m = float(aperture_decimal_z_m)
        return cls(
            mounting_height_in=height_in,
            input_unit="inch",
            meters_per_inch=INCH_TO_METERS,
            definition=MOUNTING_HEIGHT_DEFINITION,
            reference_plane_z_m=BASELINE_REFERENCE_PLANE_Z_M,
            mounting_height_m=mounting_height_m,
            emitting_aperture_plane_z_m=aperture_z_m,
            room_ceiling_z_m=DEFAULT_ROOM_HEIGHT_M,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_id": MOUNTING_HEIGHT_SCHEMA_ID,
            "schema_version": MOUNTING_HEIGHT_SCHEMA_VERSION,
            "mounting_height_in": self.mounting_height_in,
            "input_unit": self.input_unit,
            "meters_per_inch": self.meters_per_inch,
            "definition": self.definition,
            "reference_plane_z_m": self.reference_plane_z_m,
            "mounting_height_m": self.mounting_height_m,
            "emitting_aperture_plane_z_m": self.emitting_aperture_plane_z_m,
            "room_ceiling_z_m": self.room_ceiling_z_m,
        }


__all__ = [
    "INCH_TO_METERS",
    "MOUNTING_HEIGHT_DEFINITION",
    "MOUNTING_HEIGHT_SCHEMA_ID",
    "MOUNTING_HEIGHT_SCHEMA_VERSION",
    "MountingGeometry",
    "canonical_inches_to_meters",
]
