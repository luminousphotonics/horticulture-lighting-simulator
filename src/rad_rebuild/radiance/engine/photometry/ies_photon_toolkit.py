from __future__ import annotations

import csv
import math
import shutil
import subprocess  # nosec B404
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray


IN2M = 0.0254
FT2M = IN2M * 12.0

PLANCK_J_S = 6.62607015e-34
LIGHT_M_PER_S = 299_792_458.0
AVOGADRO = 6.02214076e23
LUMINOUS_EFFICACY_LM_PER_W = 683.0
PAR_MIN_NM = 400.0
PAR_MAX_NM = 700.0
PHOTON_UMOL_PER_J_NM = (1e6 * 1e-9) / (AVOGADRO * PLANCK_J_S * LIGHT_M_PER_S)
FloatArray = NDArray[np.float64]
SpdSamples = list[tuple[float, float]]


# Bandit B404/B603 waiver: run_ies2rad invokes the fixed Radiance ies2rad
# executable with argv lists and no shell. Owner: Phase 12.5C.2. Review before
# 2026-09-30.


class PhotometryParseError(ValueError):
    """Base class for malformed scientific photometry input."""


class IesParseError(PhotometryParseError):
    """Raised when an IES file is missing required numeric structure."""


class SpdParseError(PhotometryParseError):
    """Raised when an SPD CSV is malformed or physically unusable."""


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
class IesNumericHeader:
    lamp_count: int
    lumens_per_lamp: float
    candela_multiplier: float
    nvert: int
    nhorz: int
    phot_type: int
    units_type: int
    width_raw: float
    length_raw: float
    height_raw: float
    ballast_factor: float
    ballast_lamp_factor: float
    input_watts: float | None
    width_m: float
    length_m: float
    height_m: float


@dataclass(frozen=True)
class IesHeaderSummary:
    ies_path: str
    lamp_count: int
    lumens_per_lamp: float
    total_luminaire_lumens: float
    input_watts: float | None
    width_m: float
    length_m: float
    height_m: float


@dataclass(frozen=True)
class BrightDataSource:
    index: int
    modifier: str
    correction_function: str
    data_file: str
    cal_file: str
    phi_expr: str
    theta_expr: str
    source_scale: str


@dataclass(frozen=True)
class LightSourceBlock:
    index: int
    kind: str
    name: str


@dataclass(frozen=True)
class RadPrimitiveBlocks:
    primitive_types: list[str]
    primitive_names: list[str]
    polygon_blocks: list[tuple[str, list[tuple[float, float, float]]]]
    sphere_blocks: list[tuple[str, tuple[float, float, float], float]]


@dataclass(frozen=True)
class RewritePrimitiveBlocks:
    polygon_blocks: list[tuple[str, list[str], list[tuple[float, float, float]]]]
    sphere_blocks: list[tuple[str, tuple[float, float, float], float]]
    primitive_count: int


@dataclass(frozen=True)
class ApertureGeometry:
    name: str
    vertices: list[tuple[float, float, float]]
    raw_geometry: str
    manual_aperture: bool


def read_ies_lines(ies_path: Path) -> list[str]:
    try:
        return ies_path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise IesParseError(
            f"IES file {ies_path} is not valid UTF-8 text: {exc}"
        ) from exc
    except OSError as exc:
        raise IesParseError(f"failed to read IES file {ies_path}: {exc}") from exc


def _ies_numeric_values(lines: list[str]) -> list[float]:
    tilt_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("TILT="):
            tilt_idx = i
            break
    if tilt_idx is None:
        raise IesParseError("IES missing TILT line.")

    nums: list[float] = []
    for line in lines[tilt_idx + 1 :]:
        if line.strip().startswith("["):
            continue
        for part in line.split():
            try:
                value = float(part)
            except ValueError:
                raise IesParseError(
                    f"invalid IES numeric token after TILT line: {part!r}"
                ) from None
            if not math.isfinite(value):
                raise IesParseError(
                    f"non-finite IES numeric token after TILT line: {part!r}"
                )
            nums.append(value)
    return nums


def _positive_int(value: float, name: str) -> int:
    if not float(value).is_integer():
        raise IesParseError(f"IES {name} must be an integer.")
    result = int(value)
    if result <= 0:
        raise IesParseError(f"IES {name} must be positive.")
    return result


def parse_ies_numeric_header(lines: list[str]) -> IesNumericHeader:
    nums = _ies_numeric_values(lines)
    if len(nums) < 13:
        raise IesParseError("IES numeric header is incomplete.")

    idx = 0
    lamp_count = _positive_int(nums[idx], "lamp count")
    idx += 1
    lumens_per_lamp = nums[idx]
    idx += 1
    candela_multiplier = nums[idx]
    idx += 1
    nvert = _positive_int(nums[idx], "vertical angle count")
    idx += 1
    nhorz = _positive_int(nums[idx], "horizontal angle count")
    idx += 1
    phot_type = _positive_int(nums[idx], "photometric type")
    idx += 1
    units_type = _positive_int(nums[idx], "units type")
    idx += 1
    width_raw = nums[idx]
    idx += 1
    length_raw = nums[idx]
    idx += 1
    height_raw = nums[idx]
    idx += 1
    ballast_factor = nums[idx]
    idx += 1
    ballast_lamp_factor = nums[idx]
    idx += 1
    input_watts = nums[idx] if idx < len(nums) and nums[idx] > 0 else None

    if lumens_per_lamp == 0.0 or lumens_per_lamp < -1.0:
        raise IesParseError("IES lumens per lamp must be positive or the -1 sentinel.")
    if candela_multiplier <= 0.0:
        raise IesParseError("IES candela multiplier must be positive.")
    if phot_type not in {1, 2, 3}:
        raise IesParseError("IES photometric type must be 1, 2, or 3.")
    if units_type not in {1, 2}:
        raise IesParseError("IES units type must be 1 (feet) or 2 (meters).")
    if width_raw < 0.0 or length_raw < 0.0 or height_raw < 0.0:
        raise IesParseError("IES dimensions must be non-negative.")
    if ballast_factor <= 0.0 or ballast_lamp_factor <= 0.0:
        raise IesParseError("IES ballast factors must be positive.")

    if units_type == 1:
        width_m = width_raw * FT2M
        length_m = length_raw * FT2M
        height_m = height_raw * FT2M
    else:
        width_m = width_raw
        length_m = length_raw
        height_m = height_raw

    return IesNumericHeader(
        lamp_count=lamp_count,
        lumens_per_lamp=lumens_per_lamp,
        candela_multiplier=candela_multiplier,
        nvert=nvert,
        nhorz=nhorz,
        phot_type=phot_type,
        units_type=units_type,
        width_raw=width_raw,
        length_raw=length_raw,
        height_raw=height_raw,
        ballast_factor=ballast_factor,
        ballast_lamp_factor=ballast_lamp_factor,
        input_watts=input_watts,
        width_m=width_m,
        length_m=length_m,
        height_m=height_m,
    )


def _ies_angle_values(
    header: IesNumericHeader, nums: list[float]
) -> tuple[list[float], list[float], int]:
    idx = 13
    v_angles = nums[idx : idx + header.nvert]
    idx += header.nvert
    h_angles = nums[idx : idx + header.nhorz]
    idx += header.nhorz
    if len(v_angles) != header.nvert or len(h_angles) != header.nhorz:
        raise IesParseError("IES angle counts do not match numeric header.")
    return v_angles, h_angles, idx


def _validate_ies_angles(v_angles: list[float], h_angles: list[float]) -> None:
    if any(angle < 0.0 or angle > 180.0 for angle in v_angles):
        raise IesParseError("IES vertical angles must be in degrees from 0 to 180.")
    if any(angle < 0.0 or angle > 360.0 for angle in h_angles):
        raise IesParseError("IES horizontal angles must be in degrees from 0 to 360.")
    if any(b <= a for a, b in zip(v_angles, v_angles[1:])):
        raise IesParseError("IES vertical angles must be strictly increasing.")
    if any(b <= a for a, b in zip(h_angles, h_angles[1:])):
        raise IesParseError("IES horizontal angles must be strictly increasing.")


def _ies_candela_table(
    header: IesNumericHeader, nums: list[float], idx: int
) -> list[list[float]]:
    expected = header.nvert * header.nhorz
    candela_vals = nums[idx : idx + expected]
    if len(candela_vals) < expected:
        raise IesParseError("IES candela table is incomplete.")
    if any(value < 0.0 for value in candela_vals):
        raise IesParseError("IES candela table must be non-negative.")

    candela: list[list[float]] = []
    for hi in range(header.nhorz):
        row = candela_vals[hi * header.nvert : (hi + 1) * header.nvert]
        candela.append([v * header.candela_multiplier for v in row])
    return candela


def _trim_wrapping_horizontal_angle(
    h_angles: list[float], candela: list[list[float]]
) -> tuple[list[float], list[list[float]]]:
    if h_angles and abs(h_angles[-1] - 360.0) < 1e-6:
        return h_angles[:-1], candela[:-1]
    return h_angles, candela


def _angle_widths(
    angles: list[float], *, single_width: float, wraps: bool
) -> list[float]:
    count = len(angles)
    if count == 1:
        return [single_width]
    widths = [0.0] * count
    widths[0] = (angles[1] - angles[0]) / 2.0
    widths[-1] = (angles[-1] - angles[-2]) / 2.0
    for i in range(1, count - 1):
        widths[i] = (angles[i + 1] - angles[i - 1]) / 2.0
    if wraps:
        widths[0] += (2.0 * math.pi - angles[-1]) / 2.0
        widths[-1] += (angles[0] + 2.0 * math.pi - angles[-2]) / 2.0
    return widths


def integrate_ies_lumens(lines: list[str]) -> float:
    header = parse_ies_numeric_header(lines)
    nums = _ies_numeric_values(lines)
    v_angles, h_angles, idx = _ies_angle_values(header, nums)
    _validate_ies_angles(v_angles, h_angles)
    candela = _ies_candela_table(header, nums, idx)
    h_angles, candela = _trim_wrapping_horizontal_angle(h_angles, candela)

    theta = [math.radians(v) for v in v_angles]
    phi = [math.radians(h) for h in h_angles]
    nvert = len(theta)
    nhorz = len(phi)
    if nvert <= 0 or nhorz <= 0:
        raise IesParseError("IES angles missing.")

    dtheta = _angle_widths(theta, single_width=math.pi, wraps=False)
    dphi = _angle_widths(phi, single_width=2.0 * math.pi, wraps=True)

    lumens = 0.0
    for hi in range(nhorz):
        for vi in range(nvert):
            lumens += candela[hi][vi] * math.sin(theta[vi]) * dtheta[vi] * dphi[hi]
    return max(lumens, 0.0)


def summarize_ies_header(
    ies_path: Path, lines: list[str] | None = None
) -> IesHeaderSummary:
    lines = lines or read_ies_lines(ies_path)
    numeric = parse_ies_numeric_header(lines)
    total_luminaire_lumens = integrate_ies_lumens(lines)
    return IesHeaderSummary(
        ies_path=str(ies_path.resolve()),
        lamp_count=numeric.lamp_count,
        lumens_per_lamp=numeric.lumens_per_lamp,
        total_luminaire_lumens=total_luminaire_lumens,
        input_watts=numeric.input_watts,
        width_m=numeric.width_m,
        length_m=numeric.length_m,
        height_m=numeric.height_m,
    )


def write_ies_with_dimensions(
    source_ies_path: Path,
    out_path: Path,
    *,
    width_m: float,
    length_m: float,
    height_m: float,
) -> Path:
    lines = read_ies_lines(source_ies_path)
    numeric = parse_ies_numeric_header(lines)

    tilt_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("TILT="):
            tilt_idx = i
            break
    if tilt_idx is None:
        raise SystemExit("ERROR: IES missing TILT line.")

    nums = _ies_numeric_values(lines)
    if len(nums) < 13:
        raise SystemExit("ERROR: IES numeric header is incomplete.")

    if numeric.units_type == 1:
        width_raw = width_m / FT2M
        length_raw = length_m / FT2M
        height_raw = height_m / FT2M
    else:
        width_raw = width_m
        length_raw = length_m
        height_raw = height_m

    nums[7] = width_raw
    nums[8] = length_raw
    nums[9] = height_raw

    numeric_lines = [
        " ".join(f"{value:g}" for value in nums[idx : idx + 10])
        for idx in range(0, len(nums), 10)
    ]
    out_path.write_text("\n".join(lines[: tilt_idx + 1] + numeric_lines) + "\n")
    return out_path


def _photopic_v_lambda(wavelength_nm: float) -> float:
    t1 = (wavelength_nm - 568.8) * (0.0213 if wavelength_nm < 568.8 else 0.0247)
    t2 = (wavelength_nm - 530.9) * (0.0613 if wavelength_nm < 530.9 else 0.0322)
    return 0.821 * math.exp(-0.5 * t1 * t1) + 0.286 * math.exp(-0.5 * t2 * t2)


def _spd_column_keys(
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
        raise SpdParseError(
            f"SPD CSV {spd_path} must contain a wavelength column "
            "('wavelength_nm') and a relative spectrum column "
            "('relative_spd' or 'relative_intensity_normalized')."
        )
    return wl_key, rel_key


def _parse_spd_rows(
    reader: csv.DictReader[str], *, wl_key: str, rel_key: str, spd_path: Path
) -> SpdSamples:
    rows: SpdSamples = []
    for row_number, row in enumerate(reader, start=2):
        wl_raw = row.get(wl_key)
        rel_raw = row.get(rel_key)
        if wl_raw is None or rel_raw is None:
            continue
        wavelength_nm = float(wl_raw)
        relative_value = float(rel_raw)
        if not math.isfinite(wavelength_nm) or not math.isfinite(relative_value):
            raise SpdParseError(
                f"SPD CSV {spd_path} row {row_number} contains a non-finite value."
            )
        if relative_value < 0.0:
            raise SpdParseError(
                f"SPD CSV {spd_path} row {row_number} contains negative relative intensity."
            )
        rows.append((wavelength_nm, relative_value))
    return rows


def _validate_spd_rows(rows: SpdSamples, spd_path: Path) -> None:
    if len(rows) < 2:
        raise SpdParseError(f"SPD CSV {spd_path} must contain at least two samples.")
    if any(wl1 <= wl0 for (wl0, _rel0), (wl1, _rel1) in zip(rows, rows[1:])):
        raise SpdParseError(
            f"SPD CSV {spd_path} wavelengths must be unique and strictly increasing."
        )
    if sum(rel for _wl, rel in rows) <= 0.0:
        raise SpdParseError(
            f"SPD CSV {spd_path} contains no positive relative intensity values."
        )


def load_relative_spd(spd_path: Path) -> SpdSamples:
    try:
        with spd_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            wl_key, rel_key = _spd_column_keys(reader.fieldnames, spd_path)
            rows = _parse_spd_rows(
                reader, wl_key=wl_key, rel_key=rel_key, spd_path=spd_path
            )
    except UnicodeDecodeError as exc:
        raise SpdParseError(
            f"SPD CSV {spd_path} is not valid UTF-8 text: {exc}"
        ) from exc
    except OSError as exc:
        raise SpdParseError(f"failed to read SPD CSV {spd_path}: {exc}") from exc
    except ValueError as exc:
        raise SpdParseError(f"invalid SPD CSV data in {spd_path}: {exc}") from exc

    rows.sort(key=lambda item: item[0])
    _validate_spd_rows(rows, spd_path)
    return rows


def spd_photon_metrics(spd_path: Path) -> RelativeSPDPhotonMetrics:
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
            0.5 * (rel0 * _photopic_v_lambda(wl0) + rel1 * _photopic_v_lambda(wl1)) * dw
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
            0.5 * ((seg_lo * rel_lo) + (seg_hi * rel_hi)) * (seg_hi - seg_lo)
        )

    if (
        total_power <= 0.0
        or luminous_weight <= 0.0
        or par_lambda_weight <= 0.0
        or par_power <= 0.0
    ):
        raise SpdParseError(
            f"SPD CSV {spd_path} produced an invalid spectral conversion integral."
        )

    umol_per_radiant_w = PHOTON_UMOL_PER_J_NM * (par_lambda_weight / total_power)
    lm_per_radiant_w = LUMINOUS_EFFICACY_LM_PER_W * (luminous_weight / total_power)
    if lm_per_radiant_w <= 0.0:
        raise SpdParseError(
            f"SPD CSV {spd_path} produced a non-positive luminous efficacy."
        )

    return RelativeSPDPhotonMetrics(
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


def spd_lm_to_umol(spd_path: Path) -> float:
    return spd_photon_metrics(spd_path).umol_per_lumen


def _scale_ies_rad(rad_path: Path, scale: float) -> None:
    if abs(scale - 1.0) < 1e-12:
        return
    lines = rad_path.read_text().splitlines()
    out: list[str] = []
    in_light = False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            tokens = stripped.split()
            if len(tokens) >= 3 and tokens[1] == "light":
                in_light = True
                out.append(line)
                continue
            if in_light and tokens and tokens[0] == "3" and len(tokens) >= 4:
                try:
                    r = float(tokens[1]) * scale
                    g = float(tokens[2]) * scale
                    b = float(tokens[3]) * scale
                    out.append(f"3 {r:.6f} {g:.6f} {b:.6f}")
                except ValueError:
                    out.append(line)
                in_light = False
                continue
            if in_light and tokens and tokens[0] not in {"0", "3"}:
                in_light = False
        out.append(line)
    rad_path.write_text("\n".join(out) + "\n")


def normalize_ies_rad_companion_paths(rad_path: Path) -> None:
    """Make ies2rad companion file references resolvable from any cwd."""
    base_dir = rad_path.resolve().parent
    lines = rad_path.read_text().splitlines()
    changed = False

    for idx, line in enumerate(lines):
        tokens = line.split()
        line_changed = False
        if len(tokens) < 6:
            continue
        count_token = tokens[0]
        try:
            count = int(float(count_token))
        except ValueError:
            continue
        if count < 5:
            continue
        data_path = Path(tokens[2]).expanduser()
        cal_path = Path(tokens[3]).expanduser()
        if not data_path.is_absolute():
            tokens[2] = str((base_dir / data_path).resolve())
            line_changed = True
        if not cal_path.is_absolute() and cal_path.name != "source.cal":
            tokens[3] = str((base_dir / cal_path).resolve())
            line_changed = True
        if line_changed:
            lines[idx] = " ".join(tokens)
            changed = True

    if changed:
        rad_path.write_text("\n".join(lines) + "\n")


def run_ies2rad(
    out_dir: Path, ies_path: Path, out_base: str, scale: float | None
) -> Path:
    if not ies_path.exists():
        raise IesParseError(f"IES file not found: {ies_path}")
    ies2rad_exe = shutil.which("ies2rad")
    if ies2rad_exe is None:
        raise SystemExit("ERROR: ies2rad not found on PATH.")

    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [ies2rad_exe, "-o", out_base]
    if scale is not None:
        cmd += ["-m", f"{scale:.8f}"]
    cmd.append(str(ies_path.resolve()))

    try:
        subprocess.run(cmd, cwd=out_dir, check=True)  # nosec B603
    except subprocess.CalledProcessError:
        if scale is None:
            raise
        print(
            "NOTE: ies2rad -m failed; retrying without scaling and post-scaling the RAD file."
        )
        subprocess.run(  # nosec B603
            [ies2rad_exe, "-o", out_base, str(ies_path.resolve())],
            cwd=out_dir,
            check=True,
        )
        _scale_ies_rad(out_dir / f"{out_base}.rad", scale)
    rad_path = out_dir / f"{out_base}.rad"
    normalize_ies_rad_companion_paths(rad_path)
    return rad_path


def _polygon_vertices_from_block(
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
    coords: list[float] = []
    for line in block_lines[4:]:
        for token in line.split():
            try:
                coords.append(float(token))
            except ValueError as exc:
                raise SystemExit(
                    "ERROR: invalid numeric value in IES polygon block."
                ) from exc
    if len(coords) < coord_count:
        raise SystemExit("ERROR: IES polygon coordinate list is incomplete.")
    return [(coords[i], coords[i + 1], coords[i + 2]) for i in range(0, coord_count, 3)]


def _polygon_normal(
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


def _inspect_brightdata_source(lines: list[str]) -> BrightDataSource:
    for idx, line in enumerate(lines):
        tokens = line.split()
        if len(tokens) >= 3 and tokens[1] == "brightdata":
            if idx + 3 >= len(lines):
                break
            func_tokens = lines[idx + 1].split()
            arg_tokens = lines[idx + 3].split()
            if len(func_tokens) < 6 or len(arg_tokens) < 2:
                raise SystemExit("ERROR: ies2rad brightdata block is incomplete.")
            return BrightDataSource(
                index=idx,
                modifier=tokens[2],
                correction_function=func_tokens[1],
                data_file=func_tokens[2],
                cal_file=func_tokens[3],
                phi_expr=func_tokens[4],
                theta_expr=func_tokens[5],
                source_scale=arg_tokens[1],
            )
    raise SystemExit("ERROR: ies2rad output missing brightdata block.")


def _find_light_source_block(
    lines: list[str], *, start_idx: int, modifier: str, allowed_kinds: set[str]
) -> LightSourceBlock:
    for idx in range(start_idx, len(lines)):
        tokens = lines[idx].split()
        if len(tokens) >= 3 and tokens[0] == modifier and tokens[1] in allowed_kinds:
            return LightSourceBlock(index=idx, kind=tokens[1], name=tokens[2])
    allowed_label = " or ".join(sorted(allowed_kinds))
    raise SystemExit(f"ERROR: ies2rad output missing {allowed_label} block.")


def _polygon_block_from_lines(
    lines: list[str], idx: int
) -> tuple[str, list[tuple[float, float, float]], int]:
    tokens = lines[idx].split()
    if idx + 3 >= len(lines):
        raise SystemExit("ERROR: ies2rad polygon block is incomplete.")
    count_tokens = lines[idx + 3].split()
    if not count_tokens:
        raise SystemExit("ERROR: ies2rad polygon coordinate count is missing.")
    coord_count = int(float(count_tokens[0]))
    vertex_line_count = coord_count // 3
    block_end = idx + 4 + vertex_line_count
    block_lines = lines[idx:block_end]
    return tokens[2], _polygon_vertices_from_block(block_lines), block_end


def _sphere_block_from_lines(
    lines: list[str], idx: int
) -> tuple[str, tuple[float, float, float], float]:
    tokens = lines[idx].split()
    if idx + 3 >= len(lines):
        raise SystemExit("ERROR: ies2rad sphere block is incomplete.")
    real_tokens = lines[idx + 3].split()
    if len(real_tokens) < 5:
        raise SystemExit("ERROR: ies2rad sphere data is incomplete.")
    cx = float(real_tokens[-4])
    cy = float(real_tokens[-3])
    cz = float(real_tokens[-2])
    radius = float(real_tokens[-1])
    return tokens[2], (cx, cy, cz), radius


def _scan_ies_rad_primitives(
    lines: list[str], *, start_idx: int, light_name: str
) -> RadPrimitiveBlocks:
    primitive_types: list[str] = []
    primitive_names: list[str] = []
    polygon_blocks: list[tuple[str, list[tuple[float, float, float]]]] = []
    sphere_blocks: list[tuple[str, tuple[float, float, float], float]] = []
    idx = start_idx
    while idx < len(lines):
        tokens = lines[idx].split()
        if len(tokens) >= 3 and tokens[0] == light_name and tokens[1] == "polygon":
            name, vertices, block_end = _polygon_block_from_lines(lines, idx)
            primitive_types.append("polygon")
            primitive_names.append(name)
            polygon_blocks.append((name, vertices))
            idx = block_end
            continue
        if len(tokens) >= 3 and tokens[0] == light_name and tokens[1] == "sphere":
            name, center, radius = _sphere_block_from_lines(lines, idx)
            primitive_types.append("sphere")
            primitive_names.append(name)
            sphere_blocks.append((name, center, radius))
            idx += 4
            continue
        idx += 1
    return RadPrimitiveBlocks(
        primitive_types=primitive_types,
        primitive_names=primitive_names,
        polygon_blocks=polygon_blocks,
        sphere_blocks=sphere_blocks,
    )


def _primitive_geometry_summary(
    blocks: RadPrimitiveBlocks,
) -> tuple[str, str, tuple[float, float, float], list[float]]:
    if blocks.sphere_blocks:
        center = blocks.sphere_blocks[0][1]
        dims = [2.0 * blocks.sphere_blocks[0][2]] * 3
        return "sphere", "sphere", center, dims
    primitive_type = "polygon"
    geometry = "polygon" if len(blocks.polygon_blocks) == 1 else "multi_polygon"
    first_vertices = blocks.polygon_blocks[0][1]
    xs = [vertex[0] for vertex in first_vertices]
    ys = [vertex[1] for vertex in first_vertices]
    zs = [vertex[2] for vertex in first_vertices]
    center = (
        0.5 * (min(xs) + max(xs)),
        0.5 * (min(ys) + max(ys)),
        sum(zs) / len(zs),
    )
    dims = [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)]
    return primitive_type, geometry, center, dims


def inspect_ies_rad_source(rad_path: Path) -> dict[str, object]:
    lines = rad_path.read_text().splitlines()
    brightdata = _inspect_brightdata_source(lines)
    light_block = _find_light_source_block(
        lines,
        start_idx=brightdata.index + 4,
        modifier=brightdata.modifier,
        allowed_kinds={"light", "illum"},
    )
    primitives = _scan_ies_rad_primitives(
        lines, start_idx=light_block.index + 4, light_name=light_block.name
    )

    if not primitives.primitive_types:
        raise SystemExit("ERROR: ies2rad output contains no luminous primitives.")

    primitive_type, geometry, center, dims = _primitive_geometry_summary(primitives)

    return {
        "modifier": brightdata.modifier,
        "light": light_block.name,
        "light_kind": light_block.kind,
        "correction_function": brightdata.correction_function,
        "data_file": brightdata.data_file,
        "cal_file": brightdata.cal_file,
        "phi_expr": brightdata.phi_expr,
        "theta_expr": brightdata.theta_expr,
        "source_scale": brightdata.source_scale,
        "geometry": geometry,
        "primitive_type": primitive_type,
        "primitive_names": primitives.primitive_names,
        "primitive_count": len(primitives.primitive_names),
        "center_m": center,
        "dimensions_m": dims,
    }


def _select_downward_polygon(
    polygon_blocks: list[tuple[str, list[str], list[tuple[float, float, float]]]],
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
    _nx, _ny, nz = _polygon_normal(best_vertices)
    if nz > 0:
        best_vertices = list(reversed(best_vertices))
    return best_name, best_vertices


def _rewrite_brightdata_source(lines: list[str]) -> BrightDataSource:
    for idx, line in enumerate(lines):
        tokens = line.split()
        if len(tokens) >= 3 and tokens[1] == "brightdata":
            if idx + 3 >= len(lines):
                raise SystemExit("ERROR: ies2rad brightdata block is incomplete.")
            func_tokens = lines[idx + 1].split()
            arg_tokens = lines[idx + 3].split()
            if len(func_tokens) < 6:
                raise SystemExit(
                    "ERROR: ies2rad brightdata function line is incomplete."
                )
            if len(arg_tokens) < 2:
                raise SystemExit(
                    "ERROR: ies2rad brightdata argument line is incomplete."
                )
            return BrightDataSource(
                index=idx,
                modifier=tokens[2],
                correction_function=func_tokens[1],
                data_file=func_tokens[2],
                cal_file=func_tokens[3],
                phi_expr=func_tokens[4],
                theta_expr=func_tokens[5],
                source_scale=arg_tokens[1],
            )
    raise SystemExit("ERROR: ies2rad output missing brightdata block.")


def _rewrite_polygon_block_from_lines(
    lines: list[str], idx: int
) -> tuple[str, list[str], list[tuple[float, float, float]], int]:
    tokens = lines[idx].split()
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
    return tokens[2], block_lines, _polygon_vertices_from_block(block_lines), block_end


def _rewrite_sphere_block_from_lines(
    lines: list[str], idx: int
) -> tuple[str, tuple[float, float, float], float]:
    tokens = lines[idx].split()
    if idx + 3 >= len(lines):
        raise SystemExit("ERROR: ies2rad sphere block is incomplete.")
    real_tokens = lines[idx + 3].split()
    if len(real_tokens) < 5:
        raise SystemExit("ERROR: ies2rad sphere block is incomplete.")
    try:
        cx = float(real_tokens[1])
        cy = float(real_tokens[2])
        cz = float(real_tokens[3])
        radius = float(real_tokens[4])
    except ValueError as exc:
        raise SystemExit("ERROR: invalid ies2rad sphere block.") from exc
    return tokens[2], (cx, cy, cz), radius


def _scan_rewrite_primitives(
    lines: list[str], *, start_idx: int, light_name: str
) -> RewritePrimitiveBlocks:
    polygon_blocks: list[tuple[str, list[str], list[tuple[float, float, float]]]] = []
    sphere_blocks: list[tuple[str, tuple[float, float, float], float]] = []
    primitive_count = 0
    idx = start_idx
    while idx < len(lines):
        tokens = lines[idx].split()
        if len(tokens) >= 3 and tokens[0] == light_name and tokens[1] == "polygon":
            primitive_count += 1
            name, block_lines, vertices, block_end = _rewrite_polygon_block_from_lines(
                lines, idx
            )
            polygon_blocks.append((name, block_lines, vertices))
            idx = block_end
            continue
        if len(tokens) >= 3 and tokens[0] == light_name and tokens[1] == "sphere":
            primitive_count += 1
            sphere_blocks.append(_rewrite_sphere_block_from_lines(lines, idx))
            idx += 4
            continue
        idx += 1
    if primitive_count <= 0:
        raise SystemExit("ERROR: ies2rad output contains no luminous primitives.")
    return RewritePrimitiveBlocks(
        polygon_blocks=polygon_blocks,
        sphere_blocks=sphere_blocks,
        primitive_count=primitive_count,
    )


def _aperture_center(
    primitives: RewritePrimitiveBlocks,
) -> tuple[float, float, float]:
    if primitives.sphere_blocks:
        return primitives.sphere_blocks[0][1]
    if primitives.polygon_blocks:
        _, vertices = _select_downward_polygon(primitives.polygon_blocks)
        xs = [vertex[0] for vertex in vertices]
        ys = [vertex[1] for vertex in vertices]
        zs = [vertex[2] for vertex in vertices]
        return (0.5 * (min(xs) + max(xs)), 0.5 * (min(ys) + max(ys)), sum(zs) / len(zs))
    return (0.0, 0.0, 0.0)


def _raw_geometry(primitives: RewritePrimitiveBlocks) -> str:
    if primitives.sphere_blocks:
        return "sphere"
    return "polygon" if primitives.primitive_count == 1 else "multi_polygon"


def _manual_aperture_geometry(
    primitives: RewritePrimitiveBlocks,
    *,
    aperture_length_m: float | None,
    aperture_width_m: float | None,
    aperture_z_m: float | None,
    aperture_name: str,
) -> ApertureGeometry:
    center = _aperture_center(primitives)
    if aperture_length_m is None or aperture_width_m is None:
        raise SystemExit(
            "ERROR: manual effective aperture requires explicit length and width."
        )
    z = aperture_z_m if aperture_z_m is not None else center[2]
    x0 = center[0] - (0.5 * aperture_length_m)
    x1 = center[0] + (0.5 * aperture_length_m)
    y0 = center[1] - (0.5 * aperture_width_m)
    y1 = center[1] + (0.5 * aperture_width_m)
    return ApertureGeometry(
        name=aperture_name,
        vertices=[(x0, y0, z), (x0, y1, z), (x1, y1, z), (x1, y0, z)],
        raw_geometry=_raw_geometry(primitives),
        manual_aperture=True,
    )


def _selected_aperture_geometry(primitives: RewritePrimitiveBlocks) -> ApertureGeometry:
    if not primitives.polygon_blocks:
        raise SystemExit("ERROR: ies2rad output contains no luminous polygons.")
    aperture_name, aperture_vertices = _select_downward_polygon(
        primitives.polygon_blocks
    )
    return ApertureGeometry(
        name=aperture_name,
        vertices=aperture_vertices,
        raw_geometry="polygon" if primitives.primitive_count == 1 else "multi_polygon",
        manual_aperture=False,
    )


def _aperture_geometry(
    primitives: RewritePrimitiveBlocks,
    *,
    aperture_length_m: float | None,
    aperture_width_m: float | None,
    aperture_z_m: float | None,
    aperture_name: str,
) -> ApertureGeometry:
    manual_aperture = (
        aperture_length_m is not None
        or aperture_width_m is not None
        or aperture_z_m is not None
    )
    if manual_aperture:
        return _manual_aperture_geometry(
            primitives,
            aperture_length_m=aperture_length_m,
            aperture_width_m=aperture_width_m,
            aperture_z_m=aperture_z_m,
            aperture_name=aperture_name,
        )
    return _selected_aperture_geometry(primitives)


def _rewritten_rad_lines(
    *,
    lines: list[str],
    brightdata: BrightDataSource,
    light_block: list[str],
    light_name: str,
    aperture: ApertureGeometry,
) -> list[str]:
    rewritten: list[str] = list(lines[: brightdata.index])
    if rewritten and rewritten[-1].strip():
        rewritten.append("")
    rewritten.extend(
        [
            f"void brightdata {brightdata.modifier}",
            (
                f"5 flatcorr {brightdata.data_file} {brightdata.cal_file} "
                f"{brightdata.phi_expr} {brightdata.theta_expr}"
            ),
            "0",
            f"1 {brightdata.source_scale}",
            "",
        ]
    )
    rewritten.extend(light_block)
    rewritten.append("")
    rewritten.extend(
        [
            f"{light_name} polygon {aperture.name}",
            "0",
            "0",
            f"{len(aperture.vertices) * 3}",
        ]
    )
    for x, y, z in aperture.vertices:
        rewritten.append(f"\t{x:.6f}\t{y:.6f}\t{z:.6f}")
    rewritten.append("")
    return rewritten


def _aperture_metadata(
    *,
    brightdata: BrightDataSource,
    light_name: str,
    aperture: ApertureGeometry,
    primitive_count: int,
    raw_sphere_count: int,
) -> dict[str, object]:
    xs = [vertex[0] for vertex in aperture.vertices]
    ys = [vertex[1] for vertex in aperture.vertices]
    zs = [vertex[2] for vertex in aperture.vertices]
    return {
        "modifier": brightdata.modifier,
        "light": light_name,
        "geometry": "single_downward_flatcorr_aperture",
        "aperture_name": aperture.name,
        "aperture_length_m": max(xs) - min(xs),
        "aperture_width_m": max(ys) - min(ys),
        "aperture_z_m": sum(zs) / len(zs),
        "faces_removed": max(primitive_count - 1, 0),
        "primitive_count": primitive_count,
        "raw_geometry": aperture.raw_geometry,
        "raw_sphere_count": raw_sphere_count,
        "manual_aperture": aperture.manual_aperture,
    }


def rewrite_ies_rad_as_downward_aperture(
    rad_path: Path,
    *,
    aperture_length_m: float | None = None,
    aperture_width_m: float | None = None,
    aperture_z_m: float | None = None,
    aperture_name: str = "effective_aperture",
) -> dict[str, object]:
    lines = rad_path.read_text().splitlines()
    brightdata = _rewrite_brightdata_source(lines)
    light_source = _find_light_source_block(
        lines,
        start_idx=brightdata.index + 4,
        modifier=brightdata.modifier,
        allowed_kinds={"light"},
    )
    if light_source.index + 3 >= len(lines):
        raise SystemExit("ERROR: ies2rad light block is incomplete.")
    light_block = lines[light_source.index : light_source.index + 4]
    primitives = _scan_rewrite_primitives(
        lines, start_idx=light_source.index + 4, light_name=light_source.name
    )
    aperture = _aperture_geometry(
        primitives,
        aperture_length_m=aperture_length_m,
        aperture_width_m=aperture_width_m,
        aperture_z_m=aperture_z_m,
        aperture_name=aperture_name,
    )
    rewritten = _rewritten_rad_lines(
        lines=lines,
        brightdata=brightdata,
        light_block=light_block,
        light_name=light_source.name,
        aperture=aperture,
    )
    rad_path.write_text("\n".join(rewritten))

    return _aperture_metadata(
        brightdata=brightdata,
        light_name=light_source.name,
        aperture=aperture,
        primitive_count=primitives.primitive_count,
        raw_sphere_count=len(primitives.sphere_blocks),
    )
