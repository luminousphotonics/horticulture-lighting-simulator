from __future__ import annotations

import math

import pytest

from fspm_optics.plants import (
    PlantGeometryConfig,
    PlantOpticalAssumptions,
    export_scene_to_radiance,
    fit_plant_grid,
    generate_plant_scene,
    leaf_absorption_surfaces,
)
from fspm_optics.plants.mesh import LEAF_FACE_COUNT, LEAF_VERTEX_COUNT
from fspm_optics.receivers.samples import surface_geometry_by_id


def test_generation_is_deterministic_and_seed_sensitive() -> None:
    config = PlantGeometryConfig(seed=17, leaf_count_per_plant=3)
    assert generate_plant_scene(config) == generate_plant_scene(config)
    assert generate_plant_scene(config) != generate_plant_scene(
        PlantGeometryConfig(seed=18, leaf_count_per_plant=3)
    )


def test_expected_counts_and_unique_ids() -> None:
    scene = generate_plant_scene(
        PlantGeometryConfig(
            seed=3,
            plant_grid_rows=2,
            plant_grid_columns=3,
            leaf_count_per_plant=4,
        )
    )
    assert len(scene.plants) == 6
    leaves = [leaf for plant in scene.plants for leaf in plant.leaves]
    assert len(leaves) == 24
    assert len({plant.plant_id for plant in scene.plants}) == 6
    assert len({leaf.leaf_id for leaf in leaves}) == 24
    assert all(len(leaf.mesh.vertices) == LEAF_VERTEX_COUNT for leaf in leaves)
    assert all(len(leaf.mesh.faces) == LEAF_FACE_COUNT for leaf in leaves)


def test_mesh_patch_areas_and_normals_are_valid() -> None:
    scene = generate_plant_scene(
        PlantGeometryConfig(plant_grid_rows=1, plant_grid_columns=1, leaf_count_per_plant=2)
    )
    surfaces = leaf_absorption_surfaces(scene)
    geometry = surface_geometry_by_id(scene)
    assert len(surfaces) == 2 * LEAF_FACE_COUNT
    assert all(surface.area_m2 > 0.0 and math.isfinite(surface.area_m2) for surface in surfaces)
    for item in geometry.values():
        normal = item["normal"]
        assert math.sqrt(sum(component * component for component in normal)) == pytest.approx(1.0)


def test_layout_preserves_axis_mapping_and_room_bounds() -> None:
    layout = fit_plant_grid(12, 8, rows=5, columns=3)
    assert layout.length.count <= 5
    assert layout.width.count <= 3
    assert layout.length.axis_m == pytest.approx(12 * 0.3048)
    assert layout.width.axis_m == pytest.approx(8 * 0.3048)


def test_radiance_polygon_export_is_deterministic() -> None:
    scene = generate_plant_scene(
        PlantGeometryConfig(plant_grid_rows=1, plant_grid_columns=1, leaf_count_per_plant=2)
    )
    text = export_scene_to_radiance(scene)
    assert text == export_scene_to_radiance(scene)
    assert "void plastic plant_leaf_material" in text
    for surface in leaf_absorption_surfaces(scene):
        assert f"polygon {surface.surface_id}" in text


def test_invalid_optical_partition_is_rejected() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        PlantOpticalAssumptions(reflectance=0.5, transmittance=0.5, absorptance=0.5)
