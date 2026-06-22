from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from rad_rebuild.radiance.backend.jobs import (
    JobNotFoundError,
    JobOwnershipError,
    JobSseLimitError,
    job_service_for_request,
)
from rad_rebuild.radiance.backend.models import JobStatus, JobTailResponse
from rad_rebuild.radiance.backend.runtime import maybe_cleanup_runtime_state
from rad_rebuild.radiance.backend.routes.contracts import PUBLIC_ERROR_RESPONSES
from rad_rebuild.radiance.backend.workspace import _session_id_from_request


router = APIRouter()


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatus,
    status_code=200,
    operation_id="get_job_status",
    responses=PUBLIC_ERROR_RESPONSES,
)
def job_status(job_id: str, request: Request) -> JobStatus:
    maybe_cleanup_runtime_state()
    session_id = _session_id_from_request(request)
    service = job_service_for_request(request)
    try:
        job = service.get(job_id, session_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")
    except JobOwnershipError:
        raise HTTPException(status_code=403, detail="Job belongs to a different session")
    return JobStatus(job_id=job.id, status=job.status, exit_code=job.exit_code)


@router.get(
    "/jobs/{job_id}/tail",
    response_model=JobTailResponse,
    status_code=200,
    operation_id="get_job_tail",
    responses=PUBLIC_ERROR_RESPONSES,
)
def job_tail(job_id: str, request: Request, cursor: int = 0, limit: int = 200) -> dict[str, Any]:
    maybe_cleanup_runtime_state()
    session_id = _session_id_from_request(request)
    service = job_service_for_request(request)
    try:
        tail = service.tail(job_id, cursor=cursor, limit=limit, owner_session=session_id)
        job = service.get(job_id, session_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")
    except JobOwnershipError:
        raise HTTPException(status_code=403, detail="Job belongs to a different session")
    return {
        "job_id": job.id,
        "status": tail.status,
        "lines": tail.lines,
        "next_cursor": tail.next_cursor,
        "done": tail.done,
    }


@router.get(
    "/jobs/{job_id}/logs",
    response_class=StreamingResponse,
    status_code=200,
    operation_id="stream_job_logs",
    responses=PUBLIC_ERROR_RESPONSES,
)
def job_logs(job_id: str, request: Request) -> StreamingResponse:
    maybe_cleanup_runtime_state()
    session_id = _session_id_from_request(request)
    service = job_service_for_request(request)
    try:
        streamer = service.stream(job_id, owner_session=session_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")
    except JobOwnershipError:
        raise HTTPException(status_code=403, detail="Job belongs to a different session")
    except JobSseLimitError:
        raise HTTPException(status_code=429, detail="Too many job log streams")

    return StreamingResponse(
        streamer,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobStatus,
    status_code=200,
    operation_id="cancel_job",
    responses=PUBLIC_ERROR_RESPONSES,
)
def job_cancel(job_id: str, request: Request) -> JobStatus:
    maybe_cleanup_runtime_state()
    session_id = _session_id_from_request(request)
    service = job_service_for_request(request)
    try:
        job = service.cancel(job_id, session_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")
    except JobOwnershipError:
        raise HTTPException(status_code=403, detail="Job belongs to a different session")
    return JobStatus(job_id=job.id, status=job.status, exit_code=job.exit_code)
