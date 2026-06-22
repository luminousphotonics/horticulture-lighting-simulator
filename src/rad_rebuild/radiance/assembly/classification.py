from __future__ import annotations

import math
from typing import Any, Literal


FixtureShape = Literal["linear", "corner", "centerpiece", "unknown"]

PLACEHOLDER_ASSET_KEY = "placeholder"
COLLINEARITY_TOLERANCE = 1.0e-6


def _layout_type(group: dict[str, Any]) -> str:
    value = group.get("type")
    return value if isinstance(value, str) and value else "unknown"


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _invalid_result(
    group: dict[str, Any],
    *,
    module_count: int,
    warning: str,
) -> dict[str, Any]:
    return {
        "layout_type": _layout_type(group),
        "module_count": module_count,
        "shape": "unknown",
        "asset_key": PLACEHOLDER_ASSET_KEY,
        "points": [],
        "warnings": [warning],
    }


def _parse_points(group: dict[str, Any], group_index: int | None) -> tuple[list[dict[str, float]], list[str], int]:
    label = f"fixture group {group_index}" if group_index is not None else "fixture group"
    raw_points = group.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        return [], [f"{label} has no valid fixture points."], 0

    points: list[dict[str, float]] = []
    for point_index, raw_point in enumerate(raw_points):
        if not isinstance(raw_point, dict):
            return [], [f"{label} point {point_index} is not an object."], len(raw_points)
        x = _finite_float(raw_point.get("x"))
        y = _finite_float(raw_point.get("y"))
        z = _finite_float(raw_point.get("z"))
        if x is None or y is None or z is None:
            return [], [f"{label} point {point_index} must contain finite x, y, and z numbers."], len(raw_points)
        points.append({"x": x, "y": y, "z": z})
    return points, [], len(raw_points)


def _has_duplicate_points(points: list[dict[str, float]], tolerance: float) -> tuple[bool, tuple[int, int] | None]:
    threshold_sq = tolerance * tolerance
    for left_index, left in enumerate(points):
        for right_index in range(left_index + 1, len(points)):
            right = points[right_index]
            dx = left["x"] - right["x"]
            dy = left["y"] - right["y"]
            if dx * dx + dy * dy <= threshold_sq:
                return True, (left_index, right_index)
    return False, None


def _farthest_pair(points: list[dict[str, float]]) -> tuple[int, int, float]:
    pair = (0, 1)
    distance_sq = -1.0
    for left_index, left in enumerate(points):
        for right_index in range(left_index + 1, len(points)):
            right = points[right_index]
            dx = right["x"] - left["x"]
            dy = right["y"] - left["y"]
            candidate = dx * dx + dy * dy
            if candidate > distance_sq:
                distance_sq = candidate
                pair = (left_index, right_index)
    return pair[0], pair[1], distance_sq


def _points_are_collinear(points: list[dict[str, float]], tolerance: float) -> bool:
    if len(points) <= 2:
        return True
    left_index, right_index, baseline_sq = _farthest_pair(points)
    if baseline_sq <= tolerance * tolerance:
        return False
    left = points[left_index]
    right = points[right_index]
    baseline_x = right["x"] - left["x"]
    baseline_y = right["y"] - left["y"]
    baseline_length = math.sqrt(baseline_sq)
    for point in points:
        vector_x = point["x"] - left["x"]
        vector_y = point["y"] - left["y"]
        vector_length = math.hypot(vector_x, vector_y)
        if vector_length <= tolerance:
            continue
        cross = abs(baseline_x * vector_y - baseline_y * vector_x)
        if cross / (baseline_length * vector_length) > tolerance:
            return False
    return True


def _asset_for_geometry(layout_type: str, module_count: int, shape: FixtureShape) -> str | None:
    normalized_type = layout_type.lower()
    if normalized_type == "centerpiece" and module_count == 5:
        return "centerpiece"
    if shape == "linear":
        if module_count == 2:
            return "linear2"
        if module_count == 3:
            return "linear3_linear"
        if module_count == 4:
            return "linear4_linear"
    if module_count == 3 and shape == "corner":
        return "linear3_corner"
    if module_count == 4 and shape == "corner":
        if normalized_type == "l":
            return "l4_corner"
        if normalized_type == "reverse_l":
            return "l4_reverse_corner"
    return None


def classify_fixture_group(
    group: dict[str, Any],
    *,
    group_index: int | None = None,
    tolerance: float = COLLINEARITY_TOLERANCE,
) -> dict[str, Any]:
    """Classify a fixture group from geometry instead of layout type alone."""
    points, parse_warnings, raw_count = _parse_points(group, group_index)
    if parse_warnings:
        return _invalid_result(group, module_count=raw_count, warning=parse_warnings[0])

    duplicate, duplicate_indexes = _has_duplicate_points(points, tolerance)
    if duplicate:
        left, right = duplicate_indexes or (0, 0)
        label = f"fixture group {group_index}" if group_index is not None else "fixture group"
        return _invalid_result(
            group,
            module_count=len(points),
            warning=f"{label} contains duplicate layout points at indexes {left} and {right}.",
        )

    layout_type = _layout_type(group)
    module_count = len(points)
    if layout_type.lower() == "centerpiece" and module_count == 5:
        shape: FixtureShape = "centerpiece"
    else:
        shape = "linear" if _points_are_collinear(points, tolerance) else "corner"

    asset_key = _asset_for_geometry(layout_type, module_count, shape)
    warnings: list[str] = []
    if asset_key is None:
        asset_key = PLACEHOLDER_ASSET_KEY
        label = f"fixture group {group_index}" if group_index is not None else "fixture group"
        warnings.append(
            f"{label} has unsupported geometry: layout_type={layout_type!r}, "
            f"module_count={module_count}, shape={shape}."
        )

    return {
        "layout_type": layout_type,
        "module_count": module_count,
        "shape": shape,
        "asset_key": asset_key,
        "points": points,
        "warnings": warnings,
    }
