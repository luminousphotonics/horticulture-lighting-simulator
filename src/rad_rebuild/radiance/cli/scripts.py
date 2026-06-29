"""Python orchestration for Radiance shell compatibility entry points."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import secrets
import shutil
import subprocess  # nosec B404 - CLI orchestration invokes fixed command argument lists.
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

import numpy as np

from rad_rebuild.radiance.engine.emitters.generate_emitters_smd import _compute_positions_from_env
from rad_rebuild.radiance.engine.emitters.smd_generation.solution_metadata import (
    build_smd_runtime_fingerprint_from_basis_manifest,
    build_smd_runtime_fingerprint_from_env,
    build_smd_sensor_grid_snapshot,
    build_smd_solution_metadata,
    compare_smd_runtime_fingerprints,
)
from rad_rebuild.radiance.engine.optimization.solve_uniformity import (
    build_basis_solve_transform,
    load_basis_manifest,
    solution_coefficients_from_json,
)
from rad_rebuild.radiance.engine.photometry.ppfd_metrics import compute_ppfd_metrics, format_ppfd_metrics_line
from rad_rebuild.radiance.engine.plants.artifacts import (
    PlantArtifactPaths,
    write_plant_artifacts,
)
from rad_rebuild.radiance.engine.plants.config import (
    PlantGeometryConfig,
    PlantOpticalAssumptions,
)
from rad_rebuild.radiance.engine.plants.generator import generate_plant_scene
from rad_rebuild.radiance.engine.plants.leaf_materials import (
    FSPM_LEAF_RADIANCE_MATERIAL_MODE_ENV,
    FSPM_SPECTRAL_TRANSPORT_MODE_ENV,
    LEAF_RADIANCE_MATERIAL_MODE_OPAQUE_OCCLUDER,
    LEAF_RADIANCE_MATERIAL_MODE_REX_SOURCE_WEIGHTED_TRANS,
    PAR_BAND_IDS,
    SPECTRAL_TRANSPORT_MODE_BANDED_5,
    build_banded_5_transport_material_plan,
    fit_diffuse_trans_material,
    normalize_fspm_spectral_transport_mode,
    normalize_leaf_radiance_material_mode,
    opaque_leaf_material_metadata,
    par_source_weighted_leaf_coefficients,
    radiance_trans_material_definition,
    rex_source_weighted_leaf_material_metadata,
)
from rad_rebuild.radiance.engine.plants.optical_profiles import (
    REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,
)
from rad_rebuild.radiance.engine.plants.photomorphogenesis import (
    PhotomorphogenesisResponseParameters,
    write_plant_photomorphogenesis_response_artifact,
)
from rad_rebuild.radiance.engine.plants.photoreceptor import (
    write_plant_photoreceptor_exposure_artifact,
)
from rad_rebuild.radiance.engine.plants.photosynthesis import (
    PhotosynthesisResponseParameters,
    write_plant_photosynthesis_response_artifact,
)
from rad_rebuild.radiance.engine.plants.radiance_export import export_scene_to_radiance
from rad_rebuild.radiance.engine.plants.spectral import (
    fixture_spectral_distribution_from_curve_data,
    default_leafy_green_spectral_bands,
    parse_spectral_photon_fraction_overrides,
    write_plant_spectral_response_artifact,
)
from rad_rebuild.radiance.engine.plants.spectral_absorption import (
    PLANT_SPECTRAL_ABSORPTION_FILENAME,
    leaf_optical_profile_from_env,
    wavelength_photon_distribution_from_band_fractions,
    wavelength_photon_distribution_from_curve_data,
    write_banded_plant_spectral_absorption_artifact,
    write_plant_spectral_absorption_artifact,
)
from rad_rebuild.radiance.engine.plants.surface_flux import (
    FSPM_RECEIVER_GRANULARITY_ENV,
    RADIANCE_RECEIVER_METHOD,
    build_radiance_receiver_samples,
    build_radiance_receiver_surface_flux_rows,
    normalize_receiver_granularity,
    parse_rtrace_receiver_output,
    receiver_sample_input_text,
    write_radiance_receiver_plant_surface_flux_artifact,
)
from rad_rebuild.radiance.engine.simulation.basis_backends import canonicalize_basis_backend
from rad_rebuild.radiance.fspm_targets import (
    resolve_fspm_target_ppfd,
    resolve_fspm_target_tolerance,
)
from rad_rebuild.radiance.paths import REPO_ROOT


class RadianceScriptExit(IntEnum):
    OK = 0
    USAGE = 2
    VALIDATION = 3
    COMMAND_FAILED = 4


@dataclass(frozen=True)
class ScriptEvent:
    level: str
    code: str
    message: str
    details: Mapping[str, object] | None = None

    def to_json(self) -> str:
        payload: dict[str, object] = {
            "level": self.level,
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = dict(self.details)
        return json.dumps(payload, sort_keys=True)


@dataclass(frozen=True)
class UniformityConfig:
    py: str
    repo_root: Path
    script_dir: Path
    root: Path
    data_root: Path
    curve_data_root: Path
    basis_output_root: Path
    runtime_state_root: Path
    cache_root: Path
    run_basis: str
    basis_path: Path
    out_json: Path
    target_ppfd: str
    w_min: str
    w_max: str
    lambda_s: tuple[str, ...]
    lambda_r: tuple[str, ...]
    lambda_mean: str
    lambda_smooth: str
    use_chebyshev: str
    solve_method: str
    solve_mode: str
    uniform_tag: str
    uniform_layout: str
    smd_outer_per_module: str
    smd_all_per_module: str
    smd_var_mode: str
    smd_basis_backend: str
    smd_auto_dim: str
    mean_tol: str
    desired_var_mode: str
    env: dict[str, str]


def _print_event(event: ScriptEvent) -> None:
    print(event.to_json(), file=sys.stderr)


def _propagated_return_code(returncode: int) -> int:
    if returncode < 0:
        return 128 + abs(returncode)
    return returncode


def _env_text(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key)
    return default if value is None or value == "" else value


def _split_words(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.split() if part)


def _prepend_pythonpath(env: dict[str, str], repo_root: Path) -> None:
    src_path = str(repo_root / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_path if not existing else f"{src_path}{os.pathsep}{existing}"


def _apply_smd_defaults(env: dict[str, str], curve_data_root: Path) -> None:
    smd_curve_root = curve_data_root / "smd"
    env["SMD_MODEL"] = _env_text(env, "SMD_MODEL", "curve")
    if env["SMD_MODEL"] != "legacy":
        env["PPE_IS_SYSTEM"] = _env_text(env, "PPE_IS_SYSTEM", "0")
    defaults = {
        "SMD_TARGET_PPE_UMOL_PER_J": "0.0",
        "SMD_WW_COUNT": "52",
        "SMD_CW_COUNT": "52",
        "SMD_RED_COUNT": "41",
        "SMD_WW_NOMINAL_W": "0.68",
        "SMD_CW_NOMINAL_W": "0.68",
        "SMD_RED_NOMINAL_W": "0.44",
        "SMD_WW_NOMINAL_PPE": "2.73",
        "SMD_CW_NOMINAL_PPE": "2.81",
        "SMD_RED_NOMINAL_PPE": "4.13",
        "DRIVER_EFF": "0.96",
        "WIRING_EFF": "0.99",
        "SMD_PPE_REFERENCE_MODE": "nominal_current",
        "SMD_THERMAL_REF_INPUT_W": "93.392256",
        "SMD_THERMAL_REF_MULTIPLIER": "0.985",
        "SMD_THERMAL_SLOPE_PER_W": "0.0015",
        "SMD_THERMAL_MIN_MULTIPLIER": "0.90",
        "SMD_THERMAL_MAX_MULTIPLIER": "1.00",
        "SMD_WHITE_VF_CSV": str(smd_curve_root / "white_fC_vs_fV.csv"),
        "SMD_WHITE_PPE_CSV": str(smd_curve_root / "white_ppe_vs_fC.csv"),
        "SMD_RED_VF_CSV": str(smd_curve_root / "red_fC_vs_fV.csv"),
        "SMD_RED_PPE_CSV": str(smd_curve_root / "red_ppe_vs_fC.csv"),
    }
    for key, value in defaults.items():
        env[key] = _env_text(env, key, value)


def _normalize_variable_mode(
    *,
    smd_var_mode: str,
    smd_all_per_module: str,
    smd_outer_per_module: str,
) -> tuple[str, str, str]:
    if smd_var_mode:
        match smd_var_mode:
            case "module" | "modules" | "per_module":
                smd_all_per_module = "1"
                smd_outer_per_module = "0"
            case "outer" | "ring_plus_outer" | "outer_modules":
                smd_outer_per_module = "1"
                smd_all_per_module = "0"
            case "ring" | "rings":
                smd_outer_per_module = "0"
                smd_all_per_module = "0"
            case _:
                pass
    if smd_all_per_module == "1":
        smd_outer_per_module = "0"
    return smd_var_mode, smd_all_per_module, smd_outer_per_module


def _desired_var_mode(smd_all_per_module: str, smd_outer_per_module: str) -> str:
    if smd_all_per_module == "1":
        return "per_module"
    if smd_outer_per_module == "1":
        return "ring_plus_outer_modules"
    return "rings"


def _build_uniformity_config(raw_env: Mapping[str, str] | None = None) -> UniformityConfig:
    env = dict(os.environ if raw_env is None else raw_env)
    repo_root = REPO_ROOT
    script_dir = repo_root / "scripts" / "radiance"
    py = _env_text(env, "PY", sys.executable)
    root = Path(_env_text(env, "RADIANCE_OUTPUT_ROOT", str(repo_root / "outputs" / "radiance")))
    data_root = Path(_env_text(env, "RADIANCE_DATA_ROOT", str(repo_root / "data" / "radiance")))
    curve_data_root = Path(_env_text(env, "RADIANCE_CURVE_DATA_ROOT", str(data_root / "curve_data")))
    basis_output_root = Path(_env_text(env, "RADIANCE_BASIS_OUTPUT_ROOT", str(root / "basis")))
    runtime_state_root = Path(_env_text(env, "RADIANCE_RUNTIME_STATE_ROOT", str(root / "runtime_state")))
    cache_root = Path(_env_text(env, "RADIANCE_CACHE_ROOT", str(root / "cache")))

    env.update(
        {
            "ROOT": str(root),
            "RADIANCE_DATA_ROOT": str(data_root),
            "RADIANCE_CURVE_DATA_ROOT": str(curve_data_root),
            "RADIANCE_OUTPUT_ROOT": str(root),
            "RADIANCE_BASIS_OUTPUT_ROOT": str(basis_output_root),
            "RADIANCE_CACHE_ROOT": str(cache_root),
            "RADIANCE_RUNTIME_STATE_ROOT": str(runtime_state_root),
        }
    )
    _prepend_pythonpath(env, repo_root)
    _apply_smd_defaults(env, curve_data_root)

    run_basis = _env_text(env, "RUN_BASIS", "0")
    basis_path = Path(_env_text(env, "BASIS_PATH", str(basis_output_root / "basis_A.npy")))
    out_json = Path(_env_text(env, "OUT_JSON", str(root / "ring_powers_optimized.json")))
    target_ppfd = _env_text(env, "TARGET_PPFD", "1200")
    w_min = _env_text(env, "W_MIN", "10")
    w_max = _env_text(env, "W_MAX", "100")
    lambda_s = _split_words(_env_text(env, "LAMBDA_S", "0.0 1e-3 1e-2 1e-1 1.0 10.0 30.0"))
    lambda_r = _split_words(_env_text(env, "LAMBDA_R", "0.0 1e-3 1e-2 1e-1"))
    lambda_mean = _env_text(env, "LAMBDA_MEAN", "10.0")
    lambda_smooth = _env_text(env, "LAMBDA_SMOOTH", "0.0")
    use_chebyshev = _env_text(env, "USE_CHEBYSHEV", "1")
    solve_method = _env_text(env, "SOLVE_METHOD", "minvar_qp")
    solve_mode = _env_text(env, "SOLVE_MODE", "solver").lower()
    uniform_tag = "uniform"
    uniform_layout = ""
    smd_outer_per_module = _env_text(env, "SMD_OUTER_PER_MODULE", "0")
    smd_all_per_module = _env_text(env, "SMD_ALL_PER_MODULE", "0")
    smd_var_mode = _env_text(env, "SMD_VAR_MODE", "")

    if solve_mode == "formula":
        solve_mode = "uniform"
    if solve_mode in {"uniform_grid", "uniform-grid"}:
        solve_mode = "uniform"
        uniform_tag = "uniform_grid"
        uniform_layout = "grid"
    if solve_mode in {"module", "modules", "per_module"}:
        solve_mode = "solver"
        smd_var_mode = "module"

    smd_var_mode, smd_all_per_module, smd_outer_per_module = _normalize_variable_mode(
        smd_var_mode=smd_var_mode,
        smd_all_per_module=smd_all_per_module,
        smd_outer_per_module=smd_outer_per_module,
    )
    smd_auto_dim = _env_text(env, "SMD_AUTO_DIM", "")
    if not smd_auto_dim:
        smd_auto_dim = "1" if smd_all_per_module == "1" else "0"
    if smd_all_per_module == "1" and f"{float(w_min):.6f}" != "0.000000":
        print("Per-module solve: forcing W_MIN=0 for uniformity headroom.")
        w_min = "0"
    if uniform_layout:
        env["LAYOUT_MODE"] = uniform_layout
    smd_basis_backend = canonicalize_basis_backend(_env_text(env, "SMD_BASIS_BACKEND", "rtrace"))
    desired_var_mode = _desired_var_mode(smd_all_per_module, smd_outer_per_module)

    env.update(
        {
            "SMD_OUTER_PER_MODULE": smd_outer_per_module,
            "SMD_ALL_PER_MODULE": smd_all_per_module,
            "SMD_VAR_MODE": smd_var_mode,
            "SMD_BASIS_BACKEND": smd_basis_backend,
        }
    )

    return UniformityConfig(
        py=py,
        repo_root=repo_root,
        script_dir=script_dir,
        root=root,
        data_root=data_root,
        curve_data_root=curve_data_root,
        basis_output_root=basis_output_root,
        runtime_state_root=runtime_state_root,
        cache_root=cache_root,
        run_basis=run_basis,
        basis_path=basis_path,
        out_json=out_json,
        target_ppfd=target_ppfd,
        w_min=w_min,
        w_max=w_max,
        lambda_s=lambda_s,
        lambda_r=lambda_r,
        lambda_mean=lambda_mean,
        lambda_smooth=lambda_smooth,
        use_chebyshev=use_chebyshev,
        solve_method=solve_method,
        solve_mode=solve_mode,
        uniform_tag=uniform_tag,
        uniform_layout=uniform_layout,
        smd_outer_per_module=smd_outer_per_module,
        smd_all_per_module=smd_all_per_module,
        smd_var_mode=smd_var_mode,
        smd_basis_backend=smd_basis_backend,
        smd_auto_dim=smd_auto_dim,
        mean_tol=_env_text(env, "MEAN_TOL", "0.005"),
        desired_var_mode=desired_var_mode,
        env=env,
    )


def build_uniformity_solver_argv(config: UniformityConfig, manifest: Mapping[str, Any] | None = None) -> list[str]:
    smooth_group_args: list[str] = []
    if config.desired_var_mode == "ring_plus_outer_modules" and manifest:
        groups = manifest.get("variable_groups") or {}
        if isinstance(groups, Mapping):
            rings = groups.get("rings")
            outer = groups.get("outer_modules")
            if rings and outer:
                smooth_group_args = ["--smooth-groups", str(int(rings)), str(int(outer))]

    chebyshev_args = ["--use-chebyshev"] if config.use_chebyshev == "1" else []
    return [
        "-m",
        "rad_rebuild.radiance.engine.optimization.solve_uniformity",
        "--solve-method",
        config.solve_method,
        "--basis",
        str(config.basis_path),
        "--target-ppfd",
        config.target_ppfd,
        "--w-min",
        config.w_min,
        "--w-max",
        config.w_max,
        "--lambda-s",
        *config.lambda_s,
        "--lambda-smooth",
        config.lambda_smooth,
        "--lambda-r",
        *config.lambda_r,
        "--lambda-mean",
        config.lambda_mean,
        *chebyshev_args,
        *smooth_group_args,
        "--out-json",
        str(config.out_json),
    ]


def _load_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _detect_smd_layout(config: UniformityConfig) -> tuple[int, int]:
    previous_use_ring_powers = os.environ.get("USE_RING_POWERS_JSON")
    previous_ring_powers = os.environ.get("RING_POWERS_JSON")
    os.environ.update(config.env)
    os.environ["USE_RING_POWERS_JSON"] = "0"
    os.environ["RING_POWERS_JSON"] = "/dev/null"
    try:
        pos, _, meta = _compute_positions_from_env()
        rings = int(meta.get("rings", meta.get("ring_n", 0) + 1))
        return max(0, rings - 1), len(pos)
    except Exception:  # noqa: BLE001 - compatibility fallback matches legacy script.
        return 7, 0
    finally:
        if previous_use_ring_powers is None:
            os.environ.pop("USE_RING_POWERS_JSON", None)
        else:
            os.environ["USE_RING_POWERS_JSON"] = previous_use_ring_powers
        if previous_ring_powers is None:
            os.environ.pop("RING_POWERS_JSON", None)
        else:
            os.environ["RING_POWERS_JSON"] = previous_ring_powers


def _basis_manifest_path(config: UniformityConfig) -> Path:
    return config.basis_path.parent / "basis_manifest.json"


def _basis_fingerprint_requires_rebuild(config: UniformityConfig, manifest: dict[str, Any]) -> bool:
    if "generator_sha256" not in manifest or "emitter_env" not in manifest:
        raise ValueError("basis manifest missing emitter fingerprint (legacy basis)")
    ring_n = int(float(config.env.get("SMD_RING_N", "0") or "0"))
    module_count = int(float(config.env.get("SMD_MODULE_COUNT", "0") or "0"))
    saved = build_smd_runtime_fingerprint_from_basis_manifest(manifest, env=config.env)
    current = build_smd_runtime_fingerprint_from_env(
        layout_meta={"ring_n": ring_n, "rings": ring_n + 1},
        module_count=module_count,
        env=config.env,
    )
    reasons = compare_smd_runtime_fingerprints(saved, current)
    if reasons:
        raise ValueError("; ".join(reasons))
    return False


def _validate_basis_sampling_grid(config: UniformityConfig) -> None:
    if not config.basis_path.exists():
        raise ValueError("basis file missing")

    basis = np.load(config.basis_path) if config.basis_path.suffix == ".npy" else np.loadtxt(config.basis_path, delimiter=",")
    if basis.ndim != 2:
        raise ValueError(f"basis matrix has unexpected shape: {basis.shape}")

    manifest_path = _basis_manifest_path(config)
    if not manifest_path.exists():
        raise ValueError("basis manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    saved_grid = manifest.get("sensor_grid")
    if not isinstance(saved_grid, dict):
        raise ValueError("basis manifest missing sensor-grid fingerprint")
    current_grid = build_smd_sensor_grid_snapshot(env=config.env)
    expected_points = int(current_grid.get("n_points", 0) or 0)
    saved_points = int(saved_grid.get("n_points", 0) or 0)
    manifest_points = int(manifest.get("n_points", 0) or 0)
    basis_points = int(basis.shape[0])
    if expected_points <= 0:
        raise ValueError("current sensor-grid fingerprint is invalid")
    if basis_points != expected_points:
        raise ValueError(f"basis row count {basis_points} does not match current sensor grid {expected_points}")
    if saved_points != expected_points:
        raise ValueError(
            f"basis manifest sensor-grid points {saved_points} do not match current sensor grid {expected_points}"
        )
    if manifest_points != expected_points:
        raise ValueError(f"basis manifest n_points {manifest_points} do not match current sensor grid {expected_points}")


def _run_command(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str]) -> int:
    _print_event(ScriptEvent("info", "command.start", "running command", {"argv": list(argv), "cwd": str(cwd)}))
    try:
        result = subprocess.run(  # nosec B603
            list(argv),
            cwd=cwd,
            env=dict(env),
            check=False,
        )
    except FileNotFoundError:
        _print_event(ScriptEvent("error", "command.missing", "required command not found", {"command": argv[0]}))
        return int(RadianceScriptExit.VALIDATION)
    if result.returncode != 0:
        propagated = _propagated_return_code(result.returncode)
        _print_event(
            ScriptEvent(
                "error",
                "command.failed",
                "command failed",
                {"argv": list(argv), "exit_code": result.returncode, "propagated_exit_code": propagated},
            )
        )
        return propagated
    _print_event(ScriptEvent("info", "command.succeeded", "command succeeded", {"argv": list(argv), "exit_code": 0}))
    return int(RadianceScriptExit.OK)


def _ensure_uniformity_paths(config: UniformityConfig) -> None:
    for path in (config.root, config.basis_output_root, config.runtime_state_root, config.cache_root):
        path.mkdir(parents=True, exist_ok=True)
    for script_name in ("run_basis_extraction.sh", "run_simulation_smd.sh"):
        script_path = config.script_dir / script_name
        if not script_path.exists():
            raise FileNotFoundError(str(script_path))


def _print_uniformity_header(config: UniformityConfig) -> None:
    print()
    print("--- Uniformity pipeline ---")
    for key, value in (
        ("RUN_BASIS", config.run_basis),
        ("BASIS_PATH", str(config.basis_path)),
        ("TARGET_PPFD", config.target_ppfd),
        ("LAMBDA_S", " ".join(config.lambda_s)),
        ("LAMBDA_R", " ".join(config.lambda_r)),
        ("LAMBDA_MEAN", config.lambda_mean),
        ("LAMBDA_SMOOTH", config.lambda_smooth),
        ("USE_CHEBYSHEV", config.use_chebyshev),
        ("SOLVE_METHOD", config.solve_method),
        ("SOLVE_MODE", config.solve_mode),
        ("SMD_BASIS_BACKEND", config.smd_basis_backend),
        ("SMD_VAR_MODE", config.smd_var_mode),
        ("SMD_ALL_PER_MODULE", config.smd_all_per_module),
        ("SMD_OUTER_PER_MODULE", config.smd_outer_per_module),
        ("W_MIN", config.w_min),
        ("W_MAX", config.w_max),
        ("MEAN_TOL", config.mean_tol),
        ("SMD_AUTO_DIM", config.smd_auto_dim),
        ("OUT_JSON", str(config.out_json)),
    ):
        print(f"{key}={value}")
    print()


def _run_basis(config: UniformityConfig) -> int:
    env = dict(config.env)
    env["RUN_BASIS"] = "1"
    return _run_command([str(config.script_dir / "run_basis_extraction.sh")], cwd=config.repo_root, env=env)


def _run_smd_simulation(config: UniformityConfig) -> int:
    env = dict(config.env)
    for key in (
        "FSPM_SKIP_DURING_BASIS",
        "SMD_BASIS_MODE",
        "BASIS_MODE",
        "SMD_BASIS_RING",
        "SMD_BASIS_MODULE_IDX",
        "SMD_BASIS_OUTER_MODULE_IDX",
    ):
        env.pop(key, None)
    env.update(
        {
            "USE_RING_POWERS_JSON": "1",
            "RING_POWERS_STRICT": "1",
            "RING_POWERS_JSON": str(config.out_json),
        }
    )
    return _run_command([str(config.script_dir / "run_simulation_smd.sh")], cwd=config.repo_root, env=env)


def _run_uniform_solve(config: UniformityConfig) -> None:
    print("[2/3] Computing uniform per-module power...")
    basis_path = config.basis_path
    target_ppfd = float(config.target_ppfd)
    basis = np.load(basis_path) if basis_path.suffix == ".npy" else np.loadtxt(basis_path, delimiter=",")
    n_points, n_vars = basis.shape
    manifest = _load_manifest(basis_path.parent / "basis_manifest.json")
    transform = build_basis_solve_transform(basis_manifest=manifest, runtime_env=config.env)
    coeffs = np.ones((n_vars,), dtype=float)
    field_basis = basis @ coeffs
    mean_basis = float(field_basis.mean())
    if mean_basis <= 0:
        raise ValueError("Uniform solve failed: mean PPFD at 1 W <= 0.")

    q_uniform = target_ppfd / mean_basis
    coeffs = np.full((n_vars,), q_uniform, dtype=float)
    field = basis @ coeffs
    max_uniform_w = float(config.w_max)
    w_uniform = float(transform.weight_from_coeff(q_uniform, w_min=0.0, w_max=max_uniform_w))
    log_cap_metrics = config.env.get("LOG_CAP_METRICS", "").strip().lower() in {"1", "true", "yes", "on"}
    metrics = compute_ppfd_metrics(field, setpoint_ppfd=(target_ppfd if log_cap_metrics else None))

    var_mode = (manifest or {}).get("variables") or "rings"
    out: dict[str, Any] = {
        "target_ppfd": float(target_ppfd),
        "strategy": config.uniform_tag,
        "mean_ppfd_at_basis_unit": float(mean_basis),
        "uniform_w_per_module": float(w_uniform),
        "metrics": metrics,
        "basis_file": str(basis_path),
        "n_points": int(n_points),
        "solve_space": transform.solve_space,
        "basis_unit_w_per_module": float(transform.basis_unit_w_per_module),
        "basis_source_photon_umol_s": float(transform.basis_source_photon_umol_s),
        "basis_coefficients": [float(x) for x in coeffs],
    }
    layout_meta = {
        "layout_mode": (manifest or {}).get("emitter_env", {}).get("LAYOUT_MODE", config.env.get("LAYOUT_MODE", "square")),
        "layout_family": (manifest or {}).get("layout_family"),
        "room_L_m": (manifest or {}).get("room_L_m"),
        "room_W_m": (manifest or {}).get("room_W_m"),
        "ring_n": (int((manifest or {}).get("n_rings", 0)) - 1)
        if (manifest or {}).get("n_rings") is not None
        else None,
        "rings": (manifest or {}).get("n_rings"),
        "base_n": (manifest or {}).get("base_n"),
    }
    out["smd_solution_metadata"] = build_smd_solution_metadata(
        runtime_fingerprint=build_smd_runtime_fingerprint_from_env(
            layout_meta=layout_meta,
            module_count=(manifest or {}).get("layout_modules"),
        ),
        basis_manifest=manifest,
        basis_path=basis_path,
        n_points=n_points,
    )

    if var_mode == "ring_plus_outer_modules":
        groups = (manifest or {}).get("variable_groups") or {}
        ring_group = int(groups.get("rings", 0) or 0)
        outer_count = int(groups.get("outer_modules", 0) or 0)
        if ring_group + outer_count != n_vars:
            ring_indices = (manifest or {}).get("ring_indices") or []
            ring_group = len(ring_indices)
            outer_count = max(0, n_vars - ring_group)
        out.update(
            {
                "variables": "ring_plus_outer_modules",
                "ring_indices": [int(x) for x in ((manifest or {}).get("ring_indices") or list(range(ring_group)))],
                "ring_powers_W_per_module": [float(w_uniform)] * int(ring_group),
                "outer_ring_index": (manifest or {}).get("outer_ring_index"),
                "outer_ring_indices": [int(x) for x in ((manifest or {}).get("outer_ring_indices") or [])],
                "outer_ring_powers_W_per_module": [float(w_uniform)] * int(outer_count),
                "variable_groups": {"rings": int(ring_group), "outer_modules": int(outer_count)},
            }
        )
    elif var_mode == "per_module":
        module_indices = (manifest or {}).get("module_indices") or list(range(n_vars))
        out.update(
            {
                "variables": "per_module",
                "module_indices": [int(x) for x in module_indices],
                "module_powers_W_per_module": [float(w_uniform)] * int(n_vars),
                "variable_groups": {"modules": int(n_vars)},
            }
        )
    else:
        ring_indices = (manifest or {}).get("ring_indices") or list(range(n_vars))
        out.update(
            {
                "variables": "rings",
                "ring_indices": [int(x) for x in ring_indices],
                "ring_powers_W_per_module": [float(w_uniform)] * int(n_vars),
            }
        )

    config.out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Uniform per-module power: {w_uniform:.6f} W")
    print(f"Saved {config.out_json}")


def _print_basis_predicted_metrics(config: UniformityConfig) -> None:
    print("[2.5/3] Basis-predicted metrics...")
    if not config.basis_path.exists() or not config.out_json.exists():
        raise ValueError("Missing basis or output JSON for metrics check.")

    basis = np.load(config.basis_path) if config.basis_path.suffix == ".npy" else np.loadtxt(config.basis_path, delimiter=",")
    data = json.loads(config.out_json.read_text(encoding="utf-8"))
    manifest = load_basis_manifest(config.basis_path)
    transform = build_basis_solve_transform(basis_manifest=manifest, runtime_env=config.env)
    coeffs = solution_coefficients_from_json(data, transform=transform)

    if coeffs.size == 0:
        raise ValueError("No coefficients found in output JSON.")
    if basis.shape[1] != coeffs.size:
        raise ValueError(f"Basis columns {basis.shape[1]} != coefficient count {coeffs.size}.")

    predicted = basis @ coeffs
    log_cap_metrics = config.env.get("LOG_CAP_METRICS", "").strip().lower() in {"1", "true", "yes", "on"}
    metrics = compute_ppfd_metrics(
        predicted,
        setpoint_ppfd=(float(config.target_ppfd) if log_cap_metrics else None),
        legacy_metrics=True,
    )
    print("METRICS (basis):", format_ppfd_metrics_line(metrics))


def run_uniformity(raw_env: Mapping[str, str] | None = None) -> int:
    config = _build_uniformity_config(raw_env)
    try:
        _ensure_uniformity_paths(config)
    except FileNotFoundError as exc:
        _print_event(ScriptEvent("error", "path.missing", "required script is missing", {"path": str(exc)}))
        return int(RadianceScriptExit.VALIDATION)

    manifest_path = _basis_manifest_path(config)
    manifest = _load_manifest(manifest_path)
    if config.env.get("RUN_UNIFORMITY_DUMP_SOLVER_ARGV") == "1":
        print(json.dumps(build_uniformity_solver_argv(config, manifest), separators=(",", ":")))
        return int(RadianceScriptExit.OK)

    _print_uniformity_header(config)
    ring_n, module_count = _detect_smd_layout(config)
    config.env["SMD_RING_N"] = str(ring_n)
    config.env["SMD_MODULE_COUNT"] = str(module_count)
    print(f"Detected ring_n={ring_n}, modules={module_count}")

    run_basis = config.run_basis
    basis_var_mode = "rings"
    basis_n_vars = 0
    basis_layout_modules = 0
    basis_layout_rings = 0
    basis_backend_saved = "rtrace"
    if manifest:
        basis_var_mode = str(manifest.get("variables") or "rings")
        basis_n_vars = int(manifest.get("n_vars", manifest.get("n_rings", 0)) or 0)
        basis_layout_modules = int(manifest.get("layout_modules", 0) or 0)
        basis_layout_rings = int(manifest.get("n_rings", 0) or 0)
        basis_backend_saved = str(manifest.get("basis_backend") or "rtrace")
        try:
            _basis_fingerprint_requires_rebuild(config, manifest)
        except ValueError as exc:
            print(f"Basis manifest differs from current emitter settings; rebuilding basis. Reason: {exc}")
            run_basis = "1"

    print(
        "Variable mode: "
        f"desired={config.desired_var_mode}, basis={basis_var_mode}, basis_vars={basis_n_vars}"
    )

    if basis_var_mode != config.desired_var_mode:
        print(f"Basis variable mode ({basis_var_mode}) differs from desired ({config.desired_var_mode}); rebuilding basis.")
        run_basis = "1"
    if config.desired_var_mode == "per_module":
        basis_modules = basis_layout_modules if basis_layout_modules > 0 else basis_n_vars
        if module_count > 0 and basis_modules != module_count:
            print(f"Basis modules ({basis_modules}) differ from layout modules ({module_count}); rebuilding basis.")
            run_basis = "1"
    else:
        basis_rings = basis_layout_rings if basis_layout_rings > 0 else basis_n_vars
        if basis_rings != ring_n + 1:
            print(f"Basis rings ({basis_rings}) differ from layout rings ({ring_n + 1}); rebuilding basis.")
            run_basis = "1"
    if basis_backend_saved != config.smd_basis_backend:
        print(f"Basis backend ({basis_backend_saved}) differs from requested backend ({config.smd_basis_backend}); rebuilding basis.")
        run_basis = "1"
    if config.smd_basis_backend != "rtrace" and config.desired_var_mode != "rings":
        print(f"ERROR: basis backend {config.smd_basis_backend} only supports SMD ring basis mode.", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION)

    if run_basis == "1":
        print("[1/3] Building basis matrix A...")
        basis_exit = _run_basis(config)
        if basis_exit != 0:
            return basis_exit

    if not config.basis_path.exists():
        print(f"ERROR: basis file not found at {config.basis_path}. Set BASIS_PATH or run with RUN_BASIS=1.", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION)

    try:
        _validate_basis_sampling_grid(config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Basis sampling grid does not match the current room/sensor configuration; rebuilding basis. Reason: {exc}")
        basis_exit = _run_basis(config)
        if basis_exit != 0:
            return basis_exit
        if not config.basis_path.exists():
            print(f"ERROR: basis file not found at {config.basis_path} after rebuild.", file=sys.stderr)
            return int(RadianceScriptExit.VALIDATION)
        try:
            _validate_basis_sampling_grid(config)
        except (OSError, ValueError, json.JSONDecodeError) as rebuild_exc:
            print(
                "ERROR: basis sampling grid still does not match the current room/sensor configuration "
                f"after rebuild. Reason: {rebuild_exc}",
                file=sys.stderr,
            )
            return int(RadianceScriptExit.VALIDATION)

    if config.solve_mode == "uniform":
        try:
            _run_uniform_solve(config)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return int(RadianceScriptExit.VALIDATION)
        print("[3/3] Running Radiance simulation with uniform powers...")
        sim_exit = _run_smd_simulation(config)
        if sim_exit != 0:
            return sim_exit
        print()
        print(f"Pipeline complete. Uniform powers: {config.out_json}")
        print("PPFD map: ppfd_map.txt")
        print("To visualize: python3 -m rad_rebuild.radiance.engine.visualization.visualize_ppfd --overlay smd")
        return int(RadianceScriptExit.OK)

    print("[2/3] Solving per-variable powers...")
    solver_exit = _run_command([config.py, *build_uniformity_solver_argv(config, _load_manifest(manifest_path))], cwd=config.repo_root, env=config.env)
    if solver_exit != 0:
        return solver_exit

    try:
        _print_basis_predicted_metrics(config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION)

    print("[3/3] Running Radiance simulation with solved powers...")
    sim_exit = _run_smd_simulation(config)
    if sim_exit != 0:
        return sim_exit
    print()
    print(f"Pipeline complete. Solved powers: {config.out_json}")
    print("PPFD map: ppfd_map.txt")
    print("To visualize: python3 -m rad_rebuild.radiance.engine.visualization.visualize_ppfd --overlay smd")
    return int(RadianceScriptExit.OK)


@dataclass(frozen=True)
class RuntimeConfig:
    py: str
    repo_root: Path
    script_dir: Path
    root: Path
    data_root: Path
    curve_data_root: Path
    ies_root: Path
    runtime_state_root: Path
    cache_root: Path
    basis_output_root: Path
    env: dict[str, str]


@dataclass(frozen=True)
class FspmReceiverPlantMaterial:
    radiance_path: Path
    metadata: dict[str, Any]


FSPM_RTRACE_PROFILE_ENV = "FSPM_RTRACE_PROFILE"
FSPM_RTRACE_NPROC_ENV = "FSPM_RTRACE_NPROC"
FSPM_RTRACE_AMBIENT_MODE_ENV = "FSPM_RTRACE_AMBIENT_MODE"
FSPM_RTRACE_AMBIENT_MODE_DEFAULT = "default"
FSPM_RTRACE_AMBIENT_MODE_PER_BAND_AF = "per_band_af"
FSPM_RTRACE_AMBIENT_MODES = frozenset(
    {FSPM_RTRACE_AMBIENT_MODE_DEFAULT, FSPM_RTRACE_AMBIENT_MODE_PER_BAND_AF}
)


def _runtime_config(raw_env: Mapping[str, str] | None = None, *, smd_defaults: bool = False) -> RuntimeConfig:
    env = dict(os.environ if raw_env is None else raw_env)
    repo_root = REPO_ROOT
    root = Path(_env_text(env, "RADIANCE_OUTPUT_ROOT", str(repo_root / "outputs" / "radiance")))
    data_root = Path(_env_text(env, "RADIANCE_DATA_ROOT", str(repo_root / "data" / "radiance")))
    curve_data_root = Path(_env_text(env, "RADIANCE_CURVE_DATA_ROOT", str(data_root / "curve_data")))
    ies_root = Path(_env_text(env, "RADIANCE_IES_ROOT", str(data_root / "ies_sources")))
    runtime_state_root = Path(_env_text(env, "RADIANCE_RUNTIME_STATE_ROOT", str(root / "runtime_state")))
    cache_root = Path(_env_text(env, "RADIANCE_CACHE_ROOT", str(root / "cache")))
    basis_output_root = Path(_env_text(env, "RADIANCE_BASIS_OUTPUT_ROOT", str(root / "basis")))
    env.update(
        {
            "ROOT": str(root),
            "RADIANCE_DATA_ROOT": str(data_root),
            "RADIANCE_CURVE_DATA_ROOT": str(curve_data_root),
            "RADIANCE_IES_ROOT": str(ies_root),
            "RADIANCE_OUTPUT_ROOT": str(root),
            "RADIANCE_RUNTIME_STATE_ROOT": str(runtime_state_root),
            "RADIANCE_CACHE_ROOT": str(cache_root),
            "RADIANCE_BASIS_OUTPUT_ROOT": str(basis_output_root),
            "LANG": "C",
            "LC_ALL": "C",
            "LC_NUMERIC": "C",
        }
    )
    _prepend_pythonpath(env, repo_root)
    if smd_defaults:
        _apply_smd_defaults(env, curve_data_root)
    return RuntimeConfig(
        py=_env_text(env, "PY", sys.executable),
        repo_root=repo_root,
        script_dir=repo_root / "scripts" / "radiance",
        root=root,
        data_root=data_root,
        curve_data_root=curve_data_root,
        ies_root=ies_root,
        runtime_state_root=runtime_state_root,
        cache_root=cache_root,
        basis_output_root=basis_output_root,
        env=env,
    )


def _bool_env(env: Mapping[str, str], key: str, default: str = "0") -> bool:
    return _env_text(env, key, default).strip().lower() in {"1", "true", "yes", "on"}


def _float_env(env: Mapping[str, str], key: str, default: str = "0") -> float:
    return float(_env_text(env, key, default))


def _optional_float_env(env: Mapping[str, str], key: str) -> float | None:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return None
    return float(raw)


def _int_env(env: Mapping[str, str], key: str, default: int) -> int:
    try:
        return int(_env_text(env, key, str(default)))
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _strict_binary_env(env: Mapping[str, str], key: str, default: str = "0") -> bool:
    raw = _env_text(env, key, default).strip()
    if raw == "0":
        return False
    if raw == "1":
        return True
    raise ValueError(f"{key} must be 0 or 1")


def _optional_positive_int_env(env: Mapping[str, str], key: str) -> int | None:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _float_range_env(
    env: Mapping[str, str],
    min_key: str,
    max_key: str,
    default: tuple[float, float],
) -> tuple[float, float]:
    return (
        _float_env(env, min_key, str(default[0])),
        _float_env(env, max_key, str(default[1])),
    )



def _fspm_basis_extraction_active(env: Mapping[str, str]) -> bool:
    """Return true only for internally launched SMD basis-column passes.

    Stale SMD_BASIS_* shell variables must not disable FSPM for Conventional,
    HPS, or final solved SMD runs. The basis extraction loop sets the explicit
    internal FSPM_SKIP_DURING_BASIS flag when it launches basis-column passes.
    """

    return _bool_env(env, "FSPM_SKIP_DURING_BASIS")





PLANT_RUNTIME_ARTIFACT_NAMES = (
    "plant_geometry.json",
    "plant_geometry.rad",
    "plant_absorption_surfaces.json",
    "plant_surface_flux.json",
    "plant_spectral_absorption.json",
    "plant_spectral_response.json",
    "plant_photosynthesis_response.json",
    "plant_photoreceptor_exposure.json",
    "plant_photomorphogenesis_response.json",
)


def _clear_fspm_runtime_artifacts(runtime_state_root: Path) -> None:
    """Remove stale plant artifacts so metrics never mix modes/runs."""

    for name in PLANT_RUNTIME_ARTIFACT_NAMES:
        (runtime_state_root / name).unlink(missing_ok=True)



def _fspm_plants_enabled(env: Mapping[str, str]) -> bool:
    return _bool_env(env, "FSPM_PLANTS_ENABLED")


def _fspm_plant_config_from_env(env: Mapping[str, str]) -> PlantGeometryConfig:
    defaults = PlantGeometryConfig()
    optical_defaults = defaults.optical
    return PlantGeometryConfig(
        seed=_int_env(env, "FSPM_PLANT_SEED", defaults.seed),
        plant_grid_rows=_int_env(env, "FSPM_PLANT_ROWS", defaults.plant_grid_rows),
        plant_grid_columns=_int_env(
            env,
            "FSPM_PLANT_COLUMNS",
            defaults.plant_grid_columns,
        ),
        plant_spacing_m=_float_env(
            env,
            "FSPM_PLANT_SPACING_M",
            str(defaults.plant_spacing_m),
        ),
        plant_height_m=_float_env(
            env,
            "FSPM_PLANT_HEIGHT_M",
            str(defaults.plant_height_m),
        ),
        canopy_radius_m=_float_env(
            env,
            "FSPM_PLANT_CANOPY_RADIUS_M",
            str(defaults.canopy_radius_m),
        ),
        leaf_count_per_plant=_int_env(
            env,
            "FSPM_PLANT_LEAF_COUNT",
            defaults.leaf_count_per_plant,
        ),
        leaf_length_range_m=_float_range_env(
            env,
            "FSPM_PLANT_LEAF_LENGTH_MIN_M",
            "FSPM_PLANT_LEAF_LENGTH_MAX_M",
            defaults.leaf_length_range_m,
        ),
        leaf_width_range_m=_float_range_env(
            env,
            "FSPM_PLANT_LEAF_WIDTH_MIN_M",
            "FSPM_PLANT_LEAF_WIDTH_MAX_M",
            defaults.leaf_width_range_m,
        ),
        leaf_tilt_range_deg=_float_range_env(
            env,
            "FSPM_PLANT_LEAF_TILT_MIN_DEG",
            "FSPM_PLANT_LEAF_TILT_MAX_DEG",
            defaults.leaf_tilt_range_deg,
        ),
        leaf_curvature_m=_float_env(
            env,
            "FSPM_PLANT_CURVATURE_M",
            str(defaults.leaf_curvature_m),
        ),
        growth_stage=_float_env(
            env,
            "FSPM_PLANT_GROWTH_STAGE",
            str(defaults.growth_stage),
        ),
        optical=PlantOpticalAssumptions(
            reflectance=_float_env(
                env,
                "FSPM_PLANT_REFLECTANCE",
                str(optical_defaults.reflectance),
            ),
            transmittance=_float_env(
                env,
                "FSPM_PLANT_TRANSMITTANCE",
                str(optical_defaults.transmittance),
            ),
            absorptance=_float_env(
                env,
                "FSPM_PLANT_ABSORPTANCE",
                str(optical_defaults.absorptance),
            ),
        ),
    )


def _fspm_target_settings_from_env(env: Mapping[str, str]) -> tuple[float, float]:
    target_ppfd = resolve_fspm_target_ppfd(
        _optional_float_env(env, "FSPM_TARGET_PPFD_UMOL_M2_S"),
        fallback_target_ppfd=_optional_float_env(env, "TARGET_PPFD"),
    )
    tolerance = resolve_fspm_target_tolerance(
        _optional_float_env(env, "FSPM_TARGET_TOLERANCE_UMOL_M2_S")
    )
    return target_ppfd, tolerance


def _prepare_optional_plant_artifacts(
    config: RuntimeConfig,
) -> PlantArtifactPaths | None:
    if _fspm_basis_extraction_active(config.env):
        _clear_fspm_runtime_artifacts(config.runtime_state_root)
        print("FSPM plant artifacts skipped during SMD basis extraction.")
        return None
    if not _fspm_plants_enabled(config.env):
        _clear_fspm_runtime_artifacts(config.runtime_state_root)
        return None
    plant_config = _fspm_plant_config_from_env(config.env)
    return write_plant_artifacts(
        config.runtime_state_root,
        plant_config,
        active_simulation_integration=True,
        provenance_phase="Phase 03",
    )


def _prepare_optional_plant_artifacts_or_report(
    config: RuntimeConfig,
) -> tuple[int, PlantArtifactPaths | None]:
    try:
        return int(RadianceScriptExit.OK), _prepare_optional_plant_artifacts(config)
    except ValueError as exc:
        print(f"ERROR: invalid FSPM plant configuration: {exc}", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION), None


def _receiver_material_photon_distribution(
    config: RuntimeConfig,
    *,
    spectral_mode: str,
    profile_wavelength_nm: Sequence[int],
):
    spectral_distribution = _spectral_distribution_from_env(
        config.env,
        mode=spectral_mode,
        curve_data_root=config.curve_data_root,
    )
    try:
        return wavelength_photon_distribution_from_curve_data(
            config.curve_data_root,
            spectral_mode,
            profile_wavelength_nm,
            env=config.env,
            fallback_distribution=spectral_distribution,
        )
    except ValueError:
        return wavelength_photon_distribution_from_band_fractions(
            spectral_distribution,
            profile_wavelength_nm,
        )


def _prepare_fspm_receiver_plant_material(
    config: RuntimeConfig,
    plant_artifacts: PlantArtifactPaths,
    *,
    spectral_mode: str,
) -> FspmReceiverPlantMaterial:
    spectral_transport_mode = normalize_fspm_spectral_transport_mode(
        config.env.get(FSPM_SPECTRAL_TRANSPORT_MODE_ENV)
    )
    if spectral_transport_mode == SPECTRAL_TRANSPORT_MODE_BANDED_5:
        raise ValueError(
            f"{FSPM_SPECTRAL_TRANSPORT_MODE_ENV}=banded_5 is handled by the "
            "banded receiver execution path, not scalar receiver material preparation."
        )
    material_mode = normalize_leaf_radiance_material_mode(
        config.env.get(FSPM_LEAF_RADIANCE_MATERIAL_MODE_ENV)
    )
    if material_mode == LEAF_RADIANCE_MATERIAL_MODE_OPAQUE_OCCLUDER:
        return FspmReceiverPlantMaterial(
            radiance_path=plant_artifacts.radiance,
            metadata=opaque_leaf_material_metadata(),
        )
    if material_mode != LEAF_RADIANCE_MATERIAL_MODE_REX_SOURCE_WEIGHTED_TRANS:
        raise ValueError(f"Unsupported leaf Radiance material mode: {material_mode!r}.")

    profile = leaf_optical_profile_from_env(config.env, data_root=config.repo_root)
    if profile is None:
        raise ValueError(
            "FSPM_LEAF_OPTICAL_PROFILE_ID is required when "
            f"{FSPM_LEAF_RADIANCE_MATERIAL_MODE_ENV}={material_mode}."
        )
    if profile.profile_id != REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1:
        raise ValueError(
            f"{FSPM_LEAF_RADIANCE_MATERIAL_MODE_ENV}={material_mode} requires "
            f"{REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1!r}, got "
            f"{profile.profile_id!r}."
        )

    distribution = _receiver_material_photon_distribution(
        config,
        spectral_mode=spectral_mode,
        profile_wavelength_nm=profile.wavelength_nm,
    )
    coefficients = par_source_weighted_leaf_coefficients(profile, distribution)
    parameters = fit_diffuse_trans_material(coefficients)
    scene = generate_plant_scene(_fspm_plant_config_from_env(config.env))
    material_id = scene.plants[0].leaves[0].radiance_material_id
    material_definition = radiance_trans_material_definition(material_id, parameters)
    receiver_path = config.runtime_state_root / "plants_fspm_receiver_material.rad"
    receiver_path.write_text(
        export_scene_to_radiance(
            scene,
            leaf_material_definition=material_definition,
            optical_assumption_comment=(
                "# optical_assumptions "
                f"mode={material_mode} "
                f"weighting_basis={coefficients.weighting_basis} "
                f"reflectance={coefficients.reflectance:.6f} "
                f"transmittance={coefficients.transmittance:.6f} "
                f"absorptance={coefficients.absorptance:.6f}"
            ),
        ),
        encoding="utf-8",
    )
    return FspmReceiverPlantMaterial(
        radiance_path=receiver_path,
        metadata=rex_source_weighted_leaf_material_metadata(
            profile=profile,
            distribution=distribution,
            coefficients=coefficients,
            parameters=parameters,
        ),
    )


def _prepare_fspm_receiver_plant_material_or_report(
    config: RuntimeConfig,
    plant_artifacts: PlantArtifactPaths,
    *,
    spectral_mode: str,
) -> tuple[int, FspmReceiverPlantMaterial | None]:
    try:
        return int(RadianceScriptExit.OK), _prepare_fspm_receiver_plant_material(
            config,
            plant_artifacts,
            spectral_mode=spectral_mode,
        )
    except ValueError as exc:
        print(f"ERROR: invalid FSPM leaf Radiance material configuration: {exc}", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION), None


def _print_optional_plant_artifact_note(plant_artifacts: PlantArtifactPaths | None) -> None:
    if plant_artifacts is None:
        return
    print("FSPM plant artifacts:")
    print(f"  • {plant_artifacts.radiance}")
    print("  note: excluded from baseline PPFD octree; used by dedicated FSPM receiver transport.")


def _plant_receiver_rtrace_argv(
    *,
    rtrace_bin: str,
    octree: Path,
    options: Sequence[str],
    nthreads: int,
) -> list[str]:
    return [rtrace_bin, "-h", "-I+", "-n", str(nthreads), *options, str(octree)]


def _plant_receiver_rtrace_args_metadata(
    *,
    octree: Path,
    options: Sequence[str],
    nthreads: int,
) -> list[str]:
    return _plant_receiver_rtrace_argv(
        rtrace_bin="rtrace",
        octree=octree,
        options=options,
        nthreads=nthreads,
    )


def _fspm_rtrace_profile_enabled(env: Mapping[str, str]) -> bool:
    return _strict_binary_env(env, FSPM_RTRACE_PROFILE_ENV)


def _fspm_rtrace_nproc(env: Mapping[str, str]) -> int | None:
    return _optional_positive_int_env(env, FSPM_RTRACE_NPROC_ENV)


def _fspm_rtrace_ambient_mode(env: Mapping[str, str]) -> str:
    mode = _env_text(
        env,
        FSPM_RTRACE_AMBIENT_MODE_ENV,
        FSPM_RTRACE_AMBIENT_MODE_DEFAULT,
    ).strip()
    if mode not in FSPM_RTRACE_AMBIENT_MODES:
        allowed = ", ".join(sorted(FSPM_RTRACE_AMBIENT_MODES))
        raise ValueError(
            f"Unknown {FSPM_RTRACE_AMBIENT_MODE_ENV}: {mode!r}. "
            f"Expected one of: {allowed}."
        )
    return mode


def _radiance_option_value(options: Sequence[str], name: str) -> str | None:
    for index, token in enumerate(options):
        if token == name and index + 1 < len(options):
            return options[index + 1]
    return None


def _replace_radiance_option_value(
    options: Sequence[str],
    name: str,
    value: str,
) -> list[str]:
    adjusted = list(options)
    for index, token in enumerate(adjusted):
        if token == name and index + 1 < len(adjusted):
            adjusted[index + 1] = value
            return adjusted
    adjusted.extend([name, value])
    return adjusted


def _radiance_ambient_bounce_count(options: Sequence[str]) -> int:
    raw = _radiance_option_value(options, "-ab")
    if raw is None:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def _fspm_receiver_trace_options(
    config: RuntimeConfig,
    *,
    base_options: Sequence[str],
    band_id: str,
    configured_nproc: int | None,
    ambient_mode: str,
) -> tuple[list[str], str | None]:
    options = list(base_options)
    if (
        ambient_mode != FSPM_RTRACE_AMBIENT_MODE_PER_BAND_AF
        or configured_nproc is None
        or configured_nproc <= 1
        or _radiance_ambient_bounce_count(options) <= 0
    ):
        return options, None

    ambient_file = _fresh_ambient_cache(
        config,
        f"amb_plant_receivers_{band_id}_per_band_af",
    )
    return _replace_radiance_option_value(options, "-af", str(ambient_file)), str(
        ambient_file
    )


def _fspm_receiver_trace_metadata(
    *,
    receiver_sample_count: int,
    rtrace_args: Sequence[str],
    configured_nproc: int | None,
    ambient_mode: str,
    ambient_file: str | None = None,
    trace_stream_count: int = 1,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "receiver_sample_count": receiver_sample_count,
        "receiver_trace_stream_count": trace_stream_count,
        "receiver_rtrace_args": list(rtrace_args),
        "receiver_ambient_mode": ambient_mode,
        "receiver_subprocess_granularity": "per_active_band_not_per_sample",
    }
    if configured_nproc is not None:
        metadata["receiver_rtrace_nproc"] = configured_nproc
    if ambient_file is not None:
        metadata["receiver_ambient_file"] = ambient_file
    return metadata


def _trace_plant_surface_receivers(
    config: RuntimeConfig,
    *,
    receiver_input_path: Path,
    receiver_rgb_path: Path,
    octree: Path,
    options: Sequence[str],
    nthreads: int,
) -> int:
    if not octree.is_file():
        print(f"ERROR: plant receiver octree not found at {octree}", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION)

    rtrace_bin = shutil.which("rtrace")
    if rtrace_bin is None:
        _print_event(
            ScriptEvent(
                "error",
                "command.missing",
                "required command not found",
                {"command": "rtrace"},
            )
        )
        return int(RadianceScriptExit.VALIDATION)

    _print_event(
        ScriptEvent(
            "info",
            "radiance.plant_receivers",
            "tracing plant receiver samples",
            {"octree": str(octree), "receivers": str(receiver_input_path)},
        )
    )

    try:
        with receiver_input_path.open("r", encoding="utf-8") as stdin_handle, receiver_rgb_path.open(
            "w",
            encoding="utf-8",
        ) as stdout_handle:
            result = subprocess.run(  # nosec B603
                _plant_receiver_rtrace_argv(
                    rtrace_bin=rtrace_bin,
                    octree=octree,
                    options=options,
                    nthreads=nthreads,
                ),
                cwd=config.repo_root,
                env=dict(config.env),
                stdin=stdin_handle,
                stdout=stdout_handle,
                check=False,
            )
    except FileNotFoundError:
        _print_event(
            ScriptEvent(
                "error",
                "command.missing",
                "required command not found",
                {"command": "rtrace"},
            )
        )
        return int(RadianceScriptExit.VALIDATION)

    if result.returncode != 0:
        propagated = _propagated_return_code(result.returncode)
        _print_event(
            ScriptEvent(
                "error",
                "command.failed",
                "plant receiver rtrace failed",
                {
                    "command": "rtrace",
                    "exit_code": result.returncode,
                    "propagated_exit_code": propagated,
                },
            )
        )
        return propagated

    _print_event(
        ScriptEvent(
            "info",
            "command.succeeded",
            "plant receiver rtrace succeeded",
            {
                "command": "rtrace",
                "stdout": str(receiver_rgb_path),
                "exit_code": 0,
            },
        )
    )
    return int(RadianceScriptExit.OK)


def _fspm_receiver_scene_inputs(
    *,
    room: Path,
    emitter_file: Path,
    plant_rad: Path,
    static_room_oct: Path | None = None,
) -> list[str]:
    """Build plant-inclusive FSPM receiver scene inputs."""

    if static_room_oct and static_room_oct.is_file():
        return ["-f", "-i", str(static_room_oct), str(emitter_file), str(plant_rad)]
    return ["-f", str(room), str(emitter_file), str(plant_rad)]


def _build_fspm_receiver_octree(
    config: RuntimeConfig,
    *,
    room: Path,
    emitter_file: Path,
    plant_rad: Path | None,
    out_path: Path,
    static_room_oct: Path | None = None,
) -> int:
    if plant_rad is None:
        return int(RadianceScriptExit.OK)
    if not plant_rad.is_file():
        print(f"ERROR: FSPM plant geometry not found at {plant_rad}", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION)
    out_path.unlink(missing_ok=True)
    print("Building plant-inclusive FSPM receiver octree...")
    return _build_octree(
        config,
        _fspm_receiver_scene_inputs(
            room=room,
            emitter_file=emitter_file,
            plant_rad=plant_rad,
            static_room_oct=static_room_oct,
        ),
        out_path,
    )



def _infer_fixture_spectral_mode(
    env: Mapping[str, str],
    *,
    octree: Path,
    mode: str,
) -> str:
    explicit = (
        env.get("FSPM_SPECTRAL_MODE")
        or env.get("RADIANCE_SYSTEM_MODE")
        or env.get("SYSTEM_MODE")
        or ""
    ).strip()
    if explicit:
        return explicit

    haystack = " ".join(
        [
            mode,
            octree.name,
            str(octree.parent),
            env.get("RADIANCE_MODE", ""),
            env.get("MODE", ""),
        ]
    ).lower()
    if "hps" in haystack:
        return "hps"
    if "spydr" in haystack or "competitor" in haystack or "conventional" in haystack:
        return "conventional"
    if "smd" in haystack or "proposed" in haystack:
        return "smd"
    return mode




def _photomorphogenesis_parameters_from_env(env: Mapping[str, str]) -> PhotomorphogenesisResponseParameters:
    defaults = PhotomorphogenesisResponseParameters()
    return PhotomorphogenesisResponseParameters(
        red_far_red_shade_threshold=_float_env(
            env,
            "FSPM_PHOTOMORPH_RED_FAR_RED_SHADE_THRESHOLD",
            str(defaults.red_far_red_shade_threshold),
        ),
        red_far_red_full_sun_threshold=_float_env(
            env,
            "FSPM_PHOTOMORPH_RED_FAR_RED_FULL_SUN_THRESHOLD",
            str(defaults.red_far_red_full_sun_threshold),
        ),
        blue_fraction_compact_low=_float_env(
            env,
            "FSPM_PHOTOMORPH_BLUE_COMPACT_LOW",
            str(defaults.blue_fraction_compact_low),
        ),
        blue_fraction_compact_high=_float_env(
            env,
            "FSPM_PHOTOMORPH_BLUE_COMPACT_HIGH",
            str(defaults.blue_fraction_compact_high),
        ),
        photosynthetic_expansion_threshold=_float_env(
            env,
            "FSPM_PHOTOMORPH_PHOTOSYNTHETIC_EXPANSION_THRESHOLD",
            str(defaults.photosynthetic_expansion_threshold),
        ),
        shade_expansion_penalty=_float_env(
            env,
            "FSPM_PHOTOMORPH_SHADE_EXPANSION_PENALTY",
            str(defaults.shade_expansion_penalty),
        ),
    )



def _photosynthesis_parameters_from_env(env: Mapping[str, str]) -> PhotosynthesisResponseParameters:
    defaults = PhotosynthesisResponseParameters()
    return PhotosynthesisResponseParameters(
        initial_quantum_yield_mol_co2_per_mol_photons=_float_env(
            env,
            "FSPM_PHOTOSYNTHESIS_QUANTUM_YIELD",
            str(defaults.initial_quantum_yield_mol_co2_per_mol_photons),
        ),
        max_gross_assimilation_umol_co2_m2_s=_float_env(
            env,
            "FSPM_PHOTOSYNTHESIS_AMAX_UMOL_CO2_M2_S",
            str(defaults.max_gross_assimilation_umol_co2_m2_s),
        ),
        dark_respiration_umol_co2_m2_s=_float_env(
            env,
            "FSPM_PHOTOSYNTHESIS_DARK_RESPIRATION_UMOL_CO2_M2_S",
            str(defaults.dark_respiration_umol_co2_m2_s),
        ),
        curvature_factor=_float_env(
            env,
            "FSPM_PHOTOSYNTHESIS_CURVATURE",
            str(defaults.curvature_factor),
        ),
        photoperiod_hours=_float_env(
            env,
            "FSPM_PHOTOSYNTHESIS_PHOTOPERIOD_HOURS",
            str(defaults.photoperiod_hours),
        ),
    )



def _spectral_distribution_from_env(
    env: Mapping[str, str],
    *,
    mode: str,
    curve_data_root: Path,
):
    raw = (env.get("FSPM_SPECTRAL_PHOTON_FRACTIONS") or "").strip()
    if raw:
        return parse_spectral_photon_fraction_overrides(
            raw,
            distribution_id=f"env_override_{mode.lower().replace(' ', '_')}",
        )
    return fixture_spectral_distribution_from_curve_data(
        curve_data_root,
        mode,
        env=env,
    )


def _write_optional_spectral_absorption_artifact(
    config: RuntimeConfig,
    surface_flux_payload: Mapping[str, Any],
    spectral_distribution,
    *,
    spectral_mode: str,
) -> Path | None:
    profile = leaf_optical_profile_from_env(config.env, data_root=config.repo_root)
    path = config.runtime_state_root / PLANT_SPECTRAL_ABSORPTION_FILENAME
    if profile is None:
        path.unlink(missing_ok=True)
        return None

    try:
        photon_distribution = wavelength_photon_distribution_from_curve_data(
            config.curve_data_root,
            spectral_mode,
            profile.wavelength_nm,
            env=config.env,
            fallback_distribution=spectral_distribution,
        )
    except ValueError:
        photon_distribution = wavelength_photon_distribution_from_band_fractions(
            spectral_distribution,
            profile.wavelength_nm,
        )
    return write_plant_spectral_absorption_artifact(
        config.runtime_state_root,
        surface_flux_payload,
        profile,
        photon_distribution,
    )


def _write_band_receiver_plant_rad(
    config: RuntimeConfig,
    *,
    scene,
    band_material,
) -> Path:
    material_id = scene.plants[0].leaves[0].radiance_material_id
    material_definition = radiance_trans_material_definition(
        material_id,
        band_material.parameters,
    )
    band = band_material.band
    receiver_path = (
        config.runtime_state_root
        / f"plants_fspm_receiver_{band.band_id}.rad"
    )
    receiver_path.write_text(
        export_scene_to_radiance(
            scene,
            leaf_material_definition=material_definition,
            optical_assumption_comment=(
                "# optical_assumptions "
                "mode=banded_5 "
                f"band={band.band_id} "
                f"wavelength_min_nm={band.wavelength_min_nm} "
                f"wavelength_max_nm={band.wavelength_max_nm} "
                f"source_fraction_relative_to_par="
                f"{band_material.source_photon_fraction_relative_to_par:.6f} "
                f"reflectance={band_material.coefficients.reflectance:.6f} "
                f"transmittance={band_material.coefficients.transmittance:.6f} "
                f"absorptance={band_material.coefficients.absorptance:.6f}"
            ),
        ),
        encoding="utf-8",
    )
    return receiver_path


def _banded_octree_label(mode: str, band_id: str) -> str:
    mode_label = "".join(
        character.lower() if character.isalnum() else "_"
        for character in str(mode)
    ).strip("_")
    return f"{mode_label or 'fixture'}_fspm_receiver_{band_id}.oct"


def _prepare_banded_transport_plan(
    config: RuntimeConfig,
    *,
    spectral_mode: str,
):
    material_mode = normalize_leaf_radiance_material_mode(
        config.env.get(FSPM_LEAF_RADIANCE_MATERIAL_MODE_ENV)
    )
    if material_mode != LEAF_RADIANCE_MATERIAL_MODE_REX_SOURCE_WEIGHTED_TRANS:
        raise ValueError(
            f"{FSPM_SPECTRAL_TRANSPORT_MODE_ENV}=banded_5 requires "
            f"{FSPM_LEAF_RADIANCE_MATERIAL_MODE_ENV}="
            f"{LEAF_RADIANCE_MATERIAL_MODE_REX_SOURCE_WEIGHTED_TRANS}."
        )
    profile = leaf_optical_profile_from_env(config.env, data_root=config.repo_root)
    if profile is None:
        raise ValueError(
            "FSPM_LEAF_OPTICAL_PROFILE_ID is required when "
            f"{FSPM_SPECTRAL_TRANSPORT_MODE_ENV}=banded_5."
        )
    if profile.profile_id != REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1:
        raise ValueError(
            f"{FSPM_SPECTRAL_TRANSPORT_MODE_ENV}=banded_5 requires "
            f"{REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1!r}, got "
            f"{profile.profile_id!r}."
        )
    distribution = _receiver_material_photon_distribution(
        config,
        spectral_mode=spectral_mode,
        profile_wavelength_nm=profile.wavelength_nm,
    )
    return build_banded_5_transport_material_plan(profile, distribution)


def _write_banded_plant_surface_flux_artifact(
    config: RuntimeConfig,
    plant_artifacts: PlantArtifactPaths,
    ppfd_map: Path,
    *,
    room: Path,
    emitter_file: Path,
    static_room_oct: Path | None,
    mode: str,
    nthreads: int,
    receiver_scale_multiplier: float,
) -> int:
    plant_config = _fspm_plant_config_from_env(config.env)
    target_ppfd, target_tolerance = _fspm_target_settings_from_env(config.env)
    scene = generate_plant_scene(plant_config)
    spectral_mode = _infer_fixture_spectral_mode(config.env, octree=emitter_file, mode=mode)
    try:
        receiver_granularity = normalize_receiver_granularity(
            config.env.get(FSPM_RECEIVER_GRANULARITY_ENV)
        )
        samples = build_radiance_receiver_samples(
            scene,
            receiver_granularity=receiver_granularity,
        )
        plan = _prepare_banded_transport_plan(config, spectral_mode=spectral_mode)
        profile_rtrace = _fspm_rtrace_profile_enabled(config.env)
        configured_nproc = _fspm_rtrace_nproc(config.env)
        ambient_mode = _fspm_rtrace_ambient_mode(config.env)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION)

    receiver_input = (
        config.cache_root
        / f"plant_surface_receivers_{os.getpid()}_{secrets.token_hex(6)}.pts"
    )
    receiver_input.write_text(receiver_sample_input_text(samples), encoding="utf-8")
    aggregate_par_densities = [0.0 for _sample in samples]
    band_surface_rows: dict[str, list[Mapping[str, Any]]] = {}
    band_payloads: list[dict[str, Any]] = []
    active_trace_count = 0

    try:
        trace_nthreads = configured_nproc or nthreads
        for band_material in plan.bands:
            band = band_material.band
            band_payload = band_material.to_payload()
            if not band_material.receiver_trace_required:
                zero_densities = [0.0 for _sample in samples]
                band_surface_rows[band.band_id] = build_radiance_receiver_surface_flux_rows(
                    scene,
                    samples,
                    zero_densities,
                )
                band_payload.update(
                    {
                        "receiver_trace_executed": False,
                        "source_scale_applied_once": True,
                        "receiver_octree": None,
                        "receiver_plant_rad": None,
                    }
                )
                band_payload.update(
                    {
                        "receiver_sample_count": len(samples),
                        "receiver_trace_stream_count": 0,
                        "receiver_subprocess_granularity": (
                            "per_active_band_not_per_sample"
                        ),
                    }
                )
                if profile_rtrace:
                    band_payload.update(
                        {
                            "receiver_octree_build_wall_time_s": 0.0,
                            "receiver_rtrace_wall_time_s": 0.0,
                        }
                    )
                band_payloads.append(band_payload)
                continue

            receiver_rad = _write_band_receiver_plant_rad(
                config,
                scene=scene,
                band_material=band_material,
            )
            band_octree = config.cache_root / _banded_octree_label(mode, band.band_id)
            octree_start = time.perf_counter()
            receiver_oct_exit = _build_fspm_receiver_octree(
                config,
                room=room,
                emitter_file=emitter_file,
                plant_rad=receiver_rad,
                out_path=band_octree,
                static_room_oct=static_room_oct,
            )
            octree_wall_time_s = time.perf_counter() - octree_start
            if receiver_oct_exit != 0:
                return receiver_oct_exit

            receiver_rgb = (
                config.cache_root
                / f"plant_surface_receivers_{band.band_id}_{os.getpid()}_"
                f"{secrets.token_hex(6)}.rgb"
            )
            base_options = _radiance_options(
                mode,
                _fresh_ambient_cache(config, f"amb_plant_receivers_{band.band_id}"),
            )
            trace_options, ambient_file = _fspm_receiver_trace_options(
                config,
                base_options=base_options,
                band_id=band.band_id,
                configured_nproc=configured_nproc,
                ambient_mode=ambient_mode,
            )
            rtrace_args = _plant_receiver_rtrace_args_metadata(
                octree=band_octree,
                options=trace_options,
                nthreads=trace_nthreads,
            )
            trace_start = time.perf_counter()
            trace_exit = _trace_plant_surface_receivers(
                config,
                receiver_input_path=receiver_input,
                receiver_rgb_path=receiver_rgb,
                octree=band_octree,
                options=trace_options,
                nthreads=trace_nthreads,
            )
            trace_wall_time_s = time.perf_counter() - trace_start
            if trace_exit != 0:
                return trace_exit

            raw_densities = parse_rtrace_receiver_output(
                receiver_rgb.read_text(encoding="utf-8")
            )
            receiver_rgb.unlink(missing_ok=True)
            source_scale = (
                band_material.source_photon_fraction_relative_to_par
                * receiver_scale_multiplier
            )
            scaled_densities = [density * source_scale for density in raw_densities]
            if band.band_id in PAR_BAND_IDS:
                aggregate_par_densities = [
                    total + density
                    for total, density in zip(
                        aggregate_par_densities,
                        scaled_densities,
                        strict=True,
                    )
                ]
            band_surface_rows[band.band_id] = build_radiance_receiver_surface_flux_rows(
                scene,
                samples,
                scaled_densities,
            )
            active_trace_count += 1
            band_payload.update(
                {
                    "receiver_trace_executed": True,
                    "source_scale_applied_once": True,
                    "receiver_octree": str(band_octree),
                    "receiver_plant_rad": str(receiver_rad),
                }
            )
            band_payload.update(
                _fspm_receiver_trace_metadata(
                    receiver_sample_count=len(samples),
                    rtrace_args=rtrace_args,
                    configured_nproc=configured_nproc,
                    ambient_mode=ambient_mode,
                    ambient_file=ambient_file,
                )
            )
            if profile_rtrace:
                band_payload.update(
                    {
                        "receiver_octree_build_wall_time_s": octree_wall_time_s,
                        "receiver_rtrace_wall_time_s": trace_wall_time_s,
                    }
                )
            band_payloads.append(band_payload)

        banded_metadata = {
            **plan.to_payload(),
            "leaf_radiance_material_mode": (
                LEAF_RADIANCE_MATERIAL_MODE_REX_SOURCE_WEIGHTED_TRANS
            ),
            "banded_transport_active_trace_count": active_trace_count,
            "banded_transport_bands": band_payloads,
            "fspm_rtrace_profile_enabled": profile_rtrace,
            "fspm_rtrace_ambient_mode": ambient_mode,
            "receiver_trace_process_policy": "one_rtrace_stream_per_active_band",
            "receiver_trace_streams_per_active_band": 1,
            "receiver_subprocess_granularity": "per_active_band_not_per_sample",
        }
        if configured_nproc is not None:
            banded_metadata["fspm_rtrace_nproc"] = configured_nproc
        path = write_radiance_receiver_plant_surface_flux_artifact(
            config.runtime_state_root,
            scene,
            samples,
            aggregate_par_densities,
            receiver_scale_multiplier=1.0,
            source_octree="banded_5",
            receiver_granularity=receiver_granularity,
            receiver_trace_count=active_trace_count,
            target_ppfd_umol_m2_s=target_ppfd,
            target_tolerance_umol_m2_s=target_tolerance,
            target_classification_ppfd_map_path=ppfd_map,
            leaf_material_metadata=banded_metadata,
        )
        surface_flux_payload = json.loads(path.read_text(encoding="utf-8"))
        spectral_absorption_path = write_banded_plant_spectral_absorption_artifact(
            config.runtime_state_root,
            surface_flux_payload,
            band_surface_rows,
            banded_metadata,
        )
        spectral_distribution = _spectral_distribution_from_env(
            config.env,
            mode=spectral_mode,
            curve_data_root=config.curve_data_root,
        )
        spectral_path = write_plant_spectral_response_artifact(
            config.runtime_state_root,
            surface_flux_payload,
            default_leafy_green_spectral_bands(),
            spectral_distribution,
        )
        spectral_payload = json.loads(spectral_path.read_text(encoding="utf-8"))
        photosynthesis_path = write_plant_photosynthesis_response_artifact(
            config.runtime_state_root,
            spectral_payload,
            _photosynthesis_parameters_from_env(config.env),
        )
        photoreceptor_path = write_plant_photoreceptor_exposure_artifact(
            config.runtime_state_root,
            spectral_payload,
        )
        photomorphogenesis_path = write_plant_photomorphogenesis_response_artifact(
            config.runtime_state_root,
            spectral_payload,
            json.loads(photosynthesis_path.read_text(encoding="utf-8")),
            _photomorphogenesis_parameters_from_env(config.env),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: failed to write banded FSPM plant receiver artifacts: {exc}", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION)
    finally:
        receiver_input.unlink(missing_ok=True)

    print("FSPM plant surface-flux artifact:")
    print(f"  • {path}")
    print("  method: radiance_leaf_surface_receiver_sampling_v1")
    print(f"  receiver_granularity: {receiver_granularity}")
    print(f"  receiver_sample_count: {len(samples)}")
    print(f"  spectral_transport_mode: {SPECTRAL_TRANSPORT_MODE_BANDED_5}")
    print(f"  active_band_receiver_traces: {active_trace_count}")
    print("  note: banded receiver sampling does not alter heatmap uniformity.")
    print("FSPM plant spectral-absorption artifact:")
    print(f"  • {spectral_absorption_path}")
    print("  method: banded_5_radiance_leaf_receiver_transport_v1")
    print("FSPM plant spectral-response artifact:")
    print(f"  • {spectral_path}")
    print("  method: surface_flux_band_weighted_leaf_absorptance_v1")
    print(f"  distribution: {spectral_distribution.distribution_id}")
    print(f"  source: {spectral_distribution.source}")
    print("FSPM plant photosynthesis-response artifact:")
    print(f"  • {photosynthesis_path}")
    print("FSPM plant photoreceptor-exposure artifact:")
    print(f"  • {photoreceptor_path}")
    print("FSPM plant photomorphogenic-response artifact:")
    print(f"  • {photomorphogenesis_path}")
    return int(RadianceScriptExit.OK)



def _write_optional_plant_surface_flux_artifact(
    config: RuntimeConfig,
    plant_artifacts: PlantArtifactPaths | None,
    ppfd_map: Path,
    *,
    octree: Path,
    mode: str,
    nthreads: int,
    receiver_scale_multiplier: float = 1.0,
    leaf_material_metadata: Mapping[str, Any] | None = None,
    room: Path | None = None,
    emitter_file: Path | None = None,
    static_room_oct: Path | None = None,
) -> int:
    if _fspm_basis_extraction_active(config.env):
        _clear_fspm_runtime_artifacts(config.runtime_state_root)
        print("FSPM plant receiver analysis skipped during SMD basis extraction.")
        return int(RadianceScriptExit.OK)
    if plant_artifacts is None:
        _clear_fspm_runtime_artifacts(config.runtime_state_root)
        return int(RadianceScriptExit.OK)
    spectral_transport_mode = normalize_fspm_spectral_transport_mode(
        config.env.get(FSPM_SPECTRAL_TRANSPORT_MODE_ENV)
    )
    if spectral_transport_mode == SPECTRAL_TRANSPORT_MODE_BANDED_5:
        if room is None or emitter_file is None:
            print(
                "ERROR: banded FSPM receiver transport requires room and emitter scene inputs.",
                file=sys.stderr,
            )
            return int(RadianceScriptExit.VALIDATION)
        return _write_banded_plant_surface_flux_artifact(
            config,
            plant_artifacts,
            ppfd_map,
            room=room,
            emitter_file=emitter_file,
            static_room_oct=static_room_oct,
            mode=mode,
            nthreads=nthreads,
            receiver_scale_multiplier=receiver_scale_multiplier,
        )

    plant_config = _fspm_plant_config_from_env(config.env)
    target_ppfd, target_tolerance = _fspm_target_settings_from_env(config.env)
    scene = generate_plant_scene(plant_config)
    try:
        receiver_granularity = normalize_receiver_granularity(
            config.env.get(FSPM_RECEIVER_GRANULARITY_ENV)
        )
        samples = build_radiance_receiver_samples(
            scene,
            receiver_granularity=receiver_granularity,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION)

    receiver_input = config.cache_root / f"plant_surface_receivers_{os.getpid()}_{secrets.token_hex(6)}.pts"
    receiver_rgb = config.cache_root / f"plant_surface_receivers_{os.getpid()}_{secrets.token_hex(6)}.rgb"
    receiver_input.write_text(receiver_sample_input_text(samples), encoding="utf-8")

    trace_exit = _trace_plant_surface_receivers(
        config,
        receiver_input_path=receiver_input,
        receiver_rgb_path=receiver_rgb,
        octree=octree,
        options=_radiance_options(
            mode,
            _fresh_ambient_cache(config, "amb_plant_receivers"),
        ),
        nthreads=nthreads,
    )
    if trace_exit != 0:
        return trace_exit

    try:
        receiver_densities = parse_rtrace_receiver_output(receiver_rgb.read_text(encoding="utf-8"))
        path = write_radiance_receiver_plant_surface_flux_artifact(
            config.runtime_state_root,
            scene,
            samples,
            receiver_densities,
            receiver_scale_multiplier=receiver_scale_multiplier,
            source_octree=str(octree),
            receiver_granularity=receiver_granularity,
            target_ppfd_umol_m2_s=target_ppfd,
            target_tolerance_umol_m2_s=target_tolerance,
            target_classification_ppfd_map_path=ppfd_map,
            leaf_material_metadata=leaf_material_metadata,
        )
        surface_flux_payload = json.loads(path.read_text(encoding="utf-8"))
        spectral_mode = _infer_fixture_spectral_mode(config.env, octree=octree, mode=mode)
        spectral_distribution = _spectral_distribution_from_env(
            config.env,
            mode=spectral_mode,
            curve_data_root=config.curve_data_root,
        )
        spectral_absorption_path = _write_optional_spectral_absorption_artifact(
            config,
            surface_flux_payload,
            spectral_distribution,
            spectral_mode=spectral_mode,
        )
        spectral_path = write_plant_spectral_response_artifact(
            config.runtime_state_root,
            surface_flux_payload,
            default_leafy_green_spectral_bands(),
            spectral_distribution,
        )
        spectral_payload = json.loads(spectral_path.read_text(encoding="utf-8"))
        photosynthesis_path = write_plant_photosynthesis_response_artifact(
            config.runtime_state_root,
            spectral_payload,
            _photosynthesis_parameters_from_env(config.env),
        )
        photoreceptor_path = write_plant_photoreceptor_exposure_artifact(
            config.runtime_state_root,
            spectral_payload,
        )
        photomorphogenesis_path = write_plant_photomorphogenesis_response_artifact(
            config.runtime_state_root,
            spectral_payload,
            json.loads(photosynthesis_path.read_text(encoding="utf-8")),
            _photomorphogenesis_parameters_from_env(config.env),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: failed to write FSPM plant receiver surface flux: {exc}", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION)
    finally:
        receiver_input.unlink(missing_ok=True)
        receiver_rgb.unlink(missing_ok=True)

    print("FSPM plant surface-flux artifact:")
    print(f"  • {path}")
    print(f"  method: {RADIANCE_RECEIVER_METHOD}")
    print(f"  receiver_granularity: {receiver_granularity}")
    print(f"  receiver_sample_count: {len(samples)}")
    print("  note: Radiance receiver sampling does not alter heatmap uniformity.")
    if spectral_absorption_path is not None:
        print("FSPM plant spectral-absorption artifact:")
        print(f"  • {spectral_absorption_path}")
        print("  method: wavelength_binned_leaf_optical_profile_absorption_v1")
        print("  note: wavelength-binned absorption uses the explicitly selected leaf optical profile.")
    print("FSPM plant spectral-response artifact:")
    print(f"  • {spectral_path}")
    print("  method: surface_flux_band_weighted_leaf_absorptance_v1")
    print(f"  distribution: {spectral_distribution.distribution_id}")
    print(f"  source: {spectral_distribution.source}")
    print("  note: band-level absorption uses explicit spectral photon fractions and leaf optics assumptions.")
    print("FSPM plant photosynthesis-response artifact:")
    print(f"  • {photosynthesis_path}")
    print("  method: absorbed_par_non_rectangular_hyperbola_v2")
    print("  note: photosynthetic response potential is based on absorbed PAR and does not predict crop output.")
    print("FSPM plant photoreceptor-exposure artifact:")
    print(f"  • {photoreceptor_path}")
    print("  method: spectral_band_exposure_inputs_v1")
    print("  note: reports spectral exposure inputs only.")
    print("FSPM plant photomorphogenic-response artifact:")
    print(f"  • {photomorphogenesis_path}")
    print("  method: spectral_ratio_morphology_response_v1")
    print("  note: photomorphogenic response potential is based on spectral ratios and does not predict crop output.")
    return int(RadianceScriptExit.OK)


def _octree_scene_inputs(
    *,
    room: Path,
    emitter_file: Path,
    plant_rad: Path | None = None,
    static_room_oct: Path | None = None,
) -> list[str]:
    """Build baseline PPFD scene inputs.

    Plant geometry is intentionally excluded from this octree. FSPM plant
    artifacts are a separate viewer/analysis layer; they must not shadow or
    otherwise alter the baseline fixture uniformity field.
    """

    _ = plant_rad
    if static_room_oct and static_room_oct.is_file():
        return ["-f", "-i", str(static_room_oct), str(emitter_file)]
    return ["-f", str(room), str(emitter_file)]


def _clamp_0_1(value: float) -> float:
    if value != value:
        return 1.0
    return min(1.0, max(0.0, value))


def _cpu_count() -> int:
    return max(1, os.cpu_count() or 4)


def _ensure_dirs(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def _run_capture_to_file(argv: Sequence[str], out_path: Path, *, cwd: Path, env: Mapping[str, str]) -> int:
    _print_event(ScriptEvent("info", "command.start", "running command", {"argv": list(argv), "stdout": str(out_path)}))
    try:
        with out_path.open("w", encoding="utf-8") as out_handle:
            result = subprocess.run(list(argv), cwd=cwd, env=dict(env), stdout=out_handle, check=False)  # nosec B603
    except FileNotFoundError:
        _print_event(ScriptEvent("error", "command.missing", "required command not found", {"command": argv[0]}))
        return int(RadianceScriptExit.VALIDATION)
    if result.returncode != 0:
        propagated = _propagated_return_code(result.returncode)
        _print_event(
            ScriptEvent(
                "error",
                "command.failed",
                "command failed",
                {"argv": list(argv), "exit_code": result.returncode, "propagated_exit_code": propagated},
            )
        )
        return propagated
    _print_event(
        ScriptEvent(
            "info",
            "command.succeeded",
            "command succeeded",
            {"argv": list(argv), "stdout": str(out_path), "exit_code": 0},
        )
    )
    return int(RadianceScriptExit.OK)


def _command_stdout(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str]) -> str:
    result = subprocess.run(  # nosec B603
        list(argv),
        cwd=cwd,
        env=dict(env),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout.strip() or f"{argv[0]} failed")
    return result.stdout


def _build_raypath(config: RuntimeConfig) -> str:
    parts = [".", str(config.root), str(config.runtime_state_root), str(config.ies_root)]
    existing = config.env.get("RAYPATH")
    if existing:
        parts.append(existing)
    rtrace = shutil.which("rtrace")
    if rtrace:
        candidate = (Path(rtrace).resolve().parent / ".." / "lib").resolve()
        if (candidate / "rayinit.cal").is_file():
            parts.append(str(candidate))
    for candidate_text in (
        "/home/ladybugbot/lib",
        "/usr/local/lib/ray",
        "/usr/share/radiance/cal",
        "/usr/share/radiance",
        "/usr/lib/radiance",
        "/opt/radiance/lib",
    ):
        candidate = Path(candidate_text)
        if (candidate / "rayinit.cal").is_file():
            parts.append(str(candidate))
    return ":".join(parts)


def _radiance_options(mode: str, ambcache: Path | None = None) -> list[str]:
    presets = {
        "direct": ["-ab", "0", "-aa", "0", "-u+", "-dc", "1.0", "-dj", "0.60", "-ds", "0.08", "-dt", "0", "-dr", "0", "-lr", "0", "-lw", "1e-5"],
        "standard": ["-ab", "3", "-ad", "512", "-as", "128", "-aa", "0.22", "-ar", "48", "-dj", "0.35", "-ds", "0.40", "-dt", "0.08", "-dc", "0.50", "-dr", "1", "-lr", "6", "-lw", "2e-4"],
        "instant": ["-ab", "3", "-ad", "512", "-as", "128", "-aa", "0.22", "-ar", "48", "-dj", "0.35", "-ds", "0.40", "-dt", "0.08", "-dc", "0.50", "-dr", "1", "-lr", "6", "-lw", "2e-4"],
        "fast": ["-ab", "3", "-ad", "512", "-as", "128", "-aa", "0.22", "-ar", "48", "-dj", "0.35", "-ds", "0.40", "-dt", "0.08", "-dc", "0.50", "-dr", "1", "-lr", "6", "-lw", "2e-4"],
        "quality": ["-ab", "5", "-ad", "2048", "-as", "512", "-aa", "0.12", "-ar", "96", "-dj", "0.65", "-ds", "0.20", "-dt", "0.03", "-dc", "0.85", "-dr", "3", "-lr", "12", "-lw", "5e-5"],
        "rigorous": ["-ab", "6", "-ad", "4096", "-as", "1024", "-aa", "0.08", "-ar", "128", "-dj", "0.70", "-ds", "0.15", "-dt", "0.02", "-dc", "0.90", "-dr", "4", "-lr", "16", "-lw", "2e-5"],
    }
    options = list(presets.get(mode, presets["standard"]))
    if ambcache is not None:
        options.extend(["-af", str(ambcache)])
    return options


def _fresh_ambient_cache(config: RuntimeConfig, prefix: str) -> Path:
    config.cache_root.mkdir(parents=True, exist_ok=True)
    for old in config.cache_root.glob(f"{prefix}*"):
        if old.is_dir() and not old.is_symlink():
            shutil.rmtree(old, ignore_errors=True)
        else:
            old.unlink(missing_ok=True)
    return config.cache_root / f"{prefix}_{os.getpid()}_{secrets.token_hex(6)}"


def _read_points(path: Path) -> list[tuple[float, float, float]]:
    points: list[tuple[float, float, float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 3:
            points.append((float(parts[0]), float(parts[1]), float(parts[2])))
    return points


def _write_snake_and_dirs(sensor_path: Path, snake_path: Path, snake_os_path: Path, dirs_path: Path, oversample: int) -> None:
    rows: dict[float, list[tuple[float, float, float]]] = {}
    tolerance = 1e-6
    for x, y, z in _read_points(sensor_path):
        key = round(y / tolerance) * tolerance
        rows.setdefault(key, []).append((x, y, z))
    snake: list[tuple[float, float, float]] = []
    for row_index, key in enumerate(sorted(rows)):
        row = sorted(rows[key], key=lambda point: point[0])
        snake.extend(row if row_index % 2 == 0 else list(reversed(row)))
    snake_path.write_text("".join(f"{x:.6f} {y:.6f} {z:.6f}\n" for x, y, z in snake), encoding="utf-8")
    with snake_os_path.open("w", encoding="utf-8") as snake_os, dirs_path.open("w", encoding="utf-8") as dirs:
        for x, y, z in snake:
            for _ in range(oversample):
                snake_os.write(f"{x:.6f} {y:.6f} {z:.6f}\n")
                dirs.write(f"{x:.6f} {y:.6f} {z:.6f} 0 0 1\n")


BASELINE_PPFD_TRANSPORT_BASIS = "canopy_plane_scalar_par_ppfd"
BASELINE_PPFD_RGB_DECODE_METHOD = "grey_channel_average_after_equality_assertion"
BASELINE_SOURCE_CHANNEL_POLICY = "r_equals_g_equals_b_scalar_par_ppfd_carrier"
PPFD_CONVERSION_BASIS = "radiance_rgb_values_are_scalar_par_ppfd_no_179_luminous_conversion"
BASELINE_PPFD_PHOTOPIC_LUMINANCE_WEIGHTING_AVOIDED = True
BASELINE_PPFD_RGB_EQUALITY_ABS_TOL = 1e-6
BASELINE_PPFD_RGB_EQUALITY_REL_TOL = 1e-6


def _grey_channel_ppfd_from_rgb(
    red: float,
    green: float,
    blue: float,
    *,
    row_number: int,
) -> float:
    if not (
        math.isclose(
            red,
            green,
            rel_tol=BASELINE_PPFD_RGB_EQUALITY_REL_TOL,
            abs_tol=BASELINE_PPFD_RGB_EQUALITY_ABS_TOL,
        )
        and math.isclose(
            red,
            blue,
            rel_tol=BASELINE_PPFD_RGB_EQUALITY_REL_TOL,
            abs_tol=BASELINE_PPFD_RGB_EQUALITY_ABS_TOL,
        )
    ):
        raise ValueError(
            "Baseline PPFD rtrace output must use grey scalar channels "
            f"(R=G=B). Row {row_number} had R={red:.12g}, "
            f"G={green:.12g}, B={blue:.12g}."
        )
    return (red + green + blue) / 3.0


def _aggregate_rgb_to_ppfd(snake_os_path: Path, rgb_path: Path, out_map: Path, oversample: int) -> None:
    snake = _read_points(snake_os_path)
    rgb_values: list[float] = []
    for row_number, line in enumerate(rgb_path.read_text(encoding="utf-8").splitlines(), start=1):
        parts = line.split()
        if len(parts) >= 3:
            red = float(parts[-3])
            green = float(parts[-2])
            blue = float(parts[-1])
            rgb_values.append(
                _grey_channel_ppfd_from_rgb(
                    red,
                    green,
                    blue,
                    row_number=row_number,
                )
            )
    sums: dict[int, float] = {}
    counts: dict[int, int] = {}
    coords: dict[int, tuple[float, float, float]] = {}
    for index, (coord, ppfd) in enumerate(zip(snake, rgb_values, strict=False)):
        bucket = index // oversample
        coords[bucket] = coord
        sums[bucket] = sums.get(bucket, 0.0) + ppfd
        counts[bucket] = counts.get(bucket, 0) + 1
    with out_map.open("w", encoding="utf-8") as handle:
        for index in sorted(sums):
            x, y, z = coords[index]
            handle.write(f"{x:.6f} {y:.6f} {z:.6f} {sums[index] / counts[index]:.6f}\n")


def _load_ppfd(path: Path) -> np.ndarray:
    values: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.replace(",", " ").split()
        if len(parts) >= 4:
            values.append(float(parts[-1]))
    if not values:
        raise ValueError(f"No PPFD values found in {path}")
    return np.asarray(values, dtype=float)


def _ppfd_mean(path: Path) -> float:
    return float(np.mean(_load_ppfd(path)))


def _ppfd_max(path: Path) -> float:
    return float(np.max(_load_ppfd(path)))


def _scale_ppfd_map(in_map: Path, out_map: Path, multiplier: float) -> None:
    with in_map.open("r", encoding="utf-8") as source, out_map.open("w", encoding="utf-8") as target:
        for line in source:
            parts = line.split()
            if len(parts) >= 4:
                target.write(f"{float(parts[0]):.6f} {float(parts[1]):.6f} {float(parts[2]):.6f} {float(parts[3]) * multiplier:.6f}\n")
            elif parts:
                target.write(f"{float(parts[0]) * multiplier:.6f}\n")


def _canopy_area(env: Mapping[str, str]) -> float | None:
    raw = env.get("CANOPY_AREA_M2", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            return None
    try:
        length_ft = float(env.get("LENGTH_FT", "").strip() or "0")
        width_ft = float(env.get("WIDTH_FT", "").strip() or "0")
    except ValueError:
        return None
    if env.get("ALIGN_LONG_AXIS_X", "").strip() == "1" and width_ft > length_ft:
        length_ft, width_ft = width_ft, length_ft
    if length_ft > 0 and width_ft > 0:
        return length_ft * width_ft * 0.09290304
    return None


def _print_cap_metrics(config: RuntimeConfig, *, cap: str, watts: float | None, emitted_ppf: float | None) -> None:
    ppfd_path = config.root / "ppfd_map.txt"
    data = _load_ppfd(ppfd_path)
    cap_value = float(cap) if cap else None
    metrics = compute_ppfd_metrics(
        data,
        setpoint_ppfd=cap_value,
        canopy_area_m2=_canopy_area(config.env),
        total_input_watts=watts if watts and watts > 0 else None,
        emitted_ppf_umol_s=emitted_ppf if emitted_ppf and emitted_ppf > 0 else None,
        legacy_metrics=True,
    )
    print("METRICS:", format_ppfd_metrics_line(metrics))


def _run_python_module(config: RuntimeConfig, module: str, args: Sequence[str] = (), extra_env: Mapping[str, str] | None = None) -> int:
    env = dict(config.env)
    if extra_env:
        env.update(extra_env)
    return _run_command([config.py, "-m", module, *args], cwd=config.repo_root, env=env)


def _run_python_module_to_file(config: RuntimeConfig, module: str, out_path: Path, args: Sequence[str] = (), extra_env: Mapping[str, str] | None = None) -> int:
    env = dict(config.env)
    if extra_env:
        env.update(extra_env)
    return _run_capture_to_file([config.py, "-m", module, *args], out_path, cwd=config.repo_root, env=env)


def _build_octree(config: RuntimeConfig, argv: Sequence[str], out_path: Path) -> int:
    return _run_capture_to_file(["oconv", *argv], out_path, cwd=config.repo_root, env=config.env)


def _trace_ppfd(
    config: RuntimeConfig,
    *,
    octree: Path,
    dirs: Path,
    snake_os: Path,
    out_map: Path,
    oversample: int,
    nthreads: int,
    options: Sequence[str],
    tag: str,
) -> int:
    config.cache_root.mkdir(parents=True, exist_ok=True)
    rgb_path = config.cache_root / f".rgb_tmp_{tag}_{os.getpid()}_{secrets.token_hex(6)}.txt"
    _print_event(ScriptEvent("info", "radiance.trace", "tracing irradiance", {"octree": str(octree), "dirs": str(dirs)}))
    rtrace_bin = shutil.which("rtrace")
    if rtrace_bin is None:
        _print_event(ScriptEvent("error", "command.missing", "required command not found", {"command": "rtrace"}))
        return int(RadianceScriptExit.VALIDATION)
    try:
        with dirs.open("r", encoding="utf-8") as stdin_handle, rgb_path.open("w", encoding="utf-8") as stdout_handle:
            result = subprocess.run(  # nosec B603
                [rtrace_bin, "-h", "-I+", "-n", str(nthreads), *options, str(octree)],
                cwd=config.repo_root,
                env=dict(config.env),
                stdin=stdin_handle,
                stdout=stdout_handle,
                check=False,
            )
    except FileNotFoundError:
        _print_event(ScriptEvent("error", "command.missing", "required command not found", {"command": "rtrace"}))
        return int(RadianceScriptExit.VALIDATION)
    if result.returncode != 0:
        propagated = _propagated_return_code(result.returncode)
        _print_event(
            ScriptEvent(
                "error",
                "command.failed",
                "command failed",
                {"command": "rtrace", "exit_code": result.returncode, "propagated_exit_code": propagated},
            )
        )
        return propagated
    try:
        _aggregate_rgb_to_ppfd(snake_os, rgb_path, out_map, oversample)
    except ValueError as exc:
        rgb_path.unlink(missing_ok=True)
        _print_event(
            ScriptEvent(
                "error",
                "radiance.ppfd_rgb_decode.invalid",
                str(exc),
                {
                    "baseline_ppfd_transport_basis": BASELINE_PPFD_TRANSPORT_BASIS,
                    "baseline_ppfd_rgb_decode_method": BASELINE_PPFD_RGB_DECODE_METHOD,
                    "baseline_source_channel_policy": BASELINE_SOURCE_CHANNEL_POLICY,
                    "ppfd_conversion_basis": PPFD_CONVERSION_BASIS,
                    "photopic_luminance_weighting_avoided": (
                        BASELINE_PPFD_PHOTOPIC_LUMINANCE_WEIGHTING_AVOIDED
                    ),
                },
            )
        )
        return int(RadianceScriptExit.VALIDATION)
    rgb_path.unlink(missing_ok=True)
    _print_event(ScriptEvent("info", "command.succeeded", "command succeeded", {"command": "rtrace", "exit_code": 0}))
    print(f"✔ Wrote {out_map}")
    return int(RadianceScriptExit.OK)


def _symmetrize_if_requested(config: RuntimeConfig, *, in_map: Path, out_map: Path, enabled: bool, axes_only: bool) -> int:
    if not enabled:
        if in_map != out_map:
            shutil.move(str(in_map), str(out_map))
        return int(RadianceScriptExit.OK)
    args = ["--input", str(in_map), "--output", str(out_map), "--lam", "1.0", "--tol", "1e-6", "--verbose"]
    if axes_only:
        args.append("--axes-only")
    result = _run_python_module(config, "rad_rebuild.radiance.engine.photometry.symmetrize_ppfd", args)
    if result == 0 and in_map != out_map:
        in_map.unlink(missing_ok=True)
    return result


def generate_sensor_grid(argv: Sequence[str] = (), raw_env: Mapping[str, str] | None = None) -> int:
    config = _runtime_config(raw_env)
    _ensure_dirs(config.root)
    out_path = Path(argv[0]) if argv else config.root / "sensor_points.txt"
    env = dict(config.env)
    env.update(
        {
            "WALL_MARGIN_M": _env_text(env, "GRID_WALL_MARGIN_M", "0.005"),
            "GRID_MODULE_SIDE_M": _env_text(env, "GRID_MODULE_SIDE_M", "0.0"),
            "MODULE_SIDE_M": "0.0",
        }
    )
    return _run_capture_to_file(
        [config.py, "-m", "rad_rebuild.radiance.engine.geometry.generate_grid", "rtrace"],
        out_path,
        cwd=config.repo_root,
        env=env,
    )


def generate_precomputed_bundles(argv: Sequence[str] = (), raw_env: Mapping[str, str] | None = None) -> int:
    config = _runtime_config(raw_env)
    args = list(argv)
    profile = args[0] if args else "smoke"
    if profile in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  scripts/radiance/generate_precomputed_bundles.sh [smoke|full|custom] [precompute_sweep args...]\n"
        )
        return int(RadianceScriptExit.OK)
    if profile.startswith("--"):
        profile = "custom"
    else:
        args = args[1:]
    has_dataset_root = any(arg == "--dataset-root" or arg.startswith("--dataset-root=") for arg in args)
    dataset_args: list[str] = []
    if not has_dataset_root:
        precomputed_root = config.env.get("RADIANCE_PRECOMPUTED_ROOT", "")
        if not precomputed_root:
            print("ERROR: Set RADIANCE_PRECOMPUTED_ROOT or pass --dataset-root.", file=sys.stderr)
            print("Refusing to fall back to repo runtime outputs for precomputed generation.", file=sys.stderr)
            return int(RadianceScriptExit.USAGE)
        dataset_args = ["--dataset-root", precomputed_root]
    profile_args: list[str]
    match profile:
        case "smoke":
            profile_args = [
                "--length-min",
                "10",
                "--length-max",
                "10",
                "--width-min",
                "10",
                "--width-max",
                "10",
                "--square-only",
                "--modes",
                "SMD",
                "Competitor",
                "1000W HPS",
                "--competitor-layouts",
                "practical",
                "--hps-coverages",
                "4",
                "--hps-z-m",
                "0.4572",
            ]
        case "full":
            profile_args = [
                "--length-min",
                "10",
                "--length-max",
                "30",
                "--width-min",
                "10",
                "--width-max",
                "30",
                "--modes",
                "SMD",
                "Competitor",
                "1000W HPS",
                "--competitor-layouts",
                "practical",
                "--hps-coverages",
                "4",
                "--hps-ies-variants",
                "karma",
            ]
        case "custom":
            profile_args = []
        case _:
            print(f"Unknown profile: {profile}", file=sys.stderr)
            return int(RadianceScriptExit.USAGE)
    return _run_python_module(
        config,
        "rad_rebuild.radiance.engine.simulation.precompute_sweep",
        [*dataset_args, *profile_args, *args],
    )


def _prepare_static_scene(config: RuntimeConfig, *, room: Path, sensors: Path, reuse: bool) -> int:
    if reuse and room.is_file() and sensors.is_file():
        print("Reusing static room and sensor grid.")
        return int(RadianceScriptExit.OK)
    room_exit = _run_python_module_to_file(config, "rad_rebuild.radiance.engine.geometry.generate_room", room)
    if room_exit != 0:
        return room_exit
    return generate_sensor_grid([str(sensors)], config.env)


def run_simulation_smd(raw_env: Mapping[str, str] | None = None) -> int:
    config = _runtime_config(raw_env, smd_defaults=True)
    _ensure_dirs(config.root, config.runtime_state_root, config.cache_root)
    config.env["RAYPATH"] = _build_raypath(config)
    print()
    print("--- SMD PPFD Simulation (single-pass, PPFD units) ---")
    rad_tmp = Path(_env_text(config.env, "RAD_TMP", str(config.cache_root)))
    _ensure_dirs(rad_tmp)
    config.env["RAD_TMP"] = str(rad_tmp)
    ppfd_map = config.root / "ppfd_map.txt"
    ppfd_map.unlink(missing_ok=True)
    outdir = config.runtime_state_root
    octree = rad_tmp / "smd_scene.oct"
    room = config.root / "room.rad"
    sensors = config.root / "sensor_points.txt"
    octree.unlink(missing_ok=True)
    emitter_exit = _run_python_module(
        config,
        "rad_rebuild.radiance.engine.emitters.generate_emitters_smd",
        extra_env={"OPTICS": _env_text(config.env, "OPTICS", "stack"), "SUBPATCH_GRID": _env_text(config.env, "SUBPATCH_GRID", "1")},
    )
    if emitter_exit != 0:
        return emitter_exit
    static_exit = _prepare_static_scene(
        config,
        room=room,
        sensors=sensors,
        reuse=_bool_env(config.env, "REUSE_STATIC_SCENE"),
    )
    if static_exit != 0:
        return static_exit
    emitter_file = outdir / "emitters_smd_ALL_umol.rad"
    for required in (room, emitter_file, sensors):
        if not required.is_file():
            print(f"ERROR: {required} not found.", file=sys.stderr)
            return int(RadianceScriptExit.VALIDATION)
    plant_exit, plant_artifacts = _prepare_optional_plant_artifacts_or_report(config)
    if plant_exit != 0:
        return plant_exit
    plant_rad = plant_artifacts.radiance if plant_artifacts else None
    print("Geometry files:")
    print(f"  • {room}")
    _print_optional_plant_artifact_note(plant_artifacts)
    print("Emitters:")
    print(f"  • {emitter_file}")
    mode = _env_text(config.env, "MODE", "standard")
    nthreads = 1 if mode == "direct" else int(_env_text(config.env, "NTHREADS", str(_cpu_count())))
    ambcache = rad_tmp / "amb"
    for old in rad_tmp.glob("amb*"):
        old.unlink(missing_ok=True)
    static_room_oct = Path(config.env.get("STATIC_ROOM_OCT", ""))
    if str(static_room_oct) and static_room_oct.is_file():
        print("Building octree from frozen room...")
        oct_exit = _build_octree(
            config,
            _octree_scene_inputs(
                room=room,
                emitter_file=emitter_file,
                plant_rad=plant_rad,
                static_room_oct=static_room_oct,
            ),
            octree,
        )
    else:
        print("Building octree...")
        oct_exit = _build_octree(
            config,
            _octree_scene_inputs(
                room=room,
                emitter_file=emitter_file,
                plant_rad=plant_rad,
            ),
            octree,
        )
    if oct_exit != 0:
        return oct_exit
    oversample = int(_env_text(config.env, "OS", "4"))
    snake = rad_tmp / "sensors_snake.txt"
    snake_os = rad_tmp / f"sensors_snake_os_{oversample}.txt"
    dirs = rad_tmp / f"dirs_tmp_os_{oversample}.txt"
    _write_snake_and_dirs(sensors, snake, snake_os, dirs, oversample)
    trace_exit = _trace_ppfd(
        config,
        octree=octree,
        dirs=dirs,
        snake_os=snake_os,
        out_map=ppfd_map,
        oversample=oversample,
        nthreads=nthreads,
        options=_radiance_options(mode, ambcache),
        tag="smd",
    )
    if trace_exit != 0:
        return trace_exit
    if not _bool_env(config.env, "REUSE_SENSOR_CACHE"):
        for path in (snake, snake_os, dirs, rad_tmp / "sensors_source.sha256", rad_tmp / f"sensors_os_{oversample}.sha256"):
            path.unlink(missing_ok=True)
    sym_exit = _symmetrize_if_requested(
        config,
        in_map=ppfd_map,
        out_map=ppfd_map,
        enabled=_bool_env(config.env, "SYM", "1"),
        axes_only=bool(config.env.get("AXES_ONLY", "")),
    )
    if sym_exit != 0:
        return sym_exit
    if _bool_env(config.env, "LOG_CAP_METRICS", "1"):
        _print_cap_metrics(config, cap=config.env.get("SETPOINT_PPFD") or config.env.get("TARGET_PPFD", ""), watts=None, emitted_ppf=None)
    fspm_receiver_octree = rad_tmp / "smd_fspm_receiver.oct"
    receiver_octree = octree
    leaf_material_metadata: Mapping[str, Any] | None = None
    spectral_transport_mode = normalize_fspm_spectral_transport_mode(
        config.env.get(FSPM_SPECTRAL_TRANSPORT_MODE_ENV)
    )
    if (
        plant_artifacts is not None
        and spectral_transport_mode != SPECTRAL_TRANSPORT_MODE_BANDED_5
    ):
        receiver_spectral_mode = _infer_fixture_spectral_mode(
            config.env,
            octree=octree,
            mode=mode,
        )
        material_exit, receiver_material = _prepare_fspm_receiver_plant_material_or_report(
            config,
            plant_artifacts,
            spectral_mode=receiver_spectral_mode,
        )
        if material_exit != 0 or receiver_material is None:
            return material_exit
        receiver_oct_exit = _build_fspm_receiver_octree(
            config,
            room=room,
            emitter_file=emitter_file,
            plant_rad=receiver_material.radiance_path,
            out_path=fspm_receiver_octree,
            static_room_oct=static_room_oct if str(static_room_oct) else None,
        )
        if receiver_oct_exit != 0:
            return receiver_oct_exit
        receiver_octree = fspm_receiver_octree
        leaf_material_metadata = receiver_material.metadata
    surface_flux_exit = _write_optional_plant_surface_flux_artifact(
        config,
        plant_artifacts,
        ppfd_map,
        octree=receiver_octree,
        mode=mode,
        nthreads=nthreads,
        leaf_material_metadata=leaf_material_metadata,
        room=room,
        emitter_file=emitter_file,
        static_room_oct=static_room_oct if str(static_room_oct) else None,
    )
    if surface_flux_exit != 0:
        return surface_flux_exit
    return int(RadianceScriptExit.OK)


def _run_hps_pass(
    config: RuntimeConfig,
    *,
    eff_scale: float,
    tag: str,
    out_map: Path,
    room: Path,
    dirs: Path,
    snake_os: Path,
    octree: Path,
    nthreads: int,
    oversample: int,
    options_base: Sequence[str],
    plant_rad: Path | None,
) -> int:
    env = {
        "HPS_FIXTURE_PPF": config.env["HPS_FIXTURE_PPF"],
        "HPS_INPUT_WATTS": config.env["HPS_INPUT_WATTS"],
        "HPS_IES_VARIANT": config.env["HPS_IES_VARIANT"],
        "HPS_Z_M": config.env["HPS_Z_M"],
        "HPS_COVERAGE_FT": config.env["HPS_COVERAGE_FT"],
        "EFF_SCALE": f"{eff_scale:.8f}",
    }
    emitter_exit = _run_python_module(config, "rad_rebuild.radiance.engine.emitters.generate_emitters_hps", extra_env=env)
    if emitter_exit != 0:
        return emitter_exit
    emitter_file = config.runtime_state_root / "emitters_hps_ALL_umol.rad"
    if not emitter_file.is_file():
        print(f"ERROR: {emitter_file} missing.", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION)
    print("Emitters:")
    print(f"  • {emitter_file}")
    print("Building octree...")
    oct_exit = _build_octree(
        config,
        _octree_scene_inputs(
            room=room,
            emitter_file=emitter_file,
            plant_rad=plant_rad,
        ),
        octree,
    )
    if oct_exit != 0:
        return oct_exit
    map_tmp = config.cache_root / f".ppfd_map_tmp_{tag}.txt"
    ambcache = _fresh_ambient_cache(config, f"amb_hps_{tag}")
    trace_exit = _trace_ppfd(
        config,
        octree=octree,
        dirs=dirs,
        snake_os=snake_os,
        out_map=map_tmp,
        oversample=oversample,
        nthreads=nthreads,
        options=[*options_base, "-af", str(ambcache)],
        tag=tag,
    )
    if trace_exit != 0:
        return trace_exit
    return _symmetrize_if_requested(
        config,
        in_map=map_tmp,
        out_map=out_map,
        enabled=_bool_env(config.env, "SYM", "1"),
        axes_only=bool(config.env.get("AXES_ONLY", "")),
    )


def run_simulation_hps(raw_env: Mapping[str, str] | None = None) -> int:
    from rad_rebuild.radiance.engine.emitters.hps_generation.profile import PROFILE_VERSION, get_ies_comparator_profile

    config = _runtime_config(raw_env)
    _ensure_dirs(config.root, config.runtime_state_root, config.cache_root)
    config.env["RAYPATH"] = _build_raypath(config)
    print()
    print("--- 1000W HPS PPFD Simulation (single-pass, PPFD units) ---")
    ppfd_map = config.root / "ppfd_map.txt"
    ppfd_map.unlink(missing_ok=True)
    room = config.root / "room.rad"
    sensors = config.root / "sensor_points.txt"
    octree = config.cache_root / "hps_scene.oct"
    config.env["HPS_IES_VARIANT"] = _env_text(config.env, "HPS_IES_VARIANT", "karma")
    profile = get_ies_comparator_profile(config.env["HPS_IES_VARIANT"])
    config.env["HPS_FIXTURE_PPF"] = _env_text(config.env, "HPS_FIXTURE_PPF", str(profile.nominal_fixture_ppf_umol_s))
    config.env["HPS_INPUT_WATTS"] = _env_text(config.env, "HPS_INPUT_WATTS", str(profile.nominal_input_watts))
    config.env["HPS_Z_M"] = _env_text(config.env, "HPS_Z_M", "0.9144")
    config.env["HPS_COVERAGE_FT"] = _env_text(config.env, "HPS_COVERAGE_FT", "4")
    eff_scale = _clamp_0_1(_float_env(config.env, "EFF_SCALE", "1.0"))
    config.env["EFF_SCALE"] = f"{eff_scale:.8f}"
    room_exit = _run_python_module_to_file(config, "rad_rebuild.radiance.engine.geometry.generate_room", room)
    if room_exit != 0:
        return room_exit
    grid_exit = generate_sensor_grid([str(sensors)], config.env)
    if grid_exit != 0:
        return grid_exit
    print("Geometry files:")
    print(f"  • {room}")
    plant_exit, plant_artifacts = _prepare_optional_plant_artifacts_or_report(config)
    if plant_exit != 0:
        return plant_exit
    plant_rad = plant_artifacts.radiance if plant_artifacts else None
    _print_optional_plant_artifact_note(plant_artifacts)
    mode = _env_text(config.env, "MODE", "standard")
    nthreads = 1 if mode == "direct" else _cpu_count()
    oversample = int(_env_text(config.env, "OS", "4"))
    snake = config.cache_root / "sensors_snake.txt"
    snake_os = config.cache_root / f"sensors_snake_os_{oversample}.txt"
    dirs = config.cache_root / f"dirs_tmp_os_{oversample}.txt"
    _write_snake_and_dirs(sensors, snake, snake_os, dirs, oversample)
    print("Target:")
    target = config.env.get("TARGET_PPFD", "")
    print(f"  requested PPFD target: {target if target else '(not set)'}")
    print("  fixed-output policy: enabled (1000W HPS is not auto-dimmed)")
    print(f"  source model: whole_fixture_ies_comparator:{config.env['HPS_IES_VARIANT']}")
    pass1 = config.root / ".ppfd_map_pass1.txt"
    print(f"Pass 1 (EFF_SCALE={eff_scale:.8f})...")
    pass_exit = _run_hps_pass(
        config,
        eff_scale=eff_scale,
        tag="p1",
        out_map=pass1,
        room=room,
        dirs=dirs,
        snake_os=snake_os,
        octree=octree,
        nthreads=nthreads,
        oversample=oversample,
        options_base=_radiance_options(mode),
        plant_rad=plant_rad,
    )
    if pass_exit != 0:
        return pass_exit
    print(f"Pass 1 mean PPFD: {_ppfd_mean(pass1):.6f}")
    shutil.copyfile(pass1, ppfd_map)
    if target:
        print("Fixed-output note: requested target is used for reporting only; the HPS field is not scaled.")
    pass1.unlink(missing_ok=True)
    layout = _load_manifest(config.runtime_state_root / "hps_layout.json") or {}
    fixtures = len(layout.get("fixtures") or [])
    fixture_ppf = float(config.env["HPS_FIXTURE_PPF"])
    input_watts = float(config.env["HPS_INPUT_WATTS"])
    total_ppf = fixture_ppf * fixtures * eff_scale
    total_watts = (total_ppf / fixture_ppf) * input_watts if fixture_ppf > 0 and input_watts > 0 else float("nan")
    fixture_ppe = fixture_ppf / input_watts if input_watts > 0 else 0.0
    print("Final 1000W HPS output:")
    print(f"  fixtures={fixtures}")
    print(f"  coverage_ft={config.env['HPS_COVERAGE_FT']}")
    print(f"  PPF/fixture={config.env['HPS_FIXTURE_PPF']} µmol/s  dimmer(EFF_SCALE)={eff_scale:.8f}")
    print(f"  total PPF ≈ {total_ppf:.2f} µmol/s")
    print(f"  total electrical power ≈ {total_watts:.1f} W  (fixture PPE={fixture_ppe:.6f} µmol/J)")
    power_meta = config.runtime_state_root / "hps_power.txt"
    power_meta.write_text(
        "\n".join(
            [
                f"profile={PROFILE_VERSION}",
                f"coverage_ft={config.env['HPS_COVERAGE_FT']}",
                f"eff_scale={eff_scale:.8f}",
                f"fixture_ppf={config.env['HPS_FIXTURE_PPF']}",
                f"fixture_input_w={config.env['HPS_INPUT_WATTS']}",
                f"fixture_ppe={fixture_ppe:.6f}",
                f"total_ppf={total_ppf:.2f}",
                f"total_w={total_watts:.1f}",
                f"fixture_count={fixtures}",
                f"ies_variant={config.env['HPS_IES_VARIANT']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if _bool_env(config.env, "LOG_CAP_METRICS", "1"):
        _print_cap_metrics(config, cap=config.env.get("SETPOINT_PPFD") or target, watts=total_watts, emitted_ppf=total_ppf)
    emitter_file = config.runtime_state_root / "emitters_hps_ALL_umol.rad"
    fspm_receiver_octree = config.cache_root / "hps_fspm_receiver.oct"
    receiver_octree = octree
    leaf_material_metadata: Mapping[str, Any] | None = None
    spectral_transport_mode = normalize_fspm_spectral_transport_mode(
        config.env.get(FSPM_SPECTRAL_TRANSPORT_MODE_ENV)
    )
    if (
        plant_artifacts is not None
        and spectral_transport_mode != SPECTRAL_TRANSPORT_MODE_BANDED_5
    ):
        receiver_spectral_mode = _infer_fixture_spectral_mode(
            config.env,
            octree=octree,
            mode=mode,
        )
        material_exit, receiver_material = _prepare_fspm_receiver_plant_material_or_report(
            config,
            plant_artifacts,
            spectral_mode=receiver_spectral_mode,
        )
        if material_exit != 0 or receiver_material is None:
            return material_exit
        receiver_oct_exit = _build_fspm_receiver_octree(
            config,
            room=room,
            emitter_file=emitter_file,
            plant_rad=receiver_material.radiance_path,
            out_path=fspm_receiver_octree,
        )
        if receiver_oct_exit != 0:
            return receiver_oct_exit
        receiver_octree = fspm_receiver_octree
        leaf_material_metadata = receiver_material.metadata
    surface_flux_exit = _write_optional_plant_surface_flux_artifact(
        config,
        plant_artifacts,
        ppfd_map,
        octree=receiver_octree,
        mode=mode,
        nthreads=nthreads,
        leaf_material_metadata=leaf_material_metadata,
        room=room,
        emitter_file=emitter_file,
    )
    if surface_flux_exit != 0:
        return surface_flux_exit
    return int(RadianceScriptExit.OK)


def _spy_total_watts(ppf: float, ppe: float) -> float:
    return ppf / ppe if ppe > 0 else float("nan")


def _spy_droop_k(ppe_full: float, ppe_low: float, w_full: float, w_low: float) -> float:
    if ppe_full <= 0 or ppe_low <= 0 or w_full <= 0 or w_low <= 0 or w_full == w_low:
        return 0.0
    value = np.log(ppe_low / ppe_full) / -np.log(w_low / w_full)
    return float(value) if np.isfinite(value) else 0.0


def _spy_ppe_for_eff(ppe_full: float, w_full: float, eff: float, droop_k: float) -> float:
    if ppe_full <= 0 or w_full <= 0:
        return float("nan")
    fraction = min(1.2, max(0.05, (w_full * eff) / w_full))
    value = ppe_full * np.exp((-droop_k) * np.log(fraction))
    return float(value) if np.isfinite(value) else ppe_full


def _run_spydr_pass(
    config: RuntimeConfig,
    *,
    eff_scale: float,
    tag: str,
    out_map: Path,
    room: Path,
    dirs: Path,
    snake_os: Path,
    octree: Path,
    nthreads: int,
    oversample: int,
    options_base: Sequence[str],
    static_room_oct: Path | None,
    plant_rad: Path | None,
) -> int:
    emitter_exit = _run_python_module(
        config,
        "rad_rebuild.radiance.engine.emitters.generate_emitters_spydr3",
        extra_env={
            "SPYDR_PPF": config.env["SPYDR_PPF"],
            "SPYDR_Z_M": config.env["SPYDR_Z_M"],
            "NX": config.env["NX"],
            "NY": config.env["NY"],
            "SUBPATCH_GRID": config.env["SUBPATCH_GRID"],
            "EFF_SCALE": f"{eff_scale:.8f}",
        },
    )
    if emitter_exit != 0:
        return emitter_exit
    emitter_file = config.runtime_state_root / "emitters_spydr3_ALL_umol.rad"
    if not emitter_file.is_file():
        print("ERROR: conventional emitter Radiance file missing.", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION)
    print("Emitters:")
    print("  • conventional emitter Radiance file")
    if static_room_oct and static_room_oct.is_file():
        print("Building octree from frozen room...")
        oct_exit = _build_octree(
            config,
            _octree_scene_inputs(
                room=room,
                emitter_file=emitter_file,
                plant_rad=plant_rad,
                static_room_oct=static_room_oct,
            ),
            octree,
        )
    else:
        print("Building octree...")
        oct_exit = _build_octree(
            config,
            _octree_scene_inputs(
                room=room,
                emitter_file=emitter_file,
                plant_rad=plant_rad,
            ),
            octree,
        )
    if oct_exit != 0:
        return oct_exit
    map_tmp = config.cache_root / f".ppfd_map_tmp_{tag}.txt"
    ambcache = _fresh_ambient_cache(config, f"amb_spydr_{tag}")
    trace_exit = _trace_ppfd(
        config,
        octree=octree,
        dirs=dirs,
        snake_os=snake_os,
        out_map=map_tmp,
        oversample=oversample,
        nthreads=nthreads,
        options=[*options_base, "-af", str(ambcache)],
        tag=tag,
    )
    if trace_exit != 0:
        return trace_exit
    return _symmetrize_if_requested(
        config,
        in_map=map_tmp,
        out_map=out_map,
        enabled=_bool_env(config.env, "SYM", "0"),
        axes_only=bool(config.env.get("AXES_ONLY", "")),
    )


def run_simulation_spydr3(raw_env: Mapping[str, str] | None = None) -> int:
    config = _runtime_config(raw_env)
    _ensure_dirs(config.root, config.runtime_state_root, config.cache_root)
    config.env["RAYPATH"] = _build_raypath(config)
    print()
    print("--- Conventional LED PPFD Simulation (single-pass, PPFD units) ---")
    defaults = {
        "MODE": "standard",
        "SUBPATCH_GRID": "1",
        "OS": "4",
        "SYM": "0",
        "TARGET_PPFD": "",
        "AUTO_DIM": "1",
        "AUTO_DIM_MODE": "scale",
        "AUTO_DIM_TARGET": "mean",
        "SPYDR_PPF": "2240.0",
        "SPYDR_Z_M": "0.4572",
        "NX": "1",
        "NY": "1",
        "EFF_SCALE": "1.0",
        "SPYDR_PPE_UMOL_PER_J": "2.8",
        "SPYDR_DROOP": "0",
        "SPYDR_W_FULL": "663.2",
        "SPYDR_W_LOW": "331.6",
    }
    for key, value in defaults.items():
        config.env[key] = _env_text(config.env, key, value)
    config.env["SPYDR_PPE_FULL"] = _env_text(config.env, "SPYDR_PPE_FULL", config.env["SPYDR_PPE_UMOL_PER_J"])
    config.env["SPYDR_PPE_LOW"] = _env_text(config.env, "SPYDR_PPE_LOW", config.env["SPYDR_PPE_UMOL_PER_J"])
    eff_scale = _clamp_0_1(float(config.env["EFF_SCALE"]))
    ppfd_map = config.root / "ppfd_map.txt"
    ppfd_map.unlink(missing_ok=True)
    room = config.root / "room.rad"
    sensors = config.root / "sensor_points.txt"
    room_exit = _run_python_module_to_file(config, "rad_rebuild.radiance.engine.geometry.generate_room", room)
    if room_exit != 0:
        return room_exit
    grid_exit = generate_sensor_grid([str(sensors)], config.env)
    if grid_exit != 0:
        return grid_exit
    print("Geometry files:")
    print(f"  • {room}")
    plant_exit, plant_artifacts = _prepare_optional_plant_artifacts_or_report(config)
    if plant_exit != 0:
        return plant_exit
    plant_rad = plant_artifacts.radiance if plant_artifacts else None
    _print_optional_plant_artifact_note(plant_artifacts)
    mode = config.env["MODE"]
    nthreads = 1 if mode == "direct" else _cpu_count()
    oversample = int(config.env["OS"])
    static_room_oct: Path | None = None
    if _bool_env(config.env, "USE_FROZEN_ROOM_OCTREE") or (_bool_env(config.env, "AUTO_DIM") and config.env["AUTO_DIM_MODE"] == "rerun"):
        static_room_oct = config.cache_root / "spydr_room_static.oct"
        print("Building frozen room octree...")
        oct_exit = _build_octree(config, ["-f", str(room)], static_room_oct)
        if oct_exit != 0:
            return oct_exit
    snake = config.cache_root / "sensors_snake.txt"
    snake_os = config.cache_root / f"sensors_snake_os_{oversample}.txt"
    dirs = config.cache_root / f"dirs_tmp_os_{oversample}.txt"
    _write_snake_and_dirs(sensors, snake, snake_os, dirs, oversample)
    target = config.env.get("TARGET_PPFD", "")
    print("Target:")
    print(f"  target mean PPFD: {target if target else '(not set)'}")
    pass1 = config.root / ".ppfd_map_pass1.txt"
    print(f"Pass 1 (EFF_SCALE={eff_scale:.8f})...")
    pass_exit = _run_spydr_pass(
        config,
        eff_scale=eff_scale,
        tag="p1",
        out_map=pass1,
        room=room,
        dirs=dirs,
        snake_os=snake_os,
        octree=config.cache_root / "spydr_scene.oct",
        nthreads=nthreads,
        oversample=oversample,
        options_base=_radiance_options(mode),
        static_room_oct=static_room_oct,
        plant_rad=plant_rad,
    )
    if pass_exit != 0:
        return pass_exit
    mean1 = _ppfd_mean(pass1)
    peak1 = _ppfd_max(pass1)
    print(f"Pass 1 mean PPFD: {mean1:.6f}")
    print(f"Pass 1 peak PPFD: {peak1:.6f}")
    final_eff = eff_scale
    receiver_scale_multiplier = 1.0
    if _bool_env(config.env, "AUTO_DIM") and target:
        reference = peak1 if config.env["AUTO_DIM_TARGET"] == "peak" else mean1
        label = "peak-cap" if config.env["AUTO_DIM_TARGET"] == "peak" else "mean-target"
        if reference > 0:
            multiplier = float(target) / reference
            desired = eff_scale * multiplier
            final_eff = _clamp_0_1(desired)
            print(f"Auto-dim ({label}): multiplier={multiplier:.8f} → desired EFF_SCALE={desired:.8f} → final EFF_SCALE={final_eff:.8f}")
            if abs(final_eff - eff_scale) > 1e-9:
                if config.env["AUTO_DIM_MODE"] == "scale" and eff_scale > 0:
                    scale_multiplier = final_eff / eff_scale
                    receiver_scale_multiplier = scale_multiplier
                    print(f"Scale-only {label}: multiplier={scale_multiplier:.8f} (no second Radiance pass)")
                    _scale_ppfd_map(pass1, ppfd_map, scale_multiplier)
                    print(f"Scaled mean PPFD: {_ppfd_mean(ppfd_map):.6f}")
                    print(f"Scaled peak PPFD: {_ppfd_max(ppfd_map):.6f}")
                else:
                    print(f"Pass 2 (EFF_SCALE={final_eff:.8f})...")
                    pass_exit = _run_spydr_pass(
                        config,
                        eff_scale=final_eff,
                        tag="p2",
                        out_map=ppfd_map,
                        room=room,
                        dirs=dirs,
                        snake_os=snake_os,
                        octree=config.cache_root / "spydr_scene.oct",
                        nthreads=nthreads,
                        oversample=oversample,
                        options_base=_radiance_options(mode),
                        static_room_oct=static_room_oct,
                        plant_rad=plant_rad,
                    )
                    if pass_exit != 0:
                        return pass_exit
            else:
                shutil.copyfile(pass1, ppfd_map)
        else:
            print("WARNING: Pass 1 peak PPFD is <= 0; skipping auto-dim.")
            shutil.copyfile(pass1, ppfd_map)
    else:
        shutil.copyfile(pass1, ppfd_map)
    pass1.unlink(missing_ok=True)
    layout = _load_manifest(config.runtime_state_root / "spydr3_layout.json") or {}
    fixtures = len(layout.get("fixtures") or []) or int(config.env["NX"]) * int(config.env["NY"])
    layout_nx = int(layout.get("nx", config.env["NX"]) or config.env["NX"])
    layout_ny = int(layout.get("ny", config.env["NY"]) or config.env["NY"])
    layout_mode = str(layout.get("layout_mode", config.env.get("SPYDR_LAYOUT_MODE", "full")) or "full")
    total_ppf = float(config.env["SPYDR_PPF"]) * fixtures * final_eff
    droop_k = 0.0
    ppe_effective = float(config.env["SPYDR_PPE_UMOL_PER_J"])
    if _bool_env(config.env, "SPYDR_DROOP"):
        droop_k = _spy_droop_k(float(config.env["SPYDR_PPE_FULL"]), float(config.env["SPYDR_PPE_LOW"]), float(config.env["SPYDR_W_FULL"]), float(config.env["SPYDR_W_LOW"]))
        ppe_effective = _spy_ppe_for_eff(float(config.env["SPYDR_PPE_FULL"]), float(config.env["SPYDR_W_FULL"]), final_eff, droop_k)
    total_watts = _spy_total_watts(total_ppf, ppe_effective)
    fixture_input_w = _spy_total_watts(float(config.env["SPYDR_PPF"]), float(config.env["SPYDR_PPE_UMOL_PER_J"]))
    print("Final Conventional output:")
    print(f"  fixtures={fixtures} (NX={layout_nx}, NY={layout_ny})")
    print(f"  layout_mode={layout_mode}")
    print("  model=EnVision Qube QB-FSG-8B-7T660W whole-fixture IES+SPD comparator")
    print(f"  PPF/fixture={config.env['SPYDR_PPF']} µmol/s  dimmer(EFF_SCALE)={final_eff:.8f}")
    print(f"  total PPF ≈ {total_ppf:.2f} µmol/s")
    print(f"  total electrical power ≈ {total_watts:.1f} W  (assumed PPE={ppe_effective:.6f} µmol/J)")
    (config.runtime_state_root / "spydr3_power.txt").write_text(
        "\n".join(
            [
                "model_label=EnVision Qube QB-FSG-8B-7T660W whole-fixture IES+SPD comparator",
                f"ppe_effective={ppe_effective:.6f}",
                f"ppe_full={config.env['SPYDR_PPE_FULL']}",
                f"ppe_low={config.env['SPYDR_PPE_LOW']}",
                f"w_full={config.env['SPYDR_W_FULL']}",
                f"w_low={config.env['SPYDR_W_LOW']}",
                f"droop_k={droop_k:.6f}",
                f"eff_scale={final_eff:.8f}",
                f"total_ppf={total_ppf:.2f}",
                f"total_w={total_watts:.1f}",
                f"fixture_input_w={fixture_input_w:.6f}",
                f"layout_mode={layout_mode}",
                f"fixture_count={fixtures}",
                f"droop_enabled={config.env['SPYDR_DROOP']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if _bool_env(config.env, "LOG_CAP_METRICS", "1"):
        _print_cap_metrics(config, cap=config.env.get("SETPOINT_PPFD") or target, watts=total_watts, emitted_ppf=total_ppf)
    emitter_file = config.runtime_state_root / "emitters_spydr3_ALL_umol.rad"
    fspm_receiver_octree = config.cache_root / "spydr_fspm_receiver.oct"
    receiver_octree = config.cache_root / "spydr_scene.oct"
    leaf_material_metadata: Mapping[str, Any] | None = None
    spectral_transport_mode = normalize_fspm_spectral_transport_mode(
        config.env.get(FSPM_SPECTRAL_TRANSPORT_MODE_ENV)
    )
    if (
        plant_artifacts is not None
        and spectral_transport_mode != SPECTRAL_TRANSPORT_MODE_BANDED_5
    ):
        receiver_spectral_mode = _infer_fixture_spectral_mode(
            config.env,
            octree=receiver_octree,
            mode=mode,
        )
        material_exit, receiver_material = _prepare_fspm_receiver_plant_material_or_report(
            config,
            plant_artifacts,
            spectral_mode=receiver_spectral_mode,
        )
        if material_exit != 0 or receiver_material is None:
            return material_exit
        receiver_oct_exit = _build_fspm_receiver_octree(
            config,
            room=room,
            emitter_file=emitter_file,
            plant_rad=receiver_material.radiance_path,
            out_path=fspm_receiver_octree,
            static_room_oct=static_room_oct,
        )
        if receiver_oct_exit != 0:
            return receiver_oct_exit
        receiver_octree = fspm_receiver_octree
        leaf_material_metadata = receiver_material.metadata
    surface_flux_exit = _write_optional_plant_surface_flux_artifact(
        config,
        plant_artifacts,
        ppfd_map,
        octree=receiver_octree,
        mode=mode,
        nthreads=nthreads,
        receiver_scale_multiplier=receiver_scale_multiplier,
        leaf_material_metadata=leaf_material_metadata,
        room=room,
        emitter_file=emitter_file,
        static_room_oct=static_room_oct,
    )
    if surface_flux_exit != 0:
        return surface_flux_exit
    print("Done.")
    return int(RadianceScriptExit.OK)


def run_basis_extraction(raw_env: Mapping[str, str] | None = None) -> int:
    from rad_rebuild.radiance.engine.emitters.smd_generation.solution_metadata import (
        build_smd_emitter_env_snapshot,
        build_smd_layout_snapshot,
        build_smd_sensor_grid_snapshot,
    )
    from rad_rebuild.radiance.engine.photometry.smd_curve_model import curve_model_manifest
    from rad_rebuild.radiance.engine.simulation.basis_backends import (
        BASIS_BACKEND_RTRACE,
        basis_ring_modifier_name,
        describe_basis_backend_config,
    )
    from rad_rebuild.radiance.paths import RADIANCE_ENGINE_PACKAGE_ROOT

    config = _runtime_config(raw_env, smd_defaults=True)
    _ensure_dirs(config.root, config.basis_output_root, config.runtime_state_root, config.cache_root)
    print()
    print("--- Basis extraction: building A matrix from Radiance ---")
    basis_dir = config.basis_output_root / "basis_runs"
    basis_unit_w = float(_env_text(config.env, "BASIS_UNIT_W", "1.0"))
    rad_tmp = Path(_env_text(config.env, "RAD_TMP", str(config.cache_root)))
    basis_rad_tmp = rad_tmp / "basis"
    static_room_oct = basis_rad_tmp / "static_room.oct"
    basis_build_log = config.basis_output_root / "basis_build_log.json"
    basis_backend_log = basis_dir / "basis_backend_log.json"
    smd_var_mode, smd_all, smd_outer = _normalize_variable_mode(
        smd_var_mode=_env_text(config.env, "SMD_VAR_MODE", ""),
        smd_all_per_module=_env_text(config.env, "SMD_ALL_PER_MODULE", "0"),
        smd_outer_per_module=_env_text(config.env, "SMD_OUTER_PER_MODULE", "0"),
    )
    config.env.update({"SMD_VAR_MODE": smd_var_mode, "SMD_ALL_PER_MODULE": smd_all, "SMD_OUTER_PER_MODULE": smd_outer})
    pos, _, meta = _compute_positions_from_env()
    rings = int(meta.get("rings", meta.get("ring_n", 0) + 1))
    ring_n = max(0, rings - 1)
    module_count = len(pos)
    ring_vars = ring_n
    all_indices: list[int] = []
    outer_indices: list[int] = []
    outer_ring_index = ""
    if smd_all == "1":
        all_indices = list(range(module_count))
        ring_vars = 0
    elif smd_outer == "1":
        ring_max = max((int(p.get("ring", 0)) for p in pos), default=0)
        outer_ring_index = str(ring_max)
        outer_indices = [index for index, point in enumerate(pos) if int(point.get("ring", 0)) == ring_max]
        ring_vars = ring_max
    mode = _env_text(config.env, "MODE", "standard")
    oversample = int(_env_text(config.env, "OS", "4"))
    nthreads = int(_env_text(config.env, "NTHREADS", str(_cpu_count())))
    basis_backend = canonicalize_basis_backend(_env_text(config.env, "SMD_BASIS_BACKEND", "rtrace"))
    config.env.update({"MODE": mode, "OS": str(oversample), "NTHREADS": str(nthreads), "BASIS_UNIT_W": str(basis_unit_w), "SMD_BASIS_BACKEND": basis_backend})
    if basis_backend != "rtrace" and (smd_all == "1" or smd_outer == "1"):
        print(f"ERROR: basis backend {basis_backend} only supports SMD ring basis mode.", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION)
    _ensure_dirs(basis_dir, basis_rad_tmp)
    for pattern in ("basis_ring_*.txt", "basis_col_*.txt"):
        for path in basis_dir.glob(pattern):
            path.unlink(missing_ok=True)
    for path in (config.basis_output_root / "basis_A.npy", config.basis_output_root / "basis_A.csv", config.basis_output_root / "basis_manifest.json", basis_build_log, basis_backend_log):
        path.unlink(missing_ok=True)
    print(f"RINGS: 0..{ring_n} (total {rings})")
    print(f"MODULES: {module_count}")
    print(f"Basis unit power per module: {basis_unit_w} W")
    print(f"MODE: {mode}")
    print(f"Basis backend: {basis_backend}")
    print(f"Sensor oversample: {oversample}")
    print(f"Threads: {nthreads}")
    print(f"Basis temp cache: {basis_rad_tmp}")
    print()
    print("Preparing static room, sensor grid, and frozen room octree...")
    room = config.root / "room.rad"
    sensors = config.root / "sensor_points.txt"
    room_exit = _run_python_module_to_file(config, "rad_rebuild.radiance.engine.geometry.generate_room", room)
    if room_exit != 0:
        return room_exit
    grid_exit = generate_sensor_grid([str(sensors)], config.env)
    if grid_exit != 0:
        return grid_exit
    oct_exit = _build_octree(config, ["-f", str(room)], static_room_oct)
    if oct_exit != 0:
        return oct_exit
    print(f"Static room octree: {static_room_oct}")
    start = time.time()
    command_template = ""
    if basis_backend != "rtrace":
        print(f"=== Single-pass {basis_backend} ring basis ===")
        backend_exit = _run_python_module(
            config,
            "rad_rebuild.radiance.engine.simulation.basis_rcontrib",
            [
                "--backend",
                basis_backend,
                "--root",
                str(config.root),
                "--basis-dir",
                str(basis_dir),
                "--rad-tmp",
                str(basis_rad_tmp),
                "--static-room-oct",
                str(static_room_oct),
                "--sensor-points",
                str(sensors),
                "--ring-count",
                str(rings),
                "--basis-unit-w",
                str(basis_unit_w),
                "--oversample",
                str(oversample),
                "--nthreads",
                str(nthreads),
                "--log-path",
                str(basis_backend_log),
            ],
        )
        if backend_exit != 0:
            return backend_exit
    else:
        basis_env_common = {
            "RAD_TMP": str(basis_rad_tmp),
            "REUSE_STATIC_SCENE": "1",
            "REUSE_SENSOR_CACHE": "1",
            "STATIC_ROOM_OCT": str(static_room_oct),
            "SMD_BASIS_MODE": "1",
            "SMD_BASIS_UNIT_W": str(basis_unit_w),
            "MODE": mode,
            "SYM": "0",
            "NTHREADS": str(nthreads),
            "FSPM_PLANTS_ENABLED": "0",
            "FSPM_SKIP_DURING_BASIS": "1",
        }
        col = 0
        if smd_all == "1":
            command_template = f"python -m rad_rebuild.radiance.cli.scripts run-simulation-smd SMD_BASIS_MODULE_IDX=<module_idx> RAD_TMP={basis_rad_tmp}"
            for idx in all_indices:
                print(f"=== Module idx={idx}  col={col} ===")
                env = {**config.env, **basis_env_common, "SMD_BASIS_MODULE_IDX": str(idx), "SMD_BASIS_OUTER_MODULE_IDX": str(idx)}
                sim_exit = run_simulation_smd(env)
                if sim_exit != 0:
                    return sim_exit
                source = config.root / "ppfd_map.txt"
                if not source.is_file():
                    print(f"ERROR: ppfd_map.txt missing after basis run for module idx={idx}", file=sys.stderr)
                    return int(RadianceScriptExit.VALIDATION)
                shutil.move(str(source), str(basis_dir / f"basis_col_{col}.txt"))
                col += 1
        elif smd_outer == "1":
            command_template = f"python -m rad_rebuild.radiance.cli.scripts run-simulation-smd SMD_BASIS_RING=<ring> RAD_TMP={basis_rad_tmp}"
            for ring in range(ring_vars):
                print(f"=== Ring {ring} / {ring_vars - 1}  col={col} ===")
                env = {**config.env, **basis_env_common, "SMD_BASIS_RING": str(ring)}
                sim_exit = run_simulation_smd(env)
                if sim_exit != 0:
                    return sim_exit
                source = config.root / "ppfd_map.txt"
                if not source.is_file():
                    print(f"ERROR: ppfd_map.txt missing after basis run for ring {ring}", file=sys.stderr)
                    return int(RadianceScriptExit.VALIDATION)
                shutil.move(str(source), str(basis_dir / f"basis_col_{col}.txt"))
                col += 1
            for idx in outer_indices:
                print(f"=== Outer module idx={idx}  col={col} ===")
                env = {**config.env, **basis_env_common, "SMD_BASIS_OUTER_MODULE_IDX": str(idx), "SMD_BASIS_MODULE_IDX": str(idx)}
                sim_exit = run_simulation_smd(env)
                if sim_exit != 0:
                    return sim_exit
                source = config.root / "ppfd_map.txt"
                if not source.is_file():
                    print(f"ERROR: ppfd_map.txt missing after basis run for outer idx={idx}", file=sys.stderr)
                    return int(RadianceScriptExit.VALIDATION)
                shutil.move(str(source), str(basis_dir / f"basis_col_{col}.txt"))
                col += 1
        else:
            command_template = f"python -m rad_rebuild.radiance.cli.scripts run-simulation-smd SMD_BASIS_RING=<ring> RAD_TMP={basis_rad_tmp}"
            for ring in range(rings):
                print(f"=== Ring {ring} / {rings - 1} ===")
                env = {**config.env, **basis_env_common, "SMD_BASIS_RING": str(ring)}
                sim_exit = run_simulation_smd(env)
                if sim_exit != 0:
                    return sim_exit
                source = config.root / "ppfd_map.txt"
                if not source.is_file():
                    print(f"ERROR: ppfd_map.txt missing after basis run for ring {ring}", file=sys.stderr)
                    return int(RadianceScriptExit.VALIDATION)
                shutil.move(str(source), str(basis_dir / f"basis_ring_{ring}.txt"))
    elapsed = max(0.0, time.time() - start)
    print()
    print("✓ Basis runs complete. Building A matrix...")
    mode_cols = smd_outer == "1" or smd_all == "1"
    files = sorted(
        basis_dir.glob("basis_col_*.txt" if mode_cols else "basis_ring_*.txt"),
        key=lambda path: int(path.stem.split("_")[-1]),
    )
    if not files:
        print("No basis files found", file=sys.stderr)
        return int(RadianceScriptExit.VALIDATION)
    columns: list[np.ndarray[Any, np.dtype[np.float64]]] = []
    coords_ref: np.ndarray[Any, np.dtype[np.float64]] | None = None
    for file_path in files:
        data = np.loadtxt(file_path, dtype=float)
        if data.shape[1] != 4:
            print(f"{file_path} has unexpected shape {data.shape}, expected 4 cols (x,y,z,ppfd)", file=sys.stderr)
            return int(RadianceScriptExit.VALIDATION)
        xyz = data[:, :3]
        if coords_ref is None:
            coords_ref = xyz
        elif not np.allclose(coords_ref, xyz, atol=1e-6):
            print(f"Sensor grid mismatch in {file_path}", file=sys.stderr)
            return int(RadianceScriptExit.VALIDATION)
        columns.append(data[:, 3])
    matrix = np.stack(columns, axis=1)
    matrix_sha = hashlib.sha256(np.asarray(matrix, dtype=np.float64).tobytes()).hexdigest()
    np.save(config.basis_output_root / "basis_A.npy", matrix)
    np.savetxt(config.basis_output_root / "basis_A.csv", matrix, delimiter=",", fmt="%.6f")
    backend_log = _load_manifest(basis_backend_log) if basis_backend_log.exists() else None
    if basis_backend != BASIS_BACKEND_RTRACE and not mode_cols:
        expected = [basis_ring_modifier_name(index) for index in range(matrix.shape[1])]
        actual = list((backend_log or {}).get("modifier_order") or [])
        if actual != expected:
            print(f"rcontrib modifier order drift detected: expected {expected}, got {actual}", file=sys.stderr)
            return int(RadianceScriptExit.VALIDATION)
    backend_config = describe_basis_backend_config(basis_backend, sim_mode=mode, env=config.env)
    build_log = {
        "basis_backend": basis_backend,
        "basis_backend_config": backend_config,
        "command": str((backend_log or {}).get("command") or command_template),
        "wall_time_s": float((backend_log or {}).get("wall_time_s") or elapsed),
        "mode": mode,
        "oversample": oversample,
        "threads": nthreads,
        "ring_count": int(rings if rings > 0 else matrix.shape[1]),
        "sensor_count": int(matrix.shape[0]),
        "matrix_sha256": matrix_sha,
        "matrix_summary": {
            "min": float(np.min(matrix)),
            "max": float(np.max(matrix)),
            "mean": float(np.mean(matrix)),
            "col_mean": [float(value) for value in np.mean(matrix, axis=0)],
            "col_max": [float(value) for value in np.max(matrix, axis=0)],
        },
        "backend_log": backend_log,
    }
    basis_build_log.write_text(json.dumps(build_log, indent=2), encoding="utf-8")
    layout_snapshot = build_smd_layout_snapshot(module_count=module_count)
    sensor_grid = build_smd_sensor_grid_snapshot(sensor_path=sensors)
    engine_package_root = RADIANCE_ENGINE_PACKAGE_ROOT
    manifest: dict[str, Any] = {
        "n_points": int(matrix.shape[0]),
        "n_vars": int(matrix.shape[1]),
        "n_rings": int(rings if rings > 0 else matrix.shape[1]),
        "layout_modules": int(module_count),
        "room_L_m": layout_snapshot.get("room_L_m"),
        "room_W_m": layout_snapshot.get("room_W_m"),
        "layout_mode": layout_snapshot.get("layout_mode"),
        "layout_family": layout_snapshot.get("layout_family"),
        "base_n": layout_snapshot.get("base_n"),
        "sensor_file": "sensor_points.txt",
        "sensor_grid": sensor_grid,
        "basis_unit_w_per_module": basis_unit_w,
        "basis_backend": basis_backend,
        "basis_backend_config": backend_config,
        "basis_build_log_json": basis_build_log.name,
        "basis_matrix_sha256": matrix_sha,
        "generator": "generate_emitters_smd.py",
        "generator_sha256": hashlib.sha256((engine_package_root / "emitters" / "generate_emitters_smd.py").read_bytes()).hexdigest(),
        "module_profile_sha256": hashlib.sha256(
            (
                engine_package_root
                / "emitters"
                / "smd_generation"
                / "module_profile.py"
            ).read_bytes()
        ).hexdigest(),
        "curve_model_sha256": hashlib.sha256((engine_package_root / "photometry" / "smd_curve_model.py").read_bytes()).hexdigest(),
        "emitter_env": build_smd_emitter_env_snapshot(env=config.env),
        "curve_model": curve_model_manifest() if config.env.get("SMD_MODEL", "curve").strip().lower() != "legacy" else None,
    }
    if smd_all == "1":
        manifest.update({"variables": "per_module", "module_indices": list(range(matrix.shape[1])), "variable_groups": {"modules": int(matrix.shape[1])}})
    elif smd_outer == "1":
        manifest.update(
            {
                "variables": "ring_plus_outer_modules",
                "ring_indices": list(range(ring_vars)),
                "outer_ring_index": int(outer_ring_index or "0"),
                "outer_ring_indices": outer_indices,
                "variable_groups": {"rings": ring_vars, "outer_modules": len(outer_indices)},
            }
        )
    else:
        manifest.update({"variables": "rings", "ring_indices": list(range(matrix.shape[1]))})
    (config.basis_output_root / "basis_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("A shape:", matrix.shape)
    print("Basis backend:", basis_backend)
    print("Basis matrix sha256:", matrix_sha)
    print("Saved basis_A.npy and basis_A.csv and basis_manifest.json and basis_build_log.json")
    print()
    print("✓ Basis matrix A built.")
    return int(RadianceScriptExit.OK)


def reproduce(raw_env: Mapping[str, str] | None = None) -> int:
    config = _runtime_config(raw_env)
    root = config.root
    basis_root = Path(_env_text(config.env, "RADIANCE_BASIS_OUTPUT_ROOT", str(root / "basis")))
    out_dir = Path(_env_text(config.env, "OUT_DIR", "/out" if Path("/out").is_dir() else str(root / "artifacts")))
    mode = _env_text(config.env, "MODE", "quality")
    target_ppfd = _env_text(config.env, "TARGET_PPFD", "1000")
    run_basis_flag = _env_text(config.env, "RUN_BASIS", "1")
    env = dict(config.env)
    env.update(
        {
            "RADIANCE_OUTPUT_ROOT": str(root),
            "RADIANCE_BASIS_OUTPUT_ROOT": str(basis_root),
            "RADIANCE_RUNTIME_STATE_ROOT": _env_text(env, "RADIANCE_RUNTIME_STATE_ROOT", str(root / "runtime_state")),
            "RADIANCE_CACHE_ROOT": _env_text(env, "RADIANCE_CACHE_ROOT", str(root / "cache")),
            "PY": config.py,
            "MODE": mode,
            "TARGET_PPFD": target_ppfd,
            "RUN_BASIS": run_basis_flag,
            "MPLBACKEND": _env_text(env, "MPLBACKEND", "Agg"),
            "MPLCONFIGDIR": _env_text(env, "MPLCONFIGDIR", str(root / "cache" / "matplotlib")),
        }
    )
    _ensure_dirs(out_dir, root, basis_root)
    print("--- Reproduce: start ---")
    print(f"ROOT={root}")
    print(f"OUT_DIR={out_dir}")
    print(f"MODE={mode}")
    print(f"TARGET_PPFD={target_ppfd}")
    print(f"RUN_BASIS={run_basis_flag}")
    print()
    print("[1/3] Run uniformity pipeline...")
    uniformity_exit = run_uniformity(env)
    if uniformity_exit != 0:
        return uniformity_exit
    print()
    print("[2/3] Visualize PPFD...")
    viz_exit = _run_python_module(
        _runtime_config(env),
        "rad_rebuild.radiance.engine.visualization.visualize_ppfd",
        ["--overlay", "auto", "--outdir", str(out_dir / "ppfd_visualizations_proposed")],
    )
    if viz_exit != 0:
        return viz_exit
    print()
    print("[3/3] Collect artifacts and hashes...")
    copy_pairs = [
        (root / "ppfd_map.txt", out_dir / "ppfd_map.txt"),
        (root / "ring_powers_optimized.json", out_dir / "ring_powers_optimized.json"),
        (basis_root / "basis_A.npy", out_dir / "basis_A.npy"),
        (basis_root / "basis_manifest.json", out_dir / "basis_manifest.json"),
        (root / "runtime_state" / "smd_summary.txt", out_dir / "smd_summary.txt"),
        (root / "runtime_state" / "smd_layout.json", out_dir / "smd_layout.json"),
    ]
    for source, target in copy_pairs:
        if source.exists():
            shutil.copyfile(source, target)
    hash_names = {
        "ppfd_map.txt",
        "ring_powers_optimized.json",
        "basis_A.npy",
        "basis_manifest.json",
        "smd_summary.txt",
        "smd_layout.json",
        "ppfd_heatmap_overlay.png",
        "ppfd_heatmap_annotated.png",
    }
    with (out_dir / "hashes.txt").open("w", encoding="utf-8") as handle:
        for path in sorted(p for p in out_dir.rglob("*") if p.is_file() and p.name in hash_names):
            handle.write(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}\n")
    versions: dict[str, Any] = {
        "python": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "executable": sys.executable,
        "packages": {},
        "radiance": None,
        "git": None,
    }
    for module_name in ("numpy", "scipy", "pandas", "matplotlib", "plotly", "seaborn"):
        try:
            module = __import__(module_name)
            versions["packages"][module_name] = getattr(module, "__version__", None)
        except Exception:  # noqa: BLE001 - optional package provenance.
            versions["packages"][module_name] = None
    for command in (["rtrace", "-version"], ["oconv", "-version"]):
        try:
            output = _command_stdout(command, cwd=config.repo_root, env=env).strip()
        except RuntimeError:
            continue
        if output:
            versions["radiance"] = output.splitlines()[0]
            break
    try:
        versions["git"] = _command_stdout(["git", "rev-parse", "HEAD"], cwd=config.repo_root, env=env).strip()
    except RuntimeError:
        pass
    (out_dir / "versions.json").write_text(json.dumps(versions, indent=2), encoding="utf-8")
    try:
        freeze = _command_stdout([config.py, "-m", "pip", "freeze"], cwd=config.repo_root, env=env)
        (out_dir / "pip-freeze.txt").write_text(freeze, encoding="utf-8")
    except RuntimeError:
        pass
    print(f"Artifacts written to {out_dir}")
    print("--- Reproduce: done ---")
    return int(RadianceScriptExit.OK)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("generate-precomputed-bundles", help="expand a precomputed bundle profile and run precompute_sweep").add_argument("args", nargs=argparse.REMAINDER)
    subparsers.add_parser("generate-sensor-grid", help="generate the shared Radiance sensor grid").add_argument("args", nargs=argparse.REMAINDER)
    subparsers.add_parser("reproduce", help="run the reproducibility workflow")
    subparsers.add_parser("run-basis-extraction", help="build an SMD response basis matrix")
    subparsers.add_parser("run-simulation-hps", help="run the HPS comparator simulation")
    subparsers.add_parser("run-simulation-smd", help="run the SMD simulation")
    subparsers.add_parser("run-simulation-spydr3", help="run the conventional LED comparator simulation")
    subparsers.add_parser("run-uniformity", help="run the SMD uniformity workflow")
    args = parser.parse_args(argv)
    if args.command == "generate-precomputed-bundles":
        return generate_precomputed_bundles(args.args)
    if args.command == "generate-sensor-grid":
        return generate_sensor_grid(args.args)
    if args.command == "reproduce":
        return reproduce()
    if args.command == "run-basis-extraction":
        return run_basis_extraction()
    if args.command == "run-simulation-hps":
        return run_simulation_hps()
    if args.command == "run-simulation-smd":
        return run_simulation_smd()
    if args.command == "run-simulation-spydr3":
        return run_simulation_spydr3()
    if args.command == "run-uniformity":
        return run_uniformity()
    parser.error(f"unknown command: {args.command}")
    return int(RadianceScriptExit.USAGE)


if __name__ == "__main__":
    raise SystemExit(main())
