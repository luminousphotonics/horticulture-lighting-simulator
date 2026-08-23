"""Small, documented registry of authoritative Radiance surface materials.

The RGB values here are Radiance ``plastic`` coefficients, not a sampled
spectral reflectance curve.  Spectral CSV-backed materials can be added later
without changing the room geometry API.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Final, Mapping

from fspm_optics.transport.scalar_ppfd import (
    BASELINE_PPFD_RGB_EQUALITY_ABS_TOL,
    BASELINE_PPFD_RGB_EQUALITY_REL_TOL,
)

_RADIANCE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def validate_radiance_identifier(name: str) -> str:
    """Return *name* when it is a conservative Radiance-safe identifier."""

    if not isinstance(name, str) or not _RADIANCE_IDENTIFIER.fullmatch(name):
        raise ValueError(
            "material name must start with a letter or underscore and contain "
            "only letters, digits, underscores, periods, or hyphens."
        )
    return name


def _unit_interval(name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number between zero and one.")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be between zero and one.")
    return number


@dataclass(frozen=True, slots=True)
class RadiancePlasticMaterial:
    """Scalar RGB coefficients for a Radiance ``plastic`` modifier.

    Reflectance channels, specularity, and roughness are dimensionless values
    in the inclusive interval from zero to one.
    """

    name: str
    red_reflectance: float
    green_reflectance: float
    blue_reflectance: float
    specularity: float
    roughness: float
    provenance: str
    notes: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", validate_radiance_identifier(self.name))
        for field_name in (
            "red_reflectance",
            "green_reflectance",
            "blue_reflectance",
            "specularity",
            "roughness",
        ):
            object.__setattr__(
                self,
                field_name,
                _unit_interval(field_name, getattr(self, field_name)),
            )
        if not isinstance(self.provenance, str):
            raise ValueError("provenance must be a string.")
        if not isinstance(self.notes, str):
            raise ValueError("notes must be a string.")

    def to_radiance(self) -> str:
        """Format this material as deterministic Radiance source text."""

        return (
            f"void plastic {self.name}\n"
            "0\n"
            "0\n"
            f"5 {self.red_reflectance:.4f} {self.green_reflectance:.4f} "
            f"{self.blue_reflectance:.4f} {self.specularity:.4f} "
            f"{self.roughness:.4f}\n"
        )


PRODUCTION_ROOM_WALL_CEILING: Final = RadiancePlasticMaterial(
    name="room_wall_ceiling_neutral_diffuse_v1",
    red_reflectance=0.9000,
    green_reflectance=0.9000,
    blue_reflectance=0.9000,
    specularity=0.0000,
    roughness=0.0000,
    provenance="production Radiance room authority v1",
    notes="Neutral diffuse material assigned only to four walls and the ceiling.",
)

PRODUCTION_ROOM_FLOOR: Final = RadiancePlasticMaterial(
    name="room_floor_neutral_diffuse_v1",
    red_reflectance=0.1000,
    green_reflectance=0.1000,
    blue_reflectance=0.1000,
    specularity=0.0000,
    roughness=0.0000,
    provenance="production Radiance room authority v1",
    notes="Neutral diffuse material assigned only to the floor.",
)

REX_LEAF_SCALAR_TRANSPORT_PLACEHOLDER: Final = RadiancePlasticMaterial(
    name="rex_leaf_scalar_transport_placeholder",
    red_reflectance=0.2500,
    green_reflectance=0.2500,
    blue_reflectance=0.2500,
    specularity=0.0000,
    roughness=0.2000,
    provenance="geometry-only neutral scalar plant receiver transport placeholder",
    notes=(
        "Opaque equal-channel plastic used only to validate scalar receiver "
        "transport plumbing. It is not a measured or modeled Rex leaf "
        "reflectance/transmittance/absorptance material."
    ),
)

RADIANCE_PLASTIC_MATERIALS: Final[Mapping[str, RadiancePlasticMaterial]] = {
    PRODUCTION_ROOM_WALL_CEILING.name: PRODUCTION_ROOM_WALL_CEILING,
    PRODUCTION_ROOM_FLOOR.name: PRODUCTION_ROOM_FLOOR,
    REX_LEAF_SCALAR_TRANSPORT_PLACEHOLDER.name: (
        REX_LEAF_SCALAR_TRANSPORT_PLACEHOLDER
    ),
}


def get_plastic_material(name: str) -> RadiancePlasticMaterial:
    """Return a registered material, with a clear error for unknown names."""

    safe_name = validate_radiance_identifier(name)
    try:
        return RADIANCE_PLASTIC_MATERIALS[safe_name]
    except KeyError as exc:
        raise KeyError(f"unknown Radiance plastic material: {safe_name}") from exc


def validate_scalar_par_surface_material(material: object) -> object:
    """Validate equal-channel plastic reflectance for scalar PAR workspaces.

    Only :class:`RadiancePlasticMaterial` reflectance definitions are in scope.
    Other domain objects, including emitter documents whose Radiance text uses
    ``light`` source primitives, are returned unchanged and are not interpreted
    as surface reflectance.
    """

    if not isinstance(material, RadiancePlasticMaterial):
        return material
    red = material.red_reflectance
    green = material.green_reflectance
    blue = material.blue_reflectance
    if not (
        math.isclose(
            red,
            green,
            rel_tol=BASELINE_PPFD_RGB_EQUALITY_REL_TOL,
            abs_tol=BASELINE_PPFD_RGB_EQUALITY_ABS_TOL,
        )
        and math.isclose(
            red,
            blue,
            rel_tol=BASELINE_PPFD_RGB_EQUALITY_REL_TOL,
            abs_tol=BASELINE_PPFD_RGB_EQUALITY_ABS_TOL,
        )
    ):
        raise ValueError(
            "Scalar PAR room/surface plastic materials must use equal RGB "
            f"reflectance channels; {material.name!r} has "
            f"R={red:.12g}, G={green:.12g}, B={blue:.12g}."
        )
    return material
