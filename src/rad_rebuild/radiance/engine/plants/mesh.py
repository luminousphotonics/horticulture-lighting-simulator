"""Mesh helpers for deterministic lettuce-style leaf surfaces."""

from __future__ import annotations

import math

from rad_rebuild.radiance.engine.plants.models import LeafMesh, TriangleFace, Vector3

LEAF_LENGTH_SEGMENTS = 4
LEAF_WIDTH_SEGMENTS = 2
LEAF_VERTEX_COUNT = (LEAF_LENGTH_SEGMENTS + 1) * (LEAF_WIDTH_SEGMENTS + 1)
LEAF_FACE_COUNT = LEAF_LENGTH_SEGMENTS * LEAF_WIDTH_SEGMENTS * 2


def build_curved_leaf_mesh(
    *,
    origin_m: Vector3,
    azimuth_rad: float,
    length_m: float,
    width_m: float,
    tilt_rad: float,
    curvature_m: float,
    max_height_m: float,
) -> LeafMesh:
    """Build a low-density curved leaf mesh in meters."""

    local_vertices: list[Vector3] = []
    sin_tilt = math.sin(tilt_rad)
    cos_tilt = math.cos(tilt_rad)

    for i in range(LEAF_LENGTH_SEGMENTS + 1):
        v = i / LEAF_LENGTH_SEGMENTS
        profile = 0.08 + 0.92 * math.sin(math.pi * v)
        forward_m = length_m * v
        horizontal_m = forward_m * cos_tilt
        lift_m = forward_m * sin_tilt + curvature_m * math.sin(math.pi * v)

        for j in range(LEAF_WIDTH_SEGMENTS + 1):
            u = (j / LEAF_WIDTH_SEGMENTS) * 2.0 - 1.0
            lateral_m = u * width_m * 0.5 * profile
            cross_curve_m = -abs(u) * curvature_m * 0.12 * math.sin(math.pi * v)
            local_vertices.append((horizontal_m, lateral_m, lift_m + cross_curve_m))

    max_local_z = max(vertex[2] for vertex in local_vertices)
    if max_local_z > max_height_m:
        scale_z = max_height_m / max_local_z
        local_vertices = [
            (vertex[0], vertex[1], vertex[2] * scale_z) for vertex in local_vertices
        ]

    vertices = tuple(
        _rotate_and_translate(vertex, origin_m, azimuth_rad)
        for vertex in local_vertices
    )
    faces = _build_faces()
    return LeafMesh(vertices=vertices, faces=faces)


def _rotate_and_translate(
    local: Vector3,
    origin_m: Vector3,
    azimuth_rad: float,
) -> Vector3:
    x_local, y_local, z_local = local
    cos_azimuth = math.cos(azimuth_rad)
    sin_azimuth = math.sin(azimuth_rad)
    x_world = origin_m[0] + x_local * cos_azimuth - y_local * sin_azimuth
    y_world = origin_m[1] + x_local * sin_azimuth + y_local * cos_azimuth
    z_world = origin_m[2] + z_local
    return (x_world, y_world, z_world)


def _build_faces() -> tuple[TriangleFace, ...]:
    faces: list[TriangleFace] = []
    row_size = LEAF_WIDTH_SEGMENTS + 1
    for i in range(LEAF_LENGTH_SEGMENTS):
        for j in range(LEAF_WIDTH_SEGMENTS):
            lower_left = i * row_size + j
            lower_right = lower_left + 1
            upper_left = (i + 1) * row_size + j
            upper_right = upper_left + 1
            faces.append((lower_left, upper_left, lower_right))
            faces.append((lower_right, upper_left, upper_right))
    return tuple(faces)
