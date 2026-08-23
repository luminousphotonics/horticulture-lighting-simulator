"""Simple deterministic rectangular grow-room geometry."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Final

from fspm_optics.radiance.materials import (
    PRODUCTION_ROOM_FLOOR,
    PRODUCTION_ROOM_WALL_CEILING,
    RadiancePlasticMaterial,
)

FEET_TO_METERS = 0.3048
DEFAULT_ROOM_HEIGHT_M = 10.0 * FEET_TO_METERS
PRODUCTION_ROOM_MODEL_SCHEMA_ID: Final = (
    "fspm-optics.production-radiance-room-model"
)
PRODUCTION_ROOM_MODEL_SCHEMA_VERSION: Final = 1
PRODUCTION_ROOM_MODEL_ID: Final = "closed_six_surface_neutral_diffuse_v1"
PRODUCTION_ROOM_SURFACE_MATERIALS: Final = {
    "floor": PRODUCTION_ROOM_FLOOR.name,
    "ceiling": PRODUCTION_ROOM_WALL_CEILING.name,
    "wall_neg_x": PRODUCTION_ROOM_WALL_CEILING.name,
    "wall_pos_x": PRODUCTION_ROOM_WALL_CEILING.name,
    "wall_neg_y": PRODUCTION_ROOM_WALL_CEILING.name,
    "wall_pos_y": PRODUCTION_ROOM_WALL_CEILING.name,
}


def production_room_model_payload() -> dict[str, Any]:
    """Return the versioned, path-free authority for every production room.

    The authority fixes a closed six-surface topology and the exact two
    non-emitting neutral diffuse plastics. Room dimensions remain scene inputs
    and therefore are deliberately outside this model identity.
    """

    def material_payload(
        material: RadiancePlasticMaterial,
    ) -> dict[str, object]:
        return {
            "name": material.name,
            "red_reflectance": material.red_reflectance,
            "green_reflectance": material.green_reflectance,
            "blue_reflectance": material.blue_reflectance,
            "specularity": material.specularity,
            "roughness": material.roughness,
            "radiance_text": material.to_radiance(),
        }

    return {
        "schema_id": PRODUCTION_ROOM_MODEL_SCHEMA_ID,
        "schema_version": PRODUCTION_ROOM_MODEL_SCHEMA_VERSION,
        "model_id": PRODUCTION_ROOM_MODEL_ID,
        "closed_surface_count": 6,
        "surface_materials": dict(PRODUCTION_ROOM_SURFACE_MATERIALS),
        "materials": {
            "wall_ceiling": material_payload(PRODUCTION_ROOM_WALL_CEILING),
            "floor": material_payload(PRODUCTION_ROOM_FLOOR),
        },
        "emitting": False,
        "occluding": True,
    }


def production_room_model_identity_sha256() -> str:
    """Return the canonical cache/manifest identity of the room authority."""

    encoded = json.dumps(
        production_room_model_payload(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


PRODUCTION_ROOM_MODEL_IDENTITY_SHA256: Final = (
    production_room_model_identity_sha256()
)


def feet_to_meters(value_ft: float | int) -> float:
    """Convert an explicitly feet-valued length to meters."""

    value = _positive_finite("value_ft", value_ft)
    return value * FEET_TO_METERS


@dataclass(frozen=True, slots=True)
class RoomDimensions:
    """Interior room dimensions in meters, centered on x=y=0."""

    length_m: float
    width_m: float
    height_m: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "length_m", _positive_finite("length_m", self.length_m))
        object.__setattr__(self, "width_m", _positive_finite("width_m", self.width_m))
        object.__setattr__(self, "height_m", _positive_finite("height_m", self.height_m))

    @classmethod
    def from_feet(
        cls,
        *,
        length_ft: float | int,
        width_ft: float | int,
        height_ft: float | int,
    ) -> "RoomDimensions":
        return cls(
            length_m=feet_to_meters(length_ft),
            width_m=feet_to_meters(width_ft),
            height_m=feet_to_meters(height_ft),
        )

    @property
    def half_length_m(self) -> float:
        return self.length_m / 2.0

    @property
    def half_width_m(self) -> float:
        return self.width_m / 2.0


def room_radiance_text(room: RoomDimensions) -> str:
    """Return the sole production Radiance room: closed and self-contained."""

    hx, hy, height = room.half_length_m, room.half_width_m, room.height_m

    def polygon(name: str, modifier: str, vertices: tuple[tuple[float, float, float], ...]) -> str:
        lines = [f"{modifier} polygon {name}", "0", "0", str(len(vertices) * 3)]
        lines.extend(f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in vertices)
        return "\n".join(lines) + "\n"

    wall_modifier = PRODUCTION_ROOM_WALL_CEILING.name
    floor_modifier = PRODUCTION_ROOM_FLOOR.name
    sections: list[str] = [
        PRODUCTION_ROOM_WALL_CEILING.to_radiance(),
        PRODUCTION_ROOM_FLOOR.to_radiance(),
    ]
    sections.extend([
        polygon(
            "floor",
            floor_modifier,
            ((-hx, -hy, 0.0), (hx, -hy, 0.0), (hx, hy, 0.0), (-hx, hy, 0.0)),
        ),
        polygon(
            "ceiling",
            wall_modifier,
            ((-hx, -hy, height), (-hx, hy, height), (hx, hy, height), (hx, -hy, height)),
        ),
        polygon(
            "wall_neg_x",
            wall_modifier,
            ((-hx, -hy, 0.0), (-hx, hy, 0.0), (-hx, hy, height), (-hx, -hy, height)),
        ),
        polygon(
            "wall_pos_x",
            wall_modifier,
            ((hx, -hy, 0.0), (hx, -hy, height), (hx, hy, height), (hx, hy, 0.0)),
        ),
        polygon(
            "wall_neg_y",
            wall_modifier,
            ((-hx, -hy, 0.0), (-hx, -hy, height), (hx, -hy, height), (hx, -hy, 0.0)),
        ),
        polygon(
            "wall_pos_y",
            wall_modifier,
            ((-hx, hy, 0.0), (hx, hy, 0.0), (hx, hy, height), (-hx, hy, height)),
        ),
    ])
    return "\n".join(section.rstrip() for section in sections) + "\n"


def validate_production_room_radiance_text(text: str) -> str:
    """Reject room text that is not bound to the production material policy."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("production room Radiance text must be non-empty.")
    for material in (PRODUCTION_ROOM_WALL_CEILING, PRODUCTION_ROOM_FLOOR):
        if text.count(material.to_radiance().strip()) != 1:
            raise ValueError(
                "production room Radiance text has an invalid material definition."
            )
    assignments: dict[str, str] = {}
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) == 3 and tokens[1] == "polygon":
            modifier, _, surface = tokens
            if surface in assignments:
                raise ValueError("production room contains a duplicate surface.")
            assignments[surface] = modifier
    if assignments != PRODUCTION_ROOM_SURFACE_MATERIALS:
        raise ValueError(
            "production room must contain the authoritative closed six-surface binding."
        )
    return text


def write_room_radiance(
    path: str | Path,
    room: RoomDimensions,
) -> Path:
    output = Path(path)
    output.write_text(
        validate_production_room_radiance_text(room_radiance_text(room)),
        encoding="utf-8",
    )
    return output


def _positive_finite(name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive.")
    return number
