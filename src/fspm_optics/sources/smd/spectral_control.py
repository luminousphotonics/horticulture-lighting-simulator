"""Counterfactual relative-spectrum authority for Proposed Stage B transport."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Final, Mapping

from fspm_optics.fixtures.conventional_led.resources import (
    CONVENTIONAL_SPD_RESOURCE_NAME,
    CONVENTIONAL_SPD_SHA256,
)
from fspm_optics.fixtures.conventional_led.spectral import (
    CONVENTIONAL_SPD_NORMALIZATION_POLICY,
    assert_approved_spectral_resource,
    build_conventional_spectral_distribution,
)
from fspm_optics.fixtures.smd.optical_stack import (
    ACCEPTED_FIXTURE_TRANSMISSION,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    INTERNAL_SOURCE_PPE_UMOL_PER_J,
    PROPOSED_FIXTURE_OPTICAL_STACK_ID,
    PROPOSED_MODELED_BAND_TRANSMISSION,
    validate_common_accepted_band_transmission,
)
from fspm_optics.spectral.bands import FIXED_TRANSPORT_BANDS

from .profile import SmdComponentPhotonModel, SmdSourceModel

CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_ID: Final = (
    "conventional_led_control"
)
CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_LABEL: Final = (
    "Conventional LED spectrum (controlled A/B)"
)
PROPOSED_CONVENTIONAL_LED_CONTROL_SOURCE_MODEL_ID: Final = (
    "proposed_conventional_led_spd_completed_aperture_control_v1"
)
CONTROL_BOUNDARY_ID: Final = "proposed_completed_fixture_aperture"
CONTROL_NORMALIZATION_DETAILS: Final = (
    "The byte-verified Conventional LED relative radiant SPD is converted with "
    "relative_spd * wavelength_nm and normalized over 400 <= wavelength_nm < "
    "700. Its PAR fractions and separate far-red/PAR relationship are declared "
    "authoritative at the Proposed completed fixture aperture. The same fractions "
    "partition the calibrated Proposed internal PAR PPE because every modeled band "
    "uses one common accepted fixture transmission."
)
CONTROL_LIMITATIONS: Final[tuple[str, ...]] = (
    "This is a counterfactual spectral control, not the physical Proposed spectrum.",
    "Proposed geometry, layout, angular source, control zones, and power remain active.",
    "Only the byte-verified Conventional LED relative SPD is shared.",
    "No Conventional IES, geometry, power, dimming, multiplier, or writer is imported.",
    "Far-red remains outside PAR, PPE, PAR PPF, and PPFD.",
    "The active Proposed optical stack must remain wavelength-neutral across all modeled bands.",
)


@dataclass(frozen=True, slots=True)
class ControlledConventionalLedRelativeComponent:
    """One shape-only component; its nominal values have no absolute authority."""

    component_id: str = "conventional_led_relative_spd_at_completed_aperture"
    label: str = "Conventional LED relative SPD control"
    count: int = 1
    nominal_package_watts: float = 1.0
    nominal_ppe_umol_per_j: float = 1.0
    spd_resource: str = CONVENTIONAL_SPD_RESOURCE_NAME
    spectral_role: str = "counterfactual_completed_aperture_relative_shape"

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


def build_controlled_conventional_led_proposed_source_model(
    *,
    transmission_by_band: Mapping[str, object] = PROPOSED_MODELED_BAND_TRANSMISSION,
) -> SmdSourceModel:
    """Reuse the approved Conventional LED SPD as a Proposed completed-aperture control."""

    common_transmission = validate_common_accepted_band_transmission(
        transmission_by_band
    )
    distribution = build_conventional_spectral_distribution()
    assert_approved_spectral_resource(distribution)
    if (
        distribution.resource_name != CONVENTIONAL_SPD_RESOURCE_NAME
        or distribution.resource_sha256 != CONVENTIONAL_SPD_SHA256
    ):
        raise ValueError(
            "controlled Proposed spectrum requires the approved Conventional LED SPD."
        )
    fractions = dict(distribution.par_band_photon_fractions) | {
        "far_red": distribution.far_red_relative_to_par
    }
    if tuple(fractions) != tuple(band.band_id for band in FIXED_TRANSPORT_BANDS):
        raise ValueError("controlled Conventional LED spectrum does not cover the fixed bands.")
    if not math.isclose(
        math.fsum(fractions[band.band_id] for band in FIXED_TRANSPORT_BANDS[:4]),
        1.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("controlled Conventional LED PAR fractions do not close to one.")
    component = ControlledConventionalLedRelativeComponent()
    component_model = SmdComponentPhotonModel(
        component=component,
        photon_distribution=distribution.photon_distribution,
        nominal_par_ppf_umol_s=1.0,
        fraction_of_nominal_par_ppf=1.0,
        spd_sha256=distribution.resource_sha256,
    )
    control_provenance = MappingProxyType(
        {
            "counterfactual_spectral_control": True,
            "physical_proposed_spectral_configuration": False,
            "proposed_geometry_layout_angular_and_source_control_active": True,
            "relative_spd_authority": {
                "resource": CONVENTIONAL_SPD_RESOURCE_NAME,
                "sha256": CONVENTIONAL_SPD_SHA256,
                "spectral_distribution_id": distribution.distribution_id,
                "photon_weighting": "relative_spd_times_wavelength",
            },
            "authority_boundary": {
                "id": CONTROL_BOUNDARY_ID,
                "description": "Proposed completed fixture aperture",
                "completed_aperture_par_ppe_umol_per_j": (
                    COMPLETED_APERTURE_PPE_UMOL_PER_J
                ),
                "accepted_fixture_transmission": common_transmission,
                "internal_par_ppe_umol_per_j": INTERNAL_SOURCE_PPE_UMOL_PER_J,
                "far_red_outside_par_anchor": True,
            },
            "optical_stack": {
                "id": PROPOSED_FIXTURE_OPTICAL_STACK_ID,
                "spectrally_neutral_across_modeled_bands": True,
                "accepted_transmission_by_band": {
                    band_id: float(transmission_by_band[band_id])
                    for band_id in transmission_by_band
                },
                "common_accepted_transmission": ACCEPTED_FIXTURE_TRANSMISSION,
            },
        }
    )
    return SmdSourceModel(
        components=(component_model,),
        photon_distribution=distribution.photon_distribution,
        band_photon_fractions_relative_to_par=fractions,
        far_red_relative_to_par=distribution.far_red_relative_to_par,
        resource_hashes={
            CONVENTIONAL_SPD_RESOURCE_NAME: CONVENTIONAL_SPD_SHA256
        },
        source_model_id=PROPOSED_CONVENTIONAL_LED_CONTROL_SOURCE_MODEL_ID,
        normalization_policy=CONVENTIONAL_SPD_NORMALIZATION_POLICY,
        spectral_basis_id=CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_ID,
        spectral_basis_label=CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_LABEL,
        spectral_distribution_id=distribution.distribution_id,
        control_provenance=control_provenance,
        limitations=CONTROL_LIMITATIONS,
        normalization_details=CONTROL_NORMALIZATION_DETAILS,
    )


def proposed_spectral_basis_payload(model: SmdSourceModel) -> dict[str, object]:
    """Return the explicit result/artifact identity for a Proposed basis."""

    payload: dict[str, object] = {
        "id": model.spectral_basis_id,
        "label": model.spectral_basis_label,
        "source_model_id": model.source_model_id,
        "spectral_distribution_id": model.spectral_distribution_id,
        "normalization_policy": model.normalization_policy,
        "resource_hashes": dict(model.resource_hashes),
        "counterfactual_spectral_control": (
            model.spectral_basis_id
            == CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_ID
        ),
        "physical_proposed_spectral_configuration": (
            model.spectral_basis_id
            != CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_ID
        ),
    }
    if model.control_provenance is not None:
        payload["control_provenance"] = dict(model.control_provenance)
    return payload
