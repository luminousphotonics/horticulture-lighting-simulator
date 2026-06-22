from __future__ import annotations

import csv
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rad_rebuild.radiance.engine.emitters.spydr3_generation.config import FT2M, IN2M
from rad_rebuild.radiance.executables import ExecutableResolutionError

JsonObject = dict[str, Any]
IesPolygonBlock = tuple[str, list[str], list[tuple[float, float, float]]]

PLANCK_J_S = 6.62607015e-34
LIGHT_M_PER_S = 299_792_458.0
AVOGADRO = 6.02214076e23
LUMINOUS_EFFICACY_LM_PER_W = 683.0
PAR_MIN_NM = 400.0
PAR_MAX_NM = 700.0
PHOTON_UMOL_PER_J_NM = (1e6 * 1e-9) / (AVOGADRO * PLANCK_J_S * LIGHT_M_PER_S)


@dataclass(frozen=True)
class IesCandelaDistribution:
    vertical_angles: list[float]
    horizontal_angles: list[float]
    candela: list[list[float]]


@dataclass(frozen=True)
class IesBrightdataBlock:
    index: int
    dist_name: str
    data_file: str
    cal_file: str
    phi_expr: str
    theta_expr: str
    source_scale: str


@dataclass(frozen=True)
class IesLightBlock:
    index: int
    light_name: str
    block_lines: list[str]


@dataclass(frozen=True)
class RelativeSPDPhotonMetrics:
    spd_path: str
    sample_count: int
    total_relative_power_area: float
    par_relative_power_area: float
    par_fraction_of_total_power: float
    par_centroid_nm: float
    luminous_efficacy_lm_per_radiant_w: float
    par_photon_umol_per_radiant_w: float
    umol_per_lumen: float


@dataclass(frozen=True)
class SpydrIesStage:
    lines: list[str]
    lumens: float
    dims_m: tuple[float, float, float] | None
    spd_metrics: RelativeSPDPhotonMetrics
    lm_to_umol: float
    lm_to_umol_method: str
    baseline_ppf: float
    baseline_radiant_w: float
    target_radiant_w: float
    scale: float
    rad_path: Path
    source_meta: JsonObject


_IES_LINES_CACHE: dict[str, list[str]] = {}
_IES_DIMS_CACHE: dict[str, tuple[float, float, float] | None] = {}
_SPD_METRICS_CACHE: dict[str, RelativeSPDPhotonMetrics] = {}


def read_ies_lines(ies_path: Path) -> list[str]:
    key = str(ies_path.resolve())
    if key in _IES_LINES_CACHE:
        return _IES_LINES_CACHE[key]
    try:
        lines = ies_path.read_text(errors="ignore").splitlines()
        _IES_LINES_CACHE[key] = lines
        return lines
    except OSError as exc:
        raise SystemExit(f"ERROR: failed to read IES file {ies_path}: {exc}") from exc


def ies_total_lumens(lines: list[str]) -> float:
    metadata_lumens = _ies_metadata_positive_float(lines, "[_TOTALLUMINAIRELUMENS]")
    if metadata_lumens is not None:
        return metadata_lumens
    return ies_integrate_lumens(lines)


def ies_input_watts(lines: list[str]) -> float | None:
    metadata_watts = _ies_metadata_positive_float(lines, "[_INPUTWATTAGE]")
    if metadata_watts is not None:
        return metadata_watts
    nums = _ies_optional_numeric_values(lines)
    if nums is None or len(nums) < 13:
        return None

    idx = _ies_watts_header_index()
    watts = nums[idx] if idx < len(nums) else None
    if watts and watts > 0:
        return float(watts)
    return None


def ies_dimensions_m(lines: list[str]) -> tuple[float, float, float] | None:
    nums = _ies_optional_numeric_values(lines)
    if nums is None or len(nums) < 13:
        return None

    idx = 0
    idx += 1
    idx += 1
    idx += 1
    idx += 1
    idx += 1
    idx += 1
    units_type = int(nums[idx])
    idx += 1
    width = nums[idx]
    length = nums[idx + 1]
    height = nums[idx + 2]

    if units_type == 1:
        return (width * FT2M, length * FT2M, height * FT2M)
    if units_type == 2:
        return (width, length, height)
    return (width * FT2M, length * FT2M, height * FT2M)


def ies_dimensions_in_from_path(ies_path: Path) -> tuple[float, float, float] | None:
    key = str(ies_path.resolve())
    if key in _IES_DIMS_CACHE:
        dims = _IES_DIMS_CACHE[key]
    else:
        dims = ies_dimensions_m(read_ies_lines(ies_path))
        _IES_DIMS_CACHE[key] = dims
    if not dims:
        return None
    w_m, l_m, h_m = dims
    return (w_m / IN2M, l_m / IN2M, h_m / IN2M)


def spd_photon_metrics(spd_path: Path) -> RelativeSPDPhotonMetrics:
    key = str(spd_path.resolve())
    cached = _SPD_METRICS_CACHE.get(key)
    if cached is not None:
        return cached

    rows = load_relative_spd(spd_path)
    total_power = 0.0
    par_power = 0.0
    luminous_weight = 0.0
    par_lambda_weight = 0.0

    for (wl0, rel0), (wl1, rel1) in zip(rows, rows[1:]):
        if wl1 <= wl0:
            continue
        dw = wl1 - wl0
        total_power += 0.5 * (rel0 + rel1) * dw
        luminous_weight += (
            0.5 * (rel0 * photopic_v_lambda(wl0) + rel1 * photopic_v_lambda(wl1)) * dw
        )

        seg_lo = max(wl0, PAR_MIN_NM)
        seg_hi = min(wl1, PAR_MAX_NM)
        if seg_hi <= seg_lo:
            continue
        slope = (rel1 - rel0) / dw
        rel_lo = rel0 + slope * (seg_lo - wl0)
        rel_hi = rel0 + slope * (seg_hi - wl0)
        par_power += 0.5 * (rel_lo + rel_hi) * (seg_hi - seg_lo)
        par_lambda_weight += (
            0.5 * (seg_lo * rel_lo + seg_hi * rel_hi) * (seg_hi - seg_lo)
        )

    if (
        total_power <= 0.0
        or luminous_weight <= 0.0
        or par_lambda_weight <= 0.0
        or par_power <= 0.0
    ):
        raise SystemExit(
            f"ERROR: SPD CSV {spd_path} produced an invalid spectral conversion integral."
        )

    umol_per_radiant_w = PHOTON_UMOL_PER_J_NM * (par_lambda_weight / total_power)
    lm_per_radiant_w = LUMINOUS_EFFICACY_LM_PER_W * (luminous_weight / total_power)
    if lm_per_radiant_w <= 0.0:
        raise SystemExit(
            f"ERROR: SPD CSV {spd_path} produced a non-positive luminous efficacy."
        )

    metrics = RelativeSPDPhotonMetrics(
        spd_path=str(spd_path.resolve()),
        sample_count=len(rows),
        total_relative_power_area=total_power,
        par_relative_power_area=par_power,
        par_fraction_of_total_power=par_power / total_power,
        par_centroid_nm=par_lambda_weight / par_power,
        luminous_efficacy_lm_per_radiant_w=lm_per_radiant_w,
        par_photon_umol_per_radiant_w=umol_per_radiant_w,
        umol_per_lumen=umol_per_radiant_w / lm_per_radiant_w,
    )
    _SPD_METRICS_CACHE[key] = metrics
    return metrics


def spd_lm_to_umol(spd_path: Path) -> float:
    return spd_photon_metrics(spd_path).umol_per_lumen


def ies_lm_to_umol(
    *,
    lines: list[str],
    lumens: float,
    raw: str,
    spd_path: Path,
    ppe_raw: str,
) -> tuple[float, str]:
    active_raw = raw or "spectral"
    mode = active_raw.lower()
    if mode in {"spectral", "spd", "vps6", "surrogate"}:
        factor = spd_lm_to_umol(spd_path)
        return (
            factor,
            "digitized relative SPD "
            f"({spd_path.name}, wavelength-weighted PAR/lumen)",
        )
    if mode == "auto":
        watts = ies_input_watts(lines)
        try:
            ppe = float(ppe_raw)
        except ValueError:
            ppe = 0.0
        if watts and ppe and lumens > 0:
            return ppe / (lumens / watts), "IES watts + SPYDR_PPE_UMOL_PER_J override"
        factor = spd_lm_to_umol(spd_path)
        return (
            factor,
            "digitized relative SPD fallback "
            f"({spd_path.name}, wavelength-weighted PAR/lumen)",
        )
    try:
        val = float(active_raw)
    except ValueError as exc:
        raise SystemExit(f"ERROR: invalid SPYDR_IES_LM_TO_UMOL={active_raw!r}") from exc
    if val <= 0:
        raise SystemExit("ERROR: SPYDR_IES_LM_TO_UMOL must be > 0.")
    return val, "explicit SPYDR_IES_LM_TO_UMOL override"


def scale_ies_rad(rad_path: Path, scale: float) -> None:
    if abs(scale - 1.0) < 1e-12:
        return
    lines = rad_path.read_text().splitlines()
    rad_path.write_text("\n".join(_scaled_ies_rad_lines(lines, scale)) + "\n")


def _scaled_ies_rad_lines(lines: list[str], scale: float) -> list[str]:
    out = []
    in_light = False
    for line in lines:
        out_line, in_light = _scaled_ies_rad_line(line, scale, in_light)
        out.append(out_line)
    return out


def _scaled_ies_rad_line(line: str, scale: float, in_light: bool) -> tuple[str, bool]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return line, in_light
    tokens = stripped.split()
    if len(tokens) >= 3 and tokens[1] == "light":
        return line, True
    if not in_light:
        return line, False
    return _scaled_light_continuation(tokens, line, scale)


def _scaled_light_continuation(
    tokens: list[str], fallback: str, scale: float
) -> tuple[str, bool]:
    if tokens[0] == "3" and len(tokens) >= 4:
        return _scaled_light_rgb_line(tokens, fallback, scale), False
    if tokens[0] not in {"0", "3"}:
        return fallback, False
    return fallback, True


def _scaled_light_rgb_line(tokens: list[str], fallback: str, scale: float) -> str:
    try:
        r = float(tokens[1]) * scale
        g = float(tokens[2]) * scale
        b = float(tokens[3]) * scale
    except ValueError:
        return fallback
    return f"3 {r:.6f} {g:.6f} {b:.6f}"


def rewrite_ies_rad_as_downward_aperture(rad_path: Path) -> JsonObject:
    lines = rad_path.read_text().splitlines()
    brightdata = parse_ies_brightdata_block(lines)
    light_block = parse_ies_light_block(lines, brightdata)
    polygon_blocks = collect_ies_polygon_blocks(lines, light_block)
    aperture_name, aperture_vertices = select_downward_ies_polygon(polygon_blocks)
    rewritten = rewritten_ies_aperture_lines(
        lines,
        brightdata,
        light_block,
        aperture_name,
        aperture_vertices,
    )
    rad_path.write_text("\n".join(rewritten))
    return ies_aperture_metadata(
        brightdata,
        light_block,
        aperture_name,
        aperture_vertices,
        len(polygon_blocks),
    )


def run_ies2rad(
    *,
    ies_path: Path,
    out_base: str,
    scale: float,
    out_dir: Path,
    resolve_executable_fn: Callable[[str], Path],
    subprocess_module: Any,
    scale_ies_rad_fn: Callable[[Path, float], None],
) -> Path:
    if not ies_path.exists():
        raise SystemExit(f"ERROR: IES file not found: {ies_path}")
    try:
        ies2rad = resolve_executable_fn("ies2rad")
    except ExecutableResolutionError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    cmd = [str(ies2rad), "-o", out_base, "-m", f"{scale:.8f}", str(ies_path.resolve())]
    try:
        subprocess_module.run(cmd, cwd=out_dir, check=True)  # nosec B603
    except subprocess_module.CalledProcessError:
        print(
            "NOTE: ies2rad -m failed; retrying without scaling and post-scaling the RAD file."
        )
        subprocess_module.run(  # nosec B603
            [str(ies2rad), "-o", out_base, str(ies_path.resolve())],
            cwd=out_dir,
            check=True,
        )
        scale_ies_rad_fn(out_dir / f"{out_base}.rad", scale)

    return out_dir / f"{out_base}.rad"


def load_relative_spd(spd_path: Path) -> list[tuple[float, float]]:
    try:
        with spd_path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            wl_key, rel_key = spd_csv_columns(reader.fieldnames, spd_path)
            rows = relative_spd_rows(reader, wl_key, rel_key)
    except OSError as exc:
        raise SystemExit(f"ERROR: failed to read SPD CSV {spd_path}: {exc}") from exc
    except ValueError as exc:
        raise SystemExit(f"ERROR: invalid SPD CSV data in {spd_path}: {exc}") from exc
    return validate_relative_spd_rows(rows, spd_path)


def photopic_v_lambda(wavelength_nm: float) -> float:
    t1 = (wavelength_nm - 568.8) * (0.0213 if wavelength_nm < 568.8 else 0.0247)
    t2 = (wavelength_nm - 530.9) * (0.0613 if wavelength_nm < 530.9 else 0.0322)
    return 0.821 * math.exp(-0.5 * t1 * t1) + 0.286 * math.exp(-0.5 * t2 * t2)


def spd_csv_columns(
    fieldnames: Sequence[str] | None, spd_path: Path
) -> tuple[str, str]:
    field_map = {str(name).strip().lower(): str(name) for name in (fieldnames or [])}
    wl_key = field_map.get("wavelength_nm") or field_map.get("wavelength")
    rel_key = (
        field_map.get("relative_spd")
        or field_map.get("relative_intensity_normalized")
        or field_map.get("relative_intensity")
    )
    if wl_key is None or rel_key is None:
        raise SystemExit(
            f"ERROR: SPD CSV {spd_path} must contain a wavelength column "
            "('wavelength_nm') and a relative spectrum column "
            "('relative_spd' or 'relative_intensity_normalized')."
        )
    return wl_key, rel_key


def relative_spd_rows(
    reader: csv.DictReader[str], wl_key: str, rel_key: str
) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    for row in reader:
        wl_raw = row.get(wl_key)
        rel_raw = row.get(rel_key)
        if wl_raw is None or rel_raw is None:
            continue
        wl = float(wl_raw)
        rel = max(float(rel_raw), 0.0)
        rows.append((wl, rel))
    return rows


def validate_relative_spd_rows(
    rows: list[tuple[float, float]], spd_path: Path
) -> list[tuple[float, float]]:
    rows.sort(key=lambda item: item[0])
    if len(rows) < 2:
        raise SystemExit(
            f"ERROR: SPD CSV {spd_path} must contain at least two samples."
        )
    if sum(rel for _wl, rel in rows) <= 0.0:
        raise SystemExit(
            f"ERROR: SPD CSV {spd_path} contains no positive relative intensity values."
        )
    return rows


def ies_integrate_lumens(lines: list[str]) -> float:
    distribution = ies_candela_distribution(lines)
    theta = [math.radians(v) for v in distribution.vertical_angles]
    phi = [math.radians(h) for h in distribution.horizontal_angles]
    if not theta or not phi:
        raise SystemExit("ERROR: IES angles missing.")
    dtheta = angle_deltas(theta, wrap_full_circle=False)
    dphi = angle_deltas(phi, wrap_full_circle=True)
    return integrate_candela_lumens(
        theta,
        phi,
        dtheta,
        dphi,
        distribution.candela,
    )


def ies_candela_distribution(lines: list[str]) -> IesCandelaDistribution:
    nums = _ies_required_numeric_values(lines)
    candela_multiplier = nums[2]
    nvert = int(nums[3])
    nhorz = int(nums[4])
    idx = _ies_watts_header_index() + 1
    v_angles = nums[idx : idx + nvert]
    idx += nvert
    h_angles = nums[idx : idx + nhorz]
    idx += nhorz
    expected = nvert * nhorz
    candela_vals = nums[idx : idx + expected]
    if len(candela_vals) < expected:
        raise SystemExit("ERROR: IES candela table is incomplete.")
    candela = []
    for hi in range(nhorz):
        row = candela_vals[hi * nvert : (hi + 1) * nvert]
        candela.append([v * candela_multiplier for v in row])
    return trim_redundant_horizontal_angle(v_angles, h_angles, candela)


def trim_redundant_horizontal_angle(
    v_angles: list[float],
    h_angles: list[float],
    candela: list[list[float]],
) -> IesCandelaDistribution:
    if h_angles and abs(h_angles[-1] - 360.0) < 1e-6:
        h_angles = h_angles[:-1]
        candela = candela[:-1]
    return IesCandelaDistribution(
        vertical_angles=v_angles,
        horizontal_angles=h_angles,
        candela=candela,
    )


def angle_deltas(angles: list[float], *, wrap_full_circle: bool) -> list[float]:
    count = len(angles)
    if count == 1:
        return [2.0 * math.pi if wrap_full_circle else math.pi]

    deltas = [0.0] * count
    deltas[0] = (angles[1] - angles[0]) / 2.0
    deltas[-1] = (angles[-1] - angles[-2]) / 2.0
    for i in range(1, count - 1):
        deltas[i] = (angles[i + 1] - angles[i - 1]) / 2.0
    if wrap_full_circle:
        deltas[0] += (2.0 * math.pi - angles[-1]) / 2.0
        deltas[-1] += (angles[0] + 2.0 * math.pi - angles[-2]) / 2.0
    return deltas


def integrate_candela_lumens(
    theta: list[float],
    phi: list[float],
    dtheta: list[float],
    dphi: list[float],
    candela: list[list[float]],
) -> float:
    lumens = 0.0
    for hi in range(len(phi)):
        for vi in range(len(theta)):
            lumens += candela[hi][vi] * math.sin(theta[vi]) * dtheta[vi] * dphi[hi]
    return max(lumens, 0.0)


def polygon_vertices_from_block(
    block_lines: list[str],
) -> list[tuple[float, float, float]]:
    if len(block_lines) < 5:
        raise SystemExit("ERROR: IES polygon block is incomplete.")
    count_tokens = block_lines[3].split()
    if not count_tokens:
        raise SystemExit("ERROR: IES polygon coordinate count is missing.")
    try:
        coord_count = int(float(count_tokens[0]))
    except ValueError as exc:
        raise SystemExit("ERROR: invalid IES polygon coordinate count.") from exc
    if coord_count <= 0 or coord_count % 3 != 0:
        raise SystemExit("ERROR: invalid IES polygon coordinate count.")
    coords = _polygon_coords(block_lines[4:])
    if len(coords) < coord_count:
        raise SystemExit("ERROR: IES polygon coordinate list is incomplete.")
    return [(coords[i], coords[i + 1], coords[i + 2]) for i in range(0, coord_count, 3)]


def polygon_normal(
    vertices: list[tuple[float, float, float]],
) -> tuple[float, float, float]:
    if len(vertices) < 3:
        return (0.0, 0.0, 0.0)
    (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = vertices[:3]
    ux, uy, uz = x1 - x0, y1 - y0, z1 - z0
    vx, vy, vz = x2 - x0, y2 - y0, z2 - z0
    return (
        uy * vz - uz * vy,
        uz * vx - ux * vz,
        ux * vy - uy * vx,
    )


def select_downward_ies_polygon(
    polygon_blocks: list[IesPolygonBlock],
) -> tuple[str, list[tuple[float, float, float]]]:
    best_name = ""
    best_vertices: list[tuple[float, float, float]] | None = None
    best_z = float("inf")
    for name, _block_lines, vertices in polygon_blocks:
        z_values = [vertex[2] for vertex in vertices]
        if max(z_values) - min(z_values) > 1e-6:
            continue
        avg_z = sum(z_values) / len(z_values)
        if avg_z < best_z:
            best_z = avg_z
            best_name = name
            best_vertices = vertices
    if best_vertices is None:
        raise SystemExit(
            "ERROR: failed to find a downward emitting face in the ies2rad output."
        )
    _nx, _ny, nz = polygon_normal(best_vertices)
    if nz > 0:
        best_vertices = list(reversed(best_vertices))
    return best_name, best_vertices


def parse_ies_brightdata_block(lines: list[str]) -> IesBrightdataBlock:
    for idx, line in enumerate(lines):
        tokens = line.split()
        if len(tokens) >= 3 and tokens[1] == "brightdata":
            return build_ies_brightdata_block(lines, idx, tokens[2])
    raise SystemExit("ERROR: ies2rad output missing brightdata block.")


def build_ies_brightdata_block(
    lines: list[str], bright_idx: int, dist_name: str
) -> IesBrightdataBlock:
    if bright_idx + 3 >= len(lines):
        raise SystemExit("ERROR: ies2rad brightdata block is incomplete.")
    func_tokens = lines[bright_idx + 1].split()
    arg_tokens = lines[bright_idx + 3].split()
    if len(func_tokens) < 6:
        raise SystemExit("ERROR: ies2rad brightdata function line is incomplete.")
    if len(arg_tokens) < 2:
        raise SystemExit("ERROR: ies2rad brightdata argument line is incomplete.")
    return IesBrightdataBlock(
        index=bright_idx,
        dist_name=dist_name,
        data_file=func_tokens[2],
        cal_file=func_tokens[3],
        phi_expr=func_tokens[4],
        theta_expr=func_tokens[5],
        source_scale=arg_tokens[1],
    )


def parse_ies_light_block(
    lines: list[str], brightdata: IesBrightdataBlock
) -> IesLightBlock:
    for idx in range(brightdata.index + 4, len(lines)):
        tokens = lines[idx].split()
        if (
            len(tokens) >= 3
            and tokens[0] == brightdata.dist_name
            and tokens[1] == "light"
        ):
            return build_ies_light_block(lines, idx, tokens[2])
    raise SystemExit("ERROR: ies2rad output missing light block.")


def build_ies_light_block(
    lines: list[str], light_idx: int, light_name: str
) -> IesLightBlock:
    if light_idx + 3 >= len(lines):
        raise SystemExit("ERROR: ies2rad light block is incomplete.")
    return IesLightBlock(
        index=light_idx,
        light_name=light_name,
        block_lines=lines[light_idx : light_idx + 4],
    )


def parse_ies_polygon_block(
    lines: list[str], idx: int, light_name: str
) -> tuple[IesPolygonBlock | None, int]:
    tokens = lines[idx].split()
    if len(tokens) < 3 or tokens[0] != light_name or tokens[1] != "polygon":
        return None, idx + 1
    poly_name = tokens[2]
    if idx + 3 >= len(lines):
        raise SystemExit("ERROR: ies2rad polygon block is incomplete.")
    count_tokens = lines[idx + 3].split()
    if not count_tokens:
        raise SystemExit("ERROR: ies2rad polygon coordinate count is missing.")
    try:
        coord_count = int(float(count_tokens[0]))
    except ValueError as exc:
        raise SystemExit("ERROR: invalid ies2rad polygon coordinate count.") from exc
    vertex_line_count = coord_count // 3
    block_end = idx + 4 + vertex_line_count
    block_lines = lines[idx:block_end]
    return (
        poly_name,
        block_lines,
        polygon_vertices_from_block(block_lines),
    ), block_end


def collect_ies_polygon_blocks(
    lines: list[str], light_block: IesLightBlock
) -> list[IesPolygonBlock]:
    polygon_blocks: list[IesPolygonBlock] = []
    idx = light_block.index + 4
    while idx < len(lines):
        polygon_block, idx = parse_ies_polygon_block(lines, idx, light_block.light_name)
        if polygon_block is not None:
            polygon_blocks.append(polygon_block)
    if not polygon_blocks:
        raise SystemExit("ERROR: ies2rad output contains no luminous polygons.")
    return polygon_blocks


def rewritten_ies_aperture_lines(
    lines: list[str],
    brightdata: IesBrightdataBlock,
    light_block: IesLightBlock,
    aperture_name: str,
    aperture_vertices: list[tuple[float, float, float]],
) -> list[str]:
    header_lines = lines[: brightdata.index]
    rewritten: list[str] = list(header_lines)
    if rewritten and rewritten[-1].strip():
        rewritten.append("")
    rewritten.extend(
        [
            f"void brightdata {brightdata.dist_name}",
            "5 flatcorr "
            f"{brightdata.data_file} {brightdata.cal_file} "
            f"{brightdata.phi_expr} {brightdata.theta_expr}",
            "0",
            f"1 {brightdata.source_scale}",
            "",
        ]
    )
    rewritten.extend(light_block.block_lines)
    rewritten.append("")
    rewritten.extend(
        [
            f"{light_block.light_name} polygon {aperture_name}",
            "0",
            "0",
            f"{len(aperture_vertices) * 3}",
        ]
    )
    for x, y, z in aperture_vertices:
        rewritten.append(f"\t{x:.6f}\t{y:.6f}\t{z:.6f}")
    rewritten.append("")
    return rewritten


def ies_aperture_metadata(
    brightdata: IesBrightdataBlock,
    light_block: IesLightBlock,
    aperture_name: str,
    aperture_vertices: list[tuple[float, float, float]],
    polygon_count: int,
) -> JsonObject:
    xs = [vertex[0] for vertex in aperture_vertices]
    ys = [vertex[1] for vertex in aperture_vertices]
    zs = [vertex[2] for vertex in aperture_vertices]
    return {
        "modifier": brightdata.dist_name,
        "light": light_block.light_name,
        "geometry": "single_downward_flatcorr_aperture",
        "aperture_name": aperture_name,
        "aperture_length_m": max(xs) - min(xs),
        "aperture_width_m": max(ys) - min(ys),
        "aperture_z_m": sum(zs) / len(zs),
        "faces_removed": polygon_count - 1,
        "box_face_count": polygon_count,
    }


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


def _ies_watts_header_index() -> int:
    idx = 0
    idx += 1
    idx += 1
    idx += 1
    idx += 1
    idx += 1
    idx += 2
    idx += 3
    idx += 2
    return idx


def _polygon_coords(block_lines: list[str]) -> list[float]:
    coords: list[float] = []
    for line in block_lines:
        for token in line.split():
            try:
                coords.append(float(token))
            except ValueError as exc:
                raise SystemExit(
                    "ERROR: invalid numeric value in IES polygon block."
                ) from exc
    return coords
