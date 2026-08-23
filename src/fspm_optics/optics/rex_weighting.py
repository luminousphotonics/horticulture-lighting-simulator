"""Pure nominal-SMD source-weighted Rex leaf A/T/R coefficients.

The implementation consumes only the packaged Rex ``mean_of_treatments``
model columns and the strict Phase 15 nominal SMD photon distribution. It does
not create Radiance materials or execute spectral transport.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Final, Mapping, Sequence

from fspm_optics.optics.profiles import (
    REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,
    REX_LEAF_OPTICS_TREATMENT_ID,
    LeafOpticalProfile,
    load_rex_green_butterhead_mature_leaf_optics_v1,
)
from fspm_optics.sources.smd.profile import (
    SMD_NORMALIZATION_POLICY,
    SMD_SOURCE_MODEL_ID,
    SmdSourceModel,
    build_nominal_smd_source_model,
)
from fspm_optics.sources.smd.spd import SMD_RESOURCE_NAMES
from fspm_optics.spectral.bands import FIXED_TRANSPORT_BANDS
from fspm_optics.spectral.distribution import PhotonDistribution

REX_SOURCE_WEIGHTED_ATR_SCHEMA_VERSION: Final = 1
REX_SOURCE_WEIGHTED_ATR_PAYLOAD_TYPE: Final = (
    "fspm_optics_rex_source_weighted_atr"
)
REX_SOURCE_WEIGHTED_ATR_MODEL_ID: Final = (
    "rex_mean_of_treatments_nominal_smd_source_weighted_atr_v1"
)
REX_CONTROLLED_CONVENTIONAL_LED_WEIGHTED_ATR_MODEL_ID: Final = (
    "rex_mean_of_treatments_proposed_conventional_led_control_weighted_atr_v1"
)
REX_MODEL_WAVELENGTH_CLOSURE_ABS_TOLERANCE: Final = 1e-9
WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE: Final = 1e-12
WAVELENGTH_ALIGNMENT_METHOD: Final = (
    "exact_1_nm_source_to_rex_profile_join_no_interpolation_or_extrapolation"
)
PHOTON_INTEGRATION_METHOD: Final = (
    "discrete_1_nm_source_photon_weighted_sum_on_rex_profile_grid"
)


@dataclass(frozen=True, slots=True)
class RexSourceWeightingInput:
    """Source-neutral photon distribution and immutable provenance for Rex weighting."""

    source_model_id: str
    normalization_policy: str
    resource_hashes: tuple[tuple[str, str], ...]
    photon_distribution: PhotonDistribution

    def __post_init__(self) -> None:
        if not self.source_model_id or not self.normalization_policy:
            raise ValueError("source weighting identity and policy must be non-empty.")
        hashes = tuple((str(name), str(value)) for name, value in self.resource_hashes)
        if hashes != tuple(sorted(hashes)) or len({name for name, _ in hashes}) != len(hashes):
            raise ValueError("source weighting resource hashes must be sorted and unique.")
        if not hashes or any(
            not name
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for name, value in hashes
        ):
            raise ValueError("source weighting resource hashes are invalid.")
        object.__setattr__(self, "resource_hashes", hashes)

    @property
    def photon_distribution_sha256(self) -> str:
        payload = {
            "wavelength_nm": list(self.photon_distribution.wavelength_nm),
            "photon_amount": list(self.photon_distribution.photon_amount),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @property
    def identity(self) -> str:
        payload = {
            "source_model_id": self.source_model_id,
            "normalization_policy": self.normalization_policy,
            "resource_hashes": dict(self.resource_hashes),
            "photon_distribution_sha256": self.photon_distribution_sha256,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"rex-source-weighting-input-v1-{digest}"


@dataclass(frozen=True, slots=True)
class RexSourceWeightedIntervalSet:
    """Source-neutral scalar-PAR and five-band Rex weighting result."""

    source: RexSourceWeightingInput
    rex_profile_id: str
    rex_profile_version: str
    rex_treatment_id: str
    rex_profile_source: str
    rex_data_provenance: str
    rex_validation_status: str
    rex_profile_start_nm: int
    rex_profile_end_nm: int
    max_profile_wavelength_closure_error: float
    positive_source_start_nm: int
    positive_source_end_nm: int
    scalar_par: "SourceWeightedAtrInterval"
    bands: tuple["SourceWeightedAtrInterval", ...]
    unsupported_source_mass_by_interval: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if self.rex_profile_id != REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1:
            raise ValueError("source-neutral Rex weighting profile is invalid.")
        if self.rex_treatment_id != REX_LEAF_OPTICS_TREATMENT_ID:
            raise ValueError("source-neutral Rex weighting requires mean_of_treatments.")
        if (self.rex_profile_start_nm, self.rex_profile_end_nm) != (404, 797):
            raise ValueError("source-neutral Rex weighting requires the 404–797 nm grid.")
        if not 0.0 <= self.max_profile_wavelength_closure_error <= (
            REX_MODEL_WAVELENGTH_CLOSURE_ABS_TOLERANCE
        ):
            raise ValueError("source-neutral Rex profile closure diagnostic is invalid.")
        if not 0 < self.positive_source_start_nm <= self.positive_source_end_nm:
            raise ValueError("positive source wavelength coverage is invalid.")
        if self.scalar_par.interval_id != "scalar_par":
            raise ValueError("source-neutral scalar PAR interval is missing.")
        expected = tuple(band.band_id for band in FIXED_TRANSPORT_BANDS)
        if tuple(item.interval_id for item in self.bands) != expected:
            raise ValueError("source-neutral Rex bands are incomplete or out of order.")
        unsupported = tuple(
            (str(interval_id), float(value))
            for interval_id, value in self.unsupported_source_mass_by_interval
        )
        if tuple(name for name, _ in unsupported) != ("scalar_par", *expected):
            raise ValueError("unsupported source mass diagnostics are out of order.")
        if any(not math.isfinite(value) or value < 0.0 for _, value in unsupported):
            raise ValueError("unsupported source mass must be finite and non-negative.")
        object.__setattr__(self, "unsupported_source_mass_by_interval", unsupported)

    @property
    def identity(self) -> str:
        digest = hashlib.sha256(
            json.dumps(
                self.to_payload(), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        return f"rex-source-weighted-intervals-v1-{digest}"

    def band(self, band_id: str) -> "SourceWeightedAtrInterval":
        for item in self.bands:
            if item.interval_id == band_id:
                return item
        raise KeyError(f"unknown source-neutral Rex band: {band_id!r}")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source": {
                "source_model_id": self.source.source_model_id,
                "normalization_policy": self.source.normalization_policy,
                "resource_hashes": dict(self.source.resource_hashes),
                "photon_distribution_sha256": (
                    self.source.photon_distribution_sha256
                ),
                "positive_wavelength_coverage_nm": {
                    "start": self.positive_source_start_nm,
                    "end_inclusive": self.positive_source_end_nm,
                },
            },
            "rex_profile": {
                "profile_id": self.rex_profile_id,
                "profile_version": self.rex_profile_version,
                "treatment_id": self.rex_treatment_id,
                "source": self.rex_profile_source,
                "data_provenance": self.rex_data_provenance,
                "validation_status": self.rex_validation_status,
                "wavelength_range_nm": {
                    "start": self.rex_profile_start_nm,
                    "end_inclusive": self.rex_profile_end_nm,
                },
                "max_wavelength_closure_error": (
                    self.max_profile_wavelength_closure_error
                ),
            },
            "alignment": {
                "method": WAVELENGTH_ALIGNMENT_METHOD,
                "integration_method": PHOTON_INTEGRATION_METHOD,
                "interpolation_applied": False,
                "extrapolation_applied": False,
                "missing_wavelength_fill_applied": False,
                "unsupported_source_mass_by_interval": dict(
                    self.unsupported_source_mass_by_interval
                ),
            },
            "scalar_par": self.scalar_par.to_dict(),
            "bands": [item.to_dict() for item in self.bands],
        }


@dataclass(frozen=True, slots=True)
class AtrCoefficients:
    absorptance: float
    transmittance: float
    reflectance: float

    def __post_init__(self) -> None:
        for field_name in ("absorptance", "transmittance", "reflectance"):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(f"{field_name} must be a finite fraction in [0, 1].")
            object.__setattr__(self, field_name, float(value))

    @property
    def sum(self) -> float:
        return self.absorptance + self.transmittance + self.reflectance

    def to_dict(self) -> dict[str, float]:
        return {
            "absorptance": self.absorptance,
            "transmittance": self.transmittance,
            "reflectance": self.reflectance,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AtrCoefficients":
        return cls(
            absorptance=_number(payload, "absorptance"),
            transmittance=_number(payload, "transmittance"),
            reflectance=_number(payload, "reflectance"),
        )


@dataclass(frozen=True, slots=True)
class AtrClosureDiagnostics:
    raw_absorptance: float
    raw_transmittance: float
    raw_reflectance: float
    raw_sum: float
    closure_error: float
    normalization_applied: bool

    def __post_init__(self) -> None:
        for field_name in (
            "raw_absorptance",
            "raw_transmittance",
            "raw_reflectance",
            "raw_sum",
            "closure_error",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"{field_name} must be finite.")
            object.__setattr__(self, field_name, float(value))
        if not isinstance(self.normalization_applied, bool):
            raise ValueError("normalization_applied must be boolean.")
        expected_sum = (
            self.raw_absorptance
            + self.raw_transmittance
            + self.raw_reflectance
        )
        if not math.isclose(
            self.raw_sum,
            expected_sum,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError("closure raw_sum does not match raw A/T/R values.")
        if not math.isclose(
            self.closure_error,
            self.raw_sum - 1.0,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError("closure_error must equal raw_sum - 1.")

    def to_dict(self) -> dict[str, float | bool]:
        return {
            "raw_absorptance": self.raw_absorptance,
            "raw_transmittance": self.raw_transmittance,
            "raw_reflectance": self.raw_reflectance,
            "raw_sum": self.raw_sum,
            "closure_error": self.closure_error,
            "normalization_applied": self.normalization_applied,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AtrClosureDiagnostics":
        normalization_applied = payload.get("normalization_applied")
        if not isinstance(normalization_applied, bool):
            raise ValueError("normalization_applied must be boolean.")
        return cls(
            raw_absorptance=_number(payload, "raw_absorptance"),
            raw_transmittance=_number(payload, "raw_transmittance"),
            raw_reflectance=_number(payload, "raw_reflectance"),
            raw_sum=_number(payload, "raw_sum"),
            closure_error=_number(payload, "closure_error"),
            normalization_applied=normalization_applied,
        )


@dataclass(frozen=True, slots=True)
class SourceWeightedAtrInterval:
    interval_id: str
    label: str
    requested_start_nm: int
    requested_end_nm_exclusive: int
    effective_start_nm: int
    effective_end_nm_exclusive: int
    wavelength_count: int
    source_photon_weight_umol_s: float
    coefficients: AtrCoefficients
    closure_diagnostics: AtrClosureDiagnostics

    def __post_init__(self) -> None:
        if not self.interval_id or not self.label:
            raise ValueError("weighted A/T/R interval identity must be non-empty.")
        integer_fields = (
            "requested_start_nm",
            "requested_end_nm_exclusive",
            "effective_start_nm",
            "effective_end_nm_exclusive",
            "wavelength_count",
        )
        if any(
            isinstance(getattr(self, name), bool)
            or not isinstance(getattr(self, name), int)
            for name in integer_fields
        ):
            raise ValueError("weighted A/T/R wavelength metadata must be integer-valued.")
        if not (
            0 < self.requested_start_nm < self.requested_end_nm_exclusive
            and self.requested_start_nm
            <= self.effective_start_nm
            < self.effective_end_nm_exclusive
            <= self.requested_end_nm_exclusive
        ):
            raise ValueError("weighted A/T/R wavelength ranges are invalid.")
        if self.wavelength_count != (
            self.effective_end_nm_exclusive - self.effective_start_nm
        ):
            raise ValueError(
                "weighted A/T/R wavelength_count must describe a complete 1 nm grid."
            )
        if (
            isinstance(self.source_photon_weight_umol_s, bool)
            or not isinstance(self.source_photon_weight_umol_s, int | float)
            or not math.isfinite(float(self.source_photon_weight_umol_s))
            or float(self.source_photon_weight_umol_s) <= 0.0
        ):
            raise ValueError("source photon weight must be finite and positive.")
        object.__setattr__(
            self,
            "source_photon_weight_umol_s",
            float(self.source_photon_weight_umol_s),
        )
        if not math.isclose(
            self.coefficients.sum,
            1.0,
            rel_tol=0.0,
            abs_tol=WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE,
        ):
            raise ValueError(
                f"weighted A/T/R interval {self.interval_id!r} does not close to 1."
            )
        if self.closure_diagnostics.normalization_applied:
            raise ValueError("source-weighted Rex A/T/R must not be renormalized.")
        for coefficient, raw_value in zip(
            (
                self.coefficients.absorptance,
                self.coefficients.transmittance,
                self.coefficients.reflectance,
            ),
            (
                self.closure_diagnostics.raw_absorptance,
                self.closure_diagnostics.raw_transmittance,
                self.closure_diagnostics.raw_reflectance,
            ),
            strict=True,
        ):
            if coefficient != raw_value:
                raise ValueError(
                    "weighted A/T/R coefficients must preserve raw values without "
                    "normalization."
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "interval_id": self.interval_id,
            "label": self.label,
            "requested_wavelength_range": {
                "start_nm": self.requested_start_nm,
                "end_nm_exclusive": self.requested_end_nm_exclusive,
            },
            "effective_wavelength_range": {
                "start_nm": self.effective_start_nm,
                "end_nm_exclusive": self.effective_end_nm_exclusive,
            },
            "wavelength_count": self.wavelength_count,
            "source_photon_weight_umol_s": self.source_photon_weight_umol_s,
            "coefficients": self.coefficients.to_dict(),
            "closure_diagnostics": self.closure_diagnostics.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SourceWeightedAtrInterval":
        requested = _mapping(payload, "requested_wavelength_range")
        effective = _mapping(payload, "effective_wavelength_range")
        return cls(
            interval_id=_string(payload, "interval_id"),
            label=_string(payload, "label"),
            requested_start_nm=_integer(requested, "start_nm"),
            requested_end_nm_exclusive=_integer(requested, "end_nm_exclusive"),
            effective_start_nm=_integer(effective, "start_nm"),
            effective_end_nm_exclusive=_integer(effective, "end_nm_exclusive"),
            wavelength_count=_integer(payload, "wavelength_count"),
            source_photon_weight_umol_s=_number(
                payload,
                "source_photon_weight_umol_s",
            ),
            coefficients=AtrCoefficients.from_dict(_mapping(payload, "coefficients")),
            closure_diagnostics=AtrClosureDiagnostics.from_dict(
                _mapping(payload, "closure_diagnostics")
            ),
        )


@dataclass(frozen=True, slots=True)
class RexSourceWeightedAtrPayload:
    source_model_id: str
    source_normalization_policy: str
    source_resource_hashes: tuple[tuple[str, str], ...]
    rex_profile_id: str
    rex_profile_version: str
    rex_treatment_id: str
    rex_profile_source: str
    rex_data_provenance: str
    rex_validation_status: str
    rex_profile_start_nm: int
    rex_profile_end_nm: int
    max_profile_wavelength_closure_error: float
    scalar_par: SourceWeightedAtrInterval
    bands: tuple[SourceWeightedAtrInterval, ...]
    model_id: str = REX_SOURCE_WEIGHTED_ATR_MODEL_ID

    def __post_init__(self) -> None:
        for field_name in (
            "source_model_id",
            "source_normalization_policy",
            "rex_profile_id",
            "rex_profile_version",
            "rex_treatment_id",
            "rex_profile_source",
            "rex_data_provenance",
            "rex_validation_status",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must be non-empty.")
        from fspm_optics.sources.smd.spectral_control import (
            PROPOSED_CONVENTIONAL_LED_CONTROL_SOURCE_MODEL_ID,
        )
        from fspm_optics.fixtures.conventional_led.resources import (
            CONVENTIONAL_SPD_RESOURCE_NAME,
            CONVENTIONAL_SPD_SHA256,
        )
        from fspm_optics.fixtures.conventional_led.spectral import (
            CONVENTIONAL_SPD_NORMALIZATION_POLICY,
        )

        native = self.source_model_id == SMD_SOURCE_MODEL_ID
        controlled = self.source_model_id == PROPOSED_CONVENTIONAL_LED_CONTROL_SOURCE_MODEL_ID
        if not native and not controlled:
            raise ValueError("Rex weighting Proposed source model is unsupported.")
        if native and (
            self.source_normalization_policy != SMD_NORMALIZATION_POLICY
            or self.model_id != REX_SOURCE_WEIGHTED_ATR_MODEL_ID
        ):
            raise ValueError("Rex weighting requires nominal component PAR photon output.")
        if controlled and (
            self.model_id != REX_CONTROLLED_CONVENTIONAL_LED_WEIGHTED_ATR_MODEL_ID
            or self.source_normalization_policy
            != CONVENTIONAL_SPD_NORMALIZATION_POLICY
        ):
            raise ValueError("controlled Conventional LED Rex weighting identity is invalid.")
        if self.rex_profile_id != REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1:
            raise ValueError("Rex weighting payload has an incompatible profile_id.")
        if self.rex_treatment_id != REX_LEAF_OPTICS_TREATMENT_ID:
            raise ValueError("Rex weighting payload must use mean_of_treatments.")
        if (self.rex_profile_start_nm, self.rex_profile_end_nm) != (404, 797):
            raise ValueError("Rex weighting payload requires the 404–797 nm profile grid.")
        if (
            isinstance(self.max_profile_wavelength_closure_error, bool)
            or not isinstance(
                self.max_profile_wavelength_closure_error,
                int | float,
            )
            or not math.isfinite(float(self.max_profile_wavelength_closure_error))
            or float(self.max_profile_wavelength_closure_error) < 0.0
            or float(self.max_profile_wavelength_closure_error)
            > REX_MODEL_WAVELENGTH_CLOSURE_ABS_TOLERANCE
        ):
            raise ValueError("Rex profile wavelength closure diagnostic is invalid.")
        object.__setattr__(
            self,
            "max_profile_wavelength_closure_error",
            float(self.max_profile_wavelength_closure_error),
        )
        expected_band_ids = tuple(band.band_id for band in FIXED_TRANSPORT_BANDS)
        if tuple(item.interval_id for item in self.bands) != expected_band_ids:
            raise ValueError("Rex weighting payload must contain all five fixed bands.")
        if self.scalar_par.interval_id != "scalar_par":
            raise ValueError("scalar_par interval is missing from Rex weighting payload.")
        if (
            self.scalar_par.requested_start_nm,
            self.scalar_par.requested_end_nm_exclusive,
            self.scalar_par.effective_start_nm,
            self.scalar_par.effective_end_nm_exclusive,
        ) != (400, 700, 404, 700):
            raise ValueError("scalar PAR must use requested 400–699 and effective 404–699 nm.")
        for item, band in zip(self.bands, FIXED_TRANSPORT_BANDS, strict=True):
            expected_effective_start = 404 if band.band_id == "blue" else band.start_nm
            if (
                item.requested_start_nm,
                item.requested_end_nm_exclusive,
                item.effective_start_nm,
                item.effective_end_nm_exclusive,
            ) != (
                band.start_nm,
                band.end_nm_exclusive,
                expected_effective_start,
                band.end_nm_exclusive,
            ):
                raise ValueError(
                    f"Rex weighting band {band.band_id!r} has incompatible endpoints."
                )
        hash_names = [name for name, _value in self.source_resource_hashes]
        if hash_names != sorted(hash_names) or len(hash_names) != len(set(hash_names)):
            raise ValueError("source_resource_hashes must be sorted and unique.")
        if native and hash_names != sorted(SMD_RESOURCE_NAMES):
            raise ValueError("source_resource_hashes must identify all SMD resources.")
        if controlled and self.source_resource_hashes != (
            (CONVENTIONAL_SPD_RESOURCE_NAME, CONVENTIONAL_SPD_SHA256),
        ):
            raise ValueError(
                "controlled Rex weighting requires the exact approved Conventional LED SPD."
            )
        if any(not name or not value for name, value in self.source_resource_hashes):
            raise ValueError("source resource hash names and values must be non-empty.")

    def band(self, band_id: str) -> SourceWeightedAtrInterval:
        for item in self.bands:
            if item.interval_id == band_id:
                return item
        raise KeyError(f"unknown Rex source-weighted band: {band_id!r}")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": REX_SOURCE_WEIGHTED_ATR_SCHEMA_VERSION,
            "payload_type": REX_SOURCE_WEIGHTED_ATR_PAYLOAD_TYPE,
            "model_id": self.model_id,
            "source": {
                "source_model_id": self.source_model_id,
                "normalization_policy": self.source_normalization_policy,
                "resource_hashes": dict(self.source_resource_hashes),
            },
            "rex_profile": {
                "profile_id": self.rex_profile_id,
                "profile_version": self.rex_profile_version,
                "treatment_id": self.rex_treatment_id,
                "source": self.rex_profile_source,
                "data_provenance": self.rex_data_provenance,
                "validation_status": self.rex_validation_status,
                "wavelength_range": {
                    "start_nm": self.rex_profile_start_nm,
                    "end_nm": self.rex_profile_end_nm,
                },
                "coefficient_columns": [
                    "absorptance_fraction_model",
                    "transmittance_fraction_model",
                    "reflectance_fraction_model",
                ],
                "max_wavelength_closure_error": (
                    self.max_profile_wavelength_closure_error
                ),
            },
            "alignment": {
                "method": WAVELENGTH_ALIGNMENT_METHOD,
                "integration_method": PHOTON_INTEGRATION_METHOD,
                "profile_start_truncation_policy": (
                    "policy_explicit_400_to_403_nm_omission_for_rex_profile"
                ),
                "interpolation_applied": False,
                "extrapolation_applied": False,
                "missing_wavelength_fill_applied": False,
            },
            "scalar_par": self.scalar_par.to_dict(),
            "bands": {item.interval_id: item.to_dict() for item in self.bands},
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "RexSourceWeightedAtrPayload":
        if _integer(payload, "schema_version") != REX_SOURCE_WEIGHTED_ATR_SCHEMA_VERSION:
            raise ValueError("unsupported Rex source-weighted A/T/R schema_version.")
        if _string(payload, "payload_type") != REX_SOURCE_WEIGHTED_ATR_PAYLOAD_TYPE:
            raise ValueError("unexpected Rex source-weighted A/T/R payload_type.")
        model_id = _string(payload, "model_id")
        if model_id not in {
            REX_SOURCE_WEIGHTED_ATR_MODEL_ID,
            REX_CONTROLLED_CONVENTIONAL_LED_WEIGHTED_ATR_MODEL_ID,
        }:
            raise ValueError("unexpected Rex source-weighted A/T/R model_id.")
        source = _mapping(payload, "source")
        profile = _mapping(payload, "rex_profile")
        alignment = _mapping(payload, "alignment")
        if _string(alignment, "method") != WAVELENGTH_ALIGNMENT_METHOD:
            raise ValueError("unexpected Rex A/T/R wavelength alignment method.")
        if _string(alignment, "integration_method") != PHOTON_INTEGRATION_METHOD:
            raise ValueError("unexpected Rex A/T/R photon integration method.")
        if alignment.get("profile_start_truncation_policy") != (
            "policy_explicit_400_to_403_nm_omission_for_rex_profile"
        ):
            raise ValueError("unexpected Rex profile start truncation policy.")
        for key in (
            "interpolation_applied",
            "extrapolation_applied",
            "missing_wavelength_fill_applied",
        ):
            if alignment.get(key) is not False:
                raise ValueError(f"{key} must be false for strict Rex alignment.")
        wavelength_range = _mapping(profile, "wavelength_range")
        if profile.get("coefficient_columns") != [
            "absorptance_fraction_model",
            "transmittance_fraction_model",
            "reflectance_fraction_model",
        ]:
            raise ValueError("Rex A/T/R payload must identify the three model columns.")
        raw_hashes = _mapping(source, "resource_hashes")
        raw_bands = _mapping(payload, "bands")
        expected_band_ids = tuple(band.band_id for band in FIXED_TRANSPORT_BANDS)
        if set(raw_bands.keys()) != set(expected_band_ids):
            raise ValueError("serialized Rex A/T/R bands must contain all fixed bands.")
        source_hashes: list[tuple[str, str]] = []
        for name, value in raw_hashes.items():
            if not isinstance(name, str) or not name:
                raise ValueError("source resource hash names must be non-empty strings.")
            if not isinstance(value, str) or not value:
                raise ValueError("source resource hash values must be non-empty strings.")
            source_hashes.append((name, value))
        return cls(
            source_model_id=_string(source, "source_model_id"),
            source_normalization_policy=_string(source, "normalization_policy"),
            source_resource_hashes=tuple(sorted(source_hashes)),
            rex_profile_id=_string(profile, "profile_id"),
            rex_profile_version=_string(profile, "profile_version"),
            rex_treatment_id=_string(profile, "treatment_id"),
            rex_profile_source=_string(profile, "source"),
            rex_data_provenance=_string(profile, "data_provenance"),
            rex_validation_status=_string(profile, "validation_status"),
            rex_profile_start_nm=_integer(wavelength_range, "start_nm"),
            rex_profile_end_nm=_integer(wavelength_range, "end_nm"),
            max_profile_wavelength_closure_error=_number(
                profile,
                "max_wavelength_closure_error",
            ),
            scalar_par=SourceWeightedAtrInterval.from_dict(
                _mapping(payload, "scalar_par")
            ),
            bands=tuple(
                SourceWeightedAtrInterval.from_dict(
                    _mapping(raw_bands, band_id)
                )
                for band_id in expected_band_ids
            ),
            model_id=model_id,
        )


def compute_rex_source_weighted_interval_set(
    source: RexSourceWeightingInput,
    *,
    profile: LeafOpticalProfile | None = None,
) -> RexSourceWeightedIntervalSet:
    """Compute the shared Rex interval set for any explicit photon source."""

    resolved_profile = (
        load_rex_green_butterhead_mature_leaf_optics_v1()
        if profile is None
        else profile
    )
    max_profile_error = _validate_rex_model_profile(resolved_profile)
    scalar = compute_source_weighted_atr_interval(
        resolved_profile,
        source.photon_distribution,
        interval_id="scalar_par",
        label="Scalar PAR",
        requested_start_nm=400,
        requested_end_nm_exclusive=700,
        allow_profile_start_truncation=True,
    )
    bands = tuple(
        compute_source_weighted_atr_interval(
            resolved_profile,
            source.photon_distribution,
            interval_id=band.band_id,
            label=band.label,
            requested_start_nm=band.start_nm,
            requested_end_nm_exclusive=band.end_nm_exclusive,
            allow_profile_start_truncation=(band.band_id == "blue"),
        )
        for band in FIXED_TRANSPORT_BANDS
    )
    positive_wavelengths = tuple(
        int(wavelength)
        for wavelength, amount in zip(
            source.photon_distribution.wavelength_nm,
            source.photon_distribution.photon_amount,
            strict=True,
        )
        if amount > 0.0
    )
    if not positive_wavelengths:
        raise ValueError("source photon distribution has no positive wavelengths.")
    intervals = (scalar, *bands)
    unsupported: list[tuple[str, float]] = []
    for interval in intervals:
        requested_mass = source.photon_distribution.amount_between(
            interval.requested_start_nm,
            interval.requested_end_nm_exclusive,
        )
        difference = requested_mass - interval.source_photon_weight_umol_s
        if abs(difference) <= WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE:
            difference = 0.0
        if difference < 0.0:
            raise ValueError("effective Rex source mass exceeds requested interval mass.")
        unsupported.append((interval.interval_id, difference))
    return RexSourceWeightedIntervalSet(
        source=source,
        rex_profile_id=resolved_profile.profile_id,
        rex_profile_version=resolved_profile.profile_version,
        rex_treatment_id=resolved_profile.treatment_id,
        rex_profile_source=resolved_profile.source,
        rex_data_provenance=resolved_profile.data_provenance,
        rex_validation_status=resolved_profile.validation_status,
        rex_profile_start_nm=resolved_profile.wavelength_nm[0],
        rex_profile_end_nm=resolved_profile.wavelength_nm[-1],
        max_profile_wavelength_closure_error=max_profile_error,
        positive_source_start_nm=min(positive_wavelengths),
        positive_source_end_nm=max(positive_wavelengths),
        scalar_par=scalar,
        bands=bands,
        unsupported_source_mass_by_interval=tuple(unsupported),
    )


def build_rex_source_weighted_atr_payload(
    *,
    profile: LeafOpticalProfile | None = None,
    source_model: SmdSourceModel | None = None,
) -> RexSourceWeightedAtrPayload:
    """Build deterministic scalar-PAR and five-band Rex A/T/R coefficients."""

    resolved_profile = (
        load_rex_green_butterhead_mature_leaf_optics_v1()
        if profile is None
        else profile
    )
    resolved_source = (
        build_nominal_smd_source_model()
        if source_model is None
        else source_model
    )
    interval_set = compute_rex_source_weighted_interval_set(
        RexSourceWeightingInput(
            source_model_id=resolved_source.source_model_id,
            normalization_policy=resolved_source.normalization_policy,
            resource_hashes=tuple(sorted(resolved_source.resource_hashes.items())),
            photon_distribution=resolved_source.photon_distribution,
        ),
        profile=resolved_profile,
    )
    return RexSourceWeightedAtrPayload(
        source_model_id=resolved_source.source_model_id,
        source_normalization_policy=resolved_source.normalization_policy,
        source_resource_hashes=tuple(sorted(resolved_source.resource_hashes.items())),
        rex_profile_id=resolved_profile.profile_id,
        rex_profile_version=resolved_profile.profile_version,
        rex_treatment_id=resolved_profile.treatment_id,
        rex_profile_source=resolved_profile.source,
        rex_data_provenance=resolved_profile.data_provenance,
        rex_validation_status=resolved_profile.validation_status,
        rex_profile_start_nm=resolved_profile.wavelength_nm[0],
        rex_profile_end_nm=resolved_profile.wavelength_nm[-1],
        max_profile_wavelength_closure_error=(
            interval_set.max_profile_wavelength_closure_error
        ),
        scalar_par=interval_set.scalar_par,
        bands=interval_set.bands,
        model_id=(
            REX_SOURCE_WEIGHTED_ATR_MODEL_ID
            if resolved_source.source_model_id == SMD_SOURCE_MODEL_ID
            else REX_CONTROLLED_CONVENTIONAL_LED_WEIGHTED_ATR_MODEL_ID
        ),
    )


def compute_source_weighted_atr_interval(
    profile: LeafOpticalProfile,
    source_distribution: PhotonDistribution,
    *,
    interval_id: str,
    label: str,
    requested_start_nm: int,
    requested_end_nm_exclusive: int,
    allow_profile_start_truncation: bool = False,
) -> SourceWeightedAtrInterval:
    """Align exact wavelengths and compute one unnormalized weighted A/T/R set."""

    _validate_model_curve_closure(profile)
    if (
        isinstance(requested_start_nm, bool)
        or not isinstance(requested_start_nm, int)
        or isinstance(requested_end_nm_exclusive, bool)
        or not isinstance(requested_end_nm_exclusive, int)
        or requested_start_nm <= 0
        or requested_end_nm_exclusive <= requested_start_nm
    ):
        raise ValueError("requested wavelength interval must be ordered positive integers.")
    if not isinstance(allow_profile_start_truncation, bool):
        raise ValueError("allow_profile_start_truncation must be boolean.")

    profile_start = profile.wavelength_nm[0]
    profile_end = profile.wavelength_nm[-1]
    if profile_start > requested_start_nm and not allow_profile_start_truncation:
        raise ValueError(
            f"profile starts at {profile_start} nm and does not cover requested "
            f"{requested_start_nm} nm interval start."
        )
    effective_start = max(requested_start_nm, profile_start)
    requested_last = requested_end_nm_exclusive - 1
    if profile_end < requested_last:
        raise ValueError(
            f"profile ends at {profile_end} nm and does not cover requested "
            f"{requested_last} nm interval end."
        )
    expected_wavelengths = tuple(range(effective_start, requested_end_nm_exclusive))
    profile_indices = {
        wavelength: index for index, wavelength in enumerate(profile.wavelength_nm)
    }
    missing_profile = [
        wavelength
        for wavelength in expected_wavelengths
        if wavelength not in profile_indices
    ]
    if missing_profile:
        raise ValueError(
            "Rex profile has missing required 1 nm wavelengths: "
            + _compact_wavelengths(missing_profile)
        )
    source_by_wavelength = dict(
        zip(
            source_distribution.wavelength_nm,
            source_distribution.photon_amount,
            strict=True,
        )
    )
    missing_source = [
        wavelength
        for wavelength in expected_wavelengths
        if float(wavelength) not in source_by_wavelength
    ]
    if missing_source:
        raise ValueError(
            "source distribution has missing required exact wavelengths; "
            "interpolation and gap filling are disabled: "
            + _compact_wavelengths(missing_source)
        )
    weights = tuple(
        source_by_wavelength[float(wavelength)]
        for wavelength in expected_wavelengths
    )
    total_weight = math.fsum(weights)
    if not math.isfinite(total_weight) or total_weight <= 0.0:
        raise ValueError(
            f"source distribution has no positive photon weight for {interval_id!r}."
        )

    absorptance = math.fsum(
        weight * profile.absorptance[profile_indices[wavelength]]
        for wavelength, weight in zip(expected_wavelengths, weights, strict=True)
    ) / total_weight
    transmittance = math.fsum(
        weight * profile.transmittance[profile_indices[wavelength]]
        for wavelength, weight in zip(expected_wavelengths, weights, strict=True)
    ) / total_weight
    reflectance = math.fsum(
        weight * profile.reflectance[profile_indices[wavelength]]
        for wavelength, weight in zip(expected_wavelengths, weights, strict=True)
    ) / total_weight
    coefficients = AtrCoefficients(absorptance, transmittance, reflectance)
    raw_sum = coefficients.sum
    diagnostics = AtrClosureDiagnostics(
        raw_absorptance=absorptance,
        raw_transmittance=transmittance,
        raw_reflectance=reflectance,
        raw_sum=raw_sum,
        closure_error=raw_sum - 1.0,
        normalization_applied=False,
    )
    if not math.isclose(
        raw_sum,
        1.0,
        rel_tol=0.0,
        abs_tol=WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE,
    ):
        raise ValueError(
            f"source-weighted A/T/R closure failed for {interval_id!r}: "
            f"sum={raw_sum:.17g}, error={diagnostics.closure_error:.17g}."
        )
    return SourceWeightedAtrInterval(
        interval_id=interval_id,
        label=label,
        requested_start_nm=requested_start_nm,
        requested_end_nm_exclusive=requested_end_nm_exclusive,
        effective_start_nm=effective_start,
        effective_end_nm_exclusive=requested_end_nm_exclusive,
        wavelength_count=len(expected_wavelengths),
        source_photon_weight_umol_s=total_weight,
        coefficients=coefficients,
        closure_diagnostics=diagnostics,
    )


def format_rex_source_weighted_atr_json(
    payload: RexSourceWeightedAtrPayload,
) -> str:
    return json.dumps(payload.to_payload(), indent=2, sort_keys=True) + "\n"


def write_rex_source_weighted_atr_json(
    path: str | Path,
    payload: RexSourceWeightedAtrPayload,
) -> Path:
    output = Path(path)
    output.write_text(format_rex_source_weighted_atr_json(payload), encoding="utf-8")
    return output


def read_rex_source_weighted_atr_json(
    path: str | Path,
) -> RexSourceWeightedAtrPayload:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Rex source-weighted A/T/R JSON not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Rex source-weighted A/T/R JSON is malformed: {source}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Rex source-weighted A/T/R JSON root must be an object.")
    return RexSourceWeightedAtrPayload.from_payload(raw)


def _validate_rex_model_profile(profile: LeafOpticalProfile) -> float:
    if profile.profile_id != REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1:
        raise ValueError(
            "Rex source weighting requires the packaged mature Rex optical profile."
        )
    if profile.treatment_id != REX_LEAF_OPTICS_TREATMENT_ID:
        raise ValueError("Rex source weighting requires treatment mean_of_treatments.")
    expected_grid = tuple(range(404, 798))
    if profile.wavelength_nm != expected_grid:
        raise ValueError("Rex source weighting requires the complete 404–797 nm 1 nm grid.")
    return _validate_model_curve_closure(profile)


def _validate_model_curve_closure(profile: LeafOpticalProfile) -> float:
    max_error = 0.0
    for wavelength, absorptance, transmittance, reflectance in zip(
        profile.wavelength_nm,
        profile.absorptance,
        profile.transmittance,
        profile.reflectance,
        strict=True,
    ):
        for name, value in (
            ("absorptance_fraction_model", absorptance),
            ("transmittance_fraction_model", transmittance),
            ("reflectance_fraction_model", reflectance),
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"{name} at {wavelength} nm must be a finite fraction in [0, 1]."
                )
        error = abs(absorptance + transmittance + reflectance - 1.0)
        max_error = max(max_error, error)
        if error > REX_MODEL_WAVELENGTH_CLOSURE_ABS_TOLERANCE:
            raise ValueError(
                f"Rex model A/T/R closure failed at {wavelength} nm: "
                f"error={error:.17g}. No normalization was applied."
            )
    return max_error


def _compact_wavelengths(values: Sequence[int]) -> str:
    if len(values) <= 6:
        return ", ".join(f"{value} nm" for value in values)
    return (
        ", ".join(f"{value} nm" for value in values[:3])
        + f", ... ({len(values)} missing)"
    )


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object.")
    return value


def _string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _integer(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer.")
    return value


def _number(payload: Mapping[str, Any], key: str) -> float:
    value = payload.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{key} must be finite and numeric.")
    return float(value)
