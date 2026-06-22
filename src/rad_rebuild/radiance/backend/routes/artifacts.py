from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import ValidationError

from rad_rebuild.radiance.config import (
    COMPETITOR_LAYOUT_FULL,
    DEFAULT_EXECUTION_MODE,
    MODE_SMD,
    PUBLIC_DEFAULT_LENGTH_FT,
    PUBLIC_DEFAULT_WIDTH_FT,
)
from rad_rebuild.radiance.backend.artifacts import (
    _ensure_scatter,
    _ensure_visuals,
    _get_or_build_manifest,
    _normalize_visualization_dir_refs,
    _write_ppfd_csv,
)
from rad_rebuild.radiance.backend.env import (
    _canonicalize_mode_request,
    _env_for_mode,
    _normalize_mode,
    _request_uses_precomputed,
    _resolve_output_dir_path,
)
from rad_rebuild.radiance.backend.models import (
    RadianceImagesResponse,
    RadianceManifestResponse,
    RadianceRunRequest,
)
from rad_rebuild.radiance.backend.routes.contracts import PUBLIC_ERROR_RESPONSES
from rad_rebuild.radiance.backend.runtime import (
    maybe_cleanup_runtime_state,
    precomputed_request_for_available_bundle,
    request_bool_query_param,
)
from rad_rebuild.radiance.backend.workspace import (
    _artifact_token_from_request,
    _session_id_from_request,
    authorize_workspace_from_request,
)

try:
    from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
        DEFAULT_COVERAGE_FT as DEFAULT_HPS_COVERAGE_FT,
        DEFAULT_IES_VARIANT as DEFAULT_HPS_IES_VARIANT,
        DEFAULT_MOUNT_Z_M as DEFAULT_HPS_MOUNT_Z_M,
    )
    from rad_rebuild.radiance.engine.simulation.basis_backends import (
        DEFAULT_SMD_BASIS_BACKEND,
        validate_basis_backend_request,
    )
    from rad_rebuild.radiance.engine.simulation.precomputed_dataset import canonical_competitor_layout
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"Failed to import artifact route dependencies: {e}") from e


router = APIRouter()


def _artifact_w_min(mode: str, w_min: float | None) -> float:
    if w_min is not None:
        return w_min
    return 0.0 if mode == MODE_SMD else 10.0


def _route_radiance_request(**kwargs: object) -> RadianceRunRequest:
    try:
        return RadianceRunRequest.model_validate(kwargs)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/radiance/manifest",
    response_model=RadianceManifestResponse,
    status_code=200,
    operation_id="get_radiance_manifest",
    responses=PUBLIC_ERROR_RESPONSES,
)
def radiance_manifest(req: RadianceRunRequest, request: Request) -> dict[str, Any]:
    maybe_cleanup_runtime_state()
    req = _canonicalize_mode_request(req)
    matched_req = precomputed_request_for_available_bundle(req) if _request_uses_precomputed(req) else None
    if matched_req is not None:
        req = matched_req
    workspace_root = authorize_workspace_from_request(request, req)
    env = _env_for_mode(req)

    ppfd_txt = workspace_root / "ppfd_map.txt"
    if not ppfd_txt.exists():
        raise HTTPException(status_code=404, detail="ppfd_map.txt not found.")

    outdir = _ensure_visuals(req, env, workspace_root)
    manifest, manifest_path = _get_or_build_manifest(req, env, outdir, workspace_root)
    manifest = cast(dict[str, object], _normalize_visualization_dir_refs(manifest, req.mode))
    return {
        "manifest": manifest,
        "path": str(manifest_path.relative_to(workspace_root)),
    }


@router.get(
    "/radiance/images",
    response_model=RadianceImagesResponse,
    status_code=200,
    operation_id="get_radiance_images",
    responses=PUBLIC_ERROR_RESPONSES,
)
def radiance_images(
    request: Request,
    mode: str = MODE_SMD,
    execution_mode: str = DEFAULT_EXECUTION_MODE,
    sim_mode: str = "standard",
    target_ppfd: float = 1000.0,
    peak_capping_enabled: bool = False,
    length_ft: float = float(PUBLIC_DEFAULT_LENGTH_FT),
    width_ft: float = float(PUBLIC_DEFAULT_WIDTH_FT),
    w_min: float | None = None,
    w_max: float = 100.0,
    competitor_layout: str = COMPETITOR_LAYOUT_FULL,
    hps_coverage_ft: float = DEFAULT_HPS_COVERAGE_FT,
    hps_ies_variant: str = DEFAULT_HPS_IES_VARIANT,
    mount_z_m: float = 0.4572,
    sp_z_m: float = 0.4572,
    hps_z_m: float = DEFAULT_HPS_MOUNT_Z_M,
    basis_backend: str = DEFAULT_SMD_BASIS_BACKEND,
) -> dict[str, Any]:
    maybe_cleanup_runtime_state()
    mode = _normalize_mode(mode)
    peak_capping_enabled = request_bool_query_param(request, "peak_capping_enabled", peak_capping_enabled)
    try:
        basis_backend = validate_basis_backend_request(mode, basis_backend, variable_mode="rings")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session_id = _session_id_from_request(request)
    competitor_layout = canonical_competitor_layout(competitor_layout)
    req = _canonicalize_mode_request(
        _route_radiance_request(
            action="visualize",
            mode=mode,
            execution_mode=execution_mode,
            sim_mode=sim_mode,
            target_ppfd=target_ppfd,
            peak_capping_enabled=peak_capping_enabled,
            length_ft=length_ft,
            width_ft=width_ft,
            w_min=_artifact_w_min(mode, w_min),
            w_max=w_max,
            competitor_layout=competitor_layout,
            hps_coverage_ft=hps_coverage_ft,
            hps_ies_variant=hps_ies_variant,
            mount_z_m=mount_z_m,
            sp_z_m=sp_z_m,
            hps_z_m=hps_z_m,
            basis_backend=basis_backend,
        )
    )
    matched_req = precomputed_request_for_available_bundle(req) if _request_uses_precomputed(req) else None
    if matched_req is not None:
        req = matched_req
    workspace_root = authorize_workspace_from_request(request, req)
    outdir = _resolve_output_dir_path(req.mode, workspace_root)
    if (workspace_root / "ppfd_map.txt").exists():
        outdir = _ensure_visuals(req, _env_for_mode(req), workspace_root)

    def _img(name: str) -> str | None:
        path = outdir / name
        if not path.exists():
            return None
        mtime_ns = int(path.stat().st_mtime_ns)
        artifact_token = _artifact_token_from_request(request)
        token_query = f"&artifact_token={artifact_token}" if artifact_token else ""
        return (
            f"/radiance/image?mode={req.mode}&execution_mode={req.execution_mode}&sim_mode={req.sim_mode}&name={name}&session_id={session_id}"
            f"&length_ft={req.length_ft:g}&width_ft={req.width_ft:g}&target_ppfd={req.target_ppfd:g}&peak_capping_enabled={str(req.peak_capping_enabled).lower()}"
            f"&w_min={req.w_min:g}&w_max={req.w_max:g}"
            f"&hps_coverage_ft={req.hps_coverage_ft:g}&competitor_layout={req.competitor_layout}"
            f"&hps_ies_variant={req.hps_ies_variant}"
            f"&mount_z_m={req.mount_z_m:g}&sp_z_m={req.sp_z_m:g}&hps_z_m={req.hps_z_m:g}"
            f"&basis_backend={req.basis_backend}{token_query}&v={mtime_ns}"
        )

    return {
        "overlay": _img("ppfd_heatmap_overlay.png"),
        "annot": _img("ppfd_heatmap_annotated.png"),
    }


@router.get(
    "/radiance/image",
    response_class=FileResponse,
    status_code=200,
    operation_id="get_radiance_image",
    responses=PUBLIC_ERROR_RESPONSES,
)
def radiance_image(
    request: Request,
    mode: str,
    name: str,
    execution_mode: str = DEFAULT_EXECUTION_MODE,
    sim_mode: str = "standard",
    target_ppfd: float = 1000.0,
    peak_capping_enabled: bool = False,
    length_ft: float = float(PUBLIC_DEFAULT_LENGTH_FT),
    width_ft: float = float(PUBLIC_DEFAULT_WIDTH_FT),
    w_min: float | None = None,
    w_max: float = 100.0,
    competitor_layout: str = COMPETITOR_LAYOUT_FULL,
    hps_coverage_ft: float = DEFAULT_HPS_COVERAGE_FT,
    hps_ies_variant: str = DEFAULT_HPS_IES_VARIANT,
    mount_z_m: float = 0.4572,
    sp_z_m: float = 0.4572,
    hps_z_m: float = DEFAULT_HPS_MOUNT_Z_M,
    basis_backend: str = DEFAULT_SMD_BASIS_BACKEND,
) -> FileResponse:
    maybe_cleanup_runtime_state()
    mode = _normalize_mode(mode)
    peak_capping_enabled = request_bool_query_param(request, "peak_capping_enabled", peak_capping_enabled)
    try:
        basis_backend = validate_basis_backend_request(mode, basis_backend, variable_mode="rings")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    competitor_layout = canonical_competitor_layout(competitor_layout)
    req = _canonicalize_mode_request(
        _route_radiance_request(
            action="visualize",
            mode=mode,
            execution_mode=execution_mode,
            sim_mode=sim_mode,
            target_ppfd=target_ppfd,
            peak_capping_enabled=peak_capping_enabled,
            length_ft=length_ft,
            width_ft=width_ft,
            w_min=_artifact_w_min(mode, w_min),
            w_max=w_max,
            competitor_layout=competitor_layout,
            hps_coverage_ft=hps_coverage_ft,
            hps_ies_variant=hps_ies_variant,
            mount_z_m=mount_z_m,
            sp_z_m=sp_z_m,
            hps_z_m=hps_z_m,
            basis_backend=basis_backend,
        )
    )
    matched_req = precomputed_request_for_available_bundle(req) if _request_uses_precomputed(req) else None
    if matched_req is not None:
        req = matched_req
    workspace_root = authorize_workspace_from_request(request, req)
    outdir = _resolve_output_dir_path(req.mode, workspace_root)
    safe = Path(name).name
    if safe != name:
        raise HTTPException(status_code=400, detail="Invalid image name.")
    path = outdir / safe
    if (workspace_root / "ppfd_map.txt").exists():
        outdir = _ensure_visuals(req, _env_for_mode(req), workspace_root)
        path = outdir / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found.")
    return FileResponse(
        path,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get(
    "/radiance/ppfd-csv",
    response_class=FileResponse,
    status_code=200,
    operation_id="get_radiance_ppfd_csv",
    responses=PUBLIC_ERROR_RESPONSES,
)
def radiance_ppfd_csv(
    request: Request,
    mode: str = MODE_SMD,
    execution_mode: str = DEFAULT_EXECUTION_MODE,
    sim_mode: str = "standard",
    target_ppfd: float = 1000.0,
    peak_capping_enabled: bool = False,
    length_ft: float = float(PUBLIC_DEFAULT_LENGTH_FT),
    width_ft: float = float(PUBLIC_DEFAULT_WIDTH_FT),
    w_min: float | None = None,
    w_max: float = 100.0,
    competitor_layout: str = COMPETITOR_LAYOUT_FULL,
    hps_coverage_ft: float = DEFAULT_HPS_COVERAGE_FT,
    hps_ies_variant: str = DEFAULT_HPS_IES_VARIANT,
    mount_z_m: float = 0.4572,
    sp_z_m: float = 0.4572,
    hps_z_m: float = DEFAULT_HPS_MOUNT_Z_M,
    basis_backend: str = DEFAULT_SMD_BASIS_BACKEND,
) -> FileResponse:
    maybe_cleanup_runtime_state()
    mode = _normalize_mode(mode)
    peak_capping_enabled = request_bool_query_param(request, "peak_capping_enabled", peak_capping_enabled)
    try:
        basis_backend = validate_basis_backend_request(mode, basis_backend, variable_mode="rings")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    competitor_layout = canonical_competitor_layout(competitor_layout)
    req = _canonicalize_mode_request(
        _route_radiance_request(
            action="visualize",
            mode=mode,
            execution_mode=execution_mode,
            sim_mode=sim_mode,
            target_ppfd=target_ppfd,
            peak_capping_enabled=peak_capping_enabled,
            length_ft=length_ft,
            width_ft=width_ft,
            w_min=_artifact_w_min(mode, w_min),
            w_max=w_max,
            competitor_layout=competitor_layout,
            hps_coverage_ft=hps_coverage_ft,
            hps_ies_variant=hps_ies_variant,
            mount_z_m=mount_z_m,
            sp_z_m=sp_z_m,
            hps_z_m=hps_z_m,
            basis_backend=basis_backend,
        )
    )
    matched_req = precomputed_request_for_available_bundle(req) if _request_uses_precomputed(req) else None
    if matched_req is not None:
        req = matched_req
    workspace_root = authorize_workspace_from_request(request, req)
    ppfd_txt = workspace_root / "ppfd_map.txt"
    if not ppfd_txt.exists():
        raise HTTPException(status_code=404, detail="ppfd_map.txt not found.")
    outdir = _resolve_output_dir_path(req.mode, workspace_root)
    csv_path = _write_ppfd_csv(ppfd_txt, outdir)
    return FileResponse(
        csv_path,
        media_type="text/csv; charset=utf-8",
        filename="ppfd_map.csv",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get(
    "/radiance/scatter",
    response_class=Response,
    status_code=200,
    operation_id="get_radiance_scatter",
    responses=PUBLIC_ERROR_RESPONSES,
)
@router.head("/radiance/scatter", include_in_schema=False)
def radiance_scatter(
    request: Request,
    mode: str = MODE_SMD,
    execution_mode: str = DEFAULT_EXECUTION_MODE,
    sim_mode: str = "standard",
    target_ppfd: float = 1000.0,
    peak_capping_enabled: bool = False,
    length_ft: float = float(PUBLIC_DEFAULT_LENGTH_FT),
    width_ft: float = float(PUBLIC_DEFAULT_WIDTH_FT),
    w_min: float | None = None,
    w_max: float = 100.0,
    competitor_layout: str = COMPETITOR_LAYOUT_FULL,
    hps_coverage_ft: float = DEFAULT_HPS_COVERAGE_FT,
    hps_ies_variant: str = DEFAULT_HPS_IES_VARIANT,
    mount_z_m: float = 0.4572,
    sp_z_m: float = 0.4572,
    hps_z_m: float = DEFAULT_HPS_MOUNT_Z_M,
    basis_backend: str = DEFAULT_SMD_BASIS_BACKEND,
) -> FileResponse:
    maybe_cleanup_runtime_state()
    mode = _normalize_mode(mode)
    peak_capping_enabled = request_bool_query_param(request, "peak_capping_enabled", peak_capping_enabled)
    req = _canonicalize_mode_request(
        _route_radiance_request(
            action="visualize",
            mode=mode,
            execution_mode=execution_mode,
            sim_mode=sim_mode,
            target_ppfd=target_ppfd,
            peak_capping_enabled=peak_capping_enabled,
            length_ft=length_ft,
            width_ft=width_ft,
            w_min=_artifact_w_min(mode, w_min),
            w_max=w_max,
            competitor_layout=competitor_layout,
            hps_coverage_ft=hps_coverage_ft,
            hps_ies_variant=hps_ies_variant,
            mount_z_m=mount_z_m,
            sp_z_m=sp_z_m,
            hps_z_m=hps_z_m,
            basis_backend=basis_backend,
        )
    )
    matched_req = precomputed_request_for_available_bundle(req) if _request_uses_precomputed(req) else None
    if matched_req is not None:
        req = matched_req
    workspace_root = authorize_workspace_from_request(request, req)
    ppfd_map = workspace_root / "ppfd_map.txt"
    if not ppfd_map.exists():
        raise HTTPException(status_code=404, detail="ppfd_map.txt not found.")
    env = _env_for_mode(req)
    path = _ensure_scatter(req, env, workspace_root)
    if not path.exists():
        raise HTTPException(status_code=409, detail="Scatter artifact was not generated.")
    if request.method == "HEAD":
        return cast(
            FileResponse,
            Response(
                status_code=200,
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            ),
        )
    return FileResponse(
        path,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
