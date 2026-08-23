"""Validated Phase 20 absorption metrics from Phase 19 five-band artifacts.

This module performs deterministic post-processing only.  It never discovers
or executes Radiance and never derives optical coefficients from raw resources.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Final, Mapping, Sequence
import zipfile

import numpy as np
from numpy.typing import NDArray

from fspm_optics.optics.rex_material_plan import (
    RexRadianceTransMaterialPlan,
    read_rex_radiance_trans_material_plan_json,
)
from fspm_optics.optics.rex_weighting import WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE
from fspm_optics.optics.rex_weighting import AtrCoefficients
from fspm_optics.plants.generator import generate_rex_butterhead_plant
from fspm_optics.plants.radiance_export import export_plant_mesh_to_radiance
from fspm_optics.plants.rex import RexPlantConfig
from fspm_optics.receivers.samples import (
    MeshPatchReceiverSample,
    build_two_sided_patch_receivers,
    receiver_sample_input_text,
)
from fspm_optics.transport.basis.atomic import atomic_write_bytes, atomic_write_text
from fspm_optics.transport.five_band import (
    FIVE_BAND_LIMITATIONS,
    FIVE_BAND_ORDER,
    FIVE_BAND_PLAN_PAYLOAD_TYPE,
    FIVE_BAND_PLAN_SCHEMA_VERSION,
    RexFiveBandTransportPlan,
    format_rex_five_band_transport_plan_json,
    read_rex_five_band_transport_plan_json,
)
from fspm_optics.transport.five_band_execution import (
    FIVE_BAND_NATIVE_RUN_PAYLOAD_TYPE,
    FIVE_BAND_NATIVE_RUN_SCHEMA_VERSION,
    FIVE_BAND_SUMMARY_FILENAME,
)

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]

ABSORBED_SUMMARY_SCHEMA_VERSION: Final = 1
ABSORBED_SUMMARY_PAYLOAD_TYPE: Final = "fspm_optics_rex_five_band_absorbed_metrics"
ABSORBED_SUMMARY_FILENAME: Final = "absorbed_photon_summary.json"
ABSORBED_PATCH_FILENAME: Final = "absorbed_patch_metrics.npz"
PAR_BAND_ORDER: Final[tuple[str, ...]] = FIVE_BAND_ORDER[:4]
PHOTON_FLUX_DENSITY_UNITS: Final = "µmol m^-2 s^-1"
PHOTON_FLUX_UNITS: Final = "µmol s^-1"
AREA_UNITS: Final = "m^2"
FRACTION_UNITS: Final = "dimensionless"
ENERGY_CLOSURE_REL_TOLERANCE: Final = 1e-12
ENERGY_CLOSURE_ABS_TOLERANCE: Final = 1e-12
ABSORBED_SCIENTIFIC_CLAIM: Final = (
    "Optically absorbed band photon flux derived from validated Phase 19 "
    "two-sided incident receivers and the persisted Phase 17 Rex A/T/R plan."
)
LOCAL_PARTITION_LIMITATION: Final = (
    "Transmitted and reflected partition terms are local interaction diagnostics; "
    "they are not net escaped or newly intercepted photon totals."
)

NPZ_ARRAY_ORDER: Final[tuple[str, ...]] = (
    "patch_area_m2",
    "leaf_index",
    "front_receiver_index",
    "back_receiver_index",
    "absorptance",
    "transmittance",
    "reflectance",
    "front_incident_pfd",
    "back_incident_pfd",
    "combined_incident_pfd",
    "front_absorbed_pfd",
    "back_absorbed_pfd",
    "combined_absorbed_pfd",
    "incident_flux",
    "front_absorbed_flux",
    "back_absorbed_flux",
    "combined_absorbed_flux",
    "transmitted_flux",
    "reflected_flux",
    "energy_closure_error_flux",
)


class FiveBandAbsorbedMetricsError(RuntimeError):
    """Phase 20 inputs or derived metrics violated a strict contract."""


@dataclass(frozen=True, slots=True)
class SourceNeutralAbsorbedMetricsInput:
    """Minimal source-neutral authority required by the absorption engine."""

    source_family: str
    source_payload_id: str
    optics_payload_id: str
    material_plan_id: str
    plant_id: str
    receiver_identity: str
    patch_count: int
    receiver_count: int
    ordered_receiver_ids: tuple[str, ...]
    band_coefficients: tuple[tuple[str, AtrCoefficients], ...]

    def __post_init__(self) -> None:
        for name in (
            "source_family",
            "source_payload_id",
            "optics_payload_id",
            "material_plan_id",
            "plant_id",
            "receiver_identity",
        ):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty.")
        if self.patch_count != 512 or self.receiver_count != 1024:
            raise ValueError("source-neutral absorption requires 512 patches and 1024 receivers.")
        if len(self.ordered_receiver_ids) != self.receiver_count or len(
            set(self.ordered_receiver_ids)
        ) != self.receiver_count:
            raise ValueError("source-neutral receiver identities are incomplete or duplicated.")
        if tuple(name for name, _ in self.band_coefficients) != FIVE_BAND_ORDER:
            raise ValueError("source-neutral absorption coefficients must preserve fixed band order.")
        for band_id, coefficients in self.band_coefficients:
            _fixed_band(band_id)
            _assert_close(
                coefficients.absorptance
                + coefficients.transmittance
                + coefficients.reflectance,
                1.0,
                f"{band_id} source-neutral A/T/R closure",
            )


def adapt_rex_five_band_absorption_input(
    plan: RexFiveBandTransportPlan,
) -> SourceNeutralAbsorbedMetricsInput:
    """Adapt the existing SMD plan without changing its public payloads."""

    return SourceNeutralAbsorbedMetricsInput(
        source_family="smd",
        source_payload_id=plan.source_model_id,
        optics_payload_id=plan.material_policy_id,
        material_plan_id=plan.material_plan_json_sha256,
        plant_id=plan.plant_id,
        receiver_identity=plan.receiver_text_sha256,
        patch_count=plan.patch_count,
        receiver_count=plan.receiver_count,
        ordered_receiver_ids=plan.ordered_receiver_ids,
        band_coefficients=tuple(
            (band.band_id, band.material.coefficients) for band in plan.band_plans
        ),
    )


@dataclass(frozen=True, slots=True)
class ReceiverPatchPair:
    leaf_id: str
    patch_id: str
    area_m2: float
    front_receiver_id: str
    back_receiver_id: str
    front_receiver_index: int
    back_receiver_index: int

    def __post_init__(self) -> None:
        if not self.leaf_id or not self.patch_id:
            raise ValueError("receiver pair leaf_id and patch_id must be non-empty.")
        _positive("area_m2", self.area_m2)
        _non_negative_integer("front_receiver_index", self.front_receiver_index)
        _non_negative_integer("back_receiver_index", self.back_receiver_index)
        if self.front_receiver_index == self.back_receiver_index:
            raise ValueError("front and back receiver indices must differ.")
        if not self.front_receiver_id.endswith("_front"):
            raise ValueError("front receiver identity must end in _front.")
        if not self.back_receiver_id.endswith("_back"):
            raise ValueError("back receiver identity must end in _back.")

    def to_dict(self) -> dict[str, str | int | float]:
        return {
            "leaf_id": self.leaf_id,
            "patch_id": self.patch_id,
            "area_m2": self.area_m2,
            "front_receiver_id": self.front_receiver_id,
            "back_receiver_id": self.back_receiver_id,
            "front_receiver_index": self.front_receiver_index,
            "back_receiver_index": self.back_receiver_index,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReceiverPatchPair":
        return cls(
            leaf_id=_string(payload, "leaf_id"),
            patch_id=_string(payload, "patch_id"),
            area_m2=_number(payload, "area_m2"),
            front_receiver_id=_string(payload, "front_receiver_id"),
            back_receiver_id=_string(payload, "back_receiver_id"),
            front_receiver_index=_integer(payload, "front_receiver_index"),
            back_receiver_index=_integer(payload, "back_receiver_index"),
        )


@dataclass(frozen=True, slots=True)
class PatchBandAbsorbedMetrics:
    band_id: str
    leaf_id: str
    patch_id: str
    area_m2: float
    front_incident_pfd: float
    back_incident_pfd: float
    combined_incident_pfd: float
    front_absorbed_pfd: float
    back_absorbed_pfd: float
    combined_absorbed_pfd: float
    front_absorbed_flux: float
    back_absorbed_flux: float
    combined_absorbed_flux: float
    incident_flux: float
    transmitted_flux: float
    reflected_flux: float
    absorptance: float
    transmittance: float
    reflectance: float
    absorption_fraction: float | None
    energy_closure_error_flux: float

    def __post_init__(self) -> None:
        _fixed_band(self.band_id)
        _positive("area_m2", self.area_m2)
        for name in (
            "front_incident_pfd",
            "back_incident_pfd",
            "combined_incident_pfd",
            "front_absorbed_pfd",
            "back_absorbed_pfd",
            "combined_absorbed_pfd",
            "front_absorbed_flux",
            "back_absorbed_flux",
            "combined_absorbed_flux",
            "incident_flux",
            "transmitted_flux",
            "reflected_flux",
        ):
            _non_negative(name, getattr(self, name))
        for name in ("absorptance", "transmittance", "reflectance"):
            _fraction(name, getattr(self, name))
        _optional_fraction("absorption_fraction", self.absorption_fraction)
        _finite("energy_closure_error_flux", self.energy_closure_error_flux)
        _assert_close(
            self.front_incident_pfd + self.back_incident_pfd,
            self.combined_incident_pfd,
            "front/back incident PFD closure",
        )
        _assert_close(
            self.front_absorbed_flux + self.back_absorbed_flux,
            self.combined_absorbed_flux,
            "front/back absorbed flux closure",
        )
        _assert_close(
            self.combined_incident_pfd * self.area_m2,
            self.incident_flux,
            "patch incident PFD-to-flux conversion",
        )
        _assert_close(
            self.front_incident_pfd * self.absorptance,
            self.front_absorbed_pfd,
            "front absorbed PFD formula",
        )
        _assert_close(
            self.back_incident_pfd * self.absorptance,
            self.back_absorbed_pfd,
            "back absorbed PFD formula",
        )
        _assert_close(
            self.front_absorbed_pfd + self.back_absorbed_pfd,
            self.combined_absorbed_pfd,
            "front/back absorbed PFD closure",
        )
        _assert_close(
            self.front_absorbed_pfd * self.area_m2,
            self.front_absorbed_flux,
            "front absorbed PFD-to-flux conversion",
        )
        _assert_close(
            self.back_absorbed_pfd * self.area_m2,
            self.back_absorbed_flux,
            "back absorbed PFD-to-flux conversion",
        )
        _assert_close(
            self.combined_absorbed_pfd * self.area_m2,
            self.combined_absorbed_flux,
            "patch absorbed PFD-to-flux conversion",
        )
        _assert_close(
            self.incident_flux * self.transmittance,
            self.transmitted_flux,
            "patch transmitted partition formula",
        )
        _assert_close(
            self.incident_flux * self.reflectance,
            self.reflected_flux,
            "patch reflected partition formula",
        )
        _assert_optional_ratio(
            self.absorption_fraction,
            self.combined_absorbed_flux,
            self.incident_flux,
            "patch absorption_fraction",
        )
        _assert_energy_closure(
            self.incident_flux,
            self.combined_absorbed_flux,
            self.transmitted_flux,
            self.reflected_flux,
        )


@dataclass(frozen=True, slots=True)
class AggregatedBandAbsorbedMetrics:
    band_id: str
    physical_area_m2: float
    incident_flux: float
    absorbed_flux: float
    transmitted_flux: float
    reflected_flux: float
    front_absorbed_flux: float
    back_absorbed_flux: float
    area_weighted_incident_pfd: float
    area_weighted_absorbed_pfd: float
    absorptance: float
    transmittance: float
    reflectance: float
    absorption_fraction: float | None
    energy_closure_error_flux: float

    def __post_init__(self) -> None:
        _fixed_band(self.band_id)
        _positive("physical_area_m2", self.physical_area_m2)
        for name in (
            "incident_flux",
            "absorbed_flux",
            "transmitted_flux",
            "reflected_flux",
            "front_absorbed_flux",
            "back_absorbed_flux",
            "area_weighted_incident_pfd",
            "area_weighted_absorbed_pfd",
        ):
            _non_negative(name, getattr(self, name))
        for name in ("absorptance", "transmittance", "reflectance"):
            _fraction(name, getattr(self, name))
        _optional_fraction("absorption_fraction", self.absorption_fraction)
        _finite("energy_closure_error_flux", self.energy_closure_error_flux)
        _assert_close(
            self.front_absorbed_flux + self.back_absorbed_flux,
            self.absorbed_flux,
            "aggregated front/back absorbed flux closure",
        )
        _assert_close(
            self.incident_flux / self.physical_area_m2,
            self.area_weighted_incident_pfd,
            "aggregated area-weighted incident PFD",
        )
        _assert_close(
            self.absorbed_flux / self.physical_area_m2,
            self.area_weighted_absorbed_pfd,
            "aggregated area-weighted absorbed PFD",
        )
        _assert_close(
            self.absorptance + self.transmittance + self.reflectance,
            1.0,
            "aggregated A/T/R coefficient closure",
        )
        _assert_optional_ratio(
            self.absorption_fraction,
            self.absorbed_flux,
            self.incident_flux,
            "aggregated absorption_fraction",
        )
        if self.incident_flux > 0.0:
            _assert_close(
                self.absorption_fraction,
                self.absorptance,
                "aggregated absorption fraction and material absorptance",
            )
        _assert_close(
            self.incident_flux * self.absorptance,
            self.absorbed_flux,
            "aggregated absorbed partition formula",
        )
        _assert_close(
            self.incident_flux * self.transmittance,
            self.transmitted_flux,
            "aggregated transmitted partition formula",
        )
        _assert_close(
            self.incident_flux * self.reflectance,
            self.reflected_flux,
            "aggregated reflected partition formula",
        )
        _assert_close(
            self.energy_closure_error_flux,
            self.absorbed_flux
            + self.transmitted_flux
            + self.reflected_flux
            - self.incident_flux,
            "aggregated stored energy closure error",
        )
        _assert_energy_closure(
            self.incident_flux,
            self.absorbed_flux,
            self.transmitted_flux,
            self.reflected_flux,
        )

    @property
    def quantity(self) -> str:
        return (
            "far_red_photon_flux"
            if self.band_id == "far_red"
            else f"{self.band_id}_PAR_band_photon_flux"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "band_id": self.band_id,
            "quantity": self.quantity,
            "physical_area_m2": self.physical_area_m2,
            "incident_photon_flux_umol_s": self.incident_flux,
            "absorbed_photon_flux_umol_s": self.absorbed_flux,
            "transmitted_partition_photon_flux_umol_s": self.transmitted_flux,
            "reflected_partition_photon_flux_umol_s": self.reflected_flux,
            "front_absorbed_photon_flux_umol_s": self.front_absorbed_flux,
            "back_absorbed_photon_flux_umol_s": self.back_absorbed_flux,
            "area_weighted_incident_pfd_umol_m2_s": self.area_weighted_incident_pfd,
            "area_weighted_absorbed_pfd_umol_m2_s": self.area_weighted_absorbed_pfd,
            "absorptance": self.absorptance,
            "transmittance": self.transmittance,
            "reflectance": self.reflectance,
            "absorption_fraction": self.absorption_fraction,
            "energy_closure_error_umol_s": self.energy_closure_error_flux,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AggregatedBandAbsorbedMetrics":
        return cls(
            band_id=_string(payload, "band_id"),
            physical_area_m2=_number(payload, "physical_area_m2"),
            incident_flux=_number(payload, "incident_photon_flux_umol_s"),
            absorbed_flux=_number(payload, "absorbed_photon_flux_umol_s"),
            transmitted_flux=_number(
                payload, "transmitted_partition_photon_flux_umol_s"
            ),
            reflected_flux=_number(payload, "reflected_partition_photon_flux_umol_s"),
            front_absorbed_flux=_number(payload, "front_absorbed_photon_flux_umol_s"),
            back_absorbed_flux=_number(payload, "back_absorbed_photon_flux_umol_s"),
            area_weighted_incident_pfd=_number(
                payload, "area_weighted_incident_pfd_umol_m2_s"
            ),
            area_weighted_absorbed_pfd=_number(
                payload, "area_weighted_absorbed_pfd_umol_m2_s"
            ),
            absorptance=_number(payload, "absorptance"),
            transmittance=_number(payload, "transmittance"),
            reflectance=_number(payload, "reflectance"),
            absorption_fraction=_optional_number(payload, "absorption_fraction"),
            energy_closure_error_flux=_number(
                payload, "energy_closure_error_umol_s"
            ),
        )


@dataclass(frozen=True, slots=True)
class ParAbsorbedMetrics:
    physical_area_m2: float
    incident_flux: float
    absorbed_flux: float
    transmitted_flux: float
    reflected_flux: float
    front_absorbed_flux: float
    back_absorbed_flux: float
    area_weighted_incident_pfd: float
    area_weighted_absorbed_pfd: float
    absorption_fraction: float | None
    energy_closure_error_flux: float

    def __post_init__(self) -> None:
        _positive("physical_area_m2", self.physical_area_m2)
        for name in (
            "incident_flux",
            "absorbed_flux",
            "transmitted_flux",
            "reflected_flux",
            "front_absorbed_flux",
            "back_absorbed_flux",
            "area_weighted_incident_pfd",
            "area_weighted_absorbed_pfd",
        ):
            _non_negative(name, getattr(self, name))
        _optional_fraction("absorption_fraction", self.absorption_fraction)
        _finite("energy_closure_error_flux", self.energy_closure_error_flux)
        _assert_close(
            self.front_absorbed_flux + self.back_absorbed_flux,
            self.absorbed_flux,
            "PAR front/back absorbed flux closure",
        )
        _assert_close(
            self.incident_flux / self.physical_area_m2,
            self.area_weighted_incident_pfd,
            "PAR area-weighted incident PFD",
        )
        _assert_close(
            self.absorbed_flux / self.physical_area_m2,
            self.area_weighted_absorbed_pfd,
            "PAR area-weighted absorbed PFD",
        )
        _assert_optional_ratio(
            self.absorption_fraction,
            self.absorbed_flux,
            self.incident_flux,
            "PAR absorption_fraction",
        )
        _assert_close(
            self.energy_closure_error_flux,
            self.absorbed_flux
            + self.transmitted_flux
            + self.reflected_flux
            - self.incident_flux,
            "PAR stored energy closure error",
        )
        _assert_energy_closure(
            self.incident_flux,
            self.absorbed_flux,
            self.transmitted_flux,
            self.reflected_flux,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "band_ids": list(PAR_BAND_ORDER),
            "quantity": "absorbed_PAR_photon_flux",
            "physical_area_m2": self.physical_area_m2,
            "incident_PAR_photon_flux_umol_s": self.incident_flux,
            "absorbed_PAR_photon_flux_umol_s": self.absorbed_flux,
            "transmitted_partition_PAR_photon_flux_umol_s": self.transmitted_flux,
            "reflected_partition_PAR_photon_flux_umol_s": self.reflected_flux,
            "front_absorbed_PAR_photon_flux_umol_s": self.front_absorbed_flux,
            "back_absorbed_PAR_photon_flux_umol_s": self.back_absorbed_flux,
            "area_weighted_incident_PAR_pfd_umol_m2_s": (
                self.area_weighted_incident_pfd
            ),
            "area_weighted_absorbed_PAR_pfd_umol_m2_s": (
                self.area_weighted_absorbed_pfd
            ),
            "absorption_fraction": self.absorption_fraction,
            "energy_closure_error_umol_s": self.energy_closure_error_flux,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ParAbsorbedMetrics":
        if _string_sequence(payload, "band_ids") != PAR_BAND_ORDER:
            raise ValueError("PAR aggregate must contain blue, green, orange, red only.")
        return cls(
            physical_area_m2=_number(payload, "physical_area_m2"),
            incident_flux=_number(payload, "incident_PAR_photon_flux_umol_s"),
            absorbed_flux=_number(payload, "absorbed_PAR_photon_flux_umol_s"),
            transmitted_flux=_number(
                payload, "transmitted_partition_PAR_photon_flux_umol_s"
            ),
            reflected_flux=_number(
                payload, "reflected_partition_PAR_photon_flux_umol_s"
            ),
            front_absorbed_flux=_number(
                payload, "front_absorbed_PAR_photon_flux_umol_s"
            ),
            back_absorbed_flux=_number(
                payload, "back_absorbed_PAR_photon_flux_umol_s"
            ),
            area_weighted_incident_pfd=_number(
                payload, "area_weighted_incident_PAR_pfd_umol_m2_s"
            ),
            area_weighted_absorbed_pfd=_number(
                payload, "area_weighted_absorbed_PAR_pfd_umol_m2_s"
            ),
            absorption_fraction=_optional_number(payload, "absorption_fraction"),
            energy_closure_error_flux=_number(
                payload, "energy_closure_error_umol_s"
            ),
        )


@dataclass(frozen=True, slots=True)
class LeafAbsorbedMetrics:
    leaf_id: str
    physical_area_m2: float
    patch_count: int
    bands: tuple[AggregatedBandAbsorbedMetrics, ...]
    par: ParAbsorbedMetrics

    def __post_init__(self) -> None:
        if not self.leaf_id:
            raise ValueError("leaf_id must be non-empty.")
        _positive("physical_area_m2", self.physical_area_m2)
        _positive_integer("patch_count", self.patch_count)
        if tuple(item.band_id for item in self.bands) != FIVE_BAND_ORDER:
            raise ValueError("leaf band metrics must preserve fixed five-band order.")
        if any(
            not math.isclose(
                item.physical_area_m2,
                self.physical_area_m2,
                rel_tol=0.0,
                abs_tol=1e-15,
            )
            for item in self.bands
        ):
            raise ValueError("leaf band metrics must share one physical leaf area.")
        if not math.isclose(
            self.par.physical_area_m2,
            self.physical_area_m2,
            rel_tol=0.0,
            abs_tol=1e-15,
        ) or self.par != _aggregate_par(self.physical_area_m2, self.bands):
            raise ValueError("leaf PAR aggregate does not match its four PAR bands.")

    @property
    def far_red(self) -> AggregatedBandAbsorbedMetrics:
        return self.bands[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "leaf_id": self.leaf_id,
            "physical_leaf_area_m2": self.physical_area_m2,
            "patch_count": self.patch_count,
            "par_bands": {item.band_id: item.to_dict() for item in self.bands[:4]},
            "par_totals": self.par.to_dict(),
            "far_red": self.far_red.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LeafAbsorbedMetrics":
        par_bands = _mapping(payload, "par_bands")
        if tuple(par_bands) != PAR_BAND_ORDER:
            raise ValueError("leaf par_bands must preserve fixed PAR band order.")
        far_red = AggregatedBandAbsorbedMetrics.from_dict(
            _mapping(payload, "far_red")
        )
        return cls(
            leaf_id=_string(payload, "leaf_id"),
            physical_area_m2=_number(payload, "physical_leaf_area_m2"),
            patch_count=_integer(payload, "patch_count"),
            bands=tuple(
                AggregatedBandAbsorbedMetrics.from_dict(
                    _mapping(par_bands, band_id)
                )
                for band_id in PAR_BAND_ORDER
            )
            + (far_red,),
            par=ParAbsorbedMetrics.from_dict(_mapping(payload, "par_totals")),
        )


@dataclass(frozen=True, slots=True)
class WholePlantAbsorbedMetrics:
    plant_id: str
    physical_area_m2: float
    leaf_count: int
    patch_count: int
    bands: tuple[AggregatedBandAbsorbedMetrics, ...]
    par: ParAbsorbedMetrics

    def __post_init__(self) -> None:
        if not self.plant_id:
            raise ValueError("plant_id must be non-empty.")
        _positive("physical_area_m2", self.physical_area_m2)
        _positive_integer("leaf_count", self.leaf_count)
        _positive_integer("patch_count", self.patch_count)
        if tuple(item.band_id for item in self.bands) != FIVE_BAND_ORDER:
            raise ValueError("plant band metrics must preserve fixed five-band order.")
        if any(
            not math.isclose(
                item.physical_area_m2,
                self.physical_area_m2,
                rel_tol=0.0,
                abs_tol=1e-15,
            )
            for item in self.bands
        ) or self.par != _aggregate_par(self.physical_area_m2, self.bands):
            raise ValueError("plant band or PAR metrics do not share one physical area.")

    @property
    def far_red(self) -> AggregatedBandAbsorbedMetrics:
        return self.bands[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plant_id": self.plant_id,
            "total_physical_leaf_area_m2": self.physical_area_m2,
            "leaf_count": self.leaf_count,
            "patch_count": self.patch_count,
            "par_bands": {item.band_id: item.to_dict() for item in self.bands[:4]},
            "par_totals": self.par.to_dict(),
            "far_red": self.far_red.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WholePlantAbsorbedMetrics":
        par_bands = _mapping(payload, "par_bands")
        if tuple(par_bands) != PAR_BAND_ORDER:
            raise ValueError("plant par_bands must preserve fixed PAR band order.")
        return cls(
            plant_id=_string(payload, "plant_id"),
            physical_area_m2=_number(payload, "total_physical_leaf_area_m2"),
            leaf_count=_integer(payload, "leaf_count"),
            patch_count=_integer(payload, "patch_count"),
            bands=tuple(
                AggregatedBandAbsorbedMetrics.from_dict(
                    _mapping(par_bands, band_id)
                )
                for band_id in PAR_BAND_ORDER
            )
            + (
                AggregatedBandAbsorbedMetrics.from_dict(
                    _mapping(payload, "far_red")
                ),
            ),
            par=ParAbsorbedMetrics.from_dict(_mapping(payload, "par_totals")),
        )


@dataclass(frozen=True, slots=True)
class AbsorbedPatchArrays:
    arrays: tuple[tuple[str, NDArray[Any]], ...]

    def __post_init__(self) -> None:
        if tuple(name for name, _array in self.arrays) != NPZ_ARRAY_ORDER:
            raise ValueError("absorbed patch arrays do not match deterministic order.")
        frozen: list[tuple[str, NDArray[Any]]] = []
        for name, value in self.arrays:
            array = np.array(value, copy=True)
            if array.dtype.hasobject:
                raise ValueError(f"NPZ array {name} may not use object dtype.")
            array.setflags(write=False)
            frozen.append((name, array))
        object.__setattr__(self, "arrays", tuple(frozen))

    def as_dict(self) -> dict[str, NDArray[Any]]:
        return dict(self.arrays)


@dataclass(frozen=True, slots=True)
class ComputedAbsorbedMetrics:
    patch_pairs: tuple[ReceiverPatchPair, ...]
    patch_metrics: tuple[PatchBandAbsorbedMetrics, ...]
    leaves: tuple[LeafAbsorbedMetrics, ...]
    plant: WholePlantAbsorbedMetrics
    patch_arrays: AbsorbedPatchArrays


@dataclass(frozen=True, slots=True)
class AbsorbedPhotonSummary:
    workspace_root: Path
    artifact_root: Path
    phase19_summary_path: Path
    phase19_summary_sha256: str
    transport_plan_path: Path
    transport_plan_sha256: str
    material_plan_path: Path
    material_plan_sha256: str
    band_npy_hashes: tuple[tuple[str, str], ...]
    patch_npz_path: Path
    patch_npz_sha256: str
    patch_pairs: tuple[ReceiverPatchPair, ...]
    leaves: tuple[LeafAbsorbedMetrics, ...]
    plant: WholePlantAbsorbedMetrics
    limitations: tuple[str, ...] = FIVE_BAND_LIMITATIONS
    scientific_claim: str = ABSORBED_SCIENTIFIC_CLAIM

    def __post_init__(self) -> None:
        for name in (
            "phase19_summary_sha256",
            "transport_plan_sha256",
            "material_plan_sha256",
            "patch_npz_sha256",
        ):
            _sha256(name, getattr(self, name))
        if tuple(name for name, _digest in self.band_npy_hashes) != FIVE_BAND_ORDER:
            raise ValueError("summary NPY hashes must preserve fixed five-band order.")
        for band_id, digest in self.band_npy_hashes:
            _fixed_band(band_id)
            _sha256(f"{band_id} NPY hash", digest)
        if len(self.patch_pairs) != 512 or len(self.leaves) != 32:
            raise ValueError("absorbed summary requires 512 patches and 32 leaves.")
        if self.limitations != FIVE_BAND_LIMITATIONS:
            raise ValueError("absorbed summary must preserve five-band limitations.")
        if self.artifact_root != self.workspace_root / "rex_five_band":
            raise ValueError("absorbed artifact root must be the five-band workspace.")
        if self.phase19_summary_path != self.artifact_root / FIVE_BAND_SUMMARY_FILENAME:
            raise ValueError("Phase 19 summary path is not deterministic.")
        if self.transport_plan_path != self.artifact_root / "five_band_transport_plan.json":
            raise ValueError("transport plan path is not deterministic.")
        if self.patch_npz_path != self.artifact_root / ABSORBED_PATCH_FILENAME:
            raise ValueError("absorbed patch NPZ path is not deterministic.")
        if len({item.patch_id for item in self.patch_pairs}) != len(self.patch_pairs):
            raise ValueError("absorbed patch order contains duplicate patch identities.")
        if len({item.leaf_id for item in self.leaves}) != len(self.leaves):
            raise ValueError("absorbed leaf summaries contain duplicate identities.")
        if self.plant.patch_count != len(self.patch_pairs) or self.plant.leaf_count != len(
            self.leaves
        ):
            raise ValueError("absorbed summary counts do not match whole-plant metrics.")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": ABSORBED_SUMMARY_SCHEMA_VERSION,
            "payload_type": ABSORBED_SUMMARY_PAYLOAD_TYPE,
            "success": True,
            "scientific_claim": self.scientific_claim,
            "workspace_root": str(self.workspace_root),
            "artifact_root": str(self.artifact_root),
            "band_order": list(FIVE_BAND_ORDER),
            "par_band_order": list(PAR_BAND_ORDER),
            "source_artifacts": {
                "phase19_summary": str(self.phase19_summary_path),
                "phase19_summary_sha256": self.phase19_summary_sha256,
                "transport_plan": str(self.transport_plan_path),
                "transport_plan_sha256": self.transport_plan_sha256,
                "material_plan": str(self.material_plan_path),
                "material_plan_sha256": self.material_plan_sha256,
                "band_npy_sha256": dict(self.band_npy_hashes),
            },
            "output_artifacts": {
                "patch_metrics_npz": str(self.patch_npz_path),
                "patch_metrics_npz_sha256": self.patch_npz_sha256,
                "npz_array_order": list(NPZ_ARRAY_ORDER),
            },
            "counts": {
                "physical_patches": len(self.patch_pairs),
                "receivers": 2 * len(self.patch_pairs),
                "leaves": len(self.leaves),
            },
            "definitions": {
                "combined_incident_pfd": "front_incident_pfd + back_incident_pfd",
                "absorbed_pfd": "absorptance * combined_incident_pfd",
                "absorbed_flux": "absorbed_pfd * physical_patch_area",
                "physical_area_policy": (
                    "one patch area is used once after combining its front and back "
                    "incident hemispheres"
                ),
                "par_policy": "blue + green + orange + red only",
                "scalar_par_policy": "separate and not added to five-band results",
                "far_red_policy": "separate from PAR and never labeled PPFD",
                "energy_partition_policy": LOCAL_PARTITION_LIMITATION,
            },
            "units": {
                "photon_flux_density": PHOTON_FLUX_DENSITY_UNITS,
                "photon_flux": PHOTON_FLUX_UNITS,
                "physical_area": AREA_UNITS,
                "absorptance_and_fractions": FRACTION_UNITS,
            },
            "patch_order": [item.to_dict() for item in self.patch_pairs],
            "leaf_summaries": [item.to_dict() for item in self.leaves],
            "whole_plant_summary": self.plant.to_dict(),
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "AbsorbedPhotonSummary":
        if _integer(payload, "schema_version") != ABSORBED_SUMMARY_SCHEMA_VERSION:
            raise ValueError("unsupported absorbed summary schema_version.")
        if _string(payload, "payload_type") != ABSORBED_SUMMARY_PAYLOAD_TYPE:
            raise ValueError("unexpected absorbed summary payload_type.")
        if payload.get("success") is not True:
            raise ValueError("absorbed summary must record success=true.")
        if _string_sequence(payload, "band_order") != FIVE_BAND_ORDER:
            raise ValueError("absorbed summary five-band order is invalid.")
        if _string_sequence(payload, "par_band_order") != PAR_BAND_ORDER:
            raise ValueError("absorbed summary PAR band order is invalid.")
        source = _mapping(payload, "source_artifacts")
        outputs = _mapping(payload, "output_artifacts")
        raw_hashes = _mapping(source, "band_npy_sha256")
        if set(raw_hashes) != set(FIVE_BAND_ORDER):
            raise ValueError("absorbed summary band NPY hashes are incomplete.")
        raw_pairs = _mapping_sequence(payload, "patch_order")
        raw_leaves = _mapping_sequence(payload, "leaf_summaries")
        raw_limitations = _string_sequence(payload, "limitations")
        counts = _mapping(payload, "counts")
        if counts != {
            "physical_patches": 512,
            "receivers": 1024,
            "leaves": 32,
        }:
            raise ValueError("absorbed summary counts are invalid.")
        definitions = _mapping(payload, "definitions")
        expected_definitions = {
            "combined_incident_pfd": "front_incident_pfd + back_incident_pfd",
            "absorbed_pfd": "absorptance * combined_incident_pfd",
            "absorbed_flux": "absorbed_pfd * physical_patch_area",
            "physical_area_policy": (
                "one patch area is used once after combining its front and back "
                "incident hemispheres"
            ),
            "par_policy": "blue + green + orange + red only",
            "scalar_par_policy": "separate and not added to five-band results",
            "far_red_policy": "separate from PAR and never labeled PPFD",
            "energy_partition_policy": LOCAL_PARTITION_LIMITATION,
        }
        if definitions != expected_definitions:
            raise ValueError("absorbed summary definitions are invalid.")
        if _mapping(payload, "units") != {
            "photon_flux_density": PHOTON_FLUX_DENSITY_UNITS,
            "photon_flux": PHOTON_FLUX_UNITS,
            "physical_area": AREA_UNITS,
            "absorptance_and_fractions": FRACTION_UNITS,
        }:
            raise ValueError("absorbed summary units are invalid.")
        if tuple(outputs.get("npz_array_order", ())) != NPZ_ARRAY_ORDER:
            raise ValueError("absorbed summary NPZ array order is invalid.")
        return cls(
            workspace_root=Path(_string(payload, "workspace_root")),
            artifact_root=Path(_string(payload, "artifact_root")),
            phase19_summary_path=Path(_string(source, "phase19_summary")),
            phase19_summary_sha256=_string(source, "phase19_summary_sha256"),
            transport_plan_path=Path(_string(source, "transport_plan")),
            transport_plan_sha256=_string(source, "transport_plan_sha256"),
            material_plan_path=Path(_string(source, "material_plan")),
            material_plan_sha256=_string(source, "material_plan_sha256"),
            band_npy_hashes=tuple(
                (band_id, _string(raw_hashes, band_id)) for band_id in FIVE_BAND_ORDER
            ),
            patch_npz_path=Path(_string(outputs, "patch_metrics_npz")),
            patch_npz_sha256=_string(outputs, "patch_metrics_npz_sha256"),
            patch_pairs=tuple(ReceiverPatchPair.from_dict(item) for item in raw_pairs),
            leaves=tuple(LeafAbsorbedMetrics.from_dict(item) for item in raw_leaves),
            plant=WholePlantAbsorbedMetrics.from_dict(
                _mapping(payload, "whole_plant_summary")
            ),
            limitations=raw_limitations,
            scientific_claim=_string(payload, "scientific_claim"),
        )


@dataclass(frozen=True, slots=True)
class RexFiveBandAbsorbedMetricsResult:
    plan: RexFiveBandTransportPlan
    material_plan: RexRadianceTransMaterialPlan
    computed: ComputedAbsorbedMetrics
    summary: AbsorbedPhotonSummary
    summary_path: Path
    patch_npz_path: Path


def pair_two_sided_receivers(
    samples: Sequence[MeshPatchReceiverSample],
    *,
    expected_patch_count: int = 512,
) -> tuple[ReceiverPatchPair, ...]:
    """Pair explicit side metadata without relying on row parity."""

    _positive_integer("expected_patch_count", expected_patch_count)
    if len(samples) != 2 * expected_patch_count:
        raise FiveBandAbsorbedMetricsError(
            "receiver metadata row count mismatch: expected "
            f"{2 * expected_patch_count}, got {len(samples)}."
        )
    receiver_ids: set[str] = set()
    order: list[str] = []
    grouped: dict[str, dict[str, tuple[int, MeshPatchReceiverSample]]] = {}
    patch_leaf: dict[str, str] = {}
    for index, sample in enumerate(samples):
        if sample.receiver_id in receiver_ids:
            raise FiveBandAbsorbedMetricsError(
                f"duplicate receiver identity: {sample.receiver_id}."
            )
        receiver_ids.add(sample.receiver_id)
        if sample.side not in ("front", "back"):
            raise FiveBandAbsorbedMetricsError(
                f"receiver {sample.receiver_id} has invalid side metadata."
            )
        if sample.patch_id not in grouped:
            grouped[sample.patch_id] = {}
            patch_leaf[sample.patch_id] = sample.leaf_id
            order.append(sample.patch_id)
        elif patch_leaf[sample.patch_id] != sample.leaf_id:
            raise FiveBandAbsorbedMetricsError(
                f"patch {sample.patch_id} spans multiple leaf identities."
            )
        if sample.side in grouped[sample.patch_id]:
            raise FiveBandAbsorbedMetricsError(
                f"patch {sample.patch_id} has duplicate {sample.side} receivers."
            )
        grouped[sample.patch_id][sample.side] = (index, sample)

    pairs: list[ReceiverPatchPair] = []
    for patch_id in order:
        sides = grouped[patch_id]
        if set(sides) != {"front", "back"}:
            raise FiveBandAbsorbedMetricsError(
                f"patch {patch_id} must have exactly one front and one back receiver."
            )
        front_index, front = sides["front"]
        back_index, back = sides["back"]
        if (
            front.leaf_id != back.leaf_id
            or front.patch_id != back.patch_id
            or front.plant_id != back.plant_id
        ):
            raise FiveBandAbsorbedMetricsError(
                f"patch {patch_id} front/back identities do not match."
            )
        front_area = _positive(f"{patch_id} front area", front.area_m2)
        back_area = _positive(f"{patch_id} back area", back.area_m2)
        if not math.isclose(front_area, back_area, rel_tol=0.0, abs_tol=0.0):
            raise FiveBandAbsorbedMetricsError(
                f"patch {patch_id} front/back physical areas do not match."
            )
        pairs.append(
            ReceiverPatchPair(
                leaf_id=front.leaf_id,
                patch_id=patch_id,
                area_m2=front_area,
                front_receiver_id=front.receiver_id,
                back_receiver_id=back.receiver_id,
                front_receiver_index=front_index,
                back_receiver_index=back_index,
            )
        )
    if len(pairs) != expected_patch_count:
        raise FiveBandAbsorbedMetricsError(
            f"physical patch count mismatch: expected {expected_patch_count}, got {len(pairs)}."
        )
    return tuple(pairs)


def validate_incident_receiver_array(
    value: object,
    *,
    band_id: str,
    expected_receiver_count: int = 1024,
) -> FloatArray:
    """Return one finite nonnegative numeric receiver vector as float64."""

    _fixed_band(band_id)
    try:
        array = np.asarray(value)
    except (TypeError, ValueError) as exc:
        raise FiveBandAbsorbedMetricsError(
            f"{band_id} receiver array must be numeric."
        ) from exc
    if array.shape != (expected_receiver_count,):
        raise FiveBandAbsorbedMetricsError(
            f"{band_id} receiver array shape mismatch: expected "
            f"({expected_receiver_count},), got {array.shape}."
        )
    if not (
        np.issubdtype(array.dtype, np.integer)
        or np.issubdtype(array.dtype, np.floating)
    ) or np.issubdtype(array.dtype, np.bool_):
        raise FiveBandAbsorbedMetricsError(
            f"{band_id} receiver array dtype must be real numeric; got {array.dtype}."
        )
    result = np.asarray(array, dtype=np.float64)
    if not np.all(np.isfinite(result)):
        raise FiveBandAbsorbedMetricsError(
            f"{band_id} receiver array contains non-finite values."
        )
    if np.any(result < 0.0):
        raise FiveBandAbsorbedMetricsError(
            f"{band_id} receiver array contains negative values."
        )
    return result


def compute_five_band_absorbed_metrics(
    plan: RexFiveBandTransportPlan,
    samples: Sequence[MeshPatchReceiverSample],
    incident_by_band: Mapping[str, object],
) -> ComputedAbsorbedMetrics:
    """Compute patch, leaf, and whole-plant absorbed photon metrics."""

    return compute_source_neutral_absorbed_metrics(
        adapt_rex_five_band_absorption_input(plan),
        samples,
        incident_by_band,
    )


def compute_source_neutral_absorbed_metrics(
    source: SourceNeutralAbsorbedMetricsInput,
    samples: Sequence[MeshPatchReceiverSample],
    incident_by_band: Mapping[str, object],
) -> ComputedAbsorbedMetrics:
    """Compute the shared patch, leaf, and whole-plant optical partitions."""

    if tuple(incident_by_band) != FIVE_BAND_ORDER:
        raise FiveBandAbsorbedMetricsError(
            "incident arrays must contain all five bands in fixed order."
        )
    pairs = pair_two_sided_receivers(samples, expected_patch_count=source.patch_count)
    if tuple(sample.receiver_id for sample in samples) != source.ordered_receiver_ids:
        raise FiveBandAbsorbedMetricsError(
            "receiver metadata order does not match the executed transport plan."
        )
    arrays = tuple(
        validate_incident_receiver_array(
            incident_by_band[band_id],
            band_id=band_id,
            expected_receiver_count=source.receiver_count,
        )
        for band_id in FIVE_BAND_ORDER
    )
    area = np.asarray([pair.area_m2 for pair in pairs], dtype=np.float64)
    front_indices = np.asarray(
        [pair.front_receiver_index for pair in pairs], dtype=np.int64
    )
    back_indices = np.asarray(
        [pair.back_receiver_index for pair in pairs], dtype=np.int64
    )
    front = np.column_stack(
        [array[front_indices] for array in arrays]
    ).astype(np.float64, copy=False)
    back = np.column_stack(
        [array[back_indices] for array in arrays]
    ).astype(np.float64, copy=False)
    combined = front + back
    coefficients = tuple(item for _band_id, item in source.band_coefficients)
    absorptance = np.asarray(
        [item.absorptance for item in coefficients], dtype=np.float64
    )
    transmittance = np.asarray(
        [item.transmittance for item in coefficients], dtype=np.float64
    )
    reflectance = np.asarray(
        [item.reflectance for item in coefficients], dtype=np.float64
    )
    front_absorbed_pfd = front * absorptance
    back_absorbed_pfd = back * absorptance
    combined_absorbed_pfd = combined * absorptance
    area_column = area[:, np.newaxis]
    incident_flux = combined * area_column
    front_absorbed_flux = front_absorbed_pfd * area_column
    back_absorbed_flux = back_absorbed_pfd * area_column
    absorbed_flux = combined_absorbed_pfd * area_column
    transmitted_flux = incident_flux * transmittance
    reflected_flux = incident_flux * reflectance
    closure_error = (
        absorbed_flux + transmitted_flux + reflected_flux - incident_flux
    )
    if not np.allclose(
        absorbed_flux + transmitted_flux + reflected_flux,
        incident_flux,
        rtol=ENERGY_CLOSURE_REL_TOLERANCE,
        atol=ENERGY_CLOSURE_ABS_TOLERANCE,
    ):
        raise FiveBandAbsorbedMetricsError(
            "patch A/T/R energy partition does not close to incident flux."
        )

    leaf_ids: list[str] = []
    leaf_index_by_id: dict[str, int] = {}
    patch_leaf_index: list[int] = []
    for pair in pairs:
        if pair.leaf_id not in leaf_index_by_id:
            leaf_index_by_id[pair.leaf_id] = len(leaf_ids)
            leaf_ids.append(pair.leaf_id)
        patch_leaf_index.append(leaf_index_by_id[pair.leaf_id])
    leaf_index = np.asarray(patch_leaf_index, dtype=np.int64)

    patch_metrics: list[PatchBandAbsorbedMetrics] = []
    for patch_index, pair in enumerate(pairs):
        for band_index, band_id in enumerate(FIVE_BAND_ORDER):
            incident = float(incident_flux[patch_index, band_index])
            absorbed = float(absorbed_flux[patch_index, band_index])
            patch_metrics.append(
                PatchBandAbsorbedMetrics(
                    band_id=band_id,
                    leaf_id=pair.leaf_id,
                    patch_id=pair.patch_id,
                    area_m2=pair.area_m2,
                    front_incident_pfd=float(front[patch_index, band_index]),
                    back_incident_pfd=float(back[patch_index, band_index]),
                    combined_incident_pfd=float(combined[patch_index, band_index]),
                    front_absorbed_pfd=float(
                        front_absorbed_pfd[patch_index, band_index]
                    ),
                    back_absorbed_pfd=float(
                        back_absorbed_pfd[patch_index, band_index]
                    ),
                    combined_absorbed_pfd=float(
                        combined_absorbed_pfd[patch_index, band_index]
                    ),
                    front_absorbed_flux=float(
                        front_absorbed_flux[patch_index, band_index]
                    ),
                    back_absorbed_flux=float(
                        back_absorbed_flux[patch_index, band_index]
                    ),
                    combined_absorbed_flux=absorbed,
                    incident_flux=incident,
                    transmitted_flux=float(
                        transmitted_flux[patch_index, band_index]
                    ),
                    reflected_flux=float(reflected_flux[patch_index, band_index]),
                    absorptance=float(absorptance[band_index]),
                    transmittance=float(transmittance[band_index]),
                    reflectance=float(reflectance[band_index]),
                    absorption_fraction=_ratio(absorbed, incident),
                    energy_closure_error_flux=float(
                        closure_error[patch_index, band_index]
                    ),
                )
            )

    leaves: list[LeafAbsorbedMetrics] = []
    for leaf_id in leaf_ids:
        indices = np.flatnonzero(leaf_index == leaf_index_by_id[leaf_id])
        leaf_area = float(area[indices].sum())
        bands = tuple(
            _aggregate_band(
                band_index,
                leaf_area,
                indices,
                incident_flux,
                absorbed_flux,
                transmitted_flux,
                reflected_flux,
                front_absorbed_flux,
                back_absorbed_flux,
                absorptance,
                transmittance,
                reflectance,
            )
            for band_index in range(len(FIVE_BAND_ORDER))
        )
        leaves.append(
            LeafAbsorbedMetrics(
                leaf_id=leaf_id,
                physical_area_m2=leaf_area,
                patch_count=len(indices),
                bands=bands,
                par=_aggregate_par(leaf_area, bands),
            )
        )

    all_indices = np.arange(len(pairs), dtype=np.int64)
    total_area = float(area.sum())
    plant_bands = tuple(
        _aggregate_band(
            band_index,
            total_area,
            all_indices,
            incident_flux,
            absorbed_flux,
            transmitted_flux,
            reflected_flux,
            front_absorbed_flux,
            back_absorbed_flux,
            absorptance,
            transmittance,
            reflectance,
        )
        for band_index in range(len(FIVE_BAND_ORDER))
    )
    plant = WholePlantAbsorbedMetrics(
        plant_id=source.plant_id,
        physical_area_m2=total_area,
        leaf_count=len(leaves),
        patch_count=len(pairs),
        bands=plant_bands,
        par=_aggregate_par(total_area, plant_bands),
    )
    patch_arrays = AbsorbedPatchArrays(
        arrays=(
            ("patch_area_m2", area),
            ("leaf_index", leaf_index),
            ("front_receiver_index", front_indices),
            ("back_receiver_index", back_indices),
            ("absorptance", absorptance),
            ("transmittance", transmittance),
            ("reflectance", reflectance),
            ("front_incident_pfd", front),
            ("back_incident_pfd", back),
            ("combined_incident_pfd", combined),
            ("front_absorbed_pfd", front_absorbed_pfd),
            ("back_absorbed_pfd", back_absorbed_pfd),
            ("combined_absorbed_pfd", combined_absorbed_pfd),
            ("incident_flux", incident_flux),
            ("front_absorbed_flux", front_absorbed_flux),
            ("back_absorbed_flux", back_absorbed_flux),
            ("combined_absorbed_flux", absorbed_flux),
            ("transmitted_flux", transmitted_flux),
            ("reflected_flux", reflected_flux),
            ("energy_closure_error_flux", closure_error),
        )
    )
    return ComputedAbsorbedMetrics(
        patch_pairs=pairs,
        patch_metrics=tuple(patch_metrics),
        leaves=tuple(leaves),
        plant=plant,
        patch_arrays=patch_arrays,
    )


def calculate_rex_five_band_absorbed_metrics(
    workspace: str | Path,
) -> RexFiveBandAbsorbedMetricsResult:
    """Validate Phase 19 artifacts, compute absorption, and write Phase 20 outputs."""

    root = Path(workspace).expanduser().resolve()
    artifact_root = root / "rex_five_band"
    plan_path = artifact_root / "five_band_transport_plan.json"
    phase19_summary_path = artifact_root / FIVE_BAND_SUMMARY_FILENAME
    summary_path = artifact_root / ABSORBED_SUMMARY_FILENAME
    patch_npz_path = artifact_root / ABSORBED_PATCH_FILENAME
    try:
        plan = read_rex_five_band_transport_plan_json(plan_path)
    except (FileNotFoundError, ValueError) as exc:
        raise FiveBandAbsorbedMetricsError(
            f"invalid Phase 18 transport plan: {exc}"
        ) from exc
    if plan.workspace != root or plan.output_root != artifact_root:
        raise FiveBandAbsorbedMetricsError(
            "Phase 18 plan workspace identity does not match the requested workspace."
        )
    plan_hash = _sha256_file(plan_path)
    if plan_hash != _sha256_text(format_rex_five_band_transport_plan_json(plan)):
        raise FiveBandAbsorbedMetricsError(
            "Phase 18 transport plan is not its deterministic typed serialization."
        )
    phase19 = _load_json_object(
        phase19_summary_path, label="Phase 19 receiver summary"
    )
    _validate_phase19_summary(phase19, plan, plan_hash, phase19_summary_path)
    try:
        material_plan = read_rex_radiance_trans_material_plan_json(
            plan.material_plan_path
        )
    except (FileNotFoundError, ValueError) as exc:
        raise FiveBandAbsorbedMetricsError(
            f"invalid persisted Phase 17 material plan: {exc}"
        ) from exc
    material_hash = _sha256_file(plan.material_plan_path)
    _validate_material_authority(material_plan, plan, material_hash, phase19)
    samples = _reconstruct_receiver_metadata(plan)
    incident_arrays, band_hashes = _load_phase19_arrays(phase19, plan, samples)
    computed = compute_five_band_absorbed_metrics(plan, samples, incident_arrays)
    npz_bytes = format_absorbed_patch_metrics_npz(computed.patch_arrays)
    npz_hash = _sha256_bytes(npz_bytes)
    summary = AbsorbedPhotonSummary(
        workspace_root=root,
        artifact_root=artifact_root,
        phase19_summary_path=phase19_summary_path,
        phase19_summary_sha256=_sha256_file(phase19_summary_path),
        transport_plan_path=plan_path,
        transport_plan_sha256=plan_hash,
        material_plan_path=plan.material_plan_path,
        material_plan_sha256=material_hash,
        band_npy_hashes=band_hashes,
        patch_npz_path=patch_npz_path,
        patch_npz_sha256=npz_hash,
        patch_pairs=computed.patch_pairs,
        leaves=computed.leaves,
        plant=computed.plant,
        limitations=plan.limitations,
    )
    atomic_write_bytes(patch_npz_path, npz_bytes)
    atomic_write_text(summary_path, format_absorbed_photon_summary_json(summary))
    loaded_arrays = read_absorbed_patch_metrics_npz(patch_npz_path)
    _assert_array_mapping_equal(computed.patch_arrays.as_dict(), loaded_arrays)
    loaded_summary = read_absorbed_photon_summary_json(summary_path)
    if loaded_summary != summary:
        raise FiveBandAbsorbedMetricsError(
            "absorbed summary failed deterministic typed round-trip validation."
        )
    if _sha256_file(patch_npz_path) != npz_hash:
        raise FiveBandAbsorbedMetricsError(
            "absorbed patch NPZ hash changed during artifact write."
        )
    return RexFiveBandAbsorbedMetricsResult(
        plan=plan,
        material_plan=material_plan,
        computed=computed,
        summary=summary,
        summary_path=summary_path,
        patch_npz_path=patch_npz_path,
    )


def format_absorbed_photon_summary_json(summary: AbsorbedPhotonSummary) -> str:
    return json.dumps(summary.to_payload(), indent=2, sort_keys=True) + "\n"


def read_absorbed_photon_summary_json(
    path: str | Path,
) -> AbsorbedPhotonSummary:
    payload = _load_json_object(Path(path), label="absorbed photon summary")
    try:
        return AbsorbedPhotonSummary.from_payload(payload)
    except ValueError as exc:
        raise FiveBandAbsorbedMetricsError(
            f"invalid absorbed photon summary: {exc}"
        ) from exc


def format_absorbed_patch_metrics_npz(arrays: AbsorbedPatchArrays) -> bytes:
    """Serialize deterministic numeric NPY members inside a fixed-metadata ZIP."""

    output = BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for name, array in arrays.arrays:
            member = BytesIO()
            np.save(member, array, allow_pickle=False)
            info = zipfile.ZipInfo(
                filename=f"{name}.npy",
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            archive.writestr(info, member.getvalue())
    return output.getvalue()


def read_absorbed_patch_metrics_npz(
    path: str | Path,
) -> dict[str, NDArray[Any]]:
    source = Path(path)
    try:
        with np.load(source, allow_pickle=False) as archive:
            if tuple(archive.files) != NPZ_ARRAY_ORDER:
                raise FiveBandAbsorbedMetricsError(
                    "absorbed patch NPZ members are incomplete or out of order."
                )
            arrays = {name: np.array(archive[name], copy=True) for name in archive.files}
    except FileNotFoundError as exc:
        raise FiveBandAbsorbedMetricsError(
            f"absorbed patch NPZ not found: {source}"
        ) from exc
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise FiveBandAbsorbedMetricsError(
            f"absorbed patch NPZ is malformed: {source}"
        ) from exc
    if any(array.dtype.hasobject for array in arrays.values()):
        raise FiveBandAbsorbedMetricsError("absorbed patch NPZ contains object data.")
    vector_names = NPZ_ARRAY_ORDER[:4]
    coefficient_names = NPZ_ARRAY_ORDER[4:7]
    matrix_names = NPZ_ARRAY_ORDER[7:]
    if any(arrays[name].shape != (512,) for name in vector_names):
        raise FiveBandAbsorbedMetricsError("absorbed patch NPZ patch vectors are invalid.")
    if any(arrays[name].shape != (5,) for name in coefficient_names):
        raise FiveBandAbsorbedMetricsError(
            "absorbed patch NPZ coefficient vectors are invalid."
        )
    if any(arrays[name].shape != (512, 5) for name in matrix_names):
        raise FiveBandAbsorbedMetricsError("absorbed patch NPZ metric matrices are invalid.")
    if any(
        not (
            np.issubdtype(array.dtype, np.integer)
            or np.issubdtype(array.dtype, np.floating)
        )
        for array in arrays.values()
    ):
        raise FiveBandAbsorbedMetricsError("absorbed patch NPZ must be numeric.")
    for name, array in arrays.items():
        if not np.all(np.isfinite(array)):
            raise FiveBandAbsorbedMetricsError(
                f"absorbed patch NPZ array {name} contains non-finite values."
            )
    return arrays


def _aggregate_band(
    band_index: int,
    physical_area: float,
    indices: IntArray,
    incident: FloatArray,
    absorbed: FloatArray,
    transmitted: FloatArray,
    reflected: FloatArray,
    front_absorbed: FloatArray,
    back_absorbed: FloatArray,
    absorptance: FloatArray,
    transmittance: FloatArray,
    reflectance: FloatArray,
) -> AggregatedBandAbsorbedMetrics:
    incident_flux = float(incident[indices, band_index].sum())
    absorbed_flux = float(absorbed[indices, band_index].sum())
    transmitted_flux = float(transmitted[indices, band_index].sum())
    reflected_flux = float(reflected[indices, band_index].sum())
    return AggregatedBandAbsorbedMetrics(
        band_id=FIVE_BAND_ORDER[band_index],
        physical_area_m2=physical_area,
        incident_flux=incident_flux,
        absorbed_flux=absorbed_flux,
        transmitted_flux=transmitted_flux,
        reflected_flux=reflected_flux,
        front_absorbed_flux=float(front_absorbed[indices, band_index].sum()),
        back_absorbed_flux=float(back_absorbed[indices, band_index].sum()),
        area_weighted_incident_pfd=incident_flux / physical_area,
        area_weighted_absorbed_pfd=absorbed_flux / physical_area,
        absorptance=float(absorptance[band_index]),
        transmittance=float(transmittance[band_index]),
        reflectance=float(reflectance[band_index]),
        absorption_fraction=_ratio(absorbed_flux, incident_flux),
        energy_closure_error_flux=(
            absorbed_flux + transmitted_flux + reflected_flux - incident_flux
        ),
    )


def _aggregate_par(
    physical_area: float,
    bands: Sequence[AggregatedBandAbsorbedMetrics],
) -> ParAbsorbedMetrics:
    if tuple(item.band_id for item in bands) != FIVE_BAND_ORDER:
        raise FiveBandAbsorbedMetricsError("PAR aggregation requires fixed band order.")
    par = bands[:4]
    incident = math.fsum(item.incident_flux for item in par)
    absorbed = math.fsum(item.absorbed_flux for item in par)
    transmitted = math.fsum(item.transmitted_flux for item in par)
    reflected = math.fsum(item.reflected_flux for item in par)
    return ParAbsorbedMetrics(
        physical_area_m2=physical_area,
        incident_flux=incident,
        absorbed_flux=absorbed,
        transmitted_flux=transmitted,
        reflected_flux=reflected,
        front_absorbed_flux=math.fsum(item.front_absorbed_flux for item in par),
        back_absorbed_flux=math.fsum(item.back_absorbed_flux for item in par),
        area_weighted_incident_pfd=incident / physical_area,
        area_weighted_absorbed_pfd=absorbed / physical_area,
        absorption_fraction=_ratio(absorbed, incident),
        energy_closure_error_flux=absorbed + transmitted + reflected - incident,
    )


def _validate_phase19_summary(
    summary: Mapping[str, Any],
    plan: RexFiveBandTransportPlan,
    plan_hash: str,
    summary_path: Path,
) -> None:
    if summary.get("schema_version") != FIVE_BAND_NATIVE_RUN_SCHEMA_VERSION:
        raise FiveBandAbsorbedMetricsError("unsupported Phase 19 summary schema.")
    if summary.get("payload_type") != FIVE_BAND_NATIVE_RUN_PAYLOAD_TYPE:
        raise FiveBandAbsorbedMetricsError("unexpected Phase 19 summary payload type.")
    if summary.get("success") is not True:
        raise FiveBandAbsorbedMetricsError("Phase 19 summary does not record success=true.")
    if tuple(summary.get("band_order", ())) != FIVE_BAND_ORDER:
        raise FiveBandAbsorbedMetricsError("Phase 19 summary band order is invalid.")
    if summary.get("workspace_root") != str(plan.workspace) or summary.get(
        "artifact_root"
    ) != str(plan.output_root):
        raise FiveBandAbsorbedMetricsError("Phase 19 workspace identity mismatch.")
    counts = _mapping(summary, "receiver_counts")
    expected_counts = {
        "all": plan.receiver_count,
        "front": plan.patch_count,
        "back": plan.patch_count,
        "physical_patches": plan.patch_count,
    }
    if any(counts.get(key) != value for key, value in expected_counts.items()):
        raise FiveBandAbsorbedMetricsError("Phase 19 receiver counts are invalid.")
    identity = _mapping(summary, "transport_plan_identity")
    expected_identity = {
        "payload_type": FIVE_BAND_PLAN_PAYLOAD_TYPE,
        "schema_version": FIVE_BAND_PLAN_SCHEMA_VERSION,
        "manifest_path": str(plan.manifest_path),
        "manifest_sha256": plan_hash,
        "layout_sha256": plan.layout_sha256,
        "geometry_sha256": plan.geometry_sha256,
        "receiver_text_sha256": plan.receiver_text_sha256,
        "source_model_id": plan.source_model_id,
        "source_model_json_sha256": plan.source_model_json_sha256,
        "material_policy_id": plan.material_policy_id,
        "material_plan_json_sha256": plan.material_plan_json_sha256,
    }
    if any(identity.get(key) != value for key, value in expected_identity.items()):
        raise FiveBandAbsorbedMetricsError("Phase 19 transport-plan identity mismatch.")
    shared = _mapping(summary, "shared_input_hashes")
    expected_shared = {
        "transport_plan_sha256": plan_hash,
        "source_model_json_sha256": _sha256_file(plan.source_model_path),
        "material_plan_json_sha256": _sha256_file(plan.material_plan_path),
        "room_sha256": _sha256_file(plan.room_path),
        "receiver_text_sha256": _sha256_file(plan.receiver_path),
    }
    if any(shared.get(key) != value for key, value in expected_shared.items()):
        raise FiveBandAbsorbedMetricsError("Phase 19 shared input identity mismatch.")
    bands = summary.get("bands")
    if not isinstance(bands, list) or tuple(
        item.get("band_id") if isinstance(item, Mapping) else None for item in bands
    ) != FIVE_BAND_ORDER:
        raise FiveBandAbsorbedMetricsError("Phase 19 band records are invalid.")
    if not summary_path.is_file():
        raise FiveBandAbsorbedMetricsError("Phase 19 summary is missing.")
    for record, band in zip(bands, plan.band_plans, strict=True):
        record_paths = _mapping(record, "paths")
        if record_paths != {
            "octree": str(band.paths.octree_path),
            "raw_rgb": str(band.paths.rgb_output_path),
            "decoded_npy": str(band.paths.decoded_pfd_path),
        }:
            raise FiveBandAbsorbedMetricsError(
                f"{band.band_id} Phase 19 artifact paths differ from the plan."
            )
        inputs = _mapping(_mapping(record, "hashes"), "inputs")
        actual_inputs = {
            "emitters_rad_sha256": _sha256_file(band.paths.emitter_path),
            "rex_plant_rad_sha256": _sha256_file(band.paths.plant_path),
        }
        planned_inputs = {
            "emitters_rad_sha256": band.emitter_text_sha256,
            "rex_plant_rad_sha256": band.plant_text_sha256,
        }
        if inputs != actual_inputs or actual_inputs != planned_inputs:
            raise FiveBandAbsorbedMetricsError(
                f"{band.band_id} Phase 19 materialized input identity mismatch."
            )


def _validate_material_authority(
    material_plan: RexRadianceTransMaterialPlan,
    plan: RexFiveBandTransportPlan,
    material_hash: str,
    phase19: Mapping[str, Any],
) -> None:
    if material_hash != plan.material_plan_json_sha256:
        raise FiveBandAbsorbedMetricsError(
            "persisted Phase 17 material-plan hash differs from Phase 18."
        )
    identity = _mapping(phase19, "transport_plan_identity")
    if identity.get("material_plan_json_sha256") != material_hash:
        raise FiveBandAbsorbedMetricsError(
            "Phase 19 material-plan identity differs from the persisted plan."
        )
    if material_plan.policy_id != plan.material_policy_id:
        raise FiveBandAbsorbedMetricsError("material policy identity mismatch.")
    for band in plan.band_plans:
        try:
            accepted = material_plan.material(band.band_id)
        except KeyError as exc:
            raise FiveBandAbsorbedMetricsError(
                f"persisted material plan is missing {band.band_id}."
            ) from exc
        if (
            accepted.material_identifier != band.material.material_identifier
            or accepted.source_interval.coefficients != band.material.coefficients
            or accepted.parameters != band.material.parameters
            or abs(accepted.reconstruction.closure_error)
            > WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE
        ):
            raise FiveBandAbsorbedMetricsError(
                f"{band.band_id} material binding or A/T/R reconstruction mismatch."
            )


def _reconstruct_receiver_metadata(
    plan: RexFiveBandTransportPlan,
) -> tuple[MeshPatchReceiverSample, ...]:
    plant = generate_rex_butterhead_plant(RexPlantConfig(seed=plan.plant_seed))
    if _sha256_text(export_plant_mesh_to_radiance(plant)) != plan.geometry_sha256:
        raise FiveBandAbsorbedMetricsError("reconstructed Rex geometry identity mismatch.")
    samples = build_two_sided_patch_receivers(
        plant, normal_offset_m=plan.normal_offset_m
    )
    if tuple(sample.receiver_id for sample in samples) != plan.ordered_receiver_ids:
        raise FiveBandAbsorbedMetricsError("receiver ordering identity mismatch.")
    receiver_text = receiver_sample_input_text(item.to_dict() for item in samples)
    if _sha256_text(receiver_text) != plan.receiver_text_sha256:
        raise FiveBandAbsorbedMetricsError("receiver PTS identity mismatch.")
    pairs = pair_two_sided_receivers(samples, expected_patch_count=plan.patch_count)
    if tuple(pair.patch_id for pair in pairs) != tuple(
        patch.patch_id for patch in plant.patches
    ):
        raise FiveBandAbsorbedMetricsError("physical patch ordering identity mismatch.")
    if not math.isclose(
        math.fsum(pair.area_m2 for pair in pairs),
        plant.one_sided_area_m2,
        rel_tol=1e-12,
        abs_tol=1e-15,
    ):
        raise FiveBandAbsorbedMetricsError(
            "physical patch areas do not close to one-sided plant leaf area."
        )
    return samples


def _load_phase19_arrays(
    phase19: Mapping[str, Any],
    plan: RexFiveBandTransportPlan,
    samples: Sequence[MeshPatchReceiverSample],
) -> tuple[dict[str, FloatArray], tuple[tuple[str, str], ...]]:
    records = phase19.get("bands")
    if not isinstance(records, list):
        raise FiveBandAbsorbedMetricsError("Phase 19 summary bands must be a list.")
    pairs = pair_two_sided_receivers(samples, expected_patch_count=plan.patch_count)
    front = np.asarray([pair.front_receiver_index for pair in pairs], dtype=np.int64)
    back = np.asarray([pair.back_receiver_index for pair in pairs], dtype=np.int64)
    weights = np.asarray([pair.area_m2 for pair in pairs], dtype=np.float64)
    record_by_band = {
        str(record.get("band_id")): record
        for record in records
        if isinstance(record, Mapping)
    }
    loaded: dict[str, FloatArray] = {}
    hashes: list[tuple[str, str]] = []
    for band in plan.band_plans:
        record = record_by_band.get(band.band_id)
        if record is None:
            raise FiveBandAbsorbedMetricsError(
                f"Phase 19 summary is missing {band.band_id}."
            )
        paths = _mapping(record, "paths")
        if paths.get("decoded_npy") != str(band.paths.decoded_pfd_path):
            raise FiveBandAbsorbedMetricsError(
                f"{band.band_id} NPY path differs from the transport plan."
            )
        if not band.paths.decoded_pfd_path.is_file():
            raise FiveBandAbsorbedMetricsError(
                f"{band.band_id} receiver NPY is missing: {band.paths.decoded_pfd_path}"
            )
        outputs = _mapping(_mapping(record, "hashes"), "outputs")
        expected_hash = outputs.get("receiver_band_pfd_npy_sha256")
        if not isinstance(expected_hash, str):
            raise FiveBandAbsorbedMetricsError(
                f"{band.band_id} Phase 19 NPY hash is missing."
            )
        actual_hash = _sha256_file(band.paths.decoded_pfd_path)
        if actual_hash != expected_hash:
            raise FiveBandAbsorbedMetricsError(
                f"{band.band_id} receiver NPY hash mismatch."
            )
        try:
            raw = np.load(band.paths.decoded_pfd_path, allow_pickle=False)
        except (OSError, ValueError) as exc:
            raise FiveBandAbsorbedMetricsError(
                f"{band.band_id} receiver NPY is malformed."
            ) from exc
        array = validate_incident_receiver_array(
            raw, band_id=band.band_id, expected_receiver_count=plan.receiver_count
        )
        _validate_phase19_band_metrics(
            band.band_id,
            record,
            array,
            front,
            back,
            weights,
            band.expected_result_units,
        )
        loaded[band.band_id] = array
        hashes.append((band.band_id, actual_hash))
    return loaded, tuple(hashes)


def _validate_phase19_band_metrics(
    band_id: str,
    record: Mapping[str, Any],
    array: FloatArray,
    front: IntArray,
    back: IntArray,
    patch_weights: FloatArray,
    expected_units: str,
) -> None:
    metrics = _mapping(record, "metrics")
    expected_quantity = (
        "far_red_photon_flux_density"
        if band_id == "far_red"
        else f"{band_id}_PAR_band_photon_flux_density"
    )
    if metrics.get("band_id") != band_id or metrics.get("quantity") != expected_quantity:
        raise FiveBandAbsorbedMetricsError(f"{band_id} Phase 19 metric identity mismatch.")
    if metrics.get("units") != expected_units:
        raise FiveBandAbsorbedMetricsError(f"{band_id} Phase 19 metric units mismatch.")
    expected = {
        "receiver_count": len(array),
        "front_receiver_count": len(front),
        "back_receiver_count": len(back),
        "min_incident_band_pfd": float(array.min()),
        "max_incident_band_pfd": float(array.max()),
        "mean_incident_band_pfd": float(array.mean()),
        "front_mean_incident_band_pfd": float(array[front].mean()),
        "back_mean_incident_band_pfd": float(array[back].mean()),
        "area_weighted_mean_incident_band_pfd": float(
            np.average(
                np.concatenate((array[front], array[back])),
                weights=np.concatenate((patch_weights, patch_weights)),
            )
        ),
    }
    for name, value in expected.items():
        recorded = metrics.get(name)
        if isinstance(value, int):
            valid = recorded == value
        else:
            valid = isinstance(recorded, int | float) and math.isclose(
                float(recorded), value, rel_tol=1e-12, abs_tol=1e-12
            )
        if not valid:
            raise FiveBandAbsorbedMetricsError(
                f"{band_id} Phase 19 metric {name} does not match its NPY."
            )


def _assert_array_mapping_equal(
    expected: Mapping[str, NDArray[Any]],
    actual: Mapping[str, NDArray[Any]],
) -> None:
    if tuple(expected) != tuple(actual):
        raise FiveBandAbsorbedMetricsError("NPZ round-trip array names changed.")
    for name in expected:
        if expected[name].dtype != actual[name].dtype or not np.array_equal(
            expected[name], actual[name]
        ):
            raise FiveBandAbsorbedMetricsError(
                f"NPZ round-trip changed array {name}."
            )


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FiveBandAbsorbedMetricsError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FiveBandAbsorbedMetricsError(f"{label} is malformed: {path}") from exc
    if not isinstance(payload, dict):
        raise FiveBandAbsorbedMetricsError(f"{label} root must be an object.")
    return payload


def _assert_energy_closure(
    incident: float,
    absorbed: float,
    transmitted: float,
    reflected: float,
) -> None:
    if not math.isclose(
        absorbed + transmitted + reflected,
        incident,
        rel_tol=ENERGY_CLOSURE_REL_TOLERANCE,
        abs_tol=ENERGY_CLOSURE_ABS_TOLERANCE,
    ):
        raise ValueError("absorbed + transmitted + reflected must close to incident.")


def _assert_close(left: float, right: float, label: str) -> None:
    if not math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"{label} failed: {left:.17g} != {right:.17g}.")


def _assert_optional_ratio(
    value: float | None,
    numerator: float,
    denominator: float,
    label: str,
) -> None:
    expected = _ratio(numerator, denominator)
    if expected is None:
        if value is not None:
            raise ValueError(f"{label} must be null when incident flux is zero.")
        return
    if value is None or not math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"{label} does not match absorbed / incident flux.")


def _ratio(numerator: float, denominator: float) -> float | None:
    return None if denominator == 0.0 else numerator / denominator


def _fixed_band(band_id: str) -> str:
    if band_id not in FIVE_BAND_ORDER:
        raise ValueError(f"unexpected five-band identity: {band_id!r}.")
    return band_id


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be finite and numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite and numeric.")
    return number


def _non_negative(name: str, value: object) -> float:
    number = _finite(name, value)
    if number < 0.0:
        raise ValueError(f"{name} must be non-negative.")
    return number


def _positive(name: str, value: object) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be positive.")
    return number


def _fraction(name: str, value: object) -> float:
    number = _finite(name, value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be a fraction in [0, 1].")
    return number


def _optional_fraction(name: str, value: object) -> float | None:
    return None if value is None else _fraction(name, value)


def _positive_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _non_negative_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer.")
    return value


def _sha256(name: str, value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest.")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except FileNotFoundError as exc:
        raise FiveBandAbsorbedMetricsError(f"required artifact not found: {path}") from exc


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object.")
    return value


def _mapping_sequence(
    payload: Mapping[str, Any], key: str
) -> tuple[Mapping[str, Any], ...]:
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
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings.")
    return tuple(value)


def _integer(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer.")
    return value


def _number(payload: Mapping[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{key} must be finite.")
    return number


def _optional_number(payload: Mapping[str, Any], key: str) -> float | None:
    return None if payload.get(key) is None else _number(payload, key)
