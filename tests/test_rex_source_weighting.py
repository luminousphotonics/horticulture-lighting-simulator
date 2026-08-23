from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from fspm_optics.optics.profiles import (
    REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,
    REX_LEAF_OPTICS_TREATMENT_ID,
    load_rex_green_butterhead_mature_leaf_optics_v1,
)
from fspm_optics.optics.rex_weighting import (
    PHOTON_INTEGRATION_METHOD,
    WAVELENGTH_ALIGNMENT_METHOD,
    RexSourceWeightedAtrPayload,
    build_rex_source_weighted_atr_payload,
    compute_source_weighted_atr_interval,
    format_rex_source_weighted_atr_json,
    read_rex_source_weighted_atr_json,
    write_rex_source_weighted_atr_json,
)
from fspm_optics.sources.smd.profile import (
    SMD_NORMALIZATION_POLICY,
    SMD_SOURCE_MODEL_ID,
    build_nominal_smd_source_model,
)
from fspm_optics.spectral.distribution import PhotonDistribution


EXPECTED_ATR = {
    "scalar_par": (0.8066236906588139, 0.10057196464815153, 0.09280434469303463),
    "blue": (0.9043967608197288, 0.02825093123593379, 0.06735230794433743),
    "green": (0.6901907874229399, 0.1680449832047886, 0.14176422937227143),
    "orange": (0.7862929797057988, 0.11938359440059321, 0.09432342589360795),
    "red": (0.8755267572091805, 0.06339973547115643, 0.06107350731966306),
    "far_red": (0.2800452243419452, 0.40974973908972684, 0.310205036568328),
}


def _coefficient_tuple(interval) -> tuple[float, float, float]:
    coefficients = interval.coefficients
    return (
        coefficients.absorptance,
        coefficients.transmittance,
        coefficients.reflectance,
    )


def test_packaged_rex_mean_profile_is_the_weighting_input() -> None:
    profile = load_rex_green_butterhead_mature_leaf_optics_v1()
    payload = build_rex_source_weighted_atr_payload()

    assert profile.profile_id == REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1
    assert profile.treatment_id == REX_LEAF_OPTICS_TREATMENT_ID
    assert profile.wavelength_nm == tuple(range(404, 798))
    assert payload.rex_profile_id == profile.profile_id
    assert payload.rex_profile_version == "0.2"
    assert payload.rex_treatment_id == "mean_of_treatments"
    assert payload.source_model_id == SMD_SOURCE_MODEL_ID
    assert payload.source_normalization_policy == SMD_NORMALIZATION_POLICY


def test_wavelength_alignment_uses_exact_grid_and_policy_truncation() -> None:
    source = build_nominal_smd_source_model().photon_distribution
    payload = build_rex_source_weighted_atr_payload()

    assert payload.scalar_par.requested_start_nm == 400
    assert payload.scalar_par.requested_end_nm_exclusive == 700
    assert payload.scalar_par.effective_start_nm == 404
    assert payload.scalar_par.effective_end_nm_exclusive == 700
    assert payload.scalar_par.wavelength_count == 296
    assert payload.scalar_par.source_photon_weight_umol_s == pytest.approx(
        source.amount_between(404, 700),
        abs=1e-12,
    )
    assert payload.band("blue").effective_start_nm == 404
    assert payload.band("blue").wavelength_count == 96
    assert payload.band("green").effective_start_nm == 500
    serialized = payload.to_payload()
    assert serialized["alignment"]["method"] == WAVELENGTH_ALIGNMENT_METHOD
    assert serialized["alignment"]["integration_method"] == PHOTON_INTEGRATION_METHOD
    assert serialized["alignment"]["interpolation_applied"] is False
    assert serialized["alignment"]["extrapolation_applied"] is False


def test_scalar_and_five_band_regression_coefficients() -> None:
    payload = build_rex_source_weighted_atr_payload()

    assert _coefficient_tuple(payload.scalar_par) == pytest.approx(
        EXPECTED_ATR["scalar_par"],
        abs=1e-12,
    )
    for band_id in ("blue", "green", "orange", "red", "far_red"):
        assert _coefficient_tuple(payload.band(band_id)) == pytest.approx(
            EXPECTED_ATR[band_id],
            abs=1e-12,
        )


def test_orange_and_red_remain_exact_separate_intervals() -> None:
    payload = build_rex_source_weighted_atr_payload()
    orange = payload.band("orange")
    red = payload.band("red")

    assert (
        orange.requested_start_nm,
        orange.requested_end_nm_exclusive,
        orange.wavelength_count,
    ) == (600, 625, 25)
    assert (
        red.requested_start_nm,
        red.requested_end_nm_exclusive,
        red.wavelength_count,
    ) == (625, 700, 75)
    assert orange.source_photon_weight_umol_s == pytest.approx(30.749357961150295)
    assert red.source_photon_weight_umol_s == pytest.approx(114.6423666445508)
    assert orange.coefficients != red.coefficients


def test_every_interval_is_bounded_closed_and_not_renormalized() -> None:
    payload = build_rex_source_weighted_atr_payload()

    assert payload.max_profile_wavelength_closure_error <= 2.3e-16
    for interval in (payload.scalar_par, *payload.bands):
        coefficients = _coefficient_tuple(interval)
        assert all(0.0 <= value <= 1.0 for value in coefficients)
        assert sum(coefficients) == pytest.approx(1.0, abs=1e-12)
        diagnostics = interval.closure_diagnostics
        assert diagnostics.raw_absorptance == interval.coefficients.absorptance
        assert diagnostics.raw_transmittance == interval.coefficients.transmittance
        assert diagnostics.raw_reflectance == interval.coefficients.reflectance
        assert diagnostics.raw_sum == pytest.approx(1.0, abs=1e-12)
        assert abs(diagnostics.closure_error) <= 1e-12
        assert diagnostics.normalization_applied is False
        assert interval.source_photon_weight_umol_s > 0.0


def test_missing_exact_source_wavelength_is_rejected() -> None:
    profile = load_rex_green_butterhead_mature_leaf_optics_v1()
    source = build_nominal_smd_source_model().photon_distribution
    keep = [index for index, wavelength in enumerate(source.wavelength_nm) if wavelength != 550]
    incomplete = PhotonDistribution(
        tuple(source.wavelength_nm[index] for index in keep),
        tuple(source.photon_amount[index] for index in keep),
    )

    with pytest.raises(ValueError, match=r"missing required exact wavelengths.*550 nm"):
        compute_source_weighted_atr_interval(
            profile,
            incomplete,
            interval_id="green",
            label="Green",
            requested_start_nm=500,
            requested_end_nm_exclusive=600,
        )


def test_zero_source_photon_coverage_is_rejected() -> None:
    profile = load_rex_green_butterhead_mature_leaf_optics_v1()
    source = build_nominal_smd_source_model().photon_distribution
    zero_green = PhotonDistribution(
        source.wavelength_nm,
        tuple(
            0.0 if 500 <= wavelength < 600 else amount
            for wavelength, amount in zip(
                source.wavelength_nm,
                source.photon_amount,
                strict=True,
            )
        ),
    )

    with pytest.raises(ValueError, match="no positive photon weight"):
        compute_source_weighted_atr_interval(
            profile,
            zero_green,
            interval_id="green",
            label="Green",
            requested_start_nm=500,
            requested_end_nm_exclusive=600,
        )


def test_malformed_model_closure_is_rejected_without_normalization() -> None:
    profile = load_rex_green_butterhead_mature_leaf_optics_v1()
    source = build_nominal_smd_source_model().photon_distribution
    bad_absorptance = list(profile.absorptance)
    bad_absorptance[0] = 0.5
    malformed = replace(profile, absorptance=tuple(bad_absorptance))

    with pytest.raises(ValueError, match=r"closure failed at 404 nm.*No normalization"):
        compute_source_weighted_atr_interval(
            malformed,
            source,
            interval_id="blue",
            label="Blue",
            requested_start_nm=400,
            requested_end_nm_exclusive=500,
            allow_profile_start_truncation=True,
        )


def test_payload_json_round_trip_is_typed_and_deterministic(tmp_path: Path) -> None:
    payload = build_rex_source_weighted_atr_payload()
    first = format_rex_source_weighted_atr_json(payload)
    second = format_rex_source_weighted_atr_json(payload)
    output = write_rex_source_weighted_atr_json(tmp_path / "rex_atr.json", payload)

    assert first == second
    assert output.read_text(encoding="utf-8") == first
    assert read_rex_source_weighted_atr_json(output) == payload
    assert RexSourceWeightedAtrPayload.from_payload(json.loads(first)) == payload


def test_json_rejects_hidden_normalization_or_malformed_closure(tmp_path: Path) -> None:
    raw = build_rex_source_weighted_atr_payload().to_payload()
    raw["scalar_par"]["closure_diagnostics"]["normalization_applied"] = True
    source = tmp_path / "malformed.json"
    source.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="must not be renormalized"):
        read_rex_source_weighted_atr_json(source)
