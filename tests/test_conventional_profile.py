from __future__ import annotations

from dataclasses import replace

import pytest

from fspm_optics.fixtures.conventional_led.profile import (
    CONVENTIONAL_COMPARISON_PROFILE_ID,
    CONVENTIONAL_FIXTURE_POWER_W,
    CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J,
    CONVENTIONAL_FIXTURE_PPF_UMOL_S,
    CONVENTIONAL_SOURCE_ID,
    DECLARED_OPERATING_POINT_BASIS,
    SCENARIO_CLAIM_BOUNDARY,
    ConventionalProfileError,
    DeclaredModeledOperatingPoint,
    build_conventional_comparison_profile,
    format_conventional_profile_json,
)
from fspm_optics.fixtures.conventional_led.resources import (
    CONVENTIONAL_IES_SHA256,
    CONVENTIONAL_SPD_SHA256,
)
from fspm_optics.sources.smd.profile import SMD_SOURCE_MODEL_ID


def test_declared_profile_keeps_test_provenance_and_modeled_point_separate() -> None:
    profile = build_conventional_comparison_profile()

    assert profile.profile_id == CONVENTIONAL_COMPARISON_PROFILE_ID
    assert profile.source_id == CONVENTIONAL_SOURCE_ID
    assert profile.source_id != SMD_SOURCE_MODEL_ID
    assert profile.scenario_claim_boundary == SCENARIO_CLAIM_BOUNDARY
    assert profile.ies_asset.sha256 == CONVENTIONAL_IES_SHA256
    assert profile.spd_asset.sha256 == CONVENTIONAL_SPD_SHA256
    assert profile.ies_test.product_id == "CONVENTIONAL-LED-8-BAR"
    assert profile.ies_test.test_id == "GENERIC-CONVENTIONAL-LED-PHOTOMETRY"
    assert profile.ies_test.tested_input_watts == 663.20
    test_payload = profile.ies_test.to_dict()
    assert test_payload["sets_modeled_operating_point"] is False
    assert not {
        "issue_date",
        "manufacturer",
        "test_laboratory",
        "tested_voltage_v",
        "tested_current_a",
        "tested_power_factor",
    } & test_payload.keys()
    modeled = profile.modeled_operating_point
    assert modeled.electrical_power_w_per_fixture == CONVENTIONAL_FIXTURE_POWER_W == 660.0
    assert modeled.par_ppe_umol_per_j == CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J == 2.6
    assert modeled.par_ppf_umol_s_per_fixture == CONVENTIONAL_FIXTURE_PPF_UMOL_S == 1716.0
    assert modeled.basis == DECLARED_OPERATING_POINT_BASIS
    assert (
        modeled.electrical_power_w_per_fixture * modeled.par_ppe_umol_per_j
        == modeled.par_ppf_umol_s_per_fixture
    )


def test_profile_preserves_fixture_dimensions_and_authorization_boundaries() -> None:
    profile = build_conventional_comparison_profile()

    assert (
        profile.fixture_dimensions_m.width_m,
        profile.fixture_dimensions_m.length_m,
        profile.fixture_dimensions_m.height_m,
    ) == (1.087, 1.190, 0.108)
    for asset in (profile.ies_asset, profile.spd_asset):
        assert asset.user_authorized_use is True
        assert asset.user_authorized_redistribution is True
        assert asset.license_name is None
        assert asset.acquisition_history is None


def test_inconsistent_operating_point_fails_immediately_with_typed_error() -> None:
    with pytest.raises(ConventionalProfileError, match="power × PAR PPE"):
        DeclaredModeledOperatingPoint(
            electrical_power_w_per_fixture=660.0,
            par_ppe_umol_per_j=2.6,
            par_ppf_umol_s_per_fixture=1717.0,
            basis=DECLARED_OPERATING_POINT_BASIS,
        )


def test_ies_test_watts_cannot_replace_modeled_watts() -> None:
    profile = build_conventional_comparison_profile()
    with pytest.raises(ConventionalProfileError, match="must remain 660 W"):
        replace(
            profile,
            modeled_operating_point=DeclaredModeledOperatingPoint(
                electrical_power_w_per_fixture=663.20,
                par_ppe_umol_per_j=2.6,
                par_ppf_umol_s_per_fixture=1724.32,
                basis=DECLARED_OPERATING_POINT_BASIS,
            ),
        )


def test_profile_serialization_and_hash_are_deterministic() -> None:
    first = build_conventional_comparison_profile()
    second = build_conventional_comparison_profile()

    assert first == second
    assert first.profile_sha256 == second.profile_sha256
    assert len(first.profile_sha256) == 64
    assert format_conventional_profile_json(first) == (
        format_conventional_profile_json(second)
    )
    payload = first.to_payload()
    assert payload["schema_version"] == 3
    assert payload["calibration_boundaries"] == {
        "absolute_PAR_anchor_applied_exactly_once": True,
        "ies_test_watts_define_modeled_watts": False,
        "ies_lumens_or_candela_define_modeled_PAR_PPF": False,
        "spd_amplitude_defines_modeled_PAR_PPF": False,
        "spd_defines_relative_photon_shape": True,
        "rated_fixture_authority": True,
    }
