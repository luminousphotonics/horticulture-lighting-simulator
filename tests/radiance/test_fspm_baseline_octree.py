from __future__ import annotations

from pathlib import Path

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.cli.scripts import (  # noqa: E402
    _fspm_receiver_scene_inputs,
    _octree_scene_inputs,
)


def test_baseline_ppfd_octree_excludes_plant_geometry_from_dynamic_scene(tmp_path: Path) -> None:
    room = tmp_path / "room.rad"
    emitters = tmp_path / "emitters.rad"
    plants = tmp_path / "plants.rad"

    inputs = _octree_scene_inputs(
        room=room,
        emitter_file=emitters,
        plant_rad=plants,
    )

    assert str(room) in inputs
    assert str(emitters) in inputs
    assert str(plants) not in inputs


def test_baseline_ppfd_octree_excludes_plant_geometry_from_static_scene(tmp_path: Path) -> None:
    room = tmp_path / "room.rad"
    emitters = tmp_path / "emitters.rad"
    plants = tmp_path / "plants.rad"
    static_octree = tmp_path / "static_room.oct"
    static_octree.write_text("placeholder", encoding="utf-8")

    inputs = _octree_scene_inputs(
        room=room,
        emitter_file=emitters,
        plant_rad=plants,
        static_room_oct=static_octree,
    )

    assert "-i" in inputs
    assert str(static_octree) in inputs
    assert str(emitters) in inputs
    assert str(room) not in inputs
    assert str(plants) not in inputs


def test_plant_inclusive_fspm_receiver_octree_does_not_reuse_static_room_bounds(
    tmp_path: Path,
) -> None:
    room = tmp_path / "room.rad"
    emitters = tmp_path / "emitters.rad"
    plants = tmp_path / "plants_fspm_receiver_material.rad"
    static_octree = tmp_path / "static_room.oct"
    static_octree.write_text("placeholder", encoding="utf-8")

    inputs = _fspm_receiver_scene_inputs(
        room=room,
        emitter_file=emitters,
        plant_rad=plants,
        static_room_oct=static_octree,
    )

    assert "-i" not in inputs
    assert str(static_octree) not in inputs
    assert inputs == ["-f", str(room), str(emitters), str(plants)]
