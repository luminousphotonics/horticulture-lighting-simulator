"""Deterministic dry-run planning for five isolated Rex receiver bands.

Planning materializes reproducible scene inputs and command specifications but
never locates or executes Radiance.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Final, Mapping, Sequence, cast

from fspm_optics.fixtures.proposed_cob.source import (
    COB_SOURCE_MODE,
    NATIVE_SOURCE_MODE,
    ProposedSourceAuthority,
    resolve_proposed_source_authority,
)
from fspm_optics.fixtures.conventional_led.resources import (
    CONVENTIONAL_SPD_RESOURCE_NAME,
    CONVENTIONAL_SPD_SHA256,
)
from fspm_optics.fixtures.conventional_led.spectral import (
    CONVENTIONAL_SPD_NORMALIZATION_POLICY,
)
from fspm_optics.fixtures.smd.band_writer import (
    COMBINED_MODULE_EMITTING_WINDOW,
    ISOLATED_BAND_PHOTON_CARRIER_BASIS,
    ISOLATED_BAND_RGB_POLICY,
    NATIVE_LAMBERTIAN_ANGULAR_MODEL,
    SmdBandEmitterMetadata,
    build_smd_band_radiance_document,
)
from fspm_optics.fixtures.smd.module_profile import DEFAULT_SMD_MODULE_PROFILE
from fspm_optics.fixtures.smd.optical_stack import (
    ACCEPTED_FIXTURE_TRANSMISSION,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    INTERNAL_SOURCE_PPE_UMOL_PER_J,
    PROPOSED_FIXTURE_OPTICAL_STACK_ID,
)
from fspm_optics.fixtures.smd.photons import photon_flux_to_lambertian_radiance
from fspm_optics.fixtures.smd.positions import SmdLayout
from fspm_optics.fixtures.smd.power_schedule import ModulePowerSchedule
from fspm_optics.optics.rex_material_plan import (
    DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID,
    REX_RADIANCE_MATERIAL_PLAN_CLAIM,
    REX_TRANS_MODEL_ASSUMPTIONS,
    RadianceTransParameters,
    RexRadianceTransIntervalPlan,
    RexRadianceTransMaterialPlan,
    build_rex_radiance_trans_material_plan,
    format_rex_radiance_trans_material_plan_json,
    map_atr_to_diffuse_trans,
)
from fspm_optics.optics.rex_weighting import AtrCoefficients
from fspm_optics.optics.rex_weighting import build_rex_source_weighted_atr_payload
from fspm_optics.plants.generator import generate_rex_butterhead_plant
from fspm_optics.plants.radiance_export import export_plant_mesh_to_radiance
from fspm_optics.plants.rex import RexPlantConfig
from fspm_optics.radiance.commands import (
    CommandSpec,
    StdoutMode,
    build_oconv_command,
    build_plant_receiver_rtrace_command,
)
from fspm_optics.geometry.room import (
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    RoomDimensions,
    production_room_model_payload,
    room_radiance_text,
)
from fspm_optics.radiance.options import replace_radiance_option_value
from fspm_optics.receivers.samples import (
    MeshPatchReceiverSample,
    build_two_sided_patch_receivers,
    receiver_sample_input_text,
)
from fspm_optics.sources.smd.profile import (
    NATIVE_PROPOSED_SPECTRAL_BASIS_ID,
    SMD_NORMALIZATION_POLICY,
    SMD_SOURCE_MODEL_ID,
    SmdSourceModel,
    build_nominal_smd_source_model,
    format_smd_source_model_json,
)
from fspm_optics.sources.smd.spectral_control import (
    CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_ID,
    PROPOSED_CONVENTIONAL_LED_CONTROL_SOURCE_MODEL_ID,
)
from fspm_optics.spectral.bands import FIXED_TRANSPORT_BANDS, SpectralBand
from fspm_optics.transport.basis.artifacts import load_basis_workspace_artifacts
from fspm_optics.transport.basis.atomic import commit_staged, stage_bytes
from fspm_optics.transport.basis.composite import plan_composite_validation
from fspm_optics.transport.basis.source_compatibility import (
    require_current_proposed_source_manifest,
)

FIVE_BAND_ORDER: Final[tuple[str, ...]] = tuple(
    band.band_id for band in FIXED_TRANSPORT_BANDS
)
FIVE_BAND_PLAN_SCHEMA_VERSION: Final = 3
FIVE_BAND_PLAN_PAYLOAD_TYPE: Final = "fspm_optics_rex_five_band_transport_plan"
FIVE_BAND_TRANSPORT_BACKEND: Final = "repeated_isolated_scalar_rtrace_dry_run"
FIVE_BAND_SCIENTIFIC_CLAIM: Final = (
    "Band-resolved SMD source composition and Rex leaf optics within a "
    "wavelength-neutral room and fixture model."
)
SCALAR_CALIBRATION_POLICY_ID: Final = (
    "calibrated_internal_3p209706636558342_partitioned_by_relative_spectrum_v1"
)
EXPECTED_PAR_BAND_RESULT_UNITS: Final = "band_photon_flux_density_umol_m2_s"
EXPECTED_FAR_RED_RESULT_UNITS: Final = "far_red_photon_flux_density_umol_m2_s"
PHYSICAL_PATCH_AREA_POLICY: Final = (
    "one_physical_patch_area_multiplies_front_plus_back_incident_flux_once"
)
RECEIVER_HEMISPHERE_POLICY: Final = (
    "front_and_back_receivers_represent_distinct_incident_hemispheres"
)
FLOAT_ABS_TOLERANCE: Final = 1e-12

FIVE_BAND_LIMITATIONS: Final[tuple[str, ...]] = (
    "Combined module-window geometry; no package-level spatial spectrum.",
    "All SMD spectral components use one common Lambertian angular model.",
    "Room, PTFE, and PMMA optical behavior is wavelength-neutral.",
    "Rex leaf optics are diffuse and symmetric front-to-back.",
    "Source spectral shape is fixed versus drive current and temperature.",
    "Rex coefficients are fixed source-weighted averages within each band.",
    "Within-band spectral reshaping after repeated interactions is not modeled.",
    "Rex optical data do not cover source wavelengths from 400 through 403 nm.",
    "Far-red is outside PAR and must not be reported as PPFD.",
)


class FiveBandPlanError(RuntimeError):
    """A five-band dry-run plan failed an input or compatibility contract."""


class FiveBandWorkspaceConflictError(FiveBandPlanError):
    """An existing five-band artifact differs from deterministic planned text."""


@dataclass(frozen=True, slots=True)
class ComponentBandContribution:
    component_id: str
    nominal_par_ppf_umol_s: float
    fraction_of_component_par: float
    nominal_band_ppf_umol_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.component_id, str) or not self.component_id:
            raise ValueError("component_id must be a non-empty string.")
        nominal = _positive("nominal_par_ppf_umol_s", self.nominal_par_ppf_umol_s)
        fraction = _fraction(
            "fraction_of_component_par", self.fraction_of_component_par
        )
        band_ppf = _non_negative(
            "nominal_band_ppf_umol_s", self.nominal_band_ppf_umol_s
        )
        if not math.isclose(
            band_ppf,
            nominal * fraction,
            rel_tol=1e-12,
            abs_tol=FLOAT_ABS_TOLERANCE,
        ):
            raise ValueError("component nominal band PPF does not match its fraction.")
        object.__setattr__(self, "nominal_par_ppf_umol_s", nominal)
        object.__setattr__(self, "fraction_of_component_par", fraction)
        object.__setattr__(self, "nominal_band_ppf_umol_s", band_ppf)

    def to_dict(self) -> dict[str, str | float]:
        return {
            "component_id": self.component_id,
            "nominal_par_ppf_umol_s": self.nominal_par_ppf_umol_s,
            "fraction_of_component_par": self.fraction_of_component_par,
            "nominal_band_ppf_umol_s": self.nominal_band_ppf_umol_s,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ComponentBandContribution":
        return cls(
            component_id=_string(payload, "component_id"),
            nominal_par_ppf_umol_s=_number(payload, "nominal_par_ppf_umol_s"),
            fraction_of_component_par=_number(
                payload, "fraction_of_component_par"
            ),
            nominal_band_ppf_umol_s=_number(payload, "nominal_band_ppf_umol_s"),
        )


@dataclass(frozen=True, slots=True)
class BandZoneSourceAmplitude:
    control_zone_index: int
    module_count: int
    module_wattage: float
    module_band_ppf_umol_s: float
    grayscale_radiance_rgb: tuple[float, float, float]

    def __post_init__(self) -> None:
        _non_negative_integer("control_zone_index", self.control_zone_index)
        _positive_integer("module_count", self.module_count)
        watts = _non_negative("module_wattage", self.module_wattage)
        ppf = _non_negative("module_band_ppf_umol_s", self.module_band_ppf_umol_s)
        rgb = tuple(_non_negative("grayscale_radiance_rgb", value) for value in self.grayscale_radiance_rgb)
        if len(rgb) != 3 or not rgb[0] == rgb[1] == rgb[2]:
            raise ValueError("band source Radiance amplitude must have exact R=G=B.")
        object.__setattr__(self, "module_wattage", watts)
        object.__setattr__(self, "module_band_ppf_umol_s", ppf)
        object.__setattr__(self, "grayscale_radiance_rgb", rgb)

    def to_dict(self) -> dict[str, Any]:
        return {
            "control_zone_index": self.control_zone_index,
            "module_count": self.module_count,
            "module_wattage": self.module_wattage,
            "module_band_ppf_umol_s": self.module_band_ppf_umol_s,
            "grayscale_radiance_rgb": list(self.grayscale_radiance_rgb),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BandZoneSourceAmplitude":
        rgb = _number_sequence(payload, "grayscale_radiance_rgb", expected=3)
        return cls(
            control_zone_index=_integer(payload, "control_zone_index"),
            module_count=_integer(payload, "module_count"),
            module_wattage=_number(payload, "module_wattage"),
            module_band_ppf_umol_s=_number(payload, "module_band_ppf_umol_s"),
            grayscale_radiance_rgb=(rgb[0], rgb[1], rgb[2]),
        )


@dataclass(frozen=True, slots=True)
class IsolatedTransportEmitterGroup:
    """Source-neutral group of equal-amplitude emitters for one isolated run."""

    group_id: str
    emitter_count: int
    photon_flux_per_emitter_umol_s: float
    total_photon_flux_umol_s: float

    def __post_init__(self) -> None:
        if not self.group_id:
            raise ValueError("isolated emitter group_id must be non-empty.")
        count = _positive_integer("emitter_count", self.emitter_count)
        per_emitter = _non_negative(
            "photon_flux_per_emitter_umol_s",
            self.photon_flux_per_emitter_umol_s,
        )
        total = _non_negative("total_photon_flux_umol_s", self.total_photon_flux_umol_s)
        if not math.isclose(
            total,
            count * per_emitter,
            rel_tol=1e-12,
            abs_tol=FLOAT_ABS_TOLERANCE,
        ):
            raise ValueError("isolated emitter-group photon flux does not close.")
        object.__setattr__(self, "photon_flux_per_emitter_umol_s", per_emitter)
        object.__setattr__(self, "total_photon_flux_umol_s", total)

    def to_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "emitter_count": self.emitter_count,
            "photon_flux_per_emitter_umol_s": self.photon_flux_per_emitter_umol_s,
            "total_photon_flux_umol_s": self.total_photon_flux_umol_s,
        }


@dataclass(frozen=True, slots=True)
class SourceNeutralIsolatedTransportInput:
    """Narrow common boundary for SMD and Conventional isolated scalar runs."""

    interval_id: str
    label: str
    start_nm: int
    end_nm: int
    source_family: str
    source_model_id: str
    source_payload_id: str
    normalization_policy: str
    emitter_groups: tuple[IsolatedTransportEmitterGroup, ...]
    total_photon_flux_umol_s: float
    angular_model_id: str
    shared_angular_data_identity: str
    rgb_channel_policy: str = "equal_grayscale_scalar_carrier"
    absolute_photon_flux_applied_before_trace: bool = True
    spectral_fraction_applied_after_trace: bool = False

    def __post_init__(self) -> None:
        for name in (
            "interval_id",
            "label",
            "source_family",
            "source_model_id",
            "source_payload_id",
            "normalization_policy",
            "angular_model_id",
            "shared_angular_data_identity",
        ):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty.")
        if self.interval_id == "scalar_par":
            if (self.start_nm, self.end_nm) != (400, 699):
                raise ValueError("scalar PAR isolated input must span 400–699 nm.")
        else:
            band = _fixed_band(self.interval_id)
            if (self.label, self.start_nm, self.end_nm) != (
                band.label,
                band.start_nm,
                band.end_nm,
            ):
                raise ValueError("isolated band metadata does not match fixed bands.")
        if not self.emitter_groups or len(
            {item.group_id for item in self.emitter_groups}
        ) != len(self.emitter_groups):
            raise ValueError("isolated emitter groups must be non-empty and unique.")
        total = _non_negative("total_photon_flux_umol_s", self.total_photon_flux_umol_s)
        if not math.isclose(
            total,
            math.fsum(item.total_photon_flux_umol_s for item in self.emitter_groups),
            rel_tol=1e-12,
            abs_tol=FLOAT_ABS_TOLERANCE,
        ):
            raise ValueError("isolated source total photon flux does not close.")
        if self.rgb_channel_policy != "equal_grayscale_scalar_carrier":
            raise ValueError("isolated transport must use equal grayscale RGB carriers.")
        if self.absolute_photon_flux_applied_before_trace is not True:
            raise ValueError("absolute isolated photon flux must be applied before trace.")
        if self.spectral_fraction_applied_after_trace is not False:
            raise ValueError("post-trace spectral fraction application is prohibited.")
        object.__setattr__(self, "total_photon_flux_umol_s", total)

    @property
    def source_input_id(self) -> str:
        digest = hashlib.sha256(
            json.dumps(
                self.to_payload(), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        return f"source-neutral-isolated-input-v1-{digest}"

    def to_payload(self) -> dict[str, object]:
        return {
            "interval_id": self.interval_id,
            "label": self.label,
            "wavelength_interval": {
                "start_nm": self.start_nm,
                "end_nm": self.end_nm,
            },
            "source_family": self.source_family,
            "source_model_id": self.source_model_id,
            "source_payload_id": self.source_payload_id,
            "normalization_policy": self.normalization_policy,
            "emitter_groups": [item.to_dict() for item in self.emitter_groups],
            "total_photon_flux_umol_s": self.total_photon_flux_umol_s,
            "angular_model_id": self.angular_model_id,
            "shared_angular_data_identity": self.shared_angular_data_identity,
            "rgb_channel_policy": self.rgb_channel_policy,
            "absolute_photon_flux_applied_before_trace": (
                self.absolute_photon_flux_applied_before_trace
            ),
            "spectral_fraction_applied_after_trace": (
                self.spectral_fraction_applied_after_trace
            ),
        }


@dataclass(frozen=True, slots=True)
class SmdBandSourcePlan:
    band_id: str
    label: str
    start_nm: int
    end_nm: int
    total_nominal_par_ppf_umol_s: float
    nominal_band_ppf_umol_s: float
    fraction_relative_to_par: float
    component_contributions: tuple[ComponentBandContribution, ...]
    internal_source_ppe_umol_per_j: float
    accepted_fixture_transmission: float
    completed_aperture_fixture_ppe_umol_per_j: float
    raw_relative_mix_aggregate_ppe_umol_per_j: float
    pretransport_relative_mix_normalization_factor: float
    internal_band_photon_yield_umol_per_j: float
    emitter_area_m2: float
    zone_amplitudes: tuple[BandZoneSourceAmplitude, ...]
    source_model_id: str
    normalization_policy: str
    resource_hashes: tuple[tuple[str, str], ...]
    spectral_basis_id: str = NATIVE_PROPOSED_SPECTRAL_BASIS_ID
    spatial_model_id: str = COMBINED_MODULE_EMITTING_WINDOW
    angular_model_id: str = NATIVE_LAMBERTIAN_ANGULAR_MODEL
    photon_carrier_basis: str = ISOLATED_BAND_PHOTON_CARRIER_BASIS
    rgb_policy: str = ISOLATED_BAND_RGB_POLICY
    source_mode: str = NATIVE_SOURCE_MODE

    def __post_init__(self) -> None:
        band = _fixed_band(self.band_id)
        if (self.label, self.start_nm, self.end_nm) != (
            band.label,
            band.start_nm,
            band.end_nm,
        ):
            raise ValueError(f"band metadata does not match fixed {self.band_id!r} band.")
        total = _positive(
            "total_nominal_par_ppf_umol_s", self.total_nominal_par_ppf_umol_s
        )
        band_ppf = _positive("nominal_band_ppf_umol_s", self.nominal_band_ppf_umol_s)
        fraction = _positive_fraction(
            "fraction_relative_to_par", self.fraction_relative_to_par
        )
        if not math.isclose(band_ppf / total, fraction, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("nominal band PPF and PAR fraction are inconsistent.")
        if not self.component_contributions or len(
            {item.component_id for item in self.component_contributions}
        ) != len(self.component_contributions):
            raise ValueError("component contributions must be non-empty and unique.")
        if not math.isclose(
            math.fsum(item.nominal_band_ppf_umol_s for item in self.component_contributions),
            band_ppf,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("component contributions do not sum to nominal band PPF.")
        anchor = _positive(
            "internal_source_ppe_umol_per_j",
            self.internal_source_ppe_umol_per_j,
        )
        transmission = _positive(
            "accepted_fixture_transmission", self.accepted_fixture_transmission
        )
        completed_ppe = _positive(
            "completed_aperture_fixture_ppe_umol_per_j",
            self.completed_aperture_fixture_ppe_umol_per_j,
        )
        if not math.isclose(
            completed_ppe,
            COMPLETED_APERTURE_PPE_UMOL_PER_J,
            rel_tol=0.0,
            abs_tol=FLOAT_ABS_TOLERANCE,
        ):
            raise ValueError("five-band fixture calibration provenance is inconsistent.")
        if not math.isclose(
            anchor * transmission,
            completed_ppe,
            rel_tol=1e-12,
            abs_tol=FLOAT_ABS_TOLERANCE,
        ):
            raise ValueError("five-band source transmission and PPE do not close.")
        aggregate = _positive(
            "raw_relative_mix_aggregate_ppe_umol_per_j",
            self.raw_relative_mix_aggregate_ppe_umol_per_j,
        )
        normalization = _positive(
            "pretransport_relative_mix_normalization_factor",
            self.pretransport_relative_mix_normalization_factor,
        )
        if not math.isclose(
            normalization,
            anchor / aggregate,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("pre-transport relative-mix normalization is inconsistent.")
        internal_band_yield = _positive(
            "internal_band_photon_yield_umol_per_j",
            self.internal_band_photon_yield_umol_per_j,
        )
        if not math.isclose(
            internal_band_yield,
            anchor * fraction,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("internal band photon yield is inconsistent.")
        area = _positive("emitter_area_m2", self.emitter_area_m2)
        if not self.zone_amplitudes:
            raise ValueError("band source plan requires control-zone amplitudes.")
        if tuple(item.control_zone_index for item in self.zone_amplitudes) != tuple(
            range(len(self.zone_amplitudes))
        ):
            raise ValueError("band control-zone amplitudes must be complete and ordered.")
        for item in self.zone_amplitudes:
            expected_ppf = item.module_wattage * internal_band_yield
            expected_radiance = (
                expected_ppf / area
                if self.source_mode == COB_SOURCE_MODE
                else photon_flux_to_lambertian_radiance(expected_ppf, area)
            )
            if not math.isclose(
                item.module_band_ppf_umol_s,
                expected_ppf,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ) or not math.isclose(
                item.grayscale_radiance_rgb[0],
                expected_radiance,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise ValueError("control-zone band source amplitude is inconsistent.")
        expected_source = {
            NATIVE_PROPOSED_SPECTRAL_BASIS_ID: SMD_SOURCE_MODEL_ID,
            CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_ID: (
                PROPOSED_CONVENTIONAL_LED_CONTROL_SOURCE_MODEL_ID
            ),
        }.get(self.spectral_basis_id)
        if self.source_model_id != expected_source:
            raise ValueError("unexpected Proposed spectral source_model_id.")
        expected_normalization = {
            NATIVE_PROPOSED_SPECTRAL_BASIS_ID: SMD_NORMALIZATION_POLICY,
            CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_ID: (
                CONVENTIONAL_SPD_NORMALIZATION_POLICY
            ),
        }.get(self.spectral_basis_id)
        if self.normalization_policy != expected_normalization:
            raise ValueError("unexpected Proposed normalization policy.")
        _validate_hash_pairs(self.resource_hashes)
        if (
            self.spectral_basis_id
            == CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_ID
            and self.resource_hashes
            != ((CONVENTIONAL_SPD_RESOURCE_NAME, CONVENTIONAL_SPD_SHA256),)
        ):
            raise ValueError(
                "controlled Proposed source requires the exact approved Conventional LED SPD."
            )
        expected_source_geometry = {
            NATIVE_SOURCE_MODE: (
                COMBINED_MODULE_EMITTING_WINDOW,
                NATIVE_LAMBERTIAN_ANGULAR_MODEL,
            ),
            COB_SOURCE_MODE: (
                "centered_22mm_circular_les",
                "citizen_clu04q_1812e1_3000k_authenticated_ies",
            ),
        }.get(self.source_mode)
        if expected_source_geometry is None or (
            self.spatial_model_id,
            self.angular_model_id,
        ) != expected_source_geometry:
            raise ValueError("five-band Proposed source geometry is inconsistent.")
        if self.photon_carrier_basis != ISOLATED_BAND_PHOTON_CARRIER_BASIS:
            raise ValueError("unexpected isolated-band photon carrier basis.")
        if self.rgb_policy != ISOLATED_BAND_RGB_POLICY:
            raise ValueError("isolated-band sources must preserve exact R=G=B.")
        object.__setattr__(self, "total_nominal_par_ppf_umol_s", total)
        object.__setattr__(self, "nominal_band_ppf_umol_s", band_ppf)
        object.__setattr__(self, "fraction_relative_to_par", fraction)
        object.__setattr__(self, "internal_source_ppe_umol_per_j", anchor)
        object.__setattr__(self, "accepted_fixture_transmission", transmission)
        object.__setattr__(
            self, "completed_aperture_fixture_ppe_umol_per_j", completed_ppe
        )
        object.__setattr__(
            self, "raw_relative_mix_aggregate_ppe_umol_per_j", aggregate
        )
        object.__setattr__(
            self, "pretransport_relative_mix_normalization_factor", normalization
        )
        object.__setattr__(
            self, "internal_band_photon_yield_umol_per_j", internal_band_yield
        )
        object.__setattr__(self, "emitter_area_m2", area)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "band_id": self.band_id,
            "label": self.label,
            "wavelength_interval": {
                "start_nm": self.start_nm,
                "end_nm": self.end_nm,
                "interval": "inclusive_integer_bin",
            },
            "total_nominal_par_ppf_umol_s": self.total_nominal_par_ppf_umol_s,
            "nominal_band_ppf_umol_s": self.nominal_band_ppf_umol_s,
            "fraction_relative_to_par": self.fraction_relative_to_par,
            "component_contributions": [item.to_dict() for item in self.component_contributions],
            "internal_source_ppe_umol_per_j": self.internal_source_ppe_umol_per_j,
            "accepted_fixture_transmission": self.accepted_fixture_transmission,
            "completed_aperture_fixture_ppe_umol_per_j": (
                self.completed_aperture_fixture_ppe_umol_per_j
            ),
            "raw_relative_mix_aggregate_ppe_umol_per_j": (
                self.raw_relative_mix_aggregate_ppe_umol_per_j
            ),
            "pretransport_relative_mix_normalization_factor": (
                self.pretransport_relative_mix_normalization_factor
            ),
            "internal_band_photon_yield_umol_per_j": (
                self.internal_band_photon_yield_umol_per_j
            ),
            "emitter_area_m2": self.emitter_area_m2,
            "zone_amplitudes": [item.to_dict() for item in self.zone_amplitudes],
            "source_model_id": self.source_model_id,
            "normalization_policy": self.normalization_policy,
            "resource_hashes": dict(self.resource_hashes),
            "spatial_model_id": self.spatial_model_id,
            "angular_model_id": self.angular_model_id,
            "photon_carrier_basis": self.photon_carrier_basis,
            "rgb_policy": self.rgb_policy,
        }
        if self.source_mode != NATIVE_SOURCE_MODE:
            payload["source_mode"] = self.source_mode
        if self.spectral_basis_id != NATIVE_PROPOSED_SPECTRAL_BASIS_ID:
            payload["spectral_basis_id"] = self.spectral_basis_id
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SmdBandSourcePlan":
        interval = _mapping(payload, "wavelength_interval")
        return cls(
            band_id=_string(payload, "band_id"),
            label=_string(payload, "label"),
            start_nm=_integer(interval, "start_nm"),
            end_nm=_integer(interval, "end_nm"),
            total_nominal_par_ppf_umol_s=_number(
                payload, "total_nominal_par_ppf_umol_s"
            ),
            nominal_band_ppf_umol_s=_number(payload, "nominal_band_ppf_umol_s"),
            fraction_relative_to_par=_number(payload, "fraction_relative_to_par"),
            component_contributions=tuple(
                ComponentBandContribution.from_dict(item)
                for item in _mapping_sequence(payload, "component_contributions")
            ),
            internal_source_ppe_umol_per_j=_number(
                payload, "internal_source_ppe_umol_per_j"
            ),
            accepted_fixture_transmission=_number(
                payload, "accepted_fixture_transmission"
            ),
            completed_aperture_fixture_ppe_umol_per_j=_number(
                payload, "completed_aperture_fixture_ppe_umol_per_j"
            ),
            raw_relative_mix_aggregate_ppe_umol_per_j=_number(
                payload, "raw_relative_mix_aggregate_ppe_umol_per_j"
            ),
            pretransport_relative_mix_normalization_factor=_number(
                payload, "pretransport_relative_mix_normalization_factor"
            ),
            internal_band_photon_yield_umol_per_j=_number(
                payload, "internal_band_photon_yield_umol_per_j"
            ),
            emitter_area_m2=_number(payload, "emitter_area_m2"),
            zone_amplitudes=tuple(
                BandZoneSourceAmplitude.from_dict(item)
                for item in _mapping_sequence(payload, "zone_amplitudes")
            ),
            source_model_id=_string(payload, "source_model_id"),
            normalization_policy=_string(payload, "normalization_policy"),
            resource_hashes=_hash_pairs(_mapping(payload, "resource_hashes")),
            spectral_basis_id=(
                NATIVE_PROPOSED_SPECTRAL_BASIS_ID
                if "spectral_basis_id" not in payload
                else _string(payload, "spectral_basis_id")
            ),
            spatial_model_id=_string(payload, "spatial_model_id"),
            angular_model_id=_string(payload, "angular_model_id"),
            photon_carrier_basis=_string(payload, "photon_carrier_basis"),
            rgb_policy=_string(payload, "rgb_policy"),
            source_mode=str(payload.get("source_mode", NATIVE_SOURCE_MODE)),
        )


def adapt_smd_band_source_plan(
    plan: SmdBandSourcePlan,
) -> SourceNeutralIsolatedTransportInput:
    """Expose an existing SMD band through the source-neutral planning boundary."""

    payload_id = "smd-band-source-plan-v1-" + hashlib.sha256(
        json.dumps(plan.to_dict(), sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    groups = tuple(
        IsolatedTransportEmitterGroup(
            group_id=f"smd_control_zone_{item.control_zone_index:03d}",
            emitter_count=item.module_count,
            photon_flux_per_emitter_umol_s=item.module_band_ppf_umol_s,
            total_photon_flux_umol_s=(
                item.module_count * item.module_band_ppf_umol_s
            ),
        )
        for item in plan.zone_amplitudes
    )
    return SourceNeutralIsolatedTransportInput(
        interval_id=plan.band_id,
        label=plan.label,
        start_nm=plan.start_nm,
        end_nm=plan.end_nm,
        source_family="smd",
        source_model_id=plan.source_model_id,
        source_payload_id=payload_id,
        normalization_policy=plan.normalization_policy,
        emitter_groups=groups,
        total_photon_flux_umol_s=math.fsum(
            item.total_photon_flux_umol_s for item in groups
        ),
        angular_model_id=plan.angular_model_id,
        shared_angular_data_identity=plan.angular_model_id,
    )


@dataclass(frozen=True, slots=True)
class BandMaterialBinding:
    band_id: str
    material_identifier: str
    coefficients: AtrCoefficients
    parameters: RadianceTransParameters
    effective_start_nm: int
    effective_end_nm_exclusive: int
    policy_id: str
    radiance_text_sha256: str

    def __post_init__(self) -> None:
        _fixed_band(self.band_id)
        expected_identifier = f"rex_leaf_trans_{self.band_id}"
        if self.material_identifier != expected_identifier:
            raise ValueError(
                f"{self.band_id} material must be {expected_identifier!r}."
            )
        if self.parameters != map_atr_to_diffuse_trans(self.coefficients):
            raise ValueError("band material parameters do not match original A/T/R.")
        if self.policy_id != DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID:
            raise ValueError("unexpected Rex material policy.")
        if not 0 < self.effective_start_nm < self.effective_end_nm_exclusive:
            raise ValueError("band material effective wavelength interval is invalid.")
        _sha256("radiance_text_sha256", self.radiance_text_sha256)

    def to_dict(self) -> dict[str, Any]:
        return {
            "band_id": self.band_id,
            "material_identifier": self.material_identifier,
            "original_atr": self.coefficients.to_dict(),
            "radiance_arguments": self.parameters.to_dict(),
            "effective_wavelength_range": {
                "start_nm": self.effective_start_nm,
                "end_nm_exclusive": self.effective_end_nm_exclusive,
            },
            "policy_id": self.policy_id,
            "radiance_text_sha256": self.radiance_text_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BandMaterialBinding":
        interval = _mapping(payload, "effective_wavelength_range")
        return cls(
            band_id=_string(payload, "band_id"),
            material_identifier=_string(payload, "material_identifier"),
            coefficients=AtrCoefficients.from_dict(_mapping(payload, "original_atr")),
            parameters=RadianceTransParameters.from_dict(
                _mapping(payload, "radiance_arguments")
            ),
            effective_start_nm=_integer(interval, "start_nm"),
            effective_end_nm_exclusive=_integer(interval, "end_nm_exclusive"),
            policy_id=_string(payload, "policy_id"),
            radiance_text_sha256=_string(payload, "radiance_text_sha256"),
        )


@dataclass(frozen=True, slots=True)
class BandArtifactPaths:
    directory: Path
    emitter_path: Path
    plant_path: Path
    octree_path: Path
    rgb_output_path: Path
    decoded_pfd_path: Path
    ambient_cache_path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "directory": str(self.directory),
            "emitters_rad": str(self.emitter_path),
            "rex_plant_rad": str(self.plant_path),
            "scene_oct": str(self.octree_path),
            "receiver_rgb": str(self.rgb_output_path),
            "decoded_band_pfd_npy": str(self.decoded_pfd_path),
            "ambient_cache": str(self.ambient_cache_path),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BandArtifactPaths":
        return cls(
            directory=Path(_string(payload, "directory")),
            emitter_path=Path(_string(payload, "emitters_rad")),
            plant_path=Path(_string(payload, "rex_plant_rad")),
            octree_path=Path(_string(payload, "scene_oct")),
            rgb_output_path=Path(_string(payload, "receiver_rgb")),
            decoded_pfd_path=Path(_string(payload, "decoded_band_pfd_npy")),
            ambient_cache_path=Path(_string(payload, "ambient_cache")),
        )


@dataclass(frozen=True, slots=True)
class RexBandTransportPlan:
    band_id: str
    source: SmdBandSourcePlan
    material: BandMaterialBinding
    paths: BandArtifactPaths
    oconv_command: CommandSpec
    rtrace_command: CommandSpec
    emitter_text_sha256: str
    plant_text_sha256: str
    expected_result_units: str

    def __post_init__(self) -> None:
        if self.band_id != self.source.band_id or self.band_id != self.material.band_id:
            raise ValueError("band source and material binding identities must match.")
        expected_units = (
            EXPECTED_FAR_RED_RESULT_UNITS
            if self.band_id == "far_red"
            else EXPECTED_PAR_BAND_RESULT_UNITS
        )
        if self.expected_result_units != expected_units:
            raise ValueError("band result units do not match band identity.")
        _sha256("emitter_text_sha256", self.emitter_text_sha256)
        _sha256("plant_text_sha256", self.plant_text_sha256)
        if self.oconv_command.stdout_path != self.paths.octree_path:
            raise ValueError("oconv output path does not match band octree path.")
        if self.oconv_command.stdout_mode != "binary":
            raise ValueError("oconv must route binary stdout to the band octree.")
        if (
            len(self.oconv_command.argv) != 6
            or self.oconv_command.argv[:2] != ("oconv", "-f")
            or self.oconv_command.argv[-3] != str(self.paths.emitter_path)
            or Path(self.oconv_command.argv[-2]).name
            != "fixture_body_instances.rad"
            or self.oconv_command.argv[-1] != str(self.paths.plant_path)
        ):
            raise ValueError(
                "band oconv command must compile room, emitters, fixture bodies, and plant."
            )
        if self.rtrace_command.stdin_path is None or (
            self.rtrace_command.stdout_path != self.paths.rgb_output_path
        ):
            raise ValueError("rtrace input/output paths do not match band paths.")
        if self.rtrace_command.stdout_mode != "text":
            raise ValueError("rtrace must route ASCII RGB stdout as text.")
        if (
            self.rtrace_command.argv[0] != "rtrace"
            or "-h" not in self.rtrace_command.argv
            or "-I+" not in self.rtrace_command.argv
            or self.rtrace_command.argv[-1] != str(self.paths.octree_path)
        ):
            raise ValueError("band rtrace command must be an isolated irradiance trace.")
        if str(self.paths.ambient_cache_path) not in self.rtrace_command.argv:
            raise ValueError("band rtrace command must use its independent ambient cache.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "band_id": self.band_id,
            "source": self.source.to_dict(),
            "material": self.material.to_dict(),
            "paths": self.paths.to_dict(),
            "commands": {
                "oconv": _command_to_dict(self.oconv_command),
                "rtrace": _command_to_dict(self.rtrace_command),
            },
            "hashes": {
                "emitter_text_sha256": self.emitter_text_sha256,
                "plant_text_sha256": self.plant_text_sha256,
            },
            "expected_result_units": self.expected_result_units,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RexBandTransportPlan":
        commands = _mapping(payload, "commands")
        hashes = _mapping(payload, "hashes")
        return cls(
            band_id=_string(payload, "band_id"),
            source=SmdBandSourcePlan.from_dict(_mapping(payload, "source")),
            material=BandMaterialBinding.from_dict(_mapping(payload, "material")),
            paths=BandArtifactPaths.from_dict(_mapping(payload, "paths")),
            oconv_command=_command_from_dict(_mapping(commands, "oconv")),
            rtrace_command=_command_from_dict(_mapping(commands, "rtrace")),
            emitter_text_sha256=_string(hashes, "emitter_text_sha256"),
            plant_text_sha256=_string(hashes, "plant_text_sha256"),
            expected_result_units=_string(payload, "expected_result_units"),
        )


@dataclass(frozen=True, slots=True)
class RexFiveBandTransportPlan:
    workspace: Path
    output_root: Path
    manifest_path: Path
    source_model_path: Path
    material_plan_path: Path
    room_path: Path
    fixture_body_path: Path
    receiver_path: Path
    source_model_json_sha256: str
    material_plan_json_sha256: str
    room_sha256: str
    room_model_identity_sha256: str
    fixture_occlusion_identity: str
    band_plans: tuple[RexBandTransportPlan, ...]
    module_count: int
    control_zone_count: int
    watts_by_control_zone: tuple[float, ...]
    schedule_source: str
    layout_sha256: str
    plant_id: str
    plant_seed: int
    leaf_count: int
    patch_count: int
    receiver_count: int
    leaf_patch_grid: tuple[int, int]
    normal_offset_m: float
    geometry_sha256: str
    receiver_text_sha256: str
    ordered_receiver_ids: tuple[str, ...]
    source_model_id: str
    source_normalization_policy: str
    source_resource_hashes: tuple[tuple[str, str], ...]
    material_policy_id: str
    material_claim: str
    material_assumptions: tuple[str, ...]
    neutral_material_disclosure: tuple[str, ...]
    limitations: tuple[str, ...] = FIVE_BAND_LIMITATIONS
    scientific_claim: str = FIVE_BAND_SCIENTIFIC_CLAIM
    backend: str = FIVE_BAND_TRANSPORT_BACKEND
    scalar_calibration_policy: str = SCALAR_CALIBRATION_POLICY_ID
    receiver_hemisphere_policy: str = RECEIVER_HEMISPHERE_POLICY
    physical_patch_area_policy: str = PHYSICAL_PATCH_AREA_POLICY

    def __post_init__(self) -> None:
        if tuple(item.band_id for item in self.band_plans) != FIVE_BAND_ORDER:
            raise ValueError("five-band transport plans must preserve fixed band order.")
        if self.manifest_path != self.output_root / "five_band_transport_plan.json":
            raise ValueError("five-band manifest path is not deterministic.")
        if self.source_model_path != self.output_root / "source_model.json":
            raise ValueError("five-band source-model path is not deterministic.")
        if self.material_plan_path != self.output_root / "rex_material_plan.json":
            raise ValueError("five-band material-plan path is not deterministic.")
        if self.receiver_path != self.output_root / "rex_plant_receivers.pts":
            raise ValueError("five-band receiver path is not deterministic.")
        for band in self.band_plans:
            expected_directory = self.output_root / band.band_id
            expected_paths = (
                expected_directory / "emitters.rad",
                expected_directory / "rex_plant.rad",
                expected_directory / "scene.oct",
                expected_directory / "receiver.rgb",
                expected_directory / "receiver_band_pfd.npy",
                expected_directory
                / (
                    "scene."
                    f"{PRODUCTION_ROOM_MODEL_IDENTITY_SHA256[:16]}.amb"
                ),
            )
            if band.paths.directory != expected_directory or (
                band.paths.emitter_path,
                band.paths.plant_path,
                band.paths.octree_path,
                band.paths.rgb_output_path,
                band.paths.decoded_pfd_path,
                band.paths.ambient_cache_path,
            ) != expected_paths:
                raise ValueError(f"{band.band_id} artifact paths are not deterministic.")
            if band.oconv_command.argv[2] != str(self.room_path):
                raise ValueError("every band scene must use the shared room artifact.")
            if band.oconv_command.argv[4] != str(self.fixture_body_path):
                raise ValueError(
                    "every band scene must use the shared fixture-body artifact."
                )
            if band.rtrace_command.stdin_path != self.receiver_path:
                raise ValueError("every band trace must use the shared receiver artifact.")
        if len({item.paths.octree_path for item in self.band_plans}) != len(
            self.band_plans
        ) or len({item.paths.ambient_cache_path for item in self.band_plans}) != len(
            self.band_plans
        ):
            raise ValueError("each band requires a separate octree and ambient cache.")
        if (
            not self.fixture_occlusion_identity
            or not self.fixture_body_path.is_file()
        ):
            raise ValueError(
                "five-band transport requires authenticated fixture occlusion."
            )
        if self.module_count <= 0 or self.control_zone_count <= 0:
            raise ValueError("layout module and control-zone counts must be positive.")
        if len(self.watts_by_control_zone) != self.control_zone_count:
            raise ValueError("schedule must contain one wattage per control zone.")
        if self.schedule_source != "optimized_explicit_control_zones":
            raise ValueError("five-band planning requires the optimized schedule.")
        for index, watts in enumerate(self.watts_by_control_zone):
            _non_negative(f"control-zone {index} wattage", watts)
        for band in self.band_plans:
            if len(band.source.zone_amplitudes) != self.control_zone_count:
                raise ValueError("band source control-zone count does not match layout.")
            if tuple(
                item.module_wattage for item in band.source.zone_amplitudes
            ) != self.watts_by_control_zone:
                raise ValueError("band source wattages do not match optimized schedule.")
            if sum(
                item.module_count for item in band.source.zone_amplitudes
            ) != self.module_count:
                raise ValueError("band source module counts do not match layout.")
        if (
            self.leaf_count != 32
            or self.leaf_patch_grid != (4, 4)
            or self.patch_count != 512
            or self.receiver_count != 1024
        ):
            raise ValueError(
                "five-band plan requires 32 leaves, 512 patches, and 1024 receivers."
            )
        if self.receiver_count != 2 * self.patch_count:
            raise ValueError("receiver count must equal two times physical patch count.")
        if len(self.ordered_receiver_ids) != self.receiver_count or len(
            set(self.ordered_receiver_ids)
        ) != self.receiver_count:
            raise ValueError("ordered receiver IDs must be complete and unique.")
        if any(
            not self.ordered_receiver_ids[index].endswith("_front")
            or not self.ordered_receiver_ids[index + 1].endswith("_back")
            or self.ordered_receiver_ids[index][:-6]
            != self.ordered_receiver_ids[index + 1][:-5]
            for index in range(0, self.receiver_count, 2)
        ):
            raise ValueError("receiver IDs must preserve stable front/back pairs.")
        _non_negative("normal_offset_m", self.normal_offset_m)
        for name in (
            "source_model_json_sha256",
            "material_plan_json_sha256",
            "room_sha256",
            "layout_sha256",
            "geometry_sha256",
            "receiver_text_sha256",
        ):
            _sha256(name, getattr(self, name))
        _validate_hash_pairs(self.source_resource_hashes)
        if self.room_model_identity_sha256 != PRODUCTION_ROOM_MODEL_IDENTITY_SHA256:
            raise ValueError(
                "five-band room model is not the current production authority."
            )
        expected_source_policy = {
            SMD_SOURCE_MODEL_ID: SMD_NORMALIZATION_POLICY,
            PROPOSED_CONVENTIONAL_LED_CONTROL_SOURCE_MODEL_ID: (
                CONVENTIONAL_SPD_NORMALIZATION_POLICY
            ),
        }
        if self.source_model_id not in expected_source_policy:
            raise ValueError("unexpected source model identity.")
        if self.source_normalization_policy != expected_source_policy[
            self.source_model_id
        ]:
            raise ValueError("unexpected source normalization policy.")
        if (
            self.source_model_id == PROPOSED_CONVENTIONAL_LED_CONTROL_SOURCE_MODEL_ID
            and self.source_resource_hashes
            != ((CONVENTIONAL_SPD_RESOURCE_NAME, CONVENTIONAL_SPD_SHA256),)
        ):
            raise ValueError(
                "controlled transport requires the exact approved Conventional LED SPD."
            )
        if self.material_policy_id != DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID:
            raise ValueError("unexpected Rex material policy identity.")
        if self.material_claim != REX_RADIANCE_MATERIAL_PLAN_CLAIM:
            raise ValueError("unexpected Rex material claim.")
        if self.material_assumptions != REX_TRANS_MODEL_ASSUMPTIONS:
            raise ValueError("unexpected Rex material assumptions.")
        if self.limitations != FIVE_BAND_LIMITATIONS:
            raise ValueError("five-band scientific limitations must be explicit and fixed.")
        if self.scientific_claim != FIVE_BAND_SCIENTIFIC_CLAIM:
            raise ValueError("unexpected five-band scientific claim.")
        if self.backend != FIVE_BAND_TRANSPORT_BACKEND:
            raise ValueError("five-band backend is fixed to isolated scalar dry-runs.")
        if self.scalar_calibration_policy != SCALAR_CALIBRATION_POLICY_ID:
            raise ValueError("unexpected scalar calibration reconciliation policy.")
        if self.receiver_hemisphere_policy != RECEIVER_HEMISPHERE_POLICY:
            raise ValueError("unexpected receiver hemisphere policy.")
        if self.physical_patch_area_policy != PHYSICAL_PATCH_AREA_POLICY:
            raise ValueError("unexpected physical patch area policy.")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": FIVE_BAND_PLAN_SCHEMA_VERSION,
            "payload_type": FIVE_BAND_PLAN_PAYLOAD_TYPE,
            "backend": self.backend,
            "scientific_claim": self.scientific_claim,
            "scalar_calibration_policy": self.scalar_calibration_policy,
            "band_order": list(FIVE_BAND_ORDER),
            "workspace": str(self.workspace),
            "output_root": str(self.output_root),
            "shared_artifacts": {
                "manifest": str(self.manifest_path),
                "source_model": str(self.source_model_path),
                "material_plan": str(self.material_plan_path),
                "room": str(self.room_path),
                "room_model": production_room_model_payload(),
                "fixture_bodies": str(self.fixture_body_path),
                "receivers": str(self.receiver_path),
                "hashes": {
                    "source_model_json_sha256": self.source_model_json_sha256,
                    "material_plan_json_sha256": self.material_plan_json_sha256,
                    "room_sha256": self.room_sha256,
                    "room_model_identity_sha256": (
                        self.room_model_identity_sha256
                    ),
                    "fixture_occlusion_identity": (
                        self.fixture_occlusion_identity
                    ),
                },
            },
            "source_provenance": {
                "source_model_id": self.source_model_id,
                "normalization_policy": self.source_normalization_policy,
                "resource_hashes": dict(self.source_resource_hashes),
            },
            "material_provenance": {
                "policy_id": self.material_policy_id,
                "claim": self.material_claim,
                "assumptions": list(self.material_assumptions),
            },
            "layout_schedule": {
                "module_count": self.module_count,
                "control_zone_count": self.control_zone_count,
                "watts_by_control_zone": list(self.watts_by_control_zone),
                "schedule_source": self.schedule_source,
                "layout_sha256": self.layout_sha256,
            },
            "plant_receivers": {
                "plant_id": self.plant_id,
                "seed": self.plant_seed,
                "leaf_count": self.leaf_count,
                "leaf_patch_grid": list(self.leaf_patch_grid),
                "patch_count": self.patch_count,
                "receiver_count": self.receiver_count,
                "normal_offset_m": self.normal_offset_m,
                "geometry_sha256": self.geometry_sha256,
                "receiver_text_sha256": self.receiver_text_sha256,
                "ordered_receiver_ids": list(self.ordered_receiver_ids),
                "receiver_hemisphere_policy": self.receiver_hemisphere_policy,
                "physical_patch_area_policy": self.physical_patch_area_policy,
            },
            "expected_units": {
                "par_bands": EXPECTED_PAR_BAND_RESULT_UNITS,
                "far_red": EXPECTED_FAR_RED_RESULT_UNITS,
                "scalar_par_policy": (
                    "separate_optional_comparator_not_added_to_five_band_results"
                ),
            },
            "neutral_material_disclosure": list(self.neutral_material_disclosure),
            "limitations": list(self.limitations),
            "bands": [item.to_dict() for item in self.band_plans],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "RexFiveBandTransportPlan":
        if _integer(payload, "schema_version") != FIVE_BAND_PLAN_SCHEMA_VERSION:
            raise ValueError("unsupported five-band plan schema_version.")
        if _string(payload, "payload_type") != FIVE_BAND_PLAN_PAYLOAD_TYPE:
            raise ValueError("unexpected five-band plan payload_type.")
        band_order = _string_sequence(payload, "band_order")
        if band_order != FIVE_BAND_ORDER:
            raise ValueError("serialized five-band order is invalid.")
        shared = _mapping(payload, "shared_artifacts")
        if dict(_mapping(shared, "room_model")) != production_room_model_payload():
            raise ValueError(
                "serialized five-band room model is not the production authority."
            )
        shared_hashes = _mapping(shared, "hashes")
        source = _mapping(payload, "source_provenance")
        material = _mapping(payload, "material_provenance")
        layout = _mapping(payload, "layout_schedule")
        plant = _mapping(payload, "plant_receivers")
        patch_grid = _integer_sequence(plant, "leaf_patch_grid", expected=2)
        units = _mapping(payload, "expected_units")
        if (
            units.get("par_bands") != EXPECTED_PAR_BAND_RESULT_UNITS
            or units.get("far_red") != EXPECTED_FAR_RED_RESULT_UNITS
            or units.get("scalar_par_policy")
            != "separate_optional_comparator_not_added_to_five_band_results"
        ):
            raise ValueError("serialized five-band result units are invalid.")
        return cls(
            workspace=Path(_string(payload, "workspace")),
            output_root=Path(_string(payload, "output_root")),
            manifest_path=Path(_string(shared, "manifest")),
            source_model_path=Path(_string(shared, "source_model")),
            material_plan_path=Path(_string(shared, "material_plan")),
            room_path=Path(_string(shared, "room")),
            fixture_body_path=Path(_string(shared, "fixture_bodies")),
            receiver_path=Path(_string(shared, "receivers")),
            source_model_json_sha256=_string(
                shared_hashes, "source_model_json_sha256"
            ),
            material_plan_json_sha256=_string(
                shared_hashes, "material_plan_json_sha256"
            ),
            room_sha256=_string(shared_hashes, "room_sha256"),
            room_model_identity_sha256=_string(
                shared_hashes, "room_model_identity_sha256"
            ),
            fixture_occlusion_identity=_string(
                shared_hashes, "fixture_occlusion_identity"
            ),
            band_plans=tuple(
                RexBandTransportPlan.from_dict(item)
                for item in _mapping_sequence(payload, "bands")
            ),
            module_count=_integer(layout, "module_count"),
            control_zone_count=_integer(layout, "control_zone_count"),
            watts_by_control_zone=_number_sequence(layout, "watts_by_control_zone"),
            schedule_source=_string(layout, "schedule_source"),
            layout_sha256=_string(layout, "layout_sha256"),
            plant_id=_string(plant, "plant_id"),
            plant_seed=_integer(plant, "seed"),
            leaf_count=_integer(plant, "leaf_count"),
            patch_count=_integer(plant, "patch_count"),
            receiver_count=_integer(plant, "receiver_count"),
            leaf_patch_grid=(patch_grid[0], patch_grid[1]),
            normal_offset_m=_number(plant, "normal_offset_m"),
            geometry_sha256=_string(plant, "geometry_sha256"),
            receiver_text_sha256=_string(plant, "receiver_text_sha256"),
            ordered_receiver_ids=_string_sequence(plant, "ordered_receiver_ids"),
            source_model_id=_string(source, "source_model_id"),
            source_normalization_policy=_string(source, "normalization_policy"),
            source_resource_hashes=_hash_pairs(_mapping(source, "resource_hashes")),
            material_policy_id=_string(material, "policy_id"),
            material_claim=_string(material, "claim"),
            material_assumptions=_string_sequence(material, "assumptions"),
            neutral_material_disclosure=_string_sequence(
                payload, "neutral_material_disclosure"
            ),
            limitations=_string_sequence(payload, "limitations"),
            scientific_claim=_string(payload, "scientific_claim"),
            backend=_string(payload, "backend"),
            scalar_calibration_policy=_string(payload, "scalar_calibration_policy"),
            receiver_hemisphere_policy=_string(
                plant, "receiver_hemisphere_policy"
            ),
            physical_patch_area_policy=_string(plant, "physical_patch_area_policy"),
        )


def build_five_band_source_plans(
    source_model: SmdSourceModel,
    layout: SmdLayout,
    schedule: ModulePowerSchedule,
    *,
    internal_source_ppe_umol_per_j: float = INTERNAL_SOURCE_PPE_UMOL_PER_J,
    emitter_area_m2: float | None = None,
    bands: Sequence[SpectralBand] = FIXED_TRANSPORT_BANDS,
    source_authority: ProposedSourceAuthority | None = None,
) -> tuple[SmdBandSourcePlan, ...]:
    """Normalize the relative source mixture to calibrated internal PAR PPF."""

    ordered_bands = _validate_fixed_bands(bands)
    source = source_authority or resolve_proposed_source_authority()
    anchor = _positive(
        "internal_source_ppe_umol_per_j",
        (
            source.internal_source_ppe_umol_per_j
            if source_authority is not None
            else internal_source_ppe_umol_per_j
        ),
    )
    if not math.isclose(
        anchor,
        source.internal_source_ppe_umol_per_j,
        rel_tol=0.0,
        abs_tol=FLOAT_ABS_TOLERANCE,
    ):
        if source_authority is None:
            raise ValueError(
                "absolute component-PPE authority is prohibited; use the calibrated "
                f"{INTERNAL_SOURCE_PPE_UMOL_PER_J:.15g} umol/J internal source anchor."
            )
        raise ValueError(
            "five-band internal source PPE disagrees with selected source mode."
        )
    area = _positive(
        "emitter_area_m2",
        source.emitter_area_m2 if emitter_area_m2 is None else emitter_area_m2,
    )
    _validate_layout_schedule(layout, schedule)
    total_nominal = _positive(
        "source total nominal PAR PPF", source_model.total_nominal_par_ppf_umol_s
    )
    nominal_watts = math.fsum(
        item.component.count * item.component.nominal_package_watts
        for item in source_model.components
    )
    aggregate_ppe = total_nominal / _positive("nominal component watts", nominal_watts)
    normalization = anchor / aggregate_ppe
    hashes = tuple(sorted((str(key), str(value)) for key, value in source_model.resource_hashes.items()))
    _validate_hash_pairs(hashes)
    if set(source_model.band_photon_fractions_relative_to_par) != set(FIVE_BAND_ORDER):
        raise ValueError("source model must contain exactly the five fixed band fractions.")

    module_counts = tuple(
        sum(1 for module in layout.modules if module.control_zone_index == zone_index)
        for zone_index in layout.control_zone_indices
    )
    plans: list[SmdBandSourcePlan] = []
    for band in ordered_bands:
        nominal_band = source_model.photon_distribution.amount_in_band(band)
        fraction = _positive_fraction(
            f"{band.band_id} fraction_relative_to_par",
            source_model.band_photon_fractions_relative_to_par[band.band_id],
        )
        if not math.isclose(
            nominal_band / total_nominal,
            fraction,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(f"source model {band.band_id} fraction is inconsistent.")
        contributions = tuple(
            ComponentBandContribution(
                component_id=item.component.component_id,
                nominal_par_ppf_umol_s=item.nominal_par_ppf_umol_s,
                fraction_of_component_par=(
                    item.photon_distribution.amount_in_band(band)
                    / item.nominal_par_ppf_umol_s
                ),
                nominal_band_ppf_umol_s=item.photon_distribution.amount_in_band(band),
            )
            for item in source_model.components
        )
        internal_band_yield = anchor * fraction
        zones = tuple(
            BandZoneSourceAmplitude(
                control_zone_index=zone_index,
                module_count=module_counts[zone_index],
                module_wattage=schedule.watts_by_control_zone[zone_index],
                module_band_ppf_umol_s=(
                    schedule.watts_by_control_zone[zone_index] * internal_band_yield
                ),
                grayscale_radiance_rgb=(radiance, radiance, radiance),
            )
            for zone_index in layout.control_zone_indices
            for radiance in (
                (
                    schedule.watts_by_control_zone[zone_index]
                    * internal_band_yield
                    / area
                )
                if source.source_mode == COB_SOURCE_MODE
                else photon_flux_to_lambertian_radiance(
                    schedule.watts_by_control_zone[zone_index] * internal_band_yield,
                    area,
                ),
            )
        )
        plans.append(
            SmdBandSourcePlan(
                band_id=band.band_id,
                label=band.label,
                start_nm=band.start_nm,
                end_nm=band.end_nm,
                total_nominal_par_ppf_umol_s=total_nominal,
                nominal_band_ppf_umol_s=nominal_band,
                fraction_relative_to_par=fraction,
                component_contributions=contributions,
                internal_source_ppe_umol_per_j=anchor,
                accepted_fixture_transmission=(
                    source.completed_aperture_transmission
                ),
                completed_aperture_fixture_ppe_umol_per_j=(
                    COMPLETED_APERTURE_PPE_UMOL_PER_J
                ),
                raw_relative_mix_aggregate_ppe_umol_per_j=aggregate_ppe,
                pretransport_relative_mix_normalization_factor=normalization,
                internal_band_photon_yield_umol_per_j=internal_band_yield,
                emitter_area_m2=area,
                zone_amplitudes=zones,
                source_model_id=source_model.source_model_id,
                normalization_policy=source_model.normalization_policy,
                resource_hashes=hashes,
                spectral_basis_id=source_model.spectral_basis_id,
                spatial_model_id=(
                    "centered_22mm_circular_les"
                    if source.source_mode == COB_SOURCE_MODE
                    else COMBINED_MODULE_EMITTING_WINDOW
                ),
                angular_model_id=source.angular_model_id,
                source_mode=source.source_mode,
            )
        )
    par_yield = math.fsum(
        item.internal_band_photon_yield_umol_per_j
        for item in plans
        if item.band_id != "far_red"
    )
    if not math.isclose(par_yield, anchor, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(
            "blue+green+orange+red internal photon yields do not close to the "
            "calibrated internal PAR source PPE."
        )
    return tuple(plans)


def validate_five_band_material_mapping(
    material_plan: RexRadianceTransMaterialPlan,
    entries: Sequence[RexRadianceTransIntervalPlan] | None = None,
) -> tuple[RexRadianceTransIntervalPlan, ...]:
    """Return the exact ordered non-scalar Phase 17 material entries."""

    selected = tuple(material_plan.materials[1:] if entries is None else entries)
    if tuple(item.interval_id for item in selected) != FIVE_BAND_ORDER:
        raise ValueError(
            "Rex material entries must contain blue, green, orange, red, far_red "
            "exactly once and in fixed order."
        )
    if len({item.material_identifier for item in selected}) != len(selected):
        raise ValueError("Rex band material identifiers must be unique.")
    for band_id, item in zip(FIVE_BAND_ORDER, selected, strict=True):
        expected = material_plan.material(band_id)
        if item != expected or item.material_identifier != f"rex_leaf_trans_{band_id}":
            raise ValueError(f"Phase 17 material mapping mismatch for {band_id}.")
    return selected


def plan_rex_five_band_transport(
    workspace: str | Path,
    *,
    output_root: str | Path | None = None,
    seed: int = 1,
    normal_offset_m: float = 5e-5,
    source_model: SmdSourceModel | None = None,
    material_plan: RexRadianceTransMaterialPlan | None = None,
    band_materials: Sequence[RexRadianceTransIntervalPlan] | None = None,
    internal_source_ppe_umol_per_j: float = INTERNAL_SOURCE_PPE_UMOL_PER_J,
    bands: Sequence[SpectralBand] = FIXED_TRANSPORT_BANDS,
    nthreads: int | None = None,
) -> RexFiveBandTransportPlan:
    """Materialize deterministic band inputs and return non-executing commands."""

    root = Path(workspace).expanduser().resolve()
    composite = plan_composite_validation(root)
    basis = composite.paths
    destination = (
        root / "rex_five_band"
        if output_root is None
        else Path(output_root).expanduser().resolve()
    )
    source = source_model or build_nominal_smd_source_model()
    materials = material_plan or build_rex_radiance_trans_material_plan(
        build_rex_source_weighted_atr_payload(source_model=source)
    )
    if tuple(materials.phase16_atr.source_resource_hashes) != tuple(
        sorted(source.resource_hashes.items())
    ):
        raise FiveBandPlanError(
            "Phase 17 material provenance does not match the Phase 15 source resources."
        )
    ordered_materials = validate_five_band_material_mapping(materials, band_materials)
    source_plans = build_five_band_source_plans(
        source,
        composite.layout,
        composite.schedule,
        internal_source_ppe_umol_per_j=internal_source_ppe_umol_per_j,
        bands=bands,
    )

    plant = generate_rex_butterhead_plant(RexPlantConfig(seed=seed))
    receivers = build_two_sided_patch_receivers(
        plant,
        normal_offset_m=normal_offset_m,
    )
    _validate_default_receivers(plant.config, plant.patch_count, receivers)
    receiver_text = receiver_sample_input_text(item.to_dict() for item in receivers)
    generic_geometry_text = export_plant_mesh_to_radiance(plant)

    source_model_path = destination / "source_model.json"
    material_plan_path = destination / "rex_material_plan.json"
    receiver_path = destination / "rex_plant_receivers.pts"
    manifest_path = destination / "five_band_transport_plan.json"
    source_text = format_smd_source_model_json(source)
    material_text = format_rex_radiance_trans_material_plan_json(materials)
    basis_artifacts = load_basis_workspace_artifacts(root)
    basis_manifest = basis_artifacts.manifest
    require_current_proposed_source_manifest(basis_manifest)
    room_text = basis.room_path.read_text(encoding="utf-8")
    expected_room_text = room_radiance_text(
        RoomDimensions(
            basis_manifest.room_length_m,
            basis_manifest.room_width_m,
            basis_manifest.room_height_m,
        )
    )
    if room_text != expected_room_text:
        raise FiveBandPlanError(
            "five-band room.rad is not the current production room authority."
        )

    fixture_body_path = (
        root / "fixture_occlusion" / "fixture_body_instances.rad"
    )
    if (
        not basis_manifest.fixture_occlusion_identity
        or not fixture_body_path.is_file()
    ):
        raise FiveBandPlanError(
            "basis workspace lacks authenticated fixture occlusion."
        )
    trace_nthreads = (
        basis_manifest.nthreads
        if nthreads is None
        else _positive_integer("nthreads", nthreads)
    )

    band_plans: list[RexBandTransportPlan] = []
    planned_texts: list[tuple[Path, str]] = [
        (source_model_path, source_text),
        (material_plan_path, material_text),
        (receiver_path, receiver_text),
    ]
    for source_plan, material_entry in zip(
        source_plans, ordered_materials, strict=True
    ):
        band_id = source_plan.band_id
        band_directory = destination / band_id
        emitter_path = band_directory / "emitters.rad"
        plant_path = band_directory / "rex_plant.rad"
        octree_path = band_directory / "scene.oct"
        rgb_path = band_directory / "receiver.rgb"
        decoded_path = band_directory / "receiver_band_pfd.npy"
        ambient_path = band_directory / (
            "scene."
            f"{PRODUCTION_ROOM_MODEL_IDENTITY_SHA256[:16]}.amb"
        )
        emitter_document = build_smd_band_radiance_document(
            composite.layout,
            composite.schedule,
            band_id=band_id,
            internal_band_photon_yield_umol_per_j=(
                source_plan.internal_band_photon_yield_umol_per_j
            ),
        )
        _validate_emitter_against_source_plan(emitter_document.metadata, source_plan)
        plant_text = export_plant_mesh_to_radiance(
            plant,
            material_name=material_entry.material_identifier,
            material_definition=material_entry.radiance_text,
        )
        options = replace_radiance_option_value(
            basis_manifest.radiance_options,
            "-af",
            str(ambient_path),
        )
        oconv = build_oconv_command(
            (
                basis.room_path,
                emitter_path,
                fixture_body_path,
                plant_path,
            ),
            output_octree=octree_path,
            cwd=band_directory,
            label=f"compile_rex_{band_id}_receiver",
        )
        raw_rtrace = build_plant_receiver_rtrace_command(
            octree=octree_path,
            receiver_input=receiver_path,
            rgb_output=rgb_path,
            options=options,
            nthreads=trace_nthreads,
            cwd=band_directory,
        )
        rtrace = CommandSpec(
            argv=raw_rtrace.argv,
            stdin_path=raw_rtrace.stdin_path,
            stdout_path=raw_rtrace.stdout_path,
            stdout_mode=raw_rtrace.stdout_mode,
            cwd=raw_rtrace.cwd,
            env=raw_rtrace.env,
            label=f"trace_rex_{band_id}_receiver",
        )
        binding = _material_binding(material_entry)
        paths = BandArtifactPaths(
            directory=band_directory,
            emitter_path=emitter_path,
            plant_path=plant_path,
            octree_path=octree_path,
            rgb_output_path=rgb_path,
            decoded_pfd_path=decoded_path,
            ambient_cache_path=ambient_path,
        )
        band_plans.append(
            RexBandTransportPlan(
                band_id=band_id,
                source=source_plan,
                material=binding,
                paths=paths,
                oconv_command=oconv,
                rtrace_command=rtrace,
                emitter_text_sha256=_sha256_text(emitter_document.radiance_text),
                plant_text_sha256=_sha256_text(plant_text),
                expected_result_units=(
                    EXPECTED_FAR_RED_RESULT_UNITS
                    if band_id == "far_red"
                    else EXPECTED_PAR_BAND_RESULT_UNITS
                ),
            )
        )
        planned_texts.extend(
            ((emitter_path, emitter_document.radiance_text), (plant_path, plant_text))
        )

    plan = RexFiveBandTransportPlan(
        workspace=root,
        output_root=destination,
        manifest_path=manifest_path,
        source_model_path=source_model_path,
        material_plan_path=material_plan_path,
        room_path=basis.room_path,
        fixture_body_path=fixture_body_path,
        receiver_path=receiver_path,
        source_model_json_sha256=_sha256_text(source_text),
        material_plan_json_sha256=_sha256_text(material_text),
        room_sha256=_sha256_file(basis.room_path),
        room_model_identity_sha256=PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
        fixture_occlusion_identity=(
            basis_manifest.fixture_occlusion_identity
        ),
        band_plans=tuple(band_plans),
        module_count=len(composite.layout.modules),
        control_zone_count=composite.layout.control_zone_count,
        watts_by_control_zone=composite.schedule.watts_by_control_zone,
        schedule_source=composite.schedule.schedule_source,
        layout_sha256=_layout_sha256(composite.layout),
        plant_id=plant.plant_id,
        plant_seed=plant.seed,
        leaf_count=len(plant.leaves),
        patch_count=plant.patch_count,
        receiver_count=len(receivers),
        leaf_patch_grid=plant.config.leaf_patch_grid,
        normal_offset_m=float(normal_offset_m),
        geometry_sha256=_sha256_text(generic_geometry_text),
        receiver_text_sha256=_sha256_text(receiver_text),
        ordered_receiver_ids=tuple(item.receiver_id for item in receivers),
        source_model_id=source.source_model_id,
        source_normalization_policy=source.normalization_policy,
        source_resource_hashes=tuple(sorted(source.resource_hashes.items())),
        material_policy_id=materials.policy_id,
        material_claim=materials.claim_language,
        material_assumptions=materials.assumptions,
        neutral_material_disclosure=(
            (
                "room: production closed six-surface authority uses neutral diffuse "
                "0.90 walls/ceiling and neutral diffuse 0.10 floor"
            ),
            "fixture: proposed_ptfe is wavelength-neutral ideal diffuse 0.90",
            (
                "fixture: proposed_pmma is a wavelength-neutral nonabsorbing "
                "dielectric with IOR 1.49"
            ),
            f"fixture optical stack: {PROPOSED_FIXTURE_OPTICAL_STACK_ID}",
        ),
    )
    plan_text = format_rex_five_band_transport_plan_json(plan)
    planned_texts.append((manifest_path, plan_text))
    _materialize_compatible_texts(planned_texts)
    return plan


def format_rex_five_band_transport_plan_json(plan: RexFiveBandTransportPlan) -> str:
    return json.dumps(plan.to_payload(), indent=2, sort_keys=True) + "\n"


def read_rex_five_band_transport_plan_json(
    path: str | Path,
) -> RexFiveBandTransportPlan:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"five-band transport plan not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"five-band transport plan JSON is malformed: {source}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("five-band transport plan JSON root must be an object.")
    return RexFiveBandTransportPlan.from_payload(payload)


def _material_binding(entry: RexRadianceTransIntervalPlan) -> BandMaterialBinding:
    interval = entry.source_interval
    return BandMaterialBinding(
        band_id=entry.interval_id,
        material_identifier=entry.material_identifier,
        coefficients=interval.coefficients,
        parameters=entry.parameters,
        effective_start_nm=interval.effective_start_nm,
        effective_end_nm_exclusive=interval.effective_end_nm_exclusive,
        policy_id=DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID,
        radiance_text_sha256=_sha256_text(entry.radiance_text),
    )


def _validate_emitter_against_source_plan(
    metadata: SmdBandEmitterMetadata,
    source: SmdBandSourcePlan,
) -> None:
    if metadata.band_id != source.band_id:
        raise ValueError("band emitter identity does not match source plan.")
    if not math.isclose(
        metadata.internal_band_photon_yield_umol_per_j,
        source.internal_band_photon_yield_umol_per_j,
        rel_tol=0.0,
        abs_tol=1e-15,
    ):
        raise ValueError("band emitter photon yield does not match source plan.")
    if metadata.emitter_area_per_module_m2 != source.emitter_area_m2:
        raise ValueError("band emitter area does not match source plan.")
    if metadata.source_radiance_by_control_zone != tuple(
        item.grayscale_radiance_rgb[0] for item in source.zone_amplitudes
    ):
        raise ValueError("band emitter zone radiances do not match source plan.")
    if metadata.spatial_model_id != COMBINED_MODULE_EMITTING_WINDOW:
        raise ValueError("band emitter must use combined module-window geometry.")
    if metadata.optical_stack_id != PROPOSED_FIXTURE_OPTICAL_STACK_ID:
        raise ValueError("five-band planning requires the calibrated fixture stack.")
    if not math.isclose(
        metadata.accepted_fixture_transmission,
        source.accepted_fixture_transmission,
        rel_tol=0.0,
        abs_tol=1e-15,
    ):
        raise ValueError("band emitter fixture transmission does not match source plan.")


def _validate_default_receivers(
    config: RexPlantConfig,
    patch_count: int,
    receivers: Sequence[MeshPatchReceiverSample],
) -> None:
    if (
        config.leaf_count != 32
        or config.leaf_patch_grid != (4, 4)
        or patch_count != 512
        or len(receivers) != 1024
    ):
        raise ValueError(
            "five-band planning requires 32 leaves, a 4x4 patch grid, 512 "
            "physical patches, and 1024 receivers."
        )
    for index in range(0, len(receivers), 2):
        front = receivers[index]
        back = receivers[index + 1]
        if (
            front.side != "front"
            or back.side != "back"
            or front.patch_id != back.patch_id
            or front.area_m2 != back.area_m2
            or back.normal != tuple(-value for value in front.normal)
        ):
            raise ValueError("receiver samples must preserve exact front/back pairs.")


def _validate_layout_schedule(layout: SmdLayout, schedule: ModulePowerSchedule) -> None:
    if len(layout.modules) != schedule.module_count or (
        layout.control_zone_count != schedule.control_zone_count
    ):
        raise ValueError("optimized schedule dimensions must match the SMD layout.")
    if schedule.schedule_source not in {
        "optimized_explicit_control_zones",
        "uniform_module_dimming",
    }:
        raise ValueError(
            "five-band planning requires an explicit optimized or uniform schedule."
        )
    if len(schedule.watts_by_control_zone) != layout.control_zone_count:
        raise ValueError("schedule must contain one wattage per control zone.")
    for zone_index, watts in enumerate(schedule.watts_by_control_zone):
        try:
            _non_negative(f"control-zone {zone_index} wattage", watts)
        except ValueError as exc:
            raise ValueError(
                f"control-zone {zone_index} wattage must be finite and non-negative."
            ) from exc
    for module, watts in zip(layout.modules, schedule.watts_by_module, strict=True):
        _non_negative(f"module {module.module_index} wattage", watts)
        if watts != schedule.watts_by_control_zone[module.control_zone_index]:
            raise ValueError("module wattage does not match its control-zone schedule.")


def _validate_fixed_bands(bands: Sequence[SpectralBand]) -> tuple[SpectralBand, ...]:
    values = tuple(bands)
    if values != FIXED_TRANSPORT_BANDS:
        raise ValueError(
            "spectral bands must be exactly blue, green, orange, red, far_red in "
            "the established interval order."
        )
    return values


def _fixed_band(band_id: str) -> SpectralBand:
    for band in FIXED_TRANSPORT_BANDS:
        if band.band_id == band_id:
            return band
    raise ValueError(f"unknown fixed transport band: {band_id!r}.")


def _layout_sha256(layout: SmdLayout) -> str:
    payload = {
        "proposed_layout_mode": layout.proposed_layout_mode.value,
        "proposed_ring_mode": layout.proposed_ring_mode.value,
        "module_pattern_id": layout.module_pattern_id,
        "fixture_policy_id": layout.fixture_policy_id,
        "modules": [
            {
                "module_index": item.module_index,
                "x_m": item.x_m,
                "y_m": item.y_m,
                "z_m": item.z_m,
                "control_zone_index": item.control_zone_index,
            }
            for item in layout.modules
        ],
    }
    return _sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _materialize_compatible_texts(items: Sequence[tuple[Path, str]]) -> None:
    expected = tuple((path, text.encode("utf-8")) for path, text in items)
    seen: set[Path] = set()
    for path, data in expected:
        if path in seen:
            raise ValueError(f"duplicate five-band artifact path: {path}")
        seen.add(path)
        if path.exists() and (not path.is_file() or path.read_bytes() != data):
            raise FiveBandWorkspaceConflictError(
                f"existing five-band artifact is incompatible: {path}"
            )
    staged: list[tuple[Path, Path]] = []
    committed: list[Path] = []
    try:
        for path, data in expected:
            if not path.exists():
                staged.append((stage_bytes(path, data), path))
        for temporary, final in staged:
            if final.exists():
                raise FiveBandWorkspaceConflictError(
                    f"five-band artifact appeared during materialization: {final}"
                )
            commit_staged(temporary, final)
            committed.append(final)
    except BaseException:
        for path in reversed(committed):
            path.unlink(missing_ok=True)
        raise
    finally:
        for temporary, _final in staged:
            temporary.unlink(missing_ok=True)


def _command_to_dict(command: CommandSpec) -> dict[str, Any]:
    return {
        "argv": list(command.argv),
        "stdin_path": None if command.stdin_path is None else str(command.stdin_path),
        "stdout_path": None if command.stdout_path is None else str(command.stdout_path),
        "stdout_mode": command.stdout_mode,
        "cwd": None if command.cwd is None else str(command.cwd),
        "env": dict(sorted(command.env.items())),
        "label": command.label,
        "shell": False,
    }


def _command_from_dict(payload: Mapping[str, Any]) -> CommandSpec:
    if payload.get("shell") is not False:
        raise ValueError("five-band command payloads require shell=false.")
    argv = _string_sequence(payload, "argv")
    env_payload = _mapping(payload, "env")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in env_payload.items()):
        raise ValueError("command env must map strings to strings.")
    return CommandSpec(
        argv=argv,
        stdin_path=_optional_path(payload.get("stdin_path"), "stdin_path"),
        stdout_path=_optional_path(payload.get("stdout_path"), "stdout_path"),
        stdout_mode=_stdout_mode(
            payload.get("stdout_mode", "binary" if argv[0] == "oconv" else "text")
        ),
        cwd=_optional_path(payload.get("cwd"), "cwd"),
        env=dict(env_payload),
        label=_string(payload, "label"),
    )


def _optional_path(value: object, name: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string or null.")
    return Path(value)


def _stdout_mode(value: object) -> StdoutMode:
    if value not in ("text", "binary"):
        raise ValueError("stdout_mode must be 'text' or 'binary'.")
    return cast(StdoutMode, value)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256(name: str, value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest.")
    return value


def _validate_hash_pairs(values: tuple[tuple[str, str], ...]) -> None:
    if not values or tuple(sorted(values)) != values or len({key for key, _ in values}) != len(values):
        raise ValueError("resource hashes must be non-empty, unique, and sorted.")
    for key, value in values:
        if not key:
            raise ValueError("resource hash names must be non-empty.")
        _sha256(f"resource hash {key}", value)


def _hash_pairs(payload: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in payload.items()):
        raise ValueError("resource hashes must map strings to strings.")
    return tuple(sorted((str(key), str(value)) for key, value in payload.items()))


def _positive(name: str, value: object) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be finite and positive.")
    return number


def _non_negative(name: str, value: object) -> float:
    number = _finite(name, value)
    if number < 0.0:
        raise ValueError(f"{name} must be finite and non-negative.")
    return number


def _fraction(name: str, value: object) -> float:
    number = _finite(name, value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be in [0, 1].")
    return number


def _positive_fraction(name: str, value: object) -> float:
    number = _fraction(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be positive.")
    return number


def _finite(name: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{name} must be finite and numeric.")
    return float(value)


def _positive_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _non_negative_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer.")
    return value


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object.")
    return value


def _mapping_sequence(payload: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"{key} must be a list of objects.")
    return tuple(value)


def _string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _string_sequence(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{key} must be a list of non-empty strings.")
    return tuple(value)


def _integer(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer.")
    return value


def _number(payload: Mapping[str, Any], key: str) -> float:
    return _finite(key, payload.get(key))


def _number_sequence(
    payload: Mapping[str, Any], key: str, *, expected: int | None = None
) -> tuple[float, ...]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a numeric list.")
    result = tuple(_finite(key, item) for item in value)
    if expected is not None and len(result) != expected:
        raise ValueError(f"{key} must contain {expected} values.")
    return result


def _integer_sequence(
    payload: Mapping[str, Any], key: str, *, expected: int | None = None
) -> tuple[int, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        raise ValueError(f"{key} must be an integer list.")
    result = tuple(value)
    if expected is not None and len(result) != expected:
        raise ValueError(f"{key} must contain {expected} values.")
    return result
