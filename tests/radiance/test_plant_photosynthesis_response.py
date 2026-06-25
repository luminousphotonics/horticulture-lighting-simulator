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


def _surface_summary(
    surface_id: str,
    plant_id: str,
    leaf_id: str,
    *,
    area_m2: float,
    absorbed_par_density: float,
    leaf_index: int = 0,
    face_index: int = 0,
) -> dict[str, object]:
    return {
        "surface_id": surface_id,
        "plant_id": plant_id,
        "leaf_id": leaf_id,
        "leaf_index": leaf_index,
        "face_index": face_index,
        "area_m2": area_m2,
        "absorbed_par_photon_flux_umol_s": area_m2 * absorbed_par_density,
        "absorbed_par_photon_flux_density_umol_m2_s": absorbed_par_density,
        "absorbed_blue_to_par_fraction": 0.2,
        "absorbed_red_to_far_red_ratio": 4.0,
    }


def _spectral_payload_from_surfaces(
    surfaces: list[dict[str, object]],
) -> dict[str, object]:
    plant_ids = {
        str(row["plant_id"])
        for row in surfaces
    }
    leaf_ids = {
        str(row["leaf_id"])
        for row in surfaces
    }
    return {
        "schema": "rad_rebuild.fspm.plant_spectral_response.v1",
        "schema_version": 1,
        "status": "computed",
        "method": "surface_flux_band_weighted_leaf_absorptance_v1",
        "spectral_distribution": {
            "distribution_id": "test_distribution",
            "source": "test",
        },
        "plant_count": len(plant_ids),
        "leaf_count": len(leaf_ids),
        "surface_count": len(surfaces),
        "surface_summaries": surfaces,
    }


def _spectral_payload() -> dict[str, object]:
    return _spectral_payload_from_surfaces(
        [
            _surface_summary(
                "plant_000_leaf_000_face_0000",
                "plant_000",
                "plant_000_leaf_000",
                area_m2=1.0,
                absorbed_par_density=120.0,
            ),
            _surface_summary(
                "plant_001_leaf_000_face_0000",
                "plant_001",
                "plant_001_leaf_000",
                area_m2=1.0,
                absorbed_par_density=900.0,
            ),
        ],
    )


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
    assert payload["schema_version"] == 2
    assert payload["method_version"] == 2
    assert payload["status"] == "computed"
    assert payload["total_absorbed_par_photon_flux_umol_s"] == pytest.approx(1020.0)
    assert len(payload["surface_summaries"]) == 2
    assert 0.0 <= payload["area_weighted_mean_local_response_0_1"] <= 1.0
    assert 0.0 <= payload["equal_plant_mean_normalized_response_0_1"] <= 1.0
    assert 0.0 <= payload["local_response_p10_0_1"] <= 1.0
    assert 0.0 <= payload["bottom_decile_area_weighted_response_0_1"] <= 1.0
    assert payload["compensation_reference_absorbed_par_umol_m2_s"] > 0.0
    assert payload["area_fraction_below_compensation_reference"] is not None
    assert 0.0 <= payload["area_fraction_near_saturation_range"] <= 1.0
    assert payload["area_fraction_above_profile_valid_range"] is None
    assert payload["leaf_summaries"][0]["photosynthetic_response_index_0_1"] < payload["leaf_summaries"][1]["photosynthetic_response_index_0_1"]
    assert payload["plant_to_plant_photosynthetic_response_cv"] > 0
    assert payload["visualization"]["leaf_values"][0]["visual_intensity_0_1"] < payload["visualization"]["leaf_values"][1]["visual_intensity_0_1"]


def test_photosynthesis_payload_declares_v2_contract_metadata() -> None:
    payload = build_plant_photosynthesis_response_payload(_spectral_payload())

    assert payload["contract"]["schema"] == PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA
    assert payload["contract"]["method_version"] == 2
    assert payload["calibration_status"] == "uncalibrated_model_scaffold"
    assert payload["default_parameter_status"] == "unvalidated_default_parameters"
    assert payload["target_model"] == {
        "id": "lettuce_rosette_archetype_v1",
        "archetype": "generic lettuce / leafy-green rosette archetype",
        "cultivar_specific": False,
    }
    assert payload["input_basis"] == {
        "source_artifact": "runtime_state/plant_spectral_response.json",
        "driver": "surface absorbed_par_photon_flux_density_umol_m2_s",
        "basis": "surface_absorbed_PAR_from_leaf_spectral_response",
    }
    assert payload["evidence_quality_tier"] == "unvalidated_default"
    assert payload["uncertainty_notes"]
    assert "biological prediction" in payload["non_prediction_framing"]


def test_photosynthesis_v2_contract_metadata_avoids_prohibited_crop_claims() -> None:
    payload = build_plant_photosynthesis_response_payload(_spectral_payload())
    contract_text = json.dumps(
        {
            "contract": payload["contract"],
            "calibration_status": payload["calibration_status"],
            "default_parameter_status": payload["default_parameter_status"],
            "target_model": payload["target_model"],
            "input_basis": payload["input_basis"],
            "evidence_quality_tier": payload["evidence_quality_tier"],
            "uncertainty_notes": payload["uncertainty_notes"],
            "non_prediction_framing": payload["non_prediction_framing"],
        },
        sort_keys=True,
    ).lower()

    for forbidden in ("yield", "biomass", "harvest", "crop-output", "growth prediction"):
        assert forbidden not in contract_text


def test_hotspot_same_mean_scores_no_higher_than_uniform_local_response() -> None:
    uniform = build_plant_photosynthesis_response_payload(
        _spectral_payload_from_surfaces(
            [
                _surface_summary("uniform_a", "plant_000", "plant_000_leaf_000", area_m2=1.0, absorbed_par_density=500.0),
                _surface_summary("uniform_b", "plant_000", "plant_000_leaf_000", area_m2=1.0, absorbed_par_density=500.0),
            ],
        ),
    )
    hotspot = build_plant_photosynthesis_response_payload(
        _spectral_payload_from_surfaces(
            [
                _surface_summary("hotspot_a", "plant_000", "plant_000_leaf_000", area_m2=1.0, absorbed_par_density=0.0),
                _surface_summary("hotspot_b", "plant_000", "plant_000_leaf_000", area_m2=1.0, absorbed_par_density=1000.0),
            ],
        ),
    )

    assert hotspot["absorbed_par_area_weighted_mean_umol_m2_s"] == pytest.approx(
        uniform["absorbed_par_area_weighted_mean_umol_m2_s"]
    )
    assert hotspot["area_weighted_mean_local_response_0_1"] <= uniform[
        "area_weighted_mean_local_response_0_1"
    ]
    assert hotspot["nonuniformity_response_retention_0_1"] <= uniform[
        "nonuniformity_response_retention_0_1"
    ]
    assert uniform["nonuniformity_response_retention_0_1"] == pytest.approx(1.0)


def test_uniform_surface_subdivision_preserves_aggregate_response() -> None:
    single_surface = build_plant_photosynthesis_response_payload(
        _spectral_payload_from_surfaces(
            [
                _surface_summary(
                    "single",
                    "plant_000",
                    "plant_000_leaf_000",
                    area_m2=2.0,
                    absorbed_par_density=400.0,
                ),
            ],
        ),
    )
    split_surface = build_plant_photosynthesis_response_payload(
        _spectral_payload_from_surfaces(
            [
                _surface_summary("split_a", "plant_000", "plant_000_leaf_000", area_m2=1.0, absorbed_par_density=400.0),
                _surface_summary("split_b", "plant_000", "plant_000_leaf_000", area_m2=1.0, absorbed_par_density=400.0),
            ],
        ),
    )

    for key in (
        "total_absorbed_par_photon_flux_umol_s",
        "total_gross_photosynthetic_potential_umol_co2_s",
        "total_net_photosynthetic_potential_umol_co2_s",
        "total_clipped_net_photosynthetic_potential_umol_co2_s",
        "area_weighted_mean_local_response_0_1",
        "nonuniformity_response_retention_0_1",
    ):
        assert split_surface[key] == pytest.approx(single_surface[key])

    assert split_surface["leaf_summaries"][0]["photosynthetic_response_index_0_1"] == pytest.approx(
        single_surface["leaf_summaries"][0]["photosynthetic_response_index_0_1"]
    )


def test_plant_to_plant_cv_uses_normalized_response_not_total_size() -> None:
    payload = build_plant_photosynthesis_response_payload(
        _spectral_payload_from_surfaces(
            [
                _surface_summary("small", "plant_000", "plant_000_leaf_000", area_m2=1.0, absorbed_par_density=500.0),
                _surface_summary("large", "plant_001", "plant_001_leaf_000", area_m2=4.0, absorbed_par_density=500.0),
            ],
        ),
    )

    assert payload["plant_to_plant_photosynthetic_response_cv"] == pytest.approx(0.0)
    assert payload["plant_to_plant_normalized_response_cv"] == pytest.approx(0.0)
    assert payload["plant_to_plant_total_clipped_net_potential_cv"] > 0.0
    assert payload["equal_plant_mean_normalized_response_0_1"] == pytest.approx(
        payload["area_weighted_mean_local_response_0_1"]
    )


def test_photosynthesis_payload_accepts_legacy_leaf_only_spectral_summary() -> None:
    payload = build_plant_photosynthesis_response_payload(
        {
            "schema": "rad_rebuild.fspm.plant_spectral_response.v1",
            "schema_version": 1,
            "status": "computed",
            "method": "surface_flux_band_weighted_leaf_absorptance_v1",
            "plant_count": 1,
            "leaf_count": 1,
            "surface_count": 1,
            "leaf_summaries": [
                {
                    "leaf_id": "plant_000_leaf_000",
                    "plant_id": "plant_000",
                    "leaf_index": 0,
                    "surface_count": 1,
                    "area_m2": 1.0,
                    "absorbed_par_photon_flux_umol_s": 300.0,
                    "absorbed_par_photon_flux_density_umol_m2_s": 300.0,
                },
            ],
        },
    )

    assert payload["surface_summaries"][0]["receiver_scope"] == "leaf_summary"
    assert payload["surface_summaries"][0]["surface_id"] == "plant_000_leaf_000__aggregate_receiver"


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
