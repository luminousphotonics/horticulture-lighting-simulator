from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from rad_rebuild.radiance.backend.models import RuntimeStatusResponse
from rad_rebuild.radiance.backend.runtime_status import runtime_status_payload
from rad_rebuild.radiance.backend.routes.contracts import PUBLIC_ERROR_RESPONSES


router = APIRouter()


@router.get(
    "/radiance/runtime/status",
    response_model=RuntimeStatusResponse,
    status_code=200,
    operation_id="get_radiance_runtime_status",
    responses=PUBLIC_ERROR_RESPONSES,
)
def radiance_runtime_status() -> dict[str, Any]:
    return runtime_status_payload()

