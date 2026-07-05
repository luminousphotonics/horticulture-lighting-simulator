#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import json
import os
import shutil
# Subprocess calls in this module use resolved argv lists without invoking a shell.
import subprocess  # nosec B404
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Mapping, TypedDict

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX platforms.
    fcntl = None  # type: ignore[assignment]

import numpy as np

from rad_rebuild.radiance.executables import (
    ExecutableResolutionError,
    resolve_executable,
)
from rad_rebuild.radiance.config import (
    ACTIVE_RADIANCE_MODES,
    COMPETITOR_FIXTURE_PPE_UMOL_PER_J,
    COMPETITOR_FIXTURE_PPF_UMOL_S,
    MODE_COMPETITOR,
    MODE_HPS,
    MODE_SMD,
    PUBLIC_PRECOMPUTED_MAX_FT,
    PUBLIC_PRECOMPUTED_MIN_FT,
)
from rad_rebuild.radiance.paths import (
    RADIANCE_BASIS_OUTPUT_ROOT,
    RADIANCE_OUTPUT_ROOT,
    RADIANCE_SCRIPTS_ROOT,
    REPO_ROOT,
)

from rad_rebuild.radiance.engine.simulation.precomputed_dataset import (
    PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_FILENAME,
    PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_FILENAME,
    PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_NPZ_FILENAME,
    PRECOMPUTED_PLANT_RECEIVER_JSON_FILENAME,
    PRECOMPUTED_FSPM_RECEIVER_GRANULARITY,
    SCHEMA_VERSION,
    SUPPORTED_PRECOMPUTED_FSPM_RECEIVER_GRANULARITIES,
    BundleRef,
    bundle_complete,
    bundle_ref,
    canonical_plant_enabled_precomputed_request,
    canonical_competitor_layout,
    canonical_mode,
    load_manifest,
    params_match,
    request_params_for_mode,
    resolve_precomputed_root,
    validate_mesh_patch_plant_receiver_payload,
    write_precomputed_plant_receiver_npz,
)
from rad_rebuild.radiance.engine.simulation.precomputed_integrity import (
    PrecomputedIntegrityError,
    load_json_object,
    resolve_artifact_map,
    verify_basis_hash,
)
from rad_rebuild.radiance.engine.simulation.basis_backends import (
    DEFAULT_SMD_BASIS_BACKEND,
    SUPPORTED_SMD_BASIS_BACKENDS,
    validate_basis_backend_request,
)
from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
    DEFAULT_IES_VARIANT as DEFAULT_HPS_IES_VARIANT,
    DEFAULT_MOUNT_Z_M as DEFAULT_HPS_MOUNT_Z_M,
    get_ies_comparator_profile,
    normalize_ies_variant as normalize_hps_ies_variant,
)
from rad_rebuild.radiance.backend.models import RadianceRunRequest

ROOT = RADIANCE_OUTPUT_ROOT
SCRIPT_ROOT = RADIANCE_SCRIPTS_ROOT

os.environ.setdefault("RADIANCE_ROOT", str(ROOT))
os.environ.setdefault("RADIANCE_OUTPUT_ROOT", str(ROOT))
os.environ.setdefault("RADIANCE_BASIS_OUTPUT_ROOT", str(RADIANCE_BASIS_OUTPUT_ROOT))
os.environ.setdefault("PYTHONPATH", str(REPO_ROOT / "src"))
os.environ.setdefault("RADIANCE_PY", sys.executable)
os.environ.setdefault("RADIANCE_USE_DOCKER", "0")

_LOCAL_RADIANCE_BIN = Path("/opt/radiance/bin")
_LOCAL_RADIANCE_LIB = Path("/opt/radiance/lib")
if _LOCAL_RADIANCE_BIN.is_dir():
    current_path = os.environ.get("PATH", "")
    path_parts = [p for p in current_path.split(os.pathsep) if p]
    if str(_LOCAL_RADIANCE_BIN) not in path_parts:
        os.environ["PATH"] = (
            f"{_LOCAL_RADIANCE_BIN}{os.pathsep}{current_path}"
            if current_path
            else str(_LOCAL_RADIANCE_BIN)
        )
if _LOCAL_RADIANCE_LIB.is_dir():
    current_raypath = os.environ.get("RAYPATH", "")
    ray_parts = [p for p in current_raypath.split(os.pathsep) if p]
    if str(_LOCAL_RADIANCE_LIB) not in ray_parts:
        suffix = current_raypath if current_raypath else "."
        os.environ["RAYPATH"] = f"{_LOCAL_RADIANCE_LIB}{os.pathsep}{suffix}"

JsonObject = dict[str, Any]
RoomDims = tuple[int, int]
PromotionState = Literal["prepared", "backup_preserved", "target_promoted"]
PromotionHook = Callable[[str], None]
HAVE_FLOCK = fcntl is not None


@dataclass(frozen=True)
class PrecomputeSweepConfig:
    length_min: int = PUBLIC_PRECOMPUTED_MIN_FT
    length_max: int = PUBLIC_PRECOMPUTED_MAX_FT
    width_min: int = PUBLIC_PRECOMPUTED_MIN_FT
    width_max: int = PUBLIC_PRECOMPUTED_MAX_FT
    step: int = 1
    square_only: bool = False
    modes: tuple[str, ...] = tuple(ACTIVE_RADIANCE_MODES)
    dataset_root: Path | None = None
    subpatch_grid: int = 1
    mount_z_m: float = 0.4572
    smd_base_ring: int = 0
    basis_backend: str = DEFAULT_SMD_BASIS_BACKEND
    match_system_ppe: bool = False
    sp_ppf: float = COMPETITOR_FIXTURE_PPF_UMOL_S
    sp_z_m: float = 0.4572
    sp_ppe: float = COMPETITOR_FIXTURE_PPE_UMOL_PER_J
    competitor_layouts: tuple[str, ...] = ("practical",)
    hps_coverages: tuple[float, ...] = (4.0,)
    hps_ies_variants: tuple[str, ...] = (DEFAULT_HPS_IES_VARIANT,)
    hps_z_m: float = DEFAULT_HPS_MOUNT_Z_M
    hps_fixture_ppf: float | None = None
    hps_input_watts: float | None = None
    dialux_sensor_grid: bool = False
    plants_enabled: bool = False
    fspm_receiver_granularity: str = PRECOMPUTED_FSPM_RECEIVER_GRANULARITY
    force: bool = False
    dry_run: bool = False

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "PrecomputeSweepConfig":
        dataset_root = (
            Path(args.dataset_root).resolve()
            if str(args.dataset_root or "").strip()
            else None
        )
        return cls(
            length_min=int(args.length_min),
            length_max=int(args.length_max),
            width_min=int(args.width_min),
            width_max=int(args.width_max),
            step=int(args.step),
            square_only=bool(args.square_only),
            modes=tuple(str(mode) for mode in args.modes),
            dataset_root=dataset_root,
            subpatch_grid=int(args.subpatch_grid),
            mount_z_m=float(args.mount_z_m),
            smd_base_ring=int(args.smd_base_ring),
            basis_backend=str(args.basis_backend),
            match_system_ppe=bool(args.match_system_ppe),
            sp_ppf=float(args.sp_ppf),
            sp_z_m=float(args.sp_z_m),
            sp_ppe=float(args.sp_ppe),
            competitor_layouts=tuple(str(layout) for layout in args.competitor_layouts),
            hps_coverages=tuple(float(coverage) for coverage in args.hps_coverages),
            hps_ies_variants=tuple(str(variant) for variant in args.hps_ies_variants),
            hps_z_m=float(args.hps_z_m),
            hps_fixture_ppf=args.hps_fixture_ppf,
            hps_input_watts=args.hps_input_watts,
            dialux_sensor_grid=bool(args.dialux_sensor_grid),
            plants_enabled=bool(args.plants_enabled),
            fspm_receiver_granularity=str(args.fspm_receiver_granularity),
            force=bool(args.force),
            dry_run=bool(args.dry_run),
        )

    def to_namespace(self) -> argparse.Namespace:
        return argparse.Namespace(
            length_min=self.length_min,
            length_max=self.length_max,
            width_min=self.width_min,
            width_max=self.width_max,
            step=self.step,
            square_only=self.square_only,
            modes=list(self.modes),
            dataset_root="" if self.dataset_root is None else str(self.dataset_root),
            subpatch_grid=self.subpatch_grid,
            mount_z_m=self.mount_z_m,
            smd_base_ring=self.smd_base_ring,
            basis_backend=self.basis_backend,
            match_system_ppe=self.match_system_ppe,
            sp_ppf=self.sp_ppf,
            sp_z_m=self.sp_z_m,
            sp_ppe=self.sp_ppe,
            competitor_layouts=list(self.competitor_layouts),
            hps_coverages=list(self.hps_coverages),
            hps_ies_variants=list(self.hps_ies_variants),
            hps_z_m=self.hps_z_m,
            hps_fixture_ppf=self.hps_fixture_ppf,
            hps_input_watts=self.hps_input_watts,
            dialux_sensor_grid=self.dialux_sensor_grid,
            plants_enabled=self.plants_enabled,
            fspm_receiver_granularity=self.fspm_receiver_granularity,
            force=self.force,
            dry_run=self.dry_run,
        )


@dataclass(frozen=True)
class PrecomputeJobSpec:
    label: str
    mode: str
    room: RoomDims
    target_path: Path | None
    competitor_layout: str | None = None
    hps_coverage_ft: float | None = None
    hps_ies_variant: str | None = None
    plants_enabled: bool = False
    plant_rows: int | None = None
    plant_columns: int | None = None
    plant_spacing_m: float | None = None


class PrecomputeModeJob(TypedDict):
    label: str
    mode: str
    competitor_layout: str | None
    hps_coverage_ft: float | None
    hps_ies_variant: str | None


@dataclass(frozen=True)
class PrecomputePlan:
    dataset_root: Path
    rooms: tuple[RoomDims, ...]
    jobs: tuple[PrecomputeModeJob, ...]
    items: tuple[PrecomputeJobSpec, ...]

    @property
    def bundle_count(self) -> int:
        return len(self.items)


@dataclass(frozen=True)
class BundlePromotionPaths:
    transaction_id: str
    target: Path
    candidate: Path
    backup: Path
    journal: Path
    lock: Path


def _env_spydr(req: RadianceRunRequest) -> dict[str, str]:
    from rad_rebuild.radiance.backend.env import _env_spydr as build_env

    return build_env(req)


def _env_hps(req: RadianceRunRequest) -> dict[str, str]:
    from rad_rebuild.radiance.backend.env import _env_hps as build_env

    return build_env(req)


def _env_smd(req: RadianceRunRequest) -> dict[str, str]:
    from rad_rebuild.radiance.backend.env import _env_smd as build_env

    return build_env(req)


def _use_docker() -> bool:
    from rad_rebuild.radiance.backend.runner import _use_docker as use_docker

    return use_docker()


def _docker_script_command(script_path: Path, env: dict[str, str]) -> list[str]:
    from rad_rebuild.radiance.backend import runner

    docker_bin = runner._resolve_docker()
    if not docker_bin:
        raise RuntimeError("Docker CLI not found. Install Docker Desktop.")
    try:
        docker_path = resolve_executable(docker_bin, env=runner._docker_cli_env())
    except ExecutableResolutionError as exc:
        raise RuntimeError(str(exc)) from exc
    volume = f"{runner._docker_volume_path(REPO_ROOT)}:{runner.CONTAINER_REPO_ROOT}"
    container_script = runner._containerize_text(str(script_path.resolve()))
    return [
        str(docker_path),
        "run",
        "--rm",
        *runner._docker_platform_args(),
        *runner._docker_user_args(),
        "-v",
        volume,
        "-w",
        runner.CONTAINER_ROOT,
        *runner._docker_env_args(env),
        runner.IMAGE_NAME,
        "/bin/bash",
        container_script,
    ]


def _local_script_command(script_path: Path, env: dict[str, str]) -> list[str]:
    if os.name == "nt":
        raise RuntimeError("Local radiance execution is not supported on Windows.")
    try:
        bash = resolve_executable("bash", env=env)
    except ExecutableResolutionError as exc:
        raise RuntimeError(str(exc)) from exc
    return [str(bash), str(script_path.resolve())]


def ensure_image() -> None:
    from rad_rebuild.radiance.backend.runner import ensure_image as ensure_docker_image

    ensure_docker_image()


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Generate resumable precomputed Radiance bundles."
    )
    ap.add_argument("--length-min", type=int, default=PUBLIC_PRECOMPUTED_MIN_FT)
    ap.add_argument("--length-max", type=int, default=PUBLIC_PRECOMPUTED_MAX_FT)
    ap.add_argument("--width-min", type=int, default=PUBLIC_PRECOMPUTED_MIN_FT)
    ap.add_argument("--width-max", type=int, default=PUBLIC_PRECOMPUTED_MAX_FT)
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument(
        "--square-only",
        action="store_true",
        default=False,
        help="Generate only NxN layouts instead of the full rectangular grid.",
    )
    ap.add_argument("--modes", nargs="+", default=list(ACTIVE_RADIANCE_MODES))
    ap.add_argument("--dataset-root", default="")
    ap.add_argument("--subpatch-grid", type=int, default=1)
    ap.add_argument("--mount-z-m", type=float, default=0.4572)
    ap.add_argument("--smd-base-ring", type=int, default=0)
    ap.add_argument(
        "--basis-backend",
        default=DEFAULT_SMD_BASIS_BACKEND,
        choices=list(SUPPORTED_SMD_BASIS_BACKENDS),
    )
    ap.add_argument("--match-system-ppe", action="store_true", default=False)
    ap.add_argument("--sp-ppf", type=float, default=COMPETITOR_FIXTURE_PPF_UMOL_S)
    ap.add_argument("--sp-z-m", type=float, default=0.4572)
    ap.add_argument("--sp-ppe", type=float, default=COMPETITOR_FIXTURE_PPE_UMOL_PER_J)
    ap.add_argument("--competitor-layouts", nargs="+", default=["practical"])
    ap.add_argument("--hps-coverages", nargs="+", type=float, default=[4.0])
    ap.add_argument("--hps-ies-variants", nargs="+", default=[DEFAULT_HPS_IES_VARIANT])
    ap.add_argument("--hps-z-m", type=float, default=DEFAULT_HPS_MOUNT_Z_M)
    ap.add_argument("--hps-fixture-ppf", type=float, default=None)
    ap.add_argument("--hps-input-watts", type=float, default=None)
    ap.add_argument("--dialux-sensor-grid", action="store_true", default=False)
    ap.add_argument(
        "--plants-enabled",
        action="store_true",
        default=False,
        help=(
            "Plan future plant-enabled precomputed bundles using the canonical "
            "FSPM plant contract."
        ),
    )
    ap.add_argument(
        "--fspm-receiver-granularity",
        default=PRECOMPUTED_FSPM_RECEIVER_GRANULARITY,
        choices=list(SUPPORTED_PRECOMPUTED_FSPM_RECEIVER_GRANULARITIES),
        help="Plant receiver granularity for plant-enabled precomputed bundles.",
    )
    ap.add_argument("--force", action="store_true", default=False)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the generation plan without running Radiance or writing bundles.",
    )
    return ap.parse_args()


def _hps_power_defaults(variant: str, args: argparse.Namespace) -> tuple[float, float]:
    if args.hps_fixture_ppf is not None and args.hps_input_watts is not None:
        return float(args.hps_fixture_ppf), float(args.hps_input_watts)
    clean_variant = normalize_hps_ies_variant(variant)
    profile = get_ies_comparator_profile(clean_variant)
    fixture_ppf = profile.nominal_fixture_ppf_umol_s
    input_watts = profile.nominal_input_watts
    if args.hps_fixture_ppf is not None:
        fixture_ppf = float(args.hps_fixture_ppf)
    if args.hps_input_watts is not None:
        input_watts = float(args.hps_input_watts)
    return fixture_ppf, input_watts


def _req_for(
    mode: str,
    length_ft: int,
    width_ft: int,
    args: argparse.Namespace,
    hps_coverage_ft: float | None = None,
    hps_ies_variant: str | None = None,
    competitor_layout: str | None = None,
) -> RadianceRunRequest:
    canonical = canonical_mode(mode)
    basis_backend = validate_basis_backend_request(
        canonical, args.basis_backend, variable_mode="rings"
    )
    hps_variant = normalize_hps_ies_variant(hps_ies_variant)
    hps_fixture_ppf, hps_input_watts = _hps_power_defaults(hps_variant, args)
    req = RadianceRunRequest(
        action="competitor" if canonical == MODE_COMPETITOR else "uniformity",
        mode=canonical,
        length_ft=float(length_ft),
        width_ft=float(width_ft),
        target_ppfd=1000.0,
        run_basis=True,
        w_min=0.0,
        w_max=100.0,
        sim_mode="standard",
        subpatch_grid=args.subpatch_grid,
        mount_z_m=args.mount_z_m,
        overlay="auto",
        smd_base_ring=args.smd_base_ring,
        basis_backend=basis_backend,
        match_system_ppe=args.match_system_ppe,
        sp_ppf=args.sp_ppf,
        sp_z_m=args.sp_z_m,
        sp_ppe=args.sp_ppe,
        competitor_layout=str(competitor_layout or "full"),
        hps_coverage_ft=float(hps_coverage_ft if hps_coverage_ft is not None else 4.0),
        hps_z_m=args.hps_z_m,
        hps_fixture_ppf=hps_fixture_ppf,
        hps_input_watts=hps_input_watts,
        hps_ies_variant=hps_variant,
        dialux_sensor_grid=args.dialux_sensor_grid,
    )
    if bool(getattr(args, "plants_enabled", False)):
        req = canonical_plant_enabled_precomputed_request(
            req,
            receiver_granularity=str(getattr(args, "fspm_receiver_granularity", "")),
        )
    return req


def _run_script(script_path: Path, env: dict[str, str]) -> None:
    cmd = (
        _docker_script_command(script_path, env)
        if _use_docker()
        else _local_script_command(script_path, env)
    )
    subprocess.check_call(  # nosec B603
        cmd, cwd=ROOT, env=env
    )


def _with_active_python(env: Mapping[str, str]) -> dict[str, str]:
    tuned = dict(env)
    python = sys.executable
    tuned["PY"] = python
    tuned["PYTHON"] = python
    tuned["PYTHON_BIN"] = python
    tuned["PYTHON_CMD"] = python
    tuned["RADIANCE_PY"] = python
    tuned["FSPM_PRECOMPUTED_SCALAR_ONLY"] = "1"
    return tuned


def _active_python_path() -> Path:
    python = Path(sys.executable).expanduser()
    if not python.is_absolute():
        python = Path.cwd() / python
    if not python.is_file():
        raise RuntimeError(f"Active Python interpreter not found: {python}")
    if not os.access(python, os.X_OK):
        raise RuntimeError(f"Active Python interpreter is not executable: {python}")
    return python


def _parse_kv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or "=" not in s:
            continue
        key, value = s.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _gzip_copy(src: Path, dst: Path) -> None:
    with src.open("rb") as in_handle, gzip.open(dst, "wb") as out_handle:
        shutil.copyfileobj(in_handle, out_handle)


def _bundle_manifest_base(mode: str, req: RadianceRunRequest) -> JsonObject:
    canonical = canonical_mode(mode)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": canonical,
        "dims_ft": {
            "length_ft": int(max(req.length_ft, req.width_ft)),
            "width_ft": int(min(req.length_ft, req.width_ft)),
        },
        "request_params": request_params_for_mode(req),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _fast_tmp_parent() -> Path:
    override = os.getenv("RADIANCE_FAST_TMP_BASE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(tempfile.gettempdir())


def _with_fast_tmp(
    env: dict[str, str], mode: str, slug: str
) -> tuple[dict[str, str], Path | None]:
    if _use_docker():
        return env, None

    fast_base = _fast_tmp_parent()
    fast_base.mkdir(parents=True, exist_ok=True)
    run_root = Path(
        tempfile.mkdtemp(
            prefix=f"radiance-sweep-{str(mode).lower()}-{slug}-", dir=fast_base
        )
    )
    rad_tmp = run_root / "radtmp"
    tmpdir = run_root / "tmp"
    mplconfig = run_root / "mplconfig"
    for path in (rad_tmp, tmpdir, mplconfig):
        path.mkdir(parents=True, exist_ok=True)

    tuned = env.copy()
    tuned["RAD_TMP"] = str(rad_tmp)
    tuned["TMPDIR"] = str(tmpdir)
    tuned["TMP"] = str(tmpdir)
    tuned["TEMP"] = str(tmpdir)
    tuned["MPLCONFIGDIR"] = str(mplconfig)
    tuned["RADIANCE_USE_DOCKER"] = "0"
    return tuned, run_root


def _cleanup_fast_tmp(path: Path | None) -> None:
    if path is None:
        return
    if os.getenv("RADIANCE_KEEP_FAST_TMP", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    shutil.rmtree(path, ignore_errors=True)


def smd_bundle_is_sane(bundle_dir: Path, manifest: JsonObject) -> bool:
    artifact_paths = _smd_bundle_artifact_paths(bundle_dir, manifest)
    if artifact_paths is None:
        return False
    basis_path, meta_path = artifact_paths
    basis = np.load(basis_path)
    basis_meta = json.loads(meta_path.read_text())
    if not _smd_basis_matrix_is_sane(basis):
        return False
    layout_modules = int(basis_meta.get("layout_modules", 0) or 0)
    unit_w = float(basis_meta.get("basis_unit_w_per_module", 1.0) or 1.0)
    area_m2 = _smd_bundle_area_m2(manifest)
    if layout_modules <= 0 or area_m2 <= 0:
        return False
    return _smd_basis_mean_within_physical_bound(
        basis,
        layout_modules=layout_modules,
        unit_w=unit_w,
        area_m2=area_m2,
    )


def _smd_bundle_artifact_paths(
    bundle_dir: Path, manifest: JsonObject
) -> tuple[Path, Path] | None:
    try:
        artifacts = resolve_artifact_map(bundle_dir, manifest)
        verify_basis_hash(manifest, artifacts)
    except (OSError, ValueError, PrecomputedIntegrityError):
        return None
    basis_path = artifacts.get("basis_A_npy")
    meta_path = artifacts.get("basis_manifest_json")
    if basis_path is None or meta_path is None:
        return None
    return basis_path, meta_path


def _smd_basis_matrix_is_sane(basis: np.ndarray[Any, Any]) -> bool:
    if basis.ndim != 2 or basis.size == 0:
        return False
    if not np.all(np.isfinite(basis)):
        return False
    col_max = np.max(np.abs(basis), axis=0)
    col_mean = np.mean(np.abs(basis), axis=0)
    # Dead basis columns indicate an invalid variable mapping and make the bundle unusable.
    if np.any(col_max <= 1e-9) or np.any(col_mean <= 1e-12):
        return False
    return True


def _smd_bundle_area_m2(manifest: JsonObject) -> float:
    dims = manifest.get("dims_ft") or {}
    length_ft = float(dims.get("length_ft", 0) or 0.0)
    width_ft = float(dims.get("width_ft", 0) or 0.0)
    return (
        (length_ft * width_ft) * 0.09290304 if length_ft > 0 and width_ft > 0 else 0.0
    )


def _smd_basis_mean_within_physical_bound(
    basis: np.ndarray[Any, Any],
    *,
    layout_modules: int,
    unit_w: float,
    area_m2: float,
) -> bool:
    total_mean = float(basis.mean(axis=0).sum())
    # Very generous physical upper bound at 1 W/module. If the saved basis exceeds this,
    # the basis columns are almost certainly contaminated by non-isolated ring power.
    generous_umol_per_j = 10.0
    max_mean_if_all_captured = generous_umol_per_j * unit_w * layout_modules / area_m2
    return total_mean <= (max_mean_if_all_captured * 2.0)


def _smd_bundle_is_sane(bundle_dir: Path, manifest: JsonObject) -> bool:
    return smd_bundle_is_sane(bundle_dir, manifest)


def _generate_competitor_bundle(
    bundle_dir: Path, req: RadianceRunRequest, env: dict[str, str]
) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    env = _with_active_python(env)
    env["AUTO_DIM"] = "0"
    _run_script(SCRIPT_ROOT / "run_simulation_spydr3.sh", env)

    ppfd_map = ROOT / "ppfd_map.txt"
    data = np.loadtxt(ppfd_map)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    mean_ppfd = float(np.mean(data[:, 3]))
    peak_ppfd = float(np.max(data[:, 3]))

    power_meta = _parse_kv_file(ROOT / "runtime_state" / "spydr3_power.txt")
    power_json = {
        "model_label": str(power_meta.get("model_label", "") or ""),
        "ppe_effective": float(power_meta.get("ppe_effective", "0") or 0.0),
        "ppe_full": float(power_meta.get("ppe_full", "0") or 0.0),
        "ppe_low": float(power_meta.get("ppe_low", "0") or 0.0),
        "w_full": float(power_meta.get("w_full", "0") or 0.0),
        "w_low": float(power_meta.get("w_low", "0") or 0.0),
        "droop_k": float(power_meta.get("droop_k", "0") or 0.0),
        "eff_scale": float(power_meta.get("eff_scale", "1") or 1.0),
        "total_ppf": float(power_meta.get("total_ppf", "0") or 0.0),
        "total_w": float(power_meta.get("total_w", "0") or 0.0),
        "fixture_input_w": float(power_meta.get("fixture_input_w", "0") or 0.0),
        "layout_mode": str(power_meta.get("layout_mode", "") or ""),
        "fixture_count": int(float(power_meta.get("fixture_count", "0") or 0.0)),
        "droop_enabled": power_meta.get("droop_enabled", "0")
        not in {"0", "false", "False", ""},
    }

    _gzip_copy(ppfd_map, bundle_dir / "ppfd_map.txt.gz")
    shutil.copyfile(
        ROOT / "runtime_state" / "spydr3_layout.json", bundle_dir / "spydr3_layout.json"
    )
    (bundle_dir / "power.json").write_text(
        json.dumps(power_json, indent=2, sort_keys=True)
    )

    manifest = _bundle_manifest_base(MODE_COMPETITOR, req)
    artifacts = {
        "ppfd_map_txt_gz": "ppfd_map.txt.gz",
        "layout_json": "spydr3_layout.json",
        "power_json": "power.json",
    }
    if bool(getattr(req, "plants_enabled", False)):
        _copy_plant_receiver_npz_artifact(bundle_dir, artifacts)
    manifest.update(
        {
            "artifacts": artifacts,
            "stats": {
                "base_mean_ppfd": mean_ppfd,
                "base_peak_ppfd": peak_ppfd,
                "point_count": int(data.shape[0]),
            },
        }
    )
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True)
    )


def _generate_hps_bundle(
    bundle_dir: Path, req: RadianceRunRequest, env: dict[str, str]
) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    env = _with_active_python(env)
    env["AUTO_DIM"] = "0"
    _run_script(SCRIPT_ROOT / "run_simulation_hps.sh", env)

    ppfd_map = ROOT / "ppfd_map.txt"
    data = np.loadtxt(ppfd_map)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    mean_ppfd = float(np.mean(data[:, 3]))

    power_meta = _parse_kv_file(ROOT / "runtime_state" / "hps_power.txt")
    power_json = _hps_power_payload(power_meta)

    _gzip_copy(ppfd_map, bundle_dir / "ppfd_map.txt.gz")
    shutil.copyfile(
        ROOT / "runtime_state" / "hps_layout.json", bundle_dir / "hps_layout.json"
    )
    (bundle_dir / "power.json").write_text(
        json.dumps(power_json, indent=2, sort_keys=True)
    )

    manifest = _bundle_manifest_base(MODE_HPS, req)
    artifacts = {
        "ppfd_map_txt_gz": "ppfd_map.txt.gz",
        "layout_json": "hps_layout.json",
        "power_json": "power.json",
    }
    if bool(getattr(req, "plants_enabled", False)):
        _copy_plant_receiver_npz_artifact(bundle_dir, artifacts)
    manifest.update(
        {
            "artifacts": artifacts,
            "stats": {
                "base_mean_ppfd": mean_ppfd,
                "point_count": int(data.shape[0]),
            },
        }
    )
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True)
    )


def _kv_text(meta: Mapping[str, str], key: str) -> str:
    return str(meta.get(key, "") or "")


def _kv_float(meta: Mapping[str, str], key: str, default: str = "0") -> float:
    return float(meta.get(key, default) or float(default))


def _kv_int_from_float(meta: Mapping[str, str], key: str) -> int:
    return int(float(meta.get(key, "0") or 0.0))


def _hps_power_payload(power_meta: Mapping[str, str]) -> JsonObject:
    return {
        "profile": _kv_text(power_meta, "profile"),
        "model_label": _kv_text(power_meta, "model_label"),
        "model_mode": _kv_text(power_meta, "model_mode"),
        "archetype": _kv_text(power_meta, "archetype"),
        "coverage_ft": _kv_float(power_meta, "coverage_ft"),
        "eff_scale": _kv_float(power_meta, "eff_scale", "1"),
        "fixture_ppf": _kv_float(power_meta, "fixture_ppf"),
        "fixture_input_w": _kv_float(power_meta, "fixture_input_w"),
        "fixture_ppe": _kv_float(power_meta, "fixture_ppe"),
        "total_ppf": _kv_float(power_meta, "total_ppf"),
        "total_w": _kv_float(power_meta, "total_w"),
        "fixture_count": _kv_int_from_float(power_meta, "fixture_count"),
        "ies_lumens_per_lamp_lm": _kv_float(power_meta, "ies_lumens_per_lamp_lm"),
        "ies_total_luminaire_lumens_lm": _kv_float(
            power_meta, "ies_total_luminaire_lumens_lm"
        ),
        "ies_input_watts": _kv_float(power_meta, "ies_input_watts"),
        "spd_umol_per_lumen": _kv_float(power_meta, "spd_umol_per_lumen"),
        "pre_normalization_fixture_ppf_umol_s": _kv_float(
            power_meta, "pre_normalization_fixture_ppf_umol_s"
        ),
        "default_fixture_anchor_umol_s": _kv_float(
            power_meta, "default_fixture_anchor_umol_s"
        ),
        "fixture_anchor_authority": _kv_text(power_meta, "fixture_anchor_authority"),
        "final_scale_multiplier": _kv_float(power_meta, "final_scale_multiplier"),
        "effective_aperture_length_m": _kv_float(
            power_meta, "effective_aperture_length_m"
        ),
        "effective_aperture_width_m": _kv_float(
            power_meta, "effective_aperture_width_m"
        ),
        "effective_aperture_z_offset_m": _kv_float(
            power_meta, "effective_aperture_z_offset_m"
        ),
        "anchor_notes": _kv_text(power_meta, "anchor_notes"),
    }


def _copy_plant_receiver_npz_artifact(
    bundle_dir: Path,
    artifacts: dict[str, str],
    *,
    source_dir: Path | None = None,
) -> None:
    source_root = ROOT / "runtime_state" if source_dir is None else source_dir
    source = source_root / PRECOMPUTED_PLANT_RECEIVER_JSON_FILENAME
    if not source.is_file():
        raise RuntimeError(
            "Plant-enabled precomputed generation did not produce "
            f"{PRECOMPUTED_PLANT_RECEIVER_JSON_FILENAME}."
        )
    payload = load_json_object(source)
    mesh_patch_errors = validate_mesh_patch_plant_receiver_payload(payload)
    if mesh_patch_errors:
        raise RuntimeError(
            "Invalid mesh_patch plant receiver precomputed artifact: "
            + "; ".join(mesh_patch_errors)
        )
    write_precomputed_plant_receiver_npz(
        bundle_dir / PRECOMPUTED_PLANT_RECEIVER_NPZ_FILENAME,
        payload,
    )
    artifacts[PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY] = (
        PRECOMPUTED_PLANT_RECEIVER_NPZ_FILENAME
    )


def _copy_smd_plant_receiver_artifacts(
    bundle_dir: Path,
    artifacts: dict[str, str],
    *,
    basis: np.ndarray[Any, Any],
) -> None:
    receiver_json = RADIANCE_BASIS_OUTPUT_ROOT / PRECOMPUTED_PLANT_RECEIVER_JSON_FILENAME
    receiver_basis = RADIANCE_BASIS_OUTPUT_ROOT / PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_FILENAME
    if not receiver_json.is_file() or not receiver_basis.is_file():
        raise RuntimeError(
            "Plant-enabled SMD precomputed generation did not produce compact "
            "plant receiver basis artifacts."
        )
    plant_basis = np.load(receiver_basis)
    if plant_basis.ndim != 2:
        raise RuntimeError("plant_receiver_basis_A.npy must be a 2D matrix.")
    if plant_basis.shape[1] != basis.shape[1]:
        raise RuntimeError(
            "plant_receiver_basis_A columns must match canopy basis_A columns."
        )
    receiver_payload = load_json_object(receiver_json)
    basis_manifest = load_json_object(RADIANCE_BASIS_OUTPUT_ROOT / "basis_manifest.json")
    mesh_patch_errors = validate_mesh_patch_plant_receiver_payload(
        receiver_payload,
        basis_row_count=int(plant_basis.shape[0]),
    )
    if mesh_patch_errors:
        raise RuntimeError(
            "Invalid mesh_patch plant receiver precomputed artifacts: "
            + "; ".join(mesh_patch_errors)
        )
    receiver_basis_meta = receiver_payload.get("basis_metadata")
    if not isinstance(receiver_basis_meta, Mapping):
        raise RuntimeError("plant_receiver.json is missing SMD basis_metadata.")
    for key in (
        "variables",
        "ring_indices",
        "module_indices",
        "outer_ring_index",
        "outer_ring_indices",
        "variable_groups",
        "basis_matrix_sha256",
    ):
        if key in receiver_basis_meta or key in basis_manifest:
            if receiver_basis_meta.get(key) != basis_manifest.get(key):
                raise RuntimeError(
                    f"plant receiver basis metadata mismatch for {key!r}."
                )
    write_precomputed_plant_receiver_npz(
        bundle_dir / PRECOMPUTED_PLANT_RECEIVER_NPZ_FILENAME,
        receiver_payload,
    )
    np.savez_compressed(
        bundle_dir / PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_FILENAME,
        plant_receiver_basis_A=plant_basis,
    )
    artifacts[PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY] = (
        PRECOMPUTED_PLANT_RECEIVER_NPZ_FILENAME
    )
    artifacts[PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_ARTIFACT_KEY] = (
        PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_FILENAME
    )


def _generate_smd_bundle(
    bundle_dir: Path, req: RadianceRunRequest, env: dict[str, str]
) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    env = _with_active_python(env)
    basis_env = dict(env)
    if bool(getattr(req, "plants_enabled", False)):
        basis_env["FSPM_PRECOMPUTE_PLANT_RECEIVER_BASIS"] = "1"
    _run_script(SCRIPT_ROOT / "run_basis_extraction.sh", basis_env)

    py = _active_python_path()
    subprocess.check_call(  # nosec B603
        [str(py), "-m", "rad_rebuild.radiance.engine.emitters.generate_emitters_smd"],
        cwd=ROOT,
        env=env,
    )

    basis_path = RADIANCE_BASIS_OUTPUT_ROOT / "basis_A.npy"
    basis_manifest = RADIANCE_BASIS_OUTPUT_ROOT / "basis_manifest.json"
    basis_build_log = RADIANCE_BASIS_OUTPUT_ROOT / "basis_build_log.json"
    basis_backend_log = (
        RADIANCE_BASIS_OUTPUT_ROOT / "basis_runs" / "basis_backend_log.json"
    )
    basis = np.load(basis_path)

    shutil.copyfile(basis_path, bundle_dir / "basis_A.npy")
    shutil.copyfile(basis_manifest, bundle_dir / "basis_manifest.json")
    shutil.copyfile(
        ROOT / "runtime_state" / "smd_layout.json", bundle_dir / "smd_layout.json"
    )
    artifacts: dict[str, str] = {
        "basis_A_npy": "basis_A.npy",
        "basis_manifest_json": "basis_manifest.json",
        "layout_json": "smd_layout.json",
    }
    if basis_build_log.exists():
        shutil.copyfile(basis_build_log, bundle_dir / "basis_build_log.json")
        artifacts["basis_build_log_json"] = "basis_build_log.json"
    if basis_backend_log.exists():
        shutil.copyfile(basis_backend_log, bundle_dir / "basis_backend_log.json")
        artifacts["basis_backend_log_json"] = "basis_backend_log.json"
    if bool(getattr(req, "plants_enabled", False)):
        _copy_smd_plant_receiver_artifacts(bundle_dir, artifacts, basis=basis)

    manifest = _bundle_manifest_base(MODE_SMD, req)
    manifest.update(
        {
            "artifacts": artifacts,
            "stats": {
                "n_points": int(basis.shape[0]),
                "n_vars": int(basis.shape[1]),
            },
        }
    )
    if not _smd_bundle_is_sane(bundle_dir, manifest):
        raise RuntimeError(
            f"Generated SMD bundle for {bundle_dir.name} failed basis sanity validation; refusing to save corrupt data."
        )
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True)
    )


def _should_skip(ref: BundleRef, req: RadianceRunRequest, force: bool) -> bool:
    if force or not bundle_complete(ref):
        return False
    manifest = load_manifest(ref)
    if not manifest:
        return False
    if str(manifest.get("mode", "")).lower() == "smd" and not _smd_bundle_is_sane(
        ref.path, manifest
    ):
        print(f"Cached SMD bundle failed sanity validation; regenerating {ref.slug}.")
        return False
    return params_match(manifest, request_params_for_mode(req))


def _sweep_dims(args: argparse.Namespace) -> list[tuple[int, int]]:
    dims: list[tuple[int, int]] = []
    for length_ft in range(args.length_min, args.length_max + 1, args.step):
        width_start = args.width_min
        width_stop = min(args.width_max, length_ft)
        if args.square_only:
            if length_ft < width_start or length_ft > width_stop:
                continue
            dims.append((length_ft, length_ft))
            continue
        for width_ft in range(width_start, width_stop + 1, args.step):
            dims.append((length_ft, width_ft))
    return dims


def _job_specs(args: argparse.Namespace) -> list[PrecomputeModeJob]:
    jobs: list[PrecomputeModeJob] = []
    seen_hps: set[tuple[float, str]] = set()
    for raw_mode in args.modes:
        mode = canonical_mode(raw_mode)
        if mode == MODE_COMPETITOR:
            for raw_layout in args.competitor_layouts:
                layout = canonical_competitor_layout(raw_layout)
                label = (
                    "Competitor Practical Coverage"
                    if layout == "practical"
                    else MODE_COMPETITOR
                )
                jobs.append(
                    {
                        "label": label,
                        "mode": mode,
                        "competitor_layout": layout,
                        "hps_coverage_ft": None,
                        "hps_ies_variant": None,
                    }
                )
            continue
        if mode == MODE_HPS:
            for coverage in args.hps_coverages:
                cov = float(coverage)
                for raw_variant in args.hps_ies_variants:
                    variant = normalize_hps_ies_variant(
                        str(raw_variant or DEFAULT_HPS_IES_VARIANT)
                    )
                    key = (cov, variant)
                    if key in seen_hps:
                        continue
                    seen_hps.add(key)
                    label = f"1000W HPS {variant} {cov:g}x{cov:g}"
                    jobs.append(
                        {
                            "label": label,
                            "mode": mode,
                            "competitor_layout": None,
                            "hps_coverage_ft": cov,
                            "hps_ies_variant": variant,
                        }
                    )
        else:
            jobs.append(
                {
                    "label": mode,
                    "mode": mode,
                    "competitor_layout": None,
                    "hps_coverage_ft": None,
                    "hps_ies_variant": None,
                }
            )
    return jobs


def plan_precompute_sweep(config: PrecomputeSweepConfig) -> PrecomputePlan:
    args = config.to_namespace()
    dataset_root = (
        config.dataset_root
        if config.dataset_root is not None
        else resolve_precomputed_root(ROOT)
    )
    rooms = tuple(_sweep_dims(args))
    jobs = tuple(_job_specs(args))
    items: list[PrecomputeJobSpec] = []
    for length_ft, width_ft in rooms:
        for job in jobs:
            req = _req_for(
                str(job["mode"]),
                length_ft,
                width_ft,
                args,
                hps_coverage_ft=job["hps_coverage_ft"],
                hps_ies_variant=job["hps_ies_variant"],
                competitor_layout=job["competitor_layout"],
            )
            ref = bundle_ref(ROOT, req.mode, length_ft, width_ft, dataset_root, req=req)
            items.append(
                PrecomputeJobSpec(
                    label=str(job["label"]),
                    mode=req.mode,
                    room=(int(length_ft), int(width_ft)),
                    target_path=None if ref is None else ref.path,
                    competitor_layout=None
                    if job["competitor_layout"] is None
                    else str(job["competitor_layout"]),
                    hps_coverage_ft=None
                    if job["hps_coverage_ft"] is None
                    else float(job["hps_coverage_ft"]),
                    hps_ies_variant=None
                    if job["hps_ies_variant"] is None
                    else str(job["hps_ies_variant"]),
                    plants_enabled=bool(req.plants_enabled),
                    plant_rows=req.plant_rows,
                    plant_columns=req.plant_columns,
                    plant_spacing_m=req.plant_spacing_m,
                )
            )
    return PrecomputePlan(
        dataset_root=dataset_root, rooms=rooms, jobs=jobs, items=tuple(items)
    )


def _print_precompute_plan(plan: PrecomputePlan) -> None:
    print(f"Precomputed dataset root: {plan.dataset_root}")
    print(f"Planned room sizes: {len(plan.rooms)}")
    print(f"Planned mode/layout variants: {len(plan.jobs)}")
    print(f"Planned bundles: {plan.bundle_count}")


def _print_precompute_dry_run(plan: PrecomputePlan) -> None:
    for item in plan.items:
        target = (
            str(item.target_path)
            if item.target_path is not None
            else "unsupported non-integer dimensions"
        )
        plant_suffix = ""
        if item.plants_enabled:
            plant_suffix = (
                f" plants={item.plant_rows}x{item.plant_columns}"
                f" spacing={float(item.plant_spacing_m or 0.0):g}m"
            )
        print(
            f"DRY-RUN {item.label} {item.room[0]}x{item.room[1]}"
            f"{plant_suffix} -> {target}"
        )


def _new_bundle_transaction_id() -> str:
    return uuid.uuid4().hex


def _bundle_promotion_paths(
    target_dir: Path, transaction_id: str | None = None
) -> BundlePromotionPaths:
    tx_id = transaction_id or _new_bundle_transaction_id()
    parent = target_dir.parent
    slug = target_dir.name
    return BundlePromotionPaths(
        transaction_id=tx_id,
        target=target_dir,
        candidate=parent / f".{slug}.{tx_id}.candidate",
        backup=parent / f".{slug}.{tx_id}.backup",
        journal=parent / f".{slug}.promotion.json",
        lock=parent / f".{slug}.lock",
    )


@contextmanager
def _bundle_promotion_lock(target_dir: Path) -> Iterator[None]:
    paths = _bundle_promotion_paths(target_dir, "lock")
    paths.lock.parent.mkdir(parents=True, exist_ok=True)
    with paths.lock.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _bundle_dir_is_valid(bundle_dir: Path) -> bool:
    try:
        manifest = load_json_object(bundle_dir / "manifest.json")
        if int(manifest.get("schema_version", 0) or 0) != SCHEMA_VERSION:
            return False
        artifacts = resolve_artifact_map(bundle_dir, manifest)
        verify_basis_hash(manifest, artifacts)
        if str(manifest.get("mode", "")).lower() == "smd":
            return smd_bundle_is_sane(bundle_dir, manifest)
    except (OSError, ValueError, PrecomputedIntegrityError, json.JSONDecodeError):
        return False
    return True


def _write_bundle_promotion_journal(
    paths: BundlePromotionPaths, state: PromotionState
) -> None:
    paths.journal.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "transaction_id": paths.transaction_id,
        "state": state,
        "candidate": str(paths.candidate),
        "backup": str(paths.backup),
        "target": str(paths.target),
    }
    paths.journal.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _fsync_dir(paths.journal.parent)


def _read_bundle_promotion_journal(target_dir: Path) -> BundlePromotionPaths | None:
    journal = _bundle_promotion_paths(target_dir, "journal").journal
    if not journal.exists():
        return None
    data = load_json_object(journal)
    tx_id = str(data.get("transaction_id", "") or "")
    if not tx_id:
        raise RuntimeError(f"Invalid bundle promotion journal at {journal}")
    paths = BundlePromotionPaths(
        transaction_id=tx_id,
        target=Path(str(data.get("target", ""))),
        candidate=Path(str(data.get("candidate", ""))),
        backup=Path(str(data.get("backup", ""))),
        journal=journal,
        lock=_bundle_promotion_paths(target_dir, tx_id).lock,
    )
    if paths.target != target_dir:
        raise RuntimeError(f"Bundle promotion journal target mismatch at {journal}")
    return paths


def _journal_state(paths: BundlePromotionPaths) -> PromotionState:
    data = load_json_object(paths.journal)
    state = data.get("state")
    if state == "prepared":
        return "prepared"
    if state == "backup_preserved":
        return "backup_preserved"
    if state == "target_promoted":
        return "target_promoted"
    raise RuntimeError(f"Invalid bundle promotion journal state at {paths.journal}")


def _remove_tree_if_exists(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _recover_journaled_bundle_replacement(target_dir: Path) -> None:
    paths = _read_bundle_promotion_journal(target_dir)
    if paths is None:
        return
    state = _journal_state(paths)
    target_valid = paths.target.exists() and _bundle_dir_is_valid(paths.target)
    backup_valid = paths.backup.exists() and _bundle_dir_is_valid(paths.backup)
    candidate_exists = paths.candidate.exists()

    if target_valid and state == "target_promoted":
        _remove_tree_if_exists(paths.backup)
        paths.journal.unlink(missing_ok=True)
        _fsync_dir(paths.target.parent)
        return
    if target_valid and not candidate_exists:
        _remove_tree_if_exists(paths.backup)
        paths.journal.unlink(missing_ok=True)
        _fsync_dir(paths.target.parent)
        return
    if backup_valid and (not target_valid or state in {"prepared", "backup_preserved"}):
        if paths.target.exists():
            shutil.rmtree(paths.target)
        paths.backup.rename(paths.target)
        paths.journal.unlink(missing_ok=True)
        _fsync_dir(paths.target.parent)
        return
    if target_valid:
        paths.journal.unlink(missing_ok=True)
        _fsync_dir(paths.target.parent)
        return
    raise RuntimeError(
        f"Cannot recover precomputed bundle promotion for {target_dir}; no valid target or backup remains."
    )


def _base_env_for_mode(req: RadianceRunRequest, mode: str) -> dict[str, str]:
    if mode == MODE_COMPETITOR:
        return _env_spydr(req)
    if mode == MODE_HPS:
        return _env_hps(req)
    return _env_smd(req)


def _generate_bundle_for_mode(
    mode: str, tmp_dir: Path, req: RadianceRunRequest, env: dict[str, str]
) -> None:
    if mode == MODE_COMPETITOR:
        _generate_competitor_bundle(tmp_dir, req, env)
    elif mode == MODE_HPS:
        _generate_hps_bundle(tmp_dir, req, env)
    else:
        _generate_smd_bundle(tmp_dir, req, env)


def _replace_bundle_dir(
    candidate_dir: Path,
    target_dir: Path,
    *,
    transaction_id: str | None = None,
    hook: PromotionHook | None = None,
) -> None:
    paths = _bundle_promotion_paths(target_dir, transaction_id)
    if candidate_dir != paths.candidate:
        paths = BundlePromotionPaths(
            transaction_id=paths.transaction_id,
            target=paths.target,
            candidate=candidate_dir,
            backup=paths.backup,
            journal=paths.journal,
            lock=paths.lock,
        )
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    with _bundle_promotion_lock(target_dir):
        _recover_journaled_bundle_replacement(target_dir)
        if not _bundle_dir_is_valid(candidate_dir):
            raise RuntimeError(
                f"Refusing to promote invalid precomputed bundle {candidate_dir}"
            )
        if hook is not None:
            hook("candidate_validated")
        _write_bundle_promotion_journal(paths, "prepared")
        if hook is not None:
            hook("prepared")
        if paths.target.exists():
            paths.target.rename(paths.backup)
            _write_bundle_promotion_journal(paths, "backup_preserved")
            _fsync_dir(paths.target.parent)
            if hook is not None:
                hook("backup_preserved")
        paths.candidate.rename(paths.target)
        _write_bundle_promotion_journal(paths, "target_promoted")
        _fsync_dir(paths.target.parent)
        if hook is not None:
            hook("target_promoted")
        if not _bundle_dir_is_valid(paths.target):
            if paths.backup.exists() and _bundle_dir_is_valid(paths.backup):
                shutil.rmtree(paths.target)
                paths.backup.rename(paths.target)
                paths.journal.unlink(missing_ok=True)
                _fsync_dir(paths.target.parent)
            raise RuntimeError(f"Promoted precomputed bundle is invalid: {paths.target}")
        if hook is not None:
            hook("promoted_validated")
        _remove_tree_if_exists(paths.backup)
        paths.journal.unlink(missing_ok=True)
        _fsync_dir(paths.target.parent)


def _run_precompute_job(
    *,
    args: argparse.Namespace,
    dataset_root: Path,
    total: int,
    index: int,
    room: tuple[int, int],
    job: PrecomputeModeJob,
) -> None:
    length_ft, width_ft = room
    req = _req_for(
        str(job["mode"]),
        length_ft,
        width_ft,
        args,
        hps_coverage_ft=job["hps_coverage_ft"],
        hps_ies_variant=job["hps_ies_variant"],
        competitor_layout=job["competitor_layout"],
    )
    ref = bundle_ref(ROOT, req.mode, length_ft, width_ft, dataset_root, req=req)
    if ref is None:
        print(f"[{index}/{total}] Skipping unsupported size {length_ft}x{width_ft}")
        return
    if _should_skip(ref, req, args.force):
        print(
            f"[{index}/{total}] Skipping {job['label']} {ref.slug}; bundle already present"
        )
        return
    _materialize_precompute_bundle(args, ref, req, str(job["label"]), index, total)


def _materialize_precompute_bundle(
    args: argparse.Namespace,
    ref: BundleRef,
    req: RadianceRunRequest,
    label: str,
    index: int,
    total: int,
) -> None:
    promotion_paths = _bundle_promotion_paths(ref.path)
    tmp_dir = promotion_paths.candidate
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{index}/{total}] Generating {label} {ref.slug}...")
    env, fast_tmp_root = _with_fast_tmp(
        _base_env_for_mode(req, ref.mode), ref.mode, ref.slug
    )
    try:
        _generate_bundle_for_mode(ref.mode, tmp_dir, req, env)
        _replace_bundle_dir(
            tmp_dir, ref.path, transaction_id=promotion_paths.transaction_id
        )
        print(f"Saved bundle to {ref.path}")
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    finally:
        _cleanup_fast_tmp(fast_tmp_root)


def _execute_precompute_plan(args: argparse.Namespace, plan: PrecomputePlan) -> None:
    plan.dataset_root.mkdir(parents=True, exist_ok=True)
    index = 0
    for room in plan.rooms:
        for job in plan.jobs:
            index += 1
            _run_precompute_job(
                args=args,
                dataset_root=plan.dataset_root,
                total=plan.bundle_count,
                index=index,
                room=room,
                job=job,
            )


def run_precompute_sweep(config: PrecomputeSweepConfig) -> PrecomputePlan:
    args = config.to_namespace()
    plan = plan_precompute_sweep(config)

    if _use_docker() and not args.dry_run:
        print("Ensuring Radiance Docker image is available...")
        ensure_image()

    _print_precompute_plan(plan)
    if args.dry_run:
        _print_precompute_dry_run(plan)
        return plan

    _execute_precompute_plan(args, plan)
    return plan


def main() -> None:
    run_precompute_sweep(PrecomputeSweepConfig.from_args(parse_args()))


if __name__ == "__main__":
    main()
