from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, TextIO

from rad_rebuild.radiance.engine.emitters.deterministic_ids import radiance_identifier
from rad_rebuild.radiance.engine.emitters.spydr3_generation.geometry import (
    Point2D,
    SpydrFixtureGeometry,
    rect_corners,
)
from rad_rebuild.radiance.engine.emitters.spydr3_generation.ies_stage import (
    SpydrIesStage,
)

JsonObject = dict[str, Any]
RadianceIdentifier = Callable[..., str]


def write_poly(
    fh: TextIO, mat: str, name: str, z: float, corners_face_up: list[Point2D]
) -> None:
    corners = list(corners_face_up)[::-1]
    fh.write(f"{mat} polygon {name}\n0\n0\n12\n")
    for x, y in corners:
        fh.write(f"  {x:.6f} {y:.6f} {z:.6f}\n")
    fh.write("\n")


def write_bar(
    fh: TextIO,
    mat: str,
    cx: float,
    cy: float,
    z: float,
    length_m: float,
    width_m: float,
    rot_rad: float,
    sub: int,
    layout_bars: list[JsonObject],
    *,
    identifier: RadianceIdentifier = radiance_identifier,
) -> None:
    if sub <= 1:
        corners = rect_corners(cx, cy, length_m, width_m, rot_rad)
        write_poly(fh, mat, identifier("bar", cx, cy), z, corners)
        layout_bars.append({"corners": corners[::-1]})
        return

    _append_subpatched_bar(
        fh,
        mat,
        cx,
        cy,
        z,
        length_m,
        width_m,
        rot_rad,
        sub,
        layout_bars,
        identifier=identifier,
    )


def append_bar_layout(
    layout_bars: list[JsonObject],
    cx: float,
    cy: float,
    length_m: float,
    width_m: float,
    rot_rad: float,
) -> None:
    corners = rect_corners(cx, cy, length_m, width_m, rot_rad)
    layout_bars.append({"corners": corners[::-1]})


def write_proxy_box(
    fh: TextIO,
    name: str,
    cx: float,
    cy: float,
    z: float,
    length_m: float,
    width_m: float,
    height_m: float,
    rot_deg: float,
) -> None:
    half_l = 0.5 * length_m
    half_w = 0.5 * width_m
    fh.write(
        f"!genbox spydr3_proxy {name} {length_m:.6f} {width_m:.6f} {height_m:.6f} | "
        f"xform -t {-half_l:.6f} {-half_w:.6f} 0 "
        f"-rz {rot_deg:.6f} -t {cx:.6f} {cy:.6f} {z:.6f}\n"
    )


def spydr_fixture_record(
    cx: float, cy: float, geometry: SpydrFixtureGeometry
) -> JsonObject:
    bars_here: list[JsonObject] = []
    for oy in geometry.bar_offsets_m:
        append_bar_layout(
            bars_here,
            cx,
            cy + oy,
            geometry.bar_length_m,
            geometry.bar_width_m,
            geometry.rotation_rad,
        )
    body_corners = rect_corners(
        cx,
        cy,
        geometry.fixture_length_m,
        geometry.fixture_width_m,
        geometry.rotation_rad,
    )[::-1]
    return {"cx": cx, "cy": cy, "body_corners": body_corners, "bars": bars_here}


def write_spydr_fixture(
    fh: TextIO,
    cx: float,
    cy: float,
    geometry: SpydrFixtureGeometry,
    stage: SpydrIesStage,
    *,
    mount_z_m: float,
    rot_deg: float,
    ies_rot_x_deg: float,
    ies_rot_z_deg: float,
    proxy_mode: bool,
    identifier: RadianceIdentifier = radiance_identifier,
) -> None:
    zrot = rot_deg + ies_rot_z_deg
    fh.write(
        f"!xform -rx {ies_rot_x_deg:.6f} -rz {zrot:.6f} "
        f"-t {cx:.6f} {cy:.6f} {mount_z_m:.6f} {stage.rad_path.as_posix()}\n"
    )
    if proxy_mode:
        proxy_z = mount_z_m + max(0.0, 0.5 * geometry.fixture_height_m)
        write_proxy_box(
            fh,
            identifier("fixture_proxy", cx, cy),
            cx,
            cy,
            proxy_z,
            geometry.fixture_length_m,
            geometry.fixture_width_m,
            geometry.fixture_height_m,
            rot_deg,
        )


def _append_subpatched_bar(
    fh: TextIO,
    mat: str,
    cx: float,
    cy: float,
    z: float,
    length_m: float,
    width_m: float,
    rot_rad: float,
    sub: int,
    layout_bars: list[JsonObject],
    *,
    identifier: RadianceIdentifier,
) -> None:
    cell_l = length_m / sub
    cell_w = width_m / sub
    start_l = -0.5 * length_m + 0.5 * cell_l
    start_w = -0.5 * width_m + 0.5 * cell_w
    c = math.cos(rot_rad)
    s = math.sin(rot_rad)
    full_corners = rect_corners(cx, cy, length_m, width_m, rot_rad)
    layout_bars.append({"corners": full_corners[::-1]})
    for row in range(sub):
        for col in range(sub):
            dx = start_l + col * cell_l
            dy = start_w + row * cell_w
            cx2 = cx + dx * c - dy * s
            cy2 = cy + dx * s + dy * c
            corners = rect_corners(cx2, cy2, cell_l, cell_w, rot_rad)
            write_poly(fh, mat, identifier("bar", cx2, cy2), z, corners)
