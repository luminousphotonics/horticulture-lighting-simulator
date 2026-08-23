from __future__ import annotations

import json
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
from fspm_optics.transport.basis.execution import (
    BasisColumnExecutionError,
    execute_basis_column,
    execute_basis_matrix,
)
from fspm_optics.transport.basis.workspace import (
    materialize_basis_workspace,
    plan_basis_workspace,
)


class FakeBasisRunner:
    def __init__(
        self,
        rgb_rows: str,
        *,
        rgb_rows_by_zone: dict[int, str] | None = None,
    ) -> None:
        self.rgb_rows = rgb_rows
        self.rgb_rows_by_zone = rgb_rows_by_zone or {}
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
            command.stdout_path.write_bytes(b"fake octree")
        else:
            zone = int(command.stdout_path.stem.rsplit("_", maxsplit=1)[1])
            command.stdout_path.write_text(
                self.rgb_rows_by_zone.get(zone, self.rgb_rows),
                encoding="utf-8",
            )
        return RunnerResult(
            command_label=command.label,
            argv=command.argv,
            returncode=0,
            stdout_path=command.stdout_path,
            stderr_text="",
            stderr_path=None,
            wall_time_s=(
                0.125 if command.label.startswith("compile_") else 0.25
            ),
            success=True,
        )


def fake_installation() -> RadianceInstallation:
    return RadianceInstallation(
        oconv=RadianceExecutableVersion(
            "oconv", Path("/fake/bin/oconv"), "fake oconv version"
        ),
        rtrace=RadianceExecutableVersion(
            "rtrace", Path("/fake/bin/rtrace"), "fake rtrace version"
        ),
    )


def small_materialized_workspace(tmp_path: Path):
    layout = generate_proposed_led_layout(10, 10)
    room = RoomDimensions(layout.room_length_m, layout.room_width_m, 3.048)
    fixed_grid = SensorGridSpec(room, 3, 1, 0.005)
    plan = plan_basis_workspace(
        layout=layout,
        output_directory=tmp_path / "basis",
        sensor_grid=fixed_grid,
    )
    return materialize_basis_workspace(plan)


def test_execute_basis_column_runs_compile_then_trace_and_saves_column(
    tmp_path: Path,
) -> None:
    workspace = small_materialized_workspace(tmp_path)
    runner = FakeBasisRunner("1 1 1\n2 2 2\n3 3 3\n")
    result = execute_basis_column(
        workspace,
        0,
        runner,  # type: ignore[arg-type]
        radiance_installation=fake_installation(),
    )

    shape_count = len(workspace.fixture_occlusion.shapes)
    assert len(runner.calls) == shape_count + 2
    assert all(
        call.label.startswith("compile_fixture-body-shape-")
        for call in runner.calls[:shape_count]
    )
    assert runner.calls[-2].argv[0] == "/fake/bin/oconv"
    assert runner.calls[-1].argv[0] == "/fake/bin/rtrace"
    np.testing.assert_array_equal(result.column, [1.0, 2.0, 3.0])
    np.testing.assert_array_equal(
        np.load(result.metadata.column_path, allow_pickle=False), result.column
    )
    assert result.metadata.column_shape == (3,)
    assert result.metadata.oconv_wall_time_s == pytest.approx(0.125)
    assert result.metadata.rtrace_wall_time_s == pytest.approx(0.25)
    assert result.metadata.radiance_installation.oconv.version_text == (
        "fake oconv version"
    )
    payload = json.loads(result.metadata.metadata_path.read_text(encoding="utf-8"))
    assert payload["radiance_installation"]["rtrace"]["version_text"] == (
        "fake rtrace version"
    )
    assert payload["hashes"]["column_file_sha256"]


def test_execute_basis_column_rejects_wrong_scalar_row_count(tmp_path: Path) -> None:
    workspace = small_materialized_workspace(tmp_path)
    runner = FakeBasisRunner("1 1 1\n2 2 2\n")
    with pytest.raises(BasisColumnExecutionError, match="expected 3, got 2"):
        execute_basis_column(
            workspace,
            0,
            runner,  # type: ignore[arg-type]
            radiance_installation=fake_installation(),
        )
    assert len(runner.calls) == len(workspace.fixture_occlusion.shapes) + 2
    assert not (workspace.generation_plan.output_directory / "basis_control_zone_000.npy").exists()


def test_execute_basis_column_rejects_tampered_materialized_input(
    tmp_path: Path,
) -> None:
    workspace = small_materialized_workspace(tmp_path)
    workspace.emitter_paths[0].write_text("tampered\n", encoding="utf-8")
    runner = FakeBasisRunner("1 1 1\n2 2 2\n3 3 3\n")
    with pytest.raises(BasisColumnExecutionError, match="hash mismatch"):
        execute_basis_column(
            workspace,
            0,
            runner,  # type: ignore[arg-type]
            radiance_installation=fake_installation(),
        )
    assert runner.calls == []


def test_execute_basis_column_validates_zone_index_before_running(tmp_path: Path) -> None:
    workspace = small_materialized_workspace(tmp_path)
    runner = FakeBasisRunner("")
    with pytest.raises(ValueError, match="column_index"):
        execute_basis_column(
            workspace,
            5,
            runner,  # type: ignore[arg-type]
            radiance_installation=fake_installation(),
        )
    assert runner.calls == []


def test_execute_basis_matrix_runs_columns_in_order_and_saves_artifacts(
    tmp_path: Path,
) -> None:
    workspace = small_materialized_workspace(tmp_path)
    rows_by_zone = {
        zone: f"{zone + 1} {zone + 1} {zone + 1}\n" * 3
        for zone in range(workspace.manifest.control_zone_count)
    }
    runner = FakeBasisRunner("", rgb_rows_by_zone=rows_by_zone)
    result = execute_basis_matrix(
        workspace,
        runner,  # type: ignore[arg-type]
        radiance_installation=fake_installation(),
    )

    shape_labels = [
        shape.compile_command.label
        for shape in workspace.fixture_occlusion.shapes
    ]
    assert [call.label for call in runner.calls] == shape_labels + [
        label
        for zone in range(5)
        for label in (
            f"compile_basis_control_zone_{zone:03d}",
            f"trace_basis_control_zone_{zone:03d}",
        )
    ]
    assert result.matrix.shape == (3, 5)
    np.testing.assert_array_equal(
        result.matrix,
        np.tile(np.arange(1.0, 6.0), (3, 1)),
    )
    np.testing.assert_array_equal(
        np.load(result.metadata.matrix_path, allow_pickle=False),
        result.matrix,
    )
    assert result.metadata.control_zone_count == 5
    assert result.metadata.sensor_count == 3
    assert result.metadata.matrix_shape == (3, 5)
    assert result.metadata.total_wall_time_s > 0.0
    assert [column.control_zone_index for column in result.metadata.columns] == list(
        range(5)
    )
    assert all(column.column_file_sha256 for column in result.metadata.columns)
    payload = json.loads(result.metadata.summary_path.read_text(encoding="utf-8"))
    assert payload["matrix_shape"] == [3, 5]
    assert len(payload["per_column"]) == 5
    assert payload["hashes"]["matrix_sha256"] == result.metadata.matrix_sha256
    assert payload["radiance_installation"]["oconv"]["path"] == "/fake/bin/oconv"


def test_execute_basis_matrix_rejects_mismatched_column_length(
    tmp_path: Path,
) -> None:
    workspace = small_materialized_workspace(tmp_path)
    runner = FakeBasisRunner(
        "1 1 1\n2 2 2\n3 3 3\n",
        rgb_rows_by_zone={2: "1 1 1\n2 2 2\n"},
    )

    with pytest.raises(
        BasisColumnExecutionError,
        match=r"basis column 2.*expected 3, got 2",
    ):
        execute_basis_matrix(
            workspace,
            runner,  # type: ignore[arg-type]
            radiance_installation=fake_installation(),
        )

    assert len(runner.calls) == len(workspace.fixture_occlusion.shapes) + 6
    output_directory = workspace.generation_plan.output_directory
    assert not (output_directory / "basis_matrix.npy").exists()
    assert not (output_directory / "basis_matrix.execution.json").exists()
