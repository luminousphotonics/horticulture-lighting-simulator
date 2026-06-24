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
from rad_rebuild.radiance.engine.plants.surface_flux import (
    BASELINE_PPFD_PROXY_METHOD,
    SPATIAL_PPFD_PROXY_METHOD,
    RADIANCE_RECEIVER_METHOD,
    PLANT_SURFACE_FLUX_FILENAME,
    PLANT_SURFACE_FLUX_SCHEMA,
    build_baseline_proxy_surface_flux_rows,
    build_spatial_proxy_surface_flux_rows,
    build_radiance_receiver_samples,
    build_radiance_receiver_surface_flux_rows,
    build_plant_surface_flux_payload,
    parse_rtrace_receiver_output,
    read_ppfd_map_field,
    receiver_sample_input_text,
    write_baseline_proxy_plant_surface_flux_artifact,
    write_spatial_proxy_plant_surface_flux_artifact,
    write_radiance_receiver_plant_surface_flux_artifact,
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
    "PLANT_SURFACE_FLUX_FILENAME",
    "PLANT_SURFACE_FLUX_SCHEMA",
    "PlantScene",
    "SurfacePhotonAbsorption",
    "BASELINE_PPFD_PROXY_METHOD",
    "SPATIAL_PPFD_PROXY_METHOD",
    "RADIANCE_RECEIVER_METHOD",
    "build_absorption_surface_registry",
    "build_baseline_proxy_surface_flux_rows",
    "build_spatial_proxy_surface_flux_rows",
    "build_radiance_receiver_samples",
    "build_radiance_receiver_surface_flux_rows",
    "build_plant_surface_flux_payload",
    "parse_rtrace_receiver_output",
    "read_ppfd_map_field",
    "receiver_sample_input_text",
    "compute_photon_absorption_metrics",
    "export_scene_to_radiance",
    "export_scene_to_viewer",
    "generate_plant_scene",
    "leaf_absorption_surfaces",
    "write_baseline_proxy_plant_surface_flux_artifact",
    "write_spatial_proxy_plant_surface_flux_artifact",
    "write_radiance_receiver_plant_surface_flux_artifact",
    "write_plant_artifacts",
]
