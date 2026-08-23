from __future__ import annotations

from pathlib import Path

import pytest

from fspm_optics.transport.scenes import (
    plan_baseline_ppfd_scene,
    plan_fspm_receiver_scene,
)


def _scene_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    room = tmp_path / "room.rad"
    emitters = tmp_path / "emitters.rad"
    plants = tmp_path / "plants.rad"
    for path in (room, emitters, plants):
        path.write_text(f"# {path.name}\n", encoding="utf-8")
    return room, emitters, plants


def test_baseline_scene_contains_room_and_emitters_only(tmp_path: Path) -> None:
    room, emitters, plants = _scene_files(tmp_path)
    plan = plan_baseline_ppfd_scene(room=room, emitters=emitters)
    assert plan.source_files == (room, emitters)
    assert plants not in plan.source_files
    assert plan.role == "baseline_ppfd"
    assert plan.rebuild_from_sources is True


def test_receiver_scene_contains_room_emitters_and_plants(tmp_path: Path) -> None:
    room, emitters, plants = _scene_files(tmp_path)
    plan = plan_fspm_receiver_scene(
        room=room,
        emitters=emitters,
        plant_geometry=plants,
    )
    assert plan.source_files == (room, emitters, plants)
    assert plan.role == "fspm_receiver"


def test_receiver_octree_is_rebuilt_from_full_sources(tmp_path: Path) -> None:
    room, emitters, plants = _scene_files(tmp_path)
    plan = plan_fspm_receiver_scene(
        room=room,
        emitters=emitters,
        plant_geometry=plants,
    )
    command = plan.compilation_command(tmp_path / "receiver.oct")
    assert command.argv == (
        "oconv",
        "-f",
        str(room),
        str(emitters),
        str(plants),
    )
    assert "-i" not in command.argv


def test_receiver_scene_rejects_missing_plant_geometry(tmp_path: Path) -> None:
    room, emitters, _plants = _scene_files(tmp_path)
    missing = tmp_path / "missing_plants.rad"
    with pytest.raises(FileNotFoundError, match="FSPM plant geometry not found"):
        plan_fspm_receiver_scene(
            room=room,
            emitters=emitters,
            plant_geometry=missing,
        )


def test_scene_planning_rejects_missing_room_or_emitters(tmp_path: Path) -> None:
    room, emitters, _plants = _scene_files(tmp_path)
    with pytest.raises(FileNotFoundError, match="Room geometry not found"):
        plan_baseline_ppfd_scene(room=tmp_path / "missing.rad", emitters=emitters)
    with pytest.raises(FileNotFoundError, match="Emitter geometry not found"):
        plan_baseline_ppfd_scene(room=room, emitters=tmp_path / "missing.rad")
