"""Wavelength-binned leaf spectral absorption artifacts.

This module keeps Radiance-derived scalar plant receiver flux separate from
modeled leaf optical absorption. When an optical profile is explicitly selected,
source photon fractions are scaled on the profile wavelength grid so PAR sums
to the scalar receiver PPFD before computing absorbed, reflected, and
transmitted photon flux. It is not a photosynthesis, morphology, biomass, or
yield model.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from rad_rebuild.radiance.engine.plants.optical_profiles import (
    LeafOpticalProfile,
    REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,
    load_leaf_optical_profile,
)
from rad_rebuild.radiance.engine.plants.spectral import (
    SpectralPhotonDistribution,
    _candidate_spd_files,
    _curve_file_weight,
    _parse_spd_samples,
)

PLANT_SPECTRAL_ABSORPTION_SCHEMA = "rad_rebuild.fspm.plant_spectral_absorption.v1"
PLANT_SPECTRAL_ABSORPTION_SCHEMA_VERSION = 1
PLANT_SPECTRAL_ABSORPTION_FILENAME = "plant_spectral_absorption.json"
PLANT_SPECTRAL_ABSORPTION_METHOD = "wavelength_binned_leaf_optical_profile_absorption_v1"
PLANT_SURFACE_FLUX_SCHEMA = "rad_rebuild.fspm.plant_surface_flux.v1"
FSPM_LEAF_OPTICAL_PROFILE_ID_ENV = "FSPM_LEAF_OPTICAL_PROFILE_ID"
DEFAULT_FSPM_LEAF_OPTICAL_PROFILE_ID = REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1
SCALAR_FLUX_BASIS_PAR_PPFD = "par_ppfd_umol_m2_s"
SOURCE_SPECTRAL_BASIS_WAVELENGTH_RESOLVED_SPD = "wavelength_resolved_spd"
SOURCE_SPECTRAL_BASIS_BAND_FRACTION_LEGACY = "band_fraction_legacy"
NO_CROP_OUTPUT_TERMS = ["yield", "biomass", "growth", "crop_output"]
TARGET_CAPPED_ABSORPTION_BASIS = (
    "response_effective_target_capped_modeled_spectral_absorption"
)
TARGET_CAP_SCALE_BASIS = (
    "min(1,target_range_upper_ppfd_umol_m2_s/incident_par_ppfd_umol_m2_s)"
)
LEAF_MATERIAL_METADATA_KEYS: tuple[str, ...] = (
    "fspm_spectral_transport_mode",
    "leaf_radiance_material_mode",
    "leaf_material_weighting_basis",
    "leaf_material_profile_id",
    "leaf_material_profile_version",
    "leaf_material_source_spectrum_id",
    "leaf_material_source_spectrum_source",
    "leaf_material_effective_reflectance",
    "leaf_material_effective_transmittance",
    "leaf_material_effective_absorptance",
    "leaf_material_radiance_primitive",
    "leaf_material_transmission_assumption",
    "leaf_material_specular_reflectance",
    "leaf_material_specular_transmittance_fraction",
    "leaf_material_radiance_red",
    "leaf_material_radiance_green",
    "leaf_material_radiance_blue",
    "leaf_material_radiance_trans",
    "leaf_material_radiance_tspec",
)
BANDED_TRANSPORT_METADATA_KEYS: tuple[str, ...] = (
    "fspm_spectral_transport_mode",
    "leaf_radiance_material_mode",
    "leaf_material_weighting_basis",
    "leaf_material_profile_id",
    "leaf_material_profile_version",
    "leaf_material_source_spectrum_id",
    "leaf_material_source_spectrum_source",
    "band_scaling_basis",
    "banded_transport_band_count",
    "banded_transport_active_trace_count",
    "banded_transport_bands",
    "fspm_rtrace_profile_enabled",
    "fspm_rtrace_nproc",
    "fspm_rtrace_ambient_mode",
    "receiver_trace_process_policy",
    "receiver_trace_streams_per_active_band",
    "receiver_subprocess_granularity",
    "par_band_ids",
    "epar_band_ids",
    "scalar_flux_basis",
    "source_spectrum_id",
    "source_spectrum_source",
    "source_spectral_basis",
    "source_spectrum_basis",
)

SPECTRAL_ABSORPTION_BANDS: tuple[dict[str, Any], ...] = (
    {
        "band_id": "par",
        "wavelength_min_nm": 400,
        "wavelength_max_nm": 700,
        "upper_bound": "exclusive",
    },
    {
        "band_id": "epar",
        "wavelength_min_nm": 400,
        "wavelength_max_nm": 750,
        "upper_bound": "inclusive",
    },
    {
        "band_id": "blue",
        "wavelength_min_nm": 400,
        "wavelength_max_nm": 499,
        "upper_bound": "inclusive",
    },
    {
        "band_id": "green",
        "wavelength_min_nm": 500,
        "wavelength_max_nm": 599,
        "upper_bound": "inclusive",
    },
    {
        "band_id": "orange",
        "wavelength_min_nm": 600,
        "wavelength_max_nm": 624,
        "upper_bound": "inclusive",
    },
    {
        "band_id": "red",
        "wavelength_min_nm": 625,
        "wavelength_max_nm": 699,
        "upper_bound": "inclusive",
    },
    {
        "band_id": "far_red",
        "wavelength_min_nm": 700,
        "wavelength_max_nm": 750,
        "upper_bound": "inclusive",
    },
)


@dataclass(frozen=True)
class WavelengthPhotonDistribution:
    """Source photon fractions aligned to a leaf optical profile wavelength grid."""

    distribution_id: str
    wavelength_nm: tuple[int, ...]
    photon_fraction_per_nm: tuple[float, ...]
    source_spectral_basis: str
    source: str
    source_files: tuple[str, ...] = ()
    scalar_flux_basis: str = SCALAR_FLUX_BASIS_PAR_PPFD
    normalization_basis: str = "par_integral"

    def __post_init__(self) -> None:
        if not self.distribution_id:
            raise ValueError("distribution_id must be non-empty.")
        if len(self.wavelength_nm) != len(self.photon_fraction_per_nm):
            raise ValueError("wavelength and photon-fraction arrays must align.")
        if not self.wavelength_nm:
            raise ValueError("wavelength grid must not be empty.")
        if tuple(sorted(self.wavelength_nm)) != self.wavelength_nm:
            raise ValueError("wavelength_nm must be sorted ascending.")
        for value in self.photon_fraction_per_nm:
            if not math.isfinite(value) or value < 0.0:
                raise ValueError("photon fractions must be finite and non-negative.")

    def band_fraction(self, band_id: str) -> float:
        return sum(
            fraction
            for wavelength, fraction in zip(
                self.wavelength_nm,
                self.photon_fraction_per_nm,
                strict=True,
            )
            if _wavelength_in_band(wavelength, band_id)
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "distribution_id": self.distribution_id,
            "source_spectral_basis": self.source_spectral_basis,
            "scalar_flux_basis": self.scalar_flux_basis,
            "normalization_basis": self.normalization_basis,
            "source": self.source,
            "source_files": list(self.source_files),
            "par_photon_fraction_sum": self.band_fraction("par"),
            "epar_photon_fraction_sum": self.band_fraction("epar"),
            "far_red_photon_fraction_sum": self.band_fraction("far_red"),
        }


def leaf_optical_profile_from_env(
    env: Mapping[str, str],
    *,
    data_root: str | Path | None = None,
) -> LeafOpticalProfile | None:
    """Load the explicitly selected leaf optical profile, if any."""

    profile_id = (env.get(FSPM_LEAF_OPTICAL_PROFILE_ID_ENV) or DEFAULT_FSPM_LEAF_OPTICAL_PROFILE_ID).strip()
    if not profile_id:
        return None
    return load_leaf_optical_profile(profile_id, data_root=data_root)


def wavelength_photon_distribution_from_samples(
    samples: Iterable[tuple[float, float]],
    target_wavelength_nm: Iterable[int],
    *,
    distribution_id: str,
    source: str,
    source_files: Iterable[str] = (),
    source_spectral_basis: str = SOURCE_SPECTRAL_BASIS_WAVELENGTH_RESOLVED_SPD,
    scalar_flux_basis: str = SCALAR_FLUX_BASIS_PAR_PPFD,
) -> WavelengthPhotonDistribution:
    """Interpolate relative spectral power samples and normalize PAR photons to 1."""

    wavelengths = tuple(int(wavelength) for wavelength in target_wavelength_nm)
    ordered_samples = _combine_samples(samples)
    if not ordered_samples:
        raise ValueError("At least one positive SPD sample is required.")

    photon_weights: list[float] = []
    for wavelength in wavelengths:
        relative_power = _interpolate_samples(ordered_samples, float(wavelength))
        photon_weights.append(relative_power * float(wavelength))

    par_weight = sum(
        weight
        for wavelength, weight in zip(wavelengths, photon_weights, strict=True)
        if _wavelength_in_band(wavelength, "par")
    )
    if par_weight <= 0.0:
        raise ValueError("Source spectrum has no positive PAR photons on the profile grid.")

    return WavelengthPhotonDistribution(
        distribution_id=distribution_id,
        wavelength_nm=wavelengths,
        photon_fraction_per_nm=tuple(weight / par_weight for weight in photon_weights),
        source_spectral_basis=source_spectral_basis,
        scalar_flux_basis=scalar_flux_basis,
        source=source,
        source_files=tuple(source_files),
    )


def wavelength_photon_distribution_from_curve_data(
    curve_data_root: str | Path,
    mode: str | None,
    target_wavelength_nm: Iterable[int],
    *,
    env: Mapping[str, str] | None = None,
    fallback_distribution: SpectralPhotonDistribution | None = None,
) -> WavelengthPhotonDistribution:
    """Build a wavelength distribution from mode-specific curve-data SPD files."""

    mode_dir_name = _mode_dir_name(mode)
    mode_dir = Path(curve_data_root) / mode_dir_name
    files = _candidate_spd_files(mode_dir)
    source_files: list[str] = []
    weighted_samples: list[tuple[float, float]] = []

    for file_path in files:
        samples = _parse_spd_samples(file_path)
        if not samples:
            continue
        source_files.append(str(file_path))
        weight = _curve_file_weight(file_path, env)
        weighted_samples.extend((wavelength, value * weight) for wavelength, value in samples)

    if weighted_samples:
        return wavelength_photon_distribution_from_samples(
            weighted_samples,
            target_wavelength_nm,
            distribution_id=f"curve_data_{mode_dir_name}",
            source="curve_data_spd:" + "|".join(source_files),
            source_files=source_files,
        )

    if fallback_distribution is None:
        raise ValueError(f"No usable SPD samples found under {mode_dir}")
    return wavelength_photon_distribution_from_band_fractions(
        fallback_distribution,
        target_wavelength_nm,
    )


def wavelength_photon_distribution_from_band_fractions(
    distribution: SpectralPhotonDistribution,
    target_wavelength_nm: Iterable[int],
) -> WavelengthPhotonDistribution:
    """Expand legacy coarse band fractions across profile wavelengths."""

    wavelengths = tuple(int(wavelength) for wavelength in target_wavelength_nm)
    fractions = distribution.fraction_map()
    per_nm = [0.0 for _ in wavelengths]
    for band_id, band_fraction in fractions.items():
        indices = [
            index
            for index, wavelength in enumerate(wavelengths)
            if _legacy_band_contains_wavelength(band_id, wavelength)
        ]
        if not indices:
            continue
        share = band_fraction / len(indices)
        for index in indices:
            per_nm[index] += share

    par_sum = sum(
        value
        for wavelength, value in zip(wavelengths, per_nm, strict=True)
        if _wavelength_in_band(wavelength, "par")
    )
    if par_sum <= 0.0:
        raise ValueError("Legacy band fractions have no PAR photons on the profile grid.")

    return WavelengthPhotonDistribution(
        distribution_id=distribution.distribution_id,
        wavelength_nm=wavelengths,
        photon_fraction_per_nm=tuple(value / par_sum for value in per_nm),
        source_spectral_basis=SOURCE_SPECTRAL_BASIS_BAND_FRACTION_LEGACY,
        source=distribution.source,
    )


def build_plant_spectral_absorption_payload(
    surface_flux_payload: Mapping[str, Any],
    optical_profile: LeafOpticalProfile,
    photon_distribution: WavelengthPhotonDistribution,
    *,
    method: str = PLANT_SPECTRAL_ABSORPTION_METHOD,
) -> dict[str, Any]:
    """Compute wavelength-binned modeled leaf absorption from scalar surface flux."""

    _validate_profile_distribution_alignment(optical_profile, photon_distribution)
    surface_rows = _surface_flux_rows(surface_flux_payload)
    grid = _spectral_grid_payload(optical_profile, photon_distribution)
    target_context = _target_cap_context(surface_flux_payload)
    surface_summaries = [
        _surface_absorption_summary(row, optical_profile, photon_distribution)
        for row in surface_rows
    ]
    _apply_target_capped_absorption(surface_summaries, target_context)
    leaf_summaries = _aggregate_by(surface_summaries, key_name="leaf_id")
    plant_summaries = _aggregate_by(surface_summaries, key_name="plant_id")
    crop_summary = _aggregate_summary(surface_summaries)
    _add_target_leaf_fractions(crop_summary, leaf_summaries, target_context)

    return {
        "schema": PLANT_SPECTRAL_ABSORPTION_SCHEMA,
        "schema_version": PLANT_SPECTRAL_ABSORPTION_SCHEMA_VERSION,
        "artifact_role": "modeled_spectral_leaf_photon_absorption",
        "artifact_description": (
            "Modeled wavelength-binned absorbed, reflected, and transmitted "
            "leaf photon flux from an explicitly selected optical profile."
        ),
        "status": "computed",
        "method": method,
        "source_artifact": "runtime_state/plant_surface_flux.json",
        "source_surface_flux_schema": surface_flux_payload.get("schema"),
        "source_surface_flux_method": surface_flux_payload.get("method"),
        "source_surface_flux_status": surface_flux_payload.get("status"),
        **_target_cap_metadata(target_context),
        "baseline_transport_scene": surface_flux_payload.get("baseline_transport_scene"),
        "fspm_receiver_transport_scene": surface_flux_payload.get(
            "fspm_receiver_transport_scene"
        ),
        "receiver_trace_count": surface_flux_payload.get("receiver_trace_count"),
        "receiver_sample_count": surface_flux_payload.get("receiver_sample_count"),
        "receiver_granularity": surface_flux_payload.get("receiver_granularity"),
        "receiver_samples_per_leaf": surface_flux_payload.get(
            "receiver_samples_per_leaf"
        ),
        "receiver_generation_basis": surface_flux_payload.get(
            "receiver_generation_basis"
        ),
        "receiver_represented_area_m2": surface_flux_payload.get(
            "receiver_represented_area_m2"
        ),
        "receiver_sample_area_sum_m2": surface_flux_payload.get(
            "receiver_sample_area_sum_m2"
        ),
        "receiver_area_basis": surface_flux_payload.get("receiver_area_basis"),
        "receiver_side_policy": surface_flux_payload.get("receiver_side_policy"),
        "receiver_rows_per_mesh_surface_row": surface_flux_payload.get(
            "receiver_rows_per_mesh_surface_row"
        ),
        "normal_generation_basis": surface_flux_payload.get("normal_generation_basis"),
        "receiver_granularity_role": surface_flux_payload.get(
            "receiver_granularity_role"
        ),
        **{
            key: surface_flux_payload.get(key)
            for key in LEAF_MATERIAL_METADATA_KEYS
            if key in surface_flux_payload
        },
        "optical_profile": {
            "profile_id": optical_profile.profile_id,
            "profile_version": optical_profile.profile_version,
            "species": optical_profile.species,
            "cultivar": optical_profile.cultivar,
            "growth_stage": optical_profile.growth_stage,
            "leaf_side_basis": optical_profile.leaf_side_basis,
            "source": optical_profile.source,
            "data_provenance": optical_profile.data_provenance,
            "validation_status": optical_profile.validation_status,
        },
        "source_spectrum": photon_distribution.to_payload(),
        "source_spectral_basis": photon_distribution.source_spectral_basis,
        "scalar_flux_basis": photon_distribution.scalar_flux_basis,
        "wavelength_range_used_nm": {
            "min": min(photon_distribution.wavelength_nm),
            "max": max(photon_distribution.wavelength_nm),
            "count": len(photon_distribution.wavelength_nm),
        },
        "band_definitions": [dict(band) for band in SPECTRAL_ABSORPTION_BANDS],
        "spectral_grid": grid,
        "absorptance_basis_coverage": _absorptance_basis_coverage(
            optical_profile,
            photon_distribution,
        ),
        "units": {
            "wavelength": "nm",
            "photon_fraction": "fraction_of_scalar_par_ppfd",
            "photon_flux": "umol/s",
            "photon_flux_density": "umol/m2/s",
            "area": "m2",
            "optical_coefficients": "fraction",
        },
        "plant_count": surface_flux_payload.get("plant_count"),
        "leaf_count": surface_flux_payload.get("leaf_count"),
        "surface_count": surface_flux_payload.get("surface_count"),
        "crop_summary": crop_summary,
        "plant_summaries": plant_summaries,
        "leaf_summaries": leaf_summaries,
        "surface_summaries": surface_summaries,
        "outputs_do_not_predict": list(NO_CROP_OUTPUT_TERMS),
        "warnings": [
            "Scalar plant receiver flux is treated as PAR PPFD and source spectra are normalized so PAR photons sum to that scalar value.",
            "Wavelength-resolved SPD data are used when available; legacy coarse band fractions are explicitly labeled if used.",
            "Target-capped spectral absorption fields are response-effective lighting-analysis metrics only; raw modeled optical accounting fields are preserved.",
        ],
        "limitations": [
            "This artifact computes modeled spectral leaf photon absorption, not photosynthesis, morphology, biomass, or yield.",
            "Plant geometry is not inserted into a plant-shaded receiver octree in this phase.",
            "Target-capped fields are not biological absorption, yield, biomass, photosynthesis, or validated photoinhibition predictions.",
        ],
    }


def write_plant_spectral_absorption_artifact(
    target_dir: str | Path,
    surface_flux_payload: Mapping[str, Any],
    optical_profile: LeafOpticalProfile,
    photon_distribution: WavelengthPhotonDistribution,
) -> Path:
    output_dir = Path(target_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_plant_spectral_absorption_payload(
        surface_flux_payload,
        optical_profile,
        photon_distribution,
    )
    path = output_dir / PLANT_SPECTRAL_ABSORPTION_FILENAME
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def build_banded_plant_spectral_absorption_payload(
    surface_flux_payload: Mapping[str, Any],
    band_surface_flux_rows: Mapping[str, Iterable[Mapping[str, Any]]],
    banded_transport_metadata: Mapping[str, Any],
    *,
    method: str = "banded_5_radiance_leaf_receiver_transport_v1",
) -> dict[str, Any]:
    """Aggregate Level 3B band-traced receiver rows into spectral absorption."""

    band_metadata = {
        str(band.get("band_id")): band
        for band in banded_transport_metadata.get("banded_transport_bands", ())
        if isinstance(band, Mapping) and band.get("band_id")
    }
    band_summaries = [
        _banded_absorption_summary(
            band_id,
            list(rows),
            band_metadata.get(band_id, {}),
        )
        for band_id, rows in band_surface_flux_rows.items()
    ]
    band_summaries_by_id = {summary["band_id"]: summary for summary in band_summaries}
    par_band_ids = list(banded_transport_metadata.get("par_band_ids", ()))
    epar_band_ids = list(banded_transport_metadata.get("epar_band_ids", ()))
    target_context = _target_cap_context(surface_flux_payload)
    surface_summaries = _banded_surface_absorption_summaries(
        band_surface_flux_rows,
        band_metadata,
        par_band_ids=par_band_ids,
        epar_band_ids=epar_band_ids,
    )
    _apply_target_capped_absorption(surface_summaries, target_context)
    leaf_summaries = _aggregate_by(surface_summaries, key_name="leaf_id") if surface_summaries else []
    plant_summaries = _aggregate_by(surface_summaries, key_name="plant_id") if surface_summaries else []
    target_summary = _aggregate_summary(surface_summaries) if surface_summaries else {}
    crop_summary = _banded_crop_summary(
        band_summaries_by_id,
        par_band_ids=par_band_ids,
        epar_band_ids=epar_band_ids,
    )
    _copy_target_capped_summary_fields(crop_summary, target_summary)
    _add_target_leaf_fractions(crop_summary, leaf_summaries, target_context)
    _add_target_capped_band_summary_fields(band_summaries, target_summary)
    optical_profile = _banded_optical_profile_payload(surface_flux_payload)
    source_spectrum = _banded_source_spectrum_payload(surface_flux_payload)

    return {
        "schema": PLANT_SPECTRAL_ABSORPTION_SCHEMA,
        "schema_version": PLANT_SPECTRAL_ABSORPTION_SCHEMA_VERSION,
        "artifact_role": "modeled_banded_spectral_leaf_photon_absorption",
        "artifact_description": (
            "Modeled five-band absorbed, reflected, and transmitted leaf photon "
            "flux from band-specific FSPM receiver traces."
        ),
        "status": "computed",
        "method": method,
        "source_artifact": "runtime_state/plant_surface_flux.json",
        "source_surface_flux_schema": surface_flux_payload.get("schema"),
        "source_surface_flux_method": surface_flux_payload.get("method"),
        "source_surface_flux_status": surface_flux_payload.get("status"),
        **_target_cap_metadata(target_context),
        "baseline_transport_scene": surface_flux_payload.get("baseline_transport_scene"),
        "fspm_receiver_transport_scene": surface_flux_payload.get(
            "fspm_receiver_transport_scene"
        ),
        "receiver_trace_count": surface_flux_payload.get("receiver_trace_count"),
        "receiver_sample_count": surface_flux_payload.get("receiver_sample_count"),
        "receiver_granularity": surface_flux_payload.get("receiver_granularity"),
        "receiver_samples_per_leaf": surface_flux_payload.get(
            "receiver_samples_per_leaf"
        ),
        "receiver_generation_basis": surface_flux_payload.get(
            "receiver_generation_basis"
        ),
        "receiver_granularity_role": surface_flux_payload.get(
            "receiver_granularity_role"
        ),
        **{
            key: surface_flux_payload.get(key)
            for key in BANDED_TRANSPORT_METADATA_KEYS
            if key in surface_flux_payload
        },
        "optical_profile": optical_profile,
        "source_spectrum": source_spectrum,
        "units": {
            "wavelength": "nm",
            "photon_flux": "umol/s",
            "photon_flux_density": "umol/m2/s",
            "area": "m2",
            "optical_coefficients": "fraction",
        },
        "plant_count": surface_flux_payload.get("plant_count"),
        "leaf_count": surface_flux_payload.get("leaf_count"),
        "surface_count": surface_flux_payload.get("surface_count"),
        "crop_summary": crop_summary,
        "band_summaries": band_summaries,
        "plant_summaries": plant_summaries,
        "leaf_summaries": leaf_summaries,
        "surface_summaries": surface_summaries,
        "outputs_do_not_predict": list(NO_CROP_OUTPUT_TERMS),
        "warnings": [
            "Banded transport uses one FSPM receiver trace per active source band.",
            "Far-red contributes to ePAR summaries but is not included in PAR summaries.",
            "Target-capped spectral absorption fields are response-effective lighting-analysis metrics only; raw modeled optical accounting fields are preserved.",
        ],
        "limitations": [
            "This artifact computes modeled spectral leaf photon absorption, not photosynthesis, morphology, biomass, or yield.",
            "Target-capped fields are not biological absorption, yield, biomass, photosynthesis, or validated photoinhibition predictions.",
        ],
    }


def write_banded_plant_spectral_absorption_artifact(
    target_dir: str | Path,
    surface_flux_payload: Mapping[str, Any],
    band_surface_flux_rows: Mapping[str, Iterable[Mapping[str, Any]]],
    banded_transport_metadata: Mapping[str, Any],
) -> Path:
    output_dir = Path(target_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_banded_plant_spectral_absorption_payload(
        surface_flux_payload,
        band_surface_flux_rows,
        banded_transport_metadata,
    )
    path = output_dir / PLANT_SPECTRAL_ABSORPTION_FILENAME
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _banded_absorption_summary(
    band_id: str,
    rows: list[Mapping[str, Any]],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    reflectance = _finite_unit_interval(
        metadata.get("effective_reflectance", 0.0),
        f"{band_id}.effective_reflectance",
    )
    transmittance = _finite_unit_interval(
        metadata.get("effective_transmittance", 0.0),
        f"{band_id}.effective_transmittance",
    )
    absorptance = _finite_unit_interval(
        metadata.get("effective_absorptance", 0.0),
        f"{band_id}.effective_absorptance",
    )
    area = sum(float(row.get("area_m2", 0.0)) for row in rows)
    incident_flux = sum(
        float(row.get("incident_photon_flux_umol_s", 0.0)) for row in rows
    )
    incident_density_weighted = (
        incident_flux / area if area > 0.0 else 0.0
    )
    absorbed_flux = incident_flux * absorptance
    reflected_flux = incident_flux * reflectance
    transmitted_flux = incident_flux * transmittance
    return {
        "band_id": band_id,
        "wavelength_min_nm": metadata.get("wavelength_min_nm"),
        "wavelength_max_nm": metadata.get("wavelength_max_nm"),
        "included_in_par": bool(metadata.get("included_in_par")),
        "included_in_epar": bool(metadata.get("included_in_epar")),
        "source_photon_fraction_relative_to_par": float(
            metadata.get("source_photon_fraction_relative_to_par", 0.0)
        ),
        "band_has_source_photons": bool(metadata.get("band_has_source_photons")),
        "receiver_trace_required": bool(metadata.get("receiver_trace_required")),
        "receiver_trace_executed": bool(metadata.get("receiver_trace_executed")),
        "effective_reflectance": reflectance,
        "effective_transmittance": transmittance,
        "effective_absorptance": absorptance,
        "area_m2": area,
        "incident_pfd_umol_m2_s": incident_density_weighted,
        "absorbed_pfd_umol_m2_s": incident_density_weighted * absorptance,
        "reflected_pfd_umol_m2_s": incident_density_weighted * reflectance,
        "transmitted_pfd_umol_m2_s": incident_density_weighted * transmittance,
        "incident_photon_flux_umol_s": incident_flux,
        "absorbed_photon_flux_umol_s": absorbed_flux,
        "reflected_photon_flux_umol_s": reflected_flux,
        "transmitted_photon_flux_umol_s": transmitted_flux,
    }


def _banded_crop_summary(
    band_summaries_by_id: Mapping[str, Mapping[str, Any]],
    *,
    par_band_ids: Iterable[str],
    epar_band_ids: Iterable[str],
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    area_candidates: list[float] = []
    for band_id, band in band_summaries_by_id.items():
        area_candidates.append(float(band.get("area_m2", 0.0)))
        summary[f"incident_{band_id}_ppfd_umol_m2_s"] = band[
            "incident_pfd_umol_m2_s"
        ]
        summary[f"absorbed_{band_id}_ppfd_umol_m2_s"] = band[
            "absorbed_pfd_umol_m2_s"
        ]
        summary[f"reflected_{band_id}_ppfd_umol_m2_s"] = band[
            "reflected_pfd_umol_m2_s"
        ]
        summary[f"transmitted_{band_id}_ppfd_umol_m2_s"] = band[
            "transmitted_pfd_umol_m2_s"
        ]

    for label, band_ids in (("par", par_band_ids), ("epar", epar_band_ids)):
        selected = [
            band_summaries_by_id[band_id]
            for band_id in band_ids
            if band_id in band_summaries_by_id
        ]
        for quantity, source_key in (
            ("incident", "incident_pfd_umol_m2_s"),
            ("absorbed", "absorbed_pfd_umol_m2_s"),
            ("reflected", "reflected_pfd_umol_m2_s"),
            ("transmitted", "transmitted_pfd_umol_m2_s"),
        ):
            summary[f"{quantity}_{label}_ppfd_umol_m2_s"] = sum(
                float(band[source_key]) for band in selected
            )
    summary["area_m2"] = next((area for area in area_candidates if area > 0.0), 0.0)
    summary["scalar_incident_par_ppfd_umol_m2_s"] = summary.get(
        "incident_par_ppfd_umol_m2_s",
        0.0,
    )
    for label in ("par", "epar"):
        incident = float(summary.get(f"incident_{label}_ppfd_umol_m2_s", 0.0))
        if incident > 0.0:
            summary[f"absorbed_fraction_of_incident_{label}"] = (
                float(summary.get(f"absorbed_{label}_ppfd_umol_m2_s", 0.0))
                / incident
            )
            summary[f"reflected_fraction_of_incident_{label}"] = (
                float(summary.get(f"reflected_{label}_ppfd_umol_m2_s", 0.0))
                / incident
            )
            summary[f"transmitted_fraction_of_incident_{label}"] = (
                float(summary.get(f"transmitted_{label}_ppfd_umol_m2_s", 0.0))
                / incident
            )
        else:
            summary[f"absorbed_fraction_of_incident_{label}"] = 0.0
            summary[f"reflected_fraction_of_incident_{label}"] = 0.0
            summary[f"transmitted_fraction_of_incident_{label}"] = 0.0
    summary["fraction_basis"] = "par"
    summary["absorbed_fraction"] = summary["absorbed_fraction_of_incident_par"]
    summary["reflected_fraction"] = summary["reflected_fraction_of_incident_par"]
    summary["transmitted_fraction"] = summary["transmitted_fraction_of_incident_par"]
    return summary


def _banded_optical_profile_payload(surface_flux_payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "profile_id": surface_flux_payload.get("leaf_material_profile_id"),
        "profile_version": surface_flux_payload.get("leaf_material_profile_version"),
    }


def _banded_source_spectrum_payload(surface_flux_payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "distribution_id": surface_flux_payload.get("source_spectrum_id")
        or surface_flux_payload.get("leaf_material_source_spectrum_id"),
        "source_spectral_basis": surface_flux_payload.get("source_spectral_basis")
        or surface_flux_payload.get("source_spectrum_basis"),
        "scalar_flux_basis": surface_flux_payload.get("scalar_flux_basis"),
        "source": surface_flux_payload.get("source_spectrum_source")
        or surface_flux_payload.get("leaf_material_source_spectrum_source"),
    }


def _finite_unit_interval(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number.")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise ValueError(f"{name} must be in [0, 1].")
    return number


def _mode_dir_name(mode: str | None) -> str:
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


def _combine_samples(samples: Iterable[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    totals: dict[float, float] = {}
    for wavelength, value in samples:
        wavelength_float = float(wavelength)
        value_float = float(value)
        if not math.isfinite(wavelength_float) or not math.isfinite(value_float):
            continue
        if value_float <= 0.0:
            continue
        totals[wavelength_float] = totals.get(wavelength_float, 0.0) + value_float
    return tuple(sorted(totals.items()))


def _interpolate_samples(samples: tuple[tuple[float, float], ...], wavelength_nm: float) -> float:
    if wavelength_nm < samples[0][0] or wavelength_nm > samples[-1][0]:
        return 0.0
    for index, (sample_wavelength, sample_value) in enumerate(samples):
        if math.isclose(wavelength_nm, sample_wavelength, abs_tol=1e-9):
            return sample_value
        if sample_wavelength > wavelength_nm:
            prev_wavelength, prev_value = samples[index - 1]
            span = sample_wavelength - prev_wavelength
            if span <= 0.0:
                return sample_value
            fraction = (wavelength_nm - prev_wavelength) / span
            return prev_value * (1.0 - fraction) + sample_value * fraction
    return 0.0


def _surface_flux_rows(surface_flux_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if surface_flux_payload.get("schema") != PLANT_SURFACE_FLUX_SCHEMA:
        raise ValueError("Unsupported plant surface-flux schema.")
    rows = surface_flux_payload.get("surface_summaries")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Plant surface-flux payload must include surface_summaries.")
    return rows


def _validate_profile_distribution_alignment(
    optical_profile: LeafOpticalProfile,
    photon_distribution: WavelengthPhotonDistribution,
) -> None:
    if optical_profile.wavelength_nm != photon_distribution.wavelength_nm:
        raise ValueError("Optical profile and source spectrum wavelength grids must align.")


def _wavelength_in_band(wavelength_nm: int, band_id: str) -> bool:
    band = next(
        (item for item in SPECTRAL_ABSORPTION_BANDS if item["band_id"] == band_id),
        None,
    )
    if band is None:
        return False
    wavelength = int(wavelength_nm)
    lower = int(band["wavelength_min_nm"])
    upper = int(band["wavelength_max_nm"])
    if band.get("upper_bound") == "exclusive":
        return lower <= wavelength < upper
    return lower <= wavelength <= upper


def _legacy_band_contains_wavelength(band_id: str, wavelength_nm: int) -> bool:
    if band_id == "uv_a":
        return 315 <= wavelength_nm < 400
    if band_id == "blue":
        return 400 <= wavelength_nm < 500
    if band_id == "green":
        return 500 <= wavelength_nm < 600
    if band_id == "red":
        return 600 <= wavelength_nm < 700
    if band_id == "far_red":
        return 700 <= wavelength_nm <= 750
    return _wavelength_in_band(wavelength_nm, band_id)


def _absorptance_source_basis(profile: LeafOpticalProfile, index: int) -> str:
    if profile.raw_absorptance[index] is not None:
        return "raw_digitized"
    if profile.implied_absorptance[index] is not None:
        return "implied_from_reflectance_transmittance"
    return profile.absorptance_basis[index]


def _spectral_grid_payload(
    profile: LeafOpticalProfile,
    distribution: WavelengthPhotonDistribution,
) -> dict[str, Any]:
    return {
        "wavelength_nm": list(profile.wavelength_nm),
        "source_photon_fraction_per_nm": list(distribution.photon_fraction_per_nm),
        "absorptance": list(profile.absorptance),
        "reflectance": list(profile.reflectance),
        "transmittance": list(profile.transmittance),
        "absorptance_basis": list(profile.absorptance_basis),
        "absorptance_source_basis": [
            _absorptance_source_basis(profile, index)
            for index in range(len(profile.wavelength_nm))
        ],
    }


def _finite_non_negative(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a JSON number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    if number < 0.0:
        raise ValueError(f"{name} must be greater than or equal to zero.")
    return number


def _empty_band_totals() -> dict[str, dict[str, float | None]]:
    return {
        str(band["band_id"]): {
            "incident_photon_flux_umol_s": 0.0,
            "absorbed_photon_flux_umol_s": 0.0,
            "reflected_photon_flux_umol_s": 0.0,
            "transmitted_photon_flux_umol_s": 0.0,
        }
        for band in SPECTRAL_ABSORPTION_BANDS
    }


def _surface_absorption_summary(
    row: Mapping[str, Any],
    profile: LeafOpticalProfile,
    distribution: WavelengthPhotonDistribution,
) -> dict[str, Any]:
    surface_id = str(row.get("surface_id") or "")
    if not surface_id:
        raise ValueError("Each surface summary must include surface_id.")
    area = _finite_non_negative(f"area_m2[{surface_id}]", row.get("area_m2"))
    if area <= 0.0:
        raise ValueError(f"area_m2[{surface_id}] must be positive.")
    scalar_flux = _finite_non_negative(
        f"incident_photon_flux_umol_s[{surface_id}]",
        row.get("incident_photon_flux_umol_s"),
    )
    scalar_density = scalar_flux / area
    band_totals = _empty_band_totals()

    total_incident = 0.0
    total_absorbed = 0.0
    total_reflected = 0.0
    total_transmitted = 0.0

    for index, wavelength in enumerate(profile.wavelength_nm):
        incident_density = scalar_density * distribution.photon_fraction_per_nm[index]
        incident_flux = incident_density * area
        absorbed_flux = incident_flux * profile.absorptance[index]
        reflected_flux = incident_flux * profile.reflectance[index]
        transmitted_flux = incident_flux * profile.transmittance[index]

        total_incident += incident_flux
        total_absorbed += absorbed_flux
        total_reflected += reflected_flux
        total_transmitted += transmitted_flux

        for band_id in band_totals:
            if _wavelength_in_band(wavelength, band_id):
                band_totals[band_id]["incident_photon_flux_umol_s"] += incident_flux
                band_totals[band_id]["absorbed_photon_flux_umol_s"] += absorbed_flux
                band_totals[band_id]["reflected_photon_flux_umol_s"] += reflected_flux
                band_totals[band_id]["transmitted_photon_flux_umol_s"] += transmitted_flux

    result = {
        "surface_id": surface_id,
        "plant_id": row.get("plant_id"),
        "leaf_id": row.get("leaf_id"),
        "leaf_index": row.get("leaf_index"),
        "face_index": row.get("face_index"),
        "area_m2": area,
        "scalar_incident_par_photon_flux_umol_s": scalar_flux,
        "scalar_incident_par_ppfd_umol_m2_s": scalar_density,
        "total_incident_photon_flux_umol_s": total_incident,
        "total_absorbed_photon_flux_umol_s": total_absorbed,
        "total_reflected_photon_flux_umol_s": total_reflected,
        "total_transmitted_photon_flux_umol_s": total_transmitted,
        "absorbed_fraction": _ratio(total_absorbed, total_incident),
        "reflected_fraction": _ratio(total_reflected, total_incident),
        "transmitted_fraction": _ratio(total_transmitted, total_incident),
        "band_totals": band_totals,
    }
    _add_band_density_fields(result)
    return result


def _aggregate_by(
    rows: list[dict[str, Any]],
    *,
    key_name: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = row.get(key_name)
        if not isinstance(key, str) or not key:
            raise ValueError(f"Spectral absorption row missing {key_name}.")
        grouped.setdefault(key, []).append(row)

    summaries = []
    for key, group_rows in grouped.items():
        summary = _aggregate_summary(group_rows)
        summary[key_name] = key
        if key_name == "leaf_id":
            summary["plant_id"] = group_rows[0].get("plant_id")
        summaries.append(summary)
    return sorted(summaries, key=lambda item: str(item[key_name]))


def _aggregate_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    area = sum(float(row["area_m2"]) for row in rows)
    band_totals = _empty_band_totals()
    target_capped_band_totals = _empty_band_totals()
    totals = {
        "scalar_incident_par_photon_flux_umol_s": 0.0,
        "total_incident_photon_flux_umol_s": 0.0,
        "total_absorbed_photon_flux_umol_s": 0.0,
        "total_reflected_photon_flux_umol_s": 0.0,
        "total_transmitted_photon_flux_umol_s": 0.0,
    }
    target_totals = {
        "target_capped_total_incident_photon_flux_umol_s": 0.0,
        "target_capped_total_absorbed_photon_flux_umol_s": 0.0,
        "target_capped_total_reflected_photon_flux_umol_s": 0.0,
        "target_capped_total_transmitted_photon_flux_umol_s": 0.0,
    }
    for row in rows:
        for key in totals:
            totals[key] += float(row[key])
        for key in target_totals:
            target_totals[key] += float(row.get(key, 0.0) or 0.0)
        for band_id, band_values in row["band_totals"].items():
            for total_name, value in band_values.items():
                band_totals[band_id][total_name] += float(value or 0.0)
        capped = row.get("target_capped_band_totals")
        if isinstance(capped, Mapping):
            for band_id, band_values in capped.items():
                if band_id not in target_capped_band_totals or not isinstance(
                    band_values,
                    Mapping,
                ):
                    continue
                for total_name, value in band_values.items():
                    target_capped_band_totals[band_id][total_name] += float(
                        value or 0.0
                    )

    summary: dict[str, Any] = {
        "area_m2": area,
        "surface_count": len(rows),
        **totals,
        **target_totals,
        "scalar_incident_par_ppfd_umol_m2_s": (
            totals["scalar_incident_par_photon_flux_umol_s"] / area
            if area > 0.0
            else 0.0
        ),
        "absorbed_fraction": _ratio(
            totals["total_absorbed_photon_flux_umol_s"],
            totals["total_incident_photon_flux_umol_s"],
        ),
        "reflected_fraction": _ratio(
            totals["total_reflected_photon_flux_umol_s"],
            totals["total_incident_photon_flux_umol_s"],
        ),
        "transmitted_fraction": _ratio(
            totals["total_transmitted_photon_flux_umol_s"],
            totals["total_incident_photon_flux_umol_s"],
        ),
        "band_totals": band_totals,
        "target_capped_band_totals": target_capped_band_totals,
    }
    _add_band_density_fields(summary)
    _add_target_capped_density_fields(summary)
    _add_target_capped_fraction_fields(summary)
    return summary


def _add_band_density_fields(summary: dict[str, Any]) -> None:
    area = float(summary.get("area_m2", 0.0) or 0.0)
    for band_id, totals in summary["band_totals"].items():
        incident = float(totals["incident_photon_flux_umol_s"] or 0.0)
        absorbed = float(totals["absorbed_photon_flux_umol_s"] or 0.0)
        reflected = float(totals["reflected_photon_flux_umol_s"] or 0.0)
        transmitted = float(totals["transmitted_photon_flux_umol_s"] or 0.0)
        summary[f"incident_{band_id}_ppfd_umol_m2_s"] = (
            incident / area if area > 0.0 else 0.0
        )
        summary[f"absorbed_{band_id}_ppfd_umol_m2_s"] = (
            absorbed / area if area > 0.0 else 0.0
        )
        summary[f"reflected_{band_id}_ppfd_umol_m2_s"] = (
            reflected / area if area > 0.0 else 0.0
        )
        summary[f"transmitted_{band_id}_ppfd_umol_m2_s"] = (
            transmitted / area if area > 0.0 else 0.0
        )


def _target_cap_context(surface_flux_payload: Mapping[str, Any]) -> dict[str, float | None]:
    target = _optional_non_negative_float(
        surface_flux_payload.get("target_ppfd_umol_m2_s")
    )
    tolerance = _optional_non_negative_float(
        surface_flux_payload.get("target_tolerance_umol_m2_s")
    )
    lower = _optional_non_negative_float(
        surface_flux_payload.get("target_lower_threshold_umol_m2_s")
    )
    upper = _optional_non_negative_float(
        surface_flux_payload.get("target_upper_threshold_umol_m2_s")
    )
    if lower is None and target is not None and tolerance is not None:
        lower = max(0.0, target - tolerance)
    if upper is None and target is not None and tolerance is not None:
        upper = target + tolerance
    if upper is None:
        upper = target
    if lower is None:
        lower = target
    return {"lower": lower, "upper": upper}


def _target_cap_metadata(context: Mapping[str, float | None]) -> dict[str, Any]:
    return {
        "target_capped_absorption_basis": TARGET_CAPPED_ABSORPTION_BASIS,
        "target_saturation_cap_ppfd_umol_m2_s": context.get("upper"),
        "target_range_lower_ppfd_umol_m2_s": context.get("lower"),
        "target_range_upper_ppfd_umol_m2_s": context.get("upper"),
        "target_cap_scale_basis": TARGET_CAP_SCALE_BASIS,
        "raw_absorption_preserved": True,
        "not_biological_prediction": True,
    }


def _optional_non_negative_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        return None
    return number


def _target_cap_scale(incident_par_ppfd: float, cap_ppfd: float | None) -> float:
    if incident_par_ppfd <= 0.0:
        return 0.0
    if cap_ppfd is None:
        return 1.0
    return min(1.0, cap_ppfd / incident_par_ppfd)


def _apply_target_capped_absorption(
    rows: list[dict[str, Any]],
    context: Mapping[str, float | None],
) -> None:
    cap_ppfd = context.get("upper")
    for row in rows:
        incident_par = float(
            row.get("incident_par_ppfd_umol_m2_s")
            or row.get("scalar_incident_par_ppfd_umol_m2_s")
            or 0.0
        )
        cap_scale = _target_cap_scale(incident_par, cap_ppfd)
        row["target_cap_scale"] = cap_scale
        row["target_cap_scale_basis"] = TARGET_CAP_SCALE_BASIS
        capped_band_totals = _empty_band_totals()
        for band_id, band_values in row["band_totals"].items():
            if band_id not in capped_band_totals:
                continue
            for total_name, value in band_values.items():
                capped_band_totals[band_id][total_name] = (
                    float(value or 0.0) * cap_scale
                )
        row["target_capped_band_totals"] = capped_band_totals
        for total_name, source_name in (
            (
                "target_capped_total_incident_photon_flux_umol_s",
                "total_incident_photon_flux_umol_s",
            ),
            (
                "target_capped_total_absorbed_photon_flux_umol_s",
                "total_absorbed_photon_flux_umol_s",
            ),
            (
                "target_capped_total_reflected_photon_flux_umol_s",
                "total_reflected_photon_flux_umol_s",
            ),
            (
                "target_capped_total_transmitted_photon_flux_umol_s",
                "total_transmitted_photon_flux_umol_s",
            ),
        ):
            row[total_name] = float(row.get(source_name, 0.0) or 0.0) * cap_scale
        _add_target_capped_density_fields(row)
        _add_target_capped_fraction_fields(row)


def _add_target_capped_density_fields(summary: dict[str, Any]) -> None:
    area = float(summary.get("area_m2", 0.0) or 0.0)
    capped = summary.get("target_capped_band_totals")
    if not isinstance(capped, Mapping):
        return
    for band_id, totals in capped.items():
        if not isinstance(totals, Mapping):
            continue
        incident = float(totals.get("incident_photon_flux_umol_s", 0.0) or 0.0)
        absorbed = float(totals.get("absorbed_photon_flux_umol_s", 0.0) or 0.0)
        reflected = float(totals.get("reflected_photon_flux_umol_s", 0.0) or 0.0)
        transmitted = float(
            totals.get("transmitted_photon_flux_umol_s", 0.0) or 0.0
        )
        summary[f"target_capped_incident_{band_id}_ppfd_umol_m2_s"] = (
            incident / area if area > 0.0 else 0.0
        )
        summary[f"target_capped_absorbed_{band_id}_ppfd_umol_m2_s"] = (
            absorbed / area if area > 0.0 else 0.0
        )
        summary[f"target_capped_reflected_{band_id}_ppfd_umol_m2_s"] = (
            reflected / area if area > 0.0 else 0.0
        )
        summary[f"target_capped_transmitted_{band_id}_ppfd_umol_m2_s"] = (
            transmitted / area if area > 0.0 else 0.0
        )


def _add_target_capped_fraction_fields(summary: dict[str, Any]) -> None:
    for label in ("par", "epar"):
        raw_absorbed = float(
            summary.get(f"absorbed_{label}_ppfd_umol_m2_s", 0.0) or 0.0
        )
        capped_absorbed = float(
            summary.get(f"target_capped_absorbed_{label}_ppfd_umol_m2_s", 0.0) or 0.0
        )
        summary[f"excess_absorbed_{label}_ppfd_above_target_cap"] = max(
            0.0,
            raw_absorbed - capped_absorbed,
        )
        summary[f"target_capped_absorbed_{label}_fraction_of_raw"] = (
            capped_absorbed / raw_absorbed if raw_absorbed > 0.0 else 0.0
        )
    capped_incident_par = float(
        summary.get("target_capped_incident_par_ppfd_umol_m2_s", 0.0) or 0.0
    )
    capped_absorbed_par = float(
        summary.get("target_capped_absorbed_par_ppfd_umol_m2_s", 0.0) or 0.0
    )
    raw_absorbed_par = float(summary.get("absorbed_par_ppfd_umol_m2_s", 0.0) or 0.0)
    excess_absorbed_par = float(
        summary.get("excess_absorbed_par_ppfd_above_target_cap", 0.0) or 0.0
    )
    summary["target_effective_absorbed_fraction"] = (
        capped_absorbed_par / capped_incident_par if capped_incident_par > 0.0 else 0.0
    )
    summary["over_target_absorbed_par_fraction_of_raw"] = (
        excess_absorbed_par / raw_absorbed_par if raw_absorbed_par > 0.0 else 0.0
    )
    _add_target_capped_reporting_aliases(summary)


def _add_target_capped_reporting_aliases(summary: dict[str, Any]) -> None:
    for band_id in ("par", "epar", "blue", "green", "orange", "red", "far_red"):
        key = f"target_capped_absorbed_{band_id}_ppfd_umol_m2_s"
        if key in summary:
            summary[f"target_capped_absorbed_{band_id}_ppfd"] = summary[key]


def _add_target_leaf_fractions(
    summary: dict[str, Any],
    leaf_summaries: list[dict[str, Any]],
    context: Mapping[str, float | None],
) -> None:
    lower = context.get("lower")
    upper = context.get("upper")
    total = len(leaf_summaries)
    under = 0
    in_target = 0
    over = 0
    for row in leaf_summaries:
        incident = float(
            row.get("incident_par_ppfd_umol_m2_s")
            or row.get("scalar_incident_par_ppfd_umol_m2_s")
            or 0.0
        )
        if lower is not None and incident < lower:
            under += 1
        elif upper is not None and incident > upper:
            over += 1
        else:
            in_target += 1
    summary["under_target_leaf_fraction"] = under / total if total else 0.0
    summary["in_target_leaf_fraction"] = in_target / total if total else 0.0
    summary["over_target_leaf_fraction"] = over / total if total else 0.0


def _copy_target_capped_summary_fields(
    crop_summary: dict[str, Any],
    target_summary: Mapping[str, Any],
) -> None:
    for key, value in target_summary.items():
        if key.startswith("target_capped_") or key.startswith("excess_absorbed_"):
            crop_summary[key] = value
    for key in (
        "target_effective_absorbed_fraction",
        "over_target_absorbed_par_fraction_of_raw",
        "target_capped_absorbed_par_fraction_of_raw",
        "target_capped_absorbed_epar_fraction_of_raw",
    ):
        if key in target_summary:
            crop_summary[key] = target_summary[key]


def _add_target_capped_band_summary_fields(
    band_summaries: list[dict[str, Any]],
    target_summary: Mapping[str, Any],
) -> None:
    for band in band_summaries:
        band_id = str(band.get("band_id") or "")
        if not band_id:
            continue
        for quantity in ("incident", "absorbed", "reflected", "transmitted"):
            key = f"target_capped_{quantity}_{band_id}_ppfd_umol_m2_s"
            if key in target_summary:
                band[f"target_capped_{quantity}_pfd_umol_m2_s"] = target_summary[key]


def _banded_surface_absorption_summaries(
    band_surface_flux_rows: Mapping[str, Iterable[Mapping[str, Any]]],
    band_metadata: Mapping[str, Mapping[str, Any]],
    *,
    par_band_ids: Iterable[str],
    epar_band_ids: Iterable[str],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    par_ids = {str(band_id) for band_id in par_band_ids}
    epar_ids = {str(band_id) for band_id in epar_band_ids}
    for band_id, rows in band_surface_flux_rows.items():
        metadata = band_metadata.get(str(band_id), {})
        reflectance = _finite_unit_interval(
            metadata.get("effective_reflectance", 0.0),
            f"{band_id}.effective_reflectance",
        )
        transmittance = _finite_unit_interval(
            metadata.get("effective_transmittance", 0.0),
            f"{band_id}.effective_transmittance",
        )
        absorptance = _finite_unit_interval(
            metadata.get("effective_absorptance", 0.0),
            f"{band_id}.effective_absorptance",
        )
        for row in rows:
            surface_id = str(row.get("surface_id") or "")
            if not surface_id:
                raise ValueError("Each banded receiver row must include surface_id.")
            area = _finite_non_negative(f"area_m2[{surface_id}]", row.get("area_m2"))
            incident_flux = _finite_non_negative(
                f"incident_photon_flux_umol_s[{surface_id}:{band_id}]",
                row.get("incident_photon_flux_umol_s"),
            )
            item = grouped.setdefault(
                surface_id,
                {
                    "surface_id": surface_id,
                    "plant_id": row.get("plant_id"),
                    "leaf_id": row.get("leaf_id"),
                    "leaf_index": row.get("leaf_index"),
                    "face_index": row.get("face_index"),
                    "area_m2": area,
                    "scalar_incident_par_photon_flux_umol_s": 0.0,
                    "total_incident_photon_flux_umol_s": 0.0,
                    "total_absorbed_photon_flux_umol_s": 0.0,
                    "total_reflected_photon_flux_umol_s": 0.0,
                    "total_transmitted_photon_flux_umol_s": 0.0,
                    "band_totals": _empty_band_totals(),
                },
            )
            if area > 0.0:
                item["area_m2"] = area
            absorbed_flux = incident_flux * absorptance
            reflected_flux = incident_flux * reflectance
            transmitted_flux = incident_flux * transmittance
            item["total_incident_photon_flux_umol_s"] += incident_flux
            item["total_absorbed_photon_flux_umol_s"] += absorbed_flux
            item["total_reflected_photon_flux_umol_s"] += reflected_flux
            item["total_transmitted_photon_flux_umol_s"] += transmitted_flux
            if str(band_id) in par_ids:
                item["scalar_incident_par_photon_flux_umol_s"] += incident_flux
            if str(band_id) in item["band_totals"]:
                band_totals = item["band_totals"][str(band_id)]
                band_totals["incident_photon_flux_umol_s"] += incident_flux
                band_totals["absorbed_photon_flux_umol_s"] += absorbed_flux
                band_totals["reflected_photon_flux_umol_s"] += reflected_flux
                band_totals["transmitted_photon_flux_umol_s"] += transmitted_flux
    summaries = list(grouped.values())
    for summary in summaries:
        _synthesize_composite_band_totals(summary, "par", par_ids)
        _synthesize_composite_band_totals(summary, "epar", epar_ids)
        area = float(summary.get("area_m2", 0.0) or 0.0)
        summary["scalar_incident_par_ppfd_umol_m2_s"] = (
            float(summary["scalar_incident_par_photon_flux_umol_s"]) / area
            if area > 0.0
            else 0.0
        )
        summary["absorbed_fraction"] = _ratio(
            float(summary["total_absorbed_photon_flux_umol_s"]),
            float(summary["total_incident_photon_flux_umol_s"]),
        )
        summary["reflected_fraction"] = _ratio(
            float(summary["total_reflected_photon_flux_umol_s"]),
            float(summary["total_incident_photon_flux_umol_s"]),
        )
        summary["transmitted_fraction"] = _ratio(
            float(summary["total_transmitted_photon_flux_umol_s"]),
            float(summary["total_incident_photon_flux_umol_s"]),
        )
        _add_band_density_fields(summary)
    return sorted(summaries, key=lambda item: str(item["surface_id"]))


def _synthesize_composite_band_totals(
    summary: dict[str, Any],
    composite_band_id: str,
    source_band_ids: set[str],
) -> None:
    band_totals = summary.get("band_totals")
    if not isinstance(band_totals, dict) or composite_band_id not in band_totals:
        return
    for total_name in band_totals[composite_band_id]:
        band_totals[composite_band_id][total_name] = sum(
            float(band_totals.get(band_id, {}).get(total_name, 0.0) or 0.0)
            for band_id in source_band_ids
        )


def _absorptance_basis_coverage(
    profile: LeafOpticalProfile,
    distribution: WavelengthPhotonDistribution,
) -> dict[str, Any]:
    source_basis = [
        _absorptance_source_basis(profile, index)
        for index in range(len(profile.wavelength_nm))
    ]
    used_indices = [
        index
        for index, fraction in enumerate(distribution.photon_fraction_per_nm)
        if fraction > 0.0
    ]
    total_weight = sum(distribution.photon_fraction_per_nm[index] for index in used_indices)
    counts: dict[str, int] = {}
    weighted: dict[str, float] = {}
    for index in used_indices:
        basis = source_basis[index]
        counts[basis] = counts.get(basis, 0) + 1
        weighted[basis] = weighted.get(basis, 0.0) + distribution.photon_fraction_per_nm[index]

    return {
        "wavelength_count_with_source_photons": len(used_indices),
        "source_basis_counts": dict(sorted(counts.items())),
        "source_basis_photon_weighted_fraction": {
            basis: value / total_weight if total_weight > 0.0 else 0.0
            for basis, value in sorted(weighted.items())
        },
        "raw_digitized_fraction": (
            weighted.get("raw_digitized", 0.0) / total_weight
            if total_weight > 0.0
            else 0.0
        ),
        "implied_from_reflectance_transmittance_fraction": (
            sum(
                value
                for basis, value in weighted.items()
                if basis.startswith("implied_from_reflectance_transmittance")
            )
            / total_weight
            if total_weight > 0.0
            else 0.0
        ),
    }


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0.0:
        return None
    return numerator / denominator
