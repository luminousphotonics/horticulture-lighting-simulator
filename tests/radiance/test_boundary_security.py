from __future__ import annotations

import asyncio
import json
import os
import unittest
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, cast
from unittest.mock import patch

from fastapi import HTTPException, Request

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.backend import server  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.backend.routes import runs as runs_route  # noqa: E402
from rad_rebuild.web.app import app as flask_app  # noqa: E402


class _FakeRequest:
    headers: dict[str, str] = {}

    def __init__(self, session_id: str = "boundary-security") -> None:
        self.query_params = {"session_id": session_id}


def _asgi_request(
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    async def run() -> tuple[int, dict[str, str], bytes]:
        events: list[dict[str, Any]] = []
        request_headers = [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in (headers or {}).items()
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
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

        await server.app(
            scope,
            receive,
            cast(Callable[[MutableMapping[str, Any]], Awaitable[None]], send),
        )
        start = next(
            event for event in events if event["type"] == "http.response.start"
        )
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

    return asyncio.run(run())


class BoundarySecurityTests(unittest.TestCase):
    def test_fastapi_rejects_non_finite_and_extreme_json_numbers(self) -> None:
        for payload in (
            '{"action":"all","length_ft":NaN,"width_ft":10}',
            '{"action":"all","length_ft":Infinity,"width_ft":10}',
            '{"action":"all","length_ft":1e300,"width_ft":10}',
        ):
            with self.subTest(payload=payload):
                status, _headers, raw_body = _asgi_request(
                    "POST",
                    "/radiance/run",
                    body=payload.encode("utf-8"),
                    headers={"content-type": "application/json"},
                )

                self.assertEqual(status, 422)
                body = json.loads(raw_body)
                self.assertEqual(body["error"], "invalid_request")
                self.assertEqual(body["message"], "Invalid request.")
                self.assertRegex(body["correlation_id"], r"^[0-9a-f]{32}$")

    def test_fastapi_rejects_unknown_extra_fields_and_bad_enums(self) -> None:
        status, _headers, raw_body = _asgi_request(
            "POST",
            "/radiance/run",
            body=json.dumps(
                {
                    "action": "all",
                    "length_ft": 10,
                    "width_ft": 10,
                    "execution_mode": "live_magic",
                    "surprise": "field",
                }
            ).encode("utf-8"),
            headers={"content-type": "application/json"},
        )

        self.assertEqual(status, 422)
        body = json.loads(raw_body)
        self.assertEqual(body["error"], "invalid_request")
        self.assertEqual(body["message"], "Invalid request.")

    def test_live_execution_is_disabled_by_default(self) -> None:
        req = RadianceRunRequest(
            action="all",
            length_ft=10,
            width_ft=10,
            execution_mode="live_local",
        )

        with self.assertRaises(HTTPException) as raised:
            runs_route.run_radiance(req, cast(Request, _FakeRequest()))

        self.assertEqual(raised.exception.status_code, 403)
        detail = cast(dict[str, object], raised.exception.detail)
        self.assertEqual(detail["error"], "live_execution_disabled")
        self.assertEqual(detail["message"], "Live execution is disabled.")

    def test_default_cors_does_not_allow_wildcard_or_untrusted_origin(self) -> None:
        _status, headers, _raw_body = _asgi_request(
            "OPTIONS",
            "/health",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "GET",
            },
        )

        self.assertNotEqual(headers.get("access-control-allow-origin"), "*")
        self.assertNotIn("access-control-allow-origin", headers)

    def test_fastapi_healthz_is_lightweight_json(self) -> None:
        status, _headers, raw_body = _asgi_request("GET", "/healthz")

        self.assertEqual(status, 200)
        body = json.loads(raw_body)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["backend"], "horticulture-lighting-simulator-api")

    def test_flask_layout_api_rejects_non_finite_and_out_of_policy_dimensions(
        self,
    ) -> None:
        client = flask_app.test_client()
        for payload in (
            {"length_ft": "NaN", "width_ft": "10"},
            {"length_ft": "Infinity", "width_ft": "10"},
            {"length_ft": "1e300", "width_ft": "10"},
            {"length_ft": "-0", "width_ft": "10"},
            {"length_ft": "31", "width_ft": "10"},
        ):
            with (
                self.subTest(payload=payload),
                patch.dict(os.environ, {"RAD_REBUILD_SHOW_LIVE_MODES": ""}),
            ):
                response = client.post("/api/layout-generator", json=payload)

                self.assertEqual(response.status_code, 400)
                body = response.get_json()
                self.assertEqual(body["error"], "invalid_request")
                self.assertEqual(body["message"], "Invalid request.")
                self.assertRegex(body["correlation_id"], r"^[0-9a-f]{32}$")


if __name__ == "__main__":
    unittest.main()
