"""Radiance text export for deterministic plant meshes."""

from __future__ import annotations

from rad_rebuild.radiance.engine.plants.models import LeafGeometry, PlantScene


def export_scene_to_radiance(
    scene: PlantScene,
    *,
    leaf_material_definition: str | None = None,
    optical_assumption_comment: str | None = None,
) -> str:
    """Return deterministic Phase 01 Radiance text for plant geometry."""

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
