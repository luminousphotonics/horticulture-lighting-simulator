from __future__ import annotations

from pathlib import Path

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.cli.scripts import _octree_scene_inputs  # noqa: E402


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
