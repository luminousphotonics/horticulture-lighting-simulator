"""Explicit isolated-band photon-carrier output for combined SMD modules.

The scalar writer remains the geometry and Radiance-normalization authority.
This wrapper supplies a band photon yield through an explicit boundary and
replaces scalar-PAR labels with band-specific metadata before exposing the
document to callers.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Final

from fspm_optics.fixtures.proposed_cob.source import (
    COB_SOURCE_MODE,
    NATIVE_SOURCE_MODE,
    resolve_proposed_source_authority,
)
from fspm_optics.spectral.bands import FIXED_TRANSPORT_BANDS

from .module_profile import DEFAULT_SMD_MODULE_PROFILE, SmdModuleProfile
from .optical_stack import ACCEPTED_FIXTURE_TRANSMISSION
from .positions import SmdLayout
from .power_schedule import ModulePowerSchedule
from .radiance_writer import (
    SmdEmitterAssumptions,
    _build_native_document_for_internal_yield,
)

COMBINED_MODULE_EMITTING_WINDOW: Final = "combined_module_emitting_window"
NATIVE_LAMBERTIAN_ANGULAR_MODEL: Final = "native_lambertian"
ISOLATED_BAND_PHOTON_CARRIER_BASIS: Final = (
    "band_photon_radiance_umol_s_m2_sr"
)
ISOLATED_BAND_RGB_POLICY: Final = "R=G=B isolated band photon carrier"
ISOLATED_BAND_CONVERSION_POLICY: Final = (
    "direct band photon transport; no photopic 179 lm/W conversion"
)


@dataclass(frozen=True, slots=True)
class SmdBandEmitterAssumptions:
    """Explicit Proposed source mode for an isolated photon band."""

    source_mode: str = NATIVE_SOURCE_MODE


@dataclass(frozen=True, slots=True)
class SmdBandEmitterMetadata:
    """Auditable band source values with no scalar-PAR field aliases."""

    schema_version: int
    band_id: str
    module_profile_id: str
    module_count: int
    control_zone_count: int
    control_zone_ids: tuple[int, ...]
    watts_by_control_zone: tuple[float, ...]
    schedule_source: str
    min_watts: float
    max_watts: float
    total_watts: float
    internal_band_photon_yield_umol_per_j: float
    total_internal_band_photon_flux_umol_s: float
    accepted_fixture_transmission: float
    modeled_completed_aperture_band_yield_umol_per_j: float
    modeled_completed_aperture_band_photon_flux_umol_s: float
    emitter_area_per_module_m2: float
    source_radiance_by_control_zone: tuple[float, ...]
    emitter_primitive_count: int
    optical_stack_polygon_count: int
    spatial_model_id: str
    angular_model_id: str
    photon_carrier_basis: str
    rgb_policy: str
    conversion_policy: str
    optical_stack_id: str
    source_mode: str = NATIVE_SOURCE_MODE
    source_classification: str = "native_proposed_smd"
    normalized_angular_identity_sha256: str | None = None
    normalized_angular_dat_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class SmdBandRadianceDocument:
    radiance_text: str
    metadata: SmdBandEmitterMetadata
    source_angular_data_filename: str | None = None
    source_angular_data_text: str | None = None


def build_smd_band_radiance_document(
    layout: SmdLayout,
    power_schedule: ModulePowerSchedule,
    *,
    band_id: str,
    internal_band_photon_yield_umol_per_j: float,
    module_profile: SmdModuleProfile = DEFAULT_SMD_MODULE_PROFILE,
    assumptions: SmdBandEmitterAssumptions = SmdBandEmitterAssumptions(),
) -> SmdBandRadianceDocument:
    """Build one grayscale band emitter using the scalar geometry authority."""

    valid_ids = tuple(band.band_id for band in FIXED_TRANSPORT_BANDS)
    if band_id not in valid_ids:
        raise ValueError(
            f"band_id must be one of {', '.join(valid_ids)}; got {band_id!r}."
        )
    photon_yield = _positive_finite(
        "internal_band_photon_yield_umol_per_j",
        internal_band_photon_yield_umol_per_j,
    )
    authority = resolve_proposed_source_authority(assumptions.source_mode)
    completed_yield = photon_yield * authority.completed_aperture_transmission
    scalar_geometry_document = _build_native_document_for_internal_yield(
        layout,
        power_schedule,
        internal_photon_yield_umol_per_j=photon_yield,
        completed_aperture_yield_umol_per_j=completed_yield,
        module_profile=module_profile,
        completed_aperture_transmission=(
            authority.completed_aperture_transmission
        ),
        assumptions=SmdEmitterAssumptions(
            source_variant="native",
            source_mode=assumptions.source_mode,
        ),
    )
    if scalar_geometry_document.source_variant_cal_text is not None:
        raise ValueError("isolated band emitters prohibit SMD research variants.")
    text = _band_labeled_text(
        scalar_geometry_document.radiance_text,
        band_id=band_id,
        photon_yield=photon_yield,
        completed_yield=completed_yield,
    )
    base = scalar_geometry_document.metadata
    return SmdBandRadianceDocument(
        radiance_text=text,
        metadata=SmdBandEmitterMetadata(
            schema_version=2,
            band_id=band_id,
            module_profile_id=base.module_profile_id,
            module_count=base.module_count,
            control_zone_count=base.control_zone_count,
            control_zone_ids=base.control_zone_ids,
            watts_by_control_zone=base.watts_by_control_zone,
            schedule_source=base.schedule_source,
            min_watts=base.min_watts,
            max_watts=base.max_watts,
            total_watts=base.total_watts,
            internal_band_photon_yield_umol_per_j=photon_yield,
            total_internal_band_photon_flux_umol_s=base.total_internal_par_ppf_umol_s,
            accepted_fixture_transmission=base.accepted_fixture_transmission,
            modeled_completed_aperture_band_yield_umol_per_j=completed_yield,
            modeled_completed_aperture_band_photon_flux_umol_s=(
                base.modeled_completed_aperture_par_ppf_umol_s
            ),
            emitter_area_per_module_m2=base.emitter_area_per_module_m2,
            source_radiance_by_control_zone=base.source_radiance_by_control_zone,
            emitter_primitive_count=base.emitter_primitive_count,
            optical_stack_polygon_count=base.optical_stack_polygon_count,
            spatial_model_id=(
                "centered_22mm_circular_les"
                if assumptions.source_mode == COB_SOURCE_MODE
                else COMBINED_MODULE_EMITTING_WINDOW
            ),
            angular_model_id=authority.angular_model_id,
            photon_carrier_basis=ISOLATED_BAND_PHOTON_CARRIER_BASIS,
            rgb_policy=ISOLATED_BAND_RGB_POLICY,
            conversion_policy=ISOLATED_BAND_CONVERSION_POLICY,
            optical_stack_id=base.optical_stack_id,
            source_mode=base.source_mode,
            source_classification=base.source_classification,
            normalized_angular_identity_sha256=(
                base.normalized_angular_identity_sha256
            ),
            normalized_angular_dat_sha256=base.normalized_angular_dat_sha256,
        ),
        source_angular_data_filename=(
            scalar_geometry_document.source_angular_data_filename
        ),
        source_angular_data_text=(
            scalar_geometry_document.source_angular_data_text
        ),
    )


def _band_labeled_text(
    text: str,
    *,
    band_id: str,
    photon_yield: float,
    completed_yield: float,
) -> str:
    replacements = (
        (
            "# scalar PAR carrier: R=G=B photon radiance in umol s^-1 m^-2 sr^-1",
            f"# isolated band carrier: band={band_id} "
            "R=G=B photon radiance in umol s^-1 m^-2 sr^-1",
        ),
        (
            "# scalar PAR conversion: direct photon transport; "
            "no photopic 179 lm/W conversion",
            "# isolated band conversion: direct photon transport; "
            "no photopic 179 lm/W conversion",
        ),
        (
            "# source normalization occurs before transport: internal PPF = solver watts * internal source PPE",
            "# source normalization occurs before transport: internal band PPF = "
            "solver watts * internal band photon yield",
        ),
        (
            f"# internal_source_ppe_umol_per_j={photon_yield:.15g}",
            f"# internal_band_photon_yield_umol_per_j={photon_yield:.15g}",
        ),
        (
            f"# completed_aperture_fixture_ppe_umol_per_j="
            f"{completed_yield:.15g}",
            "# modeled_completed_aperture_band_yield_umol_per_j="
            f"{completed_yield:.15g}",
        ),
    )
    result = text
    for old, new in replacements:
        if result.count(old) != 1:
            raise ValueError(
                "scalar emitter text no longer matches the explicit band wrapper "
                f"boundary: missing {old!r}."
            )
        result = result.replace(old, new)
    result = result.replace(
        "internal_par_ppf_umol_s=", "internal_band_photon_flux_umol_s="
    ).replace(
        "modeled_completed_aperture_par_ppf_umol_s=",
        "modeled_completed_aperture_band_photon_flux_umol_s=",
    )
    if "scalar PAR" in result or "internal_source_ppe_umol_per_j" in result:
        raise ValueError("band emitter text retained scalar-PAR source labels.")
    return result


def _positive_finite(name: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise ValueError(f"{name} must be finite and positive.")
    return float(value)
