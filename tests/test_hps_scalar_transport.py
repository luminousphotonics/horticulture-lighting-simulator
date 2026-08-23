from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from fspm_optics.application.multispectral import (
    build_hps_juvenile_source_adapter,
)
from fspm_optics.application.source_state import PhysicalSourceState
from fspm_optics.cli import main
from fspm_optics.fixtures.hps import (
    HPS_RADIANCE_CARRIER_MULTIPLIER,
    HpsSourcePlanError,
    build_hps_radiance_source_plan,
    validate_converted_hps_ies_output,
)
from fspm_optics.geometry.active_domain import ActiveRoomDomain
from fspm_optics.radiance.runner import RunnerResult
from fspm_optics.transport.hps_scalar import (
    HpsScalarExecutables,
    HpsScalarTransportError,
    HpsScalarTransportRequest,
    compute_hps_scalar_metrics,
    decode_hps_scalar_rgb_rows,
    execute_hps_scalar_transport,
    format_hps_ppfd_values_npz,
    plan_hps_scalar_transport,
    plan_hps_scalar_workspace,
    resolve_hps_scalar_executables,
    validate_hps_converted_dat,
)


def _converted_rad() -> str:
    length = 0.798576
    width = 0.603504
    half_x = length / 2.0
    half_y = width / 2.0
    scale = HPS_RADIANCE_CARRIER_MULTIPLIER / (length * width)
    return (
        "void brightdata hps_unit_downward_flux_dist\n"
        "5 flatcorr hps_unit_downward_flux.dat source.cal src_phi src_theta\n"
        "0\n"
        f"1 {scale:.15g}\n\n"
        "hps_unit_downward_flux_dist light hps_unit_downward_flux_light\n"
        "0\n0\n3 1 1 1\n\n"
        "hps_unit_downward_flux_light polygon hps_unit_downward_flux.d\n"
        "0\n0\n12\n"
        f"{-half_x:.15g} {-half_y:.15g} -0.00025\n"
        f"{-half_x:.15g} {half_y:.15g} -0.00025\n"
        f"{half_x:.15g} {half_y:.15g} -0.00025\n"
        f"{half_x:.15g} {-half_y:.15g} -0.00025\n"
    )


def _converted_dat() -> str:
    return "2\n0 360 2\n0 90 2\n0.25 0.25 0.25 0.25\n"


class FakeHpsRunner:
    def __init__(
        self,
        *,
        converted_rad: str | None = None,
        omit_rad: bool = False,
        omit_dat: bool = False,
        invalid_rgb: bool = False,
        rgb_row: str | None = None,
        receiver_count_delta: int = 0,
        change_dat_after_oconv: bool = False,
        remove_dat_after_oconv: bool = False,
        duplicate_dat_reference: bool = False,
    ) -> None:
        self.commands = []
        self.converted_rad = _converted_rad() if converted_rad is None else converted_rad
        self.omit_rad = omit_rad
        self.omit_dat = omit_dat
        self.invalid_rgb = invalid_rgb
        self.rgb_row = rgb_row
        self.receiver_count_delta = receiver_count_delta
        self.change_dat_after_oconv = change_dat_after_oconv
        self.remove_dat_after_oconv = remove_dat_after_oconv
        self.duplicate_dat_reference = duplicate_dat_reference

    def run(self, command, *, timeout_s=None, stderr_path=None):
        del timeout_s
        self.commands.append(command)
        stderr = None if stderr_path is None else Path(stderr_path)
        if stderr is not None:
            stderr.write_text("", encoding="utf-8")
        if command.label == "convert_hps_unit_downward_flux_ies":
            assert command.cwd is not None
            text = self.converted_rad
            if self.duplicate_dat_reference:
                text += (
                    "\nvoid brightdata duplicate\n"
                    "5 flatcorr hps_unit_downward_flux.dat source.cal src_phi src_theta\n"
                    "0\n1 1\n"
                )
            if not self.omit_rad:
                (command.cwd / "hps_unit_downward_flux.rad").write_text(
                    text, encoding="ascii"
                )
            if not self.omit_dat:
                (command.cwd / "hps_unit_downward_flux.dat").write_text(
                    _converted_dat(), encoding="ascii"
                )
        elif command.label.startswith("compile_fixture-body-shape-"):
            assert command.stdout_path is not None
            command.stdout_path.write_bytes(b"fake-fixture-body-octree")
        elif command.label == "compile_hps_scalar_scene":
            assert command.stdout_path is not None
            command.stdout_path.write_bytes(b"fake-octree")
            dat = command.cwd / "source" / "hps_unit_downward_flux.dat"
            if self.remove_dat_after_oconv:
                dat.unlink()
            elif self.change_dat_after_oconv:
                dat.write_text(_converted_dat() + "\n", encoding="ascii")
        elif command.label == "baseline_scalar_ppfd_rtrace":
            assert command.stdin_path is not None
            assert command.stdout_path is not None
            count = (
                len(command.stdin_path.read_text(encoding="utf-8").splitlines())
                + self.receiver_count_delta
            )
            row = self.rgb_row or (
                "12 12 13\n" if self.invalid_rgb else "12 12 12\n"
            )
            command.stdout_path.write_text(row * count, encoding="utf-8")
            cache = Path(command.argv[command.argv.index("-af") + 1])
            cache.write_bytes(b"fake-ambient-cache")
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


def _executables(tmp_path: Path) -> HpsScalarExecutables:
    return HpsScalarExecutables(
        tmp_path / "bin" / "ies2rad",
        tmp_path / "bin" / "oconv",
        tmp_path / "bin" / "rtrace",
        "RADIANCE fake",
    )


def _request(tmp_path: Path, **kwargs) -> HpsScalarTransportRequest:
    return HpsScalarTransportRequest.from_feet(
        workspace=tmp_path / "runtime", **kwargs
    )


def test_default_plan_closes_layout_source_grid_commands_and_scene(tmp_path: Path) -> None:
    plan = plan_hps_scalar_transport(
        _request(tmp_path), executables=_executables(tmp_path)
    )
    assert (plan.layout.counts.columns_x, plan.layout.counts.rows_y) == (2, 2)
    assert len(plan.layout.fixtures) == len(plan.source.apertures) == 4
    assert plan.source.carrier_scale.fixture_ppf_umol_s == 1750.0
    assert plan.source.carrier_scale.ies2rad_multiplier == 313250.0
    assert plan.source.whole_plan_downward_ppf_umol_s == 7000.0
    assert len(plan.sensor_points) == 441
    assert plan.layout.mount.aperture_plane_z_m == pytest.approx(0.6146)
    assert (
        plan.layout.mount.aperture_plane_z_m - plan.request.reference_plane_z_m
    ) == pytest.approx(0.6096)
    assert plan.scene.scene.source_files == plan.paths.final_scene_inputs
    assert plan.paths.raw_converted_rad not in plan.scene.scene.source_files
    assert plan.ies2rad_command.argv[1:] == (
        "-dm", "-t", "default", "-c", "1", "1", "1", "-m", "313250",
        "-o", "hps_unit_downward_flux", "hps_unit_downward_flux.ies",
    )
    assert plan.ies2rad_command.cwd == plan.paths.source_directory
    assert plan.oconv_command.cwd == plan.rtrace_command.cwd == plan.paths.artifact_root
    assert plan.oconv_command.stdout_mode == "binary"
    assert plan.rtrace_command.stdin_path == plan.paths.sensor_rays
    assert plan.rtrace_command.stdout_path == plan.paths.raw_rtrace
    assert plan.paths.converted_dat_execution_reference == (
        "source/hps_unit_downward_flux.dat"
    )
    assert all(command.env == {} for command in (
        plan.ies2rad_command, plan.oconv_command, plan.rtrace_command
    ))


@pytest.mark.parametrize("aisle_enabled", [False, True])
def test_explicit_36_inch_stage_a_plans_with_and_without_aisle(
    tmp_path: Path,
    aisle_enabled: bool,
) -> None:
    domain = ActiveRoomDomain.from_feet(
        10.0,
        10.0,
        enabled=aisle_enabled,
    )
    plan = plan_hps_scalar_transport(
        _request(
            tmp_path / ("aisle" if aisle_enabled else "outer"),
            mount_height_m=0.9144,
            active_domain=domain,
        ),
        executables=_executables(tmp_path),
    )

    assert plan.layout.mount.mount_height_m == 0.9144
    assert plan.layout.mount.aperture_plane_z_m == pytest.approx(0.9194)
    assert plan.fixture_occlusion.asset_provenance[0][
        "placement_contract_sha256"
    ] == "d83577f6bb7503ff8865dbe55122bfd37d5ea0e0f2c1d9dc130a7a98f9598293"
    assert len(plan.fixture_occlusion.instances) == len(plan.layout.fixtures)


def test_workspace_is_absolute_external_and_run_root_must_be_empty(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        HpsScalarTransportRequest.from_feet(workspace=Path("relative"))
    with pytest.raises(ValueError, match="outside the repository"):
        HpsScalarTransportRequest.from_feet(
            workspace=Path(__file__).parents[1] / "runtime"
        )
    request = _request(tmp_path)
    paths = plan_hps_scalar_workspace(request)
    paths.artifact_root.mkdir(parents=True)
    (paths.artifact_root / "stale").write_text("x", encoding="ascii")
    with pytest.raises(HpsScalarTransportError, match="absent or empty"):
        execute_hps_scalar_transport(
            request, FakeHpsRunner(), executables=_executables(tmp_path)
        )


def test_native_flatcorr_contract_rejects_substitution_and_extra_geometry() -> None:
    contract = validate_converted_hps_ies_output(_converted_rad())
    assert contract.cal_reference == "source.cal"
    assert contract.neutral_rgb == (1.0, 1.0, 1.0)
    assert contract.geometry_ids == ("hps_unit_downward_flux.d",)
    assert contract.native_polygon_z_m == -0.00025
    assert contract.aperture_brightdata_scale == pytest.approx(
        313250.0 / (0.798576 * 0.603504)
    )
    replacements = (
        ("flatcorr", "boxcorr"),
        ("source.cal", "substitute.cal"),
        ("1 1 1", "1 0.9 1"),
        ("hps_unit_downward_flux.d", "hps_unit_downward_flux.u"),
        ("-0.00025", "0", 1),
    )
    for replacement in replacements:
        old, new, *count = replacement
        replacement_count = count[0] if count else -1
        with pytest.raises(HpsSourcePlanError):
            validate_converted_hps_ies_output(
                _converted_rad().replace(old, new, replacement_count)
            )
    scale = HPS_RADIANCE_CARRIER_MULTIPLIER / (0.798576 * 0.603504)
    with pytest.raises(HpsSourcePlanError, match="carrier"):
        validate_converted_hps_ies_output(
            _converted_rad().replace(f"1 {scale:.15g}", "1 1", 1)
        )
    with pytest.raises(HpsSourcePlanError, match="one brightdata"):
        validate_converted_hps_ies_output(
            _converted_rad()
            + "\nhps_unit_downward_flux_light polygon unexpected\n"
            "0\n0\n12 0 0 0 0 0 0 0 0 0 0 0 0\n"
        )


def test_fake_execution_compiles_body_then_runs_three_scientific_commands(
    tmp_path: Path,
) -> None:
    runner = FakeHpsRunner()
    result = execute_hps_scalar_transport(
        _request(tmp_path), runner, executables=_executables(tmp_path)
    )
    assert [item.label for item in runner.commands] == [
        "convert_hps_unit_downward_flux_ies",
        result.plan.fixture_occlusion.shapes[0].compile_command.label,
        "compile_hps_scalar_scene",
        "baseline_scalar_ppfd_rtrace",
    ]
    assert result.command_count == 3
    assert result.metrics.sensor_count == 441
    assert result.metrics.mean_ppfd_umol_m2_s == 12.0
    assert result.metrics.minimum_ppfd_umol_m2_s == 12.0
    assert result.metrics.maximum_ppfd_umol_m2_s == 12.0
    assert result.metrics.minimum_to_mean_uniformity == 1.0
    assert all(item.cwd is not None for item in runner.commands)
    assert runner.commands[0].stdin_path is runner.commands[0].stdout_path is None
    assert runner.commands[1].stdout_path == (
        result.plan.fixture_occlusion.shapes[0].octree_path
    )
    assert runner.commands[2].stdout_path == result.plan.paths.octree
    assert runner.commands[3].stdin_path == result.plan.paths.sensor_rays
    assert runner.commands[3].stdout_path == result.plan.paths.raw_rtrace
    with np.load(result.plan.paths.ppfd_npz, allow_pickle=False) as archive:
        assert tuple(archive.files) == ("x_m", "y_m", "z_m", "ppfd_umol_m2_s")
        assert archive["ppfd_umol_m2_s"].shape == (441,)
        assert np.all(archive["ppfd_umol_m2_s"] == 12.0)
    source = json.loads(result.plan.paths.source_conversion_summary.read_text())
    assert source["fixture_count"] == source["aperture_count"] == 4
    assert source["native_contract"]["canonical_cal_reference"] == "source.cal"
    assert source["native_contract"]["raw_converted_geometry_enters_final_scene"] is False
    assert source["native_contract"]["shared_source_definition_reused_by_all_apertures"] is True
    assert source["fixture_occlusion"]["hps_included"] is True
    summary = json.loads(result.plan.paths.scalar_transport_summary.read_text())
    assert summary["operating_point"]["total_tested_system_input_power_w"] == 4180.0
    assert summary["operating_point"]["total_planned_ppf_umol_s"] == 7000.0
    assert summary["operating_point"]["carrier_applied_exactly_once_before_trace"] is True
    assert result.plan.paths.aperture_rad.read_text().count(" polygon ") == 4
    assert result.plan.paths.raw_converted_rad not in result.plan.scene.scene.source_files
    scene = json.loads(result.plan.paths.scene_manifest.read_text())
    assert [item["role"] for item in scene["ordered_inputs"]] == [
        "room", "shared_angular_source", "fixture_apertures", "fixture_bodies"
    ]
    assert scene["fixture_occlusion_identity"] == (
        result.plan.fixture_occlusion.identity_sha256
    )
    provenance = json.loads(result.plan.paths.command_provenance_summary.read_text())
    assert [item["label"] for item in provenance["commands"]] == [
        "convert_hps_unit_downward_flux_ies",
        "compile_hps_scalar_scene",
        "baseline_scalar_ppfd_rtrace",
    ]
    assert all(item["shell"] is False for item in provenance["commands"])


def test_stage_b_adapter_reuses_scalar_source_and_authenticated_body(
    tmp_path: Path,
) -> None:
    result = execute_hps_scalar_transport(
        _request(tmp_path), FakeHpsRunner(), executables=_executables(tmp_path)
    )
    state = PhysicalSourceState.create(
        system_id="hps",
        layout_identity=result.plan.layout.to_payload(),
        full_output_schedule={"operation": "fixed_full_output"},
        operating_point={"fixture_ppf_umol_s": 1750.0},
        source_operation={
            "source_plan_id": result.plan.source.source_plan_id,
            "fixture_occlusion_identity": (
                result.plan.fixture_occlusion.identity_sha256
            ),
        },
    )
    adapter = build_hps_juvenile_source_adapter(
        root=result.plan.request.workspace,
        result=result,
        source_state=state,
    )

    assert adapter.system_id == "hps"
    assert adapter.source_state_id == state.source_state_id
    assert adapter.fixture_body_source_path == (
        result.plan.fixture_occlusion.instance_source_path
    )
    assert adapter.fixture_occlusion == (
        result.plan.fixture_occlusion.scientific_payload()
    )
    assert adapter.source_policy["source_plan_id"] == (
        result.plan.source.source_plan_id
    )
    assert adapter.source_policy["angular_dat_sha256"] == hashlib.sha256(
        result.plan.paths.converted_dat.read_bytes()
    ).hexdigest()


def test_relative_dat_spaces_hash_revalidation_and_source_failures(tmp_path: Path) -> None:
    spaced = _request(tmp_path / "workspace with spaces")
    result = execute_hps_scalar_transport(
        spaced, FakeHpsRunner(), executables=_executables(tmp_path)
    )
    assert " " in str(result.plan.paths.artifact_root)
    shared = result.plan.paths.shared_angular_rad.read_text()
    assert shared.split().count("source/hps_unit_downward_flux.dat") == 1
    assert "source.cal" in shared
    assert list(result.plan.paths.artifact_root.rglob("*.dat")) == [
        result.plan.paths.converted_dat
    ]

    cases = (
        ("missing_rad", FakeHpsRunner(omit_rad=True), "output set mismatch", 1),
        ("missing", FakeHpsRunner(omit_dat=True), "output set mismatch", 1),
        ("changed", FakeHpsRunner(change_dat_after_oconv=True), "hash changed", 3),
        ("removed", FakeHpsRunner(remove_dat_after_oconv=True), "missing or empty", 3),
        ("duplicated", FakeHpsRunner(duplicate_dat_reference=True), "invalid native", 1),
        (
            "substituted",
            FakeHpsRunner(converted_rad=_converted_rad().replace("source.cal", "other.cal")),
            "invalid native",
            1,
        ),
        (
            "substituted_dat_reference",
            FakeHpsRunner(
                converted_rad=_converted_rad().replace(
                    "hps_unit_downward_flux.dat", "substitute.dat"
                )
            ),
            "invalid native",
            1,
        ),
    )
    for name, runner, message, command_count in cases:
        request = _request(tmp_path / name)
        with pytest.raises(HpsScalarTransportError, match=message):
            execute_hps_scalar_transport(
                request, runner, executables=_executables(tmp_path)
            )
        assert len(runner.commands) == command_count
        paths = plan_hps_scalar_workspace(request)
        assert paths.failure_summary.is_file()
        assert not paths.source_conversion_summary.exists()
        assert not paths.scalar_transport_summary.exists()
        assert not paths.command_provenance_summary.exists()
        assert not paths.ppfd_npz.exists()


def test_rgb_dat_npz_and_identity_contracts_are_strict_and_deterministic(tmp_path: Path) -> None:
    metadata = validate_hps_converted_dat(_converted_dat().encode("ascii"))
    assert metadata["dimension_count"] == 2
    assert decode_hps_scalar_rgb_rows("1 1 1\n2 2 2\n", expected_count=2) == (
        1.0,
        2.0,
    )
    with pytest.raises(HpsScalarTransportError, match="value count mismatch"):
        validate_hps_converted_dat(b"2\n0 360 2\n0 90 2\n1 1 1\n")
    invalid = _request(tmp_path / "invalid")
    with pytest.raises(HpsScalarTransportError, match="grey scalar channels"):
        execute_hps_scalar_transport(
            invalid, FakeHpsRunner(invalid_rgb=True), executables=_executables(tmp_path)
        )
    for name, runner, message in (
        ("negative", FakeHpsRunner(rgb_row="-1 -1 -1\n"), "non-negative"),
        ("nonfinite", FakeHpsRunner(rgb_row="nan nan nan\n"), "finite"),
        ("row_count", FakeHpsRunner(receiver_count_delta=-1), "receiver count mismatch"),
    ):
        with pytest.raises(HpsScalarTransportError, match=message):
            execute_hps_scalar_transport(
                _request(tmp_path / name), runner, executables=_executables(tmp_path)
            )
    first = execute_hps_scalar_transport(
        _request(tmp_path / "first"), FakeHpsRunner(), executables=_executables(tmp_path)
    )
    second = execute_hps_scalar_transport(
        _request(tmp_path / "second"), FakeHpsRunner(), executables=_executables(tmp_path)
    )
    assert format_hps_ppfd_values_npz(
        first.samples
    ) == format_hps_ppfd_values_npz(first.samples)
    assert hashlib.sha256(
        format_hps_ppfd_values_npz(first.samples)
    ).hexdigest() == first.ppfd_npz_sha256
    assert compute_hps_scalar_metrics(first.samples) == first.metrics
    assert first.plan.paths.source_conversion_summary.read_bytes() == (
        second.plan.paths.source_conversion_summary.read_bytes()
    )
    assert first.plan.paths.scalar_transport_summary.read_bytes() == (
        second.plan.paths.scalar_transport_summary.read_bytes()
    )
    assert first.plan.paths.ppfd_npz.read_bytes() == second.plan.paths.ppfd_npz.read_bytes()
    phase25a = build_hps_radiance_source_plan(
        workspace=tmp_path / "independent-phase25a"
    )
    assert phase25a.profile_id == first.plan.source.profile_id
    assert phase25a.profile_sha256 == first.plan.source.profile_sha256
    assert "conventional" not in first.plan.transport_identity
    assert "smd" not in first.plan.transport_identity


def test_executable_resolution_probes_only_rtrace(tmp_path: Path) -> None:
    resolved = {name: tmp_path / name for name in ("ies2rad", "oconv", "rtrace")}
    resolver_calls = []
    probe_calls = []

    def resolver(command, *, label):
        resolver_calls.append((command, label))
        return resolved[label]

    def probe(path):
        probe_calls.append(path)
        return "RADIANCE fake"

    bins = resolve_hps_scalar_executables(
        _request(tmp_path), resolver=resolver, version_probe=probe
    )
    assert [label for _command, label in resolver_calls] == ["ies2rad", "oconv", "rtrace"]
    assert probe_calls == [resolved["rtrace"]]
    assert bins.rtrace_version_text == "RADIANCE fake"


def test_cli_routes_success_and_failure(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "hps-scalar-transport",
            "--workspace", str(tmp_path / "ok"),
            "--room-ft", "10", "10",
            "--threads", "6",
        ],
        runner=FakeHpsRunner(),
        hps_scalar_executables=_executables(tmp_path),
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "hps-scalar-transport"
    assert payload["fixture_count"] == 4
    assert payload["sensor_count"] == 441
    assert payload["total_tested_system_input_power_w"] == 4180.0
    assert payload["total_planned_ppf_umol_s"] == 7000.0

    code = main(
        ["hps-scalar-transport", "--workspace", str(tmp_path / "bad")],
        runner=FakeHpsRunner(omit_dat=True),
        hps_scalar_executables=_executables(tmp_path),
    )
    assert code == 1
    assert json.loads(capsys.readouterr().err)["success"] is False


def test_execution_import_boundaries_and_native_command_scope() -> None:
    path = Path(__file__).parents[1] / "src" / "fspm_optics" / "transport" / "hps_scalar.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    direct_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "subprocess" not in direct_imports
    assert "shell=True" not in source
    assert "os.environ" not in source
    assert ".salvage_source" not in source
    assert "conventional_led" not in source
    assert "fixtures.smd" not in source
