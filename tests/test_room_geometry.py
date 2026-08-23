from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from fspm_optics.geometry.room import (
    FEET_TO_METERS,
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    PRODUCTION_ROOM_SURFACE_MATERIALS,
    RoomDimensions,
    feet_to_meters,
    production_room_model_payload,
    room_radiance_text,
    validate_production_room_radiance_text,
    write_room_radiance,
)
from fspm_optics.radiance.materials import (
    PRODUCTION_ROOM_FLOOR,
    PRODUCTION_ROOM_WALL_CEILING,
)


def test_feet_conversion_is_explicit() -> None:
    assert feet_to_meters(12) == pytest.approx(3.6576)
    room = RoomDimensions.from_feet(length_ft=24, width_ft=12, height_ft=10)
    assert room.length_m == pytest.approx(24 * FEET_TO_METERS)
    assert room.width_m == pytest.approx(12 * FEET_TO_METERS)
    assert room.height_m == pytest.approx(10 * FEET_TO_METERS)


def test_room_text_contains_floor_ceiling_and_four_walls() -> None:
    room = RoomDimensions(4.0, 2.0, 3.0)
    text = room_radiance_text(room)
    assert PRODUCTION_ROOM_WALL_CEILING.to_radiance() in text
    assert PRODUCTION_ROOM_FLOOR.to_radiance() in text
    assert f"{PRODUCTION_ROOM_FLOOR.name} polygon floor" in text
    assert f"{PRODUCTION_ROOM_WALL_CEILING.name} polygon ceiling" in text
    for name in ("wall_neg_x", "wall_pos_x", "wall_neg_y", "wall_pos_y"):
        assert f"{PRODUCTION_ROOM_WALL_CEILING.name} polygon {name}" in text
    assert text.count(f"{PRODUCTION_ROOM_FLOOR.name} polygon ") == 1
    assert text.count(f"{PRODUCTION_ROOM_WALL_CEILING.name} polygon ") == 5
    assert text.count(" polygon ") == 6
    assert "-2.000000 -1.000000 0.000000" in text
    assert "2.000000 1.000000 3.000000" in text


def test_room_text_generation_is_deterministic_and_writer_is_thin(tmp_path: Path) -> None:
    room = RoomDimensions(3.0, 2.0, 2.5)
    first = room_radiance_text(room)
    assert first == room_radiance_text(room)
    output = tmp_path / "room.rad"
    assert write_room_radiance(output, room) == output
    assert output.read_text(encoding="utf-8") == first


def test_room_generator_has_no_material_profile_or_definition_switch() -> None:
    assert tuple(inspect.signature(room_radiance_text).parameters) == ("room",)
    assert tuple(inspect.signature(write_room_radiance).parameters) == ("path", "room")


def test_room_model_payload_authenticates_materials_and_assignments() -> None:
    payload = production_room_model_payload()
    assert payload["closed_surface_count"] == 6
    assert payload["surface_materials"] == PRODUCTION_ROOM_SURFACE_MATERIALS
    assert payload["materials"]["wall_ceiling"]["radiance_text"] == (
        PRODUCTION_ROOM_WALL_CEILING.to_radiance()
    )
    assert payload["materials"]["floor"]["radiance_text"] == (
        PRODUCTION_ROOM_FLOOR.to_radiance()
    )
    assert len(PRODUCTION_ROOM_MODEL_IDENTITY_SHA256) == 64


def test_room_authority_validator_rejects_any_alternate_material_profile() -> None:
    with pytest.raises(ValueError, match="material definition"):
        validate_production_room_radiance_text(
            "void plastic alternate_room\n0\n0\n5 0.8 0.8 0.8 0 0\n"
        )


@pytest.mark.parametrize(
    "dimensions",
    [(0.0, 2.0, 3.0), (2.0, -1.0, 3.0), (2.0, 2.0, float("inf"))],
)
def test_room_dimensions_must_be_positive_and_finite(
    dimensions: tuple[float, float, float],
) -> None:
    with pytest.raises(ValueError):
        RoomDimensions(*dimensions)
