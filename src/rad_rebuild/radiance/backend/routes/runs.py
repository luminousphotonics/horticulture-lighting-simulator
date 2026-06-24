from __future__ import annotations

import shlex
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from rad_rebuild.radiance.config import (
    MODE_COMPETITOR,
    PRECOMPUTED_DOWNLOAD_COMMAND,
    PRECOMPUTED_FULL_DATASET_SIZE_TEXT,
    PUBLIC_DEFAULT_LENGTH_FT,
    PUBLIC_DEFAULT_WIDTH_FT,
    PUBLIC_PRECOMPUTED_MAX_FT,
    PUBLIC_PRECOMPUTED_MIN_FT,
    RADIANCE_MODE_LABELS,
)
from rad_rebuild.radiance.backend.artifacts import _live_workspace_sync_shell, _visualize_command, _visualize_shell
from rad_rebuild.radiance.backend.env import (
    _apply_visualize_env,
    _canonicalize_mode_request,
    _env_for_mode,
    _make_env_base,
    _output_dir_for_mode,
    _request_uses_docker,
    _request_uses_precomputed,
)
from rad_rebuild.radiance.backend.jobs import (
    JobBackpressureError,
    JobRecord,
    JobService,
    job_service,
    job_service_for_request,
)
from rad_rebuild.radiance.backend.models import (
    LayoutRequest,
    LayoutResponse,
    RadianceRunRequest,
    RadianceRunResponse,
    request_with_updates,
)
from rad_rebuild.radiance.backend.runner import (
    _docker_command,
    _local_command,
    _shell_quote_command,
    ensure_image,
)
from rad_rebuild.radiance.backend.runtime import (
    assert_live_execution_allowed,
    maybe_cleanup_runtime_state,
    request_bool_query_param,
    pipeline_command,
    pipeline_shell,
    precomputed_mode,
    precomputed_request_for_available_bundle,
    request_runtime_identity,
)
from rad_rebuild.radiance.backend.workspace import (
    ROOT,
    artifact_key_from_request,
    allocate_workspace_for_run,
    commit_staged_workspace,
    fail_staged_workspace,
    _session_id_from_request,
)
from rad_rebuild.radiance.backend.routes.contracts import PUBLIC_ERROR_RESPONSES

try:
    from rad_rebuild.radiance.engine.layout.layout_engine import build_layout_payload
    from rad_rebuild.radiance.engine.simulation.precomputed_dataset import bundle_ref, resolve_precomputed_root
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"Failed to import run route dependencies: {e}") from e


router = APIRouter()


def _job_service_for_route(request: Request) -> JobService:
    try:
        app_state = request.app.state
    except (AttributeError, RuntimeError):
        return job_service()
    return getattr(app_state, "job_service", None) or job_service_for_request(request)


def _precomputed_bundle_detail(req: RadianceRunRequest, *, reason: str = "missing") -> dict[str, object]:
    ref = bundle_ref(ROOT, req.mode, req.length_ft, req.width_ft, req=req)
    slug = ref.slug if ref is not None else f"{req.length_ft:g}x{req.width_ft:g}"
    return {
        "error": "precomputed_bundle_missing" if reason == "missing" else "precomputed_dimension_unsupported",
        "title": "Precomputed bundle not installed" if reason == "missing" else "Room size outside public dataset range",
        "message": (
            "The selected precomputed bundle is not installed in this checkout."
            if reason == "missing"
            else f"Public precomputed playback supports whole-foot room dimensions from {PUBLIC_PRECOMPUTED_MIN_FT} ft through {PUBLIC_PRECOMPUTED_MAX_FT} ft."
        ),
        "mode": req.mode,
        "mode_label": RADIANCE_MODE_LABELS.get(req.mode, req.mode),
        "dimensions": {
            "length_ft": req.length_ft,
            "width_ft": req.width_ft,
            "slug": slug,
        },
        "dataset_root": str(resolve_precomputed_root(ROOT)),
        "download_command": PRECOMPUTED_DOWNLOAD_COMMAND,
        "estimated_size": PRECOMPUTED_FULL_DATASET_SIZE_TEXT,
        "demo": {
            "length_ft": PUBLIC_DEFAULT_LENGTH_FT,
            "width_ft": PUBLIC_DEFAULT_WIDTH_FT,
        },
    }


def _validate_public_precomputed_dimensions(req: RadianceRunRequest) -> None:
    values = (float(req.length_ft), float(req.width_ft))
    whole_feet = all(abs(value - round(value)) <= 1e-6 for value in values)
    in_range = all(PUBLIC_PRECOMPUTED_MIN_FT <= value <= PUBLIC_PRECOMPUTED_MAX_FT for value in values)
    if not whole_feet or not in_range:
        raise HTTPException(status_code=422, detail=_precomputed_bundle_detail(req, reason="range"))


_INT_PLANT_QUERY_FIELDS = {
    "plant_seed",
    "plant_rows",
    "plant_columns",
    "plant_leaf_count",
}

_FLOAT_PLANT_QUERY_FIELDS = {
    "plant_spacing_m",
    "plant_height_m",
    "plant_canopy_radius_m",
    "plant_growth_stage",
}


def _query_text(request: Request, name: str) -> str | None:
    raw = request.query_params.get(name)
    if raw is None:
        return None
    value = raw.strip()
    return value if value else None


def _query_int(request: Request, name: str) -> int | None:
    raw = _query_text(request, name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be an integer.") from exc


def _query_float(request: Request, name: str) -> float | None:
    raw = _query_text(request, name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be a number.") from exc


def _apply_run_plant_query_overrides(
    req: RadianceRunRequest,
    request: Request,
) -> RadianceRunRequest:
    updates: dict[str, object] = {}

    if request.query_params.get("plants_enabled") is not None:
        updates["plants_enabled"] = request_bool_query_param(
            request,
            "plants_enabled",
            req.plants_enabled,
        )

    for field_name in _INT_PLANT_QUERY_FIELDS:
        value = _query_int(request, field_name)
        if value is not None:
            updates[field_name] = value

    for field_name in _FLOAT_PLANT_QUERY_FIELDS:
        value = _query_float(request, field_name)
        if value is not None:
            updates[field_name] = value

    if not updates:
        return req

    try:
        return request_with_updates(req, **updates)
    except (TypeError, ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plant query parameter: {exc}",
        ) from exc


@router.post(
    "/layout",
    response_model=LayoutResponse,
    status_code=200,
    operation_id="create_layout",
    responses=PUBLIC_ERROR_RESPONSES,
)
def run_layout(req: LayoutRequest) -> dict[str, Any]:
    maybe_cleanup_runtime_state()
    try:
        return build_layout_payload(
            req.length_ft,
            req.width_ft,
            cv_enabled=req.cv_enabled,
            include_svg=req.include_svg,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/radiance/run",
    response_model=RadianceRunResponse,
    status_code=200,
    operation_id="start_radiance_run",
    responses=PUBLIC_ERROR_RESPONSES,
)
def run_radiance(req: RadianceRunRequest, request: Request) -> dict[str, Any]:
    maybe_cleanup_runtime_state()
    action = req.action.strip().lower()
    if action not in {"uniformity", "competitor", "visualize", "all"}:
        raise HTTPException(status_code=400, detail="Invalid action.")
    req = _canonicalize_mode_request(req)
    req = _apply_run_plant_query_overrides(req, request)
    req = _canonicalize_mode_request(req)
    session_id = _session_id_from_request(request)
    assert_live_execution_allowed(req, session_id)
    current_precomputed_mode = precomputed_mode()
    if action == "competitor":
        req = request_with_updates(req, mode=MODE_COMPETITOR)
        req = _canonicalize_mode_request(req)
    if action in {"uniformity", "competitor", "all"} and _request_uses_precomputed(req):
        _validate_public_precomputed_dimensions(req)
    matched_precomputed_req = (
        precomputed_request_for_available_bundle(req)
        if action in {"uniformity", "competitor", "all"} and _request_uses_precomputed(req)
        else None
    )
    if matched_precomputed_req is not None:
        req = matched_precomputed_req
    workspace_lease = allocate_workspace_for_run(session_id, req)
    workspace_root = workspace_lease.staging_workspace
    use_precomputed = (
        action in {"uniformity", "competitor", "all"}
        and _request_uses_precomputed(req)
        and matched_precomputed_req is not None
    )
    if _request_uses_docker(req) and action != "visualize" and not use_precomputed:
        try:
            ensure_image()
        except Exception as e:
            ref = bundle_ref(ROOT, req.mode, req.length_ft, req.width_ft, req=req)
            slug = ref.slug if ref is not None else f"{req.length_ft:g}x{req.width_ft:g}"
            if current_precomputed_mode == "prefer":
                detail = (
                    f"No precomputed bundle found for {req.mode} {slug} in {resolve_precomputed_root(ROOT)} "
                    f"and Docker is unavailable: {e}"
                )
            else:
                detail = f"Docker image unavailable: {e}"
            raise HTTPException(status_code=503, detail=detail)

    try:
        if action == "visualize":
            env = _make_env_base(req)
            cmd, outdir = _visualize_command(req, env, workspace_root)
            job_cwd = workspace_root
        elif action in {"competitor", "uniformity"}:
            outdir = _output_dir_for_mode(req.mode)
            if use_precomputed:
                cmd, env = pipeline_command(req, workspace_root=workspace_root, session_id=session_id)
                job_cwd = workspace_root
            else:
                if _request_uses_precomputed(req):
                    raise HTTPException(status_code=404, detail=_precomputed_bundle_detail(req))
                run_shell, env = pipeline_shell(req)
                if _request_uses_docker(req):
                    docker_cmd = _docker_command(run_shell, env)
                    sync_shell = _live_workspace_sync_shell(req, workspace_root, include_visuals=False)
                    cmd = _local_command(f"{_shell_quote_command(docker_cmd)} && {sync_shell}")
                else:
                    sync_shell = _live_workspace_sync_shell(req, workspace_root, include_visuals=False)
                    cmd = _local_command(f"{run_shell} && {sync_shell}")
                job_cwd = ROOT
        else:
            if use_precomputed:
                cmd, env = pipeline_command(req, workspace_root=workspace_root, session_id=session_id)
                _apply_visualize_env(env)
                vis_shell, outdir = _visualize_shell(
                    req,
                    base_root=workspace_root,
                    skip_secondary_plots=True,
                    skip_scatter=True,
                )
                playback = " ".join(shlex.quote(part) for part in cmd)
                cmd = _local_command(f"{playback} && {vis_shell}")
                job_cwd = workspace_root
            else:
                if _request_uses_precomputed(req):
                    raise HTTPException(status_code=404, detail=_precomputed_bundle_detail(req))
                run_shell, _ = pipeline_shell(req)
                env = _env_for_mode(req)
                _apply_visualize_env(env)
                vis_shell, outdir = _visualize_shell(
                    req,
                    skip_secondary_plots=True,
                    skip_scatter=True,
                )
                if _request_uses_docker(req):
                    docker_cmd = _docker_command(f"{run_shell} && {vis_shell}", env)
                    sync_shell = _live_workspace_sync_shell(req, workspace_root, include_visuals=True)
                    cmd = _local_command(f"{_shell_quote_command(docker_cmd)} && {sync_shell}")
                else:
                    sync_shell = _live_workspace_sync_shell(req, workspace_root, include_visuals=True)
                    cmd = _local_command(f"{run_shell} && {vis_shell} && {sync_shell}")
                job_cwd = ROOT
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

    runtime_identity = request_runtime_identity(req)

    def finalize_workspace(done_job: JobRecord) -> None:
        if done_job.status == "succeeded":
            commit_staged_workspace(workspace_lease, runtime_identity, req)
        else:
            fail_staged_workspace(workspace_lease, f"job exited with status {done_job.status}")

    try:
        job = _job_service_for_route(request).submit(
            cmd,
            job_cwd,
            env=env,
            owner_session=session_id,
            request_fingerprint=artifact_key_from_request(req),
            kind="radiance_run",
            on_complete=finalize_workspace,
        )
    except JobBackpressureError as exc:
        fail_staged_workspace(workspace_lease, f"job rejected by backpressure: {exc}")
        raise HTTPException(
            status_code=429,
            detail={
                "error": "job_queue_full",
                "message": str(exc),
            },
        ) from exc
    return {
        "job_id": job.id,
        "status": job.status,
        "outdir": outdir,
        "artifact_token": workspace_lease.artifact_token,
    }
