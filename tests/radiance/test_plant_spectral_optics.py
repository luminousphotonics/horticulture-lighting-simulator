from __future__ import annotations

import json

import pytest

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.engine.plants.spectral import (  # noqa: E402
    SPECTRAL_RESPONSE_SCHEMA,
    LeafSpectralOpticalBand,
    SpectralPhotonDistribution,
    SpectralPhotonFraction,
    build_leaf_spectral_absorption_summary,
    default_leafy_green_spectral_bands,
)


def test_leaf_spectral_band_absorptance_is_one_minus_reflectance_transmittance() -> None:
    band = LeafSpectralOpticalBand(
        "red",
        600.0,
        700.0,
        reflectance=0.10,
        transmittance=0.05,
    )

    assert band.absorptance == pytest.approx(0.85)


def test_leaf_spectral_band_rejects_invalid_energy_partition() -> None:
    with pytest.raises(ValueError, match="may not exceed"):
        LeafSpectralOpticalBand(
            "bad",
            500.0,
            600.0,
            reflectance=0.70,
            transmittance=0.40,
        )


def test_spectral_photon_distribution_rejects_fraction_sum_above_one() -> None:
    with pytest.raises(ValueError, match="may not sum"):
        SpectralPhotonDistribution(
            "bad_distribution",
            (
                SpectralPhotonFraction("blue", 0.60),
                SpectralPhotonFraction("red", 0.60),
            ),
        )


def test_spectral_absorption_summary_weights_incident_photon_fractions() -> None:
    bands = [
        LeafSpectralOpticalBand("blue", 400.0, 500.0, reflectance=0.10, transmittance=0.05),
        LeafSpectralOpticalBand("red", 600.0, 700.0, reflectance=0.05, transmittance=0.05),
        LeafSpectralOpticalBand("far_red", 700.0, 750.0, reflectance=0.20, transmittance=0.30),
    ]
    summary = build_leaf_spectral_absorption_summary(
        bands,
        [
            SpectralPhotonFraction("blue", 0.30),
            SpectralPhotonFraction("red", 0.50),
            SpectralPhotonFraction("far_red", 0.20),
        ],
    )

    expected_absorbed_fraction = (0.30 * 0.85) + (0.50 * 0.90) + (0.20 * 0.50)

    assert summary["schema"] == SPECTRAL_RESPONSE_SCHEMA
    assert summary["total_absorbed_photon_fraction"] == pytest.approx(expected_absorbed_fraction)
    assert summary["weighted_leaf_absorptance"] == pytest.approx(expected_absorbed_fraction)
    assert summary["response_inputs"]["incident_red_to_far_red_ratio"] == pytest.approx(2.5)
    assert summary["response_inputs"]["absorbed_red_to_far_red_ratio"] == pytest.approx(4.5)
    assert summary["response_inputs"]["blue_absorbed_photon_fraction"] == pytest.approx(0.255)


def test_spectral_absorption_summary_rejects_unknown_band_fraction() -> None:
    with pytest.raises(ValueError, match="unknown spectral bands"):
        build_leaf_spectral_absorption_summary(
            [LeafSpectralOpticalBand("blue", 400.0, 500.0, 0.10, 0.05)],
            {"red": 1.0},
        )


def test_default_leafy_green_spectral_bands_are_valid_and_serializable() -> None:
    summary = build_leaf_spectral_absorption_summary(
        default_leafy_green_spectral_bands(),
        {
            "blue": 0.25,
            "green": 0.35,
            "red": 0.30,
            "far_red": 0.10,
        },
    )

    assert summary["band_count"] == 5
    assert summary["total_absorbed_photon_fraction"] > 0
    assert summary["response_inputs"]["par_absorbed_photon_fraction"] > 0
    assert "yield" in summary["outputs_do_not_predict"]
    json.dumps(summary, sort_keys=True)


def test_generated_spectral_summary_does_not_claim_crop_output_in_response_inputs() -> None:
    summary = build_leaf_spectral_absorption_summary(
        default_leafy_green_spectral_bands(),
        {
            "blue": 0.25,
            "green": 0.35,
            "red": 0.30,
            "far_red": 0.10,
        },
    )

    response_text = json.dumps(summary["response_inputs"], sort_keys=True).lower()

    for forbidden in ("yield", "biomass", "crop_output"):
        assert forbidden not in response_text
