"""Pure Conventional spectral budgets for scalar and isolated-band transport."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Final

from fspm_optics.spectral.bands import FIXED_TRANSPORT_BANDS

from .layout import ConventionalLayoutPlan
from .profile import (
    CONVENTIONAL_COMPARISON_PROFILE_ID,
    CONVENTIONAL_FIXTURE_POWER_W,
    CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J,
    CONVENTIONAL_FIXTURE_PPF_UMOL_S,
    build_conventional_comparison_profile,
)
from .resources import CONVENTIONAL_SPD_RESOURCE_NAME, CONVENTIONAL_SPD_SHA256
from .spectral import (
    CONVENTIONAL_SPD_NORMALIZATION_POLICY,
    CONVENTIONAL_SPECTRAL_SOURCE_ID,
    assert_approved_spectral_resource,
    build_conventional_spectral_distribution,
)

CONVENTIONAL_BAND_SOURCE_MODEL_ID: Final = (
    "conventional_led_rated_output_five_band_source_v2"
)
CONVENTIONAL_BAND_ORDER: Final = tuple(
    band.band_id for band in FIXED_TRANSPORT_BANDS
)
CONVENTIONAL_BAND_SOURCE_LIMITATIONS: Final[tuple[str, ...]] = (
    "The packaged SPD defines normalized photon shape, not absolute output.",
    "Rated 660 W, 2.6 umol/J, and 1716 umol/s set absolute PAR output.",
    "Far-red is reported relative to PAR and remains outside PAR.",
    "IES magnitude and SPD amplitude do not set band fractions or absolute output.",
    "The source is a one-sided downward modeled comparator, not a measured full-sphere reproduction.",
)


@dataclass(frozen=True, slots=True)
class ConventionalBandPhotonBudget:
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
            raise ValueError("Conventional band metadata is not a fixed transport band.")
        if self.is_par != (self.band_id != "far_red"):
            raise ValueError("far-red must remain outside PAR.")
        for name in (
            "photon_fraction_relative_to_par",
            "effective_yield_umol_per_j",
            "per_fixture_ppf_umol_s",
            "whole_layout_ppf_umol_s",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive.")
            object.__setattr__(self, name, value)

    def to_payload(self) -> dict[str, object]:
        return {
            "band_id": self.band_id,
            "label": self.label,
            "wavelength_interval": {
                "start_nm": self.start_nm,
                "end_nm": self.end_nm,
                "interval": "inclusive_integer_bin",
            },
            "is_par": self.is_par,
            "photon_fraction_relative_to_par": self.photon_fraction_relative_to_par,
            "effective_yield_umol_per_j": self.effective_yield_umol_per_j,
            "per_fixture_ppf_umol_s": self.per_fixture_ppf_umol_s,
            "whole_layout_ppf_umol_s": self.whole_layout_ppf_umol_s,
        }


@dataclass(frozen=True, slots=True)
class ConventionalSpectralSourcePayload:
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
    bands: tuple[ConventionalBandPhotonBudget, ...]
    limitations: tuple[str, ...] = CONVENTIONAL_BAND_SOURCE_LIMITATIONS
    source_payload_id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.profile_id != CONVENTIONAL_COMPARISON_PROFILE_ID:
            raise ValueError("Conventional band source profile identity is invalid.")
        if (
            len(self.profile_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.profile_sha256
            )
            or not self.layout_id
            or not self.spectral_distribution_id
        ):
            raise ValueError("Conventional band source scientific identity is invalid.")
        if self.spectral_source_id != CONVENTIONAL_SPECTRAL_SOURCE_ID:
            raise ValueError("Conventional spectral source identity is invalid.")
        if self.spectral_source_id == "proposed_led_smd_nominal_source_v1":
            raise ValueError("SMD source identity is prohibited in Conventional payloads.")
        if (
            self.spd_resource_name,
            self.spd_resource_sha256,
            self.normalization_policy,
        ) != (
            CONVENTIONAL_SPD_RESOURCE_NAME,
            CONVENTIONAL_SPD_SHA256,
            CONVENTIONAL_SPD_NORMALIZATION_POLICY,
        ):
            raise ValueError("Conventional source payload requires the approved SPD.")
        if (
            isinstance(self.fixture_count, bool)
            or not isinstance(self.fixture_count, int)
            or self.fixture_count <= 0
        ):
            raise ValueError("fixture_count must be a positive integer.")
        if (
            self.electrical_power_w_per_fixture,
            self.scalar_par_ppe_umol_per_j,
            self.scalar_par_ppf_umol_s_per_fixture,
        ) != (
            CONVENTIONAL_FIXTURE_POWER_W,
            CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J,
            CONVENTIONAL_FIXTURE_PPF_UMOL_S,
        ):
            raise ValueError(
                "Conventional band source operating point must remain 660 W / "
                "2.6 umol/J / 1716 umol/s."
            )
        if tuple(item.band_id for item in self.bands) != CONVENTIONAL_BAND_ORDER:
            raise ValueError("Conventional bands must preserve fixed transport order.")
        if any(
            not math.isclose(
                item.effective_yield_umol_per_j,
                self.scalar_par_ppe_umol_per_j
                * item.photon_fraction_relative_to_par,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            or not math.isclose(
                item.per_fixture_ppf_umol_s,
                self.scalar_par_ppf_umol_s_per_fixture
                * item.photon_fraction_relative_to_par,
                rel_tol=1e-12,
                abs_tol=1e-9,
            )
            for item in self.bands
        ):
            raise ValueError("Conventional band fractions and absolute budgets diverge.")
        par = self.bands[:4]
        if not math.isclose(
            math.fsum(item.photon_fraction_relative_to_par for item in par),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("Conventional PAR photon fractions must sum to one.")
        if not math.isclose(
            math.fsum(item.effective_yield_umol_per_j for item in par),
            self.scalar_par_ppe_umol_per_j,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("Conventional PAR band yields must close to scalar PPE.")
        if not math.isclose(
            math.fsum(item.per_fixture_ppf_umol_s for item in par),
            self.scalar_par_ppf_umol_s_per_fixture,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("Conventional PAR band PPF must close per fixture.")
        if any(
            not math.isclose(
                item.whole_layout_ppf_umol_s,
                self.fixture_count * item.per_fixture_ppf_umol_s,
                rel_tol=1e-12,
                abs_tol=1e-9,
            )
            for item in self.bands
        ):
            raise ValueError("Conventional whole-layout band PPF is inconsistent.")
        object.__setattr__(
            self,
            "source_payload_id",
            "conventional-spectral-source-payload-v1-"
            + _hash_payload(self.scientific_payload()),
        )

    @property
    def far_red_relative_to_par(self) -> float:
        return self.band("far_red").photon_fraction_relative_to_par

    def band(self, band_id: str) -> ConventionalBandPhotonBudget:
        for item in self.bands:
            if item.band_id == band_id:
                return item
        raise KeyError(f"unknown Conventional band: {band_id!r}")

    def scientific_payload(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "model_id": CONVENTIONAL_BAND_SOURCE_MODEL_ID,
            "profile_id": self.profile_id,
            "profile_sha256": self.profile_sha256,
            "layout_id": self.layout_id,
            "fixture_count": self.fixture_count,
            "spectral_source_id": self.spectral_source_id,
            "spectral_distribution_id": self.spectral_distribution_id,
            "spd_resource": {
                "name": self.spd_resource_name,
                "sha256": self.spd_resource_sha256,
                "role": "normalized_photon_shape_only",
                "amplitude_sets_output": False,
            },
            "normalization_policy": self.normalization_policy,
            "band_order": list(CONVENTIONAL_BAND_ORDER),
            "operating_point": {
                "electrical_power_w_per_fixture": self.electrical_power_w_per_fixture,
                "scalar_par_ppe_umol_per_j": self.scalar_par_ppe_umol_per_j,
                "scalar_par_ppf_umol_s_per_fixture": (
                    self.scalar_par_ppf_umol_s_per_fixture
                ),
                "ies_magnitude_sets_output": False,
                "spd_amplitude_sets_output": False,
                "far_red_is_outside_par_anchor": True,
            },
            "bands": [item.to_payload() for item in self.bands],
            "limitations": list(self.limitations),
        }

    def to_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {"source_payload_id": self.source_payload_id}


def build_conventional_spectral_source_payload(
    layout: ConventionalLayoutPlan,
) -> ConventionalSpectralSourcePayload:
    """Recompute approved spectral fractions and anchor them to one layout."""

    profile = build_conventional_comparison_profile()
    if layout.conventional_profile_id != profile.profile_id:
        raise ValueError("Conventional layout/profile identity mismatch.")
    distribution = build_conventional_spectral_distribution()
    assert_approved_spectral_resource(distribution)
    operating = profile.modeled_operating_point
    fraction_by_band = dict(distribution.par_band_photon_fractions) | {
        "far_red": distribution.far_red_relative_to_par
    }
    budgets = tuple(
        ConventionalBandPhotonBudget(
            band_id=band.band_id,
            label=band.label,
            start_nm=band.start_nm,
            end_nm=band.end_nm,
            is_par=band.band_id != "far_red",
            photon_fraction_relative_to_par=fraction_by_band[band.band_id],
            effective_yield_umol_per_j=(
                operating.par_ppe_umol_per_j * fraction_by_band[band.band_id]
            ),
            per_fixture_ppf_umol_s=(
                operating.par_ppf_umol_s_per_fixture
                * fraction_by_band[band.band_id]
            ),
            whole_layout_ppf_umol_s=(
                len(layout.fixtures)
                * operating.par_ppf_umol_s_per_fixture
                * fraction_by_band[band.band_id]
            ),
        )
        for band in FIXED_TRANSPORT_BANDS
    )
    return ConventionalSpectralSourcePayload(
        profile_id=profile.profile_id,
        profile_sha256=profile.profile_sha256,
        layout_id=layout.layout_id,
        fixture_count=len(layout.fixtures),
        spectral_source_id=distribution.source_id,
        spectral_distribution_id=distribution.distribution_id,
        spd_resource_name=distribution.resource_name,
        spd_resource_sha256=distribution.resource_sha256,
        normalization_policy=distribution.normalization_policy,
        electrical_power_w_per_fixture=(
            operating.electrical_power_w_per_fixture
        ),
        scalar_par_ppe_umol_per_j=operating.par_ppe_umol_per_j,
        scalar_par_ppf_umol_s_per_fixture=(
            operating.par_ppf_umol_s_per_fixture
        ),
        bands=budgets,
    )


def format_conventional_spectral_source_payload_json(
    payload: ConventionalSpectralSourcePayload,
) -> str:
    return json.dumps(payload.to_payload(), indent=2, sort_keys=True) + "\n"


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
