from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rad_rebuild.radiance.engine.emitters.smd_generation.module_profile import (
    BEZEL_POCKET_M,
    GASKET_APERTURE_M,
    LID_SIDE_M,
    LID_THK_M,
    PTFE_LINER_THK_M,
    PTFE_REFLECTANCE,
    STACK_HEIGHT_M,
    WINDOW_SIDE_M,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.source_variants import (
    DEFAULT_SMD_SOURCE_VARIANT,
    get_smd_source_variant,
    normalize_smd_source_variant,
)


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return float(default)
    return float(value)


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return int(default)
    return int(value)


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return bool(default)
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")


@dataclass(frozen=True)
class SmdGeometryConfig:
    length_m: float
    width_m: float
    height_m: float
    wall_margin_m: float
    ring_n: int
    rings: int
    base_ring_n: int
    mount_z_m: float
    patch_side_m: float
    module_footprint_x_m: float
    module_footprint_y_m: float
    fixed_pitch_m: float
    subpatch_grid: int
    fixture_angle_in: float
    fixture_angle_m: float
    layout_mode: str
    perimeter_gap_fill: bool
    outer_per_module: bool


@dataclass(frozen=True)
class SmdOpticalStackConfig:
    pmma_mode: bool
    pmma_ior: float
    pmma_t: float
    pmma_thk_m: float
    pmma_side_m: float
    pmma_lid_offset_m: float
    pmma_abs_m: float
    ptfe_mode: bool
    ptfe_reflectance: float
    ptfe_thk_m: float
    stack_aperture_m: float
    stack_pocket_m: float


@dataclass(frozen=True)
class SmdElectricalConfig:
    ppe_is_system_raw: bool
    driver_eff: float
    thermal_eff: float
    board_opt_eff: float
    wiring_eff: float
    user_eff_scale: float
    target_system_ppe: float
    droop_p_nom: float
    droop_k: float
    model: str
    use_curve_model: bool
    curve_debug: bool
    debug_ring: int
    debug_module_idx: int


@dataclass(frozen=True)
class SmdBasisConfig:
    basis_mode: bool
    basis_ring: int
    basis_unit_w: float
    basis_outer_module_idx: int
    basis_module_idx: int
    rcontrib_basis_mode: bool
    modifier_grouping: str
    group_modifiers_by_ring: bool


@dataclass(frozen=True)
class SmdSourceConfig:
    optics_mode: str
    source_variant: str
    source_variant_meta: Any


@dataclass(frozen=True)
class SmdOutputConfig:
    out_dir: Path
    source_variant_cal_name: str
    source_variant_cal_file: Path
    smd_json: Path


@dataclass(frozen=True)
class RingPowerOverrideConfig:
    use_json: bool
    require_json: bool
    path: Path


def compute_pmma_absorption_m(
    pmma_ior: float, pmma_t: float, pmma_thk_m: float
) -> float:
    pmma_r0 = ((pmma_ior - 1.0) / (pmma_ior + 1.0)) ** 2
    pmma_tsurfaces = (1.0 - pmma_r0) ** 2
    pmma_tbulk = 0.0
    if pmma_t > 0 and pmma_tsurfaces > 0:
        pmma_tbulk = min(1.0, pmma_t / pmma_tsurfaces)
    return (
        0.0
        if pmma_thk_m <= 0 or pmma_tbulk <= 0
        else -math.log(pmma_tbulk) / pmma_thk_m
    )


def load_geometry_config() -> SmdGeometryConfig:
    ring_n = env_int("SMD_RING_N", 7)
    fixture_angle_in = env_float("SMD_FIXTURE_ANGLE_IN", 0.25)
    return SmdGeometryConfig(
        length_m=env_float("LENGTH_FT", 12.0) * 0.3048,
        width_m=env_float("WIDTH_FT", 12.0) * 0.3048,
        height_m=env_float("HEIGHT_M", 3.048),
        wall_margin_m=env_float("MARGIN_IN", 1.0) * 0.0254,
        ring_n=ring_n,
        rings=max(1, ring_n + 1),
        base_ring_n=env_int("SMD_BASE_RING_N", 7),
        mount_z_m=env_float("MOUNT_Z_M", 0.4572),
        patch_side_m=env_float(
            "PATCH_SIDE_M", env_float("MODULE_SIDE_M", WINDOW_SIDE_M)
        ),
        module_footprint_x_m=env_float("MODULE_FOOTPRINT_X_M", 0.1524),
        module_footprint_y_m=env_float("MODULE_FOOTPRINT_Y_M", 0.1650),
        fixed_pitch_m=env_float("SMD_FIXED_PITCH_M", 0.0),
        subpatch_grid=env_int("SUBPATCH_GRID", 3),
        fixture_angle_in=fixture_angle_in,
        fixture_angle_m=float(fixture_angle_in) * 0.0254,
        layout_mode=os.getenv("LAYOUT_MODE", "square").strip().lower(),
        perimeter_gap_fill=env_bool("SMD_PERIM_GAP_FILL", False),
        outer_per_module=env_bool("SMD_OUTER_PER_MODULE", False),
    )


def load_optical_stack_config() -> SmdOpticalStackConfig:
    pmma_ior = env_float("PMMA_IOR", 1.49)
    pmma_t = env_float("PMMA_T", 0.92)
    pmma_thk_m = env_float("PMMA_THK_M", LID_THK_M)
    return SmdOpticalStackConfig(
        pmma_mode=env_bool("PMMA_MODE", True),
        pmma_ior=pmma_ior,
        pmma_t=pmma_t,
        pmma_thk_m=pmma_thk_m,
        pmma_side_m=env_float("PMMA_SIDE_M", LID_SIDE_M),
        pmma_lid_offset_m=env_float("PMMA_LID_OFFSET_M", STACK_HEIGHT_M),
        pmma_abs_m=compute_pmma_absorption_m(pmma_ior, pmma_t, pmma_thk_m),
        ptfe_mode=env_bool("PTFE_MODE", True),
        ptfe_reflectance=env_float("PTFE_REFLECTANCE", PTFE_REFLECTANCE),
        ptfe_thk_m=env_float("PTFE_THK_M", PTFE_LINER_THK_M),
        stack_aperture_m=env_float("STACK_APERTURE_M", GASKET_APERTURE_M),
        stack_pocket_m=env_float("STACK_POCKET_M", BEZEL_POCKET_M),
    )


def load_electrical_config(pmma_mode: bool) -> SmdElectricalConfig:
    board_opt_eff = env_float("BOARD_OPT_EFF", 1.00)
    if pmma_mode and "BOARD_OPT_EFF" not in os.environ:
        board_opt_eff = 1.00
    model = os.getenv("SMD_MODEL", "curve").strip().lower() or "curve"
    return SmdElectricalConfig(
        ppe_is_system_raw=os.getenv("PPE_IS_SYSTEM", "0").strip() != "0",
        driver_eff=env_float("DRIVER_EFF", 0.96),
        thermal_eff=env_float("THERMAL_EFF", 1.00),
        board_opt_eff=board_opt_eff,
        wiring_eff=env_float("WIRING_EFF", 0.99),
        user_eff_scale=env_float("EFF_SCALE", 1.00),
        target_system_ppe=env_float("SMD_TARGET_PPE_UMOL_PER_J", 0.0),
        droop_p_nom=env_float("DROOP_P_NOM", 100.0),
        droop_k=env_float("DROOP_K", 0.12),
        model=model,
        use_curve_model=model not in {"legacy", "heuristic", "heuristic_droop", "old"},
        curve_debug=env_bool("SMD_CURVE_DEBUG", False),
        debug_ring=env_int("SMD_DEBUG_RING", 0),
        debug_module_idx=env_int("SMD_DEBUG_MODULE_IDX", -1),
    )


def load_basis_config() -> SmdBasisConfig:
    basis_outer_module_idx = int(os.getenv("SMD_BASIS_OUTER_MODULE_IDX", "-1"))
    basis_module_idx = int(
        os.getenv("SMD_BASIS_MODULE_IDX", str(basis_outer_module_idx))
    )
    rcontrib_basis_mode = os.getenv("SMD_RCONTRIB_BASIS_MODE", "0") == "1"
    modifier_grouping = (os.getenv("SMD_MODIFIER_GROUPING", "") or "").strip().lower()
    return SmdBasisConfig(
        basis_mode=os.getenv("SMD_BASIS_MODE", "0") == "1",
        basis_ring=int(os.getenv("SMD_BASIS_RING", "-1")),
        basis_unit_w=float(os.getenv("SMD_BASIS_UNIT_W", "1.0")),
        basis_outer_module_idx=basis_outer_module_idx,
        basis_module_idx=basis_module_idx,
        rcontrib_basis_mode=rcontrib_basis_mode,
        modifier_grouping=modifier_grouping,
        group_modifiers_by_ring=rcontrib_basis_mode
        or modifier_grouping in {"ring", "rings", "per_ring"},
    )


def load_source_config() -> SmdSourceConfig:
    source_variant = normalize_smd_source_variant(
        os.getenv("SMD_SOURCE_VARIANT", DEFAULT_SMD_SOURCE_VARIANT)
    )
    optics_mode = validate_optics_mode(os.getenv("OPTICS", "stack"))
    return SmdSourceConfig(
        optics_mode=optics_mode,
        source_variant=source_variant,
        source_variant_meta=get_smd_source_variant(source_variant),
    )


def validate_optics_mode(value: str | None) -> str:
    optics_mode = (
        str(value if value is not None else "stack").strip().lower() or "stack"
    )
    if optics_mode == "stack":
        return optics_mode
    raise ValueError(
        f"Unsupported SMD OPTICS={optics_mode!r}; only 'stack' is supported."
    )


def load_output_config() -> SmdOutputConfig:
    out_dir = Path(os.getenv("RADIANCE_RUNTIME_STATE_ROOT", "runtime_state"))
    out_dir.mkdir(parents=True, exist_ok=True)
    source_variant_cal_name = "smd_source_variant.cal"
    return SmdOutputConfig(
        out_dir=out_dir,
        source_variant_cal_name=source_variant_cal_name,
        source_variant_cal_file=out_dir / source_variant_cal_name,
        smd_json=out_dir / "smd_layout.json",
    )


def load_ring_power_override_config() -> RingPowerOverrideConfig:
    return RingPowerOverrideConfig(
        use_json=os.getenv("USE_RING_POWERS_JSON", "0") != "0",
        require_json=os.getenv("RING_POWERS_STRICT", "0") != "0",
        path=Path(os.getenv("RING_POWERS_JSON", "ring_powers_optimized.json")),
    )
