"""Deterministic linear target adjustment over authenticated 250-PPFD bundles.

The compact bundle is immutable input authority.  This module constructs an
in-memory playback derivative for dimmable LED systems; it never creates a new
sweep case, changes a bundle identity, or runs transport.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import csv
import hashlib
import io
import json
import math
import struct
from typing import Mapping, Sequence

from fspm_optics.application.baseline_leaf_uniformity import (
    BaselineLeafTargetPolicy,
    aggregate_leaf_position_ppfd,
    classify_baseline_leaf_ppfd,
)
from fspm_optics.application.domain import (
    CONVENTIONAL_SYSTEM_ID,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
)
from fspm_optics.application.target_control import (
    TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S,
    TARGET_CAPPED_MODE,
    resolve_global_source_dimming,
)
from fspm_optics.application.visualization import (
    VisualizationReference,
    build_ppfd_visualization_artifacts,
)
from fspm_optics.layout.overlay import (
    AuthoritativeOverlayPlan,
    OverlayLine,
    OverlayRectangle,
)
from fspm_optics.fixtures.conventional_led import (
    conservative_conventional_global_dimming_factor,
)
from fspm_optics.precomputed.compact_bundle import (
    PUBLIC_RESULT_SCHEMA_ID,
    PUBLIC_RESULT_SCHEMA_VERSION,
    CompactPlayback,
)
from fspm_optics.precomputed.contracts import (
    FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S,
)
from fspm_optics.transport.scalar_ppfd import PpfdMapSample
from fspm_optics.viewer.ppfd_heatmap import bind_ppfd_heatmap_viewer_artifacts


TARGET_ADJUSTMENT_SCHEMA_ID = "fspm-optics.precomputed-linear-target-adjustment"
TARGET_ADJUSTMENT_SCHEMA_VERSION = 1
TARGET_ADJUSTMENT_CAPABILITY = "linear_target_ppfd_adjustment_v1"
TARGET_ADJUSTMENT_LIMIT_REASON = "maximum_output"
TARGET_CAPPED_ADJUSTMENT_SCHEMA_ID = (
    "fspm-optics.precomputed-authenticated-target-capped-playback"
)
TARGET_CAPPED_ADJUSTMENT_SCHEMA_VERSION = 1
TARGET_CAPPED_ADJUSTMENT_CAPABILITY = "authenticated_target_capped_playback_v1"
FSPM_TOLERANCE_PRESENTATION_SCHEMA_ID = (
    "fspm-optics.precomputed-fspm-tolerance-presentation"
)
FSPM_TOLERANCE_PRESENTATION_SCHEMA_VERSION = 1
DEFAULT_PRECOMPUTED_FSPM_TARGET_TOLERANCE_UMOL_M2_S = 75.0
TARGET_CAPPED_CONTROL_CONTRACT = {
    "schema_id": "fspm-optics.sampled-stage-a-cap-control",
    "schema_version": 1,
    "requested_value_semantics": "requested_sampled_stage_a_maximum_ppfd",
    "full_output_authority": "authenticated_compact_bundle_target_control",
    "controller": "live_global_source_dimming",
    "conventional_compatibility": (
        "conservative_historical_ies2rad_%g_six_significant_digits"
    ),
    "scaling_arithmetic": "IEEE 754 binary64",
    "cap_compliance_tolerance_umol_m2_s": (
        TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S
    ),
}
TARGET_CAPPED_CONTROL_CONTRACT_IDENTITY_SHA256 = hashlib.sha256(
    json.dumps(
        TARGET_CAPPED_CONTROL_CONTRACT,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
).hexdigest()
ZERO_OUTPUT_RATIO_CONVENTION = (
    "zero_for_spatial_uniformity_ratios; unavailable_null_for_leaf_position_cv"
)

_LED_SYSTEMS = frozenset((PROPOSED_SYSTEM_ID, CONVENTIONAL_SYSTEM_ID))


@dataclass(frozen=True, slots=True)
class TargetAdjustment:
    """Complete deterministic control metadata for one derived playback."""

    base_bundle_identity_sha256: str
    base_target_ppfd_umol_m2_s: float
    base_achieved_mean_ppfd_umol_m2_s: float
    base_output_fraction: float
    requested_target_ppfd_umol_m2_s: float
    maximum_output_fraction: float
    maximum_achievable_ppfd_umol_m2_s: float
    desired_scale: float
    maximum_scale: float
    applied_scale: float
    output_fraction: float
    actual_achieved_mean_ppfd_umol_m2_s: float
    output_limited: bool
    limit_reason: str | None
    exact_base_target_reuse: bool
    derived_playback_identity_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_id": TARGET_ADJUSTMENT_SCHEMA_ID,
            "schema_version": TARGET_ADJUSTMENT_SCHEMA_VERSION,
            "base_bundle_identity_sha256": self.base_bundle_identity_sha256,
            "base_target_ppfd_umol_m2_s": self.base_target_ppfd_umol_m2_s,
            "base_achieved_mean_ppfd_umol_m2_s": (
                self.base_achieved_mean_ppfd_umol_m2_s
            ),
            "base_output_fraction": self.base_output_fraction,
            "requested_target_ppfd_umol_m2_s": (
                self.requested_target_ppfd_umol_m2_s
            ),
            "maximum_output_fraction": self.maximum_output_fraction,
            "maximum_achievable_ppfd_umol_m2_s": (
                self.maximum_achievable_ppfd_umol_m2_s
            ),
            "desired_scale": self.desired_scale,
            "maximum_scale": self.maximum_scale,
            "applied_scale": self.applied_scale,
            "output_fraction": self.output_fraction,
            "actual_achieved_mean_ppfd_umol_m2_s": (
                self.actual_achieved_mean_ppfd_umol_m2_s
            ),
            "output_limited": self.output_limited,
            "limit_reason": self.limit_reason,
            "exact_base_target_reuse": self.exact_base_target_reuse,
            "zero_output_ratio_convention": ZERO_OUTPUT_RATIO_CONVENTION,
            "derived_playback_identity_sha256": (
                self.derived_playback_identity_sha256
            ),
        }


@dataclass(frozen=True, slots=True)
class TargetCappedAdjustment:
    """Versioned sampled-cap derivative over one authenticated Mean base."""

    base_bundle_identity_sha256: str
    fixed_case_binding_identity_sha256: str
    target_control_contract_identity_sha256: str
    canonical_domain_identity_sha256: str
    presentation_identity_sha256: str
    base_target_ppfd_umol_m2_s: float
    base_achieved_mean_ppfd_umol_m2_s: float
    base_achieved_maximum_ppfd_umol_m2_s: float
    base_output_fraction: float
    full_output_mean_ppfd_umol_m2_s: float
    full_output_maximum_ppfd_umol_m2_s: float
    requested_cap_ppfd_umol_m2_s: float
    maximum_output_fraction: float
    controller_raw_factor: float
    capped_factor_before_native_quantization: float
    output_fraction: float
    intensity_scale: float
    actual_achieved_mean_ppfd_umol_m2_s: float
    actual_achieved_maximum_ppfd_umol_m2_s: float
    cap_binding: bool
    cap_compliant: bool
    maximum_output_saturated: bool
    derived_playback_identity_sha256: str

    @property
    def requested_target_ppfd_umol_m2_s(self) -> float:
        return self.requested_cap_ppfd_umol_m2_s

    @property
    def applied_scale(self) -> float:
        return self.intensity_scale

    @property
    def desired_scale(self) -> float:
        return self.controller_raw_factor / self.base_output_fraction

    @property
    def maximum_scale(self) -> float:
        return self.maximum_output_fraction / self.base_output_fraction

    @property
    def maximum_achievable_ppfd_umol_m2_s(self) -> float:
        return self.full_output_maximum_ppfd_umol_m2_s

    @property
    def output_limited(self) -> bool:
        # Live sampled-cap control treats a cap above capacity as feasible full
        # output, not as an infeasible requested-mean target.
        return False

    @property
    def limit_reason(self) -> None:
        return None

    @property
    def exact_base_target_reuse(self) -> bool:
        return False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_id": TARGET_CAPPED_ADJUSTMENT_SCHEMA_ID,
            "schema_version": TARGET_CAPPED_ADJUSTMENT_SCHEMA_VERSION,
            "base_bundle_identity_sha256": self.base_bundle_identity_sha256,
            "fixed_case_binding_identity_sha256": (
                self.fixed_case_binding_identity_sha256
            ),
            "target_control_contract_identity_sha256": (
                self.target_control_contract_identity_sha256
            ),
            "target_control_contract": dict(TARGET_CAPPED_CONTROL_CONTRACT),
            "lighting_target_mode": TARGET_CAPPED_MODE,
            "requested_value_semantics": (
                "requested_sampled_stage_a_maximum_ppfd"
            ),
            "canonical_domain_identity_sha256": (
                self.canonical_domain_identity_sha256
            ),
            "presentation_identity_sha256": self.presentation_identity_sha256,
            "public_result_schema": {
                "schema_id": PUBLIC_RESULT_SCHEMA_ID,
                "schema_version": PUBLIC_RESULT_SCHEMA_VERSION,
            },
            "base_target_ppfd_umol_m2_s": self.base_target_ppfd_umol_m2_s,
            "base_achieved_mean_ppfd_umol_m2_s": (
                self.base_achieved_mean_ppfd_umol_m2_s
            ),
            "base_achieved_maximum_ppfd_umol_m2_s": (
                self.base_achieved_maximum_ppfd_umol_m2_s
            ),
            "base_output_fraction": self.base_output_fraction,
            "full_output_mean_ppfd_umol_m2_s": (
                self.full_output_mean_ppfd_umol_m2_s
            ),
            "full_output_maximum_ppfd_umol_m2_s": (
                self.full_output_maximum_ppfd_umol_m2_s
            ),
            "requested_cap_ppfd_umol_m2_s": self.requested_cap_ppfd_umol_m2_s,
            "maximum_output_fraction": self.maximum_output_fraction,
            "controller_raw_factor": self.controller_raw_factor,
            "capped_factor_before_native_quantization": (
                self.capped_factor_before_native_quantization
            ),
            "output_fraction": self.output_fraction,
            "intensity_scale": self.intensity_scale,
            "actual_achieved_mean_ppfd_umol_m2_s": (
                self.actual_achieved_mean_ppfd_umol_m2_s
            ),
            "actual_achieved_maximum_ppfd_umol_m2_s": (
                self.actual_achieved_maximum_ppfd_umol_m2_s
            ),
            "feasible": True,
            "infeasibility": None,
            "cap_binding": self.cap_binding,
            "cap_compliant": self.cap_compliant,
            "compliance_tolerance_umol_m2_s": (
                TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S
            ),
            "maximum_output_saturated": self.maximum_output_saturated,
            "exact_base_target_reuse": False,
            "zero_output_ratio_convention": ZERO_OUTPUT_RATIO_CONVENTION,
            "derived_playback_identity_sha256": (
                self.derived_playback_identity_sha256
            ),
        }


@dataclass(frozen=True, slots=True)
class TargetAdjustedCompactPlayback:
    """Standalone canonical playback derived from one validated base bundle."""

    base: CompactPlayback
    adjustment: TargetAdjustment | TargetCappedAdjustment
    public_payload: Mapping[str, object]
    payloads: Mapping[str, bytes]
    samples: tuple[PpfdMapSample, ...]

    @property
    def bundle_path(self):  # type annotation follows the base contract
        return self.base.bundle_path

    @property
    def manifest(self) -> Mapping[str, object]:
        """Return the unchanged authenticated base manifest."""

        return self.base.manifest

    @property
    def run_id(self) -> str:
        return self.base.run_id

    @property
    def system_id(self) -> str:
        return self.base.system_id

    @property
    def metrics(self) -> Mapping[str, object]:
        value = self.public_payload["metrics"]
        assert isinstance(value, Mapping)
        return value

    @property
    def visualization(self) -> Mapping[str, object]:
        return _json_object(self.payloads["visualization_metadata"])

    @property
    def viewer_scene(self) -> Mapping[str, object]:
        return _json_object(self.payloads["viewer_scene"])

    @property
    def derived_playback_identity_sha256(self) -> str:
        return self.adjustment.derived_playback_identity_sha256

    def target_adjustment_metadata(self) -> dict[str, object]:
        return self.adjustment.to_dict()

    def final_baseline_ppfd_csv(self) -> tuple[str, str, bytes]:
        if self.samples is self.base.samples:
            return self.base.final_baseline_ppfd_csv()
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("x_m", "y_m", "z_m", "ppfd_umol_m2_s"))
        for sample in self.samples:
            writer.writerow(
                tuple(
                    format(value, ".17g")
                    for value in (
                        sample.x_m,
                        sample.y_m,
                        sample.z_m,
                        sample.ppfd_umol_m2_s,
                    )
                )
            )
        return (
            "ppfd.csv",
            "text/csv; charset=utf-8",
            stream.getvalue().encode("utf-8"),
        )

    def validated_ppfd_scatter(self) -> tuple[Mapping[str, object], bytes]:
        if self.samples is self.base.samples:
            return self.base.validated_ppfd_scatter()
        return self.visualization, b"".join(
            struct.pack(
                "<fff",
                float(sample.x_m),
                float(sample.y_m),
                float(sample.ppfd_umol_m2_s),
            )
            for sample in self.samples
        )

    def result_payload(self) -> dict[str, object]:
        result = self.base.result_payload()
        result.update(
            {
                "capabilities": list(self.public_payload["capabilities"]),
                "lighting_target_mode": (
                    TARGET_CAPPED_MODE
                    if isinstance(self.adjustment, TargetCappedAdjustment)
                    else "mean_target"
                ),
                "target_adjustment": self.adjustment.to_dict(),
                "derived_playback_identity_sha256": (
                    self.derived_playback_identity_sha256
                ),
            }
        )
        for key in (
            "proposed_control",
            "proposed_layout",
            "proposed_source",
            "spectral_basis",
        ):
            if self.metrics.get(key) is not None:
                result[key] = self.metrics[key]
        return result


@dataclass(frozen=True, slots=True)
class FspmTolerancePresentation:
    """Classification-only view anchored to an authenticated playback source."""

    source: CompactPlayback | TargetAdjustedCompactPlayback
    fspm_target_tolerance_umol_m2_s: float
    fspm_tolerance_derivation_identity_sha256: str
    public_payload: Mapping[str, object]
    payloads: Mapping[str, bytes]

    @property
    def bundle_path(self):  # type annotation follows the source contract
        return self.source.bundle_path

    @property
    def manifest(self) -> Mapping[str, object]:
        return self.source.manifest

    @property
    def run_id(self) -> str:
        return self.source.run_id

    @property
    def system_id(self) -> str:
        return self.source.system_id

    @property
    def metrics(self) -> Mapping[str, object]:
        value = self.public_payload["metrics"]
        assert isinstance(value, Mapping)
        return value

    @property
    def visualization(self) -> Mapping[str, object]:
        return _json_object(self.payloads["visualization_metadata"])

    @property
    def viewer_scene(self) -> Mapping[str, object]:
        return _json_object(self.payloads["viewer_scene"])

    @property
    def samples(self) -> tuple[PpfdMapSample, ...]:
        return self.source.samples

    @property
    def base(self) -> CompactPlayback:
        value = getattr(self.source, "base", self.source)
        assert isinstance(value, CompactPlayback)
        return value

    @property
    def adjustment(self) -> TargetAdjustment | TargetCappedAdjustment | None:
        value = getattr(self.source, "adjustment", None)
        if isinstance(value, (TargetAdjustment, TargetCappedAdjustment)):
            return value
        return None

    @property
    def derived_playback_identity_sha256(self) -> str | None:
        value = getattr(self.source, "derived_playback_identity_sha256", None)
        return None if value is None else str(value)

    def tolerance_presentation_metadata(self) -> dict[str, object]:
        return {
            "schema_id": FSPM_TOLERANCE_PRESENTATION_SCHEMA_ID,
            "schema_version": FSPM_TOLERANCE_PRESENTATION_SCHEMA_VERSION,
            "authenticated_bundle_identity_sha256": str(
                self.manifest["bundle_identity_sha256"]
            ),
            "source_derived_playback_identity_sha256": (
                self.derived_playback_identity_sha256
            ),
            "fspm_target_tolerance_umol_m2_s": (
                self.fspm_target_tolerance_umol_m2_s
            ),
            "classification_metadata_only": True,
            "stage_a_transport_recomputed": False,
            "derivation_identity_sha256": (
                self.fspm_tolerance_derivation_identity_sha256
            ),
        }

    def target_adjustment_metadata(self) -> dict[str, object]:
        if self.adjustment is None:
            raise AttributeError("playback has no target adjustment")
        return self.adjustment.to_dict()

    def final_baseline_ppfd_csv(self) -> tuple[str, str, bytes]:
        return self.source.final_baseline_ppfd_csv()

    def validated_ppfd_scatter(self) -> tuple[Mapping[str, object], bytes]:
        return self.source.validated_ppfd_scatter()

    def result_payload(self) -> dict[str, object]:
        result = self.source.result_payload()
        result["fspm_target_tolerance"] = self.fspm_target_tolerance_umol_m2_s
        result["fspm_tolerance_presentation"] = (
            self.tolerance_presentation_metadata()
        )
        return result


def validate_requested_target_ppfd(value: object) -> float:
    """Accept exactly finite, non-negative, non-Boolean numeric targets."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(
            "precomputed target PPFD must be a finite non-negative number."
        )
    target = float(value)
    if not math.isfinite(target) or target < 0.0:
        raise ValueError(
            "precomputed target PPFD must be a finite non-negative number."
        )
    return 0.0 if target == 0.0 else target


def validate_fspm_target_tolerance(value: object) -> float:
    """Accept exactly finite, strictly positive, non-Boolean tolerances."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(
            "FSPM target tolerance must be a finite strictly positive number."
        )
    tolerance = float(value)
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        raise ValueError(
            "FSPM target tolerance must be a finite strictly positive number."
        )
    return tolerance


def derive_fspm_tolerance_presentation(
    source: (
        CompactPlayback
        | TargetAdjustedCompactPlayback
        | FspmTolerancePresentation
    ),
    requested_tolerance_umol_m2_s: object = (
        DEFAULT_PRECOMPUTED_FSPM_TARGET_TOLERANCE_UMOL_M2_S
    ),
) -> CompactPlayback | TargetAdjustedCompactPlayback | FspmTolerancePresentation:
    """Derive tolerance-dependent presentation data without changing Stage A."""

    tolerance = validate_fspm_target_tolerance(
        requested_tolerance_umol_m2_s
    )
    if isinstance(source, FspmTolerancePresentation):
        if source.fspm_target_tolerance_umol_m2_s == tolerance:
            return source
        source = source.source
    if not isinstance(source, (CompactPlayback, TargetAdjustedCompactPlayback)):
        raise TypeError(
            "FSPM tolerance presentation requires validated compact playback."
        )
    if tolerance == DEFAULT_PRECOMPUTED_FSPM_TARGET_TOLERANCE_UMOL_M2_S:
        return source

    source_baseline = _json_object(source.payloads["baseline_leaf_uniformity"])
    source_derived_identity = getattr(
        source, "derived_playback_identity_sha256", None
    )
    identity_preimage = {
        "schema_id": FSPM_TOLERANCE_PRESENTATION_SCHEMA_ID,
        "schema_version": FSPM_TOLERANCE_PRESENTATION_SCHEMA_VERSION,
        "authenticated_bundle_identity_sha256": str(
            source.manifest["bundle_identity_sha256"]
        ),
        "source_derived_playback_identity_sha256": source_derived_identity,
        "source_baseline_leaf_derivation_identity_sha256": (
            source_baseline.get("derivation_identity_sha256")
        ),
        "fspm_target_tolerance_umol_m2_s": tolerance,
        "derivation_scope": (
            "classification_ranges_leaf_categories_target_coverage_and_metadata"
        ),
        "stage_a_transport_recomputed": False,
    }
    derivation_identity = _hash_json(identity_preimage)

    baseline_leaf = _derive_tolerance_baseline_leaf(
        source_baseline,
        tolerance=tolerance,
        tolerance_derivation_identity_sha256=derivation_identity,
    )
    baseline_bytes = _pretty_json_bytes(baseline_leaf)

    metrics = deepcopy(dict(source.metrics))
    fspm_policy = metrics.get("fspm_target_policy")
    if not isinstance(fspm_policy, dict):
        raise ValueError("FSPM target policy is missing from playback metrics.")
    _set_fspm_policy_tolerance(fspm_policy, tolerance)
    metrics["baseline_leaf_position_uniformity"] = (
        _baseline_leaf_metrics_payload(baseline_leaf, baseline_bytes)
    )
    metrics["fspm_tolerance_presentation"] = {
        **identity_preimage,
        "derivation_identity_sha256": derivation_identity,
    }

    scene = deepcopy(dict(source.viewer_scene))
    heatmap = scene.get("ppfd_heatmap")
    if not isinstance(heatmap, dict):
        raise ValueError("viewer PPFD heatmap contract is missing.")
    coverage = heatmap.get("target_coverage")
    if not isinstance(coverage, dict):
        raise ValueError("viewer Target Coverage contract is missing.")
    coverage["tolerance_ppfd_umol_m2_s"] = tolerance
    run = scene.get("run")
    if isinstance(run, dict):
        information = run.get("information")
        if isinstance(information, dict):
            scene_policy = information.get("fspm_target_policy")
            if isinstance(scene_policy, dict):
                _set_fspm_policy_tolerance(scene_policy, tolerance)

    public = deepcopy(dict(source.public_payload))
    public["metrics"] = metrics
    result_metadata = public.get("result_metadata")
    if isinstance(result_metadata, dict):
        result_metadata["fspm_target_policy"] = deepcopy(fspm_policy)
    public["fspm_tolerance_presentation"] = {
        **identity_preimage,
        "derivation_identity_sha256": derivation_identity,
    }

    payloads = dict(source.payloads)
    payloads["baseline_leaf_uniformity"] = baseline_bytes
    payloads["viewer_scene"] = _pretty_json_bytes(scene)
    payloads["public_result"] = _pretty_json_bytes(public)
    _assert_finite_json(public)
    _assert_finite_json(baseline_leaf)
    _assert_finite_json(scene)
    return FspmTolerancePresentation(
        source=source,
        fspm_target_tolerance_umol_m2_s=tolerance,
        fspm_tolerance_derivation_identity_sha256=derivation_identity,
        public_payload=public,
        payloads=payloads,
    )


def derive_target_adjusted_playback(
    base: CompactPlayback,
    requested_target_ppfd_umol_m2_s: object,
    *,
    lighting_target_mode: str = "mean_target",
    fixed_case_binding: Mapping[str, object] | None = None,
    canonical_domain: Mapping[str, object] | None = None,
    presentation_identity_sha256: str | None = None,
) -> TargetAdjustedCompactPlayback:
    """Derive one LED target view without changing the authenticated bundle."""

    if not isinstance(base, CompactPlayback):
        raise TypeError("target adjustment requires a validated compact playback.")
    if base.system_id == HPS_SYSTEM_ID:
        raise ValueError("HPS fixed-output playback rejects target overrides.")
    if base.system_id not in _LED_SYSTEMS:
        raise ValueError("precomputed target adjustment system is unsupported.")
    requested = validate_requested_target_ppfd(requested_target_ppfd_umol_m2_s)
    if lighting_target_mode == TARGET_CAPPED_MODE:
        return _derive_target_capped_playback(
            base,
            requested,
            fixed_case_binding=fixed_case_binding,
            canonical_domain=canonical_domain,
            presentation_identity_sha256=presentation_identity_sha256,
        )
    if lighting_target_mode != "mean_target":
        raise ValueError(
            "lighting_target_mode must be mean_target or target_capped."
        )
    control = _json_object(base.payloads["target_control"])
    base_target = _required_nonnegative(
        control.get("requested_target_ppfd_umol_m2_s"),
        "base requested target PPFD",
    )
    if base_target != FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S:
        raise ValueError("target adjustment requires an authenticated 250-PPFD base.")
    base_mean = _required_positive(
        control.get("achieved_mean_ppfd_umol_m2_s"),
        "base achieved mean PPFD",
    )
    base_fraction = _required_positive(
        control.get("dimming_factor"), "base output fraction"
    )
    if base_fraction > 1.0:
        raise ValueError("base output fraction exceeds authenticated maximum output.")
    maximum_fraction = _required_positive(
        control.get("maximum_output_fraction", 1.0),
        "maximum output fraction",
    )
    if maximum_fraction > 1.0:
        raise ValueError("maximum output fraction cannot exceed 1.0.")
    if base_fraction > maximum_fraction:
        raise ValueError("base output fraction exceeds authenticated maximum output.")
    maximum_scale = maximum_fraction / base_fraction
    maximum_achievable = base_mean * maximum_scale
    desired_scale = requested / base_mean
    applied_scale = min(desired_scale, maximum_scale)

    # The production target is the exact bundle operating point.  Preserve it
    # bit-for-bit even if the native carrier's finite formatting caused a tiny
    # requested-versus-achieved difference.
    exact_base_target_reuse = (
        requested == FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S
    )
    if exact_base_target_reuse:
        applied_scale = 1.0

    output_fraction = min(maximum_fraction, base_fraction * applied_scale)
    actual_mean = base_mean * applied_scale
    output_limited = requested > maximum_achievable
    limit_reason = TARGET_ADJUSTMENT_LIMIT_REASON if output_limited else None
    identity_preimage = {
        "schema_id": TARGET_ADJUSTMENT_SCHEMA_ID,
        "schema_version": TARGET_ADJUSTMENT_SCHEMA_VERSION,
        "base_bundle_identity_sha256": str(
            base.manifest["bundle_identity_sha256"]
        ),
        "base_target_ppfd_umol_m2_s": base_target,
        "base_achieved_mean_ppfd_umol_m2_s": base_mean,
        "base_output_fraction": base_fraction,
        "requested_target_ppfd_umol_m2_s": requested,
        "maximum_output_fraction": maximum_fraction,
        "maximum_achievable_ppfd_umol_m2_s": maximum_achievable,
        "desired_scale": desired_scale,
        "maximum_scale": maximum_scale,
        "applied_scale": applied_scale,
        "output_fraction": output_fraction,
        "actual_achieved_mean_ppfd_umol_m2_s": actual_mean,
        "output_limited": output_limited,
        "limit_reason": limit_reason,
        "exact_base_target_reuse": exact_base_target_reuse,
        "zero_output_ratio_convention": ZERO_OUTPUT_RATIO_CONVENTION,
    }
    derived_identity = _hash_json(identity_preimage)
    adjustment = TargetAdjustment(
        base_bundle_identity_sha256=str(
            base.manifest["bundle_identity_sha256"]
        ),
        base_target_ppfd_umol_m2_s=base_target,
        base_achieved_mean_ppfd_umol_m2_s=base_mean,
        base_output_fraction=base_fraction,
        requested_target_ppfd_umol_m2_s=requested,
        maximum_output_fraction=maximum_fraction,
        maximum_achievable_ppfd_umol_m2_s=maximum_achievable,
        desired_scale=desired_scale,
        maximum_scale=maximum_scale,
        applied_scale=applied_scale,
        output_fraction=output_fraction,
        actual_achieved_mean_ppfd_umol_m2_s=actual_mean,
        output_limited=output_limited,
        limit_reason=limit_reason,
        exact_base_target_reuse=exact_base_target_reuse,
        derived_playback_identity_sha256=derived_identity,
    )

    # Target 250 is an exact view of every base artifact.  Only the derived
    # capability/identity envelope is added outside authenticated payloads.
    if requested == FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S:
        public = deepcopy(dict(base.public_payload))
        _bind_adjustment_public_metadata(public, adjustment)
        return TargetAdjustedCompactPlayback(
            base=base,
            adjustment=adjustment,
            public_payload=public,
            payloads=base.payloads,
            samples=base.samples,
        )

    samples = tuple(
        PpfdMapSample(
            sample.x_m,
            sample.y_m,
            sample.z_m,
            sample.ppfd_umol_m2_s * applied_scale,
        )
        for sample in base.samples
    )
    field_identity = _stage_a_identity(samples)
    metrics = _derive_metrics(base.metrics, samples, adjustment)
    payloads = dict(base.payloads)

    baseline_leaf, target_coverage = _derive_baseline_leaf(
        base.payloads["baseline_leaf_uniformity"],
        applied_scale=applied_scale,
        requested_target=requested,
        achieved_mean=actual_mean,
        field_identity=field_identity,
    )
    payloads["baseline_leaf_uniformity"] = _pretty_json_bytes(baseline_leaf)
    metrics["baseline_leaf_position_uniformity"] = (
        _baseline_leaf_metrics_payload(baseline_leaf, payloads["baseline_leaf_uniformity"])
    )

    target_control = _derive_target_control(control, samples, adjustment)
    payloads["target_control"] = _pretty_json_bytes(target_control)
    payloads["operating_point"] = _pretty_json_bytes(
        _scale_effective_tree(
            _json_object(base.payloads["operating_point"]), applied_scale
        )
    )
    payloads["physical_source_state"] = _pretty_json_bytes(
        _derive_physical_source_state(
            _json_object(base.payloads["physical_source_state"]),
            applied_scale,
            output_fraction,
            derived_identity,
        )
    )

    visualization, visualization_payloads = _derive_visualization(
        base,
        samples=samples,
        requested_target=requested,
        target_coverage=target_coverage,
    )
    payloads.update(visualization_payloads)
    metrics_visualization = metrics.get("visualization")
    if isinstance(metrics_visualization, dict):
        metrics_visualization["field_identity_sha256"] = field_identity
        metrics_visualization["reference_policy"] = (
            "requested_lighting_target_plus_or_minus_200"
        )
    scene = deepcopy(dict(base.viewer_scene))
    scene["ppfd_heatmap"] = visualization["scene_reference"]
    run = scene.get("run")
    if isinstance(run, dict):
        information = run.get("information")
        if isinstance(information, dict):
            proposed_control = information.get("proposed_control")
            if isinstance(proposed_control, dict):
                information["proposed_control"] = _scale_effective_tree(
                    proposed_control, applied_scale
                )
                information["proposed_control"]["global_dimming_factor"] = (  # type: ignore[index]
                    output_fraction
                )
                information["proposed_control"]["feasible"] = (  # type: ignore[index]
                    not output_limited
                )
    payloads["viewer_scene"] = _pretty_json_bytes(scene)

    public = deepcopy(dict(base.public_payload))
    public["metrics"] = metrics
    _bind_adjustment_public_metadata(public, adjustment)
    payloads["public_result"] = _pretty_json_bytes(public)
    _assert_finite_json(public)
    for name in (
        "target_control",
        "operating_point",
        "physical_source_state",
        "baseline_leaf_uniformity",
        "visualization_metadata",
        "viewer_scene",
    ):
        _assert_finite_json(_json_object(payloads[name]))
    return TargetAdjustedCompactPlayback(
        base=base,
        adjustment=adjustment,
        public_payload=public,
        payloads=payloads,
        samples=samples,
    )


def _derive_target_capped_playback(
    base: CompactPlayback,
    requested_cap: float,
    *,
    fixed_case_binding: Mapping[str, object] | None,
    canonical_domain: Mapping[str, object] | None,
    presentation_identity_sha256: str | None,
) -> TargetAdjustedCompactPlayback:
    if not isinstance(fixed_case_binding, Mapping):
        raise ValueError(
            "Target-Capped playback requires an authenticated fixed-case binding."
        )
    if not isinstance(canonical_domain, Mapping):
        raise ValueError(
            "Target-Capped playback requires an authenticated canonical domain."
        )
    if (
        not isinstance(presentation_identity_sha256, str)
        or len(presentation_identity_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in presentation_identity_sha256
        )
    ):
        raise ValueError(
            "Target-Capped playback requires a canonical presentation identity."
        )

    control = _json_object(base.payloads["target_control"])
    if control.get("lighting_target_mode", "mean_target") != "mean_target":
        raise ValueError("Target-Capped playback requires a Mean Target base bundle.")
    base_target = _required_nonnegative(
        control.get("requested_target_ppfd_umol_m2_s"),
        "base requested target PPFD",
    )
    if base_target != FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S:
        raise ValueError("target adjustment requires an authenticated 250-PPFD base.")
    base_mean = _required_positive(
        control.get("achieved_mean_ppfd_umol_m2_s"),
        "base achieved mean PPFD",
    )
    base_maximum = _required_positive(
        control.get("achieved_maximum_ppfd_umol_m2_s"),
        "base achieved maximum PPFD",
    )
    base_fraction = _required_positive(
        control.get("dimming_factor"), "base output fraction"
    )
    maximum_fraction = _required_positive(
        control.get("maximum_output_fraction", 1.0),
        "maximum output fraction",
    )
    if base_fraction > maximum_fraction or maximum_fraction > 1.0:
        raise ValueError("authenticated output fractions are inconsistent.")
    full_mean = _required_positive(
        control.get("full_output_mean_ppfd_umol_m2_s"),
        "full-output mean PPFD",
    )
    full_maximum = _required_positive(
        control.get("full_output_maximum_ppfd_umol_m2_s"),
        "full-output sampled maximum PPFD",
    )
    actual_base_maximum = max(sample.ppfd_umol_m2_s for sample in base.samples)
    if (
        actual_base_maximum != base_maximum
        or actual_base_maximum / base_fraction != full_maximum
    ):
        raise ValueError(
            "authenticated base field disagrees with full-output cap authority."
        )
    controller = resolve_global_source_dimming(
        requested_target_ppfd=requested_cap,
        full_output_mean_ppfd=full_mean,
        full_output_maximum_ppfd=full_maximum,
        lighting_target_mode=TARGET_CAPPED_MODE,
        system_id=base.system_id,
    )
    prequantized = controller.dimming_factor
    output_fraction = (
        conservative_conventional_global_dimming_factor(prequantized)
        if base.system_id == CONVENTIONAL_SYSTEM_ID
        else prequantized
    )
    intensity_scale = output_fraction / base_fraction
    samples = tuple(
        PpfdMapSample(
            sample.x_m,
            sample.y_m,
            sample.z_m,
            float(sample.ppfd_umol_m2_s) * float(intensity_scale),
        )
        for sample in base.samples
    )
    achieved_values = tuple(sample.ppfd_umol_m2_s for sample in samples)
    achieved_mean = math.fsum(achieved_values) / len(achieved_values)
    achieved_maximum = max(achieved_values)
    cap_compliant = (
        achieved_maximum
        <= requested_cap + TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S
    )
    if not cap_compliant:
        raise ValueError(
            "derived Target-Capped Stage A field exceeds the requested cap."
        )

    fixed_binding_identity = _hash_json(dict(fixed_case_binding))
    canonical_domain_identity = _hash_json(dict(canonical_domain))
    identity_preimage = {
        "schema_id": TARGET_CAPPED_ADJUSTMENT_SCHEMA_ID,
        "schema_version": TARGET_CAPPED_ADJUSTMENT_SCHEMA_VERSION,
        "base_bundle_identity_sha256": str(
            base.manifest["bundle_identity_sha256"]
        ),
        "fixed_case_binding_identity_sha256": fixed_binding_identity,
        "target_control_contract_identity_sha256": (
            TARGET_CAPPED_CONTROL_CONTRACT_IDENTITY_SHA256
        ),
        "lighting_target_mode": TARGET_CAPPED_MODE,
        "requested_cap_ppfd_umol_m2_s": requested_cap,
        "intensity_scale": intensity_scale,
        "derived_output_fraction": output_fraction,
        "canonical_domain_identity_sha256": canonical_domain_identity,
        "presentation_identity_sha256": presentation_identity_sha256,
        "public_result_schema": {
            "schema_id": PUBLIC_RESULT_SCHEMA_ID,
            "schema_version": PUBLIC_RESULT_SCHEMA_VERSION,
        },
    }
    derived_identity = _hash_json(identity_preimage)
    adjustment = TargetCappedAdjustment(
        base_bundle_identity_sha256=str(
            base.manifest["bundle_identity_sha256"]
        ),
        fixed_case_binding_identity_sha256=fixed_binding_identity,
        target_control_contract_identity_sha256=(
            TARGET_CAPPED_CONTROL_CONTRACT_IDENTITY_SHA256
        ),
        canonical_domain_identity_sha256=canonical_domain_identity,
        presentation_identity_sha256=presentation_identity_sha256,
        base_target_ppfd_umol_m2_s=base_target,
        base_achieved_mean_ppfd_umol_m2_s=base_mean,
        base_achieved_maximum_ppfd_umol_m2_s=base_maximum,
        base_output_fraction=base_fraction,
        full_output_mean_ppfd_umol_m2_s=full_mean,
        full_output_maximum_ppfd_umol_m2_s=full_maximum,
        requested_cap_ppfd_umol_m2_s=requested_cap,
        maximum_output_fraction=maximum_fraction,
        controller_raw_factor=controller.raw_factor,
        capped_factor_before_native_quantization=prequantized,
        output_fraction=output_fraction,
        intensity_scale=intensity_scale,
        actual_achieved_mean_ppfd_umol_m2_s=achieved_mean,
        actual_achieved_maximum_ppfd_umol_m2_s=achieved_maximum,
        cap_binding=bool(controller.cap_binding),
        cap_compliant=cap_compliant,
        maximum_output_saturated=output_fraction == maximum_fraction,
        derived_playback_identity_sha256=derived_identity,
    )
    return _materialize_target_capped_playback(base, samples, adjustment)


def _materialize_target_capped_playback(
    base: CompactPlayback,
    samples: tuple[PpfdMapSample, ...],
    adjustment: TargetCappedAdjustment,
) -> TargetAdjustedCompactPlayback:
    field_identity = _stage_a_identity(samples)
    metrics = _derive_metrics(base.metrics, samples, adjustment)
    payloads = dict(base.payloads)
    baseline_leaf, target_coverage = _derive_baseline_leaf(
        base.payloads["baseline_leaf_uniformity"],
        applied_scale=adjustment.intensity_scale,
        requested_target=adjustment.requested_cap_ppfd_umol_m2_s,
        achieved_mean=adjustment.actual_achieved_mean_ppfd_umol_m2_s,
        field_identity=field_identity,
    )
    payloads["baseline_leaf_uniformity"] = _pretty_json_bytes(baseline_leaf)
    metrics["baseline_leaf_position_uniformity"] = (
        _baseline_leaf_metrics_payload(
            baseline_leaf, payloads["baseline_leaf_uniformity"]
        )
    )
    control = _json_object(base.payloads["target_control"])
    payloads["target_control"] = _pretty_json_bytes(
        _derive_target_control(control, samples, adjustment)
    )
    payloads["operating_point"] = _pretty_json_bytes(
        _scale_effective_tree(
            _json_object(base.payloads["operating_point"]),
            adjustment.intensity_scale,
        )
    )
    physical = _derive_physical_source_state(
        _json_object(base.payloads["physical_source_state"]),
        adjustment.intensity_scale,
        adjustment.output_fraction,
        adjustment.derived_playback_identity_sha256,
    )
    operation = physical.get("source_operation")
    if isinstance(operation, dict):
        operation["lighting_target_mode"] = TARGET_CAPPED_MODE
    payloads["physical_source_state"] = _pretty_json_bytes(physical)

    visualization, visualization_payloads = _derive_visualization(
        base,
        samples=samples,
        requested_target=adjustment.requested_cap_ppfd_umol_m2_s,
        target_coverage=target_coverage,
        lighting_target_mode=TARGET_CAPPED_MODE,
    )
    payloads.update(visualization_payloads)
    metrics_visualization = metrics.get("visualization")
    if isinstance(metrics_visualization, dict):
        metrics_visualization["field_identity_sha256"] = field_identity
        metrics_visualization["reference_policy"] = (
            "requested_sampled_ppfd_cap_plus_or_minus_200"
        )
    scene = deepcopy(dict(base.viewer_scene))
    scene["ppfd_heatmap"] = visualization["scene_reference"]
    run = scene.get("run")
    if isinstance(run, dict):
        information = run.get("information")
        if isinstance(information, dict):
            proposed_control = information.get("proposed_control")
            if isinstance(proposed_control, dict):
                information["proposed_control"] = _scale_effective_tree(
                    proposed_control, adjustment.intensity_scale
                )
                information["proposed_control"][  # type: ignore[index]
                    "global_dimming_factor"
                ] = adjustment.output_fraction
                information["proposed_control"][  # type: ignore[index]
                    "feasible"
                ] = True
    payloads["viewer_scene"] = _pretty_json_bytes(scene)

    public = deepcopy(dict(base.public_payload))
    public["metrics"] = metrics
    metadata = public.get("result_metadata")
    if isinstance(metadata, dict):
        metadata["lighting_target_mode"] = TARGET_CAPPED_MODE
    _bind_adjustment_public_metadata(public, adjustment)
    payloads["public_result"] = _pretty_json_bytes(public)
    _assert_finite_json(public)
    for name in (
        "target_control",
        "operating_point",
        "physical_source_state",
        "baseline_leaf_uniformity",
        "visualization_metadata",
        "viewer_scene",
    ):
        _assert_finite_json(_json_object(payloads[name]))
    return TargetAdjustedCompactPlayback(
        base=base,
        adjustment=adjustment,
        public_payload=public,
        payloads=payloads,
        samples=samples,
    )


def _bind_adjustment_public_metadata(
    public: dict[str, object],
    adjustment: TargetAdjustment | TargetCappedAdjustment,
) -> None:
    capabilities = list(public.get("capabilities", []))
    capability = (
        TARGET_CAPPED_ADJUSTMENT_CAPABILITY
        if isinstance(adjustment, TargetCappedAdjustment)
        else TARGET_ADJUSTMENT_CAPABILITY
    )
    if capability not in capabilities:
        capabilities.append(capability)
    public["capabilities"] = sorted(capabilities)
    public["target_adjustment"] = adjustment.to_dict()
    public["derived_playback_identity_sha256"] = (
        adjustment.derived_playback_identity_sha256
    )


def _derive_metrics(
    source: Mapping[str, object],
    samples: Sequence[PpfdMapSample],
    adjustment: TargetAdjustment | TargetCappedAdjustment,
) -> dict[str, object]:
    metrics = deepcopy(dict(source))
    scale = adjustment.applied_scale
    values = tuple(sample.ppfd_umol_m2_s for sample in samples)
    minimum = min(values)
    maximum = max(values)
    mean = math.fsum(values) / len(values)
    variance = math.fsum((value - mean) ** 2 for value in values) / len(values)
    standard_deviation = math.sqrt(variance)

    metrics["requested_target_ppfd_umol_m2_s"] = (
        adjustment.requested_target_ppfd_umol_m2_s
    )
    metrics["achieved_mean_ppfd_umol_m2_s"] = mean
    metrics["achieved_maximum_ppfd_umol_m2_s"] = maximum
    for key, value in (
        ("minimum_ppfd_umol_m2_s", minimum),
        ("maximum_ppfd_umol_m2_s", maximum),
        ("standard_deviation_ppfd_umol_m2_s", standard_deviation),
    ):
        if key in metrics:
            metrics[key] = value
    metrics["dimming_factor"] = adjustment.output_fraction
    metrics["target_feasible"] = not adjustment.output_limited
    metrics["target_infeasibility"] = _limit_payload(adjustment)
    metrics["target_adjustment"] = adjustment.to_dict()
    if isinstance(adjustment, TargetCappedAdjustment):
        metrics["lighting_target_mode"] = TARGET_CAPPED_MODE
        metrics["requested_sampled_ppfd_cap_umol_m2_s"] = (
            adjustment.requested_cap_ppfd_umol_m2_s
        )
        metrics["cap_binding"] = adjustment.cap_binding
        metrics["cap_compliant"] = adjustment.cap_compliant
        metrics["maximum_output_saturated"] = (
            adjustment.maximum_output_saturated
        )

    for key, value in tuple(metrics.items()):
        if isinstance(value, dict) and "statistics" in key.lower():
            metrics[key] = _scale_effective_tree(value, scale)

    if mean == 0.0:
        metrics["cv_percent"] = 0.0
    power = metrics.get("power")
    if isinstance(power, dict) and "effective_w" in power:
        power["effective_w"] = _number(power["effective_w"]) * scale
    ppf = metrics.get("ppf")
    if isinstance(ppf, dict) and "emitted_umol_s" in ppf:
        ppf["emitted_umol_s"] = _number(ppf["emitted_umol_s"]) * scale
    proposed_control = metrics.get("proposed_control")
    if isinstance(proposed_control, dict):
        metrics["proposed_control"] = _scale_effective_tree(
            proposed_control, scale
        )
        metrics["proposed_control"]["global_dimming_factor"] = (  # type: ignore[index]
            adjustment.output_fraction
        )
        metrics["proposed_control"]["feasible"] = (  # type: ignore[index]
            not adjustment.output_limited
        )
    surface = metrics.get("fspm_surface_light_metrics")
    if isinstance(surface, dict):
        groups = surface.get("surface_light")
        if isinstance(groups, dict):
            surface["surface_light"] = _scale_effective_tree(groups, scale)
        absorbed = surface.get("absorbed_par_metrics")
        if isinstance(absorbed, dict):
            surface["absorbed_par_metrics"] = _scale_effective_tree(
                absorbed, scale
            )
    spatial = metrics.get("spatial_uniformity")
    if isinstance(spatial, dict):
        report_values = spatial.get("values")
        if isinstance(report_values, dict):
            report_values.update(
                _spatial_values(
                    minimum,
                    maximum,
                    mean,
                    standard_deviation,
                    len(values),
                    base_values=report_values,
                )
            )
            if mean == 0.0:
                provenance = spatial.get("provenance")
                if isinstance(provenance, dict):
                    provenance["zero_output_ratio_convention"] = (
                        ZERO_OUTPUT_RATIO_CONVENTION
                    )
    fspm = metrics.get("fspm_target_policy")
    if isinstance(fspm, dict):
        _update_fspm_target_policy(fspm, mean)
    return metrics


def _spatial_values(
    minimum: float,
    maximum: float,
    mean: float,
    standard_deviation: float,
    count: int,
    *,
    base_values: Mapping[str, object],
) -> dict[str, object]:
    if mean == 0.0:
        cv = 0.0
        cv_percent = 0.0
        degree = 100.0
        minimum_to_mean = 0.0
        minimum_to_maximum = 0.0
    else:
        cv = standard_deviation / mean
        cv_percent = cv * 100.0
        degree = 100.0 - cv_percent
        minimum_to_mean = minimum / mean
        minimum_to_maximum = minimum / maximum if maximum > 0.0 else 0.0
    result = dict(base_values)
    result.update(
        {
            "minimum_ppfd": minimum,
            "maximum_ppfd": maximum,
            "mean_ppfd": mean,
            "population_standard_deviation_ppfd": standard_deviation,
            "coefficient_of_variation": cv,
            "coefficient_of_variation_percent": cv_percent,
            "degree_of_uniformity_percent": degree,
            "minimum_to_mean_uniformity": minimum_to_mean,
            "minimum_to_maximum_ppfd_ratio": minimum_to_maximum,
            "sample_count": count,
        }
    )
    return result


def _derive_target_control(
    source: Mapping[str, object],
    samples: Sequence[PpfdMapSample],
    adjustment: TargetAdjustment | TargetCappedAdjustment,
) -> dict[str, object]:
    result = _scale_effective_tree(source, adjustment.applied_scale)
    maximum = max(sample.ppfd_umol_m2_s for sample in samples)
    limiting_index = max(
        range(len(samples)), key=lambda index: samples[index].ppfd_umol_m2_s
    )
    limiting = samples[limiting_index]
    result.update(
        {
            "requested_target_ppfd_umol_m2_s": (
                adjustment.requested_target_ppfd_umol_m2_s
            ),
            "achieved_mean_ppfd_umol_m2_s": (
                adjustment.actual_achieved_mean_ppfd_umol_m2_s
            ),
            "achieved_maximum_ppfd_umol_m2_s": maximum,
            "raw_factor": (
                adjustment.requested_target_ppfd_umol_m2_s
                / adjustment.maximum_achievable_ppfd_umol_m2_s
            ),
            "dimming_factor": adjustment.output_fraction,
            "feasible": not adjustment.output_limited,
            "infeasibility": _limit_payload(adjustment),
            "limiting_sample": {
                "index": limiting_index,
                "x_m": limiting.x_m,
                "y_m": limiting.y_m,
                "z_m": limiting.z_m,
                "achieved_ppfd_umol_m2_s": maximum,
            },
            "precomputed_target_adjustment": adjustment.to_dict(),
            "post_trace_scaling": True,
            "base_factor_policy": source.get("factor_policy"),
            "factor_policy": (
                "precomputed_linear_min(requested_target/base_achieved_mean,"
                "maximum_output_fraction/base_output_fraction)"
            ),
            "dimming_stage": "derived_playback_post_transport_linear_scale",
        }
    )
    if isinstance(adjustment, TargetCappedAdjustment):
        result.update(
            {
                "lighting_target_mode": TARGET_CAPPED_MODE,
                "requested_value_semantics": (
                    "requested_sampled_stage_a_maximum_ppfd"
                ),
                "raw_factor": adjustment.controller_raw_factor,
                "capped_factor_before_native_quantization": (
                    adjustment.capped_factor_before_native_quantization
                ),
                "feasible": True,
                "infeasibility": None,
                "cap_binding": adjustment.cap_binding,
                "cap_compliant": adjustment.cap_compliant,
                "compliance_tolerance_umol_m2_s": (
                    TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S
                ),
                "maximum_output_saturated": (
                    adjustment.maximum_output_saturated
                ),
                "factor_policy": (
                    "conservative_historical_ies2rad_%g_compatible("
                    "min(requested_cap/full_output_sampled_maximum, 1.0))"
                    if source.get("native_carrier_numeric_format")
                    == "%g (six significant digits)"
                    else "min(requested_cap/full_output_sampled_maximum, 1.0)"
                ),
            }
        )
    fspm = result.get("fspm_target_policy")
    if isinstance(fspm, dict):
        _update_fspm_target_policy(
            fspm, adjustment.actual_achieved_mean_ppfd_umol_m2_s
        )
    return result


def _limit_payload(
    adjustment: TargetAdjustment | TargetCappedAdjustment,
) -> dict[str, object] | None:
    if not adjustment.output_limited:
        return None
    return {
        "reason": TARGET_ADJUSTMENT_LIMIT_REASON,
        "requested_target_ppfd_umol_m2_s": (
            adjustment.requested_target_ppfd_umol_m2_s
        ),
        "maximum_achievable_ppfd_umol_m2_s": (
            adjustment.maximum_achievable_ppfd_umol_m2_s
        ),
        "successful_saturated_playback": True,
    }


def _update_fspm_target_policy(policy: dict[str, object], achieved: float) -> None:
    if policy.get("mode") != "automatic":
        return
    tolerance = _required_positive(
        policy.get("tolerance_umol_m2_s"), "FSPM target tolerance"
    )
    policy["override_umol_m2_s"] = None
    policy["resolved_target_umol_m2_s"] = achieved
    policy["classification_range"] = {
        "lower_umol_m2_s": max(0.0, achieved - tolerance),
        "upper_umol_m2_s": achieved + tolerance,
        "bounds": "inclusive",
    }


def _set_fspm_policy_tolerance(
    policy: dict[str, object], tolerance: float
) -> None:
    if policy.get("mode") != "automatic":
        raise ValueError("committed playback FSPM target policy is not automatic.")
    resolved = _required_nonnegative(
        policy.get("resolved_target_umol_m2_s"), "resolved FSPM target"
    )
    policy["tolerance_umol_m2_s"] = tolerance
    policy["classification_range"] = {
        "lower_umol_m2_s": max(0.0, resolved - tolerance),
        "upper_umol_m2_s": resolved + tolerance,
        "bounds": "inclusive",
    }


def _derive_tolerance_baseline_leaf(
    source: Mapping[str, object],
    *,
    tolerance: float,
    tolerance_derivation_identity_sha256: str,
) -> dict[str, object]:
    payload = deepcopy(dict(source))
    raw_policy = payload.get("target_policy")
    if not isinstance(raw_policy, dict) or raw_policy.get("mode") != "automatic":
        raise ValueError("baseline leaf automatic target policy is missing.")
    inputs = raw_policy.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("baseline leaf target inputs are missing.")
    resolved = _required_nonnegative(
        raw_policy.get("resolved_target_umol_m2_s"),
        "baseline leaf resolved target",
    )
    requested = _optional_nonnegative(
        inputs.get("requested_lighting_target_umol_m2_s"),
        "baseline leaf requested lighting target",
    )
    achieved = _required_nonnegative(
        inputs.get("achieved_stage_a_mean_umol_m2_s"),
        "baseline leaf achieved Stage A mean",
    )
    override = _optional_nonnegative(
        inputs.get("override_umol_m2_s"), "baseline leaf target override"
    )
    policy = BaselineLeafTargetPolicy(
        system_id=str(raw_policy["system_id"]),
        mode="automatic",
        source=str(raw_policy["source"]),
        requested_lighting_target_umol_m2_s=requested,
        achieved_stage_a_mean_umol_m2_s=achieved,
        override_umol_m2_s=override,
        resolved_target_umol_m2_s=resolved,
        tolerance_umol_m2_s=tolerance,
        inclusive_lower_umol_m2_s=max(0.0, resolved - tolerance),
        inclusive_upper_umol_m2_s=resolved + tolerance,
    )
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("baseline leaf records are missing.")
    values: list[float] = []
    for raw in records:
        if not isinstance(raw, dict):
            raise ValueError("baseline leaf record is malformed.")
        value = _required_nonnegative(
            raw.get("interpolated_ppfd_umol_m2_s"),
            "baseline leaf interpolated PPFD",
        )
        raw["classification"] = classify_baseline_leaf_ppfd(value, policy)
        values.append(value)
    payload["target_policy"] = policy.to_dict()
    if payload.get("available") is True:
        payload["summary"] = aggregate_leaf_position_ppfd(values, policy)
    parent_identity = payload.get("derivation_identity_sha256")
    payload["base_derivation_identity_sha256"] = payload.get(
        "base_derivation_identity_sha256", parent_identity
    )
    payload["derivation_identity_sha256"] = _hash_json(
        {
            "schema_id": (
                "fspm-optics.precomputed-derived-baseline-leaf-tolerance"
            ),
            "schema_version": 1,
            "parent_derivation_identity_sha256": parent_identity,
            "fspm_tolerance_derivation_identity_sha256": (
                tolerance_derivation_identity_sha256
            ),
            "target_policy": policy.to_dict(),
            "records": records,
        }
    )
    return payload


def _derive_baseline_leaf(
    data: bytes,
    *,
    applied_scale: float,
    requested_target: float,
    achieved_mean: float,
    field_identity: str,
) -> tuple[dict[str, object], dict[str, object]]:
    # This deliberately matches live publication: the Target-Capped
    # visualization legend identifies the sampled cap, while 3D Target
    # Coverage and leaf classifications retain the requested-lighting-target
    # authority (the same requested numeric value), independently of the
    # automatic FSPM reference resolved from achieved mean.
    payload = deepcopy(_json_object(data))
    raw_policy = payload.get("target_policy")
    if not isinstance(raw_policy, dict):
        raise ValueError("baseline leaf target policy is missing.")
    inputs = raw_policy.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("baseline leaf target inputs are missing.")
    tolerance = _required_positive(
        inputs.get("tolerance_umol_m2_s"), "Target Coverage tolerance"
    )
    policy = BaselineLeafTargetPolicy(
        system_id=str(payload["system_id"]),
        mode="automatic",
        source="requested_lighting_target",
        requested_lighting_target_umol_m2_s=requested_target,
        achieved_stage_a_mean_umol_m2_s=achieved_mean,
        override_umol_m2_s=None,
        resolved_target_umol_m2_s=requested_target,
        tolerance_umol_m2_s=tolerance,
        inclusive_lower_umol_m2_s=max(0.0, requested_target - tolerance),
        inclusive_upper_umol_m2_s=requested_target + tolerance,
    )
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("baseline leaf records are missing.")
    values: list[float] = []
    for raw in records:
        if not isinstance(raw, dict):
            raise ValueError("baseline leaf record is malformed.")
        value = _number(raw["interpolated_ppfd_umol_m2_s"]) * applied_scale
        raw["interpolated_ppfd_umol_m2_s"] = value
        raw["classification"] = classify_baseline_leaf_ppfd(value, policy)
        values.append(value)
    payload["target_policy"] = policy.to_dict()
    field = payload.get("stage_a_field")
    if not isinstance(field, dict):
        raise ValueError("baseline leaf Stage A field is missing.")
    field["field_identity_sha256"] = field_identity
    if payload.get("available") is True:
        payload["summary"] = aggregate_leaf_position_ppfd(values, policy)
    derivation = {
        "schema_id": "fspm-optics.precomputed-derived-baseline-leaf-uniformity",
        "schema_version": 1,
        "parent_derivation_identity_sha256": payload.get(
            "derivation_identity_sha256"
        ),
        "stage_a_field_identity_sha256": field_identity,
        "target_policy": policy.to_dict(),
        "records": records,
    }
    payload["base_derivation_identity_sha256"] = payload.get(
        "derivation_identity_sha256"
    )
    payload["derivation_identity_sha256"] = _hash_json(derivation)

    target_coverage = _target_coverage_from_baseline(
        payload,
        requested_target=requested_target,
        tolerance=tolerance,
        field_identity=field_identity,
    )
    return payload, target_coverage


def _target_coverage_from_baseline(
    payload: Mapping[str, object],
    *,
    requested_target: float,
    tolerance: float,
    field_identity: str,
) -> dict[str, object]:
    # Geometry, support, palette, and sampling policy are authenticated by the
    # base viewer contract and are copied from its sibling payload later.  This
    # function returns the updated scientific fields; the caller merges them.
    return {
        "requested_target_ppfd_umol_m2_s": requested_target,
        "tolerance_ppfd_umol_m2_s": tolerance,
        "parent_source_field_identity_sha256": field_identity,
        "system_id": payload["system_id"],
    }


def _baseline_leaf_metrics_payload(
    payload: Mapping[str, object], data: bytes
) -> dict[str, object]:
    return {
        "available": payload["available"],
        "unavailable_reason_code": payload["unavailable_reason_code"],
        "artifact": "baseline-leaf-position-uniformity.v1.json",
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
        "derivation_identity_sha256": payload["derivation_identity_sha256"],
        "physical_leaf_count": payload["physical_leaf_count"],
        "target_policy": payload["target_policy"],
        "summary": payload["summary"],
    }


def _derive_visualization(
    base: CompactPlayback,
    *,
    samples: Sequence[PpfdMapSample],
    requested_target: float,
    target_coverage: Mapping[str, object],
    lighting_target_mode: str = "mean_target",
) -> tuple[dict[str, object], dict[str, bytes]]:
    base_metadata = base.visualization
    overlay = _overlay_from_metadata(base_metadata)
    base_overlay = base_metadata.get("overlay")
    layout_identity = {
        "base_layout_identity_sha256": (
            base_overlay.get("layout_identity_sha256")
            if isinstance(base_overlay, Mapping)
            else None
        )
    }
    artifacts = build_ppfd_visualization_artifacts(
        run_id=base.run_id,
        samples=samples,
        layout=None,
        layout_identity=layout_identity,
        overlay_plan=overlay,
        reference=(
            VisualizationReference.requested_sampled_cap(requested_target)
            if lighting_target_mode == TARGET_CAPPED_MODE
            else VisualizationReference.requested_target(requested_target)
        ),
    )
    by_name = {artifact.filename: artifact.data for artifact in artifacts.artifacts}
    metadata = deepcopy(artifacts.metadata)
    if artifacts.field_sha256 != _stage_a_identity(samples):
        raise ValueError("derived visualization changed the Float64 Stage A identity.")
    transforms = metadata.get("transforms")
    if isinstance(transforms, dict):
        transforms["target_rescaling"] = True
    derived_overlay = metadata.get("overlay")
    if isinstance(derived_overlay, dict) and isinstance(base_overlay, Mapping):
        derived_overlay["layout_identity_sha256"] = base_overlay.get(
            "layout_identity_sha256"
        )
    metadata_bytes = _pretty_json_bytes(metadata)
    scatter = by_name["ppfd-scatter.f32le.bin"]
    base_scene = base.viewer_scene
    base_heatmap = base_scene.get("ppfd_heatmap")
    if not isinstance(base_heatmap, Mapping):
        raise ValueError("base viewer heatmap contract is missing.")
    base_coverage = base_heatmap.get("target_coverage")
    if not isinstance(base_coverage, Mapping):
        raise ValueError("base Target Coverage contract is missing.")
    coverage = deepcopy(dict(base_coverage))
    coverage["parent_source_field_identity_sha256"] = target_coverage[
        "parent_source_field_identity_sha256"
    ]
    coverage["tolerance_ppfd_umol_m2_s"] = target_coverage[
        "tolerance_ppfd_umol_m2_s"
    ]
    reference = coverage.get("reference")
    if not isinstance(reference, dict):
        raise ValueError("base Target Coverage reference is missing.")
    reference.update(
        {
            "ppfd_umol_m2_s": requested_target,
            "source": "requested_lighting_target",
            "policy_mode": "automatic",
        }
    )
    bound = bind_ppfd_heatmap_viewer_artifacts(
        metadata_bytes=metadata_bytes,
        scatter_bytes=scatter,
        expected_field_identity_sha256=artifacts.field_sha256,
        target_coverage=coverage,
    )
    return (
        {"metadata": metadata, "scene_reference": dict(bound.scene_reference)},
        {
            "visualization_metadata": metadata_bytes,
            "heatmap": by_name["ppfd-heatmap.png"],
            "heatmap_overlay": by_name["ppfd-heatmap-overlay.png"],
        },
    )


def _overlay_from_metadata(metadata: Mapping[str, object]) -> AuthoritativeOverlayPlan:
    raw = metadata.get("overlay")
    if not isinstance(raw, Mapping):
        raise ValueError("visualization overlay metadata is missing.")
    room = raw.get("room")
    if not isinstance(room, Mapping):
        raise ValueError("visualization overlay room is missing.")
    rectangles = tuple(
        _overlay_rectangle(value)
        for value in _mapping_sequence(raw.get("rectangles"), "overlay rectangles")
    )
    lines = tuple(
        _overlay_line(value)
        for value in _mapping_sequence(raw.get("lines", []), "overlay lines")
    )
    fixtures = tuple(
        dict(value)
        for value in _mapping_sequence(raw.get("fixtures", []), "overlay fixtures")
    )
    return AuthoritativeOverlayPlan(
        system_id=str(raw["system_id"]),
        policy_id=str(raw.get("fixture_policy_id", raw.get("policy_id"))),
        coordinate_source=str(raw["coordinate_source"]),
        room_length_m=_number(room["length_x_m"]),
        room_width_m=_number(room["width_y_m"]),
        axes_swapped=bool(room["axes_swapped_from_request"]),
        rectangles=rectangles,
        lines=lines,
        fixture_metadata=fixtures,
        metadata=(
            dict(raw["metadata"])
            if isinstance(raw.get("metadata"), Mapping)
            else {}
        ),
    )


def _overlay_rectangle(raw: Mapping[str, object]) -> OverlayRectangle:
    center = raw.get("center_m")
    size = raw.get("size_m")
    stroke = raw.get("stroke")
    if not all(isinstance(value, Mapping) for value in (center, size, stroke)):
        raise ValueError("overlay rectangle is malformed.")
    return OverlayRectangle(
        primitive_id=str(raw["primitive_id"]),
        fixture_id=str(raw["fixture_id"]),
        primitive_kind=str(raw["primitive_kind"]),
        center_x_m=_number(center["x"]),  # type: ignore[index]
        center_y_m=_number(center["y"]),  # type: ignore[index]
        width_x_m=_number(size["x"]),  # type: ignore[index]
        height_y_m=_number(size["y"]),  # type: ignore[index]
        orientation_degrees=_number(raw["orientation_degrees"]),
        color_group_index=int(raw.get("color_group_index", 0)),
        stroke_color_role=str(stroke["color_role"]),  # type: ignore[index,arg-type]
        stroke_width_px=int(stroke["width_px"]),  # type: ignore[index]
    )


def _overlay_line(raw: Mapping[str, object]) -> OverlayLine:
    start = raw.get("start_m")
    end = raw.get("end_m")
    stroke = raw.get("stroke")
    if not all(isinstance(value, Mapping) for value in (start, end, stroke)):
        raise ValueError("overlay line is malformed.")
    return OverlayLine(
        primitive_id=str(raw["primitive_id"]),
        fixture_id=str(raw["fixture_id"]),
        primitive_kind=str(raw["primitive_kind"]),
        start_x_m=_number(start["x"]),  # type: ignore[index]
        start_y_m=_number(start["y"]),  # type: ignore[index]
        end_x_m=_number(end["x"]),  # type: ignore[index]
        end_y_m=_number(end["y"]),  # type: ignore[index]
        color_group_index=int(raw.get("color_group_index", 0)),
        stroke_color_role=str(stroke["color_role"]),  # type: ignore[index,arg-type]
        stroke_width_px=int(stroke["width_px"]),  # type: ignore[index]
    )


def _derive_physical_source_state(
    source: Mapping[str, object],
    scale: float,
    output_fraction: float,
    derived_identity: str,
) -> dict[str, object]:
    result = _scale_effective_tree(source, scale)
    operation = result.get("source_operation")
    if isinstance(operation, dict):
        for key in (
            "global_linear_dimming_factor",
            "global_dimming_factor",
        ):
            if key in operation:
                operation[key] = output_fraction
        operation["precomputed_post_transport_linear_scaling"] = True
    result["base_source_state_id"] = source.get("source_state_id")
    result["derived_playback_identity_sha256"] = derived_identity
    return result


def _scale_effective_tree(value: object, scale: float, *, key: str = "") -> object:
    classification = _quantity_classification(key)
    if classification and isinstance(value, int | float) and not isinstance(value, bool):
        factor = scale * scale if classification == "squared" else scale
        return float(value) * factor
    if classification and isinstance(value, list):
        factor = scale * scale if classification == "squared" else scale
        return [
            float(item) * factor
            if isinstance(item, int | float) and not isinstance(item, bool)
            else deepcopy(item)
            for item in value
        ]
    if isinstance(value, Mapping):
        return {
            str(child_key): _scale_effective_tree(
                child_value, scale, key=str(child_key)
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_scale_effective_tree(item, scale, key=key) for item in value]
    return deepcopy(value)


def _quantity_classification(key: str) -> str | None:
    name = key.lower()
    invariant_tokens = (
        "full_output",
        "declared_max",
        "rated_",
        "tested_",
        "reference_watts",
        "tolerance",
        "requested_target",
        "maximum_achievable",
        "ratio",
        "fraction",
        "efficiency",
        "percent",
        "ppe",
        "per_electrical_watt",
        "per_watt",
        "transmission",
        "absorptance",
        "reflectance",
        "coefficient",
        "area_m2",
        "width_",
        "height_",
        "length_",
        "position",
        "centroid",
        "coordinate",
        "index",
        "count",
        "denominator",
        "wavelength",
    )
    if any(token in name for token in invariant_tokens):
        return None
    if "variance" in name:
        return "squared"
    linear_exact = {
        "watts",
        "effective_w",
        "effective_umol_s",
        "dimming_factor",
        "global_dimming_factor",
        "global_linear_dimming_factor",
        "effective_carrier_multiplier_per_fixture",
        "capped_factor_before_native_quantization",
    }
    if name in linear_exact:
        return "linear"
    linear_tokens = (
        "effective_watts",
        "effective_power",
        "effective_internal_par_ppf",
        "effective_modeled_completed_aperture_par_ppf",
        "effective_ppf",
        "emitted_ppf",
        "emitted_umol_s",
        "total_power_w",
        "photon_flux_density_umol_m2_s",
        "photon_rate_umol_s",
        "absorbed_par_rate_umol_s",
        "ppfd_umol_m2_s",
    )
    return "linear" if any(token in name for token in linear_tokens) else None


def _stage_a_identity(samples: Sequence[PpfdMapSample]) -> str:
    data = b"".join(
        struct.pack(
            "<dddd",
            sample.x_m,
            sample.y_m,
            sample.z_m,
            sample.ppfd_umol_m2_s,
        )
        for sample in samples
    )
    return hashlib.sha256(data).hexdigest()


def _mapping_sequence(value: object, label: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"{label} are malformed.")
    return tuple(value)


def _json_object(data: bytes) -> dict[str, object]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("compact JSON playback payload is invalid.") from exc
    if not isinstance(value, dict):
        raise ValueError("compact JSON playback payload root is not an object.")
    return value


def _pretty_json_bytes(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("derived playback numeric field is malformed.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("derived playback numeric field is non-finite.")
    return result


def _required_nonnegative(value: object, label: str) -> float:
    result = _number(value)
    if result < 0.0:
        raise ValueError(f"{label} must be non-negative.")
    return result


def _optional_nonnegative(value: object, label: str) -> float | None:
    return None if value is None else _required_nonnegative(value, label)


def _required_positive(value: object, label: str) -> float:
    result = _number(value)
    if result <= 0.0:
        raise ValueError(f"{label} must be positive.")
    return result


def _assert_finite_json(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("derived playback contains a non-finite JSON value.")
    if isinstance(value, Mapping):
        for child in value.values():
            _assert_finite_json(child)
    elif isinstance(value, list | tuple):
        for child in value:
            _assert_finite_json(child)


__all__ = [
    "DEFAULT_PRECOMPUTED_FSPM_TARGET_TOLERANCE_UMOL_M2_S",
    "FSPM_TOLERANCE_PRESENTATION_SCHEMA_ID",
    "FSPM_TOLERANCE_PRESENTATION_SCHEMA_VERSION",
    "TARGET_ADJUSTMENT_CAPABILITY",
    "TARGET_ADJUSTMENT_LIMIT_REASON",
    "TARGET_ADJUSTMENT_SCHEMA_ID",
    "TARGET_ADJUSTMENT_SCHEMA_VERSION",
    "TARGET_CAPPED_ADJUSTMENT_CAPABILITY",
    "TARGET_CAPPED_ADJUSTMENT_SCHEMA_ID",
    "TARGET_CAPPED_ADJUSTMENT_SCHEMA_VERSION",
    "TARGET_CAPPED_CONTROL_CONTRACT_IDENTITY_SHA256",
    "ZERO_OUTPUT_RATIO_CONVENTION",
    "FspmTolerancePresentation",
    "TargetAdjustedCompactPlayback",
    "TargetAdjustment",
    "TargetCappedAdjustment",
    "derive_fspm_tolerance_presentation",
    "derive_target_adjusted_playback",
    "validate_fspm_target_tolerance",
    "validate_requested_target_ppfd",
]
