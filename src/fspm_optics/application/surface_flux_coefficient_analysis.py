"""Deterministic Phase 27G-D5-A2 analysis of a completed D5-A1 v3 sweep.

The analyzer is intentionally non-Radiance.  It first authenticates the whole
immutable D5-A1 directory, then streams its four-band receiver artifacts
through the authoritative Phase 27G-C scientific kernel.  Candidate
coefficients and neutral-distribution evidence are published to a separate
directory; no production calibration resource is generated here.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile
from typing import Mapping, Sequence

from fspm_optics.application.fspm_science import (
    FspmScientificAggregationError,
    ParPatchSurfaceLight,
    stream_juvenile_par_surface_light,
)
from fspm_optics.application.surface_flux_calibration import (
    CalibrationCriteria,
    SurfaceFluxCalibrationError,
    _hash_json,
    _inventory,
    _pretty_json,
    _r_squared,
    _read_json_object,
    _sha256_file,
    _through_origin_slope,
    _valid_sha256,
    _validated_timestamp,
)
from fspm_optics.application.surface_flux_recalibration import (
    D5_BAND_ORDER,
    D5_COMPLETION_NAME,
    D5_COMPLETION_SCHEMA_ID,
    D5_COMPLETION_SCHEMA_VERSION,
    D5_CONFIGURATION_NAME,
    D5_CONFIGURATION_SCHEMA_ID,
    D5_CONFIGURATION_SCHEMA_VERSION,
    D5_DEFERRED_DISPLAY_QUALITY_MAPPING,
    D5_EXPECTED_PLANT_COUNT,
    D5_EXPECTED_RECEIVER_BYTES_PER_BAND,
    D5_EXPECTED_RECEIVER_COUNT_PER_BAND,
    D5_EXPERIMENT_ID,
    D5_IDENTITY_VERSION,
    D5_JOB_COUNT,
    D5_JOBS_DIRECTORY,
    D5_PATCHES_PER_PLANT,
    D5_QUALITY_ORDER,
    D5_RECEIVERS_SHA256,
    D5_REFERENCE_LEVELS_UMOL_M2_S,
    D5_SAMPLING_PROFILE_ID,
    D5_STAGE_B_ARTIFACT_COUNT,
    D5_TOPOLOGY_SHA256,
    SurfaceFluxRecalibrationConfig,
    _build_completion_manifest,
    _build_scientific_inputs,
    _d5_scene_identity,
    _quality_option_identities,
    _validate_completed_job,
    _validate_completion_manifest,
    build_surface_flux_recalibration_plan,
    canonical_neutral_source_definition,
    recalibration_jobs,
)
from fspm_optics.plants.multi_scene import JuvenileScientificScene
from fspm_optics.transport.basis.atomic import atomic_write_bytes, atomic_write_text


D5_A2_ANALYSIS_ID = "phase27g-d5-a2-coefficient-analysis-v1"
D5_A2_ANALYSIS_SCHEMA_ID = "fspm-optics.surface-flux-coefficient-analysis"
D5_A2_ANALYSIS_SCHEMA_VERSION = 1
D5_A2_COEFFICIENT_SCHEMA_ID = (
    "fspm-optics.surface-flux-coefficient-payload-manifest"
)
D5_A2_COEFFICIENT_SCHEMA_VERSION = 1
D5_A2_NEUTRAL_SCHEMA_ID = (
    "fspm-optics.surface-flux-normalized-neutral-evidence"
)
D5_A2_NEUTRAL_SCHEMA_VERSION = 1
D5_A2_COMPLETION_SCHEMA_ID = (
    "fspm-optics.surface-flux-coefficient-analysis-completion"
)
D5_A2_COMPLETION_SCHEMA_VERSION = 1

D5_A2_ANALYSIS_NAME = "surface-flux-coefficient-analysis.v1.json"
D5_A2_COEFFICIENT_MANIFEST_NAME = "coefficient-payload-manifest.v1.json"
D5_A2_NEUTRAL_MANIFEST_NAME = "normalized-neutral-evidence.v1.json"
D5_A2_COMPLETION_NAME = "surface-flux-coefficient-analysis-completion.v1.json"
D5_A2_COEFFICIENT_DIRECTORY = "coefficients"
D5_A2_NEUTRAL_DIRECTORY = "normalized-neutral-evidence"
D5_A2_COMBINED_COEFFICIENT_NAME = "all-coefficients.v1.f64le.bin"

COMPONENT_TYPE = "IEEE-754 binary64"
BYTE_ORDER = "little-endian"
FLOAT64_STRIDE_BYTES = 8
COEFFICIENT_UNITS = "dimensionless q/R"
NORMALIZED_UNITS = "dimensionless u"
CANONICAL_QNAN_BITS = 0x7FF8000000000000
CANONICAL_QNAN_BYTES = struct.pack("<Q", CANONICAL_QNAN_BITS)

# This order is a publication contract.  It is deliberately side/metric
# interleaved exactly as requested, rather than alphabetically sorted.
COEFFICIENT_CHANNEL_ORDER = (
    ("front", "incident", "front_incident"),
    ("front", "absorbed", "front_absorbed"),
    ("back", "incident", "back_incident"),
    ("back", "absorbed", "back_absorbed"),
)
COEFFICIENT_ARRAY_COUNT = len(D5_QUALITY_ORDER) * len(COEFFICIENT_CHANNEL_ORDER)
COEFFICIENT_VALUE_COUNT = COEFFICIENT_ARRAY_COUNT * D5_PATCHES_PER_PLANT
COEFFICIENT_ARRAY_BYTES = D5_PATCHES_PER_PLANT * FLOAT64_STRIDE_BYTES
COMBINED_COEFFICIENT_BYTES = COEFFICIENT_VALUE_COUNT * FLOAT64_STRIDE_BYTES
NEUTRAL_ARRAY_VALUE_COUNT = D5_EXPECTED_PLANT_COUNT * D5_PATCHES_PER_PLANT
NEUTRAL_ARRAY_BYTES = NEUTRAL_ARRAY_VALUE_COUNT * FLOAT64_STRIDE_BYTES


class SurfaceFluxCoefficientAnalysisError(RuntimeError):
    """A D5-A2 structural, authentication, or publication failure."""


@dataclass(frozen=True, slots=True)
class SurfaceFluxCoefficientAnalysisPublication:
    output_directory: Path
    report_path: Path
    report: Mapping[str, object]
    completion_path: Path
    completion: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _AuthenticatedSweep:
    input_root: Path
    configuration: SurfaceFluxRecalibrationConfig
    configuration_identity: Mapping[str, object]
    completion: Mapping[str, object]
    completion_sha256: str
    scene: JuvenileScientificScene
    results: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class _DerivedJob:
    job_id: str
    family: str
    requested_level: float
    achieved_reference: float
    values_by_channel: Mapping[str, tuple[float, ...]]
    stage_c_validation: Mapping[str, object]
    source_receiver_sha256_by_band: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class _CoefficientChannel:
    family: str
    side: str
    metric: str
    channel: str
    values: tuple[float, ...]
    patch_fits: tuple[Mapping[str, object], ...]
    distribution: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _BinaryPayload:
    relative_path: str
    data: bytes
    record: Mapping[str, object]


def analyze_surface_flux_coefficients(
    *,
    input_directory: str | Path,
    output_directory: str | Path,
) -> SurfaceFluxCoefficientAnalysisPublication:
    """Authenticate and analyze one completed fixed D5-A1 v3 sweep.

    The input tree is only opened for reading.  The output must be a distinct,
    absent path and is committed by one same-filesystem directory rename.
    """

    input_root, output_root = _validate_locations(
        Path(input_directory), Path(output_directory)
    )
    authenticated = _authenticate_completed_sweep(input_root)
    derived_jobs = _derive_all_jobs(authenticated)
    criteria = authenticated.configuration.criteria
    channels, failure_summary = _analyze_channels(
        derived_jobs,
        criteria=criteria,
    )
    coefficient_payloads, coefficient_manifest = _coefficient_payloads(
        channels,
        source_completion_sha256=authenticated.completion_sha256,
    )
    neutral_payloads, neutral_manifest = _neutral_payloads(
        derived_jobs,
        channels,
        coefficient_payloads=coefficient_payloads,
        source_completion_sha256=authenticated.completion_sha256,
    )
    promotion_eligible = failure_summary["failed_patch_fit_count"] == 0
    report = _analysis_report(
        authenticated,
        derived_jobs,
        channels,
        coefficient_manifest=coefficient_manifest,
        neutral_manifest=neutral_manifest,
        criteria=criteria,
        failure_summary=failure_summary,
        promotion_eligible=promotion_eligible,
    )
    return _publish(
        output_root,
        authenticated=authenticated,
        coefficient_payloads=coefficient_payloads,
        neutral_payloads=neutral_payloads,
        coefficient_manifest=coefficient_manifest,
        neutral_manifest=neutral_manifest,
        report=report,
        promotion_eligible=promotion_eligible,
    )


def _validate_locations(input_path: Path, output_path: Path) -> tuple[Path, Path]:
    expanded_input = input_path.expanduser()
    if expanded_input.is_symlink():
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 input must be a real, non-symbolic-link directory."
        )
    try:
        input_root = expanded_input.resolve(strict=True)
    except OSError as exc:
        raise SurfaceFluxCoefficientAnalysisError(
            f"D5-A1 input directory does not exist: {input_path}"
        ) from exc
    if not input_root.is_dir() or input_root.is_symlink():
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 input must be a real, non-symbolic-link directory."
        )
    output_root = output_path.expanduser().resolve()
    if (
        output_root == input_root
        or output_root.is_relative_to(input_root)
        or input_root.is_relative_to(output_root)
    ):
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A2 output must be separate from and non-overlapping with the "
            "read-only D5-A1 input directory."
        )
    if output_root.exists():
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A2 output path must be absent; existing output is never overwritten."
        )
    return input_root, output_root


def _authenticate_completed_sweep(input_root: Path) -> _AuthenticatedSweep:
    """Authenticate all structure and bytes before Stage B values are decoded."""

    expected_root_entries = {
        D5_CONFIGURATION_NAME,
        D5_JOBS_DIRECTORY,
        D5_COMPLETION_NAME,
    }
    observed_root_entries = {path.name for path in input_root.iterdir()}
    if observed_root_entries != expected_root_entries:
        raise SurfaceFluxCoefficientAnalysisError(
            "completed D5-A1 root inventory is missing, extra, or substituted: "
            f"{sorted(observed_root_entries)!r}"
        )
    try:
        state = _read_json_object(input_root / D5_CONFIGURATION_NAME)
        completion = _read_json_object(input_root / D5_COMPLETION_NAME)
        configuration, identity, scene = _validate_configuration(
            input_root, state
        )
        configuration_sha256 = str(identity["configuration_sha256"])
        _validate_completion_identity(completion, configuration_sha256)
        actual_inventory = _inventory(
            input_root, excluded={D5_COMPLETION_NAME}
        )
        if completion.get("ordered_artifact_inventory") != actual_inventory:
            raise SurfaceFluxCoefficientAnalysisError(
                "D5-A1 completion inventory is unordered, incomplete, extra, "
                "or not byte-for-byte authenticated."
            )
        _validate_completion_manifest(
            input_root / D5_COMPLETION_NAME,
            output=input_root,
            configuration_sha256=configuration_sha256,
        )

        results = tuple(
            _validate_completed_job(
                input_root / D5_JOBS_DIRECTORY / job.job_id,
                job=job,
                order_index=order_index,
                configuration_sha256=configuration_sha256,
                scene=scene,
            )
            for order_index, job in enumerate(recalibration_jobs())
        )
        _validate_stage_evidence(results, configuration)
        reconstructed = _build_completion_manifest(
            config=configuration,
            identity=identity,
            plan={"plan_sha256": identity["plan_sha256"]},
            created_at_utc=str(state["created_at_utc"]),
            results=results,
        )
        if reconstructed != completion:
            raise SurfaceFluxCoefficientAnalysisError(
                "D5-A1 completion evidence does not exactly reconstruct from "
                "the authenticated configuration and six ordered jobs."
            )
    except SurfaceFluxCoefficientAnalysisError:
        raise
    except (OSError, ValueError, TypeError, SurfaceFluxCalibrationError) as exc:
        raise SurfaceFluxCoefficientAnalysisError(
            f"D5-A1 structural authentication failed: {exc}"
        ) from exc

    return _AuthenticatedSweep(
        input_root=input_root,
        configuration=configuration,
        configuration_identity=identity,
        completion=completion,
        completion_sha256=_sha256_file(input_root / D5_COMPLETION_NAME),
        scene=scene,
        results=results,
    )


def _validate_configuration(
    input_root: Path,
    state: Mapping[str, object],
) -> tuple[
    SurfaceFluxRecalibrationConfig,
    Mapping[str, object],
    JuvenileScientificScene,
]:
    if (
        state.get("schema_id") != D5_CONFIGURATION_SCHEMA_ID
        or state.get("schema_version") != D5_CONFIGURATION_SCHEMA_VERSION
        or state.get("experiment_id") != D5_EXPERIMENT_ID
    ):
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 configuration is not the required v3 schema and experiment."
        )
    _validated_timestamp(state.get("created_at_utc"))
    identity = state.get("identity")
    initial_cli = state.get("initial_cli_configuration")
    if not isinstance(identity, Mapping) or not isinstance(initial_cli, Mapping):
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 configuration identity or initial CLI evidence is missing."
        )
    claimed_configuration_sha256 = identity.get("configuration_sha256")
    identity_without_hash = {
        key: value for key, value in identity.items() if key != "configuration_sha256"
    }
    if (
        not _valid_sha256(claimed_configuration_sha256)
        or _hash_json(identity_without_hash) != claimed_configuration_sha256
        or identity.get("schema_id") != D5_CONFIGURATION_SCHEMA_ID
        or identity.get("schema_version") != D5_CONFIGURATION_SCHEMA_VERSION
        or identity.get("experiment_id") != D5_EXPERIMENT_ID
        or identity.get("d5_identity_version") != D5_IDENTITY_VERSION
    ):
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 configuration hash or fixed identity is invalid."
        )

    scientific = identity.get("scientific_configuration")
    if not isinstance(scientific, Mapping):
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 scientific configuration is missing."
        )
    threads = scientific.get("threads")
    commands = scientific.get("radiance_commands")
    if (
        isinstance(threads, bool)
        or not isinstance(threads, int)
        or threads <= 0
        or not isinstance(commands, Mapping)
        or not isinstance(commands.get("oconv"), str)
        or not commands.get("oconv")
        or not isinstance(commands.get("rtrace"), str)
        or not commands.get("rtrace")
    ):
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 execution-control identity is invalid."
        )
    configuration = SurfaceFluxRecalibrationConfig(
        output_directory=input_root,
        threads=threads,
        oconv_command=str(commands["oconv"]),
        rtrace_command=str(commands["rtrace"]),
    )
    if scientific != configuration.scientific_payload():
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 fixed scientific configuration changed."
        )
    expected_cli = configuration.cli_payload()
    if set(initial_cli) != set(expected_cli):
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 initial CLI configuration fields changed."
        )
    for key, value in expected_cli.items():
        if key == "output_directory":
            if not isinstance(initial_cli.get(key), str) or not initial_cli.get(key):
                raise SurfaceFluxCoefficientAnalysisError(
                    "D5-A1 original output-directory evidence is invalid."
                )
        elif initial_cli.get(key) != value:
            raise SurfaceFluxCoefficientAnalysisError(
                f"D5-A1 initial CLI configuration changed at {key!r}."
            )

    scene, material_plan = _build_scientific_inputs(configuration)
    expected_plan = build_surface_flux_recalibration_plan(configuration)
    expected_scene_identity = _d5_scene_identity(
        configuration, scene, material_plan
    )
    if (
        identity.get("scientific_configuration") != scientific
        or identity.get("neutral_source")
        != canonical_neutral_source_definition()
        or identity.get("scene_identity") != expected_scene_identity
        or identity.get("material_plan_sha256")
        != _hash_json(material_plan.to_payload())
        or identity.get("quality_option_identities")
        != _quality_option_identities()
        or identity.get("plan_sha256") != expected_plan["plan_sha256"]
    ):
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 source, scene, material, plan, or quality identity changed."
        )
    _validate_radiance_installation_identity(identity.get("radiance_installation"))
    repository_revision = identity.get("repository_revision")
    if repository_revision is not None and (
        not isinstance(repository_revision, str)
        or len(repository_revision) not in (40, 64)
        or any(character not in "0123456789abcdef" for character in repository_revision)
    ):
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 repository revision identity is invalid."
        )
    return configuration, identity, scene


def _validate_radiance_installation_identity(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {"oconv", "rtrace"}:
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 Radiance installation identity is incomplete."
        )
    for expected_name in ("oconv", "rtrace"):
        record = value.get(expected_name)
        if (
            not isinstance(record, Mapping)
            or set(record) != {"name", "path", "version_text"}
            or record.get("name") != expected_name
            or not isinstance(record.get("path"), str)
            or not record.get("path")
            or (
                record.get("version_text") is not None
                and not isinstance(record.get("version_text"), str)
            )
        ):
            raise SurfaceFluxCoefficientAnalysisError(
                f"D5-A1 {expected_name} installation identity is invalid."
            )


def _validate_completion_identity(
    completion: Mapping[str, object], configuration_sha256: str
) -> None:
    preservation = completion.get("scientific_preservation")
    promotion = completion.get("promotion_state")
    if (
        completion.get("schema_id") != D5_COMPLETION_SCHEMA_ID
        or completion.get("schema_version") != D5_COMPLETION_SCHEMA_VERSION
        or completion.get("experiment_id") != D5_EXPERIMENT_ID
        or completion.get("d5_identity_version") != D5_IDENTITY_VERSION
        or completion.get("status") != "complete"
        or completion.get("configuration_sha256") != configuration_sha256
        or completion.get("sampling_profile_id") != D5_SAMPLING_PROFILE_ID
        or completion.get("topology_sha256") != D5_TOPOLOGY_SHA256
        or completion.get("receivers_sha256") != D5_RECEIVERS_SHA256
        or completion.get("quality_order") != list(D5_QUALITY_ORDER)
        or completion.get("calibrated_quality_families")
        != list(D5_QUALITY_ORDER)
        or completion.get("reference_level_order_umol_m2_s")
        != list(D5_REFERENCE_LEVELS_UMOL_M2_S)
        or completion.get("band_order") != list(D5_BAND_ORDER)
        or completion.get("deferred_runtime_display_quality_mapping")
        != D5_DEFERRED_DISPLAY_QUALITY_MAPPING
        or completion.get("quality_option_identities")
        != _quality_option_identities()
        or completion.get("job_count") != D5_JOB_COUNT
        or completion.get("stage_a_evidence_count") != D5_JOB_COUNT
        or completion.get("stage_a_reference_trace_count") != 2 * D5_JOB_COUNT
        or completion.get("stage_b_receiver_artifact_count")
        != D5_STAGE_B_ARTIFACT_COUNT
        or preservation
        != {
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
        }
        or promotion
        != {
            "candidate_coefficients_generated": False,
            "production_resource_generated": False,
            "optimized_coloring_enabled": False,
        }
    ):
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 completion is not the required fully completed v3 contract."
        )


def _validate_stage_evidence(
    results: Sequence[Mapping[str, object]],
    configuration: SurfaceFluxRecalibrationConfig,
) -> None:
    expected = recalibration_jobs()
    if len(results) != D5_JOB_COUNT:
        raise SurfaceFluxCoefficientAnalysisError(
            "D5-A1 must contain exactly six authenticated jobs."
        )
    for order_index, (job, result) in enumerate(zip(expected, results, strict=True)):
        reference = result.get("reference")
        transport = result.get("plant_transport")
        validation = result.get("validation")
        if (
            result.get("job_order_index") != order_index
            or result.get("job_id") != job.job_id
            or result.get("quality") != job.quality
            or result.get("requested_reference_level_umol_m2_s")
            != job.requested_level
            or not isinstance(reference, Mapping)
            or not isinstance(reference.get("acceptance"), Mapping)
            or reference["acceptance"].get("pass") is not True
            or not isinstance(transport, Mapping)
            or not isinstance(validation, Mapping)
            or validation.get("all_receiver_values_finite_and_nonnegative")
            is not True
            or validation.get("receiver_identity_and_order_validated") is not True
            or validation.get("all_numerical_and_conservation_validations_pass")
            is not True
        ):
            raise SurfaceFluxCoefficientAnalysisError(
                f"D5-A1 Stage A/B/C evidence is incomplete for {job.job_id}."
            )
        resolution = reference.get("amplitude_resolution")
        final = resolution.get("final") if isinstance(resolution, Mapping) else None
        metrics = final.get("metrics") if isinstance(final, Mapping) else None
        achieved = metrics.get("mean_ppfd_umol_m2_s") if isinstance(metrics, Mapping) else None
        cv = metrics.get("coefficient_of_variation") if isinstance(metrics, Mapping) else None
        if (
            not isinstance(achieved, int | float)
            or isinstance(achieved, bool)
            or not math.isfinite(float(achieved))
            or float(achieved) <= 0.0
            or not isinstance(cv, int | float)
            or isinstance(cv, bool)
            or not math.isfinite(float(cv))
            or float(cv) < 0.0
            or float(cv) > configuration.criteria.maximum_reference_plane_cv
        ):
            raise SurfaceFluxCoefficientAnalysisError(
                f"D5-A1 achieved Stage A authority is invalid for {job.job_id}."
            )
        bands = transport.get("bands")
        if not isinstance(bands, list) or len(bands) != len(D5_BAND_ORDER):
            raise SurfaceFluxCoefficientAnalysisError(
                f"D5-A1 Stage B band evidence is incomplete for {job.job_id}."
            )
        for band_index, (band_id, band) in enumerate(
            zip(D5_BAND_ORDER, bands, strict=True)
        ):
            receiver = band.get("receiver_values") if isinstance(band, Mapping) else None
            if (
                not isinstance(band, Mapping)
                or band.get("order_index") != band_index
                or band.get("band_id") != band_id
                or not isinstance(receiver, Mapping)
                or receiver.get("row_count")
                != D5_EXPECTED_RECEIVER_COUNT_PER_BAND
                or receiver.get("stride_bytes") != FLOAT64_STRIDE_BYTES
                or receiver.get("byte_length")
                != D5_EXPECTED_RECEIVER_BYTES_PER_BAND
            ):
                raise SurfaceFluxCoefficientAnalysisError(
                    f"D5-A1 Stage B Float64 contract changed for "
                    f"{job.job_id}/{band_id}."
                )


def _derive_all_jobs(authenticated: _AuthenticatedSweep) -> tuple[_DerivedJob, ...]:
    jobs: list[_DerivedJob] = []
    for job, result in zip(
        recalibration_jobs(), authenticated.results, strict=True
    ):
        values: dict[str, list[float]] = {
            channel: [] for _side, _metric, channel in COEFFICIENT_CHANNEL_ORDER
        }
        expected_global_patch = 0

        def collect(patch: ParPatchSurfaceLight) -> None:
            nonlocal expected_global_patch
            expected_plant, expected_local_patch = divmod(
                expected_global_patch, D5_PATCHES_PER_PLANT
            )
            if (
                patch.global_patch_index != expected_global_patch
                or patch.plant_index != expected_plant
                or patch.local_patch_index != expected_local_patch
            ):
                raise SurfaceFluxCoefficientAnalysisError(
                    f"Stage C changed canonical plant/patch order for {job.job_id}."
                )
            observed = {
                "front_incident": (
                    patch.front_incident_photon_flux_density_umol_m2_s
                ),
                "front_absorbed": (
                    patch.front_absorbed_photon_flux_density_umol_m2_s
                ),
                "back_incident": (
                    patch.back_incident_photon_flux_density_umol_m2_s
                ),
                "back_absorbed": (
                    patch.back_absorbed_photon_flux_density_umol_m2_s
                ),
            }
            for channel, value in observed.items():
                if not math.isfinite(value) or value < 0.0:
                    raise SurfaceFluxCoefficientAnalysisError(
                        f"Stage C returned invalid {channel} density for {job.job_id}."
                    )
                values[channel].append(value)
            expected_global_patch += 1

        try:
            validation = stream_juvenile_par_surface_light(
                root=(
                    authenticated.input_root
                    / D5_JOBS_DIRECTORY
                    / job.job_id
                ),
                scene=authenticated.scene,
                band_records=result["plant_transport"]["bands"],
                patch_sink=collect,
            )
        except SurfaceFluxCoefficientAnalysisError:
            raise
        except (OSError, ValueError, TypeError, FspmScientificAggregationError) as exc:
            raise SurfaceFluxCoefficientAnalysisError(
                f"authoritative Stage C reconstruction failed for {job.job_id}: {exc}"
            ) from exc
        if (
            expected_global_patch != NEUTRAL_ARRAY_VALUE_COUNT
            or any(
                len(channel_values) != NEUTRAL_ARRAY_VALUE_COUNT
                for channel_values in values.values()
            )
        ):
            raise SurfaceFluxCoefficientAnalysisError(
                f"Stage C patch count is incomplete for {job.job_id}."
            )
        achieved = _achieved_reference(result)
        bands = result["plant_transport"]["bands"]
        jobs.append(
            _DerivedJob(
                job_id=job.job_id,
                family=job.quality,
                requested_level=job.requested_level,
                achieved_reference=achieved,
                values_by_channel={
                    name: tuple(channel_values)
                    for name, channel_values in values.items()
                },
                stage_c_validation=validation.to_dict(),
                source_receiver_sha256_by_band={
                    str(band["band_id"]): str(band["receiver_values"]["sha256"])
                    for band in bands
                },
            )
        )
    return tuple(jobs)


def _achieved_reference(result: Mapping[str, object]) -> float:
    return float(
        result["reference"]["amplitude_resolution"]["final"]["metrics"][
            "mean_ppfd_umol_m2_s"
        ]
    )


def _analyze_channels(
    jobs: Sequence[_DerivedJob],
    *,
    criteria: CalibrationCriteria,
) -> tuple[tuple[_CoefficientChannel, ...], Mapping[str, object]]:
    by_identity = {
        (job.family, job.requested_level): job for job in jobs
    }
    channels: list[_CoefficientChannel] = []
    failure_counts = {
        "exact_zero_coefficient": 0,
        "through_origin_r_squared_below_minimum": 0,
        "relative_ratio_drift_above_maximum": 0,
    }
    affected: dict[str, list[dict[str, object]]] = {
        reason: [] for reason in failure_counts
    }
    failed_fit_count = 0
    for family in D5_QUALITY_ORDER:
        family_jobs = tuple(
            by_identity[(family, level)]
            for level in D5_REFERENCE_LEVELS_UMOL_M2_S
        )
        achieved = tuple(job.achieved_reference for job in family_jobs)
        for side, metric, channel in COEFFICIENT_CHANNEL_ORDER:
            coefficients: list[float] = []
            patch_fits: list[Mapping[str, object]] = []
            for local_patch_index in range(D5_PATCHES_PER_PLANT):
                means = tuple(
                    math.fsum(
                        job.values_by_channel[channel][
                            plant_index * D5_PATCHES_PER_PLANT
                            + local_patch_index
                        ]
                        for plant_index in range(D5_EXPECTED_PLANT_COUNT)
                    )
                    / D5_EXPECTED_PLANT_COUNT
                    for job in family_jobs
                )
                fit = _fit_local_patch(
                    requested_levels=D5_REFERENCE_LEVELS_UMOL_M2_S,
                    achieved_levels=achieved,
                    means=means,
                    criteria=criteria,
                )
                coefficients.append(float(fit["coefficient_gamma"]))
                record = {
                    "local_patch_index": local_patch_index,
                    **fit,
                }
                patch_fits.append(record)
                reasons = fit["promotion_reasons"]
                if reasons:
                    failed_fit_count += 1
                for reason in reasons:
                    failure_counts[reason] += 1
                    affected[reason].append(
                        {
                            "family": family,
                            "side": side,
                            "metric": metric,
                            "local_patch_index": local_patch_index,
                        }
                    )
            coefficient_values = tuple(coefficients)
            channels.append(
                _CoefficientChannel(
                    family=family,
                    side=side,
                    metric=metric,
                    channel=channel,
                    values=coefficient_values,
                    patch_fits=tuple(patch_fits),
                    distribution=_distribution_summary(coefficient_values),
                )
            )
    if len(channels) != COEFFICIENT_ARRAY_COUNT:
        raise SurfaceFluxCoefficientAnalysisError(
            "internal coefficient channel order is incomplete."
        )
    return tuple(channels), {
        "failed_patch_fit_count": failed_fit_count,
        "reason_counts": failure_counts,
        "affected_local_patches": affected,
    }


def _fit_local_patch(
    *,
    requested_levels: Sequence[float],
    achieved_levels: Sequence[float],
    means: Sequence[float],
    criteria: CalibrationCriteria,
) -> dict[str, object]:
    """Apply the established D1 through-origin slope and centered R-squared."""

    if (
        len(requested_levels) != 2
        or len(achieved_levels) != 2
        or len(means) != 2
        or any(not math.isfinite(value) or value <= 0.0 for value in achieved_levels)
        or any(not math.isfinite(value) or value < 0.0 for value in means)
    ):
        raise SurfaceFluxCoefficientAnalysisError(
            "local-patch fit requires two positive achieved references and "
            "two finite nonnegative means."
        )
    coefficient = _through_origin_slope(achieved_levels, means)
    ratios = tuple(
        mean / achieved
        for mean, achieved in zip(means, achieved_levels, strict=True)
    )
    predictions = tuple(coefficient * value for value in achieved_levels)
    residuals = tuple(
        observed - predicted
        for observed, predicted in zip(means, predictions, strict=True)
    )
    r_squared = _r_squared(means, predictions)
    ratio_mean = math.fsum(ratios) / len(ratios)
    relative_drift = (
        0.0
        if ratio_mean == 0.0
        else (max(ratios) - min(ratios)) / ratio_mean
    )
    reasons: list[str] = []
    if coefficient == 0.0:
        reasons.append("exact_zero_coefficient")
    if r_squared < criteria.minimum_through_origin_r_squared:
        reasons.append("through_origin_r_squared_below_minimum")
    if relative_drift > criteria.maximum_beta_drift:
        reasons.append("relative_ratio_drift_above_maximum")
    return {
        "coefficient_gamma": coefficient,
        "coefficient_units": COEFFICIENT_UNITS,
        "through_origin_equation": (
            "gamma = sum_l(R_l * mean_q_l) / sum_l(R_l^2)"
        ),
        "r_squared": r_squared,
        "r_squared_definition": (
            "1 - sum((mean_q_l - gamma*R_l)^2) / "
            "sum((mean_q_l - mean(mean_q))^2)"
        ),
        "relative_ratio_drift": relative_drift,
        "relative_ratio_drift_definition": (
            "(max(mean_q_l/R_l) - min(mean_q_l/R_l)) / "
            "mean(mean_q_l/R_l); exact zero mean maps to exact zero drift"
        ),
        "residual_sum_squares": math.fsum(value * value for value in residuals),
        "total_sum_squares_centered": math.fsum(
            (value - math.fsum(means) / len(means)) ** 2 for value in means
        ),
        "levels": [
            {
                "requested_reference_level_umol_m2_s": requested,
                "achieved_reference_R_umol_m2_s": achieved,
                "mean_q_over_64_plants_umol_m2_s": mean,
                "response_ratio_mean_q_over_R": ratio,
                "predicted_mean_q_umol_m2_s": predicted,
                "signed_residual_umol_m2_s": residual,
            }
            for requested, achieved, mean, ratio, predicted, residual in zip(
                requested_levels,
                achieved_levels,
                means,
                ratios,
                predictions,
                residuals,
                strict=True,
            )
        ],
        "promotion_eligible": not reasons,
        "promotion_reasons": reasons,
    }


def _distribution_summary(values: Sequence[float]) -> dict[str, object]:
    numbers = tuple(float(value) for value in values)
    finite = tuple(value for value in numbers if math.isfinite(value))
    positives = tuple(value for value in finite if value > 0.0)
    zeros = sum(value == 0.0 for value in finite)
    negatives = sum(value < 0.0 for value in finite)
    mean = math.fsum(finite) / len(finite) if finite else None
    variance = (
        math.fsum((value - mean) ** 2 for value in finite) / len(finite)
        if finite and mean is not None
        else None
    )
    sorted_finite = tuple(sorted(finite))
    return {
        "count": len(numbers),
        "finite_count": len(finite),
        "all_finite": len(finite) == len(numbers),
        "nonfinite_count": len(numbers) - len(finite),
        "exact_zero_count": zeros,
        "negative_count": negatives,
        "positive_count": len(positives),
        "minimum": min(finite) if finite else None,
        "minimum_positive": min(positives) if positives else None,
        "maximum": max(finite) if finite else None,
        "mean": mean,
        "population_standard_deviation": (
            math.sqrt(variance) if variance is not None else None
        ),
        "quantile_definition": (
            "sorted linear interpolation at index (count-1)*p"
        ),
        "quantiles": {
            name: _linear_quantile(sorted_finite, probability)
            for name, probability in (
                ("p01", 0.01),
                ("p05", 0.05),
                ("p25", 0.25),
                ("p50", 0.50),
                ("p75", 0.75),
                ("p95", 0.95),
                ("p99", 0.99),
            )
        },
    }


def _linear_quantile(sorted_values: Sequence[float], probability: float) -> float | None:
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return math.fsum(
        (
            sorted_values[lower] * (1.0 - fraction),
            sorted_values[upper] * fraction,
        )
    )


def _coefficient_payloads(
    channels: Sequence[_CoefficientChannel],
    *,
    source_completion_sha256: str,
) -> tuple[tuple[_BinaryPayload, ...], Mapping[str, object]]:
    payloads: list[_BinaryPayload] = []
    combined_parts: list[bytes] = []
    for order_index, channel in enumerate(channels):
        data = struct.pack(
            f"<{D5_PATCHES_PER_PLANT}d", *channel.values
        )
        if len(data) != COEFFICIENT_ARRAY_BYTES:
            raise SurfaceFluxCoefficientAnalysisError(
                "coefficient Float64 byte length changed."
            )
        relative = (
            f"{D5_A2_COEFFICIENT_DIRECTORY}/{order_index:02d}-"
            f"{channel.family}-{channel.side}-{channel.metric}.v1.f64le.bin"
        )
        record = {
            "schema_id": D5_A2_COEFFICIENT_SCHEMA_ID,
            "schema_version": D5_A2_COEFFICIENT_SCHEMA_VERSION,
            "order_index": order_index,
            "family": channel.family,
            "side": channel.side,
            "metric": channel.metric,
            "artifact": _binary_artifact(
                relative,
                data,
                role="local_patch_surface_flux_coefficients",
                value_count=D5_PATCHES_PER_PLANT,
                shape=[D5_PATCHES_PER_PLANT],
                units=COEFFICIENT_UNITS,
                ordering="canonical local_patch_index 0..191",
            ),
            "distribution_summary": dict(channel.distribution),
        }
        payloads.append(_BinaryPayload(relative, data, record))
        combined_parts.append(data)

    combined = b"".join(combined_parts)
    if len(payloads) != COEFFICIENT_ARRAY_COUNT or len(combined) != COMBINED_COEFFICIENT_BYTES:
        raise SurfaceFluxCoefficientAnalysisError(
            "canonical coefficient payload count or byte length changed."
        )
    combined_relative = (
        f"{D5_A2_COEFFICIENT_DIRECTORY}/{D5_A2_COMBINED_COEFFICIENT_NAME}"
    )
    combined_record = {
        "schema_id": D5_A2_COEFFICIENT_SCHEMA_ID,
        "schema_version": D5_A2_COEFFICIENT_SCHEMA_VERSION,
        "artifact": _binary_artifact(
            combined_relative,
            combined,
            role="combined_canonical_local_patch_surface_flux_coefficients",
            value_count=COEFFICIENT_VALUE_COUNT,
            shape=[
                len(D5_QUALITY_ORDER),
                len(COEFFICIENT_CHANNEL_ORDER),
                D5_PATCHES_PER_PLANT,
            ],
            units=COEFFICIENT_UNITS,
            ordering=(
                "family-major Standard, Quality, Rigorous; within family "
                "front incident, front absorbed, back incident, back absorbed; "
                "within array canonical local_patch_index 0..191"
            ),
        ),
        "constituent_sha256_order": [
            payload.record["artifact"]["sha256"] for payload in payloads
        ],
    }
    payloads.append(_BinaryPayload(combined_relative, combined, combined_record))
    manifest = {
        "schema_id": D5_A2_COEFFICIENT_SCHEMA_ID,
        "schema_version": D5_A2_COEFFICIENT_SCHEMA_VERSION,
        "analysis_id": D5_A2_ANALYSIS_ID,
        "source_experiment_id": D5_EXPERIMENT_ID,
        "source_completion_sha256": source_completion_sha256,
        "component_type": COMPONENT_TYPE,
        "byte_order": BYTE_ORDER,
        "stride_bytes": FLOAT64_STRIDE_BYTES,
        "units": COEFFICIENT_UNITS,
        "coefficient_symbol": "gamma[family,side,metric,local_patch]",
        "array_count": COEFFICIENT_ARRAY_COUNT,
        "values_per_array": D5_PATCHES_PER_PLANT,
        "array_order": [
            {
                "order_index": index,
                "family": channel.family,
                "side": channel.side,
                "metric": channel.metric,
            }
            for index, channel in enumerate(channels)
        ],
        "coefficient_artifacts": [
            dict(payload.record) for payload in payloads[:-1]
        ],
        "combined_canonical_payload": combined_record,
        "production_calibration_resource": False,
    }
    return tuple(payloads), manifest


def _neutral_payloads(
    jobs: Sequence[_DerivedJob],
    channels: Sequence[_CoefficientChannel],
    *,
    coefficient_payloads: Sequence[_BinaryPayload],
    source_completion_sha256: str,
) -> tuple[tuple[_BinaryPayload, ...], Mapping[str, object]]:
    coefficient_by_identity = {
        (channel.family, channel.channel): channel for channel in channels
    }
    coefficient_hash_by_identity = {
        (
            str(payload.record["family"]),
            f"{payload.record['side']}_{payload.record['metric']}",
        ): str(payload.record["artifact"]["sha256"])
        for payload in coefficient_payloads[:-1]
    }
    job_by_identity = {
        (job.family, job.requested_level): job for job in jobs
    }
    payloads: list[_BinaryPayload] = []
    order_index = 0
    for family in D5_QUALITY_ORDER:
        for requested_level in D5_REFERENCE_LEVELS_UMOL_M2_S:
            job = job_by_identity[(family, requested_level)]
            for side, metric, channel_name in COEFFICIENT_CHANNEL_ORDER:
                coefficients = coefficient_by_identity[
                    (family, channel_name)
                ].values
                data_parts: list[bytes] = []
                defined_values: list[float] = []
                undefined_indices: list[int] = []
                for flat_index, q in enumerate(job.values_by_channel[channel_name]):
                    local_patch_index = flat_index % D5_PATCHES_PER_PLANT
                    gamma = coefficients[local_patch_index]
                    if gamma == 0.0:
                        data_parts.append(CANONICAL_QNAN_BYTES)
                        undefined_indices.append(flat_index)
                    else:
                        u = q / (gamma * job.achieved_reference)
                        data_parts.append(struct.pack("<d", u))
                        defined_values.append(u)
                data = b"".join(data_parts)
                if len(data) != NEUTRAL_ARRAY_BYTES:
                    raise SurfaceFluxCoefficientAnalysisError(
                        "normalized neutral evidence byte length changed."
                    )
                relative = (
                    f"{D5_A2_NEUTRAL_DIRECTORY}/{order_index:02d}-"
                    f"{family}-{_level_token(requested_level)}-"
                    f"{side}-{metric}.v1.f64le.bin"
                )
                record = {
                    "schema_id": D5_A2_NEUTRAL_SCHEMA_ID,
                    "schema_version": D5_A2_NEUTRAL_SCHEMA_VERSION,
                    "order_index": order_index,
                    "family": family,
                    "requested_reference_level_umol_m2_s": requested_level,
                    "achieved_reference_R_umol_m2_s": job.achieved_reference,
                    "side": side,
                    "metric": metric,
                    "source_job_id": job.job_id,
                    "source_receiver_sha256_by_band": dict(
                        job.source_receiver_sha256_by_band
                    ),
                    "coefficient_artifact_sha256": coefficient_hash_by_identity[
                        (family, channel_name)
                    ],
                    "artifact": _binary_artifact(
                        relative,
                        data,
                        role="normalized_neutral_local_patch_evidence",
                        value_count=NEUTRAL_ARRAY_VALUE_COUNT,
                        shape=[D5_EXPECTED_PLANT_COUNT, D5_PATCHES_PER_PLANT],
                        units=NORMALIZED_UNITS,
                        ordering=(
                            "plant_index 0..63 major; canonical "
                            "local_patch_index 0..191 minor"
                        ),
                    ),
                    "equation": "u = q / (gamma * achieved_R)",
                    "defined_value_distribution": _distribution_summary(
                        defined_values
                    ),
                    "undefined_exact_zero_gamma_count": len(undefined_indices),
                    "undefined_flat_indices": undefined_indices,
                    "undefined_encoding": (
                        "canonical IEEE-754 quiet NaN 0x7ff8000000000000; "
                        "used only where gamma is exactly 0.0 and u is "
                        "mathematically undefined; never interpreted as data"
                    ),
                }
                payloads.append(_BinaryPayload(relative, data, record))
                order_index += 1
    expected_count = (
        len(D5_QUALITY_ORDER)
        * len(D5_REFERENCE_LEVELS_UMOL_M2_S)
        * len(COEFFICIENT_CHANNEL_ORDER)
    )
    if len(payloads) != expected_count:
        raise SurfaceFluxCoefficientAnalysisError(
            "normalized neutral evidence matrix is incomplete."
        )
    manifest = {
        "schema_id": D5_A2_NEUTRAL_SCHEMA_ID,
        "schema_version": D5_A2_NEUTRAL_SCHEMA_VERSION,
        "analysis_id": D5_A2_ANALYSIS_ID,
        "source_experiment_id": D5_EXPERIMENT_ID,
        "source_completion_sha256": source_completion_sha256,
        "component_type": COMPONENT_TYPE,
        "byte_order": BYTE_ORDER,
        "stride_bytes": FLOAT64_STRIDE_BYTES,
        "units": NORMALIZED_UNITS,
        "equation": "u[p,k] = q[p,k] / (gamma[family,side,metric,k] * R)",
        "provenance_order": (
            "family Standard/Quality/Rigorous; requested level 250/500 "
            "bound to authenticated achieved R; channel front incident, "
            "front absorbed, back incident, back absorbed; plant_index 0..63; "
            "canonical local_patch_index 0..191"
        ),
        "front_and_back_distributions_separate": True,
        "palette_anchors_selected": False,
        "artifact_count": expected_count,
        "artifacts": [dict(payload.record) for payload in payloads],
    }
    return tuple(payloads), manifest


def _binary_artifact(
    relative_path: str,
    data: bytes,
    *,
    role: str,
    value_count: int,
    shape: list[int],
    units: str,
    ordering: str,
) -> dict[str, object]:
    return {
        "role": role,
        "path": relative_path,
        "media_type": "application/octet-stream",
        "schema": "headerless fixed-stride Float64 array",
        "component_type": COMPONENT_TYPE,
        "byte_order": BYTE_ORDER,
        "stride_bytes": FLOAT64_STRIDE_BYTES,
        "units": units,
        "value_count": value_count,
        "shape": shape,
        "ordering": ordering,
        "byte_length": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _analysis_report(
    authenticated: _AuthenticatedSweep,
    jobs: Sequence[_DerivedJob],
    channels: Sequence[_CoefficientChannel],
    *,
    coefficient_manifest: Mapping[str, object],
    neutral_manifest: Mapping[str, object],
    criteria: CalibrationCriteria,
    failure_summary: Mapping[str, object],
    promotion_eligible: bool,
) -> dict[str, object]:
    return {
        "schema_id": D5_A2_ANALYSIS_SCHEMA_ID,
        "schema_version": D5_A2_ANALYSIS_SCHEMA_VERSION,
        "analysis_id": D5_A2_ANALYSIS_ID,
        "source_experiment_id": D5_EXPERIMENT_ID,
        "source_completion_sha256": authenticated.completion_sha256,
        "source_configuration_sha256": authenticated.completion[
            "configuration_sha256"
        ],
        "source_created_at_utc": authenticated.completion["created_at_utc"],
        "input_authentication": {
            "status": "fully_authenticated_before_receiver_value_decode",
            "configuration_hash_validated": True,
            "completion_status_required": "complete",
            "complete_inventory_sizes_and_sha256_validated": True,
            "six_job_completion_manifests_validated": True,
            "stage_a_trace_count": 2 * D5_JOB_COUNT,
            "stage_b_float64_artifact_count": D5_STAGE_B_ARTIFACT_COUNT,
            "quality_option_identities_validated": True,
            "achieved_stage_a_references_authenticated": True,
        },
        "fixed_identity": {
            "sampling_profile_id": D5_SAMPLING_PROFILE_ID,
            "topology_sha256": D5_TOPOLOGY_SHA256,
            "receivers_sha256": D5_RECEIVERS_SHA256,
            "quality_order": list(D5_QUALITY_ORDER),
            "requested_level_order_umol_m2_s": list(
                D5_REFERENCE_LEVELS_UMOL_M2_S
            ),
            "band_order": list(D5_BAND_ORDER),
            "plant_count": D5_EXPECTED_PLANT_COUNT,
            "patches_per_plant": D5_PATCHES_PER_PLANT,
            "canonical_receiver_order": (
                "plant-major; local_patch_index 0..191; front then back"
            ),
        },
        "scope": {
            "calibrated_families": list(D5_QUALITY_ORDER),
            "direct_calibrated": False,
            "direct_future_display_only_mapping": dict(
                D5_DEFERRED_DISPLAY_QUALITY_MAPPING
            ),
            "direct_future_display_mapping_implemented": False,
            "direct_raw_transport_proxy": False,
            "far_red_present": False,
            "proposed_present": False,
            "conventional_present": False,
        },
        "equations": {
            "plant_mean": "mean_q_l[k] = math.fsum_p(q[l,p,k]) / 64",
            "level_ratio": "ratio_l[k] = mean_q_l[k] / achieved_R_l",
            "coefficient": (
                "gamma[k] = sum_l(achieved_R_l * mean_q_l[k]) / "
                "sum_l(achieved_R_l^2)"
            ),
            "residual": "e_l[k] = mean_q_l[k] - gamma[k] * achieved_R_l",
            "r_squared": (
                "1 - sum_l(e_l[k]^2) / "
                "sum_l((mean_q_l[k] - mean_l(mean_q_l[k]))^2)"
            ),
            "normalized_neutral": "u[p,k] = q[p,k] / (gamma[k] * achieved_R)",
            "regression_convention_reused_from": (
                "fspm_optics.application.surface_flux_calibration"
            ),
            "stage_c_kernel_reused": (
                "fspm_optics.application.fspm_science."
                "stream_juvenile_par_surface_light"
            ),
        },
        "criteria": criteria.to_dict(),
        "authenticated_jobs": [
            {
                "job_id": job.job_id,
                "family": job.family,
                "requested_reference_level_umol_m2_s": job.requested_level,
                "achieved_reference_R_umol_m2_s": job.achieved_reference,
                "source_receiver_sha256_by_band": dict(
                    job.source_receiver_sha256_by_band
                ),
                "stage_c_validation": dict(job.stage_c_validation),
            }
            for job in jobs
        ],
        "coefficient_order": coefficient_manifest["array_order"],
        "coefficient_manifest": D5_A2_COEFFICIENT_MANIFEST_NAME,
        "coefficient_manifest_sha256": hashlib.sha256(
            _pretty_json(coefficient_manifest).encode("utf-8")
        ).hexdigest(),
        "normalized_neutral_manifest": D5_A2_NEUTRAL_MANIFEST_NAME,
        "normalized_neutral_manifest_sha256": hashlib.sha256(
            _pretty_json(neutral_manifest).encode("utf-8")
        ).hexdigest(),
        "family_side_metric_analyses": [
            {
                "family": channel.family,
                "side": channel.side,
                "metric": channel.metric,
                "distribution_summary": dict(channel.distribution),
                "local_patch_fits": [dict(value) for value in channel.patch_fits],
            }
            for channel in channels
        ],
        "promotion_eligible": promotion_eligible,
        "promotion_reasons": dict(failure_summary),
        "scientific_failure_policy": (
            "linearity and exact-zero concerns are reported without deleting "
            "authenticated coefficient or normalized evidence; structural or "
            "authentication failures abort before publication"
        ),
        "zero_policy": {
            "exact_comparison_only": True,
            "near_zero_threshold_defined": False,
            "clamping_used": False,
            "substitution_used": False,
            "masking_used": False,
        },
        "deferred": [
            "production calibration-resource generation",
            "near-zero availability policy",
            "palette selection",
            "metadata v3",
            "backend or viewer coloring",
            "held-out Proposed evaluation",
            "held-out Conventional evaluation",
        ],
    }


def _publish(
    output_root: Path,
    *,
    authenticated: _AuthenticatedSweep,
    coefficient_payloads: Sequence[_BinaryPayload],
    neutral_payloads: Sequence[_BinaryPayload],
    coefficient_manifest: Mapping[str, object],
    neutral_manifest: Mapping[str, object],
    report: Mapping[str, object],
    promotion_eligible: bool,
) -> SurfaceFluxCoefficientAnalysisPublication:
    output_root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{output_root.name}.",
            suffix=".tmp",
            dir=output_root.parent,
        )
    )
    try:
        for payload in (*coefficient_payloads, *neutral_payloads):
            atomic_write_bytes(stage / payload.relative_path, payload.data)
        atomic_write_text(
            stage / D5_A2_COEFFICIENT_MANIFEST_NAME,
            _pretty_json(coefficient_manifest),
        )
        atomic_write_text(
            stage / D5_A2_NEUTRAL_MANIFEST_NAME,
            _pretty_json(neutral_manifest),
        )
        report_path = stage / D5_A2_ANALYSIS_NAME
        atomic_write_text(report_path, _pretty_json(report))
        inventory = _inventory(stage, excluded={D5_A2_COMPLETION_NAME})
        completion = {
            "schema_id": D5_A2_COMPLETION_SCHEMA_ID,
            "schema_version": D5_A2_COMPLETION_SCHEMA_VERSION,
            "analysis_id": D5_A2_ANALYSIS_ID,
            "source_experiment_id": D5_EXPERIMENT_ID,
            "source_completion_sha256": authenticated.completion_sha256,
            "status": "complete",
            "promotion_eligible": promotion_eligible,
            "coefficient_array_count": COEFFICIENT_ARRAY_COUNT,
            "normalized_neutral_artifact_count": len(neutral_payloads),
            "report": _file_artifact(stage, report_path),
            "coefficient_manifest": _file_artifact(
                stage, stage / D5_A2_COEFFICIENT_MANIFEST_NAME
            ),
            "normalized_neutral_manifest": _file_artifact(
                stage, stage / D5_A2_NEUTRAL_MANIFEST_NAME
            ),
            "ordered_artifact_inventory": inventory,
            "input_directory_modified": False,
            "radiance_invoked": False,
            "production_calibration_resource_generated": False,
        }
        completion_path = stage / D5_A2_COMPLETION_NAME
        atomic_write_text(completion_path, _pretty_json(completion))
        if output_root.exists():
            raise SurfaceFluxCoefficientAnalysisError(
                "D5-A2 output appeared during staged publication."
            )
        os.replace(stage, output_root)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return SurfaceFluxCoefficientAnalysisPublication(
        output_directory=output_root,
        report_path=output_root / D5_A2_ANALYSIS_NAME,
        report=report,
        completion_path=output_root / D5_A2_COMPLETION_NAME,
        completion=completion,
    )


def _file_artifact(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "byte_length": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _level_token(value: float) -> str:
    return format(value, ".12g").replace(".", "p")


def format_surface_flux_coefficient_analysis_json(
    payload: Mapping[str, object],
) -> str:
    """Serialize D5-A2 JSON with deterministic project formatting."""

    return _pretty_json(payload)


__all__ = [
    "BYTE_ORDER",
    "COEFFICIENT_ARRAY_COUNT",
    "COEFFICIENT_CHANNEL_ORDER",
    "COEFFICIENT_UNITS",
    "D5_A2_ANALYSIS_ID",
    "D5_A2_ANALYSIS_NAME",
    "D5_A2_ANALYSIS_SCHEMA_ID",
    "D5_A2_COEFFICIENT_MANIFEST_NAME",
    "D5_A2_COMPLETION_NAME",
    "D5_A2_NEUTRAL_MANIFEST_NAME",
    "SurfaceFluxCoefficientAnalysisError",
    "SurfaceFluxCoefficientAnalysisPublication",
    "analyze_surface_flux_coefficients",
    "format_surface_flux_coefficient_analysis_json",
]
