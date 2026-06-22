from __future__ import annotations

from typing import TextIO

from rad_rebuild.radiance.engine.emitters.deterministic_ids import radiance_identifier


def write_box(
    fh: TextIO,
    mat: str,
    name_prefix: str,
    x0: float,
    x1: float,
    y0: float,
    y1: float,
    z0: float,
    z1: float,
) -> None:
    faces = [
        ("top", [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
        ("bottom", [(x0, y1, z0), (x1, y1, z0), (x1, y0, z0), (x0, y0, z0)]),
        ("north", [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)]),
        ("south", [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]),
        ("west", [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)]),
        ("east", [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]),
    ]
    for tag, pts in faces:
        fh.write(f"{mat} polygon {name_prefix}_{tag}\n0\n0\n12\n")
        for x, y, z in pts:
            fh.write(f"  {x:.6f} {y:.6f} {z:.6f}\n")
        fh.write("\n")


def write_stack_cavity(
    fh: TextIO,
    cx: float,
    cy: float,
    z0: float,
    *,
    ptfe_mode: bool,
    aperture_m: float,
    liner_thk_m: float,
    stack_height_m: float,
) -> None:
    if not ptfe_mode or liner_thk_m <= 0 or stack_height_m <= 0 or aperture_m <= 0:
        return
    half_inner = 0.5 * aperture_m
    z1 = z0 + stack_height_m

    write_box(
        fh,
        "ptfe_liner",
        radiance_identifier("ptfe_n_m", cx, cy, z0, "n"),
        cx - half_inner - liner_thk_m,
        cx + half_inner + liner_thk_m,
        cy + half_inner,
        cy + half_inner + liner_thk_m,
        z0,
        z1,
    )
    write_box(
        fh,
        "ptfe_liner",
        radiance_identifier("ptfe_s_m", cx, cy, z0, "s"),
        cx - half_inner - liner_thk_m,
        cx + half_inner + liner_thk_m,
        cy - half_inner - liner_thk_m,
        cy - half_inner,
        z0,
        z1,
    )
    write_box(
        fh,
        "ptfe_liner",
        radiance_identifier("ptfe_w_m", cx, cy, z0, "w"),
        cx - half_inner - liner_thk_m,
        cx - half_inner,
        cy - half_inner,
        cy + half_inner,
        z0,
        z1,
    )
    write_box(
        fh,
        "ptfe_liner",
        radiance_identifier("ptfe_e_m", cx, cy, z0, "e"),
        cx + half_inner,
        cx + half_inner + liner_thk_m,
        cy - half_inner,
        cy + half_inner,
        z0,
        z1,
    )


def write_pmma_lid(
    fh: TextIO,
    module_idx: int,
    cx: float,
    cy: float,
    emitter_z: float,
    *,
    side_m: float,
    lid_offset_m: float,
    lid_thk_m: float,
) -> None:
    hx = 0.5 * side_m
    z_bot = emitter_z + lid_offset_m
    z_top = z_bot + lid_thk_m
    write_box(
        fh,
        "pmma_lid",
        f"pmma_m{module_idx:03d}",
        cx - hx,
        cx + hx,
        cy - hx,
        cy + hx,
        z_bot,
        z_top,
    )
