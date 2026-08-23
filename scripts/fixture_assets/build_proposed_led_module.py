#!/usr/bin/env python3
"""Build and authenticate the standalone Proposed LED module STEP/GLBs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
from typing import Any

from build123d import export_step, import_step
import OCP

SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

import proposed_led_module as cad  # noqa: E402
from fspm_optics.fixtures.occlusion.gltf import (  # noqa: E402
    DecodedFixtureGlb,
    decode_fixture_glb,
    node_inventory_payload,
)

SUPPLIED_SOURCE_SHA256 = (
    "b2735bfe2e16d8651157355b3a00a77bd1bcf9042e5f5a9d54c0cf36d9641286"
)
LINEAR_TOLERANCE_MM = 0.10
ANGULAR_TOLERANCE_RAD = 0.25
EXPECTED_GROUPS = (
    "module_aluminum_heatsink",
    "module_module_frame",
    "module_opaque_bottom_cover_assembly",
    "module_module_fasteners",
)
EXPECTED_LEAVES = (
    "module_heatsink_base_plate",
    *(f"module_heatsink_fin_{index:02d}" for index in range(1, 33)),
    "module_module_frame_west",
    "module_module_frame_east",
    "module_module_frame_south",
    "module_module_frame_north",
    *(f"module_corner_mount_block_{index:02d}" for index in range(1, 5)),
    "module_opaque_bottom_cover",
    *(f"module_module_screw_{index:02d}" for index in range(1, 5)),
)
EXPECTED_PATHS = (
    *(
        f"module_led_module/module_aluminum_heatsink/{name}"
        for name in EXPECTED_LEAVES[:33]
    ),
    *(
        f"module_led_module/module_module_frame/{name}"
        for name in EXPECTED_LEAVES[33:41]
    ),
    (
        "module_led_module/module_opaque_bottom_cover_assembly/"
        "module_opaque_bottom_cover"
    ),
    *(
        f"module_led_module/module_module_fasteners/{name}"
        for name in EXPECTED_LEAVES[42:]
    ),
)


def main() -> None:
    arguments = _parser().parse_args()
    gltfpack = arguments.gltfpack.expanduser().resolve()
    if not gltfpack.is_file():
        raise SystemExit(f"gltfpack executable is missing: {gltfpack}")
    version = subprocess.run(
        [str(gltfpack), "-v"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if version != "gltfpack 0.20":
        raise SystemExit(
            f"gltfpack must be pinned to 0.20; received {version!r}."
        )
    supplied = arguments.supplied_source.expanduser().resolve()
    if _sha256_file(supplied) != SUPPLIED_SOURCE_SHA256:
        raise SystemExit("supplied led_module.py failed SHA-256 authentication.")

    output = arguments.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    final_glb = arguments.final_glb.expanduser().resolve()
    final_glb.parent.mkdir(parents=True, exist_ok=True)
    step_path = output / "led_module_150x150.step"
    raw_glb = output / "led_module.raw.glb"
    provenance_path = output / "led_module.provenance.json"

    assembly = cad.make_led_module()
    assertions = validate_cad_assembly(assembly)
    export_step(assembly, step_path)
    roundtrip = import_step(step_path)
    roundtrip_assertions = validate_cad_assembly(roundtrip)
    if _hierarchy(assembly) != _hierarchy(roundtrip):
        raise ValueError("STEP round-trip changed the named component hierarchy.")

    write_raw_glb(roundtrip, raw_glb)
    raw_validation = validate_glb(
        raw_glb,
        expected_extensions=(),
        expected_generator="fspm-optics OpenCascade named-leaf GLB writer v1",
    )
    command = (
        str(gltfpack),
        "-i",
        str(raw_glb),
        "-o",
        str(final_glb),
        "-kn",
        "-km",
        "-ke",
        "-noq",
    )
    subprocess.run(command, check=True)
    packed_validation = validate_glb(
        final_glb,
        expected_extensions=(),
        expected_generator=None,
    )
    compare_raw_packed(raw_validation, packed_validation)

    provenance = {
        "schema_id": "fspm-optics.proposed-led-module-asset-provenance",
        "schema_version": 1,
        "asset_id": "proposed-led-module-v1",
        "fixture_type": "standalone_module",
        "source": {
            "supplied_path": str(supplied),
            "supplied_sha256": SUPPLIED_SOURCE_SHA256,
            "repository_source": str(
                Path("scripts/fixture_assets/proposed_led_module.py")
            ),
            "repository_source_sha256": _sha256_file(
                SCRIPT_DIR / "proposed_led_module.py"
            ),
        },
        "toolchain": {
            "python": sys.version.split()[0],
            "build123d": importlib.metadata.version("build123d"),
            "opencascade": OCP.__version__,
            "gltfpack": version,
            "blender_used": False,
        },
        "commands": {
            "build": (
                "/home/austin/Desktop/build123d-modeling/.venv/bin/python "
                "scripts/fixture_assets/build_proposed_led_module.py "
                "--gltfpack <pinned-gltfpack-0.20>"
            ),
            "pack": " ".join(
                (
                    "gltfpack",
                    "-i",
                    "led_module.raw.glb",
                    "-o",
                    "led_module.glb",
                    "-kn",
                    "-km",
                    "-ke",
                    "-noq",
                )
            ),
        },
        "tessellation": {
            "engine": "OpenCascade via Build123d import_step/tessellate",
            "linear_tolerance_mm": LINEAR_TOLERANCE_MM,
            "angular_tolerance_rad": ANGULAR_TOLERANCE_RAD,
            "normals_policy": "explicit flat per-face normals from triangle winding",
        },
        "coordinate_contract": {
            "cad_units": "millimetres",
            "gltf_units": "millimetres",
            "meters_per_asset_unit": 0.001,
            "cad_to_gltf": "(X,Y,Z)->(X,Z,-Y)",
            "cad_origin_xy_mm": [0.0, 0.0],
            "optical_center_xy_mm": [0.0, 0.0],
            "opaque_cover_light_side_cad_z_mm": 27.4,
            "registered_gltf_up_axis": "+Y",
            "auto_center": False,
            "auto_fit": False,
            "simplification": False,
            "draco": False,
            "internal_instancing": False,
        },
        "hashes": {
            "step_sha256": _sha256_file(step_path),
            "raw_glb_sha256": _sha256_file(raw_glb),
            "packed_glb_sha256": _sha256_file(final_glb),
            "inventory_sha256": raw_validation["inventory_sha256"],
            "packed_inventory_sha256": packed_validation["inventory_sha256"],
        },
        "byte_lengths": {
            "step": step_path.stat().st_size,
            "raw_glb": raw_glb.stat().st_size,
            "packed_glb": final_glb.stat().st_size,
        },
        "mechanical_assertions": assertions,
        "step_roundtrip_assertions": roundtrip_assertions,
        "raw_validation": raw_validation,
        "packed_validation": packed_validation,
    }
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(provenance, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gltfpack", required=True, type=Path)
    parser.add_argument(
        "--supplied-source",
        type=Path,
        default=Path(
            "/home/austin/Desktop/build123d-modeling/models/led_module.py"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT
        / "src/fspm_optics/resources/cad/proposed",
    )
    parser.add_argument(
        "--final-glb",
        type=Path,
        default=REPOSITORY_ROOT
        / "src/fspm_optics/resources/viewer/fixtures/proposed/led_module.glb",
    )
    return parser


def validate_cad_assembly(assembly: Any) -> dict[str, object]:
    hierarchy = _hierarchy(assembly)
    if hierarchy != {
        "module_led_module": {
            "module_aluminum_heatsink": list(EXPECTED_LEAVES[:33]),
            "module_module_frame": list(EXPECTED_LEAVES[33:41]),
            "module_opaque_bottom_cover_assembly": [
                "module_opaque_bottom_cover"
            ],
            "module_module_fasteners": list(EXPECTED_LEAVES[42:]),
        }
    }:
        raise ValueError("constructed assembly hierarchy or component names changed.")
    leaves = _leaves(assembly)
    names = tuple(item.label for item in leaves)
    if len(names) != 46 or len(set(names)) != 46:
        raise ValueError("constructed assembly must contain 46 unique named leaves.")
    if tuple(names) != EXPECTED_LEAVES:
        raise ValueError("constructed component ordering changed.")
    if len(assembly.solids()) != 46:
        raise ValueError("constructed assembly must contain exactly 46 solids.")
    for leaf in leaves:
        if (
            not leaf.is_valid
            or len(leaf.solids()) != 1
            or not math.isfinite(float(leaf.volume))
            or float(leaf.volume) <= 0.0
        ):
            raise ValueError(f"invalid constructed solid: {leaf.label}")

    bounds = assembly.bounding_box()
    _assert_vector(bounds.min, (-75.0, -75.0, -34.8), "assembly minimum")
    _assert_vector(bounds.max, (75.0, 75.0, 28.2), "assembly maximum")
    cover = next(
        item for item in leaves if item.label == "module_opaque_bottom_cover"
    )
    cover_bounds = cover.bounding_box()
    cover_size = cover_bounds.size
    _assert_vector(cover_size, (144.0, 144.0, 3.0), "opaque cover size")
    if not math.isclose(
        cover_bounds.max.Z,
        cad.OPAQUE_COVER_LIGHT_SIDE_Z,
        rel_tol=0.0,
        abs_tol=1.0e-9,
    ):
        raise ValueError("opaque-cover light-side plane changed.")
    if (
        cad.MODULE_CENTER_X,
        cad.MODULE_CENTER_Y,
        cad.OPTICAL_CENTER_X,
        cad.OPTICAL_CENTER_Y,
    ) != (0.0, 0.0, 0.0, 0.0):
        raise ValueError("mechanical or optical center changed.")
    return {
        "bounds_cad_mm": {
            "minimum": [-75.0, -75.0, -34.8],
            "maximum": [75.0, 75.0, 28.2],
        },
        "footprint_mm": [150.0, 150.0],
        "mechanical_center_xy_mm": [0.0, 0.0],
        "optical_center_xy_mm": [0.0, 0.0],
        "solid_count": 46,
        "fin_count": 32,
        "frame_rail_count": 4,
        "corner_block_count": 4,
        "heatsink_base_count": 1,
        "opaque_cover_count": 1,
        "fastener_count": 4,
        "opaque_cover_size_mm": [144.0, 144.0, 3.0],
        "opaque_cover_light_side_cad_z_mm": 27.4,
        "unique_expected_names": True,
        "finite_valid_solids": True,
        "complete_named_hierarchy": True,
    }


def write_raw_glb(assembly: Any, path: Path) -> None:
    leaves_by_name = {item.label: item for item in _leaves(assembly)}
    binary = bytearray()
    buffer_views: list[dict[str, object]] = []
    accessors: list[dict[str, object]] = []
    meshes: list[dict[str, object]] = []

    def append_view(data: bytes, target: int) -> int:
        while len(binary) % 4:
            binary.append(0)
        offset = len(binary)
        binary.extend(data)
        buffer_views.append(
            {
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": len(data),
                "target": target,
            }
        )
        return len(buffer_views) - 1

    for name in EXPECTED_LEAVES:
        vertices, faces = leaves_by_name[name].tessellate(
            LINEAR_TOLERANCE_MM,
            ANGULAR_TOLERANCE_RAD,
        )
        canonical: list[
            tuple[tuple[float, float, float], ...]
        ] = []
        for face in faces:
            points = tuple(
                _cad_to_gltf(tuple(vertices[index])) for index in face
            )
            if _triangle_area_squared(points) <= 1.0e-20:
                raise ValueError(f"OpenCascade emitted a degenerate triangle: {name}")
            minimum = min(range(3), key=points.__getitem__)
            rotated = points[minimum:] + points[:minimum]
            canonical.append(rotated)
        canonical.sort()
        positions: list[tuple[float, float, float]] = []
        normals: list[tuple[float, float, float]] = []
        for triangle in canonical:
            normal = _normal(triangle)
            positions.extend(triangle)
            normals.extend((normal, normal, normal))
        indices = tuple(range(len(positions)))
        position_bytes = b"".join(struct.pack("<3f", *item) for item in positions)
        normal_bytes = b"".join(struct.pack("<3f", *item) for item in normals)
        index_bytes = struct.pack(f"<{len(indices)}I", *indices)
        position_view = append_view(position_bytes, 34962)
        normal_view = append_view(normal_bytes, 34962)
        index_view = append_view(index_bytes, 34963)
        position_accessor = len(accessors)
        accessors.append(
            {
                "bufferView": position_view,
                "componentType": 5126,
                "count": len(positions),
                "type": "VEC3",
                "min": [
                    min(item[axis] for item in positions) for axis in range(3)
                ],
                "max": [
                    max(item[axis] for item in positions) for axis in range(3)
                ],
            }
        )
        normal_accessor = len(accessors)
        accessors.append(
            {
                "bufferView": normal_view,
                "componentType": 5126,
                "count": len(normals),
                "type": "VEC3",
                "min": [
                    min(item[axis] for item in normals) for axis in range(3)
                ],
                "max": [
                    max(item[axis] for item in normals) for axis in range(3)
                ],
            }
        )
        index_accessor = len(accessors)
        accessors.append(
            {
                "bufferView": index_view,
                "componentType": 5125,
                "count": len(indices),
                "type": "SCALAR",
                "min": [0],
                "max": [len(indices) - 1],
            }
        )
        meshes.append(
            {
                "name": name,
                "extras": {"cad_leaf_name": name},
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": position_accessor,
                            "NORMAL": normal_accessor,
                        },
                        "indices": index_accessor,
                        "material": _material_index(name),
                        "mode": 4,
                    }
                ],
            }
        )

    nodes: list[dict[str, object]] = []

    def add_node(name: str, *, children: list[int] | None = None, mesh: int | None = None) -> int:
        node: dict[str, object] = {"name": name}
        if children is not None:
            node["children"] = children
        if mesh is not None:
            node["mesh"] = mesh
        nodes.append(node)
        return len(nodes) - 1

    mesh_by_name = {name: index for index, name in enumerate(EXPECTED_LEAVES)}
    group_indices: list[int] = []
    slices = (
        ("module_aluminum_heatsink", EXPECTED_LEAVES[:33]),
        ("module_module_frame", EXPECTED_LEAVES[33:41]),
        ("module_opaque_bottom_cover_assembly", EXPECTED_LEAVES[41:42]),
        ("module_module_fasteners", EXPECTED_LEAVES[42:]),
    )
    for group_name, leaf_names in slices:
        leaf_nodes = [
            add_node(name, mesh=mesh_by_name[name]) for name in leaf_names
        ]
        group_indices.append(add_node(group_name, children=leaf_nodes))
    root = add_node("module_led_module", children=group_indices)
    materials = [
        _material(
            "anodized_aluminum",
            (0.72, 0.72, 0.68, 1.0),
            metallic=1.0,
            roughness=0.35,
        ),
        _material(
            "opaque_bottom_cover",
            (0.90, 0.90, 0.84, 1.0),
            metallic=0.0,
            roughness=0.60,
        ),
        _material(
            "dark_fastener",
            (0.04, 0.04, 0.04, 1.0),
            metallic=0.80,
            roughness=0.35,
        ),
    ]
    document = {
        "asset": {
            "version": "2.0",
            "generator": "fspm-optics OpenCascade named-leaf GLB writer v1",
            "extras": {
                "cad_units": "millimetres",
                "cad_to_gltf_axis_conversion": "(X,Y,Z)->(X,Z,-Y)",
                "linear_tolerance_mm": LINEAR_TOLERANCE_MM,
                "angular_tolerance_rad": ANGULAR_TOLERANCE_RAD,
            },
        },
        "scene": 0,
        "scenes": [{"name": "led_module", "nodes": [root]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(binary)}],
    }
    _write_glb(path, document, bytes(binary))


def validate_glb(
    path: Path,
    *,
    expected_extensions: tuple[str, ...],
    expected_generator: str | None,
) -> dict[str, object]:
    data = path.read_bytes()
    decoded = decode_fixture_glb(data)
    document = decoded.document
    used = tuple(document.get("extensionsUsed", ()))
    required = tuple(document.get("extensionsRequired", ()))
    if set(used) - set(expected_extensions) or set(required) - set(
        expected_extensions
    ):
        raise ValueError(f"{path.name} uses an unsupported extension.")
    if (
        document.get("animations")
        or document.get("cameras")
        or _contains_key(document, "uri")
        or _contains_key(document, "KHR_lights_punctual")
    ):
        raise ValueError(f"{path.name} contains prohibited GLB content.")
    if expected_generator is not None and (
        document.get("asset", {}).get("generator") != expected_generator
    ):
        raise ValueError(f"{path.name} generator identity changed.")
    transformed_nodes = [
        node
        for node in document["nodes"]
        if any(key in node for key in ("matrix", "translation", "rotation", "scale"))
    ]
    if expected_extensions:
        if (
            len(transformed_nodes) != 46
            or any(
                set(node) != {"mesh", "translation", "scale"}
                or len(node["translation"]) != 3
                or len(node["scale"]) != 3
                or not math.isclose(
                    float(node["scale"][0]),
                    float(node["scale"][1]),
                    rel_tol=0.0,
                    abs_tol=1.0e-12,
                )
                or not math.isclose(
                    float(node["scale"][1]),
                    float(node["scale"][2]),
                    rel_tol=0.0,
                    abs_tol=1.0e-12,
                )
                for node in transformed_nodes
            )
        ):
            raise ValueError(
                f"{path.name} quantization decode transforms are incompatible."
            )
    elif transformed_nodes:
        raise ValueError(f"{path.name} contains an unexpected node transform.")
    inventory = decoded.primitive_inventory
    if (
        len(inventory) != 46
        or tuple(item.node_path for item in inventory) != EXPECTED_PATHS
    ):
        raise ValueError(f"{path.name} named primitive hierarchy changed.")
    meshes = document.get("meshes")
    if not isinstance(meshes, list) or len(meshes) != 46:
        raise ValueError(f"{path.name} must retain one mesh per leaf.")
    triangles = [
        triangle
        for primitive in inventory
        for triangle in decoded.triangles(primitive)
    ]
    if any(
        not all(math.isfinite(value) for point in triangle for value in point)
        or _triangle_area_squared(triangle) <= 1.0e-20
        for triangle in triangles
    ):
        raise ValueError(f"{path.name} contains invalid or degenerate triangles.")
    for primitive in inventory:
        raw = meshes[primitive.mesh_index]["primitives"][primitive.primitive_index]
        normal_index = raw.get("attributes", {}).get("NORMAL")
        if not isinstance(normal_index, int):
            raise ValueError(f"{path.name} primitive normals are missing.")
        normals = decoded._accessor_values(normal_index)
        if any(
            len(item) != 3
            or not all(math.isfinite(float(value)) for value in item)
            or not math.isclose(
                math.sqrt(sum(float(value) ** 2 for value in item)),
                1.0,
                rel_tol=0.0,
                abs_tol=5.0e-3,
            )
            for item in normals
        ):
            raise ValueError(f"{path.name} normals are invalid.")
    bounds = _bounds(triangles)
    expected_bounds = ((-75.0, -34.8, -75.0), (75.0, 28.2, 75.0))
    for actual, expected in zip(bounds[0] + bounds[1], sum(expected_bounds, ())):
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=2.0e-4):
            raise ValueError(f"{path.name} full bounds changed.")
    cover = inventory[41]
    cover_bounds = _bounds(decoded.triangles(cover))
    expected_cover = ((-72.0, 24.4, -72.0), (72.0, 27.4, 72.0))
    for actual, expected in zip(
        cover_bounds[0] + cover_bounds[1], sum(expected_cover, ())
    ):
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=2.0e-4):
            raise ValueError(f"{path.name} opaque cover bounds changed.")
    projected_area = 0.5 * math.fsum(
        abs(
            (triangle[1][0] - triangle[0][0])
            * (triangle[2][2] - triangle[0][2])
            - (triangle[1][2] - triangle[0][2])
            * (triangle[2][0] - triangle[0][0])
        )
        for triangle in triangles
    )
    inventory_sha256 = _hash_json(node_inventory_payload(decoded))
    return {
        "byte_length": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "node_count": len(document["nodes"]),
        "mesh_count": len(meshes),
        "primitive_count": len(inventory),
        "triangle_count": len(triangles),
        "inventory_sha256": inventory_sha256,
        "extensions_used": list(used),
        "extensions_required": list(required),
        "bounds_gltf_mm": [list(bounds[0]), list(bounds[1])],
        "opaque_cover_bounds_gltf_mm": [
            list(cover_bounds[0]),
            list(cover_bounds[1]),
        ],
        "opaque_cover_light_side_gltf_y_mm": cover_bounds[1][1],
        "projected_area_mm2": projected_area,
        "indexed_finite_triangles": True,
        "finite_unit_normals": True,
        "embedded_data_only": True,
        "unexpected_transforms": False,
        "quantization_decode_transform_count": len(transformed_nodes),
        "degenerate_triangle_count": 0,
    }


def compare_raw_packed(
    raw: dict[str, object], packed: dict[str, object]
) -> None:
    for key in ("mesh_count", "primitive_count"):
        if raw[key] != packed[key]:
            raise ValueError(f"packed GLB changed {key}.")
    raw_bounds = raw["bounds_gltf_mm"]
    packed_bounds = packed["bounds_gltf_mm"]
    if not isinstance(raw_bounds, list) or not isinstance(packed_bounds, list):
        raise ValueError("GLB validation bounds are malformed.")
    max_residual = max(
        abs(float(left) - float(right))
        for raw_row, packed_row in zip(raw_bounds, packed_bounds, strict=True)
        for left, right in zip(raw_row, packed_row, strict=True)
    )
    raw_area = float(raw["projected_area_mm2"])
    packed_area = float(packed["projected_area_mm2"])
    area_relative = abs(packed_area - raw_area) / raw_area
    if max_residual > 2.0e-4 or area_relative > 2.0e-5:
        raise ValueError("raw/packed GLB geometry agreement exceeded tolerance.")
    raw["packed_bounds_max_residual_mm"] = max_residual
    raw["packed_projected_area_relative_error"] = area_relative


def _hierarchy(node: Any) -> dict[str, object]:
    return {
        node.label: (
            [_child.label for _child in node.children]
            if node.children and not any(child.children for child in node.children)
            else {
                child.label: [leaf.label for leaf in child.children]
                for child in node.children
            }
        )
    }


def _leaves(node: Any) -> list[Any]:
    output: list[Any] = []
    for child in node.children:
        if child.children:
            output.extend(_leaves(child))
        else:
            output.append(child)
    return output


def _cad_to_gltf(
    point: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (float(point[0]), float(point[2]), -float(point[1]))


def _normal(
    triangle: tuple[tuple[float, float, float], ...],
) -> tuple[float, float, float]:
    first = tuple(triangle[1][axis] - triangle[0][axis] for axis in range(3))
    second = tuple(triangle[2][axis] - triangle[0][axis] for axis in range(3))
    cross = (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
    length = math.sqrt(sum(value * value for value in cross))
    if length <= 1.0e-12:
        raise ValueError("cannot compute a normal for a degenerate triangle.")
    return tuple(value / length for value in cross)  # type: ignore[return-value]


def _triangle_area_squared(
    triangle: tuple[tuple[float, float, float], ...],
) -> float:
    first = tuple(triangle[1][axis] - triangle[0][axis] for axis in range(3))
    second = tuple(triangle[2][axis] - triangle[0][axis] for axis in range(3))
    cross = (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
    return 0.25 * sum(value * value for value in cross)


def _bounds(
    triangles: list[Any] | tuple[Any, ...],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    points = tuple(point for triangle in triangles for point in triangle)
    return (
        tuple(min(point[axis] for point in points) for axis in range(3)),
        tuple(max(point[axis] for point in points) for axis in range(3)),
    )  # type: ignore[return-value]


def _material(
    name: str,
    color: tuple[float, float, float, float],
    *,
    metallic: float,
    roughness: float,
) -> dict[str, object]:
    return {
        "name": name,
        "pbrMetallicRoughness": {
            "baseColorFactor": list(color),
            "metallicFactor": metallic,
            "roughnessFactor": roughness,
        },
        "alphaMode": "OPAQUE",
        "doubleSided": True,
    }


def _material_index(name: str) -> int:
    if "opaque_bottom_cover" in name:
        return 1
    if "module_screw" in name:
        return 2
    return 0


def _write_glb(path: Path, document: dict[str, object], binary: bytes) -> None:
    json_chunk = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    json_chunk += b" " * (-len(json_chunk) % 4)
    binary += b"\x00" * (-len(binary) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(binary)
    data = (
        struct.pack("<III", 0x46546C67, 2, total)
        + struct.pack("<II", len(json_chunk), 0x4E4F534A)
        + json_chunk
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )
    path.write_bytes(data)


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def _assert_vector(actual: Any, expected: tuple[float, ...], label: str) -> None:
    values = (float(actual.X), float(actual.Y), float(actual.Z))
    if any(
        not math.isclose(left, right, rel_tol=0.0, abs_tol=1.0e-9)
        for left, right in zip(values, expected, strict=True)
    ):
        raise ValueError(f"{label} changed: {values!r}.")


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
