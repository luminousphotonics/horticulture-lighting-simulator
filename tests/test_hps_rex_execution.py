from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from fspm_optics.cli import main
from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.runner import RunnerResult
from fspm_optics.transport.hps import HPS_REX_RUN_ORDER
from fspm_optics.transport.hps_rex_execution import (
    HPS_REX_INCIDENT_ARRAY_ORDER,
    HpsRexExecutables,
    HpsRexTransportError,
    HpsRexTransportRequest,
    compute_hps_incident_run_metrics,
    compute_hps_scalar_four_band_diagnostics,
    execute_hps_rex_transport,
    format_hps_rex_execution_plan_json,
    format_hps_rex_incident_npz,
    parse_hps_rex_receiver_rgb,
    plan_hps_rex_execution,
    plan_hps_rex_workspace,
    resolve_hps_rex_executables,
    validate_hps_rex_execution_plan,
)

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
    half_x = length / 2.0
    half_y = width / 2.0
    scale = 313250.0 / (length * width)
    return (
        "void brightdata hps_unit_downward_flux_dist\n"
        "5 flatcorr hps_unit_downward_flux.dat source.cal src_phi src_theta\n"
        "0\n"
        f"1 {scale:.15g}\n\n"
        "hps_unit_downward_flux_dist light "
        "hps_unit_downward_flux_light\n"
        "0\n0\n3 1 1 1\n\n"
        "hps_unit_downward_flux_light polygon "
        "hps_unit_downward_flux.d\n"
        "0\n0\n12\n"
        f"{-half_x:.15g} {-half_y:.15g} -0.00025\n"
        f"{-half_x:.15g} {half_y:.15g} -0.00025\n"
        f"{half_x:.15g} {half_y:.15g} -0.00025\n"
        f"{half_x:.15g} {-half_y:.15g} -0.00025\n"
    )


def _converted_dat() -> str:
    return "2\n0 360 2\n0 90 2\n0.25 0.25 0.25 0.25\n"


class FakeHpsRexRunner:
    def __init__(
        self,
        *,
        fail_label: str | None = None,
        mutate_after_oconv: str | None = None,
        mutate_during_ies: str | None = None,
        malformed_interval: str | None = None,
        substituted_native_source: bool = False,
    ) -> None:
        self.fail_label = fail_label
        self.mutate_after_oconv = mutate_after_oconv
        self.mutate_during_ies = mutate_during_ies
        self.malformed_interval = malformed_interval
        self.substituted_native_source = substituted_native_source
        self.commands: list[CommandSpec] = []

    def run(self, command, *, timeout_s=None, stderr_path=None):
        del timeout_s
        self.commands.append(command)
        stderr = Path(stderr_path) if stderr_path is not None else None
        if stderr is not None:
            stderr.write_text("", encoding="utf-8")
        failed = command.label == self.fail_label
        if command.label == "convert_hps_unit_downward_flux_ies":
            assert command.cwd is not None
            converted = _converted_rad()
            if self.substituted_native_source:
                converted = converted.replace("source.cal", "substitute.cal")
            (command.cwd / "hps_unit_downward_flux.rad").write_text(
                converted, encoding="ascii"
            )
            (command.cwd / "hps_unit_downward_flux.dat").write_text(
                _converted_dat(), encoding="ascii"
            )
            root = command.cwd.parent
            if self.mutate_during_ies == "receiver":
                receiver = root / "receivers" / "rex_receiver_rays.pts"
                receiver.write_text(receiver.read_text() + "0 0 0 0 0 1\n")
            elif self.mutate_during_ies == "receiver_metadata":
                metadata = root / "receivers" / "rex_receiver_metadata.json"
                metadata.write_text(metadata.read_text() + "\n")
            elif self.mutate_during_ies == "material":
                plant = root / "00_scalar_par" / "rex_plant.rad"
                plant.write_text(plant.read_text() + "# changed\n")
        elif command.label.startswith("compile_fixture-body-shape-") and not failed:
            assert command.stdout_path is not None
            command.stdout_path.write_bytes(b"fake-fixture-body-octree")
        elif command.label.startswith("compile_hps_rex_") and not failed:
            assert command.stdout_path is not None
            command.stdout_path.write_bytes(b"fake-octree")
            interval = command.label.removeprefix("compile_hps_rex_")
            root = command.cwd
            if self.mutate_after_oconv == "dat" and interval == "scalar_par":
                dat = root / "source" / "hps_unit_downward_flux.dat"
                dat.write_text(_converted_dat() + "\n", encoding="ascii")
            elif self.mutate_after_oconv == "source" and interval == "scalar_par":
                source = root / "00_scalar_par" / "hps_source.rad"
                source.write_text(source.read_text() + "# changed\n")
        elif command.label.startswith("trace_hps_rex_") and not failed:
            interval = command.label.removeprefix("trace_hps_rex_")
            assert command.stdin_path is not None
            assert command.stdout_path is not None
            count = len(command.stdin_path.read_text().splitlines())
            front, back = RUN_VALUES[interval]
            rows = "".join(
                f"{front} {front} {front}\n"
                if index % 2 == 0
                else f"{back} {back} {back}\n"
                for index in range(count)
            )
            if interval == self.malformed_interval:
                rows = rows.rsplit("\n", 2)[0] + "\n"
            command.stdout_path.write_text(rows, encoding="utf-8")
            cache = Path(command.argv[command.argv.index("-af") + 1])
            cache.write_bytes(b"fake-ambient-cache")
        return RunnerResult(
            command_label=command.label,
            argv=command.argv,
            returncode=7 if failed else 0,
            stdout_path=command.stdout_path,
            stderr_text=None,
            stderr_path=stderr,
            wall_time_s=0.0,
            success=not failed,
        )


def _request(tmp_path: Path, **kwargs) -> HpsRexTransportRequest:
    return HpsRexTransportRequest.from_feet(
        workspace=tmp_path / "runtime workspace with spaces", **kwargs
    )


def _executables(tmp_path: Path) -> HpsRexExecutables:
    return HpsRexExecutables(
        tmp_path / "bin" / "ies2rad",
        tmp_path / "bin" / "oconv",
        tmp_path / "bin" / "rtrace",
        "RADIANCE fake",
    )


def test_request_workspace_and_default_plan_closure(tmp_path: Path) -> None:
    request = _request(tmp_path)
    paths = plan_hps_rex_workspace(request)
    assert paths.artifact_root == request.workspace / "hps_rex_transport"
    assert paths.converted_dat_execution_reference == (
        "source/hps_unit_downward_flux.dat"
    )
    assert not request.workspace.exists()
    with pytest.raises(ValueError, match="absolute"):
        HpsRexTransportRequest.from_feet(workspace=Path("relative"))
    plan = plan_hps_rex_execution(request, executables=_executables(tmp_path))
    assert tuple(run.interval_id for run in plan.runs) == HPS_REX_RUN_ORDER
    assert len(plan.commands) == 13
    assert len(plan.bundle.layout.fixtures) == len(plan.source_plan.apertures) == 4
    assert plan.bundle.source_payload.fixture_count == 4
    assert plan.bundle.source_payload.scalar_par_ppf_umol_s_per_fixture == 1750.0
    assert 4 * 1750.0 == 7000.0
    assert len(plan.receiver_samples) == 1024
    assert plan.bundle.leaf_count == 32
    assert plan.bundle.plant_polygon_count == 5248
    assert plan.bundle.patch_count == 512
    assert not request.workspace.exists()


def test_plan_binds_phase25b_budgets_sources_materials_and_commands(tmp_path: Path) -> None:
    plan = plan_hps_rex_execution(
        _request(tmp_path), executables=_executables(tmp_path)
    )
    expected_budgets = (1750.0,) + tuple(
        plan.bundle.source_payload.band(name).per_fixture_ppf_umol_s
        for name in HPS_REX_RUN_ORDER[1:]
    )
    assert tuple(run.part25b.per_fixture_ppf_umol_s for run in plan.runs) == (
        expected_budgets
    )
    assert len({run.source_text_sha256 for run in plan.runs}) == 6
    assert len({run.part25b.material.material_identifier for run in plan.runs}) == 6
    assert len({run.paths.octree for run in plan.runs}) == 6
    assert len({run.paths.ambient_cache for run in plan.runs}) == 6
    for run in plan.runs:
        assert run.part25b.carrier_multiplier == pytest.approx(
            run.part25b.per_fixture_ppf_umol_s * 179.0
        )
        assert run.source_text.count(" polygon ") == 4
        assert run.source_text.split().count(
            plan.paths.converted_dat_execution_reference
        ) == 1
        assert run.source_text.split().count("source.cal") == 1
        assert run.part25b.material.radiance_text.strip() in run.plant_text
        assert run.plant_text.count(" polygon ") == 5248
        assert run.oconv_command.cwd == run.rtrace_command.cwd == (
            plan.paths.artifact_root
        )
        assert run.rtrace_command.stdin_path == plan.paths.receiver_rays
        assert run.rtrace_command.stdout_path == run.paths.raw_rgb
        assert run.rtrace_command.argv[1:5] == ("-h", "-I+", "-n", "6")
    assert validate_hps_rex_execution_plan(plan) is plan
    repeat = plan_hps_rex_execution(
        _request(tmp_path / "repeat"), executables=_executables(tmp_path)
    )
    assert format_hps_rex_execution_plan_json(plan) == (
        format_hps_rex_execution_plan_json(repeat)
    )


def test_fake_execution_compiles_body_and_runs_exact_13_transport_commands(
    tmp_path: Path,
) -> None:
    runner = FakeHpsRexRunner()
    result = execute_hps_rex_transport(
        _request(tmp_path), runner, executables=_executables(tmp_path)
    )
    assert len(runner.commands) == 14
    assert result.command_count == 13
    assert [command.label for command in runner.commands] == [
        result.plan.fixture_occlusion.shapes[0].compile_command.label,
        "convert_hps_unit_downward_flux_ies",
        *[
            label
            for interval in HPS_REX_RUN_ORDER
            for label in (
                f"compile_hps_rex_{interval}",
                f"trace_hps_rex_{interval}",
            )
        ],
    ]
    assert all(command.cwd is not None for command in runner.commands)
    assert all(command.env == {} for command in runner.commands)
    assert all(
        run.oconv_command.cwd == run.rtrace_command.cwd == result.plan.paths.artifact_root
        for run in result.plan.runs
    )
    assert list(result.plan.paths.artifact_root.rglob("*.dat")) == [
        result.plan.paths.converted_dat
    ]
    with np.load(
        result.plan.paths.incident_receiver_values_npz, allow_pickle=False
    ) as archive:
        assert tuple(archive.files) == HPS_REX_INCIDENT_ARRAY_ORDER
        assert all(archive[name].shape == (1024,) for name in archive.files)
    diagnostics = result.scalar_four_band_diagnostics
    assert diagnostics.combined.signed_difference == pytest.approx(0.0)
    assert diagnostics.combined.absolute_difference == pytest.approx(0.0)
    assert diagnostics.receiver_level_rmse == pytest.approx(0.0)
    summary = json.loads(result.plan.paths.incident_transport_summary.read_text())
    assert summary["incident_arrays"]["far_red_included_in_par"] is False
    assert summary["incident_arrays"]["post_trace_179_conversion"] is False
    assert summary["fixture_occlusion"]["identity_sha256"] == (
        result.plan.fixture_occlusion.identity_sha256
    )
    assert summary["native_commands"] == {
        "expected": 13,
        "ies2rad": 1,
        "oconv": 6,
        "recorded": 13,
        "rtrace": 6,
        "shell": False,
    }
    assert [item["interval_id"] for item in summary["run_artifacts"]] == list(
        HPS_REX_RUN_ORDER
    )
    expected_digest_fields = {
        "interval_id",
        "source_rad_sha256",
        "plant_rad_sha256",
        "scene_manifest_sha256",
        "octree_sha256",
        "ambient_cache_sha256",
        "raw_rgb_sha256",
        "decoded_npy_sha256",
    }
    for item, run in zip(summary["run_artifacts"], result.plan.runs, strict=True):
        assert set(item) == expected_digest_fields
        for field, path in (
            ("source_rad_sha256", run.paths.source_rad),
            ("plant_rad_sha256", run.paths.plant_rad),
            ("scene_manifest_sha256", run.paths.scene_manifest),
            ("octree_sha256", run.paths.octree),
            ("ambient_cache_sha256", run.paths.ambient_cache),
            ("raw_rgb_sha256", run.paths.raw_rgb),
            ("decoded_npy_sha256", run.paths.decoded_npy),
        ):
            assert path.read_bytes()
            assert item[field] == hashlib.sha256(path.read_bytes()).hexdigest()
    provenance = json.loads(
        result.plan.paths.command_provenance_summary.read_text()
    )
    assert provenance["success"] is True
    assert all(item["shell"] is False for item in provenance["commands"])


def test_metrics_receiver_pairing_far_red_and_npz_are_deterministic(
    tmp_path: Path,
) -> None:
    plan = plan_hps_rex_execution(
        _request(tmp_path), executables=_executables(tmp_path)
    )
    arrays = tuple(
        (
            f"{interval}_umol_m2_s",
            np.asarray(
                [RUN_VALUES[interval][index % 2] for index in range(1024)],
                dtype=np.float64,
            ),
        )
        for interval in HPS_REX_RUN_ORDER
    )
    first = format_hps_rex_incident_npz(arrays)
    second = format_hps_rex_incident_npz(arrays)
    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    scalar = arrays[0][1]
    four_band = np.add.reduce([item[1] for item in arrays[1:5]])
    metrics = compute_hps_incident_run_metrics(
        "scalar_par", scalar, plan.receiver_samples
    )
    diagnostics = compute_hps_scalar_four_band_diagnostics(
        scalar, four_band, plan.receiver_samples
    )
    assert metrics.front_receiver_count == metrics.back_receiver_count == 512
    assert metrics.physical_patch_count == 512
    assert metrics.front_area_weighted_mean_incident_pfd == pytest.approx(10.0)
    assert metrics.back_area_weighted_mean_incident_pfd == pytest.approx(2.0)
    assert metrics.combined_area_weighted_mean_incident_pfd == pytest.approx(12.0)
    assert diagnostics.receiver_level_rmse == pytest.approx(0.0)
    assert "far_red_umol_m2_s" in HPS_REX_INCIDENT_ARRAY_ORDER
    assert "four_band_par_umol_m2_s" not in HPS_REX_INCIDENT_ARRAY_ORDER


@pytest.mark.parametrize(
    ("runner", "match", "command_count"),
    [
        (FakeHpsRexRunner(mutate_after_oconv="dat"), "DAT hash mismatch", 3),
        (
            FakeHpsRexRunner(mutate_after_oconv="source"),
            "source hash mismatch",
            3,
        ),
        (
            FakeHpsRexRunner(mutate_during_ies="material"),
            "plant hash mismatch",
            2,
        ),
        (
            FakeHpsRexRunner(mutate_during_ies="receiver"),
            "receiver rays hash mismatch",
            2,
        ),
        (
            FakeHpsRexRunner(mutate_during_ies="receiver_metadata"),
            "receiver metadata hash mismatch",
            2,
        ),
        (
            FakeHpsRexRunner(substituted_native_source=True),
            "invalid native HPS source",
            2,
        ),
    ],
)
def test_substitution_and_hash_failures_stop_before_trace(
    tmp_path: Path,
    runner: FakeHpsRexRunner,
    match: str,
    command_count: int,
) -> None:
    request = _request(tmp_path)
    with pytest.raises(HpsRexTransportError, match=match):
        execute_hps_rex_transport(
            request, runner, executables=_executables(tmp_path)
        )
    assert len(runner.commands) == command_count
    root = request.workspace / "hps_rex_transport"
    assert (root / "failure_summary.json").is_file()
    assert not (root / "incident_transport_summary.json").exists()
    assert not (root / "incident_receiver_values.npz").exists()


def test_nonempty_root_partial_failure_and_strict_rgb(tmp_path: Path) -> None:
    request = _request(tmp_path / "stale")
    root = request.workspace / "hps_rex_transport"
    root.mkdir(parents=True)
    (root / "stale.json").write_text("{}\n")
    runner = FakeHpsRexRunner()
    with pytest.raises(HpsRexTransportError, match="absent or empty"):
        execute_hps_rex_transport(
            request, runner, executables=_executables(tmp_path)
        )
    assert runner.commands == []

    failed = _request(tmp_path / "failed")
    runner = FakeHpsRexRunner(fail_label="trace_hps_rex_green")
    with pytest.raises(HpsRexTransportError, match="return code 7"):
        execute_hps_rex_transport(
            failed, runner, executables=_executables(tmp_path)
        )
    assert runner.commands[-1].label == "trace_hps_rex_green"
    assert not any("orange" in command.label for command in runner.commands)

    for text, match in (
        ("1 1 1\n" * 1023, "expected 1024"),
        ("nan nan nan\n" + "1 1 1\n" * 1023, "finite"),
        ("-1 -1 -1\n" + "1 1 1\n" * 1023, "non-negative"),
        ("1 1.1 1\n" + "1 1 1\n" * 1023, "R=G=B"),
    ):
        with pytest.raises(HpsRexTransportError, match=match):
            parse_hps_rex_receiver_rgb(text, interval_id="blue")


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

    binaries = resolve_hps_rex_executables(
        resolver=resolver, version_probe=probe
    )
    assert [label for _command, label in resolver_calls] == [
        "ies2rad",
        "oconv",
        "rtrace",
    ]
    assert probe_calls == [resolved["rtrace"]]
    assert binaries.rtrace_version_text == "RADIANCE fake"


def test_cli_routing_and_import_boundary(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "hps-rex-transport",
            "--workspace",
            str(tmp_path / "ok"),
            "--room-ft",
            "10",
            "10",
            "--threads",
            "6",
        ],
        runner=FakeHpsRexRunner(),
        hps_rex_executables=_executables(tmp_path),
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "hps-rex-transport"
    assert payload["receiver_count"] == 1024
    assert payload["native_command_count"] == 13

    code = main(
        [
            "hps-rex-transport",
            "--workspace",
            str(tmp_path / "bad"),
        ],
        runner=FakeHpsRexRunner(
            fail_label="compile_hps_rex_scalar_par"
        ),
        hps_rex_executables=_executables(tmp_path),
    )
    assert code == 1
    assert json.loads(capsys.readouterr().err)["success"] is False

    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "transport"
        / "hps_rex_execution.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "subprocess" not in imports
    assert "shell=True" not in source
    assert ".salvage_source" not in source
    assert "fixtures.conventional_led" not in source
    assert "fixtures.smd" not in source
