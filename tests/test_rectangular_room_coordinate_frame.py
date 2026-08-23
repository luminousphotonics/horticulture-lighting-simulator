from __future__ import annotations

import hashlib
import json
import math

import pytest

from fspm_optics.application.baseline_leaf_uniformity import (
    build_baseline_physical_leaf_scene,
    leaf_representative_positions,
)
from fspm_optics.fixtures.conventional_led import (
    plan_conventional_layout_from_feet,
)
from fspm_optics.fixtures.hps import plan_hps_layout_from_feet
from fspm_optics.fixtures.smd.config import SmdLayoutConfig
from fspm_optics.fixtures.smd.positions import generate_smd_layout
from fspm_optics.geometry.coordinate_frame import (
    ROOM_BOUNDS_TOLERANCE_M,
    RoomCoordinateFrame,
)
from fspm_optics.geometry.room import RoomDimensions
from fspm_optics.geometry.sensor_grid import (
    build_adaptive_sensor_grid,
    generate_sensor_points,
)
from fspm_optics.plants.multi_scene import (
    JuvenileScientificSceneError,
    build_juvenile_natural_fit_scene,
)
from fspm_optics.plants.natural_fit import (
    PlantLocalXYBoundsM,
    plan_natural_fit_layout_from_feet,
)
from fspm_optics.viewer.artifacts import build_run_viewer_artifacts


def _inside(frame: RoomCoordinateFrame, point: tuple[float, float, float]) -> bool:
    return frame.contains_simulation_position(
        point,
        tolerance_m=ROOM_BOUNDS_TOLERANCE_M,
    )


def test_square_frame_is_identity_and_rectangular_orientations_align_identically() -> None:
    square = RoomCoordinateFrame(10 * 0.3048, 10 * 0.3048)
    portrait = RoomCoordinateFrame(10 * 0.3048, 20 * 0.3048)
    landscape = RoomCoordinateFrame(20 * 0.3048, 10 * 0.3048)
    portrait_plan = plan_natural_fit_layout_from_feet(10, 20)
    landscape_plan = plan_natural_fit_layout_from_feet(20, 10)

    assert square.rotation_matrix_row_major == (1, 0, 0, 0, 1, 0, 0, 0, 1)
    assert square.requested_to_simulation_position((1.0, 2.0, 3.0)) == (
        1.0,
        2.0,
        3.0,
    )
    assert portrait.rotation_matrix_row_major == (0, 1, 0, -1, 0, 0, 0, 0, 1)
    assert portrait.to_payload()["determinant"] == 1
    assert portrait.simulation_bounds_to_requested((-2.0, 3.0, -1.0, 4.0)) == (
        -4.0,
        1.0,
        -2.0,
        3.0,
    )
    assert (
        portrait.simulation_length_m,
        portrait.simulation_width_m,
    ) == pytest.approx((20 * 0.3048, 10 * 0.3048))
    assert (
        landscape.simulation_length_m,
        landscape.simulation_width_m,
    ) == pytest.approx((20 * 0.3048, 10 * 0.3048))
    assert (
        portrait_plan.aligned_room_m,
        portrait_plan.total_count,
    ) == (landscape_plan.aligned_room_m, landscape_plan.total_count)
    assert portrait_plan.total_count == landscape_plan.total_count == 128

    for value in ((0.25, -0.75, 1.5), (-1.0, 2.0, -0.5)):
        simulation = portrait.requested_to_simulation_position(value)
        assert portrait.simulation_to_requested_position(simulation) == pytest.approx(
            value
        )
        direction = portrait.requested_to_simulation_direction(value)
        assert portrait.simulation_to_requested_direction(direction) == pytest.approx(
            value
        )


def test_all_stage_a_layouts_grid_and_stage_b_share_the_same_portrait_frame() -> None:
    frame = RoomCoordinateFrame(10 * 0.3048, 20 * 0.3048)
    proposed = generate_smd_layout(
        SmdLayoutConfig(room_length_ft=10, room_width_ft=20)
    )
    hps = plan_hps_layout_from_feet(10, 20)
    conventional = plan_conventional_layout_from_feet(10, 20)
    natural_fit = plan_natural_fit_layout_from_feet(10, 20)
    sensor_grid = build_adaptive_sensor_grid(
        RoomDimensions(10 * 0.3048, 20 * 0.3048, 10 * 0.3048)
    )

    expected = (frame.simulation_length_m, frame.simulation_width_m)
    assert (proposed.room_length_m, proposed.room_width_m) == pytest.approx(expected)
    assert (
        hps.room_axes.aligned.length_m,
        hps.room_axes.aligned.width_m,
    ) == pytest.approx(expected)
    assert (
        conventional.room_axes.aligned.length_m,
        conventional.room_axes.aligned.width_m,
    ) == pytest.approx(expected)
    assert (
        natural_fit.aligned_room_m.length_m,
        natural_fit.aligned_room_m.width_m,
    ) == pytest.approx(expected)
    assert (
        sensor_grid.spec.room.length_m,
        sensor_grid.spec.room.width_m,
    ) == pytest.approx(expected)
    assert all(_inside(frame, (point.x_m, point.y_m, point.z_m)) for point in (
        generate_sensor_points(sensor_grid.spec)
    ))
    assert all(_inside(frame, (module.x_m, module.y_m, module.z_m)) for module in proposed.modules)
    half_x, half_y = expected[0] / 2.0, expected[1] / 2.0
    assert all(
        -half_x <= fixture.footprint_bounds_m.aligned_min_x_m
        <= fixture.footprint_bounds_m.aligned_max_x_m <= half_x
        and -half_y <= fixture.footprint_bounds_m.aligned_min_y_m
        <= fixture.footprint_bounds_m.aligned_max_y_m <= half_y
        for fixture in (*hps.fixtures, *conventional.fixtures)
    )
    assert proposed.axes_swapped
    assert hps.room_axes.rotation_degrees_about_z == -90
    assert conventional.room_axes.rotation_degrees_about_z == -90
    assert natural_fit.coordinate_frame.rotation_degrees_about_z == -90


def test_portrait_stage_b_keeps_all_128_plants_vertices_and_receivers_in_room() -> None:
    plan = plan_natural_fit_layout_from_feet(10, 20)
    scene = build_juvenile_natural_fit_scene(plan)
    frame = plan.coordinate_frame

    assert plan.total_count == scene.counts.plant_count == 128
    assert scene.counts.receiver_count == 128 * 384
    assert len(tuple(scene.plants)) == 128
    assert all(_inside(frame, plant.origin_m) for plant in scene.plants)

    leaves = tuple(scene.iter_leaves())
    assert len(leaves) == 128 * 12
    assert all(_inside(frame, vertex) for leaf in leaves for vertex in leaf.vertices)

    receivers = tuple(scene.iter_receivers())
    assert len(receivers) == 128 * 384
    assert all(_inside(frame, receiver.point_m) for receiver in receivers)
    for front, back in zip(receivers[::2], receivers[1::2], strict=True):
        assert front.side == "front" and back.side == "back"
        assert math.sqrt(sum(value * value for value in front.normal)) == pytest.approx(1.0)
        assert math.sqrt(sum(value * value for value in back.normal)) == pytest.approx(1.0)
        assert sum(a * b for a, b in zip(front.normal, back.normal, strict=True)) == pytest.approx(-1.0)
        requested = frame.simulation_to_requested_direction(front.normal)
        assert frame.requested_to_simulation_direction(requested) == pytest.approx(
            front.normal
        )


def test_scene_construction_fails_closed_when_declared_bounds_hide_overflow() -> None:
    unsafe_plan = plan_natural_fit_layout_from_feet(
        10,
        20,
        plant_local_bounds_m=PlantLocalXYBoundsM(-0.001, 0.001, -0.001, 0.001),
    )
    with pytest.raises(JuvenileScientificSceneError, match="outside the aligned room"):
        build_juvenile_natural_fit_scene(unsafe_plan)


def test_stage_a_leaf_diagnostics_and_stage_b_receivers_use_aligned_axes() -> None:
    plan = plan_natural_fit_layout_from_feet(10, 20)
    physical = build_baseline_physical_leaf_scene(plan)
    diagnostics = leaf_representative_positions(physical)
    scene = build_juvenile_natural_fit_scene(plan)
    first = diagnostics[0]
    centroid = next(
        value
        for value in diagnostics
        if value.plant_index == 0 and value.local_leaf_index == 0
    )
    expected_requested = plan.coordinate_frame.simulation_to_requested_position(
        (centroid.aligned_x_m, centroid.aligned_y_m, 0.0)
    )

    assert first.aligned_x_m < -plan.aligned_room_m.width_m / 2.0
    assert (first.requested_x_m, first.requested_y_m) == pytest.approx(
        expected_requested[:2]
    )
    assert scene.plants[0].origin_m[:2] == pytest.approx(
        (plan.plants[0].aligned_x_m, plan.plants[0].aligned_y_m)
    )
    assert scene.receiver_at(0).point_m[0] < -plan.aligned_room_m.width_m / 2.0


def test_portrait_viewer_reports_requested_and_aligned_rooms_and_contains_crop() -> None:
    plan = plan_natural_fit_layout_from_feet(10, 20)
    artifact_sha = hashlib.sha256(plan.to_json().encode()).hexdigest()
    artifacts = build_run_viewer_artifacts(
        run_id="1" * 32,
        system_id="proposed",
        requested_length_ft=10,
        requested_width_ft=20,
        natural_fit=plan,
        natural_fit_artifact_sha256=artifact_sha,
        fixture_catalog_sha256="2" * 64,
        fixture_catalog_byte_length=1,
        fixture_authoritative_layout_sha256="3" * 64,
        fixture_plan_sha256="4" * 64,
        fixture_count=1,
        fixture_asset_group_count=1,
    )
    payload = json.loads(artifacts.scene_manifest.data)

    assert payload["requested_room"] == {
        "length_ft": 10,
        "length_m": pytest.approx(3.048),
        "width_ft": 20,
        "width_m": pytest.approx(6.096),
    }
    assert payload["aligned_simulation_room"]["length_m"] == pytest.approx(6.096)
    assert payload["aligned_simulation_room"]["width_m"] == pytest.approx(3.048)
    assert payload["aligned_simulation_room"]["coordinate_frame"]["determinant"] == 1
    plant_bounds = payload["projected_footprint_bounds"]
    room_bounds = payload["room_bounds"]
    assert all(
        room_bounds["minimum_xz"][axis] - ROOM_BOUNDS_TOLERANCE_M
        <= plant_bounds["minimum_xz"][axis]
        <= plant_bounds["maximum_xz"][axis]
        <= room_bounds["maximum_xz"][axis] + ROOM_BOUNDS_TOLERANCE_M
        for axis in (0, 1)
    )
