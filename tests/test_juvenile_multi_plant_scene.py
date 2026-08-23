from __future__ import annotations

import ast
from dataclasses import fields, replace
import hashlib
from itertools import islice
import json
import math
from pathlib import Path

import pytest

from fspm_optics.plants import (
    JuvenileScientificSceneError,
    NaturalFitPolicy,
    build_juvenile_natural_fit_scene,
    generate_rex_butterhead_plant,
    generate_rex_juvenile_preheading_plant,
    plan_natural_fit_layout,
    plan_natural_fit_layout_from_feet,
)
from fspm_optics.plants import multi_scene


@pytest.mark.parametrize(
    ("room_ft", "expected"),
    [
        (10, (64, 768, 122_880, 12_288, 24_576)),
        (20, (256, 3_072, 491_520, 49_152, 98_304)),
        (30, (529, 6_348, 1_015_680, 101_568, 203_136)),
    ],
)
def test_exact_scene_totals_are_derived_from_canonical_topology(
    room_ft: int,
    expected: tuple[int, int, int, int, int],
) -> None:
    layout = plan_natural_fit_layout_from_feet(room_ft, room_ft)
    scene = build_juvenile_natural_fit_scene(layout)

    assert (
        scene.counts.plant_count,
        scene.counts.leaf_count,
        scene.counts.face_count,
        scene.counts.patch_count,
        scene.counts.receiver_count,
    ) == expected
    assert scene.topology.leaf_count == len(scene.canonical_plant.leaves)
    assert scene.topology.face_count == scene.canonical_plant.face_count
    assert scene.topology.patch_count == scene.canonical_plant.patch_count
    assert scene.topology.receiver_count == len(scene.canonical_receivers)


def test_canonical_topology_is_generated_once_and_not_copied_per_plant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = multi_scene.generate_rex_juvenile_preheading_plant

    def counted_generator(config=None):
        nonlocal calls
        calls += 1
        return original(config)

    monkeypatch.setattr(
        multi_scene,
        "generate_rex_juvenile_preheading_plant",
        counted_generator,
    )
    scene = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout_from_feet(30, 30)
    )

    assert calls == 1
    assert len(scene.plants) == 529
    assert {field.name for field in fields(scene.plants[0])} == {
        "plant_index",
        "plant_id",
        "row_y",
        "column_x",
        "origin_m",
    }
    assert all(not hasattr(plant, "leaves") for plant in scene.plants)
    assert scene.canonical_plant is not None
    assert len(scene.canonical_receivers) == 384


def test_first_middle_and_last_face_vertices_are_exact_translations() -> None:
    scene = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout_from_feet(30, 30)
    )
    indices = (0, scene.counts.face_count // 2, scene.counts.face_count - 1)

    for global_index in indices:
        record = scene.face_at(global_index)
        plant_index, local_index = divmod(
            global_index, scene.topology.face_count
        )
        canonical = scene.canonical_plant.faces[local_index]
        origin = scene.plants[plant_index].origin_m
        expected = tuple(
            tuple(vertex[axis] + origin[axis] for axis in range(3))
            for vertex in canonical.vertices
        )
        assert record.vertices == expected
        assert record.centroid == tuple(
            canonical.centroid[axis] + origin[axis] for axis in range(3)
        )
        assert record.unit_normal == canonical.unit_normal
        assert record.vertex_indices == canonical.vertex_indices
        assert record.global_face_index == (
            plant_index * scene.topology.face_count + record.local_face_index
        )
        assert record.global_leaf_index == (
            plant_index * scene.topology.leaf_count + record.local_leaf_index
        )


def test_front_back_receivers_translate_without_changing_normals_or_side() -> None:
    scene = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout_from_feet(30, 30)
    )
    for global_index in (
        0,
        1,
        scene.topology.receiver_count,
        scene.counts.receiver_count - 2,
        scene.counts.receiver_count - 1,
    ):
        record = scene.receiver_at(global_index)
        plant_index, local_index = divmod(
            global_index, scene.topology.receiver_count
        )
        canonical = scene.canonical_receivers[local_index]
        origin = scene.plants[plant_index].origin_m
        assert record.point_m == tuple(
            canonical.point_m[axis] + origin[axis] for axis in range(3)
        )
        assert record.normal == canonical.normal
        assert record.side == canonical.side
        assert record.local_patch_index == local_index // 2
        assert record.global_patch_index == (
            plant_index * scene.topology.patch_count + local_index // 2
        )
        assert record.global_receiver_index == (
            plant_index * scene.topology.receiver_count + local_index
        )
        assert record.global_leaf_index == (
            plant_index * scene.topology.leaf_count + record.local_leaf_index
        )


def test_layout_order_and_all_expansion_orders_are_plant_major() -> None:
    layout = plan_natural_fit_layout_from_feet(10, 10)
    scene = build_juvenile_natural_fit_scene(layout)

    assert [plant.plant_id for plant in scene.plants] == [
        placement.plant_id for placement in layout.plants
    ]
    assert [
        (plant.row_y, plant.column_x) for plant in scene.plants
    ] == [
        (placement.grid_index.row_y, placement.grid_index.column_x)
        for placement in layout.plants
    ]
    leaves = tuple(islice(scene.iter_leaves(), scene.topology.leaf_count + 1))
    assert [item.global_leaf_index for item in leaves] == list(
        range(scene.topology.leaf_count + 1)
    )
    assert leaves[-2].plant_index == 0
    assert leaves[-1].plant_index == 1
    first_last_face = scene.face_at(scene.topology.face_count - 1)
    second_first_face = scene.face_at(scene.topology.face_count)
    assert first_last_face.global_face_index + 1 == second_first_face.global_face_index
    assert (first_last_face.plant_index, first_last_face.local_face_index) == (
        0,
        scene.topology.face_count - 1,
    )
    assert (second_first_face.plant_index, second_first_face.local_face_index) == (
        1,
        0,
    )
    patches = tuple(islice(scene.iter_patches(), scene.topology.patch_count + 1))
    assert patches[-2].global_patch_index + 1 == patches[-1].global_patch_index
    assert patches[-1].global_patch_index == (
        patches[-1].plant_index * scene.topology.patch_count
        + patches[-1].local_patch_index
    )
    canonical_patch = scene.canonical_plant.patches[patches[-1].local_patch_index]
    assert patches[-1].unit_normal == canonical_patch.unit_normal
    assert patches[-1].canonical_triangle_face_ids == (
        canonical_patch.triangle_face_ids
    )
    first_last_receiver = scene.receiver_at(scene.topology.receiver_count - 1)
    second_first_receiver = scene.receiver_at(scene.topology.receiver_count)
    assert first_last_receiver.global_receiver_index + 1 == (
        second_first_receiver.global_receiver_index
    )


def test_composite_ids_are_safe_stable_and_unique_for_largest_scene() -> None:
    first = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout_from_feet(30, 30)
    )
    second = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout_from_feet(30, 30)
    )

    leaf_ids = {item.leaf_id for item in first.iter_leaves()}
    patch_ids = {item.patch_id for item in first.iter_patches()}
    receiver_ids = {item.receiver_id for item in first.iter_receivers()}
    assert len(leaf_ids) == first.counts.leaf_count
    assert len(patch_ids) == first.counts.patch_count
    assert len(receiver_ids) == first.counts.receiver_count
    assert all("/" not in value and "\\" not in value for value in receiver_ids)
    assert first.face_at(first.counts.face_count - 1).face_id == (
        second.face_at(second.counts.face_count - 1).face_id
    )


def test_serialization_scene_hash_and_phase26e_provenance_are_stable() -> None:
    layout = plan_natural_fit_layout_from_feet(10, 10)
    first = build_juvenile_natural_fit_scene(layout)
    second = build_juvenile_natural_fit_scene(layout)

    assert first == second
    assert first.layout_plan_hash == layout.plan_hash
    assert first.scene_hash == second.scene_hash
    assert first.scene_id == second.scene_id
    assert first.to_json() == second.to_json()
    assert first.to_json().endswith("\n")
    payload = json.loads(first.to_json())
    assert payload["layout"]["plan_hash"] == layout.plan_hash
    assert "/home/" not in first.to_json()


def test_scene_hash_changes_with_layout_identity_and_profile_is_hash_relevant() -> None:
    baseline = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout(3.048, 3.048)
    )
    changed = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout(
            3.048,
            3.048,
            policy=NaturalFitPolicy(target_center_pitch_m=0.41),
        )
    )

    assert baseline.layout_plan_hash != changed.layout_plan_hash
    assert baseline.scene_hash != changed.scene_hash
    identity = baseline.identity_payload()
    canonical = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    changed_profile = identity | {"profile_id": "changed_profile"}
    changed_canonical = json.dumps(
        changed_profile,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == baseline.scene_hash
    assert hashlib.sha256(changed_canonical).hexdigest() != baseline.scene_hash


def test_invalid_profiles_positions_duplicates_and_indices_fail_closed() -> None:
    other_profile_layout = plan_natural_fit_layout(
        3.048,
        3.048,
        profile_id="other_profile",
    )
    with pytest.raises(JuvenileScientificSceneError, match="profile"):
        build_juvenile_natural_fit_scene(other_profile_layout)
    with pytest.raises(JuvenileScientificSceneError, match="accepted juvenile"):
        build_juvenile_natural_fit_scene(
            plan_natural_fit_layout_from_feet(10, 10),
            canonical_plant=generate_rex_butterhead_plant(),
        )

    scene = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout_from_feet(10, 10)
    )
    invalid_origin = replace(scene.plants[0], origin_m=(math.nan, 0.0, 0.0))
    with pytest.raises(JuvenileScientificSceneError, match="origin"):
        replace(scene, plants=(invalid_origin, *scene.plants[1:]))
    with pytest.raises(JuvenileScientificSceneError, match="unique"):
        replace(scene, plants=(scene.plants[0], scene.plants[0], *scene.plants[2:]))
    with pytest.raises(JuvenileScientificSceneError, match="aggregate"):
        replace(
            scene,
            counts=replace(scene.counts, face_count=scene.counts.face_count - 1),
        )
    duplicate_receiver = replace(
        scene.canonical_receivers[1],
        receiver_id=scene.canonical_receivers[0].receiver_id,
    )
    with pytest.raises(JuvenileScientificSceneError, match="receiver IDs"):
        replace(
            scene,
            canonical_receivers=(
                scene.canonical_receivers[0],
                duplicate_receiver,
                *scene.canonical_receivers[2:],
            ),
        )
    out_of_order = replace(scene.plants[1], row_y=0, column_x=0)
    with pytest.raises(JuvenileScientificSceneError, match="unique|Y-major"):
        replace(scene, plants=(scene.plants[0], out_of_order, *scene.plants[2:]))
    with pytest.raises(JuvenileScientificSceneError, match="global face index"):
        scene.face_at(scene.counts.face_count)
    with pytest.raises(JuvenileScientificSceneError, match="global receiver index"):
        scene.receiver_at(-1)
    with pytest.raises(TypeError):
        build_juvenile_natural_fit_scene(  # type: ignore[call-arg]
            plan_natural_fit_layout_from_feet(10, 10),
            source_id="forbidden",
        )


def test_expansion_is_lazy_and_single_plant_and_mature_contracts_are_unchanged() -> None:
    juvenile_before = generate_rex_juvenile_preheading_plant()
    mature_before = generate_rex_butterhead_plant()
    scene = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout_from_feet(30, 30),
        canonical_plant=juvenile_before,
    )

    face_iterator = scene.iter_faces()
    assert iter(face_iterator) is face_iterator
    assert next(face_iterator).global_face_index == 0
    assert not isinstance(face_iterator, tuple)
    assert scene.canonical_plant is juvenile_before
    assert generate_rex_juvenile_preheading_plant() == juvenile_before
    assert generate_rex_butterhead_plant() == mature_before


def test_scene_module_has_source_neutral_import_and_payload_boundaries() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "plants"
        / "multi_scene.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(
        forbidden in module
        for module in imported_modules
        for forbidden in (
            "fixtures",
            "sources",
            "spectral",
            "transport",
            "viewer",
            "radiance",
            "subprocess",
        )
    )
    assert not any(
        token in source
        for token in (
            "target_ppfd",
            "dimming",
            "post_trace",
            "quality_settings",
        )
    )
    payload = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout_from_feet(10, 10)
    ).to_payload()

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {
                nested for item in value.values() for nested in keys(item)
            }
        if isinstance(value, list):
            return {nested for item in value for nested in keys(item)}
        return set()

    assert not keys(payload) & {
        "fixture",
        "emitter",
        "source",
        "spectrum",
        "lighting_system",
        "browser",
        "radiance_options",
    }
