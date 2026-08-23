"""Immutable data structures for generated plant geometry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from fspm_optics.plants.config import PlantGeometryConfig

if TYPE_CHECKING:
    from fspm_optics.plants.rex import RexPlantConfig
    from fspm_optics.plants.rex_juvenile import RexJuvenilePreheadingConfig

Vector3: TypeAlias = tuple[float, float, float]
TriangleFace: TypeAlias = tuple[int, int, int]
LeafLayer: TypeAlias = Literal["outer", "mid", "inner"]


@dataclass(frozen=True)
class LeafMesh:
    """Simple triangle mesh for one generated leaf surface."""

    vertices: tuple[Vector3, ...]
    faces: tuple[TriangleFace, ...]


@dataclass(frozen=True)
class LeafGeometry:
    """Generated leaf geometry and stable cross-export metadata."""

    plant_id: str
    leaf_id: str
    leaf_index: int
    azimuth_rad: float
    length_m: float
    width_m: float
    tilt_rad: float
    curvature_m: float
    mesh: LeafMesh
    radiance_material_id: str


@dataclass(frozen=True)
class PlantGeometry:
    """A deterministic leafy-green plant instance in a grid."""

    plant_id: str
    row: int
    column: int
    center_m: Vector3
    leaves: tuple[LeafGeometry, ...]


@dataclass(frozen=True)
class PlantScene:
    """Complete deterministic plant scene generated from one config."""

    config: PlantGeometryConfig
    plants: tuple[PlantGeometry, ...]


@dataclass(frozen=True, slots=True)
class ScientificMeshFace:
    """One stable triangular scientific receiver/export surface."""

    plant_id: str
    leaf_id: str
    leaf_rank: int
    leaf_layer: LeafLayer
    face_id: str
    face_index: int
    vertex_indices: TriangleFace
    vertices: tuple[Vector3, Vector3, Vector3]
    centroid: Vector3
    unit_normal: Vector3
    area_m2: float


@dataclass(frozen=True, slots=True)
class ScientificMeshPatch:
    """Stable patch with separate aggregate and receiver-carrier geometry."""

    plant_id: str
    leaf_id: str
    leaf_rank: int
    leaf_layer: LeafLayer
    patch_id: str
    face_id: str
    patch_index: int
    patch_u_index: int
    patch_v_index: int
    triangle_face_ids: tuple[str, ...]
    centroid: Vector3
    unit_normal: Vector3
    area_m2: float
    surface_anchor: Vector3 | None = None
    carrier_face_id: str | None = None
    carrier_barycentric: tuple[float, float, float] | None = None
    carrier_unit_normal: Vector3 | None = None
    receiver_normal_basis: str = "area_weighted_patch_normal_legacy"


@dataclass(frozen=True, slots=True)
class LeafWorldTransform:
    """Deterministic placement metadata for a generated Rex leaf."""

    origin_m: Vector3
    azimuth_rad: float
    elevation_rad: float
    horizontal_scale: float
    vertical_scale: float
    vertical_offset_m: float


@dataclass(frozen=True, slots=True)
class LeafSurface:
    """Curved Rex leaf surface and its stable scientific face records."""

    plant_id: str
    leaf_id: str
    leaf_rank: int
    leaf_layer: LeafLayer
    world_transform: LeafWorldTransform
    azimuth_rad: float
    elevation_rad: float
    length_m: float
    width_m: float
    nominal_thickness_m: float
    vertices: tuple[Vector3, ...]
    triangle_indices: tuple[TriangleFace, ...]
    faces: tuple[ScientificMeshFace, ...]
    patches: tuple[ScientificMeshPatch, ...]
    radiance_material_id: str


@dataclass(frozen=True, slots=True)
class PlantMesh:
    """Canonical deterministic scientific mesh for one Rex plant."""

    plant_id: str
    config: "RexPlantConfig | RexJuvenilePreheadingConfig"
    seed: int
    leaves: tuple[LeafSurface, ...]

    @property
    def faces(self) -> tuple[ScientificMeshFace, ...]:
        return tuple(face for leaf in self.leaves for face in leaf.faces)

    @property
    def face_count(self) -> int:
        return sum(len(leaf.faces) for leaf in self.leaves)

    @property
    def patches(self) -> tuple[ScientificMeshPatch, ...]:
        return tuple(patch for leaf in self.leaves for patch in leaf.patches)

    @property
    def patch_count(self) -> int:
        return sum(len(leaf.patches) for leaf in self.leaves)

    @property
    def receiver_count(self) -> int:
        return 2 * self.patch_count

    @property
    def vertex_count(self) -> int:
        return sum(len(leaf.vertices) for leaf in self.leaves)

    @property
    def one_sided_area_m2(self) -> float:
        return sum(face.area_m2 for face in self.faces)
