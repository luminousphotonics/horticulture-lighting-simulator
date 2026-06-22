#!/usr/bin/env python3
"""Helpers for SMD solution metadata and compatibility checks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, cast

from rad_rebuild.radiance.engine.geometry.generate_grid import build_grid_spec
from rad_rebuild.radiance.engine.emitters.smd_generation.module_profile import (
    MODULE_PROFILE_VERSION,
)
from rad_rebuild.radiance.engine.photometry.smd_curve_model import (
    CURVE_ROOT,
    DEFAULT_RED_PPE_CSV,
    DEFAULT_RED_VF_CSV,
    DEFAULT_WHITE_PPE_CSV,
    DEFAULT_WHITE_VF_CSV,
    curve_model_manifest,
)
from rad_rebuild.radiance.paths import RADIANCE_ENGINE_PACKAGE_ROOT


GENERATOR_PATH = RADIANCE_ENGINE_PACKAGE_ROOT / "emitters" / "generate_emitters_smd.py"
MODULE_PROFILE_PATH = (
    RADIANCE_ENGINE_PACKAGE_ROOT
    / "emitters"
    / "smd_generation"
    / "module_profile.py"
)
CURVE_MODEL_PATH = RADIANCE_ENGINE_PACKAGE_ROOT / "photometry" / "smd_curve_model.py"


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return ""


def _round6(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _normalize_room_dims(
    length_m: float | None, width_m: float | None
) -> tuple[float | None, float | None]:
    if length_m is None or width_m is None:
        return length_m, width_m
    a = float(length_m)
    b = float(width_m)
    if b > a:
        a, b = b, a
    return a, b


def _normalized_layout_snapshot(layout: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(layout or {})
    room_l, room_w = _normalize_room_dims(data.get("room_L_m"), data.get("room_W_m"))
    if room_l is not None:
        data["room_L_m"] = float(room_l)
    if room_w is not None:
        data["room_W_m"] = float(room_w)
    return data


def _env_float(
    name: str, default: float, env: Mapping[str, str] | None = None
) -> float:
    source = env if env is not None else os.environ
    try:
        return float(str(source.get(name, str(default))).strip() or default)
    except Exception:
        return float(default)


def _env_int(name: str, default: int, env: Mapping[str, str] | None = None) -> int:
    source = env if env is not None else os.environ
    try:
        return int(float(str(source.get(name, str(default))).strip() or default))
    except Exception:
        return int(default)


def _env_bool(name: str, default: bool, env: Mapping[str, str] | None = None) -> int:
    source = env if env is not None else os.environ
    return int((str(source.get(name, "1" if default else "0")).strip() or "0") != "0")


def _portable_curve_env_value(
    env_name: str,
    default_path: Path,
    env: Mapping[str, str],
) -> str:
    raw = str(env.get(env_name, "")).strip()
    if not raw:
        return default_path.name
    path = Path(raw).expanduser()
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    try:
        return str(resolved.relative_to(CURVE_ROOT.resolve()))
    except Exception:
        pass
    if resolved.name == default_path.name:
        return default_path.name
    return raw


def _normalize_curve_env_snapshot(
    env_snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    data = dict(env_snapshot or {})
    defaults = {
        "SMD_WHITE_VF_CSV": DEFAULT_WHITE_VF_CSV,
        "SMD_WHITE_PPE_CSV": DEFAULT_WHITE_PPE_CSV,
        "SMD_RED_VF_CSV": DEFAULT_RED_VF_CSV,
        "SMD_RED_PPE_CSV": DEFAULT_RED_PPE_CSV,
    }
    for key, default_path in defaults.items():
        data[key] = _portable_curve_env_value(
            key, default_path, {key: str(data.get(key, "") or "")}
        )
    return data


def _normalize_curve_model_fingerprint(
    curve_model: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(curve_model, Mapping):
        return None
    data = cast(dict[str, Any], json.loads(json.dumps(curve_model)))
    curve_files = data.get("curve_files")
    if isinstance(curve_files, dict):
        defaults = {
            "white_vf_csv": DEFAULT_WHITE_VF_CSV,
            "white_ppe_csv": DEFAULT_WHITE_PPE_CSV,
            "red_vf_csv": DEFAULT_RED_VF_CSV,
            "red_ppe_csv": DEFAULT_RED_PPE_CSV,
        }
        curve_files = {
            key: curve_files.get(key) for key in defaults if key in curve_files
        }
        for key, default_path in defaults.items():
            entry = curve_files.get(key)
            if isinstance(entry, dict):
                entry["path"] = _portable_curve_env_value(
                    "CURVE_PATH",
                    default_path,
                    {"CURVE_PATH": str(entry.get("path", "") or "")},
                )
        data["curve_files"] = curve_files
    channels = data.get("channels")
    if isinstance(channels, dict):
        for payload in channels.values():
            if not isinstance(payload, dict):
                continue
            payload.pop("spectral_par_photon_umol_per_radiant_w", None)
            payload.pop("nominal_radiant_efficiency", None)
            payload.pop("spectral_components", None)
    return data


def build_smd_emitter_env_snapshot(
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source = env if env is not None else os.environ
    return {
        "SMD_MODEL": (str(source.get("SMD_MODEL", "curve")).strip().lower() or "curve"),
        "SMD_MODULE_PROFILE": str(
            source.get("SMD_MODULE_PROFILE", MODULE_PROFILE_VERSION)
        ).strip(),
        "SMD_BASE_RING_N": _env_int("SMD_BASE_RING_N", 7, env=source),
        "SMD_PERIM_GAP_FILL": _env_bool("SMD_PERIM_GAP_FILL", False, env=source),
        "SMD_OUTER_PER_MODULE": _env_bool("SMD_OUTER_PER_MODULE", False, env=source),
        "SMD_ALL_PER_MODULE": _env_bool("SMD_ALL_PER_MODULE", False, env=source),
        "LAYOUT_MODE": str(source.get("LAYOUT_MODE", "square")).strip(),
        "LENGTH_FT": _env_float("LENGTH_FT", 12.0, env=source),
        "WIDTH_FT": _env_float("WIDTH_FT", 12.0, env=source),
        "SMD_FIXED_PITCH_M": _env_float("SMD_FIXED_PITCH_M", 0.0, env=source),
        "SMD_SOURCE_VARIANT": str(source.get("SMD_SOURCE_VARIANT", "native"))
        .strip()
        .lower()
        or "native",
        "SMD_OUTER_OPTICS": _env_bool("SMD_OUTER_OPTICS", False, env=source),
        "SMD_OUTER_FWHM_DEG": _env_float("SMD_OUTER_FWHM_DEG", 40.0, env=source),
        "SMD_ROW_ZONES": _env_bool("SMD_ROW_ZONES", False, env=source),
        "SMD_ROW_EDGE_FILL": _env_bool("SMD_ROW_EDGE_FILL", False, env=source),
        "SMD_PERIM_STRIPS": _env_bool("SMD_PERIM_STRIPS", False, env=source),
        "SMD_PERIM_STRIP_LEN_M": _env_float("SMD_PERIM_STRIP_LEN_M", 0.36, env=source),
        "SMD_PERIM_STRIP_WIDTH_M": _env_float(
            "SMD_PERIM_STRIP_WIDTH_M", 0.12, env=source
        ),
        "SMD_PERIM_STRIP_GAP_M": _env_float("SMD_PERIM_STRIP_GAP_M", 0.0, env=source),
        "DROOP_P_NOM": _env_float("DROOP_P_NOM", 100.0, env=source),
        "DROOP_K": _env_float("DROOP_K", 0.12, env=source),
        "PATCH_SIDE_M": _env_float("PATCH_SIDE_M", 0.12, env=source),
        "MODULE_SIDE_M": _env_float("MODULE_SIDE_M", 0.12, env=source),
        "MODULE_FOOTPRINT_X_M": _env_float("MODULE_FOOTPRINT_X_M", 0.1524, env=source),
        "MODULE_FOOTPRINT_Y_M": _env_float("MODULE_FOOTPRINT_Y_M", 0.1650, env=source),
        "SMD_FIXTURE_ANGLE_IN": _env_float("SMD_FIXTURE_ANGLE_IN", 0.25, env=source),
        "PMMA_MODE": _env_bool("PMMA_MODE", True, env=source),
        "PMMA_IOR": _env_float("PMMA_IOR", 1.49, env=source),
        "PMMA_T": _env_float("PMMA_T", 0.92, env=source),
        "PMMA_THK_M": _env_float("PMMA_THK_M", 0.003, env=source),
        "PMMA_SIDE_M": _env_float("PMMA_SIDE_M", 0.128, env=source),
        "PMMA_LID_OFFSET_M": _env_float("PMMA_LID_OFFSET_M", 0.008, env=source),
        "PTFE_MODE": _env_bool("PTFE_MODE", True, env=source),
        "PTFE_REFLECTANCE": _env_float("PTFE_REFLECTANCE", 0.97, env=source),
        "PTFE_THK_M": _env_float("PTFE_THK_M", 0.000508, env=source),
        "STACK_APERTURE_M": _env_float("STACK_APERTURE_M", 0.126, env=source),
        "MARGIN_IN": _env_float("MARGIN_IN", 1.0, env=source),
        "PPE_IS_SYSTEM": _env_bool("PPE_IS_SYSTEM", False, env=source),
        "SMD_TARGET_PPE_UMOL_PER_J": _env_float(
            "SMD_TARGET_PPE_UMOL_PER_J", 0.0, env=source
        ),
        "SMD_WW_COUNT": _env_int("SMD_WW_COUNT", 52, env=source),
        "SMD_CW_COUNT": _env_int("SMD_CW_COUNT", 52, env=source),
        "SMD_RED_COUNT": _env_int("SMD_RED_COUNT", 41, env=source),
        "SMD_WW_NOMINAL_W": _env_float("SMD_WW_NOMINAL_W", 0.68, env=source),
        "SMD_CW_NOMINAL_W": _env_float("SMD_CW_NOMINAL_W", 0.68, env=source),
        "SMD_RED_NOMINAL_W": _env_float("SMD_RED_NOMINAL_W", 0.44, env=source),
        "SMD_WW_NOMINAL_PPE": _env_float("SMD_WW_NOMINAL_PPE", 2.73, env=source),
        "SMD_CW_NOMINAL_PPE": _env_float("SMD_CW_NOMINAL_PPE", 2.81, env=source),
        "SMD_RED_NOMINAL_PPE": _env_float("SMD_RED_NOMINAL_PPE", 4.13, env=source),
        "DRIVER_EFF": _env_float("DRIVER_EFF", 0.96, env=source),
        "THERMAL_EFF": _env_float("THERMAL_EFF", 1.0, env=source),
        "BOARD_OPT_EFF": _env_float("BOARD_OPT_EFF", 1.0, env=source),
        "WIRING_EFF": _env_float("WIRING_EFF", 0.99, env=source),
        "EFF_SCALE": _env_float("EFF_SCALE", 1.0, env=source),
        "SMD_PPE_REFERENCE_MODE": str(
            source.get("SMD_PPE_REFERENCE_MODE", "nominal_current")
        )
        .strip()
        .lower(),
        "SMD_THERMAL_REF_INPUT_W": _env_float(
            "SMD_THERMAL_REF_INPUT_W", 0.0, env=source
        ),
        "SMD_THERMAL_REF_MULTIPLIER": _env_float(
            "SMD_THERMAL_REF_MULTIPLIER", 0.985, env=source
        ),
        "SMD_THERMAL_SLOPE_PER_W": _env_float(
            "SMD_THERMAL_SLOPE_PER_W", 0.0015, env=source
        ),
        "SMD_THERMAL_MIN_MULTIPLIER": _env_float(
            "SMD_THERMAL_MIN_MULTIPLIER", 0.90, env=source
        ),
        "SMD_THERMAL_MAX_MULTIPLIER": _env_float(
            "SMD_THERMAL_MAX_MULTIPLIER", 1.00, env=source
        ),
        "SMD_WHITE_VF_CSV": _portable_curve_env_value(
            "SMD_WHITE_VF_CSV", DEFAULT_WHITE_VF_CSV, source
        ),
        "SMD_WHITE_PPE_CSV": _portable_curve_env_value(
            "SMD_WHITE_PPE_CSV", DEFAULT_WHITE_PPE_CSV, source
        ),
        "SMD_RED_VF_CSV": _portable_curve_env_value(
            "SMD_RED_VF_CSV", DEFAULT_RED_VF_CSV, source
        ),
        "SMD_RED_PPE_CSV": _portable_curve_env_value(
            "SMD_RED_PPE_CSV", DEFAULT_RED_PPE_CSV, source
        ),
        "OPTICS": str(source.get("OPTICS", "stack")).strip(),
        "SUBPATCH_GRID": _env_int("SUBPATCH_GRID", 1, env=source),
    }


def build_smd_sensor_grid_snapshot(
    *,
    env: Mapping[str, str] | None = None,
    sensor_path: Path | None = None,
) -> dict[str, Any]:
    spec = build_grid_spec(env=dict(env) if env is not None else None)
    snapshot = _base_sensor_grid_snapshot(spec)
    if sensor_path and sensor_path.exists():
        try:
            snapshot.update(_sensor_file_snapshot(sensor_path, spec))
        except (OSError, ValueError):
            snapshot["sensor_file"] = str(sensor_path)
    return snapshot


def _base_sensor_grid_snapshot(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sample_layout": str(spec.get("sample_layout", "centered")),
        "grid_z_m": _round6(spec.get("grid_z_m")),
        "target_spacing_m": _round6(spec.get("target_spacing_m")),
        "min_points_x": int(spec.get("min_points_x", 0) or 0),
        "min_points_y": int(spec.get("min_points_y", 0) or 0),
        "min_floor_points_x": int(spec.get("min_floor_points_x", 0) or 0),
        "min_floor_points_y": int(spec.get("min_floor_points_y", 0) or 0),
        "max_points_x": int(spec.get("max_points_x", 0) or 0),
        "max_points_y": int(spec.get("max_points_y", 0) or 0),
        "wall_margin_m": _round6(spec.get("wall_margin_m")),
        "grid_module_side_m": _round6(spec.get("grid_module_side_m")),
        "length_m": _round6(spec.get("length_m")),
        "width_m": _round6(spec.get("width_m")),
        "interior_length_m": _round6(spec.get("interior_length_m")),
        "interior_width_m": _round6(spec.get("interior_width_m")),
        "base_resolution_x": int(spec.get("base_resolution_x", 0) or 0),
        "base_resolution_y": int(spec.get("base_resolution_y", 0) or 0),
        "resolution_x": int(spec.get("resolution_x", 0) or 0),
        "resolution_y": int(spec.get("resolution_y", 0) or 0),
        "explicit_resolution": bool(spec.get("explicit_resolution", False)),
        "minimum_floor_enforced_x": bool(spec.get("minimum_floor_enforced_x", False)),
        "minimum_floor_enforced_y": bool(spec.get("minimum_floor_enforced_y", False)),
        "n_points": int(spec.get("n_points", 0) or 0),
        "x_min": _round6(spec.get("x_min")),
        "x_max": _round6(spec.get("x_max")),
        "y_min": _round6(spec.get("y_min")),
        "y_max": _round6(spec.get("y_max")),
    }


def _sensor_file_snapshot(sensor_path: Path, spec: Mapping[str, Any]) -> dict[str, Any]:
    xs: set[float] = set()
    ys: set[float] = set()
    n_points = 0
    x_min = y_min = z_min = None
    x_max = y_max = z_max = None
    with sensor_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) < 3:
                continue
            x = round(float(parts[0]), 6)
            y = round(float(parts[1]), 6)
            z = round(float(parts[2]), 6)
            xs.add(x)
            ys.add(y)
            n_points += 1
            x_min = x if x_min is None else min(x_min, x)
            x_max = x if x_max is None else max(x_max, x)
            y_min = y if y_min is None else min(y_min, y)
            y_max = y if y_max is None else max(y_max, y)
            z_min = z if z_min is None else min(z_min, z)
            z_max = z if z_max is None else max(z_max, z)
    return {
        "sensor_file": str(sensor_path),
        "sensor_file_sha256": _sha256_file(sensor_path),
        "n_points": int(n_points),
        "resolution_x": int(len(xs)),
        "resolution_y": int(len(ys)),
        "grid_z_m": z_min if z_min == z_max else _round6(spec.get("grid_z_m")),
        "x_min": x_min,
        "x_max": x_max,
        "y_min": y_min,
        "y_max": y_max,
    }


def build_smd_layout_snapshot(
    layout_meta: dict[str, Any] | None = None,
    module_count: int | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    meta = layout_meta or {}
    room_l, room_w = _layout_room_dimensions(meta, env)
    if room_l is None or room_w is None:
        raise ValueError("SMD layout room dimensions are required.")
    ring_n, rings = _layout_ring_fields(meta)
    return {
        "layout_mode": str(
            meta.get("layout_mode", (env or os.environ).get("LAYOUT_MODE", "square"))
        ),
        "layout_family": meta.get("layout_family"),
        "room_L_m": float(room_l),
        "room_W_m": float(room_w),
        "ring_n": int(ring_n) if ring_n is not None else None,
        "rings": int(rings) if rings is not None else None,
        "module_count": int(module_count) if module_count is not None else None,
        "spacing_m": float(meta.get("spacing_m", 0.0) or 0.0),
        "pitch_x_m": float(meta.get("pitch_x_m", 0.0) or 0.0),
        "pitch_y_m": float(meta.get("pitch_y_m", 0.0) or 0.0),
        "base_n": int(meta.get("base_n", 0) or 0),
    }


def _layout_room_dimensions(
    meta: Mapping[str, Any], env: Mapping[str, str] | None
) -> tuple[float | None, float | None]:
    room_l = meta.get("room_L_m")
    room_w = meta.get("room_W_m")
    if room_l is None:
        room_l = _env_float(
            "LENGTH_M", _env_float("LENGTH_FT", 12.0, env=env) * 0.3048, env=env
        )
    if room_w is None:
        room_w = _env_float(
            "WIDTH_M", _env_float("WIDTH_FT", 12.0, env=env) * 0.3048, env=env
        )
    return _normalize_room_dims(room_l, room_w)


def _layout_ring_fields(meta: Mapping[str, Any]) -> tuple[int | None, int | None]:
    rings = meta.get("rings")
    ring_n = meta.get("ring_n")
    if rings is None and ring_n is not None:
        rings = int(ring_n) + 1
    if ring_n is None and rings is not None:
        ring_n = max(0, int(rings) - 1)
    return (
        int(ring_n) if ring_n is not None else None,
        int(rings) if rings is not None else None,
    )


def build_smd_runtime_fingerprint_from_env(
    *,
    layout_meta: dict[str, Any] | None = None,
    module_count: int | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    env_snapshot = build_smd_emitter_env_snapshot(env=env)
    model = str(env_snapshot.get("SMD_MODEL", "curve"))
    return {
        "schema_version": 1,
        "generator_sha256": _sha256_file(GENERATOR_PATH),
        "module_profile_sha256": _sha256_file(MODULE_PROFILE_PATH),
        "curve_model_sha256": _sha256_file(CURVE_MODEL_PATH)
        if model != "legacy"
        else "",
        "emitter_env": env_snapshot,
        "curve_model": curve_model_manifest(env=env) if model != "legacy" else None,
        "layout": build_smd_layout_snapshot(
            layout_meta=layout_meta, module_count=module_count, env=env
        ),
        "sensor_grid": build_smd_sensor_grid_snapshot(env=env),
    }


def build_smd_runtime_fingerprint_from_basis_manifest(
    manifest: dict[str, Any] | None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(manifest, dict):
        return None
    emitter_env = _normalize_curve_env_snapshot(
        manifest.get("emitter_env")
        if isinstance(manifest.get("emitter_env"), dict)
        else {}
    )
    if not emitter_env:
        return None
    model = str(emitter_env.get("SMD_MODEL", "curve"))
    layout = build_smd_layout_snapshot(
        {
            "layout_mode": emitter_env.get("LAYOUT_MODE"),
            "layout_family": manifest.get("layout_family"),
            "room_L_m": manifest.get("room_L_m"),
            "room_W_m": manifest.get("room_W_m"),
            "ring_n": (int(manifest.get("n_rings", 0)) - 1)
            if manifest.get("n_rings") is not None
            else None,
            "rings": manifest.get("n_rings"),
            "spacing_m": manifest.get("spacing_m"),
            "pitch_x_m": manifest.get("pitch_x_m"),
            "pitch_y_m": manifest.get("pitch_y_m"),
            "base_n": manifest.get("base_n"),
        },
        module_count=manifest.get("layout_modules"),
        env=env,
    )
    return {
        "schema_version": 1,
        "generator_sha256": manifest.get("generator_sha256", ""),
        "module_profile_sha256": manifest.get("module_profile_sha256", ""),
        "curve_model_sha256": manifest.get("curve_model_sha256", "")
        if model != "legacy"
        else "",
        "emitter_env": emitter_env,
        "curve_model": _normalize_curve_model_fingerprint(manifest.get("curve_model"))
        if model != "legacy"
        else None,
        "layout": layout,
        "sensor_grid": manifest.get("sensor_grid"),
    }


def build_smd_solution_metadata(
    *,
    runtime_fingerprint: dict[str, Any] | None,
    basis_manifest: dict[str, Any] | None,
    basis_path: Path | None,
    n_points: int,
) -> dict[str, Any]:
    basis_sig = {}
    if isinstance(basis_manifest, dict):
        basis_sig = {
            "variables": basis_manifest.get("variables"),
            "n_rings": basis_manifest.get("n_rings"),
            "layout_modules": basis_manifest.get("layout_modules"),
            "ring_indices": basis_manifest.get("ring_indices"),
            "module_indices": basis_manifest.get("module_indices"),
            "outer_ring_index": basis_manifest.get("outer_ring_index"),
            "outer_ring_indices": basis_manifest.get("outer_ring_indices"),
            "layout_family": basis_manifest.get("layout_family"),
        }
    return {
        "schema_version": 1,
        "runtime_fingerprint": runtime_fingerprint,
        "basis_manifest_sha256": _sha256_file(
            (basis_path.parent / "basis_manifest.json")
        )
        if basis_path
        else "",
        "basis_file_sha256": _sha256_file(basis_path) if basis_path else "",
        "basis_file": str(basis_path) if basis_path else None,
        "n_points": int(n_points),
        "basis_signature": basis_sig,
    }


def compare_smd_runtime_fingerprints(
    saved: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(saved, dict):
        return ["missing runtime fingerprint metadata"]
    if not isinstance(current, dict):
        return ["missing current runtime fingerprint"]
    reasons: list[str] = []
    _append_basic_fingerprint_reasons(saved, current, reasons)
    _append_emitter_env_reasons(saved, current, reasons)
    _append_curve_fingerprint_reasons(saved, current, reasons)
    saved_layout, current_layout = _layout_snapshots_for_compare(saved, current)
    _append_layout_reasons(saved_layout, current_layout, reasons)
    _append_layout_family_reason(saved_layout, current_layout, reasons)
    saved_grid, current_grid = _sensor_grid_snapshots_for_compare(saved, current)
    _append_sensor_grid_exact_reasons(saved_grid, current_grid, reasons)
    if not reasons:
        _append_sensor_grid_float_reasons(saved_grid, current_grid, reasons)
    return reasons


def _append_basic_fingerprint_reasons(
    saved: Mapping[str, Any], current: Mapping[str, Any], reasons: list[str]
) -> None:
    if saved.get("schema_version") != current.get("schema_version"):
        reasons.append("fingerprint schema version changed")
    if saved.get("generator_sha256") != current.get("generator_sha256"):
        reasons.append("generate_emitters_smd.py changed")
    if saved.get("module_profile_sha256") != current.get("module_profile_sha256"):
        reasons.append("smd_generation/module_profile.py changed")


def _fingerprint_child(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    child = mapping.get(key)
    return cast(dict[str, Any], child) if isinstance(child, dict) else {}


def _append_emitter_env_reasons(
    saved: Mapping[str, Any], current: Mapping[str, Any], reasons: list[str]
) -> None:
    saved_env = _normalize_curve_env_snapshot(_fingerprint_child(saved, "emitter_env"))
    current_env = _normalize_curve_env_snapshot(
        _fingerprint_child(current, "emitter_env")
    )
    for key, current_value in current_env.items():
        if key not in saved_env:
            reasons.append(f"saved fingerprint missing emitter config: {key}")
            break
        saved_value = saved_env.get(key)
        if saved_value != current_value:
            reasons.append(
                f"SMD emitter config changed: {key} {saved_value} -> {current_value}"
            )
            break


def _append_curve_fingerprint_reasons(
    saved: Mapping[str, Any], current: Mapping[str, Any], reasons: list[str]
) -> None:
    if _normalize_curve_model_fingerprint(
        saved.get("curve_model")
    ) != _normalize_curve_model_fingerprint(current.get("curve_model")):
        reasons.append("curve-model fingerprint changed")


def _layout_snapshots_for_compare(
    saved: Mapping[str, Any], current: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        _normalized_layout_snapshot(_fingerprint_child(saved, "layout")),
        _normalized_layout_snapshot(_fingerprint_child(current, "layout")),
    )


def _append_layout_reasons(
    saved_layout: Mapping[str, Any],
    current_layout: Mapping[str, Any],
    reasons: list[str],
) -> None:
    for key in (
        "layout_mode",
        "room_L_m",
        "room_W_m",
        "ring_n",
        "rings",
        "module_count",
    ):
        reason = _layout_field_reason(saved_layout, current_layout, key)
        if reason:
            reasons.append(reason)
            break


def _layout_field_reason(
    saved_layout: Mapping[str, Any], current_layout: Mapping[str, Any], key: str
) -> str | None:
    current_value = current_layout.get(key)
    if current_value is None:
        return None
    if key not in saved_layout:
        return f"saved fingerprint missing layout field: {key}"
    saved_value = saved_layout.get(key)
    if saved_value is None:
        return f"layout fingerprint changed: {key} {saved_value} -> {current_value}"
    if key in {"room_L_m", "room_W_m"}:
        if abs(float(saved_value) - float(current_value)) > 1e-6:
            return f"layout fingerprint changed: {key} {saved_value} -> {current_value}"
        return None
    if saved_value != current_value:
        return f"layout fingerprint changed: {key} {saved_value} -> {current_value}"
    return None


def _append_layout_family_reason(
    saved_layout: Mapping[str, Any],
    current_layout: Mapping[str, Any],
    reasons: list[str],
) -> None:
    if (
        reasons
        or "layout_family" not in current_layout
        or "layout_family" not in saved_layout
    ):
        return
    current_value = current_layout.get("layout_family")
    saved_value = saved_layout.get("layout_family")
    if (
        current_value is not None
        and saved_value is not None
        and saved_value != current_value
    ):
        reasons.append(
            f"layout fingerprint changed: layout_family {saved_value} -> {current_value}"
        )


def _sensor_grid_snapshots_for_compare(
    saved: Mapping[str, Any], current: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        _fingerprint_child(saved, "sensor_grid"),
        _fingerprint_child(current, "sensor_grid"),
    )


def _append_sensor_grid_exact_reasons(
    saved_grid: Mapping[str, Any],
    current_grid: Mapping[str, Any],
    reasons: list[str],
) -> None:
    for key in (
        "sample_layout",
        "resolution_x",
        "resolution_y",
        "n_points",
        "explicit_resolution",
        "min_points_x",
        "min_points_y",
        "max_points_x",
        "max_points_y",
    ):
        reason = _sensor_grid_exact_reason(saved_grid, current_grid, key)
        if reason:
            reasons.append(reason)
            break


def _sensor_grid_exact_reason(
    saved_grid: Mapping[str, Any], current_grid: Mapping[str, Any], key: str
) -> str | None:
    current_value = current_grid.get(key)
    saved_value = saved_grid.get(key)
    if current_value is None:
        return None
    if saved_value is None:
        return f"saved fingerprint missing sensor-grid field: {key}"
    if saved_value != current_value:
        return (
            f"sensor-grid fingerprint changed: {key} {saved_value} -> {current_value}"
        )
    return None


def _append_sensor_grid_float_reasons(
    saved_grid: Mapping[str, Any],
    current_grid: Mapping[str, Any],
    reasons: list[str],
) -> None:
    for key in (
        "grid_z_m",
        "target_spacing_m",
        "wall_margin_m",
        "grid_module_side_m",
        "length_m",
        "width_m",
        "interior_length_m",
        "interior_width_m",
        "x_min",
        "x_max",
        "y_min",
        "y_max",
    ):
        reason = _sensor_grid_float_reason(saved_grid, current_grid, key)
        if reason:
            reasons.append(reason)
            break


def _sensor_grid_float_reason(
    saved_grid: Mapping[str, Any], current_grid: Mapping[str, Any], key: str
) -> str | None:
    current_value = current_grid.get(key)
    saved_value = saved_grid.get(key)
    if current_value is None:
        return None
    if saved_value is None:
        return f"saved fingerprint missing sensor-grid field: {key}"
    if abs(float(saved_value) - float(current_value)) > 1e-6:
        return (
            f"sensor-grid fingerprint changed: {key} {saved_value} -> {current_value}"
        )
    return None


def format_fingerprint_mismatch(reasons: list[str]) -> str:
    if not reasons:
        return "compatible"
    return "; ".join(str(reason) for reason in reasons)


def json_dumps_compact(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))
