#!/usr/bin/env python3
from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from rad_rebuild.radiance.config import (
    BACKEND_API_TITLE,
)
from rad_rebuild.radiance.settings import RadianceSettings, get_settings
from rad_rebuild.radiance.backend.jobs import JobService, job_service
from rad_rebuild.radiance.backend.routes import (
    assembly,
    artifacts,
    costs,
    docker,
    health,
    jobs,
    metrics,
    photometrics,
    reproduce,
    runtime_status,
    runs,
)


LOGGER = logging.getLogger(__name__)


def _configured_cors_origins(settings: RadianceSettings) -> list[str]:
    return list(settings.cors_allow_origins)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    service = _app.state.job_service_factory()
    _app.state.job_service = service
    recovered = service.recover_orphaned_jobs()
    if recovered:
        LOGGER.warning("Recovered %s orphaned Radiance jobs on startup.", recovered)
    service.start()
    _app.state.dependencies_ready = True
    try:
        yield
    finally:
        _app.state.dependencies_ready = False
        service.shutdown()


def _correlation_id(request: Request) -> str:
    assigned = getattr(request.state, "request_id", "")
    if isinstance(assigned, str) and len(assigned) == 32 and all(char in "0123456789abcdef" for char in assigned):
        return assigned
    existing = request.headers.get("x-correlation-id", "").strip().lower()
    if len(existing) == 32 and all(char in "0123456789abcdef" for char in existing):
        return existing
    return uuid.uuid4().hex


def _public_error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    detail: object | None = None,
) -> JSONResponse:
    correlation_id = _correlation_id(request)
    payload: dict[str, object] = {
        "ok": False,
        "error": code,
        "message": message,
        "correlation_id": correlation_id,
    }
    if isinstance(detail, dict):
        payload["detail"] = detail
    response = JSONResponse(payload, status_code=status_code)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


def _host_allowed(host_header: str, allowed_hosts: set[str]) -> bool:
    if not host_header:
        return True
    hostname = host_header.rsplit("@", 1)[-1].split(":", 1)[0].strip().lower()
    return hostname in allowed_hosts


def _should_apply_frame_options(path: str) -> bool:
    return path != "/radiance/scatter"


def create_app(
    *,
    settings: RadianceSettings | None = None,
    job_service_factory: Callable[[], JobService] = job_service,
) -> FastAPI:
    active_settings = settings or get_settings()
    backend_app = FastAPI(title=BACKEND_API_TITLE, lifespan=_lifespan)
    backend_app.state.settings = active_settings
    backend_app.state.job_service_factory = job_service_factory
    backend_app.state.job_service = None
    backend_app.state.dependencies_ready = False
    backend_app.state.requests_total = 0

    allowed_hosts = {"127.0.0.1", "localhost", "testserver", active_settings.backend_host.lower()}
    cors_origins = _configured_cors_origins(active_settings)
    if cors_origins:
        backend_app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "HEAD", "OPTIONS"],
            allow_headers=["content-type", "x-correlation-id", "x-request-id"],
        )

    @backend_app.middleware("http")
    async def request_lifecycle_middleware(request: Request, call_next):
        request_id = _correlation_id(request)
        request.state.request_id = request_id
        if not _host_allowed(request.headers.get("host", ""), allowed_hosts):
            return _public_error_response(request, 400, "invalid_host", "Invalid host.")
        raw_length = request.headers.get("content-length")
        if raw_length:
            try:
                content_length = int(raw_length)
            except ValueError:
                return _public_error_response(request, 400, "invalid_request", "Invalid request.")
            if content_length > active_settings.max_request_bytes:
                return _public_error_response(request, 413, "request_too_large", "Request body is too large.")
        response = await call_next(request)
        backend_app.state.requests_total = int(backend_app.state.requests_total) + 1
        response.headers.setdefault("X-Request-ID", request_id)
        response.headers.setdefault("X-Correlation-ID", request_id)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if _should_apply_frame_options(request.url.path):
            response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        return response

    @backend_app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        LOGGER.info("Request validation failed: %s", exc)
        return _public_error_response(request, 422, "invalid_request", "Invalid request.")

    @backend_app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("error") or "request_failed")
            message = str(detail.get("message") or "Request failed.")
            public_detail: object | None = detail
        else:
            status_code = int(exc.status_code)
            if status_code == 403:
                code, message = "forbidden", "Request forbidden."
            elif status_code == 404:
                code, message = "not_found", "Requested resource was not found."
            elif status_code == 422:
                code, message = "invalid_request", "Invalid request."
            elif status_code >= 500:
                code, message = "server_error", "Internal server error."
            else:
                code, message = "request_failed", "Request failed."
            public_detail = None
        return _public_error_response(request, exc.status_code, code, message, detail=public_detail)

    @backend_app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        correlation_id = _correlation_id(request)
        LOGGER.exception("Unhandled request failure correlation_id=%s", correlation_id)
        payload = {
            "ok": False,
            "error": "server_error",
            "message": "Internal server error.",
            "correlation_id": correlation_id,
        }
        response = JSONResponse(payload, status_code=500)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Request-ID"] = correlation_id
        return response

    backend_app.include_router(health.router)
    backend_app.include_router(docker.router)
    backend_app.include_router(runs.router)
    backend_app.include_router(artifacts.router)
    backend_app.include_router(assembly.router)
    backend_app.include_router(photometrics.router)
    backend_app.include_router(metrics.router)
    backend_app.include_router(costs.router)
    backend_app.include_router(reproduce.router)
    backend_app.include_router(runtime_status.router)
    backend_app.include_router(jobs.router)
    return backend_app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "rad_rebuild.radiance.backend.server:create_app",
        host=settings.backend_host,
        port=settings.backend_port,
        factory=True,
    )


if __name__ == "__main__":
    main()
