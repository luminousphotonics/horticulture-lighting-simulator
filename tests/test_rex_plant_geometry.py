from __future__ import annotations

from collections import Counter
import math

import pytest

from fspm_optics.plants import (
    RexPlantConfig,
    generate_rex_butterhead_plant,
    validate_plant_mesh,
)


def test_default_mature_rex_geometry_is_deterministic_and_seed_sensitive() -> None:
    config = RexPlantConfig(seed=19)
    first = generate_rex_butterhead_plant(config)
    second = generate_rex_butterhead_plant(config)
    changed = generate_rex_butterhead_plant(config, seed=20)

    assert first == second
    assert first != changed
    assert first.plant_id == "rex_plant_000"
    assert len(first.leaves) == 32
    assert first.config.leaf_patch_grid == (4, 4)
    assert first.patch_count == 512
    assert first.receiver_count == 1024
    assert [leaf.leaf_id for leaf in first.leaves] == [
        leaf.leaf_id for leaf in changed.leaves
    ]
    assert first.leaves[0].vertices != changed.leaves[0].vertices


@pytest.mark.parametrize("leaf_count", [24, 32, 40])
def test_leaf_count_range_and_layered_rosette(leaf_count: int) -> None:
    plant = generate_rex_butterhead_plant(RexPlantConfig(leaf_count=leaf_count))
    layers = Counter(leaf.leaf_layer for leaf in plant.leaves)

    assert len(plant.leaves) == leaf_count
    assert set(layers) == {"outer", "mid", "inner"}
    assert 0.35 <= layers["outer"] / leaf_count <= 0.45
    assert 0.35 <= layers["mid"] / leaf_count <= 0.45
    assert 0.15 <= layers["inner"] / leaf_count <= 0.25


def test_default_rex_envelope_and_broad_curved_leaf_character() -> None:
    config = RexPlantConfig()
    plant = generate_rex_butterhead_plant(config)
    report = validate_plant_mesh(plant)

    assert report.projected_diameter_m == pytest.approx(
        config.projected_diameter_m,
        abs=1e-12,
    )
    assert report.height_m == pytest.approx(config.plant_height_m, abs=1e-12)
    assert report.leaf_count == 32
    assert report.patch_count == 512
    assert report.receiver_count == 1024
    assert report.leaf_patch_grid == (4, 4)
    assert report.total_leaf_area_m2 > 0.0
    assert 0.25 <= report.projected_diameter_m <= 0.32
    assert 0.12 <= report.height_m <= 0.20
    assert all(leaf.width_m / leaf.length_m >= 0.65 for leaf in plant.leaves)
    for leaf in plant.leaves:
        rounded_surface_normals = {
            tuple(round(component, 6) for component in face.unit_normal)
            for face in leaf.faces
        }
        assert len(rounded_surface_normals) > 3


def test_side_profile_uses_overlapping_low_mid_and_upright_heart_layers() -> None:
    plant = generate_rex_butterhead_plant(RexPlantConfig(seed=1))
    by_layer = {
        layer: [leaf for leaf in plant.leaves if leaf.leaf_layer == layer]
        for layer in ("outer", "mid", "inner")
    }
    mean_elevation = {
        layer: math.degrees(
            math.fsum(leaf.elevation_rad for leaf in leaves) / len(leaves)
        )
        for layer, leaves in by_layer.items()
    }
    mean_reach = {
        layer: math.fsum(
            max(math.hypot(vertex[0], vertex[1]) for vertex in leaf.vertices)
            for leaf in leaves
        )
        / len(leaves)
        for layer, leaves in by_layer.items()
    }

    assert mean_elevation["outer"] < 20.0
    assert 25.0 < mean_elevation["mid"] < 55.0
    assert mean_elevation["inner"] > 65.0
    assert mean_reach["outer"] > mean_reach["mid"] > mean_reach["inner"]
    assert max(
        leaf.world_transform.origin_m[2] for leaf in by_layer["outer"]
    ) > min(leaf.world_transform.origin_m[2] for leaf in by_layer["mid"])
    assert max(
        leaf.world_transform.origin_m[2] for leaf in by_layer["mid"]
    ) > min(leaf.world_transform.origin_m[2] for leaf in by_layer["inner"])
    assert all(
        leaf.vertices[-1][2] < max(vertex[2] for vertex in leaf.vertices)
        for leaf in by_layer["outer"]
    )


def test_stable_leaf_and_face_records_satisfy_scientific_invariants() -> None:
    plant = generate_rex_butterhead_plant(RexPlantConfig(seed=7))
    face_ids: list[str] = []

    for expected_rank, leaf in enumerate(plant.leaves, start=1):
        assert leaf.plant_id == plant.plant_id
        assert leaf.leaf_rank == expected_rank
        assert leaf.leaf_id == f"{plant.plant_id}_leaf_{expected_rank:03d}"
        assert leaf.leaf_layer in {"outer", "mid", "inner"}
        assert all(
            math.isfinite(value)
            for vertex in leaf.vertices
            for value in vertex
        )
        for expected_face_index, face in enumerate(leaf.faces):
            assert face.face_index == expected_face_index
            assert face.face_id == (
                f"{leaf.leaf_id}_face_{expected_face_index:04d}"
            )
            assert face.leaf_id == leaf.leaf_id
            assert face.leaf_rank == leaf.leaf_rank
            assert face.leaf_layer == leaf.leaf_layer
            assert face.vertices == tuple(
                leaf.vertices[index] for index in face.vertex_indices
            )
            assert face.area_m2 > 0.0 and math.isfinite(face.area_m2)
            assert all(math.isfinite(value) for value in face.centroid)
            magnitude = math.sqrt(
                sum(component * component for component in face.unit_normal)
            )
            assert magnitude == pytest.approx(1.0, abs=1e-12)
            face_ids.append(face.face_id)
    assert len(face_ids) == plant.face_count
    assert len(face_ids) == len(set(face_ids))

    patch_ids: list[str] = []
    for leaf in plant.leaves:
        assert len(leaf.patches) == 16
        assert math.fsum(patch.area_m2 for patch in leaf.patches) == pytest.approx(
            math.fsum(face.area_m2 for face in leaf.faces),
            rel=1e-12,
        )
        for patch in leaf.patches:
            assert patch.area_m2 > 0.0 and math.isfinite(patch.area_m2)
            assert patch.face_id == patch.patch_id
            magnitude = math.sqrt(
                sum(component * component for component in patch.unit_normal)
            )
            assert magnitude == pytest.approx(1.0, abs=1e-12)
            patch_ids.append(patch.patch_id)
    assert len(patch_ids) == 512
    assert len(patch_ids) == len(set(patch_ids))


@pytest.mark.parametrize(("patch_u", "patch_v"), [(2, 3), (5, 4), (6, 6)])
def test_explicit_patch_grid_changes_patch_and_receiver_counts(
    patch_u: int,
    patch_v: int,
) -> None:
    config = RexPlantConfig(leaf_patch_u=patch_u, leaf_patch_v=patch_v)
    plant = generate_rex_butterhead_plant(config)

    assert config.leaf_patch_grid == (patch_u, patch_v)
    assert plant.patch_count == config.leaf_count * patch_u * patch_v
    assert plant.receiver_count == 2 * plant.patch_count


def test_rex_public_config_exposes_explicit_patch_grid_without_density_modes() -> None:
    fields = set(RexPlantConfig.__dataclass_fields__)
    assert {"leaf_patch_u", "leaf_patch_v"} <= fields
    assert all("density" not in name and "mode" not in name for name in fields)


@pytest.mark.parametrize("leaf_count", [23, 41])
def test_rex_config_rejects_leaf_counts_outside_scientific_range(
    leaf_count: int,
) -> None:
    with pytest.raises(ValueError, match="24..40"):
        RexPlantConfig(leaf_count=leaf_count)
