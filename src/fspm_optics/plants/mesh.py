"""Mesh helpers for deterministic lettuce-style leaf surfaces."""

from __future__ import annotations

import math
from typing import cast

from fspm_optics.plants.models import (
    LeafLayer,
    LeafMesh,
    ScientificMeshFace,
    ScientificMeshPatch,
    TriangleFace,
    Vector3,
)
from fspm_optics.plants.surface_geometry import closest_point_on_faces

LEAF_LENGTH_SEGMENTS = 4
LEAF_WIDTH_SEGMENTS = 2
LEAF_VERTEX_COUNT = (LEAF_LENGTH_SEGMENTS + 1) * (LEAF_WIDTH_SEGMENTS + 1)
LEAF_FACE_COUNT = LEAF_LENGTH_SEGMENTS * LEAF_WIDTH_SEGMENTS * 2


def triangle_centroid(a: Vector3, b: Vector3, c: Vector3) -> Vector3:
    """Return the arithmetic centroid of one triangle."""

    return cast(
        Vector3,
        tuple((a[index] + b[index] + c[index]) / 3.0 for index in range(3)),
    )


def triangle_area_m2(a: Vector3, b: Vector3, c: Vector3) -> float:
    """Return a triangle's area in square meters."""

    cross = _triangle_cross(a, b, c)
    magnitude = math.sqrt(sum(component * component for component in cross))
    return 0.5 * magnitude


def triangle_unit_normal(a: Vector3, b: Vector3, c: Vector3) -> Vector3:
    """Return the winding-defined unit normal for a non-degenerate triangle."""

    cross = _triangle_cross(a, b, c)
    magnitude = math.sqrt(sum(component * component for component in cross))
    if not math.isfinite(magnitude) or magnitude <= 0.0:
        raise ValueError("Triangle geometry must have finite positive area.")
    return cast(Vector3, tuple(component / magnitude for component in cross))


def scientific_mesh_faces(
    *,
    plant_id: str,
    leaf_id: str,
    leaf_rank: int,
    leaf_layer: LeafLayer,
    vertices: tuple[Vector3, ...],
    triangle_indices: tuple[TriangleFace, ...],
) -> tuple[ScientificMeshFace, ...]:
    """Build stable scientific face records from one canonical leaf mesh."""

    records: list[ScientificMeshFace] = []
    for face_index, indices in enumerate(triangle_indices):
        if any(
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index >= len(vertices)
            for index in indices
        ):
            raise ValueError(
                f"Leaf {leaf_id} face {face_index} references a missing vertex."
            )
        try:
            face_vertices = (
                vertices[indices[0]],
                vertices[indices[1]],
                vertices[indices[2]],
            )
        except IndexError as exc:
            raise ValueError(
                f"Leaf {leaf_id} face {face_index} references a missing vertex."
            ) from exc
        area = triangle_area_m2(*face_vertices)
        if not math.isfinite(area) or area <= 0.0:
            raise ValueError(
                f"Leaf {leaf_id} face {face_index} must have finite positive area."
            )
        face_id = f"{leaf_id}_face_{face_index:04d}"
        records.append(
            ScientificMeshFace(
                plant_id=plant_id,
                leaf_id=leaf_id,
                leaf_rank=leaf_rank,
                leaf_layer=leaf_layer,
                face_id=face_id,
                face_index=face_index,
                vertex_indices=indices,
                vertices=face_vertices,
                centroid=triangle_centroid(*face_vertices),
                unit_normal=triangle_unit_normal(*face_vertices),
                area_m2=area,
            )
        )
    return tuple(records)


def scientific_mesh_patches(
    *,
    plant_id: str,
    leaf_id: str,
    leaf_rank: int,
    leaf_layer: LeafLayer,
    faces: tuple[ScientificMeshFace, ...],
    u_segments: int,
    v_segments: int,
    patch_u_count: int,
    patch_v_count: int,
    u_cuts: tuple[int, ...] | None = None,
    v_cuts: tuple[int, ...] | None = None,
    surface_constrained_receivers: bool = False,
) -> tuple[ScientificMeshPatch, ...]:
    """Aggregate exact triangulated cells into stable parametric receiver patches."""

    if (u_cuts is None) != (v_cuts is None):
        raise ValueError("Longitudinal and lateral cuts must be supplied together.")
    if u_cuts is None:
        if (
            u_segments % patch_u_count != 0
            or v_segments % patch_v_count != 0
        ):
            raise ValueError("Leaf mesh segments must be divisible by the patch grid.")
        u_bounds = tuple(
            index * u_segments // patch_u_count
            for index in range(patch_u_count + 1)
        )
        v_bounds = tuple(
            index * v_segments // patch_v_count
            for index in range(patch_v_count + 1)
        )
    else:
        u_bounds = _validated_patch_bounds(
            u_cuts,
            segment_count=u_segments,
            patch_count=patch_u_count,
            axis="u",
        )
        v_bounds = _validated_patch_bounds(
            v_cuts,
            segment_count=v_segments,
            patch_count=patch_v_count,
            axis="v",
        )
    grouped: list[list[ScientificMeshFace]] = [
        [] for _ in range(patch_u_count * patch_v_count)
    ]
    cursor = 0
    for u_cell in range(u_segments):
        patch_u = _patch_band(u_cell, u_bounds)
        triangle_count = 1 if u_cell in {0, u_segments - 1} else 2
        for v_cell in range(v_segments):
            patch_v = _patch_band(v_cell, v_bounds)
            group_index = patch_u * patch_v_count + patch_v
            grouped[group_index].extend(
                faces[cursor : cursor + triangle_count]
            )
            cursor += triangle_count
    if cursor != len(faces):
        raise ValueError("Leaf triangle ordering does not match patch aggregation.")

    patches: list[ScientificMeshPatch] = []
    for patch_u in range(patch_u_count):
        for patch_v in range(patch_v_count):
            patch_index = patch_u * patch_v_count + patch_v
            patch_faces = grouped[patch_index]
            if not patch_faces:
                raise ValueError("Every receiver patch must contain triangle faces.")
            area = math.fsum(face.area_m2 for face in patch_faces)
            centroid = cast(
                Vector3,
                tuple(
                    math.fsum(
                        face.centroid[axis] * face.area_m2
                        for face in patch_faces
                    )
                    / area
                    for axis in range(3)
                ),
            )
            integrated_normal = tuple(
                math.fsum(
                    face.unit_normal[axis] * face.area_m2
                    for face in patch_faces
                )
                for axis in range(3)
            )
            magnitude = math.sqrt(
                sum(component * component for component in integrated_normal)
            )
            if not math.isfinite(magnitude) or magnitude <= 0.0:
                raise ValueError("Receiver patch has an invalid integrated normal.")
            unit_normal = cast(
                Vector3,
                tuple(component / magnitude for component in integrated_normal),
            )
            patch_id = f"{leaf_id}_patch_u{patch_u:02d}_v{patch_v:02d}"
            carrier = (
                closest_point_on_faces(centroid, patch_faces)
                if surface_constrained_receivers
                else None
            )
            patches.append(
                ScientificMeshPatch(
                    plant_id=plant_id,
                    leaf_id=leaf_id,
                    leaf_rank=leaf_rank,
                    leaf_layer=leaf_layer,
                    patch_id=patch_id,
                    face_id=patch_id,
                    patch_index=patch_index,
                    patch_u_index=patch_u,
                    patch_v_index=patch_v,
                    triangle_face_ids=tuple(
                        face.face_id for face in patch_faces
                    ),
                    centroid=centroid,
                    unit_normal=unit_normal,
                    area_m2=area,
                    surface_anchor=(carrier.point if carrier is not None else None),
                    carrier_face_id=(
                        carrier.face.face_id if carrier is not None else None
                    ),
                    carrier_barycentric=(
                        carrier.barycentric if carrier is not None else None
                    ),
                    carrier_unit_normal=(
                        carrier.face.unit_normal if carrier is not None else None
                    ),
                    receiver_normal_basis=(
                        "carrier_triangle_winding_defined_unit_normal"
                        if carrier is not None
                        else "area_weighted_patch_normal_legacy"
                    ),
                )
            )
    return tuple(patches)


def _validated_patch_bounds(
    cuts: tuple[int, ...],
    *,
    segment_count: int,
    patch_count: int,
    axis: str,
) -> tuple[int, ...]:
    if (
        len(cuts) != patch_count - 1
        or any(isinstance(value, bool) or not isinstance(value, int) for value in cuts)
        or tuple(sorted(cuts)) != cuts
        or len(set(cuts)) != len(cuts)
        or any(value <= 0 or value >= segment_count for value in cuts)
    ):
        raise ValueError(
            f"{axis}-axis cuts must be increasing internal cell boundaries."
        )
    return (0, *cuts, segment_count)


def _patch_band(cell_index: int, bounds: tuple[int, ...]) -> int:
    for band in range(len(bounds) - 1):
        if bounds[band] <= cell_index < bounds[band + 1]:
            return band
    raise ValueError("Mesh cell lies outside declared patch boundaries.")


def build_rex_leaf_mesh(
    *,
    origin_m: Vector3,
    azimuth_rad: float,
    elevation_rad: float,
    length_m: float,
    width_m: float,
    arch_strength_m: float,
    cup_strength_m: float,
    edge_curl_m: float,
    tip_sag_m: float,
    margin_wave_amplitude: float,
    margin_wave_phase_rad: float,
    u_segments: int,
    v_segments: int,
    distal_lift_m: float = 0.0,
) -> LeafMesh:
    """Build a broad, rounded, curved Rex leaf with non-degenerate tip fans."""

    if u_segments < 3 or v_segments < 2:
        raise ValueError("Rex leaf resolution requires u>=3 and v>=2 segments.")
    if length_m <= 0.0 or width_m <= 0.0:
        raise ValueError("Rex leaf length and width must be positive.")
    vertices: list[Vector3] = [
        _rex_surface_point(
            origin_m=origin_m,
            azimuth_rad=azimuth_rad,
            elevation_rad=elevation_rad,
            length_m=length_m,
            width_m=width_m,
            arch_strength_m=arch_strength_m,
            cup_strength_m=cup_strength_m,
            edge_curl_m=edge_curl_m,
            tip_sag_m=tip_sag_m,
            margin_wave_amplitude=margin_wave_amplitude,
            margin_wave_phase_rad=margin_wave_phase_rad,
            distal_lift_m=distal_lift_m,
            u=0.0,
            v=0.0,
        )
    ]
    interior_rows: list[tuple[int, ...]] = []
    for u_index in range(1, u_segments):
        u = u_index / u_segments
        row: list[int] = []
        for v_index in range(v_segments + 1):
            v = -1.0 + 2.0 * v_index / v_segments
            row.append(len(vertices))
            vertices.append(
                _rex_surface_point(
                    origin_m=origin_m,
                    azimuth_rad=azimuth_rad,
                    elevation_rad=elevation_rad,
                    length_m=length_m,
                    width_m=width_m,
                    arch_strength_m=arch_strength_m,
                    cup_strength_m=cup_strength_m,
                    edge_curl_m=edge_curl_m,
                    tip_sag_m=tip_sag_m,
                    margin_wave_amplitude=margin_wave_amplitude,
                    margin_wave_phase_rad=margin_wave_phase_rad,
                    distal_lift_m=distal_lift_m,
                    u=u,
                    v=v,
                )
            )
        interior_rows.append(tuple(row))
    tip_index = len(vertices)
    vertices.append(
        _rex_surface_point(
            origin_m=origin_m,
            azimuth_rad=azimuth_rad,
            elevation_rad=elevation_rad,
            length_m=length_m,
            width_m=width_m,
            arch_strength_m=arch_strength_m,
            cup_strength_m=cup_strength_m,
            edge_curl_m=edge_curl_m,
            tip_sag_m=tip_sag_m,
            margin_wave_amplitude=margin_wave_amplitude,
            margin_wave_phase_rad=margin_wave_phase_rad,
            distal_lift_m=distal_lift_m,
            u=1.0,
            v=0.0,
        )
    )
    faces: list[TriangleFace] = []
    first_row = interior_rows[0]
    for v_index in range(v_segments):
        faces.append((0, first_row[v_index], first_row[v_index + 1]))
    for lower, upper in zip(interior_rows, interior_rows[1:], strict=False):
        for v_index in range(v_segments):
            lower_left = lower[v_index]
            lower_right = lower[v_index + 1]
            upper_left = upper[v_index]
            upper_right = upper[v_index + 1]
            faces.append((lower_left, upper_left, lower_right))
            faces.append((lower_right, upper_left, upper_right))
    last_row = interior_rows[-1]
    for v_index in range(v_segments):
        faces.append((last_row[v_index], tip_index, last_row[v_index + 1]))
    return LeafMesh(vertices=tuple(vertices), faces=tuple(faces))


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


def _triangle_cross(a: Vector3, b: Vector3, c: Vector3) -> Vector3:
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    return (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )


def _rex_surface_point(
    *,
    origin_m: Vector3,
    azimuth_rad: float,
    elevation_rad: float,
    length_m: float,
    width_m: float,
    arch_strength_m: float,
    cup_strength_m: float,
    edge_curl_m: float,
    tip_sag_m: float,
    margin_wave_amplitude: float,
    margin_wave_phase_rad: float,
    distal_lift_m: float,
    u: float,
    v: float,
) -> Vector3:
    width_profile = math.sin(math.pi * u) ** 0.58 * (1.0 - 0.12 * u)
    margin_wave = math.sin(5.0 * math.pi * u + margin_wave_phase_rad)
    outline_scale = 1.0 + margin_wave_amplitude * margin_wave * abs(v) ** 2
    lateral_m = 0.5 * width_m * v * width_profile * outline_scale
    forward_m = length_m * u
    horizontal_m = forward_m * math.cos(elevation_rad)
    lift_m = forward_m * math.sin(elevation_rad)
    longitudinal_profile = math.sin(math.pi * u)
    vertical_m = (
        lift_m
        + arch_strength_m * longitudinal_profile
        + cup_strength_m * longitudinal_profile * v * v
        + edge_curl_m * u**1.8 * abs(v) ** 3
        - tip_sag_m * u**3
        + 0.18
        * width_m
        * margin_wave_amplitude
        * margin_wave
        * longitudinal_profile
        * abs(v) ** 2
    )
    if distal_lift_m != 0.0:
        vertical_m += distal_lift_m * u * u * (3.0 - 2.0 * u)
    cos_azimuth = math.cos(azimuth_rad)
    sin_azimuth = math.sin(azimuth_rad)
    return (
        origin_m[0] + horizontal_m * cos_azimuth - lateral_m * sin_azimuth,
        origin_m[1] + horizontal_m * sin_azimuth + lateral_m * cos_azimuth,
        origin_m[2] + vertical_m,
    )
