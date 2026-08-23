from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest

from fspm_optics.application import surface_flux_calibration as calibration
from fspm_optics.application.surface_flux_calibration import (
    CALIBRATION_SCHEMA_ID,
    CalibrationCriteria,
    HistogramSpecification,
    SurfaceFluxCalibrationConfig,
    SurfaceFluxCalibrationError,
    analyze_calibration_sweep,
    calibration_scene_identity,
    canonical_neutral_source_definition,
    configuration_identity,
    format_calibration_report,
    render_neutral_source,
    run_surface_flux_calibration,
)
from fspm_optics.optics.rex_material_plan import (
    build_rex_radiance_trans_material_plan,
)
from fspm_optics.plants.multi_scene import build_juvenile_natural_fit_scene
from fspm_optics.plants.natural_fit import plan_natural_fit_layout_from_feet
from fspm_optics.radiance.runner import RunnerResult
from fspm_optics.radiance.versioning import (
    RadianceExecutableVersion,
    RadianceInstallation,
)
from fspm_optics.surface_flux_calibration_cli import (
    build_parser,
    config_from_namespace,
)


REPOSITORY = Path(__file__).parents[1]
CREATED_AT = "2026-07-14T12:00:00Z"


def _installation() -> RadianceInstallation:
    return RadianceInstallation(
        oconv=RadianceExecutableVersion(
            name="oconv",
            path=Path("/test-radiance/bin/oconv"),
            version_text="test Radiance oconv",
        ),
        rtrace=RadianceExecutableVersion(
            name="rtrace",
            path=Path("/test-radiance/bin/rtrace"),
            version_text="test Radiance rtrace",
        ),
    )


class _LinearGreyRunner:
    def __init__(self, *, interrupt_label: str | None = None) -> None:
        self.amplitudes: dict[Path, float] = {}
        self.labels: list[str] = []
        self.interrupt_label = interrupt_label

    def run(self, command, *, timeout_s=None, stderr_path=None):
        del timeout_s
        self.labels.append(command.label)
        if self.interrupt_label and self.interrupt_label in command.label:
            raise KeyboardInterrupt
        if stderr_path is not None:
            Path(stderr_path).write_text("", encoding="utf-8")
        if command.label.endswith("_oconv"):
            source_path = next(
                Path(value)
                for value in command.argv
                if str(value).endswith("source.rad")
            )
            amplitude = _source_amplitude(source_path)
            assert command.stdout_path is not None
            command.stdout_path.write_bytes(b"test-octree\n")
            self.amplitudes[command.stdout_path] = amplitude
        elif command.label.endswith("_rtrace"):
            assert command.stdin_path is not None
            assert command.stdout_path is not None
            octree = Path(command.argv[-1])
            amplitude = self.amplitudes[octree]
            count = len(command.stdin_path.read_text(encoding="utf-8").splitlines())
            reference = "_reference_" in command.label
            rows = []
            for index in range(count):
                value = amplitude if reference or index % 2 == 0 else amplitude * 0.5
                rows.append(f"{value:.17g} {value:.17g} {value:.17g}\n")
            command.stdout_path.write_text("".join(rows), encoding="utf-8")
        else:
            raise AssertionError(f"unexpected command label: {command.label}")
        return RunnerResult(
            command_label=command.label,
            argv=command.argv,
            returncode=0,
            stdout_path=command.stdout_path,
            stderr_text="",
            stderr_path=None if stderr_path is None else Path(stderr_path),
            wall_time_s=0.0,
            success=True,
        )


class _NoExecutionRunner:
    def run(self, command, *, timeout_s=None, stderr_path=None):
        del command, timeout_s, stderr_path
        raise AssertionError("a validated resume must not execute Radiance")


def _source_amplitude(path: Path) -> float:
    lines = path.read_text(encoding="utf-8").splitlines()
    glow_index = lines.index(f"void glow {calibration.SOURCE_GLOW_MODIFIER}")
    return float(lines[glow_index + 3].split()[1])


def _config(output: Path, *, resume: bool = False) -> SurfaceFluxCalibrationConfig:
    return SurfaceFluxCalibrationConfig(
        output_directory=output,
        room_length_ft=2.0,
        room_width_ft=2.0,
        reference_grid_x=2,
        reference_grid_y=2,
        quality="direct",
        reference_levels_umol_m2_s=(250.0, 500.0),
        threads=1,
        resume=resume,
    )


def _synthetic_level(level: float, beta: float = 0.4) -> dict[str, object]:
    statistics = {
        metric: {
            "area_weighted_mean_density_umol_m2_s": beta * level,
            "beta_level": beta,
            "distribution": {
                "bin_area_fractions": [0.25, 0.75],
                "underflow": {"area_fraction": 0.0},
                "overflow": {"area_fraction": 0.0},
            },
        }
        for metric in calibration.SIDE_METRICS
    }
    return {
        "kind": "primary",
        "quality": "direct",
        "requested_reference_level_umol_m2_s": level,
        "reference": {
            "amplitude_resolution": {
                "final": {"metrics": {"mean_ppfd_umol_m2_s": level}}
            },
            "acceptance": {"pass": True},
        },
        "statistics": statistics,
        "validation": {
            "all_numerical_and_conservation_validations_pass": True
        },
    }


def test_calibration_api_has_no_lighting_system_discriminator_or_imports() -> None:
    assert "system_id" not in {item.name for item in fields(SurfaceFluxCalibrationConfig)}
    source_path = (
        REPOSITORY
        / "src"
        / "fspm_optics"
        / "application"
        / "surface_flux_calibration.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = (
        "fixtures",
        "sources",
        "application.domain",
        "application.systems",
        "application.target_control",
        "application.proposed",
        "transport.conventional",
        "transport.hps",
    )
    assert not any(
        value in module for module in imported for value in forbidden
    )
    text = source_path.read_text(encoding="utf-8").lower()
    for token in (
        "system_id",
        "target_control",
        "mount_height",
        ".ies",
        ".dat",
    ):
        assert token not in text


def test_neutral_source_is_canonical_equal_four_band_and_excludes_far_red() -> None:
    definition = canonical_neutral_source_definition()
    spectrum = definition["spectrum"]

    assert spectrum["included_band_order"] == ["blue", "green", "orange", "red"]
    assert spectrum["photon_fractions"] == {
        "blue": 0.25,
        "green": 0.25,
        "orange": 0.25,
        "red": 0.25,
    }
    assert spectrum["sum"] == 1.0
    assert spectrum["excluded_intervals"] == ["far_red"]
    assert "source neutral_reference_upper_hemisphere" in render_neutral_source(1.0)
    assert render_neutral_source(1.0).endswith("4 0 0 1 180\n")
    assert definition == canonical_neutral_source_definition()


def test_requested_intensity_does_not_change_source_scene_or_material_identity(
    tmp_path: Path,
) -> None:
    first = _config(tmp_path / "first")
    second = SurfaceFluxCalibrationConfig(
        **{
            **{
                name: getattr(first, name)
                for name in (
                    "room_length_ft",
                    "room_width_ft",
                    "reference_plane_z_m",
                    "reference_grid_x",
                    "reference_grid_y",
                    "reference_inset_m",
                    "quality",
                    "threads",
                    "oconv_command",
                    "rtrace_command",
                    "histogram",
                    "criteria",
                )
            },
            "output_directory": tmp_path / "second",
            "reference_levels_umol_m2_s": (300.0, 600.0),
        }
    )
    scene = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout_from_feet(2.0, 2.0)
    )
    materials = build_rex_radiance_trans_material_plan()

    assert calibration_scene_identity(first, scene, materials) == (
        calibration_scene_identity(second, scene, materials)
    )
    first_identity = configuration_identity(
        first,
        scene=scene,
        material_plan=materials,
        radiance_installation=_installation(),
        repository_revision="a" * 40,
    )
    repeated_identity = configuration_identity(
        first,
        scene=scene,
        material_plan=materials,
        radiance_installation=_installation(),
        repository_revision="a" * 40,
    )
    assert first_identity == repeated_identity
    assert first_identity["configuration_sha256"] != configuration_identity(
        second,
        scene=scene,
        material_plan=materials,
        radiance_installation=_installation(),
        repository_revision="a" * 40,
    )["configuration_sha256"]


def test_cli_defaults_and_validation() -> None:
    parser = build_parser()
    arguments = parser.parse_args([])
    config = config_from_namespace(arguments)

    assert config.output_directory.name == ".surface-flux-calibration"
    assert config.reference_levels_umol_m2_s == (250.0, 375.0, 500.0, 625.0, 750.0)
    assert config.quality == "standard"
    with pytest.raises(ValueError, match="strictly increasing"):
        SurfaceFluxCalibrationConfig(
            output_directory=Path("calibration"),
            reference_levels_umol_m2_s=(500.0, 250.0),
        )
    with pytest.raises(ValueError, match="higher"):
        SurfaceFluxCalibrationConfig(
            output_directory=Path("calibration"),
            quality="rigorous",
            verification_level_umol_m2_s=500.0,
            verification_quality="rigorous",
        )


def test_exact_area_weighted_beta_and_fixed_overflow_accounting() -> None:
    specification = HistogramSpecification(minimum=0.0, maximum=1.0, bin_count=2)
    accumulator = calibration._MetricAccumulator(5.0, specification)
    accumulator.add(1.0, 1.0)
    accumulator.add(3.0, 3.0)
    accumulator.add(10.0, 2.0)
    payload = accumulator.payload()

    assert payload["area_weighted_mean_density_umol_m2_s"] == pytest.approx(5.0)
    assert payload["beta_level"] == pytest.approx(1.0)
    assert payload["distribution"]["overflow"] == {
        "count": 1,
        "area_m2": 2.0,
        "area_fraction": pytest.approx(1.0 / 3.0),
    }
    assert specification.boundaries == (0.0, 0.5, 1.0)


def test_through_origin_regression_drift_and_acceptance() -> None:
    analysis = analyze_calibration_sweep(
        [_synthetic_level(250.0), _synthetic_level(500.0), _synthetic_level(750.0)]
    )

    for metric in calibration.SIDE_METRICS:
        result = analysis["metrics"][metric]
        assert result["through_origin"]["slope_beta"] == pytest.approx(0.4)
        assert result["through_origin"]["r_squared"] == pytest.approx(1.0)
        assert result["unconstrained_diagnostic"]["intercept_umol_m2_s"] == pytest.approx(0.0)
        assert result["maximum_relative_beta_drift"] == 0.0
        assert result["normalized_distribution_drift"]["maximum"] == 0.0
        assert result["pass"] is True
    assert analysis["pass"] is True


def test_optional_higher_quality_convergence_is_reported_separately() -> None:
    primary = [_synthetic_level(250.0), _synthetic_level(500.0)]
    verification = _synthetic_level(500.0, beta=0.402)
    verification["kind"] = "verification"
    verification["quality"] = "rigorous"

    result = calibration._verification_analysis(
        primary,
        verification,
        criteria=CalibrationCriteria(),
    )

    assert result["executed"] is True
    assert result["level_umol_m2_s"] == 500.0
    assert result["metrics"]["front_incident"]["relative_difference"] == pytest.approx(0.005)
    assert result["pass"] is True


def test_beta_and_distribution_drift_fail_without_replacement_defaults() -> None:
    first = _synthetic_level(250.0, beta=0.4)
    second = _synthetic_level(500.0, beta=0.42)
    second["statistics"]["front_incident"]["distribution"][
        "bin_area_fractions"
    ] = [0.35, 0.65]

    analysis = analyze_calibration_sweep([first, second])
    result = analysis["metrics"]["front_incident"]

    assert result["maximum_relative_beta_drift"] > 0.01
    assert result["normalized_distribution_drift"]["maximum"] == pytest.approx(0.1)
    assert result["pass"] is False
    assert analysis["pass"] is False


def test_full_fake_sweep_is_independent_compact_and_resumable(tmp_path: Path) -> None:
    output = tmp_path / "calibration"
    runner = _LinearGreyRunner()
    material_plan = build_rex_radiance_trans_material_plan()
    expected_front_absorbed_beta = sum(
        material_plan.material(band_id).source_interval.coefficients.absorptance
        / 4.0
        for band_id in ("blue", "green", "orange", "red")
    )
    first = run_surface_flux_calibration(
        _config(output),
        runner=runner,
        radiance_installation=_installation(),
        created_at_utc=CREATED_AT,
        repository_root=REPOSITORY,
    )

    assert first.report_path.name == "surface-flux-calibration-report.v1.json"
    assert first.report["schema_id"] == CALIBRATION_SCHEMA_ID
    assert first.report["acceptance"]["pass"] is True
    assert len([label for label in runner.labels if label.endswith("_rtrace")]) == 12
    assert len(set(runner.labels)) == len(runner.labels)
    levels = first.report["ordered_level_results"]
    assert [value["requested_reference_level_umol_m2_s"] for value in levels] == [250.0, 500.0]
    for level in levels:
        assert level["reference"]["plant_free"] is True
        assert level["reference"]["amplitude_resolution"]["final"]["metrics"]["coefficient_of_variation"] == 0.0
        assert level["plant_transport"]["band_order"] == ["blue", "green", "orange", "red"]
        assert level["plant_transport"]["far_red_executed"] is False
        assert level["statistics"]["front_incident"]["beta_level"] == pytest.approx(1.0)
        assert level["statistics"]["back_incident"]["beta_level"] == pytest.approx(0.5)
        assert level["statistics"]["front_absorbed"]["beta_level"] == pytest.approx(
            expected_front_absorbed_beta
        )
        assert level["statistics"]["back_absorbed"]["beta_level"] == pytest.approx(
            expected_front_absorbed_beta * 0.5
        )
        assert level["validation"]["authoritative_phase27g_c_equations_reused"] is True
        reference_commands = [
            command
            for command in level["commands"]
            if "_reference_" in command["label"]
        ]
        assert reference_commands
        assert all(
            "plant-geometry.rad" not in " ".join(command["argv"])
            for command in reference_commands
        )
        source_hashes = {
            band["source_sha256"] for band in level["plant_transport"]["bands"]
        }
        assert len(source_hashes) == 1
    serialized = first.report_path.read_text(encoding="utf-8")
    assert '"receivers": [' not in serialized
    assert '"patches": [' not in serialized
    assert first.report["compactness"]["full_receiver_arrays_present"] is False
    assert format_calibration_report(first.report) == serialized

    resumed = run_surface_flux_calibration(
        _config(output, resume=True),
        runner=_NoExecutionRunner(),
        radiance_installation=_installation(),
        created_at_utc="2099-01-01T00:00:00Z",
        repository_root=REPOSITORY,
    )
    assert resumed.report["created_at_utc"] == CREATED_AT
    assert resumed.report["configuration_sha256"] == first.report["configuration_sha256"]


def test_resume_rejects_corruption_before_any_execution(tmp_path: Path) -> None:
    output = tmp_path / "calibration"
    run_surface_flux_calibration(
        _config(output),
        runner=_LinearGreyRunner(),
        radiance_installation=_installation(),
        created_at_utc=CREATED_AT,
        repository_root=REPOSITORY,
    )
    raw = next(output.glob("levels/primary-250-direct/bands/*/receiver-values.v1.f64le.bin"))
    raw.write_bytes(raw.read_bytes() + b"corruption")

    with pytest.raises(SurfaceFluxCalibrationError, match="hash validation"):
        run_surface_flux_calibration(
            _config(output, resume=True),
            runner=_NoExecutionRunner(),
            radiance_installation=_installation(),
            repository_root=REPOSITORY,
        )


def test_resume_refuses_a_different_configuration(tmp_path: Path) -> None:
    output = tmp_path / "calibration"
    run_surface_flux_calibration(
        _config(output),
        runner=_LinearGreyRunner(),
        radiance_installation=_installation(),
        created_at_utc=CREATED_AT,
        repository_root=REPOSITORY,
    )
    changed = SurfaceFluxCalibrationConfig(
        output_directory=output,
        room_length_ft=2.0,
        room_width_ft=2.0,
        reference_grid_x=2,
        reference_grid_y=2,
        quality="direct",
        reference_levels_umol_m2_s=(250.0, 600.0),
        threads=1,
        resume=True,
    )

    with pytest.raises(SurfaceFluxCalibrationError, match="refusing to mix"):
        run_surface_flux_calibration(
            changed,
            runner=_NoExecutionRunner(),
            radiance_installation=_installation(),
            repository_root=REPOSITORY,
        )


def test_interrupt_removes_active_stage_and_preserves_resumable_state(
    tmp_path: Path,
) -> None:
    output = tmp_path / "calibration"
    runner = _LinearGreyRunner(interrupt_label="reference_pilot_rtrace")

    with pytest.raises(KeyboardInterrupt):
        run_surface_flux_calibration(
            _config(output),
            runner=runner,
            radiance_installation=_installation(),
            created_at_utc=CREATED_AT,
            repository_root=REPOSITORY,
        )

    assert (output / "calibration-configuration.v1.json").is_file()
    assert not tuple((output / "levels").glob(".*.tmp"))
    assert not tuple((output / "levels").glob("primary-*"))


def test_report_serialization_rejects_nonfinite_values() -> None:
    with pytest.raises(ValueError):
        format_calibration_report({"invalid": float("nan")})


def test_runtime_output_is_not_packaged_or_published_to_the_ui() -> None:
    pyproject = (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
    gitignore = (REPOSITORY / ".gitignore").read_text(encoding="utf-8")
    web = (REPOSITORY / "src/fspm_optics/resources/web/app.js").read_text(
        encoding="utf-8"
    )

    assert "fspm-optics-calibrate-surface-flux" in pyproject
    assert ".surface-flux-calibration/" in gitignore
    assert "surface-flux-calibration-report" not in web
    assert ".surface-flux-calibration" not in pyproject.split(
        "[tool.setuptools.package-data]", 1
    )[1]
