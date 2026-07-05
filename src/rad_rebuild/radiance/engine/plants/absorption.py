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
from rad_rebuild.radiance.fspm_targets import (
    fspm_target_metadata,
    resolve_fspm_target_ppfd,
    resolve_fspm_target_tolerance,
)

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


def _coefficient_of_variation(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if mean <= 0.0:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance) / mean


def _lighting_region(value: float, *, lower_threshold: float, upper_threshold: float) -> str:
    tolerance = 1e-9 * max(1.0, abs(value), abs(lower_threshold), abs(upper_threshold))
    if value < lower_threshold - tolerance:
        return "under_lit"
    if value > upper_threshold + tolerance:
        return "over_lit"
    return "target_range"


def _target_metric_fields(
    *,
    capped_flux: float,
    capped_density: float,
    excess_flux: float,
    deficit_flux: float,
) -> dict[str, float]:
    return {
        "target_capped_incident_flux_density_umol_m2_s": capped_density,
        "target_capped_incident_flux_umol_s": capped_flux,
        "excess_incident_flux_above_target_umol_s": excess_flux,
        "deficit_to_target_incident_flux_umol_s": deficit_flux,
        "target_capped_flux_density_umol_m2_s": capped_density,
        "target_capped_flux_umol_s": capped_flux,
        "excess_flux_above_target_umol_s": excess_flux,
        "under_target_deficit_umol_s": deficit_flux,
    }


def _annotate_regions(rows: list[dict[str, Any]], target: Mapping[str, Any]) -> list[dict[str, Any]]:
    density_key = "target_classification_ppfd_umol_m2_s"
    target_ppfd = float(target["target_ppfd_umol_m2_s"])
    lower_threshold = float(target["target_lower_threshold_umol_m2_s"])
    upper_threshold = float(target["target_upper_threshold_umol_m2_s"])
    annotated: list[dict[str, Any]] = []
    for row in rows:
        value = float(
            row.get(density_key, row.get("incident_photon_flux_density_umol_m2_s", 0.0))
            or 0.0
        )
        area = float(row.get("one_sided_leaf_area_m2", row.get("area_m2", 0.0)) or 0.0)
        capped_flux_value = row.get(
            "target_capped_incident_flux_umol_s",
            row.get("target_capped_flux_umol_s"),
        )
        excess_flux_value = row.get(
            "excess_incident_flux_above_target_umol_s",
            row.get("excess_flux_above_target_umol_s"),
        )
        deficit_flux_value = row.get(
            "deficit_to_target_incident_flux_umol_s",
            row.get("under_target_deficit_umol_s"),
        )
        capped_flux = (
            float(capped_flux_value)
            if isinstance(capped_flux_value, int | float)
            else min(value, target_ppfd) * area
        )
        excess_flux = (
            float(excess_flux_value)
            if isinstance(excess_flux_value, int | float)
            else max(0.0, value - target_ppfd) * area
        )
        deficit_flux = (
            float(deficit_flux_value)
            if isinstance(deficit_flux_value, int | float)
            else max(0.0, target_ppfd - value) * area
        )
        capped_density = capped_flux / area if area > 0.0 else 0.0
        annotated.append(
            {
                **row,
                "target_classification_ppfd_umol_m2_s": value,
                "lighting_region": _lighting_region(
                    value,
                    lower_threshold=lower_threshold,
                    upper_threshold=upper_threshold,
                ),
                **_target_metric_fields(
                    capped_flux=capped_flux,
                    capped_density=capped_density,
                    excess_flux=excess_flux,
                    deficit_flux=deficit_flux,
                ),
            }
        )
    return annotated


def _surface_metric_rows(
    surface_metrics: list[SurfacePhotonAbsorption],
    *,
    target_classification_ppfd_by_surface_id: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric in surface_metrics:
        row = asdict(metric)
        area = metric.area_m2
        incident_density = (
            metric.incident_photon_flux_umol_s / area if area > 0.0 else 0.0
        )
        classification_density = (
            float(target_classification_ppfd_by_surface_id[metric.surface_id])
            if target_classification_ppfd_by_surface_id is not None
            else incident_density
        )
        row["incident_photon_flux_density_umol_m2_s"] = incident_density
        row["target_classification_ppfd_umol_m2_s"] = classification_density
        row["target_classification_incident_flux_umol_s"] = classification_density * area
        row["absorbed_photon_flux_density_umol_m2_s"] = (
            metric.absorbed_photon_flux_umol_s / area if area > 0.0 else 0.0
        )
        rows.append(row)
    return rows


def _region_counts(rows: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
    total = len(rows)
    under = sum(1 for row in rows if row.get("lighting_region") == "under_lit")
    target_range = sum(1 for row in rows if row.get("lighting_region") == "target_range")
    over = sum(1 for row in rows if row.get("lighting_region") == "over_lit")
    return {
        f"under_lit_{label}_count": under,
        f"target_range_{label}_count": target_range,
        f"over_lit_{label}_count": over,
        f"under_lit_{label}_fraction": under / total if total else 0.0,
        f"target_range_{label}_fraction": target_range / total if total else 0.0,
        f"over_lit_{label}_fraction": over / total if total else 0.0,
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.floor((len(ordered) - 1) * fraction)))
    return ordered[index]


def _target_capped_totals(
    surface_rows: list[dict[str, Any]],
    plant_rows: list[dict[str, Any]],
    leaf_rows: list[dict[str, Any]],
    *,
    target_ppfd: float,
) -> dict[str, Any]:
    total_area = sum(float(row.get("area_m2", 0.0) or 0.0) for row in surface_rows)
    raw_total = sum(float(row.get("incident_photon_flux_umol_s", 0.0) or 0.0) for row in surface_rows)
    classification_total = sum(
        float(
            row.get(
                "target_classification_incident_flux_umol_s",
                float(row.get("target_classification_ppfd_umol_m2_s", 0.0) or 0.0)
                * float(row.get("area_m2", 0.0) or 0.0),
            )
            or 0.0
        )
        for row in surface_rows
    )
    capped_total = sum(
        float(row.get("target_capped_incident_flux_umol_s", 0.0) or 0.0)
        for row in surface_rows
    )
    excess_total = sum(
        float(row.get("excess_incident_flux_above_target_umol_s", 0.0) or 0.0)
        for row in surface_rows
    )
    deficit_total = sum(
        float(row.get("deficit_to_target_incident_flux_umol_s", 0.0) or 0.0)
        for row in surface_rows
    )
    target_opportunity = target_ppfd * total_area
    plant_capped_densities = [
        float(row.get("target_capped_incident_flux_density_umol_m2_s", 0.0) or 0.0)
        for row in plant_rows
    ]
    leaf_raw_densities = [
        float(row.get("incident_photon_flux_density_umol_m2_s", 0.0) or 0.0)
        for row in leaf_rows
    ]
    leaf_classification_densities = [
        float(row.get("target_classification_ppfd_umol_m2_s", 0.0) or 0.0)
        for row in leaf_rows
    ]
    leaf_capped_densities = [
        float(row.get("target_capped_incident_flux_density_umol_m2_s", 0.0) or 0.0)
        for row in leaf_rows
    ]
    target_capped_mean = capped_total / total_area if total_area > 0.0 else 0.0
    target_capped = {
        "raw_mean_flux_density_umol_m2_s": raw_total / total_area if total_area > 0.0 else 0.0,
        "target_classification_mean_ppfd_umol_m2_s": (
            classification_total / total_area if total_area > 0.0 else 0.0
        ),
        "target_classification_total_incident_flux_umol_s": classification_total,
        "target_capped_incident_mean_flux_density_umol_m2_s": target_capped_mean,
        "target_capped_incident_flux_total_umol_s": capped_total,
        "target_capped_incident_total_flux_umol_s": capped_total,
        "excess_incident_flux_above_target_umol_s": excess_total,
        "excess_incident_flux_fraction": (
            excess_total / classification_total if classification_total > 0.0 else 0.0
        ),
        "deficit_to_target_incident_flux_umol_s": deficit_total,
        "deficit_to_target_incident_flux_fraction": (
            deficit_total / target_opportunity if target_opportunity > 0.0 else 0.0
        ),
        "raw_total_flux_umol_s": raw_total,
        "target_capped_flux_total_umol_s": capped_total,
        "target_capped_total_flux_umol_s": capped_total,
        "excess_flux_above_target_umol_s": excess_total,
        "excess_flux_fraction": (
            excess_total / classification_total if classification_total > 0.0 else 0.0
        ),
        "under_target_deficit_umol_s": deficit_total,
        "under_target_deficit_fraction": (
            deficit_total / target_opportunity if target_opportunity > 0.0 else 0.0
        ),
        "target_capped_mean_flux_density_umol_m2_s": target_capped_mean,
        "lower_tail_raw_flux_density_umol_m2_s": _percentile(leaf_raw_densities, 0.10),
        "lower_tail_target_classification_ppfd_umol_m2_s": _percentile(
            leaf_classification_densities,
            0.10,
        ),
        "lower_tail_target_capped_incident_flux_density_umol_m2_s": _percentile(
            leaf_capped_densities,
            0.10,
        ),
        "lower_tail_target_capped_flux_density_umol_m2_s": _percentile(
            leaf_capped_densities,
            0.10,
        ),
        "plant_to_plant_target_capped_incident_flux_cv": _coefficient_of_variation(
            plant_capped_densities
        ),
        "plant_to_plant_target_capped_flux_cv": _coefficient_of_variation(
            plant_capped_densities
        ),
    }
    return target_capped


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
    target_ppfd_umol_m2_s: float | None = None,
    target_tolerance_umol_m2_s: float | None = None,
    target_classification_ppfd_by_surface_id: Mapping[str, float] | None = None,
    target_classification_metadata: Mapping[str, Any] | None = None,
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

    classification_by_surface_id: dict[str, float] | None = None
    if target_classification_ppfd_by_surface_id is not None:
        classification_ids = set(target_classification_ppfd_by_surface_id)
        missing_classification = sorted(expected_ids - classification_ids)
        unknown_classification = sorted(classification_ids - expected_ids)
        if missing_classification:
            raise ValueError(
                "Missing target classification PPFD for "
                f"{len(missing_classification)} plant surfaces."
            )
        if unknown_classification:
            raise ValueError(
                "Unknown target classification surface IDs supplied: "
                f"{unknown_classification[:3]}"
            )
        classification_by_surface_id = {
            surface_id: _finite_non_negative(
                f"target_classification_ppfd_by_surface_id[{surface_id}]",
                target_classification_ppfd_by_surface_id[surface_id],
            )
            for surface_id in sorted(expected_ids)
        }

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

    target_ppfd = resolve_fspm_target_ppfd(target_ppfd_umol_m2_s)
    target_tolerance = resolve_fspm_target_tolerance(target_tolerance_umol_m2_s)
    target_metadata_keys = {
        "target_classification_basis",
        "target_classification_basis_label",
        "target_classification_source",
        "target_classification_note",
    }
    filtered_target_metadata = {
        key: value
        for key, value in dict(target_classification_metadata or {}).items()
        if key in target_metadata_keys
    }
    target_metadata = fspm_target_metadata(
        target_ppfd_umol_m2_s=target_ppfd,
        tolerance_umol_m2_s=target_tolerance,
        **filtered_target_metadata,
    )
    surface_rows = _annotate_regions(
        _surface_metric_rows(
            surface_metrics,
            target_classification_ppfd_by_surface_id=classification_by_surface_id,
        ),
        target_metadata,
    )
    plant_summaries = _annotate_regions(
        _aggregate_by(
            surface_metrics,
            key_name="plant_id",
            target=target_metadata,
            target_classification_ppfd_by_surface_id=classification_by_surface_id,
        ),
        target_metadata,
    )
    leaf_summaries = _annotate_regions(
        _aggregate_by(
            surface_metrics,
            key_name="leaf_id",
            target=target_metadata,
            target_classification_ppfd_by_surface_id=classification_by_surface_id,
        ),
        target_metadata,
    )
    total_incident = sum(metric.incident_photon_flux_umol_s for metric in surface_metrics)
    total_absorbed = sum(metric.absorbed_photon_flux_umol_s for metric in surface_metrics)
    plant_absorbed_values = [
        float(row["absorbed_photon_flux_umol_s"])
        for row in plant_summaries
    ]
    plant_absorption_cv = _coefficient_of_variation(plant_absorbed_values)
    leaf_region_counts = _region_counts(leaf_summaries, label="leaf")
    surface_region_counts = _region_counts(surface_rows, label="surface")
    plant_region_counts = _region_counts(plant_summaries, label="plant")
    target_capped = _target_capped_totals(
        surface_rows,
        plant_summaries,
        leaf_summaries,
        target_ppfd=target_ppfd,
    )

    return {
        "schema": PHOTON_ABSORPTION_METRICS_SCHEMA,
        "schema_version": PHOTON_ABSORPTION_SCHEMA_VERSION,
        "method": method,
        "units": {
            "area": "m2",
            "incident_photon_flux": "umol/s",
            "absorbed_photon_flux": "umol/s",
        },
        "target": target_metadata,
        "target_ppfd_umol_m2_s": target_metadata["target_ppfd_umol_m2_s"],
        "target_tolerance_umol_m2_s": target_metadata["tolerance_umol_m2_s"],
        "target_classification_basis": target_metadata["target_classification_basis"],
        "target_classification_basis_label": target_metadata[
            "target_classification_basis_label"
        ],
        "target_classification_source": target_metadata["target_classification_source"],
        "target_classification_note": target_metadata["target_classification_note"],
        "target_basis": target_metadata["target_basis"],
        "target_basis_label": target_metadata["target_basis_label"],
        "target_lower_threshold_umol_m2_s": target_metadata[
            "target_lower_threshold_umol_m2_s"
        ],
        "target_upper_threshold_umol_m2_s": target_metadata[
            "target_upper_threshold_umol_m2_s"
        ],
        "target_capping_enabled": target_metadata["target_capping_enabled"],
        "plant_count": len(plant_summaries),
        "leaf_count": len(leaf_summaries),
        "surface_count": len(surface_metrics),
        "one_sided_leaf_area_m2": sum(metric.area_m2 for metric in surface_metrics),
        "total_incident_photon_flux_umol_s": total_incident,
        "total_absorbed_photon_flux_umol_s": total_absorbed,
        "mean_absorbed_fraction_of_incident": (
            total_absorbed / total_incident if total_incident > 0.0 else 0.0
        ),
        "plant_to_plant_absorbed_photon_flux_cv": plant_absorption_cv,
        **leaf_region_counts,
        **surface_region_counts,
        **plant_region_counts,
        "under_lit_leaves": leaf_region_counts["under_lit_leaf_count"],
        "target_range_leaves": leaf_region_counts["target_range_leaf_count"],
        "over_lit_leaves": leaf_region_counts["over_lit_leaf_count"],
        **target_capped,
        "plant_summaries": plant_summaries,
        "leaf_summaries": leaf_summaries,
        "surfaces": surface_rows,
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
    target: Mapping[str, Any] | None = None,
    target_classification_ppfd_by_surface_id: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[SurfacePhotonAbsorption]] = {}
    for metric in surface_metrics:
        key = getattr(metric, key_name)
        grouped.setdefault(key, []).append(metric)

    rows: list[dict[str, Any]] = []
    for key in sorted(grouped):
        items = grouped[key]
        area = sum(item.area_m2 for item in items)
        incident = sum(item.incident_photon_flux_umol_s for item in items)
        absorbed = sum(item.absorbed_photon_flux_umol_s for item in items)
        classification_flux = 0.0
        for item in items:
            raw_density = item.incident_photon_flux_umol_s / item.area_m2 if item.area_m2 > 0.0 else 0.0
            classification_density = (
                float(target_classification_ppfd_by_surface_id[item.surface_id])
                if target_classification_ppfd_by_surface_id is not None
                else raw_density
            )
            classification_flux += classification_density * item.area_m2
        target_ppfd = float(target["target_ppfd_umol_m2_s"]) if target else None
        target_capped_flux = 0.0
        excess_flux = 0.0
        deficit_flux = 0.0
        if target_ppfd is not None:
            for item in items:
                raw_density = item.incident_photon_flux_umol_s / item.area_m2 if item.area_m2 > 0.0 else 0.0
                classification_density = (
                    float(target_classification_ppfd_by_surface_id[item.surface_id])
                    if target_classification_ppfd_by_surface_id is not None
                    else raw_density
                )
                target_capped_flux += min(classification_density, target_ppfd) * item.area_m2
                excess_flux += max(0.0, classification_density - target_ppfd) * item.area_m2
                deficit_flux += max(0.0, target_ppfd - classification_density) * item.area_m2
        row: dict[str, Any] = {
            key_name: key,
            "surface_count": len(items),
            "one_sided_leaf_area_m2": area,
            "incident_photon_flux_umol_s": incident,
            "absorbed_photon_flux_umol_s": absorbed,
            "target_classification_incident_flux_umol_s": classification_flux,
            "target_classification_ppfd_umol_m2_s": (
                classification_flux / area if area > 0.0 else 0.0
            ),
            "incident_photon_flux_density_umol_m2_s": (
                incident / area if area > 0.0 else 0.0
            ),
            "absorbed_photon_flux_density_umol_m2_s": (
                absorbed / area if area > 0.0 else 0.0
            ),
            "absorbed_fraction_of_incident": (
                absorbed / incident if incident > 0.0 else 0.0
            ),
        }
        if target_ppfd is not None:
            row.update(
                _target_metric_fields(
                    capped_flux=target_capped_flux,
                    capped_density=target_capped_flux / area if area > 0.0 else 0.0,
                    excess_flux=excess_flux,
                    deficit_flux=deficit_flux,
                )
            )

        if key_name == "leaf_id":
            plant_ids = sorted({item.plant_id for item in items})
            if len(plant_ids) != 1:
                raise ValueError(f"Leaf {key!r} maps to multiple plant IDs: {plant_ids}")
            row["plant_id"] = plant_ids[0]
            row["leaf_index"] = items[0].leaf_index

        if key_name == "plant_id":
            row["leaf_count"] = len({item.leaf_id for item in items})

        rows.append(row)

    return rows
