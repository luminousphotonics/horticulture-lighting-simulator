from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path
from enum import Enum
from typing import Any

from fastapi import HTTPException

from rad_rebuild.radiance.config import (
    DEFAULT_EXECUTION_MODE,
    COMPETITOR_FIXTURE_PPE_UMOL_PER_J,
    COMPETITOR_LAYOUT_FULL,
    COMPETITOR_LAYOUT_PRACTICAL,
    EXECUTION_MODE_LIVE_DOCKER,
    EXECUTION_MODE_PRECOMPUTED,
    MODE_COMPETITOR,
    MODE_HPS,
    MODE_SMD,
    canonicalize_execution_mode as _config_canonicalize_execution_mode,
    canonicalize_quality_preset as _config_canonicalize_quality_preset,
    canonicalize_radiance_mode,
    output_dir_for_mode as _config_output_dir_for_mode,
    overlay_for_mode as _config_overlay_for_mode,
)
from rad_rebuild.radiance.domain import plant_geometry_config_from_request
from rad_rebuild.radiance.paths import (
    RADIANCE_CURVE_DATA_ROOT,
    RADIANCE_DATA_ROOT,
    RADIANCE_IES_ROOT,
    REPO_ROOT,
)
from rad_rebuild.radiance.settings import get_settings

from .workspace import BASIS_ROOT, CACHE_ROOT, ROOT, VISUALIZATION_ROOT
from .models import RadianceRunRequest

try:
    from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
        DEFAULT_IES_VARIANT as DEFAULT_HPS_IES_VARIANT,
        coverage_grid_counts as hps_coverage_grid_counts,
        normalize_ies_variant as normalize_hps_ies_variant,
    )
    from rad_rebuild.radiance.engine.emitters.smd_generation.module_profile import (
        GASKET_APERTURE_M,
        LID_SIDE_M,
        LID_THK_M,
        MODULE_PROFILE_VERSION,
        PTFE_LINER_THK_M,
        PTFE_REFLECTANCE,
        STACK_HEIGHT_M,
    )
    from rad_rebuild.radiance.engine.layout.layout_generator import generate_layout_with_zones
    from rad_rebuild.radiance.engine.photometry.smd_curve_model import (
        DEFAULT_RED_PPE_CSV,
        DEFAULT_RED_VF_CSV,
        DEFAULT_WHITE_PPE_CSV,
        DEFAULT_WHITE_VF_CSV,
    )
    from rad_rebuild.radiance.engine.simulation.basis_backends import (
        DEFAULT_SMD_BASIS_BACKEND,
        canonicalize_basis_backend,
        validate_basis_backend_request,
    )
    from rad_rebuild.radiance.engine.simulation.precomputed_dataset import (
        PRECOMPUTED_ROOT_ENV,
        canonical_competitor_layout,
        resolve_precomputed_root,
    )
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"Failed to import backend env dependencies: {e}") from e


DEFAULT_SENSOR_GRID_PROFILE = "adaptive_centered_v1"
DEFAULT_SENSOR_GRID_SPACING_M = 0.25
DEFAULT_OUTER_MARGIN_IN = 1.0
IN2M = 0.0254
SPYDR_FIXTURE_LENGTH_IN = 46.85
SPYDR_FIXTURE_WIDTH_IN = 42.8
SMD_MODULE_FOOTPRINT_X_M = 0.1524
SMD_MODULE_FOOTPRINT_Y_M = 0.1650
SMD_FIXTURE_GUARD_IN = 0.25
LOCAL_RADIANCE_BIN = Path("/opt/radiance/bin")
LOCAL_RADIANCE_LIB = Path("/opt/radiance/lib")
HPS_MODE_LABEL = MODE_HPS
DISABLE_RADIANCE_AUTODETECT_ENV = "RAD_REBUILD_DISABLE_RADIANCE_AUTODETECT"
PLANT_ENV_KEYS = (
    "FSPM_PLANTS_ENABLED",
    "FSPM_PLANT_SEED",
    "FSPM_PLANT_ROWS",
    "FSPM_PLANT_COLUMNS",
    "FSPM_PLANT_SPACING_M",
    "FSPM_PLANT_HEIGHT_M",
    "FSPM_PLANT_CANOPY_RADIUS_M",
    "FSPM_PLANT_LEAF_COUNT",
    "FSPM_PLANT_LEAF_LENGTH_MIN_M",
    "FSPM_PLANT_LEAF_LENGTH_MAX_M",
    "FSPM_PLANT_LEAF_WIDTH_MIN_M",
    "FSPM_PLANT_LEAF_WIDTH_MAX_M",
    "FSPM_PLANT_LEAF_TILT_MIN_DEG",
    "FSPM_PLANT_LEAF_TILT_MAX_DEG",
    "FSPM_PLANT_CURVATURE_M",
    "FSPM_PLANT_GROWTH_STAGE",
    "FSPM_PLANT_REFLECTANCE",
    "FSPM_PLANT_TRANSMITTANCE",
    "FSPM_PLANT_ABSORPTANCE",
)


def _canonicalize_execution_mode(raw: str | None) -> str:
    try:
        return _config_canonicalize_execution_mode(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _canonicalize_quality_preset(raw: str | None) -> str:
    return _config_canonicalize_quality_preset(raw)


def _contract_str(value: object, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _request_uses_precomputed(req: Any) -> bool:
    return _canonicalize_execution_mode(getattr(req, "execution_mode", DEFAULT_EXECUTION_MODE)) == EXECUTION_MODE_PRECOMPUTED


def _request_uses_docker(req: Any) -> bool:
    return _canonicalize_execution_mode(getattr(req, "execution_mode", DEFAULT_EXECUTION_MODE)) == EXECUTION_MODE_LIVE_DOCKER


def _docker_path_prefix() -> str:
    if os.name == "nt":
        return ""
    return "/opt/homebrew/bin:/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin"


def _resolve_python() -> str:
    for prefix in (
        REPO_ROOT / ".venv" / "bin",
        REPO_ROOT / "venv" / "bin",
        ROOT / ".venv" / "bin",
        ROOT / "venv" / "bin",
        ROOT.parent / ".venv" / "bin",
        ROOT.parent / "venv" / "bin",
    ):
        for name in ("python3", "python"):
            candidate = prefix / name
            if candidate.exists():
                return str(candidate)
    return get_settings().radiance_python or "python3"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _prepend_path(env: dict[str, str], key: str, value: str) -> None:
    clean = value.strip()
    if not clean:
        return
    parts = [part for part in env.get(key, "").split(os.pathsep) if part]
    if clean in parts:
        return
    env[key] = f"{clean}{os.pathsep}{env[key]}" if env.get(key) else clean


def _configured_radiance_bin() -> Path | None:
    raw = os.environ.get("RADIANCE_BIN_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    home = os.environ.get("RADIANCE_HOME", "").strip()
    if home:
        return Path(home).expanduser() / "bin"
    return None


def _configured_radiance_lib() -> Path | None:
    raw = os.environ.get("RADIANCE_LIB_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    home = os.environ.get("RADIANCE_HOME", "").strip()
    if home:
        return Path(home).expanduser() / "lib"
    return None


def _apply_private_photometry_paths(env: dict[str, str]) -> None:
    conventional_ies = env.get("RAD_REBUILD_PRIVATE_CONVENTIONAL_IES", "").strip()
    hps_ies = env.get("RAD_REBUILD_PRIVATE_HPS_IES", "").strip()

    if conventional_ies:
        env["SPYDR_IES_PATH"] = conventional_ies
    if hps_ies:
        env["HPS_IES_PATH"] = hps_ies


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PATH", "")
    configured_bin = _configured_radiance_bin()
    if configured_bin is not None:
        _prepend_path(env, "PATH", str(configured_bin))
    elif LOCAL_RADIANCE_BIN.is_dir() and not _env_flag(DISABLE_RADIANCE_AUTODETECT_ENV):
        _prepend_path(env, "PATH", str(LOCAL_RADIANCE_BIN))
    configured_lib = _configured_radiance_lib()
    if configured_lib is not None:
        current_raypath = env.get("RAYPATH", ".")
        env["RAYPATH"] = current_raypath
        _prepend_path(env, "RAYPATH", str(configured_lib))
    elif LOCAL_RADIANCE_LIB.is_dir() and not _env_flag(DISABLE_RADIANCE_AUTODETECT_ENV):
        current_raypath = env.get("RAYPATH", ".")
        env["RAYPATH"] = current_raypath
        _prepend_path(env, "RAYPATH", str(LOCAL_RADIANCE_LIB))
    prefix = _docker_path_prefix()
    if prefix:
        env["PATH"] = f"{prefix}:{env['PATH']}" if env["PATH"] else prefix
    env["PYTHONUNBUFFERED"] = "1"
    # Never inherit last-run power overrides from the server process environment.
    # The solve pipeline enables these explicitly when it has generated a
    # layout-compatible schedule; direct runs must start from a clean env.
    env["USE_RING_POWERS_JSON"] = "0"
    env["RING_POWERS_STRICT"] = "0"
    env.pop("RING_POWERS_REQUIRED", None)
    env.pop("RING_POWERS_JSON", None)
    env.pop("OUT_JSON", None)
    env.pop("BASIS_PATH", None)
    env["SMD_BASIS_MODE"] = "0"
    env["SMD_BASIS_RING"] = "-1"
    env["SMD_BASIS_MODULE_IDX"] = "-1"
    env["SMD_BASIS_OUTER_MODULE_IDX"] = "-1"
    env["SMD_OUTER_PER_MODULE"] = "0"
    env["SMD_PERIM_GAP_FILL"] = "0"
    env["OPTICS"] = "stack"
    env["RADIANCE_DATA_ROOT"] = str(RADIANCE_DATA_ROOT)
    env["RADIANCE_CURVE_DATA_ROOT"] = str(RADIANCE_CURVE_DATA_ROOT)
    env["RADIANCE_IES_ROOT"] = str(RADIANCE_IES_ROOT)
    env["RADIANCE_OUTPUT_ROOT"] = str(ROOT)
    env["RADIANCE_RUNTIME_STATE_ROOT"] = str(ROOT / "runtime_state")
    env["RADIANCE_BASIS_OUTPUT_ROOT"] = str(BASIS_ROOT)
    env["RADIANCE_VISUALIZATION_OUTPUT_ROOT"] = str(VISUALIZATION_ROOT)
    env["RADIANCE_CACHE_ROOT"] = str(CACHE_ROOT)
    env[PRECOMPUTED_ROOT_ENV] = str(resolve_precomputed_root())
    env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
    _apply_private_photometry_paths(env)
    return env


def _aligned_dims_ft(length_ft: float, width_ft: float) -> tuple[float, float]:
    if width_ft > length_ft:
        return width_ft, length_ft
    return length_ft, width_ft


def _diamond_to_axis(x: float, y: float) -> tuple[float, float]:
    return (float(x) + float(y), float(x) - float(y))


@lru_cache(maxsize=512)
def _smd_exact_tiled_pitch_m(length_ft: float, width_ft: float) -> tuple[float, float]:
    length_ft, width_ft = _aligned_dims_ft(length_ft, width_ft)
    room_l_m = length_ft * 0.3048
    room_w_m = width_ft * 0.3048
    margin_m = DEFAULT_OUTER_MARGIN_IN * IN2M
    module_half_x = 0.5 * SMD_MODULE_FOOTPRINT_X_M
    module_half_y = 0.5 * SMD_MODULE_FOOTPRINT_Y_M
    fixture_guard_m = SMD_FIXTURE_GUARD_IN * IN2M

    layout = generate_layout_with_zones(length_ft, width_ft, base_n_override=None)
    all_points = layout.get("all_positions") or []
    if not all_points:
        raise RuntimeError("Unable to derive SMD exact-tiled spacing for practical competitor layout.")

    axis_points = [_diamond_to_axis(x, y) for x, y in all_points]
    xs = [p[0] for p in axis_points]
    ys = [p[1] for p in axis_points]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    if span_x <= 0 or span_y <= 0:
        raise RuntimeError("Invalid SMD exact-tiled span for practical competitor layout.")

    usable_half_x = (room_l_m * 0.5) - margin_m - module_half_x - fixture_guard_m
    usable_half_y = (room_w_m * 0.5) - margin_m - module_half_y - fixture_guard_m
    dim_x = 2.0 * usable_half_x
    dim_y = 2.0 * usable_half_y
    if dim_x <= 0 or dim_y <= 0:
        raise RuntimeError("Room interior too small to derive SMD exact-tiled spacing.")

    return dim_x / span_x, dim_y / span_y


@lru_cache(maxsize=512)
def _spydr_practical_target_gap_in(length_ft: float, width_ft: float) -> tuple[float, float]:
    pitch_x_m, pitch_y_m = _smd_exact_tiled_pitch_m(length_ft, width_ft)
    return (
        max((pitch_x_m / IN2M) - (SMD_MODULE_FOOTPRINT_X_M / IN2M), 0.0),
        max((pitch_y_m / IN2M) - (SMD_MODULE_FOOTPRINT_Y_M / IN2M), 0.0),
    )


def _distributed_gap_in(usable_in: float, fixture_span_in: float, count: int) -> float:
    if count <= 0:
        return 0.0
    return max(usable_in - count * fixture_span_in, 0.0) / float(count + 1)


def _practical_count_for_axis(usable_in: float, fixture_span_in: float, target_gap_in: float) -> int:
    if usable_in <= 0 or fixture_span_in <= 0:
        return 1
    numerator = max(usable_in - target_gap_in, fixture_span_in)
    denominator = max(fixture_span_in + target_gap_in, 1e-9)
    count = int(math.floor(numerator / denominator))
    return max(1, count)


@lru_cache(maxsize=512)
def _spydr_practical_layout_metrics_in(length_ft: float, width_ft: float) -> tuple[int, int, float, float, float, float]:
    length_ft, width_ft = _aligned_dims_ft(length_ft, width_ft)
    usable_x_in = max(0.0, length_ft * 12.0 - 2.0 * DEFAULT_OUTER_MARGIN_IN)
    usable_y_in = max(0.0, width_ft * 12.0 - 2.0 * DEFAULT_OUTER_MARGIN_IN)
    target_gap_x_in, target_gap_y_in = _spydr_practical_target_gap_in(length_ft, width_ft)
    nx = _practical_count_for_axis(usable_x_in, SPYDR_FIXTURE_LENGTH_IN, target_gap_x_in)
    ny = _practical_count_for_axis(usable_y_in, SPYDR_FIXTURE_WIDTH_IN, target_gap_y_in)
    actual_gap_x_in = _distributed_gap_in(usable_x_in, SPYDR_FIXTURE_LENGTH_IN, nx)
    actual_gap_y_in = _distributed_gap_in(usable_y_in, SPYDR_FIXTURE_WIDTH_IN, ny)
    return nx, ny, target_gap_x_in, target_gap_y_in, actual_gap_x_in, actual_gap_y_in


def _spydr_grid_counts(length_ft: float, width_ft: float, competitor_layout: str = COMPETITOR_LAYOUT_FULL) -> tuple[int, int]:
    length_ft, width_ft = _aligned_dims_ft(length_ft, width_ft)
    usable_x_in = max(0.0, length_ft * 12.0 - 2.0 * DEFAULT_OUTER_MARGIN_IN)
    usable_y_in = max(0.0, width_ft * 12.0 - 2.0 * DEFAULT_OUTER_MARGIN_IN)
    competitor_layout = canonical_competitor_layout(competitor_layout)
    if competitor_layout == COMPETITOR_LAYOUT_PRACTICAL:
        nx, ny, _target_gap_x_in, _target_gap_y_in, _actual_gap_x_in, _actual_gap_y_in = _spydr_practical_layout_metrics_in(length_ft, width_ft)
        return nx, ny
    nx = max(1, int(usable_x_in // SPYDR_FIXTURE_LENGTH_IN))
    ny = max(1, int(usable_y_in // SPYDR_FIXTURE_WIDTH_IN))
    return nx, ny


def _hps_grid_counts(length_ft: float, width_ft: float, coverage_ft: float) -> tuple[int, int]:
    return hps_coverage_grid_counts(length_ft, width_ft, coverage_ft, DEFAULT_OUTER_MARGIN_IN)


def _auto_layout_mode(length_ft: float, width_ft: float) -> str:
    if length_ft <= 0 or width_ft <= 0:
        return "square"
    if abs(length_ft - width_ft) > 1e-6:
        return "rect_rect"
    return "square"


def _make_env_base(req: Any) -> dict[str, str]:
    env = _base_env()
    py = _resolve_python()
    env["PY"] = py
    env["PATH"] = f"{Path(py).parent}:{env['PATH']}"
    if _request_uses_docker(req):
        env["RAD_TMP"] = "/tmp/radiance-cache"
    try:
        length_ft, width_ft = _aligned_dims_ft(req.length_ft, req.width_ft)
    except Exception:
        length_ft, width_ft = float(req.length_ft), float(req.width_ft)
    env["LENGTH_FT"] = f"{length_ft:g}"
    env["WIDTH_FT"] = f"{width_ft:g}"
    env["ALIGN_LONG_AXIS_X"] = "1"
    env["MODE"] = req.sim_mode
    env["SUBPATCH_GRID"] = str(req.subpatch_grid)
    env["MOUNT_Z_M"] = f"{req.mount_z_m:g}"
    if req.dialux_sensor_grid:
        # DIALux edge-aligned grid: 15x15 points with sensors on the room edges.
        env["RESOLUTION_X"] = "15"
        env["RESOLUTION_Y"] = "15"
        env["GRID_SAMPLE_LAYOUT"] = "edge_aligned"
        env["GRID_WALL_MARGIN_M"] = "0.0005"
        env["WALL_MARGIN_M"] = "0.0005"
        env["GRID_MODULE_SIDE_M"] = "0.0"
        # Enforce left/right + front/back symmetry on the sampled grid.
        env["SYM"] = "1"
        env["AXES_ONLY"] = "1"
    else:
        env["GRID_SAMPLE_LAYOUT"] = "centered"
        env["GRID_TARGET_SPACING_M"] = f"{DEFAULT_SENSOR_GRID_SPACING_M:g}"
        env["GRID_MIN_POINTS_X"] = "5"
        env["GRID_MIN_POINTS_Y"] = "5"
    try:
        area_m2 = (length_ft * width_ft) * 0.09290304
        if area_m2 > 0:
            env["CANOPY_AREA_M2"] = f"{area_m2:.6f}"
    except Exception:
        pass
    _apply_plant_request_env(env, req)
    return env


def _apply_plant_request_env(env: dict[str, str], req: Any) -> None:
    for key in PLANT_ENV_KEYS:
        env.pop(key, None)
    if not bool(getattr(req, "plants_enabled", False)):
        return

    config = plant_geometry_config_from_request(req)
    optical = config.optical
    leaf_length_min, leaf_length_max = config.leaf_length_range_m
    leaf_width_min, leaf_width_max = config.leaf_width_range_m
    leaf_tilt_min, leaf_tilt_max = config.leaf_tilt_range_deg
    env.update(
        {
            "FSPM_PLANTS_ENABLED": "1",
            "FSPM_PLANT_SEED": str(config.seed),
            "FSPM_PLANT_ROWS": str(config.plant_grid_rows),
            "FSPM_PLANT_COLUMNS": str(config.plant_grid_columns),
            "FSPM_PLANT_SPACING_M": f"{config.plant_spacing_m:g}",
            "FSPM_PLANT_HEIGHT_M": f"{config.plant_height_m:g}",
            "FSPM_PLANT_CANOPY_RADIUS_M": f"{config.canopy_radius_m:g}",
            "FSPM_PLANT_LEAF_COUNT": str(config.leaf_count_per_plant),
            "FSPM_PLANT_LEAF_LENGTH_MIN_M": f"{leaf_length_min:g}",
            "FSPM_PLANT_LEAF_LENGTH_MAX_M": f"{leaf_length_max:g}",
            "FSPM_PLANT_LEAF_WIDTH_MIN_M": f"{leaf_width_min:g}",
            "FSPM_PLANT_LEAF_WIDTH_MAX_M": f"{leaf_width_max:g}",
            "FSPM_PLANT_LEAF_TILT_MIN_DEG": f"{leaf_tilt_min:g}",
            "FSPM_PLANT_LEAF_TILT_MAX_DEG": f"{leaf_tilt_max:g}",
            "FSPM_PLANT_CURVATURE_M": f"{config.leaf_curvature_m:g}",
            "FSPM_PLANT_GROWTH_STAGE": f"{config.growth_stage:g}",
            "FSPM_PLANT_REFLECTANCE": f"{optical.reflectance:g}",
            "FSPM_PLANT_TRANSMITTANCE": f"{optical.transmittance:g}",
            "FSPM_PLANT_ABSORPTANCE": f"{optical.absorptance:g}",
        }
    )


def _env_smd(req: Any) -> dict[str, str]:
    env = _make_env_base(req)
    env["MARGIN_IN"] = f"{DEFAULT_OUTER_MARGIN_IN:g}"
    env["LAYOUT_MODE"] = "exact_tiled"
    env["OPTICS"] = "stack"
    env["SMD_MODEL"] = "curve"
    env["SMD_MODULE_PROFILE"] = MODULE_PROFILE_VERSION
    env["PPE_IS_SYSTEM"] = "0"
    env["TARGET_PPFD"] = f"{req.target_ppfd:g}"
    env["LOG_CAP_METRICS"] = "1" if req.peak_capping_enabled else "0"
    env["RUN_BASIS"] = "1" if req.run_basis else "0"
    env["W_MIN"] = f"{req.w_min:g}"
    env["W_MAX"] = f"{req.w_max:g}"
    env["SMD_BASE_RING_N"] = str(req.smd_base_ring)
    env["PMMA_MODE"] = "1"
    env["PMMA_SIDE_M"] = f"{LID_SIDE_M:g}"
    env["PMMA_THK_M"] = f"{LID_THK_M:g}"
    env["PMMA_LID_OFFSET_M"] = f"{STACK_HEIGHT_M:g}"
    env["PTFE_MODE"] = "1"
    env["PTFE_REFLECTANCE"] = f"{PTFE_REFLECTANCE:g}"
    env["PTFE_THK_M"] = f"{PTFE_LINER_THK_M:g}"
    env["STACK_APERTURE_M"] = f"{GASKET_APERTURE_M:g}"
    env["SMD_WW_COUNT"] = "52"
    env["SMD_CW_COUNT"] = "52"
    env["SMD_RED_COUNT"] = "41"
    env["SMD_WW_NOMINAL_W"] = "0.68"
    env["SMD_CW_NOMINAL_W"] = "0.68"
    env["SMD_RED_NOMINAL_W"] = "0.44"
    env["SMD_WW_NOMINAL_PPE"] = "2.73"
    env["SMD_CW_NOMINAL_PPE"] = "2.81"
    env["SMD_RED_NOMINAL_PPE"] = "4.13"
    env["DRIVER_EFF"] = "0.96"
    env["WIRING_EFF"] = "0.99"
    env["SMD_PPE_REFERENCE_MODE"] = "nominal_current"
    env["SMD_THERMAL_REF_INPUT_W"] = "93.392256"
    env["SMD_THERMAL_REF_MULTIPLIER"] = "0.985"
    env["SMD_THERMAL_SLOPE_PER_W"] = "0.0015"
    env["SMD_THERMAL_MIN_MULTIPLIER"] = "0.90"
    env["SMD_THERMAL_MAX_MULTIPLIER"] = "1.00"
    env["SMD_WHITE_VF_CSV"] = str(DEFAULT_WHITE_VF_CSV)
    env["SMD_WHITE_PPE_CSV"] = str(DEFAULT_WHITE_PPE_CSV)
    env["SMD_RED_VF_CSV"] = str(DEFAULT_RED_VF_CSV)
    env["SMD_RED_PPE_CSV"] = str(DEFAULT_RED_PPE_CSV)
    env["SMD_TARGET_PPE_UMOL_PER_J"] = "0.0"
    env["SMD_BASIS_BACKEND"] = canonicalize_basis_backend(req.basis_backend)
    env["SOLVE_METHOD"] = "minvar_qp"
    env["SOLVE_MODE"] = "solver"
    for name in (
        "SMD_RCONTRIB_MCPT_AB",
        "SMD_RCONTRIB_MCPT_AD",
        "SMD_RCONTRIB_MCPT_LR",
        "SMD_RCONTRIB_MCPT_LW",
    ):
        value = os.getenv(name, "").strip()
        if value:
            env[name] = value
    if req.match_system_ppe:
        env["SMD_MODEL"] = "legacy"
        env["PPE_IS_SYSTEM"] = "1"
        env["SMD_TARGET_PPE_UMOL_PER_J"] = "2.700"
        env["EFF_SCALE"] = "1.0"
        env["DROOP_K"] = "0.0"
    return env


def _env_spydr(req: Any) -> dict[str, str]:
    env = _make_env_base(req)
    layout = canonical_competitor_layout(req.competitor_layout)
    nx, ny = _spydr_grid_counts(req.length_ft, req.width_ft, layout)
    env["NX"] = str(nx)
    env["NY"] = str(ny)
    env["MARGIN_IN"] = f"{DEFAULT_OUTER_MARGIN_IN:g}"
    env["SPYDR_PPF"] = f"{req.sp_ppf:g}"
    env["SPYDR_Z_M"] = f"{req.sp_z_m:g}"
    env["EFF_SCALE"] = "1.0"
    env["SPYDR_PPE_UMOL_PER_J"] = f"{req.sp_ppe:g}"
    env["SPYDR_LAYOUT_MODE"] = layout
    env["SYM"] = "0"
    if layout == COMPETITOR_LAYOUT_PRACTICAL:
        _nx, _ny, target_gap_x_in, target_gap_y_in, actual_gap_x_in, actual_gap_y_in = _spydr_practical_layout_metrics_in(req.length_ft, req.width_ft)
        env["SPYDR_TARGET_GAP_X_IN"] = f"{target_gap_x_in:g}"
        env["SPYDR_TARGET_GAP_Y_IN"] = f"{target_gap_y_in:g}"
        env["SPYDR_ACTUAL_GAP_X_IN"] = f"{actual_gap_x_in:g}"
        env["SPYDR_ACTUAL_GAP_Y_IN"] = f"{actual_gap_y_in:g}"
    env["TARGET_PPFD"] = f"{req.target_ppfd:g}"
    env["ALIGN_LONG_AXIS_X"] = "1"
    env["AUTO_DIM_MODE"] = "scale"
    env["AUTO_DIM"] = "1"
    env["AUTO_DIM_TARGET"] = "peak" if req.peak_capping_enabled else "mean"
    env["LOG_CAP_METRICS"] = "1" if req.peak_capping_enabled else "0"
    if req.match_system_ppe:
        env["SPYDR_PPE_UMOL_PER_J"] = f"{COMPETITOR_FIXTURE_PPE_UMOL_PER_J:.15g}"
        env["SPYDR_DROOP"] = "0"
    return env


def _env_hps(req: Any) -> dict[str, str]:
    env = _make_env_base(req)
    nx, ny = _hps_grid_counts(req.length_ft, req.width_ft, req.hps_coverage_ft)
    env["NX"] = str(nx)
    env["NY"] = str(ny)
    env["MARGIN_IN"] = f"{DEFAULT_OUTER_MARGIN_IN:g}"
    env["HPS_COVERAGE_FT"] = f"{req.hps_coverage_ft:g}"
    env["HPS_Z_M"] = f"{req.hps_z_m:g}"
    env["HPS_FIXTURE_PPF"] = f"{req.hps_fixture_ppf:g}"
    env["HPS_INPUT_WATTS"] = f"{req.hps_input_watts:g}"
    env["HPS_IES_VARIANT"] = normalize_hps_ies_variant(
        getattr(req, "hps_ies_variant", DEFAULT_HPS_IES_VARIANT)
    )
    env["TARGET_PPFD"] = f"{req.target_ppfd:g}"
    env["EFF_SCALE"] = "1.0"
    env["AUTO_DIM_MODE"] = "scale"
    env["LOG_CAP_METRICS"] = "0"
    return env


def _output_dir_for_mode(mode: str) -> str:
    return _config_output_dir_for_mode(mode)


def _resolve_output_dir_path(mode: str, base_root: Path | None = None) -> Path:
    if base_root is None or base_root == ROOT:
        return VISUALIZATION_ROOT / _output_dir_for_mode(mode)
    root = base_root
    return root / _output_dir_for_mode(mode)


def _overlay_for_mode(mode: str, overlay: str) -> str:
    return _config_overlay_for_mode(mode, overlay)


def _normalize_mode(mode: str) -> str:
    try:
        return canonicalize_radiance_mode(mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _canonicalize_mode_request(req: Any) -> RadianceRunRequest:
    data = req.model_dump() if hasattr(req, "model_dump") else dict(vars(req))
    mode = _normalize_mode(_contract_str(data.get("mode", MODE_SMD), MODE_SMD))
    execution_mode = _canonicalize_execution_mode(_contract_str(data.get("execution_mode", DEFAULT_EXECUTION_MODE), DEFAULT_EXECUTION_MODE))
    sim_mode = _canonicalize_quality_preset(_contract_str(data.get("sim_mode", "standard"), "standard"))
    competitor_layout = canonical_competitor_layout(_contract_str(data.get("competitor_layout", COMPETITOR_LAYOUT_FULL), COMPETITOR_LAYOUT_FULL))
    try:
        hps_ies_variant = normalize_hps_ies_variant(_contract_str(data.get("hps_ies_variant", DEFAULT_HPS_IES_VARIANT), DEFAULT_HPS_IES_VARIANT))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        basis_backend = validate_basis_backend_request(
            mode,
            _contract_str(data.get("basis_backend", DEFAULT_SMD_BASIS_BACKEND), DEFAULT_SMD_BASIS_BACKEND),
            variable_mode="rings",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data.update(
        {
            "mode": mode,
            "execution_mode": execution_mode,
            "sim_mode": sim_mode,
            "competitor_layout": competitor_layout,
            "hps_ies_variant": hps_ies_variant,
            "basis_backend": basis_backend,
            "peak_capping_enabled": bool(data.get("peak_capping_enabled", False)),
        }
    )
    if mode == HPS_MODE_LABEL:
        data["match_system_ppe"] = False
        data["peak_capping_enabled"] = False
    if mode != MODE_COMPETITOR:
        data["competitor_layout"] = COMPETITOR_LAYOUT_FULL
    return RadianceRunRequest.model_construct(**data)


def _apply_visualize_env(env: dict[str, str]) -> None:
    env.setdefault("MPLBACKEND", "Agg")
    env.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")


def _env_for_mode(req: Any) -> dict[str, str]:
    if req.mode == MODE_COMPETITOR:
        return _env_spydr(req)
    if req.mode == HPS_MODE_LABEL:
        return _env_hps(req)
    return _env_smd(req)
