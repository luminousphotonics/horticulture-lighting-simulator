"""Strict deterministic GLB decoding for physical fixture transport geometry."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import struct
from typing import Any, Mapping, Sequence

GLB_MAGIC = 0x46546C67
GLB_VERSION = 2
GLB_JSON_CHUNK = 0x4E4F534A
GLB_BINARY_CHUNK = 0x004E4942
TRIANGLES_MODE = 4

_COMPONENT_FORMATS: Mapping[int, tuple[str, int]] = {
    5120: ("b", 1),
    5121: ("B", 1),
    5122: ("h", 2),
    5123: ("H", 2),
    5125: ("I", 4),
    5126: ("f", 4),
}
_TYPE_WIDTHS: Mapping[str, int] = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}

Matrix4 = tuple[float, ...]
Point3 = tuple[float, float, float]
Triangle = tuple[Point3, Point3, Point3]


class FixtureGlbError(ValueError):
    """An authenticated fixture GLB violates the supported transport contract."""


@dataclass(frozen=True, slots=True)
class GlbPrimitiveInventory:
    node_index: int
    node_path: str
    mesh_index: int
    primitive_index: int
    position_accessor: int
    index_accessor: int
    material_index: int | None
    mode: int

    def to_payload(self) -> dict[str, object]:
        return {
            "node_index": self.node_index,
            "node_path": self.node_path,
            "mesh_index": self.mesh_index,
            "primitive_index": self.primitive_index,
            "position_accessor": self.position_accessor,
            "index_accessor": self.index_accessor,
            "material_index": self.material_index,
            "mode": self.mode,
        }


@dataclass(frozen=True, slots=True)
class DecodedFixtureGlb:
    document: Mapping[str, Any]
    binary: bytes
    primitive_inventory: tuple[GlbPrimitiveInventory, ...]
    node_world_matrices: Mapping[int, Matrix4]

    def triangles(
        self,
        primitive: GlbPrimitiveInventory,
    ) -> tuple[Triangle, ...]:
        positions = self._accessor_values(primitive.position_accessor)
        indices = self._accessor_values(primitive.index_accessor)
        if any(len(value) != 3 for value in positions):
            raise FixtureGlbError("fixture POSITION accessor must contain VEC3 values.")
        if any(len(value) != 1 for value in indices):
            raise FixtureGlbError("fixture index accessor must contain SCALAR values.")
        if len(indices) % 3:
            raise FixtureGlbError("fixture triangle index count must be divisible by three.")
        matrix = self.node_world_matrices[primitive.node_index]
        points = tuple(
            transform_point(
                matrix,
                (float(value[0]), float(value[1]), float(value[2])),
            )
            for value in positions
        )
        output: list[Triangle] = []
        for offset in range(0, len(indices), 3):
            selected = tuple(int(indices[offset + index][0]) for index in range(3))
            if any(index < 0 or index >= len(points) for index in selected):
                raise FixtureGlbError("fixture triangle index is out of bounds.")
            output.append(
                (points[selected[0]], points[selected[1]], points[selected[2]])
            )
        return tuple(output)

    def _accessor_values(self, accessor_index: int) -> tuple[tuple[float | int, ...], ...]:
        accessors = _sequence(self.document, "accessors")
        views = _sequence(self.document, "bufferViews")
        try:
            accessor = _mapping(accessors[accessor_index], "accessor")
            view = _mapping(views[int(accessor["bufferView"])], "bufferView")
            component_type = int(accessor["componentType"])
            component_format, component_size = _COMPONENT_FORMATS[component_type]
            width = _TYPE_WIDTHS[str(accessor["type"])]
            count = int(accessor["count"])
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise FixtureGlbError("fixture accessor metadata is invalid.") from exc
        if (
            accessor.get("sparse") is not None
            or int(view.get("buffer", 0)) != 0
            or count <= 0
        ):
            raise FixtureGlbError("sparse or external fixture accessors are unsupported.")
        packed_size = component_size * width
        stride = int(view.get("byteStride", packed_size))
        if stride < packed_size:
            raise FixtureGlbError("fixture accessor byte stride is invalid.")
        start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
        end = start + stride * (count - 1) + packed_size
        view_end = int(view.get("byteOffset", 0)) + int(view["byteLength"])
        if start < 0 or end > len(self.binary) or end > view_end:
            raise FixtureGlbError("fixture accessor exceeds its embedded buffer view.")
        normalized = bool(accessor.get("normalized", False))
        values: list[tuple[float | int, ...]] = []
        for item_index in range(count):
            raw = struct.unpack_from(
                f"<{width}{component_format}",
                self.binary,
                start + item_index * stride,
            )
            values.append(
                tuple(
                    _normalize_component(value, component_type)
                    if normalized
                    else value
                    for value in raw
                )
            )
        return tuple(values)


def decode_fixture_glb(data: bytes) -> DecodedFixtureGlb:
    """Decode one embedded GLB and resolve authored scene-node transforms."""

    if len(data) < 28:
        raise FixtureGlbError("fixture GLB is truncated.")
    magic, version, declared_length = struct.unpack_from("<III", data, 0)
    if (
        magic != GLB_MAGIC
        or version != GLB_VERSION
        or declared_length != len(data)
    ):
        raise FixtureGlbError("fixture GLB header is incompatible.")
    offset = 12
    chunks: list[tuple[int, bytes]] = []
    while offset < len(data):
        if offset + 8 > len(data):
            raise FixtureGlbError("fixture GLB chunk header is truncated.")
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk_end = offset + chunk_length
        if chunk_end > len(data):
            raise FixtureGlbError("fixture GLB chunk exceeds declared length.")
        chunks.append((chunk_type, data[offset:chunk_end]))
        offset = chunk_end
    if (
        len(chunks) != 2
        or chunks[0][0] != GLB_JSON_CHUNK
        or chunks[1][0] != GLB_BINARY_CHUNK
    ):
        raise FixtureGlbError("fixture GLB must contain one JSON and one binary chunk.")
    try:
        document = json.loads(chunks[0][1].rstrip(b" \x00"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureGlbError("fixture GLB JSON is invalid.") from exc
    if not isinstance(document, dict):
        raise FixtureGlbError("fixture GLB JSON root must be an object.")
    roots = _scene_roots(document)
    nodes = _sequence(document, "nodes")
    meshes = _sequence(document, "meshes")
    world: dict[int, Matrix4] = {}
    paths: dict[int, str] = {}

    def visit(
        node_index: int,
        parent_world: Matrix4,
        named_path: tuple[str, ...],
        active: frozenset[int],
    ) -> None:
        if node_index in active:
            raise FixtureGlbError("fixture GLB node hierarchy contains a cycle.")
        if node_index in world:
            raise FixtureGlbError("fixture GLB scene must be a strict node tree.")
        try:
            node = _mapping(nodes[node_index], "node")
        except IndexError as exc:
            raise FixtureGlbError("fixture GLB scene references an unknown node.") from exc
        name = node.get("name")
        next_path = named_path + ((str(name),) if name else ())
        node_world = multiply_matrix4(parent_world, node_local_matrix(node))
        world[node_index] = node_world
        paths[node_index] = "/".join(next_path) or f"node[{node_index}]"
        for child in node.get("children", ()):
            visit(
                int(child),
                node_world,
                next_path,
                active | {node_index},
            )

    for root in roots:
        visit(root, identity_matrix4(), (), frozenset())
    if set(world) != set(range(len(nodes))):
        raise FixtureGlbError("fixture GLB contains nodes outside its declared scene.")

    inventory: list[GlbPrimitiveInventory] = []
    for node_index in range(len(nodes)):
        node = _mapping(nodes[node_index], "node")
        if "mesh" not in node:
            continue
        try:
            mesh_index = int(node["mesh"])
            mesh = _mapping(meshes[mesh_index], "mesh")
            primitives = _sequence(mesh, "primitives")
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise FixtureGlbError("fixture node references invalid mesh data.") from exc
        for primitive_index, raw_primitive in enumerate(primitives):
            primitive = _mapping(raw_primitive, "mesh primitive")
            attributes = _mapping(primitive.get("attributes"), "mesh attributes")
            if (
                int(primitive.get("mode", TRIANGLES_MODE)) != TRIANGLES_MODE
                or "POSITION" not in attributes
                or "indices" not in primitive
                or primitive.get("targets")
            ):
                raise FixtureGlbError(
                    "fixture transport requires indexed, non-morphed triangles."
                )
            inventory.append(
                GlbPrimitiveInventory(
                    node_index=node_index,
                    node_path=paths[node_index],
                    mesh_index=mesh_index,
                    primitive_index=primitive_index,
                    position_accessor=int(attributes["POSITION"]),
                    index_accessor=int(primitive["indices"]),
                    material_index=(
                        None
                        if primitive.get("material") is None
                        else int(primitive["material"])
                    ),
                    mode=int(primitive.get("mode", TRIANGLES_MODE)),
                )
            )
    if not inventory:
        raise FixtureGlbError("fixture GLB contains no transportable mesh primitives.")
    return DecodedFixtureGlb(
        document=document,
        binary=chunks[1][1],
        primitive_inventory=tuple(inventory),
        node_world_matrices=world,
    )


def node_inventory_payload(decoded: DecodedFixtureGlb) -> dict[str, object]:
    """Return exact node/primitive inventory used to authenticate classifications."""

    nodes = _sequence(decoded.document, "nodes")
    node_records: list[dict[str, object]] = []
    for index, raw_node in enumerate(nodes):
        node = _mapping(raw_node, "node")
        node_records.append(
            {
                "index": index,
                "name": node.get("name"),
                "mesh": node.get("mesh"),
                "children": list(node.get("children", ())),
                "matrix": node.get("matrix"),
                "translation": node.get("translation"),
                "rotation": node.get("rotation"),
                "scale": node.get("scale"),
            }
        )
    return {
        "nodes": node_records,
        "primitives": [
            item.to_payload() for item in decoded.primitive_inventory
        ],
    }


def identity_matrix4() -> Matrix4:
    return (
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    )


def node_local_matrix(node: Mapping[str, Any]) -> Matrix4:
    if "matrix" in node:
        raw = node["matrix"]
        if not isinstance(raw, Sequence) or len(raw) != 16:
            raise FixtureGlbError("fixture node matrix must contain 16 values.")
        values = tuple(_finite(value, "node matrix") for value in raw)
        return tuple(
            values[column * 4 + row]
            for row in range(4)
            for column in range(4)
        )
    translation = _fixed_vector(node.get("translation", (0.0, 0.0, 0.0)), 3, "translation")
    rotation = _fixed_vector(node.get("rotation", (0.0, 0.0, 0.0, 1.0)), 4, "rotation")
    scale = _fixed_vector(node.get("scale", (1.0, 1.0, 1.0)), 3, "scale")
    x, y, z, w = rotation
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm <= 1.0e-15:
        raise FixtureGlbError("fixture node quaternion has zero length.")
    x, y, z, w = (value / norm for value in (x, y, z, w))
    rotation_matrix = (
        1.0 - 2.0 * (y * y + z * z),
        2.0 * (x * y - z * w),
        2.0 * (x * z + y * w),
        0.0,
        2.0 * (x * y + z * w),
        1.0 - 2.0 * (x * x + z * z),
        2.0 * (y * z - x * w),
        0.0,
        2.0 * (x * z - y * w),
        2.0 * (y * z + x * w),
        1.0 - 2.0 * (x * x + y * y),
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    )
    scale_matrix = (
        scale[0],
        0.0,
        0.0,
        0.0,
        0.0,
        scale[1],
        0.0,
        0.0,
        0.0,
        0.0,
        scale[2],
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    )
    translated = list(multiply_matrix4(rotation_matrix, scale_matrix))
    translated[3], translated[7], translated[11] = translation
    return tuple(translated)


def multiply_matrix4(left: Matrix4, right: Matrix4) -> Matrix4:
    return tuple(
        math.fsum(left[row * 4 + inner] * right[inner * 4 + column] for inner in range(4))
        for row in range(4)
        for column in range(4)
    )


def transform_point(matrix: Matrix4, point: Point3) -> Point3:
    x, y, z = point
    return (
        matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
        matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
        matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
    )


def _scene_roots(document: Mapping[str, Any]) -> tuple[int, ...]:
    scenes = _sequence(document, "scenes")
    try:
        scene_index = int(document.get("scene", 0))
        scene = _mapping(scenes[scene_index], "scene")
        roots = tuple(int(value) for value in scene["nodes"])
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise FixtureGlbError("fixture GLB scene declaration is invalid.") from exc
    if not roots or len(set(roots)) != len(roots):
        raise FixtureGlbError("fixture GLB scene roots must be non-empty and unique.")
    return roots


def _normalize_component(value: int | float, component_type: int) -> float:
    if component_type == 5120:
        return max(float(value) / 127.0, -1.0)
    if component_type == 5121:
        return float(value) / 255.0
    if component_type == 5122:
        return max(float(value) / 32767.0, -1.0)
    if component_type == 5123:
        return float(value) / 65535.0
    raise FixtureGlbError("normalized fixture accessor component type is unsupported.")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FixtureGlbError(f"fixture GLB {name} must be an object.")
    return value


def _sequence(value: Mapping[str, Any], name: str) -> Sequence[Any]:
    result = value.get(name)
    if not isinstance(result, list):
        raise FixtureGlbError(f"fixture GLB {name} must be an array.")
    return result


def _fixed_vector(value: Any, count: int, name: str) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != count:
        raise FixtureGlbError(f"fixture node {name} must contain {count} values.")
    return tuple(_finite(item, name) for item in value)


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FixtureGlbError(f"fixture {name} values must be numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise FixtureGlbError(f"fixture {name} values must be finite.")
    return result


__all__ = [
    "DecodedFixtureGlb",
    "FixtureGlbError",
    "GlbPrimitiveInventory",
    "Matrix4",
    "Point3",
    "Triangle",
    "decode_fixture_glb",
    "identity_matrix4",
    "multiply_matrix4",
    "node_inventory_payload",
    "node_local_matrix",
    "transform_point",
]
