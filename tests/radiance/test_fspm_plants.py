from __future__ import annotations

import importlib
import inspect
import json
import math
import pkgutil
from pathlib import Path
from typing import Any

import pytest

from rad_rebuild.radiance.engine.plants import (
    CALIBRATED_PLANT_EDGE_CENTER_MARGIN_M,
    PlantGeometryConfig,
    PlantOpticalAssumptions,
    canonical_room_dimensions_ft,
    export_scene_to_radiance,
    export_scene_to_viewer,
    fit_plant_geometry_config_to_room,
    fit_plant_grid_axis,
    generate_plant_scene,
    measure_plant_local_xy_footprint,
    write_plant_artifacts,
)
from rad_rebuild.radiance.engine.plants import generator as plant_generator
from rad_rebuild.radiance.engine.plants.artifacts import (
    PLANT_ARTIFACT_SCHEMA,
    PLANT_ARTIFACT_SCHEMA_VERSION,
    PLANT_CONFIG_FILENAME,
    PLANT_ABSORPTION_SURFACES_FILENAME,
    PLANTS_MANIFEST_FILENAME,
    PLANTS_RAD_FILENAME,
    PLANTS_VIEWER_FILENAME,
)
from rad_rebuild.radiance.engine.plants.mesh import LEAF_FACE_COUNT, LEAF_VERTEX_COUNT
from rad_rebuild.radiance.engine.plants.absorption import (  # noqa: E402
    PHOTON_ABSORPTION_SCAFFOLD_SCHEMA,
    build_absorption_surface_registry,
    leaf_absorption_surfaces,
)


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
def test_invalid_dimensions_fail(kwargs: dict[str, Any]) -> None:
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


def _room_fit_config(length_ft: int, width_ft: int) -> PlantGeometryConfig:
    return fit_plant_geometry_config_to_room(
        PlantGeometryConfig(seed=1, plant_spacing_m=0.40),
        length_ft=length_ft,
        width_ft=width_ft,
    )


def _forced_room_fit_config(
    length_ft: int,
    width_ft: int,
    *,
    rows: int,
    columns: int,
) -> PlantGeometryConfig:
    return fit_plant_geometry_config_to_room(
        PlantGeometryConfig(
            seed=1,
            plant_grid_rows=rows,
            plant_grid_columns=columns,
            plant_spacing_m=0.40,
        ),
        length_ft=length_ft,
        width_ft=width_ft,
        rows=rows,
        columns=columns,
    )


def _local_vertices(
    scene,
) -> dict[tuple[str, str], tuple[tuple[float, float, float], ...]]:
    local: dict[tuple[str, str], tuple[tuple[float, float, float], ...]] = {}
    for plant in scene.plants:
        center_x, center_y, center_z = plant.center_m
        for leaf in plant.leaves:
            local[(plant.plant_id, leaf.leaf_id)] = tuple(
                (
                    vertex_x - center_x,
                    vertex_y - center_y,
                    vertex_z - center_z,
                )
                for vertex_x, vertex_y, vertex_z in leaf.mesh.vertices
            )
    return local


def _one_sided_leaf_area(scene) -> float:
    return sum(surface.area_m2 for surface in leaf_absorption_surfaces(scene))


def _assert_local_vertices_equal(
    first: dict[tuple[str, str], tuple[tuple[float, float, float], ...]],
    second: dict[tuple[str, str], tuple[tuple[float, float, float], ...]],
) -> None:
    assert first.keys() == second.keys()
    for key, first_vertices in first.items():
        second_vertices = second[key]
        assert len(first_vertices) == len(second_vertices)
        for first_vertex, second_vertex in zip(
            first_vertices,
            second_vertices,
            strict=True,
        ):
            assert first_vertex == pytest.approx(second_vertex, abs=1e-12)


def _assert_scene_inside_room(scene, *, epsilon_m: float = 1e-9) -> None:
    assert scene.config.room_length_m is not None
    assert scene.config.room_width_m is not None
    x_min = -scene.config.room_length_m / 2.0 - epsilon_m
    x_max = scene.config.room_length_m / 2.0 + epsilon_m
    y_min = -scene.config.room_width_m / 2.0 - epsilon_m
    y_max = scene.config.room_width_m / 2.0 + epsilon_m
    for plant in scene.plants:
        center_x, center_y, _center_z = plant.center_m
        assert x_min <= center_x <= x_max
        assert y_min <= center_y <= y_max
        for leaf in plant.leaves:
            for vertex_x, vertex_y, _vertex_z in leaf.mesh.vertices:
                assert x_min <= vertex_x <= x_max
                assert y_min <= vertex_y <= y_max


def _assert_plant_bbox_inside_room(scene, *, epsilon_m: float = 1e-9) -> None:
    assert scene.config.room_length_m is not None
    assert scene.config.room_width_m is not None
    xs = [
        vertex_x
        for plant in scene.plants
        for leaf in plant.leaves
        for vertex_x, _vertex_y, _vertex_z in leaf.mesh.vertices
    ]
    ys = [
        vertex_y
        for plant in scene.plants
        for leaf in plant.leaves
        for _vertex_x, vertex_y, _vertex_z in leaf.mesh.vertices
    ]
    assert min(xs) >= -scene.config.room_length_m / 2.0 - epsilon_m
    assert max(xs) <= scene.config.room_length_m / 2.0 + epsilon_m
    assert min(ys) >= -scene.config.room_width_m / 2.0 - epsilon_m
    assert max(ys) <= scene.config.room_width_m / 2.0 + epsilon_m


def test_room_fit_generator_has_no_leaf_vertex_clipping_path() -> None:
    source = inspect.getsource(plant_generator)

    assert "clip" not in source.lower()
    assert "clamp" not in source.lower()
    assert "min(max(vertex" not in source


def test_room_fit_measures_unmodified_local_plant_footprint() -> None:
    config = PlantGeometryConfig(seed=1, plant_grid_rows=8, plant_grid_columns=8)
    scene = generate_plant_scene(config)
    footprint = measure_plant_local_xy_footprint(config)
    expected_half_x = max(
        abs(vertex_x - plant.center_m[0])
        for plant in scene.plants
        for leaf in plant.leaves
        for vertex_x, _vertex_y, _vertex_z in leaf.mesh.vertices
    )
    expected_half_y = max(
        abs(vertex_y - plant.center_m[1])
        for plant in scene.plants
        for leaf in plant.leaves
        for _vertex_x, vertex_y, _vertex_z in leaf.mesh.vertices
    )

    assert footprint.half_extent_x_m == pytest.approx(expected_half_x)
    assert footprint.half_extent_y_m == pytest.approx(expected_half_y)


def test_fit_room_plant_geometry_stays_inside_unique_10_to_20_rooms() -> None:
    for length_ft in range(10, 21):
        for width_ft in range(length_ft, 21):
            scene = generate_plant_scene(_room_fit_config(length_ft, width_ft))
            _assert_scene_inside_room(scene)


def test_fit_room_plant_grid_preserves_10x10_calibration() -> None:
    config = _room_fit_config(10, 10)
    scene = generate_plant_scene(config)
    first_row = [plant for plant in scene.plants if plant.row == 0]
    first_column = [plant for plant in scene.plants if plant.column == 0]

    assert config.plant_grid_rows == 8
    assert config.plant_grid_columns == 8
    assert config.plant_row_margin_m is not None
    assert config.plant_column_margin_m is not None
    assert config.plant_row_margin_m >= CALIBRATED_PLANT_EDGE_CENTER_MARGIN_M
    assert config.plant_column_margin_m >= CALIBRATED_PLANT_EDGE_CENTER_MARGIN_M
    assert config.plant_row_spacing_m == pytest.approx(0.40, abs=0.02)
    assert config.plant_column_spacing_m == pytest.approx(0.40, abs=0.02)
    assert first_row[1].center_m[1] - first_row[0].center_m[1] == pytest.approx(
        config.plant_column_spacing_m
    )
    assert first_column[1].center_m[0] - first_column[0].center_m[0] == pytest.approx(
        config.plant_row_spacing_m
    )


def test_fit_room_plant_grid_maps_canonical_rectangular_axes_without_transpose() -> None:
    assert canonical_room_dimensions_ft(10, 12) == (12.0, 10.0)
    assert canonical_room_dimensions_ft(12, 10) == (12.0, 10.0)

    config = _room_fit_config(10, 12)
    scene = generate_plant_scene(config)
    row_indexes = {plant.row for plant in scene.plants}
    column_indexes = {plant.column for plant in scene.plants}

    assert config.room_length_m == pytest.approx(12 * 0.3048)
    assert config.room_width_m == pytest.approx(10 * 0.3048)
    assert config.plant_grid_rows == 9
    assert config.plant_grid_columns == 8
    assert row_indexes == set(range(9))
    assert column_indexes == set(range(8))
    assert config.room_length_m is not None
    assert config.room_width_m is not None
    assert config.plant_column_margin_m is not None
    assert config.plant_row_margin_m is not None
    room_length_m = config.room_length_m
    room_width_m = config.room_width_m
    plant_row_margin_m = config.plant_row_margin_m
    plant_column_margin_m = config.plant_column_margin_m
    assert max(plant.center_m[0] for plant in scene.plants) == pytest.approx(
        room_length_m / 2.0 - plant_row_margin_m
    )
    assert max(plant.center_m[1] for plant in scene.plants) == pytest.approx(
        room_width_m / 2.0 - plant_column_margin_m
    )

    transposed_config = _room_fit_config(12, 10)
    transposed_scene = generate_plant_scene(transposed_config)
    assert transposed_config.plant_grid_rows == 9
    assert transposed_config.plant_grid_columns == 8
    assert transposed_config.room_length_m == pytest.approx(config.room_length_m)
    assert transposed_config.room_width_m == pytest.approx(config.room_width_m)
    assert {plant.row for plant in transposed_scene.plants} == set(range(9))
    assert {plant.column for plant in transposed_scene.plants} == set(range(8))
    _assert_plant_bbox_inside_room(scene)
    _assert_plant_bbox_inside_room(transposed_scene)


@pytest.mark.parametrize(("length_ft", "width_ft"), [(10, 11), (10, 12), (12, 12)])
def test_fit_room_perimeter_cases_do_not_hang_outside(
    length_ft: int,
    width_ft: int,
) -> None:
    scene = generate_plant_scene(_room_fit_config(length_ft, width_ft))

    _assert_scene_inside_room(scene)


def test_room_fit_preserves_local_leaf_geometry_across_room_sizes() -> None:
    ten_by_ten = generate_plant_scene(
        _forced_room_fit_config(10, 10, rows=8, columns=8)
    )
    twelve_by_twelve = generate_plant_scene(
        _forced_room_fit_config(12, 12, rows=8, columns=8)
    )

    _assert_local_vertices_equal(
        _local_vertices(ten_by_ten),
        _local_vertices(twelve_by_twelve),
    )
    assert _one_sided_leaf_area(ten_by_ten) == pytest.approx(
        _one_sided_leaf_area(twelve_by_twelve)
    )


def test_room_fit_lowers_count_instead_of_crowding_or_clipping() -> None:
    layout = fit_plant_grid_axis(
        10,
        count=50,
        local_half_extent_m=0.17,
    )

    assert layout.count < 50
    assert layout.count == 8


def test_radiance_export_is_deterministic() -> None:
    scene = generate_plant_scene(PlantGeometryConfig(seed=99))

    assert export_scene_to_radiance(scene) == export_scene_to_radiance(scene)


def test_radiance_export_contains_expected_material_and_surface_ids() -> None:
    rad_text = export_scene_to_radiance(generate_plant_scene())

    assert "void plastic plant_leaf_material" in rad_text
    assert "reflectance=0.220000 transmittance=0.080000" in rad_text
    assert "polygon plant_r000_c000_leaf_000_face_0000" in rad_text
    assert "# leaf_id=plant_r001_c001_leaf_011" in rad_text


def test_radiance_export_accepts_receiver_only_leaf_material_definition() -> None:
    scene = generate_plant_scene()

    rad_text = export_scene_to_radiance(
        scene,
        leaf_material_definition=(
            "void trans plant_leaf_material\n"
            "0\n"
            "0\n"
            "7 0.470000 0.470000 0.470000 0.000000 0.000000 0.510638 0.000000\n"
        ),
        optical_assumption_comment="# optical_assumptions mode=rex_source_weighted_trans",
    )

    assert "void trans plant_leaf_material" in rad_text
    assert "void plastic plant_leaf_material" not in rad_text
    assert "# optical_assumptions mode=rex_source_weighted_trans" in rad_text
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


def test_write_plant_artifacts_writes_expected_files_to_tmp_path(
    tmp_path: Path,
) -> None:
    paths = write_plant_artifacts(tmp_path)

    assert paths.directory == tmp_path
    assert {path.name for path in tmp_path.iterdir()} == {
        PLANTS_RAD_FILENAME,
        PLANTS_VIEWER_FILENAME,
        PLANTS_MANIFEST_FILENAME,
        PLANT_CONFIG_FILENAME,
    PLANT_ABSORPTION_SURFACES_FILENAME,
    }
    assert paths.radiance.read_text(encoding="utf-8").startswith(
        "# FSPM Phase 01 deterministic plant geometry\n"
    )


def test_write_plant_artifacts_is_deterministic(tmp_path: Path) -> None:
    config = PlantGeometryConfig(seed=123, plant_grid_rows=1, plant_grid_columns=1)
    first = tmp_path / "first"
    second = tmp_path / "second"

    write_plant_artifacts(first, config)
    write_plant_artifacts(second, config)

    for filename in (
        PLANTS_RAD_FILENAME,
        PLANTS_VIEWER_FILENAME,
        PLANTS_MANIFEST_FILENAME,
        PLANT_CONFIG_FILENAME,
    PLANT_ABSORPTION_SURFACES_FILENAME,
    ):
        assert (first / filename).read_bytes() == (second / filename).read_bytes()


def test_plant_manifest_includes_config_seed_version_and_provenance(
    tmp_path: Path,
) -> None:
    config = PlantGeometryConfig(seed=77, plant_grid_rows=1, plant_grid_columns=2)
    paths = write_plant_artifacts(tmp_path, config)

    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))

    assert manifest["schema"] == PLANT_ARTIFACT_SCHEMA
    assert manifest["schema_version"] == PLANT_ARTIFACT_SCHEMA_VERSION
    assert manifest["active_simulation_integration"] is False
    assert manifest["config"]["seed"] == 77
    assert manifest["config"]["plant_grid_columns"] == 2
    assert manifest["provenance"] == {
        "phase": "Phase 02",
        "generator": "deterministic_leafy_green_rosette",
        "source_module": "rad_rebuild.radiance.engine.plants.artifacts",
        "units": "meters",
    }
    assert manifest["counts"] == {"plants": 2, "leaves": 24}


def test_plant_artifact_json_files_are_parseable(tmp_path: Path) -> None:
    paths = write_plant_artifacts(tmp_path)

    config = json.loads(paths.config.read_text(encoding="utf-8"))
    viewer = json.loads(paths.viewer.read_text(encoding="utf-8"))
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))

    assert config["seed"] == 1
    assert viewer["schema"] == "rad_rebuild.fspm.plants.viewer.v1"
    assert manifest["artifact_filenames"]["viewer"] == PLANTS_VIEWER_FILENAME
    assert manifest["artifact_filenames"]["absorption_surfaces"] == PLANT_ABSORPTION_SURFACES_FILENAME


def test_plant_manifest_file_records_bind_written_files(tmp_path: Path) -> None:
    paths = write_plant_artifacts(tmp_path)
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    file_records = {record["path"]: record for record in manifest["files"]}

    assert set(file_records) == {
        PLANTS_RAD_FILENAME,
        PLANTS_VIEWER_FILENAME,
        PLANT_CONFIG_FILENAME,
    PLANT_ABSORPTION_SURFACES_FILENAME,
    }
    for filename, record in file_records.items():
        data = (tmp_path / filename).read_bytes()
        assert record["bytes"] == len(data)
        assert len(record["sha256"]) == 64
        assert "/" not in record["path"]


def test_write_plant_artifacts_does_not_write_outside_target_directory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    sibling = tmp_path / "sibling"
    sibling.mkdir()

    paths = write_plant_artifacts(target)

    assert {path.name for path in target.iterdir()} == {
        PLANT_CONFIG_FILENAME,
    PLANT_ABSORPTION_SURFACES_FILENAME,
        PLANTS_MANIFEST_FILENAME,
        PLANTS_RAD_FILENAME,
        PLANTS_VIEWER_FILENAME,
    }
    assert list(sibling.iterdir()) == []
    for path in (paths.radiance, paths.viewer, paths.manifest, paths.config):
        assert path.parent == target


def test_phase07_absorption_surface_registry_matches_radiance_surface_ids() -> None:
    scene = generate_plant_scene(
        PlantGeometryConfig(seed=5, plant_grid_rows=1, plant_grid_columns=1)
    )
    rad_text = export_scene_to_radiance(scene)
    surfaces = leaf_absorption_surfaces(scene)

    assert surfaces
    assert all(surface.surface_id in rad_text for surface in surfaces)
    assert all(surface.area_m2 > 0.0 for surface in surfaces)
    assert all(math.isfinite(surface.area_m2) for surface in surfaces)
    assert surfaces[0].plant_id == "plant_r000_c000"
    assert surfaces[0].leaf_id == "plant_r000_c000_leaf_000"


def test_phase07_absorption_registry_is_scaffold_only_and_not_yield_model() -> None:
    scene = generate_plant_scene(
        PlantGeometryConfig(seed=6, plant_grid_rows=1, plant_grid_columns=1)
    )

    registry = build_absorption_surface_registry(scene)

    assert registry["schema"] == PHOTON_ABSORPTION_SCAFFOLD_SCHEMA
    assert registry["status"] == "scaffold_only"
    assert registry["plant_count"] == 1
    assert registry["leaf_count"] == scene.config.leaf_count_per_plant
    assert registry["surface_count"] == len(leaf_absorption_surfaces(scene))
    assert registry["one_sided_leaf_area_m2"] > 0.0
    assert registry["outputs_do_not_predict"] == [
        "yield",
        "biomass",
        "growth",
        "crop_output",
    ]
    assert "umol/s" == registry["units"]["future_absorbed_photon_flux"]
