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
    PLANT_SPECTRAL_RESPONSE_METHOD_BANDED,
    build_banded_plant_spectral_response_payload,
    build_plant_spectral_response_payload,
    default_fixture_spectral_distribution,
    fixture_spectral_distribution_from_curve_data,
    parse_spectral_photon_fraction_overrides,
    write_banded_plant_spectral_response_artifact,
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


def _band_totals(
    *,
    blue: float,
    green: float,
    orange: float,
    red: float,
    far_red: float,
) -> dict[str, dict[str, float]]:
    totals = {
        "blue": {
            "incident_photon_flux_umol_s": 100.0,
            "absorbed_photon_flux_umol_s": blue,
            "reflected_photon_flux_umol_s": 10.0,
            "transmitted_photon_flux_umol_s": 5.0,
        },
        "green": {
            "incident_photon_flux_umol_s": 90.0,
            "absorbed_photon_flux_umol_s": green,
            "reflected_photon_flux_umol_s": 9.0,
            "transmitted_photon_flux_umol_s": 4.0,
        },
        "orange": {
            "incident_photon_flux_umol_s": 80.0,
            "absorbed_photon_flux_umol_s": orange,
            "reflected_photon_flux_umol_s": 8.0,
            "transmitted_photon_flux_umol_s": 3.0,
        },
        "red": {
            "incident_photon_flux_umol_s": 70.0,
            "absorbed_photon_flux_umol_s": red,
            "reflected_photon_flux_umol_s": 7.0,
            "transmitted_photon_flux_umol_s": 2.0,
        },
        "far_red": {
            "incident_photon_flux_umol_s": 60.0,
            "absorbed_photon_flux_umol_s": far_red,
            "reflected_photon_flux_umol_s": 6.0,
            "transmitted_photon_flux_umol_s": 20.0,
        },
    }
    totals["par"] = {
        key: sum(totals[band][key] for band in ("blue", "green", "orange", "red"))
        for key in totals["blue"]
    }
    totals["epar"] = {
        key: totals["par"][key] + totals["far_red"][key]
        for key in totals["blue"]
    }
    return totals


def _banded_spectral_absorption_payload() -> dict[str, object]:
    band_totals = _band_totals(
        blue=60.0,
        green=50.0,
        orange=30.0,
        red=40.0,
        far_red=25.0,
    )
    base = {
        "area_m2": 2.0,
        "scalar_incident_par_photon_flux_umol_s": 340.0,
        "total_incident_photon_flux_umol_s": 400.0,
        "total_absorbed_photon_flux_umol_s": 205.0,
        "total_reflected_photon_flux_umol_s": 40.0,
        "total_transmitted_photon_flux_umol_s": 34.0,
        "band_totals": band_totals,
    }
    return {
        "schema": "rad_rebuild.fspm.plant_spectral_absorption.v1",
        "schema_version": 1,
        "status": "computed",
        "method": "wavelength_binned_leaf_optical_profile_absorption_v1",
        "fspm_spectral_transport_mode": "banded_5",
        "source_surface_flux_schema": "rad_rebuild.fspm.plant_surface_flux.v1",
        "source_surface_flux_method": "radiance_leaf_surface_receiver_sampling_v1",
        "source_surface_flux_status": "computed",
        "plant_count": 1,
        "leaf_count": 1,
        "surface_count": 1,
        "par_band_ids": ["blue", "green", "orange", "red"],
        "epar_band_ids": ["blue", "green", "orange", "red", "far_red"],
        "source_spectrum": {
            "distribution_id": "test_banded_source",
            "source": "test",
            "normalization_basis": "par_integral",
        },
        "band_summaries": [
            {
                "band_id": band_id,
                "wavelength_min_nm": wavelength_min,
                "wavelength_max_nm": wavelength_max,
                "source_photon_fraction_relative_to_par": 0.2,
                "effective_reflectance": 0.1,
                "effective_transmittance": 0.05,
                "effective_absorptance": 0.85,
            }
            for band_id, wavelength_min, wavelength_max in (
                ("blue", 400, 499),
                ("green", 500, 599),
                ("orange", 600, 624),
                ("red", 625, 699),
                ("far_red", 700, 750),
            )
        ],
        "surface_summaries": [
            {
                "surface_id": "plant_000_leaf_000_face_0000",
                "plant_id": "plant_000",
                "leaf_id": "plant_000_leaf_000",
                "leaf_index": 0,
                "face_index": 0,
                "lighting_region": "nominal",
                **base,
            }
        ],
        "leaf_summaries": [
            {
                "leaf_id": "plant_000_leaf_000",
                "plant_id": "plant_000",
                "surface_count": 1,
                **base,
            }
        ],
        "plant_summaries": [
            {
                "plant_id": "plant_000",
                "surface_count": 1,
                **base,
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


def test_banded_spectral_response_uses_absorption_band_totals() -> None:
    payload = build_banded_plant_spectral_response_payload(
        _banded_spectral_absorption_payload()
    )
    leaf = payload["leaf_summaries"][0]

    assert payload["schema"] == PLANT_SPECTRAL_RESPONSE_SCHEMA
    assert payload["method"] == PLANT_SPECTRAL_RESPONSE_METHOD_BANDED
    assert payload["source_artifact"] == "runtime_state/plant_spectral_absorption.json"
    assert payload["source_data_basis"] == "banded_5_receiver_absorption"
    assert payload["total_absorbed_par_photon_flux_umol_s"] == pytest.approx(180.0)
    assert leaf["absorbed_par_photon_flux_umol_s"] == pytest.approx(180.0)
    assert leaf["absorbed_orange_photon_flux_umol_s"] == pytest.approx(30.0)
    assert leaf["absorbed_far_red_photon_flux_umol_s"] == pytest.approx(25.0)
    assert leaf["absorbed_par_photon_flux_density_umol_m2_s"] == pytest.approx(90.0)
    assert payload["band_totals"]["par"]["absorbed_photon_flux_umol_s"] == pytest.approx(
        180.0
    )
    assert payload["band_totals"]["far_red"]["absorbed_photon_flux_umol_s"] == pytest.approx(
        25.0
    )
    assert payload["spectral_distribution"]["distribution_id"] == "test_banded_source"


def test_banded_spectral_response_artifact_export_is_compact(tmp_path) -> None:
    path, full_payload = write_banded_plant_spectral_response_artifact(
        tmp_path,
        _banded_spectral_absorption_payload(),
        return_payload=True,
    )
    compact = json.loads(path.read_text(encoding="utf-8"))

    assert full_payload["leaf_summaries"][0]["absorbed_orange_photon_flux_umol_s"] > 0.0
    assert compact["method"] == PLANT_SPECTRAL_RESPONSE_METHOD_BANDED
    assert compact["source_artifact"] == "runtime_state/plant_spectral_absorption.json"
    assert compact["band_totals"]["orange"]["absorbed_photon_flux_umol_s"] > 0.0
    assert "surface_summaries" not in compact
    assert "leaf_summaries" not in compact
    assert "plant_summaries" not in compact
    assert "visualization" not in compact


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
    assert "surface_summaries" not in payload
    assert "leaf_summaries" not in payload
    assert "plant_summaries" not in payload
    assert "visualization" not in payload
    assert payload["outputs_do_not_predict"] == ["yield", "biomass", "growth", "crop_output"]



def test_fixture_spectral_distribution_loads_mode_curve_data(tmp_path) -> None:
    curve_root = tmp_path / "curve_data"
    smd = curve_root / "smd"
    conventional = curve_root / "conventional"
    hps = curve_root / "hps"
    smd.mkdir(parents=True)
    conventional.mkdir(parents=True)
    hps.mkdir(parents=True)

    (smd / "smd_combined_spd.csv").write_text(
        "wavelength_nm,relative_power\n450,1\n660,3\n720,0.2\n",
        encoding="utf-8",
    )
    (conventional / "conventional_spd.csv").write_text(
        "wavelength_nm,relative_power\n450,1\n660,1\n720,1\n",
        encoding="utf-8",
    )
    (hps / "hps_spd.csv").write_text(
        "wavelength_nm,relative_power\n450,0.1\n590,1\n660,2\n720,2\n",
        encoding="utf-8",
    )

    smd_distribution = fixture_spectral_distribution_from_curve_data(curve_root, "smd")
    conventional_distribution = fixture_spectral_distribution_from_curve_data(curve_root, "conventional")
    hps_distribution = fixture_spectral_distribution_from_curve_data(curve_root, "1000W HPS")

    assert smd_distribution.distribution_id == "curve_data_smd"
    assert conventional_distribution.distribution_id == "curve_data_conventional"
    assert hps_distribution.distribution_id == "curve_data_hps"
    assert smd_distribution.source.startswith("curve_data_spd:")
    assert hps_distribution.fraction_map()["far_red"] > smd_distribution.fraction_map()["far_red"]


def test_fixture_spectral_distribution_ignores_non_spectral_curve_rows(tmp_path) -> None:
    curve_root = tmp_path / "curve_data"
    smd = curve_root / "smd"
    smd.mkdir(parents=True)

    (smd / "white_ppe_vs_fC.csv").write_text(
        "fC,ppe\n0.5,2.7\n1.0,2.8\n",
        encoding="utf-8",
    )
    (smd / "real_spd.csv").write_text(
        "wavelength_nm,relative_power\n450,1\n660,1\n",
        encoding="utf-8",
    )

    distribution = fixture_spectral_distribution_from_curve_data(curve_root, "smd")

    assert distribution.distribution_id == "curve_data_smd"
    assert "real_spd.csv" in distribution.source
    assert "white_ppe_vs_fC.csv" not in distribution.source


def test_fixture_spectral_distribution_falls_back_when_curve_data_missing(tmp_path) -> None:
    distribution = fixture_spectral_distribution_from_curve_data(
        tmp_path / "missing_curve_data",
        "1000W HPS",
    )

    assert distribution.distribution_id == "development_default_hps"
