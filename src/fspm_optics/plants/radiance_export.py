"""Radiance text export for deterministic plant meshes."""

from __future__ import annotations

from fspm_optics.plants.models import LeafGeometry, PlantMesh, PlantScene


def export_scene_to_radiance(
    scene: PlantScene | PlantMesh,
    *,
    leaf_material_definition: str | None = None,
    optical_assumption_comment: str | None = None,
) -> str:
    """Return deterministic Phase 01 Radiance text for plant geometry."""

    if isinstance(scene, PlantMesh):
        return export_plant_mesh_to_radiance(
            scene,
            material_definition=leaf_material_definition,
            include_metadata_comments=True,
        )

    material = scene.config.optical
    material_lines = (
        leaf_material_definition.strip().splitlines()
        if leaf_material_definition is not None
        else [
            f"void plastic {scene.plants[0].leaves[0].radiance_material_id}",
            "0",
            "0",
            f"5 {material.reflectance:.6f} {material.reflectance:.6f} "
            f"{material.reflectance:.6f} 0.000000 0.000000",
        ]
    )
    lines = [
        "# FSPM Phase 01 deterministic plant geometry",
        optical_assumption_comment
        or (
            "# optical_assumptions "
            f"reflectance={material.reflectance:.6f} "
            f"transmittance={material.transmittance:.6f} "
            f"absorptance={material.absorptance:.6f}"
        ),
        *material_lines,
        "",
    ]

    for plant in scene.plants:
        lines.append(f"# plant_id={plant.plant_id} row={plant.row} column={plant.column}")
        for leaf in plant.leaves:
            lines.extend(_leaf_to_radiance(leaf))
    return "\n".join(lines).rstrip() + "\n"


def export_plant_mesh_to_radiance(
    plant: PlantMesh,
    *,
    material_name: str | None = None,
    material_definition: str | None = None,
    include_metadata_comments: bool = True,
) -> str:
    """Export exact scientific mesh triangles without resampling geometry."""

    resolved_material = material_name or plant.config.radiance_material_id
    if not resolved_material:
        raise ValueError("material_name must be non-empty.")
    lines = [
        "# deterministic Rex butterhead scientific plant mesh",
        f"# plant_id={plant.plant_id} seed={plant.seed}",
        f"# leaf_count={len(plant.leaves)} face_count={plant.face_count}",
    ]
    if material_definition is not None:
        definition = material_definition.strip()
        if not definition:
            raise ValueError("material_definition must be non-empty when supplied.")
        lines.extend((definition, ""))
    else:
        lines.append("# material definition intentionally external to geometry export")
        lines.append("")
    for leaf in plant.leaves:
        if include_metadata_comments:
            lines.append(
                f"# leaf_id={leaf.leaf_id} leaf_rank={leaf.leaf_rank} "
                f"leaf_layer={leaf.leaf_layer}"
            )
        for face in leaf.faces:
            if include_metadata_comments:
                lines.append(
                    f"# face_id={face.face_id} area_m2={face.area_m2:.12g}"
                )
            lines.extend(
                (
                    f"{resolved_material} polygon {face.face_id}",
                    "0",
                    "0",
                    "9",
                )
            )
            lines.extend(
                f"{x_m:.17g} {y_m:.17g} {z_m:.17g}"
                for x_m, y_m, z_m in face.vertices
            )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _leaf_to_radiance(leaf: LeafGeometry) -> list[str]:
    lines: list[str] = []
    for face_index, face in enumerate(leaf.mesh.faces):
        surface_id = f"{leaf.leaf_id}_face_{face_index:04d}"
        lines.append(f"# leaf_id={leaf.leaf_id} face_index={face_index}")
        lines.append(f"{leaf.radiance_material_id} polygon {surface_id}")
        lines.append("0")
        lines.append("0")
        lines.append("9")
        for vertex_index in face:
            x_m, y_m, z_m = leaf.mesh.vertices[vertex_index]
            lines.append(f"{x_m:.9f} {y_m:.9f} {z_m:.9f}")
        lines.append("")
    return lines
