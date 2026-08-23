from __future__ import annotations

from dataclasses import replace
import hashlib
import math

import pytest

from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_IES_RESOURCE_NAME,
    CONVENTIONAL_IES_SHA256,
    OriginalIesIdentity,
    build_downward_normalized_derived_ies,
    conventional_resource_bytes,
    integrate_type_c_hemisphere_flux,
    load_approved_lm63,
    parse_lm63,
)


def _identity() -> OriginalIesIdentity:
    return OriginalIesIdentity(
        resource_name=CONVENTIONAL_IES_RESOURCE_NAME,
        sha256=CONVENTIONAL_IES_SHA256,
        product_id="CONVENTIONAL-LED-8-BAR",
        test_id="GENERIC-CONVENTIONAL-LED-PHOTOMETRY",
        tested_input_watts=663.20,
        candela_multiplier=1.0,
        ballast_factor=1.0,
        future_use_factor_2019=1.1,
    )


def test_derived_ies_bytes_are_deterministic_and_original_resource_is_unchanged() -> None:
    original_before = conventional_resource_bytes(CONVENTIONAL_IES_RESOURCE_NAME)
    photometry = load_approved_lm63()
    first = build_downward_normalized_derived_ies(
        photometry, original_ies=_identity()
    )
    second = build_downward_normalized_derived_ies(
        photometry, original_ies=_identity()
    )
    original_after = conventional_resource_bytes(CONVENTIONAL_IES_RESOURCE_NAME)

    assert first == second
    assert first.filename == "conventional_led_unit_downward_flux.ies"
    assert "unit_downward_flux" in first.filename
    assert "unit_downward_flux" in first.normalization_policy
    assert first.text.encode("ascii") == second.text.encode("ascii")
    assert first.sha256 == hashlib.sha256(first.text.encode("ascii")).hexdigest()
    assert original_before == original_after
    assert hashlib.sha256(original_after).hexdigest() == CONVENTIONAL_IES_SHA256
    assert first.sha256 != CONVENTIONAL_IES_SHA256


def test_derived_ies_round_trip_preserves_complete_angles_and_c_plane_asymmetry() -> None:
    original = load_approved_lm63()
    document = build_downward_normalized_derived_ies(
        original, original_ies=_identity()
    )
    derived = parse_lm63(document.text)

    assert derived.vertical_angles_deg == original.vertical_angles_deg
    assert derived.horizontal_angles_deg == original.horizontal_angles_deg
    assert derived.vertical_angle_count == 181
    assert derived.horizontal_angle_count == 17
    assert len(derived.candela_by_horizontal_plane) == 17
    assert derived.candela_by_horizontal_plane[0] == derived.candela_by_horizontal_plane[-1]
    assert derived.candela_by_horizontal_plane[0] != derived.candela_by_horizontal_plane[8]


def test_original_full_downward_upward_flux_closes_and_fractions_sum_to_one() -> None:
    flux = integrate_type_c_hemisphere_flux(load_approved_lm63())

    assert flux.full_sphere_flux_cd_sr == pytest.approx(
        flux.downward_hemisphere_flux_cd_sr + flux.upward_hemisphere_flux_cd_sr
    )
    assert flux.downward_fraction_of_full_sphere + flux.upward_fraction_of_full_sphere == (
        pytest.approx(1.0, abs=1e-14)
    )
    assert 0.0 < flux.upward_fraction_of_full_sphere < 1.0
    assert 0.0 < flux.downward_fraction_of_full_sphere < 1.0


def test_hemisphere_integration_splits_a_segment_crossing_ninety_degrees() -> None:
    synthetic = parse_lm63(
        "IESNA:LM-63-2019\n"
        "[TEST]crossing-90\n"
        "TILT=NONE\n"
        "1 -1 1 4 2 1 2 1 1 1\n"
        "1 1 1\n"
        "0 80 100 180\n"
        "0 360\n"
        "1 1 1 1\n"
        "1 1 1 1\n"
    )
    flux = integrate_type_c_hemisphere_flux(synthetic)

    assert flux.downward_hemisphere_flux_cd_sr == pytest.approx(2.0 * math.pi)
    assert flux.upward_hemisphere_flux_cd_sr == pytest.approx(2.0 * math.pi)
    assert flux.downward_fraction_of_full_sphere == pytest.approx(0.5)
    assert flux.upward_fraction_of_full_sphere == pytest.approx(0.5)


def test_derived_ies_reintegrates_downward_to_one_and_retains_upward_values() -> None:
    original = load_approved_lm63()
    document = build_downward_normalized_derived_ies(
        original, original_ies=_identity()
    )
    derived = parse_lm63(document.text)
    flux = integrate_type_c_hemisphere_flux(derived)

    assert flux.downward_hemisphere_flux_cd_sr == 1.0
    assert flux.upward_hemisphere_flux_cd_sr > 0.0
    assert flux.full_sphere_flux_cd_sr > 1.0
    first_upward_index = next(
        index for index, angle in enumerate(derived.vertical_angles_deg) if angle > 90.0
    )
    assert max(
        value
        for plane in derived.candela_by_horizontal_plane
        for value in plane[first_upward_index:]
    ) > 0.0
    for original_plane, derived_plane in zip(
        original.candela_by_horizontal_plane,
        derived.candela_by_horizontal_plane,
        strict=True,
    ):
        assert derived_plane == pytest.approx(
            tuple(value * document.candela_normalization_scale for value in original_plane)
        )
    assert derived.candela_multiplier == 1.0
    assert derived.ballast_factor == 1.0
    assert derived.future_use_factor == 1.0
    assert derived.input_watts == 1.0
    assert derived.lumens_per_lamp == -1.0
    assert document.duplicate_closure_plane_counted == 1


def test_uniform_candela_scale_cannot_change_derived_unit_shape() -> None:
    original = load_approved_lm63()
    scaled = replace(
        original,
        candela_by_horizontal_plane=tuple(
            tuple(value * 37.0 for value in plane)
            for plane in original.candela_by_horizontal_plane
        ),
    )
    base_document = build_downward_normalized_derived_ies(
        original, original_ies=_identity()
    )
    scaled_document = build_downward_normalized_derived_ies(
        scaled, original_ies=_identity()
    )
    base = parse_lm63(base_document.text)
    changed = parse_lm63(scaled_document.text)

    changed_values = tuple(value for row in changed.candela_by_horizontal_plane for value in row)
    base_values = tuple(value for row in base.candela_by_horizontal_plane for value in row)
    assert changed_values == pytest.approx(base_values)
    assert integrate_type_c_hemisphere_flux(
        changed
    ).downward_hemisphere_flux_cd_sr == 1.0
    assert scaled_document.original_flux.downward_hemisphere_flux_cd_sr == pytest.approx(
        37.0 * base_document.original_flux.downward_hemisphere_flux_cd_sr
    )


def test_original_2019_future_use_field_is_documented_but_cannot_survive_staging() -> None:
    original = load_approved_lm63()
    assert original.future_use_factor == pytest.approx(1.1)

    derived = parse_lm63(
        build_downward_normalized_derived_ies(original, original_ies=_identity()).text
    )
    assert derived.future_use_factor == 1.0
    assert "[_FSPM_ORIGINAL_SHA256]" in build_downward_normalized_derived_ies(
        original, original_ies=_identity()
    ).text
    assert "declared PAR PPF is external" in build_downward_normalized_derived_ies(
        original, original_ies=_identity()
    ).text
