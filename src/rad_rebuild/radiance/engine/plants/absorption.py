"""Phase 07 plant photon absorption metrics scaffold.

This module is intentionally engine-only. It does not execute Radiance,
estimate crop output, or predict yield/biomass. It provides deterministic
surface IDs and conservative aggregation helpers for future Radiance-derived
per-surface photon flux inputs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

from rad_rebuild.radiance.engine.plants.models import LeafGeometry, PlantScene, Vector3

PHOTON_ABSORPTION_SCAFFOLD_SCHEMA = (
    "rad_rebuild.fspm.plant_photon_absorption.scaffold.v1"
)
PHOTON_ABSORPTION_METRICS_SCHEMA = (
    "rad_rebuild.fspm.plant_photon_absorption.metrics.v1"
)
PHOTON_ABSORPTION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class LeafAbsorptionSurface:
    """Traceable leaf-face surface for future photon-flux mapping."""

    surface_id: str
    plant_id: str
    leaf_id: str
    leaf_index: int
    face_index: int
    area_m2: float
    absorptance: float


@dataclass(frozen=True)
class SurfacePhotonAbsorption:
    """Computed absorption for one leaf-face surface."""

    surface_id: str
    plant_id: str
    leaf_id: str
    leaf_index: int
    face_index: int
    area_m2: float
    incident_photon_flux_umol_s: float
    absorbed_photon_flux_umol_s: float
    absorptance: float


def _triangle_area_m2(a: Vector3, b: Vector3, c: Vector3) -> float:
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    return 0.5 * math.sqrt(
        cross[0] * cross[0] + cross[1] * cross[1] + cross[2] * cross[2]
    )


def _finite_non_negative(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a JSON number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    if number < 0.0:
        raise ValueError(f"{name} must be greater than or equal to zero.")
    return number


def _leaf_surfaces(
    leaf: LeafGeometry,
    absorptance: float,
) -> tuple[LeafAbsorptionSurface, ...]:
    surfaces: list[LeafAbsorptionSurface] = []
    for face_index, face in enumerate(leaf.mesh.faces):
        vertices = leaf.mesh.vertices
        area_m2 = _triangle_area_m2(
            vertices[face[0]],
            vertices[face[1]],
            vertices[face[2]],
        )
        if not math.isfinite(area_m2) or area_m2 <= 0.0:
            raise ValueError(f"Leaf face area must be finite and positive: {leaf.leaf_id}")
        surfaces.append(
            LeafAbsorptionSurface(
                surface_id=f"{leaf.leaf_id}_face_{face_index:04d}",
                plant_id=leaf.plant_id,
                leaf_id=leaf.leaf_id,
                leaf_index=leaf.leaf_index,
                face_index=face_index,
                area_m2=area_m2,
                absorptance=absorptance,
            )
        )
    return tuple(surfaces)


def leaf_absorption_surfaces(scene: PlantScene) -> tuple[LeafAbsorptionSurface, ...]:
    """Return deterministic leaf-face registry entries for future mapping."""

    absorptance = scene.config.optical.absorptance
    surfaces: list[LeafAbsorptionSurface] = []
    for plant in scene.plants:
        for leaf in plant.leaves:
            surfaces.extend(_leaf_surfaces(leaf, absorptance))
    return tuple(surfaces)


def build_absorption_surface_registry(scene: PlantScene) -> dict[str, Any]:
    """Build a conservative geometry/ID registry for future absorption metrics."""

    surfaces = leaf_absorption_surfaces(scene)
    optical = scene.config.optical
    plant_ids = {plant.plant_id for plant in scene.plants}
    leaf_ids = {leaf.leaf_id for plant in scene.plants for leaf in plant.leaves}

    return {
        "schema": PHOTON_ABSORPTION_SCAFFOLD_SCHEMA,
        "schema_version": PHOTON_ABSORPTION_SCHEMA_VERSION,
        "status": "scaffold_only",
        "method": "surface_registry_from_deterministic_geometry",
        "units": {
            "area": "m2",
            "future_incident_photon_flux": "umol/s",
            "future_absorbed_photon_flux": "umol/s",
        },
        "plant_count": len(plant_ids),
        "leaf_count": len(leaf_ids),
        "surface_count": len(surfaces),
        "one_sided_leaf_area_m2": sum(surface.area_m2 for surface in surfaces),
        "optical_assumptions": {
            "reflectance": optical.reflectance,
            "transmittance": optical.transmittance,
            "absorptance": optical.absorptance,
        },
        "surfaces": [asdict(surface) for surface in surfaces],
        "outputs_do_not_predict": [
            "yield",
            "biomass",
            "growth",
            "crop_output",
        ],
        "assumptions": [
            "Surface IDs match deterministic Radiance polygon IDs.",
            "Leaf area is one-sided triangle mesh area in meters squared.",
            "Absorptance is an explicit optical assumption, not a measured crop response.",
        ],
        "limitations": [
            "No Radiance photon absorption solver is executed by this scaffold.",
            "No photosynthesis, growth, yield, biomass, or crop-output model is included.",
            "Future flux values must be added only after the sampling method is reviewed.",
        ],
    }


def compute_photon_absorption_metrics(
    scene: PlantScene,
    incident_flux_by_surface_id: Mapping[str, float],
    *,
    method: str = "external_surface_flux_map",
) -> dict[str, Any]:
    """Aggregate absorption from externally supplied per-surface photon flux.

    Inputs are expected to be incident photon flux values in µmol/s keyed by the
    deterministic surface IDs from `leaf_absorption_surfaces()`.
    """

    surfaces = leaf_absorption_surfaces(scene)
    expected_ids = {surface.surface_id for surface in surfaces}
    supplied_ids = set(incident_flux_by_surface_id)

    missing = sorted(expected_ids - supplied_ids)
    unknown = sorted(supplied_ids - expected_ids)
    if missing:
        raise ValueError(f"Missing incident photon flux for {len(missing)} plant surfaces.")
    if unknown:
        raise ValueError(f"Unknown plant surface IDs supplied: {unknown[:3]}")

    surface_metrics: list[SurfacePhotonAbsorption] = []
    for surface in surfaces:
        incident = _finite_non_negative(
            f"incident_flux_by_surface_id[{surface.surface_id}]",
            incident_flux_by_surface_id[surface.surface_id],
        )
        surface_metrics.append(
            SurfacePhotonAbsorption(
                surface_id=surface.surface_id,
                plant_id=surface.plant_id,
                leaf_id=surface.leaf_id,
                leaf_index=surface.leaf_index,
                face_index=surface.face_index,
                area_m2=surface.area_m2,
                incident_photon_flux_umol_s=incident,
                absorbed_photon_flux_umol_s=incident * surface.absorptance,
                absorptance=surface.absorptance,
            )
        )

    plant_summaries = _aggregate_by(surface_metrics, key_name="plant_id")
    leaf_summaries = _aggregate_by(surface_metrics, key_name="leaf_id")
    total_incident = sum(metric.incident_photon_flux_umol_s for metric in surface_metrics)
    total_absorbed = sum(metric.absorbed_photon_flux_umol_s for metric in surface_metrics)

    return {
        "schema": PHOTON_ABSORPTION_METRICS_SCHEMA,
        "schema_version": PHOTON_ABSORPTION_SCHEMA_VERSION,
        "method": method,
        "units": {
            "area": "m2",
            "incident_photon_flux": "umol/s",
            "absorbed_photon_flux": "umol/s",
        },
        "plant_count": len(plant_summaries),
        "leaf_count": len(leaf_summaries),
        "surface_count": len(surface_metrics),
        "one_sided_leaf_area_m2": sum(metric.area_m2 for metric in surface_metrics),
        "total_incident_photon_flux_umol_s": total_incident,
        "total_absorbed_photon_flux_umol_s": total_absorbed,
        "mean_absorbed_fraction_of_incident": (
            total_absorbed / total_incident if total_incident > 0.0 else 0.0
        ),
        "plant_summaries": plant_summaries,
        "leaf_summaries": leaf_summaries,
        "surfaces": [asdict(metric) for metric in surface_metrics],
        "outputs_do_not_predict": [
            "yield",
            "biomass",
            "growth",
            "crop_output",
        ],
        "limitations": [
            "These metrics depend on externally supplied surface photon flux values.",
            "This function does not run Radiance or define a leaf sampling strategy.",
            "Absorbed photon flux is computed from explicit absorptance assumptions only.",
        ],
    }


def _aggregate_by(
    surface_metrics: list[SurfacePhotonAbsorption],
    *,
    key_name: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[SurfacePhotonAbsorption]] = {}
    for metric in surface_metrics:
        key = getattr(metric, key_name)
        grouped.setdefault(key, []).append(metric)

    rows: list[dict[str, Any]] = []
    for key in sorted(grouped):
        items = grouped[key]
        incident = sum(item.incident_photon_flux_umol_s for item in items)
        absorbed = sum(item.absorbed_photon_flux_umol_s for item in items)
        rows.append(
            {
                key_name: key,
                "surface_count": len(items),
                "one_sided_leaf_area_m2": sum(item.area_m2 for item in items),
                "incident_photon_flux_umol_s": incident,
                "absorbed_photon_flux_umol_s": absorbed,
                "absorbed_fraction_of_incident": (
                    absorbed / incident if incident > 0.0 else 0.0
                ),
            }
        )
    return rows
