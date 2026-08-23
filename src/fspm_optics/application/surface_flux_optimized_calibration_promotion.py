"""Deterministic Phase 27G-D5-B1 candidate-resource promotion.

The completed D5-A2 directory is treated as immutable input.  Every completion,
manifest, report, inventory, and referenced binary is authenticated before a
two-file candidate resource is staged and atomically promoted.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import tempfile
from typing import Mapping, Sequence

from fspm_optics.application.surface_flux_calibration import (
    CalibrationCriteria,
    SurfaceFluxCalibrationError,
    _inventory,
    _pretty_json,
    _read_json_object,
    _r_squared,
    _sha256_file,
    _through_origin_slope,
    _valid_sha256,
    _validated_timestamp,
)
from fspm_optics.application.surface_flux_coefficient_analysis import (
    BYTE_ORDER as A2_BYTE_ORDER,
    COEFFICIENT_ARRAY_COUNT as A2_COEFFICIENT_ARRAY_COUNT,
    COEFFICIENT_CHANNEL_ORDER as A2_COEFFICIENT_CHANNEL_ORDER,
    COEFFICIENT_UNITS as A2_COEFFICIENT_UNITS,
    COEFFICIENT_VALUE_COUNT as A2_COEFFICIENT_VALUE_COUNT,
    COMBINED_COEFFICIENT_BYTES as A2_COMBINED_COEFFICIENT_BYTES,
    COMPONENT_TYPE as A2_COMPONENT_TYPE,
    D5_A2_ANALYSIS_ID,
    D5_A2_ANALYSIS_NAME,
    D5_A2_ANALYSIS_SCHEMA_ID,
    D5_A2_ANALYSIS_SCHEMA_VERSION,
    D5_A2_COEFFICIENT_DIRECTORY,
    D5_A2_COEFFICIENT_MANIFEST_NAME,
    D5_A2_COEFFICIENT_SCHEMA_ID,
    D5_A2_COEFFICIENT_SCHEMA_VERSION,
    D5_A2_COMBINED_COEFFICIENT_NAME,
    D5_A2_COMPLETION_NAME,
    D5_A2_COMPLETION_SCHEMA_ID,
    D5_A2_COMPLETION_SCHEMA_VERSION,
    D5_A2_NEUTRAL_DIRECTORY,
    D5_A2_NEUTRAL_MANIFEST_NAME,
    D5_A2_NEUTRAL_SCHEMA_ID,
    D5_A2_NEUTRAL_SCHEMA_VERSION,
    FLOAT64_STRIDE_BYTES as A2_FLOAT64_STRIDE_BYTES,
    NEUTRAL_ARRAY_BYTES,
    NEUTRAL_ARRAY_VALUE_COUNT,
    NORMALIZED_UNITS,
    _distribution_summary as _a2_distribution_summary,
)
from fspm_optics.application.fspm_science import (
    COEFFICIENT_CLOSURE_ABS_TOLERANCE,
    LEAF_STRUCT,
    LOCAL_CLOSURE_ABS_TOLERANCE,
    LOCAL_CLOSURE_REL_TOLERANCE,
    PATCH_STRUCT,
    PLANT_STRUCT,
)
from fspm_optics.application.surface_flux_optimized_calibration import (
    COEFFICIENT_ARRAY_BYTES,
    COEFFICIENT_ORDERING,
    COEFFICIENT_PAYLOAD_BYTES,
    COEFFICIENT_SHAPE,
    COEFFICIENT_UNITS,
    DEFERRED_SCOPE,
    OPTIMIZED_CALIBRATION_MANIFEST_NAME,
    OPTIMIZED_CALIBRATION_PAYLOAD_NAME,
    OPTIMIZED_CALIBRATION_RESOURCE_ID,
    OPTIMIZED_CALIBRATION_RESOURCE_SCHEMA_ID,
    OPTIMIZED_CALIBRATION_RESOURCE_SCHEMA_VERSION,
    expected_array_order,
    expected_quality_mapping_payload,
    fixed_scientific_identity,
    load_optimized_surface_flux_calibration,
)
from fspm_optics.application.surface_flux_recalibration import (
    D5_BAND_ORDER,
    D5_DEFERRED_DISPLAY_QUALITY_MAPPING,
    D5_EXPECTED_PLANT_COUNT,
    D5_EXPERIMENT_ID,
    D5_JOB_COUNT,
    D5_PATCHES_PER_PLANT,
    D5_QUALITY_ORDER,
    D5_RECEIVERS_SHA256,
    D5_REFERENCE_LEVELS_UMOL_M2_S,
    D5_SAMPLING_PROFILE_ID,
    D5_STAGE_B_ARTIFACT_COUNT,
    D5_TOPOLOGY_SHA256,
    SurfaceFluxRecalibrationConfig,
    _build_scientific_inputs,
    recalibration_jobs,
)


NORMALIZED_ARTIFACT_COUNT = (
    len(D5_QUALITY_ORDER)
    * len(D5_REFERENCE_LEVELS_UMOL_M2_S)
    * len(A2_COEFFICIENT_CHANNEL_ORDER)
)
EXPECTED_A2_ROOT_ENTRIES = {
    D5_A2_ANALYSIS_NAME,
    D5_A2_COEFFICIENT_MANIFEST_NAME,
    D5_A2_NEUTRAL_MANIFEST_NAME,
    D5_A2_COMPLETION_NAME,
    D5_A2_COEFFICIENT_DIRECTORY,
    D5_A2_NEUTRAL_DIRECTORY,
}


class OptimizedCalibrationPromotionError(RuntimeError):
    """A D5-A2 authority or candidate publication contract failed."""


@dataclass(frozen=True, slots=True)
class OptimizedCalibrationCandidatePublication:
    """One atomically published, non-packaged two-file candidate."""

    output_directory: Path
    manifest_path: Path
    payload_path: Path
    manifest: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _AuthenticatedA2:
    root: Path
    completion: Mapping[str, object]
    completion_sha256: str
    report: Mapping[str, object]
    coefficient_manifest: Mapping[str, object]
    neutral_manifest: Mapping[str, object]
    combined_payload: bytes
    individual_payloads: tuple[bytes, ...]
    material_coefficient_authorities: tuple[Mapping[str, object], ...]


def promote_optimized_surface_flux_calibration(
    *,
    input_directory: str | Path,
    output_directory: str | Path,
) -> OptimizedCalibrationCandidatePublication:
    """Authenticate completed A2 evidence and publish a candidate resource."""

    input_root, output_root = _validate_locations(
        Path(input_directory), Path(output_directory)
    )
    authenticated = _authenticate_completed_a2(input_root)
    manifest = _candidate_manifest(authenticated)
    return _publish_candidate(output_root, authenticated.combined_payload, manifest)


def format_optimized_calibration_resource_json(
    manifest: Mapping[str, object],
) -> str:
    """Serialize the candidate manifest with deterministic project formatting."""

    return _pretty_json(manifest)


def _validate_locations(input_path: Path, output_path: Path) -> tuple[Path, Path]:
    expanded_input = input_path.expanduser()
    if expanded_input.is_symlink():
        raise OptimizedCalibrationPromotionError(
            "D5-A2 input must be a real, non-symbolic-link directory."
        )
    try:
        input_root = expanded_input.resolve(strict=True)
    except OSError as exc:
        raise OptimizedCalibrationPromotionError(
            f"completed D5-A2 input directory does not exist: {input_path}"
        ) from exc
    if not input_root.is_dir() or input_root.is_symlink():
        raise OptimizedCalibrationPromotionError(
            "D5-A2 input must be a real, non-symbolic-link directory."
        )
    output_root = output_path.expanduser().resolve()
    if (
        output_root == input_root
        or output_root.is_relative_to(input_root)
        or input_root.is_relative_to(output_root)
    ):
        raise OptimizedCalibrationPromotionError(
            "candidate output must be distinct from and non-overlapping with "
            "the read-only D5-A2 input."
        )
    if output_root.exists():
        raise OptimizedCalibrationPromotionError(
            "candidate output directory must be absent."
        )
    return input_root, output_root


def _authenticate_completed_a2(root: Path) -> _AuthenticatedA2:
    """Authenticate the complete A2 tree before decoding coefficient values."""

    entries = tuple(root.iterdir())
    if {path.name for path in entries} != EXPECTED_A2_ROOT_ENTRIES or any(
        path.is_symlink() for path in entries
    ):
        raise OptimizedCalibrationPromotionError(
            "completed D5-A2 root inventory is missing, extra, or unsafe."
        )
    if not (root / D5_A2_COEFFICIENT_DIRECTORY).is_dir() or not (
        root / D5_A2_NEUTRAL_DIRECTORY
    ).is_dir():
        raise OptimizedCalibrationPromotionError(
            "completed D5-A2 binary directories are invalid."
        )
    try:
        completion = _read_json_object(root / D5_A2_COMPLETION_NAME)
        _validate_completion(completion)
        actual_inventory = _inventory(root, excluded={D5_A2_COMPLETION_NAME})
        if completion.get("ordered_artifact_inventory") != actual_inventory:
            raise OptimizedCalibrationPromotionError(
                "D5-A2 completion inventory is incomplete, reordered, extra, "
                "or not byte-for-byte authenticated."
            )

        report_record = _artifact_authority(
            completion.get("report"),
            expected_path=D5_A2_ANALYSIS_NAME,
            label="D5-A2 report",
        )
        coefficient_record = _artifact_authority(
            completion.get("coefficient_manifest"),
            expected_path=D5_A2_COEFFICIENT_MANIFEST_NAME,
            label="D5-A2 coefficient manifest",
        )
        neutral_record = _artifact_authority(
            completion.get("normalized_neutral_manifest"),
            expected_path=D5_A2_NEUTRAL_MANIFEST_NAME,
            label="D5-A2 normalized-evidence manifest",
        )
        report = _authorized_json(root, report_record, "D5-A2 report")
        coefficient_manifest = _authorized_json(
            root, coefficient_record, "D5-A2 coefficient manifest"
        )
        neutral_manifest = _authorized_json(
            root, neutral_record, "D5-A2 normalized-evidence manifest"
        )
        _validate_report(
            report,
            completion=completion,
            coefficient_record=coefficient_record,
            neutral_record=neutral_record,
        )
        individual, combined = _validate_coefficient_manifest(
            root, coefficient_manifest, report=report
        )
        material_authorities = _validate_report_jobs(report)
        _validate_neutral_manifest(
            root,
            neutral_manifest,
            report=report,
            coefficient_manifest=coefficient_manifest,
        )
    except OptimizedCalibrationPromotionError:
        raise
    except (OSError, TypeError, ValueError, SurfaceFluxCalibrationError) as exc:
        raise OptimizedCalibrationPromotionError(
            f"completed D5-A2 authentication failed: {exc}"
        ) from exc
    return _AuthenticatedA2(
        root=root,
        completion=completion,
        completion_sha256=_sha256_file(root / D5_A2_COMPLETION_NAME),
        report=report,
        coefficient_manifest=coefficient_manifest,
        neutral_manifest=neutral_manifest,
        combined_payload=combined,
        individual_payloads=individual,
        material_coefficient_authorities=material_authorities,
    )


def _validate_completion(completion: Mapping[str, object]) -> None:
    _require_fields(
        completion,
        {
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
        },
        "D5-A2 completion",
    )
    if (
        completion.get("schema_id") != D5_A2_COMPLETION_SCHEMA_ID
        or completion.get("schema_version") != D5_A2_COMPLETION_SCHEMA_VERSION
        or completion.get("analysis_id") != D5_A2_ANALYSIS_ID
        or completion.get("source_experiment_id") != D5_EXPERIMENT_ID
        or not _valid_sha256(completion.get("source_completion_sha256"))
        or completion.get("status") != "complete"
        or completion.get("promotion_eligible") is not True
        or completion.get("coefficient_array_count") != A2_COEFFICIENT_ARRAY_COUNT
        or completion.get("normalized_neutral_artifact_count")
        != NORMALIZED_ARTIFACT_COUNT
        or not isinstance(completion.get("ordered_artifact_inventory"), list)
        or completion.get("input_directory_modified") is not False
        or completion.get("radiance_invoked") is not False
        or completion.get("production_calibration_resource_generated") is not False
    ):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 completion is incompatible or not promotion-eligible."
        )


def _validate_report(
    report: Mapping[str, object],
    *,
    completion: Mapping[str, object],
    coefficient_record: Mapping[str, object],
    neutral_record: Mapping[str, object],
) -> None:
    expected_fields = {
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
    _require_fields(report, expected_fields, "D5-A2 report")
    if (
        report.get("schema_id") != D5_A2_ANALYSIS_SCHEMA_ID
        or report.get("schema_version") != D5_A2_ANALYSIS_SCHEMA_VERSION
        or report.get("analysis_id") != D5_A2_ANALYSIS_ID
        or report.get("source_experiment_id") != D5_EXPERIMENT_ID
        or report.get("source_completion_sha256")
        != completion.get("source_completion_sha256")
        or not _valid_sha256(report.get("source_configuration_sha256"))
        or report.get("promotion_eligible") is not True
        or report.get("coefficient_manifest")
        != D5_A2_COEFFICIENT_MANIFEST_NAME
        or report.get("coefficient_manifest_sha256")
        != coefficient_record.get("sha256")
        or report.get("normalized_neutral_manifest")
        != D5_A2_NEUTRAL_MANIFEST_NAME
        or report.get("normalized_neutral_manifest_sha256")
        != neutral_record.get("sha256")
    ):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 report identity, authority links, or eligibility is invalid."
        )
    try:
        _validated_timestamp(report.get("source_created_at_utc"))
    except (SurfaceFluxCalibrationError, ValueError) as exc:
        raise OptimizedCalibrationPromotionError(
            "D5-A2 source completion timestamp is invalid."
        ) from exc
    if report.get("input_authentication") != {
        "status": "fully_authenticated_before_receiver_value_decode",
        "configuration_hash_validated": True,
        "completion_status_required": "complete",
        "complete_inventory_sizes_and_sha256_validated": True,
        "six_job_completion_manifests_validated": True,
        "stage_a_trace_count": 2 * D5_JOB_COUNT,
        "stage_b_float64_artifact_count": D5_STAGE_B_ARTIFACT_COUNT,
        "quality_option_identities_validated": True,
        "achieved_stage_a_references_authenticated": True,
    }:
        raise OptimizedCalibrationPromotionError(
            "D5-A2 input-authentication evidence is incomplete."
        )
    if report.get("fixed_identity") != {
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
    }:
        raise OptimizedCalibrationPromotionError(
            "D5-A2 optimized scientific identity changed."
        )
    if report.get("scope") != {
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
    }:
        raise OptimizedCalibrationPromotionError(
            "D5-A2 derivation scope or Direct declaration changed."
        )
    zero_policy = report.get("zero_policy")
    if zero_policy != {
        "exact_comparison_only": True,
        "near_zero_threshold_defined": False,
        "clamping_used": False,
        "substitution_used": False,
        "masking_used": False,
    }:
        raise OptimizedCalibrationPromotionError(
            "D5-A2 zero policy is incompatible."
        )
    if report.get("equations") != {
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
    } or report.get("criteria") != CalibrationCriteria().to_dict():
        raise OptimizedCalibrationPromotionError(
            "D5-A2 equations or promotion criteria changed."
        )
    if report.get("scientific_failure_policy") != (
        "linearity and exact-zero concerns are reported without deleting "
        "authenticated coefficient or normalized evidence; structural or "
        "authentication failures abort before publication"
    ) or report.get("deferred") != [
        "production calibration-resource generation",
        "near-zero availability policy",
        "palette selection",
        "metadata v3",
        "backend or viewer coloring",
        "held-out Proposed evaluation",
        "held-out Conventional evaluation",
    ]:
        raise OptimizedCalibrationPromotionError(
            "D5-A2 failure policy or deferred scope changed."
        )
    reasons = report.get("promotion_reasons")
    if reasons != {
        "failed_patch_fit_count": 0,
        "reason_counts": {
            "exact_zero_coefficient": 0,
            "through_origin_r_squared_below_minimum": 0,
            "relative_ratio_drift_above_maximum": 0,
        },
        "affected_local_patches": {
            "exact_zero_coefficient": [],
            "through_origin_r_squared_below_minimum": [],
            "relative_ratio_drift_above_maximum": [],
        },
    }:
        raise OptimizedCalibrationPromotionError(
            "D5-A2 report contains failed coefficient fits."
        )
    expected_order = [
        {
            "order_index": index,
            "family": family,
            "side": side,
            "metric": metric,
        }
        for index, (family, side, metric) in enumerate(expected_array_order())
    ]
    if report.get("coefficient_order") != expected_order:
        raise OptimizedCalibrationPromotionError(
            "D5-A2 report coefficient order is incompatible."
        )


def _validate_report_jobs(
    report: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    jobs = report.get("authenticated_jobs")
    expected_jobs = recalibration_jobs()
    if not isinstance(jobs, list) or len(jobs) != len(expected_jobs):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 authenticated job evidence is incomplete."
        )
    scene, _material_plan = _build_scientific_inputs(
        SurfaceFluxRecalibrationConfig(output_directory=Path.cwd())
    )
    expected_stage_c_counts = scene.counts.to_payload()
    canonical_materials: tuple[Mapping[str, object], ...] | None = None
    for record_value, expected in zip(jobs, expected_jobs, strict=True):
        record = _mapping(record_value, "authenticated D5-A2 job")
        _require_fields(
            record,
            {
                "job_id",
                "family",
                "requested_reference_level_umol_m2_s",
                "achieved_reference_R_umol_m2_s",
                "source_receiver_sha256_by_band",
                "stage_c_validation",
            },
            "authenticated D5-A2 job",
        )
        source_hashes = record.get("source_receiver_sha256_by_band")
        validation = _mapping(
            record.get("stage_c_validation"), "Stage C validation"
        )
        _require_fields(
            validation,
            {
                "counts",
                "modeled_physical_one_sided_leaf_area_m2",
                "closure",
                "raw_receiver_sha256_by_band",
                "material_coefficient_authorities",
                "derivation_digests",
                "par_band_order",
                "far_red_executed_or_aggregated",
                "authoritative_phase27g_c_equations_reused",
            },
            "Stage C validation",
        )
        if (
            record.get("job_id") != expected.job_id
            or record.get("family") != expected.quality
            or record.get("requested_reference_level_umol_m2_s")
            != expected.requested_level
            or not _positive_finite(record.get("achieved_reference_R_umol_m2_s"))
            or not isinstance(source_hashes, Mapping)
            or list(source_hashes) != list(D5_BAND_ORDER)
            or any(not _valid_sha256(source_hashes[band]) for band in D5_BAND_ORDER)
            or validation.get("par_band_order") != list(D5_BAND_ORDER)
            or validation.get("far_red_executed_or_aggregated") is not False
            or validation.get("authoritative_phase27g_c_equations_reused") is not True
            or validation.get("raw_receiver_sha256_by_band") != dict(source_hashes)
        ):
            raise OptimizedCalibrationPromotionError(
                f"D5-A2 job or Stage C authority is invalid: {expected.job_id}"
            )
        _validate_stage_c_numerical_evidence(
            validation, expected_counts=expected_stage_c_counts
        )
        material_records = validation.get("material_coefficient_authorities")
        if not isinstance(material_records, list) or len(material_records) != len(
            D5_BAND_ORDER
        ):
            raise OptimizedCalibrationPromotionError(
                f"D5-A2 material authority is incomplete: {expected.job_id}"
            )
        current: list[Mapping[str, object]] = []
        for band, material_value in zip(
            D5_BAND_ORDER, material_records, strict=True
        ):
            material = _mapping(material_value, "Stage C material authority")
            if (
                material.get("band_id") != band
                or material.get("raw_receiver_sha256") != source_hashes[band]
            ):
                raise OptimizedCalibrationPromotionError(
                    f"D5-A2 material/raw authority changed: {expected.job_id}/{band}"
                )
            current.append(
                {
                    key: value
                    for key, value in material.items()
                    if key != "raw_receiver_sha256"
                }
            )
        current_tuple = tuple(current)
        if canonical_materials is None:
            canonical_materials = current_tuple
        elif current_tuple != canonical_materials:
            raise OptimizedCalibrationPromotionError(
                "D5-A2 material identities differ across calibrated jobs."
            )
    if canonical_materials is None:
        raise OptimizedCalibrationPromotionError(
            "D5-A2 material authority matrix is empty."
        )
    fixed_scientific_identity(canonical_materials)
    return canonical_materials


def _validate_stage_c_numerical_evidence(
    validation: Mapping[str, object],
    *,
    expected_counts: Mapping[str, int],
) -> None:
    counts = _mapping(validation.get("counts"), "Stage C counts")
    if dict(counts) != dict(expected_counts):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 Stage C scene counts are incompatible."
        )
    if not _positive_finite(
        validation.get("modeled_physical_one_sided_leaf_area_m2")
    ):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 Stage C modeled leaf area is invalid."
        )
    closure = _mapping(validation.get("closure"), "Stage C closure evidence")
    closure_fields = {
        "coefficient_abs_tolerance",
        "local_rel_tolerance",
        "local_abs_tolerance",
        "maximum_band_density_closure_error_umol_m2_s",
        "maximum_band_rate_closure_error_umol_s",
        "maximum_par_or_far_red_density_closure_error_umol_m2_s",
        "maximum_par_or_far_red_rate_closure_error_umol_s",
        "maximum_patch_to_leaf_conservation_error_umol_s",
        "maximum_leaf_to_plant_conservation_error_umol_s",
        "maximum_plant_to_room_conservation_error_umol_s",
    }
    _require_fields(closure, closure_fields, "Stage C closure evidence")
    if (
        closure.get("coefficient_abs_tolerance")
        != COEFFICIENT_CLOSURE_ABS_TOLERANCE
        or closure.get("local_rel_tolerance") != LOCAL_CLOSURE_REL_TOLERANCE
        or closure.get("local_abs_tolerance") != LOCAL_CLOSURE_ABS_TOLERANCE
        or any(
            not _finite_nonnegative(closure.get(name))
            for name in closure_fields
            if name
            not in {
                "coefficient_abs_tolerance",
                "local_rel_tolerance",
                "local_abs_tolerance",
            }
        )
    ):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 Stage C closure evidence is invalid."
        )
    digests = _mapping(
        validation.get("derivation_digests"), "Stage C derivation digests"
    )
    if set(digests) != {"patch", "leaf", "plant"}:
        raise OptimizedCalibrationPromotionError(
            "D5-A2 Stage C derivation digest inventory is invalid."
        )
    for name, row_count, stride in (
        ("patch", counts["patches"], PATCH_STRUCT.size),
        ("leaf", counts["leaves"], LEAF_STRUCT.size),
        ("plant", counts["plants"], PLANT_STRUCT.size),
    ):
        record = _mapping(digests.get(name), f"Stage C {name} digest")
        _require_fields(
            record,
            {"sha256", "byte_length", "row_count", "stride_bytes"},
            f"Stage C {name} digest",
        )
        if (
            not _valid_sha256(record.get("sha256"))
            or record.get("row_count") != row_count
            or record.get("stride_bytes") != stride
            or record.get("byte_length") != row_count * stride
        ):
            raise OptimizedCalibrationPromotionError(
                f"D5-A2 Stage C {name} derivation digest is invalid."
            )


def _validate_coefficient_manifest(
    root: Path,
    manifest: Mapping[str, object],
    *,
    report: Mapping[str, object],
) -> tuple[tuple[bytes, ...], bytes]:
    _require_fields(
        manifest,
        {
            "schema_id",
            "schema_version",
            "analysis_id",
            "source_experiment_id",
            "source_completion_sha256",
            "component_type",
            "byte_order",
            "stride_bytes",
            "units",
            "coefficient_symbol",
            "array_count",
            "values_per_array",
            "array_order",
            "coefficient_artifacts",
            "combined_canonical_payload",
            "production_calibration_resource",
        },
        "D5-A2 coefficient manifest",
    )
    expected_order_records = report["coefficient_order"]
    artifacts = manifest.get("coefficient_artifacts")
    if (
        manifest.get("schema_id") != D5_A2_COEFFICIENT_SCHEMA_ID
        or manifest.get("schema_version") != D5_A2_COEFFICIENT_SCHEMA_VERSION
        or manifest.get("analysis_id") != D5_A2_ANALYSIS_ID
        or manifest.get("source_experiment_id") != D5_EXPERIMENT_ID
        or manifest.get("source_completion_sha256")
        != report.get("source_completion_sha256")
        or manifest.get("component_type") != A2_COMPONENT_TYPE
        or manifest.get("byte_order") != A2_BYTE_ORDER
        or manifest.get("stride_bytes") != A2_FLOAT64_STRIDE_BYTES
        or manifest.get("units") != A2_COEFFICIENT_UNITS
        or manifest.get("coefficient_symbol")
        != "gamma[family,side,metric,local_patch]"
        or manifest.get("array_count") != A2_COEFFICIENT_ARRAY_COUNT
        or manifest.get("values_per_array") != D5_PATCHES_PER_PLANT
        or manifest.get("array_order") != expected_order_records
        or not isinstance(artifacts, list)
        or len(artifacts) != A2_COEFFICIENT_ARRAY_COUNT
        or manifest.get("production_calibration_resource") is not False
    ):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 coefficient manifest identity or order is invalid."
        )

    analyses = report.get("family_side_metric_analyses")
    if not isinstance(analyses, list) or len(analyses) != A2_COEFFICIENT_ARRAY_COUNT:
        raise OptimizedCalibrationPromotionError(
            "D5-A2 local-patch fit analyses are incomplete."
        )
    achieved_by_family = {
        family: tuple(
            float(job["achieved_reference_R_umol_m2_s"])
            for job in report["authenticated_jobs"]
            if job["family"] == family
        )
        for family in D5_QUALITY_ORDER
    }
    if any(
        len(values) != len(D5_REFERENCE_LEVELS_UMOL_M2_S)
        for values in achieved_by_family.values()
    ):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 achieved-reference matrix is incomplete."
        )
    individual: list[bytes] = []
    individual_hashes: list[str] = []
    for order_index, (record_value, identity, fit_value) in enumerate(
        zip(artifacts, expected_array_order(), analyses, strict=True)
    ):
        family, side, metric = identity
        record = _mapping(record_value, "D5-A2 coefficient artifact")
        _require_fields(
            record,
            {
                "schema_id",
                "schema_version",
                "order_index",
                "family",
                "side",
                "metric",
                "artifact",
                "distribution_summary",
            },
            "D5-A2 coefficient artifact",
        )
        expected_path = (
            f"{D5_A2_COEFFICIENT_DIRECTORY}/{order_index:02d}-"
            f"{family}-{side}-{metric}.v1.f64le.bin"
        )
        artifact = _a2_binary_record(
            record.get("artifact"),
            expected_path=expected_path,
            expected_role="local_patch_surface_flux_coefficients",
            expected_value_count=D5_PATCHES_PER_PLANT,
            expected_shape=[D5_PATCHES_PER_PLANT],
            expected_units=A2_COEFFICIENT_UNITS,
            expected_ordering="canonical local_patch_index 0..191",
        )
        if (
            record.get("schema_id") != D5_A2_COEFFICIENT_SCHEMA_ID
            or record.get("schema_version") != D5_A2_COEFFICIENT_SCHEMA_VERSION
            or record.get("order_index") != order_index
            or record.get("family") != family
            or record.get("side") != side
            or record.get("metric") != metric
        ):
            raise OptimizedCalibrationPromotionError(
                "D5-A2 coefficient artifact identity is reordered or substituted."
            )
        data = _authorized_bytes(root, artifact, f"coefficient {order_index}")
        values = struct.unpack(f"<{D5_PATCHES_PER_PLANT}d", data)
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise OptimizedCalibrationPromotionError(
                "D5-A2 coefficient contains a negative or non-finite value."
            )
        if record.get("distribution_summary") != _a2_distribution_summary(values):
            raise OptimizedCalibrationPromotionError(
                "D5-A2 coefficient distribution summary is invalid."
            )
        _validate_fit_analysis(
            fit_value,
            identity,
            values,
            achieved_levels=achieved_by_family[family],
        )
        individual.append(data)
        individual_hashes.append(str(artifact["sha256"]))

    combined_record = _mapping(
        manifest.get("combined_canonical_payload"),
        "D5-A2 combined coefficient payload",
    )
    _require_fields(
        combined_record,
        {"schema_id", "schema_version", "artifact", "constituent_sha256_order"},
        "D5-A2 combined coefficient payload",
    )
    combined_artifact = _a2_binary_record(
        combined_record.get("artifact"),
        expected_path=(
            f"{D5_A2_COEFFICIENT_DIRECTORY}/{D5_A2_COMBINED_COEFFICIENT_NAME}"
        ),
        expected_role="combined_canonical_local_patch_surface_flux_coefficients",
        expected_value_count=A2_COEFFICIENT_VALUE_COUNT,
        expected_shape=[
            len(D5_QUALITY_ORDER),
            len(A2_COEFFICIENT_CHANNEL_ORDER),
            D5_PATCHES_PER_PLANT,
        ],
        expected_units=A2_COEFFICIENT_UNITS,
        expected_ordering=COEFFICIENT_ORDERING,
    )
    if (
        combined_record.get("schema_id") != D5_A2_COEFFICIENT_SCHEMA_ID
        or combined_record.get("schema_version")
        != D5_A2_COEFFICIENT_SCHEMA_VERSION
        or combined_record.get("constituent_sha256_order") != individual_hashes
    ):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 combined coefficient authority is invalid."
        )
    combined = _authorized_bytes(root, combined_artifact, "combined coefficients")
    if combined != b"".join(individual):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 individual arrays do not concatenate byte-for-byte into "
            "the authenticated combined payload."
        )
    if len(combined) != A2_COMBINED_COEFFICIENT_BYTES:
        raise OptimizedCalibrationPromotionError(
            "D5-A2 combined coefficient payload length changed."
        )
    return tuple(individual), combined


def _validate_fit_analysis(
    value: object,
    identity: tuple[str, str, str],
    coefficients: Sequence[float],
    *,
    achieved_levels: Sequence[float],
) -> None:
    record = _mapping(value, "D5-A2 family/side/metric analysis")
    _require_fields(
        record,
        {
            "family",
            "side",
            "metric",
            "distribution_summary",
            "local_patch_fits",
        },
        "D5-A2 family/side/metric analysis",
    )
    family, side, metric = identity
    fits = record.get("local_patch_fits")
    if (
        record.get("family") != family
        or record.get("side") != side
        or record.get("metric") != metric
        or not isinstance(record.get("distribution_summary"), Mapping)
        or not isinstance(fits, list)
        or len(fits) != D5_PATCHES_PER_PLANT
    ):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 coefficient fit analysis identity is invalid."
        )
    if record.get("distribution_summary") != _a2_distribution_summary(coefficients):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 fit-analysis distribution does not match its coefficients."
        )
    for index, (fit_value, coefficient) in enumerate(
        zip(fits, coefficients, strict=True)
    ):
        fit = _mapping(fit_value, "D5-A2 local-patch fit")
        _require_fields(
            fit,
            {
                "local_patch_index",
                "coefficient_gamma",
                "coefficient_units",
                "through_origin_equation",
                "r_squared",
                "r_squared_definition",
                "relative_ratio_drift",
                "relative_ratio_drift_definition",
                "residual_sum_squares",
                "total_sum_squares_centered",
                "levels",
                "promotion_eligible",
                "promotion_reasons",
            },
            "D5-A2 local-patch fit",
        )
        if (
            fit.get("local_patch_index") != index
            or fit.get("coefficient_gamma") != coefficient
            or fit.get("coefficient_units") != A2_COEFFICIENT_UNITS
            or fit.get("promotion_eligible") is not True
            or fit.get("promotion_reasons") != []
        ):
            raise OptimizedCalibrationPromotionError(
                "D5-A2 local-patch fit does not authenticate its coefficient."
            )
        _validate_fit_numbers(
            fit, coefficient, achieved_levels=achieved_levels
        )


def _validate_fit_numbers(
    fit: Mapping[str, object],
    coefficient: float,
    *,
    achieved_levels: Sequence[float],
) -> None:
    if (
        fit.get("through_origin_equation")
        != "gamma = sum_l(R_l * mean_q_l) / sum_l(R_l^2)"
        or fit.get("r_squared_definition")
        != (
            "1 - sum((mean_q_l - gamma*R_l)^2) / "
            "sum((mean_q_l - mean(mean_q))^2)"
        )
        or fit.get("relative_ratio_drift_definition")
        != (
            "(max(mean_q_l/R_l) - min(mean_q_l/R_l)) / "
            "mean(mean_q_l/R_l); exact zero mean maps to exact zero drift"
        )
    ):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 local-patch regression convention changed."
        )
    levels = fit.get("levels")
    if not isinstance(levels, list) or len(levels) != 2:
        raise OptimizedCalibrationPromotionError(
            "D5-A2 local-patch level evidence is incomplete."
        )
    achieved: list[float] = []
    means: list[float] = []
    predictions: list[float] = []
    residuals: list[float] = []
    ratios: list[float] = []
    for expected_requested, expected_achieved, level_value in zip(
        D5_REFERENCE_LEVELS_UMOL_M2_S,
        achieved_levels,
        levels,
        strict=True,
    ):
        level = _mapping(level_value, "D5-A2 local-patch level")
        _require_fields(
            level,
            {
                "requested_reference_level_umol_m2_s",
                "achieved_reference_R_umol_m2_s",
                "mean_q_over_64_plants_umol_m2_s",
                "response_ratio_mean_q_over_R",
                "predicted_mean_q_umol_m2_s",
                "signed_residual_umol_m2_s",
            },
            "D5-A2 local-patch level",
        )
        achieved_value = level.get("achieved_reference_R_umol_m2_s")
        mean_value = level.get("mean_q_over_64_plants_umol_m2_s")
        if (
            level.get("requested_reference_level_umol_m2_s")
            != expected_requested
            or not _positive_finite(achieved_value)
            or achieved_value != expected_achieved
            or not _finite_nonnegative(mean_value)
        ):
            raise OptimizedCalibrationPromotionError(
                "D5-A2 local-patch achieved reference or mean is invalid."
            )
        achieved_number = float(achieved_value)
        mean_number = float(mean_value)
        ratio = mean_number / achieved_number
        predicted = coefficient * achieved_number
        residual = mean_number - predicted
        if (
            level.get("response_ratio_mean_q_over_R") != ratio
            or level.get("predicted_mean_q_umol_m2_s") != predicted
            or level.get("signed_residual_umol_m2_s") != residual
        ):
            raise OptimizedCalibrationPromotionError(
                "D5-A2 local-patch level arithmetic is invalid."
            )
        achieved.append(achieved_number)
        means.append(mean_number)
        ratios.append(ratio)
        predictions.append(predicted)
        residuals.append(residual)
    ratio_mean = math.fsum(ratios) / 2
    drift = (
        0.0
        if ratio_mean == 0.0
        else (max(ratios) - min(ratios)) / ratio_mean
    )
    mean_of_means = math.fsum(means) / 2
    if (
        coefficient != _through_origin_slope(achieved, means)
        or fit.get("r_squared") != _r_squared(means, predictions)
        or fit.get("relative_ratio_drift") != drift
        or fit.get("residual_sum_squares")
        != math.fsum(value * value for value in residuals)
        or fit.get("total_sum_squares_centered")
        != math.fsum((value - mean_of_means) ** 2 for value in means)
    ):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 local-patch through-origin fit evidence is invalid."
        )


def _validate_neutral_manifest(
    root: Path,
    manifest: Mapping[str, object],
    *,
    report: Mapping[str, object],
    coefficient_manifest: Mapping[str, object],
) -> None:
    _require_fields(
        manifest,
        {
            "schema_id",
            "schema_version",
            "analysis_id",
            "source_experiment_id",
            "source_completion_sha256",
            "component_type",
            "byte_order",
            "stride_bytes",
            "units",
            "equation",
            "provenance_order",
            "front_and_back_distributions_separate",
            "palette_anchors_selected",
            "artifact_count",
            "artifacts",
        },
        "D5-A2 normalized-evidence manifest",
    )
    artifacts = manifest.get("artifacts")
    if (
        manifest.get("schema_id") != D5_A2_NEUTRAL_SCHEMA_ID
        or manifest.get("schema_version") != D5_A2_NEUTRAL_SCHEMA_VERSION
        or manifest.get("analysis_id") != D5_A2_ANALYSIS_ID
        or manifest.get("source_experiment_id") != D5_EXPERIMENT_ID
        or manifest.get("source_completion_sha256")
        != report.get("source_completion_sha256")
        or manifest.get("component_type") != A2_COMPONENT_TYPE
        or manifest.get("byte_order") != A2_BYTE_ORDER
        or manifest.get("stride_bytes") != A2_FLOAT64_STRIDE_BYTES
        or manifest.get("units") != NORMALIZED_UNITS
        or manifest.get("equation")
        != "u[p,k] = q[p,k] / (gamma[family,side,metric,k] * R)"
        or manifest.get("provenance_order")
        != (
            "family Standard/Quality/Rigorous; requested level 250/500 "
            "bound to authenticated achieved R; channel front incident, "
            "front absorbed, back incident, back absorbed; plant_index 0..63; "
            "canonical local_patch_index 0..191"
        )
        or manifest.get("front_and_back_distributions_separate") is not True
        or manifest.get("palette_anchors_selected") is not False
        or manifest.get("artifact_count") != NORMALIZED_ARTIFACT_COUNT
        or not isinstance(artifacts, list)
        or len(artifacts) != NORMALIZED_ARTIFACT_COUNT
    ):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 normalized-evidence manifest is incompatible."
        )
    coefficient_hashes = {
        (
            str(record["family"]),
            str(record["side"]),
            str(record["metric"]),
        ): str(record["artifact"]["sha256"])
        for record in coefficient_manifest["coefficient_artifacts"]
    }
    jobs = {
        (str(record["family"]), float(record["requested_reference_level_umol_m2_s"])): record
        for record in report["authenticated_jobs"]
    }
    order_index = 0
    for family in D5_QUALITY_ORDER:
        for level in D5_REFERENCE_LEVELS_UMOL_M2_S:
            job = jobs[(family, level)]
            for side, metric, _channel in A2_COEFFICIENT_CHANNEL_ORDER:
                record = _mapping(
                    artifacts[order_index], "D5-A2 normalized-evidence artifact"
                )
                _require_fields(
                    record,
                    {
                        "schema_id",
                        "schema_version",
                        "order_index",
                        "family",
                        "requested_reference_level_umol_m2_s",
                        "achieved_reference_R_umol_m2_s",
                        "side",
                        "metric",
                        "source_job_id",
                        "source_receiver_sha256_by_band",
                        "coefficient_artifact_sha256",
                        "artifact",
                        "equation",
                        "defined_value_distribution",
                        "undefined_exact_zero_gamma_count",
                        "undefined_flat_indices",
                        "undefined_encoding",
                    },
                    "D5-A2 normalized-evidence artifact",
                )
                expected_path = (
                    f"{D5_A2_NEUTRAL_DIRECTORY}/{order_index:02d}-{family}-"
                    f"{_level_token(level)}-{side}-{metric}.v1.f64le.bin"
                )
                artifact = _a2_binary_record(
                    record.get("artifact"),
                    expected_path=expected_path,
                    expected_role="normalized_neutral_local_patch_evidence",
                    expected_value_count=NEUTRAL_ARRAY_VALUE_COUNT,
                    expected_shape=[D5_EXPECTED_PLANT_COUNT, D5_PATCHES_PER_PLANT],
                    expected_units=NORMALIZED_UNITS,
                    expected_ordering=(
                        "plant_index 0..63 major; canonical local_patch_index "
                        "0..191 minor"
                    ),
                )
                if (
                    record.get("schema_id") != D5_A2_NEUTRAL_SCHEMA_ID
                    or record.get("schema_version") != D5_A2_NEUTRAL_SCHEMA_VERSION
                    or record.get("order_index") != order_index
                    or record.get("family") != family
                    or record.get("requested_reference_level_umol_m2_s") != level
                    or record.get("achieved_reference_R_umol_m2_s")
                    != job["achieved_reference_R_umol_m2_s"]
                    or record.get("side") != side
                    or record.get("metric") != metric
                    or record.get("source_job_id") != job["job_id"]
                    or record.get("source_receiver_sha256_by_band")
                    != job["source_receiver_sha256_by_band"]
                    or record.get("coefficient_artifact_sha256")
                    != coefficient_hashes[(family, side, metric)]
                    or record.get("equation") != "u = q / (gamma * achieved_R)"
                    or record.get("undefined_exact_zero_gamma_count") != 0
                    or record.get("undefined_flat_indices") != []
                    or not isinstance(record.get("defined_value_distribution"), Mapping)
                    or record.get("undefined_encoding")
                    != (
                        "canonical IEEE-754 quiet NaN 0x7ff8000000000000; "
                        "used only where gamma is exactly 0.0 and u is "
                        "mathematically undefined; never interpreted as data"
                    )
                ):
                    raise OptimizedCalibrationPromotionError(
                        "D5-A2 normalized evidence provenance or order is invalid."
                    )
                data = _authorized_bytes(
                    root, artifact, f"normalized evidence {order_index}"
                )
                values = tuple(value for (value,) in struct.iter_unpack("<d", data))
                if len(data) != NEUTRAL_ARRAY_BYTES or any(
                    not math.isfinite(value) or value < 0.0 for value in values
                ):
                    raise OptimizedCalibrationPromotionError(
                        "promotion-eligible normalized evidence contains an "
                        "undefined, negative, or non-finite value."
                    )
                if record.get("defined_value_distribution") != (
                    _a2_distribution_summary(values)
                ):
                    raise OptimizedCalibrationPromotionError(
                        "D5-A2 normalized-evidence distribution is invalid."
                    )
                order_index += 1


def _candidate_manifest(authenticated: _AuthenticatedA2) -> dict[str, object]:
    payload = authenticated.combined_payload
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    report = authenticated.report
    completion = authenticated.completion
    arrays = [
        {
            "order_index": order_index,
            "family": family,
            "side": side,
            "metric": metric,
            "byte_offset": order_index * COEFFICIENT_ARRAY_BYTES,
            "byte_length": COEFFICIENT_ARRAY_BYTES,
            "value_count": D5_PATCHES_PER_PLANT,
            "units": COEFFICIENT_UNITS,
            "slice_sha256": hashlib.sha256(
                payload[
                    order_index * COEFFICIENT_ARRAY_BYTES :
                    (order_index + 1) * COEFFICIENT_ARRAY_BYTES
                ]
            ).hexdigest(),
        }
        for order_index, (family, side, metric) in enumerate(
            expected_array_order()
        )
    ]
    source_authority = {
        "d5_a1": {
            "experiment_id": D5_EXPERIMENT_ID,
            "completion_sha256": report["source_completion_sha256"],
            "configuration_sha256": report["source_configuration_sha256"],
            "completion_created_at_utc": report["source_created_at_utc"],
        },
        "d5_a2": {
            "analysis_id": D5_A2_ANALYSIS_ID,
            "completion": _source_file_authority(
                D5_A2_COMPLETION_NAME,
                (authenticated.root / D5_A2_COMPLETION_NAME).stat().st_size,
                authenticated.completion_sha256,
            ),
            "report": _source_file_authority_from_record(completion["report"]),
            "coefficient_manifest": _source_file_authority_from_record(
                completion["coefficient_manifest"]
            ),
            "normalized_neutral_manifest": _source_file_authority_from_record(
                completion["normalized_neutral_manifest"]
            ),
            "combined_coefficient_payload": _source_file_authority(
                (
                    f"{D5_A2_COEFFICIENT_DIRECTORY}/"
                    f"{D5_A2_COMBINED_COEFFICIENT_NAME}"
                ),
                len(payload),
                payload_sha256,
            ),
            "promotion_eligible": True,
        },
    }
    return {
        "schema_id": OPTIMIZED_CALIBRATION_RESOURCE_SCHEMA_ID,
        "schema_version": OPTIMIZED_CALIBRATION_RESOURCE_SCHEMA_VERSION,
        "resource_id": OPTIMIZED_CALIBRATION_RESOURCE_ID,
        "status": "candidate",
        "packaged_resource": False,
        "production_enabled": False,
        "source_authority": source_authority,
        "scientific_identity": fixed_scientific_identity(
            authenticated.material_coefficient_authorities
        ),
        "coefficient_payload": {
            "role": "combined_canonical_local_patch_surface_flux_coefficients",
            "filename": OPTIMIZED_CALIBRATION_PAYLOAD_NAME,
            "media_type": "application/octet-stream",
            "schema": "headerless fixed-stride Float64 array",
            "component_type": A2_COMPONENT_TYPE,
            "byte_order": A2_BYTE_ORDER,
            "stride_bytes": A2_FLOAT64_STRIDE_BYTES,
            "units": COEFFICIENT_UNITS,
            "shape": COEFFICIENT_SHAPE,
            "value_count": A2_COEFFICIENT_VALUE_COUNT,
            "byte_length": COEFFICIENT_PAYLOAD_BYTES,
            "sha256": payload_sha256,
            "ordering": COEFFICIENT_ORDERING,
        },
        "arrays": arrays,
        "quality_mapping": expected_quality_mapping_payload(),
        "derivation_scope": {
            "calibrated_families": list(D5_QUALITY_ORDER),
            "direct_calibration_present": False,
            "far_red_present": False,
            "held_out_systems_present": {
                "proposed": False,
                "conventional": False,
            },
            "evaluated_system_inputs_used": False,
        },
        "byte_preservation": {
            "source_combined_payload_sha256": payload_sha256,
            "candidate_payload_sha256": payload_sha256,
            "individual_arrays_concatenated_byte_for_byte": True,
            "combined_payload_copied_without_transformation": True,
            "coefficient_values_reencoded": False,
            "coefficient_values_modified": False,
        },
        "deferred": list(DEFERRED_SCOPE),
    }


def _publish_candidate(
    output_root: Path,
    payload: bytes,
    manifest: Mapping[str, object],
) -> OptimizedCalibrationCandidatePublication:
    if len(payload) != COEFFICIENT_PAYLOAD_BYTES:
        raise OptimizedCalibrationPromotionError(
            "candidate payload has the wrong byte length."
        )
    output_root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{output_root.name}.",
            suffix=".tmp",
            dir=output_root.parent,
        )
    )
    try:
        _write_fsynced(stage / OPTIMIZED_CALIBRATION_PAYLOAD_NAME, payload)
        _write_fsynced(
            stage / OPTIMIZED_CALIBRATION_MANIFEST_NAME,
            format_optimized_calibration_resource_json(manifest).encode("utf-8"),
        )
        loaded = load_optimized_surface_flux_calibration(stage)
        if loaded.payload != payload or dict(loaded.manifest) != dict(manifest):
            raise OptimizedCalibrationPromotionError(
                "staged candidate does not reload as its authenticated source bytes."
            )
        if output_root.exists():
            raise OptimizedCalibrationPromotionError(
                "candidate output appeared during staged publication."
            )
        os.replace(stage, output_root)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return OptimizedCalibrationCandidatePublication(
        output_directory=output_root,
        manifest_path=output_root / OPTIMIZED_CALIBRATION_MANIFEST_NAME,
        payload_path=output_root / OPTIMIZED_CALIBRATION_PAYLOAD_NAME,
        manifest=manifest,
    )


def _write_fsynced(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _authorized_json(
    root: Path, record: Mapping[str, object], label: str
) -> dict[str, object]:
    path = _authorized_path(root, str(record["path"]), label)
    if path.stat().st_size != record["byte_length"] or _sha256_file(path) != record[
        "sha256"
    ]:
        raise OptimizedCalibrationPromotionError(f"{label} failed authentication.")
    return _read_json_object(path)


def _authorized_bytes(
    root: Path, record: Mapping[str, object], label: str
) -> bytes:
    path = _authorized_path(root, str(record["path"]), label)
    data = path.read_bytes()
    if len(data) != record["byte_length"] or hashlib.sha256(data).hexdigest() != record[
        "sha256"
    ]:
        raise OptimizedCalibrationPromotionError(f"{label} failed authentication.")
    return data


def _authorized_path(root: Path, relative: str, label: str) -> Path:
    posix = PurePosixPath(relative)
    if (
        not relative
        or posix.is_absolute()
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise OptimizedCalibrationPromotionError(f"{label} path is unsafe.")
    path = root.joinpath(*posix.parts)
    if path.is_symlink() or not path.is_file():
        raise OptimizedCalibrationPromotionError(f"{label} is missing or unsafe.")
    resolved_root = root.resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    if not resolved_path.is_relative_to(resolved_root):
        raise OptimizedCalibrationPromotionError(f"{label} escaped its root.")
    return resolved_path


def _artifact_authority(
    value: object, *, expected_path: str, label: str
) -> Mapping[str, object]:
    record = _mapping(value, label)
    _require_fields(record, {"path", "byte_length", "sha256"}, label)
    if (
        record.get("path") != expected_path
        or isinstance(record.get("byte_length"), bool)
        or not isinstance(record.get("byte_length"), int)
        or record["byte_length"] <= 0
        or not _valid_sha256(record.get("sha256"))
    ):
        raise OptimizedCalibrationPromotionError(f"{label} authority is invalid.")
    return record


def _a2_binary_record(
    value: object,
    *,
    expected_path: str,
    expected_role: str,
    expected_value_count: int,
    expected_shape: list[int],
    expected_units: str,
    expected_ordering: str,
) -> Mapping[str, object]:
    record = _mapping(value, "D5-A2 binary artifact")
    _require_fields(
        record,
        {
            "role",
            "path",
            "media_type",
            "schema",
            "component_type",
            "byte_order",
            "stride_bytes",
            "units",
            "value_count",
            "shape",
            "ordering",
            "byte_length",
            "sha256",
        },
        "D5-A2 binary artifact",
    )
    expected_length = expected_value_count * A2_FLOAT64_STRIDE_BYTES
    if (
        record.get("role") != expected_role
        or record.get("path") != expected_path
        or record.get("media_type") != "application/octet-stream"
        or record.get("schema") != "headerless fixed-stride Float64 array"
        or record.get("component_type") != A2_COMPONENT_TYPE
        or record.get("byte_order") != A2_BYTE_ORDER
        or record.get("stride_bytes") != A2_FLOAT64_STRIDE_BYTES
        or record.get("units") != expected_units
        or record.get("value_count") != expected_value_count
        or record.get("shape") != expected_shape
        or record.get("ordering") != expected_ordering
        or record.get("byte_length") != expected_length
        or not _valid_sha256(record.get("sha256"))
    ):
        raise OptimizedCalibrationPromotionError(
            "D5-A2 binary artifact schema, size, order, or identity is invalid."
        )
    return record


def _source_file_authority_from_record(
    record: Mapping[str, object],
) -> dict[str, object]:
    return _source_file_authority(
        Path(str(record["path"])).name,
        int(record["byte_length"]),
        str(record["sha256"]),
    )


def _source_file_authority(
    filename: str, byte_length: int, sha256: str
) -> dict[str, object]:
    return {
        "filename": filename,
        "byte_length": byte_length,
        "sha256": sha256,
    }


def _positive_finite(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
        and float(value) > 0.0
    )


def _finite_nonnegative(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise OptimizedCalibrationPromotionError(f"{label} must be an object.")
    return value


def _require_fields(
    value: Mapping[str, object], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        raise OptimizedCalibrationPromotionError(
            f"{label} field inventory is incompatible."
        )


def _level_token(value: float) -> str:
    return format(value, ".12g").replace(".", "p")


__all__ = [
    "OptimizedCalibrationCandidatePublication",
    "OptimizedCalibrationPromotionError",
    "format_optimized_calibration_resource_json",
    "promote_optimized_surface_flux_calibration",
]
