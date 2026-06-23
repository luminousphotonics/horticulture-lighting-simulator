"""Immutable data structures for generated plant geometry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from rad_rebuild.radiance.engine.plants.config import PlantGeometryConfig

Vector3: TypeAlias = tuple[float, float, float]
TriangleFace: TypeAlias = tuple[int, int, int]


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
