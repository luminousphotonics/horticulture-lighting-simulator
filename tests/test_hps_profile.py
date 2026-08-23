from __future__ import annotations

from dataclasses import replace

import pytest

from fspm_optics.fixtures.conventional_led.profile import CONVENTIONAL_SOURCE_ID
from fspm_optics.fixtures.hps import (
    DECLARED_OPERATING_POINT_BASIS,
    HPS_COMPARISON_PROFILE_ID,
    HPS_IES_SHA256,
    HPS_INITIAL_LAMP_PAR_PPF_UMOL_S,
    HPS_NOMINAL_LAMP_CLASS_POWER_W,
    HPS_SOURCE_ID,
    HPS_SPD_SHA256,
    HPS_SYSTEM_PPE_UMOL_PER_J,
    HPS_TESTED_SYSTEM_INPUT_POWER_W,
    HpsDeclaredOperatingPoint,
    HpsProfileError,
    build_hps_comparison_profile,
    format_hps_profile_json,
)
from fspm_optics.sources.smd.profile import SMD_SOURCE_MODEL_ID


def test_hps_profile_separates_measured_identity_from_modeled_calibration() -> None:
    profile = build_hps_comparison_profile()

    assert profile.profile_id == HPS_COMPARISON_PROFILE_ID
    assert profile.source_id == HPS_SOURCE_ID
    assert profile.profile_id == "hps_1000w_source_authority_v3"
    assert profile.source_id == "hps_fixed_initial_par_source_v3"
    assert profile.source_id not in (CONVENTIONAL_SOURCE_ID, SMD_SOURCE_MODEL_ID)
    assert profile.photometric_asset.sha256 == HPS_IES_SHA256
    assert profile.spectral_asset.sha256 == HPS_SPD_SHA256
    assert profile.ies_test.nominal_lamp_power_w == 1000.0
    assert profile.ies_test.tested_input_watts == 1045.0
    assert profile.ies_test.test_id == "ANONYMIZED HPS PHOTOMETRY"
    assert profile.ies_test.test_laboratory == "ANONYMIZED"
    assert profile.ies_test.manufacturer == "GENERIC HPS REFERENCE"
    assert profile.ies_test.catalog_id == "HPS-1000W"
    test_payload = profile.ies_test.to_payload()
    assert test_payload["sets_tested_system_input_power_authority"] is True
    assert test_payload["sets_initial_lamp_PAR_PPF_authority"] is False
    modeled = profile.modeled_operating_point
    assert (
        modeled.electrical_power_w_per_fixture,
        modeled.par_ppe_umol_per_j,
        modeled.par_ppf_umol_s_per_fixture,
    ) == (
        HPS_TESTED_SYSTEM_INPUT_POWER_W,
        HPS_SYSTEM_PPE_UMOL_PER_J,
        HPS_INITIAL_LAMP_PAR_PPF_UMOL_S,
    )
    assert modeled.par_ppe_umol_per_j == 1750.0 / 1045.0
    assert modeled.fixed_output is True
    assert HPS_NOMINAL_LAMP_CLASS_POWER_W == 1000.0
    assert modeled.basis == DECLARED_OPERATING_POINT_BASIS
    assert modeled.to_payload()["nominal_lamp_class_is_provenance_only"] is True


def test_hps_profile_preserves_flat_ies_footprint_and_claim_boundary() -> None:
    profile = build_hps_comparison_profile()

    assert (
        profile.fixture_dimensions_m.length_x_m,
        profile.fixture_dimensions_m.width_y_m,
        profile.fixture_dimensions_m.emitting_height_m,
    ) == (0.798576, 0.603504, 0.0)
    boundaries = profile.to_payload()["calibration_boundaries"]
    assert boundaries["ies_supplies_angular_shape_and_footprint"] is True
    assert boundaries["documented_initial_lamp_PAR_PPF_is_absolute_authority"] is True
    assert boundaries["tested_system_input_power_is_absolute_authority"] is True
    assert boundaries["system_PPE_is_computed_from_PPF_over_power"] is True
    assert boundaries["fixed_output"] is True
    assert boundaries["ies_lumens_or_raw_candela_define_PAR_PPF"] is False
    assert profile.photometric_asset.license_name is None
    assert profile.photometric_asset.acquisition_history is None


def test_hps_modeled_point_cannot_be_replaced_by_an_inconsistent_claim() -> None:
    profile = build_hps_comparison_profile()
    with pytest.raises(HpsProfileError, match="power × PAR PPE"):
        replace(
            profile,
            modeled_operating_point=HpsDeclaredOperatingPoint(
                electrical_power_w_per_fixture=1045.0,
                par_ppe_umol_per_j=HPS_SYSTEM_PPE_UMOL_PER_J,
                par_ppf_umol_s_per_fixture=1800.0,
                basis=DECLARED_OPERATING_POINT_BASIS,
            ),
        )


def test_hps_profile_serialization_and_identity_are_deterministic() -> None:
    first = build_hps_comparison_profile()
    second = build_hps_comparison_profile()

    assert first == second
    assert first.profile_sha256 == second.profile_sha256
    assert format_hps_profile_json(first) == format_hps_profile_json(second)
