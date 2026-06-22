from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi import HTTPException
from pydantic import ValidationError

from rad_rebuild.radiance.config import (
    COMPETITOR_LAYOUT_FULL,
    DEFAULT_EXECUTION_MODE,
    MODE_SMD,
    PUBLIC_DEFAULT_LENGTH_FT,
    PUBLIC_DEFAULT_WIDTH_FT,
)
from rad_rebuild.radiance.backend.env import (
    _canonicalize_mode_request,
    _normalize_mode,
    _request_uses_precomputed,
)
from rad_rebuild.radiance.backend.metrics import get_metrics_payload
from rad_rebuild.radiance.backend.models import RadianceMetricsResponse, RadianceRunRequest
from rad_rebuild.radiance.backend.routes.contracts import PUBLIC_ERROR_RESPONSES
from rad_rebuild.radiance.backend.runtime import (
    maybe_cleanup_runtime_state,
    precomputed_request_for_available_bundle,
    request_bool_query_param,
)
from rad_rebuild.radiance.backend.workspace import authorize_workspace_from_request

try:
    from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
        DEFAULT_COVERAGE_FT as DEFAULT_HPS_COVERAGE_FT,
        DEFAULT_IES_VARIANT as DEFAULT_HPS_IES_VARIANT,
        DEFAULT_MOUNT_Z_M as DEFAULT_HPS_MOUNT_Z_M,
    )
    from rad_rebuild.radiance.engine.simulation.basis_backends import DEFAULT_SMD_BASIS_BACKEND
    from rad_rebuild.radiance.engine.simulation.precomputed_dataset import canonical_competitor_layout
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"Failed to import metrics route dependencies: {e}") from e


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


@router.get(
    "/radiance/metrics",
    response_model=RadianceMetricsResponse,
    status_code=200,
    operation_id="get_radiance_metrics",
    responses=PUBLIC_ERROR_RESPONSES,
)
def radiance_metrics(
    request: Request,
    mode: str = MODE_SMD,
    execution_mode: str = DEFAULT_EXECUTION_MODE,
    sim_mode: str = "standard",
    target_ppfd: float | None = None,
    peak_capping_enabled: bool = False,
    length_ft: float | None = None,
    width_ft: float | None = None,
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

    req_length = length_ft if length_ft is not None else float(PUBLIC_DEFAULT_LENGTH_FT)
    req_width = width_ft if width_ft is not None else float(PUBLIC_DEFAULT_WIDTH_FT)
    req_target = target_ppfd if target_ppfd is not None else 1000.0
    competitor_layout = canonical_competitor_layout(competitor_layout)
    req = _canonicalize_mode_request(
        _route_radiance_request(
            action="metrics",
            mode=mode,
            execution_mode=execution_mode,
            sim_mode=sim_mode,
            length_ft=req_length,
            width_ft=req_width,
            w_min=_artifact_w_min(mode, w_min),
            w_max=w_max,
            target_ppfd=req_target,
            peak_capping_enabled=peak_capping_enabled,
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
    return get_metrics_payload(req, workspace_root)
