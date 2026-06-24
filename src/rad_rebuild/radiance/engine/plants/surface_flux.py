"""Deterministic plant surface-flux artifact design scaffold.

This module produces a traceable `plant_surface_flux.json` artifact keyed by
the deterministic leaf-face surface IDs from the plant absorption registry.

The current live integration uses a conservative baseline-PPFD proxy so the
JSON contract, aggregation, validation, and viewer-coloring data can be tested
before replacing the proxy with a reviewed Radiance per-surface receiver method.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from rad_rebuild.radiance.engine.plants.absorption import (
    LeafAbsorptionSurface,
    compute_photon_absorption_metrics,
    leaf_absorption_surfaces,
)
from rad_rebuild.radiance.engine.plants.models import PlantScene, Vector3

PLANT_SURFACE_FLUX_SCHEMA = "rad_rebuild.fspm.plant_surface_flux.v1"
PLANT_SURFACE_FLUX_SCHEMA_VERSION = 1
PLANT_SURFACE_FLUX_FILENAME = "plant_surface_flux.json"
BASELINE_PPFD_PROXY_METHOD = "baseline_ppfd_mean_orientation_proxy_v1"
NO_CROP_OUTPUT_TERMS = ["yield", "biomass", "growth", "crop_output"]


def _finite_non_negative(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a JSON number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    if number < 0.0:
        raise ValueError(f"{name} must be greater than or equal to zero.")
    return number


def _vector_sub(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vector3, b: Vector3) -> Vector3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _unit_normal(a: Vector3, b: Vector3, c: Vector3, *, surface_id: str) -> Vector3:
    normal = _cross(_vector_sub(b, a), _vector_sub(c, a))
    mag = math.sqrt(normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2])
    if not math.isfinite(mag) or mag <= 0.0:
        raise ValueError(f"Surface {surface_id} has a degenerate normal.")
    return (normal[0] / mag, normal[1] / mag, normal[2] / mag)


def _centroid(a: Vector3, b: Vector3, c: Vector3) -> Vector3:
    return ((a[0] + b[0] + c[0]) / 3.0, (a[1] + b[1] + c[1]) / 3.0, (a[2] + b[2] + c[2]) / 3.0)


def _surface_geometry_by_id(scene: PlantScene) -> dict[str, dict[str, Any]]:
    surface_registry = {surface.surface_id: surface for surface in leaf_absorption_surfaces(scene)}
    geometry: dict[str, dict[str, Any]] = {}

    for plant in scene.plants:
        for leaf in plant.leaves:
            for face_index, face in enumerate(leaf.mesh.faces):
                surface_id = f"{leaf.leaf_id}_face_{face_index:04d}"
                if surface_id not in surface_registry:
                    raise ValueError(f"Surface registry missing deterministic surface ID: {surface_id}")
                vertices = leaf.mesh.vertices
                a, b, c = vertices[face[0]], vertices[face[1]], vertices[face[2]]
                surface = surface_registry[surface_id]
                geometry[surface_id] = {
                    "surface": surface,
                    "centroid_m": _centroid(a, b, c),
                    "normal": _unit_normal(a, b, c, surface_id=surface_id),
                }

    missing = set(surface_registry) - set(geometry)
    if missing:
        raise ValueError(f"Missing mesh geometry for {len(missing)} plant surfaces.")
    return geometry


def build_baseline_proxy_surface_flux_rows(
    scene: PlantScene,
    baseline_ppfd_mean_umol_m2_s: float,
) -> list[dict[str, Any]]:
    """Create deterministic proxy flux rows for every plant surface.

    The proxy does not claim to be final per-leaf Radiance absorption. It uses
    the unblocked baseline canopy PPFD mean, surface area, and a two-sided
    orientation factor to populate the artifact contract for testing.
    """

    baseline = _finite_non_negative(
        "baseline_ppfd_mean_umol_m2_s",
        baseline_ppfd_mean_umol_m2_s,
    )
    geometry = _surface_geometry_by_id(scene)
    max_height = max(float(scene.config.plant_height_m), 1e-9)

    rows: list[dict[str, Any]] = []
    for surface_id in sorted(geometry):
        item = geometry[surface_id]
        surface = item["surface"]
        if not isinstance(surface, LeafAbsorptionSurface):
            raise ValueError(f"Invalid surface registry entry for {surface_id}.")
        centroid = item["centroid_m"]
        normal = item["normal"]
        orientation_factor = 0.35 + 0.65 * abs(float(normal[2]))
        height_factor = 0.90 + 0.20 * min(1.0, max(0.0, float(centroid[2]) / max_height))
        incident_density = baseline * orientation_factor * height_factor
        rows.append(
            {
                "surface_id": surface.surface_id,
                "plant_id": surface.plant_id,
                "leaf_id": surface.leaf_id,
                "leaf_index": surface.leaf_index,
                "face_index": surface.face_index,
                "area_m2": surface.area_m2,
                "centroid_m": [float(v) for v in centroid],
                "normal": [float(v) for v in normal],
                "incident_photon_flux_density_umol_m2_s": incident_density,
                "incident_photon_flux_umol_s": incident_density * surface.area_m2,
                "source": "baseline_ppfd_orientation_proxy",
            }
        )
    return rows


def _surface_rows_by_id(
    scene: PlantScene,
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    geometry = _surface_geometry_by_id(scene)
    expected_ids = set(geometry)
    by_id: dict[str, Mapping[str, Any]] = {}

    for row in rows:
        surface_id = row.get("surface_id")
        if not isinstance(surface_id, str) or not surface_id:
            raise ValueError("Each surface-flux row must include a non-empty surface_id.")
        if surface_id in by_id:
            raise ValueError(f"Duplicate surface flux row for surface_id {surface_id!r}.")
        if surface_id not in expected_ids:
            raise ValueError(f"Unknown surface_id in surface flux rows: {surface_id!r}.")

        surface = geometry[surface_id]["surface"]
        expected_leaf_id = surface.leaf_id
        row_leaf_id = row.get("leaf_id")
        if row_leaf_id is not None and row_leaf_id != expected_leaf_id:
            raise ValueError(
                f"surface_id {surface_id!r} belongs to leaf_id {expected_leaf_id!r}, "
                f"not {row_leaf_id!r}."
            )

        by_id[surface_id] = row

    missing = sorted(expected_ids - set(by_id))
    if missing:
        raise ValueError(f"Missing incident photon flux for {len(missing)} plant surfaces.")
    return by_id


def _normalize_surface_rows(
    scene: PlantScene,
    rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    geometry = _surface_geometry_by_id(scene)
    by_id = _surface_rows_by_id(scene, rows)

    normalized: list[dict[str, Any]] = []
    incident_by_surface_id: dict[str, float] = {}
    for surface_id in sorted(by_id):
        raw = by_id[surface_id]
        surface = geometry[surface_id]["surface"]
        incident = _finite_non_negative(
            f"incident_photon_flux_umol_s[{surface_id}]",
            raw.get("incident_photon_flux_umol_s"),
        )
        incident_by_surface_id[surface_id] = incident
        density = incident / surface.area_m2 if surface.area_m2 > 0.0 else 0.0
        normalized.append(
            {
                "surface_id": surface_id,
                "plant_id": surface.plant_id,
                "leaf_id": surface.leaf_id,
                "leaf_index": surface.leaf_index,
                "face_index": surface.face_index,
                "area_m2": surface.area_m2,
                "incident_photon_flux_umol_s": incident,
                "incident_photon_flux_density_umol_m2_s": density,
                "centroid_m": [float(v) for v in geometry[surface_id]["centroid_m"]],
                "normal": [float(v) for v in geometry[surface_id]["normal"]],
            }
        )
    return normalized, incident_by_surface_id


def _visual_value(value: float, *, min_value: float, max_value: float) -> float:
    if max_value <= min_value:
        return 0.5
    return min(1.0, max(0.0, (value - min_value) / (max_value - min_value)))


def _density_range(rows: list[dict[str, Any]], key: str) -> tuple[float, float]:
    values = [float(row.get(key, 0.0) or 0.0) for row in rows]
    if not values:
        return 0.0, 0.0
    return min(values), max(values)


def _visualization_payload(
    leaf_summaries: list[dict[str, Any]],
    plant_summaries: list[dict[str, Any]],
    surface_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    key = "absorbed_photon_flux_density_umol_m2_s"
    leaf_min, leaf_max = _density_range(leaf_summaries, key)
    surface_min, surface_max = _density_range(surface_summaries, key)
    return {
        "color_metric": key,
        "normalization": "linear_0_1",
        "leaf_scale": {
            "min": leaf_min,
            "max": leaf_max,
        },
        "surface_scale": {
            "min": surface_min,
            "max": surface_max,
        },
        "leaf_values": [
            {
                "leaf_id": row["leaf_id"],
                "plant_id": row["plant_id"],
                "lighting_region": row["lighting_region"],
                "absorbed_photon_flux_density_umol_m2_s": row[key],
                "visual_intensity_0_1": _visual_value(row[key], min_value=leaf_min, max_value=leaf_max),
            }
            for row in leaf_summaries
        ],
        "plant_values": [
            {
                "plant_id": row["plant_id"],
                "lighting_region": row["lighting_region"],
                "absorbed_photon_flux_density_umol_m2_s": row[key],
            }
            for row in plant_summaries
        ],
        "surface_values": [
            {
                "surface_id": row["surface_id"],
                "leaf_id": row["leaf_id"],
                "plant_id": row["plant_id"],
                "absorbed_photon_flux_density_umol_m2_s": row[key],
                "visual_intensity_0_1": _visual_value(row[key], min_value=surface_min, max_value=surface_max),
            }
            for row in surface_summaries
        ],
    }


def build_plant_surface_flux_payload(
    scene: PlantScene,
    surface_flux_rows: Iterable[Mapping[str, Any]],
    *,
    method: str,
    source_ppfd_map: str | None = None,
    baseline_ppfd_mean_umol_m2_s: float | None = None,
) -> dict[str, Any]:
    normalized_rows, incident_by_surface_id = _normalize_surface_rows(scene, surface_flux_rows)
    absorption_metrics = compute_photon_absorption_metrics(
        scene,
        incident_by_surface_id,
        method=method,
    )

    surface_by_id = {row["surface_id"]: row for row in normalized_rows}
    surface_summaries: list[dict[str, Any]] = []
    for row in absorption_metrics["surfaces"]:
        surface_id = row["surface_id"]
        normalized = surface_by_id[surface_id]
        area = float(row["area_m2"])
        absorbed = float(row["absorbed_photon_flux_umol_s"])
        surface_summaries.append(
            {
                **row,
                "incident_photon_flux_density_umol_m2_s": normalized[
                    "incident_photon_flux_density_umol_m2_s"
                ],
                "absorbed_photon_flux_density_umol_m2_s": absorbed / area if area > 0.0 else 0.0,
                "centroid_m": normalized["centroid_m"],
                "normal": normalized["normal"],
            }
        )

    plant_summaries = list(absorption_metrics["plant_summaries"])
    leaf_summaries = list(absorption_metrics["leaf_summaries"])
    visualization = _visualization_payload(leaf_summaries, plant_summaries, surface_summaries)

    return {
        "schema": PLANT_SURFACE_FLUX_SCHEMA,
        "schema_version": PLANT_SURFACE_FLUX_SCHEMA_VERSION,
        "status": "proxy" if method == BASELINE_PPFD_PROXY_METHOD else "computed",
        "method": method,
        "source_ppfd_map": source_ppfd_map,
        "baseline_ppfd_mean_umol_m2_s": baseline_ppfd_mean_umol_m2_s,
        "units": {
            "area": "m2",
            "incident_photon_flux": "umol/s",
            "absorbed_photon_flux": "umol/s",
            "photon_flux_density": "umol/m2/s",
        },
        "plant_count": absorption_metrics["plant_count"],
        "leaf_count": absorption_metrics["leaf_count"],
        "surface_count": absorption_metrics["surface_count"],
        "one_sided_leaf_area_m2": absorption_metrics["one_sided_leaf_area_m2"],
        "total_incident_photon_flux_umol_s": absorption_metrics[
            "total_incident_photon_flux_umol_s"
        ],
        "total_absorbed_photon_flux_umol_s": absorption_metrics[
            "total_absorbed_photon_flux_umol_s"
        ],
        "mean_absorbed_fraction_of_incident": absorption_metrics[
            "mean_absorbed_fraction_of_incident"
        ],
        "plant_to_plant_absorbed_photon_flux_cv": absorption_metrics[
            "plant_to_plant_absorbed_photon_flux_cv"
        ],
        "under_lit_leaf_count": absorption_metrics["under_lit_leaf_count"],
        "over_lit_leaf_count": absorption_metrics["over_lit_leaf_count"],
        "plant_summaries": plant_summaries,
        "leaf_summaries": leaf_summaries,
        "surface_summaries": surface_summaries,
        "visualization": visualization,
        "outputs_do_not_predict": list(NO_CROP_OUTPUT_TERMS),
        "warnings": [
            "This artifact currently uses an unblocked baseline PPFD proxy, not final Radiance leaf-surface receiver sampling.",
            "Plant geometry is not inserted into the baseline PPFD octree and does not shadow the heatmap.",
        ],
        "limitations": [
            "Per-surface flux values are contract-valid proxy values until the Radiance receiver method is reviewed.",
            "Absorption is computed from explicit optical absorptance assumptions.",
        ],
    }


def write_plant_surface_flux_artifact(
    target_dir: str | Path,
    payload: Mapping[str, Any],
) -> Path:
    output_dir = Path(target_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / PLANT_SURFACE_FLUX_FILENAME
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_baseline_proxy_plant_surface_flux_artifact(
    target_dir: str | Path,
    scene: PlantScene,
    *,
    baseline_ppfd_mean_umol_m2_s: float,
    source_ppfd_map: str = "ppfd_map.txt",
) -> Path:
    rows = build_baseline_proxy_surface_flux_rows(scene, baseline_ppfd_mean_umol_m2_s)
    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=BASELINE_PPFD_PROXY_METHOD,
        source_ppfd_map=source_ppfd_map,
        baseline_ppfd_mean_umol_m2_s=baseline_ppfd_mean_umol_m2_s,
    )
    return write_plant_surface_flux_artifact(target_dir, payload)
