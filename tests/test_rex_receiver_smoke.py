from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.geometry.room import RoomDimensions
from fspm_optics.geometry.sensor_grid import SensorGridSpec
from fspm_optics.plants.generator import generate_rex_butterhead_plant
from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.materials import (
    REX_LEAF_SCALAR_TRANSPORT_PLACEHOLDER,
)
from fspm_optics.radiance.runner import RunnerResult
from fspm_optics.radiance.versioning import (
    RadianceExecutableVersion,
    RadianceInstallation,
)
from fspm_optics.receivers.samples import build_two_sided_patch_receivers
from fspm_optics.transport.basis.artifacts import (
    save_basis_execution_summary,
    save_basis_matrix,
)
from fspm_optics.transport.basis.solve import solve_basis_workspace
from fspm_optics.transport.basis.workspace import (
    materialize_basis_workspace,
    plan_basis_workspace,
)
from fspm_optics.transport.plant_receiver import (
    RexReceiverSmokeError,
    compute_rex_receiver_metrics,
    execute_rex_receiver_smoke,
    plan_rex_receiver_smoke,
)


class FakeRexReceiverRunner:
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
            command.stdout_path.write_bytes(b"fake Rex receiver octree")
        else:
            command.stdout_path.write_text(self.rgb_rows, encoding="utf-8")
        return RunnerResult(
            command_label=command.label,
            argv=command.argv,
            returncode=0,
            stdout_path=command.stdout_path,
            stderr_text="",
            stderr_path=None,
            wall_time_s=0.05 * len(self.calls),
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


def test_dry_plan_materializes_deterministic_rex_inputs_and_commands(
    tmp_path: Path,
) -> None:
    root = solved_workspace(tmp_path)
    plan = plan_rex_receiver_smoke(root, seed=1)
    first_plant_text = plan.paths.plant_path.read_text(encoding="utf-8")
    first_receiver_text = plan.paths.receiver_path.read_text(encoding="utf-8")
    repeated = plan_rex_receiver_smoke(root, seed=1)

    assert len(plan.plant.leaves) == 32
    assert plan.plant.patch_count == 512
    assert plan.receiver_count == 1024
    assert len(first_receiver_text.splitlines()) == 1024
    assert first_plant_text.count(" polygon ") == plan.plant.face_count
    assert repeated.paths == plan.paths
    assert repeated.oconv_command == plan.oconv_command
    assert repeated.rtrace_command == plan.rtrace_command
    assert repeated.paths.plant_path.read_text(encoding="utf-8") == first_plant_text
    assert repeated.paths.receiver_path.read_text(encoding="utf-8") == first_receiver_text
    assert plan.oconv_command.argv == (
        "oconv",
        "-f",
        str(root / "room.rad"),
        str(root / "final_composite_emitters.rad"),
        str(root / "rex_plant.rad"),
    )
    assert plan.oconv_command.stdout_path == root / "plant_receiver.oct"
    assert plan.rtrace_command.stdin_path == root / "rex_plant_receivers.pts"
    assert plan.rtrace_command.stdout_path == root / "plant_receiver.rgb"
    assert plan.rtrace_command.argv[0] == "rtrace"
    assert "-I+" in plan.rtrace_command.argv
    assert "-h" in plan.rtrace_command.argv


def test_execute_runs_oconv_then_rtrace_and_writes_flux_metrics(
    tmp_path: Path,
) -> None:
    plan = plan_rex_receiver_smoke(solved_workspace(tmp_path))
    rows = "10 10 10\n2 2 2\n" * plan.plant.patch_count
    runner = FakeRexReceiverRunner(rows)

    result = execute_rex_receiver_smoke(
        plan,
        runner,  # type: ignore[arg-type]
        radiance_installation=fake_installation(),
    )

    assert [call.label for call in runner.calls] == [
        "compile_rex_plant_receiver",
        "plant_receiver_rtrace",
    ]
    assert runner.calls[0].argv[0] == "/fake/oconv"
    assert runner.calls[1].argv[0] == "/fake/rtrace"
    assert result.flux.shape == (1024,)
    assert result.metrics.receiver_count == 1024
    assert result.metrics.front_receiver_count == 512
    assert result.metrics.back_receiver_count == 512
    assert result.metrics.mean_incident_ppfd == pytest.approx(6.0)
    assert result.metrics.front_mean_incident_ppfd == pytest.approx(10.0)
    assert result.metrics.back_mean_incident_ppfd == pytest.approx(2.0)
    assert result.metrics.front_back_ratio == pytest.approx(5.0)
    assert result.metrics.area_weighted_mean_incident_ppfd == pytest.approx(6.0)
    for path in (
        plan.paths.flux_path,
        plan.paths.metrics_path,
        plan.paths.execution_path,
    ):
        assert path.is_file()
    metrics = json.loads(plan.paths.metrics_path.read_text(encoding="utf-8"))
    execution = json.loads(plan.paths.execution_path.read_text(encoding="utf-8"))
    assert metrics["leaf_optics_policy"].endswith("not_rex_rta")
    assert execution["radiance_installation"]["rtrace"]["path"] == "/fake/rtrace"


def test_receiver_row_count_mismatch_fails_clearly(tmp_path: Path) -> None:
    plan = plan_rex_receiver_smoke(solved_workspace(tmp_path))
    runner = FakeRexReceiverRunner("1 1 1\n" * (plan.receiver_count - 1))

    with pytest.raises(
        RexReceiverSmokeError,
        match=r"expected 1024, got 1023",
    ):
        execute_rex_receiver_smoke(
            plan,
            runner,  # type: ignore[arg-type]
            radiance_installation=fake_installation(),
        )

    assert len(runner.calls) == 2
    assert not plan.paths.flux_path.exists()


def test_metrics_separate_front_and_back_groups() -> None:
    plant = generate_rex_butterhead_plant()
    samples = build_two_sided_patch_receivers(plant)
    values = np.tile([8.0, 4.0], plant.patch_count)

    metrics = compute_rex_receiver_metrics(
        values,
        samples,
        leaf_count=len(plant.leaves),
        patch_count=plant.patch_count,
    )

    assert metrics.front_mean_incident_ppfd == 8.0
    assert metrics.back_mean_incident_ppfd == 4.0
    assert metrics.front_back_ratio == 2.0
    assert metrics.minimum_incident_ppfd == 4.0
    assert metrics.maximum_incident_ppfd == 8.0


def test_scalar_leaf_placeholder_has_equal_rgb_and_clear_scope() -> None:
    material = REX_LEAF_SCALAR_TRANSPORT_PLACEHOLDER

    assert material.red_reflectance == material.green_reflectance
    assert material.green_reflectance == material.blue_reflectance
    assert "placeholder" in material.name
    assert "not a measured or modeled Rex" in material.notes
