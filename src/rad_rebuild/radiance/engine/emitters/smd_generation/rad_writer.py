from __future__ import annotations

from collections.abc import Callable
from typing import TextIO

from rad_rebuild.radiance.engine.emitters.deterministic_ids import radiance_identifier

RadianceIdentifier = Callable[..., str]


def write_area_square(
    fh: TextIO,
    mat: str,
    cx: float,
    cy: float,
    z: float,
    side: float,
    *,
    identifier: RadianceIdentifier = radiance_identifier,
) -> None:
    hs = side * 0.5
    fh.write(f"{mat} polygon {identifier('poly', cx, cy)}\n0\n0\n12\n")
    fh.write(f"  {cx - hs:.6f} {cy + hs:.6f} {z:.6f}\n")
    fh.write(f"  {cx + hs:.6f} {cy + hs:.6f} {z:.6f}\n")
    fh.write(f"  {cx + hs:.6f} {cy - hs:.6f} {z:.6f}\n")
    fh.write(f"  {cx - hs:.6f} {cy - hs:.6f} {z:.6f}\n\n")


def write_area_rect(
    fh: TextIO,
    mat: str,
    cx: float,
    cy: float,
    z: float,
    lx: float,
    ly: float,
    *,
    identifier: RadianceIdentifier = radiance_identifier,
) -> None:
    hx = lx * 0.5
    hy = ly * 0.5
    fh.write(f"{mat} polygon {identifier('poly', cx, cy, lx, ly)}\n0\n0\n12\n")
    fh.write(f"  {cx - hx:.6f} {cy + hy:.6f} {z:.6f}\n")
    fh.write(f"  {cx + hx:.6f} {cy + hy:.6f} {z:.6f}\n")
    fh.write(f"  {cx + hx:.6f} {cy - hy:.6f} {z:.6f}\n")
    fh.write(f"  {cx - hx:.6f} {cy - hy:.6f} {z:.6f}\n\n")


def write_area_grid(
    fh: TextIO,
    mat: str,
    cx: float,
    cy: float,
    z: float,
    side: float,
    grid: int,
    *,
    identifier: RadianceIdentifier = radiance_identifier,
) -> None:
    if grid <= 1:
        write_area_square(fh, mat, cx, cy, z, side, identifier=identifier)
        return
    cell = side / grid
    start = -0.5 * side + 0.5 * cell
    for row in range(grid):
        for col in range(grid):
            px = cx + start + col * cell
            py = cy + start + row * cell
            write_area_square(fh, mat, px, py, z, cell, identifier=identifier)


def write_area_rect_grid(
    fh: TextIO,
    mat: str,
    cx: float,
    cy: float,
    z: float,
    lx: float,
    ly: float,
    grid: int,
    *,
    identifier: RadianceIdentifier = radiance_identifier,
) -> None:
    if grid <= 1:
        write_area_rect(fh, mat, cx, cy, z, lx, ly, identifier=identifier)
        return
    cell_x = lx / grid
    cell_y = ly / grid
    start_x = -0.5 * lx + 0.5 * cell_x
    start_y = -0.5 * ly + 0.5 * cell_y
    for row in range(grid):
        for col in range(grid):
            px = cx + start_x + col * cell_x
            py = cy + start_y + row * cell_y
            write_area_rect(
                fh, mat, px, py, z, cell_x, cell_y, identifier=identifier
            )


def write_brightfunc_ref(
    fh: TextIO, patt_name: str, funcname: str, cal_name: str
) -> None:
    fh.write(f"void brightfunc {patt_name}\n")
    fh.write(f"2 {funcname} {cal_name}\n")
    fh.write("0\n")
    fh.write("0\n\n")
