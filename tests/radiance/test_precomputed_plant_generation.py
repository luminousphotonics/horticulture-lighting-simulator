from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.cli import scripts  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_HPS, MODE_SMD  # noqa: E402
from rad_rebuild.radiance.domain import plant_geometry_config_from_request  # noqa: E402
from rad_rebuild.radiance.engine.plants import generate_plant_scene  # noqa: E402
from rad_rebuild.radiance.engine.plants.absorption import leaf_absorption_surfaces  # noqa: E402
from rad_rebuild.radiance.engine.simulation import precompute_sweep  # noqa: E402
from rad_rebuild.radiance.engine.simulation import precomputed_playback  # noqa: E402
from rad_rebuild.radiance.engine.simulation.precomputed_dataset import (  # noqa: E402
    PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_FILENAME,
    PRECOMPUTED_PLANT_RECEIVER_JSON_GZ_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_JSON_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_JSON_FILENAME,
    PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_SCHEMA,
    PRECOMPUTED_PLANT_RECEIVER_SCHEMA_VERSION,
    build_precomputed_plant_receiver_payload,
    build_smd_precomputed_plant_receiver_basis_payload,
    load_precomputed_plant_receiver_npz,
    request_params_for_mode,
)
from rad_rebuild.radiance.engine.simulation.precomputed_integrity import (  # noqa: E402
    resolve_artifact_map,
)


def _sha256_array(matrix: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(matrix, dtype=np.float64).tobytes()).hexdigest()


def _plant_req(mode: str, *, plants_enabled: bool = True) -> RadianceRunRequest:
    return RadianceRunRequest(
        action="competitor" if mode == MODE_COMPETITOR else "uniformity",
        mode=mode,
        length_ft=10.0,
        width_ft=10.0,
        target_ppfd=1000.0,
        plants_enabled=plants_enabled,
        plant_rows=1,
        plant_columns=1,
        plant_spacing_m=0.4,
        plant_leaf_count=1,
    )


def _surface_flux_payload(req: RadianceRunRequest, ppfd: float) -> dict[str, object]:
    scene = generate_plant_scene(plant_geometry_config_from_request(req))
    rows = [
        {
            "surface_id": surface.surface_id,
            "plant_id": surface.plant_id,
            "leaf_id": surface.leaf_id,
            "leaf_index": surface.leaf_index,
            "face_index": surface.face_index,
            "area_m2": surface.area_m2,
            "incident_photon_flux_density_umol_m2_s": ppfd,
        }
        for surface in leaf_absorption_surfaces(scene)
    ]
    return {
        "receiver_granularity": req.fspm_receiver_granularity,
        "receiver_generation_basis": "synthetic_receiver_basis",
        "receiver_area_basis": "synthetic_area_basis",
        "receiver_side_policy": "synthetic_side_policy",
        "normal_generation_basis": "synthetic_normal_basis",
        "receiver_granularity_role": "synthetic_test",
        "leaf_material_profile_id": req.fspm_leaf_optical_profile_id,
        "leaf_radiance_material_mode": req.fspm_leaf_radiance_material_mode,
        "fspm_spectral_transport_mode": req.fspm_spectral_transport_mode,
        "plant_count": 1,
        "leaf_count": 1,
        "surface_count": len(rows),
        "surface_summaries": rows,
        "target_capped_incident_mean_flux_density_umol_m2_s": 12.0,
        "target_range_leaf_fraction": 0.0,
    }


def _write_plant_receiver_json(
    runtime_state: Path,
    req: RadianceRunRequest,
    *,
    ppfd: float,
    value_semantics: str,
) -> None:
    payload = build_precomputed_plant_receiver_payload(
        _surface_flux_payload(req, ppfd),
        value_semantics=value_semantics,
    )
    runtime_state.mkdir(parents=True, exist_ok=True)
    (runtime_state / PRECOMPUTED_PLANT_RECEIVER_JSON_FILENAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _patch_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path]:
    root = tmp_path / "engine-root"
    basis_root = tmp_path / "basis-root"
    (root / "runtime_state").mkdir(parents=True)
    basis_root.mkdir(parents=True)
    monkeypatch.setattr(precompute_sweep, "ROOT", root)
    monkeypatch.setattr(precompute_sweep, "RADIANCE_BASIS_OUTPUT_ROOT", basis_root)
    return root, basis_root


def _write_ppfd(path: Path, values: list[float]) -> None:
    path.write_text(
        "".join(f"{index} 0 0 {value}\n" for index, value in enumerate(values)),
        encoding="utf-8",
    )


def _stale_system_python_env() -> dict[str, str]:
    return {
        "PY": "/usr/bin/python3.12",
        "PYTHON": "/usr/bin/python3.12",
        "PYTHON_BIN": "/usr/bin/python3.12",
        "PYTHON_CMD": "/usr/bin/python3.12",
        "RADIANCE_PY": "/usr/bin/python3.12",
    }


def _assert_active_python_env(env: dict[str, str]) -> None:
    assert env["PY"] == sys.executable
    assert env["PYTHON"] == sys.executable
    assert env["PYTHON_BIN"] == sys.executable
    assert env["PYTHON_CMD"] == sys.executable
    assert env["RADIANCE_PY"] == sys.executable
    assert env["FSPM_PRECOMPUTED_SCALAR_ONLY"] == "1"


def _assert_no_spectral_biology_manifest_artifacts(manifest: dict[str, object]) -> None:
    artifacts = manifest.get("artifacts")
    assert isinstance(artifacts, dict)
    forbidden_names = set(scripts.PLANT_SPECTRAL_BIOLOGY_ARTIFACT_NAMES)
    assert not forbidden_names.intersection(str(value) for value in artifacts.values())
    assert not forbidden_names.intersection(str(key) for key in artifacts)


def test_plant_enabled_conventional_generation_writes_compact_receiver_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _basis_root = _patch_roots(monkeypatch, tmp_path)
    req = _plant_req(MODE_COMPETITOR)

    def fake_run_script(_script_path: Path, env: dict[str, str]) -> None:
        _assert_active_python_env(env)
        _write_ppfd(root / "ppfd_map.txt", [100.0, 200.0])
        (root / "runtime_state" / "spydr3_layout.json").write_text(
            '{"fixtures": []}',
            encoding="utf-8",
        )
        (root / "runtime_state" / "spydr3_power.txt").write_text(
            "model_label=synthetic\ntotal_ppf=1000\ntotal_w=500\n",
            encoding="utf-8",
        )
        _write_plant_receiver_json(
            root / "runtime_state",
            req,
            ppfd=444.0,
            value_semantics="full_output_raw_plant_ppfd",
        )

    monkeypatch.setattr(precompute_sweep, "_run_script", fake_run_script)
    bundle_dir = tmp_path / "bundle"

    precompute_sweep._generate_competitor_bundle(
        bundle_dir, req, _stale_system_python_env()
    )

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    _assert_no_spectral_biology_manifest_artifacts(manifest)
    artifacts = resolve_artifact_map(bundle_dir, manifest)
    assert PRECOMPUTED_PLANT_RECEIVER_JSON_ARTIFACT_KEY not in artifacts
    assert PRECOMPUTED_PLANT_RECEIVER_JSON_GZ_ARTIFACT_KEY not in artifacts
    receiver_path = artifacts[PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY]
    receiver = load_precomputed_plant_receiver_npz(receiver_path)

    assert receiver["schema"] == PRECOMPUTED_PLANT_RECEIVER_SCHEMA
    assert receiver["schema_version"] == PRECOMPUTED_PLANT_RECEIVER_SCHEMA_VERSION
    assert receiver["value_semantics"] == "full_output_raw_plant_ppfd"
    assert "target_capped_incident_mean_flux_density_umol_m2_s" not in receiver
    assert receiver["surface_receivers"][0]["stored_ppfd_umol_m2_s"] == pytest.approx(
        444.0
    )
    assert precomputed_playback._load_plant_receiver_payload(artifacts) is not None


def test_plant_enabled_hps_generation_writes_fixed_output_receiver_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _basis_root = _patch_roots(monkeypatch, tmp_path)
    req = _plant_req(MODE_HPS)

    def fake_run_script(_script_path: Path, env: dict[str, str]) -> None:
        _assert_active_python_env(env)
        _write_ppfd(root / "ppfd_map.txt", [300.0, 300.0])
        (root / "runtime_state" / "hps_layout.json").write_text(
            '{"fixtures": []}',
            encoding="utf-8",
        )
        (root / "runtime_state" / "hps_power.txt").write_text(
            "fixture_ppf=1797.4\ntotal_ppf=1797.4\ntotal_w=1045\n",
            encoding="utf-8",
        )
        _write_plant_receiver_json(
            root / "runtime_state",
            req,
            ppfd=321.0,
            value_semantics="fixed_output_plant_ppfd",
        )

    monkeypatch.setattr(precompute_sweep, "_run_script", fake_run_script)
    bundle_dir = tmp_path / "hps-bundle"

    precompute_sweep._generate_hps_bundle(bundle_dir, req, _stale_system_python_env())

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    _assert_no_spectral_biology_manifest_artifacts(manifest)
    artifacts = resolve_artifact_map(bundle_dir, manifest)
    assert PRECOMPUTED_PLANT_RECEIVER_JSON_ARTIFACT_KEY not in artifacts
    assert PRECOMPUTED_PLANT_RECEIVER_JSON_GZ_ARTIFACT_KEY not in artifacts
    receiver = load_precomputed_plant_receiver_npz(
        artifacts[PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY]
    )

    assert receiver["value_semantics"] == "fixed_output_plant_ppfd"
    assert receiver["surface_receivers"][0]["stored_ppfd_umol_m2_s"] == pytest.approx(
        321.0
    )


def test_plant_enabled_smd_generation_writes_receiver_basis_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, basis_root = _patch_roots(monkeypatch, tmp_path)
    req = _plant_req(MODE_SMD)
    canopy_basis = np.array([[1.0, 0.5], [0.8, 0.4]], dtype=np.float64)
    plant_basis = np.array([[10.0, 20.0], [30.0, 40.0]], dtype=np.float64)

    calls: dict[str, object] = {}

    def fake_run_script(_script_path: Path, env: dict[str, str]) -> None:
        assert env["FSPM_PRECOMPUTE_PLANT_RECEIVER_BASIS"] == "1"
        _assert_active_python_env(env)
        np.save(basis_root / "basis_A.npy", canopy_basis)
        (basis_root / "basis_manifest.json").write_text(
            json.dumps(
                {
                    "n_points": 2,
                    "n_vars": 2,
                    "n_rings": 2,
                    "layout_modules": 10,
                    "basis_unit_w_per_module": 1.0,
                    "variables": "rings",
                    "ring_indices": [0, 1],
                    "emitter_env": {"LAYOUT_MODE": "exact_tiled"},
                    "basis_matrix_sha256": _sha256_array(canopy_basis),
                }
            ),
            encoding="utf-8",
        )
        (root / "runtime_state" / "smd_layout.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "units": "meters",
                    "rings": 2,
                    "modules": 10,
                    "positions": [],
                }
            ),
            encoding="utf-8",
        )
        receiver_payload = build_precomputed_plant_receiver_payload(
            _surface_flux_payload(req, 0.0),
            value_semantics="smd_receiver_basis",
            basis_metadata={
                "variables": "rings",
                "ring_indices": [0, 1],
                "basis_matrix_sha256": _sha256_array(canopy_basis),
            },
        )
        (basis_root / PRECOMPUTED_PLANT_RECEIVER_JSON_FILENAME).write_text(
            json.dumps(receiver_payload),
            encoding="utf-8",
        )
        np.save(basis_root / PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_FILENAME, plant_basis)

    def fake_check_call(
        cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
    ) -> int:
        calls["cmd"] = cmd
        calls["cwd"] = cwd
        assert env is not None
        _assert_active_python_env(env)
        return 0

    monkeypatch.setattr(precompute_sweep, "_run_script", fake_run_script)
    monkeypatch.setattr(precompute_sweep.subprocess, "check_call", fake_check_call)
    bundle_dir = tmp_path / "smd-bundle"

    precompute_sweep._generate_smd_bundle(
        bundle_dir, req, _stale_system_python_env()
    )

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    _assert_no_spectral_biology_manifest_artifacts(manifest)
    artifacts = resolve_artifact_map(bundle_dir, manifest)
    assert PRECOMPUTED_PLANT_RECEIVER_JSON_ARTIFACT_KEY not in artifacts
    assert PRECOMPUTED_PLANT_RECEIVER_JSON_GZ_ARTIFACT_KEY not in artifacts
    assert PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_ARTIFACT_KEY not in artifacts
    with np.load(
        artifacts[PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_ARTIFACT_KEY],
        allow_pickle=False,
    ) as archive:
        copied_plant_basis = archive["plant_receiver_basis_A"]
    receiver = load_precomputed_plant_receiver_npz(
        artifacts[PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY]
    )

    assert copied_plant_basis.shape == plant_basis.shape
    assert copied_plant_basis.shape[1] == canopy_basis.shape[1]
    assert np.allclose(copied_plant_basis, plant_basis)
    assert receiver["basis_metadata"]["ring_indices"] == [0, 1]
    assert receiver["value_semantics"] == "smd_receiver_basis"
    assert precomputed_playback._load_plant_receiver_payload(artifacts) is not None
    assert calls["cmd"] == [
        sys.executable,
        "-m",
        "rad_rebuild.radiance.engine.emitters.generate_emitters_smd",
    ]


def test_precompute_generation_does_not_hardcode_system_python() -> None:
    source_paths = [
        Path(precompute_sweep.__file__),
        precompute_sweep.SCRIPT_ROOT / "run_basis_extraction.sh",
    ]
    for source_path in source_paths:
        source = source_path.read_text(encoding="utf-8")
        assert "/usr/bin/python3.12" not in source
        assert "python3.12" not in source


def test_smd_receiver_basis_builder_preserves_surface_and_column_order() -> None:
    first = {
        "schema": PRECOMPUTED_PLANT_RECEIVER_SCHEMA,
        "schema_version": PRECOMPUTED_PLANT_RECEIVER_SCHEMA_VERSION,
        "value_semantics": "full_output_raw_plant_ppfd",
        "surface_receivers": [
            {"surface_id": "surface_a", "stored_ppfd_umol_m2_s": 1.0},
            {"surface_id": "surface_b", "stored_ppfd_umol_m2_s": 2.0},
        ],
    }
    second = {
        **first,
        "surface_receivers": [
            {"surface_id": "surface_a", "stored_ppfd_umol_m2_s": 3.0},
            {"surface_id": "surface_b", "stored_ppfd_umol_m2_s": 4.0},
        ],
    }

    receiver_payload, basis = build_smd_precomputed_plant_receiver_basis_payload(
        [first, second],
        basis_metadata={"variables": "rings", "ring_indices": [0, 1]},
    )

    assert receiver_payload["value_semantics"] == "smd_receiver_basis"
    assert [
        row["surface_id"] for row in receiver_payload["surface_receivers"]
    ] == ["surface_a", "surface_b"]
    assert np.allclose(basis, np.array([[1.0, 3.0], [2.0, 4.0]]))
    assert receiver_payload["basis_metadata"]["ring_indices"] == [0, 1]


def test_non_plant_conventional_generation_manifest_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _basis_root = _patch_roots(monkeypatch, tmp_path)
    req = _plant_req(MODE_COMPETITOR, plants_enabled=False)

    def fake_run_script(_script_path: Path, _env: dict[str, str]) -> None:
        _write_ppfd(root / "ppfd_map.txt", [100.0, 100.0])
        (root / "runtime_state" / "spydr3_layout.json").write_text(
            '{"fixtures": []}',
            encoding="utf-8",
        )
        (root / "runtime_state" / "spydr3_power.txt").write_text(
            "model_label=synthetic\ntotal_ppf=1000\ntotal_w=500\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(precompute_sweep, "_run_script", fake_run_script)
    bundle_dir = tmp_path / "non-plant-bundle"

    precompute_sweep._generate_competitor_bundle(bundle_dir, req, {})

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert PRECOMPUTED_PLANT_RECEIVER_JSON_ARTIFACT_KEY not in manifest["artifacts"]
    assert PRECOMPUTED_PLANT_RECEIVER_JSON_GZ_ARTIFACT_KEY not in manifest["artifacts"]
    assert PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY not in manifest["artifacts"]


def test_older_bundle_without_plant_artifacts_remains_optional(tmp_path: Path) -> None:
    ppfd_path = tmp_path / "ppfd_map.txt.gz"
    with gzip.open(ppfd_path, "wt", encoding="utf-8") as handle:
        handle.write("0 0 0 100\n")

    assert precomputed_playback._load_plant_receiver_payload({}) is None


def test_runtime_targets_remain_excluded_from_plant_bundle_identity() -> None:
    base = _plant_req(MODE_SMD)
    changed = RadianceRunRequest(
        **{
            **base.model_dump(),
            "target_ppfd": 500.0,
            "fspm_target_ppfd_umol_m2_s": 250.0,
            "fspm_target_tolerance_umol_m2_s": 5.0,
        }
    )

    assert request_params_for_mode(base) == request_params_for_mode(changed)
