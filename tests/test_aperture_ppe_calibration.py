from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import fspm_optics.fixtures.smd.aperture_ppe_calibration as calibration
from fspm_optics.fixtures.smd.aperture_ppe_calibration import (
    APERTURE_AREA_M2,
    APERTURE_SIDE_M,
    AperturePpeCalibrationError,
    CalibrationConfig,
    aperture_flux_from_rgb,
    build_calibration_actions,
    build_parser,
    calibration_scene_texts,
    check_options,
    evaluate_observations,
    execute_calibration,
    main,
    parse_equal_rgb,
    unit_emitter_radiance,
)
from fspm_optics.fixtures.smd.optical_stack import (
    ACCEPTED_FIXTURE_TRANSMISSION,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    INTERNAL_SOURCE_PPE_UMOL_PER_J,
    PROPOSED_FIXTURE_OPTICAL_STACK_ID,
    proposed_fixture_materials_radiance_text,
    proposed_fixture_stack_radiance_text,
)
from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.runner import RunnerResult


def _polygon_vertices(
    document: str,
    primitive_name: str,
) -> tuple[tuple[float, float, float], ...]:
    lines = document.splitlines()
    index = next(
        index
        for index, line in enumerate(lines)
        if line.endswith(f" polygon {primitive_name}")
    )
    coordinate_count = int(lines[index + 3])
    coordinates = [
        float(token)
        for line in lines[index + 4 : index + 4 + coordinate_count // 3]
        for token in line.split()
    ]
    return tuple(
        tuple(coordinates[index : index + 3])  # type: ignore[arg-type]
        for index in range(0, len(coordinates), 3)
    )


def _normal(
    vertices: tuple[tuple[float, float, float], ...],
) -> tuple[float, float, float]:
    first, second, third = vertices[:3]
    edge_a = tuple(second[index] - first[index] for index in range(3))
    edge_b = tuple(third[index] - first[index] for index in range(3))
    cross = (
        edge_a[1] * edge_b[2] - edge_a[2] * edge_b[1],
        edge_a[2] * edge_b[0] - edge_a[0] * edge_b[2],
        edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0],
    )
    magnitude = math.sqrt(sum(value * value for value in cross))
    return tuple(value / magnitude for value in cross)


class _FakeRfluxmtxRunner:
    def __init__(self, fluxes: dict[str, float]) -> None:
        self.fluxes = fluxes
        self.commands: list[CommandSpec] = []

    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult:
        del timeout_s
        self.commands.append(command)
        key = command.label.removeprefix("aperture-ppe-")
        flux = self.fluxes[key]
        coefficient = flux / (math.pi * APERTURE_AREA_M2)
        assert command.stdout_path is not None
        command.stdout_path.write_text(
            f"{coefficient} {coefficient} {coefficient}\n",
            encoding="utf-8",
        )
        resolved_stderr = Path(stderr_path) if stderr_path is not None else None
        if resolved_stderr is not None:
            resolved_stderr.write_text("", encoding="utf-8")
        return RunnerResult(
            command_label=command.label,
            argv=command.argv,
            returncode=0,
            stdout_path=command.stdout_path,
            stderr_text=None,
            stderr_path=resolved_stderr,
            wall_time_s=0.25,
            success=True,
        )


def test_unit_lambertian_flux_and_pi_area_conversion_are_exact() -> None:
    radiance = unit_emitter_radiance()
    assert math.pi * 0.120**2 * radiance == pytest.approx(1.0, rel=1.0e-15)
    coefficient = 1.0 / (math.pi * APERTURE_AREA_M2)
    rgb = parse_equal_rgb(f"{coefficient} {coefficient} {coefficient}")
    assert aperture_flux_from_rgb(rgb) == pytest.approx(1.0, rel=1.0e-15)
    assert APERTURE_SIDE_M == pytest.approx(0.126)
    assert APERTURE_AREA_M2 == pytest.approx(0.015876)
    with pytest.raises(AperturePpeCalibrationError, match="channels disagree"):
        parse_equal_rgb("1 1.000000001 1")


def test_approved_geometry_materials_normals_and_sole_aperture() -> None:
    scenes = calibration_scene_texts()
    materials = scenes["materials.rad"]
    system = scenes["system.rad"]
    sender = scenes["aperture_sender.rad"]
    emitter = scenes["internal_emitter_receiver.rad"]
    assert set(scenes) == {
        "materials.rad",
        "system.rad",
        "aperture_sender.rad",
        "internal_emitter_receiver.rad",
        "analytic_uniform_hemisphere_receiver.rad",
    }
    assert system == proposed_fixture_stack_radiance_text(
        0, 0.0, 0.0, 0.0, name_prefix="calibration"
    )
    assert materials == proposed_fixture_materials_radiance_text()
    assert "void dielectric proposed_pmma\n0\n0\n5 1 1 1 1.49 0" in materials
    assert (
        "void plastic proposed_ptfe\n0\n0\n"
        "5 0.90 0.90 0.90 0 0"
    ) in materials
    assert "void plastic proposed_black_bezel\n0\n0\n5 0 0 0 0 0" in materials
    assert "0.92" not in materials

    sender_vertices = _polygon_vertices(sender, "calibration_aperture_sender")
    emitter_vertices = _polygon_vertices(
        emitter,
        "calibration_internal_emitter_receiver",
    )
    assert _normal(sender_vertices) == pytest.approx((0.0, 0.0, -1.0))
    assert _normal(emitter_vertices) == pytest.approx((0.0, 0.0, -1.0))
    assert max(abs(vertex[0]) for vertex in sender_vertices) == pytest.approx(0.063)
    assert max(abs(vertex[1]) for vertex in sender_vertices) == pytest.approx(0.063)
    assert {vertex[2] for vertex in sender_vertices} == {-1.0e-6}
    assert max(abs(vertex[0]) for vertex in emitter_vertices) == pytest.approx(0.060)
    assert max(abs(vertex[1]) for vertex in emitter_vertices) == pytest.approx(0.060)
    assert {vertex[2] for vertex in emitter_vertices} == {0.011}
    assert sender.count("#@rfluxmtx h=u") == 1
    assert emitter.count("#@rfluxmtx h=u") == 1

    pmma_faces = ("top", "bottom", "north", "south", "west", "east")
    pmma_vertices = tuple(
        vertex
        for face in pmma_faces
        for vertex in _polygon_vertices(system, f"calibration_m0000_pmma_{face}")
    )
    assert max(abs(vertex[0]) for vertex in pmma_vertices) == pytest.approx(0.064)
    assert max(abs(vertex[1]) for vertex in pmma_vertices) == pytest.approx(0.064)
    assert {vertex[2] for vertex in pmma_vertices} == {0.0, 0.003}
    assert _normal(
        _polygon_vertices(system, "calibration_m0000_pmma_top")
    ) == pytest.approx((0.0, 0.0, 1.0))
    assert _normal(
        _polygon_vertices(system, "calibration_m0000_pmma_bottom")
    ) == pytest.approx((0.0, 0.0, -1.0))

    ptfe_normals = {
        "north": (0.0, -1.0, 0.0),
        "south": (0.0, 1.0, 0.0),
        "west": (1.0, 0.0, 0.0),
        "east": (-1.0, 0.0, 0.0),
    }
    for side, expected in ptfe_normals.items():
        vertices = _polygon_vertices(system, f"calibration_m0000_ptfe_{side}")
        assert _normal(vertices) == pytest.approx(expected)
        assert {vertex[2] for vertex in vertices} == {0.003, 0.011}
        assert _normal(
            _polygon_vertices(system, f"calibration_m0000_black_bezel_{side}")
        ) == pytest.approx((0.0, 0.0, 1.0))
    assert system.count("proposed_black_bezel polygon") == 4
    assert system.count("proposed_ptfe polygon") == 4


def test_exact_four_actions_options_and_nproc_propagation(tmp_path: Path) -> None:
    config = CalibrationConfig(output_directory=tmp_path, nproc=6)
    actions = build_calibration_actions(config)
    assert [action.key for action in actions] == ["analytic", "A", "B", "C"]
    assert len(actions) == 4
    expected_a = (
        "-V+", "-h", "-u-", "-n", "6", "-c", "5000",
        "-ab", "6", "-ad", "2048", "-ds", "0.05", "-dj", "1",
        "-lr", "-24", "-lw", "1e-06", "-st", "0",
        "-av", "0", "0", "0", "-aw", "0",
    )
    expected_b = (
        "-V+", "-h", "-u-", "-n", "6", "-c", "20000",
        "-ab", "6", "-ad", "2048", "-ds", "0.05", "-dj", "1",
        "-lr", "-24", "-lw", "1e-06", "-st", "0",
        "-av", "0", "0", "0", "-aw", "0",
    )
    expected_c = (
        "-V+", "-h", "-u-", "-n", "6", "-c", "20000",
        "-ab", "8", "-ad", "4096", "-ds", "0.025", "-dj", "1",
        "-lr", "-32", "-lw", "1e-07", "-st", "0",
        "-av", "0", "0", "0", "-aw", "0",
    )
    assert check_options("A", 6) == expected_a
    assert check_options("B", 6) == expected_b
    assert check_options("C", 6) == expected_c
    assert actions[0].options == expected_c
    assert actions[1].options == expected_a
    assert actions[2].options == expected_b
    assert actions[3].options == expected_c
    for action in actions:
        assert action.command.argv[0] == "rfluxmtx"
        assert action.command.argv[1 : 1 + len(action.options)] == action.options
        assert "-u-" in action.command.argv
        assert action.command.argv[action.command.argv.index("-n") + 1] == "6"
        assert all(
            option not in action.command.argv
            for option in ("-aa", "-ar", "-as", "-dr", "-dt", "-dc")
        )


def test_parser_surface_dry_default_and_positive_nproc(tmp_path: Path) -> None:
    parser = build_parser()
    destinations = {action.dest for action in parser._actions}
    assert destinations == {"help", "output_dir", "nproc", "execute"}
    assert parser.parse_args(()).execute is False
    assert parser.parse_args(()).nproc == 1
    assert parser.parse_args(("--execute",)).execute is True
    with pytest.raises(ValueError, match="positive integer"):
        CalibrationConfig(output_directory=tmp_path, nproc=0)


def test_default_cli_path_is_a_dry_run_without_radiance(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "dry"
    assert main(("--nproc", "6", "--output-dir", str(output))) == 0
    printed = capsys.readouterr().out
    result_path = output / "aperture_ppe_calibration.json"
    assert str(result_path) in printed
    payload = json.loads(result_path.read_text())
    assert payload["status"] == "planned"
    assert payload["radiance_version"] is None
    assert payload["nproc"] == 6
    assert len(payload["actions"]) == 4
    assert all(action["status"] == "planned" for action in payload["actions"])
    assert payload["t_fixture_candidate"] is None
    assert payload["required_internal_ppe_umol_per_j"] is None


def test_execute_flag_dispatches_only_when_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    planned = tmp_path / "planned.json"
    executed = tmp_path / "executed.json"

    def fake_plan(config: CalibrationConfig) -> Path:
        calls.append("plan")
        return planned

    def fake_execute(config: CalibrationConfig) -> Path:
        calls.append("execute")
        return executed

    monkeypatch.setattr(calibration, "plan_calibration", fake_plan)
    monkeypatch.setattr(calibration, "execute_calibration", fake_execute)
    assert calibration.main(("--output-dir", str(tmp_path))) == 0
    assert calls == ["plan"]
    assert calibration.main(("--output-dir", str(tmp_path), "--execute")) == 0
    assert calls == ["plan", "execute"]


def test_acceptance_uses_check_c_and_calculates_required_internal_ppe() -> None:
    result = evaluate_observations(
        analytic_flux=1.0000001,
        flux_a=0.800,
        flux_b=0.804,
        flux_c=0.808,
    )
    assert all(result["acceptance_checks"].values())
    assert result["relative_differences"]["A_to_B"] == pytest.approx(0.005)
    assert result["relative_differences"]["B_to_C"] == pytest.approx(
        abs(0.808 - 0.804) / 0.804
    )
    assert result["t_fixture_candidate"] == pytest.approx(0.808)
    assert result["required_internal_ppe_umol_per_j"] == pytest.approx(2.6 / 0.808)


@pytest.mark.parametrize(
    ("analytic", "a", "b", "c", "failed_check"),
    (
        (1.000002, 0.8, 0.8, 0.8, "analytic_unit_flux_error_below_1e-6"),
        (1.0, 0.8, 0.808, 0.808, "A_to_B_relative_difference_below_1_percent"),
        (1.0, 0.8, 0.8, 0.808, "B_to_C_relative_difference_below_1_percent"),
    ),
)
def test_candidate_is_null_when_any_strict_check_fails(
    analytic: float,
    a: float,
    b: float,
    c: float,
    failed_check: str,
) -> None:
    result = evaluate_observations(
        analytic_flux=analytic,
        flux_a=a,
        flux_b=b,
        flux_c=c,
    )
    assert result["acceptance_checks"][failed_check] is False
    assert result["t_fixture_candidate"] is None
    assert result["required_internal_ppe_umol_per_j"] is None


def test_fake_execution_writes_one_result_raw_streams_and_progress(
    tmp_path: Path,
) -> None:
    config = CalibrationConfig(output_directory=tmp_path / "native", nproc=6)
    runner = _FakeRfluxmtxRunner(
        {"analytic": 1.0, "A": 0.800, "B": 0.804, "C": 0.808}
    )
    progress: list[str] = []
    result_path = execute_calibration(
        config,
        runner=runner,
        rfluxmtx_command="synthetic-rfluxmtx",
        radiance_version="synthetic Radiance",
        progress=progress.append,
    )
    payload = json.loads(result_path.read_text())
    assert payload["status"] == "completed"
    assert payload["radiance_version"] == "synthetic Radiance"
    assert payload["nproc"] == 6
    assert payload["t_fixture_candidate"] == pytest.approx(0.808)
    assert payload["required_internal_ppe_umol_per_j"] == pytest.approx(2.6 / 0.808)
    assert payload["schema_id"] == (
        "fspm-optics.backward-rfluxmtx-aperture-diagnostic"
    )
    assert payload["candidate_is_production_authority"] is False
    assert payload["authority_scope"]["production_normalization_authority"] is False
    assert payload["current_forward_flux_production_authority_reference"] == {
        "t_fixture": ACCEPTED_FIXTURE_TRANSMISSION,
        "completed_aperture_ppe_umol_per_j": (
            COMPLETED_APERTURE_PPE_UMOL_PER_J
        ),
        "internal_source_ppe_umol_per_j": INTERNAL_SOURCE_PPE_UMOL_PER_J,
        "optical_stack_id": PROPOSED_FIXTURE_OPTICAL_STACK_ID,
    }
    assert len(runner.commands) == 4
    assert len(progress) == 8
    assert all(action["status"] == "completed" for action in payload["actions"])
    for key in ("analytic", "A", "B", "C"):
        assert (config.output_directory / "raw" / f"{key}.stdout").is_file()
        assert (config.output_directory / "raw" / f"{key}.stderr").is_file()


def test_calibration_uses_shared_authority_without_application_or_absorption_imports() -> None:
    source = Path(calibration.__file__).read_text(encoding="utf-8")
    assert ACCEPTED_FIXTURE_TRANSMISSION == 0.8331432504293806
    assert INTERNAL_SOURCE_PPE_UMOL_PER_J == 3.120711832761085
    assert "from .photons" not in source
    assert "fspm_optics.application" not in source
    assert "pmma_absorption_coefficient_m" not in source
    assert "179" not in source
