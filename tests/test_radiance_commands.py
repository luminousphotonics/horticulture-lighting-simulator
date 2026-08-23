from __future__ import annotations

from pathlib import Path

import pytest

from fspm_optics.radiance.commands import (
    LOCAL_DEFAULT_NTHREADS,
    CommandSpec,
    build_baseline_rtrace_command,
    build_oconv_argv,
    build_oconv_command,
    build_plant_receiver_rtrace_command,
    validate_command_paths,
)


def test_command_spec_preserves_argv_and_metadata() -> None:
    argv = ("tool", "--flag", "value with spaces")
    spec = CommandSpec(
        argv=argv,
        stdin_path=Path("input.txt"),
        stdout_path=Path("output.txt"),
        cwd=Path("work"),
        env={"MODE": "local"},
        label="example",
    )
    assert spec.argv == argv
    assert spec.stdin_path == Path("input.txt")
    assert spec.stdout_path == Path("output.txt")
    assert spec.cwd == Path("work")
    assert spec.env == {"MODE": "local"}
    assert spec.label == "example"


def test_oconv_argv_and_command_are_deterministic(tmp_path: Path) -> None:
    room = tmp_path / "room.rad"
    emitters = tmp_path / "emitters.rad"
    output = tmp_path / "scene.oct"
    expected = ("oconv", "-f", str(room), str(emitters))
    assert build_oconv_argv((room, emitters)) == expected
    assert build_oconv_argv((room, emitters)) == expected
    spec = build_oconv_command((room, emitters), output_octree=output)
    assert spec.argv == expected
    assert spec.stdout_path == output
    assert spec.stdout_mode == "binary"


def test_baseline_rtrace_uses_six_threads_by_default(tmp_path: Path) -> None:
    spec = build_baseline_rtrace_command(
        octree=tmp_path / "baseline.oct",
        receiver_input=tmp_path / "rays.txt",
        rgb_output=tmp_path / "baseline.rgb",
        options=("-ab", "3", "-aa", "0.22"),
    )
    assert LOCAL_DEFAULT_NTHREADS == 6
    assert spec.argv == (
        "rtrace",
        "-h",
        "-I+",
        "-n",
        "6",
        "-ab",
        "3",
        "-aa",
        "0.22",
        str(tmp_path / "baseline.oct"),
    )


def test_explicit_nthreads_overrides_local_default(tmp_path: Path) -> None:
    spec = build_plant_receiver_rtrace_command(
        octree=tmp_path / "receiver.oct",
        receiver_input=tmp_path / "receivers.txt",
        rgb_output=tmp_path / "receiver.rgb",
        nthreads=2,
    )
    assert spec.argv[:5] == ("rtrace", "-h", "-I+", "-n", "2")
    assert spec.label == "plant_receiver_rtrace"
    assert spec.stdout_mode == "text"


def test_path_validation_is_read_only_and_clear(tmp_path: Path) -> None:
    stdin = tmp_path / "rays.txt"
    stdin.write_text("0 0 0 0 0 1\n", encoding="utf-8")
    output = tmp_path / "trace.rgb"
    spec = build_baseline_rtrace_command(
        octree=tmp_path / "scene.oct",
        receiver_input=stdin,
        rgb_output=output,
    )
    assert validate_command_paths(spec) is spec
    assert not output.exists()

    missing = CommandSpec(argv=("tool",), stdin_path=tmp_path / "missing.txt", label="test")
    with pytest.raises(FileNotFoundError, match="test stdin not found"):
        validate_command_paths(missing)


def test_builders_do_not_execute(tmp_path: Path) -> None:
    output = tmp_path / "never_created.oct"
    spec = build_oconv_command(("room.rad", "emitters.rad"), output_octree=output)
    assert not output.exists()
    assert spec.argv[0] == "oconv"


def test_command_boundary_has_no_process_execution_import() -> None:
    package_root = Path(__file__).parents[1] / "src" / "fspm_optics" / "radiance"
    assert "subprocess" not in (package_root / "commands.py").read_text(encoding="utf-8")
