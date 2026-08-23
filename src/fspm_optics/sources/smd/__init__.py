"""Strict explicit-component Proposed LED/SMD source model."""

from .profile import (
    DEFAULT_SMD_COMPONENTS,
    SMD_NORMALIZATION_POLICY,
    NATIVE_PROPOSED_SPECTRAL_BASIS_ID,
    SMD_SOURCE_MODEL_ID,
    SMD_SOURCE_MODEL_LIMITATIONS,
    SmdComponent,
    SmdSourceModel,
    build_nominal_smd_source_model,
    format_smd_source_model_json,
    read_smd_source_model_json,
    write_smd_source_model_json,
)
from .spectral_control import (
    CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_ID,
    PROPOSED_CONVENTIONAL_LED_CONTROL_SOURCE_MODEL_ID,
    build_controlled_conventional_led_proposed_source_model,
    proposed_spectral_basis_payload,
)
from .spd import (
    SMD_RESOURCE_NAMES,
    RelativeRadiantSpd,
    load_smd_spd,
    load_spd_csv,
    smd_resource_path,
)

__all__ = [
    "DEFAULT_SMD_COMPONENTS",
    "CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS_ID",
    "NATIVE_PROPOSED_SPECTRAL_BASIS_ID",
    "PROPOSED_CONVENTIONAL_LED_CONTROL_SOURCE_MODEL_ID",
    "SMD_NORMALIZATION_POLICY",
    "SMD_RESOURCE_NAMES",
    "SMD_SOURCE_MODEL_ID",
    "SMD_SOURCE_MODEL_LIMITATIONS",
    "RelativeRadiantSpd",
    "SmdComponent",
    "SmdSourceModel",
    "build_nominal_smd_source_model",
    "build_controlled_conventional_led_proposed_source_model",
    "format_smd_source_model_json",
    "load_smd_spd",
    "load_spd_csv",
    "read_smd_source_model_json",
    "smd_resource_path",
    "write_smd_source_model_json",
    "proposed_spectral_basis_payload",
]
