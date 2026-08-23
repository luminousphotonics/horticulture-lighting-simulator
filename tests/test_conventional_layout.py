from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import json
import math
from pathlib import Path

import pytest

from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_COMPARISON_PROFILE_ID,
    DEFAULT_MOUNT_HEIGHT_M,
    FULL_FIT_LAYOUT_POLICY,
    PRACTICAL_LAYOUT_POLICY,
    ROLLING_BENCH_LAYOUT_POLICY,
    ROLLING_BENCH_PITCH_M,
    LayoutPolicy,
    MountReferencePlaneSemantics,
    NoLegalConventionalLayoutError,
    ResolvedFixtureCounts,
    RoomDimensionsM,
    UnsupportedFixtureRotationError,
    plan_conventional_layout,
    plan_conventional_layout_from_feet,
    proposed_smd_exact_tiled_pitch,
)
from fspm_optics.fixtures.smd.config import SmdLayoutConfig
from fspm_optics.fixtures.smd.positions import generate_smd_layout


def test_ten_by_ten_practical_regression() -> None:
    plan = plan_conventional_layout_from_feet(10, 10)

    assert plan.policy.name == PRACTICAL_LAYOUT_POLICY
    assert plan.resolved_counts.columns_x == 2
    assert plan.resolved_counts.rows_y == 2
    assert plan.resolved_counts.total == 4
    assert plan.target_practical_gaps_m is not None
    assert plan.target_practical_gaps_m.x_m / 0.0254 == pytest.approx(5.15, abs=0.01)
    assert plan.target_practical_gaps_m.y_m / 0.0254 == pytest.approx(
        4.60433, abs=0.01
    )
    assert plan.actual_centered_gaps_m.x_m == pytest.approx(0.20573333333333334)
    assert plan.actual_centered_gaps_m.y_m == pytest.approx(0.2744)
    assert [item.aperture_center_m.aligned_x_m for item in plan.fixtures] == (
        pytest.approx([-0.6978666666666667, 0.6978666666666667] * 2)
    )
    assert [item.aperture_center_m.aligned_y_m for item in plan.fixtures] == (
        pytest.approx([-0.6807, -0.6807, 0.6807, 0.6807])
    )


@pytest.mark.parametrize(
    ("length_ft", "width_ft", "columns_x", "rows_y"),
    (
        (12.0, 12.0, 3, 3),
        (24.0, 12.0, 6, 3),
        (12.0, 24.0, 6, 3),
    ),
)
def test_rolling_bench_uses_centered_rigid_48_in_array_under_aligned_axes(
    length_ft: float,
    width_ft: float,
    columns_x: int,
    rows_y: int,
) -> None:
    plan = plan_conventional_layout_from_feet(
        length_ft,
        width_ft,
        policy=ROLLING_BENCH_LAYOUT_POLICY,
    )

    assert plan.resolved_counts == ResolvedFixtureCounts(columns_x, rows_y)
    assert plan.wall_margin_m.x_m_per_wall == 0.0
    assert plan.wall_margin_m.y_m_per_wall == 0.0
    x_by_column = [
        plan.fixtures[column].aperture_center_m.aligned_x_m
        for column in range(columns_x)
    ]
    y_by_row = [
        plan.fixtures[row * columns_x].aperture_center_m.aligned_y_m
        for row in range(rows_y)
    ]
    assert [
        right - left for left, right in zip(x_by_column, x_by_column[1:])
    ] == pytest.approx([ROLLING_BENCH_PITCH_M] * (columns_x - 1))
    assert [
        upper - lower for lower, upper in zip(y_by_row, y_by_row[1:])
    ] == pytest.approx([ROLLING_BENCH_PITCH_M] * (rows_y - 1))
    assert (x_by_column[0] + x_by_column[-1]) / 2.0 == pytest.approx(0.0)
    assert (y_by_row[0] + y_by_row[-1]) / 2.0 == pytest.approx(0.0)


def test_rolling_bench_orientation_edge_gaps_and_provenance_are_explicit() -> None:
    plan = plan_conventional_layout_from_feet(
        12,
        12,
        policy=ROLLING_BENCH_LAYOUT_POLICY,
    )
    provenance = plan.placement_provenance_payload()

    assert provenance["mode_id"] == ROLLING_BENCH_LAYOUT_POLICY
    assert provenance["center_to_center_pitch_m"] == {
        "x_m": 1.2192,
        "y_m": 1.2192,
    }
    assert provenance["fixture_footprint_m"] == {
        "x_extent_m": 1.087,
        "y_extent_m": 1.190,
    }
    edge_gaps = provenance["calculated_fixture_edge_gaps_m"]
    assert edge_gaps["x_m"] == pytest.approx(0.1322)
    assert edge_gaps["y_m"] == pytest.approx(0.0292)
    assert edge_gaps["x_m"] / 0.0254 == pytest.approx(5.204724409448819)
    assert edge_gaps["y_m"] / 0.0254 == pytest.approx(1.149606299212603)
    assert round(edge_gaps["x_m"] / 0.0254, 3) == 5.205
    assert round(edge_gaps["y_m"] / 0.0254, 3) == 1.150
    assert provenance["orientation"] == {
        "fixture_rotation_degrees_about_aligned_z": 90.0,
        "aligned_x_extent_from_fixture_axis": "width",
        "aligned_y_extent_from_fixture_axis": "length",
        "automatic_rotation": False,
    }
    assert provenance["fixture_counts"] == {
        "columns_x": 3,
        "rows_y": 3,
        "total": 9,
    }
    assert provenance["array_bounds_m"] == {
        "aligned_min_x_m": pytest.approx(-1.7627),
        "aligned_max_x_m": pytest.approx(1.7627),
        "aligned_min_y_m": pytest.approx(-1.8142),
        "aligned_max_y_m": pytest.approx(1.8142),
    }
    assert provenance["perimeter_margins_m"] == {
        "x_m": pytest.approx(0.0661),
        "y_m": pytest.approx(0.0146),
    }
    assert all(
        fixture.transform.fixture_rotation_degrees == 90.0
        for fixture in plan.fixtures
    )


def test_rolling_bench_every_fixture_is_inside_room_in_both_axis_conventions() -> None:
    for dimensions in ((24.0, 12.0), (12.0, 24.0)):
        plan = plan_conventional_layout_from_feet(
            *dimensions,
            policy=ROLLING_BENCH_LAYOUT_POLICY,
        )
        half_x = plan.room_axes.aligned.length_m / 2.0
        half_y = plan.room_axes.aligned.width_m / 2.0
        for fixture in plan.fixtures:
            bounds = fixture.footprint_bounds_m
            assert -half_x <= bounds.aligned_min_x_m
            assert bounds.aligned_max_x_m <= half_x
            assert -half_y <= bounds.aligned_min_y_m
            assert bounds.aligned_max_y_m <= half_y


def test_rolling_bench_serialization_round_trips_without_coordinate_change() -> None:
    plan = plan_conventional_layout_from_feet(
        24,
        12,
        policy=ROLLING_BENCH_LAYOUT_POLICY,
    )
    payload = json.loads(plan.to_json())

    assert payload == plan.to_payload()
    assert payload["policy"]["name"] == ROLLING_BENCH_LAYOUT_POLICY
    assert payload["placement_provenance"] == (
        plan.placement_provenance_payload()
    )
    assert [
        item["aperture_center_m"] for item in payload["fixtures"]
    ] == [
        {
            "aligned_x_m": item.aperture_center_m.aligned_x_m,
            "aligned_y_m": item.aperture_center_m.aligned_y_m,
            "requested_x_m": item.aperture_center_m.requested_x_m,
            "requested_y_m": item.aperture_center_m.requested_y_m,
            "z_m": item.aperture_center_m.z_m,
        }
        for item in plan.fixtures
    ]


def test_practical_target_gaps_explicitly_derive_from_smd_pitch_per_axis() -> None:
    plan = plan_conventional_layout_from_feet(14, 9)
    smd = generate_smd_layout(
        SmdLayoutConfig(
            room_length_ft=14,
            room_width_ft=9,
            proposed_layout_mode="linear",
        )
    )

    assert plan.policy.target_spacing_basis == (
        "proposed_smd_exact_tiled_pitch_per_aligned_axis"
    )
    assert plan.proposed_smd_exact_tiled_pitch_m is not None
    assert plan.target_practical_gaps_m is not None
    assert plan.proposed_smd_exact_tiled_pitch_m.x_m == smd.pitch_x_m
    assert plan.proposed_smd_exact_tiled_pitch_m.y_m == smd.pitch_y_m
    assert plan.target_practical_gaps_m.x_m == pytest.approx(
        smd.pitch_x_m - 0.1524
    )
    assert plan.target_practical_gaps_m.y_m == pytest.approx(
        smd.pitch_y_m - 0.1650
    )
    assert plan.target_practical_gaps_m.x_m != plan.target_practical_gaps_m.y_m


def test_square_room_has_deterministic_unswapped_axes() -> None:
    plan = plan_conventional_layout_from_feet(10.0, 10.0)

    assert plan.room_axes.axes_swapped is False
    assert plan.room_axes.aligned_x_from_requested_axis == "length"
    assert plan.room_axes.aligned_y_from_requested_axis == "width"
    assert plan.room_axes.requested == plan.room_axes.aligned


def test_rectangular_room_aligns_long_axis_and_maps_back_to_requested_axes() -> None:
    plan = plan_conventional_layout_from_feet(8, 12)
    first = plan.fixtures[0]

    assert plan.room_axes.axes_swapped is True
    assert plan.room_axes.requested.length_m == pytest.approx(8 * 0.3048)
    assert plan.room_axes.aligned.length_m == pytest.approx(12 * 0.3048)
    assert plan.room_axes.aligned_x_from_requested_axis == "width"
    assert first.aperture_center_m.requested_x_m == (
        -first.aperture_center_m.aligned_y_m
    )
    assert first.aperture_center_m.requested_y_m == (
        first.aperture_center_m.aligned_x_m
    )
    assert plan.room_axes.rotation_degrees_about_z == -90
    bounds = first.footprint_bounds_m
    assert bounds.requested_max_x_m - bounds.requested_min_x_m == pytest.approx(1.087)
    assert bounds.requested_max_y_m - bounds.requested_min_y_m == pytest.approx(1.190)


def test_rectangular_practical_counts_are_resolved_independently() -> None:
    plan = plan_conventional_layout_from_feet(16, 8)

    assert plan.resolved_counts.columns_x > plan.resolved_counts.rows_y
    assert plan.resolved_counts.columns_x * plan.resolved_counts.rows_y == len(
        plan.fixtures
    )


def test_actual_gap_is_equal_at_both_edges_and_between_footprints() -> None:
    plan = plan_conventional_layout_from_feet(10, 10)
    margin = plan.wall_margin_m.x_m_per_wall
    room_left = -plan.room_axes.aligned.length_m / 2.0
    first = plan.fixtures[0].footprint_bounds_m
    second = plan.fixtures[1].footprint_bounds_m

    left_gap = first.aligned_min_x_m - (room_left + margin)
    middle_gap = second.aligned_min_x_m - first.aligned_max_x_m
    room_right = plan.room_axes.aligned.length_m / 2.0
    right_gap = (room_right - margin) - second.aligned_max_x_m
    assert left_gap == pytest.approx(plan.actual_centered_gaps_m.x_m)
    assert middle_gap == pytest.approx(plan.actual_centered_gaps_m.x_m)
    assert right_gap == pytest.approx(plan.actual_centered_gaps_m.x_m)


def test_fixture_order_is_y_major_with_x_increasing_and_stable_ids() -> None:
    first = plan_conventional_layout_from_feet(10, 10)
    second = plan_conventional_layout_from_feet(10.0, 10.0)

    assert [item.identity.grid_index for item in first.fixtures] == [
        item.identity.grid_index for item in second.fixtures
    ]
    assert [item.identity.fixture_id for item in first.fixtures] == [
        item.identity.fixture_id for item in second.fixtures
    ]
    assert [
        (item.identity.grid_index.row_y, item.identity.grid_index.column_x)
        for item in first.fixtures
    ] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert first.fixtures[0].aperture_center_m.aligned_x_m < (
        first.fixtures[1].aperture_center_m.aligned_x_m
    )


def test_one_fixture_is_centered_without_clamp_or_rescale() -> None:
    plan = plan_conventional_layout_from_feet(5, 4)
    fixture = plan.fixtures[0]

    assert plan.resolved_counts.total == 1
    assert fixture.aperture_center_m.aligned_x_m == pytest.approx(0.0)
    assert fixture.aperture_center_m.aligned_y_m == pytest.approx(0.0)
    assert plan.fixture_dimensions_m.length_m == 1.190
    assert plan.fixture_dimensions_m.width_m == 1.087
    assert plan.fixture_dimensions_m.height_m == 0.108


def test_practical_formula_count_is_not_silently_reduced_after_resolution() -> None:
    plan = plan_conventional_layout_from_feet(20, 9)
    target = plan.target_practical_gaps_m
    assert target is not None
    expected_columns = math.floor(
        max(
            plan.usable_footprint_m.length_m - target.x_m,
            plan.fixture_dimensions_m.length_m,
        )
        / (plan.fixture_dimensions_m.length_m + target.x_m)
    )
    expected_rows = math.floor(
        max(
            plan.usable_footprint_m.width_m - target.y_m,
            plan.fixture_dimensions_m.width_m,
        )
        / (plan.fixture_dimensions_m.width_m + target.y_m)
    )

    assert plan.resolved_counts.columns_x == max(1, expected_columns)
    assert plan.resolved_counts.rows_y == max(1, expected_rows)
    assert len(plan.fixtures) == max(1, expected_columns) * max(1, expected_rows)


@pytest.mark.parametrize(
    ("length_m", "width_m"),
    [(1.20, 1.20), (2.0, 1.10), (1.10, 2.0)],
)
def test_physically_too_small_room_returns_no_fixture_and_raises_typed_error(
    length_m: float, width_m: float
) -> None:
    with pytest.raises(NoLegalConventionalLayoutError, match="too small"):
        plan_conventional_layout(length_m, width_m)


def test_every_footprint_is_inside_room_and_distinct() -> None:
    plan = plan_conventional_layout_from_feet(15, 9)
    half_x = plan.room_axes.aligned.length_m / 2.0
    half_y = plan.room_axes.aligned.width_m / 2.0
    bounds = [item.footprint_bounds_m for item in plan.fixtures]

    for item in bounds:
        assert item.aligned_min_x_m >= -half_x
        assert item.aligned_max_x_m <= half_x
        assert item.aligned_min_y_m >= -half_y
        assert item.aligned_max_y_m <= half_y
    for index, left in enumerate(bounds):
        for right in bounds[index + 1 :]:
            separated = (
                left.aligned_max_x_m <= right.aligned_min_x_m
                or right.aligned_max_x_m <= left.aligned_min_x_m
                or left.aligned_max_y_m <= right.aligned_min_y_m
                or right.aligned_max_y_m <= left.aligned_min_y_m
            )
            assert separated


@pytest.mark.parametrize("rotation", [0.1, -90.0, 360.0])
def test_nonzero_rotation_is_explicitly_rejected(rotation: float) -> None:
    with pytest.raises(UnsupportedFixtureRotationError, match="fixed 0 degree"):
        plan_conventional_layout_from_feet(
            10, 10, fixture_rotation_degrees=rotation
        )


def test_full_fit_is_separate_and_never_implicit() -> None:
    practical = plan_conventional_layout_from_feet(10, 10)
    full_fit = plan_conventional_layout_from_feet(
        10, 10, policy=FULL_FIT_LAYOUT_POLICY
    )

    assert practical.policy == LayoutPolicy()
    assert full_fit.policy == LayoutPolicy.full_fit()
    assert full_fit.policy.name == FULL_FIT_LAYOUT_POLICY
    assert full_fit.proposed_smd_exact_tiled_pitch_m is None
    assert full_fit.target_practical_gaps_m is None
    assert practical.layout_id != full_fit.layout_id


def test_mount_height_is_aperture_plane_not_fixture_center_height() -> None:
    plan = plan_conventional_layout_from_feet(5, 4)
    fixture = plan.fixtures[0]

    assert plan.mount.mount_height_m == DEFAULT_MOUNT_HEIGHT_M
    assert plan.mount.mount_height_definition == (
        "emitting_aperture_plane_to_receiver_reference_plane"
    )
    assert fixture.aperture_center_m.z_m == pytest.approx(0.4572)
    assert fixture.fixture_center_m.z_m == pytest.approx(0.5112)
    assert fixture.transform.local_emission_axis == "negative_z"
    assert fixture.transform.world_emission_direction == (0.0, 0.0, -1.0)


def test_nonzero_reference_plane_translates_both_planes_without_redefinition() -> None:
    plan = plan_conventional_layout_from_feet(
        5,
        4,
        mount=MountReferencePlaneSemantics(reference_plane_z_m=0.25),
    )
    fixture = plan.fixtures[0]

    assert fixture.aperture_center_m.z_m == pytest.approx(0.7072)
    assert fixture.fixture_center_m.z_m == pytest.approx(0.7612)
    assert fixture.fixture_center_m.z_m - fixture.aperture_center_m.z_m == (
        pytest.approx(0.108 / 2.0)
    )


def test_layout_serialization_and_identity_are_deterministic_for_numeric_equivalents() -> None:
    first = plan_conventional_layout_from_feet(10, 10)
    second = plan_conventional_layout(3.048, 3.048)

    assert first == second
    assert first.layout_id == second.layout_id
    assert first.to_json() == second.to_json()
    assert first.to_json().endswith("\n")
    assert "/home/" not in first.to_json()
    assert ".salvage_source" not in first.to_json()


def test_layout_and_fixture_identities_remain_conventional_not_smd() -> None:
    plan = plan_conventional_layout_from_feet(10, 10)

    assert plan.conventional_profile_id == CONVENTIONAL_COMPARISON_PROFILE_ID
    assert plan.layout_id.startswith("conventional-layout-v1-")
    assert all(
        item.identity.fixture_id.startswith("conventional-fixture-")
        for item in plan.fixtures
    )
    assert all(
        item.identity.conventional_profile_id == CONVENTIONAL_COMPARISON_PROFILE_ID
        for item in plan.fixtures
    )


def test_layout_records_are_immutable() -> None:
    plan = plan_conventional_layout_from_feet(5, 4)

    with pytest.raises(FrozenInstanceError):
        plan.resolved_counts.columns_x = 99  # type: ignore[misc]


def test_smd_pitch_identity_is_stable_and_system_labeled() -> None:
    room = RoomDimensionsM(3.048, 3.048)
    first = proposed_smd_exact_tiled_pitch(room)
    second = proposed_smd_exact_tiled_pitch(room)

    assert first == second
    assert len(first.proposed_smd_layout_identity) == 64


def test_layout_module_has_no_radiance_execution_or_legacy_imports() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "fixtures"
        / "conventional_led"
        / "layout.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
    }

    assert imported_roots <= {
        "__future__",
        "dataclasses",
        "fspm_optics",
        "hashlib",
        "json",
        "math",
        "typing",
    }
    assert "subprocess" not in source
    assert ".salvage_source" not in source
    assert "Radiance" not in source
