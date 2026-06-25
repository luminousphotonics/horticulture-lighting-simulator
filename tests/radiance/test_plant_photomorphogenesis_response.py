from __future__ import annotations

import json

import pytest

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.engine.plants.photomorphogenesis import (  # noqa: E402
    PLANT_PHOTOMORPHOGENESIS_RESPONSE_SCHEMA,
    PhotomorphogenesisResponseParameters,
    blue_compactness_response_index,
    build_plant_photomorphogenesis_response_payload,
    shade_avoidance_response_index,
    write_plant_photomorphogenesis_response_artifact,
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
                "absorbed_par_photon_flux_density_umol_m2_s": 300.0,
                "absorbed_blue_to_par_fraction": 0.30,
                "absorbed_red_to_far_red_ratio": 4.5,
                "transmitted_far_red_photon_flux_umol_s": 1.0,
            },
            {
                "leaf_id": "plant_001_leaf_000",
                "plant_id": "plant_001",
                "leaf_index": 0,
                "surface_count": 1,
                "area_m2": 1.0,
                "absorbed_par_photon_flux_density_umol_m2_s": 300.0,
                "absorbed_blue_to_par_fraction": 0.08,
                "absorbed_red_to_far_red_ratio": 1.1,
                "transmitted_far_red_photon_flux_umol_s": 10.0,
            },
        ],
    }


def _photosynthesis_payload() -> dict[str, object]:
    return {
        "schema": "rad_rebuild.fspm.plant_photosynthetic_light_response.v2",
        "schema_version": 2,
        "status": "computed",
        "method": "absorbed_par_non_rectangular_hyperbola_v2",
        "leaf_summaries": [
            {
                "leaf_id": "plant_000_leaf_000",
                "plant_id": "plant_000",
                "photosynthetic_response_index_0_1": 0.80,
            },
            {
                "leaf_id": "plant_001_leaf_000",
                "plant_id": "plant_001",
                "photosynthetic_response_index_0_1": 0.80,
            },
        ],
    }


def test_shade_avoidance_index_increases_as_red_far_red_ratio_drops() -> None:
    params = PhotomorphogenesisResponseParameters()

    low_ratio = shade_avoidance_response_index(1.1, params)
    high_ratio = shade_avoidance_response_index(4.5, params)

    assert low_ratio > high_ratio
    assert low_ratio > 0.9
    assert high_ratio == pytest.approx(0.0)


def test_blue_compactness_index_increases_with_blue_fraction() -> None:
    params = PhotomorphogenesisResponseParameters()

    low_blue = blue_compactness_response_index(0.08, params)
    high_blue = blue_compactness_response_index(0.30, params)

    assert high_blue > low_blue
    assert low_blue == pytest.approx(0.0)


def test_photomorphogenesis_payload_combines_spectral_and_photosynthesis_inputs() -> None:
    payload = build_plant_photomorphogenesis_response_payload(
        _spectral_payload(),
        _photosynthesis_payload(),
    )

    first_leaf = payload["leaf_summaries"][0]
    second_leaf = payload["leaf_summaries"][1]

    assert payload["schema"] == PLANT_PHOTOMORPHOGENESIS_RESPONSE_SCHEMA
    assert payload["status"] == "computed"
    assert payload["legacy_output"] is True
    assert payload["morphology_hypothesis_status"] == "legacy_heuristic_response_potential"
    assert payload["core_photoreceptor_exposure_artifact"] == (
        "runtime_state/plant_photoreceptor_exposure.json"
    )
    assert first_leaf["blue_compactness_response_index_0_1"] > second_leaf["blue_compactness_response_index_0_1"]
    assert second_leaf["shade_avoidance_response_index_0_1"] > first_leaf["shade_avoidance_response_index_0_1"]
    assert payload["shade_avoidance_leaf_count"] == 1
    assert payload["visualization"]["leaf_values"][0]["visual_intensity_0_1"] != payload["visualization"]["leaf_values"][1]["visual_intensity_0_1"]


def test_photomorphogenesis_artifact_export_is_deterministic(tmp_path) -> None:
    first_path = write_plant_photomorphogenesis_response_artifact(
        tmp_path,
        _spectral_payload(),
        _photosynthesis_payload(),
    )
    first = first_path.read_text(encoding="utf-8")
    second_path = write_plant_photomorphogenesis_response_artifact(
        tmp_path,
        _spectral_payload(),
        _photosynthesis_payload(),
    )
    second = second_path.read_text(encoding="utf-8")

    assert first_path == second_path
    assert first == second
    data = json.loads(first)
    assert data["source_artifacts"]["spectral_response"] == "runtime_state/plant_spectral_response.json"
    assert data["outputs_do_not_predict"] == ["yield", "biomass", "growth", "crop_output"]


def test_photomorphogenesis_response_inputs_do_not_claim_crop_output() -> None:
    payload = build_plant_photomorphogenesis_response_payload(
        _spectral_payload(),
        _photosynthesis_payload(),
    )
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
