from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rad_rebuild.radiance.engine.emitters.smd_generation.config import env_float, env_int

JsonObject = dict[str, Any]


def read_smd_layout_json(path: Path) -> tuple[list[JsonObject], float]:
    """Return (positions, spacing_m) from snapshot (all meters)."""
    data = json.loads(path.read_text())
    if data.get("units") != "meters":
        raise ValueError("smd_layout.json is not in meters.")
    positions = data.get("positions", [])
    spacing = float(data.get("spacing", 0.0))
    return positions, spacing


def write_smd_layout_json(
    path: Path,
    positions: list[JsonObject],
    spacing_m: float,
    room_L_m: float,
    room_W_m: float,
    margin_m: float,
    ring_n: int,
    patch_side_m: float,
    z_m: float = 0.0,
    extra: dict[str, Any] | None = None,
    fixture_groups: list[dict[str, Any]] | None = None,
    fixture_counts: dict[str, int] | None = None,
) -> None:
    def _rf(value: float | int | str, precision: int = 6) -> float:
        return float(round(float(value), precision))

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 2,
        "generator": "generate_emitters_smd.py",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "units": "meters",
        "rings": int(ring_n + 1),
        "modules": int(len(positions)),
        "spacing": _rf(spacing_m),
        "patch_side": _rf(patch_side_m),
        "room": {"L": _rf(room_L_m), "W": _rf(room_W_m)},
        "margin": _rf(margin_m),
        "z": _rf(z_m),
        "positions": [
            {
                "x": _rf(position.get("x", 0.0)),
                "y": _rf(position.get("y", 0.0)),
                "z": _rf(position.get("z", z_m)),
                "ring": int(position.get("ring", 0)),
                **({"kind": position.get("kind")} if position.get("kind") else {}),
                **(
                    {
                        "lx": _rf(position.get("lx", 0.0)),
                        "ly": _rf(position.get("ly", 0.0)),
                    }
                    if position.get("lx") and position.get("ly")
                    else {}
                ),
            }
            for position in positions
        ],
    }
    if fixture_groups:
        payload["fixture_groups"] = fixture_groups
        payload["fixture_count"] = int(len(fixture_groups))
    if fixture_counts:
        payload["fixture_counts"] = fixture_counts
    if extra:
        meta = dict(extra)
        for key in ("version", "units", "positions"):
            meta.pop(key, None)
        payload["meta"] = meta
    path.write_text(json.dumps(payload, indent=2))


def build_runtime_config_snapshot(
    *,
    smd_model: str,
    source_variant_key: str,
    ppe_is_system_raw: bool,
    ppe_is_system: bool,
    target_system_ppe: float,
    user_eff_scale: float,
    driver_eff: float,
    wiring_eff: float,
    pmma_mode: bool,
    pmma_t: float,
    ring_powers_compat_status: str,
    ring_powers_compat_message: str,
    use_curve_model: bool,
    curve_model: Any,
    curve_runtime_notes: list[str],
    per_led_w: dict[str, float],
    ppe_umol_per_j: dict[str, float],
    droop_k: float,
) -> str:
    lines = [
        "Runtime config:",
        f"  SMD_MODEL                : {smd_model}",
        f"  SMD_SOURCE_VARIANT       : {source_variant_key}",
        f"  PPE_IS_SYSTEM raw/effective : {int(ppe_is_system_raw)} / {int(ppe_is_system)}",
        f"  SMD_TARGET_PPE_UMOL_PER_J: {target_system_ppe:.3f}",
        f"  EFF_SCALE                : {user_eff_scale:.3f}",
        f"  DRIVER_EFF               : {driver_eff:.3f}",
        f"  WIRING_EFF               : {wiring_eff:.3f}",
        f"  PMMA_MODE                : {int(pmma_mode)}",
        f"  PMMA_T                   : {pmma_t:.3f}",
        f"  USE_RING_POWERS_JSON     : {os.getenv('USE_RING_POWERS_JSON', '0')}",
        f"  RING_POWERS_JSON         : {os.getenv('RING_POWERS_JSON', 'ring_powers_optimized.json')}",
        f"  ring powers status       : {ring_powers_compat_status}",
    ]
    if ring_powers_compat_message:
        lines.append(f"  ring powers note         : {ring_powers_compat_message}")
    if use_curve_model and curve_model is not None:
        lines.extend(
            [
                f"  SMD_PPE_REFERENCE_MODE   : {curve_model.ppe_reference_mode}",
                f"  SMD_WHITE_VF_CSV         : {curve_model.white.vf_curve.source_path}",
                f"  SMD_WHITE_PPE_CSV        : {curve_model.white.raw_rel_ppe_curve.source_path}",
                f"  SMD_RED_VF_CSV           : {curve_model.red.vf_curve.source_path}",
                f"  SMD_RED_PPE_CSV          : {curve_model.red.raw_rel_ppe_curve.source_path}",
                f"  nominal pkg W            : WW={env_float('SMD_WW_NOMINAL_W', 0.68):.3f}, CW={env_float('SMD_CW_NOMINAL_W', 0.68):.3f}, R={env_float('SMD_RED_NOMINAL_W', 0.44):.3f}",
                f"  nominal pkg PPE          : WW={env_float('SMD_WW_NOMINAL_PPE', 2.73):.3f}, CW={env_float('SMD_CW_NOMINAL_PPE', 2.81):.3f}, R={env_float('SMD_RED_NOMINAL_PPE', 4.13):.3f}",
                f"  package counts           : WW={env_int('SMD_WW_COUNT', 52)}, CW={env_int('SMD_CW_COUNT', 52)}, R={env_int('SMD_RED_COUNT', 41)}",
                f"  thermal model            : ref_mult={curve_model.thermal_ref_multiplier:.3f} @ {curve_model.thermal_ref_input_w:.2f} W, slope={curve_model.thermal_slope_per_w:.6f}/W, clamp={curve_model.thermal_min_multiplier:.3f}..{curve_model.thermal_max_multiplier:.3f}",
                "  legacy droop active      : no",
                "  legacy thermal tier      : no",
            ]
        )
        if curve_runtime_notes:
            lines.append(
                f"  runtime notes            : {'; '.join(curve_runtime_notes)}"
            )
    else:
        lines.extend(
            [
                f"  nominal pkg W            : WW={per_led_w.get('WW', 0.0):.3f}, CW={per_led_w.get('CW', 0.0):.3f}, R={per_led_w.get('R', 0.0):.3f}",
                f"  nominal pkg PPE          : WW={ppe_umol_per_j.get('WW', 0.0):.3f}, CW={ppe_umol_per_j.get('CW', 0.0):.3f}, R={ppe_umol_per_j.get('R', 0.0):.3f}",
                f"  legacy droop active      : {'yes' if droop_k != 0.0 else 'no'}",
                f"  legacy thermal tier      : {'no' if ppe_is_system else 'yes'}",
            ]
        )
    return "\n".join(lines)


def format_smd_spacing(
    *,
    spacing: float,
    layout_mode: str,
    pitch_x: float | None,
    pitch_y: float | None,
) -> str:
    spacing_str = f"{spacing:.4f} m"
    if (
        layout_mode in {"rect_rect", "grid", "uniform", "matrix", "exact_tiled"}
        and pitch_x
        and pitch_y
    ):
        if abs(pitch_x - pitch_y) < 1e-6:
            spacing_str = f"pitch={pitch_x:.4f} m (uniform)"
        else:
            spacing_str = f"pitch_x={pitch_x:.4f} m, pitch_y={pitch_y:.4f} m"
    return spacing_str


def format_smd_optics_desc(
    *,
    optics_mode: str,
    module_profile_version: str,
) -> str:
    if optics_mode == "stack":
        return f"physical stack ({module_profile_version})"
    return f"unsupported ({optics_mode})"


def format_smd_power_mode_desc(
    *,
    per_module_mode: bool,
    module_override_map: dict[int, float],
    basis_mode: bool,
    basis_module_idx: int,
) -> str:
    if not per_module_mode:
        return "ring-wise"
    if module_override_map or (basis_mode and basis_module_idx >= 0):
        return "per-module"
    return "outer ring per-module"


def format_smd_pmma_desc(
    *, pmma_mode: bool, pmma_ior: float, pmma_t: float, pmma_thk_m: float
) -> str:
    return (
        f"enabled (n={pmma_ior:.2f}, T={pmma_t:.2f} @ {pmma_thk_m * 1e3:.1f} mm)"
        if pmma_mode
        else "disabled"
    )


def format_smd_ptfe_desc(
    *,
    ptfe_mode: bool,
    ptfe_reflectance: float,
    ptfe_thk_m: float,
    stack_aperture_m: float,
    stack_pocket_m: float,
) -> str:
    return (
        f"enabled (rho={ptfe_reflectance:.2f}, t={ptfe_thk_m * 1e3:.3f} mm, aperture={stack_aperture_m * 1e3:.1f} mm, pocket={stack_pocket_m * 1e3:.1f} mm)"
        if ptfe_mode
        else "disabled"
    )


def format_per_module_stats(
    per_module_mode: bool, per_mod_w: list[float] | None
) -> tuple[str, str]:
    per_module_note = ""
    if per_module_mode:
        per_module_note = (
            "  note        : per-module mode (ring lines show per-ring averages)\n"
        )
    per_module_stats = ""
    if per_module_mode and per_mod_w:
        w_min = float(min(per_mod_w))
        w_max = float(max(per_mod_w))
        w_avg = float(sum(per_mod_w) / max(1, len(per_mod_w)))
        per_module_stats = f"  per-module watts (W in, min/avg/max): {w_min:5.2f}/{w_avg:5.2f}/{w_max:5.2f}\n"
    return per_module_note, per_module_stats


def build_smd_summary_text(
    *,
    rings_local: int,
    module_count: int,
    module_profile_version: str,
    spacing_str: str,
    wall_margin_m: float,
    patch_side_m: float,
    optics_desc: str,
    source_variant_desc: str,
    pmma_desc: str,
    ptfe_desc: str,
    power_mode_desc: str,
    per_module_note: str,
    per_module_stats: str,
    subpatch_grid: int,
    ring_powers_source: str,
    runtime_snapshot: str,
    model_desc: str,
    derate_desc: str,
    ref_desc: str,
    droop_desc: str,
    thermal_desc: str,
    validation_text: str,
    scheduled_ppe_note: str,
    ring_lines: list[str],
    total_w_in: float,
    total_w_eff: float,
    ppe_str: str,
    avg_ppe: float,
    source_ppe_run: float,
    total_umol: float,
    wall_plug_ppe_run: float,
    total_output_umol: float,
    debug_text: str,
) -> str:
    return (
        "SMD macro emitter summary:\n"
        f"  rings       : {rings_local}  (center=ring 0)\n"
        f"  modules     : {module_count}  ({module_profile_version})\n"
        f"  spacing     : {spacing_str}  (outer margin {wall_margin_m:.3f} m)\n"
        f"  patch side  : {patch_side_m * 1e3:.1f} mm\n"
        + f"  optics      : {optics_desc}\n"
        + f"  source shape: {source_variant_desc}\n"
        + f"  pmma lid    : {pmma_desc}\n"
        + f"  ptfe cavity : {ptfe_desc}\n"
        + f"  power_mode  : {power_mode_desc}\n"
        + per_module_note
        + per_module_stats
        + f"  subgrid     : {subpatch_grid}x{subpatch_grid}\n"
        + f"  ring powers : {ring_powers_source}\n"
        + runtime_snapshot
        + model_desc
        + derate_desc
        + ref_desc
        + droop_desc
        + thermal_desc
        + validation_text
        + scheduled_ppe_note
        + (
            "  per-ring avg watts (per module, input → effective):\n"
            + "\n".join(ring_lines)
            + "\n"
        )
        + f"  total electrical input ≈ {total_w_in:.1f} W\n"
        + f"  total effective (derated) ≈ {total_w_eff:.1f} W\n"
        + f"  nominal PPE by package (µmol/J): {ppe_str}\n"
        + f"  avg PPE (package blend, base) ≈ {avg_ppe:.3f} µmol/J\n"
        + f"  run-average source PPE (pre-PMMA) ≈ {source_ppe_run:.3f} µmol/J → total source photons ≈ {total_umol:.0f} µmol/s\n"
        + f"  run-average wall-plug PPE (post-PMMA) ≈ {wall_plug_ppe_run:.3f} µmol/J → total emitted photons ≈ {total_output_umol:.0f} µmol/s\n"
        + (debug_text if debug_text else "")
    )


def write_smd_summary(path: Path, summary_text: str) -> None:
    path.write_text(summary_text)
