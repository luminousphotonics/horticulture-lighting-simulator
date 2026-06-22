from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import json
import os
from pathlib import Path
from collections.abc import Sequence
from typing import Any

Point2D = tuple[float, float]
Point3D = tuple[float, float, float]
OverlayPoint = Point2D | Point3D
PlotKwargs = dict[str, Any]
JsonObject = dict[str, Any]
PointLayer = tuple[str, list[Point3D], PlotKwargs]
PolygonLayer = tuple[str, Sequence[Sequence[OverlayPoint]], PlotKwargs]
LineLayer = tuple[str, list[tuple[Point3D, Point3D]], PlotKwargs]


@dataclass
class OverlayContext:
    plane: str
    points: list[PointLayer] = field(default_factory=list)
    polys: list[PolygonLayer] = field(default_factory=list)
    lines: list[LineLayer] = field(default_factory=list)
    room_bounds: tuple[float, float] | None = None

    def project_point(self, pt: OverlayPoint | None) -> Point2D | None:
        if pt is None:
            return None
        if len(pt) == 2:
            return (pt[0], pt[1])
        x, y, z = pt
        if self.plane == "xy":
            return (x, y)
        if self.plane == "xz":
            return (x, z)
        return (y, z)

    def project_poly(self, poly: Sequence[OverlayPoint]) -> list[Point2D]:
        out: list[Point2D] = []
        for point in poly:
            projected = self.project_point(point)
            if projected is None:
                continue
            out.append(projected)
        return out


LAYOUT_FILENAMES = {
    "smd": "smd_layout.json",
    "spydr3": "spydr3_layout.json",
    "hps": "hps_layout.json",
    "cob": "cob_layout.json",
}


def _runtime_state_root() -> Path:
    workspace_runtime = Path.cwd() / "runtime_state"
    if any((workspace_runtime / name).exists() for name in LAYOUT_FILENAMES.values()):
        return workspace_runtime
    return Path(os.getenv("RADIANCE_RUNTIME_STATE_ROOT", "runtime_state"))


def _layout_path(name: str) -> Path:
    return _runtime_state_root() / LAYOUT_FILENAMES[name]


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except Exception:
        return 0.0


def auto_pick_overlay_mode() -> str:
    candidates = []
    for name in ("smd", "spydr3", "hps", "cob"):
        path = _layout_path(name)
        if path.exists():
            candidates.append((name, _safe_mtime(path)))
    if not candidates:
        return "smd"
    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates[0][0]


def _set_room_bounds(context: OverlayContext, room: JsonObject | None) -> None:
    if room and "L" in room and "W" in room:
        context.room_bounds = (float(room["L"]), float(room["W"]))


def _smd_module_layers(
    data: JsonObject,
) -> tuple[str, list[Point3D], list[list[Point3D]]]:
    z0 = float(data.get("z", 0.0))
    patch_value = data.get("patch_side")
    if patch_value is None:
        patch_value = data.get("patch", 0.127)
    patch_side = float(patch_value)
    centers: list[Point3D] = []
    polys: list[list[Point3D]] = []
    hx = 0.5 * patch_side
    has_strips = False
    for point in data.get("positions", []):
        cx = float(point["x"])
        cy = float(point["y"])
        cz = float(point.get("z", z0))
        centers.append((cx, cy, cz))
        if point.get("kind") == "strip" and point.get("lx") and point.get("ly"):
            has_strips = True
            hx2 = 0.5 * float(point.get("lx"))
            hy2 = 0.5 * float(point.get("ly"))
            polys.append(
                [
                    (cx - hx2, cy - hy2, cz),
                    (cx + hx2, cy - hy2, cz),
                    (cx + hx2, cy + hy2, cz),
                    (cx - hx2, cy + hy2, cz),
                ]
            )
        else:
            polys.append(
                [
                    (cx - hx, cy - hx, cz),
                    (cx + hx, cy - hx, cz),
                    (cx + hx, cy + hx, cz),
                    (cx - hx, cy + hx, cz),
                ]
            )
    return ("SMD emitters" if has_strips else "SMD modules", centers, polys)


def _add_smd_module_layers(
    context: OverlayContext,
    *,
    label: str,
    centers: list[Point3D],
    polys: list[list[Point3D]],
) -> None:
    context.polys.append(
        (
            label,
            polys,
            dict(edgecolor="#F2F4F7", facecolor="none", linewidth=0.9, alpha=0.9),
        )
    )
    context.points.append(
        (
            "SMD centers",
            centers,
            dict(marker="o", s=10, facecolors="red", edgecolors="red", linewidths=0.6),
        )
    )


def _smd_fixture_groups_from_json(data: JsonObject, z0: float) -> list[JsonObject]:
    fixture_groups = data.get("fixture_groups")
    if isinstance(fixture_groups, list) and fixture_groups:
        return [group for group in fixture_groups if isinstance(group, dict)]
    meta = data.get("meta") or {}
    positions = data.get("positions") or []
    if not (meta and positions):
        return []
    previous_use_json = os.environ.get("USE_RING_POWERS_JSON")
    try:
        os.environ["USE_RING_POWERS_JSON"] = "0"
        gem = importlib.import_module("generate_emitters_smd")
        built_groups, _ = gem._build_fixture_overlay(positions, meta, z0)
        if isinstance(built_groups, list):
            return [group for group in built_groups if isinstance(group, dict)]
        return []
    except (AttributeError, ImportError, TypeError, ValueError):
        return []
    finally:
        if previous_use_json is None:
            os.environ.pop("USE_RING_POWERS_JSON", None)
        else:
            os.environ["USE_RING_POWERS_JSON"] = previous_use_json


def _smd_fixture_lines(
    fixture_groups: Sequence[JsonObject], z0: float
) -> list[tuple[Point3D, Point3D]]:
    lines: list[tuple[Point3D, Point3D]] = []
    for group in fixture_groups:
        module_type = group.get("type", "")
        pts: list[Point3D] = []
        for pt in group.get("points", []):
            try:
                x = float(pt.get("x"))
                y = float(pt.get("y"))
            except (TypeError, ValueError):
                continue
            pts.append((x, y, z0))
        if not pts:
            continue
        if module_type == "centerpiece":
            base = pts[0]
            lines.extend((base, point) for point in pts[1:])
        elif "linear" in module_type:
            lines.extend(zip(pts, pts[1:]))
        elif len(pts) >= 4:
            long = pts[1:4] if module_type == "L" else pts[0:3]
            short = pts[0:2] if module_type == "L" else pts[2:4]
            lines.extend(zip(long, long[1:]))
            lines.extend(zip(short, short[1:]))
        else:
            lines.extend(zip(pts, pts[1:]))
    return lines


def _add_smd_fixture_layers(
    context: OverlayContext, fixture_groups: Sequence[JsonObject], z0: float
) -> None:
    lines = _smd_fixture_lines(fixture_groups, z0)
    if lines:
        context.lines.append(
            (
                "SMD fixtures",
                lines,
                dict(
                    color="#F2F4F7",
                    linewidth=1.2,
                    alpha=0.9,
                    legend_count=len(fixture_groups),
                ),
            )
        )


def _try_overlay_smd_json(context: OverlayContext, smd_json: Path) -> bool:
    try:
        data = json.loads(smd_json.read_text())
        if data.get("units") != "meters":
            print("Overlay (SMD) skipped: smd_layout.json not in meters")
            return True
        _set_room_bounds(context, data.get("room"))
        z0 = float(data.get("z", 0.0))
        label, centers, polys = _smd_module_layers(data)
        _add_smd_module_layers(context, label=label, centers=centers, polys=polys)
        fixture_groups = _smd_fixture_groups_from_json(data, z0)
        if fixture_groups:
            _add_smd_fixture_layers(context, fixture_groups, z0)
            print(f"Overlay: {len(fixture_groups)} SMD fixtures (from layout JSON)")
        print(f"Overlay: {len(centers)} SMD modules (from JSON)")
        return True
    except (
        AttributeError,
        ImportError,
        json.JSONDecodeError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as e:
        print("Overlay (SMD) JSON read failed; falling back to module:", e)
        return False


def _try_overlay_smd_module(context: OverlayContext) -> None:
    try:
        gem = importlib.import_module("generate_emitters_smd")
        module_positions = gem.get_module_positions()
        positions, _spacing = (
            module_positions
            if (isinstance(module_positions, tuple) and len(module_positions) == 2)
            else (module_positions, None)
        )
        patch_side = float(getattr(gem, "PATCH_SIDE_M", 0.127))
        data = {
            "z": 0.0,
            "patch_side": patch_side,
            "positions": positions,
        }
        label, centers, polys = _smd_module_layers(data)
        _add_smd_module_layers(context, label=label, centers=centers, polys=polys)
        print(f"Overlay: {len(centers)} SMD modules (from module)")
    except (AttributeError, ImportError, KeyError, TypeError, ValueError) as e:
        print("Overlay (SMD) unavailable:", e)


def try_overlay_smd(context: OverlayContext) -> None:
    smd_json = _layout_path("smd")
    if smd_json.exists() and _try_overlay_smd_json(context, smd_json):
        return
    _try_overlay_smd_module(context)


def try_overlay_spydr3(context: OverlayContext) -> None:
    spydr_json = _layout_path("spydr3")
    if spydr_json.exists():
        try:
            data = json.loads(spydr_json.read_text())
            nfx = len(data.get("fixtures", []))
            print(f"Overlay: Conventional from JSON ({nfx} fixtures)")
            room = data.get("room")
            if room and "L" in room and "W" in room:
                context.room_bounds = (float(room["L"]), float(room["W"]))
            bars = []
            centers = []
            z0 = float(data.get("z", 0.0))
            for fixture in data["fixtures"]:
                centers.append((fixture["cx"], fixture["cy"], z0))
                for bar in fixture["bars"]:
                    xy = [(float(x), float(y)) for (x, y) in bar["corners"]]
                    bars.append(xy)
            context.polys.append(
                (
                    "Conventional bars",
                    bars,
                    dict(edgecolor="red", facecolor="none", linewidth=1.0, alpha=0.9),
                )
            )
            context.points.append(
                (
                    "Conventional centers",
                    centers,
                    dict(
                        marker="s",
                        s=28,
                        facecolors="none",
                        edgecolors="red",
                        linewidths=1.0,
                    ),
                )
            )
            return
        except Exception as e:
            print("Overlay (Conventional) JSON read failed; falling back to module:", e)

    try:
        gsp = importlib.import_module("generate_emitters_spydr3")
        positions = gsp.get_fixture_positions()
        pts = [(point["x"], point["y"], point.get("z", 0.0)) for point in positions]
        context.points.append(
            (
                "Conventional fixtures",
                pts,
                dict(
                    marker="s",
                    s=50,
                    facecolors="none",
                    edgecolors="red",
                    linewidths=1.2,
                ),
            )
        )
        print(f"Overlay: {len(pts)} Conventional fixtures (centers only)")
    except Exception as e:
        print("Overlay (Conventional) unavailable:", e)


def _hps_layers_from_json(
    data: JsonObject,
) -> tuple[list[Point3D], list[list[Point2D]], list[tuple[Point3D, Point3D]], int]:
    fixtures = data.get("fixtures") or []
    z0 = float(data.get("z", 0.0))
    polys: list[list[Point2D]] = []
    centers: list[Point3D] = []
    lamp_lines: list[tuple[Point3D, Point3D]] = []
    for fixture in fixtures:
        cx = float(fixture.get("cx", 0.0))
        cy = float(fixture.get("cy", 0.0))
        centers.append((cx, cy, z0))
        corners = fixture.get("body_corners") or []
        if corners:
            try:
                polys.append([(float(x), float(y)) for (x, y) in corners])
            except (TypeError, ValueError):
                pass
        lamp_line = fixture.get("lamp_line") or []
        if len(lamp_line) >= 2:
            try:
                a = lamp_line[0]
                b = lamp_line[1]
                lamp_lines.append(
                    (
                        (
                            float(a[0]),
                            float(a[1]),
                            float(a[2]) if len(a) > 2 else z0,
                        ),
                        (
                            float(b[0]),
                            float(b[1]),
                            float(b[2]) if len(b) > 2 else z0,
                        ),
                    )
                )
            except (IndexError, TypeError, ValueError):
                pass
    return centers, polys, lamp_lines, len(fixtures)


def _add_hps_layers(
    context: OverlayContext,
    *,
    centers: list[Point3D],
    polys: list[list[Point2D]],
    lamp_lines: list[tuple[Point3D, Point3D]],
    fixture_count: int,
) -> None:
    if polys:
        context.polys.append(
            (
                "1000W HPS fixtures",
                polys,
                dict(edgecolor="red", facecolor="none", linewidth=1.1, alpha=0.95),
            )
        )
    if lamp_lines:
        context.lines.append(
            (
                "HPS lamp axis",
                lamp_lines,
                dict(color="red", linewidth=1.0, alpha=0.9, legend_count=fixture_count),
            )
        )
    if centers:
        context.points.append(
            (
                "1000W HPS centers",
                centers,
                dict(
                    marker="D",
                    s=18,
                    facecolors="none",
                    edgecolors="red",
                    linewidths=1.0,
                ),
            )
        )


def _try_overlay_hps_json(context: OverlayContext, hps_json: Path) -> bool:
    try:
        data = json.loads(hps_json.read_text())
        _set_room_bounds(context, data.get("room"))
        centers, polys, lamp_lines, fixture_count = _hps_layers_from_json(data)
        _add_hps_layers(
            context,
            centers=centers,
            polys=polys,
            lamp_lines=lamp_lines,
            fixture_count=fixture_count,
        )
        print(f"Overlay: 1000W HPS from JSON ({fixture_count} fixtures)")
        return True
    except (
        AttributeError,
        json.JSONDecodeError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as e:
        print("Overlay (1000W HPS) JSON read failed; falling back to module:", e)
        return False


def _try_overlay_hps_module(context: OverlayContext) -> None:
    try:
        gh = importlib.import_module("generate_emitters_hps")
        positions = gh.get_fixture_positions()
        pts = [(point["x"], point["y"], point.get("z", 0.0)) for point in positions]
        context.points.append(
            (
                "1000W HPS fixtures",
                pts,
                dict(
                    marker="D",
                    s=32,
                    facecolors="none",
                    edgecolors="red",
                    linewidths=1.0,
                ),
            )
        )
        print(f"Overlay: {len(pts)} 1000W HPS fixtures (centers only)")
    except (AttributeError, ImportError, KeyError, TypeError, ValueError) as e:
        print("Overlay (1000W HPS) unavailable:", e)


def try_overlay_hps(context: OverlayContext) -> None:
    hps_json = _layout_path("hps")
    if hps_json.exists() and _try_overlay_hps_json(context, hps_json):
        return
    _try_overlay_hps_module(context)


def _cob_centers(data: JsonObject, z0: float) -> list[Point3D]:
    centers: list[Point3D] = []
    for cob in data.get("cobs", []):
        center = cob.get("center") or cob.get("center_xyz") or cob.get("center_m")
        if not (isinstance(center, list) and len(center) >= 2):
            continue
        cx = float(center[0])
        cy = float(center[1])
        cz = float(center[2]) if len(center) >= 3 else z0
        centers.append((cx, cy, cz))
    return centers


def _cob_polys(data: JsonObject) -> list[list[Point2D]]:
    polys: list[list[Point2D]] = []
    for segment in data.get("strip_segments", []):
        corners = segment.get("corners_xy") or segment.get("corners")
        if not (isinstance(corners, list) and len(corners) >= 4):
            continue
        try:
            xy = [(float(point[0]), float(point[1])) for point in corners[:4]]
        except (IndexError, TypeError, ValueError):
            continue
        polys.append(xy)
    return polys


def _add_cob_layers(
    context: OverlayContext,
    *,
    centers: list[Point3D],
    polys: list[list[Point2D]],
) -> None:
    if polys:
        context.polys.append(
            (
                "COB halos",
                polys,
                dict(edgecolor="red", facecolor="none", linewidth=1.2, alpha=0.95),
            )
        )
    if centers:
        context.points.append(
            (
                "COB centers",
                centers,
                dict(marker="o", s=10, color="white", linewidths=0.6),
            )
        )


def _try_overlay_cob_json(context: OverlayContext, cob_json: Path) -> bool:
    try:
        data = json.loads(cob_json.read_text())
        if data.get("units") != "meters":
            print("Overlay (COB) skipped: cob_layout.json not in meters")
            return True
        _set_room_bounds(context, data.get("room") or {})
        z_value = data.get("z_emit_m")
        if z_value is None:
            z_value = data.get("z", 0.0)
        z0 = float(z_value)
        centers = _cob_centers(data, z0)
        polys = _cob_polys(data)
        _add_cob_layers(context, centers=centers, polys=polys)
        print(
            f"Overlay: COB from JSON ({len(centers)} COBs, {len(polys)} halo segments)"
        )
        return True
    except (
        AttributeError,
        json.JSONDecodeError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as e:
        print("Overlay (COB) JSON read failed; falling back to module:", e)
        return False


def _try_overlay_cob_module(context: OverlayContext) -> None:
    try:
        gc = importlib.import_module("generate_emitters_cob")
        positions = gc.get_module_positions()[0]
        pts = [(point["x"], point["y"], point.get("z", 0.0)) for point in positions]
        context.points.append(
            ("COB centers", pts, dict(marker="o", s=10, color="white", linewidths=0.6))
        )
        print(f"Overlay: {len(pts)} COB centers (from module)")
    except (
        AttributeError,
        ImportError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
    ) as e:
        print("Overlay (COB) unavailable:", e)


def try_overlay_cob(context: OverlayContext) -> None:
    cob_json = _layout_path("cob")
    if cob_json.exists() and _try_overlay_cob_json(context, cob_json):
        return
    _try_overlay_cob_module(context)


def load_overlay_context(overlay: str, plane: str) -> OverlayContext:
    context = OverlayContext(plane=plane)
    if overlay == "auto":
        mode = auto_pick_overlay_mode()
        if mode == "smd":
            try_overlay_smd(context)
        elif mode == "spydr3":
            try_overlay_spydr3(context)
        elif mode == "hps":
            try_overlay_hps(context)
        elif mode == "cob":
            try_overlay_cob(context)
    elif overlay == "smd":
        try_overlay_smd(context)
    elif overlay == "spydr3":
        try_overlay_spydr3(context)
    elif overlay == "hps":
        try_overlay_hps(context)
    elif overlay == "cob":
        try_overlay_cob(context)
    elif overlay == "both":
        try_overlay_smd(context)
        try_overlay_spydr3(context)
    return context
