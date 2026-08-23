from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from fspm_optics.angular_experiment_cli import build_parser
from fspm_optics.application.angular_experiment import (
    CASE_CONTROL_FILENAME,
    CASE_RESULT_FILENAME,
    DISPLAY_TITLES,
    EXPERIMENT_PROPOSED_CONTROL_MODE,
    EXPERIMENT_PROPOSED_LAYOUT_MODE,
    EXPERIMENT_PROPOSED_RING_MODE,
    EXPERIMENT_SCHEMA_VERSION,
    AngularExperimentError,
    AngularExperimentConfig,
    _execute_case,
    _finalization_provenance,
    _safe_case_artifact,
    build_experiment_plan,
    build_experiment_summary,
    conventional_polar_profile,
    execute_angular_experiment,
    experiment_radiance_options,
    planned_cases,
    resolve_experiment_grid,
    validate_completed_case,
)
from fspm_optics.application.angular_polar_diagram import (
    INKSCAPE_INCOMPATIBLE_SNAP_ENVIRONMENT_VARIABLES,
    POLAR_PNG_FILENAME,
    POLAR_PNG_HEIGHT,
    POLAR_PNG_WIDTH,
    POLAR_RAW_PNG_FILENAME,
    POLAR_RAW_SVG_FILENAME,
    POLAR_SVG_FILENAME,
    AngularPolarDiagramError,
    authenticate_angular_polar_comparison,
    build_angular_polar_comparison_svg,
    publish_angular_polar_comparison,
    publish_revalidated_angular_polar_comparison,
    render_angular_polar_comparison_png,
)
from fspm_optics.application.angular_republish import (
    AXIS_PLATEAU_RELATIVE_TOLERANCE,
    PRESENTATION_BIN_CENTERS_DEG,
    VALIDATION_DIRECTORY_NAME,
    VALIDATION_FILENAME,
    VALIDATION_LEVELS,
    build_presentation_profiles,
    evaluate_axis_plateau_contract,
    load_completed_experiment_config,
    republish_characterization_only,
)
from fspm_optics.fixtures.smd.angular_characterization import (
    CHARACTERIZATION_DIRECTION_REPLICATE_COUNT,
    CHARACTERIZATION_SCHEMA_ID,
    CHARACTERIZATION_SCHEMA_VERSION,
    AngularCharacterizationConfig,
    AngularCharacterizationError,
    MeasuredAngularProfile,
    execute_fixed_completed_aperture_profile,
    far_field_azimuth_quadrature,
    format_far_field_receivers,
    load_completed_aperture_characterization,
    profile_from_azimuth_samples,
)
from fspm_optics.radiance.runner import RunnerResult
from fspm_optics.radiance.versioning import (
    RadianceExecutableVersion,
    RadianceInstallation,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.power_schedule import build_optimized_module_schedule
from fspm_optics.fixtures.smd.radiance_writer import (
    SmdEmitterAssumptions,
    build_smd_radiance_document,
)
from fspm_optics.fixtures.smd.source_variants import (
    ANGULAR_MODIFIER_NAME,
    NARROW_SMD_SOURCE_VARIANT,
    NATIVE_SMD_SOURCE_VARIANT,
    WIDE_SMD_SOURCE_VARIANT,
    CompletedApertureAngularCalibration,
    ideal_aperture_radiance_exponent,
    ideal_intensity_exponent,
    axisymmetric_integrated_flux,
    get_smd_source_variant,
    measured_fwhm_deg,
    normalized_planar_radiance_gain,
    normalized_profile,
    planar_radiant_intensity,
    profile_sha256,
    source_variant_cal_text,
)
from fspm_optics.transport.proposed_uniform import (
    materialize_uniform_proposed_stage_a,
    plan_uniform_proposed_stage_a,
)


def _calibration(
    variant: str,
    *,
    fwhm: float,
    transmission: float,
) -> CompletedApertureAngularCalibration:
    exponent = 0.0 if variant == NATIVE_SMD_SOURCE_VARIANT else (
        ideal_aperture_radiance_exponent(fwhm)
    )
    return CompletedApertureAngularCalibration(
        variant_key=variant,
        internal_radiance_exponent=exponent,
        internal_modifier_gain=normalized_planar_radiance_gain(exponent),
        measured_completed_aperture_fwhm_deg=fwhm,
        completed_aperture_transmission=transmission,
        reference_input_w=1.0,
        measured_reference_completed_aperture_ppf_umol_s=2.6,
        target_completed_aperture_fwhm_deg=(
            None if variant == NATIVE_SMD_SOURCE_VARIANT else fwhm
        ),
        fwhm_tolerance_deg=0.5,
        reference_flux_relative_tolerance=0.005,
        profile_sha256="a" * 64,
        characterization_identity_sha256="b" * 64,
    )


def _document(variant: str, fwhm: float, transmission: float):
    layout = generate_proposed_led_layout(
        10,
        10,
        proposed_layout_mode=EXPERIMENT_PROPOSED_LAYOUT_MODE,
        proposed_ring_mode=EXPERIMENT_PROPOSED_RING_MODE,
    )
    schedule = build_optimized_module_schedule(layout, (1, 0, 0, 0))
    calibration = _calibration(
        variant,
        fwhm=fwhm,
        transmission=transmission,
    )
    return build_smd_radiance_document(
        layout,
        schedule,
        assumptions=SmdEmitterAssumptions(
            source_variant=variant,
            completed_aperture_angular_calibration=calibration,
            allow_non_authoritative_backward_calibration=True,
        ),
    )


def _json_identity(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _profile(
    key: str,
    *,
    fwhm: float,
) -> dict[str, object]:
    angles = [float(value) / 2.0 for value in range(181)]
    exponent = ideal_intensity_exponent(fwhm)
    intensity = [
        max(0.0, math.cos(math.radians(angle))) ** exponent
        for angle in angles
    ]
    normalized = [value / max(intensity) for value in intensity]
    measured = measured_fwhm_deg(angles, normalized)
    return {
        "profile_key": key,
        "display_title": DISPLAY_TITLES[key],
        "angle_deg": angles,
        "normalized_radiant_intensity": normalized,
        "measured_fwhm_deg": measured,
        "profile_sha256": profile_sha256(angles, normalized),
        "characterization_identity_sha256": hashlib.sha256(
            f"characterization:{key}".encode()
        ).hexdigest(),
        "source_authority": {
            "type": "test_authoritative_completed_aperture_characterization",
            "measurement_boundary": "completed_aperture_radiant_intensity",
        },
    }


def _diagram_profiles() -> list[dict[str, object]]:
    return [
        conventional_polar_profile(),
        _profile(NATIVE_SMD_SOURCE_VARIANT, fwhm=118.0),
        _profile(WIDE_SMD_SOURCE_VARIANT, fwhm=140.0),
        _profile(NARROW_SMD_SOURCE_VARIANT, fwhm=100.0),
    ]


def _radiance_installation() -> RadianceInstallation:
    return RadianceInstallation(
        oconv=RadianceExecutableVersion(
            "oconv", Path("/fake/oconv"), "fake oconv"
        ),
        rtrace=RadianceExecutableVersion(
            "rtrace", Path("/fake/rtrace"), "fake rtrace"
        ),
    )


class _CompleteSceneRecordingRunner:
    def __init__(self) -> None:
        self.commands: list[object] = []

    def run(
        self,
        command: object,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult:
        del timeout_s, stderr_path
        self.commands.append(command)
        output = command.stdout_path
        assert output is not None
        if output.suffix == ".oct":
            output.write_bytes(b"recorded complete scene octree")
        else:
            assert command.stdin_path is not None
            sensor_count = len(
                command.stdin_path.read_text(encoding="utf-8").splitlines()
            )
            output.write_text(
                "1000 1000 1000\n" * sensor_count,
                encoding="utf-8",
            )
        return RunnerResult(
            command_label=command.label,
            argv=command.argv,
            returncode=0,
            stdout_path=output,
            stderr_text="",
            stderr_path=None,
            wall_time_s=0.01,
            success=True,
        )


@pytest.mark.parametrize("target_fwhm", (100.0, 140.0))
def test_projected_area_cosine_is_included_in_completed_aperture_fwhm(
    target_fwhm: float,
) -> None:
    intensity_exponent = ideal_intensity_exponent(target_fwhm)
    radiance_exponent = ideal_aperture_radiance_exponent(target_fwhm)
    assert radiance_exponent == pytest.approx(intensity_exponent - 1.0)

    angles = tuple(float(value) / 10.0 for value in range(901))
    profile = tuple(
        planar_radiant_intensity(angle, radiance_exponent)
        for angle in angles
    )
    assert measured_fwhm_deg(angles, profile) == pytest.approx(
        target_fwhm,
        abs=1e-9,
    )
    # Omitting projected area would measure the radiance exponent itself and
    # fail the preregistered completed-aperture target.
    half_angle = math.degrees(
        math.acos(0.5 ** (1.0 / radiance_exponent))
    ) if radiance_exponent > 0.0 else None
    if half_angle is not None:
        assert 2.0 * half_angle != pytest.approx(target_fwhm)


@pytest.mark.parametrize(
    ("variant", "fwhm", "transmission"),
    (
        (NATIVE_SMD_SOURCE_VARIANT, 114.5, 0.81),
        (WIDE_SMD_SOURCE_VARIANT, 140.0, 0.77),
        (NARROW_SMD_SOURCE_VARIANT, 100.0, 0.84),
    ),
)
def test_reference_watt_ppf_and_completed_aperture_ppe_close_across_variants(
    variant: str,
    fwhm: float,
    transmission: float,
) -> None:
    document = _document(variant, fwhm, transmission)
    metadata = document.metadata
    assert (
        metadata.modeled_completed_aperture_par_ppf_umol_s
        / metadata.total_watts
    ) == pytest.approx(2.6)
    assert metadata.completed_aperture_fixture_ppe_umol_per_j == 2.6
    assert metadata.internal_source_ppe_umol_per_j == pytest.approx(
        2.6 / transmission
    )
    assert metadata.accepted_fixture_transmission == transmission


@pytest.mark.parametrize(
    ("variant", "fwhm", "transmission"),
    (
        (WIDE_SMD_SOURCE_VARIANT, 140.0, 0.77),
        (NARROW_SMD_SOURCE_VARIANT, 100.0, 0.84),
    ),
)
def test_angular_cal_is_materialized_and_active_in_emitting_modifier_chain(
    tmp_path: Path,
    variant: str,
    fwhm: float,
    transmission: float,
) -> None:
    layout = generate_proposed_led_layout(10, 10)
    calibration = _calibration(
        variant,
        fwhm=fwhm,
        transmission=transmission,
    )
    workspace = plan_uniform_proposed_stage_a(
        layout=layout,
        output_directory=tmp_path / variant,
        radiance_options=experiment_radiance_options("standard"),
        emitter_assumptions=SmdEmitterAssumptions(
            source_variant=variant,
            completed_aperture_angular_calibration=calibration,
            allow_non_authoritative_backward_calibration=True,
        ),
    )
    materialize_uniform_proposed_stage_a(workspace)
    assert workspace.paths.source_variant_cal is not None
    assert workspace.paths.source_variant_cal.is_file()
    source = workspace.paths.source.read_text()
    assert f"void brightfunc {ANGULAR_MODIFIER_NAME}" in source
    assert f"{ANGULAR_MODIFIER_NAME} light smd_control_zone_" in source
    assert (
        workspace.emitter_document.metadata.angular_cal_sha256 is not None
    )


def test_experiment_grids_resolve_without_changing_production_policy() -> None:
    square = resolve_experiment_grid(10, 10)
    rectangle = resolve_experiment_grid(30, 50)
    assert (square.spec.resolution_x, square.spec.resolution_y) == (21, 21)
    assert (rectangle.spec.resolution_x, rectangle.spec.resolution_y) == (105, 63)
    assert square.policy.target_spacing_m == 0.145
    assert rectangle.policy.target_spacing_m == 0.145


def test_plan_has_six_stage_a_cases_without_hps_or_stage_b(
    tmp_path: Path,
) -> None:
    config = AngularExperimentConfig(tmp_path / "experiment", 250.0)
    plan = build_experiment_plan(config)
    cases = planned_cases(config)
    assert len(cases) == 6
    assert [case.variant_key for case in cases] == [
        NATIVE_SMD_SOURCE_VARIANT,
        WIDE_SMD_SOURCE_VARIANT,
        NARROW_SMD_SOURCE_VARIANT,
    ] * 2
    assert config.quality == "standard"
    assert [
        (case.room_length_ft, case.room_width_ft) for case in cases
    ] == [(10.0, 10.0)] * 3 + [(30.0, 50.0)] * 3
    assert [
        (case.expected_resolution_x, case.expected_resolution_y)
        for case in cases
    ] == [(21, 21)] * 3 + [(105, 63)] * 3
    assert [case.module_count for case in cases] == [41] * 3 + [711] * 3
    assert len({case.planned_complete_scene_identity for case in cases}) == 6
    assert len({case.planned_uniform_control_identity for case in cases}) == 6
    assert plan["scope"]["stage_a_only"] is True
    assert plan["scope"]["stage_b_planned"] is False
    assert plan["scope"]["hps_cases"] is False
    assert all(case["stage"] == "A" for case in plan["cases"])
    assert all(case["system_id"] == "proposed" for case in plan["cases"])
    assert all("hps" not in case["variant"].lower() for case in plan["cases"])
    assert plan["schema_version"] == 2
    assert plan["execution_contract"] == {
        "backend": "production_uniform_complete_scene_stage_a",
        "proposed_layout_mode": "standalone_modules",
        "proposed_ring_mode": "reduced_one_ring",
        "proposed_control_mode": "uniform_module_dimming",
        "basis_matrix_solver_enabled": False,
        "complete_scene_trace_count_per_case": 1,
        "basis_column_count_per_case": 0,
        "global_dimming_factor_count_per_case": 1,
        "all_module_reference_watts_equal": True,
        "all_module_effective_watts_equal": True,
    }
    serialized = json.dumps(plan, sort_keys=True).lower()
    assert "planned_basis" not in serialized
    assert "solver_schedule" not in serialized
    assert "optimized" not in serialized


def test_case_executes_one_uniform_complete_scene_and_authenticates_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "fspm_optics.transport.proposed_uniform."
        "discover_radiance_installation",
        _radiance_installation,
    )
    config = AngularExperimentConfig(tmp_path / "experiment", 250.0)
    case = planned_cases(config)[0]
    runner = _CompleteSceneRecordingRunner()
    result = _execute_case(
        config,
        case=case,
        case_root=config.output_directory / "cases" / case.case_id,
        calibration=_calibration(
            NATIVE_SMD_SOURCE_VARIANT,
            fwhm=118.0,
            transmission=0.8,
        ),
        runner=runner,
    )

    labels = [command.label for command in runner.commands]
    assert labels.count("trace_proposed_uniform_complete_scene") == 1
    assert all("basis" not in label and "rfluxmtx" not in label for label in labels)
    assert result["proposed_layout_mode"] == "standalone_modules"
    assert result["proposed_ring_mode"] == "reduced_one_ring"
    assert result["proposed_control_mode"] == "uniform_module_dimming"
    assert result["basis_matrix_solver_enabled"] is False
    assert result["complete_scene_trace_count"] == 1
    assert result["basis_column_count"] == 0
    assert result["radiance_quality"] == "standard"
    assert result["module_count"] == 41
    assert result["achieved_mean_ppfd_umol_m2_s"] == pytest.approx(250.0)
    assert result["all_module_reference_watts_equal"] is True
    assert result["all_module_effective_watts_equal"] is True
    assert result["electrical_power_w"] == pytest.approx(
        result["solved_electrical_power_w"]
    )
    case_root = config.output_directory / "cases" / case.case_id
    assert not any("basis" in path.name for path in case_root.rglob("*"))
    control = json.loads(
        (case_root / CASE_CONTROL_FILENAME).read_text(encoding="utf-8")
    )
    assert len(set(control["reference_watts_by_module"])) == 1
    assert len(set(control["effective_watts_by_module"])) == 1
    assert control["global_dimming_factor"] == pytest.approx(
        result["global_dimming_factor"]
    )
    assert validate_completed_case(
        case_root / CASE_RESULT_FILENAME,
        experiment_id=config.experiment_id,
        expected_case=case,
    ) == result

    (case_root / "complete-scene" / "uniform_complete_reference.rgb").write_text(
        "stale\n", encoding="utf-8"
    )
    with pytest.raises(AngularExperimentError, match="artifact"):
        validate_completed_case(
            case_root / CASE_RESULT_FILENAME,
            experiment_id=config.experiment_id,
            expected_case=case,
        )


def test_production_remains_native_and_altered_requires_calibration() -> None:
    layout = generate_proposed_led_layout(10, 10)
    schedule = build_optimized_module_schedule(layout, (1, 2, 3, 4, 5))
    native = build_smd_radiance_document(layout, schedule)
    assert native.metadata.source_variant == NATIVE_SMD_SOURCE_VARIANT
    assert native.source_variant_cal_text is None
    with pytest.raises(ValueError, match="no completed-aperture calibration"):
        build_smd_radiance_document(
            layout,
            schedule,
            assumptions=SmdEmitterAssumptions(
                source_variant=WIDE_SMD_SOURCE_VARIANT
            ),
        )
    backward = _calibration(
        NATIVE_SMD_SOURCE_VARIANT,
        fwhm=120.0,
        transmission=0.8,
    )
    with pytest.raises(ValueError, match="requires explicit diagnostic opt-in"):
        SmdEmitterAssumptions(
            completed_aperture_angular_calibration=backward
        )


def test_cli_requires_output_target_and_exposes_quality_threads_resume() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["plan"])
    parsed = parser.parse_args(
        [
            "execute",
            "--output-dir",
            "results",
            "--target-ppfd",
            "900",
            "--quality",
            "rigorous",
            "--threads",
            "8",
            "--resume",
        ]
    )
    assert parsed.target_ppfd == 900.0
    assert parsed.quality == "rigorous"
    assert parsed.threads == 8
    assert parsed.resume is True
    default_quality = parser.parse_args(
        ["plan", "--output-dir", "results", "--target-ppfd", "250"]
    )
    assert default_quality.quality == "standard"
    republish = parser.parse_args(
        [
            "republish-characterization",
            "--output-dir",
            "completed-results",
        ]
    )
    assert republish.action == "republish-characterization"
    assert republish.output_dir == Path("completed-results")
    assert not hasattr(republish, "target_ppfd")
    assert experiment_radiance_options("direct")[-1] == "-u-"


def test_v1_resume_and_republish_inputs_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "v1-results"
    root.mkdir()
    (root / "angular-experiment-plan.json").write_text(
        json.dumps(
            {
                "schema_id": (
                    "fspm-optics.completed-aperture-angular-sensitivity-"
                    "experiment"
                ),
                "schema_version": 1,
                "artifact_type": "experiment_plan",
                "status": "planned",
                "experiment_id": "completed-aperture-angular-v1-stale",
                "target_ppfd_umol_m2_s": 250.0,
                "controlled_variables": {
                    "radiance_quality": "quality",
                    "threads": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    config = AngularExperimentConfig(root, 250.0)
    with pytest.raises(AngularExperimentError, match="conflicts"):
        execute_angular_experiment(config, resume=True)
    with pytest.raises(AngularExperimentError, match="incompatible"):
        load_completed_experiment_config(root)
    assert config.experiment_id.startswith("completed-aperture-angular-v2-")
    assert EXPERIMENT_SCHEMA_VERSION == 2


def test_finalization_provenance_distinguishes_authenticated_reuse() -> None:
    origins = [
        {
            "case_id": f"case-{index}",
            "origin": "reused_authenticated_case_result",
            "case_artifacts_modified_during_finalization": False,
        }
        for index in range(6)
    ]
    provenance = _finalization_provenance(
        resume=True,
        characterization_reused=True,
        case_result_origins=origins,
    )
    assert provenance["mode"] == "finalization_only_authenticated_resume"
    assert provenance["reused_authenticated_case_result_count"] == 6
    assert provenance["case_result_executed_during_invocation_count"] == 0
    assert provenance["radiance_executed_during_this_invocation"] is False
    assert (
        provenance[
            "existing_case_artifacts_recomputed_during_finalization"
        ]
        is False
    )
    assert (
        provenance[
            "existing_case_artifacts_rewritten_during_finalization"
        ]
        is False
    )


def test_inkscape_child_removes_only_incompatible_snap_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in INKSCAPE_INCOMPATIBLE_SNAP_ENVIRONMENT_VARIABLES:
        monkeypatch.setenv(variable, f"bad:{variable}")
    monkeypatch.setenv("FSPM_UNRELATED_ENVIRONMENT", "preserved")
    monkeypatch.setenv("RAYPATH", "/opt/radiance/lib")
    original_path = "/usr/local/bin:/usr/bin:/bin"
    monkeypatch.setenv("PATH", original_path)
    captured: dict[str, object] = {}

    def fake_run(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        **_kwargs: object,
    ) -> SimpleNamespace:
        captured["env"] = env
        output = next(
            value.split("=", 1)[1]
            for value in argv
            if value.startswith("--export-filename=")
        )
        png = (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00\x00\x00\rIHDR"
            + struct.pack(">II", POLAR_PNG_WIDTH, POLAR_PNG_HEIGHT)
        )
        Path(output).write_bytes(png)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(
        "fspm_optics.application.angular_polar_diagram.shutil.which",
        lambda _name: "/usr/bin/inkscape",
    )
    monkeypatch.setattr(
        "fspm_optics.application.angular_polar_diagram.subprocess.run",
        fake_run,
    )
    render_angular_polar_comparison_png(
        b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>'
    )
    child_environment = captured["env"]
    assert isinstance(child_environment, dict)
    assert all(
        variable not in child_environment
        for variable in INKSCAPE_INCOMPATIBLE_SNAP_ENVIRONMENT_VARIABLES
    )
    assert child_environment["FSPM_UNRELATED_ENVIRONMENT"] == "preserved"
    assert child_environment["RAYPATH"] == "/opt/radiance/lib"
    assert child_environment["PATH"] == original_path


def test_polar_svg_contains_exact_required_panels_and_authoritative_arrays() -> None:
    profiles = _diagram_profiles()
    first = build_angular_polar_comparison_svg(profiles)
    assert first == build_angular_polar_comparison_svg(profiles)
    text = first.decode("utf-8")
    assert "HPS" not in text
    root = ElementTree.fromstring(first)
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    rendered_text = {
        "".join(element.itertext())
        for element in root.findall(".//svg:text", namespace)
    }
    assert {
        "(a) Conventional LED",
        "(b) Modularized System",
        "(c) Modularized-Altered (140° FWHM)",
        "(d) Modularized-Altered (100° FWHM)",
    }.issubset(rendered_text)
    panels = root.findall(".//svg:g[@class='polar-panel']", namespace)
    curves = root.findall(
        ".//svg:polyline[@class='distribution-curve']", namespace
    )
    assert len(panels) == len(curves) == 4
    assert {
        (
            panel.attrib["data-radial-min"],
            panel.attrib["data-radial-max"],
            panel.attrib["data-radial-ticks"],
        )
        for panel in panels
    } == {("0", "1", "0.25,0.5,0.75,1")}
    profiles_by_key = {
        str(profile["profile_key"]): profile for profile in profiles
    }
    for curve in curves:
        authoritative = profiles_by_key[curve.attrib["data-profile-key"]]
        assert json.loads(curve.attrib["data-authoritative-angle-deg"]) == (
            authoritative["angle_deg"]
        )
        assert json.loads(
            curve.attrib["data-authoritative-normalized-intensity"]
        ) == authoritative["normalized_radiant_intensity"]
        assert curve.attrib["data-profile-sha256"] == (
            authoritative["profile_sha256"]
        )


def test_polar_artifacts_are_deterministic_and_summary_hashes_authenticate(
    tmp_path: Path,
) -> None:
    profiles = _diagram_profiles()
    first = publish_angular_polar_comparison(tmp_path, profiles)
    second = publish_angular_polar_comparison(
        tmp_path, profiles, require_existing=True
    )
    assert first == second
    summary = {"polar_diagram": first}
    authenticate_angular_polar_comparison(
        tmp_path, summary["polar_diagram"]
    )
    for declaration in first["artifacts"].values():
        artifact = tmp_path / declaration["path"]
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == (
            declaration["sha256"]
        )
        assert artifact.stat().st_size == declaration["byte_length"]
    png = (tmp_path / POLAR_PNG_FILENAME).read_bytes()
    assert struct.unpack(">II", png[16:24]) == (
        POLAR_PNG_WIDTH,
        POLAR_PNG_HEIGHT,
    )
    assert (tmp_path / POLAR_SVG_FILENAME).read_bytes() == (
        build_angular_polar_comparison_svg(profiles)
    )


def test_two_degree_presentation_transform_preserves_authenticated_raw_data() -> None:
    raw = _diagram_profiles()
    presentation, transformation, provenance = build_presentation_profiles(raw)
    assert transformation["method"] == "2_degree_centered_angular_bin_mean"
    assert transformation["scientific_data_transform"] is True
    assert transformation["applied_consistently_to_all_four_profiles"] is True
    assert transformation["raw_profiles_preserved"] is True
    assert all(
        profile["angle_deg"] == list(PRESENTATION_BIN_CENTERS_DEG)
        for profile in presentation
    )
    assert [profile["profile_sha256"] for profile in raw] == [
        provenance[str(profile["profile_key"])]["raw_profile_sha256"]
        for profile in raw
    ]
    assert all(
        record["absolute_fwhm_difference_deg"] <= 0.5
        for record in provenance.values()
    )
    assert raw == _diagram_profiles()


def test_far_field_validation_distances_resolve_the_aperture_below_sample_step() -> None:
    assert [
        (
            level.key,
            level.angle_step_deg,
            level.azimuth_count,
            level.far_field_distance_m,
        )
        for level in VALIDATION_LEVELS
    ] == [
        ("distance_check", 0.125, 16, 80.0),
        ("sampling_check", 0.25, 8, 160.0),
        ("accepted", 0.125, 16, 160.0),
    ]
    for level in VALIDATION_LEVELS:
        payload = level.to_dict()
        assert payload["aperture_angular_extent_deg"] < level.angle_step_deg
        assert (
            payload["aperture_angular_extent_to_sample_interval_ratio"]
            < 1.0
        )
    for count in (8, 16):
        nodes, weights = far_field_azimuth_quadrature(
            count, method="square_symmetry_endpoint_trapezoid"
        )
        assert len(nodes) == len(weights) == count
        assert nodes[0] == 0.0
        assert nodes[-1] == 90.0
        assert math.fsum(weights) == pytest.approx(1.0)
        assert weights[0] == weights[-1]
        assert weights[0] * 2.0 == pytest.approx(weights[1])


def test_filtered_axis_plateau_accepts_sub_half_percent_raw_excess_unchanged() -> None:
    angles = [float(index) / 4.0 for index in range(361)]
    values = [
        (
            1.004
            if 1.25 <= angle <= 2.75
            else 1.0
            if angle < 40.0
            else max(0.0, (90.0 - angle) / 50.0)
        )
        for angle in angles
    ]
    original_angles = list(angles)
    original_values = list(values)
    observation = evaluate_axis_plateau_contract(angles, values)
    assert angles == original_angles
    assert values == original_values
    assert observation["accepted"] is True
    assert observation["tolerance_relative_excess"] == (
        AXIS_PLATEAU_RELATIVE_TOLERANCE
    )
    assert observation["raw_observation"] == {
        "optical_axis_intensity": 1.0,
        "global_maximum_intensity": 1.004,
        "maximum_angle_deg": 1.25,
        "relative_excess_above_optical_axis": pytest.approx(0.004),
    }
    filtered = observation["filtered_observation"]
    assert filtered["maximum_angle_deg"] == 2.0
    assert filtered["relative_excess_above_optical_axis"] <= 0.005
    sample_keys = [(angle, 45.0) for angle in angles if angle < 90.0]
    with pytest.raises(ValueError, match="must peak on the optical axis"):
        profile_from_azimuth_samples(
            angles_deg=angles,
            sample_keys=sample_keys,
            irradiance_values=values[:-1],
        )
    validation_profile = profile_from_azimuth_samples(
        angles_deg=angles,
        sample_keys=sample_keys,
        irradiance_values=values[:-1],
        normalize_to_optical_axis=True,
    )
    assert validation_profile.raw_radiant_intensity == tuple(values)
    assert validation_profile.normalized_radiant_intensity == tuple(values)
    assert (
        validation_profile.normalization_reference
        == "optical_axis_intensity"
    )


def test_filtered_axis_plateau_rejects_excess_above_half_percent() -> None:
    angles = [float(index) / 4.0 for index in range(361)]
    values = [
        (
            1.02
            if 1.25 <= angle <= 2.75
            else 1.0
            if angle < 40.0
            else max(0.0, (90.0 - angle) / 50.0)
        )
        for angle in angles
    ]
    original = list(values)
    with pytest.raises(
        AngularExperimentError,
        match=r"at 2°: relative excess 0\.0175 exceeds 0\.005",
    ):
        evaluate_axis_plateau_contract(
            angles, values, context="140 degree distance_check"
        )
    assert values == original


def _observed_standard_failure_samples(
    *,
    transient_fireflies: bool,
) -> tuple[
    list[float],
    list[tuple[float, float]],
    list[float],
]:
    """Reproduce the two isolated spikes in the failed 140-degree trace."""

    angles = [float(index) / 2.0 for index in range(181)]
    azimuths = (11.25, 33.75, 56.25, 78.75)
    optical_axis = 0.000385629925
    observed = {
        0.0: (
            0.0003856297,
            0.0003856300,
            0.0003856297,
            0.0003856303,
        ),
        2.0: (
            0.0003856341,
            0.0003856297,
            0.0003856382,
            0.0003856238,
        ),
        52.5: (
            0.0003593034,
            0.0003586579,
            0.002310739,
            0.0003591326,
        ),
        72.0: (
            0.001951446,
            0.0002546490,
            0.0002544484,
            0.0002546009,
        ),
    }
    sample_keys: list[tuple[float, float]] = []
    values: list[float] = []
    for angle in angles[:-1]:
        baseline = optical_axis * max(
            0.0, math.cos(math.radians(angle))
        ) ** 0.45
        direction_values = observed.get(
            angle,
            tuple(
                baseline * (1.0 + (index - 1.5) * 1e-6)
                for index in range(len(azimuths))
            ),
        )
        for index, (azimuth, value) in enumerate(
            zip(azimuths, direction_values, strict=True)
        ):
            clean = value
            if angle == 52.5 and index == 2:
                clean = 0.5 * (direction_values[1] + direction_values[3])
            elif angle == 72.0 and index == 0:
                clean = direction_values[3]
            replicates = (
                (value, clean, clean)
                if transient_fireflies
                else (value,)
            )
            for replicate in replicates:
                sample_keys.append((angle, azimuth))
                values.append(replicate)
    return angles, sample_keys, values


def test_standard_characterization_accepts_observed_firefly_signature() -> None:
    angles, failed_keys, failed_values = _observed_standard_failure_samples(
        transient_fireflies=False
    )
    with pytest.raises(ValueError, match="must peak on the optical axis"):
        profile_from_azimuth_samples(
            angles_deg=angles,
            sample_keys=failed_keys,
            irradiance_values=failed_values,
        )
    axis = math.fsum(failed_values[:4]) / 4.0
    maximum_values = failed_values[4 * 105 : 4 * 106]
    maximum = math.fsum(maximum_values) / 4.0
    assert axis == pytest.approx(0.000385629925)
    assert maximum == pytest.approx(0.000846958225)
    assert (maximum - axis) / maximum == pytest.approx(
        0.5446883758641108
    )
    assert angles[105] == 52.5

    angles, replicated_keys, replicated_values = (
        _observed_standard_failure_samples(transient_fireflies=True)
    )
    profile = profile_from_azimuth_samples(
        angles_deg=angles,
        sample_keys=replicated_keys,
        irradiance_values=replicated_values,
        normalize_to_optical_axis=True,
        required_direction_replicate_count=(
            CHARACTERIZATION_DIRECTION_REPLICATE_COUNT
        ),
        validate_filtered_axis_plateau=True,
    )
    assert profile.direction_replicate_count == 3
    assert profile.normalization_reference == "optical_axis_intensity"
    assert profile.axis_plateau_contract is not None
    assert profile.axis_plateau_contract["accepted"] is True
    assert profile.normalized_radiant_intensity[0] == pytest.approx(1.0)
    assert max(profile.normalized_radiant_intensity) == pytest.approx(
        profile.normalized_radiant_intensity[4], rel=1e-12
    )


@pytest.mark.parametrize("asymmetric", (False, True))
def test_standard_characterization_rejects_material_off_axis_profiles(
    asymmetric: bool,
) -> None:
    angles, sample_keys, values = _observed_standard_failure_samples(
        transient_fireflies=True
    )
    optical_axis = 0.000385629925
    for index, (angle, azimuth) in enumerate(sample_keys):
        if 19.0 <= angle <= 21.0 and (
            not asymmetric or azimuth == 56.25
        ) and index % CHARACTERIZATION_DIRECTION_REPLICATE_COUNT != 0:
            values[index] = optical_axis * (1.05 if not asymmetric else 1.3)
    with pytest.raises(
        AngularCharacterizationError,
        match="filtered peak exceeds the optical-axis plateau",
    ):
        profile_from_azimuth_samples(
            angles_deg=angles,
            sample_keys=sample_keys,
            irradiance_values=values,
            normalize_to_optical_axis=True,
            required_direction_replicate_count=(
                CHARACTERIZATION_DIRECTION_REPLICATE_COUNT
            ),
            validate_filtered_axis_plateau=True,
        )


def test_characterization_receiver_order_keeps_replicates_adjacent() -> None:
    receivers, keys = format_far_field_receivers(
        angles_deg=(0.0, 0.5, 90.0),
        azimuth_count=4,
        distance_m=20.0,
        direction_replicate_count=(
            CHARACTERIZATION_DIRECTION_REPLICATE_COUNT
        ),
    )
    rows = receivers.splitlines()
    assert len(rows) == len(keys) == 24
    assert keys[:6] == (
        (0.0, 11.25),
        (0.0, 11.25),
        (0.0, 11.25),
        (0.0, 33.75),
        (0.0, 33.75),
        (0.0, 33.75),
    )
    assert rows[0] == rows[1] == rows[2]
    assert rows[3] == rows[4] == rows[5]


def test_revalidated_diagrams_publish_clean_and_raw_hash_authenticated_pairs(
    tmp_path: Path,
) -> None:
    raw = _diagram_profiles()
    presentation, transformation, provenance = build_presentation_profiles(raw)
    declaration = publish_revalidated_angular_polar_comparison(
        tmp_path,
        presentation_profiles=presentation,
        raw_profiles=raw,
        transformation=transformation,
        curve_provenance=provenance,
    )
    repeated = publish_revalidated_angular_polar_comparison(
        tmp_path,
        presentation_profiles=presentation,
        raw_profiles=raw,
        transformation=transformation,
        curve_provenance=provenance,
    )
    assert repeated == declaration
    authenticate_angular_polar_comparison(tmp_path, declaration)
    assert declaration["scientific_data_transform"] is True
    assert set(declaration["artifacts"]) == {
        "authoritative_vector",
        "high_resolution_raster",
        "raw_audit_vector",
        "raw_audit_raster",
    }
    for artifact in declaration["artifacts"].values():
        data = (tmp_path / artifact["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
    clean = (tmp_path / POLAR_SVG_FILENAME).read_text(encoding="utf-8")
    raw_svg = (tmp_path / POLAR_RAW_SVG_FILENAME).read_text(encoding="utf-8")
    assert "2° centered angular-bin mean" in clean
    assert "authenticated raw profiles are archived" in clean
    assert "Authenticated raw completed-aperture validation profiles" in raw_svg
    assert "HPS" not in clean + raw_svg
    assert (tmp_path / POLAR_RAW_PNG_FILENAME).is_file()


def test_completed_summary_embeds_the_authenticated_diagram_declaration(
    tmp_path: Path,
) -> None:
    config = AngularExperimentConfig(tmp_path, 1000.0)
    profiles = _diagram_profiles()
    declaration = publish_angular_polar_comparison(tmp_path, profiles)
    results = []
    metric_values = {
        "minimum_ppfd": 900.0,
        "maximum_ppfd": 1100.0,
        "population_standard_deviation_ppfd": 25.0,
        "coefficient_of_variation": 0.025,
        "coefficient_of_variation_percent": 2.5,
        "degree_of_uniformity_percent": 90.0,
        "minimum_to_mean_uniformity": 0.9,
        "minimum_to_maximum_ppfd_ratio": 900.0 / 1100.0,
    }
    for case in planned_cases(config):
        case_root = tmp_path / "cases" / case.case_id
        case_root.mkdir(parents=True)
        (case_root / "metrics.json").write_text(
            json.dumps({"values": metric_values}),
            encoding="utf-8",
        )
        complete_scene_identity = hashlib.sha256(
            f"complete-scene:{case.case_id}".encode()
        ).hexdigest()
        uniform_control_identity = hashlib.sha256(
            f"uniform-control:{case.case_id}".encode()
        ).hexdigest()
        results.append(
            {
                "order": case.order,
                "case_id": case.case_id,
                "room_dimensions_ft": {
                    "length": case.room_length_ft,
                    "width": case.room_width_ft,
                },
                "room_dimensions_m_after_long_axis_alignment": {
                    "length": case.room_length_ft * 0.3048,
                    "width": case.room_width_ft * 0.3048,
                    "height": 3.0,
                },
                "variant": case.variant_key,
                "proposed_layout_mode": EXPERIMENT_PROPOSED_LAYOUT_MODE,
                "proposed_ring_mode": EXPERIMENT_PROPOSED_RING_MODE,
                "proposed_control_mode": EXPERIMENT_PROPOSED_CONTROL_MODE,
                "basis_matrix_solver_enabled": False,
                "complete_scene_trace_count": 1,
                "basis_column_count": 0,
                "target_mean_ppfd_umol_m2_s": 1000.0,
                "achieved_mean_ppfd_umol_m2_s": 1000.0,
                "sensor_grid": {
                    "resolution_x": case.expected_resolution_x,
                    "resolution_y": case.expected_resolution_y,
                },
                "solved_electrical_power_w": 10.0,
                "completed_aperture_ppf_umol_s": 26.0,
                "completed_aperture_ppe_umol_per_j": 2.6,
                "identities": {
                    "complete_scene_identity_sha256": (
                        complete_scene_identity
                    ),
                    "uniform_control_identity_sha256": (
                        uniform_control_identity
                    ),
                },
                "artifact_hashes": {},
            }
        )
    summary = build_experiment_summary(
        config,
        characterization_payload={"radiance_provenance": {}},
        polar_profiles=profiles,
        polar_diagram=declaration,
        case_results=results,
    )
    assert summary["polar_diagram"] == declaration
    rows = summary["stage_a_table_rows"]
    assert [
        (row["room_dimensions_ft"], row["variant"]) for row in rows
    ] == [
        (
            {"length": case.room_length_ft, "width": case.room_width_ft},
            case.variant_key,
        )
        for case in planned_cases(config)
    ]
    assert all(
        {
            "room_dimensions_ft",
            "variant",
            "measured_fwhm_deg",
            "achieved_mean_ppfd_umol_m2_s",
            "coefficient_of_variation_percent",
            "minimum_to_mean_uniformity",
            "minimum_to_maximum_ppfd_ratio",
            "solved_electrical_power_w",
        }.issubset(row)
        for row in rows
    )
    authenticate_angular_polar_comparison(
        tmp_path, summary["polar_diagram"]
    )


def test_polar_artifact_authentication_and_case_paths_fail_closed(
    tmp_path: Path,
) -> None:
    declaration = publish_angular_polar_comparison(
        tmp_path, _diagram_profiles()
    )
    (tmp_path / POLAR_SVG_FILENAME).write_bytes(b"tampered")
    with pytest.raises(
        AngularPolarDiagramError, match="failed authentication"
    ):
        authenticate_angular_polar_comparison(tmp_path, declaration)
    with pytest.raises(AngularExperimentError, match="unsafe artifact path"):
        _safe_case_artifact(tmp_path, "../outside")


def test_characterization_payload_identity_fails_closed_on_tampering(
    tmp_path: Path,
) -> None:
    profiles = {
        key: _profile(key, fwhm=fwhm)
        for key, fwhm in (
            (NATIVE_SMD_SOURCE_VARIANT, 118.0),
            (WIDE_SMD_SOURCE_VARIANT, 140.0),
            (NARROW_SMD_SOURCE_VARIANT, 100.0),
        )
    }
    calibrations: dict[str, dict[str, object]] = {}
    for key, profile in profiles.items():
        target = (
            None
            if key == NATIVE_SMD_SOURCE_VARIANT
            else 140.0
            if key == WIDE_SMD_SOURCE_VARIANT
            else 100.0
        )
        exponent = (
            0.0
            if target is None
            else ideal_aperture_radiance_exponent(target)
        )
        identity_payload = {
            "variant": key,
            "internal_radiance_exponent": exponent,
            "internal_modifier_gain": normalized_planar_radiance_gain(exponent),
            "profile_sha256": profile["profile_sha256"],
            "measured_completed_aperture_fwhm_deg": profile[
                "measured_fwhm_deg"
            ],
            "completed_aperture_transmission": 0.8,
            "reference_input_w": 1.0,
            "measured_reference_completed_aperture_ppf_umol_s": 2.6,
            "optical_representation": "physical_internal_source_stack",
        }
        calibration = CompletedApertureAngularCalibration(
            variant_key=key,
            internal_radiance_exponent=exponent,
            internal_modifier_gain=normalized_planar_radiance_gain(exponent),
            measured_completed_aperture_fwhm_deg=float(
                profile["measured_fwhm_deg"]
            ),
            completed_aperture_transmission=0.8,
            reference_input_w=1.0,
            measured_reference_completed_aperture_ppf_umol_s=2.6,
            target_completed_aperture_fwhm_deg=target,
            fwhm_tolerance_deg=0.5,
            reference_flux_relative_tolerance=0.005,
            profile_sha256=str(profile["profile_sha256"]),
            characterization_identity_sha256=_json_identity(
                identity_payload
            ),
        )
        calibrations[key] = calibration.to_dict()
    payload: dict[str, object] = {
        "schema_id": CHARACTERIZATION_SCHEMA_ID,
        "schema_version": CHARACTERIZATION_SCHEMA_VERSION,
        "status": "completed",
        "profiles": {
            key: MeasuredAngularProfile(
                angles_deg=tuple(profile["angle_deg"]),
                normalized_radiant_intensity=tuple(
                    profile["normalized_radiant_intensity"]
                ),
                measured_fwhm_deg=float(profile["measured_fwhm_deg"]),
                normalized_solid_angle_integral_sr=(
                    axisymmetric_integrated_flux(
                        profile["angle_deg"],
                        profile["normalized_radiant_intensity"],
                    )
                ),
                profile_sha256=str(profile["profile_sha256"]),
                normalization_reference="optical_axis_intensity",
                axis_plateau_contract=evaluate_axis_plateau_contract(
                    profile["angle_deg"],
                    profile["normalized_radiant_intensity"],
                ),
                direction_replicate_count=(
                    CHARACTERIZATION_DIRECTION_REPLICATE_COUNT
                ),
            ).to_dict()
            for key, profile in profiles.items()
        },
        "calibrations": calibrations,
    }
    payload["characterization_identity_sha256"] = _json_identity(payload)
    path = tmp_path / "angular-characterization.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    load_completed_aperture_characterization(path)
    payload["radiance_provenance"] = {"rtrace": "tampered"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        AngularCharacterizationError, match="identity is stale"
    ):
        load_completed_aperture_characterization(path)


def test_fixed_source_validation_executes_only_far_field_oconv_and_rtrace(
    tmp_path: Path,
) -> None:
    calibration = _calibration(
        WIDE_SMD_SOURCE_VARIANT,
        fwhm=140.0,
        transmission=0.8,
    )
    exact_cal = source_variant_cal_text(
        get_smd_source_variant(WIDE_SMD_SOURCE_VARIANT),
        calibration,
    )

    class FarFieldRunner:
        def __init__(self) -> None:
            self.commands = []

        def run(
            self,
            command,
            *,
            timeout_s=None,
            stderr_path=None,
        ) -> RunnerResult:
            del timeout_s, stderr_path
            self.commands.append(command)
            if command.label == "angular-characterization-oconv":
                command.stdout_path.write_bytes(b"fixed-source-octree")
            elif command.label == "angular-characterization-rtrace":
                receiver_count = len(
                    command.stdin_path.read_text(encoding="utf-8").splitlines()
                )
                command.stdout_path.write_text(
                    "1 1 1\n" * receiver_count,
                    encoding="utf-8",
                )
            else:
                raise AssertionError(f"unexpected execution: {command.label}")
            return RunnerResult(
                command_label=command.label,
                argv=command.argv,
                returncode=0,
                stdout_path=command.stdout_path,
                stderr_text=None,
                stderr_path=None,
                wall_time_s=0.0,
                success=True,
            )

    runner = FarFieldRunner()
    directory = tmp_path / "fixed-source"
    measured = execute_fixed_completed_aperture_profile(
        AngularCharacterizationConfig(
            output_directory=tmp_path,
            quality="direct",
            nthreads=6,
            angle_step_deg=0.25,
            azimuth_count=8,
            far_field_distance_m=40.0,
        ),
        exponent=calibration.internal_radiance_exponent,
        evaluation_directory=directory,
        authenticated_cal_text=exact_cal,
        runner=runner,
        oconv_path="/usr/bin/oconv",
        rtrace_path="/usr/bin/rtrace",
    )
    assert [command.label for command in runner.commands] == [
        "angular-characterization-oconv",
        "angular-characterization-rtrace",
    ]
    assert exact_cal is not None
    assert (directory / "smd_source_variant.cal").read_text() == exact_cal
    assert measured.normalization_reference == "optical_axis_intensity"
    assert measured.raw_radiant_intensity is not None
    trace_command = runner.commands[1]
    assert trace_command.argv[
        trace_command.argv.index("-n") : trace_command.argv.index("-n") + 2
    ] == ("-n", "6")
    archived = json.loads(
        (directory / "profile.json").read_text(encoding="utf-8")
    )
    assert archived["raw_azimuth_averaged_radiant_intensity"] == list(
        measured.raw_radiant_intensity
    )
    commands = " ".join(
        argument for command in runner.commands for argument in command.argv
    ).lower()
    assert all(
        prohibited not in commands
        for prohibited in ("rfluxmtx", "basis", "solver", "room.rad")
    )


def test_characterization_only_republish_keeps_cases_immutable_and_cannot_run_stage_a(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fspm_optics.application.angular_experiment as experiment_module

    root = tmp_path / "completed"
    root.mkdir()
    config = AngularExperimentConfig(root, 250.0, "quality", 3)
    (root / "angular-experiment-plan.json").write_text(
        json.dumps(build_experiment_plan(config), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert load_completed_experiment_config(root) == config
    calibrations = {
        NATIVE_SMD_SOURCE_VARIANT: _calibration(
            NATIVE_SMD_SOURCE_VARIANT, fwhm=118.0, transmission=0.8
        ),
        WIDE_SMD_SOURCE_VARIANT: _calibration(
            WIDE_SMD_SOURCE_VARIANT, fwhm=140.0, transmission=0.8
        ),
        NARROW_SMD_SOURCE_VARIANT: _calibration(
            NARROW_SMD_SOURCE_VARIANT, fwhm=100.0, transmission=0.8
        ),
    }
    characterization = {
        "characterization_identity_sha256": "c" * 64,
        "calibrations": {
            key: calibration.to_dict()
            for key, calibration in calibrations.items()
        },
        "radiance_provenance": {},
    }
    synthetic_results: dict[str, dict[str, object]] = {}
    for case in planned_cases(config):
        case_root = root / "cases" / case.case_id
        complete_scene = case_root / "complete-scene"
        complete_scene.mkdir(parents=True)
        (case_root / CASE_RESULT_FILENAME).write_text(
            f"immutable case {case.case_id}\n", encoding="utf-8"
        )
        (complete_scene / "uniform_complete_reference_ppfd.npy").write_bytes(
            f"immutable reference field {case.case_id}".encode()
        )
        (complete_scene / "room.rad").write_text(
            f"immutable room {case.case_id}\n", encoding="utf-8"
        )
        source_path = complete_scene / "uniform_complete_source.rad"
        source_path.write_text(
            f"immutable source {case.case_id}\n", encoding="utf-8"
        )
        (case_root / CASE_CONTROL_FILENAME).write_text(
            f"immutable uniform control {case.case_id}\n", encoding="utf-8"
        )
        (case_root / "metrics.json").write_text(
            f"immutable metrics {case.case_id}\n", encoding="utf-8"
        )
        (case_root / "ppfd.csv").write_text(
            f"immutable ppfd {case.case_id}\n", encoding="utf-8"
        )
        cal_text = source_variant_cal_text(
            get_smd_source_variant(case.variant_key),
            calibrations[case.variant_key],
        )
        artifact_hashes: dict[str, str] = {}
        artifact_hashes["complete-scene/uniform_complete_source.rad"] = (
            hashlib.sha256(source_path.read_bytes()).hexdigest()
        )
        cal_hash = None
        if cal_text is not None:
            cal_path = complete_scene / "smd_source_variant.cal"
            cal_path.write_text(cal_text, encoding="utf-8")
            cal_hash = hashlib.sha256(cal_path.read_bytes()).hexdigest()
            artifact_hashes[
                "complete-scene/smd_source_variant.cal"
            ] = cal_hash
        synthetic_results[case.case_id] = {
            "order": case.order,
            "case_id": case.case_id,
            "case_result_identity_sha256": hashlib.sha256(
                case.case_id.encode()
            ).hexdigest(),
            "identities": {
                "source_identity_sha256": hashlib.sha256(
                    f"source:{case.case_id}".encode()
                ).hexdigest(),
                "angular_cal_sha256": cal_hash,
            },
            "artifact_hashes": artifact_hashes,
        }

    def tree_hashes() -> dict[str, str]:
        cases = root / "cases"
        return {
            path.relative_to(cases).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(cases.rglob("*"))
            if path.is_file()
        }

    before = tree_hashes()
    monkeypatch.setattr(
        "fspm_optics.application.angular_republish."
        "load_completed_aperture_characterization",
        lambda _path: (
            calibrations,
            {
                key: {"profile_sha256": calibration.profile_sha256}
                for key, calibration in calibrations.items()
            },
            characterization,
        ),
    )
    monkeypatch.setattr(
        "fspm_optics.application.angular_republish.validate_completed_case",
        lambda result_path, **_kwargs: synthetic_results[
            result_path.parent.name
        ],
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "Stage A, complete-scene, uniform-control, or room execution was reached"
        )

    monkeypatch.setattr(experiment_module, "_execute_case", forbidden)
    monkeypatch.setattr(experiment_module, "apply_target_control", forbidden)
    assert not hasattr(experiment_module, "execute_basis_matrix")
    assert not hasattr(experiment_module, "plan_basis_workspace")
    calls: list[Path] = []

    def fixed_profile(
        level_config,
        _exponent: float,
        directory: Path,
        _cal_text: str | None,
    ) -> MeasuredAngularProfile:
        assert not directory.is_relative_to(root / "cases")
        assert "room.rad" not in directory.as_posix()
        calls.append(directory)
        directory.mkdir(parents=True)
        (directory / "fixed-source-far-field-only.txt").write_text(
            "no complete scene, uniform control, room scene, calibration, or "
            "flux measurement\n",
            encoding="utf-8",
        )
        key = directory.parent.name
        fwhm = calibrations[key].measured_completed_aperture_fwhm_deg
        angles = tuple(
            float(index) * level_config.angle_step_deg
            for index in range(
                int(90.0 / level_config.angle_step_deg) + 1
            )
        )
        exponent = ideal_intensity_exponent(fwhm)
        values = normalized_profile(
            [
                max(0.0, math.cos(math.radians(angle))) ** exponent
                for angle in angles
            ]
        )
        return MeasuredAngularProfile(
            angles_deg=angles,
            normalized_radiant_intensity=values,
            measured_fwhm_deg=measured_fwhm_deg(angles, values),
            normalized_solid_angle_integral_sr=axisymmetric_integrated_flux(
                angles, values
            ),
            profile_sha256=profile_sha256(angles, values),
        )

    published: dict[str, object] = {}

    def fake_publisher(
        output_directory: Path,
        **kwargs: object,
    ) -> dict[str, object]:
        assert output_directory == root
        published.update(kwargs)
        return {"schema_id": "test-diagram"}

    def fake_summary(
        _config: AngularExperimentConfig,
        **kwargs: object,
    ) -> dict[str, object]:
        assert kwargs["case_results"] == [
            synthetic_results[case.case_id] for case in planned_cases(config)
        ]
        return {
            "schema_id": "test-summary",
            "summary_identity_sha256": "0" * 64,
            "finalization_provenance": kwargs["finalization_provenance"],
        }

    monkeypatch.setattr(
        "fspm_optics.application.angular_republish.build_experiment_summary",
        fake_summary,
    )
    summary_path = republish_characterization_only(
        config,
        profile_executor=fixed_profile,
        diagram_publisher=fake_publisher,
        progress=lambda _message: None,
    )
    assert summary_path.is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["angular_profile_revalidation"]["scientific_data_transform"]
    assert set(summary["angular_profile_revalidation"]["raw_profiles"]) == {
        "conventional",
        NATIVE_SMD_SOURCE_VARIANT,
        WIDE_SMD_SOURCE_VARIANT,
        NARROW_SMD_SOURCE_VARIANT,
    }
    assert set(
        summary["angular_profile_revalidation"]["presentation_profiles"]
    ) == set(summary["angular_profile_revalidation"]["raw_profiles"])
    assert (
        summary["finalization_provenance"]["case_artifacts_recomputed"]
        is False
    )
    assert len(calls) == 9
    assert before == tree_hashes()
    assert set(published) == {
        "presentation_profiles",
        "raw_profiles",
        "transformation",
        "curve_provenance",
    }
    validation = json.loads(
        (
            root
            / VALIDATION_DIRECTORY_NAME
            / VALIDATION_FILENAME
        ).read_text(encoding="utf-8")
    )
    assert validation["execution_scope"] == {
        "fixed_source_far_field_completed_aperture_measurement_only": True,
        "stage_a_executed": False,
        "complete_scene_trace_executed": False,
        "room_radiance_executed": False,
        "uniform_control_recomputed": False,
        "cal_parameters_recalibrated": False,
        "transport_normalization_remeasured": False,
        "post_trace_scaling": False,
        "raw_validation_samples_preserved_unchanged": True,
    }
    assert (
        validation["validation_settings"][
            "axis_plateau_relative_excess_tolerance"
        ]
        == 0.005
    )
    distance_selection = validation["validation_settings"][
        "far_field_distance_selection"
    ]
    assert validation["validation_settings"]["experiment_plan_threads"] == 3
    assert validation["validation_settings"]["validation_rtrace_threads"] == 1
    assert distance_selection["aperture_side_m"] == 0.126
    assert distance_selection["superseded_pair"]["distance_check_m"] == 20.0
    assert distance_selection["superseded_pair"]["accepted_m"] == 40.0
    assert distance_selection["selected_pair"]["distance_check_m"] == 80.0
    assert distance_selection["selected_pair"]["accepted_m"] == 160.0
    for variant in (
        NATIVE_SMD_SOURCE_VARIANT,
        WIDE_SMD_SOURCE_VARIANT,
        NARROW_SMD_SOURCE_VARIANT,
    ):
        for level in validation["convergence"][variant]["levels"]:
            assert {
                "raw_fwhm_deg",
                "filtered_fwhm_deg",
                "angle_step_deg",
                "azimuth_count_over_one_square_symmetry_quadrant",
                "far_field_distance_m",
                "aperture_angular_extent_deg",
            }.issubset(level)
            assert level["rtrace_threads"] == 1
            plateau = level["axis_plateau_contract"]
            assert plateau["accepted"] is True
            assert set(plateau["raw_observation"]) == {
                "optical_axis_intensity",
                "global_maximum_intensity",
                "maximum_angle_deg",
                "relative_excess_above_optical_axis",
            }
            assert set(plateau["filtered_observation"]) == set(
                plateau["raw_observation"]
            )
        for check in validation["convergence"][variant]["checks"]:
            assert (
                check["filtered_fwhm_difference_threshold_deg"] == 0.25
            )
            assert check["profile_error_threshold"] == 0.02
            assert check["profile_error_metric"].startswith("rms_")
