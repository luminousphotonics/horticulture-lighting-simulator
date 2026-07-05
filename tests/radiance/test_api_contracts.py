from __future__ import annotations

import json
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import anyio
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.backend.jobs import (  # noqa: E402
    JobBackpressureError,
    JobNotFoundError,
    JobRecord,
    JobTail,
)
from rad_rebuild.radiance.backend.models import (  # noqa: E402
    ElectricalCostRequest,
    ElectricalCostStage,
    LayoutRequest,
    RadianceRunRequest,
)
from rad_rebuild.radiance.backend.routes import artifacts as artifacts_route  # noqa: E402
from rad_rebuild.radiance.backend.routes import costs as costs_route  # noqa: E402
from rad_rebuild.radiance.backend.routes import docker as docker_route  # noqa: E402
from rad_rebuild.radiance.backend.routes import jobs as jobs_route  # noqa: E402
from rad_rebuild.radiance.backend.routes import metrics as metrics_route  # noqa: E402
from rad_rebuild.radiance.backend.routes import reproduce as reproduce_route  # noqa: E402
from rad_rebuild.radiance.backend.routes import runtime_status as runtime_status_route  # noqa: E402
from rad_rebuild.radiance.backend.routes import runs as runs_route  # noqa: E402
from rad_rebuild.radiance.backend.server import create_app  # noqa: E402
from rad_rebuild.radiance.domain import JobState  # noqa: E402


class _FakeRequest:
    method = "GET"

    def __init__(self, session_id: str = "api-contract", job_service: object | None = None) -> None:
        self.query_params = {"session_id": session_id}
        self.headers: dict[str, str] = {}
        self.state = SimpleNamespace()
        self.app = SimpleNamespace(state=SimpleNamespace(job_service=job_service))


def _request(session_id: str = "api-contract", job_service: object | None = None) -> Request:
    return cast(Request, _FakeRequest(session_id, job_service))


async def _call_validation_handler(
    handler: object,
    request: Request,
    exc: RequestValidationError,
) -> Response:
    typed = cast(
        Callable[[Request, RequestValidationError], Awaitable[Response]],
        handler,
    )
    return await typed(request, exc)


async def _call_http_exception_handler(
    handler: object,
    request: Request,
    exc: HTTPException,
) -> Response:
    typed = cast(Callable[[Request, HTTPException], Awaitable[Response]], handler)
    return await typed(request, exc)


def _job(job_id: str = "job-1", status: str = JobState.QUEUED.value) -> JobRecord:
    return JobRecord(
        id=job_id,
        status=status,
        command=["true"],
        cwd=Path("/tmp"),
        env={},
        owner_session="api-contract",
        request_fingerprint="fingerprint",
        kind="test",
        exit_code=None,
        attempts=1,
        created_at=1.0,
        queued_at=1.0,
        started_at=None,
        finished_at=None,
        updated_at=1.0,
        timeout_s=None,
        failure=None,
        log_path=Path("/tmp/job-1.log"),
    )


class _BackpressureJobService:
    def submit(self, *args: object, **kwargs: object) -> JobRecord:
        raise JobBackpressureError("queue full")


class _CapturingJobService:
    def __init__(self) -> None:
        self.submit_kwargs: dict[str, object] | None = None

    def submit(self, *args: object, **kwargs: object) -> JobRecord:
        del args
        self.submit_kwargs = kwargs
        return _job("captured-job", JobState.QUEUED.value)


class _ReproduceJobService:
    def submit(self, *args: object, **kwargs: object) -> JobRecord:
        return _job("reproduce-job", JobState.QUEUED.value)


class _TailJobService:
    def get(self, job_id: str, owner_session: str | None = None) -> JobRecord:
        del owner_session
        return _job(job_id, JobState.RUNNING.value)

    def tail(
        self,
        job_id: str,
        *,
        cursor: int = 0,
        limit: int = 200,
        owner_session: str | None = None,
    ) -> JobTail:
        del job_id, cursor, limit, owner_session
        return JobTail(
            lines=["first", "second"],
            next_cursor=2,
            done=False,
            status=JobState.RUNNING.value,
        )


class _MissingJobService:
    def get(self, job_id: str, owner_session: str | None = None) -> JobRecord:
        del owner_session
        raise JobNotFoundError(job_id)

    def tail(
        self,
        job_id: str,
        *,
        cursor: int = 0,
        limit: int = 200,
        owner_session: str | None = None,
    ) -> JobTail:
        del cursor, limit, owner_session
        raise JobNotFoundError(job_id)


def test_layout_response_contract() -> None:
    payload = {
        "length_ft": 10.0,
        "width_ft": 12.0,
        "module_count": 1,
        "module_counts": {"linear2": 1},
        "total_cost": 150,
        "all_positions": [(0.0, 0.0)],
        "svg": "<svg></svg>",
        "svg_download_name": "layout.svg",
        "png_download_name": "layout.png",
    }
    with patch.object(runs_route, "build_layout_payload", return_value=payload):
        result = runs_route.run_layout(LayoutRequest(length_ft=10, width_ft=12))

    assert result == payload


def test_radiance_run_backpressure_uses_structured_detail() -> None:
    lease = SimpleNamespace(
        staging_workspace=Path(tempfile.mkdtemp(prefix="rad_rebuild_run_contract_")),
        artifact_token="artifact-token",
    )
    with (
        patch.object(runs_route, "maybe_cleanup_runtime_state", lambda: None),
        patch.object(runs_route, "assert_live_execution_allowed", lambda _req, _session: None),
        patch.object(runs_route, "allocate_workspace_for_run", return_value=lease),
        patch.object(runs_route, "fail_staged_workspace", lambda _lease, _reason: None),
        patch.object(runs_route, "_visualize_command", return_value=(["true"], "visuals")),
        patch.object(runs_route, "_make_env_base", return_value={}),
    ):
        try:
            runs_route.run_radiance(
                RadianceRunRequest(
                    action="visualize",
                    mode="SMD",
                    execution_mode="live_local",
                    length_ft=10,
                    width_ft=10,
                    target_ppfd=1000,
                ),
                _request(job_service=_BackpressureJobService()),
            )
        except HTTPException as exc:
            assert exc.status_code == 429
            assert cast(dict[str, str], exc.detail) == {
                "error": "job_queue_full",
                "message": "queue full",
            }
        else:
            raise AssertionError("Expected run backpressure to raise HTTPException")


def test_live_local_run_uses_configured_local_timeout() -> None:
    lease = SimpleNamespace(
        staging_workspace=Path(tempfile.mkdtemp(prefix="rad_rebuild_live_timeout_")),
        artifact_token="artifact-token",
    )
    service = _CapturingJobService()
    with (
        patch.object(runs_route, "maybe_cleanup_runtime_state", lambda: None),
        patch.object(runs_route, "assert_live_execution_allowed", lambda _req, _session: None),
        patch.object(runs_route, "allocate_workspace_for_run", return_value=lease),
        patch.object(runs_route, "_visualize_command", return_value=(["true"], "visuals")),
        patch.object(runs_route, "_make_env_base", return_value={}),
        patch.object(
            runs_route,
            "get_settings",
            return_value=SimpleNamespace(local_live_job_timeout_s=3600.0),
        ),
    ):
        result = runs_route.run_radiance(
            RadianceRunRequest(
                action="visualize",
                mode="SMD",
                execution_mode="live_local",
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
            ),
            _request(job_service=service),
        )

    assert result["job_id"] == "captured-job"
    assert service.submit_kwargs is not None
    assert service.submit_kwargs["timeout_s"] == 3600.0


def test_live_local_run_can_disable_local_timeout() -> None:
    lease = SimpleNamespace(
        staging_workspace=Path(tempfile.mkdtemp(prefix="rad_rebuild_live_timeout_none_")),
        artifact_token="artifact-token",
    )
    service = _CapturingJobService()
    with (
        patch.object(runs_route, "maybe_cleanup_runtime_state", lambda: None),
        patch.object(runs_route, "assert_live_execution_allowed", lambda _req, _session: None),
        patch.object(runs_route, "allocate_workspace_for_run", return_value=lease),
        patch.object(runs_route, "_visualize_command", return_value=(["true"], "visuals")),
        patch.object(runs_route, "_make_env_base", return_value={}),
        patch.object(
            runs_route,
            "get_settings",
            return_value=SimpleNamespace(local_live_job_timeout_s=None),
        ),
    ):
        runs_route.run_radiance(
            RadianceRunRequest(
                action="visualize",
                mode="SMD",
                execution_mode="live_local",
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
            ),
            _request(job_service=service),
        )

    assert service.submit_kwargs is not None
    assert service.submit_kwargs["timeout_s"] is None


def test_job_tail_response_contract() -> None:
    with patch.object(jobs_route, "job_service_for_request", return_value=_TailJobService()):
        result = jobs_route.job_tail("job-1", _request())

    assert result == {
        "job_id": "job-1",
        "status": "running",
        "lines": ["first", "second"],
        "next_cursor": 2,
        "done": False,
    }


def test_manifest_response_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="rad_rebuild_manifest_contract_") as tmp:
        workspace = Path(tmp)
        manifest_path = workspace / "artifacts" / "radiance_manifest_smd.json"
        manifest_path.parent.mkdir()
        (workspace / "ppfd_map.txt").write_text("0 0 0 1000\n", encoding="utf-8")
        manifest = {"schema_version": 2, "mode": "SMD"}
        with (
            patch.object(artifacts_route, "maybe_cleanup_runtime_state", lambda: None),
            patch.object(artifacts_route, "authorize_workspace_from_request", return_value=workspace),
            patch.object(artifacts_route, "_env_for_mode", return_value={}),
            patch.object(artifacts_route, "_ensure_visuals", return_value=workspace / "visuals"),
            patch.object(
                artifacts_route,
                "_get_or_build_manifest",
                return_value=(manifest, manifest_path),
            ),
        ):
            result = artifacts_route.radiance_manifest(
                RadianceRunRequest(
                    action="visualize",
                    mode="SMD",
                    length_ft=10,
                    width_ft=10,
                ),
                _request(),
            )

    assert result == {
        "manifest": {"schema_version": 2, "mode": "SMD"},
        "path": "artifacts/radiance_manifest_smd.json",
    }


def test_images_response_contract_allows_missing_images() -> None:
    with tempfile.TemporaryDirectory(prefix="rad_rebuild_images_contract_") as tmp:
        workspace = Path(tmp)
        with (
            patch.object(artifacts_route, "maybe_cleanup_runtime_state", lambda: None),
            patch.object(artifacts_route, "authorize_workspace_from_request", return_value=workspace),
        ):
            result = artifacts_route.radiance_images(_request())

    assert result == {"overlay": None, "annot": None}


def test_metrics_response_contract_preserves_cost_estimate_null() -> None:
    with (
        patch(
            "rad_rebuild.radiance.backend.routes.metrics.authorize_workspace_from_request",
            return_value=Path("/tmp"),
        ),
        patch(
            "rad_rebuild.radiance.backend.routes.metrics.get_metrics_payload",
            return_value={"metrics": {"mean": 1000.0}, "cost_estimate": None},
        ),
    ):
        payload = metrics_route.radiance_metrics(_request())

    assert payload == {"metrics": {"mean": 1000.0}, "cost_estimate": None}


def test_electrical_estimate_response_contract() -> None:
    payload = {
        "ok": True,
        "mode": "SMD",
        "utility_rate_kwh": 0.12,
        "cycle_kwh": 100.0,
        "cycle_cost_usd": 12.0,
        "stages": [
            {
                "name": "Veg",
                "days": 10.0,
                "hours_per_day": 18.0,
                "avg_ppfd": 500.0,
                "simulated_mean_ppfd": 500.0,
                "stage_watts": 500.0,
                "stage_hours": 180.0,
                "stage_kwh": 90.0,
                "stage_cost_usd": 10.8,
                "usable_efficacy_umol_j": 2.5,
                "watts_basis": "input",
                "fixed_output": False,
            }
        ],
        "notes": ["contract"],
    }
    with (
        patch.object(costs_route, "maybe_cleanup_runtime_state", lambda: None),
        patch.object(costs_route, "assert_live_execution_allowed", lambda _req, _session: None),
        patch.object(costs_route, "build_electrical_estimate_payload", return_value=payload),
    ):
        result = costs_route.radiance_electrical_estimate(
            ElectricalCostRequest(
                action="metrics",
                mode="SMD",
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
                utility_rate_kwh=0.12,
                stages=[
                    ElectricalCostStage(
                        name="Veg",
                        days=10,
                        hours_per_day=18,
                        avg_ppfd=500,
                    )
                ],
            ),
            _request(),
        )

    assert result == payload


def test_docker_status_response_contract() -> None:
    with patch.object(
        docker_route,
        "docker_status_payload",
        return_value={"available": True, "detail": "Docker disabled."},
    ):
        result = docker_route.docker_status()

    assert result == {"available": True, "detail": "Docker disabled."}


def test_runtime_status_response_contract() -> None:
    payload = {
        "live_execution_enabled": True,
        "live_supported_modes": ["SMD"],
        "live_unsupported_mode_message": "Proposed only.",
        "modes": {
            "precomputed": {"available": True},
            "live_docker": {
                "available": False,
                "reason": "forced_unavailable_for_testing",
                "supported_lighting_modes": ["SMD"],
                "docker_cli": None,
                "daemon_available": False,
                "image_available": False,
                "can_build_image": True,
                "setup_commands": [],
            },
            "live_local": {
                "available": False,
                "reason": "missing_executables",
                "supported_lighting_modes": ["SMD"],
                "radiance_home": None,
                "radiance_bin_dir": None,
                "radiance_lib_dir": None,
                "detected_executables": {},
                "missing_executables": ["oconv"],
                "env_values": {},
                "setup_commands": [],
            },
        },
    }
    with patch.object(runtime_status_route, "runtime_status_payload", return_value=payload):
        result = runtime_status_route.radiance_runtime_status()

    assert result == payload


def test_reproduce_response_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="rad_rebuild_reproduce_contract_") as tmp:
        with (
            patch.object(reproduce_route, "_use_docker", return_value=True),
            patch.object(reproduce_route, "ensure_image", lambda: None),
            patch.object(reproduce_route, "reproduce_command", return_value=["true"]),
            patch.object(reproduce_route, "ARTIFACTS", Path(tmp) / "artifacts"),
            patch.object(
                reproduce_route,
                "job_service_for_request",
                return_value=_ReproduceJobService(),
            ),
        ):
            result = reproduce_route.run_reproduce(
                _request(job_service=_ReproduceJobService())
            )

    assert result == {"job_id": "reproduce-job", "status": "queued"}


def test_validation_error_uses_public_error_envelope() -> None:
    app = create_app()
    handler = app.exception_handlers[RequestValidationError]
    response = anyio.run(
        _call_validation_handler,
        handler,
        _request(),
        RequestValidationError([]),
    )
    payload = json.loads(bytes(response.body).decode("utf-8"))

    assert response.status_code == 422
    assert payload["ok"] is False
    assert payload["error"] == "invalid_request"
    assert "detail" not in payload


def test_dict_detail_http_error_uses_public_error_envelope() -> None:
    app = create_app()
    handler = app.exception_handlers[HTTPException]
    response = anyio.run(
        _call_http_exception_handler,
        handler,
        _request(),
        HTTPException(
            status_code=429,
            detail={"error": "job_queue_full", "message": "queue full"},
        ),
    )
    payload = json.loads(bytes(response.body).decode("utf-8"))

    assert response.status_code == 429
    assert payload["ok"] is False
    assert payload["error"] == "job_queue_full"
    assert payload["detail"] == {"error": "job_queue_full", "message": "queue full"}


def test_string_detail_http_error_uses_public_error_envelope() -> None:
    with patch.object(jobs_route, "job_service_for_request", return_value=_MissingJobService()):
        try:
            jobs_route.job_tail("missing", _request())
        except HTTPException as exc:
            app = create_app()
            handler = app.exception_handlers[HTTPException]
            response = anyio.run(_call_http_exception_handler, handler, _request(), exc)
        else:
            raise AssertionError("Expected missing job to raise HTTPException")
    payload = json.loads(bytes(response.body).decode("utf-8"))

    assert response.status_code == 404
    assert payload["ok"] is False
    assert payload["error"] == "not_found"
    assert "detail" not in payload
