from __future__ import annotations

import math
from dataclasses import dataclass

Point2D = tuple[float, float]
FixtureCenter = tuple[float, float]


@dataclass(frozen=True)
class SpydrFixtureGeometry:
    fixture_length_in: float
    fixture_width_in: float
    fixture_height_in: float
    bar_length_in: float
    bar_width_in: float
    bar_height_in: float
    fixture_length_m: float
    fixture_width_m: float
    fixture_height_m: float
    bar_length_m: float
    bar_width_m: float
    bar_height_m: float
    spacing_m: float
    rotation_rad: float
    bar_offsets_m: list[float]
    bar_offsets_in: list[float]
    fixture_ppf_active: float
    fixture_ppe: float
    fixture_input_w: float


def maybe_swap_dims(
    length_m: float, width_m: float, *, align_long_axis_x: bool
) -> tuple[float, float]:
    if align_long_axis_x and width_m > length_m:
        return width_m, length_m
    return length_m, width_m


def reference_bar_width_in(
    total_width_in: float, *, bar_width_raw: str, bars: int
) -> float:
    if bar_width_raw:
        return float(bar_width_raw)
    if bars <= 1:
        return total_width_in
    return total_width_in / max((2 * bars) - 1, 1)


def bar_spacing_in(
    total_width_in: float,
    bar_width_in: float,
    *,
    bar_spacing_raw: str,
    bars: int,
) -> float:
    if bar_spacing_raw:
        return float(bar_spacing_raw)
    if bars <= 1:
        return 0.0
    return max(total_width_in - bar_width_in, 0.0) / float(bars - 1)


def rect_corners(
    ccx: float, ccy: float, lx: float, ly: float, rot_rad: float
) -> list[Point2D]:
    c = math.cos(rot_rad)
    s = math.sin(rot_rad)
    pts = [(-lx / 2, -ly / 2), (lx / 2, -ly / 2), (lx / 2, ly / 2), (-lx / 2, ly / 2)]
    out: list[Point2D] = []
    for dx, dy in pts:
        x = ccx + c * dx - s * dy
        y = ccy + s * dx + c * dy
        out.append((x, y))
    return out


def fixture_half_extents(
    fixture_length_m: float, fixture_width_m: float
) -> tuple[float, float]:
    return 0.5 * fixture_length_m, 0.5 * fixture_width_m


def safe_half_spans(
    length_m: float, width_m: float, wall_margin: float, hx: float, hy: float
) -> tuple[float, float]:
    half_x = 0.5 * length_m
    half_y = 0.5 * width_m
    ax = half_x - wall_margin - hx
    ay = half_y - wall_margin - hy
    if ax <= 0 or ay <= 0:
        raise SystemExit(
            "ERROR: fixture is larger than allowed interior span. Reduce bar length/spacing or margin."
        )
    return ax, ay


def evenly_spaced_centers(
    span_m: float, fixture_span_m: float, count: int, wall_margin_m: float
) -> list[float]:
    if count <= 0:
        return []
    usable = span_m - 2.0 * wall_margin_m
    max_count = int(math.floor(usable / max(fixture_span_m, 1e-9)))
    if max_count <= 0:
        raise SystemExit(
            "ERROR: fixtures overlap given current room size, margin, or bar lengths."
        )
    if count > max_count:
        print(
            f"NOTE: reducing fixture count from {count} to {max_count} to avoid overlap."
        )
        count = max_count
    free = usable - count * fixture_span_m
    if count == 1:
        return [0.0]
    gap = free / (count + 1)
    start = -0.5 * usable + gap + 0.5 * fixture_span_m
    return [start + i * (fixture_span_m + gap) for i in range(count)]


def fixture_centers(
    *,
    length_m: float,
    width_m: float,
    wall_margin_m: float,
    edge_inset_m: float,
    nx: int,
    ny: int,
    fixture_length_m: float,
    fixture_width_m: float,
) -> tuple[list[FixtureCenter], int, int]:
    hx, hy = fixture_half_extents(fixture_length_m, fixture_width_m)
    eff_margin = wall_margin_m + edge_inset_m
    safe_half_spans(length_m, width_m, eff_margin, hx, hy)
    xs = evenly_spaced_centers(length_m, 2.0 * hx, nx, eff_margin)
    ys = evenly_spaced_centers(width_m, 2.0 * hy, ny, eff_margin)
    return [(x, y) for y in ys for x in xs], len(xs), len(ys)
