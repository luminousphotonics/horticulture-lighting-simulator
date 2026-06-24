"""Deterministic plant geometry core for FSPM groundwork."""

from __future__ import annotations

from rad_rebuild.radiance.engine.plants.absorption import (
    PHOTON_ABSORPTION_METRICS_SCHEMA,
    PHOTON_ABSORPTION_SCAFFOLD_SCHEMA,
    PHOTON_ABSORPTION_SCHEMA_VERSION,
    LeafAbsorptionSurface,
    SurfacePhotonAbsorption,
    build_absorption_surface_registry,
    compute_photon_absorption_metrics,
    leaf_absorption_surfaces,
)
from rad_rebuild.radiance.engine.plants.artifacts import (
    PlantArtifactPaths,
    write_plant_artifacts,
)
from rad_rebuild.radiance.engine.plants.config import (
    PlantGeometryConfig,
    PlantOpticalAssumptions,
)
from rad_rebuild.radiance.engine.plants.generator import generate_plant_scene
from rad_rebuild.radiance.engine.plants.models import (
    LeafGeometry,
    LeafMesh,
    PlantGeometry,
    PlantScene,
)
from rad_rebuild.radiance.engine.plants.radiance_export import (
    export_scene_to_radiance,
)
from rad_rebuild.radiance.engine.plants.viewer_export import export_scene_to_viewer

__all__ = [
    "LeafAbsorptionSurface",
    "LeafGeometry",
    "LeafMesh",
    "PHOTON_ABSORPTION_METRICS_SCHEMA",
    "PHOTON_ABSORPTION_SCAFFOLD_SCHEMA",
    "PHOTON_ABSORPTION_SCHEMA_VERSION",
    "PlantArtifactPaths",
    "PlantGeometry",
    "PlantGeometryConfig",
    "PlantOpticalAssumptions",
    "PlantScene",
    "SurfacePhotonAbsorption",
    "build_absorption_surface_registry",
    "compute_photon_absorption_metrics",
    "export_scene_to_radiance",
    "export_scene_to_viewer",
    "generate_plant_scene",
    "leaf_absorption_surfaces",
    "write_plant_artifacts",
]
