"""Pure scalar-PAR Radiance generation for the calibrated Proposed fixture."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from fspm_optics.fixtures.proposed_cob.source import (
    COB_ANGULAR_DAT_FILENAME,
    COB_ANGULAR_MODIFIER_NAME,
    COB_FORWARD_CHARACTERIZATION_IDENTITY_SHA256,
    COB_COMPLETED_APERTURE_FWHM_DEG,
    COB_IES_SHA256,
    COB_LES_DIAMETER_M,
    COB_NORMALIZATION_POLICY,
    COB_SOURCE_MODE,
    NATIVE_SOURCE_MODE,
    native_proposed_spd_identity_sha256,
    resolve_proposed_source_authority,
)

from .module_profile import DEFAULT_SMD_MODULE_PROFILE, SmdModuleProfile
from .optical_stack import (
    ACCEPTED_FIXTURE_TRANSMISSION,
    APERTURE_SIDE_M,
    BEZEL_OUTER_SIDE_M,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    EMITTER_AREA_M2,
    EMITTER_SIDE_M,
    EMITTER_Z_M,
    INTERNAL_CAVITY_HEIGHT_M,
    INTERNAL_SOURCE_PPE_UMOL_PER_J,
    OUTWARD_APERTURE_NORMAL,
    PMMA_SIDE_M,
    PMMA_THICKNESS_M,
    PROPOSED_FIXTURE_OPTICAL_STACK_ID,
    PTFE_PHYSICAL_THICKNESS_M,
    downward_circle_radiance_text,
    downward_square_radiance_text,
    proposed_fixture_emitter_z_m,
    proposed_fixture_materials_radiance_text,
    proposed_fixture_stack_radiance_text,
)
from .photons import (
    SCALAR_PAR_CARRIER_BASIS,
    SCALAR_PAR_CONVERSION_POLICY,
    SCALAR_PAR_RGB_POLICY,
    photon_flux_to_lambertian_radiance,
    watts_to_photon_flux_umol_s,
)
from .positions import SmdLayout
from .power_schedule import ModulePowerSchedule
from .source_variants import (
    ANGULAR_CAL_FILENAME,
    ANGULAR_MODIFIER_NAME,
    CompletedApertureAngularCalibration,
    SmdSourceVariant,
    angular_modifier_radiance_text,
    get_smd_source_variant,
    source_variant_cal_text,
)


@dataclass(frozen=True, slots=True)
class SmdEmitterAssumptions:
    """Source selection with explicit opt-in for retired angular research data."""

    source_variant: str = "native"
    completed_aperture_angular_calibration: (
        CompletedApertureAngularCalibration | None
    ) = None
    allow_non_authoritative_backward_calibration: bool = False
    source_mode: str = NATIVE_SOURCE_MODE
    cob_data_root: Path | None = None

    def __post_init__(self) -> None:
        variant = get_smd_source_variant(self.source_variant)
        calibration = self.completed_aperture_angular_calibration
        if calibration is not None and calibration.variant_key != variant.key:
            raise ValueError("completed-aperture angular calibration variant mismatch.")
        if calibration is not None and not self.allow_non_authoritative_backward_calibration:
            raise ValueError(
                "backward flux-matrix angular calibration is non-authoritative "
                "research data and requires explicit diagnostic opt-in."
            )
        if (
            self.allow_non_authoritative_backward_calibration
            and calibration is None
        ):
            raise ValueError(
                "non-authoritative backward-calibration opt-in requires a "
                "completed research calibration."
            )
        if self.source_mode == COB_SOURCE_MODE:
            if (
                not variant.is_native
                or calibration is not None
                or self.allow_non_authoritative_backward_calibration
            ):
                raise ValueError(
                    "COB Mode is incompatible with SMD angular research variants."
                )
            # Authenticate at the selection boundary so missing or changed data
            # cannot silently fall back to native emission.
            resolve_proposed_source_authority(
                self.source_mode, data_root=self.cob_data_root
            )
        elif self.source_mode != NATIVE_SOURCE_MODE:
            raise ValueError(f"unsupported Proposed source mode: {self.source_mode!r}.")


@dataclass(frozen=True, slots=True)
class SmdEmitterMetadata:
    """Unambiguous pre-optics and completed-aperture source provenance."""

    schema_version: int
    module_profile_id: str
    module_count: int
    control_zone_count: int
    control_zone_ids: tuple[int, ...]
    watts_by_control_zone: tuple[float, ...]
    schedule_source: str
    min_watts: float
    max_watts: float
    total_watts: float
    internal_source_ppe_umol_per_j: float
    total_internal_par_ppf_umol_s: float
    accepted_fixture_transmission: float
    completed_aperture_fixture_ppe_umol_per_j: float
    modeled_completed_aperture_par_ppf_umol_s: float
    emitter_area_per_module_m2: float
    source_radiance_by_control_zone: tuple[float, ...]
    emitter_primitive_count: int
    optical_stack_polygon_count: int
    source_variant: str
    source_variant_cal_filename: str | None
    scalar_par_carrier_basis: str
    scalar_par_rgb_policy: str
    scalar_par_conversion_policy: str
    optical_stack_id: str
    emitter_z_offset_from_aperture_m: float
    completed_aperture_side_m: float
    completed_aperture_outward_normal: tuple[float, float, float]
    ptfe_physical_thickness_m_metadata: float
    measured_completed_aperture_fwhm_deg: float | None = None
    internal_radiance_exponent: float | None = None
    angular_modifier_gain: float | None = None
    angular_modifier_name: str | None = None
    angular_cal_sha256: str | None = None
    completed_aperture_characterization_identity_sha256: str | None = None
    source_mode: str = NATIVE_SOURCE_MODE
    source_classification: str = "native_proposed_smd"
    emitter_shape: str = "centered_square_emitting_window"
    emitter_diameter_m: float | None = None
    authenticated_ies_sha256: str | None = None
    normalized_angular_identity_sha256: str | None = None
    normalized_angular_dat_sha256: str | None = None
    angular_normalization_policy: str | None = None
    controlled_spd_identity_sha256: str | None = None


def smd_source_identity_payload(
    metadata: SmdEmitterMetadata,
) -> dict[str, object]:
    """Return the normalization-sensitive identity used by reusable artifacts."""

    return {
        "source_mode": metadata.source_mode,
        "classification": metadata.source_classification,
        "emitter_shape": metadata.emitter_shape,
        "emitter_area_per_module_m2": metadata.emitter_area_per_module_m2,
        "authenticated_ies_sha256": metadata.authenticated_ies_sha256,
        "normalized_angular_identity_sha256": (
            metadata.normalized_angular_identity_sha256
        ),
        "angular_normalization_policy": metadata.angular_normalization_policy,
        "completed_aperture_characterization_identity_sha256": (
            metadata.completed_aperture_characterization_identity_sha256
        ),
        "controlled_spd_identity_sha256": (
            metadata.controlled_spd_identity_sha256
        ),
        "completed_aperture_transmission": (
            metadata.accepted_fixture_transmission
        ),
        "completed_aperture_ppe_umol_per_j": (
            metadata.completed_aperture_fixture_ppe_umol_per_j
        ),
    }


def smd_source_identity_sha256(metadata: SmdEmitterMetadata) -> str:
    """Hash every source field that can affect normalized emitted transport."""

    return _hash_json(smd_source_identity_payload(metadata))


def current_proposed_source_identity_payload(
    source_mode: str = NATIVE_SOURCE_MODE,
    *,
    cob_data_root: Path | None = None,
) -> dict[str, object]:
    """Return the sole current production identity for one Proposed source mode."""

    authority = resolve_proposed_source_authority(
        source_mode, data_root=cob_data_root
    )
    law = authority.angular_law
    is_cob = authority.source_mode == COB_SOURCE_MODE
    return {
        "source_mode": authority.source_mode,
        "classification": authority.classification,
        "emitter_shape": authority.emitter_shape,
        "emitter_area_per_module_m2": authority.emitter_area_m2,
        "authenticated_ies_sha256": None if law is None else law.ies_sha256,
        "normalized_angular_identity_sha256": (
            None if law is None else law.normalized_identity_sha256
        ),
        "angular_normalization_policy": (
            COB_NORMALIZATION_POLICY if is_cob else None
        ),
        "completed_aperture_characterization_identity_sha256": (
            COB_FORWARD_CHARACTERIZATION_IDENTITY_SHA256 if is_cob else None
        ),
        "controlled_spd_identity_sha256": (
            native_proposed_spd_identity_sha256() if is_cob else None
        ),
        "completed_aperture_transmission": (
            authority.completed_aperture_transmission
        ),
        "completed_aperture_ppe_umol_per_j": (
            authority.completed_aperture_ppe_umol_per_j
        ),
    }


def current_proposed_source_identity_sha256(
    source_mode: str = NATIVE_SOURCE_MODE,
    *,
    cob_data_root: Path | None = None,
) -> str:
    """Hash the current forward-flux source authority for compatibility checks."""

    return _hash_json(
        current_proposed_source_identity_payload(
            source_mode, cob_data_root=cob_data_root
        )
    )


@dataclass(frozen=True, slots=True)
class SmdRadianceDocument:
    radiance_text: str
    metadata: SmdEmitterMetadata
    source_variant_cal_text: str | None
    source_angular_data_filename: str | None = None
    source_angular_data_text: str | None = None


def build_smd_radiance_document(
    layout: SmdLayout,
    power_schedule: ModulePowerSchedule,
    *,
    module_profile: SmdModuleProfile = DEFAULT_SMD_MODULE_PROFILE,
    assumptions: SmdEmitterAssumptions = SmdEmitterAssumptions(),
) -> SmdRadianceDocument:
    """Build the sole calibrated production source before Radiance transport."""

    if assumptions.source_mode == COB_SOURCE_MODE:
        authority = resolve_proposed_source_authority(
            assumptions.source_mode, data_root=assumptions.cob_data_root
        )
        return _build_native_document_for_internal_yield(
            layout,
            power_schedule,
            internal_photon_yield_umol_per_j=(
                authority.internal_source_ppe_umol_per_j
            ),
            completed_aperture_yield_umol_per_j=(
                authority.completed_aperture_ppe_umol_per_j
            ),
            completed_aperture_transmission=(
                authority.completed_aperture_transmission
            ),
            module_profile=module_profile,
            assumptions=assumptions,
        )
    calibration = assumptions.completed_aperture_angular_calibration
    internal_yield = (
        INTERNAL_SOURCE_PPE_UMOL_PER_J
        if calibration is None
        else calibration.internal_source_ppe_umol_per_j
    )
    transmission = (
        ACCEPTED_FIXTURE_TRANSMISSION
        if calibration is None
        else calibration.completed_aperture_transmission
    )
    return _build_native_document_for_internal_yield(
        layout,
        power_schedule,
        internal_photon_yield_umol_per_j=internal_yield,
        completed_aperture_yield_umol_per_j=COMPLETED_APERTURE_PPE_UMOL_PER_J,
        completed_aperture_transmission=transmission,
        module_profile=module_profile,
        assumptions=assumptions,
    )


def _build_native_document_for_internal_yield(
    layout: SmdLayout,
    power_schedule: ModulePowerSchedule,
    *,
    internal_photon_yield_umol_per_j: float,
    completed_aperture_yield_umol_per_j: float,
    completed_aperture_transmission: float = ACCEPTED_FIXTURE_TRANSMISSION,
    module_profile: SmdModuleProfile = DEFAULT_SMD_MODULE_PROFILE,
    assumptions: SmdEmitterAssumptions = SmdEmitterAssumptions(),
) -> SmdRadianceDocument:
    """Internal amplitude boundary shared by scalar and isolated-band writers."""

    if assumptions.source_mode == COB_SOURCE_MODE:
        return _build_cob_document_for_internal_yield(
            layout,
            power_schedule,
            internal_photon_yield_umol_per_j=internal_photon_yield_umol_per_j,
            completed_aperture_yield_umol_per_j=(
                completed_aperture_yield_umol_per_j
            ),
            completed_aperture_transmission=completed_aperture_transmission,
            module_profile=module_profile,
            assumptions=assumptions,
        )
    if assumptions.source_mode != NATIVE_SOURCE_MODE:
        raise ValueError(f"unsupported Proposed source mode: {assumptions.source_mode!r}.")

    _validate_layout_schedule(layout, power_schedule)
    _validate_authoritative_profile(module_profile)
    internal_yield = _positive(
        "internal_photon_yield_umol_per_j", internal_photon_yield_umol_per_j
    )
    completed_yield = _positive(
        "completed_aperture_yield_umol_per_j",
        completed_aperture_yield_umol_per_j,
    )
    transmission = _positive(
        "completed_aperture_transmission",
        completed_aperture_transmission,
    )
    if transmission > 1.0:
        raise ValueError("completed_aperture_transmission must not exceed one.")
    if not math.isclose(
        internal_yield * transmission,
        completed_yield,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ValueError(
            "completed-aperture yield must equal internal yield times the "
            "variant-specific fixture transmission."
        )
    variant = get_smd_source_variant(assumptions.source_variant)
    calibration = assumptions.completed_aperture_angular_calibration
    _require_completed_aperture_calibration(variant, calibration)
    cal_text = source_variant_cal_text(variant, calibration)
    modifier_text = angular_modifier_radiance_text(variant, calibration)
    emitting_modifier = (
        ANGULAR_MODIFIER_NAME if modifier_text is not None else "void"
    )

    zone_radiances = tuple(
        photon_flux_to_lambertian_radiance(watts * internal_yield, EMITTER_AREA_M2)
        for watts in power_schedule.watts_by_control_zone
    )
    sections: list[str] = [
        _header_text(
            layout,
            power_schedule,
            module_profile,
            internal_yield,
            completed_yield,
            transmission,
            variant,
            calibration,
        ),
    ]
    if modifier_text is not None:
        sections.append(modifier_text)
    sections.extend(
        (
            _zone_light_materials_text(
                zone_radiances,
                modifier=emitting_modifier,
            ),
            proposed_fixture_materials_radiance_text(),
        )
    )
    for module, watts in zip(
        layout.modules, power_schedule.watts_by_module, strict=True
    ):
        internal_ppf = watts_to_photon_flux_umol_s(watts, internal_yield)
        completed_ppf = watts_to_photon_flux_umol_s(watts, completed_yield)
        emitter_z = proposed_fixture_emitter_z_m(module.z_m)
        sections.append(
            "# module "
            f"{module.module_index:04d} control_zone={module.control_zone_index} "
            f"solver_watts={watts:.9f} internal_par_ppf_umol_s={internal_ppf:.9f} "
            f"modeled_completed_aperture_par_ppf_umol_s={completed_ppf:.9f}\n"
            + downward_square_radiance_text(
                f"smd_control_zone_{module.control_zone_index:03d}",
                f"smd_m{module.module_index:04d}_internal_emitter",
                module.x_m,
                module.y_m,
                emitter_z,
                EMITTER_SIDE_M,
            )
        )
        sections.append(
            proposed_fixture_stack_radiance_text(
                module.module_index,
                module.x_m,
                module.y_m,
                module.z_m,
            )
        )

    radiance_text = "\n".join(section.rstrip() for section in sections) + "\n"
    total_watts = power_schedule.total_watts
    metadata = SmdEmitterMetadata(
        schema_version=2,
        module_profile_id=module_profile.profile_id,
        module_count=len(layout.modules),
        control_zone_count=layout.control_zone_count,
        control_zone_ids=layout.control_zone_indices,
        watts_by_control_zone=power_schedule.watts_by_control_zone,
        schedule_source=power_schedule.schedule_source,
        min_watts=power_schedule.min_watts,
        max_watts=power_schedule.max_watts,
        total_watts=total_watts,
        internal_source_ppe_umol_per_j=internal_yield,
        total_internal_par_ppf_umol_s=total_watts * internal_yield,
        accepted_fixture_transmission=transmission,
        completed_aperture_fixture_ppe_umol_per_j=completed_yield,
        modeled_completed_aperture_par_ppf_umol_s=total_watts * completed_yield,
        emitter_area_per_module_m2=EMITTER_AREA_M2,
        source_radiance_by_control_zone=zone_radiances,
        emitter_primitive_count=len(layout.modules),
        optical_stack_polygon_count=14 * len(layout.modules),
        source_variant=variant.key,
        source_variant_cal_filename=(
            ANGULAR_CAL_FILENAME if cal_text is not None else None
        ),
        scalar_par_carrier_basis=SCALAR_PAR_CARRIER_BASIS,
        scalar_par_rgb_policy=SCALAR_PAR_RGB_POLICY,
        scalar_par_conversion_policy=SCALAR_PAR_CONVERSION_POLICY,
        optical_stack_id=PROPOSED_FIXTURE_OPTICAL_STACK_ID,
        emitter_z_offset_from_aperture_m=EMITTER_Z_M,
        completed_aperture_side_m=APERTURE_SIDE_M,
        completed_aperture_outward_normal=OUTWARD_APERTURE_NORMAL,
        ptfe_physical_thickness_m_metadata=PTFE_PHYSICAL_THICKNESS_M,
        measured_completed_aperture_fwhm_deg=(
            None
            if calibration is None
            else calibration.measured_completed_aperture_fwhm_deg
        ),
        internal_radiance_exponent=(
            None if calibration is None else calibration.internal_radiance_exponent
        ),
        angular_modifier_gain=(
            None if calibration is None else calibration.internal_modifier_gain
        ),
        angular_modifier_name=(
            ANGULAR_MODIFIER_NAME if cal_text is not None else None
        ),
        angular_cal_sha256=(
            None if cal_text is None else _sha256_text(cal_text)
        ),
        completed_aperture_characterization_identity_sha256=(
            None
            if calibration is None
            else calibration.characterization_identity_sha256
        ),
    )
    return SmdRadianceDocument(radiance_text, metadata, cal_text)


def _build_cob_document_for_internal_yield(
    layout: SmdLayout,
    power_schedule: ModulePowerSchedule,
    *,
    internal_photon_yield_umol_per_j: float,
    completed_aperture_yield_umol_per_j: float,
    completed_aperture_transmission: float,
    module_profile: SmdModuleProfile,
    assumptions: SmdEmitterAssumptions,
) -> SmdRadianceDocument:
    """Build the authenticated 22 mm COB LES inside the unchanged stack."""

    _validate_layout_schedule(layout, power_schedule)
    _validate_authoritative_profile(module_profile)
    authority = resolve_proposed_source_authority(
        assumptions.source_mode, data_root=assumptions.cob_data_root
    )
    law = authority.angular_law
    if law is None:  # pragma: no cover - resolver contract
        raise ValueError("COB source authority is missing its angular law.")
    internal_yield = _positive(
        "internal_photon_yield_umol_per_j", internal_photon_yield_umol_per_j
    )
    completed_yield = _positive(
        "completed_aperture_yield_umol_per_j",
        completed_aperture_yield_umol_per_j,
    )
    transmission = _positive(
        "completed_aperture_transmission", completed_aperture_transmission
    )
    if not math.isclose(
        internal_yield * transmission,
        completed_yield,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ValueError(
            "COB completed-aperture yield must equal its independently "
            "calibrated internal yield times COB stack transmission."
        )

    # The normalized DAT is unit radiant intensity (integral = 1). flatcorr
    # divides by projected cosine, so base radiance is flux / physical LES area.
    zone_radiances = tuple(
        watts * internal_yield / authority.emitter_area_m2
        for watts in power_schedule.watts_by_control_zone
    )
    zone_values = ",".join(
        f"{value:.9f}" for value in power_schedule.watts_by_control_zone
    )
    sections = [
        (
            "# fspm_optics controlled Proposed COB source-shape surrogate\n"
            "# not a Citizen product-efficacy model or hybrid COB/red-ring product\n"
            "# scalar PAR carrier: R=G=B photon radiance in umol s^-1 m^-2 sr^-1\n"
            "# scalar PAR conversion: direct photon transport; no photopic 179 lm/W conversion\n"
            "# source normalization occurs before transport: internal PPF = solver watts * internal source PPE\n"
            "# no receiver, PPFD-map, plant-absorption, or finite-plane reconciliation\n"
            f"# optical_stack_id={PROPOSED_FIXTURE_OPTICAL_STACK_ID}\n"
            f"# module_profile={module_profile.profile_id}\n"
            f"# source_mode={COB_SOURCE_MODE}\n"
            f"# authenticated_ies_sha256={COB_IES_SHA256}\n"
            f"# normalized_angular_identity_sha256={law.normalized_identity_sha256}\n"
            f"# les_shape=centered_circle les_diameter_m={COB_LES_DIAMETER_M:.15g}\n"
            f"# module_count={len(layout.modules)} control_zone_count={layout.control_zone_count}\n"
            f"# schedule_source={power_schedule.schedule_source}\n"
            f"# watts_by_control_zone={zone_values}\n"
            f"# cob_completed_aperture_transmission={transmission:.16g}\n"
            f"# internal_source_ppe_umol_per_j={internal_yield:.15g}\n"
            f"# completed_aperture_fixture_ppe_umol_per_j={completed_yield:.15g}\n"
            "# normalized_before_fixture_and_room_transport=true\n"
            "# post_scale_traced_ppfd=false\n"
        ),
        (
            f"void brightdata {COB_ANGULAR_MODIFIER_NAME}\n"
            f"5 flatcorr {COB_ANGULAR_DAT_FILENAME} source.cal src_phi4 src_theta\n"
            "0\n"
            "0\n"
        ),
        _zone_light_materials_text(
            zone_radiances, modifier=COB_ANGULAR_MODIFIER_NAME
        ),
        proposed_fixture_materials_radiance_text(),
    ]
    for module, watts in zip(
        layout.modules, power_schedule.watts_by_module, strict=True
    ):
        internal_ppf = watts_to_photon_flux_umol_s(watts, internal_yield)
        completed_ppf = watts_to_photon_flux_umol_s(watts, completed_yield)
        emitter_z = proposed_fixture_emitter_z_m(module.z_m)
        sections.append(
            "# module "
            f"{module.module_index:04d} control_zone={module.control_zone_index} "
            f"solver_watts={watts:.9f} internal_par_ppf_umol_s={internal_ppf:.9f} "
            f"modeled_completed_aperture_par_ppf_umol_s={completed_ppf:.9f}\n"
            + downward_circle_radiance_text(
                f"smd_control_zone_{module.control_zone_index:03d}",
                f"cob_m{module.module_index:04d}_internal_les",
                module.x_m,
                module.y_m,
                emitter_z,
                COB_LES_DIAMETER_M,
            )
        )
        sections.append(
            proposed_fixture_stack_radiance_text(
                module.module_index,
                module.x_m,
                module.y_m,
                module.z_m,
            )
        )
    radiance_text = "\n".join(section.rstrip() for section in sections) + "\n"
    total_watts = power_schedule.total_watts
    metadata = SmdEmitterMetadata(
        schema_version=3,
        module_profile_id=module_profile.profile_id,
        module_count=len(layout.modules),
        control_zone_count=layout.control_zone_count,
        control_zone_ids=layout.control_zone_indices,
        watts_by_control_zone=power_schedule.watts_by_control_zone,
        schedule_source=power_schedule.schedule_source,
        min_watts=power_schedule.min_watts,
        max_watts=power_schedule.max_watts,
        total_watts=total_watts,
        internal_source_ppe_umol_per_j=internal_yield,
        total_internal_par_ppf_umol_s=total_watts * internal_yield,
        accepted_fixture_transmission=transmission,
        completed_aperture_fixture_ppe_umol_per_j=completed_yield,
        modeled_completed_aperture_par_ppf_umol_s=total_watts * completed_yield,
        emitter_area_per_module_m2=authority.emitter_area_m2,
        source_radiance_by_control_zone=zone_radiances,
        emitter_primitive_count=len(layout.modules),
        optical_stack_polygon_count=14 * len(layout.modules),
        source_variant="not_applicable",
        source_variant_cal_filename=None,
        scalar_par_carrier_basis=SCALAR_PAR_CARRIER_BASIS,
        scalar_par_rgb_policy=SCALAR_PAR_RGB_POLICY,
        scalar_par_conversion_policy=SCALAR_PAR_CONVERSION_POLICY,
        optical_stack_id=PROPOSED_FIXTURE_OPTICAL_STACK_ID,
        emitter_z_offset_from_aperture_m=EMITTER_Z_M,
        completed_aperture_side_m=APERTURE_SIDE_M,
        completed_aperture_outward_normal=OUTWARD_APERTURE_NORMAL,
        ptfe_physical_thickness_m_metadata=PTFE_PHYSICAL_THICKNESS_M,
        measured_completed_aperture_fwhm_deg=COB_COMPLETED_APERTURE_FWHM_DEG,
        angular_modifier_name=COB_ANGULAR_MODIFIER_NAME,
        angular_cal_sha256=law.dat_sha256,
        source_mode=COB_SOURCE_MODE,
        source_classification=COB_SOURCE_MODE,
        emitter_shape="centered_circular_les",
        emitter_diameter_m=COB_LES_DIAMETER_M,
        authenticated_ies_sha256=COB_IES_SHA256,
        normalized_angular_identity_sha256=law.normalized_identity_sha256,
        normalized_angular_dat_sha256=law.dat_sha256,
        angular_normalization_policy=COB_NORMALIZATION_POLICY,
        completed_aperture_characterization_identity_sha256=(
            COB_FORWARD_CHARACTERIZATION_IDENTITY_SHA256
        ),
        controlled_spd_identity_sha256=(
            native_proposed_spd_identity_sha256()
        ),
    )
    return SmdRadianceDocument(
        radiance_text,
        metadata,
        None,
        COB_ANGULAR_DAT_FILENAME,
        law.dat_text,
    )


def smd_radiance_text(
    layout: SmdLayout,
    power_schedule: ModulePowerSchedule,
    *,
    module_profile: SmdModuleProfile = DEFAULT_SMD_MODULE_PROFILE,
    assumptions: SmdEmitterAssumptions = SmdEmitterAssumptions(),
) -> str:
    """Convenience wrapper returning only the pure Radiance source text."""

    return build_smd_radiance_document(
        layout,
        power_schedule,
        module_profile=module_profile,
        assumptions=assumptions,
    ).radiance_text


def _validate_layout_schedule(
    layout: SmdLayout, schedule: ModulePowerSchedule
) -> None:
    module_count = len(layout.modules)
    if schedule.module_count != module_count or len(schedule.watts_by_module) != module_count:
        raise ValueError("power schedule module count must match the SMD layout.")
    if (
        schedule.control_zone_count != layout.control_zone_count
        or len(schedule.watts_by_control_zone) != layout.control_zone_count
    ):
        raise ValueError("power schedule control-zone count must match the SMD layout.")
    for module, watts in zip(layout.modules, schedule.watts_by_module, strict=True):
        if not 0 <= module.control_zone_index < layout.control_zone_count:
            raise ValueError(
                f"module {module.module_index} has an invalid control-zone ID."
            )
        expected = schedule.watts_by_control_zone[module.control_zone_index]
        if watts != expected:
            raise ValueError(
                f"module {module.module_index} wattage does not match its control zone."
            )


def _validate_authoritative_profile(profile: SmdModuleProfile) -> None:
    if not profile.profile_id:
        raise ValueError("module profile ID must not be empty.")
    expected = {
        "window_side_m": EMITTER_SIDE_M,
        "lid_side_m": PMMA_SIDE_M,
        "lid_thickness_m": PMMA_THICKNESS_M,
        "stack_height_m": INTERNAL_CAVITY_HEIGHT_M,
        "bezel_pocket_m": BEZEL_OUTER_SIDE_M,
        "gasket_aperture_m": APERTURE_SIDE_M,
        "ptfe_liner_thickness_m": PTFE_PHYSICAL_THICKNESS_M,
    }
    for name, value in expected.items():
        if not math.isclose(float(getattr(profile, name)), value, rel_tol=0.0, abs_tol=1e-15):
            raise ValueError(
                f"module profile {name} must match the calibrated Proposed fixture authority."
            )


def _require_completed_aperture_calibration(
    variant: SmdSourceVariant,
    calibration: CompletedApertureAngularCalibration | None,
) -> None:
    if variant.is_native:
        if calibration is not None and calibration.variant_key != variant.key:
            raise ValueError("native completed-aperture calibration mismatch.")
        return
    if calibration is None or calibration.variant_key != variant.key:
        raise ValueError(
            f"source variant {variant.key!r} is research-only and has no "
            "completed-aperture calibration; it cannot claim 2.6 umol/J."
        )


def _header_text(
    layout: SmdLayout,
    schedule: ModulePowerSchedule,
    profile: SmdModuleProfile,
    internal_yield: float,
    completed_yield: float,
    transmission: float,
    variant: SmdSourceVariant,
    calibration: CompletedApertureAngularCalibration | None,
) -> str:
    zone_values = ",".join(
        f"{value:.9f}" for value in schedule.watts_by_control_zone
    )
    modifier_active = calibration is not None and not variant.is_native
    measured_fwhm = (
        ""
        if calibration is None
        else format(calibration.measured_completed_aperture_fwhm_deg, ".15g")
    )
    return (
        "# fspm_optics calibrated Proposed LED fixture scene\n"
        "# scalar PAR carrier: R=G=B photon radiance in umol s^-1 m^-2 sr^-1\n"
        "# scalar PAR conversion: direct photon transport; no photopic 179 lm/W conversion\n"
        "# source normalization occurs before transport: internal PPF = solver watts * internal source PPE\n"
        "# no receiver, PPFD-map, plant-absorption, or finite-plane reconciliation\n"
        f"# optical_stack_id={PROPOSED_FIXTURE_OPTICAL_STACK_ID}\n"
        f"# module_profile={profile.profile_id}\n"
        f"# source_variant={variant.key}\n"
        f"# module_count={len(layout.modules)} control_zone_count={layout.control_zone_count}\n"
        f"# schedule_source={schedule.schedule_source}\n"
        f"# watts_by_control_zone={zone_values}\n"
        f"# variant_specific_completed_aperture_transmission={transmission:.16g}\n"
        f"# internal_source_ppe_umol_per_j={internal_yield:.15g}\n"
        f"# completed_aperture_fixture_ppe_umol_per_j={completed_yield:.15g}\n"
        f"# angular_modifier_active={modifier_active}\n"
        f"# measured_completed_aperture_fwhm_deg={measured_fwhm}\n"
    )


def _zone_light_materials_text(
    zone_radiances: tuple[float, ...],
    *,
    modifier: str = "void",
) -> str:
    sections = []
    for zone_index, radiance in enumerate(zone_radiances):
        sections.append(
            f"{modifier} light smd_control_zone_{zone_index:03d}\n"
            "0\n"
            "0\n"
            f"3 {radiance:.9f} {radiance:.9f} {radiance:.9f}"
        )
    return "\n\n".join(sections) + "\n"


def _positive(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be finite and positive.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive.")
    return number


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_json(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
