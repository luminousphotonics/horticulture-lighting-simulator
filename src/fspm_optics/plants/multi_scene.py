"""Source-neutral juvenile Natural-fit scientific scene planning."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Iterator, Literal

from fspm_optics.geometry.coordinate_frame import ROOM_BOUNDS_TOLERANCE_M
from fspm_optics.plants.generator import generate_rex_juvenile_preheading_plant
from fspm_optics.plants.models import (
    PlantMesh,
    ScientificMeshFace,
    ScientificMeshPatch,
    TriangleFace,
    Vector3,
)
from fspm_optics.plants.natural_fit import (
    NATURAL_FIT_POLICY_ID,
    NaturalFitLayoutPlan,
)
from fspm_optics.plants.rex_juvenile import (
    REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID,
    REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID,
    REX_JUVENILE_PREHEADING_PROFILE_ID,
    REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS,
    RexJuvenilePreheadingConfig,
)
from fspm_optics.plants.surface_geometry import barycentric_point
from fspm_optics.receivers.samples import (
    MeshPatchReceiverSample,
    build_two_sided_patch_receivers,
)

JUVENILE_SCIENTIFIC_SCENE_SCHEMA_ID = (
    "fspm-optics.juvenile-natural-fit-scientific-scene"
)
JUVENILE_SCIENTIFIC_SCENE_SCHEMA_VERSION = 2
JUVENILE_SCIENTIFIC_LEGACY_SCENE_SCHEMA_VERSION = 1
JUVENILE_SCIENTIFIC_COORDINATE_SYSTEM = "right-handed, meters, z-up"
JUVENILE_PLACEMENT_CONVENTION = "world_xyz = local_xyz + plant_origin_xyz"
JUVENILE_SCENE_ORDERING = "plant-major; canonical order within each plant"
JUVENILE_REFERENCE_PLANE_Z_M = 0.0
NATURAL_FIT_LAYOUT_SCHEMA_ID = "fspm-optics.natural-fit-plant-layout"
_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class JuvenileScientificSceneError(ValueError):
    """Raised when a juvenile multi-plant scene contract is inconsistent."""


@dataclass(frozen=True, slots=True)
class CanonicalJuvenileTopology:
    """Identity and dynamic counts for the one shared canonical topology."""

    canonical_plant_id: str
    sampling_profile_id: str
    leaf_count: int
    face_count: int
    patch_count: int
    receiver_count: int
    receiver_normal_offset_m: float
    topology_sha256: str
    receivers_sha256: str

    def to_payload(
        self,
        *,
        include_sampling_profile: bool = True,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "canonical_plant_id": self.canonical_plant_id,
            "counts": {
                "leaves": self.leaf_count,
                "faces": self.face_count,
                "patches": self.patch_count,
                "receivers": self.receiver_count,
            },
            "receiver_normal_offset_m": self.receiver_normal_offset_m,
            "topology_sha256": self.topology_sha256,
            "receivers_sha256": self.receivers_sha256,
        }
        if include_sampling_profile:
            payload["sampling_profile_id"] = self.sampling_profile_id
        return payload


@dataclass(frozen=True, slots=True)
class JuvenileSceneAggregateCounts:
    plant_count: int
    leaf_count: int
    face_count: int
    patch_count: int
    receiver_count: int

    def to_payload(self) -> dict[str, int]:
        return {
            "plants": self.plant_count,
            "leaves": self.leaf_count,
            "faces": self.face_count,
            "patches": self.patch_count,
            "receivers": self.receiver_count,
        }


@dataclass(frozen=True, slots=True)
class JuvenileScenePlant:
    """One compact Phase 26E plant origin; no topology is duplicated here."""

    plant_index: int
    plant_id: str
    row_y: int
    column_x: int
    origin_m: Vector3

    def to_payload(self) -> dict[str, object]:
        return {
            "plant_index": self.plant_index,
            "plant_id": self.plant_id,
            "grid": {"row_y": self.row_y, "column_x": self.column_x},
            "origin_m": list(self.origin_m),
        }


@dataclass(frozen=True, slots=True)
class ExpandedJuvenileLeaf:
    plant_index: int
    plant_id: str
    local_leaf_index: int
    global_leaf_index: int
    leaf_id: str
    canonical_leaf_id: str
    leaf_rank: int
    leaf_layer: str
    origin_m: Vector3
    vertices: tuple[Vector3, ...]
    triangle_indices: tuple[TriangleFace, ...]


@dataclass(frozen=True, slots=True)
class ExpandedJuvenileFace:
    plant_index: int
    plant_id: str
    local_leaf_index: int
    global_leaf_index: int
    local_face_index: int
    global_face_index: int
    face_index_within_leaf: int
    face_id: str
    canonical_face_id: str
    canonical_leaf_id: str
    vertex_indices: TriangleFace
    vertices: tuple[Vector3, Vector3, Vector3]
    centroid: Vector3
    unit_normal: Vector3
    area_m2: float


@dataclass(frozen=True, slots=True)
class ExpandedJuvenilePatch:
    plant_index: int
    plant_id: str
    local_leaf_index: int
    global_leaf_index: int
    local_patch_index: int
    global_patch_index: int
    patch_index_within_leaf: int
    patch_id: str
    canonical_patch_id: str
    face_id: str
    canonical_face_id: str
    canonical_leaf_id: str
    triangle_face_ids: tuple[str, ...]
    canonical_triangle_face_ids: tuple[str, ...]
    centroid: Vector3
    unit_normal: Vector3
    area_m2: float
    surface_anchor: Vector3 | None
    carrier_face_id: str | None
    canonical_carrier_face_id: str | None
    carrier_barycentric: tuple[float, float, float] | None
    carrier_unit_normal: Vector3 | None
    receiver_normal_basis: str


@dataclass(frozen=True, slots=True)
class ExpandedJuvenileReceiver:
    plant_index: int
    plant_id: str
    local_leaf_index: int
    global_leaf_index: int
    local_patch_index: int
    global_patch_index: int
    local_receiver_index: int
    global_receiver_index: int
    receiver_id: str
    canonical_receiver_id: str
    canonical_patch_id: str
    face_id: str
    canonical_face_id: str
    canonical_leaf_id: str
    side: Literal["front", "back"]
    point_m: Vector3
    normal: Vector3
    area_m2: float
    normal_offset_m: float
    sampling_profile_id: str | None
    surface_anchor_m: Vector3 | None
    carrier_face_id: str | None
    canonical_carrier_face_id: str | None
    carrier_barycentric: tuple[float, float, float] | None
    normal_generation_basis_id: str


@dataclass(frozen=True, slots=True)
class JuvenileScientificScene:
    """Compact immutable scene plan with lazy scientific expansion."""

    schema_id: str
    schema_version: int
    profile_id: str
    sampling_profile_id: str
    layout_schema_id: str
    layout_policy_id: str
    layout_plan_hash: str
    topology: CanonicalJuvenileTopology
    counts: JuvenileSceneAggregateCounts
    plants: tuple[JuvenileScenePlant, ...]
    coordinate_system: str
    placement_convention: str
    reference_plane_z_m: float
    ordering: str
    canonical_plant: PlantMesh = field(repr=False, compare=False)
    canonical_receivers: tuple[MeshPatchReceiverSample, ...] = field(
        repr=False,
        compare=False,
    )
    scene_hash: str = field(init=False)
    scene_id: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            self.schema_id != JUVENILE_SCIENTIFIC_SCENE_SCHEMA_ID
            or self.schema_version
            not in {
                JUVENILE_SCIENTIFIC_LEGACY_SCENE_SCHEMA_VERSION,
                JUVENILE_SCIENTIFIC_SCENE_SCHEMA_VERSION,
            }
            or self.profile_id != REX_JUVENILE_PREHEADING_PROFILE_ID
            or self.sampling_profile_id not in REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS
            or (
                self.schema_version
                == JUVENILE_SCIENTIFIC_LEGACY_SCENE_SCHEMA_VERSION
                and self.sampling_profile_id
                != REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID
            )
            or (
                self.schema_version == JUVENILE_SCIENTIFIC_SCENE_SCHEMA_VERSION
                and self.sampling_profile_id
                != REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
            )
            or self.layout_schema_id != NATURAL_FIT_LAYOUT_SCHEMA_ID
            or self.layout_policy_id != NATURAL_FIT_POLICY_ID
            or self.coordinate_system != JUVENILE_SCIENTIFIC_COORDINATE_SYSTEM
            or self.placement_convention != JUVENILE_PLACEMENT_CONVENTION
            or self.reference_plane_z_m != JUVENILE_REFERENCE_PLANE_Z_M
            or self.ordering != JUVENILE_SCENE_ORDERING
        ):
            raise JuvenileScientificSceneError("scene identity contract is invalid.")
        _validate_scene_components(self)
        digest = _hash_payload(self.identity_payload())
        object.__setattr__(self, "scene_hash", digest)
        object.__setattr__(
            self,
            "scene_id",
            f"juvenile-natural-fit-scene-v{self.schema_version}-{digest}",
        )

    def identity_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "layout": {
                "schema_id": self.layout_schema_id,
                "policy_id": self.layout_policy_id,
                "plan_hash": self.layout_plan_hash,
            },
            "canonical_topology": self.topology.to_payload(
                include_sampling_profile=(
                    self.schema_version
                    != JUVENILE_SCIENTIFIC_LEGACY_SCENE_SCHEMA_VERSION
                )
            ),
            "aggregate_counts": self.counts.to_payload(),
            "coordinate_system": self.coordinate_system,
            "placement_convention": self.placement_convention,
            "reference_plane_z_m": self.reference_plane_z_m,
            "ordering": self.ordering,
            "plants": [plant.to_payload() for plant in self.plants],
        }
        if self.schema_version != JUVENILE_SCIENTIFIC_LEGACY_SCENE_SCHEMA_VERSION:
            payload["sampling_profile_id"] = self.sampling_profile_id
        return payload

    def to_payload(self) -> dict[str, object]:
        return self.identity_payload() | {
            "scene_hash": self.scene_hash,
            "scene_id": self.scene_id,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_payload(), indent=2, sort_keys=True, ensure_ascii=False
        ) + "\n"

    def iter_leaves(self) -> Iterator[ExpandedJuvenileLeaf]:
        """Yield translated leaves in plant-major/canonical-leaf order."""

        leaves_per_plant = self.topology.leaf_count
        for plant in self.plants:
            for local_leaf_index, leaf in enumerate(self.canonical_plant.leaves):
                yield ExpandedJuvenileLeaf(
                    plant_index=plant.plant_index,
                    plant_id=plant.plant_id,
                    local_leaf_index=local_leaf_index,
                    global_leaf_index=(
                        plant.plant_index * leaves_per_plant + local_leaf_index
                    ),
                    leaf_id=_composite_id(plant.plant_id, leaf.leaf_id),
                    canonical_leaf_id=leaf.leaf_id,
                    leaf_rank=leaf.leaf_rank,
                    leaf_layer=leaf.leaf_layer,
                    origin_m=_translate(leaf.world_transform.origin_m, plant.origin_m),
                    vertices=tuple(
                        _translate(vertex, plant.origin_m)
                        for vertex in leaf.vertices
                    ),
                    triangle_indices=leaf.triangle_indices,
                )

    def iter_faces(self) -> Iterator[ExpandedJuvenileFace]:
        """Yield translated triangles without materializing the complete scene."""

        faces = self.canonical_plant.faces
        leaves_per_plant = self.topology.leaf_count
        faces_per_plant = self.topology.face_count
        for plant in self.plants:
            for local_face_index, face in enumerate(faces):
                yield _expanded_face(
                    plant,
                    face,
                    local_face_index=local_face_index,
                    leaves_per_plant=leaves_per_plant,
                    faces_per_plant=faces_per_plant,
                )

    def face_at(self, global_face_index: int) -> ExpandedJuvenileFace:
        """Expand one face directly without walking preceding scene geometry."""

        plant_index, local_face_index = _split_global_index(
            global_face_index,
            per_plant=self.topology.face_count,
            total=self.counts.face_count,
            label="face",
        )
        return _expanded_face(
            self.plants[plant_index],
            self.canonical_plant.faces[local_face_index],
            local_face_index=local_face_index,
            leaves_per_plant=self.topology.leaf_count,
            faces_per_plant=self.topology.face_count,
        )

    def iter_patches(self) -> Iterator[ExpandedJuvenilePatch]:
        """Yield translated physical patches in plant-major order."""

        patches = self.canonical_plant.patches
        leaves_per_plant = self.topology.leaf_count
        patches_per_plant = self.topology.patch_count
        for plant in self.plants:
            for local_patch_index, patch in enumerate(patches):
                local_leaf_index = patch.leaf_rank - 1
                yield ExpandedJuvenilePatch(
                    plant_index=plant.plant_index,
                    plant_id=plant.plant_id,
                    local_leaf_index=local_leaf_index,
                    global_leaf_index=(
                        plant.plant_index * leaves_per_plant + local_leaf_index
                    ),
                    local_patch_index=local_patch_index,
                    global_patch_index=(
                        plant.plant_index * patches_per_plant + local_patch_index
                    ),
                    patch_index_within_leaf=patch.patch_index,
                    patch_id=_composite_id(plant.plant_id, patch.patch_id),
                    canonical_patch_id=patch.patch_id,
                    face_id=_composite_id(plant.plant_id, patch.face_id),
                    canonical_face_id=patch.face_id,
                    canonical_leaf_id=patch.leaf_id,
                    triangle_face_ids=tuple(
                        _composite_id(plant.plant_id, face_id)
                        for face_id in patch.triangle_face_ids
                    ),
                    canonical_triangle_face_ids=patch.triangle_face_ids,
                    centroid=_translate(patch.centroid, plant.origin_m),
                    unit_normal=patch.unit_normal,
                    area_m2=patch.area_m2,
                    surface_anchor=(
                        _translate(patch.surface_anchor, plant.origin_m)
                        if patch.surface_anchor is not None
                        else None
                    ),
                    carrier_face_id=(
                        _composite_id(plant.plant_id, patch.carrier_face_id)
                        if patch.carrier_face_id is not None
                        else None
                    ),
                    canonical_carrier_face_id=patch.carrier_face_id,
                    carrier_barycentric=patch.carrier_barycentric,
                    carrier_unit_normal=patch.carrier_unit_normal,
                    receiver_normal_basis=patch.receiver_normal_basis,
                )

    def iter_receivers(self) -> Iterator[ExpandedJuvenileReceiver]:
        """Yield translated front/back receivers in plant-major order."""

        leaves_per_plant = self.topology.leaf_count
        patches_per_plant = self.topology.patch_count
        receivers_per_plant = self.topology.receiver_count
        for plant in self.plants:
            for local_receiver_index, receiver in enumerate(
                self.canonical_receivers
            ):
                yield _expanded_receiver(
                    plant,
                    receiver,
                    local_receiver_index=local_receiver_index,
                    leaves_per_plant=leaves_per_plant,
                    patches_per_plant=patches_per_plant,
                    receivers_per_plant=receivers_per_plant,
                )

    def receiver_at(
        self, global_receiver_index: int
    ) -> ExpandedJuvenileReceiver:
        """Expand one receiver directly without walking preceding records."""

        plant_index, local_receiver_index = _split_global_index(
            global_receiver_index,
            per_plant=self.topology.receiver_count,
            total=self.counts.receiver_count,
            label="receiver",
        )
        return _expanded_receiver(
            self.plants[plant_index],
            self.canonical_receivers[local_receiver_index],
            local_receiver_index=local_receiver_index,
            leaves_per_plant=self.topology.leaf_count,
            patches_per_plant=self.topology.patch_count,
            receivers_per_plant=self.topology.receiver_count,
        )


def build_juvenile_natural_fit_scene(
    layout: NaturalFitLayoutPlan,
    *,
    canonical_plant: PlantMesh | None = None,
    sampling_profile_id: str | None = None,
) -> JuvenileScientificScene:
    """Build a compact source-neutral scene from an accepted Phase 26E plan."""

    if not isinstance(layout, NaturalFitLayoutPlan):
        raise JuvenileScientificSceneError(
            "layout must be an immutable NaturalFitLayoutPlan."
        )
    if layout.profile_id != REX_JUVENILE_PREHEADING_PROFILE_ID:
        raise JuvenileScientificSceneError(
            "layout profile does not match the accepted juvenile profile."
        )
    if sampling_profile_id is not None and (
        sampling_profile_id not in REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS
    ):
        raise JuvenileScientificSceneError("sampling profile is not declared.")
    plant = canonical_plant
    if plant is None:
        plant = generate_rex_juvenile_preheading_plant(
            RexJuvenilePreheadingConfig(
                sampling_profile_id=(
                    sampling_profile_id
                    or REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
                )
            )
        )
    _validate_canonical_plant(plant)
    if (
        sampling_profile_id is not None
        and plant.config.sampling_profile_id != sampling_profile_id
    ):
        raise JuvenileScientificSceneError(
            "canonical plant and requested sampling profiles disagree."
        )
    receivers = build_two_sided_patch_receivers(plant)
    _validate_canonical_receivers(plant, receivers)
    topology = CanonicalJuvenileTopology(
        canonical_plant_id=plant.plant_id,
        sampling_profile_id=plant.config.sampling_profile_id,
        leaf_count=len(plant.leaves),
        face_count=plant.face_count,
        patch_count=plant.patch_count,
        receiver_count=len(receivers),
        receiver_normal_offset_m=receivers[0].normal_offset_m,
        topology_sha256=_canonical_topology_hash(plant),
        receivers_sha256=_canonical_receivers_hash(receivers),
    )
    plants = tuple(
        JuvenileScenePlant(
            plant_index=plant_index,
            plant_id=placement.plant_id,
            row_y=placement.grid_index.row_y,
            column_x=placement.grid_index.column_x,
            origin_m=(
                placement.aligned_x_m,
                placement.aligned_y_m,
                JUVENILE_REFERENCE_PLANE_Z_M,
            ),
        )
        for plant_index, placement in enumerate(layout.plants)
    )
    counts = JuvenileSceneAggregateCounts(
        plant_count=len(plants),
        leaf_count=len(plants) * topology.leaf_count,
        face_count=len(plants) * topology.face_count,
        patch_count=len(plants) * topology.patch_count,
        receiver_count=len(plants) * topology.receiver_count,
    )
    layout_payload = layout.identity_payload()
    scene = JuvenileScientificScene(
        schema_id=JUVENILE_SCIENTIFIC_SCENE_SCHEMA_ID,
        schema_version=(
            JUVENILE_SCIENTIFIC_SCENE_SCHEMA_VERSION
            if plant.config.uses_surface_constrained_receivers
            else JUVENILE_SCIENTIFIC_LEGACY_SCENE_SCHEMA_VERSION
        ),
        profile_id=REX_JUVENILE_PREHEADING_PROFILE_ID,
        sampling_profile_id=plant.config.sampling_profile_id,
        layout_schema_id=str(layout_payload["schema_id"]),
        layout_policy_id=layout.policy.policy_id,
        layout_plan_hash=layout.plan_hash,
        topology=topology,
        counts=counts,
        plants=plants,
        coordinate_system=JUVENILE_SCIENTIFIC_COORDINATE_SYSTEM,
        placement_convention=JUVENILE_PLACEMENT_CONVENTION,
        reference_plane_z_m=JUVENILE_REFERENCE_PLANE_Z_M,
        ordering=JUVENILE_SCENE_ORDERING,
        canonical_plant=plant,
        canonical_receivers=receivers,
    )
    _validate_scene_within_room(scene, layout)
    return scene


def _validate_scene_within_room(
    scene: JuvenileScientificScene,
    layout: NaturalFitLayoutPlan,
) -> None:
    """Fail closed if any Stage B origin, vertex, or receiver leaves the room."""

    frame = layout.coordinate_frame
    local_vertices = tuple(
        vertex
        for leaf in scene.canonical_plant.leaves
        for vertex in leaf.vertices
    )
    local_receivers = scene.canonical_receivers
    local_points = local_vertices + tuple(
        receiver.point_m for receiver in local_receivers
    )
    local_min_x = min(point[0] for point in local_points)
    local_max_x = max(point[0] for point in local_points)
    local_min_y = min(point[1] for point in local_points)
    local_max_y = max(point[1] for point in local_points)
    for receiver in local_receivers:
        magnitude = math.sqrt(sum(value * value for value in receiver.normal))
        if (
            not math.isfinite(magnitude)
            or not math.isclose(magnitude, 1.0, rel_tol=0.0, abs_tol=1.0e-12)
        ):
            raise JuvenileScientificSceneError(
                "receiver normal violates the aligned room contract: "
                f"{receiver.receiver_id}"
            )
    for plant in scene.plants:
        corners = (
            (plant.origin_m[0] + local_min_x, plant.origin_m[1] + local_min_y, 0.0),
            (plant.origin_m[0] + local_min_x, plant.origin_m[1] + local_max_y, 0.0),
            (plant.origin_m[0] + local_max_x, plant.origin_m[1] + local_min_y, 0.0),
            (plant.origin_m[0] + local_max_x, plant.origin_m[1] + local_max_y, 0.0),
        )
        if not frame.contains_simulation_position(
            plant.origin_m,
            tolerance_m=ROOM_BOUNDS_TOLERANCE_M,
        ) or any(
            not frame.contains_simulation_position(
                corner,
                tolerance_m=ROOM_BOUNDS_TOLERANCE_M,
            )
            for corner in corners
        ):
            raise JuvenileScientificSceneError(
                "plant origin, geometry, or receiver lies outside the aligned room: "
                f"{plant.plant_id}"
            )


def _validate_scene_components(scene: JuvenileScientificScene) -> None:
    _validate_canonical_plant(scene.canonical_plant)
    _validate_canonical_receivers(
        scene.canonical_plant, scene.canonical_receivers
    )
    topology = scene.topology
    if (
        topology.canonical_plant_id != scene.canonical_plant.plant_id
        or topology.sampling_profile_id != scene.sampling_profile_id
        or scene.sampling_profile_id
        != scene.canonical_plant.config.sampling_profile_id
        or topology.leaf_count != len(scene.canonical_plant.leaves)
        or topology.face_count != scene.canonical_plant.face_count
        or topology.patch_count != scene.canonical_plant.patch_count
        or topology.receiver_count != len(scene.canonical_receivers)
        or topology.receiver_normal_offset_m
        != scene.canonical_receivers[0].normal_offset_m
        or topology.topology_sha256
        != _canonical_topology_hash(scene.canonical_plant)
        or topology.receivers_sha256
        != _canonical_receivers_hash(scene.canonical_receivers)
    ):
        raise JuvenileScientificSceneError(
            "canonical topology identity or counts are inconsistent."
        )
    expected_counts = JuvenileSceneAggregateCounts(
        plant_count=len(scene.plants),
        leaf_count=len(scene.plants) * topology.leaf_count,
        face_count=len(scene.plants) * topology.face_count,
        patch_count=len(scene.plants) * topology.patch_count,
        receiver_count=len(scene.plants) * topology.receiver_count,
    )
    if scene.counts != expected_counts:
        raise JuvenileScientificSceneError("aggregate scene counts are inconsistent.")
    plant_ids = tuple(plant.plant_id for plant in scene.plants)
    if len(plant_ids) != len(set(plant_ids)):
        raise JuvenileScientificSceneError("scene plant IDs must be unique.")
    grid_indices = tuple(
        (plant.row_y, plant.column_x) for plant in scene.plants
    )
    if (
        len(grid_indices) != len(set(grid_indices))
        or grid_indices != tuple(sorted(grid_indices))
    ):
        raise JuvenileScientificSceneError(
            "scene plants must preserve Y-major/X-minor grid order."
        )
    for expected_index, plant in enumerate(scene.plants):
        if (
            plant.plant_index != expected_index
            or not _SAFE_ID.fullmatch(plant.plant_id)
            or not all(math.isfinite(value) for value in plant.origin_m)
            or plant.origin_m[2] != JUVENILE_REFERENCE_PLANE_Z_M
        ):
            raise JuvenileScientificSceneError(
                "scene plant ordering, identity, or origin is invalid."
            )
    if not _valid_sha256(scene.layout_plan_hash):
        raise JuvenileScientificSceneError("layout plan hash is malformed.")


def _validate_canonical_plant(plant: PlantMesh) -> None:
    if not isinstance(plant, PlantMesh):
        raise JuvenileScientificSceneError("canonical plant must be PlantMesh.")
    if (
        not isinstance(plant.config, RexJuvenilePreheadingConfig)
        or plant.config.profile_id != REX_JUVENILE_PREHEADING_PROFILE_ID
        or plant.plant_id != plant.config.plant_id
        or len(plant.leaves) != plant.config.leaf_count
    ):
        raise JuvenileScientificSceneError(
            "canonical plant is not the accepted juvenile profile."
        )
    leaves = plant.leaves
    faces = plant.faces
    patches = plant.patches
    if not faces or not patches:
        raise JuvenileScientificSceneError(
            "canonical juvenile topology must contain faces and patches."
        )
    for values, label in (
        ((leaf.leaf_id for leaf in leaves), "leaf"),
        ((face.face_id for face in faces), "face"),
        ((patch.patch_id for patch in patches), "patch"),
    ):
        identities = tuple(values)
        if (
            len(identities) != len(set(identities))
            or any(not _SAFE_ID.fullmatch(value) for value in identities)
        ):
            raise JuvenileScientificSceneError(
                f"canonical {label} identities are invalid."
            )
    if tuple(leaf.leaf_rank for leaf in leaves) != tuple(
        range(1, len(leaves) + 1)
    ):
        raise JuvenileScientificSceneError("canonical leaf ordering is invalid.")
    leaves_by_rank = {leaf.leaf_rank: leaf for leaf in leaves}
    face_ids_by_leaf = {
        leaf.leaf_id: {face.face_id for face in leaf.faces}
        for leaf in leaves
    }
    faces_by_id = {face.face_id: face for face in faces}
    optimized = (
        plant.config.sampling_profile_id
        == REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
    )
    for face in faces:
        leaf = leaves_by_rank.get(face.leaf_rank)
        if (
            leaf is None
            or face.plant_id != plant.plant_id
            or face.leaf_id != leaf.leaf_id
            or not _finite_vectors(face.vertices)
            or not _finite_vector(face.centroid)
            or not _finite_vector(face.unit_normal)
            or not math.isfinite(face.area_m2)
            or face.area_m2 <= 0.0
        ):
            raise JuvenileScientificSceneError("canonical face geometry is invalid.")
    for patch in patches:
        leaf = leaves_by_rank.get(patch.leaf_rank)
        carrier_values = (
            patch.surface_anchor,
            patch.carrier_face_id,
            patch.carrier_barycentric,
            patch.carrier_unit_normal,
        )
        if (
            leaf is None
            or patch.plant_id != plant.plant_id
            or patch.leaf_id != leaf.leaf_id
            or not patch.triangle_face_ids
            or any(
                face_id not in face_ids_by_leaf[patch.leaf_id]
                for face_id in patch.triangle_face_ids
            )
            or not _finite_vector(patch.centroid)
            or not _finite_vector(patch.unit_normal)
            or not math.isfinite(patch.area_m2)
            or patch.area_m2 <= 0.0
            or (optimized and not all(value is not None for value in carrier_values))
            or (not optimized and any(value is not None for value in carrier_values))
            or patch.receiver_normal_basis
            != (
                "carrier_triangle_winding_defined_unit_normal"
                if optimized
                else "area_weighted_patch_normal_legacy"
            )
        ):
            raise JuvenileScientificSceneError("canonical patch geometry is invalid.")
        if optimized:
            carrier_face = faces_by_id.get(str(patch.carrier_face_id))
            if (
                carrier_face is None
                or carrier_face.face_id not in patch.triangle_face_ids
                or patch.carrier_unit_normal != carrier_face.unit_normal
                or patch.surface_anchor is None
                or patch.carrier_barycentric is None
                or not _finite_vector(patch.surface_anchor)
            ):
                raise JuvenileScientificSceneError(
                    "canonical patch carrier identity is invalid."
                )
            reconstructed = barycentric_point(
                carrier_face.vertices,
                patch.carrier_barycentric,
            )
            if _squared_distance(reconstructed, patch.surface_anchor) > 1e-24:
                raise JuvenileScientificSceneError(
                    "canonical patch carrier anchor is off its triangle."
                )
    assigned_face_ids = tuple(
        face_id for patch in patches for face_id in patch.triangle_face_ids
    )
    canonical_face_ids = tuple(face.face_id for face in faces)
    if (
        len(assigned_face_ids) != len(set(assigned_face_ids))
        or set(assigned_face_ids) != set(canonical_face_ids)
    ):
        raise JuvenileScientificSceneError(
            "canonical patch-to-face assignment is inconsistent."
        )


def _validate_canonical_receivers(
    plant: PlantMesh,
    receivers: tuple[MeshPatchReceiverSample, ...],
) -> None:
    patches = plant.patches
    if len(receivers) != 2 * len(patches):
        raise JuvenileScientificSceneError("canonical receiver count is invalid.")
    receiver_ids = tuple(receiver.receiver_id for receiver in receivers)
    if (
        len(receiver_ids) != len(set(receiver_ids))
        or any(not _SAFE_ID.fullmatch(value) for value in receiver_ids)
    ):
        raise JuvenileScientificSceneError("canonical receiver IDs must be unique.")
    for local_receiver_index, receiver in enumerate(receivers):
        patch = patches[local_receiver_index // 2]
        expected_side = "front" if local_receiver_index % 2 == 0 else "back"
        base_normal = patch.carrier_unit_normal or patch.unit_normal
        expected_normal = (
            base_normal
            if expected_side == "front"
            else tuple(-value for value in base_normal)
        )
        direction = 1.0 if expected_side == "front" else -1.0
        anchor = patch.surface_anchor or patch.centroid
        expected_point = (
            anchor[0] + direction * base_normal[0] * receiver.normal_offset_m,
            anchor[1] + direction * base_normal[1] * receiver.normal_offset_m,
            anchor[2] + direction * base_normal[2] * receiver.normal_offset_m,
        )
        signed_distance = _dot(_subtract(receiver.point_m, anchor), base_normal)
        direction_alignment = _dot(receiver.normal, base_normal)
        if (
            receiver.side != expected_side
            or receiver.plant_id != plant.plant_id
            or receiver.patch_id != patch.patch_id
            or receiver.face_id != patch.face_id
            or receiver.leaf_id != patch.leaf_id
            or receiver.leaf_rank != patch.leaf_rank
            or receiver.leaf_layer != patch.leaf_layer
            or receiver.normal != expected_normal
            or receiver.point_m != expected_point
            or receiver.sampling_profile_id != plant.config.sampling_profile_id
            or receiver.surface_anchor_m != patch.surface_anchor
            or receiver.carrier_face_id != patch.carrier_face_id
            or receiver.carrier_barycentric != patch.carrier_barycentric
            or receiver.normal_generation_basis_id != patch.receiver_normal_basis
            or abs(signed_distance - direction * receiver.normal_offset_m) > 1e-12
            or abs(direction_alignment - direction) > 1e-12
            or not _finite_vector(receiver.point_m)
            or not _finite_vector(receiver.normal)
            or not math.isfinite(receiver.area_m2)
            or receiver.area_m2 <= 0.0
            or receiver.area_m2 != patch.area_m2
            or not math.isfinite(receiver.normal_offset_m)
            or receiver.normal_offset_m < 0.0
            or receiver.normal_offset_m != receivers[0].normal_offset_m
        ):
            raise JuvenileScientificSceneError(
                "canonical receiver ordering or geometry is invalid."
            )


def _canonical_topology_hash(plant: PlantMesh) -> str:
    payload = {
        "profile_id": plant.config.profile_id,
        "plant_id": plant.plant_id,
        "seed": plant.seed,
        "leaves": [
            {
                "leaf_id": leaf.leaf_id,
                "leaf_rank": leaf.leaf_rank,
                "leaf_layer": leaf.leaf_layer,
                "vertices": [list(vertex) for vertex in leaf.vertices],
                "triangle_indices": [list(face) for face in leaf.triangle_indices],
            }
            for leaf in plant.leaves
        ],
        "faces": [_face_identity_payload(face) for face in plant.faces],
        "patches": [_patch_identity_payload(patch) for patch in plant.patches],
    }
    return _hash_payload(payload)


def _canonical_receivers_hash(
    receivers: tuple[MeshPatchReceiverSample, ...],
) -> str:
    return _hash_payload(
        [
            {
                "receiver_id": receiver.receiver_id,
                "plant_id": receiver.plant_id,
                "leaf_id": receiver.leaf_id,
                "patch_id": receiver.patch_id,
                "side": receiver.side,
                "point_m": list(receiver.point_m),
                "normal": list(receiver.normal),
                "area_m2": receiver.area_m2,
                "normal_offset_m": receiver.normal_offset_m,
            }
            for receiver in receivers
        ]
    )


def _face_identity_payload(face: ScientificMeshFace) -> dict[str, object]:
    return {
        "face_id": face.face_id,
        "leaf_id": face.leaf_id,
        "leaf_rank": face.leaf_rank,
        "face_index": face.face_index,
        "vertex_indices": list(face.vertex_indices),
        "vertices": [list(vertex) for vertex in face.vertices],
        "centroid": list(face.centroid),
        "unit_normal": list(face.unit_normal),
        "area_m2": face.area_m2,
    }


def _patch_identity_payload(patch: ScientificMeshPatch) -> dict[str, object]:
    payload: dict[str, object] = {
        "patch_id": patch.patch_id,
        "leaf_id": patch.leaf_id,
        "leaf_rank": patch.leaf_rank,
        "patch_index": patch.patch_index,
        "patch_u_index": patch.patch_u_index,
        "patch_v_index": patch.patch_v_index,
        "triangle_face_ids": list(patch.triangle_face_ids),
        "centroid": list(patch.centroid),
        "unit_normal": list(patch.unit_normal),
        "area_m2": patch.area_m2,
    }
    if patch.surface_anchor is not None:
        payload.update(
            {
                "surface_anchor": list(patch.surface_anchor),
                "carrier_face_id": patch.carrier_face_id,
                "carrier_barycentric": list(patch.carrier_barycentric or ()),
                "carrier_unit_normal": list(patch.carrier_unit_normal or ()),
                "receiver_normal_basis": patch.receiver_normal_basis,
            }
        )
    return payload


def _translate(local: Vector3, origin: Vector3) -> Vector3:
    result = (
        local[0] + origin[0],
        local[1] + origin[1],
        local[2] + origin[2],
    )
    if not _finite_vector(result):
        raise JuvenileScientificSceneError("translated coordinate is non-finite.")
    return result


def _expanded_face(
    plant: JuvenileScenePlant,
    face: ScientificMeshFace,
    *,
    local_face_index: int,
    leaves_per_plant: int,
    faces_per_plant: int,
) -> ExpandedJuvenileFace:
    local_leaf_index = face.leaf_rank - 1
    return ExpandedJuvenileFace(
        plant_index=plant.plant_index,
        plant_id=plant.plant_id,
        local_leaf_index=local_leaf_index,
        global_leaf_index=(
            plant.plant_index * leaves_per_plant + local_leaf_index
        ),
        local_face_index=local_face_index,
        global_face_index=(
            plant.plant_index * faces_per_plant + local_face_index
        ),
        face_index_within_leaf=face.face_index,
        face_id=_composite_id(plant.plant_id, face.face_id),
        canonical_face_id=face.face_id,
        canonical_leaf_id=face.leaf_id,
        vertex_indices=face.vertex_indices,
        vertices=tuple(
            _translate(vertex, plant.origin_m) for vertex in face.vertices
        ),
        centroid=_translate(face.centroid, plant.origin_m),
        unit_normal=face.unit_normal,
        area_m2=face.area_m2,
    )


def _expanded_receiver(
    plant: JuvenileScenePlant,
    receiver: MeshPatchReceiverSample,
    *,
    local_receiver_index: int,
    leaves_per_plant: int,
    patches_per_plant: int,
    receivers_per_plant: int,
) -> ExpandedJuvenileReceiver:
    local_leaf_index = receiver.leaf_rank - 1
    local_patch_index = local_receiver_index // 2
    return ExpandedJuvenileReceiver(
        plant_index=plant.plant_index,
        plant_id=plant.plant_id,
        local_leaf_index=local_leaf_index,
        global_leaf_index=(
            plant.plant_index * leaves_per_plant + local_leaf_index
        ),
        local_patch_index=local_patch_index,
        global_patch_index=(
            plant.plant_index * patches_per_plant + local_patch_index
        ),
        local_receiver_index=local_receiver_index,
        global_receiver_index=(
            plant.plant_index * receivers_per_plant + local_receiver_index
        ),
        receiver_id=_composite_id(plant.plant_id, receiver.receiver_id),
        canonical_receiver_id=receiver.receiver_id,
        canonical_patch_id=receiver.patch_id,
        face_id=_composite_id(plant.plant_id, receiver.face_id),
        canonical_face_id=receiver.face_id,
        canonical_leaf_id=receiver.leaf_id,
        side=receiver.side,
        point_m=_translate(receiver.point_m, plant.origin_m),
        normal=receiver.normal,
        area_m2=receiver.area_m2,
        normal_offset_m=receiver.normal_offset_m,
        sampling_profile_id=receiver.sampling_profile_id,
        surface_anchor_m=(
            _translate(receiver.surface_anchor_m, plant.origin_m)
            if receiver.surface_anchor_m is not None
            else None
        ),
        carrier_face_id=(
            _composite_id(plant.plant_id, receiver.carrier_face_id)
            if receiver.carrier_face_id is not None
            else None
        ),
        canonical_carrier_face_id=receiver.carrier_face_id,
        carrier_barycentric=receiver.carrier_barycentric,
        normal_generation_basis_id=receiver.normal_generation_basis_id,
    )


def _split_global_index(
    value: int,
    *,
    per_plant: int,
    total: int,
    label: str,
) -> tuple[int, int]:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < total:
        raise JuvenileScientificSceneError(
            f"global {label} index must be in 0..{total - 1}."
        )
    return divmod(value, per_plant)


def _composite_id(plant_id: str, canonical_id: str) -> str:
    value = f"{plant_id}__{canonical_id}"
    if not _SAFE_ID.fullmatch(value):
        raise JuvenileScientificSceneError("composite scientific ID is unsafe.")
    return value


def _finite_vector(value: Vector3) -> bool:
    return len(value) == 3 and all(math.isfinite(component) for component in value)


def _finite_vectors(values: tuple[Vector3, ...]) -> bool:
    return all(_finite_vector(value) for value in values)


def _subtract(left: Vector3, right: Vector3) -> Vector3:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _dot(left: Vector3, right: Vector3) -> float:
    return math.fsum(left[index] * right[index] for index in range(3))


def _squared_distance(left: Vector3, right: Vector3) -> float:
    difference = _subtract(left, right)
    return _dot(difference, difference)


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _hash_payload(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
