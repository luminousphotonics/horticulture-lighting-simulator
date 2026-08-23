"""Native Proposed baseline orchestration and unified artifact publication."""

from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
from typing import Callable, Iterable, Mapping

import numpy as np

from fspm_optics import __version__
from fspm_optics.fixtures.proposed_cob.source import (
    native_proposed_spd_identity_sha256,
    resolve_proposed_source_authority,
)
from fspm_optics.fixtures.smd.radiance_writer import SmdEmitterAssumptions
from fspm_optics.fixtures.smd.config import mechanical_envelope
from fspm_optics.fixtures.smd.positions import SmdLayout, generate_proposed_led_layout
from fspm_optics.geometry.active_domain import ActiveRoomDomain
from fspm_optics.geometry.mounting import MountingGeometry
from fspm_optics.geometry.room import (
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    production_room_model_payload,
)
from fspm_optics.geometry.room import DEFAULT_ROOM_HEIGHT_M, RoomDimensions
from fspm_optics.geometry.sensor_grid import generate_sensor_points
from fspm_optics.layout.fixture_plan import fixture_policy_id
from fspm_optics.layout.mode import resolve_proposed_layout_mode
from fspm_optics.layout.overlay import bind_overlay_to_active_domain
from fspm_optics.layout.ring import (
    proposed_module_pattern_id,
    resolve_proposed_ring_mode,
)
from fspm_optics.plants.natural_fit import (
    NaturalFitLayoutError,
    plan_natural_fit_layout_from_feet,
)
from fspm_optics.plants.multi_scene import (
    JuvenileScientificScene,
    build_juvenile_natural_fit_scene,
)
from fspm_optics.plants.rex_juvenile import (
    REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID,
    REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID,
    REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS,
)
from fspm_optics.plants.radiance_scene_export import (
    COMPACT_RECEIVER_INDEX_SCHEMA_ID,
    COMPACT_RECEIVER_INDEX_SCHEMA_VERSION,
    JUVENILE_RADIANCE_EXPORT_SCHEMA_ID,
    JUVENILE_RADIANCE_EXPORT_SCHEMA_VERSION,
    JuvenileRadianceArtifactMetadata,
    PLANT_ORIGIN_STRIDE_BYTES,
    RECEIVER_ORDERING,
    plan_juvenile_radiance_export,
)
from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.options import radiance_options
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.spectral.bands import FIXED_TRANSPORT_BANDS
from fspm_optics.sources.smd.profile import build_nominal_smd_source_model
from fspm_optics.sources.smd.spectral_control import (
    proposed_spectral_basis_payload,
)
from fspm_optics.transport.basis.execution import execute_basis_matrix
from fspm_optics.transport.basis.workspace import (
    materialize_basis_workspace,
    plan_basis_workspace,
)
from fspm_optics.transport.proposed_uniform import (
    UniformStageAError,
    execute_uniform_proposed_stage_a,
    materialize_uniform_proposed_stage_a,
    plan_uniform_proposed_stage_a,
)
from fspm_optics.transport.scalar_ppfd import PpfdMapSample
from fspm_optics.viewer.artifacts import (
    PROFILE_ID,
    VIEWER_RESOURCE_VERSION,
    validate_profile_artifacts,
    validate_run_scene_artifacts,
)
from fspm_optics.viewer.fixtures import (
    build_fixture_publication,
    validate_fixture_publication,
)
from fspm_optics.viewer.models import (
    BinaryDisplayArtifact,
    SurfaceFluxViewerArtifacts,
)
from fspm_optics.viewer.ppfd_heatmap import bind_ppfd_heatmap_viewer_artifacts

from .domain import (
    ANALYSIS_SCOPE_DESCRIPTIONS,
    ANALYSIS_SCOPE_LABELS,
    ANALYSIS_SCOPE_SCHEMA_ID,
    ANALYSIS_SCOPE_SCHEMA_VERSION,
    AnalysisScope,
    EMITTED_PPF_BOUNDARIES,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    ProposedControlMode,
    NATIVE_PROPOSED_SPECTRAL_BASIS_ID,
    SUPPORTED_SYSTEM_IDS,
    ProposedRunRequest,
)
from .baseline_leaf_uniformity import (
    BASELINE_LEAF_UNIFORMITY_FILENAME,
    build_baseline_physical_leaf_scene,
    build_baseline_leaf_uniformity_publication,
)
from .multispectral import (
    BAND_ORDER,
    CANONICAL_COUNTS_PER_PLANT,
    JUVENILE_MULTISPECTRAL_SCHEMA_ID,
    JUVENILE_MULTISPECTRAL_SCHEMA_VERSION,
    build_proposed_juvenile_source_adapter,
    execute_juvenile_multispectral_transport,
)
from .fspm_science import (
    COEFFICIENT_CLOSURE_ABS_TOLERANCE,
    FSPM_AGGREGATION_SCHEMA_ID,
    FSPM_AGGREGATION_SCHEMA_VERSION,
    FSPM_DERIVATION_GRAPH_SCHEMA_ID,
    FSPM_DERIVATION_GRAPH_SCHEMA_VERSION,
    FSPM_ROOM_SUMMARY_SCHEMA_ID,
    FSPM_ROOM_SUMMARY_SCHEMA_VERSION,
    LEAF_INDEX_FIELDS,
    LOCAL_CLOSURE_ABS_TOLERANCE,
    LOCAL_CLOSURE_REL_TOLERANCE,
    PATCH_INDEX_FIELDS,
    PAR_BAND_ORDER,
    PLANT_INDEX_FIELDS,
    binary_contract_for_band_order,
    build_room_summary_payload,
    recompute_aggregation_digests,
)
from .publication import (
    PublicationError,
    RunSciencePublication,
    publish_native_baseline_run,
    scientific_runtime_identity,
    validate_emitted_ppf_contract,
)
from .spatial_uniformity import compute_spatial_uniformity
from .source_state import (
    PhysicalSourceState,
    PhysicalSourceStateError,
    hash_json,
    validate_physical_source_state_payload,
)
from .surface_flux_display import (
    AUTHENTICATED_SURFACE_FLUX_DISPLAY_SCHEMA_VERSION,
    AUTHENTICATED_SURFACE_FLUX_METADATA_FILENAME,
    MAX_DISPLAY_PLANT_COUNT,
    SURFACE_FLUX_DISPLAY_SCHEMA_ID,
    SURFACE_FLUX_DISPLAY_SCHEMA_VERSION,
    SURFACE_FLUX_METADATA_FILENAME,
    SURFACE_FLUX_VALUES_FILENAME,
    SurfaceFluxDisplayError,
    _convert_patch_values,
    authenticated_palette_contract,
    calibration_provenance,
    palette_contract,
    quality_family_mapping_contract,
    select_quality_family,
    select_surface_flux_reference,
    select_authenticated_achieved_surface_flux_reference,
)
from .surface_flux_display_calibration import (
    COEFFICIENT_COUNT as DISPLAY_CALIBRATION_COEFFICIENT_COUNT,
    D5_RECEIVERS_SHA256,
    D5_SAMPLING_PROFILE_ID,
    D5_TOPOLOGY_SHA256,
    DISPLAY_CALIBRATION_RESOURCE_ID,
    ESTIMATOR_ID as DISPLAY_CALIBRATION_ESTIMATOR_ID,
    PAYLOAD_BYTE_LENGTH as DISPLAY_CALIBRATION_BYTE_LENGTH,
    PINNED_C2_COMPLETION_SHA256,
    PINNED_C2_REPORT_SHA256,
    VIEWER_CALIBRATION_PAYLOAD_NAME,
    quality_dispatch_contract as display_quality_dispatch_contract,
)
from .target_control import (
    FullOutputSchedule,
    TargetControlResult,
    apply_target_control,
    derive_full_output_schedule,
    derive_uniform_full_output_schedule,
    resolve_fspm_target_policy,
)
from .visualization import (
    VISUALIZATION_SCHEMA_ID,
    VisualizationReference,
    build_scatter_vertical_display_transform,
    detect_regular_grid,
    heatmap_gridline_policy_metadata,
    overlay_style_policy_metadata,
    reference_plane_field_identity,
)

EventSink = Callable[[str, str, Mapping[str, object] | None], None]


class ProposedRunError(RuntimeError):
    """Structured terminal failure from one Proposed native run."""

    def __init__(
        self,
        code: str,
        stage: str,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "stage": self.stage,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class ProposedRunOutcome:
    run_id: str
    metrics: dict[str, object]
    manifest: dict[str, object]
    target_feasible: bool | None
    target_infeasibility: dict[str, object] | None
    system_id: str = PROPOSED_SYSTEM_ID

    def __post_init__(self) -> None:
        """Enforce the shared result discriminator while retaining this alias."""

        if not isinstance(self.run_id, str) or not self.run_id:
            raise ValueError("native run outcome requires a non-empty run ID.")
        if self.system_id not in SUPPORTED_SYSTEM_IDS:
            raise ValueError("native run outcome system identifier is unsupported.")
        if not isinstance(self.metrics, dict) or not isinstance(self.manifest, dict):
            raise ValueError("native run outcome payloads must be dictionaries.")

        for label, payload in (
            ("metrics", self.metrics),
            ("manifest", self.manifest),
        ):
            declared_run_id = payload.get("run_id")
            if declared_run_id is not None and declared_run_id != self.run_id:
                raise ValueError(f"{label} run ID disagrees with the outcome.")
            declared_system = payload.get("system_id")
            if declared_system is not None and declared_system != self.system_id:
                raise ValueError(
                    f"{label} system discriminator disagrees with the outcome."
                )

        if self.system_id == HPS_SYSTEM_ID:
            if (
                self.target_feasible is not None
                or self.target_infeasibility is not None
            ):
                raise ValueError(
                    "HPS feasibility fields must be null for fixed-full-output operation."
                )
            for key in (
                "requested_target_ppfd_umol_m2_s",
                "dimming_factor",
                "target_feasible",
                "target_infeasibility",
            ):
                if key in self.metrics and self.metrics[key] is not None:
                    raise ValueError(
                        "HPS metrics must keep lighting-target result fields null."
                    )
            request_payload = self.manifest.get("request")
            if isinstance(request_payload, Mapping) and (
                "target_ppfd_umol_m2_s" in request_payload
            ):
                raise ValueError("HPS manifest must omit a lighting target.")
            target_control = self.manifest.get("target_control")
            if isinstance(target_control, Mapping) and target_control and (
                target_control.get("lighting_target_supported") is not False
                or "requested_target_ppfd_umol_m2_s" in target_control
            ):
                raise ValueError("HPS target-control result is contradictory.")
            return

        if not isinstance(self.target_feasible, bool):
            raise ValueError("LED run outcomes require a boolean feasibility result.")
        if self.target_feasible:
            if self.target_infeasibility is not None:
                raise ValueError(
                    "a feasible LED outcome cannot include infeasibility details."
                )
        elif (
            not isinstance(self.target_infeasibility, dict)
            or not self.target_infeasibility
        ):
            raise ValueError("an infeasible LED outcome requires structured details.")
        if (
            "target_feasible" in self.metrics
            and self.metrics["target_feasible"] is not self.target_feasible
        ):
            raise ValueError("metrics feasibility disagrees with the outcome.")
        if (
            "target_infeasibility" in self.metrics
            and self.metrics["target_infeasibility"] != self.target_infeasibility
        ):
            raise ValueError("metrics infeasibility disagrees with the outcome.")


# Neutral aliases are the shared Phase 27E contract; Proposed names remain for
# Phase 27B compatibility with callers and tests.
NativeRunError = ProposedRunError
NativeRunOutcome = ProposedRunOutcome


class ReportingRunner:
    """Preserve LocalRunner semantics while exposing command boundary events."""

    def __init__(self, delegate: LocalRunner, event_sink: EventSink) -> None:
        self._delegate = delegate
        self._event_sink = event_sink

    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult:
        label = command.label or command.argv[0]
        self._event_sink(
            "radiance.command.started",
            f"Started native Radiance command: {label}",
            {"label": label},
        )
        result = self._delegate.run(
            command,
            timeout_s=timeout_s,
            stderr_path=stderr_path,
        )
        self._event_sink(
            "radiance.command.finished",
            f"Finished native Radiance command: {label}",
            {
                "label": label,
                "returncode": result.returncode,
                "wall_time_s": result.wall_time_s,
                "success": result.success,
            },
        )
        return result


def run_proposed_baseline(
    *,
    run_id: str,
    request: ProposedRunRequest,
    workspace: Path,
    event_sink: EventSink,
    runner: LocalRunner | None = None,
) -> ProposedRunOutcome:
    """Execute one real basis workflow and publish its run-scoped artifacts."""

    root = workspace.resolve()
    if not root.is_absolute() or not root.is_dir():
        raise ProposedRunError(
            "invalid_workspace",
            "workspace",
            "run workspace must be an existing absolute directory.",
        )
    _emit(
        event_sink,
        "analysis.stage.baseline.started",
        "Stage A started: plant-free horizontal PPFD baseline.",
        {
            "logical_stage": "baseline_ppfd",
            "plants_in_scientific_transport": False,
            "analysis_scope": request.analysis_scope.value,
        },
    )
    _emit(event_sink, "layout.started", "Planning Proposed module layout.")
    try:
        source_authority = resolve_proposed_source_authority(
            request.source_mode.value
        )
        emitter_assumptions = SmdEmitterAssumptions(
            source_mode=request.source_mode.value
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProposedRunError(
            "proposed_source_authentication_failed",
            "source_authentication",
            str(exc),
        ) from exc
    try:
        layout = generate_proposed_led_layout(
            request.active_domain.active_requested_length_ft,
            request.active_domain.active_requested_width_ft,
            mount_z_m=request.mounting_geometry.emitting_aperture_plane_z_m,
            proposed_layout_mode=request.proposed_layout_mode,
            proposed_ring_mode=request.proposed_ring_mode,
        )
    except ValueError as exc:
        raise ProposedRunError(
            "proposed_layout_infeasible", "layout", str(exc)
        ) from exc
    try:
        natural_fit = plan_natural_fit_layout_from_feet(
            request.room_length_ft,
            request.room_width_ft,
            active_domain=request.active_domain,
        )
    except NaturalFitLayoutError as exc:
        raise ProposedRunError(
            "natural_fit_layout_infeasible", "natural_fit", str(exc)
        ) from exc
    _emit(
        event_sink,
        "layout.completed",
        "Planned Proposed modules and the source-neutral Natural-fit plant layout.",
        {
            "proposed_layout_mode": layout.proposed_layout_mode.value,
            "proposed_ring_mode": layout.proposed_ring_mode.value,
            "module_pattern_id": layout.module_pattern_id,
            "fixture_policy_id": layout.fixture_policy_id,
            "mechanical_envelope_id": layout.mechanical_envelope_id,
            "fixture_asset_set_id": layout.fixture_asset_set_id,
            "module_count": len(layout.modules),
            "control_zone_count": layout.control_zone_count,
            "plant_count": natural_fit.total_count,
            "proposed_source_mode": request.source_mode.value,
        },
    )

    options = tuple(radiance_options(request.quality))
    reporting_runner = ReportingRunner(runner or LocalRunner(), event_sink)
    physical_room = RoomDimensions(
        request.active_domain.outer_aligned_length_m,
        request.active_domain.outer_aligned_width_m,
        DEFAULT_ROOM_HEIGHT_M,
    )
    sensor_room = RoomDimensions(
        request.active_domain.active_aligned_length_m,
        request.active_domain.active_aligned_width_m,
        DEFAULT_ROOM_HEIGHT_M,
    )
    if request.control_mode is ProposedControlMode.BASIS_MATRIX_OPTIMIZED:
        basis_root = root / "basis"
        _emit(
            event_sink,
            "basis.planning",
            "Planning isolated native basis workspace.",
            {"proposed_control_mode": request.control_mode.value},
        )
        try:
            basis_plan = plan_basis_workspace(
                layout=layout,
                output_directory=basis_root,
                radiance_options=options,
                physical_room=physical_room,
                sensor_room=sensor_room,
                emitter_assumptions=emitter_assumptions,
            )
            materialized = materialize_basis_workspace(basis_plan)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProposedRunError(
                "basis_workspace_failed", "basis_workspace", str(exc)
            ) from exc
        _emit(
            event_sink,
            "basis.executing",
            "Executing one native Radiance column per Proposed control zone.",
            {
                "sensor_count": materialized.manifest.sensor_count,
                "control_zone_count": materialized.manifest.control_zone_count,
                "proposed_control_mode": request.control_mode.value,
            },
        )
        try:
            basis_execution = execute_basis_matrix(  # type: ignore[arg-type]
                materialized, reporting_runner
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProposedRunError(
                "native_basis_execution_failed", "basis_execution", str(exc)
            ) from exc
        normalized_basis = np.asarray(
            basis_execution.matrix / materialized.manifest.reference_watts,
            dtype=float,
        )
        _emit(
            event_sink,
            "schedule.solving",
            "Deriving target-independent CV-optimal full-output schedule.",
            {"proposed_control_mode": request.control_mode.value},
        )
        try:
            full_output = derive_full_output_schedule(
                normalized_basis,
                layout,
                source_authority=source_authority,
            )
            controlled = apply_target_control(
                full_output,
                request.target_ppfd_umol_m2_s,
                request.lighting_target_mode.value,
            )
        except (RuntimeError, ValueError) as exc:
            raise ProposedRunError(
                "target_control_failed", "target_control", str(exc)
            ) from exc
        sensor_grid_spec = basis_plan.sensor_grid_spec
        room_text = basis_plan.room_text
        transport_threads = materialized.manifest.nthreads
        radiance_installation = basis_execution.metadata.radiance_installation
        engine_identity: dict[str, object] = {
            "backend": "proposed_control_zone_basis",
            "control_mode": request.control_mode.value,
            "basis_matrix_solver_enabled": True,
            "basis_manifest": materialized.manifest.to_dict(),
            "matrix_sha256": basis_execution.metadata.matrix_sha256,
        }
        fixture_occlusion = materialized.fixture_occlusion
        if fixture_occlusion is None:
            raise ProposedRunError(
                "basis_fixture_occlusion_missing",
                "basis_execution",
                "basis workspace did not retain authenticated fixture bodies.",
            )
    else:
        _emit(
            event_sink,
            "uniform_stage_a.planning",
            "Planning one complete equal-module Proposed reference scene.",
            {
                "proposed_control_mode": request.control_mode.value,
                "basis_matrix_solver_enabled": False,
            },
        )
        try:
            uniform_plan = plan_uniform_proposed_stage_a(
                layout=layout,
                output_directory=root / "uniform-stage-a",
                radiance_options=options,
                physical_room=physical_room,
                sensor_room=sensor_room,
                emitter_assumptions=emitter_assumptions,
            )
            materialize_uniform_proposed_stage_a(uniform_plan)
            uniform_execution = execute_uniform_proposed_stage_a(
                uniform_plan,
                reporting_runner,  # type: ignore[arg-type]
            )
            full_output = derive_uniform_full_output_schedule(
                uniform_execution.reference_field,
                layout,
                reference_watts_per_module=(
                    uniform_plan.reference_watts_per_module
                ),
                source_authority=source_authority,
            )
            controlled = apply_target_control(
                full_output,
                request.target_ppfd_umol_m2_s,
                request.lighting_target_mode.value,
            )
        except (OSError, RuntimeError, ValueError, UniformStageAError) as exc:
            raise ProposedRunError(
                "uniform_stage_a_failed", "uniform_stage_a", str(exc)
            ) from exc
        _emit(
            event_sink,
            "uniform_stage_a.completed",
            "Traced one complete equal-module scene and resolved global dimming.",
            {
                "proposed_control_mode": request.control_mode.value,
                "complete_scene_trace_count": 1,
                "basis_column_count": 0,
                "target_feasible": controlled.feasible,
            },
        )
        sensor_grid_spec = uniform_plan.sensor_grid_spec
        room_text = uniform_plan.room_text
        transport_threads = uniform_plan.nthreads
        radiance_installation = uniform_execution.radiance_installation
        engine_identity = {
            "backend": "proposed_uniform_complete_scene",
            "control_mode": request.control_mode.value,
            "basis_matrix_solver_enabled": False,
            "uniform_stage_a_identity_sha256": uniform_plan.identity_sha256,
            "uniform_reference_field_sha256": (
                uniform_execution.execution_metadata["field_sha256"]
            ),
            "complete_scene_trace_count": 1,
            "basis_column_count": 0,
        }
        fixture_occlusion = uniform_plan.fixture_occlusion

    engine_identity["fixture_occlusion"] = (
        fixture_occlusion.scientific_payload()
    )
    points = generate_sensor_points(sensor_grid_spec)
    if len(points) != len(full_output.full_output_field):
        raise ProposedRunError(
            "sensor_field_mismatch",
            "artifact_publication",
            "sensor point count does not match the achieved field.",
        )
    full_samples = tuple(
        PpfdMapSample(point.x_m, point.y_m, point.z_m, value)
        for point, value in zip(
            points, full_output.full_output_field, strict=True
        )
    )
    achieved_samples = tuple(
        PpfdMapSample(sample.x_m, sample.y_m, sample.z_m, value)
        for sample, value in zip(
            full_samples, controlled.achieved_field, strict=True
        )
    )
    achieved_spatial = compute_spatial_uniformity(
        tuple(sample.ppfd_umol_m2_s for sample in achieved_samples)
    )
    layout_identity = _layout_identity(
        layout,
        active_domain=request.active_domain.to_payload(),
    )
    schedule_payload = _schedule_payload(
        layout, full_output, control_mode=request.control_mode
    )
    limiting_sample = full_samples[controlled.limiting_sample_index]
    target_payload = _target_payload(
        controlled,
        {},
        limiting_sample=limiting_sample,
    ) | {
        "proposed_control_mode": request.control_mode.value,
        "basis_matrix_solver_enabled": (
            request.control_mode is ProposedControlMode.BASIS_MATRIX_OPTIMIZED
        ),
    }
    runtime_software_identity = {
        "fspm_optics_version": __version__,
        "python_version": sys.version.split()[0],
        "radiance": {
            "oconv": {
                "name": radiance_installation.oconv.name,
                "version_text": radiance_installation.oconv.version_text,
            },
            "rtrace": {
                "name": radiance_installation.rtrace.name,
                "version_text": radiance_installation.rtrace.version_text,
            },
        },
    }
    runtime_provenance = {
        **runtime_software_identity,
        "radiance_executable_paths": {
            "oconv": str(radiance_installation.oconv.path),
            "rtrace": str(radiance_installation.rtrace.path),
        },
    }
    fixture_count = len(layout.fixtures)
    solver_enabled = (
        request.control_mode is ProposedControlMode.BASIS_MATRIX_OPTIMIZED
    )
    effective_watts_by_module = tuple(
        value * controlled.dimming_factor
        for value in full_output.schedule.watts_by_module
    )
    uniform_mode = (
        request.control_mode is ProposedControlMode.UNIFORM_MODULE_DIMMING
    )
    proposed_control = {
        "mode": request.control_mode.value,
        "label": (
            "Basis-matrix optimized"
            if solver_enabled
            else "Uniform module dimming"
        ),
        "basis_matrix_solver_enabled": solver_enabled,
        "module_count": len(layout.modules),
        "uniform_reference_watts_per_module": (
            full_output.declared_max_watts_per_module if uniform_mode else None
        ),
        "effective_watts_per_module": (
            effective_watts_by_module[0] if uniform_mode else None
        ),
        "global_dimming_factor": controlled.dimming_factor,
        "total_power_w": controlled.effective_power_w,
        "emitted_ppf_umol_s": (
            controlled.effective_modeled_completed_aperture_par_ppf_umol_s
        ),
        "feasible": controlled.feasible,
        "all_module_reference_watts_equal": (
            len(set(full_output.schedule.watts_by_module)) == 1
        ),
        "all_module_effective_watts_equal": (
            len(set(effective_watts_by_module)) == 1
        ),
    }
    proposed_source = source_authority.to_dict()
    controlled_spd = proposed_spectral_basis_payload(
        build_nominal_smd_source_model()
    )
    proposed_source["controlled_spd"] = controlled_spd | {
        "identity_sha256": native_proposed_spd_identity_sha256(),
        "held_fixed_for_source_shape_experiment": (
            request.source_mode.value == "cob_source_shape_surrogate"
        ),
        "stage_a_scalar_transport_is_spd_independent": True,
    }
    if request.source_mode.value == "cob_source_shape_surrogate":
        proposed_source["viewer_active_emitter"] = {
            "representation": "active_centered_internal_cob_les",
            "smd_emitter_plane_active": False,
            "diameter_m": 0.022,
            "normal_scientific_xyz": [0.0, 0.0, -1.0],
            "modules": [
                {
                    "module_index": module.module_index,
                    "center_scientific_xyz_m": [
                        module.x_m,
                        module.y_m,
                        module.z_m + 0.011,
                    ],
                }
                for module in layout.modules
            ],
            "fixture_body_transforms_changed": False,
            "fixture_occlusion_classification_changed": False,
        }
    operating_point = {
        "mode": "target_controlled_global_dimming",
        "lighting_target_mode": request.lighting_target_mode.value,
        "proposed_control": proposed_control,
        "proposed_source": proposed_source,
        "per_module_declared_max_w": full_output.declared_max_watts_per_module,
        "power": {
            "full_output_w": controlled.full_output_power_w,
            "effective_w": controlled.effective_power_w,
        },
        "ppf": {
            "emitted_umol_s": (
                controlled.effective_modeled_completed_aperture_par_ppf_umol_s
            ),
            "emission_boundary_id": (
                EMITTED_PPF_BOUNDARIES[PROPOSED_SYSTEM_ID]["id"]
            ),
            "emission_boundary_description": (
                EMITTED_PPF_BOUNDARIES[PROPOSED_SYSTEM_ID]["description"]
            ),
            "internal_source_ppe_umol_per_j": (
                full_output.internal_source_ppe_umol_per_j
            ),
            "completed_aperture_fixture_ppe_umol_per_j": (
                full_output.completed_aperture_fixture_ppe_umol_per_j
            ),
            "accepted_fixture_transmission": (
                full_output.accepted_fixture_transmission
            ),
            "full_output_internal_par_ppf_umol_s": (
                controlled.full_output_internal_par_ppf_umol_s
            ),
            "effective_internal_par_ppf_umol_s": (
                controlled.effective_internal_par_ppf_umol_s
            ),
            "full_output_modeled_completed_aperture_par_ppf_umol_s": (
                controlled.full_output_modeled_completed_aperture_par_ppf_umol_s
            ),
            "effective_modeled_completed_aperture_par_ppf_umol_s": (
                controlled.effective_modeled_completed_aperture_par_ppf_umol_s
            ),
            "optical_stack_id": full_output.optical_stack_id,
        },
        "dimming_factor": controlled.dimming_factor,
        "global_source_dimming_factor": controlled.dimming_factor,
        "basis_coefficient_factor": (
            controlled.dimming_factor if solver_enabled else None
        ),
        "post_trace_source_normalization": False,
    }
    source_state = PhysicalSourceState.create(
        system_id=request.system,
        layout_identity=layout_identity,
        full_output_schedule=schedule_payload,
        operating_point=operating_point,
        source_operation={
            "policy_id": (
                "proposed_cv_optimal_stage_a_source_operation_v1"
                if solver_enabled
                else "proposed_uniform_module_dimming_stage_a_source_operation_v1"
            ),
            "proposed_control_mode": request.control_mode.value,
            "proposed_source": proposed_source,
            "proposed_layout_mode": layout.proposed_layout_mode.value,
            "proposed_ring_mode": layout.proposed_ring_mode.value,
            "module_pattern_id": layout.module_pattern_id,
            "fixture_policy_id": layout.fixture_policy_id,
            "mechanical_envelope": {
                "id": layout.mechanical_envelope_id,
                "width_x_m": layout.module_footprint_x_m,
                "height_y_m": layout.module_footprint_y_m,
            },
            "fixture_asset_set_id": layout.fixture_asset_set_id,
            "basis_matrix_solver_enabled": solver_enabled,
            "mounting_height": request.mounting_geometry.to_payload(),
            "target_independent_relative_schedule": solver_enabled,
            "uniform_reference_watts_per_module": (
                full_output.declared_max_watts_per_module
                if uniform_mode
                else None
            ),
            "declared_max_watts_per_module": (
                full_output.declared_max_watts_per_module
            ),
            "full_output_watts_by_control_zone": list(
                full_output.schedule.watts_by_control_zone
            ),
            "full_output_watts_by_module": list(
                full_output.schedule.watts_by_module
            ),
            "global_linear_dimming_factor": controlled.dimming_factor,
            "lighting_target_mode": request.lighting_target_mode.value,
            "effective_watts_by_control_zone": [
                value * controlled.dimming_factor
                for value in full_output.schedule.watts_by_control_zone
            ],
            "effective_watts_by_module": list(effective_watts_by_module),
            "all_module_reference_watts_equal": (
                proposed_control["all_module_reference_watts_equal"]
            ),
            "all_module_effective_watts_equal": (
                proposed_control["all_module_effective_watts_equal"]
            ),
            "target_control_resolved_in_stage_a": True,
            "final_composite_trace": False,
            "fixture_occlusion_identity": fixture_occlusion.identity_sha256,
        },
    )
    _emit(
        event_sink,
        "analysis.stage.baseline.completed",
        "Stage A completed with a validated final Proposed source state.",
        {
            "logical_stage": "baseline_ppfd",
            "source_state_id": source_state.source_state_id,
            "target_feasible": controlled.feasible,
        },
    )
    multispectral = None
    if request.analysis_scope is AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM:
        _emit(
            event_sink,
            "analysis.stage.fspm.started",
            "Stage B started: juvenile multi-plant multispectral transport.",
            {
                "logical_stage": "multispectral_fspm",
                "source_state_id": source_state.source_state_id,
                "include_far_red": request.include_far_red,
            },
        )
        try:
            fspm_reference = resolve_fspm_target_policy(
                mode=request.fspm_target_mode,
                achieved_baseline_mean_ppfd=achieved_spatial.mean_ppfd,
                tolerance_umol_m2_s=request.fspm_target_tolerance_umol_m2_s,
                override_umol_m2_s=request.fspm_target_override_umol_m2_s,
            )
            adapter = build_proposed_juvenile_source_adapter(
                layout=layout,
                full_output_schedule=full_output.schedule,
                dimming_factor=controlled.dimming_factor,
                room_text=room_text,
                source_state=source_state,
                quality_profile=request.quality,
                threads=transport_threads,
                oconv_bin=radiance_installation.oconv.path,
                rtrace_bin=radiance_installation.rtrace.path,
                spectral_basis=request.spectral_basis,
                control_mode=request.control_mode,
                source_mode=request.source_mode.value,
                fixture_occlusion=fixture_occlusion,
            )
            multispectral = execute_juvenile_multispectral_transport(
                root=root,
                run_id=run_id,
                room_length_ft=request.room_length_ft,
                room_width_ft=request.room_width_ft,
                active_domain=request.active_domain,
                source_state=source_state,
                adapter=adapter,
                emitted_par_ppf_umol_s=(
                    operating_point["ppf"]["emitted_umol_s"]
                ),
                modeled_electrical_power_w=(
                    operating_point["power"]["effective_w"]
                ),
                fspm_reference=fspm_reference.to_dict(),
                event_sink=event_sink,
                include_far_red=bool(request.include_far_red),
                runner=reporting_runner,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProposedRunError(
                "juvenile_multispectral_transport_failed",
                "multispectral_fspm",
                str(exc),
            ) from exc
        _emit(
            event_sink,
            "analysis.stage.fspm.completed",
            "Stage B completed with validated requested raw receiver artifacts.",
            {
                "logical_stage": "multispectral_fspm",
                "source_state_id": source_state.source_state_id,
                "include_far_red": request.include_far_red,
            },
        )

    science = RunSciencePublication(
        system_id=request.system,
        samples=achieved_samples,
        layout_identity=layout_identity,
        overlay_plan=bind_overlay_to_active_domain(
            layout.authoritative_overlay_plan(),
            request.active_domain,
        ),
        visualization_reference=(
            VisualizationReference.requested_sampled_cap(
                request.target_ppfd_umol_m2_s
            )
            if request.lighting_target_mode.value == "target_capped"
            else VisualizationReference.requested_target(
                request.target_ppfd_umol_m2_s
            )
        ),
        quality_options=options,
        target_control=target_payload,
        full_output_schedule=schedule_payload,
        operating_point=operating_point,
        counts={
            "fixture_groups": fixture_count,
            "fixtures": fixture_count,
            "modules": len(layout.modules),
            "control_zones": layout.control_zone_count,
        },
        transport_policy={
            "backend": (
                "isolated_rtrace_basis"
                if solver_enabled
                else "single_complete_uniform_rtrace"
            ),
            "proposed_control_mode": request.control_mode.value,
            "proposed_source_mode": request.source_mode.value,
            "proposed_source_identity_sha256": _hash_json(proposed_source),
            "source_normalization_boundary": (
                "internal emitter radiance before optical-stack and room transport"
            ),
            "basis_matrix_solver_enabled": solver_enabled,
            "basis_reference_watts_per_module": (
                materialized.manifest.reference_watts
                if solver_enabled
                else None
            ),
            "basis_source_coefficient_global_target_factor": (
                controlled.dimming_factor if solver_enabled else None
            ),
            "basis_source_coefficient_semantics": (
                "solver-controlled electrical watts before linear transport"
                if solver_enabled
                else None
            ),
            "uniform_reference_watts_per_module": (
                full_output.declared_max_watts_per_module
                if uniform_mode
                else None
            ),
            "complete_scene_trace_count": 1 if uniform_mode else None,
            "basis_column_count": 0 if uniform_mode else layout.control_zone_count,
            "post_trace_source_normalization": False,
            "additional_post_trace_correction": False,
            "post_trace_symmetrization": False,
            "final_field_global_target_control": True,
            "spatial_normalization": False,
            "distribution_shape_correction": False,
            "final_composite_trace": False,
            "global_factor_shared_by_future_spectral_bands": True,
        },
        runtime_provenance=runtime_provenance,
        engine_provenance={
            "mounting_height": request.mounting_geometry.to_payload(),
            **engine_identity,
        },
        engine_artifacts={},
        target_feasible=controlled.feasible,
        target_infeasibility=controlled.infeasibility,
        physical_source_state=source_state,
        mounting_height=request.mounting_geometry.to_payload(),
        multispectral_transport=multispectral,
    )
    try:
        published = publish_native_baseline_run(
            root,
            run_id=run_id,
            request=request,
            science=science,
            event_sink=event_sink,
        )
    except (OSError, PublicationError, RuntimeError, ValueError) as exc:
        raise ProposedRunError(
            "artifact_publication_failed", "artifact_publication", str(exc)
        ) from exc
    _emit(
        event_sink,
        "science.completed",
        "Completed the requested Proposed analysis scope and artifact publication.",
        {
            "analysis_scope": request.analysis_scope.value,
            "target_feasible": controlled.feasible,
            "achieved_mean_ppfd": controlled.achieved_mean_ppfd,
            "dimming_factor": controlled.dimming_factor,
        },
    )
    return ProposedRunOutcome(
        run_id=run_id,
        metrics=published.metrics,
        manifest=published.manifest,
        target_feasible=controlled.feasible,
        target_infeasibility=controlled.infeasibility,
    )


def validate_success_artifacts(
    root: Path,
    *,
    expected_run_id: str,
    expected_system_id: str | None = None,
    expected_request: Mapping[str, object] | None = None,
) -> None:
    """Fail closed unless every public success artifact is complete and local."""

    required = (
        "manifest.json",
        "metrics.json",
        "ppfd.csv",
        "target_control.json",
        "full_output_schedule.json",
        "operating-point.json",
        "physical-source-state.json",
        "natural_fit_layout.json",
        "events.jsonl",
        "run.log",
        "plant-layout-viewer/index.html",
        "plant-layout-viewer/fixture-height-controller.js",
        "plant-layout-viewer/surface-flux.js",
        "plant-layout-viewer/scene.v1.json",
        "plant-layout-viewer/instances.f32le.bin",
        "plant-layout-viewer/fixtures/catalog.v1.json",
        f"plant-layout-viewer/profiles/{PROFILE_ID}/profile.v1.json",
        f"plant-layout-viewer/profiles/{PROFILE_ID}/geometry.glb",
        f"plant-layout-viewer/profiles/{PROFILE_ID}/identity-map.v1.json",
        f"plant-layout-viewer/profiles/{PROFILE_ID}/receivers.f32le.bin",
        "plant-layout-viewer/vendor/three.module.js",
        "plant-layout-viewer/vendor/three.core.js",
        "plant-layout-viewer/vendor/addons/controls/OrbitControls.js",
        "plant-layout-viewer/vendor/addons/environments/RoomEnvironment.js",
        "plant-layout-viewer/vendor/addons/loaders/GLTFLoader.js",
        "plant-layout-viewer/vendor/addons/utils/BufferGeometryUtils.js",
        "plant-layout-viewer/vendor/addons/utils/SkeletonUtils.js",
        "ppfd-heatmap.png",
        "ppfd-heatmap-overlay.png",
        "visualization.json",
        "ppfd-scatter.f32le.bin",
        "ppfd-scatter-viewer/index.html",
        "ppfd-scatter-viewer/styles.css",
        "ppfd-scatter-viewer/main.js",
        "ppfd-scatter-viewer/presentation-export.js",
    )
    resolved_root = root.resolve()
    for relative in required:
        path = resolved_root / relative
        if (
            _contains_symlink(resolved_root, path)
            or not path.is_file()
            or not path.resolve().is_relative_to(resolved_root)
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                f"required run artifact is missing or unsafe: {relative}",
            )
    manifest = _read_json_object(resolved_root / "manifest.json")
    metrics = _read_json_object(resolved_root / "metrics.json")
    shared_schema_version = manifest.get("schema_version")
    if (
        manifest.get("schema_id") != "fspm-optics.native-baseline-run"
        or shared_schema_version not in {2, 3, 4}
        or metrics.get("schema_id") != "fspm-optics.native-baseline-metrics"
        or metrics.get("schema_version") != shared_schema_version
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "shared manifest or metrics schema is incompatible.",
        )
    scene_header = _read_json_object(
        resolved_root / "plant-layout-viewer/scene.v1.json"
    )
    if scene_header.get("schema_version") == 4:
        for relative in (
            "plant-layout-viewer/ppfd-heatmap.js",
            "plant-layout-viewer/ppfd-heatmap/visualization.json",
            "plant-layout-viewer/ppfd-heatmap/ppfd-scatter.f32le.bin",
        ):
            path = resolved_root / relative
            if (
                _contains_symlink(resolved_root, path)
                or not path.is_file()
                or not path.resolve().is_relative_to(resolved_root)
            ):
                raise ProposedRunError(
                    "artifact_validation_failed",
                    "promotion",
                    "required current viewer artifact is missing or unsafe: "
                    f"{relative}",
                )
    if scene_header.get("viewer_resource_version") == VIEWER_RESOURCE_VERSION:
        target_coverage_path = resolved_root / "plant-layout-viewer/target-coverage.js"
        if (
            _contains_symlink(resolved_root, target_coverage_path)
            or not target_coverage_path.is_file()
            or not target_coverage_path.resolve().is_relative_to(resolved_root)
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "required Target Coverage viewer resource is missing or unsafe.",
            )
    if shared_schema_version == 4:
        baseline_leaf_path = resolved_root / BASELINE_LEAF_UNIFORMITY_FILENAME
        if (
            _contains_symlink(resolved_root, baseline_leaf_path)
            or not baseline_leaf_path.is_file()
            or not baseline_leaf_path.resolve().is_relative_to(resolved_root)
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "schema-v4 baseline leaf-position artifact is missing or unsafe.",
            )
    if (
        expected_system_id is not None
        and manifest.get("system_id") != expected_system_id
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "published manifest system does not match the authorized request.",
        )
    if (
        expected_request is not None
        and manifest.get("request") != dict(expected_request)
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "published manifest request does not match the authorized request.",
        )
    parsed_artifacts: dict[str, dict[str, object]] = {}
    for filename in (
        "target_control.json",
        "full_output_schedule.json",
        "physical-source-state.json",
        "natural_fit_layout.json",
        "plant-layout-viewer/scene.v1.json",
        "plant-layout-viewer/fixtures/catalog.v1.json",
        "visualization.json",
    ):
        parsed_artifacts[filename] = _read_json_object(resolved_root / filename)
    parsed_artifacts["operating-point.json"] = _read_json_object(
        resolved_root / "operating-point.json"
    )
    try:
        validate_emitted_ppf_contract(
            str(manifest.get("system_id")),
            power=parsed_artifacts["operating-point.json"].get("power"),
            ppf=parsed_artifacts["operating-point.json"].get("ppf"),
        )
    except PublicationError as exc:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            f"emitted-PPF result contract is invalid: {exc}",
        ) from exc
    try:
        physical_source_state = validate_physical_source_state_payload(
            parsed_artifacts["physical-source-state.json"]
        )
    except PhysicalSourceStateError as exc:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            f"physical source-state validation failed: {exc}",
        ) from exc
    natural_fit_path = resolved_root / "natural_fit_layout.json"
    natural_fit_bytes = natural_fit_path.read_bytes()
    natural_fit_sha256 = hashlib.sha256(natural_fit_bytes).hexdigest()
    request_record = manifest.get("request")
    if not isinstance(request_record, dict):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "run request is missing before viewer validation.",
        )
    mounting_height = None
    if shared_schema_version in {3, 4}:
        try:
            mounting_height = MountingGeometry.resolve(
                request_record.get("mounting_height_in")
            ).to_payload()
        except ValueError as exc:
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "published mounting-height request is invalid.",
            ) from exc
        source_operation = physical_source_state.payload().get(
            "source_operation"
        )
        if (
            manifest.get("mounting_height") != mounting_height
            or metrics.get("mounting_height") != mounting_height
            or not isinstance(source_operation, dict)
            or source_operation.get("mounting_height") != mounting_height
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "published mounting-height provenance or source state is inconsistent.",
            )
    if (
        shared_schema_version == 4
        and manifest.get("system_id") == "proposed"
        and "proposed_layout_mode" in request_record
    ):
        layout_record = manifest.get("layout")
        manifest_overlay = manifest.get("authoritative_overlay_plan")
        overlay_metadata = (
            manifest_overlay.get("metadata")
            if isinstance(manifest_overlay, dict)
            else None
        )
        source_operation = physical_source_state.payload().get(
            "source_operation"
        )
        try:
            resolved_layout_mode = resolve_proposed_layout_mode(
                request_record.get("proposed_layout_mode")
            )
            resolved_ring_mode = resolve_proposed_ring_mode(
                request_record.get("proposed_ring_mode")
            )
            expected_module_pattern_id = proposed_module_pattern_id(
                resolved_ring_mode
            )
            expected_fixture_policy_id = fixture_policy_id(
                resolved_layout_mode
            )
            envelope_id, footprint_x, footprint_y = mechanical_envelope(
                resolved_layout_mode
            )
            expected_asset_set_id = (
                "proposed-led-module-v1"
                if resolved_layout_mode.value == "standalone_modules"
                else "historical-proposed-fixture-assets-v1"
            )
        except ValueError as exc:
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "published Proposed layout mode is invalid.",
            ) from exc
        expected_layout_publication = {
            "mode": resolved_layout_mode.value,
            "fixture_policy_id": expected_fixture_policy_id,
            "ring_mode": resolved_ring_mode.value,
            "module_pattern_id": expected_module_pattern_id,
        }
        if (
            not isinstance(layout_record, dict)
            or layout_record.get("proposed_layout_mode")
            != resolved_layout_mode.value
            or layout_record.get("fixture_policy_id")
            != expected_fixture_policy_id
            or layout_record.get("proposed_ring_mode")
            != resolved_ring_mode.value
            or layout_record.get("module_pattern_id")
            != expected_module_pattern_id
            or layout_record.get("mechanical_envelope")
            != {
                "id": envelope_id,
                "width_x_m": footprint_x,
                "height_y_m": footprint_y,
            }
            or layout_record.get("fixture_asset_set_id")
            != expected_asset_set_id
            or manifest.get("proposed_layout")
            != expected_layout_publication
            or metrics.get("proposed_layout")
            != expected_layout_publication
            or not isinstance(overlay_metadata, dict)
            or overlay_metadata.get("proposed_layout_mode")
            != resolved_layout_mode.value
            or overlay_metadata.get("fixture_policy_id")
            != expected_fixture_policy_id
            or overlay_metadata.get("proposed_ring_mode")
            != resolved_ring_mode.value
            or overlay_metadata.get("module_pattern_id")
            != expected_module_pattern_id
            or not isinstance(source_operation, dict)
            or source_operation.get("proposed_layout_mode")
            != resolved_layout_mode.value
            or source_operation.get("fixture_policy_id")
            != expected_fixture_policy_id
            or source_operation.get("proposed_ring_mode")
            != resolved_ring_mode.value
            or source_operation.get("module_pattern_id")
            != expected_module_pattern_id
            or source_operation.get("mechanical_envelope")
            != {
                "id": envelope_id,
                "width_x_m": footprint_x,
                "height_y_m": footprint_y,
            }
            or source_operation.get("fixture_asset_set_id")
            != expected_asset_set_id
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "Proposed request, layout, overlay, metrics, and source-state "
                "composition modes disagree.",
            )
    try:
        active_domain = _active_domain_from_request_record(request_record)
        natural_fit = plan_natural_fit_layout_from_feet(
            request_record["room_length_ft"],
            request_record["room_width_ft"],
            active_domain=active_domain,
        )
        baseline_leaf_scene = (
            build_baseline_physical_leaf_scene(natural_fit)
            if shared_schema_version == 4
            else None
        )
        if parsed_artifacts["natural_fit_layout.json"] != natural_fit.to_payload():
            raise ValueError("Natural-fit artifact does not match the authorized request.")
        layout_record = manifest.get("layout")
        if not isinstance(layout_record, dict):
            raise ValueError("authoritative fixture layout identity is missing.")
        expected_fixture_publication = build_fixture_publication(
            run_id=expected_run_id,
            system_id=str(manifest.get("system_id")),
            requested_length_ft=request_record["room_length_ft"],
            requested_width_ft=request_record["room_width_ft"],
            layout_identity=layout_record,
            mounting_height=mounting_height,
        )
        fixture_catalog_path = (
            resolved_root / "plant-layout-viewer/fixtures/catalog.v1.json"
        )
        fixture_catalog_bytes = fixture_catalog_path.read_bytes()
        fixture_root = resolved_root / "plant-layout-viewer/fixtures"
        if (
            _contains_symlink(resolved_root, fixture_root)
            or not fixture_root.is_dir()
            or {path.name for path in fixture_root.iterdir()}
            != {"assets", "catalog.v1.json", "transforms"}
        ):
            raise ValueError("fixture catalog root inventory is incomplete or unsafe.")
        expected_fixture_paths = {
            item.relative_path for item in expected_fixture_publication.files
        }
        fixture_files: dict[str, bytes] = {}
        for directory_name in ("assets", "transforms"):
            directory = resolved_root / "plant-layout-viewer/fixtures" / directory_name
            if _contains_symlink(resolved_root, directory) or not directory.is_dir():
                raise ValueError("fixture catalog directory is missing or unsafe.")
            for path in directory.iterdir():
                relative = path.relative_to(
                    resolved_root / "plant-layout-viewer"
                ).as_posix()
                if (
                    relative not in expected_fixture_paths
                    or _contains_symlink(resolved_root, path)
                    or not path.is_file()
                    or not path.resolve().is_relative_to(resolved_root)
                ):
                    raise ValueError(
                        f"fixture catalog artifact is undeclared or unsafe: {relative}"
                    )
                fixture_files[relative] = path.read_bytes()
        for expected_file in expected_fixture_publication.files:
            path = resolved_root / "plant-layout-viewer" / expected_file.relative_path
            if (
                _contains_symlink(resolved_root, path)
                or not path.is_file()
                or not path.resolve().is_relative_to(resolved_root)
            ):
                raise ValueError(
                    f"fixture catalog artifact is missing or unsafe: {expected_file.relative_path}"
                )
        fixture_catalog = validate_fixture_publication(
            fixture_catalog_bytes,
            fixture_files,
            expected_run_id=expected_run_id,
            expected_system_id=str(manifest.get("system_id")),
            expected_requested_length_ft=request_record["room_length_ft"],
            expected_requested_width_ft=request_record["room_width_ft"],
            expected_layout_identity=layout_record,
            expected_mounting_height=mounting_height,
        )
        fixture_catalog_sha256 = hashlib.sha256(fixture_catalog_bytes).hexdigest()
        surface_flux = _validate_surface_flux_display_artifacts(
            resolved_root,
            manifest=manifest,
            metrics=metrics,
            expected_run_id=expected_run_id,
            expected_quality=str(request_record.get("quality")),
            expected_plant_count=natural_fit.total_count,
            expected_layout_plan_hash=natural_fit.plan_hash,
            expected_available=(
                manifest.get("fspm_surface_flux_display") is not None
            ),
        )
        visualization_record = manifest.get("visualization")
        if not isinstance(visualization_record, dict):
            raise ValueError(
                "visualization identity is missing before viewer validation."
            )
        visualization_bytes = (resolved_root / "visualization.json").read_bytes()
        scatter_bytes = (resolved_root / "ppfd-scatter.f32le.bin").read_bytes()
        scene_path = resolved_root / "plant-layout-viewer/scene.v1.json"
        scene_header = parsed_artifacts["plant-layout-viewer/scene.v1.json"]
        ppfd_heatmap = None
        if scene_header.get("schema_version") == 4:
            scene_ppfd_heatmap = scene_header.get("ppfd_heatmap")
            if not isinstance(scene_ppfd_heatmap, Mapping):
                raise ValueError("viewer PPFD heatmap contract is missing.")
            ppfd_heatmap = bind_ppfd_heatmap_viewer_artifacts(
                metadata_bytes=visualization_bytes,
                scatter_bytes=scatter_bytes,
                expected_field_identity_sha256=str(
                    visualization_record.get("field_identity_sha256")
                ),
                target_coverage=scene_ppfd_heatmap.get("target_coverage"),
            )
        scene_files = {
            "instances.f32le.bin": (
                resolved_root / "plant-layout-viewer/instances.f32le.bin"
            ).read_bytes()
        }
        if surface_flux is not None:
            scene_files.update(
                {artifact.filename: artifact.data for artifact in surface_flux.files}
            )
        if ppfd_heatmap is not None:
            for artifact, source_bytes in (
                (ppfd_heatmap.metadata, visualization_bytes),
                (ppfd_heatmap.scalar_field, scatter_bytes),
            ):
                viewer_bytes = (
                    resolved_root / "plant-layout-viewer" / artifact.filename
                ).read_bytes()
                if viewer_bytes != source_bytes:
                    raise ValueError(
                        "viewer PPFD heatmap source is not byte-identical to Stage A."
                    )
                scene_files[artifact.filename] = viewer_bytes
        expected_sampling_profile_id = (
            REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID
            if (
                surface_flux is not None
                and surface_flux.schema_version
                != AUTHENTICATED_SURFACE_FLUX_DISPLAY_SCHEMA_VERSION
            )
            else REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
        )
        scene = validate_run_scene_artifacts(
            scene_path.read_bytes(),
            scene_files,
            expected_run_id=expected_run_id,
            expected_system_id=str(manifest.get("system_id")),
            expected_requested_length_ft=request_record["room_length_ft"],
            expected_requested_width_ft=request_record["room_width_ft"],
            expected_natural_fit=natural_fit,
            expected_natural_fit_artifact_sha256=natural_fit_sha256,
            expected_fixture_catalog_sha256=fixture_catalog_sha256,
            expected_fixture_catalog_byte_length=len(fixture_catalog_bytes),
            expected_fixture_authoritative_layout_sha256=(
                expected_fixture_publication.authoritative_layout_sha256
            ),
            expected_fixture_plan_sha256=str(fixture_catalog["fixture_plan_sha256"]),
            expected_fixture_count=int(fixture_catalog["fixture_count"]),
            expected_fixture_asset_group_count=int(
                fixture_catalog["asset_group_count"]
            ),
            expected_mounting_height=mounting_height,
            expected_run_information=(
                {
                    "proposed_control": dict(manifest["proposed_control"]),
                    "proposed_source": dict(manifest["proposed_source"]),
                    "proposed_layout": {
                        "ring_mode": manifest["proposed_layout"]["ring_mode"],
                        "module_pattern_id": manifest["proposed_layout"][
                            "module_pattern_id"
                        ],
                    },
                }
                if (
                    manifest.get("system_id") == PROPOSED_SYSTEM_ID
                    and isinstance(manifest.get("proposed_control"), Mapping)
                    and isinstance(manifest.get("proposed_source"), Mapping)
                    and isinstance(manifest.get("proposed_layout"), Mapping)
                )
                else None
            ),
            expected_surface_flux=surface_flux,
            expected_ppfd_heatmap=ppfd_heatmap,
            expected_sampling_profile_id=expected_sampling_profile_id,
        )
        profile_record = scene["profile"]
        profile_root = resolved_root / "plant-layout-viewer/profiles" / PROFILE_ID
        profile_bytes = (profile_root / "profile.v1.json").read_bytes()
        if (
            hashlib.sha256(profile_bytes).hexdigest()
            != profile_record["manifest_sha256"]
        ):
            raise ValueError("viewer profile manifest hash is incompatible.")
        validate_profile_artifacts(
            profile_bytes,
            {
                "geometry.glb": (profile_root / "geometry.glb").read_bytes(),
                "identity-map.v1.json": (
                    profile_root / "identity-map.v1.json"
                ).read_bytes(),
                "receivers.f32le.bin": (
                    profile_root / "receivers.f32le.bin"
                ).read_bytes(),
            },
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise ProposedRunError(
            "artifact_validation_failed", "promotion", str(exc)
        ) from exc
    scope_value = request_record.get("analysis_scope")
    try:
        analysis_scope = AnalysisScope(scope_value)
    except (TypeError, ValueError) as exc:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "published analysis scope is unsupported.",
        ) from exc
    expected_scope_payload = _analysis_scope_validation_payload(analysis_scope)
    transport_expected = (
        analysis_scope is AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM
    )
    if (
        manifest.get("analysis_scope") != expected_scope_payload
        or metrics.get("analysis_scope") != expected_scope_payload
        or manifest.get("logical_stages")
        != _logical_stages_validation_payload(analysis_scope)
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "analysis-scope and logical-stage declarations disagree.",
        )
    spectral_basis = manifest.get("spectral_basis")
    if metrics.get("spectral_basis") != spectral_basis:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "manifest and metrics spectral-basis declarations disagree.",
        )
    transport_declaration = manifest.get("multispectral_transport")
    historical_spectral_provenance = (
        transport_expected
        and isinstance(transport_declaration, Mapping)
        and transport_declaration.get("transport_schema_version") == 2
    )
    if historical_spectral_provenance:
        if spectral_basis is not None or "spectral_basis" in request_record:
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "historical multispectral publication contains future spectral-basis provenance.",
            )
    elif manifest.get("system_id") == PROPOSED_SYSTEM_ID:
        request_basis = request_record.get("spectral_basis")
        if (
            not isinstance(spectral_basis, dict)
            or not isinstance(spectral_basis.get("id"), str)
            or spectral_basis.get("stage_a_scalar_baseline_is_spd_independent")
            is not True
            or (
                transport_expected
                and (
                    request_basis != spectral_basis.get("id")
                    or spectral_basis.get("applied_to_multispectral_transport")
                    is not True
                )
            )
            or (
                not transport_expected
                and (
                    "spectral_basis" in request_record
                    or spectral_basis.get("id")
                    != NATIVE_PROPOSED_SPECTRAL_BASIS_ID
                    or spectral_basis.get("applied_to_multispectral_transport")
                    is not False
                    or spectral_basis.get("counterfactual_spectral_control")
                    is not False
                )
            )
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "Proposed spectral-basis provenance is incompatible with the request.",
            )
    elif spectral_basis is not None or "spectral_basis" in request_record:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "non-Proposed run contains Proposed spectral-basis provenance.",
        )
    proposed_control = manifest.get("proposed_control")
    if metrics.get("proposed_control") != proposed_control:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "manifest and metrics Proposed control declarations disagree.",
        )
    if manifest.get("system_id") == PROPOSED_SYSTEM_ID:
        requested_control_mode = request_record.get("proposed_control_mode")
        if (
            not isinstance(proposed_control, dict)
            or proposed_control.get("mode") != requested_control_mode
            or requested_control_mode
            not in {
                ProposedControlMode.BASIS_MATRIX_OPTIMIZED.value,
                ProposedControlMode.UNIFORM_MODULE_DIMMING.value,
            }
            or proposed_control.get("basis_matrix_solver_enabled")
            is not (
                requested_control_mode
                == ProposedControlMode.BASIS_MATRIX_OPTIMIZED.value
            )
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "Proposed control provenance is incompatible with the request.",
            )
    elif (
        proposed_control is not None
        or "proposed_control_mode" in request_record
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "non-Proposed run contains Proposed control provenance.",
        )
    proposed_source = manifest.get("proposed_source")
    if metrics.get("proposed_source") != proposed_source:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "manifest and metrics Proposed source declarations disagree.",
        )
    if manifest.get("system_id") == PROPOSED_SYSTEM_ID:
        requested_source_mode = request_record.get("proposed_source_mode")
        if (
            not isinstance(proposed_source, dict)
            or proposed_source.get("source_mode") != requested_source_mode
            or requested_source_mode
            not in {"native_smd", "cob_source_shape_surrogate"}
            or (
                requested_source_mode == "cob_source_shape_surrogate"
                and (
                    proposed_source.get("classification")
                    != "cob_source_shape_surrogate"
                    or not isinstance(
                        proposed_source.get("authenticated_angular_law"),
                        Mapping,
                    )
                )
            )
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "Proposed source provenance is incompatible with the request.",
            )
    elif (
        proposed_source is not None
        or "proposed_source_mode" in request_record
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "non-Proposed run contains Proposed source provenance.",
        )
    expected_viewer_surface_flux = (
        None
        if surface_flux is None
        else {
            "metadata": surface_flux.metadata.filename,
            "metadata_sha256": surface_flux.metadata.sha256,
            "patch_values": surface_flux.patch_values.filename,
            "patch_values_sha256": surface_flux.patch_values.sha256,
            **(
                {}
                if surface_flux.calibration_coefficients is None
                else {
                    "display_calibration_coefficients": (
                        surface_flux.calibration_coefficients.filename
                    ),
                    "display_calibration_coefficients_sha256": (
                        surface_flux.calibration_coefficients.sha256
                    ),
                }
            ),
        }
    )
    expected_viewer_publication = {
        "fixture_catalog": "plant-layout-viewer/fixtures/catalog.v1.json",
        "fixture_catalog_sha256": fixture_catalog_sha256,
        "fixture_plan_sha256": fixture_catalog["fixture_plan_sha256"],
        "resource_version": scene["viewer_resource_version"],
        "scene": "plant-layout-viewer/scene.v1.json",
        "scope": "authorized_run_natural_fit_scene",
        "surface_flux": expected_viewer_surface_flux,
    }
    if ppfd_heatmap is not None:
        expected_viewer_publication["ppfd_heatmap"] = {
            "metadata": ppfd_heatmap.metadata.filename,
            "metadata_sha256": ppfd_heatmap.metadata.sha256,
            "scalar_field": ppfd_heatmap.scalar_field.filename,
            "scalar_field_sha256": ppfd_heatmap.scalar_field.sha256,
            "source_field_identity_sha256": ppfd_heatmap.scene_reference[
                "source_field_identity_sha256"
            ],
        }
    if mounting_height is not None:
        expected_viewer_publication["mounting_height_sha256"] = _hash_json(
            mounting_height
        )
    if manifest.get("natural_fit") != {
        "artifact_role": "natural_fit_layout",
        "artifact_sha256": natural_fit_sha256,
        "ordering": "Y-major/X-minor",
        "plan_hash": natural_fit.plan_hash,
        "plant_count": natural_fit.total_count,
        "policy_id": natural_fit.policy.policy_id,
        "profile_id": natural_fit.profile_id,
        "transport_executed": transport_expected,
    } or manifest.get("viewer_publication") != expected_viewer_publication:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "run Natural-fit or viewer publication identity is incompatible.",
        )
    declared_run_artifacts = manifest.get("artifacts")
    if not isinstance(declared_run_artifacts, dict):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "shared run artifact inventory is missing.",
        )
    required_artifact_roles = {
        "metrics": "metrics.json",
        "ppfd_csv": "ppfd.csv",
        "target_control": "target_control.json",
        "full_output_schedule": "full_output_schedule.json",
        "operating_point": "operating-point.json",
        "natural_fit_layout": "natural_fit_layout.json",
        "physical_source_state": "physical-source-state.json",
        "fixture_catalog": "plant-layout-viewer/fixtures/catalog.v1.json",
        "events": "events.jsonl",
        "run_log": "run.log",
        "plant_layout_viewer": "plant-layout-viewer/index.html",
        "ppfd_heatmap": "ppfd-heatmap.png",
        "ppfd_heatmap_overlay": "ppfd-heatmap-overlay.png",
        "visualization_metadata": "visualization.json",
        "ppfd_scatter_data": "ppfd-scatter.f32le.bin",
        "ppfd_scatter_viewer": "ppfd-scatter-viewer/index.html",
    }
    if shared_schema_version == 4:
        required_artifact_roles["baseline_leaf_position_uniformity"] = (
            BASELINE_LEAF_UNIFORMITY_FILENAME
        )
    if surface_flux is not None:
        required_artifact_roles.update(
            {
                "surface_flux_display_metadata": (
                    "plant-layout-viewer/" + surface_flux.metadata.filename
                ),
                "surface_flux_display_values": (
                    "plant-layout-viewer/" + surface_flux.patch_values.filename
                ),
                **(
                    {}
                    if surface_flux.calibration_coefficients is None
                    else {
                        "surface_flux_display_calibration": (
                            "plant-layout-viewer/"
                            + surface_flux.calibration_coefficients.filename
                        )
                    }
                ),
            }
        )
    if any(
        declared_run_artifacts.get(role) != relative
        for role, relative in required_artifact_roles.items()
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "shared run artifact role mapping is incompatible.",
        )
    for role, relative in declared_run_artifacts.items():
        if not isinstance(role, str) or not isinstance(relative, str):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "shared run artifact inventory is invalid.",
            )
        path = resolved_root / relative
        if (
            _contains_symlink(resolved_root, path)
            or not path.is_file()
            or not path.resolve().is_relative_to(resolved_root)
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                f"declared run artifact is missing or unsafe: {relative}",
            )
    if manifest.get("run_id") != expected_run_id or metrics.get("run_id") != expected_run_id:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "run artifact identity does not match the allocated run ID.",
        )
    system_id = manifest.get("system_id")
    target_control_artifact = parsed_artifacts["target_control.json"]
    manifest_room = manifest.get("room")
    manifest_quality = manifest.get("quality")
    expected_metric_room = {
        "length_ft": (
            manifest_room.get("length_ft")
            if isinstance(manifest_room, dict)
            else None
        ),
        "width_ft": (
            manifest_room.get("width_ft")
            if isinstance(manifest_room, dict)
            else None
        ),
        **(
            {}
            if active_domain is None or not isinstance(manifest_room, dict)
            else {
                "outer_area_m2": manifest_room.get("outer_area_m2"),
                "active_length_ft": active_domain.active_requested_length_ft,
                "active_width_ft": active_domain.active_requested_width_ft,
                "active_area_m2": manifest_room.get("active_area_m2"),
            }
        ),
    }
    if (
            system_id not in {"proposed", "conventional", "hps"}
            or metrics.get("system_id") != system_id
            or not isinstance(manifest.get("request"), dict)
            or not isinstance(manifest_room, dict)
            or not isinstance(manifest_quality, dict)
            or not isinstance(manifest_quality.get("name"), str)
            or manifest_quality.get("radiance_options")
            != radiance_options(str(manifest_quality.get("name")))
            or manifest["request"].get("system") != system_id
            or manifest["request"].get("room_length_ft")
            != manifest_room.get("length_ft")
            or manifest["request"].get("room_width_ft")
            != manifest_room.get("width_ft")
            or metrics.get("room") != expected_metric_room
            or (
                active_domain is not None
                and (
                    manifest.get("active_domain") != active_domain.to_payload()
                    or manifest_room.get("outer_area_m2")
                    != active_domain.outer_area_m2
                    or manifest_room.get("active_area_m2")
                    != active_domain.active_area_m2
                    or manifest_room.get("evaluated_stage_a_receiver_area_m2")
                    != active_domain.active_area_m2
                )
            )
            or manifest["request"].get("quality")
            != manifest_quality.get("name")
            or metrics.get("quality") != manifest_quality.get("name")
            or metrics.get("requested_target_ppfd_umol_m2_s")
            != manifest["request"].get("target_ppfd_umol_m2_s")
            or (
                system_id == "hps"
                and (
                    "target_ppfd_umol_m2_s" in manifest["request"]
                    or target_control_artifact.get("lighting_target_supported")
                    is not False
                    or "requested_target_ppfd_umol_m2_s"
                    in target_control_artifact
                )
            )
            or (
                system_id != "hps"
                and (
                    target_control_artifact.get(
                        "requested_target_ppfd_umol_m2_s"
                    )
                    != manifest["request"].get("target_ppfd_umol_m2_s")
                    or (
                        "lighting_target_mode" in manifest["request"]
                        and (
                            target_control_artifact.get("lighting_target_mode")
                            != manifest["request"].get("lighting_target_mode")
                            or metrics.get("lighting_target_mode")
                            != manifest["request"].get("lighting_target_mode")
                            or manifest.get("lighting_target_mode")
                            != manifest["request"].get("lighting_target_mode")
                        )
                    )
                )
            )
            or manifest.get("target_control") != target_control_artifact
            or target_control_artifact.get("achieved_mean_ppfd_umol_m2_s")
            != metrics.get("achieved_mean_ppfd_umol_m2_s")
            or (
                system_id != "hps"
                and target_control_artifact.get(
                    "achieved_maximum_ppfd_umol_m2_s"
                )
                != metrics.get("achieved_maximum_ppfd_umol_m2_s")
            )
            or target_control_artifact.get("feasible")
            != metrics.get("target_feasible")
            or target_control_artifact.get("infeasibility")
            != metrics.get("target_infeasibility")
            or target_control_artifact.get("dimming_factor")
            != metrics.get("dimming_factor")
            or target_control_artifact.get("full_output_mean_ppfd_umol_m2_s")
            != metrics.get("full_output_mean_ppfd_umol_m2_s")
            or manifest.get("operating_point")
            != parsed_artifacts["operating-point.json"]
            or metrics.get("operating_point")
            != parsed_artifacts["operating-point.json"]
            or metrics.get("power")
            != parsed_artifacts["operating-point.json"].get("power")
            or metrics.get("ppf")
            != parsed_artifacts["operating-point.json"].get("ppf")
            or manifest.get("fspm_target_policy")
            != metrics.get("fspm_target_policy")
            or target_control_artifact.get("fspm_target_policy")
            != metrics.get("fspm_target_policy")
            or manifest.get("spatial_uniformity")
            != metrics.get("spatial_uniformity")
            or manifest.get("engine_provenance")
            != metrics.get("engine_provenance")
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "discriminated request, result, and target policies disagree.",
        )
    source_state_payload = parsed_artifacts["physical-source-state.json"]
    expected_source_state_manifest = {
        "artifact": "physical-source-state.json",
        "artifact_sha256": _sha256_stream(
            resolved_root / "physical-source-state.json"
        ),
        "source_state_id": physical_source_state.source_state_id,
        "stage_a_authoritative_handoff": True,
    }
    if (
        manifest.get("physical_source_state") != expected_source_state_manifest
        or source_state_payload.get("system_id") != system_id
        or source_state_payload.get("layout_identity_sha256")
        != hash_json(manifest.get("layout"))
        or source_state_payload.get("full_output_schedule_sha256")
        != hash_json(parsed_artifacts["full_output_schedule.json"])
        or source_state_payload.get("operating_point_sha256")
        != hash_json(parsed_artifacts["operating-point.json"])
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "physical source-state handoff disagrees with Stage A artifacts.",
        )
    scientific_limits = metrics.get("scientific_limits")
    if (
        not isinstance(scientific_limits, dict)
        or scientific_limits.get("run_includes_juvenile_fspm_transport")
        is not transport_expected
        or scientific_limits.get("fspm_surface_light_aggregation_executed")
        is not transport_expected
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "scientific limits disagree with the requested analysis scope.",
        )
    if metrics.get("fspm_execution") != {
        "reference_resolution_executes_transport": False,
        "run_includes_juvenile_fspm_transport": transport_expected,
        "aggregation_executed": transport_expected,
    }:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "FSPM execution semantics disagree with the requested analysis scope.",
        )
    (
        multispectral_metadata_sha256,
        fspm_aggregation_metadata_sha256,
    ) = _validate_multispectral_transport_artifacts(
        resolved_root,
        manifest=manifest,
        expected_run_id=expected_run_id,
        expected_system_id=str(system_id),
        expected_source_state_id=physical_source_state.source_state_id,
        expected_source_state=source_state_payload,
        expected_fspm_reference=metrics.get("fspm_target_policy"),
        transport_expected=transport_expected,
    )
    surface_metrics = metrics.get("fspm_surface_light_metrics")
    if not transport_expected and surface_metrics is not None:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "baseline-only metrics contain FSPM surface-light values.",
        )
    if transport_expected:
        aggregation_declaration = manifest.get("fspm_scientific_aggregation")
        aggregation_public = (
            aggregation_declaration.get("public_artifacts")
            if isinstance(aggregation_declaration, Mapping)
            else None
        )
        if not isinstance(aggregation_public, Mapping):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "FSPM aggregation public paths are missing.",
            )
        room_summary = _read_json_object(
            resolved_root / str(aggregation_public["room_surface_light_summary"])
        )
        aggregation_metadata = _read_json_object(
            resolved_root / str(aggregation_public["aggregation_metadata"])
        )
        expected_surface_metrics = {
            "modeled_physical_one_sided_leaf_area_m2": room_summary.get(
                "modeled_physical_one_sided_leaf_area_m2"
            ),
            "counts": room_summary.get("counts"),
            "surface_light": room_summary.get("surface_light"),
            "closure": room_summary.get("closure"),
            "raw_receiver_sha256_by_band": room_summary.get(
                "raw_receiver_sha256_by_band"
            ),
            "aggregation_metadata_sha256": (
                fspm_aggregation_metadata_sha256
            ),
            "aggregate_artifact_sha256_by_role": {
                str(record["role"]): str(record["sha256"])
                for record in aggregation_metadata.get(
                    "ordered_artifact_inventory", []
                )
                if isinstance(record, dict)
                and isinstance(record.get("role"), str)
                and isinstance(record.get("sha256"), str)
            },
        }
        extended_absorbed_metrics = isinstance(
            aggregation_metadata.get("absorbed_par_metrics"), Mapping
        )
        if (
            aggregation_metadata.get("schema_version")
            == FSPM_AGGREGATION_SCHEMA_VERSION
        ):
            expected_surface_metrics.update(
                {
                    "band_order": aggregation_metadata.get("band_order"),
                    "far_red_executed": aggregation_metadata.get(
                        "far_red_executed"
                    ),
                }
            )
        if extended_absorbed_metrics:
            expected_surface_metrics["absorbed_par_metrics"] = room_summary.get(
                "absorbed_par_metrics"
            )
        if surface_metrics != expected_surface_metrics:
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "run metrics disagree with authoritative FSPM surface aggregation.",
            )
        absorbed_metrics = room_summary.get("absorbed_par_metrics")
        absorbed_authorities = (
            absorbed_metrics.get("authorities")
            if isinstance(absorbed_metrics, Mapping)
            else None
        )
        metric_power = metrics.get("power")
        metric_ppf = metrics.get("ppf")
        if extended_absorbed_metrics and (
            not isinstance(absorbed_authorities, Mapping)
            or not isinstance(metric_power, Mapping)
            or not isinstance(metric_ppf, Mapping)
            or not _close_finite_metric(
                absorbed_authorities.get("emitted_par_ppf_umol_s"),
                metric_ppf.get("emitted_umol_s"),
            )
            or not _close_finite_metric(
                absorbed_authorities.get("modeled_electrical_power_w"),
                metric_power.get("effective_w"),
            )
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "Stage B efficiency denominators disagree with Stage A.",
            )
    if not (resolved_root / "ppfd.csv").read_text(encoding="utf-8").startswith(
        "x_m,y_m,z_m,ppfd_umol_m2_s\n"
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "PPFD CSV header is incompatible.",
        )
    csv_samples = _read_ppfd_csv(resolved_root / "ppfd.csv")
    field_sha256 = reference_plane_field_identity(csv_samples)
    visualization = _read_json_object(resolved_root / "visualization.json")
    if (
        visualization.get("schema_id") != VISUALIZATION_SCHEMA_ID
        or visualization.get("schema_version") != 1
        or visualization.get("run_id") != expected_run_id
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "visualization metadata identity is incompatible.",
        )
    field = visualization.get("field")
    metric_visualization = metrics.get("visualization")
    manifest_visualization = manifest.get("visualization")
    if not isinstance(field, dict) or not isinstance(metric_visualization, dict) or not isinstance(manifest_visualization, dict):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "visualization field agreement metadata is missing.",
        )
    grid_metadata = visualization.get("grid")
    try:
        expected_gridline_policy = heatmap_gridline_policy_metadata(
            detect_regular_grid(csv_samples)
        )
    except ValueError as exc:
        raise ProposedRunError(
            "artifact_validation_failed", "promotion", str(exc)
        ) from exc
    if (
        not isinstance(grid_metadata, dict)
        or grid_metadata.get("cell_gridlines") != expected_gridline_policy
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "heatmap cell-boundary gridline policy is incompatible.",
        )
    expected_count = len(csv_samples)
    identities = (
        field.get("identity_sha256"),
        metric_visualization.get("field_identity_sha256"),
        manifest_visualization.get("field_identity_sha256"),
    )
    counts = (
        field.get("sample_count"),
        metric_visualization.get("sample_count"),
        manifest_visualization.get("sample_count"),
    )
    uniformity = metrics.get("spatial_uniformity")
    uniformity_values = uniformity.get("values") if isinstance(uniformity, dict) else None
    try:
        recomputed_uniformity = compute_spatial_uniformity(
            tuple(sample.ppfd_umol_m2_s for sample in csv_samples)
        ).values_dict()
    except ValueError as exc:
        raise ProposedRunError(
            "artifact_validation_failed", "promotion", str(exc)
        ) from exc
    uniformity_agrees = (
        isinstance(uniformity_values, dict)
        and uniformity_values == recomputed_uniformity
    )
    if (
        any(identity != field_sha256 for identity in identities)
        or any(count != expected_count for count in counts)
        or not uniformity_agrees
        or metrics.get("achieved_mean_ppfd_umol_m2_s")
        != recomputed_uniformity["mean_ppfd"]
        or metrics.get("minimum_ppfd_umol_m2_s")
        != recomputed_uniformity["minimum_ppfd"]
        or metrics.get("maximum_ppfd_umol_m2_s")
        != recomputed_uniformity["maximum_ppfd"]
        or metrics.get("standard_deviation_ppfd_umol_m2_s")
        != recomputed_uniformity["population_standard_deviation_ppfd"]
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "CSV, metrics, heatmap, overlay, and scatter field identities disagree.",
        )
    request_fspm = manifest["request"].get("fspm_target")
    if not isinstance(request_fspm, dict):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "normalized FSPM request policy is missing.",
        )
    try:
        expected_fspm_policy = resolve_fspm_target_policy(
            mode=request_fspm.get("mode"),
            achieved_baseline_mean_ppfd=recomputed_uniformity["mean_ppfd"],
            tolerance_umol_m2_s=request_fspm.get("tolerance_umol_m2_s"),
            override_umol_m2_s=request_fspm.get("override_umol_m2_s"),
        ).to_dict()
    except (TypeError, ValueError) as exc:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "normalized FSPM request policy is invalid.",
        ) from exc
    if metrics.get("fspm_target_policy") != expected_fspm_policy:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "FSPM classification policy does not match the final achieved field.",
        )
    baseline_leaf_uniformity_sha256: str | None = None
    if shared_schema_version == 4:
        if baseline_leaf_scene is None:
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "schema-v4 baseline leaf-position geometry is missing.",
            )
        manifest_overlay_payload = manifest.get("authoritative_overlay_plan")
        if not isinstance(manifest_overlay_payload, Mapping):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "baseline leaf-position overlay authority is missing.",
            )
        try:
            expected_baseline_leaf = (
                build_baseline_leaf_uniformity_publication(
                    run_id=expected_run_id,
                    system_id=str(system_id),
                    analysis_scope=analysis_scope.value,
                    samples=csv_samples,
                    scene=baseline_leaf_scene,
                    overlay_payload=manifest_overlay_payload,
                    requested_lighting_target_umol_m2_s=request_record.get(
                        "target_ppfd_umol_m2_s"
                    ),
                    achieved_stage_a_mean_umol_m2_s=recomputed_uniformity[
                        "mean_ppfd"
                    ],
                    target_mode=str(request_fspm.get("mode")),
                    target_override_umol_m2_s=request_fspm.get(
                        "override_umol_m2_s"
                    ),
                    target_tolerance_umol_m2_s=request_fspm.get(
                        "tolerance_umol_m2_s"
                    ),
                )
            )
        except (TypeError, ValueError) as exc:
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                f"baseline leaf-position recomputation failed: {exc}",
            ) from exc
        baseline_leaf_path = resolved_root / BASELINE_LEAF_UNIFORMITY_FILENAME
        actual_baseline_leaf_bytes = baseline_leaf_path.read_bytes()
        actual_baseline_leaf = _read_json_object(baseline_leaf_path)
        expected_baseline_leaf_metrics = expected_baseline_leaf.metrics_payload()
        expected_baseline_leaf_manifest = expected_baseline_leaf.manifest_payload()
        available_outputs = manifest.get("available_visual_outputs")
        if (
            actual_baseline_leaf_bytes != expected_baseline_leaf.data
            or actual_baseline_leaf != expected_baseline_leaf.payload
            or (
                scene.get("viewer_resource_version") == VIEWER_RESOURCE_VERSION
                and scene.get("ppfd_heatmap", {}).get("target_coverage")
                != expected_baseline_leaf.target_coverage
            )
            or metrics.get("baseline_leaf_position_uniformity")
            != expected_baseline_leaf_metrics
            or manifest.get("baseline_leaf_position_uniformity")
            != expected_baseline_leaf_manifest
            or not isinstance(available_outputs, Mapping)
            or available_outputs.get("baseline_leaf_position_uniformity")
            is not expected_baseline_leaf.payload["available"]
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "baseline leaf-position artifact, summary, or bindings disagree.",
            )
        baseline_leaf_uniformity_sha256 = expected_baseline_leaf.sha256
    request_payload = manifest.get("request")
    display = visualization.get("display")
    if not isinstance(request_payload, dict) or not isinstance(display, dict):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "visualization target-centered display metadata is missing.",
        )
    reference_metadata = display.get("reference")
    if not isinstance(reference_metadata, dict):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "visualization reference metadata is missing.",
        )
    reference_kind = reference_metadata.get("kind")
    reference_value = display.get("reference_ppfd_umol_m2_s")
    if reference_kind == "requested_lighting_target":
        expected_policy = "requested_lighting_target_plus_or_minus_200"
        scatter_policy = "requested_target_window_ppfd_to_room_span_v1"
        expected_reference = request_payload.get("target_ppfd_umol_m2_s")
        if (
            manifest.get("system_id") == "hps"
            or display.get("requested_lighting_target_ppfd_umol_m2_s")
            != expected_reference
        ):
            reference_value = None
    elif reference_kind == "requested_sampled_ppfd_cap":
        expected_policy = "requested_sampled_ppfd_cap_plus_or_minus_200"
        scatter_policy = "requested_sampled_cap_window_ppfd_to_room_span_v1"
        expected_reference = request_payload.get("target_ppfd_umol_m2_s")
        if (
            manifest.get("system_id") == "hps"
            or request_payload.get("lighting_target_mode") != "target_capped"
            or display.get("requested_sampled_ppfd_cap_umol_m2_s")
            != expected_reference
        ):
            reference_value = None
    elif reference_kind == "achieved_final_baseline_mean":
        expected_policy = "achieved_final_baseline_mean_plus_or_minus_200"
        scatter_policy = "achieved_baseline_window_ppfd_to_room_span_v1"
        expected_reference = metrics.get("achieved_mean_ppfd_umol_m2_s")
        if (
            manifest.get("system_id") != "hps"
            or "target_ppfd_umol_m2_s" in request_payload
            or "requested_lighting_target_ppfd_umol_m2_s" in display
        ):
            reference_value = None
    else:
        expected_policy = ""
        scatter_policy = ""
        expected_reference = None
    if (
        reference_metadata.get("center_ppfd_umol_m2_s") != reference_value
        or reference_value != expected_reference
    ):
        reference_value = None
    if (
        isinstance(reference_value, bool)
        or not isinstance(reference_value, int | float)
        or not np.isfinite(reference_value)
        or reference_value <= 0.0
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "visualization reference center is invalid.",
        )
    reference_value = float(reference_value)
    expected_limits = [reference_value - 200.0, reference_value + 200.0]
    normalization = display.get("normalization")
    visible_legend = display.get("visible_legend")
    rasterization = display.get("rasterization")
    if (
        display.get("color_limits_ppfd_umol_m2_s") != expected_limits
        or display.get("color_limit_policy") != expected_policy
        or not isinstance(normalization, dict)
        or normalization.get("display_only") is not True
        or normalization.get("ppfd_values_modified") is not False
        or normalization.get("under_range_behavior")
        != "saturate_to_colormap_minimum_color"
        or normalization.get("over_range_behavior")
        != "saturate_to_colormap_maximum_color"
        or normalization.get("points_discarded") is not False
        or not isinstance(rasterization, dict)
        or rasterization.get("cell_gridlines_policy_id")
        != expected_gridline_policy["policy_id"]
        or visible_legend
        != {
            "minimum_ppfd_umol_m2_s": expected_limits[0],
            "center_ppfd_umol_m2_s": reference_value,
            "maximum_ppfd_umol_m2_s": expected_limits[1],
            "units": "umol/m^2/s",
        }
        or field.get("observed_minimum_ppfd_umol_m2_s")
        != min(sample.ppfd_umol_m2_s for sample in csv_samples)
        or field.get("observed_maximum_ppfd_umol_m2_s")
        != max(sample.ppfd_umol_m2_s for sample in csv_samples)
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "reference-centered display limits or raw observed bounds disagree.",
        )
    overlay = visualization.get("overlay")
    manifest_layout = manifest.get("layout")
    scientific_identity = manifest.get("scientific_identity")
    manifest_overlay = manifest.get("authoritative_overlay_plan")
    shared_overlay_agrees = (
        isinstance(overlay, dict)
        and isinstance(manifest_overlay, dict)
        and manifest_overlay.get("system_id") == system_id
        and overlay.get("system_id") == manifest_overlay.get("system_id")
        and overlay.get("policy_id") == manifest_overlay.get("policy_id")
        and overlay.get("coordinate_source")
        == manifest_overlay.get("coordinate_source")
        and overlay.get("room") == manifest_overlay.get("room")
        and overlay.get("rectangles") == manifest_overlay.get("rectangles")
        and overlay.get("lines") == manifest_overlay.get("lines")
        and overlay.get("fixtures") == manifest_overlay.get("fixtures")
        and overlay.get("style_policy")
        == overlay_style_policy_metadata(str(system_id))
        and overlay.get("geometry_inference") is False
        and manifest_overlay.get("geometry_inference") is False
    )
    manifest_room = manifest.get("room")
    manifest_quality = manifest.get("quality")
    runtime_provenance = manifest.get("runtime_provenance")
    engine_provenance = manifest.get("engine_provenance")
    if (
        not isinstance(request_payload, dict)
        or not isinstance(manifest_room, dict)
        or not isinstance(manifest_layout, dict)
        or not isinstance(manifest_overlay, dict)
        or not isinstance(manifest_quality, dict)
        or not isinstance(runtime_provenance, dict)
        or not isinstance(engine_provenance, dict)
        or not isinstance(scientific_identity, dict)
        or not isinstance(manifest_quality.get("name"), str)
        or not isinstance(manifest_quality.get("radiance_options"), list)
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "shared scientific identity inputs are missing or invalid.",
        )
    if (
        mounting_height is not None
        and engine_provenance.get("mounting_height") != mounting_height
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "engine and mounting-height provenance disagree.",
        )
    overlay_room = manifest_overlay.get("room")
    if (
        not isinstance(overlay_room, dict)
        or manifest_room.get("length_m") != overlay_room.get("length_x_m")
        or manifest_room.get("width_m") != overlay_room.get("width_y_m")
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "shared room and authoritative overlay dimensions disagree.",
        )
    room_identity_payload: Mapping[str, object]
    if active_domain is None:
        room_identity_payload = {
            "length_ft": manifest_room.get("length_ft"),
            "width_ft": manifest_room.get("width_ft"),
        }
    else:
        room_identity_payload = active_domain.to_payload()
    expected_scientific_identity = {
        "request_sha256": _hash_json(request_payload),
        "room_sha256": _hash_json(room_identity_payload),
        "fixture_layout_sha256": _hash_json(manifest_layout),
        "natural_fit_layout_sha256": natural_fit_sha256,
        "overlay_plan_sha256": _hash_json(manifest_overlay),
        "full_output_schedule_sha256": _hash_json(
            parsed_artifacts["full_output_schedule.json"]
        ),
        "target_control_sha256": _hash_json(
            parsed_artifacts["target_control.json"]
        ),
        "operating_point_sha256": _hash_json(
            parsed_artifacts["operating-point.json"]
        ),
        "physical_source_state_sha256": _sha256_stream(
            resolved_root / "physical-source-state.json"
        ),
        "reference_plane_field_sha256": field_sha256,
        "quality_sha256": _hash_json(
            {
                "quality": manifest_quality["name"],
                "radiance_options": manifest_quality["radiance_options"],
            }
        ),
        "runtime_software_sha256": _hash_json(
            scientific_runtime_identity(runtime_provenance)
        ),
        "engine_provenance_sha256": _hash_json(engine_provenance),
    }
    if active_domain is not None:
        expected_scientific_identity["active_domain_sha256"] = (
            active_domain.identity_sha256
        )
    if mounting_height is not None:
        expected_scientific_identity["mounting_height_sha256"] = _hash_json(
            mounting_height
        )
    if baseline_leaf_uniformity_sha256 is not None:
        expected_scientific_identity[
            "baseline_leaf_position_uniformity_artifact_sha256"
        ] = baseline_leaf_uniformity_sha256
        expected_scientific_identity[
            "baseline_leaf_position_uniformity_derivation_sha256"
        ] = expected_baseline_leaf.derivation_identity_sha256
    if multispectral_metadata_sha256 is not None:
        expected_scientific_identity[
            "multispectral_transport_metadata_sha256"
        ] = multispectral_metadata_sha256
    if fspm_aggregation_metadata_sha256 is not None:
        expected_scientific_identity[
            "fspm_scientific_aggregation_metadata_sha256"
        ] = fspm_aggregation_metadata_sha256
    if spectral_basis is not None:
        expected_scientific_identity["spectral_basis_identity_sha256"] = (
            _hash_json(spectral_basis)
        )
    if proposed_control is not None:
        expected_scientific_identity["proposed_control_identity_sha256"] = (
            _hash_json(proposed_control)
        )
    if proposed_source is not None:
        expected_scientific_identity["proposed_source_identity_sha256"] = (
            _hash_json(proposed_source)
        )
    if surface_flux is not None:
        expected_scientific_identity["surface_flux_display_metadata_sha256"] = (
            surface_flux.metadata.sha256
        )
        expected_scientific_identity["surface_flux_display_values_sha256"] = (
            surface_flux.patch_values.sha256
        )
        if surface_flux.calibration_coefficients is not None:
            expected_scientific_identity["surface_flux_display_calibration_sha256"] = (
                surface_flux.calibration_coefficients.sha256
            )
    expected_scientific_identity["scientific_run_sha256"] = _hash_json(
        expected_scientific_identity
    )
    if scientific_identity != expected_scientific_identity:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "shared scientific manifest hashes failed recomputation.",
        )
    if (
        not isinstance(overlay, dict)
        or not isinstance(manifest_layout, dict)
        or not isinstance(scientific_identity, dict)
        or not shared_overlay_agrees
        or overlay.get("layout_identity_sha256")
        != scientific_identity.get("fixture_layout_sha256")
        or overlay.get("source_field_identity_sha256") != field_sha256
        or overlay.get("source_sample_count") != expected_count
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "authoritative fixture overlay metadata disagrees with the layout plan.",
        )
    annotations = visualization.get("annotations")
    if (
        not isinstance(annotations, dict)
        or annotations.get("plain_heatmap") is not True
        or annotations.get("overlay_heatmap") is not False
        or annotations.get("value_transform") is not False
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "heatmap annotation metadata is incompatible.",
        )
    inventory = manifest.get("visualization_inventory")
    if not isinstance(inventory, dict):
        raise ProposedRunError(
            "artifact_validation_failed", "promotion", "visualization inventory is missing."
        )
    expected_inventory = {
        "ppfd-heatmap.png",
        "ppfd-heatmap-overlay.png",
        "ppfd-scatter.f32le.bin",
        "ppfd-scatter-viewer/index.html",
        "ppfd-scatter-viewer/styles.css",
        "ppfd-scatter-viewer/main.js",
        "ppfd-scatter-viewer/presentation-export.js",
        "visualization.json",
    }
    if set(inventory) != expected_inventory:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "visualization inventory is incomplete or contains unexpected files.",
        )
    for relative, record in inventory.items():
        if not isinstance(relative, str) or not isinstance(record, dict):
            raise ProposedRunError(
                "artifact_validation_failed", "promotion", "visualization inventory is invalid."
            )
        path = resolved_root / relative
        if (
            _contains_symlink(resolved_root, path)
            or not path.is_file()
            or not path.resolve().is_relative_to(resolved_root)
        ):
            raise ProposedRunError(
                "artifact_validation_failed", "promotion", f"visualization artifact is unsafe: {relative}"
            )
        data = path.read_bytes()
        if record.get("byte_length") != len(data) or record.get("sha256") != hashlib.sha256(data).hexdigest():
            raise ProposedRunError(
                "artifact_validation_failed", "promotion", f"visualization artifact hash failed: {relative}"
            )
    declared_artifacts = visualization.get("artifacts")
    if (
        not isinstance(declared_artifacts, dict)
        or any(
            declared_artifacts.get(relative) != inventory.get(relative)
            for relative in expected_inventory - {"visualization.json"}
        )
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "visualization metadata and manifest inventories disagree.",
        )
    transforms = visualization.get("transforms")
    expected_transform_keys = {
        "physics_recomputed",
        "target_rescaling",
        "symmetrization",
        "smoothing",
        "clipping",
        "correction",
        "hidden_normalization",
        "sample_exclusion",
    }
    if (
        not isinstance(transforms, dict)
        or set(transforms) != expected_transform_keys
        or any(value is not False for value in transforms.values())
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "visualization transform declarations are incompatible.",
        )
    for png_name in ("ppfd-heatmap.png", "ppfd-heatmap-overlay.png"):
        if not (resolved_root / png_name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ProposedRunError(
                "artifact_validation_failed", "promotion", f"PNG artifact is invalid: {png_name}"
            )
    scatter = visualization.get("scatter")
    if not isinstance(scatter, dict):
        raise ProposedRunError(
            "artifact_validation_failed", "promotion", "scatter schema is missing."
        )
    scatter_data = (resolved_root / "ppfd-scatter.f32le.bin").read_bytes()
    if len(scatter_data) != expected_count * 12:
        raise ProposedRunError(
            "artifact_validation_failed", "promotion", "scatter binary length is invalid."
        )
    scatter_values = np.frombuffer(scatter_data, dtype="<f4")
    expected_scatter_values = np.asarray(
        [
            component
            for sample in csv_samples
            for component in (
                sample.x_m,
                sample.y_m,
                sample.ppfd_umol_m2_s,
            )
        ],
        dtype="<f4",
    )
    horizontal_reference_span = max(
        max(sample.x_m for sample in csv_samples)
        - min(sample.x_m for sample in csv_samples),
        max(sample.y_m for sample in csv_samples)
        - min(sample.y_m for sample in csv_samples),
    )
    expected_vertical_transform = build_scatter_vertical_display_transform(
        requested_target_ppfd_umol_m2_s=reference_value,
        raw_minimum_ppfd_umol_m2_s=min(
            sample.ppfd_umol_m2_s for sample in csv_samples
        ),
        raw_maximum_ppfd_umol_m2_s=max(
            sample.ppfd_umol_m2_s for sample in csv_samples
        ),
        horizontal_reference_span_m=horizontal_reference_span,
        reference_kind=reference_kind,
        policy_id=scatter_policy,
    ).to_metadata()
    if (
        scatter.get("filename") != "ppfd-scatter.f32le.bin"
        or scatter.get("component_type") != "float32"
        or scatter.get("byte_order") != "little-endian"
        or scatter.get("record_layout") != "x_m,y_m,ppfd_umol_m2_s"
        or scatter.get("stride_bytes") != 12
        or scatter.get("count") != expected_count
        or scatter.get("byte_length") != expected_count * 12
        or len(scatter_values) != expected_count * 3
        or not np.all(np.isfinite(scatter_values))
        or not np.array_equal(scatter_values, expected_scatter_values)
        or scatter.get("sha256") != hashlib.sha256(scatter_data).hexdigest()
        or scatter.get("source_field_identity_sha256") != field_sha256
        or scatter.get("load_policy")
        != "main_application_action_opens_viewer_then_fetch_and_sha256_validate"
        or scatter.get("vertical_display_transform")
        != expected_vertical_transform
    ):
        raise ProposedRunError(
            "artifact_validation_failed", "promotion", "scatter binary validation failed."
        )


def _analysis_scope_validation_payload(
    scope: AnalysisScope,
) -> dict[str, object]:
    return {
        "schema_id": ANALYSIS_SCOPE_SCHEMA_ID,
        "schema_version": ANALYSIS_SCOPE_SCHEMA_VERSION,
        "value": scope.value,
        "label": ANALYSIS_SCOPE_LABELS[scope],
        "description": ANALYSIS_SCOPE_DESCRIPTIONS[scope],
    }


def _logical_stages_validation_payload(
    scope: AnalysisScope,
) -> list[dict[str, object]]:
    stages: list[dict[str, object]] = [
        {
            "order_index": 0,
            "stage_id": "baseline_ppfd",
            "role": "baseline",
            "plants_in_scientific_transport": False,
            "plant_geometry_in_viewer_is_display_only": True,
            "status": "complete",
        }
    ]
    if scope is AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM:
        stages.append(
            {
                "order_index": 1,
                "stage_id": "multispectral_fspm",
                "role": "FSPM",
                "plants_in_scientific_transport": True,
                "source_state_from_stage": "baseline_ppfd",
                "surface_light_aggregation_executed": True,
                "status": "complete",
            }
        )
    return stages


def _validate_surface_flux_display_artifacts(
    root: Path,
    *,
    manifest: Mapping[str, object],
    metrics: Mapping[str, object],
    expected_run_id: str,
    expected_quality: str,
    expected_plant_count: int,
    expected_layout_plan_hash: str,
    expected_available: bool,
) -> SurfaceFluxViewerArtifacts | None:
    declaration = manifest.get("fspm_surface_flux_display")
    viewer = manifest.get("viewer_publication")
    metric_declaration = metrics.get("fspm_surface_flux_coloring")
    outputs = manifest.get("available_visual_outputs")
    artifacts = manifest.get("artifacts")
    if not all(isinstance(item, Mapping) for item in (viewer, outputs, artifacts)):
        raise ValueError("surface-flux publication declarations are missing.")
    viewer = dict(viewer)
    outputs = dict(outputs)
    artifacts = dict(artifacts)
    declared_roles = {
        role
        for role in (
            "surface_flux_display_metadata",
            "surface_flux_display_values",
            "surface_flux_display_calibration",
        )
        if role in artifacts
    }
    if not expected_available:
        if (
            declaration is not None
            or metric_declaration is not None
            or viewer.get("surface_flux") is not None
            or outputs.get("fspm_surface_flux_coloring") is not False
            or declared_roles
        ):
            raise ValueError("baseline-only publication contains surface coloring.")
        return None
    declaration_version = (
        declaration.get("schema_version")
        if isinstance(declaration, Mapping)
        else None
    )
    expected_roles = {
        "surface_flux_display_metadata",
        "surface_flux_display_values",
    }
    if declaration_version == AUTHENTICATED_SURFACE_FLUX_DISPLAY_SCHEMA_VERSION:
        expected_roles.add("surface_flux_display_calibration")
    if (
        not isinstance(declaration, Mapping)
        or not isinstance(metric_declaration, Mapping)
        or not isinstance(viewer.get("surface_flux"), Mapping)
        or outputs.get("fspm_surface_flux_coloring") is not True
        or declared_roles != expected_roles
    ):
        raise ValueError("surface-flux display publication is incomplete.")

    metadata_record = declaration.get("metadata")
    values_record = declaration.get("patch_values")
    if not isinstance(metadata_record, Mapping) or not isinstance(
        values_record, Mapping
    ):
        raise ValueError("surface-flux artifact records are missing.")
    if declaration_version not in {
        SURFACE_FLUX_DISPLAY_SCHEMA_VERSION,
        AUTHENTICATED_SURFACE_FLUX_DISPLAY_SCHEMA_VERSION,
    }:
        raise ValueError(
            "surface-flux publications must use metadata schema version 2 or 3."
        )
    metadata_filename = (
        AUTHENTICATED_SURFACE_FLUX_METADATA_FILENAME
        if declaration_version == AUTHENTICATED_SURFACE_FLUX_DISPLAY_SCHEMA_VERSION
        else SURFACE_FLUX_METADATA_FILENAME
    )
    expected_records = [
        (
            metadata_filename,
            metadata_record,
            "surface_flux_display_metadata",
        ),
        (
            SURFACE_FLUX_VALUES_FILENAME,
            values_record,
            "surface_flux_display_values",
        ),
    ]
    if declaration_version == AUTHENTICATED_SURFACE_FLUX_DISPLAY_SCHEMA_VERSION:
        expected_records.append(
            (
                VIEWER_CALIBRATION_PAYLOAD_NAME,
                declaration.get("display_calibration_coefficients"),
                "surface_flux_display_calibration",
            )
        )
    display_artifacts: list[BinaryDisplayArtifact] = []
    for filename, record, role in expected_records:
        if (
            not isinstance(record, Mapping)
            or record.get("filename") != filename
            or isinstance(record.get("byte_length"), bool)
            or not isinstance(record.get("byte_length"), int)
            or record["byte_length"] <= 0
            or not isinstance(record.get("sha256"), str)
            or len(record["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in record["sha256"])
            or artifacts.get(role) != f"plant-layout-viewer/{filename}"
        ):
            raise ValueError("surface-flux artifact record is incompatible.")
        path = root / "plant-layout-viewer" / filename
        if (
            _contains_symlink(root, path)
            or not path.is_file()
            or not path.resolve().is_relative_to(root)
        ):
            raise ValueError("surface-flux artifact is missing or unsafe.")
        data = path.read_bytes()
        if len(data) != record["byte_length"]:
            raise ValueError("surface-flux artifact byte length is incompatible.")
        display_artifacts.append(
            BinaryDisplayArtifact(filename, data, str(record["sha256"]))
        )
    surface_flux = SurfaceFluxViewerArtifacts(*display_artifacts)
    metadata = json.loads(surface_flux.metadata.data)
    if not isinstance(metadata, dict):
        raise ValueError("surface-flux metadata must be a JSON object.")
    if declaration_version == AUTHENTICATED_SURFACE_FLUX_DISPLAY_SCHEMA_VERSION:
        return _validate_surface_flux_display_v3(
            root,
            surface_flux=surface_flux,
            metadata=metadata,
            declaration=declaration,
            metric_declaration=metric_declaration,
            viewer=viewer,
            expected_run_id=expected_run_id,
            expected_quality=expected_quality,
            expected_plant_count=expected_plant_count,
            expected_layout_plan_hash=expected_layout_plan_hash,
            expected_achieved_stage_a_mean=metrics.get(
                "achieved_mean_ppfd_umol_m2_s"
            ),
        )
    selection = select_quality_family(expected_quality)
    topology = metadata.get("topology")
    reference = metadata.get("reference")
    display_record = metadata.get("display_artifact")
    if not all(
        isinstance(item, Mapping)
        for item in (topology, reference, display_record)
    ):
        raise ValueError("surface-flux metadata contracts are missing.")
    expected_counts = {
        "plants": expected_plant_count,
        "leaves": expected_plant_count * 12,
        "faces": expected_plant_count * 1920,
        "patches": expected_plant_count * 192,
        "receivers": expected_plant_count * 384,
    }
    if (
        selection is None
        or expected_plant_count > MAX_DISPLAY_PLANT_COUNT
        or metadata.get("schema_id") != SURFACE_FLUX_DISPLAY_SCHEMA_ID
        or metadata.get("schema_version") != SURFACE_FLUX_DISPLAY_SCHEMA_VERSION
        or metadata.get("availability") != "available"
        or metadata.get("run_id") != expected_run_id
        or metadata.get("profile_id") != PROFILE_ID
        or metadata.get("quality_family") != selection.to_dict()
        or metadata.get("quality_family_mapping")
        != quality_family_mapping_contract()
        or metadata.get("selected_calibration_profile")
        != selection.profile.to_dict()
        or metadata.get("calibration_provenance") != calibration_provenance()
        or metadata.get("palettes") != palette_contract()
        or metadata.get("calibration_limitations")
        != [
            "Open-boundary uniform-upper-hemisphere calibration.",
            "Frozen juvenile Rex topology/material scope.",
            "Direct and Rigorous remain declared quality-family proxies.",
            (
                "Local-patch normalization removes fixed morphology response "
                "but intentionally preserves plant-position, occlusion, and "
                "directional-lighting differences."
            ),
            "Back receiver calibration and palette are intentionally deferred.",
        ]
        or metadata.get("failure_policy")
        != {
            "invalid_or_unavailable": "neutral plant material",
            "clear_stale_gpu_resources": True,
            "partial_scientific_coloring_allowed": False,
        }
        or topology.get("counts") != expected_counts
        or topology.get("canonical_profile_id") != PROFILE_ID
        or topology.get("layout_plan_hash") != expected_layout_plan_hash
        or topology.get("topology_sha256")
        != calibration_provenance()["topology_sha256"]
        or topology.get("receivers_sha256")
        != calibration_provenance()["receivers_sha256"]
        or topology.get("local_patches_per_plant") != 192
        or topology.get("global_patch_equation")
        != "global_patch_index = 192 * plant_index + local_patch_index"
        or topology.get("front_receiver_equation")
        != "384 * plant_index + 2 * local_patch_index"
        or topology.get("coefficient_is_average_over_all_64_plants") is not True
        or topology.get("coefficient_position_dependence") is not False
        or topology.get("instance_mapping")
        != "instance_id equals canonical plant_index"
        or topology.get("front_back_selection")
        != "fragment gl_FrontFacing selects front or back"
        or topology.get("ordering")
        != "Y-major/X-minor plants; plant-major canonical patches"
    ):
        raise ValueError("surface-flux metadata identity is incompatible.")
    if metadata.get("display_equation") != {
        "front": {
            "formula": (
                "u[p,k,m] = q[p,k,m] / "
                "(gamma[quality_family,m,k] * R)"
            ),
            "variable": "u",
            "q_authority": (
                "Phase 27G-C raw Float64 PAR surface-light density"
            ),
            "gamma_index": (
                "canonical local_patch_index k in [0, 191], reused across plants"
            ),
            "u_equals_one_meaning": (
                "Expected-equivalent exposure for the same canonical local "
                "patch under the fixed neutral upper-hemisphere calibration; "
                "not a leaf target and not raw-q uniformity."
            ),
            "front_beta_used_for_coloring": False,
        },
        "back": {
            "formula": "z = q / (beta * R)",
            "variable": "z",
            "q_authority": (
                "Phase 27G-C raw Float64 PAR surface-light density"
            ),
            "z_equals_one_meaning": (
                "expected-equivalent neutral-reference exposure; not a leaf target"
            ),
            "back_beta_used_for_coloring": True,
        },
        "transport_correction": False,
        "raw_q_modified": False,
    }:
        raise ValueError("surface-flux normalization equations are incompatible.")
    if (
        display_record.get("filename") != surface_flux.patch_values.filename
        or display_record.get("byte_length") != surface_flux.patch_values.byte_size
        or display_record.get("sha256") != surface_flux.patch_values.sha256
        or display_record.get("component_type") != "float32"
        or display_record.get("byte_order") != "little-endian"
        or display_record.get("row_count") != expected_counts["patches"]
        or display_record.get("stride_bytes") != 16
        or display_record.get("byte_length") != expected_counts["patches"] * 16
        or display_record.get("record_layout")
        != (
            "front_incident_par, back_incident_par, "
            "front_absorbed_par, back_absorbed_par"
        )
        or display_record.get("channel_semantics")
        != "raw q; neither front u nor back z"
        or display_record.get("channel_units") != "umol/m^2/s"
        or display_record.get("raw_q_float32_derivative") is not True
        or display_record.get("raw_q_modified") is not False
        or display_record.get("normalization_applied_to_artifact") is not False
        or "display_only_float32_derivative" in display_record
        or display_record.get("texture_layout")
        != {
            "format": "RGBA32F",
            "width": 192,
            "height": expected_plant_count,
            "texel_count": expected_counts["patches"],
            "bounded_maximum_plant_count": MAX_DISPLAY_PLANT_COUNT,
        }
    ):
        raise ValueError("surface-flux display payload authority is incompatible.")
    if any(
        not math.isfinite(value) or value < 0.0
        for (value,) in struct.iter_unpack("<f", surface_flux.patch_values.data)
    ):
        raise ValueError("surface-flux display payload contains an invalid value.")
    request = manifest.get("request")
    if not isinstance(request, Mapping):
        raise ValueError("surface-flux Stage A request provenance is missing.")
    requested_reference = request.get("target_ppfd_umol_m2_s")
    try:
        if requested_reference is None:
            reference_contract = select_surface_flux_reference(
                operating_policy="fixed-output",
                achieved_stage_a_mean_ppfd_umol_m2_s=metrics.get(
                    "achieved_mean_ppfd_umol_m2_s"
                ),
            )
        else:
            reference_contract = select_surface_flux_reference(
                operating_policy="target-controlled",
                requested_stage_a_target_ppfd_umol_m2_s=requested_reference,
                achieved_stage_a_mean_ppfd_umol_m2_s=metrics.get(
                    "achieved_mean_ppfd_umol_m2_s"
                ),
            )
    except (SurfaceFluxDisplayError, TypeError, ValueError) as exc:
        raise ValueError("surface-flux Stage A reference is incompatible.") from exc
    if dict(reference) != reference_contract.to_dict():
        raise ValueError("surface-flux Stage A reference is incompatible.")
    aggregation_declaration = manifest.get("fspm_scientific_aggregation")
    transport_declaration = manifest.get("multispectral_transport")
    boundary = metadata.get("scientific_artifact_boundary")
    if (
        not isinstance(aggregation_declaration, Mapping)
        or not isinstance(transport_declaration, Mapping)
        or not isinstance(boundary, Mapping)
    ):
        raise ValueError("surface-flux scientific boundary is missing.")
    aggregation_record = aggregation_declaration.get("metadata_artifact")
    transport_record = transport_declaration.get("metadata_artifact")
    if not isinstance(aggregation_record, Mapping) or not isinstance(
        transport_record, Mapping
    ):
        raise ValueError("surface-flux source metadata authorities are missing.")
    aggregation_metadata_path = aggregation_record.get(
        "path", "fspm-aggregation/aggregation.v1.json"
    )
    transport_metadata_path = transport_record.get(
        "path", "fspm-transport/transport.v2.json"
    )
    if not isinstance(aggregation_metadata_path, str) or not isinstance(
        transport_metadata_path, str
    ):
        raise ValueError("surface-flux source metadata paths are missing.")
    aggregation_metadata = _read_json_object(
        root / aggregation_metadata_path
    )
    transport_metadata = _read_json_object(root / transport_metadata_path)
    inventory = aggregation_metadata.get("ordered_artifact_inventory")
    if not isinstance(inventory, list):
        raise ValueError("surface-flux aggregation inventory is missing.")
    inventory_by_role = {
        item["role"]: item
        for item in inventory
        if isinstance(item, dict) and isinstance(item.get("role"), str)
    }
    source_authorities = aggregation_metadata.get(
        "material_coefficient_authorities"
    )
    if not isinstance(source_authorities, list):
        raise ValueError("surface-flux material authorities are missing.")
    aggregation_schema_version = boundary.get("aggregation_schema_version")
    transport_schema_version = boundary.get("transport_schema_version")
    executed_band_order = tuple(
        transport_metadata.get(
            "band_order",
            BAND_ORDER if transport_schema_version == 2 else (),
        )
    )
    expected_material_authorities = [
        {
            "band_id": authority.get("band_id"),
            "material_sha256": authority.get("material_sha256"),
            "material_provenance_sha256": authority.get(
                "material_provenance_sha256"
            ),
            "raw_receiver_sha256": authority.get("raw_receiver_sha256"),
        }
        for authority in source_authorities
        if isinstance(authority, Mapping)
    ]
    if (
        boundary.get("aggregation_schema_id") != FSPM_AGGREGATION_SCHEMA_ID
        or boundary.get("aggregation_schema_version")
        != aggregation_schema_version
        or boundary.get("transport_schema_id")
        != JUVENILE_MULTISPECTRAL_SCHEMA_ID
        or boundary.get("transport_schema_version")
        != transport_schema_version
        or boundary.get("aggregation_metadata_sha256")
        != aggregation_record.get("sha256")
        or boundary.get("transport_metadata_sha256")
        != transport_record.get("sha256")
        or boundary.get("compact_receiver_index_sha256")
        != transport_metadata.get("compact_receiver_index", {}).get("sha256")
        or boundary.get("patch_float64_artifact")
        != inventory_by_role.get("fspm_patch_surface_light")
        or boundary.get("room_summary_artifact")
        != inventory_by_role.get("fspm_room_surface_light_summary")
        or boundary.get("material_coefficient_authorities")
        != expected_material_authorities
        or (
            aggregation_schema_version != 1
            and (
                boundary.get("executed_band_order")
                != list(executed_band_order)
                or boundary.get("far_red_executed")
                is not (executed_band_order == BAND_ORDER)
            )
        )
        or boundary.get("raw_float64_values_modified") is not False
        or boundary.get("far_red_read_into_display_values") is not False
    ):
        raise ValueError("surface-flux Phase 27G-C authority chain is incompatible.")
    patch_authority = inventory_by_role.get("fspm_patch_surface_light")
    expected_patch_path = (
        "fspm-aggregation/patch-surface-light.v1.f64le.bin"
        if aggregation_schema_version == 1
        else "fspm-aggregation/patch-surface-light.v2.f64le.bin"
    )
    patch_struct, _patch_indices, patch_float_fields = (
        binary_contract_for_band_order(executed_band_order)["patch"]
    )
    if (
        not isinstance(patch_authority, Mapping)
        or patch_authority.get("path")
        != expected_patch_path
    ):
        raise ValueError("surface-flux Phase 27G-C patch route is incompatible.")
    try:
        patch_path, _patch_sha256 = _validate_scientific_artifact_record(
            root,
            patch_authority,
            expected_role="fspm_patch_surface_light",
            expected_path=expected_patch_path,
            expected_media_type="application/octet-stream",
            expected_row_count=expected_counts["patches"],
            expected_stride=patch_struct.size,
        )
        rebuilt_values, rebuilt_legends = _convert_patch_values(
            patch_path,
            expected_counts=expected_counts,
            selection=selection,
            reference=reference_contract,
            patch_struct=patch_struct,
            patch_float_fields=patch_float_fields,
        )
    except (
        OSError,
        ProposedRunError,
        SurfaceFluxDisplayError,
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            "surface-flux Phase 27G-C display derivative is incompatible."
        ) from exc
    if surface_flux.patch_values.data != rebuilt_values:
        raise ValueError(
            "surface-flux Float32 artifact is not the unchanged raw-q derivative."
        )
    if metadata.get("legends") != rebuilt_legends:
        raise ValueError(
            "surface-flux legends do not match the authenticated raw-q derivative."
        )
    if metadata.get("metrics") != {
        "available": ["incident_par", "absorbed_par"],
        "incident_par": "blue + green + orange + red incident density",
        "absorbed_par": "blue + green + orange + red absorbed density",
        "far_red_excluded": True,
    }:
        raise ValueError("surface-flux PAR metric identity is incompatible.")
    expected_declaration = {
        "schema_id": SURFACE_FLUX_DISPLAY_SCHEMA_ID,
        "schema_version": SURFACE_FLUX_DISPLAY_SCHEMA_VERSION,
        "metadata": dict(metadata_record),
        "patch_values": dict(values_record),
        "quality_family": metadata["quality_family"],
        "reference": reference,
        "display_only": True,
    }
    expected_viewer = {
        "metadata": surface_flux.metadata.filename,
        "metadata_sha256": surface_flux.metadata.sha256,
        "patch_values": surface_flux.patch_values.filename,
        "patch_values_sha256": surface_flux.patch_values.sha256,
    }
    expected_metric_declaration = {
        "available": True,
        "display_only": True,
        "raw_scientific_values_modified": False,
        "metadata_artifact": surface_flux.metadata.filename,
        "metadata_sha256": surface_flux.metadata.sha256,
        "quality_family": metadata["quality_family"],
        "reference": reference,
        "metrics": ["incident_par", "absorbed_par"],
        "far_red_excluded": True,
    }
    if (
        dict(declaration) != expected_declaration
        or viewer.get("surface_flux") != expected_viewer
        or dict(metric_declaration) != expected_metric_declaration
    ):
        raise ValueError("surface-flux declarations disagree with their artifacts.")
    return surface_flux


def _validate_surface_flux_display_v3(
    root: Path,
    *,
    surface_flux: SurfaceFluxViewerArtifacts,
    metadata: Mapping[str, object],
    declaration: Mapping[str, object],
    metric_declaration: Mapping[str, object],
    viewer: Mapping[str, object],
    expected_run_id: str,
    expected_quality: str,
    expected_plant_count: int,
    expected_layout_plan_hash: str,
    expected_achieved_stage_a_mean: object,
) -> SurfaceFluxViewerArtifacts:
    """Authenticate the additive v3 resource and unchanged raw-q derivative."""

    calibration_artifact = surface_flux.calibration_coefficients
    if calibration_artifact is None:
        raise ValueError("surface-flux metadata v3 coefficient payload is missing.")
    quality = metadata.get("quality_family")
    profile = metadata.get("selected_calibration_profile")
    provenance = metadata.get("calibration_provenance")
    resource = metadata.get("calibration_resource")
    availability = metadata.get("calibration_availability")
    topology = metadata.get("topology")
    reference = metadata.get("reference")
    equation = metadata.get("display_equation")
    palettes = metadata.get("palettes")
    display = metadata.get("display_artifact")
    boundary = metadata.get("scientific_artifact_boundary")
    if not all(
        isinstance(value, Mapping)
        for value in (
            quality,
            profile,
            provenance,
            resource,
            availability,
            topology,
            reference,
            equation,
            palettes,
            display,
            boundary,
        )
    ):
        raise ValueError("surface-flux metadata v3 contracts are incomplete.")
    dispatch = display_quality_dispatch_contract().get(expected_quality)
    if not isinstance(dispatch, Mapping):
        raise ValueError("surface-flux metadata v3 quality dispatch is invalid.")
    family = dispatch["coefficient_family"]
    expected_counts = {
        "plants": expected_plant_count,
        "leaves": expected_plant_count * 12,
        "faces": expected_plant_count * 1920,
        "patches": expected_plant_count * 192,
        "receivers": expected_plant_count * 384,
    }
    expected_reference = select_authenticated_achieved_surface_flux_reference(
        operating_policy=str(reference.get("operating_policy")),
        achieved_stage_a_mean_ppfd_umol_m2_s=expected_achieved_stage_a_mean,
    ).to_dict()
    if (
        metadata.get("schema_id") != SURFACE_FLUX_DISPLAY_SCHEMA_ID
        or metadata.get("schema_version")
        != AUTHENTICATED_SURFACE_FLUX_DISPLAY_SCHEMA_VERSION
        or metadata.get("availability") != "available"
        or metadata.get("run_id") != expected_run_id
        or metadata.get("profile_id") != PROFILE_ID
        or metadata.get("quality_family_mapping")
        != display_quality_dispatch_contract()
        or quality.get("evaluated_quality") != expected_quality
        or quality.get("coefficient_family") != family
        or quality.get("display_only_proxy") != dispatch["display_only_proxy"]
        or quality.get("raw_transport_proxied") is not False
        or profile.get("resource_id") != DISPLAY_CALIBRATION_RESOURCE_ID
        or profile.get("coefficient_family") != family
        or profile.get("estimator") != DISPLAY_CALIBRATION_ESTIMATOR_ID
        or profile.get("coefficient_count_per_block") != 192
        or profile.get("block_order")
        != ["front_incident", "front_absorbed", "back_incident", "back_absorbed"]
        or profile.get("all_cells_available") is not True
        or profile.get("threshold_masking_applied") is not False
        or provenance.get("resource_id") != DISPLAY_CALIBRATION_RESOURCE_ID
        or provenance.get("estimator") != DISPLAY_CALIBRATION_ESTIMATOR_ID
        or provenance.get("topology_sha256") != D5_TOPOLOGY_SHA256
        or provenance.get("receivers_sha256") != D5_RECEIVERS_SHA256
        or provenance.get("sampling_profile_id") != D5_SAMPLING_PROFILE_ID
        or provenance.get("c2_report_sha256") != PINNED_C2_REPORT_SHA256
        or provenance.get("c2_completion_sha256")
        != PINNED_C2_COMPLETION_SHA256
        or provenance.get("d5_b1_modified") is not False
        or dict(reference) != expected_reference
        or metric_declaration.get("reference") != expected_reference
        or topology.get("counts") != expected_counts
        or topology.get("layout_plan_hash") != expected_layout_plan_hash
        or topology.get("topology_sha256") != D5_TOPOLOGY_SHA256
        or topology.get("receivers_sha256") != D5_RECEIVERS_SHA256
        or topology.get("sampling_profile_id") != D5_SAMPLING_PROFILE_ID
        or topology.get("front_receiver_equation")
        != "384 * plant_index + 2 * local_patch_index"
        or topology.get("back_receiver_equation")
        != "384 * plant_index + 2 * local_patch_index + 1"
        or availability.get("available_cell_count")
        != DISPLAY_CALIBRATION_COEFFICIENT_COUNT
        or availability.get("masked_cell_count") != 0
        or availability.get("all_authenticated_finite_positive_cells_available")
        is not True
        or availability.get("repeatability_threshold_applied") is not False
        or availability.get("a2_failure_mask_applied") is not False
    ):
        raise ValueError("surface-flux metadata v3 identity is incompatible.")
    front_palette = palettes.get("front")
    back_palette = palettes.get("back")
    if (
        equation.get("formula")
        != (
            "u[p,k,s,m] = q[p,k,s,m] / "
            "(gamma[coefficient_family,s,m,k] * R)"
        )
        or equation.get("variable") != "u"
        or equation.get("gamma_estimator")
        != DISPLAY_CALIBRATION_ESTIMATOR_ID
        or equation.get("reference") != "authenticated achieved Stage A mean"
        or equation.get("raw_rgba_order")
        != ["front_incident", "back_incident", "front_absorbed", "back_absorbed"]
        or equation.get("coefficient_block_order")
        != ["front_incident", "front_absorbed", "back_incident", "back_absorbed"]
        or equation.get("explicit_order_remapping") != [0, 2, 1, 3]
        or equation.get("transport_correction") is not False
        or equation.get("raw_q_modified") is not False
        or not isinstance(front_palette, Mapping)
        or not isinstance(back_palette, Mapping)
        or front_palette.get("variable") != "u"
        or back_palette.get("variable") != "u"
        or front_palette.get("shared_across_metrics_systems_and_quality_families")
        is not True
        or back_palette.get("shared_across_metrics_systems_and_quality_families")
        is not True
        or front_palette.get("per_run_extrema_normalization") is not False
        or back_palette.get("per_run_extrema_normalization") is not False
        or palettes != authenticated_palette_contract()
    ):
        raise ValueError("surface-flux metadata v3 equation or palette is incompatible.")
    if (
        resource.get("filename") != calibration_artifact.filename
        or resource.get("byte_length") != DISPLAY_CALIBRATION_BYTE_LENGTH
        or resource.get("sha256") != calibration_artifact.sha256
        or resource.get("resource_id") != DISPLAY_CALIBRATION_RESOURCE_ID
        or resource.get("component_type") != "float64"
        or resource.get("byte_order") != "little-endian"
        or resource.get("stride_bytes") != 8
        or resource.get("coefficient_count")
        != DISPLAY_CALIBRATION_COEFFICIENT_COUNT
        or resource.get("family_order") != ["standard", "quality"]
        or resource.get("block_order")
        != ["front_incident", "front_absorbed", "back_incident", "back_absorbed"]
        or resource.get("values_per_block") != 192
    ):
        raise ValueError("surface-flux metadata v3 coefficient authority failed.")
    coefficients = tuple(
        value[0]
        for value in struct.iter_unpack("<d", calibration_artifact.data)
    )
    if (
        len(coefficients) != DISPLAY_CALIBRATION_COEFFICIENT_COUNT
        or any(not math.isfinite(value) or value <= 0.0 for value in coefficients)
    ):
        raise ValueError("surface-flux metadata v3 coefficient payload is invalid.")
    if (
        display.get("filename") != surface_flux.patch_values.filename
        or display.get("sha256") != surface_flux.patch_values.sha256
        or display.get("byte_length") != surface_flux.patch_values.byte_size
        or display.get("record_layout")
        != (
            "front_incident_par, back_incident_par, "
            "front_absorbed_par, back_absorbed_par"
        )
        or display.get("raw_q_float32_derivative") is not True
        or display.get("raw_q_modified") is not False
        or display.get("normalization_applied_to_artifact") is not False
    ):
        raise ValueError("surface-flux metadata v3 raw derivative is invalid.")
    if any(
        not math.isfinite(value) or value < 0.0
        for (value,) in struct.iter_unpack("<f", surface_flux.patch_values.data)
    ):
        raise ValueError("surface-flux metadata v3 raw derivative has invalid values.")
    legends = metadata.get("legends")
    if not isinstance(legends, Mapping):
        raise ValueError("surface-flux metadata v3 legends are missing.")
    for metric in ("incident_par", "absorbed_par"):
        metric_legends = legends.get(metric)
        if not isinstance(metric_legends, Mapping):
            raise ValueError("surface-flux metadata v3 metric legend is missing.")
        for side in ("front", "back"):
            legend = metric_legends.get(side)
            if (
                not isinstance(legend, Mapping)
                or legend.get("coefficient_estimator")
                != DISPLAY_CALIBRATION_ESTIMATOR_ID
                or legend.get("coefficient_count") != 192
                or legend.get("all_cells_available") is not True
                or legend.get("availability_mask_applied") is not False
            ):
                raise ValueError(
                    "surface-flux metadata v3 front/back all-cell legend is invalid."
                )
    patch_record = boundary.get("patch_float64_artifact")
    if not isinstance(patch_record, Mapping):
        raise ValueError("surface-flux metadata v3 raw Stage C authority is missing.")
    relative = patch_record.get("path")
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("surface-flux metadata v3 raw Stage C path is unsafe.")
    patch_path = root / relative
    if (
        _contains_symlink(root, patch_path)
        or not patch_path.is_file()
        or not patch_path.resolve().is_relative_to(root)
        or patch_path.stat().st_size != patch_record.get("byte_length")
        or hashlib.sha256(patch_path.read_bytes()).hexdigest()
        != patch_record.get("sha256")
    ):
        raise ValueError("surface-flux metadata v3 raw Stage C authentication failed.")
    executed = tuple(boundary.get("executed_band_order", ()))
    patch_struct, _indices, patch_fields = binary_contract_for_band_order(executed)[
        "patch"
    ]
    rebuilt, _legends = _convert_patch_values(
        patch_path,
        expected_counts=expected_counts,
        selection=select_quality_family(expected_quality),
        reference=select_authenticated_achieved_surface_flux_reference(
            operating_policy=str(reference["operating_policy"]),
            achieved_stage_a_mean_ppfd_umol_m2_s=reference["value"],
        ),
        sampling_profile_id=D5_SAMPLING_PROFILE_ID,
        patch_struct=patch_struct,
        patch_float_fields=patch_fields,
    )
    if rebuilt != surface_flux.patch_values.data:
        raise ValueError(
            "surface-flux metadata v3 Float32 bytes are not the unchanged raw-q derivative."
        )
    expected_viewer = {
        "metadata": surface_flux.metadata.filename,
        "metadata_sha256": surface_flux.metadata.sha256,
        "patch_values": surface_flux.patch_values.filename,
        "patch_values_sha256": surface_flux.patch_values.sha256,
        "display_calibration_coefficients": calibration_artifact.filename,
        "display_calibration_coefficients_sha256": calibration_artifact.sha256,
    }
    if viewer.get("surface_flux") != expected_viewer:
        raise ValueError("surface-flux metadata v3 viewer declaration is incompatible.")
    if declaration.get("display_calibration_coefficients") != {
        "filename": calibration_artifact.filename,
        "byte_length": calibration_artifact.byte_size,
        "sha256": calibration_artifact.sha256,
    }:
        raise ValueError("surface-flux metadata v3 manifest declaration is incompatible.")
    expected_declaration = {
        "schema_id": SURFACE_FLUX_DISPLAY_SCHEMA_ID,
        "schema_version": AUTHENTICATED_SURFACE_FLUX_DISPLAY_SCHEMA_VERSION,
        "metadata": {
            "filename": surface_flux.metadata.filename,
            "byte_length": surface_flux.metadata.byte_size,
            "sha256": surface_flux.metadata.sha256,
        },
        "patch_values": {
            "filename": surface_flux.patch_values.filename,
            "byte_length": surface_flux.patch_values.byte_size,
            "sha256": surface_flux.patch_values.sha256,
        },
        "display_calibration_coefficients": {
            "filename": calibration_artifact.filename,
            "byte_length": calibration_artifact.byte_size,
            "sha256": calibration_artifact.sha256,
        },
        "quality_family": dict(quality),
        "reference": dict(reference),
        "display_only": True,
    }
    expected_metric = {
        "available": True,
        "display_only": True,
        "raw_scientific_values_modified": False,
        "metadata_artifact": surface_flux.metadata.filename,
        "metadata_sha256": surface_flux.metadata.sha256,
        "quality_family": dict(quality),
        "reference": dict(reference),
        "metrics": ["incident_par", "absorbed_par"],
        "far_red_excluded": True,
    }
    if dict(declaration) != expected_declaration or dict(metric_declaration) != expected_metric:
        raise ValueError("surface-flux metadata v3 publication declarations disagree.")
    return surface_flux


def _validate_multispectral_transport_artifacts(
    root: Path,
    *,
    manifest: Mapping[str, object],
    expected_run_id: str,
    expected_system_id: str,
    expected_source_state_id: str,
    expected_source_state: Mapping[str, object],
    expected_fspm_reference: object,
    transport_expected: bool,
) -> tuple[str | None, str | None]:
    declaration = manifest.get("multispectral_transport")
    aggregation_declaration = manifest.get("fspm_scientific_aggregation")
    artifacts = manifest.get("artifacts")
    outputs = manifest.get("available_visual_outputs")
    if not isinstance(artifacts, dict) or not isinstance(outputs, dict):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "shared FSPM artifact declarations are missing.",
        )
    transport_roles = {
        str(role)
        for role in artifacts
        if str(role).startswith("band_")
        or str(role).startswith("multispectral_")
    }
    aggregation_roles = {
        "aggregation_metadata",
        "patch_surface_light",
        "leaf_surface_light",
        "plant_surface_light",
        "room_surface_light_summary",
        "scientific_derivation_graph",
    }
    declared_aggregation_roles = set(artifacts) & aggregation_roles
    if not transport_expected:
        if (
            declaration is not None
            or aggregation_declaration is not None
            or transport_roles
            or declared_aggregation_roles
            or outputs.get("raw_multispectral_fspm_receivers") is not False
            or outputs.get("fspm_surface_light_metrics") is not False
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "baseline-only publication contains FSPM transport artifacts.",
            )
        return None, None
    if (
        not isinstance(declaration, dict)
        or not isinstance(aggregation_declaration, dict)
        or outputs.get("raw_multispectral_fspm_receivers") is not True
        or outputs.get("fspm_surface_light_metrics") is not True
        or not isinstance(expected_fspm_reference, Mapping)
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "FSPM transport or aggregation declaration is incomplete.",
        )

    transport_schema_version = declaration.get("transport_schema_version")
    historical_transport = transport_schema_version == 2
    if transport_schema_version not in {2, JUVENILE_MULTISPECTRAL_SCHEMA_VERSION}:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "multispectral transport schema version is unsupported.",
        )
    request_record = manifest.get("request")
    if not isinstance(request_record, Mapping):
        raise ProposedRunError(
            "artifact_validation_failed", "promotion", "run request is missing."
        )
    if historical_transport:
        if "include_far_red" in request_record:
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "historical transport request contains a future far-red field.",
            )
        include_far_red = True
        executed_band_order = BAND_ORDER
        transport_metadata_relative = "fspm-transport/transport.v2.json"
    else:
        include_far_red = request_record.get("include_far_red")
        if not isinstance(include_far_red, bool):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "multispectral request must authenticate include_far_red.",
            )
        executed_band_order = BAND_ORDER if include_far_red else PAR_BAND_ORDER
        transport_metadata_relative = "fspm-transport/transport.v4.json"
    expected_public_artifacts = {
        "transport_metadata": transport_metadata_relative,
        "compact_receiver_index": (
            "fspm-transport/scene/receiver-index.v1.json"
        ),
        "plant_origins": (
            "fspm-transport/scene/plant-origins.v1.f64le.bin"
        ),
        "scene_export_manifest": (
            "fspm-transport/scene/export-manifest.v2.json"
        ),
        **{
            f"band_{band_id}_receiver_values": (
                f"fspm-transport/bands/{index:02d}-{band_id}/"
                "receiver-values.v1.f64le.bin"
            )
            for index, band_id in enumerate(executed_band_order)
        },
    }
    if declaration.get("public_artifacts") != expected_public_artifacts:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "multispectral public artifact inventory is incompatible.",
        )
    expected_run_roles = {
        "multispectral_transport_metadata": expected_public_artifacts[
            "transport_metadata"
        ],
        "multispectral_compact_receiver_index": expected_public_artifacts[
            "compact_receiver_index"
        ],
        "multispectral_plant_origins": expected_public_artifacts[
            "plant_origins"
        ],
        **{
            role: relative
            for role, relative in expected_public_artifacts.items()
            if role.startswith("band_")
        },
    }
    if transport_roles != set(expected_run_roles) or any(
        artifacts.get(role) != relative
        for role, relative in expected_run_roles.items()
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "multispectral manifest artifact roles are incomplete.",
        )

    metadata_path, metadata_sha256 = _validate_raw_artifact_record(
        root,
        declaration.get("metadata_artifact"),
        expected_role="multispectral_transport_metadata",
        expected_path=expected_public_artifacts["transport_metadata"],
        expected_media_type="application/json",
        expected_row_count=None,
    )
    metadata = _read_json_object(metadata_path)
    if (
        metadata.get("schema_id") != JUVENILE_MULTISPECTRAL_SCHEMA_ID
        or metadata.get("schema_version") != transport_schema_version
        or metadata.get("run_id") != expected_run_id
        or metadata.get("system_id") != expected_system_id
        or metadata.get("source_state_id") != expected_source_state_id
        or (
            not historical_transport
            and (
                declaration.get("room_model_identity_sha256")
                != PRODUCTION_ROOM_MODEL_IDENTITY_SHA256
                or declaration.get("room_text_sha256")
                != metadata.get("room_text_sha256")
                or metadata.get("room_model")
                != production_room_model_payload()
                or metadata.get("room_model_identity_sha256")
                != PRODUCTION_ROOM_MODEL_IDENTITY_SHA256
                or not isinstance(metadata.get("room_text_sha256"), str)
                or len(metadata["room_text_sha256"]) != 64
            )
        )
        or metadata.get("fspm_reference") != dict(expected_fspm_reference)
        or metadata.get("reference_resolution_executes_transport") is not False
        or metadata.get("run_includes_juvenile_fspm_transport") is not True
        or metadata.get("fspm_reference_controls_transport") is not False
        or metadata.get("band_order") != list(executed_band_order)
        or metadata.get("par_band_order") != list(PAR_BAND_ORDER)
        or metadata.get("far_red_preserved_separately") is not True
        or metadata.get("units") != "umol/m^2/s"
        or metadata.get("component_type") != "float64"
        or metadata.get("byte_order") != "little-endian"
        or metadata.get("stride_bytes") != 8
        or metadata.get("record_layout")
        != "one band photon-flux-density value per global receiver"
        or (
            not historical_transport
            and (
                metadata.get("include_far_red") is not include_far_red
                or metadata.get("executed_band_order")
                != list(executed_band_order)
                or metadata.get("far_red_executed") is not include_far_red
            )
        )
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "multispectral transport metadata identity is incompatible.",
        )
    scope = metadata.get("analysis_scope")
    stage = metadata.get("logical_stage")
    source_planning = metadata.get("source_planning")
    stage_boundary = metadata.get("scientific_stage_boundary")
    memory = metadata.get("memory_complexity")
    planned_bands = (
        source_planning.get("bands")
        if isinstance(source_planning, dict)
        else None
    )
    transport_spectral_basis = metadata.get("spectral_basis")
    run_spectral_basis = manifest.get("spectral_basis")
    request_spectral_basis = request_record.get("spectral_basis")
    if historical_transport:
        if any(
            value is not None
            for value in (
                transport_spectral_basis,
                run_spectral_basis,
                request_spectral_basis,
                declaration.get("spectral_basis"),
            )
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "historical transport contains future spectral-basis provenance.",
            )
    elif expected_system_id == PROPOSED_SYSTEM_ID:
        if (
            not isinstance(transport_spectral_basis, dict)
            or transport_spectral_basis.get("id") != request_spectral_basis
            or not isinstance(run_spectral_basis, dict)
            or run_spectral_basis
            != transport_spectral_basis
            | {
                "applied_to_multispectral_transport": True,
                "stage_a_scalar_baseline_is_spd_independent": True,
            }
            or declaration.get("spectral_basis") != transport_spectral_basis
            or not isinstance(source_planning, dict)
            or source_planning.get("spectral_basis")
            != transport_spectral_basis
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "Proposed multispectral spectral-basis authority is inconsistent.",
            )
    elif (
        transport_spectral_basis is not None
        or declaration.get("spectral_basis") is not None
        or request_spectral_basis is not None
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "comparator multispectral transport contains Proposed spectral control.",
        )
    if (
        scope
        != {
            "schema_id": ANALYSIS_SCOPE_SCHEMA_ID,
            "schema_version": ANALYSIS_SCOPE_SCHEMA_VERSION,
            "value": AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM.value,
        }
        or stage
        != {
            "stage_id": "multispectral_fspm",
            "role": "FSPM",
            "plants_in_scientific_transport": True,
            "baseline_recomputed": False,
        }
        or not isinstance(source_planning, dict)
        or source_planning.get("system_id") != expected_system_id
        or source_planning.get("source_state_id") != expected_source_state_id
        or not isinstance(source_planning.get("source_policy"), dict)
        or not isinstance(planned_bands, list)
        or [
            record.get("band_id") if isinstance(record, dict) else None
            for record in planned_bands
        ]
        != list(executed_band_order)
        or source_planning.get("post_trace_scaling") is not False
        or source_planning.get("symmetry_reconstruction") is not False
        or stage_boundary
        != {
            "raw_transport_authoritative": True,
            "surface_light_aggregation_published_separately": True,
            "surface_coloring_performed": False,
            "symmetry_reconstruction_applied": False,
        }
        or not isinstance(memory, dict)
        or memory.get("resident_five_band_arrays") is not False
        or memory.get("expanded_receiver_identity_materialized") is not False
        or memory.get("source")
        != "O(one band source document + fixture/module layout)"
        or memory.get("export") != "O(1) expanded-record working memory"
        or memory.get("decode")
        != "O(1) values plus one line; one band processed at a time"
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "multispectral stage policy declarations are incompatible.",
        )

    receiver_count = metadata.get("receiver_count")
    plant = metadata.get("plant")
    transport_room = metadata.get("room")
    baseline_room = manifest.get("room")
    if (
        isinstance(receiver_count, bool)
        or not isinstance(receiver_count, int)
        or receiver_count <= 0
        or not isinstance(plant, dict)
        or not isinstance(transport_room, dict)
        or not isinstance(baseline_room, dict)
        or transport_room.get("requested_length_ft")
        != baseline_room.get("length_ft")
        or transport_room.get("requested_width_ft")
        != baseline_room.get("width_ft")
        or transport_room.get("orientation")
        != "aligned_simulation_long_axis_x_y_cross_z_up"
        or plant.get("canonical_counts_per_plant")
        != CANONICAL_COUNTS_PER_PLANT
        or plant.get("geometry_included_in_scientific_transport") is not True
        or plant.get("clipped_scaled_clamped_or_capped") is not False
        or plant.get("ordering")
        != "Y-major/X-minor plants; plant-major receivers; front then back"
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "juvenile scene counts or frozen-geometry policy is incompatible.",
        )
    plant_count = plant.get("plant_count")
    global_counts = plant.get("global_counts")
    if (
        isinstance(plant_count, bool)
        or not isinstance(plant_count, int)
        or plant_count <= 0
        or not isinstance(global_counts, dict)
        or global_counts
        != {
            "plants": plant_count,
            "leaves": plant_count * CANONICAL_COUNTS_PER_PLANT["leaves"],
            "faces": plant_count * CANONICAL_COUNTS_PER_PLANT["faces"],
            "patches": plant_count * CANONICAL_COUNTS_PER_PLANT["patches"],
            "receivers": (
                plant_count * CANONICAL_COUNTS_PER_PLANT["receivers"]
            ),
        }
        or receiver_count != global_counts["receivers"]
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "juvenile aggregate counts do not close over the plant count.",
        )
    try:
        declared_sampling_profile_id = plant.get("sampling_profile_id")
        if declared_sampling_profile_id is None:
            # Historical D2 transport metadata predates explicit sampling identity.
            expected_sampling_profile_id = (
                REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID
            )
        elif (
            isinstance(declared_sampling_profile_id, str)
            and declared_sampling_profile_id
            in REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS
        ):
            expected_sampling_profile_id = str(declared_sampling_profile_id)
        else:
            raise ValueError("transport sampling profile is not declared")
        active_domain_payload = transport_room.get("active_domain")
        if active_domain_payload is None:
            active_domain = None
        elif not isinstance(active_domain_payload, Mapping) or not isinstance(
            active_domain_payload.get("enabled"), bool
        ):
            raise ValueError("transport active-domain identity is invalid")
        else:
            active_domain = ActiveRoomDomain.from_feet(
                transport_room["requested_length_ft"],
                transport_room["requested_width_ft"],
                enabled=active_domain_payload["enabled"],
            )
        if (
            active_domain is not None
            and dict(active_domain_payload) != active_domain.to_payload()
        ):
            raise ValueError("transport active-domain identity is inconsistent")
        expected_natural_fit = plan_natural_fit_layout_from_feet(
            transport_room["requested_length_ft"],
            transport_room["requested_width_ft"],
            active_domain=active_domain,
        )
        expected_scene = build_juvenile_natural_fit_scene(
            expected_natural_fit,
            sampling_profile_id=expected_sampling_profile_id,
        )
        if transport_room.get("coordinate_frame") != (
            expected_natural_fit.coordinate_frame.to_payload()
        ) or transport_room.get("aligned_simulation_length_m") != (
            expected_natural_fit.aligned_room_m.length_m
        ) or transport_room.get("aligned_simulation_width_m") != (
            expected_natural_fit.aligned_room_m.width_m
        ):
            raise ValueError("transport coordinate frame is inconsistent")
        expected_export_plan = plan_juvenile_radiance_export(expected_scene)
        expected_geometry = _streamed_export_artifact(
            "plant_geometry",
            "plant-geometry.rad",
            "model/vnd.radiance",
            expected_export_plan.iter_geometry_bytes(),
        )
        expected_receiver_input = _streamed_export_artifact(
            "receiver_input",
            "receivers.pts",
            "text/plain",
            expected_export_plan.iter_receiver_input_bytes(),
        )
        expected_origins = _streamed_export_artifact(
            "plant_origins",
            "plant-origins.v1.f64le.bin",
            "application/octet-stream",
            expected_export_plan.iter_plant_origin_bytes(),
        )
        compact_dependencies = (
            expected_geometry,
            expected_receiver_input,
            expected_origins,
        )
        expected_compact_bytes = expected_export_plan.compact_receiver_index_bytes(
            compact_dependencies
        )
        expected_compact = _streamed_export_artifact(
            "compact_receiver_index",
            "receiver-index.v1.json",
            "application/json",
            (expected_compact_bytes,),
        )
        expected_export_artifacts = (
            *compact_dependencies,
            expected_compact,
        )
        expected_export_manifest = expected_export_plan.manifest_payload(
            expected_export_artifacts
        )
        expected_export_manifest_bytes = expected_export_plan.manifest_bytes(
            expected_export_artifacts
        )
    except (KeyError, RuntimeError, TypeError, ValueError) as exc:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            f"compact juvenile identity recomputation failed: {exc}",
        ) from exc
    if (
        plant.get("profile_id") != expected_scene.profile_id
        or (
            plant.get("sampling_profile_id") is not None
            and plant.get("sampling_profile_id")
            != expected_scene.sampling_profile_id
        )
        or plant.get("layout_policy_id") != expected_scene.layout_policy_id
        or plant.get("layout_plan_hash") != expected_scene.layout_plan_hash
        or plant.get("scene_id") != expected_scene.scene_id
        or plant.get("scene_hash") != expected_scene.scene_hash
        or plant_count != expected_scene.counts.plant_count
        or global_counts != expected_scene.counts.to_payload()
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "juvenile scene identity does not match the Natural-fit request.",
        )

    compact_record = metadata.get("compact_receiver_index")
    compact_path, _compact_sha256 = _validate_raw_artifact_record(
        root,
        compact_record,
        expected_role="compact_receiver_index",
        expected_path=expected_public_artifacts["compact_receiver_index"],
        expected_media_type="application/json",
        expected_row_count=None,
    )
    origins_record = metadata.get("plant_origins")
    origins_path, _origins_sha256 = _validate_raw_artifact_record(
        root,
        origins_record,
        expected_role="plant_origins",
        expected_path=expected_public_artifacts["plant_origins"],
        expected_media_type="application/octet-stream",
        expected_row_count=None,
    )
    export_record = metadata.get("scene_export_manifest")
    export_path, _export_sha256 = _validate_raw_artifact_record(
        root,
        export_record,
        expected_role="scene_export_manifest",
        expected_path=expected_public_artifacts["scene_export_manifest"],
        expected_media_type="application/json",
        expected_row_count=None,
    )
    expected_by_role = {
        item.role: item for item in expected_export_artifacts
    }
    if (
        _read_json_object(compact_path)
        != expected_export_plan.compact_receiver_index_payload(
            compact_dependencies
        )
        or _read_json_object(compact_path).get("schema_id")
        != COMPACT_RECEIVER_INDEX_SCHEMA_ID
        or _read_json_object(compact_path).get("schema_version")
        != COMPACT_RECEIVER_INDEX_SCHEMA_VERSION
        or compact_record.get("byte_length")
        != expected_compact.byte_length
        or compact_record.get("sha256") != expected_compact.sha256
        or origins_record.get("byte_length")
        != plant_count * PLANT_ORIGIN_STRIDE_BYTES
        or origins_record.get("byte_length") != expected_origins.byte_length
        or origins_record.get("sha256") != expected_origins.sha256
        or origins_path.stat().st_size != plant_count * PLANT_ORIGIN_STRIDE_BYTES
        or _read_json_object(export_path) != expected_export_manifest
        or export_record.get("byte_length")
        != len(expected_export_manifest_bytes)
        or export_record.get("sha256")
        != hashlib.sha256(expected_export_manifest_bytes).hexdigest()
        or expected_export_manifest.get("schema_id")
        != JUVENILE_RADIANCE_EXPORT_SCHEMA_ID
        or expected_export_manifest.get("schema_version")
        != JUVENILE_RADIANCE_EXPORT_SCHEMA_VERSION
        or expected_export_manifest.get("ordering", {}).get("receivers")
        != RECEIVER_ORDERING
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "compact receiver identity or export manifest disagrees with the scene.",
        )
    for role, expected_artifact in expected_by_role.items():
        artifact_path = root / "fspm-transport" / "scene" / expected_artifact.logical_name
        if (
            _contains_symlink(root, artifact_path)
            or not artifact_path.is_file()
            or artifact_path.stat().st_size != expected_artifact.byte_length
            or _sha256_stream(artifact_path) != expected_artifact.sha256
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                f"juvenile scene artifact integrity failed: {role}",
            )
    scene_root = root / "fspm-transport" / "scene"
    if (
        _contains_symlink(root, scene_root)
        or {path.name for path in scene_root.iterdir()}
        != {
            "plant-geometry.rad",
            "receivers.pts",
            "plant-origins.v1.f64le.bin",
            "receiver-index.v1.json",
            "export-manifest.v2.json",
        }
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "juvenile scene export contains missing or extra artifacts.",
        )

    bands = metadata.get("bands")
    if not isinstance(bands, list) or len(bands) != len(executed_band_order):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "multispectral band records are incomplete.",
        )
    _validate_stage_b_source_state_reuse(
        expected_system_id,
        expected_source_state,
        manifest.get("layout"),
        source_planning,
        bands,
    )
    expected_inventory: list[object] = [
        compact_record,
        origins_record,
        export_record,
    ]
    for order_index, (band_id, band_record) in enumerate(
        zip(executed_band_order, bands, strict=True)
    ):
        planning_record = planned_bands[order_index]
        if (
            not isinstance(band_record, dict)
            or not isinstance(planning_record, dict)
            or band_record.get("order_index") != order_index
            or band_record.get("band_id") != band_id
            or band_record.get("definition")
            != FIXED_TRANSPORT_BANDS[order_index].to_dict()
            or band_record.get("is_par") is not (band_id != "far_red")
            or band_record.get("units") != "umol/m^2/s"
            or band_record.get("post_trace_transforms") != []
            or not isinstance(band_record.get("source"), dict)
            or not isinstance(band_record.get("material"), dict)
            or planning_record.get("source_sha256")
            != band_record.get("source_sha256")
            or planning_record.get("material_sha256")
            != band_record.get("material_sha256")
            or planning_record.get("source_provenance")
            != band_record.get("source")
            or planning_record.get("material_provenance")
            != band_record.get("material")
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                f"multispectral band metadata is invalid: {band_id}",
            )
        band_root = (
            root / "fspm-transport" / "bands" / f"{order_index:02d}-{band_id}"
        )
        for input_name, hash_field in (
            ("source.rad", "source_sha256"),
            ("leaf-material.rad", "material_sha256"),
            ("receiver.rgb", "raw_rgb_sha256"),
        ):
            input_path = band_root / input_name
            expected_hash = band_record.get(hash_field)
            if (
                not isinstance(expected_hash, str)
                or len(expected_hash) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in expected_hash
                )
                or _contains_symlink(root, input_path)
                or not input_path.is_file()
                or not input_path.resolve().is_relative_to(root)
                or _sha256_stream(input_path) != expected_hash
            ):
                raise ProposedRunError(
                    "artifact_validation_failed",
                    "promotion",
                    f"multispectral band input provenance failed: "
                    f"{band_id}/{input_name}",
                )
        values_record = band_record.get("receiver_values")
        values_path, _values_sha256 = _validate_raw_artifact_record(
            root,
            values_record,
            expected_role=f"{band_id}_receiver_values",
            expected_path=expected_public_artifacts[
                f"band_{band_id}_receiver_values"
            ],
            expected_media_type="application/octet-stream",
            expected_row_count=receiver_count,
        )
        _validate_nonnegative_f64le_stream(values_path, receiver_count)
        expected_inventory.append(values_record)
    bands_root = root / "fspm-transport/bands"
    if (
        _contains_symlink(root, bands_root)
        or not bands_root.is_dir()
        or {path.name for path in bands_root.iterdir()}
        != {
            f"{index:02d}-{band_id}"
            for index, band_id in enumerate(executed_band_order)
        }
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "multispectral band artifact inventory is incomplete or contains extras.",
        )
    if metadata.get("ordered_artifact_inventory") != expected_inventory:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "multispectral ordered artifact inventory disagrees with its records.",
        )
    if (
        declaration.get("source_state_id") != expected_source_state_id
        or declaration.get("receiver_count") != receiver_count
        or declaration.get("band_order") != list(executed_band_order)
        or (
            not historical_transport
            and (
                declaration.get("include_far_red") is not include_far_red
                or declaration.get("far_red_executed") is not include_far_red
            )
        )
        or declaration.get("transport_schema_id")
        != JUVENILE_MULTISPECTRAL_SCHEMA_ID
        or declaration.get("transport_schema_version")
        != transport_schema_version
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "manifest and multispectral metadata disagree.",
        )
    aggregation_sha256 = _validate_fspm_scientific_aggregation(
        root,
        manifest=manifest,
        declaration=aggregation_declaration,
        expected_run_id=expected_run_id,
        expected_system_id=expected_system_id,
        expected_source_state_id=expected_source_state_id,
        expected_scene=expected_scene,
        transport_metadata_sha256=metadata_sha256,
        transport_bands=bands,
        transport_schema_version=transport_schema_version,
        executed_band_order=executed_band_order,
        include_far_red=include_far_red,
    )
    return metadata_sha256, aggregation_sha256


def _streamed_export_artifact(
    role: str,
    logical_name: str,
    media_type: str,
    chunks: Iterable[bytes],
) -> JuvenileRadianceArtifactMetadata:
    digest = hashlib.sha256()
    byte_length = 0
    for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise ValueError("export stream yielded a non-byte chunk.")
        digest.update(chunk)
        byte_length += len(chunk)
    return JuvenileRadianceArtifactMetadata(
        role=role,
        logical_name=logical_name,
        media_type=media_type,
        byte_length=byte_length,
        sha256=digest.hexdigest(),
    )


def _validate_fspm_scientific_aggregation(
    root: Path,
    *,
    manifest: Mapping[str, object],
    declaration: Mapping[str, object],
    expected_run_id: str,
    expected_system_id: str,
    expected_source_state_id: str,
    expected_scene: JuvenileScientificScene,
    transport_metadata_sha256: str,
    transport_bands: list[object],
    transport_schema_version: int,
    executed_band_order: tuple[str, ...],
    include_far_red: bool,
) -> str:
    aggregation_schema_version = declaration.get("schema_version")
    historical_aggregation = aggregation_schema_version == 1
    if (transport_schema_version, aggregation_schema_version) not in {
        (2, 1),
        (JUVENILE_MULTISPECTRAL_SCHEMA_VERSION, FSPM_AGGREGATION_SCHEMA_VERSION),
    }:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "transport and aggregation schema versions are incompatible.",
        )
    suffix = "v1" if historical_aggregation else "v2"
    expected_public = {
        "aggregation_metadata": f"fspm-aggregation/aggregation.{suffix}.json",
        "patch_surface_light": (
            f"fspm-aggregation/patch-surface-light.{suffix}.f64le.bin"
        ),
        "leaf_surface_light": (
            f"fspm-aggregation/leaf-surface-light.{suffix}.f64le.bin"
        ),
        "plant_surface_light": (
            f"fspm-aggregation/plant-surface-light.{suffix}.f64le.bin"
        ),
        "room_surface_light_summary": (
            f"fspm-aggregation/room-summary.{suffix}.json"
        ),
        "scientific_derivation_graph": (
            f"fspm-aggregation/derivation-graph.{suffix}.json"
        ),
    }
    aggregation_root = root / "fspm-aggregation"
    if (
        _contains_symlink(root, aggregation_root)
        or not aggregation_root.is_dir()
        or {path.name for path in aggregation_root.iterdir()}
        != {Path(path).name for path in expected_public.values()}
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "FSPM aggregation contains missing or extra artifacts.",
        )
    artifacts = manifest.get("artifacts")
    if (
        not isinstance(artifacts, dict)
        or declaration.get("schema_id") != FSPM_AGGREGATION_SCHEMA_ID
        or declaration.get("schema_version") != aggregation_schema_version
        or declaration.get("aggregation_executed") is not True
        or declaration.get("public_artifacts") != expected_public
        or any(artifacts.get(role) != path for role, path in expected_public.items())
        or (
            not historical_aggregation
            and (
                declaration.get("include_far_red") is not include_far_red
                or declaration.get("band_order") != list(executed_band_order)
                or declaration.get("far_red_executed") is not include_far_red
            )
        )
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "FSPM aggregation manifest declaration is incompatible.",
        )
    metadata_path, metadata_sha256 = _validate_scientific_artifact_record(
        root,
        declaration.get("metadata_artifact"),
        expected_role="fspm_scientific_aggregation_metadata",
        expected_path=expected_public["aggregation_metadata"],
        expected_media_type="application/json",
        expected_row_count=None,
        expected_stride=None,
    )
    metadata = _read_json_object(metadata_path)
    counts = expected_scene.counts.to_payload()
    transport_declaration = manifest.get("multispectral_transport")
    transport_spectral_basis = (
        transport_declaration.get("spectral_basis")
        if isinstance(transport_declaration, Mapping)
        else None
    )
    if (
        declaration.get("source_state_id") != expected_source_state_id
        or declaration.get("transport_metadata_sha256")
        != transport_metadata_sha256
        or declaration.get("counts") != counts
        or metadata.get("schema_id") != FSPM_AGGREGATION_SCHEMA_ID
        or metadata.get("schema_version") != aggregation_schema_version
        or metadata.get("run_id") != expected_run_id
        or metadata.get("system_id") != expected_system_id
        or metadata.get("source_state_id") != expected_source_state_id
        or declaration.get("spectral_basis") != transport_spectral_basis
        or metadata.get("spectral_basis") != transport_spectral_basis
        or metadata.get("transport_metadata_sha256")
        != transport_metadata_sha256
        or metadata.get("transport_metadata")
        != {
            "path": f"fspm-transport/transport.v{transport_schema_version}.json",
            "sha256": transport_metadata_sha256,
        }
        or metadata.get("run_includes_juvenile_fspm_transport") is not True
        or metadata.get("aggregation_executed") is not True
        or metadata.get("reference_resolution_executes_transport") is not False
        or metadata.get("reference_controls_aggregation") is not False
        or metadata.get("counts") != counts
        or metadata.get("band_order") != list(executed_band_order)
        or metadata.get("par_band_order") != list(PAR_BAND_ORDER)
        or metadata.get("far_red_preserved_separately") is not True
        or metadata.get("raw_receiver_authorities")
        != [
            record.get("receiver_values")
            if isinstance(record, dict)
            else None
            for record in transport_bands
        ]
        or (
            not historical_aggregation
            and (
                metadata.get("include_far_red") is not include_far_red
                or metadata.get("executed_band_order")
                != list(executed_band_order)
                or metadata.get("far_red_executed") is not include_far_red
            )
        )
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "FSPM aggregation authority links are incompatible.",
        )
    expected_coefficients: list[dict[str, object]] = []
    for record in transport_bands:
        material = record.get("material") if isinstance(record, dict) else None
        original = material.get("original_atr") if isinstance(material, dict) else None
        if not isinstance(record, dict) or not isinstance(original, dict):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "FSPM material coefficient provenance is missing.",
            )
        alpha = original.get("absorptance")
        tau = original.get("transmittance")
        rho = original.get("reflectance")
        if any(
            isinstance(value, bool) or not isinstance(value, int | float)
            for value in (alpha, tau, rho)
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                "FSPM material coefficients are invalid.",
            )
        expected_coefficients.append(
            {
                "band_id": record.get("band_id"),
                "absorptance": alpha,
                "transmittance": tau,
                "reflectance": rho,
                "coefficient_sum": math.fsum((alpha, tau, rho)),
                "closure_abs_tolerance": COEFFICIENT_CLOSURE_ABS_TOLERANCE,
                "material_sha256": record.get("material_sha256"),
                "material_provenance_sha256": _hash_json(material),
                "raw_receiver_sha256": record.get("receiver_values", {}).get(
                    "sha256"
                ),
            }
        )
    expected_equations = {
        "per_side_density": {
            "incident": "I_bqs",
            "absorbed": "alpha_b * I_bqs",
            "transmitted": "tau_b * I_bqs",
            "reflected": "rho_b * I_bqs",
        },
        "per_side_rate": (
            "quantity_density_bqs * physical_one_sided_patch_area_q"
        ),
        "combined_patch_rate": "front_rate + back_rate",
        "combined_incident_density": "front_density + back_density",
        "physical_area_application_count_per_side": 1,
        "par": "blue + green + orange + red",
        "far_red_combined_with_par": False,
        "higher_level_density": (
            "sum(side_density * physical_one_sided_patch_area) / "
            "sum(physical_one_sided_patch_area)"
        ),
    }
    expected_summation = {
        "identity_order": (
            (
                "plant-major, canonical leaf, canonical patch, front then back, "
                "blue/green/orange/red/far_red"
            )
            if historical_aggregation
            else (
                "plant-major, canonical leaf, canonical patch, front then back, "
                + "/".join(executed_band_order)
            )
        ),
        "within_patch_band_sum": "math.fsum in canonical band order",
        "hierarchical_sum": (
            "Neumaier compensated sum in canonical identity order"
        ),
        "density_aggregation": "area-weighted only",
        "naive_density_sum_or_mean": False,
    }
    expected_conservation = {
        "patch_to_leaf": (
            "each canonical patch contributes exactly once to its current "
            "authoritative leaf accumulator"
        ),
        "leaf_to_plant": (
            "independently compared with direct patch-to-plant sum"
        ),
        "plant_to_room": (
            "independently compared with leaf-to-room and patch-to-room sums"
        ),
        "relative_tolerance": LOCAL_CLOSURE_REL_TOLERANCE,
        "absolute_rate_tolerance_umol_s": LOCAL_CLOSURE_ABS_TOLERANCE,
    }
    binary_contract = binary_contract_for_band_order(executed_band_order)
    patch_struct, _patch_indices, patch_float_fields = binary_contract["patch"]
    leaf_struct, _leaf_indices, summary_float_fields = binary_contract["leaf"]
    plant_struct, _plant_indices, _plant_fields = binary_contract["plant"]
    expected_binary_schemas = {
        "patch": _validation_binary_schema(
            patch_struct.size,
            PATCH_INDEX_FIELDS,
            patch_float_fields,
            include_offsets=not historical_aggregation,
        ),
        "leaf": _validation_binary_schema(
            leaf_struct.size,
            LEAF_INDEX_FIELDS,
            summary_float_fields,
            include_offsets=not historical_aggregation,
        ),
        "plant": _validation_binary_schema(
            plant_struct.size,
            PLANT_INDEX_FIELDS,
            summary_float_fields,
            include_offsets=not historical_aggregation,
        ),
    }
    if (
        metadata.get("material_coefficient_authorities")
        != expected_coefficients
        or metadata.get("equations") != expected_equations
        or metadata.get("summation_policy") != expected_summation
        or metadata.get("conservation_policy") != expected_conservation
        or metadata.get("binary_schemas") != expected_binary_schemas
        or metadata.get("scientific_claim")
        != (
            "coefficient-based immediate surface interaction accounting; "
            "transmitted and reflected rates are not directional outgoing "
            "transport simulations"
        )
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "FSPM equations, schemas, or coefficient authorities are incompatible.",
        )
    expected_exclusions = {
        "surface_coloring_performed": False,
        "target_scaling_applied": False,
        "display_normalization_applied": False,
        "symmetry_reconstruction_applied": False,
        "per_face_value_duplication_performed": False,
        "growth_or_photosynthesis_modeled": False,
    }
    memory = metadata.get("memory_complexity")
    if (
        metadata.get("scientific_exclusions") != expected_exclusions
        or not isinstance(memory, dict)
        or memory.get("resident_five_band_arrays") is not False
        or memory.get("expanded_receiver_identity_materialized") is not False
        or memory.get("nested_patch_objects_materialized") is not False
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "FSPM aggregation bounded-memory policy is incompatible.",
        )

    inventory = metadata.get("ordered_artifact_inventory")
    if not isinstance(inventory, list) or len(inventory) != 5:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "FSPM aggregation artifact inventory is incomplete.",
        )
    expected_binary = (
        (
            "patch",
            "fspm_patch_surface_light",
            expected_public["patch_surface_light"],
            counts["patches"],
            patch_struct.size,
        ),
        (
            "leaf",
            "fspm_leaf_surface_light",
            expected_public["leaf_surface_light"],
            counts["leaves"],
            leaf_struct.size,
        ),
        (
            "plant",
            "fspm_plant_surface_light",
            expected_public["plant_surface_light"],
            counts["plants"],
            plant_struct.size,
        ),
    )
    validated_records: list[Mapping[str, object]] = []
    for index, (
        _level,
        role,
        path,
        row_count,
        stride,
    ) in enumerate(expected_binary):
        record = inventory[index]
        _validate_scientific_artifact_record(
            root,
            record,
            expected_role=role,
            expected_path=path,
            expected_media_type="application/octet-stream",
            expected_row_count=row_count,
            expected_stride=stride,
        )
        if not isinstance(record, dict):  # guarded by validator
            raise AssertionError("unreachable scientific artifact type")
        validated_records.append(record)
    room_record = inventory[3]
    room_path, _room_sha256 = _validate_scientific_artifact_record(
        root,
        room_record,
        expected_role="fspm_room_surface_light_summary",
        expected_path=expected_public["room_surface_light_summary"],
        expected_media_type="application/json",
        expected_row_count=None,
        expected_stride=None,
    )
    graph_record = inventory[4]
    graph_path, graph_sha256 = _validate_scientific_artifact_record(
        root,
        graph_record,
        expected_role="fspm_scientific_derivation_graph",
        expected_path=expected_public["scientific_derivation_graph"],
        expected_media_type="application/json",
        expected_row_count=None,
        expected_stride=None,
    )
    if metadata.get("derivation_graph_sha256") != graph_sha256:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "FSPM derivation graph identity is inconsistent.",
        )
    extended_absorbed_metrics = isinstance(
        metadata.get("absorbed_par_metrics"), Mapping
    )
    operating_authority = metadata.get("stage_a_operating_point_authority")
    if extended_absorbed_metrics and not isinstance(operating_authority, Mapping):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "FSPM Stage A operating-point authority is missing.",
        )
    if not extended_absorbed_metrics:
        operating_authority = {
            "emitted_par_ppf_umol_s": 1.0,
            "modeled_electrical_power_w": 1.0,
        }
    try:
        result, recomputed = recompute_aggregation_digests(
            root=root,
            scene=expected_scene,
            band_records=(
                record for record in transport_bands if isinstance(record, dict)
            ),
        )
        expected_room = build_room_summary_payload(
            run_id=expected_run_id,
            system_id=expected_system_id,
            source_state_id=expected_source_state_id,
            scene=expected_scene,
            result=result,
            emitted_par_ppf_umol_s=operating_authority.get(
                "emitted_par_ppf_umol_s"
            ),
            modeled_electrical_power_w=operating_authority.get(
                "modeled_electrical_power_w"
            ),
        )
        if historical_aggregation:
            expected_room["schema_version"] = 1
            for key in (
                "include_far_red",
                "band_order",
                "executed_band_order",
                "par_band_order",
                "far_red_executed",
            ):
                expected_room.pop(key, None)
        if not extended_absorbed_metrics:
            expected_room.pop("absorbed_par_metrics", None)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            f"FSPM aggregation recomputation failed: {exc}",
        ) from exc
    for (level, _role, _path, _rows, _stride), record in zip(
        expected_binary,
        validated_records,
        strict=True,
    ):
        digest = recomputed[level]
        if any(
            record.get(key) != digest.get(key)
            for key in ("sha256", "byte_length", "row_count", "stride_bytes")
        ):
            raise ProposedRunError(
                "artifact_validation_failed",
                "promotion",
                f"FSPM {level} aggregation failed independent recomputation.",
            )
    room = _read_json_object(room_path)
    if (
        room != expected_room
        or room.get("schema_id") != FSPM_ROOM_SUMMARY_SCHEMA_ID
        or room.get("schema_version")
        != (1 if historical_aggregation else FSPM_ROOM_SUMMARY_SCHEMA_VERSION)
        or room.get("target_or_reference_cap_applied") is not False
        or room.get("symmetry_reconstruction_applied") is not False
        or metadata.get("closure") != room.get("closure")
        or metadata.get("room_metrics") != room.get("surface_light")
        or (
            extended_absorbed_metrics
            and metadata.get("absorbed_par_metrics")
            != room.get("absorbed_par_metrics")
        )
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "FSPM room summary failed independent recomputation.",
        )
    graph = _read_json_object(graph_path)
    authorities = graph.get("authorities")
    compact_index_path = root / "fspm-transport/scene/receiver-index.v1.json"
    compact_index = _read_json_object(compact_index_path)
    expected_raw_graph = [
        {
            "band_id": record.get("band_id"),
            "artifact": record.get("receiver_values"),
            "material_sha256": record.get("material_sha256"),
            "material_provenance_sha256": _hash_json(record.get("material")),
        }
        for record in transport_bands
        if isinstance(record, dict)
    ]
    if (
        graph.get("schema_id") != FSPM_DERIVATION_GRAPH_SCHEMA_ID
        or graph.get("schema_version")
        != (1 if historical_aggregation else FSPM_DERIVATION_GRAPH_SCHEMA_VERSION)
        or graph.get("run_id") != expected_run_id
        or graph.get("system_id") != expected_system_id
        or graph.get("source_state_id") != expected_source_state_id
        or not isinstance(authorities, dict)
        or authorities.get("transport_metadata")
        != {
            "path": f"fspm-transport/transport.v{transport_schema_version}.json",
            "sha256": transport_metadata_sha256,
        }
        or authorities.get("compact_receiver_index")
        != {
            "artifact_sha256": _sha256_stream(compact_index_path),
            "canonical_payload_sha256": _hash_json(compact_index),
        }
        or authorities.get("raw_bands") != expected_raw_graph
        or (
            not historical_aggregation
            and (
                graph.get("band_order") != list(executed_band_order)
                or graph.get("far_red_executed") is not include_far_red
            )
        )
        or graph.get("ordered_derivation")
        != [
            "raw_receiver_side_density",
            "coefficient_partition_per_band_and_side",
            "physical_patch_rate_with_area_applied_once_per_side",
            "four_band_par_sum_with_far_red_separate",
            "canonical_patch_to_leaf_sum",
            "canonical_leaf_to_plant_sum",
            "canonical_plant_to_modeled_surface_room_sum",
        ]
        or graph.get("outputs") != inventory[:4]
        or graph.get("prohibited_transforms")
        != {
            "spectral_fraction_reapplication": False,
            "source_dimming_reapplication": False,
            "post_trace_179_conversion": False,
            "target_scaling": False,
            "display_normalization": False,
            "symmetry_reconstruction": False,
        }
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "FSPM derivation graph is incomplete or inconsistent.",
        )
    return metadata_sha256


def _validation_binary_schema(
    stride_bytes: int,
    index_fields: tuple[str, ...],
    value_fields: tuple[str, ...],
    *,
    include_offsets: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "byte_order": "little-endian",
        "stride_bytes": stride_bytes,
        "index_component_type": "uint64",
        "index_fields": list(index_fields),
        "value_component_type": "float64",
        "value_fields": list(value_fields),
        "record_order": "canonical global identity order",
    }
    if include_offsets:
        payload["index_field_offsets_bytes"] = {
            field: index * 8 for index, field in enumerate(index_fields)
        }
        payload["value_field_offsets_bytes"] = {
            field: (len(index_fields) + index) * 8
            for index, field in enumerate(value_fields)
        }
    return payload


def _validate_scientific_artifact_record(
    root: Path,
    record: object,
    *,
    expected_role: str,
    expected_path: str,
    expected_media_type: str,
    expected_row_count: int | None,
    expected_stride: int | None,
) -> tuple[Path, str]:
    if not isinstance(record, dict):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            f"scientific artifact record is missing: {expected_role}",
        )
    expected_keys = {"role", "path", "media_type", "byte_length", "sha256"}
    if expected_row_count is not None:
        expected_keys.update(("row_count", "stride_bytes"))
    sha256 = record.get("sha256")
    byte_length = record.get("byte_length")
    if (
        set(record) != expected_keys
        or record.get("role") != expected_role
        or record.get("path") != expected_path
        or record.get("media_type") != expected_media_type
        or isinstance(byte_length, bool)
        or not isinstance(byte_length, int)
        or byte_length < 0
        or not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
        or (
            expected_row_count is not None
            and (
                record.get("row_count") != expected_row_count
                or record.get("stride_bytes") != expected_stride
                or byte_length != expected_row_count * int(expected_stride or 0)
            )
        )
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            f"scientific artifact record is invalid: {expected_role}",
        )
    path = root / expected_path
    if (
        _contains_symlink(root, path)
        or not path.is_file()
        or not path.resolve().is_relative_to(root)
        or path.stat().st_size != byte_length
        or _sha256_stream(path) != sha256
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            f"scientific artifact integrity failed: {expected_role}",
        )
    return path, sha256


def _validate_stage_b_source_state_reuse(
    system_id: str,
    source_state: Mapping[str, object],
    layout_identity: object,
    source_planning: Mapping[str, object],
    bands: list[object],
) -> None:
    operation = source_state.get("source_operation")
    source_policy = source_planning.get("source_policy")
    if not isinstance(operation, dict) or not isinstance(source_policy, dict):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "Stage B source policy is not linked to Stage A operation.",
        )
    source_records = [
        record.get("source") if isinstance(record, dict) else None
        for record in bands
    ]
    if any(not isinstance(record, dict) for record in source_records):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "Stage B band source provenance is missing.",
        )
    if system_id == "proposed":
        effective_zones = operation.get("effective_watts_by_control_zone")
        effective_modules = operation.get("effective_watts_by_module")
        expected_dimming = operation.get("global_linear_dimming_factor")
        layout_modules = (
            layout_identity.get("modules")
            if isinstance(layout_identity, dict)
            else None
        )
        proposed_agrees = (
            isinstance(effective_zones, list)
            and isinstance(effective_modules, list)
            and isinstance(layout_modules, list)
            and len(effective_modules) == len(layout_modules)
            and source_policy.get("global_dimming_factor") == expected_dimming
            and source_policy.get("target_control_recomputed") is False
        )
        if proposed_agrees:
            proposed_agrees = all(
                isinstance(module, dict)
                and isinstance(module.get("control_zone_index"), int)
                and not isinstance(module.get("control_zone_index"), bool)
                and 0 <= module["control_zone_index"] < len(effective_zones)
                and effective_modules[module_index]
                == effective_zones[module["control_zone_index"]]
                for module_index, module in enumerate(layout_modules)
            )
        expected_zone_counts = (
            [
                sum(
                    1
                    for module in layout_modules
                    if isinstance(module, dict)
                    and module.get("control_zone_index") == zone_index
                )
                for zone_index in range(len(effective_zones))
            ]
            if isinstance(effective_zones, list)
            and isinstance(layout_modules, list)
            else None
        )
        for source in source_records:
            if not isinstance(source, dict):  # pragma: no cover - guarded above
                proposed_agrees = False
                continue
            amplitudes = source.get("zone_amplitudes")
            proposed_agrees = proposed_agrees and isinstance(amplitudes, list)
            if isinstance(amplitudes, list):
                proposed_agrees = proposed_agrees and [
                    amplitude.get("module_wattage")
                    if isinstance(amplitude, dict)
                    else None
                    for amplitude in amplitudes
                ] == effective_zones
                proposed_agrees = proposed_agrees and [
                    amplitude.get("module_count")
                    if isinstance(amplitude, dict)
                    else None
                    for amplitude in amplitudes
                ] == expected_zone_counts
                proposed_agrees = proposed_agrees and (
                    source.get("stage_a_global_dimming_factor")
                    == expected_dimming
                )
        agrees = proposed_agrees
    elif system_id == "conventional":
        expected_dimming = operation.get("global_dimming_factor")
        expected_ppf = operation.get("effective_fixture_ppf_umol_s")
        expected_carrier = operation.get(
            "effective_carrier_multiplier_per_fixture"
        )
        par_sources = [
            source
            for source in source_records
            if isinstance(source, dict) and source.get("band_id") != "far_red"
        ]
        ppf_total = sum(
            float(value) if isinstance(value, int | float) and not isinstance(value, bool)
            else math.nan
            for source in par_sources
            for value in (source.get("effective_per_fixture_ppf_umol_s"),)
        )
        carrier_total = sum(
            float(value) if isinstance(value, int | float) and not isinstance(value, bool)
            else math.nan
            for source in par_sources
            for value in (source.get("carrier_multiplier"),)
        )
        agrees = (
            source_policy.get("global_dimming_factor") == expected_dimming
            and source_policy.get("target_control_recomputed") is False
            and all(
                isinstance(source, dict)
                and source.get("stage_a_global_dimming_factor")
                == expected_dimming
                for source in source_records
            )
            and isinstance(expected_ppf, int | float)
            and not isinstance(expected_ppf, bool)
            and isinstance(expected_carrier, int | float)
            and not isinstance(expected_carrier, bool)
            and math.isclose(
                ppf_total,
                float(expected_ppf),
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            and math.isclose(
                carrier_total,
                float(expected_carrier),
                rel_tol=1e-12,
                abs_tol=1e-9,
            )
        )
    elif system_id == "hps":
        hps_par_sources = [
            source
            for source in source_records
            if isinstance(source, dict) and source.get("band_id") != "far_red"
        ]
        hps_ppf_total = sum(
            float(value) if isinstance(value, int | float) and not isinstance(value, bool)
            else math.nan
            for source in hps_par_sources
            for value in (source.get("per_fixture_ppf_umol_s"),)
        )
        expected_hps_ppf = operation.get("fixture_ppf_umol_s")
        agrees = (
            operation.get("operation") == "fixed_full_output"
            and operation.get("dimming_supported") is False
            and source_policy.get("operation") == "fixed_full_output"
            and source_policy.get("dimming_supported") is False
            and source_policy.get("target_control_recomputed") is False
            and source_policy.get("post_trace_scaling") is False
            and isinstance(expected_hps_ppf, int | float)
            and not isinstance(expected_hps_ppf, bool)
            and math.isclose(
                hps_ppf_total,
                float(expected_hps_ppf),
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            and all(
                isinstance(source, dict)
                and source.get("operation") == "fixed_full_output"
                for source in source_records
            )
        )
    else:  # pragma: no cover - system discriminator guarded by caller
        agrees = False
    if not agrees:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            "Stage B source provenance does not reuse the exact Stage A operation.",
        )


def _validate_raw_artifact_record(
    root: Path,
    record: object,
    *,
    expected_role: str,
    expected_path: str,
    expected_media_type: str,
    expected_row_count: int | None,
) -> tuple[Path, str]:
    if not isinstance(record, dict):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            f"raw artifact record is missing: {expected_role}",
        )
    expected_keys = {"role", "path", "media_type", "byte_length", "sha256"}
    if expected_row_count is not None:
        expected_keys.add("row_count")
    sha256 = record.get("sha256")
    byte_length = record.get("byte_length")
    if (
        set(record) != expected_keys
        or record.get("role") != expected_role
        or record.get("path") != expected_path
        or record.get("media_type") != expected_media_type
        or isinstance(byte_length, bool)
        or not isinstance(byte_length, int)
        or byte_length < 0
        or not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
        or (
            expected_row_count is not None
            and record.get("row_count") != expected_row_count
        )
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            f"raw artifact record is invalid: {expected_role}",
        )
    path = root / expected_path
    if (
        _contains_symlink(root, path)
        or not path.is_file()
        or not path.resolve().is_relative_to(root)
        or path.stat().st_size != byte_length
        or _sha256_stream(path) != sha256
    ):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            f"raw artifact integrity failed: {expected_role}",
        )
    return path, sha256


def _validate_nonnegative_f64le_stream(path: Path, expected_count: int) -> None:
    if path.stat().st_size != expected_count * 8:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            f"raw receiver binary length is invalid: {path.name}",
        )
    observed = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 4096), b""):
            if len(chunk) % 8:
                raise ProposedRunError(
                    "artifact_validation_failed",
                    "promotion",
                    f"raw receiver binary is misaligned: {path.name}",
                )
            for (value,) in struct.iter_unpack("<d", chunk):
                if not math.isfinite(value) or value < 0.0:
                    raise ProposedRunError(
                        "artifact_validation_failed",
                        "promotion",
                        f"raw receiver binary contains an invalid value: {path.name}",
                    )
                observed += 1
    if observed != expected_count:
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            f"raw receiver binary count is invalid: {path.name}",
        )


def _sha256_stream(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schedule_payload(
    layout: SmdLayout,
    full_output: FullOutputSchedule,
    *,
    control_mode: ProposedControlMode,
) -> dict[str, object]:
    uniform = control_mode is ProposedControlMode.UNIFORM_MODULE_DIMMING
    return {
        "schema_id": "fspm-optics.proposed-full-output-schedule",
        "schema_version": 2,
        "proposed_control_mode": control_mode.value,
        "basis_matrix_solver_enabled": not uniform,
        "policy": (
            "uniform_equal_module_reference_then_global_dimming"
            if uniform
            else "target_independent_cv_optimal_relative_nnls_max_normalized"
        ),
        "coefficient_units": "watts_per_module_by_control_zone",
        "uniform_reference_watts_per_module": (
            full_output.declared_max_watts_per_module if uniform else None
        ),
        "all_module_reference_watts_equal": (
            len(set(full_output.schedule.watts_by_module)) == 1
        ),
        "declared_max_watts_per_module": full_output.declared_max_watts_per_module,
        "relative_coefficients": list(full_output.relative_coefficients),
        "watts_by_control_zone": list(
            full_output.schedule.watts_by_control_zone
        ),
        "watts_by_module": list(full_output.schedule.watts_by_module),
        "control_zone_count": full_output.schedule.control_zone_count,
        "module_count": full_output.schedule.module_count,
        "full_output_power_w": full_output.full_output_power_w,
        "internal_source_ppe_umol_per_j": (
            full_output.internal_source_ppe_umol_per_j
        ),
        "full_output_internal_par_ppf_umol_s": (
            full_output.full_output_internal_par_ppf_umol_s
        ),
        "accepted_fixture_transmission": full_output.accepted_fixture_transmission,
        "completed_aperture_fixture_ppe_umol_per_j": (
            full_output.completed_aperture_fixture_ppe_umol_per_j
        ),
        "full_output_modeled_completed_aperture_par_ppf_umol_s": (
            full_output.full_output_modeled_completed_aperture_par_ppf_umol_s
        ),
        "optical_stack_id": full_output.optical_stack_id,
        "full_output_field_metrics": {
            "mean_ppfd_umol_m2_s": full_output.full_output_mean_ppfd,
            "maximum_ppfd_umol_m2_s": max(full_output.full_output_field),
            "standard_deviation_ppfd_umol_m2_s": (
                full_output.full_output_standard_deviation_ppfd
            ),
            "cv_percent": full_output.full_output_cv_percent,
        },
        "modules": [
            {
                "module_index": module.module_index,
                "control_zone_index": module.control_zone_index,
                "watts": watts,
            }
            for module, watts in zip(
                layout.modules,
                full_output.schedule.watts_by_module,
                strict=True,
            )
        ],
    }


def _target_payload(
    controlled: TargetControlResult,
    fspm_policy: Mapping[str, object],
    *,
    limiting_sample: PpfdMapSample,
) -> dict[str, object]:
    return {
        "schema_id": "fspm-optics.proposed-global-target-control",
        "schema_version": 3,
        "lighting_target_mode": controlled.lighting_target_mode,
        "requested_value_semantics": (
            "requested_sampled_stage_a_maximum_ppfd"
            if controlled.lighting_target_mode == "target_capped"
            else "requested_mean_stage_a_ppfd"
        ),
        "requested_target_ppfd_umol_m2_s": controlled.requested_target_ppfd,
        "full_output_mean_ppfd_umol_m2_s": controlled.full_output_mean_ppfd,
        "full_output_maximum_ppfd_umol_m2_s": (
            controlled.full_output_maximum_ppfd
        ),
        "achieved_mean_ppfd_umol_m2_s": controlled.achieved_mean_ppfd,
        "achieved_maximum_ppfd_umol_m2_s": (
            controlled.achieved_maximum_ppfd
        ),
        "raw_factor": controlled.raw_factor,
        "dimming_factor": controlled.dimming_factor,
        "factor_policy": (
            "min(requested_cap/full_output_sampled_maximum, 1.0)"
            if controlled.lighting_target_mode == "target_capped"
            else "min(requested_target/full_output_mean, 1.0)"
        ),
        "feasible": controlled.feasible,
        "infeasibility": controlled.infeasibility,
        "cap_binding": controlled.cap_binding,
        "cap_compliant": controlled.cap_compliant,
        "compliance_tolerance_umol_m2_s": (
            controlled.compliance_tolerance_umol_m2_s
        ),
        "limiting_sample": {
            "index": controlled.limiting_sample_index,
            "x_m": limiting_sample.x_m,
            "y_m": limiting_sample.y_m,
            "z_m": limiting_sample.z_m,
            "achieved_ppfd_umol_m2_s": (
                controlled.achieved_maximum_ppfd
            ),
        },
        "full_output_limiting_sample": {
            "index": controlled.limiting_sample_index,
            "x_m": limiting_sample.x_m,
            "y_m": limiting_sample.y_m,
            "z_m": limiting_sample.z_m,
            "ppfd_umol_m2_s": controlled.full_output_maximum_ppfd,
        },
        "global_factor_shared_by_future_spectral_bands": True,
        "full_output_power_w": controlled.full_output_power_w,
        "effective_power_w": controlled.effective_power_w,
        "full_output_internal_par_ppf_umol_s": (
            controlled.full_output_internal_par_ppf_umol_s
        ),
        "effective_internal_par_ppf_umol_s": (
            controlled.effective_internal_par_ppf_umol_s
        ),
        "full_output_modeled_completed_aperture_par_ppf_umol_s": (
            controlled.full_output_modeled_completed_aperture_par_ppf_umol_s
        ),
        "effective_modeled_completed_aperture_par_ppf_umol_s": (
            controlled.effective_modeled_completed_aperture_par_ppf_umol_s
        ),
        "post_trace_source_normalization": False,
        "fspm_target_policy": dict(fspm_policy),
    }


def _layout_identity(
    layout: SmdLayout,
    *,
    active_domain: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        **(
            {}
            if active_domain is None
            else {"active_domain": dict(active_domain)}
        ),
        "room_length_m": layout.room_length_m,
        "room_width_m": layout.room_width_m,
        "pitch_x_m": layout.pitch_x_m,
        "pitch_y_m": layout.pitch_y_m,
        "axes_swapped": layout.axes_swapped,
        "proposed_layout_mode": layout.proposed_layout_mode.value,
        "proposed_ring_mode": layout.proposed_ring_mode.value,
        "module_pattern_id": layout.module_pattern_id,
        "fixture_policy_id": layout.fixture_policy_id,
        "mechanical_envelope": {
            "id": layout.mechanical_envelope_id,
            "width_x_m": layout.module_footprint_x_m,
            "height_y_m": layout.module_footprint_y_m,
        },
        "fixture_asset_set_id": layout.fixture_asset_set_id,
        **(
            {}
            if layout.alignment_lattice is None
            else {"alignment_lattice": layout.alignment_lattice.to_payload()}
        ),
        "topology": {
            "base_n": layout.topology.base_n,
            "nominal_base_n": layout.topology.nominal_base_n,
            "proposed_ring_mode": layout.topology.proposed_ring_mode.value,
            "module_pattern_id": layout.topology.module_pattern_id,
            "square_tile_count": layout.topology.square_tile_count,
            "connector_count": layout.topology.connector_count,
            "has_rectangular_extension": layout.topology.has_rectangular_extension,
            "rectangular_long_ft": layout.topology.rectangular_long_ft,
            "rectangular_offset": layout.topology.rectangular_offset,
            "control_zone_count": layout.topology.control_zone_count,
        },
        "modules": [
            {
                "module_index": module.module_index,
                "x_m": module.x_m,
                "y_m": module.y_m,
                "z_m": module.z_m,
                "control_zone_index": module.control_zone_index,
            }
            for module in layout.modules
        ],
        "fixtures": [
            {
                "fixture_id": fixture.fixture_id,
                "fixture_type": fixture.fixture_type,
                "display_fixture_type": fixture.display_fixture_type,
                "source_orientation": fixture.source_orientation,
                "orientation_degrees": fixture.orientation_degrees,
                "member_module_indices": list(fixture.member_module_indices),
                "connectors": [
                    {
                        "start_module_index": connector.start_module_index,
                        "end_module_index": connector.end_module_index,
                    }
                    for connector in fixture.connectors
                ],
            }
            for fixture in layout.fixtures
        ],
    }


def _active_domain_from_request_record(
    request_record: Mapping[str, object],
) -> ActiveRoomDomain | None:
    payload = request_record.get("active_domain")
    aisle_mode = request_record.get("aisle_mode")
    if payload is None and aisle_mode is None:
        return None
    if not isinstance(aisle_mode, bool) or not isinstance(payload, Mapping):
        raise ValueError("request active-domain provenance is missing.")
    domain = ActiveRoomDomain.from_feet(
        request_record["room_length_ft"],  # type: ignore[arg-type]
        request_record["room_width_ft"],  # type: ignore[arg-type]
        enabled=aisle_mode,
    )
    if dict(payload) != domain.to_payload():
        raise ValueError("request active-domain provenance is inconsistent.")
    return domain


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProposedRunError(
            "artifact_validation_failed", "promotion", str(exc)
        ) from exc
    if not isinstance(payload, dict):
        raise ProposedRunError(
            "artifact_validation_failed",
            "promotion",
            f"artifact root must be a JSON object: {path.name}",
        )
    return payload


def _read_ppfd_csv(path: Path) -> tuple[PpfdMapSample, ...]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = tuple(csv.DictReader(handle))
        samples = tuple(
            PpfdMapSample(
                float(row["x_m"]),
                float(row["y_m"]),
                float(row["z_m"]),
                float(row["ppfd_umol_m2_s"]),
            )
            for row in rows
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ProposedRunError(
            "artifact_validation_failed", "promotion", "PPFD CSV is invalid."
        ) from exc
    if not samples:
        raise ProposedRunError(
            "artifact_validation_failed", "promotion", "PPFD CSV is empty."
        )
    return samples


def _contains_symlink(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    if root.is_symlink():
        return True
    current = root
    for part in relative.parts:
        if part in ("", ".", ".."):
            return True
        current /= part
        if current.is_symlink():
            return True
    return False


def _close_finite_metric(left: object, right: object) -> bool:
    if (
        isinstance(left, bool)
        or not isinstance(left, int | float)
        or isinstance(right, bool)
        or not isinstance(right, int | float)
        or not math.isfinite(float(left))
        or not math.isfinite(float(right))
        or float(left) <= 0.0
        or float(right) <= 0.0
    ):
        return False
    return math.isclose(
        float(left), float(right), rel_tol=1e-12, abs_tol=1e-9
    )


def _hash_json(payload: object) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _emit(
    sink: EventSink,
    event_type: str,
    message: str,
    data: Mapping[str, object] | None = None,
) -> None:
    sink(event_type, message, data)
