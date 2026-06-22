from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from rad_rebuild.radiance.backend.costs import build_electrical_estimate_payload
from rad_rebuild.radiance.backend.metrics import _metrics_payload_for_request
from rad_rebuild.radiance.backend.models import ElectricalCostRequest, ElectricalEstimateResponse
from rad_rebuild.radiance.backend.routes.contracts import PUBLIC_ERROR_RESPONSES
from rad_rebuild.radiance.backend.runtime import (
    assert_live_execution_allowed,
    ensure_request_workspace,
    maybe_cleanup_runtime_state,
)
from rad_rebuild.radiance.backend.workspace import _session_id_from_request


router = APIRouter()


@router.post(
    "/radiance/electrical-estimate",
    response_model=ElectricalEstimateResponse,
    status_code=200,
    operation_id="create_radiance_electrical_estimate",
    responses=PUBLIC_ERROR_RESPONSES,
)
def radiance_electrical_estimate(req: ElectricalCostRequest, request: Request) -> dict[str, Any]:
    maybe_cleanup_runtime_state()
    session_id = _session_id_from_request(request)
    assert_live_execution_allowed(req, session_id)
    return build_electrical_estimate_payload(
        req,
        session_id,
        ensure_request_workspace,
        _metrics_payload_for_request,
    )
