from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.backend.metrics import _metrics_payload_for_request  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.backend.routes.metrics import (  # noqa: E402
    _apply_metrics_plant_query_overrides,
)
from rad_rebuild.radiance.config import EXECUTION_MODE_LIVE_LOCAL  # noqa: E402
from rad_rebuild.radiance.engine.plants import PlantGeometryConfig, generate_plant_scene, write_plant_artifacts  # noqa: E402
from rad_rebuild.radiance.engine.plants.surface_flux import write_spatial_proxy_plant_surface_flux_artifact  # noqa: E402


def _request(query: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(query_params=query)


def _write_ppfd_map(workspace_root) -> None:
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

    payload = _metrics_payload_for_request(req, tmp_path)

    assert "plant_photon_absorption" not in payload["metrics"]


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

    payload = _metrics_payload_for_request(req, tmp_path)
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
    assert "Absorbed photon flux values are not computed" in scaffold["note"]


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

    payload = _metrics_payload_for_request(req, tmp_path)
    absorption = payload["metrics"]["plant_photon_absorption"]

    assert absorption["source_artifact"] == "runtime_state/plant_surface_flux.json"
    assert absorption["status"] == "proxy"
    assert absorption["method"] == "baseline_ppfd_spatial_interpolation_orientation_proxy_v1"
    assert absorption["total_absorbed_photon_flux_umol_s"] > 0
    assert absorption["plant_to_plant_absorbed_photon_flux_cv"] >= 0
    assert absorption["leaf_summaries"]
