"""Native local execution of isolated SMD basis columns."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from fspm_optics.fixtures.occlusion import compile_fixture_occlusion
from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.radiance.versioning import (
    RadianceInstallation,
    discover_radiance_installation,
)

from .artifacts import save_basis_execution_summary, save_basis_matrix
from .atomic import atomic_save_npy, atomic_write_text
from .parsing import parse_basis_column, stack_basis_columns
from .workspace import MaterializedBasisWorkspace

FloatArray = NDArray[np.float64]


class BasisColumnExecutionError(RuntimeError):
    """A planned native basis-column execution did not complete safely."""


class BasisMatrixExecutionError(RuntimeError):
    """A complete native basis-matrix execution did not validate safely."""


@dataclass(frozen=True, slots=True)
class BasisColumnExecutionMetadata:
    control_zone_index: int
    sensor_count: int
    column_shape: tuple[int, ...]
    oconv_wall_time_s: float
    rtrace_wall_time_s: float
    octree_path: Path
    rgb_output_path: Path
    column_path: Path
    metadata_path: Path
    room_sha256: str
    sensor_sha256: str
    emitter_sha256: str
    octree_sha256: str
    rgb_output_sha256: str
    column_file_sha256: str
    oconv_command: CommandSpec
    rtrace_command: CommandSpec
    radiance_installation: RadianceInstallation

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "control_zone_index": self.control_zone_index,
            "sensor_count": self.sensor_count,
            "column_shape": list(self.column_shape),
            "wall_times_s": {
                "oconv": self.oconv_wall_time_s,
                "rtrace": self.rtrace_wall_time_s,
            },
            "paths": {
                "octree": str(self.octree_path),
                "rgb_output": str(self.rgb_output_path),
                "column": str(self.column_path),
                "metadata": str(self.metadata_path),
            },
            "hashes": {
                "room_sha256": self.room_sha256,
                "sensor_sha256": self.sensor_sha256,
                "emitter_sha256": self.emitter_sha256,
                "octree_sha256": self.octree_sha256,
                "rgb_output_sha256": self.rgb_output_sha256,
                "column_file_sha256": self.column_file_sha256,
            },
            "commands": {
                "oconv": _command_dict(self.oconv_command),
                "rtrace": _command_dict(self.rtrace_command),
            },
            "radiance_installation": self.radiance_installation.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class BasisColumnExecutionResult:
    column: FloatArray
    metadata: BasisColumnExecutionMetadata
    oconv_result: RunnerResult
    rtrace_result: RunnerResult


@dataclass(frozen=True, slots=True)
class BasisMatrixColumnSummary:
    control_zone_index: int
    oconv_wall_time_s: float
    rtrace_wall_time_s: float
    emitter_sha256: str
    octree_sha256: str
    rgb_output_sha256: str
    column_file_sha256: str
    column_path: Path

    @property
    def total_wall_time_s(self) -> float:
        return self.oconv_wall_time_s + self.rtrace_wall_time_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "control_zone_index": self.control_zone_index,
            "wall_times_s": {
                "oconv": self.oconv_wall_time_s,
                "rtrace": self.rtrace_wall_time_s,
                "total": self.total_wall_time_s,
            },
            "hashes": {
                "emitter_sha256": self.emitter_sha256,
                "octree_sha256": self.octree_sha256,
                "rgb_output_sha256": self.rgb_output_sha256,
                "column_file_sha256": self.column_file_sha256,
            },
            "column_path": str(self.column_path),
        }


@dataclass(frozen=True, slots=True)
class BasisMatrixExecutionMetadata:
    control_zone_count: int
    sensor_count: int
    matrix_shape: tuple[int, int]
    columns: tuple[BasisMatrixColumnSummary, ...]
    total_wall_time_s: float
    matrix_path: Path
    summary_path: Path
    matrix_sha256: str
    radiance_installation: RadianceInstallation

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "control_zone_count": self.control_zone_count,
            "sensor_count": self.sensor_count,
            "matrix_shape": list(self.matrix_shape),
            "per_column": [column.to_dict() for column in self.columns],
            "total_wall_time_s": self.total_wall_time_s,
            "paths": {
                "matrix": str(self.matrix_path),
                "summary": str(self.summary_path),
            },
            "hashes": {"matrix_sha256": self.matrix_sha256},
            "radiance_installation": self.radiance_installation.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class BasisMatrixExecutionResult:
    matrix: FloatArray
    columns: tuple[BasisColumnExecutionResult, ...]
    metadata: BasisMatrixExecutionMetadata


def execute_basis_column(
    workspace: MaterializedBasisWorkspace,
    column_index: int,
    runner: LocalRunner,
    *,
    radiance_installation: RadianceInstallation | None = None,
    oconv_timeout_s: float | None = None,
    rtrace_timeout_s: float | None = None,
) -> BasisColumnExecutionResult:
    """Compile, trace, parse, and persist one planned isolated basis column."""

    if (
        isinstance(column_index, bool)
        or not isinstance(column_index, int)
        or not 0 <= column_index < len(workspace.generation_plan.columns)
    ):
        raise ValueError(
            "column_index must identify an existing zero-based control zone."
        )
    _validate_materialized_inputs(workspace, column_index)
    installation = radiance_installation or discover_radiance_installation()
    if workspace.fixture_occlusion is None:
        raise BasisColumnExecutionError(
            "materialized basis workspace lacks fixture occlusion."
        )
    compile_fixture_occlusion(
        workspace.fixture_occlusion,
        runner,
        oconv_executable=installation.oconv.path,
        timeout_s=oconv_timeout_s,
    )
    column_plan = workspace.generation_plan.columns[column_index]
    oconv_command = _replace_executable(
        column_plan.oconv_command, installation.oconv.path
    )
    rtrace_command = _replace_executable(
        column_plan.rtrace_command, installation.rtrace.path
    )

    oconv_result = runner.run(oconv_command, timeout_s=oconv_timeout_s)
    if not oconv_result.success:
        raise BasisColumnExecutionError(
            oconv_result.failure_message or "Radiance scene compilation failed."
        )
    if not column_plan.octree_path.is_file():
        raise BasisColumnExecutionError(
            f"scene compilation did not create octree: {column_plan.octree_path}"
        )

    rtrace_result = runner.run(rtrace_command, timeout_s=rtrace_timeout_s)
    if not rtrace_result.success:
        raise BasisColumnExecutionError(
            rtrace_result.failure_message or "Radiance scalar trace failed."
        )
    if not column_plan.rgb_output_path.is_file():
        raise BasisColumnExecutionError(
            "scalar trace did not create output: "
            f"{column_plan.rgb_output_path}"
        )
    rgb_text = column_plan.rgb_output_path.read_text(encoding="utf-8")
    try:
        column = parse_basis_column(
            rgb_text,
            expected_sensor_count=workspace.manifest.sensor_count,
        )
    except ValueError as exc:
        raise BasisColumnExecutionError(
            f"basis column {column_index} output validation failed: {exc}"
        ) from exc

    column_path = workspace.generation_plan.output_directory / (
        f"basis_control_zone_{column_index:03d}.npy"
    )
    atomic_save_npy(column_path, column)
    metadata_path = workspace.generation_plan.output_directory / (
        f"basis_control_zone_{column_index:03d}.execution.json"
    )
    metadata = BasisColumnExecutionMetadata(
        control_zone_index=column_index,
        sensor_count=workspace.manifest.sensor_count,
        column_shape=tuple(column.shape),
        oconv_wall_time_s=oconv_result.wall_time_s,
        rtrace_wall_time_s=rtrace_result.wall_time_s,
        octree_path=column_plan.octree_path,
        rgb_output_path=column_plan.rgb_output_path,
        column_path=column_path,
        metadata_path=metadata_path,
        room_sha256=_sha256_file(workspace.room_path),
        sensor_sha256=_sha256_file(workspace.sensor_path),
        emitter_sha256=_sha256_file(workspace.emitter_paths[column_index]),
        octree_sha256=_sha256_file(column_plan.octree_path),
        rgb_output_sha256=_sha256_file(column_plan.rgb_output_path),
        column_file_sha256=_sha256_file(column_path),
        oconv_command=oconv_command,
        rtrace_command=rtrace_command,
        radiance_installation=installation,
    )
    atomic_write_text(
        metadata_path,
        json.dumps(metadata.to_dict(), indent=2, sort_keys=True) + "\n",
    )
    return BasisColumnExecutionResult(
        column=column,
        metadata=metadata,
        oconv_result=oconv_result,
        rtrace_result=rtrace_result,
    )


def execute_basis_matrix(
    workspace: MaterializedBasisWorkspace,
    runner: LocalRunner,
    *,
    radiance_installation: RadianceInstallation | None = None,
    oconv_timeout_s: float | None = None,
    rtrace_timeout_s: float | None = None,
) -> BasisMatrixExecutionResult:
    """Execute every control-zone column in order and persist the complete basis."""

    installation = radiance_installation or discover_radiance_installation()
    sensor_count = workspace.manifest.sensor_count
    control_zone_count = workspace.manifest.control_zone_count
    results: list[BasisColumnExecutionResult] = []
    summaries: list[BasisMatrixColumnSummary] = []

    for column_index in range(control_zone_count):
        result = execute_basis_column(
            workspace,
            column_index,
            runner,
            radiance_installation=installation,
            oconv_timeout_s=oconv_timeout_s,
            rtrace_timeout_s=rtrace_timeout_s,
        )
        if result.column.ndim != 1 or result.column.shape[0] != sensor_count:
            raise BasisMatrixExecutionError(
                f"basis column {column_index} length mismatch: "
                f"expected {sensor_count}, got {result.column.shape}."
            )
        results.append(result)
        metadata = result.metadata
        summaries.append(
            BasisMatrixColumnSummary(
                control_zone_index=column_index,
                oconv_wall_time_s=metadata.oconv_wall_time_s,
                rtrace_wall_time_s=metadata.rtrace_wall_time_s,
                emitter_sha256=metadata.emitter_sha256,
                octree_sha256=metadata.octree_sha256,
                rgb_output_sha256=metadata.rgb_output_sha256,
                column_file_sha256=metadata.column_file_sha256,
                column_path=metadata.column_path,
            )
        )

    try:
        matrix = stack_basis_columns(
            [result.column for result in results],
            expected_sensor_count=sensor_count,
            expected_control_zone_count=control_zone_count,
        )
    except ValueError as exc:
        raise BasisMatrixExecutionError(
            f"complete basis matrix validation failed: {exc}"
        ) from exc

    output_directory = workspace.generation_plan.output_directory
    matrix_path = output_directory / "basis_matrix.npy"
    summary_path = output_directory / "basis_matrix.execution.json"
    save_basis_matrix(matrix_path, matrix, workspace.manifest)
    metadata = BasisMatrixExecutionMetadata(
        control_zone_count=control_zone_count,
        sensor_count=sensor_count,
        matrix_shape=tuple(matrix.shape),
        columns=tuple(summaries),
        total_wall_time_s=math.fsum(
            summary.total_wall_time_s for summary in summaries
        ),
        matrix_path=matrix_path,
        summary_path=summary_path,
        matrix_sha256=_sha256_file(matrix_path),
        radiance_installation=installation,
    )
    save_basis_execution_summary(summary_path, metadata.to_dict())
    return BasisMatrixExecutionResult(
        matrix=matrix,
        columns=tuple(results),
        metadata=metadata,
    )


def _validate_materialized_inputs(
    workspace: MaterializedBasisWorkspace, column_index: int
) -> None:
    if workspace.fixture_occlusion is None:
        raise BasisColumnExecutionError(
            "materialized basis workspace lacks fixture occlusion."
        )
    required = (
        workspace.room_path,
        workspace.sensor_path,
        workspace.emitter_paths[column_index],
        workspace.manifest_path,
        workspace.fixture_occlusion.instance_source_path,
    )
    for path in required:
        if not path.is_file():
            raise BasisColumnExecutionError(f"materialized basis input missing: {path}")
    if (
        workspace.source_variant_cal_path is not None
        and not workspace.source_variant_cal_path.is_file()
    ):
        raise BasisColumnExecutionError(
            "materialized angular CAL input missing: "
            f"{workspace.source_variant_cal_path}"
        )
    if workspace.source_variant_cal_path is not None:
        actual_cal_sha256 = _sha256_file(workspace.source_variant_cal_path)
        expected_cal_hashes = {
            column.emitter_document.metadata.angular_cal_sha256
            for column in workspace.generation_plan.columns
        }
        if expected_cal_hashes != {actual_cal_sha256}:
            raise BasisColumnExecutionError(
                "materialized angular CAL content disagrees with the active "
                "emitter modifier chain."
            )
    manifest = workspace.manifest
    expected_hashes = (
        (workspace.room_path, manifest.room_text_sha256),
        (workspace.sensor_path, manifest.sensor_text_sha256),
        (
            workspace.emitter_paths[column_index],
            manifest.emitter_text_sha256_by_control_zone[column_index],
        ),
    )
    for path, expected in expected_hashes:
        actual = _sha256_file(path)
        if expected is None or actual != expected:
            raise BasisColumnExecutionError(
                f"materialized basis input hash mismatch: {path}"
            )


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


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
