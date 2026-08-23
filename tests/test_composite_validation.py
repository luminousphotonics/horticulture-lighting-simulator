from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.geometry.room import RoomDimensions
from fspm_optics.geometry.sensor_grid import SensorGridSpec
from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.runner import RunnerResult
from fspm_optics.radiance.versioning import (
    RadianceExecutableVersion,
    RadianceInstallation,
)
from fspm_optics.transport.basis.artifacts import (
    save_basis_execution_summary,
    save_basis_matrix,
)
from fspm_optics.transport.basis.composite import (
    CompositeValidationError,
    compare_composite_ppfd,
    execute_composite_validation,
    plan_composite_validation,
)
from fspm_optics.transport.basis.solve import solve_basis_workspace
from fspm_optics.transport.basis.workspace import (
    materialize_basis_workspace,
    plan_basis_workspace,
)


class FakeCompositeRunner:
    def __init__(self, rgb_rows: str) -> None:
        self.rgb_rows = rgb_rows
        self.calls: list[CommandSpec] = []

    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult:
        del timeout_s, stderr_path
        self.calls.append(command)
        assert command.stdout_path is not None
        if command.stdout_path.suffix == ".oct":
            command.stdout_path.write_bytes(b"fake final octree")
        else:
            command.stdout_path.write_text(self.rgb_rows, encoding="utf-8")
        return RunnerResult(
            command_label=command.label,
            argv=command.argv,
            returncode=0,
            stdout_path=command.stdout_path,
            stderr_text="",
            stderr_path=None,
            wall_time_s=0.1 * len(self.calls),
            success=True,
        )


def fake_installation() -> RadianceInstallation:
    return RadianceInstallation(
        RadianceExecutableVersion("oconv", Path("/fake/oconv"), "fake"),
        RadianceExecutableVersion("rtrace", Path("/fake/rtrace"), "fake"),
    )


def solved_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "basis"
    layout = generate_proposed_led_layout(10, 10)
    room = RoomDimensions(layout.room_length_m, layout.room_width_m, 3.048)
    grid = SensorGridSpec(room, 3, 1, 0.005)
    materialized = materialize_basis_workspace(
        plan_basis_workspace(
            layout=layout,
            output_directory=root,
            sensor_grid=grid,
        )
    )
    matrix_path = save_basis_matrix(
        root / "basis_matrix.npy",
        np.ones(materialized.manifest.matrix_shape),
        materialized.manifest,
    )
    save_basis_execution_summary(
        root / "basis_matrix.execution.json",
        {
            "schema_version": 1,
            "sensor_count": materialized.manifest.sensor_count,
            "control_zone_count": materialized.manifest.control_zone_count,
            "matrix_shape": list(materialized.manifest.matrix_shape),
            "per_column": [
                {"control_zone_index": index}
                for index in range(materialized.manifest.control_zone_count)
            ],
            "hashes": {
                "matrix_sha256": hashlib.sha256(matrix_path.read_bytes()).hexdigest()
            },
        },
    )
    solve_basis_workspace(root, target_ppfd=10.0)
    return root


def test_plan_materializes_all_scheduled_modules_and_deterministic_commands(
    tmp_path: Path,
) -> None:
    plan = plan_composite_validation(solved_workspace(tmp_path))

    emitter_text = plan.paths.emitter_path.read_text(encoding="utf-8")
    assert emitter_text.count("# module ") == 61
    assert plan.schedule.module_count == 61
    assert plan.oconv_command.argv == (
        "oconv",
        "-f",
        str(plan.paths.room_path),
        str(plan.paths.emitter_path),
    )
    assert plan.oconv_command.stdout_path == plan.paths.octree_path
    assert plan.rtrace_command.argv[0] == "rtrace"
    assert "-h" in plan.rtrace_command.argv
    assert "-I+" in plan.rtrace_command.argv
    assert "-n" in plan.rtrace_command.argv
    assert plan.rtrace_command.stdin_path == plan.paths.sensor_path
    assert plan.rtrace_command.stdout_path == plan.paths.rgb_output_path
    assert plan.paths.ambient_cache_path is not None
    assert str(plan.paths.ambient_cache_path) in plan.rtrace_command.argv


def test_execute_runs_compile_then_trace_and_writes_final_artifacts(
    tmp_path: Path,
) -> None:
    plan = plan_composite_validation(solved_workspace(tmp_path))
    runner = FakeCompositeRunner("9 9 9\n10 10 10\n11 11 11\n")
    result = execute_composite_validation(
        plan,
        runner,  # type: ignore[arg-type]
        radiance_installation=fake_installation(),
    )

    assert [call.label for call in runner.calls] == [
        "compile_final_composite",
        "trace_final_composite",
    ]
    assert runner.calls[0].argv[0] == "/fake/oconv"
    assert runner.calls[1].argv[0] == "/fake/rtrace"
    np.testing.assert_array_equal(result.actual_ppfd, [9.0, 10.0, 11.0])
    assert result.comparison.actual.mean == pytest.approx(10.0)
    assert result.comparison.rmse_actual_vs_predicted == pytest.approx(
        math.sqrt(2.0 / 3.0)
    )
    for path in (
        plan.paths.actual_ppfd_path,
        plan.paths.metrics_path,
        plan.paths.comparison_path,
        plan.paths.execution_path,
    ):
        assert path.is_file()
    metrics = json.loads(plan.paths.metrics_path.read_text(encoding="utf-8"))
    comparison = json.loads(
        plan.paths.comparison_path.read_text(encoding="utf-8")
    )
    execution = json.loads(plan.paths.execution_path.read_text(encoding="utf-8"))
    assert metrics["min"] == 9.0
    assert metrics["max"] == 11.0
    assert comparison["threshold_policy"].startswith("report_only")
    assert execution["radiance_installation"]["rtrace"]["path"] == "/fake/rtrace"


def test_final_ppfd_parsing_validates_sensor_row_count(tmp_path: Path) -> None:
    plan = plan_composite_validation(solved_workspace(tmp_path))
    runner = FakeCompositeRunner("9 9 9\n10 10 10\n")

    with pytest.raises(
        CompositeValidationError,
        match=r"expected 3, got 2",
    ):
        execute_composite_validation(
            plan,
            runner,  # type: ignore[arg-type]
            radiance_installation=fake_installation(),
        )
    assert len(runner.calls) == 2
    assert not plan.paths.actual_ppfd_path.exists()


def test_comparison_metrics_are_correct() -> None:
    metrics = compare_composite_ppfd(
        [1.0, 2.0, 3.0],
        [1.0, 1.0, 5.0],
        target_ppfd=2.0,
    )

    assert metrics.actual.mean == pytest.approx(2.0)
    assert metrics.actual.minimum == 1.0
    assert metrics.actual.maximum == 3.0
    assert metrics.actual.standard_deviation == pytest.approx(math.sqrt(2.0 / 3.0))
    assert metrics.actual.cv_percent == pytest.approx(100.0 * math.sqrt(2.0 / 3.0) / 2.0)
    assert metrics.rmse_actual_vs_predicted == pytest.approx(math.sqrt(5.0 / 3.0))
    assert metrics.mae_actual_vs_predicted == pytest.approx(1.0)
    assert metrics.max_absolute_error == 2.0
    assert metrics.relative_rmse_percent_of_target == pytest.approx(
        50.0 * math.sqrt(5.0 / 3.0)
    )


def test_comparison_rejects_shape_mismatch() -> None:
    with pytest.raises(ValueError, match="shapes must match"):
        compare_composite_ppfd([1.0, 2.0], [1.0], target_ppfd=1.0)


@pytest.mark.parametrize(
    ("actual", "predicted"),
    [
        ([1.0, float("nan")], [1.0, 2.0]),
        ([1.0, 2.0], [1.0, float("inf")]),
    ],
)
def test_comparison_rejects_non_finite_values(
    actual: list[float],
    predicted: list[float],
) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        compare_composite_ppfd(actual, predicted, target_ppfd=1.0)


def test_plan_rejects_invalid_predicted_ppfd_shape_and_values(tmp_path: Path) -> None:
    shape_root = solved_workspace(tmp_path / "shape")
    with (shape_root / "predicted_ppfd.npy").open("wb") as handle:
        np.save(handle, np.ones(2), allow_pickle=False)
    with pytest.raises(CompositeValidationError, match="shape mismatch"):
        plan_composite_validation(shape_root)

    finite_root = solved_workspace(tmp_path / "finite")
    with (finite_root / "predicted_ppfd.npy").open("wb") as handle:
        np.save(handle, np.array([1.0, np.nan, 3.0]), allow_pickle=False)
    with pytest.raises(ValueError, match="non-finite"):
        plan_composite_validation(finite_root)
