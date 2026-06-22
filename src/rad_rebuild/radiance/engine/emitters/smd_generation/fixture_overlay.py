from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, cast

from rad_rebuild.radiance.engine.emitters.smd_generation.positions import (
    _diamond_to_axis,
)
from rad_rebuild.radiance.engine.layout.layout_generator import (
    generate_layout,
    generate_layout_with_zones,
    get_central_modules,
    get_min_fixtures,
    get_module_cobs,
    get_ring_positions,
    get_ring_positions_rect,
)

PositionRecord = dict[str, Any]
JsonObject = dict[str, Any]
FixturePoint = dict[str, float]
FixtureGroup = tuple[str, str, list[tuple[float, float]]]
SquareFixtureGroup = tuple[str, str, list[tuple[int, int]]]
LayoutGenerator = Callable[..., JsonObject]

_generate_layout = cast(LayoutGenerator, generate_layout)
_generate_layout_with_zones = cast(LayoutGenerator, generate_layout_with_zones)
_get_ring_positions = cast(Callable[[int], list[tuple[int, int]]], get_ring_positions)
_get_ring_positions_rect = cast(
    Callable[[int, int], tuple[list[tuple[float, float]], int, int, int, int]],
    get_ring_positions_rect,
)
_get_min_fixtures = cast(
    Callable[..., tuple[int, list[tuple[int, int]]]],
    get_min_fixtures,
)
_get_module_cobs = cast(Callable[..., list[FixtureGroup]], get_module_cobs)
_get_central_modules = cast(Callable[[int], list[FixtureGroup]], get_central_modules)


@dataclass(frozen=True)
class SmdFixtureOverlaySettings:
    length_m: float
    width_m: float
    layout_mode: str


@dataclass(frozen=True)
class FixtureOverlayMeta:
    layout_mode: str
    pitch_x: float
    pitch_y: float
    ring_n: int


def _map_exact_fixture_groups(
    groups: list[FixtureGroup],
    all_positions: list[tuple[float, float]],
    pitch_x_m: float,
    pitch_y_m: float,
    z_m: float,
) -> tuple[list[JsonObject], JsonObject]:
    if not groups or not all_positions or pitch_x_m <= 0 or pitch_y_m <= 0:
        return [], {}

    axis_points = [_diamond_to_axis(x, y) for x, y in all_positions]
    xs = [p[0] for p in axis_points]
    ys = [p[1] for p in axis_points]
    x_center = 0.5 * (min(xs) + max(xs))
    y_center = 0.5 * (min(ys) + max(ys))

    fixture_groups: list[JsonObject] = []
    counts: dict[str, int] = {}
    for mtype, orient, cobs in groups:
        pts = []
        for x, y in cobs:
            ax, ay = _diamond_to_axis(x, y)
            pts.append(
                {
                    "x": round((ax - x_center) * pitch_x_m, 6),
                    "y": round((ay - y_center) * pitch_y_m, 6),
                    "z": round(float(z_m), 6),
                }
            )
        fixture_groups.append({"type": mtype, "orient": orient, "points": pts})
        counts[mtype] = counts.get(mtype, 0) + 1
    return fixture_groups, counts


def _infer_ring_n_from_positions(positions: list[PositionRecord]) -> int:
    ij = [(p.get("i"), p.get("j")) for p in positions if "i" in p and "j" in p]
    if not ij:
        return 0
    ring_n = 0
    for i, j in ij:
        if i is None or j is None:
            continue
        try:
            ring_n = max(ring_n, int(round(abs(float(i)) + abs(float(j)))))
        except (TypeError, ValueError):
            continue
    return ring_n


def _fixture_groups_square(ring_n: int) -> list[SquareFixtureGroup]:
    groups: list[SquareFixtureGroup] = []
    if ring_n <= 0:
        return groups
    if ring_n >= 1:
        groups.append(("centerpiece", "o1", [(0, 0), (1, 0), (0, 1), (-1, 0), (0, -1)]))
    for L in range(2, ring_n + 1):
        inner_ring = L - 1
        ring_pos = _get_ring_positions(inner_ring)
        r = len(ring_pos)
        corners = {0, inner_ring + 1, 2 * (inner_ring + 1), 3 * (inner_ring + 1)}
        _min_fix, modules = _get_min_fixtures(r, corners, inner_ring)
        raw_groups = _get_module_cobs(r, modules, ring_pos)
        groups.extend(cast(list[SquareFixtureGroup], raw_groups))
    return groups


def _fixture_groups_rect(ring_n: int, offset: int) -> list[FixtureGroup]:
    groups: list[FixtureGroup] = []
    if ring_n <= 0:
        return groups
    if offset > 0:
        groups.extend(_get_central_modules(offset))
    for k in range(1, ring_n + 1):
        ring_pos, len_left, len_top, len_right, _len_bottom = _get_ring_positions_rect(
            k, offset
        )
        r = len(ring_pos)
        corners = {
            0,
            len_left - 1,
            len_left + len_top - 1,
            len_left + len_top + len_right - 1,
        }
        _min_fix, modules = _get_min_fixtures(r, corners)
        groups.extend(_get_module_cobs(r, modules, ring_pos))
    return groups


def _map_fixture_groups_square(
    groups: list[SquareFixtureGroup],
    pitch_x: float,
    pitch_y: float,
    z_m: float,
) -> tuple[list[JsonObject], JsonObject]:
    fixture_groups: list[JsonObject] = []
    counts: dict[str, int] = {}
    for mtype, orient, cobs in groups:
        pts: list[FixturePoint] = []
        for i, j in cobs:
            x = (float(i) - float(j)) * pitch_x
            y = (float(i) + float(j)) * pitch_y
            pts.append({"x": round(x, 6), "y": round(y, 6), "z": round(float(z_m), 6)})
        fixture_groups.append({"type": mtype, "orient": orient, "points": pts})
        counts[mtype] = counts.get(mtype, 0) + 1
    return fixture_groups, counts


def _map_fixture_groups_rect(
    groups: list[FixtureGroup],
    pitch_x: float,
    pitch_y: float,
    z_m: float,
) -> tuple[list[JsonObject], JsonObject]:
    fixture_groups: list[JsonObject] = []
    counts: dict[str, int] = {}
    all_pts = [pt for _, _, cobs in groups for pt in cobs]
    if not all_pts:
        return [], {}
    u_vals = [float(u) for u, _ in all_pts]
    v_vals = [float(v) for _, v in all_pts]
    u_center = 0.5 * (min(u_vals) + max(u_vals))
    v_center = 0.5 * (min(v_vals) + max(v_vals))

    for mtype, orient, cobs in groups:
        pts: list[FixturePoint] = []
        for u, v in cobs:
            x = (float(u) - u_center) * pitch_x
            y = (float(v) - v_center) * pitch_y
            pts.append({"x": round(x, 6), "y": round(y, 6), "z": round(float(z_m), 6)})
        fixture_groups.append({"type": mtype, "orient": orient, "points": pts})
        counts[mtype] = counts.get(mtype, 0) + 1
    return fixture_groups, counts


def _fixture_overlay_meta(
    meta: JsonObject, default_layout_mode: str
) -> FixtureOverlayMeta:
    layout_mode = str(meta.get("layout_mode", default_layout_mode))
    pitch_x = float(meta.get("pitch_x_m", meta.get("spacing_m", 0.0)) or 0.0)
    pitch_y = float(meta.get("pitch_y_m", pitch_x) or pitch_x)
    return FixtureOverlayMeta(
        layout_mode=layout_mode,
        pitch_x=pitch_x,
        pitch_y=pitch_y,
        ring_n=int(meta.get("ring_n", 0) or 0),
    )


def _square_fixture_overlay(
    positions: list[PositionRecord], overlay: FixtureOverlayMeta, z_m: float
) -> tuple[list[JsonObject], JsonObject]:
    ring_n = overlay.ring_n
    if ring_n <= 0:
        ring_n = _infer_ring_n_from_positions(positions)
    if ring_n <= 0 or overlay.pitch_x <= 0 or overlay.pitch_y <= 0:
        return [], {}
    groups = _fixture_groups_square(ring_n)
    return _map_fixture_groups_square(groups, overlay.pitch_x, overlay.pitch_y, z_m)


def _rect_fixture_overlay(
    meta: JsonObject, overlay: FixtureOverlayMeta, z_m: float
) -> tuple[list[JsonObject], JsonObject]:
    if overlay.ring_n <= 0 or overlay.pitch_x <= 0 or overlay.pitch_y <= 0:
        return [], {}
    try:
        offset = int(meta.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    if offset <= 0:
        groups = _fixture_groups_square(overlay.ring_n)
        return _map_fixture_groups_square(groups, overlay.pitch_x, overlay.pitch_y, z_m)
    rect_groups = _fixture_groups_rect(overlay.ring_n, offset)
    return _map_fixture_groups_rect(rect_groups, overlay.pitch_x, overlay.pitch_y, z_m)


def _room_ft_from_meta(
    meta: JsonObject, settings: SmdFixtureOverlaySettings
) -> tuple[float, float]:
    length_m = float(meta.get("room_L_m", settings.length_m))
    width_m = float(meta.get("room_W_m", settings.width_m))
    return (
        length_m / 0.3048 if length_m > 0 else 0.0,
        width_m / 0.3048 if width_m > 0 else 0.0,
    )


def _base_n_override_from_meta(meta: JsonObject) -> int | None:
    try:
        base_n = int(meta.get("base_n", os.environ.get("SMD_BASE_RING_N", "0")))
    except (TypeError, ValueError):
        base_n = 0
    return base_n if base_n > 0 else None


def _exact_fixture_overlay(
    meta: JsonObject,
    overlay: FixtureOverlayMeta,
    z_m: float,
    settings: SmdFixtureOverlaySettings,
) -> tuple[list[JsonObject], JsonObject]:
    length_ft, width_ft = _room_ft_from_meta(meta, settings)
    layout = _generate_layout_with_zones(
        length_ft,
        width_ft,
        base_n_override=_base_n_override_from_meta(meta),
    )
    exact_groups = cast(list[FixtureGroup], layout.get("module_groups") or [])
    all_positions = cast(list[tuple[float, float]], layout.get("all_positions") or [])
    pitch_x = float(
        meta.get("pitch_x_m", meta.get("pitch_m", meta.get("spacing_m", 0.0))) or 0.0
    )
    pitch_y = float(
        meta.get("pitch_y_m", meta.get("pitch_m", meta.get("spacing_m", 0.0))) or 0.0
    )
    return _map_exact_fixture_groups(
        exact_groups,
        all_positions,
        pitch_x if pitch_x > 0 else overlay.pitch_x,
        pitch_y if pitch_y > 0 else overlay.pitch_y,
        z_m,
    )


def _fallback_layout_groups(
    meta: JsonObject, settings: SmdFixtureOverlaySettings
) -> tuple[list[FixtureGroup], list[tuple[float, float]]]:
    length_ft, width_ft = _room_ft_from_meta(meta, settings)
    base_n = int(os.environ.get("SMD_BASE_RING_N", "0") or "0")
    layout = _generate_layout(
        length_ft,
        width_ft,
        base_n_override=base_n if base_n > 0 else None,
    )
    return (
        cast(list[FixtureGroup], layout.get("module_groups") or []),
        cast(list[tuple[float, float]], layout.get("all_positions") or []),
    )


def _fallback_axis_center(
    all_positions: list[tuple[float, float]],
) -> tuple[float, float]:
    uv = [(float(i) - float(j), float(i) + float(j)) for i, j in all_positions]
    u_vals = [u for u, _ in uv]
    v_vals = [v for _, v in uv]
    return 0.5 * (min(u_vals) + max(u_vals)), 0.5 * (min(v_vals) + max(v_vals))


def _map_fallback_fixture_groups(
    groups: list[FixtureGroup],
    all_positions: list[tuple[float, float]],
    overlay: FixtureOverlayMeta,
    z_m: float,
) -> tuple[list[JsonObject], JsonObject]:
    if not groups or not all_positions:
        return [], {}
    u_center, v_center = _fallback_axis_center(all_positions)
    pitch_x = overlay.pitch_x if overlay.pitch_x > 0 else 1.0
    pitch_y = overlay.pitch_y if overlay.pitch_y > 0 else 1.0
    fixture_groups: list[JsonObject] = []
    counts: dict[str, int] = {}
    for mtype, orient, cobs in groups:
        pts: list[FixturePoint] = []
        for i, j in cobs:
            u = float(i) - float(j)
            v = float(i) + float(j)
            pts.append(
                {
                    "x": round((u - u_center) * pitch_x, 6),
                    "y": round((v - v_center) * pitch_y, 6),
                    "z": round(float(z_m), 6),
                }
            )
        fixture_groups.append({"type": mtype, "orient": orient, "points": pts})
        counts[mtype] = counts.get(mtype, 0) + 1
    return fixture_groups, counts


def _fallback_fixture_overlay(
    meta: JsonObject,
    overlay: FixtureOverlayMeta,
    z_m: float,
    settings: SmdFixtureOverlaySettings,
) -> tuple[list[JsonObject], JsonObject]:
    groups, all_positions = _fallback_layout_groups(meta, settings)
    return _map_fallback_fixture_groups(groups, all_positions, overlay, z_m)


def build_fixture_overlay(
    positions: list[PositionRecord],
    meta: JsonObject,
    z_m: float,
    settings: SmdFixtureOverlaySettings,
) -> tuple[list[JsonObject], JsonObject]:
    overlay = _fixture_overlay_meta(meta, settings.layout_mode)
    if overlay.layout_mode in {"grid", "uniform", "matrix"}:
        return [], {}
    if overlay.layout_mode == "square":
        return _square_fixture_overlay(positions, overlay, z_m)
    if overlay.layout_mode == "rect_rect":
        return _rect_fixture_overlay(meta, overlay, z_m)
    if overlay.layout_mode in {"exact_tiled", "tiled", "exact", "modular"}:
        return _exact_fixture_overlay(meta, overlay, z_m, settings)
    return _fallback_fixture_overlay(meta, overlay, z_m, settings)
