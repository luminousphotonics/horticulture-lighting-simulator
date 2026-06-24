from __future__ import annotations

import json

import pytest

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.engine.plants.photosynthesis import (  # noqa: E402
    PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA,
    PhotosynthesisResponseParameters,
    build_plant_photosynthesis_response_payload,
    photosynthetic_gross_rate_umol_co2_m2_s,
    photosynthetic_net_rate_umol_co2_m2_s,
    write_plant_photosynthesis_response_artifact,
)


def _spectral_payload() -> dict[str, object]:
    return {
        "schema": "rad_rebuild.fspm.plant_spectral_response.v1",
        "schema_version": 1,
        "status": "computed",
        "method": "surface_flux_band_weighted_leaf_absorptance_v1",
        "spectral_distribution": {
            "distribution_id": "test_distribution",
            "source": "test",
        },
        "plant_count": 2,
        "leaf_count": 2,
        "surface_count": 2,
        "leaf_summaries": [
            {
                "leaf_id": "plant_000_leaf_000",
                "plant_id": "plant_000",
                "leaf_index": 0,
                "surface_count": 1,
                "area_m2": 1.0,
                "absorbed_par_photon_flux_umol_s": 120.0,
                "absorbed_par_photon_flux_density_umol_m2_s": 120.0,
                "absorbed_blue_to_par_fraction": 0.2,
                "absorbed_red_to_far_red_ratio": 4.0,
            },
            {
                "leaf_id": "plant_001_leaf_000",
                "plant_id": "plant_001",
                "leaf_index": 0,
                "surface_count": 1,
                "area_m2": 1.0,
                "absorbed_par_photon_flux_umol_s": 900.0,
                "absorbed_par_photon_flux_density_umol_m2_s": 900.0,
                "absorbed_blue_to_par_fraction": 0.2,
                "absorbed_red_to_far_red_ratio": 4.0,
            },
        ],
    }


def test_photosynthetic_response_curve_is_monotonic_and_saturating() -> None:
    params = PhotosynthesisResponseParameters()

    low = photosynthetic_gross_rate_umol_co2_m2_s(100.0, params)
    mid = photosynthetic_gross_rate_umol_co2_m2_s(500.0, params)
    high = photosynthetic_gross_rate_umol_co2_m2_s(2000.0, params)

    assert low < mid < high
    assert high < params.max_gross_assimilation_umol_co2_m2_s


def test_net_response_subtracts_dark_respiration() -> None:
    params = PhotosynthesisResponseParameters(dark_respiration_umol_co2_m2_s=2.0)

    gross = photosynthetic_gross_rate_umol_co2_m2_s(500.0, params)
    net = photosynthetic_net_rate_umol_co2_m2_s(500.0, params)

    assert net == pytest.approx(gross - 2.0)


def test_photosynthesis_payload_uses_absorbed_par_density() -> None:
    payload = build_plant_photosynthesis_response_payload(_spectral_payload())

    assert payload["schema"] == PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA
    assert payload["status"] == "computed"
    assert payload["total_absorbed_par_photon_flux_umol_s"] == pytest.approx(1020.0)
    assert payload["leaf_summaries"][0]["photosynthetic_response_index_0_1"] < payload["leaf_summaries"][1]["photosynthetic_response_index_0_1"]
    assert payload["plant_to_plant_photosynthetic_response_cv"] > 0
    assert payload["visualization"]["leaf_values"][0]["visual_intensity_0_1"] < payload["visualization"]["leaf_values"][1]["visual_intensity_0_1"]


def test_photosynthesis_artifact_export_is_deterministic(tmp_path) -> None:
    first_path = write_plant_photosynthesis_response_artifact(tmp_path, _spectral_payload())
    first = first_path.read_text(encoding="utf-8")
    second_path = write_plant_photosynthesis_response_artifact(tmp_path, _spectral_payload())
    second = second_path.read_text(encoding="utf-8")

    assert first_path == second_path
    assert first == second
    data = json.loads(first)
    assert data["source_artifact"] == "runtime_state/plant_spectral_response.json"
    assert data["outputs_do_not_predict"] == ["yield", "biomass", "growth", "crop_output"]


def test_photosynthesis_response_inputs_do_not_claim_crop_output() -> None:
    payload = build_plant_photosynthesis_response_payload(_spectral_payload())
    response_text = json.dumps(
        {
            "leaf_summaries": payload["leaf_summaries"],
            "plant_summaries": payload["plant_summaries"],
            "visualization": payload["visualization"],
        },
        sort_keys=True,
    ).lower()

    for forbidden in ("yield", "biomass", "harvest_weight", "crop_output"):
        assert forbidden not in response_text
