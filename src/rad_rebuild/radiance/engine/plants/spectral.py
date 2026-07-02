"""Spectral leaf-optics contracts for FSPM response modeling.

This module is intentionally pure engine code. It does not run Radiance,
predict yield, or claim cultivar-specific biology. It provides validated
band-level photon absorption factors and response-input summaries that later
photosynthesis and photomorphogenesis modules can consume.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

SPECTRAL_RESPONSE_SCHEMA = "rad_rebuild.fspm.plant_spectral_response.scaffold.v1"
SPECTRAL_RESPONSE_SCHEMA_VERSION = 1
SPECTRAL_ABSORPTION_METHOD = "band_weighted_leaf_absorptance_v1"
NO_CROP_OUTPUT_TERMS = ["yield", "biomass", "growth", "crop_output"]

PAR_BAND_IDS = frozenset({"blue", "green", "red"})
SPECTRAL_BAND_RANGES_NM: tuple[tuple[str, float, float], ...] = (
    ("uv_a", 315.0, 400.0),
    ("blue", 400.0, 500.0),
    ("green", 500.0, 600.0),
    ("red", 600.0, 700.0),
    ("far_red", 700.0, 750.0),
)
_NUMERIC_TOKEN_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


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


PLANT_SPECTRAL_RESPONSE_SCHEMA = "rad_rebuild.fspm.plant_spectral_response.v1"
PLANT_SPECTRAL_RESPONSE_SCHEMA_VERSION = 1
PLANT_SPECTRAL_RESPONSE_FILENAME = "plant_spectral_response.json"
PLANT_SURFACE_FLUX_SCHEMA = "rad_rebuild.fspm.plant_surface_flux.v1"
PLANT_SPECTRAL_ABSORPTION_SCHEMA = "rad_rebuild.fspm.plant_spectral_absorption.v1"
PLANT_SPECTRAL_RESPONSE_METHOD = "surface_flux_band_weighted_leaf_absorptance_v1"
PLANT_SPECTRAL_RESPONSE_METHOD_BANDED = "banded_5_receiver_absorption_response_v1"
BANDED_RESPONSE_SOURCE_BASIS = "banded_5_receiver_absorption"


def _finite_non_negative(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a JSON number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    if number < 0.0:
        raise ValueError(f"{name} must be greater than or equal to zero.")
    return number


def default_fixture_spectral_distribution(mode: str | None = None) -> SpectralPhotonDistribution:
    """Return deterministic development spectral photon fractions by fixture mode.

    These are model-input defaults, not measured SPD claims. Later phases can
    replace them with measured SPD-derived fractions.
    """

    label = (mode or "").lower()
    if "hps" in label:
        fractions = {
            "blue": 0.04,
            "green": 0.39,
            "red": 0.46,
            "far_red": 0.11,
        }
        distribution_id = "development_default_hps"
    elif "competitor" in label or "conventional" in label or "spydr" in label:
        fractions = {
            "blue": 0.16,
            "green": 0.34,
            "red": 0.44,
            "far_red": 0.06,
        }
        distribution_id = "development_default_conventional_led"
    else:
        fractions = {
            "blue": 0.20,
            "green": 0.36,
            "red": 0.40,
            "far_red": 0.04,
        }
        distribution_id = "development_default_proposed_led"

    return SpectralPhotonDistribution(
        distribution_id,
        tuple(
            SpectralPhotonFraction(band_id, fraction)
            for band_id, fraction in fractions.items()
        ),
        source="development_default_fixture_spectral_distribution",
    )


def parse_spectral_photon_fraction_overrides(
    text: str,
    *,
    distribution_id: str = "env_override",
) -> SpectralPhotonDistribution:
    """Parse band fractions such as 'blue=0.2,green=0.35,red=0.4,far_red=0.05'."""

    entries: list[SpectralPhotonFraction] = []
    for raw_item in text.replace(";", ",").split(","):
        item = raw_item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Invalid spectral fraction item: {item!r}")
        band_id, raw_value = item.split("=", 1)
        entries.append(SpectralPhotonFraction(band_id.strip(), float(raw_value.strip())))

    return SpectralPhotonDistribution(
        distribution_id,
        tuple(entries),
        source="environment_override",
    )



def _spectral_mode_dir_name(mode: str | None) -> str:
    label = (mode or "").strip().lower().replace("_", " ")
    if "hps" in label or "gavita" in label:
        return "hps"
    if (
        "conventional" in label
        or "competitor" in label
        or "spydr" in label
        or "qube" in label
    ):
        return "conventional"
    return "smd"


def _band_id_for_wavelength_nm(wavelength_nm: float) -> str | None:
    for band_id, low_nm, high_nm in SPECTRAL_BAND_RANGES_NM:
        if low_nm <= wavelength_nm < high_nm:
            return band_id
    if math.isclose(wavelength_nm, 750.0):
        return "far_red"
    return None


def _candidate_spd_files(mode_dir: Path) -> list[Path]:
    if not mode_dir.is_dir():
        return []
    candidates = sorted(
        path
        for path in mode_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".csv", ".txt", ".tsv", ".dat"}
    )
    preferred_tokens = ("combined", "system", "total", "aggregate", "weighted")
    preferred = [
        path
        for path in candidates
        if any(token in path.stem.lower() for token in preferred_tokens)
    ]
    if preferred:
        return preferred
    spectral_tokens = ("spd", "spectrum", "spectral", "wavelength", "nm")
    spectral_named = [
        path
        for path in candidates
        if any(token in path.stem.lower() for token in spectral_tokens)
    ]
    return spectral_named or candidates


def _curve_file_weight(path: Path, env: Mapping[str, str] | None) -> float:
    """Approximate SMD channel weighting when raw channel SPDs are present."""

    if env is None:
        return 1.0
    name = path.stem.lower()
    if any(token in name for token in ("combined", "system", "total", "aggregate", "weighted")):
        return 1.0

    def env_float(key: str, default: float) -> float:
        try:
            return float(env.get(key, "") or default)
        except ValueError:
            return default

    def env_count(key: str, default: float) -> float:
        try:
            return float(env.get(key, "") or default)
        except ValueError:
            return default

    if "red" in name and "far" not in name:
        return (
            env_count("SMD_RED_COUNT", 41.0)
            * env_float("SMD_RED_NOMINAL_W", 0.44)
            * env_float("SMD_RED_NOMINAL_PPE", 4.13)
        )
    if any(token in name for token in ("cw", "cool")):
        return (
            env_count("SMD_CW_COUNT", 52.0)
            * env_float("SMD_CW_NOMINAL_W", 0.68)
            * env_float("SMD_CW_NOMINAL_PPE", 2.81)
        )
    if any(token in name for token in ("ww", "warm")):
        return (
            env_count("SMD_WW_COUNT", 52.0)
            * env_float("SMD_WW_NOMINAL_W", 0.68)
            * env_float("SMD_WW_NOMINAL_PPE", 2.73)
        )
    if "white" in name:
        warm = (
            env_count("SMD_WW_COUNT", 52.0)
            * env_float("SMD_WW_NOMINAL_W", 0.68)
            * env_float("SMD_WW_NOMINAL_PPE", 2.73)
        )
        cool = (
            env_count("SMD_CW_COUNT", 52.0)
            * env_float("SMD_CW_NOMINAL_W", 0.68)
            * env_float("SMD_CW_NOMINAL_PPE", 2.81)
        )
        return warm + cool
    return 1.0


def _parse_spd_samples(path: Path) -> list[tuple[float, float]]:
    samples: list[tuple[float, float]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return samples

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = _NUMERIC_TOKEN_RE.findall(stripped)
        if len(tokens) < 2:
            continue
        try:
            wavelength_nm = float(tokens[0])
            value = float(tokens[-1])
        except ValueError:
            continue
        if not math.isfinite(wavelength_nm) or not math.isfinite(value):
            continue
        if value <= 0.0:
            continue
        if _band_id_for_wavelength_nm(wavelength_nm) is None:
            continue
        samples.append((wavelength_nm, value))
    return samples


def _samples_to_photon_fractions(samples: list[tuple[float, float]]) -> dict[str, float]:
    totals = {band_id: 0.0 for band_id, _low, _high in SPECTRAL_BAND_RANGES_NM}
    for wavelength_nm, relative_power in samples:
        band_id = _band_id_for_wavelength_nm(wavelength_nm)
        if band_id is None:
            continue
        # For relative spectral power data, photon count is proportional to power * wavelength.
        totals[band_id] += relative_power * wavelength_nm

    total = sum(totals.values())
    if total <= 0.0:
        raise ValueError("SPD samples did not contain positive in-band energy.")
    return {
        band_id: value / total
        for band_id, value in totals.items()
        if value > 0.0
    }


def fixture_spectral_distribution_from_curve_data(
    curve_data_root: str | Path,
    mode: str | None,
    *,
    env: Mapping[str, str] | None = None,
    fallback_to_defaults: bool = True,
) -> SpectralPhotonDistribution:
    """Build fixture spectral photon fractions from mode-specific curve-data SPDs."""

    mode_dir_name = _spectral_mode_dir_name(mode)
    mode_dir = Path(curve_data_root) / mode_dir_name
    files = _candidate_spd_files(mode_dir)
    weighted_samples: list[tuple[float, float]] = []
    source_files: list[str] = []

    for file_path in files:
        samples = _parse_spd_samples(file_path)
        if not samples:
            continue
        source_files.append(str(file_path))
        weight = _curve_file_weight(file_path, env)
        weighted_samples.extend(
            (wavelength_nm, value * weight)
            for wavelength_nm, value in samples
        )

    if not weighted_samples:
        if fallback_to_defaults:
            return default_fixture_spectral_distribution(mode)
        raise ValueError(f"No usable SPD samples found under {mode_dir}")

    fractions = _samples_to_photon_fractions(weighted_samples)
    return SpectralPhotonDistribution(
        f"curve_data_{mode_dir_name}",
        tuple(
            SpectralPhotonFraction(band_id, fractions[band_id])
            for band_id in sorted(fractions)
        ),
        source="curve_data_spd:" + "|".join(source_files),
    )

def _surface_flux_rows(surface_flux_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if surface_flux_payload.get("schema") != PLANT_SURFACE_FLUX_SCHEMA:
        raise ValueError("Unsupported plant surface-flux schema.")
    rows = surface_flux_payload.get("surface_summaries")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Plant surface-flux payload must include surface_summaries.")
    return rows


def _visual_value(value: float, *, min_value: float, max_value: float) -> float:
    if max_value <= min_value:
        return 0.5
    return min(1.0, max(0.0, (value - min_value) / (max_value - min_value)))


def _lighting_region(value: float, mean_value: float) -> str:
    if mean_value <= 0.0:
        return "nominal"
    if value < mean_value * 0.80:
        return "under_lit"
    if value > mean_value * 1.20:
        return "over_lit"
    return "nominal"


def _empty_band_totals(bands: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    return {
        str(band["band_id"]): {
            "incident_photon_flux_umol_s": 0.0,
            "absorbed_photon_flux_umol_s": 0.0,
            "reflected_photon_flux_umol_s": 0.0,
            "transmitted_photon_flux_umol_s": 0.0,
        }
        for band in bands
    }


def _surface_spectral_summary(
    row: Mapping[str, Any],
    bands: list[dict[str, Any]],
) -> dict[str, Any]:
    surface_id = str(row.get("surface_id") or "")
    if not surface_id:
        raise ValueError("Each surface summary must include surface_id.")

    incident_flux = _finite_non_negative(
        f"incident_photon_flux_umol_s[{surface_id}]",
        row.get("incident_photon_flux_umol_s"),
    )
    area = _finite_non_negative(f"area_m2[{surface_id}]", row.get("area_m2"))
    if area <= 0.0:
        raise ValueError(f"area_m2[{surface_id}] must be positive.")

    band_rows: list[dict[str, Any]] = []
    band_totals = _empty_band_totals(bands)

    for band in bands:
        band_id = str(band["band_id"])
        incident = incident_flux * float(band["incident_photon_fraction"])
        absorbed = incident * float(band["absorptance"])
        reflected = incident * float(band["reflectance"])
        transmitted = incident * float(band["transmittance"])

        band_totals[band_id]["incident_photon_flux_umol_s"] += incident
        band_totals[band_id]["absorbed_photon_flux_umol_s"] += absorbed
        band_totals[band_id]["reflected_photon_flux_umol_s"] += reflected
        band_totals[band_id]["transmitted_photon_flux_umol_s"] += transmitted

        band_rows.append(
            {
                "band_id": band_id,
                "wavelength_min_nm": band["wavelength_min_nm"],
                "wavelength_max_nm": band["wavelength_max_nm"],
                "incident_photon_flux_umol_s": incident,
                "absorbed_photon_flux_umol_s": absorbed,
                "reflected_photon_flux_umol_s": reflected,
                "transmitted_photon_flux_umol_s": transmitted,
                "absorbed_photon_flux_density_umol_m2_s": absorbed / area,
            }
        )

    absorbed_par = sum(
        item["absorbed_photon_flux_umol_s"]
        for item in band_rows
        if item["band_id"] in PAR_BAND_IDS
    )
    absorbed_blue = sum(
        item["absorbed_photon_flux_umol_s"]
        for item in band_rows
        if item["band_id"] == "blue"
    )
    absorbed_red = sum(
        item["absorbed_photon_flux_umol_s"]
        for item in band_rows
        if item["band_id"] == "red"
    )
    absorbed_far_red = sum(
        item["absorbed_photon_flux_umol_s"]
        for item in band_rows
        if item["band_id"] == "far_red"
    )
    transmitted_far_red = sum(
        item["transmitted_photon_flux_umol_s"]
        for item in band_rows
        if item["band_id"] == "far_red"
    )

    return {
        "surface_id": surface_id,
        "plant_id": row.get("plant_id"),
        "leaf_id": row.get("leaf_id"),
        "leaf_index": row.get("leaf_index"),
        "face_index": row.get("face_index"),
        "area_m2": area,
        "incident_photon_flux_umol_s": incident_flux,
        "absorbed_photon_flux_umol_s": sum(
            item["absorbed_photon_flux_umol_s"]
            for item in band_rows
        ),
        "absorbed_par_photon_flux_umol_s": absorbed_par,
        "absorbed_blue_photon_flux_umol_s": absorbed_blue,
        "absorbed_red_photon_flux_umol_s": absorbed_red,
        "absorbed_far_red_photon_flux_umol_s": absorbed_far_red,
        "transmitted_far_red_photon_flux_umol_s": transmitted_far_red,
        "absorbed_photon_flux_density_umol_m2_s": sum(
            item["absorbed_photon_flux_umol_s"]
            for item in band_rows
        )
        / area,
        "absorbed_par_photon_flux_density_umol_m2_s": absorbed_par / area,
        "absorbed_red_to_far_red_ratio": _ratio(absorbed_red, absorbed_far_red),
        "absorbed_blue_to_par_fraction": _ratio(absorbed_blue, absorbed_par),
        "bands": band_rows,
        "band_totals": band_totals,
    }


def _aggregate_spectral_rows(
    rows: list[dict[str, Any]],
    *,
    key_name: str,
    bands: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    for row in rows:
        key = row.get(key_name)
        if not isinstance(key, str) or not key:
            raise ValueError(f"Spectral row missing {key_name}.")
        item = grouped.setdefault(
            key,
            {
                key_name: key,
                "plant_id": row.get("plant_id"),
                "leaf_id": row.get("leaf_id") if key_name == "leaf_id" else None,
                "area_m2": 0.0,
                "surface_count": 0,
                "incident_photon_flux_umol_s": 0.0,
                "absorbed_photon_flux_umol_s": 0.0,
                "absorbed_par_photon_flux_umol_s": 0.0,
                "absorbed_blue_photon_flux_umol_s": 0.0,
                "absorbed_red_photon_flux_umol_s": 0.0,
                "absorbed_far_red_photon_flux_umol_s": 0.0,
                "transmitted_far_red_photon_flux_umol_s": 0.0,
                "band_totals": _empty_band_totals(bands),
            },
        )

        item["area_m2"] += float(row["area_m2"])
        item["surface_count"] += 1
        for name in (
            "incident_photon_flux_umol_s",
            "absorbed_photon_flux_umol_s",
            "absorbed_par_photon_flux_umol_s",
            "absorbed_blue_photon_flux_umol_s",
            "absorbed_red_photon_flux_umol_s",
            "absorbed_far_red_photon_flux_umol_s",
            "transmitted_far_red_photon_flux_umol_s",
        ):
            item[name] += float(row[name])

        for band_id, totals in row["band_totals"].items():
            for total_name, value in totals.items():
                item["band_totals"][band_id][total_name] += float(value)

    aggregated: list[dict[str, Any]] = []
    for item in grouped.values():
        area = float(item["area_m2"])
        absorbed_par = float(item["absorbed_par_photon_flux_umol_s"])
        absorbed_blue = float(item["absorbed_blue_photon_flux_umol_s"])
        absorbed_red = float(item["absorbed_red_photon_flux_umol_s"])
        absorbed_far_red = float(item["absorbed_far_red_photon_flux_umol_s"])
        item["absorbed_photon_flux_density_umol_m2_s"] = (
            float(item["absorbed_photon_flux_umol_s"]) / area if area > 0.0 else 0.0
        )
        item["absorbed_par_photon_flux_density_umol_m2_s"] = (
            absorbed_par / area if area > 0.0 else 0.0
        )
        item["absorbed_red_to_far_red_ratio"] = _ratio(absorbed_red, absorbed_far_red)
        item["absorbed_blue_to_par_fraction"] = _ratio(absorbed_blue, absorbed_par)
        aggregated.append(item)

    values = [
        float(item["absorbed_par_photon_flux_density_umol_m2_s"])
        for item in aggregated
    ]
    mean_value = sum(values) / len(values) if values else 0.0
    for item in aggregated:
        item["lighting_region"] = _lighting_region(
            float(item["absorbed_par_photon_flux_density_umol_m2_s"]),
            mean_value,
        )

    return sorted(aggregated, key=lambda item: str(item[key_name]))


def _band_totals_from_surfaces(
    rows: list[dict[str, Any]],
    bands: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    totals = _empty_band_totals(bands)
    for row in rows:
        for band_id, band_values in row["band_totals"].items():
            for key, value in band_values.items():
                totals[band_id][key] += float(value)
    return totals


def _banded_absorption_rows(
    spectral_absorption_payload: Mapping[str, Any],
    key: str,
) -> list[Mapping[str, Any]]:
    rows = spectral_absorption_payload.get(key)
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"Banded spectral-absorption payload must include {key}.")
    return rows


def _copy_absorption_band_totals(
    row: Mapping[str, Any],
) -> dict[str, dict[str, float]]:
    raw_totals = row.get("band_totals")
    if not isinstance(raw_totals, Mapping):
        raise ValueError("Banded spectral-absorption rows must include band_totals.")

    copied: dict[str, dict[str, float]] = {}
    for band_id, raw_band in raw_totals.items():
        if not isinstance(raw_band, Mapping):
            raise ValueError(f"band_totals[{band_id}] must be an object.")
        copied[str(band_id)] = {
            "incident_photon_flux_umol_s": _finite_non_negative(
                f"{band_id}.incident_photon_flux_umol_s",
                raw_band.get("incident_photon_flux_umol_s", 0.0),
            ),
            "absorbed_photon_flux_umol_s": _finite_non_negative(
                f"{band_id}.absorbed_photon_flux_umol_s",
                raw_band.get("absorbed_photon_flux_umol_s", 0.0),
            ),
            "reflected_photon_flux_umol_s": _finite_non_negative(
                f"{band_id}.reflected_photon_flux_umol_s",
                raw_band.get("reflected_photon_flux_umol_s", 0.0),
            ),
            "transmitted_photon_flux_umol_s": _finite_non_negative(
                f"{band_id}.transmitted_photon_flux_umol_s",
                raw_band.get("transmitted_photon_flux_umol_s", 0.0),
            ),
        }
    return copied


def _banded_band_flux(
    band_totals: Mapping[str, Mapping[str, float]],
    band_id: str,
    key: str,
) -> float:
    band = band_totals.get(band_id)
    if not isinstance(band, Mapping):
        return 0.0
    return float(band.get(key, 0.0) or 0.0)


def _banded_absorbed_par_flux(
    band_totals: Mapping[str, Mapping[str, float]],
) -> float:
    if "par" in band_totals:
        return _banded_band_flux(band_totals, "par", "absorbed_photon_flux_umol_s")
    return sum(
        _banded_band_flux(band_totals, band_id, "absorbed_photon_flux_umol_s")
        for band_id in ("blue", "green", "orange", "red")
    )


def _banded_response_band_rows(
    band_totals: Mapping[str, Mapping[str, float]],
    band_summaries_by_id: Mapping[str, Mapping[str, Any]],
    *,
    area_m2: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for band_id, totals in band_totals.items():
        metadata = band_summaries_by_id.get(band_id, {})
        absorbed = float(totals.get("absorbed_photon_flux_umol_s", 0.0) or 0.0)
        rows.append(
            {
                "band_id": band_id,
                "wavelength_min_nm": metadata.get("wavelength_min_nm"),
                "wavelength_max_nm": metadata.get("wavelength_max_nm"),
                "incident_photon_flux_umol_s": float(
                    totals.get("incident_photon_flux_umol_s", 0.0) or 0.0
                ),
                "absorbed_photon_flux_umol_s": absorbed,
                "reflected_photon_flux_umol_s": float(
                    totals.get("reflected_photon_flux_umol_s", 0.0) or 0.0
                ),
                "transmitted_photon_flux_umol_s": float(
                    totals.get("transmitted_photon_flux_umol_s", 0.0) or 0.0
                ),
                "absorbed_photon_flux_density_umol_m2_s": (
                    absorbed / area_m2 if area_m2 > 0.0 else 0.0
                ),
            }
        )
    return sorted(rows, key=lambda item: str(item["band_id"]))


def _banded_response_summary(
    row: Mapping[str, Any],
    *,
    id_key: str,
    band_summaries_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    row_id = row.get(id_key)
    if not isinstance(row_id, str) or not row_id:
        raise ValueError(f"Each banded spectral-absorption row must include {id_key}.")
    area = _finite_non_negative(f"area_m2[{row_id}]", row.get("area_m2"))
    if area <= 0.0:
        raise ValueError(f"area_m2[{row_id}] must be positive.")

    band_totals = _copy_absorption_band_totals(row)
    total_incident = _finite_non_negative(
        f"total_incident_photon_flux_umol_s[{row_id}]",
        row.get("total_incident_photon_flux_umol_s"),
    )
    total_absorbed = _finite_non_negative(
        f"total_absorbed_photon_flux_umol_s[{row_id}]",
        row.get("total_absorbed_photon_flux_umol_s"),
    )
    absorbed_par = _banded_absorbed_par_flux(band_totals)
    absorbed_blue = _banded_band_flux(
        band_totals,
        "blue",
        "absorbed_photon_flux_umol_s",
    )
    absorbed_green = _banded_band_flux(
        band_totals,
        "green",
        "absorbed_photon_flux_umol_s",
    )
    absorbed_orange = _banded_band_flux(
        band_totals,
        "orange",
        "absorbed_photon_flux_umol_s",
    )
    absorbed_red = _banded_band_flux(
        band_totals,
        "red",
        "absorbed_photon_flux_umol_s",
    )
    absorbed_far_red = _banded_band_flux(
        band_totals,
        "far_red",
        "absorbed_photon_flux_umol_s",
    )
    transmitted_far_red = _banded_band_flux(
        band_totals,
        "far_red",
        "transmitted_photon_flux_umol_s",
    )

    summary = {
        id_key: row_id,
        "plant_id": row.get("plant_id"),
        "leaf_id": row.get("leaf_id"),
        "leaf_index": row.get("leaf_index"),
        "face_index": row.get("face_index"),
        "surface_count": row.get("surface_count"),
        "area_m2": area,
        "lighting_region": row.get("lighting_region") or "nominal",
        "incident_photon_flux_umol_s": total_incident,
        "absorbed_photon_flux_umol_s": total_absorbed,
        "absorbed_par_photon_flux_umol_s": absorbed_par,
        "absorbed_blue_photon_flux_umol_s": absorbed_blue,
        "absorbed_green_photon_flux_umol_s": absorbed_green,
        "absorbed_orange_photon_flux_umol_s": absorbed_orange,
        "absorbed_red_photon_flux_umol_s": absorbed_red,
        "absorbed_far_red_photon_flux_umol_s": absorbed_far_red,
        "transmitted_far_red_photon_flux_umol_s": transmitted_far_red,
        "absorbed_photon_flux_density_umol_m2_s": total_absorbed / area,
        "absorbed_par_photon_flux_density_umol_m2_s": absorbed_par / area,
        "absorbed_red_to_far_red_ratio": _ratio(absorbed_red, absorbed_far_red),
        "absorbed_blue_to_par_fraction": _ratio(absorbed_blue, absorbed_par),
        "bands": _banded_response_band_rows(
            band_totals,
            band_summaries_by_id,
            area_m2=area,
        ),
        "band_totals": band_totals,
    }
    return summary


def _banded_band_totals_from_rows(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = {}
    for row in rows:
        for band_id, band_values in row["band_totals"].items():
            band_total = totals.setdefault(
                band_id,
                {
                    "incident_photon_flux_umol_s": 0.0,
                    "absorbed_photon_flux_umol_s": 0.0,
                    "reflected_photon_flux_umol_s": 0.0,
                    "transmitted_photon_flux_umol_s": 0.0,
                },
            )
            for key, value in band_values.items():
                band_total[key] += float(value)
    return totals


def _banded_spectral_distribution_payload(
    spectral_absorption_payload: Mapping[str, Any],
) -> dict[str, Any]:
    source_spectrum = spectral_absorption_payload.get("source_spectrum")
    source = source_spectrum if isinstance(source_spectrum, Mapping) else {}
    band_summaries = spectral_absorption_payload.get("band_summaries")
    band_rows = band_summaries if isinstance(band_summaries, list) else []
    return {
        "distribution_id": source.get(
            "distribution_id",
            spectral_absorption_payload.get("source_spectrum_id"),
        ),
        "source": source.get("source", spectral_absorption_payload.get("source_spectrum_source")),
        "source_spectral_basis": spectral_absorption_payload.get("source_spectral_basis"),
        "normalization_basis": source.get("normalization_basis"),
        "photon_fractions_relative_to_par": [
            {
                "band_id": band.get("band_id"),
                "photon_fraction_relative_to_par": band.get(
                    "source_photon_fraction_relative_to_par"
                ),
            }
            for band in band_rows
            if isinstance(band, Mapping) and band.get("band_id") is not None
        ],
    }


def _banded_leaf_optics_payload(
    spectral_absorption_payload: Mapping[str, Any],
) -> dict[str, Any]:
    band_summaries = spectral_absorption_payload.get("band_summaries")
    band_rows = band_summaries if isinstance(band_summaries, list) else []
    return {
        "source": "runtime_state/plant_spectral_absorption.json",
        "basis": BANDED_RESPONSE_SOURCE_BASIS,
        "optical_profile": spectral_absorption_payload.get("optical_profile"),
        "bands": [
            {
                "band_id": band.get("band_id"),
                "wavelength_min_nm": band.get("wavelength_min_nm"),
                "wavelength_max_nm": band.get("wavelength_max_nm"),
                "reflectance": band.get("effective_reflectance"),
                "transmittance": band.get("effective_transmittance"),
                "absorptance": band.get("effective_absorptance"),
            }
            for band in band_rows
            if isinstance(band, Mapping) and band.get("band_id") is not None
        ],
    }


def _spectral_visualization_payload(
    leaf_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    key = "absorbed_par_photon_flux_density_umol_m2_s"
    values = [float(row.get(key, 0.0) or 0.0) for row in leaf_summaries]
    min_value = min(values) if values else 0.0
    max_value = max(values) if values else 0.0

    return {
        "color_metric": key,
        "normalization": "linear_0_1",
        "leaf_scale": {
            "min": min_value,
            "max": max_value,
        },
        "leaf_values": [
            {
                "leaf_id": row["leaf_id"],
                "plant_id": row["plant_id"],
                "lighting_region": row["lighting_region"],
                key: row[key],
                "absorbed_red_to_far_red_ratio": row["absorbed_red_to_far_red_ratio"],
                "absorbed_blue_to_par_fraction": row["absorbed_blue_to_par_fraction"],
                "visual_intensity_0_1": _visual_value(
                    float(row[key]),
                    min_value=min_value,
                    max_value=max_value,
                ),
            }
            for row in leaf_summaries
        ],
    }


def build_plant_spectral_response_payload(
    surface_flux_payload: Mapping[str, Any],
    optical_bands: list[LeafSpectralOpticalBand],
    photon_distribution: SpectralPhotonDistribution,
    *,
    method: str = PLANT_SPECTRAL_RESPONSE_METHOD,
) -> dict[str, Any]:
    """Convert total leaf-surface receiver flux into band-level absorption."""

    surface_flux_rows = _surface_flux_rows(surface_flux_payload)
    leaf_optics_summary = build_leaf_spectral_absorption_summary(
        optical_bands,
        photon_distribution.photon_fractions,
    )
    bands = list(leaf_optics_summary["bands"])

    surface_summaries = [
        _surface_spectral_summary(row, bands)
        for row in surface_flux_rows
    ]
    leaf_summaries = _aggregate_spectral_rows(
        surface_summaries,
        key_name="leaf_id",
        bands=bands,
    )
    plant_summaries = _aggregate_spectral_rows(
        surface_summaries,
        key_name="plant_id",
        bands=bands,
    )
    band_totals = _band_totals_from_surfaces(surface_summaries, bands)

    return {
        "schema": PLANT_SPECTRAL_RESPONSE_SCHEMA,
        "schema_version": PLANT_SPECTRAL_RESPONSE_SCHEMA_VERSION,
        "status": "computed",
        "method": method,
        "source_surface_flux_schema": surface_flux_payload.get("schema"),
        "source_surface_flux_method": surface_flux_payload.get("method"),
        "source_surface_flux_status": surface_flux_payload.get("status"),
        "source_artifact": "runtime_state/plant_surface_flux.json",
        "spectral_distribution": photon_distribution.to_payload(),
        "leaf_optics": leaf_optics_summary,
        "units": {
            "wavelength": "nm",
            "photon_flux": "umol/s",
            "photon_flux_density": "umol/m2/s",
            "area": "m2",
        },
        "plant_count": surface_flux_payload.get("plant_count"),
        "leaf_count": surface_flux_payload.get("leaf_count"),
        "surface_count": surface_flux_payload.get("surface_count"),
        "total_incident_photon_flux_umol_s": sum(
            float(row["incident_photon_flux_umol_s"])
            for row in surface_summaries
        ),
        "total_absorbed_photon_flux_umol_s": sum(
            float(row["absorbed_photon_flux_umol_s"])
            for row in surface_summaries
        ),
        "total_absorbed_par_photon_flux_umol_s": sum(
            float(row["absorbed_par_photon_flux_umol_s"])
            for row in surface_summaries
        ),
        "band_totals": band_totals,
        "plant_summaries": plant_summaries,
        "leaf_summaries": leaf_summaries,
        "surface_summaries": surface_summaries,
        "visualization": _spectral_visualization_payload(leaf_summaries),
        "outputs_do_not_predict": list(NO_CROP_OUTPUT_TERMS),
        "warnings": [
            "Spectral response uses explicit fixture spectral photon fractions and leaf optical factors.",
            "Default spectral distributions are development inputs until replaced by measured SPD-derived fractions.",
        ],
        "limitations": [
            "This artifact computes band-level absorbed photon flux, not photosynthesis, morphology, biomass, or yield.",
            "Leaf optical properties are model assumptions unless measured reflectance/transmittance data are provided.",
        ],
    }


def build_banded_plant_spectral_response_payload(
    spectral_absorption_payload: Mapping[str, Any],
    *,
    method: str = PLANT_SPECTRAL_RESPONSE_METHOD_BANDED,
) -> dict[str, Any]:
    """Build spectral response from true banded receiver absorption rows."""

    if spectral_absorption_payload.get("schema") != PLANT_SPECTRAL_ABSORPTION_SCHEMA:
        raise ValueError("Unsupported plant spectral-absorption schema.")
    if spectral_absorption_payload.get("fspm_spectral_transport_mode") != "banded_5":
        raise ValueError(
            "Banded spectral-response builder requires banded_5 spectral absorption."
        )

    band_summaries = spectral_absorption_payload.get("band_summaries")
    band_rows = band_summaries if isinstance(band_summaries, list) else []
    band_summaries_by_id = {
        str(band["band_id"]): band
        for band in band_rows
        if isinstance(band, Mapping) and band.get("band_id") is not None
    }
    surface_summaries = [
        _banded_response_summary(
            row,
            id_key="surface_id",
            band_summaries_by_id=band_summaries_by_id,
        )
        for row in _banded_absorption_rows(spectral_absorption_payload, "surface_summaries")
    ]
    leaf_summaries = [
        _banded_response_summary(
            row,
            id_key="leaf_id",
            band_summaries_by_id=band_summaries_by_id,
        )
        for row in _banded_absorption_rows(spectral_absorption_payload, "leaf_summaries")
    ]
    plant_summaries = [
        _banded_response_summary(
            row,
            id_key="plant_id",
            band_summaries_by_id=band_summaries_by_id,
        )
        for row in _banded_absorption_rows(spectral_absorption_payload, "plant_summaries")
    ]
    band_totals = _banded_band_totals_from_rows(surface_summaries)

    return {
        "schema": PLANT_SPECTRAL_RESPONSE_SCHEMA,
        "schema_version": PLANT_SPECTRAL_RESPONSE_SCHEMA_VERSION,
        "status": "computed",
        "method": method,
        "source_data_basis": BANDED_RESPONSE_SOURCE_BASIS,
        "source_artifact": "runtime_state/plant_spectral_absorption.json",
        "source_spectral_absorption_schema": spectral_absorption_payload.get("schema"),
        "source_spectral_absorption_method": spectral_absorption_payload.get("method"),
        "source_spectral_absorption_status": spectral_absorption_payload.get("status"),
        "source_surface_flux_schema": spectral_absorption_payload.get(
            "source_surface_flux_schema"
        ),
        "source_surface_flux_method": spectral_absorption_payload.get(
            "source_surface_flux_method"
        ),
        "source_surface_flux_status": spectral_absorption_payload.get(
            "source_surface_flux_status"
        ),
        "fspm_spectral_transport_mode": spectral_absorption_payload.get(
            "fspm_spectral_transport_mode"
        ),
        "par_band_ids": spectral_absorption_payload.get("par_band_ids"),
        "epar_band_ids": spectral_absorption_payload.get("epar_band_ids"),
        "spectral_distribution": _banded_spectral_distribution_payload(
            spectral_absorption_payload
        ),
        "leaf_optics": _banded_leaf_optics_payload(spectral_absorption_payload),
        "units": {
            "wavelength": "nm",
            "photon_flux": "umol/s",
            "photon_flux_density": "umol/m2/s",
            "area": "m2",
        },
        "plant_count": spectral_absorption_payload.get("plant_count"),
        "leaf_count": spectral_absorption_payload.get("leaf_count"),
        "surface_count": spectral_absorption_payload.get("surface_count"),
        "total_incident_photon_flux_umol_s": sum(
            float(row["incident_photon_flux_umol_s"])
            for row in surface_summaries
        ),
        "total_absorbed_photon_flux_umol_s": sum(
            float(row["absorbed_photon_flux_umol_s"])
            for row in surface_summaries
        ),
        "total_absorbed_par_photon_flux_umol_s": sum(
            float(row["absorbed_par_photon_flux_umol_s"])
            for row in surface_summaries
        ),
        "band_totals": band_totals,
        "plant_summaries": plant_summaries,
        "leaf_summaries": leaf_summaries,
        "surface_summaries": surface_summaries,
        "visualization": _spectral_visualization_payload(leaf_summaries),
        "outputs_do_not_predict": list(NO_CROP_OUTPUT_TERMS),
        "warnings": [
            "Spectral response uses band-resolved FSPM receiver absorption data.",
            "Far-red contributes to ePAR summaries but is not included in PAR summaries.",
        ],
        "limitations": [
            "This artifact computes band-level absorbed photon flux, not photosynthesis, morphology, biomass, or yield.",
            "Broad transport bands are not wavelength-action models.",
        ],
    }


def write_plant_spectral_response_artifact(
    target_dir: str | Path,
    surface_flux_payload: Mapping[str, Any],
    optical_bands: list[LeafSpectralOpticalBand],
    photon_distribution: SpectralPhotonDistribution,
    *,
    return_payload: bool = False,
) -> Path | tuple[Path, dict[str, Any]]:
    output_dir = Path(target_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_plant_spectral_response_payload(
        surface_flux_payload,
        optical_bands,
        photon_distribution,
    )
    path = output_dir / PLANT_SPECTRAL_RESPONSE_FILENAME
    path.write_text(
        json.dumps(compact_plant_spectral_response_payload(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return (path, payload) if return_payload else path


def write_banded_plant_spectral_response_artifact(
    target_dir: str | Path,
    spectral_absorption_payload: Mapping[str, Any],
    *,
    return_payload: bool = False,
) -> Path | tuple[Path, dict[str, Any]]:
    output_dir = Path(target_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_banded_plant_spectral_response_payload(spectral_absorption_payload)
    path = output_dir / PLANT_SPECTRAL_RESPONSE_FILENAME
    path.write_text(
        json.dumps(compact_plant_spectral_response_payload(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return (path, payload) if return_payload else path


def compact_plant_spectral_response_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(payload).items()
        if key not in {
            "plant_summaries",
            "leaf_summaries",
            "surface_summaries",
            "visualization",
        }
    }
