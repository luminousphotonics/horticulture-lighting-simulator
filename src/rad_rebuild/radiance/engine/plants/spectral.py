"""Spectral leaf-optics contracts for FSPM response modeling.

This module is intentionally pure engine code. It does not run Radiance,
predict yield, or claim cultivar-specific biology. It provides validated
band-level photon absorption factors and response-input summaries that later
photosynthesis and photomorphogenesis modules can consume.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping

SPECTRAL_RESPONSE_SCHEMA = "rad_rebuild.fspm.plant_spectral_response.scaffold.v1"
SPECTRAL_RESPONSE_SCHEMA_VERSION = 1
SPECTRAL_ABSORPTION_METHOD = "band_weighted_leaf_absorptance_v1"
NO_CROP_OUTPUT_TERMS = ["yield", "biomass", "growth", "crop_output"]

PAR_BAND_IDS = frozenset({"blue", "green", "red"})


def _finite_fraction(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a JSON number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    if number < 0.0 or number > 1.0:
        raise ValueError(f"{name} must be between 0 and 1.")
    return number


def _finite_positive(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a JSON number.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive.")
    return number


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0.0:
        return None
    return numerator / denominator


@dataclass(frozen=True)
class LeafSpectralOpticalBand:
    """Leaf optical factors for one wavelength band."""

    band_id: str
    wavelength_min_nm: float
    wavelength_max_nm: float
    reflectance: float
    transmittance: float

    def __post_init__(self) -> None:
        if not isinstance(self.band_id, str) or not self.band_id:
            raise ValueError("band_id must be a non-empty string.")
        wavelength_min = _finite_positive("wavelength_min_nm", self.wavelength_min_nm)
        wavelength_max = _finite_positive("wavelength_max_nm", self.wavelength_max_nm)
        if wavelength_max <= wavelength_min:
            raise ValueError("wavelength_max_nm must be greater than wavelength_min_nm.")
        reflectance = _finite_fraction("reflectance", self.reflectance)
        transmittance = _finite_fraction("transmittance", self.transmittance)
        if reflectance + transmittance > 1.0 + 1e-9:
            raise ValueError("reflectance + transmittance may not exceed 1.0.")

    @property
    def wavelength_center_nm(self) -> float:
        return (float(self.wavelength_min_nm) + float(self.wavelength_max_nm)) / 2.0

    @property
    def absorptance(self) -> float:
        return max(0.0, 1.0 - float(self.reflectance) - float(self.transmittance))

    def to_payload(self) -> dict[str, Any]:
        return {
            "band_id": self.band_id,
            "wavelength_min_nm": float(self.wavelength_min_nm),
            "wavelength_max_nm": float(self.wavelength_max_nm),
            "wavelength_center_nm": self.wavelength_center_nm,
            "reflectance": float(self.reflectance),
            "transmittance": float(self.transmittance),
            "absorptance": self.absorptance,
        }


@dataclass(frozen=True)
class SpectralPhotonFraction:
    """Incident photon fraction assigned to a wavelength band."""

    band_id: str
    photon_fraction: float

    def __post_init__(self) -> None:
        if not isinstance(self.band_id, str) or not self.band_id:
            raise ValueError("band_id must be a non-empty string.")
        _finite_fraction("photon_fraction", self.photon_fraction)


@dataclass(frozen=True)
class SpectralPhotonDistribution:
    """Named fixture or source spectral photon distribution."""

    distribution_id: str
    photon_fractions: tuple[SpectralPhotonFraction, ...]
    source: str = "model_input"

    def __post_init__(self) -> None:
        if not isinstance(self.distribution_id, str) or not self.distribution_id:
            raise ValueError("distribution_id must be a non-empty string.")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("source must be a non-empty string.")
        if not self.photon_fractions:
            raise ValueError("At least one spectral photon fraction is required.")
        _fraction_by_band(self.photon_fractions)

    def fraction_map(self) -> dict[str, float]:
        return _fraction_by_band(self.photon_fractions)

    def to_payload(self) -> dict[str, Any]:
        return {
            "distribution_id": self.distribution_id,
            "source": self.source,
            "photon_fractions": [
                {
                    "band_id": entry.band_id,
                    "photon_fraction": float(entry.photon_fraction),
                }
                for entry in self.photon_fractions
            ],
        }


def _fraction_by_band(
    photon_fractions: Mapping[str, float] | Iterable[SpectralPhotonFraction],
) -> dict[str, float]:
    if isinstance(photon_fractions, Mapping):
        entries = [
            SpectralPhotonFraction(str(band_id), value)
            for band_id, value in photon_fractions.items()
        ]
    else:
        entries = list(photon_fractions)

    if not entries:
        raise ValueError("At least one spectral photon fraction is required.")

    fractions: dict[str, float] = {}
    for entry in entries:
        if entry.band_id in fractions:
            raise ValueError(f"Duplicate spectral photon fraction band: {entry.band_id!r}.")
        fractions[entry.band_id] = _finite_fraction(
            f"photon_fraction[{entry.band_id}]",
            entry.photon_fraction,
        )

    total = sum(fractions.values())
    if total <= 0.0:
        raise ValueError("At least one spectral photon fraction must be greater than zero.")
    if total > 1.0 + 1e-6:
        raise ValueError("Spectral photon fractions may not sum to more than 1.0.")
    return fractions


def _sum_band(rows: list[dict[str, Any]], band_id: str, key: str) -> float:
    return sum(float(row[key]) for row in rows if row["band_id"] == band_id)


def _sum_bands(rows: list[dict[str, Any]], band_ids: frozenset[str], key: str) -> float:
    return sum(float(row[key]) for row in rows if row["band_id"] in band_ids)


def build_leaf_spectral_absorption_summary(
    optical_bands: list[LeafSpectralOpticalBand],
    photon_fractions: Mapping[str, float] | Iterable[SpectralPhotonFraction],
    *,
    method: str = SPECTRAL_ABSORPTION_METHOD,
) -> dict[str, Any]:
    """Build a deterministic band-weighted leaf absorption summary."""

    if not optical_bands:
        raise ValueError("At least one leaf optical band is required.")

    by_band: dict[str, LeafSpectralOpticalBand] = {}
    for band in optical_bands:
        if band.band_id in by_band:
            raise ValueError(f"Duplicate leaf optical band: {band.band_id!r}.")
        by_band[band.band_id] = band

    fractions = _fraction_by_band(photon_fractions)
    unknown = sorted(set(fractions) - set(by_band))
    if unknown:
        raise ValueError(f"Photon fractions supplied for unknown spectral bands: {unknown}")

    rows: list[dict[str, Any]] = []
    total_incident_fraction = 0.0
    total_absorbed_fraction = 0.0
    total_reflected_fraction = 0.0
    total_transmitted_fraction = 0.0

    for band in sorted(by_band.values(), key=lambda item: item.wavelength_min_nm):
        incident_fraction = fractions.get(band.band_id, 0.0)
        absorbed_fraction = incident_fraction * band.absorptance
        reflected_fraction = incident_fraction * float(band.reflectance)
        transmitted_fraction = incident_fraction * float(band.transmittance)

        total_incident_fraction += incident_fraction
        total_absorbed_fraction += absorbed_fraction
        total_reflected_fraction += reflected_fraction
        total_transmitted_fraction += transmitted_fraction

        rows.append(
            {
                **band.to_payload(),
                "incident_photon_fraction": incident_fraction,
                "absorbed_photon_fraction": absorbed_fraction,
                "reflected_photon_fraction": reflected_fraction,
                "transmitted_photon_fraction": transmitted_fraction,
            }
        )

    par_incident = _sum_bands(rows, PAR_BAND_IDS, "incident_photon_fraction")
    par_absorbed = _sum_bands(rows, PAR_BAND_IDS, "absorbed_photon_fraction")
    blue_incident = _sum_band(rows, "blue", "incident_photon_fraction")
    blue_absorbed = _sum_band(rows, "blue", "absorbed_photon_fraction")
    red_incident = _sum_band(rows, "red", "incident_photon_fraction")
    red_absorbed = _sum_band(rows, "red", "absorbed_photon_fraction")
    far_red_incident = _sum_band(rows, "far_red", "incident_photon_fraction")
    far_red_absorbed = _sum_band(rows, "far_red", "absorbed_photon_fraction")
    far_red_transmitted = _sum_band(rows, "far_red", "transmitted_photon_fraction")

    return {
        "schema": SPECTRAL_RESPONSE_SCHEMA,
        "schema_version": SPECTRAL_RESPONSE_SCHEMA_VERSION,
        "method": method,
        "status": "scaffold",
        "units": {
            "wavelength": "nm",
            "photon_fraction": "fraction_of_incident_photons",
        },
        "band_count": len(rows),
        "total_incident_photon_fraction": total_incident_fraction,
        "unmodeled_incident_photon_fraction": max(0.0, 1.0 - total_incident_fraction),
        "total_absorbed_photon_fraction": total_absorbed_fraction,
        "total_reflected_photon_fraction": total_reflected_fraction,
        "total_transmitted_photon_fraction": total_transmitted_fraction,
        "weighted_leaf_absorptance": _ratio(total_absorbed_fraction, total_incident_fraction),
        "bands": rows,
        "response_inputs": {
            "par_incident_photon_fraction": par_incident,
            "par_absorbed_photon_fraction": par_absorbed,
            "blue_incident_photon_fraction": blue_incident,
            "blue_absorbed_photon_fraction": blue_absorbed,
            "red_incident_photon_fraction": red_incident,
            "red_absorbed_photon_fraction": red_absorbed,
            "far_red_incident_photon_fraction": far_red_incident,
            "far_red_absorbed_photon_fraction": far_red_absorbed,
            "far_red_transmitted_photon_fraction": far_red_transmitted,
            "incident_red_to_far_red_ratio": _ratio(red_incident, far_red_incident),
            "absorbed_red_to_far_red_ratio": _ratio(red_absorbed, far_red_absorbed),
            "absorbed_blue_to_par_fraction": _ratio(blue_absorbed, par_absorbed),
        },
        "outputs_do_not_predict": list(NO_CROP_OUTPUT_TERMS),
        "limitations": [
            "Band-level leaf optics are explicit model inputs, not inferred crop outcomes.",
            "This scaffold does not compute photosynthesis, morphology, biomass, or yield.",
            "Future phases should connect these band summaries to measured SPD data and validated response models.",
        ],
    }


def default_leafy_green_spectral_bands() -> list[LeafSpectralOpticalBand]:
    """Return conservative placeholder bands for development and tests.

    These are not cultivar-specific measured optical properties. They exist so
    downstream spectral-response code has a deterministic contract while real
    measured leaf reflectance/transmittance data are added later.
    """

    return [
        LeafSpectralOpticalBand("uv_a", 315.0, 400.0, reflectance=0.08, transmittance=0.03),
        LeafSpectralOpticalBand("blue", 400.0, 500.0, reflectance=0.08, transmittance=0.04),
        LeafSpectralOpticalBand("green", 500.0, 600.0, reflectance=0.14, transmittance=0.10),
        LeafSpectralOpticalBand("red", 600.0, 700.0, reflectance=0.07, transmittance=0.04),
        LeafSpectralOpticalBand("far_red", 700.0, 750.0, reflectance=0.18, transmittance=0.28),
    ]
