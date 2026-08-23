"""One local ASGI application for UI, jobs, and run-scoped artifacts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from importlib import resources
import json
import logging
from pathlib import Path
import threading

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from fspm_optics.application.domain import (
    CONVENTIONAL_SYSTEM_ID,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    RequestValidationError,
    parse_run_request,
)
from fspm_optics.layout.mode import (
    DEFAULT_PROPOSED_LAYOUT_MODE,
    ProposedLayoutMode,
    resolve_proposed_layout_mode,
)
from .jobs import JobManager, JobNotFoundError, JobQueueFullError, RunExecutor
from .mode import ApplicationMode
from .precomputed import (
    PrecomputedCatalog,
    PrecomputedPlaybackError,
    PrecomputedPlaybackNotFound,
)
from .workspaces import RuntimeWorkspaces, WorkspaceSafetyError

LOGGER = logging.getLogger(__name__)

MAX_REQUEST_BYTES = 64 * 1024
LIVE_SYSTEM_IDS = (
    PROPOSED_SYSTEM_ID,
    CONVENTIONAL_SYSTEM_ID,
    HPS_SYSTEM_ID,
)
PUBLIC_ARTIFACTS = {
    "ppfd.csv": ("ppfd.csv", "text/csv; charset=utf-8", True),
    "target-control.json": (
        "target_control.json",
        "application/json",
        False,
    ),
    "full-output-schedule.json": (
        "full_output_schedule.json",
        "application/json",
        False,
    ),
    "operating-point.json": (
        "operating-point.json",
        "application/json",
        False,
    ),
    "physical-source-state.json": (
        "physical-source-state.json",
        "application/json",
        False,
    ),
    "natural-fit-layout.json": (
        "natural_fit_layout.json",
        "application/json",
        False,
    ),
    "baseline-leaf-position-uniformity.v1.json": (
        "baseline-leaf-position-uniformity.v1.json",
        "application/json",
        False,
    ),
    "run.log": ("run.log", "text/plain; charset=utf-8", True),
    "events.jsonl": ("events.jsonl", "application/x-ndjson", True),
    "ppfd-heatmap.png": ("ppfd-heatmap.png", "image/png", False),
    "ppfd-heatmap-overlay.png": (
        "ppfd-heatmap-overlay.png",
        "image/png",
        False,
    ),
    "visualization.json": ("visualization.json", "application/json", False),
    "ppfd-scatter.f32le.bin": (
        "ppfd-scatter.f32le.bin",
        "application/octet-stream",
        False,
    ),
}
STATIC_ASSETS = {
    "homeleaf.png": "image/png",
    "styles.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "analysis-scope-ui.js": "text/javascript; charset=utf-8",
    "spectral-basis-ui.js": "text/javascript; charset=utf-8",
    "layout-mode-ui.js": "text/javascript; charset=utf-8",
    "proposed-control-mode-ui.js": "text/javascript; charset=utf-8",
    "proposed-ring-mode-ui.js": "text/javascript; charset=utf-8",
    "proposed-source-mode-ui.js": "text/javascript; charset=utf-8",
    "lighting-target-mode-ui.js": "text/javascript; charset=utf-8",
    "result-view.js": "text/javascript; charset=utf-8",
    "result-contracts.js": "text/javascript; charset=utf-8",
    "system-labels.js": "text/javascript; charset=utf-8",
}


def create_app(
    *,
    runtime_root: str | Path,
    max_workers: int = 1,
    max_queued: int = 1,
    run_executor: RunExecutor | None = None,
    keep_runtime: bool = False,
    proposed_layout_mode: ProposedLayoutMode | str = DEFAULT_PROPOSED_LAYOUT_MODE,
    precomputed_root: str | Path | None = None,
) -> Starlette:
    """Construct the trusted-local live app without starting its runtime."""

    repository_root = _repository_root()
    workspaces = RuntimeWorkspaces(runtime_root, repository_root)
    precomputed = PrecomputedCatalog(
        precomputed_root or repository_root / "precomputed",
        repository_root=repository_root,
    )
    manager_options: dict[str, object] = {
        "max_workers": max_workers,
        "max_queued": max_queued,
        "proposed_layout_mode": resolve_proposed_layout_mode(
            proposed_layout_mode
        ),
    }
    if run_executor is not None:
        manager_options["run_executor"] = run_executor
    jobs = JobManager(workspaces, **manager_options)  # type: ignore[arg-type]
    shutdown_lock = threading.Lock()
    shutdown_complete = False

    def shutdown_runtime() -> None:
        """Stop workers before cleanup; safe for lifespan and CLI finally paths."""

        nonlocal shutdown_complete
        with shutdown_lock:
            if shutdown_complete:
                return
            worker_stopped = False
            try:
                jobs.shutdown()
                worker_stopped = True
            except BaseException as exc:
                LOGGER.warning(
                    "failed to shut down the runtime worker cleanly; runtime "
                    "artifacts were preserved: %s",
                    exc,
                )
            finally:
                workspaces.stop_server(
                    keep_runtime=keep_runtime or not worker_stopped
                )
                shutdown_complete = True

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        workspaces.start_server()
        try:
            jobs.start()
            yield
        finally:
            shutdown_runtime()

    async def index(_request: Request) -> Response:
        return Response(
            _web_resource("index.html"),
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    async def static_asset(request: Request) -> Response:
        name = request.path_params["asset_name"]
        media_type = STATIC_ASSETS.get(name)
        if media_type is None:
            return _error(404, "not_found", "Static asset not found.")
        return Response(
            _web_resource(name),
            media_type=media_type,
            headers={"Cache-Control": "no-store"},
        )

    async def post_run(request: Request) -> Response:
        length = request.headers.get("content-length")
        if length is not None:
            try:
                if int(length) > MAX_REQUEST_BYTES:
                    return _error(413, "request_too_large", "Request body is too large.")
            except ValueError:
                return _error(400, "invalid_content_length", "Invalid Content-Length.")
        body = await request.body()
        if len(body) > MAX_REQUEST_BYTES:
            return _error(413, "request_too_large", "Request body is too large.")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _error(400, "invalid_json", "Request body must be valid JSON.")
        try:
            parsed = parse_run_request(
                payload,
                proposed_layout_mode=jobs.proposed_layout_mode,
            )
            if parsed.system not in LIVE_SYSTEM_IDS:
                raise RequestValidationError(
                    "system",
                    "Live Simulation requires a canonical supported system.",
                )
            job_id, run_id = jobs.submit(parsed)
        except RequestValidationError as exc:
            return JSONResponse({"error": exc.to_dict()}, status_code=422)
        except JobQueueFullError as exc:
            return _error(503, "job_queue_full", str(exc))
        return JSONResponse(
            {
                "job_id": job_id,
                "run_id": run_id,
                "state": "queued",
                "analysis_scope": parsed.analysis_scope.value,
                "lighting_target_mode": (
                    parsed.lighting_target_mode.value
                    if hasattr(parsed, "lighting_target_mode")
                    else None
                ),
                "job_url": f"/api/jobs/{job_id}",
            },
            status_code=202,
        )

    async def job_status(request: Request) -> Response:
        try:
            cursor = _cursor(request.query_params.get("cursor", "0"))
            payload = jobs.snapshot(request.path_params["job_id"], cursor)
        except (ValueError, WorkspaceSafetyError) as exc:
            return _error(400, "invalid_job_query", str(exc))
        except JobNotFoundError:
            return _error(404, "job_not_found", "Job not found.")
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def precomputed_availability(_request: Request) -> Response:
        try:
            payload = precomputed.availability()
        except ValueError as exc:
            return _error(
                409,
                "precomputed_catalog_invalid",
                str(exc),
            )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def capabilities(_request: Request) -> Response:
        return JSONResponse(
            {
                "application_mode": ApplicationMode.TRUSTED_LOCAL_LIVE.value,
                "precomputed_playback": {
                    "enabled": True,
                    "systems": ["proposed", "conventional", "hps"],
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
                "live_simulation": {
                    "enabled": True,
                    "systems": list(LIVE_SYSTEM_IDS),
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    async def post_precomputed_playback(request: Request) -> Response:
        payload_or_error = await _json_request_payload(request)
        if isinstance(payload_or_error, Response):
            return payload_or_error
        try:
            record = precomputed.load(payload_or_error)
        except PrecomputedPlaybackError as exc:
            return _error(exc.status_code, exc.code, str(exc))
        return JSONResponse(
            {
                "state": "succeeded",
                "execution_mode": "precomputed",
                "result": record.result_payload(),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def precomputed_metrics(request: Request) -> Response:
        try:
            record = precomputed.get(request.path_params["playback_id"])
        except PrecomputedPlaybackNotFound:
            return _error(404, "precomputed_playback_not_found", "Playback not found.")
        return JSONResponse(record.metrics(), headers={"Cache-Control": "no-store"})

    async def precomputed_manifest(request: Request) -> Response:
        try:
            record = precomputed.get(request.path_params["playback_id"])
        except PrecomputedPlaybackNotFound:
            return _error(404, "precomputed_playback_not_found", "Playback not found.")
        return JSONResponse(record.manifest(), headers={"Cache-Control": "no-store"})

    async def precomputed_artifact(request: Request) -> Response:
        try:
            record = precomputed.get(request.path_params["playback_id"])
            artifact = record.artifact(request.path_params["artifact_name"])
        except PrecomputedPlaybackNotFound:
            return _error(404, "precomputed_artifact_not_found", "Artifact not found.")
        headers = {"Cache-Control": "no-store"}
        if artifact.download_name is not None:
            headers["Content-Disposition"] = (
                f'attachment; filename="{artifact.download_name}"'
            )
        return Response(
            artifact.data,
            media_type=artifact.media_type,
            headers=headers,
        )

    async def precomputed_viewer_artifact(request: Request) -> Response:
        try:
            record = precomputed.get(request.path_params["playback_id"])
            artifact = record.viewer_artifact(request.path_params["viewer_path"])
        except PrecomputedPlaybackNotFound:
            return _error(404, "precomputed_viewer_not_found", "Viewer artifact not found.")
        except PrecomputedPlaybackError as exc:
            return _error(exc.status_code, exc.code, str(exc))
        return Response(
            artifact.data,
            media_type=artifact.media_type,
            headers={"Cache-Control": "no-store"},
        )

    async def precomputed_scatter_artifact(request: Request) -> Response:
        try:
            record = precomputed.get(request.path_params["playback_id"])
            artifact = record.scatter_artifact(request.path_params["scatter_path"])
        except PrecomputedPlaybackNotFound:
            return _error(404, "precomputed_scatter_not_found", "Scatter artifact not found.")
        return Response(
            artifact.data,
            media_type=artifact.media_type,
            headers={"Cache-Control": "no-store"},
        )

    async def run_metrics(request: Request) -> Response:
        return _run_json(workspaces, request.path_params["run_id"], "metrics.json")

    async def run_manifest(request: Request) -> Response:
        return _run_json(workspaces, request.path_params["run_id"], "manifest.json")

    async def run_artifact(request: Request) -> Response:
        name = request.path_params["artifact_name"]
        record = PUBLIC_ARTIFACTS.get(name)
        if record is None:
            return _error(404, "artifact_not_allowed", "Artifact is not allowlisted.")
        try:
            root = workspaces.completed_run(request.path_params["run_id"])
            relative, media_type, download = record
            path = _safe_public_file(root, relative)
        except WorkspaceSafetyError as exc:
            return _error(400, "invalid_run_id", str(exc))
        except FileNotFoundError:
            return _error(404, "run_or_artifact_not_found", "Run artifact not found.")
        return FileResponse(
            path,
            media_type=media_type,
            filename=name if download else None,
            headers={"Cache-Control": "no-store"},
        )

    async def fspm_artifact(request: Request) -> Response:
        try:
            root = workspaces.completed_run(request.path_params["run_id"])
            manifest_path = _safe_public_file(root, "manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest root is not an object.")
            transport = manifest.get("multispectral_transport")
            transport_public = (
                transport.get("public_artifacts")
                if isinstance(transport, dict)
                else None
            )
            aggregation = manifest.get("fspm_scientific_aggregation")
            aggregation_public = (
                aggregation.get("public_artifacts")
                if isinstance(aggregation, dict)
                else None
            )
            suffix = request.path_params["fspm_path"]
            candidates = {
                "fspm-transport/" + suffix,
                "fspm-aggregation/" + suffix,
            }
            declared = {
                value
                for public in (transport_public, aggregation_public)
                if isinstance(public, dict)
                for value in public.values()
                if isinstance(value, str)
            }
            matches = candidates & declared
            if len(matches) != 1:
                return _error(
                    404,
                    "fspm_artifact_not_allowed",
                    "FSPM artifact is not declared by this run.",
                )
            requested = matches.pop()
            path = _safe_public_file(root, requested)
        except WorkspaceSafetyError as exc:
            return _error(400, "unsafe_fspm_path", str(exc))
        except FileNotFoundError:
            return _error(
                404,
                "fspm_artifact_not_found",
                "FSPM run artifact not found.",
            )
        except (json.JSONDecodeError, ValueError):
            return _error(
                500,
                "invalid_run_artifact",
                "Published run manifest is invalid.",
            )
        media_type = (
            "application/json"
            if path.suffix == ".json"
            else "application/octet-stream"
        )
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": "no-store"},
        )

    async def viewer_artifact(request: Request) -> Response:
        try:
            path = workspaces.safe_viewer_file(
                request.path_params["run_id"],
                request.path_params["viewer_path"],
            )
        except WorkspaceSafetyError as exc:
            return _error(400, "unsafe_viewer_path", str(exc))
        except FileNotFoundError:
            return _error(404, "viewer_artifact_not_found", "Viewer artifact not found.")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    async def scatter_artifact(request: Request) -> Response:
        try:
            path = workspaces.safe_scatter_file(
                request.path_params["run_id"],
                request.path_params["scatter_path"],
            )
        except WorkspaceSafetyError as exc:
            return _error(400, "unsafe_scatter_path", str(exc))
        except FileNotFoundError:
            return _error(
                404,
                "scatter_artifact_not_found",
                "Scatter viewer artifact not found.",
            )
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    app = Starlette(
        debug=False,
        lifespan=lifespan,
        routes=[
            Route("/", index, methods=["GET"]),
            Route("/static/{asset_name}", static_asset, methods=["GET"]),
            Route("/api/capabilities", capabilities, methods=["GET"]),
            Route("/api/runs", post_run, methods=["POST"]),
            Route("/api/jobs/{job_id}", job_status, methods=["GET"]),
            Route(
                "/api/precomputed/availability",
                precomputed_availability,
                methods=["GET"],
            ),
            Route(
                "/api/precomputed/playbacks",
                post_precomputed_playback,
                methods=["POST"],
            ),
            Route(
                "/api/precomputed/playbacks/{playback_id}/metrics",
                precomputed_metrics,
                methods=["GET"],
            ),
            Route(
                "/api/precomputed/playbacks/{playback_id}/manifest",
                precomputed_manifest,
                methods=["GET"],
            ),
            Route(
                "/api/precomputed/playbacks/{playback_id}/artifacts/{artifact_name}",
                precomputed_artifact,
                methods=["GET"],
            ),
            Route("/api/runs/{run_id}/metrics", run_metrics, methods=["GET"]),
            Route("/api/runs/{run_id}/manifest", run_manifest, methods=["GET"]),
            Route(
                "/api/runs/{run_id}/artifacts/{artifact_name}",
                run_artifact,
                methods=["GET"],
            ),
            Route(
                "/api/runs/{run_id}/fspm/{fspm_path:path}",
                fspm_artifact,
                methods=["GET"],
            ),
            Route(
                "/runs/{run_id}/viewer/{viewer_path:path}",
                viewer_artifact,
                methods=["GET"],
            ),
            Route(
                "/runs/{run_id}/scatter/{scatter_path:path}",
                scatter_artifact,
                methods=["GET"],
            ),
            Route(
                "/precomputed/{playback_id}/viewer/{viewer_path:path}",
                precomputed_viewer_artifact,
                methods=["GET"],
            ),
            Route(
                "/precomputed/{playback_id}/scatter/{scatter_path:path}",
                precomputed_scatter_artifact,
                methods=["GET"],
            ),
        ],
    )
    app.state.jobs = jobs
    app.state.workspaces = workspaces
    app.state.precomputed = precomputed
    app.state.application_mode = ApplicationMode.TRUSTED_LOCAL_LIVE
    app.state.shutdown_runtime = shutdown_runtime
    return app


def _run_json(workspaces: RuntimeWorkspaces, run_id: str, filename: str) -> Response:
    try:
        root = workspaces.completed_run(run_id)
        path = _safe_public_file(root, filename)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("artifact root is not a JSON object.")
    except WorkspaceSafetyError as exc:
        return _error(400, "invalid_run_id", str(exc))
    except FileNotFoundError:
        return _error(404, "run_not_found", "Completed run not found.")
    except (json.JSONDecodeError, ValueError):
        return _error(500, "invalid_run_artifact", "Published run artifact is invalid.")
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


async def _json_request_payload(request: Request) -> dict[str, object] | Response:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > MAX_REQUEST_BYTES:
                return _error(413, "request_too_large", "Request body is too large.")
        except ValueError:
            return _error(400, "invalid_content_length", "Invalid Content-Length.")
    body = await request.body()
    if len(body) > MAX_REQUEST_BYTES:
        return _error(413, "request_too_large", "Request body is too large.")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error(400, "invalid_json", "Request body must be valid JSON.")
    if not isinstance(payload, dict):
        return _error(422, "invalid_request", "Request body must be a JSON object.")
    return payload


def _safe_public_file(root: Path, filename: str) -> Path:
    candidate = root / filename
    if candidate.is_symlink() or any(
        parent.is_symlink()
        for parent in candidate.parents
        if parent != root.parent
    ):
        raise WorkspaceSafetyError("artifact symlinks are not allowed.")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise WorkspaceSafetyError("artifact escaped its run boundary.")
    return resolved


def _cursor(value: str) -> int:
    if not value.isascii() or not value.isdigit():
        raise ValueError("cursor must be a non-negative integer.")
    return int(value)


def _web_resource(name: str) -> bytes:
    resource_group = "viewer" if name == "system-labels.js" else "web"
    return (
        resources.files("fspm_optics")
        .joinpath("resources", resource_group, name)
        .read_bytes()
    )


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / ".git").exists():
            return parent
    raise RuntimeError("unable to locate the fspm-optics repository root.")
