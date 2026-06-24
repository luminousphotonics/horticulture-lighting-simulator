"""Runtime artifact writer for deterministic plant geometry exports."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from rad_rebuild.radiance.engine.plants.absorption import build_absorption_surface_registry
from rad_rebuild.radiance.engine.plants.config import PlantGeometryConfig
from rad_rebuild.radiance.engine.plants.generator import generate_plant_scene
from rad_rebuild.radiance.engine.plants.models import PlantScene
from rad_rebuild.radiance.engine.plants.radiance_export import (
    export_scene_to_radiance,
)
from rad_rebuild.radiance.engine.plants.viewer_export import export_scene_to_viewer

PLANTS_RAD_FILENAME = "plants.rad"
PLANTS_VIEWER_FILENAME = "plants_viewer.json"
PLANTS_MANIFEST_FILENAME = "plants_manifest.json"
PLANT_CONFIG_FILENAME = "plant_config.json"
PLANT_ABSORPTION_SURFACES_FILENAME = "plant_absorption_surfaces.json"
PLANT_ARTIFACT_FILENAMES = (
    PLANTS_RAD_FILENAME,
    PLANTS_VIEWER_FILENAME,
    PLANTS_MANIFEST_FILENAME,
    PLANT_CONFIG_FILENAME,
    PLANT_ABSORPTION_SURFACES_FILENAME,
)
PLANT_ARTIFACT_SCHEMA = "rad_rebuild.fspm.plants.artifacts.v1"
PLANT_ARTIFACT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PlantArtifactPaths:
    """Paths written by the Phase 02 plant artifact export helper."""

    directory: Path
    radiance: Path
    viewer: Path
    manifest: Path
    config: Path
    absorption_surfaces: Path


def write_plant_artifacts(
    target_dir: str | Path,
    config: PlantGeometryConfig | None = None,
    *,
    active_simulation_integration: bool = False,
    provenance_phase: str = "Phase 02",
) -> PlantArtifactPaths:
    """Write deterministic plant artifacts into a caller-provided directory."""

    output_dir = Path(target_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise ValueError(f"target_dir must be a directory: {output_dir}")

    scene = generate_plant_scene(config)
    paths = PlantArtifactPaths(
        directory=output_dir,
        radiance=output_dir / PLANTS_RAD_FILENAME,
        viewer=output_dir / PLANTS_VIEWER_FILENAME,
        manifest=output_dir / PLANTS_MANIFEST_FILENAME,
        config=output_dir / PLANT_CONFIG_FILENAME,
        absorption_surfaces=output_dir / PLANT_ABSORPTION_SURFACES_FILENAME,
    )
    _assert_fixed_artifact_paths(output_dir, paths)

    radiance_text = export_scene_to_radiance(scene)
    viewer_json = _canonical_json(export_scene_to_viewer(scene))
    config_json = _canonical_json(_config_payload(scene.config))
    absorption_json = _canonical_json(build_absorption_surface_registry(scene))

    paths.radiance.write_text(radiance_text, encoding="utf-8")
    paths.viewer.write_text(viewer_json, encoding="utf-8")
    paths.config.write_text(config_json, encoding="utf-8")
    paths.absorption_surfaces.write_text(absorption_json, encoding="utf-8")

    manifest = _manifest_payload(
        scene,
        paths,
        active_simulation_integration=active_simulation_integration,
        provenance_phase=provenance_phase,
    )
    paths.manifest.write_text(_canonical_json(manifest), encoding="utf-8")
    return paths


def _assert_fixed_artifact_paths(
    output_dir: Path,
    paths: PlantArtifactPaths,
) -> None:
    expected = {
        paths.radiance: PLANTS_RAD_FILENAME,
        paths.viewer: PLANTS_VIEWER_FILENAME,
        paths.manifest: PLANTS_MANIFEST_FILENAME,
        paths.config: PLANT_CONFIG_FILENAME,
        paths.absorption_surfaces: PLANT_ABSORPTION_SURFACES_FILENAME,
    }
    for path, filename in expected.items():
        if path.parent != output_dir or path.name != filename:
            raise ValueError("plant artifact paths must stay inside target_dir")


def _manifest_payload(
    scene: PlantScene,
    paths: PlantArtifactPaths,
    *,
    active_simulation_integration: bool,
    provenance_phase: str,
) -> dict[str, Any]:
    files = [
        _file_record(paths.radiance),
        _file_record(paths.viewer),
        _file_record(paths.config),
        _file_record(paths.absorption_surfaces),
    ]
    return {
        "schema": PLANT_ARTIFACT_SCHEMA,
        "schema_version": PLANT_ARTIFACT_SCHEMA_VERSION,
        "artifact_filenames": {
            "radiance": PLANTS_RAD_FILENAME,
            "viewer": PLANTS_VIEWER_FILENAME,
            "config": PLANT_CONFIG_FILENAME,
            "absorption_surfaces": PLANT_ABSORPTION_SURFACES_FILENAME,
            "manifest": PLANTS_MANIFEST_FILENAME,
        },
        "active_simulation_integration": active_simulation_integration,
        "config": _config_payload(scene.config),
        "counts": {
            "plants": len(scene.plants),
            "leaves": sum(len(plant.leaves) for plant in scene.plants),
        },
        "files": files,
        "provenance": {
            "phase": provenance_phase,
            "generator": "deterministic_leafy_green_rosette",
            "source_module": "rad_rebuild.radiance.engine.plants.artifacts",
            "units": "meters",
        },
    }


def _file_record(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": path.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _config_payload(config: PlantGeometryConfig) -> dict[str, Any]:
    return {
        "seed": config.seed,
        "plant_grid_rows": config.plant_grid_rows,
        "plant_grid_columns": config.plant_grid_columns,
        "plant_spacing_m": config.plant_spacing_m,
        "plant_height_m": config.plant_height_m,
        "canopy_radius_m": config.canopy_radius_m,
        "leaf_count_per_plant": config.leaf_count_per_plant,
        "leaf_length_range_m": list(config.leaf_length_range_m),
        "leaf_width_range_m": list(config.leaf_width_range_m),
        "leaf_tilt_range_deg": list(config.leaf_tilt_range_deg),
        "leaf_curvature_m": config.leaf_curvature_m,
        "growth_stage": config.growth_stage,
        "optical": {
            "reflectance": config.optical.reflectance,
            "transmittance": config.optical.transmittance,
            "absorptance": config.optical.absorptance,
        },
    }


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
