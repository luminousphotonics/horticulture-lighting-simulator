from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from rad_rebuild.radiance.backend.jobs import JobBackpressureError, job_service_for_request
from rad_rebuild.radiance.backend.models import ReproduceResponse
from rad_rebuild.radiance.backend.runner import _use_docker, ensure_image, reproduce_command
from rad_rebuild.radiance.backend.routes.contracts import PUBLIC_ERROR_RESPONSES
from rad_rebuild.radiance.backend.workspace import ARTIFACTS, ROOT, _session_id_from_request


router = APIRouter()


@router.post(
    "/reproduce",
    response_model=ReproduceResponse,
    status_code=200,
    operation_id="start_reproduce",
    responses=PUBLIC_ERROR_RESPONSES,
)
def run_reproduce(request: Request) -> dict[str, Any]:
    if not _use_docker():
        raise HTTPException(status_code=400, detail="Reproduce requires Docker. Set RADIANCE_USE_DOCKER=1.")
    try:
        ensure_image()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Docker build failed. Ensure Docker Desktop is running.\n{e}",
        )
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        cmd = reproduce_command(ARTIFACTS)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    try:
        job = job_service_for_request(request).submit(
            cmd,
            ROOT,
            owner_session=_session_id_from_request(request),
            request_fingerprint="reproduce",
            kind="reproduce",
        )
    except JobBackpressureError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "job_queue_full",
                "message": str(exc),
            },
        ) from exc
    return {"job_id": job.id, "status": job.status}
