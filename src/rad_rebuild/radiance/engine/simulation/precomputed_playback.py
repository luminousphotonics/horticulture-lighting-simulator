#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
# Subprocess calls in this module use resolved argv lists without invoking a shell.
import subprocess  # nosec B404
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import numpy.typing as npt

from rad_rebuild.radiance.assembly.fspm_panel import (
    FSPM_PANEL_METRICS_FILENAME,
    write_fspm_panel_metrics_artifact,
)
from rad_rebuild.radiance.assembly.scene import AssemblySceneError, build_assembly_scene
from rad_rebuild.radiance.executables import (
    ExecutableResolutionError,
    resolve_executable,
)
from rad_rebuild.radiance.config import (
    COMPETITOR_FIXTURE_PPE_UMOL_PER_J,
    COMPETITOR_FIXTURE_PPF_UMOL_S,
)
from rad_rebuild.radiance.paths import (
    RADIANCE_OUTPUT_ROOT,
    RADIANCE_SCRIPTS_ROOT,
    REPO_ROOT,
)

from rad_rebuild.radiance.engine.photometry.ppfd_metrics import format_ppfd_metrics_line
from rad_rebuild.radiance.engine.simulation.precomputed_dataset import (
    PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_ARTIFACT_KEY,
    PRECOMPUTED_FSPM_RECEIVER_GRANULARITY,
    PRECOMPUTED_FSPM_SPECTRAL_TRANSPORT_MODE,
    PRECOMPUTED_PLANT_RECEIVER_JSON_GZ_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_JSON_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_JSON_FILENAME,
    PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_SCHEMA,
    PRECOMPUTED_PLANT_RECEIVER_SCHEMA_VERSION,
    bundle_ref,
    load_precomputed_plant_receiver_npz_compact,
    load_manifest,
    write_precomputed_plant_receiver_payload,
)
from rad_rebuild.radiance.engine.simulation.precomputed_integrity import (
    PrecomputedIntegrityError,
    load_json_object,
    resolve_artifact_map,
    verify_basis_hash,
)
from rad_rebuild.radiance.engine.simulation.basis_backends import (
    DEFAULT_SMD_BASIS_BACKEND,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.solution_metadata import (
    build_smd_runtime_fingerprint_from_env,
    build_smd_solution_metadata,
)
from rad_rebuild.radiance.engine.optimization.solve_uniformity import (
    solve_minvar_qp,
)
from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
    DEFAULT_MOUNT_Z_M as DEFAULT_HPS_MOUNT_Z_M,
    NOMINAL_FIXTURE_PPF_UMOL_S as DEFAULT_HPS_FIXTURE_PPF,
    NOMINAL_INPUT_WATTS as DEFAULT_HPS_INPUT_WATTS,
)
from rad_rebuild.radiance.engine.plants.artifacts import (
    PLANT_ABSORPTION_SURFACES_FILENAME,
    PLANT_CONFIG_FILENAME,
    PLANTS_MANIFEST_FILENAME,
    PLANTS_RAD_FILENAME,
    PLANTS_VIEWER_FILENAME,
    write_plant_artifacts,
)
from rad_rebuild.radiance.engine.plants import (
    generate_plant_scene,
)
from rad_rebuild.radiance.domain import plant_geometry_config_from_request
from rad_rebuild.radiance.engine.plants.surface_flux import (
    PLANT_SURFACE_FLUX_FILENAME,
    RADIANCE_RECEIVER_METHOD,
    build_plant_surface_flux_payload,
    receiver_area_basis,
    receiver_generation_basis,
    receiver_granularity_role,
    receiver_side_policy,
    normal_generation_basis,
    write_plant_surface_flux_artifact,
)
from rad_rebuild.radiance.engine.plants.absorption import leaf_absorption_surfaces
from rad_rebuild.radiance.fspm_targets import (
    resolve_fspm_target_ppfd,
    resolve_fspm_target_tolerance,
)
from rad_rebuild.radiance.backend.models import RadianceRunRequest, request_with_updates

ROOT = RADIANCE_OUTPUT_ROOT
WORK_ROOT = Path(os.getenv("RADIANCE_WORKSPACE_ROOT", str(ROOT))).resolve()
os.environ.setdefault("RADIANCE_ROOT", str(ROOT))
os.environ.setdefault("RADIANCE_OUTPUT_ROOT", str(ROOT))
os.environ.setdefault("RADIANCE_PY", sys.executable)
os.environ.setdefault("PYTHONPATH", str(REPO_ROOT / "src"))

JsonObject = dict[str, Any]
FloatArray = npt.NDArray[np.float64]
ResolvedArtifacts = dict[str, Path]
RuntimeSource = Mapping[str, Any]

STALE_PRECOMPUTED_RUNTIME_FILES = (
    Path("assembly_scene.json"),
    Path("runtime_state") / PRECOMPUTED_PLANT_RECEIVER_JSON_FILENAME,
    Path("runtime_state") / PLANT_SURFACE_FLUX_FILENAME,
    Path("runtime_state") / PLANTS_VIEWER_FILENAME,
    Path("runtime_state") / FSPM_PANEL_METRICS_FILENAME,
    Path("runtime_state") / PLANTS_RAD_FILENAME,
    Path("runtime_state") / PLANTS_MANIFEST_FILENAME,
    Path("runtime_state") / PLANT_CONFIG_FILENAME,
    Path("runtime_state") / PLANT_ABSORPTION_SURFACES_FILENAME,
    Path("runtime_state") / "plant_spectral_absorption.json",
    Path("runtime_state") / "plant_spectral_response.json",
    Path("runtime_state") / "plant_photosynthesis_response.json",
    Path("runtime_state") / "plant_photoreceptor_exposure.json",
    Path("runtime_state") / "plant_photomorphogenesis_response.json",
)


class PlaybackError(RuntimeError):
    """Structured precomputed playback failure."""


@dataclass(frozen=True)
class PrecomputedPlaybackConfig:
    mode: str
    length_ft: float
    width_ft: float
    target_ppfd: float = 1000.0
    peak_capping_enabled: bool = False
    w_min: float = 0.0
    w_max: float = 100.0
    subpatch_grid: int = 1
    mount_z_m: float = 0.4572
    smd_base_ring: int = 0
    basis_backend: str = DEFAULT_SMD_BASIS_BACKEND
    match_system_ppe: bool = False
    sp_ppf: float = COMPETITOR_FIXTURE_PPF_UMOL_S
    sp_z_m: float = 0.4572
    sp_ppe: float = COMPETITOR_FIXTURE_PPE_UMOL_PER_J
    competitor_layout: str = "full"
    hps_coverage_ft: float = 4.0
    hps_z_m: float = DEFAULT_HPS_MOUNT_Z_M
    hps_fixture_ppf: float = DEFAULT_HPS_FIXTURE_PPF
    hps_input_watts: float = DEFAULT_HPS_INPUT_WATTS
    hps_ies_variant: str = ""
    dialux_sensor_grid: bool = False
    plants_enabled: bool = False
    plant_seed: int | None = None
    plant_rows: int | None = None
    plant_columns: int | None = None
    plant_spacing_m: float | None = None
    plant_height_m: float | None = None
    plant_canopy_radius_m: float | None = None
    plant_leaf_count: int | None = None
    plant_growth_stage: float | None = None
    fspm_receiver_granularity: str = PRECOMPUTED_FSPM_RECEIVER_GRANULARITY
    fspm_leaf_optical_profile_id: str = "rex_green_butterhead_mature_leaf_optics_v1"
    fspm_leaf_radiance_material_mode: str = "rex_source_weighted_trans"
    fspm_spectral_transport_mode: str = PRECOMPUTED_FSPM_SPECTRAL_TRANSPORT_MODE
    fspm_target_ppfd_umol_m2_s: float | None = None
    fspm_target_tolerance_umol_m2_s: float | None = None
    dataset_root: Path | None = None
    workspace_root: Path = WORK_ROOT

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "PrecomputedPlaybackConfig":
        dataset_root = (
            Path(args.dataset_root).resolve()
            if str(args.dataset_root or "").strip()
            else None
        )
        workspace_root = (
            Path(args.workspace_root).resolve()
            if str(args.workspace_root or "").strip()
            else WORK_ROOT
        )
        return cls(
            mode=str(args.mode),
            length_ft=float(args.length_ft),
            width_ft=float(args.width_ft),
            target_ppfd=float(args.target_ppfd),
            peak_capping_enabled=bool(args.peak_capping_enabled),
            w_min=float(args.w_min),
            w_max=float(args.w_max),
            subpatch_grid=int(args.subpatch_grid),
            mount_z_m=float(args.mount_z_m),
            smd_base_ring=int(args.smd_base_ring),
            basis_backend=str(args.basis_backend),
            match_system_ppe=bool(args.match_system_ppe),
            sp_ppf=float(args.sp_ppf),
            sp_z_m=float(args.sp_z_m),
            sp_ppe=float(args.sp_ppe),
            competitor_layout=str(args.competitor_layout),
            hps_coverage_ft=float(args.hps_coverage_ft),
            hps_z_m=float(args.hps_z_m),
            hps_fixture_ppf=float(args.hps_fixture_ppf),
            hps_input_watts=float(args.hps_input_watts),
            hps_ies_variant=str(args.hps_ies_variant),
            dialux_sensor_grid=bool(args.dialux_sensor_grid),
            plants_enabled=bool(args.plants_enabled),
            plant_seed=args.plant_seed,
            plant_rows=args.plant_rows,
            plant_columns=args.plant_columns,
            plant_spacing_m=args.plant_spacing_m,
            plant_height_m=args.plant_height_m,
            plant_canopy_radius_m=args.plant_canopy_radius_m,
            plant_leaf_count=args.plant_leaf_count,
            plant_growth_stage=args.plant_growth_stage,
            fspm_receiver_granularity=str(args.fspm_receiver_granularity),
            fspm_leaf_optical_profile_id=str(args.fspm_leaf_optical_profile_id),
            fspm_leaf_radiance_material_mode=str(args.fspm_leaf_radiance_material_mode),
            fspm_spectral_transport_mode=str(args.fspm_spectral_transport_mode),
            fspm_target_ppfd_umol_m2_s=args.fspm_target_ppfd_umol_m2_s,
            fspm_target_tolerance_umol_m2_s=args.fspm_target_tolerance_umol_m2_s,
            dataset_root=dataset_root,
            workspace_root=workspace_root,
        )

    @classmethod
    def from_request(
        cls,
        req: RadianceRunRequest,
        *,
        dataset_root: Path | None = None,
        workspace_root: Path = WORK_ROOT,
    ) -> "PrecomputedPlaybackConfig":
        return cls(
            mode=req.mode,
            length_ft=float(req.length_ft),
            width_ft=float(req.width_ft),
            target_ppfd=float(req.target_ppfd),
            peak_capping_enabled=bool(req.peak_capping_enabled),
            w_min=float(req.w_min),
            w_max=float(req.w_max),
            subpatch_grid=int(req.subpatch_grid),
            mount_z_m=float(req.mount_z_m),
            smd_base_ring=int(req.smd_base_ring),
            basis_backend=str(req.basis_backend),
            match_system_ppe=bool(req.match_system_ppe),
            sp_ppf=float(req.sp_ppf),
            sp_z_m=float(req.sp_z_m),
            sp_ppe=float(req.sp_ppe),
            competitor_layout=str(req.competitor_layout),
            hps_coverage_ft=float(req.hps_coverage_ft),
            hps_z_m=float(req.hps_z_m),
            hps_fixture_ppf=float(req.hps_fixture_ppf),
            hps_input_watts=float(req.hps_input_watts),
            hps_ies_variant=str(req.hps_ies_variant),
            dialux_sensor_grid=bool(req.dialux_sensor_grid),
            plants_enabled=bool(req.plants_enabled),
            plant_seed=req.plant_seed,
            plant_rows=req.plant_rows,
            plant_columns=req.plant_columns,
            plant_spacing_m=req.plant_spacing_m,
            plant_height_m=req.plant_height_m,
            plant_canopy_radius_m=req.plant_canopy_radius_m,
            plant_leaf_count=req.plant_leaf_count,
            plant_growth_stage=req.plant_growth_stage,
            fspm_receiver_granularity=str(req.fspm_receiver_granularity),
            fspm_leaf_optical_profile_id=str(req.fspm_leaf_optical_profile_id),
            fspm_leaf_radiance_material_mode=str(req.fspm_leaf_radiance_material_mode),
            fspm_spectral_transport_mode=str(req.fspm_spectral_transport_mode),
            fspm_target_ppfd_umol_m2_s=req.fspm_target_ppfd_umol_m2_s,
            fspm_target_tolerance_umol_m2_s=req.fspm_target_tolerance_umol_m2_s,
            dataset_root=dataset_root,
            workspace_root=workspace_root,
        )

    def to_request(self) -> RadianceRunRequest:
        return RadianceRunRequest(
            action="uniformity",
            mode=self.mode,
            length_ft=self.length_ft,
            width_ft=self.width_ft,
            target_ppfd=self.target_ppfd,
            peak_capping_enabled=self.peak_capping_enabled,
            w_min=self.w_min,
            w_max=self.w_max,
            subpatch_grid=self.subpatch_grid,
            mount_z_m=self.mount_z_m,
            smd_base_ring=self.smd_base_ring,
            basis_backend=self.basis_backend,
            match_system_ppe=self.match_system_ppe,
            sp_ppf=self.sp_ppf,
            sp_z_m=self.sp_z_m,
            sp_ppe=self.sp_ppe,
            competitor_layout=self.competitor_layout,
            hps_coverage_ft=self.hps_coverage_ft,
            hps_z_m=self.hps_z_m,
            hps_fixture_ppf=self.hps_fixture_ppf,
            hps_input_watts=self.hps_input_watts,
            hps_ies_variant=self.hps_ies_variant,
            dialux_sensor_grid=self.dialux_sensor_grid,
            plants_enabled=self.plants_enabled,
            plant_seed=self.plant_seed,
            plant_rows=self.plant_rows,
            plant_columns=self.plant_columns,
            plant_spacing_m=self.plant_spacing_m,
            plant_height_m=self.plant_height_m,
            plant_canopy_radius_m=self.plant_canopy_radius_m,
            plant_leaf_count=self.plant_leaf_count,
            plant_growth_stage=self.plant_growth_stage,
            fspm_receiver_granularity=self.fspm_receiver_granularity,
            fspm_leaf_optical_profile_id=self.fspm_leaf_optical_profile_id,
            fspm_leaf_radiance_material_mode=self.fspm_leaf_radiance_material_mode,
            fspm_spectral_transport_mode=self.fspm_spectral_transport_mode,
            fspm_target_ppfd_umol_m2_s=self.fspm_target_ppfd_umol_m2_s,
            fspm_target_tolerance_umol_m2_s=self.fspm_target_tolerance_umol_m2_s,
        )


@dataclass(frozen=True)
class PrecomputedPlaybackResult:
    mode: str
    bundle_dir: Path
    workspace_root: Path
    manifest: JsonObject


@dataclass(frozen=True)
class SmdLayoutPosition:
    ring: int


def _precomputed_runtime_source(
    req: RadianceRunRequest,
    *,
    run_id: str | None = None,
) -> JsonObject:
    return {
        "run_id": run_id or uuid.uuid4().hex,
        "source": "precomputed_playback",
        "mode": req.mode,
        "room_size_ft": {
            "length_ft": float(req.length_ft),
            "width_ft": float(req.width_ft),
        },
        "lighting_target_ppfd": float(req.target_ppfd),
        "fspm_target_ppfd": resolve_fspm_target_ppfd(
            getattr(req, "fspm_target_ppfd_umol_m2_s", None),
            fallback_target_ppfd=getattr(req, "target_ppfd", None),
        ),
        "fspm_tolerance": resolve_fspm_target_tolerance(
            getattr(req, "fspm_target_tolerance_umol_m2_s", None)
        ),
    }


def _cleanup_precomputed_runtime_artifacts(workspace_root: Path) -> None:
    for relative_path in STALE_PRECOMPUTED_RUNTIME_FILES:
        path = workspace_root / relative_path
        if path.is_file() or path.is_symlink():
            path.unlink()


def _runtime_source_payload(runtime_source: RuntimeSource | None) -> JsonObject | None:
    if runtime_source is None:
        return None
    return dict(runtime_source)


def _with_runtime_source(payload: Mapping[str, Any], runtime_source: RuntimeSource | None) -> JsonObject:
    out = dict(payload)
    source = _runtime_source_payload(runtime_source)
    if source is not None:
        out["runtime_source"] = source
    return out


def _stamp_json_artifact(path: Path, runtime_source: RuntimeSource | None) -> None:
    source = _runtime_source_payload(runtime_source)
    if source is None or not path.is_file():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return
    payload["runtime_source"] = source
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_precomputed_fspm_panel_metrics(
    workspace_root: Path,
    *,
    plants: Mapping[str, Any] | None,
    surface: Mapping[str, Any] | None,
    runtime_source: RuntimeSource | None,
) -> Path | None:
    path = write_fspm_panel_metrics_artifact(
        workspace_root,
        plants=plants,
        surface=surface,
    )
    if path is not None:
        _stamp_json_artifact(path, runtime_source)
    return path


def _env_spydr(req: RadianceRunRequest) -> dict[str, str]:
    from rad_rebuild.radiance.backend.env import _env_spydr as build_env

    return build_env(req)


def _env_hps(req: RadianceRunRequest) -> dict[str, str]:
    from rad_rebuild.radiance.backend.env import _env_hps as build_env

    return build_env(req)


def _env_smd(req: RadianceRunRequest) -> dict[str, str]:
    from rad_rebuild.radiance.backend.env import _env_smd as build_env

    return build_env(req)


def _workspace_env(env: dict[str, str], workspace_root: Path) -> dict[str, str]:
    out = dict(env)
    out["RADIANCE_OUTPUT_ROOT"] = str(workspace_root)
    out["RADIANCE_RUNTIME_STATE_ROOT"] = str(workspace_root / "runtime_state")
    out["RADIANCE_VISUALIZATION_OUTPUT_ROOT"] = str(workspace_root)
    return out


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Materialize precomputed Radiance artifacts into the engine workspace."
    )
    ap.add_argument("--mode", required=True)
    ap.add_argument("--length-ft", type=float, required=True)
    ap.add_argument("--width-ft", type=float, required=True)
    ap.add_argument("--target-ppfd", type=float, default=1000.0)
    ap.add_argument("--peak-capping-enabled", action="store_true", default=False)
    ap.add_argument("--w-min", type=float, default=0.0)
    ap.add_argument("--w-max", type=float, default=100.0)
    ap.add_argument("--subpatch-grid", type=int, default=1)
    ap.add_argument("--mount-z-m", type=float, default=0.4572)
    ap.add_argument("--smd-base-ring", type=int, default=0)
    ap.add_argument("--basis-backend", default=DEFAULT_SMD_BASIS_BACKEND)
    ap.add_argument("--match-system-ppe", action="store_true", default=False)
    ap.add_argument("--sp-ppf", type=float, default=COMPETITOR_FIXTURE_PPF_UMOL_S)
    ap.add_argument("--sp-z-m", type=float, default=0.4572)
    ap.add_argument("--sp-ppe", type=float, default=COMPETITOR_FIXTURE_PPE_UMOL_PER_J)
    ap.add_argument("--competitor-layout", default="full")
    ap.add_argument("--hps-coverage-ft", type=float, default=4.0)
    ap.add_argument("--hps-z-m", type=float, default=DEFAULT_HPS_MOUNT_Z_M)
    ap.add_argument("--hps-fixture-ppf", type=float, default=DEFAULT_HPS_FIXTURE_PPF)
    ap.add_argument("--hps-input-watts", type=float, default=DEFAULT_HPS_INPUT_WATTS)
    ap.add_argument("--hps-ies-variant", default="")
    ap.add_argument("--dialux-sensor-grid", action="store_true", default=False)
    ap.add_argument("--plants-enabled", action="store_true", default=False)
    ap.add_argument("--plant-seed", type=int, default=None)
    ap.add_argument("--plant-rows", type=int, default=None)
    ap.add_argument("--plant-columns", type=int, default=None)
    ap.add_argument("--plant-spacing-m", type=float, default=None)
    ap.add_argument("--plant-height-m", type=float, default=None)
    ap.add_argument("--plant-canopy-radius-m", type=float, default=None)
    ap.add_argument("--plant-leaf-count", type=int, default=None)
    ap.add_argument("--plant-growth-stage", type=float, default=None)
    ap.add_argument(
        "--fspm-receiver-granularity",
        default=PRECOMPUTED_FSPM_RECEIVER_GRANULARITY,
    )
    ap.add_argument(
        "--fspm-leaf-optical-profile-id",
        default="rex_green_butterhead_mature_leaf_optics_v1",
    )
    ap.add_argument(
        "--fspm-leaf-radiance-material-mode",
        default="rex_source_weighted_trans",
    )
    ap.add_argument(
        "--fspm-spectral-transport-mode",
        default=PRECOMPUTED_FSPM_SPECTRAL_TRANSPORT_MODE,
    )
    ap.add_argument(
        "--fspm-target-ppfd-umol-m2-s",
        "--fspm-target-ppfd",
        dest="fspm_target_ppfd_umol_m2_s",
        type=float,
        default=None,
    )
    ap.add_argument(
        "--fspm-target-tolerance-umol-m2-s",
        "--fspm-target-tolerance",
        dest="fspm_target_tolerance_umol_m2_s",
        type=float,
        default=None,
    )
    ap.add_argument("--dataset-root", default="")
    ap.add_argument("--workspace-root", default="")
    return ap.parse_args(argv)


def _request_from_args(args: argparse.Namespace) -> RadianceRunRequest:
    return RadianceRunRequest(
        action="uniformity",
        mode=args.mode,
        length_ft=args.length_ft,
        width_ft=args.width_ft,
        target_ppfd=args.target_ppfd,
        peak_capping_enabled=args.peak_capping_enabled,
        w_min=args.w_min,
        w_max=args.w_max,
        subpatch_grid=args.subpatch_grid,
        mount_z_m=args.mount_z_m,
        smd_base_ring=args.smd_base_ring,
        basis_backend=args.basis_backend,
        match_system_ppe=args.match_system_ppe,
        sp_ppf=args.sp_ppf,
        sp_z_m=args.sp_z_m,
        sp_ppe=args.sp_ppe,
        competitor_layout=args.competitor_layout,
        hps_coverage_ft=args.hps_coverage_ft,
        hps_z_m=args.hps_z_m,
        hps_fixture_ppf=args.hps_fixture_ppf,
        hps_input_watts=args.hps_input_watts,
        hps_ies_variant=args.hps_ies_variant,
        dialux_sensor_grid=args.dialux_sensor_grid,
        plants_enabled=args.plants_enabled,
        plant_seed=args.plant_seed,
        plant_rows=args.plant_rows,
        plant_columns=args.plant_columns,
        plant_spacing_m=args.plant_spacing_m,
        plant_height_m=args.plant_height_m,
        plant_canopy_radius_m=args.plant_canopy_radius_m,
        plant_leaf_count=args.plant_leaf_count,
        plant_growth_stage=args.plant_growth_stage,
        fspm_receiver_granularity=args.fspm_receiver_granularity,
        fspm_leaf_optical_profile_id=args.fspm_leaf_optical_profile_id,
        fspm_leaf_radiance_material_mode=args.fspm_leaf_radiance_material_mode,
        fspm_spectral_transport_mode=args.fspm_spectral_transport_mode,
        fspm_target_ppfd_umol_m2_s=args.fspm_target_ppfd_umol_m2_s,
        fspm_target_tolerance_umol_m2_s=args.fspm_target_tolerance_umol_m2_s,
    )


def _run_local(
    cmd: list[str],
    *,
    env: dict[str, str],
    workspace_root: Path,
    capture_stdout: bool = False,
) -> str:
    result = subprocess.run(  # nosec B603
        cmd,
        cwd=workspace_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        command = " ".join(str(part) for part in cmd)
        detail = [f"Command failed with exit code {result.returncode}: {command}"]
        if result.stdout:
            detail.append(f"stdout:\n{result.stdout.rstrip()}")
        if result.stderr:
            detail.append(f"stderr:\n{result.stderr.rstrip()}")
        raise RuntimeError("\n".join(detail))
    if not capture_stdout and result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.stdout if capture_stdout else ""


def _write_room_and_grid(env: dict[str, str], workspace_root: Path) -> None:
    try:
        py = resolve_executable(env.get("PY") or sys.executable, env=env)
        bash = resolve_executable("bash", env=env)
    except ExecutableResolutionError as exc:
        raise RuntimeError(str(exc)) from exc
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "runtime_state").mkdir(parents=True, exist_ok=True)
    room_text = _run_local(
        [str(py), "-m", "rad_rebuild.radiance.engine.geometry.generate_room"],
        env=env,
        workspace_root=workspace_root,
        capture_stdout=True,
    )
    (workspace_root / "room.rad").write_text(room_text)
    grid_env = dict(env)
    grid_env["ROOT"] = str(ROOT)
    _run_local(
        [
            str(bash),
            str(RADIANCE_SCRIPTS_ROOT / "generate_sensor_grid.sh"),
            str(workspace_root / "sensor_points.txt"),
        ],
        env=grid_env,
        workspace_root=workspace_root,
    )


def _copy_layout(src: Path, dest_name: str, workspace_root: Path) -> None:
    dst = workspace_root / "runtime_state" / dest_name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def _copy_optional_bundle_artifact(
    resolved_artifacts: ResolvedArtifacts, artifact_key: str, dest: Path
) -> None:
    src = resolved_artifacts.get(artifact_key)
    if src is None:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)


def _manifest_request_params(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    params = manifest.get("request_params")
    return params if isinstance(params, Mapping) else {}


def _request_with_manifest_plant_params(
    req: RadianceRunRequest,
    manifest: Mapping[str, Any],
    resolved_artifacts: ResolvedArtifacts,
) -> RadianceRunRequest:
    if not (
        PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY in resolved_artifacts
        or PRECOMPUTED_PLANT_RECEIVER_JSON_GZ_ARTIFACT_KEY in resolved_artifacts
        or PRECOMPUTED_PLANT_RECEIVER_JSON_ARTIFACT_KEY in resolved_artifacts
    ):
        return req
    params = _manifest_request_params(manifest)
    updates: dict[str, Any] = {"plants_enabled": True}
    for key in (
        "plant_seed",
        "plant_rows",
        "plant_columns",
        "plant_spacing_m",
        "plant_height_m",
        "plant_canopy_radius_m",
        "plant_leaf_count",
        "plant_growth_stage",
        "fspm_receiver_granularity",
        "fspm_leaf_optical_profile_id",
        "fspm_leaf_radiance_material_mode",
        "fspm_spectral_transport_mode",
    ):
        if key in params:
            updates[key] = params[key]
    return request_with_updates(req, **updates)


def _load_power_json(path: Path) -> JsonObject:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid power metadata in {path}")
    return data


def _write_competitor_power(
    power: JsonObject, scale: float, workspace_root: Path
) -> None:
    dst = workspace_root / "runtime_state" / "spydr3_power.txt"
    base_ppf = float(power.get("total_ppf", 0.0) or 0.0)
    base_w = float(power.get("total_w", 0.0) or 0.0)
    lines = [
        f"model_label={power.get('model_label', '')}",
        f"ppe_effective={power.get('ppe_effective', '')}",
        f"ppe_full={power.get('ppe_full', '')}",
        f"ppe_low={power.get('ppe_low', '')}",
        f"w_full={power.get('w_full', '')}",
        f"w_low={power.get('w_low', '')}",
        f"droop_k={power.get('droop_k', '')}",
        f"eff_scale={scale:.8f}",
        f"total_ppf={base_ppf * scale:.8f}",
        f"total_w={base_w * scale:.8f}",
        f"fixture_input_w={power.get('fixture_input_w', '')}",
        f"layout_mode={power.get('layout_mode', '')}",
        f"fixture_count={power.get('fixture_count', '')}",
        f"droop_enabled={int(bool(power.get('droop_enabled', False)))}",
    ]
    dst.write_text("\n".join(lines) + "\n")


def _write_hps_power(power: JsonObject, scale: float, workspace_root: Path) -> None:
    dst = workspace_root / "runtime_state" / "hps_power.txt"
    base_ppf = float(power.get("total_ppf", 0.0) or 0.0)
    base_w = float(power.get("total_w", 0.0) or 0.0)
    fixture_ppf = float(power.get("fixture_ppf", 0.0) or 0.0)
    fixture_input_w = float(power.get("fixture_input_w", 0.0) or 0.0)
    lines = [
        f"profile={power.get('profile', '')}",
        f"coverage_ft={power.get('coverage_ft', '')}",
        f"eff_scale={scale:.8f}",
        f"fixture_ppf={fixture_ppf * scale:.8f}",
        f"active_fixture_ppf_umol_s={float(power.get('active_fixture_ppf_umol_s', fixture_ppf) or 0.0) * scale:.8f}",
        f"fixture_input_w={fixture_input_w * scale:.8f}",
        f"fixture_ppe={power.get('fixture_ppe', '')}",
        f"total_ppf={base_ppf * scale:.8f}",
        f"total_w={base_w * scale:.8f}",
        f"fixture_count={power.get('fixture_count', '')}",
        f"model_label={power.get('model_label', '')}",
        f"model_mode={power.get('model_mode', '')}",
        f"archetype={power.get('archetype', '')}",
        f"ies_lumens_per_lamp_lm={power.get('ies_lumens_per_lamp_lm', '')}",
        f"ies_total_luminaire_lumens_lm={power.get('ies_total_luminaire_lumens_lm', '')}",
        f"ies_input_watts={power.get('ies_input_watts', '')}",
        f"spd_umol_per_lumen={power.get('spd_umol_per_lumen', '')}",
        f"pre_normalization_fixture_ppf_umol_s={float(power.get('pre_normalization_fixture_ppf_umol_s', 0.0) or 0.0) * scale:.8f}",
        f"default_fixture_anchor_umol_s={power.get('default_fixture_anchor_umol_s', '')}",
        f"fixture_anchor_authority={power.get('fixture_anchor_authority', '')}",
        f"final_scale_multiplier={power.get('final_scale_multiplier', '')}",
        f"effective_aperture_length_m={power.get('effective_aperture_length_m', '')}",
        f"effective_aperture_width_m={power.get('effective_aperture_width_m', '')}",
        f"effective_aperture_z_offset_m={power.get('effective_aperture_z_offset_m', '')}",
    ]
    if str(power.get("anchor_notes", "") or ""):
        lines.append(f"anchor_notes={power.get('anchor_notes', '')}")
    dst.write_text("\n".join(lines) + "\n")


def _json_float(data: JsonObject, key: str, default: float = 0.0) -> float:
    return float(data.get(key, default) or default)


def _json_text(data: JsonObject, key: str, default: str = "") -> str:
    return str(data.get(key, default) or default)


def _hps_summary_base_lines(
    layout: JsonObject, power: JsonObject, scale: float
) -> list[str]:
    fixtures = layout.get("fixtures", [])
    nx = int(layout.get("nx", 0) or 0)
    ny = int(layout.get("ny", 0) or 0)
    fixture_ppf = _json_float(power, "fixture_ppf") * scale
    return [
        str(power.get("model_label", "1000W HPS") or "1000W HPS"),
        f"mode={power.get('model_mode', '')}",
        f"archetype={power.get('archetype', layout.get('archetype', ''))}",
        f"profile={power.get('profile', '')}",
        f"fixtures={len(fixtures)} (NX={nx}, NY={ny})",
        f"coverage_ft={power.get('coverage_ft', '')}",
        f"nominal_fixture_ppf={fixture_ppf:.1f} umol/s",
        f"active_fixture_ppf_umol_s={_json_float(power, 'active_fixture_ppf_umol_s') * scale:.6f}",
        f"dimmer(EFF_SCALE)={scale:.4f}",
        f"total_fixture_ppf={_json_float(power, 'total_ppf') * scale:.1f} umol/s",
        f"total_input_w={_json_float(power, 'total_w') * scale:.1f} W",
        f"fixture_ppe={power.get('fixture_ppe', '')} umol/J",
    ]


def _hps_ies_summary_lines(power: JsonObject, scale: float) -> list[str]:
    if _json_float(power, "ies_total_luminaire_lumens_lm") <= 0.0:
        return []
    lines = [
        f"ies_lumens_per_lamp_lm={_json_float(power, 'ies_lumens_per_lamp_lm'):.6f}",
        f"ies_total_luminaire_lumens_lm={_json_float(power, 'ies_total_luminaire_lumens_lm'):.6f}",
        f"ies_input_watts={_json_float(power, 'ies_input_watts'):.6f}",
        f"spd_umol_per_lumen={_json_float(power, 'spd_umol_per_lumen'):.9f}",
        f"pre_normalization_fixture_ppf_umol_s={_json_float(power, 'pre_normalization_fixture_ppf_umol_s') * scale:.6f}",
        f"default_fixture_anchor_umol_s={_json_float(power, 'default_fixture_anchor_umol_s'):.6f}",
        f"fixture_anchor_authority={power.get('fixture_anchor_authority', '')}",
        f"final_scale_multiplier={_json_float(power, 'final_scale_multiplier'):.9f}",
        f"effective_aperture_length_m={_json_float(power, 'effective_aperture_length_m'):.6f}",
        f"effective_aperture_width_m={_json_float(power, 'effective_aperture_width_m'):.6f}",
        f"effective_aperture_z_offset_m={_json_float(power, 'effective_aperture_z_offset_m'):.6f}",
    ]
    if _json_text(power, "anchor_notes"):
        lines.append(f"anchor_notes={power.get('anchor_notes', '')}")
    return lines


def _hps_lamp_arc_summary_lines(layout: JsonObject) -> list[str]:
    lamp_arc = layout.get("lamp_arc_m") or {}
    if not isinstance(lamp_arc, dict) or not lamp_arc:
        return []
    return [
        f"lamp_arc_length_m={float(lamp_arc.get('length', 0.0) or 0.0):.4f}",
        f"lamp_arc_diameter_m={float(lamp_arc.get('diameter', 0.0) or 0.0):.4f}",
    ]


def _write_hps_summary(
    layout_path: Path, power: JsonObject, scale: float, workspace_root: Path
) -> None:
    layout = json.loads(layout_path.read_text())
    summary = _hps_summary_base_lines(layout, power, scale)
    summary.extend(_hps_ies_summary_lines(power, scale))
    summary.extend(_hps_lamp_arc_summary_lines(layout))
    (workspace_root / "runtime_state" / "hps_summary.txt").write_text(
        "\n".join(summary) + "\n"
    )


def _ppfd_stats_from_gzip(src_map: Path) -> tuple[float, float]:
    base_peak = 0.0
    accum = 0.0
    count = 0
    with gzip.open(src_map, "rt", encoding="utf-8") as src:
        for line in src:
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            value = float(parts[3])
            base_peak = max(base_peak, value)
            accum += value
            count += 1
    return base_peak, (accum / count) if count else 0.0


def _competitor_base_stats(stats: JsonObject, src_map: Path) -> tuple[float, float]:
    base_peak = float(stats.get("base_peak_ppfd", 0.0) or 0.0)
    base_mean = float(stats.get("base_mean_ppfd", 0.0) or 0.0)
    if base_peak > 0.0 and base_mean > 0.0:
        return base_peak, base_mean
    measured_peak, measured_mean = _ppfd_stats_from_gzip(src_map)
    return (
        base_peak if base_peak > 0.0 else measured_peak,
        base_mean if base_mean > 0.0 else measured_mean,
    )


def _competitor_scale(
    req: RadianceRunRequest, base_peak: float, base_mean: float
) -> float:
    if req.target_ppfd <= 0:
        return 1.0
    if req.peak_capping_enabled and base_peak > 0:
        return min(1.0, float(req.target_ppfd) / base_peak)
    if base_mean > 0:
        return min(1.0, float(req.target_ppfd) / base_mean)
    return 1.0


def _write_scaled_ppfd_map(src_map: Path, dst_map: Path, scale: float) -> None:
    with (
        gzip.open(src_map, "rt", encoding="utf-8") as src,
        dst_map.open("w", encoding="utf-8") as dst,
    ):
        for line in src:
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            ppfd = float(parts[3]) * scale
            dst.write(f"{parts[0]} {parts[1]} {parts[2]} {ppfd:.6f}\n")


def _print_competitor_scale(
    bundle_dir: Path,
    req: RadianceRunRequest,
    *,
    base_mean: float,
    scale: float,
) -> None:
    print(f"Loaded competitor bundle from {bundle_dir}")
    if req.peak_capping_enabled:
        print(
            f"Applied peak-cap scale factor {scale:.6f} for target {req.target_ppfd:.2f} µmol/m²/s"
        )
        return
    print(
        f"Applied mean-target scale factor {scale:.6f} for target {req.target_ppfd:.2f} µmol/m²/s"
    )
    if (
        base_mean > 0
        and float(req.target_ppfd) > base_mean
        and abs(scale - 1.0) < 1e-12
    ):
        print(
            "NOTE: requested target exceeds conventional fixture output; using full fixture output (scale 1.0)."
        )


def _require_artifact(artifacts: ResolvedArtifacts, key: str) -> Path:
    path = artifacts.get(key)
    if path is None:
        raise PlaybackError(f"Precomputed bundle is missing required artifact {key!r}")
    return path


def _load_plant_receiver_payload(
    resolved_artifacts: ResolvedArtifacts,
) -> JsonObject | None:
    npz_path = resolved_artifacts.get(PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY)
    if npz_path is not None:
        try:
            payload = load_precomputed_plant_receiver_npz_compact(npz_path)
        except ValueError as exc:
            raise PlaybackError(str(exc)) from exc
        return _validate_plant_receiver_payload(payload, npz_path)

    gz_path = resolved_artifacts.get(PRECOMPUTED_PLANT_RECEIVER_JSON_GZ_ARTIFACT_KEY)
    if gz_path is not None:
        with gzip.open(gz_path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise PlaybackError(f"Invalid precomputed plant receiver JSON in {gz_path}")
        return _validate_plant_receiver_payload(payload, gz_path)

    json_path = resolved_artifacts.get(PRECOMPUTED_PLANT_RECEIVER_JSON_ARTIFACT_KEY)
    if json_path is None:
        return None
    payload = load_json_object(json_path)
    return _validate_plant_receiver_payload(payload, json_path)


def _validate_plant_receiver_payload(payload: JsonObject, path: Path) -> JsonObject:
    if payload.get("schema") != PRECOMPUTED_PLANT_RECEIVER_SCHEMA:
        raise PlaybackError(
            f"Unsupported precomputed plant receiver schema in {path}: "
            f"{payload.get('schema')!r}"
        )
    if int(payload.get("schema_version", 0) or 0) != (
        PRECOMPUTED_PLANT_RECEIVER_SCHEMA_VERSION
    ):
        raise PlaybackError(
            f"Unsupported precomputed plant receiver schema_version in {path}: "
            f"{payload.get('schema_version')!r}"
        )
    return payload


def _load_plant_receiver_basis(
    resolved_artifacts: ResolvedArtifacts,
) -> FloatArray | None:
    npz_path = resolved_artifacts.get(PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_ARTIFACT_KEY)
    if npz_path is not None:
        with np.load(npz_path, allow_pickle=False) as archive:
            if "plant_receiver_basis_A" in archive:
                basis = archive["plant_receiver_basis_A"]
            elif len(archive.files) == 1:
                basis = archive[archive.files[0]]
            else:
                raise PlaybackError(
                    f"Precomputed plant receiver basis archive {npz_path} is missing "
                    "plant_receiver_basis_A."
                )
        return np.asarray(basis, dtype=np.float64)

    npy_path = resolved_artifacts.get(PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_ARTIFACT_KEY)
    if npy_path is not None:
        return np.asarray(np.load(npy_path, allow_pickle=False), dtype=np.float64)
    return None


def _plant_receiver_entries(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payload.get("surface_receivers", payload.get("rows", []))
    if not isinstance(raw, list) or not raw:
        raise PlaybackError("Precomputed plant receiver artifact has no receiver rows.")
    entries: list[Mapping[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise PlaybackError(
                f"Precomputed plant receiver row {index} must be an object."
            )
        surface_id = item.get("surface_id")
        if not isinstance(surface_id, str) or not surface_id:
            raise PlaybackError(
                f"Precomputed plant receiver row {index} is missing surface_id."
            )
        entries.append(item)
    return entries


def _plant_receiver_samples(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payload.get("receiver_samples")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PlaybackError("Precomputed plant receiver receiver_samples must be a list.")
    entries: list[Mapping[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise PlaybackError(
                f"Precomputed plant receiver sample {index} must be an object."
            )
        surface_id = item.get("surface_id")
        if not isinstance(surface_id, str) or not surface_id:
            raise PlaybackError(
                f"Precomputed plant receiver sample {index} is missing surface_id."
            )
        side = item.get("side")
        if not isinstance(side, str) or not side:
            raise PlaybackError(
                f"Precomputed plant receiver sample {index} is missing side."
            )
        entries.append(item)
    return entries


def _plant_receiver_value_entries(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    samples = _plant_receiver_samples(payload)
    return samples if samples else _plant_receiver_entries(payload)


def _compact_sample_arrays(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    arrays = payload.get("_sample_arrays")
    return arrays if isinstance(arrays, Mapping) else None


def _compact_surface_arrays(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    arrays = payload.get("_surface_arrays")
    return arrays if isinstance(arrays, Mapping) else None


def _plant_receiver_value_count(payload: Mapping[str, Any]) -> int:
    arrays = _compact_sample_arrays(payload)
    if arrays is not None and "stored_ppfd_umol_m2_s" in arrays:
        return int(len(arrays["stored_ppfd_umol_m2_s"]))
    return len(_plant_receiver_value_entries(payload))


def _receiver_density_value(entry: Mapping[str, Any], *, index: int) -> float:
    for key in (
        "runtime_ppfd_umol_m2_s",
        "stored_ppfd_umol_m2_s",
        "receiver_ppfd_umol_m2_s",
        "incident_photon_flux_density_umol_m2_s",
    ):
        value = entry.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            number = float(value)
            if np.isfinite(number) and number >= 0.0:
                return number
    raise PlaybackError(
        f"Precomputed plant receiver row {index} has no non-negative PPFD value."
    )


def _plant_receiver_runtime_values(
    payload: Mapping[str, Any],
    resolved_artifacts: ResolvedArtifacts,
    *,
    scale: float | None = None,
    coeffs: FloatArray | None = None,
) -> list[float]:
    arrays = _compact_sample_arrays(payload)
    if arrays is not None:
        basis = _load_plant_receiver_basis(resolved_artifacts)
        sample_count = int(len(arrays["stored_ppfd_umol_m2_s"]))
        if basis is not None:
            if coeffs is None:
                raise PlaybackError(
                    "Precomputed plant receiver basis requires solved SMD coefficients."
                )
            if basis.ndim != 2:
                raise PlaybackError("Precomputed plant receiver basis must be a 2D matrix.")
            if basis.shape[0] != sample_count:
                raise PlaybackError(
                    "Precomputed plant receiver basis row count does not match receiver samples."
                )
            if basis.shape[1] != coeffs.size:
                raise PlaybackError(
                    "Precomputed plant receiver basis columns do not match SMD coefficients."
                )
            return [max(0.0, float(value)) for value in (basis @ coeffs)]
        multiplier = 1.0 if scale is None else float(scale)
        stored = np.asarray(arrays["stored_ppfd_umol_m2_s"], dtype=np.float64)
        return [max(0.0, float(value) * multiplier) for value in stored]

    entries = _plant_receiver_value_entries(payload)
    basis = _load_plant_receiver_basis(resolved_artifacts)
    if basis is not None:
        if coeffs is None:
            raise PlaybackError(
                "Precomputed plant receiver basis requires solved SMD coefficients."
            )
        if basis.ndim != 2:
            raise PlaybackError("Precomputed plant receiver basis must be a 2D matrix.")
        if basis.shape[0] != len(entries):
            raise PlaybackError(
                "Precomputed plant receiver basis row count does not match receiver rows."
            )
        if basis.shape[1] != coeffs.size:
            raise PlaybackError(
                "Precomputed plant receiver basis columns do not match SMD coefficients."
            )
        values = basis @ coeffs
        return [max(0.0, float(value)) for value in values]
    multiplier = 1.0 if scale is None else float(scale)
    return [
        _receiver_density_value(entry, index=index) * multiplier
        for index, entry in enumerate(entries)
    ]


def _plant_receiver_surface_flux_rows(
    req: RadianceRunRequest,
    payload: Mapping[str, Any],
    runtime_values: list[float],
) -> tuple[Any, list[dict[str, Any]]]:
    scene = generate_plant_scene(plant_geometry_config_from_request(req))
    surfaces = {surface.surface_id: surface for surface in leaf_absorption_surfaces(scene)}
    samples = _plant_receiver_samples(payload)
    entries = samples if samples else _plant_receiver_entries(payload)
    if len(entries) != len(runtime_values):
        raise PlaybackError("Plant receiver values do not align with receiver rows.")
    if samples:
        grouped: dict[str, list[tuple[Mapping[str, Any], float]]] = {}
        for entry, density in zip(entries, runtime_values, strict=True):
            surface_id = str(entry["surface_id"])
            if surface_id not in surfaces:
                raise PlaybackError(
                    f"Precomputed plant receiver sample references unknown surface_id "
                    f"{surface_id!r}."
                )
            grouped.setdefault(surface_id, []).append((entry, max(0.0, float(density))))
        sample_rows: list[dict[str, Any]] = []
        for surface_id in sorted(grouped):
            surface = surfaces[surface_id]
            sample_items = grouped[surface_id]
            incident_density = sum(density for _entry, density in sample_items)
            side_summaries: list[dict[str, Any]] = []
            for entry, density in sample_items:
                side = str(entry.get("side") or "unknown")
                area_m2 = float(entry.get("area_m2") or surface.area_m2)
                side_row: dict[str, Any] = {
                    "sample_id": str(entry.get("sample_id") or f"{surface_id}_{side}"),
                    "surface_id": surface.surface_id,
                    "side": side,
                    "plant_id": surface.plant_id,
                    "leaf_id": surface.leaf_id,
                    "leaf_index": surface.leaf_index,
                    "face_index": surface.face_index,
                    "area_m2": area_m2,
                    "incident_photon_flux_density_umol_m2_s": density,
                    "incident_photon_flux_umol_s": density * area_m2,
                    "source": "precomputed_plant_receiver_playback",
                }
                for key in ("centroid_m", "normal"):
                    value = entry.get(key)
                    if isinstance(value, list):
                        side_row[key] = list(value)
                side_summaries.append(side_row)
            sample_rows.append(
                {
                    "surface_id": surface.surface_id,
                    "plant_id": surface.plant_id,
                    "leaf_id": surface.leaf_id,
                    "leaf_index": surface.leaf_index,
                    "face_index": surface.face_index,
                    "area_m2": surface.area_m2,
                    "receiver_sample_count": len(sample_items),
                    "receiver_granularity": "mesh_patch",
                    "receiver_rows_per_mesh_surface_row": len(sample_items),
                    "receiver_sides": [
                        str(entry.get("side") or "unknown")
                        for entry, _density in sample_items
                    ],
                    "side_summaries": side_summaries,
                    "visual_granularity": "mesh_patch",
                    "incident_photon_flux_density_umol_m2_s": incident_density,
                    "incident_photon_flux_umol_s": incident_density * surface.area_m2,
                    "source": "precomputed_plant_receiver_playback",
                }
            )
        return scene, sample_rows
    surface_rows: list[dict[str, Any]] = []
    for index, (entry, density) in enumerate(zip(entries, runtime_values, strict=True)):
        surface_id = str(entry["surface_id"])
        row_surface = surfaces.get(surface_id)
        if row_surface is None:
            raise PlaybackError(
                f"Precomputed plant receiver row {index} references unknown surface_id "
                f"{surface_id!r}."
            )
        value = max(0.0, float(density))
        surface_rows.append(
            {
                "surface_id": row_surface.surface_id,
                "plant_id": row_surface.plant_id,
                "leaf_id": row_surface.leaf_id,
                "leaf_index": row_surface.leaf_index,
                "face_index": row_surface.face_index,
                "area_m2": row_surface.area_m2,
                "incident_photon_flux_density_umol_m2_s": value,
                "incident_photon_flux_umol_s": value * row_surface.area_m2,
                "source": "precomputed_plant_receiver_playback",
            }
        )
    return scene, surface_rows


def _dense_mesh_patch_detail_from_arrays(
    payload: Mapping[str, Any],
    runtime_values: list[float],
    *,
    leaf_count: int,
    leaf_ids: Sequence[str] | None = None,
) -> JsonObject | None:
    arrays = _compact_sample_arrays(payload)
    if arrays is None:
        return None
    leaf_indices = np.asarray(arrays["leaf_index"], dtype=np.int32)
    face_indices = np.asarray(arrays["face_index"], dtype=np.int32)
    sides = np.asarray(arrays["side"])
    values = np.asarray(runtime_values, dtype=np.float64)
    if not (
        leaf_indices.size == face_indices.size == sides.size == values.size
    ):
        raise PlaybackError("Compact mesh_patch receiver sample arrays are misaligned.")
    if leaf_indices.size == 0:
        return None
    leaf_id_values = np.asarray(arrays["leaf_id"]) if "leaf_id" in arrays else None
    leaf_id_list = [str(value) for value in leaf_ids] if leaf_ids is not None else []
    if leaf_id_values is not None and leaf_id_values.size == values.size:
        if not leaf_id_list:
            seen: set[str] = set()
            for value in leaf_id_values:
                leaf_id = str(value)
                if leaf_id and leaf_id not in seen:
                    seen.add(leaf_id)
                    leaf_id_list.append(leaf_id)
        leaf_row_by_id = {leaf_id: index for index, leaf_id in enumerate(leaf_id_list)}
        leaf_rows = np.asarray(
            [leaf_row_by_id.get(str(value), -1) for value in leaf_id_values],
            dtype=np.int32,
        )
        leaf_count = len(leaf_id_list)
    else:
        leaf_rows = leaf_indices
        if not leaf_id_list:
            leaf_id_list = [f"leaf_{index:04d}" for index in range(leaf_count)]
    patches_per_leaf = int(face_indices.max()) + 1
    if patches_per_leaf <= 0 or leaf_count <= 0:
        return None
    dense = {
        "front": np.zeros((leaf_count, patches_per_leaf), dtype=np.float64),
        "back": np.zeros((leaf_count, patches_per_leaf), dtype=np.float64),
    }
    for leaf_row, face_index, side, value in zip(
        leaf_rows,
        face_indices,
        sides,
        values,
        strict=True,
    ):
        leaf = int(leaf_row)
        face = int(face_index)
        side_key = str(side)
        if leaf < 0 or leaf >= leaf_count or face < 0 or face >= patches_per_leaf:
            continue
        if side_key in dense:
            dense[side_key][leaf, face] = max(0.0, float(value))
    sides_list = ["front", "back"]
    return {
        "mode": "raw_leaf_surface_flux",
        "visual_granularity": "mesh_patch",
        "receiver_granularity": "mesh_patch",
        "encoding": "leaf_major_dense",
        "leaf_count": int(leaf_count),
        "leaf_ids": leaf_id_list,
        "true_sample_count_per_leaf": int(patches_per_leaf * len(sides_list)),
        "samples_per_leaf": int(patches_per_leaf * len(sides_list)),
        "patches_per_leaf": int(patches_per_leaf),
        "mesh_surface_rows_per_leaf": int(patches_per_leaf),
        "sides": sides_list,
        "side_policy": "front_and_back_per_mesh_surface_row",
        "top_bottom_support": True,
        "value_field": "incident_photon_flux_density_umol_m2_s",
        "patch_face_indices": [
            list(range(patches_per_leaf)) for _index in range(leaf_count)
        ],
        "values_ppfd": {
            "front": dense["front"].tolist(),
            "back": dense["back"].tolist(),
        },
    }


def _compact_mesh_patch_surface_flux_rows(
    req: RadianceRunRequest,
    payload: Mapping[str, Any],
    runtime_values: list[float],
) -> tuple[Any, list[dict[str, Any]], JsonObject | None]:
    arrays = _compact_sample_arrays(payload)
    if arrays is None:
        raise PlaybackError("Compact mesh_patch playback requires receiver sample arrays.")
    scene = generate_plant_scene(plant_geometry_config_from_request(req))
    surfaces = list(leaf_absorption_surfaces(scene))
    leaf_ids = [
        leaf.leaf_id
        for plant in scene.plants
        for leaf in plant.leaves
    ]
    leaf_count = len(leaf_ids)
    detail = _dense_mesh_patch_detail_from_arrays(
        payload,
        runtime_values,
        leaf_count=leaf_count,
        leaf_ids=leaf_ids,
    )
    if detail is None:
        raise PlaybackError("Compact mesh_patch playback could not build dense detail.")
    patches_per_leaf = int(detail["patches_per_leaf"])
    surface_values = np.zeros(leaf_count * patches_per_leaf, dtype=np.float64)
    sample_counts = np.zeros(leaf_count * patches_per_leaf, dtype=np.int32)
    leaf_indices = np.asarray(arrays["leaf_index"], dtype=np.int32)
    face_indices = np.asarray(arrays["face_index"], dtype=np.int32)
    values = np.asarray(runtime_values, dtype=np.float64)
    if "leaf_id" in arrays:
        leaf_row_by_id = {leaf_id: index for index, leaf_id in enumerate(leaf_ids)}
        leaf_id_values = np.asarray(arrays["leaf_id"])
        leaf_rows = np.asarray(
            [leaf_row_by_id.get(str(value), -1) for value in leaf_id_values],
            dtype=np.int32,
        )
    else:
        leaf_rows = leaf_indices
    surface_keys = leaf_rows * patches_per_leaf + face_indices
    valid = (
        (leaf_rows >= 0)
        & (leaf_rows < leaf_count)
        & (face_indices >= 0)
        & (face_indices < patches_per_leaf)
    )
    np.add.at(surface_values, surface_keys[valid], values[valid])
    np.add.at(sample_counts, surface_keys[valid], 1)
    leaf_row_by_id = {leaf_id: index for index, leaf_id in enumerate(leaf_ids)}
    rows: list[dict[str, Any]] = []
    for surface in surfaces:
        leaf_row = leaf_row_by_id.get(surface.leaf_id, -1)
        key = int(leaf_row) * patches_per_leaf + int(surface.face_index)
        value = (
            max(0.0, float(surface_values[key]))
            if 0 <= key < surface_values.size
            else 0.0
        )
        sample_count = int(sample_counts[key]) if 0 <= key < sample_counts.size else 0
        rows.append(
            {
                "surface_id": surface.surface_id,
                "plant_id": surface.plant_id,
                "leaf_id": surface.leaf_id,
                "leaf_index": surface.leaf_index,
                "face_index": surface.face_index,
                "area_m2": surface.area_m2,
                "receiver_sample_count": sample_count or 2,
                "receiver_granularity": "mesh_patch",
                "receiver_rows_per_mesh_surface_row": sample_count or 2,
                "receiver_sides": ["front", "back"],
                "visual_granularity": "mesh_patch",
                "incident_photon_flux_density_umol_m2_s": value,
                "incident_photon_flux_umol_s": value * surface.area_m2,
                "source": "precomputed_plant_receiver_playback",
            }
        )
    return scene, rows, detail


def _precomputed_plant_receiver_metadata(
    req: RadianceRunRequest,
    payload: Mapping[str, Any],
    *,
    runtime_values: list[float],
    scale: float | None,
    coeffs: FloatArray | None,
) -> dict[str, Any]:
    granularity = str(
        payload.get("receiver_granularity")
        or getattr(
            req,
            "fspm_receiver_granularity",
            PRECOMPUTED_FSPM_RECEIVER_GRANULARITY,
        )
    )
    source_mode = "smd_basis_coefficients" if coeffs is not None else "scaled_raw_values"
    return {
        "precomputed_plant_receiver_schema": payload.get("schema"),
        "precomputed_plant_receiver_schema_version": payload.get("schema_version"),
        "precomputed_plant_receiver_value_semantics": payload.get("value_semantics"),
        "precomputed_plant_receiver_source_mode": source_mode,
        "precomputed_plant_receiver_scale": 1.0 if scale is None else float(scale),
        "runtime_receiver_min_ppfd_umol_m2_s": (
            min(runtime_values) if runtime_values else 0.0
        ),
        "runtime_receiver_max_ppfd_umol_m2_s": (
            max(runtime_values) if runtime_values else 0.0
        ),
        "runtime_receiver_mean_ppfd_umol_m2_s": (
            sum(runtime_values) / len(runtime_values) if runtime_values else 0.0
        ),
        "receiver_granularity": granularity,
        "receiver_generation_basis": receiver_generation_basis(granularity),
        "receiver_area_basis": receiver_area_basis(granularity),
        "receiver_side_policy": receiver_side_policy(granularity),
        "normal_generation_basis": normal_generation_basis(granularity),
        "receiver_granularity_role": receiver_granularity_role(granularity),
        "fspm_spectral_transport_mode": req.fspm_spectral_transport_mode,
        "leaf_radiance_material_mode": req.fspm_leaf_radiance_material_mode,
        "leaf_material_profile_id": req.fspm_leaf_optical_profile_id,
    }


def _write_runtime_plant_receiver_payload(
    workspace_root: Path,
    receiver_payload: Mapping[str, Any],
    runtime_values: list[float],
    *,
    runtime_source: RuntimeSource | None = None,
) -> None:
    if _compact_sample_arrays(receiver_payload) is not None:
        payload = {
            str(key): value
            for key, value in receiver_payload.items()
            if not str(key).startswith("_")
            and key not in {"receiver_samples", "surface_receivers"}
        }
        payload["runtime_value_source"] = "precomputed_playback"
        payload["runtime_receiver_sample_count"] = len(runtime_values)
        source = _runtime_source_payload(runtime_source)
        if source is not None:
            payload["runtime_source"] = source
        write_precomputed_plant_receiver_payload(
            workspace_root / "runtime_state" / PRECOMPUTED_PLANT_RECEIVER_JSON_FILENAME,
            payload,
        )
        return
    entries = _plant_receiver_value_entries(receiver_payload)
    rows: list[JsonObject] = []
    for entry, value in zip(entries, runtime_values, strict=True):
        row = dict(entry)
        row["runtime_ppfd_umol_m2_s"] = max(0.0, float(value))
        rows.append(row)
    payload = {
        str(key): value
        for key, value in receiver_payload.items()
        if not str(key).startswith("_")
    }
    if _plant_receiver_samples(receiver_payload):
        payload["receiver_samples"] = rows
    else:
        payload["surface_receivers"] = rows
    payload["runtime_value_source"] = "precomputed_playback"
    source = _runtime_source_payload(runtime_source)
    if source is not None:
        payload["runtime_source"] = source
    write_precomputed_plant_receiver_payload(
        workspace_root / "runtime_state" / PRECOMPUTED_PLANT_RECEIVER_JSON_FILENAME,
        payload,
    )


def _diagnostic_stats(values: Sequence[float]) -> JsonObject:
    finite = [float(value) for value in values if np.isfinite(float(value))]
    if not finite:
        return {"count": 0, "min": None, "mean": None, "max": None}
    return {
        "count": len(finite),
        "min": min(finite),
        "mean": sum(finite) / len(finite),
        "max": max(finite),
    }


def _diagnostic_range_counts(
    values: Sequence[float],
    *,
    lower: float | None,
    upper: float | None,
) -> JsonObject:
    if lower is None or upper is None:
        return {"under_lit": None, "target_range": None, "over_lit": None}
    under = sum(1 for value in values if float(value) < lower)
    over = sum(1 for value in values if float(value) > upper)
    target = len(values) - under - over
    return {"under_lit": under, "target_range": target, "over_lit": over}


def _diagnostic_ppfd_map_values(path: Path) -> list[float]:
    if not path.is_file():
        return []
    values: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 4:
            try:
                values.append(float(parts[3]))
            except ValueError:
                continue
    return values


def _diagnostic_receiver_entries(path: Path) -> list[Mapping[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    raw = payload.get("surface_receivers", payload.get("rows", []))
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, Mapping)]


def _diagnostic_leaf_values(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    visualization = payload.get("visualization")
    if isinstance(visualization, Mapping):
        raw = visualization.get("leaf_values")
        if isinstance(raw, list):
            return [row for row in raw if isinstance(row, Mapping)]
    raw_leaf_summaries = payload.get("leaf_summaries")
    if isinstance(raw_leaf_summaries, list):
        return [row for row in raw_leaf_summaries if isinstance(row, Mapping)]
    return []


def precomputed_plant_playback_diagnostics(workspace_root: str | Path) -> JsonObject:
    """Summarize no-Radiance plant playback sources used by the FSPM panel."""

    root = Path(workspace_root)
    runtime_root = root / "runtime_state"
    ppfd_map_values = _diagnostic_ppfd_map_values(root / "ppfd_map.txt")
    receiver_entries = _diagnostic_receiver_entries(
        runtime_root / PRECOMPUTED_PLANT_RECEIVER_JSON_FILENAME
    )
    stored_values = [
        float(row["stored_ppfd_umol_m2_s"])
        for row in receiver_entries
        if isinstance(row.get("stored_ppfd_umol_m2_s"), int | float)
    ]
    runtime_values = [
        float(row["runtime_ppfd_umol_m2_s"])
        for row in receiver_entries
        if isinstance(row.get("runtime_ppfd_umol_m2_s"), int | float)
    ]

    surface_payload: JsonObject = {}
    surface_path = runtime_root / "plant_surface_flux.json"
    if surface_path.is_file():
        try:
            loaded = json.loads(surface_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                surface_payload = loaded
        except json.JSONDecodeError:
            surface_payload = {}
    leaf_values = _diagnostic_leaf_values(surface_payload)
    leaf_raw_values = [
        float(row["incident_photon_flux_density_umol_m2_s"])
        for row in leaf_values
        if isinstance(row.get("incident_photon_flux_density_umol_m2_s"), int | float)
    ]
    if not runtime_values:
        runtime_values = leaf_raw_values
    leaf_classification_values = [
        float(row["target_classification_ppfd_umol_m2_s"])
        for row in leaf_values
        if isinstance(row.get("target_classification_ppfd_umol_m2_s"), int | float)
    ]
    lower_value = surface_payload.get("target_lower_threshold_umol_m2_s")
    upper_value = surface_payload.get("target_upper_threshold_umol_m2_s")
    lower = float(lower_value) if isinstance(lower_value, int | float) else None
    upper = float(upper_value) if isinstance(upper_value, int | float) else None
    return {
        "baseline_ppfd_map": {
            **_diagnostic_stats(ppfd_map_values),
            "target_counts": _diagnostic_range_counts(
                ppfd_map_values,
                lower=lower,
                upper=upper,
            ),
        },
        "plant_receiver_stored_ppfd": _diagnostic_stats(stored_values),
        "plant_receiver_runtime_ppfd": {
            **_diagnostic_stats(runtime_values),
            "target_counts": _diagnostic_range_counts(
                runtime_values,
                lower=lower,
                upper=upper,
            ),
        },
        "plant_surface_flux_ppfd": _diagnostic_stats(leaf_raw_values),
        "leaf_aggregate_classification_ppfd": {
            **_diagnostic_stats(leaf_classification_values),
            "target_counts": _diagnostic_range_counts(
                leaf_classification_values,
                lower=lower,
                upper=upper,
            ),
        },
        "target_range": {"lower": lower, "upper": upper},
        "fspm_panel_source": surface_payload.get("target_classification_source"),
        "leaf_aggregate_counts": {
            "under_lit": surface_payload.get("under_lit_leaf_count"),
            "target_range": surface_payload.get("target_range_leaf_count"),
            "over_lit": surface_payload.get("over_lit_leaf_count"),
        },
    }


def _target_classification_map_for_playback(workspace_root: Path) -> Path | None:
    path = workspace_root / "ppfd_map.txt"
    return path if path.is_file() else None


def _materialize_plant_receiver_surface_flux(
    req: RadianceRunRequest,
    workspace_root: Path,
    resolved_artifacts: ResolvedArtifacts,
    *,
    scale: float | None = None,
    coeffs: FloatArray | None = None,
    runtime_source: RuntimeSource | None = None,
) -> JsonObject | None:
    if not bool(getattr(req, "plants_enabled", False)):
        return None
    receiver_payload = _load_plant_receiver_payload(resolved_artifacts)
    if receiver_payload is None:
        return None
    runtime_values = _plant_receiver_runtime_values(
        receiver_payload,
        resolved_artifacts,
        scale=scale,
        coeffs=coeffs,
    )
    _write_runtime_plant_receiver_payload(
        workspace_root,
        receiver_payload,
        runtime_values,
        runtime_source=runtime_source,
    )
    dense_detail: JsonObject | None = None
    if _compact_sample_arrays(receiver_payload) is not None:
        scene, rows, dense_detail = _compact_mesh_patch_surface_flux_rows(
            req,
            receiver_payload,
            runtime_values,
        )
    else:
        scene, rows = _plant_receiver_surface_flux_rows(
            req,
            receiver_payload,
            runtime_values,
        )
    surface_count = len(leaf_absorption_surfaces(scene))
    leaf_count = len({leaf.leaf_id for plant in scene.plants for leaf in plant.leaves})
    receiver_sample_count = _plant_receiver_value_count(receiver_payload)
    receiver_samples_per_leaf = (
        receiver_sample_count / leaf_count if leaf_count else 0.0
    )
    receiver_rows_per_surface = (
        receiver_sample_count / surface_count if surface_count else 0.0
    )
    target_ppfd = resolve_fspm_target_ppfd(
        getattr(req, "fspm_target_ppfd_umol_m2_s", None),
        fallback_target_ppfd=getattr(req, "target_ppfd", None),
    )
    target_tolerance = resolve_fspm_target_tolerance(
        getattr(req, "fspm_target_tolerance_umol_m2_s", None)
    )
    one_sided_area = sum(surface.area_m2 for surface in leaf_absorption_surfaces(scene))
    metadata = _precomputed_plant_receiver_metadata(
        req,
        receiver_payload,
        runtime_values=runtime_values,
        scale=scale,
        coeffs=coeffs,
    )
    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=RADIANCE_RECEIVER_METHOD,
        source_ppfd_map="ppfd_map.txt",
        ppfd_field_summary=metadata,
        target_ppfd_umol_m2_s=target_ppfd,
        target_tolerance_umol_m2_s=target_tolerance,
        target_classification_ppfd_map_path=_target_classification_map_for_playback(
            workspace_root
        ),
        receiver_sample_count=receiver_sample_count,
        receiver_granularity=str(metadata["receiver_granularity"]),
        receiver_samples_per_leaf=receiver_samples_per_leaf,
        receiver_generation_basis=str(metadata["receiver_generation_basis"]),
        receiver_represented_area_m2=one_sided_area,
        receiver_sample_area_sum_m2=one_sided_area,
        receiver_area_basis=str(metadata["receiver_area_basis"]),
        receiver_side_policy=str(metadata["receiver_side_policy"]),
        receiver_rows_per_mesh_surface_row=receiver_rows_per_surface,
        normal_generation_basis=str(metadata["normal_generation_basis"]),
        receiver_granularity_role_value=str(metadata["receiver_granularity_role"]),
        leaf_material_metadata=metadata,
    )
    payload.update(
        {
            "baseline_transport_scene": "precomputed_playback",
            "fspm_receiver_transport_scene": "precomputed_plant_receiver_artifact",
            "receiver_trace_count": 0,
            "receiver_sample_count": receiver_sample_count,
            "receiver_samples_per_leaf": receiver_samples_per_leaf,
            "receiver_represented_area_m2": one_sided_area,
            "receiver_sample_area_sum_m2": one_sided_area,
            "receiver_rows_per_mesh_surface_row": receiver_rows_per_surface,
        }
    )
    if dense_detail is not None:
        visualization = payload.setdefault("visualization", {})
        if isinstance(visualization, dict):
            visualization["raw_leaf_surface_flux_detail"] = dense_detail
            visualization["visual_granularity"] = "mesh_patch"
            raw_summary = visualization.get("raw_leaf_surface_flux_summary")
            if isinstance(raw_summary, dict):
                raw_summary["visualization_granularity"] = "mesh_patch"
                raw_summary["surface_detail_sample_count"] = receiver_sample_count
                raw_summary["surface_detail_bucket_counts"] = []
        payload["raw_leaf_surface_flux_detail"] = dense_detail
        payload["visualization_granularity"] = "mesh_patch"
        payload["mesh_patch_side_detail_available"] = True
        raw_summary = payload.get("raw_leaf_surface_flux_summary")
        if isinstance(raw_summary, dict):
            raw_summary["visualization_granularity"] = "mesh_patch"
            raw_summary["surface_detail_sample_count"] = receiver_sample_count
            raw_summary["surface_detail_bucket_counts"] = []
    payload = _with_runtime_source(payload, runtime_source)
    try:
        plant_paths = write_plant_artifacts(
            workspace_root / "runtime_state",
            plant_geometry_config_from_request(req),
            active_simulation_integration=True,
            provenance_phase="precomputed_playback",
        )
    except ValueError as exc:
        raise PlaybackError(f"Invalid precomputed plant geometry: {exc}") from exc
    _stamp_json_artifact(plant_paths.viewer, runtime_source)
    plants_payload = load_json_object(plant_paths.viewer)
    write_plant_surface_flux_artifact(workspace_root / "runtime_state", payload)
    _write_precomputed_fspm_panel_metrics(
        workspace_root,
        plants=plants_payload,
        surface=payload,
        runtime_source=runtime_source,
    )
    return payload


def _required_artifact_keys(mode: str) -> tuple[str, ...]:
    normalized = str(mode).lower()
    if normalized == "competitor":
        return ("ppfd_map_txt_gz", "layout_json", "power_json")
    if normalized in {"1000w hps", "1000w de hps", "hps"}:
        return ("ppfd_map_txt_gz", "layout_json", "power_json")
    return ("basis_A_npy", "layout_json")


def _preflight_playback_artifacts(
    bundle_dir: Path, manifest: JsonObject, mode: str
) -> ResolvedArtifacts:
    try:
        resolved_artifacts = resolve_artifact_map(bundle_dir, manifest)
        verify_basis_hash(manifest, resolved_artifacts)
    except (OSError, ValueError, PrecomputedIntegrityError) as exc:
        raise PlaybackError(str(exc)) from exc
    for key in _required_artifact_keys(mode):
        _require_artifact(resolved_artifacts, key)
    return resolved_artifacts


def _write_assembly_scene_json(
    workspace_root: Path,
    req: RadianceRunRequest,
    *,
    runtime_source: RuntimeSource | None = None,
) -> None:
    try:
        scene = build_assembly_scene(workspace_root, req)
    except AssemblySceneError as exc:
        raise PlaybackError(str(exc)) from exc
    source = _runtime_source_payload(runtime_source)
    if source is not None:
        scene["runtime_source"] = source
    (workspace_root / "assembly_scene.json").write_text(
        json.dumps(scene, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _materialize_competitor(
    bundle_dir: Path,
    manifest: JsonObject,
    req: RadianceRunRequest,
    workspace_root: Path,
    resolved_artifacts: ResolvedArtifacts,
    *,
    runtime_source: RuntimeSource | None = None,
) -> None:
    runtime_source = runtime_source or _precomputed_runtime_source(req)
    _cleanup_precomputed_runtime_artifacts(workspace_root)
    env = _workspace_env(_env_spydr(req), workspace_root)
    env["AUTO_DIM"] = "0"
    _write_room_and_grid(env, workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)

    stats = manifest.get("stats") or {}
    src_map = _require_artifact(resolved_artifacts, "ppfd_map_txt_gz")
    base_peak, base_mean = _competitor_base_stats(stats, src_map)
    scale = _competitor_scale(req, base_peak, base_mean)

    dst_map = workspace_root / "ppfd_map.txt"
    _write_scaled_ppfd_map(src_map, dst_map, scale)

    _copy_layout(
        _require_artifact(resolved_artifacts, "layout_json"),
        "spydr3_layout.json",
        workspace_root,
    )
    power = _load_power_json(_require_artifact(resolved_artifacts, "power_json"))
    _write_competitor_power(power, scale, workspace_root)
    _materialize_plant_receiver_surface_flux(
        req,
        workspace_root,
        resolved_artifacts,
        scale=scale,
        runtime_source=runtime_source,
    )
    _print_competitor_scale(bundle_dir, req, base_mean=base_mean, scale=scale)


def _materialize_hps(
    bundle_dir: Path,
    manifest: JsonObject,
    req: RadianceRunRequest,
    workspace_root: Path,
    resolved_artifacts: ResolvedArtifacts,
    *,
    runtime_source: RuntimeSource | None = None,
) -> None:
    runtime_source = runtime_source or _precomputed_runtime_source(req)
    _cleanup_precomputed_runtime_artifacts(workspace_root)
    env = _workspace_env(_env_hps(req), workspace_root)
    env["AUTO_DIM"] = "0"
    _write_room_and_grid(env, workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)

    scale = 1.0

    src_map = _require_artifact(resolved_artifacts, "ppfd_map_txt_gz")
    dst_map = workspace_root / "ppfd_map.txt"
    with (
        gzip.open(src_map, "rt", encoding="utf-8") as src,
        dst_map.open("w", encoding="utf-8") as dst,
    ):
        for line in src:
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            ppfd = float(parts[3]) * scale
            dst.write(f"{parts[0]} {parts[1]} {parts[2]} {ppfd:.6f}\n")

    _copy_layout(
        _require_artifact(resolved_artifacts, "layout_json"),
        "hps_layout.json",
        workspace_root,
    )
    power = _load_power_json(_require_artifact(resolved_artifacts, "power_json"))
    layout_path = workspace_root / "runtime_state" / "hps_layout.json"
    _write_hps_power(power, scale, workspace_root)
    _write_hps_summary(layout_path, power, scale, workspace_root)
    _materialize_plant_receiver_surface_flux(
        req,
        workspace_root,
        resolved_artifacts,
        scale=scale,
        runtime_source=runtime_source,
    )
    print(f"Loaded 1000W HPS bundle from {bundle_dir}")
    print("Applied fixed-output HPS playback (no target scaling).")


def _write_smd_solution_json(
    out_path: Path,
    manifest: JsonObject | None,
    runtime_env: dict[str, str] | None,
    weights: FloatArray,
    solve_details: JsonObject | None,
    target_ppfd: float,
    metrics: JsonObject,
    basis_path: Path,
    n_points: int,
) -> None:
    manifest_data = manifest or {}
    solve_data = solve_details or {}
    out = _smd_solution_base_payload(
        weights=weights,
        solve_details=solve_data,
        target_ppfd=target_ppfd,
        metrics=metrics,
        basis_path=basis_path,
        n_points=n_points,
        basis_unit_w=_smd_basis_unit_w(manifest_data, solve_data),
    )
    out["smd_solution_metadata"] = build_smd_solution_metadata(
        runtime_fingerprint=build_smd_runtime_fingerprint_from_env(
            layout_meta=_smd_solution_layout_meta(manifest),
            module_count=manifest_data.get("layout_modules")
            if isinstance(manifest, dict)
            else None,
            env=runtime_env,
        ),
        basis_manifest=manifest,
        basis_path=basis_path,
        n_points=n_points,
    )
    _add_smd_solution_variable_payload(out, manifest_data, weights)
    out["n_points"] = int(manifest_data.get("n_points", 0) or 0)
    out_path.write_text(json.dumps(out, indent=2))


def _smd_solution_layout_meta(manifest: JsonObject | None) -> JsonObject | None:
    layout_meta = None
    if isinstance(manifest, dict):
        n_rings = manifest.get("n_rings")
        layout_meta = {
            "layout_mode": (
                (manifest.get("emitter_env") or {})
                if isinstance(manifest.get("emitter_env"), dict)
                else {}
            ).get("LAYOUT_MODE"),
            "layout_family": manifest.get("layout_family"),
            "room_L_m": manifest.get("room_L_m"),
            "room_W_m": manifest.get("room_W_m"),
            "ring_n": (int(n_rings) - 1) if n_rings is not None else None,
            "rings": n_rings,
            "spacing_m": manifest.get("spacing_m"),
            "pitch_x_m": manifest.get("pitch_x_m"),
            "pitch_y_m": manifest.get("pitch_y_m"),
            "base_n": manifest.get("base_n"),
        }
    return layout_meta


def _smd_basis_unit_w(manifest: JsonObject, solve_details: JsonObject) -> float:
    basis_unit_w = solve_details.get("basis_unit_w_per_module")
    if basis_unit_w is None:
        basis_unit_w = manifest.get("basis_unit_w_per_module", 1.0) or 1.0
    return float(basis_unit_w)


def _smd_solution_base_payload(
    *,
    weights: FloatArray,
    solve_details: JsonObject,
    target_ppfd: float,
    metrics: JsonObject,
    basis_path: Path,
    n_points: int,
    basis_unit_w: float,
) -> JsonObject:
    return {
        "target_ppfd": float(target_ppfd),
        "strategy": "minvar_qp",
        "lambda_s_best": None,
        "lambda_r_best": None,
        "lambda_mean": None,
        "lambda_smooth": 0.0,
        "metrics": metrics,
        "basis_file": str(basis_path),
        "n_points": int(n_points),
        "solve_space": solve_details.get("solve_space", "electrical_w_per_module"),
        "basis_unit_w_per_module": basis_unit_w,
        "basis_source_photon_umol_s": float(
            solve_details.get("basis_source_photon_umol_s", 0.0) or 0.0
        ),
        "basis_coefficients": [
            float(x) for x in (solve_details.get("basis_coefficients") or list(weights))
        ],
    }


def _add_smd_solution_variable_payload(
    out: JsonObject, manifest: JsonObject, weights: FloatArray
) -> None:
    var_mode = manifest.get("variables") or "rings"
    if var_mode == "per_module":
        out.update(_per_module_solution_payload(manifest, weights))
        return
    if var_mode == "ring_plus_outer_modules":
        out.update(_ring_plus_outer_solution_payload(manifest, weights))
        return
    out.update(_ring_solution_payload(manifest, weights))


def _per_module_solution_payload(
    manifest: JsonObject, weights: FloatArray
) -> JsonObject:
    module_indices = manifest.get("module_indices") or list(range(int(weights.size)))
    return {
        "variables": "per_module",
        "module_indices": [int(x) for x in module_indices],
        "module_powers_W_per_module": [float(x) for x in weights],
        "variable_groups": {"modules": int(weights.size)},
    }


def _ring_plus_outer_solution_payload(
    manifest: JsonObject, weights: FloatArray
) -> JsonObject:
    groups = manifest.get("variable_groups") or {}
    ring_group = int(groups.get("rings", 0) or 0)
    ring_indices = manifest.get("ring_indices") or list(range(ring_group))
    return {
        "variables": "ring_plus_outer_modules",
        "ring_indices": [int(x) for x in ring_indices],
        "ring_powers_W_per_module": [float(x) for x in weights[:ring_group]],
        "outer_ring_index": manifest.get("outer_ring_index"),
        "outer_ring_indices": [
            int(x) for x in (manifest.get("outer_ring_indices") or [])
        ],
        "outer_ring_powers_W_per_module": [float(x) for x in weights[ring_group:]],
        "variable_groups": {
            "rings": int(ring_group),
            "outer_modules": int(max(0, weights.size - ring_group)),
        },
    }


def _ring_solution_payload(manifest: JsonObject, weights: FloatArray) -> JsonObject:
    ring_indices = manifest.get("ring_indices") or list(range(int(weights.size)))
    return {
        "variables": "rings",
        "ring_indices": [int(x) for x in ring_indices],
        "ring_powers_W_per_module": [float(x) for x in weights],
    }


def _smd_variable_module_counts(
    layout: JsonObject, basis_meta: JsonObject | None, variable_count: int
) -> list[int]:
    if not isinstance(layout, dict) or "positions" not in layout:
        return [1] * variable_count
    positions = _validated_smd_positions(layout["positions"])
    var_mode = (basis_meta or {}).get("variables") or "rings"
    if var_mode == "per_module":
        return [1] * variable_count
    if var_mode == "ring_plus_outer_modules":
        return _smd_ring_plus_outer_module_counts(positions, basis_meta, variable_count)
    return _smd_ring_module_counts(positions, basis_meta, variable_count)


def _validated_smd_positions(raw_positions: object) -> list[SmdLayoutPosition]:
    if not isinstance(raw_positions, list):
        raise PlaybackError("Malformed SMD layout: positions must be a list")
    positions: list[SmdLayoutPosition] = []
    for index, raw in enumerate(raw_positions):
        if not isinstance(raw, dict):
            raise PlaybackError(f"Malformed SMD layout position {index}: expected object")
        raw_ring = raw.get("ring")
        if not isinstance(raw_ring, int):
            raise PlaybackError(
                f"Malformed SMD layout position {index}: ring must be an integer"
            )
        positions.append(SmdLayoutPosition(ring=raw_ring))
    return positions


def _validated_int_list(raw_values: object, field: str) -> list[int]:
    if not isinstance(raw_values, list):
        raise PlaybackError(f"Malformed SMD basis manifest: {field} must be a list")
    values: list[int] = []
    for index, raw in enumerate(raw_values):
        if not isinstance(raw, int):
            raise PlaybackError(
                f"Malformed SMD basis manifest: {field}[{index}] must be an integer"
            )
        values.append(raw)
    return values


def _validated_variable_groups(raw_groups: object) -> JsonObject:
    if not isinstance(raw_groups, dict):
        raise PlaybackError(
            "Malformed SMD basis manifest: variable_groups must be an object"
        )
    return raw_groups


def _validated_nonnegative_int(raw_value: object, field: str) -> int:
    if not isinstance(raw_value, int) or raw_value < 0:
        raise PlaybackError(
            f"Malformed SMD basis manifest: {field} must be a nonnegative integer"
        )
    return raw_value


def _count_positions_for_ring(
    positions: list[SmdLayoutPosition], ring_idx: int
) -> int:
    return sum(1 for position in positions if position.ring == ring_idx)


def _smd_ring_plus_outer_module_counts(
    positions: list[SmdLayoutPosition],
    basis_meta: JsonObject | None,
    variable_count: int,
) -> list[int]:
    meta = basis_meta or {}
    groups = _validated_variable_groups(meta.get("variable_groups") or {})
    ring_group = _validated_nonnegative_int(
        groups.get("rings", 0), "variable_groups.rings"
    )
    ring_indices = _validated_int_list(
        meta.get("ring_indices") or list(range(ring_group)), "ring_indices"
    )
    counts = [
        _count_positions_for_ring(positions, ring_idx)
        for ring_idx in ring_indices[:ring_group]
    ]
    counts.extend([1] * max(0, variable_count - len(counts)))
    return counts[:variable_count]


def _smd_ring_module_counts(
    positions: list[SmdLayoutPosition], basis_meta: JsonObject | None, variable_count: int
) -> list[int]:
    meta = basis_meta or {}
    ring_indices = _validated_int_list(
        meta.get("ring_indices") or list(range(variable_count)), "ring_indices"
    )
    counts = [
        _count_positions_for_ring(positions, ring_idx)
        for ring_idx in ring_indices[:variable_count]
    ]
    counts.extend([1] * max(0, variable_count - len(counts)))
    return counts[:variable_count]


def _write_smd_summary(
    *,
    layout_path: Path,
    workspace_root: Path,
    basis_meta: JsonObject | None,
    weights: FloatArray,
    solve_details: JsonObject | None,
    metrics: JsonObject,
    target_ppfd: float,
) -> None:
    layout = json.loads(layout_path.read_text()) if layout_path.exists() else {}
    counts = _smd_variable_module_counts(layout, basis_meta, int(weights.size))
    total_watts = float(sum(float(w) * int(count) for w, count in zip(weights, counts)))
    total_photons = _smd_summary_total_photons(
        weights=weights,
        counts=counts,
        total_watts=total_watts,
        basis_meta=basis_meta,
        solve_details=solve_details,
    )
    summary = _smd_summary_lines(
        layout=layout,
        counts=counts,
        weights=weights,
        total_watts=total_watts,
        total_photons=total_photons,
        metrics=metrics,
        target_ppfd=target_ppfd,
    )
    dst = workspace_root / "runtime_state" / "smd_summary.txt"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(summary) + "\n", encoding="utf-8")


def _smd_summary_total_photons(
    *,
    weights: FloatArray,
    counts: list[int],
    total_watts: float,
    basis_meta: JsonObject | None,
    solve_details: JsonObject | None,
) -> float:
    basis_source_photons = float(
        (solve_details or {}).get("basis_source_photon_umol_s", 0.0) or 0.0
    )
    basis_unit_w = float(
        (solve_details or {}).get("basis_unit_w_per_module", 1.0) or 1.0
    )
    if basis_source_photons > 0.0 and basis_unit_w > 0.0:
        total_photons = float(
            sum(
                float(coeff) * basis_source_photons * int(count)
                for coeff, count in zip(weights / basis_unit_w, counts)
            )
        )
    else:
        avg_ppe = float(
            ((basis_meta or {}).get("curve_model") or {}).get(
                "nominal_wall_plug_ppe", 0.0
            )
            or 0.0
        )
        total_photons = total_watts * avg_ppe if avg_ppe > 0.0 else 0.0
    return total_photons


def _smd_summary_lines(
    *,
    layout: JsonObject,
    counts: list[int],
    weights: FloatArray,
    total_watts: float,
    total_photons: float,
    metrics: JsonObject,
    target_ppfd: float,
) -> list[str]:
    ring_lines = [
        f"  variable {idx}: {float(watts):.3f} W/module across {int(count)} module(s)"
        for idx, (watts, count) in enumerate(zip(weights, counts))
    ]
    return [
        "SMD macro emitter summary:",
        "  source      : precomputed playback",
        f"  target PPFD : {float(target_ppfd):.3f} umol/m2/s",
        f"  modules     : {int(layout.get('modules', sum(counts) or 0) or 0)}",
        f"  rings       : {int(layout.get('rings', len(counts)) or 0)}",
        f"  basis vars  : {int(weights.size)}",
        f"  total electrical input ≈ {total_watts:.1f} W",
        f"  total emitted photons ≈ {total_photons:.0f} umol/s",
        "  solved schedule:",
        *ring_lines,
        f"  metrics     : {format_ppfd_metrics_line(metrics)}",
    ]


def _materialize_smd(
    bundle_dir: Path,
    manifest: JsonObject,
    req: RadianceRunRequest,
    workspace_root: Path,
    resolved_artifacts: ResolvedArtifacts,
    *,
    runtime_source: RuntimeSource | None = None,
) -> None:
    runtime_source = runtime_source or _precomputed_runtime_source(req)
    _cleanup_precomputed_runtime_artifacts(workspace_root)
    env = _workspace_env(_env_smd(req), workspace_root)
    _write_room_and_grid(env, workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)

    basis_path = _require_artifact(resolved_artifacts, "basis_A_npy")
    bundle_basis_manifest = resolved_artifacts.get("basis_manifest_json")
    basis = np.asarray(np.load(basis_path, allow_pickle=False), dtype=np.float64)
    basis_meta = (
        load_json_object(bundle_basis_manifest)
        if bundle_basis_manifest is not None
        else None
    )

    weights, metrics, solve_details = solve_minvar_qp(
        basis,
        float(req.target_ppfd),
        float(req.w_min),
        float(req.w_max),
        legacy_metrics=True,
        tol_mean=0.005,
        basis_manifest=basis_meta,
        runtime_env=env,
    )

    sensor_path = workspace_root / "sensor_points.txt"
    coords = np.loadtxt(sensor_path)
    if coords.ndim == 1:
        coords = coords.reshape(1, -1)
    if coords.shape[0] != basis.shape[0]:
        raise RuntimeError(
            f"Sensor grid size {coords.shape[0]} does not match basis rows {basis.shape[0]} for {bundle_dir}"
        )

    coeffs = np.asarray(
        (solve_details or {}).get("basis_coefficients") or list(weights), dtype=float
    )
    ppfd = basis @ coeffs
    out_map = workspace_root / "ppfd_map.txt"
    with out_map.open("w", encoding="utf-8") as handle:
        for xyz, val in zip(coords, ppfd):
            handle.write(f"{xyz[0]:.6f} {xyz[1]:.6f} {xyz[2]:.6f} {float(val):.6f}\n")
    _materialize_plant_receiver_surface_flux(
        req,
        workspace_root,
        resolved_artifacts,
        coeffs=coeffs,
        runtime_source=runtime_source,
    )

    ring_json = workspace_root / "ring_powers_optimized.json"
    _write_smd_solution_json(
        ring_json,
        basis_meta,
        env,
        weights,
        solve_details,
        req.target_ppfd,
        metrics,
        basis_path,
        basis.shape[0],
    )
    _copy_layout(
        _require_artifact(resolved_artifacts, "layout_json"),
        "smd_layout.json",
        workspace_root,
    )
    layout_path = workspace_root / "runtime_state" / "smd_layout.json"
    _copy_optional_bundle_artifact(
        resolved_artifacts,
        "basis_manifest_json",
        workspace_root / "basis" / "basis_manifest.json",
    )
    _copy_optional_bundle_artifact(
        resolved_artifacts,
        "basis_build_log_json",
        workspace_root / "basis" / "basis_build_log.json",
    )
    _copy_optional_bundle_artifact(
        resolved_artifacts,
        "basis_backend_log_json",
        workspace_root / "basis" / "basis_runs" / "basis_backend_log.json",
    )
    _write_smd_summary(
        layout_path=layout_path,
        workspace_root=workspace_root,
        basis_meta=basis_meta,
        weights=weights,
        solve_details=solve_details,
        metrics=metrics,
        target_ppfd=req.target_ppfd,
    )

    print(f"Loaded SMD basis bundle from {bundle_dir}")
    print("Solved precomputed SMD field:")
    print(format_ppfd_metrics_line(metrics))


def run_precomputed_playback(
    config: PrecomputedPlaybackConfig,
) -> PrecomputedPlaybackResult:
    workspace_root = config.workspace_root.resolve()
    req = config.to_request()
    dataset_root = (
        config.dataset_root.resolve() if config.dataset_root is not None else None
    )
    ref = bundle_ref(ROOT, req.mode, req.length_ft, req.width_ft, dataset_root, req=req)
    if ref is None:
        raise PlaybackError("Precomputed playback requires integer-foot room sizes.")
    manifest = load_manifest(ref)
    if manifest is None:
        raise PlaybackError(
            f"No precomputed manifest found for {req.mode} {req.length_ft}x{req.width_ft} at {ref.path}"
        )

    mode = str(manifest.get("mode") or req.mode)
    resolved_artifacts = _preflight_playback_artifacts(ref.path, manifest, mode)
    req = _request_with_manifest_plant_params(req, manifest, resolved_artifacts)
    runtime_source = _precomputed_runtime_source(req)
    if mode.lower() == "competitor":
        _materialize_competitor(
            ref.path,
            manifest,
            req,
            workspace_root,
            resolved_artifacts,
            runtime_source=runtime_source,
        )
    elif mode.lower() in {"1000w hps", "1000w de hps", "hps"}:
        _materialize_hps(
            ref.path,
            manifest,
            req,
            workspace_root,
            resolved_artifacts,
            runtime_source=runtime_source,
        )
    else:
        _materialize_smd(
            ref.path,
            manifest,
            req,
            workspace_root,
            resolved_artifacts,
            runtime_source=runtime_source,
        )
    _write_assembly_scene_json(workspace_root, req, runtime_source=runtime_source)
    return PrecomputedPlaybackResult(
        mode=mode,
        bundle_dir=ref.path,
        workspace_root=workspace_root,
        manifest=manifest,
    )


def main() -> None:
    try:
        run_precomputed_playback(PrecomputedPlaybackConfig.from_args(parse_args()))
    except PlaybackError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
