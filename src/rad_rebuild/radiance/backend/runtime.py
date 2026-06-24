from __future__ import annotations

import json
import os
import shlex
import threading
import time
from pathlib import Path

from fastapi import HTTPException, Request

from rad_rebuild.radiance.config import (
    BACKEND_VERSION,
    DEFAULT_EXECUTION_MODE,
    MODE_COMPETITOR,
    MODE_SMD,
)
from rad_rebuild.radiance.settings import get_settings
from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
    DEFAULT_MOUNT_Z_M as DEFAULT_HPS_MOUNT_Z_M,
    get_ies_comparator_profile,
    normalize_ies_variant as normalize_hps_ies_variant,
)

from .artifacts import (
    _live_workspace_sync_shell,
    _prune_file_caches,
    _sha256_file,
)
from .env import (
    HPS_MODE_LABEL,
    _canonicalize_execution_mode,
    _canonicalize_mode_request,
    _canonicalize_quality_preset,
    _env_hps,
    _env_smd,
    _env_spydr,
    _make_env_base,
    _request_uses_docker,
    _request_uses_precomputed,
    _normalize_mode,
    _resolve_python,
)
from .jobs import _prune_old_jobs
from .models import RadianceRunRequest, request_with_updates
from .runner import _docker_command, _local_command, _run_cmd, ensure_image
from .runtime_status import live_mode_supported, unsupported_live_mode_detail
from .workspace import (
    ENGINE_PACKAGE_ROOT,
    ROOT,
    SCRIPT_ROOT,
    allocate_workspace_for_run,
    commit_staged_workspace,
    fail_staged_workspace,
    _prune_old_sessions,
    _workspace_last_run_path,
    _workspace_root_for_request,
    prune_workspace_store,
)

try:
    from rad_rebuild.radiance.engine.simulation.precomputed_dataset import (
        bundle_complete,
        bundle_ref,
        effective_precomputed_mode,
        load_manifest,
        params_match,
        request_params_for_mode,
        resolve_precomputed_root,
    )
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"Failed to import backend runtime dependencies: {e}") from e


RUNTIME_CLEANUP_INTERVAL_S = get_settings().runtime_cleanup_interval_s
LIVE_EXECUTION_RATE_LIMIT_WINDOW_S = 60.0
LIVE_EXECUTION_RATE_LIMIT_REQUESTS = 3

_cleanup_lock = threading.Lock()
_last_cleanup_at = 0.0
_live_rate_lock = threading.Lock()
_live_rate_events: dict[str, list[float]] = {}


def live_execution_enabled() -> bool:
    return get_settings().live_execution_enabled


def assert_live_execution_allowed(req: RadianceRunRequest, session_id: str) -> None:
    if _request_uses_precomputed(req):
        return
    if not live_mode_supported(req.mode, os.environ):
        raise HTTPException(status_code=422, detail=unsupported_live_mode_detail(req.mode, os.environ))
    if not live_execution_enabled():
        raise HTTPException(
            status_code=403,
            detail={
                "error": "live_execution_disabled",
                "message": "Live execution is disabled.",
            },
        )
    now = time.time()
    cutoff = now - LIVE_EXECUTION_RATE_LIMIT_WINDOW_S
    with _live_rate_lock:
        events = [event for event in _live_rate_events.get(session_id, []) if event >= cutoff]
        if len(events) >= LIVE_EXECUTION_RATE_LIMIT_REQUESTS:
            _live_rate_events[session_id] = events
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "live_execution_rate_limited",
                    "message": "Live execution rate limit exceeded.",
                },
            )
        events.append(now)
        _live_rate_events[session_id] = events


def request_bool_query_param(request: Request, name: str, default: bool = False) -> bool:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    return default


def maybe_cleanup_runtime_state(force: bool = False) -> None:
    global _last_cleanup_at
    now = time.time()
    if not force and (now - _last_cleanup_at) < RUNTIME_CLEANUP_INTERVAL_S:
        return
    with _cleanup_lock:
        now = time.time()
        if not force and (now - _last_cleanup_at) < RUNTIME_CLEANUP_INTERVAL_S:
            return
        _prune_old_jobs(now)
        _prune_old_sessions(now)
        prune_workspace_store(now=now)
        _prune_file_caches()
        _last_cleanup_at = now


def request_runtime_identity(req: RadianceRunRequest) -> dict[str, object]:
    req = _canonicalize_mode_request(req)
    mode = _normalize_mode(req.mode)
    if mode == MODE_COMPETITOR:
        env = _env_spydr(req)
    elif mode == HPS_MODE_LABEL:
        env = _env_hps(req)
    else:
        env = _env_smd(req)
    env_clean = {k: env[k] for k in sorted(env) if k not in {"PATH", "PY"}}
    identity: dict[str, object] = {
        "mode": mode,
        "execution_mode": _canonicalize_execution_mode(getattr(req, "execution_mode", DEFAULT_EXECUTION_MODE)),
        "sim_mode": _canonicalize_quality_preset(getattr(req, "sim_mode", "standard")),
        "request_params": request_params_for_mode(req),
        "env": env_clean,
        "backend_version": BACKEND_VERSION,
    }
    if mode == MODE_SMD:
        identity.update(
            {
                "generator_sha256": _sha256_file(ENGINE_PACKAGE_ROOT / "emitters" / "generate_emitters_smd.py"),
                "curve_model_sha256": _sha256_file(ENGINE_PACKAGE_ROOT / "photometry" / "smd_curve_model.py"),
                "module_profile_sha256": _sha256_file(
                    ENGINE_PACKAGE_ROOT
                    / "emitters"
                    / "smd_generation"
                    / "module_profile.py"
                ),
            }
        )
        for key in ("SMD_WHITE_VF_CSV", "SMD_WHITE_PPE_CSV", "SMD_RED_VF_CSV", "SMD_RED_PPE_CSV"):
            path_str = env_clean.get(key)
            if isinstance(path_str, str) and path_str:
                p = Path(path_str)
                identity[f"{key}_sha256"] = _sha256_file(p) if p.exists() else ""
    return identity


def workspace_cache_is_current(req: RadianceRunRequest, session_id: str) -> bool:
    workspace_root = _workspace_root_for_request(session_id, req)
    ppfd_map = workspace_root / "ppfd_map.txt"
    if not ppfd_map.exists():
        return False
    last_run_path = _workspace_last_run_path(session_id, req)
    if not last_run_path.exists():
        return False
    try:
        last_run = json.loads(last_run_path.read_text())
    except Exception:
        return False
    cached_identity = last_run.get("runtime_identity")
    return cached_identity == request_runtime_identity(req)


def precomputed_mode() -> str:
    return effective_precomputed_mode(ROOT)


def _precomputed_bundle_exact(req: RadianceRunRequest) -> tuple[Path, dict[str, object]] | None:
    ref = bundle_ref(ROOT, req.mode, req.length_ft, req.width_ft, req=req)
    if ref is None or not bundle_complete(ref):
        return None
    manifest = load_manifest(ref)
    if not manifest:
        return None
    if not params_match(manifest, request_params_for_mode(req)):
        return None
    return ref.path, manifest


def precomputed_bundle(req: RadianceRunRequest) -> tuple[Path, dict[str, object]] | None:
    exact = _precomputed_bundle_exact(req)
    if exact is not None:
        return exact
    matched = precomputed_request_for_available_bundle(req)
    if matched is None:
        return None
    return _precomputed_bundle_exact(matched)


def _hps_default_power_for_variant(variant: str) -> tuple[float, float]:
    clean = normalize_hps_ies_variant(variant)
    profile = get_ies_comparator_profile(clean)
    return profile.nominal_fixture_ppf_umol_s, profile.nominal_input_watts


def precomputed_request_for_available_bundle(req: RadianceRunRequest) -> RadianceRunRequest | None:
    if _precomputed_bundle_exact(req) is not None:
        return req
    if _normalize_mode(req.mode) != HPS_MODE_LABEL:
        return None
    variant = normalize_hps_ies_variant(req.hps_ies_variant)
    fixture_ppf, input_watts = _hps_default_power_for_variant(variant)
    candidate = request_with_updates(
        req,
        hps_ies_variant=variant,
        hps_z_m=DEFAULT_HPS_MOUNT_Z_M,
        hps_fixture_ppf=fixture_ppf,
        hps_input_watts=input_watts,
    )
    if _precomputed_bundle_exact(candidate) is not None:
        return candidate
    return None


def pipeline_shell(req: RadianceRunRequest) -> tuple[str, dict[str, str]]:
    mode = req.mode
    if mode == MODE_COMPETITOR:
        env = _env_spydr(req)
        return f"bash {shlex.quote(str(SCRIPT_ROOT / 'run_simulation_spydr3.sh'))}", env
    if mode == HPS_MODE_LABEL:
        env = _env_hps(req)
        return f"bash {shlex.quote(str(SCRIPT_ROOT / 'run_simulation_hps.sh'))}", env
    env = _env_smd(req)
    return f"bash {shlex.quote(str(SCRIPT_ROOT / 'run_uniformity.sh'))}", env


def pipeline_command(
    req: RadianceRunRequest,
    *,
    workspace_root: Path,
    session_id: str = "anon",
) -> tuple[list[str], dict[str, str]]:
    current_precomputed_mode = precomputed_mode()
    bundle = None
    if _request_uses_precomputed(req) and current_precomputed_mode != "off":
        matched_req = precomputed_request_for_available_bundle(req)
        if matched_req is not None:
            req = matched_req
            bundle = _precomputed_bundle_exact(req)
    if bundle is not None:
        env = _make_env_base(req)
        py = _resolve_python()
        env["PY"] = py
        cmd = [
            py,
            "-m",
            "rad_rebuild.radiance.engine.simulation.precomputed_playback",
            "--mode",
            req.mode,
            "--length-ft",
            f"{req.length_ft:g}",
            "--width-ft",
            f"{req.width_ft:g}",
            "--target-ppfd",
            f"{req.target_ppfd:g}",
            "--w-min",
            f"{req.w_min:g}",
            "--w-max",
            f"{req.w_max:g}",
            "--subpatch-grid",
            str(req.subpatch_grid),
            "--mount-z-m",
            f"{req.mount_z_m:g}",
            "--smd-base-ring",
            str(req.smd_base_ring),
            "--basis-backend",
            req.basis_backend,
            "--sp-ppf",
            f"{req.sp_ppf:g}",
            "--sp-z-m",
            f"{req.sp_z_m:g}",
            "--sp-ppe",
            f"{req.sp_ppe:g}",
            "--competitor-layout",
            req.competitor_layout,
            "--hps-coverage-ft",
            f"{req.hps_coverage_ft:g}",
            "--hps-z-m",
            f"{req.hps_z_m:g}",
            "--hps-fixture-ppf",
            f"{req.hps_fixture_ppf:g}",
            "--hps-input-watts",
            f"{req.hps_input_watts:g}",
            "--hps-ies-variant",
            req.hps_ies_variant,
            "--dataset-root",
            str(resolve_precomputed_root(ROOT)),
            "--workspace-root",
            str(workspace_root),
        ]
        if req.match_system_ppe:
            cmd.append("--match-system-ppe")
        if req.peak_capping_enabled:
            cmd.append("--peak-capping-enabled")
        if req.dialux_sensor_grid:
            cmd.append("--dialux-sensor-grid")
        return cmd, env
    if _request_uses_precomputed(req) and current_precomputed_mode == "only":
        ref = bundle_ref(ROOT, req.mode, req.length_ft, req.width_ft, req=req)
        slug = ref.slug if ref is not None else f"{req.length_ft:g}x{req.width_ft:g}"
        raise RuntimeError(
            f"Precomputed mode is active, but no matching bundle was found for {req.mode} {slug} "
            f"in {resolve_precomputed_root(ROOT)}."
        )
    shell_cmd, env = pipeline_shell(req)
    cmd = _docker_command(shell_cmd, env) if _request_uses_docker(req) else _local_command(shell_cmd)
    return cmd, env


def ensure_request_workspace(req: RadianceRunRequest, session_id: str) -> Path:
    req = _canonicalize_mode_request(req)
    current_precomputed_mode = precomputed_mode()
    if _request_uses_precomputed(req) and current_precomputed_mode != "off":
        matched_req = precomputed_request_for_available_bundle(req)
        if matched_req is not None:
            req = matched_req
    if workspace_cache_is_current(req, session_id):
        return _workspace_root_for_request(session_id, req)

    lease = allocate_workspace_for_run(session_id, req)
    workspace_root = lease.staging_workspace

    bundle = precomputed_bundle(req) if _request_uses_precomputed(req) and current_precomputed_mode != "off" else None
    try:
        if bundle is not None:
            cmd, env = pipeline_command(req, workspace_root=workspace_root, session_id=session_id)
            _run_cmd(cmd, cwd=workspace_root, env=env)
        else:
            if _request_uses_precomputed(req) and current_precomputed_mode == "only":
                ref = bundle_ref(ROOT, req.mode, req.length_ft, req.width_ft, req=req)
                slug = ref.slug if ref is not None else f"{req.length_ft:g}x{req.width_ft:g}"
                raise RuntimeError(
                    f"Precomputed mode is active, but no matching bundle was found for {req.mode} {slug} "
                    f"in {resolve_precomputed_root(ROOT)}."
                )

            shell_cmd, env = pipeline_shell(req)
            if _request_uses_docker(req):
                ensure_image()
                cmd = _docker_command(shell_cmd, env)
                _run_cmd(cmd, cwd=ROOT, env=env)
                sync_shell = _live_workspace_sync_shell(req, workspace_root, include_visuals=False)
                _run_cmd(_local_command(sync_shell), cwd=ROOT, env=env)
            else:
                sync_shell = _live_workspace_sync_shell(req, workspace_root, include_visuals=False)
                cmd = _local_command(f"{shell_cmd} && {sync_shell}")
                _run_cmd(cmd, cwd=ROOT, env=env)
        commit_staged_workspace(lease, request_runtime_identity(req), req)
    except Exception as exc:
        fail_staged_workspace(lease, str(exc))
        raise
    return lease.committed_workspace
