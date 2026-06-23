from __future__ import annotations

import importlib
import json
import math
import pkgutil
from pathlib import Path

import pytest

from rad_rebuild.radiance.engine.plants import (
    PlantGeometryConfig,
    PlantOpticalAssumptions,
    export_scene_to_radiance,
    export_scene_to_viewer,
    generate_plant_scene,
)
from rad_rebuild.radiance.engine.plants.mesh import LEAF_FACE_COUNT, LEAF_VERTEX_COUNT


def test_default_config_is_valid() -> None:
    config = PlantGeometryConfig()

    assert config.seed == 1
    assert config.plant_grid_rows == 2
    assert config.plant_grid_columns == 2
    assert config.optical == PlantOpticalAssumptions()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"plant_grid_rows": 0},
        {"plant_grid_columns": 0},
        {"plant_spacing_m": 0.0},
        {"plant_height_m": -0.1},
        {"canopy_radius_m": 0.0},
        {"leaf_count_per_plant": 0},
        {"leaf_length_range_m": (0.0, 0.2)},
        {"leaf_length_range_m": (0.2, 0.1)},
        {"leaf_width_range_m": (-0.1, 0.1)},
        {"leaf_width_range_m": (0.1, 0.05)},
        {"leaf_tilt_range_deg": (50.0, 10.0)},
        {"leaf_curvature_m": -0.001},
        {"plant_spacing_m": math.nan},
        {"plant_height_m": math.inf},
    ],
)
def test_invalid_dimensions_fail(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PlantGeometryConfig(**kwargs)


@pytest.mark.parametrize("growth_stage", [-0.1, 1.1, math.nan, math.inf])
def test_invalid_growth_stage_fails(growth_stage: float) -> None:
    with pytest.raises(ValueError):
        PlantGeometryConfig(growth_stage=growth_stage)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reflectance": -0.1, "transmittance": 0.1, "absorptance": 1.0},
        {"reflectance": 1.1, "transmittance": 0.0, "absorptance": -0.1},
        {"reflectance": math.nan, "transmittance": 0.1, "absorptance": 0.9},
        {"reflectance": 0.4, "transmittance": 0.4, "absorptance": 0.4},
        {"reflectance": 0.2, "transmittance": 0.1, "absorptance": 0.6},
    ],
)
def test_invalid_material_coefficients_fail(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        PlantOpticalAssumptions(**kwargs)


def test_same_seed_and_config_gives_identical_geometry() -> None:
    config = PlantGeometryConfig(seed=42)

    assert generate_plant_scene(config) == generate_plant_scene(config)


def test_different_seed_changes_geometry() -> None:
    seed_one = generate_plant_scene(PlantGeometryConfig(seed=42))
    seed_two = generate_plant_scene(PlantGeometryConfig(seed=43))

    assert seed_one != seed_two
    assert (
        seed_one.plants[0].leaves[0].mesh.vertices
        != seed_two.plants[0].leaves[0].mesh.vertices
    )


def test_expected_plant_and_leaf_counts() -> None:
    scene = generate_plant_scene()

    assert len(scene.plants) == 4
    assert sum(len(plant.leaves) for plant in scene.plants) == 48


def test_expected_vertex_and_face_counts_per_leaf() -> None:
    scene = generate_plant_scene()

    for plant in scene.plants:
        for leaf in plant.leaves:
            assert len(leaf.mesh.vertices) == LEAF_VERTEX_COUNT
            assert len(leaf.mesh.faces) == LEAF_FACE_COUNT


def test_plant_ids_are_unique_and_deterministic() -> None:
    scene = generate_plant_scene()
    plant_ids = [plant.plant_id for plant in scene.plants]

    assert plant_ids == [
        "plant_r000_c000",
        "plant_r000_c001",
        "plant_r001_c000",
        "plant_r001_c001",
    ]
    assert len(plant_ids) == len(set(plant_ids))


def test_leaf_ids_are_unique_and_deterministic() -> None:
    scene = generate_plant_scene()
    leaf_ids = [leaf.leaf_id for plant in scene.plants for leaf in plant.leaves]

    assert leaf_ids[:3] == [
        "plant_r000_c000_leaf_000",
        "plant_r000_c000_leaf_001",
        "plant_r000_c000_leaf_002",
    ]
    assert leaf_ids[-1] == "plant_r001_c001_leaf_011"
    assert len(leaf_ids) == len(set(leaf_ids))


def test_all_coordinates_are_finite() -> None:
    scene = generate_plant_scene()

    for plant in scene.plants:
        for value in plant.center_m:
            assert math.isfinite(value)
        for leaf in plant.leaves:
            for vertex in leaf.mesh.vertices:
                assert all(math.isfinite(value) for value in vertex)


def test_radiance_export_is_deterministic() -> None:
    scene = generate_plant_scene(PlantGeometryConfig(seed=99))

    assert export_scene_to_radiance(scene) == export_scene_to_radiance(scene)


def test_radiance_export_contains_expected_material_and_surface_ids() -> None:
    rad_text = export_scene_to_radiance(generate_plant_scene())

    assert "void plastic plant_leaf_material" in rad_text
    assert "reflectance=0.220000 transmittance=0.080000" in rad_text
    assert "polygon plant_r000_c000_leaf_000_face_0000" in rad_text
    assert "# leaf_id=plant_r001_c001_leaf_011" in rad_text


def test_viewer_export_is_json_serializable() -> None:
    payload = export_scene_to_viewer(generate_plant_scene())

    serialized = json.dumps(payload, sort_keys=True)

    assert "plant_r000_c000_leaf_000" in serialized


def test_viewer_export_contains_expected_ids_and_counts() -> None:
    payload = export_scene_to_viewer(generate_plant_scene())
    plants = payload["plants"]

    assert isinstance(plants, list)
    assert len(plants) == 4
    assert plants[0]["plant_id"] == "plant_r000_c000"
    assert len(plants[0]["leaves"]) == 12
    assert plants[0]["leaves"][0]["leaf_id"] == "plant_r000_c000_leaf_000"
    assert len(plants[0]["leaves"][0]["mesh"]["vertices"]) == LEAF_VERTEX_COUNT


def test_plant_package_does_not_import_web_or_backend_code() -> None:
    plants_package = importlib.import_module("rad_rebuild.radiance.engine.plants")
    package_path = plants_package.__path__
    forbidden_imports = (
        "rad_rebuild.radiance.backend",
        "rad_rebuild.web",
    )

    for module_info in pkgutil.walk_packages(
        package_path, plants_package.__name__ + "."
    ):
        module = importlib.import_module(module_info.name)
        module_file = getattr(module, "__file__", None)
        assert module_file is not None
        source = Path(module_file).read_text(encoding="utf-8")
        for forbidden in forbidden_imports:
            assert forbidden not in source
