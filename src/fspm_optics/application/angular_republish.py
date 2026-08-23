"""Characterization-only recovery for completed angular experiments.

The entry point in this module treats ``cases/`` as an authenticated, immutable
input.  It can execute only fixed-source far-field characterization and
top-level finalization; complete-scene Stage A transport and uniform control
are intentionally not imported.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Callable, Final, Mapping, Sequence

from fspm_optics.application.angular_experiment import (
    CASE_RESULT_FILENAME,
    EXPERIMENT_PLAN_FILENAME,
    EXPERIMENT_SCHEMA_ID,
    EXPERIMENT_SCHEMA_VERSION,
    EXPERIMENT_SUMMARY_FILENAME,
    AngularExperimentConfig,
    AngularExperimentError,
    authoritative_polar_profiles,
    build_experiment_plan,
    build_experiment_summary,
    experiment_radiance_options,
    planned_cases,
    validate_completed_case,
)
from fspm_optics.application.angular_polar_diagram import (
    publish_revalidated_angular_polar_comparison,
)
from fspm_optics.fixtures.smd.angular_characterization import (
    CHARACTERIZATION_FILENAME,
    AngularCharacterizationConfig,
    MeasuredAngularProfile,
    execute_fixed_completed_aperture_profile,
    load_completed_aperture_characterization,
)
from fspm_optics.fixtures.smd.angular_validation import (
    AXIS_PLATEAU_BIN_CENTERS_DEG as PRESENTATION_BIN_CENTERS_DEG,
    AXIS_PLATEAU_BIN_WIDTH_DEG as PRESENTATION_BIN_WIDTH_DEG,
    AXIS_PLATEAU_RELATIVE_TOLERANCE,
    AngularProfileValidationError,
    evaluate_axis_plateau,
)
from fspm_optics.fixtures.smd.optical_stack import APERTURE_SIDE_M
from fspm_optics.fixtures.smd.source_variants import (
    ANGULAR_CAL_FILENAME,
    ANGULAR_VARIANT_KEYS,
    NATIVE_SMD_SOURCE_VARIANT,
    CompletedApertureAngularCalibration,
    get_smd_source_variant,
    measured_fwhm_deg,
    measured_optical_axis_reference_fwhm_deg,
    profile_sha256,
    source_variant_cal_text,
)
from fspm_optics.transport.basis.atomic import atomic_write_text

VALIDATION_DIRECTORY_NAME: Final = "angular-characterization-validation"
VALIDATION_FILENAME: Final = "angular-characterization-validation.json"
VALIDATION_SCHEMA_ID: Final = (
    "fspm-optics.completed-aperture-angular-characterization-validation"
)
VALIDATION_SCHEMA_VERSION: Final = 1
ORIGINAL_FWHM_MAX_DIFFERENCE_DEG: Final = 0.5
PRESENTATION_FWHM_MAX_DIFFERENCE_DEG: Final = 0.5
CONVERGENCE_FWHM_MAX_DIFFERENCE_DEG: Final = 0.25
CONVERGENCE_RMS_INTENSITY_MAX_DIFFERENCE: Final = 0.02
VALIDATION_RTRACE_THREADS: Final = 1


class AngularRepublishError(AngularExperimentError):
    """Characterization-only republish failed closed."""


@dataclass(frozen=True, slots=True)
class ValidationLevel:
    key: str
    angle_step_deg: float
    azimuth_count: int
    far_field_distance_m: float

    def to_dict(self) -> dict[str, object]:
        angular_extent = _aperture_angular_extent_deg(
            self.far_field_distance_m
        )
        return {
            "key": self.key,
            "angle_step_deg": self.angle_step_deg,
            "azimuth_count_over_one_square_symmetry_quadrant": (
                self.azimuth_count
            ),
            "far_field_distance_m": self.far_field_distance_m,
            "aperture_angular_extent_deg": angular_extent,
            "aperture_angular_extent_to_sample_interval_ratio": (
                angular_extent / self.angle_step_deg
            ),
        }


VALIDATION_LEVELS: Final = (
    ValidationLevel("distance_check", 0.125, 16, 80.0),
    ValidationLevel("sampling_check", 0.25, 8, 160.0),
    ValidationLevel("accepted", 0.125, 16, 160.0),
)

ProfileExecutor = Callable[
    [
        AngularCharacterizationConfig,
        float,
        Path,
        str | None,
    ],
    MeasuredAngularProfile,
]
DiagramPublisher = Callable[..., dict[str, object]]


def _aperture_angular_extent_deg(distance_m: float) -> float:
    return math.degrees(
        2.0 * math.atan(APERTURE_SIDE_M / (2.0 * distance_m))
    )


def evaluate_axis_plateau_contract(
    angles_deg: Sequence[float],
    radiant_intensity: Sequence[float],
    *,
    context: str = "completed-aperture profile",
) -> dict[str, object]:
    """Expose the shared physical-axis validator with republish errors."""

    try:
        return evaluate_axis_plateau(
            angles_deg, radiant_intensity, context=context
        )
    except AngularProfileValidationError as error:
        raise AngularRepublishError(str(error)) from error


def load_completed_experiment_config(
    output_directory: str | Path,
) -> AngularExperimentConfig:
    """Reconstruct the exact experiment identity without writing a plan."""

    root = Path(output_directory).expanduser().resolve()
    plan_path = root / EXPERIMENT_PLAN_FILENAME
    if plan_path.is_symlink() or not plan_path.is_file():
        raise AngularRepublishError(
            "characterization-only republish requires a safe completed plan."
        )
    payload = _read_json_mapping(plan_path, "experiment plan")
    controlled = payload.get("controlled_variables")
    if (
        payload.get("schema_id") != EXPERIMENT_SCHEMA_ID
        or payload.get("schema_version") != EXPERIMENT_SCHEMA_VERSION
        or payload.get("artifact_type") != "experiment_plan"
        or payload.get("status") != "planned"
        or not isinstance(controlled, Mapping)
    ):
        raise AngularRepublishError("experiment plan identity is incompatible.")
    try:
        target = payload["target_ppfd_umol_m2_s"]
        threads = controlled["threads"]
        quality = controlled["radiance_quality"]
        if (
            isinstance(target, bool)
            or not isinstance(target, int | float)
            or isinstance(threads, bool)
            or not isinstance(threads, int)
            or not isinstance(quality, str)
        ):
            raise TypeError
        config = AngularExperimentConfig(
            output_directory=root,
            target_ppfd_umol_m2_s=float(target),
            quality=quality,
            nthreads=threads,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise AngularRepublishError(
            "experiment plan configuration is malformed."
        ) from error
    if (
        payload.get("experiment_id") != config.experiment_id
        or controlled.get("target_mean_ppfd_umol_m2_s")
        != config.target_ppfd_umol_m2_s
        or controlled.get("radiance_options")
        != list(experiment_radiance_options(config.quality))
    ):
        raise AngularRepublishError(
            "experiment plan does not match the reconstructed experiment identity."
        )
    if payload != build_experiment_plan(config):
        raise AngularRepublishError(
            "experiment plan does not match the complete v2 plan contract."
        )
    return config


def republish_characterization_only(
    config: AngularExperimentConfig,
    *,
    profile_executor: ProfileExecutor | None = None,
    diagram_publisher: DiagramPublisher = (
        publish_revalidated_angular_polar_comparison
    ),
    progress: Callable[[str], None] = print,
) -> Path:
    """Validate fixed sources, remeasure far field, and republish top-level data."""

    root = config.output_directory
    _validate_plan_without_writing(config)
    summary_path = root / EXPERIMENT_SUMMARY_FILENAME
    if summary_path.exists() and (
        summary_path.is_symlink() or not summary_path.is_file()
    ):
        raise AngularRepublishError(
            "existing angular experiment summary is unsafe."
        )
    cases_root = root / "cases"
    before = _immutable_tree_hashes(cases_root)
    calibrations, original_profiles, characterization = (
        load_completed_aperture_characterization(
            root / "characterization" / CHARACTERIZATION_FILENAME
        )
    )
    case_results, source_authority, authenticated_cal = _authenticate_case_sources(
        config, calibrations
    )
    if _immutable_tree_hashes(cases_root) != before:
        raise AngularRepublishError(
            "case artifacts changed during source authentication."
        )

    executor = profile_executor or _default_profile_executor
    if (root / VALIDATION_DIRECTORY_NAME).exists():
        raise AngularRepublishError(
            f"{VALIDATION_DIRECTORY_NAME}/ already exists; refusing to overwrite "
            "a prior validation run."
        )
    staging = Path(
        tempfile.mkdtemp(prefix=".angular-validation-", dir=root)
    )
    try:
        raw_modular, convergence = _run_validation_levels(
            config,
            staging=staging,
            calibrations=calibrations,
            authenticated_cal=authenticated_cal,
            executor=executor,
            progress=progress,
        )
        raw_profiles = _raw_figure_profiles(
            characterization=characterization,
            original_profiles=original_profiles,
            modular_profiles=raw_modular,
            source_authority=source_authority,
        )
        presentation_profiles, transformation, curve_provenance = (
            build_presentation_profiles(raw_profiles)
        )
        validation_payload = _validation_payload(
            config=config,
            raw_profiles=raw_profiles,
            presentation_profiles=presentation_profiles,
            convergence=convergence,
            source_authority=source_authority,
            transformation=transformation,
            case_snapshot=before,
        )
        atomic_write_text(
            staging / VALIDATION_FILENAME,
            _json_text(validation_payload),
        )
        if _immutable_tree_hashes(cases_root) != before:
            raise AngularRepublishError(
                "case artifacts changed during far-field validation."
            )
        validation_root = root / VALIDATION_DIRECTORY_NAME
        validation_sha256 = _sha256_file(staging / VALIDATION_FILENAME)
        diagram = diagram_publisher(
            root,
            presentation_profiles=presentation_profiles,
            raw_profiles=raw_profiles,
            transformation=transformation,
            curve_provenance=curve_provenance,
        )
        finalization = {
            "mode": "characterization_only_republish",
            "case_result_origin": "reused_authenticated_case_results",
            "case_artifacts_recomputed": False,
            "case_artifacts_modified_during_finalization": False,
            "stage_a_executed_during_finalization": False,
            "complete_scene_transport_executed_during_finalization": False,
            "uniform_control_recomputed_during_finalization": False,
            "normalization_remeasured_during_finalization": False,
            "cal_parameters_recalibrated_during_finalization": False,
            "far_field_characterization_executed_during_finalization": True,
            "raw_validation_samples_preserved_unchanged": True,
            "authenticated_case_ids": [
                str(result["case_id"]) for result in case_results
            ],
        }
        summary = build_experiment_summary(
            config,
            characterization_payload=characterization,
            polar_profiles=presentation_profiles,
            polar_diagram=diagram,
            case_results=case_results,
            finalization_provenance=finalization,
        )
        summary.pop("summary_identity_sha256", None)
        summary["angular_profile_revalidation"] = {
            "path": f"{VALIDATION_DIRECTORY_NAME}/{VALIDATION_FILENAME}",
            "sha256": validation_sha256,
            "validation_identity_sha256": validation_payload[
                "validation_identity_sha256"
            ],
            "raw_profiles": {
                str(profile["profile_key"]): {
                    "profile_sha256": profile["profile_sha256"],
                    "measured_fwhm_deg": profile["measured_fwhm_deg"],
                    "unfiltered_raw_fwhm_deg": profile.get(
                        "unfiltered_raw_fwhm_deg",
                        profile["measured_fwhm_deg"],
                    ),
                    "validated_filtered_fwhm_deg": profile.get(
                        "validated_filtered_fwhm_deg",
                        profile["measured_fwhm_deg"],
                    ),
                    "physical_fwhm_authority": profile.get(
                        "physical_fwhm_authority",
                        "unfiltered_authoritative_profile",
                    ),
                    "axis_plateau_contract": profile.get(
                        "axis_plateau_contract"
                    ),
                }
                for profile in raw_profiles
            },
            "presentation_profiles": {
                str(profile["profile_key"]): {
                    "profile_sha256": profile["profile_sha256"],
                    "measured_fwhm_deg": profile["measured_fwhm_deg"],
                    "axis_plateau_contract": profile.get(
                        "axis_plateau_contract"
                    ),
                }
                for profile in presentation_profiles
            },
            "transformation": transformation,
            "scientific_data_transform": True,
        }
        summary["summary_identity_sha256"] = _identity(summary)
        if _immutable_tree_hashes(cases_root) != before:
            raise AngularRepublishError(
                "case artifacts changed during top-level republish."
            )
        os.replace(staging, validation_root)
        staging = validation_root
        atomic_write_text(
            summary_path,
            _json_text(summary),
        )
        if _immutable_tree_hashes(cases_root) != before:
            raise AngularRepublishError(
                "case artifacts changed while publishing the summary."
            )
        progress("Published characterization-only angular validation.")
        return summary_path
    except Exception:
        if staging.exists() and staging.name.startswith(".angular-validation-"):
            shutil.rmtree(staging)
        raise


def build_presentation_profiles(
    raw_profiles: Sequence[Mapping[str, object]],
) -> tuple[
    list[dict[str, object]],
    dict[str, object],
    dict[str, dict[str, object]],
]:
    """Apply one deterministic, sampling-grid-independent 2° bin mean."""

    output: list[dict[str, object]] = []
    provenance: dict[str, dict[str, object]] = {}
    for raw in raw_profiles:
        angles = [float(value) for value in raw["angle_deg"]]
        values = [float(value) for value in raw["normalized_radiant_intensity"]]
        key = str(raw["profile_key"])
        plateau = evaluate_axis_plateau_contract(
            angles,
            values,
            context=f"{key} presentation source",
        )
        normalized = list(
            plateau[
                "filtered_optical_axis_normalized_radiant_intensity"
            ]
        )
        fwhm = measured_optical_axis_reference_fwhm_deg(
            PRESENTATION_BIN_CENTERS_DEG, normalized
        )
        unfiltered_raw_fwhm = (
            measured_optical_axis_reference_fwhm_deg(angles, values)
            if raw.get("normalization_reference")
            == "optical_axis_intensity"
            else measured_fwhm_deg(angles, values)
        )
        validated_physical_fwhm = float(
            raw.get(
                "validated_filtered_fwhm_deg",
                unfiltered_raw_fwhm,
            )
        )
        difference = abs(fwhm - validated_physical_fwhm)
        if difference > PRESENTATION_FWHM_MAX_DIFFERENCE_DEG:
            raise AngularRepublishError(
                f"presentation FWHM differs from authenticated raw FWHM for "
                f"{key}: {difference:.6g}° exceeds "
                f"{PRESENTATION_FWHM_MAX_DIFFERENCE_DEG:.6g}°."
            )
        profile = dict(raw)
        profile.update(
            {
                "angle_deg": list(PRESENTATION_BIN_CENTERS_DEG),
                "normalized_radiant_intensity": normalized,
                "measured_fwhm_deg": fwhm,
                "profile_sha256": profile_sha256(
                    PRESENTATION_BIN_CENTERS_DEG, normalized
                ),
                "scientific_data_transform": True,
                "presentation_transform": (
                    "2_degree_centered_angular_bin_mean"
                ),
                "normalization_reference": "optical_axis_intensity",
                "axis_plateau_contract": plateau,
            }
        )
        output.append(profile)
        provenance[key] = {
            "raw_profile_sha256": raw["profile_sha256"],
            "unfiltered_raw_fwhm_deg": unfiltered_raw_fwhm,
            "validated_physical_fwhm_deg": validated_physical_fwhm,
            "validated_physical_fwhm_authority": (
                raw.get(
                    "physical_fwhm_authority",
                    "unfiltered_authoritative_profile",
                )
            ),
            "presentation_profile_sha256": profile["profile_sha256"],
            "presentation_measured_fwhm_deg": fwhm,
            "absolute_fwhm_difference_deg": difference,
            "raw_array_archive": (
                f"{VALIDATION_DIRECTORY_NAME}/{VALIDATION_FILENAME}"
            ),
            "scientific_data_transform": True,
            "axis_plateau_contract": plateau,
        }
    transformation = {
        "method": "2_degree_centered_angular_bin_mean",
        "bin_width_deg": PRESENTATION_BIN_WIDTH_DEG,
        "bin_centers_deg": list(PRESENTATION_BIN_CENTERS_DEG),
        "integration": (
            "exact mean of the piecewise-linear raw profile over each centered "
            "bin, clipped to the measured 0-to-90-degree domain"
        ),
        "post_bin_normalization": (
            "divide_all_bins_by_the_filtered_optical_axis_intensity"
        ),
        "normalization_reference": "optical_axis_intensity",
        "axis_plateau_relative_excess_tolerance": (
            AXIS_PLATEAU_RELATIVE_TOLERANCE
        ),
        "applied_consistently_to_all_four_profiles": True,
        "raw_profiles_preserved": True,
        "scientific_data_transform": True,
    }
    return output, transformation, provenance


def _run_validation_levels(
    config: AngularExperimentConfig,
    *,
    staging: Path,
    calibrations: Mapping[str, CompletedApertureAngularCalibration],
    authenticated_cal: Mapping[str, str | None],
    executor: ProfileExecutor,
    progress: Callable[[str], None],
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    measured: dict[str, dict[str, MeasuredAngularProfile]] = {}
    plateau_observations: dict[str, dict[str, dict[str, object]]] = {}
    filtered_fwhm_by_variant: dict[str, dict[str, float]] = {}
    convergence: dict[str, object] = {}
    for key in ANGULAR_VARIANT_KEYS:
        calibration = calibrations[key]
        measured[key] = {}
        plateau_observations[key] = {}
        for level in VALIDATION_LEVELS:
            progress(
                f"Far-field validation {key}: {level.key} "
                f"({level.angle_step_deg:g}°, {level.azimuth_count} azimuths, "
                f"{level.far_field_distance_m:g} m)."
            )
            level_config = AngularCharacterizationConfig(
                output_directory=staging,
                quality=config.quality,
                nthreads=VALIDATION_RTRACE_THREADS,
                angle_step_deg=level.angle_step_deg,
                azimuth_count=level.azimuth_count,
                far_field_distance_m=level.far_field_distance_m,
                fwhm_tolerance_deg=ORIGINAL_FWHM_MAX_DIFFERENCE_DEG,
            )
            profile = executor(
                level_config,
                calibration.internal_radiance_exponent,
                staging / key / level.key,
                authenticated_cal[key],
            )
            plateau_values = (
                profile.raw_radiant_intensity
                if profile.raw_radiant_intensity is not None
                else profile.normalized_radiant_intensity
            )
            plateau_observations[key][level.key] = (
                evaluate_axis_plateau_contract(
                    profile.angles_deg,
                    plateau_values,
                    context=f"{key} {level.key}",
                )
            )
            measured[key][level.key] = profile
        accepted = measured[key]["accepted"]
        filtered_fwhm = {
            level.key: measured_optical_axis_reference_fwhm_deg(
                plateau_observations[key][level.key][
                    "filtered_angle_deg"
                ],
                plateau_observations[key][level.key][
                    "filtered_optical_axis_normalized_radiant_intensity"
                ],
            )
            for level in VALIDATION_LEVELS
        }
        filtered_fwhm_by_variant[key] = filtered_fwhm
        original_difference = abs(
            filtered_fwhm["accepted"]
            - calibration.measured_completed_aperture_fwhm_deg
        )
        level_records = [
            {
                **level.to_dict(),
                "rtrace_threads": VALIDATION_RTRACE_THREADS,
                "azimuth_quadrature": (
                    "square_symmetry_endpoint_trapezoid"
                ),
                "raw_profile_sha256": measured[key][
                    level.key
                ].profile_sha256,
                "raw_fwhm_deg": measured[key][level.key].measured_fwhm_deg,
                "filtered_fwhm_deg": filtered_fwhm[level.key],
                "workspace_hashes": _immutable_tree_hashes(
                    staging / key / level.key
                ),
                "axis_plateau_contract": plateau_observations[key][
                    level.key
                ],
            }
            for level in VALIDATION_LEVELS
        ]
        checks: list[dict[str, object]] = []
        for comparison_key in ("distance_check", "sampling_check"):
            comparison = measured[key][comparison_key]
            raw_fwhm_difference = abs(
                accepted.measured_fwhm_deg
                - comparison.measured_fwhm_deg
            )
            filtered_fwhm_difference = abs(
                filtered_fwhm["accepted"]
                - filtered_fwhm[comparison_key]
            )
            rms = _profile_rms_difference(accepted, comparison)
            passed = (
                filtered_fwhm_difference
                <= CONVERGENCE_FWHM_MAX_DIFFERENCE_DEG
                and rms <= CONVERGENCE_RMS_INTENSITY_MAX_DIFFERENCE
            )
            checks.append(
                {
                    "accepted_level": "accepted",
                    "comparison_level": comparison_key,
                    "accepted_raw_fwhm_deg": accepted.measured_fwhm_deg,
                    "comparison_raw_fwhm_deg": comparison.measured_fwhm_deg,
                    "absolute_raw_fwhm_difference_deg": raw_fwhm_difference,
                    "filtered_fwhm_difference_threshold_deg": (
                        CONVERGENCE_FWHM_MAX_DIFFERENCE_DEG
                    ),
                    "accepted_filtered_fwhm_deg": filtered_fwhm[
                        "accepted"
                    ],
                    "comparison_filtered_fwhm_deg": filtered_fwhm[
                        comparison_key
                    ],
                    "absolute_filtered_fwhm_difference_deg": (
                        filtered_fwhm_difference
                    ),
                    "profile_error_metric": (
                        "rms_optical_axis_normalized_intensity_difference_"
                        "on_accepted_grid"
                    ),
                    "profile_error_value": rms,
                    "profile_error_threshold": (
                        CONVERGENCE_RMS_INTENSITY_MAX_DIFFERENCE
                    ),
                    "passed": passed,
                }
            )
        convergence[key] = {
            "levels": level_records,
            "checks": checks,
            "original_characterization_fwhm_deg": (
                calibration.measured_completed_aperture_fwhm_deg
            ),
            "accepted_validated_raw_fwhm_deg": accepted.measured_fwhm_deg,
            "accepted_validated_filtered_fwhm_deg": filtered_fwhm[
                "accepted"
            ],
            "physical_fwhm_authority": (
                "2_degree_centered_angular_bin_mean_profile"
            ),
            "absolute_original_fwhm_difference_deg": original_difference,
            "original_agreement_passed": (
                original_difference <= ORIGINAL_FWHM_MAX_DIFFERENCE_DEG
            ),
        }
        failed_checks = [
            check for check in checks if check["passed"] is not True
        ]
        if failed_checks:
            raise AngularRepublishError(
                f"far-field validation did not converge for {key}. "
                "Complete convergence diagnostics: "
                + json.dumps(
                    convergence[key],
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        if original_difference > ORIGINAL_FWHM_MAX_DIFFERENCE_DEG:
            raise AngularRepublishError(
                f"validated filtered FWHM disagrees with original "
                f"characterization "
                f"for {key}: {original_difference:.6g}° exceeds "
                f"{ORIGINAL_FWHM_MAX_DIFFERENCE_DEG:.6g}°. "
                "Complete convergence diagnostics: "
                + json.dumps(
                    convergence[key],
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
    raw = {
        key: {
            **measured[key]["accepted"].to_dict(),
            "profile_key": key,
            "normalization_reference": "optical_axis_intensity",
            "unfiltered_raw_fwhm_deg": measured[key][
                "accepted"
            ].measured_fwhm_deg,
            "validated_filtered_fwhm_deg": filtered_fwhm_by_variant[key][
                "accepted"
            ],
            "physical_fwhm_authority": (
                "2_degree_centered_angular_bin_mean_profile"
            ),
            "axis_plateau_contract": plateau_observations[key][
                "accepted"
            ],
        }
        for key in ANGULAR_VARIANT_KEYS
    }
    return raw, convergence


def _authenticate_case_sources(
    config: AngularExperimentConfig,
    calibrations: Mapping[str, CompletedApertureAngularCalibration],
) -> tuple[
    list[dict[str, object]],
    dict[str, dict[str, object]],
    dict[str, str | None],
]:
    results: list[dict[str, object]] = []
    authority: dict[str, dict[str, object]] = {
        key: {"variant": key, "cases": []} for key in ANGULAR_VARIANT_KEYS
    }
    cal_by_variant: dict[str, str | None] = {}
    for case in planned_cases(config):
        case_root = config.output_directory / "cases" / case.case_id
        result = validate_completed_case(
            case_root / CASE_RESULT_FILENAME,
            experiment_id=config.experiment_id,
            expected_case=case,
        )
        results.append(result)
        identities = result["identities"]
        artifact_hashes = result["artifact_hashes"]
        case_record = {
            "case_id": case.case_id,
            "case_result_identity_sha256": result[
                "case_result_identity_sha256"
            ],
            "source_identity_sha256": identities["source_identity_sha256"],
            "complete_scene_source_sha256": artifact_hashes[
                "complete-scene/uniform_complete_source.rad"
            ],
            "angular_cal_sha256": identities["angular_cal_sha256"],
        }
        authority[case.variant_key]["cases"].append(case_record)
        cal_path = case_root / "complete-scene" / ANGULAR_CAL_FILENAME
        if case.variant_key == NATIVE_SMD_SOURCE_VARIANT:
            if cal_path.exists():
                raise AngularRepublishError(
                    f"native case unexpectedly has a CAL artifact: {case.case_id}."
                )
            actual_cal = None
        else:
            if cal_path.is_symlink() or not cal_path.is_file():
                raise AngularRepublishError(
                    f"altered case CAL is missing or unsafe: {case.case_id}."
                )
            actual_cal = cal_path.read_text(encoding="utf-8")
            if _sha256_bytes(actual_cal.encode("utf-8")) != artifact_hashes[
                f"complete-scene/{ANGULAR_CAL_FILENAME}"
            ]:
                raise AngularRepublishError(
                    f"altered case CAL hash disagrees: {case.case_id}."
                )
        previous = cal_by_variant.get(case.variant_key)
        if case.variant_key in cal_by_variant and previous != actual_cal:
            raise AngularRepublishError(
                f"completed cases disagree on exact CAL bytes for "
                f"{case.variant_key}."
            )
        cal_by_variant[case.variant_key] = actual_cal
    for key in ANGULAR_VARIANT_KEYS:
        expected_cal = source_variant_cal_text(
            get_smd_source_variant(key), calibrations[key]
        )
        if cal_by_variant.get(key) != expected_cal:
            raise AngularRepublishError(
                f"authenticated case CAL does not match characterization for {key}."
            )
        authority[key]["exact_cal_sha256"] = (
            None
            if expected_cal is None
            else _sha256_bytes(expected_cal.encode("utf-8"))
        )
        authority[key]["all_case_source_artifacts_hash_validated"] = True
        authority[key]["source_artifacts_recomputed"] = False
    return results, authority, cal_by_variant


def _raw_figure_profiles(
    *,
    characterization: Mapping[str, object],
    original_profiles: Mapping[str, Mapping[str, object]],
    modular_profiles: Mapping[str, Mapping[str, object]],
    source_authority: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    # Reuse the established profile/title/source-boundary builder, then replace
    # only its modular arrays with the newly validated raw arrays.
    seed = authoritative_polar_profiles(
        characterization_payload=characterization,
        modular_profiles=original_profiles,
    )
    seeded = {str(profile["profile_key"]): profile for profile in seed}
    output = [dict(seeded["conventional"])]
    for key in ANGULAR_VARIANT_KEYS:
        profile = dict(seeded[key])
        profile.update(dict(modular_profiles[key]))
        profile["source_authority"] = {
            **dict(profile["source_authority"]),
            "validation_source_artifacts": dict(source_authority[key]),
            "arrays": "accepted_higher_sampling_far_field_validation",
        }
        output.append(profile)
    return output


def _validation_payload(
    *,
    config: AngularExperimentConfig,
    raw_profiles: Sequence[Mapping[str, object]],
    presentation_profiles: Sequence[Mapping[str, object]],
    convergence: Mapping[str, object],
    source_authority: Mapping[str, object],
    transformation: Mapping[str, object],
    case_snapshot: Mapping[str, str],
) -> dict[str, object]:
    validation_settings = {
        "levels": [level.to_dict() for level in VALIDATION_LEVELS],
        "accepted_level": "accepted",
        "quality": config.quality,
        "radiance_options": list(experiment_radiance_options(config.quality)),
        "experiment_plan_threads": config.nthreads,
        "validation_rtrace_threads": VALIDATION_RTRACE_THREADS,
        "validation_thread_rationale": (
            "single-worker rtrace is required for byte-repeatable far-field "
            "irradiance profiles; Stage A thread settings are unchanged"
        ),
        "azimuth_quadrature": {
            "method": "square_symmetry_endpoint_trapezoid",
            "domain_deg": [0.0, 90.0],
            "endpoint_weights": "half_weight",
            "interior_weights": "full_weight",
            "unit_sum": True,
            "rationale": (
                "endpoint-aware quadrature integrates the square-symmetry "
                "boundaries without coherent midpoint visibility aliasing"
            ),
        },
        "deterministic_sampling": True,
        "convergence_fwhm_max_difference_deg": (
            CONVERGENCE_FWHM_MAX_DIFFERENCE_DEG
        ),
        "convergence_rms_intensity_max_difference": (
            CONVERGENCE_RMS_INTENSITY_MAX_DIFFERENCE
        ),
        "original_fwhm_max_difference_deg": (
            ORIGINAL_FWHM_MAX_DIFFERENCE_DEG
        ),
        "axis_plateau_relative_excess_tolerance": (
            AXIS_PLATEAU_RELATIVE_TOLERANCE
        ),
        "axis_plateau_filter": "2_degree_centered_angular_bin_mean",
        "axis_plateau_normalization_reference": (
            "filtered_optical_axis_intensity"
        ),
        "far_field_distance_selection": {
            "aperture_side_m": APERTURE_SIDE_M,
            "angular_extent_equation": (
                "2 * atan(aperture_side_m / (2 * distance_m))"
            ),
            "superseded_pair": {
                "distance_check_m": 20.0,
                "distance_check_aperture_angular_extent_deg": (
                    _aperture_angular_extent_deg(20.0)
                ),
                "accepted_m": 40.0,
                "accepted_aperture_angular_extent_deg": (
                    _aperture_angular_extent_deg(40.0)
                ),
                "reason_superseded": (
                    "finite aperture angular extents exceeded the 0.125 "
                    "degree accepted validation interval"
                ),
            },
            "selected_pair": {
                "distance_check_m": 80.0,
                "distance_check_aperture_angular_extent_deg": (
                    _aperture_angular_extent_deg(80.0)
                ),
                "accepted_m": 160.0,
                "accepted_aperture_angular_extent_deg": (
                    _aperture_angular_extent_deg(160.0)
                ),
            },
            "rationale": (
                "80 m places the full 0.126 m aperture below one 0.125 "
                "degree validation interval; 160 m halves that remaining "
                "finite-distance angular extent for the accepted and "
                "sampling-check measurements"
            ),
        },
    }
    validation_settings["settings_identity_sha256"] = _identity(
        validation_settings
    )
    payload: dict[str, object] = {
        "schema_id": VALIDATION_SCHEMA_ID,
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "status": "completed",
        "experiment_id": config.experiment_id,
        "operation": "characterization_only_republish",
        "execution_scope": {
            "fixed_source_far_field_completed_aperture_measurement_only": True,
            "stage_a_executed": False,
            "complete_scene_trace_executed": False,
            "room_radiance_executed": False,
            "uniform_control_recomputed": False,
            "cal_parameters_recalibrated": False,
            "transport_normalization_remeasured": False,
            "post_trace_scaling": False,
            "raw_validation_samples_preserved_unchanged": True,
        },
        "validation_settings": validation_settings,
        "authenticated_sources": dict(source_authority),
        "case_tree_before_sha256": _identity(case_snapshot),
        "case_artifact_count": len(case_snapshot),
        "convergence": dict(convergence),
        "raw_profiles": [dict(profile) for profile in raw_profiles],
        "presentation_profiles": [
            dict(profile) for profile in presentation_profiles
        ],
        "transformation": dict(transformation),
        "scientific_data_transform": True,
    }
    payload["validation_identity_sha256"] = _identity(payload)
    return payload


def _default_profile_executor(
    config: AngularCharacterizationConfig,
    exponent: float,
    directory: Path,
    authenticated_cal_text: str | None,
) -> MeasuredAngularProfile:
    return execute_fixed_completed_aperture_profile(
        config,
        exponent=exponent,
        evaluation_directory=directory,
        authenticated_cal_text=authenticated_cal_text,
    )


def _profile_rms_difference(
    accepted: MeasuredAngularProfile,
    comparison: MeasuredAngularProfile,
) -> float:
    squared = [
        (
            value
            - _interpolate(
                comparison.angles_deg,
                comparison.normalized_radiant_intensity,
                angle,
            )
        )
        ** 2
        for angle, value in zip(
            accepted.angles_deg,
            accepted.normalized_radiant_intensity,
            strict=True,
        )
    ]
    return math.sqrt(math.fsum(squared) / len(squared))


def _interpolate(
    angles: Sequence[float],
    values: Sequence[float],
    target: float,
) -> float:
    if target <= angles[0]:
        return float(values[0])
    for left, right, value_left, value_right in zip(
        angles[:-1], angles[1:], values[:-1], values[1:], strict=True
    ):
        if target <= right:
            fraction = (target - left) / (right - left)
            return float(value_left) + fraction * (
                float(value_right) - float(value_left)
            )
    return float(values[-1])


def _validate_plan_without_writing(config: AngularExperimentConfig) -> None:
    loaded = load_completed_experiment_config(config.output_directory)
    if loaded != config:
        raise AngularRepublishError(
            "requested republish configuration differs from the completed plan."
        )


def _immutable_tree_hashes(root: Path) -> dict[str, str]:
    resolved_root = root.resolve()
    if root.is_symlink() or not root.is_dir():
        raise AngularRepublishError("completed cases directory is missing or unsafe.")
    inventory: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AngularRepublishError(
                f"completed cases contain an unsafe symlink: {path}."
            )
        if path.is_file():
            resolved = path.resolve()
            if not resolved.is_relative_to(resolved_root):
                raise AngularRepublishError(
                    f"completed case artifact escapes cases/: {path}."
                )
            inventory[resolved.relative_to(resolved_root).as_posix()] = (
                _sha256_file(resolved)
            )
    if not inventory:
        raise AngularRepublishError("completed cases directory is empty.")
    return inventory


def _read_json_mapping(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AngularRepublishError(f"{label} is unreadable.") from error
    if not isinstance(payload, dict):
        raise AngularRepublishError(f"{label} is malformed.")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(payload: object) -> str:
    return _sha256_bytes(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def _json_text(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
