"""Strict diffuse-only Rex A/T/R to Radiance ``trans`` material planning.

This module maps the immutable Phase 16 source-weighted coefficient payload to
one grayscale material per isolated transport interval. It does not execute or
orchestrate Radiance.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Final, Mapping, Sequence

from fspm_optics.optics.rex_weighting import (
    WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE,
    AtrCoefficients,
    RexSourceWeightedAtrPayload,
    SourceWeightedAtrInterval,
    build_rex_source_weighted_atr_payload,
)
from fspm_optics.radiance.materials import validate_radiance_identifier

DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID: Final = (
    "diffuse_only_symmetric_thin_leaf_energy_partition"
)
REX_RADIANCE_MATERIAL_PLAN_SCHEMA_VERSION: Final = 1
REX_RADIANCE_MATERIAL_PLAN_PAYLOAD_TYPE: Final = (
    "fspm_optics_rex_radiance_trans_material_plan"
)
REX_RADIANCE_MATERIAL_PLAN_CLAIM: Final = (
    "source-weighted, diffuse, symmetric, thin-leaf energy-partition model"
)
DEFAULT_REX_TRANS_MATERIAL_PREFIX: Final = "rex_leaf_trans"
REX_TRANS_MODEL_ASSUMPTIONS: Final[tuple[str, ...]] = (
    "all_source_weighted_reflectance_is_modeled_as_diffuse",
    "all_source_weighted_transmittance_is_modeled_as_diffuse",
    "front_to_back_optical_behavior_is_symmetric",
    "leaf_geometry_is_one_infinitely_thin_surface",
    "one_grayscale_material_is_used_per_isolated_transport_run",
    "spectral_bands_are_not_packed_into_radiance_rgb_channels",
    "specular_reflectance_is_zero",
    "specular_transmittance_is_zero",
    "surface_roughness_is_zero",
)


@dataclass(frozen=True, slots=True)
class RadianceTransParameters:
    """Seven real arguments of one Radiance ``trans`` primitive."""

    a1: float
    a2: float
    a3: float
    a4: float
    a5: float
    a6: float
    a7: float

    def __post_init__(self) -> None:
        for field_name in ("a1", "a2", "a3", "a4", "a5", "a6", "a7"):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(
                    f"Radiance trans parameter {field_name} must be a finite "
                    "fraction in [0, 1]."
                )
            object.__setattr__(self, field_name, _canonical_zero(float(value)))

    @property
    def values(self) -> tuple[float, float, float, float, float, float, float]:
        return (self.a1, self.a2, self.a3, self.a4, self.a5, self.a6, self.a7)

    @property
    def is_grayscale(self) -> bool:
        return self.a1 == self.a2 == self.a3

    def to_dict(self) -> dict[str, float]:
        return {
            "a1": self.a1,
            "a2": self.a2,
            "a3": self.a3,
            "a4": self.a4,
            "a5": self.a5,
            "a6": self.a6,
            "a7": self.a7,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RadianceTransParameters":
        return cls(**{name: _number(payload, name) for name in _TRANS_ARGUMENT_NAMES})


@dataclass(frozen=True, slots=True)
class RadianceTransReconstruction:
    """Forward energy partition reconstructed from seven ``trans`` arguments."""

    diffuse_reflectance: float
    diffuse_transmittance: float
    specular_reflectance: float
    specular_transmittance: float
    total_reflectance: float
    total_transmittance: float
    absorptance: float
    closure_error: float

    def __post_init__(self) -> None:
        for field_name in _RECONSTRUCTION_FIELD_NAMES:
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"{field_name} must be finite.")
            number = _canonical_zero(float(value))
            if field_name != "closure_error" and not 0.0 <= number <= 1.0:
                raise ValueError(f"{field_name} must be in [0, 1].")
            object.__setattr__(self, field_name, number)
        if abs(self.closure_error) > WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE:
            raise ValueError("reconstructed Radiance trans energy partition does not close.")
        expected_closure_error = (
            self.absorptance
            + self.total_reflectance
            + self.total_transmittance
            - 1.0
        )
        if not math.isclose(
            self.closure_error,
            expected_closure_error,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError(
                "closure_error must equal absorptance + reflectance + "
                "transmittance - 1."
            )
        if not math.isclose(
            self.total_reflectance,
            self.diffuse_reflectance + self.specular_reflectance,
            rel_tol=0.0,
            abs_tol=WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE,
        ):
            raise ValueError("total_reflectance does not match diffuse + specular.")
        if not math.isclose(
            self.total_transmittance,
            self.diffuse_transmittance + self.specular_transmittance,
            rel_tol=0.0,
            abs_tol=WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE,
        ):
            raise ValueError("total_transmittance does not match diffuse + specular.")

    def to_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in _RECONSTRUCTION_FIELD_NAMES}

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "RadianceTransReconstruction":
        return cls(
            **{
                name: _number(payload, name)
                for name in _RECONSTRUCTION_FIELD_NAMES
            }
        )


@dataclass(frozen=True, slots=True)
class RexRadianceTransIntervalPlan:
    """One Phase 16 interval and its exact diffuse-only material mapping."""

    source_interval: SourceWeightedAtrInterval
    material_identifier: str
    parameters: RadianceTransParameters
    reconstruction: RadianceTransReconstruction

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "material_identifier",
            validate_radiance_identifier(self.material_identifier),
        )
        _validate_approved_parameters(self.parameters)
        expected_parameters = map_atr_to_diffuse_trans(self.source_interval.coefficients)
        if self.parameters != expected_parameters:
            raise ValueError(
                f"material parameters do not match source interval "
                f"{self.source_interval.interval_id!r}."
            )
        expected_reconstruction = reconstruct_radiance_trans(self.parameters)
        if self.reconstruction != expected_reconstruction:
            raise ValueError("stored trans reconstruction does not match parameters.")
        _validate_reconstruction_matches_atr(
            self.source_interval.coefficients,
            self.reconstruction,
        )

    @property
    def interval_id(self) -> str:
        return self.source_interval.interval_id

    @property
    def radiance_text(self) -> str:
        return render_radiance_trans_material(
            self.material_identifier,
            self.parameters,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "interval_id": self.interval_id,
            "material_identifier": self.material_identifier,
            "original_atr": self.source_interval.coefficients.to_dict(),
            "radiance_arguments": self.parameters.to_dict(),
            "reconstruction": self.reconstruction.to_dict(),
            "radiance_text": self.radiance_text,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        source_interval: SourceWeightedAtrInterval,
    ) -> "RexRadianceTransIntervalPlan":
        if _string(payload, "interval_id") != source_interval.interval_id:
            raise ValueError("material interval_id does not match Phase 16 interval.")
        original = AtrCoefficients.from_dict(_mapping(payload, "original_atr"))
        if original != source_interval.coefficients:
            raise ValueError("serialized original A/T/R differs from Phase 16.")
        plan = cls(
            source_interval=source_interval,
            material_identifier=_string(payload, "material_identifier"),
            parameters=RadianceTransParameters.from_dict(
                _mapping(payload, "radiance_arguments")
            ),
            reconstruction=RadianceTransReconstruction.from_dict(
                _mapping(payload, "reconstruction")
            ),
        )
        if payload.get("radiance_text") != plan.radiance_text:
            raise ValueError("serialized Radiance material text is not deterministic.")
        return plan


@dataclass(frozen=True, slots=True)
class RexRadianceTransMaterialPlan:
    """Scalar PAR plus five-band strict Rex ``trans`` material plan."""

    phase16_atr: RexSourceWeightedAtrPayload
    materials: tuple[RexRadianceTransIntervalPlan, ...]
    policy_id: str = DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID
    claim_language: str = REX_RADIANCE_MATERIAL_PLAN_CLAIM
    assumptions: tuple[str, ...] = REX_TRANS_MODEL_ASSUMPTIONS

    def __post_init__(self) -> None:
        if self.policy_id != DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID:
            raise ValueError("unexpected Rex Radiance material policy_id.")
        if self.claim_language != REX_RADIANCE_MATERIAL_PLAN_CLAIM:
            raise ValueError("unexpected Rex Radiance material claim language.")
        if self.assumptions != REX_TRANS_MODEL_ASSUMPTIONS:
            raise ValueError("unexpected Rex Radiance material assumptions.")
        expected_intervals = (
            self.phase16_atr.scalar_par,
            *self.phase16_atr.bands,
        )
        if tuple(item.source_interval for item in self.materials) != expected_intervals:
            raise ValueError(
                "material plans must preserve scalar PAR and Phase 16 band order."
            )
        identifiers = [item.material_identifier for item in self.materials]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("material identifiers must be unique.")

    def material(self, interval_id: str) -> RexRadianceTransIntervalPlan:
        for material in self.materials:
            if material.interval_id == interval_id:
                return material
        raise KeyError(f"unknown Rex Radiance material interval: {interval_id!r}")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": REX_RADIANCE_MATERIAL_PLAN_SCHEMA_VERSION,
            "payload_type": REX_RADIANCE_MATERIAL_PLAN_PAYLOAD_TYPE,
            "policy_id": self.policy_id,
            "claim_language": self.claim_language,
            "assumptions": list(self.assumptions),
            "phase16_atr": self.phase16_atr.to_payload(),
            "materials": {
                item.interval_id: item.to_dict() for item in self.materials
            },
        }

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
    ) -> "RexRadianceTransMaterialPlan":
        if _integer(payload, "schema_version") != (
            REX_RADIANCE_MATERIAL_PLAN_SCHEMA_VERSION
        ):
            raise ValueError("unsupported Rex Radiance material plan schema_version.")
        if _string(payload, "payload_type") != REX_RADIANCE_MATERIAL_PLAN_PAYLOAD_TYPE:
            raise ValueError("unexpected Rex Radiance material plan payload_type.")
        raw_assumptions = payload.get("assumptions")
        if not isinstance(raw_assumptions, list) or any(
            not isinstance(item, str) for item in raw_assumptions
        ):
            raise ValueError("material plan assumptions must be a string list.")
        phase16 = RexSourceWeightedAtrPayload.from_payload(
            _mapping(payload, "phase16_atr")
        )
        intervals = (phase16.scalar_par, *phase16.bands)
        raw_materials = _mapping(payload, "materials")
        interval_ids = tuple(item.interval_id for item in intervals)
        if set(raw_materials) != set(interval_ids):
            raise ValueError("material plan must contain scalar PAR and all five bands.")
        return cls(
            phase16_atr=phase16,
            materials=tuple(
                RexRadianceTransIntervalPlan.from_dict(
                    _mapping(raw_materials, interval.interval_id),
                    source_interval=interval,
                )
                for interval in intervals
            ),
            policy_id=_string(payload, "policy_id"),
            claim_language=_string(payload, "claim_language"),
            assumptions=tuple(raw_assumptions),
        )


def map_atr_to_diffuse_trans(
    coefficients: AtrCoefficients,
) -> RadianceTransParameters:
    """Map closed A/T/R to the approved diffuse-only seven arguments."""

    absorptance = _finite_fraction("absorptance", coefficients.absorptance)
    transmittance = _finite_fraction("transmittance", coefficients.transmittance)
    reflectance = _finite_fraction("reflectance", coefficients.reflectance)
    total = math.fsum((absorptance, transmittance, reflectance))
    if not math.isclose(
        total,
        1.0,
        rel_tol=0.0,
        abs_tol=WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE,
    ):
        raise ValueError(
            "A/T/R coefficients must close to 1 within "
            f"{WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE:g}; got {total:.17g}."
        )
    scattered = reflectance + transmittance
    if scattered <= 0.0:
        raise ValueError(
            "R + T must be greater than zero; no perfect-absorber trans policy exists."
        )
    if scattered > 1.0:
        raise ValueError("R + T may not exceed 1.")
    transmitted_fraction = (
        0.0 if transmittance == 0.0 else transmittance / scattered
    )
    parameters = RadianceTransParameters(
        a1=scattered,
        a2=scattered,
        a3=scattered,
        a4=0.0,
        a5=0.0,
        a6=transmitted_fraction,
        a7=0.0,
    )
    reconstruction = reconstruct_radiance_trans(parameters)
    _validate_reconstruction_matches_atr(coefficients, reconstruction)
    return parameters


def reconstruct_radiance_trans(
    parameters: RadianceTransParameters,
) -> RadianceTransReconstruction:
    """Forward-reconstruct the scalar energy partition of gray ``trans`` args."""

    if not parameters.is_grayscale:
        raise ValueError("scalar trans reconstruction requires A1=A2=A3.")
    color = parameters.a1
    remaining = 1.0 - parameters.a4
    diffuse_reflectance = remaining * color * (1.0 - parameters.a6)
    diffuse_transmittance = (
        remaining * color * parameters.a6 * (1.0 - parameters.a7)
    )
    specular_reflectance = parameters.a4
    specular_transmittance = (
        remaining * color * parameters.a6 * parameters.a7
    )
    total_reflectance = diffuse_reflectance + specular_reflectance
    total_transmittance = diffuse_transmittance + specular_transmittance
    absorptance = 1.0 - total_reflectance - total_transmittance
    closure_error = (
        absorptance + total_reflectance + total_transmittance - 1.0
    )
    return RadianceTransReconstruction(
        diffuse_reflectance=diffuse_reflectance,
        diffuse_transmittance=diffuse_transmittance,
        specular_reflectance=specular_reflectance,
        specular_transmittance=specular_transmittance,
        total_reflectance=total_reflectance,
        total_transmittance=total_transmittance,
        absorptance=absorptance,
        closure_error=closure_error,
    )


def build_rex_radiance_trans_material_plan(
    phase16_atr: RexSourceWeightedAtrPayload | None = None,
    *,
    material_prefix: str = DEFAULT_REX_TRANS_MATERIAL_PREFIX,
) -> RexRadianceTransMaterialPlan:
    """Build scalar PAR plus five deterministic grayscale material plans."""

    source = phase16_atr or build_rex_source_weighted_atr_payload()
    intervals = (source.scalar_par, *source.bands)
    materials = build_rex_radiance_trans_intervals(
        intervals,
        material_prefix=material_prefix,
    )
    return RexRadianceTransMaterialPlan(
        phase16_atr=source,
        materials=materials,
    )


def build_rex_radiance_trans_intervals(
    intervals: Sequence[SourceWeightedAtrInterval],
    *,
    material_prefix: str,
) -> tuple[RexRadianceTransIntervalPlan, ...]:
    """Map any ordered source-weighted Rex intervals through the approved model."""

    ordered = tuple(intervals)
    if not ordered or len({item.interval_id for item in ordered}) != len(ordered):
        raise ValueError("Rex material intervals must be non-empty and unique.")
    prefix = validate_radiance_identifier(material_prefix)
    materials: list[RexRadianceTransIntervalPlan] = []
    for interval in ordered:
        parameters = map_atr_to_diffuse_trans(interval.coefficients)
        materials.append(
            RexRadianceTransIntervalPlan(
                source_interval=interval,
                material_identifier=validate_radiance_identifier(
                    f"{prefix}_{interval.interval_id}"
                ),
                parameters=parameters,
                reconstruction=reconstruct_radiance_trans(parameters),
            )
        )
    return tuple(materials)


def render_radiance_trans_material(
    material_identifier: str,
    parameters: RadianceTransParameters,
) -> str:
    """Render one deterministic, exactly round-trippable material definition."""

    identifier = validate_radiance_identifier(material_identifier)
    arguments = " ".join(_format_radiance_number(value) for value in parameters.values)
    return f"void trans {identifier}\n0\n0\n7 {arguments}\n"


def format_rex_radiance_trans_material_plan_json(
    plan: RexRadianceTransMaterialPlan,
) -> str:
    return json.dumps(plan.to_payload(), indent=2, sort_keys=True) + "\n"


def write_rex_radiance_trans_material_plan_json(
    path: str | Path,
    plan: RexRadianceTransMaterialPlan,
) -> Path:
    output = Path(path)
    output.write_text(
        format_rex_radiance_trans_material_plan_json(plan),
        encoding="utf-8",
    )
    return output


def read_rex_radiance_trans_material_plan_json(
    path: str | Path,
) -> RexRadianceTransMaterialPlan:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Rex Radiance material plan JSON not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Rex Radiance material plan JSON is malformed: {source}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Rex Radiance material plan JSON root must be an object.")
    return RexRadianceTransMaterialPlan.from_payload(raw)


def _validate_approved_parameters(parameters: RadianceTransParameters) -> None:
    if not parameters.is_grayscale:
        raise ValueError("approved Rex trans parameters must be grayscale.")
    if (parameters.a4, parameters.a5, parameters.a7) != (0.0, 0.0, 0.0):
        raise ValueError(
            "approved Rex trans parameters require Rs=roughness=Ts=0."
        )


def _validate_reconstruction_matches_atr(
    coefficients: AtrCoefficients,
    reconstruction: RadianceTransReconstruction,
) -> None:
    differences = {
        "absorptance": abs(reconstruction.absorptance - coefficients.absorptance),
        "transmittance": abs(
            reconstruction.total_transmittance - coefficients.transmittance
        ),
        "reflectance": abs(
            reconstruction.total_reflectance - coefficients.reflectance
        ),
    }
    if any(
        difference > WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE
        for difference in differences.values()
    ):
        details = ", ".join(
            f"{name}={value:.17g}" for name, value in differences.items()
        )
        raise ValueError(f"Radiance trans reconstruction differs from A/T/R: {details}.")


def _finite_fraction(name: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError(f"{name} must be a finite fraction in [0, 1].")
    return _canonical_zero(float(value))


def _format_radiance_number(value: float) -> str:
    return format(_canonical_zero(value), ".17g")


def _canonical_zero(value: float) -> float:
    return 0.0 if value == 0.0 else value


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object.")
    return value


def _string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _integer(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer.")
    return value


def _number(payload: Mapping[str, Any], key: str) -> float:
    value = payload.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{key} must be finite and numeric.")
    return _canonical_zero(float(value))


_TRANS_ARGUMENT_NAMES: Final = ("a1", "a2", "a3", "a4", "a5", "a6", "a7")
_RECONSTRUCTION_FIELD_NAMES: Final = (
    "diffuse_reflectance",
    "diffuse_transmittance",
    "specular_reflectance",
    "specular_transmittance",
    "total_reflectance",
    "total_transmittance",
    "absorptance",
    "closure_error",
)
