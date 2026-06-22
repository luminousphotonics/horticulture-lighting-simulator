#!/usr/bin/env python3
# generate_emitters_smd.py • v3.5
# - Active optics: OPTICS=stack PMMA/PTFE physical stack surrogate
# - Per-run layout snapshot: runtime_state/smd_layout.json (meters)

from __future__ import annotations

import json
import math
import os
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, TextIO, cast
from rad_rebuild.radiance.engine.emitters.smd_generation.module_profile import (
    CHANNEL_COUNTS,
    MODULE_PROFILE_VERSION,
    PER_LED_W as PROFILE_PER_LED_W,
    PPE_UMOL_PER_J as PROFILE_PPE_UMOL_PER_J,
    module_profile_meta,
)
from rad_rebuild.radiance.engine.emitters.deterministic_ids import radiance_identifier
from rad_rebuild.radiance.engine.photometry.smd_curve_model import (
    CURVE_MODEL_VERSION,
    ModuleState,
    load_smd_curve_model,
    validate_curve_model,
)
from rad_rebuild.radiance.engine.simulation.basis_backends import (
    basis_ring_modifier_name,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.solution_metadata import (
    _sha256_file,
    build_smd_runtime_fingerprint_from_env,
    compare_smd_runtime_fingerprints,
    format_fingerprint_mismatch,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.source_variants import (
    write_smd_source_variant_cal,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.config import (
    env_float as _env_float,
    load_basis_config,
    load_electrical_config,
    load_geometry_config,
    load_optical_stack_config,
    load_output_config,
    load_ring_power_override_config,
    load_source_config,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.optical_stack import (
    write_pmma_lid,
    write_stack_cavity,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.outputs import (
    build_runtime_config_snapshot,
    build_smd_summary_text,
    format_per_module_stats,
    format_smd_optics_desc,
    format_smd_pmma_desc,
    format_smd_power_mode_desc,
    format_smd_ptfe_desc,
    format_smd_spacing,
    read_smd_layout_json as _read_smd_layout_json,
    write_smd_layout_json,
    write_smd_summary,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.power_schedule import (
    basis_ring_power_schedule,
    default_ring_power_schedule,
    expand_ring_power_schedule,
    module_basis_ring_power_schedule,
    ring_power_for,
    uniform_ring_power_schedule,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.fixture_overlay import (
    SmdFixtureOverlaySettings,
    build_fixture_overlay,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.photons import (
    compute_droop_scale as _compute_droop_scale,
    get_ring_watts_and_counts as _get_ring_watts_and_counts,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.positions import (
    PerimeterGapEdges as PerimeterGapEdges,
    PerimeterGapGeometry as PerimeterGapGeometry,
    SmdPositionContext as SmdPositionContext,
    SmdPositionSettings,
    _apply_perimeter_gap_fill as _apply_perimeter_gap_fill,
    compute_positions_from_env,
    get_module_positions as _get_module_positions_for_settings,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.power_overrides import (
    RingPowerOverrideUpdates,
    ring_power_override_updates,
    unique_ints_or_empty,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.rad_writer import (
    write_area_grid,
    write_area_rect,
    write_area_rect_grid,
    write_area_square,
    write_brightfunc_ref,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.summary import (
    curve_debug_text as _summary_curve_debug_text,
    photon_totals as _summary_photon_totals,
    ring_power_summary as _summary_ring_power_summary,
)

PositionRecord = dict[str, Any]
JsonObject = dict[str, Any]


@dataclass(frozen=True)
class SmdEmitterConfig:
    """Boundary config for SMD emitter artifact generation.

    Environment variables are still accepted for CLI/script compatibility, but
    this config is the typed orchestration seam for Phase 11 decomposition.  The
    scientific source construction below continues to use the already-loaded
    module configuration so this first split does not change formulas.
    """

    output_dir: Path
    layout_json: Path

    @classmethod
    def from_loaded_environment(cls) -> "SmdEmitterConfig":
        return cls(output_dir=OUT_DIR, layout_json=SMD_JSON)


@dataclass(frozen=True)
class SmdEmitterEvent:
    level: str
    message: str


@dataclass(frozen=True)
class SmdEmitterResult:
    emitter_rad: Path
    summary_txt: Path
    layout_json: Path
    module_count: int
    ring_count: int
    total_input_w: float
    total_effective_w: float
    total_source_umol_s: float
    total_output_umol_s: float
    events: tuple[SmdEmitterEvent, ...]


@dataclass(frozen=True)
class SmdLayoutStage:
    positions: list[PositionRecord]
    spacing: float
    meta: JsonObject
    layout_mode: Any
    pitch_x: Any
    pitch_y: Any
    rings_local: int


@dataclass(frozen=True)
class SmdPowerStage:
    ring_counts: Counter[int]
    outer_ring: int
    outer_override_map: dict[int, float]
    module_override_map: dict[int, float]
    per_mod_w: list[float] | None
    per_module_mode: bool


@dataclass(frozen=True)
class SmdLossStage:
    droop_scale: float
    per_mod_rad: list[float]


@dataclass(frozen=True)
class SmdSummaryStage:
    summary_path: Path
    summary_text: str
    power_mode_desc: str
    total_w_in: float
    total_w_eff: float
    total_umol: float
    total_output_umol: float


# ──────────────────────────────────────────────────────────────────────────────
# Environment → geometry
# ──────────────────────────────────────────────────────────────────────────────
GEOMETRY_CONFIG = load_geometry_config()
OPTICAL_STACK_CONFIG = load_optical_stack_config()

# Room (feet/inches) → meters
LENGTH_M = GEOMETRY_CONFIG.length_m
WIDTH_M = GEOMETRY_CONFIG.width_m
HEIGHT_M = GEOMETRY_CONFIG.height_m
WALL_MARGIN_M = GEOMETRY_CONFIG.wall_margin_m

# Rings: RING_N = n (outer index), RINGS = n+1 (0..n)
RING_N = GEOMETRY_CONFIG.ring_n  # GUI sets this directly or via density → ring count
RINGS = GEOMETRY_CONFIG.rings
SMD_BASE_RING_N = GEOMETRY_CONFIG.base_ring_n

# Source mounting plane.
MOUNT_Z_M = GEOMETRY_CONFIG.mount_z_m  # 18 in

# Macro patch footprint (meters)
# PATCH_SIDE_M = emitting surface; MODULE_FOOTPRINT_* = heatsink footprint for spacing.
PATCH_SIDE_M = GEOMETRY_CONFIG.patch_side_m
MODULE_FOOTPRINT_X_M = GEOMETRY_CONFIG.module_footprint_x_m
MODULE_FOOTPRINT_Y_M = GEOMETRY_CONFIG.module_footprint_y_m
SMD_FIXED_PITCH_M = GEOMETRY_CONFIG.fixed_pitch_m
SUBPATCH_GRID = (
    GEOMETRY_CONFIG.subpatch_grid
)  # subdiv to distribute emission over board
SMD_FIXTURE_ANGLE_IN = GEOMETRY_CONFIG.fixture_angle_in
SMD_FIXTURE_ANGLE_M = GEOMETRY_CONFIG.fixture_angle_m
PMMA_MODE = OPTICAL_STACK_CONFIG.pmma_mode
PMMA_IOR = OPTICAL_STACK_CONFIG.pmma_ior
PMMA_T = OPTICAL_STACK_CONFIG.pmma_t
PMMA_THK_M = OPTICAL_STACK_CONFIG.pmma_thk_m
PMMA_SIDE_M = OPTICAL_STACK_CONFIG.pmma_side_m
PMMA_LID_OFFSET_M = OPTICAL_STACK_CONFIG.pmma_lid_offset_m
PTFE_MODE = OPTICAL_STACK_CONFIG.ptfe_mode
PTFE_REFLECTANCE_LOCAL = OPTICAL_STACK_CONFIG.ptfe_reflectance
PTFE_THK_M = OPTICAL_STACK_CONFIG.ptfe_thk_m
STACK_APERTURE_M = OPTICAL_STACK_CONFIG.stack_aperture_m
STACK_POCKET_M = OPTICAL_STACK_CONFIG.stack_pocket_m
PMMA_ABS_M = OPTICAL_STACK_CONFIG.pmma_abs_m

# Layout mode: 'square', 'rect_rect', 'grid', or 'exact_tiled' (horticultural tile/connect/extend)
LAYOUT_MODE = GEOMETRY_CONFIG.layout_mode
SMD_PERIM_GAP_FILL = GEOMETRY_CONFIG.perimeter_gap_fill
SMD_OUTER_PER_MODULE = GEOMETRY_CONFIG.outer_per_module

# ──────────────────────────────────────────────────────────────────────────────
# Electrical / spectral model
# ──────────────────────────────────────────────────────────────────────────────
COUNT_PER_MODULE: Dict[str, int] = dict(CHANNEL_COUNTS)

PER_LED_W: Dict[str, float] = dict(PROFILE_PER_LED_W)

PPE_UMOL_PER_J: Dict[str, float] = dict(PROFILE_PPE_UMOL_PER_J)

RING_POWER_BY_RING: Dict[int, float] = dict(default_ring_power_schedule().watts_by_ring)
RING_POWERS_SOURCE = "built-in"
RING_POWERS_COMPAT_STATUS = "not-used"
RING_POWERS_COMPAT_MESSAGE = ""


def _ensure_ring_power_entries(required_rings: int | None = None) -> None:
    """Ensure every active layout ring has an explicit power entry."""
    target_rings = int(required_rings if required_rings is not None else RINGS)
    RING_POWER_BY_RING.update(
        expand_ring_power_schedule(RING_POWER_BY_RING, target_rings)
    )


def _ring_power_for(ring: int) -> float:
    _ensure_ring_power_entries(max(RINGS, int(ring) + 1))
    return ring_power_for(RING_POWER_BY_RING, ring)


def _apply_basis_ring_powers(required_rings: int) -> None:
    target_rings = max(0, int(required_rings))
    if BASIS_RING >= 0 and BASIS_MODULE_IDX < 0:
        RING_POWER_BY_RING.update(
            basis_ring_power_schedule(target_rings, BASIS_RING, BASIS_UNIT_W)
        )
        return
    if BASIS_MODULE_IDX >= 0:
        RING_POWER_BY_RING.update(module_basis_ring_power_schedule(target_rings))


# Global derating
# Important: PPE_UMOL_PER_J can either represent:
#   (A) fixture/system-level PPE (already includes driver/thermal/optical losses), or
#   (B) LED/board-level PPE (needs additional derate multipliers applied).
#
# For SMD we default to (B) so PPE is board-level by default; set
# PPE_IS_SYSTEM=1 if your PPE values are already system-level.
ELECTRICAL_CONFIG = load_electrical_config(PMMA_MODE)

PPE_IS_SYSTEM_RAW = ELECTRICAL_CONFIG.ppe_is_system_raw
PPE_IS_SYSTEM = PPE_IS_SYSTEM_RAW

DRIVER_EFF = ELECTRICAL_CONFIG.driver_eff
THERMAL_EFF = ELECTRICAL_CONFIG.thermal_eff
BOARD_OPT_EFF = ELECTRICAL_CONFIG.board_opt_eff
WIRING_EFF = ELECTRICAL_CONFIG.wiring_eff
USER_EFF_SCALE = ELECTRICAL_CONFIG.user_eff_scale  # dimmer fraction (0..1)
TARGET_SYSTEM_PPE = ELECTRICAL_CONFIG.target_system_ppe
DROOP_P_NOM = ELECTRICAL_CONFIG.droop_p_nom
DROOP_K = ELECTRICAL_CONFIG.droop_k
DROOP_SCALE = 1.0
SMD_MODEL = ELECTRICAL_CONFIG.model
USE_CURVE_MODEL = ELECTRICAL_CONFIG.use_curve_model
SMD_CURVE_DEBUG = ELECTRICAL_CONFIG.curve_debug
SMD_DEBUG_RING = ELECTRICAL_CONFIG.debug_ring
SMD_DEBUG_MODULE_IDX = ELECTRICAL_CONFIG.debug_module_idx
CURVE_RUNTIME_NOTES: List[str] = []

if USE_CURVE_MODEL and PPE_IS_SYSTEM:
    CURVE_RUNTIME_NOTES.append("forced PPE_IS_SYSTEM=0 because SMD_MODEL=curve")
    PPE_IS_SYSTEM = False

if TARGET_SYSTEM_PPE > 0.0 and not USE_CURVE_MODEL:
    base_nom_w = sum(COUNT_PER_MODULE[ch] * PER_LED_W[ch] for ch in COUNT_PER_MODULE)
    base_ppf = sum(
        COUNT_PER_MODULE[ch] * PER_LED_W[ch] * PPE_UMOL_PER_J[ch]
        for ch in COUNT_PER_MODULE
    )
    base_ppe = (base_ppf / base_nom_w) if base_nom_w > 0 else 0.0
    if base_ppe > 0:
        USER_EFF_SCALE = TARGET_SYSTEM_PPE / base_ppe
        if USER_EFF_SCALE < 0.0:
            USER_EFF_SCALE = 0.0
        if USER_EFF_SCALE > 1.0:
            print(
                f"NOTE: SMD_TARGET_PPE_UMOL_PER_J={TARGET_SYSTEM_PPE:.3f} exceeds base PPE "
                f"{base_ppe:.3f}; clamping EFF_SCALE to 1.0."
            )
            USER_EFF_SCALE = 1.0
    else:
        print("WARNING: Unable to compute base PPE for SMD target PPE scaling.")
    PPE_IS_SYSTEM = True
elif TARGET_SYSTEM_PPE > 0.0 and USE_CURVE_MODEL:
    CURVE_RUNTIME_NOTES.append(
        "ignoring SMD_TARGET_PPE_UMOL_PER_J because SMD_MODEL=curve uses the CSV-driven package model"
    )
    print(
        "NOTE: ignoring SMD_TARGET_PPE_UMOL_PER_J because SMD_MODEL=curve uses the CSV-driven package model."
    )

if PPE_IS_SYSTEM:
    LOSS_SCALE = USER_EFF_SCALE
else:
    LOSS_SCALE = DRIVER_EFF * THERMAL_EFF * BOARD_OPT_EFF * WIRING_EFF * USER_EFF_SCALE

EFF_SCALE = LOSS_SCALE
CURVE_MODEL = load_smd_curve_model() if USE_CURVE_MODEL else None
CURVE_MODEL_VALIDATION = validate_curve_model() if USE_CURVE_MODEL else None

# ----------------------------------------------------------------------
# Basis-mode overrides for building A (per-ring response matrix)
# ----------------------------------------------------------------------
# When SMD_BASIS_MODE=1, we:
#   - Force one ring (SMD_BASIS_RING) to SMD_BASIS_UNIT_W (per module)
#   - Force all other rings to 0 W
#   - Optionally neutralize derates (SMD_BASIS_UNDERRATE=1 → EFF_SCALE=1.0)
BASIS_CONFIG = load_basis_config()

BASIS_MODE = BASIS_CONFIG.basis_mode
BASIS_RING = BASIS_CONFIG.basis_ring
BASIS_UNIT_W = BASIS_CONFIG.basis_unit_w
BASIS_OUTER_MODULE_IDX = BASIS_CONFIG.basis_outer_module_idx
BASIS_MODULE_IDX = BASIS_CONFIG.basis_module_idx
RCONTRIB_BASIS_MODE = BASIS_CONFIG.rcontrib_basis_mode
MODIFIER_GROUPING = BASIS_CONFIG.modifier_grouping
GROUP_MODIFIERS_BY_RING = BASIS_CONFIG.group_modifiers_by_ring

PER_MODULE_OUTER_INDICES: List[int] = []
PER_MODULE_OUTER_POWERS: List[float] = []
PER_MODULE_INDICES: List[int] = []
PER_MODULE_POWERS: List[float] = []
OUTER_RING_INDEX_JSON: int | None = None

_basis_target_rings = max(RINGS, BASIS_RING + 1 if BASIS_RING >= 0 else 0)
_ensure_ring_power_entries(_basis_target_rings)

if BASIS_MODE:
    _apply_basis_ring_powers(_basis_target_rings)
    if BASIS_RING >= 0 and BASIS_MODULE_IDX < 0:
        RING_POWERS_SOURCE = f"basis_mode_ring_{BASIS_RING}"
    elif BASIS_MODULE_IDX >= 0:
        RING_POWERS_SOURCE = f"basis_mode_module_idx_{BASIS_MODULE_IDX}"
elif RCONTRIB_BASIS_MODE:
    RING_POWER_BY_RING.update(
        uniform_ring_power_schedule(_basis_target_rings, BASIS_UNIT_W)
    )
    RING_POWERS_SOURCE = f"rcontrib_basis_all_rings_unit_{BASIS_UNIT_W:g}W"


def _validate_ring_power_payload(
    path: Path,
    data: JsonObject,
    positions: list[PositionRecord],
    meta: JsonObject,
) -> None:
    reasons = _ring_power_payload_reasons(data, positions, meta)
    if reasons:
        mismatch = format_fingerprint_mismatch(reasons)
        raise RuntimeError(
            f"Incompatible ring powers JSON {path}: {mismatch}. "
            "Re-solve the optimized powers under the current SMD model before reusing them."
        )


def _ring_power_payload_reasons(
    data: JsonObject,
    positions: list[PositionRecord],
    meta: JsonObject,
) -> list[str]:
    current_fp = build_smd_runtime_fingerprint_from_env(
        layout_meta=meta, module_count=len(positions)
    )
    solution_meta = data.get("smd_solution_metadata")
    saved_fp = (
        solution_meta.get("runtime_fingerprint")
        if isinstance(solution_meta, dict)
        else None
    )
    reasons = compare_smd_runtime_fingerprints(saved_fp, current_fp)
    current_model = (
        str(
            (
                (current_fp.get("emitter_env") if isinstance(current_fp, dict) else {})
                or {}
            ).get("SMD_MODEL", "curve")
        )
        .strip()
        .lower()
    )
    reasons = compare_smd_runtime_fingerprints(saved_fp, current_fp)
    _append_curve_solve_reasons(data, current_model, reasons)
    _append_basis_hash_reasons(solution_meta, reasons)
    _append_ring_coverage_reasons(data, int(meta.get("rings", RINGS) or RINGS), reasons)
    _append_module_coverage_reasons(data, len(positions), reasons)
    _append_outer_index_reasons(data, len(positions), reasons)
    return reasons


def _append_curve_solve_reasons(
    data: JsonObject, current_model: str, reasons: list[str]
) -> None:
    solve_space = str(data.get("solve_space", "") or "")
    if current_model != "legacy":
        if solve_space != "curve_source_photon_scale":
            reasons.append("optimized powers were not solved in curve photon space")
        if not data.get("basis_coefficients"):
            reasons.append("optimized powers missing curve solve coefficients")


def _append_basis_hash_reasons(solution_meta: object, reasons: list[str]) -> None:
    if isinstance(solution_meta, dict):
        basis_root = Path(os.getenv("RADIANCE_BASIS_OUTPUT_ROOT", "."))
        current_basis_manifest = basis_root / "basis_manifest.json"
        current_basis_file = basis_root / "basis_A.npy"
        saved_basis_manifest_sha = str(
            solution_meta.get("basis_manifest_sha256", "") or ""
        )
        saved_basis_file_sha = str(solution_meta.get("basis_file_sha256", "") or "")
        if current_basis_manifest.exists() and saved_basis_manifest_sha:
            current_basis_manifest_sha = _sha256_file(current_basis_manifest)
            if current_basis_manifest_sha != saved_basis_manifest_sha:
                reasons.append("basis manifest changed since powers were solved")
        if current_basis_file.exists() and saved_basis_file_sha:
            current_basis_file_sha = _sha256_file(current_basis_file)
            if current_basis_file_sha != saved_basis_file_sha:
                reasons.append("basis matrix changed since powers were solved")


def _unique_ints_or_empty(values: object) -> list[int]:
    return unique_ints_or_empty(values)


def _append_ring_coverage_reasons(
    data: JsonObject, expected_rings: int, reasons: list[str]
) -> None:
    ring_idxs = data.get("ring_indices")
    ring_powers = data.get("ring_powers_W_per_module") or data.get("ring_powers")
    if ring_powers is not None:
        if ring_idxs is None:
            ring_idxs = list(range(len(ring_powers)))
        ring_idx_list = _unique_ints_or_empty(ring_idxs)
        if ring_idx_list != list(range(expected_rings)):
            reasons.append(
                f"ring index coverage changed (json={ring_idx_list}, runtime={list(range(expected_rings))})"
            )


def _append_module_coverage_reasons(
    data: JsonObject, module_count: int, reasons: list[str]
) -> None:
    module_idxs = data.get("module_indices")
    module_powers = data.get("module_powers_W_per_module") or data.get(
        "module_powers_W"
    )
    if module_powers is not None:
        if module_idxs is None:
            module_idxs = list(range(len(module_powers)))
        module_idx_list = _unique_ints_or_empty(module_idxs)
        if module_idx_list != list(range(module_count)):
            reasons.append(
                f"module index coverage changed (json={len(module_idx_list)} modules, runtime={module_count} modules)"
            )


def _append_outer_index_reasons(
    data: JsonObject, module_count: int, reasons: list[str]
) -> None:
    outer_idxs = data.get("outer_ring_indices") or []
    if outer_idxs:
        invalid = [
            index
            for index in _unique_ints_or_empty(outer_idxs)
            if index < 0 or index >= module_count
        ]
        if not invalid and not _unique_ints_or_empty(outer_idxs):
            invalid = [-1]
        if invalid:
            reasons.append("outer-ring module indices changed")


def _ring_power_override_updates(
    path: Path, data: JsonObject
) -> RingPowerOverrideUpdates:
    return ring_power_override_updates(path, data)


def _reset_ring_power_override_state() -> None:
    global PER_MODULE_OUTER_INDICES, PER_MODULE_OUTER_POWERS
    global PER_MODULE_INDICES, PER_MODULE_POWERS, OUTER_RING_INDEX_JSON
    PER_MODULE_OUTER_INDICES = []
    PER_MODULE_OUTER_POWERS = []
    PER_MODULE_INDICES = []
    PER_MODULE_POWERS = []
    OUTER_RING_INDEX_JSON = None


def _apply_ring_power_override_updates(
    path: Path, updates: RingPowerOverrideUpdates
) -> None:
    global RING_POWERS_SOURCE, PER_MODULE_OUTER_INDICES, PER_MODULE_OUTER_POWERS
    global PER_MODULE_INDICES, PER_MODULE_POWERS, OUTER_RING_INDEX_JSON
    global RING_POWERS_COMPAT_STATUS, RING_POWERS_COMPAT_MESSAGE
    RING_POWER_BY_RING.update(updates.ring_updates)
    if updates.outer_ring_index is not None:
        OUTER_RING_INDEX_JSON = updates.outer_ring_index
    PER_MODULE_OUTER_INDICES = updates.outer_indices
    PER_MODULE_OUTER_POWERS = updates.outer_powers
    PER_MODULE_INDICES = updates.module_indices
    PER_MODULE_POWERS = updates.module_powers
    if updates.applied_parts:
        _ensure_ring_power_entries()
        RING_POWERS_SOURCE = f"json:{path}"
        RING_POWERS_COMPAT_STATUS = "compatible"
        RING_POWERS_COMPAT_MESSAGE = "validated against current SMD runtime fingerprint"


def _apply_ring_powers_override(
    positions: list[PositionRecord], meta: JsonObject
) -> None:
    """
    If USE_RING_POWERS_JSON=1 and a JSON file is available (default path: ring_powers_optimized.json),
    override scheduled ring powers accordingly. Skipped when BASIS_MODE=1.
    """
    global RING_POWERS_SOURCE, PER_MODULE_OUTER_INDICES, PER_MODULE_OUTER_POWERS
    global PER_MODULE_INDICES, PER_MODULE_POWERS, OUTER_RING_INDEX_JSON
    global RING_POWERS_COMPAT_STATUS, RING_POWERS_COMPAT_MESSAGE
    if BASIS_MODE:
        return
    ring_override_config = load_ring_power_override_config()
    path = ring_override_config.path
    if not ring_override_config.use_json or not path.exists():
        RING_POWERS_COMPAT_STATUS = "disabled"
        RING_POWERS_COMPAT_MESSAGE = "ring-power JSON overrides disabled"
        return
    try:
        data = cast(JsonObject, json.loads(path.read_text()))
        _validate_ring_power_payload(path, data, positions, meta)
        updates = _ring_power_override_updates(path, data)
        for warning in updates.warnings:
            print(warning)
        _apply_ring_power_override_updates(path, updates)
        if updates.applied_parts:
            suffix = ", ".join(sorted(set(updates.applied_parts)))
            print(f"Applied validated power overrides from {path} ({suffix})")
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as e:
        RING_POWERS_COMPAT_STATUS = "invalid"
        RING_POWERS_COMPAT_MESSAGE = str(e)
        if ring_override_config.require_json:
            raise RuntimeError(f"Failed to load ring powers JSON ({path}): {e}") from e
        print(f"WARNING: ignoring incompatible ring powers JSON ({path}): {e}")
        RING_POWERS_SOURCE = "computed-defaults"
        _reset_ring_power_override_state()


# ──────────────────────────────────────────────────────────────────────────────
# Optics
# ──────────────────────────────────────────────────────────────────────────────
SOURCE_CONFIG = load_source_config()

OPTICS_MODE = SOURCE_CONFIG.optics_mode
SMD_SOURCE_VARIANT = SOURCE_CONFIG.source_variant
SMD_SOURCE_VARIANT_META = SOURCE_CONFIG.source_variant_meta

# ──────────────────────────────────────────────────────────────────────────────
# Files / constants
# ──────────────────────────────────────────────────────────────────────────────
OUTPUT_CONFIG = load_output_config()

OUT_DIR = OUTPUT_CONFIG.out_dir
SOURCE_VARIANT_CAL_NAME = OUTPUT_CONFIG.source_variant_cal_name
SOURCE_VARIANT_CAL_FILE = OUTPUT_CONFIG.source_variant_cal_file
SMD_JSON = OUTPUT_CONFIG.smd_json

PI = math.pi


# ──────────────────────────────────────────────────────────────────────────────
# Public API for overlay/GUI
# ──────────────────────────────────────────────────────────────────────────────
def read_smd_layout_json() -> tuple[list[PositionRecord], float]:
    """Return (positions, spacing_m) from snapshot (all meters)."""
    return _read_smd_layout_json(SMD_JSON)


def _smd_position_settings() -> SmdPositionSettings:
    return SmdPositionSettings(
        height_m=HEIGHT_M,
        wall_margin_m=WALL_MARGIN_M,
        ring_n=RING_N,
        rings=RINGS,
        base_ring_n=SMD_BASE_RING_N,
        mount_z_m=MOUNT_Z_M,
        patch_side_m=PATCH_SIDE_M,
        module_footprint_x_m=MODULE_FOOTPRINT_X_M,
        module_footprint_y_m=MODULE_FOOTPRINT_Y_M,
        fixed_pitch_m=SMD_FIXED_PITCH_M,
        fixture_angle_m=SMD_FIXTURE_ANGLE_M,
        layout_mode=LAYOUT_MODE,
        perimeter_gap_fill=SMD_PERIM_GAP_FILL,
    )


def _smd_fixture_overlay_settings() -> SmdFixtureOverlaySettings:
    return SmdFixtureOverlaySettings(
        length_m=LENGTH_M,
        width_m=WIDTH_M,
        layout_mode=LAYOUT_MODE,
    )


def _compute_positions_from_env() -> tuple[list[PositionRecord], float, JsonObject]:
    """
    Compute positions from env (meters), no JSON dependency.
    Returns (positions, spacing, meta).
    """
    return compute_positions_from_env(_smd_position_settings())


def get_module_positions() -> tuple[list[PositionRecord], float]:
    """
    Decide which layout generator to use based on LAYOUT_MODE.
    """
    return _get_module_positions_for_settings(_smd_position_settings())


# ──────────────────────────────────────────────────────────────────────────────
# Watts → photons → radiance
# ──────────────────────────────────────────────────────────────────────────────
def _per_module_nominal_watts() -> float:
    if USE_CURVE_MODEL and CURVE_MODEL is not None:
        return float(CURVE_MODEL.nominal_module_input_w)
    return sum(COUNT_PER_MODULE[ch] * PER_LED_W[ch] for ch in COUNT_PER_MODULE)


def _curve_eval_input_w(w_in: float) -> float:
    # Keep the legacy EFF_SCALE dimmer behavior by scaling the scheduled input
    # watts before we evaluate the electrical and photon curves.
    return max(0.0, float(w_in) * USER_EFF_SCALE)


@lru_cache(maxsize=None)
def _curve_module_state_for_input_w(input_w: float) -> ModuleState:
    if not USE_CURVE_MODEL or CURVE_MODEL is None:
        raise RuntimeError("curve model requested while SMD_MODEL is not curve")
    return CURVE_MODEL.evaluate_module(float(input_w), pmma_active=PMMA_MODE)


def _curve_module_state_for_w(w_in: float) -> ModuleState:
    return _curve_module_state_for_input_w(_curve_eval_input_w(w_in))


def _thermal_eff_for_watts(w_in: float) -> float:
    if USE_CURVE_MODEL and CURVE_MODEL is not None:
        return float(_curve_module_state_for_w(w_in).thermal_multiplier)
    # Two-tier thermal model based on per-module input watts.
    if PPE_IS_SYSTEM:
        return 1.0
    if w_in <= 0:
        return 1.0
    if w_in <= 70.0:
        return 0.97
    return 0.93


def _thermal_eff_avg_for_watts(watts: list[float]) -> float:
    if PPE_IS_SYSTEM:
        return 1.0
    num = 0.0
    den = 0.0
    for w in watts:
        if w <= 0:
            continue
        den += w
        num += w * _thermal_eff_for_watts(w)
    return (num / den) if den > 0 else 1.0


def _module_photon_flux_umol_s(ring: int) -> float:
    if USE_CURVE_MODEL and CURVE_MODEL is not None:
        return float(
            _curve_module_state_for_w(_ring_power_for(ring)).source_photon_umol_s
        )
    pw = _ring_power_for(ring)
    thermal = _thermal_eff_for_watts(pw)
    target_w = pw * EFF_SCALE * thermal
    nom_w = _per_module_nominal_watts()
    scale = 0.0 if nom_w <= 0 else target_w / nom_w
    phi = 0.0
    for ch, n in COUNT_PER_MODULE.items():
        p_elec_ch = n * PER_LED_W[ch] * scale
        phi += p_elec_ch * PPE_UMOL_PER_J[ch]
    return phi


def _module_radiance_umol_per_sr_m2(ring: int) -> float:
    A = PATCH_SIDE_M * PATCH_SIDE_M
    phi = _module_photon_flux_umol_s(ring)
    return 0.0 if A <= 0 else phi / (A * PI)  # Lambertian baseline


def _radiance_for_area_ring(ring: int, area_m2: float) -> float:
    if area_m2 <= 0:
        return 0.0
    phi = _module_photon_flux_umol_s(ring)
    return phi / (area_m2 * PI)


def _module_photon_flux_umol_s_for_w(target_w: float) -> float:
    if USE_CURVE_MODEL and CURVE_MODEL is not None:
        return float(_curve_module_state_for_w(target_w).source_photon_umol_s)
    # target_w is input watts per module; apply EFF_SCALE to get effective.
    nom_w = _per_module_nominal_watts()
    thermal = _thermal_eff_for_watts(target_w)
    scale = 0.0 if nom_w <= 0 else (target_w * EFF_SCALE * thermal) / nom_w
    phi = 0.0
    for ch, n in COUNT_PER_MODULE.items():
        p_elec_ch = n * PER_LED_W[ch] * scale
        phi += p_elec_ch * PPE_UMOL_PER_J[ch]
    return phi


def _module_radiance_umol_per_sr_m2_for_w(target_w: float) -> float:
    A = PATCH_SIDE_M * PATCH_SIDE_M
    phi = _module_photon_flux_umol_s_for_w(target_w)
    return 0.0 if A <= 0 else phi / (A * PI)


def _radiance_for_area_w(target_w: float, area_m2: float) -> float:
    if area_m2 <= 0:
        return 0.0
    phi = _module_photon_flux_umol_s_for_w(target_w)
    return phi / (area_m2 * PI)


def _module_output_photon_umol_s_for_w(target_w: float) -> float:
    if USE_CURVE_MODEL and CURVE_MODEL is not None:
        return float(_curve_module_state_for_w(target_w).output_photon_umol_s)
    return _module_photon_flux_umol_s_for_w(target_w)


def _module_output_photon_umol_s(ring: int) -> float:
    return _module_output_photon_umol_s_for_w(_ring_power_for(ring))


def _module_effective_watts_for_w(target_w: float) -> float:
    if USE_CURVE_MODEL and CURVE_MODEL is not None:
        return float(_curve_module_state_for_w(target_w).effective_w_after_losses)
    return float(target_w) * LOSS_SCALE * _thermal_eff_for_watts(target_w)


def compute_droop_scale(
    ring_watts_per_module: list[float],
    ring_module_counts: list[int],
    *,
    P_NOM: float = 100.0,
    K: float = 0.12,
) -> float:
    return _compute_droop_scale(
        ring_watts_per_module,
        ring_module_counts,
        P_NOM=P_NOM,
        K=K,
    )


def get_ring_watts_and_counts(
    ring_counts: dict[int, int], rings_local: int
) -> tuple[list[float], list[int]]:
    return _get_ring_watts_and_counts(ring_counts, rings_local, _ring_power_for)


def _curve_debug_report(module_idx: int, ring: int, scheduled_w: float) -> str:
    state = _curve_module_state_for_w(scheduled_w)
    lines = [
        "Curve debug:",
        f"  module_idx  : {module_idx}",
        f"  ring        : {ring}",
        f"  input_w     : {scheduled_w:.3f} W/module",
        f"  dimmed_w    : {_curve_eval_input_w(scheduled_w):.3f} W/module",
        f"  led_supply  : {state.led_supply_w:.3f} W/module",
        f"  drive_ratio : {state.drive_ratio:.6f}",
        f"  thermal_mult: {state.thermal_multiplier:.6f}",
        f"  eff_w       : {state.effective_w_after_losses:.3f} W/module",
    ]
    for channel in state.channels:
        lines.extend(
            [
                f"  {channel.label}_current : {channel.current_a * 1000.0:.3f} mA/package",
                f"  {channel.label}_vf      : {channel.vf_v:.4f} V/package",
                f"  {channel.label}_rel_ppe : raw {channel.rel_ppe_multiplier_raw:.6f}, used {channel.rel_ppe_multiplier_norm:.6f}",
                f"  {channel.label}_power   : {channel.channel_power_w:.3f} W/channel ({channel.package_power_w:.4f} W/package)",
                f"  {channel.label}_photons : {channel.channel_photon_umol_s_raw:.3f} umol/s/channel",
            ]
        )
    lines.extend(
        [
            f"  source_ppf  : {state.source_photon_umol_s:.3f} umol/s/module",
            f"  output_ppf  : {state.output_photon_umol_s:.3f} umol/s/module",
            f"  source_ppe  : {state.source_ppe_umol_per_j:.4f} umol/J",
            f"  wall_ppe    : {state.wall_plug_ppe_umol_per_j:.4f} umol/J",
        ]
    )
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# RAD writers
# ──────────────────────────────────────────────────────────────────────────────
def _write_area_square(
    fh: TextIO, mat: str, cx: float, cy: float, z: float, side: float
) -> None:
    write_area_square(fh, mat, cx, cy, z, side, identifier=radiance_identifier)


def _write_area_rect(
    fh: TextIO, mat: str, cx: float, cy: float, z: float, lx: float, ly: float
) -> None:
    write_area_rect(fh, mat, cx, cy, z, lx, ly, identifier=radiance_identifier)


def _write_area_grid(
    fh: TextIO,
    mat: str,
    cx: float,
    cy: float,
    z: float,
    side: float,
    grid: int,
) -> None:
    write_area_grid(
        fh, mat, cx, cy, z, side, grid, identifier=radiance_identifier
    )


def _write_area_rect_grid(
    fh: TextIO,
    mat: str,
    cx: float,
    cy: float,
    z: float,
    lx: float,
    ly: float,
    grid: int,
) -> None:
    write_area_rect_grid(
        fh, mat, cx, cy, z, lx, ly, grid, identifier=radiance_identifier
    )


def _write_brightfunc_ref(
    fh: TextIO, patt_name: str, funcname: str, cal_name: str
) -> None:
    write_brightfunc_ref(fh, patt_name, funcname, cal_name)


def _build_fixture_overlay(
    positions: list[PositionRecord],
    meta: JsonObject,
    z_m: float,
) -> tuple[list[JsonObject], JsonObject]:
    return build_fixture_overlay(
        positions,
        meta,
        z_m,
        _smd_fixture_overlay_settings(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Main generation
# ──────────────────────────────────────────────────────────────────────────────
def _log_pmma_configuration(log: Callable[..., None]) -> None:
    pmma_env = os.environ.get("PMMA_MODE", "")
    log(
        f"PMMA_MODE env={pmma_env!r} parsed={int(PMMA_MODE)} using_lid={bool(PMMA_MODE)}"
    )
    if PMMA_MODE and "BOARD_OPT_EFF" in os.environ and BOARD_OPT_EFF < 0.999:
        log(
            "WARNING: PMMA_MODE=1 with BOARD_OPT_EFF < 1.0 may double-count optical losses.",
            level="warning",
        )


def _prepare_layout_stage() -> SmdLayoutStage:
    positions, spacing, meta = _compute_positions_from_env()
    return SmdLayoutStage(
        positions=positions,
        spacing=spacing,
        meta=meta,
        layout_mode=meta.get("layout_mode", LAYOUT_MODE),
        pitch_x=meta.get("pitch_x_m"),
        pitch_y=meta.get("pitch_y_m"),
        rings_local=int(meta.get("rings", RINGS)),
    )


def _apply_power_source_for_layout(
    positions: list[PositionRecord], meta: JsonObject, rings_local: int
) -> None:
    if BASIS_MODE:
        _apply_basis_ring_powers(rings_local)
    elif RCONTRIB_BASIS_MODE:
        RING_POWER_BY_RING.update(
            uniform_ring_power_schedule(rings_local, BASIS_UNIT_W)
        )
    else:
        _apply_ring_powers_override(positions, meta)


def _outer_ring_for_positions(positions: list[PositionRecord]) -> int:
    return max(
        (int(p.get("ring", 0)) for p in positions if p.get("kind") != "strip"),
        default=0,
    )


def _per_module_watts_for_positions(
    positions: list[PositionRecord],
    *,
    outer_override_map: dict[int, float],
    module_override_map: dict[int, float],
) -> list[float] | None:
    if BASIS_MODE and BASIS_MODULE_IDX >= 0:
        per_mod_w = [0.0 for _ in positions]
        if 0 <= BASIS_MODULE_IDX < len(per_mod_w):
            per_mod_w[BASIS_MODULE_IDX] = float(BASIS_UNIT_W)
        return per_mod_w
    if module_override_map:
        return [
            float(module_override_map.get(idx, _ring_power_for(int(p.get("ring", 0)))))
            for idx, p in enumerate(positions)
        ]
    if outer_override_map:
        return [
            float(outer_override_map.get(idx, _ring_power_for(int(p.get("ring", 0)))))
            for idx, p in enumerate(positions)
        ]
    return None


def _prepare_power_stage(layout: SmdLayoutStage) -> SmdPowerStage:
    _apply_power_source_for_layout(layout.positions, layout.meta, layout.rings_local)
    ring_counts: Counter[int] = Counter(int(p["ring"]) for p in layout.positions)
    outer_override_map = {
        int(idx): float(w)
        for idx, w in zip(PER_MODULE_OUTER_INDICES, PER_MODULE_OUTER_POWERS)
    }
    module_override_map = {
        int(idx): float(w) for idx, w in zip(PER_MODULE_INDICES, PER_MODULE_POWERS)
    }
    per_mod_w = _per_module_watts_for_positions(
        layout.positions,
        outer_override_map=outer_override_map,
        module_override_map=module_override_map,
    )
    per_module_mode = per_mod_w is not None
    if GROUP_MODIFIERS_BY_RING and per_module_mode:
        raise RuntimeError(
            "Ring-grouped modifier mode does not support per-module SMD power schedules."
        )
    return SmdPowerStage(
        ring_counts=ring_counts,
        outer_ring=_outer_ring_for_positions(layout.positions),
        outer_override_map=outer_override_map,
        module_override_map=module_override_map,
        per_mod_w=per_mod_w,
        per_module_mode=per_module_mode,
    )


def _configure_loss_stage(
    layout: SmdLayoutStage, power: SmdPowerStage, log: Callable[..., None]
) -> SmdLossStage:
    global LOSS_SCALE, EFF_SCALE, DROOP_SCALE
    droop_scale = 1.0
    if USE_CURVE_MODEL and CURVE_MODEL is not None:
        DROOP_SCALE = 1.0
        LOSS_SCALE = DRIVER_EFF * WIRING_EFF * USER_EFF_SCALE
        EFF_SCALE = USER_EFF_SCALE
        log(
            "SMD model: "
            f"{CURVE_MODEL_VERSION} "
            f"(driver={CURVE_MODEL.driver_eff:.3f}, wiring={CURVE_MODEL.wiring_eff:.3f}, "
            f"thermal_ref={CURVE_MODEL.thermal_ref_multiplier:.3f} @ {CURVE_MODEL.thermal_ref_input_w:.2f} W)"
        )
    else:
        if not power.per_module_mode and not BASIS_MODE:
            ring_watts, ring_mods = get_ring_watts_and_counts(
                power.ring_counts, layout.rings_local
            )
            droop_scale = compute_droop_scale(
                ring_watts, ring_mods, P_NOM=DROOP_P_NOM, K=DROOP_K
            )
        DROOP_SCALE = float(droop_scale)
        log(
            f"Droop scale: {DROOP_SCALE:.4f} (P_NOM={DROOP_P_NOM:.1f}, K={DROOP_K:.3f})"
        )
        opt_eff = 1.0 if PMMA_MODE else BOARD_OPT_EFF
        LOSS_SCALE = (
            USER_EFF_SCALE
            if PPE_IS_SYSTEM
            else DRIVER_EFF * THERMAL_EFF * opt_eff * WIRING_EFF * USER_EFF_SCALE
        )
        EFF_SCALE = LOSS_SCALE * DROOP_SCALE
    return SmdLossStage(
        droop_scale=droop_scale,
        per_mod_rad=_per_module_radiance_values(layout.positions, power),
    )


def _per_module_radiance_values(
    positions: list[PositionRecord], power: SmdPowerStage
) -> list[float]:
    if not power.per_module_mode:
        return []
    if power.per_mod_w is None:
        raise RuntimeError("per-module mode selected without per-module watt values")
    per_mod_rad: list[float] = []
    for idx, p in enumerate(positions):
        lx = float(p.get("lx", PATCH_SIDE_M))
        ly = float(p.get("ly", PATCH_SIDE_M))
        per_mod_rad.append(_radiance_for_area_w(power.per_mod_w[idx], lx * ly))
    return per_mod_rad


def _write_smd_emitter_rad(
    *,
    config: SmdEmitterConfig,
    layout: SmdLayoutStage,
    power: SmdPowerStage,
    loss: SmdLossStage,
) -> Path:
    use_stack_optics = OPTICS_MODE == "stack"
    if not SMD_SOURCE_VARIANT_META.is_native:
        write_smd_source_variant_cal(SOURCE_VARIANT_CAL_FILE, SMD_SOURCE_VARIANT_META)

    out = config.output_dir / "emitters_smd_ALL_umol.rad"
    with out.open("w") as fh:
        _write_smd_rad_header(fh, layout, use_stack_optics)
        _write_smd_rad_materials(fh, use_stack_optics)
        _write_smd_rad_modules(fh, layout, power, loss)
    return out


def _write_smd_rad_header(
    fh: TextIO, layout: SmdLayoutStage, use_stack_optics: bool
) -> None:
    fh.write("# SMD macro emitters in µmol/s/sr/m²\n")
    if (
        layout.layout_mode in {"rect_rect", "grid", "uniform", "matrix", "exact_tiled"}
        and layout.pitch_x
        and layout.pitch_y
    ):
        if abs(layout.pitch_x - layout.pitch_y) < 1e-6:
            fh.write(
                f"# rings={layout.rings_local} modules={len(layout.positions)} pitch={layout.pitch_x:.4f} m "
                f"(uniform; outer margin {WALL_MARGIN_M:.3f} m)\n"
            )
        else:
            fh.write(
                f"# rings={layout.rings_local} modules={len(layout.positions)} "
                f"pitch_x={layout.pitch_x:.4f} m pitch_y={layout.pitch_y:.4f} m "
                f"(outer margin {WALL_MARGIN_M:.3f} m)\n"
            )
    else:
        fh.write(
            f"# rings={layout.rings_local} modules={len(layout.positions)} "
            f"spacing={layout.spacing:.4f} m (outer margin {WALL_MARGIN_M:.3f} m)\n"
        )
    fh.write(
        f"# patch side = {PATCH_SIDE_M * 1e3:.1f} mm  subgrid = {SUBPATCH_GRID}x{SUBPATCH_GRID}\n"
    )
    fh.write(f"# source angular variant = {SMD_SOURCE_VARIANT_META.key}\n")
    if use_stack_optics:
        fh.write(
            f"# optics = physical stack ({MODULE_PROFILE_VERSION}; PMMA lid + PTFE cavity)\n\n"
        )
    else:
        raise RuntimeError(f"Unsupported SMD optics mode: {OPTICS_MODE!r}")


def _write_smd_rad_materials(fh: TextIO, use_stack_optics: bool) -> None:
    if use_stack_optics and PTFE_MODE:
        fh.write(
            "# PTFE cavity liner: high-reflectance diffuse walls around the gasket aperture\n"
        )
        fh.write(
            f"void plastic ptfe_liner\n0\n0\n5 {PTFE_REFLECTANCE_LOCAL:.4f} "
            f"{PTFE_REFLECTANCE_LOCAL:.4f} {PTFE_REFLECTANCE_LOCAL:.4f} 0 0\n\n"
        )
    if PMMA_MODE:
        pmma_k = max(0.0, float(PMMA_ABS_M))
        fh.write("# PMMA lid: dielectric with absorption so T=0.92 at 3.0 mm\n")
        fh.write(
            f"void dielectric pmma_lid\n0\n0\n5 1 1 1 {PMMA_IOR:.4f} {pmma_k:.6f}\n\n"
        )
    if not SMD_SOURCE_VARIANT_META.is_native:
        _write_brightfunc_ref(
            fh,
            "smd_source_variant_pattern",
            SMD_SOURCE_VARIANT_META.cal_function_name or "smd_source_variant",
            cal_name=SOURCE_VARIANT_CAL_NAME,
        )


def _write_smd_rad_modules(
    fh: TextIO, layout: SmdLayoutStage, power: SmdPowerStage, loss: SmdLossStage
) -> None:
    emitted_materials: set[str] = set()
    for idx, p in enumerate(layout.positions):
        ring = int(p["ring"])
        cx, cy, cz = float(p["x"]), float(p["y"]), float(p["z"])
        lx = float(p.get("lx", PATCH_SIDE_M))
        ly = float(p.get("ly", PATCH_SIDE_M))
        base_rad = (
            loss.per_mod_rad[idx]
            if power.per_module_mode
            else _radiance_for_area_ring(ring, lx * ly)
        )
        mat = (
            basis_ring_modifier_name(ring)
            if GROUP_MODIFIERS_BY_RING
            else f"smd_L{ring}_m{idx:03d}"
        )
        _write_smd_material_once(fh, mat, base_rad, emitted_materials)
        if p.get("kind") == "strip":
            _write_area_rect_grid(fh, mat, cx, cy, cz, lx, ly, SUBPATCH_GRID)
        else:
            _write_area_grid(fh, mat, cx, cy, cz, PATCH_SIDE_M, SUBPATCH_GRID)
        write_stack_cavity(
            fh,
            cx,
            cy,
            cz,
            ptfe_mode=PTFE_MODE,
            aperture_m=STACK_APERTURE_M,
            liner_thk_m=PTFE_THK_M,
            stack_height_m=PMMA_LID_OFFSET_M,
        )
        if PMMA_MODE:
            write_pmma_lid(
                fh,
                idx,
                cx,
                cy,
                cz,
                side_m=PMMA_SIDE_M,
                lid_offset_m=PMMA_LID_OFFSET_M,
                lid_thk_m=PMMA_THK_M,
            )


def _write_smd_material_once(
    fh: TextIO, mat: str, base_rad: float, emitted_materials: set[str]
) -> None:
    if mat in emitted_materials:
        return
    if SMD_SOURCE_VARIANT_META.is_native:
        fh.write(
            f"void light {mat}\n0\n0\n3 {base_rad:.6f} {base_rad:.6f} {base_rad:.6f}\n\n"
        )
    else:
        fh.write(
            f"smd_source_variant_pattern light {mat}\n0\n0\n3 "
            f"{base_rad:.6f} {base_rad:.6f} {base_rad:.6f}\n\n"
        )
    emitted_materials.add(mat)


def _thermal_eff_average(layout: SmdLayoutStage, power: SmdPowerStage) -> float:
    if power.per_module_mode:
        if power.per_mod_w is None:
            raise RuntimeError(
                "per-module mode selected without per-module watt values"
            )
        return _thermal_eff_avg_for_watts([float(w) for w in power.per_mod_w])
    thermal_watts: list[float] = []
    for ring in range(layout.rings_local):
        count = power.ring_counts.get(ring, 0)
        thermal_watts.extend([float(_ring_power_for(ring))] * int(count))
    return _thermal_eff_avg_for_watts(thermal_watts)


def _summary_descriptions(
    thermal_eff_avg: float,
) -> tuple[str, str, str, str, str]:
    if USE_CURVE_MODEL and CURVE_MODEL is not None:
        return (
            f"  smd_model   : {SMD_MODEL} ({CURVE_MODEL_VERSION})\n",
            f"  electrical  : driver {CURVE_MODEL.driver_eff:.3f} × wiring {CURVE_MODEL.wiring_eff:.3f} × "
            f"user_scale(EFF_SCALE env) {USER_EFF_SCALE:.3f}\n",
            f"  ppe_ref     : {CURVE_MODEL.ppe_reference_mode}\n",
            "  droop model : curve-driven Vf(I) + relative PPE(I); legacy x^(-K) disabled\n",
            f"  thermal     : continuous multiplier (avg {thermal_eff_avg:.3f}; ref {CURVE_MODEL.thermal_ref_multiplier:.3f} @ "
            f"{CURVE_MODEL.thermal_ref_input_w:.2f} W, slope {CURVE_MODEL.thermal_slope_per_w:.6f}/W, "
            f"clamp {CURVE_MODEL.thermal_min_multiplier:.3f}..{CURVE_MODEL.thermal_max_multiplier:.3f})\n",
        )
    derate_desc = (
        f"  loss_scale  : {LOSS_SCALE:.4f} (= user_scale(EFF_SCALE env) {USER_EFF_SCALE:.3f})\n"
        if PPE_IS_SYSTEM
        else f"  loss_scale  : {LOSS_SCALE:.4f} (= DRIVER_EFF {DRIVER_EFF:.3f} × THERMAL_EFF tiered(avg {thermal_eff_avg:.3f}) × "
        f"BOARD_OPT_EFF {BOARD_OPT_EFF:.3f} × WIRING_EFF {WIRING_EFF:.3f} × user_scale(EFF_SCALE env) {USER_EFF_SCALE:.3f})\n"
    )
    thermal_desc = (
        "  thermal tier: disabled (PPE_IS_SYSTEM=1)\n"
        if PPE_IS_SYSTEM
        else "  thermal tier: <=70 W/mod 0.97, >70 W/mod 0.93"
        f" (avg {thermal_eff_avg:.3f})\n"
    )
    return (
        f"  smd_model   : {SMD_MODEL} (legacy heuristic)\n",
        derate_desc,
        "",
        f"  droop_scale : {DROOP_SCALE:.4f} (P_NOM={DROOP_P_NOM:.1f}, K={DROOP_K:.3f})\n",
        thermal_desc,
    )


def _ring_power_summary(
    layout: SmdLayoutStage, power: SmdPowerStage
) -> tuple[float, float, list[str]]:
    return _summary_ring_power_summary(
        rings_local=layout.rings_local,
        ring_counts=dict(power.ring_counts),
        per_module_mode=power.per_module_mode,
        positions=layout.positions,
        per_mod_w=power.per_mod_w,
        ring_power_for=_ring_power_for,
        module_effective_watts_for_w=_module_effective_watts_for_w,
    )


def _ppe_summary() -> tuple[float, str]:
    if USE_CURVE_MODEL and CURVE_MODEL is not None:
        ww_count = int(os.environ.get("SMD_WW_COUNT", "52") or "52")
        cw_count = int(os.environ.get("SMD_CW_COUNT", "52") or "52")
        red_count = int(os.environ.get("SMD_RED_COUNT", "41") or "41")
        ww_nom_w = _env_float("SMD_WW_NOMINAL_W", 0.68)
        cw_nom_w = _env_float("SMD_CW_NOMINAL_W", 0.68)
        red_nom_w = _env_float("SMD_RED_NOMINAL_W", 0.44)
        ww_nom_ppe = _env_float("SMD_WW_NOMINAL_PPE", 2.73)
        cw_nom_ppe = _env_float("SMD_CW_NOMINAL_PPE", 2.81)
        red_nom_ppe = _env_float("SMD_RED_NOMINAL_PPE", 4.13)
        base_led_w = ww_count * ww_nom_w + cw_count * cw_nom_w + red_count * red_nom_w
        base_ppf = (
            ww_count * ww_nom_w * ww_nom_ppe
            + cw_count * cw_nom_w * cw_nom_ppe
            + red_count * red_nom_w * red_nom_ppe
        )
        return (
            base_ppf / max(base_led_w, 1e-9),
            f"WW={ww_nom_ppe:.3f}, CW={cw_nom_ppe:.3f}, R={red_nom_ppe:.3f}",
        )
    avg_ppe = sum(
        COUNT_PER_MODULE[ch] * PER_LED_W[ch] * PPE_UMOL_PER_J[ch]
        for ch in COUNT_PER_MODULE
    ) / max(_per_module_nominal_watts(), 1e-9)
    ppe_parts = [
        f"{ch}={PPE_UMOL_PER_J[ch]:.3f}"
        for ch in sorted(COUNT_PER_MODULE.keys())
        if COUNT_PER_MODULE.get(ch, 0) > 0
    ]
    return avg_ppe, ", ".join(ppe_parts) if ppe_parts else "(none)"


def _photon_totals(layout: SmdLayoutStage, power: SmdPowerStage) -> tuple[float, float]:
    return _summary_photon_totals(
        rings_local=layout.rings_local,
        ring_counts=dict(power.ring_counts),
        per_module_mode=power.per_module_mode,
        per_mod_w=power.per_mod_w,
        module_photon_flux_umol_s=_module_photon_flux_umol_s,
        module_output_photon_umol_s=_module_output_photon_umol_s,
        module_photon_flux_umol_s_for_w=_module_photon_flux_umol_s_for_w,
        module_output_photon_umol_s_for_w=_module_output_photon_umol_s_for_w,
    )


def _curve_debug_text(layout: SmdLayoutStage, power: SmdPowerStage) -> str:
    return _summary_curve_debug_text(
        positions=layout.positions,
        per_module_mode=power.per_module_mode,
        per_mod_w=power.per_mod_w,
        enabled=bool(
            USE_CURVE_MODEL
            and CURVE_MODEL is not None
            and (SMD_CURVE_DEBUG or CURVE_MODEL.debug_enabled)
        ),
        debug_module_idx=SMD_DEBUG_MODULE_IDX,
        debug_ring=SMD_DEBUG_RING,
        ring_power_for=_ring_power_for,
        curve_debug_report=_curve_debug_report,
    )


def _curve_validation_text() -> str:
    if not (USE_CURVE_MODEL and CURVE_MODEL_VALIDATION):
        return ""
    return (
        f"  nominal reference: {CURVE_MODEL_VALIDATION['nominal_input_w']:.2f} W in -> "
        f"{CURVE_MODEL_VALIDATION['nominal_source_umol_s']:.1f} umol/s source, "
        f"{CURVE_MODEL_VALIDATION['nominal_output_umol_s']:.1f} umol/s after PMMA, "
        f"wall PPE {CURVE_MODEL_VALIDATION['nominal_wall_plug_ppe']:.3f} umol/J\n"
    )


def _scheduled_ppe_note(layout: SmdLayoutStage, total_w_in: float) -> str:
    if not (USE_CURVE_MODEL and CURVE_MODEL_VALIDATION and layout.positions):
        return ""
    avg_input_w = total_w_in / max(len(layout.positions), 1)
    ref_input_w = float(cast(float, CURVE_MODEL_VALIDATION["nominal_input_w"]))
    if abs(avg_input_w - ref_input_w) <= 1e-3:
        return ""
    return (
        f"  schedule note: run-average PPE reflects the solved schedule ({avg_input_w:.2f} W/module avg), "
        f"while the nominal reference uses {ref_input_w:.2f} W/module.\n"
    )


def _write_smd_summary_stage(
    *, config: SmdEmitterConfig, layout: SmdLayoutStage, power: SmdPowerStage
) -> SmdSummaryStage:
    thermal_eff_avg = _thermal_eff_average(layout, power)
    model_desc, derate_desc, ref_desc, droop_desc, thermal_desc = _summary_descriptions(
        thermal_eff_avg
    )
    total_w_in, total_w_eff, lines = _ring_power_summary(layout, power)
    avg_ppe, ppe_str = _ppe_summary()
    if total_w_eff > total_w_in + 1e-6:
        raise SystemExit(
            f"ERROR: effective watts ({total_w_eff:.3f}) exceed electrical input ({total_w_in:.3f}). "
            "Check loss_scale/efficiency math."
        )
    total_umol, total_output_umol = _photon_totals(layout, power)
    source_ppe_run = (
        (total_umol / total_w_in) if total_w_in > 0 else avg_ppe * EFF_SCALE
    )
    wall_plug_ppe_run = (
        (total_output_umol / total_w_in) if total_w_in > 0 else source_ppe_run
    )
    power_mode_desc = format_smd_power_mode_desc(
        per_module_mode=power.per_module_mode,
        module_override_map=power.module_override_map,
        basis_mode=BASIS_MODE,
        basis_module_idx=BASIS_MODULE_IDX,
    )
    summary_path = config.output_dir / "smd_summary.txt"
    summary_text = build_smd_summary_text(
        rings_local=layout.rings_local,
        module_count=len(layout.positions),
        module_profile_version=MODULE_PROFILE_VERSION,
        spacing_str=format_smd_spacing(
            spacing=layout.spacing,
            layout_mode=layout.layout_mode,
            pitch_x=layout.pitch_x,
            pitch_y=layout.pitch_y,
        ),
        wall_margin_m=WALL_MARGIN_M,
        patch_side_m=PATCH_SIDE_M,
        optics_desc=format_smd_optics_desc(
            optics_mode=OPTICS_MODE,
            module_profile_version=MODULE_PROFILE_VERSION,
        ),
        source_variant_desc=SMD_SOURCE_VARIANT_META.short_label,
        pmma_desc=format_smd_pmma_desc(
            pmma_mode=PMMA_MODE,
            pmma_ior=PMMA_IOR,
            pmma_t=PMMA_T,
            pmma_thk_m=PMMA_THK_M,
        ),
        ptfe_desc=format_smd_ptfe_desc(
            ptfe_mode=PTFE_MODE,
            ptfe_reflectance=PTFE_REFLECTANCE_LOCAL,
            ptfe_thk_m=PTFE_THK_M,
            stack_aperture_m=STACK_APERTURE_M,
            stack_pocket_m=STACK_POCKET_M,
        ),
        power_mode_desc=power_mode_desc,
        per_module_note=format_per_module_stats(power.per_module_mode, power.per_mod_w)[
            0
        ],
        per_module_stats=format_per_module_stats(
            power.per_module_mode, power.per_mod_w
        )[1],
        subpatch_grid=SUBPATCH_GRID,
        ring_powers_source=RING_POWERS_SOURCE,
        runtime_snapshot=_runtime_snapshot_text(),
        model_desc=model_desc,
        derate_desc=derate_desc,
        ref_desc=ref_desc,
        droop_desc=droop_desc,
        thermal_desc=thermal_desc,
        validation_text=_curve_validation_text(),
        scheduled_ppe_note=_scheduled_ppe_note(layout, total_w_in),
        ring_lines=lines,
        total_w_in=total_w_in,
        total_w_eff=total_w_eff,
        ppe_str=ppe_str,
        avg_ppe=avg_ppe,
        source_ppe_run=source_ppe_run,
        total_umol=total_umol,
        wall_plug_ppe_run=wall_plug_ppe_run,
        total_output_umol=total_output_umol,
        debug_text=_curve_debug_text(layout, power),
    )
    write_smd_summary(summary_path, summary_text)
    return SmdSummaryStage(
        summary_path=summary_path,
        summary_text=summary_text,
        power_mode_desc=power_mode_desc,
        total_w_in=total_w_in,
        total_w_eff=total_w_eff,
        total_umol=total_umol,
        total_output_umol=total_output_umol,
    )


def _runtime_snapshot_text() -> str:
    return (
        build_runtime_config_snapshot(
            smd_model=SMD_MODEL,
            source_variant_key=SMD_SOURCE_VARIANT_META.key,
            ppe_is_system_raw=PPE_IS_SYSTEM_RAW,
            ppe_is_system=PPE_IS_SYSTEM,
            target_system_ppe=TARGET_SYSTEM_PPE,
            user_eff_scale=USER_EFF_SCALE,
            driver_eff=DRIVER_EFF,
            wiring_eff=WIRING_EFF,
            pmma_mode=PMMA_MODE,
            pmma_t=PMMA_T,
            ring_powers_compat_status=RING_POWERS_COMPAT_STATUS,
            ring_powers_compat_message=RING_POWERS_COMPAT_MESSAGE,
            use_curve_model=USE_CURVE_MODEL,
            curve_model=CURVE_MODEL,
            curve_runtime_notes=CURVE_RUNTIME_NOTES,
            per_led_w=PER_LED_W,
            ppe_umol_per_j=PPE_UMOL_PER_J,
            droop_k=DROOP_K,
        )
        + "\n"
    )


def _write_smd_layout_snapshot(
    *,
    config: SmdEmitterConfig,
    layout: SmdLayoutStage,
    power: SmdPowerStage,
    summary: SmdSummaryStage,
) -> None:
    meta = layout.meta
    outer_indices = [
        index
        for index, position in enumerate(layout.positions)
        if int(position.get("ring", 0)) == power.outer_ring
    ]
    room_l_m = float(meta.get("room_L_m", LENGTH_M))
    room_w_m = float(meta.get("room_W_m", WIDTH_M))
    margin_m = float(meta.get("margin_m", WALL_MARGIN_M))
    pitch_x = float(meta.get("pitch_x_m", layout.spacing))
    pitch_y = float(meta.get("pitch_y_m", layout.spacing))
    fixture_groups, fixture_counts = _build_fixture_overlay(
        layout.positions,
        meta,
        MOUNT_Z_M,
    )
    write_smd_layout_json(
        path=config.layout_json,
        positions=layout.positions,
        spacing_m=layout.spacing,
        room_L_m=room_l_m,
        room_W_m=room_w_m,
        margin_m=margin_m,
        ring_n=meta.get("ring_n", RING_N),
        patch_side_m=PATCH_SIDE_M,
        z_m=MOUNT_Z_M,
        fixture_groups=fixture_groups,
        fixture_counts=fixture_counts,
        extra={
            **meta,
            **module_profile_meta(),
            "source": "generate_emitters_smd.py",
            "layout_mode": layout.layout_mode,
            "pitch_x_m": pitch_x,
            "pitch_y_m": pitch_y,
            "swapped_axes": meta.get("swapped_axes", False),
            "rings": layout.rings_local,
            "power_mode": summary.power_mode_desc,
            "source_variant": SMD_SOURCE_VARIANT_META.key,
            "source_variant_label": SMD_SOURCE_VARIANT_META.short_label,
            "source_variant_description": SMD_SOURCE_VARIANT_META.description,
            "outer_ring_index": power.outer_ring,
            "outer_ring_indices": outer_indices,
            "stack_aperture_m": STACK_APERTURE_M,
            "stack_pocket_m": STACK_POCKET_M,
            "stack_height_m": PMMA_LID_OFFSET_M,
            "ptfe_mode": PTFE_MODE,
            "ptfe_reflectance": PTFE_REFLECTANCE_LOCAL,
            "ptfe_thk_m": PTFE_THK_M,
        },
    )


def run_smd_emitter_generation(
    config: SmdEmitterConfig | None = None,
    *,
    emit: Callable[[SmdEmitterEvent], None] | None = None,
) -> SmdEmitterResult:
    config = config or SmdEmitterConfig.from_loaded_environment()
    events: list[SmdEmitterEvent] = []

    def log(message: str, *, level: str = "info") -> None:
        event = SmdEmitterEvent(level=level, message=message)
        events.append(event)
        if emit is not None:
            emit(event)

    _log_pmma_configuration(log)
    layout = _prepare_layout_stage()
    power = _prepare_power_stage(layout)
    loss = _configure_loss_stage(layout, power, log)
    out = _write_smd_emitter_rad(
        config=config,
        layout=layout,
        power=power,
        loss=loss,
    )

    summary = _write_smd_summary_stage(config=config, layout=layout, power=power)
    log(summary.summary_text.rstrip())
    log(f"✔ Wrote {out}")
    log(f"✔ Wrote {summary.summary_path}")

    _write_smd_layout_snapshot(
        config=config,
        layout=layout,
        power=power,
        summary=summary,
    )
    log("✔ Wrote runtime_state/smd_layout.json")
    return SmdEmitterResult(
        emitter_rad=out,
        summary_txt=summary.summary_path,
        layout_json=config.layout_json,
        module_count=len(layout.positions),
        ring_count=layout.rings_local,
        total_input_w=float(summary.total_w_in),
        total_effective_w=float(summary.total_w_eff),
        total_source_umol_s=float(summary.total_umol),
        total_output_umol_s=float(summary.total_output_umol),
        events=tuple(events),
    )


def main() -> None:
    run_smd_emitter_generation(emit=lambda event: print(event.message))


if __name__ == "__main__":
    main()
