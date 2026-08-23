"""Shared run-scoped artifact publication for every supported lighting system."""

from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import io
import json
import math
from pathlib import Path
from typing import Callable, Mapping

from fspm_optics.fixtures.conventional_led.profile import (
    CONVENTIONAL_FIXTURE_POWER_W,
)
from fspm_optics.fixtures.smd.config import (
    DEFAULT_MODULE_FOOTPRINT_X_M,
    DEFAULT_MODULE_FOOTPRINT_Y_M,
)
from fspm_optics.geometry.active_domain import AISLE_WIDTH_FT, AISLE_WIDTH_M
from fspm_optics.geometry.mounting import MountingGeometry
from fspm_optics.layout.overlay import AuthoritativeOverlayPlan
from fspm_optics.plants.natural_fit import (
    NaturalFitLayoutPlan,
    plan_natural_fit_layout_from_feet,
)
from fspm_optics.plants.rex_juvenile import (
    REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID,
    REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID,
    REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS,
)
from fspm_optics.radiance.options import radiance_options
from fspm_optics.transport.basis.atomic import atomic_write_text
from fspm_optics.transport.scalar_ppfd import PpfdMapSample
from fspm_optics.viewer.artifacts import VIEWER_RESOURCE_VERSION
from fspm_optics.viewer.ppfd_heatmap import bind_ppfd_heatmap_viewer_artifacts
from fspm_optics.viewer.publish import publish_run_viewer

from .domain import (
    ANALYSIS_SCOPE_DESCRIPTIONS,
    ANALYSIS_SCOPE_LABELS,
    ANALYSIS_SCOPE_SCHEMA_ID,
    ANALYSIS_SCOPE_SCHEMA_VERSION,
    AnalysisScope,
    CONVENTIONAL_SYSTEM_ID,
    EMITTED_PPF_BOUNDARIES,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    PROPOSED_SPECTRAL_BASIS_LABELS,
    RunRequest,
    SYSTEM_DISPLAY_NAMES,
)
from .baseline_leaf_uniformity import (
    BASELINE_LEAF_UNIFORMITY_FILENAME,
    build_baseline_physical_leaf_scene,
    build_baseline_leaf_uniformity_publication,
)
from .multispectral import JuvenileMultispectralPublication
from .source_state import PhysicalSourceState, hash_json
from .spatial_uniformity import compute_spatial_uniformity
from .surface_flux_display import (
    SurfaceFluxDisplayError,
    SurfaceFluxDisplayPublication,
    build_surface_flux_display_publication,
    select_authenticated_achieved_surface_flux_reference,
    select_surface_flux_reference,
)
from .surface_flux_display_calibration import (
    SurfaceFluxDisplayCalibrationError,
    load_packaged_surface_flux_display_calibration,
)
from .target_control import (
    TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S,
    resolve_fspm_target_policy,
)
from .visualization import (
    VISUALIZATION_SCHEMA_ID,
    VisualizationArtifactSet,
    VisualizationReference,
    publish_ppfd_visualizations,
)

EventSink = Callable[[str, str, Mapping[str, object] | None], None]
NATIVE_BASELINE_PUBLICATION_SCHEMA_VERSION = 4


class PublicationError(RuntimeError):
    """Shared success artifacts could not be completed in the staging run."""


def _validate_conventional_fixture_power_contract(
    *,
    layout_identity: Mapping[str, object],
    overlay_plan: AuthoritativeOverlayPlan,
    counts: Mapping[str, int],
    target_control: Mapping[str, object],
    full_output_schedule: Mapping[str, object],
    operating_point: Mapping[str, object],
) -> None:
    """Bind Conventional power accounting to one authoritative fixture count."""

    fixtures = layout_identity.get("fixtures")
    fixture_count = counts.get("fixtures")
    schedule_count = full_output_schedule.get("fixture_count")
    overlay_count = len(overlay_plan.fixture_metadata)
    if (
        not isinstance(fixtures, list)
        or isinstance(fixture_count, bool)
        or not isinstance(fixture_count, int)
        or fixture_count <= 0
        or len(fixtures) != fixture_count
        or schedule_count != fixture_count
        or overlay_count != fixture_count
    ):
        raise PublicationError(
            "Conventional layout, overlay, schedule, and reported fixture "
            "counts disagree."
        )

    dimming = target_control.get("dimming_factor")
    if (
        isinstance(dimming, bool)
        or not isinstance(dimming, int | float)
        or not math.isfinite(float(dimming))
        or not 0.0 < float(dimming) <= 1.0
    ):
        raise PublicationError(
            "Conventional power accounting requires a capped positive dimming factor."
        )
    full_output_power = fixture_count * CONVENTIONAL_FIXTURE_POWER_W
    effective_power = full_output_power * float(dimming)
    power = operating_point.get("power")
    if not isinstance(power, Mapping):
        raise PublicationError("Conventional operating-point power is missing.")
    for label, observed, expected in (
        (
            "operating-point full-output power",
            power.get("full_output_w"),
            full_output_power,
        ),
        (
            "operating-point effective power",
            power.get("effective_w"),
            effective_power,
        ),
        (
            "target-control full-output power",
            target_control.get("full_output_power_w"),
            full_output_power,
        ),
        (
            "target-control effective power",
            target_control.get("effective_power_w"),
            effective_power,
        ),
        (
            "schedule full-output power",
            full_output_schedule.get("full_output_power_w"),
            full_output_power,
        ),
    ):
        if (
            isinstance(observed, bool)
            or not isinstance(observed, int | float)
            or not math.isfinite(float(observed))
            or not math.isclose(
                float(observed),
                expected,
                rel_tol=0.0,
                abs_tol=1.0e-9,
            )
        ):
            raise PublicationError(
                f"Conventional {label} disagrees with "
                f"{fixture_count} × {CONVENTIONAL_FIXTURE_POWER_W:g} W."
            )


def _positive_finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PublicationError(f"{field} must be a finite positive number.")
    resolved = float(value)
    if not math.isfinite(resolved) or resolved <= 0.0:
        raise PublicationError(f"{field} must be a finite positive number.")
    return resolved


def validate_emitted_ppf_contract(
    system_id: str,
    *,
    power: object,
    ppf: object,
) -> None:
    """Validate the common UI PPF value and its system-specific boundary."""

    if system_id not in EMITTED_PPF_BOUNDARIES:
        raise PublicationError("emitted-PPF system identifier is unsupported.")
    if not isinstance(power, Mapping) or not isinstance(ppf, Mapping):
        raise PublicationError("operating point must declare power and PPF totals.")

    effective_power = _positive_finite_number(
        power.get("effective_w"), field="power.effective_w"
    )
    emitted_ppf = _positive_finite_number(
        ppf.get("emitted_umol_s"), field="ppf.emitted_umol_s"
    )
    expected_boundary = EMITTED_PPF_BOUNDARIES[system_id]
    if (
        ppf.get("emission_boundary_id") != expected_boundary["id"]
        or ppf.get("emission_boundary_description")
        != expected_boundary["description"]
    ):
        raise PublicationError(
            "ppf emitted value has a missing or incompatible emission boundary."
        )

    if system_id == PROPOSED_SYSTEM_ID:
        expected_emitted = _positive_finite_number(
            ppf.get(
                "effective_modeled_completed_aperture_par_ppf_umol_s"
            ),
            field=(
                "ppf.effective_modeled_completed_aperture_par_ppf_umol_s"
            ),
        )
        ppe = _positive_finite_number(
            ppf.get("completed_aperture_fixture_ppe_umol_per_j"),
            field="ppf.completed_aperture_fixture_ppe_umol_per_j",
        )
    elif system_id in {CONVENTIONAL_SYSTEM_ID, HPS_SYSTEM_ID}:
        expected_emitted = _positive_finite_number(
            ppf.get("effective_umol_s"), field="ppf.effective_umol_s"
        )
        ppe = _positive_finite_number(
            ppf.get("ppe_umol_per_j"), field="ppf.ppe_umol_per_j"
        )
    else:  # pragma: no cover - guarded by the boundary lookup above.
        raise PublicationError("emitted-PPF system identifier is unsupported.")

    if not math.isclose(emitted_ppf, expected_emitted, rel_tol=1e-12, abs_tol=1e-9):
        raise PublicationError(
            "ppf.emitted_umol_s disagrees with the system emission boundary."
        )
    if not math.isclose(
        emitted_ppf,
        effective_power * ppe,
        rel_tol=1e-12,
        abs_tol=1e-9,
    ):
        raise PublicationError(
            "ppf.emitted_umol_s disagrees with effective power and boundary PPE."
        )


def _validate_target_cap_publication(
    *,
    request: RunRequest,
    samples: tuple[PpfdMapSample, ...],
    target_control: dict[str, object],
) -> None:
    """Fail closed on the raw final Stage A samples and limiting provenance."""

    requested = float(request.target_ppfd_umol_m2_s)
    tolerance = target_control.get("compliance_tolerance_umol_m2_s")
    if (
        isinstance(tolerance, bool)
        or not isinstance(tolerance, int | float)
        or float(tolerance) != TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S
    ):
        raise PublicationError("sampled-cap compliance tolerance is invalid.")
    limiting_index = max(
        range(len(samples)),
        key=lambda index: samples[index].ppfd_umol_m2_s,
    )
    limiting = samples[limiting_index]
    maximum = limiting.ppfd_umol_m2_s
    compliant = maximum <= requested + float(tolerance)
    if not compliant:
        raise PublicationError(
            "final published Stage A field exceeds the requested sampled PPFD cap."
        )
    if target_control.get("cap_compliant") is not True:
        raise PublicationError("sampled-cap compliance status is contradictory.")
    provenance = target_control.get("limiting_sample")
    expected = {
        "index": limiting_index,
        "x_m": limiting.x_m,
        "y_m": limiting.y_m,
        "z_m": limiting.z_m,
        "achieved_ppfd_umol_m2_s": maximum,
    }
    if provenance != expected:
        raise PublicationError("sampled-cap limiting-sample provenance is invalid.")
    full_maximum = target_control.get("full_output_maximum_ppfd_umol_m2_s")
    if (
        isinstance(full_maximum, bool)
        or not isinstance(full_maximum, int | float)
        or not math.isfinite(float(full_maximum))
        or float(full_maximum) <= 0.0
    ):
        raise PublicationError("sampled-cap full-output maximum is invalid.")
    expected_binding = float(full_maximum) > requested
    if target_control.get("cap_binding") is not expected_binding:
        raise PublicationError("sampled-cap binding status is contradictory.")


@dataclass(frozen=True, slots=True)
class RunSciencePublication:
    """Discriminated scientific inputs consumed by one publication pipeline."""

    system_id: str
    samples: tuple[PpfdMapSample, ...]
    layout_identity: Mapping[str, object]
    overlay_plan: AuthoritativeOverlayPlan
    visualization_reference: VisualizationReference
    quality_options: tuple[str, ...]
    target_control: Mapping[str, object]
    full_output_schedule: Mapping[str, object]
    operating_point: Mapping[str, object]
    counts: Mapping[str, int]
    transport_policy: Mapping[str, object]
    runtime_provenance: Mapping[str, object]
    engine_provenance: Mapping[str, object]
    engine_artifacts: Mapping[str, str]
    target_feasible: bool | None
    target_infeasibility: Mapping[str, object] | None
    physical_source_state: PhysicalSourceState
    mounting_height: Mapping[str, object]
    multispectral_transport: JuvenileMultispectralPublication | None = None

    def __post_init__(self) -> None:
        if self.system_id not in SYSTEM_DISPLAY_NAMES:
            raise PublicationError("publication system identifier is unsupported.")
        if self.overlay_plan.system_id != self.system_id:
            raise PublicationError("layout overlay system identity disagrees with the run.")
        if not self.samples:
            raise PublicationError("publication requires final baseline samples.")
        if self.physical_source_state.payload().get("system_id") != self.system_id:
            raise PublicationError(
                "physical source-state system identity disagrees with the run."
            )
        validate_emitted_ppf_contract(
            self.system_id,
            power=self.operating_point.get("power"),
            ppf=self.operating_point.get("ppf"),
        )
        if self.system_id == CONVENTIONAL_SYSTEM_ID:
            _validate_conventional_fixture_power_contract(
                layout_identity=self.layout_identity,
                overlay_plan=self.overlay_plan,
                counts=self.counts,
                target_control=self.target_control,
                full_output_schedule=self.full_output_schedule,
                operating_point=self.operating_point,
            )
        try:
            expected_mounting = MountingGeometry.resolve(
                self.mounting_height.get("mounting_height_in")
            ).to_payload()
        except (AttributeError, ValueError) as exc:
            raise PublicationError(
                "mounting-height provenance is incomplete."
            ) from exc
        if dict(self.mounting_height) != expected_mounting:
            raise PublicationError("mounting-height provenance is inconsistent.")
        if self.system_id == HPS_SYSTEM_ID:
            if self.visualization_reference.kind != "achieved_final_baseline_mean":
                raise PublicationError(
                    "HPS visualization must reference its achieved full-output mean."
                )
            if self.target_feasible is not None or self.target_infeasibility is not None:
                raise PublicationError(
                    "HPS publication must not carry lighting-target feasibility."
                )
            if (
                self.target_control.get("lighting_target_supported") is not False
                or "requested_target_ppfd_umol_m2_s" in self.target_control
            ):
                raise PublicationError(
                    "HPS publication contains contradictory target control."
                )
        else:
            lighting_target_mode = self.target_control.get(
                "lighting_target_mode", "mean_target"
            )
            expected_reference_kind = (
                "requested_sampled_ppfd_cap"
                if lighting_target_mode == "target_capped"
                else "requested_lighting_target"
            )
            if self.visualization_reference.kind != expected_reference_kind:
                raise PublicationError(
                    "LED visualization reference disagrees with its lighting "
                    "target policy."
                )
            if not isinstance(self.target_feasible, bool):
                raise PublicationError(
                    "LED publication requires a boolean lighting-target feasibility result."
                )
            if (
                "requested_target_ppfd_umol_m2_s" not in self.target_control
                or self.target_control.get("feasible") is not self.target_feasible
                or self.target_control.get("infeasibility")
                != self.target_infeasibility
            ):
                raise PublicationError(
                    "LED publication target-control fields are contradictory."
                )
            if self.target_feasible and self.target_infeasibility is not None:
                raise PublicationError(
                    "a feasible LED publication cannot include infeasibility details."
                )
            if not self.target_feasible and not isinstance(
                self.target_infeasibility, Mapping
            ):
                raise PublicationError(
                    "an infeasible LED publication requires structured details."
                )
            if not self.target_feasible and not self.target_infeasibility:
                raise PublicationError(
                    "an infeasible LED publication requires non-empty details."
                )


@dataclass(frozen=True, slots=True)
class PublishedRun:
    metrics: dict[str, object]
    manifest: dict[str, object]
    visualization: VisualizationArtifactSet


def scientific_runtime_identity(
    runtime_provenance: Mapping[str, object],
) -> dict[str, object]:
    """Exclude diagnostic executable locations from portable software identity."""

    return {
        key: value
        for key, value in runtime_provenance.items()
        if key != "radiance_executable_paths"
    }


def _aisle_publication_payload(
    request: RunRequest,
    science: RunSciencePublication,
    natural_fit: NaturalFitLayoutPlan,
) -> dict[str, object]:
    domain = request.active_domain
    half_x = domain.outer_aligned_length_m / 2.0
    half_y = domain.outer_aligned_width_m / 2.0

    fixture_bounds: list[tuple[float, float, float, float]] = []
    fixtures = science.layout_identity.get("fixtures")
    modules = science.layout_identity.get("modules")
    if science.system_id == PROPOSED_SYSTEM_ID and isinstance(modules, list):
        for module in modules:
            if not isinstance(module, Mapping):
                fixture_bounds.clear()
                break
            try:
                center_x = float(module["x_m"])
                center_y = float(module["y_m"])
            except (KeyError, TypeError, ValueError):
                fixture_bounds.clear()
                break
            fixture_bounds.append(
                (
                    center_x - DEFAULT_MODULE_FOOTPRINT_X_M / 2.0,
                    center_x + DEFAULT_MODULE_FOOTPRINT_X_M / 2.0,
                    center_y - DEFAULT_MODULE_FOOTPRINT_Y_M / 2.0,
                    center_y + DEFAULT_MODULE_FOOTPRINT_Y_M / 2.0,
                )
            )
    elif science.system_id in {
        CONVENTIONAL_SYSTEM_ID,
        HPS_SYSTEM_ID,
    } and isinstance(fixtures, list):
        for fixture in fixtures:
            bounds = (
                fixture.get("footprint_bounds_m")
                if isinstance(fixture, Mapping)
                else None
            )
            if isinstance(bounds, Mapping):
                try:
                    fixture_bounds.append(
                        (
                            float(bounds["aligned_min_x_m"]),
                            float(bounds["aligned_max_x_m"]),
                            float(bounds["aligned_min_y_m"]),
                            float(bounds["aligned_max_y_m"]),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    fixture_bounds.clear()
                    break
    if not fixture_bounds:
        for rectangle in science.overlay_plan.rectangles:
            radians = math.radians(rectangle.orientation_degrees)
            half_extent_x = (
                abs(math.cos(radians)) * rectangle.width_x_m
                + abs(math.sin(radians)) * rectangle.height_y_m
            ) / 2.0
            half_extent_y = (
                abs(math.sin(radians)) * rectangle.width_x_m
                + abs(math.cos(radians)) * rectangle.height_y_m
            ) / 2.0
            fixture_bounds.append(
                (
                    rectangle.center_x_m - half_extent_x,
                    rectangle.center_x_m + half_extent_x,
                    rectangle.center_y_m - half_extent_y,
                    rectangle.center_y_m + half_extent_y,
                )
            )
    fixture_clearance = min(
        min(
            bound[0] + half_x,
            half_x - bound[1],
            bound[2] + half_y,
            half_y - bound[3],
        )
        for bound in fixture_bounds
    )
    sensor_clearance = min(
        min(
            sample.x_m + half_x,
            half_x - sample.x_m,
            sample.y_m + half_y,
            half_y - sample.y_m,
        )
        for sample in science.samples
    )
    local = natural_fit.plant_local_bounds_m
    plant_bounds = (
        min(item.aligned_x_m + local.min_x_m for item in natural_fit.plants),
        max(item.aligned_x_m + local.max_x_m for item in natural_fit.plants),
        min(item.aligned_y_m + local.min_y_m for item in natural_fit.plants),
        max(item.aligned_y_m + local.max_y_m for item in natural_fit.plants),
    )
    plant_clearance = min(
        plant_bounds[0] + half_x,
        half_x - plant_bounds[1],
        plant_bounds[2] + half_y,
        half_y - plant_bounds[3],
    )
    return {
        "enabled": domain.enabled,
        "fixed_width_ft_per_wall": AISLE_WIDTH_FT,
        "fixed_width_m_per_wall": AISLE_WIDTH_M,
        "applied_width_ft_per_wall": domain.aisle_width_ft,
        "applied_width_m_per_wall": domain.aisle_width_m,
        "active_domain_identity_sha256": domain.identity_sha256,
        "outer_room_dimensions_m": {
            "length": domain.outer_requested_length_m,
            "width": domain.outer_requested_width_m,
        },
        "active_grow_dimensions_m": {
            "length": domain.active_requested_length_m,
            "width": domain.active_requested_width_m,
        },
        "outer_room_area_m2": domain.outer_area_m2,
        "active_grow_area_m2": domain.active_area_m2,
        "evaluated_stage_a_receiver_area_m2": domain.active_area_m2,
        "final_fixture_count": int(science.counts["fixtures"]),
        "final_plant_count": natural_fit.total_count,
        "actual_minimum_fixture_bound_clearance_m": fixture_clearance,
        "actual_minimum_plant_bound_clearance_m": plant_clearance,
        "actual_minimum_sensor_clearance_m": sensor_clearance,
    }


def publish_native_baseline_run(
    root: Path,
    *,
    run_id: str,
    request: RunRequest,
    science: RunSciencePublication,
    event_sink: EventSink,
) -> PublishedRun:
    """Publish one field through the common metrics/manifest/viewer pipeline."""

    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise PublicationError("run publication root must be a directory.")
    if request.system != science.system_id:
        raise PublicationError("request and science system discriminators disagree.")
    if dict(science.mounting_height) != request.mounting_geometry.to_payload():
        raise PublicationError(
            "request and scientific mounting-height provenance disagree."
        )
    if any(
        sample.z_m != request.mounting_geometry.reference_plane_z_m
        for sample in science.samples
    ):
        raise PublicationError(
            "baseline samples do not use the authoritative mounting reference plane."
        )
    transport_required = (
        request.analysis_scope
        is AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM
    )
    if transport_required != (science.multispectral_transport is not None):
        raise PublicationError(
            "analysis scope and multispectral transport completion disagree."
        )
    if science.multispectral_transport is not None and (
        science.multispectral_transport.metadata.get("run_id") != run_id
        or science.multispectral_transport.metadata.get("system_id")
        != science.system_id
        or science.multispectral_transport.metadata.get("source_state_id")
        != science.physical_source_state.source_state_id
        or science.multispectral_transport.metadata.get("include_far_red")
        is not request.include_far_red
        or science.multispectral_transport.metadata.get("far_red_executed")
        is not request.include_far_red
        or not isinstance(
            science.multispectral_transport.metadata.get("room"), Mapping
        )
        or science.multispectral_transport.metadata["room"].get(
            "active_domain"
        )
        != request.active_domain.to_payload()
        or (
            science.system_id == PROPOSED_SYSTEM_ID
            and (
                not isinstance(
                    science.multispectral_transport.metadata.get("spectral_basis"),
                    Mapping,
                )
                or science.multispectral_transport.metadata["spectral_basis"].get(
                    "id"
                )
                != request.spectral_basis.value
            )
        )
    ):
        raise PublicationError(
            "multispectral transport is not linked to this run's Stage A source state."
        )
    if science.multispectral_transport is not None:
        aggregation = science.multispectral_transport.scientific_aggregation
        if (
            aggregation.metadata.get("run_id") != run_id
            or aggregation.metadata.get("system_id") != science.system_id
            or aggregation.metadata.get("source_state_id")
            != science.physical_source_state.source_state_id
            or aggregation.metadata.get("transport_metadata_sha256")
            != science.multispectral_transport.metadata_artifact.sha256
            or aggregation.metadata.get("aggregation_executed") is not True
            or aggregation.metadata.get("include_far_red")
            is not request.include_far_red
            or aggregation.metadata.get("far_red_executed")
            is not request.include_far_red
            or aggregation.metadata.get("band_order")
            != science.multispectral_transport.metadata.get("band_order")
            or aggregation.metadata.get("spectral_basis")
            != science.multispectral_transport.metadata.get("spectral_basis")
        ):
            raise PublicationError(
                "scientific aggregation is not linked to the immutable Stage B transport."
            )
    if science.quality_options != tuple(radiance_options(request.quality)):
        raise PublicationError(
            "publication quality options disagree with the approved request preset."
        )
    try:
        natural_fit = plan_natural_fit_layout_from_feet(
            request.room_length_ft,
            request.room_width_ft,
            active_domain=request.active_domain,
        )
        baseline_leaf_scene = build_baseline_physical_leaf_scene(
            natural_fit,
            canonical_plant=(
                None
                if science.multispectral_transport is None
                else science.multispectral_transport.juvenile_scene.canonical_plant
            ),
        )
        if baseline_leaf_scene.layout_plan_hash != natural_fit.plan_hash:
            raise ValueError(
                "juvenile scene does not match the authorized Natural-fit plan."
            )
        spatial = compute_spatial_uniformity(
            tuple(sample.ppfd_umol_m2_s for sample in science.samples)
        )
        fspm_policy = resolve_fspm_target_policy(
            mode=request.fspm_target_mode,
            achieved_baseline_mean_ppfd=spatial.mean_ppfd,
            tolerance_umol_m2_s=request.fspm_target_tolerance_umol_m2_s,
            override_umol_m2_s=request.fspm_target_override_umol_m2_s,
        )
        baseline_leaf_uniformity = build_baseline_leaf_uniformity_publication(
            run_id=run_id,
            system_id=science.system_id,
            analysis_scope=request.analysis_scope.value,
            samples=science.samples,
            scene=baseline_leaf_scene,
            overlay_payload=science.overlay_plan.to_dict(),
            requested_lighting_target_umol_m2_s=getattr(
                request, "target_ppfd_umol_m2_s", None
            ),
            achieved_stage_a_mean_umol_m2_s=spatial.mean_ppfd,
            target_mode=request.fspm_target_mode,
            target_override_umol_m2_s=(
                request.fspm_target_override_umol_m2_s
            ),
            target_tolerance_umol_m2_s=(
                request.fspm_target_tolerance_umol_m2_s
            ),
        )
    except (RuntimeError, ValueError) as exc:
        raise PublicationError(str(exc)) from exc
    visualization_reference = science.visualization_reference
    if visualization_reference.kind == "achieved_final_baseline_mean":
        visualization_reference = VisualizationReference.achieved_baseline_mean(
            spatial.mean_ppfd
        )
    reported_field = (
        "final_full_output_horizontal_reference_plane_ppfd"
        if science.system_id == "hps"
        else "final_achieved_target_controlled_horizontal_reference_plane_ppfd"
    )
    spatial_report = spatial.report_dict(reported_field=reported_field)

    _emit(
        event_sink,
        "artifacts.writing",
        "Writing final PPFD and shared scientific metadata artifacts.",
        {"system": science.system_id, "sample_count": len(science.samples)},
    )
    natural_fit_json = natural_fit.to_json()
    natural_fit_artifact_sha256 = hashlib.sha256(
        natural_fit_json.encode("utf-8")
    ).hexdigest()
    atomic_write_text(resolved_root / "natural_fit_layout.json", natural_fit_json)
    atomic_write_text(resolved_root / "ppfd.csv", _ppfd_csv(science.samples))
    atomic_write_text(
        resolved_root / BASELINE_LEAF_UNIFORMITY_FILENAME,
        baseline_leaf_uniformity.data.decode("utf-8"),
    )
    fspm_payload = fspm_policy.to_dict()
    target_control = dict(science.target_control)
    target_control["achieved_mean_ppfd_umol_m2_s"] = spatial.mean_ppfd
    if science.system_id != HPS_SYSTEM_ID:
        lighting_target_mode = request.lighting_target_mode.value
        declared_mode = target_control.get("lighting_target_mode")
        if declared_mode not in {None, lighting_target_mode}:
            raise PublicationError(
                "target-control lighting mode disagrees with the canonical request."
            )
        target_control["lighting_target_mode"] = lighting_target_mode
        target_control["achieved_maximum_ppfd_umol_m2_s"] = spatial.maximum_ppfd
        if lighting_target_mode == "target_capped":
            _validate_target_cap_publication(
                request=request,
                samples=science.samples,
                target_control=target_control,
            )
    target_control["fspm_target_policy"] = fspm_payload
    full_output_schedule = dict(science.full_output_schedule)
    if science.system_id == "hps":
        # Fixed-output HPS has one and only one field.  Use the shared Float64
        # calculation as the canonical statistic everywhere, avoiding a
        # platform-dependent last-bit difference from transport-side fmean.
        target_control["full_output_mean_ppfd_umol_m2_s"] = spatial.mean_ppfd
        field_metrics = full_output_schedule.get("full_output_field_metrics")
        if isinstance(field_metrics, Mapping):
            full_output_schedule["full_output_field_metrics"] = {
                **dict(field_metrics),
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
                "minimum_to_mean_uniformity": (
                    spatial.minimum_to_mean_uniformity
                ),
            }
    operating_point = dict(science.operating_point)
    source_state_payload = science.physical_source_state.to_dict()
    if (
        source_state_payload.get("layout_identity_sha256")
        != hash_json(science.layout_identity)
        or source_state_payload.get("full_output_schedule_sha256")
        != hash_json(full_output_schedule)
        or source_state_payload.get("operating_point_sha256")
        != hash_json(operating_point)
    ):
        raise PublicationError(
            "physical source-state hashes disagree with Stage A publication inputs."
        )
    source_state_path = resolved_root / "physical-source-state.json"
    _write_json(source_state_path, source_state_payload)
    source_state_artifact_sha256 = hashlib.sha256(
        source_state_path.read_bytes()
    ).hexdigest()
    _write_json(resolved_root / "target_control.json", target_control)
    _write_json(
        resolved_root / "full_output_schedule.json", full_output_schedule
    )
    _write_json(resolved_root / "operating-point.json", operating_point)

    _emit(
        event_sink,
        "visualizations.publishing",
        "Publishing the shared validated PPFD visualization derivatives.",
        {"sample_count": len(science.samples)},
    )
    visualization = publish_ppfd_visualizations(
        resolved_root,
        run_id=run_id,
        samples=science.samples,
        layout=None,
        layout_identity=science.layout_identity,
        overlay_plan=science.overlay_plan,
        reference=visualization_reference,
    )

    requested_target = getattr(request, "target_ppfd_umol_m2_s", None)
    mounting_height = dict(science.mounting_height)
    surface_flux_display: SurfaceFluxDisplayPublication | None = None
    if science.multispectral_transport is not None:
        operating_policy = (
            "target-controlled" if requested_target is not None else "fixed-output"
        )
        transport_sampling = None
        try:
            transport_plant = science.multispectral_transport.metadata.get("plant")
            transport_sampling = (
                transport_plant.get("sampling_profile_id")
                if isinstance(transport_plant, Mapping)
                else None
            )
            display_calibration = None
            if transport_sampling == REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID:
                display_calibration = load_packaged_surface_flux_display_calibration()
                surface_flux_reference = (
                    select_authenticated_achieved_surface_flux_reference(
                        operating_policy=operating_policy,
                        achieved_stage_a_mean_ppfd_umol_m2_s=spatial.mean_ppfd,
                    )
                )
            else:
                surface_flux_reference = select_surface_flux_reference(
                    operating_policy=operating_policy,
                    requested_stage_a_target_ppfd_umol_m2_s=requested_target,
                    achieved_stage_a_mean_ppfd_umol_m2_s=spatial.mean_ppfd,
                )
            surface_flux_display = build_surface_flux_display_publication(
                resolved_root,
                run_id=run_id,
                quality=request.quality,
                natural_fit=natural_fit,
                reference=surface_flux_reference,
                multispectral=science.multispectral_transport,
                display_calibration=display_calibration,
            )
        except SurfaceFluxDisplayCalibrationError as exc:
            _emit(
                event_sink,
                "surface_flux_display.unavailable",
                "Surface-flux coloring is unavailable because its authenticated "
                "display-calibration resource failed closed; raw Stage C is retained.",
                {"reason": str(exc)},
            )
        except SurfaceFluxDisplayError as exc:
            if (
                transport_sampling
                != REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
                and "sampling profile is not covered" not in str(exc)
            ):
                raise PublicationError(
                    f"surface-flux display validation failed: {exc}"
                ) from exc
            _emit(
                event_sink,
                "surface_flux_display.unavailable",
                "Surface-flux coloring is unavailable because its sampling-specific "
                "display calibration did not authenticate; raw Stage C is retained.",
                {"reason": str(exc)},
            )
        except (OSError, ValueError) as exc:
            if (
                transport_sampling
                == REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
            ):
                _emit(
                    event_sink,
                    "surface_flux_display.unavailable",
                    "Surface-flux coloring is unavailable because an optimized "
                    "display artifact failed authentication; raw Stage C is retained.",
                    {"reason": str(exc)},
                )
            else:
                raise PublicationError(
                    f"surface-flux display validation failed: {exc}"
                ) from exc
        if surface_flux_display is not None:
            _emit(
                event_sink,
                "surface_flux_display.validated",
                "Validated the display-only surface-flux derivative and calibration family.",
                {
                    "quality": request.quality,
                    "profile_id": surface_flux_display.metadata["quality_family"][
                        "selected_profile_id"
                    ],
                },
            )
    power = operating_point.get("power")
    ppf = operating_point.get("ppf")
    if not isinstance(power, Mapping) or not isinstance(ppf, Mapping):
        raise PublicationError("operating point must declare power and PPF totals.")
    spectral_basis_payload: dict[str, object] | None = None
    proposed_control_payload: dict[str, object] | None = None
    proposed_source_payload: dict[str, object] | None = None
    if science.system_id == PROPOSED_SYSTEM_ID:
        raw_proposed_control = operating_point.get("proposed_control")
        proposed_control_payload = (
            dict(raw_proposed_control)
            if isinstance(raw_proposed_control, Mapping)
            else {
                "mode": request.control_mode.value,
                "label": (
                    "Uniform module dimming"
                    if request.control_mode.value == "uniform_module_dimming"
                    else "Basis-matrix optimized"
                ),
                "basis_matrix_solver_enabled": (
                    request.control_mode.value == "basis_matrix_optimized"
                ),
            }
        )
        raw_proposed_source = operating_point.get("proposed_source")
        if not isinstance(raw_proposed_source, Mapping):
            raise PublicationError("Proposed source-mode provenance is missing.")
        proposed_source_payload = dict(raw_proposed_source)
        if science.multispectral_transport is None:
            spectral_basis_payload = {
                "id": request.spectral_basis.value,
                "label": PROPOSED_SPECTRAL_BASIS_LABELS[request.spectral_basis],
                "applied_to_multispectral_transport": False,
                "stage_a_scalar_baseline_is_spd_independent": True,
                "counterfactual_spectral_control": False,
                "physical_proposed_spectral_configuration": True,
            }
        else:
            raw_spectral_basis = science.multispectral_transport.metadata.get(
                "spectral_basis"
            )
            if not isinstance(raw_spectral_basis, Mapping):
                raise PublicationError("Proposed spectral basis provenance is missing.")
            spectral_basis_payload = dict(raw_spectral_basis) | {
                "applied_to_multispectral_transport": True,
                "stage_a_scalar_baseline_is_spd_independent": True,
            }
    metrics: dict[str, object] = {
        "schema_id": "fspm-optics.native-baseline-metrics",
        "schema_version": NATIVE_BASELINE_PUBLICATION_SCHEMA_VERSION,
        "run_id": run_id,
        "system_id": science.system_id,
        "system": SYSTEM_DISPLAY_NAMES[science.system_id],
        "lighting_target_mode": (
            None
            if science.system_id == HPS_SYSTEM_ID
            else request.lighting_target_mode.value
        ),
        **(
            {}
            if science.system_id != PROPOSED_SYSTEM_ID
            else {
                "proposed_layout": {
                    "mode": science.layout_identity.get(
                        "proposed_layout_mode"
                    ),
                    "fixture_policy_id": science.layout_identity.get(
                        "fixture_policy_id"
                    ),
                    "ring_mode": science.layout_identity.get(
                        "proposed_ring_mode"
                    ),
                    "module_pattern_id": science.layout_identity.get(
                        "module_pattern_id"
                    ),
                }
            }
        ),
        "mounting_height": mounting_height,
        "analysis_scope": _analysis_scope_payload(request.analysis_scope),
        **(
            {}
            if spectral_basis_payload is None
            else {"spectral_basis": spectral_basis_payload}
        ),
        **(
            {}
            if proposed_control_payload is None
            else {"proposed_control": proposed_control_payload}
        ),
        **(
            {}
            if proposed_source_payload is None
            else {"proposed_source": proposed_source_payload}
        ),
        "runtime_status": "scientifically_complete",
        "requested_target_ppfd_umol_m2_s": requested_target,
        "full_output_mean_ppfd_umol_m2_s": target_control.get(
            "full_output_mean_ppfd_umol_m2_s"
        ),
        "achieved_mean_ppfd_umol_m2_s": spatial.mean_ppfd,
        "achieved_maximum_ppfd_umol_m2_s": spatial.maximum_ppfd,
        "full_output_maximum_ppfd_umol_m2_s": target_control.get(
            "full_output_maximum_ppfd_umol_m2_s"
        ),
        "cap_binding": target_control.get("cap_binding"),
        "cap_compliant": target_control.get("cap_compliant"),
        "cap_compliance_tolerance_umol_m2_s": target_control.get(
            "compliance_tolerance_umol_m2_s"
        ),
        "limiting_sample": target_control.get("limiting_sample"),
        "minimum_ppfd_umol_m2_s": spatial.minimum_ppfd,
        "maximum_ppfd_umol_m2_s": spatial.maximum_ppfd,
        "standard_deviation_ppfd_umol_m2_s": (
            spatial.population_standard_deviation_ppfd
        ),
        "cv_percent": spatial.coefficient_of_variation_percent,
        "spatial_uniformity": spatial_report,
        "visualization": {
            "field_identity_sha256": visualization.field_sha256,
            "sample_count": visualization.sample_count,
            "metadata_artifact": "visualization.json",
            "reference_policy": visualization_reference.color_limit_policy,
        },
        "dimming_factor": target_control.get("dimming_factor"),
        "target_feasible": science.target_feasible,
        "target_infeasibility": (
            None
            if science.target_infeasibility is None
            else dict(science.target_infeasibility)
        ),
        "power": dict(power),
        "ppf": dict(ppf),
        "operating_point": operating_point,
        "counts": {
            **dict(science.counts),
            "sensors": visualization.sample_count,
            "natural_fit_plants": natural_fit.total_count,
        },
        "room": {
            "length_ft": request.room_length_ft,
            "width_ft": request.room_width_ft,
            "outer_area_m2": request.active_domain.outer_area_m2,
            "active_length_ft": (
                request.active_domain.active_requested_length_ft
            ),
            "active_width_ft": (
                request.active_domain.active_requested_width_ft
            ),
            "active_area_m2": request.active_domain.active_area_m2,
        },
        "aisle_mode": _aisle_publication_payload(
            request,
            science,
            natural_fit,
        ),
        "quality": request.quality,
        "fspm_target_policy": fspm_payload,
        "baseline_leaf_position_uniformity": (
            baseline_leaf_uniformity.metrics_payload()
        ),
        "fspm_execution": {
            "reference_resolution_executes_transport": False,
            "run_includes_juvenile_fspm_transport": (
                science.multispectral_transport is not None
            ),
            "aggregation_executed": (
                science.multispectral_transport is not None
            ),
        },
        "fspm_surface_light_metrics": (
            None
            if science.multispectral_transport is None
            else {
                "modeled_physical_one_sided_leaf_area_m2": (
                    science.multispectral_transport.scientific_aggregation.room_summary[
                        "modeled_physical_one_sided_leaf_area_m2"
                    ]
                ),
                "counts": (
                    science.multispectral_transport.scientific_aggregation.room_summary[
                        "counts"
                    ]
                ),
                "surface_light": (
                    science.multispectral_transport.scientific_aggregation.room_summary[
                        "surface_light"
                    ]
                ),
                "absorbed_par_metrics": (
                    science.multispectral_transport.scientific_aggregation.room_summary[
                        "absorbed_par_metrics"
                    ]
                ),
                "band_order": list(
                    science.multispectral_transport.scientific_aggregation.metadata[
                        "band_order"
                    ]
                ),
                "far_red_executed": (
                    science.multispectral_transport.scientific_aggregation.metadata[
                        "far_red_executed"
                    ]
                ),
                "closure": (
                    science.multispectral_transport.scientific_aggregation.room_summary[
                        "closure"
                    ]
                ),
                "raw_receiver_sha256_by_band": (
                    science.multispectral_transport.scientific_aggregation.room_summary[
                        "raw_receiver_sha256_by_band"
                    ]
                ),
                "aggregation_metadata_sha256": (
                    science.multispectral_transport.scientific_aggregation
                    .metadata_artifact.sha256
                ),
                "aggregate_artifact_sha256_by_role": {
                    str(record["role"]): str(record["sha256"])
                    for record in science.multispectral_transport.scientific_aggregation.metadata[
                        "ordered_artifact_inventory"
                    ]
                },
            }
        ),
        "fspm_surface_flux_coloring": (
            None
            if surface_flux_display is None
            else {
                "available": True,
                "display_only": True,
                "raw_scientific_values_modified": False,
                "metadata_artifact": (
                    surface_flux_display.viewer_artifacts.metadata.filename
                ),
                "metadata_sha256": (
                    surface_flux_display.viewer_artifacts.metadata.sha256
                ),
                "quality_family": dict(
                    surface_flux_display.metadata["quality_family"]
                ),
                "reference": dict(surface_flux_display.metadata["reference"]),
                "metrics": ["incident_par", "absorbed_par"],
                "far_red_excluded": True,
            }
        ),
        "engine_provenance": dict(science.engine_provenance),
        "scientific_limits": {
            "run_includes_juvenile_fspm_transport": (
                science.multispectral_transport is not None
            ),
            "fspm_surface_light_aggregation_executed": (
                science.multispectral_transport is not None
            ),
            "fspm_override_affects_baseline_transport": False,
            "visualization_affects_raw_samples": False,
            "target_capped_incident_totals_produced": False,
            "target_capped_absorbed_totals_produced": False,
            "surface_flux_coloring_is_display_only": True,
            "surface_flux_coloring_modifies_phase27g_c_values": False,
        },
    }

    _emit(
        event_sink,
        "viewer.publishing",
        "Publishing the accepted run-local plant-layout viewer.",
    )
    viewer_sampling_profile_id = (
        REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
    )
    if science.multispectral_transport is not None:
        transport_plant = science.multispectral_transport.metadata.get("plant")
        declared_sampling = (
            transport_plant.get("sampling_profile_id")
            if isinstance(transport_plant, Mapping)
            else None
        )
        if (
            isinstance(declared_sampling, str)
            and declared_sampling in REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS
        ):
            viewer_sampling_profile_id = str(declared_sampling)
        elif surface_flux_display is not None:
            # Historical D2 metadata predates the explicit sampling-profile field.
            viewer_sampling_profile_id = (
                REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID
            )
    layout_identity = dict(science.layout_identity)
    visualization_files = {
        artifact.filename: artifact.data for artifact in visualization.artifacts
    }
    ppfd_heatmap = bind_ppfd_heatmap_viewer_artifacts(
        metadata_bytes=visualization_files["visualization.json"],
        scatter_bytes=visualization_files["ppfd-scatter.f32le.bin"],
        expected_field_identity_sha256=visualization.field_sha256,
        target_coverage=baseline_leaf_uniformity.target_coverage,
    )
    publish_run_viewer(
        resolved_root / "plant-layout-viewer",
        run_id=run_id,
        system_id=science.system_id,
        requested_length_ft=request.room_length_ft,
        requested_width_ft=request.room_width_ft,
        natural_fit=natural_fit,
        natural_fit_artifact_sha256=natural_fit_artifact_sha256,
        layout_identity=layout_identity,
        mounting_height=mounting_height,
        run_information=(
            None
            if proposed_control_payload is None
            else {
                "proposed_control": proposed_control_payload,
                "proposed_source": proposed_source_payload,
                "proposed_layout": {
                    "ring_mode": layout_identity.get("proposed_ring_mode"),
                    "module_pattern_id": layout_identity.get(
                        "module_pattern_id"
                    ),
                },
            }
        ),
        surface_flux=(
            None
            if surface_flux_display is None
            else surface_flux_display.viewer_artifacts
        ),
        ppfd_heatmap=ppfd_heatmap,
        sampling_profile_id=viewer_sampling_profile_id,
        canonical_plant=(
            baseline_leaf_scene.canonical_plant
            if baseline_leaf_scene.canonical_plant.config.sampling_profile_id
            == viewer_sampling_profile_id
            else None
        ),
    )
    fixture_catalog_path = resolved_root / "plant-layout-viewer/fixtures/catalog.v1.json"
    fixture_catalog_bytes = fixture_catalog_path.read_bytes()
    fixture_catalog_sha256 = hashlib.sha256(fixture_catalog_bytes).hexdigest()
    fixture_catalog = json.loads(fixture_catalog_bytes)
    if not isinstance(fixture_catalog, dict):
        raise PublicationError("fixture catalog must be a JSON object.")

    request_payload = request.to_dict()
    runtime_provenance = dict(science.runtime_provenance)
    identities: dict[str, str] = {
        "request_sha256": _hash_json(request_payload),
        "room_sha256": _hash_json(
            request.active_domain.to_payload()
        ),
        "active_domain_sha256": request.active_domain.identity_sha256,
        "fixture_layout_sha256": _hash_json(layout_identity),
        "mounting_height_sha256": _hash_json(mounting_height),
        "natural_fit_layout_sha256": natural_fit_artifact_sha256,
        "overlay_plan_sha256": _hash_json(science.overlay_plan.to_dict()),
        "full_output_schedule_sha256": _hash_json(full_output_schedule),
        "target_control_sha256": _hash_json(target_control),
        "operating_point_sha256": _hash_json(operating_point),
        "physical_source_state_sha256": source_state_artifact_sha256,
        "reference_plane_field_sha256": visualization.field_sha256,
        "baseline_leaf_position_uniformity_artifact_sha256": (
            baseline_leaf_uniformity.sha256
        ),
        "baseline_leaf_position_uniformity_derivation_sha256": (
            baseline_leaf_uniformity.derivation_identity_sha256
        ),
        "quality_sha256": _hash_json(
            {"quality": request.quality, "radiance_options": science.quality_options}
        ),
        "runtime_software_sha256": _hash_json(
            scientific_runtime_identity(runtime_provenance)
        ),
        "engine_provenance_sha256": _hash_json(science.engine_provenance),
    }
    if science.multispectral_transport is not None:
        identities["multispectral_transport_metadata_sha256"] = (
            science.multispectral_transport.metadata_artifact.sha256
        )
        identities["fspm_scientific_aggregation_metadata_sha256"] = (
            science.multispectral_transport.scientific_aggregation
            .metadata_artifact.sha256
        )
    if spectral_basis_payload is not None:
        identities["spectral_basis_identity_sha256"] = _hash_json(
            spectral_basis_payload
        )
    if proposed_control_payload is not None:
        identities["proposed_control_identity_sha256"] = _hash_json(
            proposed_control_payload
        )
    if proposed_source_payload is not None:
        identities["proposed_source_identity_sha256"] = _hash_json(
            proposed_source_payload
        )
    if surface_flux_display is not None:
        identities["surface_flux_display_metadata_sha256"] = (
            surface_flux_display.viewer_artifacts.metadata.sha256
        )
        identities["surface_flux_display_values_sha256"] = (
            surface_flux_display.viewer_artifacts.patch_values.sha256
        )
        if surface_flux_display.viewer_artifacts.calibration_coefficients is not None:
            identities["surface_flux_display_calibration_sha256"] = (
                surface_flux_display.viewer_artifacts.calibration_coefficients.sha256
            )
    identities["scientific_run_sha256"] = _hash_json(identities)
    field_source = (
        "final_full_output_reference_plane"
        if science.system_id == "hps"
        else "final_achieved_target_controlled_reference_plane"
    )
    manifest: dict[str, object] = {
        "schema_id": "fspm-optics.native-baseline-run",
        "schema_version": NATIVE_BASELINE_PUBLICATION_SCHEMA_VERSION,
        "run_id": run_id,
        "system_id": science.system_id,
        "system": SYSTEM_DISPLAY_NAMES[science.system_id],
        "lighting_target_mode": (
            None
            if science.system_id == HPS_SYSTEM_ID
            else request.lighting_target_mode.value
        ),
        **(
            {}
            if science.system_id != PROPOSED_SYSTEM_ID
            else {
                "proposed_layout": {
                    "mode": layout_identity.get("proposed_layout_mode"),
                    "fixture_policy_id": layout_identity.get(
                        "fixture_policy_id"
                    ),
                    "ring_mode": layout_identity.get(
                        "proposed_ring_mode"
                    ),
                    "module_pattern_id": layout_identity.get(
                        "module_pattern_id"
                    ),
                }
            }
        ),
        "analysis_scope": _analysis_scope_payload(request.analysis_scope),
        **(
            {}
            if spectral_basis_payload is None
            else {"spectral_basis": spectral_basis_payload}
        ),
        **(
            {}
            if proposed_control_payload is None
            else {"proposed_control": proposed_control_payload}
        ),
        **(
            {}
            if proposed_source_payload is None
            else {"proposed_source": proposed_source_payload}
        ),
        "logical_stages": _logical_stages_payload(request.analysis_scope),
        "request": request_payload,
        "mounting_height": mounting_height,
        "active_domain": request.active_domain.to_payload(),
        "room": {
            "length_ft": request.room_length_ft,
            "width_ft": request.room_width_ft,
            "length_m": science.overlay_plan.room_length_m,
            "width_m": science.overlay_plan.room_width_m,
            "outer_area_m2": request.active_domain.outer_area_m2,
            "active_area_m2": request.active_domain.active_area_m2,
            "evaluated_stage_a_receiver_area_m2": (
                request.active_domain.active_area_m2
            ),
        },
        "quality": {
            "name": request.quality,
            "radiance_options": list(science.quality_options),
        },
        "layout": layout_identity,
        "authoritative_overlay_plan": science.overlay_plan.to_dict(),
        "natural_fit": {
            "artifact_role": "natural_fit_layout",
            "artifact_sha256": natural_fit_artifact_sha256,
            "ordering": "Y-major/X-minor",
            "plan_hash": natural_fit.plan_hash,
            "plant_count": natural_fit.total_count,
            "policy_id": natural_fit.policy.policy_id,
            "profile_id": natural_fit.profile_id,
            "transport_executed": science.multispectral_transport is not None,
        },
        "baseline_leaf_position_uniformity": (
            baseline_leaf_uniformity.manifest_payload()
        ),
        "physical_source_state": {
            "artifact": "physical-source-state.json",
            "artifact_sha256": source_state_artifact_sha256,
            "source_state_id": science.physical_source_state.source_state_id,
            "stage_a_authoritative_handoff": True,
        },
        "multispectral_transport": (
            None
            if science.multispectral_transport is None
            else science.multispectral_transport.manifest_payload()
        ),
        "fspm_scientific_aggregation": (
            None
            if science.multispectral_transport is None
            else science.multispectral_transport.scientific_aggregation
            .manifest_payload()
        ),
        "fspm_surface_flux_display": (
            None
            if surface_flux_display is None
            else surface_flux_display.manifest_payload()
        ),
        "viewer_publication": {
            "fixture_catalog": "plant-layout-viewer/fixtures/catalog.v1.json",
            "fixture_catalog_sha256": fixture_catalog_sha256,
            "fixture_plan_sha256": fixture_catalog.get("fixture_plan_sha256"),
            "mounting_height_sha256": _hash_json(mounting_height),
            "resource_version": VIEWER_RESOURCE_VERSION,
            "scene": "plant-layout-viewer/scene.v1.json",
            "scope": "authorized_run_natural_fit_scene",
            "ppfd_heatmap": {
                "metadata": ppfd_heatmap.metadata.filename,
                "metadata_sha256": ppfd_heatmap.metadata.sha256,
                "scalar_field": ppfd_heatmap.scalar_field.filename,
                "scalar_field_sha256": ppfd_heatmap.scalar_field.sha256,
                "source_field_identity_sha256": (
                    ppfd_heatmap.scene_reference[
                        "source_field_identity_sha256"
                    ]
                ),
            },
            "surface_flux": (
                None
                if surface_flux_display is None
                else {
                    "metadata": (
                        surface_flux_display.viewer_artifacts.metadata.filename
                    ),
                    "metadata_sha256": (
                        surface_flux_display.viewer_artifacts.metadata.sha256
                    ),
                    "patch_values": (
                        surface_flux_display.viewer_artifacts.patch_values.filename
                    ),
                    "patch_values_sha256": (
                        surface_flux_display.viewer_artifacts.patch_values.sha256
                    ),
                    **(
                        {}
                        if surface_flux_display.viewer_artifacts.calibration_coefficients
                        is None
                        else {
                            "display_calibration_coefficients": (
                                surface_flux_display.viewer_artifacts
                                .calibration_coefficients.filename
                            ),
                            "display_calibration_coefficients_sha256": (
                                surface_flux_display.viewer_artifacts
                                .calibration_coefficients.sha256
                            ),
                        }
                    ),
                }
            ),
        },
        "transport_policy": dict(science.transport_policy),
        "target_control": target_control,
        "operating_point": operating_point,
        "spatial_uniformity": spatial_report,
        "fspm_target_policy": fspm_payload,
        "scientific_identity": identities,
        "visualization": {
            "schema_id": VISUALIZATION_SCHEMA_ID,
            "field_identity_sha256": visualization.field_sha256,
            "sample_count": visualization.sample_count,
            "source": field_source,
            "reference_policy": visualization_reference.color_limit_policy,
            "physics_recomputed": False,
        },
        "visualization_inventory": visualization.inventory(),
        "runtime_provenance": runtime_provenance,
        "engine_provenance": dict(science.engine_provenance),
        "artifacts": {
            "metrics": "metrics.json",
            "ppfd_csv": "ppfd.csv",
            "target_control": "target_control.json",
            "full_output_schedule": "full_output_schedule.json",
            "operating_point": "operating-point.json",
            "natural_fit_layout": "natural_fit_layout.json",
            "baseline_leaf_position_uniformity": (
                BASELINE_LEAF_UNIFORMITY_FILENAME
            ),
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
            **dict(science.engine_artifacts),
            **(
                {}
                if surface_flux_display is None
                else {
                    "surface_flux_display_metadata": (
                        "plant-layout-viewer/"
                        + surface_flux_display.viewer_artifacts.metadata.filename
                    ),
                    "surface_flux_display_values": (
                        "plant-layout-viewer/"
                        + surface_flux_display.viewer_artifacts.patch_values.filename
                    ),
                    **(
                        {}
                        if surface_flux_display.viewer_artifacts.calibration_coefficients
                        is None
                        else {
                            "surface_flux_display_calibration": (
                                "plant-layout-viewer/"
                                + surface_flux_display.viewer_artifacts
                                .calibration_coefficients.filename
                            )
                        }
                    ),
                }
            ),
            **(
                {}
                if science.multispectral_transport is None
                else {
                    "multispectral_transport_metadata": (
                        science.multispectral_transport.metadata_artifact.path
                    ),
                    "multispectral_compact_receiver_index": (
                        science.multispectral_transport.public_artifacts[
                            "compact_receiver_index"
                        ]
                    ),
                    "multispectral_plant_origins": (
                        science.multispectral_transport.public_artifacts[
                            "plant_origins"
                        ]
                    ),
                    **{
                        key: value
                        for key, value in (
                            science.multispectral_transport.public_artifacts.items()
                        )
                        if key.startswith("band_")
                    },
                    **{
                        key: value
                        for key, value in (
                            science.multispectral_transport.scientific_aggregation
                            .public_artifacts.items()
                        )
                    },
                }
            ),
        },
        "available_visual_outputs": {
            "plant_layout_viewer": True,
            "ppfd_heatmap": True,
            "ppfd_heatmap_overlay": True,
            "ppfd_scatter": True,
            "baseline_leaf_position_uniformity": (
                baseline_leaf_uniformity.payload["available"]
            ),
            "raw_multispectral_fspm_receivers": (
                science.multispectral_transport is not None
            ),
            "fspm_surface_light_metrics": (
                science.multispectral_transport is not None
            ),
            "fspm_surface_flux_coloring": surface_flux_display is not None,
        },
    }
    _write_json(resolved_root / "metrics.json", metrics)
    _write_json(resolved_root / "manifest.json", manifest)
    return PublishedRun(metrics, manifest, visualization)


def _ppfd_csv(samples: tuple[PpfdMapSample, ...]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("x_m", "y_m", "z_m", "ppfd_umol_m2_s"))
    for sample in samples:
        writer.writerow(
            (
                format(sample.x_m, ".17g"),
                format(sample.y_m, ".17g"),
                format(sample.z_m, ".17g"),
                format(sample.ppfd_umol_m2_s, ".17g"),
            )
        )
    return stream.getvalue()


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    atomic_write_text(
        path,
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
    )


def _analysis_scope_payload(scope: AnalysisScope) -> dict[str, object]:
    return {
        "schema_id": ANALYSIS_SCOPE_SCHEMA_ID,
        "schema_version": ANALYSIS_SCOPE_SCHEMA_VERSION,
        "value": scope.value,
        "label": ANALYSIS_SCOPE_LABELS[scope],
        "description": ANALYSIS_SCOPE_DESCRIPTIONS[scope],
    }


def _logical_stages_payload(scope: AnalysisScope) -> list[dict[str, object]]:
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


__all__ = [
    "NATIVE_BASELINE_PUBLICATION_SCHEMA_VERSION",
    "PublicationError",
    "PublishedRun",
    "RunSciencePublication",
    "publish_native_baseline_run",
    "validate_emitted_ppf_contract",
]
