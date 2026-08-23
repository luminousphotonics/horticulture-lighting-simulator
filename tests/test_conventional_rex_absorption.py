from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from fspm_optics.cli import main
from fspm_optics.optics.rex_weighting import AtrCoefficients
from fspm_optics.radiance.runner import RunnerResult
from fspm_optics.transport.conventional_rex_absorption import (
    CONVENTIONAL_ABSORBED_NPZ_ORDER,
    ConventionalRexAbsorbedMetricsError,
    calculate_conventional_rex_absorbed_metrics,
    compute_conventional_rex_absorbed_metrics,
    format_conventional_absorbed_patch_npz,
    load_conventional_rex_incident_workspace,
    read_conventional_absorbed_patch_npz,
)
from fspm_optics.transport.conventional_rex_execution import (
    CONVENTIONAL_REX_INCIDENT_ARRAY_ORDER,
    ConventionalRexExecutables,
    ConventionalRexTransportRequest,
    execute_conventional_rex_transport,
)
from fspm_optics.transport.five_band import FIVE_BAND_ORDER
from fspm_optics.transport.five_band_absorption import (
    ABSORBED_SUMMARY_PAYLOAD_TYPE,
    ABSORBED_SUMMARY_SCHEMA_VERSION,
    SourceNeutralAbsorbedMetricsInput,
    adapt_rex_five_band_absorption_input,
    compute_five_band_absorbed_metrics,
)
from tests.support.workspace_templates import clone_authenticated_workspace


def _converted_rad() -> str:
    lx, wy, hz = 1.190 / 2.0, 1.087 / 2.0, 0.108 / 2.0
    corners = {
        0: (-lx, -wy, -hz),
        1: (lx, -wy, -hz),
        2: (-lx, wy, -hz),
        3: (lx, wy, -hz),
        4: (-lx, -wy, hz),
        5: (lx, -wy, hz),
        6: (-lx, wy, hz),
        7: (lx, wy, hz),
    }
    faces = (
        (".d", (0, 2, 3, 1)),
        (".u", (4, 5, 7, 6)),
        (".1", (0, 1, 5, 4)),
        (".2", (1, 3, 7, 5)),
        (".3", (3, 2, 6, 7)),
        (".4", (2, 0, 4, 6)),
    )
    sections = [
        "void brightdata conventional_led_unit_downward_flux_dist\n"
        "5 boxcorr conventional_led_unit_downward_flux.dat source.cal src_phi src_theta\n"
        "0\n4 307164 1.190 1.087 0.108\n",
        "conventional_led_unit_downward_flux_dist light "
        "conventional_led_unit_downward_flux_light\n0\n0\n3 1 1 1\n",
    ]
    for suffix, indices in faces:
        values = " ".join(str(value) for index in indices for value in corners[index])
        sections.append(
            "conventional_led_unit_downward_flux_light polygon "
            f"conventional_led_unit_downward_flux{suffix}\n0\n0\n12 {values}\n"
        )
    return "\n".join(sections)


RUN_VALUES = {
    "scalar_par": (10.0, 2.0),
    "blue": (1.0, 0.2),
    "green": (2.0, 0.4),
    "orange": (3.0, 0.6),
    "red": (4.0, 0.8),
    "far_red": (5.0, 1.0),
}


class FakeNativeRunner:
    def run(self, command, *, timeout_s=None, stderr_path=None):
        del timeout_s
        stderr = Path(stderr_path)
        stderr.write_text("", encoding="utf-8")
        if command.label == "convert_conventional_unit_downward_flux_ies":
            command.cwd.joinpath("conventional_led_unit_downward_flux.rad").write_text(
                _converted_rad(), encoding="ascii"
            )
            command.cwd.joinpath("conventional_led_unit_downward_flux.dat").write_text(
                "2\n0 360 2\n0 180 2\n0.25 0.25 0.25 0.25\n",
                encoding="ascii",
            )
        elif command.label.startswith("compile_fixture-body-shape-"):
            command.stdout_path.write_bytes(b"fake-fixture-body-octree")
        elif command.label.startswith("compile_conventional_rex_"):
            command.stdout_path.write_bytes(b"fake-octree")
        elif command.label.startswith("trace_conventional_rex_"):
            interval = command.label.removeprefix("trace_conventional_rex_")
            front, back = RUN_VALUES[interval]
            rows = "".join(
                f"{front} {front} {front}\n"
                if index % 2 == 0
                else f"{back} {back} {back}\n"
                for index in range(1024)
            )
            command.stdout_path.write_text(rows, encoding="utf-8")
            Path(command.argv[command.argv.index("-af") + 1]).write_bytes(b"cache")
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


def _executables(tmp_path: Path) -> ConventionalRexExecutables:
    return ConventionalRexExecutables(
        tmp_path / "bin" / "ies2rad",
        tmp_path / "bin" / "oconv",
        tmp_path / "bin" / "rtrace",
        "RADIANCE fake",
    )


def _native_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "native workspace"
    execute_conventional_rex_transport(
        ConventionalRexTransportRequest.from_feet(workspace=workspace),
        FakeNativeRunner(),
        executables=_executables(tmp_path),
    )
    return workspace


@pytest.fixture(scope="module")
def native_workspace_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _native_workspace(tmp_path_factory.mktemp("conventional-rex-template"))


@pytest.fixture
def native_workspace(native_workspace_template: Path, tmp_path: Path) -> Path:
    return clone_authenticated_workspace(
        native_workspace_template,
        tmp_path / "native workspace",
        rebase_external_parent=True,
    )


def test_successful_workspace_load_is_strict_and_read_only(tmp_path: Path) -> None:
    workspace = _native_workspace(tmp_path)
    native_root = workspace / "conventional_rex_transport"
    before = {
        path.relative_to(native_root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in native_root.rglob("*")
        if path.is_file()
    }
    loaded = load_conventional_rex_incident_workspace(workspace)
    after = {
        path.relative_to(native_root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in native_root.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert loaded.plan.bundle.source_payload.spectral_source_id.startswith("conventional")
    assert tuple(name for name, _ in loaded.incident_arrays) == (
        CONVENTIONAL_REX_INCIDENT_ARRAY_ORDER
    )
    assert all(array.shape == (1024,) for _, array in loaded.incident_arrays)
    assert len(loaded.native_artifact_hashes) == 6


def test_patch_partitions_use_one_area_and_close_locally(
    native_workspace: Path,
) -> None:
    loaded = load_conventional_rex_incident_workspace(native_workspace)
    computed = compute_conventional_rex_absorbed_metrics(loaded)
    arrays = computed.patch_arrays.as_dict()
    assert arrays["patch_area_m2"].shape == (512,)
    assert arrays["front_incident_pfd"].shape == (512, 5)
    assert np.allclose(
        arrays["combined_incident_pfd"],
        arrays["front_incident_pfd"] + arrays["back_incident_pfd"],
    )
    assert np.allclose(
        arrays["absorbed_flux"],
        arrays["combined_absorbed_pfd"] * arrays["patch_area_m2"][:, None],
    )
    assert np.allclose(
        arrays["absorbed_flux"]
        + arrays["transmitted_flux"]
        + arrays["reflected_flux"],
        arrays["incident_flux"],
        rtol=1e-12,
        atol=1e-12,
    )
    assert computed.maximum_local_closure_error_flux <= 1e-12
    assert computed.maximum_local_closure_error_pfd <= 1e-12


def test_par_far_red_scalar_leaf_and_plant_aggregation(
    native_workspace: Path,
) -> None:
    loaded = load_conventional_rex_incident_workspace(native_workspace)
    computed = compute_conventional_rex_absorbed_metrics(loaded)
    plant = computed.spectral.plant
    assert plant.patch_count == 512
    assert plant.leaf_count == len(computed.spectral.leaves) == 32
    assert plant.par.incident_flux == pytest.approx(
        sum(item.incident_flux for item in plant.bands[:4])
    )
    assert plant.par.absorbed_flux == pytest.approx(
        sum(item.absorbed_flux for item in plant.bands[:4])
    )
    assert plant.far_red == plant.bands[4]
    assert plant.far_red.incident_flux not in (
        plant.par.incident_flux,
        plant.par.absorbed_flux,
    )
    assert sum(item.patch_count for item in computed.spectral.leaves) == 512
    assert computed.scalar_diagnostic.reconciliation_scale_applied is False
    assert computed.scalar_diagnostic.scientific_pass_threshold_applied is False
    assert computed.scalar_diagnostic.relative_rmse_normalization_basis == (
        "RMS of scalar absorbed-PFD reference"
    )


def test_deterministic_absorbed_summary_npz_and_idempotent_existing_outputs(
    native_workspace: Path,
) -> None:
    first = calculate_conventional_rex_absorbed_metrics(native_workspace)
    summary_bytes = first.summary_path.read_bytes()
    npz_bytes = first.patch_npz_path.read_bytes()
    second = calculate_conventional_rex_absorbed_metrics(native_workspace)
    assert second.summary_id == first.summary_id
    assert second.summary_path.read_bytes() == summary_bytes
    assert second.patch_npz_path.read_bytes() == npz_bytes
    arrays = read_conventional_absorbed_patch_npz(first.patch_npz_path)
    assert tuple(arrays) == CONVENTIONAL_ABSORBED_NPZ_ORDER
    assert format_conventional_absorbed_patch_npz(first.computed.patch_arrays) == npz_bytes
    assert first.summary["par_band_order"] == ["blue", "green", "orange", "red"]
    assert first.summary["far_red_policy"] == "separate_from_PAR"
    assert first.summary["native_incident_artifacts_modified"] is False


def test_partial_failure_and_identity_hash_substitution_are_rejected(
    native_workspace: Path,
) -> None:
    root = native_workspace / "conventional_rex_transport"
    (root / "failure_summary.json").write_text(
        json.dumps({"success": False, "error": "partial"}) + "\n"
    )
    with pytest.raises(ConventionalRexAbsorbedMetricsError, match="incomplete"):
        load_conventional_rex_incident_workspace(native_workspace)
    (root / "failure_summary.json").unlink()

    source_payload = root / "conventional_source_payload.json"
    source_payload.write_text(
        source_payload.read_text().replace(
            "conventional_led_relative_spd_v2",
            "proposed_led_smd_nominal_source_v1",
        )
    )
    with pytest.raises(ConventionalRexAbsorbedMetricsError, match="typed plan"):
        load_conventional_rex_incident_workspace(native_workspace)


@pytest.mark.parametrize("mutation", ("shape", "nan", "negative"))
def test_incident_array_shape_finite_and_nonnegative_validation(
    native_workspace: Path,
    mutation: str,
) -> None:
    root = native_workspace / "conventional_rex_transport"
    npz_path = root / "incident_receiver_values.npz"
    with np.load(npz_path, allow_pickle=False) as archive:
        arrays = [(name, np.array(archive[name], copy=True)) for name in archive.files]
    if mutation == "shape":
        arrays[1] = (arrays[1][0], arrays[1][1][:-1])
    elif mutation == "nan":
        arrays[1][1][0] = np.nan
    else:
        arrays[1][1][0] = -1.0
    np.savez(npz_path, **dict(arrays))
    bytes_value = npz_path.read_bytes()
    summary_path = root / "incident_transport_summary.json"
    summary = json.loads(summary_path.read_text())
    summary["incident_arrays"]["sha256"] = hashlib.sha256(bytes_value).hexdigest()
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    match = "shape|vector" if mutation == "shape" else "finite|non-negative"
    with pytest.raises((ConventionalRexAbsorbedMetricsError, ValueError), match=match):
        load_conventional_rex_incident_workspace(native_workspace)


def test_receiver_metadata_pairing_and_area_mismatch_fail(
    native_workspace: Path,
) -> None:
    root = native_workspace / "conventional_rex_transport"
    metadata_path = root / "receivers" / "rex_receiver_metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["receivers"][1]["area_m2"] *= 2.0
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    with pytest.raises(ConventionalRexAbsorbedMetricsError, match="typed plan"):
        load_conventional_rex_incident_workspace(native_workspace)


def test_existing_inconsistent_absorbed_artifacts_are_not_overwritten(
    native_workspace: Path,
) -> None:
    absorbed = native_workspace / "conventional_rex_transport" / "absorbed"
    absorbed.mkdir()
    stale = absorbed / "absorbed_patch_metrics.npz"
    stale.write_bytes(b"stale")
    before = stale.read_bytes()
    with pytest.raises(ConventionalRexAbsorbedMetricsError, match="stale or incomplete"):
        calculate_conventional_rex_absorbed_metrics(native_workspace)
    assert stale.read_bytes() == before


def test_smd_phase20_adapter_and_schema_remain_unchanged(monkeypatch) -> None:
    coefficients = AtrCoefficients(0.7, 0.2, 0.1)
    bands = tuple(
        SimpleNamespace(
            band_id=band_id,
            material=SimpleNamespace(coefficients=coefficients),
        )
        for band_id in FIVE_BAND_ORDER
    )
    plan = SimpleNamespace(
        source_model_id="proposed_led_smd_nominal_source_v1",
        material_policy_id="diffuse_only_symmetric_thin_leaf_energy_partition",
        material_plan_json_sha256="a" * 64,
        plant_id="plant",
        receiver_text_sha256="b" * 64,
        patch_count=512,
        receiver_count=1024,
        ordered_receiver_ids=tuple(f"receiver_{index}" for index in range(1024)),
        band_plans=bands,
    )
    adapted = adapt_rex_five_band_absorption_input(plan)
    assert isinstance(adapted, SourceNeutralAbsorbedMetricsInput)
    assert adapted.source_family == "smd"
    assert tuple(name for name, _ in adapted.band_coefficients) == FIVE_BAND_ORDER
    assert ABSORBED_SUMMARY_SCHEMA_VERSION == 1
    assert ABSORBED_SUMMARY_PAYLOAD_TYPE == "fspm_optics_rex_five_band_absorbed_metrics"

    sentinel = object()
    monkeypatch.setattr(
        "fspm_optics.transport.five_band_absorption.compute_source_neutral_absorbed_metrics",
        lambda source, samples, arrays: sentinel,
    )
    assert compute_five_band_absorbed_metrics(plan, (), {}) is sentinel


def test_cli_success_failure_and_no_native_execution(
    native_workspace: Path,
    tmp_path: Path,
    capsys,
) -> None:
    code = main(
        ["conventional-rex-absorbed-metrics", "--workspace", str(native_workspace)]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "conventional-rex-absorbed-metrics"
    assert payload["success"] is True
    assert "absorbed_par_photon_flux_umol_s" in payload
    assert "absorbed_far_red_photon_flux_umol_s" in payload

    code = main(
        [
            "conventional-rex-absorbed-metrics",
            "--workspace",
            str(tmp_path / "missing"),
        ]
    )
    assert code == 1
    assert json.loads(capsys.readouterr().err)["success"] is False


def test_absorption_import_boundary_has_no_radiance_execution_or_legacy_access() -> None:
    path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "transport"
        / "conventional_rex_absorption.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "subprocess" not in imported_names
    assert "LocalRunner" not in source
    assert "execute_conventional_rex_transport" not in source
    assert "shell=True" not in source
    assert ".salvage_source" not in source
