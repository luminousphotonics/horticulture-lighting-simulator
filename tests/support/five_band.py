from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

import numpy as np

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
from fspm_optics.transport.basis.solve import solve_basis_workspace
from fspm_optics.transport.basis.workspace import (
    materialize_basis_workspace,
    plan_basis_workspace,
)
from fspm_optics.transport.five_band import FIVE_BAND_ORDER


class RecordingFiveBandRunner:
    def __init__(
        self,
        *,
        fail_band: str | None = None,
        fail_stage: str | None = None,
        malformed_band: str | None = None,
        before_first_run: Callable[[], None] | None = None,
    ) -> None:
        self.fail_band = fail_band
        self.fail_stage = fail_stage
        self.malformed_band = malformed_band
        self.before_first_run = before_first_run
        self.calls: list[CommandSpec] = []

    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult:
        del timeout_s, stderr_path
        if not self.calls and self.before_first_run is not None:
            self.before_first_run()
        self.calls.append(command)
        band_id = next(
            item
            for item in sorted(FIVE_BAND_ORDER, key=len, reverse=True)
            if f"_{item}_" in command.label
        )
        stage = "oconv" if command.label.startswith("compile_") else "rtrace"
        failed = band_id == self.fail_band and stage == self.fail_stage
        if not failed:
            assert command.stdout_path is not None
            if stage == "oconv":
                assert command.stdout_mode == "binary"
                command.stdout_path.write_bytes(b"\x00fake-octree\xff" + band_id.encode())
            else:
                assert command.stdout_mode == "text"
                assert command.stdin_path is not None
                receiver_count = len(
                    command.stdin_path.read_text(encoding="utf-8").splitlines()
                )
                if band_id == self.malformed_band:
                    rows = "1 1 1\n" * (receiver_count - 1)
                else:
                    band_offset = FIVE_BAND_ORDER.index(band_id)
                    rows = "".join(
                        f"{10 + band_offset} {10 + band_offset} {10 + band_offset}\n"
                        if index % 2 == 0
                        else f"{2 + band_offset} {2 + band_offset} {2 + band_offset}\n"
                        for index in range(receiver_count)
                    )
                command.stdout_path.write_text(rows, encoding="utf-8")
        return RunnerResult(
            command_label=command.label,
            argv=command.argv,
            returncode=9 if failed else 0,
            stdout_path=command.stdout_path,
            stderr_text="intentional failure" if failed else "",
            stderr_path=None,
            wall_time_s=0.01 * len(self.calls),
            success=not failed,
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
