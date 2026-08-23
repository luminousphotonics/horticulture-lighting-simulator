"""Public authenticated-precomputed-only ASGI application."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from email.utils import formatdate, parsedate_to_datetime
import hashlib
import json
from importlib import resources
import logging
import os
from pathlib import Path
import re
import time
import uuid

from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from fspm_optics.precomputed.target_adjustment import (
    DEFAULT_PRECOMPUTED_FSPM_TARGET_TOLERANCE_UMOL_M2_S,
)

from .mode import ApplicationMode
from .precomputed import (
    PrecomputedCatalog,
    PrecomputedPlaybackError,
    PrecomputedPlaybackNotFound,
)
from .playback_queue import (
    PlaybackIdempotencyConflict,
    PlaybackIdempotencyInvalid,
    PlaybackLaunchQueue,
    PlaybackQueueFull,
    PlaybackRequestNotFound,
)


MAX_REQUEST_BYTES = 64 * 1024
DEFAULT_MAX_CONCURRENT_WORK = 4
DEFAULT_WORK_WAIT_SECONDS = 5.0
DEFAULT_PLAYBACK_ACTIVE_LIMIT = 4
DEFAULT_PLAYBACK_WAITING_LIMIT = 128
DEFAULT_PLAYBACK_RECORD_LIMIT = 512
DEFAULT_PLAYBACK_RECORD_TTL_SECONDS = 15 * 60.0
DEFAULT_PLAYBACK_EXPIRED_TTL_SECONDS = 60.0
RETRY_AFTER_SECONDS = 2
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
REVALIDATE_CACHE_CONTROL = "public, max-age=0, must-revalidate"
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; object-src 'none'; "
        "frame-ancestors 'none'; form-action 'self'; connect-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self'; "
        "worker-src 'self' blob:"
    ),
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
STATIC_ASSETS = {
    "homeleaf.png": "image/png",
    "og-image.png": "image/png",
    "styles.css": "text/css; charset=utf-8",
    "public-app.js": "text/javascript; charset=utf-8",
    "result-view.js": "text/javascript; charset=utf-8",
    "result-contracts.js": "text/javascript; charset=utf-8",
    "system-labels.js": "text/javascript; charset=utf-8",
}

_SHARED_RESULT_PLACEHOLDER = "        <!-- SHARED_RESULT_VIEW -->"
_SHARED_RESULT_START = "        <!-- SHARED_RESULT_VIEW_START -->"
_SHARED_RESULT_END = "    <!-- SHARED_RESULT_VIEW_END -->"

logger = logging.getLogger("uvicorn.error.fspm_optics.public.http")
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,63}\Z")


def create_public_app(
    *,
    precomputed_root: str | Path | None = None,
) -> Starlette:
    """Build the public app without a runtime, job manager, or native worker."""

    repository_root = Path.cwd().resolve()
    catalog = PrecomputedCatalog(
        precomputed_root or repository_root / "precomputed",
        repository_root=repository_root,
        base_bundle_cache_size=_environment_integer(
            "FSPM_BASE_BUNDLE_CACHE_SIZE", 4, minimum=1, maximum=24
        ),
        playback_cache_size=_environment_integer(
            "FSPM_PLAYBACK_CACHE_SIZE", 8, minimum=1, maximum=64
        ),
        max_sessions=_environment_integer(
            "FSPM_MAX_SESSIONS", 256, minimum=1, maximum=4096
        ),
        session_ttl_seconds=_environment_float(
            "FSPM_SESSION_TTL_SECONDS", 1800.0, minimum=1.0, maximum=86400.0
        ),
    )
    work = _PublicWorkLimiter(
        maximum=_environment_integer(
            "FSPM_MAX_CONCURRENT_WORK",
            DEFAULT_MAX_CONCURRENT_WORK,
            minimum=1,
            maximum=8,
        ),
        wait_seconds=_environment_float(
            "FSPM_WORK_WAIT_SECONDS",
            DEFAULT_WORK_WAIT_SECONDS,
            minimum=0.1,
            maximum=30.0,
        ),
    )

    async def load_queued_playback(
        selector: dict[str, object],
    ) -> dict[str, object]:
        record = await work.run_admitted(
            lambda: catalog.load(selector, initial_pin=False)
        )
        return record.result_payload()

    queue = PlaybackLaunchQueue(
        load_queued_playback,
        active_limit=_environment_integer(
            "FSPM_PLAYBACK_ACTIVE_LIMIT",
            DEFAULT_PLAYBACK_ACTIVE_LIMIT,
            minimum=1,
            maximum=8,
        ),
        waiting_limit=_environment_integer(
            "FSPM_PLAYBACK_WAITING_LIMIT",
            DEFAULT_PLAYBACK_WAITING_LIMIT,
            minimum=1,
            maximum=1024,
        ),
        record_limit=_environment_integer(
            "FSPM_PLAYBACK_RECORD_LIMIT",
            DEFAULT_PLAYBACK_RECORD_LIMIT,
            minimum=2,
            maximum=4096,
        ),
        terminal_ttl_seconds=_environment_float(
            "FSPM_PLAYBACK_RECORD_TTL_SECONDS",
            DEFAULT_PLAYBACK_RECORD_TTL_SECONDS,
            minimum=1.0,
            maximum=86400.0,
        ),
        expired_ttl_seconds=_environment_float(
            "FSPM_PLAYBACK_EXPIRED_TTL_SECONDS",
            DEFAULT_PLAYBACK_EXPIRED_TTL_SECONDS,
            minimum=1.0,
            maximum=3600.0,
        ),
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        logger.info("public startup mode=public_precomputed")
        try:
            await work.run(catalog.initialize)
            await queue.start()
            yield
        finally:
            logger.info("public shutdown started")
            await queue.shutdown()
            work.shutdown()
            catalog.shutdown()
            logger.info("public shutdown complete")

    async def index(request: Request) -> Response:
        return _bytes_response(
            request,
            _public_index(),
            media_type="text/html; charset=utf-8",
            cache_control=REVALIDATE_CACHE_CONTROL,
        )

    async def static_asset(request: Request) -> Response:
        name = request.path_params["asset_name"]
        media_type = STATIC_ASSETS.get(name)
        if media_type is None:
            return _error(404, "not_found", "Static asset not found.")
        path = _web_resource_path(name)
        if path is not None:
            return _file_response(
                request,
                path,
                media_type=media_type,
                cache_control=REVALIDATE_CACHE_CONTROL,
                work=work,
            )
        return _bytes_response(
            request,
            _web_resource(name),
            media_type=media_type,
            cache_control=REVALIDATE_CACHE_CONTROL,
        )

    async def liveness(_request: Request) -> Response:
        return JSONResponse(
            {"status": "alive"}, headers={"Cache-Control": "no-store"}
        )

    async def readiness(_request: Request) -> Response:
        if not catalog.ready:
            return JSONResponse(
                {"status": "not_ready"},
                status_code=503,
                headers={"Cache-Control": "no-store", "Retry-After": "2"},
            )
        queue_stats = await queue.stats()
        return JSONResponse(
            {
                "status": "ready",
                "mode": ApplicationMode.PUBLIC_PRECOMPUTED.value,
                "catalog_cases": catalog.plan.case_count,
                "plan_identity_sha256": catalog.plan.plan_identity_sha256,
                "playback_queue": queue_stats,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def availability(request: Request) -> Response:
        try:
            payload = await work.run(catalog.availability)
        except _PublicOverloaded:
            return _overloaded()
        except (RuntimeError, ValueError):
            logger.exception("catalog availability failed")
            return _error(
                503,
                "precomputed_catalog_unavailable",
                "Authenticated precomputed data is unavailable.",
                headers={"Retry-After": "2"},
            )
        return _json_bytes_response(
            request, payload, cache_control="public, max-age=60"
        )

    async def capabilities(request: Request) -> Response:
        return _json_bytes_response(
            request,
            {
                "application_mode": ApplicationMode.PUBLIC_PRECOMPUTED.value,
                "precomputed_playback": {
                    "enabled": True,
                    "systems": ["proposed", "conventional", "hps"],
                    "fspm_target_tolerance": {
                        "enabled": True,
                        "supported_systems": [
                            "proposed",
                            "conventional",
                            "hps",
                        ],
                        "type": "number",
                        "default": (
                            DEFAULT_PRECOMPUTED_FSPM_TARGET_TOLERANCE_UMOL_M2_S
                        ),
                        "finite": True,
                        "strictly_positive": True,
                    },
                    "lighting_target_modes": {
                        "proposed": ["mean_target", "target_capped"],
                        "conventional": ["mean_target", "target_capped"],
                        "hps": [],
                    },
                    "fixture_layout_modes": {
                        "proposed": [],
                        "conventional": ["rolling_bench", "practical"],
                        "hps": [],
                    },
                },
                "live_simulation": {"enabled": False, "systems": []},
            },
            cache_control="public, max-age=300",
        )

    async def create_playback(request: Request) -> Response:
        payload_or_error = await _json_request_payload(request)
        if isinstance(payload_or_error, Response):
            return payload_or_error
        try:
            if not catalog.ready:
                await work.run(catalog.initialize)
            selector = catalog.prepare_selector(payload_or_error)
            cached = catalog.load_if_cached(selector)
            admission = await queue.submit(
                selector,
                request.headers.get("idempotency-key"),
                immediate_result=(
                    None if cached is None else cached.result_payload()
                ),
            )
        except _PublicOverloaded:
            return _overloaded()
        except PlaybackQueueFull:
            return _error(
                503,
                "playback_queue_full",
                "Playback admission is busy; retry shortly.",
                headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
            )
        except PlaybackIdempotencyInvalid as exc:
            return _error(400, "invalid_idempotency_key", str(exc))
        except PlaybackIdempotencyConflict as exc:
            return _error(409, "idempotency_key_conflict", str(exc))
        except PrecomputedPlaybackError as exc:
            return _playback_error(exc)
        response_state = admission.payload["state"]
        return JSONResponse(
            admission.payload,
            status_code=(
                200
                if response_state in {"completed", "failed", "expired"}
                else 202
            ),
            headers={"Cache-Control": "no-store"},
        )

    async def playback_request_status(request: Request) -> Response:
        try:
            payload = await queue.get(request.path_params["request_id"])
        except PlaybackRequestNotFound:
            return _error(
                404,
                "playback_request_not_found",
                "Playback request not found.",
            )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def metrics(request: Request) -> Response:
        try:
            payload = await work.run(
                lambda: catalog.get(
                    request.path_params["playback_id"]
                ).metrics()
            )
        except _PublicOverloaded:
            return _overloaded()
        except PrecomputedPlaybackNotFound:
            return _error(
                404, "precomputed_playback_not_found", "Playback not found."
            )
        except PrecomputedPlaybackError as exc:
            return _playback_error(exc)
        return _json_bytes_response(
            request, payload, cache_control=IMMUTABLE_CACHE_CONTROL
        )

    async def manifest(request: Request) -> Response:
        try:
            payload = await work.run(
                lambda: catalog.get(
                    request.path_params["playback_id"]
                ).manifest()
            )
        except _PublicOverloaded:
            return _overloaded()
        except PrecomputedPlaybackNotFound:
            return _error(
                404, "precomputed_playback_not_found", "Playback not found."
            )
        except PrecomputedPlaybackError as exc:
            return _playback_error(exc)
        return _json_bytes_response(
            request, payload, cache_control=IMMUTABLE_CACHE_CONTROL
        )

    async def artifact(request: Request) -> Response:
        try:
            item = await work.run(
                lambda: catalog.get(
                    request.path_params["playback_id"]
                ).artifact(request.path_params["artifact_name"])
            )
        except _PublicOverloaded:
            return _overloaded()
        except PrecomputedPlaybackNotFound:
            return _error(
                404, "precomputed_artifact_not_found", "Artifact not found."
            )
        except PrecomputedPlaybackError as exc:
            return _playback_error(exc)
        return _artifact_response(request, item)

    async def viewer_artifact(request: Request) -> Response:
        try:
            result = await work.run(
                _resolve_viewer_response,
                catalog,
                request.path_params["playback_id"],
                request.path_params["viewer_path"],
            )
        except _PublicOverloaded:
            return _overloaded()
        except PrecomputedPlaybackNotFound:
            return _error(
                404,
                "precomputed_viewer_not_found",
                "Viewer artifact not found.",
            )
        except PrecomputedPlaybackError as exc:
            return _playback_error(exc)
        if isinstance(result[0], Path):
            return _file_response(
                request,
                result[0],
                media_type=result[1],
                cache_control=REVALIDATE_CACHE_CONTROL,
                work=work,
            )
        return _artifact_response(request, result[0])

    async def scatter_artifact(request: Request) -> Response:
        try:
            result = await work.run(
                _resolve_scatter_response,
                catalog,
                request.path_params["playback_id"],
                request.path_params["scatter_path"],
            )
        except _PublicOverloaded:
            return _overloaded()
        except PrecomputedPlaybackNotFound:
            return _error(
                404,
                "precomputed_scatter_not_found",
                "Scatter artifact not found.",
            )
        except PrecomputedPlaybackError as exc:
            return _playback_error(exc)
        if isinstance(result[0], Path):
            return _file_response(
                request,
                result[0],
                media_type=result[1],
                cache_control=REVALIDATE_CACHE_CONTROL,
                work=work,
            )
        return _artifact_response(request, result[0])

    async def unexpected_error(_request: Request, _exc: Exception) -> Response:
        logger.exception("unexpected public request failure", exc_info=_exc)
        return _error(
            500, "internal_server_error", "The request could not be completed."
        )

    app = Starlette(
        debug=False,
        lifespan=lifespan,
        exception_handlers={Exception: unexpected_error},
        routes=[
            Route("/", index, methods=["GET"]),
            Route("/static/{asset_name}", static_asset, methods=["GET"]),
            Route("/health/live", liveness, methods=["GET"]),
            Route("/health/ready", readiness, methods=["GET"]),
            Route("/api/capabilities", capabilities, methods=["GET"]),
            Route(
                "/api/precomputed/availability", availability, methods=["GET"]
            ),
            Route(
                "/api/precomputed/playbacks",
                create_playback,
                methods=["POST"],
            ),
            Route(
                "/api/precomputed/playback-requests/{request_id}",
                playback_request_status,
                methods=["GET"],
            ),
            Route(
                "/api/precomputed/playbacks/{playback_id}/metrics",
                metrics,
                methods=["GET"],
            ),
            Route(
                "/api/precomputed/playbacks/{playback_id}/manifest",
                manifest,
                methods=["GET"],
            ),
            Route(
                "/api/precomputed/playbacks/{playback_id}/artifacts/{artifact_name}",
                artifact,
                methods=["GET"],
            ),
            Route(
                "/precomputed/{playback_id}/viewer/{viewer_path:path}",
                viewer_artifact,
                methods=["GET"],
            ),
            Route(
                "/precomputed/{playback_id}/scatter/{scatter_path:path}",
                scatter_artifact,
                methods=["GET"],
            ),
        ],
    )
    app.state.application_mode = ApplicationMode.PUBLIC_PRECOMPUTED
    app.state.precomputed = catalog
    app.state.public_work_limiter = work
    app.state.playback_queue = queue
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=_allowed_hosts(),
        www_redirect=False,
    )
    app.add_middleware(_SecurityObservabilityMiddleware)
    return app


class _PublicOverloaded(RuntimeError):
    pass


class _PublicWorkLimiter:
    def __init__(self, *, maximum: int, wait_seconds: float) -> None:
        self.maximum = maximum
        self.wait_seconds = wait_seconds
        self._semaphore = asyncio.Semaphore(maximum)
        self._executor = ThreadPoolExecutor(
            max_workers=maximum, thread_name_prefix="fspm-public"
        )

    async def run(self, function, *args):
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self.wait_seconds
            )
        except TimeoutError as exc:
            logger.warning("public work rejected because capacity is exhausted")
            raise _PublicOverloaded from exc
        return await self._run_acquired(function, *args)

    async def run_admitted(self, function, *args):
        """Run accepted queue work without a second overload decision."""

        await self._semaphore.acquire()
        return await self._run_acquired(function, *args)

    async def run_file_io(self, function, *args):
        await self._semaphore.acquire()
        return await self._run_acquired(function, *args)

    async def _run_acquired(self, function, *args):
        try:
            future = self._executor.submit(function, *args)
            while not future.done():
                await asyncio.sleep(0.001)
            return future.result()
        finally:
            self._semaphore.release()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


class _FileSliceResponse(Response):
    chunk_size = 64 * 1024

    def __init__(
        self,
        path: Path,
        *,
        start: int,
        end: int,
        status_code: int,
        media_type: str,
        headers: dict[str, str],
        work: _PublicWorkLimiter,
    ) -> None:
        self.path = path
        self.start = start
        self.end = end
        self.status_code = status_code
        self.media_type = media_type
        self.background = None
        self.work = work
        self.init_headers(headers)

    async def __call__(self, scope, receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )
        if scope["method"].upper() == "HEAD":
            await send({"type": "http.response.body", "body": b""})
            return
        source = self.path.open("rb")
        try:
            source.seek(self.start)
            remaining = self.end - self.start
            while remaining > 0:
                data = await self.work.run_file_io(
                    source.read, min(self.chunk_size, remaining)
                )
                if not data:
                    raise RuntimeError("static file ended before its declared size.")
                remaining -= len(data)
                await send(
                    {
                        "type": "http.response.body",
                        "body": data,
                        "more_body": remaining > 0,
                    }
                )
        finally:
            source.close()


class _SecurityObservabilityMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        request_id = _request_id(scope)
        status_code = 500

        async def send_with_headers(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _value in headers}
                for name, value in SECURITY_HEADERS.items():
                    encoded = name.lower().encode("latin-1")
                    if encoded not in existing:
                        headers.append((encoded, value.encode("latin-1")))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            route_path = _route_template(scope.get("path", ""))
            log = logger.debug if route_path.startswith("/health/") else logger.info
            log(
                "request id=%s method=%s route=%s status=%d elapsed_ms=%.3f",
                request_id,
                scope.get("method", ""),
                route_path,
                status_code,
                elapsed_ms,
            )


def _route_template(path: str) -> str:
    if path in {
        "/",
        "/health/live",
        "/health/ready",
        "/api/capabilities",
        "/api/precomputed/availability",
        "/api/precomputed/playbacks",
    }:
        return path
    patterns = (
        (
            re.compile(r"/static/[^/]+\Z"),
            "/static/{asset_name}",
        ),
        (
            re.compile(
                r"/api/precomputed/playback-requests/[0-9a-f]{32}\Z"
            ),
            "/api/precomputed/playback-requests/{request_id}",
        ),
        (
            re.compile(r"/api/precomputed/playbacks/[0-9a-f]{32}/metrics\Z"),
            "/api/precomputed/playbacks/{playback_id}/metrics",
        ),
        (
            re.compile(r"/api/precomputed/playbacks/[0-9a-f]{32}/manifest\Z"),
            "/api/precomputed/playbacks/{playback_id}/manifest",
        ),
        (
            re.compile(
                r"/api/precomputed/playbacks/[0-9a-f]{32}/artifacts/[^/]+\Z"
            ),
            "/api/precomputed/playbacks/{playback_id}/artifacts/{artifact_name}",
        ),
        (
            re.compile(r"/precomputed/[0-9a-f]{32}/viewer/.+\Z"),
            "/precomputed/{playback_id}/viewer/{viewer_path:path}",
        ),
        (
            re.compile(r"/precomputed/[0-9a-f]{32}/scatter/.+\Z"),
            "/precomputed/{playback_id}/scatter/{scatter_path:path}",
        ),
    )
    for pattern, template in patterns:
        if pattern.fullmatch(path):
            return template
    return "<unmatched>"


def _resolve_viewer_response(
    catalog: PrecomputedCatalog,
    playback_id: str,
    relative: str,
):
    record = catalog.get(playback_id)
    static_file = record.viewer_static_file(relative)
    if static_file is not None:
        return static_file
    item = record.viewer_artifact(relative)
    return item, item.media_type


def _resolve_scatter_response(
    catalog: PrecomputedCatalog,
    playback_id: str,
    relative: str,
):
    record = catalog.get(playback_id)
    static_file = record.scatter_static_file(relative)
    if static_file is not None:
        return static_file
    item = record.scatter_artifact(relative)
    return item, item.media_type


async def _json_request_payload(
    request: Request,
) -> dict[str, object] | Response:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip()
    if content_type.lower() != "application/json":
        return _error(
            415,
            "unsupported_media_type",
            "Request Content-Type must be application/json.",
        )
    content_encoding = request.headers.get("content-encoding", "identity").lower()
    if content_encoding != "identity":
        return _error(
            415,
            "unsupported_content_encoding",
            "Compressed request bodies are not supported.",
        )
    length = request.headers.get("content-length")
    if length is not None:
        try:
            parsed_length = int(length)
            if parsed_length < 0:
                raise ValueError
            if parsed_length > MAX_REQUEST_BYTES:
                return _error(
                    413, "request_too_large", "Request body is too large."
                )
        except ValueError:
            return _error(400, "invalid_content_length", "Invalid Content-Length.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_REQUEST_BYTES:
            return _error(413, "request_too_large", "Request body is too large.")
    try:
        payload = json.loads(bytes(body).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error(400, "invalid_json", "Request body must be valid JSON.")
    if not isinstance(payload, dict):
        return _error(
            422, "invalid_request", "Request body must be a JSON object."
        )
    return payload


def _public_index() -> bytes:
    public_source = _web_resource("public-index.html").decode("utf-8")
    live_source = _web_resource("index.html").decode("utf-8")
    if public_source.count(_SHARED_RESULT_PLACEHOLDER) != 1:
        raise RuntimeError("public frontend result-view placeholder is invalid.")
    if (
        live_source.count(_SHARED_RESULT_START) != 1
        or live_source.count(_SHARED_RESULT_END) != 1
    ):
        raise RuntimeError("shared result-view source markers are invalid.")
    start = live_source.index(_SHARED_RESULT_START) + len(_SHARED_RESULT_START)
    end = live_source.index(_SHARED_RESULT_END, start)
    shared_result_view = live_source[start:end].strip("\n")
    source = public_source.replace(
        _SHARED_RESULT_PLACEHOLDER, shared_result_view
    )
    return source.encode("utf-8")


def _web_resource(name: str) -> bytes:
    resource_group = "viewer" if name == "system-labels.js" else "web"
    return (
        resources.files("fspm_optics")
        .joinpath("resources", resource_group, name)
        .read_bytes()
    )


def _web_resource_path(name: str) -> Path | None:
    resource_group = "viewer" if name == "system-labels.js" else "web"
    node = resources.files("fspm_optics").joinpath(
        "resources", resource_group, name
    )
    if not node.is_file():
        return None
    try:
        return Path(node).resolve(strict=True)
    except TypeError:
        return None


def _json_bytes_response(
    request: Request,
    value: object,
    *,
    cache_control: str,
) -> Response:
    data = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return _bytes_response(
        request,
        data,
        media_type="application/json",
        cache_control=cache_control,
    )


def _artifact_response(request: Request, item) -> Response:
    headers: dict[str, str] = {}
    if item.download_name is not None:
        headers["Content-Disposition"] = (
            f'attachment; filename="{item.download_name}"'
        )
    return _bytes_response(
        request,
        item.data,
        media_type=item.media_type,
        cache_control=IMMUTABLE_CACHE_CONTROL,
        headers=headers,
        etag=item.etag,
        allow_range=item.media_type in {
            "application/octet-stream",
            "model/gltf-binary",
        },
    )


def _bytes_response(
    request: Request,
    data: bytes,
    *,
    media_type: str,
    cache_control: str,
    headers: dict[str, str] | None = None,
    etag: str | None = None,
    allow_range: bool = False,
) -> Response:
    etag = etag or f'"{hashlib.sha256(data).hexdigest()}"'
    response_headers = dict(headers or {})
    response_headers.update({"Cache-Control": cache_control, "ETag": etag})
    if allow_range:
        response_headers["Accept-Ranges"] = "bytes"
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=response_headers)
    body = data
    status_code = 200
    if allow_range and request.headers.get("range") is not None:
        try:
            start, end = _single_byte_range(
                request.headers["range"], len(data)
            )
        except ValueError:
            return Response(
                status_code=416,
                headers={
                    **response_headers,
                    "Content-Range": f"bytes */{len(data)}",
                },
            )
        body = data[start:end]
        status_code = 206
        response_headers["Content-Range"] = f"bytes {start}-{end - 1}/{len(data)}"
    return Response(
        body,
        status_code=status_code,
        media_type=media_type,
        headers=response_headers,
    )


def _file_response(
    request: Request,
    path: Path,
    *,
    media_type: str,
    cache_control: str,
    work: _PublicWorkLimiter,
) -> Response:
    stat_result = path.stat()
    etag_base = f"{stat_result.st_mtime}-{stat_result.st_size}"
    etag = f'"{hashlib.md5(etag_base.encode(), usedforsecurity=False).hexdigest()}"'
    last_modified = _http_date(stat_result.st_mtime)
    base_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": cache_control,
        "ETag": etag,
        "Last-Modified": last_modified,
    }
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(
            status_code=304,
            headers=base_headers,
        )
    modified_since = request.headers.get("if-modified-since")
    if modified_since is not None:
        try:
            timestamp = parsedate_to_datetime(modified_since).timestamp()
        except (TypeError, ValueError, OverflowError):
            timestamp = -1.0
        if int(stat_result.st_mtime) <= int(timestamp):
            return Response(status_code=304, headers=base_headers)
    start = 0
    end = stat_result.st_size
    status_code = 200
    range_header = request.headers.get("range")
    if_range = request.headers.get("if-range")
    if range_header is not None and (
        if_range is None or if_range in {etag, last_modified}
    ):
        try:
            start, end = _single_byte_range(range_header, stat_result.st_size)
        except ValueError:
            return Response(
                status_code=416,
                headers={
                    **base_headers,
                    "Content-Range": f"bytes */{stat_result.st_size}",
                },
            )
        status_code = 206
        base_headers["Content-Range"] = (
            f"bytes {start}-{end - 1}/{stat_result.st_size}"
        )
    base_headers["Content-Length"] = str(end - start)
    return _FileSliceResponse(
        path,
        start=start,
        end=end,
        status_code=status_code,
        media_type=media_type,
        headers=base_headers,
        work=work,
    )


def _etag_matches(header: str | None, etag: str) -> bool:
    if header is None:
        return False
    return any(candidate.strip() in {"*", etag} for candidate in header.split(","))


def _http_date(timestamp: float) -> str:
    return formatdate(timestamp, usegmt=True)


def _single_byte_range(header: str, size: int) -> tuple[int, int]:
    if size <= 0 or not header.lower().startswith("bytes="):
        raise ValueError("invalid byte range")
    value = header.partition("=")[2].strip()
    if not value or "," in value or "-" not in value:
        raise ValueError("invalid byte range")
    start_text, end_text = (part.strip() for part in value.split("-", 1))
    try:
        if not start_text:
            suffix = int(end_text)
            if suffix <= 0:
                raise ValueError
            start = max(0, size - suffix)
            end = size
        else:
            start = int(start_text)
            end = size if not end_text else min(size, int(end_text) + 1)
    except ValueError as exc:
        raise ValueError("invalid byte range") from exc
    if start < 0 or start >= size or end <= start:
        raise ValueError("unsatisfiable byte range")
    return start, end


def _request_id(scope) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() != b"x-request-id":
            continue
        try:
            candidate = value.decode("ascii")
        except UnicodeDecodeError:
            break
        if _REQUEST_ID.fullmatch(candidate):
            return candidate
        break
    return uuid.uuid4().hex


def _allowed_hosts() -> list[str]:
    defaults = ["localhost", "127.0.0.1", "[::1]", "testserver", "*.onrender.com"]
    configured = os.environ.get("FSPM_ALLOWED_HOSTS", "")
    if not configured.strip():
        return defaults
    additions = [value.strip().lower() for value in configured.split(",")]
    if any(
        not value
        or value == "*"
        or "://" in value
        or "/" in value
        or " " in value
        or ("*" in value and not value.startswith("*."))
        for value in additions
    ):
        raise ValueError("FSPM_ALLOWED_HOSTS contains an invalid host pattern.")
    return list(dict.fromkeys([*defaults, *additions]))


def _environment_integer(
    name: str, default: int, *, minimum: int, maximum: int
) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _environment_float(
    name: str, default: float, *, minimum: float, maximum: float
) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _overloaded() -> JSONResponse:
    return _error(
        503,
        "service_overloaded",
        "Service is busy; retry shortly.",
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


def _playback_error(exc: PrecomputedPlaybackError) -> JSONResponse:
    headers = (
        {"Retry-After": str(RETRY_AFTER_SECONDS)}
        if exc.status_code == 503
        else None
    )
    return _error(exc.status_code, exc.code, str(exc), headers=headers)


def _error(
    status: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status,
        headers={"Cache-Control": "no-store", **(headers or {})},
    )


__all__ = ["create_public_app"]
