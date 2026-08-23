"""Plan and execute final composite SMD PPFD validation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

from fspm_optics.fixtures.smd.positions import (
    SmdLayout,
    generate_proposed_led_layout,
)
from fspm_optics.fixtures.smd.power_schedule import (
    ModulePowerSchedule,
    build_optimized_module_schedule,
)
from fspm_optics.fixtures.smd.radiance_writer import (
    SmdRadianceDocument,
    build_smd_radiance_document,
)
from fspm_optics.geometry.room import (
    FEET_TO_METERS,
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
)
from fspm_optics.radiance.commands import (
    CommandSpec,
    build_baseline_rtrace_command,
    build_oconv_command,
)
from fspm_optics.radiance.options import replace_radiance_option_value
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.radiance.versioning import (
    RadianceInstallation,
    discover_radiance_installation,
)

from .artifacts import load_basis_workspace_artifacts, save_json_artifact
from .atomic import atomic_save_npy, atomic_write_text
from .parsing import parse_basis_column
from .source_compatibility import require_current_proposed_source_manifest

FloatArray = NDArray[np.float64]


class CompositeValidationError(RuntimeError):
    """A final composite plan or execution failed validation."""


@dataclass(frozen=True, slots=True)
class PpfdFieldMetrics:
    mean: float
    minimum: float
    maximum: float
    standard_deviation: float
    cv_percent: float

    def to_dict(self) -> dict[str, float]:
        return {
            "mean": self.mean,
            "min": self.minimum,
            "max": self.maximum,
            "std": self.standard_deviation,
            "cv_percent": self.cv_percent,
        }


@dataclass(frozen=True, slots=True)
class CompositeComparisonMetrics:
    sensor_count: int
    target_ppfd: float
    actual: PpfdFieldMetrics
    predicted: PpfdFieldMetrics
    rmse_actual_vs_predicted: float
    mae_actual_vs_predicted: float
    max_absolute_error: float
    relative_rmse_percent_of_target: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor_count": self.sensor_count,
            "target_ppfd": self.target_ppfd,
            "actual": self.actual.to_dict(),
            "predicted": self.predicted.to_dict(),
            "errors": {
                "rmse_actual_vs_predicted": self.rmse_actual_vs_predicted,
                "mae_actual_vs_predicted": self.mae_actual_vs_predicted,
                "max_absolute_error": self.max_absolute_error,
                "relative_rmse_percent_of_target": (
                    self.relative_rmse_percent_of_target
                ),
            },
            "threshold_policy": "report_only_no_hard_comparison_thresholds",
        }


@dataclass(frozen=True, slots=True)
class CompositeValidationPaths:
    room_path: Path
    sensor_path: Path
    schedule_path: Path
    predicted_ppfd_path: Path
    emitter_path: Path
    fixture_body_path: Path
    octree_path: Path
    rgb_output_path: Path
    ambient_cache_path: Path | None
    actual_ppfd_path: Path
    metrics_path: Path
    comparison_path: Path
    execution_path: Path


@dataclass(frozen=True, slots=True)
class CompositeValidationPlan:
    workspace: Path
    sensor_count: int
    target_ppfd: float
    layout: SmdLayout
    schedule: ModulePowerSchedule
    emitter_document: SmdRadianceDocument
    predicted_ppfd: FloatArray
    paths: CompositeValidationPaths
    oconv_command: CommandSpec
    rtrace_command: CommandSpec


@dataclass(frozen=True, slots=True)
class CompositeValidationResult:
    actual_ppfd: FloatArray
    comparison: CompositeComparisonMetrics
    plan: CompositeValidationPlan
    oconv_result: RunnerResult
    rtrace_result: RunnerResult
    radiance_installation: RadianceInstallation


def plan_composite_validation(
    workspace: str | Path,
) -> CompositeValidationPlan:
    """Validate solved artifacts and materialize a deterministic composite emitter."""

    root = Path(workspace).expanduser().resolve()
    basis = load_basis_workspace_artifacts(root)
    manifest = basis.manifest
    require_current_proposed_source_manifest(manifest)
    room_path = root / "room.rad"
    sensor_path = root / "sensors.pts"
    fixture_body_path = (
        root / "fixture_occlusion" / "fixture_body_instances.rad"
    )
    _validate_hashed_input(room_path, manifest.room_text_sha256, "room.rad")
    _validate_hashed_input(sensor_path, manifest.sensor_text_sha256, "sensors.pts")
    if (
        not manifest.fixture_occlusion_identity
        or not fixture_body_path.is_file()
    ):
        raise CompositeValidationError(
            "basis fixture occlusion is missing from final composite validation."
        )

    layout = generate_proposed_led_layout(
        manifest.room_length_m / FEET_TO_METERS,
        manifest.room_width_m / FEET_TO_METERS,
        proposed_layout_mode=manifest.proposed_layout_mode,
        proposed_ring_mode=manifest.proposed_ring_mode,
    )
    if layout.fixture_policy_id != manifest.fixture_policy_id:
        raise CompositeValidationError(
            "composite layout fixture policy does not match basis metadata."
        )
    if layout.control_zone_count != manifest.control_zone_count:
        raise CompositeValidationError(
            "composite layout control-zone count does not match basis metadata: "
            f"expected {manifest.control_zone_count}, got {layout.control_zone_count}."
        )
    if len(layout.modules) != manifest.layout_module_count:
        raise CompositeValidationError(
            "composite layout module count does not match basis metadata: "
            f"expected {manifest.layout_module_count}, got {len(layout.modules)}."
        )

    schedule_path = root / "optimized_module_schedule.json"
    schedule_payload = _load_json_object(schedule_path, "optimized module schedule")
    schedule = _validated_schedule(schedule_payload, layout)
    emitter_document = build_smd_radiance_document(layout, schedule)
    if emitter_document.metadata.module_profile_id != manifest.module_profile_id:
        raise CompositeValidationError(
            "final composite module profile does not match the basis manifest: "
            f"expected {manifest.module_profile_id!r}, got "
            f"{emitter_document.metadata.module_profile_id!r}."
        )
    if emitter_document.source_variant_cal_text is not None:
        raise CompositeValidationError(
            "final composite validation currently requires the native SMD source variant."
        )

    solution = _load_json_object(root / "basis_solution.json", "basis solution")
    target_ppfd = _finite_non_negative(
        solution.get("target_ppfd"),
        "basis solution target_ppfd",
    )
    _validate_solution_schedule(
        solution,
        schedule,
        expected_sensor_count=manifest.sensor_count,
    )
    predicted_path = root / "predicted_ppfd.npy"
    predicted = _load_ppfd_vector(
        predicted_path,
        expected_sensor_count=manifest.sensor_count,
        label="predicted PPFD",
    )

    emitter_path = root / "final_composite_emitters.rad"
    atomic_write_text(emitter_path, emitter_document.radiance_text)
    emitter_hash = _sha256_file(emitter_path)
    octree_path = root / "final_composite.oct"
    rgb_path = root / "final_composite.rgb"
    ambient_path = (
        None
        if manifest.ambient_cache_policy == "disabled"
        else root / (
            f"final_composite_{emitter_hash[:16]}_"
            f"{manifest.fixture_occlusion_identity[:16]}_"
            f"{PRODUCTION_ROOM_MODEL_IDENTITY_SHA256[:16]}.amb"
        )
    )
    radiance_options = list(manifest.radiance_options)
    if ambient_path is not None:
        radiance_options = replace_radiance_option_value(
            radiance_options,
            "-af",
            str(ambient_path),
        )
    oconv_command = build_oconv_command(
        (room_path, emitter_path, fixture_body_path),
        output_octree=octree_path,
        cwd=root,
        label="compile_final_composite",
    )
    raw_rtrace = build_baseline_rtrace_command(
        octree=octree_path,
        receiver_input=sensor_path,
        rgb_output=rgb_path,
        options=radiance_options,
        nthreads=manifest.nthreads,
        cwd=root,
    )
    rtrace_command = CommandSpec(
        argv=raw_rtrace.argv,
        stdin_path=raw_rtrace.stdin_path,
        stdout_path=raw_rtrace.stdout_path,
        stdout_mode=raw_rtrace.stdout_mode,
        cwd=raw_rtrace.cwd,
        env=raw_rtrace.env,
        label="trace_final_composite",
    )
    paths = CompositeValidationPaths(
        room_path=room_path,
        sensor_path=sensor_path,
        schedule_path=schedule_path,
        predicted_ppfd_path=predicted_path,
        emitter_path=emitter_path,
        fixture_body_path=fixture_body_path,
        octree_path=octree_path,
        rgb_output_path=rgb_path,
        ambient_cache_path=ambient_path,
        actual_ppfd_path=root / "final_composite_ppfd.npy",
        metrics_path=root / "final_composite_metrics.json",
        comparison_path=root / "final_composite_comparison.json",
        execution_path=root / "final_composite.execution.json",
    )
    return CompositeValidationPlan(
        workspace=root,
        sensor_count=manifest.sensor_count,
        target_ppfd=target_ppfd,
        layout=layout,
        schedule=schedule,
        emitter_document=emitter_document,
        predicted_ppfd=predicted,
        paths=paths,
        oconv_command=oconv_command,
        rtrace_command=rtrace_command,
    )


def execute_composite_validation(
    plan: CompositeValidationPlan,
    runner: LocalRunner,
    *,
    radiance_installation: RadianceInstallation | None = None,
    oconv_timeout_s: float | None = None,
    rtrace_timeout_s: float | None = None,
) -> CompositeValidationResult:
    """Execute the planned composite compile/trace and persist comparison artifacts."""

    installation = radiance_installation or discover_radiance_installation()
    oconv_command = _replace_executable(plan.oconv_command, installation.oconv.path)
    rtrace_command = _replace_executable(plan.rtrace_command, installation.rtrace.path)
    oconv_result = runner.run(oconv_command, timeout_s=oconv_timeout_s)
    if not oconv_result.success:
        raise CompositeValidationError(
            oconv_result.failure_message or "final composite scene compilation failed."
        )
    if not plan.paths.octree_path.is_file():
        raise CompositeValidationError(
            f"final composite compilation did not create: {plan.paths.octree_path}"
        )
    rtrace_result = runner.run(rtrace_command, timeout_s=rtrace_timeout_s)
    if not rtrace_result.success:
        raise CompositeValidationError(
            rtrace_result.failure_message or "final composite scalar trace failed."
        )
    if not plan.paths.rgb_output_path.is_file():
        raise CompositeValidationError(
            f"final composite trace did not create: {plan.paths.rgb_output_path}"
        )
    try:
        actual = parse_basis_column(
            plan.paths.rgb_output_path.read_text(encoding="utf-8"),
            expected_sensor_count=plan.sensor_count,
        )
    except ValueError as exc:
        raise CompositeValidationError(
            f"final composite PPFD output validation failed: {exc}"
        ) from exc
    comparison = compare_composite_ppfd(
        actual,
        plan.predicted_ppfd,
        target_ppfd=plan.target_ppfd,
    )
    atomic_save_npy(plan.paths.actual_ppfd_path, actual)
    save_json_artifact(
        plan.paths.metrics_path,
        {
            "schema_version": 1,
            "artifact_type": "fspm_optics_final_composite_ppfd_metrics",
            "sensor_count": comparison.sensor_count,
            "target_ppfd": comparison.target_ppfd,
            **comparison.actual.to_dict(),
        },
    )
    save_json_artifact(
        plan.paths.comparison_path,
        {
            "schema_version": 1,
            "artifact_type": "fspm_optics_final_composite_comparison",
            **comparison.to_dict(),
        },
    )
    save_json_artifact(
        plan.paths.execution_path,
        _execution_payload(
            plan,
            comparison,
            oconv_command,
            rtrace_command,
            oconv_result,
            rtrace_result,
            installation,
        ),
    )
    return CompositeValidationResult(
        actual_ppfd=actual,
        comparison=comparison,
        plan=plan,
        oconv_result=oconv_result,
        rtrace_result=rtrace_result,
        radiance_installation=installation,
    )


def compare_composite_ppfd(
    actual_ppfd: Sequence[float] | FloatArray,
    predicted_ppfd: Sequence[float] | FloatArray,
    *,
    target_ppfd: float,
) -> CompositeComparisonMetrics:
    """Return report-only final-vs-predicted metrics after strict validation."""

    actual = _ppfd_vector(actual_ppfd, "actual PPFD")
    predicted = _ppfd_vector(predicted_ppfd, "predicted PPFD")
    if actual.shape != predicted.shape:
        raise ValueError(
            "actual and predicted PPFD shapes must match: "
            f"actual={actual.shape}, predicted={predicted.shape}."
        )
    target = _finite_non_negative(target_ppfd, "target_ppfd")
    residual = actual - predicted
    rmse = float(np.sqrt(np.mean(np.square(residual))))
    mae = float(np.mean(np.abs(residual)))
    max_absolute_error = float(np.max(np.abs(residual)))
    relative_rmse = None if target == 0.0 else 100.0 * rmse / target
    return CompositeComparisonMetrics(
        sensor_count=len(actual),
        target_ppfd=target,
        actual=_field_metrics(actual),
        predicted=_field_metrics(predicted),
        rmse_actual_vs_predicted=rmse,
        mae_actual_vs_predicted=mae,
        max_absolute_error=max_absolute_error,
        relative_rmse_percent_of_target=relative_rmse,
    )


def _validated_schedule(
    payload: Mapping[str, Any],
    layout: SmdLayout,
) -> ModulePowerSchedule:
    schema_version = payload.get("schema_version", 1)
    if schema_version not in (1, 2):
        raise CompositeValidationError(
            "optimized module schedule schema version is unsupported."
        )
    if schema_version == 2 and (
        payload.get("proposed_layout_mode")
        != layout.proposed_layout_mode.value
        or payload.get("proposed_ring_mode")
        != layout.proposed_ring_mode.value
        or payload.get("module_pattern_id") != layout.module_pattern_id
        or payload.get("fixture_policy_id") != layout.fixture_policy_id
    ):
        raise CompositeValidationError(
            "optimized module schedule fixture composition does not match layout."
        )
    if payload.get("schedule_source") != "optimized_explicit_control_zones":
        raise CompositeValidationError(
            "optimized module schedule must use explicit optimized control zones."
        )
    if payload.get("control_zone_count") != layout.control_zone_count:
        raise CompositeValidationError(
            "optimized module schedule control-zone count does not match layout."
        )
    if payload.get("module_count") != len(layout.modules):
        raise CompositeValidationError(
            "optimized module schedule module count does not match layout."
        )
    zone_values = payload.get("watts_by_control_zone")
    if not isinstance(zone_values, list):
        raise CompositeValidationError(
            "optimized module schedule is missing watts_by_control_zone."
        )
    try:
        schedule = build_optimized_module_schedule(layout, zone_values)
    except ValueError as exc:
        raise CompositeValidationError(
            f"optimized module schedule is invalid: {exc}"
        ) from exc
    modules = payload.get("modules")
    if not isinstance(modules, list) or len(modules) != len(layout.modules):
        raise CompositeValidationError(
            "optimized module schedule must contain every scheduled module."
        )
    for expected_module, expected_watts, item in zip(
        layout.modules,
        schedule.watts_by_module,
        modules,
        strict=True,
    ):
        if not isinstance(item, Mapping):
            raise CompositeValidationError(
                "optimized module schedule entries must be JSON objects."
            )
        if (
            item.get("module_index") != expected_module.module_index
            or item.get("control_zone_index") != expected_module.control_zone_index
        ):
            raise CompositeValidationError(
                "optimized module schedule module indices or control zones do not "
                "match the deterministic layout."
            )
        watts = _finite_non_negative(item.get("watts"), "scheduled module watts")
        if not math.isclose(watts, expected_watts, rel_tol=1e-12, abs_tol=1e-12):
            raise CompositeValidationError(
                f"scheduled module {expected_module.module_index} wattage does not "
                "match its control-zone wattage."
            )
    return schedule


def _validate_solution_schedule(
    solution: Mapping[str, Any],
    schedule: ModulePowerSchedule,
    *,
    expected_sensor_count: int,
) -> None:
    if solution.get("success") is not True:
        raise CompositeValidationError("basis solution is not marked successful.")
    if solution.get("sensor_count") != expected_sensor_count:
        raise CompositeValidationError(
            "basis solution sensor_count does not match the basis workspace."
        )
    if solution.get("control_zone_count") != schedule.control_zone_count:
        raise CompositeValidationError(
            "basis solution control-zone count does not match the optimized schedule."
        )
    solution_watts = solution.get("watts_by_control_zone")
    if not isinstance(solution_watts, list) or len(solution_watts) != (
        schedule.control_zone_count
    ):
        raise CompositeValidationError(
            "basis solution must contain one wattage per control zone."
        )
    for index, (raw_watts, scheduled_watts) in enumerate(
        zip(solution_watts, schedule.watts_by_control_zone, strict=True)
    ):
        watts = _finite_non_negative(
            raw_watts,
            f"basis solution control-zone {index} watts",
        )
        if not math.isclose(watts, scheduled_watts, rel_tol=1e-12, abs_tol=1e-12):
            raise CompositeValidationError(
                "basis solution and optimized schedule wattages do not match at "
                f"control zone {index}."
            )


def _field_metrics(values: FloatArray) -> PpfdFieldMetrics:
    mean = float(values.mean())
    standard_deviation = float(values.std(ddof=0))
    cv_percent = 0.0 if mean == 0.0 else 100.0 * standard_deviation / mean
    return PpfdFieldMetrics(
        mean=mean,
        minimum=float(values.min()),
        maximum=float(values.max()),
        standard_deviation=standard_deviation,
        cv_percent=cv_percent,
    )


def _load_ppfd_vector(
    path: Path,
    *,
    expected_sensor_count: int,
    label: str,
) -> FloatArray:
    try:
        values = np.load(path, allow_pickle=False)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} artifact not found: {path}") from exc
    vector = _ppfd_vector(values, label)
    if vector.shape != (expected_sensor_count,):
        raise CompositeValidationError(
            f"{label} shape mismatch: expected ({expected_sensor_count},), "
            f"got {vector.shape}."
        )
    return vector


def _ppfd_vector(values: Sequence[float] | FloatArray, label: str) -> FloatArray:
    try:
        vector = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a numeric vector.") from exc
    if vector.ndim != 1 or vector.size == 0:
        raise ValueError(f"{label} must be a non-empty one-dimensional vector.")
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{label} contains non-finite values.")
    if np.any(vector < 0.0):
        raise ValueError(f"{label} contains negative PPFD values.")
    return np.asarray(vector, dtype=float)


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CompositeValidationError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise CompositeValidationError(f"{label} root must be a JSON object.")
    return payload


def _validate_hashed_input(path: Path, expected_hash: str | None, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"materialized {label} not found: {path}")
    actual_hash = _sha256_file(path)
    if expected_hash is None or actual_hash != expected_hash:
        raise CompositeValidationError(f"materialized {label} hash mismatch: {path}")


def _finite_non_negative(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CompositeValidationError(f"{label} must be finite and non-negative.")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise CompositeValidationError(f"{label} must be finite and non-negative.")
    return number


def _replace_executable(command: CommandSpec, executable: Path) -> CommandSpec:
    return CommandSpec(
        argv=(str(executable), *command.argv[1:]),
        stdin_path=command.stdin_path,
        stdout_path=command.stdout_path,
        stdout_mode=command.stdout_mode,
        cwd=command.cwd,
        env=command.env,
        label=command.label,
    )


def _command_dict(command: CommandSpec) -> dict[str, Any]:
    return {
        "argv": list(command.argv),
        "stdin_path": None if command.stdin_path is None else str(command.stdin_path),
        "stdout_path": None if command.stdout_path is None else str(command.stdout_path),
        "stdout_mode": command.stdout_mode,
        "cwd": None if command.cwd is None else str(command.cwd),
        "env": dict(sorted(command.env.items())),
        "label": command.label,
    }


def _execution_payload(
    plan: CompositeValidationPlan,
    comparison: CompositeComparisonMetrics,
    oconv_command: CommandSpec,
    rtrace_command: CommandSpec,
    oconv_result: RunnerResult,
    rtrace_result: RunnerResult,
    installation: RadianceInstallation,
) -> dict[str, Any]:
    paths = plan.paths
    return {
        "schema_version": 1,
        "artifact_type": "fspm_optics_final_composite_execution",
        "success": True,
        "sensor_count": plan.sensor_count,
        "target_ppfd": plan.target_ppfd,
        "wall_times_s": {
            "oconv": oconv_result.wall_time_s,
            "rtrace": rtrace_result.wall_time_s,
            "total": oconv_result.wall_time_s + rtrace_result.wall_time_s,
        },
        "paths": {
            "room": str(paths.room_path),
            "sensors": str(paths.sensor_path),
            "emitters": str(paths.emitter_path),
            "octree": str(paths.octree_path),
            "rgb_output": str(paths.rgb_output_path),
            "actual_ppfd": str(paths.actual_ppfd_path),
            "predicted_ppfd": str(paths.predicted_ppfd_path),
            "metrics": str(paths.metrics_path),
            "comparison": str(paths.comparison_path),
            "execution": str(paths.execution_path),
        },
        "hashes": {
            "room_sha256": _sha256_file(paths.room_path),
            "sensor_sha256": _sha256_file(paths.sensor_path),
            "emitter_sha256": _sha256_file(paths.emitter_path),
            "octree_sha256": _sha256_file(paths.octree_path),
            "rgb_output_sha256": _sha256_file(paths.rgb_output_path),
            "actual_ppfd_sha256": _sha256_file(paths.actual_ppfd_path),
            "predicted_ppfd_sha256": _sha256_file(paths.predicted_ppfd_path),
            "metrics_sha256": _sha256_file(paths.metrics_path),
            "comparison_sha256": _sha256_file(paths.comparison_path),
        },
        "commands": {
            "oconv": _command_dict(oconv_command),
            "rtrace": _command_dict(rtrace_command),
        },
        "comparison": comparison.to_dict(),
        "radiance_installation": installation.to_dict(),
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
