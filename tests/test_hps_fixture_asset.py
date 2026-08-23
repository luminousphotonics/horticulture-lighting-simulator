from __future__ import annotations

import hashlib
from importlib.resources import files
import json
from pathlib import Path

from fspm_optics.fixtures.occlusion import (
    load_authenticated_fixture_asset,
    validate_complete_classification_manifest,
)
from fspm_optics.fixtures.occlusion.gltf import decode_fixture_glb
from fspm_optics.viewer.fixtures import ASSET_REGISTRY


REPOSITORY = Path(__file__).parents[1]
CAD_ROOT = REPOSITORY / "src/fspm_optics/resources/cad/hps"
FINAL_GLB = (
    REPOSITORY
    / "src/fspm_optics/resources/viewer/fixtures/hps/hps.glb"
)
PROVENANCE_PATH = CAD_ROOT / "hps_1000w_fixture.provenance.json"
SOURCE = REPOSITORY / "scripts/fixture_assets/hps_1000w_fixture.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_hps_provenance_authenticates_source_and_every_generated_artifact() -> None:
    provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    assert provenance["asset_id"] == "hps-housing-v3"
    assert provenance["source"]["supplied_sha256"] == (
        "5bc9ca6670440a3d39e331063930b85eb6c67b9ab5c52569e30f7ee4a91596c4"
    )
    assert provenance["source"]["repository_source_sha256"] == _sha256(SOURCE)
    assert provenance["source"]["byte_for_byte_equal"] is True
    hashes = provenance["hashes"]
    assert hashes == {
        "packed_glb_sha256": (
            "083df1475e84e9552d5ec548fc34669092b5ba4284fcf33ebf7dbb2f26439d7d"
        ),
        "packed_inventory_sha256": (
            "aa0dd3eabb398a06713a8c7522c746cbf8f966c01325e6749d292b4b4c2b91d5"
        ),
        "raw_glb_sha256": (
            "09bff23bdb43de8c9e9d8c3422188517afad76868260cf3686a1fd277b9c4051"
        ),
        "raw_inventory_sha256": (
            "567f69761e41f49b3fb0620bc8ae58c278dd0ab50caa75313a21cdc594b5984a"
        ),
        "step_sha256": (
            "10423382d90007159b351fc417fbb94ec3a7305dd14242d59a5070967de061c7"
        ),
    }
    assert provenance["byte_lengths"] == {
        "packed_glb": 353160,
        "raw_glb": 515592,
        "step": 334749,
    }
    assert _sha256(CAD_ROOT / "hps_1000w_fixture.step") == hashes["step_sha256"]
    assert _sha256(CAD_ROOT / "hps_1000w_fixture.raw.glb") == hashes["raw_glb_sha256"]
    assert _sha256(FINAL_GLB) == hashes["packed_glb_sha256"]


def test_hps_cad_step_and_decoded_glbs_retain_closed_reflector_contract() -> None:
    provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    mechanical = provenance["mechanical_assertions"]
    roundtrip = provenance["step_roundtrip_assertions"]
    assert mechanical["bounds_cad_mm"] == [
        [-472.04394483199064, -321.3100001, -248.92000010000007],
        [422.9100001, 321.3100001, 0.0],
    ]
    for assertions in (mechanical, roundtrip):
        assert assertions["solid_count"] == 8
        assert assertions["unique_named_leaf_count"] == 8
        assert assertions["reflector_skirt_one_valid_solid"] is True
        assert set(assertions["leaf_solid_counts"].values()) == {1}
        assert assertions["sealed_top_box"]["former_vertical_roof_bore_absent"] is True
        assert assertions["sealed_top_box"]["unperforated_single_valid_solid"] is True
        assert abs(assertions["sealed_top_box"]["volume_mm3"] - 18_306_288.0) < 1e-6
        assert assertions["rear_exhaust"]["negative_x_end_only"] is True
        assert assertions["rear_exhaust"]["rear_wall_center_below_roof_seam"] is True
        rays = assertions["hood_opening_ray_assertions"]
        assert rays["closed_positive_y_side"]["hit"] is True
        assert rays["closed_negative_y_side"]["hit"] is True
        assert rays["horizontal_positive_x"]["hit"] is True
        assert rays["sealed_roof_straight_up"]["hit"] is True
        assert rays["sealed_roof_upward_positive_y"]["hit"] is True
        assert rays["sealed_roof_upward_negative_y"]["hit"] is True
        assert rays["sealed_roof_upward_positive_x"]["hit"] is True
        assert rays["rear_outside_bore_positive_y"]["hit"] is True
        assert rays["rear_outside_bore_negative_y"]["hit"] is True
        assert rays["rear_exhaust_bore_axis"]["hit"] is False
        assert rays["luminous_axis_to_rear_exhaust"]["hit"] is False
        assert rays["optical_opening_straight_down"]["hit"] is False
        rear = assertions["complete_assembly_ray_assertions"][
            "luminous_axis_to_rear_exhaust"
        ]
        assert rear["hood_and_flange_clear"] is True
        assert set(rear["intervening_internal_leaf_first_distances_mm"]) == {
            "ceramic_socket", "hps_bulb_base", "socket_bracket",
        }
    for validation in (provenance["raw_validation"], provenance["packed_validation"]):
        assert validation["bounds_gltf_mm"] == [
            [-471.9875183105469, -248.9199981689453, -321.30999755859375],
            [422.9100036621094, 0.0, 321.30999755859375],
        ]
        assert validation["mesh_count"] == validation["primitive_count"] == 8
        assert validation["triangle_count"] == 6022
        assert validation["degenerate_triangle_count"] == 0
        assert validation["extensions_used"] == validation["extensions_required"] == []
        assert validation["hood_opening_ray_assertions"]["sealed_roof_straight_up"]["hit"] is True
        assert validation["hood_opening_ray_assertions"]["rear_exhaust_bore_axis"]["hit"] is False
        assert validation["full_geometry_sha256"] == (
            "8584166b13409cf1f5739e0f86a9fd78fa5c2fc704636c4809a13b4406efa9f1"
        )
        assert validation["sealed_top_box"]["former_vertical_roof_bore_absent"] is True
        assert validation["sealed_top_box"]["straight_up_ray_hits"] is True
        assert abs(
            validation["sealed_top_box"]["decoded_closed_mesh_volume_mm3"]
            - 18_306_288.314208984
        ) < 1e-6
    agreement = provenance["raw_packed_agreement"]
    assert agreement["triangle_geometry_sha256_equal"] is True
    assert agreement["bounds_max_residual_mm"] == 0.0
    assert agreement["surface_area_relative_error"] == 0.0
    assert agreement["projected_area_relative_error"] == 0.0
    assert agreement["hood_opening_rays_equal"] is True
    assert agreement["complete_assembly_rays_equal"] is True
    classification = provenance["classification_next_phase"]
    assert set(classification["document_inventory"]) == {
        "nodes", "accessors", "meshes", "materials",
    }
    assert len(classification["primitive_inventory"]) == 8
    assert all(
        len(item["geometry_sha256"]) == 64
        for item in classification["primitive_inventory"]
    )
    assert len(classification["node_primitive_inventory"]["nodes"]) == 11
    assert len(classification["node_primitive_inventory"]["primitives"]) == 8
    assert all(
        set(item) == {
            "index_accessor", "material_index", "mesh_index", "mode",
            "node_index", "node_path", "position_accessor", "primitive_index",
        }
        for item in classification["node_primitive_inventory"]["primitives"]
    )


def test_packed_hps_glb_retains_named_leaf_and_material_inventory() -> None:
    decoded = decode_fixture_glb(FINAL_GLB.read_bytes())
    paths = tuple(item.node_path for item in decoded.primitive_inventory)
    assert paths == (
        "competitor_hps_1000w/reflector_hood/reflector_hood_top_box",
        "competitor_hps_1000w/reflector_hood/reflector_hood_flared_skirt",
        "competitor_hps_1000w/exhaust_flange",
        "competitor_hps_1000w/socket_bracket_assembly/socket_bracket",
        "competitor_hps_1000w/socket_bracket_assembly/ceramic_socket",
        "competitor_hps_1000w/hps_bulb_base",
        "competitor_hps_1000w/hps_bulb_glass",
        "competitor_hps_1000w/hps_inner_arc_tube",
    )
    assert len(set(paths)) == 8
    materials = decoded.document["materials"]
    assert len(materials) == 8
    assert all(material["doubleSided"] is True for material in materials)
    glass = next(material for material in materials if material["name"] == "hps_bulb_glass")
    assert glass["alphaMode"] == "BLEND"
    assert glass["pbrMetallicRoughness"]["baseColorFactor"][3] == 0.349999994
    assert all(
        material.get("alphaMode", "OPAQUE") == "OPAQUE"
        for material in materials
        if material is not glass
    )


def test_historical_v3_is_immutable_and_v4_authenticates_hps() -> None:
    hps = [asset for asset in ASSET_REGISTRY if asset.system_id == "hps"]
    assert [asset.asset_id for asset in hps] == ["hps-housing-v3"]
    manifest_path = (
        REPOSITORY
        / "src/fspm_optics/resources/data/fixture_occlusion/classification.v3.json"
    )
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    assert len(manifest_bytes) == 692248
    assert hashlib.sha256(manifest_bytes).hexdigest() == (
        "bdbd6c4ca708bcd446a0535490a642c56a321a8d1f7ef4122fee54a4d15eab7e"
    )
    assert all(item["asset_id"] != "hps-housing-v3" for item in manifest["assets"])
    assert {item["system_id"] for item in manifest["assets"]} == {
        "conventional", "proposed",
    }
    provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    boundary = provenance["scientific_boundary"]
    assert boundary["hps_fixture_occlusion_enabled"] is False
    assert boundary["classification_manifest_changed"] is False
    assert boundary["classification_manifest_byte_length"] == len(manifest_bytes)
    assert boundary["classification_manifest_sha256"] == hashlib.sha256(
        manifest_bytes
    ).hexdigest()
    assert boundary["housing_fitted_to_luminous_aperture"] is False
    assert boundary["luminous_aperture_m"] == [0.798576, 0.603504, 0.0]

    active_manifest_bytes = files("fspm_optics").joinpath(
        "resources",
        "data",
        "fixture_occlusion",
        "classification.v4.json",
    ).read_bytes()
    assert len(active_manifest_bytes) == 695743
    assert hashlib.sha256(active_manifest_bytes).hexdigest() == (
        "253a3e5bcf924d05785bc31ace2db242258fdd12b7b7ba22ff405ea0bcd866bb"
    )
    active_manifest = json.loads(active_manifest_bytes)
    assert active_manifest["assets"][:-1] == manifest["assets"]
    assert active_manifest["assets"][-1]["asset_id"] == "hps-housing-v3"

    active = validate_complete_classification_manifest()
    authenticated = load_authenticated_fixture_asset("hps-housing-v3")
    assert active[-1] is authenticated
    assert authenticated.inventory_sha256 == (
        "aa0dd3eabb398a06713a8c7522c746cbf8f966c01325e6749d292b4b4c2b91d5"
    )
    assert authenticated.classification_sha256 == (
        "3b4606d23220121cd7a9bc13bea4d3ed5c478e3de76c0e234f0050bf604971a8"
    )
    assert authenticated.classification_counts == {
        "decorative_exclusion": 0,
        "emitter": 1,
        "existing_optical_stack_duplicate": 1,
        "external_occluder": 6,
    }
    assert {
        item.inventory.node_path: item.classification
        for item in authenticated.primitives
    } == {
        "competitor_hps_1000w/reflector_hood/reflector_hood_top_box": "external_occluder",
        "competitor_hps_1000w/reflector_hood/reflector_hood_flared_skirt": "external_occluder",
        "competitor_hps_1000w/exhaust_flange": "external_occluder",
        "competitor_hps_1000w/socket_bracket_assembly/socket_bracket": "external_occluder",
        "competitor_hps_1000w/socket_bracket_assembly/ceramic_socket": "external_occluder",
        "competitor_hps_1000w/hps_bulb_base": "external_occluder",
        "competitor_hps_1000w/hps_bulb_glass": "existing_optical_stack_duplicate",
        "competitor_hps_1000w/hps_inner_arc_tube": "emitter",
    }
