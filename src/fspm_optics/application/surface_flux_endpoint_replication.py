"""Phase 27G-D5-C1 endpoint-only Stage B replication.

This additive runner authenticates completed D5-A1 v3 and D5-A2 v1 inputs,
reuses the A1 Stage A reference and source-amplitude authorities, and executes
only fresh Quality/Standard Stage B receiver traces.  It deliberately does not
derive coefficients, promote calibration data, or change any runtime/display
mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Callable, Mapping, Protocol, Sequence

from fspm_optics.application import surface_flux_coefficient_analysis as a2
from fspm_optics.application.surface_flux_calibration import (
    SurfaceFluxCalibrationError,
    _Job,
    _clean_interrupted_level_stages,
    _command_payload,
    _decode_receiver_rgb,
    _discover_repository_root,
    _hash_json,
    _inventory,
    _pretty_json,
    _read_json_object,
    _relabel,
    _remove_stage,
    _repository_revision,
    _require_nonempty,
    _run_required,
    _sha256_file,
    _utc_now,
    _valid_sha256,
    _validate_export_artifacts,
    _validated_timestamp,
    canonical_calibration_boundary_text,
    render_neutral_source,
)
from fspm_optics.application.surface_flux_coefficient_analysis import (
    COEFFICIENT_ARRAY_COUNT,
    D5_A2_ANALYSIS_ID,
    D5_A2_ANALYSIS_NAME,
    D5_A2_ANALYSIS_SCHEMA_ID,
    D5_A2_ANALYSIS_SCHEMA_VERSION,
    D5_A2_COEFFICIENT_DIRECTORY,
    D5_A2_COEFFICIENT_MANIFEST_NAME,
    D5_A2_COEFFICIENT_SCHEMA_ID,
    D5_A2_COEFFICIENT_SCHEMA_VERSION,
    D5_A2_COMPLETION_NAME,
    D5_A2_COMPLETION_SCHEMA_ID,
    D5_A2_COMPLETION_SCHEMA_VERSION,
    D5_A2_NEUTRAL_DIRECTORY,
    D5_A2_NEUTRAL_MANIFEST_NAME,
    D5_A2_NEUTRAL_SCHEMA_ID,
    D5_A2_NEUTRAL_SCHEMA_VERSION,
)
from fspm_optics.application.surface_flux_recalibration import (
    D5_BAND_ORDER,
    D5_COMPLETION_NAME,
    D5_EXPECTED_PLANT_COUNT,
    D5_EXPECTED_RECEIVER_BYTES_PER_BAND,
    D5_EXPECTED_RECEIVER_COUNT_PER_BAND,
    D5_EXPERIMENT_ID,
    D5_JOBS_DIRECTORY,
    D5_PATCHES_PER_PLANT,
    D5_QUALITY_ORDER,
    D5_RECEIVERS_SHA256,
    D5_REFERENCE_LEVELS_UMOL_M2_S,
    D5_SAMPLING_PROFILE_ID,
    D5_STAGE_B_ARTIFACT_COUNT,
    D5_TOPOLOGY_SHA256,
    _quality_option_identity,
    recalibration_jobs,
)
from fspm_optics.optics.rex_material_plan import (
    RexRadianceTransMaterialPlan,
    render_radiance_trans_material,
)
from fspm_optics.plants.multi_scene import JuvenileScientificScene
from fspm_optics.plants.radiance_scene_export import (
    DEFAULT_LEAF_MATERIAL_MODIFIER,
    materialize_juvenile_radiance_export,
    plan_juvenile_radiance_export,
)
from fspm_optics.radiance.commands import (
    CommandSpec,
    build_oconv_command,
    build_plant_receiver_rtrace_command,
)
from fspm_optics.radiance.options import radiance_options
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.radiance.versioning import (
    RadianceInstallation,
    discover_radiance_installation,
)
from fspm_optics.transport.basis.atomic import atomic_write_text


D5_C1_EXPERIMENT_ID = "phase27g-d5-c1-endpoint-replication-v1"
D5_C1_REPETITION_ID = "repeat-1"
D5_C1_SCHEMA_VERSION = 1
D5_C1_FAMILY_ORDER = ("quality", "standard")
D5_C1_LEVEL_ORDER = (250.0, 500.0)
D5_C1_JOB_COUNT = 4
D5_C1_STAGE_A_TRACE_COUNT = 0
D5_C1_STAGE_B_TRACE_COUNT = 16
D5_C1_SENTINEL_JOB_ID = "quality-250"
D5_C1_SENTINEL_BAND_ID = "blue"
D5_C1_SENTINEL_TRACE_ORDER_INDEX = 0
D5_C1_CANONICAL_RECEIVER_ORDER = (
    "plant-major; local_patch_index 0..191; front then back"
)

D5_C1_PLAN_SCHEMA_ID = "fspm-optics.surface-flux-endpoint-replication-plan"
D5_C1_CONFIGURATION_SCHEMA_ID = (
    "fspm-optics.surface-flux-endpoint-replication-configuration"
)
D5_C1_TRACE_SCHEMA_ID = "fspm-optics.surface-flux-endpoint-replication-trace"
D5_C1_JOB_SCHEMA_ID = "fspm-optics.surface-flux-endpoint-replication-job"
D5_C1_JOB_COMPLETION_SCHEMA_ID = (
    "fspm-optics.surface-flux-endpoint-replication-job-completion"
)
D5_C1_OUTCOME_SCHEMA_ID = "fspm-optics.surface-flux-endpoint-replication-outcome"

D5_C1_CONFIGURATION_NAME = "endpoint-replication-configuration.v1.json"
D5_C1_JOB_RESULT_NAME = "job-result.v1.json"
D5_C1_JOB_COMPLETION_NAME = "job-completion.v1.json"
D5_C1_OUTCOME_NAME = "surface-flux-endpoint-replication-outcome.v1.json"
D5_C1_JOBS_DIRECTORY = "jobs"
D5_C1_TRACES_DIRECTORY = "traces"

D5_C1_DEFAULT_A1_INPUT_DIRECTORY = Path(
    "/home/austin/fspm-calibration-runs/phase27g-d5-a1-run-v3b"
)
D5_C1_DEFAULT_A2_INPUT_DIRECTORY = Path(
    "/home/austin/fspm-calibration-runs/phase27g-d5-a2-analysis-v1"
)
D5_C1_DEFAULT_OUTPUT_DIRECTORY = Path(
    "/home/austin/fspm-calibration-runs/phase27g-d5-c1-endpoint-replication-v1"
)

_A2_ROOT_ENTRIES = {
    D5_A2_ANALYSIS_NAME,
    D5_A2_COEFFICIENT_MANIFEST_NAME,
    D5_A2_NEUTRAL_MANIFEST_NAME,
    D5_A2_COMPLETION_NAME,
    D5_A2_COEFFICIENT_DIRECTORY,
    D5_A2_NEUTRAL_DIRECTORY,
}
_A2_COMPLETION_FIELDS = {
    "schema_id",
    "schema_version",
    "analysis_id",
    "source_experiment_id",
    "source_completion_sha256",
    "status",
    "promotion_eligible",
    "coefficient_array_count",
    "normalized_neutral_artifact_count",
    "report",
    "coefficient_manifest",
    "normalized_neutral_manifest",
    "ordered_artifact_inventory",
    "input_directory_modified",
    "radiance_invoked",
    "production_calibration_resource_generated",
}
_A2_REPORT_FIELDS = {
    "schema_id",
    "schema_version",
    "analysis_id",
    "source_experiment_id",
    "source_completion_sha256",
    "source_configuration_sha256",
    "source_created_at_utc",
    "input_authentication",
    "fixed_identity",
    "scope",
    "equations",
    "criteria",
    "authenticated_jobs",
    "coefficient_order",
    "coefficient_manifest",
    "coefficient_manifest_sha256",
    "normalized_neutral_manifest",
    "normalized_neutral_manifest_sha256",
    "family_side_metric_analyses",
    "promotion_eligible",
    "promotion_reasons",
    "scientific_failure_policy",
    "zero_policy",
    "deferred",
}

EventSink = Callable[[str, Mapping[str, object] | None], None]


class SurfaceFluxEndpointReplicationError(RuntimeError):
    """A D5-C1 input, execution, or publication contract failed."""


class CommandRunner(Protocol):
    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult: ...


@dataclass(frozen=True, slots=True)
class SurfaceFluxEndpointReplicationConfig:
    a1_input_directory: Path = D5_C1_DEFAULT_A1_INPUT_DIRECTORY
    a2_input_directory: Path = D5_C1_DEFAULT_A2_INPUT_DIRECTORY
    output_directory: Path = D5_C1_DEFAULT_OUTPUT_DIRECTORY
    resume: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "a1_input_directory", Path(self.a1_input_directory))
        object.__setattr__(self, "a2_input_directory", Path(self.a2_input_directory))
        object.__setattr__(self, "output_directory", Path(self.output_directory))
        if not isinstance(self.resume, bool):
            raise ValueError("resume must be Boolean.")

    def cli_payload(self) -> dict[str, object]:
        return {
            "a1_input_directory": str(self.a1_input_directory.expanduser().resolve()),
            "a2_input_directory": str(self.a2_input_directory.expanduser().resolve()),
            "output_directory": str(self.output_directory.expanduser().resolve()),
            "resume_requested": self.resume,
        }


@dataclass(frozen=True, slots=True)
class SurfaceFluxEndpointReplicationPublication:
    output_directory: Path
    outcome_path: Path
    outcome: Mapping[str, object]

    @property
    def status(self) -> str:
        return str(self.outcome["status"])


@dataclass(frozen=True, slots=True)
class _AuthenticatedA2:
    root: Path
    completion: Mapping[str, object]
    completion_sha256: str
    report: Mapping[str, object]
    report_sha256: str


@dataclass(frozen=True, slots=True)
class _SourceJob:
    job: _Job
    a1_order_index: int
    a1_job_root: Path
    threads: int
    original_radiance_installation: Mapping[str, object]
    achieved_reference: float
    total_source_amplitude: float
    band_source_amplitude: float
    bands: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class _AuthenticatedInputs:
    a1: a2._AuthenticatedSweep
    a2: _AuthenticatedA2
    source_jobs: tuple[_SourceJob, ...]


def endpoint_replication_jobs() -> tuple[_Job, ...]:
    """Return the immutable Quality-first endpoint matrix."""

    jobs = tuple(
        _Job(
            job_id=f"{family}-{_level_token(level)}",
            kind="primary",
            requested_level=level,
            quality=family,
        )
        for family in D5_C1_FAMILY_ORDER
        for level in D5_C1_LEVEL_ORDER
    )
    expected = (
        "quality-250",
        "quality-500",
        "standard-250",
        "standard-500",
    )
    if (
        tuple(job.job_id for job in jobs) != expected
        or len({job.job_id for job in jobs}) != D5_C1_JOB_COUNT
        or any(job.quality not in D5_C1_FAMILY_ORDER for job in jobs)
    ):
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 fixed Quality-first job matrix changed."
        )
    return jobs


def build_surface_flux_endpoint_replication_plan(
    config: SurfaceFluxEndpointReplicationConfig,
) -> dict[str, object]:
    """Authenticate A1/A2 and return the non-executing, non-writing C1 plan."""

    _require_config(config)
    a1_root, a2_root, output_root = _validated_locations(config)
    authenticated = _authenticate_inputs(a1_root, a2_root)
    return _build_plan(authenticated, output_root=output_root)


def run_surface_flux_endpoint_replication(
    config: SurfaceFluxEndpointReplicationConfig,
    *,
    runner: CommandRunner | None = None,
    radiance_installation: RadianceInstallation | None = None,
    event_sink: EventSink | None = None,
    created_at_utc: str | None = None,
    repository_root: Path | None = None,
) -> SurfaceFluxEndpointReplicationPublication:
    """Execute, resume, or authenticate one fixed D5-C1 repetition."""

    _require_config(config)
    sink = event_sink or (lambda _message, _data=None: None)
    a1_root, a2_root, output = _validated_locations(config)

    # Both upstream trees are fully authenticated before executable discovery.
    authenticated = _authenticate_inputs(a1_root, a2_root)
    plan = _build_plan(authenticated, output_root=output)
    outcome_path = output / D5_C1_OUTCOME_NAME
    if output.exists() and outcome_path.exists():
        state = _read_json_object(output / D5_C1_CONFIGURATION_NAME)
        identity = _validate_stored_identity_without_discovery(
            state,
            config=config,
            authenticated=authenticated,
            plan=plan,
        )
        outcome = _validate_outcome(
            output,
            identity=identity,
            authenticated=authenticated,
        )
        return SurfaceFluxEndpointReplicationPublication(output, outcome_path, outcome)
    if output.exists() and not config.resume:
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 output must initially be absent; use --resume only for an "
            "exact partial run."
        )

    repository = (
        _discover_repository_root()
        if repository_root is None
        else Path(repository_root).resolve()
    )
    source_config = authenticated.a1.configuration
    try:
        installation = radiance_installation or discover_radiance_installation(
            oconv_command=source_config.oconv_command,
            rtrace_command=source_config.rtrace_command,
            cwd=repository,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 Radiance discovery failed after input authentication: {exc}"
        ) from exc
    original_installation = authenticated.a1.configuration_identity.get(
        "radiance_installation"
    )
    if installation.to_dict() != original_installation:
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 repeated executable identities do not exactly match A1."
        )

    identity = _configuration_identity(
        config,
        authenticated=authenticated,
        plan=plan,
        installation=installation,
        repository_revision=_repository_revision(repository),
    )
    state = _prepare_output(
        output,
        config=config,
        identity=identity,
        created_at_utc=created_at_utc or _utc_now(),
    )
    jobs_root = output / D5_C1_JOBS_DIRECTORY
    if jobs_root.exists() and (jobs_root.is_symlink() or not jobs_root.is_dir()):
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 jobs root is unsafe or not a directory."
        )
    jobs_root.mkdir(exist_ok=True)
    if config.resume:
        _clean_interrupted_level_stages(jobs_root)
    _validate_jobs_directory(jobs_root)
    _validate_committed_job_prefix(jobs_root)

    native_runner = runner or LocalRunner()
    results: list[dict[str, object]] = []
    for job_order_index, source in enumerate(authenticated.source_jobs):
        final_root = jobs_root / source.job.job_id
        if final_root.exists():
            if not config.resume:
                raise SurfaceFluxEndpointReplicationError(
                    f"D5-C1 job exists without --resume: {source.job.job_id}"
                )
            result = _validate_completed_job(
                final_root,
                source=source,
                job_order_index=job_order_index,
                configuration_sha256=str(identity["configuration_sha256"]),
                installation=installation,
            )
        else:
            result = _execute_and_commit_job(
                jobs_root,
                source=source,
                job_order_index=job_order_index,
                configuration_sha256=str(identity["configuration_sha256"]),
                authenticated=authenticated,
                runner=native_runner,
                installation=installation,
                event_sink=sink,
            )
        results.append(result)
        if result.get("status") == "blocked":
            break

    status = "blocked" if results[-1].get("status") == "blocked" else "complete"
    if status == "complete" and len(results) != D5_C1_JOB_COUNT:
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 cannot publish complete without four ordered jobs."
        )
    outcome = _build_outcome(
        output,
        identity=identity,
        authenticated=authenticated,
        results=results,
        created_at_utc=str(state["created_at_utc"]),
        status=status,
    )
    if outcome_path.exists():
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 outcome appeared during execution; refusing overwrite."
        )
    atomic_write_text(outcome_path, _pretty_json(outcome))
    validated = _validate_outcome(
        output,
        identity=identity,
        authenticated=authenticated,
    )
    sink(
        f"Published authenticated D5-C1 {status} outcome.",
        {
            "outcome": str(outcome_path),
            "status": status,
            "stage_b_trace_count_executed": validated[
                "stage_b_trace_count_executed"
            ],
        },
    )
    return SurfaceFluxEndpointReplicationPublication(output, outcome_path, validated)


def _validated_locations(
    config: SurfaceFluxEndpointReplicationConfig,
) -> tuple[Path, Path, Path]:
    a1_root = _real_input_root(config.a1_input_directory, "D5-A1")
    a2_root = _real_input_root(config.a2_input_directory, "D5-A2")
    if a1_root == a2_root or a1_root.is_relative_to(a2_root) or a2_root.is_relative_to(a1_root):
        raise SurfaceFluxEndpointReplicationError(
            "D5-A1 and D5-A2 inputs must be distinct and non-overlapping."
        )
    expanded_output = config.output_directory.expanduser()
    if expanded_output.is_symlink():
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 output must not be a symbolic link."
        )
    output = expanded_output.resolve()
    for input_root in (a1_root, a2_root):
        if (
            output == input_root
            or output.is_relative_to(input_root)
            or input_root.is_relative_to(output)
        ):
            raise SurfaceFluxEndpointReplicationError(
                "D5-C1 output must not overlap either read-only input."
            )
    if output.exists() and not output.is_dir():
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 output must be an absent path or a real directory."
        )
    return a1_root, a2_root, output


def _real_input_root(path: Path, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise SurfaceFluxEndpointReplicationError(
            f"{label} input must be a real non-symbolic-link directory."
        )
    try:
        root = expanded.resolve(strict=True)
    except OSError as exc:
        raise SurfaceFluxEndpointReplicationError(
            f"{label} input directory does not exist: {path}"
        ) from exc
    if not root.is_dir() or root.is_symlink():
        raise SurfaceFluxEndpointReplicationError(
            f"{label} input must be a real non-symbolic-link directory."
        )
    return root


def _authenticate_inputs(a1_root: Path, a2_root: Path) -> _AuthenticatedInputs:
    try:
        authenticated_a1 = a2._authenticate_completed_sweep(a1_root)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 rejected the completed D5-A1 v3 authority: {exc}"
        ) from exc
    authenticated_a2 = _authenticate_completed_a2(a2_root, authenticated_a1)
    source_jobs = _extract_source_jobs(authenticated_a1, authenticated_a2)
    return _AuthenticatedInputs(authenticated_a1, authenticated_a2, source_jobs)


def _authenticate_completed_a2(
    root: Path,
    authenticated_a1: a2._AuthenticatedSweep,
) -> _AuthenticatedA2:
    entries = tuple(root.iterdir())
    if {path.name for path in entries} != _A2_ROOT_ENTRIES or any(
        path.is_symlink() for path in entries
    ):
        raise SurfaceFluxEndpointReplicationError(
            "completed D5-A2 v1 root inventory is missing, extra, or unsafe."
        )
    if not (root / D5_A2_COEFFICIENT_DIRECTORY).is_dir() or not (
        root / D5_A2_NEUTRAL_DIRECTORY
    ).is_dir():
        raise SurfaceFluxEndpointReplicationError(
            "completed D5-A2 v1 binary directories are invalid."
        )
    try:
        completion = _read_json_object(root / D5_A2_COMPLETION_NAME)
        _validate_a2_completion(completion, authenticated_a1)
        actual_inventory = _inventory(root, excluded={D5_A2_COMPLETION_NAME})
        if completion.get("ordered_artifact_inventory") != actual_inventory:
            raise SurfaceFluxEndpointReplicationError(
                "D5-A2 completion inventory is incomplete, reordered, extra, "
                "or not byte-authenticated."
            )
        report_record = _a2_authority(
            completion.get("report"), D5_A2_ANALYSIS_NAME, "D5-A2 report"
        )
        coefficient_record = _a2_authority(
            completion.get("coefficient_manifest"),
            D5_A2_COEFFICIENT_MANIFEST_NAME,
            "D5-A2 coefficient manifest",
        )
        neutral_record = _a2_authority(
            completion.get("normalized_neutral_manifest"),
            D5_A2_NEUTRAL_MANIFEST_NAME,
            "D5-A2 normalized-neutral manifest",
        )
        report = _authorized_json(root, report_record, "D5-A2 report")
        coefficient = _authorized_json(
            root, coefficient_record, "D5-A2 coefficient manifest"
        )
        neutral = _authorized_json(
            root, neutral_record, "D5-A2 normalized-neutral manifest"
        )
        if (
            coefficient.get("schema_id") != D5_A2_COEFFICIENT_SCHEMA_ID
            or coefficient.get("schema_version")
            != D5_A2_COEFFICIENT_SCHEMA_VERSION
            or coefficient.get("analysis_id") != D5_A2_ANALYSIS_ID
            or coefficient.get("source_experiment_id") != D5_EXPERIMENT_ID
            or coefficient.get("source_completion_sha256")
            != authenticated_a1.completion_sha256
            or coefficient.get("array_count") != COEFFICIENT_ARRAY_COUNT
            or not isinstance(coefficient.get("coefficient_artifacts"), list)
            or len(coefficient["coefficient_artifacts"]) != COEFFICIENT_ARRAY_COUNT
            or coefficient.get("production_calibration_resource") is not False
        ):
            raise SurfaceFluxEndpointReplicationError(
                "D5-A2 coefficient-manifest schema is incompatible."
            )
        if (
            neutral.get("schema_id") != D5_A2_NEUTRAL_SCHEMA_ID
            or neutral.get("schema_version") != D5_A2_NEUTRAL_SCHEMA_VERSION
            or neutral.get("analysis_id") != D5_A2_ANALYSIS_ID
            or neutral.get("source_experiment_id") != D5_EXPERIMENT_ID
            or neutral.get("source_completion_sha256")
            != authenticated_a1.completion_sha256
            or neutral.get("artifact_count") != 24
            or not isinstance(neutral.get("artifacts"), list)
            or len(neutral["artifacts"]) != 24
            or neutral.get("palette_anchors_selected") is not False
        ):
            raise SurfaceFluxEndpointReplicationError(
                "D5-A2 normalized-neutral schema is incompatible."
            )
        _validate_a2_report(
            report,
            completion=completion,
            authenticated_a1=authenticated_a1,
            coefficient_sha256=str(coefficient_record["sha256"]),
            neutral_sha256=str(neutral_record["sha256"]),
        )
    except SurfaceFluxEndpointReplicationError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise SurfaceFluxEndpointReplicationError(
            f"completed D5-A2 v1 authentication failed: {exc}"
        ) from exc
    return _AuthenticatedA2(
        root=root,
        completion=completion,
        completion_sha256=_sha256_file(root / D5_A2_COMPLETION_NAME),
        report=report,
        report_sha256=str(report_record["sha256"]),
    )


def _validate_a2_completion(
    completion: Mapping[str, object], authenticated_a1: a2._AuthenticatedSweep
) -> None:
    if set(completion) != _A2_COMPLETION_FIELDS:
        raise SurfaceFluxEndpointReplicationError(
            "D5-A2 completion field inventory is incompatible."
        )
    eligible = completion.get("promotion_eligible")
    if (
        completion.get("schema_id") != D5_A2_COMPLETION_SCHEMA_ID
        or completion.get("schema_version") != D5_A2_COMPLETION_SCHEMA_VERSION
        or completion.get("analysis_id") != D5_A2_ANALYSIS_ID
        or completion.get("source_experiment_id") != D5_EXPERIMENT_ID
        or completion.get("source_completion_sha256")
        != authenticated_a1.completion_sha256
        or completion.get("status") != "complete"
        or not isinstance(eligible, bool)
        or completion.get("coefficient_array_count") != COEFFICIENT_ARRAY_COUNT
        or completion.get("normalized_neutral_artifact_count") != 24
        or not isinstance(completion.get("ordered_artifact_inventory"), list)
        or completion.get("input_directory_modified") is not False
        or completion.get("radiance_invoked") is not False
        or completion.get("production_calibration_resource_generated") is not False
    ):
        raise SurfaceFluxEndpointReplicationError(
            "D5-A2 completion is not the required completed v1 authority."
        )


def _validate_a2_report(
    report: Mapping[str, object],
    *,
    completion: Mapping[str, object],
    authenticated_a1: a2._AuthenticatedSweep,
    coefficient_sha256: str,
    neutral_sha256: str,
) -> None:
    if set(report) != _A2_REPORT_FIELDS:
        raise SurfaceFluxEndpointReplicationError(
            "D5-A2 report field inventory is incompatible."
        )
    if (
        report.get("schema_id") != D5_A2_ANALYSIS_SCHEMA_ID
        or report.get("schema_version") != D5_A2_ANALYSIS_SCHEMA_VERSION
        or report.get("analysis_id") != D5_A2_ANALYSIS_ID
        or report.get("source_experiment_id") != D5_EXPERIMENT_ID
        or report.get("source_completion_sha256")
        != authenticated_a1.completion_sha256
        or report.get("source_configuration_sha256")
        != authenticated_a1.completion.get("configuration_sha256")
        or report.get("source_created_at_utc")
        != authenticated_a1.completion.get("created_at_utc")
        or report.get("promotion_eligible")
        is not completion.get("promotion_eligible")
        or report.get("coefficient_manifest")
        != D5_A2_COEFFICIENT_MANIFEST_NAME
        or report.get("coefficient_manifest_sha256") != coefficient_sha256
        or report.get("normalized_neutral_manifest")
        != D5_A2_NEUTRAL_MANIFEST_NAME
        or report.get("normalized_neutral_manifest_sha256") != neutral_sha256
    ):
        raise SurfaceFluxEndpointReplicationError(
            "D5-A2 report authority links or source identity changed."
        )
    _validated_timestamp(report.get("source_created_at_utc"))
    expected_input_authentication = {
        "status": "fully_authenticated_before_receiver_value_decode",
        "configuration_hash_validated": True,
        "completion_status_required": "complete",
        "complete_inventory_sizes_and_sha256_validated": True,
        "six_job_completion_manifests_validated": True,
        "stage_a_trace_count": 12,
        "stage_b_float64_artifact_count": D5_STAGE_B_ARTIFACT_COUNT,
        "quality_option_identities_validated": True,
        "achieved_stage_a_references_authenticated": True,
    }
    expected_fixed = {
        "sampling_profile_id": D5_SAMPLING_PROFILE_ID,
        "topology_sha256": D5_TOPOLOGY_SHA256,
        "receivers_sha256": D5_RECEIVERS_SHA256,
        "quality_order": list(D5_QUALITY_ORDER),
        "requested_level_order_umol_m2_s": list(D5_REFERENCE_LEVELS_UMOL_M2_S),
        "band_order": list(D5_BAND_ORDER),
        "plant_count": D5_EXPECTED_PLANT_COUNT,
        "patches_per_plant": D5_PATCHES_PER_PLANT,
        "canonical_receiver_order": D5_C1_CANONICAL_RECEIVER_ORDER,
    }
    expected_scope = {
        "calibrated_families": list(D5_QUALITY_ORDER),
        "direct_calibrated": False,
        "direct_future_display_only_mapping": {"direct": "standard"},
        "direct_future_display_mapping_implemented": False,
        "direct_raw_transport_proxy": False,
        "far_red_present": False,
        "proposed_present": False,
        "conventional_present": False,
    }
    if (
        report.get("input_authentication") != expected_input_authentication
        or report.get("fixed_identity") != expected_fixed
        or report.get("scope") != expected_scope
    ):
        raise SurfaceFluxEndpointReplicationError(
            "D5-A2 authenticated identity or scope changed."
        )
    _validate_a2_job_links(report, authenticated_a1)


def _validate_a2_job_links(
    report: Mapping[str, object], authenticated_a1: a2._AuthenticatedSweep
) -> None:
    records = report.get("authenticated_jobs")
    jobs = recalibration_jobs()
    if not isinstance(records, list) or len(records) != len(jobs):
        raise SurfaceFluxEndpointReplicationError(
            "D5-A2 authenticated job matrix is incomplete."
        )
    for job, result, record in zip(
        jobs, authenticated_a1.results, records, strict=True
    ):
        if not isinstance(record, Mapping):
            raise SurfaceFluxEndpointReplicationError(
                "D5-A2 authenticated job record is invalid."
            )
        achieved = _a1_achieved_reference(result, job.job_id)
        bands = _a1_bands(result, job.job_id)
        expected_hashes = {
            band_id: band["receiver_values"]["sha256"]
            for band_id, band in zip(D5_BAND_ORDER, bands, strict=True)
        }
        if (
            record.get("job_id") != job.job_id
            or record.get("family") != job.quality
            or record.get("requested_reference_level_umol_m2_s")
            != job.requested_level
            or record.get("achieved_reference_R_umol_m2_s") != achieved
            or record.get("source_receiver_sha256_by_band") != expected_hashes
        ):
            raise SurfaceFluxEndpointReplicationError(
                f"D5-A2 job authority disagrees with A1: {job.job_id}"
            )


def _extract_source_jobs(
    authenticated_a1: a2._AuthenticatedSweep,
    authenticated_a2: _AuthenticatedA2,
) -> tuple[_SourceJob, ...]:
    del authenticated_a2
    a1_jobs = recalibration_jobs()
    by_id = {
        job.job_id: (index, job, result)
        for index, (job, result) in enumerate(
            zip(a1_jobs, authenticated_a1.results, strict=True)
        )
    }
    source_jobs: list[_SourceJob] = []
    for job in endpoint_replication_jobs():
        a1_order_index, a1_job, result = by_id[job.job_id]
        if a1_job.quality != job.quality or a1_job.requested_level != job.requested_level:
            raise SurfaceFluxEndpointReplicationError(
                f"D5-C1 A1 endpoint identity changed: {job.job_id}"
            )
        achieved = _a1_achieved_reference(result, job.job_id)
        reference = _mapping(result.get("reference"), f"{job.job_id} reference")
        resolution = _mapping(
            reference.get("amplitude_resolution"), f"{job.job_id} amplitude"
        )
        total_amplitude = _positive_finite(
            resolution.get("resolved_total_source_radiance_amplitude"),
            f"{job.job_id} total source amplitude",
        )
        transport = _mapping(
            result.get("plant_transport"), f"{job.job_id} plant transport"
        )
        band_amplitude = _positive_finite(
            transport.get("band_source_amplitude"),
            f"{job.job_id} band source amplitude",
        )
        bands = _a1_bands(result, job.job_id)
        if not math.isclose(
            total_amplitude / len(D5_BAND_ORDER),
            band_amplitude,
            rel_tol=0.0,
            abs_tol=0.0,
        ):
            raise SurfaceFluxEndpointReplicationError(
                f"D5-C1 source amplitudes disagree for {job.job_id}."
            )
        for band_order_index, (band_id, band) in enumerate(
            zip(D5_BAND_ORDER, bands, strict=True)
        ):
            source = _mapping(band.get("source"), f"{job.job_id}/{band_id} source")
            receiver = _mapping(
                band.get("receiver_values"), f"{job.job_id}/{band_id} receiver"
            )
            if (
                source.get("rendered_amplitude") != band_amplitude
                or receiver.get("row_count") != D5_EXPECTED_RECEIVER_COUNT_PER_BAND
                or receiver.get("byte_length") != D5_EXPECTED_RECEIVER_BYTES_PER_BAND
                or not _valid_sha256(receiver.get("sha256"))
            ):
                raise SurfaceFluxEndpointReplicationError(
                    f"D5-C1 original Stage B authority changed: {job.job_id}/{band_id}"
                )
            _validate_original_stage_b_commands(
                band,
                job=job,
                band_id=band_id,
                band_order_index=band_order_index,
                threads=authenticated_a1.configuration.threads,
                installation=_mapping(
                    authenticated_a1.configuration_identity.get(
                        "radiance_installation"
                    ),
                    "D5-A1 Radiance installation",
                ),
            )
        source_jobs.append(
            _SourceJob(
                job=job,
                a1_order_index=a1_order_index,
                a1_job_root=(
                    authenticated_a1.input_root / D5_JOBS_DIRECTORY / job.job_id
                ),
                threads=authenticated_a1.configuration.threads,
                original_radiance_installation=_mapping(
                    authenticated_a1.configuration_identity.get(
                        "radiance_installation"
                    ),
                    "D5-A1 Radiance installation",
                ),
                achieved_reference=achieved,
                total_source_amplitude=total_amplitude,
                band_source_amplitude=band_amplitude,
                bands=bands,
            )
        )
    return tuple(source_jobs)


def _build_plan(
    authenticated: _AuthenticatedInputs,
    *,
    output_root: Path,
) -> dict[str, object]:
    trace_order: list[dict[str, object]] = []
    trace_index = 0
    for job_order_index, source in enumerate(authenticated.source_jobs):
        for band_order_index, (band_id, band) in enumerate(
            zip(D5_BAND_ORDER, source.bands, strict=True)
        ):
            receiver = _mapping(
                band.get("receiver_values"), f"{source.job.job_id}/{band_id} receiver"
            )
            trace_order.append(
                {
                    "trace_order_index": trace_index,
                    "job_order_index": job_order_index,
                    "job_id": source.job.job_id,
                    "family": source.job.quality,
                    "requested_reference_level_umol_m2_s": source.job.requested_level,
                    "band_order_index": band_order_index,
                    "band_id": band_id,
                    "sentinel": trace_index == D5_C1_SENTINEL_TRACE_ORDER_INDEX,
                    "original_receiver_sha256": receiver["sha256"],
                }
            )
            trace_index += 1
    if len(trace_order) != D5_C1_STAGE_B_TRACE_COUNT:
        raise SurfaceFluxEndpointReplicationError("D5-C1 trace plan is incomplete.")
    plan: dict[str, object] = {
        "schema_id": D5_C1_PLAN_SCHEMA_ID,
        "schema_version": D5_C1_SCHEMA_VERSION,
        "experiment_id": D5_C1_EXPERIMENT_ID,
        "repetition_id": D5_C1_REPETITION_ID,
        "mode": "plan_only",
        "input_authentication_performed": True,
        "radiance_discovery_performed": False,
        "radiance_execution_performed": False,
        "output_created": False,
        "families": list(D5_C1_FAMILY_ORDER),
        "levels_umol_m2_s": list(D5_C1_LEVEL_ORDER),
        "bands": list(D5_BAND_ORDER),
        "job_order": [
            {
                "job_order_index": index,
                **source.job.to_dict(),
                "achieved_reference_R_umol_m2_s": source.achieved_reference,
                "resolved_total_source_radiance_amplitude": source.total_source_amplitude,
                "band_source_amplitude": source.band_source_amplitude,
            }
            for index, source in enumerate(authenticated.source_jobs)
        ],
        "trace_order": trace_order,
        "stage_a_trace_count": D5_C1_STAGE_A_TRACE_COUNT,
        "stage_b_trace_count": D5_C1_STAGE_B_TRACE_COUNT,
        "sentinel": {
            "trace_order_index": D5_C1_SENTINEL_TRACE_ORDER_INDEX,
            "job_id": D5_C1_SENTINEL_JOB_ID,
            "family": "quality",
            "requested_reference_level_umol_m2_s": 250.0,
            "band_id": D5_C1_SENTINEL_BAND_ID,
            "stop_if_byte_identical_to_original": True,
            "remaining_trace_count_if_blocked": 15,
        },
        "quality_option_identities": [
            _quality_option_identity(family) for family in D5_C1_FAMILY_ORDER
        ],
        "source_authorities": _source_authorities(authenticated),
        "receiver_contract": _receiver_contract(),
        "cache_policy": _cache_policy(),
        "scope": _scope_contract(),
        "output_contract": {
            "output_directory": str(output_root),
            "initial_state": "absent",
            "sequential_execution": True,
            "atomic_per_job_directory_commit": True,
            "resume_reauthenticates_committed_jobs": True,
            "overwrite_supported": False,
            "blocked_is_not_complete": True,
        },
    }
    plan["plan_sha256"] = _hash_json(plan)
    return plan


def _configuration_identity(
    config: SurfaceFluxEndpointReplicationConfig,
    *,
    authenticated: _AuthenticatedInputs,
    plan: Mapping[str, object],
    installation: RadianceInstallation,
    repository_revision: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_id": D5_C1_CONFIGURATION_SCHEMA_ID,
        "schema_version": D5_C1_SCHEMA_VERSION,
        "experiment_id": D5_C1_EXPERIMENT_ID,
        "repetition_id": D5_C1_REPETITION_ID,
        "a1_input_directory": str(authenticated.a1.input_root),
        "a2_input_directory": str(authenticated.a2.root),
        "output_directory": str(config.output_directory.expanduser().resolve()),
        "source_authorities": _source_authorities(authenticated),
        "plan_sha256": plan["plan_sha256"],
        "quality_option_identities": [
            _quality_option_identity(family) for family in D5_C1_FAMILY_ORDER
        ],
        "original_radiance_installation": authenticated.a1.configuration_identity[
            "radiance_installation"
        ],
        "repeated_radiance_installation": installation.to_dict(),
        "threads": authenticated.a1.configuration.threads,
        "cache_policy": _cache_policy(),
        "repository_revision": repository_revision,
    }
    payload["configuration_sha256"] = _hash_json(payload)
    return payload


def _prepare_output(
    output: Path,
    *,
    config: SurfaceFluxEndpointReplicationConfig,
    identity: Mapping[str, object],
    created_at_utc: str,
) -> dict[str, object]:
    if not output.exists():
        output.mkdir(parents=True)
    entries = tuple(output.iterdir())
    state_path = output / D5_C1_CONFIGURATION_NAME
    if not entries:
        state = {
            "schema_id": D5_C1_CONFIGURATION_SCHEMA_ID,
            "schema_version": D5_C1_SCHEMA_VERSION,
            "experiment_id": D5_C1_EXPERIMENT_ID,
            "repetition_id": D5_C1_REPETITION_ID,
            "created_at_utc": _validated_timestamp(created_at_utc),
            "initial_cli_configuration": config.cli_payload(),
            "identity": dict(identity),
        }
        atomic_write_text(state_path, _pretty_json(state))
        return state
    if not config.resume:
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 output is non-empty; use --resume for an exact partial run."
        )
    allowed = {
        D5_C1_CONFIGURATION_NAME,
        D5_C1_JOBS_DIRECTORY,
        D5_C1_OUTCOME_NAME,
    }
    unexpected = sorted(path.name for path in entries if path.name not in allowed)
    if unexpected:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 output contains unexpected entries: {unexpected!r}."
        )
    state = _read_json_object(state_path)
    _validate_state_header(state, identity)
    if state.get("identity") != dict(identity):
        raise SurfaceFluxEndpointReplicationError(
            "stored D5-C1 configuration does not match exactly."
        )
    return state


def _validate_stored_identity_without_discovery(
    state: Mapping[str, object],
    *,
    config: SurfaceFluxEndpointReplicationConfig,
    authenticated: _AuthenticatedInputs,
    plan: Mapping[str, object],
) -> Mapping[str, object]:
    identity = state.get("identity")
    if not isinstance(identity, Mapping):
        raise SurfaceFluxEndpointReplicationError(
            "stored D5-C1 configuration identity is missing."
        )
    _validate_state_header(state, identity)
    installation = identity.get("repeated_radiance_installation")
    expected_original = authenticated.a1.configuration_identity.get(
        "radiance_installation"
    )
    without_hash = {
        key: value for key, value in identity.items() if key != "configuration_sha256"
    }
    if (
        identity.get("schema_id") != D5_C1_CONFIGURATION_SCHEMA_ID
        or identity.get("schema_version") != D5_C1_SCHEMA_VERSION
        or identity.get("experiment_id") != D5_C1_EXPERIMENT_ID
        or identity.get("repetition_id") != D5_C1_REPETITION_ID
        or identity.get("a1_input_directory") != str(authenticated.a1.input_root)
        or identity.get("a2_input_directory") != str(authenticated.a2.root)
        or identity.get("output_directory")
        != str(config.output_directory.expanduser().resolve())
        or identity.get("source_authorities") != _source_authorities(authenticated)
        or identity.get("plan_sha256") != plan.get("plan_sha256")
        or identity.get("quality_option_identities")
        != [_quality_option_identity(family) for family in D5_C1_FAMILY_ORDER]
        or identity.get("original_radiance_installation") != expected_original
        or installation != expected_original
        or identity.get("threads") != authenticated.a1.configuration.threads
        or identity.get("cache_policy") != _cache_policy()
        or identity.get("configuration_sha256") != _hash_json(without_hash)
    ):
        raise SurfaceFluxEndpointReplicationError(
            "stored D5-C1 identity or authenticated source chain changed."
        )
    _validated_timestamp(state.get("created_at_utc"))
    return identity


def _validate_state_header(
    state: Mapping[str, object], identity: Mapping[str, object]
) -> None:
    expected_fields = {
        "schema_id",
        "schema_version",
        "experiment_id",
        "repetition_id",
        "created_at_utc",
        "initial_cli_configuration",
        "identity",
    }
    initial = state.get("initial_cli_configuration")
    if set(state) != expected_fields or not isinstance(initial, Mapping):
        raise SurfaceFluxEndpointReplicationError(
            "stored D5-C1 configuration field inventory is incompatible."
        )
    if set(initial) != {
        "a1_input_directory",
        "a2_input_directory",
        "output_directory",
        "resume_requested",
    }:
        raise SurfaceFluxEndpointReplicationError(
            "stored D5-C1 initial CLI field inventory is incompatible."
        )
    try:
        initial_a1 = Path(str(initial["a1_input_directory"])).expanduser().resolve()
        initial_a2 = Path(str(initial["a2_input_directory"])).expanduser().resolve()
        initial_output = Path(str(initial["output_directory"])).expanduser().resolve()
    except (OSError, ValueError) as exc:
        raise SurfaceFluxEndpointReplicationError(
            "stored D5-C1 initial CLI paths are invalid."
        ) from exc
    if (
        state.get("schema_id") != D5_C1_CONFIGURATION_SCHEMA_ID
        or state.get("schema_version") != D5_C1_SCHEMA_VERSION
        or state.get("experiment_id") != D5_C1_EXPERIMENT_ID
        or state.get("repetition_id") != D5_C1_REPETITION_ID
        or initial_a1 != Path(str(identity.get("a1_input_directory")))
        or initial_a2 != Path(str(identity.get("a2_input_directory")))
        or initial_output != Path(str(identity.get("output_directory")))
        or not isinstance(initial.get("resume_requested"), bool)
    ):
        raise SurfaceFluxEndpointReplicationError(
            "stored D5-C1 configuration header or initial CLI identity changed."
        )
    _validated_timestamp(state.get("created_at_utc"))


def _execute_and_commit_job(
    jobs_root: Path,
    *,
    source: _SourceJob,
    job_order_index: int,
    configuration_sha256: str,
    authenticated: _AuthenticatedInputs,
    runner: CommandRunner,
    installation: RadianceInstallation,
    event_sink: EventSink,
) -> dict[str, object]:
    final_root = jobs_root / source.job.job_id
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{source.job.job_id}.", suffix=".tmp", dir=jobs_root
        )
    )
    try:
        traces: list[dict[str, object]] = []
        status = "complete"
        material_plan = _material_plan()
        for band_order_index, (band_id, original_band) in enumerate(
            zip(D5_BAND_ORDER, source.bands, strict=True)
        ):
            trace_order_index = job_order_index * len(D5_BAND_ORDER) + band_order_index
            event_sink(
                f"Executing D5-C1 trace {trace_order_index + 1}/16 "
                f"({source.job.job_id}/{band_id}).",
                {"trace_order_index": trace_order_index},
            )
            trace_root = (
                stage
                / D5_C1_TRACES_DIRECTORY
                / f"{band_order_index:02d}-{band_id}"
            )
            trace = _execute_trace(
                trace_root,
                source=source,
                original_band=original_band,
                band_id=band_id,
                band_order_index=band_order_index,
                trace_order_index=trace_order_index,
                scene=authenticated.a1.scene,
                material_plan=material_plan,
                threads=authenticated.a1.configuration.threads,
                runner=runner,
                installation=installation,
            )
            traces.append(trace)
            if (
                trace_order_index == D5_C1_SENTINEL_TRACE_ORDER_INDEX
                and trace["byte_identical_to_original"] is True
            ):
                status = "blocked"
                break
        result = _job_result(
            source=source,
            job_order_index=job_order_index,
            configuration_sha256=configuration_sha256,
            traces=traces,
            status=status,
        )
        result_path = stage / D5_C1_JOB_RESULT_NAME
        atomic_write_text(result_path, _pretty_json(result))
        inventory = _inventory(stage, excluded={D5_C1_JOB_COMPLETION_NAME})
        completion = {
            "schema_id": D5_C1_JOB_COMPLETION_SCHEMA_ID,
            "schema_version": D5_C1_SCHEMA_VERSION,
            "experiment_id": D5_C1_EXPERIMENT_ID,
            "repetition_id": D5_C1_REPETITION_ID,
            "configuration_sha256": configuration_sha256,
            "job_order_index": job_order_index,
            "job": source.job.to_dict(),
            "status": status,
            "trace_count": len(traces),
            "job_result": _file_authority(stage, result_path),
            "ordered_artifact_inventory": inventory,
            "atomic_commit_policy": (
                "all retained artifacts hashed before same-filesystem directory rename"
            ),
        }
        atomic_write_text(
            stage / D5_C1_JOB_COMPLETION_NAME, _pretty_json(completion)
        )
        if final_root.exists():
            raise SurfaceFluxEndpointReplicationError(
                f"D5-C1 job destination appeared: {source.job.job_id}"
            )
        os.replace(stage, final_root)
        return _validate_completed_job(
            final_root,
            source=source,
            job_order_index=job_order_index,
            configuration_sha256=configuration_sha256,
            installation=installation,
        )
    except BaseException:
        _remove_stage(stage, jobs_root)
        raise


def _material_plan() -> RexRadianceTransMaterialPlan:
    # The A1 authenticator rebuilt and validated this exact plan.  Rebuild it once
    # through its authoritative helper without reading or copying A1 workspaces.
    from fspm_optics.optics.rex_material_plan import (
        build_rex_radiance_trans_material_plan,
    )

    return build_rex_radiance_trans_material_plan()


def _execute_trace(
    trace_root: Path,
    *,
    source: _SourceJob,
    original_band: Mapping[str, object],
    band_id: str,
    band_order_index: int,
    trace_order_index: int,
    scene: JuvenileScientificScene,
    material_plan: RexRadianceTransMaterialPlan,
    threads: int,
    runner: CommandRunner,
    installation: RadianceInstallation,
) -> dict[str, object]:
    trace_root.mkdir(parents=True)
    boundary_path = trace_root / "scene-boundary.rad"
    source_path = trace_root / "source.rad"
    material_path = trace_root / "leaf-material.rad"
    octree_path = trace_root / "scene.oct"
    ambient_path = trace_root / "scene.amb"
    raw_rgb_path = trace_root / "receiver.rgb"
    values_path = trace_root / "receiver-values.v1.f64le.bin"
    oconv_stderr = trace_root / "oconv.stderr.log"
    rtrace_stderr = trace_root / "rtrace.stderr.log"
    atomic_write_text(boundary_path, canonical_calibration_boundary_text())
    try:
        export = materialize_juvenile_radiance_export(
            plan_juvenile_radiance_export(scene), trace_root / "scene"
        )
        _validate_export_artifacts(export.artifacts, trace_root / "scene")
    except (OSError, RuntimeError, ValueError) as exc:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 scene export failed for trace {trace_order_index}: {exc}"
        ) from exc
    material = material_plan.material(band_id)
    atomic_write_text(source_path, render_neutral_source(source.band_source_amplitude))
    atomic_write_text(
        material_path,
        render_radiance_trans_material(
            DEFAULT_LEAF_MATERIAL_MODIFIER, material.parameters
        ),
    )
    if (
        _sha256_file(source_path) != original_band.get("source_sha256")
        or _sha256_file(material_path) != original_band.get("material_sha256")
    ):
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 reconstructed source/material changed for "
            f"{source.job.job_id}/{band_id}."
        )
    options = tuple(radiance_options(source.job.quality, ambient_cache=ambient_path))
    oconv = build_oconv_command(
        (boundary_path, source_path, material_path, export.geometry_path),
        output_octree=octree_path,
        cwd=trace_root,
        oconv_bin=installation.oconv.path,
        label=(
            f"surface_flux_endpoint_replication_{source.job.job_id}_{band_id}_oconv"
        ),
    )
    rtrace = _relabel(
        build_plant_receiver_rtrace_command(
            octree=octree_path,
            receiver_input=export.receiver_input_path,
            rgb_output=raw_rgb_path,
            options=options,
            nthreads=threads,
            cwd=trace_root,
            rtrace_bin=installation.rtrace.path,
        ),
        f"surface_flux_endpoint_replication_{source.job.job_id}_{band_id}_rtrace",
    )
    try:
        _run_required(runner, oconv, oconv_stderr)
        _require_nonempty(octree_path, f"D5-C1 {band_id} octree")
        if ambient_path.exists():
            raise SurfaceFluxEndpointReplicationError(
                f"D5-C1 fresh ambient cache was not absent before trace {trace_order_index}."
            )
        _run_required(runner, rtrace, rtrace_stderr)
        _require_nonempty(raw_rgb_path, f"D5-C1 {band_id} receiver output")
        receiver = _decode_receiver_rgb(
            raw_rgb_path,
            values_path,
            expected_count=D5_EXPECTED_RECEIVER_COUNT_PER_BAND,
            band_id=band_id,
            root=trace_root,
        )
    except SurfaceFluxCalibrationError as exc:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 Stage B trace failed for {source.job.job_id}/{band_id}: {exc}"
        ) from exc
    original_receiver = _mapping(
        original_band.get("receiver_values"),
        f"{source.job.job_id}/{band_id} original receiver",
    )
    a1_receiver_path = _original_receiver_path(source, original_receiver)
    _validate_original_receiver_file(a1_receiver_path, original_receiver)
    identical = _complete_files_identical(a1_receiver_path, values_path)
    workspace = f"{D5_C1_TRACES_DIRECTORY}/{band_order_index:02d}-{band_id}"
    repeated_receiver = _rooted_trace_artifact(workspace, receiver)
    return {
        "schema_id": D5_C1_TRACE_SCHEMA_ID,
        "schema_version": D5_C1_SCHEMA_VERSION,
        "experiment_id": D5_C1_EXPERIMENT_ID,
        "repetition_id": D5_C1_REPETITION_ID,
        "trace_order_index": trace_order_index,
        "job_id": source.job.job_id,
        "family": source.job.quality,
        "requested_reference_level_umol_m2_s": source.job.requested_level,
        "band_order_index": band_order_index,
        "band_id": band_id,
        "sentinel": trace_order_index == D5_C1_SENTINEL_TRACE_ORDER_INDEX,
        "status": "complete",
        "stage_a_trace_count": 0,
        "achieved_reference_R_umol_m2_s": source.achieved_reference,
        "resolved_total_source_radiance_amplitude": source.total_source_amplitude,
        "rendered_band_source_amplitude": source.band_source_amplitude,
        "amplitude_provenance": "authenticated D5-A1 Stage A final authority",
        "original": {
            "receiver_values": {
                **dict(original_receiver),
                "path": (
                    f"{D5_JOBS_DIRECTORY}/{source.job.job_id}/"
                    f"{original_receiver['path']}"
                ),
            },
            "commands": dict(_mapping(original_band.get("commands"), "commands")),
            "radiance_installation": dict(source.original_radiance_installation),
        },
        "repeated": {
            "workspace": workspace,
            "receiver_values": repeated_receiver,
            "commands": {
                "oconv": _command_payload(oconv, trace_root),
                "rtrace": _command_payload(rtrace, trace_root),
            },
            "radiance_installation": installation.to_dict(),
        },
        "cache": {
            **_cache_policy(),
            "workspace": workspace,
            "ambient_cache_path": f"{workspace}/scene.amb",
            "ambient_cache_absent_before_rtrace": True,
            "ambient_cache_present_after_rtrace": ambient_path.is_file(),
        },
        "byte_identical_to_original": identical,
        "comparison": {
            "complete_float64_bytes_compared": True,
            "original_sha256": original_receiver["sha256"],
            "repeated_sha256": receiver["sha256"],
            "sha256_identical": original_receiver["sha256"] == receiver["sha256"],
        },
        "independence_interpretation": (
            "nonidentical bytes do not prove an independently seeded sample"
            if not identical
            else "byte identity fails the mandatory nonidentical-repetition sentinel"
        ),
    }


def _original_receiver_path(
    source: _SourceJob, receiver: Mapping[str, object]
) -> Path:
    return source.a1_job_root / str(receiver["path"])


def _job_result(
    *,
    source: _SourceJob,
    job_order_index: int,
    configuration_sha256: str,
    traces: Sequence[Mapping[str, object]],
    status: str,
) -> dict[str, object]:
    return {
        "schema_id": D5_C1_JOB_SCHEMA_ID,
        "schema_version": D5_C1_SCHEMA_VERSION,
        "experiment_id": D5_C1_EXPERIMENT_ID,
        "repetition_id": D5_C1_REPETITION_ID,
        "configuration_sha256": configuration_sha256,
        "status": status,
        "job_order_index": job_order_index,
        "job": source.job.to_dict(),
        "a1_job_order_index": source.a1_order_index,
        "stage_a_trace_count": 0,
        "stage_b_trace_count": len(traces),
        "achieved_reference_R_umol_m2_s": source.achieved_reference,
        "resolved_total_source_radiance_amplitude": source.total_source_amplitude,
        "band_source_amplitude": source.band_source_amplitude,
        "amplitude_provenance": "authenticated D5-A1 Stage A final authority",
        "quality_option_identity": _quality_option_identity(source.job.quality),
        "ordered_traces": [dict(trace) for trace in traces],
        "sentinel_blocked": status == "blocked",
        "variance_analysis_suitable": False if status == "blocked" else None,
        "production_calibration_or_promotion_claim": False,
    }


def _validate_completed_job(
    root: Path,
    *,
    source: _SourceJob,
    job_order_index: int,
    configuration_sha256: str,
    installation: RadianceInstallation,
) -> dict[str, object]:
    if not root.is_dir() or root.is_symlink():
        raise SurfaceFluxEndpointReplicationError(
            f"committed D5-C1 job is missing or unsafe: {source.job.job_id}"
        )
    completion = _read_json_object(root / D5_C1_JOB_COMPLETION_NAME)
    status = completion.get("status")
    expected_trace_count = 1 if status == "blocked" else len(D5_BAND_ORDER)
    if (
        completion.get("schema_id") != D5_C1_JOB_COMPLETION_SCHEMA_ID
        or completion.get("schema_version") != D5_C1_SCHEMA_VERSION
        or completion.get("experiment_id") != D5_C1_EXPERIMENT_ID
        or completion.get("repetition_id") != D5_C1_REPETITION_ID
        or completion.get("configuration_sha256") != configuration_sha256
        or completion.get("job_order_index") != job_order_index
        or completion.get("job") != source.job.to_dict()
        or status not in {"complete", "blocked"}
        or (status == "blocked" and job_order_index != 0)
        or completion.get("trace_count") != expected_trace_count
    ):
        raise SurfaceFluxEndpointReplicationError(
            f"committed D5-C1 job identity is invalid: {source.job.job_id}"
        )
    actual_inventory = _inventory(root, excluded={D5_C1_JOB_COMPLETION_NAME})
    if completion.get("ordered_artifact_inventory") != actual_inventory:
        raise SurfaceFluxEndpointReplicationError(
            f"committed D5-C1 job inventory changed: {source.job.job_id}"
        )
    result_authority = _mapping(completion.get("job_result"), "job result")
    result_path = root / D5_C1_JOB_RESULT_NAME
    if (
        result_authority != _file_authority(root, result_path)
        or result_authority.get("path") != D5_C1_JOB_RESULT_NAME
    ):
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 job-result authority changed: {source.job.job_id}"
        )
    expected_completion = {
        "schema_id": D5_C1_JOB_COMPLETION_SCHEMA_ID,
        "schema_version": D5_C1_SCHEMA_VERSION,
        "experiment_id": D5_C1_EXPERIMENT_ID,
        "repetition_id": D5_C1_REPETITION_ID,
        "configuration_sha256": configuration_sha256,
        "job_order_index": job_order_index,
        "job": source.job.to_dict(),
        "status": status,
        "trace_count": expected_trace_count,
        "job_result": _file_authority(root, result_path),
        "ordered_artifact_inventory": actual_inventory,
        "atomic_commit_policy": (
            "all retained artifacts hashed before same-filesystem directory rename"
        ),
    }
    if completion != expected_completion:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 job completion does not reconstruct: {source.job.job_id}"
        )
    result = _read_json_object(result_path)
    if (
        result.get("schema_id") != D5_C1_JOB_SCHEMA_ID
        or result.get("schema_version") != D5_C1_SCHEMA_VERSION
        or result.get("experiment_id") != D5_C1_EXPERIMENT_ID
        or result.get("repetition_id") != D5_C1_REPETITION_ID
        or result.get("configuration_sha256") != configuration_sha256
        or result.get("status") != status
        or result.get("job_order_index") != job_order_index
        or result.get("job") != source.job.to_dict()
        or result.get("a1_job_order_index") != source.a1_order_index
        or result.get("stage_a_trace_count") != 0
        or result.get("stage_b_trace_count") != expected_trace_count
        or result.get("achieved_reference_R_umol_m2_s")
        != source.achieved_reference
        or result.get("resolved_total_source_radiance_amplitude")
        != source.total_source_amplitude
        or result.get("band_source_amplitude") != source.band_source_amplitude
        or result.get("quality_option_identity")
        != _quality_option_identity(source.job.quality)
        or result.get("production_calibration_or_promotion_claim") is not False
    ):
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 job result contract changed: {source.job.job_id}"
        )
    traces = result.get("ordered_traces")
    if not isinstance(traces, list) or len(traces) != expected_trace_count:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 trace matrix is incomplete: {source.job.job_id}"
        )
    for band_order_index, trace in enumerate(traces):
        _validate_trace(
            root,
            trace,
            source=source,
            job_order_index=job_order_index,
            band_order_index=band_order_index,
            installation=installation,
        )
    expected_result = _job_result(
        source=source,
        job_order_index=job_order_index,
        configuration_sha256=configuration_sha256,
        traces=traces,
        status=str(status),
    )
    if result != expected_result:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 job result does not reconstruct: {source.job.job_id}"
        )
    sentinel_identical = traces[0].get("byte_identical_to_original") is True
    if job_order_index == 0 and (status == "blocked") != sentinel_identical:
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 sentinel status and byte comparison disagree."
        )
    return result


def _validate_trace(
    job_root: Path,
    value: object,
    *,
    source: _SourceJob,
    job_order_index: int,
    band_order_index: int,
    installation: RadianceInstallation,
) -> None:
    trace = _mapping(value, "D5-C1 trace")
    expected_trace_fields = {
        "schema_id",
        "schema_version",
        "experiment_id",
        "repetition_id",
        "trace_order_index",
        "job_id",
        "family",
        "requested_reference_level_umol_m2_s",
        "band_order_index",
        "band_id",
        "sentinel",
        "status",
        "stage_a_trace_count",
        "achieved_reference_R_umol_m2_s",
        "resolved_total_source_radiance_amplitude",
        "rendered_band_source_amplitude",
        "amplitude_provenance",
        "original",
        "repeated",
        "cache",
        "byte_identical_to_original",
        "comparison",
        "independence_interpretation",
    }
    if set(trace) != expected_trace_fields:
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 trace field inventory is incompatible."
        )
    band_id = D5_BAND_ORDER[band_order_index]
    trace_order_index = job_order_index * len(D5_BAND_ORDER) + band_order_index
    original_band = source.bands[band_order_index]
    original_receiver = _mapping(
        original_band.get("receiver_values"), "original receiver"
    )
    repeated = _mapping(trace.get("repeated"), "repeated trace")
    receiver = _mapping(repeated.get("receiver_values"), "repeated receiver")
    workspace = f"{D5_C1_TRACES_DIRECTORY}/{band_order_index:02d}-{band_id}"
    expected_path = f"{workspace}/receiver-values.v1.f64le.bin"
    receiver_path = job_root / expected_path
    if (
        trace.get("schema_id") != D5_C1_TRACE_SCHEMA_ID
        or trace.get("schema_version") != D5_C1_SCHEMA_VERSION
        or trace.get("experiment_id") != D5_C1_EXPERIMENT_ID
        or trace.get("repetition_id") != D5_C1_REPETITION_ID
        or trace.get("trace_order_index") != trace_order_index
        or trace.get("job_id") != source.job.job_id
        or trace.get("family") != source.job.quality
        or trace.get("requested_reference_level_umol_m2_s")
        != source.job.requested_level
        or trace.get("band_order_index") != band_order_index
        or trace.get("band_id") != band_id
        or trace.get("sentinel")
        is not (trace_order_index == D5_C1_SENTINEL_TRACE_ORDER_INDEX)
        or trace.get("status") != "complete"
        or trace.get("stage_a_trace_count") != 0
        or trace.get("achieved_reference_R_umol_m2_s")
        != source.achieved_reference
        or trace.get("resolved_total_source_radiance_amplitude")
        != source.total_source_amplitude
        or trace.get("rendered_band_source_amplitude")
        != source.band_source_amplitude
        or trace.get("amplitude_provenance")
        != "authenticated D5-A1 Stage A final authority"
        or set(repeated)
        != {"workspace", "receiver_values", "commands", "radiance_installation"}
        or set(receiver)
        != {
            "role",
            "path",
            "media_type",
            "byte_length",
            "sha256",
            "row_count",
            "stride_bytes",
        }
        or repeated.get("workspace") != workspace
        or repeated.get("radiance_installation") != installation.to_dict()
        or receiver.get("path") != expected_path
        or receiver.get("row_count") != D5_EXPECTED_RECEIVER_COUNT_PER_BAND
        or receiver.get("stride_bytes") != 8
        or receiver.get("byte_length") != D5_EXPECTED_RECEIVER_BYTES_PER_BAND
        or not receiver_path.is_file()
        or receiver_path.is_symlink()
        or receiver_path.stat().st_size != receiver.get("byte_length")
        or _sha256_file(receiver_path) != receiver.get("sha256")
    ):
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 trace contract changed: {source.job.job_id}/{band_id}"
        )
    original = _mapping(trace.get("original"), "original trace")
    expected_original = {
        **dict(original_receiver),
        "path": (
            f"{D5_JOBS_DIRECTORY}/{source.job.job_id}/"
            f"{original_receiver['path']}"
        ),
    }
    if (
        set(original) != {"receiver_values", "commands", "radiance_installation"}
        or original.get("receiver_values") != expected_original
        or original.get("commands") != original_band.get("commands")
        or original.get("radiance_installation")
        != repeated.get("radiance_installation")
    ):
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 original pairing changed: {source.job.job_id}/{band_id}"
        )
    commands = _mapping(repeated.get("commands"), "repeated commands")
    _validate_repeated_commands(
        commands,
        family=source.job.quality,
        threads=source.threads,
        installation=installation,
    )
    cache = _mapping(trace.get("cache"), "trace cache")
    expected_cache_fields = set(_cache_policy()) | {
        "workspace",
        "ambient_cache_path",
        "ambient_cache_absent_before_rtrace",
        "ambient_cache_present_after_rtrace",
    }
    if (
        set(cache) != expected_cache_fields
        or cache.get("policy") != _cache_policy()["policy"]
        or cache.get("a1_cache_reused") is not False
        or cache.get("trace_local_workspace") is not True
        or cache.get("ambient_cache_initial_state") != "absent"
        or cache.get("workspace") != workspace
        or cache.get("ambient_cache_path") != f"{workspace}/scene.amb"
        or cache.get("ambient_cache_absent_before_rtrace") is not True
    ):
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 fresh-cache evidence changed: {source.job.job_id}/{band_id}"
        )
    original_path = _original_receiver_path(source, original_receiver)
    _validate_original_receiver_file(original_path, original_receiver)
    identical = _complete_files_identical(original_path, receiver_path)
    comparison = _mapping(trace.get("comparison"), "trace comparison")
    if trace.get("byte_identical_to_original") is not identical:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 byte comparison changed: {source.job.job_id}/{band_id}"
        )
    if comparison != {
        "complete_float64_bytes_compared": True,
        "original_sha256": original_receiver["sha256"],
        "repeated_sha256": receiver["sha256"],
        "sha256_identical": original_receiver["sha256"] == receiver["sha256"],
    }:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 comparison authority changed: {source.job.job_id}/{band_id}"
        )
    expected_interpretation = (
        "byte identity fails the mandatory nonidentical-repetition sentinel"
        if identical
        else "nonidentical bytes do not prove an independently seeded sample"
    )
    if trace.get("independence_interpretation") != expected_interpretation:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 independence limitation changed: {source.job.job_id}/{band_id}"
        )


def _validate_repeated_commands(
    commands: Mapping[str, object],
    *,
    family: str,
    threads: object,
    installation: RadianceInstallation,
) -> None:
    if set(commands) != {"oconv", "rtrace"}:
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 repeated command field inventory is incompatible."
        )
    oconv = _mapping(commands.get("oconv"), "repeated oconv command")
    rtrace = _mapping(commands.get("rtrace"), "repeated rtrace command")
    command_fields = {
        "label",
        "argv",
        "stdin_path",
        "stdout_path",
        "cwd",
        "environment",
        "shell",
    }
    if isinstance(threads, bool) or not isinstance(threads, int) or threads <= 0:
        raise SurfaceFluxEndpointReplicationError("D5-C1 thread authority is invalid.")
    expected_options = radiance_options(family) + ["-af", "scene.amb"]
    expected_rtrace = [
        str(installation.rtrace.path),
        "-h",
        "-I+",
        "-n",
        str(threads),
        *expected_options,
        "scene.oct",
    ]
    expected_oconv = [
        str(installation.oconv.path),
        "-f",
        "scene-boundary.rad",
        "source.rad",
        "leaf-material.rad",
        "scene/plant-geometry.rad",
    ]
    if (
        set(oconv) != command_fields
        or set(rtrace) != command_fields
        or oconv.get("argv") != expected_oconv
        or oconv.get("stdin_path") is not None
        or oconv.get("stdout_path") != "scene.oct"
        or oconv.get("cwd") != "."
        or oconv.get("environment") != {}
        or oconv.get("shell") is not False
        or rtrace.get("argv") != expected_rtrace
        or rtrace.get("stdin_path") != "scene/receivers.pts"
        or rtrace.get("stdout_path") != "receiver.rgb"
        or rtrace.get("cwd") != "."
        or rtrace.get("environment") != {}
        or rtrace.get("shell") is not False
        or "-u" in expected_rtrace
        or "-u+" in expected_rtrace
        or "-u-" in expected_rtrace
    ):
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 repeated {family} command options or process settings changed."
        )


def _validate_original_stage_b_commands(
    band: Mapping[str, object],
    *,
    job: _Job,
    band_id: str,
    band_order_index: int,
    threads: int,
    installation: Mapping[str, object],
) -> None:
    commands = _mapping(band.get("commands"), "original Stage B commands")
    if set(commands) != {"oconv", "rtrace"}:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 original command inventory changed: {job.job_id}/{band_id}"
        )
    oconv = _mapping(commands.get("oconv"), "original oconv command")
    rtrace = _mapping(commands.get("rtrace"), "original rtrace command")
    command_fields = {
        "label",
        "argv",
        "stdin_path",
        "stdout_path",
        "cwd",
        "environment",
        "shell",
    }
    oconv_identity = _mapping(installation.get("oconv"), "original oconv identity")
    rtrace_identity = _mapping(installation.get("rtrace"), "original rtrace identity")
    band_root = f"bands/{band_order_index:02d}-{band_id}"
    expected_oconv = [
        oconv_identity.get("path"),
        "-f",
        "scene-boundary.rad",
        f"{band_root}/source.rad",
        f"{band_root}/leaf-material.rad",
        "scene/plant-geometry.rad",
    ]
    expected_rtrace = [
        rtrace_identity.get("path"),
        "-h",
        "-I+",
        "-n",
        str(threads),
        *radiance_options(job.quality),
        "-af",
        f"{band_root}/scene.amb",
        f"{band_root}/scene.oct",
    ]
    if (
        set(oconv) != command_fields
        or set(rtrace) != command_fields
        or oconv.get("argv") != expected_oconv
        or oconv.get("stdin_path") is not None
        or oconv.get("stdout_path") != f"{band_root}/scene.oct"
        or oconv.get("cwd") != "."
        or oconv.get("environment") != {}
        or oconv.get("shell") is not False
        or rtrace.get("argv") != expected_rtrace
        or rtrace.get("stdin_path") != "scene/receivers.pts"
        or rtrace.get("stdout_path") != f"{band_root}/receiver.rgb"
        or rtrace.get("cwd") != "."
        or rtrace.get("environment") != {}
        or rtrace.get("shell") is not False
    ):
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 original process/options changed: {job.job_id}/{band_id}"
        )


def _build_outcome(
    output: Path,
    *,
    identity: Mapping[str, object],
    authenticated: _AuthenticatedInputs,
    results: Sequence[Mapping[str, object]],
    created_at_utc: str,
    status: str,
) -> dict[str, object]:
    executed = sum(int(result["stage_b_trace_count"]) for result in results)
    blocked = status == "blocked"
    expected_executed = 1 if blocked else D5_C1_STAGE_B_TRACE_COUNT
    if executed != expected_executed:
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 outcome trace count disagrees with sentinel status."
        )
    inventory = _inventory(output, excluded={D5_C1_OUTCOME_NAME})
    return {
        "schema_id": D5_C1_OUTCOME_SCHEMA_ID,
        "schema_version": D5_C1_SCHEMA_VERSION,
        "experiment_id": D5_C1_EXPERIMENT_ID,
        "repetition_id": D5_C1_REPETITION_ID,
        "configuration_sha256": identity["configuration_sha256"],
        "created_at_utc": _validated_timestamp(created_at_utc),
        "status": status,
        "execution_complete": not blocked,
        "blocked_reason": (
            "quality-250 blue repeat was byte-identical to the authenticated A1 artifact; nonidentical repetition was not demonstrated"
            if blocked
            else None
        ),
        "families": list(D5_C1_FAMILY_ORDER),
        "job_order": [source.job.job_id for source in authenticated.source_jobs],
        "band_order": list(D5_BAND_ORDER),
        "stage_a_trace_count": 0,
        "stage_b_trace_count_planned": D5_C1_STAGE_B_TRACE_COUNT,
        "stage_b_trace_count_executed": executed,
        "stage_b_trace_count_remaining": D5_C1_STAGE_B_TRACE_COUNT - executed,
        "sentinel": {
            "job_id": D5_C1_SENTINEL_JOB_ID,
            "band_id": D5_C1_SENTINEL_BAND_ID,
            "trace_order_index": D5_C1_SENTINEL_TRACE_ORDER_INDEX,
            "byte_identical_to_original": results[0]["ordered_traces"][0][
                "byte_identical_to_original"
            ],
            "passed": not blocked,
        },
        "source_authorities": _source_authorities(authenticated),
        "ordered_jobs": [
            {
                "job_order_index": int(result["job_order_index"]),
                "job_id": result["job"]["job_id"],
                "status": result["status"],
                "trace_count": result["stage_b_trace_count"],
                "job_result": _file_authority(
                    output,
                    output
                    / D5_C1_JOBS_DIRECTORY
                    / str(result["job"]["job_id"])
                    / D5_C1_JOB_RESULT_NAME,
                ),
                "job_completion": _file_authority(
                    output,
                    output
                    / D5_C1_JOBS_DIRECTORY
                    / str(result["job"]["job_id"])
                    / D5_C1_JOB_COMPLETION_NAME,
                ),
            }
            for result in results
        ],
        "ordered_artifact_inventory": inventory,
        "cache_policy": _cache_policy(),
        "receiver_contract": _receiver_contract(),
        "scope": _scope_contract(),
        "variance_analysis_suitable": False if blocked else None,
        "variance_analysis_suitability_claimed": False,
        "independently_seeded_sample_claimed": False,
        "nonidentical_bytes_prove_independent_seed": False,
        "production_calibration_resource_generated": False,
        "promotion_claimed": False,
        "a1_input_modified": False,
        "a2_input_modified": False,
    }


def _validate_outcome(
    output: Path,
    *,
    identity: Mapping[str, object],
    authenticated: _AuthenticatedInputs,
) -> dict[str, object]:
    outcome_path = output / D5_C1_OUTCOME_NAME
    outcome = _read_json_object(outcome_path)
    status = outcome.get("status")
    if status not in {"complete", "blocked"}:
        raise SurfaceFluxEndpointReplicationError("D5-C1 outcome status is invalid.")
    state = _read_json_object(output / D5_C1_CONFIGURATION_NAME)
    job_count = 1 if status == "blocked" else D5_C1_JOB_COUNT
    results = []
    installation = _installation_from_identity(identity)
    for job_order_index, source in enumerate(authenticated.source_jobs[:job_count]):
        results.append(
            _validate_completed_job(
                output / D5_C1_JOBS_DIRECTORY / source.job.job_id,
                source=source,
                job_order_index=job_order_index,
                configuration_sha256=str(identity["configuration_sha256"]),
                installation=installation,
            )
        )
    expected = _build_outcome(
        output,
        identity=identity,
        authenticated=authenticated,
        results=results,
        created_at_utc=str(state["created_at_utc"]),
        status=str(status),
    )
    if outcome != expected:
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 outcome does not reconstruct from authenticated committed jobs."
        )
    return outcome


def _installation_from_identity(identity: Mapping[str, object]) -> RadianceInstallation:
    from fspm_optics.radiance.versioning import RadianceExecutableVersion

    value = _mapping(
        identity.get("repeated_radiance_installation"),
        "repeated Radiance installation",
    )
    records = {}
    for name in ("oconv", "rtrace"):
        record = _mapping(value.get(name), f"{name} identity")
        if record.get("name") != name or not isinstance(record.get("path"), str):
            raise SurfaceFluxEndpointReplicationError(
                f"stored D5-C1 {name} identity is invalid."
            )
        records[name] = RadianceExecutableVersion(
            name=name,
            path=Path(str(record["path"])),
            version_text=(
                None
                if record.get("version_text") is None
                else str(record["version_text"])
            ),
        )
    return RadianceInstallation(oconv=records["oconv"], rtrace=records["rtrace"])


def _source_authorities(authenticated: _AuthenticatedInputs) -> dict[str, object]:
    return {
        "d5_a1": {
            "experiment_id": D5_EXPERIMENT_ID,
            "input_directory": str(authenticated.a1.input_root),
            "completion_path": D5_COMPLETION_NAME,
            "completion_sha256": authenticated.a1.completion_sha256,
            "configuration_sha256": authenticated.a1.completion[
                "configuration_sha256"
            ],
            "status": "complete",
        },
        "d5_a2": {
            "analysis_id": D5_A2_ANALYSIS_ID,
            "input_directory": str(authenticated.a2.root),
            "report_path": D5_A2_ANALYSIS_NAME,
            "report_sha256": authenticated.a2.report_sha256,
            "completion_path": D5_A2_COMPLETION_NAME,
            "completion_sha256": authenticated.a2.completion_sha256,
            "status": "complete",
            "promotion_eligible": authenticated.a2.completion[
                "promotion_eligible"
            ],
        },
    }


def _receiver_contract() -> dict[str, object]:
    return {
        "sampling_profile_id": D5_SAMPLING_PROFILE_ID,
        "topology_sha256": D5_TOPOLOGY_SHA256,
        "receivers_sha256": D5_RECEIVERS_SHA256,
        "plant_count": D5_EXPECTED_PLANT_COUNT,
        "patches_per_plant": D5_PATCHES_PER_PLANT,
        "receiver_count_per_band": D5_EXPECTED_RECEIVER_COUNT_PER_BAND,
        "receiver_byte_length_per_band": D5_EXPECTED_RECEIVER_BYTES_PER_BAND,
        "component_type": "IEEE-754 binary64",
        "byte_order": "little-endian",
        "stride_bytes": 8,
        "canonical_order": D5_C1_CANONICAL_RECEIVER_ORDER,
    }


def _cache_policy() -> dict[str, object]:
    return {
        "policy": "fresh trace-local workspace and initially absent ambient cache",
        "trace_local_workspace": True,
        "ambient_cache_initial_state": "absent",
        "ambient_cache_shared_across_traces": False,
        "a1_cache_reused": False,
    }


def _scope_contract() -> dict[str, object]:
    return {
        "quality_present": True,
        "standard_present": True,
        "rigorous_present": False,
        "direct_present": False,
        "far_red_present": False,
        "proposed_present": False,
        "conventional_present": False,
        "endpoint_family_roles": {
            "standard": (
                "exact Standard coefficients; future Direct display-only proxy "
                "under study"
            ),
            "quality": (
                "exact Quality coefficients; future Rigorous display-only proxy "
                "under study"
            ),
        },
        "future_display_only_proxy_under_study": {
            "direct": "standard",
            "rigorous": "quality",
        },
        "display_proxy_mapping_implemented": False,
        "raw_transport_changed": False,
        "coefficient_refit_performed": False,
        "production_resource_generated": False,
        "promotion_performed": False,
    }


def _a1_achieved_reference(result: Mapping[str, object], job_id: str) -> float:
    reference = _mapping(result.get("reference"), f"{job_id} reference")
    resolution = _mapping(
        reference.get("amplitude_resolution"), f"{job_id} amplitude resolution"
    )
    final = _mapping(resolution.get("final"), f"{job_id} final Stage A")
    metrics = _mapping(final.get("metrics"), f"{job_id} final metrics")
    return _positive_finite(
        metrics.get("mean_ppfd_umol_m2_s"), f"{job_id} achieved reference"
    )


def _a1_bands(
    result: Mapping[str, object], job_id: str
) -> tuple[Mapping[str, object], ...]:
    transport = _mapping(result.get("plant_transport"), f"{job_id} transport")
    bands = transport.get("bands")
    if not isinstance(bands, list) or len(bands) != len(D5_BAND_ORDER):
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 A1 band matrix is incomplete: {job_id}"
        )
    values = tuple(_mapping(value, f"{job_id} band") for value in bands)
    if tuple(value.get("band_id") for value in values) != D5_BAND_ORDER:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 A1 band order changed: {job_id}"
        )
    return values


def _a2_authority(value: object, path: str, label: str) -> Mapping[str, object]:
    record = _mapping(value, label)
    if (
        set(record) != {"path", "byte_length", "sha256"}
        or record.get("path") != path
        or isinstance(record.get("byte_length"), bool)
        or not isinstance(record.get("byte_length"), int)
        or int(record["byte_length"]) <= 0
        or not _valid_sha256(record.get("sha256"))
    ):
        raise SurfaceFluxEndpointReplicationError(f"{label} authority is invalid.")
    return record


def _authorized_json(
    root: Path, record: Mapping[str, object], label: str
) -> dict[str, object]:
    relative = str(record["path"])
    posix = PurePosixPath(relative)
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        raise SurfaceFluxEndpointReplicationError(f"{label} path is unsafe.")
    path = root.joinpath(*posix.parts)
    if (
        not path.is_file()
        or path.is_symlink()
        or not path.resolve(strict=True).is_relative_to(root.resolve(strict=True))
        or path.stat().st_size != record["byte_length"]
        or _sha256_file(path) != record["sha256"]
    ):
        raise SurfaceFluxEndpointReplicationError(f"{label} failed authentication.")
    return _read_json_object(path)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SurfaceFluxEndpointReplicationError(f"{label} must be an object.")
    return value


def _positive_finite(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise SurfaceFluxEndpointReplicationError(f"{label} must be finite and positive.")
    return float(value)


def _rooted_trace_artifact(
    workspace: str, artifact: Mapping[str, object]
) -> dict[str, object]:
    return {**dict(artifact), "path": f"{workspace}/{artifact['path']}"}


def _file_authority(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix(),
        "byte_length": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _validate_original_receiver_file(
    path: Path, receiver: Mapping[str, object]
) -> None:
    if (
        not path.is_file()
        or path.is_symlink()
        or path.stat().st_size != receiver.get("byte_length")
        or _sha256_file(path) != receiver.get("sha256")
    ):
        raise SurfaceFluxEndpointReplicationError(
            "authenticated D5-A1 receiver changed during D5-C1 execution."
        )


def _complete_files_identical(left: Path, right: Path) -> bool:
    if (
        not left.is_file()
        or left.is_symlink()
        or not right.is_file()
        or right.is_symlink()
        or left.stat().st_size != right.stat().st_size
    ):
        return False
    with left.open("rb") as left_handle, right.open("rb") as right_handle:
        while True:
            left_chunk = left_handle.read(1024 * 1024)
            right_chunk = right_handle.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _validate_jobs_directory(root: Path) -> None:
    expected = {job.job_id for job in endpoint_replication_jobs()}
    unexpected = sorted(
        path.name
        for path in root.iterdir()
        if path.name not in expected
        and not (path.name.startswith(".") and path.name.endswith(".tmp"))
    )
    if unexpected:
        raise SurfaceFluxEndpointReplicationError(
            f"D5-C1 jobs directory contains unknown entries: {unexpected!r}."
        )


def _validate_committed_job_prefix(root: Path) -> None:
    ordered = [job.job_id for job in endpoint_replication_jobs()]
    committed = {
        path.name
        for path in root.iterdir()
        if not (path.name.startswith(".") and path.name.endswith(".tmp"))
    }
    expected = set(ordered[: len(committed)])
    if committed != expected:
        raise SurfaceFluxEndpointReplicationError(
            "D5-C1 committed jobs are not a contiguous Quality-first prefix."
        )


def _level_token(value: float) -> str:
    return format(value, ".12g").replace(".", "p")


def _require_config(config: SurfaceFluxEndpointReplicationConfig) -> None:
    if not isinstance(config, SurfaceFluxEndpointReplicationConfig):
        raise TypeError("config must be SurfaceFluxEndpointReplicationConfig.")


def format_surface_flux_endpoint_replication_json(
    payload: Mapping[str, object],
) -> str:
    """Serialize a C1 plan, job, or outcome as strict deterministic JSON."""

    return _pretty_json(payload)


__all__ = [
    "D5_C1_DEFAULT_A1_INPUT_DIRECTORY",
    "D5_C1_DEFAULT_A2_INPUT_DIRECTORY",
    "D5_C1_DEFAULT_OUTPUT_DIRECTORY",
    "D5_C1_EXPERIMENT_ID",
    "D5_C1_FAMILY_ORDER",
    "D5_C1_OUTCOME_NAME",
    "D5_C1_STAGE_A_TRACE_COUNT",
    "D5_C1_STAGE_B_TRACE_COUNT",
    "SurfaceFluxEndpointReplicationConfig",
    "SurfaceFluxEndpointReplicationError",
    "SurfaceFluxEndpointReplicationPublication",
    "build_surface_flux_endpoint_replication_plan",
    "endpoint_replication_jobs",
    "format_surface_flux_endpoint_replication_json",
    "run_surface_flux_endpoint_replication",
]
