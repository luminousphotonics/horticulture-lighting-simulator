"""Deterministic geometric queries on authoritative triangle surfaces."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from fspm_optics.plants.models import ScientificMeshFace, Vector3

FLOAT64_DISTANCE_RELATIVE_TOLERANCE = 1e-12
FLOAT64_DISTANCE_ABSOLUTE_TOLERANCE_M2 = 1e-24


class SurfaceGeometryError(ValueError):
    """An authoritative triangle query cannot be evaluated exactly."""


@dataclass(frozen=True, slots=True)
class ClosestPointResult:
    point: Vector3
    barycentric: tuple[float, float, float]
    squared_distance: float


@dataclass(frozen=True, slots=True)
class FaceClosestPointResult:
    point: Vector3
    barycentric: tuple[float, float, float]
    squared_distance: float
    face: ScientificMeshFace


def closest_point_on_triangle(
    point: Vector3,
    first: Vector3,
    second: Vector3,
    third: Vector3,
    *,
    triangle_id: str = "triangle",
) -> ClosestPointResult:
    """Return the closest point and barycentrics on a non-degenerate triangle."""

    if not all(_finite_vector(value) for value in (point, first, second, third)):
        raise SurfaceGeometryError(
            f"Closest-point input for {triangle_id} is non-finite."
        )
    edge_ab = _subtract(second, first)
    edge_ac = _subtract(third, first)
    cross = _cross(edge_ab, edge_ac)
    cross_squared = _squared_length(cross)
    if not math.isfinite(cross_squared) or cross_squared <= 0.0:
        raise SurfaceGeometryError(
            f"Closest-point triangle {triangle_id} is degenerate."
        )

    offset_ap = _subtract(point, first)
    d1 = _dot(edge_ab, offset_ap)
    d2 = _dot(edge_ac, offset_ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return _result(point, first, (1.0, 0.0, 0.0))

    offset_bp = _subtract(point, second)
    d3 = _dot(edge_ab, offset_bp)
    d4 = _dot(edge_ac, offset_bp)
    if d3 >= 0.0 and d4 <= d3:
        return _result(point, second, (0.0, 1.0, 0.0))

    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        fraction = d1 / (d1 - d3)
        result = _add(first, _scale(edge_ab, fraction))
        return _result(point, result, (1.0 - fraction, fraction, 0.0))

    offset_cp = _subtract(point, third)
    d5 = _dot(edge_ab, offset_cp)
    d6 = _dot(edge_ac, offset_cp)
    if d6 >= 0.0 and d5 <= d6:
        return _result(point, third, (0.0, 0.0, 1.0))

    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        fraction = d2 / (d2 - d6)
        result = _add(first, _scale(edge_ac, fraction))
        return _result(point, result, (1.0 - fraction, 0.0, fraction))

    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        edge_bc = _subtract(third, second)
        fraction = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        result = _add(second, _scale(edge_bc, fraction))
        return _result(point, result, (0.0, 1.0 - fraction, fraction))

    denominator = va + vb + vc
    if denominator <= 0.0 or not math.isfinite(denominator):
        raise SurfaceGeometryError(
            f"Closest-point triangle {triangle_id} has invalid barycentric geometry."
        )
    inverse = 1.0 / denominator
    second_weight = vb * inverse
    third_weight = vc * inverse
    first_weight = 1.0 - second_weight - third_weight
    result = _add(
        first,
        _add(_scale(edge_ab, second_weight), _scale(edge_ac, third_weight)),
    )
    return _result(
        point,
        result,
        (first_weight, second_weight, third_weight),
    )


def closest_point_on_faces(
    point: Vector3,
    faces: Iterable[ScientificMeshFace],
) -> FaceClosestPointResult:
    """Return the closest face point, breaking distance ties by stable face ID."""

    ordered = tuple(sorted(faces, key=lambda face: face.face_id))
    if not ordered:
        raise SurfaceGeometryError(
            "Closest-point measurement requires at least one triangle."
        )
    best: FaceClosestPointResult | None = None
    for face in ordered:
        result = closest_point_on_triangle(
            point,
            *face.vertices,
            triangle_id=face.face_id,
        )
        candidate = FaceClosestPointResult(
            point=result.point,
            barycentric=result.barycentric,
            squared_distance=result.squared_distance,
            face=face,
        )
        if best is None:
            best = candidate
            continue
        tolerance = _distance_tolerance(
            candidate.squared_distance,
            best.squared_distance,
        )
        if candidate.squared_distance < best.squared_distance - tolerance:
            best = candidate
        elif (
            abs(candidate.squared_distance - best.squared_distance) <= tolerance
            and candidate.face.face_id < best.face.face_id
        ):
            best = candidate
    if best is None:  # pragma: no cover - guarded by nonempty input
        raise SurfaceGeometryError("Closest-point selection failed.")
    return best


def barycentric_point(
    vertices: tuple[Vector3, Vector3, Vector3],
    barycentric: tuple[float, float, float],
) -> Vector3:
    """Reconstruct a triangle point from finite normalized barycentrics."""

    if not all(_finite_vector(vertex) for vertex in vertices) or not all(
        math.isfinite(value) for value in barycentric
    ):
        raise SurfaceGeometryError("Barycentric reconstruction input is non-finite.")
    if any(value < -FLOAT64_DISTANCE_RELATIVE_TOLERANCE for value in barycentric):
        raise SurfaceGeometryError("Barycentric coordinates lie outside the triangle.")
    if abs(math.fsum(barycentric) - 1.0) > FLOAT64_DISTANCE_RELATIVE_TOLERANCE:
        raise SurfaceGeometryError("Barycentric coordinates do not sum to one.")
    return (
        math.fsum(barycentric[index] * vertices[index][0] for index in range(3)),
        math.fsum(barycentric[index] * vertices[index][1] for index in range(3)),
        math.fsum(barycentric[index] * vertices[index][2] for index in range(3)),
    )


def _result(
    source: Vector3,
    point: Vector3,
    barycentric: tuple[float, float, float],
) -> ClosestPointResult:
    return ClosestPointResult(point, barycentric, _squared_distance(source, point))


def _add(left: Vector3, right: Vector3) -> Vector3:
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def _subtract(left: Vector3, right: Vector3) -> Vector3:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _scale(vector: Vector3, factor: float) -> Vector3:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def _dot(left: Vector3, right: Vector3) -> float:
    return math.fsum(left[index] * right[index] for index in range(3))


def _cross(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _squared_length(vector: Vector3) -> float:
    return _dot(vector, vector)


def _squared_distance(left: Vector3, right: Vector3) -> float:
    return _squared_length(_subtract(left, right))


def _finite_vector(vector: Vector3) -> bool:
    return len(vector) == 3 and all(math.isfinite(value) for value in vector)


def _distance_tolerance(left: float, right: float) -> float:
    return max(
        FLOAT64_DISTANCE_ABSOLUTE_TOLERANCE_M2,
        FLOAT64_DISTANCE_RELATIVE_TOLERANCE * max(abs(left), abs(right)),
    )


__all__ = [
    "ClosestPointResult",
    "FLOAT64_DISTANCE_ABSOLUTE_TOLERANCE_M2",
    "FLOAT64_DISTANCE_RELATIVE_TOLERANCE",
    "FaceClosestPointResult",
    "SurfaceGeometryError",
    "barycentric_point",
    "closest_point_on_faces",
    "closest_point_on_triangle",
]
