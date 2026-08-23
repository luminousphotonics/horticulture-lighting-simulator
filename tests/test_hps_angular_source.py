from __future__ import annotations

from dataclasses import replace
import hashlib
import math
from pathlib import Path

import pytest

from fspm_optics.fixtures.hps import (
    HPS_RADIANCE_CARRIER_MULTIPLIER,
    HpsFixturePlacement,
    build_hps_derived_ies,
    build_hps_radiance_source_plan,
    derive_hps_carrier_scale,
    format_hps_apertures_rad,
    format_hps_source_plan_json,
    load_hps_lm63,
    normalize_hps_angular_distribution,
)
from fspm_optics.photometry import (
    expand_type_c_horizontal_symmetry,
    integrate_type_c_flux,
    parse_lm63,
)


def _flat(rows: tuple[tuple[float, ...], ...]) -> tuple[float, ...]:
    return tuple(value for row in rows for value in row)


def _theta_space_trapezoid_flux(photometry) -> float:
    """Reproduce the superseded Phase 24 diagnostic quadrature."""

    vertical = tuple(math.radians(value) for value in photometry.vertical_angles_deg)
    horizontal = tuple(math.radians(value) for value in photometry.horizontal_angles_deg)
    cells: list[float] = []
    for plane_index in range(len(horizontal) - 1):
        delta_horizontal = horizontal[plane_index + 1] - horizontal[plane_index]
        first = photometry.candela_by_horizontal_plane[plane_index]
        second = photometry.candela_by_horizontal_plane[plane_index + 1]
        for vertical_index in range(len(vertical) - 1):
            gamma_0 = vertical[vertical_index]
            gamma_1 = vertical[vertical_index + 1]
            delta_gamma = gamma_1 - gamma_0
            lower = 0.5 * (
                first[vertical_index] + second[vertical_index]
            ) * math.sin(gamma_0)
            upper = 0.5 * (
                first[vertical_index + 1] + second[vertical_index + 1]
            ) * math.sin(gamma_1)
            cells.append(0.5 * (lower + upper) * delta_gamma * delta_horizontal)
    return math.fsum(cells)


def test_quadrant_expansion_closes_once_and_preserves_type_c_symmetry() -> None:
    declared = load_hps_lm63()
    expanded = expand_type_c_horizontal_symmetry(declared)
    declared_planes = declared.candela_by_horizontal_plane
    expected_plane_indices = (0, 1, 2, 3, 4, 3, 2, 1, 0, 1, 2, 3, 4, 3, 2, 1, 0)

    assert expanded.horizontal_angles_deg == tuple(22.5 * index for index in range(17))
    assert expanded.candela_by_horizontal_plane == tuple(
        declared_planes[index] for index in expected_plane_indices
    )
    assert len(expanded.candela_by_horizontal_plane) - 1 == 16
    assert expanded.candela_by_horizontal_plane[-1] == expanded.candela_by_horizontal_plane[0]
    assert expanded.has_duplicate_horizontal_closure

    flux = integrate_type_c_flux(expanded)
    assert flux.horizontal_domain_degrees == pytest.approx(360.0)
    assert flux.horizontal_closure_counted_once is True
    assert flux.full_expanded_table_flux_cd_sr == pytest.approx(
        118057.25673187636,
        rel=1e-12,
    )
    assert flux.downward_flux_cd_sr == pytest.approx(
        flux.full_expanded_table_flux_cd_sr
    )
    assert flux.upward_flux_cd_sr == 0.0
    assert _theta_space_trapezoid_flux(expanded) == pytest.approx(
        117927.390044,
        rel=1e-9,
    )
    assert _theta_space_trapezoid_flux(expanded) != pytest.approx(
        flux.downward_flux_cd_sr,
        rel=1e-6,
    )


def test_type_c_cell_integration_uses_four_corner_mean_and_exact_solid_angle() -> None:
    synthetic = parse_lm63(
        "IESNA:LM-63-2002\n"
        "[TEST]EXACT-SOLID-ANGLE\n"
        "TILT=NONE\n"
        "1 -1 1 3 2 1 2 1 1 0\n"
        "1 1 1\n"
        "0 60 90\n"
        "0 360\n"
        "2 4 8\n"
        "2 4 8\n"
    )

    flux = integrate_type_c_flux(synthetic)
    expected = 2.0 * math.pi * (
        0.5 * (2.0 + 4.0) * (math.cos(0.0) - math.cos(math.pi / 3.0))
        + 0.5 * (4.0 + 8.0) * (math.cos(math.pi / 3.0) - math.cos(math.pi / 2.0))
    )

    assert expected == pytest.approx(9.0 * math.pi)
    assert flux.downward_flux_cd_sr == pytest.approx(expected, rel=1e-15)
    assert flux.horizontal_domain_degrees == pytest.approx(360.0)


def test_hps_angular_normalization_is_invariant_to_uniform_candela_scaling() -> None:
    original = load_hps_lm63()
    scaled = replace(
        original,
        candela_by_horizontal_plane=tuple(
            tuple(value * 37.0 for value in row)
            for row in original.candela_by_horizontal_plane
        ),
    )
    base_distribution = normalize_hps_angular_distribution(original)
    scaled_distribution = normalize_hps_angular_distribution(scaled)
    base_document = build_hps_derived_ies(original)
    scaled_document = build_hps_derived_ies(scaled)

    assert _flat(scaled_distribution.relative_candela_by_expanded_plane) == pytest.approx(
        _flat(base_distribution.relative_candela_by_expanded_plane)
    )
    assert scaled_distribution.original_flux.downward_flux_cd_sr == pytest.approx(
        37.0 * base_distribution.original_flux.downward_flux_cd_sr
    )
    assert _flat(
        parse_lm63(scaled_document.text).candela_by_horizontal_plane
    ) == pytest.approx(
        _flat(parse_lm63(base_document.text).candela_by_horizontal_plane)
    )
    assert _flat(scaled_distribution.unit_downward_candela_by_expanded_plane) == (
        pytest.approx(_flat(base_distribution.unit_downward_candela_by_expanded_plane))
    )
    unit_photometry = replace(
        expand_type_c_horizontal_symmetry(original),
        candela_multiplier=1.0,
        ballast_factor=1.0,
        future_use_factor=1.0,
        candela_by_horizontal_plane=(
            base_distribution.unit_downward_candela_by_expanded_plane
        ),
    )
    assert integrate_type_c_flux(unit_photometry).downward_flux_cd_sr == pytest.approx(
        1.0, abs=1e-12
    )


def test_hps_derived_ies_is_deterministic_metric_unit_downward_flux() -> None:
    original = load_hps_lm63()
    first = build_hps_derived_ies(original)
    second = build_hps_derived_ies(original)
    parsed = parse_lm63(first.text)

    assert first == second
    assert first.sha256 == hashlib.sha256(first.text.encode("ascii")).hexdigest()
    assert parsed.units_type == 2
    assert (
        parsed.fixture_width_m,
        parsed.fixture_length_m,
        parsed.fixture_height_m,
    ) == (0.603504, 0.798576, 0.0)
    assert parsed.horizontal_angles_deg == tuple(22.5 * index for index in range(17))
    assert parsed.candela_by_horizontal_plane[0] == parsed.candela_by_horizontal_plane[-1]
    assert parsed.candela_multiplier == 1.0
    assert parsed.ballast_factor == 1.0
    assert parsed.future_use_factor == 1.0
    assert parsed.input_watts == 1.0
    assert integrate_type_c_flux(parsed).downward_flux_cd_sr == 1.0
    assert first.original_flux.full_expanded_table_flux_cd_sr == (
        first.original_flux.downward_flux_cd_sr
    )
    assert first.to_payload()["excluded_upward_flux_cd_sr"] == 0.0
    assert first.to_payload()["excluded_upward_fraction"] == 0.0


def test_source_plan_applies_only_initial_ppf_times_179_and_one_flat_aperture_each(
    tmp_path: Path,
) -> None:
    placements = (
        HpsFixturePlacement("hps_a", -0.5, 0.25, 1.2),
        HpsFixturePlacement("hps_b", 0.5, 0.25, 1.2),
    )
    plan = build_hps_radiance_source_plan(
        workspace=tmp_path,
        placements=placements,
    )
    carrier = derive_hps_carrier_scale()

    assert carrier.ies2rad_multiplier == HPS_RADIANCE_CARRIER_MULTIPLIER
    assert carrier.ies2rad_multiplier == 1750.0 * 179.0 == 313250.0
    assert len(plan.apertures) == len(placements)
    assert plan.whole_plan_downward_ppf_umol_s == 2 * 1750.0
    for aperture in plan.apertures:
        assert aperture.normal == (0.0, 0.0, -1.0)
        assert (aperture.length_x_m, aperture.width_y_m, aperture.emitting_height_m) == (
            0.798576,
            0.603504,
            0.0,
        )
        assert aperture.transported_downward_ppf_umol_s == 1750.0
    assert "legacy_lumen_integration" in plan.prohibited_scale_or_shape_paths
    assert "d4_post_trace_symmetrization" in plan.prohibited_scale_or_shape_paths
    assert plan.scientific_payload()["post_trace_scale"] is None
    assert plan.scientific_payload()["converted_generated_geometry_enters_final_scene"] is False
    assert plan.scientific_payload()["shared_angular_source"]["aperture_correction"] == (
        "flatcorr"
    )


def test_hps_source_plan_and_aperture_serialization_are_deterministic(
    tmp_path: Path,
) -> None:
    first = build_hps_radiance_source_plan(workspace=tmp_path)
    second = build_hps_radiance_source_plan(workspace=tmp_path)

    assert first.source_plan_id == second.source_plan_id
    assert format_hps_source_plan_json(first) == format_hps_source_plan_json(second)
    assert format_hps_apertures_rad(first) == format_hps_apertures_rad(second)
    assert first.ies2rad.command.argv == second.ies2rad.command.argv
    assert first.ies2rad.command.cwd == tmp_path
    assert first.ies2rad.scientific_payload()["execution_performed"] is False
