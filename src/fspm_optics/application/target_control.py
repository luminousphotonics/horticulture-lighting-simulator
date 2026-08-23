"""Approved Proposed full-output, dimming, and FSPM metadata policies."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np
from numpy.typing import NDArray

from fspm_optics.fixtures.proposed_cob.source import (
    ProposedSourceAuthority,
    resolve_proposed_source_authority,
)
from fspm_optics.fixtures.smd.optical_stack import (
    ACCEPTED_FIXTURE_TRANSMISSION,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    INTERNAL_SOURCE_PPE_UMOL_PER_J,
    PROPOSED_FIXTURE_OPTICAL_STACK_ID,
)
from fspm_optics.fixtures.smd.positions import SmdLayout
from fspm_optics.fixtures.smd.power_schedule import (
    DEFAULT_OPTIMIZED_MAX_WATTS_PER_MODULE,
    PROPOSED_RATED_REFERENCE_WATTS_PER_MODULE,
    ModulePowerSchedule,
    build_optimized_module_schedule,
    build_uniform_module_schedule,
)
from fspm_optics.optimization.uniformity import solve_relative_uniformity

FloatArray = NDArray[np.float64]
FSPM_REFERENCE_POLICY_SCHEMA_ID = "fspm-optics.fspm-reference-policy"
FSPM_REFERENCE_POLICY_SCHEMA_VERSION = 2
TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S = 1e-6
MEAN_TARGET_MODE = "mean_target"
TARGET_CAPPED_MODE = "target_capped"


class TargetControlError(RuntimeError):
    """A valid native basis could not produce the approved control result."""


@dataclass(frozen=True, slots=True)
class FullOutputSchedule:
    schedule: ModulePowerSchedule
    full_output_field: tuple[float, ...]
    full_output_mean_ppfd: float
    full_output_standard_deviation_ppfd: float
    full_output_cv_percent: float
    relative_coefficients: tuple[float, ...]
    declared_max_watts_per_module: float
    full_output_power_w: float
    full_output_internal_par_ppf_umol_s: float
    full_output_modeled_completed_aperture_par_ppf_umol_s: float
    internal_source_ppe_umol_per_j: float
    completed_aperture_fixture_ppe_umol_per_j: float
    accepted_fixture_transmission: float
    optical_stack_id: str


@dataclass(frozen=True, slots=True)
class TargetControlResult:
    lighting_target_mode: str
    requested_target_ppfd: float
    full_output_mean_ppfd: float
    full_output_maximum_ppfd: float
    achieved_field: tuple[float, ...]
    achieved_mean_ppfd: float
    achieved_maximum_ppfd: float
    achieved_standard_deviation_ppfd: float
    achieved_cv_percent: float
    raw_factor: float
    dimming_factor: float
    feasible: bool
    infeasibility: dict[str, object] | None
    cap_binding: bool | None
    cap_compliant: bool | None
    compliance_tolerance_umol_m2_s: float
    limiting_sample_index: int
    full_output_power_w: float
    effective_power_w: float
    full_output_internal_par_ppf_umol_s: float
    effective_internal_par_ppf_umol_s: float
    full_output_modeled_completed_aperture_par_ppf_umol_s: float
    effective_modeled_completed_aperture_par_ppf_umol_s: float


@dataclass(frozen=True, slots=True)
class FspmTargetPolicy:
    mode: str
    override_umol_m2_s: float | None
    resolved_target_umol_m2_s: float
    tolerance_umol_m2_s: float
    inclusive_lower_umol_m2_s: float
    inclusive_upper_umol_m2_s: float
    affects_baseline_transport: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_id": FSPM_REFERENCE_POLICY_SCHEMA_ID,
            "schema_version": FSPM_REFERENCE_POLICY_SCHEMA_VERSION,
            "mode": self.mode,
            "override_umol_m2_s": self.override_umol_m2_s,
            "resolved_target_umol_m2_s": self.resolved_target_umol_m2_s,
            "tolerance_umol_m2_s": self.tolerance_umol_m2_s,
            "classification_range": {
                "lower_umol_m2_s": self.inclusive_lower_umol_m2_s,
                "upper_umol_m2_s": self.inclusive_upper_umol_m2_s,
                "bounds": "inclusive",
            },
            "policy_scope": "metadata_and_classification_only",
            "affects_baseline_transport": self.affects_baseline_transport,
            "reference_resolution_executes_transport": False,
        }


@dataclass(frozen=True, slots=True)
class GlobalSourceDimmingPolicy:
    """One capped factor derived from a target-independent full-output field."""

    lighting_target_mode: str
    requested_target_ppfd: float
    full_output_mean_ppfd: float
    full_output_maximum_ppfd: float
    raw_factor: float
    dimming_factor: float
    feasible: bool
    infeasibility: dict[str, object] | None
    cap_binding: bool | None
    compliance_tolerance_umol_m2_s: float


def resolve_global_source_dimming(
    *,
    requested_target_ppfd: float,
    full_output_mean_ppfd: float,
    full_output_maximum_ppfd: float | None = None,
    lighting_target_mode: str = MEAN_TARGET_MODE,
    system_id: str,
) -> GlobalSourceDimmingPolicy:
    """Resolve only the source factor; never transform a traced sample field."""

    requested = _nonnegative(
        "requested_target_ppfd", requested_target_ppfd
    )
    full_mean = _positive("full_output_mean_ppfd", full_output_mean_ppfd)
    full_maximum = _positive(
        "full_output_maximum_ppfd",
        full_mean if full_output_maximum_ppfd is None else full_output_maximum_ppfd,
    )
    mode = _lighting_target_mode(lighting_target_mode)
    if not isinstance(system_id, str) or not system_id:
        raise ValueError("system_id must be non-empty.")
    denominator = full_mean if mode == MEAN_TARGET_MODE else full_maximum
    raw_factor = requested / denominator
    dimming_factor = min(raw_factor, 1.0)
    feasible = mode == TARGET_CAPPED_MODE or raw_factor <= 1.0
    infeasibility = None
    if mode == MEAN_TARGET_MODE and not feasible:
        infeasibility = {
            "code": "requested_target_exceeds_full_output_mean",
            "system_id": system_id,
            "message": "Requested target exceeds the system full-output mean.",
            "requested_target_ppfd": requested,
            "maximum_achievable_mean_ppfd": full_mean,
        }
    return GlobalSourceDimmingPolicy(
        lighting_target_mode=mode,
        requested_target_ppfd=requested,
        full_output_mean_ppfd=full_mean,
        full_output_maximum_ppfd=full_maximum,
        raw_factor=raw_factor,
        dimming_factor=dimming_factor,
        feasible=feasible,
        infeasibility=infeasibility,
        cap_binding=(raw_factor < 1.0 if mode == TARGET_CAPPED_MODE else None),
        compliance_tolerance_umol_m2_s=(
            TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S
        ),
    )


def scale_global_output_field_float64(
    full_output_field: Sequence[float] | FloatArray,
    global_output_fraction: float,
) -> tuple[float, ...]:
    """Apply one authoritative global fraction with explicit Float64 arithmetic."""

    if (
        isinstance(global_output_fraction, bool)
        or not isinstance(global_output_fraction, int | float)
    ):
        raise ValueError(
            "global_output_fraction must be finite and in the closed unit interval."
        )
    factor = float(global_output_fraction)
    if not math.isfinite(factor) or not 0.0 <= factor <= 1.0:
        raise ValueError(
            "global_output_fraction must be finite and in the closed unit interval."
        )
    field = np.asarray(full_output_field, dtype=np.float64)
    if (
        field.ndim != 1
        or field.size == 0
        or not np.all(np.isfinite(field))
        or np.any(field < 0.0)
    ):
        raise TargetControlError(
            "full-output field must be a finite non-negative vector."
        )
    scaled = np.multiply(field, np.float64(factor), dtype=np.float64)
    if not np.all(np.isfinite(scaled)) or np.any(scaled < 0.0):
        raise TargetControlError("scaled global-output field is invalid.")
    return tuple(float(value) for value in scaled)


def derive_full_output_schedule(
    normalized_basis_ppfd_per_watt: Sequence[Sequence[float]] | FloatArray,
    layout: SmdLayout,
    *,
    max_watts_per_module: float = DEFAULT_OPTIMIZED_MAX_WATTS_PER_MODULE,
    source_authority: ProposedSourceAuthority | None = None,
) -> FullOutputSchedule:
    """Derive the target-independent relative schedule and normalize to max W."""

    maximum_watts = _positive("max_watts_per_module", max_watts_per_module)
    source = source_authority or resolve_proposed_source_authority()
    matrix = np.asarray(normalized_basis_ppfd_per_watt, dtype=float)
    relative = solve_relative_uniformity(matrix)
    if not relative.success:
        raise TargetControlError(
            relative.failure_reason or "relative uniformity solve failed."
        )
    if len(relative.coefficients) != layout.control_zone_count:
        raise TargetControlError(
            "relative coefficient count does not match the Proposed layout."
        )
    watts = tuple(value * maximum_watts for value in relative.coefficients)
    schedule = build_optimized_module_schedule(layout, watts)
    if not math.isclose(schedule.max_watts, maximum_watts, abs_tol=1e-9):
        raise TargetControlError(
            "full-output schedule did not normalize its highest zone to the declared maximum."
        )
    field = np.asarray(matrix @ np.asarray(watts, dtype=float), dtype=float)
    if field.ndim != 1 or not np.all(np.isfinite(field)) or np.any(field < 0.0):
        raise TargetControlError("full-output field is invalid.")
    mean = float(field.mean())
    if not math.isfinite(mean) or mean <= 0.0:
        raise TargetControlError("full-output mean PPFD must be finite and positive.")
    standard_deviation = float(field.std(ddof=0))
    total_power = schedule.total_watts
    return FullOutputSchedule(
        schedule=schedule,
        full_output_field=tuple(float(value) for value in field),
        full_output_mean_ppfd=mean,
        full_output_standard_deviation_ppfd=standard_deviation,
        full_output_cv_percent=100.0 * standard_deviation / mean,
        relative_coefficients=relative.coefficients,
        declared_max_watts_per_module=maximum_watts,
        full_output_power_w=total_power,
        full_output_internal_par_ppf_umol_s=(
            total_power * INTERNAL_SOURCE_PPE_UMOL_PER_J
            if source_authority is None
            else total_power * source.internal_source_ppe_umol_per_j
        ),
        full_output_modeled_completed_aperture_par_ppf_umol_s=(
            total_power * COMPLETED_APERTURE_PPE_UMOL_PER_J
        ),
        internal_source_ppe_umol_per_j=source.internal_source_ppe_umol_per_j,
        completed_aperture_fixture_ppe_umol_per_j=(
            COMPLETED_APERTURE_PPE_UMOL_PER_J
        ),
        accepted_fixture_transmission=source.completed_aperture_transmission,
        optical_stack_id=PROPOSED_FIXTURE_OPTICAL_STACK_ID,
    )


def derive_uniform_full_output_schedule(
    reference_field: Sequence[float] | FloatArray,
    layout: SmdLayout,
    *,
    reference_watts_per_module: float = PROPOSED_RATED_REFERENCE_WATTS_PER_MODULE,
    source_authority: ProposedSourceAuthority | None = None,
) -> FullOutputSchedule:
    """Describe one complete equal-module reference trace without optimization."""

    reference_watts = _positive(
        "reference_watts_per_module", reference_watts_per_module
    )
    source = source_authority or resolve_proposed_source_authority()
    field = np.asarray(reference_field, dtype=float)
    if (
        field.ndim != 1
        or field.size == 0
        or not np.all(np.isfinite(field))
        or np.any(field < 0.0)
    ):
        raise TargetControlError("uniform reference field is invalid.")
    mean = float(field.mean())
    if not math.isfinite(mean) or mean <= 0.0:
        raise TargetControlError(
            "uniform reference field mean PPFD must be finite and positive."
        )
    schedule = build_uniform_module_schedule(layout, reference_watts)
    standard_deviation = float(field.std(ddof=0))
    total_power = schedule.total_watts
    return FullOutputSchedule(
        schedule=schedule,
        full_output_field=tuple(float(value) for value in field),
        full_output_mean_ppfd=mean,
        full_output_standard_deviation_ppfd=standard_deviation,
        full_output_cv_percent=100.0 * standard_deviation / mean,
        relative_coefficients=tuple(1.0 for _ in layout.control_zone_indices),
        declared_max_watts_per_module=reference_watts,
        full_output_power_w=total_power,
        full_output_internal_par_ppf_umol_s=(
            total_power * INTERNAL_SOURCE_PPE_UMOL_PER_J
            if source_authority is None
            else total_power * source.internal_source_ppe_umol_per_j
        ),
        full_output_modeled_completed_aperture_par_ppf_umol_s=(
            total_power * COMPLETED_APERTURE_PPE_UMOL_PER_J
        ),
        internal_source_ppe_umol_per_j=source.internal_source_ppe_umol_per_j,
        completed_aperture_fixture_ppe_umol_per_j=(
            COMPLETED_APERTURE_PPE_UMOL_PER_J
        ),
        accepted_fixture_transmission=source.completed_aperture_transmission,
        optical_stack_id=PROPOSED_FIXTURE_OPTICAL_STACK_ID,
    )


def apply_target_control(
    full_output: FullOutputSchedule,
    requested_target_ppfd: float,
    lighting_target_mode: str = MEAN_TARGET_MODE,
) -> TargetControlResult:
    """Apply one non-amplifying global factor to schedule-derived field and power."""

    requested = _positive("requested_target_ppfd", requested_target_ppfd)
    full_mean = _positive(
        "full_output_mean_ppfd", full_output.full_output_mean_ppfd
    )
    full_field = np.asarray(full_output.full_output_field, dtype=float)
    full_maximum = _positive("full_output_maximum_ppfd", float(full_field.max()))
    mode = _lighting_target_mode(lighting_target_mode)
    sampled_cap_policy = None
    if mode == TARGET_CAPPED_MODE:
        # Keep live Proposed control and authenticated playback on the same
        # sampled-cap factor authority.
        sampled_cap_policy = resolve_global_source_dimming(
            requested_target_ppfd=requested,
            full_output_mean_ppfd=full_mean,
            full_output_maximum_ppfd=full_maximum,
            lighting_target_mode=mode,
            system_id="proposed",
        )
        raw_factor = sampled_cap_policy.raw_factor
        dimming_factor = sampled_cap_policy.dimming_factor
    else:
        raw_factor = requested / full_mean
        dimming_factor = min(raw_factor, 1.0)
    achieved = full_field * dimming_factor
    achieved_mean = float(achieved.mean())
    achieved_maximum = float(achieved.max())
    standard_deviation = float(achieved.std(ddof=0))
    feasible = mode == TARGET_CAPPED_MODE or raw_factor <= 1.0
    infeasibility = None
    if mode == MEAN_TARGET_MODE and not feasible:
        infeasibility = {
            "code": "requested_target_exceeds_full_output_mean",
            "message": "Requested target exceeds the Proposed full-output mean.",
            "requested_target_ppfd": requested,
            "maximum_achievable_mean_ppfd": full_mean,
        }
    return TargetControlResult(
        lighting_target_mode=mode,
        requested_target_ppfd=requested,
        full_output_mean_ppfd=full_mean,
        full_output_maximum_ppfd=full_maximum,
        achieved_field=tuple(float(value) for value in achieved),
        achieved_mean_ppfd=achieved_mean,
        achieved_maximum_ppfd=achieved_maximum,
        achieved_standard_deviation_ppfd=standard_deviation,
        achieved_cv_percent=100.0 * standard_deviation / achieved_mean,
        raw_factor=raw_factor,
        dimming_factor=dimming_factor,
        feasible=feasible,
        infeasibility=infeasibility,
        cap_binding=(
            sampled_cap_policy.cap_binding
            if sampled_cap_policy is not None
            else None
        ),
        cap_compliant=(
            achieved_maximum
            <= requested + TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S
            if mode == TARGET_CAPPED_MODE
            else None
        ),
        compliance_tolerance_umol_m2_s=(
            TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S
        ),
        limiting_sample_index=int(np.argmax(achieved)),
        full_output_power_w=full_output.full_output_power_w,
        effective_power_w=full_output.full_output_power_w * dimming_factor,
        full_output_internal_par_ppf_umol_s=(
            full_output.full_output_internal_par_ppf_umol_s
        ),
        effective_internal_par_ppf_umol_s=(
            full_output.full_output_internal_par_ppf_umol_s * dimming_factor
        ),
        full_output_modeled_completed_aperture_par_ppf_umol_s=(
            full_output.full_output_modeled_completed_aperture_par_ppf_umol_s
        ),
        effective_modeled_completed_aperture_par_ppf_umol_s=(
            full_output.full_output_modeled_completed_aperture_par_ppf_umol_s
            * dimming_factor
        ),
    )


def resolve_fspm_target_policy(
    *,
    mode: str,
    achieved_baseline_mean_ppfd: float,
    tolerance_umol_m2_s: float,
    override_umol_m2_s: float | None = None,
) -> FspmTargetPolicy:
    """Resolve automatic/override FSPM metadata after baseline completion."""

    achieved = _positive(
        "achieved_baseline_mean_ppfd", achieved_baseline_mean_ppfd
    )
    tolerance = _positive("tolerance_umol_m2_s", tolerance_umol_m2_s)
    normalized_mode = str(mode).strip().lower()
    if normalized_mode == "automatic":
        if override_umol_m2_s is not None:
            raise ValueError("automatic FSPM mode must not include an override.")
        resolved = achieved
        override = None
    elif normalized_mode == "override":
        if override_umol_m2_s is None:
            raise ValueError("override FSPM mode requires a positive target.")
        override = _positive("override_umol_m2_s", override_umol_m2_s)
        resolved = override
    else:
        raise ValueError("mode must be automatic or override.")
    return FspmTargetPolicy(
        mode=normalized_mode,
        override_umol_m2_s=override,
        resolved_target_umol_m2_s=resolved,
        tolerance_umol_m2_s=tolerance,
        inclusive_lower_umol_m2_s=max(0.0, resolved - tolerance),
        inclusive_upper_umol_m2_s=resolved + tolerance,
    )


def _positive(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be finite and positive.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive.")
    return number


def _nonnegative(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be finite and non-negative.")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be finite and non-negative.")
    return 0.0 if number == 0.0 else number


def _lighting_target_mode(value: object) -> str:
    mode = str(value)
    if mode not in {MEAN_TARGET_MODE, TARGET_CAPPED_MODE}:
        raise ValueError(
            "lighting_target_mode must be mean_target or target_capped."
        )
    return mode
