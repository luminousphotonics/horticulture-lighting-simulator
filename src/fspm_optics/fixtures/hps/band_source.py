"""Absolute HPS scalar and fixed-band photon budgets for one layout."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import math
from typing import Final

from fspm_optics.spectral.bands import FIXED_TRANSPORT_BANDS

from .errors import HpsSourceModelError
from .layout import HpsLayoutPlan
from .profile import (
    HPS_COMPARISON_PROFILE_ID,
    HPS_INITIAL_LAMP_PAR_PPF_UMOL_S,
    HPS_SYSTEM_PPE_UMOL_PER_J,
    HPS_TESTED_SYSTEM_INPUT_POWER_W,
    build_hps_comparison_profile,
)
from .resources import HPS_SPD_RESOURCE_NAME, HPS_SPD_SHA256
from .spectral import (
    HPS_SPD_NORMALIZATION_POLICY,
    HPS_SPECTRAL_SOURCE_ID,
    assert_approved_hps_spectral_resource,
    build_hps_spectral_distribution,
)

HPS_BAND_SOURCE_MODEL_ID: Final = "hps_fixed_output_five_band_source_v2"
HPS_BAND_ORDER: Final = tuple(band.band_id for band in FIXED_TRANSPORT_BANDS)
HPS_BAND_SOURCE_LIMITATIONS: Final = (
    "The packaged SPD defines relative photon shape only.",
    "Documented 1750 umol/s initial lamp PAR PPF and tested 1045 W system input set authority.",
    "Far-red remains outside PAR and is expressed relative to PAR.",
    "Orange and red remain separate fixed transport intervals.",
    "SPD amplitude, IES lumens, and raw candela magnitude do not set output.",
)


@dataclass(frozen=True, slots=True)
class HpsBandPhotonBudget:
    band_id: str
    label: str
    start_nm: int
    end_nm: int
    is_par: bool
    photon_fraction_relative_to_par: float
    effective_yield_umol_per_j: float
    per_fixture_ppf_umol_s: float
    whole_layout_ppf_umol_s: float

    def __post_init__(self) -> None:
        expected = next(
            (band for band in FIXED_TRANSPORT_BANDS if band.band_id == self.band_id),
            None,
        )
        if expected is None or (self.label, self.start_nm, self.end_nm) != (
            expected.label,
            expected.start_nm,
            expected.end_nm,
        ):
            raise HpsSourceModelError("HPS band metadata is invalid.")
        if self.is_par != (self.band_id != "far_red"):
            raise HpsSourceModelError("HPS far-red must remain outside PAR.")
        for name in (
            "photon_fraction_relative_to_par",
            "effective_yield_umol_per_j",
            "per_fixture_ppf_umol_s",
            "whole_layout_ppf_umol_s",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise HpsSourceModelError(f"{name} must be finite and positive.")
            object.__setattr__(self, name, value)

    def to_payload(self) -> dict[str, object]:
        return {
            "band_id": self.band_id,
            "label": self.label,
            "start_nm": self.start_nm,
            "end_nm": self.end_nm,
            "is_par": self.is_par,
            "photon_fraction_relative_to_par": self.photon_fraction_relative_to_par,
            "effective_yield_umol_per_j": self.effective_yield_umol_per_j,
            "per_fixture_ppf_umol_s": self.per_fixture_ppf_umol_s,
            "whole_layout_ppf_umol_s": self.whole_layout_ppf_umol_s,
        }


@dataclass(frozen=True, slots=True)
class HpsSpectralSourcePayload:
    profile_id: str
    profile_sha256: str
    layout_id: str
    fixture_count: int
    spectral_source_id: str
    spectral_distribution_id: str
    spd_resource_name: str
    spd_resource_sha256: str
    normalization_policy: str
    electrical_power_w_per_fixture: float
    scalar_par_ppe_umol_per_j: float
    scalar_par_ppf_umol_s_per_fixture: float
    bands: tuple[HpsBandPhotonBudget, ...]
    limitations: tuple[str, ...] = HPS_BAND_SOURCE_LIMITATIONS
    source_payload_id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.profile_id != HPS_COMPARISON_PROFILE_ID:
            raise HpsSourceModelError("HPS spectral payload profile identity is invalid.")
        if (
            len(self.profile_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.profile_sha256
            )
            or not self.layout_id
            or not self.spectral_distribution_id
        ):
            raise HpsSourceModelError("HPS spectral payload identity is incomplete.")
        if self.spectral_source_id != HPS_SPECTRAL_SOURCE_ID:
            raise HpsSourceModelError("HPS spectral payload source identity is invalid.")
        if "smd" in self.spectral_source_id or "conventional" in self.spectral_source_id:
            raise HpsSourceModelError("cross-source spectral payload is prohibited.")
        if (
            self.spd_resource_name,
            self.spd_resource_sha256,
            self.normalization_policy,
        ) != (
            HPS_SPD_RESOURCE_NAME,
            HPS_SPD_SHA256,
            HPS_SPD_NORMALIZATION_POLICY,
        ):
            raise HpsSourceModelError("HPS spectral payload requires the approved SPD.")
        if (
            isinstance(self.fixture_count, bool)
            or not isinstance(self.fixture_count, int)
            or self.fixture_count <= 0
        ):
            raise HpsSourceModelError("HPS fixture_count must be positive.")
        if (
            self.electrical_power_w_per_fixture,
            self.scalar_par_ppe_umol_per_j,
            self.scalar_par_ppf_umol_s_per_fixture,
        ) != (
            HPS_TESTED_SYSTEM_INPUT_POWER_W,
            HPS_SYSTEM_PPE_UMOL_PER_J,
            HPS_INITIAL_LAMP_PAR_PPF_UMOL_S,
        ):
            raise HpsSourceModelError("HPS spectral operating point is invalid.")
        if tuple(item.band_id for item in self.bands) != HPS_BAND_ORDER:
            raise HpsSourceModelError("HPS band order is invalid.")
        par = self.bands[:4]
        if not math.isclose(
            math.fsum(item.photon_fraction_relative_to_par for item in par),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise HpsSourceModelError("HPS PAR band fractions do not close.")
        if not math.isclose(
            math.fsum(item.effective_yield_umol_per_j for item in par),
            self.scalar_par_ppe_umol_per_j,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise HpsSourceModelError("HPS PAR band yields do not close to PPE.")
        if not math.isclose(
            math.fsum(item.per_fixture_ppf_umol_s for item in par),
            self.scalar_par_ppf_umol_s_per_fixture,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise HpsSourceModelError("HPS per-fixture PAR budgets do not close.")
        for item in self.bands:
            if not math.isclose(
                item.effective_yield_umol_per_j,
                self.scalar_par_ppe_umol_per_j
                * item.photon_fraction_relative_to_par,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ) or not math.isclose(
                item.per_fixture_ppf_umol_s,
                self.scalar_par_ppf_umol_s_per_fixture
                * item.photon_fraction_relative_to_par,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ) or not math.isclose(
                item.whole_layout_ppf_umol_s,
                self.fixture_count * item.per_fixture_ppf_umol_s,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise HpsSourceModelError("HPS band budget derivation is inconsistent.")
        object.__setattr__(
            self,
            "source_payload_id",
            "hps-spectral-payload-v2-" + _hash_payload(self.scientific_payload()),
        )

    @property
    def far_red_relative_to_par(self) -> float:
        return self.band("far_red").photon_fraction_relative_to_par

    def band(self, band_id: str) -> HpsBandPhotonBudget:
        for item in self.bands:
            if item.band_id == band_id:
                return item
        raise KeyError(f"unknown HPS band: {band_id!r}")

    def scientific_payload(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "model_id": HPS_BAND_SOURCE_MODEL_ID,
            "profile_id": self.profile_id,
            "profile_sha256": self.profile_sha256,
            "layout_id": self.layout_id,
            "fixture_count": self.fixture_count,
            "spectral_source_id": self.spectral_source_id,
            "spectral_distribution_id": self.spectral_distribution_id,
            "spd_resource": {
                "name": self.spd_resource_name,
                "sha256": self.spd_resource_sha256,
                "amplitude_sets_output": False,
            },
            "normalization_policy": self.normalization_policy,
            "operating_point": {
                "electrical_power_w_per_fixture": self.electrical_power_w_per_fixture,
                "scalar_par_ppe_umol_per_j": self.scalar_par_ppe_umol_per_j,
                "scalar_par_ppf_umol_s_per_fixture": self.scalar_par_ppf_umol_s_per_fixture,
                "initial_lamp_par_ppf_authority": True,
                "tested_system_input_power_authority": True,
                "system_ppe_computed_as_ppf_over_power": True,
                "fixed_output": True,
            },
            "bands": [item.to_payload() for item in self.bands],
            "limitations": list(self.limitations),
        }

    def to_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {"source_payload_id": self.source_payload_id}


def build_hps_spectral_source_payload(
    layout: HpsLayoutPlan,
) -> HpsSpectralSourcePayload:
    profile = build_hps_comparison_profile()
    if layout.profile_id != profile.profile_id:
        raise HpsSourceModelError("HPS layout/profile identity mismatch.")
    distribution = build_hps_spectral_distribution()
    assert_approved_hps_spectral_resource(distribution)
    operating = profile.modeled_operating_point
    budgets = list(
        HpsBandPhotonBudget(
            band_id=band.band_id,
            label=band.label,
            start_nm=band.start_nm,
            end_nm=band.end_nm,
            is_par=band.band_id != "far_red",
            photon_fraction_relative_to_par=distribution.fraction(band.band_id),
            effective_yield_umol_per_j=(
                operating.par_ppe_umol_per_j * distribution.fraction(band.band_id)
            ),
            per_fixture_ppf_umol_s=(
                operating.par_ppf_umol_s_per_fixture * distribution.fraction(band.band_id)
            ),
            whole_layout_ppf_umol_s=(
                len(layout.fixtures)
                * operating.par_ppf_umol_s_per_fixture
                * distribution.fraction(band.band_id)
            ),
        )
        for band in FIXED_TRANSPORT_BANDS
    )
    red_index = next(
        index for index, budget in enumerate(budgets) if budget.band_id == "red"
    )
    preceding_par = tuple(budget for budget in budgets[:red_index] if budget.is_par)
    red = budgets[red_index]
    closed_red_ppe = operating.par_ppe_umol_per_j - math.fsum(
        budget.effective_yield_umol_per_j for budget in preceding_par
    )
    closed_red_ppf = operating.par_ppf_umol_s_per_fixture - math.fsum(
        budget.per_fixture_ppf_umol_s for budget in preceding_par
    )
    budgets[red_index] = replace(
        red,
        effective_yield_umol_per_j=closed_red_ppe,
        per_fixture_ppf_umol_s=closed_red_ppf,
        whole_layout_ppf_umol_s=len(layout.fixtures) * closed_red_ppf,
    )
    return HpsSpectralSourcePayload(
        profile_id=profile.profile_id,
        profile_sha256=profile.profile_sha256,
        layout_id=layout.layout_id,
        fixture_count=len(layout.fixtures),
        spectral_source_id=distribution.source_id,
        spectral_distribution_id=distribution.distribution_id,
        spd_resource_name=distribution.resource_name,
        spd_resource_sha256=distribution.resource_sha256,
        normalization_policy=distribution.normalization_policy,
        electrical_power_w_per_fixture=operating.electrical_power_w_per_fixture,
        scalar_par_ppe_umol_per_j=operating.par_ppe_umol_per_j,
        scalar_par_ppf_umol_s_per_fixture=operating.par_ppf_umol_s_per_fixture,
        bands=tuple(budgets),
    )


def format_hps_spectral_source_payload_json(
    payload: HpsSpectralSourcePayload,
) -> str:
    return json.dumps(payload.to_payload(), indent=2, sort_keys=True) + "\n"


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
