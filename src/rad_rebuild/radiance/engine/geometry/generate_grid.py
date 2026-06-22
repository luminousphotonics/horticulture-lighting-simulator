#!/usr/bin/env python3
"""
generate_grid.py

Build a regular sensor grid that matches the current room footprint.

Environment variables:

  LENGTH_M / LENGTH_FT, WIDTH_M / WIDTH_FT  – should match generate_room.py
  GRID_Z             – sensor height above floor (m)
  RESOLUTION_X       – fixed number of points along X (legacy / explicit override)
  RESOLUTION_Y       – fixed number of points along Y (legacy / explicit override)
  GRID_TARGET_SPACING_M – target physical sample spacing for adaptive grids (m)
  GRID_MIN_POINTS_X / GRID_MIN_POINTS_Y – minimum points per axis for adaptive grids
  GRID_MIN_FLOOR_POINTS_X / GRID_MIN_FLOOR_POINTS_Y – enforced minimum floor after adaptive spacing
  GRID_MAX_POINTS_X / GRID_MAX_POINTS_Y – optional maximum points per axis (0 = uncapped)
  GRID_SAMPLE_LAYOUT – "centered" (default) or "edge_aligned"

Default behavior uses an adaptive cell-centered grid with approximately
constant physical spacing across room sizes, then enforces a minimum point
floor so very small layouts do not collapse to unusably coarse heatmaps.
"""

import os
import sys
from collections.abc import Mapping
from typing import Literal, TypedDict


FT_TO_M = 0.3048
GridEnv = Mapping[str, object]


class GridSpec(TypedDict):
    length_m: float
    width_m: float
    grid_z_m: float
    sample_layout: str
    target_spacing_m: float
    min_points_x: int
    min_points_y: int
    min_floor_points_x: int
    min_floor_points_y: int
    max_points_x: int
    max_points_y: int
    wall_margin_m: float
    grid_module_side_m: float
    interior_length_m: float
    interior_width_m: float
    base_resolution_x: int
    base_resolution_y: int
    resolution_x: int
    resolution_y: int
    explicit_resolution: bool
    minimum_floor_enforced_x: bool
    minimum_floor_enforced_y: bool
    x_coords: list[float]
    y_coords: list[float]
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    n_points: int


FloatGridSpecKey = Literal[
    "length_m",
    "width_m",
    "grid_z_m",
    "target_spacing_m",
    "wall_margin_m",
    "grid_module_side_m",
    "interior_length_m",
    "interior_width_m",
    "x_min",
    "x_max",
    "y_min",
    "y_max",
]
CoordGridSpecKey = Literal["x_coords", "y_coords"]


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


def _f_from(source: GridEnv | None, name: str, default: float) -> float:
    env = source if source is not None else os.environ
    try:
        return float(str(env.get(name, default)).strip() or default)
    except Exception:
        return default


def _maybe_swap_dims(L: float, W: float) -> tuple[float, float]:
    align = os.getenv("ALIGN_LONG_AXIS_X", "1") == "1"
    if align and W > L:
        return W, L
    return L, W


def _maybe_swap_dims_from(
    source: GridEnv | None, L: float, W: float
) -> tuple[float, float]:
    env = source if source is not None else os.environ
    align = str(env.get("ALIGN_LONG_AXIS_X", "1")).strip() == "1"
    if align and W > L:
        return W, L
    return L, W


def _linspace(start: float, stop: float, count: int) -> list[float]:
    if count <= 1:
        return [float(start)]
    step = (stop - start) / float(count - 1)
    return [start + step * i for i in range(count)]


def _axis_centers(half_span: float, count: int) -> list[float]:
    if count <= 1:
        return [0.0]
    full_span = 2.0 * float(half_span)
    cell = full_span / float(count)
    start = -float(half_span) + 0.5 * cell
    return [start + cell * i for i in range(count)]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


def _env_int_from(source: GridEnv | None, name: str, default: int) -> int:
    env = source if source is not None else os.environ
    try:
        return int(float(str(env.get(name, str(default))).strip() or default))
    except Exception:
        return default


def _adaptive_count(
    span_m: float, target_spacing_m: float, min_points: int, max_points: int
) -> int:
    if span_m <= 0:
        return max(1, min_points)
    target = max(target_spacing_m, 1e-6)
    count = max(min_points, int(round(span_m / target)))
    if max_points > 0:
        count = min(count, max_points)
    return max(1, count)


def _grid_spec_float(spec: GridSpec, name: FloatGridSpecKey) -> float:
    return float(spec[name])


def _grid_spec_coords(spec: GridSpec, name: CoordGridSpecKey) -> list[float]:
    return spec[name]


def build_grid_spec(env: GridEnv | None = None) -> GridSpec:
    source = env if env is not None else os.environ

    length_m = _f_from(source, "LENGTH_M", _f_from(source, "LENGTH_FT", 12.0) * FT_TO_M)
    width_m = _f_from(source, "WIDTH_M", _f_from(source, "WIDTH_FT", 12.0) * FT_TO_M)
    length_m, width_m = _maybe_swap_dims_from(source, length_m, width_m)

    grid_z = _f_from(source, "GRID_Z", 0.005)
    resolution_x_raw = str(source.get("RESOLUTION_X", "")).strip()
    resolution_y_raw = str(source.get("RESOLUTION_Y", "")).strip()
    target_spacing_m = _f_from(source, "GRID_TARGET_SPACING_M", 0.25)
    min_points_x = _env_int_from(source, "GRID_MIN_POINTS_X", 5)
    min_points_y = _env_int_from(source, "GRID_MIN_POINTS_Y", 5)
    min_floor_points_x = _env_int_from(source, "GRID_MIN_FLOOR_POINTS_X", 21)
    min_floor_points_y = _env_int_from(source, "GRID_MIN_FLOOR_POINTS_Y", 21)
    max_points_x = _env_int_from(source, "GRID_MAX_POINTS_X", 0)
    max_points_y = _env_int_from(source, "GRID_MAX_POINTS_Y", 0)
    sample_layout = (
        str(source.get("GRID_SAMPLE_LAYOUT", "centered")).strip().lower() or "centered"
    )

    # Use the same interior notion as emitter layout: subtract wall margin + half module.
    # Keep a small inset so sensors are not inside the wall surfaces.
    wall_margin_m = _f_from(
        source, "WALL_MARGIN_M", _f_from(source, "GRID_WALL_MARGIN_M", 0.005)
    )
    grid_module_side_m = _f_from(
        source, "GRID_MODULE_SIDE_M", _f_from(source, "MODULE_SIDE_M", 0.0)
    )
    shrink = 2.0 * (wall_margin_m + grid_module_side_m / 2.0)
    length_int_m = max(0.1, length_m - shrink)
    width_int_m = max(0.1, width_m - shrink)

    half_x = length_int_m / 2.0
    half_y = width_int_m / 2.0

    explicit_resolution = bool(resolution_x_raw or resolution_y_raw)
    x_coords: list[float]
    y_coords: list[float]
    if explicit_resolution:
        resolution_x = max(1, _env_int_from(source, "RESOLUTION_X", 15))
        resolution_y = max(1, _env_int_from(source, "RESOLUTION_Y", 15))
        if sample_layout == "edge_aligned":
            x_coords = _linspace(-half_x, half_x, resolution_x)
            y_coords = _linspace(-half_y, half_y, resolution_y)
        else:
            x_coords = _axis_centers(half_x, resolution_x)
            y_coords = _axis_centers(half_y, resolution_y)
    else:
        base_resolution_x = _adaptive_count(
            length_int_m, target_spacing_m, min_points_x, max_points_x
        )
        base_resolution_y = _adaptive_count(
            width_int_m, target_spacing_m, min_points_y, max_points_y
        )
        resolution_x = max(base_resolution_x, min_floor_points_x)
        resolution_y = max(base_resolution_y, min_floor_points_y)
        if max_points_x > 0:
            resolution_x = min(resolution_x, max_points_x)
        if max_points_y > 0:
            resolution_y = min(resolution_y, max_points_y)
        x_coords = _axis_centers(half_x, resolution_x)
        y_coords = _axis_centers(half_y, resolution_y)
    if explicit_resolution:
        base_resolution_x = resolution_x
        base_resolution_y = resolution_y

    return {
        "length_m": float(length_m),
        "width_m": float(width_m),
        "grid_z_m": float(grid_z),
        "sample_layout": sample_layout,
        "target_spacing_m": float(target_spacing_m),
        "min_points_x": int(min_points_x),
        "min_points_y": int(min_points_y),
        "min_floor_points_x": int(min_floor_points_x),
        "min_floor_points_y": int(min_floor_points_y),
        "max_points_x": int(max_points_x),
        "max_points_y": int(max_points_y),
        "wall_margin_m": float(wall_margin_m),
        "grid_module_side_m": float(grid_module_side_m),
        "interior_length_m": float(length_int_m),
        "interior_width_m": float(width_int_m),
        "base_resolution_x": int(base_resolution_x),
        "base_resolution_y": int(base_resolution_y),
        "resolution_x": int(resolution_x),
        "resolution_y": int(resolution_y),
        "explicit_resolution": bool(explicit_resolution),
        "minimum_floor_enforced_x": bool(
            (not explicit_resolution) and resolution_x > base_resolution_x
        ),
        "minimum_floor_enforced_y": bool(
            (not explicit_resolution) and resolution_y > base_resolution_y
        ),
        "x_coords": x_coords,
        "y_coords": y_coords,
        "x_min": float(min(x_coords) if x_coords else 0.0),
        "x_max": float(max(x_coords) if x_coords else 0.0),
        "y_min": float(min(y_coords) if y_coords else 0.0),
        "y_max": float(max(y_coords) if y_coords else 0.0),
        "n_points": int(len(x_coords) * len(y_coords)),
    }


def print_coords_mode(spec: GridSpec | None = None) -> None:
    spec = spec or build_grid_spec()
    x_coords = _grid_spec_coords(spec, "x_coords")
    y_coords = _grid_spec_coords(spec, "y_coords")
    grid_z = _grid_spec_float(spec, "grid_z_m")
    print("x y z")
    for y in y_coords:
        for x in x_coords:
            print(f"{x:.6f} {y:.6f} {grid_z:.6f}")


def print_rtrace_mode(spec: GridSpec | None = None) -> None:
    spec = spec or build_grid_spec()
    x_coords = _grid_spec_coords(spec, "x_coords")
    y_coords = _grid_spec_coords(spec, "y_coords")
    grid_z = _grid_spec_float(spec, "grid_z_m")
    # rtrace wants: x y z dx dy dz
    for y in y_coords:
        for x in x_coords:
            print(f"{x:.6f} {y:.6f} {grid_z:.6f} 0.000000 0.000000 1.000000")


def usage_and_exit() -> None:
    print("Usage: generate_grid.py [coords|rtrace]")
    sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        usage_and_exit()
    mode = sys.argv[1].lower()
    spec = build_grid_spec()
    if mode == "coords":
        print_coords_mode(spec)
    elif mode == "rtrace":
        print_rtrace_mode(spec)
    else:
        usage_and_exit()
