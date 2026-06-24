from __future__ import annotations

import json

import pytest

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.engine.plants import PlantGeometryConfig, generate_plant_scene  # noqa: E402
from rad_rebuild.radiance.engine.plants.surface_flux import (  # noqa: E402
    BASELINE_PPFD_PROXY_METHOD,
    PLANT_SURFACE_FLUX_SCHEMA,
    build_baseline_proxy_surface_flux_rows,
    build_plant_surface_flux_payload,
    write_baseline_proxy_plant_surface_flux_artifact,
)


def _scene():
    return generate_plant_scene(
        PlantGeometryConfig(
            seed=19,
            plant_grid_rows=2,
            plant_grid_columns=1,
            leaf_count_per_plant=4,
        )
    )


def test_surface_flux_payload_aggregates_leaf_and_plant_absorption() -> None:
    scene = _scene()
    rows = build_baseline_proxy_surface_flux_rows(scene, 1000.0)
    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=BASELINE_PPFD_PROXY_METHOD,
        source_ppfd_map="ppfd_map.txt",
        baseline_ppfd_mean_umol_m2_s=1000.0,
    )

    assert payload["schema"] == PLANT_SURFACE_FLUX_SCHEMA
    assert payload["status"] == "proxy"
    assert payload["plant_count"] == 2
    assert payload["leaf_count"] == 8
    assert payload["surface_count"] == len(rows)
    assert payload["total_absorbed_photon_flux_umol_s"] > 0
    assert payload["plant_to_plant_absorbed_photon_flux_cv"] >= 0
    assert len(payload["plant_summaries"]) == 2
    assert len(payload["leaf_summaries"]) == 8
    assert all("plant_id" in row for row in payload["leaf_summaries"])
    assert all("leaf_index" in row for row in payload["leaf_summaries"])
    assert payload["visualization"]["color_metric"] == "absorbed_photon_flux_density_umol_m2_s"
    assert payload["visualization"]["leaf_values"]
    assert all("plant_id" in row for row in payload["visualization"]["leaf_values"])


def test_surface_flux_artifact_export_is_deterministic(tmp_path) -> None:
    scene = _scene()

    first_path = write_baseline_proxy_plant_surface_flux_artifact(
        tmp_path,
        scene,
        baseline_ppfd_mean_umol_m2_s=1000.0,
    )
    first = first_path.read_text(encoding="utf-8")
    second_path = write_baseline_proxy_plant_surface_flux_artifact(
        tmp_path,
        scene,
        baseline_ppfd_mean_umol_m2_s=1000.0,
    )
    second = second_path.read_text(encoding="utf-8")

    assert first_path == second_path
    assert first == second


def test_surface_flux_rejects_missing_surface_ids() -> None:
    scene = _scene()
    rows = build_baseline_proxy_surface_flux_rows(scene, 1000.0)

    with pytest.raises(ValueError, match="Missing incident photon flux"):
        build_plant_surface_flux_payload(
            scene,
            rows[:-1],
            method=BASELINE_PPFD_PROXY_METHOD,
        )


def test_surface_flux_rejects_invalid_leaf_ids() -> None:
    scene = _scene()
    rows = build_baseline_proxy_surface_flux_rows(scene, 1000.0)
    rows[0] = {**rows[0], "leaf_id": "not_the_surface_leaf"}

    with pytest.raises(ValueError, match="belongs to leaf_id"):
        build_plant_surface_flux_payload(
            scene,
            rows,
            method=BASELINE_PPFD_PROXY_METHOD,
        )


def test_surface_flux_summaries_do_not_use_crop_output_language() -> None:
    scene = _scene()
    rows = build_baseline_proxy_surface_flux_rows(scene, 1000.0)
    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=BASELINE_PPFD_PROXY_METHOD,
    )

    summary_text = json.dumps(
        {
            "plant_summaries": payload["plant_summaries"],
            "leaf_summaries": payload["leaf_summaries"],
            "visualization": payload["visualization"],
        },
        sort_keys=True,
    ).lower()

    for forbidden in ("yield", "biomass", "crop_output"):
        assert forbidden not in summary_text
