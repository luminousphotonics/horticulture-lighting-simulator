"""HPS-photon-weighted Rex optics and diffuse material identity adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Final

from fspm_optics.fixtures.hps.band_source import (
    HPS_BAND_SOURCE_MODEL_ID,
    HpsSpectralSourcePayload,
)
from fspm_optics.fixtures.hps.resources import HPS_SPD_RESOURCE_NAME
from fspm_optics.fixtures.hps.profile import HPS_INITIAL_LAMP_PAR_PPF_UMOL_S
from fspm_optics.fixtures.hps.spectral import build_hps_spectral_distribution
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

HPS_REX_WEIGHTED_ATR_MODEL_ID: Final = (
    "rex_mean_of_treatments_hps_source_weighted_atr_v2"
)
HPS_REX_MATERIAL_PREFIX: Final = "hps_rex_leaf_trans"
HPS_REX_OPTICS_LIMITATIONS: Final = (
    "Rex coefficients are HPS-SPD-photon-weighted interval averages.",
    "The Rex model begins at 404 nm; positive 400-403 nm source mass is reported unsupported.",
    "Unsupported source mass is not filled, extrapolated, or redistributed.",
    "The model is a diffuse symmetric infinitely thin leaf, not a measured BSDF.",
)


@dataclass(frozen=True, slots=True)
class HpsRexWeightedAtrPayload:
    spectral_source_payload_id: str
    intervals: RexSourceWeightedIntervalSet
    limitations: tuple[str, ...] = HPS_REX_OPTICS_LIMITATIONS
    optical_payload_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.spectral_source_payload_id:
            raise ValueError("HPS spectral source payload identity is required.")
        if self.intervals.source.source_model_id != HPS_BAND_SOURCE_MODEL_ID:
            raise ValueError("HPS Rex optics reject SMD and Conventional source identities.")
        unsupported = dict(self.intervals.unsupported_source_mass_by_interval)
        if not math.isclose(
            self.intervals.source.photon_distribution.par_amount,
            HPS_INITIAL_LAMP_PAR_PPF_UMOL_S,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("HPS Rex source must preserve 1750 umol/s PAR input.")
        if unsupported["scalar_par"] <= 0.0 or unsupported["blue"] <= 0.0:
            raise ValueError("HPS Rex optics must retain unsupported 400-403 nm mass.")
        if any(
            unsupported[name] != 0.0
            for name in ("green", "orange", "red", "far_red")
        ):
            raise ValueError("HPS unsupported source mass may occur only at 400-403 nm.")
        if not math.isclose(
            unsupported["scalar_par"],
            unsupported["blue"],
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("HPS scalar/blue unsupported source mass must match.")
        object.__setattr__(
            self,
            "optical_payload_id",
            "hps-rex-weighted-atr-v2-" + _hash_payload(self.scientific_payload()),
        )

    @property
    def scalar_par(self):
        return self.intervals.scalar_par

    @property
    def bands(self):
        return self.intervals.bands

    @property
    def unsupported_scalar_par_umol_s(self) -> float:
        return dict(self.intervals.unsupported_source_mass_by_interval)["scalar_par"]

    @property
    def unsupported_scalar_par_fraction(self) -> float:
        return (
            self.unsupported_scalar_par_umol_s
            / self.intervals.source.photon_distribution.par_amount
        )

    def band(self, band_id: str):
        return self.intervals.band(band_id)

    def scientific_payload(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "model_id": HPS_REX_WEIGHTED_ATR_MODEL_ID,
            "spectral_source_payload_id": self.spectral_source_payload_id,
            "weighted_intervals": self.intervals.to_payload(),
            "source_coverage": {
                "positive_start_nm": self.intervals.positive_source_start_nm,
                "positive_end_nm": self.intervals.positive_source_end_nm,
                "rex_start_nm": self.intervals.rex_profile_start_nm,
                "rex_end_nm": self.intervals.rex_profile_end_nm,
                "unsupported_scalar_par_umol_s": self.unsupported_scalar_par_umol_s,
                "unsupported_scalar_par_fraction": self.unsupported_scalar_par_fraction,
                "unsupported_mass_redistributed": False,
            },
            "limitations": list(self.limitations),
        }

    def to_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {"optical_payload_id": self.optical_payload_id}


@dataclass(frozen=True, slots=True)
class HpsRexRadianceMaterialPlan:
    optical_payload: HpsRexWeightedAtrPayload
    materials: tuple[RexRadianceTransIntervalPlan, ...]
    policy_id: str = DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID
    claim_language: str = REX_RADIANCE_MATERIAL_PLAN_CLAIM
    assumptions: tuple[str, ...] = REX_TRANS_MODEL_ASSUMPTIONS
    material_plan_id: str = field(init=False)

    def __post_init__(self) -> None:
        expected = (self.optical_payload.scalar_par, *self.optical_payload.bands)
        if tuple(item.source_interval for item in self.materials) != expected:
            raise ValueError("HPS Rex materials must preserve scalar/five-band order.")
        expected_ids = tuple(
            f"{HPS_REX_MATERIAL_PREFIX}_{item.interval_id}" for item in expected
        )
        if tuple(item.material_identifier for item in self.materials) != expected_ids:
            raise ValueError("HPS Rex material identities are invalid.")
        if self.policy_id != DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID:
            raise ValueError("HPS Rex material policy identity is invalid.")
        object.__setattr__(
            self,
            "material_plan_id",
            "hps-rex-material-plan-v2-" + _hash_payload(self.scientific_payload()),
        )

    def material(self, interval_id: str) -> RexRadianceTransIntervalPlan:
        for item in self.materials:
            if item.interval_id == interval_id:
                return item
        raise KeyError(f"unknown HPS Rex material: {interval_id!r}")

    def scientific_payload(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "policy_id": self.policy_id,
            "claim_language": self.claim_language,
            "assumptions": list(self.assumptions),
            "optical_payload_id": self.optical_payload.optical_payload_id,
            "materials": [item.to_dict() for item in self.materials],
        }

    def to_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {"material_plan_id": self.material_plan_id}


def build_hps_rex_weighted_atr_payload(
    spectral_source: HpsSpectralSourcePayload,
) -> HpsRexWeightedAtrPayload:
    if not isinstance(spectral_source, HpsSpectralSourcePayload):
        raise TypeError("HPS Rex weighting requires HpsSpectralSourcePayload.")
    if spectral_source.scientific_payload()["model_id"] != HPS_BAND_SOURCE_MODEL_ID:
        raise ValueError("HPS Rex weighting rejects non-HPS source identity.")
    distribution = build_hps_spectral_distribution()
    absolute = distribution.photon_distribution.scaled(
        spectral_source.scalar_par_ppf_umol_s_per_fixture
    )
    source = RexSourceWeightingInput(
        source_model_id=HPS_BAND_SOURCE_MODEL_ID,
        normalization_policy=distribution.normalization_policy,
        resource_hashes=((HPS_SPD_RESOURCE_NAME, distribution.resource_sha256),),
        photon_distribution=absolute,
    )
    return HpsRexWeightedAtrPayload(
        spectral_source_payload_id=spectral_source.source_payload_id,
        intervals=compute_rex_source_weighted_interval_set(source),
    )


def build_hps_rex_radiance_material_plan(
    optical_payload: HpsRexWeightedAtrPayload,
) -> HpsRexRadianceMaterialPlan:
    if not isinstance(optical_payload, HpsRexWeightedAtrPayload):
        raise TypeError("HPS material planning requires HPS Rex optical payload.")
    intervals = (optical_payload.scalar_par, *optical_payload.bands)
    return HpsRexRadianceMaterialPlan(
        optical_payload=optical_payload,
        materials=build_rex_radiance_trans_intervals(
            intervals,
            material_prefix=HPS_REX_MATERIAL_PREFIX,
        ),
    )


def format_hps_rex_weighted_atr_json(payload: HpsRexWeightedAtrPayload) -> str:
    return json.dumps(payload.to_payload(), indent=2, sort_keys=True) + "\n"


def format_hps_rex_material_plan_json(plan: HpsRexRadianceMaterialPlan) -> str:
    return json.dumps(plan.to_payload(), indent=2, sort_keys=True) + "\n"


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
