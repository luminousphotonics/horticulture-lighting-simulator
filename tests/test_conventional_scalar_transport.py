from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from fspm_optics.cli import main
from fspm_optics.fixtures.conventional_led import (
    build_conventional_radiance_source_plan,
)
from fspm_optics.radiance.runner import RunnerResult
from fspm_optics.transport.conventional_scalar import (
    ConventionalScalarExecutables,
    ConventionalScalarTransportError,
    ConventionalScalarTransportRequest,
    compute_conventional_scalar_metrics,
    execute_conventional_scalar_transport,
    format_ppfd_values_npz,
    plan_conventional_scalar_transport,
    plan_conventional_scalar_workspace,
    resolve_conventional_scalar_executables,
    validate_converted_dat,
)


def _converted_rad(carrier_multiplier: float = 307164.0) -> str:
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
        f"0\n4 {carrier_multiplier:g} 1.190 1.087 0.108\n",
        "conventional_led_unit_downward_flux_dist light "
        "conventional_led_unit_downward_flux_light\n0\n0\n3 1 1 1\n",
    ]
    for suffix, indices in faces:
        values = " ".join(
            str(value) for index in indices for value in corners[index]
        )
        sections.append(
            "conventional_led_unit_downward_flux_light polygon "
            f"conventional_led_unit_downward_flux{suffix}\n0\n0\n12 {values}\n"
        )
    return "\n".join(sections)


def _converted_dat() -> str:
    return "2\n0 360 2\n0 180 2\n0.25 0.25 0.25 0.25\n"


class FakeConventionalRunner:
    def __init__(
        self,
        *,
        invalid_rgb: bool = False,
        omit_dat: bool = False,
        remove_dat_after_oconv: bool = False,
        mismatch_dat_after_oconv: bool = False,
    ) -> None:
        self.commands = []
        self.invalid_rgb = invalid_rgb
        self.omit_dat = omit_dat
        self.remove_dat_after_oconv = remove_dat_after_oconv
        self.mismatch_dat_after_oconv = mismatch_dat_after_oconv

    def run(self, command, *, timeout_s=None, stderr_path=None):
        del timeout_s
        self.commands.append(command)
        stderr = None if stderr_path is None else Path(stderr_path)
        if stderr is not None:
            stderr.write_text("", encoding="utf-8")
        if command.label == "convert_conventional_unit_downward_flux_ies":
            assert command.cwd is not None
            multiplier = float(command.argv[command.argv.index("-m") + 1])
            (command.cwd / "conventional_led_unit_downward_flux.rad").write_text(
                _converted_rad(multiplier), encoding="ascii"
            )
            if not self.omit_dat:
                (command.cwd / "conventional_led_unit_downward_flux.dat").write_text(
                    _converted_dat(), encoding="ascii"
                )
        elif command.label.startswith("compile_fixture-body-shape-"):
            assert command.stdout_path is not None
            command.stdout_path.write_bytes(b"fake-body-octree")
        elif command.label == "compile_conventional_scalar_scene":
            assert command.stdout_path is not None
            command.stdout_path.write_bytes(b"fake-octree")
            dat_path = (
                command.cwd
                / "source"
                / "conventional_led_unit_downward_flux.dat"
            )
            if self.remove_dat_after_oconv:
                dat_path.unlink()
            elif self.mismatch_dat_after_oconv:
                dat_path.write_text(_converted_dat() + "\n", encoding="ascii")
        elif command.label == "baseline_scalar_ppfd_rtrace":
            assert command.stdin_path is not None
            assert command.stdout_path is not None
            count = len(command.stdin_path.read_text(encoding="utf-8").splitlines())
            row = "10 10 11\n" if self.invalid_rgb else "10 10 10\n"
            command.stdout_path.write_text(row * count, encoding="utf-8")
            cache_index = command.argv.index("-af") + 1
            Path(command.argv[cache_index]).write_bytes(b"fake-ambient-cache")
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


def _executables(tmp_path: Path) -> ConventionalScalarExecutables:
    return ConventionalScalarExecutables(
        tmp_path / "bin" / "ies2rad",
        tmp_path / "bin" / "oconv",
        tmp_path / "bin" / "rtrace",
        "RADIANCE fake",
    )


def _request(tmp_path: Path, **kwargs) -> ConventionalScalarTransportRequest:
    return ConventionalScalarTransportRequest.from_feet(
        workspace=tmp_path / "runtime",
        **kwargs,
    )


def test_request_has_explicit_units_and_rejects_invalid_values(tmp_path: Path) -> None:
    request = _request(tmp_path)
    assert request.room_length_m == pytest.approx(3.048)
    assert request.room_width_m == pytest.approx(3.048)
    assert request.layout_policy == "practical"
    assert request.quality_profile == "standard"
    assert request.reference_plane_z_m == 0.005
    assert request.mount_height_m == 0.4572
    assert request.reference_plane_z_m + request.mount_height_m == 0.4622
    with pytest.raises(ValueError, match="threads"):
        _request(tmp_path / "threads", threads=0)
    with pytest.raises(ValueError, match="quality_profile"):
        _request(tmp_path / "quality", quality_profile="preview")
    with pytest.raises(ValueError, match="outside the repository"):
        ConventionalScalarTransportRequest.from_feet(
            workspace=Path(__file__).parents[1] / "runtime"
        )


def test_workspace_paths_are_deterministic_and_conventional_only(tmp_path: Path) -> None:
    paths = plan_conventional_scalar_workspace(_request(tmp_path))
    assert paths.artifact_root == tmp_path / "runtime" / "conventional_scalar"
    assert paths.final_scene_inputs == (
        paths.room_rad,
        paths.shared_angular_rad,
        paths.aperture_rad,
        paths.fixture_body_instances_rad,
    )
    assert paths.ppfd_npz.name == "ppfd_values.npz"
    assert paths.source_conversion_summary.name == "source_conversion_summary.json"
    assert paths.scalar_transport_summary.name == "scalar_transport_summary.json"
    assert "smd" not in str(paths.artifact_root).lower()


def test_default_plan_closes_10x10_counts_commands_and_scene_order(tmp_path: Path) -> None:
    plan = plan_conventional_scalar_transport(
        _request(tmp_path), executables=_executables(tmp_path)
    )
    assert (plan.layout.resolved_counts.columns_x, plan.layout.resolved_counts.rows_y) == (2, 2)
    assert len(plan.layout.fixtures) == 4
    assert len(plan.source.apertures) == 32
    assert plan.source.whole_layout_transported_downward_ppf_umol_s == 6864.0
    assert plan.source.derived_ies.derived_flux.downward_hemisphere_flux_cd_sr == pytest.approx(1.0)
    assert len(plan.sensor_points) == 441
    assert plan.scene.scene.source_files == plan.paths.final_scene_inputs
    assert plan.paths.raw_converted_rad not in plan.scene.scene.source_files
    assert plan.ies2rad_command.argv[1:] == (
        "-dm", "-t", "default", "-c", "1", "1", "1", "-m", "307164",
        "-o", "conventional_led_unit_downward_flux",
        "conventional_led_unit_downward_flux.ies",
    )
    assert plan.oconv_command.argv[1:] == (
        "-f", *(str(path) for path in plan.paths.final_scene_inputs)
    )
    assert plan.oconv_command.stdout_mode == "binary"
    assert plan.oconv_command.stdout_path == plan.paths.octree
    assert plan.paths.converted_dat_execution_reference == (
        "source/conventional_led_unit_downward_flux.dat"
    )
    assert plan.oconv_command.cwd == plan.paths.artifact_root
    assert plan.rtrace_command.cwd == plan.paths.artifact_root
    assert (
        plan.oconv_command.cwd / plan.paths.converted_dat_execution_reference
    ) == plan.paths.converted_dat
    assert (
        plan.rtrace_command.cwd / plan.paths.converted_dat_execution_reference
    ) == plan.paths.converted_dat
    assert plan.rtrace_command.stdin_path == plan.paths.sensor_rays
    assert plan.rtrace_command.stdout_path == plan.paths.raw_rtrace
    assert plan.rtrace_command.argv[1:5] == ("-h", "-I+", "-n", "6")
    assert "-af" in plan.rtrace_command.argv
    assert plan.ies2rad_command.env == plan.oconv_command.env == plan.rtrace_command.env == {}


def test_rolling_bench_changes_only_fixture_geometry_not_receiver_domain(
    tmp_path: Path,
) -> None:
    practical = plan_conventional_scalar_transport(
        _request(
            tmp_path / "practical",
            room_length_ft=24.0,
            room_width_ft=12.0,
            layout_policy="practical",
        ),
        executables=_executables(tmp_path / "practical"),
    )
    rolling = plan_conventional_scalar_transport(
        _request(
            tmp_path / "rolling",
            room_length_ft=24.0,
            room_width_ft=12.0,
            layout_policy="rolling_bench",
        ),
        executables=_executables(tmp_path / "rolling"),
    )

    assert rolling.layout.layout_id != practical.layout.layout_id
    assert rolling.layout.fixtures != practical.layout.fixtures
    assert rolling.room == practical.room
    assert rolling.room_text == practical.room_text
    assert rolling.sensor_grid_spec == practical.sensor_grid_spec
    assert rolling.sensor_points == practical.sensor_points
    assert rolling.sensor_text.encode("ascii") == practical.sensor_text.encode("ascii")
    assert rolling.sensor_identity == practical.sensor_identity
    assert rolling.layout.mount == practical.layout.mount
    assert rolling.source.original_ies == practical.source.original_ies
    assert (
        rolling.source.angular_normalization
        == practical.source.angular_normalization
    )
    assert (
        rolling.source.declared_operating_point
        == practical.source.declared_operating_point
    )
    assert rolling.source.carrier_scale == practical.source.carrier_scale
    assert rolling.request.quality_profile == practical.request.quality_profile
    assert rolling.request.global_dimming_factor == (
        practical.request.global_dimming_factor
    )
    assert [
        option
        for index, option in enumerate(rolling.radiance_options)
        if index == 0 or rolling.radiance_options[index - 1] != "-af"
    ] == [
        option
        for index, option in enumerate(practical.radiance_options)
        if index == 0 or practical.radiance_options[index - 1] != "-af"
    ]


def test_executable_resolution_probes_rtrace_only(tmp_path: Path) -> None:
    resolved = {
        "ies2rad": tmp_path / "ies2rad",
        "oconv": tmp_path / "oconv",
        "rtrace": tmp_path / "rtrace",
    }
    resolver_calls = []
    probe_calls = []

    def resolver(command, *, label):
        resolver_calls.append((command, label))
        return resolved[label]

    def probe(path):
        probe_calls.append(path)
        return "RADIANCE fake"

    executables = resolve_conventional_scalar_executables(
        _request(tmp_path), resolver=resolver, version_probe=probe
    )
    assert [label for _command, label in resolver_calls] == [
        "ies2rad", "oconv", "rtrace"
    ]
    assert probe_calls == [resolved["rtrace"]]
    assert executables.rtrace_version_text == "RADIANCE fake"


def test_native_fake_execution_writes_validated_deterministic_artifacts(tmp_path: Path) -> None:
    runner = FakeConventionalRunner()
    first = execute_conventional_scalar_transport(
        _request(tmp_path), runner, executables=_executables(tmp_path)
    )
    assert [item.label for item in runner.commands] == [
        "convert_conventional_unit_downward_flux_ies",
        next(
            item.label
            for item in runner.commands
            if item.label.startswith("compile_fixture-body-shape-")
        ),
        "compile_conventional_scalar_scene",
        "baseline_scalar_ppfd_rtrace",
    ]
    assert first.metrics.sensor_count == 441
    assert first.metrics.mean_ppfd_umol_m2_s == 10.0
    assert first.metrics.minimum_ppfd_umol_m2_s == 10.0
    assert first.metrics.maximum_ppfd_umol_m2_s == 10.0
    assert first.metrics.standard_deviation_ppfd_umol_m2_s == 0.0
    assert first.metrics.coefficient_of_variation == 0.0
    summary = json.loads(first.plan.paths.scalar_transport_summary.read_text())
    assert summary["operating_point"] == {
        "auto_dimming": False,
        "fixed_full_output": True,
        "rated_fixture_power_w": 660.0,
        "tested_ies_input_w_provenance_only": 663.2,
        "full_output_fixture_ppf_umol_s": 1716.0,
        "full_output_par_ppe_umol_per_j": 2.6,
        "applied_global_dimming_factor": 1.0,
        "effective_fixture_power_w": 660.0,
        "effective_fixture_ppf_umol_s": 1716.0,
        "effective_par_ppe_umol_per_j": 2.6,
        "post_trace_scale": None,
        "target_ppfd": None,
        "full_output_total_power_w": 2640.0,
        "effective_total_power_w": 2640.0,
        "full_output_total_ppf_umol_s": 6864.0,
        "effective_total_ppf_umol_s": 6864.0,
        "dimming_stage": "ies2rad_carrier_before_trace",
        "post_trace_dimming": False,
    }
    source = json.loads(first.plan.paths.source_conversion_summary.read_text())
    assert source["adaptation"]["flatcorr_factor"] == pytest.approx(
        307164.0 / first.plan.source.emitting_boundary_area_m2_per_fixture
    )
    assert source["adaptation"]["raw_full_box_enters_final_scene"] is False
    assert source["fixture_count"] == 4
    assert source["aperture_count"] == 32
    with np.load(first.plan.paths.ppfd_npz, allow_pickle=False) as archive:
        assert tuple(archive.files) == ("x_m", "y_m", "z_m", "ppfd_umol_m2_s")
        assert archive["ppfd_umol_m2_s"].shape == (441,)
        assert np.all(archive["ppfd_umol_m2_s"] == 10.0)
    aperture_text = first.plan.paths.aperture_rad.read_text()
    assert aperture_text.count(" polygon ") == 32
    assert "!xform" not in aperture_text
    shared_text = first.plan.paths.shared_angular_rad.read_text()
    assert "boxcorr" not in shared_text
    assert "flatcorr" in shared_text
    assert "source.cal" in shared_text
    assert (
        "source/conventional_led_unit_downward_flux.dat"
        in shared_text
    )
    assert list(first.plan.paths.artifact_root.rglob("*.dat")) == [
        first.plan.paths.converted_dat
    ]


def test_nontrivial_source_factor_uses_native_ies2rad_carrier_precision(
    tmp_path: Path,
) -> None:
    requested_factor = 0.8123456789
    expected_carrier = float(format(307164.0 * requested_factor, ".6g"))
    expected_factor = expected_carrier / 307164.0
    request = _request(
        tmp_path,
        global_dimming_factor=requested_factor,
    )

    result = execute_conventional_scalar_transport(
        request,
        FakeConventionalRunner(),
        executables=_executables(tmp_path),
    )

    assert request.global_dimming_factor == expected_factor
    assert result.plan.source.carrier_scale.ies2rad_multiplier == expected_carrier
    assert (
        result.plan.source.carrier_scale.global_dimming_factor
        == expected_factor
    )
    carrier = result.source_conversion_summary["carrier_derivation"]
    assert carrier["ies2rad_multiplier"] == expected_carrier
    assert carrier["global_source_dimming_factor"] == expected_factor
    assert carrier["carrier_quantized_to_native_ies2rad_output"] is True
    assert carrier["ies2rad_output_numeric_format"] == "%g (six significant digits)"
    operating = result.scalar_transport_summary["operating_point"]
    assert operating["effective_fixture_power_w"] == pytest.approx(
        660.0 * expected_factor
    )
    assert operating["effective_fixture_ppf_umol_s"] == pytest.approx(
        1716.0 * expected_factor
    )
    assert operating["effective_par_ppe_umol_per_j"] == pytest.approx(2.6)
    assert operating["post_trace_dimming"] is False


def test_nonempty_workspace_and_missing_or_invalid_outputs_fail_closed(tmp_path: Path) -> None:
    stale_request = _request(tmp_path / "stale")
    stale_root = stale_request.workspace / "conventional_scalar"
    stale_root.mkdir(parents=True)
    (stale_root / "old.json").write_text("{}")
    with pytest.raises(ConventionalScalarTransportError, match="absent or empty"):
        execute_conventional_scalar_transport(
            stale_request,
            FakeConventionalRunner(),
            executables=_executables(tmp_path),
        )

    missing_request = _request(tmp_path / "missing")
    with pytest.raises(ConventionalScalarTransportError, match="output set mismatch"):
        execute_conventional_scalar_transport(
            missing_request,
            FakeConventionalRunner(omit_dat=True),
            executables=_executables(tmp_path),
        )
    assert not (
        missing_request.workspace
        / "conventional_scalar"
        / "scalar_transport_summary.json"
    ).exists()
    assert (
        missing_request.workspace / "conventional_scalar" / "failure_summary.json"
    ).is_file()

    rgb_request = _request(tmp_path / "rgb")
    with pytest.raises(ConventionalScalarTransportError, match="grey scalar channels"):
        execute_conventional_scalar_transport(
            rgb_request,
            FakeConventionalRunner(invalid_rgb=True),
            executables=_executables(tmp_path),
        )

    mismatch_request = _request(tmp_path / "mismatch")
    mismatch_runner = FakeConventionalRunner(mismatch_dat_after_oconv=True)
    with pytest.raises(ConventionalScalarTransportError, match="hash changed"):
        execute_conventional_scalar_transport(
            mismatch_request,
            mismatch_runner,
            executables=_executables(tmp_path),
        )
    assert [command.label for command in mismatch_runner.commands] == [
        "convert_conventional_unit_downward_flux_ies",
        next(
            command.label
            for command in mismatch_runner.commands
            if command.label.startswith("compile_fixture-body-shape-")
        ),
        "compile_conventional_scalar_scene",
    ]

    removed_request = _request(tmp_path / "removed")
    removed_runner = FakeConventionalRunner(remove_dat_after_oconv=True)
    with pytest.raises(ConventionalScalarTransportError, match="missing or empty"):
        execute_conventional_scalar_transport(
            removed_request,
            removed_runner,
            executables=_executables(tmp_path),
        )
    assert [command.label for command in removed_runner.commands] == [
        "convert_conventional_unit_downward_flux_ies",
        next(
            command.label
            for command in removed_runner.commands
            if command.label.startswith("compile_fixture-body-shape-")
        ),
        "compile_conventional_scalar_scene",
    ]


def test_run_root_relative_dat_reference_supports_workspace_spaces(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path / "runtime workspace with spaces")
    result = execute_conventional_scalar_transport(
        request,
        FakeConventionalRunner(),
        executables=_executables(tmp_path),
    )
    plan = result.plan
    assert plan.oconv_command.cwd == plan.rtrace_command.cwd == plan.paths.artifact_root
    assert " " in str(plan.paths.artifact_root)
    assert plan.paths.converted_dat_execution_reference == (
        "source/conventional_led_unit_downward_flux.dat"
    )
    assert list(plan.paths.artifact_root.rglob("*.dat")) == [
        plan.paths.converted_dat
    ]


def test_dat_and_npz_contracts_are_strict_and_deterministic(tmp_path: Path) -> None:
    metadata = validate_converted_dat(_converted_dat().encode("ascii"))
    assert metadata["dimension_count"] == 2
    assert metadata["value_count"] == 4
    with pytest.raises(ConventionalScalarTransportError, match="value count mismatch"):
        validate_converted_dat(b"2\n0 360 2\n0 180 2\n1 1 1\n")
    result = execute_conventional_scalar_transport(
        _request(tmp_path),
        FakeConventionalRunner(),
        executables=_executables(tmp_path),
    )
    first = format_ppfd_values_npz(result.samples)
    second = format_ppfd_values_npz(result.samples)
    assert first == second
    assert hashlib.sha256(first).hexdigest() == result.ppfd_npz_sha256
    assert compute_conventional_scalar_metrics(result.samples) == result.metrics
    repeat = execute_conventional_scalar_transport(
        _request(tmp_path / "repeat"),
        FakeConventionalRunner(),
        executables=_executables(tmp_path),
    )
    assert repeat.plan.paths.source_conversion_summary.read_bytes() == (
        result.plan.paths.source_conversion_summary.read_bytes()
    )
    assert repeat.plan.paths.scalar_transport_summary.read_bytes() == (
        result.plan.paths.scalar_transport_summary.read_bytes()
    )
    assert repeat.plan.paths.ppfd_npz.read_bytes() == result.plan.paths.ppfd_npz.read_bytes()


def test_cli_routes_success_and_returns_nonzero_on_failure(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "conventional-scalar-transport",
            "--workspace", str(tmp_path / "ok"),
            "--threads", "2",
        ],
        runner=FakeConventionalRunner(),
        conventional_executables=_executables(tmp_path),
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "conventional-scalar-transport"
    assert payload["success"] is True
    assert payload["fixture_count"] == 4
    assert payload["rated_fixture_power_w"] == 660.0
    assert payload["full_output_fixture_ppe_umol_per_j"] == 2.6
    assert payload["full_output_fixture_ppf_umol_s"] == 1716.0
    assert payload["total_rated_power_w"] == 2640.0
    assert payload["total_full_output_ppf_umol_s"] == 6864.0

    code = main(
        ["conventional-scalar-transport", "--workspace", str(tmp_path / "bad")],
        runner=FakeConventionalRunner(omit_dat=True),
        conventional_executables=_executables(tmp_path),
    )
    assert code == 1
    failure = json.loads(capsys.readouterr().err)
    assert failure["success"] is False


def test_command_and_summary_contracts_exclude_smd_targeting_and_postprocessing(
    tmp_path: Path,
) -> None:
    result = execute_conventional_scalar_transport(
        _request(tmp_path),
        FakeConventionalRunner(),
        executables=_executables(tmp_path),
    )
    command_summary = json.loads(
        result.plan.paths.command_provenance_summary.read_text()
    )
    assert command_summary["radiance_version"]["oconv_version"] is None
    assert all(item["shell"] is False for item in command_summary["commands"])
    assert all(item["env"] == {} for item in command_summary["commands"])
    scientific_text = result.plan.paths.scalar_transport_summary.read_text().lower()
    assert '"target_ppfd": null' in scientific_text
    assert '"auto_dimming": false' in scientific_text
    assert '"post_trace_179_conversion": false' in scientific_text
    assert "proposed_led_smd_nominal_source_v1" not in scientific_text
    assert "179" not in result.plan.paths.raw_rtrace.read_text()
    source_summary = json.loads(
        result.plan.paths.source_conversion_summary.read_text(encoding="utf-8")
    )
    assert source_summary["carrier_derivation"]["post_trace_179_conversion"] is False
    assert source_summary["adaptation"]["execution_dat_reference"] == (
        "source/conventional_led_unit_downward_flux.dat"
    )
    assert source_summary["adaptation"]["flatcorr_factor"] == pytest.approx(
        307164.0
        / result.plan.source.emitting_boundary_area_m2_per_fixture
    )
    assert result.plan.source.transported_downward_ppf_umol_s_per_fixture == 1716.0
    assert result.plan.source.whole_layout_transported_downward_ppf_umol_s == 6864.0
    phase22_source = build_conventional_radiance_source_plan(
        result.plan.layout,
        workspace=tmp_path / "independent-phase22-plan",
    )
    assert phase22_source.source_plan_id == result.plan.source.source_plan_id
    assert phase22_source.shared_angular_source.identity == (
        result.plan.source.shared_angular_source.identity
    )


def test_execution_module_preserves_command_and_runner_import_boundaries() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "transport"
        / "conventional_scalar.py"
    )
    source = source_path.read_text(encoding="utf-8")
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
