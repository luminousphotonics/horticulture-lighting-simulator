from __future__ import annotations

import json
import logging
import math
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from flask import Blueprint, Flask, Response, current_app, g, jsonify, redirect, render_template, request, url_for

from rad_rebuild.radiance.engine.layout.domain import ModuleTuple, PointTuple
from rad_rebuild.radiance.engine.layout.layout_engine import (
    build_layout_payload,
    build_layout_payload_from_layout,
    build_layout_png,
)
from rad_rebuild.radiance.engine.layout.layout_generator import generate_layout
from rad_rebuild.radiance.config import (
    ACTIVE_RADIANCE_MODE_OPTIONS,
    DEFAULT_BACKEND_HOST,
    DEFAULT_BACKEND_PORT,
    DEFAULT_EXECUTION_MODE,
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_WEB_PORT,
    ENV_RADIANCE_MAX_REQUEST_BYTES,
    ENV_RESEARCH_API_BASE,
    ENV_RESEARCH_BACKEND_HOST,
    ENV_RESEARCH_BACKEND_PORT,
    ENV_RESEARCH_WEB_PORT,
    EXECUTION_MODE_OPTIONS,
    PRECOMPUTED_DOWNLOAD_COMMAND,
    PRECOMPUTED_FULL_DATASET_SIZE_TEXT,
    PUBLIC_DEFAULT_LENGTH_FT,
    PUBLIC_DEFAULT_WIDTH_FT,
    PUBLIC_EXECUTION_MODE_OPTIONS,
    PUBLIC_PRECOMPUTED_MAX_FT,
    PUBLIC_PRECOMPUTED_MIN_FT,
    is_production_deployment,
)
from rad_rebuild.radiance.paths import REPO_ROOT

from .proxy import RadianceProxyClient


PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"
ENV_SHOW_LIVE_MODES = "RAD_REBUILD_SHOW_LIVE_MODES"
ENV_TRUSTED_HOSTS = "RAD_REBUILD_TRUSTED_HOSTS"
ENV_FRAME_ANCESTORS = "RAD_REBUILD_FRAME_ANCESTORS"
ENV_PROXY_MAX_RESPONSE_BYTES = "RADIANCE_PROXY_MAX_RESPONSE_BYTES"
LOGGER = logging.getLogger(__name__)
IMMUTABLE_STATIC_PREFIXES = ("/static/vendor/", "/static/viewer/")
IMMUTABLE_STATIC_SUFFIXES = (".css", ".glb", ".js", ".json")


@dataclass(frozen=True)
class WebSettings:
    repo_root: Path
    web_port: int
    max_request_bytes: int
    max_proxy_response_bytes: int
    backend_base_url: str
    proxy_connect_timeout_s: float
    proxy_read_timeout_s: float
    proxy_stream_read_timeout_s: float
    trusted_hosts: tuple[str, ...]
    frame_ancestors: tuple[str, ...]


def _int_env(env: Mapping[str, str], name: str, default: int, *, minimum: int = 1) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return value


def _float_env(env: Mapping[str, str], name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum:g}.")
    return value


def _csv_tuple(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _frame_ancestor_sources(raw: str) -> tuple[str, ...]:
    sources: list[str] = []
    for item in _csv_tuple(raw):
        if item in {"'self'", "self"}:
            sources.append("'self'")
        elif item.startswith("https://"):
            sources.append(item)
    return tuple(dict.fromkeys(sources))


def load_web_settings(env: Mapping[str, str] | None = None) -> WebSettings:
    source = os.environ if env is None else env
    backend_base = source.get(ENV_RESEARCH_API_BASE, "").strip().rstrip("/")
    if not backend_base:
        backend_host = source.get(ENV_RESEARCH_BACKEND_HOST, DEFAULT_BACKEND_HOST).strip() or DEFAULT_BACKEND_HOST
        backend_port = _int_env(source, ENV_RESEARCH_BACKEND_PORT, DEFAULT_BACKEND_PORT)
        backend_base = f"http://{backend_host}:{backend_port}"
    trusted_hosts = _csv_tuple(source.get(ENV_TRUSTED_HOSTS, "localhost,127.0.0.1"))
    return WebSettings(
        repo_root=REPO_ROOT,
        web_port=_int_env(source, ENV_RESEARCH_WEB_PORT, DEFAULT_WEB_PORT),
        max_request_bytes=_int_env(source, ENV_RADIANCE_MAX_REQUEST_BYTES, DEFAULT_MAX_REQUEST_BYTES),
        max_proxy_response_bytes=_int_env(source, ENV_PROXY_MAX_RESPONSE_BYTES, 16 * 1024 * 1024),
        backend_base_url=backend_base,
        proxy_connect_timeout_s=_float_env(source, "RADIANCE_PROXY_CONNECT_TIMEOUT_S", 2.0),
        proxy_read_timeout_s=_float_env(source, "RADIANCE_PROXY_READ_TIMEOUT_S", 300.0),
        proxy_stream_read_timeout_s=_float_env(source, "RADIANCE_PROXY_STREAM_READ_TIMEOUT_S", 300.0),
        trusted_hosts=trusted_hosts,
        frame_ancestors=_frame_ancestor_sources(source.get(ENV_FRAME_ANCESTORS, "")),
    )


def _env_truthy(name: str) -> bool:
    raw = os.environ.get(name, "")
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _show_live_modes(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return not is_production_deployment(source) and _env_truthy(ENV_SHOW_LIVE_MODES)


def _request_id() -> str:
    existing = request.headers.get("X-Request-ID", "").strip().lower()
    if len(existing) == 32 and all(char in "0123456789abcdef" for char in existing):
        return existing
    existing = request.headers.get("X-Correlation-ID", "").strip().lower()
    if len(existing) == 32 and all(char in "0123456789abcdef" for char in existing):
        return existing
    return uuid.uuid4().hex


def _public_error(status: int, code: str, message: str) -> Response:
    correlation_id = getattr(g, "request_id", None) or _request_id()
    response = jsonify(
        {
            "ok": False,
            "error": code,
            "message": message,
            "correlation_id": correlation_id,
        }
    )
    response.status_code = status
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["X-Request-ID"] = correlation_id
    return response


def _parse_positive_float(raw_value: object, field_name: str, *, maximum: float | None = None) -> float:
    if isinstance(raw_value, bool) or raw_value is None:
        raise ValueError(f"{field_name} must be a number.")
    if isinstance(raw_value, str):
        raw_clean = raw_value.strip()
        if raw_clean != raw_value or not raw_clean:
            raise ValueError(f"{field_name} must be a number.")
    try:
        value = float(cast(Any, raw_value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number.") from exc

    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite.")
    if value == 0.0 and math.copysign(1.0, value) < 0:
        raise ValueError(f"{field_name} may not be negative zero.")
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must be no greater than {maximum:g}.")
    return value


def _host_allowed(host_header: str, trusted_hosts: tuple[str, ...]) -> bool:
    if not trusted_hosts:
        return True
    hostname = host_header.rsplit("@", 1)[-1].split(":", 1)[0].strip().lower()
    return hostname in {host.lower() for host in trusted_hosts}


def _content_security_policy(path: str, frame_ancestor_origins: tuple[str, ...] = ()) -> str:
    is_scatter_artifact = path == "/radiance-api/radiance/scatter"
    is_assembly_viewer = path == "/viewer/assembly"
    if is_scatter_artifact or is_assembly_viewer:
        frame_ancestors = " ".join(("'self'", *frame_ancestor_origins))
    else:
        frame_ancestors = "'none'"
    script_src = "'self' 'unsafe-inline'" if is_scatter_artifact else "'self'"
    directives = [
        "default-src 'self'",
        "base-uri 'self'",
        f"frame-ancestors {frame_ancestors}",
        "object-src 'none'",
        f"script-src {script_src}",
        "script-src-attr 'none'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: blob:",
        "connect-src 'self'",
        "frame-src 'self'",
        "form-action 'self'",
    ]
    return "; ".join(directives)


def _radiance_template_context() -> dict[str, object]:
    show_live_modes = _show_live_modes()
    return {
        "radiance_mode_options": ACTIVE_RADIANCE_MODE_OPTIONS,
        "radiance_execution_mode_options": EXECUTION_MODE_OPTIONS if show_live_modes else PUBLIC_EXECUTION_MODE_OPTIONS,
        "radiance_show_live_modes": show_live_modes,
        "radiance_default_execution_mode": DEFAULT_EXECUTION_MODE,
        "radiance_public_min_ft": PUBLIC_PRECOMPUTED_MIN_FT,
        "radiance_public_max_ft": PUBLIC_PRECOMPUTED_MAX_FT,
        "radiance_default_length_ft": PUBLIC_DEFAULT_LENGTH_FT,
        "radiance_default_width_ft": PUBLIC_DEFAULT_WIDTH_FT,
        "radiance_precomputed_download_command": PRECOMPUTED_DOWNLOAD_COMMAND,
        "radiance_precomputed_size_text": PRECOMPUTED_FULL_DATASET_SIZE_TEXT,
    }


def create_pages_blueprint() -> Blueprint:
    pages = Blueprint("pages", __name__)

    @pages.get("/")
    def index() -> str:
        return render_template("radiance_simulator.html")

    @pages.get("/layout-generator")
    @pages.get("/layout-generator/")
    def layout_generator_page() -> Any:
        return redirect("/radiance-simulator", code=301)

    @pages.get("/technology/layout-generator")
    @pages.get("/technology/layout-generator/")
    def layout_generator_legacy_redirect() -> Any:
        return redirect("/radiance-simulator", code=301)

    @pages.get("/radiance-simulator")
    @pages.get("/radiance-simulator/")
    def radiance_simulator_page() -> str:
        return render_template("radiance_simulator.html")

    @pages.get("/viewer/assembly")
    def assembly_viewer_page() -> str:
        return render_template("assembly_viewer.html")

    @pages.get("/technology/radiance-simulator")
    @pages.get("/technology/radiance-simulator/")
    def radiance_simulator_legacy_redirect() -> Any:
        return redirect(url_for("pages.radiance_simulator_page"), code=301)

    return pages


def create_layout_blueprint() -> Blueprint:
    layout = Blueprint("layout", __name__)

    @layout.post("/api/layout-generator")
    def layout_generator_api() -> Any:
        payload = request.get_json(silent=True) or request.form.to_dict(flat=True)
        try:
            length_ft = _parse_positive_float(
                payload.get("length_ft"),
                "length_ft",
                maximum=float(PUBLIC_PRECOMPUTED_MAX_FT),
            )
            width_ft = _parse_positive_float(
                payload.get("width_ft"),
                "width_ft",
                maximum=float(PUBLIC_PRECOMPUTED_MAX_FT),
            )
        except ValueError as exc:
            current_app.logger.info("Invalid layout request: %s", exc)
            return _public_error(400, "invalid_request", "Invalid request.")

        try:
            result = build_layout_payload(length_ft, width_ft, include_svg=True)
        except Exception:
            current_app.logger.exception("Layout generation failed")
            return _public_error(500, "generation_failed", "Layout generation failed.")
        return jsonify(result), 200

    @layout.get("/api/layout-generator/png")
    def layout_generator_png_api() -> Response:
        try:
            length_ft = _parse_positive_float(
                request.args.get("length_ft"),
                "length_ft",
                maximum=float(PUBLIC_PRECOMPUTED_MAX_FT),
            )
            width_ft = _parse_positive_float(
                request.args.get("width_ft"),
                "width_ft",
                maximum=float(PUBLIC_PRECOMPUTED_MAX_FT),
            )
        except ValueError as exc:
            current_app.logger.info("Invalid layout PNG request: %s", exc)
            return _public_error(400, "invalid_request", "Invalid request.")

        try:
            layout_result = generate_layout(length_ft, width_ft)
            result = build_layout_payload_from_layout(
                layout_result,
                length_ft=length_ft,
                width_ft=width_ft,
                include_svg=False,
            )
            png_bytes = build_layout_png(
                cast(list[PointTuple], result["all_positions"]),
                cast(list[ModuleTuple], layout_result["module_groups"]),
                length_ft=length_ft,
                width_ft=width_ft,
            )
        except Exception:
            current_app.logger.exception("Layout PNG generation failed")
            return _public_error(500, "generation_failed", "Layout generation failed.")

        response = Response(png_bytes, mimetype="image/png")
        response.headers["Content-Disposition"] = f'attachment; filename="{result["png_download_name"]}"'
        return response

    return layout


def create_proxy_blueprint() -> Blueprint:
    proxy = Blueprint("radiance_proxy", __name__)

    @proxy.route("/radiance-api/<path:path>", methods=["GET", "POST", "HEAD"])
    def radiance_api_proxy(path: str) -> Response:
        stream = path.startswith("jobs/") and path.endswith("/logs")
        client: RadianceProxyClient = current_app.extensions["radiance_proxy_client"]
        return client.proxy(path, request, stream=stream, request_id=getattr(g, "request_id", None))

    return proxy


def create_ops_blueprint() -> Blueprint:
    ops = Blueprint("ops", __name__)

    @ops.get("/health")
    @ops.get("/healthz")
    def health() -> Response:
        return jsonify({"status": "ok", "service": "rad-rebuild-web"})

    @ops.get("/ready")
    def ready() -> Any:
        client: RadianceProxyClient = current_app.extensions["radiance_proxy_client"]
        if not client.backend_ready():
            return jsonify({"status": "not_ready", "dependencies": {"radiance_backend": "unavailable"}}), 503
        return jsonify({"status": "ready", "dependencies": {"radiance_backend": "ready"}})

    @ops.get("/metrics")
    def metrics() -> Response:
        payload = {
            "service": "rad-rebuild-web",
            "requests_total": current_app.extensions.get("requests_total", 0),
        }
        return Response(json.dumps(payload, sort_keys=True) + "\n", mimetype="application/json")

    return ops


def create_app(
    config: Mapping[str, object] | None = None,
    services: Mapping[str, object] | None = None,
) -> Flask:
    settings = load_web_settings()
    flask_app = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
    )
    flask_app.config.from_mapping(
        MAX_CONTENT_LENGTH=settings.max_request_bytes,
        WEB_SETTINGS=settings,
        TRUSTED_HOSTS=settings.trusted_hosts,
    )
    if config:
        flask_app.config.update(config)

    active_settings = flask_app.config["WEB_SETTINGS"]
    proxy_client = None if services is None else services.get("radiance_proxy_client")
    if proxy_client is None:
        proxy_client = RadianceProxyClient(
            active_settings.backend_base_url,
            connect_timeout_s=active_settings.proxy_connect_timeout_s,
            read_timeout_s=active_settings.proxy_read_timeout_s,
            stream_read_timeout_s=active_settings.proxy_stream_read_timeout_s,
            max_request_bytes=active_settings.max_request_bytes,
            max_response_bytes=active_settings.max_proxy_response_bytes,
        )
    flask_app.extensions["radiance_proxy_client"] = proxy_client
    flask_app.extensions["requests_total"] = 0

    @flask_app.context_processor
    def inject_radiance_config() -> dict[str, object]:
        return _radiance_template_context()

    @flask_app.before_request
    def attach_request_context() -> Response | None:
        trusted_hosts = tuple(flask_app.config.get("TRUSTED_HOSTS") or ())
        if not _host_allowed(request.host, trusted_hosts):
            return _public_error(400, "invalid_host", "Invalid host.")
        g.request_id = _request_id()
        return None

    @flask_app.after_request
    def attach_response_headers(response: Response) -> Response:
        flask_app.extensions["requests_total"] = int(flask_app.extensions.get("requests_total", 0)) + 1
        request_id = getattr(g, "request_id", None)
        if request_id:
            response.headers.setdefault("X-Request-ID", request_id)
            response.headers.setdefault("X-Correlation-ID", request_id)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.path not in {"/radiance-api/radiance/scatter", "/viewer/assembly"}:
            response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        active_settings = flask_app.config["WEB_SETTINGS"]
        response.headers.setdefault(
            "Content-Security-Policy",
            _content_security_policy(request.path, active_settings.frame_ancestors),
        )
        if is_production_deployment(os.environ) and request.path.startswith("/static/"):
            if (
                request.path.startswith(IMMUTABLE_STATIC_PREFIXES)
                and request.path.endswith(IMMUTABLE_STATIC_SUFFIXES)
            ):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif request.path.endswith((".css", ".js")):
                response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    flask_app.register_blueprint(create_pages_blueprint())
    flask_app.register_blueprint(create_layout_blueprint())
    flask_app.register_blueprint(create_proxy_blueprint())
    flask_app.register_blueprint(create_ops_blueprint())
    return flask_app


app = create_app()


def main() -> None:
    settings: WebSettings = app.config["WEB_SETTINGS"]
    debug = os.environ.get("FLASK_DEBUG", "").strip() == "1"
    app.run(host="0.0.0.0", port=settings.web_port, debug=debug)


if __name__ == "__main__":
    main()
