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
from fspm_optics.transport.conventional_rex import CONVENTIONAL_REX_RUN_ORDER
from fspm_optics.transport.conventional_rex_execution import (
    CONVENTIONAL_REX_INCIDENT_ARRAY_ORDER,
    ConventionalRexExecutables,
    ConventionalRexTransportError,
    ConventionalRexTransportRequest,
    compute_incident_run_metrics,
    compute_scalar_four_band_diagnostics,
    execute_conventional_rex_transport,
    format_conventional_rex_incident_npz,
    parse_conventional_rex_receiver_rgb,
    plan_conventional_rex_execution,
    plan_conventional_rex_execution_workspace,
)


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


def _converted_dat() -> str:
    return "2\n0 360 2\n0 180 2\n0.25 0.25 0.25 0.25\n"


RUN_VALUES = {
    "scalar_par": (10.0, 2.0),
    "blue": (1.0, 0.2),
    "green": (2.0, 0.4),
    "orange": (3.0, 0.6),
    "red": (4.0, 0.8),
    "far_red": (0.5, 0.1),
}


class FakeConventionalRexRunner:
    def __init__(
        self,
        *,
        fail_label: str | None = None,
        mutate_after_oconv: str | None = None,
        mutate_during_ies: str | None = None,
        malformed_interval: str | None = None,
    ) -> None:
        self.fail_label = fail_label
        self.mutate_after_oconv = mutate_after_oconv
        self.mutate_during_ies = mutate_during_ies
        self.malformed_interval = malformed_interval
        self.commands: list[CommandSpec] = []

    def run(self, command, *, timeout_s=None, stderr_path=None):
        del timeout_s
        self.commands.append(command)
        stderr = Path(stderr_path) if stderr_path is not None else None
        if stderr is not None:
            stderr.write_text("", encoding="utf-8")
        failed = command.label == self.fail_label
        if command.label == "convert_conventional_unit_downward_flux_ies":
            assert command.cwd is not None
            (command.cwd / "conventional_led_unit_downward_flux.rad").write_text(
                _converted_rad(), encoding="ascii"
            )
            (command.cwd / "conventional_led_unit_downward_flux.dat").write_text(
                _converted_dat(), encoding="ascii"
            )
            if self.mutate_during_ies == "receiver":
                receiver = command.cwd.parent / "receivers" / "rex_receiver_rays.pts"
                receiver.write_text(receiver.read_text() + "0 0 0 0 0 1\n")
            elif self.mutate_during_ies == "material":
                plant = command.cwd.parent / "00_scalar_par" / "rex_plant.rad"
                plant.write_text(plant.read_text() + "# changed\n")
        elif command.label.startswith("compile_fixture-body-shape-") and not failed:
            assert command.stdout_path is not None
            command.stdout_path.write_bytes(b"fake-body-octree")
        elif command.label.startswith("compile_conventional_rex_") and not failed:
            assert command.stdout_path is not None
            command.stdout_path.write_bytes(b"fake-octree")
            interval = command.label.removeprefix("compile_conventional_rex_")
            if self.mutate_after_oconv == "dat" and interval == "scalar_par":
                dat = command.cwd / "source" / "conventional_led_unit_downward_flux.dat"
                dat.write_text(_converted_dat() + "\n", encoding="ascii")
            elif self.mutate_after_oconv == "source" and interval == "scalar_par":
                source = command.cwd / "00_scalar_par" / "source.rad"
                source.write_text(source.read_text() + "# changed\n")
        elif command.label.startswith("trace_conventional_rex_") and not failed:
            interval = command.label.removeprefix("trace_conventional_rex_")
            assert command.stdin_path is not None
            assert command.stdout_path is not None
            count = len(command.stdin_path.read_text().splitlines())
            front_value, back_value = RUN_VALUES[interval]
            rows = "".join(
                f"{front_value} {front_value} {front_value}\n"
                if index % 2 == 0
                else f"{back_value} {back_value} {back_value}\n"
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


def _request(tmp_path: Path, **kwargs) -> ConventionalRexTransportRequest:
    return ConventionalRexTransportRequest.from_feet(
        workspace=tmp_path / "runtime workspace with spaces",
        **kwargs,
    )


def _executables(tmp_path: Path) -> ConventionalRexExecutables:
    return ConventionalRexExecutables(
        tmp_path / "bin" / "ies2rad",
        tmp_path / "bin" / "oconv",
        tmp_path / "bin" / "rtrace",
        "RADIANCE fake",
    )


def test_request_and_workspace_are_typed_fixed_and_write_free(tmp_path: Path) -> None:
    request = _request(tmp_path)
    paths = plan_conventional_rex_execution_workspace(request)
    assert request.room_length_m == pytest.approx(3.048)
    assert request.room_width_m == pytest.approx(3.048)
    assert request.threads == 6
    assert request.quality_profile == "standard"
    assert paths.artifact_root == request.workspace / "conventional_rex_transport"
    assert paths.converted_dat_execution_reference == (
        "source/conventional_led_unit_downward_flux.dat"
    )
    assert not request.workspace.exists()
    with pytest.raises(ValueError, match="absolute"):
        ConventionalRexTransportRequest.from_feet(workspace=Path("relative"))
    with pytest.raises(ValueError, match="threads"):
        _request(tmp_path / "threads", threads=0)
    with pytest.raises(ValueError, match="quality_profile"):
        _request(tmp_path / "quality", quality_profile="preview")


def test_plan_has_exact_sources_materials_receivers_and_commands(tmp_path: Path) -> None:
    plan = plan_conventional_rex_execution(
        _request(tmp_path), executables=_executables(tmp_path)
    )
    assert tuple(run.interval_id for run in plan.runs) == CONVENTIONAL_REX_RUN_ORDER
    assert len(plan.receiver_samples) == 1024
    assert tuple(item.receiver_id for item in plan.receiver_samples) == (
        plan.bundle.ordered_receiver_ids
    )
    assert plan.paths.receiver_rays == plan.bundle.shared_paths.receivers
    assert len({run.paths.octree for run in plan.runs}) == 6
    assert len({run.paths.ambient_cache for run in plan.runs}) == 6
    assert len({run.angular_modifier_id for run in plan.runs}) == 6
    assert tuple(run.part1.per_fixture_ppf_umol_s for run in plan.runs) == pytest.approx(
        (
            1716.0,
            300.99699236008087,
            747.9933521852188,
            234.54461205436155,
            432.4650434003389,
            15.851002094559862,
        )
    )
    for run in plan.runs:
        assert run.part1.carrier_multiplier == pytest.approx(
            run.part1.per_fixture_ppf_umol_s * 179.0
        )
        assert run.part1.flat_source_correction == pytest.approx(
            run.part1.carrier_multiplier / run.part1.aperture_area_m2
        )
        assert run.source_text.count(" polygon ") == 32
        assert " illum " in run.source_text
        assert " light " not in run.source_text
        assert plan.paths.converted_dat_execution_reference in run.source_text
        assert "source.cal" in run.source_text
        assert run.part1.material.radiance_text.strip() in run.plant_text
        assert run.plant_text.count(" polygon ") == 5248
        assert run.oconv_command.cwd == run.rtrace_command.cwd == plan.paths.artifact_root
        assert run.oconv_command.stdout_mode == "binary"
        assert run.oconv_command.stdout_path == run.paths.octree
        assert run.rtrace_command.stdin_path == plan.paths.receiver_rays
        assert run.rtrace_command.stdout_path == run.paths.raw_rgb
        assert run.rtrace_command.argv[1:5] == ("-h", "-I+", "-n", "6")
    assert not plan.request.workspace.exists()


def test_fake_native_execution_is_sequential_and_writes_incident_artifacts(tmp_path: Path) -> None:
    runner = FakeConventionalRexRunner()
    result = execute_conventional_rex_transport(
        _request(tmp_path), runner, executables=_executables(tmp_path)
    )
    assert len(runner.commands) == result.command_count == 14
    assert [item.label for item in runner.commands] == [
        result.plan.fixture_occlusion.shapes[0].compile_command.label,
        "convert_conventional_unit_downward_flux_ies",
        *[
            label
            for interval in CONVENTIONAL_REX_RUN_ORDER
            for label in (
                f"compile_conventional_rex_{interval}",
                f"trace_conventional_rex_{interval}",
            )
        ],
    ]
    assert sum(command.argv[0].endswith("ies2rad") for command in runner.commands) == 1
    assert all(command.cwd is not None for command in runner.commands)
    assert all(command.env == {} for command in runner.commands)
    assert all(
        prohibited not in token
        for command in runner.commands
        for token in command.argv
        for prohibited in ("xform", "rcontrib", "rfluxmtx", "smd")
    )
    assert list(result.plan.paths.artifact_root.rglob("*.dat")) == [
        result.plan.paths.converted_dat
    ]
    assert result.scalar_four_band_diagnostics.combined.absolute_difference == pytest.approx(0.0)
    assert result.scalar_four_band_diagnostics.receiver_level_rmse == pytest.approx(0.0)
    with np.load(result.plan.paths.incident_receiver_values_npz, allow_pickle=False) as archive:
        assert tuple(archive.files) == CONVENTIONAL_REX_INCIDENT_ARRAY_ORDER
        assert all(archive[name].shape == (1024,) for name in archive.files)
        assert "four_band_par_umol_m2_s" not in archive.files
    summary = json.loads(result.plan.paths.incident_transport_summary.read_text())
    assert summary["native_commands"] == {
        "expected": 14,
        "ies2rad": 1,
        "oconv": 7,
        "recorded": 14,
        "rtrace": 6,
        "sequential_local_execution": True,
        "shell": False,
    }
    assert summary["incident_arrays"]["post_trace_179_conversion"] is False
    assert summary["incident_arrays"]["post_trace_spectral_fraction"] is False
    assert summary["incident_arrays"]["far_red_included_in_par"] is False
    provenance = json.loads(
        result.plan.paths.command_provenance_summary.read_text()
    )
    assert provenance["success"] is True
    assert provenance["recorded_native_command_count"] == 14
    assert all(item["shell"] is False for item in provenance["commands"])
    assert all(
        item["cwd"] is None or not Path(item["cwd"]).is_absolute()
        for item in provenance["commands"]
    )
    assert all(len(item["stderr_sha256"]) == 64 for item in provenance["commands"])


def test_metrics_four_band_diagnostics_and_npz_are_deterministic(tmp_path: Path) -> None:
    plan = plan_conventional_rex_execution(
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
        for interval in CONVENTIONAL_REX_RUN_ORDER
    )
    first = format_conventional_rex_incident_npz(arrays)
    second = format_conventional_rex_incident_npz(arrays)
    assert first == second
    scalar = arrays[0][1]
    four_band = np.add.reduce([item[1] for item in arrays[1:5]])
    metrics = compute_incident_run_metrics(
        "scalar_par", scalar, plan.receiver_samples
    )
    diagnostics = compute_scalar_four_band_diagnostics(
        scalar, four_band, plan.receiver_samples
    )
    assert metrics.front_area_weighted_mean_incident_pfd == pytest.approx(10.0)
    assert metrics.back_area_weighted_mean_incident_pfd == pytest.approx(2.0)
    assert metrics.combined_area_weighted_mean_incident_pfd == pytest.approx(12.0)
    assert diagnostics.receiver_level_rmse == pytest.approx(0.0)
    assert diagnostics.reconciliation_scale_applied is False
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


@pytest.mark.parametrize(
    ("runner", "match", "expected_labels"),
    [
        (
            FakeConventionalRexRunner(mutate_after_oconv="dat"),
            "DAT hash changed",
            3,
        ),
        (
            FakeConventionalRexRunner(mutate_after_oconv="source"),
            "source hash mismatch",
            3,
        ),
        (
            FakeConventionalRexRunner(mutate_during_ies="material"),
            "plant hash mismatch",
            2,
        ),
        (
            FakeConventionalRexRunner(mutate_during_ies="receiver"),
            "receiver rays hash mismatch",
            2,
        ),
    ],
)
def test_hash_mismatches_fail_before_the_next_trace(
    tmp_path: Path,
    runner: FakeConventionalRexRunner,
    match: str,
    expected_labels: int,
) -> None:
    request = _request(tmp_path)
    with pytest.raises(ConventionalRexTransportError, match=match):
        execute_conventional_rex_transport(
            request, runner, executables=_executables(tmp_path)
        )
    assert len(runner.commands) == expected_labels
    root = request.workspace / "conventional_rex_transport"
    assert (root / "failure_summary.json").is_file()
    assert not (root / "incident_transport_summary.json").exists()
    assert not (root / "incident_receiver_values.npz").exists()


def test_partial_failure_stops_later_runs_and_preserves_failure_provenance(tmp_path: Path) -> None:
    runner = FakeConventionalRexRunner(
        fail_label="trace_conventional_rex_green"
    )
    request = _request(tmp_path)
    with pytest.raises(ConventionalRexTransportError, match="return code 7"):
        execute_conventional_rex_transport(
            request, runner, executables=_executables(tmp_path)
        )
    assert runner.commands[-1].label == "trace_conventional_rex_green"
    assert not any("orange" in command.label for command in runner.commands)
    root = request.workspace / "conventional_rex_transport"
    failure = json.loads((root / "failure_summary.json").read_text())
    provenance = json.loads((root / "command_provenance_summary.json").read_text())
    assert failure["failing_interval_id"] == "green"
    assert failure["failing_command_label"] == "trace_conventional_rex_green"
    assert provenance["success"] is False
    assert provenance["commands"][-1]["return_code"] == 7
    assert not (root / "incident_transport_summary.json").exists()


def test_nonempty_workspace_is_rejected_before_any_command(tmp_path: Path) -> None:
    request = _request(tmp_path)
    root = request.workspace / "conventional_rex_transport"
    root.mkdir(parents=True)
    (root / "stale.json").write_text("{}\n")
    runner = FakeConventionalRexRunner()
    with pytest.raises(ConventionalRexTransportError, match="absent or empty"):
        execute_conventional_rex_transport(
            request, runner, executables=_executables(tmp_path)
        )
    assert runner.commands == []
    assert not (root / "failure_summary.json").exists()


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("1 1 1\n" * 1023, "expected 1024"),
        ("nan nan nan\n" + "1 1 1\n" * 1023, "finite"),
        ("-1 -1 -1\n" + "1 1 1\n" * 1023, "non-negative"),
        ("1 1.1 1\n" + "1 1 1\n" * 1023, "R=G=B"),
        ("1 1\n" + "1 1 1\n" * 1023, "exactly three"),
    ],
)
def test_strict_receiver_decoder_rejects_structural_failures(text: str, match: str) -> None:
    with pytest.raises(ConventionalRexTransportError, match=match):
        parse_conventional_rex_receiver_rgb(text, interval_id="blue")


def test_cli_routes_one_focused_command_and_returns_nonzero_on_failure(
    tmp_path: Path, capsys
) -> None:
    code = main(
        [
            "conventional-rex-transport",
            "--workspace",
            str(tmp_path / "ok"),
        ],
        runner=FakeConventionalRexRunner(),
        conventional_rex_executables=_executables(tmp_path),
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "conventional-rex-transport"
    assert payload["success"] is True
    assert payload["receiver_count"] == 1024
    assert payload["native_command_count"] == 14

    code = main(
        [
            "conventional-rex-transport",
            "--workspace",
            str(tmp_path / "failed"),
        ],
        runner=FakeConventionalRexRunner(
            fail_label="compile_conventional_rex_scalar_par"
        ),
        conventional_rex_executables=_executables(tmp_path),
    )
    assert code == 1
    assert json.loads(capsys.readouterr().err)["success"] is False


def test_execution_import_boundary_has_no_direct_process_or_legacy_access() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "transport"
        / "conventional_rex_execution.py"
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
    assert ".salvage_source" not in source
    assert "copyfile" not in source
