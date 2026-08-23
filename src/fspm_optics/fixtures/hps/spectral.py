"""HPS relative SPD to strict PAR-normalized photon distribution."""

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
    RED_BAND,
)
from fspm_optics.spectral.distribution import (
    PhotonDistribution,
    normalize_relative_radiant_spd_to_par_photons,
)
from fspm_optics.spectral.spd import RelativeRadiantSpd

from .errors import HpsSourceModelError
from .resources import HPS_SPD_SHA256, load_hps_spd

HPS_SPECTRAL_SOURCE_ID: Final = "hps_se_relative_photon_shape_v2"
HPS_SPD_NORMALIZATION_POLICY: Final = (
    "relative_radiant_spd_times_wavelength_discrete_PAR_photon_unit_total_v1"
)
_PAR_BANDS: Final = (BLUE_BAND, GREEN_BAND, ORANGE_BAND, RED_BAND)


@dataclass(frozen=True, slots=True)
class HpsSpectralDistribution:
    source_id: str
    distribution_id: str
    resource_name: str
    resource_sha256: str
    normalization_policy: str
    photon_distribution: PhotonDistribution
    par_band_photon_fractions: tuple[tuple[str, float], ...]
    far_red_relative_to_par: float

    def __post_init__(self) -> None:
        if self.source_id != HPS_SPECTRAL_SOURCE_ID:
            raise HpsSourceModelError("HPS spectral source identity is invalid.")
        if "smd" in self.source_id or "conventional" in self.source_id:
            raise HpsSourceModelError("cross-source spectral identity is prohibited.")
        if self.normalization_policy != HPS_SPD_NORMALIZATION_POLICY:
            raise HpsSourceModelError("HPS SPD normalization policy is invalid.")
        if self.distribution_id != hps_spectral_distribution_id(self.resource_sha256):
            raise HpsSourceModelError("HPS spectral distribution identity is stale.")
        expected = tuple(band.band_id for band in _PAR_BANDS)
        if tuple(name for name, _ in self.par_band_photon_fractions) != expected:
            raise HpsSourceModelError("HPS PAR bands are incomplete or out of order.")
        values = tuple(value for _, value in self.par_band_photon_fractions)
        if not math.isclose(math.fsum(values), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise HpsSourceModelError("HPS PAR fractions must sum to one.")
        if not math.isclose(
            self.photon_distribution.par_amount,
            1.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise HpsSourceModelError("HPS photon distribution must be PAR-normalized.")
        if not math.isfinite(self.far_red_relative_to_par) or self.far_red_relative_to_par <= 0:
            raise HpsSourceModelError("HPS far-red/PAR ratio must be positive.")

    def fraction(self, band_id: str) -> float:
        for name, value in self.par_band_photon_fractions:
            if name == band_id:
                return value
        if band_id == "far_red":
            return self.far_red_relative_to_par
        raise KeyError(f"unknown HPS spectral band: {band_id!r}")

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "source_id": self.source_id,
            "distribution_id": self.distribution_id,
            "resource": {
                "name": self.resource_name,
                "sha256": self.resource_sha256,
                "role": "approved_relative_spectral_shape_only",
                "amplitude_sets_fraction_or_output": False,
            },
            "normalization_policy": self.normalization_policy,
            "par_band_photon_fractions": dict(self.par_band_photon_fractions),
            "far_red_relative_to_par": self.far_red_relative_to_par,
            "photon_distribution": {
                "wavelength_nm": list(self.photon_distribution.wavelength_nm),
                "amount_relative_to_PAR": list(self.photon_distribution.photon_amount),
            },
        }


def hps_spectral_distribution_id(resource_sha256: str) -> str:
    canonical = json.dumps(
        {
            "resource_sha256": resource_sha256,
            "normalization_policy": HPS_SPD_NORMALIZATION_POLICY,
            "source_id": HPS_SPECTRAL_SOURCE_ID,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "hps-spectral-v2-" + hashlib.sha256(canonical).hexdigest()


def build_hps_spectral_distribution(
    spd: RelativeRadiantSpd | None = None,
    *,
    data_root: str | Path | None = None,
) -> HpsSpectralDistribution:
    if spd is not None and data_root is not None:
        raise HpsSourceModelError("provide either explicit HPS SPD or data_root, not both.")
    source = load_hps_spd(data_root=data_root) if spd is None else spd
    photons = normalize_relative_radiant_spd_to_par_photons(
        source.wavelength_nm,
        source.relative_spd,
    )
    fractions = list(
        (band.band_id, photons.amount_in_band(band) / photons.par_amount)
        for band in _PAR_BANDS
    )
    red_name, _ = fractions[-1]
    fractions[-1] = (
        red_name,
        1.0 - math.fsum(value for _, value in fractions[:-1]),
    )
    return HpsSpectralDistribution(
        source_id=HPS_SPECTRAL_SOURCE_ID,
        distribution_id=hps_spectral_distribution_id(source.sha256),
        resource_name=source.resource_name,
        resource_sha256=source.sha256,
        normalization_policy=HPS_SPD_NORMALIZATION_POLICY,
        photon_distribution=photons,
        par_band_photon_fractions=tuple(fractions),
        far_red_relative_to_par=(photons.amount_in_band(FAR_RED_BAND) / photons.par_amount),
    )


def assert_approved_hps_spectral_resource(
    distribution: HpsSpectralDistribution,
) -> None:
    if distribution.resource_sha256 != HPS_SPD_SHA256:
        raise HpsSourceModelError("production HPS science requires the approved SPD hash.")


def format_hps_spectral_distribution_json(
    distribution: HpsSpectralDistribution,
) -> str:
    return json.dumps(distribution.to_payload(), indent=2, sort_keys=True) + "\n"
