from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from fspm_optics.fixtures.occlusion.gltf import decode_fixture_glb


REPOSITORY = Path(__file__).parents[1]
CAD_ROOT = REPOSITORY / "src/fspm_optics/resources/cad/proposed"
FINAL_GLB = (
    REPOSITORY
    / "src/fspm_optics/resources/viewer/fixtures/proposed/led_module.glb"
)
PROVENANCE_PATH = CAD_ROOT / "led_module.provenance.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_standalone_module_provenance_authenticates_every_generated_artifact() -> None:
    provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    hashes = provenance["hashes"]
    assert provenance["asset_id"] == "proposed-led-module-v1"
    assert provenance["source"]["supplied_sha256"] == (
        "b2735bfe2e16d8651157355b3a00a77bd1bcf9042e5f5a9d54c0cf36d9641286"
    )
    assert hashes == {
        "inventory_sha256": (
            "325143c0c25c430288315cbab16b79eb9e0b840e8d271dbcc29d501119d7f10a"
        ),
        "packed_glb_sha256": (
            "f910a7fc512635bcb3a48d7b546175ef13623135836a76eeb625b2ca12cd5445"
        ),
        "packed_inventory_sha256": (
            "d796169a3d3c34e930c31846bbb2be6451b160081dad43ab7faa9f2bfbce70a3"
        ),
        "raw_glb_sha256": (
            "f221556c4a47d3fe58122940a0edf26376c3b3d3f924ec29d47fec024b8372f2"
        ),
        "step_sha256": (
            "e89610cd20df30adc63d1c21198438bce209b3fa429c035797533ec250eb5ab8"
        ),
    }
    assert _sha256(CAD_ROOT / "led_module_150x150.step") == hashes["step_sha256"]
    assert _sha256(CAD_ROOT / "led_module.raw.glb") == hashes["raw_glb_sha256"]
    assert _sha256(FINAL_GLB) == hashes["packed_glb_sha256"]


def test_standalone_module_mechanical_and_raw_packed_contract_is_exact() -> None:
    provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    mechanical = provenance["mechanical_assertions"]
    roundtrip = provenance["step_roundtrip_assertions"]
    raw = provenance["raw_validation"]
    packed = provenance["packed_validation"]
    assert mechanical == roundtrip
    assert mechanical["footprint_mm"] == [150.0, 150.0]
    assert mechanical["mechanical_center_xy_mm"] == [0.0, 0.0]
    assert mechanical["optical_center_xy_mm"] == [0.0, 0.0]
    assert mechanical["fin_count"] == 32
    assert mechanical["frame_rail_count"] == 4
    assert mechanical["corner_block_count"] == 4
    assert mechanical["heatsink_base_count"] == 1
    assert mechanical["opaque_cover_count"] == 1
    assert mechanical["fastener_count"] == 4
    assert mechanical["solid_count"] == 46
    assert mechanical["opaque_cover_size_mm"] == [144.0, 144.0, 3.0]
    assert mechanical["opaque_cover_light_side_cad_z_mm"] == 27.4
    assert raw["bounds_gltf_mm"] == packed["bounds_gltf_mm"]
    assert raw["projected_area_mm2"] == packed["projected_area_mm2"]
    assert raw["packed_bounds_max_residual_mm"] == 0.0
    assert raw["packed_projected_area_relative_error"] == 0.0
    assert (
        raw["degenerate_triangle_count"]
        == packed["degenerate_triangle_count"]
        == 0
    )
    assert raw["primitive_count"] == packed["primitive_count"] == 46
    assert raw["triangle_count"] == packed["triangle_count"] == 1304
    assert packed["opaque_cover_light_side_gltf_y_mm"] == pytest.approx(
        27.4,
        abs=5.0e-7,
    )


def test_packed_standalone_glb_preserves_one_named_primitive_per_leaf() -> None:
    decoded = decode_fixture_glb(FINAL_GLB.read_bytes())
    paths = tuple(item.node_path for item in decoded.primitive_inventory)
    assert len(paths) == len(set(paths)) == 46
    assert sum("module_heatsink_fin_" in path for path in paths) == 32
    assert sum("module_module_frame_" in path for path in paths) == 4
    assert sum("module_corner_mount_block_" in path for path in paths) == 4
    assert sum("module_module_screw_" in path for path in paths) == 4
    assert sum(path.endswith("/module_heatsink_base_plate") for path in paths) == 1
    assert sum(path.endswith("/module_opaque_bottom_cover") for path in paths) == 1
