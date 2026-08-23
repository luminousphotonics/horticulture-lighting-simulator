from __future__ import annotations

from pathlib import Path

from fspm_optics.fixtures.conventional_led import (
    build_conventional_radiance_source_plan,
    plan_conventional_layout_from_feet,
)
from fspm_optics.transport.conventional_scenes import (
    plan_conventional_scene_bundle,
)


def _source_plan():
    layout = plan_conventional_layout_from_feet(10, 10)
    return build_conventional_radiance_source_plan(layout, workspace="runtime")


def test_baseline_and_fspm_scene_composition_remain_explicit_and_distinct() -> None:
    source = _source_plan()
    bundle = plan_conventional_scene_bundle(
        source,
        room="room.rad",
        room_identity="room-v1",
        shared_angular_source="conventional_shared.rad",
        fixture_apertures="conventional_apertures.rad",
        fixture_bodies="fixture_bodies.rad",
        horizontal_sensor_grid="baseline_sensor_grid.pts",
        sensor_grid_identity="sensor-grid-v1",
        rex_plant_geometry="rex_plants.rad",
        rex_plant_geometry_identity="rex-plants-v1",
        rex_receiver_input="rex_receivers.pts",
        rex_receiver_identity="rex-receivers-v1",
    )

    assert bundle.baseline.scene.source_files == (
        Path("room.rad"),
        Path("conventional_shared.rad"),
        Path("conventional_apertures.rad"),
        Path("fixture_bodies.rad"),
    )
    assert bundle.baseline.receiver_input_path == Path("baseline_sensor_grid.pts")
    assert bundle.baseline.receiver_kind == "horizontal_sensor_grid"
    assert Path("rex_plants.rad") not in bundle.baseline.scene.source_files
    assert source.ies2rad.expected_paths.converted_rad not in (
        bundle.baseline.scene.source_files
    )
    assert bundle.fspm.scene.source_files == (
        Path("room.rad"),
        Path("conventional_shared.rad"),
        Path("conventional_apertures.rad"),
        Path("fixture_bodies.rad"),
        Path("rex_plants.rad"),
    )
    assert bundle.fspm.receiver_input_path == Path("rex_receivers.pts")
    assert bundle.fspm.receiver_kind == "rex_plant_receivers"
    assert source.ies2rad.expected_paths.converted_rad not in bundle.fspm.scene.source_files
    assert bundle.baseline.scene_id != bundle.fspm.scene_id
    assert bundle.baseline.future_octree_identity != bundle.fspm.future_octree_identity
    assert (
        bundle.baseline.future_ambient_cache_identity
        != bundle.fspm.future_ambient_cache_identity
    )


def test_source_physics_changes_scene_and_future_runtime_identities() -> None:
    practical = build_conventional_radiance_source_plan(
        plan_conventional_layout_from_feet(12, 10), workspace="runtime"
    )
    full_fit = build_conventional_radiance_source_plan(
        plan_conventional_layout_from_feet(12, 10, policy="full_fit"),
        workspace="runtime",
    )

    def baseline_id(source):
        return plan_conventional_scene_bundle(
            source,
            room="room.rad",
            room_identity="room-v1",
            shared_angular_source="shared.rad",
            fixture_apertures="apertures.rad",
            fixture_bodies="fixture_bodies.rad",
            horizontal_sensor_grid="grid.pts",
            sensor_grid_identity="grid-v1",
            rex_plant_geometry="plants.rad",
            rex_plant_geometry_identity="plants-v1",
            rex_receiver_input="receivers.pts",
            rex_receiver_identity="receivers-v1",
        ).baseline

    first = baseline_id(practical)
    second = baseline_id(full_fit)
    assert first.source_plan_id != second.source_plan_id
    assert first.scene_id != second.scene_id
    assert first.future_octree_identity != second.future_octree_identity
    assert first.future_ambient_cache_identity != second.future_ambient_cache_identity


def test_scene_planning_is_pure_and_does_not_require_runtime_files() -> None:
    bundle = plan_conventional_scene_bundle(
        _source_plan(),
        room="missing room.rad",
        room_identity="room-v1",
        shared_angular_source="missing shared.rad",
        fixture_apertures="missing apertures.rad",
        fixture_bodies="missing fixture bodies.rad",
        horizontal_sensor_grid="missing grid.pts",
        sensor_grid_identity="grid-v1",
        rex_plant_geometry="missing plants.rad",
        rex_plant_geometry_identity="plants-v1",
        rex_receiver_input="missing receivers.pts",
        rex_receiver_identity="receivers-v1",
    )

    assert bundle.baseline.scene.rebuild_from_sources is True
    assert bundle.fspm.scene.rebuild_from_sources is True
    assert not Path("missing room.rad").exists()
