from __future__ import annotations

import json

import pytest

from fspm_optics.fixtures.hps import (
    HPS_CENTER_PITCH_M,
    HPS_LAYOUT_POLICY_ID,
    NoLegalHpsLayoutError,
    format_hps_layout_json,
    plan_hps_layout,
    plan_hps_layout_from_feet,
)


def _aligned_centers(plan) -> tuple[tuple[float, float], ...]:
    return tuple((item.aligned_x_m, item.aligned_y_m) for item in plan.fixtures)


def test_hps_10x10_fixed_pitch_layout_regression() -> None:
    plan = plan_hps_layout_from_feet(10.0, 10.0)

    assert plan.policy_id == HPS_LAYOUT_POLICY_ID
    assert plan.room_axes.axes_swapped is False
    assert (plan.counts.columns_x, plan.counts.rows_y, plan.counts.total) == (2, 2, 4)
    assert (plan.pitch_x_m, plan.pitch_y_m) == (
        HPS_CENTER_PITCH_M,
        HPS_CENTER_PITCH_M,
    )
    assert _aligned_centers(plan) == (
        (-0.6096, -0.6096),
        (0.6096, -0.6096),
        (-0.6096, 0.6096),
        (0.6096, 0.6096),
    )
    assert plan.actual_wall_gap_x_m / 0.0254 == pytest.approx(20.28, abs=1e-12)
    assert plan.actual_wall_gap_y_m / 0.0254 == pytest.approx(24.12, abs=1e-12)
    assert tuple(
        (item.grid_index.row_y, item.grid_index.column_x) for item in plan.fixtures
    ) == ((0, 0), (0, 1), (1, 0), (1, 1))


def test_rectangular_layout_aligns_long_axis_and_keeps_fixed_counts_and_pitch() -> None:
    plan = plan_hps_layout_from_feet(8.0, 12.0)

    assert plan.room_axes.axes_swapped is True
    assert plan.room_axes.aligned_x_from_requested_axis == "width"
    assert plan.room_axes.aligned_y_from_requested_axis == "length"
    assert (plan.counts.columns_x, plan.counts.rows_y) == (3, 2)
    assert _aligned_centers(plan) == (
        (-1.2192, -0.6096),
        (0.0, -0.6096),
        (1.2192, -0.6096),
        (-1.2192, 0.6096),
        (0.0, 0.6096),
        (1.2192, 0.6096),
    )
    assert tuple(
        (item.requested_x_m, item.requested_y_m) for item in plan.fixtures
    ) == tuple((-y, x) for x, y in _aligned_centers(plan))
    assert plan.room_axes.rotation_degrees_about_z == -90
    assert all(
        item.footprint_bounds_m.aligned_max_x_m
        - item.footprint_bounds_m.aligned_min_x_m
        == pytest.approx(0.798576)
        for item in plan.fixtures
    )


def test_metre_and_explicit_feet_entry_points_are_identical() -> None:
    from_feet = plan_hps_layout_from_feet(10.0, 8.0)
    from_metres = plan_hps_layout(3.048, 2.4384)

    assert from_feet == from_metres
    assert from_feet.layout_id == from_metres.layout_id


def test_count_selection_fails_when_one_complete_fixture_cannot_fit() -> None:
    with pytest.raises(NoLegalHpsLayoutError, match="too small"):
        plan_hps_layout_from_feet(2.7, 2.7)


def test_mount_height_is_aperture_to_reference_plane_and_zero_height_center() -> None:
    plan = plan_hps_layout_from_feet(
        10.0,
        10.0,
        reference_plane_z_m=0.125,
    )

    assert plan.mount.mount_height_m == 0.6096
    assert plan.mount.aperture_plane_z_m == pytest.approx(0.7346)
    assert plan.mount.fixture_center_z_m == plan.mount.aperture_plane_z_m
    assert all(item.aperture_z_m == plan.mount.aperture_plane_z_m for item in plan.fixtures)
    assert all(item.fixture_center_z_m == item.aperture_z_m for item in plan.fixtures)


def test_layout_serialization_is_deterministic_and_records_prohibited_adjustments() -> None:
    first = plan_hps_layout_from_feet(10.0, 10.0)
    second = plan_hps_layout_from_feet(10.0, 10.0)
    payload = json.loads(format_hps_layout_json(first))

    assert first.layout_id == second.layout_id
    assert format_hps_layout_json(first) == format_hps_layout_json(second)
    assert payload["count_rule"] == (
        "max(1, floor(aligned_axis_ft / 4))_then_deterministic_"
        "decrement_until_complete_fixture_bounds_fit"
    )
    assert set(payload["prohibited_adjustments"]) == {
        "clipping",
        "rescaling",
        "overlap",
        "clamping",
        "rotation",
    }
