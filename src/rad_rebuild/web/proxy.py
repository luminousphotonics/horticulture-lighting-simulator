from __future__ import annotations

import http.client
import logging
import socket
import urllib.parse
from collections.abc import Iterator

from flask import Response, jsonify, stream_with_context
from werkzeug.wrappers import Request as FlaskRequest


LOGGER = logging.getLogger(__name__)

REQUEST_HEADER_ALLOWLIST = {
    "accept",
    "content-type",
    "x-correlation-id",
    "x-request-id",
}
RESPONSE_HEADER_ALLOWLIST = {
    "cache-control",
    "content-disposition",
    "content-type",
    "etag",
    "last-modified",
    "x-accel-buffering",
}


class ProxyError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _public_error(status: int, code: str, message: str, request_id: str | None) -> Response:
    response = jsonify(
        {
            "ok": False,
            "error": code,
            "message": message,
            "correlation_id": request_id,
        }
    )
    response.status_code = status
    if request_id:
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Correlation-ID"] = request_id
    return response


class RadianceProxyClient:
    def __init__(
        self,
        base_url: str,
        *,
        connect_timeout_s: float,
        read_timeout_s: float,
        stream_read_timeout_s: float,
        max_request_bytes: int,
        max_response_bytes: int,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Radiance proxy base URL must be http(s) with a hostname.")
        self.base_url = base_url.rstrip("/")
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = parsed.port
        self._base_path = parsed.path.rstrip("/")
        self.connect_timeout_s = float(connect_timeout_s)
        self.read_timeout_s = float(read_timeout_s)
        self.stream_read_timeout_s = float(stream_read_timeout_s)
        self.max_request_bytes = int(max_request_bytes)
        self.max_response_bytes = int(max_response_bytes)

    def backend_ready(self) -> bool:
        connection = self._connection(self.connect_timeout_s)
        try:
            connection.request("GET", self._target_path("ready", ""), headers={"Accept": "application/json"})
            response = connection.getresponse()
            response.read(1024)
            return 200 <= response.status < 300
        except OSError:
            return False
        finally:
            connection.close()

    def proxy(self, path: str, request: FlaskRequest, *, stream: bool = False, request_id: str | None = None) -> Response:
        try:
            return self._proxy(path, request, stream=stream, request_id=request_id)
        except ProxyError as exc:
            return _public_error(exc.status_code, exc.code, exc.message, request_id)
        except (OSError, http.client.HTTPException, TimeoutError) as exc:
            LOGGER.warning("Radiance backend proxy failed: %s", exc)
            return _public_error(503, "radiance_backend_unavailable", "Radiance backend is unavailable.", request_id)

    def _proxy(self, path: str, request: FlaskRequest, *, stream: bool, request_id: str | None) -> Response:
        body = request.get_data() if request.method in {"POST", "PUT", "PATCH"} else b""
        if len(body) > self.max_request_bytes:
            raise ProxyError(413, "request_too_large", "Request body is too large.")

        headers = self._request_headers(request, request_id)
        connection = self._connection(self.connect_timeout_s)
        target = self._target_path(path, request.query_string.decode("utf-8", errors="strict"))
        try:
            connection.request(request.method, target, body=body or None, headers=headers)
            connection.sock.settimeout(self.stream_read_timeout_s if stream else self.read_timeout_s)
            upstream = connection.getresponse()
        except Exception:
            connection.close()
            raise

        response_headers = self._response_headers(upstream)
        if not stream:
            try:
                response_body = self._read_limited(upstream)
            finally:
                connection.close()
            return Response(response_body, status=upstream.status, headers=response_headers)

        response_headers.setdefault("Cache-Control", "no-cache")
        response_headers.setdefault("X-Accel-Buffering", "no")
        return Response(
            stream_with_context(self._stream_limited(upstream, connection)),
            status=upstream.status,
            headers=response_headers,
        )

    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        conn_cls: type[http.client.HTTPConnection]
        conn_cls = http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
        return conn_cls(self._host, self._port, timeout=timeout)

    def _target_path(self, path: str, query: str) -> str:
        clean_path = path.lstrip("/")
        target = f"{self._base_path}/{clean_path}" if self._base_path else f"/{clean_path}"
        return f"{target}?{query}" if query else target

    def _request_headers(self, request: FlaskRequest, request_id: str | None) -> dict[str, str]:
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() in REQUEST_HEADER_ALLOWLIST
        }
        if request_id:
            headers["X-Request-ID"] = request_id
            headers["X-Correlation-ID"] = request_id
        return headers

    def _response_headers(self, response: http.client.HTTPResponse) -> dict[str, str]:
        return {
            key: value
            for key, value in response.getheaders()
            if key.lower() in RESPONSE_HEADER_ALLOWLIST
        }

    def _read_limited(self, response: http.client.HTTPResponse) -> bytes:
        body = response.read(self.max_response_bytes + 1)
        if len(body) > self.max_response_bytes:
            raise ProxyError(502, "radiance_response_too_large", "Radiance backend response is too large.")
        return body

    def _stream_limited(self, response: http.client.HTTPResponse, connection: http.client.HTTPConnection) -> Iterator[bytes]:
        total = 0
        try:
            while True:
                try:
                    chunk = response.read(4096)
                except socket.timeout as exc:
                    raise ProxyError(504, "radiance_backend_timeout", "Radiance backend timed out.") from exc
                if not chunk:
                    break
                total += len(chunk)
                if total > self.max_response_bytes:
                    raise ProxyError(502, "radiance_response_too_large", "Radiance backend response is too large.")
                yield chunk
        finally:
            connection.close()
