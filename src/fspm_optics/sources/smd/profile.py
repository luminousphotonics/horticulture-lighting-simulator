"""Explicit nominal-component SMD photon source model."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Final, Mapping

from fspm_optics.spectral.bands import (
    FIXED_TRANSPORT_BANDS,
    PAR_END_NM_EXCLUSIVE,
    PAR_START_NM,
)
from fspm_optics.spectral.distribution import (
    PhotonDistribution,
    combine_photon_distributions,
    normalize_relative_radiant_spd_to_par_photons,
)

from .spd import (
    SMD_RESOURCE_NAMES,
    SMD_SPD_RESOURCE_NAMES,
    load_smd_spd,
    sha256_resource,
    smd_resource_path,
)

SMD_SOURCE_MODEL_ID: Final = "proposed_led_smd_relative_spectral_mix_v2"
NATIVE_PROPOSED_SPECTRAL_BASIS_ID: Final = "native_proposed"
NATIVE_PROPOSED_SPECTRAL_BASIS_LABEL: Final = "Native Proposed spectrum"
SMD_NORMALIZATION_POLICY: Final = (
    "relative_component_and_spd_mix_only_then_internal_fixture_ppe_anchor"
)
SMD_NORMALIZATION_DETAILS: Final = (
    "relative radiant SPD is converted with relative_spd * wavelength_nm, "
    "normalized over discrete samples satisfying 400 <= wavelength_nm < 700, "
    "then scaled by count * nominal_package_watts * nominal_ppe_umol_per_j "
    "solely to preserve the manuscript relative component/channel mixture; "
    "absolute PAR amplitude is assigned later from the calibrated internal "
    "fixture source PPE"
)

SMD_SOURCE_MODEL_LIMITATIONS: Final[tuple[str, ...]] = (
    "Source SPDs are relative digitized curves, not absolute spectroradiometry.",
    "Datasheet/source provenance for the SPD curves is incomplete.",
    "Spectral shape is fixed with current and temperature.",
    (
        "Nominal component watts and PPE values determine relative channel "
        "balance only; they have no authority over absolute fixture efficacy."
    ),
    "PMMA transmission is treated as spectrally flat for now.",
    "No wavelength-dependent PMMA/PTFE/optic transmission yet.",
    "No thermal spectral shift.",
    "No drive-current spectral shift.",
    (
        "Rex A/T/R data are digitized and remain unvalidated against this "
        "simulator's physical crop environment."
    ),
)


@dataclass(frozen=True, slots=True)
class SmdComponent:
    component_id: str
    label: str
    count: int
    nominal_package_watts: float
    nominal_ppe_umol_per_j: float
    spd_resource: str
    spectral_role: str

    def __post_init__(self) -> None:
        if not self.component_id or not self.label or not self.spectral_role:
            raise ValueError("SMD component identity fields must be non-empty.")
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count <= 0:
            raise ValueError("SMD component count must be a positive integer.")
        for name in ("nominal_package_watts", "nominal_ppe_umol_per_j"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(float(value))
                or float(value) <= 0.0
            ):
                raise ValueError(f"{name} must be finite and positive.")
        if self.spd_resource not in SMD_SPD_RESOURCE_NAMES:
            raise ValueError(
                f"component {self.component_id!r} has an unknown explicit SPD resource."
            )

    @property
    def nominal_par_ppf_umol_s(self) -> float:
        return (
            self.count
            * float(self.nominal_package_watts)
            * float(self.nominal_ppe_umol_per_j)
        )

    def to_dict(self) -> dict[str, str | int | float]:
        return {
            "component_id": self.component_id,
            "label": self.label,
            "count": self.count,
            "nominal_package_watts": self.nominal_package_watts,
            "nominal_ppe_umol_per_j": self.nominal_ppe_umol_per_j,
            "spd_resource": self.spd_resource,
            "spectral_role": self.spectral_role,
        }


DEFAULT_SMD_COMPONENTS: Final[tuple[SmdComponent, ...]] = (
    SmdComponent(
        component_id="warm_white_3000k",
        label="Warm white 3000 K",
        count=52,
        nominal_package_watts=0.68,
        nominal_ppe_umol_per_j=2.73,
        spd_resource="smd_3000k_spd.csv",
        spectral_role="warm_white",
    ),
    SmdComponent(
        component_id="cool_white_5000k",
        label="Cool white 5000 K",
        count=52,
        nominal_package_watts=0.68,
        nominal_ppe_umol_per_j=2.81,
        spd_resource="smd_5000k_spd.csv",
        spectral_role="cool_white",
    ),
    SmdComponent(
        component_id="deep_red_660nm",
        label="Deep red 660 nm",
        count=41,
        nominal_package_watts=0.44,
        nominal_ppe_umol_per_j=4.13,
        spd_resource="smd_660nm_spd.csv",
        spectral_role="deep_red",
    ),
)


@dataclass(frozen=True, slots=True)
class SmdComponentPhotonModel:
    component: Any
    photon_distribution: PhotonDistribution
    nominal_par_ppf_umol_s: float
    fraction_of_nominal_par_ppf: float
    spd_sha256: str


@dataclass(frozen=True, slots=True)
class SmdSourceModel:
    components: tuple[SmdComponentPhotonModel, ...]
    photon_distribution: PhotonDistribution
    band_photon_fractions_relative_to_par: Mapping[str, float]
    far_red_relative_to_par: float
    resource_hashes: Mapping[str, str]
    source_model_id: str = SMD_SOURCE_MODEL_ID
    normalization_policy: str = SMD_NORMALIZATION_POLICY
    spectral_basis_id: str = NATIVE_PROPOSED_SPECTRAL_BASIS_ID
    spectral_basis_label: str = NATIVE_PROPOSED_SPECTRAL_BASIS_LABEL
    spectral_distribution_id: str = SMD_SOURCE_MODEL_ID
    control_provenance: Mapping[str, Any] | None = None
    limitations: tuple[str, ...] = SMD_SOURCE_MODEL_LIMITATIONS
    normalization_details: str = SMD_NORMALIZATION_DETAILS

    def __post_init__(self) -> None:
        for value in (
            self.source_model_id,
            self.normalization_policy,
            self.spectral_basis_id,
            self.spectral_basis_label,
            self.spectral_distribution_id,
        ):
            if not isinstance(value, str) or not value:
                raise ValueError("Proposed spectral source identity is incomplete.")
        if not self.components or not self.resource_hashes:
            raise ValueError("Proposed spectral source authority is incomplete.")
        if self.source_model_id == SMD_SOURCE_MODEL_ID:
            if (
                self.normalization_policy != SMD_NORMALIZATION_POLICY
                or self.spectral_basis_id
                != NATIVE_PROPOSED_SPECTRAL_BASIS_ID
            ):
                raise ValueError("native Proposed spectral identity is inconsistent.")
        elif self.spectral_basis_id == "conventional_led_control":
            from fspm_optics.fixtures.conventional_led.resources import (
                CONVENTIONAL_SPD_RESOURCE_NAME,
                CONVENTIONAL_SPD_SHA256,
            )
            from fspm_optics.fixtures.conventional_led.spectral import (
                CONVENTIONAL_SPD_NORMALIZATION_POLICY,
                build_conventional_spectral_distribution,
            )

            approved_distribution = build_conventional_spectral_distribution()
            approved_fractions = dict(
                approved_distribution.par_band_photon_fractions
            ) | {"far_red": approved_distribution.far_red_relative_to_par}
            from fspm_optics.fixtures.smd.optical_stack import (
                ACCEPTED_FIXTURE_TRANSMISSION,
                COMPLETED_APERTURE_PPE_UMOL_PER_J,
                INTERNAL_SOURCE_PPE_UMOL_PER_J,
                PROPOSED_FIXTURE_OPTICAL_STACK_ID,
                validate_common_accepted_band_transmission,
            )

            boundary = (
                self.control_provenance.get("authority_boundary")
                if isinstance(self.control_provenance, Mapping)
                else None
            )
            optical_stack = (
                self.control_provenance.get("optical_stack")
                if isinstance(self.control_provenance, Mapping)
                else None
            )
            if (
                self.source_model_id
                != "proposed_conventional_led_spd_completed_aperture_control_v1"
                or self.normalization_policy
                != CONVENTIONAL_SPD_NORMALIZATION_POLICY
                or dict(self.resource_hashes)
                != {CONVENTIONAL_SPD_RESOURCE_NAME: CONVENTIONAL_SPD_SHA256}
                or not isinstance(self.control_provenance, Mapping)
                or self.control_provenance.get("counterfactual_spectral_control")
                is not True
                or self.control_provenance.get(
                    "physical_proposed_spectral_configuration"
                )
                is not False
                or self.spectral_distribution_id
                != approved_distribution.distribution_id
                or self.photon_distribution
                != approved_distribution.photon_distribution
                or dict(self.band_photon_fractions_relative_to_par)
                != approved_fractions
                or not isinstance(boundary, Mapping)
                or boundary.get("id")
                != "proposed_completed_fixture_aperture"
                or boundary.get("completed_aperture_par_ppe_umol_per_j")
                != COMPLETED_APERTURE_PPE_UMOL_PER_J
                or boundary.get("accepted_fixture_transmission")
                != ACCEPTED_FIXTURE_TRANSMISSION
                or boundary.get("internal_par_ppe_umol_per_j")
                != INTERNAL_SOURCE_PPE_UMOL_PER_J
                or boundary.get("far_red_outside_par_anchor") is not True
                or not isinstance(optical_stack, Mapping)
                or optical_stack.get("id") != PROPOSED_FIXTURE_OPTICAL_STACK_ID
                or optical_stack.get("spectrally_neutral_across_modeled_bands")
                is not True
            ):
                raise ValueError(
                    "controlled Proposed source requires the approved Conventional LED authority."
                )
            validate_common_accepted_band_transmission(
                optical_stack.get("accepted_transmission_by_band")
            )
        else:
            raise ValueError("unsupported Proposed spectral source identity.")
        if self.spectral_basis_id == "conventional_led_control":
            expected_band_ids = tuple(
                band.band_id for band in FIXED_TRANSPORT_BANDS
            )
            if tuple(self.band_photon_fractions_relative_to_par) != expected_band_ids:
                raise ValueError("Proposed source must contain every fixed band in order.")
            par_sum = math.fsum(
                self.band_photon_fractions_relative_to_par[band_id]
                for band_id in expected_band_ids[:4]
            )
            if (
                not math.isclose(par_sum, 1.0, rel_tol=0.0, abs_tol=1e-12)
                or self.far_red_relative_to_par
                != self.band_photon_fractions_relative_to_par["far_red"]
            ):
                raise ValueError("Proposed source PAR/far-red fractions are inconsistent.")

    @property
    def total_nominal_par_ppf_umol_s(self) -> float:
        return math.fsum(item.nominal_par_ppf_umol_s for item in self.components)

    def to_payload(self) -> dict[str, Any]:
        nominal = {
            item.component.component_id: item.nominal_par_ppf_umol_s
            for item in self.components
        }
        fractions = {
            item.component.component_id: item.fraction_of_nominal_par_ppf
            for item in self.components
        }
        payload: dict[str, Any] = {
            "schema_version": 1,
            "source_model_id": self.source_model_id,
            "normalization_policy": self.normalization_policy,
            "normalization_details": self.normalization_details,
            "raw_component_values_are_absolute_ppe_authority": False,
            "components": [item.component.to_dict() for item in self.components],
            "component_nominal_PAR_PPF": nominal,
            "component_PAR_fractions": fractions,
            "total_nominal_PAR_PPF_umol_s": self.total_nominal_par_ppf_umol_s,
            "band_definitions": [band.to_dict() for band in FIXED_TRANSPORT_BANDS],
            "band_photon_fractions_relative_to_PAR": dict(
                self.band_photon_fractions_relative_to_par
            ),
            "far_red_relative_to_PAR": self.far_red_relative_to_par,
            "par_normalization_wavelength_range": {
                "start_nm": PAR_START_NM,
                "end_nm_exclusive": PAR_END_NM_EXCLUSIVE,
            },
            "resource_hashes": dict(self.resource_hashes),
            "limitations": list(self.limitations),
        }
        if self.spectral_basis_id != NATIVE_PROPOSED_SPECTRAL_BASIS_ID:
            payload["spectral_basis"] = {
                "id": self.spectral_basis_id,
                "label": self.spectral_basis_label,
                "spectral_distribution_id": self.spectral_distribution_id,
            }
            payload["control_provenance"] = dict(self.control_provenance or {})
        return payload


def build_nominal_smd_source_model(
    *,
    data_root: str | Path | None = None,
) -> SmdSourceModel:
    """Build the fixed explicit-component nominal SMD photon source model."""

    total_nominal = math.fsum(
        component.nominal_par_ppf_umol_s for component in DEFAULT_SMD_COMPONENTS
    )
    component_models: list[SmdComponentPhotonModel] = []
    for component in DEFAULT_SMD_COMPONENTS:
        spd = load_smd_spd(component.spd_resource, data_root=data_root)
        normalized = normalize_relative_radiant_spd_to_par_photons(
            spd.wavelength_nm,
            spd.relative_spd,
        )
        nominal = component.nominal_par_ppf_umol_s
        component_models.append(
            SmdComponentPhotonModel(
                component=component,
                photon_distribution=normalized.scaled(nominal),
                nominal_par_ppf_umol_s=nominal,
                fraction_of_nominal_par_ppf=nominal / total_nominal,
                spd_sha256=spd.sha256,
            )
        )
    combined = combine_photon_distributions(
        item.photon_distribution for item in component_models
    )
    combined_par = combined.par_amount
    if not math.isclose(combined_par, total_nominal, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(
            "combined SMD photon distribution does not preserve nominal PAR PPF."
        )
    band_fractions = {
        band.band_id: combined.amount_in_band(band) / combined_par
        for band in FIXED_TRANSPORT_BANDS
    }
    par_fraction_sum = math.fsum(
        value for band_id, value in band_fractions.items() if band_id != "far_red"
    )
    if (
        any(not math.isfinite(value) or value <= 0.0 for value in band_fractions.values())
        or not math.isclose(par_fraction_sum, 1.0, rel_tol=0.0, abs_tol=1e-12)
    ):
        raise ValueError("SMD relative PAR channel fractions must be positive and close to one.")
    resource_hashes = {
        resource_name: sha256_resource(
            smd_resource_path(resource_name, data_root=data_root)
        )
        for resource_name in SMD_RESOURCE_NAMES
    }
    return SmdSourceModel(
        components=tuple(component_models),
        photon_distribution=combined,
        band_photon_fractions_relative_to_par=band_fractions,
        far_red_relative_to_par=band_fractions["far_red"],
        resource_hashes=resource_hashes,
    )


def format_smd_source_model_json(model: SmdSourceModel) -> str:
    return json.dumps(model.to_payload(), indent=2, sort_keys=True) + "\n"


def write_smd_source_model_json(
    path: str | Path,
    model: SmdSourceModel,
) -> Path:
    output = Path(path)
    output.write_text(format_smd_source_model_json(model), encoding="utf-8")
    return output


def read_smd_source_model_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"SMD source model JSON not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"SMD source model JSON is malformed: {source}") from exc
    if not isinstance(payload, dict):
        raise ValueError("SMD source model JSON root must be an object.")
    required = {
        "schema_version",
        "source_model_id",
        "normalization_policy",
        "components",
        "component_nominal_PAR_PPF",
        "component_PAR_fractions",
        "band_photon_fractions_relative_to_PAR",
        "far_red_relative_to_PAR",
        "par_normalization_wavelength_range",
        "resource_hashes",
        "limitations",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(
            "SMD source model JSON is missing required fields: " + ", ".join(missing)
        )
    if payload["schema_version"] != 1:
        raise ValueError("unsupported SMD source model schema_version.")
    return payload
