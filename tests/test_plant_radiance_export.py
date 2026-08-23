from __future__ import annotations

from fspm_optics.plants import (
    RexPlantConfig,
    export_plant_mesh_to_radiance,
    export_scene_to_radiance,
    generate_rex_butterhead_plant,
)


def compact_plant():
    return generate_rex_butterhead_plant(
        RexPlantConfig(
            leaf_count=24,
            leaf_patch_u=2,
            leaf_patch_v=2,
        )
    )


def test_radiance_export_preserves_scientific_face_count_and_order() -> None:
    plant = compact_plant()
    text = export_plant_mesh_to_radiance(plant)
    polygon_lines = [line for line in text.splitlines() if " polygon " in line]

    assert len(polygon_lines) == plant.face_count
    assert polygon_lines == [
        f"{plant.config.radiance_material_id} polygon {face.face_id}"
        for face in plant.faces
    ]
    assert text == export_plant_mesh_to_radiance(plant)
    assert text == export_scene_to_radiance(plant)


def test_radiance_export_uses_exact_face_vertices_without_resampling() -> None:
    plant = compact_plant()
    first_face = plant.faces[0]
    text = export_plant_mesh_to_radiance(
        plant,
        material_definition="void plastic rex_leaf_surface\n0\n0\n5 0.2 0.2 0.2 0 0",
    )

    assert f"polygon {first_face.face_id}" in text
    for x_m, y_m, z_m in first_face.vertices:
        assert f"{x_m:.17g} {y_m:.17g} {z_m:.17g}" in text
    assert text.count("void plastic rex_leaf_surface") == 1
