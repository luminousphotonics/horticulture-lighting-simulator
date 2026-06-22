#!/usr/bin/env python3
# generate_emitters_spydr3.py • Conventional LED comparator emitters
# - Default path: EnVision Qube QB-FSG-8B-7T660W whole-fixture IES comparator
#   using measured luminaire photometry + digitized relative SPD + fixture PPF
#   normalization
# - Polygons face DOWN (-Z)
# - Writes runtime_state/spydr3_layout.json for robust overlays

from __future__ import annotations

import json
import math
import os
# ies2rad calls use resolved argv lists without invoking a shell.
import subprocess  # nosec B404
from pathlib import Path
from typing import Any, TextIO

from rad_rebuild.radiance.executables import (
    resolve_executable,
)
from rad_rebuild.radiance.engine.photometry.ies_photon_toolkit import (
    normalize_ies_rad_companion_paths,
)
from rad_rebuild.radiance.engine.emitters.deterministic_ids import radiance_identifier
from rad_rebuild.radiance.engine.emitters.spydr3_generation.config import (
    DEFAULT_BARS,
    DEFAULT_FIXTURE_HEIGHT_IN,
    DEFAULT_FIXTURE_LENGTH_IN,
    DEFAULT_FIXTURE_PPE_UMOL_PER_J,
    DEFAULT_FIXTURE_PPF_UMOL_S,
    DEFAULT_FIXTURE_WIDTH_IN,
    DEFAULT_IES_PATH,
    DEFAULT_IES_SPD_PATH,
    DEFAULT_MODEL,
    IN2M,
    QUBE_IES_LABEL,
    clamp_derate,
    runtime_state_root,
)
from rad_rebuild.radiance.engine.emitters.spydr3_generation.geometry import (
    FixtureCenter,
    Point2D,
    SpydrFixtureGeometry,
    bar_spacing_in,
    fixture_centers,
    fixture_half_extents,
    maybe_swap_dims,
    rect_corners as _geometry_rect_corners,
    reference_bar_width_in,
    safe_half_spans,
    evenly_spaced_centers,
)
from rad_rebuild.radiance.engine.emitters.spydr3_generation.ies_stage import (
    IesBrightdataBlock,
    IesCandelaDistribution,
    IesLightBlock,
    IesPolygonBlock,
    RelativeSPDPhotonMetrics,
    SpydrIesStage,
    angle_deltas,
    collect_ies_polygon_blocks,
    ies_aperture_metadata,
    ies_candela_distribution,
    ies_dimensions_in_from_path,
    ies_dimensions_m,
    ies_input_watts,
    ies_integrate_lumens,
    ies_lm_to_umol,
    ies_total_lumens,
    integrate_candela_lumens,
    load_relative_spd,
    parse_ies_brightdata_block,
    parse_ies_light_block,
    parse_ies_polygon_block,
    photopic_v_lambda,
    polygon_normal,
    polygon_vertices_from_block,
    read_ies_lines,
    relative_spd_rows,
    rewritten_ies_aperture_lines,
    rewrite_ies_rad_as_downward_aperture,
    run_ies2rad,
    scale_ies_rad,
    select_downward_ies_polygon,
    spd_csv_columns,
    spd_lm_to_umol,
    spd_photon_metrics,
    trim_redundant_horizontal_angle,
    validate_relative_spd_rows,
)
from rad_rebuild.radiance.engine.emitters.spydr3_generation.rad_writer import (
    append_bar_layout as _writer_append_bar_layout,
    spydr_fixture_record,
    write_bar as _writer_write_bar,
    write_poly as _writer_write_poly,
    write_proxy_box,
    write_spydr_fixture,
)
from rad_rebuild.radiance.engine.emitters.spydr3_generation.summary import (
    apply_spydr_ies_layout_metadata,
    build_spydr_summary_text,
    spydr_completion_lines,
    spydr_summary_detail_line,
)

PI = math.pi
OUT_DIR = runtime_state_root()
OUT_DIR.mkdir(parents=True, exist_ok=True)
JsonObject = dict[str, Any]


# --- Helpers ---
def _maybe_swap_dims(length_m: float, width_m: float) -> tuple[float, float]:
    return maybe_swap_dims(
        length_m,
        width_m,
        align_long_axis_x=os.getenv("ALIGN_LONG_AXIS_X", "1") == "1",
    )


# --- Fixture (env-overridable) ---
MODEL = os.getenv("SPYDR_MODEL", DEFAULT_MODEL)
PPF_FIXTURE = float(os.getenv("SPYDR_PPF", f"{DEFAULT_FIXTURE_PPF_UMOL_S}"))  # umol/s
BARS = int(os.getenv("SPYDR_BARS", f"{DEFAULT_BARS}"))
BAR_WIDTH_IN_RAW = os.getenv("SPYDR_BAR_WIDTH_IN", "").strip()
BAR_LENGTH_IN_RAW = os.getenv("SPYDR_BAR_LENGTH_IN", "").strip()
FIXTURE_LENGTH_IN_RAW = os.getenv("SPYDR_FIXTURE_LENGTH_IN", "").strip()
BAR_HEIGHT_IN_RAW = os.getenv("SPYDR_BAR_HEIGHT_IN", "").strip()
TOTAL_WIDTH_IN_RAW = os.getenv("SPYDR_TOTAL_WIDTH_IN", "").strip()
FIXTURE_LENGTH_IN = float(FIXTURE_LENGTH_IN_RAW or f"{DEFAULT_FIXTURE_LENGTH_IN}")
TOTAL_WIDTH_IN = float(TOTAL_WIDTH_IN_RAW or f"{DEFAULT_FIXTURE_WIDTH_IN}")
BAR_SPACING_IN_RAW = os.getenv("SPYDR_BAR_SPACING_IN", "").strip()

IES_PATH = Path(os.getenv("SPYDR_IES_PATH", str(DEFAULT_IES_PATH)))
IES_BASENAME = os.getenv("SPYDR_IES_BASENAME", IES_PATH.stem)
IES_ROT_X_DEG = float(os.getenv("SPYDR_IES_ROT_X_DEG", "0"))
IES_ROT_Z_DEG = float(os.getenv("SPYDR_IES_ROT_Z_DEG", "0"))
IES_LM_TO_UMOL_RAW = os.getenv("SPYDR_IES_LM_TO_UMOL", "spectral").strip()
IES_SPD_PATH = Path(
    os.getenv(
        "SPYDR_IES_SPD_PATH",
        str(DEFAULT_IES_SPD_PATH),
    )
)
USE_IES_DIMS = os.getenv("SPYDR_USE_IES_DIMS", "1").strip() != "0"
PROXY_MODE = os.getenv("SPYDR_PROXY_MODE", "0").strip() != "0"
# SPYDR_PPF is treated as fixture/system-level photon flux (umol/s).
# EFF_SCALE is the only scaling applied here (dimmer fraction); this avoids any
# chance of "double derating" from electrical efficiency assumptions.
DERATE = float(os.getenv("EFF_SCALE", "1.0"))
DERATE = clamp_derate(DERATE)

# --- Room / placement ---
_len_ft = float(os.getenv("LENGTH_FT", "12").strip())
_wid_ft = float(os.getenv("WIDTH_FT", "12").strip())
LENGTH_M = _len_ft * 0.3048
WIDTH_M = _wid_ft * 0.3048
LENGTH_M, WIDTH_M = _maybe_swap_dims(LENGTH_M, WIDTH_M)
WALL_MARGIN_M = float(os.getenv("MARGIN_IN", "0").strip()) * 0.0254
EDGE_INSET_M = float(os.getenv("SPYDR_EDGE_INSET_IN", "0").strip()) * 0.0254
MOUNT_Z_M = float(os.getenv("SPYDR_Z_M", "0.4572"))
LAYOUT_MODE_RAW = (
    os.getenv("SPYDR_LAYOUT_MODE", "full").strip().lower().replace("’", "'")
)
TARGET_GAP_X_IN = float(os.getenv("SPYDR_TARGET_GAP_X_IN", "0").strip() or "0")
TARGET_GAP_Y_IN = float(os.getenv("SPYDR_TARGET_GAP_Y_IN", "0").strip() or "0")
ACTUAL_GAP_X_IN = float(os.getenv("SPYDR_ACTUAL_GAP_X_IN", "0").strip() or "0")
ACTUAL_GAP_Y_IN = float(os.getenv("SPYDR_ACTUAL_GAP_Y_IN", "0").strip() or "0")
LAYOUT_MODE = (
    "practical"
    if LAYOUT_MODE_RAW in {"practical", "practical_coverage", "practical coverage"}
    else "full"
)

# Grid of fixtures (MUST be passed in when you generate)
NX = int(os.getenv("NX", "1"))
NY = int(os.getenv("NY", "1"))
ROT_DEG = float(os.getenv("ROT_DEG", "0"))
SUBPATCH = int(os.getenv("SUBPATCH_GRID", "3"))
COMPAT = os.getenv("COMPAT", "0") == "1"

def _reference_bar_width_in(total_width_in: float) -> float:
    return reference_bar_width_in(
        total_width_in, bar_width_raw=BAR_WIDTH_IN_RAW, bars=BARS
    )


def _bar_spacing_in(total_width_in: float, bar_width_in: float) -> float:
    return bar_spacing_in(
        total_width_in,
        bar_width_in,
        bar_spacing_raw=BAR_SPACING_IN_RAW,
        bars=BARS,
    )


def rect_corners(
    ccx: float, ccy: float, lx: float, ly: float, rot_rad: float
) -> list[Point2D]:
    return _geometry_rect_corners(ccx, ccy, lx, ly, rot_rad)


def write_poly(
    fh: TextIO, mat: str, name: str, z: float, corners_face_up: list[Point2D]
) -> None:
    _writer_write_poly(fh, mat, name, z, corners_face_up)


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
) -> None:
    _writer_write_bar(
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
        identifier=radiance_identifier,
    )


def append_bar_layout(
    layout_bars: list[JsonObject],
    cx: float,
    cy: float,
    length_m: float,
    width_m: float,
    rot_rad: float,
) -> None:
    _writer_append_bar_layout(layout_bars, cx, cy, length_m, width_m, rot_rad)


def _read_ies_lines(ies_path: Path) -> list[str]:
    return read_ies_lines(ies_path)


def _ies_metadata_positive_float(lines: list[str], tag: str) -> float | None:
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(tag):
            continue
        parts = stripped.split("]", 1)
        if len(parts) != 2:
            continue
        try:
            val = float(parts[1].strip().split()[0])
        except (IndexError, ValueError):
            continue
        if val > 0:
            return val
    return None


def _ies_total_lumens(lines: list[str]) -> float:
    return ies_total_lumens(lines)


def _ies_tilt_index(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        if line.strip().startswith("TILT="):
            return i
    return None


def _ies_numeric_values_after_tilt(lines: list[str], tilt_idx: int) -> list[float]:
    nums: list[float] = []
    for line in lines[tilt_idx + 1 :]:
        if line.strip().startswith("["):
            continue
        for part in line.split():
            try:
                nums.append(float(part))
            except ValueError:
                continue
    return nums


def _ies_optional_numeric_values(lines: list[str]) -> list[float] | None:
    tilt_idx = _ies_tilt_index(lines)
    if tilt_idx is None:
        return None
    return _ies_numeric_values_after_tilt(lines, tilt_idx)


def _ies_required_numeric_values(lines: list[str]) -> list[float]:
    values = _ies_optional_numeric_values(lines)
    if values is None:
        raise SystemExit("ERROR: IES missing TILT line.")
    if len(values) < 13:
        raise SystemExit("ERROR: IES numeric header is incomplete.")
    return values


def _ies_input_watts(lines: list[str]) -> float | None:
    return ies_input_watts(lines)


def _ies_watts_header_index() -> int:
    idx = 0
    idx += 1  # nlamps
    idx += 1  # lumens per lamp
    idx += 1  # candela multiplier
    idx += 1  # nvert
    idx += 1  # nhorz
    idx += 2  # phot_type, units_type
    idx += 3  # width, length, height
    idx += 2  # bf, blf
    return idx


def _ies_candela_distribution(lines: list[str]) -> IesCandelaDistribution:
    return ies_candela_distribution(lines)


def _trim_redundant_horizontal_angle(
    v_angles: list[float],
    h_angles: list[float],
    candela: list[list[float]],
) -> IesCandelaDistribution:
    return trim_redundant_horizontal_angle(v_angles, h_angles, candela)


def _angle_deltas(angles: list[float], *, wrap_full_circle: bool) -> list[float]:
    return angle_deltas(angles, wrap_full_circle=wrap_full_circle)


def _integrate_candela_lumens(
    theta: list[float],
    phi: list[float],
    dtheta: list[float],
    dphi: list[float],
    candela: list[list[float]],
) -> float:
    return integrate_candela_lumens(theta, phi, dtheta, dphi, candela)


def _ies_integrate_lumens(lines: list[str]) -> float:
    return ies_integrate_lumens(lines)


def _ies_dimensions_m(lines: list[str]) -> tuple[float, float, float] | None:
    return ies_dimensions_m(lines)


def _ies_dimensions_in_from_path(ies_path: Path) -> tuple[float, float, float] | None:
    return ies_dimensions_in_from_path(ies_path)


def _effective_fixture_dims_in() -> tuple[float, float, float]:
    length_in = FIXTURE_LENGTH_IN
    width_in = TOTAL_WIDTH_IN
    height_in = float(BAR_HEIGHT_IN_RAW or f"{DEFAULT_FIXTURE_HEIGHT_IN}")
    if USE_IES_DIMS and IES_PATH.exists():
        dims = _ies_dimensions_in_from_path(IES_PATH)
        if dims:
            w_in, l_in, h_in = dims
            if not FIXTURE_LENGTH_IN_RAW:
                length_in = l_in
            if not TOTAL_WIDTH_IN_RAW:
                width_in = w_in
            if not BAR_HEIGHT_IN_RAW:
                height_in = h_in
    return length_in, width_in, height_in


def _effective_bar_dims_in() -> tuple[float, float, float]:
    fixture_length_in, fixture_width_in, fixture_height_in = (
        _effective_fixture_dims_in()
    )
    length_in = float(BAR_LENGTH_IN_RAW or fixture_length_in)
    width_in = _reference_bar_width_in(fixture_width_in)
    height_in = float(BAR_HEIGHT_IN_RAW or fixture_height_in)
    return length_in, width_in, height_in


def _photopic_v_lambda(wavelength_nm: float) -> float:
    return photopic_v_lambda(wavelength_nm)


def _spd_csv_columns(
    fieldnames: Any, spd_path: Path
) -> tuple[str, str]:
    return spd_csv_columns(fieldnames, spd_path)


def _relative_spd_rows(
    reader: Any, wl_key: str, rel_key: str
) -> list[tuple[float, float]]:
    return relative_spd_rows(reader, wl_key, rel_key)


def _validate_relative_spd_rows(
    rows: list[tuple[float, float]], spd_path: Path
) -> list[tuple[float, float]]:
    return validate_relative_spd_rows(rows, spd_path)


def _load_relative_spd(spd_path: Path) -> list[tuple[float, float]]:
    return load_relative_spd(spd_path)


def _spd_photon_metrics(spd_path: Path) -> RelativeSPDPhotonMetrics:
    return spd_photon_metrics(spd_path)


def _spd_lm_to_umol(spd_path: Path) -> float:
    return spd_lm_to_umol(spd_path)


def _ies_lm_to_umol(lines: list[str], lumens: float) -> tuple[float, str]:
    return ies_lm_to_umol(
        lines=lines,
        lumens=lumens,
        raw=IES_LM_TO_UMOL_RAW,
        spd_path=IES_SPD_PATH,
        ppe_raw=os.getenv("SPYDR_PPE_UMOL_PER_J", "").strip(),
    )


def _scale_ies_rad(rad_path: Path, scale: float) -> None:
    scale_ies_rad(rad_path, scale)


def _polygon_vertices_from_block(
    block_lines: list[str],
) -> list[tuple[float, float, float]]:
    return polygon_vertices_from_block(block_lines)


def _polygon_normal(
    vertices: list[tuple[float, float, float]],
) -> tuple[float, float, float]:
    return polygon_normal(vertices)


def _select_downward_ies_polygon(
    polygon_blocks: list[IesPolygonBlock],
) -> tuple[str, list[tuple[float, float, float]]]:
    return select_downward_ies_polygon(polygon_blocks)


def _parse_ies_brightdata_block(lines: list[str]) -> IesBrightdataBlock:
    return parse_ies_brightdata_block(lines)


def _build_ies_brightdata_block(
    lines: list[str], bright_idx: int, dist_name: str
) -> IesBrightdataBlock:
    from rad_rebuild.radiance.engine.emitters.spydr3_generation.ies_stage import (
        build_ies_brightdata_block,
    )

    return build_ies_brightdata_block(lines, bright_idx, dist_name)


def _parse_ies_light_block(
    lines: list[str], brightdata: IesBrightdataBlock
) -> IesLightBlock:
    return parse_ies_light_block(lines, brightdata)


def _build_ies_light_block(
    lines: list[str], light_idx: int, light_name: str
) -> IesLightBlock:
    from rad_rebuild.radiance.engine.emitters.spydr3_generation.ies_stage import (
        build_ies_light_block,
    )

    return build_ies_light_block(lines, light_idx, light_name)


def _parse_ies_polygon_block(
    lines: list[str], idx: int, light_name: str
) -> tuple[IesPolygonBlock | None, int]:
    return parse_ies_polygon_block(lines, idx, light_name)


def _collect_ies_polygon_blocks(
    lines: list[str], light_block: IesLightBlock
) -> list[IesPolygonBlock]:
    return collect_ies_polygon_blocks(lines, light_block)


def _rewritten_ies_aperture_lines(
    lines: list[str],
    brightdata: IesBrightdataBlock,
    light_block: IesLightBlock,
    aperture_name: str,
    aperture_vertices: list[tuple[float, float, float]],
) -> list[str]:
    return rewritten_ies_aperture_lines(
        lines, brightdata, light_block, aperture_name, aperture_vertices
    )


def _ies_aperture_metadata(
    brightdata: IesBrightdataBlock,
    light_block: IesLightBlock,
    aperture_name: str,
    aperture_vertices: list[tuple[float, float, float]],
    polygon_count: int,
) -> JsonObject:
    return ies_aperture_metadata(
        brightdata, light_block, aperture_name, aperture_vertices, polygon_count
    )


def _rewrite_ies_rad_as_downward_aperture(rad_path: Path) -> JsonObject:
    return rewrite_ies_rad_as_downward_aperture(rad_path)


def _run_ies2rad(ies_path: Path, out_base: str, scale: float) -> Path:
    return run_ies2rad(
        ies_path=ies_path,
        out_base=out_base,
        scale=scale,
        out_dir=OUT_DIR,
        resolve_executable_fn=resolve_executable,
        subprocess_module=subprocess,
        scale_ies_rad_fn=_scale_ies_rad,
    )


def _write_proxy_box(
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
    write_proxy_box(fh, name, cx, cy, z, length_m, width_m, height_m, rot_deg)


def _fixture_half_extents(
    fixture_length_m: float, fixture_width_m: float
) -> tuple[float, float]:
    return fixture_half_extents(fixture_length_m, fixture_width_m)


def _safe_half_spans(
    length_m: float, width_m: float, wall_margin: float, hx: float, hy: float
) -> tuple[float, float]:
    return safe_half_spans(length_m, width_m, wall_margin, hx, hy)


def _evenly_spaced_centers(
    span_m: float, fixture_span_m: float, count: int, wall_margin_m: float
) -> list[float]:
    return evenly_spaced_centers(span_m, fixture_span_m, count, wall_margin_m)


def _fixture_centers(
    fixture_length_m: float, fixture_width_m: float
) -> tuple[list[FixtureCenter], int, int]:
    return fixture_centers(
        length_m=LENGTH_M,
        width_m=WIDTH_M,
        wall_margin_m=WALL_MARGIN_M,
        edge_inset_m=EDGE_INSET_M,
        nx=NX,
        ny=NY,
        fixture_length_m=fixture_length_m,
        fixture_width_m=fixture_width_m,
    )


def get_fixture_positions() -> list[dict[str, float]]:
    fixture_length_in, fixture_width_in, _fixture_height_in = (
        _effective_fixture_dims_in()
    )
    centers, _, _ = _fixture_centers(fixture_length_in * IN2M, fixture_width_in * IN2M)
    return [{"x": cx, "y": cy, "z": MOUNT_Z_M} for (cx, cy) in centers]


def _spydr_fixture_geometry() -> SpydrFixtureGeometry:
    fixture_length_in, fixture_width_in, fixture_height_in = (
        _effective_fixture_dims_in()
    )
    bar_length_in, bar_width_in, bar_height_in = _effective_bar_dims_in()
    fixture_ppe = float(
        os.getenv("SPYDR_PPE_UMOL_PER_J", f"{DEFAULT_FIXTURE_PPE_UMOL_PER_J}")
    )
    if fixture_ppe <= 0.0:
        raise SystemExit("ERROR: SPYDR_PPE_UMOL_PER_J must be > 0.")
    spacing = _bar_spacing_in(fixture_width_in, bar_width_in) * IN2M
    offs = (
        [(i - (BARS - 1) / 2.0) * spacing for i in range(BARS)] if BARS > 0 else [0.0]
    )
    return SpydrFixtureGeometry(
        fixture_length_in=fixture_length_in,
        fixture_width_in=fixture_width_in,
        fixture_height_in=fixture_height_in,
        bar_length_in=bar_length_in,
        bar_width_in=bar_width_in,
        bar_height_in=bar_height_in,
        fixture_length_m=fixture_length_in * IN2M,
        fixture_width_m=fixture_width_in * IN2M,
        fixture_height_m=fixture_height_in * IN2M,
        bar_length_m=bar_length_in * IN2M,
        bar_width_m=bar_width_in * IN2M,
        bar_height_m=bar_height_in * IN2M,
        spacing_m=spacing,
        rotation_rad=math.radians(ROT_DEG),
        bar_offsets_m=offs,
        bar_offsets_in=[off / IN2M for off in offs],
        fixture_ppf_active=PPF_FIXTURE * DERATE,
        fixture_ppe=fixture_ppe,
        fixture_input_w=PPF_FIXTURE / fixture_ppe,
    )


def _spydr_layout_pitch(
    centers: list[FixtureCenter], geometry: SpydrFixtureGeometry
) -> tuple[float | None, float | None, float | None, float | None]:
    unique_xs = sorted({round(cx, 9) for (cx, _cy) in centers})
    unique_ys = sorted({round(cy, 9) for (_cx, cy) in centers})
    pitch_x_in = ((unique_xs[1] - unique_xs[0]) / IN2M) if len(unique_xs) > 1 else None
    pitch_y_in = ((unique_ys[1] - unique_ys[0]) / IN2M) if len(unique_ys) > 1 else None
    actual_gap_x_in = (
        (pitch_x_in - geometry.fixture_length_in) if pitch_x_in is not None else None
    )
    actual_gap_y_in = (
        (pitch_y_in - geometry.fixture_width_in) if pitch_y_in is not None else None
    )
    return pitch_x_in, pitch_y_in, actual_gap_x_in, actual_gap_y_in


def _base_spydr_layout(
    geometry: SpydrFixtureGeometry,
    centers: list[FixtureCenter],
    nx_eff: int,
    ny_eff: int,
) -> JsonObject:
    pitch_x_in, pitch_y_in, actual_gap_x_in, actual_gap_y_in = _spydr_layout_pitch(
        centers, geometry
    )
    return {
        "model": MODEL,
        "model_label": QUBE_IES_LABEL,
        "units": "meters",
        "layout_mode": LAYOUT_MODE,
        "layout_label": "Practical Coverage"
        if LAYOUT_MODE == "practical"
        else "Full Coverage",
        "pitch_x_in": pitch_x_in,
        "pitch_y_in": pitch_y_in,
        "target_gap_x_in": TARGET_GAP_X_IN if LAYOUT_MODE == "practical" else None,
        "target_gap_y_in": TARGET_GAP_Y_IN if LAYOUT_MODE == "practical" else None,
        "actual_gap_x_in": ACTUAL_GAP_X_IN
        if LAYOUT_MODE == "practical"
        else actual_gap_x_in,
        "actual_gap_y_in": ACTUAL_GAP_Y_IN
        if LAYOUT_MODE == "practical"
        else actual_gap_y_in,
        "array_anchor": "centered_even_gap"
        if LAYOUT_MODE == "practical"
        else "centered",
        "nx": int(nx_eff),
        "ny": int(ny_eff),
        "z": MOUNT_Z_M,
        "bars": BARS,
        "rot_deg": ROT_DEG,
        "fixture_length_m": geometry.fixture_length_m,
        "fixture_width_m": geometry.fixture_width_m,
        "fixture_height_m": geometry.fixture_height_m,
        "bar_length_m": geometry.bar_length_m,
        "bar_width_m": geometry.bar_width_m,
        "bar_height_m": geometry.bar_height_m,
        "bar_spacing_m": geometry.spacing_m,
        "bar_offsets_in": geometry.bar_offsets_in,
        "fixture_ppf_anchor_umol_s": PPF_FIXTURE,
        "active_fixture_ppf_umol_s": geometry.fixture_ppf_active,
        "fixture_ppe_umol_j": geometry.fixture_ppe,
        "fixture_input_w_reference": geometry.fixture_input_w,
        "ies_mode": True,
        "ies_file": str(IES_PATH),
        "fixture_emitter_interpretation": "single_whole_fixture_ies_emitter",
        "reference_geometry": "8_bar_non_emitting_overlay_geometry",
        "room": {"L": LENGTH_M, "W": WIDTH_M},
        "fixtures": [],
    }


def _build_spydr_ies_stage(fixture_ppf_active: float) -> SpydrIesStage:
    ies_lines = _read_ies_lines(IES_PATH)
    ies_lumens = _ies_total_lumens(ies_lines)
    if ies_lumens <= 0:
        raise SystemExit("ERROR: IES total lumens is zero or invalid.")
    ies_dims = _ies_dimensions_m(ies_lines)
    spd_metrics = _spd_photon_metrics(IES_SPD_PATH)
    ies_lm_to_umol, ies_lm_to_umol_method = _ies_lm_to_umol(ies_lines, ies_lumens)
    if spd_metrics.par_photon_umol_per_radiant_w <= 0:
        raise SystemExit("ERROR: SPD photon conversion is invalid.")
    ies_baseline_ppf = ies_lumens * ies_lm_to_umol
    if ies_baseline_ppf <= 0:
        raise SystemExit("ERROR: IES lumen-to-photon bridge is invalid.")
    ies_target_radiant_w = (
        fixture_ppf_active / spd_metrics.par_photon_umol_per_radiant_w
    )
    ies_baseline_radiant_w = ies_lumens / spd_metrics.luminous_efficacy_lm_per_radiant_w
    if ies_baseline_radiant_w <= 0:
        raise SystemExit("ERROR: SPD luminous efficacy bridge is invalid.")
    ies_scale = fixture_ppf_active / ies_baseline_ppf
    ies_rad_path = _run_ies2rad(IES_PATH, IES_BASENAME, ies_scale)
    normalize_ies_rad_companion_paths(ies_rad_path)
    ies_source_meta = _rewrite_ies_rad_as_downward_aperture(ies_rad_path)
    normalize_ies_rad_companion_paths(ies_rad_path)
    return SpydrIesStage(
        lines=ies_lines,
        lumens=ies_lumens,
        dims_m=ies_dims,
        spd_metrics=spd_metrics,
        lm_to_umol=ies_lm_to_umol,
        lm_to_umol_method=ies_lm_to_umol_method,
        baseline_ppf=ies_baseline_ppf,
        baseline_radiant_w=ies_baseline_radiant_w,
        target_radiant_w=ies_target_radiant_w,
        scale=ies_scale,
        rad_path=ies_rad_path,
        source_meta=ies_source_meta,
    )


def _apply_spydr_ies_layout_metadata(layout: JsonObject, stage: SpydrIesStage) -> None:
    apply_spydr_ies_layout_metadata(
        layout=layout,
        stage=stage,
        ies_spd_path=IES_SPD_PATH,
        ies_rot_x_deg=IES_ROT_X_DEG,
        ies_rot_z_deg=IES_ROT_Z_DEG,
        default_fixture_ppf_umol_s=DEFAULT_FIXTURE_PPF_UMOL_S,
    )


def _write_spydr_header(
    fh: TextIO, geometry: SpydrFixtureGeometry, stage: SpydrIesStage
) -> None:
    fh.write(
        f"# {QUBE_IES_LABEL} | fixture PPF anchor={PPF_FIXTURE:.0f} umol/s | derate={DERATE:.3f}\n"
    )
    fh.write(
        f"# fixture envelope: L={geometry.fixture_length_m:.4f} m  W={geometry.fixture_width_m:.4f} m  "
        f"H={geometry.fixture_height_m:.4f} m\n"
    )
    fh.write(
        f"# reference bars: count={BARS}  L={geometry.bar_length_m:.4f} m  W={geometry.bar_width_m:.4f} m  "
        f"H={geometry.bar_height_m:.4f} m  spacing={geometry.spacing_m:.4f} m\n"
    )
    fh.write(
        f"# IES: {IES_PATH} | lumens={stage.lumens:.1f} lm | lm_to_umol={stage.lm_to_umol:.6g} | "
        f"baseline_ppf={stage.baseline_ppf:.6f} umol/s | "
        f"spd_umol_per_radiant_w={stage.spd_metrics.par_photon_umol_per_radiant_w:.6f} | "
        f"scale={stage.scale:.6g}\n"
    )
    fh.write(
        "# whole-fixture interpretation: one active IES emitter per fixture center; "
        "8-bar geometry is reference-only for overlays / footprint documentation\n"
    )
    fh.write(
        "# active Radiance source geometry: single downward flatcorr aperture "
        f"(replacing the original {stage.source_meta['box_face_count']}-face ies2rad box emitter)\n"
    )
    fh.write(f"# IES conversion: {stage.lm_to_umol_method}\n")
    fh.write("# NOTE: IES profile assumed to emit DOWN (-Z)\n\n")


def _spydr_fixture_record(
    cx: float, cy: float, geometry: SpydrFixtureGeometry
) -> JsonObject:
    return spydr_fixture_record(cx, cy, geometry)


def _write_spydr_fixture(
    fh: TextIO,
    cx: float,
    cy: float,
    geometry: SpydrFixtureGeometry,
    stage: SpydrIesStage,
) -> None:
    write_spydr_fixture(
        fh,
        cx,
        cy,
        geometry,
        stage,
        mount_z_m=MOUNT_Z_M,
        rot_deg=ROT_DEG,
        ies_rot_x_deg=IES_ROT_X_DEG,
        ies_rot_z_deg=IES_ROT_Z_DEG,
        proxy_mode=PROXY_MODE,
        identifier=radiance_identifier,
    )


def _write_spydr_rad(
    out: Path,
    centers: list[FixtureCenter],
    geometry: SpydrFixtureGeometry,
    stage: SpydrIesStage,
    layout: JsonObject,
) -> None:
    with out.open("w") as fh:
        _write_spydr_header(fh, geometry, stage)
        if PROXY_MODE:
            fh.write("void plastic spydr3_proxy\n0\n0\n5 0 0 0 0 0\n\n")
        for cx, cy in centers:
            _write_spydr_fixture(fh, cx, cy, geometry, stage)
            layout["fixtures"].append(_spydr_fixture_record(cx, cy, geometry))


def _spydr_summary_detail_line(stage: SpydrIesStage) -> str:
    return spydr_summary_detail_line(
        stage=stage,
        model_label=QUBE_IES_LABEL,
        ies_path=IES_PATH,
        ies_spd_path=IES_SPD_PATH,
        ppf_fixture=PPF_FIXTURE,
        ies_rot_x_deg=IES_ROT_X_DEG,
        ies_rot_z_deg=IES_ROT_Z_DEG,
    )


def _write_spydr_summary(
    layout: JsonObject, geometry: SpydrFixtureGeometry, stage: SpydrIesStage
) -> None:
    (OUT_DIR / "spydr3_summary.txt").write_text(
        build_spydr_summary_text(
            layout=layout,
            geometry=geometry,
            stage=stage,
            model_label=QUBE_IES_LABEL,
            bars=BARS,
            ppf_fixture=PPF_FIXTURE,
            derate=DERATE,
            ies_path=IES_PATH,
            ies_spd_path=IES_SPD_PATH,
            ies_rot_x_deg=IES_ROT_X_DEG,
            ies_rot_z_deg=IES_ROT_Z_DEG,
        )
    )
    (OUT_DIR / "spydr3_layout.json").write_text(json.dumps(layout, indent=2))


def _print_spydr_completion(
    out: Path, layout: JsonObject, geometry: SpydrFixtureGeometry, stage: SpydrIesStage
) -> None:
    for line in spydr_completion_lines(
        out=out,
        geometry=geometry,
        stage=stage,
        ppf_fixture=PPF_FIXTURE,
    ):
        print(line)


def main() -> None:
    geometry = _spydr_fixture_geometry()
    out = OUT_DIR / (
        "emitters_smd_ALL_umol.rad" if COMPAT else "emitters_spydr3_ALL_umol.rad"
    )
    centers, nx_eff, ny_eff = _fixture_centers(
        geometry.fixture_length_m, geometry.fixture_width_m
    )
    layout = _base_spydr_layout(geometry, centers, nx_eff, ny_eff)
    stage = _build_spydr_ies_stage(geometry.fixture_ppf_active)
    _apply_spydr_ies_layout_metadata(layout, stage)
    _write_spydr_rad(out, centers, geometry, stage, layout)
    _write_spydr_summary(layout, geometry, stage)
    _print_spydr_completion(out, layout, geometry, stage)


if __name__ == "__main__":
    main()
