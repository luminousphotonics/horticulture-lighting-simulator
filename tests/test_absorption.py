from __future__ import annotations

import math

import pytest

from fspm_optics.plants import (
    PlantGeometryConfig,
    build_absorption_surface_registry,
    compute_photon_absorption_metrics,
    export_scene_to_radiance,
    generate_plant_scene,
    leaf_absorption_surfaces,
)
from fspm_optics.plants.absorption import (
    PHOTON_ABSORPTION_METRICS_SCHEMA,
    PHOTON_ABSORPTION_SCAFFOLD_SCHEMA,
)
from fspm_optics.plants.mesh import LEAF_FACE_COUNT


def _scene():
    return generate_plant_scene(
        PlantGeometryConfig(
            seed=7,
            plant_grid_rows=1,
            plant_grid_columns=1,
            leaf_count_per_plant=3,
        )
    )


def test_absorption_surfaces_match_polygon_ids_and_geometry() -> None:
    scene = _scene()
    text = export_scene_to_radiance(scene)
    surfaces = leaf_absorption_surfaces(scene)
    assert len(surfaces) == 3 * LEAF_FACE_COUNT
    assert all(surface.surface_id in text for surface in surfaces)
    assert all(surface.plant_id == "plant_r000_c000" for surface in surfaces)
    assert all(surface.area_m2 > 0.0 and math.isfinite(surface.area_m2) for surface in surfaces)
    assert all(surface.absorptance == scene.config.optical.absorptance for surface in surfaces)


def test_absorption_surface_registry_is_scaffold_only() -> None:
    registry = build_absorption_surface_registry(_scene())
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


def test_compute_absorption_metrics_from_external_surface_flux() -> None:
    scene = _scene()
    surfaces = leaf_absorption_surfaces(scene)
    metrics = compute_photon_absorption_metrics(
        scene,
        {surface.surface_id: 2.5 for surface in surfaces},
    )
    expected_incident = 2.5 * len(surfaces)
    assert metrics["schema"] == PHOTON_ABSORPTION_METRICS_SCHEMA
    assert metrics["surface_count"] == len(surfaces)
    assert metrics["plant_count"] == 1
    assert metrics["leaf_count"] == 3
    assert metrics["total_incident_photon_flux_umol_s"] == pytest.approx(expected_incident)
    assert metrics["total_absorbed_photon_flux_umol_s"] == pytest.approx(
        expected_incident * scene.config.optical.absorptance
    )
    assert metrics["mean_absorbed_fraction_of_incident"] == pytest.approx(
        scene.config.optical.absorptance
    )
    assert len(metrics["plant_summaries"]) == 1
    assert len(metrics["leaf_summaries"]) == 3


def test_absorption_metrics_reject_incomplete_unknown_and_invalid_flux() -> None:
    scene = _scene()
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
