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
    PLANT_SPECTRAL_RESPONSE_SCHEMA,
    build_plant_spectral_response_payload,
    default_fixture_spectral_distribution,
    parse_spectral_photon_fraction_overrides,
    write_plant_spectral_response_artifact,
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



def _surface_flux_payload() -> dict[str, object]:
    return {
        "schema": "rad_rebuild.fspm.plant_surface_flux.v1",
        "schema_version": 1,
        "status": "computed",
        "method": "radiance_leaf_surface_receiver_sampling_v1",
        "plant_count": 1,
        "leaf_count": 1,
        "surface_count": 1,
        "surface_summaries": [
            {
                "surface_id": "plant_000_leaf_000_face_0000",
                "plant_id": "plant_000",
                "leaf_id": "plant_000_leaf_000",
                "leaf_index": 0,
                "face_index": 0,
                "area_m2": 2.0,
                "incident_photon_flux_umol_s": 100.0,
            }
        ],
    }


def test_default_fixture_spectral_distributions_are_mode_specific() -> None:
    proposed = default_fixture_spectral_distribution("smd").fraction_map()
    conventional = default_fixture_spectral_distribution("competitor").fraction_map()
    hps = default_fixture_spectral_distribution("1000W HPS").fraction_map()

    assert proposed != conventional
    assert conventional != hps
    assert hps["far_red"] > proposed["far_red"]
    assert sum(proposed.values()) == pytest.approx(1.0)
    assert sum(conventional.values()) == pytest.approx(1.0)
    assert sum(hps.values()) == pytest.approx(1.0)


def test_spectral_fraction_override_parser() -> None:
    distribution = parse_spectral_photon_fraction_overrides(
        "blue=0.2, green=0.3, red=0.4, far_red=0.1"
    )

    assert distribution.source == "environment_override"
    assert distribution.fraction_map()["red"] == pytest.approx(0.4)


def test_plant_spectral_response_payload_converts_surface_flux_to_band_fluxes() -> None:
    bands = [
        LeafSpectralOpticalBand("blue", 400.0, 500.0, reflectance=0.10, transmittance=0.05),
        LeafSpectralOpticalBand("red", 600.0, 700.0, reflectance=0.05, transmittance=0.05),
        LeafSpectralOpticalBand("far_red", 700.0, 750.0, reflectance=0.20, transmittance=0.30),
    ]
    distribution = SpectralPhotonDistribution(
        "test_distribution",
        (
            SpectralPhotonFraction("blue", 0.25),
            SpectralPhotonFraction("red", 0.50),
            SpectralPhotonFraction("far_red", 0.25),
        ),
    )

    payload = build_plant_spectral_response_payload(
        _surface_flux_payload(),
        bands,
        distribution,
    )

    expected_total = (100.0 * 0.25 * 0.85) + (100.0 * 0.50 * 0.90) + (100.0 * 0.25 * 0.50)
    expected_par = (100.0 * 0.25 * 0.85) + (100.0 * 0.50 * 0.90)

    assert payload["schema"] == PLANT_SPECTRAL_RESPONSE_SCHEMA
    assert payload["status"] == "computed"
    assert payload["total_absorbed_photon_flux_umol_s"] == pytest.approx(expected_total)
    assert payload["total_absorbed_par_photon_flux_umol_s"] == pytest.approx(expected_par)
    assert payload["leaf_summaries"][0]["absorbed_par_photon_flux_density_umol_m2_s"] == pytest.approx(
        expected_par / 2.0
    )
    assert payload["visualization"]["leaf_values"][0]["visual_intensity_0_1"] == pytest.approx(0.5)


def test_plant_spectral_response_artifact_export_is_deterministic(tmp_path) -> None:
    distribution = default_fixture_spectral_distribution("smd")
    first_path = write_plant_spectral_response_artifact(
        tmp_path,
        _surface_flux_payload(),
        default_leafy_green_spectral_bands(),
        distribution,
    )
    first = first_path.read_text(encoding="utf-8")
    second_path = write_plant_spectral_response_artifact(
        tmp_path,
        _surface_flux_payload(),
        default_leafy_green_spectral_bands(),
        distribution,
    )
    second = second_path.read_text(encoding="utf-8")

    assert first_path == second_path
    assert first == second
    payload = json.loads(first)
    assert payload["source_artifact"] == "runtime_state/plant_surface_flux.json"
    assert payload["outputs_do_not_predict"] == ["yield", "biomass", "growth", "crop_output"]
