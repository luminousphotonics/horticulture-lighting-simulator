"""Strict discriminated request contracts for local native baseline runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math
from typing import Any, Mapping, TypeAlias

from fspm_optics.fixtures.proposed_cob.source import (
    COB_SOURCE_MODE,
    NATIVE_SOURCE_MODE,
)
from fspm_optics.fixtures.conventional_led.layout import (
    LayoutPolicyName,
    PRACTICAL_LAYOUT_POLICY,
    ROLLING_BENCH_LAYOUT_POLICY,
)
from fspm_optics.geometry.active_domain import ActiveRoomDomain
from fspm_optics.geometry.mounting import MountingGeometry
from fspm_optics.layout.mode import (
    DEFAULT_PROPOSED_LAYOUT_MODE,
    ProposedLayoutMode,
    resolve_proposed_layout_mode,
)
from fspm_optics.layout.ring import (
    DEFAULT_PROPOSED_RING_MODE,
    ProposedRingMode,
    resolve_proposed_ring_mode,
)

PROPOSED_SYSTEM_ID = "proposed"
CONVENTIONAL_SYSTEM_ID = "conventional"
HPS_SYSTEM_ID = "hps"
SUPPORTED_SYSTEM = PROPOSED_SYSTEM_ID
SUPPORTED_SYSTEM_IDS = (
    PROPOSED_SYSTEM_ID,
    CONVENTIONAL_SYSTEM_ID,
    HPS_SYSTEM_ID,
)
SYSTEM_DISPLAY_NAMES = {
    PROPOSED_SYSTEM_ID: "Proposed LED System",
    CONVENTIONAL_SYSTEM_ID: "Conventional LED System",
    HPS_SYSTEM_ID: "1000W HPS System",
}
EMITTED_PPF_BOUNDARIES = {
    PROPOSED_SYSTEM_ID: {
        "id": "completed_fixture_aperture",
        "description": "Completed-aperture effective fixture output.",
    },
    CONVENTIONAL_SYSTEM_ID: {
        "id": "fixture_output_after_source_side_dimming",
        "description": "Effective fixture output after source-side dimming.",
    },
    HPS_SYSTEM_ID: {
        "id": "fixture_output_at_fixed_full_output",
        "description": "Fixed full-output fixture PPF.",
    },
}
SUPPORTED_QUALITIES = ("direct", "standard", "quality", "rigorous")
DEFAULT_FSPM_TOLERANCE_UMOL_M2_S = 75.0
DEFAULT_LED_MOUNTING_HEIGHT_IN = 18.0
DEFAULT_HPS_MOUNTING_HEIGHT_IN = 24.0
ANALYSIS_SCOPE_SCHEMA_ID = "fspm-optics.analysis-scope"
ANALYSIS_SCOPE_SCHEMA_VERSION = 1
NATIVE_PROPOSED_SPECTRAL_BASIS_ID = "native_proposed"
CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_ID = "conventional_led_control"


class AnalysisScope(StrEnum):
    """Explicit logical scientific scope for one application run."""

    BASELINE_PPFD = "baseline_ppfd"
    BASELINE_PLUS_MULTISPECTRAL_FSPM = "baseline_plus_multispectral_fspm"


class ProposedSpectralBasis(StrEnum):
    """Proposed-only relative spectrum used by multispectral Stage B."""

    NATIVE_PROPOSED = NATIVE_PROPOSED_SPECTRAL_BASIS_ID
    CONVENTIONAL_LED_CONTROL = CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_ID


class ProposedControlMode(StrEnum):
    """Stable Proposed Stage A source-control policies."""

    BASIS_MATRIX_OPTIMIZED = "basis_matrix_optimized"
    UNIFORM_MODULE_DIMMING = "uniform_module_dimming"


class LightingTargetMode(StrEnum):
    """Stable Stage A lighting-target policies for dimmable LED systems."""

    MEAN_TARGET = "mean_target"
    TARGET_CAPPED = "target_capped"


class ProposedSourceMode(StrEnum):
    """Proposed-only internal emitting geometry and angular law."""

    NATIVE_SMD = NATIVE_SOURCE_MODE
    COB_SOURCE_SHAPE_SURROGATE = COB_SOURCE_MODE


PROPOSED_SPECTRAL_BASIS_LABELS = {
    ProposedSpectralBasis.NATIVE_PROPOSED: "Native Proposed spectrum",
    ProposedSpectralBasis.CONVENTIONAL_LED_CONTROL: (
        "Conventional LED spectrum (controlled A/B)"
    ),
}


ANALYSIS_SCOPE_LABELS = {
    AnalysisScope.BASELINE_PPFD: "Baseline PPFD",
    AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM: (
        "Baseline + Multispectral FSPM"
    ),
}
ANALYSIS_SCOPE_DESCRIPTIONS = {
    AnalysisScope.BASELINE_PPFD: (
        "Plant-free horizontal PPFD, uniformity metrics, CSV, heatmaps, "
        "fixture overlays, and 3D scatter."
    ),
    AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM: (
        "Runs the same plant-free baseline first, then performs four-band PAR "
        "transport through the populated plant scene and publishes "
        "authoritative surface-light aggregation. Far-red is optional."
    ),
}

_COMMON_FIELDS = {
    "system",
    "room_length_ft",
    "room_width_ft",
    "quality",
    "analysis_scope",
    "fspm_target_mode",
    "fspm_target_ppfd",
    "fspm_target_tolerance",
    "lighting_target_mode",
    "mounting_height_in",
    "include_far_red",
    "aisle_mode",
}


class RequestValidationError(ValueError):
    """A browser request failed the closed local-run contract."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {
            "code": "invalid_request",
            "field": self.field,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ProposedRunRequest:
    system: str
    target_ppfd_umol_m2_s: float
    room_length_ft: float
    room_width_ft: float
    quality: str
    fspm_target_mode: str
    fspm_target_override_umol_m2_s: float | None
    fspm_target_tolerance_umol_m2_s: float
    lighting_target_mode: LightingTargetMode = LightingTargetMode.MEAN_TARGET
    aisle_mode: bool = False
    proposed_layout_mode: ProposedLayoutMode = DEFAULT_PROPOSED_LAYOUT_MODE
    proposed_ring_mode: ProposedRingMode = DEFAULT_PROPOSED_RING_MODE
    analysis_scope: AnalysisScope = AnalysisScope.BASELINE_PPFD
    include_far_red: bool | None = None
    mounting_height_in: float = DEFAULT_LED_MOUNTING_HEIGHT_IN
    spectral_basis: ProposedSpectralBasis = ProposedSpectralBasis.NATIVE_PROPOSED
    control_mode: ProposedControlMode = ProposedControlMode.BASIS_MATRIX_OPTIMIZED
    source_mode: ProposedSourceMode = ProposedSourceMode.NATIVE_SMD
    mounting_geometry: MountingGeometry = field(init=False, repr=False)
    active_domain: ActiveRoomDomain = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_request_instance(self, expected_system=PROPOSED_SYSTEM_ID)

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        proposed_layout_mode: ProposedLayoutMode | str = DEFAULT_PROPOSED_LAYOUT_MODE,
    ) -> "ProposedRunRequest":
        values = _parse_led_payload(
            payload,
            expected_system=PROPOSED_SYSTEM_ID,
            allowed_fields=_COMMON_FIELDS
            | {
                "target_ppfd",
                "spectral_basis",
                "proposed_control_mode",
                "proposed_ring_mode",
                "proposed_source_mode",
            },
        )
        values["spectral_basis"] = _proposed_spectral_basis(
            payload.get("spectral_basis"),
            analysis_scope=values["analysis_scope"],
        )
        values["control_mode"] = _proposed_control_mode(
            payload.get("proposed_control_mode")
        )
        values["source_mode"] = _proposed_source_mode(
            payload.get("proposed_source_mode")
        )
        values["proposed_ring_mode"] = _proposed_ring_mode(
            payload.get("proposed_ring_mode")
        )
        values["proposed_layout_mode"] = resolve_proposed_layout_mode(
            proposed_layout_mode
        )
        return cls(**values)

    def to_dict(self) -> dict[str, object]:
        return _request_dict(self, target_ppfd=self.target_ppfd_umol_m2_s)


@dataclass(frozen=True, slots=True)
class ConventionalRunRequest:
    system: str
    target_ppfd_umol_m2_s: float
    room_length_ft: float
    room_width_ft: float
    quality: str
    fspm_target_mode: str
    fspm_target_override_umol_m2_s: float | None
    fspm_target_tolerance_umol_m2_s: float
    lighting_target_mode: LightingTargetMode = LightingTargetMode.MEAN_TARGET
    aisle_mode: bool = False
    layout_mode: LayoutPolicyName = PRACTICAL_LAYOUT_POLICY
    analysis_scope: AnalysisScope = AnalysisScope.BASELINE_PPFD
    include_far_red: bool | None = None
    mounting_height_in: float = DEFAULT_LED_MOUNTING_HEIGHT_IN
    mounting_geometry: MountingGeometry = field(init=False, repr=False)
    active_domain: ActiveRoomDomain = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_request_instance(self, expected_system=CONVENTIONAL_SYSTEM_ID)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ConventionalRunRequest":
        values = _parse_led_payload(
            payload,
            expected_system=CONVENTIONAL_SYSTEM_ID,
            allowed_fields=_COMMON_FIELDS | {"target_ppfd", "layout_mode"},
        )
        values["layout_mode"] = _conventional_layout_mode(
            payload.get("layout_mode")
        )
        return cls(**values)

    def to_dict(self) -> dict[str, object]:
        return _request_dict(self, target_ppfd=self.target_ppfd_umol_m2_s)


@dataclass(frozen=True, slots=True)
class HpsRunRequest:
    system: str
    room_length_ft: float
    room_width_ft: float
    quality: str
    fspm_target_mode: str
    fspm_target_override_umol_m2_s: float | None
    fspm_target_tolerance_umol_m2_s: float
    aisle_mode: bool = False
    analysis_scope: AnalysisScope = AnalysisScope.BASELINE_PPFD
    include_far_red: bool | None = None
    mounting_height_in: float = DEFAULT_HPS_MOUNTING_HEIGHT_IN
    mounting_geometry: MountingGeometry = field(init=False, repr=False)
    active_domain: ActiveRoomDomain = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_request_instance(self, expected_system=HPS_SYSTEM_ID)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "HpsRunRequest":
        if not isinstance(payload, Mapping):
            raise RequestValidationError(
                "request", "request body must be a JSON object."
            )
        if "target_ppfd" in payload:
            raise RequestValidationError(
                "target_ppfd",
                "HPS is fixed at full output and rejects a lighting target.",
            )
        if "lighting_target_mode" in payload:
            raise RequestValidationError(
                "lighting_target_mode",
                "HPS is fixed at full output and rejects a lighting target mode.",
            )
        values = _parse_common_payload(
            payload,
            expected_system=HPS_SYSTEM_ID,
            allowed_fields=_COMMON_FIELDS,
        )
        return cls(**values)

    def to_dict(self) -> dict[str, object]:
        return _request_dict(self, target_ppfd=None)


RunRequest: TypeAlias = ProposedRunRequest | ConventionalRunRequest | HpsRunRequest


def parse_run_request(
    payload: Mapping[str, Any],
    *,
    proposed_layout_mode: ProposedLayoutMode | str = DEFAULT_PROPOSED_LAYOUT_MODE,
) -> RunRequest:
    """Discriminate once by the exact stable system identifier."""

    if not isinstance(payload, Mapping):
        raise RequestValidationError("request", "request body must be a JSON object.")
    if "system" not in payload:
        raise RequestValidationError("system", "system discriminator is required.")
    system = payload.get("system")
    if (
        payload.get("layout_mode") == ROLLING_BENCH_LAYOUT_POLICY
        and system != CONVENTIONAL_SYSTEM_ID
    ):
        raise RequestValidationError(
            "layout_mode",
            "Rolling Bench (rolling_bench) is supported only for Conventional "
            "LED System.",
        )
    if system == PROPOSED_SYSTEM_ID:
        return ProposedRunRequest.from_payload(
            payload,
            proposed_layout_mode=proposed_layout_mode,
        )
    if system == CONVENTIONAL_SYSTEM_ID:
        return ConventionalRunRequest.from_payload(payload)
    if system == HPS_SYSTEM_ID:
        return HpsRunRequest.from_payload(payload)
    raise RequestValidationError(
        "system",
        "system must be exactly proposed, conventional, or hps.",
    )


def _parse_led_payload(
    payload: Mapping[str, Any],
    *,
    expected_system: str,
    allowed_fields: set[str] | None = None,
) -> dict[str, object]:
    values = _parse_common_payload(
        payload,
        expected_system=expected_system,
        allowed_fields=(
            _COMMON_FIELDS | {"target_ppfd"}
            if allowed_fields is None
            else allowed_fields
        ),
    )
    values["target_ppfd_umol_m2_s"] = _positive(
        "target_ppfd", payload.get("target_ppfd")
    )
    values["lighting_target_mode"] = _lighting_target_mode(
        payload.get("lighting_target_mode")
    )
    return values


def _parse_common_payload(
    payload: Mapping[str, Any],
    *,
    expected_system: str,
    allowed_fields: set[str],
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise RequestValidationError("request", "request body must be a JSON object.")
    extras = sorted(str(key) for key in payload if key not in allowed_fields)
    if extras:
        raise RequestValidationError(
            "request",
            "unsupported request fields: " + ", ".join(extras),
        )
    system = payload.get("system")
    if system != expected_system:
        raise RequestValidationError(
            "system", f"system must be exactly {expected_system}."
        )
    length = _room_dimension("room_length_ft", payload.get("room_length_ft"))
    width = _room_dimension("room_width_ft", payload.get("room_width_ft"))
    quality = payload.get("quality", "standard")
    if not isinstance(quality, str) or quality not in SUPPORTED_QUALITIES:
        raise RequestValidationError(
            "quality",
            "quality must be one of direct, standard, quality, or rigorous.",
        )
    mode = payload.get("fspm_target_mode", "automatic")
    if not isinstance(mode, str) or mode not in {"automatic", "override"}:
        raise RequestValidationError(
            "fspm_target_mode", "FSPM target mode must be automatic or override."
        )
    raw_override = payload.get("fspm_target_ppfd")
    override = None
    if mode == "override":
        override = _positive("fspm_target_ppfd", raw_override)
    elif "fspm_target_ppfd" in payload:
        raise RequestValidationError(
            "fspm_target_ppfd",
            "automatic FSPM target mode must not include an override.",
        )
    tolerance = _positive(
        "fspm_target_tolerance",
        payload.get(
            "fspm_target_tolerance", DEFAULT_FSPM_TOLERANCE_UMOL_M2_S
        ),
    )
    default_mounting_height_in = (
        DEFAULT_HPS_MOUNTING_HEIGHT_IN
        if expected_system == HPS_SYSTEM_ID
        else DEFAULT_LED_MOUNTING_HEIGHT_IN
    )
    mounting_height_in = payload.get(
        "mounting_height_in", default_mounting_height_in
    )
    analysis_scope = _analysis_scope(payload.get("analysis_scope"))
    include_far_red: bool | None = None
    if analysis_scope is AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM:
        include_far_red = payload.get("include_far_red", False)
        if not isinstance(include_far_red, bool):
            raise RequestValidationError(
                "include_far_red",
                "include_far_red must be a Boolean under the multispectral scope.",
            )
    elif "include_far_red" in payload:
        raise RequestValidationError(
            "include_far_red",
            "baseline-only scope must not include include_far_red.",
        )
    aisle_mode = payload.get("aisle_mode", False)
    if not isinstance(aisle_mode, bool):
        raise RequestValidationError(
            "aisle_mode", "aisle_mode must be a Boolean."
        )
    return {
        "system": expected_system,
        "room_length_ft": length,
        "room_width_ft": width,
        "quality": quality,
        "analysis_scope": analysis_scope,
        "include_far_red": include_far_red,
        "fspm_target_mode": mode,
        "fspm_target_override_umol_m2_s": override,
        "fspm_target_tolerance_umol_m2_s": tolerance,
        "mounting_height_in": mounting_height_in,
        "aisle_mode": aisle_mode,
    }


def _request_dict(request: RunRequest, *, target_ppfd: float | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "system": request.system,
        "room_length_ft": request.room_length_ft,
        "room_width_ft": request.room_width_ft,
        "quality": request.quality,
        "analysis_scope": request.analysis_scope.value,
        "mounting_height_in": request.mounting_height_in,
        "aisle_mode": request.aisle_mode,
        "active_domain": request.active_domain.to_payload(),
        "fspm_target": {
            "mode": request.fspm_target_mode,
            "override_umol_m2_s": request.fspm_target_override_umol_m2_s,
            "tolerance_umol_m2_s": request.fspm_target_tolerance_umol_m2_s,
        },
    }
    if target_ppfd is not None:
        payload["target_ppfd_umol_m2_s"] = target_ppfd
        payload["lighting_target_mode"] = request.lighting_target_mode.value
    if (
        isinstance(request, ConventionalRunRequest)
        and request.layout_mode != PRACTICAL_LAYOUT_POLICY
    ):
        payload["layout_mode"] = request.layout_mode
    if isinstance(request, ProposedRunRequest):
        payload["proposed_layout_mode"] = request.proposed_layout_mode.value
        payload["proposed_ring_mode"] = request.proposed_ring_mode.value
        payload["proposed_control_mode"] = request.control_mode.value
        payload["proposed_source_mode"] = request.source_mode.value
    if request.analysis_scope is AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM:
        payload["include_far_red"] = request.include_far_red
        if isinstance(request, ProposedRunRequest):
            payload["spectral_basis"] = request.spectral_basis.value
    return payload


def _validate_request_instance(
    request: RunRequest,
    *,
    expected_system: str,
) -> None:
    """Keep directly constructed typed requests as strict as parsed requests."""

    if request.system != expected_system:
        raise RequestValidationError(
            "system", f"system must be exactly {expected_system}."
        )
    if hasattr(request, "target_ppfd_umol_m2_s"):
        object.__setattr__(
            request,
            "target_ppfd_umol_m2_s",
            _positive(
                "target_ppfd",
                getattr(request, "target_ppfd_umol_m2_s"),
            ),
        )
        object.__setattr__(
            request,
            "lighting_target_mode",
            _lighting_target_mode(
                getattr(request, "lighting_target_mode", None)
            ),
        )
    object.__setattr__(
        request,
        "room_length_ft",
        _room_dimension("room_length_ft", request.room_length_ft),
    )
    object.__setattr__(
        request,
        "room_width_ft",
        _room_dimension("room_width_ft", request.room_width_ft),
    )
    if not isinstance(request.aisle_mode, bool):
        raise RequestValidationError(
            "aisle_mode", "aisle_mode must be a Boolean."
        )
    try:
        active_domain = ActiveRoomDomain.from_feet(
            request.room_length_ft,
            request.room_width_ft,
            enabled=request.aisle_mode,
        )
    except ValueError as exc:
        raise RequestValidationError("aisle_mode", str(exc)) from exc
    object.__setattr__(request, "active_domain", active_domain)
    try:
        mounting = MountingGeometry.resolve(request.mounting_height_in)
    except ValueError as exc:
        raise RequestValidationError("mounting_height_in", str(exc)) from exc
    object.__setattr__(request, "mounting_height_in", mounting.mounting_height_in)
    object.__setattr__(request, "mounting_geometry", mounting)
    if (
        not isinstance(request.quality, str)
        or request.quality not in SUPPORTED_QUALITIES
    ):
        raise RequestValidationError(
            "quality",
            "quality must be one of direct, standard, quality, or rigorous.",
        )
    object.__setattr__(
        request,
        "analysis_scope",
        _analysis_scope(request.analysis_scope),
    )
    include_far_red = request.include_far_red
    if request.analysis_scope is AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM:
        if include_far_red is None:
            include_far_red = False
        if not isinstance(include_far_red, bool):
            raise RequestValidationError(
                "include_far_red",
                "include_far_red must be a Boolean under the multispectral scope.",
            )
    elif include_far_red is not None:
        raise RequestValidationError(
            "include_far_red",
            "baseline-only scope must not include include_far_red.",
        )
    object.__setattr__(request, "include_far_red", include_far_red)
    if isinstance(request, ProposedRunRequest):
        object.__setattr__(
            request,
            "proposed_layout_mode",
            resolve_proposed_layout_mode(request.proposed_layout_mode),
        )
        object.__setattr__(
            request,
            "proposed_ring_mode",
            _proposed_ring_mode(request.proposed_ring_mode),
        )
        if (
            request.proposed_ring_mode is ProposedRingMode.REDUCED_ONE_RING
            and request.proposed_layout_mode
            is not ProposedLayoutMode.STANDALONE_MODULES
        ):
            raise RequestValidationError(
                "proposed_ring_mode",
                "reduced_one_ring is supported only with the standalone_modules "
                "Proposed physical-composition mode.",
            )
        object.__setattr__(
            request,
            "spectral_basis",
            _proposed_spectral_basis(
                request.spectral_basis,
                analysis_scope=request.analysis_scope,
            ),
        )
        object.__setattr__(
            request,
            "control_mode",
            _proposed_control_mode(request.control_mode),
        )
        object.__setattr__(
            request,
            "source_mode",
            _proposed_source_mode(request.source_mode),
        )
        if (
            request.source_mode is ProposedSourceMode.COB_SOURCE_SHAPE_SURROGATE
            and request.spectral_basis
            is not ProposedSpectralBasis.NATIVE_PROPOSED
        ):
            raise RequestValidationError(
                "spectral_basis",
                "COB Mode fixes the native Proposed SPD and five-band fractions.",
            )
    if isinstance(request, ConventionalRunRequest):
        object.__setattr__(
            request,
            "layout_mode",
            _conventional_layout_mode(request.layout_mode),
        )
    if (
        not isinstance(request.fspm_target_mode, str)
        or request.fspm_target_mode not in {"automatic", "override"}
    ):
        raise RequestValidationError(
            "fspm_target_mode", "FSPM target mode must be automatic or override."
        )
    override = request.fspm_target_override_umol_m2_s
    if request.fspm_target_mode == "automatic":
        if override is not None:
            raise RequestValidationError(
                "fspm_target_ppfd",
                "automatic FSPM target mode must not include an override.",
            )
    else:
        override = _positive("fspm_target_ppfd", override)
    object.__setattr__(request, "fspm_target_override_umol_m2_s", override)
    object.__setattr__(
        request,
        "fspm_target_tolerance_umol_m2_s",
        _positive(
            "fspm_target_tolerance",
            request.fspm_target_tolerance_umol_m2_s,
        ),
    )


def _positive(field: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RequestValidationError(field, f"{field} must be a finite positive number.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise RequestValidationError(field, f"{field} must be a finite positive number.")
    return number


def _analysis_scope(value: object) -> AnalysisScope:
    if value is None:
        return AnalysisScope.BASELINE_PPFD
    if isinstance(value, AnalysisScope):
        return value
    if isinstance(value, str):
        try:
            return AnalysisScope(value)
        except ValueError:
            pass
    raise RequestValidationError(
        "analysis_scope",
        "analysis_scope must be baseline_ppfd or "
        "baseline_plus_multispectral_fspm.",
    )


def _lighting_target_mode(value: object) -> LightingTargetMode:
    if value is None:
        return LightingTargetMode.MEAN_TARGET
    if isinstance(value, LightingTargetMode):
        return value
    if isinstance(value, str):
        try:
            return LightingTargetMode(value)
        except ValueError:
            pass
    raise RequestValidationError(
        "lighting_target_mode",
        "lighting_target_mode must be mean_target or target_capped.",
    )


def _proposed_spectral_basis(
    value: object,
    *,
    analysis_scope: object,
) -> ProposedSpectralBasis:
    if value is None:
        return ProposedSpectralBasis.NATIVE_PROPOSED
    if analysis_scope is not AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM:
        if value is ProposedSpectralBasis.NATIVE_PROPOSED:
            return ProposedSpectralBasis.NATIVE_PROPOSED
        raise RequestValidationError(
            "spectral_basis",
            "baseline-only Proposed scope must not include spectral_basis.",
        )
    if isinstance(value, ProposedSpectralBasis):
        return value
    if isinstance(value, str):
        try:
            return ProposedSpectralBasis(value)
        except ValueError:
            pass
    raise RequestValidationError(
        "spectral_basis",
        "spectral_basis must be native_proposed or conventional_led_control.",
    )


def _proposed_control_mode(value: object) -> ProposedControlMode:
    if value is None:
        return ProposedControlMode.BASIS_MATRIX_OPTIMIZED
    if isinstance(value, ProposedControlMode):
        return value
    if isinstance(value, str):
        try:
            return ProposedControlMode(value)
        except ValueError:
            pass
    raise RequestValidationError(
        "proposed_control_mode",
        "proposed_control_mode must be basis_matrix_optimized or "
        "uniform_module_dimming.",
    )


def _proposed_source_mode(value: object) -> ProposedSourceMode:
    if value is None:
        return ProposedSourceMode.NATIVE_SMD
    if isinstance(value, ProposedSourceMode):
        return value
    if isinstance(value, str):
        try:
            return ProposedSourceMode(value)
        except ValueError:
            pass
    raise RequestValidationError(
        "proposed_source_mode",
        "proposed_source_mode must be native_smd or "
        "cob_source_shape_surrogate.",
    )


def _proposed_ring_mode(value: object) -> ProposedRingMode:
    try:
        return resolve_proposed_ring_mode(value)
    except ValueError as exc:
        raise RequestValidationError(
            "proposed_ring_mode",
            "proposed_ring_mode must be exactly full or reduced_one_ring.",
        ) from exc


def _conventional_layout_mode(value: object) -> LayoutPolicyName:
    if value is None:
        return PRACTICAL_LAYOUT_POLICY
    if value in (PRACTICAL_LAYOUT_POLICY, ROLLING_BENCH_LAYOUT_POLICY):
        return value  # type: ignore[return-value]
    raise RequestValidationError(
        "layout_mode",
        "Conventional layout_mode must be practical or rolling_bench.",
    )


def _room_dimension(field: str, value: object) -> float:
    return _positive(field, value)


__all__ = [
    "ANALYSIS_SCOPE_DESCRIPTIONS",
    "ANALYSIS_SCOPE_LABELS",
    "ANALYSIS_SCOPE_SCHEMA_ID",
    "ANALYSIS_SCOPE_SCHEMA_VERSION",
    "AnalysisScope",
    "CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_ID",
    "CONVENTIONAL_SYSTEM_ID",
    "DEFAULT_HPS_MOUNTING_HEIGHT_IN",
    "DEFAULT_LED_MOUNTING_HEIGHT_IN",
    "EMITTED_PPF_BOUNDARIES",
    "ConventionalRunRequest",
    "HPS_SYSTEM_ID",
    "HpsRunRequest",
    "PROPOSED_SYSTEM_ID",
    "NATIVE_PROPOSED_SPECTRAL_BASIS_ID",
    "PROPOSED_SPECTRAL_BASIS_LABELS",
    "ProposedControlMode",
    "ProposedSourceMode",
    "ProposedLayoutMode",
    "ProposedRingMode",
    "ProposedSpectralBasis",
    "ProposedRunRequest",
    "RequestValidationError",
    "RunRequest",
    "SUPPORTED_QUALITIES",
    "SUPPORTED_SYSTEM_IDS",
    "SYSTEM_DISPLAY_NAMES",
    "parse_run_request",
]
