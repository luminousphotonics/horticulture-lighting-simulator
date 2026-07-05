from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.backend.metrics import _metrics_payload_for_request  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.backend.routes.metrics import (  # noqa: E402
    _apply_metrics_plant_query_overrides,
)
from rad_rebuild.radiance.config import EXECUTION_MODE_LIVE_LOCAL, MODE_SMD  # noqa: E402
from rad_rebuild.radiance.engine.plants import PlantGeometryConfig, generate_plant_scene, write_plant_artifacts  # noqa: E402
from rad_rebuild.radiance.engine.plants.spectral_absorption import PLANT_SPECTRAL_ABSORPTION_SCHEMA  # noqa: E402
from rad_rebuild.radiance.engine.plants.surface_flux import write_spatial_proxy_plant_surface_flux_artifact  # noqa: E402


def _request(query: dict[str, str]) -> Any:
    return SimpleNamespace(query_params=query)


def _metrics_payload(req: RadianceRunRequest, workspace_root: Any) -> dict[str, Any]:
    return cast(dict[str, Any], _metrics_payload_for_request(req, workspace_root))


def _write_ppfd_map(workspace_root: Any) -> None:
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "ppfd_map.txt").write_text(
        "0 0 0 100\n1 0 0 120\n0 1 0 110\n1 1 0 130\n",
        encoding="utf-8",
    )


def test_metrics_payload_omits_plant_absorption_when_artifact_missing(tmp_path) -> None:
    _write_ppfd_map(tmp_path)
    req = RadianceRunRequest(
        action="metrics",
        execution_mode=EXECUTION_MODE_LIVE_LOCAL,
        plants_enabled=False,
    )

    payload = _metrics_payload(req, tmp_path)

    assert "plant_photon_absorption" not in payload["metrics"]


def test_smd_metrics_parse_emitted_photons_and_plane_utilization(tmp_path) -> None:
    _write_ppfd_map(tmp_path)
    runtime = tmp_path / "runtime_state"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "smd_summary.txt").write_text(
        "\n".join(
            [
                "SMD macro emitter summary:",
                "  total electrical input ≈ 100.0 W",
                (
                    "  run-average source PPE (pre-PMMA) ≈ 3.000 µmol/J "
                    "→ total source photons ≈ 3000 µmol/s"
                ),
                (
                    "  run-average wall-plug PPE (post-PMMA) ≈ 2.000 µmol/J "
                    "→ total emitted photons ≈ 2000 µmol/s"
                ),
            ]
        ),
        encoding="utf-8",
    )
    req = RadianceRunRequest(
        action="metrics",
        execution_mode=EXECUTION_MODE_LIVE_LOCAL,
        mode=MODE_SMD,
        length_ft=10,
        width_ft=10,
    )

    payload = _metrics_payload(req, tmp_path)
    metrics = payload["metrics"]

    assert metrics["watts_in"] == pytest.approx(100.0)
    assert metrics["ppf_emitted"] == pytest.approx(2000.0)
    assert metrics["capture_frac"] == pytest.approx(metrics["ppf_out"] / 2000.0)
    assert metrics["plane_utilization"] == pytest.approx(metrics["capture_frac"])


def test_metrics_payload_includes_scaffold_only_plant_absorption_summary(tmp_path) -> None:
    _write_ppfd_map(tmp_path)
    write_plant_artifacts(
        tmp_path / "runtime_state",
        PlantGeometryConfig(
            seed=11,
            plant_grid_rows=1,
            plant_grid_columns=1,
            leaf_count_per_plant=4,
        ),
        active_simulation_integration=True,
        provenance_phase="Phase 07",
    )
    req = RadianceRunRequest(
        action="metrics",
        execution_mode=EXECUTION_MODE_LIVE_LOCAL,
        plants_enabled=True,
        plant_seed=11,
        plant_rows=1,
        plant_columns=1,
        plant_leaf_count=4,
    )

    payload = _metrics_payload(req, tmp_path)
    scaffold = payload["metrics"]["plant_photon_absorption"]

    assert scaffold["status"] == "scaffold_only"
    assert scaffold["source_artifact"] == "runtime_state/plant_absorption_surfaces.json"
    assert scaffold["plant_count"] == 1
    assert scaffold["leaf_count"] == 4
    assert scaffold["surface_count"] > 0
    assert scaffold["one_sided_leaf_area_m2"] > 0
    assert scaffold["outputs_do_not_predict"] == [
        "yield",
        "biomass",
        "growth",
        "crop_output",
    ]
    assert "Incident leaf-surface flux requires plant_surface_flux.json" in scaffold["note"]


def test_metrics_route_plant_query_overrides_align_with_workspace_fingerprint() -> None:
    req = RadianceRunRequest(
        action="metrics",
        execution_mode=EXECUTION_MODE_LIVE_LOCAL,
        plants_enabled=False,
    )

    updated = _apply_metrics_plant_query_overrides(
        req,
        _request(
            {
                "plants_enabled": "true",
                "plant_seed": "31",
                "plant_rows": "1",
                "plant_columns": "2",
                "plant_leaf_count": "7",
                "plant_spacing_m": "0.33",
            }
        ),
    )

    assert updated.plants_enabled is True
    assert updated.plant_seed == 31
    assert updated.plant_rows == 1
    assert updated.plant_columns == 2
    assert updated.plant_leaf_count == 7
    assert updated.plant_spacing_m == pytest.approx(0.33)


def test_metrics_route_invalid_plant_query_is_400() -> None:
    req = RadianceRunRequest(
        action="metrics",
        execution_mode=EXECUTION_MODE_LIVE_LOCAL,
    )

    with pytest.raises(HTTPException) as exc_info:
        _apply_metrics_plant_query_overrides(
            req,
            _request({"plants_enabled": "true", "plant_leaf_count": "bad"}),
        )

    assert exc_info.value.status_code == 400


def test_metrics_payload_prefers_surface_flux_artifact_when_available(tmp_path) -> None:
    _write_ppfd_map(tmp_path)
    config = PlantGeometryConfig(
        seed=13,
        plant_grid_rows=1,
        plant_grid_columns=1,
        leaf_count_per_plant=4,
    )
    write_plant_artifacts(
        tmp_path / "runtime_state",
        config,
        active_simulation_integration=True,
        provenance_phase="Phase 09",
    )
    write_spatial_proxy_plant_surface_flux_artifact(
        tmp_path / "runtime_state",
        generate_plant_scene(config),
        ppfd_map_path=tmp_path / "ppfd_map.txt",
    )
    req = RadianceRunRequest(
        action="metrics",
        execution_mode=EXECUTION_MODE_LIVE_LOCAL,
        plants_enabled=True,
        plant_seed=13,
        plant_rows=1,
        plant_columns=1,
        plant_leaf_count=4,
    )

    payload = _metrics_payload(req, tmp_path)
    absorption = payload["metrics"]["plant_photon_absorption"]

    assert payload["metrics"]["plant_incident_surface_flux"] is absorption
    assert absorption["source_artifact"] == "runtime_state/plant_surface_flux.json"
    assert absorption["artifact_role"] == "incident_leaf_surface_flux"
    assert absorption["status"] == "proxy"
    assert absorption["method"] == "baseline_ppfd_spatial_interpolation_orientation_proxy_v1"
    assert absorption["target_ppfd_umol_m2_s"] == 275.0
    assert absorption["target_tolerance_umol_m2_s"] == 20.0
    assert absorption["target_classification_source"] == "interpolated_runtime_ppfd_map"
    assert "target_range_leaf_count" in absorption
    assert "target_capped_incident_flux_total_umol_s" in absorption
    assert absorption["total_absorbed_photon_flux_umol_s"] > 0
    assert absorption["legacy_broadband_absorbed_flux_total_umol_s"] == absorption[
        "total_absorbed_photon_flux_umol_s"
    ]
    assert "scalar optical-assumption diagnostics" in absorption["broadband_absorption_note"]
    assert absorption["plant_to_plant_absorbed_photon_flux_cv"] >= 0
    assert "leaf_summaries" not in absorption


def test_metrics_payload_includes_modeled_spectral_absorption_when_available(tmp_path) -> None:
    _write_ppfd_map(tmp_path)
    runtime = tmp_path / "runtime_state"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "plant_spectral_absorption.json").write_text(
        json.dumps(
            {
                "schema": PLANT_SPECTRAL_ABSORPTION_SCHEMA,
                "schema_version": 1,
                "status": "computed",
                "method": "wavelength_binned_leaf_optical_profile_absorption_v1",
                "source_surface_flux_method": "radiance_leaf_surface_receiver_sampling_v1",
                "optical_profile": {
                    "profile_id": "rex_green_butterhead_mature_leaf_optics_v1",
                    "profile_version": "v1",
                },
                "source_spectrum": {"distribution_id": "curve_data_smd"},
                "source_spectral_basis": "wavelength_resolved_spd",
                "scalar_flux_basis": "par_ppfd_umol_m2_s",
                "plant_count": 1,
                "leaf_count": 2,
                "surface_count": 4,
                "crop_summary": {
                    "scalar_incident_par_ppfd_umol_m2_s": 300.0,
                    "absorbed_par_ppfd_umol_m2_s": 190.0,
                    "absorbed_epar_ppfd_umol_m2_s": 204.0,
                    "absorbed_blue_ppfd_umol_m2_s": 38.0,
                    "absorbed_green_ppfd_umol_m2_s": 52.0,
                    "absorbed_orange_ppfd_umol_m2_s": 14.0,
                    "absorbed_red_ppfd_umol_m2_s": 86.0,
                    "absorbed_far_red_ppfd_umol_m2_s": 14.0,
                    "target_capped_absorbed_par_ppfd": 186.0,
                    "target_capped_absorbed_epar_ppfd": 199.0,
                    "target_capped_absorbed_par_fraction_of_raw": 0.979,
                    "target_effective_absorbed_fraction": 0.63,
                    "under_target_leaf_fraction": 0.5,
                    "in_target_leaf_fraction": 0.5,
                    "over_target_leaf_fraction": 0.0,
                    "absorbed_fraction": 0.63,
                    "reflected_fraction": 0.25,
                    "transmitted_fraction": 0.12,
                },
            }
        ),
        encoding="utf-8",
    )
    req = RadianceRunRequest(
        action="metrics",
        execution_mode=EXECUTION_MODE_LIVE_LOCAL,
        plants_enabled=True,
    )

    payload = _metrics_payload(req, tmp_path)
    spectral = payload["metrics"]["plant_spectral_absorption"]

    assert spectral["artifact_role"] == "modeled_spectral_leaf_photon_absorption"
    assert spectral["optical_profile_id"] == "rex_green_butterhead_mature_leaf_optics_v1"
    assert spectral["source_spectral_basis"] == "wavelength_resolved_spd"
    assert spectral["absorbed_par_ppfd_umol_m2_s"] == 190.0
    assert spectral["target_capped_absorbed_par_ppfd"] == 186.0
    assert spectral["target_capped_absorbed_par_fraction_of_raw"] == 0.979
    assert spectral["under_target_leaf_fraction"] == 0.5
    assert spectral["absorbed_far_red_ppfd_umol_m2_s"] == 14.0
