from __future__ import annotations

import math

import pytest

from fspm_optics.application.baseline_leaf_uniformity import (
    OUTSIDE_SUPPORT_REASON,
    TARGET_CLASSIFICATION_BASIS,
    TARGET_CLASSIFICATION_SOURCE,
    TARGET_COVERAGE_LIMITATION,
    TARGET_COVERAGE_PALETTE,
    ZERO_MEAN_REASON,
    BaselineLeafUniformityError,
    LeafPositionOutsideSupport,
    StageAInterpolationField,
    aggregate_leaf_position_ppfd,
    build_baseline_leaf_uniformity_publication,
    build_baseline_physical_leaf_scene,
    classify_baseline_leaf_ppfd,
    leaf_representative_positions,
    one_sided_area_weighted_centroid,
    resolve_baseline_leaf_target_policy,
)
from fspm_optics.plants.models import ScientificMeshFace
from fspm_optics.plants.multi_scene import build_juvenile_natural_fit_scene
from fspm_optics.plants.natural_fit import plan_natural_fit_layout_from_feet
from fspm_optics.transport.scalar_ppfd import PpfdMapSample


def _samples(
    values: tuple[tuple[float, float], tuple[float, float]] = (
        (0.0, 10.0),
        (20.0, 30.0),
    ),
) -> tuple[PpfdMapSample, ...]:
    return tuple(
        PpfdMapSample(x, y, 0.005, values[y_index][x_index])
        for y_index, y in enumerate((-1.0, 1.0))
        for x_index, x in enumerate((-1.0, 1.0))
    )


def _target(
    *,
    system_id: str = "proposed",
    mode: str = "automatic",
    requested: float | None = 10.0,
    achieved: float = 11.0,
    tolerance: float = 2.0,
    override: float | None = None,
):
    return resolve_baseline_leaf_target_policy(
        system_id=system_id,
        mode=mode,
        requested_lighting_target_umol_m2_s=requested,
        achieved_stage_a_mean_umol_m2_s=achieved,
        tolerance_umol_m2_s=tolerance,
        override_umol_m2_s=override,
    )


def _overlay(*, axes_swapped: bool) -> dict[str, object]:
    return {
        "schema_id": "test-authoritative-overlay",
        "room": {"axes_swapped_from_request": axes_swapped},
    }


def _physical_scene(length_ft: float = 2.0, width_ft: float = 2.0):
    plan = plan_natural_fit_layout_from_feet(length_ft, width_ft)
    return build_baseline_physical_leaf_scene(plan)


def _face(
    *,
    face_index: int,
    centroid: tuple[float, float, float],
    area_m2: float,
) -> ScientificMeshFace:
    return ScientificMeshFace(
        plant_id="plant",
        leaf_id="leaf",
        leaf_rank=1,
        leaf_layer="outer",
        face_id=f"face_{face_index}",
        face_index=face_index,
        vertex_indices=(0, 1, 2),
        vertices=((0.0, 0.0, 0.0),) * 3,
        centroid=centroid,
        unit_normal=(0.0, 0.0, 1.0),
        area_m2=area_m2,
    )


def test_asymmetric_triangle_area_weighted_centroid_uses_one_sided_area() -> None:
    centroid = one_sided_area_weighted_centroid(
        (
            _face(face_index=0, centroid=(0.0, 0.0, 0.0), area_m2=1.0),
            _face(face_index=1, centroid=(4.0, 2.0, 1.0), area_m2=3.0),
        )
    )

    assert centroid == pytest.approx((3.0, 1.5, 0.75))
    with pytest.raises(BaselineLeafUniformityError, match="positive"):
        one_sided_area_weighted_centroid(
            (_face(face_index=0, centroid=(0.0, 0.0, 0.0), area_m2=0.0),)
        )


def test_one_ordered_record_per_physical_leaf_before_receiver_expansion() -> None:
    plan = plan_natural_fit_layout_from_feet(2.0, 2.0)
    scene = build_baseline_physical_leaf_scene(plan)
    positions = leaf_representative_positions(scene)
    receiver_scene = build_juvenile_natural_fit_scene(
        plan,
        canonical_plant=scene.canonical_plant,
    )

    assert len(positions) == scene.leaf_count
    assert receiver_scene.counts.receiver_count > len(positions)
    assert tuple(item.global_leaf_index for item in positions) == tuple(
        range(scene.leaf_count)
    )
    assert tuple(item.plant_index for item in positions) == tuple(
        index // len(scene.canonical_plant.leaves)
        for index in range(scene.leaf_count)
    )
    assert len({item.leaf_id for item in positions}) == scene.leaf_count


def test_strict_bilinear_interpolation_centers_interior_and_half_cell_edges() -> None:
    field = StageAInterpolationField.from_samples(_samples())

    assert field.sample(-1.0, 1.0) == 20.0
    assert field.sample(0.0, 0.0) == 15.0
    assert field.sample(-2.0, -2.0) == 0.0
    assert field.sample(2.0, 2.0) == 30.0
    assert field.sample(-2.0, 0.0) == 10.0
    with pytest.raises(LeafPositionOutsideSupport):
        field.sample(2.000001, 0.0)


@pytest.mark.parametrize(
    "invalid_samples",
    [
        (
            PpfdMapSample(-1.0, -1.0, 0.005, 1.0),
            PpfdMapSample(1.0, -1.0, 0.005, 1.0),
            PpfdMapSample(-1.0, 1.0, 0.005, 1.0),
        ),
        (
            PpfdMapSample(-1.0, -1.0, 0.005, 1.0),
            PpfdMapSample(1.0, -1.0, 0.005, 1.0),
            PpfdMapSample(-1.0, 1.0, 0.005, 1.0),
            PpfdMapSample(-1.0, 1.0, 0.005, 2.0),
        ),
        tuple(
            PpfdMapSample(x, y, 0.005, 1.0)
            for y in (-1.0, 1.0)
            for x in (-1.0, 0.0, 2.0)
        ),
        (
            PpfdMapSample(-1.0, -1.0, 0.005, 1.0),
            PpfdMapSample(1.0, -1.0, 0.005, 1.0),
            PpfdMapSample(-1.0, 1.0, 0.006, 1.0),
            PpfdMapSample(1.0, 1.0, 0.006, 1.0),
        ),
        (
            PpfdMapSample(0.0, -1.0, 0.005, 1.0),
            PpfdMapSample(0.0, 1.0, 0.005, 1.0),
        ),
    ],
    ids=("missing", "duplicate", "irregular", "multi_z", "one_x_center"),
)
def test_invalid_incomplete_duplicate_irregular_and_multiz_grids_fail_closed(
    invalid_samples: tuple[PpfdMapSample, ...],
) -> None:
    with pytest.raises(BaselineLeafUniformityError):
        StageAInterpolationField.from_samples(invalid_samples)


def test_stage_a_authoritative_order_and_nonnegative_finite_values_are_strict() -> None:
    with pytest.raises(BaselineLeafUniformityError, match="Y-major"):
        StageAInterpolationField.from_samples(tuple(reversed(_samples())))

    for invalid in (-1.0, math.inf):
        forged = object.__new__(PpfdMapSample)
        object.__setattr__(forged, "x_m", -1.0)
        object.__setattr__(forged, "y_m", -1.0)
        object.__setattr__(forged, "z_m", 0.005)
        object.__setattr__(forged, "ppfd_umol_m2_s", invalid)
        with pytest.raises(BaselineLeafUniformityError):
            StageAInterpolationField.from_samples((forged,) + _samples()[1:])


@pytest.mark.parametrize(
    (
        "system_id",
        "mode",
        "requested",
        "achieved",
        "override",
        "resolved",
        "source",
    ),
    [
        (
            "proposed",
            "automatic",
            900.0,
            850.0,
            None,
            900.0,
            "requested_lighting_target",
        ),
        (
            "conventional",
            "automatic",
            800.0,
            760.0,
            None,
            800.0,
            "requested_lighting_target",
        ),
        (
            "hps",
            "automatic",
            None,
            700.0,
            None,
            700.0,
            "achieved_stage_a_baseline_mean",
        ),
        (
            "proposed",
            "override",
            900.0,
            850.0,
            640.0,
            640.0,
            "authenticated_fspm_override",
        ),
        (
            "conventional",
            "override",
            800.0,
            760.0,
            650.0,
            650.0,
            "authenticated_fspm_override",
        ),
        (
            "hps",
            "override",
            None,
            700.0,
            660.0,
            660.0,
            "authenticated_fspm_override",
        ),
    ],
)
def test_led_hps_automatic_and_override_target_matrix(
    system_id: str,
    mode: str,
    requested: float | None,
    achieved: float,
    override: float | None,
    resolved: float,
    source: str,
) -> None:
    policy = resolve_baseline_leaf_target_policy(
        system_id=system_id,
        mode=mode,
        requested_lighting_target_umol_m2_s=requested,
        achieved_stage_a_mean_umol_m2_s=achieved,
        tolerance_umol_m2_s=20.0,
        override_umol_m2_s=override,
    )

    assert policy.resolved_target_umol_m2_s == resolved
    assert policy.source == source
    assert policy.inclusive_lower_umol_m2_s == max(0.0, resolved - 20.0)
    assert policy.inclusive_upper_umol_m2_s == resolved + 20.0


def test_exact_threshold_boundaries_and_lower_bound_clamping() -> None:
    policy = _target()
    assert classify_baseline_leaf_ppfd(7.999999, policy) == "under_lit"
    assert classify_baseline_leaf_ppfd(8.0, policy) == "target_range"
    assert classify_baseline_leaf_ppfd(12.0, policy) == "target_range"
    assert classify_baseline_leaf_ppfd(12.000001, policy) == "over_lit"

    clamped = _target(tolerance=20.0)
    assert clamped.inclusive_lower_umol_m2_s == 0.0
    assert classify_baseline_leaf_ppfd(0.0, clamped) == "target_range"


def test_equal_leaf_counts_percentages_mad_and_population_cv() -> None:
    summary = aggregate_leaf_position_ppfd((8.0, 10.0, 12.0, 14.0), _target())

    assert summary["denominator_leaf_count"] == 4
    assert summary["under_lit_leaves"]["count"] == 0
    assert summary["target_range_leaves"] == {
        "available": True,
        "count": 3,
        "percentage": 75.0,
    }
    assert summary["over_lit_leaves"] == {
        "available": True,
        "count": 1,
        "percentage": 25.0,
    }
    assert sum(
        summary[name]["count"]
        for name in (
            "under_lit_leaves",
            "target_range_leaves",
            "over_lit_leaves",
        )
    ) == summary["denominator_leaf_count"]
    assert summary["mean_absolute_deviation_from_target"][
        "value_umol_m2_s"
    ] == 2.0
    assert summary["leaf_position_ppfd_coefficient_of_variation"][
        "value_percent"
    ] == pytest.approx(100.0 * math.sqrt(5.0) / 11.0)


def test_zero_mean_preserves_counts_and_mad_but_makes_only_cv_unavailable() -> None:
    summary = aggregate_leaf_position_ppfd((0.0, 0.0), _target())

    assert summary["available"] is True
    assert summary["under_lit_leaves"]["count"] == 2
    assert summary["mean_absolute_deviation_from_target"][
        "value_umol_m2_s"
    ] == 10.0
    assert summary["leaf_position_ppfd_coefficient_of_variation"] == {
        "available": False,
        "value_percent": None,
        "reason_code": ZERO_MEAN_REASON,
    }


def test_outside_support_publishes_complete_authenticated_unavailability() -> None:
    tiny_field = tuple(
        PpfdMapSample(x, y, 0.005, 10.0)
        for y in (-0.01, 0.01)
        for x in (-0.01, 0.01)
    )
    publication = build_baseline_leaf_uniformity_publication(
        run_id="a" * 32,
        system_id="proposed",
        analysis_scope="baseline_ppfd",
        samples=tiny_field,
        scene=_physical_scene(),
        overlay_payload=_overlay(axes_swapped=False),
        requested_lighting_target_umol_m2_s=10.0,
        achieved_stage_a_mean_umol_m2_s=10.0,
        target_mode="automatic",
        target_override_umol_m2_s=None,
        target_tolerance_umol_m2_s=2.0,
    )

    assert publication.payload["available"] is False
    assert publication.payload["unavailable_reason_code"] == OUTSIDE_SUPPORT_REASON
    assert publication.payload["records"] == []
    assert publication.payload["summary"]["available"] is False
    assert publication.payload["summary"]["denominator_leaf_count"] == (
        publication.payload["physical_leaf_count"]
    )
    assert publication.target_coverage["availability"] == "unavailable"
    assert publication.target_coverage["unavailable_reason_code"] == (
        OUTSIDE_SUPPORT_REASON
    )


def test_portrait_axis_swap_stores_requested_and_actual_field_coordinates() -> None:
    publication = build_baseline_leaf_uniformity_publication(
        run_id="b" * 32,
        system_id="proposed",
        analysis_scope="baseline_ppfd",
        samples=_samples(),
        scene=_physical_scene(2.0, 3.0),
        overlay_payload=_overlay(axes_swapped=True),
        requested_lighting_target_umol_m2_s=10.0,
        achieved_stage_a_mean_umol_m2_s=15.0,
        target_mode="automatic",
        target_override_umol_m2_s=None,
        target_tolerance_umol_m2_s=2.0,
    )
    record = publication.payload["records"][0]
    requested_x, requested_y = record["requested_representative_xy_m"]

    assert publication.payload["coordinate_transform"]["axes_swapped"] is True
    assert record["field_sampling_xy_m"] == [requested_y, -requested_x]
    assert record["aligned_simulation_xy_m"] == record["field_sampling_xy_m"]
    assert publication.payload["coordinate_transform"]["determinant"] == 1
    assert record["interpolated_ppfd_umol_m2_s"] == pytest.approx(
            StageAInterpolationField.from_samples(_samples()).sample(
                requested_y,
                -requested_x,
            )
    )


def test_derivation_identity_excludes_run_and_analysis_scope() -> None:
    scene = _physical_scene()
    arguments = {
        "system_id": "proposed",
        "samples": _samples(),
        "scene": scene,
        "overlay_payload": _overlay(axes_swapped=False),
        "requested_lighting_target_umol_m2_s": 10.0,
        "achieved_stage_a_mean_umol_m2_s": 15.0,
        "target_mode": "automatic",
        "target_override_umol_m2_s": None,
        "target_tolerance_umol_m2_s": 2.0,
    }
    baseline = build_baseline_leaf_uniformity_publication(
        run_id="c" * 32,
        analysis_scope="baseline_ppfd",
        **arguments,
    )
    multispectral = build_baseline_leaf_uniformity_publication(
        run_id="d" * 32,
        analysis_scope="baseline_plus_multispectral_fspm",
        **arguments,
    )

    assert baseline.derivation_identity_sha256 == (
        multispectral.derivation_identity_sha256
    )
    assert baseline.sha256 != multispectral.sha256
    assert baseline.payload["scientific_dependencies"] == {
        "stage_b": False,
        "multispectral_bands": False,
        "far_red_selection": False,
        "leaf_surface_receivers": False,
        "phase27g_c_surface_light": False,
        "surface_flux_calibration": False,
    }
    assert baseline.target_coverage == multispectral.target_coverage


def test_target_coverage_contract_is_compact_authenticated_and_baseline_only() -> None:
    publication = build_baseline_leaf_uniformity_publication(
        run_id="e" * 32,
        system_id="proposed",
        analysis_scope="baseline_ppfd",
        samples=_samples(),
        scene=_physical_scene(),
        overlay_payload=_overlay(axes_swapped=False),
        requested_lighting_target_umol_m2_s=10.0,
        achieved_stage_a_mean_umol_m2_s=15.0,
        target_mode="automatic",
        target_override_umol_m2_s=None,
        target_tolerance_umol_m2_s=2.0,
    )
    contract = publication.target_coverage

    assert contract["availability"] == "available"
    assert contract["target_classification_basis"] == TARGET_CLASSIFICATION_BASIS
    assert contract["target_classification_source"] == TARGET_CLASSIFICATION_SOURCE
    assert contract["scientific_limitation"] == TARGET_COVERAGE_LIMITATION
    assert contract["reference"] == {
        "ppfd_umol_m2_s": 10.0,
        "source": "requested_lighting_target",
        "policy_mode": "automatic",
    }
    assert contract["tolerance_ppfd_umol_m2_s"] == 2.0
    assert contract["target_band_deviation"] == {
        "minimum": -1.0,
        "maximum": 1.0,
        "bounds": "inclusive",
    }
    assert contract["palette"]["anchors"] == [
        {
            "deviation": deviation,
            "color_name": color_name,
            "srgb_hex": srgb_hex,
        }
        for deviation, color_name, srgb_hex in TARGET_COVERAGE_PALETTE
    ]
    geometry = contract["canonical_leaf_geometry"]
    assert geometry["canonical_leaf_count"] == 12
    assert geometry["displayed_leaf_ordering"] == (
        "plant-major; canonical-leaf order"
    )
    assert len(geometry["representative_centroids_simulation_xy_m"]) == 12
    assert len(geometry["canonical_leaf_ids"]) == 12
    assert "records" not in contract
    assert "stage_b" not in contract
    assert "surface_flux" not in contract
    assert "calibration" not in contract


def test_hps_target_coverage_uses_achieved_stage_a_reference() -> None:
    publication = build_baseline_leaf_uniformity_publication(
        run_id="f" * 32,
        system_id="hps",
        analysis_scope="baseline_ppfd",
        samples=_samples(),
        scene=_physical_scene(),
        overlay_payload=_overlay(axes_swapped=False),
        requested_lighting_target_umol_m2_s=None,
        achieved_stage_a_mean_umol_m2_s=15.0,
        target_mode="automatic",
        target_override_umol_m2_s=None,
        target_tolerance_umol_m2_s=2.0,
    )

    assert publication.target_coverage["reference"] == {
        "ppfd_umol_m2_s": 15.0,
        "source": "achieved_stage_a_baseline_mean",
        "policy_mode": "automatic",
    }
