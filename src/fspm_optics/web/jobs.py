"""Thread-safe bounded execution and cursor-based job event snapshots."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
import time
from typing import Callable, Mapping
import uuid

from fspm_optics.application.domain import RunRequest, SYSTEM_DISPLAY_NAMES
from fspm_optics.application.proposed import (
    NativeRunError,
    NativeRunOutcome,
    validate_success_artifacts,
)
from fspm_optics.application.systems import run_system_baseline
from fspm_optics.layout.mode import (
    DEFAULT_PROPOSED_LAYOUT_MODE,
    ProposedLayoutMode,
    resolve_proposed_layout_mode,
)
from fspm_optics.transport.basis.atomic import atomic_write_text

from .workspaces import RuntimeWorkspaces, WorkspaceSafetyError, validate_run_id

RunExecutor = Callable[..., NativeRunOutcome]


class JobQueueFullError(RuntimeError):
    """The bounded local execution capacity is currently exhausted."""


class JobNotFoundError(LookupError):
    """No in-memory job exists for the requested safe identifier."""


@dataclass(slots=True)
class JobRecord:
    job_id: str
    run_id: str
    request: RunRequest
    workspace: Path
    state: str = "queued"
    events: list[dict[str, object]] = field(default_factory=list)
    result: dict[str, object] | None = None
    error: dict[str, object] | None = None


class JobManager:
    """One bounded worker by default, with one queued slot and no fake progress."""

    def __init__(
        self,
        workspaces: RuntimeWorkspaces,
        *,
        max_workers: int = 1,
        max_queued: int = 1,
        run_executor: RunExecutor = run_system_baseline,
        proposed_layout_mode: ProposedLayoutMode | str = DEFAULT_PROPOSED_LAYOUT_MODE,
    ) -> None:
        if max_workers <= 0 or max_queued < 0:
            raise ValueError("worker and queue bounds are invalid.")
        self.workspaces = workspaces
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="fspm-optics-native",
        )
        self._capacity = threading.BoundedSemaphore(max_workers + max_queued)
        self._run_executor = run_executor
        self.proposed_layout_mode = resolve_proposed_layout_mode(
            proposed_layout_mode
        )
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.RLock()
        self._accepting = False

    def start(self) -> None:
        with self._lock:
            self._accepting = True

    def submit(self, request: RunRequest) -> tuple[str, str]:
        with self._lock:
            if not self._accepting:
                raise RuntimeError("job manager is not accepting work.")
        if not self._capacity.acquire(blocking=False):
            raise JobQueueFullError("native run queue is full.")
        run_id = uuid.uuid4().hex
        job_id = uuid.uuid4().hex
        try:
            workspace = self.workspaces.allocate_staging(run_id)
            record = JobRecord(job_id, run_id, request, workspace)
            with self._lock:
                self._jobs[job_id] = record
            self._append_event(
                record,
                "job.queued",
                "Run accepted by the bounded native worker.",
                {"state": "queued"},
            )
            self._executor.submit(self._execute, record)
        except BaseException:
            self._capacity.release()
            raise
        return job_id, run_id

    def snapshot(self, job_id: str, cursor: int) -> dict[str, object]:
        validated = validate_run_id(job_id)
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise ValueError("cursor must be a non-negative integer.")
        with self._lock:
            record = self._jobs.get(validated)
            if record is None:
                raise JobNotFoundError(validated)
            fresh = [event.copy() for event in record.events if int(event["cursor"]) > cursor]
            next_cursor = int(record.events[-1]["cursor"]) if record.events else cursor
            return {
                "job_id": record.job_id,
                "run_id": record.run_id,
                "state": record.state,
                "analysis_scope": record.request.analysis_scope.value,
                "lighting_target_mode": (
                    record.request.lighting_target_mode.value
                    if hasattr(record.request, "lighting_target_mode")
                    else None
                ),
                "cursor": cursor,
                "next_cursor": next_cursor,
                "events": fresh,
                "logs": [
                    {"cursor": event["cursor"], "line": event["message"]}
                    for event in fresh
                ],
                "terminal": record.state in {"succeeded", "failed"},
                "result": None if record.result is None else record.result.copy(),
                "error": None if record.error is None else record.error.copy(),
            }

    def shutdown(self) -> None:
        with self._lock:
            self._accepting = False
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _execute(self, record: JobRecord) -> None:
        try:
            with self._lock:
                record.state = "running"
            self._append_event(
                record,
                "job.running",
                f"Native {SYSTEM_DISPLAY_NAMES[record.request.system]} run started.",
                {"state": "running", "system_id": record.request.system},
            )
            outcome = self._run_executor(
                run_id=record.run_id,
                request=record.request,
                workspace=record.workspace,
                event_sink=lambda kind, message, data=None: self._append_event(
                    record, kind, message, data
                ),
            )
            if not isinstance(outcome, NativeRunOutcome):
                raise NativeRunError(
                    "invalid_run_result",
                    "dispatch",
                    "run executor returned an unsupported result contract.",
                )
            if (
                outcome.run_id != record.run_id
                or outcome.system_id != record.request.system
            ):
                raise NativeRunError(
                    "result_identity_mismatch",
                    "dispatch",
                    "run result identity does not match the authorized request.",
                    details={
                        "expected_run_id": record.run_id,
                        "actual_run_id": outcome.run_id,
                        "expected_system_id": record.request.system,
                        "actual_system_id": outcome.system_id,
                    },
                )
            self._append_event(
                record,
                "artifacts.validating",
                "Validating required success artifacts before promotion.",
            )
            validate_success_artifacts(
                record.workspace,
                expected_run_id=record.run_id,
                expected_system_id=record.request.system,
                expected_request=record.request.to_dict(),
            )
            self._append_event(
                record,
                "artifacts.validated",
                "Success artifacts passed validation.",
            )
            result_payload: dict[str, object] = {
                "run_id": record.run_id,
                "system_id": outcome.system_id,
                "analysis_scope": record.request.analysis_scope.value,
                "lighting_target_mode": (
                    record.request.lighting_target_mode.value
                    if hasattr(record.request, "lighting_target_mode")
                    else None
                ),
                "target_feasible": outcome.target_feasible,
                "target_infeasibility": outcome.target_infeasibility,
                "metrics_url": f"/api/runs/{record.run_id}/metrics",
                "manifest_url": f"/api/runs/{record.run_id}/manifest",
                "physical_source_state_url": (
                    f"/api/runs/{record.run_id}/artifacts/"
                    "physical-source-state.json"
                ),
                "ppfd_csv_url": (
                    f"/api/runs/{record.run_id}/artifacts/ppfd.csv"
                ),
                "baseline_leaf_position_uniformity_url": (
                    f"/api/runs/{record.run_id}/artifacts/"
                    "baseline-leaf-position-uniformity.v1.json"
                ),
                "plant_layout_viewer_url": (
                    f"/runs/{record.run_id}/viewer/index.html"
                ),
                "ppfd_heatmap_url": (
                    f"/api/runs/{record.run_id}/artifacts/ppfd-heatmap.png"
                ),
                "ppfd_heatmap_overlay_url": (
                    f"/api/runs/{record.run_id}/artifacts/ppfd-heatmap-overlay.png"
                ),
                "visualization_metadata_url": (
                    f"/api/runs/{record.run_id}/artifacts/visualization.json"
                ),
                "ppfd_scatter_viewer_url": (
                    f"/runs/{record.run_id}/scatter/index.html"
                ),
            }
            if outcome.metrics.get("spectral_basis") is not None:
                result_payload["spectral_basis"] = outcome.metrics[
                    "spectral_basis"
                ]
            if outcome.metrics.get("proposed_control") is not None:
                result_payload["proposed_control"] = outcome.metrics[
                    "proposed_control"
                ]
            if outcome.metrics.get("proposed_layout") is not None:
                result_payload["proposed_layout"] = outcome.metrics[
                    "proposed_layout"
                ]
            if outcome.metrics.get("proposed_source") is not None:
                result_payload["proposed_source"] = outcome.metrics[
                    "proposed_source"
                ]
            multispectral = outcome.manifest.get("multispectral_transport")
            if isinstance(multispectral, dict):
                public_artifacts = multispectral.get("public_artifacts")
                if not isinstance(public_artifacts, dict):
                    raise NativeRunError(
                        "invalid_multispectral_publication",
                        "promotion",
                        "multispectral public artifact inventory is missing.",
                    )
                prefix = "fspm-transport/"
                if any(
                    not isinstance(relative, str)
                    or not relative.startswith(prefix)
                    for relative in public_artifacts.values()
                ):
                    raise NativeRunError(
                        "invalid_multispectral_publication",
                        "promotion",
                        "multispectral public artifact path is invalid.",
                    )
                result_payload["multispectral_artifact_urls"] = {
                    str(name): (
                        f"/api/runs/{record.run_id}/fspm/"
                        f"{relative.removeprefix(prefix)}"
                    )
                    for name, relative in public_artifacts.items()
                }
            aggregation = outcome.manifest.get("fspm_scientific_aggregation")
            if isinstance(aggregation, dict):
                public_artifacts = aggregation.get("public_artifacts")
                if not isinstance(public_artifacts, dict):
                    raise NativeRunError(
                        "invalid_fspm_aggregation_publication",
                        "promotion",
                        "FSPM aggregation public artifact inventory is missing.",
                    )
                prefix = "fspm-aggregation/"
                if any(
                    not isinstance(relative, str)
                    or not relative.startswith(prefix)
                    for relative in public_artifacts.values()
                ):
                    raise NativeRunError(
                        "invalid_fspm_aggregation_publication",
                        "promotion",
                        "FSPM aggregation public artifact path is invalid.",
                    )
                result_payload["fspm_scientific_artifact_urls"] = {
                    str(name): (
                        f"/api/runs/{record.run_id}/fspm/"
                        f"{relative.removeprefix(prefix)}"
                    )
                    for name, relative in public_artifacts.items()
                }
            promoted = self.workspaces.promote(record.run_id)
            with self._lock:
                record.workspace = promoted
            try:
                self._append_event(
                    record,
                    "job.succeeded",
                    "Run promoted atomically and is available by run ID.",
                    {"state": "succeeded"},
                )
            except OSError:
                # Promotion is the success boundary. A late diagnostic append
                # must not demote or expose the completed run as a failure.
                pass
            with self._lock:
                record.result = result_payload
                record.state = "succeeded"
        except BaseException as exc:
            self._fail(record, exc)
        finally:
            self._capacity.release()

    def _fail(self, record: JobRecord, exc: BaseException) -> None:
        if isinstance(exc, NativeRunError):
            error = exc.to_dict()
        else:
            error = {
                "code": "native_run_failed",
                "stage": "internal",
                "message": str(exc) or exc.__class__.__name__,
                "details": {"exception_type": exc.__class__.__name__},
            }
        try:
            self._append_event(
                record,
                "job.failed",
                str(error["message"]),
                {"state": "failed", "error": error},
            )
        except (OSError, RuntimeError):
            pass
        try:
            atomic_write_text(
                record.workspace / "failure.json",
                json.dumps(error, indent=2, sort_keys=True) + "\n",
            )
        except OSError:
            pass
        try:
            retained = self.workspaces.retain_failed(record.run_id)
        except (OSError, RuntimeError, WorkspaceSafetyError):
            retained = record.workspace
        with self._lock:
            record.workspace = retained
            record.state = "failed"
            record.error = error

    def _append_event(
        self,
        record: JobRecord,
        event_type: str,
        message: str,
        data: Mapping[str, object] | None = None,
    ) -> None:
        with self._lock:
            cursor = len(record.events) + 1
            event: dict[str, object] = {
                "cursor": cursor,
                "timestamp_unix_s": time.time(),
                "type": str(event_type),
                "message": str(message),
                "data": dict(data or {}),
            }
            record.events.append(event)
            workspace = record.workspace
            with (workspace / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
            with (workspace / "run.log").open("a", encoding="utf-8") as handle:
                handle.write(f"[{cursor}] {message}\n")
