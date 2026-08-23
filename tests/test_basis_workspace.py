from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.geometry.room import (
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    RoomDimensions,
)
from fspm_optics.geometry.sensor_grid import (
    AdaptiveSensorGridPolicy,
    SensorGridSpec,
)
from fspm_optics.transport.basis.workspace import (
    BasisWorkspaceConflictError,
    materialize_basis_workspace,
    plan_basis_workspace,
)
from fspm_optics.transport.basis import workspace as workspace_module
from fspm_optics.radiance.materials import (
    PRODUCTION_ROOM_FLOOR,
    PRODUCTION_ROOM_WALL_CEILING,
)


def workspace_plan(
    root: Path,
    *,
    reference_watts: float = 1.0,
    radiance_options: tuple[str, ...] | None = None,
    adaptive_policy: AdaptiveSensorGridPolicy = AdaptiveSensorGridPolicy(),
    proposed_layout_mode: str = "linear",
):
    return plan_basis_workspace(
        layout=generate_proposed_led_layout(
            10,
            10,
            proposed_layout_mode=proposed_layout_mode,
        ),
        output_directory=root,
        reference_watts=reference_watts,
        radiance_options=radiance_options,
        adaptive_policy=adaptive_policy,
    )


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ten_by_ten_workspace_materializes_five_isolated_emitters(tmp_path: Path) -> None:
    workspace = workspace_plan(tmp_path / "basis")
    materialized = materialize_basis_workspace(workspace)

    assert len(materialized.emitter_paths) == 5
    assert all(path.is_file() for path in materialized.emitter_paths)
    assert materialized.room_path.is_file()
    assert materialized.sensor_path.is_file()
    assert materialized.manifest_path.is_file()
    assert workspace.sensor_grid_spec.point_count == 441
    assert all(not path.exists() for path in materialized.octree_paths)
    assert all(not path.exists() for path in materialized.rtrace_output_paths)


def test_materialized_basis_room_uses_production_room_authority(tmp_path: Path) -> None:
    workspace = workspace_plan(tmp_path / "basis")
    materialized = materialize_basis_workspace(workspace)
    room_text = materialized.room_path.read_text(encoding="utf-8")
    assert PRODUCTION_ROOM_WALL_CEILING.to_radiance() in room_text
    assert PRODUCTION_ROOM_FLOOR.to_radiance() in room_text
    assert room_text.count(f"{PRODUCTION_ROOM_FLOOR.name} polygon ") == 1
    assert room_text.count(f"{PRODUCTION_ROOM_WALL_CEILING.name} polygon ") == 5
    assert (
        materialized.manifest.room_model_identity_sha256
        == PRODUCTION_ROOM_MODEL_IDENTITY_SHA256
    )


def test_each_materialized_emitter_activates_one_control_zone(tmp_path: Path) -> None:
    workspace = workspace_plan(tmp_path / "basis")
    materialized = materialize_basis_workspace(workspace)
    for column, path in zip(
        workspace.generation_plan.columns,
        materialized.emitter_paths,
        strict=True,
    ):
        assert path.read_text(encoding="utf-8") == column.emitter_document.radiance_text
        coefficients = column.emitter_document.metadata.watts_by_control_zone
        assert sum(value > 0.0 for value in coefficients) == 1
        assert coefficients[column.control_zone_index] == 1.0


def test_workspace_hashes_are_stable_and_change_with_scientific_inputs(
    tmp_path: Path,
) -> None:
    first = workspace_plan(tmp_path / "one")
    relocated = workspace_plan(tmp_path / "two")
    higher_power = workspace_plan(tmp_path / "three", reference_watts=2.0)
    different_options = workspace_plan(
        tmp_path / "four", radiance_options=("-ab", "5", "-aa", "0.12")
    )
    finer_grid = workspace_plan(
        tmp_path / "five",
        adaptive_policy=AdaptiveSensorGridPolicy(
            target_spacing_m=0.1,
            min_floor_points_x=21,
            min_floor_points_y=21,
        ),
    )

    assert first.manifest.room_text_sha256 == relocated.manifest.room_text_sha256
    assert first.manifest.sensor_text_sha256 == relocated.manifest.sensor_text_sha256
    assert (
        first.manifest.emitter_text_sha256_by_control_zone
        == relocated.manifest.emitter_text_sha256_by_control_zone
    )
    assert first.manifest.command_policy_sha256 == relocated.manifest.command_policy_sha256
    assert (
        first.manifest.emitter_text_sha256_by_control_zone
        != higher_power.manifest.emitter_text_sha256_by_control_zone
    )
    assert first.manifest.command_policy_sha256 == higher_power.manifest.command_policy_sha256
    assert first.manifest.command_policy_sha256 != different_options.manifest.command_policy_sha256
    assert first.manifest.sensor_text_sha256 != finer_grid.manifest.sensor_text_sha256


def test_basis_workspace_identity_authenticates_mode_without_changing_sources(
    tmp_path: Path,
) -> None:
    linear = workspace_plan(tmp_path / "linear")
    repeated = workspace_plan(tmp_path / "linear-repeated")
    legacy = workspace_plan(
        tmp_path / "legacy",
        proposed_layout_mode="legacy",
    )

    assert linear.manifest == repeated.manifest
    assert linear.manifest.proposed_layout_mode.value == "linear"
    assert legacy.manifest.proposed_layout_mode.value == "legacy"
    assert linear.manifest.fixture_policy_id != legacy.manifest.fixture_policy_id
    assert (
        linear.manifest.emitter_text_sha256_by_control_zone
        == legacy.manifest.emitter_text_sha256_by_control_zone
    )
    assert (
        linear.manifest.emitter_source_sha256
        == legacy.manifest.emitter_source_sha256
    )
    assert (
        linear.manifest.command_policy_sha256
        != legacy.manifest.command_policy_sha256
    )
    assert [
        item.ambient_cache_path.name
        for item in linear.generation_plan.columns
        if item.ambient_cache_path is not None
    ] != [
        item.ambient_cache_path.name
        for item in legacy.generation_plan.columns
        if item.ambient_cache_path is not None
    ]
    assert linear.manifest.to_dict() != legacy.manifest.to_dict()


def test_manifest_hashes_match_every_materialized_input(tmp_path: Path) -> None:
    workspace = workspace_plan(tmp_path / "basis")
    materialized = materialize_basis_workspace(workspace)
    manifest = materialized.manifest

    assert manifest.room_text_sha256 == file_sha256(materialized.room_path)
    assert manifest.sensor_text_sha256 == file_sha256(materialized.sensor_path)
    assert manifest.emitter_text_sha256_by_control_zone == tuple(
        file_sha256(path) for path in materialized.emitter_paths
    )


def test_fixed_resolution_workspace_remains_supported(tmp_path: Path) -> None:
    layout = generate_proposed_led_layout(10, 10)
    room = RoomDimensions(layout.room_length_m, layout.room_width_m, 3.048)
    fixed = SensorGridSpec(room, 4, 3, 0.005)
    workspace = plan_basis_workspace(
        layout=layout,
        output_directory=tmp_path / "fixed",
        sensor_grid=fixed,
    )
    assert workspace.adaptive_sensor_grid is None
    assert workspace.sensor_grid_spec.point_count == 12
    assert workspace.manifest.sensor_count == 12


def test_incompatible_workspace_is_rejected_before_new_files_are_written(
    tmp_path: Path,
) -> None:
    root = tmp_path / "basis"
    root.mkdir()
    (root / "room.rad").write_text("incompatible\n", encoding="utf-8")
    workspace = workspace_plan(root)

    with pytest.raises(BasisWorkspaceConflictError, match="incompatible"):
        materialize_basis_workspace(workspace)
    assert not (root / "sensors.pts").exists()
    assert not tuple(root.glob("basis_control_zone_*.rad"))
    assert not (root / "basis_manifest.json").exists()


def test_compatible_workspace_materialization_is_idempotent(tmp_path: Path) -> None:
    workspace = workspace_plan(tmp_path / "basis")
    first = materialize_basis_workspace(workspace)
    second = materialize_basis_workspace(workspace)
    assert first == second


def test_workspace_transaction_rolls_back_partial_commits_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "basis"
    workspace = workspace_plan(root)
    real_commit = workspace_module.commit_staged
    calls = 0

    def fail_second_commit(temporary_path: Path, final_path: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated workspace replacement failure")
        return real_commit(temporary_path, final_path)

    monkeypatch.setattr(workspace_module, "commit_staged", fail_second_commit)
    with pytest.raises(OSError, match="simulated"):
        materialize_basis_workspace(workspace)
    assert root.is_dir()
    assert not tuple(root.iterdir())
