from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import Response

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.assembly.scene import AssemblySceneError, build_assembly_scene  # noqa: E402
from rad_rebuild.radiance.backend.models import AssemblySceneResponse, RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.backend.routes import assembly as assembly_route  # noqa: E402
from rad_rebuild.radiance.backend.workspace import (  # noqa: E402
    allocate_workspace_for_run,
    commit_staged_workspace,
)
from rad_rebuild.radiance.config import EXECUTION_MODE_LIVE_DOCKER, MODE_COMPETITOR, MODE_HPS, MODE_SMD  # noqa: E402
from rad_rebuild.radiance.engine.plants import PlantGeometryConfig, write_plant_artifacts  # noqa: E402
from rad_rebuild.radiance.engine.plants.photoreceptor import PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA  # noqa: E402
from rad_rebuild.radiance.engine.plants.photosynthesis import PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA  # noqa: E402
from rad_rebuild.radiance.engine.plants.spectral import PLANT_SPECTRAL_RESPONSE_SCHEMA  # noqa: E402
from rad_rebuild.radiance.engine.plants.spectral_absorption import PLANT_SPECTRAL_ABSORPTION_SCHEMA  # noqa: E402
from rad_rebuild.radiance.engine.plants.surface_flux import PLANT_SURFACE_FLUX_SCHEMA  # noqa: E402


class _FakeRequest:
    headers: dict[str, str]

    def __init__(
        self,
        *,
        session_id: str = "assembly-scene",
        token: str | None = None,
        method: str = "GET",
    ) -> None:
        self.query_params = {"session_id": session_id}
        if token is not None:
            self.query_params["artifact_token"] = token
        self.headers = {}
        self.method = method


def _request(
    *,
    session_id: str = "assembly-scene",
    token: str | None = None,
    method: str = "GET",
) -> Request:
    return cast(Request, _FakeRequest(session_id=session_id, token=token, method=method))


def _smd_req(**overrides: object) -> RadianceRunRequest:
    data: dict[str, object] = {
        "action": "all",
        "mode": MODE_SMD,
        "execution_mode": EXECUTION_MODE_LIVE_DOCKER,
        "length_ft": 10,
        "width_ft": 10,
        "target_ppfd": 1000,
        "mount_z_m": 0.4572,
    }
    data.update(overrides)
    return RadianceRunRequest.model_validate(data)


def _competitor_req(**overrides: object) -> RadianceRunRequest:
    data: dict[str, object] = {
        "action": "all",
        "mode": MODE_COMPETITOR,
        "execution_mode": EXECUTION_MODE_LIVE_DOCKER,
        "length_ft": 10,
        "width_ft": 10,
        "target_ppfd": 1000,
        "w_min": 10,
        "competitor_layout": "full",
    }
    data.update(overrides)
    return RadianceRunRequest.model_validate(data)


def _hps_req(**overrides: object) -> RadianceRunRequest:
    data: dict[str, object] = {
        "action": "all",
        "mode": MODE_HPS,
        "execution_mode": EXECUTION_MODE_LIVE_DOCKER,
        "length_ft": 10,
        "width_ft": 10,
        "target_ppfd": 1000,
        "w_min": 10,
        "hps_coverage_ft": 4,
        "hps_ies_variant": "karma",
    }
    data.update(overrides)
    return RadianceRunRequest.model_validate(data)


def _layout_payload() -> dict[str, Any]:
    return {
        "version": 2,
        "units": "meters",
        "room": {"L": 3.048, "W": 3.048},
        "z": 0.4572,
        "positions": [
            {"x": 0.0, "y": 0.0, "z": 0.4572, "ring": 0},
            {"x": 0.2, "y": 0.0, "z": 0.4572, "ring": 0},
        ],
        "fixture_groups": [
            {
                "type": "linear3",
                "orient": "o1",
                "points": [
                    {"x": 0.0, "y": 0.0, "z": 0.4572},
                    {"x": 0.2, "y": 0.0, "z": 0.4572},
                ],
            }
        ],
    }


def _competitor_layout_payload() -> dict[str, Any]:
    return {
        "model": "QB-FSG-8B-7T660W",
        "model_label": "EnVision Qube QB-FSG-8B-7T660W IES+SPD comparator",
        "units": "meters",
        "layout_mode": "full",
        "layout_label": "Full",
        "nx": 1,
        "ny": 1,
        "z": 0.4572,
        "rot_deg": 0.0,
        "fixture_length_m": 1.19,
        "fixture_width_m": 1.087,
        "fixture_height_m": 0.108,
        "room": {"L": 3.048, "W": 3.048},
        "fixtures": [
            {
                "cx": 0.25,
                "cy": -0.5,
                "body_corners": [
                    [-0.345, 0.0435],
                    [0.845, 0.0435],
                    [0.845, -1.0435],
                    [-0.345, -1.0435],
                ],
            }
        ],
    }


def _hps_layout_payload() -> dict[str, Any]:
    return {
        "profile": "glh_karma_8_hps1000_research_primary_v1",
        "label": "1000W HPS",
        "units": "meters",
        "coverage_ft": 4.0,
        "z": 0.4572,
        "room": {"L": 3.048, "W": 3.048},
        "fixture_envelope_m": {"length": 0.798576, "width": 0.603504, "height": 0.0},
        "nx": 1,
        "ny": 1,
        "fixtures": [
            {
                "cx": -0.25,
                "cy": 0.5,
                "lamp_line": [[-0.33, 0.5, 0.4572], [-0.17, 0.5, 0.4572]],
                "body_corners": [
                    [-0.649288, 0.198248],
                    [0.149288, 0.198248],
                    [0.149288, 0.801752],
                    [-0.649288, 0.801752],
                ],
            }
        ],
    }


def _write_layout(workspace_root: Path, payload: dict[str, Any] | None = None) -> None:
    runtime = workspace_root / "runtime_state"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "smd_layout.json").write_text(
        json.dumps(payload if payload is not None else _layout_payload()),
        encoding="utf-8",
    )


def _write_competitor_layout(workspace_root: Path, payload: dict[str, Any] | None = None) -> None:
    runtime = workspace_root / "runtime_state"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "spydr3_layout.json").write_text(
        json.dumps(payload if payload is not None else _competitor_layout_payload()),
        encoding="utf-8",
    )


def _write_hps_layout(workspace_root: Path, payload: dict[str, Any] | None = None) -> None:
    runtime = workspace_root / "runtime_state"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "hps_layout.json").write_text(
        json.dumps(payload if payload is not None else _hps_layout_payload()),
        encoding="utf-8",
    )


def _write_minimal_workspace(workspace_root: Path) -> None:
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "ppfd_map.txt").write_text("0 0 0 1000\n", encoding="utf-8")
    (workspace_root / "room.rad").write_text("# room\n", encoding="utf-8")
    (workspace_root / "sensor_points.txt").write_text("0 0 0\n", encoding="utf-8")
    _write_layout(workspace_root)


def _write_minimal_workspace_for_mode(workspace_root: Path, mode: str) -> None:
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "ppfd_map.txt").write_text("0 0 0 1000\n", encoding="utf-8")
    (workspace_root / "room.rad").write_text("# room\n", encoding="utf-8")
    (workspace_root / "sensor_points.txt").write_text("0 0 0\n", encoding="utf-8")
    if mode == MODE_COMPETITOR:
        _write_competitor_layout(workspace_root)
    elif mode == MODE_HPS:
        _write_hps_layout(workspace_root)
    else:
        _write_layout(workspace_root)


def test_builder_returns_schema3_geometry_aware_fixture_instances(tmp_path: Path) -> None:
    _write_layout(tmp_path)

    scene = build_assembly_scene(tmp_path, _smd_req())

    AssemblySceneResponse.model_validate(scene)
    assert scene["schema_version"] == 3
    assert scene["system"] == "proposed_led_system"
    assert scene["mode"] == MODE_SMD
    assert scene["assets"] == {
        "manifest": "/static/viewer/proposed_led_system/manifest.json",
        "anchors": "/static/viewer/proposed_led_system/anchors.json",
    }
    assert scene["module_assets"]["linear2"]["high"] == (
        "/static/viewer/proposed_led_system/fixture_L2_linear.high.glb"
    )
    assert scene["room"]["length_m"] == 3.048
    assert scene["instances"][0]["id"] == "fixture-0001"
    assert scene["instances"][0] == {
        "id": "fixture-0001",
        "layout_type": "linear3",
        "orient": "o1",
        "module_count": 2,
        "shape": "linear",
        "asset_key": "linear2",
        "asset_fallback_key": None,
        "points": [
            {"x": 0.0, "y": 0.0, "z": 0.4572},
            {"x": 0.2, "y": 0.0, "z": 0.4572},
        ],
        "warnings": [],
    }
    assert scene["fixture_counts_by_layout_type"] == {"linear3": 1}
    assert scene["fixture_counts_by_asset_key"] == {"linear2": 1}
    assert scene["missing_asset_keys"] == []
    assert scene["asset_fallbacks_used"] == []
    assert str(tmp_path) not in json.dumps(scene)
    assert "plants" not in scene
    assert "fspm_metrics" not in scene


def test_builder_attaches_optional_plant_viewer_payload(tmp_path: Path) -> None:
    _write_layout(tmp_path)
    write_plant_artifacts(
        tmp_path / "runtime_state",
        PlantGeometryConfig(
            seed=13,
            plant_grid_rows=1,
            plant_grid_columns=1,
            leaf_count_per_plant=4,
        ),
    )

    scene = build_assembly_scene(tmp_path, _smd_req(plants_enabled=True, plant_seed=13, plant_rows=1, plant_columns=1, plant_leaf_count=4))

    AssemblySceneResponse.model_validate(scene)
    assert scene["plants"]["schema"] == "rad_rebuild.fspm.plants.viewer.v1"
    assert scene["plants"]["config"]["seed"] == 13
    assert len(scene["plants"]["plants"]) == 1
    assert len(scene["plants"]["plants"][0]["leaves"]) == 4
    assert scene["plants"]["plants"][0]["plant_id"] == "plant_r000_c000"
    assert scene["plants"]["plants"][0]["leaves"][0]["leaf_id"] == "plant_r000_c000_leaf_000"
    assert scene["fspm_metrics"]["schema"] == "rad_rebuild.fspm.viewer_panel.v1"
    assert scene["fspm_metrics"]["counts"]["plant_count"] == 1
    assert scene["fspm_metrics"]["counts"]["leaf_count"] == 4
    assert str(tmp_path) not in json.dumps(scene)


def test_builder_attaches_sanitized_fspm_panel_metrics(tmp_path: Path) -> None:
    _write_layout(tmp_path)
    runtime = tmp_path / "runtime_state"
    write_plant_artifacts(
        runtime,
        PlantGeometryConfig(
            seed=17,
            plant_grid_rows=1,
            plant_grid_columns=1,
            leaf_count_per_plant=1,
        ),
    )
    (runtime / "plant_surface_flux.json").write_text(
        json.dumps(
            {
                "schema": PLANT_SURFACE_FLUX_SCHEMA,
                "schema_version": 1,
                "status": "computed",
                "method": "radiance_leaf_surface_receiver_v1",
                "plant_count": 1,
                "leaf_count": 1,
                "surface_count": 2,
                "one_sided_leaf_area_m2": 0.012,
                "target_ppfd_umol_m2_s": 275.0,
                "target_tolerance_umol_m2_s": 20.0,
                "target_classification_basis": "canopy_plane_equivalent_incident_ppfd",
                "target_classification_basis_label": "canopy-plane equivalent incident PPFD",
                "target_classification_source": "interpolated_runtime_ppfd_map",
                "target_range_leaf_count": 1,
                "under_lit_leaf_count": 0,
                "over_lit_leaf_count": 0,
                "target_classification_mean_ppfd_umol_m2_s": 275.0,
                "target_capped_incident_flux_total_umol_s": 3.3,
                "excess_incident_flux_above_target_umol_s": 0.0,
                "deficit_to_target_incident_flux_umol_s": 0.0,
                "plant_to_plant_target_capped_incident_flux_cv": 0.0,
                "lower_tail_target_classification_ppfd_umol_m2_s": 275.0,
                "target_capped_incident_mean_flux_density_umol_m2_s": 275.0,
                "target_capped_flux_total_umol_s": 3.3,
                "excess_flux_above_target_umol_s": 0.0,
                "under_target_deficit_umol_s": 0.0,
                "plant_to_plant_target_capped_flux_cv": 0.0,
                "lower_tail_raw_flux_density_umol_m2_s": 275.0,
                "target_capped_mean_flux_density_umol_m2_s": 275.0,
                "total_absorbed_photon_flux_umol_s": 6.0,
                "total_incident_photon_flux_umol_s": 8.57,
                "mean_absorbed_fraction_of_incident": 0.7,
                "plant_to_plant_absorbed_photon_flux_cv": 0.0,
                "leaf_summaries": [
                    {
                        "leaf_id": "plant_r000_c000_leaf_000",
                        "absorbed_photon_flux_density_umol_m2_s": 500.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (runtime / "plant_spectral_response.json").write_text(
        json.dumps(
            {
                "schema": PLANT_SPECTRAL_RESPONSE_SCHEMA,
                "schema_version": 1,
                "status": "computed",
                "method": "surface_flux_band_weighted_leaf_absorptance_v1",
                "plant_count": 1,
                "leaf_count": 1,
                "surface_count": 2,
                "total_absorbed_photon_flux_umol_s": 6.0,
                "total_absorbed_par_photon_flux_umol_s": 5.0,
                "band_totals": {
                    "blue": {"absorbed_photon_flux_umol_s": 1.0},
                    "green": {"absorbed_photon_flux_umol_s": 1.5},
                    "red": {"absorbed_photon_flux_umol_s": 2.5},
                    "far_red": {"absorbed_photon_flux_umol_s": 1.0},
                },
            }
        ),
        encoding="utf-8",
    )
    (runtime / "plant_photosynthesis_response.json").write_text(
        json.dumps(
            {
                "schema": PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA,
                "schema_version": 2,
                "status": "computed",
                "method": "absorbed_par_non_rectangular_hyperbola_v2",
                "method_version": "v2",
                "calibration_status": "uncalibrated_model_scaffold",
                "default_parameter_status": "unvalidated_defaults",
                "input_basis": "absorbed_par",
                "plant_count": 1,
                "leaf_count": 1,
                "surface_count": 2,
                "total_absorbed_par_photon_flux_umol_s": 5.0,
                "absorbed_par_area_weighted_mean_umol_m2_s": 416.7,
                "area_weighted_mean_local_response_0_1": 0.64,
                "equal_plant_mean_normalized_response_0_1": 0.64,
                "local_response_p10_0_1": 0.58,
                "bottom_decile_area_weighted_response_0_1": 0.58,
                "nonuniformity_response_retention_0_1": 0.97,
                "plant_to_plant_photosynthetic_response_cv": 0.0,
            }
        ),
        encoding="utf-8",
    )
    (runtime / "plant_photoreceptor_exposure.json").write_text(
        json.dumps(
            {
                "schema": PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA,
                "schema_version": 1,
                "status": "computed",
                "method": "spectral_band_exposure_inputs_v1",
                "plant_count": 1,
                "leaf_count": 1,
                "surface_count": 2,
                "phytochrome_pss_proxy": {"value": None, "status": "not_computed"},
                "blue_photon_dose": {"value_umol_m2": None, "status": "not_computed"},
                "plant_summaries": [
                    {
                        "absorbed_blue_pfd_umol_m2_s": 83.3,
                        "absorbed_green_pfd_umol_m2_s": 125.0,
                        "absorbed_red_pfd_umol_m2_s": 208.3,
                        "absorbed_far_red_pfd_umol_m2_s": 83.3,
                        "absorbed_blue_fraction_of_par": 0.2,
                        "absorbed_red_to_far_red_ratio_diagnostic": 2.5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (runtime / "plant_spectral_absorption.json").write_text(
        json.dumps(
            {
                "schema": PLANT_SPECTRAL_ABSORPTION_SCHEMA,
                "schema_version": 1,
                "status": "computed",
                "method": "wavelength_binned_leaf_optical_profile_absorption_v1",
                "optical_profile": {
                    "profile_id": "rex_green_butterhead_mature_leaf_optics_v1",
                    "profile_version": "v1",
                },
                "source_spectrum": {"distribution_id": "curve_data_smd"},
                "source_spectral_basis": "wavelength_resolved_spd",
                "scalar_flux_basis": "par_ppfd_umol_m2_s",
                "plant_count": 1,
                "leaf_count": 1,
                "surface_count": 2,
                "crop_summary": {
                    "scalar_incident_par_ppfd_umol_m2_s": 500.0,
                    "absorbed_par_ppfd_umol_m2_s": 320.0,
                    "absorbed_epar_ppfd_umol_m2_s": 338.0,
                    "absorbed_blue_ppfd_umol_m2_s": 60.0,
                    "absorbed_green_ppfd_umol_m2_s": 85.0,
                    "absorbed_orange_ppfd_umol_m2_s": 25.0,
                    "absorbed_red_ppfd_umol_m2_s": 150.0,
                    "absorbed_far_red_ppfd_umol_m2_s": 18.0,
                    "absorbed_fraction": 0.64,
                    "reflected_fraction": 0.24,
                    "transmitted_fraction": 0.12,
                },
            }
        ),
        encoding="utf-8",
    )

    scene = build_assembly_scene(tmp_path, _smd_req(plants_enabled=True, plant_seed=17, plant_rows=1, plant_columns=1, plant_leaf_count=1))

    panel = scene["fspm_metrics"]
    assert panel["incident_leaf_surface_flux"]["target_ppfd_umol_m2_s"] == 275.0
    assert panel["incident_leaf_surface_flux"]["artifact_role"] == "incident_leaf_surface_flux"
    assert panel["plant_surface_absorption"]["target_ppfd_umol_m2_s"] == 275.0
    assert (
        panel["plant_surface_absorption"]["target_classification_source"]
        == "interpolated_runtime_ppfd_map"
    )
    assert panel["plant_surface_absorption"]["target_range_leaf_count"] == 1
    assert panel["plant_surface_absorption"]["mean_absorbed_photon_flux_density_umol_m2_s"] == 500.0
    assert (
        panel["modeled_spectral_absorption"]["optical_profile_id"]
        == "rex_green_butterhead_mature_leaf_optics_v1"
    )
    assert panel["modeled_spectral_absorption"]["absorbed_par_ppfd_umol_m2_s"] == 320.0
    assert panel["modeled_spectral_absorption"]["absorbed_far_red_ppfd_umol_m2_s"] == 18.0
    assert panel["spectral_exposure"]["total_absorbed_par_photon_flux_umol_s"] == 5.0
    assert panel["photosynthetic_light_response_potential"]["local_response_p10_0_1"] == 0.58
    assert panel["photoreceptor_exposure"]["mean_absorbed_blue_pfd_umol_m2_s"] == 83.3
    assert "plants" in scene
    assert str(tmp_path) not in json.dumps(scene)


def test_builder_rejects_malformed_plant_viewer_payload(tmp_path: Path) -> None:
    _write_layout(tmp_path)
    runtime = tmp_path / "runtime_state"
    (runtime / "plants_viewer.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(AssemblySceneError) as raised:
        build_assembly_scene(tmp_path, _smd_req(plants_enabled=True))

    assert raised.value.status_code == 422
    assert raised.value.error == "invalid_assembly_scene"
    assert raised.value.message == "Plant viewer payload is malformed."


def test_builder_returns_conventional_single_fixture_instances(tmp_path: Path) -> None:
    _write_competitor_layout(tmp_path)

    scene = build_assembly_scene(tmp_path, _competitor_req())

    AssemblySceneResponse.model_validate(scene)
    assert scene["schema_version"] == 3
    assert scene["system"] == "conventional_led_system"
    assert scene["mode"] == MODE_COMPETITOR
    assert scene["placement_strategy"] == "single_fixture_center"
    assert scene["assets"] == {
        "manifest": "/static/viewer/conventional_led_system/manifest.json",
    }
    assert scene["module_assets"]["fixture"] == {
        "high": "/static/viewer/conventional_led_system/fixture.high.glb",
        "medium": "/static/viewer/conventional_led_system/fixture.medium.glb",
        "proxy": "/static/viewer/conventional_led_system/fixture.proxy.glb",
    }
    assert scene["instances"] == [
        {
            "id": "fixture-0001",
            "layout_type": "single_fixture_center",
            "orient": "yaw_deg:0",
            "module_count": 1,
            "shape": "fixture",
            "asset_key": "fixture",
            "asset_fallback_key": None,
            "points": [
                {"x": -0.345, "y": 0.0435, "z": 0.4572},
                {"x": 0.845, "y": 0.0435, "z": 0.4572},
                {"x": 0.845, "y": -1.0435, "z": 0.4572},
                {"x": -0.345, "y": -1.0435, "z": 0.4572},
            ],
            "position": {"x": 0.25, "y": -0.5, "z": 0.4572},
            "yaw_deg": 0.0,
            "layout_source": {
                "mode": MODE_COMPETITOR,
                "path": "runtime_state/spydr3_layout.json",
                "fixture_index": 0,
                "position_fields": ["cx", "cy", "z"],
                "orientation_source": "rot_deg",
            },
            "warnings": [],
        }
    ]
    assert scene["fixture_counts_by_asset_key"] == {"fixture": 1}
    assert scene["missing_asset_keys"] == []
    assert scene["asset_fallbacks_used"] == []
    assert str(tmp_path) not in json.dumps(scene)


def test_builder_returns_hps_single_fixture_instances(tmp_path: Path) -> None:
    _write_hps_layout(tmp_path)

    scene = build_assembly_scene(tmp_path, _hps_req())

    AssemblySceneResponse.model_validate(scene)
    assert scene["schema_version"] == 3
    assert scene["system"] == "hps_1000w_system"
    assert scene["mode"] == MODE_HPS
    assert scene["placement_strategy"] == "single_fixture_center"
    assert scene["assets"] == {
        "manifest": "/static/viewer/hps_1000w_system/manifest.json",
    }
    assert scene["module_assets"]["fixture"] == {
        "high": "/static/viewer/hps_1000w_system/fixture.high.glb",
        "medium": "/static/viewer/hps_1000w_system/fixture.medium.glb",
        "proxy": "/static/viewer/hps_1000w_system/fixture.proxy.glb",
    }
    instance = scene["instances"][0]
    assert instance["asset_key"] == "fixture"
    assert instance["position"] == {"x": -0.25, "y": 0.5, "z": 0.4572}
    assert instance["yaw_deg"] == 0.0
    assert instance["layout_source"]["orientation_source"] == "lamp_line"
    assert scene["fixture_counts_by_asset_key"] == {"fixture": 1}
    assert scene["missing_asset_keys"] == []
    assert str(tmp_path) not in json.dumps(scene)


def test_builder_rejects_missing_layout_cleanly(tmp_path: Path) -> None:
    with pytest.raises(AssemblySceneError) as raised:
        build_assembly_scene(tmp_path, _smd_req())

    assert raised.value.status_code == 404
    assert raised.value.error == "assembly_layout_not_found"


def test_builder_rejects_missing_conventional_layout_cleanly(tmp_path: Path) -> None:
    with pytest.raises(AssemblySceneError) as raised:
        build_assembly_scene(tmp_path, _competitor_req())

    assert raised.value.status_code == 404
    assert raised.value.error == "assembly_layout_not_found"
    assert raised.value.message == "Conventional LED assembly layout was not found."


def test_builder_rejects_malformed_hps_layout_cleanly(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime_state"
    runtime.mkdir()
    (runtime / "hps_layout.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(AssemblySceneError) as raised:
        build_assembly_scene(tmp_path, _hps_req())

    assert raised.value.status_code == 422
    assert raised.value.error == "invalid_assembly_scene"
    assert raised.value.message == "1000W HPS assembly layout is malformed."


def test_builder_rejects_malformed_conventional_fixture_cleanly(tmp_path: Path) -> None:
    layout = _competitor_layout_payload()
    layout["fixtures"][0].pop("body_corners")
    _write_competitor_layout(tmp_path, layout)

    with pytest.raises(AssemblySceneError) as raised:
        build_assembly_scene(tmp_path, _competitor_req())

    assert raised.value.status_code == 422
    assert "body_corners" in raised.value.message


def test_builder_rejects_unknown_mode_cleanly(tmp_path: Path) -> None:
    req = RadianceRunRequest.model_construct(mode="Experimental", length_ft=10, width_ft=10)

    with pytest.raises(AssemblySceneError) as raised:
        build_assembly_scene(tmp_path, req)

    assert raised.value.status_code == 422
    assert raised.value.error == "assembly_view_unsupported"
    assert "Experimental" in raised.value.message


def test_builder_reports_nonfinite_fixture_points_as_placeholder_warning(tmp_path: Path) -> None:
    layout = _layout_payload()
    layout["fixture_groups"][0]["points"][0]["x"] = float("nan")
    _write_layout(tmp_path, layout)

    scene = build_assembly_scene(tmp_path, _smd_req())

    assert scene["instances"][0]["asset_key"] == "placeholder"
    assert scene["missing_asset_keys"] == ["placeholder"]
    assert "must contain finite x, y, and z numbers" in scene["warnings"][0]


def test_builder_reports_missing_optional_linear3_corner_asset_as_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    (asset_root / "fixture_L3_linear.high.glb").write_bytes(b"glb")
    monkeypatch.setattr("rad_rebuild.radiance.assembly.scene.ASSET_ROOT_PATH", asset_root)
    _write_layout(
        tmp_path,
        {
            "version": 2,
            "units": "meters",
            "room": {"L": 3.048, "W": 3.048},
            "z": 0.4572,
            "fixture_groups": [
                {
                    "type": "linear3",
                    "orient": "o2",
                    "points": [
                        {"x": 0.0, "y": 0.0, "z": 0.4572},
                        {"x": 0.0, "y": 0.5, "z": 0.4572},
                        {"x": 0.5, "y": 0.5, "z": 0.4572},
                    ],
                }
            ],
        },
    )

    scene = build_assembly_scene(tmp_path, _smd_req())

    assert scene["instances"][0]["asset_key"] == "linear3_corner"
    assert scene["instances"][0]["asset_fallback_key"] == "linear3_linear"
    assert scene["missing_asset_keys"] == []
    assert scene["asset_fallbacks_used"] == [
        {
            "asset_key": "linear3_corner",
            "fallback_asset_key": "linear3_linear",
            "instance_ids": ["fixture-0001"],
            "reason": "optional fixture asset 'linear3_corner' is missing; using 'linear3_linear'.",
        }
    ]
    assert "optional fixture asset 'linear3_corner' is missing" in scene["warnings"][0]


def test_route_authorizes_committed_workspace_with_artifact_token() -> None:
    session_id = "assembly-token"
    req = _smd_req()
    lease = allocate_workspace_for_run(session_id, req)
    _write_minimal_workspace(lease.staging_workspace)
    commit_staged_workspace(lease, {"runtime": "assembly-test"}, req)

    response = assembly_route.radiance_assembly_scene(
        _request(session_id=session_id, token=lease.artifact_token),
        mode=MODE_SMD,
        execution_mode=EXECUTION_MODE_LIVE_DOCKER,
        length_ft=10,
        width_ft=10,
        target_ppfd=1000,
    )

    assert isinstance(response, dict)
    assert response["instances"][0]["asset_key"] == "linear2"
    assert response["fixture_counts_by_asset_key"] == {"linear2": 1}

    with pytest.raises(HTTPException) as missing:
        assembly_route.radiance_assembly_scene(
            _request(session_id=session_id),
            mode=MODE_SMD,
            execution_mode=EXECUTION_MODE_LIVE_DOCKER,
            length_ft=10,
            width_ft=10,
            target_ppfd=1000,
        )
    assert missing.value.status_code == 403


def test_route_authorizes_plant_enabled_workspace_with_matching_query() -> None:
    session_id = "assembly-plant-token"
    req = _smd_req(plants_enabled=True, plant_seed=31, plant_rows=1, plant_columns=1, plant_leaf_count=3)
    lease = allocate_workspace_for_run(session_id, req)
    _write_minimal_workspace(lease.staging_workspace)
    write_plant_artifacts(
        lease.staging_workspace / "runtime_state",
        PlantGeometryConfig(
            seed=31,
            plant_grid_rows=1,
            plant_grid_columns=1,
            leaf_count_per_plant=3,
        ),
    )
    commit_staged_workspace(lease, {"runtime": "assembly-plant-test"}, req)

    response = assembly_route.radiance_assembly_scene(
        _request(session_id=session_id, token=lease.artifact_token),
        mode=MODE_SMD,
        execution_mode=EXECUTION_MODE_LIVE_DOCKER,
        length_ft=10,
        width_ft=10,
        target_ppfd=1000,
        plants_enabled=True,
        plant_seed=31,
        plant_rows=1,
        plant_columns=1,
        plant_leaf_count=3,
    )

    assert isinstance(response, dict)
    assert response["plants"]["config"]["seed"] == 31
    assert len(response["plants"]["plants"][0]["leaves"]) == 3


@pytest.mark.parametrize(
    ("mode", "req", "expected_asset"),
    [
        (
            MODE_COMPETITOR,
            _competitor_req(),
            "/static/viewer/conventional_led_system/fixture.high.glb",
        ),
        (
            MODE_HPS,
            _hps_req(),
            "/static/viewer/hps_1000w_system/fixture.high.glb",
        ),
    ],
)
def test_route_returns_authorized_single_fixture_scene_for_supported_non_smd_modes(
    mode: str,
    req: RadianceRunRequest,
    expected_asset: str,
) -> None:
    session_id = f"assembly-token-{mode.lower().replace(' ', '-')}"
    lease = allocate_workspace_for_run(session_id, req)
    _write_minimal_workspace_for_mode(lease.staging_workspace, mode)
    commit_staged_workspace(lease, {"runtime": "assembly-test"}, req)

    response = assembly_route.radiance_assembly_scene(
        _request(session_id=session_id, token=lease.artifact_token),
        mode=mode,
        execution_mode=EXECUTION_MODE_LIVE_DOCKER,
        length_ft=10,
        width_ft=10,
        target_ppfd=1000,
        w_min=10,
        competitor_layout="full",
        hps_coverage_ft=4,
        hps_ies_variant="karma",
    )

    assert isinstance(response, dict)
    assert response["instances"][0]["asset_key"] == "fixture"
    assert response["module_assets"]["fixture"]["high"] == expected_asset
    assert response["fixture_counts_by_asset_key"] == {"fixture": 1}


def test_route_head_validates_scene_without_payload(tmp_path: Path) -> None:
    _write_layout(tmp_path)

    with patch.object(
        assembly_route,
        "authorize_workspace_from_request",
        return_value=tmp_path,
    ) as authorize:
        response = assembly_route.radiance_assembly_scene(
            _request(method="HEAD"),
            mode=MODE_SMD,
            execution_mode=EXECUTION_MODE_LIVE_DOCKER,
            length_ft=10,
            width_ft=10,
            target_ppfd=1000,
        )

    authorize.assert_called_once()
    assert isinstance(response, Response)
    assert response.status_code == 200
    assert response.body == b""


def test_route_missing_layout_fails_cleanly_after_authorization(tmp_path: Path) -> None:
    with patch.object(
        assembly_route,
        "authorize_workspace_from_request",
        return_value=tmp_path,
    ):
        with pytest.raises(HTTPException) as raised:
            assembly_route.radiance_assembly_scene(
                _request(),
                mode=MODE_SMD,
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
            )

    assert raised.value.status_code == 404
    assert raised.value.detail == {
        "error": "assembly_layout_not_found",
        "message": "SMD assembly layout was not found.",
    }
