from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from fspm_optics.application import surface_flux_calibration as historical_calibration
from fspm_optics.application import surface_flux_recalibration as recalibration
from fspm_optics.application.surface_flux_recalibration import (
    D5_BAND_ORDER,
    D5_COMPLETION_SCHEMA_ID,
    D5_JOB_COUNT,
    D5_QUALITY_ORDER,
    D5_RECEIVERS_SHA256,
    D5_REFERENCE_LEVELS_UMOL_M2_S,
    D5_SAMPLING_PROFILE_ID,
    D5_STAGE_B_ARTIFACT_COUNT,
    D5_TOPOLOGY_SHA256,
    SurfaceFluxRecalibrationConfig,
    SurfaceFluxRecalibrationError,
    build_surface_flux_recalibration_plan,
    recalibration_jobs,
    run_surface_flux_recalibration,
)
from fspm_optics.geometry.sensor_grid import SensorPoint, format_rtrace_receivers
from fspm_optics.radiance.commands import build_baseline_rtrace_command
from fspm_optics.radiance.options import radiance_options
from fspm_optics.radiance.versioning import (
    RadianceExecutableVersion,
    RadianceInstallation,
)
from fspm_optics.surface_flux_recalibration_cli import (
    build_parser,
    config_from_namespace,
)


REPOSITORY = Path(__file__).parents[1]
CREATED_AT = "2026-07-15T12:00:00Z"


def _installation() -> RadianceInstallation:
    return RadianceInstallation(
        oconv=RadianceExecutableVersion(
            name="oconv",
            path=Path("/test-radiance/bin/oconv"),
            version_text="mock Radiance oconv",
        ),
        rtrace=RadianceExecutableVersion(
            name="rtrace",
            path=Path("/test-radiance/bin/rtrace"),
            version_text="mock Radiance rtrace",
        ),
    )


class _NoExecutionRunner:
    def run(self, command, *, timeout_s=None, stderr_path=None):
        del command, timeout_s, stderr_path
        raise AssertionError("mock D5 executor must not delegate to Radiance")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _artifact(root: Path, relative: str, data: bytes, role: str) -> dict[str, object]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "role": role,
        "path": relative,
        "media_type": (
            "text/plain"
            if path.suffix in {".rad", ".pts", ".rgb", ".log"}
            else "application/octet-stream"
        ),
        "byte_length": len(data),
        "sha256": _sha(data),
    }


def _rtrace_command(quality: str, ambient_path: str) -> dict[str, object]:
    options = radiance_options(quality)
    if quality != "direct":
        options.extend(["-af", ambient_path])
    return {
        "label": f"mock_{quality}_rtrace",
        "argv": [
            "/test-radiance/bin/rtrace",
            "-h",
            "-I+",
            "-n",
            "1",
            *options,
            "scene.oct",
        ],
        "stdin_path": "receivers.pts",
        "stdout_path": "receiver.rgb",
        "cwd": ".",
        "environment": {},
        "shell": False,
    }


def _mock_execute_factory(calls: list[str]):
    def execute(
        root,
        *,
        config,
        job,
        scene,
        material_plan,
        runner,
        installation,
        configuration_sha256,
        event_sink,
    ):
        del config, material_plan, runner, installation, event_sink
        calls.append(job.job_id)
        pilot_raw = _artifact(
            root,
            "reference/pilot/reference.rgb",
            b"250 250 250\n",
            "reference_pilot_raw_rgb",
        )
        final_raw = _artifact(
            root,
            "reference/final/reference.rgb",
            b"500 500 500\n",
            "reference_final_raw_rgb",
        )
        sensor = _artifact(
            root,
            "reference/horizontal-reference-plane.pts",
            b"0 0 0 0 0 1\n",
            "horizontal_reference_plane_receivers",
        )

        def trace(phase: str, raw: dict[str, object]) -> dict[str, object]:
            source = _artifact(
                root,
                f"reference/{phase}/source.rad",
                historical_calibration.render_neutral_source(1.0).encode("ascii"),
                f"reference_{phase}_source",
            )
            return {
                "source_amplitude": 1.0,
                "source_artifact": source,
                "raw_reference_artifact": raw,
                "metrics": {
                    "sample_count": 441,
                    "mean_ppfd_umol_m2_s": job.requested_level,
                    "coefficient_of_variation": 0.0,
                },
                "commands": [
                    {
                        "label": f"mock_{phase}_oconv",
                        "argv": ["/test-radiance/bin/oconv", "source.rad"],
                        "stdin_path": None,
                        "stdout_path": "scene.oct",
                        "cwd": ".",
                        "environment": {},
                        "shell": False,
                    },
                    _rtrace_command(
                        job.quality,
                        f"reference/{phase}/scene.amb",
                    ),
                ],
                "plants_present": False,
            }

        bands = []
        receiver_bytes = b"\0" * (scene.counts.receiver_count * 8)
        for index, band_id in enumerate(D5_BAND_ORDER):
            band_root = f"bands/{index:02d}-{band_id}"
            source_artifact = _artifact(
                root,
                f"{band_root}/source.rad",
                historical_calibration.render_neutral_source(1.0).encode("ascii"),
                f"{band_id}_source",
            )
            material_artifact = _artifact(
                root,
                f"{band_root}/leaf-material.rad",
                f"mock {band_id} material\n".encode("ascii"),
                f"{band_id}_leaf_material",
            )
            relative = f"bands/{index:02d}-{band_id}/receiver-values.v1.f64le.bin"
            receiver = _artifact(
                root,
                relative,
                receiver_bytes,
                f"{band_id}_receiver_values",
            )
            receiver.update(
                {
                    "row_count": scene.counts.receiver_count,
                    "stride_bytes": 8,
                }
            )
            bands.append(
                {
                    "order_index": index,
                    "band_id": band_id,
                    "definition": {"band_id": band_id},
                    "source": {
                        "source_model_id": (
                            historical_calibration.SOURCE_MODEL_ID
                        ),
                        "source_definition_sha256": (
                            historical_calibration.canonical_neutral_source_definition()[
                                "source_definition_sha256"
                            ]
                        ),
                        "photon_fraction": 0.25,
                        "rendered_amplitude": 1.0,
                        "same_spatial_and_angular_field_for_every_band": True,
                    },
                    "source_sha256": source_artifact["sha256"],
                    "material": {"band_id": band_id},
                    "material_sha256": material_artifact["sha256"],
                    "material_artifact": material_artifact,
                    "receiver_values": receiver,
                    "commands": {
                        "oconv": {
                            "label": f"mock_{band_id}_oconv",
                            "argv": ["/test-radiance/bin/oconv", "source.rad"],
                            "stdin_path": None,
                            "stdout_path": "scene.oct",
                            "cwd": ".",
                            "environment": {},
                            "shell": False,
                        },
                        "rtrace": _rtrace_command(
                            job.quality,
                            f"bands/{index:02d}-{band_id}/scene.amb",
                        ),
                    },
                    "post_trace_transforms": [],
                }
            )
        return {
            "schema_id": "historical-executor-placeholder",
            "schema_version": 0,
            "configuration_sha256": configuration_sha256,
            "job_id": job.job_id,
            "artifact_root_from_report": f"levels/{job.job_id}",
            "kind": "primary",
            "quality": job.quality,
            "requested_reference_level_umol_m2_s": job.requested_level,
            "reference": {
                "plant_free": True,
                "horizontal_sensor_grid": sensor,
                "amplitude_resolution": {
                    "method": (
                        "independent unit-amplitude pilot then independent final trace"
                    ),
                    "pilot": trace("pilot", pilot_raw),
                    "resolved_total_source_radiance_amplitude": 1.0,
                    "final": trace("final", final_raw),
                    "requested_minus_achieved_umol_m2_s": 0.0,
                    "relative_achievement_error": 0.0,
                },
                "acceptance": {"pass": True},
            },
            "plant_transport": {
                "independent_execution": True,
                "band_order": list(D5_BAND_ORDER),
                "band_count": 4,
                "far_red_executed": False,
                "equal_photon_fraction_per_band": 0.25,
                "band_source_amplitude": 1.0,
                "receiver_count_per_band": scene.counts.receiver_count,
                "bands": bands,
            },
            "scene_identity": {
                "plant": {
                    "topology_sha256": D5_TOPOLOGY_SHA256,
                    "receivers_sha256": D5_RECEIVERS_SHA256,
                }
            },
            "juvenile_identity": {
                "topology_sha256": D5_TOPOLOGY_SHA256,
                "receivers_sha256": D5_RECEIVERS_SHA256,
            },
            "statistics": {},
            "validation": {
                "all_receiver_values_finite_and_nonnegative": True,
                "receiver_identity_and_order_validated": True,
                "all_numerical_and_conservation_validations_pass": True,
            },
            "commands": [],
        }

    return execute


def _mock_execute_with_historical_stage_a_command_hoisting(
    calls: list[str],
):
    """Match the real D1 executor shape consumed by the D5 wrapper."""

    execute = _mock_execute_factory(calls)

    def execute_with_hoisting(*args, **kwargs):
        result = execute(*args, **kwargs)
        job = kwargs["job"]
        resolution = result["reference"]["amplitude_resolution"]
        commands = []
        for phase in ("pilot", "final"):
            phase_commands = resolution[phase].pop("commands")
            for command_kind, command in zip(
                ("oconv", "rtrace"), phase_commands, strict=True
            ):
                command["label"] = (
                    f"surface_flux_calibration_{job.job_id}_reference_"
                    f"{phase}_{command_kind}"
                )
            commands.extend(phase_commands)
        for band in result["plant_transport"]["bands"]:
            commands.extend(
                (band["commands"]["oconv"], band["commands"]["rtrace"])
            )
        result["commands"] = commands
        result["command_count"] = len(commands)
        return result

    return execute_with_hoisting


def test_fixed_plan_has_exact_optimized_matrix_and_no_radiance_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        recalibration,
        "discover_radiance_installation",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("--plan must not discover Radiance")
        ),
    )
    output = tmp_path / "d5"
    plan = build_surface_flux_recalibration_plan(
        SurfaceFluxRecalibrationConfig(output_directory=output)
    )

    assert plan["radiance_discovery_performed"] is False
    assert plan["radiance_execution_performed"] is False
    assert plan["job_count"] == D5_JOB_COUNT == 6
    assert plan["stage_a_reference_trace_count"] == 12
    assert plan["stage_b_receiver_artifact_count"] == D5_STAGE_B_ARTIFACT_COUNT == 24
    assert plan["scene_identity"]["sampling_profile_id"] == D5_SAMPLING_PROFILE_ID
    assert plan["scene_identity"]["fixed_topology_sha256"] == D5_TOPOLOGY_SHA256
    assert plan["scene_identity"]["fixed_receivers_sha256"] == D5_RECEIVERS_SHA256
    definition = historical_calibration.canonical_neutral_source_definition()
    assert plan["experiment_id"].endswith("-v3")
    assert plan["d5_identity_version"] == 3
    assert plan["scene_identity"]["source_model_id"] == definition["source_model_id"]
    assert plan["scene_identity"]["radiance_emitter_material_type"] == "glow"
    assert plan["scene_identity"]["glow_maximum_radius"] == 0.0
    assert plan["scientific_configuration"]["neutral_source"] == {
        "source_model_id": definition["source_model_id"],
        "source_definition_sha256": definition["source_definition_sha256"],
        "radiance_emitter_material_type": "glow",
        "glow_maximum_radius": 0.0,
        "executed_families_use_ambient_evaluation": True,
    }
    assert plan["calibrated_quality_families"] == [
        "standard",
        "quality",
        "rigorous",
    ]
    assert plan["deferred_runtime_display_quality_mapping"] == {
        "mapping": {"direct": "standard"},
        "scope": "display normalization only",
        "raw_transport_uses_requested_quality": True,
        "implemented_in_d5_a1": False,
    }
    assert plan["quality_proxy_policy"] == {
        "proxy_among_executed_calibration_families": False,
        "direct_calibration_executed": False,
    }
    assert not output.exists()


def test_executed_stage_a_uses_historical_glow_and_ambient_command_semantics() -> None:
    source = historical_calibration.render_neutral_source(1.0)
    definition = historical_calibration.canonical_neutral_source_definition()
    assert source == definition["radiance_primitives"][
        "normalized_unit_radiance_text"
    ]
    assert "void glow neutral_reference_hemisphere_glow" in source
    assert source.endswith("4 0 0 1 180\n")
    glow_arguments = source.splitlines()[4].split()
    assert glow_arguments == ["4", "1", "1", "1", "0"]
    receiver_text = format_rtrace_receivers((SensorPoint(0.0, 0.0, 0.005),))
    assert receiver_text == "0.000000 0.000000 0.005000 0.000000 0.000000 1.000000\n"
    command = build_baseline_rtrace_command(
        octree=Path("scene.oct"),
        receiver_input=Path("reference.pts"),
        rgb_output=Path("reference.rgb"),
        options=radiance_options(
            "standard",
            ambient_cache=Path("reference/scene.amb"),
        ),
        nthreads=1,
        rtrace_bin=Path("/test-radiance/bin/rtrace"),
    )
    assert command.argv[:5] == (
        "/test-radiance/bin/rtrace",
        "-h",
        "-I+",
        "-n",
        "1",
    )
    assert command.argv[-1] == "scene.oct"
    assert command.argv[command.argv.index("-ab") + 1] == "3"
    assert command.argv[command.argv.index("-af") + 1] == "reference/scene.amb"
    recalibration._validate_rtrace_options(
        {"argv": list(command.argv)},
        quality="standard",
        job_id="standard-250",
    )


@pytest.mark.parametrize("old_version", ("v1", "v2"))
def test_v1_and_v2_configuration_cannot_resume_as_v3(
    old_version: str,
    tmp_path: Path,
) -> None:
    output = tmp_path / old_version
    output.mkdir()
    stored = {
        "schema_id": recalibration.D5_CONFIGURATION_SCHEMA_ID,
        "schema_version": recalibration.D5_CONFIGURATION_SCHEMA_VERSION,
        "experiment_id": (
            f"phase27g-d5-a1-optimized-surface-flux-sweep-{old_version}"
        ),
        "created_at_utc": CREATED_AT,
        "initial_cli_configuration": {},
        "identity": {"configuration_sha256": "a" * 64},
    }
    (output / recalibration.D5_CONFIGURATION_NAME).write_text(
        recalibration._pretty_json(stored),
        encoding="utf-8",
    )
    with pytest.raises(
        SurfaceFluxRecalibrationError,
        match="does not match exactly",
    ):
        recalibration._prepare_output_root(
            SurfaceFluxRecalibrationConfig(
                output_directory=output,
                resume=True,
            ),
            identity={"configuration_sha256": "b" * 64},
            created_at_utc=CREATED_AT,
        )


def test_stage_a_zero_trace_remains_fail_closed(tmp_path: Path) -> None:
    raw = tmp_path / "reference.rgb"
    raw.write_text("0 0 0\n0 0 0\n", encoding="ascii")
    with pytest.raises(
        historical_calibration.SurfaceFluxCalibrationError,
        match="reference-plane mean must be positive",
    ):
        historical_calibration._decode_reference_metrics(raw, expected_count=2)


@pytest.mark.parametrize(
    "bad_path",
    (
        "/tmp/reference/pilot/source.rad",
        "../reference/pilot/source.rad",
        ".standard-250.stale.tmp/reference/pilot/source.rad",
        "jobs/standard-250/reference/pilot/source.rad",
    ),
)
def test_stage_a_authority_rejects_noncanonical_or_staging_paths(
    bad_path: str,
    tmp_path: Path,
) -> None:
    artifact = {
        "role": "reference_pilot_source",
        "path": bad_path,
        "media_type": "text/plain",
        "byte_length": 1,
        "sha256": _sha(b"x"),
    }

    with pytest.raises(
        SurfaceFluxRecalibrationError,
        match="canonical artifact authority is invalid",
    ):
        recalibration._validate_stage_a_artifact(
            tmp_path,
            artifact,
            expected_path="reference/pilot/source.rad",
            expected_role="reference_pilot_source",
            label="D5 Stage A pilot source",
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("role", "reference_final_source"),
        ("media_type", "application/octet-stream"),
        ("byte_length", 0),
        ("sha256", "0" * 63),
    ),
)
def test_stage_a_authority_rejects_role_size_hash_or_type_substitution(
    field: str,
    replacement: object,
    tmp_path: Path,
) -> None:
    artifact = {
        "role": "reference_pilot_source",
        "path": "reference/pilot/source.rad",
        "media_type": "text/plain",
        "byte_length": 1,
        "sha256": _sha(b"x"),
    }
    artifact[field] = replacement

    with pytest.raises(
        SurfaceFluxRecalibrationError,
        match="canonical artifact authority is invalid",
    ):
        recalibration._validate_stage_a_artifact(
            tmp_path,
            artifact,
            expected_path="reference/pilot/source.rad",
            expected_role="reference_pilot_source",
            label="D5 Stage A pilot source",
        )


def test_job_order_is_quality_major_and_level_minor_without_proxies() -> None:
    jobs = recalibration_jobs()
    assert D5_QUALITY_ORDER == ("standard", "quality", "rigorous")
    assert D5_REFERENCE_LEVELS_UMOL_M2_S == (250.0, 500.0)
    assert [(job.quality, job.requested_level) for job in jobs] == [
        (quality, level)
        for quality in D5_QUALITY_ORDER
        for level in D5_REFERENCE_LEVELS_UMOL_M2_S
    ]
    assert [job.job_id for job in jobs] == [
        "standard-250",
        "standard-500",
        "quality-250",
        "quality-500",
        "rigorous-250",
        "rigorous-500",
    ]


def test_exact_quality_option_identity_rejects_fallback_and_extra_flags() -> None:
    for quality in D5_QUALITY_ORDER:
        recalibration._validate_rtrace_options(
            _rtrace_command(quality, f"{quality}/scene.amb"),
            quality=quality,
            job_id=f"{quality}-250",
        )
    substituted = _rtrace_command("quality", "quality/scene.amb")
    substituted["argv"].insert(-1, "-extra")
    with pytest.raises(
        SurfaceFluxRecalibrationError,
        match="option identity changed",
    ):
        recalibration._validate_rtrace_options(
            substituted,
            quality="quality",
            job_id="quality-250",
        )
    with pytest.raises(
        SurfaceFluxRecalibrationError,
        match="not an executed calibrated family",
    ):
        recalibration._validate_rtrace_options(
            _rtrace_command("direct", "direct/scene.amb"),
            quality="direct",
            job_id="direct-250",
        )
    with pytest.raises(
        SurfaceFluxRecalibrationError,
        match="not an executed calibrated family",
    ):
        recalibration._validate_rtrace_options(
            _rtrace_command("standard", "standard/scene.amb"),
            quality="instant",
            job_id="instant-250",
        )


def test_mock_sweep_publishes_24_raw_artifacts_and_resume_executes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "d5"
    calls: list[str] = []
    monkeypatch.setattr(recalibration, "_execute_level", _mock_execute_factory(calls))
    publication = run_surface_flux_recalibration(
        SurfaceFluxRecalibrationConfig(output_directory=output, threads=1),
        runner=_NoExecutionRunner(),
        radiance_installation=_installation(),
        created_at_utc=CREATED_AT,
        repository_root=REPOSITORY,
    )

    assert calls == [job.job_id for job in recalibration_jobs()]
    assert publication.completion["schema_id"] == D5_COMPLETION_SCHEMA_ID
    assert publication.completion["stage_b_receiver_artifact_count"] == 24
    assert publication.completion["stage_a_reference_trace_count"] == 12
    assert len(publication.completion["ordered_stage_b_receiver_artifacts"]) == 24
    assert publication.completion["calibrated_quality_families"] == [
        "standard",
        "quality",
        "rigorous",
    ]
    assert publication.completion[
        "deferred_runtime_display_quality_mapping"
    ] == {"direct": "standard"}
    assert publication.completion["promotion_state"] == {
        "candidate_coefficients_generated": False,
        "production_resource_generated": False,
        "optimized_coloring_enabled": False,
    }

    monkeypatch.setattr(
        recalibration,
        "_execute_level",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("authenticated resume must not execute a completed job")
        ),
    )
    resumed = run_surface_flux_recalibration(
        SurfaceFluxRecalibrationConfig(
            output_directory=output,
            threads=1,
            resume=True,
        ),
        runner=_NoExecutionRunner(),
        radiance_installation=_installation(),
        repository_root=REPOSITORY,
    )
    assert resumed.completion == publication.completion


def test_real_stage_a_command_hoisting_is_canonicalized_before_atomic_promotion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "d5"
    calls: list[str] = []
    monkeypatch.setattr(
        recalibration,
        "_execute_level",
        _mock_execute_with_historical_stage_a_command_hoisting(calls),
    )

    publication = run_surface_flux_recalibration(
        SurfaceFluxRecalibrationConfig(output_directory=output, threads=1),
        runner=_NoExecutionRunner(),
        radiance_installation=_installation(),
        created_at_utc=CREATED_AT,
        repository_root=REPOSITORY,
    )

    assert calls == [job.job_id for job in recalibration_jobs()]
    assert publication.completion["status"] == "complete"
    assert not any(
        path.name.startswith(".") and path.name.endswith(".tmp")
        for path in (output / recalibration.D5_JOBS_DIRECTORY).iterdir()
    )
    committed = output / "jobs/standard-250"
    result = recalibration._read_json_object(
        committed / recalibration.D5_JOB_RESULT_NAME
    )
    resolution = result["reference"]["amplitude_resolution"]
    for phase in ("pilot", "final"):
        commands = resolution[phase]["commands"]
        assert [command["label"] for command in commands] == [
            f"surface_flux_calibration_standard-250_reference_{phase}_oconv",
            f"surface_flux_calibration_standard-250_reference_{phase}_rtrace",
        ]
        assert resolution[phase]["source_artifact"]["path"] == (
            f"reference/{phase}/source.rad"
        )
        assert resolution[phase]["raw_reference_artifact"]["path"] == (
            f"reference/{phase}/reference.rgb"
        )
    job_completion = recalibration._read_json_object(
        committed / recalibration.D5_JOB_COMPLETION_NAME
    )
    inventory_paths = {
        artifact["path"]
        for artifact in job_completion["ordered_artifact_inventory"]
    }
    assert "reference/pilot/source.rad" in inventory_paths
    assert "reference/pilot/reference.rgb" in inventory_paths
    assert "reference/final/source.rad" in inventory_paths
    assert "reference/final/reference.rgb" in inventory_paths
    top_stage_a = publication.completion["ordered_stage_a_evidence"][0]
    assert top_stage_a["pilot_source_artifact"]["path"] == (
        "jobs/standard-250/reference/pilot/source.rad"
    )
    assert [
        command["label"] for command in top_stage_a["pilot_commands"]
    ] == [
        "surface_flux_calibration_standard-250_reference_pilot_oconv",
        "surface_flux_calibration_standard-250_reference_pilot_rtrace",
    ]
    assert top_stage_a["final_raw_reference_artifact"]["path"] == (
        "jobs/standard-250/reference/final/reference.rgb"
    )


def test_resume_rejects_corrupted_receiver_before_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "d5"
    monkeypatch.setattr(recalibration, "_execute_level", _mock_execute_factory([]))
    run_surface_flux_recalibration(
        SurfaceFluxRecalibrationConfig(output_directory=output, threads=1),
        runner=_NoExecutionRunner(),
        radiance_installation=_installation(),
        created_at_utc=CREATED_AT,
        repository_root=REPOSITORY,
    )
    raw = (
        output
        / "jobs/standard-250/bands/00-blue/receiver-values.v1.f64le.bin"
    )
    raw.write_bytes(raw.read_bytes() + b"corruption")

    with pytest.raises(SurfaceFluxRecalibrationError, match="failed validation"):
        run_surface_flux_recalibration(
            SurfaceFluxRecalibrationConfig(
                output_directory=output,
                threads=1,
                resume=True,
            ),
            runner=_NoExecutionRunner(),
            radiance_installation=_installation(),
            repository_root=REPOSITORY,
        )


def test_d5_cli_exposes_only_fixed_plan_execution_and_resume_controls() -> None:
    parser = build_parser()
    plan_args = parser.parse_args(["--plan", "--output-dir", "/tmp/d5-plan"])
    config = config_from_namespace(plan_args)
    assert plan_args.plan is True
    assert config.quality_families == D5_QUALITY_ORDER
    assert config.reference_levels_umol_m2_s == D5_REFERENCE_LEVELS_UMOL_M2_S
    assert config.sampling_profile_id == D5_SAMPLING_PROFILE_ID
    destinations = {action.dest for action in parser._actions}
    assert "quality" not in destinations
    assert "reference_levels" not in destinations
    assert "sampling_profile" not in destinations


def test_d5_execution_module_has_no_display_publication_or_system_imports() -> None:
    source = (
        REPOSITORY
        / "src/fspm_optics/application/surface_flux_recalibration.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
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
        "surface_flux_display",
        "application.publication",
        "application.proposed",
        "application.systems",
        "resources.calibration",
        "viewer",
    )
    assert not any(token in module for token in forbidden for module in imported)
