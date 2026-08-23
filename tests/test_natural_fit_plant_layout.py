from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import json
import math
from pathlib import Path

import pytest

from fspm_optics.plants import (
    NATURAL_FIT_POLICY_ID,
    REX_JUVENILE_PREHEADING_LOCAL_XY_BOUNDS_M,
    NaturalFitLayoutError,
    NaturalFitPolicy,
    PlantLocalXYBoundsM,
    generate_rex_juvenile_preheading_plant,
    plan_natural_fit_layout,
    plan_natural_fit_layout_from_feet,
)


def test_planner_bounds_are_the_exact_generated_juvenile_bounds() -> None:
    plant = generate_rex_juvenile_preheading_plant()
    vertices = tuple(
        vertex for leaf in plant.leaves for vertex in leaf.vertices
    )
    exact = PlantLocalXYBoundsM(
        min_x_m=min(vertex[0] for vertex in vertices),
        max_x_m=max(vertex[0] for vertex in vertices),
        min_y_m=min(vertex[1] for vertex in vertices),
        max_y_m=max(vertex[1] for vertex in vertices),
    )

    assert exact == REX_JUVENILE_PREHEADING_LOCAL_XY_BOUNDS_M
    assert exact.to_payload() == {
        "min_x_m": -0.08162080833475895,
        "max_x_m": 0.08590156495994676,
        "min_y_m": -0.08414348531724969,
        "max_y_m": 0.06819392493850181,
    }


@pytest.mark.parametrize(
    ("room_ft", "count_per_axis", "total"),
    [(10, 8, 64), (20, 16, 256), (30, 23, 529)],
)
def test_expected_square_room_counts_follow_policy(
    room_ft: int, count_per_axis: int, total: int
) -> None:
    plan = plan_natural_fit_layout_from_feet(room_ft, room_ft)

    assert plan.policy.policy_id == NATURAL_FIT_POLICY_ID
    assert plan.count_x == plan.count_y == count_per_axis
    assert plan.total_count == total


def test_rectangular_and_portrait_rooms_share_long_axis_x_simulation_frame() -> None:
    landscape = plan_natural_fit_layout_from_feet(12, 8)
    portrait = plan_natural_fit_layout_from_feet(8, 12)

    assert (landscape.count_x, landscape.count_y) == (10, 7)
    assert (portrait.count_x, portrait.count_y) == (10, 7)
    assert portrait.requested_room_m.length_m == pytest.approx(8 * 0.3048)
    assert portrait.requested_room_m.width_m == pytest.approx(12 * 0.3048)
    assert portrait.axis_mapping.axes_swapped is True
    assert portrait.axis_mapping.aligned_x_from_requested_axis == "width"
    assert portrait.axis_mapping.aligned_y_from_requested_axis == "length"
    assert portrait.axis_mapping.rotation_degrees_about_z == -90
    assert all(
        item.aligned_x_m == item.requested_y_m
        and item.aligned_y_m == -item.requested_x_m
        for item in portrait.plants
    )


def test_axis_counts_are_selected_independently() -> None:
    plan = plan_natural_fit_layout_from_feet(20, 10)

    assert plan.count_x == 16
    assert plan.count_y == 8
    assert plan.x_axis.actual_pitch_m != plan.y_axis.actual_pitch_m


def test_full_asymmetric_geometry_bounds_retain_boundary_clearance() -> None:
    plan = plan_natural_fit_layout_from_feet(12, 8)
    bounds = plan.plant_local_bounds_m
    half_x = plan.requested_room_m.length_m / 2.0
    half_y = plan.requested_room_m.width_m / 2.0
    clearance = plan.policy.boundary_clearance_m

    for plant in plan.plants:
        assert plant.requested_x_m + bounds.min_x_m >= -half_x + clearance - 1e-12
        assert plant.requested_x_m + bounds.max_x_m <= half_x - clearance + 1e-12
        assert plant.requested_y_m + bounds.min_y_m >= -half_y + clearance - 1e-12
        assert plant.requested_y_m + bounds.max_y_m <= half_y - clearance + 1e-12


def test_adjacent_geometry_retains_minimum_interplant_clearance() -> None:
    plan = plan_natural_fit_layout_from_feet(10, 10)
    bounds = plan.plant_local_bounds_m
    required = plan.policy.interplant_geometry_clearance_m
    first_row = plan.plants[: plan.count_x]
    first_column = plan.plants[:: plan.count_x]

    for left, right in zip(first_row, first_row[1:]):
        gap = (right.requested_x_m + bounds.min_x_m) - (
            left.requested_x_m + bounds.max_x_m
        )
        assert gap >= required - 1e-12
    for lower, upper in zip(first_column, first_column[1:]):
        gap = (upper.requested_y_m + bounds.min_y_m) - (
            lower.requested_y_m + bounds.max_y_m
        )
        assert gap >= required - 1e-12


def test_one_plant_uses_midpoint_of_each_allowable_origin_interval() -> None:
    bounds = REX_JUVENILE_PREHEADING_LOCAL_XY_BOUNDS_M
    room_x = bounds.span_x_m + 0.02
    room_y = bounds.span_y_m + 0.02
    plan = plan_natural_fit_layout(room_x, room_y)

    assert (plan.count_x, plan.count_y, plan.total_count) == (1, 1, 1)
    assert plan.x_axis.actual_pitch_m is None
    assert plan.y_axis.actual_pitch_m is None
    assert plan.plants[0].requested_x_m == pytest.approx(
        (plan.x_axis.origin_min_m + plan.x_axis.origin_max_m) / 2.0
    )
    assert plan.plants[0].requested_y_m == pytest.approx(
        (plan.y_axis.origin_min_m + plan.y_axis.origin_max_m) / 2.0
    )


def test_exact_pitch_error_tie_prefers_larger_count() -> None:
    bounds = PlantLocalXYBoundsM(-0.05, 0.05, -0.05, 0.05)
    # With 0.005 m boundary clearance this gives usable span 0.96 m.
    # Counts 3 and 4 have pitches 0.48 and 0.32 m, equally far from 0.40 m.
    plan = plan_natural_fit_layout(
        1.07,
        1.07,
        profile_id="tie_probe",
        plant_local_bounds_m=bounds,
    )

    assert plan.count_x == plan.count_y == 4
    assert plan.x_axis.actual_pitch_m == pytest.approx(0.32)


def test_positions_are_y_major_x_minor_with_stable_ids() -> None:
    plan = plan_natural_fit_layout_from_feet(8, 5)
    first_row = plan.plants[: plan.count_x]

    assert [item.grid_index.row_y for item in first_row] == [0] * plan.count_x
    assert [item.grid_index.column_x for item in first_row] == list(
        range(plan.count_x)
    )
    assert [item.requested_x_m for item in first_row] == sorted(
        item.requested_x_m for item in first_row
    )
    assert plan.plants[plan.count_x].grid_index.row_y == 1
    assert plan.plants[0].plant_id.endswith("_layout_r000_c000")
    assert plan.plants[-1].plant_id.endswith(
        f"_layout_r{plan.count_y - 1:03d}_c{plan.count_x - 1:03d}"
    )


def test_ids_serialization_and_plan_hash_are_stable() -> None:
    first = plan_natural_fit_layout_from_feet(10, 10)
    second = plan_natural_fit_layout(3.048, 3.048)

    assert first == second
    assert [item.plant_id for item in first.plants] == [
        item.plant_id for item in second.plants
    ]
    assert first.plan_hash == second.plan_hash
    assert len(first.plan_hash) == 64
    assert first.to_json() == second.to_json()
    assert first.to_json().endswith("\n")
    assert json.loads(first.to_json())["plan_hash"] == first.plan_hash


def test_hash_changes_for_every_policy_relevant_input_class() -> None:
    baseline = plan_natural_fit_layout(3.048, 3.048)
    room_changed = plan_natural_fit_layout(3.049, 3.048)
    policy_changed = tuple(
        plan_natural_fit_layout(3.048, 3.048, policy=policy)
        for policy in (
            NaturalFitPolicy(target_center_pitch_m=0.41),
            NaturalFitPolicy(boundary_clearance_m=0.006),
            NaturalFitPolicy(interplant_geometry_clearance_m=0.011),
        )
    )
    bounds = REX_JUVENILE_PREHEADING_LOCAL_XY_BOUNDS_M
    bounds_changed = plan_natural_fit_layout(
        3.048,
        3.048,
        plant_local_bounds_m=PlantLocalXYBoundsM(
            bounds.min_x_m - 0.001,
            bounds.max_x_m,
            bounds.min_y_m,
            bounds.max_y_m,
        ),
    )

    assert len(
        {
            baseline.plan_hash,
            room_changed.plan_hash,
            *(plan.plan_hash for plan in policy_changed),
            bounds_changed.plan_hash,
        }
    ) == 6


@pytest.mark.parametrize(
    ("length_m", "width_m"),
    [
        (0.0, 1.0),
        (-1.0, 1.0),
        (math.nan, 1.0),
        (1.0, math.inf),
        (True, 1.0),
    ],
)
def test_invalid_room_dimensions_fail_closed(
    length_m: float, width_m: float
) -> None:
    with pytest.raises(NaturalFitLayoutError):
        plan_natural_fit_layout(length_m, width_m)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_center_pitch_m": 0.0},
        {"target_center_pitch_m": math.nan},
        {"boundary_clearance_m": -0.001},
        {"boundary_clearance_m": math.inf},
        {"interplant_geometry_clearance_m": -0.001},
        {"interplant_geometry_clearance_m": math.nan},
    ],
)
def test_invalid_policy_values_fail_closed(kwargs: dict[str, float]) -> None:
    with pytest.raises(NaturalFitLayoutError):
        NaturalFitPolicy(**kwargs)


@pytest.mark.parametrize(
    "values",
    [
        (0.0, 0.0, -0.1, 0.1),
        (-0.1, 0.1, 0.0, 0.0),
        (math.nan, 0.1, -0.1, 0.1),
        (-0.1, math.inf, -0.1, 0.1),
    ],
)
def test_invalid_local_bounds_fail_closed(
    values: tuple[float, float, float, float]
) -> None:
    with pytest.raises(NaturalFitLayoutError):
        PlantLocalXYBoundsM(*values)


def test_infeasible_containment_fails_instead_of_clamping() -> None:
    bounds = REX_JUVENILE_PREHEADING_LOCAL_XY_BOUNDS_M
    with pytest.raises(NaturalFitLayoutError, match="cannot be contained"):
        plan_natural_fit_layout(bounds.span_x_m, bounds.span_y_m)


def test_plan_records_are_immutable() -> None:
    plan = plan_natural_fit_layout_from_feet(10, 10)
    with pytest.raises(FrozenInstanceError):
        plan.x_axis.count = 2  # type: ignore[misc]


def test_planner_has_no_fixture_source_system_or_execution_coupling() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "plants"
        / "natural_fit.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert not any(
        value in module
        for module in imported_modules
        for value in ("fixtures", "sources", "spectral", "transport", "radiance")
    )
    assert "subprocess" not in source
    planner = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "plan_natural_fit_layout"
    )
    public_parameters = {
        argument.arg for argument in (*planner.args.args, *planner.args.kwonlyargs)
    }
    assert not (public_parameters & {"count", "rows", "columns"})
    assert not any(isinstance(node, ast.While) for node in ast.walk(planner))
    payload = plan_natural_fit_layout_from_feet(5, 5).to_payload()
    forbidden_keys = {"fixture", "source", "spectrum", "transport", "system"}

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {
                nested
                for item in value.values()
                for nested in keys(item)
            }
        if isinstance(value, list):
            return {nested for item in value for nested in keys(item)}
        return set()

    assert not (keys(payload) & forbidden_keys)


def test_planning_does_not_generate_mutate_or_count_size_the_profile() -> None:
    plant_before = generate_rex_juvenile_preheading_plant()
    small = plan_natural_fit_layout_from_feet(10, 10)
    large = plan_natural_fit_layout_from_feet(30, 30)
    plant_after = generate_rex_juvenile_preheading_plant()

    assert plant_before == plant_after
    assert small.plant_local_bounds_m is REX_JUVENILE_PREHEADING_LOCAL_XY_BOUNDS_M
    assert large.plant_local_bounds_m is REX_JUVENILE_PREHEADING_LOCAL_XY_BOUNDS_M
    source = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "plants"
        / "natural_fit.py"
    ).read_text(encoding="utf-8")
    assert "generator" not in source
    assert "generate_rex" not in source
