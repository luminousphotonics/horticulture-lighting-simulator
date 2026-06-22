from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest

from scripts.viewer.validate_viewer_assets import (
    AnchorMetadataError,
    anchor_metadata_from_manifest,
    inspect_glb,
    validate_manifest,
    write_anchor_metadata,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_manifest(root: Path, payload: dict[str, Any]) -> Path:
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return manifest_path


def _tiny_glb(module_names: list[str] | None = None) -> bytes:
    names = module_names if module_names is not None else ["module_1"]
    accessors = []
    meshes = []
    nodes = []
    for index, name in enumerate(names):
        center_x = float(index * 100)
        accessors.append(
            {
                "componentType": 5126,
                "type": "VEC3",
                "count": 3,
                "min": [center_x - 1.0, -2.0, -1.0],
                "max": [center_x + 1.0, 2.0, 1.0],
            }
        )
        meshes.append({"primitives": [{"attributes": {"POSITION": index}, "mode": 4}]})
        nodes.append({"name": name, "mesh": index})
    json_text = json.dumps(
        {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": list(range(len(nodes)))}],
            "nodes": nodes,
            "meshes": meshes,
            "accessors": accessors,
        },
        separators=(",", ":"),
    )
    padded_length = (len(json_text.encode("utf-8")) + 3) // 4 * 4
    json_chunk = json_text.encode("utf-8").ljust(padded_length, b" ")
    header = struct.pack("<III", 0x46546C67, 2, 20 + len(json_chunk))
    chunk_header = struct.pack("<II", len(json_chunk), 0x4E4F534A)
    return header + chunk_header + json_chunk


def _valid_manifest() -> dict[str, Any]:
    return {
        "schema_version": 3,
        "system": "proposed_led_system",
        "display_name": "Proposed LED System",
        "units": "meters",
        "source_units": "millimeters",
        "viewer_scale": 0.001,
        "axis_mapping": {
            "cad_horizontal": ["x", "z"],
            "cad_vertical": "y",
            "layout_horizontal": ["x", "y"],
            "layout_vertical": "z",
        },
        "anchor_metadata": "anchors.json",
        "module_assets": {
            "centerpiece": {
                "high": "fixture_centerpiece.high.glb",
                "medium": "fixture_centerpiece.medium.glb",
                "proxy": "fixture_centerpiece.proxy.glb",
                "anchor_source": "module_nodes",
            },
            "linear2": {
                "high": "fixture_L2_linear.high.glb",
                "medium": "fixture_L2_linear.medium.glb",
                "proxy": "fixture_L2_linear.proxy.glb",
                "anchor_source": "module_nodes",
            },
            "linear3_linear": {
                "high": "fixture_L3_linear.high.glb",
                "medium": "fixture_L3_linear.medium.glb",
                "proxy": "fixture_L3_linear.proxy.glb",
                "anchor_source": "module_nodes",
            },
            "linear3_corner": {
                "high": "fixture_L3.high.glb",
                "medium": "fixture_L3.medium.glb",
                "proxy": "fixture_L3.proxy.glb",
                "anchor_source": "module_nodes",
                "optional": True,
                "fallback_asset_key": "linear3_linear",
            },
            "linear4_linear": {
                "high": "fixture_L4_linear.high.glb",
                "medium": "fixture_L4_linear.medium.glb",
                "proxy": "fixture_L4_linear.proxy.glb",
                "anchor_source": "module_nodes",
            },
            "l4_corner": {
                "high": "fixture_L4.high.glb",
                "medium": "fixture_L4.medium.glb",
                "proxy": "fixture_L4.proxy.glb",
                "anchor_source": "module_nodes",
            },
            "l4_reverse_corner": {
                "high": "fixture_L4_reverse.high.glb",
                "medium": "fixture_L4_reverse.medium.glb",
                "proxy": "fixture_L4_reverse.proxy.glb",
                "anchor_source": "module_nodes",
            },
        },
        "notes": [],
    }


def _simple_manifest(system: str = "conventional_led_system") -> dict[str, Any]:
    display_name = "Conventional LED System"
    note = "Single fixture asset used for direct fixture-to-fixture placement."
    if system == "hps_1000w_system":
        display_name = "1000W HPS System"
        note = "Single HPS fixture asset used for direct fixture-to-fixture placement."
    return {
        "schema_version": 1,
        "system": system,
        "display_name": display_name,
        "units": "meters",
        "source_units": "millimeters",
        "viewer_scale": 0.001,
        "placement_strategy": "single_fixture_center",
        "axis_mapping": {
            "cad_horizontal": ["x", "z"],
            "cad_vertical": "y",
            "layout_horizontal": ["x", "y"],
            "layout_vertical": "z",
        },
        "mount_orientation": "leds_down",
        "assets": {
            "fixture": {
                "high": "fixture.high.glb",
                "medium": "fixture.medium.glb",
                "proxy": "fixture.proxy.glb",
            }
        },
        "notes": [note],
    }


def _manifest_filenames(manifest: dict[str, object]) -> set[str]:
    module_assets = manifest["module_assets"]
    assert isinstance(module_assets, dict)
    filenames: set[str] = set()
    for asset in module_assets.values():
        assert isinstance(asset, dict)
        for key in ("high", "medium", "proxy"):
            value = asset[key]
            assert isinstance(value, str)
            filenames.add(value)
    return filenames


def _asset_key_for_filename(manifest: dict[str, object], filename: str) -> str | None:
    module_assets = manifest["module_assets"]
    assert isinstance(module_assets, dict)
    for asset_key, asset in module_assets.items():
        assert isinstance(asset, dict)
        if asset.get("high") == filename:
            assert isinstance(asset_key, str)
            return asset_key
    return None


def _simple_manifest_filenames(manifest: dict[str, object]) -> set[str]:
    assets = manifest["assets"]
    assert isinstance(assets, dict)
    filenames: set[str] = set()
    for asset in assets.values():
        assert isinstance(asset, dict)
        for key in ("high", "medium", "proxy"):
            value = asset[key]
            assert isinstance(value, str)
            filenames.add(value)
    return filenames


def _anchor_names_for_asset(asset_key: str) -> list[str]:
    counts = {
        "centerpiece": 5,
        "linear2": 2,
        "linear3_linear": 3,
        "linear3_corner": 3,
        "linear4_linear": 4,
        "l4_corner": 4,
        "l4_reverse_corner": 4,
    }
    start = 0 if asset_key == "centerpiece" else 1
    return [f"module_{index}" for index in range(start, start + counts[asset_key])]


def _write_assets(root: Path, manifest: dict[str, object], *, omit: set[str] | None = None) -> None:
    omitted = omit or set()
    for filename in _manifest_filenames(manifest) - omitted:
        asset_key = _asset_key_for_filename(manifest, filename)
        names = _anchor_names_for_asset(asset_key) if asset_key else ["module_1"]
        (root / filename).write_bytes(_tiny_glb(names))


def _write_simple_assets(root: Path, manifest: dict[str, object], *, omit: set[str] | None = None) -> None:
    omitted = omit or set()
    for filename in _simple_manifest_filenames(manifest) - omitted:
        (root / filename).write_bytes(_tiny_glb(["fixture_body"]))


def _write_optimized_assets_without_module_nodes(root: Path, manifest: dict[str, object]) -> None:
    for filename in _manifest_filenames(manifest):
        (root / filename).write_bytes(_tiny_glb([]))


def _write_anchor_sidecar(root: Path, manifest_path: Path) -> None:
    payload = anchor_metadata_from_manifest(manifest_path)
    (root / "anchors.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_raw_high_assets(root: Path, manifest: dict[str, object]) -> None:
    module_assets = manifest["module_assets"]
    assert isinstance(module_assets, dict)
    for asset_key, asset in module_assets.items():
        assert isinstance(asset_key, str)
        assert isinstance(asset, dict)
        high = asset["high"]
        assert isinstance(high, str)
        (root / high).write_bytes(_tiny_glb(_anchor_names_for_asset(asset_key)))


def test_valid_manifest_reports_asset_sizes_and_glb_stats(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    _write_assets(tmp_path, manifest)
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_anchor_sidecar(tmp_path, manifest_path)

    report = validate_manifest(manifest_path)

    assert report.ok
    assert not report.warnings
    assert len(report.asset_infos) == len(_manifest_filenames(manifest))
    high_infos = [asset for asset in report.asset_infos if asset.key.endswith(".high")]
    assert {asset.mesh_count for asset in high_infos} == {2, 3, 4, 5}
    assert all(asset.primitive_count == asset.mesh_count for asset in high_infos)
    assert all(asset.vertex_count == asset.mesh_count * 3 for asset in high_infos)
    assert all(asset.triangle_count == asset.mesh_count for asset in high_infos)
    assert all(not asset.compression_extensions for asset in high_infos)


@pytest.mark.parametrize(
    "manifest_path",
    [
        REPO_ROOT / "src/rad_rebuild/web/static/viewer/proposed_led_system/manifest.json",
        REPO_ROOT / "src/rad_rebuild/web/static/viewer/conventional_led_system/manifest.json",
        REPO_ROOT / "src/rad_rebuild/web/static/viewer/hps_1000w_system/manifest.json",
    ],
)
def test_static_viewer_manifests_validate(manifest_path: Path) -> None:
    report = validate_manifest(manifest_path)

    assert report.ok
    assert report.asset_infos


@pytest.mark.parametrize("system", ["conventional_led_system", "hps_1000w_system"])
def test_simple_fixture_manifest_reports_asset_sizes_and_glb_stats(tmp_path: Path, system: str) -> None:
    manifest = _simple_manifest(system)
    _write_simple_assets(tmp_path, manifest)
    manifest_path = _write_manifest(tmp_path, manifest)

    report = validate_manifest(manifest_path)

    assert report.ok
    assert not report.warnings
    assert len(report.asset_infos) == 3
    high_info = next(asset for asset in report.asset_infos if asset.key == "fixture.high")
    assert high_info.mesh_count == 1
    assert high_info.primitive_count == 1
    assert high_info.vertex_count == 3
    assert high_info.triangle_count == 1
    assert high_info.anchors == ()


@pytest.mark.parametrize("system", ["conventional_led_system", "hps_1000w_system"])
def test_simple_fixture_manifest_does_not_require_anchor_sidecar(tmp_path: Path, system: str) -> None:
    manifest = _simple_manifest(system)
    _write_simple_assets(tmp_path, manifest)
    manifest_path = _write_manifest(tmp_path, manifest)

    report = validate_manifest(manifest_path)

    assert report.ok
    assert not (tmp_path / "anchors.json").exists()


def test_simple_fixture_manifest_rejects_missing_high_asset(tmp_path: Path) -> None:
    manifest = _simple_manifest()
    _write_simple_assets(tmp_path, manifest, omit={"fixture.high.glb"})
    manifest_path = _write_manifest(tmp_path, manifest)

    report = validate_manifest(manifest_path)

    assert not report.ok
    assert any("fixture asset 'fixture' 'high' is missing" in error for error in report.errors)


def test_simple_fixture_missing_medium_and_proxy_lods_are_warnings(tmp_path: Path) -> None:
    manifest = _simple_manifest()
    _write_simple_assets(tmp_path, manifest, omit={"fixture.medium.glb", "fixture.proxy.glb"})
    manifest_path = _write_manifest(tmp_path, manifest)

    report = validate_manifest(manifest_path)

    assert report.ok
    assert any("fixture.medium.glb" in warning for warning in report.warnings)
    assert any("fixture.proxy.glb" in warning for warning in report.warnings)


def test_simple_fixture_missing_medium_and_proxy_lods_fail_in_strict_lod_mode(tmp_path: Path) -> None:
    manifest = _simple_manifest()
    _write_simple_assets(tmp_path, manifest, omit={"fixture.medium.glb", "fixture.proxy.glb"})
    manifest_path = _write_manifest(tmp_path, manifest)

    report = validate_manifest(manifest_path, strict_lods=True)

    assert not report.ok
    assert any("fixture.medium.glb" in error for error in report.errors)
    assert any("fixture.proxy.glb" in error for error in report.errors)


def test_manifest_rejects_missing_required_high_asset(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    _write_assets(tmp_path, manifest, omit={"fixture_centerpiece.high.glb"})
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_anchor_sidecar(tmp_path, manifest_path)

    report = validate_manifest(manifest_path)

    assert not report.ok
    assert any("fixture_centerpiece.high.glb" in error for error in report.errors)


def test_manifest_rejects_unsafe_asset_path(tmp_path: Path) -> None:
    payload = _valid_manifest()
    module_assets = dict(payload["module_assets"])  # type: ignore[arg-type]
    centerpiece = dict(module_assets["centerpiece"])  # type: ignore[arg-type]
    centerpiece["high"] = "../private.step"
    module_assets["centerpiece"] = centerpiece
    payload["module_assets"] = module_assets
    manifest_path = _write_manifest(tmp_path, payload)
    (tmp_path / "anchors.json").write_text(
        json.dumps({"schema_version": 1, "system": "proposed_led_system", "units": "meters", "viewer_scale": 0.001}),
        encoding="utf-8",
    )

    report = validate_manifest(manifest_path)

    assert not report.ok
    assert "module asset 'centerpiece' 'high' must be a safe relative path" in report.errors


def test_manifest_allows_missing_optional_high_when_fallback_exists(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    _write_assets(tmp_path, manifest)
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_anchor_sidecar(tmp_path, manifest_path)
    (tmp_path / "fixture_L3.high.glb").unlink()

    report = validate_manifest(manifest_path)

    assert report.ok
    assert any("optional module asset 'linear3_corner' high GLB is missing" in warning for warning in report.warnings)


def test_optimized_glbs_without_embedded_module_anchors_pass_with_valid_sidecar(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    _write_assets(tmp_path, manifest)
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_anchor_sidecar(tmp_path, manifest_path)
    _write_optimized_assets_without_module_nodes(tmp_path, manifest)

    report = validate_manifest(manifest_path)

    assert report.ok
    assert any("high GLB exposes 0 embedded module anchors" in warning for warning in report.warnings)


def test_missing_anchor_sidecar_fails_validation(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    _write_assets(tmp_path, manifest)
    manifest_path = _write_manifest(tmp_path, manifest)

    report = validate_manifest(manifest_path)

    assert not report.ok
    assert any("anchor metadata sidecar is missing" in error for error in report.errors)


def test_incomplete_anchor_sidecar_fails_validation(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    _write_assets(tmp_path, manifest)
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_anchor_sidecar(tmp_path, manifest_path)
    sidecar_path = tmp_path / "anchors.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["anchors_m"]["linear4_linear"] = sidecar["anchors_m"]["linear4_linear"][:2]
    sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")

    report = validate_manifest(manifest_path)

    assert not report.ok
    assert "anchor metadata for 'linear4_linear' must contain 4 anchors, found 2" in report.errors


def test_write_anchors_refuses_empty_generated_anchors_by_default(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    _write_assets(tmp_path, manifest)
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_anchor_sidecar(tmp_path, manifest_path)
    _write_optimized_assets_without_module_nodes(tmp_path, manifest)
    sidecar_path = tmp_path / "anchors.json"
    original = sidecar_path.read_text(encoding="utf-8")

    with pytest.raises(AnchorMetadataError, match="refusing to write incomplete anchor metadata"):
        write_anchor_metadata(manifest_path, sidecar_path)

    assert sidecar_path.read_text(encoding="utf-8") == original


def test_anchor_source_dir_generates_anchors_from_raw_glbs(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    _write_optimized_assets_without_module_nodes(tmp_path, manifest)
    manifest_path = _write_manifest(tmp_path, manifest)
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    _write_raw_high_assets(raw_root, manifest)

    metadata = write_anchor_metadata(manifest_path, tmp_path / "anchors.json", anchor_source_dir=raw_root)
    report = validate_manifest(manifest_path)

    assert [anchor["name"] for anchor in metadata["anchors_m"]["centerpiece"]] == [
        "module_0",
        "module_1",
        "module_2",
        "module_3",
        "module_4",
    ]
    assert report.ok


def test_missing_medium_and_proxy_lods_are_warnings(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    high_files = {
        filename
        for filename in _manifest_filenames(manifest)
        if filename.endswith(".high.glb")
    }
    _write_assets(tmp_path, manifest, omit=_manifest_filenames(manifest) - high_files)
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_anchor_sidecar(tmp_path, manifest_path)

    report = validate_manifest(manifest_path)

    assert report.ok
    assert any("fixture_centerpiece.medium.glb" in warning for warning in report.warnings)
    assert any("fixture_centerpiece.proxy.glb" in warning for warning in report.warnings)


def test_missing_required_medium_and_proxy_lods_fail_in_strict_lod_mode(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    high_files = {
        filename
        for filename in _manifest_filenames(manifest)
        if filename.endswith(".high.glb")
    }
    _write_assets(tmp_path, manifest, omit=_manifest_filenames(manifest) - high_files)
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_anchor_sidecar(tmp_path, manifest_path)

    report = validate_manifest(manifest_path, strict_lods=True)

    assert not report.ok
    assert any("fixture_centerpiece.medium.glb" in error for error in report.errors)
    assert any("fixture_centerpiece.proxy.glb" in error for error in report.errors)


def test_missing_optional_inspection_tool_is_warning_unless_strict(tmp_path: Path, monkeypatch) -> None:
    manifest = _valid_manifest()
    _write_assets(tmp_path, manifest)
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_anchor_sidecar(tmp_path, manifest_path)
    monkeypatch.setattr("scripts.viewer.validate_viewer_assets.shutil.which", lambda _command: None)

    relaxed = validate_manifest(manifest_path, inspect_assets=True)
    strict = validate_manifest(manifest_path, inspect_assets=True, strict_tools=True)

    assert relaxed.ok
    assert relaxed.warnings == ["optional tool missing: gltf-transform"]
    assert not strict.ok
    assert strict.errors == ["optional tool missing: gltf-transform"]


def test_extra_l3_high_glb_is_accepted_only_when_mapped(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    module_assets = dict(manifest["module_assets"])  # type: ignore[arg-type]
    module_assets.pop("linear3_corner")
    manifest["module_assets"] = module_assets
    _write_assets(tmp_path, manifest)
    (tmp_path / "fixture_L3.high.glb").write_bytes(_tiny_glb(["module_1", "module_2", "module_3"]))
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_anchor_sidecar(tmp_path, manifest_path)

    report = validate_manifest(manifest_path)

    assert not report.ok
    assert "GLB file is present but not mapped in module_assets: fixture_L3.high.glb" in report.errors


def test_builtin_anchor_extraction_sorts_module_numbers(tmp_path: Path) -> None:
    glb_path = tmp_path / "fixture.high.glb"
    glb_path.write_bytes(_tiny_glb(["module_10", "module_2", "module_1"]))

    inspection = inspect_glb(glb_path, viewer_scale=0.001)

    assert [anchor.name for anchor in inspection.anchors] == ["module_1", "module_2", "module_10"]
    assert [anchor.x for anchor in inspection.anchors] == [0.2, 0.1, 0.0]


def test_builtin_anchor_extraction_groups_module_submeshes(tmp_path: Path) -> None:
    glb_path = tmp_path / "fixture.high.glb"
    glb_path.write_bytes(_tiny_glb(["module_1_body", "module_1_leds", "module_2_body"]))

    inspection = inspect_glb(glb_path, viewer_scale=0.001)

    assert [anchor.name for anchor in inspection.anchors] == ["module_1", "module_2"]
    assert [anchor.x for anchor in inspection.anchors] == [0.05, 0.2]
