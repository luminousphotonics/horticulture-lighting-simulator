from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fspm_optics.transport.basis import atomic
from fspm_optics.transport.basis.atomic import atomic_save_npy, atomic_write_text
from fspm_optics.transport.basis.artifacts import (
    load_basis_artifacts,
    load_basis_manifest,
    save_basis_artifacts,
    validate_basis_matrix_shape,
)
from fspm_optics.transport.basis.manifest import BasisManifest


def artifact_manifest() -> BasisManifest:
    return BasisManifest(
        room_length_m=3.048,
        room_width_m=3.048,
        room_height_m=3.048,
        sensor_count=3,
        control_zone_count=2,
        matrix_shape=(3, 2),
        radiance_options=("-ab", "3"),
        nthreads=6,
        reference_watts=1.0,
        layout_module_count=61,
        module_profile_id="145led_clear_lid_ptfe_stack_v2",
        ambient_cache_policy="per_control_zone",
    )


def test_saved_matrix_and_manifest_round_trip(tmp_path: Path) -> None:
    manifest = artifact_manifest()
    matrix = np.array([[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]])
    matrix_path = tmp_path / "basis_A.npy"
    manifest_path = tmp_path / "basis_manifest.json"

    saved = save_basis_artifacts(
        matrix_path=matrix_path,
        manifest_path=manifest_path,
        matrix=matrix,
        manifest=manifest,
    )
    loaded = load_basis_artifacts(
        matrix_path=matrix_path,
        manifest_path=manifest_path,
    )

    assert saved.matrix_path == matrix_path
    assert loaded.manifest == manifest
    np.testing.assert_array_equal(loaded.matrix, matrix)
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["n_vars"] == 2


def test_matrix_shape_is_validated_against_manifest() -> None:
    manifest = artifact_manifest()
    with pytest.raises(ValueError, match="does not match manifest"):
        validate_basis_matrix_shape(np.ones((2, 3)), manifest)


def test_loaded_manifest_rejects_internal_shape_mismatch(tmp_path: Path) -> None:
    payload = artifact_manifest().to_dict()
    payload["matrix_shape"] = [2, 3]
    path = tmp_path / "basis_manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="basis matrix shape"):
        load_basis_manifest(path)


def test_matrix_path_requires_npy_suffix(tmp_path: Path) -> None:
    manifest = artifact_manifest()
    with pytest.raises(ValueError, match=".npy suffix"):
        save_basis_artifacts(
            matrix_path=tmp_path / "basis.dat",
            manifest_path=tmp_path / "basis.json",
            matrix=np.ones((3, 2)),
            manifest=manifest,
        )


def test_atomic_text_write_leaves_no_final_or_temporary_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "room.rad"

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(atomic.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        atomic_write_text(output, "room\n")
    assert not output.exists()
    assert not tuple(tmp_path.glob(".room.rad.*.tmp"))


def test_atomic_npy_write_leaves_no_final_or_temporary_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "basis.npy"

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(atomic.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        atomic_save_npy(output, np.ones((2, 2)))
    assert not output.exists()
    assert not tuple(tmp_path.glob(".basis.npy.*.tmp"))
