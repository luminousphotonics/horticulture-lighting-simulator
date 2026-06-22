from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.backend import server  # noqa: E402
from rad_rebuild.radiance.settings import load_settings  # noqa: E402
from rad_rebuild.web.app import create_app  # noqa: E402
from rad_rebuild.web.proxy import RadianceProxyClient  # noqa: E402


async def _asgi_request_async(
    app: Any,
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    events: list[dict[str, Any]] = []
    request_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in {"host": "testserver", **(headers or {})}.items()
    ]
    raw_path, _separator, raw_query = path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": raw_path,
        "raw_path": raw_path.encode("ascii"),
        "query_string": raw_query.encode("ascii"),
        "headers": request_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        events.append(dict(message))

    await app(
        scope,
        receive,
        cast(Callable[[dict[str, Any]], Awaitable[None]], send),
    )
    start = next(event for event in events if event["type"] == "http.response.start")
    chunks: list[bytes] = []
    for event in events:
        if event["type"] == "http.response.body":
            event_body = event.get("body", b"")
            if isinstance(event_body, bytes):
                chunks.append(event_body)
    response_headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start["headers"]
    }
    return int(start["status"]), response_headers, b"".join(chunks)


def _asgi_request(
    app: Any,
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    return asyncio.run(
        _asgi_request_async(app, method, path, body=body, headers=headers)
    )


class _FakeProxy:
    def __init__(self, ready: bool) -> None:
        self.ready = ready
        self.paths: list[str] = []

    def backend_ready(self) -> bool:
        return self.ready

    def proxy(
        self,
        path: str,
        _request: object,
        *,
        stream: bool = False,
        request_id: str | None = None,
    ):
        self.paths.append(f"{path}:{stream}:{request_id is not None}")
        return json.dumps({"path": path, "stream": stream})


class _FakeJobService:
    def __init__(self) -> None:
        self.started = False
        self.shutdown_called = False
        self.recovered = 0

    def recover_orphaned_jobs(self) -> int:
        self.recovered += 1
        return 0

    def start(self) -> None:
        self.started = True

    def shutdown(self) -> None:
        self.shutdown_called = True


class _FakeSocket:
    timeout: float | None = None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout


class _FakeHTTPResponse:
    def __init__(self, body: bytes) -> None:
        self.status = 200
        self._body = body

    def getheaders(self) -> list[tuple[str, str]]:
        return [("Content-Type", "application/json"), ("X-Secret-Internal", "hidden")]

    def read(self, size: int | None = None) -> bytes:
        if size is None:
            size = len(self._body)
        chunk = self._body[:size]
        self._body = self._body[size:]
        return chunk


class _FakeHTTPConnection:
    def __init__(self, response_body: bytes) -> None:
        self.response_body = response_body
        self.sock = _FakeSocket()
        self.closed = False
        self.request_headers_seen: dict[str, str] = {}
        self.request_body = b""

    def request(
        self,
        _method: str,
        _target: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.request_headers_seen = {
            key.lower(): value for key, value in (headers or {}).items()
        }
        self.request_body = body or b""

    def getresponse(self) -> _FakeHTTPResponse:
        return _FakeHTTPResponse(self.response_body)

    def close(self) -> None:
        self.closed = True


class _FakeProxyClient(RadianceProxyClient):
    def __init__(
        self, *, response_body: bytes, max_request_bytes: int, max_response_bytes: int
    ) -> None:
        super().__init__(
            "http://127.0.0.1:1",
            connect_timeout_s=1.0,
            read_timeout_s=1.0,
            stream_read_timeout_s=1.0,
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
        )
        self.connection = _FakeHTTPConnection(response_body)

    def _connection(self, timeout: float) -> Any:
        return self.connection


class Phase08LifecycleTests(unittest.TestCase):
    def test_flask_import_does_not_create_render_disk_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_phase08_import_") as tmp:
            target = Path(tmp) / "render-disk"
            script = (
                "import os, pathlib; "
                f"os.environ['RENDER_DISK_PATH'] = {str(target)!r}; "
                "import rad_rebuild.web.app; "
                f"raise SystemExit(1 if pathlib.Path({str(target)!r}).exists() else 0)"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[2],
                env={"PYTHONPATH": "src", **dict(**__import__("os").environ)},
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_flask_factories_keep_proxy_state_isolated(self) -> None:
        first_proxy = _FakeProxy(ready=True)
        second_proxy = _FakeProxy(ready=False)
        first = create_app(services={"radiance_proxy_client": first_proxy})
        second = create_app(services={"radiance_proxy_client": second_proxy})

        self.assertEqual(first.test_client().get("/ready").status_code, 200)
        self.assertEqual(second.test_client().get("/ready").status_code, 503)

    def test_proxy_has_limits_and_header_allowlists(self) -> None:
        proxy = _FakeProxyClient(
            response_body=b'{"ok": true}', max_request_bytes=8, max_response_bytes=64
        )
        app = create_app(services={"radiance_proxy_client": proxy})
        response = app.test_client().get(
            "/radiance-api/health",
            headers={"X-Request-ID": "a" * 32, "X-Secret-Internal": "leak"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})
        self.assertIn("content-type", {key.lower() for key in response.headers.keys()})
        self.assertNotIn("X-Secret-Internal", response.headers)
        self.assertEqual(
            proxy.connection.request_headers_seen.get("x-request-id"), "a" * 32
        )
        self.assertNotIn("x-secret-internal", proxy.connection.request_headers_seen)
        self.assertTrue(proxy.connection.closed)

    def test_proxy_scatter_and_assembly_viewer_can_be_framed_by_modal(self) -> None:
        proxy = _FakeProxyClient(
            response_body=b"<html></html>", max_request_bytes=8, max_response_bytes=64
        )
        app = create_app(services={"radiance_proxy_client": proxy})

        scatter_response = app.test_client().get("/radiance-api/radiance/scatter")
        viewer_response = app.test_client().get("/viewer/assembly")
        normal_response = app.test_client().get("/radiance-api/health")

        self.assertEqual(scatter_response.status_code, 200)
        self.assertNotIn("X-Frame-Options", scatter_response.headers)
        scatter_csp = scatter_response.headers["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'self'", scatter_csp)
        self.assertIn("script-src 'self' 'unsafe-inline'", scatter_csp)
        self.assertEqual(viewer_response.status_code, 200)
        self.assertNotIn("X-Frame-Options", viewer_response.headers)
        viewer_csp = viewer_response.headers["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'self'", viewer_csp)
        self.assertIn("script-src 'self'", viewer_csp)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", viewer_csp)
        self.assertEqual(normal_response.headers["X-Frame-Options"], "DENY")
        self.assertIn(
            "script-src 'self'",
            normal_response.headers["Content-Security-Policy"],
        )
        self.assertNotIn(
            "script-src 'self' 'unsafe-inline'",
            normal_response.headers["Content-Security-Policy"],
        )

    def test_proxy_rejects_oversized_request_and_response(self) -> None:
        proxy = _FakeProxyClient(
            response_body=b"12345", max_request_bytes=4, max_response_bytes=4
        )
        app = create_app(services={"radiance_proxy_client": proxy})
        too_large_request = app.test_client().post(
            "/radiance-api/health", data=b"12345"
        )
        too_large_response = app.test_client().get("/radiance-api/health")

        self.assertEqual(too_large_request.status_code, 413)
        self.assertEqual(too_large_request.get_json()["error"], "request_too_large")
        self.assertEqual(too_large_response.status_code, 502)
        self.assertEqual(
            too_large_response.get_json()["error"], "radiance_response_too_large"
        )

    def test_fastapi_readiness_tracks_lifespan_dependencies(self) -> None:
        fake_service = _FakeJobService()
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_phase08_backend_") as tmp:
            settings = load_settings({"RADIANCE_OUTPUT_ROOT": tmp})
            backend = server.create_app(
                settings=settings, job_service_factory=cast(Any, lambda: fake_service)
            )

            status_before, _headers, body_before = _asgi_request(
                backend, "GET", "/ready"
            )
            self.assertEqual(status_before, 503)
            self.assertEqual(json.loads(body_before)["status"], "not_ready")

            async def run_lifespan() -> tuple[int, bytes]:
                async with backend.router.lifespan_context(backend):
                    (
                        status_after,
                        _headers_after,
                        body_after,
                    ) = await _asgi_request_async(backend, "GET", "/ready")
                    return status_after, body_after

            status_after, body_after = asyncio.run(run_lifespan())

        self.assertEqual(status_after, 200)
        self.assertEqual(json.loads(body_after)["status"], "ready")
        self.assertTrue(fake_service.started)
        self.assertTrue(fake_service.shutdown_called)

    def test_fastapi_frame_policy_allows_scatter_modal_only(self) -> None:
        self.assertFalse(server._should_apply_frame_options("/radiance/scatter"))
        self.assertTrue(server._should_apply_frame_options("/health"))

    def test_fastapi_openapi_has_health_readiness_and_unique_operation_ids(
        self,
    ) -> None:
        schema = server.create_app().openapi()
        self.assertIn("/health", schema["paths"])
        self.assertIn("/ready", schema["paths"])
        operation_ids = [
            operation["operationId"]
            for path_item in schema["paths"].values()
            for operation in path_item.values()
            if isinstance(operation, dict) and "operationId" in operation
        ]
        self.assertEqual(len(operation_ids), len(set(operation_ids)))


if __name__ == "__main__":
    unittest.main()
