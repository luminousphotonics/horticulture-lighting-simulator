from __future__ import annotations

import json
import math

import pytest

from rad_rebuild.radiance.engine.plants import (
    PlantGeometryConfig,
    build_absorption_surface_registry,
    compute_photon_absorption_metrics,
    export_scene_to_radiance,
    generate_plant_scene,
    leaf_absorption_surfaces,
    write_plant_artifacts,
)
from rad_rebuild.radiance.engine.plants.absorption import (
    PHOTON_ABSORPTION_METRICS_SCHEMA,
    PHOTON_ABSORPTION_SCAFFOLD_SCHEMA,
)
from rad_rebuild.radiance.engine.plants.artifacts import (
    PLANT_ABSORPTION_SURFACES_FILENAME,
    PLANTS_MANIFEST_FILENAME,
)
from rad_rebuild.radiance.engine.plants.mesh import LEAF_FACE_COUNT


def _single_plant_scene():
    return generate_plant_scene(
        PlantGeometryConfig(
            seed=7,
            plant_grid_rows=1,
            plant_grid_columns=1,
            leaf_count_per_plant=3,
        )
    )


def test_absorption_surfaces_match_deterministic_radiance_surface_ids() -> None:
    scene = _single_plant_scene()
    rad_text = export_scene_to_radiance(scene)
    surfaces = leaf_absorption_surfaces(scene)

    assert len(surfaces) == 3 * LEAF_FACE_COUNT
    assert all(surface.surface_id in rad_text for surface in surfaces)
    assert all(surface.plant_id == "plant_r000_c000" for surface in surfaces)
    assert all(surface.area_m2 > 0.0 for surface in surfaces)
    assert all(math.isfinite(surface.area_m2) for surface in surfaces)


def test_absorption_surface_registry_is_scaffold_only() -> None:
    registry = build_absorption_surface_registry(_single_plant_scene())

    assert registry["schema"] == PHOTON_ABSORPTION_SCAFFOLD_SCHEMA
    assert registry["status"] == "scaffold_only"
    assert registry["plant_count"] == 1
    assert registry["leaf_count"] == 3
    assert registry["surface_count"] == 3 * LEAF_FACE_COUNT
    assert registry["one_sided_leaf_area_m2"] > 0.0
    assert registry["outputs_do_not_predict"] == [
        "yield",
        "biomass",
        "growth",
        "crop_output",
    ]


def test_compute_photon_absorption_metrics_from_external_surface_flux() -> None:
    scene = _single_plant_scene()
    surfaces = leaf_absorption_surfaces(scene)
    flux = {surface.surface_id: 2.5 for surface in surfaces}

    metrics = compute_photon_absorption_metrics(scene, flux)

    expected_incident = 2.5 * len(surfaces)
    expected_absorbed = expected_incident * scene.config.optical.absorptance

    assert metrics["schema"] == PHOTON_ABSORPTION_METRICS_SCHEMA
    assert metrics["surface_count"] == len(surfaces)
    assert metrics["plant_count"] == 1
    assert metrics["leaf_count"] == 3
    assert metrics["total_incident_photon_flux_umol_s"] == pytest.approx(expected_incident)
    assert metrics["total_absorbed_photon_flux_umol_s"] == pytest.approx(expected_absorbed)
    assert metrics["mean_absorbed_fraction_of_incident"] == pytest.approx(
        scene.config.optical.absorptance
    )
    assert len(metrics["plant_summaries"]) == 1
    assert len(metrics["leaf_summaries"]) == 3
    assert metrics["outputs_do_not_predict"] == [
        "yield",
        "biomass",
        "growth",
        "crop_output",
    ]


def test_compute_photon_absorption_metrics_rejects_missing_unknown_and_invalid_flux() -> None:
    scene = _single_plant_scene()
    surfaces = leaf_absorption_surfaces(scene)
    complete = {surface.surface_id: 1.0 for surface in surfaces}

    missing = dict(complete)
    missing.pop(surfaces[0].surface_id)
    with pytest.raises(ValueError, match="Missing incident photon flux"):
        compute_photon_absorption_metrics(scene, missing)

    unknown = dict(complete)
    unknown["unknown_surface"] = 1.0
    with pytest.raises(ValueError, match="Unknown plant surface IDs"):
        compute_photon_absorption_metrics(scene, unknown)

    negative = dict(complete)
    negative[surfaces[0].surface_id] = -1.0
    with pytest.raises(ValueError, match="greater than or equal to zero"):
        compute_photon_absorption_metrics(scene, negative)

    nonfinite = dict(complete)
    nonfinite[surfaces[0].surface_id] = math.inf
    with pytest.raises(ValueError, match="must be finite"):
        compute_photon_absorption_metrics(scene, nonfinite)


def test_write_plant_artifacts_includes_absorption_surface_registry(tmp_path) -> None:
    paths = write_plant_artifacts(
        tmp_path,
        PlantGeometryConfig(seed=8, plant_grid_rows=1, plant_grid_columns=1),
    )

    assert paths.absorption_surfaces.name == PLANT_ABSORPTION_SURFACES_FILENAME
    registry = json.loads(paths.absorption_surfaces.read_text(encoding="utf-8"))
    assert registry["schema"] == PHOTON_ABSORPTION_SCAFFOLD_SCHEMA
    assert registry["status"] == "scaffold_only"

    manifest = json.loads((tmp_path / PLANTS_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    file_paths = {record["path"] for record in manifest["files"]}
    assert PLANT_ABSORPTION_SURFACES_FILENAME in file_paths
    assert manifest["artifact_filenames"]["absorption_surfaces"] == PLANT_ABSORPTION_SURFACES_FILENAME
