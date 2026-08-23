"""Resumable controller for the authenticated fixed production plan.

Importing this module never constructs a runtime workspace or native worker.
The bounded worker is imported lazily only by ``BoundedNativeSweepExecutor``
after the explicit execution path selects it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import time
from typing import Callable, Mapping, Protocol
import uuid

from fspm_optics.precomputed.compact_bundle import (
    CompactBundleExport,
    CompactBundleStatus,
    CompactBundleValidation,
    export_compact_bundle,
    validate_compact_bundle,
)
from fspm_optics.precomputed.fixed_plan import (
    FixedSweepCase,
    FixedSweepPlan,
    build_fixed_sweep_plan,
)
from fspm_optics.radiance.executables import resolve_executable
from fspm_optics.viewer.fixtures import ASSET_REGISTRY


PROGRESS_SCHEMA_ID = "fspm-optics.fixed-precomputed-sweep-progress"
PROGRESS_SCHEMA_VERSION = 1
PROGRESS_FILENAME = "fixed-sweep-progress.v1.json"


class SweepExecutionState(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FixedCaseStatus:
    case: FixedSweepCase
    path: Path
    validation: CompactBundleValidation

    @property
    def complete(self) -> bool:
        return self.validation.status is CompactBundleStatus.VALID

    def to_payload(self) -> dict[str, object]:
        return {
            "ordinal": self.case.ordinal,
            "case_id": self.case.case_id,
            "path": str(self.path),
            "status": self.validation.status.value,
            "message": self.validation.message,
            "bundle_identity_sha256": (
                self.validation.bundle_identity_sha256
            ),
        }


@dataclass(frozen=True, slots=True)
class FixedSweepStatus:
    plan_identity_sha256: str
    output_root: Path
    cases: tuple[FixedCaseStatus, ...]

    @property
    def valid_count(self) -> int:
        return sum(item.complete for item in self.cases)

    @property
    def remaining_count(self) -> int:
        return len(self.cases) - self.valid_count

    @property
    def invalid_count(self) -> int:
        return sum(
            item.validation.status
            not in {CompactBundleStatus.VALID, CompactBundleStatus.MISSING}
            for item in self.cases
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "plan_identity_sha256": self.plan_identity_sha256,
            "output_root": str(self.output_root),
            "total_cases": len(self.cases),
            "valid_or_skipped_cases": self.valid_count,
            "missing_or_pending_cases": sum(
                item.validation.status is CompactBundleStatus.MISSING
                for item in self.cases
            ),
            "invalid_or_incompatible_cases": self.invalid_count,
            "remaining_cases": self.remaining_count,
            "cases": [item.to_payload() for item in self.cases],
        }


@dataclass(frozen=True, slots=True)
class FixedSweepExecutionResult:
    plan_identity_sha256: str
    initial_status: FixedSweepStatus
    final_status: FixedSweepStatus
    executed_case_ids: tuple[str, ...]
    skipped_case_ids: tuple[str, ...]


class FixedCaseExecutor(Protocol):
    def __call__(self, case: FixedSweepCase) -> Path:
        """Execute one native case and return its completed runtime directory."""


ProgressSink = Callable[[str, FixedSweepCase | None, Mapping[str, object]], None]
BundleExporter = Callable[..., CompactBundleExport]


def validate_output_root(
    output_root: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> Path:
    """Resolve one bounded output root without creating or changing it."""

    raw = Path(output_root).expanduser().absolute()
    for component in (raw, *raw.parents):
        if component.exists() and component.is_symlink():
            raise ValueError(
                "precomputed output path components must not be symlinks."
            )
    resolved = raw.resolve()
    forbidden = {Path(resolved.anchor), Path.home().resolve()}
    if repository_root is not None:
        forbidden.add(Path(repository_root).expanduser().resolve())
    if resolved in forbidden:
        raise ValueError("precomputed output root is too broad or protected.")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("precomputed output root must be a directory.")
    return resolved


def inspect_fixed_sweep(
    output_root: str | Path,
    *,
    plan: FixedSweepPlan | None = None,
) -> FixedSweepStatus:
    """Freshly validate all 24 destinations; no path is created or changed."""

    fixed_plan = plan or build_fixed_sweep_plan()
    root = validate_output_root(output_root)
    cases_list: list[FixedCaseStatus] = []
    for case in fixed_plan.cases:
        path = _validated_case_output_path(root, case)
        cases_list.append(
            FixedCaseStatus(
                case=case,
                path=path,
                validation=validate_compact_bundle(
                    path,
                    **case.bundle_validation_expectations(
                        fixed_plan.plan_identity_sha256
                    ),
                ),
            )
        )
    cases = tuple(cases_list)
    return FixedSweepStatus(fixed_plan.plan_identity_sha256, root, cases)


def _validated_case_output_path(root: Path, case: FixedSweepCase) -> Path:
    path = case.output_path(root)
    for component in (path, *path.parents):
        if component == root.parent:
            break
        if component.exists() and component.is_symlink():
            raise ValueError("fixed case output path must not contain symlinks.")
    if not path.resolve().is_relative_to(root):
        raise ValueError("fixed case output path escapes the output root.")
    return path


def execute_fixed_sweep(
    output_root: str | Path,
    executor: FixedCaseExecutor,
    *,
    plan: FixedSweepPlan | None = None,
    exporter: BundleExporter = export_compact_bundle,
    progress_sink: ProgressSink | None = None,
) -> FixedSweepExecutionResult:
    """Sequentially execute only cases lacking an exact valid compact bundle."""

    fixed_plan = plan or build_fixed_sweep_plan()
    root = validate_output_root(output_root)
    initial = inspect_fixed_sweep(root, plan=fixed_plan)
    root.mkdir(parents=True, exist_ok=True)
    skipped = [item.case.case_id for item in initial.cases if item.complete]
    executed: list[str] = []
    failed: list[str] = []
    _write_progress(root, fixed_plan, initial, state=SweepExecutionState.PENDING)

    for initial_case in initial.cases:
        case = initial_case.case
        if initial_case.complete:
            _emit(
                progress_sink,
                "case.skipped",
                case,
                {
                    "path": str(initial_case.path),
                    "bundle_identity_sha256": (
                        initial_case.validation.bundle_identity_sha256
                    ),
                },
            )
            continue
        current = inspect_fixed_sweep(root, plan=fixed_plan)
        fresh_case = current.cases[case.ordinal - 1]
        if fresh_case.case.case_id != case.case_id:
            raise RuntimeError("fixed plan ordering changed during execution.")
        if fresh_case.complete:
            skipped.append(case.case_id)
            _emit(
                progress_sink,
                "case.skipped",
                case,
                {
                    "path": str(fresh_case.path),
                    "bundle_identity_sha256": (
                        fresh_case.validation.bundle_identity_sha256
                    ),
                    "discovered_after_initial_status": True,
                },
            )
            continue
        _emit(
            progress_sink,
            "case.executing",
            case,
            {"path": str(initial_case.path)},
        )
        _write_progress(
            root,
            fixed_plan,
            current,
            state=SweepExecutionState.EXECUTING,
            current_case=case,
            completed_case_ids=tuple(executed),
            failed_case_ids=tuple(failed),
        )
        try:
            completed_runtime = Path(executor(case)).resolve(strict=True)
            if not completed_runtime.is_dir():
                raise ValueError("case executor did not return a completed runtime.")
            destination = fresh_case.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            exported = exporter(
                completed_runtime,
                destination,
                fixed_case_binding=case.binding(fixed_plan.plan_identity_sha256),
                fixed_plan_inputs=case.compatibility_inputs,
            )
            validation = validate_compact_bundle(
                destination,
                **case.bundle_validation_expectations(
                    fixed_plan.plan_identity_sha256
                ),
            )
            if not validation.valid:
                raise RuntimeError(
                    "published compact bundle failed exact resume validation: "
                    f"{validation.status.value}: {validation.message}"
                )
            executed.append(case.case_id)
            _emit(
                progress_sink,
                "case.published",
                case,
                {
                    "path": str(destination),
                    "bundle_identity_sha256": exported.bundle_identity_sha256,
                },
            )
            refreshed = inspect_fixed_sweep(root, plan=fixed_plan)
            _write_progress(
                root,
                fixed_plan,
                refreshed,
                state=SweepExecutionState.COMPLETED,
                current_case=case,
                completed_case_ids=tuple(executed),
                failed_case_ids=tuple(failed),
            )
        except BaseException as exc:
            failed.append(case.case_id)
            failed_status = inspect_fixed_sweep(root, plan=fixed_plan)
            _write_progress(
                root,
                fixed_plan,
                failed_status,
                state=SweepExecutionState.FAILED,
                current_case=case,
                failure=str(exc) or exc.__class__.__name__,
                completed_case_ids=tuple(executed),
                failed_case_ids=tuple(failed),
            )
            _emit(
                progress_sink,
                "case.failed",
                case,
                {"message": str(exc) or exc.__class__.__name__},
            )
            raise

    final = inspect_fixed_sweep(root, plan=fixed_plan)
    if final.valid_count != fixed_plan.case_count:
        raise RuntimeError("fixed sweep ended without 24 valid compact bundles.")
    _write_progress(
        root,
        fixed_plan,
        final,
        state=SweepExecutionState.COMPLETED,
        completed_case_ids=tuple(executed),
        failed_case_ids=tuple(failed),
    )
    _emit(progress_sink, "plan.completed", None, final.to_payload())
    return FixedSweepExecutionResult(
        plan_identity_sha256=fixed_plan.plan_identity_sha256,
        initial_status=initial,
        final_status=final,
        executed_case_ids=tuple(executed),
        skipped_case_ids=tuple(skipped),
    )


def preflight_fixed_sweep_execution(
    output_root: str | Path,
    *,
    repository_root: str | Path,
    plan: FixedSweepPlan | None = None,
) -> dict[str, object]:
    """Validate execution prerequisites without initializing the worker."""

    fixed_plan = plan or build_fixed_sweep_plan()
    root = validate_output_root(output_root, repository_root=repository_root)
    if fixed_plan.case_count != 24:
        raise ValueError("production preflight requires the exact 24-case plan.")
    executables = {
        name: str(resolve_executable(name, label=name))
        for name in ("ies2rad", "oconv", "rtrace")
    }
    from fspm_optics.fixtures.conventional_led.resources import (
        CONVENTIONAL_RESOURCE_HASHES,
        CONVENTIONAL_RESOURCE_NAMES,
        conventional_resource_bytes,
    )
    from fspm_optics.fixtures.hps.resources import (
        HPS_RESOURCE_HASHES,
        HPS_RESOURCE_NAMES,
        hps_resource_bytes,
    )

    source_assets = [
        {
            "resource_name": name,
            "byte_size": len(data),
            "sha256": expected,
        }
        for names, loader, hashes in (
            (
                CONVENTIONAL_RESOURCE_NAMES,
                conventional_resource_bytes,
                CONVENTIONAL_RESOURCE_HASHES,
            ),
            (HPS_RESOURCE_NAMES, hps_resource_bytes, HPS_RESOURCE_HASHES),
        )
        for name in names
        for data, expected in ((loader(name), hashes[name]),)
    ]
    assets: list[dict[str, object]] = []
    for asset in ASSET_REGISTRY:
        source = resources.files("fspm_optics").joinpath(
            "resources", "viewer", *Path(asset.resource_path).parts
        )
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != asset.byte_size or digest != asset.sha256:
            raise ValueError(f"packaged fixture asset failed identity: {asset.asset_id}")
        assets.append(
            {
                "asset_id": asset.asset_id,
                "byte_size": len(data),
                "sha256": digest,
            }
        )
    return {
        "plan_identity_sha256": fixed_plan.plan_identity_sha256,
        "case_count": fixed_plan.case_count,
        "output_root": str(root),
        "default_concurrency": 1,
        "executables": executables,
        "source_assets": source_assets,
        "fixture_assets": assets,
    }


class BoundedNativeSweepExecutor:
    """Explicit, sequential adapter over the existing bounded JobManager."""

    def __init__(
        self,
        *,
        runtime_root: str | Path,
        repository_root: str | Path,
        poll_interval_s: float = 0.25,
    ) -> None:
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.repository_root = Path(repository_root).expanduser().resolve()
        self.poll_interval_s = float(poll_interval_s)
        if self.poll_interval_s <= 0.0:
            raise ValueError("native job polling interval must be positive.")
        self._workspaces: object | None = None
        self._manager: object | None = None

    def __enter__(self) -> "BoundedNativeSweepExecutor":
        # These imports are intentionally unreachable from plan/status commands.
        from fspm_optics.web.jobs import JobManager
        from fspm_optics.web.workspaces import RuntimeWorkspaces

        workspaces = RuntimeWorkspaces(self.runtime_root, self.repository_root)
        workspaces.start_server()
        try:
            manager = JobManager(workspaces, max_workers=1, max_queued=0)
            manager.start()
        except BaseException:
            workspaces.stop_server()
            raise
        self._workspaces = workspaces
        self._manager = manager
        return self

    def __exit__(self, *_exc: object) -> None:
        manager, self._manager = self._manager, None
        workspaces, self._workspaces = self._workspaces, None
        try:
            if manager is not None:
                manager.shutdown()  # type: ignore[attr-defined]
        finally:
            if workspaces is not None:
                # Preserve the existing managed-runtime cleanup policy.
                workspaces.stop_server()  # type: ignore[attr-defined]

    def __call__(self, case: FixedSweepCase) -> Path:
        if self._manager is None or self._workspaces is None:
            raise RuntimeError("bounded native executor is not active.")
        job_id, run_id = self._manager.submit(case.request)  # type: ignore[attr-defined]
        cursor = 0
        while True:
            snapshot = self._manager.snapshot(job_id, cursor)  # type: ignore[attr-defined]
            cursor = int(snapshot["next_cursor"])
            if snapshot["terminal"]:
                if snapshot["state"] != "succeeded":
                    error = snapshot.get("error")
                    raise RuntimeError(f"native fixed-sweep case failed: {error}")
                return self._workspaces.completed_run(run_id)  # type: ignore[attr-defined]
            time.sleep(self.poll_interval_s)


def _write_progress(
    root: Path,
    plan: FixedSweepPlan,
    status: FixedSweepStatus,
    *,
    state: SweepExecutionState,
    current_case: FixedSweepCase | None = None,
    failure: str | None = None,
    completed_case_ids: tuple[str, ...] = (),
    failed_case_ids: tuple[str, ...] = (),
) -> None:
    payload = {
        "schema_id": PROGRESS_SCHEMA_ID,
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "plan_identity_sha256": plan.plan_identity_sha256,
        "state": state.value,
        "current_case_id": None if current_case is None else current_case.case_id,
        "failure": failure,
        "executed_completed_cases": len(completed_case_ids),
        "executed_completed_case_ids": list(completed_case_ids),
        "validated_completed_cases": status.valid_count,
        "preexisting_or_skipped_cases": max(
            0, status.valid_count - len(completed_case_ids)
        ),
        "failed_cases": len(failed_case_ids),
        "failed_case_ids": list(failed_case_ids),
        "remaining_cases": status.remaining_count,
        "status": status.to_payload(),
        "completion_authority": "fresh_compact_bundle_validation",
    }
    destination = root / PROGRESS_FILENAME
    temporary = root / f".{PROGRESS_FILENAME}.{uuid.uuid4().hex}.partial"
    data = json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
    ) + "\n"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _emit(
    sink: ProgressSink | None,
    event: str,
    case: FixedSweepCase | None,
    data: Mapping[str, object],
) -> None:
    if sink is not None:
        sink(event, case, data)


__all__ = [
    "PROGRESS_FILENAME",
    "BoundedNativeSweepExecutor",
    "FixedCaseExecutor",
    "FixedCaseStatus",
    "FixedSweepExecutionResult",
    "FixedSweepStatus",
    "SweepExecutionState",
    "execute_fixed_sweep",
    "inspect_fixed_sweep",
    "preflight_fixed_sweep_execution",
    "validate_output_root",
]
