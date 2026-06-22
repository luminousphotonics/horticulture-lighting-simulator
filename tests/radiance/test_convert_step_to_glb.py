from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.viewer import convert_step_to_glb


def test_parse_args_preserves_two_argument_conversion() -> None:
    args = convert_step_to_glb.parse_args(["input.step", "output.raw.glb"])

    assert args.input_step == Path("input.step")
    assert args.output_glb == Path("output.raw.glb")
    assert args.make_lods is False
    assert args.high_ratio == 0.45
    assert args.medium_ratio == 0.18
    assert args.proxy_ratio == 0.04


def test_parse_args_accepts_lod_generation_options() -> None:
    args = convert_step_to_glb.parse_args(
        [
            "fixture.step",
            "build/fixture.raw.glb",
            "--make-lods",
            "--viewer-dir",
            "src/rad_rebuild/web/static/viewer/proposed_led_system",
            "--asset-name",
            "fixture_centerpiece",
            "--high-ratio",
            "0.5",
            "--medium-ratio",
            "0.2",
            "--proxy-ratio",
            "0.05",
            "--gltfpack",
            "/opt/bin/gltfpack",
            "--compress-meshopt",
        ]
    )

    assert args.make_lods is True
    assert args.viewer_dir == Path("src/rad_rebuild/web/static/viewer/proposed_led_system")
    assert args.asset_name == "fixture_centerpiece"
    assert args.high_ratio == 0.5
    assert args.medium_ratio == 0.2
    assert args.proxy_ratio == 0.05
    assert args.gltfpack == "/opt/bin/gltfpack"
    assert args.compress_meshopt is True


def test_build_gltfpack_command_uses_expected_optimization_flags(tmp_path: Path) -> None:
    command = convert_step_to_glb.build_gltfpack_command(
        raw_glb=tmp_path / "fixture.raw.glb",
        output_glb=tmp_path / "fixture.high.glb",
        ratio=0.45,
        gltfpack="gltfpack",
        compress_meshopt=False,
    )

    assert command == [
        "gltfpack",
        "-i",
        str(tmp_path / "fixture.raw.glb"),
        "-o",
        str(tmp_path / "fixture.high.glb"),
        "-si",
        "0.45",
        "-kn",
        "-km",
        "-ke",
    ]
    assert "-cc" not in command


def test_generate_lods_invokes_gltfpack_for_high_medium_and_proxy(tmp_path: Path) -> None:
    raw_glb = tmp_path / "fixture.raw.glb"
    raw_glb.write_bytes(b"glb")
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    outputs = convert_step_to_glb.generate_lods(
        raw_glb,
        convert_step_to_glb.LodGenerationOptions(
            viewer_dir=tmp_path / "viewer",
            asset_name="fixture_centerpiece",
            gltfpack="mock-gltfpack",
        ),
        runner=runner,
    )

    assert set(outputs) == {"high", "medium", "proxy"}
    assert [call[0] for call in calls] == ["mock-gltfpack", "mock-gltfpack", "mock-gltfpack"]
    assert [call[call.index("-si") + 1] for call in calls] == ["0.45", "0.18", "0.04"]
    assert [Path(call[call.index("-o") + 1]).name for call in calls] == [
        "fixture_centerpiece.high.glb",
        "fixture_centerpiece.medium.glb",
        "fixture_centerpiece.proxy.glb",
    ]


def test_generate_lods_can_enable_meshopt_compression_warning(tmp_path: Path, capsys) -> None:
    raw_glb = tmp_path / "fixture.raw.glb"
    raw_glb.write_bytes(b"glb")
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    convert_step_to_glb.generate_lods(
        raw_glb,
        convert_step_to_glb.LodGenerationOptions(
            viewer_dir=tmp_path / "viewer",
            asset_name="fixture_centerpiece",
            compress_meshopt=True,
        ),
        runner=runner,
    )

    assert all("-cc" in call for call in calls)
    assert "GLTFLoader.setMeshoptDecoder" in capsys.readouterr().err


def test_main_two_argument_path_still_converts_only_raw_glb(tmp_path: Path, monkeypatch, capsys) -> None:
    input_step = tmp_path / "fixture.step"
    output_glb = tmp_path / "fixture.raw.glb"
    input_step.write_text("STEP", encoding="utf-8")
    converted: list[tuple[Path, Path]] = []

    def fake_convert(input_path: Path, output_path: Path) -> None:
        converted.append((input_path, output_path))
        output_path.write_bytes(b"raw")

    monkeypatch.setattr(convert_step_to_glb, "convert_step_to_glb", fake_convert)

    assert convert_step_to_glb.main([str(input_step), str(output_glb)]) == 0

    assert converted == [(input_step, output_glb)]
    assert capsys.readouterr().out.strip() == str(output_glb)
