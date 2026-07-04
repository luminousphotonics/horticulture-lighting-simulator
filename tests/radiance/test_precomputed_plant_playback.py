from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from contextlib import redirect_stdout
import io

import numpy as np
import pytest

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.assembly.scene import build_assembly_scene  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_HPS, MODE_SMD  # noqa: E402
from rad_rebuild.radiance.domain import plant_geometry_config_from_request  # noqa: E402
from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (  # noqa: E402
    DEFAULT_IES_VARIANT as DEFAULT_HPS_IES_VARIANT,
)
from rad_rebuild.radiance.engine.plants import generate_plant_scene  # noqa: E402
from rad_rebuild.radiance.engine.plants.absorption import (  # noqa: E402
    _lighting_region,
    leaf_absorption_surfaces,
)
from rad_rebuild.radiance.engine.plants.surface_flux import (  # noqa: E402
    PLANT_SURFACE_FLUX_SCHEMA,
)
from rad_rebuild.radiance.engine.simulation import precomputed_playback  # noqa: E402
from rad_rebuild.radiance.engine.simulation.precomputed_dataset import (  # noqa: E402
    PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_JSON_GZ_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_JSON_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY,
    PRECOMPUTED_PLANT_RECEIVER_SCHEMA,
    PRECOMPUTED_PLANT_RECEIVER_SCHEMA_VERSION,
    request_params_for_mode,
    write_precomputed_plant_receiver_npz,
)


def _basis_sha256(basis: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(basis, dtype=np.float64).tobytes()).hexdigest()


def _plant_req(
    mode: str,
    *,
    target_ppfd: float = 100.0,
    fspm_target: float | None = 180.0,
    fspm_tolerance: float | None = 20.0,
    plant_rows: int = 1,
    plant_columns: int = 1,
    plant_leaf_count: int = 1,
) -> RadianceRunRequest:
    return RadianceRunRequest(
        action="competitor" if mode == MODE_COMPETITOR else "uniformity",
        mode=mode,
        length_ft=10.0,
        width_ft=10.0,
        target_ppfd=target_ppfd,
        plants_enabled=True,
        plant_rows=plant_rows,
        plant_columns=plant_columns,
        plant_spacing_m=0.40,
        plant_leaf_count=plant_leaf_count,
        fspm_target_ppfd_umol_m2_s=fspm_target,
        fspm_target_tolerance_umol_m2_s=fspm_tolerance,
        hps_ies_variant=DEFAULT_HPS_IES_VARIANT,
    )


def _receiver_entries(req: RadianceRunRequest, stored_ppfd: float) -> list[dict[str, object]]:
    scene = generate_plant_scene(plant_geometry_config_from_request(req))
    return [
        {
            "surface_id": surface.surface_id,
            "stored_ppfd_umol_m2_s": stored_ppfd,
        }
        for surface in leaf_absorption_surfaces(scene)
    ]


def _write_receiver_json(
    tmp_path: Path,
    req: RadianceRunRequest,
    *,
    stored_ppfd: float,
    value_semantics: str,
) -> Path:
    path = tmp_path / "plant_receiver.json"
    path.write_text(
        json.dumps(
            {
                "schema": PRECOMPUTED_PLANT_RECEIVER_SCHEMA,
                "schema_version": PRECOMPUTED_PLANT_RECEIVER_SCHEMA_VERSION,
                "receiver_granularity": req.fspm_receiver_granularity,
                "value_semantics": value_semantics,
                "surface_receivers": _receiver_entries(req, stored_ppfd),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _receiver_artifacts(
    tmp_path: Path,
    req: RadianceRunRequest,
    *,
    stored_ppfd: float,
    value_semantics: str = "full_output_raw_plant_ppfd",
) -> dict[str, Path]:
    return {
        PRECOMPUTED_PLANT_RECEIVER_JSON_ARTIFACT_KEY: _write_receiver_json(
            tmp_path,
            req,
            stored_ppfd=stored_ppfd,
            value_semantics=value_semantics,
        )
    }


def test_playback_loads_older_gzip_receiver_artifact(tmp_path: Path) -> None:
    req = _plant_req(MODE_COMPETITOR)
    receiver_json = _write_receiver_json(
        tmp_path,
        req,
        stored_ppfd=123.0,
        value_semantics="full_output_raw_plant_ppfd",
    )
    receiver_gz = tmp_path / "plant_receiver.json.gz"
    with gzip.open(receiver_gz, "wt", encoding="utf-8", compresslevel=9) as handle:
        json.dump(
            json.loads(receiver_json.read_text(encoding="utf-8")),
            handle,
            sort_keys=True,
            separators=(",", ":"),
        )

    payload = precomputed_playback._load_plant_receiver_payload(
        {PRECOMPUTED_PLANT_RECEIVER_JSON_GZ_ARTIFACT_KEY: receiver_gz}
    )

    assert payload is not None
    assert payload["surface_receivers"][0]["stored_ppfd_umol_m2_s"] == pytest.approx(
        123.0
    )


def _patch_room_and_grid(monkeypatch: pytest.MonkeyPatch, workspace_root: Path) -> None:
    def fake_room_and_grid(_env: dict[str, str], _workspace_root: Path) -> None:
        workspace_root.mkdir(parents=True, exist_ok=True)
        (workspace_root / "runtime_state").mkdir(parents=True, exist_ok=True)
        (workspace_root / "sensor_points.txt").write_text(
            "0 0 0\n1 0 0\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        precomputed_playback,
        "_write_room_and_grid",
        fake_room_and_grid,
    )


def _write_gz_ppfd(path: Path, values: list[float]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for index, value in enumerate(values):
            handle.write(f"{index} 0 0 {value}\n")


def _competitor_artifacts(tmp_path: Path, req: RadianceRunRequest) -> dict[str, Path]:
    ppfd_path = tmp_path / "ppfd_map.txt.gz"
    _write_gz_ppfd(ppfd_path, [200.0, 200.0])
    layout_path = tmp_path / "spydr3_layout.json"
    layout_path.write_text('{"fixtures": []}', encoding="utf-8")
    power_path = tmp_path / "power.json"
    power_path.write_text(
        json.dumps(
            {
                "model_label": "synthetic conventional",
                "total_ppf": 1000.0,
                "total_w": 500.0,
            }
        ),
        encoding="utf-8",
    )
    return {
        "ppfd_map_txt_gz": ppfd_path,
        "layout_json": layout_path,
        "power_json": power_path,
        **_receiver_artifacts(
            tmp_path,
            req,
            stored_ppfd=400.0,
            value_semantics="full_output_raw_plant_ppfd",
        ),
    }


def _hps_artifacts(tmp_path: Path, req: RadianceRunRequest) -> dict[str, Path]:
    ppfd_path = tmp_path / "ppfd_map.txt.gz"
    _write_gz_ppfd(ppfd_path, [300.0, 300.0])
    layout_path = tmp_path / "hps_layout.json"
    layout_path.write_text('{"fixtures": []}', encoding="utf-8")
    power_path = tmp_path / "power.json"
    power_path.write_text(
        json.dumps(
            {
                "model_label": "synthetic hps",
                "fixture_ppf": 1797.4,
                "total_ppf": 1797.4,
                "total_w": 1045.0,
            }
        ),
        encoding="utf-8",
    )
    return {
        "ppfd_map_txt_gz": ppfd_path,
        "layout_json": layout_path,
        "power_json": power_path,
        **_receiver_artifacts(
            tmp_path,
            req,
            stored_ppfd=400.0,
            value_semantics="fixed_output_plant_ppfd",
        ),
    }


def _materialize_competitor_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    req: RadianceRunRequest,
) -> dict[str, object]:
    workspace = tmp_path / f"workspace_{req.target_ppfd:g}_{req.fspm_target_ppfd_umol_m2_s}_{req.fspm_target_tolerance_umol_m2_s}"
    _patch_room_and_grid(monkeypatch, workspace)
    with redirect_stdout(io.StringIO()):
        precomputed_playback._materialize_competitor(
            tmp_path,
            {"stats": {"base_mean_ppfd": 200.0, "base_peak_ppfd": 200.0}},
            req,
            workspace,
            _competitor_artifacts(tmp_path, req),
        )
    return json.loads(
        (workspace / "runtime_state" / "plant_surface_flux.json").read_text(
            encoding="utf-8"
        )
    )


def _materialize_hps_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    req: RadianceRunRequest,
) -> dict[str, object]:
    workspace = tmp_path / f"hps_workspace_{req.target_ppfd:g}_{req.fspm_target_ppfd_umol_m2_s}_{req.fspm_target_tolerance_umol_m2_s}"
    _patch_room_and_grid(monkeypatch, workspace)
    with redirect_stdout(io.StringIO()):
        precomputed_playback._materialize_hps(
            tmp_path,
            {},
            req,
            workspace,
            _hps_artifacts(tmp_path, req),
        )
    return json.loads(
        (workspace / "runtime_state" / "plant_surface_flux.json").read_text(
            encoding="utf-8"
        )
    )


def _assert_surface_flux_color_payload(payload: dict[str, object]) -> None:
    assert payload["schema"] == PLANT_SURFACE_FLUX_SCHEMA
    visualization = payload.get("visualization")
    assert isinstance(visualization, dict)
    assert visualization["color_metric"] == "incident_photon_flux_density_umol_m2_s"
    assert visualization["color_quantity"] == "incident_leaf_surface_ppfd"
    leaf_values = visualization.get("leaf_values")
    assert isinstance(leaf_values, list)
    assert leaf_values
    assert "target_deviation" in leaf_values[0]
    assert "target_classification_ppfd_umol_m2_s" in leaf_values[0]


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _runtime_run_id(payload: dict[str, object]) -> str:
    runtime_source = payload.get("runtime_source")
    assert isinstance(runtime_source, dict)
    run_id = runtime_source.get("run_id")
    assert isinstance(run_id, str)
    assert run_id
    return run_id


def test_conventional_lighting_target_scales_runtime_plant_ppfd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lower_target = _materialize_competitor_payload(
        tmp_path,
        monkeypatch,
        _plant_req(MODE_COMPETITOR, target_ppfd=50.0),
    )
    higher_target = _materialize_competitor_payload(
        tmp_path,
        monkeypatch,
        _plant_req(MODE_COMPETITOR, target_ppfd=100.0),
    )

    assert lower_target["raw_mean_flux_density_umol_m2_s"] == pytest.approx(100.0)
    assert higher_target["raw_mean_flux_density_umol_m2_s"] == pytest.approx(200.0)
    _assert_surface_flux_color_payload(higher_target)


def test_conventional_fspm_target_and_tolerance_recompute_reporting_not_raw_ppfd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _materialize_competitor_payload(
        tmp_path,
        monkeypatch,
        _plant_req(
            MODE_COMPETITOR,
            target_ppfd=100.0,
            fspm_target=180.0,
            fspm_tolerance=25.0,
        ),
    )
    changed_target = _materialize_competitor_payload(
        tmp_path,
        monkeypatch,
        _plant_req(
            MODE_COMPETITOR,
            target_ppfd=100.0,
            fspm_target=80.0,
            fspm_tolerance=25.0,
        ),
    )
    changed_tolerance = _materialize_competitor_payload(
        tmp_path,
        monkeypatch,
        _plant_req(
            MODE_COMPETITOR,
            target_ppfd=100.0,
            fspm_target=180.0,
            fspm_tolerance=10.0,
        ),
    )

    assert base["raw_mean_flux_density_umol_m2_s"] == pytest.approx(
        changed_target["raw_mean_flux_density_umol_m2_s"]
    )
    assert base["raw_mean_flux_density_umol_m2_s"] == pytest.approx(
        changed_tolerance["raw_mean_flux_density_umol_m2_s"]
    )
    assert base["target_capped_incident_mean_flux_density_umol_m2_s"] == pytest.approx(
        180.0
    )
    assert changed_target[
        "target_capped_incident_mean_flux_density_umol_m2_s"
    ] == pytest.approx(80.0)
    assert base["target_range_leaf_fraction"] == pytest.approx(1.0)
    assert changed_tolerance["over_lit_leaf_fraction"] == pytest.approx(1.0)


def test_hps_lighting_target_does_not_scale_runtime_plant_ppfd_but_fspm_reporting_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lower_lighting = _materialize_hps_payload(
        tmp_path,
        monkeypatch,
        _plant_req(
            MODE_HPS,
            target_ppfd=100.0,
            fspm_target=400.0,
            fspm_tolerance=20.0,
        ),
    )
    higher_lighting = _materialize_hps_payload(
        tmp_path,
        monkeypatch,
        _plant_req(
            MODE_HPS,
            target_ppfd=1000.0,
            fspm_target=400.0,
            fspm_tolerance=20.0,
        ),
    )
    changed_fspm = _materialize_hps_payload(
        tmp_path,
        monkeypatch,
        _plant_req(
            MODE_HPS,
            target_ppfd=1000.0,
            fspm_target=350.0,
            fspm_tolerance=20.0,
        ),
    )

    assert lower_lighting["raw_mean_flux_density_umol_m2_s"] == pytest.approx(400.0)
    assert higher_lighting["raw_mean_flux_density_umol_m2_s"] == pytest.approx(400.0)
    assert higher_lighting[
        "target_capped_incident_mean_flux_density_umol_m2_s"
    ] == pytest.approx(400.0)
    assert changed_fspm[
        "target_capped_incident_mean_flux_density_umol_m2_s"
    ] == pytest.approx(350.0)
    assert higher_lighting["target_range_leaf_fraction"] == pytest.approx(1.0)
    assert changed_fspm["over_lit_leaf_fraction"] == pytest.approx(1.0)
    _assert_surface_flux_color_payload(higher_lighting)


def _smd_artifacts(tmp_path: Path, req: RadianceRunRequest) -> dict[str, Path]:
    basis = np.array([[100.0], [100.0]], dtype=float)
    basis_path = tmp_path / "basis_A.npy"
    np.save(basis_path, basis)
    basis_manifest = {
        "n_points": 2,
        "n_vars": 1,
        "n_rings": 1,
        "layout_modules": 1,
        "basis_unit_w_per_module": 1.0,
        "variables": "rings",
        "ring_indices": [0],
        "emitter_env": {"LAYOUT_MODE": "exact_tiled"},
        "matrix_sha256": _basis_sha256(basis),
    }
    basis_manifest_path = tmp_path / "basis_manifest.json"
    basis_manifest_path.write_text(json.dumps(basis_manifest), encoding="utf-8")
    layout_path = tmp_path / "smd_layout.json"
    layout_path.write_text(
        json.dumps(
            {
                "version": 2,
                "units": "meters",
                "rings": 1,
                "modules": 1,
                "positions": [{"x": 0.0, "y": 0.0, "z": 0.4572, "ring": 0}],
            }
        ),
        encoding="utf-8",
    )
    receiver_json = _write_receiver_json(
        tmp_path,
        req,
        stored_ppfd=0.0,
        value_semantics="smd_receiver_basis",
    )
    receiver_count = len(_receiver_entries(req, 0.0))
    plant_basis = np.full((receiver_count, 1), 20.0, dtype=float)
    plant_basis_path = tmp_path / "plant_receiver_basis_A.npy"
    np.save(plant_basis_path, plant_basis)
    return {
        "basis_A_npy": basis_path,
        "basis_manifest_json": basis_manifest_path,
        "layout_json": layout_path,
        PRECOMPUTED_PLANT_RECEIVER_JSON_ARTIFACT_KEY: receiver_json,
        PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_ARTIFACT_KEY: plant_basis_path,
    }


def _write_smd_bundle(
    dataset_root: Path,
    req: RadianceRunRequest,
) -> Path:
    bundle_dir = dataset_root / "smd" / "10x10"
    bundle_dir.mkdir(parents=True)
    artifacts = _smd_artifacts(bundle_dir, req)
    receiver_npz_path = bundle_dir / "plant_receiver.npz"
    write_precomputed_plant_receiver_npz(
        receiver_npz_path,
        json.loads(
            artifacts[PRECOMPUTED_PLANT_RECEIVER_JSON_ARTIFACT_KEY].read_text(
                encoding="utf-8"
            )
        ),
    )
    plant_basis_npz_path = bundle_dir / "plant_receiver_basis_A.npz"
    np.savez_compressed(
        plant_basis_npz_path,
        plant_receiver_basis_A=np.load(
            artifacts[PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_ARTIFACT_KEY],
            allow_pickle=False,
        ),
    )
    manifest = {
        "schema_version": 1,
        "mode": MODE_SMD,
        "dims_ft": {"length_ft": 10, "width_ft": 10},
        "request_params": request_params_for_mode(req),
        "artifacts": {
            "basis_A_npy": artifacts["basis_A_npy"].name,
            "basis_manifest_json": artifacts["basis_manifest_json"].name,
            "layout_json": artifacts["layout_json"].name,
            PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY: receiver_npz_path.name,
            PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_ARTIFACT_KEY: plant_basis_npz_path.name,
        },
        "stats": {"n_points": 2, "n_vars": 1},
    }
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return bundle_dir


def _materialize_smd_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    req: RadianceRunRequest,
) -> tuple[dict[str, object], dict[str, object]]:
    workspace = tmp_path / f"smd_workspace_{req.target_ppfd:g}_{req.fspm_target_ppfd_umol_m2_s}_{req.fspm_target_tolerance_umol_m2_s}"
    _patch_room_and_grid(monkeypatch, workspace)
    with redirect_stdout(io.StringIO()):
        precomputed_playback._materialize_smd(
            tmp_path,
            {},
            req,
            workspace,
            _smd_artifacts(tmp_path, req),
        )
    surface_flux = json.loads(
        (workspace / "runtime_state" / "plant_surface_flux.json").read_text(
            encoding="utf-8"
        )
    )
    solution = json.loads(
        (workspace / "ring_powers_optimized.json").read_text(encoding="utf-8")
    )
    return surface_flux, solution


def test_smd_plant_receiver_basis_uses_canopy_playback_coefficients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _plant_req(
        MODE_SMD,
        target_ppfd=100.0,
        fspm_target=180.0,
        fspm_tolerance=25.0,
    )
    surface_flux, solution = _materialize_smd_payload(tmp_path, monkeypatch, req)
    coeff = float(solution["basis_coefficients"][0])

    assert coeff > 0.0
    assert surface_flux["raw_mean_flux_density_umol_m2_s"] == pytest.approx(
        20.0 * coeff
    )
    _assert_surface_flux_color_payload(surface_flux)


def test_smd_plant_receiver_basis_column_mismatch_fails_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _plant_req(MODE_SMD, target_ppfd=100.0)
    workspace = tmp_path / "basis_mismatch_workspace"
    _patch_room_and_grid(monkeypatch, workspace)
    artifacts = _smd_artifacts(tmp_path, req)
    receiver_count = len(_receiver_entries(req, 0.0))
    mismatch_path = tmp_path / "plant_receiver_basis_mismatch.npy"
    np.save(mismatch_path, np.zeros((receiver_count, 2), dtype=float))
    artifacts[PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_ARTIFACT_KEY] = mismatch_path

    with pytest.raises(
        precomputed_playback.PlaybackError,
        match="plant receiver basis columns do not match SMD coefficients",
    ):
        precomputed_playback._materialize_smd(
            tmp_path,
            {},
            req,
            workspace,
            artifacts,
        )


def test_smd_target_classification_uses_runtime_plant_receiver_not_ppfd_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _plant_req(
        MODE_SMD,
        target_ppfd=275.0,
        fspm_target=275.0,
        fspm_tolerance=20.0,
    )
    surface_flux, solution = _materialize_smd_payload(tmp_path, monkeypatch, req)
    coeff = float(solution["basis_coefficients"][0])

    assert surface_flux["raw_mean_flux_density_umol_m2_s"] == pytest.approx(
        20.0 * coeff
    )
    assert surface_flux["raw_mean_flux_density_umol_m2_s"] < 255.0
    assert (
        surface_flux["target_classification_source"]
        == "plant_surface_receiver_rows"
    )
    assert surface_flux["target_classification_mean_ppfd_umol_m2_s"] == pytest.approx(
        surface_flux["raw_mean_flux_density_umol_m2_s"]
    )
    assert surface_flux["target_range_leaf_fraction"] == pytest.approx(0.0)
    assert surface_flux["under_lit_leaf_count"] == surface_flux["leaf_count"]
    assert surface_flux["over_lit_leaf_count"] == 0


def test_precomputed_plant_playback_diagnostics_report_runtime_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _plant_req(
        MODE_SMD,
        target_ppfd=275.0,
        fspm_target=275.0,
        fspm_tolerance=20.0,
    )
    workspace = tmp_path / "diagnostic_workspace"
    _patch_room_and_grid(monkeypatch, workspace)
    with redirect_stdout(io.StringIO()):
        precomputed_playback._materialize_smd(
            tmp_path,
            {},
            req,
            workspace,
            _smd_artifacts(tmp_path, req),
        )

    diagnostics = precomputed_playback.precomputed_plant_playback_diagnostics(
        workspace
    )

    assert diagnostics["baseline_ppfd_map"]["mean"] == pytest.approx(275.0)
    assert diagnostics["baseline_ppfd_map"]["target_counts"]["target_range"] == 2
    assert diagnostics["plant_receiver_runtime_ppfd"]["mean"] < 255.0
    assert diagnostics["leaf_aggregate_classification_ppfd"]["mean"] == pytest.approx(
        diagnostics["plant_receiver_runtime_ppfd"]["mean"]
    )
    assert diagnostics["leaf_aggregate_classification_ppfd"]["target_counts"][
        "under_lit"
    ] == diagnostics["leaf_aggregate_classification_ppfd"]["count"]
    assert diagnostics["fspm_panel_source"] == "plant_surface_receiver_rows"


def test_fspm_target_tolerance_boundaries_are_inclusive() -> None:
    lower = 255.0
    upper = 295.0

    assert (
        _lighting_region(254.99, lower_threshold=lower, upper_threshold=upper)
        == "under_lit"
    )
    assert (
        _lighting_region(255.0, lower_threshold=lower, upper_threshold=upper)
        == "target_range"
    )
    assert (
        _lighting_region(275.0, lower_threshold=lower, upper_threshold=upper)
        == "target_range"
    )
    assert (
        _lighting_region(295.0, lower_threshold=lower, upper_threshold=upper)
        == "target_range"
    )
    assert (
        _lighting_region(295.01, lower_threshold=lower, upper_threshold=upper)
        == "over_lit"
    )


def test_smd_receiver_json_is_canonical_for_surface_flux_scene_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _plant_req(
        MODE_SMD,
        target_ppfd=100.0,
        fspm_target=180.0,
        fspm_tolerance=25.0,
    )
    workspace = tmp_path / "smd_scene_workspace"
    _patch_room_and_grid(monkeypatch, workspace)
    artifacts = _smd_artifacts(tmp_path, req)
    assert "plant_surface_flux_json" not in artifacts

    with redirect_stdout(io.StringIO()):
        precomputed_playback._materialize_smd(
            tmp_path,
            {},
            req,
            workspace,
            artifacts,
        )

    runtime = workspace / "runtime_state"
    assert (runtime / "plants_viewer.json").is_file()
    assert (runtime / "plant_surface_flux.json").is_file()
    scene = build_assembly_scene(workspace, req)
    surface_flux = scene["plants"]["surface_flux"]
    _assert_surface_flux_color_payload(surface_flux)


def test_cli_playback_infers_plant_artifacts_from_manifest_receiver_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_req = _plant_req(
        MODE_SMD,
        target_ppfd=100.0,
        fspm_target=180.0,
        fspm_tolerance=25.0,
    )
    dataset_root = tmp_path / "dataset"
    _write_smd_bundle(dataset_root, bundle_req)
    workspace = tmp_path / "cli_workspace"
    _patch_room_and_grid(monkeypatch, workspace)

    config = precomputed_playback.PrecomputedPlaybackConfig(
        mode=MODE_SMD,
        length_ft=10.0,
        width_ft=10.0,
        target_ppfd=100.0,
        dataset_root=dataset_root,
        workspace_root=workspace,
    )
    with redirect_stdout(io.StringIO()):
        precomputed_playback.run_precomputed_playback(config)

    runtime = workspace / "runtime_state"
    assert (runtime / "plant_receiver.json").is_file()
    assert (runtime / "plant_surface_flux.json").is_file()
    assert (runtime / "plants_viewer.json").is_file()
    assert (workspace / "assembly_scene.json").is_file()
    for forbidden in (
        "plant_spectral_absorption.json",
        "plant_spectral_response.json",
        "plant_photosynthesis_response.json",
        "plant_photoreceptor_exposure.json",
        "plant_photomorphogenesis_response.json",
    ):
        assert not (runtime / forbidden).exists()

    receiver = json.loads((runtime / "plant_receiver.json").read_text(encoding="utf-8"))
    assert receiver["surface_receivers"][0]["runtime_ppfd_umol_m2_s"] > 0.0
    surface_flux = json.loads(
        (runtime / "plant_surface_flux.json").read_text(encoding="utf-8")
    )
    _assert_surface_flux_color_payload(surface_flux)
    scene = json.loads((workspace / "assembly_scene.json").read_text(encoding="utf-8"))
    _assert_surface_flux_color_payload(scene["plants"]["surface_flux"])


def _run_smd_bundle_playback(
    dataset_root: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    target_ppfd: float,
    fspm_target: float,
    fspm_tolerance: float,
) -> None:
    _patch_room_and_grid(monkeypatch, workspace)
    config = precomputed_playback.PrecomputedPlaybackConfig(
        mode=MODE_SMD,
        length_ft=10.0,
        width_ft=10.0,
        target_ppfd=target_ppfd,
        fspm_target_ppfd_umol_m2_s=fspm_target,
        fspm_target_tolerance_umol_m2_s=fspm_tolerance,
        dataset_root=dataset_root,
        workspace_root=workspace,
    )
    with redirect_stdout(io.StringIO()):
        precomputed_playback.run_precomputed_playback(config)


def test_precomputed_playback_cleans_stale_plant_runtime_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_req = _plant_req(MODE_SMD, target_ppfd=100.0)
    dataset_root = tmp_path / "dataset"
    _write_smd_bundle(dataset_root, bundle_req)
    workspace = tmp_path / "shared_workspace"
    runtime = workspace / "runtime_state"
    runtime.mkdir(parents=True)
    stale = {"stale": True, "runtime_source": {"run_id": "stale"}}
    for name in (
        "plant_receiver.json",
        "plant_surface_flux.json",
        "plants_viewer.json",
        "fspm_panel_metrics.json",
        "plant_spectral_absorption.json",
    ):
        (runtime / name).write_text(json.dumps(stale), encoding="utf-8")
    (workspace / "assembly_scene.json").write_text(json.dumps(stale), encoding="utf-8")

    _run_smd_bundle_playback(
        dataset_root,
        workspace,
        monkeypatch,
        target_ppfd=100.0,
        fspm_target=180.0,
        fspm_tolerance=20.0,
    )

    assert not (runtime / "plant_spectral_absorption.json").exists()
    receiver = _load_json(runtime / "plant_receiver.json")
    surface = _load_json(runtime / "plant_surface_flux.json")
    viewer = _load_json(runtime / "plants_viewer.json")
    panel = _load_json(runtime / "fspm_panel_metrics.json")
    scene = _load_json(workspace / "assembly_scene.json")
    for payload in (receiver, surface, viewer, panel, scene):
        assert "stale" not in payload

    run_id = _runtime_run_id(receiver)
    assert _runtime_run_id(surface) == run_id
    assert _runtime_run_id(viewer) == run_id
    assert _runtime_run_id(panel) == run_id
    assert _runtime_run_id(scene) == run_id
    assert scene["plants"]["surface_flux"]["runtime_source"]["run_id"] == run_id
    assert scene["fspm_metrics"]["runtime_source"]["run_id"] == run_id


def test_precomputed_playback_reuses_workspace_without_stale_target_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_req = _plant_req(MODE_SMD, target_ppfd=750.0)
    dataset_root = tmp_path / "dataset"
    _write_smd_bundle(dataset_root, bundle_req)
    workspace = tmp_path / "shared_workspace"

    _run_smd_bundle_playback(
        dataset_root,
        workspace,
        monkeypatch,
        target_ppfd=750.0,
        fspm_target=750.0,
        fspm_tolerance=20.0,
    )
    first_surface = _load_json(workspace / "runtime_state" / "plant_surface_flux.json")
    first_run_id = _runtime_run_id(first_surface)

    _run_smd_bundle_playback(
        dataset_root,
        workspace,
        monkeypatch,
        target_ppfd=275.0,
        fspm_target=275.0,
        fspm_tolerance=20.0,
    )

    runtime = workspace / "runtime_state"
    surface = _load_json(runtime / "plant_surface_flux.json")
    panel = _load_json(runtime / "fspm_panel_metrics.json")
    scene = _load_json(workspace / "assembly_scene.json")
    second_run_id = _runtime_run_id(surface)

    assert second_run_id != first_run_id
    assert surface["target_ppfd_umol_m2_s"] == pytest.approx(275.0)
    assert panel["incident_leaf_surface_flux"]["target_ppfd_umol_m2_s"] == pytest.approx(
        275.0
    )
    assert scene["plants"]["surface_flux"]["target_ppfd_umol_m2_s"] == pytest.approx(
        275.0
    )
    assert scene["runtime_source"]["lighting_target_ppfd"] == pytest.approx(275.0)
    assert scene["runtime_source"]["fspm_target_ppfd"] == pytest.approx(275.0)
    assert _runtime_run_id(panel) == second_run_id
    assert _runtime_run_id(scene) == second_run_id
    assert scene["plants"]["surface_flux"]["runtime_source"]["run_id"] == second_run_id
    assert scene["fspm_metrics"]["runtime_source"]["run_id"] == second_run_id
    assert surface["target_classification_source"] == "plant_surface_receiver_rows"
    assert surface["target_classification_mean_ppfd_umol_m2_s"] == pytest.approx(
        surface["raw_mean_flux_density_umol_m2_s"]
    )
    assert surface["raw_mean_flux_density_umol_m2_s"] < 275.0


def test_precomputed_playback_materializes_10x10_compact_plant_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_req = _plant_req(
        MODE_SMD,
        target_ppfd=100.0,
        plant_rows=8,
        plant_columns=8,
        plant_leaf_count=12,
    )
    dataset_root = tmp_path / "dataset"
    _write_smd_bundle(dataset_root, bundle_req)
    workspace = tmp_path / "compact_counts_workspace"

    _run_smd_bundle_playback(
        dataset_root,
        workspace,
        monkeypatch,
        target_ppfd=100.0,
        fspm_target=180.0,
        fspm_tolerance=20.0,
    )

    runtime = workspace / "runtime_state"
    receiver = _load_json(runtime / "plant_receiver.json")
    surface = _load_json(runtime / "plant_surface_flux.json")
    panel = _load_json(runtime / "fspm_panel_metrics.json")
    scene = _load_json(workspace / "assembly_scene.json")
    leaf_values = surface["visualization"]["leaf_values"]

    assert len(receiver["surface_receivers"]) == 12288
    assert surface["leaf_count"] == 768
    assert surface["surface_count"] == 12288
    assert len(leaf_values) == 768
    assert panel["counts"]["leaf_count"] == 768
    assert panel["counts"]["surface_count"] == 12288
    assert scene["fspm_metrics"]["counts"]["leaf_count"] == 768
    assert scene["plants"]["surface_flux"]["surface_count"] == 12288


def test_cli_playback_fspm_aliases_parse() -> None:
    args = precomputed_playback.parse_args(
        [
            "--mode",
            MODE_SMD,
            "--length-ft",
            "10",
            "--width-ft",
            "10",
            "--target-ppfd",
            "750",
            "--fspm-target-ppfd",
            "275",
            "--fspm-target-tolerance",
            "20",
        ]
    )

    assert args.fspm_target_ppfd_umol_m2_s == pytest.approx(275.0)
    assert args.fspm_target_tolerance_umol_m2_s == pytest.approx(20.0)


def test_smd_lighting_target_changes_plant_ppfd_but_fspm_controls_do_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lower, _ = _materialize_smd_payload(
        tmp_path,
        monkeypatch,
        _plant_req(MODE_SMD, target_ppfd=50.0, fspm_target=15.0, fspm_tolerance=15.0),
    )
    higher, _ = _materialize_smd_payload(
        tmp_path,
        monkeypatch,
        _plant_req(MODE_SMD, target_ppfd=100.0, fspm_target=15.0, fspm_tolerance=15.0),
    )
    changed_fspm, _ = _materialize_smd_payload(
        tmp_path,
        monkeypatch,
        _plant_req(MODE_SMD, target_ppfd=100.0, fspm_target=5.0, fspm_tolerance=5.0),
    )

    assert higher["raw_mean_flux_density_umol_m2_s"] > lower[
        "raw_mean_flux_density_umol_m2_s"
    ]
    assert changed_fspm["raw_mean_flux_density_umol_m2_s"] == pytest.approx(
        higher["raw_mean_flux_density_umol_m2_s"]
    )
    assert higher["target_capped_incident_mean_flux_density_umol_m2_s"] == pytest.approx(15.0)
    assert changed_fspm["target_capped_incident_mean_flux_density_umol_m2_s"] == pytest.approx(5.0)


def test_missing_fspm_target_falls_back_to_lighting_target(tmp_path: Path) -> None:
    req = _plant_req(
        MODE_COMPETITOR,
        target_ppfd=123.0,
        fspm_target=None,
        fspm_tolerance=10.0,
    )
    payload = precomputed_playback._materialize_plant_receiver_surface_flux(
        req,
        tmp_path / "fallback_workspace",
        _receiver_artifacts(
            tmp_path,
            req,
            stored_ppfd=200.0,
        ),
        scale=1.0,
    )

    assert payload is not None
    assert payload["target_ppfd_umol_m2_s"] == pytest.approx(123.0)


def test_runtime_targets_are_not_plant_bundle_identity_fields() -> None:
    first = _plant_req(
        MODE_SMD,
        target_ppfd=100.0,
        fspm_target=180.0,
        fspm_tolerance=20.0,
    )
    second = _plant_req(
        MODE_SMD,
        target_ppfd=200.0,
        fspm_target=120.0,
        fspm_tolerance=5.0,
    )

    assert request_params_for_mode(first) == request_params_for_mode(second)


def test_playback_config_preserves_independent_fspm_runtime_controls() -> None:
    req = _plant_req(
        MODE_SMD,
        target_ppfd=700.0,
        fspm_target=250.0,
        fspm_tolerance=12.0,
    )

    round_trip = precomputed_playback.PrecomputedPlaybackConfig.from_request(
        req
    ).to_request()

    assert round_trip.target_ppfd == pytest.approx(700.0)
    assert round_trip.fspm_target_ppfd_umol_m2_s == pytest.approx(250.0)
    assert round_trip.fspm_target_tolerance_umol_m2_s == pytest.approx(12.0)
    assert round_trip.plants_enabled is True
    assert round_trip.plant_rows == 1
    assert round_trip.plant_columns == 1
