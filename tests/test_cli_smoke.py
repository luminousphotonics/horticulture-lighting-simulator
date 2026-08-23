from __future__ import annotations

import json
from pathlib import Path

from fspm_optics import cli
from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.runner import RunnerResult
from fspm_optics.radiance.versioning import (
    RadianceExecutableVersion,
    RadianceInstallation,
)


class CliFakeRunner:
    def __init__(self) -> None:
        self.calls: list[CommandSpec] = []

    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult:
        del timeout_s, stderr_path
        self.calls.append(command)
        assert command.stdout_path is not None
        if command.stdout_path.suffix == ".oct":
            command.stdout_path.write_bytes(b"fake octree")
        else:
            assert command.stdin_path is not None
            sensor_count = len(command.stdin_path.read_text(encoding="utf-8").splitlines())
            if command.stdout_path.stem.startswith("basis_control_zone_"):
                zone = int(command.stdout_path.stem.rsplit("_", maxsplit=1)[1])
                value = zone + 1
            else:
                value = 10
            command.stdout_path.write_text(
                f"{value} {value} {value}\n" * sensor_count,
                encoding="utf-8",
            )
        return RunnerResult(
            command_label=command.label,
            argv=command.argv,
            returncode=0,
            stdout_path=command.stdout_path,
            stderr_text="",
            stderr_path=None,
            wall_time_s=0.01,
            success=True,
        )


def cli_installation() -> RadianceInstallation:
    return RadianceInstallation(
        RadianceExecutableVersion("oconv", Path("/fake/oconv"), "fake"),
        RadianceExecutableVersion("rtrace", Path("/fake/rtrace"), "fake"),
    )


def prepare_solved_cli_workspace(
    root: Path,
    runner: CliFakeRunner,
    capsys,
) -> None:
    assert cli.main(
        [
            "smd-basis-smoke",
            "--workspace",
            str(root),
            "--room-ft",
            "10",
            "10",
            "--all-zones",
            "--execute",
        ],
        runner=runner,  # type: ignore[arg-type]
        radiance_installation=cli_installation(),
    ) == 0
    capsys.readouterr()
    assert cli.main(
        [
            "smd-basis-solve",
            "--workspace",
            str(root),
            "--target-ppfd",
            "10",
        ]
    ) == 0
    capsys.readouterr()


def test_smoke_cli_defaults_to_dry_run_without_execution(
    tmp_path: Path, capsys
) -> None:
    runner = CliFakeRunner()
    exit_code = cli.main(
        [
            "smd-basis-smoke",
            "--workspace",
            str(tmp_path / "basis"),
            "--room-ft",
            "10",
            "10",
            "--zone",
            "0",
        ],
        runner=runner,  # type: ignore[arg-type]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["dry_run"] is True
    assert payload["control_zone_index"] == 0
    assert payload["oconv_argv"][0] == "oconv"
    assert payload["rtrace_argv"][0] == "rtrace"
    assert runner.calls == []


def test_smoke_cli_execute_uses_injected_local_runner(
    tmp_path: Path, capsys
) -> None:
    runner = CliFakeRunner()
    exit_code = cli.main(
        [
            "smd-basis-smoke",
            "--workspace",
            str(tmp_path / "basis"),
            "--room-ft",
            "10",
            "10",
            "--zone",
            "0",
            "--execute",
        ],
        runner=runner,  # type: ignore[arg-type]
        radiance_installation=cli_installation(),
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["dry_run"] is False
    assert payload["execution"]["control_zone_index"] == 0
    assert len(runner.calls) == 2
    assert runner.calls[0].argv[0] == "/fake/oconv"
    assert runner.calls[1].argv[0] == "/fake/rtrace"


def test_smoke_cli_all_zones_dry_run_does_not_execute(
    tmp_path: Path, capsys
) -> None:
    runner = CliFakeRunner()
    exit_code = cli.main(
        [
            "smd-basis-smoke",
            "--workspace",
            str(tmp_path / "basis"),
            "--room-ft",
            "10",
            "10",
            "--all-zones",
        ],
        runner=runner,  # type: ignore[arg-type]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["dry_run"] is True
    assert payload["all_zones"] is True
    assert payload["control_zone_count"] == 5
    assert [column["control_zone_index"] for column in payload["columns"]] == list(
        range(5)
    )
    assert runner.calls == []


def test_smoke_cli_all_zones_execute_uses_injected_local_runner(
    tmp_path: Path, capsys
) -> None:
    runner = CliFakeRunner()
    exit_code = cli.main(
        [
            "smd-basis-smoke",
            "--workspace",
            str(tmp_path / "basis"),
            "--room-ft",
            "10",
            "10",
            "--all-zones",
            "--execute",
        ],
        runner=runner,  # type: ignore[arg-type]
        radiance_installation=cli_installation(),
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["dry_run"] is False
    assert payload["all_zones"] is True
    assert payload["execution"]["control_zone_count"] == 5
    assert payload["execution"]["matrix_shape"] == [441, 5]
    assert len(runner.calls) == 10
    assert [call.stdout_path.suffix for call in runner.calls] == [
        suffix for _ in range(5) for suffix in (".oct", ".rgb")
    ]
    matrix_path = Path(payload["execution"]["paths"]["matrix"])
    assert matrix_path.is_file()


def test_basis_solve_cli_uses_persisted_fake_workspace(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "basis"
    runner = CliFakeRunner()
    assert cli.main(
        [
            "smd-basis-smoke",
            "--workspace",
            str(root),
            "--room-ft",
            "10",
            "10",
            "--all-zones",
            "--execute",
        ],
        runner=runner,  # type: ignore[arg-type]
        radiance_installation=cli_installation(),
    ) == 0
    capsys.readouterr()

    exit_code = cli.main(
        [
            "smd-basis-solve",
            "--workspace",
            str(root),
            "--target-ppfd",
            "10",
            "--min-watts",
            "0",
            "--max-watts",
            "100",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["command"] == "smd-basis-solve"
    assert payload["success"] is True
    assert payload["mean_ppfd"] == 10.0
    assert len(payload["watts_by_control_zone"]) == 5
    assert Path(payload["artifacts"]["basis_solution"]).is_file()
    assert Path(payload["artifacts"]["optimized_module_schedule"]).is_file()
    assert Path(payload["artifacts"]["predicted_ppfd"]).is_file()
    assert Path(payload["artifacts"]["predicted_ppfd_metrics"]).is_file()


def test_composite_validation_cli_defaults_to_dry_run(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "basis"
    runner = CliFakeRunner()
    prepare_solved_cli_workspace(root, runner, capsys)
    call_count = len(runner.calls)

    exit_code = cli.main(
        [
            "smd-composite-validate",
            "--workspace",
            str(root),
        ],
        runner=runner,  # type: ignore[arg-type]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["command"] == "smd-composite-validate"
    assert payload["dry_run"] is True
    assert payload["module_count"] == 61
    assert payload["oconv_command"]["argv"][0] == "oconv"
    assert payload["rtrace_command"]["argv"][0] == "rtrace"
    assert "-I+" in payload["rtrace_command"]["argv"]
    assert Path(payload["emitter_path"]).is_file()
    assert len(runner.calls) == call_count


def test_composite_validation_cli_execute_uses_injected_runner(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "basis"
    runner = CliFakeRunner()
    prepare_solved_cli_workspace(root, runner, capsys)
    call_count = len(runner.calls)

    exit_code = cli.main(
        [
            "smd-composite-validate",
            "--workspace",
            str(root),
            "--execute",
        ],
        runner=runner,  # type: ignore[arg-type]
        radiance_installation=cli_installation(),
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["dry_run"] is False
    assert payload["success"] is True
    assert payload["comparison"]["actual"]["mean"] == 10.0
    assert len(runner.calls) == call_count + 2
    assert runner.calls[-2].label == "compile_final_composite"
    assert runner.calls[-1].label == "trace_final_composite"
    assert Path(payload["artifacts"]["actual_ppfd"]).is_file()
    assert Path(payload["artifacts"]["metrics"]).is_file()
    assert Path(payload["artifacts"]["comparison"]).is_file()
    assert Path(payload["artifacts"]["execution"]).is_file()


def test_rex_receiver_cli_defaults_to_dry_run(tmp_path: Path, capsys) -> None:
    root = tmp_path / "basis"
    runner = CliFakeRunner()
    prepare_solved_cli_workspace(root, runner, capsys)
    call_count = len(runner.calls)

    exit_code = cli.main(
        [
            "rex-receiver-smoke",
            "--workspace",
            str(root),
            "--seed",
            "1",
        ],
        runner=runner,  # type: ignore[arg-type]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["command"] == "rex-receiver-smoke"
    assert payload["dry_run"] is True
    assert payload["leaf_count"] == 32
    assert payload["patch_count"] == 512
    assert payload["receiver_count"] == 1024
    assert payload["oconv_command"]["argv"][0] == "oconv"
    assert payload["rtrace_command"]["argv"][0] == "rtrace"
    assert Path(payload["paths"]["plant"]).is_file()
    assert Path(payload["paths"]["receivers"]).is_file()
    assert len(runner.calls) == call_count


def test_rex_receiver_cli_execute_uses_injected_runner(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "basis"
    runner = CliFakeRunner()
    prepare_solved_cli_workspace(root, runner, capsys)
    call_count = len(runner.calls)

    exit_code = cli.main(
        [
            "rex-receiver-smoke",
            "--workspace",
            str(root),
            "--seed",
            "1",
            "--execute",
        ],
        runner=runner,  # type: ignore[arg-type]
        radiance_installation=cli_installation(),
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["dry_run"] is False
    assert payload["success"] is True
    assert payload["metrics"]["receiver_count"] == 1024
    assert len(runner.calls) == call_count + 2
    assert runner.calls[-2].label == "compile_rex_plant_receiver"
    assert runner.calls[-1].label == "plant_receiver_rtrace"
    assert Path(payload["artifacts"]["flux"]).is_file()
    assert Path(payload["artifacts"]["metrics"]).is_file()
    assert Path(payload["artifacts"]["execution"]).is_file()


def test_rex_five_band_receiver_cli_defaults_to_dry_run(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "basis"
    runner = CliFakeRunner()
    prepare_solved_cli_workspace(root, runner, capsys)
    call_count = len(runner.calls)

    exit_code = cli.main(
        [
            "rex-five-band-receiver-smoke",
            "--workspace",
            str(root),
        ],
        runner=runner,  # type: ignore[arg-type]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["command"] == "rex-five-band-receiver-smoke"
    assert payload["dry_run"] is True
    assert payload["band_order"] == ["blue", "green", "orange", "red", "far_red"]
    assert payload["threads"] == 6
    assert payload["receiver_count"] == 1024
    assert len(payload["bands"]) == 5
    assert all(item["oconv_command"]["stdout_mode"] == "binary" for item in payload["bands"])
    assert all(item["rtrace_command"]["stdout_mode"] == "text" for item in payload["bands"])
    assert len(runner.calls) == call_count


def test_rex_five_band_receiver_cli_executes_ten_injected_runner_calls(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "basis"
    runner = CliFakeRunner()
    prepare_solved_cli_workspace(root, runner, capsys)
    call_count = len(runner.calls)

    exit_code = cli.main(
        [
            "rex-five-band-receiver-smoke",
            "--workspace",
            str(root),
            "--threads",
            "4",
            "--execute",
        ],
        runner=runner,  # type: ignore[arg-type]
        radiance_installation=cli_installation(),
    )

    payload = json.loads(capsys.readouterr().out)
    calls = runner.calls[call_count:]
    assert exit_code == 0
    assert payload["dry_run"] is False
    assert payload["success"] is True
    assert payload["band_order"] == ["blue", "green", "orange", "red", "far_red"]
    assert len(calls) == 10
    assert [call.label for call in calls] == [
        label
        for band_id in ("blue", "green", "orange", "red", "far_red")
        for label in (
            f"compile_rex_{band_id}_receiver",
            f"trace_rex_{band_id}_receiver",
        )
    ]
    assert all(
        call.argv[call.argv.index("-n") + 1] == "4"
        for call in calls
        if call.argv[0] == "/fake/rtrace"
    )
    assert Path(payload["summary"]).is_file()
