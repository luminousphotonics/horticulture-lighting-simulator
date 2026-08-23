"""One discriminated dispatcher for Proposed, Conventional, and HPS."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping

from fspm_optics import __version__
from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_FULL_OUTPUT_CARRIER_MULTIPLIER,
    CONVENTIONAL_FIXTURE_POWER_W,
    CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J,
    CONVENTIONAL_FIXTURE_PPF_UMOL_S,
    CONVENTIONAL_IES_RESOURCE_NAME,
    CONVENTIONAL_IES_SHA256,
    CONVENTIONAL_SPD_RESOURCE_NAME,
    CONVENTIONAL_SPD_SHA256,
    canonical_conventional_global_dimming_factor,
    conservative_conventional_global_dimming_factor,
    derive_conventional_carrier_scale,
)
from fspm_optics.fixtures.hps import (
    HPS_IES_RESOURCE_NAME,
    HPS_IES_SHA256,
    HPS_NOMINAL_LAMP_CLASS_POWER_W,
    HPS_RADIANCE_CARRIER_MULTIPLIER,
    HPS_SPD_RESOURCE_NAME,
    HPS_SPD_SHA256,
)
from fspm_optics.radiance.options import radiance_options
from fspm_optics.radiance.runner import LocalRunner
from fspm_optics.layout.overlay import bind_overlay_to_active_domain
from fspm_optics.transport.conventional_scalar import (
    ConventionalScalarMetrics,
    ConventionalScalarTransportRequest,
    ConventionalScalarTransportResult,
    PpfdMapSample,
    compute_conventional_scalar_metrics,
    execute_conventional_scalar_transport,
    format_ppfd_values_npz,
)
from fspm_optics.transport.basis.atomic import atomic_write_bytes, atomic_write_text
from fspm_optics.transport.hps_scalar import (
    HPS_FIXTURE_POWER_W,
    HPS_FIXTURE_PPE_UMOL_PER_J,
    HPS_FIXTURE_PPF_UMOL_S,
    HpsScalarTransportRequest,
    HpsScalarTransportResult,
    execute_hps_scalar_transport,
)

from .domain import (
    AnalysisScope,
    CONVENTIONAL_SYSTEM_ID,
    ConventionalRunRequest,
    EMITTED_PPF_BOUNDARIES,
    HPS_SYSTEM_ID,
    HpsRunRequest,
    ProposedRunRequest,
    RunRequest,
)
from .multispectral import (
    build_conventional_juvenile_source_adapter,
    build_hps_juvenile_source_adapter,
    execute_juvenile_multispectral_transport,
)
from .proposed import (
    EventSink,
    ProposedRunError,
    ProposedRunOutcome,
    ReportingRunner,
    run_proposed_baseline,
)
from .publication import (
    PublicationError,
    RunSciencePublication,
    publish_native_baseline_run,
)
from .source_state import PhysicalSourceState
from .spatial_uniformity import compute_spatial_uniformity
from .target_control import (
    TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S,
    resolve_fspm_target_policy,
    resolve_global_source_dimming,
    scale_global_output_field_float64,
)
from .visualization import VisualizationReference, reference_plane_field_identity


CONVENTIONAL_STAGE_A_SCALING_POLICY_ID = (
    "conventional_full_output_float64_global_scaling_v1"
)


def run_system_baseline(
    *,
    run_id: str,
    request: RunRequest,
    workspace: Path,
    event_sink: EventSink,
    runner: LocalRunner | None = None,
) -> ProposedRunOutcome:
    """Dispatch exactly once from the validated request variant."""

    if isinstance(request, ProposedRunRequest):
        return run_proposed_baseline(
            run_id=run_id,
            request=request,
            workspace=workspace,
            event_sink=event_sink,
            runner=runner,
        )
    if isinstance(request, ConventionalRunRequest):
        return run_conventional_baseline(
            run_id=run_id,
            request=request,
            workspace=workspace,
            event_sink=event_sink,
            runner=runner,
        )
    if isinstance(request, HpsRunRequest):
        return run_hps_baseline(
            run_id=run_id,
            request=request,
            workspace=workspace,
            event_sink=event_sink,
            runner=runner,
        )
    raise ProposedRunError(
        "unsupported_system_request",
        "dispatch",
        "validated run request variant is unsupported.",
    )


def run_conventional_baseline(
    *,
    run_id: str,
    request: ConventionalRunRequest,
    workspace: Path,
    event_sink: EventSink,
    runner: LocalRunner | None = None,
) -> ProposedRunOutcome:
    """Trace full output once, then apply one capped global Float64 factor."""

    root = _workspace_root(workspace)
    reporting_runner = ReportingRunner(runner or LocalRunner(), event_sink)
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
    _emit(
        event_sink,
        "conventional.full_output.started",
        (
            "Tracing the target-independent practical Conventional layout at "
            "full output."
            if request.layout_mode == "practical"
            else "Tracing the target-independent Rolling Bench Conventional "
            "layout at full output."
        ),
        {"layout_policy": request.layout_mode},
    )
    try:
        full_request = ConventionalScalarTransportRequest.from_feet(
            workspace=root / "engine" / "conventional-full-output",
            room_length_ft=request.room_length_ft,
            room_width_ft=request.room_width_ft,
            layout_policy=request.layout_mode,
            quality_profile=request.quality,
            global_dimming_factor=1.0,
            reference_plane_z_m=request.mounting_geometry.reference_plane_z_m,
            mount_height_m=request.mounting_geometry.mounting_height_m,
            active_domain=request.active_domain,
        )
        full = execute_conventional_scalar_transport(
            full_request, reporting_runner
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProposedRunError(
            "conventional_full_output_failed", "baseline_transport", str(exc)
        ) from exc
    full_spatial = compute_spatial_uniformity(
        tuple(sample.ppfd_umol_m2_s for sample in full.samples)
    )
    full_mean = full.metrics.mean_ppfd_umol_m2_s
    full_maximum = full_spatial.maximum_ppfd
    if full_mean <= 0.0:
        raise ProposedRunError(
            "conventional_full_output_invalid",
            "target_control",
            "Conventional full-output mean must be positive.",
        )
    control = resolve_global_source_dimming(
        requested_target_ppfd=request.target_ppfd_umol_m2_s,
        full_output_mean_ppfd=full_mean,
        full_output_maximum_ppfd=full_maximum,
        lighting_target_mode=request.lighting_target_mode.value,
        system_id=request.system,
    )
    raw_factor = control.raw_factor
    requested_capped_factor = control.dimming_factor
    dimming_factor = (
        conservative_conventional_global_dimming_factor(
            requested_capped_factor
        )
        if request.lighting_target_mode.value == "target_capped"
        else canonical_conventional_global_dimming_factor(
            requested_capped_factor
        )
    )
    feasible = control.feasible
    infeasibility = control.infeasibility
    _emit(
        event_sink,
        "conventional.global_output_scaling.started",
        "Applying the authoritative global output fraction to the full-output "
        "Float64 Conventional field.",
        {
            "requested_capped_factor": requested_capped_factor,
            "global_output_fraction": dimming_factor,
            "scaling_policy_id": CONVENTIONAL_STAGE_A_SCALING_POLICY_ID,
        },
    )
    try:
        achieved_values = scale_global_output_field_float64(
            tuple(sample.ppfd_umol_m2_s for sample in full.samples),
            dimming_factor,
        )
        achieved_samples = tuple(
            PpfdMapSample(sample.x_m, sample.y_m, sample.z_m, value)
            for sample, value in zip(full.samples, achieved_values, strict=True)
        )
        achieved_metrics = compute_conventional_scalar_metrics(
            achieved_samples
        )
        scaled_summary_path = _materialize_conventional_scaled_result(
            root=root,
            full=full,
            samples=achieved_samples,
            metrics=achieved_metrics,
            global_output_fraction=dimming_factor,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProposedRunError(
            "conventional_global_output_scaling_failed",
            "target_control",
            str(exc),
        ) from exc
    effective_carrier = derive_conventional_carrier_scale(
        CONVENTIONAL_FIXTURE_PPF_UMOL_S * dimming_factor
    )
    achieved_spatial = compute_spatial_uniformity(
        tuple(sample.ppfd_umol_m2_s for sample in achieved_samples)
    )
    cap_compliant = (
        achieved_spatial.maximum_ppfd
        <= request.target_ppfd_umol_m2_s
        + TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S
        if request.lighting_target_mode.value == "target_capped"
        else None
    )
    if cap_compliant is False:
        raise ProposedRunError(
            "conventional_target_cap_noncompliant",
            "target_control",
            "Authenticated final Conventional Stage A field exceeds the "
            "requested sampled PPFD cap.",
        )
    full_limiting_index = max(
        range(len(full.samples)),
        key=lambda index: full.samples[index].ppfd_umol_m2_s,
    )
    achieved_limiting_index = max(
        range(len(achieved_samples)),
        key=lambda index: achieved_samples[index].ppfd_umol_m2_s,
    )
    full_limiting = full.samples[full_limiting_index]
    achieved_limiting = achieved_samples[achieved_limiting_index]
    fixture_count = len(full.plan.layout.fixtures)
    effective_ppf = fixture_count * effective_carrier.fixture_ppf_umol_s
    target_control = {
        "schema_id": "fspm-optics.conventional-global-target-control",
        "schema_version": 4,
        "lighting_target_supported": True,
        "lighting_target_mode": request.lighting_target_mode.value,
        "requested_value_semantics": (
            "requested_sampled_stage_a_maximum_ppfd"
            if request.lighting_target_mode.value == "target_capped"
            else "requested_mean_stage_a_ppfd"
        ),
        "requested_target_ppfd_umol_m2_s": request.target_ppfd_umol_m2_s,
        "full_output_mean_ppfd_umol_m2_s": full_mean,
        "full_output_maximum_ppfd_umol_m2_s": full_maximum,
        "achieved_mean_ppfd_umol_m2_s": achieved_spatial.mean_ppfd,
        "achieved_maximum_ppfd_umol_m2_s": achieved_spatial.maximum_ppfd,
        "raw_factor": raw_factor,
        "capped_factor_before_native_quantization": requested_capped_factor,
        "dimming_factor": dimming_factor,
        "factor_policy": (
            "conservative_historical_ies2rad_%g_compatible("
            "min(requested_cap/full_output_sampled_maximum, 1.0))"
            if request.lighting_target_mode.value == "target_capped"
            else
            "historical_ies2rad_%g_compatible("
            "min(requested_target/full_output_mean, 1.0))"
        ),
        "native_carrier_numeric_format": "%g (six significant digits)",
        "dimming_stage": "post_trace_float64_global_linear_scaling",
        "post_trace_scaling": True,
        "scaling_policy_id": CONVENTIONAL_STAGE_A_SCALING_POLICY_ID,
        "scaling_formula": (
            "final_ppfd[i] = float64(full_output_ppfd[i]) * "
            "float64(dimming_factor)"
        ),
        "feasible": feasible,
        "infeasibility": infeasibility,
        "cap_binding": control.cap_binding,
        "cap_compliant": cap_compliant,
        "compliance_tolerance_umol_m2_s": (
            TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S
        ),
        "full_output_limiting_sample": {
            "index": full_limiting_index,
            "x_m": full_limiting.x_m,
            "y_m": full_limiting.y_m,
            "z_m": full_limiting.z_m,
            "ppfd_umol_m2_s": full_limiting.ppfd_umol_m2_s,
        },
        "limiting_sample": {
            "index": achieved_limiting_index,
            "x_m": achieved_limiting.x_m,
            "y_m": achieved_limiting.y_m,
            "z_m": achieved_limiting.z_m,
            "achieved_ppfd_umol_m2_s": achieved_limiting.ppfd_umol_m2_s,
        },
        "rated_fixture_power_w": CONVENTIONAL_FIXTURE_POWER_W,
        "tested_ies_input_w_provenance_only": (
            full.plan.source.original_ies.tested_input_watts
        ),
        "full_output_par_ppe_umol_per_j": (
            CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J
        ),
        "full_output_power_w": fixture_count * CONVENTIONAL_FIXTURE_POWER_W,
        "effective_power_w": (
            fixture_count * CONVENTIONAL_FIXTURE_POWER_W * dimming_factor
        ),
        "full_output_ppf_umol_s": (
            fixture_count * CONVENTIONAL_FIXTURE_PPF_UMOL_S
        ),
        "effective_ppf_umol_s": effective_ppf,
        "full_output_carrier_multiplier_per_fixture": (
            full.plan.source.carrier_scale.ies2rad_multiplier
        ),
        "effective_carrier_multiplier_per_fixture": (
            effective_carrier.ies2rad_multiplier
        ),
    }
    schedule = {
        "schema_id": "fspm-optics.conventional-full-output-schedule",
        "schema_version": 2,
        "policy": f"{request.layout_mode}_layout_equal_fixture_full_output",
        "target_independent_layout": True,
        "layout_policy": request.layout_mode,
        "fixture_count": fixture_count,
        "rated_fixture_power_w": CONVENTIONAL_FIXTURE_POWER_W,
        "tested_ies_input_w_provenance_only": (
            full.plan.source.original_ies.tested_input_watts
        ),
        "full_output_fixture_ppe_umol_per_j": (
            CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J
        ),
        "full_output_fixture_ppf_umol_s": CONVENTIONAL_FIXTURE_PPF_UMOL_S,
        "full_output_power_w": fixture_count * CONVENTIONAL_FIXTURE_POWER_W,
        "full_output_ppf_umol_s": (
            fixture_count * CONVENTIONAL_FIXTURE_PPF_UMOL_S
        ),
        "full_output_field_metrics": full.metrics.to_payload(),
    }
    operating_point = {
        "mode": "globally_dimmed_from_declared_full_output",
        "lighting_target_mode": request.lighting_target_mode.value,
        "fixture": {
            "rated_electrical_power_w": CONVENTIONAL_FIXTURE_POWER_W,
            "tested_ies_input_w_provenance_only": (
                full.plan.source.original_ies.tested_input_watts
            ),
            "full_output_ppe_umol_per_j": (
                CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J
            ),
            "full_output_ppf_umol_s": CONVENTIONAL_FIXTURE_PPF_UMOL_S,
        },
        "fixture_count": fixture_count,
        "global_dimming_factor": dimming_factor,
        "capped_factor_before_native_quantization": requested_capped_factor,
        "dimming_stage": "post_trace_float64_global_linear_scaling",
        "power": {
            "full_output_w": fixture_count * CONVENTIONAL_FIXTURE_POWER_W,
            "effective_w": (
                fixture_count * CONVENTIONAL_FIXTURE_POWER_W * dimming_factor
            ),
        },
        "ppf": {
            "emitted_umol_s": effective_ppf,
            "emission_boundary_id": (
                EMITTED_PPF_BOUNDARIES[CONVENTIONAL_SYSTEM_ID]["id"]
            ),
            "emission_boundary_description": (
                EMITTED_PPF_BOUNDARIES[CONVENTIONAL_SYSTEM_ID]["description"]
            ),
            "ppe_umol_per_j": CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J,
            "full_output_umol_s": (
                fixture_count * CONVENTIONAL_FIXTURE_PPF_UMOL_S
            ),
            "effective_umol_s": effective_ppf,
        },
        "carrier": {
            "full_output_multiplier_per_fixture": (
                full.plan.source.carrier_scale.ies2rad_multiplier
            ),
            "effective_multiplier_per_fixture": (
                effective_carrier.ies2rad_multiplier
            ),
            "native_numeric_format": "%g (six significant digits)",
        },
        "post_trace_scale": dimming_factor,
        "post_trace_scaling_policy_id": (
            CONVENTIONAL_STAGE_A_SCALING_POLICY_ID
        ),
    }
    layout_identity = full.plan.layout.to_payload() | {
        "active_domain": request.active_domain.to_payload()
    }
    source_state = PhysicalSourceState.create(
        system_id=request.system,
        layout_identity=layout_identity,
        full_output_schedule=schedule,
        operating_point=operating_point,
        source_operation={
            "policy_id": "conventional_global_stage_a_source_operation_v3",
            "mounting_height": request.mounting_geometry.to_payload(),
            "layout_policy": request.layout_mode,
            "layout_id": full.plan.layout.layout_id,
            "fixture_count": fixture_count,
            "rated_fixture_power_w": CONVENTIONAL_FIXTURE_POWER_W,
            "tested_ies_input_w_provenance_only": (
                full.plan.source.original_ies.tested_input_watts
            ),
            "full_output_fixture_ppe_umol_per_j": (
                CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J
            ),
            "full_output_fixture_ppf_umol_s": CONVENTIONAL_FIXTURE_PPF_UMOL_S,
            "effective_fixture_power_w": (
                CONVENTIONAL_FIXTURE_POWER_W * dimming_factor
            ),
            "global_dimming_factor": dimming_factor,
            "lighting_target_mode": request.lighting_target_mode.value,
            "effective_fixture_ppf_umol_s": (
                effective_carrier.fixture_ppf_umol_s
            ),
            "effective_carrier_multiplier_per_fixture": (
                effective_carrier.ies2rad_multiplier
            ),
            "full_output_carrier_applied_before_trace": True,
            "global_dimming_applied_before_trace": False,
            "global_dimming_applied_after_trace": True,
            "post_trace_scaling": True,
            "post_trace_scaling_policy_id": (
                CONVENTIONAL_STAGE_A_SCALING_POLICY_ID
            ),
            "post_trace_179_conversion": False,
            "target_control_resolved_in_stage_a": True,
            "fixture_occlusion_identity": (
                full.plan.fixture_occlusion.identity_sha256
            ),
        },
    )
    _emit(
        event_sink,
        "analysis.stage.baseline.completed",
        "Stage A completed with a validated final Conventional source state.",
        {
            "logical_stage": "baseline_ppfd",
            "source_state_id": source_state.source_state_id,
            "target_feasible": feasible,
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
            adapter = build_conventional_juvenile_source_adapter(
                root=root,
                result=full,
                source_state=source_state,
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
            },
        )
    science = RunSciencePublication(
        system_id=request.system,
        samples=achieved_samples,
        layout_identity=layout_identity,
        overlay_plan=bind_overlay_to_active_domain(
            full.plan.layout.authoritative_overlay_plan(),
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
        quality_options=tuple(radiance_options(request.quality)),
        target_control=target_control,
        full_output_schedule=schedule,
        operating_point=operating_point,
        counts={
            "fixture_groups": fixture_count,
            "fixtures": fixture_count,
            "modules": 0,
            "control_zones": 1,
        },
        transport_policy={
            "backend": "native_conventional_scalar_rtrace",
            "layout_policy": request.layout_mode,
            "unit_downward_distribution": True,
            "carrier_derivation": "fixture_ppf_umol_s * 179",
            "carrier_numeric_format": "%g (six significant digits)",
            "full_output_carrier_quantized_before_trace": True,
            "global_output_fraction_applied_before_trace": False,
            "direct_equal_grey_decode": True,
            "fixture_occlusion": (
                full.plan.fixture_occlusion.scientific_payload()
            ),
            "post_trace_179_conversion": False,
            "fixture_level_optimization": False,
            "target_dependent_fixture_count": False,
            "post_trace_scaling": True,
            "post_trace_scaling_policy_id": (
                CONVENTIONAL_STAGE_A_SCALING_POLICY_ID
            ),
            "post_trace_scaling_arithmetic": "IEEE 754 binary64",
            "post_trace_symmetrization": False,
            "final_composite_trace": False,
        },
        runtime_provenance=_runtime_provenance(full),
        engine_provenance={
            "mounting_height": request.mounting_geometry.to_payload(),
            "full_output": _conventional_engine_identity(full),
            "final": _conventional_scaled_engine_identity(
                full,
                achieved_samples,
                dimming_factor=dimming_factor,
                effective_carrier_multiplier=(
                    effective_carrier.ies2rad_multiplier
                ),
            ),
            "source_rerun_performed": False,
            "native_stage_a_command_counts": {
                "ies2rad_conversion": 1,
                "fixture_shape_compilation": 1,
                "scalar_scene_compilation": 1,
                "baseline_rtrace": 1,
            },
            "original_ies_resource": CONVENTIONAL_IES_RESOURCE_NAME,
            "original_ies_sha256": CONVENTIONAL_IES_SHA256,
            "ies_authority": "downward_angular_distribution_footprint_orientation_and_test_provenance",
            "tested_ies_input_w_provenance_only": (
                full.plan.source.original_ies.tested_input_watts
            ),
            "excluded_upward_ies_fraction": (
                full.plan.source.derived_ies.original_flux.upward_fraction_of_full_sphere
            ),
            "relative_spd_resource": CONVENTIONAL_SPD_RESOURCE_NAME,
            "relative_spd_sha256": CONVENTIONAL_SPD_SHA256,
            "relative_spd_authority": "relative_spectral_shape_only",
        },
        engine_artifacts={
            "conventional_full_output_summary": _relative(
                root, full.plan.paths.scalar_transport_summary
            ),
            "conventional_final_summary": _relative(
                root, scaled_summary_path
            ),
        },
        target_feasible=feasible,
        target_infeasibility=infeasibility,
        physical_source_state=source_state,
        mounting_height=request.mounting_geometry.to_payload(),
        multispectral_transport=multispectral,
    )
    return _publish_comparator(
        root, run_id, request, science, event_sink, "Conventional"
    )


def run_hps_baseline(
    *,
    run_id: str,
    request: HpsRunRequest,
    workspace: Path,
    event_sink: EventSink,
    runner: LocalRunner | None = None,
) -> ProposedRunOutcome:
    """Trace the sole HPS profile once at its declared full output."""

    root = _workspace_root(workspace)
    reporting_runner = ReportingRunner(runner or LocalRunner(), event_sink)
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
    _emit(
        event_sink,
        "hps.full_output.started",
        "Tracing the fixed-output HPS baseline.",
        {"lighting_target_supported": False},
    )
    try:
        transport_request = HpsScalarTransportRequest.from_feet(
            workspace=root / "engine" / "hps-full-output",
            room_length_ft=request.room_length_ft,
            room_width_ft=request.room_width_ft,
            quality_profile=request.quality,
            reference_plane_z_m=request.mounting_geometry.reference_plane_z_m,
            mount_height_m=request.mounting_geometry.mounting_height_m,
            active_domain=request.active_domain,
        )
        result = execute_hps_scalar_transport(
            transport_request,
            reporting_runner,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProposedRunError(
            "hps_full_output_failed", "baseline_transport", str(exc)
        ) from exc
    fixture_count = len(result.plan.layout.fixtures)
    spatial = compute_spatial_uniformity(
        tuple(sample.ppfd_umol_m2_s for sample in result.samples)
    )
    achieved_mean = spatial.mean_ppfd
    target_control = {
        "schema_id": "fspm-optics.hps-fixed-output-control",
        "schema_version": 2,
        "lighting_target_supported": False,
        "operation": "full_output_only",
        "full_output_mean_ppfd_umol_m2_s": achieved_mean,
        "achieved_mean_ppfd_umol_m2_s": achieved_mean,
        "dimming_supported": False,
        "post_trace_scaling": False,
        "post_trace_symmetrization": False,
    }
    schedule = {
        "schema_id": "fspm-optics.hps-full-output-schedule",
        "schema_version": 2,
        "policy": "fixed_full_output",
        "layout_policy": result.plan.layout.policy_id,
        "fixture_count": fixture_count,
        "fixture_power_w": HPS_FIXTURE_POWER_W,
        "fixture_ppe_umol_per_j": HPS_FIXTURE_PPE_UMOL_PER_J,
        "fixture_ppf_umol_s": HPS_FIXTURE_PPF_UMOL_S,
        "source_authority": {
            "initial_lamp_par_ppf_umol_s": HPS_FIXTURE_PPF_UMOL_S,
            "tested_system_input_power_w": HPS_FIXTURE_POWER_W,
            "computed_system_ppe_umol_per_j": HPS_FIXTURE_PPE_UMOL_PER_J,
            "nominal_lamp_class_power_w": HPS_NOMINAL_LAMP_CLASS_POWER_W,
            "nominal_lamp_class_is_provenance_only": True,
            "relative_spd_resource": HPS_SPD_RESOURCE_NAME,
            "relative_spd_sha256": HPS_SPD_SHA256,
            "ies_angular_resource": HPS_IES_RESOURCE_NAME,
            "ies_angular_sha256": HPS_IES_SHA256,
            "excluded_upward_ies_fraction": result.plan.source.derived_ies.to_payload()[
                "excluded_upward_fraction"
            ],
            "source_side_ppf_times_179": True,
            "radiance_carrier_per_fixture": HPS_RADIANCE_CARRIER_MULTIPLIER,
            "fixed_output": True,
        },
        "full_output_power_w": fixture_count * HPS_FIXTURE_POWER_W,
        "full_output_ppf_umol_s": fixture_count * HPS_FIXTURE_PPF_UMOL_S,
        "full_output_field_metrics": {
            "sensor_count": spatial.sample_count,
            "mean_ppfd_umol_m2_s": spatial.mean_ppfd,
            "minimum_ppfd_umol_m2_s": spatial.minimum_ppfd,
            "maximum_ppfd_umol_m2_s": spatial.maximum_ppfd,
            "standard_deviation_ppfd_umol_m2_s": (
                spatial.population_standard_deviation_ppfd
            ),
            "coefficient_of_variation": spatial.coefficient_of_variation,
            "coefficient_of_variation_percent": (
                spatial.coefficient_of_variation_percent
            ),
            "minimum_to_mean_uniformity": spatial.minimum_to_mean_uniformity,
        },
    }
    operating_point = {
        "mode": "fixed_full_output",
        "fixture": {
            "electrical_power_w": HPS_FIXTURE_POWER_W,
            "ppe_umol_per_j": HPS_FIXTURE_PPE_UMOL_PER_J,
            "ppf_umol_s": HPS_FIXTURE_PPF_UMOL_S,
            "electrical_power_role": "tested_system_input",
            "ppf_role": "documented_initial_lamp_PAR_PPF",
            "ppe_role": "computed_system_PPF_over_input_power",
            "nominal_lamp_class_power_w": HPS_NOMINAL_LAMP_CLASS_POWER_W,
            "nominal_lamp_class_is_provenance_only": True,
        },
        "fixture_count": fixture_count,
        "dimming_supported": False,
        "power": {
            "full_output_w": fixture_count * HPS_FIXTURE_POWER_W,
            "effective_w": fixture_count * HPS_FIXTURE_POWER_W,
        },
        "ppf": {
            "emitted_umol_s": fixture_count * HPS_FIXTURE_PPF_UMOL_S,
            "emission_boundary_id": (
                EMITTED_PPF_BOUNDARIES[HPS_SYSTEM_ID]["id"]
            ),
            "emission_boundary_description": (
                EMITTED_PPF_BOUNDARIES[HPS_SYSTEM_ID]["description"]
            ),
            "ppe_umol_per_j": HPS_FIXTURE_PPE_UMOL_PER_J,
            "full_output_umol_s": fixture_count * HPS_FIXTURE_PPF_UMOL_S,
            "effective_umol_s": fixture_count * HPS_FIXTURE_PPF_UMOL_S,
        },
        "post_trace_scale": None,
    }
    layout = result.plan.layout
    layout_identity = layout.to_payload() | {
        "active_domain": request.active_domain.to_payload()
    }
    source_state = PhysicalSourceState.create(
        system_id=request.system,
        layout_identity=layout_identity,
        full_output_schedule=schedule,
        operating_point=operating_point,
        source_operation={
            "policy_id": "hps_fixed_stage_a_source_operation_v2",
            "mounting_height": request.mounting_geometry.to_payload(),
            "layout_policy": layout.policy_id,
            "layout_id": layout.layout_id,
            "source_plan_id": result.plan.source.source_plan_id,
            "fixture_count": fixture_count,
            "fixture_power_w": HPS_FIXTURE_POWER_W,
            "fixture_ppe_umol_per_j": HPS_FIXTURE_PPE_UMOL_PER_J,
            "fixture_ppf_umol_s": HPS_FIXTURE_PPF_UMOL_S,
            "operation": "fixed_full_output",
            "dimming_supported": False,
            "target_control_resolved_in_stage_a": False,
            "post_trace_scaling": False,
            "radiance_carrier_per_fixture": HPS_RADIANCE_CARRIER_MULTIPLIER,
            "ppe_matching": False,
            "peak_cap_control": False,
            "output_equalization": False,
            "fixture_occlusion_identity": (
                result.plan.fixture_occlusion.identity_sha256
            ),
        },
    )
    _emit(
        event_sink,
        "analysis.stage.baseline.completed",
        "Stage A completed with the fixed full-output HPS source state.",
        {
            "logical_stage": "baseline_ppfd",
            "source_state_id": source_state.source_state_id,
            "operation": "fixed_full_output",
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
                achieved_baseline_mean_ppfd=achieved_mean,
                tolerance_umol_m2_s=request.fspm_target_tolerance_umol_m2_s,
                override_umol_m2_s=request.fspm_target_override_umol_m2_s,
            )
            adapter = build_hps_juvenile_source_adapter(
                root=root,
                result=result,
                source_state=source_state,
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
            },
        )
    science = RunSciencePublication(
        system_id=HPS_SYSTEM_ID,
        samples=result.samples,
        layout_identity=layout_identity,
        overlay_plan=bind_overlay_to_active_domain(
            layout.authoritative_overlay_plan(),
            request.active_domain,
        ),
        visualization_reference=VisualizationReference.achieved_baseline_mean(
            achieved_mean
        ),
        quality_options=tuple(radiance_options(request.quality)),
        target_control=target_control,
        full_output_schedule=schedule,
        operating_point=operating_point,
        counts={
            "fixture_groups": fixture_count,
            "fixtures": fixture_count,
            "modules": 0,
            "control_zones": 0,
        },
        transport_policy={
            "backend": "native_hps_scalar_rtrace",
            "full_output_only": True,
            "layout_policy": layout.policy_id,
            "ies_variant_selection": False,
            "dimming": False,
            "post_trace_scaling": False,
            "post_trace_symmetrization": False,
            "ppe_matching": False,
            "peak_cap_control": False,
            "output_equalization": False,
        },
        runtime_provenance=_runtime_provenance(result),
        engine_provenance={
            "mounting_height": request.mounting_geometry.to_payload(),
            "transport_identity": result.plan.transport_identity,
            "result_identity": result.plan.result_identity,
            "fixture_occlusion": (
                result.plan.fixture_occlusion.scientific_payload()
            ),
            "source_plan_id": result.plan.source.source_plan_id,
            "layout_id": layout.layout_id,
            "original_ies_resource": HPS_IES_RESOURCE_NAME,
            "original_ies_sha256": HPS_IES_SHA256,
            "relative_spd_resource": HPS_SPD_RESOURCE_NAME,
            "relative_spd_sha256": HPS_SPD_SHA256,
            "excluded_upward_ies_fraction": result.plan.source.derived_ies.to_payload()[
                "excluded_upward_fraction"
            ],
            "source_side_ppf_times_179": True,
            "radiance_carrier_per_fixture": HPS_RADIANCE_CARRIER_MULTIPLIER,
            "aperture_m": {
                "length_x": layout.fixture_length_x_m,
                "width_y": layout.fixture_width_y_m,
            },
            "mount_height_m": layout.mount.mount_height_m,
        },
        engine_artifacts={
            "hps_scalar_summary": _relative(
                root, result.plan.paths.scalar_transport_summary
            ),
        },
        target_feasible=None,
        target_infeasibility=None,
        physical_source_state=source_state,
        mounting_height=request.mounting_geometry.to_payload(),
        multispectral_transport=multispectral,
    )
    return _publish_comparator(root, run_id, request, science, event_sink, "HPS")


def _publish_comparator(
    root: Path,
    run_id: str,
    request: ConventionalRunRequest | HpsRunRequest,
    science: RunSciencePublication,
    event_sink: EventSink,
    label: str,
) -> ProposedRunOutcome:
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
        f"Completed the requested {label} analysis scope and shared publication.",
        {
            "system_id": request.system,
            "analysis_scope": request.analysis_scope.value,
        },
    )
    return ProposedRunOutcome(
        run_id=run_id,
        metrics=published.metrics,
        manifest=published.manifest,
        target_feasible=science.target_feasible,
        target_infeasibility=(
            None
            if science.target_infeasibility is None
            else dict(science.target_infeasibility)
        ),
        system_id=request.system,
    )


def _workspace_root(workspace: Path) -> Path:
    root = workspace.resolve()
    if not root.is_absolute() or not root.is_dir():
        raise ProposedRunError(
            "invalid_workspace",
            "workspace",
            "run workspace must be an existing absolute directory.",
        )
    return root


def _runtime_provenance(
    result: ConventionalScalarTransportResult | HpsScalarTransportResult,
) -> dict[str, object]:
    scalar = result.scalar_transport_summary
    radiance = scalar.get("radiance") if isinstance(scalar, Mapping) else None
    return {
        "fspm_optics_version": __version__,
        "python_version": sys.version.split()[0],
        "radiance": dict(radiance) if isinstance(radiance, Mapping) else {},
    }


def _materialize_conventional_scaled_result(
    *,
    root: Path,
    full: ConventionalScalarTransportResult,
    samples: tuple[PpfdMapSample, ...],
    metrics: ConventionalScalarMetrics,
    global_output_fraction: float,
) -> Path:
    """Persist and authenticate the final field derived from one native trace."""

    if full.plan.request.global_dimming_factor != 1.0:
        raise ValueError(
            "Conventional Stage A scaling authority must be a full-output trace."
        )
    if len(full.plan.fixture_occlusion.shapes) != 1:
        raise ValueError(
            "Conventional Stage A requires exactly one fixture-body shape "
            "compilation."
        )
    if len(samples) != len(full.samples) or any(
        (scaled.x_m, scaled.y_m, scaled.z_m)
        != (native.x_m, native.y_m, native.z_m)
        for native, scaled in zip(full.samples, samples, strict=True)
    ):
        raise ValueError(
            "scaled Conventional field changed the native coordinate ordering."
        )
    output_root = root / "engine" / "conventional-achieved"
    if output_root.exists() and (
        not output_root.is_dir() or any(output_root.iterdir())
    ):
        raise ValueError(
            "scaled Conventional artifact root must be absent or empty."
        )
    output_root.mkdir(parents=True, exist_ok=True)
    full_summary = full.plan.paths.scalar_transport_summary
    full_npz = full.plan.paths.ppfd_npz
    command_summary = full.plan.paths.command_provenance_summary
    for label, path in (
        ("full-output scalar summary", full_summary),
        ("full-output Float64 NPZ", full_npz),
        ("full-output command provenance", command_summary),
    ):
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"{label} is missing or empty.")
    observed_full_npz_hash = _sha256_file(full_npz)
    if observed_full_npz_hash != full.ppfd_npz_sha256:
        raise ValueError("full-output Float64 NPZ hash changed before scaling.")
    command_payload = json.loads(command_summary.read_text(encoding="utf-8"))
    commands = command_payload.get("commands")
    if not isinstance(commands, list) or [
        command.get("label") if isinstance(command, Mapping) else None
        for command in commands
    ] != [
        "convert_conventional_unit_downward_flux_ies",
        "compile_conventional_scalar_scene",
        "baseline_scalar_ppfd_rtrace",
    ]:
        raise ValueError(
            "full-output Conventional command provenance is incomplete."
        )

    scaled_npz = output_root / "ppfd_values.npz"
    scaled_npz_bytes = format_ppfd_values_npz(samples)
    atomic_write_bytes(scaled_npz, scaled_npz_bytes)
    scaled_npz_hash = hashlib.sha256(scaled_npz_bytes).hexdigest()
    payload: dict[str, object] = {
        "schema_id": "fspm-optics.conventional-stage-a-scaled-result",
        "schema_version": 1,
        "success": True,
        "policy_id": CONVENTIONAL_STAGE_A_SCALING_POLICY_ID,
        "authority": {
            "global_output_fraction": global_output_fraction,
            "factor_source": (
                "existing Conventional global target controller with "
                "historical ies2rad %g compatibility quantization"
            ),
            "formula": (
                "final_ppfd[i] = float64(full_output_ppfd[i]) * "
                "float64(global_output_fraction)"
            ),
            "arithmetic": "IEEE 754 binary64 elementwise multiplication",
            "coordinate_and_sample_order": "unchanged from full-output trace",
        },
        "full_output_transport": {
            "transport_identity": full.plan.transport_identity,
            "result_identity": full.plan.result_identity,
            "source_plan_id": full.plan.source.source_plan_id,
            "scalar_transport_summary": _relative(root, full_summary),
            "scalar_transport_summary_sha256": _sha256_file(full_summary),
            "command_provenance_summary": _relative(root, command_summary),
            "command_provenance_summary_sha256": (
                _sha256_file(command_summary)
            ),
            "ppfd_values_npz": _relative(root, full_npz),
            "ppfd_values_npz_sha256": observed_full_npz_hash,
        },
        "scaled_result": {
            "ppfd_values_npz": _relative(root, scaled_npz),
            "ppfd_values_npz_sha256": scaled_npz_hash,
            "reference_plane_field_sha256": (
                reference_plane_field_identity(samples)
            ),
            "metrics": metrics.to_payload(),
        },
        "native_stage_a_command_counts": {
            "ies2rad_conversion": 1,
            "fixture_shape_compilation": 1,
            "scalar_scene_compilation": 1,
            "baseline_rtrace": 1,
        },
        "native_commands_after_full_output_trace": [],
    }
    payload["derivation_identity_sha256"] = _sha256_json(payload)
    summary = output_root / "scaled_result_summary.json"
    atomic_write_text(
        summary,
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n",
    )
    return summary


def _conventional_engine_identity(
    result: ConventionalScalarTransportResult,
) -> dict[str, object]:
    return {
        "transport_identity": result.plan.transport_identity,
        "source_plan_id": result.plan.source.source_plan_id,
        "layout_id": result.plan.layout.layout_id,
        "result_identity": result.plan.result_identity,
        "global_source_dimming_factor": result.plan.request.global_dimming_factor,
        "carrier_multiplier": result.plan.source.carrier_scale.ies2rad_multiplier,
        "post_trace_scale": None,
    }


def _conventional_scaled_engine_identity(
    full: ConventionalScalarTransportResult,
    samples: tuple[PpfdMapSample, ...],
    *,
    dimming_factor: float,
    effective_carrier_multiplier: float,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "policy_id": CONVENTIONAL_STAGE_A_SCALING_POLICY_ID,
        "full_output_transport_identity": full.plan.transport_identity,
        "full_output_result_identity": full.plan.result_identity,
        "source_plan_id": full.plan.source.source_plan_id,
        "layout_id": full.plan.layout.layout_id,
        "global_source_dimming_factor": dimming_factor,
        "effective_carrier_multiplier": effective_carrier_multiplier,
        "post_trace_scale": dimming_factor,
        "reference_plane_field_sha256": reference_plane_field_identity(samples),
    }
    return payload | {
        "result_identity": (
            "conventional-scaled-stage-a-result-v1-" + _sha256_json(payload)
        )
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise ProposedRunError(
            "engine_artifact_escape",
            "artifact_publication",
            "engine artifact escaped the allocated run workspace.",
        ) from exc


def _emit(
    sink: EventSink,
    event_type: str,
    message: str,
    data: Mapping[str, object] | None = None,
) -> None:
    sink(event_type, message, data)


__all__ = [
    "run_conventional_baseline",
    "run_hps_baseline",
    "run_system_baseline",
]
