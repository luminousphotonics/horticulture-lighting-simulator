from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.cli import scripts  # noqa: E402
from rad_rebuild.radiance.backend.artifacts import _live_workspace_sync_shell  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.config import MODE_SMD  # noqa: E402
from rad_rebuild.radiance.engine.emitters.smd_generation.outputs import write_smd_layout_json  # noqa: E402
from rad_rebuild.radiance.engine.plants.artifacts import (  # noqa: E402
    PLANT_ARTIFACT_FILENAMES,
    PLANT_CONFIG_FILENAME,
    PLANTS_MANIFEST_FILENAME,
    PLANTS_RAD_FILENAME,
)
from rad_rebuild.radiance.paths import REPO_ROOT  # noqa: E402


SHELL_ENTRYPOINTS: Mapping[str, str] = {
    "generate_precomputed_bundles.sh": "generate-precomputed-bundles",
    "generate_sensor_grid.sh": "generate-sensor-grid",
    "reproduce.sh": "reproduce",
    "run_basis_extraction.sh": "run-basis-extraction",
    "run_simulation_hps.sh": "run-simulation-hps",
    "run_simulation_smd.sh": "run-simulation-smd",
    "run_simulation_spydr3.sh": "run-simulation-spydr3",
    "run_uniformity.sh": "run-uniformity",
}


def _events(stderr: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stderr.splitlines() if line.startswith("{")]


def _base_env(tmp_path: Path) -> dict[str, str]:
    return {
        "PY": sys.executable,
        "RADIANCE_OUTPUT_ROOT": str(tmp_path),
        "RADIANCE_RUNTIME_STATE_ROOT": str(tmp_path / "runtime_state"),
        "RADIANCE_CACHE_ROOT": str(tmp_path / "cache"),
        "RADIANCE_BASIS_OUTPUT_ROOT": str(tmp_path / "basis"),
        "LOG_CAP_METRICS": "0",
    }


def _write_room_and_sensors(root: Path) -> None:
    (root / "room.rad").write_text("# room\n", encoding="utf-8")
    (root / "sensor_points.txt").write_text("0 0 0\n", encoding="utf-8")


def _write_ppfd(path: Path, value: float = 100.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"0 0 0 {value:.6f}\n", encoding="utf-8")


def _write_octree(
    _config: scripts.RuntimeConfig, _argv: Sequence[str], out_path: Path
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("octree\n", encoding="utf-8")
    return int(scripts.RadianceScriptExit.OK)


def test_public_python_subcommand_matrix_dispatches_every_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_args(name: str, exit_code: int) -> Any:
        def _fake(
            argv: Sequence[str] = (), raw_env: Mapping[str, str] | None = None
        ) -> int:
            assert raw_env is None
            calls.append((name, tuple(argv)))
            return exit_code

        return _fake

    def fake_noargs(name: str, exit_code: int) -> Any:
        def _fake(raw_env: Mapping[str, str] | None = None) -> int:
            assert raw_env is None
            calls.append((name, ()))
            return exit_code

        return _fake

    matrix = (
        (
            "generate-precomputed-bundles",
            ("custom", "--dataset-root", "/tmp/dataset"),
            "generate_precomputed_bundles",
            fake_args("generate-precomputed-bundles", 11),
            11,
        ),
        (
            "generate-sensor-grid",
            ("out with spaces.txt",),
            "generate_sensor_grid",
            fake_args("generate-sensor-grid", 12),
            12,
        ),
        ("reproduce", (), "reproduce", fake_noargs("reproduce", 13), 13),
        (
            "run-basis-extraction",
            (),
            "run_basis_extraction",
            fake_noargs("run-basis-extraction", 14),
            14,
        ),
        (
            "run-simulation-hps",
            (),
            "run_simulation_hps",
            fake_noargs("run-simulation-hps", 15),
            15,
        ),
        (
            "run-simulation-smd",
            (),
            "run_simulation_smd",
            fake_noargs("run-simulation-smd", 16),
            16,
        ),
        (
            "run-simulation-spydr3",
            (),
            "run_simulation_spydr3",
            fake_noargs("run-simulation-spydr3", 17),
            17,
        ),
        ("run-uniformity", (), "run_uniformity", fake_noargs("run-uniformity", 18), 18),
    )

    for command, extra_args, attr, fake, expected_exit in matrix:
        monkeypatch.setattr(scripts, attr, fake)
        assert scripts.main([command, *extra_args]) == expected_exit

    assert calls == [
        (command, tuple(extra_args)) for command, extra_args, *_rest in matrix
    ]


@pytest.mark.parametrize(("script_name", "command"), tuple(SHELL_ENTRYPOINTS.items()))
def test_shell_entrypoint_matrix_execs_python_cli_with_exact_argv(
    script_name: str,
    command: str,
    tmp_path: Path,
) -> None:
    stub = tmp_path / "python_stub.py"
    argv_log = tmp_path / "argv.json"
    stub.write_text(
        f"#!{sys.executable}\n"
        "from __future__ import annotations\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "open(os.environ['RAD_STUB_ARGV'], 'w', encoding='utf-8').write(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = os.environ.copy()
    env.update({"PY": str(stub), "RAD_STUB_ARGV": str(argv_log)})

    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts" / "radiance" / script_name),
            "--flag",
            "value with spaces",
            "",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_log.read_text(encoding="utf-8"))
    assert argv[:3] == ["-m", "rad_rebuild.radiance.cli.scripts", command]
    assert argv[-3:] == ["--flag", "value with spaces", ""]


def test_run_command_emits_success_and_propagates_child_exit(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    success = scripts._run_command(
        [sys.executable, "-c", "pass"], cwd=tmp_path, env=os.environ
    )
    failure = scripts._run_command(
        [sys.executable, "-c", "import sys; sys.exit(7)"], cwd=tmp_path, env=os.environ
    )

    assert success == int(scripts.RadianceScriptExit.OK)
    assert failure == 7
    events = _events(capsys.readouterr().err)
    assert events[0]["code"] == "command.start"
    assert events[1]["code"] == "command.succeeded"
    assert events[-1]["code"] == "command.failed"
    assert events[-1]["details"]["exit_code"] == 7
    assert events[-1]["details"]["propagated_exit_code"] == 7


def test_run_command_maps_signal_exit_to_shell_status(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[list[str]]:
        return subprocess.CompletedProcess(["fake"], -2)

    monkeypatch.setattr(scripts.subprocess, "run", fake_run)

    assert scripts._run_command(["fake"], cwd=tmp_path, env=os.environ) == 130
    event = _events(capsys.readouterr().err)[-1]
    assert event["details"] == {
        "argv": ["fake"],
        "exit_code": -2,
        "propagated_exit_code": 130,
    }


def test_run_command_missing_executable_is_validation_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "definitely-missing-tool"

    assert scripts._run_command([str(missing)], cwd=tmp_path, env=os.environ) == int(
        scripts.RadianceScriptExit.VALIDATION
    )
    event = _events(capsys.readouterr().err)[-1]
    assert event["code"] == "command.missing"
    assert event["details"] == {"command": str(missing)}


def test_capture_to_file_emits_success_and_preserves_stdout(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out_path = tmp_path / "captured output.txt"

    result = scripts._run_capture_to_file(
        [sys.executable, "-c", "print('captured')"],
        out_path,
        cwd=tmp_path,
        env=os.environ,
    )

    assert result == int(scripts.RadianceScriptExit.OK)
    assert out_path.read_text(encoding="utf-8") == "captured\n"
    event = _events(capsys.readouterr().err)[-1]
    assert event["code"] == "command.succeeded"
    assert event["details"]["stdout"] == str(out_path)


def test_trace_ppfd_missing_rtrace_is_validation_error(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = scripts._runtime_config({"RADIANCE_OUTPUT_ROOT": str(tmp_path)})
    monkeypatch.setattr(scripts.shutil, "which", lambda _name: None)

    result = scripts._trace_ppfd(
        config,
        octree=tmp_path / "scene.oct",
        dirs=tmp_path / "dirs.txt",
        snake_os=tmp_path / "sensors.txt",
        out_map=tmp_path / "ppfd_map.txt",
        oversample=1,
        nthreads=1,
        options=(),
        tag="missing",
    )

    assert result == int(scripts.RadianceScriptExit.VALIDATION)
    event = _events(capsys.readouterr().err)[-1]
    assert event["code"] == "command.missing"
    assert event["details"] == {"command": "rtrace"}


def test_trace_ppfd_uses_unique_rgb_temp_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env = _base_env(tmp_path)
    config = scripts._runtime_config(env)
    dirs = tmp_path / "dirs.txt"
    sensors = tmp_path / "sensors.txt"
    octree = tmp_path / "scene.oct"
    out_map = tmp_path / "ppfd_map.txt"
    dirs.write_text("0 0 1\n", encoding="utf-8")
    sensors.write_text("0 0 0\n", encoding="utf-8")
    octree.write_text("octree\n", encoding="utf-8")
    seen_rgb_paths: list[Path] = []

    def fake_run(*_args: Any, stdout: Any, **_kwargs: Any) -> subprocess.CompletedProcess[list[str]]:
        stdout.write("0.0 0.0 0.0\n")
        seen_rgb_paths.append(Path(stdout.name))
        return subprocess.CompletedProcess(["rtrace"], 0)

    def fake_aggregate(_snake_os: Path, rgb_path: Path, aggregate_out: Path, _oversample: int) -> None:
        seen_rgb_paths.append(rgb_path)
        aggregate_out.write_text("0 0 0 0\n", encoding="utf-8")

    monkeypatch.setattr(scripts.shutil, "which", lambda _name: "/usr/bin/rtrace")
    monkeypatch.setattr(scripts.subprocess, "run", fake_run)
    monkeypatch.setattr(scripts, "_aggregate_rgb_to_ppfd", fake_aggregate)
    monkeypatch.setattr(scripts.secrets, "token_hex", lambda _size: "abcdef123456")

    result = scripts._trace_ppfd(
        config,
        octree=octree,
        dirs=dirs,
        snake_os=sensors,
        out_map=out_map,
        oversample=1,
        nthreads=1,
        options=(),
        tag="smd",
    )

    assert result == int(scripts.RadianceScriptExit.OK)
    assert seen_rgb_paths
    assert all(path.name.startswith(".rgb_tmp_smd_") for path in seen_rgb_paths)
    assert all(path.name != ".rgb_tmp_smd.txt" for path in seen_rgb_paths)
    assert not seen_rgb_paths[-1].exists()


def test_basis_sampling_grid_requires_manifest(tmp_path: Path) -> None:
    basis_dir = tmp_path / "basis"
    basis_dir.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "RADIANCE_OUTPUT_ROOT": str(tmp_path),
            "RADIANCE_BASIS_OUTPUT_ROOT": str(basis_dir),
            "BASIS_PATH": str(basis_dir / "basis_A.npy"),
        }
    )
    config = scripts._build_uniformity_config(env)
    np.save(config.basis_path, np.ones((1, 1), dtype=float))

    with pytest.raises(ValueError, match="basis manifest missing"):
        scripts._validate_basis_sampling_grid(config)


def test_precomputed_generation_requires_explicit_dataset_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = scripts.generate_precomputed_bundles(
        ["custom", "--modes", "SMD"],
        {"RADIANCE_OUTPUT_ROOT": str(tmp_path), "RADIANCE_PRECOMPUTED_ROOT": ""},
    )

    assert result == int(scripts.RadianceScriptExit.USAGE)
    assert (
        "Set RADIANCE_PRECOMPUTED_ROOT or pass --dataset-root."
        in capsys.readouterr().err
    )


def test_empty_optional_uniformity_values_normalize_to_ring_mode(
    tmp_path: Path,
) -> None:
    config = scripts._build_uniformity_config(
        {
            "RADIANCE_OUTPUT_ROOT": str(tmp_path),
            "RADIANCE_BASIS_OUTPUT_ROOT": str(tmp_path / "basis"),
            "SMD_VAR_MODE": "",
            "SMD_ALL_PER_MODULE": "",
            "SMD_OUTER_PER_MODULE": "",
        }
    )

    assert config.smd_var_mode == ""
    assert config.desired_var_mode == "rings"
    assert "--smooth-groups" not in scripts.build_uniformity_solver_argv(config, {})


def test_smd_simulation_orchestration_succeeds_with_stubbed_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env = _base_env(tmp_path)
    env["FSPM_RECEIVER_GRANULARITY"] = "mesh_patch"
    octree_argvs: list[tuple[str, ...]] = []

    def fake_python_module(
        config: scripts.RuntimeConfig,
        module: str,
        args: Sequence[str] = (),
        extra_env: Mapping[str, str] | None = None,
    ) -> int:
        assert module == "rad_rebuild.radiance.engine.emitters.generate_emitters_smd"
        assert args == ()
        assert extra_env is not None
        (config.runtime_state_root / "emitters_smd_ALL_umol.rad").write_text(
            "# emitters\n", encoding="utf-8"
        )
        return int(scripts.RadianceScriptExit.OK)

    def fake_prepare_static_scene(
        _config: scripts.RuntimeConfig,
        *,
        room: Path,
        sensors: Path,
        reuse: bool,
    ) -> int:
        assert reuse is False
        room.write_text("# room\n", encoding="utf-8")
        sensors.write_text("0 0 0\n", encoding="utf-8")
        return int(scripts.RadianceScriptExit.OK)

    def fake_build_octree(
        _config: scripts.RuntimeConfig, argv: Sequence[str], out_path: Path
    ) -> int:
        assert argv[0] == "-f"
        octree_argvs.append(tuple(argv))
        out_path.write_text("octree\n", encoding="utf-8")
        return int(scripts.RadianceScriptExit.OK)

    def fake_trace_ppfd(
        _config: scripts.RuntimeConfig,
        *,
        octree: Path,
        dirs: Path,
        snake_os: Path,
        out_map: Path,
        oversample: int,
        nthreads: int,
        options: Sequence[str],
        tag: str,
    ) -> int:
        assert octree.name == "smd_scene.oct"
        assert dirs.name.startswith("dirs_tmp_os_")
        assert snake_os.name.startswith("sensors_snake_os_")
        assert oversample == 4
        assert nthreads >= 1
        assert options
        assert tag == "smd"
        _write_ppfd(out_map)
        return int(scripts.RadianceScriptExit.OK)

    def fake_symmetrize(
        _config: scripts.RuntimeConfig,
        *,
        in_map: Path,
        out_map: Path,
        enabled: bool,
        axes_only: bool,
    ) -> int:
        assert in_map == out_map
        assert enabled is True
        assert axes_only is False
        return int(scripts.RadianceScriptExit.OK)

    monkeypatch.setattr(scripts, "_run_python_module", fake_python_module)
    monkeypatch.setattr(scripts, "_prepare_static_scene", fake_prepare_static_scene)
    monkeypatch.setattr(scripts, "_build_octree", fake_build_octree)
    monkeypatch.setattr(scripts, "_trace_ppfd", fake_trace_ppfd)
    monkeypatch.setattr(scripts, "_symmetrize_if_requested", fake_symmetrize)

    assert scripts.run_simulation_smd(env) == int(scripts.RadianceScriptExit.OK)
    assert (tmp_path / "ppfd_map.txt").is_file()
    assert octree_argvs
    assert all(PLANTS_RAD_FILENAME not in part for part in octree_argvs[0])
    assert not (tmp_path / "runtime_state" / PLANTS_RAD_FILENAME).exists()


def test_smd_simulation_includes_plants_only_when_gate_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env = _base_env(tmp_path)
    env.update(
        {
            "FSPM_PLANTS_ENABLED": "1",
            "FSPM_PLANT_SEED": "99",
            "FSPM_PLANT_ROWS": "1",
            "FSPM_PLANT_COLUMNS": "1",
            "MODE": "direct",
        }
    )
    octree_argvs: list[tuple[str, ...]] = []
    plant_receiver_calls: list[Path] = []
    receiver_sample_counts: list[int] = []

    def fake_python_module(
        config: scripts.RuntimeConfig,
        module: str,
        args: Sequence[str] = (),
        extra_env: Mapping[str, str] | None = None,
    ) -> int:
        assert module == "rad_rebuild.radiance.engine.emitters.generate_emitters_smd"
        assert args == ()
        assert extra_env is not None
        (config.runtime_state_root / "emitters_smd_ALL_umol.rad").write_text(
            "# emitters\n", encoding="utf-8"
        )
        return int(scripts.RadianceScriptExit.OK)

    def fake_prepare_static_scene(
        _config: scripts.RuntimeConfig,
        *,
        room: Path,
        sensors: Path,
        reuse: bool,
    ) -> int:
        assert reuse is False
        room.write_text("# room\n", encoding="utf-8")
        sensors.write_text("0 0 0\n", encoding="utf-8")
        return int(scripts.RadianceScriptExit.OK)

    def fake_build_octree(
        _config: scripts.RuntimeConfig,
        argv: Sequence[str],
        out_path: Path,
    ) -> int:
        octree_argvs.append(tuple(argv))
        out_path.write_text("octree\n", encoding="utf-8")
        return int(scripts.RadianceScriptExit.OK)

    def fake_trace_ppfd(
        _config: scripts.RuntimeConfig,
        *,
        octree: Path,
        dirs: Path,
        snake_os: Path,
        out_map: Path,
        oversample: int,
        nthreads: int,
        options: Sequence[str],
        tag: str,
    ) -> int:
        del octree, dirs, snake_os, oversample, nthreads, options, tag
        _write_ppfd(out_map)
        return int(scripts.RadianceScriptExit.OK)

    def fake_trace_plant_receivers(
        _config: scripts.RuntimeConfig,
        *,
        receiver_input_path: Path,
        receiver_rgb_path: Path,
        octree: Path,
        options: Sequence[str],
        nthreads: int,
    ) -> int:
        assert octree.name == "smd_fspm_receiver.oct"
        assert options
        assert nthreads == 1
        plant_receiver_calls.append(octree)
        sample_count = len(receiver_input_path.read_text(encoding="utf-8").splitlines())
        receiver_sample_counts.append(sample_count)
        receiver_rgb_path.write_text(
            "".join("1 1 1\n" for _ in range(sample_count)),
            encoding="utf-8",
        )
        return int(scripts.RadianceScriptExit.OK)

    def fake_symmetrize(
        _config: scripts.RuntimeConfig,
        *,
        in_map: Path,
        out_map: Path,
        enabled: bool,
        axes_only: bool,
    ) -> int:
        assert in_map == out_map
        assert enabled is True
        assert axes_only is False
        return int(scripts.RadianceScriptExit.OK)

    monkeypatch.setattr(scripts, "_run_python_module", fake_python_module)
    monkeypatch.setattr(scripts, "_prepare_static_scene", fake_prepare_static_scene)
    monkeypatch.setattr(scripts, "_build_octree", fake_build_octree)
    monkeypatch.setattr(scripts, "_trace_ppfd", fake_trace_ppfd)
    monkeypatch.setattr(scripts, "_trace_plant_surface_receivers", fake_trace_plant_receivers)
    monkeypatch.setattr(scripts, "_symmetrize_if_requested", fake_symmetrize)

    assert scripts.run_simulation_smd(env) == int(scripts.RadianceScriptExit.OK)

    runtime = tmp_path / "runtime_state"
    plant_rad = runtime / PLANTS_RAD_FILENAME
    assert plant_rad.is_file()
    assert (runtime / PLANT_CONFIG_FILENAME).is_file()
    manifest = json.loads((runtime / PLANTS_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["active_simulation_integration"] is True
    assert manifest["config"]["seed"] == 99
    assert manifest["config"]["plant_grid_rows"] == 1
    assert len(octree_argvs) == 2
    assert octree_argvs[0] == (
        "-f",
        str(tmp_path / "room.rad"),
        str(runtime / "emitters_smd_ALL_umol.rad"),
    )
    assert octree_argvs[1] == (
        "-f",
        str(tmp_path / "room.rad"),
        str(runtime / "emitters_smd_ALL_umol.rad"),
        str(plant_rad),
    )
    assert plant_receiver_calls == [tmp_path / "cache" / "smd_fspm_receiver.oct"]
    surface_flux = json.loads(
        (runtime / "plant_surface_flux.json").read_text(encoding="utf-8")
    )
    assert receiver_sample_counts == [surface_flux["leaf_count"] * 4]
    assert surface_flux["baseline_transport_scene"] == "room_emitters_only"
    assert surface_flux["fspm_receiver_transport_scene"] == "room_emitters_plants"
    assert surface_flux["receiver_trace_count"] == 1
    assert surface_flux["receiver_granularity"] == "leaf_quadrature_4"
    assert surface_flux["receiver_granularity_role"] == "development_demo_default"
    assert surface_flux["receiver_sample_count"] == surface_flux["leaf_count"] * 4
    assert surface_flux["receiver_samples_per_leaf"] == pytest.approx(4.0)


def test_static_room_octree_keeps_baseline_and_fspm_receiver_inputs_separate(
    tmp_path: Path,
) -> None:
    room = tmp_path / "room.rad"
    emitter = tmp_path / "emitters.rad"
    plant = tmp_path / "plants.rad"
    static_room_oct = tmp_path / "static_room.oct"
    static_room_oct.write_text("octree\n", encoding="utf-8")

    assert scripts._octree_scene_inputs(room=room, emitter_file=emitter) == [
        "-f",
        str(room),
        str(emitter),
    ]
    assert scripts._octree_scene_inputs(
        room=room,
        emitter_file=emitter,
        plant_rad=plant,
        static_room_oct=static_room_oct,
    ) == ["-f", "-i", str(static_room_oct), str(emitter)]
    assert scripts._fspm_receiver_scene_inputs(
        room=room,
        emitter_file=emitter,
        plant_rad=plant,
        static_room_oct=static_room_oct,
    ) == ["-f", "-i", str(static_room_oct), str(emitter), str(plant)]


def test_live_workspace_sync_shell_optionally_copies_plant_artifacts(
    tmp_path: Path,
) -> None:
    req = RadianceRunRequest(
        action="all",
        mode=MODE_SMD,
        execution_mode="live_local",
        length_ft=10,
        width_ft=10,
        target_ppfd=1000,
    )
    command = _live_workspace_sync_shell(
        req,
        tmp_path / "workspace",
        include_visuals=False,
    )

    for filename in PLANT_ARTIFACT_FILENAMES:
        assert f"runtime_state/{filename}" in command


def test_hps_simulation_orchestration_succeeds_with_stubbed_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env = _base_env(tmp_path)
    env.update({"TARGET_PPFD": "", "MODE": "direct"})

    def fake_python_module_to_file(
        config: scripts.RuntimeConfig,
        module: str,
        out_path: Path,
        args: Sequence[str] = (),
        extra_env: Mapping[str, str] | None = None,
    ) -> int:
        assert module == "rad_rebuild.radiance.engine.geometry.generate_room"
        assert args == ()
        assert extra_env is None
        out_path.write_text("# room\n", encoding="utf-8")
        (config.root / "sensor_points.txt").parent.mkdir(parents=True, exist_ok=True)
        return int(scripts.RadianceScriptExit.OK)

    def fake_generate_sensor_grid(
        argv: Sequence[str] = (), raw_env: Mapping[str, str] | None = None
    ) -> int:
        assert argv
        assert raw_env is not None
        Path(argv[0]).write_text("0 0 0\n", encoding="utf-8")
        return int(scripts.RadianceScriptExit.OK)

    def fake_python_module(
        config: scripts.RuntimeConfig,
        module: str,
        args: Sequence[str] = (),
        extra_env: Mapping[str, str] | None = None,
    ) -> int:
        assert module == "rad_rebuild.radiance.engine.emitters.generate_emitters_hps"
        assert args == ()
        assert extra_env is not None
        config.runtime_state_root.mkdir(parents=True, exist_ok=True)
        (config.runtime_state_root / "emitters_hps_ALL_umol.rad").write_text(
            "# hps\n", encoding="utf-8"
        )
        (config.runtime_state_root / "hps_layout.json").write_text(
            json.dumps({"fixtures": [{}]}), encoding="utf-8"
        )
        return int(scripts.RadianceScriptExit.OK)

    def fake_trace_ppfd(
        _config: scripts.RuntimeConfig,
        *,
        octree: Path,
        dirs: Path,
        snake_os: Path,
        out_map: Path,
        oversample: int,
        nthreads: int,
        options: Sequence[str],
        tag: str,
    ) -> int:
        assert octree.name == "hps_scene.oct"
        assert dirs.is_file()
        assert snake_os.is_file()
        assert oversample == 4
        assert nthreads == 1
        assert options
        assert "-af" in options
        amb_path = Path(options[options.index("-af") + 1])
        assert amb_path.name.startswith("amb_hps_p1_")
        assert amb_path.name != "amb_p1"
        assert tag == "p1"
        _write_ppfd(out_map, 250.0)
        return int(scripts.RadianceScriptExit.OK)

    def fake_symmetrize(
        _config: scripts.RuntimeConfig,
        *,
        in_map: Path,
        out_map: Path,
        enabled: bool,
        axes_only: bool,
    ) -> int:
        assert enabled is True
        assert axes_only is False
        out_map.write_text(in_map.read_text(encoding="utf-8"), encoding="utf-8")
        return int(scripts.RadianceScriptExit.OK)

    monkeypatch.setattr(
        scripts, "_run_python_module_to_file", fake_python_module_to_file
    )
    monkeypatch.setattr(scripts, "generate_sensor_grid", fake_generate_sensor_grid)
    monkeypatch.setattr(scripts, "_run_python_module", fake_python_module)
    monkeypatch.setattr(scripts, "_build_octree", _write_octree)
    monkeypatch.setattr(scripts, "_trace_ppfd", fake_trace_ppfd)
    monkeypatch.setattr(scripts, "_symmetrize_if_requested", fake_symmetrize)

    assert scripts.run_simulation_hps(env) == int(scripts.RadianceScriptExit.OK)
    assert (tmp_path / "ppfd_map.txt").read_text(
        encoding="utf-8"
    ) == "0 0 0 250.000000\n"


def test_spydr_simulation_orchestration_succeeds_with_stubbed_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env = _base_env(tmp_path)
    env.update({"TARGET_PPFD": "120", "AUTO_DIM": "1", "AUTO_DIM_MODE": "scale"})

    def fake_python_module_to_file(
        _config: scripts.RuntimeConfig,
        module: str,
        out_path: Path,
        args: Sequence[str] = (),
        extra_env: Mapping[str, str] | None = None,
    ) -> int:
        assert module == "rad_rebuild.radiance.engine.geometry.generate_room"
        assert args == ()
        assert extra_env is None
        out_path.write_text("# room\n", encoding="utf-8")
        return int(scripts.RadianceScriptExit.OK)

    def fake_generate_sensor_grid(
        argv: Sequence[str] = (), raw_env: Mapping[str, str] | None = None
    ) -> int:
        assert argv
        assert raw_env is not None
        Path(argv[0]).write_text("0 0 0\n", encoding="utf-8")
        return int(scripts.RadianceScriptExit.OK)

    def fake_python_module(
        config: scripts.RuntimeConfig,
        module: str,
        args: Sequence[str] = (),
        extra_env: Mapping[str, str] | None = None,
    ) -> int:
        assert module == "rad_rebuild.radiance.engine.emitters.generate_emitters_spydr3"
        assert args == ()
        assert extra_env is not None
        config.runtime_state_root.mkdir(parents=True, exist_ok=True)
        (config.runtime_state_root / "emitters_spydr3_ALL_umol.rad").write_text(
            "# spydr\n", encoding="utf-8"
        )
        (config.runtime_state_root / "spydr3_layout.json").write_text(
            json.dumps({"fixtures": [{}, {}], "nx": 1, "ny": 2, "layout_mode": "full"}),
            encoding="utf-8",
        )
        return int(scripts.RadianceScriptExit.OK)

    def fake_trace_ppfd(
        _config: scripts.RuntimeConfig,
        *,
        octree: Path,
        dirs: Path,
        snake_os: Path,
        out_map: Path,
        oversample: int,
        nthreads: int,
        options: Sequence[str],
        tag: str,
    ) -> int:
        assert octree.name == "spydr_scene.oct"
        assert dirs.is_file()
        assert snake_os.is_file()
        assert oversample == 4
        assert nthreads >= 1
        assert options
        assert "-af" in options
        amb_path = Path(options[options.index("-af") + 1])
        assert amb_path.name.startswith("amb_spydr_p1_")
        assert amb_path.name != "amb_p1"
        assert tag == "p1"
        _write_ppfd(out_map, 100.0)
        return int(scripts.RadianceScriptExit.OK)

    def fake_symmetrize(
        _config: scripts.RuntimeConfig,
        *,
        in_map: Path,
        out_map: Path,
        enabled: bool,
        axes_only: bool,
    ) -> int:
        assert enabled is False
        assert axes_only is False
        out_map.write_text(in_map.read_text(encoding="utf-8"), encoding="utf-8")
        return int(scripts.RadianceScriptExit.OK)

    monkeypatch.setattr(
        scripts, "_run_python_module_to_file", fake_python_module_to_file
    )
    monkeypatch.setattr(scripts, "generate_sensor_grid", fake_generate_sensor_grid)
    monkeypatch.setattr(scripts, "_run_python_module", fake_python_module)
    monkeypatch.setattr(scripts, "_build_octree", _write_octree)
    monkeypatch.setattr(scripts, "_trace_ppfd", fake_trace_ppfd)
    monkeypatch.setattr(scripts, "_symmetrize_if_requested", fake_symmetrize)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cache" / "amb_spydr_p1_stale").write_text("bad ambient\n", encoding="utf-8")

    assert scripts.run_simulation_spydr3(env) == int(scripts.RadianceScriptExit.OK)
    assert (tmp_path / "ppfd_map.txt").read_text(
        encoding="utf-8"
    ) == "0 0 0 100.000000\n"
    assert not (tmp_path / "cache" / "amb_spydr_p1_stale").exists()


def test_basis_extraction_builds_manifest_with_stubbed_ring_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env = _base_env(tmp_path)
    env.update({"MODE": "direct", "OS": "1", "SMD_MODEL": "legacy"})

    def fake_positions_from_env() -> tuple[
        list[dict[str, float]], None, dict[str, int]
    ]:
        return (
            [{"x": 0.0, "y": 0.0, "z": 0.0, "ring": 0.0}],
            None,
            {"rings": 1, "ring_n": 0},
        )

    def fake_python_module_to_file(
        _config: scripts.RuntimeConfig,
        _module: str,
        out_path: Path,
        args: Sequence[str] = (),
        extra_env: Mapping[str, str] | None = None,
    ) -> int:
        assert args == ()
        assert extra_env is None
        out_path.write_text("# generated\n", encoding="utf-8")
        return int(scripts.RadianceScriptExit.OK)

    def fake_generate_sensor_grid(
        argv: Sequence[str] = (), raw_env: Mapping[str, str] | None = None
    ) -> int:
        assert argv
        assert raw_env is not None
        Path(argv[0]).write_text("0 0 0\n", encoding="utf-8")
        return int(scripts.RadianceScriptExit.OK)

    def fake_run_simulation_smd(raw_env: Mapping[str, str] | None = None) -> int:
        assert raw_env is not None
        (Path(raw_env["RADIANCE_OUTPUT_ROOT"]) / "ppfd_map.txt").write_text(
            "0 0 0 1.000000\n0 1 0 2.000000\n",
            encoding="utf-8",
        )
        return int(scripts.RadianceScriptExit.OK)

    monkeypatch.setattr(scripts, "_compute_positions_from_env", fake_positions_from_env)
    monkeypatch.setattr(
        scripts, "_run_python_module_to_file", fake_python_module_to_file
    )
    monkeypatch.setattr(scripts, "generate_sensor_grid", fake_generate_sensor_grid)
    monkeypatch.setattr(scripts, "_build_octree", _write_octree)
    monkeypatch.setattr(scripts, "run_simulation_smd", fake_run_simulation_smd)

    assert scripts.run_basis_extraction(env) == int(scripts.RadianceScriptExit.OK)
    manifest = json.loads(
        (tmp_path / "basis" / "basis_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["variables"] == "rings"
    assert manifest["n_vars"] == 1


def test_reproduce_orchestration_collects_versions_and_hashes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "artifacts"
    env = _base_env(tmp_path)
    env.update({"OUT_DIR": str(out_dir), "RUN_BASIS": "0"})

    def fake_run_uniformity(raw_env: Mapping[str, str] | None = None) -> int:
        assert raw_env is not None
        root = Path(raw_env["RADIANCE_OUTPUT_ROOT"])
        basis_root = Path(raw_env["RADIANCE_BASIS_OUTPUT_ROOT"])
        runtime_root = Path(raw_env["RADIANCE_RUNTIME_STATE_ROOT"])
        for path in (root, basis_root, runtime_root):
            path.mkdir(parents=True, exist_ok=True)
        _write_ppfd(root / "ppfd_map.txt", 123.0)
        (root / "ring_powers_optimized.json").write_text("{}", encoding="utf-8")
        np.save(basis_root / "basis_A.npy", np.ones((1, 1), dtype=float))
        (basis_root / "basis_manifest.json").write_text("{}", encoding="utf-8")
        (runtime_root / "smd_summary.txt").write_text("summary\n", encoding="utf-8")
        (runtime_root / "smd_layout.json").write_text("{}", encoding="utf-8")
        return int(scripts.RadianceScriptExit.OK)

    def fake_python_module(
        _config: scripts.RuntimeConfig,
        module: str,
        args: Sequence[str] = (),
        extra_env: Mapping[str, str] | None = None,
    ) -> int:
        assert module == "rad_rebuild.radiance.engine.visualization.visualize_ppfd"
        assert args[-2:] == ["--outdir", str(out_dir / "ppfd_visualizations_proposed")]
        assert extra_env is None
        return int(scripts.RadianceScriptExit.OK)

    def fake_command_stdout(
        argv: Sequence[str], *, cwd: Path, env: Mapping[str, str]
    ) -> str:
        assert cwd == REPO_ROOT
        assert env
        if argv[:2] == ["git", "rev-parse"]:
            return "abcdef\n"
        if argv[:3] == [sys.executable, "-m", "pip"]:
            return "rad-rebuild==0.1\n"
        return "Radiance 5.4\n"

    monkeypatch.setattr(scripts, "run_uniformity", fake_run_uniformity)
    monkeypatch.setattr(scripts, "_run_python_module", fake_python_module)
    monkeypatch.setattr(scripts, "_command_stdout", fake_command_stdout)

    assert scripts.reproduce(env) == int(scripts.RadianceScriptExit.OK)
    assert (out_dir / "hashes.txt").is_file()
    assert (
        json.loads((out_dir / "versions.json").read_text(encoding="utf-8"))["git"]
        == "abcdef"
    )


def test_smd_layout_timestamp_is_timezone_aware_utc_with_legacy_z_suffix(
    tmp_path: Path,
) -> None:
    layout_path = tmp_path / "smd_layout.json"

    write_smd_layout_json(
        layout_path,
        positions=[{"x": 0.0, "y": 0.0, "z": 0.0, "ring": 0}],
        spacing_m=1.0,
        room_L_m=3.0,
        room_W_m=3.0,
        margin_m=0.1,
        ring_n=0,
        patch_side_m=0.2,
    )

    timestamp = json.loads(layout_path.read_text(encoding="utf-8"))["timestamp"]
    assert timestamp.endswith("Z")
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    offset = parsed.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 0
