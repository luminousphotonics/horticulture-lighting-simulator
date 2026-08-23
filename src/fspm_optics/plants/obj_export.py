"""Optional debug-only OBJ export from canonical scientific plant meshes."""

from __future__ import annotations

from pathlib import Path

from fspm_optics.plants.models import PlantMesh

DEFAULT_REX_OBJ_MATERIAL_NAME = "lettuce_green"
DEFAULT_REX_OBJ_MTL_FILENAME = "rex_butterhead.mtl"


def export_plant_mesh_to_obj(
    plant: PlantMesh,
    *,
    material_library_filename: str = DEFAULT_REX_OBJ_MTL_FILENAME,
    material_name: str = DEFAULT_REX_OBJ_MATERIAL_NAME,
) -> str:
    """Return OBJ text using exactly the canonical vertices and triangle faces."""

    if not material_library_filename or Path(material_library_filename).name != (
        material_library_filename
    ):
        raise ValueError("material_library_filename must be a plain filename.")
    if not material_name or any(character.isspace() for character in material_name):
        raise ValueError("material_name must be a non-empty token.")
    lines = [
        "# deterministic Rex butterhead scientific mesh debug export",
        f"# plant_id={plant.plant_id} seed={plant.seed}",
        f"mtllib {material_library_filename}",
        f"o {plant.plant_id}",
        f"usemtl {material_name}",
    ]
    vertex_offset = 1
    for leaf in plant.leaves:
        lines.append(f"g {leaf.leaf_id}")
        lines.append(
            f"# leaf_rank={leaf.leaf_rank} leaf_layer={leaf.leaf_layer}"
        )
        lines.extend(
            f"v {x_m:.17g} {y_m:.17g} {z_m:.17g}"
            for x_m, y_m, z_m in leaf.vertices
        )
        for face in leaf.faces:
            a, b, c = (
                vertex_offset + face.vertex_indices[0],
                vertex_offset + face.vertex_indices[1],
                vertex_offset + face.vertex_indices[2],
            )
            lines.append(f"# face_id={face.face_id}")
            lines.append(f"f {a} {b} {c}")
        vertex_offset += len(leaf.vertices)
    return "\n".join(lines) + "\n"


def export_rex_material_to_mtl(
    *,
    material_name: str = DEFAULT_REX_OBJ_MATERIAL_NAME,
) -> str:
    """Return a visual-inspection-only lettuce green OBJ material."""

    if not material_name or any(character.isspace() for character in material_name):
        raise ValueError("material_name must be a non-empty token.")
    return (
        "# visual inspection material; not an optical transport definition\n"
        f"newmtl {material_name}\n"
        "Ka 0.035000 0.070000 0.025000\n"
        "Kd 0.180000 0.480000 0.145000\n"
        "Ks 0.025000 0.025000 0.025000\n"
        "Ns 12.000000\n"
        "d 1.000000\n"
        "illum 2\n"
    )


def write_plant_mesh_mtl(
    path: str | Path,
    *,
    material_name: str = DEFAULT_REX_OBJ_MATERIAL_NAME,
) -> Path:
    """Write the visual-only lettuce material beside a debug OBJ."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        export_rex_material_to_mtl(material_name=material_name),
        encoding="utf-8",
    )
    return output


def write_plant_mesh_obj(
    path: str | Path,
    plant: PlantMesh,
    *,
    material_name: str = DEFAULT_REX_OBJ_MATERIAL_NAME,
) -> Path:
    """Write exact scientific OBJ geometry and its visual-only MTL sidecar."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    material_path = output.with_suffix(".mtl")
    write_plant_mesh_mtl(material_path, material_name=material_name)
    output.write_text(
        export_plant_mesh_to_obj(
            plant,
            material_library_filename=material_path.name,
            material_name=material_name,
        ),
        encoding="utf-8",
    )
    return output
