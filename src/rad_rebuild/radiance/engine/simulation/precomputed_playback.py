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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

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
    bundle_ref,
    load_manifest,
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
from rad_rebuild.radiance.backend.models import RadianceRunRequest

ROOT = RADIANCE_OUTPUT_ROOT
WORK_ROOT = Path(os.getenv("RADIANCE_WORKSPACE_ROOT", str(ROOT))).resolve()
os.environ.setdefault("RADIANCE_ROOT", str(ROOT))
os.environ.setdefault("RADIANCE_OUTPUT_ROOT", str(ROOT))
os.environ.setdefault("RADIANCE_PY", sys.executable)
os.environ.setdefault("PYTHONPATH", str(REPO_ROOT / "src"))

JsonObject = dict[str, Any]
FloatArray = npt.NDArray[np.float64]
ResolvedArtifacts = dict[str, Path]


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


def parse_args() -> argparse.Namespace:
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
    ap.add_argument("--dataset-root", default="")
    ap.add_argument("--workspace-root", default="")
    return ap.parse_args()


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


def _materialize_competitor(
    bundle_dir: Path,
    manifest: JsonObject,
    req: RadianceRunRequest,
    workspace_root: Path,
    resolved_artifacts: ResolvedArtifacts,
) -> None:
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
    _print_competitor_scale(bundle_dir, req, base_mean=base_mean, scale=scale)


def _materialize_hps(
    bundle_dir: Path,
    manifest: JsonObject,
    req: RadianceRunRequest,
    workspace_root: Path,
    resolved_artifacts: ResolvedArtifacts,
) -> None:
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
) -> None:
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
    if mode.lower() == "competitor":
        _materialize_competitor(
            ref.path, manifest, req, workspace_root, resolved_artifacts
        )
    elif mode.lower() in {"1000w hps", "1000w de hps", "hps"}:
        _materialize_hps(ref.path, manifest, req, workspace_root, resolved_artifacts)
    else:
        _materialize_smd(ref.path, manifest, req, workspace_root, resolved_artifacts)
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
