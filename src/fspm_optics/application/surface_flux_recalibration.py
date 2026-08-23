"""Versioned Phase 27G-D5-A1 optimized surface-flux calibration sweep.

This module deliberately wraps the established D1 Stage A/Stage B executor
without changing its historical configuration, schemas, reports, or CLI.  The
D5 path fixes the optimized receiver identity, two requested reference levels,
and four independently executed Radiance quality families.  It produces only
authenticated execution evidence; coefficient derivation and display use are
out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import tempfile
from typing import Callable, Mapping, Protocol, Sequence

from fspm_optics.application.surface_flux_calibration import (
    CalibrationCriteria,
    HistogramSpecification,
    SurfaceFluxCalibrationError,
    _Job,
    _clean_interrupted_level_stages,
    _discover_repository_root,
    _execute_level,
    _hash_json,
    _inventory,
    _pretty_json,
    _read_json_object,
    _remove_stage,
    _repository_revision,
    _sha256_file,
    _utc_now,
    _validate_output_location,
    _validated_timestamp,
    calibration_scene_identity,
    canonical_neutral_source_definition,
    render_neutral_source,
)
from fspm_optics.optics.rex_material_plan import (
    RexRadianceTransMaterialPlan,
    build_rex_radiance_trans_material_plan,
)
from fspm_optics.plants.multi_scene import (
    JuvenileScientificScene,
    build_juvenile_natural_fit_scene,
)
from fspm_optics.plants.natural_fit import plan_natural_fit_layout_from_feet
from fspm_optics.plants.rex_juvenile import (
    REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID,
)
from fspm_optics.radiance.commands import CommandSpec, LOCAL_DEFAULT_NTHREADS
from fspm_optics.radiance.options import RADIANCE_QUALITY_NAMES, radiance_options
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.radiance.versioning import (
    RadianceInstallation,
    discover_radiance_installation,
)
from fspm_optics.transport.basis.atomic import atomic_write_text


D5_EXPERIMENT_ID = "phase27g-d5-a1-optimized-surface-flux-sweep-v3"
D5_IDENTITY_VERSION = 3
D5_SAMPLING_PROFILE_ID = (
    "rex_juvenile_surface_sampling_equal_area_cell_aligned_4x4_v1"
)
D5_TOPOLOGY_SHA256 = (
    "d59c27614c9b4fde3d60f8ebf090b56bbc6692d77baeed67753e8081ff70138f"
)
D5_RECEIVERS_SHA256 = (
    "e2f6606ba78642ffe6f9de0601d87c8f4e4197b66cb3ef9d284f8324e1ee305f"
)
D5_QUALITY_ORDER = ("standard", "quality", "rigorous")
D5_REFERENCE_LEVELS_UMOL_M2_S = (250.0, 500.0)
D5_BAND_ORDER = ("blue", "green", "orange", "red")
D5_DEFERRED_DISPLAY_QUALITY_MAPPING = {"direct": "standard"}
D5_PHOTON_FRACTION_PER_BAND = 0.25
D5_JOB_COUNT = 6
D5_STAGE_B_ARTIFACT_COUNT = 24
D5_PATCHES_PER_PLANT = 192
D5_RECEIVERS_PER_PLANT = 384
D5_EXPECTED_PLANT_COUNT = 64
D5_EXPECTED_RECEIVER_COUNT_PER_BAND = 24_576
D5_EXPECTED_RECEIVER_BYTES_PER_BAND = 196_608
D5_PLAN_SCHEMA_ID = "fspm-optics.surface-flux-recalibration-plan"
D5_PLAN_SCHEMA_VERSION = 1
D5_CONFIGURATION_SCHEMA_ID = (
    "fspm-optics.surface-flux-recalibration-configuration"
)
D5_CONFIGURATION_SCHEMA_VERSION = 1
D5_JOB_RESULT_SCHEMA_ID = "fspm-optics.surface-flux-recalibration-job"
D5_JOB_RESULT_SCHEMA_VERSION = 1
D5_JOB_COMPLETION_SCHEMA_ID = (
    "fspm-optics.surface-flux-recalibration-job-completion"
)
D5_JOB_COMPLETION_SCHEMA_VERSION = 1
D5_COMPLETION_SCHEMA_ID = "fspm-optics.surface-flux-recalibration-completion"
D5_COMPLETION_SCHEMA_VERSION = 1

D5_DEFAULT_OUTPUT_DIRECTORY = Path(".surface-flux-recalibration-d5")
D5_CONFIGURATION_NAME = "recalibration-configuration.v1.json"
D5_JOB_RESULT_NAME = "job-result.v1.json"
D5_JOB_COMPLETION_NAME = "job-completion.v1.json"
D5_COMPLETION_NAME = "surface-flux-recalibration-completion.v1.json"
D5_JOBS_DIRECTORY = "jobs"

EventSink = Callable[[str, Mapping[str, object] | None], None]


class CommandRunner(Protocol):
    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult: ...


class SurfaceFluxRecalibrationError(SurfaceFluxCalibrationError):
    """The D5-A1 plan, execution, or authentication contract failed."""


@dataclass(frozen=True, slots=True)
class SurfaceFluxRecalibrationConfig:
    """Fixed scientific configuration plus non-scientific execution controls."""

    output_directory: Path = D5_DEFAULT_OUTPUT_DIRECTORY
    threads: int = LOCAL_DEFAULT_NTHREADS
    oconv_command: str | Path = "oconv"
    rtrace_command: str | Path = "rtrace"
    resume: bool = False
    room_length_ft: float = field(default=10.0, init=False)
    room_width_ft: float = field(default=10.0, init=False)
    reference_plane_z_m: float = field(default=0.005, init=False)
    reference_grid_x: int = field(default=21, init=False)
    reference_grid_y: int = field(default=21, init=False)
    reference_inset_m: float = field(default=0.005, init=False)
    histogram: HistogramSpecification = field(
        default_factory=HistogramSpecification,
        init=False,
    )
    criteria: CalibrationCriteria = field(
        default_factory=CalibrationCriteria,
        init=False,
    )

    def __post_init__(self) -> None:
        output = Path(self.output_directory).expanduser()
        output = (Path.cwd() / output).resolve() if not output.is_absolute() else output.resolve()
        object.__setattr__(self, "output_directory", output)
        if (
            isinstance(self.threads, bool)
            or not isinstance(self.threads, int)
            or self.threads <= 0
        ):
            raise ValueError("threads must be a positive integer.")
        for name in ("oconv_command", "rtrace_command"):
            if not str(getattr(self, name)):
                raise ValueError(f"{name} must be non-empty.")
        if not isinstance(self.resume, bool):
            raise ValueError("resume must be boolean.")

    @property
    def sampling_profile_id(self) -> str:
        return D5_SAMPLING_PROFILE_ID

    @property
    def quality_families(self) -> tuple[str, ...]:
        return D5_QUALITY_ORDER

    @property
    def reference_levels_umol_m2_s(self) -> tuple[float, ...]:
        return D5_REFERENCE_LEVELS_UMOL_M2_S

    def scientific_payload(self) -> dict[str, object]:
        return {
            "experiment_id": D5_EXPERIMENT_ID,
            "d5_identity_version": D5_IDENTITY_VERSION,
            "sampling_profile_id": D5_SAMPLING_PROFILE_ID,
            "topology_sha256": D5_TOPOLOGY_SHA256,
            "receivers_sha256": D5_RECEIVERS_SHA256,
            "room": {
                "length_ft": self.room_length_ft,
                "width_ft": self.room_width_ft,
                "nominal_height_ft": 10.0,
            },
            "reference_plane": {
                "z_m": self.reference_plane_z_m,
                "grid": [self.reference_grid_x, self.reference_grid_y],
                "inset_m": self.reference_inset_m,
                "layout": "centered",
            },
            "quality_order": list(D5_QUALITY_ORDER),
            "reference_levels_umol_m2_s": list(
                D5_REFERENCE_LEVELS_UMOL_M2_S
            ),
            "band_order": list(D5_BAND_ORDER),
            "photon_fractions": {
                band: D5_PHOTON_FRACTION_PER_BAND for band in D5_BAND_ORDER
            },
            "neutral_source": {
                "source_model_id": canonical_neutral_source_definition()[
                    "source_model_id"
                ],
                "source_definition_sha256": canonical_neutral_source_definition()[
                    "source_definition_sha256"
                ],
                "radiance_emitter_material_type": "glow",
                "glow_maximum_radius": 0.0,
                "executed_families_use_ambient_evaluation": True,
            },
            "far_red_executed": False,
            "calibration_quality_proxy_used": False,
            "quality_fallback_allowed": False,
            "calibrated_quality_families": list(D5_QUALITY_ORDER),
            "deferred_runtime_display_quality_mapping": dict(
                D5_DEFERRED_DISPLAY_QUALITY_MAPPING
            ),
            "direct_transport_execution_in_calibration": False,
            "evaluated_system_inputs_used": False,
            "threads": self.threads,
            "radiance_commands": {
                "oconv": str(self.oconv_command),
                "rtrace": str(self.rtrace_command),
            },
            "histogram": self.histogram.to_dict(),
            "criteria": self.criteria.to_dict(),
        }

    def cli_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {
            "output_directory": str(self.output_directory),
            "resume_requested": self.resume,
        }


@dataclass(frozen=True, slots=True)
class SurfaceFluxRecalibrationPublication:
    completion_path: Path
    completion: Mapping[str, object]


def recalibration_jobs() -> tuple[_Job, ...]:
    """Return the immutable quality-major, level-minor D5 job order."""

    jobs = tuple(
        _Job(
            job_id=f"{quality}-{_level_token(level)}",
            kind="primary",
            requested_level=level,
            quality=quality,
        )
        for quality in D5_QUALITY_ORDER
        for level in D5_REFERENCE_LEVELS_UMOL_M2_S
    )
    _validate_job_matrix(jobs)
    return jobs


def build_surface_flux_recalibration_plan(
    config: SurfaceFluxRecalibrationConfig,
) -> dict[str, object]:
    """Build the deterministic D5 plan without discovering or running Radiance."""

    _require_config(config)
    scene, material_plan = _build_scientific_inputs(config)
    jobs = recalibration_jobs()
    plan: dict[str, object] = {
        "schema_id": D5_PLAN_SCHEMA_ID,
        "schema_version": D5_PLAN_SCHEMA_VERSION,
        "experiment_id": D5_EXPERIMENT_ID,
        "d5_identity_version": D5_IDENTITY_VERSION,
        "mode": "plan_only",
        "radiance_discovery_performed": False,
        "radiance_execution_performed": False,
        "scientific_configuration": config.scientific_payload(),
        "scene_identity": _d5_scene_identity(config, scene, material_plan),
        "calibrated_quality_families": list(D5_QUALITY_ORDER),
        "deferred_runtime_display_quality_mapping": {
            "mapping": dict(D5_DEFERRED_DISPLAY_QUALITY_MAPPING),
            "scope": "display normalization only",
            "raw_transport_uses_requested_quality": True,
            "implemented_in_d5_a1": False,
        },
        "quality_proxy_policy": {
            "proxy_among_executed_calibration_families": False,
            "direct_calibration_executed": False,
        },
        "quality_option_identities": _quality_option_identities(),
        "job_order": [
            {
                "order_index": index,
                **job.to_dict(),
                "stage_a_reference_trace_count": 2,
                "stage_b_band_order": list(D5_BAND_ORDER),
                "stage_b_receiver_artifact_count": len(D5_BAND_ORDER),
            }
            for index, job in enumerate(jobs)
        ],
        "job_count": len(jobs),
        "stage_a_reference_trace_count": 2 * len(jobs),
        "stage_b_receiver_artifact_count": (
            len(jobs) * len(D5_BAND_ORDER)
        ),
        "expected_receiver_artifact": {
            "component_type": "float64",
            "byte_order": "little-endian",
            "stride_bytes": 8,
            "row_count": scene.counts.receiver_count,
            "byte_length": D5_EXPECTED_RECEIVER_BYTES_PER_BAND,
            "canonical_order": (
                "plant-major; local_patch_index 0..191; front then back"
            ),
        },
        "deferred": [
            "coefficient calculation",
            "palette derivation",
            "near-zero policy",
            "calibration promotion or resource generation",
            "surface-flux display metadata changes",
            "backend or viewer coloring changes",
        ],
    }
    plan["plan_sha256"] = _hash_json(plan)
    return plan


def run_surface_flux_recalibration(
    config: SurfaceFluxRecalibrationConfig,
    *,
    runner: CommandRunner | None = None,
    radiance_installation: RadianceInstallation | None = None,
    event_sink: EventSink | None = None,
    created_at_utc: str | None = None,
    repository_root: Path | None = None,
) -> SurfaceFluxRecalibrationPublication:
    """Execute or authenticate-resume the complete fixed D5-A1 sweep."""

    _require_config(config)
    sink = event_sink or (lambda _message, _data=None: None)
    repository = (
        _discover_repository_root()
        if repository_root is None
        else Path(repository_root).resolve()
    )
    _validate_output_location(config.output_directory, repository)
    try:
        installation = radiance_installation or discover_radiance_installation(
            oconv_command=config.oconv_command,
            rtrace_command=config.rtrace_command,
            cwd=repository,
        )
        scene, material_plan = _build_scientific_inputs(config)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SurfaceFluxRecalibrationError(
            f"D5 scientific input or Radiance discovery failed: {exc}"
        ) from exc

    plan = build_surface_flux_recalibration_plan(config)
    identity = _configuration_identity(
        config,
        plan=plan,
        scene=scene,
        material_plan=material_plan,
        installation=installation,
        repository_revision=_repository_revision(repository),
    )
    configuration_sha256 = str(identity["configuration_sha256"])
    state = _prepare_output_root(
        config,
        identity=identity,
        created_at_utc=created_at_utc or _utc_now(),
    )
    jobs_root = config.output_directory / D5_JOBS_DIRECTORY
    jobs_root.mkdir(exist_ok=True)
    existing_completion = config.output_directory / D5_COMPLETION_NAME
    if existing_completion.exists():
        if not config.resume:
            raise SurfaceFluxRecalibrationError(
                "D5 completion manifest already exists without --resume."
            )
        _validate_completion_manifest(
            existing_completion,
            output=config.output_directory,
            configuration_sha256=configuration_sha256,
        )
    if config.resume:
        _clean_interrupted_level_stages(jobs_root)
    jobs = recalibration_jobs()
    _validate_jobs_directory(jobs_root, jobs)

    native_runner = runner or LocalRunner()
    results: list[dict[str, object]] = []
    for order_index, job in enumerate(jobs):
        final_root = jobs_root / job.job_id
        if final_root.exists():
            if not config.resume:
                raise SurfaceFluxRecalibrationError(
                    f"D5 job already exists without --resume: {job.job_id}"
                )
            sink(
                f"Authenticating completed D5 job {job.job_id}.",
                {"job": job.to_dict(), "order_index": order_index},
            )
            results.append(
                _validate_completed_job(
                    final_root,
                    job=job,
                    order_index=order_index,
                    configuration_sha256=configuration_sha256,
                    scene=scene,
                )
            )
            continue

        sink(
            f"Executing D5 job {job.job_id}.",
            {"job": job.to_dict(), "order_index": order_index},
        )
        stage = Path(
            tempfile.mkdtemp(
                prefix=f".{job.job_id}.", suffix=".tmp", dir=jobs_root
            )
        )
        try:
            try:
                result = _execute_level(
                    stage,
                    config=config,
                    job=job,
                    scene=scene,
                    material_plan=material_plan,
                    runner=native_runner,
                    installation=installation,
                    configuration_sha256=configuration_sha256,
                    event_sink=sink,
                )
            except SurfaceFluxCalibrationError as exc:
                raise SurfaceFluxRecalibrationError(
                    f"D5 job {job.job_id} failed: {exc}"
                ) from exc
            _attach_d5_stage_a_command_authorities(result, job=job)
            result.update(
                {
                    "schema_id": D5_JOB_RESULT_SCHEMA_ID,
                    "schema_version": D5_JOB_RESULT_SCHEMA_VERSION,
                    "experiment_id": D5_EXPERIMENT_ID,
                    "d5_identity_version": D5_IDENTITY_VERSION,
                    "job_order_index": order_index,
                    "sampling_profile_id": D5_SAMPLING_PROFILE_ID,
                    "expected_topology_sha256": D5_TOPOLOGY_SHA256,
                    "expected_receivers_sha256": D5_RECEIVERS_SHA256,
                    "neutral_source_model_id": (
                        canonical_neutral_source_definition()["source_model_id"]
                    ),
                    "neutral_source_definition_sha256": (
                        canonical_neutral_source_definition()[
                            "source_definition_sha256"
                        ]
                    ),
                    "radiance_emitter_material_type": "glow",
                    "artifact_root_from_report": (
                        f"{D5_JOBS_DIRECTORY}/{job.job_id}"
                    ),
                    "quality_option_identity": _quality_option_identity(
                        job.quality
                    ),
                }
            )
            _validate_job_result_contract(
                result,
                root=stage,
                job=job,
                order_index=order_index,
                configuration_sha256=configuration_sha256,
                scene=scene,
            )
            result_path = stage / D5_JOB_RESULT_NAME
            atomic_write_text(result_path, _pretty_json(result))
            artifact_inventory = _inventory(
                stage, excluded={D5_JOB_COMPLETION_NAME}
            )
            completion = {
                "schema_id": D5_JOB_COMPLETION_SCHEMA_ID,
                "schema_version": D5_JOB_COMPLETION_SCHEMA_VERSION,
                "experiment_id": D5_EXPERIMENT_ID,
                "configuration_sha256": configuration_sha256,
                "job_order_index": order_index,
                "job": job.to_dict(),
                "job_result": {
                    "path": D5_JOB_RESULT_NAME,
                    "byte_length": result_path.stat().st_size,
                    "sha256": _sha256_file(result_path),
                },
                "ordered_artifact_inventory": artifact_inventory,
                "completion_policy": (
                    "all retained artifacts hashed before same-filesystem "
                    "directory commit"
                ),
            }
            atomic_write_text(
                stage / D5_JOB_COMPLETION_NAME, _pretty_json(completion)
            )
            if final_root.exists():
                raise SurfaceFluxRecalibrationError(
                    f"D5 job destination appeared during execution: {job.job_id}"
                )
            os.replace(stage, final_root)
            results.append(
                _validate_completed_job(
                    final_root,
                    job=job,
                    order_index=order_index,
                    configuration_sha256=configuration_sha256,
                    scene=scene,
                )
            )
        except BaseException:
            _remove_stage(stage, jobs_root)
            raise
        sink(
            f"Completed D5 job {job.job_id}.",
            {"job": job.to_dict(), "order_index": order_index},
        )

    _validate_complete_result_matrix(results, jobs)
    completion = _build_completion_manifest(
        config=config,
        identity=identity,
        plan=plan,
        created_at_utc=str(state["created_at_utc"]),
        results=results,
    )
    completion_path = config.output_directory / D5_COMPLETION_NAME
    if completion_path.exists():
        if not config.resume:
            raise SurfaceFluxRecalibrationError(
                "D5 completion manifest already exists without --resume."
            )
        validated = _validate_completion_manifest(
            completion_path,
            output=config.output_directory,
            configuration_sha256=configuration_sha256,
        )
        if validated != completion:
            raise SurfaceFluxRecalibrationError(
                "Authenticated D5 completion manifest disagrees with the "
                "completed job matrix; refusing to overwrite it."
            )
    else:
        atomic_write_text(completion_path, _pretty_json(completion))
        validated = _validate_completion_manifest(
            completion_path,
            output=config.output_directory,
            configuration_sha256=configuration_sha256,
        )
    sink(
        "Published the authenticated D5-A1 completion manifest.",
        {
            "completion": str(completion_path),
            "stage_b_receiver_artifact_count": D5_STAGE_B_ARTIFACT_COUNT,
        },
    )
    return SurfaceFluxRecalibrationPublication(
        completion_path=completion_path,
        completion=validated,
    )


def _build_scientific_inputs(
    config: SurfaceFluxRecalibrationConfig,
) -> tuple[JuvenileScientificScene, RexRadianceTransMaterialPlan]:
    layout = plan_natural_fit_layout_from_feet(
        config.room_length_ft,
        config.room_width_ft,
    )
    scene = build_juvenile_natural_fit_scene(
        layout,
        sampling_profile_id=REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID,
    )
    material_plan = build_rex_radiance_trans_material_plan()
    _validate_scene(scene)
    return scene, material_plan


def _validate_scene(scene: JuvenileScientificScene) -> None:
    if (
        D5_SAMPLING_PROFILE_ID
        != REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
        or scene.sampling_profile_id != D5_SAMPLING_PROFILE_ID
        or scene.topology.sampling_profile_id != D5_SAMPLING_PROFILE_ID
    ):
        raise SurfaceFluxRecalibrationError(
            "D5 sampling profile identity is incompatible."
        )
    if scene.topology.topology_sha256 != D5_TOPOLOGY_SHA256:
        raise SurfaceFluxRecalibrationError("D5 topology SHA-256 changed.")
    if scene.topology.receivers_sha256 != D5_RECEIVERS_SHA256:
        raise SurfaceFluxRecalibrationError("D5 receiver SHA-256 changed.")
    if (
        scene.topology.patch_count != D5_PATCHES_PER_PLANT
        or scene.topology.receiver_count != D5_RECEIVERS_PER_PLANT
        or scene.counts.plant_count != D5_EXPECTED_PLANT_COUNT
        or scene.counts.receiver_count != D5_EXPECTED_RECEIVER_COUNT_PER_BAND
    ):
        raise SurfaceFluxRecalibrationError(
            "D5 canonical plant, patch, or receiver counts changed."
        )


def _d5_scene_identity(
    config: SurfaceFluxRecalibrationConfig,
    scene: JuvenileScientificScene,
    material_plan: RexRadianceTransMaterialPlan,
) -> dict[str, object]:
    identity = calibration_scene_identity(config, scene, material_plan)
    source_definition = canonical_neutral_source_definition()
    identity["source_model_id"] = source_definition["source_model_id"]
    identity["radiance_emitter_material_type"] = "glow"
    identity["glow_maximum_radius"] = 0.0
    identity["sampling_profile_id"] = D5_SAMPLING_PROFILE_ID
    identity["fixed_topology_sha256"] = D5_TOPOLOGY_SHA256
    identity["fixed_receivers_sha256"] = D5_RECEIVERS_SHA256
    identity["calibrated_quality_families"] = list(D5_QUALITY_ORDER)
    identity["deferred_runtime_display_quality_mapping"] = dict(
        D5_DEFERRED_DISPLAY_QUALITY_MAPPING
    )
    identity["d5_scene_identity_sha256"] = _hash_json(identity)
    return identity


def _configuration_identity(
    config: SurfaceFluxRecalibrationConfig,
    *,
    plan: Mapping[str, object],
    scene: JuvenileScientificScene,
    material_plan: RexRadianceTransMaterialPlan,
    installation: RadianceInstallation,
    repository_revision: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_id": D5_CONFIGURATION_SCHEMA_ID,
        "schema_version": D5_CONFIGURATION_SCHEMA_VERSION,
        "experiment_id": D5_EXPERIMENT_ID,
        "d5_identity_version": D5_IDENTITY_VERSION,
        "scientific_configuration": config.scientific_payload(),
        "plan_sha256": plan["plan_sha256"],
        "neutral_source": canonical_neutral_source_definition(),
        "scene_identity": _d5_scene_identity(config, scene, material_plan),
        "material_plan_sha256": _hash_json(material_plan.to_payload()),
        "quality_option_identities": _quality_option_identities(),
        "radiance_installation": installation.to_dict(),
        "repository_revision": repository_revision,
    }
    payload["configuration_sha256"] = _hash_json(payload)
    return payload


def _prepare_output_root(
    config: SurfaceFluxRecalibrationConfig,
    *,
    identity: Mapping[str, object],
    created_at_utc: str,
) -> dict[str, object]:
    output = config.output_directory
    if output.exists() and (not output.is_dir() or output.is_symlink()):
        raise SurfaceFluxRecalibrationError(
            "D5 output must be a real directory or an absent path."
        )
    if not output.exists():
        output.mkdir(parents=True)
    entries = tuple(output.iterdir())
    state_path = output / D5_CONFIGURATION_NAME
    if not entries:
        state = {
            "schema_id": D5_CONFIGURATION_SCHEMA_ID,
            "schema_version": D5_CONFIGURATION_SCHEMA_VERSION,
            "experiment_id": D5_EXPERIMENT_ID,
            "created_at_utc": _validated_timestamp(created_at_utc),
            "initial_cli_configuration": config.cli_payload(),
            "identity": dict(identity),
        }
        atomic_write_text(state_path, _pretty_json(state))
        return state
    if not config.resume:
        raise SurfaceFluxRecalibrationError(
            "D5 output is non-empty; use --resume only for an exact configuration."
        )
    allowed = {D5_CONFIGURATION_NAME, D5_JOBS_DIRECTORY, D5_COMPLETION_NAME}
    unexpected = sorted(path.name for path in entries if path.name not in allowed)
    if unexpected:
        raise SurfaceFluxRecalibrationError(
            f"D5 output contains unexpected entries: {unexpected!r}."
        )
    state = _read_json_object(state_path)
    if (
        state.get("schema_id") != D5_CONFIGURATION_SCHEMA_ID
        or state.get("schema_version") != D5_CONFIGURATION_SCHEMA_VERSION
        or state.get("experiment_id") != D5_EXPERIMENT_ID
        or state.get("identity") != dict(identity)
    ):
        raise SurfaceFluxRecalibrationError(
            "Stored D5 configuration does not match exactly; refusing to mix results."
        )
    _validated_timestamp(state.get("created_at_utc"))
    return state


def _validate_jobs_directory(root: Path, jobs: Sequence[_Job]) -> None:
    expected = {job.job_id for job in jobs}
    unexpected = sorted(
        path.name
        for path in root.iterdir()
        if path.name not in expected
        and not (path.name.startswith(".") and path.name.endswith(".tmp"))
    )
    if unexpected:
        raise SurfaceFluxRecalibrationError(
            f"D5 jobs directory contains unknown or substituted jobs: {unexpected!r}."
        )


def _validate_completed_job(
    root: Path,
    *,
    job: _Job,
    order_index: int,
    configuration_sha256: str,
    scene: JuvenileScientificScene,
) -> dict[str, object]:
    if not root.is_dir() or root.is_symlink():
        raise SurfaceFluxRecalibrationError(
            f"Completed D5 job path is missing or unsafe: {root}"
        )
    completion = _read_json_object(root / D5_JOB_COMPLETION_NAME)
    if (
        completion.get("schema_id") != D5_JOB_COMPLETION_SCHEMA_ID
        or completion.get("schema_version") != D5_JOB_COMPLETION_SCHEMA_VERSION
        or completion.get("experiment_id") != D5_EXPERIMENT_ID
        or completion.get("configuration_sha256") != configuration_sha256
        or completion.get("job_order_index") != order_index
        or completion.get("job") != job.to_dict()
    ):
        raise SurfaceFluxRecalibrationError(
            f"Completed D5 job identity is invalid: {job.job_id}"
        )
    inventory = completion.get("ordered_artifact_inventory")
    if not isinstance(inventory, list):
        raise SurfaceFluxRecalibrationError(
            f"Completed D5 job inventory is missing: {job.job_id}"
        )
    paths = [
        record.get("path") if isinstance(record, Mapping) else None
        for record in inventory
    ]
    if (
        any(not isinstance(path, str) for path in paths)
        or paths != sorted(paths)
        or len(paths) != len(set(paths))
    ):
        raise SurfaceFluxRecalibrationError(
            f"Completed D5 job inventory is duplicated or unordered: {job.job_id}"
        )
    resolved_root = root.resolve(strict=True)
    for record in inventory:
        if not isinstance(record, Mapping):
            raise SurfaceFluxRecalibrationError("D5 artifact record is invalid.")
        relative = record.get("path")
        path = root / str(relative)
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(resolved_root)
            or path.stat().st_size != record.get("byte_length")
            or _sha256_file(path) != record.get("sha256")
        ):
            raise SurfaceFluxRecalibrationError(
                f"Completed D5 artifact failed size/hash validation: {relative}"
            )
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != D5_JOB_COMPLETION_NAME
    }
    if actual != set(paths):
        raise SurfaceFluxRecalibrationError(
            f"Completed D5 job has missing or extra artifacts: {job.job_id}"
        )
    result_authority = completion.get("job_result")
    result_path = root / D5_JOB_RESULT_NAME
    if (
        not isinstance(result_authority, Mapping)
        or result_authority.get("path") != D5_JOB_RESULT_NAME
        or result_authority.get("byte_length") != result_path.stat().st_size
        or result_authority.get("sha256") != _sha256_file(result_path)
    ):
        raise SurfaceFluxRecalibrationError(
            f"Completed D5 job-result authority is invalid: {job.job_id}"
        )
    result = _read_json_object(result_path)
    _validate_job_result_contract(
        result,
        root=root,
        job=job,
        order_index=order_index,
        configuration_sha256=configuration_sha256,
        scene=scene,
    )
    return result


def _validate_job_result_contract(
    result: Mapping[str, object],
    *,
    root: Path,
    job: _Job,
    order_index: int,
    configuration_sha256: str,
    scene: JuvenileScientificScene,
) -> None:
    if (
        result.get("schema_id") != D5_JOB_RESULT_SCHEMA_ID
        or result.get("schema_version") != D5_JOB_RESULT_SCHEMA_VERSION
        or result.get("experiment_id") != D5_EXPERIMENT_ID
        or result.get("d5_identity_version") != D5_IDENTITY_VERSION
        or result.get("configuration_sha256") != configuration_sha256
        or result.get("job_id") != job.job_id
        or result.get("job_order_index") != order_index
        or result.get("kind") != "primary"
        or result.get("quality") != job.quality
        or result.get("requested_reference_level_umol_m2_s")
        != job.requested_level
        or result.get("sampling_profile_id") != D5_SAMPLING_PROFILE_ID
        or result.get("expected_topology_sha256") != D5_TOPOLOGY_SHA256
        or result.get("expected_receivers_sha256") != D5_RECEIVERS_SHA256
        or result.get("neutral_source_model_id")
        != canonical_neutral_source_definition()["source_model_id"]
        or result.get("neutral_source_definition_sha256")
        != canonical_neutral_source_definition()["source_definition_sha256"]
        or result.get("radiance_emitter_material_type") != "glow"
        or result.get("quality_option_identity")
        != _quality_option_identity(job.quality)
    ):
        raise SurfaceFluxRecalibrationError(
            f"D5 job result identity is incompatible: {job.job_id}"
        )
    juvenile = result.get("juvenile_identity")
    scene_identity = result.get("scene_identity")
    if (
        not isinstance(juvenile, Mapping)
        or juvenile.get("topology_sha256") != D5_TOPOLOGY_SHA256
        or juvenile.get("receivers_sha256") != D5_RECEIVERS_SHA256
        or not isinstance(scene_identity, Mapping)
        or not isinstance(scene_identity.get("plant"), Mapping)
        or scene_identity["plant"].get("topology_sha256")
        != D5_TOPOLOGY_SHA256
        or scene_identity["plant"].get("receivers_sha256")
        != D5_RECEIVERS_SHA256
    ):
        raise SurfaceFluxRecalibrationError(
            f"D5 job scene identity is incompatible: {job.job_id}"
        )
    transport = result.get("plant_transport")
    if (
        not isinstance(transport, Mapping)
        or transport.get("independent_execution") is not True
        or transport.get("band_order") != list(D5_BAND_ORDER)
        or transport.get("band_count") != len(D5_BAND_ORDER)
        or transport.get("far_red_executed") is not False
        or transport.get("equal_photon_fraction_per_band")
        != D5_PHOTON_FRACTION_PER_BAND
        or transport.get("receiver_count_per_band")
        != scene.counts.receiver_count
    ):
        raise SurfaceFluxRecalibrationError(
            f"D5 Stage B contract is incompatible: {job.job_id}"
        )
    bands = transport.get("bands")
    if not isinstance(bands, list) or len(bands) != len(D5_BAND_ORDER):
        raise SurfaceFluxRecalibrationError(
            f"D5 Stage B band inventory is incomplete: {job.job_id}"
        )
    observed_ids: list[object] = []
    for band_index, (band, expected_band) in enumerate(
        zip(bands, D5_BAND_ORDER, strict=True)
    ):
        if not isinstance(band, Mapping):
            raise SurfaceFluxRecalibrationError("D5 band record is invalid.")
        observed_ids.append(band.get("band_id"))
        source = band.get("source")
        receiver = band.get("receiver_values")
        commands = band.get("commands")
        if (
            band.get("order_index") != band_index
            or band.get("band_id") != expected_band
            or not isinstance(source, Mapping)
            or source.get("source_model_id")
            != canonical_neutral_source_definition()["source_model_id"]
            or source.get("source_definition_sha256")
            != canonical_neutral_source_definition()["source_definition_sha256"]
            or source.get("photon_fraction")
            != D5_PHOTON_FRACTION_PER_BAND
            or not isinstance(receiver, Mapping)
            or receiver.get("path")
            != (
                f"bands/{band_index:02d}-{expected_band}/"
                "receiver-values.v1.f64le.bin"
            )
            or receiver.get("stride_bytes") != 8
            or receiver.get("row_count") != scene.counts.receiver_count
            or receiver.get("byte_length") != scene.counts.receiver_count * 8
            or not _valid_sha256(receiver.get("sha256"))
            or not isinstance(commands, Mapping)
        ):
            raise SurfaceFluxRecalibrationError(
                f"D5 {expected_band} receiver contract is incompatible: {job.job_id}"
            )
        receiver_path = root / str(receiver["path"])
        if (
            not receiver_path.is_file()
            or receiver_path.is_symlink()
            or receiver_path.stat().st_size != receiver["byte_length"]
            or _sha256_file(receiver_path) != receiver["sha256"]
        ):
            raise SurfaceFluxRecalibrationError(
                f"D5 {expected_band} receiver artifact changed: {job.job_id}"
            )
        source_path = root / f"bands/{band_index:02d}-{expected_band}/source.rad"
        material_artifact = band.get("material_artifact")
        if (
            not source_path.is_file()
            or source_path.is_symlink()
            or _sha256_file(source_path) != band.get("source_sha256")
            or not isinstance(material_artifact, Mapping)
        ):
            raise SurfaceFluxRecalibrationError(
                f"D5 {expected_band} source/material authority changed: {job.job_id}"
            )
        _validate_glow_source_text(
            source_path,
            amplitude=source.get("rendered_amplitude"),
            label=f"D5 {expected_band} source",
        )
        _validate_declared_artifact(
            root,
            material_artifact,
            label=f"D5 {expected_band} material",
        )
        if material_artifact.get("sha256") != band.get("material_sha256"):
            raise SurfaceFluxRecalibrationError(
                f"D5 {expected_band} material hashes disagree: {job.job_id}"
            )
        _validate_rtrace_options(
            commands.get("rtrace"), quality=job.quality, job_id=job.job_id
        )
    if observed_ids != list(D5_BAND_ORDER) or len(set(observed_ids)) != 4:
        raise SurfaceFluxRecalibrationError(
            f"D5 band inventory is duplicated or substituted: {job.job_id}"
        )
    _validate_stage_a(result, root=root, job=job, scene=scene)
    validation = result.get("validation")
    if (
        not isinstance(validation, Mapping)
        or validation.get("all_receiver_values_finite_and_nonnegative") is not True
        or validation.get("receiver_identity_and_order_validated") is not True
        or validation.get("all_numerical_and_conservation_validations_pass")
        is not True
    ):
        raise SurfaceFluxRecalibrationError(
            f"D5 Stage C validation evidence is incomplete: {job.job_id}"
        )


def _validate_stage_a(
    result: Mapping[str, object],
    *,
    root: Path,
    job: _Job,
    scene: JuvenileScientificScene,
) -> None:
    del scene
    reference = result.get("reference")
    if not isinstance(reference, Mapping) or reference.get("plant_free") is not True:
        raise SurfaceFluxRecalibrationError(
            f"D5 Stage A plant-free reference is missing: {job.job_id}"
        )
    sensor = reference.get("horizontal_sensor_grid")
    if not isinstance(sensor, Mapping):
        raise SurfaceFluxRecalibrationError(
            f"D5 Stage A sensor-grid authority is missing: {job.job_id}"
        )
    _validate_stage_a_artifact(
        root,
        sensor,
        expected_path="reference/horizontal-reference-plane.pts",
        expected_role="horizontal_reference_plane_receivers",
        label="D5 Stage A sensor grid",
    )
    resolution = reference.get("amplitude_resolution")
    if (
        not isinstance(resolution, Mapping)
        or resolution.get("method")
        != "independent unit-amplitude pilot then independent final trace"
    ):
        raise SurfaceFluxRecalibrationError(
            f"D5 Stage A resolution contract changed: {job.job_id}"
        )
    for phase in ("pilot", "final"):
        trace = resolution.get(phase)
        if not isinstance(trace, Mapping) or trace.get("plants_present") is not False:
            raise SurfaceFluxRecalibrationError(
                f"D5 Stage A {phase} evidence is missing: {job.job_id}"
            )
        raw = trace.get("raw_reference_artifact")
        source = trace.get("source_artifact")
        commands = trace.get("commands")
        if (
            not isinstance(raw, Mapping)
            or not isinstance(source, Mapping)
            or not _valid_sha256(raw.get("sha256"))
            or not isinstance(raw.get("byte_length"), int)
            or raw.get("byte_length", 0) <= 0
            or not isinstance(commands, list)
            or len(commands) != 2
        ):
            raise SurfaceFluxRecalibrationError(
                f"D5 Stage A {phase} artifact authority is invalid: {job.job_id}"
            )
        _validate_stage_a_artifact(
            root,
            source,
            expected_path=f"reference/{phase}/source.rad",
            expected_role=f"reference_{phase}_source",
            label=f"D5 Stage A {phase} source",
        )
        _validate_glow_source_text(
            root / str(source["path"]),
            amplitude=trace.get("source_amplitude"),
            label=f"D5 Stage A {phase} source",
        )
        _validate_stage_a_artifact(
            root,
            raw,
            expected_path=f"reference/{phase}/reference.rgb",
            expected_role=f"reference_{phase}_raw_rgb",
            label=f"D5 Stage A {phase} raw reference",
        )
        rtrace = next(
            (
                command
                for command in commands
                if isinstance(command, Mapping)
                and str(command.get("label", "")).endswith("_rtrace")
            ),
            None,
        )
        _validate_rtrace_options(rtrace, quality=job.quality, job_id=job.job_id)
    final = resolution.get("final")
    final_metrics = final.get("metrics") if isinstance(final, Mapping) else None
    achieved = (
        final_metrics.get("mean_ppfd_umol_m2_s")
        if isinstance(final_metrics, Mapping)
        else None
    )
    if not isinstance(achieved, int | float) or achieved <= 0:
        raise SurfaceFluxRecalibrationError(
            f"D5 achieved Stage A reference is invalid: {job.job_id}"
        )


def _attach_d5_stage_a_command_authorities(
    result: dict[str, object],
    *,
    job: _Job,
) -> None:
    """Restore D5's nested Stage A command authority after D1 hoisting.

    The historical executor moves pilot/final commands into the level-wide
    command list.  D5 retains that historical list and additionally serializes
    the same immutable command payloads with their pilot/final artifacts.
    """

    reference = result.get("reference")
    resolution = (
        reference.get("amplitude_resolution")
        if isinstance(reference, Mapping)
        else None
    )
    hoisted = result.get("commands")
    if not isinstance(resolution, Mapping) or not isinstance(hoisted, list):
        raise SurfaceFluxRecalibrationError(
            f"D5 Stage A command authority is missing: {job.job_id}"
        )
    for phase in ("pilot", "final"):
        trace = resolution.get(phase)
        if not isinstance(trace, dict):
            raise SurfaceFluxRecalibrationError(
                f"D5 Stage A {phase} command authority is missing: {job.job_id}"
            )
        existing = trace.get("commands")
        if existing is not None:
            if not isinstance(existing, list) or len(existing) != 2:
                raise SurfaceFluxRecalibrationError(
                    f"D5 Stage A {phase} command authority is malformed: "
                    f"{job.job_id}"
                )
            continue
        selected: list[dict[str, object]] = []
        for command_kind in ("oconv", "rtrace"):
            expected_label = (
                f"surface_flux_calibration_{job.job_id}_reference_"
                f"{phase}_{command_kind}"
            )
            matches = [
                command
                for command in hoisted
                if isinstance(command, Mapping)
                and command.get("label") == expected_label
            ]
            if len(matches) != 1:
                raise SurfaceFluxRecalibrationError(
                    f"D5 Stage A {phase} {command_kind} command authority "
                    f"is missing, duplicated, or substituted: {job.job_id}"
                )
            selected.append(dict(matches[0]))
        trace["commands"] = selected


def _validate_stage_a_artifact(
    root: Path,
    artifact: Mapping[str, object],
    *,
    expected_path: str,
    expected_role: str,
    label: str,
) -> None:
    if (
        set(artifact)
        != {"role", "path", "media_type", "byte_length", "sha256"}
        or artifact.get("role") != expected_role
        or artifact.get("path") != expected_path
        or artifact.get("media_type") != "text/plain"
        or isinstance(artifact.get("byte_length"), bool)
        or not isinstance(artifact.get("byte_length"), int)
        or artifact.get("byte_length", 0) <= 0
        or not _valid_sha256(artifact.get("sha256"))
    ):
        raise SurfaceFluxRecalibrationError(
            f"{label} canonical artifact authority is invalid."
        )
    _validate_declared_artifact(root, artifact, label=label)


def _validate_rtrace_options(
    command: object,
    *,
    quality: str,
    job_id: str,
) -> None:
    if quality not in D5_QUALITY_ORDER or quality not in RADIANCE_QUALITY_NAMES:
        raise SurfaceFluxRecalibrationError(
            f"D5 quality is not an executed calibrated family: {quality!r}"
        )
    if not isinstance(command, Mapping) or not isinstance(command.get("argv"), list):
        raise SurfaceFluxRecalibrationError(
            f"D5 rtrace command evidence is missing: {job_id}"
        )
    argv = [str(value) for value in command["argv"]]
    expected = radiance_options(quality)
    if (
        len(argv) < 7
        or argv[1:4] != ["-h", "-I+", "-n"]
        or not argv[4].isdigit()
        or int(argv[4]) <= 0
    ):
        raise SurfaceFluxRecalibrationError(
            f"D5 rtrace scalar-irradiance prefix changed: {job_id}"
        )
    option_tokens = argv[5:-1]
    expected_tokens = list(expected)
    if (
        len(option_tokens) != len(expected_tokens) + 2
        or option_tokens[: len(expected_tokens)] != expected_tokens
        or option_tokens[-2] != "-af"
        or not option_tokens[-1].endswith("scene.amb")
    ):
        raise SurfaceFluxRecalibrationError(
            f"D5 {quality} Radiance option identity changed: {job_id}"
        )


def _build_completion_manifest(
    *,
    config: SurfaceFluxRecalibrationConfig,
    identity: Mapping[str, object],
    plan: Mapping[str, object],
    created_at_utc: str,
    results: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    stage_a: list[dict[str, object]] = []
    stage_b: list[dict[str, object]] = []
    jobs: list[dict[str, object]] = []
    for result in results:
        job_id = str(result["job_id"])
        reference = result["reference"]
        resolution = reference["amplitude_resolution"]
        pilot = resolution["pilot"]
        final = resolution["final"]
        stage_a.append(
            {
                "job_id": job_id,
                "job_order_index": result["job_order_index"],
                "quality": result["quality"],
                "requested_reference_level_umol_m2_s": result[
                    "requested_reference_level_umol_m2_s"
                ],
                "method": resolution["method"],
                "horizontal_sensor_grid": _rooted_artifact(
                    job_id, reference["horizontal_sensor_grid"]
                ),
                "pilot_source_artifact": _rooted_artifact(
                    job_id, pilot["source_artifact"]
                ),
                "pilot_raw_reference_artifact": _rooted_artifact(
                    job_id, pilot["raw_reference_artifact"]
                ),
                "pilot_commands": pilot["commands"],
                "final_source_artifact": _rooted_artifact(
                    job_id, final["source_artifact"]
                ),
                "final_raw_reference_artifact": _rooted_artifact(
                    job_id, final["raw_reference_artifact"]
                ),
                "final_commands": final["commands"],
                "achieved_reference_mean_umol_m2_s": final["metrics"][
                    "mean_ppfd_umol_m2_s"
                ],
                "resolved_total_source_radiance_amplitude": resolution[
                    "resolved_total_source_radiance_amplitude"
                ],
            }
        )
        bands = result["plant_transport"]["bands"]
        for band in bands:
            stage_b.append(
                {
                    "job_id": job_id,
                    "job_order_index": result["job_order_index"],
                    "quality": result["quality"],
                    "requested_reference_level_umol_m2_s": result[
                        "requested_reference_level_umol_m2_s"
                    ],
                    "band_order_index": band["order_index"],
                    "band_id": band["band_id"],
                    "photon_fraction": band["source"]["photon_fraction"],
                    "receiver_values": _rooted_artifact(
                        job_id, band["receiver_values"]
                    ),
                    "source_sha256": band["source_sha256"],
                    "material_sha256": band["material_sha256"],
                    "rtrace_command": band["commands"]["rtrace"],
                }
            )
        jobs.append(
            {
                "job_id": job_id,
                "job_order_index": result["job_order_index"],
                "quality": result["quality"],
                "requested_reference_level_umol_m2_s": result[
                    "requested_reference_level_umol_m2_s"
                ],
                "job_result": {
                    "path": f"{D5_JOBS_DIRECTORY}/{job_id}/{D5_JOB_RESULT_NAME}",
                    "sha256": _sha256_file(
                        config.output_directory
                        / D5_JOBS_DIRECTORY
                        / job_id
                        / D5_JOB_RESULT_NAME
                    ),
                },
                "job_completion": {
                    "path": (
                        f"{D5_JOBS_DIRECTORY}/{job_id}/{D5_JOB_COMPLETION_NAME}"
                    ),
                    "sha256": _sha256_file(
                        config.output_directory
                        / D5_JOBS_DIRECTORY
                        / job_id
                        / D5_JOB_COMPLETION_NAME
                    ),
                },
            }
        )
    if len(stage_a) != D5_JOB_COUNT or len(stage_b) != D5_STAGE_B_ARTIFACT_COUNT:
        raise SurfaceFluxRecalibrationError(
            "D5 completion evidence count is incomplete."
        )
    inventory = _inventory(config.output_directory, excluded={D5_COMPLETION_NAME})
    return {
        "schema_id": D5_COMPLETION_SCHEMA_ID,
        "schema_version": D5_COMPLETION_SCHEMA_VERSION,
        "experiment_id": D5_EXPERIMENT_ID,
        "d5_identity_version": D5_IDENTITY_VERSION,
        "created_at_utc": created_at_utc,
        "status": "complete",
        "configuration_sha256": identity["configuration_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "sampling_profile_id": D5_SAMPLING_PROFILE_ID,
        "topology_sha256": D5_TOPOLOGY_SHA256,
        "receivers_sha256": D5_RECEIVERS_SHA256,
        "neutral_source": canonical_neutral_source_definition(),
        "calibrated_quality_families": list(D5_QUALITY_ORDER),
        "deferred_runtime_display_quality_mapping": dict(
            D5_DEFERRED_DISPLAY_QUALITY_MAPPING
        ),
        "quality_order": list(D5_QUALITY_ORDER),
        "reference_level_order_umol_m2_s": list(
            D5_REFERENCE_LEVELS_UMOL_M2_S
        ),
        "band_order": list(D5_BAND_ORDER),
        "job_count": D5_JOB_COUNT,
        "stage_a_evidence_count": D5_JOB_COUNT,
        "stage_a_reference_trace_count": 2 * D5_JOB_COUNT,
        "stage_b_receiver_artifact_count": D5_STAGE_B_ARTIFACT_COUNT,
        "quality_option_identities": _quality_option_identities(),
        "ordered_jobs": jobs,
        "ordered_stage_a_evidence": stage_a,
        "ordered_stage_b_receiver_artifacts": stage_b,
        "ordered_artifact_inventory": inventory,
        "scientific_preservation": {
            "receiver_values_component_type": "float64",
            "receiver_values_byte_order": "little-endian",
            "receiver_values_modified_after_decode": False,
            "canonical_receiver_order_validated_by_stage_c": True,
            "far_red_executed": False,
            "calibration_quality_proxy_used": False,
            "quality_fallback_used": False,
            "direct_calibration_executed": False,
            "direct_runtime_transport_preset_changed": False,
            "deferred_display_mapping_implemented": False,
        },
        "promotion_state": {
            "candidate_coefficients_generated": False,
            "production_resource_generated": False,
            "optimized_coloring_enabled": False,
        },
    }


def _validate_completion_manifest(
    path: Path,
    *,
    output: Path,
    configuration_sha256: str,
) -> dict[str, object]:
    payload = _read_json_object(path)
    if (
        payload.get("schema_id") != D5_COMPLETION_SCHEMA_ID
        or payload.get("schema_version") != D5_COMPLETION_SCHEMA_VERSION
        or payload.get("experiment_id") != D5_EXPERIMENT_ID
        or payload.get("d5_identity_version") != D5_IDENTITY_VERSION
        or payload.get("configuration_sha256") != configuration_sha256
        or payload.get("quality_order") != list(D5_QUALITY_ORDER)
        or payload.get("reference_level_order_umol_m2_s")
        != list(D5_REFERENCE_LEVELS_UMOL_M2_S)
        or payload.get("band_order") != list(D5_BAND_ORDER)
        or payload.get("neutral_source")
        != canonical_neutral_source_definition()
        or payload.get("calibrated_quality_families") != list(D5_QUALITY_ORDER)
        or payload.get("deferred_runtime_display_quality_mapping")
        != D5_DEFERRED_DISPLAY_QUALITY_MAPPING
        or payload.get("job_count") != D5_JOB_COUNT
        or payload.get("stage_a_evidence_count") != D5_JOB_COUNT
        or payload.get("stage_a_reference_trace_count") != 2 * D5_JOB_COUNT
        or payload.get("stage_b_receiver_artifact_count")
        != D5_STAGE_B_ARTIFACT_COUNT
    ):
        raise SurfaceFluxRecalibrationError(
            "D5 completion manifest identity or counts are incompatible."
        )
    inventory = payload.get("ordered_artifact_inventory")
    if not isinstance(inventory, list):
        raise SurfaceFluxRecalibrationError(
            "D5 completion artifact inventory is missing."
        )
    for record in inventory:
        if not isinstance(record, Mapping):
            raise SurfaceFluxRecalibrationError(
                "D5 completion artifact record is invalid."
            )
        artifact = output / str(record.get("path"))
        if (
            not artifact.is_file()
            or artifact.is_symlink()
            or artifact.stat().st_size != record.get("byte_length")
            or _sha256_file(artifact) != record.get("sha256")
        ):
            raise SurfaceFluxRecalibrationError(
                f"D5 completion artifact failed validation: {record.get('path')}"
            )
    return payload


def _validate_complete_result_matrix(
    results: Sequence[Mapping[str, object]],
    jobs: Sequence[_Job],
) -> None:
    expected = [
        (
            index,
            job.job_id,
            job.quality,
            job.requested_level,
        )
        for index, job in enumerate(jobs)
    ]
    observed = [
        (
            result.get("job_order_index"),
            result.get("job_id"),
            result.get("quality"),
            result.get("requested_reference_level_umol_m2_s"),
        )
        for result in results
    ]
    if observed != expected or len({value[1] for value in observed}) != D5_JOB_COUNT:
        raise SurfaceFluxRecalibrationError(
            "D5 completed result matrix is missing, duplicated, reordered, or substituted."
        )


def _validate_job_matrix(jobs: Sequence[_Job]) -> None:
    expected_calibrated_families = tuple(
        quality for quality in RADIANCE_QUALITY_NAMES if quality != "direct"
    )
    if (
        tuple(D5_QUALITY_ORDER) != expected_calibrated_families
        or "direct" in D5_QUALITY_ORDER
        or len(jobs) != D5_JOB_COUNT
        or len({job.job_id for job in jobs}) != D5_JOB_COUNT
        or tuple(job.quality for job in jobs)
        != tuple(
            quality
            for quality in D5_QUALITY_ORDER
            for _level in D5_REFERENCE_LEVELS_UMOL_M2_S
        )
        or tuple(job.requested_level for job in jobs)
        != D5_REFERENCE_LEVELS_UMOL_M2_S * len(D5_QUALITY_ORDER)
    ):
        raise SurfaceFluxRecalibrationError(
            "D5 fixed quality/level job matrix is incompatible."
        )


def _quality_option_identity(quality: str) -> dict[str, object]:
    if quality not in D5_QUALITY_ORDER or quality not in RADIANCE_QUALITY_NAMES:
        raise SurfaceFluxRecalibrationError(
            f"D5 rejects non-calibrated, unknown, or fallback quality {quality!r}."
        )
    options = radiance_options(quality)
    payload = {
        "quality": quality,
        "base_radiance_options": options,
        "ambient_cache_policy": "job-and-trace-local",
        "ambient_evaluation_required": True,
        "proxy": False,
        "fallback": False,
    }
    payload["option_identity_sha256"] = _hash_json(payload)
    return payload


def _quality_option_identities() -> list[dict[str, object]]:
    return [_quality_option_identity(quality) for quality in D5_QUALITY_ORDER]


def _rooted_artifact(job_id: str, artifact: Mapping[str, object]) -> dict[str, object]:
    return {
        **dict(artifact),
        "path": f"{D5_JOBS_DIRECTORY}/{job_id}/{artifact['path']}",
    }


def _validate_declared_artifact(
    root: Path,
    artifact: Mapping[str, object],
    *,
    label: str,
) -> None:
    relative = artifact.get("path")
    if not isinstance(relative, str) or not relative:
        raise SurfaceFluxRecalibrationError(f"{label} path is invalid.")
    path = root / relative
    if (
        not path.is_file()
        or path.is_symlink()
        or not path.resolve().is_relative_to(root.resolve(strict=True))
        or path.stat().st_size != artifact.get("byte_length")
        or not _valid_sha256(artifact.get("sha256"))
        or _sha256_file(path) != artifact.get("sha256")
    ):
        raise SurfaceFluxRecalibrationError(
            f"{label} failed complete identity, size, or hash validation."
        )


def _validate_glow_source_text(
    path: Path,
    *,
    amplitude: object,
    label: str,
) -> None:
    try:
        expected = render_neutral_source(amplitude)  # type: ignore[arg-type]
        observed = path.read_text(encoding="ascii")
    except (OSError, UnicodeError, ValueError) as exc:
        raise SurfaceFluxRecalibrationError(
            f"{label} could not authenticate its historical glow emitter: {exc}"
        ) from exc
    if observed != expected:
        raise SurfaceFluxRecalibrationError(
            f"{label} does not match the authenticated historical glow emitter."
        )


def _level_token(value: float) -> str:
    return format(value, ".12g").replace(".", "p")


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _require_config(config: SurfaceFluxRecalibrationConfig) -> None:
    if not isinstance(config, SurfaceFluxRecalibrationConfig):
        raise TypeError("config must be SurfaceFluxRecalibrationConfig.")


def format_surface_flux_recalibration_json(
    payload: Mapping[str, object],
) -> str:
    """Serialize a D5 plan or manifest deterministically."""

    return _pretty_json(payload)


__all__ = [
    "D5_BAND_ORDER",
    "D5_COMPLETION_NAME",
    "D5_COMPLETION_SCHEMA_ID",
    "D5_DEFAULT_OUTPUT_DIRECTORY",
    "D5_DEFERRED_DISPLAY_QUALITY_MAPPING",
    "D5_EXPERIMENT_ID",
    "D5_IDENTITY_VERSION",
    "D5_JOB_COUNT",
    "D5_QUALITY_ORDER",
    "D5_RECEIVERS_SHA256",
    "D5_REFERENCE_LEVELS_UMOL_M2_S",
    "D5_SAMPLING_PROFILE_ID",
    "D5_STAGE_B_ARTIFACT_COUNT",
    "D5_TOPOLOGY_SHA256",
    "SurfaceFluxRecalibrationConfig",
    "SurfaceFluxRecalibrationError",
    "SurfaceFluxRecalibrationPublication",
    "build_surface_flux_recalibration_plan",
    "format_surface_flux_recalibration_json",
    "recalibration_jobs",
    "run_surface_flux_recalibration",
]
