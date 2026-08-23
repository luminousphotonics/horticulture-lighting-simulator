"""Approved Conventional relative SPD to normalized photon distribution."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Final

from fspm_optics.spectral.bands import (
    BLUE_BAND,
    FAR_RED_BAND,
    GREEN_BAND,
    ORANGE_BAND,
    PAR_END_NM_EXCLUSIVE,
    PAR_START_NM,
    RED_BAND,
)
from fspm_optics.spectral.distribution import (
    PhotonDistribution,
    normalize_relative_radiant_spd_to_par_photons,
)
from fspm_optics.spectral.spd import RelativeRadiantSpd

from .errors import ConventionalSpdError
from .resources import CONVENTIONAL_SPD_SHA256, load_conventional_spd

CONVENTIONAL_SPECTRAL_SOURCE_ID: Final = "conventional_led_relative_spd_v2"
CONVENTIONAL_SPD_NORMALIZATION_POLICY: Final = (
    "relative_radiant_spd_times_wavelength_discrete_PAR_photon_unit_total_v1"
)
_PAR_BANDS: Final = (BLUE_BAND, GREEN_BAND, ORANGE_BAND, RED_BAND)


@dataclass(frozen=True, slots=True)
class ConventionalSpectralDistribution:
    """PAR-normalized photons plus a separate far-red/PAR diagnostic."""

    source_id: str
    distribution_id: str
    resource_name: str
    resource_sha256: str
    normalization_policy: str
    photon_distribution: PhotonDistribution
    par_band_photon_fractions: tuple[tuple[str, float], ...]
    far_red_relative_to_par: float

    def __post_init__(self) -> None:
        pairs = tuple(
            (str(band_id), float(value))
            for band_id, value in self.par_band_photon_fractions
        )
        object.__setattr__(self, "par_band_photon_fractions", pairs)
        if self.source_id == "proposed_led_smd_nominal_source_v1":
            raise ConventionalSpdError(
                "Conventional spectral identity must remain distinct from SMD."
            )
        if self.source_id != CONVENTIONAL_SPECTRAL_SOURCE_ID:
            raise ConventionalSpdError("Conventional spectral source ID is invalid.")
        if self.normalization_policy != CONVENTIONAL_SPD_NORMALIZATION_POLICY:
            raise ConventionalSpdError("Conventional SPD normalization policy is invalid.")
        if not self.source_id or not self.distribution_id or not self.resource_name:
            raise ConventionalSpdError("spectral distribution identity is incomplete.")
        if len(self.resource_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.resource_sha256
        ):
            raise ConventionalSpdError("spectral resource SHA-256 is malformed.")
        if self.distribution_id != spectral_distribution_id(self.resource_sha256):
            raise ConventionalSpdError("spectral distribution identity is stale.")
        expected_ids = tuple(band.band_id for band in _PAR_BANDS)
        if tuple(key for key, _ in self.par_band_photon_fractions) != expected_ids:
            raise ConventionalSpdError(
                "Conventional PAR fractions must contain blue, green, orange, and red."
            )
        values = tuple(value for _, value in self.par_band_photon_fractions)
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise ConventionalSpdError("PAR band fractions must be finite and non-negative.")
        if not math.isclose(math.fsum(values), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ConventionalSpdError("the four Conventional PAR fractions must sum to one.")
        if (
            not math.isfinite(self.far_red_relative_to_par)
            or self.far_red_relative_to_par < 0.0
        ):
            raise ConventionalSpdError("far-red relative to PAR must be non-negative.")
        if not math.isclose(
            self.photon_distribution.par_amount,
            1.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ConventionalSpdError(
                "Conventional photon distribution must be unit-normalized over PAR."
            )

    def fraction(self, band_id: str) -> float:
        for candidate, value in self.par_band_photon_fractions:
            if candidate == band_id:
                return value
        raise KeyError(f"unknown Conventional PAR band: {band_id!r}")

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "source_id": self.source_id,
            "distribution_id": self.distribution_id,
            "resource": {
                "name": self.resource_name,
                "sha256": self.resource_sha256,
                "role": "approved_relative_modeled_spectral_power_shape",
                "defines_absolute_PAR_PPF": False,
                "defines_PPE_or_electrical_power": False,
            },
            "normalization_policy": self.normalization_policy,
            "normalization_details": (
                "relative radiant SPD samples are converted with relative_spd * "
                "wavelength_nm and normalized over 400 <= wavelength_nm < 700"
            ),
            "par_normalization_wavelength_range": {
                "start_nm": PAR_START_NM,
                "end_nm_exclusive": PAR_END_NM_EXCLUSIVE,
            },
            "par_band_photon_fractions": dict(self.par_band_photon_fractions),
            "far_red_relative_to_par": self.far_red_relative_to_par,
            "photon_distribution": {
                "wavelength_nm": list(self.photon_distribution.wavelength_nm),
                "amount_relative_to_PAR": list(
                    self.photon_distribution.photon_amount
                ),
            },
            "absolute_PAR_PPF_umol_s": None,
        }


def spectral_distribution_id(resource_sha256: str) -> str:
    canonical = json.dumps(
        {
            "resource_sha256": resource_sha256,
            "normalization_policy": CONVENTIONAL_SPD_NORMALIZATION_POLICY,
            "source_id": CONVENTIONAL_SPECTRAL_SOURCE_ID,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "conventional_led_spectral_v2_" + hashlib.sha256(canonical).hexdigest()[:20]


def build_conventional_spectral_distribution(
    spd: RelativeRadiantSpd | None = None,
    *,
    data_root: str | Path | None = None,
) -> ConventionalSpectralDistribution:
    """Recompute photon fractions from the packaged SPD or an explicit test SPD."""

    if spd is not None and data_root is not None:
        raise ConventionalSpdError("provide either an explicit SPD or data_root, not both.")
    source = load_conventional_spd(data_root=data_root) if spd is None else spd
    try:
        photons = normalize_relative_radiant_spd_to_par_photons(
            source.wavelength_nm,
            source.relative_spd,
        )
    except ValueError as exc:
        raise ConventionalSpdError(str(exc)) from exc
    par_total = photons.par_amount
    fractions = tuple(
        (band.band_id, photons.amount_in_band(band) / par_total)
        for band in _PAR_BANDS
    )
    far_red = photons.amount_in_band(FAR_RED_BAND) / par_total
    return ConventionalSpectralDistribution(
        source_id=CONVENTIONAL_SPECTRAL_SOURCE_ID,
        distribution_id=spectral_distribution_id(source.sha256),
        resource_name=source.resource_name,
        resource_sha256=source.sha256,
        normalization_policy=CONVENTIONAL_SPD_NORMALIZATION_POLICY,
        photon_distribution=photons,
        par_band_photon_fractions=fractions,
        far_red_relative_to_par=far_red,
    )


def format_conventional_spectral_distribution_json(
    distribution: ConventionalSpectralDistribution,
) -> str:
    return json.dumps(distribution.to_payload(), indent=2, sort_keys=True) + "\n"


def assert_approved_spectral_resource(
    distribution: ConventionalSpectralDistribution,
) -> None:
    """Reject accidental use of a non-approved SPD in a production profile."""

    if distribution.resource_sha256 != CONVENTIONAL_SPD_SHA256:
        raise ConventionalSpdError(
            "production Conventional spectral distribution must use the approved SPD hash."
        )
