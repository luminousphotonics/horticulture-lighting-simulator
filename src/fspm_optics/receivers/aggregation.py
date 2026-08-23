"""Receiver flux-row construction and area-weighted surface aggregation."""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
import math
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping, cast

from fspm_optics.fspm.targets import (
    FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY,
    FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY_LABEL,
    FSPM_TARGET_CLASSIFICATION_NOTE_CANOPY_MAP,
    FSPM_TARGET_CLASSIFICATION_SOURCE_PPFD_MAP,
)
from fspm_optics.plants.absorption import (
    LeafAbsorptionSurface,
    compute_photon_absorption_metrics,
    leaf_absorption_surfaces,
)
from fspm_optics.plants.models import PlantScene, Vector3
from fspm_optics.receivers.samples import (
    RECEIVER_GRANULARITY_LEAF_CENTROID,
    RECEIVER_GRANULARITY_LEAF_QUADRATURE_4,
    RECEIVER_GRANULARITY_MESH_PATCH,
    finite_non_negative,
    leaf_surface_geometry,
    normal_generation_basis,
    normalize_receiver_granularity,
    receiver_area_basis,
    receiver_generation_basis,
    receiver_side_policy,
    surface_geometry_by_id,
)

PLANT_SURFACE_FLUX_SCHEMA = "fspm_optics.fspm.plant_surface_flux.v1"
PLANT_SURFACE_FLUX_SCHEMA_VERSION = 1
BASELINE_PPFD_PROXY_METHOD = "baseline_ppfd_mean_orientation_proxy_v1"
SPATIAL_PPFD_PROXY_METHOD = "baseline_ppfd_spatial_interpolation_orientation_proxy_v1"
RADIANCE_RECEIVER_METHOD = "radiance_leaf_surface_receiver_sampling_v1"
NO_CROP_OUTPUT_TERMS = ["yield", "biomass", "growth", "crop_output"]
BASELINE_PPFD_TRANSPORT_BASIS = "canopy_plane_scalar_par_ppfd"
BASELINE_PPFD_RGB_DECODE_METHOD = "grey_channel_average_after_equality_assertion"
BASELINE_SOURCE_CHANNEL_POLICY = "r_equals_g_equals_b_scalar_par_ppfd_carrier"
PPFD_CONVERSION_BASIS = "radiance_rgb_values_are_scalar_par_ppfd_no_luminous_conversion"


@dataclass(frozen=True)
class PpfdMapField:
    """Interpolatable baseline PPFD field."""

    xs: tuple[float, ...]
    ys: tuple[float, ...]
    values: Mapping[tuple[float, float], float]
    mean_umol_m2_s: float
    min_umol_m2_s: float
    max_umol_m2_s: float
    sample_count: int

    @property
    def rectangular(self) -> bool:
        return len(self.values) == len(self.xs) * len(self.ys)

    def sample(self, x_m: float, y_m: float) -> float:
        x, y = float(x_m), float(y_m)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("PPFD sample coordinates must be finite.")
        if not self.rectangular:
            nearest = min(
                self.values,
                key=lambda key: (key[0] - x) ** 2 + (key[1] - y) ** 2,
            )
            return float(self.values[nearest])
        x0, x1, xt = _axis_bracket(self.xs, x)
        y0, y1, yt = _axis_bracket(self.ys, y)
        low = self.values[(x0, y0)] * (1.0 - xt) + self.values[(x1, y0)] * xt
        high = self.values[(x0, y1)] * (1.0 - xt) + self.values[(x1, y1)] * xt
        return float(low * (1.0 - yt) + high * yt)

    def summary(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "grid_x_count": len(self.xs),
            "grid_y_count": len(self.ys),
            "rectangular": self.rectangular,
            "mean_umol_m2_s": self.mean_umol_m2_s,
            "min_umol_m2_s": self.min_umol_m2_s,
            "max_umol_m2_s": self.max_umol_m2_s,
            "baseline_ppfd_transport_basis": BASELINE_PPFD_TRANSPORT_BASIS,
            "baseline_ppfd_rgb_decode_method": BASELINE_PPFD_RGB_DECODE_METHOD,
            "baseline_source_channel_policy": BASELINE_SOURCE_CHANNEL_POLICY,
            "ppfd_conversion_basis": PPFD_CONVERSION_BASIS,
            "uses_luminous_efficacy_factor": False,
        }


def _axis_bracket(axis: tuple[float, ...], value: float) -> tuple[float, float, float]:
    if not axis:
        raise ValueError("PPFD interpolation axis is empty.")
    if len(axis) == 1 or value <= axis[0]:
        return axis[0], axis[0], 0.0
    if value >= axis[-1]:
        return axis[-1], axis[-1], 0.0
    index = bisect.bisect_left(axis, value)
    lower, upper = axis[index - 1], axis[index]
    return lower, upper, (value - lower) / (upper - lower)


def read_ppfd_map_field(path: str | Path) -> PpfdMapField:
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"PPFD map not found: {source}")
    buckets: dict[tuple[float, float], list[float]] = {}
    sample_count = 0
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.replace(",", " ").split()
        if len(parts) < 4:
            continue
        try:
            key = (round(float(parts[0]), 6), round(float(parts[1]), 6))
            ppfd = finite_non_negative(f"{source}:{line_number}:ppfd", float(parts[3]))
        except ValueError as exc:
            raise ValueError(f"Invalid PPFD map row {line_number}: {line!r}") from exc
        buckets.setdefault(key, []).append(ppfd)
        sample_count += 1
    if not buckets:
        raise ValueError(f"PPFD map has no usable samples: {source}")
    values = {key: fmean(samples) for key, samples in sorted(buckets.items())}
    ppfd_values = list(values.values())
    return PpfdMapField(
        xs=tuple(sorted({key[0] for key in values})),
        ys=tuple(sorted({key[1] for key in values})),
        values=values,
        mean_umol_m2_s=fmean(ppfd_values),
        min_umol_m2_s=min(ppfd_values),
        max_umol_m2_s=max(ppfd_values),
        sample_count=sample_count,
    )


def _proxy_rows(
    scene: PlantScene,
    density_at: Any,
    *,
    source: str,
) -> list[dict[str, Any]]:
    geometry = surface_geometry_by_id(scene)
    max_height = max(float(scene.config.plant_height_m), 1e-9)
    rows: list[dict[str, Any]] = []
    for surface_id, item in sorted(geometry.items()):
        surface = cast(LeafAbsorptionSurface, item["surface"])
        centroid = cast(Vector3, item["centroid_m"])
        normal = cast(Vector3, item["normal"])
        sampled = float(density_at(centroid))
        orientation_factor = 0.35 + 0.65 * abs(normal[2])
        height_factor = 0.90 + 0.20 * min(1.0, max(0.0, centroid[2] / max_height))
        density = sampled * orientation_factor * height_factor
        rows.append(
            {
                "surface_id": surface_id,
                "plant_id": surface.plant_id,
                "leaf_id": surface.leaf_id,
                "leaf_index": surface.leaf_index,
                "face_index": surface.face_index,
                "area_m2": surface.area_m2,
                "centroid_m": list(centroid),
                "normal": list(normal),
                "incident_photon_flux_density_umol_m2_s": density,
                "incident_photon_flux_umol_s": density * surface.area_m2,
                "source": source,
            }
        )
    return rows


def build_baseline_proxy_surface_flux_rows(
    scene: PlantScene, baseline_ppfd_mean_umol_m2_s: float
) -> list[dict[str, Any]]:
    baseline = finite_non_negative(
        "baseline_ppfd_mean_umol_m2_s", baseline_ppfd_mean_umol_m2_s
    )
    return _proxy_rows(scene, lambda _centroid: baseline, source="baseline_ppfd_orientation_proxy")


def build_spatial_proxy_surface_flux_rows(
    scene: PlantScene, ppfd_map_path: str | Path
) -> list[dict[str, Any]]:
    field = read_ppfd_map_field(ppfd_map_path)
    rows = _proxy_rows(
        scene,
        lambda centroid: field.sample(centroid[0], centroid[1]),
        source="baseline_ppfd_spatial_interpolation_orientation_proxy",
    )
    for row in rows:
        centroid = row["centroid_m"]
        row["sampled_ppfd_umol_m2_s"] = field.sample(centroid[0], centroid[1])
    return rows


def _sample_granularity(samples: list[Mapping[str, Any]]) -> str:
    values = {
        normalize_receiver_granularity(sample.get("receiver_granularity"))
        for sample in samples
    }
    if len(values) != 1:
        raise ValueError("Receiver samples must use a single receiver_granularity.")
    return next(iter(values)) if values else RECEIVER_GRANULARITY_MESH_PATCH


def build_radiance_receiver_surface_flux_rows(
    scene: PlantScene,
    receiver_samples: Iterable[Mapping[str, Any]],
    receiver_flux_density_umol_m2_s: Iterable[float],
    *,
    receiver_scale_multiplier: float = 1.0,
) -> list[dict[str, Any]]:
    """Pair provided receiver values with deterministic leaf surfaces."""

    samples = list(receiver_samples)
    densities = [
        finite_non_negative(f"receiver_density[{index}]", value)
        * finite_non_negative("receiver_scale_multiplier", receiver_scale_multiplier)
        for index, value in enumerate(receiver_flux_density_umol_m2_s)
    ]
    if len(samples) != len(densities):
        raise ValueError(
            f"Receiver sample count {len(samples)} does not match output count {len(densities)}."
        )
    granularity = _sample_granularity(samples)
    geometry = surface_geometry_by_id(scene)

    if granularity == RECEIVER_GRANULARITY_MESH_PATCH:
        grouped: dict[str, list[tuple[Mapping[str, Any], float]]] = defaultdict(list)
        for sample, density in zip(samples, densities, strict=True):
            surface_id = sample.get("surface_id")
            if not isinstance(surface_id, str) or surface_id not in geometry:
                raise ValueError(f"Unknown receiver sample surface_id: {surface_id!r}.")
            grouped[surface_id].append((sample, density))
        missing = set(geometry) - set(grouped)
        if missing:
            raise ValueError(f"Missing receiver samples for {len(missing)} surfaces.")
        rows: list[dict[str, Any]] = []
        for surface_id, item in sorted(geometry.items()):
            surface = cast(LeafAbsorptionSurface, item["surface"])
            density = sum(value for _sample, value in grouped[surface_id])
            rows.append(
                _surface_row(
                    surface,
                    cast(Vector3, item["centroid_m"]),
                    cast(Vector3, item["normal"]),
                    density,
                    granularity,
                    len(grouped[surface_id]),
                    side_summaries=[
                        {
                            "sample_id": str(sample.get("sample_id")),
                            "side": str(sample.get("side") or "unknown"),
                            "incident_photon_flux_density_umol_m2_s": value,
                            "incident_photon_flux_umol_s": value * surface.area_m2,
                        }
                        for sample, value in grouped[surface_id]
                    ],
                )
            )
        return rows

    by_leaf: dict[str, list[tuple[Mapping[str, Any], float]]] = defaultdict(list)
    for sample, density in zip(samples, densities, strict=True):
        leaf_id = sample.get("leaf_id")
        if not isinstance(leaf_id, str):
            raise ValueError("Representative receiver sample is missing leaf_id.")
        by_leaf[leaf_id].append((sample, density))
    leaf_geometry = leaf_surface_geometry(scene)
    if set(by_leaf) != set(leaf_geometry):
        raise ValueError("Representative receiver samples do not match generated leaves.")
    rows = []
    for leaf_id, items in sorted(leaf_geometry.items()):
        weighted = 0.0
        represented_area = 0.0
        for sample, density in by_leaf[leaf_id]:
            area = finite_non_negative("receiver sample area", sample.get("area_m2"))
            weighted += density * area
            represented_area += area
        if represented_area <= 0.0:
            raise ValueError(f"Representative receiver area is zero for {leaf_id}.")
        density = weighted / represented_area
        for item in items:
            rows.append(
                _surface_row(
                    cast(LeafAbsorptionSurface, item["surface"]),
                    cast(Vector3, item["centroid_m"]),
                    cast(Vector3, item["normal"]),
                    density,
                    granularity,
                    len(by_leaf[leaf_id]),
                )
            )
    return rows


def _surface_row(
    surface: LeafAbsorptionSurface,
    centroid: Vector3,
    normal: Vector3,
    density: float,
    granularity: str,
    sample_count: int,
    *,
    side_summaries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    row = {
        "surface_id": surface.surface_id,
        "plant_id": surface.plant_id,
        "leaf_id": surface.leaf_id,
        "leaf_index": surface.leaf_index,
        "face_index": surface.face_index,
        "area_m2": surface.area_m2,
        "centroid_m": list(centroid),
        "normal": list(normal),
        "receiver_sample_count": sample_count,
        "receiver_granularity": granularity,
        "receiver_generation_basis": receiver_generation_basis(granularity),
        "receiver_area_basis": receiver_area_basis(granularity),
        "receiver_side_policy": receiver_side_policy(granularity),
        "normal_generation_basis": normal_generation_basis(granularity),
        "incident_photon_flux_density_umol_m2_s": density,
        "incident_photon_flux_umol_s": density * surface.area_m2,
        "source": f"radiance_{granularity}_receiver",
    }
    if side_summaries is not None:
        row["side_summaries"] = side_summaries
    return row


def build_plant_surface_flux_payload(
    scene: PlantScene,
    surface_flux_rows: Iterable[Mapping[str, Any]],
    *,
    method: str,
    source_ppfd_map: str | None = None,
    baseline_ppfd_mean_umol_m2_s: float | None = None,
    ppfd_field_summary: Mapping[str, Any] | None = None,
    target_ppfd_umol_m2_s: float | None = None,
    target_tolerance_umol_m2_s: float | None = None,
    target_classification_ppfd_map_path: str | Path | None = None,
    target_classification_ppfd_by_surface_id: Mapping[str, float] | None = None,
    target_classification_metadata: Mapping[str, Any] | None = None,
    leaf_material_metadata: Mapping[str, Any] | None = None,
    **receiver_metadata: Any,
) -> dict[str, Any]:
    """Aggregate exact surface rows into traceable leaf and plant summaries."""

    row_list = [dict(row) for row in surface_flux_rows]
    rows_by_id = {str(row.get("surface_id")): row for row in row_list}
    if len(rows_by_id) != len(row_list):
        raise ValueError("Surface rows contain duplicate surface IDs.")
    registry = {
        surface.surface_id: surface for surface in leaf_absorption_surfaces(scene)
    }
    if set(rows_by_id) != set(registry):
        missing = sorted(set(registry) - set(rows_by_id))
        unknown = sorted(set(rows_by_id) - set(registry))
        raise ValueError(f"Surface rows do not match registry; missing={missing[:3]}, unknown={unknown[:3]}.")

    classification_metadata = dict(target_classification_metadata or {})
    classification_field_summary: dict[str, Any] = {}
    if target_classification_ppfd_map_path is not None:
        field = read_ppfd_map_field(target_classification_ppfd_map_path)
        target_classification_ppfd_by_surface_id = {
            surface_id: field.sample(
                float(row["centroid_m"][0]),
                float(row["centroid_m"][1]),
            )
            for surface_id, row in rows_by_id.items()
        }
        classification_field_summary = field.summary()
        classification_metadata = {
            "target_classification_basis": FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY,
            "target_classification_basis_label": FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY_LABEL,
            "target_classification_source": FSPM_TARGET_CLASSIFICATION_SOURCE_PPFD_MAP,
            "target_classification_note": FSPM_TARGET_CLASSIFICATION_NOTE_CANOPY_MAP,
            **classification_metadata,
        }
    incident_by_surface_id: dict[str, float] = {}
    for surface_id, surface in sorted(registry.items()):
        row = rows_by_id[surface_id]
        density = finite_non_negative(
            f"{surface_id}.incident density",
            row.get("incident_photon_flux_density_umol_m2_s"),
        )
        incident_by_surface_id[surface_id] = density * surface.area_m2

    absorption_metrics = compute_photon_absorption_metrics(
        scene,
        incident_by_surface_id,
        method=method,
        target_ppfd_umol_m2_s=target_ppfd_umol_m2_s,
        target_tolerance_umol_m2_s=target_tolerance_umol_m2_s,
        target_classification_ppfd_by_surface_id=(
            target_classification_ppfd_by_surface_id
        ),
        target_classification_metadata=classification_metadata,
    )
    metric_by_id = {
        str(row["surface_id"]): row for row in absorption_metrics["surfaces"]
    }
    surfaces = [
        {
            **rows_by_id[surface_id],
            **metric_by_id[surface_id],
        }
        for surface_id in sorted(registry)
    ]
    reserved_metric_keys = {
        "schema",
        "schema_version",
        "method",
        "units",
        "surfaces",
        "plant_summaries",
        "leaf_summaries",
        "outputs_do_not_predict",
        "limitations",
    }
    payload: dict[str, Any] = {
        "schema": PLANT_SURFACE_FLUX_SCHEMA,
        "schema_version": PLANT_SURFACE_FLUX_SCHEMA_VERSION,
        "status": "proxy" if method in {BASELINE_PPFD_PROXY_METHOD, SPATIAL_PPFD_PROXY_METHOD} else "computed",
        "method": method,
        "source_ppfd_map": source_ppfd_map,
        "baseline_ppfd_mean_umol_m2_s": baseline_ppfd_mean_umol_m2_s,
        "ppfd_field_summary": dict(ppfd_field_summary or {}),
        "target_classification_ppfd_field_summary": classification_field_summary,
        **{
            key: value
            for key, value in absorption_metrics.items()
            if key not in reserved_metric_keys
        },
        "plant_summaries": absorption_metrics["plant_summaries"],
        "leaf_summaries": absorption_metrics["leaf_summaries"],
        "surface_summaries": surfaces,
        "units": {"area": "m2", "photon_flux": "umol/s", "photon_flux_density": "umol/m2/s"},
        "outputs_do_not_predict": absorption_metrics["outputs_do_not_predict"],
        "limitations": absorption_metrics["limitations"],
        **dict(leaf_material_metadata or {}),
        **{key: value for key, value in receiver_metadata.items() if value is not None},
    }
    return payload
