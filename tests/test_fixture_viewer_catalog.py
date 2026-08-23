from __future__ import annotations

from dataclasses import replace
import hashlib
from importlib import resources
import json
import math
import struct

import pytest

from fspm_optics.application.proposed import _layout_identity
from fspm_optics.fixtures.conventional_led.layout import (
    plan_conventional_layout_from_feet,
)
from fspm_optics.fixtures.hps.layout import (
    plan_hps_layout,
    plan_hps_layout_from_feet,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.viewer.fixtures import (
    ANCHOR_TOLERANCE_M,
    ASSET_REGISTRY,
    CATALOG_SCHEMA_ID,
    MATRIX_STRIDE_BYTES,
    _column_major,
    _direct_matrix,
    _matrix3_determinant,
    _normalized_spatial_determinant,
    _proposed_display_fixture_type,
    _proposed_matrix,
    _standalone_proposed_matrix,
    _transform,
    _validate_affine_matrix,
    _validate_anchor_residuals,
    build_fixture_publication,
    hps_placement_contract_sha256,
    resolve_fixture_transport_placements,
    validate_fixture_publication,
    validate_glb_content,
    validate_hps_publication_transform,
    validate_standalone_proposed_transform,
)

RUN_ID = "b" * 32

EXPECTED_REGISTRY = {
    "proposed-centerpiece-v1": (
        "proposed", "centerpiece", "fixtures/proposed/centerpiece.glb", 362744,
        "76daa0290eafb3947b30489d304287b25dab998f847ba5d5a8163775cb38c1ba",
    ),
    "proposed-linear2-v1": (
        "proposed", "linear2", "fixtures/proposed/linear2.glb", 165832,
        "814de19659cff0130b508f197a2194383748eee191f10927524229aabd5c288d",
    ),
    "proposed-linear3-v1": (
        "proposed", "linear3", "fixtures/proposed/linear3.glb", 233288,
        "e9cb018d9ce5bb6babe59044d88639c6620d6018eb22e71f6fc404c250145d93",
    ),
    "proposed-corner3-v1": (
        "proposed", "corner3", "fixtures/proposed/corner3.glb", 238648,
        "e30f98457b1af1725843ed0c42edfc9d22516445c602f4edcc8b8ed116c7d243",
    ),
    "proposed-linear4-v1": (
        "proposed", "linear4", "fixtures/proposed/linear4.glb", 353128,
        "1f77749ae1c3dda08cf8a521734f9aea5e54b64df68b963341bdca5407ca6809",
    ),
    "proposed-l-v1": (
        "proposed", "L", "fixtures/proposed/l.glb", 363232,
        "ea1c9ea94aa08c30a655458c0a01e74a3687e2e4333203d10961520d6634730e",
    ),
    "proposed-reverse-l-v1": (
        "proposed", "reverse_L", "fixtures/proposed/reverse_l.glb", 363064,
        "eb87a439e99ae95ac5ad73ea96ba4de9bbcd03609e45355d4e92a8210034badd",
    ),
    "proposed-led-module-v1": (
        "proposed", "standalone_module", "fixtures/proposed/led_module.glb", 88540,
        "f910a7fc512635bcb3a48d7b546175ef13623135836a76eeb625b2ca12cd5445",
    ),
    "conventional-led-8-bar-v1": (
        "conventional", "conventional_led_8_bar",
        "fixtures/conventional/conventional_led_8_bar.glb", 109392,
        "0d640d8e20bfdc213d3722dc44652979366b7c19ef153fff10372fe66051af53",
    ),
    "hps-housing-v3": (
        "hps", "hps_1000w_fixture",
        "fixtures/hps/hps.glb", 353160,
        "083df1475e84e9552d5ec548fc34669092b5ba4284fcf33ebf7dbb2f26439d7d",
    ),
}


def test_publication_resolver_uses_exact_ordered_l_topology_not_scientific_label() -> None:
    forward = (
        (-5.0, -1.0),
        (-6.0, 0.0),
        (-5.0, 1.0),
        (-4.0, 2.0),
    )
    indices = [0, 1, 2, 3]
    for points, expected_display_type in (
        (forward, "L"),
        (tuple(reversed(forward)), "reverse_L"),
    ):
        modules = {
            index: {"x_m": point[0], "y_m": point[1]}
            for index, point in enumerate(points)
        }
        for scientific_type, connector_pairs in (
            ("L", ((1, 2), (2, 3), (0, 1))),
            ("reverse_L", ((0, 1), (1, 2), (2, 3))),
        ):
            connectors = [
                {"start_module_index": start, "end_module_index": end}
                for start, end in connector_pairs
            ]
            assert _proposed_display_fixture_type(
                scientific_type,
                indices,
                connectors,
                modules,
            ) == expected_display_type


def test_registry_has_exact_assets_paths_sizes_hashes_systems_and_contracts() -> None:
    assert len(ASSET_REGISTRY) == 10
    for asset in ASSET_REGISTRY:
        expected = EXPECTED_REGISTRY[asset.asset_id]
        assert (
            asset.system_id, asset.fixture_type, asset.resource_path,
            asset.byte_size, asset.sha256,
        ) == expected
        assert asset.meters_per_asset_unit == 0.001
        assert asset.local_up_axis == "positive_y_after_authored_glb_scene_transform"
        assert asset.approved_extensions == ("KHR_mesh_quantization",)
    hps = next(item for item in ASSET_REGISTRY if item.system_id == "hps")
    conventional_led = next(item for item in ASSET_REGISTRY if item.system_id == "conventional")
    corner3 = next(
        item for item in ASSET_REGISTRY
        if item.asset_id == "proposed-corner3-v1"
    )
    assert conventional_led.dimension_correction_scale_xyz == pytest.approx(
        (1.0000000041789914, 1.1552917718844276, 0.999998375729409)
    )
    conventional_glb = resources.files("fspm_optics").joinpath(
        "resources", "viewer", *conventional_led.resource_path.split("/")
    ).read_bytes()
    json_length, json_type = struct.unpack_from("<II", conventional_glb, 12)
    assert json_type == 0x4E4F534A
    json_chunk = conventional_glb[20 : 20 + json_length]
    bin_header_offset = 20 + json_length
    bin_length, bin_type = struct.unpack_from(
        "<II", conventional_glb, bin_header_offset
    )
    assert bin_type == 0x004E4942
    bin_chunk = conventional_glb[
        bin_header_offset + 8 : bin_header_offset + 8 + bin_length
    ]
    document = json.loads(json_chunk.rstrip(b" \t\r\n\0"))
    semantic = {
        key: document.get(key)
        for key in (
            "scene",
            "scenes",
            "nodes",
            "meshes",
            "accessors",
            "bufferViews",
            "buffers",
            "materials",
            "textures",
            "images",
            "samplers",
        )
    }
    semantic_fingerprint = hashlib.sha256(
        json.dumps(
            semantic,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert hashlib.sha256(conventional_glb).hexdigest() == (
        "0d640d8e20bfdc213d3722dc44652979366b7c19ef153fff10372fe66051af53"
    )
    assert hashlib.sha256(bin_chunk).hexdigest() == (
        "0f9e313c0e80f155e4a7d6923ecf0b56ce8678f65eb0873357c21f1a705c2801"
    )
    assert semantic_fingerprint == (
        "142b968af06b0b16905472c558eda3fad99011744ec3b6cc08714c6408a21771"
    )
    json_text = json_chunk.decode("utf-8").lower()
    assert all(
        value not in json_text
        for value in (
            "qu" + "be",
            "spy" + "dr",
            "bell" + "ing",
            "gpm" + "-3000",
            "qb" + "-fsg",
            "blc" + "2107022e",
        )
    )
    assert hps.placement_plane_local_y_mm == -248.92
    assert hps.placement_correction_local_x_mm == 0.0
    assert hps.placement_correction_local_y_mm == 248.92
    assert hps.placement_correction_local_z_mm == 0.0
    assert hps.dimension_correction_scale_xyz == (1.0, 1.0, 1.0)
    assert corner3.placement_plane_local_y_mm == pytest.approx(
        27.396988109
    )
    assert corner3.anchors_m == (
        (0.000014092, -0.008038842, 0.000010835),
        (0.51399358, -0.008038842, 0.000010835),
        (0.51399358, -0.008038842, -0.514010832),
    )


def test_packaged_assets_match_registry_and_reject_corruption() -> None:
    root = resources.files("fspm_optics").joinpath("resources", "viewer")
    for asset in ASSET_REGISTRY:
        data = root.joinpath(*asset.resource_path.split("/")).read_bytes()
        assert len(data) == asset.byte_size
        assert hashlib.sha256(data).hexdigest() == asset.sha256
        assert validate_glb_content(data, asset)["asset"]["version"] == "2.0"
        corrupted = data[:-1] + bytes((data[-1] ^ 1,))
        assert hashlib.sha256(corrupted).hexdigest() != asset.sha256


def test_proposed_l_family_and_linear_assets_share_authored_pbr_material_contract() -> None:
    root = resources.files("fspm_optics").joinpath("resources", "viewer")
    asset_ids = {
        "proposed-l-v1",
        "proposed-linear3-v1",
        "proposed-corner3-v1",
        "proposed-linear4-v1",
        "proposed-reverse-l-v1",
    }
    base_color_factor_sets: set[tuple[tuple[float, ...], ...]] = set()
    inspected_asset_ids: set[str] = set()
    for asset in ASSET_REGISTRY:
        if asset.asset_id not in asset_ids:
            continue
        inspected_asset_ids.add(asset.asset_id)
        document = validate_glb_content(
            root.joinpath(*asset.resource_path.split("/")).read_bytes(), asset
        )
        factors: set[tuple[float, ...]] = set()
        for material in document["materials"]:
            pbr = material.get("pbrMetallicRoughness", {})
            factors.add(tuple(pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])))
            assert pbr.get("metallicFactor", 1.0) == 1.0
            assert pbr.get("roughnessFactor", 1.0) == 1.0
            assert "baseColorTexture" not in pbr
            assert "metallicRoughnessTexture" not in pbr
            assert material.get("alphaMode", "OPAQUE") == "OPAQUE"
            assert material["doubleSided"] is True
            assert "normalTexture" not in material
        base_color_factor_sets.add(tuple(sorted(factors)))
    assert inspected_asset_ids == asset_ids
    assert len(base_color_factor_sets) == 1


def test_glb_validator_rejects_external_uris_animations_lights_and_extensions() -> None:
    asset = ASSET_REGISTRY[0]
    for payload, message in (
        ({"buffers": [{"byteLength": 4, "uri": "https://invalid"}]}, "external URIs"),
        ({"buffers": [{"byteLength": 4}], "animations": [{}]}, "animations"),
        ({"buffers": [{"byteLength": 4}], "cameras": [{}]}, "cameras"),
        (
            {"buffers": [{"byteLength": 4}], "extensions": {"KHR_lights_punctual": {}}},
            "lights",
        ),
        ({"buffers": [{"byteLength": 4}], "extensionsUsed": ["VENDOR_unknown"]}, "extension"),
        (
            {"buffers": [{"byteLength": 4}], "extensions": {"VENDOR_unknown": {}}},
            "extension",
        ),
    ):
        payload |= {
            "asset": {"version": "2.0"},
            "materials": [{"doubleSided": True}],
        }
        data = _minimal_glb(payload)
        with pytest.raises(ValueError, match=message):
            validate_glb_content(data, replace(asset, byte_size=len(data)))


@pytest.mark.parametrize(
    ("system_id", "layout"),
    (
        ("proposed", _layout_identity(generate_proposed_led_layout(12.5, 7.75))),
        ("conventional", plan_conventional_layout_from_feet(12.5, 7.75).to_payload()),
        ("hps", plan_hps_layout_from_feet(12.5, 7.75).to_payload()),
    ),
)
def test_run_catalogs_bind_identity_and_publish_only_used_assets(
    system_id: str, layout: dict[str, object],
) -> None:
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id=system_id,
        requested_length_ft=12.5,
        requested_width_ft=7.75,
        layout_identity=layout,
    )
    catalog = json.loads(publication.catalog.data)
    assert catalog["schema_id"] == CATALOG_SCHEMA_ID
    assert catalog["run"] == {"run_id": RUN_ID, "system_id": system_id}
    assert catalog["fixture_count"] == len(catalog["fixture_plan"]["fixtures"])
    declared = {
        f"fixtures/{group['asset']['filename']}" for group in catalog["asset_groups"]
    } | {
        f"fixtures/{group['instance_matrices']['filename']}"
        for group in catalog["asset_groups"]
    }
    assert declared == {item.relative_path for item in publication.files}
    assert all(
        group["ordered_fixture_ids"]
        == [
            record["fixture_id"]
            for record in catalog["fixture_plan"]["fixtures"]
            if record["display_asset_id"] == group["display_asset_id"]
        ]
        for group in catalog["asset_groups"]
    )
    validate_fixture_publication(
        publication.catalog.data,
        {item.relative_path: item.data for item in publication.files},
        expected_run_id=RUN_ID,
        expected_system_id=system_id,
        expected_requested_length_ft=12.5,
        expected_requested_width_ft=7.75,
        expected_layout_identity=layout,
    )


@pytest.mark.parametrize(
    ("system_id", "layout"),
    (
        ("proposed", _layout_identity(generate_proposed_led_layout(10, 20))),
        ("conventional", plan_conventional_layout_from_feet(10, 20).to_payload()),
        ("hps", plan_hps_layout_from_feet(10, 20).to_payload()),
    ),
)
def test_portrait_viewer_fixtures_use_the_aligned_simulation_room(
    system_id: str,
    layout: dict[str, object],
) -> None:
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id=system_id,
        requested_length_ft=10,
        requested_width_ft=20,
        layout_identity=layout,
    )
    records = json.loads(publication.catalog.data)["fixture_plan"]["fixtures"]

    assert records
    assert all(
        -3.048 <= record["scientific_translation_m"]["x"] <= 3.048
        and -1.524 <= record["scientific_translation_m"]["y"] <= 1.524
        for record in records
    )
    validate_fixture_publication(
        publication.catalog.data,
        {item.relative_path: item.data for item in publication.files},
        expected_run_id=RUN_ID,
        expected_system_id=system_id,
        expected_requested_length_ft=10,
        expected_requested_width_ft=20,
        expected_layout_identity=layout,
    )


@pytest.mark.parametrize(("length_ft", "width_ft"), ((10.0, 10.0), (12.0, 10.0)))
def test_proposed_catalog_selects_corner3_and_preserves_straight_linear3(
    length_ft: float, width_ft: float,
) -> None:
    layout = _layout_identity(
        generate_proposed_led_layout(
            length_ft,
            width_ft,
            proposed_layout_mode="legacy",
        )
    )
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id="proposed",
        requested_length_ft=length_ft,
        requested_width_ft=width_ft,
        layout_identity=layout,
    )
    repeated = build_fixture_publication(
        run_id=RUN_ID,
        system_id="proposed",
        requested_length_ft=length_ft,
        requested_width_ft=width_ft,
        layout_identity=layout,
    )
    catalog = json.loads(publication.catalog.data)
    records = catalog["fixture_plan"]["fixtures"]
    assert catalog["fixture_plan"]["display_classification_policy"] == (
        "proposed_member_connector_topology_v2"
    )
    corner = [item for item in records if item["display_fixture_type"] == "corner3"]
    straight = [item for item in records if item["display_fixture_type"] == "linear3"]

    assert corner and straight
    assert all(item["fixture_type"] == "linear3" for item in corner + straight)
    assert {item["display_asset_id"] for item in corner} == {"proposed-corner3-v1"}
    assert {item["display_asset_id"] for item in straight} == {"proposed-linear3-v1"}
    assert all(
        item["anchor_max_residual_m"] <= ANCHOR_TOLERANCE_M
        for item in corner + straight
    )
    files = {item.relative_path: item.data for item in publication.files}
    for asset_id, selected in (
        ("proposed-corner3-v1", corner),
        ("proposed-linear3-v1", straight),
    ):
        group = next(
            item for item in catalog["asset_groups"]
            if item["display_asset_id"] == asset_id
        )
        matrices = group["instance_matrices"]
        matrix_bytes = files[f"fixtures/{matrices['filename']}"]
        assert group["ordered_fixture_ids"] == [
            item["fixture_id"] for item in selected
        ]
        assert len(matrix_bytes) == len(selected) * MATRIX_STRIDE_BYTES
        assert matrices["sha256"] == hashlib.sha256(matrix_bytes).hexdigest()
    assert publication.catalog.data == repeated.catalog.data
    assert publication.files == repeated.files


@pytest.mark.parametrize(
    ("length_ft", "width_ft", "expected_asset_ids"),
    (
        (
            10.0,
            10.0,
            {
                "proposed-centerpiece-v1",
                "proposed-linear3-v1",
                "proposed-corner3-v1",
                "proposed-linear4-v1",
                "proposed-reverse-l-v1",
            },
        ),
        (
            10.0,
            20.0,
            {
                "proposed-linear2-v1",
                "proposed-linear3-v1",
                "proposed-linear4-v1",
                "proposed-l-v1",
                "proposed-reverse-l-v1",
            },
        ),
    ),
)
def test_proposed_ten_foot_layouts_publish_exact_display_asset_inventories(
    length_ft: float,
    width_ft: float,
    expected_asset_ids: set[str],
) -> None:
    layout = _layout_identity(
        generate_proposed_led_layout(
            length_ft,
            width_ft,
            proposed_layout_mode="legacy",
        )
    )
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id="proposed",
        requested_length_ft=length_ft,
        requested_width_ft=width_ft,
        layout_identity=layout,
    )
    catalog = json.loads(publication.catalog.data)
    assert {
        group["display_asset_id"] for group in catalog["asset_groups"]
    } == expected_asset_ids
    files = {item.relative_path: item.data for item in publication.files}
    assert all(
        record["anchor_max_residual_m"] <= ANCHOR_TOLERANCE_M
        for record in catalog["fixture_plan"]["fixtures"]
    )
    for group in catalog["asset_groups"]:
        matrix_record = group["instance_matrices"]
        matrix_bytes = files[f"fixtures/{matrix_record['filename']}"]
        for offset in range(0, len(matrix_bytes), MATRIX_STRIDE_BYTES):
            column_major = struct.unpack_from("<16f", matrix_bytes, offset)
            row_major = tuple(
                column_major[column * 4 + row]
                for row in range(4)
                for column in range(4)
            )
            _validate_affine_matrix(row_major)
            assert abs(_normalized_spatial_determinant(row_major)) == pytest.approx(
                1.0, abs=1.0e-5
            )


@pytest.mark.parametrize(("length_ft", "width_ft"), ((10.0, 10.0), (10.0, 20.0)))
def test_default_standalone_publication_copies_one_module_asset_for_every_source(
    length_ft: float,
    width_ft: float,
) -> None:
    layout = _layout_identity(generate_proposed_led_layout(length_ft, width_ft))
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id="proposed",
        requested_length_ft=length_ft,
        requested_width_ft=width_ft,
        layout_identity=layout,
    )
    catalog = json.loads(publication.catalog.data)
    assert {
        group["display_asset_id"] for group in catalog["asset_groups"]
    } == {"proposed-led-module-v1"}
    assert catalog["fixture_count"] == len(layout["modules"])
    assert catalog["fixture_count"] == len(layout["fixtures"])
    assert all(
        fixture["member_module_indices"] == [module["module_index"]]
        for fixture, module in zip(
            catalog["fixture_plan"]["fixtures"],
            layout["modules"],
            strict=True,
        )
    )
    published_glbs = {
        item.relative_path
        for item in publication.files
        if item.relative_path.endswith(".glb")
    }
    assert len(published_glbs) == 1
    assert not any(
        token in relative
        for relative in published_glbs
        for token in (
            "centerpiece", "linear2", "linear3", "corner3",
            "linear4", "reverse-l", "proposed-l-",
        )
    )


@pytest.mark.parametrize(
    ("length_ft", "width_ft", "expected_asset_ids"),
    (
        (10.0, 10.0, {"proposed-centerpiece-v1", "proposed-linear2-v1"}),
        (10.0, 20.0, {"proposed-linear2-v1", "proposed-linear3-v1"}),
    ),
)
def test_explicit_linear_publication_preserves_historical_fixture_assets(
    length_ft: float,
    width_ft: float,
    expected_asset_ids: set[str],
) -> None:
    layout = _layout_identity(
        generate_proposed_led_layout(
            length_ft,
            width_ft,
            proposed_layout_mode="linear",
        )
    )
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id="proposed",
        requested_length_ft=length_ft,
        requested_width_ft=width_ft,
        layout_identity=layout,
    )
    catalog = json.loads(publication.catalog.data)
    assert {
        group["display_asset_id"] for group in catalog["asset_groups"]
    } == expected_asset_ids


def test_viewer_fixture_identity_is_deterministic_and_mode_distinct() -> None:
    standalone_layout = _layout_identity(generate_proposed_led_layout(10.0, 10.0))
    linear_layout = _layout_identity(
        generate_proposed_led_layout(
            10.0,
            10.0,
            proposed_layout_mode="linear",
        )
    )
    legacy_layout = _layout_identity(
        generate_proposed_led_layout(
            10.0,
            10.0,
            proposed_layout_mode="legacy",
        )
    )
    standalone = build_fixture_publication(
        run_id=RUN_ID,
        system_id="proposed",
        requested_length_ft=10.0,
        requested_width_ft=10.0,
        layout_identity=standalone_layout,
    )
    repeated = build_fixture_publication(
        run_id=RUN_ID,
        system_id="proposed",
        requested_length_ft=10.0,
        requested_width_ft=10.0,
        layout_identity=standalone_layout,
    )
    linear = build_fixture_publication(
        run_id=RUN_ID,
        system_id="proposed",
        requested_length_ft=10.0,
        requested_width_ft=10.0,
        layout_identity=linear_layout,
    )
    legacy = build_fixture_publication(
        run_id=RUN_ID,
        system_id="proposed",
        requested_length_ft=10.0,
        requested_width_ft=10.0,
        layout_identity=legacy_layout,
    )
    assert standalone == repeated
    assert len({
        standalone.authoritative_layout_sha256,
        linear.authoritative_layout_sha256,
        legacy.authoritative_layout_sha256,
    }) == 3
    assert len({
        standalone.fixture_plan_sha256,
        linear.fixture_plan_sha256,
        legacy.fixture_plan_sha256,
    }) == 3
    assert len({
        standalone.catalog.sha256,
        linear.catalog.sha256,
        legacy.catalog.sha256,
    }) == 3


@pytest.mark.parametrize(("length_ft", "width_ft"), ((10.0, 10.0), (10.0, 20.0)))
def test_proposed_fixture_bounds_stay_in_room_anchor_and_actual_asset_envelope(
    length_ft: float,
    width_ft: float,
) -> None:
    layout = _layout_identity(generate_proposed_led_layout(length_ft, width_ft))
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id="proposed",
        requested_length_ft=length_ft,
        requested_width_ft=width_ft,
        layout_identity=layout,
    )
    catalog = json.loads(publication.catalog.data)
    files = {item.relative_path: item.data for item in publication.files}
    fixture_bounds = [[math.inf, math.inf, math.inf], [-math.inf, -math.inf, -math.inf]]
    maximum_instance_span = [0.0, 0.0, 0.0]

    for group in catalog["asset_groups"]:
        primitives = _authored_glb_primitive_bounds(
            files[f"fixtures/{group['asset']['filename']}"]
        )
        matrix_record = group["instance_matrices"]
        matrix_data = files[f"fixtures/{matrix_record['filename']}"]
        for offset in range(0, len(matrix_data), MATRIX_STRIDE_BYTES):
            column_major = struct.unpack_from("<16f", matrix_data, offset)
            server = tuple(
                column_major[column * 4 + row]
                for row in range(4)
                for column in range(4)
            )
            instance_bounds = [
                [math.inf, math.inf, math.inf],
                [-math.inf, -math.inf, -math.inf],
            ]
            for authored_world, minimum, maximum in primitives:
                final = _matrix4_multiply(server, authored_world)
                for corner in _box_corners(minimum, maximum):
                    transformed = _transform(final, corner)
                    for axis, value in enumerate(transformed):
                        instance_bounds[0][axis] = min(
                            instance_bounds[0][axis], value
                        )
                        instance_bounds[1][axis] = max(
                            instance_bounds[1][axis], value
                        )
                        fixture_bounds[0][axis] = min(fixture_bounds[0][axis], value)
                        fixture_bounds[1][axis] = max(fixture_bounds[1][axis], value)
            for axis in range(3):
                maximum_instance_span[axis] = max(
                    maximum_instance_span[axis],
                    instance_bounds[1][axis] - instance_bounds[0][axis],
                )

    records = catalog["fixture_plan"]["fixtures"]
    anchor_centers = [
        (
            record["scientific_translation_m"]["x"],
            record["scientific_translation_m"]["z"],
            -record["scientific_translation_m"]["y"],
        )
        for record in records
    ]
    length_m, width_m = length_ft * 0.3048, width_ft * 0.3048
    room_minimum = (-length_m / 2.0, 0.0, -width_m / 2.0)
    room_maximum = (length_m / 2.0, 0.0, width_m / 2.0)
    envelope_minimum = tuple(
        min(room_minimum[axis], min(point[axis] for point in anchor_centers))
        - maximum_instance_span[axis]
        for axis in range(3)
    )
    envelope_maximum = tuple(
        max(room_maximum[axis], max(point[axis] for point in anchor_centers))
        + maximum_instance_span[axis]
        for axis in range(3)
    )
    assert all(
        envelope_minimum[axis] - 1.0e-6
        <= fixture_bounds[0][axis]
        <= fixture_bounds[1][axis]
        <= envelope_maximum[axis] + 1.0e-6
        for axis in range(3)
    )
    assert maximum_instance_span[0] < length_m
    assert maximum_instance_span[2] < width_m


def test_ten_by_ten_implicated_fixture_ids_select_linear4_with_exact_order() -> None:
    layout = _layout_identity(
        generate_proposed_led_layout(
            10.0,
            10.0,
            proposed_layout_mode="legacy",
        )
    )
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id="proposed",
        requested_length_ft=10.0,
        requested_width_ft=10.0,
        layout_identity=layout,
    )
    catalog = json.loads(publication.catalog.data)
    records = catalog["fixture_plan"]["fixtures"]
    expected_scientific_types = {
        "proposed-fixture-0007": "L",
        "proposed-fixture-0008": "L",
        "proposed-fixture-0009": "L",
        "proposed-fixture-0010": "L",
        "proposed-fixture-0013": "L",
        "proposed-fixture-0014": "reverse_L",
    }
    implicated = [
        record for record in records
        if record["fixture_id"] in expected_scientific_types
    ]
    assert [record["fixture_id"] for record in implicated] == list(
        expected_scientific_types
    )
    assert all(
        record["fixture_type"] == expected_scientific_types[record["fixture_id"]]
        and record["display_fixture_type"] == "linear4"
        and record["display_asset_id"] == "proposed-linear4-v1"
        and record["anchor_max_residual_m"] <= ANCHOR_TOLERANCE_M
        for record in implicated
    )
    linear4_group = next(
        group for group in catalog["asset_groups"]
        if group["display_asset_id"] == "proposed-linear4-v1"
    )
    assert linear4_group["ordered_fixture_ids"] == [
        record["fixture_id"] for record in records
        if record["display_asset_id"] == "proposed-linear4-v1"
    ]
    matrix_record = linear4_group["instance_matrices"]
    matrix_bytes = next(
        item.data for item in publication.files
        if item.relative_path == f"fixtures/{matrix_record['filename']}"
    )
    for offset in range(0, len(matrix_bytes), MATRIX_STRIDE_BYTES):
        column_major = struct.unpack_from("<16f", matrix_bytes, offset)
        row_major = tuple(
            column_major[column * 4 + row]
            for row in range(4)
            for column in range(4)
        )
        assert _matrix3_determinant(row_major) < 0.0


def test_ten_by_ten_server_parity_selects_two_positive_reverse_l_instances() -> None:
    layout = _layout_identity(
        generate_proposed_led_layout(
            10.0,
            10.0,
            proposed_layout_mode="legacy",
        )
    )
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id="proposed",
        requested_length_ft=10.0,
        requested_width_ft=10.0,
        layout_identity=layout,
    )
    catalog = json.loads(publication.catalog.data)
    files = {item.relative_path: item.data for item in publication.files}
    parity_by_fixture_id: dict[str, int] = {}
    for group in catalog["asset_groups"]:
        matrix_record = group["instance_matrices"]
        matrix_bytes = files[f"fixtures/{matrix_record['filename']}"]
        for instance_index, fixture_id in enumerate(group["ordered_fixture_ids"]):
            column_major = struct.unpack_from(
                "<16f", matrix_bytes, instance_index * MATRIX_STRIDE_BYTES
            )
            row_major = tuple(
                column_major[column * 4 + row]
                for row in range(4)
                for column in range(4)
            )
            parity_by_fixture_id[fixture_id] = (
                1 if _matrix3_determinant(row_major) > 0.0 else -1
            )

    assert parity_by_fixture_id["proposed-fixture-0001"] == 1
    assert parity_by_fixture_id["proposed-fixture-0002"] == 1
    assert all(
        parity == -1
        for fixture_id, parity in parity_by_fixture_id.items()
        if fixture_id not in {"proposed-fixture-0001", "proposed-fixture-0002"}
    )
    records = {
        record["fixture_id"]: record
        for record in catalog["fixture_plan"]["fixtures"]
    }
    assert {
        records[fixture_id]["display_asset_id"]
        for fixture_id in ("proposed-fixture-0001", "proposed-fixture-0002")
    } == {"proposed-reverse-l-v1"}


def test_proposed_orientation_variants_follow_every_authoritative_connector() -> None:
    variants: dict[str, set[float]] = {}
    for length_ft, width_ft in ((10.0, 10.0), (10.0, 20.0)):
        generated = generate_proposed_led_layout(
            length_ft,
            width_ft,
            proposed_layout_mode="legacy",
        )
        layout = _layout_identity(generated)
        publication = build_fixture_publication(
            run_id=RUN_ID,
            system_id="proposed",
            requested_length_ft=length_ft,
            requested_width_ft=width_ft,
            layout_identity=layout,
        )
        records = json.loads(publication.catalog.data)["fixture_plan"]["fixtures"]
        assert [item["fixture_id"] for item in records] == [
            fixture.fixture_id for fixture in generated.fixtures
        ]
        for fixture, record in zip(generated.fixtures, records, strict=True):
            connector = fixture.connectors[0]
            start = generated.modules[connector.start_module_index]
            end = generated.modules[connector.end_module_index]
            expected_yaw = round(
                math.degrees(math.atan2(end.y_m - start.y_m, end.x_m - start.x_m))
                % 360.0,
                6,
            ) % 360.0
            assert fixture.orientation_degrees == expected_yaw
            assert (
                record["orientation"]["authoritative_aligned_degrees"]
                == expected_yaw
            )
            variants.setdefault(fixture.display_fixture_type, set()).add(expected_yaw)
    assert variants.keys() >= {
        "centerpiece",
        "linear2",
        "linear3",
        "corner3",
        "linear4",
        "L",
        "reverse_L",
    }
    assert all(
        values and all(math.isfinite(value) for value in values)
        for values in variants.values()
    )


def test_corner3_and_linear3_topology_cannot_be_interchanged() -> None:
    layout = _layout_identity(
        generate_proposed_led_layout(
            10.0,
            10.0,
            proposed_layout_mode="legacy",
        )
    )
    corner = next(
        item for item in layout["fixtures"]
        if item["display_fixture_type"] == "corner3"
    )
    straight = next(
        item for item in layout["fixtures"]
        if item["display_fixture_type"] == "linear3"
    )
    for fixture_id, wrong_type in (
        (corner["fixture_id"], "linear3"),
        (straight["fixture_id"], "corner3"),
    ):
        altered = json.loads(json.dumps(layout))
        altered_fixture = next(
            item for item in altered["fixtures"]
            if item["fixture_id"] == fixture_id
        )
        altered_fixture["display_fixture_type"] = wrong_type
        with pytest.raises(ValueError, match="member topology"):
            build_fixture_publication(
                run_id=RUN_ID,
                system_id="proposed",
                requested_length_ft=10.0,
                requested_width_ft=10.0,
                layout_identity=altered,
            )


def test_collinear_and_right_angle_four_module_assets_cannot_interchange() -> None:
    layout = _layout_identity(
        generate_proposed_led_layout(
            10.0,
            10.0,
            proposed_layout_mode="legacy",
        )
    )
    for fixture_id, wrong_type in (
        ("proposed-fixture-0007", "L"),
        ("proposed-fixture-0001", "linear4"),
    ):
        altered = json.loads(json.dumps(layout))
        altered_fixture = next(
            item for item in altered["fixtures"]
            if item["fixture_id"] == fixture_id
        )
        altered_fixture["display_fixture_type"] = wrong_type
        with pytest.raises(ValueError, match="member topology"):
            build_fixture_publication(
                run_id=RUN_ID,
                system_id="proposed",
                requested_length_ft=10.0,
                requested_width_ft=10.0,
                layout_identity=altered,
            )


def test_l_and_reverse_l_display_assets_cannot_interchange() -> None:
    layout = _layout_identity(
        generate_proposed_led_layout(
            10.0,
            20.0,
            proposed_layout_mode="legacy",
        )
    )
    fixtures = {
        item["display_fixture_type"]: item
        for item in layout["fixtures"]
        if item["display_fixture_type"] in {"L", "reverse_L"}
    }
    assert fixtures.keys() == {"L", "reverse_L"}
    for display_type, wrong_type in (("L", "reverse_L"), ("reverse_L", "L")):
        altered = json.loads(json.dumps(layout))
        altered_fixture = next(
            item for item in altered["fixtures"]
            if item["fixture_id"] == fixtures[display_type]["fixture_id"]
        )
        altered_fixture["display_fixture_type"] = wrong_type
        with pytest.raises(ValueError, match="member topology"):
            build_fixture_publication(
                run_id=RUN_ID,
                system_id="proposed",
                requested_length_ft=10.0,
                requested_width_ft=20.0,
                layout_identity=altered,
            )


def test_proposed_publication_omits_corner3_when_layout_does_not_use_it() -> None:
    layout = _layout_identity(generate_proposed_led_layout(4.0, 4.0))
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id="proposed",
        requested_length_ft=4.0,
        requested_width_ft=4.0,
        layout_identity=layout,
    )
    catalog = json.loads(publication.catalog.data)
    asset_ids = {group["display_asset_id"] for group in catalog["asset_groups"]}
    fixture_glbs = {
        item.relative_path for item in publication.files
        if item.relative_path.endswith(".glb")
    }

    assert "proposed-corner3-v1" not in asset_ids
    assert not any("proposed-corner3-v1" in path for path in fixture_glbs)
    assert len(fixture_glbs) == len(asset_ids)


def test_matrix_buffers_are_exact_little_endian_column_major_float32() -> None:
    layout = plan_conventional_layout_from_feet(10.0, 10.0).to_payload()
    publication = build_fixture_publication(
        run_id=RUN_ID, system_id="conventional",
        requested_length_ft=10.0, requested_width_ft=10.0,
        layout_identity=layout,
    )
    catalog = json.loads(publication.catalog.data)
    group = catalog["asset_groups"][0]
    record = group["instance_matrices"]
    data = next(
        item.data for item in publication.files
        if item.relative_path == f"fixtures/{record['filename']}"
    )
    assert record["stride_bytes"] == MATRIX_STRIDE_BYTES == 16 * 4
    assert record["byte_length"] == record["count"] * MATRIX_STRIDE_BYTES
    assert record["sha256"] == hashlib.sha256(data).hexdigest()
    first = struct.unpack_from("<16f", data)
    scientific = catalog["fixture_plan"]["fixtures"][0]["scientific_translation_m"]
    assert first[12] == pytest.approx(scientific["x"], abs=2.0e-6)
    assert first[13] == pytest.approx(scientific["z"], abs=2.0e-6)
    assert first[14] == pytest.approx(-scientific["y"], abs=2.0e-6)
    assert first[0] > 0.0 and first[10] < 0.0


def test_rolling_bench_viewer_uses_authoritative_simulation_coordinates() -> None:
    plan = plan_conventional_layout_from_feet(
        24.0,
        12.0,
        policy="rolling_bench",
    )
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id="conventional",
        requested_length_ft=24.0,
        requested_width_ft=12.0,
        layout_identity=plan.to_payload(),
    )
    records = json.loads(publication.catalog.data)["fixture_plan"]["fixtures"]

    assert [
        (
            record["scientific_translation_m"]["x"],
            record["scientific_translation_m"]["y"],
            record["scientific_translation_m"]["z"],
        )
        for record in records
    ] == pytest.approx(
        [
            (
                fixture.aperture_center_m.aligned_x_m,
                fixture.aperture_center_m.aligned_y_m,
                fixture.aperture_center_m.z_m,
            )
            for fixture in plan.fixtures
        ]
    )
    assert {
        record["orientation"]["authoritative_aligned_degrees"]
        for record in records
    } == {90.0}


def test_fixture_catalog_accepts_room_dimensions_above_thirty_feet() -> None:
    plan = plan_conventional_layout_from_feet(
        30.0,
        50.0,
        policy="rolling_bench",
    )
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id="conventional",
        requested_length_ft=30.0,
        requested_width_ft=50.0,
        layout_identity=plan.to_payload(),
    )

    assert json.loads(publication.catalog.data)["requested_room_ft"] == {
        "length": 30.0,
        "width": 50.0,
    }


@pytest.mark.parametrize(
    ("system_id", "layout"),
    (
        ("proposed", _layout_identity(generate_proposed_led_layout(10.0, 10.0))),
        ("conventional", plan_conventional_layout_from_feet(10.0, 10.0).to_payload()),
        ("hps", plan_hps_layout_from_feet(10.0, 10.0).to_payload()),
    ),
)
def test_every_published_server_matrix_is_finite_affine_and_nonsingular(
    system_id: str, layout: dict[str, object]
) -> None:
    publication = build_fixture_publication(
        run_id=RUN_ID, system_id=system_id,
        requested_length_ft=10.0, requested_width_ft=10.0,
        layout_identity=layout,
    )
    files = {item.relative_path: item.data for item in publication.files}
    catalog = json.loads(publication.catalog.data)
    for group in catalog["asset_groups"]:
        matrix_artifact = group["instance_matrices"]
        data = files[f"fixtures/{matrix_artifact['filename']}"]
        parities: set[int] = set()
        for offset in range(0, len(data), MATRIX_STRIDE_BYTES):
            column_major = struct.unpack_from("<16f", data, offset)
            matrix = tuple(
                column_major[column * 4 + row]
                for row in range(4)
                for column in range(4)
            )
            assert all(math.isfinite(value) for value in matrix)
            assert matrix[12:] == (0.0, 0.0, 0.0, 1.0)
            determinant = _matrix3_determinant(matrix)
            assert abs(determinant) > 1.0e-15
            parities.add(-1 if determinant < 0.0 else 1)
        assert len(parities) == 1


def test_authored_glb_nodes_have_proper_positive_scale_decompositions() -> None:
    root = resources.files("fspm_optics").joinpath("resources", "viewer")
    for asset in ASSET_REGISTRY:
        document = validate_glb_content(
            root.joinpath(*asset.resource_path.split("/")).read_bytes(), asset
        )
        for node in document.get("nodes", []):
            assert "matrix" not in node
            assert all(value > 0.0 for value in node.get("scale", [1.0, 1.0, 1.0]))
            rotation = node.get("rotation")
            if rotation is not None:
                assert math.sqrt(
                    sum(value * value for value in rotation)
                ) == pytest.approx(
                    1.0,
                    abs=2.0e-7,
                )


def test_server_matrix_composes_scientific_handedness_and_yaw_once() -> None:
    conventional_led = next(item for item in ASSET_REGISTRY if item.system_id == "conventional")
    neutral = replace(
        conventional_led,
        placement_correction_local_x_mm=0.0,
        placement_correction_local_y_mm=0.0,
        placement_correction_local_z_mm=0.0,
        dimension_correction_scale_xyz=(1.0, 1.0, 1.0),
    )
    matrix = _direct_matrix(neutral, 1.0, 2.0, 0.5, 90.0)
    assert _matrix3_determinant(matrix) < 0.0
    assert _transform(matrix, (0.0, 0.0, 0.0)) == pytest.approx(
        (1.0, 0.5, -2.0)
    )
    assert _transform(matrix, (1000.0, 0.0, 0.0)) == pytest.approx(
        (1.0, 0.5, -3.0)
    )


def test_all_seven_proposed_horizontal_anchors_and_aperture_planes_validate() -> None:
    proposed_assets = [
        item for item in ASSET_REGISTRY
        if item.system_id == "proposed" and item.fixture_type != "standalone_module"
    ]
    assert len(proposed_assets) == 7
    assert ANCHOR_TOLERANCE_M == 2.5e-4
    angle = math.radians(37.0)
    cosine, sine = math.cos(angle), math.sin(angle)
    for asset in proposed_assets:
        targets = [
            (
                0.3 + 1.17 * (cosine * anchor[0] - sine * anchor[2]),
                0.4572,
                -0.8 + 1.17 * (sine * anchor[0] + cosine * anchor[2]),
            )
            for anchor in asset.anchors_m
        ]
        matrix = _proposed_matrix(asset, targets)
        residual = max(
            math.hypot(
                _transform(
                    matrix,
                    tuple(component * 1000 for component in anchor),
                )[0]
                - target[0],
                _transform(
                    matrix,
                    tuple(component * 1000 for component in anchor),
                )[2]
                - target[2],
            )
            for anchor, target in zip(asset.anchors_m, targets, strict=True)
        )
        assert residual <= ANCHOR_TOLERANCE_M
        assert (
            matrix[5] * asset.placement_plane_local_y_mm + matrix[7]
        ) == pytest.approx(0.4572, abs=1.0e-12)
        assert abs(_normalized_spatial_determinant(matrix)) == pytest.approx(
            1.0, abs=1.0e-5
        )
        assert len(_column_major(matrix)) == 16


def test_standalone_proposed_transform_is_exact_rigid_mm_conversion() -> None:
    asset = next(
        item for item in ASSET_REGISTRY
        if item.asset_id == "proposed-led-module-v1"
    )
    center = (0.75, -0.25, 0.4572)
    matrix = _standalone_proposed_matrix(asset, *center)
    validate_standalone_proposed_transform(
        matrix,
        module_center_m=center,
        placement_plane_local_y_mm=27.4,
    )
    assert matrix[:12] == (
        0.001, 0.0, 0.0, center[0],
        0.0, -0.001, 0.0, center[2] + 0.0274,
        0.0, 0.0, 0.001, -center[1],
    )
    assert _transform(matrix, (0.0, 27.4, 0.0)) == pytest.approx(
        (center[0], center[2], -center[1]),
        abs=1.0e-15,
    )
    assert abs(_normalized_spatial_determinant(matrix)) == pytest.approx(
        1.0,
        abs=1.0e-12,
    )


def test_exact_l_family_diagnostic_uses_only_the_matching_ordered_asset() -> None:
    points = (
        (-5.0, -1.0),
        (-6.0, 0.0),
        (-5.0, 1.0),
        (-4.0, 2.0),
    )
    assets = {asset.fixture_type: asset for asset in ASSET_REGISTRY}
    for ordered_points, matching_type, rejected_type in (
        (points, "L", "reverse_L"),
        (tuple(reversed(points)), "reverse_L", "L"),
    ):
        targets = [(x, 0.4572, -y) for x, y in ordered_points]
        matrix = _proposed_matrix(assets[matching_type], targets)
        _validate_anchor_residuals(assets[matching_type], matrix, targets)
        assert abs(_normalized_spatial_determinant(matrix)) == pytest.approx(
            1.0, abs=1.0e-5
        )
        with pytest.raises(ValueError, match="orthogonal|normalized determinant"):
            _proposed_matrix(assets[rejected_type], targets)


@pytest.mark.parametrize(
    ("matrix", "message"),
    (
        (
            (
                math.nan, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 0.0, 1.0,
            ),
            "finite affine",
        ),
        (
            (
                1.0, 0.0, 0.0, 0.0,
                0.0, 0.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 0.0, 1.0,
            ),
            "nonsingular",
        ),
        (
            (
                1.0, 0.0, 1.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 0.0, 1.0,
            ),
            "orthogonal",
        ),
    ),
)
def test_fixture_matrix_validation_rejects_nonfinite_singular_and_sheared(
    matrix: tuple[float, ...], message: str
) -> None:
    if message == "orthogonal":
        assert abs(_normalized_spatial_determinant(matrix)) == pytest.approx(
            math.sqrt(0.5)
        )
    with pytest.raises(ValueError, match=message):
        _validate_affine_matrix(matrix)


def test_anchor_validation_rejects_excessive_residual() -> None:
    asset = next(item for item in ASSET_REGISTRY if item.fixture_type == "linear2")
    matrix = _proposed_matrix(
        asset,
        [(-0.5, 0.4572, 0.0), (0.5, 0.4572, 0.0)],
    )
    with pytest.raises(ValueError, match="anchor residual"):
        _validate_anchor_residuals(
            asset,
            matrix,
            [(-0.5, 0.4572, 0.0), (0.6, 0.4572, 0.0)],
        )


def test_hps_housing_and_conventional_aperture_planes_are_explicit() -> None:
    conventional = build_fixture_publication(
        run_id=RUN_ID, system_id="conventional", requested_length_ft=10.0,
        requested_width_ft=10.0,
        layout_identity=plan_conventional_layout_from_feet(10.0, 10.0).to_payload(),
    )
    hps = build_fixture_publication(
        run_id=RUN_ID, system_id="hps", requested_length_ft=10.0,
        requested_width_ft=10.0,
        layout_identity=plan_hps_layout_from_feet(10.0, 10.0).to_payload(),
    )
    conventional_record = json.loads(conventional.catalog.data)["fixture_plan"]["fixtures"][0]
    hps_record = json.loads(hps.catalog.data)["fixture_plan"]["fixtures"][0]
    assert conventional_record["placement_plane"] == "emitting_aperture_plane"
    assert hps_record["placement_plane"] == "luminous_aperture_plane_separate_from_housing"
    assert hps_record["placement_contract_sha256"] == (
        "d83577f6bb7503ff8865dbe55122bfd37d5ea0e0f2c1d9dc130a7a98f9598293"
    )
    assert hps_record["scientific_translation_m"]["z"] == 0.6096
    hps_asset = next(item for item in ASSET_REGISTRY if item.system_id == "hps")
    hps_matrix = _direct_matrix(hps_asset, 0.0, 0.0, 0.6096, 0.0)
    assert _matrix3_determinant(hps_matrix) < 0.0
    assert hps_matrix == pytest.approx((
        0.001, 0.0, 0.0, 0.0,
        0.0, 0.001, 0.0, 0.85852,
        0.0, 0.0, -0.001, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ), abs=1.0e-15)
    assert _transform(hps_matrix, (0.0, -248.92, 0.0)) == pytest.approx(
        (0.0, 0.6096, 0.0), abs=1.0e-15
    )


def test_hps_float32_publication_uses_its_exact_quantization_error_bound() -> None:
    asset = next(item for item in ASSET_REGISTRY if item.system_id == "hps")
    scientific_center = (1.25, -0.75, 0.9144)
    authoritative = _direct_matrix(asset, *scientific_center, 0.0)
    published = tuple(
        struct.unpack("<16f", struct.pack("<16f", *authoritative))
    )
    validation = validate_hps_publication_transform(
        asset=asset,
        authoritative_matrix_row_major=authoritative,
        published_matrix_row_major=published,
        scientific_center_m=scientific_center,
        placement_contract_sha256=hps_placement_contract_sha256(asset),
    )

    assert validation.published_outward_normal_scientific == (0.0, 0.0, -1.0)
    assert validation.maximum_publication_error_m == pytest.approx(
        6.648767736372463e-8,
        rel=0.0,
        abs=1.0e-20,
    )
    assert validation.maximum_publication_error_m <= (
        validation.maximum_float32_error_bound_m
    )
    assert all(
        point[2]
        == pytest.approx(
            0.9144,
            abs=validation.maximum_float32_error_bound_m,
        )
        for point in validation.published_points_scientific_m
    )
    mutated = replace(asset, placement_correction_local_y_mm=248.919998)
    assert hps_placement_contract_sha256(mutated) != (
        hps_placement_contract_sha256(asset)
    )
    with pytest.raises(ValueError, match="authenticated placement contract"):
        validate_hps_publication_transform(
            asset=mutated,
            authoritative_matrix_row_major=authoritative,
            published_matrix_row_major=published,
            scientific_center_m=scientific_center,
            placement_contract_sha256=hps_placement_contract_sha256(asset),
        )


def test_hps_transport_reuses_the_byte_exact_viewer_publication_matrix() -> None:
    layout = plan_hps_layout_from_feet(
        12.0,
        8.0,
        reference_plane_z_m=0.005,
        mount_height_m=0.9144,
    )
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id="hps",
        requested_length_ft=12.0,
        requested_width_ft=8.0,
        layout_identity=layout.to_payload(),
    )
    transport = resolve_fixture_transport_placements(
        "hps",
        layout.to_payload(),
    )
    matrix_file = next(
        item for item in publication.files if item.relative_path.endswith(".f32le.bin")
    )
    first_column_major = struct.unpack("<16f", matrix_file.data[:64])
    first_row_major = tuple(
        first_column_major[column * 4 + row]
        for row in range(4)
        for column in range(4)
    )
    assert transport[0].matrix_row_major == first_row_major
    assert transport[0].placement_contract_sha256 == (
        "d83577f6bb7503ff8865dbe55122bfd37d5ea0e0f2c1d9dc130a7a98f9598293"
    )


def test_catalog_rejects_wrong_run_system_layout_hash_and_inventory() -> None:
    layout = plan_hps_layout_from_feet(10.0, 10.0).to_payload()
    publication = build_fixture_publication(
        run_id=RUN_ID, system_id="hps", requested_length_ft=10.0,
        requested_width_ft=10.0, layout_identity=layout,
    )
    files = {item.relative_path: item.data for item in publication.files}
    assert publication.authoritative_layout_sha256 == json.loads(
        publication.catalog.data
    )["authoritative_layout_sha256"]
    with pytest.raises(ValueError, match="run, request, system, or layout"):
        validate_fixture_publication(
            publication.catalog.data, files, expected_run_id="c" * 32,
            expected_system_id="hps", expected_requested_length_ft=10.0,
            expected_requested_width_ft=10.0, expected_layout_identity=layout,
        )
    with pytest.raises(ValueError, match="run, request, system, or layout"):
        validate_fixture_publication(
            publication.catalog.data, files, expected_run_id=RUN_ID,
            expected_system_id="conventional", expected_requested_length_ft=10.0,
            expected_requested_width_ft=10.0, expected_layout_identity=layout,
        )
    missing = dict(files)
    missing.pop(next(iter(missing)))
    with pytest.raises(ValueError, match="inventory"):
        validate_fixture_publication(
            publication.catalog.data, missing, expected_run_id=RUN_ID,
            expected_system_id="hps", expected_requested_length_ft=10.0,
            expected_requested_width_ft=10.0, expected_layout_identity=layout,
        )
    expansive = dict(files)
    expansive["fixtures/../escape.glb"] = b"undeclared"
    with pytest.raises(ValueError, match="inventory"):
        validate_fixture_publication(
            publication.catalog.data, expansive, expected_run_id=RUN_ID,
            expected_system_id="hps", expected_requested_length_ft=10.0,
            expected_requested_width_ft=10.0, expected_layout_identity=layout,
        )
    altered = dict(files)
    relative = next(iter(altered))
    altered[relative] = altered[relative][:-1] + bytes((altered[relative][-1] ^ 1,))
    with pytest.raises(ValueError, match="exact validation"):
        validate_fixture_publication(
            publication.catalog.data, altered, expected_run_id=RUN_ID,
            expected_system_id="hps", expected_requested_length_ft=10.0,
            expected_requested_width_ft=10.0, expected_layout_identity=layout,
        )


def test_proposed_catalog_rejects_unsupported_or_cross_system_asset_types() -> None:
    layout = _layout_identity(
        generate_proposed_led_layout(
            10.0,
            10.0,
            proposed_layout_mode="linear",
        )
    )
    altered = json.loads(json.dumps(layout))
    altered["fixtures"][0]["fixture_type"] = "conventional_led_8_bar"
    with pytest.raises(ValueError, match="unsupported"):
        build_fixture_publication(
            run_id=RUN_ID, system_id="proposed", requested_length_ft=10.0,
            requested_width_ft=10.0, layout_identity=altered,
        )

    wrong_yaw = json.loads(json.dumps(layout))
    wrong_yaw["fixtures"][0]["orientation_degrees"] += 1.0
    with pytest.raises(ValueError, match="yaw"):
        build_fixture_publication(
            run_id=RUN_ID, system_id="proposed", requested_length_ft=10.0,
            requested_width_ft=10.0, layout_identity=wrong_yaw,
        )


def test_direct_catalog_rejects_changed_authoritative_fixture_order() -> None:
    layout = plan_conventional_layout_from_feet(20.0, 20.0).to_payload()
    altered = json.loads(json.dumps(layout))
    altered["fixtures"].reverse()
    with pytest.raises(ValueError, match="Y-major/X-minor"):
        build_fixture_publication(
            run_id=RUN_ID, system_id="conventional", requested_length_ft=20.0,
            requested_width_ft=20.0, layout_identity=altered,
        )


def test_twenty_by_twenty_conventional_catalog_accepts_authoritative_order() -> None:
    layout = plan_conventional_layout_from_feet(20.0, 20.0).to_payload()
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id="conventional",
        requested_length_ft=20.0,
        requested_width_ft=20.0,
        layout_identity=layout,
    )
    catalog = json.loads(publication.catalog.data)
    assert catalog["fixture_count"] == layout["resolved_counts"]["total"]
    assert catalog["fixture_plan"]["fixtures"][0]["fixture_id"] == (
        layout["fixtures"][0]["fixture_id"]
    )


def _matrix4_multiply(
    left: tuple[float, ...], right: tuple[float, ...]
) -> tuple[float, ...]:
    return tuple(
        sum(left[row * 4 + inner] * right[inner * 4 + column] for inner in range(4))
        for row in range(4)
        for column in range(4)
    )


def _glb_node_local_matrix(node: dict[str, object]) -> tuple[float, ...]:
    authored_matrix = node.get("matrix")
    if isinstance(authored_matrix, list):
        return tuple(
            float(authored_matrix[column * 4 + row])
            for row in range(4)
            for column in range(4)
        )
    translation = node.get("translation", [0.0, 0.0, 0.0])
    rotation = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
    scale = node.get("scale", [1.0, 1.0, 1.0])
    assert isinstance(translation, list)
    assert isinstance(rotation, list)
    assert isinstance(scale, list)
    tx, ty, tz = (float(value) for value in translation)
    x, y, z, w = (float(value) for value in rotation)
    sx, sy, sz = (float(value) for value in scale)
    return (
        (1.0 - 2.0 * (y * y + z * z)) * sx,
        (2.0 * (x * y - z * w)) * sy,
        (2.0 * (x * z + y * w)) * sz,
        tx,
        (2.0 * (x * y + z * w)) * sx,
        (1.0 - 2.0 * (x * x + z * z)) * sy,
        (2.0 * (y * z - x * w)) * sz,
        ty,
        (2.0 * (x * z - y * w)) * sx,
        (2.0 * (y * z + x * w)) * sy,
        (1.0 - 2.0 * (x * x + y * y)) * sz,
        tz,
        0.0, 0.0, 0.0, 1.0,
    )


def _authored_glb_primitive_bounds(
    data: bytes,
) -> tuple[
    tuple[tuple[float, ...], tuple[float, float, float], tuple[float, float, float]],
    ...,
]:
    _, version, total_length = struct.unpack_from("<III", data, 0)
    assert version == 2 and total_length == len(data)
    json_length, json_type = struct.unpack_from("<II", data, 12)
    assert json_type == 0x4E4F534A
    document = json.loads(data[20:20 + json_length].decode("utf-8"))
    nodes = document["nodes"]
    meshes = document["meshes"]
    accessors = document["accessors"]
    identity = (
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )
    output: list[
        tuple[tuple[float, ...], tuple[float, float, float], tuple[float, float, float]]
    ] = []

    def visit(node_index: int, parent_world: tuple[float, ...]) -> None:
        node = nodes[node_index]
        world = _matrix4_multiply(parent_world, _glb_node_local_matrix(node))
        mesh_index = node.get("mesh")
        if isinstance(mesh_index, int):
            for primitive in meshes[mesh_index]["primitives"]:
                accessor = accessors[primitive["attributes"]["POSITION"]]
                minimum = tuple(float(value) for value in accessor["min"])
                maximum = tuple(float(value) for value in accessor["max"])
                assert len(minimum) == len(maximum) == 3
                output.append((world, minimum, maximum))
        for child in node.get("children", []):
            visit(child, world)

    scene_index = document.get("scene", 0)
    for root_node in document["scenes"][scene_index]["nodes"]:
        visit(root_node, identity)
    assert output
    return tuple(output)


def _box_corners(
    minimum: tuple[float, float, float], maximum: tuple[float, float, float]
) -> tuple[tuple[float, float, float], ...]:
    return tuple(
        (x, y, z)
        for x in (minimum[0], maximum[0])
        for y in (minimum[1], maximum[1])
        for z in (minimum[2], maximum[2])
    )


def _minimal_glb(payload: dict[str, object]) -> bytes:
    document = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    document += b" " * (-len(document) % 4)
    binary = b"\0" * 4
    total = 12 + 8 + len(document) + 8 + len(binary)
    return b"".join(
        (
            struct.pack("<III", 0x46546C67, 2, total),
            struct.pack("<II", len(document), 0x4E4F534A), document,
            struct.pack("<II", len(binary), 0x004E4942), binary,
        )
    )


def test_hps_catalog_uses_mount_height_relative_to_reference_plane() -> None:
    layout = plan_hps_layout(
        3.2004,
        3.7338,
        reference_plane_z_m=0.005,
        mount_height_m=0.4572,
    )
    payload = layout.to_payload()

    assert payload["mount"]["reference_plane_z_m"] == pytest.approx(0.005)
    assert payload["mount"]["aperture_plane_z_m"] == pytest.approx(0.4622)
    assert (
        payload["mount"]["aperture_plane_z_m"]
        - payload["mount"]["reference_plane_z_m"]
    ) == pytest.approx(0.4572)

    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id="hps",
        requested_length_ft=10.5,
        requested_width_ft=12.25,
        layout_identity=payload,
    )

    assert publication.files
