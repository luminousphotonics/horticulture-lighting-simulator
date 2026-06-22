#!/usr/bin/env python3
# rect_layout.py
#
# Exact 203-emitter rectangular layout for SMD modules, axis-aligned:
#   n = 7, offset = 6
#   - ring 0: horizontal spine on v = 0, u = -6,-4,-2,0,2,4,6
#   - rings 1..7: rectangular perimeters in (u,v) with (u+v) even
#
# Mapping:
#   x = u * pitch_x
#   y = v * pitch_y
#
# where pitch_x and pitch_y are chosen so the full pattern just fits inside
# the usable interior of the current room (LENGTH_M x WIDTH_M), so for
# 12' x 24' you get a proper 2:1 rectangle, axis-aligned, not rotated.

from __future__ import annotations

from dataclasses import dataclass
import math
import os

FT_TO_M = 0.3048
UvPoint = tuple[int, int, int]
RectPosition = dict[str, float | int]
RectMetadata = dict[str, float | int | str | bool]


@dataclass(frozen=True)
class RectRoomSpec:
    """Validated rectangular layout room inputs in meters."""

    length_m: float
    width_m: float
    height_m: float
    wall_margin_m: float
    module_side_x_m: float
    module_side_y_m: float
    mount_z_m: float
    swapped_axes: bool


@dataclass(frozen=True)
class RectUvLayout:
    """UV pattern selection before physical coordinate scaling."""

    points: list[UvPoint]
    ring_n: int
    offset: int
    fixed_pitch_x_m: float
    fixed_pitch_y_m: float


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _room_dims() -> tuple[float, float, float]:
    """
    Resolve room dimensions from env, preferring the ft-based inputs used by
    generate_emitters_smd.py. Falls back to explicit *_M values.
    Returns (length_x_m, width_y_m, height_m).
    """
    len_ft = _float_env("LENGTH_FT", 0.0)
    wid_ft = _float_env("WIDTH_FT", 0.0)
    len_m_env = _float_env("LENGTH_M", 0.0)
    wid_m_env = _float_env("WIDTH_M", 0.0)

    length_m = len_m_env if len_m_env > 0 else len_ft * FT_TO_M
    width_m = wid_m_env if wid_m_env > 0 else wid_ft * FT_TO_M

    if length_m <= 0:
        length_m = 3.6576  # 12 ft default
    if width_m <= 0:
        width_m = 3.6576  # square fallback

    height_m = _float_env("HEIGHT_M", 3.048)  # default 10 ft
    return length_m, width_m, height_m


def _maybe_swap_dims(length_m: float, width_m: float) -> tuple[float, float, bool]:
    """
    Optionally swap dimensions so the long axis lies along +X for rectangular
    layouts. Controlled by ALIGN_LONG_AXIS_X (default: "1" to align).
    """
    align = os.getenv("ALIGN_LONG_AXIS_X", "1") == "1"
    if align and width_m > length_m:
        return width_m, length_m, True
    return length_m, width_m, False


# ──────────────────────────────────────────────────────────────────────────────
# 1) UV pattern helpers: spine + rectangular rings, parameterized by ring_n, offset
# ──────────────────────────────────────────────────────────────────────────────


def _generate_rect_uv(ring_n: int, offset: int) -> list[UvPoint]:
    """
    Generate (u, v, ring) for a rectangular layout with:
      - ring 0: horizontal spine from u=-offset..+offset (step 2), v=0
      - rings 1..ring_n: rectangular perimeters with u_max = offset + k, v_max = k
    """
    pts: list[UvPoint] = []

    # Central horizontal spine: ring 0
    for u in range(-offset, offset + 1, 2):
        pts.append((u, 0, 0))

    # Rectangular rings: ring = k, k = 1..ring_n
    for k in range(1, ring_n + 1):
        u_max = offset + k
        v_max = k
        for u in range(-u_max, u_max + 1):
            for v in range(-v_max, v_max + 1):
                if not (abs(u) == u_max or abs(v) == v_max):
                    continue
                if (u + v) % 2 != 0:
                    continue
                pts.append((u, v, k))

    dedup: dict[tuple[int, int], int] = {}
    for u, v, ring in pts:
        key = (u, v)
        if key not in dedup or ring < dedup[key]:
            dedup[key] = ring

    return [(u, v, ring) for (u, v), ring in dedup.items()]


# Legacy alias: generate the 233-emitter layout (ring_n=7, offset=8)
def _generate_uv_203() -> list[UvPoint]:
    return _generate_rect_uv(ring_n=7, offset=8)


def _generate_square_uv(ring_n: int) -> list[UvPoint]:
    """
    Axis-aligned diamond rings (no rotation in physical space):
      ring 0: single center (0,0)
      ring k>=1: perimeter of |u|+|v| = k (no parity thinning), 4k modules.
    """
    pts: list[UvPoint] = [(0, 0, 0)]
    for k in range(1, ring_n + 1):
        # traverse diamond perimeter
        for i in range(k + 1):
            pts.append((k - i, i, k))
            pts.append((-i, k - i, k))
            pts.append((-k + i, -i, k))
            pts.append((i, -k + i, k))
    dedup: dict[tuple[int, int], int] = {}
    for u, v, ring in pts:
        key = (u, v)
        if key not in dedup or ring < dedup[key]:
            dedup[key] = ring
    return [(u, v, ring) for (u, v), ring in dedup.items()]


def _resolve_room_spec(
    *,
    length_m: float | None,
    width_m: float | None,
    height_m: float | None,
    wall_margin_m: float | None,
    module_side_x_m: float | None,
    module_side_y_m: float | None,
    mount_z_m: float | None,
) -> RectRoomSpec:
    len_m_env, wid_m_env, h_m_env = _room_dims()
    resolved_length_m = len_m_env if length_m is None else length_m
    resolved_width_m = wid_m_env if width_m is None else width_m
    resolved_length_m, resolved_width_m, swapped = _maybe_swap_dims(
        resolved_length_m, resolved_width_m
    )
    resolved_height_m = h_m_env if height_m is None else height_m
    resolved_wall_margin_m = (
        _float_env("WALL_MARGIN_M", 0.127) if wall_margin_m is None else wall_margin_m
    )
    resolved_module_side_x_m = (
        _float_env("MODULE_FOOTPRINT_X_M", 0.1524)
        if module_side_x_m is None
        else module_side_x_m
    )
    resolved_module_side_y_m = (
        _float_env("MODULE_FOOTPRINT_Y_M", 0.1650)
        if module_side_y_m is None
        else module_side_y_m
    )
    resolved_mount_z_m = (
        _float_env("MOUNT_Z_M", resolved_height_m - 0.15875)
        if mount_z_m is None
        else mount_z_m
    )
    return RectRoomSpec(
        length_m=resolved_length_m,
        width_m=resolved_width_m,
        height_m=resolved_height_m,
        wall_margin_m=resolved_wall_margin_m,
        module_side_x_m=resolved_module_side_x_m,
        module_side_y_m=resolved_module_side_y_m,
        mount_z_m=resolved_mount_z_m,
        swapped_axes=swapped,
    )


def _interior_spans(spec: RectRoomSpec) -> tuple[float, float]:
    fixture_leg_m = _float_env("SMD_FIXTURE_ANGLE_IN", 0.25) * 0.0254
    half_x_int = (
        (spec.length_m / 2.0)
        - spec.wall_margin_m
        - spec.module_side_x_m / 2.0
        - fixture_leg_m
    )
    half_y_int = (
        (spec.width_m / 2.0)
        - spec.wall_margin_m
        - spec.module_side_y_m / 2.0
        - fixture_leg_m
    )
    if half_x_int <= 0 or half_y_int <= 0:
        raise RuntimeError(
            "Rect layout: non-positive interior region; "
            "check LENGTH_M, WIDTH_M, WALL_MARGIN_M, MODULE_SIDE_M."
        )
    return 2.0 * half_x_int, 2.0 * half_y_int


def _base_pitch_parameters(spec: RectRoomSpec) -> tuple[float, int, float]:
    fixture_leg_m = _float_env("SMD_FIXTURE_ANGLE_IN", 0.25) * 0.0254
    base_short_m = 3.6576
    base_half_short = (
        (base_short_m / 2.0)
        - spec.wall_margin_m
        - spec.module_side_y_m / 2.0
        - fixture_leg_m
    )
    base_ring_n = int(_float_env("SMD_BASE_RING_N", 7) or 7)
    if base_ring_n < 1:
        base_ring_n = 1
    base_span_v = float(2 * base_ring_n)
    base_pitch = (2.0 * base_half_short) / base_span_v
    base_long_m = 7.3152
    base_half_long = (
        (base_long_m / 2.0)
        - spec.wall_margin_m
        - spec.module_side_x_m / 2.0
        - fixture_leg_m
    )
    base_span_long_m = 2.0 * base_half_long
    base_offset = int(_float_env("SMD_BASE_OFFSET", base_ring_n + 1))
    return base_pitch, base_offset, base_span_long_m


def _uv_from_fixed_pitch(
    *,
    fixed_pitch: float,
    dim_x_int: float,
    dim_y_int: float,
    squareish: bool,
) -> RectUvLayout:
    pitch_x = float(fixed_pitch)
    pitch_y = float(fixed_pitch)
    max_u = int(math.floor(dim_x_int / (2.0 * pitch_x)))
    max_v = int(math.floor(dim_y_int / (2.0 * pitch_y)))
    if max_u < 1 or max_v < 1:
        pitch_x = min(pitch_x, dim_x_int / 2.0)
        pitch_y = min(pitch_y, dim_y_int / 2.0)
        max_u = max(1, int(math.floor(dim_x_int / (2.0 * pitch_x))))
        max_v = max(1, int(math.floor(dim_y_int / (2.0 * pitch_y))))
    ring_n = max(1, min(max_u, max_v))
    if squareish:
        return RectUvLayout(
            points=_generate_square_uv(ring_n=ring_n),
            ring_n=ring_n,
            offset=0,
            fixed_pitch_x_m=pitch_x,
            fixed_pitch_y_m=pitch_y,
        )
    offset = max(0, max_u - ring_n)
    if offset % 2 != 0:
        offset = max(0, offset - 1)
    return RectUvLayout(
        points=_generate_rect_uv(ring_n=ring_n, offset=offset),
        ring_n=ring_n,
        offset=offset,
        fixed_pitch_x_m=pitch_x,
        fixed_pitch_y_m=pitch_y,
    )


def _uv_from_scaled_pitch(
    *,
    dim_x_int: float,
    dim_y_int: float,
    squareish: bool,
    base_pitch: float,
    base_offset: int,
    base_span_long_m: float,
) -> RectUvLayout:
    if squareish:
        ring_n = max(1, int(math.floor((dim_y_int / (2.0 * base_pitch)) + 0.5)))
        return RectUvLayout(
            points=_generate_square_uv(ring_n=ring_n),
            ring_n=ring_n,
            offset=0,
            fixed_pitch_x_m=0.0,
            fixed_pitch_y_m=0.0,
        )
    ring_n = max(1, int(math.floor(dim_y_int / (2.0 * base_pitch))))
    required_u = math.ceil(dim_x_int / (2.0 * base_pitch))
    offset_scaled = int(round(base_offset * (dim_x_int / base_span_long_m)))
    offset = max(ring_n, offset_scaled, int(required_u - ring_n))
    if offset % 2 != 0:
        offset += 1
    return RectUvLayout(
        points=_generate_rect_uv(ring_n=ring_n, offset=offset),
        ring_n=ring_n,
        offset=offset,
        fixed_pitch_x_m=0.0,
        fixed_pitch_y_m=0.0,
    )


def _uv_layout_for_room(
    *, spec: RectRoomSpec, dim_x_int: float, dim_y_int: float
) -> RectUvLayout:
    fixed_pitch = _float_env("SMD_FIXED_PITCH_M", 0.0)
    aspect = dim_x_int / dim_y_int if dim_y_int > 0 else 1.0
    eps = float(_float_env("SMD_SQUAREISH_EPS", 0.02))
    squareish = (1.0 - eps) <= aspect <= (1.0 + eps)
    if fixed_pitch > 0:
        return _uv_from_fixed_pitch(
            fixed_pitch=fixed_pitch,
            dim_x_int=dim_x_int,
            dim_y_int=dim_y_int,
            squareish=squareish,
        )
    base_pitch, base_offset, base_span_long_m = _base_pitch_parameters(spec)
    return _uv_from_scaled_pitch(
        dim_x_int=dim_x_int,
        dim_y_int=dim_y_int,
        squareish=squareish,
        base_pitch=base_pitch,
        base_offset=base_offset,
        base_span_long_m=base_span_long_m,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 2) Map UV -> physical X,Y, axis-aligned
# ──────────────────────────────────────────────────────────────────────────────


def build_rect_grid(
    length_m: float | None = None,
    width_m: float | None = None,
    height_m: float | None = None,
    wall_margin_m: float | None = None,
    module_side_x_m: float | None = None,
    module_side_y_m: float | None = None,
    mount_z_m: float | None = None,
) -> tuple[list[RectPosition], RectMetadata]:
    """
    Called by generate_emitters_smd.get_module_positions() when LAYOUT_MODE=rect_rect.

    Steps:
      1) Generate fixed 203-emitter (u,v,ring) pattern.
      2) Compute min/max u and v over all points.
      3) Pick pitch_x and pitch_y so that:
           (max_u - min_u) * pitch_x ≈ interior X span
           (max_v - min_v) * pitch_y ≈ interior Y span
      4) Map:
           x = (u - u_center) * pitch_x
           y = (v - v_center) * pitch_y
         so pattern is centered in the room and axis-aligned.
    """

    spec = _resolve_room_spec(
        length_m=length_m,
        width_m=width_m,
        height_m=height_m,
        wall_margin_m=wall_margin_m,
        module_side_x_m=module_side_x_m,
        module_side_y_m=module_side_y_m,
        mount_z_m=mount_z_m,
    )
    dim_x_int, dim_y_int = _interior_spans(spec)
    uv_layout = _uv_layout_for_room(spec=spec, dim_x_int=dim_x_int, dim_y_int=dim_y_int)
    uv_points = uv_layout.points

    us_all = [u for u, _, _ in uv_points]
    vs_all = [v for _, v, _ in uv_points]
    min_u, max_u = min(us_all), max(us_all)
    min_v, max_v = min(vs_all), max(vs_all)

    span_u = max_u - min_u
    span_v = max_v - min_v
    if span_u <= 0 or span_v <= 0:
        raise RuntimeError("Rect layout: invalid UV span; cannot fit pattern.")
    if uv_layout.fixed_pitch_x_m > 0:
        pitch_x = uv_layout.fixed_pitch_x_m
        pitch_y = uv_layout.fixed_pitch_y_m
    else:
        pitch_x = dim_x_int / span_u
        pitch_y = dim_y_int / span_v
    pitch = min(pitch_x, pitch_y)

    # Center of UV pattern (in u,v grid)
    u_center = 0.5 * (min_u + max_u)
    v_center = 0.5 * (min_v + max_v)

    positions: list[RectPosition] = []
    for u, v, ring in uv_points:
        x = (u - u_center) * pitch_x
        y = (v - v_center) * pitch_y
        positions.append(
            {
                "x": float(x),
                "y": float(y),
                "z": float(spec.mount_z_m),
                "ring": int(ring),
            }
        )

    ring_max = max(r for _, _, r in uv_points)
    rings_count = int(ring_max + 1)

    meta: RectMetadata = {
        "pitch_x_m": float(pitch_x),
        "pitch_y_m": float(pitch_y),
        "pitch_m": float(pitch),
        "fixed_pitch_m": float(uv_layout.fixed_pitch_x_m)
        if uv_layout.fixed_pitch_x_m > 0
        else 0.0,
        "span_u": int(span_u),
        "span_v": int(span_v),
        "footprint_x_m": float(span_u * pitch_x),
        "footprint_y_m": float(span_v * pitch_y),
        "z_mount_m": float(spec.mount_z_m),
        "length_m": float(spec.length_m),
        "width_m": float(spec.width_m),
        "height_m": float(spec.height_m),
        "wall_margin_m": float(spec.wall_margin_m),
        "module_side_x_m": float(spec.module_side_x_m),
        "module_side_y_m": float(spec.module_side_y_m),
        "layout_mode": "rect_rect",
        "swapped_axes": bool(spec.swapped_axes),
        "modules": len(positions),
        "rings_count": rings_count,
        "ring_n": uv_layout.ring_n,
        "offset": uv_layout.offset,
    }

    return positions, meta
