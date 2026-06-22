#!/usr/bin/env python3
"""Validate static viewer asset manifests and referenced GLB files.

CadQuery, gltfpack, and gltf-transform are optional developer tooling for
preparing and inspecting static viewer assets. They are intentionally not
runtime dependencies of the packaged web app.

Anchors are intentionally sidecar metadata: extract or maintain anchors from
source/raw CAD exports, then optimize runtime GLBs without requiring them to
retain module_# node names. The viewer treats anchors.json as the source of
truth for CAD module anchors.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROPOSED_SYSTEM = "proposed_led_system"
SIMPLE_FIXTURE_SYSTEMS = frozenset({"conventional_led_system", "hps_1000w_system"})
REQUIRED_MODULE_ASSET_KEYS = (
    "centerpiece",
    "linear2",
    "linear3_linear",
    "linear3_corner",
    "linear4_linear",
    "l4_corner",
    "l4_reverse_corner",
)
REQUIRED_SIMPLE_ASSET_KEYS = ("fixture",)
LOD_KEYS = ("high", "medium", "proxy")
PROPOSED_REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    "schema_version": int,
    "system": str,
    "display_name": str,
    "units": str,
    "source_units": str,
    "viewer_scale": (int, float),
    "axis_mapping": dict,
    "module_assets": dict,
    "anchor_metadata": str,
    "notes": list,
}
SIMPLE_REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    "schema_version": int,
    "system": str,
    "display_name": str,
    "units": str,
    "source_units": str,
    "viewer_scale": (int, float),
    "placement_strategy": str,
    "axis_mapping": dict,
    "mount_orientation": str,
    "assets": dict,
    "notes": list,
}
EXPECTED_AXIS_MAPPING = {
    "cad_horizontal": ["x", "z"],
    "cad_vertical": "y",
    "layout_horizontal": ["x", "y"],
    "layout_vertical": "z",
}
EXPECTED_ANCHOR_COUNTS = {
    "centerpiece": 5,
    "linear2": 2,
    "linear3_linear": 3,
    "linear3_corner": 3,
    "linear4_linear": 4,
    "l4_corner": 4,
    "l4_reverse_corner": 4,
}
DEFAULT_HIGH_SIZE_THRESHOLD_BYTES = 25 * 1024 * 1024
MODULE_NODE_PATTERN = re.compile(r"^module_(\d+)(?:_|$)", re.IGNORECASE)
GLB_MAGIC = 0x46546C67
GLB_JSON_CHUNK = 0x4E4F534A
GLB_BIN_CHUNK = 0x004E4942
COMPONENT_FORMATS = {
    5120: ("b", 1),
    5121: ("B", 1),
    5122: ("h", 2),
    5123: ("H", 2),
    5125: ("I", 4),
    5126: ("f", 4),
}
ACCESSOR_COMPONENTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}


@dataclass(frozen=True)
class AnchorInfo:
    name: str
    x: float
    z: float
    vertical_y: float


@dataclass(frozen=True)
class GlbInspection:
    mesh_count: int
    primitive_count: int
    vertex_count: int
    triangle_count: int
    extensions_used: tuple[str, ...]
    compression_extensions: tuple[str, ...]
    anchors: tuple[AnchorInfo, ...]


@dataclass(frozen=True)
class AssetInfo:
    key: str
    path: Path
    size_bytes: int
    mesh_count: int = 0
    primitive_count: int = 0
    vertex_count: int = 0
    triangle_count: int = 0
    extensions_used: tuple[str, ...] = ()
    compression_extensions: tuple[str, ...] = ()
    anchors: tuple[AnchorInfo, ...] = ()


@dataclass
class ValidationReport:
    manifest_path: Path
    asset_infos: list[AssetInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    inspections: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


class GlbInspectionError(ValueError):
    """Raised when a GLB cannot be inspected by the built-in reader."""


class AnchorMetadataError(ValueError):
    """Raised when anchor metadata cannot be safely generated or written."""


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _node_vector(node: dict[str, Any], field_name: str, fallback: list[float], length: int) -> list[float]:
    raw_value = node.get(field_name)
    if not isinstance(raw_value, list) or len(raw_value) < length:
        return fallback
    return [float(raw_value[index]) for index in range(length)]


def _relative_asset_path(raw_path: object, *, key: str) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    if any(part in {"", "."} for part in candidate.parts):
        return None
    return candidate


def _load_manifest(manifest_path: Path, report: ValidationReport) -> dict[str, Any] | None:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        report.errors.append(f"manifest is missing: {manifest_path}")
        return None
    except json.JSONDecodeError as exc:
        report.errors.append(f"manifest is not valid JSON: {exc}")
        return None
    if not isinstance(data, dict):
        report.errors.append("manifest root must be a JSON object")
        return None
    return data


def _validate_required_fields(
    manifest: dict[str, Any],
    report: ValidationReport,
    required_fields: dict[str, type | tuple[type, ...]],
) -> None:
    for field_name, expected_type in required_fields.items():
        if field_name not in manifest:
            report.errors.append(f"manifest is missing required field: {field_name}")
            continue
        if not isinstance(manifest[field_name], expected_type):
            report.errors.append(f"manifest field {field_name!r} has the wrong type")


def _validate_common_structure(manifest: dict[str, Any], report: ValidationReport) -> None:
    viewer_scale = manifest.get("viewer_scale")
    if isinstance(viewer_scale, int | float) and viewer_scale <= 0:
        report.errors.append("manifest field 'viewer_scale' must be positive")

    notes = manifest.get("notes")
    if isinstance(notes, list) and not all(isinstance(note, str) for note in notes):
        report.errors.append("manifest field 'notes' must contain only strings")

    axis_mapping = manifest.get("axis_mapping")
    if isinstance(axis_mapping, dict) and axis_mapping != EXPECTED_AXIS_MAPPING:
        report.errors.append("manifest field 'axis_mapping' does not match the viewer coordinate contract")


def _validate_structure(manifest: dict[str, Any], report: ValidationReport) -> None:
    system = manifest.get("system")
    if system == PROPOSED_SYSTEM:
        _validate_required_fields(manifest, report, PROPOSED_REQUIRED_FIELDS)
        if manifest.get("schema_version") != 3:
            report.errors.append("manifest field 'schema_version' must be 3 for proposed_led_system")
    elif system in SIMPLE_FIXTURE_SYSTEMS:
        _validate_required_fields(manifest, report, SIMPLE_REQUIRED_FIELDS)
        if manifest.get("schema_version") != 1:
            report.errors.append(f"manifest field 'schema_version' must be 1 for {system}")
        if manifest.get("placement_strategy") != "single_fixture_center":
            report.errors.append("manifest field 'placement_strategy' must be 'single_fixture_center'")
        mount_orientation = manifest.get("mount_orientation")
        if isinstance(mount_orientation, str) and not mount_orientation.strip():
            report.errors.append("manifest field 'mount_orientation' must be non-empty")
    else:
        if not isinstance(system, str):
            report.errors.append("manifest field 'system' has the wrong type")
        else:
            report.errors.append(f"manifest field 'system' is not supported: {system}")
    _validate_common_structure(manifest, report)


def _load_glb(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise GlbInspectionError(f"unable to read GLB: {exc}") from exc
    if len(payload) < 20:
        raise GlbInspectionError("GLB is too small")
    magic, version, declared_length = struct.unpack_from("<III", payload, 0)
    if magic != GLB_MAGIC or version != 2:
        raise GlbInspectionError("file is not a binary glTF 2.0 GLB")
    if declared_length > len(payload):
        raise GlbInspectionError("GLB declared length exceeds file size")

    offset = 12
    json_chunk: bytes | None = None
    bin_chunk = b""
    while offset + 8 <= len(payload):
        chunk_length, chunk_type = struct.unpack_from("<II", payload, offset)
        offset += 8
        chunk = payload[offset : offset + chunk_length]
        offset += chunk_length
        if chunk_type == GLB_JSON_CHUNK:
            json_chunk = chunk
        elif chunk_type == GLB_BIN_CHUNK:
            bin_chunk = chunk
    if json_chunk is None:
        raise GlbInspectionError("GLB is missing its JSON chunk")
    try:
        gltf = json.loads(json_chunk.decode("utf-8").rstrip(" \t\r\n\0"))
    except json.JSONDecodeError as exc:
        raise GlbInspectionError(f"GLB JSON chunk is malformed: {exc}") from exc
    if not isinstance(gltf, dict):
        raise GlbInspectionError("GLB JSON root must be an object")
    return gltf, bin_chunk


def _identity_matrix() -> list[list[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _matrix_multiply(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [
        [
            sum(left[row][inner] * right[inner][column] for inner in range(4))
            for column in range(4)
        ]
        for row in range(4)
    ]


def _node_matrix(node: dict[str, Any]) -> list[list[float]]:
    raw_matrix = node.get("matrix")
    if isinstance(raw_matrix, list) and len(raw_matrix) == 16:
        values = [float(value) for value in raw_matrix]
        return [
            [values[0], values[4], values[8], values[12]],
            [values[1], values[5], values[9], values[13]],
            [values[2], values[6], values[10], values[14]],
            [values[3], values[7], values[11], values[15]],
        ]

    translation = _node_vector(node, "translation", [0.0, 0.0, 0.0], 3)
    rotation = _node_vector(node, "rotation", [0.0, 0.0, 0.0, 1.0], 4)
    scale = _node_vector(node, "scale", [1.0, 1.0, 1.0], 3)
    tx, ty, tz = (float(translation[index]) for index in range(3))
    sx, sy, sz = (float(scale[index]) for index in range(3))
    x, y, z, w = (float(rotation[index]) for index in range(4))
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length > 0:
        x, y, z, w = x / length, y / length, z / length, w / length
    rotation_matrix = [
        [1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * z * w, 2 * x * z + 2 * y * w, 0.0],
        [2 * x * y + 2 * z * w, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * x * w, 0.0],
        [2 * x * z - 2 * y * w, 2 * y * z + 2 * x * w, 1 - 2 * x * x - 2 * y * y, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    scale_matrix = [
        [sx, 0.0, 0.0, 0.0],
        [0.0, sy, 0.0, 0.0],
        [0.0, 0.0, sz, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    translate_matrix = [
        [1.0, 0.0, 0.0, tx],
        [0.0, 1.0, 0.0, ty],
        [0.0, 0.0, 1.0, tz],
        [0.0, 0.0, 0.0, 1.0],
    ]
    return _matrix_multiply(translate_matrix, _matrix_multiply(rotation_matrix, scale_matrix))


def _transform_point(matrix: list[list[float]], point: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = point
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
    )


def _accessor_component_count(accessor: dict[str, Any]) -> int:
    return ACCESSOR_COMPONENTS.get(str(accessor.get("type")), 1)


def _accessor_stride(gltf: dict[str, Any], accessor: dict[str, Any]) -> int:
    buffer_view_index = accessor.get("bufferView")
    buffer_views = _as_list(gltf.get("bufferViews"))
    buffer_view = buffer_views[buffer_view_index] if isinstance(buffer_view_index, int) and buffer_view_index < len(buffer_views) else {}
    if isinstance(buffer_view, dict) and isinstance(buffer_view.get("byteStride"), int):
        return int(buffer_view["byteStride"])
    component_type = int(accessor.get("componentType", 5126))
    _format, component_size = COMPONENT_FORMATS.get(component_type, ("f", 4))
    return component_size * _accessor_component_count(accessor)


def _accessor_bounds(gltf: dict[str, Any], bin_chunk: bytes, accessor_index: object) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    accessors = _as_list(gltf.get("accessors"))
    if not isinstance(accessor_index, int) or accessor_index >= len(accessors):
        return None
    accessor = accessors[accessor_index]
    if not isinstance(accessor, dict):
        return None
    raw_min = accessor.get("min")
    raw_max = accessor.get("max")
    if isinstance(raw_min, list) and isinstance(raw_max, list) and len(raw_min) >= 3 and len(raw_max) >= 3:
        return (
            (float(raw_min[0]), float(raw_min[1]), float(raw_min[2])),
            (float(raw_max[0]), float(raw_max[1]), float(raw_max[2])),
        )

    buffer_view_index = accessor.get("bufferView")
    buffer_views = _as_list(gltf.get("bufferViews"))
    if not isinstance(buffer_view_index, int) or buffer_view_index >= len(buffer_views):
        return None
    buffer_view = buffer_views[buffer_view_index]
    if not isinstance(buffer_view, dict) or buffer_view.get("buffer", 0) != 0:
        return None
    if isinstance(buffer_view.get("extensions"), dict):
        return None
    component_type = int(accessor.get("componentType", 5126))
    component_format = COMPONENT_FORMATS.get(component_type)
    if component_format is None or _accessor_component_count(accessor) < 3:
        return None
    unpack_format, component_size = component_format
    count = int(accessor.get("count", 0))
    stride = _accessor_stride(gltf, accessor)
    byte_offset = int(buffer_view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    values_min = [math.inf, math.inf, math.inf]
    values_max = [-math.inf, -math.inf, -math.inf]
    for index in range(count):
        offset = byte_offset + index * stride
        if offset + component_size * 3 > len(bin_chunk):
            return None
        x, y, z = struct.unpack_from("<" + unpack_format * 3, bin_chunk, offset)
        for axis_index, value in enumerate((float(x), float(y), float(z))):
            values_min[axis_index] = min(values_min[axis_index], value)
            values_max[axis_index] = max(values_max[axis_index], value)
    if any(not math.isfinite(value) for value in values_min + values_max):
        return None
    return (tuple(values_min), tuple(values_max))  # type: ignore[return-value]


def _bbox_corners(bounds: tuple[tuple[float, float, float], tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    lower, upper = bounds
    return [
        (x, y, z)
        for x in (lower[0], upper[0])
        for y in (lower[1], upper[1])
        for z in (lower[2], upper[2])
    ]


def _merge_bounds(
    current: tuple[list[float], list[float]] | None,
    point: tuple[float, float, float],
) -> tuple[list[float], list[float]]:
    if current is None:
        return ([point[0], point[1], point[2]], [point[0], point[1], point[2]])
    lower, upper = current
    for index, value in enumerate(point):
        lower[index] = min(lower[index], value)
        upper[index] = max(upper[index], value)
    return lower, upper


def _scene_root_nodes(gltf: dict[str, Any]) -> list[int]:
    scenes = _as_list(gltf.get("scenes"))
    scene_index = gltf.get("scene", 0)
    if isinstance(scene_index, int) and scene_index < len(scenes):
        scene = scenes[scene_index]
        if isinstance(scene, dict) and isinstance(scene.get("nodes"), list):
            return [node for node in _as_list(scene.get("nodes")) if isinstance(node, int)]

    nodes = _as_list(gltf.get("nodes"))
    child_nodes = {
        child
        for node in nodes
        if isinstance(node, dict)
        for child in _as_list(node.get("children"))
        if isinstance(child, int)
    }
    return [index for index in range(len(nodes)) if index not in child_nodes]


def _global_node_matrices(gltf: dict[str, Any]) -> dict[int, list[list[float]]]:
    nodes = _as_list(gltf.get("nodes"))
    matrices: dict[int, list[list[float]]] = {}

    def walk(node_index: int, parent_matrix: list[list[float]]) -> None:
        if node_index < 0 or node_index >= len(nodes):
            return
        node = nodes[node_index]
        if not isinstance(node, dict):
            return
        matrix = _matrix_multiply(parent_matrix, _node_matrix(node))
        matrices[node_index] = matrix
        for child_index in _as_list(node.get("children")):
            if isinstance(child_index, int):
                walk(child_index, matrix)

    for root_index in _scene_root_nodes(gltf):
        walk(root_index, _identity_matrix())
    return matrices


def _descendant_indexes(gltf: dict[str, Any], node_index: int) -> list[int]:
    nodes = _as_list(gltf.get("nodes"))
    indexes: list[int] = []

    def walk(index: int) -> None:
        if index < 0 or index >= len(nodes):
            return
        node = nodes[index]
        if not isinstance(node, dict):
            return
        indexes.append(index)
        for child_index in _as_list(node.get("children")):
            if isinstance(child_index, int):
                walk(child_index)

    walk(node_index)
    return indexes


def _mesh_bounds_for_node(
    gltf: dict[str, Any],
    bin_chunk: bytes,
    node_index: int,
    global_matrices: dict[int, list[list[float]]],
) -> tuple[list[float], list[float]] | None:
    nodes = _as_list(gltf.get("nodes"))
    meshes = _as_list(gltf.get("meshes"))
    bounds: tuple[list[float], list[float]] | None = None
    for descendant_index in _descendant_indexes(gltf, node_index):
        node = nodes[descendant_index]
        if not isinstance(node, dict):
            continue
        mesh_index = node.get("mesh")
        if not isinstance(mesh_index, int) or mesh_index >= len(meshes):
            continue
        mesh = meshes[mesh_index]
        if not isinstance(mesh, dict):
            continue
        node_matrix = global_matrices.get(descendant_index, _identity_matrix())
        for primitive in _as_list(mesh.get("primitives")):
            if not isinstance(primitive, dict):
                continue
            attributes = primitive.get("attributes")
            if not isinstance(attributes, dict):
                continue
            primitive_bounds = _accessor_bounds(gltf, bin_chunk, attributes.get("POSITION"))
            if primitive_bounds is None:
                continue
            for corner in _bbox_corners(primitive_bounds):
                bounds = _merge_bounds(bounds, _transform_point(node_matrix, corner))
    return bounds


def _module_node_indexes(gltf: dict[str, Any]) -> dict[int, list[int]]:
    nodes = _as_list(gltf.get("nodes"))
    matches: dict[int, list[int]] = {}
    for node_index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        name = str(node.get("name", ""))
        match = MODULE_NODE_PATTERN.match(name)
        if match:
            matches.setdefault(int(match.group(1)), []).append(node_index)
    return dict(sorted(matches.items()))


def _merge_bounds_pair(
    current: tuple[list[float], list[float]] | None,
    candidate: tuple[list[float], list[float]] | None,
) -> tuple[list[float], list[float]] | None:
    if candidate is None:
        return current
    lower, upper = candidate
    merged = current
    for point in (
        (lower[0], lower[1], lower[2]),
        (upper[0], upper[1], upper[2]),
    ):
        merged = _merge_bounds(merged, point)
    return merged


def _extension_names(value: object, names: set[str]) -> None:
    if isinstance(value, dict):
        extensions = value.get("extensions")
        if isinstance(extensions, dict):
            names.update(str(name) for name in extensions)
        for child in value.values():
            _extension_names(child, names)
    elif isinstance(value, list):
        for child in value:
            _extension_names(child, names)


def _compression_extensions(extensions: set[str]) -> tuple[str, ...]:
    markers = ("draco", "meshopt", "quantization", "basisu", "texture_transform")
    return tuple(sorted(name for name in extensions if any(marker in name.lower() for marker in markers)))


def _primitive_triangle_count(gltf: dict[str, Any], primitive: dict[str, Any]) -> int:
    accessors = _as_list(gltf.get("accessors"))
    count = 0
    index_accessor = primitive.get("indices")
    if isinstance(index_accessor, int) and index_accessor < len(accessors) and isinstance(accessors[index_accessor], dict):
        count = int(accessors[index_accessor].get("count", 0))
    else:
        attributes = primitive.get("attributes")
        position_accessor = attributes.get("POSITION") if isinstance(attributes, dict) else None
        if isinstance(position_accessor, int) and position_accessor < len(accessors) and isinstance(accessors[position_accessor], dict):
            count = int(accessors[position_accessor].get("count", 0))
    mode = int(primitive.get("mode", 4))
    if mode == 4:
        return count // 3
    if mode in {5, 6}:
        return max(0, count - 2)
    return 0


def inspect_glb(path: Path, *, viewer_scale: float) -> GlbInspection:
    gltf, bin_chunk = _load_glb(path)
    meshes = _as_list(gltf.get("meshes"))
    accessors = _as_list(gltf.get("accessors"))
    primitive_count = 0
    vertex_count = 0
    triangle_count = 0
    for mesh in meshes:
        if not isinstance(mesh, dict):
            continue
        for primitive in _as_list(mesh.get("primitives")):
            if not isinstance(primitive, dict):
                continue
            primitive_count += 1
            attributes = primitive.get("attributes")
            position_accessor = attributes.get("POSITION") if isinstance(attributes, dict) else None
            if isinstance(position_accessor, int) and position_accessor < len(accessors) and isinstance(accessors[position_accessor], dict):
                vertex_count += int(accessors[position_accessor].get("count", 0))
            triangle_count += _primitive_triangle_count(gltf, primitive)

    extensions: set[str] = set(str(name) for name in _as_list(gltf.get("extensionsUsed")) if isinstance(name, str))
    _extension_names(gltf, extensions)
    global_matrices = _global_node_matrices(gltf)
    anchors: list[AnchorInfo] = []
    for module_number, node_indexes in _module_node_indexes(gltf).items():
        bounds: tuple[list[float], list[float]] | None = None
        for node_index in node_indexes:
            bounds = _merge_bounds_pair(bounds, _mesh_bounds_for_node(gltf, bin_chunk, node_index, global_matrices))
        if bounds is None:
            continue
        lower, upper = bounds
        center = tuple((lower[index] + upper[index]) / 2 for index in range(3))
        anchors.append(
            AnchorInfo(
                name=f"module_{module_number}",
                x=round(center[0] * viewer_scale, 9),
                z=round(center[2] * viewer_scale, 9),
                vertical_y=round(center[1] * viewer_scale, 9),
            )
        )

    return GlbInspection(
        mesh_count=sum(1 for mesh in meshes if isinstance(mesh, dict)),
        primitive_count=primitive_count,
        vertex_count=vertex_count,
        triangle_count=triangle_count,
        extensions_used=tuple(sorted(extensions)),
        compression_extensions=_compression_extensions(extensions),
        anchors=tuple(anchors),
    )


def _validate_asset_path(
    *,
    asset_key: str,
    lod_key: str,
    raw_path: object,
    manifest_path: Path,
    report: ValidationReport,
    required: bool,
    viewer_scale: float,
    high_size_threshold_bytes: int,
    inspect_built_in: bool,
    asset_label: str = "module asset",
) -> bool:
    relative_path = _relative_asset_path(raw_path, key=f"{asset_key}.{lod_key}")
    if relative_path is None:
        report.errors.append(f"{asset_label} {asset_key!r} {lod_key!r} must be a safe relative path")
        return False
    if relative_path.suffix.lower() != ".glb":
        report.errors.append(f"{asset_label} {asset_key!r} {lod_key!r} must reference a .glb file")
        return False
    asset_path = manifest_path.parent / relative_path
    if not asset_path.is_file():
        message = f"{asset_label} {asset_key!r} {lod_key!r} is missing: {asset_path}"
        if required:
            report.errors.append(message)
        else:
            report.warnings.append(message)
        return False

    size_bytes = asset_path.stat().st_size
    if lod_key == "high" and size_bytes > high_size_threshold_bytes:
        report.warnings.append(
            f"{asset_label} {asset_key!r} high GLB is {size_bytes} bytes, above the configured threshold "
            f"of {high_size_threshold_bytes} bytes"
        )

    inspection: GlbInspection | None = None
    if inspect_built_in:
        try:
            inspection = inspect_glb(asset_path, viewer_scale=viewer_scale)
        except GlbInspectionError as exc:
            report.errors.append(f"built-in GLB inspection failed for asset {asset_key!r} {lod_key!r}: {exc}")
        if asset_label == "module asset" and lod_key == "high" and inspection is not None:
            expected_anchor_count = EXPECTED_ANCHOR_COUNTS.get(asset_key)
            if expected_anchor_count is not None and len(inspection.anchors) != expected_anchor_count:
                report.warnings.append(
                    f"module asset {asset_key!r} high GLB exposes {len(inspection.anchors)} embedded module anchors; "
                    f"runtime anchor metadata sidecar must provide {expected_anchor_count}"
                )

    report.asset_infos.append(
        AssetInfo(
            key=f"{asset_key}.{lod_key}",
            path=asset_path,
            size_bytes=size_bytes,
            mesh_count=inspection.mesh_count if inspection else 0,
            primitive_count=inspection.primitive_count if inspection else 0,
            vertex_count=inspection.vertex_count if inspection else 0,
            triangle_count=inspection.triangle_count if inspection else 0,
            extensions_used=inspection.extensions_used if inspection else (),
            compression_extensions=inspection.compression_extensions if inspection else (),
            anchors=inspection.anchors if inspection else (),
        )
    )
    return True


def _manifest_referenced_paths(manifest: dict[str, Any], asset_field: str) -> set[str]:
    asset_map = manifest.get(asset_field)
    if not isinstance(asset_map, dict):
        return set()
    paths: set[str] = set()
    for asset in asset_map.values():
        if not isinstance(asset, dict):
            continue
        for lod_key in LOD_KEYS:
            value = asset.get(lod_key)
            if isinstance(value, str):
                paths.add(value)
    return paths


def _validate_unmapped_glbs(
    manifest: dict[str, Any],
    manifest_path: Path,
    report: ValidationReport,
    *,
    asset_field: str,
    glob_pattern: str,
) -> None:
    referenced_paths = _manifest_referenced_paths(manifest, asset_field)
    for asset_path in sorted(manifest_path.parent.glob(glob_pattern)):
        if asset_path.name not in referenced_paths:
            report.errors.append(f"GLB file is present but not mapped in {asset_field}: {asset_path.name}")


def _validate_module_assets(
    manifest: dict[str, Any],
    manifest_path: Path,
    report: ValidationReport,
    *,
    high_size_threshold_bytes: int,
    inspect_built_in: bool,
    strict_lods: bool,
) -> None:
    module_assets = manifest.get("module_assets")
    if not isinstance(module_assets, dict):
        return

    viewer_scale = float(manifest.get("viewer_scale", 0.001))
    for asset_key in REQUIRED_MODULE_ASSET_KEYS:
        if asset_key not in module_assets:
            report.errors.append(f"manifest module_assets missing required key: {asset_key}")

    high_available: dict[str, bool] = {}
    for asset_key, asset in sorted(module_assets.items()):
        if not isinstance(asset_key, str) or not asset_key:
            report.errors.append("manifest module_assets keys must be non-empty strings")
            continue
        if not isinstance(asset, dict):
            report.errors.append(f"manifest module asset {asset_key!r} must be an object")
            continue
        for lod_key in LOD_KEYS:
            if lod_key not in asset:
                report.errors.append(f"manifest module asset {asset_key!r} missing required key: {lod_key}")
                continue
            required = (lod_key == "high" or strict_lods) and asset.get("optional") is not True
            exists = _validate_asset_path(
                asset_key=asset_key,
                lod_key=lod_key,
                raw_path=asset[lod_key],
                manifest_path=manifest_path,
                report=report,
                required=required,
                viewer_scale=viewer_scale,
                high_size_threshold_bytes=high_size_threshold_bytes,
                inspect_built_in=inspect_built_in,
            )
            if lod_key == "high":
                high_available[asset_key] = exists
        if asset.get("anchor_source") != "module_nodes":
            report.errors.append(f"manifest module asset {asset_key!r} anchor_source must be 'module_nodes'")
        fallback_asset_key = asset.get("fallback_asset_key")
        if fallback_asset_key is not None and not isinstance(fallback_asset_key, str):
            report.errors.append(f"manifest module asset {asset_key!r} fallback_asset_key must be a string")

    for asset_key, asset in sorted(module_assets.items()):
        if not isinstance(asset, dict) or asset.get("optional") is not True:
            continue
        if high_available.get(asset_key, False):
            continue
        fallback_asset_key = asset.get("fallback_asset_key")
        if isinstance(fallback_asset_key, str) and high_available.get(fallback_asset_key, False):
            report.warnings.append(
                f"optional module asset {asset_key!r} high GLB is missing; fallback {fallback_asset_key!r} is available"
            )
        else:
            report.errors.append(f"optional module asset {asset_key!r} high GLB is missing and no fallback is available")

    _validate_unmapped_glbs(
        manifest,
        manifest_path,
        report,
        asset_field="module_assets",
        glob_pattern="fixture_*.glb",
    )


def _validate_simple_assets(
    manifest: dict[str, Any],
    manifest_path: Path,
    report: ValidationReport,
    *,
    high_size_threshold_bytes: int,
    inspect_built_in: bool,
    strict_lods: bool,
) -> None:
    assets = manifest.get("assets")
    if not isinstance(assets, dict):
        return

    viewer_scale = float(manifest.get("viewer_scale", 0.001))
    for asset_key in REQUIRED_SIMPLE_ASSET_KEYS:
        if asset_key not in assets:
            report.errors.append(f"manifest assets missing required key: {asset_key}")

    for asset_key, asset in sorted(assets.items()):
        if not isinstance(asset_key, str) or not asset_key:
            report.errors.append("manifest assets keys must be non-empty strings")
            continue
        if not isinstance(asset, dict):
            report.errors.append(f"manifest asset {asset_key!r} must be an object")
            continue
        for lod_key in LOD_KEYS:
            if lod_key not in asset:
                report.errors.append(f"manifest asset {asset_key!r} missing required key: {lod_key}")
                continue
            required = lod_key == "high" or strict_lods
            _validate_asset_path(
                asset_key=asset_key,
                lod_key=lod_key,
                raw_path=asset[lod_key],
                manifest_path=manifest_path,
                report=report,
                required=required,
                viewer_scale=viewer_scale,
                high_size_threshold_bytes=high_size_threshold_bytes,
                inspect_built_in=inspect_built_in,
                asset_label="fixture asset",
            )

    _validate_unmapped_glbs(
        manifest,
        manifest_path,
        report,
        asset_field="assets",
        glob_pattern="*.glb",
    )


def _load_anchor_sidecar(manifest: dict[str, Any], manifest_path: Path, report: ValidationReport) -> dict[str, Any] | None:
    relative_path = _relative_asset_path(manifest.get("anchor_metadata"), key="anchor_metadata")
    if relative_path is None:
        report.errors.append("manifest field 'anchor_metadata' must be a safe relative path")
        return None
    anchor_path = manifest_path.parent / relative_path
    try:
        data = json.loads(anchor_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        report.errors.append(f"anchor metadata sidecar is missing: {anchor_path}")
        return None
    except json.JSONDecodeError as exc:
        report.errors.append(f"anchor metadata sidecar is not valid JSON: {exc}")
        return None
    if not isinstance(data, dict):
        report.errors.append("anchor metadata sidecar root must be a JSON object")
        return None
    return data


def _validate_anchor_sidecar(manifest: dict[str, Any], manifest_path: Path, report: ValidationReport) -> None:
    sidecar = _load_anchor_sidecar(manifest, manifest_path, report)
    if sidecar is None:
        return
    if sidecar.get("schema_version") != 1:
        report.errors.append("anchor metadata field 'schema_version' must be 1")
    if sidecar.get("system") != manifest.get("system"):
        report.errors.append("anchor metadata field 'system' must match the manifest")
    if sidecar.get("units") != manifest.get("units"):
        report.errors.append("anchor metadata field 'units' must match the manifest")
    if sidecar.get("viewer_scale") != manifest.get("viewer_scale"):
        report.errors.append("anchor metadata field 'viewer_scale' must match the manifest")
    if sidecar.get("axis_mapping") != manifest.get("axis_mapping"):
        report.errors.append("anchor metadata field 'axis_mapping' must match the manifest")

    anchors_m = sidecar.get("anchors_m")
    if not isinstance(anchors_m, dict):
        report.errors.append("anchor metadata field 'anchors_m' must be an object")
        return
    module_assets = _as_dict(manifest.get("module_assets"))
    for asset_key, asset in sorted(module_assets.items()):
        if not isinstance(asset, dict):
            continue
        anchors = anchors_m.get(asset_key)
        if not isinstance(anchors, list) or not anchors:
            report.errors.append(f"anchor metadata missing anchors for asset key {asset_key!r}")
            continue
        expected_count = EXPECTED_ANCHOR_COUNTS.get(asset_key)
        if expected_count is not None and len(anchors) != expected_count:
            report.errors.append(
                f"anchor metadata for {asset_key!r} must contain {expected_count} anchors, found {len(anchors)}"
            )
        seen_names: set[str] = set()
        for index, anchor in enumerate(anchors):
            if not isinstance(anchor, dict):
                report.errors.append(f"anchor metadata for {asset_key!r} index {index} must be an object")
                continue
            name = anchor.get("name")
            if not isinstance(name, str) or not MODULE_NODE_PATTERN.fullmatch(name):
                report.errors.append(f"anchor metadata for {asset_key!r} index {index} has an invalid module name")
            elif name in seen_names:
                report.errors.append(f"anchor metadata for {asset_key!r} repeats module name {name!r}")
            else:
                seen_names.add(name)
            for field_name in ("x", "z", "vertical_y"):
                value = anchor.get(field_name)
                if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
                    report.errors.append(
                        f"anchor metadata for {asset_key!r} anchor {name or index!r} field {field_name!r} must be finite"
                    )


def _anchor_metadata_errors(metadata: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if metadata.get("schema_version") != 1:
        errors.append("generated anchor metadata field 'schema_version' must be 1")
    if metadata.get("system") != manifest.get("system"):
        errors.append("generated anchor metadata field 'system' must match the manifest")
    if metadata.get("units") != manifest.get("units"):
        errors.append("generated anchor metadata field 'units' must match the manifest")
    if metadata.get("viewer_scale") != manifest.get("viewer_scale"):
        errors.append("generated anchor metadata field 'viewer_scale' must match the manifest")
    if metadata.get("axis_mapping") != manifest.get("axis_mapping"):
        errors.append("generated anchor metadata field 'axis_mapping' must match the manifest")

    anchors_m = metadata.get("anchors_m")
    if not isinstance(anchors_m, dict):
        return [*errors, "generated anchor metadata field 'anchors_m' must be an object"]

    module_assets = _as_dict(manifest.get("module_assets"))
    for asset_key, asset in sorted(module_assets.items()):
        if not isinstance(asset, dict):
            continue
        expected_count = EXPECTED_ANCHOR_COUNTS.get(asset_key)
        if expected_count is None:
            continue
        anchors = anchors_m.get(asset_key)
        if not isinstance(anchors, list) or not anchors:
            errors.append(f"generated anchor metadata missing anchors for asset key {asset_key!r}")
            continue
        if len(anchors) != expected_count:
            errors.append(
                f"generated anchor metadata for {asset_key!r} must contain {expected_count} anchors, found {len(anchors)}"
            )
            continue
        seen_names: set[str] = set()
        for index, anchor in enumerate(anchors):
            if not isinstance(anchor, dict):
                errors.append(f"generated anchor metadata for {asset_key!r} index {index} must be an object")
                continue
            name = anchor.get("name")
            if not isinstance(name, str) or not MODULE_NODE_PATTERN.fullmatch(name):
                errors.append(f"generated anchor metadata for {asset_key!r} index {index} has an invalid module name")
            elif name in seen_names:
                errors.append(f"generated anchor metadata for {asset_key!r} repeats module name {name!r}")
            else:
                seen_names.add(name)
            for field_name in ("x", "z", "vertical_y"):
                value = anchor.get(field_name)
                if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
                    errors.append(
                        f"generated anchor metadata for {asset_key!r} anchor {name or index!r} "
                        f"field {field_name!r} must be finite"
                    )
    return errors


def _inspect_assets_with_external_tool(report: ValidationReport, *, strict_tools: bool) -> None:
    tool = shutil.which("gltf-transform")
    if tool is None:
        message = "optional tool missing: gltf-transform"
        if strict_tools:
            report.errors.append(message)
        else:
            report.warnings.append(message)
        return

    for asset in report.asset_infos:
        result = subprocess.run(  # nosec B603
            [tool, "inspect", str(asset.path)],
            check=False,
            capture_output=True,
            text=True,
        )
        output = (result.stdout or result.stderr).strip()
        report.inspections[asset.key] = output
        if result.returncode != 0:
            report.errors.append(f"gltf-transform inspect failed for asset {asset.key!r}: {output}")


def validate_manifest(
    manifest_path: Path,
    *,
    inspect_assets: bool = False,
    strict_tools: bool = False,
    strict_lods: bool = False,
    high_size_threshold_bytes: int = DEFAULT_HIGH_SIZE_THRESHOLD_BYTES,
) -> ValidationReport:
    report = ValidationReport(manifest_path=manifest_path)
    manifest = _load_manifest(manifest_path, report)
    if manifest is None:
        return report
    _validate_structure(manifest, report)
    system = manifest.get("system")
    if system == PROPOSED_SYSTEM:
        _validate_module_assets(
            manifest,
            manifest_path,
            report,
            high_size_threshold_bytes=high_size_threshold_bytes,
            inspect_built_in=True,
            strict_lods=strict_lods,
        )
        _validate_anchor_sidecar(manifest, manifest_path, report)
    elif system in SIMPLE_FIXTURE_SYSTEMS:
        _validate_simple_assets(
            manifest,
            manifest_path,
            report,
            high_size_threshold_bytes=high_size_threshold_bytes,
            inspect_built_in=True,
            strict_lods=strict_lods,
        )
    if inspect_assets:
        _inspect_assets_with_external_tool(report, strict_tools=strict_tools)
    return report


def _anchor_source_path(
    manifest_path: Path,
    high: Path,
    *,
    anchor_source_dir: Path | None,
) -> Path:
    if anchor_source_dir is None:
        return manifest_path.parent / high
    return anchor_source_dir / high.name


def anchor_metadata_from_manifest(
    manifest_path: Path,
    *,
    anchor_source_dir: Path | None = None,
) -> dict[str, Any]:
    """Generate anchor metadata from source/raw GLBs.

    Intended workflow: extract or maintain anchors from source/raw CAD exports,
    then optimize GLBs for runtime. Optimized high/medium/proxy GLBs do not need
    to retain module node names because anchors.json is the runtime source of
    truth for CAD module anchors.
    """
    report = ValidationReport(manifest_path=manifest_path)
    manifest = _load_manifest(manifest_path, report)
    if manifest is None:
        raise GlbInspectionError("; ".join(report.errors))
    if manifest.get("system") != PROPOSED_SYSTEM:
        raise GlbInspectionError("anchor metadata generation is supported only for proposed_led_system manifests")
    module_assets = manifest.get("module_assets")
    if not isinstance(module_assets, dict):
        raise GlbInspectionError("manifest module_assets must be an object")
    viewer_scale = float(manifest.get("viewer_scale", 0.001))
    anchors_m: dict[str, list[dict[str, float | str]]] = {}
    source_files: dict[str, str] = {}
    for asset_key, asset in sorted(module_assets.items()):
        if not isinstance(asset_key, str) or not isinstance(asset, dict):
            continue
        high = _relative_asset_path(asset.get("high"), key=f"{asset_key}.high")
        if high is None:
            continue
        high_path = _anchor_source_path(manifest_path, high, anchor_source_dir=anchor_source_dir)
        if not high_path.is_file():
            continue
        inspection = inspect_glb(high_path, viewer_scale=viewer_scale)
        anchors_m[asset_key] = [
            {
                "name": anchor.name,
                "x": anchor.x,
                "z": anchor.z,
                "vertical_y": anchor.vertical_y,
            }
            for anchor in inspection.anchors
        ]
        source_files[asset_key] = high.name
    return {
        "schema_version": 1,
        "system": manifest.get("system"),
        "units": manifest.get("units"),
        "source_units": manifest.get("source_units"),
        "viewer_scale": manifest.get("viewer_scale"),
        "axis_mapping": manifest.get("axis_mapping"),
        "anchors_m": anchors_m,
        "source_files": source_files,
    }


def write_anchor_metadata(
    manifest_path: Path,
    output_path: Path,
    *,
    anchor_source_dir: Path | None = None,
    allow_incomplete: bool = False,
) -> dict[str, Any]:
    report = ValidationReport(manifest_path=manifest_path)
    manifest = _load_manifest(manifest_path, report)
    if manifest is None:
        raise AnchorMetadataError("; ".join(report.errors))
    metadata = anchor_metadata_from_manifest(manifest_path, anchor_source_dir=anchor_source_dir)
    errors = _anchor_metadata_errors(metadata, manifest)
    if errors and not allow_incomplete:
        raise AnchorMetadataError(
            "refusing to write incomplete anchor metadata; existing file was left unchanged: "
            + "; ".join(errors)
        )
    output_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def _print_report(report: ValidationReport) -> None:
    print(f"Manifest: {report.manifest_path}")
    for asset in report.asset_infos:
        compression = ", ".join(asset.compression_extensions) or "none"
        extensions = ", ".join(asset.extensions_used) or "none"
        meshopt = "yes" if any("meshopt" in extension.lower() for extension in asset.compression_extensions) else "no"
        print(
            "Asset "
            f"{asset.key}: {asset.path} ({asset.size_bytes} bytes, "
            f"meshes={asset.mesh_count}, primitives={asset.primitive_count}, "
            f"vertices~={asset.vertex_count}, triangles~={asset.triangle_count}, "
            f"compression={compression}, meshopt={meshopt}, extensions={extensions}, anchors={len(asset.anchors)})"
        )
    for key, output in report.inspections.items():
        print(f"gltf-transform inspect [{key}]:")
        print(output or "  <no output>")
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in report.errors:
        print(f"error: {error}", file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate static 3D viewer asset manifests.")
    parser.add_argument("manifest", type=Path, help="Path to viewer manifest.json.")
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Run optional 'gltf-transform inspect' for each referenced GLB when available.",
    )
    parser.add_argument(
        "--strict-tools",
        action="store_true",
        help="Fail when optional inspection tools requested by this command are missing.",
    )
    parser.add_argument(
        "--strict-lods",
        action="store_true",
        help="Fail when required assets are missing medium or proxy LOD files.",
    )
    parser.add_argument(
        "--high-size-threshold-mb",
        type=float,
        default=DEFAULT_HIGH_SIZE_THRESHOLD_BYTES / (1024 * 1024),
        help="Warn when a high GLB exceeds this size. Defaults to 25 MiB.",
    )
    parser.add_argument(
        "--write-anchors",
        type=Path,
        help="Write generated module anchor metadata JSON for source/raw high GLB assets.",
    )
    parser.add_argument(
        "--anchor-source-dir",
        type=Path,
        help="Directory containing raw/debug GLBs with module_# nodes to use when generating anchors.",
    )
    parser.add_argument(
        "--allow-incomplete-anchors",
        action="store_true",
        help="Allow --write-anchors to write empty or incomplete generated anchor arrays.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.write_anchors is not None:
        try:
            write_anchor_metadata(
                args.manifest,
                args.write_anchors,
                anchor_source_dir=args.anchor_source_dir,
                allow_incomplete=args.allow_incomplete_anchors,
            )
        except (AnchorMetadataError, GlbInspectionError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    report = validate_manifest(
        args.manifest,
        inspect_assets=args.inspect,
        strict_tools=args.strict_tools,
        strict_lods=args.strict_lods,
        high_size_threshold_bytes=int(args.high_size_threshold_mb * 1024 * 1024),
    )
    _print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
