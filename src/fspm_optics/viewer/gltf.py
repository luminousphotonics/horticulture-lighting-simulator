"""Minimal deterministic GLB encoder for the canonical juvenile plant mesh."""

from __future__ import annotations

import json
import math
import struct
from typing import Iterable, cast

from fspm_optics.plants.models import LeafSurface, PlantMesh, Vector3

GLB_MAGIC = 0x46546C67
GLB_VERSION = 2
JSON_CHUNK_TYPE = 0x4E4F534A
BIN_CHUNK_TYPE = 0x004E4942


def scientific_to_display(vector: Vector3) -> Vector3:
    """Apply the proper right-handed meters/z-up to meters/Y-up rotation."""

    return (vector[0], vector[2], -vector[1])


def build_juvenile_geometry_glb(
    plant: PlantMesh,
    face_to_patch: tuple[int, ...],
) -> tuple[bytes, float, float]:
    """Encode one nonindexed display mesh with leaf-local smooth normals and UVs."""

    if len(face_to_patch) != plant.face_count:
        raise ValueError("face_to_patch length must equal the plant face count.")
    positions: list[float] = []
    normals: list[float] = []
    texcoords: list[float] = []
    leaf_indices: list[int] = []
    face_indices: list[int] = []
    patch_indices: list[int] = []
    max_position_error = 0.0
    max_normal_error = 0.0

    face_index = 0
    for leaf_index, leaf in enumerate(plant.leaves):
        smooth_normals = _smooth_leaf_normals(leaf)
        vertex_texcoords = _leaf_vertex_texcoords(plant, leaf)
        for face in leaf.faces:
            if face.leaf_rank - 1 != leaf_index:
                raise ValueError(
                    "Leaf face ordering is incompatible with compact identity."
                )
            for vertex_index, vertex in zip(
                face.vertex_indices,
                face.vertices,
                strict=True,
            ):
                if vertex != leaf.vertices[vertex_index]:
                    raise ValueError(
                        "Leaf face vertices do not match source vertex indices."
                    )
                display_position = scientific_to_display(vertex)
                display_normal = scientific_to_display(smooth_normals[vertex_index])
                positions.extend(display_position)
                normals.extend(display_normal)
                texcoords.extend(vertex_texcoords[vertex_index])
                leaf_indices.append(leaf_index)
                face_indices.append(face_index)
                patch_indices.append(face_to_patch[face_index])
                max_position_error = max(
                    max_position_error,
                    _float32_vector_error(display_position),
                )
                max_normal_error = max(
                    max_normal_error,
                    _float32_vector_error(display_normal),
                )
            face_index += 1
    if face_index != plant.face_count:
        raise ValueError(
            "Leaf face emission did not preserve the canonical face count."
        )

    vertex_count = plant.face_count * 3
    position_bytes = struct.pack(f"<{len(positions)}f", *positions)
    normal_bytes = struct.pack(f"<{len(normals)}f", *normals)
    texcoord_bytes = struct.pack(f"<{len(texcoords)}f", *texcoords)
    leaf_bytes = struct.pack(f"<{vertex_count}I", *leaf_indices)
    face_bytes = struct.pack(f"<{vertex_count}I", *face_indices)
    patch_bytes = struct.pack(f"<{vertex_count}I", *patch_indices)
    binary, offsets = _join_aligned(
        (
            position_bytes,
            normal_bytes,
            texcoord_bytes,
            leaf_bytes,
            face_bytes,
            patch_bytes,
        )
    )
    position_min, position_max = _component_bounds(positions)
    document = {
        "asset": {
            "generator": "fspm-optics juvenile scientific viewer publisher",
            "version": "2.0",
        },
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {
                "buffer": 0,
                "byteLength": len(block),
                "byteOffset": offset,
                "target": 34962,
            }
            for block, offset in zip(
                (
                    position_bytes,
                    normal_bytes,
                    texcoord_bytes,
                    leaf_bytes,
                    face_bytes,
                    patch_bytes,
                ),
                offsets,
                strict=True,
            )
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": vertex_count,
                "max": position_max,
                "min": position_min,
                "type": "VEC3",
            },
            {
                "bufferView": 1,
                "componentType": 5126,
                "count": vertex_count,
                "type": "VEC3",
            },
            {
                "bufferView": 2,
                "componentType": 5126,
                "count": vertex_count,
                "max": [1.0, 1.0],
                "min": [0.0, 0.0],
                "type": "VEC2",
            },
            *(
                {
                    "bufferView": buffer_view,
                    "componentType": 5125,
                    "count": vertex_count,
                    "type": "SCALAR",
                }
                for buffer_view in (3, 4, 5)
            ),
        ],
        "materials": [
            {
                "doubleSided": True,
                "name": "juvenile_rex_scientific_surface",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.18, 0.48, 0.145, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.82,
                },
            }
        ],
        "meshes": [
            {
                "name": plant.config.profile_id,
                "primitives": [
                    {
                        "attributes": {
                            "NORMAL": 1,
                            "POSITION": 0,
                            "TEXCOORD_0": 2,
                            "_FACE_INDEX": 4,
                            "_LEAF_INDEX": 3,
                            "_PATCH_INDEX": 5,
                        },
                        "material": 0,
                        "mode": 4,
                    }
                ],
            }
        ],
        "nodes": [{"mesh": 0, "name": plant.plant_id}],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
    }
    json_bytes = json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _encode_glb(json_bytes, binary), max_position_error, max_normal_error


def _smooth_leaf_normals(leaf: LeafSurface) -> tuple[Vector3, ...]:
    """Return deterministic area-weighted normals scoped to one source leaf."""

    accumulated = [[0.0, 0.0, 0.0] for _ in leaf.vertices]
    incident_faces: list[list[int]] = [[] for _ in leaf.vertices]
    for face_offset, face in enumerate(leaf.faces):
        first, second, third = (
            leaf.vertices[index] for index in face.vertex_indices
        )
        edge_ab = tuple(second[axis] - first[axis] for axis in range(3))
        edge_ac = tuple(third[axis] - first[axis] for axis in range(3))
        cross = (
            edge_ab[1] * edge_ac[2] - edge_ab[2] * edge_ac[1],
            edge_ab[2] * edge_ac[0] - edge_ab[0] * edge_ac[2],
            edge_ab[0] * edge_ac[1] - edge_ab[1] * edge_ac[0],
        )
        if not all(math.isfinite(value) for value in cross):
            raise ValueError("Viewer smooth-normal accumulation is non-finite.")
        for vertex_index in face.vertex_indices:
            for axis in range(3):
                accumulated[vertex_index][axis] += cross[axis]
            incident_faces[vertex_index].append(face_offset)

    result: list[Vector3] = []
    for vertex_index, vector in enumerate(accumulated):
        magnitude = math.sqrt(math.fsum(value * value for value in vector))
        if not math.isfinite(magnitude) or magnitude <= 0.0:
            raise ValueError("Viewer smooth normal must have finite positive length.")
        normal = cast(Vector3, tuple(value / magnitude for value in vector))
        if any(
            math.fsum(
                normal[axis] * leaf.faces[face_offset].unit_normal[axis]
                for axis in range(3)
            )
            <= 0.0
            for face_offset in incident_faces[vertex_index]
        ):
            raise ValueError(
                "Viewer smooth normal left the winding-defined hemisphere."
            )
        result.append(normal)
    return tuple(result)


def _leaf_vertex_texcoords(
    plant: PlantMesh,
    leaf: LeafSurface,
) -> tuple[tuple[float, float], ...]:
    """Recover exact generator parameters from authoritative structured indices."""

    u_segments, v_segments = plant.config.mesh_segments_for_layer(leaf.leaf_layer)
    expected_vertex_count = 2 + (u_segments - 1) * (v_segments + 1)
    if len(leaf.vertices) != expected_vertex_count:
        raise ValueError(
            "Leaf vertex count does not match its structured UV provenance."
        )
    coordinates: list[tuple[float, float]] = [(0.0, 0.5)]
    for u_index in range(1, u_segments):
        u = u_index / u_segments
        for v_index in range(v_segments + 1):
            coordinates.append((u, v_index / v_segments))
    coordinates.append((1.0, 0.5))
    return tuple(coordinates)


def _float32_vector_error(vector: Vector3) -> float:
    converted = struct.unpack("<3f", struct.pack("<3f", *vector))
    return max(abs(actual - rounded) for actual, rounded in zip(vector, converted))


def _join_aligned(blocks: Iterable[bytes]) -> tuple[bytes, tuple[int, ...]]:
    result = bytearray()
    offsets: list[int] = []
    for block in blocks:
        while len(result) % 4:
            result.append(0)
        offsets.append(len(result))
        result.extend(block)
    while len(result) % 4:
        result.append(0)
    return bytes(result), tuple(offsets)


def _component_bounds(values: list[float]) -> tuple[list[float], list[float]]:
    if not values or len(values) % 3:
        raise ValueError("POSITION values must contain complete vectors.")
    components = tuple(values[index::3] for index in range(3))
    return (
        [min(component) for component in components],
        [max(component) for component in components],
    )


def _encode_glb(json_bytes: bytes, binary: bytes) -> bytes:
    json_padding = (-len(json_bytes)) % 4
    padded_json = json_bytes + b" " * json_padding
    binary_padding = (-len(binary)) % 4
    padded_binary = binary + b"\0" * binary_padding
    total_length = 12 + 8 + len(padded_json) + 8 + len(padded_binary)
    if not math.isfinite(float(total_length)):
        raise ValueError("GLB length is invalid.")
    return b"".join(
        (
            struct.pack("<III", GLB_MAGIC, GLB_VERSION, total_length),
            struct.pack("<II", len(padded_json), JSON_CHUNK_TYPE),
            padded_json,
            struct.pack("<II", len(padded_binary), BIN_CHUNK_TYPE),
            padded_binary,
        )
    )
