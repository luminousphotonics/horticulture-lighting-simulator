"""Deterministic plant surface-flux artifact design scaffold.

This module produces a traceable `plant_surface_flux.json` artifact keyed by
the deterministic leaf-face surface IDs from the plant absorption registry.

The current live integration supports configurable Radiance receiver sampling
granularity while preserving the deterministic leaf-face artifact registry.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from rad_rebuild.radiance.engine.plants.absorption import (
    LeafAbsorptionSurface,
    compute_photon_absorption_metrics,
    leaf_absorption_surfaces,
)
from rad_rebuild.radiance.engine.plants.leaf_materials import opaque_leaf_material_metadata
from rad_rebuild.radiance.engine.plants.models import PlantScene, Vector3
from rad_rebuild.radiance.fspm_targets import (
    FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY,
    FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY_LABEL,
    FSPM_TARGET_CLASSIFICATION_NOTE_CANOPY_MAP,
    FSPM_TARGET_CLASSIFICATION_SOURCE_PPFD_MAP,
)

PLANT_SURFACE_FLUX_SCHEMA = "rad_rebuild.fspm.plant_surface_flux.v1"
PLANT_SURFACE_FLUX_SCHEMA_VERSION = 1
PLANT_SURFACE_FLUX_FILENAME = "plant_surface_flux.json"
BASELINE_PPFD_PROXY_METHOD = "baseline_ppfd_mean_orientation_proxy_v1"
SPATIAL_PPFD_PROXY_METHOD = "baseline_ppfd_spatial_interpolation_orientation_proxy_v1"
RADIANCE_RECEIVER_METHOD = "radiance_leaf_surface_receiver_sampling_v1"
NO_CROP_OUTPUT_TERMS = ["yield", "biomass", "growth", "crop_output"]
FSPM_RECEIVER_GRANULARITY_ENV = "FSPM_RECEIVER_GRANULARITY"
RECEIVER_GRANULARITY_LEAF_CENTROID = "leaf_centroid"
RECEIVER_GRANULARITY_LEAF_QUADRATURE_4 = "leaf_quadrature_4"
RECEIVER_GRANULARITY_MESH_PATCH = "mesh_patch"
# Manual live validation showed quadrature is close enough to mesh-patch
# reference behavior for normal development/demo iteration while avoiding the
# full reference receiver count.
DEFAULT_FSPM_RECEIVER_GRANULARITY = RECEIVER_GRANULARITY_LEAF_QUADRATURE_4
FSPM_RECEIVER_GRANULARITIES = frozenset(
    {
        RECEIVER_GRANULARITY_LEAF_CENTROID,
        RECEIVER_GRANULARITY_LEAF_QUADRATURE_4,
        RECEIVER_GRANULARITY_MESH_PATCH,
    }
)


def normalize_receiver_granularity(value: object) -> str:
    """Return a supported FSPM receiver granularity value."""

    if value is None:
        return DEFAULT_FSPM_RECEIVER_GRANULARITY
    text = str(value).strip().lower()
    if not text:
        return DEFAULT_FSPM_RECEIVER_GRANULARITY
    if text not in FSPM_RECEIVER_GRANULARITIES:
        allowed = ", ".join(sorted(FSPM_RECEIVER_GRANULARITIES))
        raise ValueError(
            f"Unknown {FSPM_RECEIVER_GRANULARITY_ENV}: {value!r}. "
            f"Expected one of: {allowed}."
        )
    return text


def receiver_generation_basis(granularity: str) -> str:
    normalized = normalize_receiver_granularity(granularity)
    if normalized == RECEIVER_GRANULARITY_LEAF_CENTROID:
        return "one_mesh_patch_centroid_nearest_leaf_area_centroid"
    if normalized == RECEIVER_GRANULARITY_LEAF_QUADRATURE_4:
        return "four_area_partition_mesh_patch_centroids_per_leaf"
    return "leaf_surface_patch_centroids_and_normals_front_back_samples"


def receiver_granularity_role(granularity: str) -> str:
    normalized = normalize_receiver_granularity(granularity)
    if normalized == RECEIVER_GRANULARITY_LEAF_CENTROID:
        return "smoke_debug"
    if normalized == RECEIVER_GRANULARITY_LEAF_QUADRATURE_4:
        return "development_demo_default"
    return "high_resolution_reference"


def receiver_area_basis(granularity: str) -> str:
    normalized = normalize_receiver_granularity(granularity)
    if normalized == RECEIVER_GRANULARITY_MESH_PATCH:
        return "one_sided_leaf_mesh_area_front_back_receiver_samples_summed"
    return "one_sided_leaf_mesh_area_representative_sample_weights"


def receiver_side_policy(granularity: str) -> str:
    normalized = normalize_receiver_granularity(granularity)
    if normalized == RECEIVER_GRANULARITY_MESH_PATCH:
        return "front_and_back_per_mesh_surface_row"
    return "single_light_facing_side"


def normal_generation_basis(granularity: str) -> str:
    normalized = normalize_receiver_granularity(granularity)
    if normalized == RECEIVER_GRANULARITY_MESH_PATCH:
        return "mesh_face_normal_with_explicit_backside_sample"
    if normalized == RECEIVER_GRANULARITY_LEAF_CENTROID:
        return "nearest_mesh_patch_to_leaf_area_centroid_oriented_upward"
    return "nearest_mesh_patch_to_area_partition_centroid_oriented_upward"


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


def _normalize_vector(vector: Vector3, *, fallback: Vector3 = (0.0, 0.0, 1.0)) -> Vector3:
    mag = math.sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2])
    if not math.isfinite(mag) or mag <= 0.0:
        return fallback
    return (vector[0] / mag, vector[1] / mag, vector[2] / mag)


def _light_facing_normal(normal: Vector3) -> Vector3:
    unit = _normalize_vector(normal)
    if unit[2] < 0.0:
        return (-unit[0], -unit[1], -unit[2])
    return unit


def _distance_squared(a: Vector3, b: Vector3) -> float:
    return (
        (a[0] - b[0]) * (a[0] - b[0])
        + (a[1] - b[1]) * (a[1] - b[1])
        + (a[2] - b[2]) * (a[2] - b[2])
    )


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


def _leaf_surface_geometry(scene: PlantScene) -> dict[str, list[dict[str, Any]]]:
    by_leaf: dict[str, list[dict[str, Any]]] = {}
    for surface_id, item in _surface_geometry_by_id(scene).items():
        surface = item["surface"]
        if not isinstance(surface, LeafAbsorptionSurface):
            raise ValueError(f"Invalid surface registry entry for {surface_id}.")
        by_leaf.setdefault(surface.leaf_id, []).append(
            {
                **item,
                "surface_id": surface_id,
                "area_m2": surface.area_m2,
                "face_index": surface.face_index,
                "plant_id": surface.plant_id,
                "leaf_id": surface.leaf_id,
                "leaf_index": surface.leaf_index,
            }
        )
    for items in by_leaf.values():
        items.sort(key=lambda item: int(item["face_index"]))
    return by_leaf


def _aggregate_receiver_geometry(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    item_list = list(items)
    total_area = sum(float(item.get("area_m2", 0.0) or 0.0) for item in item_list)
    if total_area <= 0.0:
        raise ValueError("Receiver representative area must be positive.")

    centroid = [0.0, 0.0, 0.0]
    for item in item_list:
        area = float(item["area_m2"])
        item_centroid = item["centroid_m"]
        for index in range(3):
            centroid[index] += float(item_centroid[index]) * area

    area_centroid = tuple(value / total_area for value in centroid)
    # Keep the traced point on an actual mesh patch. Averaged interior points can
    # self-occlude against opaque plant geometry in the FSPM receiver octree.
    representative = min(
        item_list,
        key=lambda item: _distance_squared(item["centroid_m"], area_centroid),
    )
    return {
        "area_m2": total_area,
        "centroid_m": representative["centroid_m"],
        "normal": _light_facing_normal(representative["normal"]),
        "area_weighted_centroid_m": area_centroid,
    }


def _partition_surface_items_by_area(
    items: Iterable[dict[str, Any]],
    partition_count: int,
) -> list[list[dict[str, Any]]]:
    ordered = sorted(items, key=lambda item: int(item["face_index"]))
    if partition_count <= 0:
        raise ValueError("partition_count must be positive.")
    if len(ordered) <= partition_count:
        return [[item] for item in ordered]

    total_area = sum(float(item["area_m2"]) for item in ordered)
    target_area = total_area / partition_count
    partitions: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_area = 0.0
    for index, item in enumerate(ordered):
        remaining_items = len(ordered) - index
        remaining_partitions = partition_count - len(partitions)
        if (
            len(partitions) < partition_count - 1
            and current
            and current_area >= target_area
            and remaining_items >= remaining_partitions
        ):
            partitions.append(current)
            current = []
            current_area = 0.0
        current.append(item)
        current_area += float(item["area_m2"])
    if current:
        partitions.append(current)
    return partitions


def _receiver_sample_metadata(
    *,
    sample_id: str,
    surface_id: str,
    plant_id: str,
    leaf_id: str,
    leaf_index: int,
    face_index: int | None,
    side: str,
    centroid: Vector3,
    direction: Vector3,
    area_m2: float,
    offset_m: float,
    granularity: str,
    representative_sample_count: int,
) -> dict[str, Any]:
    origin = (
        centroid[0] + direction[0] * offset_m,
        centroid[1] + direction[1] * offset_m,
        centroid[2] + direction[2] * offset_m,
    )
    return {
        "sample_id": sample_id,
        "surface_id": surface_id,
        "plant_id": plant_id,
        "leaf_id": leaf_id,
        "leaf_index": leaf_index,
        "face_index": face_index,
        "side": side,
        "origin_m": [float(value) for value in origin],
        "direction": [float(value) for value in direction],
        "area_m2": float(area_m2),
        "receiver_granularity": granularity,
        "receiver_generation_basis": receiver_generation_basis(granularity),
        "receiver_area_basis": receiver_area_basis(granularity),
        "receiver_side_policy": receiver_side_policy(granularity),
        "normal_generation_basis": normal_generation_basis(granularity),
        "leaf_representative_sample_count": representative_sample_count,
    }



@dataclass(frozen=True)
class PpfdMapField:
    """Interpolatable unblocked baseline PPFD map."""

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
        x = float(x_m)
        y = float(y_m)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("PPFD sample coordinates must be finite.")

        if not self.rectangular:
            nearest = min(
                self.values,
                key=lambda key: (key[0] - x) * (key[0] - x) + (key[1] - y) * (key[1] - y),
            )
            return float(self.values[nearest])

        x0, x1, xt = _axis_bracket(self.xs, x)
        y0, y1, yt = _axis_bracket(self.ys, y)

        q00 = float(self.values[(x0, y0)])
        q10 = float(self.values[(x1, y0)])
        q01 = float(self.values[(x0, y1)])
        q11 = float(self.values[(x1, y1)])

        low = q00 * (1.0 - xt) + q10 * xt
        high = q01 * (1.0 - xt) + q11 * xt
        return low * (1.0 - yt) + high * yt

    def summary(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "grid_x_count": len(self.xs),
            "grid_y_count": len(self.ys),
            "rectangular": self.rectangular,
            "mean_umol_m2_s": self.mean_umol_m2_s,
            "min_umol_m2_s": self.min_umol_m2_s,
            "max_umol_m2_s": self.max_umol_m2_s,
        }


def _axis_bracket(axis: tuple[float, ...], value: float) -> tuple[float, float, float]:
    if not axis:
        raise ValueError("PPFD interpolation axis is empty.")
    if len(axis) == 1 or value <= axis[0]:
        return axis[0], axis[0], 0.0
    if value >= axis[-1]:
        return axis[-1], axis[-1], 0.0

    index = bisect.bisect_left(axis, value)
    lower = axis[index - 1]
    upper = axis[index]
    fraction = (value - lower) / (upper - lower) if upper > lower else 0.0
    return lower, upper, min(1.0, max(0.0, fraction))


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
            x = round(float(parts[0]), 6)
            y = round(float(parts[1]), 6)
            ppfd = _finite_non_negative(f"{source}:{line_number}:ppfd", float(parts[3]))
        except ValueError as exc:
            raise ValueError(f"Invalid PPFD map row {line_number}: {line!r}") from exc
        buckets.setdefault((x, y), []).append(ppfd)
        sample_count += 1

    if not buckets:
        raise ValueError(f"PPFD map has no usable samples: {source}")

    values = {
        key: sum(samples) / len(samples)
        for key, samples in sorted(buckets.items())
    }
    ppfd_values = list(values.values())
    xs = tuple(sorted({key[0] for key in values}))
    ys = tuple(sorted({key[1] for key in values}))

    return PpfdMapField(
        xs=xs,
        ys=ys,
        values=values,
        mean_umol_m2_s=sum(ppfd_values) / len(ppfd_values),
        min_umol_m2_s=min(ppfd_values),
        max_umol_m2_s=max(ppfd_values),
        sample_count=sample_count,
    )


def build_spatial_proxy_surface_flux_rows(
    scene: PlantScene,
    ppfd_map_path: str | Path,
) -> list[dict[str, Any]]:
    """Create proxy surface flux rows by spatially sampling the baseline PPFD map."""

    field = read_ppfd_map_field(ppfd_map_path)
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
        sampled_ppfd = field.sample(float(centroid[0]), float(centroid[1]))
        orientation_factor = 0.35 + 0.65 * abs(float(normal[2]))
        height_factor = 0.90 + 0.20 * min(1.0, max(0.0, float(centroid[2]) / max_height))
        incident_density = sampled_ppfd * orientation_factor * height_factor
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
                "sampled_ppfd_umol_m2_s": sampled_ppfd,
                "incident_photon_flux_density_umol_m2_s": incident_density,
                "incident_photon_flux_umol_s": incident_density * surface.area_m2,
                "source": "baseline_ppfd_spatial_interpolation_orientation_proxy",
            }
        )
    return rows

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



def build_radiance_receiver_samples(
    scene: PlantScene,
    *,
    receiver_granularity: str | None = None,
    two_sided: bool | None = None,
    offset_m: float = 0.0005,
) -> list[dict[str, Any]]:
    """Build rtrace -I+ receiver samples at deterministic plant receiver points.

    `mesh_patch` preserves the original per-mesh-face, front/back sampling. The
    lower-cost modes trace representative leaf samples and later expand their
    area-weighted density back onto the deterministic mesh-face registry.
    """

    granularity = normalize_receiver_granularity(receiver_granularity)
    if offset_m < 0.0 or not math.isfinite(offset_m):
        raise ValueError("offset_m must be finite and non-negative.")

    if granularity == RECEIVER_GRANULARITY_MESH_PATCH:
        return _build_mesh_patch_receiver_samples(
            scene,
            two_sided=True if two_sided is None else two_sided,
            offset_m=offset_m,
        )
    if two_sided:
        raise ValueError(
            "two_sided receiver sampling is only supported for "
            f"{RECEIVER_GRANULARITY_MESH_PATCH} granularity."
        )
    if granularity == RECEIVER_GRANULARITY_LEAF_CENTROID:
        return _build_leaf_centroid_receiver_samples(scene, offset_m=offset_m)
    if granularity == RECEIVER_GRANULARITY_LEAF_QUADRATURE_4:
        return _build_leaf_quadrature_receiver_samples(scene, offset_m=offset_m)
    raise AssertionError(f"Unhandled receiver granularity: {granularity}")


def _build_mesh_patch_receiver_samples(
    scene: PlantScene,
    *,
    two_sided: bool,
    offset_m: float,
) -> list[dict[str, Any]]:
    geometry = _surface_geometry_by_id(scene)
    samples: list[dict[str, Any]] = []

    for surface_id in sorted(geometry):
        item = geometry[surface_id]
        surface = item["surface"]
        if not isinstance(surface, LeafAbsorptionSurface):
            raise ValueError(f"Invalid surface registry entry for {surface_id}.")
        centroid = item["centroid_m"]
        normal = item["normal"]

        directions: list[tuple[str, Vector3]] = [("front", normal)]
        if two_sided:
            directions.append(("back", (-normal[0], -normal[1], -normal[2])))

        for side, direction in directions:
            samples.append(
                _receiver_sample_metadata(
                    sample_id=f"{surface_id}_{side}",
                    surface_id=surface.surface_id,
                    plant_id=surface.plant_id,
                    leaf_id=surface.leaf_id,
                    leaf_index=surface.leaf_index,
                    face_index=surface.face_index,
                    side=side,
                    centroid=centroid,
                    direction=direction,
                    area_m2=surface.area_m2,
                    offset_m=offset_m,
                    granularity=RECEIVER_GRANULARITY_MESH_PATCH,
                    representative_sample_count=2 if two_sided else 1,
                )
            )

    return samples


def _build_leaf_centroid_receiver_samples(
    scene: PlantScene,
    *,
    offset_m: float,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for leaf_id, items in sorted(_leaf_surface_geometry(scene).items()):
        representative = _aggregate_receiver_geometry(items)
        first = items[0]
        samples.append(
            _receiver_sample_metadata(
                sample_id=f"{leaf_id}_centroid",
                surface_id=f"{leaf_id}_centroid",
                plant_id=str(first["plant_id"]),
                leaf_id=leaf_id,
                leaf_index=int(first["leaf_index"]),
                face_index=None,
                side="front",
                centroid=representative["centroid_m"],
                direction=representative["normal"],
                area_m2=representative["area_m2"],
                offset_m=offset_m,
                granularity=RECEIVER_GRANULARITY_LEAF_CENTROID,
                representative_sample_count=1,
            )
        )
    return samples


def _build_leaf_quadrature_receiver_samples(
    scene: PlantScene,
    *,
    offset_m: float,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for leaf_id, items in sorted(_leaf_surface_geometry(scene).items()):
        first = items[0]
        buckets = _partition_surface_items_by_area([dict(item) for item in items], 4)
        non_empty_bucket_count = len(buckets)
        for bucket_index, bucket in enumerate(buckets):
            representative = _aggregate_receiver_geometry(bucket)
            samples.append(
                _receiver_sample_metadata(
                    sample_id=f"{leaf_id}_quadrature_{bucket_index + 1}",
                    surface_id=f"{leaf_id}_quadrature_{bucket_index + 1}",
                    plant_id=str(first["plant_id"]),
                    leaf_id=leaf_id,
                    leaf_index=int(first["leaf_index"]),
                    face_index=None,
                    side="front",
                    centroid=representative["centroid_m"],
                    direction=representative["normal"],
                    area_m2=representative["area_m2"],
                    offset_m=offset_m,
                    granularity=RECEIVER_GRANULARITY_LEAF_QUADRATURE_4,
                    representative_sample_count=non_empty_bucket_count,
                )
            )
    return samples


def receiver_sample_input_text(samples: Iterable[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for sample in samples:
        origin = sample.get("origin_m")
        direction = sample.get("direction")
        if not isinstance(origin, list) or len(origin) != 3:
            raise ValueError("Receiver sample origin_m must contain three values.")
        if not isinstance(direction, list) or len(direction) != 3:
            raise ValueError("Receiver sample direction must contain three values.")
        values = [*origin, *direction]
        if any(not isinstance(value, int | float) or not math.isfinite(float(value)) for value in values):
            raise ValueError("Receiver sample input contains a non-finite value.")
        lines.append(
            f"{float(origin[0]):.6f} {float(origin[1]):.6f} {float(origin[2]):.6f} "
            f"{float(direction[0]):.8f} {float(direction[1]):.8f} {float(direction[2]):.8f}\n"
        )
    return "".join(lines)


def parse_rtrace_receiver_output(text: str) -> list[float]:
    densities: list[float] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 3:
            raise ValueError(f"Malformed rtrace receiver output row {line_number}: {line!r}")
        try:
            channels = [float(parts[-3]), float(parts[-2]), float(parts[-1])]
        except ValueError as exc:
            raise ValueError(f"Malformed rtrace receiver output row {line_number}: {line!r}") from exc
        if any(not math.isfinite(channel) or channel < 0.0 for channel in channels):
            raise ValueError(f"Invalid rtrace receiver output row {line_number}: {line!r}")
        densities.append(sum(channels) / 3.0)
    return densities


def build_radiance_receiver_surface_flux_rows(
    scene: PlantScene,
    receiver_samples: Iterable[Mapping[str, Any]],
    receiver_flux_density_umol_m2_s: Iterable[float],
    *,
    receiver_scale_multiplier: float = 1.0,
) -> list[dict[str, Any]]:
    """Aggregate rtrace receiver samples into surface flux rows."""

    samples = list(receiver_samples)
    granularity = _receiver_granularity_from_samples(samples)
    if granularity in {
        RECEIVER_GRANULARITY_LEAF_CENTROID,
        RECEIVER_GRANULARITY_LEAF_QUADRATURE_4,
    }:
        return _build_representative_receiver_surface_flux_rows(
            scene,
            samples,
            receiver_flux_density_umol_m2_s,
            receiver_scale_multiplier=receiver_scale_multiplier,
            receiver_granularity=granularity,
        )
    return _build_mesh_patch_receiver_surface_flux_rows(
        scene,
        samples,
        receiver_flux_density_umol_m2_s,
        receiver_scale_multiplier=receiver_scale_multiplier,
    )


def _receiver_granularity_from_samples(samples: Iterable[Mapping[str, Any]]) -> str:
    granularities = {
        sample.get("receiver_granularity")
        for sample in samples
        if isinstance(sample.get("receiver_granularity"), str)
    }
    if not granularities:
        return RECEIVER_GRANULARITY_MESH_PATCH
    if len(granularities) != 1:
        raise ValueError("Receiver samples must use a single receiver_granularity.")
    return normalize_receiver_granularity(next(iter(granularities)))


def _build_mesh_patch_receiver_surface_flux_rows(
    scene: PlantScene,
    receiver_samples: Iterable[Mapping[str, Any]],
    receiver_flux_density_umol_m2_s: Iterable[float],
    *,
    receiver_scale_multiplier: float,
) -> list[dict[str, Any]]:
    scale = _finite_non_negative("receiver_scale_multiplier", receiver_scale_multiplier)
    samples = list(receiver_samples)
    densities = [
        _finite_non_negative(f"receiver_flux_density_umol_m2_s[{index}]", value)
        for index, value in enumerate(receiver_flux_density_umol_m2_s)
    ]
    if len(samples) != len(densities):
        raise ValueError(
            f"Receiver sample count {len(samples)} does not match rtrace output count {len(densities)}."
        )

    geometry = _surface_geometry_by_id(scene)
    expected_ids = set(geometry)
    grouped: dict[str, list[tuple[Mapping[str, Any], float]]] = {}

    for sample, density in zip(samples, densities, strict=True):
        surface_id = sample.get("surface_id")
        if not isinstance(surface_id, str) or surface_id not in expected_ids:
            raise ValueError(f"Unknown receiver sample surface_id: {surface_id!r}.")
        grouped.setdefault(surface_id, []).append((sample, density * scale))

    missing = sorted(expected_ids - set(grouped))
    if missing:
        raise ValueError(f"Missing receiver samples for {len(missing)} plant surfaces.")

    rows: list[dict[str, Any]] = []
    for surface_id in sorted(grouped):
        item = geometry[surface_id]
        surface = item["surface"]
        if not isinstance(surface, LeafAbsorptionSurface):
            raise ValueError(f"Invalid surface registry entry for {surface_id}.")
        sample_items = grouped[surface_id]
        incident_density = sum(density for _sample, density in sample_items)
        centroid = item["centroid_m"]
        normal = item["normal"]
        rows.append(
            {
                "surface_id": surface.surface_id,
                "plant_id": surface.plant_id,
                "leaf_id": surface.leaf_id,
                "leaf_index": surface.leaf_index,
                "face_index": surface.face_index,
                "area_m2": surface.area_m2,
                "centroid_m": [float(value) for value in centroid],
                "normal": [float(value) for value in normal],
                "receiver_sample_count": len(sample_items),
                "receiver_granularity": RECEIVER_GRANULARITY_MESH_PATCH,
                "receiver_generation_basis": receiver_generation_basis(
                    RECEIVER_GRANULARITY_MESH_PATCH
                ),
                "receiver_area_basis": receiver_area_basis(
                    RECEIVER_GRANULARITY_MESH_PATCH
                ),
                "receiver_side_policy": receiver_side_policy(
                    RECEIVER_GRANULARITY_MESH_PATCH
                ),
                "receiver_rows_per_mesh_surface_row": len(sample_items),
                "normal_generation_basis": normal_generation_basis(
                    RECEIVER_GRANULARITY_MESH_PATCH
                ),
                "receiver_sides": [
                    str(sample.get("side") or "unknown")
                    for sample, _density in sample_items
                ],
                "incident_photon_flux_density_umol_m2_s": incident_density,
                "incident_photon_flux_umol_s": incident_density * surface.area_m2,
                "source": "radiance_two_sided_leaf_surface_receiver",
            }
        )

    return rows


def _build_representative_receiver_surface_flux_rows(
    scene: PlantScene,
    receiver_samples: Iterable[Mapping[str, Any]],
    receiver_flux_density_umol_m2_s: Iterable[float],
    *,
    receiver_scale_multiplier: float,
    receiver_granularity: str,
) -> list[dict[str, Any]]:
    scale = _finite_non_negative("receiver_scale_multiplier", receiver_scale_multiplier)
    samples = list(receiver_samples)
    densities = [
        _finite_non_negative(f"receiver_flux_density_umol_m2_s[{index}]", value)
        for index, value in enumerate(receiver_flux_density_umol_m2_s)
    ]
    if len(samples) != len(densities):
        raise ValueError(
            f"Receiver sample count {len(samples)} does not match rtrace output count {len(densities)}."
        )

    by_leaf: dict[str, list[tuple[Mapping[str, Any], float]]] = {}
    for sample, density in zip(samples, densities, strict=True):
        sample_granularity = normalize_receiver_granularity(sample.get("receiver_granularity"))
        if sample_granularity != receiver_granularity:
            raise ValueError("Receiver samples must use a single receiver_granularity.")
        leaf_id = sample.get("leaf_id")
        if not isinstance(leaf_id, str) or not leaf_id:
            raise ValueError("Representative receiver sample is missing leaf_id.")
        by_leaf.setdefault(leaf_id, []).append((sample, density * scale))

    leaf_geometry = _leaf_surface_geometry(scene)
    expected_leaf_ids = set(leaf_geometry)
    missing = sorted(expected_leaf_ids - set(by_leaf))
    unknown = sorted(set(by_leaf) - expected_leaf_ids)
    if missing:
        raise ValueError(f"Missing representative receiver samples for {len(missing)} leaves.")
    if unknown:
        raise ValueError(f"Unknown representative receiver leaf_id: {unknown[:3]}")

    rows: list[dict[str, Any]] = []
    basis = receiver_generation_basis(receiver_granularity)
    for leaf_id in sorted(leaf_geometry):
        sample_items = by_leaf[leaf_id]
        sample_area_density_total = 0.0
        representative_area_total = 0.0
        for sample, density in sample_items:
            area = _finite_non_negative(
                f"receiver_sample_area_m2[{sample.get('sample_id', leaf_id)}]",
                sample.get("area_m2"),
            )
            representative_area_total += area
            sample_area_density_total += density * area
        leaf_area = sum(float(item["area_m2"]) for item in leaf_geometry[leaf_id])
        if representative_area_total <= 0.0:
            raise ValueError(f"Representative receiver area is zero for leaf_id {leaf_id!r}.")
        incident_density = sample_area_density_total / representative_area_total

        for item in leaf_geometry[leaf_id]:
            surface = item["surface"]
            if not isinstance(surface, LeafAbsorptionSurface):
                raise ValueError(f"Invalid surface registry entry for {item['surface_id']}.")
            centroid = item["centroid_m"]
            normal = item["normal"]
            rows.append(
                {
                    "surface_id": surface.surface_id,
                    "plant_id": surface.plant_id,
                    "leaf_id": surface.leaf_id,
                    "leaf_index": surface.leaf_index,
                    "face_index": surface.face_index,
                    "area_m2": surface.area_m2,
                    "centroid_m": [float(value) for value in centroid],
                    "normal": [float(value) for value in normal],
                    "receiver_sample_count": len(sample_items),
                    "receiver_granularity": receiver_granularity,
                    "receiver_generation_basis": basis,
                    "receiver_area_basis": receiver_area_basis(receiver_granularity),
                    "receiver_side_policy": receiver_side_policy(receiver_granularity),
                    "receiver_rows_per_mesh_surface_row": (
                        len(sample_items) / len(leaf_geometry[leaf_id])
                    ),
                    "normal_generation_basis": normal_generation_basis(
                        receiver_granularity
                    ),
                    "representative_leaf_area_m2": leaf_area,
                    "representative_receiver_area_m2": representative_area_total,
                    "incident_photon_flux_density_umol_m2_s": incident_density,
                    "incident_photon_flux_umol_s": incident_density * surface.area_m2,
                    "source": f"radiance_{receiver_granularity}_receiver",
                }
            )

    return rows


def write_radiance_receiver_plant_surface_flux_artifact(
    target_dir: str | Path,
    scene: PlantScene,
    receiver_samples: Iterable[Mapping[str, Any]],
    receiver_flux_density_umol_m2_s: Iterable[float],
    *,
    receiver_scale_multiplier: float = 1.0,
    source_octree: str | None = None,
    receiver_granularity: str | None = None,
    baseline_transport_scene: str = "room_emitters_only",
    fspm_receiver_transport_scene: str = "room_emitters_plants",
    receiver_trace_count: int = 1,
    target_ppfd_umol_m2_s: float | None = None,
    target_tolerance_umol_m2_s: float | None = None,
    target_classification_ppfd_map_path: str | Path | None = None,
    leaf_material_metadata: Mapping[str, Any] | None = None,
) -> Path:
    samples = list(receiver_samples)
    detected_granularity = _receiver_granularity_from_samples(samples)
    granularity = normalize_receiver_granularity(receiver_granularity or detected_granularity)
    if granularity != detected_granularity:
        raise ValueError(
            "receiver_granularity does not match receiver sample metadata: "
            f"{granularity!r} != {detected_granularity!r}."
        )
    leaf_count = len({leaf.leaf_id for plant in scene.plants for leaf in plant.leaves})
    surfaces = leaf_absorption_surfaces(scene)
    surface_count = len(surfaces)
    one_sided_area = sum(surface.area_m2 for surface in surfaces)
    receiver_sample_count = len(samples)
    receiver_samples_per_leaf = (
        receiver_sample_count / leaf_count if leaf_count else 0.0
    )
    receiver_rows_per_mesh_surface_row = (
        receiver_sample_count / surface_count if surface_count else 0.0
    )
    generation_basis = receiver_generation_basis(granularity)
    area_basis = receiver_area_basis(granularity)
    side_policy = receiver_side_policy(granularity)
    normal_basis = normal_generation_basis(granularity)
    granularity_role = receiver_granularity_role(granularity)
    material_metadata = dict(leaf_material_metadata or opaque_leaf_material_metadata())
    receiver_sample_area_sum = sum(
        _finite_non_negative(
            f"receiver_sample_area_m2[{sample.get('sample_id', index)}]",
            sample.get("area_m2"),
        )
        for index, sample in enumerate(samples)
    )
    rows = build_radiance_receiver_surface_flux_rows(
        scene,
        samples,
        receiver_flux_density_umol_m2_s,
        receiver_scale_multiplier=receiver_scale_multiplier,
    )
    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=RADIANCE_RECEIVER_METHOD,
        source_ppfd_map=None,
        ppfd_field_summary={
            "receiver_sample_count": receiver_sample_count,
            "receiver_granularity": granularity,
            "receiver_samples_per_leaf": receiver_samples_per_leaf,
            "receiver_generation_basis": generation_basis,
            "receiver_represented_area_m2": one_sided_area,
            "receiver_sample_area_sum_m2": receiver_sample_area_sum,
            "receiver_area_basis": area_basis,
            "receiver_side_policy": side_policy,
            "receiver_rows_per_mesh_surface_row": receiver_rows_per_mesh_surface_row,
            "normal_generation_basis": normal_basis,
            "receiver_granularity_role": granularity_role,
            **material_metadata,
            "two_sided": any(sample.get("side") == "back" for sample in samples),
            "source_octree": source_octree,
            "receiver_scale_multiplier": receiver_scale_multiplier,
        },
        receiver_sample_count=receiver_sample_count,
        receiver_granularity=granularity,
        receiver_samples_per_leaf=receiver_samples_per_leaf,
        receiver_generation_basis=generation_basis,
        receiver_represented_area_m2=one_sided_area,
        receiver_sample_area_sum_m2=receiver_sample_area_sum,
        receiver_area_basis=area_basis,
        receiver_side_policy=side_policy,
        receiver_rows_per_mesh_surface_row=receiver_rows_per_mesh_surface_row,
        normal_generation_basis=normal_basis,
        receiver_granularity_role_value=granularity_role,
        leaf_material_metadata=material_metadata,
        target_ppfd_umol_m2_s=target_ppfd_umol_m2_s,
        target_tolerance_umol_m2_s=target_tolerance_umol_m2_s,
        target_classification_ppfd_map_path=target_classification_ppfd_map_path,
    )
    payload.update(
        {
            "baseline_transport_scene": baseline_transport_scene,
            "fspm_receiver_transport_scene": fspm_receiver_transport_scene,
            "receiver_trace_count": receiver_trace_count,
            "receiver_sample_count": receiver_sample_count,
            "receiver_granularity": granularity,
            "receiver_samples_per_leaf": receiver_samples_per_leaf,
            "receiver_generation_basis": generation_basis,
            "receiver_represented_area_m2": one_sided_area,
            "receiver_sample_area_sum_m2": receiver_sample_area_sum,
            "receiver_area_basis": area_basis,
            "receiver_side_policy": side_policy,
            "receiver_rows_per_mesh_surface_row": receiver_rows_per_mesh_surface_row,
            "normal_generation_basis": normal_basis,
            "receiver_granularity_role": granularity_role,
            **material_metadata,
        }
    )
    return write_plant_surface_flux_artifact(target_dir, payload)

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


def _target_classification_from_ppfd_map(
    normalized_rows: Iterable[Mapping[str, Any]],
    ppfd_map_path: str | Path,
) -> tuple[dict[str, float], dict[str, Any]]:
    field = read_ppfd_map_field(ppfd_map_path)
    classification: dict[str, float] = {}
    for row in normalized_rows:
        surface_id = row.get("surface_id")
        centroid = row.get("centroid_m")
        if not isinstance(surface_id, str) or not surface_id:
            raise ValueError("Target classification row is missing surface_id.")
        if (
            not isinstance(centroid, list)
            or len(centroid) < 2
            or not all(isinstance(value, int | float) for value in centroid[:2])
        ):
            raise ValueError(
                f"Surface {surface_id!r} is missing centroid_m for PPFD map sampling."
            )
        classification[surface_id] = field.sample(float(centroid[0]), float(centroid[1]))
    return classification, {
        "target_classification_basis": FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY,
        "target_classification_basis_label": FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY_LABEL,
        "target_classification_source": FSPM_TARGET_CLASSIFICATION_SOURCE_PPFD_MAP,
        "target_classification_note": FSPM_TARGET_CLASSIFICATION_NOTE_CANOPY_MAP,
        "target_classification_ppfd_field_summary": field.summary(),
    }


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
    key = "incident_photon_flux_density_umol_m2_s"
    leaf_min, leaf_max = _density_range(leaf_summaries, key)
    surface_min, surface_max = _density_range(surface_summaries, key)
    return {
        "color_metric": key,
        "color_quantity": "incident_leaf_surface_ppfd",
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
                key: row[key],
                "visual_intensity_0_1": _visual_value(row[key], min_value=leaf_min, max_value=leaf_max),
            }
            for row in leaf_summaries
        ],
        "plant_values": [
            {
                "plant_id": row["plant_id"],
                "lighting_region": row["lighting_region"],
                key: row[key],
            }
            for row in plant_summaries
        ],
        "surface_values": [
            {
                "surface_id": row["surface_id"],
                "leaf_id": row["leaf_id"],
                "plant_id": row["plant_id"],
                "lighting_region": row["lighting_region"],
                key: row[key],
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
    ppfd_field_summary: Mapping[str, Any] | None = None,
    target_ppfd_umol_m2_s: float | None = None,
    target_tolerance_umol_m2_s: float | None = None,
    target_classification_ppfd_map_path: str | Path | None = None,
    target_classification_ppfd_by_surface_id: Mapping[str, float] | None = None,
    target_classification_metadata: Mapping[str, Any] | None = None,
    receiver_sample_count: int | None = None,
    receiver_granularity: str | None = None,
    receiver_samples_per_leaf: float | None = None,
    receiver_generation_basis: str | None = None,
    receiver_represented_area_m2: float | None = None,
    receiver_sample_area_sum_m2: float | None = None,
    receiver_area_basis: str | None = None,
    receiver_side_policy: str | None = None,
    receiver_rows_per_mesh_surface_row: float | None = None,
    normal_generation_basis: str | None = None,
    receiver_granularity_role_value: str | None = None,
    leaf_material_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_rows, incident_by_surface_id = _normalize_surface_rows(scene, surface_flux_rows)
    classification_by_surface_id = target_classification_ppfd_by_surface_id
    metadata_keys = {
        "target_classification_basis",
        "target_classification_basis_label",
        "target_classification_source",
        "target_classification_note",
    }
    classification_metadata = {
        key: value
        for key, value in dict(target_classification_metadata or {}).items()
        if key in metadata_keys
    }
    classification_field_summary: Mapping[str, Any] | None = None
    if target_classification_ppfd_map_path is not None:
        classification_by_surface_id, map_metadata = _target_classification_from_ppfd_map(
            normalized_rows,
            target_classification_ppfd_map_path,
        )
        classification_field_summary = map_metadata.pop(
            "target_classification_ppfd_field_summary",
            None,
        )
        classification_metadata = {**map_metadata, **classification_metadata}
    absorption_metrics = compute_photon_absorption_metrics(
        scene,
        incident_by_surface_id,
        method=method,
        target_ppfd_umol_m2_s=target_ppfd_umol_m2_s,
        target_tolerance_umol_m2_s=target_tolerance_umol_m2_s,
        target_classification_ppfd_by_surface_id=classification_by_surface_id,
        target_classification_metadata=classification_metadata,
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

    target_keys = (
        "target",
        "target_ppfd_umol_m2_s",
        "target_tolerance_umol_m2_s",
        "target_classification_basis",
        "target_classification_basis_label",
        "target_classification_source",
        "target_classification_note",
        "target_basis",
        "target_basis_label",
        "target_lower_threshold_umol_m2_s",
        "target_upper_threshold_umol_m2_s",
        "target_capping_enabled",
        "under_lit_leaf_count",
        "target_range_leaf_count",
        "over_lit_leaf_count",
        "under_lit_leaf_fraction",
        "target_range_leaf_fraction",
        "over_lit_leaf_fraction",
        "under_lit_leaves",
        "target_range_leaves",
        "over_lit_leaves",
        "under_lit_surface_count",
        "target_range_surface_count",
        "over_lit_surface_count",
        "under_lit_surface_fraction",
        "target_range_surface_fraction",
        "over_lit_surface_fraction",
        "under_lit_plant_count",
        "target_range_plant_count",
        "over_lit_plant_count",
        "under_lit_plant_fraction",
        "target_range_plant_fraction",
        "over_lit_plant_fraction",
        "raw_mean_flux_density_umol_m2_s",
        "target_classification_mean_ppfd_umol_m2_s",
        "target_classification_total_incident_flux_umol_s",
        "target_capped_incident_mean_flux_density_umol_m2_s",
        "target_capped_incident_flux_total_umol_s",
        "target_capped_incident_total_flux_umol_s",
        "excess_incident_flux_above_target_umol_s",
        "excess_incident_flux_fraction",
        "deficit_to_target_incident_flux_umol_s",
        "deficit_to_target_incident_flux_fraction",
        "target_capped_mean_flux_density_umol_m2_s",
        "raw_total_flux_umol_s",
        "target_capped_flux_total_umol_s",
        "target_capped_total_flux_umol_s",
        "excess_flux_above_target_umol_s",
        "excess_flux_fraction",
        "under_target_deficit_umol_s",
        "under_target_deficit_fraction",
        "lower_tail_raw_flux_density_umol_m2_s",
        "lower_tail_target_classification_ppfd_umol_m2_s",
        "lower_tail_target_capped_incident_flux_density_umol_m2_s",
        "lower_tail_target_capped_flux_density_umol_m2_s",
        "plant_to_plant_target_capped_incident_flux_cv",
        "plant_to_plant_target_capped_flux_cv",
    )
    status = "proxy" if method in {BASELINE_PPFD_PROXY_METHOD, SPATIAL_PPFD_PROXY_METHOD} else "computed"
    material_metadata = dict(leaf_material_metadata or {})
    warnings = [
        "This artifact is the incident leaf-surface flux contract; use plant_spectral_absorption.json for modeled spectral absorbed/reflected/transmitted leaf photon flux when available.",
        "Plant geometry is not inserted into the baseline PPFD octree and does not shadow the heatmap.",
    ]
    limitations = [
        "Legacy broadband absorbed fields are scalar optical-assumption diagnostics, not Rex optical-profile spectral absorption.",
    ]
    if status == "proxy":
        warnings.append(
            "Proxy methods use an unblocked baseline PPFD field, not final plant-shaded Radiance receiver sampling."
        )
        limitations.insert(
            0,
            "Per-surface flux values are contract-valid proxy values until the Radiance receiver-sample method is reviewed.",
        )
    else:
        warnings.append(
            "Computed receiver flux uses a plant-inclusive FSPM receiver scene separate from baseline canopy-plane PPFD transport."
        )

    return {
        "schema": PLANT_SURFACE_FLUX_SCHEMA,
        "schema_version": PLANT_SURFACE_FLUX_SCHEMA_VERSION,
        "artifact_role": "incident_leaf_surface_flux",
        "artifact_description": (
            "Incident leaf receiver-sample photon flux and target-fit metrics. "
            "Legacy broadband absorbed fields use configured scalar optical "
            "assumptions and are not wavelength-resolved modeled leaf absorption."
        ),
        "modeled_spectral_absorption_artifact": "runtime_state/plant_spectral_absorption.json",
        "status": status,
        "method": method,
        "source_ppfd_map": source_ppfd_map,
        "baseline_ppfd_mean_umol_m2_s": baseline_ppfd_mean_umol_m2_s,
        "ppfd_field_summary": dict(ppfd_field_summary or {}),
        "target_classification_ppfd_field_summary": dict(
            classification_field_summary or {}
        ),
        "units": {
            "area": "m2",
            "incident_photon_flux": "umol/s",
            "absorbed_photon_flux": "umol/s",
            "photon_flux_density": "umol/m2/s",
            "target_classification_ppfd": "umol/m2/s",
        },
        "plant_count": absorption_metrics["plant_count"],
        "leaf_count": absorption_metrics["leaf_count"],
        "surface_count": absorption_metrics["surface_count"],
        "receiver_sample_count": receiver_sample_count,
        "receiver_granularity": receiver_granularity,
        "receiver_samples_per_leaf": receiver_samples_per_leaf,
        "receiver_generation_basis": receiver_generation_basis,
        "receiver_represented_area_m2": receiver_represented_area_m2,
        "receiver_sample_area_sum_m2": receiver_sample_area_sum_m2,
        "receiver_area_basis": receiver_area_basis,
        "receiver_side_policy": receiver_side_policy,
        "receiver_rows_per_mesh_surface_row": receiver_rows_per_mesh_surface_row,
        "normal_generation_basis": normal_generation_basis,
        "receiver_granularity_role": receiver_granularity_role_value,
        **material_metadata,
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
        **{key: absorption_metrics[key] for key in target_keys if key in absorption_metrics},
        "plant_summaries": plant_summaries,
        "leaf_summaries": leaf_summaries,
        "surface_summaries": surface_summaries,
        "visualization": visualization,
        "outputs_do_not_predict": list(NO_CROP_OUTPUT_TERMS),
        "warnings": warnings,
        "limitations": limitations,
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
    target_ppfd_umol_m2_s: float | None = None,
    target_tolerance_umol_m2_s: float | None = None,
) -> Path:
    rows = build_baseline_proxy_surface_flux_rows(scene, baseline_ppfd_mean_umol_m2_s)
    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=BASELINE_PPFD_PROXY_METHOD,
        source_ppfd_map=source_ppfd_map,
        baseline_ppfd_mean_umol_m2_s=baseline_ppfd_mean_umol_m2_s,
        target_ppfd_umol_m2_s=target_ppfd_umol_m2_s,
        target_tolerance_umol_m2_s=target_tolerance_umol_m2_s,
    )
    return write_plant_surface_flux_artifact(target_dir, payload)


def write_spatial_proxy_plant_surface_flux_artifact(
    target_dir: str | Path,
    scene: PlantScene,
    *,
    ppfd_map_path: str | Path,
    source_ppfd_map: str = "ppfd_map.txt",
    target_ppfd_umol_m2_s: float | None = None,
    target_tolerance_umol_m2_s: float | None = None,
) -> Path:
    field = read_ppfd_map_field(ppfd_map_path)
    rows = build_spatial_proxy_surface_flux_rows(scene, ppfd_map_path)
    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=SPATIAL_PPFD_PROXY_METHOD,
        source_ppfd_map=source_ppfd_map,
        baseline_ppfd_mean_umol_m2_s=field.mean_umol_m2_s,
        ppfd_field_summary=field.summary(),
        target_ppfd_umol_m2_s=target_ppfd_umol_m2_s,
        target_tolerance_umol_m2_s=target_tolerance_umol_m2_s,
        target_classification_ppfd_map_path=ppfd_map_path,
    )
    return write_plant_surface_flux_artifact(target_dir, payload)
