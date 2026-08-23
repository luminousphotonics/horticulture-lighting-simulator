"""Numerical validation for canonical scientific plant meshes."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path

from fspm_optics.plants.models import PlantMesh
from fspm_optics.plants.surface_geometry import barycentric_point


@dataclass(frozen=True, slots=True)
class PlantMeshValidationReport:
    plant_id: str
    seed: int
    leaf_count: int
    patch_count: int
    receiver_count: int
    vertex_count: int
    face_count: int
    projected_diameter_m: float
    height_m: float
    total_leaf_area_m2: float
    leaf_patch_grid: tuple[int, int]

    @property
    def one_sided_area_m2(self) -> float:
        """Compatibility alias for total one-sided scientific leaf area."""

        return self.total_leaf_area_m2

    def to_dict(self) -> dict[str, object]:
        return {
            "plant_id": self.plant_id,
            "seed": self.seed,
            "leaf_count": self.leaf_count,
            "patch_count": self.patch_count,
            "receiver_count": self.receiver_count,
            "vertex_count": self.vertex_count,
            "face_count": self.face_count,
            "projected_diameter_m": self.projected_diameter_m,
            "height_m": self.height_m,
            "total_leaf_area_m2": self.total_leaf_area_m2,
            "leaf_patch_grid": list(self.leaf_patch_grid),
        }


def validate_plant_mesh(plant: PlantMesh) -> PlantMeshValidationReport:
    """Validate required finite geometry and stable-ID invariants."""

    if not plant.leaves:
        raise ValueError("Plant mesh must contain at least one leaf.")
    leaf_ids = [leaf.leaf_id for leaf in plant.leaves]
    if len(leaf_ids) != len(set(leaf_ids)):
        raise ValueError("Plant mesh leaf IDs must be unique.")
    expected_ranks = list(range(1, len(plant.leaves) + 1))
    if [leaf.leaf_rank for leaf in plant.leaves] != expected_ranks:
        raise ValueError("Plant mesh leaf ranks must be contiguous and one-based.")
    vertices = [vertex for leaf in plant.leaves for vertex in leaf.vertices]
    if any(not all(math.isfinite(value) for value in vertex) for vertex in vertices):
        raise ValueError("Plant mesh contains non-finite vertices.")
    faces = plant.faces
    if not faces:
        raise ValueError("Plant mesh must contain at least one face.")
    face_ids = [face.face_id for face in faces]
    if len(face_ids) != len(set(face_ids)):
        raise ValueError("Plant mesh face IDs must be unique.")
    for face in faces:
        if not math.isfinite(face.area_m2) or face.area_m2 <= 0.0:
            raise ValueError(f"Face {face.face_id} must have finite positive area.")
        if not all(math.isfinite(value) for value in face.centroid):
            raise ValueError(f"Face {face.face_id} has a non-finite centroid.")
        if not all(math.isfinite(value) for value in face.unit_normal):
            raise ValueError(f"Face {face.face_id} has a non-finite normal.")
        magnitude = math.sqrt(sum(value * value for value in face.unit_normal))
        if not math.isclose(magnitude, 1.0, rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError(f"Face {face.face_id} normal is not unit length.")
    patches = plant.patches
    faces_by_id = {face.face_id: face for face in faces}
    requires_carrier = bool(
        getattr(plant.config, "uses_surface_constrained_receivers", False)
    )
    patch_ids = [patch.patch_id for patch in patches]
    if len(patch_ids) != len(set(patch_ids)):
        raise ValueError("Plant mesh patch IDs must be unique.")
    expected_patch_count = (
        len(plant.leaves)
        * plant.config.leaf_patch_u
        * plant.config.leaf_patch_v
    )
    if len(patches) != expected_patch_count:
        raise ValueError(
            "Plant mesh patch count does not match leaf count and patch grid."
        )
    for patch in patches:
        if not math.isfinite(patch.area_m2) or patch.area_m2 <= 0.0:
            raise ValueError(
                f"Patch {patch.patch_id} must have finite positive area."
            )
        magnitude = math.sqrt(sum(value * value for value in patch.unit_normal))
        if not math.isclose(magnitude, 1.0, rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError(f"Patch {patch.patch_id} normal is not unit length.")
        carrier_values = (
            patch.surface_anchor,
            patch.carrier_face_id,
            patch.carrier_barycentric,
            patch.carrier_unit_normal,
        )
        carrier_complete = all(value is not None for value in carrier_values)
        if (
            requires_carrier != carrier_complete
            or (any(value is not None for value in carrier_values) and not carrier_complete)
        ):
            raise ValueError(
                f"Patch {patch.patch_id} carrier geometry does not match its profile."
            )
        if requires_carrier:
            carrier = faces_by_id.get(str(patch.carrier_face_id))
            if (
                carrier is None
                or carrier.face_id not in patch.triangle_face_ids
                or patch.carrier_unit_normal != carrier.unit_normal
                or patch.receiver_normal_basis
                != "carrier_triangle_winding_defined_unit_normal"
                or patch.surface_anchor is None
                or patch.carrier_barycentric is None
            ):
                raise ValueError(f"Patch {patch.patch_id} carrier is invalid.")
            reconstructed = barycentric_point(
                carrier.vertices,
                patch.carrier_barycentric,
            )
            if math.fsum(
                (reconstructed[index] - patch.surface_anchor[index]) ** 2
                for index in range(3)
            ) > 1e-24:
                raise ValueError(
                    f"Patch {patch.patch_id} carrier anchor is off-triangle."
                )
    face_area = math.fsum(face.area_m2 for face in faces)
    patch_area = math.fsum(patch.area_m2 for patch in patches)
    if not math.isclose(face_area, patch_area, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError("Receiver patch area must preserve total triangle area.")
    maximum_radius = max(math.hypot(vertex[0], vertex[1]) for vertex in vertices)
    minimum_z = min(vertex[2] for vertex in vertices)
    maximum_z = max(vertex[2] for vertex in vertices)
    return PlantMeshValidationReport(
        plant_id=plant.plant_id,
        seed=plant.seed,
        leaf_count=len(plant.leaves),
        patch_count=len(patches),
        receiver_count=2 * len(patches),
        vertex_count=len(vertices),
        face_count=len(faces),
        projected_diameter_m=2.0 * maximum_radius,
        height_m=maximum_z - minimum_z,
        total_leaf_area_m2=face_area,
        leaf_patch_grid=plant.config.leaf_patch_grid,
    )


def rex_geometry_summary(plant: PlantMesh) -> dict[str, object]:
    """Return the deterministic scientific geometry/receiver summary."""

    return validate_plant_mesh(plant).to_dict()


def write_rex_geometry_summary(path: str | Path, plant: PlantMesh) -> Path:
    """Write the deterministic geometry summary beside a debug export."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(rex_geometry_summary(plant), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output
