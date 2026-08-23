"""Conventional-source-weighted Rex optics and diffuse material planning."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Final

from fspm_optics.fixtures.conventional_led.band_source import (
    CONVENTIONAL_BAND_SOURCE_MODEL_ID,
    ConventionalSpectralSourcePayload,
)
from fspm_optics.fixtures.conventional_led.resources import (
    CONVENTIONAL_SPD_RESOURCE_NAME,
)
from fspm_optics.fixtures.conventional_led.spectral import (
    build_conventional_spectral_distribution,
)
from fspm_optics.optics.rex_material_plan import (
    DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID,
    REX_RADIANCE_MATERIAL_PLAN_CLAIM,
    REX_TRANS_MODEL_ASSUMPTIONS,
    RexRadianceTransIntervalPlan,
    build_rex_radiance_trans_intervals,
)
from fspm_optics.optics.rex_weighting import (
    RexSourceWeightedIntervalSet,
    RexSourceWeightingInput,
    compute_rex_source_weighted_interval_set,
)

CONVENTIONAL_REX_WEIGHTED_ATR_MODEL_ID: Final = (
    "rex_mean_of_treatments_conventional_source_weighted_atr_v1"
)
CONVENTIONAL_REX_MATERIAL_PREFIX: Final = "conventional_rex_leaf_trans"
CONVENTIONAL_REX_OPTICS_LIMITATIONS: Final[tuple[str, ...]] = (
    "Rex coefficients are Conventional-photon-weighted interval averages.",
    "The model is not measured BRDF, BTDF, or BSDF data.",
    "The leaf is an infinitely thin diffuse symmetric energy-partition surface.",
    "No source mass is invented outside actual Conventional SPD support.",
)


@dataclass(frozen=True, slots=True)
class ConventionalRexWeightedAtrPayload:
    spectral_source_payload_id: str
    intervals: RexSourceWeightedIntervalSet
    limitations: tuple[str, ...] = CONVENTIONAL_REX_OPTICS_LIMITATIONS
    optical_payload_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.spectral_source_payload_id:
            raise ValueError("Conventional spectral source payload identity is required.")
        if self.intervals.source.source_model_id != CONVENTIONAL_BAND_SOURCE_MODEL_ID:
            raise ValueError("Conventional Rex optics cannot consume an SMD source.")
        if "smd" in self.intervals.source.source_model_id.lower():
            raise ValueError("SMD source identity is prohibited in Conventional Rex optics.")
        object.__setattr__(
            self,
            "optical_payload_id",
            "conventional-rex-weighted-atr-v1-"
            + _hash_payload(self.scientific_payload()),
        )

    @property
    def scalar_par(self):
        return self.intervals.scalar_par

    @property
    def bands(self):
        return self.intervals.bands

    def band(self, band_id: str):
        return self.intervals.band(band_id)

    def scientific_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "model_id": CONVENTIONAL_REX_WEIGHTED_ATR_MODEL_ID,
            "spectral_source_payload_id": self.spectral_source_payload_id,
            "weighted_intervals": self.intervals.to_payload(),
            "limitations": list(self.limitations),
        }

    def to_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {
            "optical_payload_id": self.optical_payload_id
        }


@dataclass(frozen=True, slots=True)
class ConventionalRexRadianceMaterialPlan:
    optical_payload: ConventionalRexWeightedAtrPayload
    materials: tuple[RexRadianceTransIntervalPlan, ...]
    policy_id: str = DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID
    claim_language: str = REX_RADIANCE_MATERIAL_PLAN_CLAIM
    assumptions: tuple[str, ...] = REX_TRANS_MODEL_ASSUMPTIONS
    material_plan_id: str = field(init=False)

    def __post_init__(self) -> None:
        expected = (self.optical_payload.scalar_par, *self.optical_payload.bands)
        if tuple(item.source_interval for item in self.materials) != expected:
            raise ValueError("Conventional Rex materials must preserve interval order.")
        expected_ids = tuple(
            f"{CONVENTIONAL_REX_MATERIAL_PREFIX}_{item.interval_id}"
            for item in expected
        )
        if tuple(item.material_identifier for item in self.materials) != expected_ids:
            raise ValueError("Conventional Rex material identities are invalid.")
        if self.policy_id != DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID:
            raise ValueError("Conventional Rex material policy is invalid.")
        object.__setattr__(
            self,
            "material_plan_id",
            "conventional-rex-material-plan-v1-"
            + _hash_payload(self.scientific_payload()),
        )

    def material(self, interval_id: str) -> RexRadianceTransIntervalPlan:
        for item in self.materials:
            if item.interval_id == interval_id:
                return item
        raise KeyError(f"unknown Conventional Rex material: {interval_id!r}")

    def scientific_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "policy_id": self.policy_id,
            "claim_language": self.claim_language,
            "assumptions": list(self.assumptions),
            "optical_payload_id": self.optical_payload.optical_payload_id,
            "materials": [item.to_dict() for item in self.materials],
        }

    def to_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {"material_plan_id": self.material_plan_id}


def build_conventional_rex_weighted_atr_payload(
    spectral_source: ConventionalSpectralSourcePayload,
) -> ConventionalRexWeightedAtrPayload:
    """Weight the packaged Rex model by the approved Conventional photon shape."""

    distribution = build_conventional_spectral_distribution()
    absolute_distribution = distribution.photon_distribution.scaled(
        spectral_source.scalar_par_ppf_umol_s_per_fixture
    )
    source = RexSourceWeightingInput(
        source_model_id=CONVENTIONAL_BAND_SOURCE_MODEL_ID,
        normalization_policy=distribution.normalization_policy,
        resource_hashes=((CONVENTIONAL_SPD_RESOURCE_NAME, distribution.resource_sha256),),
        photon_distribution=absolute_distribution,
    )
    intervals = compute_rex_source_weighted_interval_set(source)
    return ConventionalRexWeightedAtrPayload(
        spectral_source_payload_id=spectral_source.source_payload_id,
        intervals=intervals,
    )


def build_conventional_rex_radiance_material_plan(
    optical_payload: ConventionalRexWeightedAtrPayload,
) -> ConventionalRexRadianceMaterialPlan:
    intervals = (optical_payload.scalar_par, *optical_payload.bands)
    materials = build_rex_radiance_trans_intervals(
        intervals,
        material_prefix=CONVENTIONAL_REX_MATERIAL_PREFIX,
    )
    return ConventionalRexRadianceMaterialPlan(
        optical_payload=optical_payload,
        materials=materials,
    )


def format_conventional_rex_weighted_atr_json(
    payload: ConventionalRexWeightedAtrPayload,
) -> str:
    return json.dumps(payload.to_payload(), indent=2, sort_keys=True) + "\n"


def format_conventional_rex_material_plan_json(
    plan: ConventionalRexRadianceMaterialPlan,
) -> str:
    return json.dumps(plan.to_payload(), indent=2, sort_keys=True) + "\n"


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
