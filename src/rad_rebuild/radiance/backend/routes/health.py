from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict

from rad_rebuild.radiance.config import (
    BACKEND_SERVICE_NAME,
    BACKEND_VERSION,
)


router = APIRouter()


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    backend: str
    version: str


class ReadinessResponse(HealthResponse):
    dependencies: dict[str, str]


class MetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    requests_total: int
    dependencies_ready: bool


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_health",
)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", backend=BACKEND_SERVICE_NAME, version=BACKEND_VERSION)


@router.get(
    "/healthz",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def healthz() -> HealthResponse:
    return await health()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    status_code=status.HTTP_200_OK,
    responses={503: {"model": ReadinessResponse}},
    operation_id="get_readiness",
)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    dependencies_ready = bool(getattr(request.app.state, "dependencies_ready", False))
    if not dependencies_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if dependencies_ready else "not_ready",
        backend=BACKEND_SERVICE_NAME,
        version=BACKEND_VERSION,
        dependencies={"job_service": "ready" if dependencies_ready else "not_ready"},
    )


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_backend_metrics",
)
async def metrics(request: Request) -> MetricsResponse:
    return MetricsResponse(
        service=BACKEND_SERVICE_NAME,
        requests_total=int(getattr(request.app.state, "requests_total", 0)),
        dependencies_ready=bool(getattr(request.app.state, "dependencies_ready", False)),
    )
