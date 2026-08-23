"""Deterministic plant receiver sample generation and rtrace input formatting."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Literal, Mapping, cast

from fspm_optics.plants.absorption import (
    LeafAbsorptionSurface,
    leaf_absorption_surfaces,
)
from fspm_optics.plants.models import PlantMesh, PlantScene, Vector3

FSPM_RECEIVER_GRANULARITY_ENV = "FSPM_RECEIVER_GRANULARITY"
RECEIVER_GRANULARITY_LEAF_CENTROID = "leaf_centroid"
RECEIVER_GRANULARITY_LEAF_QUADRATURE_4 = "leaf_quadrature_4"
RECEIVER_GRANULARITY_MESH_PATCH = "mesh_patch"
DEFAULT_FSPM_RECEIVER_GRANULARITY = RECEIVER_GRANULARITY_LEAF_QUADRATURE_4
FSPM_RECEIVER_GRANULARITIES = frozenset(
    {
        RECEIVER_GRANULARITY_LEAF_CENTROID,
        RECEIVER_GRANULARITY_LEAF_QUADRATURE_4,
        RECEIVER_GRANULARITY_MESH_PATCH,
    }
)


@dataclass(frozen=True, slots=True)
class MeshPatchReceiverSample:
    """One stable front/back receiver derived from a scientific leaf patch."""

    receiver_id: str
    plant_id: str
    leaf_id: str
    leaf_rank: int
    leaf_layer: str
    patch_id: str
    face_id: str
    side: Literal["front", "back"]
    point_m: Vector3
    normal: Vector3
    area_m2: float
    normal_offset_m: float
    sampling_profile_id: str | None = None
    surface_anchor_m: Vector3 | None = None
    carrier_face_id: str | None = None
    carrier_barycentric: tuple[float, float, float] | None = None
    normal_generation_basis_id: str = "area_weighted_patch_normal_legacy"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "receiver_id": self.receiver_id,
            "sample_id": self.receiver_id,
            "surface_id": self.face_id,
            "face_id": self.face_id,
            "patch_id": self.patch_id,
            "plant_id": self.plant_id,
            "leaf_id": self.leaf_id,
            "leaf_rank": self.leaf_rank,
            "leaf_layer": self.leaf_layer,
            "side": self.side,
            "point_m": [float(value) for value in self.point_m],
            "origin_m": [float(value) for value in self.point_m],
            "normal": [float(value) for value in self.normal],
            "direction": [float(value) for value in self.normal],
            "area_m2": self.area_m2,
            "normal_offset_m": self.normal_offset_m,
            "receiver_granularity": RECEIVER_GRANULARITY_MESH_PATCH,
            "receiver_generation_basis": (
                "surface_anchor_plus_or_minus_epsilon_along_carrier_triangle_normal"
                if self.surface_anchor_m is not None
                else receiver_generation_basis(RECEIVER_GRANULARITY_MESH_PATCH)
            ),
            "receiver_area_basis": receiver_area_basis(
                RECEIVER_GRANULARITY_MESH_PATCH
            ),
            "receiver_side_policy": receiver_side_policy(
                RECEIVER_GRANULARITY_MESH_PATCH
            ),
            "normal_generation_basis": self.normal_generation_basis_id,
            "leaf_representative_sample_count": 2,
        }
        if self.sampling_profile_id is not None:
            payload["sampling_profile_id"] = self.sampling_profile_id
        if self.surface_anchor_m is not None:
            payload.update(
                {
                    "surface_anchor_m": list(self.surface_anchor_m),
                    "carrier_face_id": self.carrier_face_id,
                    "carrier_barycentric": list(self.carrier_barycentric or ()),
                }
            )
        return payload


def build_two_sided_patch_receivers(
    plant: PlantMesh,
    *,
    normal_offset_m: float = 5e-5,
) -> tuple[MeshPatchReceiverSample, ...]:
    """Build one front/back pair from every canonical receiver patch."""

    if (
        isinstance(normal_offset_m, bool)
        or not isinstance(normal_offset_m, int | float)
        or not math.isfinite(float(normal_offset_m))
        or float(normal_offset_m) < 0.0
    ):
        raise ValueError("normal_offset_m must be finite and non-negative.")
    offset = float(normal_offset_m)
    samples: list[MeshPatchReceiverSample] = []
    sampling_profile_id = getattr(plant.config, "sampling_profile_id", None)
    for patch in plant.patches:
        carrier_values = (
            patch.surface_anchor,
            patch.carrier_face_id,
            patch.carrier_barycentric,
            patch.carrier_unit_normal,
        )
        if any(value is not None for value in carrier_values) and not all(
            value is not None for value in carrier_values
        ):
            raise ValueError("Receiver carrier geometry must be complete or absent.")
        surface_anchor = patch.surface_anchor or patch.centroid
        front_normal = patch.carrier_unit_normal or patch.unit_normal
        back_normal = (
            -front_normal[0],
            -front_normal[1],
            -front_normal[2],
        )
        for side, normal in (("front", front_normal), ("back", back_normal)):
            direction = 1.0 if side == "front" else -1.0
            point = (
                surface_anchor[0] + direction * front_normal[0] * offset,
                surface_anchor[1] + direction * front_normal[1] * offset,
                surface_anchor[2] + direction * front_normal[2] * offset,
            )
            samples.append(
                MeshPatchReceiverSample(
                    receiver_id=f"{patch.patch_id}_{side}",
                    plant_id=patch.plant_id,
                    leaf_id=patch.leaf_id,
                    leaf_rank=patch.leaf_rank,
                    leaf_layer=patch.leaf_layer,
                    patch_id=patch.patch_id,
                    face_id=patch.face_id,
                    side=cast(Literal["front", "back"], side),
                    point_m=point,
                    normal=normal,
                    area_m2=patch.area_m2,
                    normal_offset_m=offset,
                    sampling_profile_id=sampling_profile_id,
                    surface_anchor_m=patch.surface_anchor,
                    carrier_face_id=patch.carrier_face_id,
                    carrier_barycentric=patch.carrier_barycentric,
                    normal_generation_basis_id=patch.receiver_normal_basis,
                )
            )
    return tuple(samples)


def normalize_receiver_granularity(value: object) -> str:
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
    return "scientific_mesh_reference"


def receiver_area_basis(granularity: str) -> str:
    if normalize_receiver_granularity(granularity) == RECEIVER_GRANULARITY_MESH_PATCH:
        return "one_sided_leaf_mesh_area_front_back_receiver_samples_summed"
    return "one_sided_leaf_mesh_area_representative_sample_weights"


def receiver_side_policy(granularity: str) -> str:
    if normalize_receiver_granularity(granularity) == RECEIVER_GRANULARITY_MESH_PATCH:
        return "front_and_back_per_mesh_surface_row"
    return "single_light_facing_side"


def normal_generation_basis(granularity: str) -> str:
    normalized = normalize_receiver_granularity(granularity)
    if normalized == RECEIVER_GRANULARITY_MESH_PATCH:
        return "sampling_profile_declared_patch_or_carrier_normal_with_backside_sample"
    if normalized == RECEIVER_GRANULARITY_LEAF_CENTROID:
        return "nearest_mesh_patch_to_leaf_area_centroid_oriented_upward"
    return "nearest_mesh_patch_to_area_partition_centroid_oriented_upward"


def finite_non_negative(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number.")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be finite and non-negative.")
    return number


def _vector_sub(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vector3, b: Vector3) -> Vector3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _triangle_geometry(
    a: Vector3, b: Vector3, c: Vector3, *, surface_id: str
) -> tuple[Vector3, Vector3, float]:
    cross = _cross(_vector_sub(b, a), _vector_sub(c, a))
    magnitude = math.sqrt(sum(component * component for component in cross))
    if not math.isfinite(magnitude) or magnitude <= 0.0:
        raise ValueError(f"Surface {surface_id} has degenerate geometry.")
    normal = tuple(component / magnitude for component in cross)
    centroid = tuple((a[index] + b[index] + c[index]) / 3.0 for index in range(3))
    return cast(Vector3, centroid), cast(Vector3, normal), magnitude / 2.0


def surface_geometry_by_id(scene: PlantScene) -> dict[str, dict[str, Any]]:
    registry = {
        surface.surface_id: surface for surface in leaf_absorption_surfaces(scene)
    }
    geometry: dict[str, dict[str, Any]] = {}
    for plant in scene.plants:
        for leaf in plant.leaves:
            for face_index, face in enumerate(leaf.mesh.faces):
                surface_id = f"{leaf.leaf_id}_face_{face_index:04d}"
                vertices = leaf.mesh.vertices
                centroid, normal, _area = _triangle_geometry(
                    vertices[face[0]],
                    vertices[face[1]],
                    vertices[face[2]],
                    surface_id=surface_id,
                )
                geometry[surface_id] = {
                    "surface": registry[surface_id],
                    "centroid_m": centroid,
                    "normal": normal,
                }
    return geometry


def leaf_surface_geometry(scene: PlantScene) -> dict[str, list[dict[str, Any]]]:
    by_leaf: dict[str, list[dict[str, Any]]] = {}
    for surface_id, item in surface_geometry_by_id(scene).items():
        surface = cast(LeafAbsorptionSurface, item["surface"])
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


def _distance_squared(a: Vector3, b: Vector3) -> float:
    return sum((a[index] - b[index]) ** 2 for index in range(3))


def _aggregate_receiver_geometry(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    item_list = list(items)
    total_area = sum(float(item["area_m2"]) for item in item_list)
    if total_area <= 0.0:
        raise ValueError("Receiver representative area must be positive.")
    centroid = tuple(
        sum(float(item["centroid_m"][index]) * float(item["area_m2"]) for item in item_list)
        / total_area
        for index in range(3)
    )
    representative = min(
        item_list,
        key=lambda item: _distance_squared(cast(Vector3, item["centroid_m"]), cast(Vector3, centroid)),
    )
    normal = cast(Vector3, representative["normal"])
    if normal[2] < 0.0:
        normal = (-normal[0], -normal[1], -normal[2])
    return {
        "area_m2": total_area,
        "centroid_m": representative["centroid_m"],
        "normal": normal,
    }


def _partition_surface_items_by_area(
    items: Iterable[dict[str, Any]], partition_count: int
) -> list[list[dict[str, Any]]]:
    ordered = sorted(items, key=lambda item: int(item["face_index"]))
    if partition_count <= 0:
        raise ValueError("partition_count must be positive.")
    if len(ordered) <= partition_count:
        return [[item] for item in ordered]
    target_area = sum(float(item["area_m2"]) for item in ordered) / partition_count
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


def _sample(
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
    origin = tuple(centroid[index] + direction[index] * offset_m for index in range(3))
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


def build_radiance_receiver_samples(
    scene: PlantScene | PlantMesh,
    *,
    receiver_granularity: str | None = None,
    two_sided: bool | None = None,
    offset_m: float = 5e-5,
) -> list[dict[str, Any]]:
    """Create receiver rows only; this function never executes Radiance."""

    if isinstance(scene, PlantMesh):
        granularity = normalize_receiver_granularity(
            RECEIVER_GRANULARITY_MESH_PATCH
            if receiver_granularity is None
            else receiver_granularity
        )
        if granularity != RECEIVER_GRANULARITY_MESH_PATCH:
            raise ValueError(
                "Scientific PlantMesh receivers require mesh_patch granularity."
            )
        if two_sided is False:
            raise ValueError(
                "Scientific PlantMesh receivers require front and back samples."
            )
        return [
            sample.to_dict()
            for sample in build_two_sided_patch_receivers(
                scene,
                normal_offset_m=offset_m,
            )
        ]

    granularity = normalize_receiver_granularity(receiver_granularity)
    if not math.isfinite(offset_m) or offset_m < 0.0:
        raise ValueError("offset_m must be finite and non-negative.")
    if granularity == RECEIVER_GRANULARITY_MESH_PATCH:
        samples: list[dict[str, Any]] = []
        for surface_id, item in sorted(surface_geometry_by_id(scene).items()):
            surface = cast(LeafAbsorptionSurface, item["surface"])
            normal = cast(Vector3, item["normal"])
            directions = [("front", normal)]
            if two_sided is None or two_sided:
                directions.append(("back", (-normal[0], -normal[1], -normal[2])))
            for side, direction in directions:
                samples.append(
                    _sample(
                        sample_id=f"{surface_id}_{side}",
                        surface_id=surface_id,
                        plant_id=surface.plant_id,
                        leaf_id=surface.leaf_id,
                        leaf_index=surface.leaf_index,
                        face_index=surface.face_index,
                        side=side,
                        centroid=cast(Vector3, item["centroid_m"]),
                        direction=direction,
                        area_m2=surface.area_m2,
                        offset_m=offset_m,
                        granularity=granularity,
                        representative_sample_count=len(directions),
                    )
                )
        return samples
    if two_sided:
        raise ValueError("two_sided is supported only for mesh_patch receivers.")

    samples = []
    for leaf_id, items in sorted(leaf_surface_geometry(scene).items()):
        buckets = (
            [items]
            if granularity == RECEIVER_GRANULARITY_LEAF_CENTROID
            else _partition_surface_items_by_area([dict(item) for item in items], 4)
        )
        first = items[0]
        for bucket_index, bucket in enumerate(buckets):
            representative = _aggregate_receiver_geometry(bucket)
            suffix = "centroid" if len(buckets) == 1 else f"quadrature_{bucket_index + 1}"
            sample = _sample(
                sample_id=f"{leaf_id}_{suffix}",
                surface_id=f"{leaf_id}_{suffix}",
                plant_id=str(first["plant_id"]),
                leaf_id=leaf_id,
                leaf_index=int(first["leaf_index"]),
                face_index=None,
                side="front",
                centroid=cast(Vector3, representative["centroid_m"]),
                direction=cast(Vector3, representative["normal"]),
                area_m2=float(representative["area_m2"]),
                offset_m=offset_m,
                granularity=granularity,
                representative_sample_count=len(buckets),
            )
            sample["mapped_surface_ids"] = [str(item["surface_id"]) for item in bucket]
            sample["mapped_face_indices"] = [int(item["face_index"]) for item in bucket]
            if len(buckets) > 1:
                sample["quadrature_index"] = bucket_index
            samples.append(sample)
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
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            for value in values
        ):
            raise ValueError("Receiver sample input contains a non-finite value.")
        lines.append(
            f"{float(origin[0]):.6f} {float(origin[1]):.6f} {float(origin[2]):.6f} "
            f"{float(direction[0]):.8f} {float(direction[1]):.8f} {float(direction[2]):.8f}\n"
        )
    return "".join(lines)
