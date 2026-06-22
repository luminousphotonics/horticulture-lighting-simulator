from __future__ import annotations

import math
import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

from rad_rebuild.radiance.engine.emitters.smd_generation.config import env_float as _env_float
from rad_rebuild.radiance.engine.geometry.rect_layout import build_rect_grid
from rad_rebuild.radiance.engine.layout.layout_generator import (
    generate_layout_with_zones,
)

PositionRecord = dict[str, Any]
JsonObject = dict[str, Any]
LayoutGenerator = Any

SQRT2 = math.sqrt(2.0)
RECT_RECT_RING_TARGET_COUNTS = (7, 16, 20, 24, 28, 32, 36, 40)
_generate_layout_with_zones = cast(LayoutGenerator, generate_layout_with_zones)


@dataclass(frozen=True)
class PerimeterGapGeometry:
    ring_max: int
    inner_ring: int
    outer: list[PositionRecord]
    center_x: float
    center_y: float
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    min_step: float
    tolerance: float


@dataclass(frozen=True)
class PerimeterGapEdges:
    left: list[PositionRecord]
    right: list[PositionRecord]
    bottom: list[PositionRecord]
    top: list[PositionRecord]
    axis_aligned: bool


@dataclass(frozen=True)
class SmdPositionSettings:
    height_m: float
    wall_margin_m: float
    ring_n: int
    rings: int
    base_ring_n: int
    mount_z_m: float
    patch_side_m: float
    module_footprint_x_m: float
    module_footprint_y_m: float
    fixed_pitch_m: float
    fixture_angle_m: float
    layout_mode: str
    perimeter_gap_fill: bool


@dataclass(frozen=True)
class SmdPositionContext:
    length_m: float
    width_m: float
    height_m: float
    margin_m: float
    patch_m: float
    module_footprint_x_m: float
    module_footprint_y_m: float
    module_half_x: float
    module_half_y: float
    module_half_max: float
    layout_mode: str
    fixed_pitch: float
    fixture_guard_m: float
    usable_half_x: float
    usable_half_y: float
    base_pitch_axis: float
    base_ring_n: int
    ring_n: int
    rings: int
    mount_z_m: float
    perimeter_gap_fill: bool


def _solve_spacing(
    rings: int,
    length_m: float,
    width_m: float,
    wall_margin_m: float,
    module_half_x: float,
    module_half_y: float,
) -> float:
    """
    rings = n+1, so use L = n for geometry.
    Enforce that the module square stays inside margin and lattice fits n rings.
    """
    L = max(1, rings - 1)
    half_x = 0.5 * length_m - wall_margin_m - module_half_x
    half_y = 0.5 * width_m - wall_margin_m - module_half_y
    lim = min(half_x, half_y)
    if lim <= 0:
        raise SystemExit("ERROR: Not enough interior span given margin and module size.")
    return lim * math.sqrt(2.0) / L


def _ring_ij(L: int) -> list[tuple[int, int]]:
    if L == 0:
        return [(0, 0)]
    pts: list[tuple[int, int]] = []
    i = L
    j = 0
    for k in range(L):
        pts.append((i - k, j - k))
    for k in range(L):
        pts.append((0 - k, -L + k))
    for k in range(L):
        pts.append((-L + k, 0 + k))
    for k in range(L):
        pts.append((0 + k, L - k))
    return pts


def _ij_to_xy(i: int, j: int, spacing: float) -> tuple[float, float]:
    return ((i - j) * spacing / SQRT2, (i + j) * spacing / SQRT2)


def _min_positive_step(values: list[float], min_tol: float = 1e-4) -> float:
    diffs = [
        values[index + 1] - values[index]
        for index in range(len(values) - 1)
        if values[index + 1] - values[index] > min_tol
    ]
    return min(diffs) if diffs else 0.0


def _perimeter_gap_geometry(
    positions: list[PositionRecord],
) -> PerimeterGapGeometry | None:
    if not positions:
        return None

    ring_max = max(int(p.get("ring", 0)) for p in positions)
    if ring_max <= 0:
        return None

    outer = [p for p in positions if int(p.get("ring", 0)) == ring_max]
    if not outer:
        return None

    cx = sum(float(p.get("x", 0.0)) for p in positions) / max(1, len(positions))
    cy = sum(float(p.get("y", 0.0)) for p in positions) / max(1, len(positions))
    xs = sorted({round(float(p.get("x", 0.0)), 6) for p in outer})
    ys = sorted({round(float(p.get("y", 0.0)), 6) for p in outer})
    min_step = min(
        [
            step
            for step in (_min_positive_step(xs), _min_positive_step(ys))
            if step > 0.0
        ],
        default=0.0,
    )
    return PerimeterGapGeometry(
        ring_max=ring_max,
        inner_ring=ring_max - 1,
        outer=outer,
        center_x=cx,
        center_y=cy,
        x_min=min(xs),
        x_max=max(xs),
        y_min=min(ys),
        y_max=max(ys),
        min_step=min_step,
        tolerance=max(1e-4, min_step * 0.3) if min_step > 0 else 1e-4,
    )


def _perimeter_gap_edges(geometry: PerimeterGapGeometry) -> PerimeterGapEdges:
    tol = geometry.tolerance
    left = [
        p for p in geometry.outer if abs(float(p.get("x", 0.0)) - geometry.x_min) <= tol
    ]
    right = [
        p for p in geometry.outer if abs(float(p.get("x", 0.0)) - geometry.x_max) <= tol
    ]
    bottom = [
        p for p in geometry.outer if abs(float(p.get("y", 0.0)) - geometry.y_min) <= tol
    ]
    top = [
        p for p in geometry.outer if abs(float(p.get("y", 0.0)) - geometry.y_max) <= tol
    ]
    return PerimeterGapEdges(
        left=left,
        right=right,
        bottom=bottom,
        top=top,
        axis_aligned=bool(left and right and top and bottom),
    )


def _drop_perimeter_inner_ring(
    positions: list[PositionRecord],
    geometry: PerimeterGapGeometry,
    edges: PerimeterGapEdges,
) -> list[PositionRecord]:
    if not edges.axis_aligned:
        return [p for p in positions if int(p.get("ring", 0)) != geometry.inner_ring]
    kept: list[PositionRecord] = []
    for position in positions:
        if int(position.get("ring", 0)) != geometry.inner_ring:
            kept.append(position)
            continue
        x = float(position.get("x", 0.0))
        if (
            abs(x - geometry.x_min) > geometry.tolerance
            and abs(x - geometry.x_max) > geometry.tolerance
        ):
            kept.append(position)
    return kept


def _position_keys(positions: list[PositionRecord]) -> set[tuple[float, float]]:
    return {
        (
            round(float(position.get("x", 0.0)), 6),
            round(float(position.get("y", 0.0)), 6),
        )
        for position in positions
    }


def _append_gap_fill_point(
    kept: list[PositionRecord],
    existing: set[tuple[float, float]],
    *,
    ring: int,
    i_value: float,
    j_value: float,
    x: float,
    y: float,
    z: float,
) -> bool:
    key = (round(x, 6), round(y, 6))
    if key in existing:
        return False
    kept.append(
        {
            "ring": ring,
            "i": i_value,
            "j": j_value,
            "x": round(x, 6),
            "y": round(y, 6),
            "z": z,
        }
    )
    existing.add(key)
    return True


def _edge_range(start: float, end: float, step: float) -> Iterator[float]:
    if step <= 0:
        return
    count = int(math.floor((end - start) / step + 1e-6))
    for index in range(count + 1):
        value = start + index * step
        if value > end + 1e-6:
            break
        yield value


def _axis_edge_step(
    values_a: list[float], values_b: list[float], fallback: float
) -> float:
    base_step = min(
        [
            step
            for step in (
                _min_positive_step(values_a),
                _min_positive_step(values_b),
                fallback,
            )
            if step > 0.0
        ],
        default=0.0,
    )
    return base_step * 0.5 if base_step > 0 else 0.0


def _add_axis_range_points(
    kept: list[PositionRecord],
    existing: set[tuple[float, float]],
    geometry: PerimeterGapGeometry,
    *,
    step: float,
    along_axis: str,
) -> int:
    added = 0
    z_value = float(geometry.outer[0].get("z", 0.0))
    primary_start, primary_end = (
        (geometry.y_min, geometry.y_max)
        if along_axis == "y"
        else (geometry.x_min, geometry.x_max)
    )
    edge_values = (
        (geometry.x_min, geometry.x_max)
        if along_axis == "y"
        else (geometry.y_min, geometry.y_max)
    )
    for primary in _edge_range(primary_start, primary_end, step):
        for edge_value in edge_values:
            x, y = (edge_value, primary) if along_axis == "y" else (primary, edge_value)
            added += int(
                _append_gap_fill_point(
                    kept,
                    existing,
                    ring=geometry.ring_max,
                    i_value=0.0,
                    j_value=0.0,
                    x=x,
                    y=y,
                    z=z_value,
                )
            )
    return added


def _add_midpoints_edge(
    kept: list[PositionRecord],
    existing: set[tuple[float, float]],
    geometry: PerimeterGapGeometry,
    points: list[PositionRecord],
    axis: str,
) -> int:
    if len(points) < 2:
        return 0
    added = 0
    key_func = (
        (lambda point: float(point.get("y", 0.0)))
        if axis == "x"
        else (lambda point: float(point.get("x", 0.0)))
    )
    for first, second in zip(
        sorted(points, key=key_func), sorted(points, key=key_func)[1:]
    ):
        x = (
            float(first.get("x", 0.0))
            if axis == "x"
            else 0.5 * (float(first.get("x", 0.0)) + float(second.get("x", 0.0)))
        )
        y = (
            0.5 * (float(first.get("y", 0.0)) + float(second.get("y", 0.0)))
            if axis == "x"
            else float(first.get("y", 0.0))
        )
        added += int(
            _append_gap_fill_point(
                kept,
                existing,
                ring=geometry.ring_max,
                i_value=float(first.get("i", 0.0)),
                j_value=float(first.get("j", 0.0)),
                x=x,
                y=y,
                z=float(first.get("z", 0.0)),
            )
        )
    return added


def _fill_axis_aligned_perimeter(
    kept: list[PositionRecord],
    existing: set[tuple[float, float]],
    geometry: PerimeterGapGeometry,
    edges: PerimeterGapEdges,
) -> int:
    left_y = sorted({float(p.get("y", 0.0)) for p in edges.left})
    right_y = sorted({float(p.get("y", 0.0)) for p in edges.right})
    top_x = sorted({float(p.get("x", 0.0)) for p in edges.top})
    bottom_x = sorted({float(p.get("x", 0.0)) for p in edges.bottom})
    added = _add_axis_range_points(
        kept,
        existing,
        geometry,
        step=_axis_edge_step(left_y, right_y, geometry.min_step),
        along_axis="y",
    )
    added += _add_axis_range_points(
        kept,
        existing,
        geometry,
        step=_axis_edge_step(top_x, bottom_x, geometry.min_step),
        along_axis="x",
    )
    added += _add_midpoints_edge(kept, existing, geometry, edges.left, "x")
    added += _add_midpoints_edge(kept, existing, geometry, edges.right, "x")
    added += _add_midpoints_edge(kept, existing, geometry, edges.top, "y")
    added += _add_midpoints_edge(kept, existing, geometry, edges.bottom, "y")
    return added


def _perimeter_angle(geometry: PerimeterGapGeometry, point: PositionRecord) -> float:
    return math.atan2(
        float(point.get("y", 0.0)) - geometry.center_y,
        float(point.get("x", 0.0)) - geometry.center_x,
    )


def _fill_non_axis_perimeter(
    kept: list[PositionRecord],
    existing: set[tuple[float, float]],
    geometry: PerimeterGapGeometry,
) -> int:
    outer_sorted = sorted(
        geometry.outer, key=lambda point: _perimeter_angle(geometry, point)
    )
    if len(outer_sorted) < 2:
        return 0
    added = 0
    for index, first in enumerate(outer_sorted):
        second = outer_sorted[(index + 1) % len(outer_sorted)]
        added += int(
            _append_gap_fill_point(
                kept,
                existing,
                ring=geometry.ring_max,
                i_value=float(first.get("i", 0.0)),
                j_value=float(first.get("j", 0.0)),
                x=0.5 * (float(first.get("x", 0.0)) + float(second.get("x", 0.0))),
                y=0.5 * (float(first.get("y", 0.0)) + float(second.get("y", 0.0))),
                z=float(first.get("z", 0.0)),
            )
        )
    return added


def _apply_perimeter_gap_fill(
    positions: list[PositionRecord],
) -> tuple[list[PositionRecord], JsonObject]:
    geometry = _perimeter_gap_geometry(positions)
    if geometry is None:
        return positions, {}
    edges = _perimeter_gap_edges(geometry)
    kept = _drop_perimeter_inner_ring(positions, geometry, edges)
    existing = _position_keys(kept)
    added = (
        _fill_axis_aligned_perimeter(kept, existing, geometry, edges)
        if edges.axis_aligned
        else _fill_non_axis_perimeter(kept, existing, geometry)
    )

    info: JsonObject = {
        "perim_gap_fill": True,
        "perim_gap_fill_removed_ring": int(geometry.inner_ring),
        "perim_gap_fill_added": int(added),
    }
    return kept, info


def _layout_point_key(x: float, y: float) -> tuple[float, float]:
    return (round(float(x), 6), round(float(y), 6))


def _diamond_to_axis(x: float, y: float) -> tuple[float, float]:
    return (float(x) + float(y), float(x) - float(y))


def _maybe_swap_layout_dims(
    length_m: float, width_m: float
) -> tuple[float, float, bool]:
    align = os.getenv("ALIGN_LONG_AXIS_X", "1") == "1"
    if align and width_m > length_m:
        return width_m, length_m, True
    return length_m, width_m, False


def _load_position_context(settings: SmdPositionSettings) -> SmdPositionContext:
    length_m = _env_float("LENGTH_M", _env_float("LENGTH_FT", 12.0) * 0.3048)
    width_m = _env_float("WIDTH_M", _env_float("WIDTH_FT", 12.0) * 0.3048)
    margin_m = settings.wall_margin_m
    patch_m = settings.patch_side_m
    module_half_x = 0.5 * settings.module_footprint_x_m
    module_half_y = 0.5 * settings.module_footprint_y_m
    module_half_max = max(module_half_x, module_half_y)
    layout_mode = os.environ.get("LAYOUT_MODE", settings.layout_mode).strip().lower()
    fixed_pitch = _env_float("SMD_FIXED_PITCH_M", settings.fixed_pitch_m)
    fixture_guard_m = float(settings.fixture_angle_m)
    usable_half_x = (length_m * 0.5) - margin_m - module_half_x - fixture_guard_m
    usable_half_y = (width_m * 0.5) - margin_m - module_half_y - fixture_guard_m
    if usable_half_x <= 0 or usable_half_y <= 0:
        raise SystemExit("ERROR: negative/zero usable half-span; check margin/patch/room.")
    base_short_m = 3.6576
    base_ring_n = max(1, int(settings.base_ring_n))
    base_pitch_axis = (
        (base_short_m / 2.0 - margin_m - module_half_max - fixture_guard_m)
        * 2.0
        / (2 * base_ring_n)
    )
    return SmdPositionContext(
        length_m=length_m,
        width_m=width_m,
        height_m=settings.height_m,
        margin_m=margin_m,
        patch_m=patch_m,
        module_footprint_x_m=settings.module_footprint_x_m,
        module_footprint_y_m=settings.module_footprint_y_m,
        module_half_x=module_half_x,
        module_half_y=module_half_y,
        module_half_max=module_half_max,
        layout_mode=layout_mode,
        fixed_pitch=fixed_pitch,
        fixture_guard_m=fixture_guard_m,
        usable_half_x=usable_half_x,
        usable_half_y=usable_half_y,
        base_pitch_axis=base_pitch_axis,
        base_ring_n=base_ring_n,
        ring_n=settings.ring_n,
        rings=settings.rings,
        mount_z_m=settings.mount_z_m,
        perimeter_gap_fill=settings.perimeter_gap_fill,
    )


def _add_fixed_pitch_meta(meta: JsonObject, fixed_pitch: float) -> None:
    if fixed_pitch > 0:
        meta["fixed_pitch_m"] = float(fixed_pitch)


def _apply_optional_gap_fill(
    context: SmdPositionContext, positions: list[PositionRecord], meta: JsonObject
) -> list[PositionRecord]:
    if not context.perimeter_gap_fill:
        return positions
    filled, info = _apply_perimeter_gap_fill(positions)
    meta.update(info)
    return filled


def _grid_positions_from_context(
    context: SmdPositionContext,
) -> tuple[list[PositionRecord], float, JsonObject]:
    pitch_x = (
        float(context.fixed_pitch)
        if context.fixed_pitch > 0
        else float(context.base_pitch_axis)
    )
    pitch_y = pitch_x
    pitch_x = min(pitch_x, max(context.usable_half_x, 1e-9))
    pitch_y = min(pitch_y, max(context.usable_half_y, 1e-9))
    nx = max(0, int(math.floor(context.usable_half_x / max(pitch_x, 1e-9))))
    ny = max(0, int(math.floor(context.usable_half_y / max(pitch_y, 1e-9))))
    positions, ring_max = _grid_position_records(context, nx, ny, pitch_x, pitch_y)
    spacing_eff = min(pitch_x, pitch_y)
    meta: JsonObject = {
        "room_L_m": context.length_m,
        "room_W_m": context.width_m,
        "margin_m": context.margin_m,
        "patch_side_m": context.patch_m,
        "ring_n": int(ring_max),
        "rings": int(ring_max + 1),
        "spacing_m": float(spacing_eff),
        "layout_mode": context.layout_mode,
        "pitch_x_m": float(pitch_x),
        "pitch_y_m": float(pitch_y),
        "pitch_m": float(spacing_eff),
    }
    _add_fixed_pitch_meta(meta, context.fixed_pitch)
    return _apply_optional_gap_fill(context, positions, meta), spacing_eff, meta


def _grid_position_records(
    context: SmdPositionContext, nx: int, ny: int, pitch_x: float, pitch_y: float
) -> tuple[list[PositionRecord], int]:
    positions: list[PositionRecord] = []
    ring_max = 0
    for ix in range(-nx, nx + 1):
        for iy in range(-ny, ny + 1):
            ring = max(abs(ix), abs(iy))
            ring_max = max(ring_max, ring)
            positions.append(
                {
                    "ring": int(ring),
                    "i": int(ix),
                    "j": int(iy),
                    "x": round(float(ix) * pitch_x, 6),
                    "y": round(float(iy) * pitch_y, 6),
                    "z": context.mount_z_m,
                }
            )
    return positions, ring_max


def _square_axis_pitch(context: SmdPositionContext) -> tuple[int, float]:
    half_min = min(context.usable_half_x, context.usable_half_y)
    if context.fixed_pitch > 0:
        d_axis = min(float(context.fixed_pitch), half_min)
        return max(1, int(math.floor(half_min / max(d_axis, 1e-9)))), d_axis
    ring_n = max(
        1,
        int(math.floor((half_min / max(context.base_pitch_axis, 1e-9)) + 0.5)),
    )
    return ring_n, half_min / ring_n


def _square_positions_from_context(
    context: SmdPositionContext,
) -> tuple[list[PositionRecord], float, JsonObject]:
    ring_n, d_axis = _square_axis_pitch(context)
    rings_local = ring_n + 1
    positions: list[PositionRecord] = []
    for ring in range(rings_local):
        for i, j in _ring_ij(ring):
            x, y = _ij_to_xy(i, j, d_axis * SQRT2)
            positions.append(
                {
                    "ring": ring,
                    "i": i,
                    "j": j,
                    "x": round(x, 6),
                    "y": round(y, 6),
                    "z": context.mount_z_m,
                }
            )
    meta: JsonObject = {
        "room_L_m": context.length_m,
        "room_W_m": context.width_m,
        "margin_m": context.margin_m,
        "patch_side_m": context.patch_m,
        "ring_n": ring_n,
        "rings": rings_local,
        "spacing_m": d_axis,
        "d_axis_m": d_axis,
        "layout_mode": context.layout_mode,
        "pitch_x_m": d_axis,
        "pitch_y_m": d_axis,
        "pitch_m": d_axis,
    }
    _add_fixed_pitch_meta(meta, context.fixed_pitch)
    return _apply_optional_gap_fill(context, positions, meta), d_axis, meta


def _rect_positions_from_context(
    context: SmdPositionContext,
) -> tuple[list[PositionRecord], float, JsonObject]:
    positions, meta_rect = build_rect_grid(
        length_m=context.length_m,
        width_m=context.width_m,
        height_m=context.height_m,
        wall_margin_m=context.margin_m,
        module_side_x_m=context.module_footprint_x_m,
        module_side_y_m=context.module_footprint_y_m,
        mount_z_m=context.mount_z_m,
    )
    pitch_x = float(meta_rect.get("pitch_x_m", 0.0))
    pitch_y = float(meta_rect.get("pitch_y_m", 0.0))
    pitch = (
        float(meta_rect.get("pitch_m", 0.0))
        if "pitch_m" in meta_rect
        else min(pitch_x, pitch_y)
    )
    spacing_eff = pitch if pitch > 0 else 0.0
    rings_count = int(meta_rect.get("rings_count", context.rings))
    meta: JsonObject = {
        "room_L_m": context.length_m,
        "room_W_m": context.width_m,
        "room_H_m": context.height_m,
        "margin_m": context.margin_m,
        "patch_side_m": context.patch_m,
        "ring_n": rings_count - 1,
        "rings": rings_count,
        "spacing_m": spacing_eff,
        "layout_mode": context.layout_mode,
        "pitch_x_m": pitch_x,
        "pitch_y_m": pitch_y,
        "pitch_m": pitch,
        "swapped_axes": bool(meta_rect.get("swapped_axes", False)),
    }
    meta.update({key: value for key, value in meta_rect.items() if key not in meta})
    return _apply_optional_gap_fill(context, positions, meta), spacing_eff, meta


def _load_exact_layout(
    context: SmdPositionContext,
) -> tuple[JsonObject, bool, float, float]:
    room_length_m, room_width_m, swapped_axes = _maybe_swap_layout_dims(
        context.length_m, context.width_m
    )
    base_n_value = int(os.environ.get("SMD_BASE_RING_N", "0") or "0")
    base_n_override: int | None = base_n_value if base_n_value > 0 else None
    layout = _generate_layout_with_zones(
        room_length_m / 0.3048 if room_length_m > 0 else 0.0,
        room_width_m / 0.3048 if room_width_m > 0 else 0.0,
        base_n_override=base_n_override,
    )
    return layout, swapped_axes, room_length_m, room_width_m


def _exact_axis_points(
    all_points: list[tuple[float, float]],
) -> tuple[list[tuple[float, float]], float, float]:
    axis_points = [_diamond_to_axis(x, y) for x, y in all_points]
    xs = [point[0] for point in axis_points]
    ys = [point[1] for point in axis_points]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    if span_x <= 0 or span_y <= 0:
        raise SystemExit("ERROR: invalid exact_tiled layout span.")
    return axis_points, span_x, span_y


def _exact_pitch(
    context: SmdPositionContext,
    room_length_m: float,
    room_width_m: float,
    span_x: float,
    span_y: float,
) -> tuple[float, float]:
    dim_x_fit = 2.0 * (
        (room_length_m * 0.5)
        - context.margin_m
        - context.module_half_x
        - context.fixture_guard_m
    )
    dim_y_fit = 2.0 * (
        (room_width_m * 0.5)
        - context.margin_m
        - context.module_half_y
        - context.fixture_guard_m
    )
    pitch_x = dim_x_fit / span_x
    pitch_y = dim_y_fit / span_y
    if context.fixed_pitch > 0:
        pitch_x = min(float(context.fixed_pitch), pitch_x)
        pitch_y = min(float(context.fixed_pitch), pitch_y)
    if pitch_x <= 0 or pitch_y <= 0:
        raise SystemExit("ERROR: invalid exact_tiled layout pitch.")
    return pitch_x, pitch_y


def _exact_zone_map(zone_groups: list[JsonObject]) -> dict[tuple[float, float], int]:
    zone_map: dict[tuple[float, float], int] = {}
    for zone in zone_groups:
        zone_idx = int(zone.get("zone", 0))
        for x, y in zone.get("points", []):
            zone_map[_layout_point_key(x, y)] = zone_idx
    return zone_map


def _exact_position_records(
    context: SmdPositionContext,
    all_points: list[tuple[float, float]],
    zone_map: dict[tuple[float, float], int],
    axis_points: list[tuple[float, float]],
    pitch_x_m: float,
    pitch_y_m: float,
) -> list[PositionRecord]:
    xs = [point[0] for point in axis_points]
    ys = [point[1] for point in axis_points]
    x_center = 0.5 * (min(xs) + max(xs))
    y_center = 0.5 * (min(ys) + max(ys))
    records: list[PositionRecord] = []
    for raw_x, raw_y in all_points:
        ax, ay = _diamond_to_axis(raw_x, raw_y)
        records.append(
            {
                "ring": int(zone_map.get(_layout_point_key(raw_x, raw_y), 0)),
                "i": float(raw_x),
                "j": float(raw_y),
                "x": round((ax - x_center) * pitch_x_m, 6),
                "y": round((ay - y_center) * pitch_y_m, 6),
                "z": context.mount_z_m,
            }
        )
    return records


def _exact_meta(
    context: SmdPositionContext,
    topology: JsonObject,
    room_length_m: float,
    room_width_m: float,
    swapped_axes: bool,
    zone_count: int,
    pitch_x_m: float,
    pitch_y_m: float,
    span_x: float,
    span_y: float,
) -> JsonObject:
    spacing = float(min(pitch_x_m, pitch_y_m))
    meta: JsonObject = {
        "room_L_m": room_length_m,
        "room_W_m": room_width_m,
        "room_H_m": context.height_m,
        "margin_m": context.margin_m,
        "patch_side_m": context.patch_m,
        "ring_n": max(0, zone_count - 1),
        "rings": zone_count,
        "spacing_m": spacing,
        "layout_mode": "exact_tiled",
        "pitch_x_m": float(pitch_x_m),
        "pitch_y_m": float(pitch_y_m),
        "pitch_m": spacing,
        "layout_family": "horticultural_tiled_v1",
        "base_n": int(topology.get("base_n", 0) or 0),
        "square_tile_count": int(topology.get("square_tile_count", 0) or 0),
        "connector_count": int(topology.get("connector_count", 0) or 0),
        "has_rect_extension": bool(topology.get("has_rect_extension", False)),
        "rect_long_ft": float(topology.get("rect_long_ft", 0.0) or 0.0),
        "rect_offset": int(topology.get("rect_offset", 0) or 0),
        "span_x_units": float(span_x),
        "span_y_units": float(span_y),
        "swapped_axes": bool(swapped_axes),
    }
    _add_fixed_pitch_meta(meta, context.fixed_pitch)
    return meta


def _exact_positions_from_context(
    context: SmdPositionContext,
) -> tuple[list[PositionRecord], float, JsonObject]:
    layout, swapped_axes, room_length_m, room_width_m = _load_exact_layout(context)
    all_points = cast(list[tuple[float, float]], layout.get("all_positions") or [])
    zone_groups = cast(list[JsonObject], layout.get("zone_groups") or [])
    topology = cast(JsonObject, layout.get("topology") or {})
    if not all_points or not zone_groups:
        raise SystemExit("ERROR: exact_tiled layout generation returned no positions.")
    axis_points, span_x, span_y = _exact_axis_points(all_points)
    pitch_x_m, pitch_y_m = _exact_pitch(
        context, room_length_m, room_width_m, span_x, span_y
    )
    rings_local = int(topology.get("zone_count", len(zone_groups)) or len(zone_groups))
    meta = _exact_meta(
        context,
        topology,
        room_length_m,
        room_width_m,
        swapped_axes,
        rings_local,
        pitch_x_m,
        pitch_y_m,
        span_x,
        span_y,
    )
    positions = _exact_position_records(
        context,
        all_points,
        _exact_zone_map(zone_groups),
        axis_points,
        pitch_x_m,
        pitch_y_m,
    )
    return (
        _apply_optional_gap_fill(context, positions, meta),
        float(min(pitch_x_m, pitch_y_m)),
        meta,
    )


def _rect_control_ring_zero() -> list[tuple[int, int]]:
    return [(index, 0) for index in range(-3, 4)]


def _rect_control_ring_one() -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    for index in range(-3, 4):
        points.append((index, 1))
        points.append((index, -1))
    points.append((-4, 0))
    points.append((4, 0))
    if len(points) != 16:
        raise SystemExit(f"rect_rect ring 1 got {len(points)} != 16")
    return points


def _rect_control_ring_border(ring: int) -> list[tuple[int, int]]:
    half_x = 3 + ring
    half_y = 1 + ring
    border: list[tuple[int, int]] = []
    for index in range(-half_x, half_x + 1):
        border.append((index, -half_y))
        border.append((index, half_y))
    for index in range(-half_y + 1, half_y):
        border.append((-half_x, index))
        border.append((half_x, index))
    seen: set[tuple[int, int]] = set()
    unique: list[tuple[int, int]] = []
    for point in border:
        if point not in seen:
            seen.add(point)
            unique.append(point)
    return unique


def _sample_rect_control_ring(
    ring: int, unique: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    needed = RECT_RECT_RING_TARGET_COUNTS[ring]
    if needed > len(unique):
        raise SystemExit(
            f"L={ring}: need {needed} border points, only have {len(unique)}"
        )
    step = max(1, len(unique) // needed)
    points = unique[0 : len(unique) : step]
    if len(points) > needed:
        points = points[:needed]
    index = 0
    while len(points) < needed and index < len(unique):
        candidate = unique[len(points) % len(unique)]
        if candidate not in points:
            points.append(candidate)
        index += 1
    if len(points) != needed:
        raise SystemExit(f"ring {ring} got {len(points)} != {needed}")
    return points


def _rect_control_ring_indices(ring: int) -> list[tuple[int, int]]:
    if ring == 0:
        return _rect_control_ring_zero()
    if ring == 1:
        return _rect_control_ring_one()
    if ring >= len(RECT_RECT_RING_TARGET_COUNTS):
        raise SystemExit(f"rect_rect ring L={ring} beyond target-count table")
    return _sample_rect_control_ring(ring, _rect_control_ring_border(ring))


def _fallback_positions_for_context(
    context: SmdPositionContext,
) -> tuple[list[PositionRecord], float, JsonObject]:
    spacing = _solve_spacing(
        context.rings,
        context.length_m,
        context.width_m,
        context.margin_m,
        context.module_half_x,
        context.module_half_y,
    )
    if context.layout_mode not in {"square", "rect_rect"}:
        raise SystemExit(f"Unknown LAYOUT_MODE={context.layout_mode!r}")
    positions: list[PositionRecord] = []
    for ring in range(context.rings):
        indices = (
            _ring_ij(ring)
            if context.layout_mode == "square"
            else _rect_control_ring_indices(ring)
        )
        for i, j in indices:
            x, y = _ij_to_xy(i, j, spacing)
            positions.append(
                {
                    "ring": ring,
                    "i": i,
                    "j": j,
                    "x": round(x, 6),
                    "y": round(y, 6),
                    "z": context.mount_z_m,
                }
            )
    meta: JsonObject = {
        "room_L_m": context.length_m,
        "room_W_m": context.width_m,
        "margin_m": context.margin_m,
        "patch_side_m": context.patch_m,
        "ring_n": context.ring_n,
        "rings": context.rings,
        "spacing_m": spacing,
        "d_axis_m": SQRT2 * spacing,
        "layout_mode": context.layout_mode,
    }
    return positions, spacing, meta


def compute_positions_from_env(
    settings: SmdPositionSettings,
) -> tuple[list[PositionRecord], float, JsonObject]:
    context = _load_position_context(settings)
    if context.layout_mode in {"grid", "uniform", "matrix"}:
        return _grid_positions_from_context(context)
    if context.layout_mode == "square":
        return _square_positions_from_context(context)
    if context.layout_mode == "rect_rect":
        return _rect_positions_from_context(context)
    if context.layout_mode in {"exact_tiled", "tiled", "exact", "modular"}:
        return _exact_positions_from_context(context)
    return _fallback_positions_for_context(context)


def get_module_positions(
    settings: SmdPositionSettings,
) -> tuple[list[PositionRecord], float]:
    positions, spacing, _meta = compute_positions_from_env(settings)
    return positions, spacing
