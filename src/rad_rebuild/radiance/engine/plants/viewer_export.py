"""JSON-friendly viewer export for deterministic plant geometry."""

from __future__ import annotations

from rad_rebuild.radiance.engine.plants.models import LeafGeometry, PlantScene


def export_scene_to_viewer(scene: PlantScene) -> dict[str, object]:
    """Return JSON-serializable plant scene data without writing files."""

    return {
        "schema": "rad_rebuild.fspm.plants.viewer.v1",
        "units": "meters",
        "config": {
            "seed": scene.config.seed,
            "plant_grid_rows": scene.config.plant_grid_rows,
            "plant_grid_columns": scene.config.plant_grid_columns,
            "plant_spacing_m": scene.config.plant_spacing_m,
            "plant_height_m": scene.config.plant_height_m,
            "canopy_radius_m": scene.config.canopy_radius_m,
            "leaf_count_per_plant": scene.config.leaf_count_per_plant,
            "growth_stage": scene.config.growth_stage,
        },
        "material": {
            "id": "plant_leaf_material",
            "reflectance": scene.config.optical.reflectance,
            "transmittance": scene.config.optical.transmittance,
            "absorptance": scene.config.optical.absorptance,
        },
        "plants": [
            {
                "plant_id": plant.plant_id,
                "row": plant.row,
                "column": plant.column,
                "center_m": list(plant.center_m),
                "leaves": [_leaf_to_viewer(leaf) for leaf in plant.leaves],
            }
            for plant in scene.plants
        ],
    }


def _leaf_to_viewer(leaf: LeafGeometry) -> dict[str, object]:
    return {
        "plant_id": leaf.plant_id,
        "leaf_id": leaf.leaf_id,
        "leaf_index": leaf.leaf_index,
        "radiance_material_id": leaf.radiance_material_id,
        "metadata": {
            "azimuth_rad": leaf.azimuth_rad,
            "length_m": leaf.length_m,
            "width_m": leaf.width_m,
            "tilt_rad": leaf.tilt_rad,
            "curvature_m": leaf.curvature_m,
        },
        "mesh": {
            "vertices": [list(vertex) for vertex in leaf.mesh.vertices],
            "faces": [list(face) for face in leaf.mesh.faces],
        },
    }
