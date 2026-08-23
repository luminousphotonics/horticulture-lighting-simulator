"""Plan and execute the completed-aperture Stage A angular experiment."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Final, Mapping, Sequence

import numpy as np

from fspm_optics import __version__
from fspm_optics.application.angular_polar_diagram import (
    POLAR_PNG_FILENAME,
    POLAR_PNG_HEIGHT,
    POLAR_PNG_WIDTH,
    POLAR_SVG_FILENAME,
    POLAR_SVG_HEIGHT,
    POLAR_SVG_WIDTH,
    AngularPolarDiagramError,
    authenticate_angular_polar_comparison,
    publish_angular_polar_comparison,
)
from fspm_optics.application.spatial_uniformity import compute_spatial_uniformity
from fspm_optics.application.target_control import (
    apply_target_control,
    derive_uniform_full_output_schedule,
)
from fspm_optics.fixtures.conventional_led.angular import (
    normalize_lm63_angular_distribution,
)
from fspm_optics.fixtures.conventional_led.lm63 import load_approved_lm63
from fspm_optics.fixtures.conventional_led.resources import (
    CONVENTIONAL_IES_SHA256,
)
from fspm_optics.fixtures.smd.angular_characterization import (
    CHARACTERIZATION_DIRECTION_REPLICATE_COUNT,
    CHARACTERIZATION_FILENAME,
    DEFAULT_ANGLE_STEP_DEG,
    DEFAULT_AZIMUTH_COUNT,
    DEFAULT_FAR_FIELD_DISTANCE_M,
    AngularCharacterizationConfig,
    execute_completed_aperture_characterization,
    load_completed_aperture_characterization,
)
from fspm_optics.fixtures.smd.optical_stack import (
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    PROPOSED_FIXTURE_OPTICAL_STACK_ID,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.radiance_writer import SmdEmitterAssumptions
from fspm_optics.fixtures.smd.source_variants import (
    ALTERED_FWHM_TOLERANCE_DEG,
    ANGULAR_MODIFIER_NAME,
    ANGULAR_VARIANT_KEYS,
    NARROW_SMD_SOURCE_VARIANT,
    NATIVE_SMD_SOURCE_VARIANT,
    REFERENCE_FLUX_RELATIVE_TOLERANCE,
    WIDE_SMD_SOURCE_VARIANT,
    axisymmetric_integrated_flux,
    measured_fwhm_deg,
    normalized_profile,
    profile_sha256,
)
from fspm_optics.geometry.mounting import MountingGeometry
from fspm_optics.geometry.room import (
    DEFAULT_ROOM_HEIGHT_M,
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    RoomDimensions,
    production_room_model_payload,
)
from fspm_optics.geometry.sensor_grid import (
    AdaptiveSensorGrid,
    AdaptiveSensorGridPolicy,
    BASELINE_REFERENCE_PLANE_Z_M,
    build_adaptive_sensor_grid,
    generate_sensor_points,
)
from fspm_optics.radiance.options import radiance_options
from fspm_optics.radiance.runner import LocalRunner
from fspm_optics.transport.basis.atomic import atomic_write_text
from fspm_optics.transport.proposed_uniform import (
    UNIFORM_STAGE_A_SCHEMA_ID,
    UNIFORM_STAGE_A_SCHEMA_VERSION,
    UniformStageAPlan,
    execute_uniform_proposed_stage_a,
    materialize_uniform_proposed_stage_a,
    plan_uniform_proposed_stage_a,
)
from fspm_optics.transport.scalar_ppfd import PpfdMapSample, format_ppfd_map

EXPERIMENT_SCHEMA_ID: Final = (
    "fspm-optics.completed-aperture-angular-sensitivity-experiment"
)
EXPERIMENT_SCHEMA_VERSION: Final = 2
EXPERIMENT_PLAN_FILENAME: Final = "angular-experiment-plan.json"
EXPERIMENT_SUMMARY_FILENAME: Final = "angular-experiment-summary.json"
CASE_RESULT_FILENAME: Final = "case-result.json"
CASE_METRICS_FILENAME: Final = "metrics.json"
CASE_CONTROL_FILENAME: Final = "uniform-control.json"
CASE_PPFD_FILENAME: Final = "ppfd.csv"
EXPERIMENT_SYSTEM_ID: Final = "proposed"
EXPERIMENT_DISPLAY_SYSTEM_NAME: Final = "Modularized"
EXPERIMENT_MOUNTING_HEIGHT_IN: Final = 18.0
EXPERIMENT_TARGET_SENSOR_SPACING_M: Final = 0.145
EXPERIMENT_PROPOSED_LAYOUT_MODE: Final = "standalone_modules"
EXPERIMENT_PROPOSED_RING_MODE: Final = "reduced_one_ring"
EXPERIMENT_PROPOSED_CONTROL_MODE: Final = "uniform_module_dimming"
EXPERIMENT_GRID_POLICY_ID: Final = (
    "completed_aperture_angular_experiment_cell_centered_0p145m_v1"
)
EXPERIMENT_CLAIM_SCOPE: Final = (
    "This Stage A-only experiment tests robustness of the Modularized "
    "topology-plus-control strategy for horizontal reference-plane PPFD "
    "uniformity only. It does not test plant geometry, leaf optics, "
    "multispectral transport, or absorbed-light performance."
)
ROOM_CASES: Final = ((10.0, 10.0), (30.0, 50.0))
DISPLAY_TITLES: Final = {
    "conventional": "Conventional LED",
    NATIVE_SMD_SOURCE_VARIANT: "Modularized System",
    WIDE_SMD_SOURCE_VARIANT: "Modularized-Altered (140° FWHM)",
    NARROW_SMD_SOURCE_VARIANT: "Modularized-Altered (100° FWHM)",
}


class AngularExperimentError(RuntimeError):
    """The experiment could not safely plan, execute, resume, or publish."""


@dataclass(frozen=True, slots=True)
class AngularExperimentConfig:
    output_directory: Path
    target_ppfd_umol_m2_s: float
    quality: str = "standard"
    nthreads: int = 1

    def __post_init__(self) -> None:
        output = Path(self.output_directory).expanduser()
        if not output.is_absolute():
            output = Path.cwd() / output
        object.__setattr__(self, "output_directory", output.resolve())
        target = _positive(
            "target_ppfd_umol_m2_s", self.target_ppfd_umol_m2_s
        )
        object.__setattr__(self, "target_ppfd_umol_m2_s", target)
        if (
            isinstance(self.nthreads, bool)
            or not isinstance(self.nthreads, int)
            or self.nthreads <= 0
        ):
            raise ValueError("nthreads must be a positive integer.")
        quality = str(self.quality).strip().lower()
        if quality not in {"direct", "standard", "quality", "rigorous"}:
            raise ValueError(
                "quality must be direct, standard, quality, or rigorous."
            )
        object.__setattr__(self, "quality", quality)

    @property
    def experiment_id(self) -> str:
        return "completed-aperture-angular-v2-" + _identity(
            {
                "schema_id": EXPERIMENT_SCHEMA_ID,
                "schema_version": EXPERIMENT_SCHEMA_VERSION,
                "system_id": EXPERIMENT_SYSTEM_ID,
                "target_ppfd_umol_m2_s": self.target_ppfd_umol_m2_s,
                "quality": self.quality,
                "radiance_options": list(experiment_radiance_options(self.quality)),
                "nthreads": self.nthreads,
                "mounting_height_in": EXPERIMENT_MOUNTING_HEIGHT_IN,
                "target_sensor_spacing_m": EXPERIMENT_TARGET_SENSOR_SPACING_M,
                "rooms_ft": [list(room) for room in ROOM_CASES],
                "variants": list(ANGULAR_VARIANT_KEYS),
                "proposed_layout_mode": EXPERIMENT_PROPOSED_LAYOUT_MODE,
                "proposed_ring_mode": EXPERIMENT_PROPOSED_RING_MODE,
                "proposed_control_mode": EXPERIMENT_PROPOSED_CONTROL_MODE,
                "basis_matrix_solver_enabled": False,
                "complete_scene_trace_count_per_case": 1,
                "basis_column_count_per_case": 0,
                "characterization_direction_replicate_count": (
                    CHARACTERIZATION_DIRECTION_REPLICATE_COUNT
                ),
                "characterization_direction_replicate_aggregation": (
                    "per_direction_median"
                ),
            }
        )[:24]


@dataclass(frozen=True, slots=True)
class AngularExperimentCase:
    order: int
    case_id: str
    room_length_ft: float
    room_width_ft: float
    variant_key: str
    expected_resolution_x: int
    expected_resolution_y: int
    module_count: int
    radiance_quality: str
    target_ppfd_umol_m2_s: float
    planned_complete_scene_identity: str
    planned_uniform_control_identity: str

    def to_dict(self) -> dict[str, object]:
        return {
            "order": self.order,
            "case_id": self.case_id,
            "stage": "A",
            "system_id": EXPERIMENT_SYSTEM_ID,
            "room_dimensions_ft": {
                "length": self.room_length_ft,
                "width": self.room_width_ft,
            },
            "variant": self.variant_key,
            "proposed_layout_mode": EXPERIMENT_PROPOSED_LAYOUT_MODE,
            "proposed_ring_mode": EXPERIMENT_PROPOSED_RING_MODE,
            "proposed_control_mode": EXPERIMENT_PROPOSED_CONTROL_MODE,
            "basis_matrix_solver_enabled": False,
            "complete_scene_trace_count": 1,
            "basis_column_count": 0,
            "module_count": self.module_count,
            "radiance_quality": self.radiance_quality,
            "expected_sensor_resolution_after_long_axis_alignment": {
                "x": self.expected_resolution_x,
                "y": self.expected_resolution_y,
            },
            "planned_complete_scene_identity_sha256": (
                self.planned_complete_scene_identity
            ),
            "planned_uniform_control_identity_sha256": (
                self.planned_uniform_control_identity
            ),
        }


def experiment_sensor_grid_policy() -> AdaptiveSensorGridPolicy:
    """Return the experiment-only policy without changing production defaults."""

    return AdaptiveSensorGridPolicy(
        target_spacing_m=EXPERIMENT_TARGET_SENSOR_SPACING_M,
        min_points_x=1,
        min_points_y=1,
        min_floor_points_x=1,
        min_floor_points_y=1,
        max_points_x=0,
        max_points_y=0,
        wall_margin_m=0.005,
        module_side_m=0.0,
        align_long_axis_x=True,
    )


def resolve_experiment_grid(
    room_length_ft: float, room_width_ft: float
) -> AdaptiveSensorGrid:
    requested_room = (float(room_length_ft), float(room_width_ft))
    if requested_room not in ROOM_CASES:
        raise AngularExperimentError(
            "the angular experiment grid is defined only for the preregistered "
            "10×10 ft and 30×50 ft rooms."
        )
    mounting = MountingGeometry.resolve(EXPERIMENT_MOUNTING_HEIGHT_IN)
    layout = generate_proposed_led_layout(
        room_length_ft,
        room_width_ft,
        mount_z_m=mounting.emitting_aperture_plane_z_m,
        proposed_layout_mode=EXPERIMENT_PROPOSED_LAYOUT_MODE,
        proposed_ring_mode=EXPERIMENT_PROPOSED_RING_MODE,
    )
    room = RoomDimensions(
        layout.room_length_m,
        layout.room_width_m,
        DEFAULT_ROOM_HEIGHT_M,
    )
    grid = build_adaptive_sensor_grid(
        room,
        canopy_height_m=BASELINE_REFERENCE_PLANE_Z_M,
        policy=experiment_sensor_grid_policy(),
    )
    expected = (21, 21) if requested_room == ROOM_CASES[0] else (105, 63)
    actual = (grid.spec.resolution_x, grid.spec.resolution_y)
    if actual != expected:
        raise AngularExperimentError(
            f"experiment grid resolved to {actual}, expected {expected} after "
            "long-axis alignment."
        )
    return grid


def planned_cases(config: AngularExperimentConfig) -> tuple[AngularExperimentCase, ...]:
    cases: list[AngularExperimentCase] = []
    order = 0
    for room_length_ft, room_width_ft in ROOM_CASES:
        grid = resolve_experiment_grid(room_length_ft, room_width_ft)
        layout = generate_proposed_led_layout(
            room_length_ft,
            room_width_ft,
            proposed_layout_mode=EXPERIMENT_PROPOSED_LAYOUT_MODE,
            proposed_ring_mode=EXPERIMENT_PROPOSED_RING_MODE,
        )
        room_label = (
            f"{int(room_length_ft)}x{int(room_width_ft)}"
        )
        for variant_key in ANGULAR_VARIANT_KEYS:
            case_id = f"room-{room_label}__{variant_key.replace('_', '-')}"
            complete_scene_payload = {
                "experiment_id": config.experiment_id,
                "case_id": case_id,
                "purpose": "one_complete_equal_module_stage_a_scene",
                "variant": variant_key,
                "proposed_layout_mode": EXPERIMENT_PROPOSED_LAYOUT_MODE,
                "proposed_ring_mode": EXPERIMENT_PROPOSED_RING_MODE,
                "module_count": len(layout.modules),
                "sensor_resolution": [
                    grid.spec.resolution_x,
                    grid.spec.resolution_y,
                ],
                "radiance_quality": config.quality,
                "radiance_options": list(
                    experiment_radiance_options(config.quality)
                ),
                "threads": config.nthreads,
                "complete_scene_trace_count": 1,
                "basis_column_count": 0,
            }
            complete_scene_identity = _identity(complete_scene_payload)
            control_payload = {
                "experiment_id": config.experiment_id,
                "case_id": case_id,
                "purpose": "global_uniform_module_dimming",
                "proposed_control_mode": EXPERIMENT_PROPOSED_CONTROL_MODE,
                "planned_complete_scene_identity_sha256": (
                    complete_scene_identity
                ),
                "target_mean_ppfd_umol_m2_s": (
                    config.target_ppfd_umol_m2_s
                ),
            }
            cases.append(
                AngularExperimentCase(
                    order=order,
                    case_id=case_id,
                    room_length_ft=room_length_ft,
                    room_width_ft=room_width_ft,
                    variant_key=variant_key,
                    expected_resolution_x=grid.spec.resolution_x,
                    expected_resolution_y=grid.spec.resolution_y,
                    module_count=len(layout.modules),
                    radiance_quality=config.quality,
                    target_ppfd_umol_m2_s=config.target_ppfd_umol_m2_s,
                    planned_complete_scene_identity=complete_scene_identity,
                    planned_uniform_control_identity=_identity(control_payload),
                )
            )
            order += 1
    return tuple(cases)


def build_experiment_plan(config: AngularExperimentConfig) -> dict[str, object]:
    mounting = MountingGeometry.resolve(EXPERIMENT_MOUNTING_HEIGHT_IN)
    cases = planned_cases(config)
    if len(cases) != 6:
        raise AngularExperimentError("the Stage A matrix must contain exactly six cases.")
    if any(case.variant_key not in ANGULAR_VARIANT_KEYS for case in cases):
        raise AngularExperimentError("the experiment matrix contains a prohibited source.")
    return {
        "schema_id": EXPERIMENT_SCHEMA_ID,
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "artifact_type": "experiment_plan",
        "experiment_id": config.experiment_id,
        "status": "planned",
        "system": {
            "internal_id": EXPERIMENT_SYSTEM_ID,
            "experiment_facing_copy": EXPERIMENT_DISPLAY_SYSTEM_NAME,
        },
        "scope": {
            "stage_a_only": True,
            "stage_b_planned": False,
            "multispectral_transport": False,
            "plant_geometry": False,
            "leaf_optics": False,
            "absorbed_light_metrics": False,
            "hps_cases": False,
            "conventional_stage_a_rerun": False,
        },
        "target_ppfd_umol_m2_s": config.target_ppfd_umol_m2_s,
        "execution_contract": {
            "backend": "production_uniform_complete_scene_stage_a",
            "proposed_layout_mode": EXPERIMENT_PROPOSED_LAYOUT_MODE,
            "proposed_ring_mode": EXPERIMENT_PROPOSED_RING_MODE,
            "proposed_control_mode": EXPERIMENT_PROPOSED_CONTROL_MODE,
            "basis_matrix_solver_enabled": False,
            "complete_scene_trace_count_per_case": 1,
            "basis_column_count_per_case": 0,
            "global_dimming_factor_count_per_case": 1,
            "all_module_reference_watts_equal": True,
            "all_module_effective_watts_equal": True,
        },
        "controlled_variables": controlled_variable_contract(config, mounting),
        "angular_source_contract": {
            "fwhm_boundary": "completed_aperture_radiant_intensity",
            "ideal_intensity_law": "I(theta) proportional to cos(theta)^m",
            "ideal_intensity_exponent_equation": (
                "m = ln(0.5) / ln(cos(FWHM/2))"
            ),
            "planar_aperture_radiance_exponent_equation": "q = m - 1",
            "projected_area_cosine_included": True,
            "physical_stack_recalibration": True,
            "target_fwhm_deg": [140.0, 100.0],
            "altered_fwhm_absolute_tolerance_deg": (
                ALTERED_FWHM_TOLERANCE_DEG
            ),
            "reference_input_flux_relative_tolerance": (
                REFERENCE_FLUX_RELATIVE_TOLERANCE
            ),
            "native_fwhm_hardcoded": False,
            "altered_variants_use_native_transmission_authority": False,
            "post_trace_source_flux_scaling": False,
            "direction_replicate_count": (
                CHARACTERIZATION_DIRECTION_REPLICATE_COUNT
            ),
            "direction_replicate_aggregation": "per_direction_median",
            "sample_order": "angle_then_azimuth_then_replicate",
            "fwhm_reference": "authenticated_optical_axis_intensity",
            "axis_plateau_validation": (
                "2_degree_centered_angular_bin_mean"
            ),
        },
        "resume_policy": {
            "explicit_resume_flag_required": True,
            "completed_case_reuse_requires_hash_validation": True,
            "partial_case_reuse": False,
            "partial_case_behavior": "fail_closed",
            "complete_scene_artifact_reuse_across_cases": False,
            "uniform_control_artifact_reuse_across_cases": False,
            "ambient_cache_reuse_across_cases": False,
        },
        "polar_diagram_contract": {
            "layout": "2x2_for_16x9_presentation",
            "panel_titles_by_position": {
                "top_left": "(a) Conventional LED",
                "bottom_left": "(b) Modularized System",
                "top_right": (
                    "(c) Modularized-Altered (140° FWHM)"
                ),
                "bottom_right": (
                    "(d) Modularized-Altered (100° FWHM)"
                ),
            },
            "radial_scale": [0.0, 1.0],
            "angular_convention": "0_deg_downward_axis_plus_minus_90_horizon",
            "authoritative_vector": {
                "path": POLAR_SVG_FILENAME,
                "size_px": [POLAR_SVG_WIDTH, POLAR_SVG_HEIGHT],
            },
            "high_resolution_raster": {
                "path": POLAR_PNG_FILENAME,
                "size_px": [POLAR_PNG_WIDTH, POLAR_PNG_HEIGHT],
            },
            "hps_profile": False,
        },
        "cases": [case.to_dict() for case in cases],
    }


def controlled_variable_contract(
    config: AngularExperimentConfig,
    mounting: MountingGeometry | None = None,
) -> dict[str, object]:
    resolved_mounting = mounting or MountingGeometry.resolve(
        EXPERIMENT_MOUNTING_HEIGHT_IN
    )
    options = experiment_radiance_options(config.quality)
    return {
        "target_mean_ppfd_umol_m2_s": config.target_ppfd_umol_m2_s,
        "mounting_height": resolved_mounting.to_payload(),
        "room_height_m": DEFAULT_ROOM_HEIGHT_M,
        "room_model": {
            **production_room_model_payload(),
            "identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
            "same_for_all_six_cases": True,
        },
        "radiance_quality": config.quality,
        "radiance_options": list(options),
        "radiance_options_identity_sha256": _identity(list(options)),
        "threads": config.nthreads,
        "sensor_grid_policy": {
            "policy_id": EXPERIMENT_GRID_POLICY_ID,
            "layout": "cell_centered",
            "target_spacing_m": EXPERIMENT_TARGET_SENSOR_SPACING_M,
            "wall_margin_m": 0.005,
            "long_axis_aligned_to_x": True,
            "production_default_changed": False,
            "resolved_grids": {
                "10x10_ft": [21, 21],
                "30x50_ft_after_long_axis_alignment": [105, 63],
            },
        },
        "proposed_physical_composition_mode": (
            EXPERIMENT_PROPOSED_LAYOUT_MODE
        ),
        "proposed_ring_mode": EXPERIMENT_PROPOSED_RING_MODE,
        "proposed_control_mode": EXPERIMENT_PROPOSED_CONTROL_MODE,
        "basis_matrix_solver_enabled": False,
        "complete_scene_trace_count_per_case": 1,
        "basis_column_count_per_case": 0,
        "completed_aperture_ppe_umol_per_j": (
            COMPLETED_APERTURE_PPE_UMOL_PER_J
        ),
        "source_flux_normalization": (
            "variant-specific measured physical-stack transmission applied to "
            "internal source amplitude before Stage A transport"
        ),
        "post_trace_source_flux_scaling": False,
    }


def plan_angular_experiment(config: AngularExperimentConfig) -> Path:
    root = config.output_directory
    root.mkdir(parents=True, exist_ok=True)
    plan_path = root / EXPERIMENT_PLAN_FILENAME
    expected_text = _json_text(build_experiment_plan(config))
    if plan_path.exists():
        if (
            plan_path.is_symlink()
            or not plan_path.is_file()
            or plan_path.read_text(encoding="utf-8") != expected_text
        ):
            raise AngularExperimentError(
                "existing angular experiment plan conflicts with the requested identity."
            )
        return plan_path
    existing = tuple(root.iterdir())
    if existing:
        raise AngularExperimentError(
            "output directory is non-empty and has no compatible experiment plan."
        )
    atomic_write_text(plan_path, expected_text)
    return plan_path


def execute_angular_experiment(
    config: AngularExperimentConfig,
    *,
    resume: bool = False,
    progress: Any = print,
) -> Path:
    plan_path = plan_angular_experiment(config)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    characterization_root = config.output_directory / "characterization"
    characterization_path = characterization_root / CHARACTERIZATION_FILENAME
    characterization_reused = False
    if characterization_path.exists():
        if not resume:
            raise AngularExperimentError(
                "completed characterization already exists; pass --resume to "
                "hash-validate and reuse it."
            )
        calibrations, modular_profiles, characterization_payload = (
            load_completed_aperture_characterization(characterization_path)
        )
        _validate_characterization_config(config, characterization_payload)
        characterization_reused = True
    else:
        if characterization_root.exists() and tuple(characterization_root.iterdir()):
            raise AngularExperimentError(
                "partial angular characterization exists and cannot be resumed "
                "safely; use a fresh output directory."
            )
        characterization_path = execute_completed_aperture_characterization(
            AngularCharacterizationConfig(
                output_directory=characterization_root,
                quality=config.quality,
                nthreads=config.nthreads,
                fwhm_tolerance_deg=ALTERED_FWHM_TOLERANCE_DEG,
                reference_flux_relative_tolerance=(
                    REFERENCE_FLUX_RELATIVE_TOLERANCE
                ),
            ),
            progress=progress,
        )
        calibrations, modular_profiles, characterization_payload = (
            load_completed_aperture_characterization(characterization_path)
        )

    results: list[dict[str, object]] = []
    case_result_origins: list[dict[str, object]] = []
    cases_by_id = {case.case_id: case for case in planned_cases(config)}
    for raw_case in plan["cases"]:
        case_id = raw_case["case_id"]
        case = cases_by_id[case_id]
        case_root = config.output_directory / "cases" / case.case_id
        result_path = case_root / CASE_RESULT_FILENAME
        if result_path.exists():
            if not resume:
                raise AngularExperimentError(
                    f"case {case.case_id} already completed; pass --resume to "
                    "validate it."
                )
            result = validate_completed_case(
                result_path,
                experiment_id=config.experiment_id,
                expected_case=case,
            )
            progress(f"Validated completed case {case.case_id}.")
            case_result_origins.append(
                {
                    "case_id": case.case_id,
                    "origin": "reused_authenticated_case_result",
                    "case_artifacts_modified_during_finalization": False,
                }
            )
        else:
            if case_root.exists() and tuple(case_root.iterdir()):
                raise AngularExperimentError(
                    f"case {case.case_id} is partial and cannot be safely reused; "
                    "use a fresh output directory."
                )
            progress(
                f"Executing one complete-scene uniform Stage A trace for "
                f"{case.case_id}."
            )
            result = _execute_case(
                config,
                case=case,
                case_root=case_root,
                calibration=calibrations[case.variant_key],
            )
            case_result_origins.append(
                {
                    "case_id": case.case_id,
                    "origin": "executed_during_this_invocation",
                    "case_artifacts_modified_during_finalization": None,
                }
            )
        results.append(result)

    if len(results) != 6:
        raise AngularExperimentError("six completed case results are required.")
    polar_profiles = authoritative_polar_profiles(
        characterization_payload=characterization_payload,
        modular_profiles=modular_profiles,
    )
    summary_path = config.output_directory / EXPERIMENT_SUMMARY_FILENAME
    existing_summary: dict[str, object] | None = None
    if summary_path.exists():
        if summary_path.is_symlink() or not summary_path.is_file():
            raise AngularExperimentError(
                "existing angular experiment summary is unsafe."
            )
        try:
            raw_existing_summary = json.loads(
                summary_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as error:
            raise AngularExperimentError(
                "existing angular experiment summary is unreadable."
            ) from error
        if not isinstance(raw_existing_summary, dict):
            raise AngularExperimentError(
                "existing angular experiment summary is malformed."
            )
        existing_summary = raw_existing_summary
    finalization_provenance: Mapping[str, object]
    if existing_summary is None:
        finalization_provenance = _finalization_provenance(
            resume=resume,
            characterization_reused=characterization_reused,
            case_result_origins=case_result_origins,
        )
    else:
        raw_finalization = existing_summary.get("finalization_provenance")
        if not isinstance(raw_finalization, Mapping):
            raise AngularExperimentError(
                "existing angular experiment summary has no finalization provenance."
            )
        finalization_provenance = raw_finalization
    try:
        polar_diagram = publish_angular_polar_comparison(
            config.output_directory,
            polar_profiles,
            require_existing=summary_path.exists(),
        )
    except AngularPolarDiagramError as error:
        raise AngularExperimentError(str(error)) from error
    summary = build_experiment_summary(
        config,
        characterization_payload=characterization_payload,
        polar_profiles=polar_profiles,
        polar_diagram=polar_diagram,
        case_results=results,
        finalization_provenance=finalization_provenance,
    )
    expected_summary = _json_text(summary)
    if existing_summary is not None:
        declaration = existing_summary.get("polar_diagram")
        if not isinstance(declaration, Mapping):
            raise AngularExperimentError(
                "existing angular experiment summary has no polar diagram declaration."
            )
        try:
            authenticate_angular_polar_comparison(
                config.output_directory, declaration
            )
        except AngularPolarDiagramError as error:
            raise AngularExperimentError(str(error)) from error
        if _json_text(existing_summary) != expected_summary:
            raise AngularExperimentError(
                "existing angular experiment summary conflicts with validated cases."
            )
    else:
        atomic_write_text(summary_path, expected_summary)
    return summary_path


def validate_completed_case(
    result_path: str | Path,
    *,
    experiment_id: str,
    expected_case: AngularExperimentCase,
) -> dict[str, object]:
    """Authenticate one completed v2 uniform complete-scene case."""

    unresolved_path = Path(result_path)
    if unresolved_path.is_symlink():
        raise AngularExperimentError(
            f"completed case result is an unsafe symlink: "
            f"{expected_case.case_id}."
        )
    path = unresolved_path.resolve()
    payload = _read_case_json(path, f"completed case {expected_case.case_id}")
    if (
        payload.get("schema_id") != EXPERIMENT_SCHEMA_ID
        or payload.get("schema_version") != EXPERIMENT_SCHEMA_VERSION
        or payload.get("artifact_type") != "stage_a_case_result"
        or payload.get("status") != "completed"
        or payload.get("experiment_id") != experiment_id
        or payload.get("case_id") != expected_case.case_id
        or payload.get("variant") != expected_case.variant_key
        or payload.get("order") != expected_case.order
        or payload.get("stage") != "A"
        or payload.get("system_id") != EXPERIMENT_SYSTEM_ID
        or payload.get("proposed_layout_mode")
        != EXPERIMENT_PROPOSED_LAYOUT_MODE
        or payload.get("proposed_ring_mode") != EXPERIMENT_PROPOSED_RING_MODE
        or payload.get("proposed_control_mode")
        != EXPERIMENT_PROPOSED_CONTROL_MODE
        or payload.get("basis_matrix_solver_enabled") is not False
        or payload.get("complete_scene_trace_count") != 1
        or payload.get("basis_column_count") != 0
        or payload.get("radiance_quality") != expected_case.radiance_quality
        or payload.get("module_count") != expected_case.module_count
        or payload.get("target_mean_ppfd_umol_m2_s")
        != expected_case.target_ppfd_umol_m2_s
    ):
        raise AngularExperimentError(
            f"completed case identity is incompatible: {expected_case.case_id}."
        )

    room = payload.get("room_dimensions_ft")
    sensor_grid = payload.get("sensor_grid")
    expected_resolution = (
        expected_case.expected_resolution_x,
        expected_case.expected_resolution_y,
    )
    if (
        not isinstance(room, Mapping)
        or room.get("length") != expected_case.room_length_ft
        or room.get("width") != expected_case.room_width_ft
        or not isinstance(sensor_grid, Mapping)
        or sensor_grid.get("policy_id") != EXPERIMENT_GRID_POLICY_ID
        or (
            sensor_grid.get("resolution_x"),
            sensor_grid.get("resolution_y"),
        )
        != expected_resolution
        or sensor_grid.get("sample_count")
        != expected_resolution[0] * expected_resolution[1]
    ):
        raise AngularExperimentError(
            f"completed case room or sensor grid is incompatible: "
            f"{expected_case.case_id}."
        )

    artifact_hashes = payload.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict):
        raise AngularExperimentError("completed case artifact hashes are missing.")
    actual_inventory = _workspace_artifact_hashes(
        path.parent, exclude={CASE_RESULT_FILENAME}
    )
    if set(artifact_hashes) != set(actual_inventory):
        raise AngularExperimentError(
            f"completed case artifact inventory is incomplete or unexpected: "
            f"{expected_case.case_id}."
        )
    for relative, expected_hash in artifact_hashes.items():
        if (
            not isinstance(relative, str)
            or not _is_sha256(expected_hash)
            or actual_inventory.get(relative) != expected_hash
        ):
            raise AngularExperimentError(
                f"completed case artifact failed hash validation: "
                f"{expected_case.case_id}."
            )
    prohibited_artifacts = [
        relative
        for relative in artifact_hashes
        if "basis" in relative.lower() or "solver" in relative.lower()
    ]
    if prohibited_artifacts:
        raise AngularExperimentError(
            f"completed case declares prohibited v1 artifacts: "
            f"{expected_case.case_id}."
        )

    scene_prefix = "complete-scene/"
    required = {
        CASE_PPFD_FILENAME,
        CASE_METRICS_FILENAME,
        CASE_CONTROL_FILENAME,
        scene_prefix + "room.rad",
        scene_prefix + "sensors.pts",
        scene_prefix + "uniform_complete_source.rad",
        scene_prefix + "uniform_complete_scene.oct",
        scene_prefix + "uniform_complete_reference.rgb",
        scene_prefix + "uniform_complete_reference_ppfd.npy",
        scene_prefix + "uniform_stage_a_manifest.json",
        scene_prefix + "uniform_stage_a_execution.json",
    }
    if not required.issubset(artifact_hashes) or not any(
        relative.startswith(scene_prefix + "fixture_occlusion/")
        for relative in artifact_hashes
    ):
        raise AngularExperimentError(
            f"completed case is missing complete-scene artifacts: "
            f"{expected_case.case_id}."
        )
    cal_relative = scene_prefix + "smd_source_variant.cal"
    if expected_case.variant_key == NATIVE_SMD_SOURCE_VARIANT:
        if cal_relative in artifact_hashes:
            raise AngularExperimentError(
                "native completed case unexpectedly declares an angular CAL."
            )
    elif cal_relative not in artifact_hashes:
        raise AngularExperimentError(
            f"altered completed case is missing its angular CAL: "
            f"{expected_case.case_id}."
        )

    manifest = _read_case_json(
        _safe_case_artifact(
            path.parent, scene_prefix + "uniform_stage_a_manifest.json"
        ),
        f"complete-scene manifest {expected_case.case_id}",
    )
    execution = _read_case_json(
        _safe_case_artifact(
            path.parent, scene_prefix + "uniform_stage_a_execution.json"
        ),
        f"complete-scene execution {expected_case.case_id}",
    )
    control = _read_case_json(
        _safe_case_artifact(path.parent, CASE_CONTROL_FILENAME),
        f"uniform control {expected_case.case_id}",
    )
    identities = payload.get("identities")
    if not isinstance(identities, Mapping):
        raise AngularExperimentError("completed case identities are missing.")

    complete_scene_identity = identities.get(
        "complete_scene_identity_sha256"
    )
    expected_scene_artifact_identity = _complete_scene_artifact_identity(
        artifact_hashes
    )
    source_identity = _uniform_source_identity_from_artifacts(
        artifact_hashes
    )
    options = list(
        experiment_radiance_options(expected_case.radiance_quality)
    )
    common_scene_contract = (
        manifest.get("schema_id") == UNIFORM_STAGE_A_SCHEMA_ID
        and manifest.get("schema_version") == UNIFORM_STAGE_A_SCHEMA_VERSION
        and manifest.get("control_mode")
        == EXPERIMENT_PROPOSED_CONTROL_MODE
        and manifest.get("proposed_layout_mode")
        == EXPERIMENT_PROPOSED_LAYOUT_MODE
        and manifest.get("proposed_ring_mode")
        == EXPERIMENT_PROPOSED_RING_MODE
        and manifest.get("basis_matrix_solver_enabled") is False
        and manifest.get("complete_scene_trace_count") == 1
        and manifest.get("basis_column_count") == 0
        and manifest.get("module_count") == expected_case.module_count
        and manifest.get("sensor_count")
        == expected_resolution[0] * expected_resolution[1]
        and manifest.get("radiance_options") == options
        and payload.get("radiance_options") == options
        and manifest.get("identity_sha256") == complete_scene_identity
        and execution.get("schema_id") == UNIFORM_STAGE_A_SCHEMA_ID
        and execution.get("schema_version") == UNIFORM_STAGE_A_SCHEMA_VERSION
        and execution.get("control_mode")
        == EXPERIMENT_PROPOSED_CONTROL_MODE
        and execution.get("proposed_layout_mode")
        == EXPERIMENT_PROPOSED_LAYOUT_MODE
        and execution.get("proposed_ring_mode")
        == EXPERIMENT_PROPOSED_RING_MODE
        and execution.get("basis_matrix_solver_enabled") is False
        and execution.get("complete_scene_trace_count") == 1
        and execution.get("basis_column_count") == 0
        and execution.get("module_count") == expected_case.module_count
        and execution.get("sensor_count")
        == expected_resolution[0] * expected_resolution[1]
        and execution.get("identity_sha256") == complete_scene_identity
        and execution.get("field_sha256")
        == artifact_hashes[
            scene_prefix + "uniform_complete_reference_ppfd.npy"
        ]
        and execution.get("room_sha256")
        == artifact_hashes[scene_prefix + "room.rad"]
        and execution.get("sensor_sha256")
        == artifact_hashes[scene_prefix + "sensors.pts"]
        and execution.get("source_sha256")
        == artifact_hashes[scene_prefix + "uniform_complete_source.rad"]
    )
    if not common_scene_contract:
        raise AngularExperimentError(
            f"completed case complete-scene execution failed authentication: "
            f"{expected_case.case_id}."
        )

    _require_case_identity_field(
        "planned_complete_scene_identity_sha256",
        identities.get("planned_complete_scene_identity_sha256"),
        expected_case.planned_complete_scene_identity,
        case_id=expected_case.case_id,
        require_sha256=True,
    )
    _require_case_identity_field(
        "planned_uniform_control_identity_sha256",
        identities.get("planned_uniform_control_identity_sha256"),
        expected_case.planned_uniform_control_identity,
        case_id=expected_case.case_id,
        require_sha256=True,
    )
    _require_case_identity_field(
        "complete_scene_identity_sha256",
        complete_scene_identity,
        manifest.get("identity_sha256"),
        case_id=expected_case.case_id,
        require_sha256=True,
    )
    _require_case_identity_field(
        "complete_scene_artifact_identity_sha256",
        identities.get("complete_scene_artifact_identity_sha256"),
        expected_scene_artifact_identity,
        case_id=expected_case.case_id,
        require_sha256=True,
    )
    _require_case_identity_field(
        "source_identity_sha256",
        identities.get("source_identity_sha256"),
        source_identity,
        case_id=expected_case.case_id,
        require_sha256=True,
    )
    _require_case_identity_field(
        "angular_cal_sha256",
        identities.get("angular_cal_sha256"),
        artifact_hashes.get(cal_relative),
        case_id=expected_case.case_id,
        require_sha256=expected_case.variant_key != NATIVE_SMD_SOURCE_VARIANT,
    )
    _require_case_identity_field(
        "radiance_options_identity_sha256",
        identities.get("radiance_options_identity_sha256"),
        _identity(options),
        case_id=expected_case.case_id,
        require_sha256=True,
    )

    source_text = _safe_case_artifact(
        path.parent, scene_prefix + "uniform_complete_source.rad"
    ).read_text(encoding="utf-8")
    modifier_active = (
        f"void brightfunc {ANGULAR_MODIFIER_NAME}" in source_text
        and f"{ANGULAR_MODIFIER_NAME} light smd_control_zone_" in source_text
    )
    if modifier_active != (
        expected_case.variant_key != NATIVE_SMD_SOURCE_VARIANT
    ):
        raise AngularExperimentError(
            f"completed case emitting modifier chain is incompatible: "
            f"{expected_case.case_id}."
        )

    reference_watts = _finite_number_list(
        control.get("reference_watts_by_module"),
        expected_count=expected_case.module_count,
        context=f"reference module wattages for {expected_case.case_id}",
    )
    effective_watts = _finite_number_list(
        control.get("effective_watts_by_module"),
        expected_count=expected_case.module_count,
        context=f"effective module wattages for {expected_case.case_id}",
    )
    try:
        dimming_factor = float(control["global_dimming_factor"])
        achieved_mean = float(payload["achieved_mean_ppfd_umol_m2_s"])
        solved_power = float(payload["solved_electrical_power_w"])
        electrical_power = float(payload["electrical_power_w"])
        completed_ppf = float(payload["completed_aperture_ppf_umol_s"])
    except (KeyError, TypeError, ValueError) as error:
        raise AngularExperimentError(
            f"completed case uniform control is malformed: "
            f"{expected_case.case_id}."
        ) from error
    control_without_identity = dict(control)
    control_identity = control_without_identity.pop(
        "uniform_control_identity_sha256", None
    )
    expected_effective = tuple(
        value * dimming_factor for value in reference_watts
    )
    if (
        control.get("schema_id")
        != "fspm-optics.angular-experiment-uniform-control"
        or control.get("schema_version") != EXPERIMENT_SCHEMA_VERSION
        or control.get("experiment_id") != experiment_id
        or control.get("case_id") != expected_case.case_id
        or control.get("variant") != expected_case.variant_key
        or control.get("proposed_control_mode")
        != EXPERIMENT_PROPOSED_CONTROL_MODE
        or control.get("basis_matrix_solver_enabled") is not False
        or control.get("complete_scene_trace_count") != 1
        or control.get("basis_column_count") != 0
        or control.get("module_count") != expected_case.module_count
        or control.get("all_module_reference_watts_equal") is not True
        or control.get("all_module_effective_watts_equal") is not True
        or manifest.get("all_module_reference_watts_equal") is not True
        or execution.get("all_module_reference_watts_equal") is not True
        or manifest.get("reference_watts_per_module") != reference_watts[0]
        or execution.get("reference_watts_per_module") != reference_watts[0]
        or len(set(reference_watts)) != 1
        or len(set(effective_watts)) != 1
        or not 0.0 < dimming_factor <= 1.0
        or any(
            not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9)
            for actual, expected in zip(
                effective_watts, expected_effective, strict=True
            )
        )
        or not math.isclose(
            math.fsum(effective_watts),
            solved_power,
            rel_tol=1e-12,
            abs_tol=1e-9,
        )
        or not math.isclose(
            electrical_power, solved_power, rel_tol=0.0, abs_tol=1e-12
        )
        or control.get("target_mean_ppfd_umol_m2_s")
        != expected_case.target_ppfd_umol_m2_s
        or not math.isclose(
            achieved_mean,
            expected_case.target_ppfd_umol_m2_s,
            rel_tol=1e-9,
            abs_tol=1e-6,
        )
        or control.get("achieved_mean_ppfd_umol_m2_s") != achieved_mean
        or control.get("solved_electrical_power_w") != solved_power
        or control.get("completed_aperture_ppf_umol_s") != completed_ppf
        or control.get("completed_aperture_ppe_umol_per_j")
        != COMPLETED_APERTURE_PPE_UMOL_PER_J
        or control.get("planned_uniform_control_identity_sha256")
        != expected_case.planned_uniform_control_identity
        or control.get("complete_scene_identity_sha256")
        != complete_scene_identity
        or control.get("source_normalization_before_transport") is not True
        or control.get("post_trace_source_flux_scaling") is not False
        or control_identity != _identity(control_without_identity)
        or identities.get("uniform_control_identity_sha256")
        != control_identity
        or payload.get("global_dimming_factor") != dimming_factor
        or payload.get("reference_watts_per_module") != reference_watts[0]
        or payload.get("effective_watts_per_module") != effective_watts[0]
        or payload.get("all_module_reference_watts_equal") is not True
        or payload.get("all_module_effective_watts_equal") is not True
        or not math.isfinite(completed_ppf)
        or not math.isclose(
            completed_ppf,
            solved_power * COMPLETED_APERTURE_PPE_UMOL_PER_J,
            rel_tol=1e-12,
            abs_tol=1e-9,
        )
        or payload.get("completed_aperture_ppe_umol_per_j")
        != COMPLETED_APERTURE_PPE_UMOL_PER_J
    ):
        raise AngularExperimentError(
            f"completed case uniform control failed authentication: "
            f"{expected_case.case_id}."
        )

    identity = payload.get("case_result_identity_sha256")
    without_identity = dict(payload)
    without_identity.pop("case_result_identity_sha256", None)
    if identity != _identity(without_identity):
        raise AngularExperimentError(
            f"completed case result identity is stale: {expected_case.case_id}."
        )
    return payload


def _execute_case(
    config: AngularExperimentConfig,
    *,
    case: AngularExperimentCase,
    case_root: Path,
    calibration: Any,
    runner: Any | None = None,
) -> dict[str, object]:
    """Execute one production complete-scene trace and one uniform control."""

    case_root.mkdir(parents=True, exist_ok=False)
    mounting = MountingGeometry.resolve(EXPERIMENT_MOUNTING_HEIGHT_IN)
    layout = generate_proposed_led_layout(
        case.room_length_ft,
        case.room_width_ft,
        mount_z_m=mounting.emitting_aperture_plane_z_m,
        proposed_layout_mode=EXPERIMENT_PROPOSED_LAYOUT_MODE,
        proposed_ring_mode=EXPERIMENT_PROPOSED_RING_MODE,
    )
    grid = resolve_experiment_grid(case.room_length_ft, case.room_width_ft)
    complete_scene_plan = plan_uniform_proposed_stage_a(
        layout=layout,
        output_directory=case_root / "complete-scene",
        radiance_options=experiment_radiance_options(config.quality),
        adaptive_policy=experiment_sensor_grid_policy(),
        nthreads=config.nthreads,
        use_ambient_cache=True,
        emitter_assumptions=SmdEmitterAssumptions(
            source_variant=case.variant_key,
            completed_aperture_angular_calibration=calibration,
            allow_non_authoritative_backward_calibration=True,
        ),
    )
    actual_resolution = (
        complete_scene_plan.sensor_grid_spec.resolution_x,
        complete_scene_plan.sensor_grid_spec.resolution_y,
    )
    expected_resolution = (
        case.expected_resolution_x,
        case.expected_resolution_y,
    )
    if (
        actual_resolution != expected_resolution
        or complete_scene_plan.sensor_grid_spec != grid.spec
        or len(layout.modules) != case.module_count
        or layout.proposed_layout_mode.value
        != EXPERIMENT_PROPOSED_LAYOUT_MODE
        or layout.proposed_ring_mode.value != EXPERIMENT_PROPOSED_RING_MODE
    ):
        raise AngularExperimentError(
            f"case {case.case_id} complete-scene plan violates its pinned "
            "room, grid, or module contract."
        )

    materialize_uniform_proposed_stage_a(complete_scene_plan)
    uniform_execution = execute_uniform_proposed_stage_a(
        complete_scene_plan,
        runner if runner is not None else LocalRunner(),
    )
    full_output = derive_uniform_full_output_schedule(
        uniform_execution.reference_field,
        layout,
        reference_watts_per_module=(
            complete_scene_plan.reference_watts_per_module
        ),
    )
    target_control = apply_target_control(
        full_output,
        config.target_ppfd_umol_m2_s,
    )
    if not target_control.feasible:
        raise AngularExperimentError(
            f"case {case.case_id} cannot achieve the requested target at the "
            "declared uniform module power limit."
        )
    achieved_field = np.asarray(
        target_control.achieved_field, dtype=np.float64
    )
    achieved_mean = float(achieved_field.mean(dtype=np.float64))
    if not math.isclose(
        achieved_mean,
        config.target_ppfd_umol_m2_s,
        rel_tol=1e-9,
        abs_tol=1e-6,
    ):
        raise AngularExperimentError(
            f"case {case.case_id} global uniform dimming missed the common "
            "target mean."
        )

    reference_watts = tuple(full_output.schedule.watts_by_module)
    effective_watts = tuple(
        value * target_control.dimming_factor for value in reference_watts
    )
    if (
        len(reference_watts) != case.module_count
        or len(set(reference_watts)) != 1
        or len(set(effective_watts)) != 1
    ):
        raise AngularExperimentError(
            f"case {case.case_id} did not retain equal module wattages."
        )
    effective_power_w = math.fsum(effective_watts)
    if not math.isclose(
        effective_power_w,
        target_control.effective_power_w,
        rel_tol=1e-12,
        abs_tol=1e-9,
    ):
        raise AngularExperimentError(
            f"case {case.case_id} uniform electrical power is inconsistent."
        )

    metrics = compute_spatial_uniformity(achieved_field)
    points = generate_sensor_points(grid.spec)
    samples = tuple(
        PpfdMapSample(point.x_m, point.y_m, point.z_m, float(value))
        for point, value in zip(points, achieved_field, strict=True)
    )
    ppfd_path = case_root / CASE_PPFD_FILENAME
    metrics_path = case_root / CASE_METRICS_FILENAME
    control_path = case_root / CASE_CONTROL_FILENAME
    atomic_write_text(ppfd_path, format_ppfd_map(samples))
    atomic_write_text(
        metrics_path,
        _json_text(
            metrics.report_dict(
                reported_field=(
                    "angular_experiment_final_stage_a_uniform_complete_scene_"
                    "globally_dimmed_horizontal_reference_plane_ppfd"
                )
            )
        ),
    )

    source_identity = _uniform_source_identity(complete_scene_plan)
    angular_cal_sha256 = (
        None
        if complete_scene_plan.paths.source_variant_cal is None
        else _sha256_file(complete_scene_plan.paths.source_variant_cal)
    )
    completed_ppf = (
        effective_power_w * COMPLETED_APERTURE_PPE_UMOL_PER_J
    )
    control_payload: dict[str, object] = {
        "schema_id": "fspm-optics.angular-experiment-uniform-control",
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "artifact_type": "uniform_module_control",
        "experiment_id": config.experiment_id,
        "case_id": case.case_id,
        "variant": case.variant_key,
        "proposed_control_mode": EXPERIMENT_PROPOSED_CONTROL_MODE,
        "basis_matrix_solver_enabled": False,
        "complete_scene_trace_count": 1,
        "basis_column_count": 0,
        "module_count": case.module_count,
        "all_module_reference_watts_equal": True,
        "all_module_effective_watts_equal": True,
        "reference_watts_per_module": reference_watts[0],
        "effective_watts_per_module": effective_watts[0],
        "reference_watts_by_module": list(reference_watts),
        "effective_watts_by_module": list(effective_watts),
        "global_dimming_factor": target_control.dimming_factor,
        "full_output_mean_ppfd_umol_m2_s": (
            full_output.full_output_mean_ppfd
        ),
        "target_mean_ppfd_umol_m2_s": config.target_ppfd_umol_m2_s,
        "achieved_mean_ppfd_umol_m2_s": achieved_mean,
        "solved_electrical_power_w": effective_power_w,
        "completed_aperture_ppf_umol_s": completed_ppf,
        "completed_aperture_ppe_umol_per_j": (
            COMPLETED_APERTURE_PPE_UMOL_PER_J
        ),
        "planned_uniform_control_identity_sha256": (
            case.planned_uniform_control_identity
        ),
        "complete_scene_identity_sha256": (
            complete_scene_plan.identity_sha256
        ),
        "source_normalization_before_transport": True,
        "post_trace_source_flux_scaling": False,
        "field_evaluation": (
            "one_complete_equal_module_reference_field_times_one_global_"
            "dimming_factor"
        ),
    }
    control_identity = _identity(control_payload)
    control_payload["uniform_control_identity_sha256"] = control_identity
    atomic_write_text(control_path, _json_text(control_payload))

    spacing_x = (
        grid.spec.room.length_m - 2.0 * grid.spec.inset_m
    ) / grid.spec.resolution_x
    spacing_y = (
        grid.spec.room.width_m - 2.0 * grid.spec.inset_m
    ) / grid.spec.resolution_y
    artifact_hashes = _workspace_artifact_hashes(case_root)
    scene_artifact_identity = _complete_scene_artifact_identity(
        artifact_hashes
    )
    result: dict[str, object] = {
        "schema_id": EXPERIMENT_SCHEMA_ID,
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "artifact_type": "stage_a_case_result",
        "status": "completed",
        "experiment_id": config.experiment_id,
        "case_id": case.case_id,
        "order": case.order,
        "stage": "A",
        "system_id": EXPERIMENT_SYSTEM_ID,
        "variant": case.variant_key,
        "proposed_layout_mode": EXPERIMENT_PROPOSED_LAYOUT_MODE,
        "proposed_ring_mode": EXPERIMENT_PROPOSED_RING_MODE,
        "proposed_control_mode": EXPERIMENT_PROPOSED_CONTROL_MODE,
        "basis_matrix_solver_enabled": False,
        "complete_scene_trace_count": 1,
        "basis_column_count": 0,
        "radiance_quality": config.quality,
        "module_count": case.module_count,
        "room_dimensions_ft": {
            "length": case.room_length_ft,
            "width": case.room_width_ft,
        },
        "room_dimensions_m_after_long_axis_alignment": {
            "length": layout.room_length_m,
            "width": layout.room_width_m,
            "height": DEFAULT_ROOM_HEIGHT_M,
        },
        "mounting_height": mounting.to_payload(),
        "target_mean_ppfd_umol_m2_s": config.target_ppfd_umol_m2_s,
        "achieved_mean_ppfd_umol_m2_s": achieved_mean,
        "sensor_grid": {
            "policy_id": EXPERIMENT_GRID_POLICY_ID,
            "resolution_x": grid.spec.resolution_x,
            "resolution_y": grid.spec.resolution_y,
            "sample_count": grid.spec.point_count,
            "layout": grid.spec.layout,
            "physical_spacing_m": {"x": spacing_x, "y": spacing_y},
            "target_spacing_m": EXPERIMENT_TARGET_SENSOR_SPACING_M,
            "long_axis_aligned_to_x": True,
        },
        "reference_watts_per_module": reference_watts[0],
        "effective_watts_per_module": effective_watts[0],
        "all_module_reference_watts_equal": True,
        "all_module_effective_watts_equal": True,
        "global_dimming_factor": target_control.dimming_factor,
        "solved_electrical_power_w": effective_power_w,
        "electrical_power_w": effective_power_w,
        "completed_aperture_ppf_umol_s": completed_ppf,
        "completed_aperture_ppe_umol_per_j": (
            COMPLETED_APERTURE_PPE_UMOL_PER_J
        ),
        "identities": {
            "source_identity_sha256": source_identity,
            "angular_cal_sha256": angular_cal_sha256,
            "complete_scene_identity_sha256": (
                complete_scene_plan.identity_sha256
            ),
            "complete_scene_artifact_identity_sha256": (
                scene_artifact_identity
            ),
            "planned_complete_scene_identity_sha256": (
                case.planned_complete_scene_identity
            ),
            "uniform_control_identity_sha256": control_identity,
            "planned_uniform_control_identity_sha256": (
                case.planned_uniform_control_identity
            ),
            "radiance_options_identity_sha256": _identity(
                list(experiment_radiance_options(config.quality))
            ),
        },
        "radiance_options": list(experiment_radiance_options(config.quality)),
        "artifact_hashes": artifact_hashes,
    }
    result["case_result_identity_sha256"] = _identity(result)
    atomic_write_text(case_root / CASE_RESULT_FILENAME, _json_text(result))
    return result


def build_experiment_summary(
    config: AngularExperimentConfig,
    *,
    characterization_payload: Mapping[str, object],
    polar_profiles: Sequence[Mapping[str, object]],
    polar_diagram: Mapping[str, object],
    case_results: Sequence[Mapping[str, object]],
    finalization_provenance: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if len(case_results) != 6:
        raise AngularExperimentError("summary publication requires six case results.")
    if len(polar_profiles) != 4:
        raise AngularExperimentError(
            "summary publication requires four authenticated polar profiles."
        )
    try:
        authenticate_angular_polar_comparison(
            config.output_directory, polar_diagram
        )
    except AngularPolarDiagramError as error:
        raise AngularExperimentError(str(error)) from error
    profile_by_key = {
        str(profile.get("profile_key")): profile for profile in polar_profiles
    }
    rows: list[dict[str, object]] = []
    seen_complete_scene: set[str] = set()
    seen_uniform_control: set[str] = set()
    expected_cases = planned_cases(config)
    for expected_order, (expected_case, case_result) in enumerate(
        zip(expected_cases, case_results, strict=True)
    ):
        if (
            case_result.get("order") != expected_order
            or case_result.get("case_id") != expected_case.case_id
            or case_result.get("variant") != expected_case.variant_key
            or case_result.get("room_dimensions_ft")
            != {
                "length": expected_case.room_length_ft,
                "width": expected_case.room_width_ft,
            }
            or case_result.get("proposed_layout_mode")
            != EXPERIMENT_PROPOSED_LAYOUT_MODE
            or case_result.get("proposed_ring_mode")
            != EXPERIMENT_PROPOSED_RING_MODE
            or case_result.get("proposed_control_mode")
            != EXPERIMENT_PROPOSED_CONTROL_MODE
            or case_result.get("basis_matrix_solver_enabled") is not False
            or case_result.get("complete_scene_trace_count") != 1
            or case_result.get("basis_column_count") != 0
        ):
            raise AngularExperimentError(
                "case results are not in the preregistered v2 order or mode."
            )
        case_root = (
            config.output_directory / "cases" / str(case_result["case_id"])
        )
        metrics_payload = json.loads(
            (case_root / CASE_METRICS_FILENAME).read_text(encoding="utf-8")
        )
        values = metrics_payload["values"]
        identities = case_result["identities"]
        complete_scene_identity = identities[
            "complete_scene_identity_sha256"
        ]
        uniform_control_identity = identities[
            "uniform_control_identity_sha256"
        ]
        if (
            complete_scene_identity in seen_complete_scene
            or uniform_control_identity in seen_uniform_control
        ):
            raise AngularExperimentError(
                "every case must retain distinct complete-scene and uniform-control "
                "identities."
            )
        seen_complete_scene.add(complete_scene_identity)
        seen_uniform_control.add(uniform_control_identity)
        source_profile = profile_by_key.get(expected_case.variant_key)
        if not isinstance(source_profile, Mapping) or not isinstance(
            source_profile.get("measured_fwhm_deg"), int | float
        ):
            raise AngularExperimentError(
                f"summary FWHM authority is missing for "
                f"{expected_case.variant_key}."
            )
        rows.append(
            {
                "order": expected_order,
                "case_id": case_result["case_id"],
                "room_dimensions_ft": case_result["room_dimensions_ft"],
                "room_dimensions_m_after_long_axis_alignment": (
                    case_result[
                        "room_dimensions_m_after_long_axis_alignment"
                    ]
                ),
                "variant": case_result["variant"],
                "measured_fwhm_deg": float(
                    source_profile["measured_fwhm_deg"]
                ),
                "target_mean_ppfd_umol_m2_s": (
                    case_result["target_mean_ppfd_umol_m2_s"]
                ),
                "achieved_mean_ppfd_umol_m2_s": (
                    case_result["achieved_mean_ppfd_umol_m2_s"]
                ),
                "minimum_ppfd_umol_m2_s": values["minimum_ppfd"],
                "maximum_ppfd_umol_m2_s": values["maximum_ppfd"],
                "population_standard_deviation_ppfd_umol_m2_s": (
                    values["population_standard_deviation_ppfd"]
                ),
                "coefficient_of_variation": values[
                    "coefficient_of_variation"
                ],
                "coefficient_of_variation_percent": values[
                    "coefficient_of_variation_percent"
                ],
                "degree_of_uniformity_percent": values[
                    "degree_of_uniformity_percent"
                ],
                "minimum_to_mean_uniformity": values[
                    "minimum_to_mean_uniformity"
                ],
                "minimum_to_maximum_ppfd_ratio": values[
                    "minimum_to_maximum_ppfd_ratio"
                ],
                "sensor_grid": case_result["sensor_grid"],
                "solved_electrical_power_w": case_result[
                    "solved_electrical_power_w"
                ],
                "completed_aperture_ppf_umol_s": case_result[
                    "completed_aperture_ppf_umol_s"
                ],
                "completed_aperture_ppe_umol_per_j": case_result[
                    "completed_aperture_ppe_umol_per_j"
                ],
                "identities": identities,
                "artifact_hashes": case_result["artifact_hashes"],
            }
        )
    mounting = MountingGeometry.resolve(EXPERIMENT_MOUNTING_HEIGHT_IN)
    summary: dict[str, object] = {
        "schema_id": EXPERIMENT_SCHEMA_ID,
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "artifact_type": "completed_experiment_summary",
        "experiment_id": config.experiment_id,
        "status": "completed",
        "system": {
            "internal_id": EXPERIMENT_SYSTEM_ID,
            "experiment_facing_copy": EXPERIMENT_DISPLAY_SYSTEM_NAME,
        },
        "controlled_variable_contract": controlled_variable_contract(
            config, mounting
        ),
        "software_and_radiance_provenance": {
            "fspm_optics_version": __version__,
            "fspm_optics_source_tree_identity_sha256": (
                _source_tree_identity()
            ),
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "git": _git_provenance(),
            "radiance": characterization_payload["radiance_provenance"],
        },
        "polar_profiles": [dict(profile) for profile in polar_profiles],
        "polar_diagram": dict(polar_diagram),
        "finalization_provenance": dict(
            finalization_provenance
            or {
                "mode": "direct_summary_builder_call",
                "case_result_origin": "caller_supplied",
            }
        ),
        "stage_a_table_rows": rows,
        "identity_contract": {
            "complete_equal_module_scene_per_case": True,
            "global_uniform_control_per_case": True,
            "basis_matrix_solver_enabled": False,
            "complete_scene_trace_count_per_case": 1,
            "basis_column_count_per_case": 0,
            "distinct_complete_scene_identities": (
                len(seen_complete_scene) == 6
            ),
            "distinct_uniform_control_identities": (
                len(seen_uniform_control) == 6
            ),
            "ambient_cache_scope": "per_case_complete_scene",
            "cross_variant_ambient_cache_reuse": False,
            "cross_variant_complete_scene_reuse": False,
            "cross_variant_uniform_control_reuse": False,
        },
        "claim_scope": EXPERIMENT_CLAIM_SCOPE,
        "prohibited_claims": {
            "performance_conclusion_embedded_by_runner": False,
            "plant_or_absorption_conclusion": False,
            "stage_b_result": False,
        },
    }
    summary["summary_identity_sha256"] = _identity(summary)
    return summary


def authoritative_polar_profiles(
    *,
    characterization_payload: Mapping[str, object],
    modular_profiles: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    """Bind plot-ready profiles to the exact experiment authorities."""

    raw_calibrations = characterization_payload.get("calibrations")
    artifact_identity = characterization_payload.get(
        "characterization_identity_sha256"
    )
    if not isinstance(raw_calibrations, Mapping) or not _is_sha256(
        artifact_identity
    ):
        raise AngularExperimentError(
            "polar publication requires an authenticated characterization."
        )
    profiles = [conventional_polar_profile()]
    for key in ANGULAR_VARIANT_KEYS:
        source_profile = modular_profiles.get(key)
        calibration = raw_calibrations.get(key)
        if not isinstance(source_profile, Mapping) or not isinstance(
            calibration, Mapping
        ):
            raise AngularExperimentError(
                f"polar publication authority is missing for {key}."
            )
        characterization_identity = calibration.get(
            "characterization_identity_sha256"
        )
        if (
            calibration.get("profile_sha256")
            != source_profile.get("profile_sha256")
            or not isinstance(characterization_identity, str)
        ):
            raise AngularExperimentError(
                f"polar profile and characterization identities disagree for {key}."
            )
        profile = dict(source_profile)
        profile.update(
            {
                "profile_key": key,
                "display_title": DISPLAY_TITLES[key],
                "characterization_identity_sha256": characterization_identity,
                "source_authority": {
                    "type": (
                        "radiance_completed_aperture_characterization_through_"
                        "physical_optical_stack"
                    ),
                    "characterization_artifact": (
                        f"characterization/{CHARACTERIZATION_FILENAME}"
                    ),
                    "characterization_identity_sha256": (
                        characterization_identity
                    ),
                    "characterization_artifact_identity_sha256": (
                        artifact_identity
                    ),
                    "optical_stack_id": PROPOSED_FIXTURE_OPTICAL_STACK_ID,
                    "measurement_boundary": (
                        "completed_aperture_far_field_radiant_intensity"
                    ),
                    "same_boundary_for_all_modularized_variants": True,
                },
            }
        )
        profiles.append(profile)
    return profiles


def conventional_polar_profile() -> dict[str, object]:
    """Derive the requested polar profile from the normalized IES authority."""

    photometry = load_approved_lm63()
    distribution = normalize_lm63_angular_distribution(
        photometry,
        asset_sha256=CONVENTIONAL_IES_SHA256,
    )
    angles: list[float] = []
    intensity: list[float] = []
    unique_planes = photometry.candela_by_horizontal_plane[:-1]
    for vertical_index, angle in enumerate(photometry.vertical_angles_deg):
        if angle > 90.0:
            break
        angles.append(float(angle))
        intensity.append(
            math.fsum(
                plane[vertical_index] for plane in unique_planes
            )
            / len(unique_planes)
        )
    normalized = normalized_profile(intensity)
    if angles[-1] < 90.0:
        angles.append(90.0)
        normalized = (*normalized, 0.0)
    measured = measured_fwhm_deg(angles, normalized)
    integral = axisymmetric_integrated_flux(angles, normalized)
    unit_flux_profile = [value / integral for value in normalized]
    reintegrated_unit_flux = axisymmetric_integrated_flux(
        angles, unit_flux_profile
    )
    identity = profile_sha256(angles, normalized)
    return {
        "profile_key": "conventional",
        "display_title": DISPLAY_TITLES["conventional"],
        "characterization_identity_sha256": None,
        "angle_deg": angles,
        "normalized_radiant_intensity": list(normalized),
        "measured_fwhm_deg": measured,
        "integrated_flux_closure": {
            "normalized_solid_angle_integral_sr": integral,
            "unit_flux_scale_per_sr": 1.0 / integral,
            "reintegrated_unit_flux": reintegrated_unit_flux,
            "absolute_closure_error": abs(
                reintegrated_unit_flux - 1.0
            ),
            "profile_peak_normalized_to_one": True,
            "authoritative_3d_cell_probability_sum": (
                distribution.total_probability
            ),
            "authoritative_3d_probability_closure_error": abs(
                distribution.total_probability - 1.0
            ),
            "duplicate_0_360_plane_counted_once": True,
        },
        "profile_sha256": identity,
        "source_authority": {
            "type": "existing_normalized_ies_angular_authority",
            "asset_sha256": CONVENTIONAL_IES_SHA256,
            "angular_distribution_id": distribution.distribution_id,
            "measurement_boundary": (
                "completed_luminaire_output_from_normalized_IES"
            ),
            "characterization_identity": (
                "not_applicable_existing_IES_angular_authority"
            ),
            "conventional_stage_a_rerun": False,
        },
    }


def experiment_radiance_options(quality: str) -> tuple[str, ...]:
    """Resolve one deterministic option vector shared by all six cases."""

    options = [
        option
        for option in radiance_options(quality)
        if option not in {"-u+", "-u-"}
    ]
    options.append("-u-")
    return tuple(options)


def _validate_characterization_config(
    config: AngularExperimentConfig,
    characterization_payload: Mapping[str, object],
) -> None:
    raw = characterization_payload.get("configuration")
    if not isinstance(raw, Mapping):
        raise AngularExperimentError("characterization configuration is missing.")
    if (
        raw.get("quality") != config.quality
        or raw.get("nthreads") != config.nthreads
        or raw.get("radiance_options")
        != list(experiment_radiance_options(config.quality))
        or raw.get("angle_step_deg") != DEFAULT_ANGLE_STEP_DEG
        or raw.get("azimuth_count_over_one_square-symmetry_quadrant")
        != DEFAULT_AZIMUTH_COUNT
        or raw.get("direction_replicate_count")
        != CHARACTERIZATION_DIRECTION_REPLICATE_COUNT
        or raw.get("direction_replicate_aggregation")
        != "per_direction_median"
        or raw.get("sample_order")
        != "angle_then_azimuth_then_replicate"
        or raw.get("far_field_distance_m")
        != DEFAULT_FAR_FIELD_DISTANCE_M
        or raw.get("fwhm_tolerance_deg") != ALTERED_FWHM_TOLERANCE_DEG
        or raw.get("reference_flux_relative_tolerance")
        != REFERENCE_FLUX_RELATIVE_TOLERANCE
    ):
        raise AngularExperimentError(
            "completed characterization options conflict with the experiment plan."
        )


def _workspace_artifact_hashes(
    case_root: Path,
    *,
    exclude: set[str] | None = None,
) -> dict[str, str]:
    root = Path(case_root).resolve()
    excluded = exclude or set()
    inventory: dict[str, str] = {}
    for artifact in sorted(root.rglob("*")):
        if artifact.is_symlink():
            raise AngularExperimentError(
                f"case workspace contains an unsafe symlink: {artifact}."
            )
        if not artifact.is_file():
            continue
        resolved = artifact.resolve()
        if not resolved.is_relative_to(root):
            raise AngularExperimentError(
                f"case workspace artifact escapes its root: {artifact}."
            )
        relative = resolved.relative_to(root).as_posix()
        if relative in excluded:
            continue
        inventory[relative] = _sha256_file(resolved)
    return inventory


def _uniform_source_identity(plan: UniformStageAPlan) -> str:
    artifact_hashes = {
        "complete-scene/uniform_complete_source.rad": _sha256_file(
            plan.paths.source
        )
    }
    if plan.paths.source_variant_cal is not None:
        artifact_hashes["complete-scene/smd_source_variant.cal"] = (
            _sha256_file(plan.paths.source_variant_cal)
        )
    if plan.paths.source_angular_data is not None:
        artifact_hashes[
            "complete-scene/" + plan.paths.source_angular_data.name
        ] = _sha256_file(plan.paths.source_angular_data)
    return _uniform_source_identity_from_artifacts(artifact_hashes)


def _uniform_source_identity_from_artifacts(
    artifact_hashes: Mapping[str, object],
) -> str:
    prefix = "complete-scene/"
    source_relative = prefix + "uniform_complete_source.rad"
    source_hash = artifact_hashes.get(source_relative)
    if not _is_sha256(source_hash):
        raise AngularExperimentError(
            "complete-scene source artifact identity is missing."
        )
    angular_data = {
        relative: value
        for relative, value in artifact_hashes.items()
        if (
            isinstance(relative, str)
            and relative.startswith(prefix)
            and relative.endswith(".dat")
        )
    }
    if any(not _is_sha256(value) for value in angular_data.values()):
        raise AngularExperimentError(
            "complete-scene angular data identity is malformed."
        )
    return _identity(
        {
            "complete_scene_source_sha256": source_hash,
            "angular_cal_sha256": artifact_hashes.get(
                prefix + "smd_source_variant.cal"
            ),
            "angular_data_sha256_by_path": dict(sorted(angular_data.items())),
            "physical_stack_id": PROPOSED_FIXTURE_OPTICAL_STACK_ID,
        }
    )


def _complete_scene_artifact_identity(
    artifact_hashes: Mapping[str, object],
) -> str:
    scene_hashes = {
        relative: value
        for relative, value in artifact_hashes.items()
        if isinstance(relative, str) and relative.startswith("complete-scene/")
    }
    if not scene_hashes or any(
        not _is_sha256(value) for value in scene_hashes.values()
    ):
        raise AngularExperimentError(
            "complete-scene artifact identity is missing or malformed."
        )
    return _identity(dict(sorted(scene_hashes.items())))


def _read_case_json(path: Path, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise AngularExperimentError(f"{label} is missing or unsafe.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AngularExperimentError(f"{label} is unreadable.") from error
    if not isinstance(payload, dict):
        raise AngularExperimentError(f"{label} is malformed.")
    return payload


def _finite_number_list(
    value: object,
    *,
    expected_count: int,
    context: str,
) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != expected_count:
        raise AngularExperimentError(f"{context} is malformed.")
    try:
        numbers = tuple(float(item) for item in value)
    except (TypeError, ValueError) as error:
        raise AngularExperimentError(f"{context} is malformed.") from error
    if any(not math.isfinite(item) or item < 0.0 for item in numbers):
        raise AngularExperimentError(f"{context} is malformed.")
    return numbers


def _require_case_identity_field(
    field: str,
    actual: object,
    expected: object,
    *,
    case_id: str,
    require_sha256: bool = False,
    require_sha256_list: bool = False,
) -> None:
    if require_sha256 and not _is_sha256(actual):
        raise AngularExperimentError(
            f"completed case identity {field} is not a valid SHA-256: "
            f"{case_id}."
        )
    if require_sha256_list and (
        not isinstance(actual, list)
        or any(not _is_sha256(value) for value in actual)
    ):
        raise AngularExperimentError(
            f"completed case identity {field} is not a valid SHA-256 list: "
            f"{case_id}."
        )
    if actual != expected:
        raise AngularExperimentError(
            f"completed case identity {field} mismatch: {case_id}."
        )


def _safe_case_artifact(case_root: Path, relative: str) -> Path:
    root = Path(case_root).resolve()
    candidate = Path(relative)
    if (
        not relative
        or candidate.is_absolute()
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise AngularExperimentError(
            f"completed case declares an unsafe artifact path: {relative!r}."
        )
    artifact = (root / candidate).resolve()
    if not artifact.is_relative_to(root):
        raise AngularExperimentError(
            f"completed case artifact escapes its root: {relative!r}."
        )
    return artifact


def _git_provenance() -> dict[str, object]:
    def run(*argv: str) -> str | None:
        try:
            result = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        text = result.stdout.strip()
        return text if result.returncode == 0 and text else None

    commit = run("git", "rev-parse", "HEAD")
    status = run("git", "status", "--porcelain")
    return {
        "commit_sha": commit,
        "working_tree_dirty": status is not None,
    }


def _finalization_provenance(
    *,
    resume: bool,
    characterization_reused: bool,
    case_result_origins: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    reused_count = sum(
        origin.get("origin") == "reused_authenticated_case_result"
        for origin in case_result_origins
    )
    executed_count = len(case_result_origins) - reused_count
    finalization_only = (
        resume
        and characterization_reused
        and reused_count == len(case_result_origins)
        and executed_count == 0
    )
    return {
        "mode": (
            "finalization_only_authenticated_resume"
            if finalization_only
            else "execution_and_finalization"
        ),
        "resume_requested": resume,
        "characterization": {
            "origin": (
                "reused_authenticated_characterization"
                if characterization_reused
                else "executed_during_this_invocation"
            ),
            "artifacts_modified_during_finalization": False,
        },
        "case_results": [dict(origin) for origin in case_result_origins],
        "reused_authenticated_case_result_count": reused_count,
        "case_result_executed_during_invocation_count": executed_count,
        "radiance_executed_during_this_invocation": (
            not characterization_reused or executed_count > 0
        ),
        "radiance_executed_during_post_execution_finalization": False,
        "existing_case_artifacts_recomputed_during_finalization": False,
        "existing_case_artifacts_rewritten_during_finalization": False,
        "post_execution_publication_artifacts": [
            POLAR_SVG_FILENAME,
            POLAR_PNG_FILENAME,
            EXPERIMENT_SUMMARY_FILENAME,
        ],
    }


def _source_tree_identity() -> str:
    package_root = Path(__file__).resolve().parents[1]
    payload = [
        {
            "path": path.relative_to(package_root).as_posix(),
            "sha256": _sha256_file(path),
        }
        for path in sorted(package_root.rglob("*.py"))
        if path.is_file() and not path.is_symlink()
    ]
    if not payload:
        raise AngularExperimentError(
            "fspm_optics source tree identity cannot be empty."
        )
    return _identity(payload)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _identity(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _json_text(payload: object) -> str:
    return json.dumps(
        payload, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"


def _positive(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be finite and positive.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive.")
    return number
