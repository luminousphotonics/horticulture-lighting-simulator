"""Exact fixed-catalog resolution and requested-orientation playback views."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
from typing import Mapping, Protocol

from fspm_optics.application.domain import (
    CONVENTIONAL_SYSTEM_ID,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    ConventionalRunRequest,
    HpsRunRequest,
    ProposedRunRequest,
    RunRequest,
)
from fspm_optics.geometry.coordinate_frame import RoomCoordinateFrame
from fspm_optics.precomputed.compact_bundle import (
    CompactPlayback,
)
from fspm_optics.precomputed.committed_playback import (
    build_committed_playback_plan,
    load_committed_case_bundle,
)
from fspm_optics.precomputed.contracts import (
    canonical_room_domain,
)
from fspm_optics.precomputed.target_adjustment import (
    DEFAULT_PRECOMPUTED_FSPM_TARGET_TOLERANCE_UMOL_M2_S,
    FspmTolerancePresentation,
    TargetCappedAdjustment,
    TargetAdjustedCompactPlayback,
    derive_fspm_tolerance_presentation,
    derive_target_adjusted_playback,
    validate_fspm_target_tolerance,
    validate_requested_target_ppfd,
)
from fspm_optics.transport.scalar_ppfd import PpfdMapSample


REQUESTED_PLAYBACK_SCHEMA_ID = "fspm-optics.requested-orientation-playback"
REQUESTED_PLAYBACK_SCHEMA_VERSION = 2
TARGET_CAPPED_REQUESTED_PLAYBACK_SCHEMA_VERSION = 3
FSPM_TOLERANCE_REQUESTED_PLAYBACK_SCHEMA_VERSION = 4
_TARGET_UNSET = object()
CanonicalPlayback = (
    CompactPlayback | TargetAdjustedCompactPlayback | FspmTolerancePresentation
)


class PlaybackCase(Protocol):
    ordinal: int
    case_id: str
    system_id: str
    layout_variant: str
    aisle_enabled: bool
    canonical_domain: Mapping[str, object]
    request: RunRequest

    def output_path(self, output_root: str | Path) -> Path: ...

    def bundle_validation_expectations(
        self, plan_identity_sha256: str
    ) -> dict[str, object]: ...


class PlaybackPlan(Protocol):
    cases: tuple[PlaybackCase, ...]
    plan_identity_sha256: str

    @property
    def case_count(self) -> int: ...


@dataclass(frozen=True, slots=True)
class FixedPlaybackResolution:
    case: PlaybackCase
    requested_length_ft: float
    requested_width_ft: float
    bundle_path: Path
    playback: "RequestedCompactPlayback"


@dataclass(frozen=True, slots=True)
class RequestedCompactPlayback:
    """A presentation view over one freshly validated canonical bundle."""

    canonical: CanonicalPlayback
    case: PlaybackCase
    requested_length_ft: float
    requested_width_ft: float
    coordinate_frame: RoomCoordinateFrame
    presentation_identity_sha256: str

    @classmethod
    def create(
        cls,
        canonical: CanonicalPlayback,
        case: PlaybackCase,
        *,
        requested_length_ft: float,
        requested_width_ft: float,
    ) -> "RequestedCompactPlayback":
        requested_domain = canonical_room_domain(
            requested_length_ft, requested_width_ft
        )
        if requested_domain != case.canonical_domain:
            raise ValueError("requested orientation does not match the canonical case.")
        canonical_room = case.canonical_domain["canonical_aligned_room"]
        canonical_length_ft = float(canonical_room["length_x_ft"])
        canonical_width_ft = float(canonical_room["width_y_ft"])
        frame = RoomCoordinateFrame(
            canonical_length_ft * 0.3048,
            canonical_width_ft * 0.3048,
        )
        canonical_identity = str(
            canonical.manifest["bundle_identity_sha256"]
        )
        presentation = {
            "schema_id": REQUESTED_PLAYBACK_SCHEMA_ID,
            "schema_version": REQUESTED_PLAYBACK_SCHEMA_VERSION,
            "canonical_bundle_identity_sha256": canonical_identity,
            "canonical_case_id": case.case_id,
            "requested_room_ft": {
                "length": float(requested_length_ft),
                "width": float(requested_width_ft),
            },
            "canonical_display_room_ft": {
                "length": canonical_length_ft,
                "width": canonical_width_ft,
            },
            "simulation_to_requested": {
                "rotation_degrees_about_z": 0,
                "determinant": 1,
                "translation_m": [0.0, 0.0, 0.0],
            },
            "coordinate_frame": frame.to_payload(),
        }
        derived_identity = getattr(
            canonical, "derived_playback_identity_sha256", None
        )
        if derived_identity is not None:
            presentation["derived_playback_identity_sha256"] = derived_identity
        tolerance_derivation_identity = getattr(
            canonical, "fspm_tolerance_derivation_identity_sha256", None
        )
        if tolerance_derivation_identity is not None:
            presentation["schema_version"] = (
                FSPM_TOLERANCE_REQUESTED_PLAYBACK_SCHEMA_VERSION
            )
            presentation["fspm_target_tolerance_umol_m2_s"] = (
                canonical.fspm_target_tolerance_umol_m2_s
            )
            presentation["fspm_tolerance_derivation_identity_sha256"] = (
                tolerance_derivation_identity
            )
        adjustment = getattr(canonical, "adjustment", None)
        if (
            isinstance(adjustment, TargetCappedAdjustment)
            and tolerance_derivation_identity is None
        ):
            presentation["schema_version"] = (
                TARGET_CAPPED_REQUESTED_PLAYBACK_SCHEMA_VERSION
            )
            presentation["lighting_target_mode"] = "target_capped"
            identity = adjustment.presentation_identity_sha256
        else:
            identity = hashlib.sha256(_canonical_json(presentation)).hexdigest()
        return cls(
            canonical=canonical,
            case=case,
            requested_length_ft=float(requested_length_ft),
            requested_width_ft=float(requested_width_ft),
            coordinate_frame=frame,
            presentation_identity_sha256=identity,
        )

    @property
    def canonical_bundle_identity_sha256(self) -> str:
        return str(self.canonical.manifest["bundle_identity_sha256"])

    @property
    def derived_playback_identity_sha256(self) -> str | None:
        value = getattr(
            self.canonical, "derived_playback_identity_sha256", None
        )
        return None if value is None else str(value)

    @property
    def fspm_target_tolerance_umol_m2_s(self) -> float:
        value = getattr(
            self.canonical, "fspm_target_tolerance_umol_m2_s", None
        )
        if value is not None:
            return validate_fspm_target_tolerance(value)
        policy = self.metrics.get("fspm_target_policy")
        if not isinstance(policy, Mapping):
            raise ValueError("playback FSPM target policy is missing.")
        return validate_fspm_target_tolerance(
            policy.get("tolerance_umol_m2_s")
        )

    @property
    def fspm_tolerance_derivation_identity_sha256(self) -> str | None:
        value = getattr(
            self.canonical,
            "fspm_tolerance_derivation_identity_sha256",
            None,
        )
        return None if value is None else str(value)

    @property
    def metrics(self) -> Mapping[str, object]:
        """Request dimension order leaves published scalar metrics invariant."""

        return self.canonical.metrics

    def simulation_to_requested_position(
        self, value: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        """Return a position in the canonical landscape display frame."""

        return self.coordinate_frame.simulation_to_requested_position(value)

    def simulation_to_requested_direction(
        self, value: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        """Return a direction in the canonical landscape display frame."""

        return self.coordinate_frame.simulation_to_requested_direction(value)

    def simulation_bounds_to_requested(
        self, bounds: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        return self.coordinate_frame.simulation_bounds_to_requested(bounds)

    @property
    def samples(self) -> tuple[PpfdMapSample, ...]:
        return tuple(
            sorted(
                self.canonical.samples,
                key=lambda sample: (sample.y_m, sample.x_m, sample.z_m),
            )
        )

    def canonical_display_room_ft(self) -> dict[str, float]:
        room = self.case.canonical_domain["canonical_aligned_room"]
        return {
            "length": float(room["length_x_ft"]),
            "width": float(room["width_y_ft"]),
        }

    def presentation_metadata(self) -> dict[str, object]:
        adjustment = getattr(self.canonical, "adjustment", None)
        schema_version = (
            FSPM_TOLERANCE_REQUESTED_PLAYBACK_SCHEMA_VERSION
            if self.fspm_tolerance_derivation_identity_sha256 is not None
            else TARGET_CAPPED_REQUESTED_PLAYBACK_SCHEMA_VERSION
            if isinstance(adjustment, TargetCappedAdjustment)
            else REQUESTED_PLAYBACK_SCHEMA_VERSION
        )
        payload: dict[str, object] = {
            "schema_id": REQUESTED_PLAYBACK_SCHEMA_ID,
            "schema_version": schema_version,
            "presentation_identity_sha256": self.presentation_identity_sha256,
            "canonical_bundle_identity_sha256": (
                self.canonical_bundle_identity_sha256
            ),
            "canonical_case_id": self.case.case_id,
            "requested_room_ft": {
                "length": self.requested_length_ft,
                "width": self.requested_width_ft,
            },
            "canonical_display_room_ft": self.canonical_display_room_ft(),
            "coordinate_frame": self.coordinate_frame.to_payload(),
            "simulation_to_requested_rotation_degrees_about_z": 0,
            "viewer_global_rotation_degrees_about_y": 0,
            "heatmap_counterclockwise_quarter_turns": 0,
            "scalar_metrics_invariant": True,
            "fspm_target_tolerance_umol_m2_s": (
                self.fspm_target_tolerance_umol_m2_s
            ),
        }
        if self.fspm_tolerance_derivation_identity_sha256 is not None:
            payload["fspm_tolerance_presentation"] = (
                self.canonical.tolerance_presentation_metadata()
            )
        if self.derived_playback_identity_sha256 is not None:
            payload["derived_playback_identity_sha256"] = (
                self.derived_playback_identity_sha256
            )
            adjustment = getattr(self.canonical, "adjustment", None)
            if adjustment is not None:
                payload["target_adjustment"] = adjustment.to_dict()
                payload["scalar_metrics_invariant"] = (
                    adjustment.exact_base_target_reuse
                )
        if isinstance(adjustment, TargetCappedAdjustment):
            payload["lighting_target_mode"] = "target_capped"
        return payload

    def final_baseline_ppfd_csv(self) -> tuple[str, str, bytes]:
        lines = ["x_m,y_m,z_m,ppfd_umol_m2_s\r\n"]
        lines.extend(
            f"{sample.x_m:.17g},{sample.y_m:.17g},{sample.z_m:.17g},"
            f"{sample.ppfd_umol_m2_s:.17g}\r\n"
            for sample in self.samples
        )
        return (
            "ppfd.csv",
            "text/csv; charset=utf-8",
            "".join(lines).encode("utf-8"),
        )

    def validated_ppfd_scatter(self) -> tuple[Mapping[str, object], bytes]:
        canonical_metadata, _canonical_scatter = (
            self.canonical.validated_ppfd_scatter()
        )
        scatter = b"".join(
            struct.pack(
                "<fff",
                float(sample.x_m),
                float(sample.y_m),
                float(sample.ppfd_umol_m2_s),
            )
            for sample in self.samples
        )
        metadata = json.loads(json.dumps(canonical_metadata))
        metadata["requested_orientation"] = self.presentation_metadata()
        record = metadata.get("scatter")
        if isinstance(record, dict):
            record["sha256"] = hashlib.sha256(scatter).hexdigest()
            record["byte_length"] = len(scatter)
            record["count"] = len(self.samples)
        return metadata, scatter

    def heatmap_payloads(self) -> dict[str, object]:
        """Return canonical pixels without a requested-dimension transform."""

        canonical_pixels_reused = True
        adjustment = getattr(self.canonical, "adjustment", None)
        if adjustment is not None:
            canonical_pixels_reused = all(
                self.canonical.payloads[name]
                == self.canonical.base.payloads[name]
                for name in ("heatmap", "heatmap_overlay")
            )
        return {
            "plain_png": self.canonical.payloads["heatmap"],
            "overlay_png": self.canonical.payloads["heatmap_overlay"],
            "canonical_pixels_reused": canonical_pixels_reused,
            "presentation": self.presentation_metadata(),
        }

    def plant_instance_translations(self) -> bytes:
        return self.canonical.payloads["viewer_instances"]

    def fixture_transform_payloads(self) -> dict[str, bytes]:
        return {
            name: data
            for name, data in self.canonical.payloads.items()
            if name.startswith("fixture_transforms_")
        }

    def natural_fit_layout(self) -> Mapping[str, object]:
        payload = _json_payload(self.canonical.payloads["natural_fit_layout"])
        payload["requested_orientation"] = self.presentation_metadata()
        return payload

    def viewer_scene(self) -> Mapping[str, object]:
        """Return canonical geometry plus request-order provenance."""

        scene = json.loads(json.dumps(self.canonical.viewer_scene))
        scene["requested_orientation"] = self.presentation_metadata()
        return scene

    def result_payload(self) -> dict[str, object]:
        result = self.canonical.result_payload()
        result["canonical_bundle_identity_sha256"] = (
            self.canonical_bundle_identity_sha256
        )
        result["presentation_identity_sha256"] = (
            self.presentation_identity_sha256
        )
        result["fspm_target_tolerance"] = (
            self.fspm_target_tolerance_umol_m2_s
        )
        result["requested_orientation"] = self.presentation_metadata()
        return result


def resolve_fixed_case(
    request: RunRequest,
    *,
    plan: PlaybackPlan | None = None,
    target_ppfd_umol_m2_s: object = _TARGET_UNSET,
    lighting_target_mode: str | None = None,
) -> PlaybackCase:
    """Resolve only a complete exact fixed-plan request to one canonical case."""

    fixed_plan = plan or build_committed_playback_plan()
    _resolve_playback_target(request, target_ppfd_umol_m2_s)
    _resolve_playback_mode(request, lighting_target_mode)
    requested_domain = canonical_room_domain(
        request.room_length_ft, request.room_width_ft
    )
    layout_variant = _layout_variant(request)
    candidates = [
        case
        for case in fixed_plan.cases
        if case.system_id == request.system
        and case.layout_variant == layout_variant
        and case.aisle_enabled is request.aisle_mode
        and case.canonical_domain == requested_domain
    ]
    if len(candidates) != 1:
        raise ValueError("request does not resolve to exactly one fixed sweep case.")
    case = candidates[0]
    _validate_request_equivalence(request, case.request)
    return case


def load_fixed_playback(
    output_root: str | Path,
    request: RunRequest,
    *,
    plan: PlaybackPlan | None = None,
    target_ppfd_umol_m2_s: object = _TARGET_UNSET,
    lighting_target_mode: str | None = None,
    fspm_target_tolerance: object = (
        DEFAULT_PRECOMPUTED_FSPM_TARGET_TOLERANCE_UMOL_M2_S
    ),
) -> FixedPlaybackResolution:
    """Freshly validate one exact canonical bundle, then orient it for request."""

    fixed_plan = plan or build_committed_playback_plan()
    case = resolve_fixed_case(
        request,
        plan=fixed_plan,
        target_ppfd_umol_m2_s=target_ppfd_umol_m2_s,
        lighting_target_mode=lighting_target_mode,
    )
    path = case.output_path(output_root)
    canonical = load_committed_case_bundle(
        path,
        case,  # type: ignore[arg-type]
        fixed_plan.plan_identity_sha256,
    )
    return resolve_loaded_fixed_playback(
        output_root,
        request,
        canonical,
        plan=fixed_plan,
        target_ppfd_umol_m2_s=target_ppfd_umol_m2_s,
        lighting_target_mode=lighting_target_mode,
        fspm_target_tolerance=fspm_target_tolerance,
    )


def resolve_loaded_fixed_playback(
    output_root: str | Path,
    request: RunRequest,
    canonical: CompactPlayback,
    *,
    plan: PlaybackPlan | None = None,
    target_ppfd_umol_m2_s: object = _TARGET_UNSET,
    lighting_target_mode: str | None = None,
    fspm_target_tolerance: object = (
        DEFAULT_PRECOMPUTED_FSPM_TARGET_TOLERANCE_UMOL_M2_S
    ),
) -> FixedPlaybackResolution:
    """Build a requested view over one already authenticated base bundle.

    Public serving uses this boundary to share immutable validated bundle data
    across target-derived views.  The bound path and exact fixed case are still
    checked before any derived playback is constructed.
    """

    if not isinstance(canonical, CompactPlayback):
        raise TypeError("loaded fixed playback requires a compact base bundle.")
    fixed_plan = plan or build_committed_playback_plan()
    requested_target = _resolve_playback_target(
        request, target_ppfd_umol_m2_s
    )
    requested_mode = _resolve_playback_mode(request, lighting_target_mode)
    requested_tolerance = validate_fspm_target_tolerance(
        fspm_target_tolerance
    )
    case = resolve_fixed_case(
        request,
        plan=fixed_plan,
        target_ppfd_umol_m2_s=target_ppfd_umol_m2_s,
        lighting_target_mode=lighting_target_mode,
    )
    path = case.output_path(output_root).expanduser().resolve()
    if canonical.bundle_path != path:
        raise ValueError("authenticated base bundle does not match the fixed case.")
    expected_bundle_identity = getattr(
        case, "bundle_identity_sha256", None
    )
    if (
        expected_bundle_identity is not None
        and canonical.manifest.get("bundle_identity_sha256")
        != expected_bundle_identity
    ):
        raise ValueError("authenticated base bundle identity is not allowlisted.")
    authenticates_manifest = getattr(case, "authenticates_manifest", None)
    if callable(authenticates_manifest) and not authenticates_manifest(
        canonical.manifest
    ):
        raise ValueError("authenticated base bundle case identity is not allowlisted.")
    canonical_view: CanonicalPlayback = canonical
    if requested_target is not None:
        presentation_identity = None
        if requested_mode == "target_capped":
            presentation_identity = _target_capped_presentation_identity(
                canonical,
                case,
                requested_length_ft=request.room_length_ft,
                requested_width_ft=request.room_width_ft,
            )
        run_configuration = canonical.public_payload.get("run_configuration")
        if not isinstance(run_configuration, Mapping):
            raise ValueError("authenticated run configuration is missing.")
        fixed_binding = run_configuration.get("fixed_plan")
        canonical_domain = run_configuration.get("canonical_domain")
        canonical_view = derive_target_adjusted_playback(
            canonical,
            requested_target,
            lighting_target_mode=requested_mode,
            fixed_case_binding=(
                fixed_binding if isinstance(fixed_binding, Mapping) else None
            ),
            canonical_domain=(
                canonical_domain if isinstance(canonical_domain, Mapping) else None
            ),
            presentation_identity_sha256=presentation_identity,
        )
    canonical_view = derive_fspm_tolerance_presentation(
        canonical_view, requested_tolerance
    )
    playback = RequestedCompactPlayback.create(
        canonical_view,
        case,
        requested_length_ft=request.room_length_ft,
        requested_width_ft=request.room_width_ft,
    )
    return FixedPlaybackResolution(
        case=case,
        requested_length_ft=request.room_length_ft,
        requested_width_ft=request.room_width_ft,
        bundle_path=path,
        playback=playback,
    )


def _layout_variant(request: RunRequest) -> str:
    if isinstance(request, ProposedRunRequest):
        return "proposed_reduced_one_ring"
    if isinstance(request, ConventionalRunRequest):
        return str(request.layout_mode)
    if isinstance(request, HpsRunRequest):
        return "fixed_full_output"
    raise ValueError("unsupported fixed playback request variant.")


def _validate_request_equivalence(actual: RunRequest, expected: RunRequest) -> None:
    excluded = {"room_length_ft", "room_width_ft", "active_domain"}
    if actual.system in {PROPOSED_SYSTEM_ID, CONVENTIONAL_SYSTEM_ID}:
        excluded.add("target_ppfd_umol_m2_s")
    actual_payload = {
        key: value for key, value in actual.to_dict().items() if key not in excluded
    }
    expected_payload = {
        key: value
        for key, value in expected.to_dict().items()
        if key not in excluded
    }
    if actual_payload != expected_payload:
        raise ValueError(
            "requested playback configuration is not an exact fixed-plan case."
        )


def _resolve_playback_target(
    request: RunRequest, target_ppfd_umol_m2_s: object
) -> float | None:
    if request.system == HPS_SYSTEM_ID:
        if target_ppfd_umol_m2_s is not _TARGET_UNSET:
            raise ValueError(
                "HPS fixed-output playback rejects target overrides."
            )
        return None
    if request.system not in {PROPOSED_SYSTEM_ID, CONVENTIONAL_SYSTEM_ID}:
        raise ValueError("unsupported fixed playback target-control system.")
    value = (
        request.target_ppfd_umol_m2_s  # type: ignore[union-attr]
        if target_ppfd_umol_m2_s is _TARGET_UNSET
        else target_ppfd_umol_m2_s
    )
    return validate_requested_target_ppfd(value)


def _resolve_playback_mode(
    request: RunRequest, lighting_target_mode: str | None
) -> str | None:
    if request.system == HPS_SYSTEM_ID:
        if lighting_target_mode is not None:
            raise ValueError(
                "HPS fixed-output playback rejects target-policy overrides."
            )
        return None
    mode = (
        request.lighting_target_mode.value  # type: ignore[union-attr]
        if lighting_target_mode is None
        else lighting_target_mode
    )
    if mode not in {"mean_target", "target_capped"}:
        raise ValueError(
            "lighting_target_mode must be mean_target or target_capped."
        )
    return mode


def _target_capped_presentation_identity(
    canonical: CompactPlayback,
    case: PlaybackCase,
    *,
    requested_length_ft: float,
    requested_width_ft: float,
) -> str:
    canonical_room = case.canonical_domain["canonical_aligned_room"]
    frame = RoomCoordinateFrame(
        float(canonical_room["length_x_ft"]) * 0.3048,
        float(canonical_room["width_y_ft"]) * 0.3048,
    )
    preimage = {
        "schema_id": REQUESTED_PLAYBACK_SCHEMA_ID,
        "schema_version": TARGET_CAPPED_REQUESTED_PLAYBACK_SCHEMA_VERSION,
        "canonical_bundle_identity_sha256": str(
            canonical.manifest["bundle_identity_sha256"]
        ),
        "canonical_case_id": case.case_id,
        "requested_room_ft": {
            "length": float(requested_length_ft),
            "width": float(requested_width_ft),
        },
        "canonical_display_room_ft": {
            "length": float(canonical_room["length_x_ft"]),
            "width": float(canonical_room["width_y_ft"]),
        },
        "simulation_to_requested": {
            "rotation_degrees_about_z": 0,
            "determinant": 1,
            "translation_m": [0.0, 0.0, 0.0],
        },
        "coordinate_frame": frame.to_payload(),
        "lighting_target_mode": "target_capped",
    }
    return hashlib.sha256(_canonical_json(preimage)).hexdigest()


def _json_payload(data: bytes) -> dict[str, object]:
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("compact JSON payload root is not an object.")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "FSPM_TOLERANCE_REQUESTED_PLAYBACK_SCHEMA_VERSION",
    "REQUESTED_PLAYBACK_SCHEMA_ID",
    "REQUESTED_PLAYBACK_SCHEMA_VERSION",
    "FixedPlaybackResolution",
    "RequestedCompactPlayback",
    "load_fixed_playback",
    "resolve_loaded_fixed_playback",
    "resolve_fixed_case",
]
