from __future__ import annotations

from pathlib import Path

import pytest

from fspm_optics.geometry.room import RoomDimensions
from fspm_optics.geometry.sensor_grid import (
    AdaptiveSensorGridPolicy,
    SensorGridSpec,
    build_adaptive_sensor_grid,
    format_rtrace_receivers,
    generate_sensor_points,
    write_sensor_grid,
)


def test_default_adaptive_square_grid_is_deterministic() -> None:
    room = RoomDimensions.from_feet(length_ft=10, width_ft=10, height_ft=10)
    first = build_adaptive_sensor_grid(room)
    second = build_adaptive_sensor_grid(room)
    assert first == second
    assert first.spec.resolution_x == first.spec.resolution_y == 21
    assert first.point_count == 441
    assert first.base_resolution_x == first.base_resolution_y == 12
    assert first.minimum_floor_enforced_x is True
    assert first.minimum_floor_enforced_y is True


def test_adaptive_rectangular_grid_aligns_long_axis_and_preserves_spacing_policy() -> None:
    room = RoomDimensions.from_feet(length_ft=10, width_ft=20, height_ft=10)
    grid = build_adaptive_sensor_grid(room)
    assert grid.axes_swapped is True
    assert grid.spec.room.length_m == pytest.approx(20 * 0.3048)
    assert grid.spec.room.width_m == pytest.approx(10 * 0.3048)
    assert grid.base_resolution_x == grid.spec.resolution_x == 24
    assert grid.base_resolution_y == 12
    assert grid.spec.resolution_y == 21
    assert grid.point_count == 24 * 21


def test_adaptive_policy_caps_are_applied_after_minimum_floor() -> None:
    room = RoomDimensions.from_feet(length_ft=10, width_ft=10, height_ft=10)
    grid = build_adaptive_sensor_grid(
        room,
        policy=AdaptiveSensorGridPolicy(max_points_x=15, max_points_y=16),
    )
    assert (grid.spec.resolution_x, grid.spec.resolution_y) == (15, 16)


def test_centered_sensor_count_and_coordinates_are_deterministic() -> None:
    room = RoomDimensions.from_feet(length_ft=4, width_ft=2, height_ft=10)
    spec = SensorGridSpec(
        room=room,
        resolution_x=3,
        resolution_y=3,
        canopy_height_m=0.25,
    )
    first = generate_sensor_points(spec)
    second = generate_sensor_points(spec)
    assert first == second
    assert len(first) == spec.point_count == 9
    assert [point.x_m for point in first[:3]] == pytest.approx([-0.4064, 0.0, 0.4064])
    assert [first[index].y_m for index in (0, 3, 6)] == pytest.approx(
        [-0.2032, 0.0, 0.2032]
    )
    assert all(point.z_m == 0.25 for point in first)


def test_rtrace_rows_have_six_fields_and_upward_normals() -> None:
    spec = SensorGridSpec(
        room=RoomDimensions(2.0, 1.0, 2.5),
        resolution_x=2,
        resolution_y=2,
        canopy_height_m=0.4,
    )
    points = generate_sensor_points(spec)
    lines = format_rtrace_receivers(points).splitlines()
    assert len(lines) == 4
    assert all(len(line.split()) == 6 for line in lines)
    assert all(tuple(float(value) for value in line.split()[-3:]) == (0.0, 0.0, 1.0) for line in lines)
    assert all((point.dx, point.dy, point.dz) == (0.0, 0.0, 1.0) for point in points)


def test_edge_aligned_grid_uses_room_inset_boundaries() -> None:
    spec = SensorGridSpec(
        room=RoomDimensions(4.0, 2.0, 3.0),
        resolution_x=3,
        resolution_y=2,
        canopy_height_m=0.5,
        inset_m=0.25,
        layout="edge_aligned",
    )
    points = generate_sensor_points(spec)
    assert [point.x_m for point in points[:3]] == [-1.75, 0.0, 1.75]
    assert sorted({point.y_m for point in points}) == [-0.75, 0.75]


def test_sensor_grid_writer_matches_pure_formatter(tmp_path: Path) -> None:
    points = generate_sensor_points(
        SensorGridSpec(RoomDimensions(2.0, 2.0, 2.0), 2, 1, 0.3)
    )
    output = tmp_path / "receivers.txt"
    assert write_sensor_grid(output, points) == output
    assert output.read_text(encoding="utf-8") == format_rtrace_receivers(points)
