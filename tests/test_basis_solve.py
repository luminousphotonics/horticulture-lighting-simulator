from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.geometry.room import RoomDimensions
from fspm_optics.geometry.sensor_grid import SensorGridSpec
from fspm_optics.transport.basis.artifacts import (
    load_basis_workspace_artifacts,
    save_basis_execution_summary,
    save_basis_matrix,
)
from fspm_optics.transport.basis.solve import BasisSolveError, solve_basis_workspace
from fspm_optics.transport.basis.workspace import (
    materialize_basis_workspace,
    plan_basis_workspace,
)


def fake_basis_workspace(
    tmp_path: Path,
    *,
    reference_watts: float = 1.0,
    matrix: np.ndarray | None = None,
) -> Path:
    root = tmp_path / "basis"
    layout = generate_proposed_led_layout(10, 10)
    room = RoomDimensions(layout.room_length_m, layout.room_width_m, 3.048)
    grid = SensorGridSpec(room, 3, 1, 0.005)
    materialized = materialize_basis_workspace(
        plan_basis_workspace(
            layout=layout,
            output_directory=root,
            sensor_grid=grid,
            reference_watts=reference_watts,
        )
    )
    values = (
        np.full(materialized.manifest.matrix_shape, reference_watts, dtype=float)
        if matrix is None
        else np.asarray(matrix, dtype=float)
    )
    matrix_path = save_basis_matrix(
        root / "basis_matrix.npy",
        values,
        materialized.manifest,
    )
    matrix_hash = hashlib.sha256(matrix_path.read_bytes()).hexdigest()
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
            "hashes": {"matrix_sha256": matrix_hash},
        },
    )
    return root


def test_loads_basis_workspace_and_validates_matrix_shape(tmp_path: Path) -> None:
    root = fake_basis_workspace(tmp_path, reference_watts=2.0)
    loaded = load_basis_workspace_artifacts(root)

    assert loaded.matrix.shape == (3, 5)
    assert loaded.manifest.matrix_shape == (3, 5)
    assert loaded.reference_watts_per_module == 2.0
    assert loaded.reference_watts_source == "basis_manifest"


def test_solves_feasible_basis_with_exact_mean_and_reference_normalization(
    tmp_path: Path,
) -> None:
    root = fake_basis_workspace(tmp_path, reference_watts=2.0)
    result = solve_basis_workspace(
        root,
        target_ppfd=10.0,
        min_watts=0.0,
        max_watts=100.0,
    )

    assert result.uniformity.success
    assert result.uniformity.mean == pytest.approx(10.0, abs=1e-9)
    assert sum(result.uniformity.coefficients) == pytest.approx(10.0, abs=1e-8)
    assert result.schedule.watts_by_control_zone == pytest.approx(
        result.uniformity.coefficients
    )
    assert result.schedule.schedule_source == "optimized_explicit_control_zones"
    assert result.schedule.control_zone_count == 5


def test_rejects_infeasible_target_without_writing_solution(tmp_path: Path) -> None:
    root = fake_basis_workspace(tmp_path)
    with pytest.raises(BasisSolveError, match="infeasible"):
        solve_basis_workspace(
            root,
            target_ppfd=10.0,
            min_watts=0.0,
            max_watts=1.0,
        )

    assert not (root / "basis_solution.json").exists()
    assert not (root / "optimized_module_schedule.json").exists()
    assert not (root / "predicted_ppfd.npy").exists()
    assert not (root / "predicted_ppfd_metrics.json").exists()


def test_rejects_missing_and_mismatched_execution_metadata(tmp_path: Path) -> None:
    missing_root = fake_basis_workspace(tmp_path / "missing")
    (missing_root / "basis_matrix.execution.json").unlink()
    with pytest.raises(FileNotFoundError, match="execution summary not found"):
        load_basis_workspace_artifacts(missing_root)

    mismatch_root = fake_basis_workspace(tmp_path / "mismatch")
    summary_path = mismatch_root / "basis_matrix.execution.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["sensor_count"] = 99
    summary_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="sensor_count mismatch"):
        load_basis_workspace_artifacts(mismatch_root)


def test_rejects_matrix_hash_mismatch(tmp_path: Path) -> None:
    root = fake_basis_workspace(tmp_path)
    matrix_path = root / "basis_matrix.npy"
    with matrix_path.open("wb") as handle:
        np.save(handle, np.full((3, 5), 2.0), allow_pickle=False)

    with pytest.raises(ValueError, match="hash does not match"):
        load_basis_workspace_artifacts(root)


def test_writes_solution_schedule_prediction_and_metrics_artifacts(
    tmp_path: Path,
) -> None:
    root = fake_basis_workspace(tmp_path)
    result = solve_basis_workspace(root, target_ppfd=5.0)

    for path in (
        result.artifact_paths.solution_path,
        result.artifact_paths.schedule_path,
        result.artifact_paths.predicted_ppfd_path,
        result.artifact_paths.metrics_path,
    ):
        assert path.is_file()
    solution = json.loads(
        result.artifact_paths.solution_path.read_text(encoding="utf-8")
    )
    schedule = json.loads(
        result.artifact_paths.schedule_path.read_text(encoding="utf-8")
    )
    metrics = json.loads(
        result.artifact_paths.metrics_path.read_text(encoding="utf-8")
    )
    predicted = np.load(result.artifact_paths.predicted_ppfd_path, allow_pickle=False)

    assert solution["coefficient_units"] == "watts_per_module_by_control_zone"
    assert len(solution["watts_by_control_zone"]) == 5
    assert solution["basis_column_normalization"] == (
        "divide_by_reference_watts_per_module"
    )
    assert schedule["schedule_source"] == "optimized_explicit_control_zones"
    assert len(schedule["control_zones"]) == 5
    assert [zone["control_zone_index"] for zone in schedule["control_zones"]] == list(
        range(5)
    )
    assert len(schedule["modules"]) == 61
    assert metrics["mean_ppfd"] == pytest.approx(5.0)
    np.testing.assert_allclose(predicted, 5.0)


def test_missing_manifest_reference_uses_explicit_or_default_fallback(
    tmp_path: Path,
) -> None:
    explicit_root = fake_basis_workspace(tmp_path / "explicit")
    manifest_path = explicit_root / "basis_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    del payload["reference_watts"]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    explicit = load_basis_workspace_artifacts(
        explicit_root,
        reference_watts_per_module=2.0,
    )
    assert explicit.reference_watts_per_module == 2.0
    assert explicit.reference_watts_source == "explicit_missing_manifest_reference"

    default_root = fake_basis_workspace(tmp_path / "default")
    default_manifest_path = default_root / "basis_manifest.json"
    default_payload = json.loads(default_manifest_path.read_text(encoding="utf-8"))
    del default_payload["reference_watts"]
    default_manifest_path.write_text(json.dumps(default_payload), encoding="utf-8")
    default = load_basis_workspace_artifacts(default_root)
    assert default.reference_watts_per_module == 1.0
    assert default.reference_watts_source == "default_missing_manifest_reference"
