from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from fspm_optics.cli import main
from fspm_optics.radiance.runner import RunnerResult
from fspm_optics.transport.hps_rex_absorption import (
    HPS_ABSORBED_NPZ_ORDER,
    HpsRexAbsorbedMetricsError,
    calculate_hps_rex_absorbed_metrics,
    compute_hps_rex_absorbed_metrics,
    format_hps_absorbed_patch_npz,
    load_hps_rex_incident_workspace,
    read_hps_absorbed_patch_npz,
)
from fspm_optics.transport.hps_rex_execution import (
    HpsRexExecutables,
    HpsRexTransportRequest,
    execute_hps_rex_transport,
)
from tests.support.workspace_templates import clone_authenticated_workspace

RUN_VALUES = {
    "scalar_par": (10.0, 2.0),
    "blue": (1.0, 0.2),
    "green": (2.0, 0.4),
    "orange": (3.0, 0.6),
    "red": (4.0, 0.8),
    "far_red": (0.5, 0.1),
}


def _converted_rad() -> str:
    length = 0.798576
    width = 0.603504
    scale = 313250.0 / (length * width)
    return (
        "void brightdata hps_unit_downward_flux_dist\n"
        "5 flatcorr hps_unit_downward_flux.dat source.cal src_phi src_theta\n"
        "0\n"
        f"1 {scale:.15g}\n\n"
        "hps_unit_downward_flux_dist light hps_unit_downward_flux_light\n"
        "0\n0\n3 1 1 1\n\n"
        "hps_unit_downward_flux_light polygon hps_unit_downward_flux.d\n"
        "0\n0\n12\n"
        f"{-length / 2:.15g} {-width / 2:.15g} -0.00025\n"
        f"{-length / 2:.15g} {width / 2:.15g} -0.00025\n"
        f"{length / 2:.15g} {width / 2:.15g} -0.00025\n"
        f"{length / 2:.15g} {-width / 2:.15g} -0.00025\n"
    )


class FakeHpsRexRunner:
    def run(self, command, *, timeout_s=None, stderr_path=None):
        del timeout_s
        stderr = None if stderr_path is None else Path(stderr_path)
        if stderr is not None:
            stderr.write_text("", encoding="utf-8")
        if command.label == "convert_hps_unit_downward_flux_ies":
            command.cwd.joinpath("hps_unit_downward_flux.rad").write_text(
                _converted_rad(), encoding="ascii"
            )
            command.cwd.joinpath("hps_unit_downward_flux.dat").write_text(
                "2\n0 360 2\n0 90 2\n0.25 0.25 0.25 0.25\n", encoding="ascii"
            )
        elif command.label.startswith("compile_fixture-body-shape-"):
            command.stdout_path.write_bytes(b"fake-fixture-body-octree")
        elif command.label.startswith("compile_hps_rex_"):
            command.stdout_path.write_bytes(b"fake-octree")
        elif command.label.startswith("trace_hps_rex_"):
            interval = command.label.removeprefix("trace_hps_rex_")
            front, back = RUN_VALUES[interval]
            rows = "".join(
                f"{front} {front} {front}\n" if index % 2 == 0 else f"{back} {back} {back}\n"
                for index in range(1024)
            )
            command.stdout_path.write_text(rows, encoding="ascii")
            Path(command.argv[command.argv.index("-af") + 1]).write_bytes(b"fake-cache")
        return RunnerResult(
            command_label=command.label,
            argv=command.argv,
            returncode=0,
            stdout_path=command.stdout_path,
            stderr_text=None,
            stderr_path=stderr,
            wall_time_s=0.0,
            success=True,
        )


def _native_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "native HPS workspace"
    execute_hps_rex_transport(
        HpsRexTransportRequest.from_feet(workspace=workspace),
        FakeHpsRexRunner(),
        executables=HpsRexExecutables(
            tmp_path / "bin/ies2rad",
            tmp_path / "bin/oconv",
            tmp_path / "bin/rtrace",
            "RADIANCE fake",
        ),
    )
    return workspace


@pytest.fixture(scope="module")
def native_workspace_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _native_workspace(tmp_path_factory.mktemp("hps-rex-template"))


@pytest.fixture
def native_workspace(native_workspace_template: Path, tmp_path: Path) -> Path:
    return clone_authenticated_workspace(
        native_workspace_template,
        tmp_path / "native HPS workspace",
        rebase_external_parent=True,
    )


def test_successful_phase25d_load_is_read_only_and_hps_isolated(tmp_path: Path) -> None:
    workspace = _native_workspace(tmp_path)
    root = workspace / "hps_rex_transport"
    before = {
        path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    loaded = load_hps_rex_incident_workspace(workspace)
    after = {
        path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert loaded.plan.bundle.source_payload.spectral_source_id.startswith("hps")
    assert len(loaded.incident_arrays) == 6
    assert all(array.shape == (1024,) for _, array in loaded.incident_arrays)
    assert len(loaded.native_artifact_hashes) == 6


def test_partitions_pair_sides_apply_one_area_and_close(
    native_workspace: Path,
) -> None:
    computed = compute_hps_rex_absorbed_metrics(
        load_hps_rex_incident_workspace(native_workspace)
    )
    arrays = computed.patch_arrays.as_dict()
    assert arrays["front_incident_pfd"].shape == (512, 5)
    assert np.array_equal(
        arrays["combined_incident_pfd"],
        arrays["front_incident_pfd"] + arrays["back_incident_pfd"],
    )
    assert np.allclose(
        arrays["absorbed_flux"],
        arrays["combined_absorbed_pfd"] * arrays["patch_area_m2"][:, None],
    )
    assert np.allclose(
        arrays["absorbed_flux"] + arrays["transmitted_flux"] + arrays["reflected_flux"],
        arrays["incident_flux"],
        rtol=1e-12,
        atol=1e-12,
    )
    assert computed.maximum_local_closure_error_flux <= 1e-12
    assert computed.maximum_local_closure_error_pfd <= 1e-12


def test_patch_leaf_plant_par_far_red_and_scalar_diagnostics(
    native_workspace: Path,
) -> None:
    computed = compute_hps_rex_absorbed_metrics(
        load_hps_rex_incident_workspace(native_workspace)
    )
    plant = computed.spectral.plant
    assert (plant.patch_count, plant.leaf_count, len(computed.spectral.leaves)) == (512, 32, 32)
    assert plant.par.absorbed_flux == pytest.approx(sum(item.absorbed_flux for item in plant.bands[:4]))
    assert plant.far_red == plant.bands[4]
    assert sum(item.patch_count for item in computed.spectral.leaves) == 512
    diagnostic = computed.scalar_diagnostic
    assert diagnostic.reconciliation_scale_applied is False
    assert diagnostic.scientific_pass_threshold_applied is False
    assert diagnostic.relative_rmse_normalization_basis == "RMS of scalar absorbed-PFD reference"


def test_deterministic_npz_idempotence_and_overwrite_refusal(
    native_workspace: Path,
) -> None:
    first = calculate_hps_rex_absorbed_metrics(native_workspace)
    summary_bytes = first.summary_path.read_bytes()
    npz_bytes = first.patch_npz_path.read_bytes()
    second = calculate_hps_rex_absorbed_metrics(native_workspace)
    assert second.summary_id == first.summary_id
    assert second.summary_path.read_bytes() == summary_bytes
    assert second.patch_npz_path.read_bytes() == npz_bytes
    assert tuple(read_hps_absorbed_patch_npz(first.patch_npz_path)) == HPS_ABSORBED_NPZ_ORDER
    assert format_hps_absorbed_patch_npz(first.computed.patch_arrays) == npz_bytes
    assert first.summary["par_band_order"] == ["blue", "green", "orange", "red"]
    assert first.summary["far_red_policy"] == "separate_from_PAR"
    assert first.summary["source_coverage"]["unsupported_scalar_par_umol_s"] > 0.0
    assert first.summary["unsupported_source_mass_redistributed"] is False
    assert first.summary["native_incident_artifacts_modified"] is False
    first.patch_npz_path.write_bytes(b"changed")
    with pytest.raises(HpsRexAbsorbedMetricsError, match="differ"):
        calculate_hps_rex_absorbed_metrics(native_workspace)


@pytest.mark.parametrize("mutation", ("shape", "nan", "negative"))
def test_invalid_final_incident_arrays_fail_before_outputs(
    native_workspace: Path,
    mutation: str,
) -> None:
    root = native_workspace / "hps_rex_transport"
    path = root / "incident_receiver_values.npz"
    with np.load(path, allow_pickle=False) as archive:
        arrays = [(name, np.array(archive[name], copy=True)) for name in archive.files]
    if mutation == "shape":
        arrays[1] = arrays[1][0], arrays[1][1][:-1]
    elif mutation == "nan":
        arrays[1][1][0] = np.nan
    else:
        arrays[1][1][0] = -1.0
    np.savez(path, **dict(arrays))
    summary_path = root / "incident_transport_summary.json"
    summary = json.loads(summary_path.read_text())
    summary["incident_arrays"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    with pytest.raises(HpsRexAbsorbedMetricsError):
        calculate_hps_rex_absorbed_metrics(native_workspace)
    assert not (root / "absorbed").exists()


@pytest.mark.parametrize("target", ("failure", "source", "receiver", "command", "decoded"))
def test_partial_substituted_stale_and_changed_authorities_fail(
    native_workspace: Path,
    target: str,
) -> None:
    root = native_workspace / "hps_rex_transport"
    if target == "failure":
        (root / "failure_summary.json").write_text('{"success": false}\n')
    elif target == "source":
        path = root / "hps_source_payload.json"
        path.write_text(path.read_text().replace("hps", "conventional_led", 1))
    elif target == "receiver":
        path = root / "receivers/rex_receiver_metadata.json"
        payload = json.loads(path.read_text())
        payload["receivers"][1]["area_m2"] *= 2.0
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    elif target == "command":
        path = root / "command_provenance_summary.json"
        payload = json.loads(path.read_text())
        payload["commands"][0]["shell"] = True
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        path = root / "01_blue/incident_values.npy"
        np.save(path, np.zeros(1024, dtype=np.float64), allow_pickle=False)
    with pytest.raises(HpsRexAbsorbedMetricsError):
        load_hps_rex_incident_workspace(native_workspace)
    assert not (root / "absorbed").exists()


@pytest.mark.parametrize("target", ("body_source", "body_octree", "body_marker"))
def test_fixture_body_source_and_compiled_identity_fail_closed(
    native_workspace: Path,
    target: str,
) -> None:
    shape_root = (
        native_workspace
        / "hps_rex_transport/shared/fixture_occlusion/shapes"
    )
    source = next(shape_root.glob("*.rad"))
    octree = next(shape_root.glob("*.oct"))
    marker = octree.with_name(octree.name + ".identity.json")
    if target == "body_source":
        source.write_text(source.read_text() + "# tampered\n", encoding="utf-8")
    elif target == "body_octree":
        octree.write_bytes(b"substituted-fixture-body-octree")
    else:
        payload = json.loads(marker.read_text())
        payload["compiled_octree_sha256"] = "0" * 64
        marker.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    with pytest.raises(HpsRexAbsorbedMetricsError):
        load_hps_rex_incident_workspace(native_workspace)


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_run_artifacts",
        "missing_octree_digest",
        "missing_cache_digest",
        "changed_octree",
        "changed_cache",
        "empty_octree",
        "empty_cache",
        "substituted_octree_digest",
        "substituted_cache_digest",
    ),
)
def test_opaque_native_artifact_digest_closure_is_required(
    native_workspace: Path,
    mutation: str,
) -> None:
    root = native_workspace / "hps_rex_transport"
    summary_path = root / "incident_transport_summary.json"
    summary = json.loads(summary_path.read_text())
    record = summary["run_artifacts"][1]
    octree = root / "01_blue/fspm_scene.oct"
    cache = root / "01_blue/fspm_scene.amb"
    if mutation == "missing_run_artifacts":
        del summary["run_artifacts"]
    elif mutation == "missing_octree_digest":
        del record["octree_sha256"]
    elif mutation == "missing_cache_digest":
        del record["ambient_cache_sha256"]
    elif mutation == "changed_octree":
        octree.write_bytes(b"substituted-octree")
    elif mutation == "changed_cache":
        cache.write_bytes(b"substituted-cache")
    elif mutation == "empty_octree":
        octree.write_bytes(b"")
    elif mutation == "empty_cache":
        cache.write_bytes(b"")
    elif mutation == "substituted_octree_digest":
        record["octree_sha256"] = "0" * 64
    else:
        record["ambient_cache_sha256"] = "0" * 64
    if "digest" in mutation or mutation == "missing_run_artifacts":
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    with pytest.raises(HpsRexAbsorbedMetricsError):
        load_hps_rex_incident_workspace(native_workspace)
    assert not (root / "absorbed").exists()


def test_partial_or_unknown_absorbed_directory_fails_closed(
    native_workspace: Path,
) -> None:
    absorbed = native_workspace / "hps_rex_transport/absorbed"
    absorbed.mkdir()
    stale = absorbed / "absorbed_patch_metrics.npz"
    stale.write_bytes(b"stale")
    with pytest.raises(HpsRexAbsorbedMetricsError, match="partial"):
        calculate_hps_rex_absorbed_metrics(native_workspace)
    assert stale.read_bytes() == b"stale"


def test_cli_routes_without_native_discovery_or_execution(
    native_workspace: Path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "fspm_optics.transport.hps_rex_execution.resolve_hps_rex_executables",
        lambda: pytest.fail("native discovery must not run"),
    )
    code = main(
        ["hps-rex-absorbed-metrics", "--workspace", str(native_workspace)]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "hps-rex-absorbed-metrics"
    assert payload["success"] is True


def test_hps_absorption_import_boundary_is_pure() -> None:
    path = Path(__file__).parents[1] / "src/fspm_optics/transport/hps_rex_absorption.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "subprocess" not in imported
    assert "LocalRunner" not in source
    assert "execute_hps_rex_transport" not in source
    assert "resolve_hps_rex_executables" not in source
    assert "shell=True" not in source
