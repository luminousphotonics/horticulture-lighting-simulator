"""Pure discrete photon-distribution normalization and aggregation."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

from .bands import PAR_END_NM_EXCLUSIVE, PAR_START_NM, SpectralBand


@dataclass(frozen=True, slots=True)
class PhotonDistribution:
    """Photon amounts on a strictly increasing discrete wavelength grid."""

    wavelength_nm: tuple[float, ...]
    photon_amount: tuple[float, ...]

    def __post_init__(self) -> None:
        wavelengths = tuple(float(value) for value in self.wavelength_nm)
        amounts = tuple(float(value) for value in self.photon_amount)
        if not wavelengths or len(wavelengths) != len(amounts):
            raise ValueError(
                "photon distribution wavelengths and amounts must have equal "
                "non-zero lengths."
            )
        if any(not math.isfinite(value) or value <= 0.0 for value in wavelengths):
            raise ValueError("photon distribution wavelengths must be finite and positive.")
        if any(
            current <= previous
            for previous, current in zip(wavelengths, wavelengths[1:])
        ):
            raise ValueError(
                "photon distribution wavelengths must be strictly increasing."
            )
        if any(not math.isfinite(value) or value < 0.0 for value in amounts):
            raise ValueError("photon distribution amounts must be finite and non-negative.")
        object.__setattr__(self, "wavelength_nm", wavelengths)
        object.__setattr__(self, "photon_amount", amounts)

    def amount_between(
        self,
        start_nm: float,
        end_nm_exclusive: float,
    ) -> float:
        return math.fsum(
            amount
            for wavelength, amount in zip(
                self.wavelength_nm,
                self.photon_amount,
                strict=True,
            )
            if start_nm <= wavelength < end_nm_exclusive
        )

    def amount_in_band(self, band: SpectralBand) -> float:
        return math.fsum(
            amount
            for wavelength, amount in zip(
                self.wavelength_nm,
                self.photon_amount,
                strict=True,
            )
            if band.contains(wavelength)
        )

    @property
    def par_amount(self) -> float:
        return self.amount_between(PAR_START_NM, PAR_END_NM_EXCLUSIVE)

    def scaled(self, multiplier: float) -> "PhotonDistribution":
        if (
            isinstance(multiplier, bool)
            or not isinstance(multiplier, int | float)
            or not math.isfinite(float(multiplier))
            or float(multiplier) < 0.0
        ):
            raise ValueError("photon distribution multiplier must be finite and non-negative.")
        scale = float(multiplier)
        return PhotonDistribution(
            self.wavelength_nm,
            tuple(amount * scale for amount in self.photon_amount),
        )


def normalize_relative_radiant_spd_to_par_photons(
    wavelength_nm: Sequence[float | int],
    relative_spd: Sequence[float | int],
) -> PhotonDistribution:
    """Convert radiant shape using ``relative_spd * wavelength`` and PAR-normalize."""

    if len(wavelength_nm) != len(relative_spd) or not wavelength_nm:
        raise ValueError("wavelength_nm and relative_spd must have equal non-zero lengths.")
    wavelengths = tuple(float(value) for value in wavelength_nm)
    values = tuple(float(value) for value in relative_spd)
    raw = PhotonDistribution(
        wavelengths,
        tuple(value * wavelength for wavelength, value in zip(wavelengths, values, strict=True)),
    )
    par_total = raw.par_amount
    if not math.isfinite(par_total) or par_total <= 0.0:
        raise ValueError(
            "relative radiant SPD must have positive photon weight over "
            "400 <= wavelength_nm < 700."
        )
    return raw.scaled(1.0 / par_total)


def combine_photon_distributions(
    distributions: Iterable[PhotonDistribution],
) -> PhotonDistribution:
    """Sum distributions on the deterministic union of their sample grids."""

    items = tuple(distributions)
    if not items:
        raise ValueError("at least one photon distribution is required.")
    amounts_by_wavelength: dict[float, list[float]] = {}
    for distribution in items:
        for wavelength, amount in zip(
            distribution.wavelength_nm,
            distribution.photon_amount,
            strict=True,
        ):
            amounts_by_wavelength.setdefault(wavelength, []).append(amount)
    wavelengths = tuple(sorted(amounts_by_wavelength))
    return PhotonDistribution(
        wavelengths,
        tuple(math.fsum(amounts_by_wavelength[value]) for value in wavelengths),
    )
