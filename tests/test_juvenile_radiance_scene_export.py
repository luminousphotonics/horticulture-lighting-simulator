from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
from itertools import islice
import json
import math
import struct

import pytest

from fspm_optics.plants import (
    DEFAULT_LEAF_MATERIAL_MODIFIER,
    JUVENILE_RADIANCE_EXPORTER_ID,
    REX_JUVENILE_PREHEADING_LOCAL_XY_BOUNDS_M,
    JuvenileRadianceExportError,
    JuvenileScientificSceneError,
    build_juvenile_natural_fit_scene,
    materialize_juvenile_radiance_export,
    plan_juvenile_radiance_export,
    plan_natural_fit_layout,
    plan_natural_fit_layout_from_feet,
)
from fspm_optics.plants.multi_scene import JuvenileScientificScene


def _one_plant_scene() -> JuvenileScientificScene:
    bounds = REX_JUVENILE_PREHEADING_LOCAL_XY_BOUNDS_M
    return build_juvenile_natural_fit_scene(
        plan_natural_fit_layout(bounds.span_x_m + 0.02, bounds.span_y_m + 0.02)
    )


@pytest.mark.parametrize(
    ("room_ft", "plants", "faces", "receivers"),
    [
        (10, 64, 122_880, 24_576),
        (20, 256, 491_520, 98_304),
        (30, 529, 1_015_680, 203_136),
    ],
)
def test_plan_uses_exact_dynamic_phase26g_counts(
    room_ft: int,
    plants: int,
    faces: int,
    receivers: int,
) -> None:
    scene = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout_from_feet(room_ft, room_ft)
    )
    plan = plan_juvenile_radiance_export(scene)

    assert scene.counts.plant_count == plants
    assert plan.geometry_count == faces
    assert plan.receiver_count == receivers
    assert plan.geometry_count == scene.counts.face_count
    assert plan.receiver_count == scene.counts.receiver_count


def test_plan_and_manifest_retain_exact_phase26e_and_phase26g_identity() -> None:
    layout = plan_natural_fit_layout_from_feet(10, 10)
    scene = build_juvenile_natural_fit_scene(layout)
    first = plan_juvenile_radiance_export(scene)
    second = plan_juvenile_radiance_export(scene)
    manifest = first.manifest_payload()

    assert first == second
    assert first.export_plan_hash == second.export_plan_hash
    assert first.manifest_bytes() == second.manifest_bytes()
    assert manifest["exporter_id"] == JUVENILE_RADIANCE_EXPORTER_ID
    assert manifest["scene"] == {
        "scene_id": scene.scene_id,
        "scene_hash": scene.scene_hash,
        "profile_id": scene.profile_id,
        "sampling_profile_id": scene.sampling_profile_id,
        "layout_schema_id": scene.layout_schema_id,
        "layout_policy_id": scene.layout_policy_id,
        "layout_plan_hash": layout.plan_hash,
    }
    assert manifest["export_plan_hash"] == first.export_plan_hash
    assert manifest["manifest"] == {
        "logical_name": "export-manifest.v2.json",
        "media_type": "application/json",
    }
    assert "/home/" not in first.manifest_bytes().decode("utf-8")


def test_geometry_stream_preserves_boundary_records_and_exact_winding() -> None:
    scene = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout_from_feet(10, 10)
    )
    plan = plan_juvenile_radiance_export(scene)
    wanted = {
        0,
        scene.topology.face_count - 1,
        scene.topology.face_count,
        scene.counts.face_count - 1,
    }
    records = {
        record.global_face_index: record
        for record in plan.iter_geometry_records()
        if record.global_face_index in wanted
    }

    assert set(records) == wanted
    for global_index, record in records.items():
        expanded = scene.face_at(global_index)
        assert record.global_face_index == global_index
        assert record.primitive_id == expanded.face_id
        assert record.canonical_face_id == expanded.canonical_face_id
        assert record.vertex_indices == expanded.vertex_indices
        assert record.vertices == expanded.vertices
        assert record.unit_normal == expanded.unit_normal
        assert record.area_m2 == expanded.area_m2
        edge_a = tuple(
            record.vertices[1][axis] - record.vertices[0][axis]
            for axis in range(3)
        )
        edge_b = tuple(
            record.vertices[2][axis] - record.vertices[0][axis]
            for axis in range(3)
        )
        cross = (
            edge_a[1] * edge_b[2] - edge_a[2] * edge_b[1],
            edge_a[2] * edge_b[0] - edge_a[0] * edge_b[2],
            edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0],
        )
        assert math.fsum(
            component * normal
            for component, normal in zip(cross, record.unit_normal, strict=True)
        ) > 0.0

    assert records[scene.topology.face_count - 1].plant_index == 0
    assert records[scene.topology.face_count].plant_index == 1
    primitive_ids = {record.primitive_id for record in plan.iter_geometry_records()}
    assert len(primitive_ids) == plan.geometry_count
    assert all("/" not in value and "\\" not in value for value in primitive_ids)


def test_geometry_bytes_use_symbolic_modifier_and_round_trip_safe_numbers() -> None:
    scene = _one_plant_scene()
    plan = plan_juvenile_radiance_export(scene)
    record = next(plan.iter_geometry_records())
    text = next(plan.iter_geometry_bytes()).decode("ascii")

    assert text.startswith(
        f"{DEFAULT_LEAF_MATERIAL_MODIFIER} polygon {record.primitive_id}\n0\n0\n9\n"
    )
    for vertex in record.vertices:
        assert " ".join(f"{value:.17g}" for value in vertex) in text
    assert "void plastic" not in text
    assert "trans " not in text
    assert "reflectance" not in text


def test_receiver_stream_preserves_rows_identity_sides_offsets_and_normals() -> None:
    scene = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout_from_feet(10, 10)
    )
    plan = plan_juvenile_radiance_export(scene)
    wanted = {
        0,
        1,
        scene.topology.receiver_count - 1,
        scene.topology.receiver_count,
        scene.counts.receiver_count - 1,
    }
    records = {
        record.row_index: record
        for record in plan.iter_receiver_records()
        if record.row_index in wanted
    }

    assert set(records) == wanted
    for row_index, record in records.items():
        expanded = scene.receiver_at(row_index)
        assert record.row_index == record.global_receiver_index == row_index
        assert record.receiver_id == expanded.receiver_id
        assert record.plant_id == expanded.plant_id
        assert record.canonical_leaf_id == expanded.canonical_leaf_id
        assert record.leaf_rank == expanded.local_leaf_index + 1
        assert record.leaf_layer == scene.canonical_plant.leaves[
            expanded.local_leaf_index
        ].leaf_layer
        assert record.canonical_patch_id == expanded.canonical_patch_id
        assert record.point_m == expanded.point_m
        assert record.normal == expanded.normal
        assert record.area_m2 == expanded.area_m2
        assert record.side == expanded.side
        assert record.normal_offset_m == expanded.normal_offset_m
    assert records[0].side == "front"
    assert records[1].side == "back"
    assert records[scene.topology.receiver_count - 1].plant_index == 0
    assert records[scene.topology.receiver_count].plant_index == 1


def test_receiver_native_rows_are_exact_six_column_global_order() -> None:
    plan = plan_juvenile_radiance_export(_one_plant_scene())
    records = plan.iter_receiver_records()
    rows = plan.iter_receiver_input_bytes()

    for expected_index, (record, row) in enumerate(
        islice(zip(records, rows, strict=True), 4)
    ):
        assert record.row_index == expected_index
        assert row.decode("ascii") == (
            " ".join(
                f"{value:.17g}" for value in (*record.point_m, *record.normal)
            )
            + "\n"
        )
        assert len(row.split()) == 6


def test_receiver_identity_is_path_free_and_maps_every_trace_row() -> None:
    scene = _one_plant_scene()
    plan = plan_juvenile_radiance_export(scene)
    payload = json.loads(b"".join(plan.iter_receiver_identity_bytes()))

    assert payload["scene_id"] == scene.scene_id
    assert payload["scene_hash"] == scene.scene_hash
    assert payload["layout_plan_hash"] == scene.layout_plan_hash
    assert payload["receiver_count"] == plan.receiver_count
    assert len(payload["receivers"]) == plan.receiver_count
    for index, row in enumerate(payload["receivers"]):
        assert row["row_index"] == row["global_receiver_index"] == index
        assert row["receiver"]["side"] == ("front" if index % 2 == 0 else "back")
        assert row["area_m2"] > 0.0
        assert row["normal_offset_m"] == scene.topology.receiver_normal_offset_m
    assert "/home/" not in json.dumps(payload)


def test_compact_index_reconstructs_legacy_row_identity_exactly(tmp_path) -> None:
    scene = _one_plant_scene()
    plan = plan_juvenile_radiance_export(scene)
    publication = materialize_juvenile_radiance_export(plan, tmp_path)
    compact = json.loads(publication.compact_receiver_index_path.read_bytes())
    legacy = json.loads(b"".join(plan.iter_receiver_identity_bytes()))

    assert legacy["schema_version"] == 1
    assert compact["ordering"]["trace_row_mapping"] == (
        "trace row i equals global receiver index i"
    )
    for row in legacy["receivers"]:
        global_index = row["global_receiver_index"]
        plant_index, local_receiver_index = divmod(
            global_index,
            scene.topology.receiver_count,
        )
        canonical_receiver = scene.canonical_receivers[local_receiver_index]
        origin = scene.plants[plant_index].origin_m
        assert row["plant"]["index"] == plant_index
        assert row["receiver"]["local_index"] == local_receiver_index
        assert row["patch"]["global_index"] == (
            plant_index * scene.topology.patch_count
            + local_receiver_index // 2
        )
        assert row["receiver"]["side"] == (
            "front" if local_receiver_index % 2 == 0 else "back"
        )
        assert row["point_m"] == [
            canonical_receiver.point_m[axis] + origin[axis]
            for axis in range(3)
        ]
        assert row["normal"] == list(canonical_receiver.normal)
        assert row["area_m2"] == canonical_receiver.area_m2
        assert row["normal_offset_m"] == canonical_receiver.normal_offset_m
    origins = publication.plant_origins_path.read_bytes()
    assert len(origins) == scene.counts.plant_count * 24
    assert struct.unpack("<3d", origins[:24]) == scene.plants[0].origin_m


def test_materialization_streams_integrity_metadata_and_is_deterministic(
    tmp_path,
) -> None:
    plan = plan_juvenile_radiance_export(_one_plant_scene())
    first = materialize_juvenile_radiance_export(plan, tmp_path / "first")
    second = materialize_juvenile_radiance_export(plan, tmp_path / "second")
    first_by_role = {artifact.role: artifact for artifact in first.artifacts}
    second_by_role = {artifact.role: artifact for artifact in second.artifacts}

    assert first_by_role == second_by_role
    for role, path in (
        ("plant_geometry", first.geometry_path),
        ("receiver_input", first.receiver_input_path),
        ("plant_origins", first.plant_origins_path),
        ("compact_receiver_index", first.compact_receiver_index_path),
    ):
        data = path.read_bytes()
        assert first_by_role[role].byte_length == len(data)
        assert first_by_role[role].sha256 == hashlib.sha256(data).hexdigest()
    manifest = json.loads(first.manifest_path.read_bytes())
    assert manifest == plan.manifest_payload(first.artifacts)
    assert first.manifest_artifact.byte_length == first.manifest_path.stat().st_size
    assert first.manifest_artifact.sha256 == hashlib.sha256(
        first.manifest_path.read_bytes()
    ).hexdigest()
    compact = json.loads(first.compact_receiver_index_path.read_bytes())
    assert compact["counts"] == plan.scene.counts.to_payload()
    assert compact["reconstruction"]["approximate_spatial_matching"] is False
    assert compact["plant_origins"]["row_count"] == plan.scene.counts.plant_count
    assert "receivers" not in compact
    assert first.compact_receiver_index_path.stat().st_size < 16_384


def test_export_plan_and_iterators_are_immutable_and_lazy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene = _one_plant_scene()
    calls = 0
    original = JuvenileScientificScene.iter_faces

    def counted(self):
        nonlocal calls
        for face in original(self):
            calls += 1
            yield face

    monkeypatch.setattr(JuvenileScientificScene, "iter_faces", counted)
    plan = plan_juvenile_radiance_export(scene)
    assert calls == 0
    iterator = plan.iter_geometry_records()
    assert iter(iterator) is iterator
    assert next(iterator).global_face_index == 0
    assert calls == 1
    with pytest.raises(FrozenInstanceError):
        plan.leaf_material_modifier = "changed"  # type: ignore[misc]


def test_invalid_scene_modifier_geometry_and_counts_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene = _one_plant_scene()
    with pytest.raises(JuvenileRadianceExportError, match="scene"):
        plan_juvenile_radiance_export(object())  # type: ignore[arg-type]
    with pytest.raises(JuvenileRadianceExportError, match="Radiance-safe"):
        plan_juvenile_radiance_export(scene, leaf_material_modifier="bad/path")
    with pytest.raises(JuvenileScientificSceneError, match="aggregate"):
        replace(
            scene,
            counts=replace(
                scene.counts,
                receiver_count=scene.counts.receiver_count - 1,
            ),
        )

    original = JuvenileScientificScene.iter_faces
    first = next(original(scene))
    invalid_records = (
        (replace(first, face_id="bad/path"), "Radiance-safe"),
        (
            replace(
                first,
                vertices=((math.nan, 0.0, 0.0), *first.vertices[1:]),
            ),
            "non-finite",
        ),
        (
            replace(
                first,
                vertices=(
                    first.vertices[0],
                    first.vertices[2],
                    first.vertices[1],
                ),
            ),
            "winding",
        ),
        (replace(first, area_m2=0.0), "degenerate"),
    )
    for invalid_record, message in invalid_records:
        def invalid_faces(self, record=invalid_record):
            yield record
            yield from islice(original(self), 1, None)

        monkeypatch.setattr(JuvenileScientificScene, "iter_faces", invalid_faces)
        with pytest.raises(JuvenileRadianceExportError, match=message):
            next(plan_juvenile_radiance_export(scene).iter_geometry_records())


def test_plan_payload_has_no_lighting_or_transport_fields() -> None:
    payload = plan_juvenile_radiance_export(_one_plant_scene()).manifest_payload()

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
        "lighting_system",
        "spectrum",
        "spd",
        "target_ppfd",
        "dimming",
        "quality_settings",
        "radiance_options",
        "command",
        "octree",
        "trace_result",
    }
