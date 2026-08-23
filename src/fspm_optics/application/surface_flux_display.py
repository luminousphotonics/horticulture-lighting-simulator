"""Validated, system-neutral Phase 27G-D2 surface-flux display derivatives.

The authoritative Phase 27G-C Float64 patch table is read but never changed.
This module validates its complete scientific chain, authenticates the fixed
front local-patch calibration, selects one immutable quality-family profile,
and emits one bounded raw-q Float32 RGBA table for the instanced plant viewer.
Front display normalization is derived only after those raw bytes and the v2
metadata have been authenticated.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import resources
import json
import math
from pathlib import Path
import struct
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence

from fspm_optics.plants import (
    NaturalFitLayoutPlan,
    REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID,
    REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID,
    RexJuvenilePreheadingConfig,
    generate_rex_juvenile_preheading_plant,
    legacy_rex_juvenile_preheading_config,
)
from fspm_optics.viewer.artifacts import PROFILE_ID
from fspm_optics.viewer.models import (
    BinaryDisplayArtifact,
    SurfaceFluxViewerArtifacts,
)

from .fspm_science import (
    BAND_ORDER,
    FSPM_AGGREGATION_SCHEMA_ID,
    FSPM_AGGREGATION_SCHEMA_VERSION,
    PAR_BAND_ORDER,
    PATCH_INDEX_FIELDS,
    binary_contract_for_band_order,
)
from .surface_flux_display_calibration import (
    BLOCK_ORDER as DISPLAY_CALIBRATION_BLOCK_ORDER,
    COEFFICIENT_COUNT as DISPLAY_CALIBRATION_COEFFICIENT_COUNT,
    D5_RECEIVERS_SHA256,
    D5_SAMPLING_PROFILE_ID,
    D5_TOPOLOGY_SHA256,
    DISPLAY_CALIBRATION_RESOURCE_ID,
    ESTIMATOR_ID as DISPLAY_CALIBRATION_ESTIMATOR_ID,
    VIEWER_CALIBRATION_PAYLOAD_NAME,
    AuthenticatedSurfaceFluxDisplayCalibration,
    quality_dispatch_contract as display_quality_dispatch_contract,
)

JUVENILE_MULTISPECTRAL_SCHEMA_ID = (
    "fspm-optics.juvenile-multi-plant-five-band-receivers"
)
JUVENILE_MULTISPECTRAL_SCHEMA_VERSION = 3
LEGACY_JUVENILE_MULTISPECTRAL_SCHEMA_VERSION = 2
LEGACY_FSPM_AGGREGATION_SCHEMA_VERSION = 1
COMPACT_RECEIVER_INDEX_SCHEMA_ID = (
    "fspm-optics.juvenile-compact-receiver-index"
)
COMPACT_RECEIVER_INDEX_SCHEMA_VERSION = 1

SURFACE_FLUX_DISPLAY_SCHEMA_ID = "fspm-optics.surface-flux-display"
SURFACE_FLUX_DISPLAY_SCHEMA_VERSION = 2
SURFACE_FLUX_METADATA_FILENAME = "surface-flux/metadata.v2.json"
AUTHENTICATED_SURFACE_FLUX_DISPLAY_SCHEMA_VERSION = 3
AUTHENTICATED_SURFACE_FLUX_METADATA_FILENAME = "surface-flux/metadata.v3.json"
SURFACE_FLUX_VALUES_FILENAME = "surface-flux/patch-values.v1.f32le.bin"
SURFACE_FLUX_VALUES_STRIDE_BYTES = 16
PATCHES_PER_PLANT = 192
LEAVES_PER_PLANT = 12
FACES_PER_PLANT = 1920
RECEIVERS_PER_PLANT = 384
MAX_DISPLAY_PLANT_COUNT = 625
METRIC_ORDER = ("incident_par", "absorbed_par")
SIDE_ORDER = ("front", "back")

CALIBRATION_REPORT_SCHEMA_ID = "fspm-optics.surface-flux-calibration-report"
CALIBRATION_REPORT_SCHEMA_VERSION = 1
CALIBRATION_REPOSITORY_REVISION = (
    "3a35cc3a8d143e624a468049344ed15676d791e8"
)
CALIBRATION_CONFIGURATION_SHA256 = (
    "d388d6293a94102232f5211dd8bb0b942b401ea30794a63c9f36d8920ae4d9b0"
)
CALIBRATION_SOURCE_MODEL_ID = "neutral-uniform-upper-hemisphere-v1"
CALIBRATION_SOURCE_DEFINITION_SHA256 = (
    "cfe8b701186ef8c2cd49ae8d8e9783968353cbc9383e47e3674b8909bb3e6530"
)
CALIBRATION_SCENE_SHA256 = (
    "a3bf55f2e2755ee74a28c6285d8b2408ead18ca3c416487988b238debe784d32"
)
CALIBRATION_TOPOLOGY_SHA256 = (
    "b199a43c98aa899657aae4fbda0b04fee72290d05636aa39d51240598c95fa09"
)
CALIBRATION_RECEIVERS_SHA256 = (
    "e9631120fea1672e2047e0db7fdb8297ca9e4a9ba89cd27b947dd89af99038fc"
)
CALIBRATION_MATERIAL_PLAN_SHA256 = (
    "c5d829ecdbde2b06602468e187fd75c6314f55e671b2d5529289d40adb4ccff6"
)
FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_ID = (
    "fspm-optics.surface-flux-front-local-patch-calibration-input"
)
FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_VERSION = 1
FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_NAME = (
    "surface-flux-front-local-patch-calibration.v1.json"
)
FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_SHA256 = (
    "6f22972a6ffe41db494f464abafdcad85bc4434bf7c6e326ebabfef6c1008bd3"
)
FRONT_LOCAL_PATCH_COMBINED_SHA256 = (
    "b1e3cec31fc4b4e81ce03fd88291edaafd8bb6a717a4d228e57c60d61bd21a07"
)
FRONT_LOCAL_PATCH_COMBINED_BYTE_LENGTH = 6144
FRONT_LOCAL_PATCH_COEFFICIENT_ORDER = "canonical local_patch_index 0..191"
FRONT_LOCAL_PATCH_COMBINED_ORDER = (
    "standard incident_par; standard absorbed_par; "
    "quality incident_par; quality absorbed_par"
)
FRONT_LOCAL_PATCH_PROFILE_HASHES = MappingProxyType(
    {
        ("standard", "incident_par"): (
            "a0216419e067bedaa20d54f3dbbb4c9ea375aa0ed5198e3e286d036144a925ec"
        ),
        ("standard", "absorbed_par"): (
            "f4b79e943efd58e07b847b1c3c51ecc8490c4af483c131918e6b00eb46c3a1fb"
        ),
        ("quality", "incident_par"): (
            "725d4b6ae9570fbafc4e01c745e120123325949d9c349230730149904ce34c41"
        ),
        ("quality", "absorbed_par"): (
            "98b5c1b07067f407e56c8ddd39015211822625fc8f522be480bcbfd47e86939f"
        ),
    }
)

FRONT_ANCHORS = (0.0, 0.10, 0.31, 0.89, 1.0, 1.76, 2.07, 2.18)
FRONT_LOCAL_PATCH_ANCHORS = (
    0.0,
    0.59,
    0.81,
    0.89,
    1.0,
    1.14,
    1.52,
    2.84,
)
BACK_ANCHORS = (0.0, 0.025, 0.050, 0.14, 0.42, 1.0, 2.75, 5.33)
LEGACY_FRONT_ANCHORS = FRONT_ANCHORS
FRONT_PALETTE = (
    ("blue", "#2563EB"),
    ("cyan", "#06B6D4"),
    ("teal", "#14B8A6"),
    ("green_low", "#22C55E"),
    ("green_reference", "#22C55E"),
    ("green_high", "#22C55E"),
    ("orange", "#F59E0B"),
    ("red", "#DC2626"),
)
BACK_PALETTE = (
    ("blue", "#2563EB"),
    ("cyan", "#06B6D4"),
    ("teal", "#14B8A6"),
    ("green_low", "#22C55E"),
    ("green_reference", "#22C55E"),
    ("yellow_green", "#A3E635"),
    ("orange", "#F59E0B"),
    ("red", "#DC2626"),
)
SCHEMA_V3_BACK_PALETTE_ID = (
    "surface-flux-back-log-compressed-blue-to-red-8-v2"
)
SCHEMA_V3_BACK_ANCHORS = (
    0.0,
    0.4,
    0.7,
    0.9,
    1.0,
    10.0,
    100.0,
    512.0,
)
SCHEMA_V3_BACK_PALETTE = (
    ("blue", "#2563EB"),
    ("cyan", "#06B6D4"),
    ("teal", "#14B8A6"),
    ("green_low", "#22C55E"),
    ("green_reference", "#22C55E"),
    ("yellow_green", "#A3E635"),
    ("orange", "#F59E0B"),
    ("red", "#DC2626"),
)
# Compatibility name for the original scalar palette; v2 code uses the two
# explicit side-specific palettes above and never applies this to the front.
PALETTE = BACK_PALETTE

_PATCH_VALUE_FIELDS = MappingProxyType(
    {
        "front_incident": (
            "par_front_incident_photon_flux_density_umol_m2_s"
        ),
        "back_incident": (
            "par_back_incident_photon_flux_density_umol_m2_s"
        ),
        "front_absorbed": (
            "par_front_absorbed_photon_flux_density_umol_m2_s"
        ),
        "back_absorbed": (
            "par_back_absorbed_photon_flux_density_umol_m2_s"
        ),
    }
)


class SurfaceFluxDisplayError(RuntimeError):
    """A D2 calibration, artifact, reference, or topology gate failed."""


@dataclass(frozen=True, slots=True)
class FrontLocalPatchMetric:
    """One authenticated ordered gamma profile for a front PAR metric."""

    coefficients: tuple[float, ...]
    float64_little_endian_sha256: str
    derivation: str

    def to_dict(self) -> dict[str, object]:
        return {
            "coefficient_count": len(self.coefficients),
            "coefficient_order": FRONT_LOCAL_PATCH_COEFFICIENT_ORDER,
            "coefficients": list(self.coefficients),
            "float64_little_endian_sha256": (
                self.float64_little_endian_sha256
            ),
            "derivation": self.derivation,
        }


@dataclass(frozen=True, slots=True)
class _FrontLocalPatchCalibration:
    profiles: Mapping[str, Mapping[str, FrontLocalPatchMetric]]
    limitations: tuple[str, ...]


def _calibration_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SurfaceFluxDisplayError(f"{label} is missing or invalid.")
    return value


def _calibration_positive_finite(label: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SurfaceFluxDisplayError(f"{label} must be finite and positive.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise SurfaceFluxDisplayError(f"{label} must be finite and positive.")
    return number


def _load_front_local_patch_calibration() -> _FrontLocalPatchCalibration:
    """Load and authenticate the sole packaged local-patch calibration."""

    try:
        raw = (
            resources.files("fspm_optics")
            .joinpath(
                "resources",
                "calibration",
                FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_NAME,
            )
            .read_bytes()
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise SurfaceFluxDisplayError(
            "required packaged front local-patch calibration is unavailable."
        ) from exc
    if hashlib.sha256(raw).hexdigest() != FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_SHA256:
        raise SurfaceFluxDisplayError(
            "packaged front local-patch calibration hash is incompatible."
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SurfaceFluxDisplayError(
            "packaged front local-patch calibration is not valid UTF-8 JSON."
        ) from exc
    if not isinstance(payload, dict):
        raise SurfaceFluxDisplayError(
            "packaged front local-patch calibration must be a JSON object."
        )
    _validate_front_local_patch_calibration_identity(payload)
    profiles_payload = _calibration_mapping(
        payload.get("profiles"), "calibration profiles"
    )
    expected_profile_contracts = {
        "standard": {
            "calibration_source_quality": "standard",
            "evaluated_quality_mapping": ["direct", "standard"],
            "proxy_mapping": {"direct": True, "standard": False},
        },
        "quality": {
            "calibration_source_quality": "quality",
            "evaluated_quality_mapping": ["quality", "rigorous"],
            "proxy_mapping": {"quality": False, "rigorous": True},
        },
    }
    if set(profiles_payload) != set(expected_profile_contracts):
        raise SurfaceFluxDisplayError(
            "front local-patch calibration profile inventory is incompatible."
        )
    decoded_profiles: dict[str, Mapping[str, FrontLocalPatchMetric]] = {}
    combined = bytearray()
    for family in ("standard", "quality"):
        profile = _calibration_mapping(
            profiles_payload.get(family), f"{family} calibration profile"
        )
        expected_profile = expected_profile_contracts[family]
        if any(profile.get(name) != value for name, value in expected_profile.items()):
            raise SurfaceFluxDisplayError(
                f"{family} front local-patch family mapping is incompatible."
            )
        metrics = _calibration_mapping(
            profile.get("metrics"), f"{family} calibration metrics"
        )
        if set(metrics) != set(METRIC_ORDER):
            raise SurfaceFluxDisplayError(
                f"{family} front local-patch metric inventory is incompatible."
            )
        decoded_metrics: dict[str, FrontLocalPatchMetric] = {}
        for metric in METRIC_ORDER:
            metric_payload = _calibration_mapping(
                metrics.get(metric), f"{family} {metric} calibration metric"
            )
            coefficients_payload = metric_payload.get("coefficients")
            if (
                metric_payload.get("coefficient_count") != PATCHES_PER_PLANT
                or metric_payload.get("coefficient_order")
                != FRONT_LOCAL_PATCH_COEFFICIENT_ORDER
                or metric_payload.get("coefficient_symbol") != "gamma_k"
                or metric_payload.get("coefficient_units") != "dimensionless q/R"
                or not isinstance(coefficients_payload, list)
                or len(coefficients_payload) != PATCHES_PER_PLANT
                or not isinstance(metric_payload.get("derivation"), str)
                or not metric_payload["derivation"]
            ):
                raise SurfaceFluxDisplayError(
                    f"{family} {metric} local-patch coefficient contract is invalid."
                )
            coefficients = tuple(
                _calibration_positive_finite("front local-patch gamma", value)
                for value in coefficients_payload
            )
            encoded = struct.pack(f"<{PATCHES_PER_PLANT}d", *coefficients)
            expected_hash = FRONT_LOCAL_PATCH_PROFILE_HASHES[(family, metric)]
            if (
                metric_payload.get("float64_little_endian_sha256")
                != expected_hash
                or hashlib.sha256(encoded).hexdigest() != expected_hash
                or metric_payload.get("minimum") != min(coefficients)
                or metric_payload.get("maximum") != max(coefficients)
            ):
                raise SurfaceFluxDisplayError(
                    f"{family} {metric} local-patch coefficients failed authentication."
                )
            combined.extend(encoded)
            decoded_metrics[metric] = FrontLocalPatchMetric(
                coefficients=coefficients,
                float64_little_endian_sha256=expected_hash,
                derivation=metric_payload["derivation"],
            )
        decoded_profiles[family] = MappingProxyType(decoded_metrics)
    derivation = _calibration_mapping(
        payload.get("derivation_contract"), "calibration derivation contract"
    )
    if (
        derivation.get("profile_arrays_binary_order")
        != FRONT_LOCAL_PATCH_COMBINED_ORDER
        or derivation.get("combined_float64_little_endian_byte_length")
        != FRONT_LOCAL_PATCH_COMBINED_BYTE_LENGTH
        or derivation.get("combined_float64_little_endian_sha256")
        != FRONT_LOCAL_PATCH_COMBINED_SHA256
        or len(combined) != FRONT_LOCAL_PATCH_COMBINED_BYTE_LENGTH
        or hashlib.sha256(combined).hexdigest()
        != FRONT_LOCAL_PATCH_COMBINED_SHA256
    ):
        raise SurfaceFluxDisplayError(
            "combined front local-patch coefficient profile failed authentication."
        )
    limitations = payload.get("limitations")
    if (
        not isinstance(limitations, list)
        or not limitations
        or any(not isinstance(item, str) or not item for item in limitations)
    ):
        raise SurfaceFluxDisplayError(
            "front local-patch calibration limitations are incomplete."
        )
    return _FrontLocalPatchCalibration(
        profiles=MappingProxyType(decoded_profiles),
        limitations=tuple(limitations),
    )


def _validate_front_local_patch_calibration_identity(
    payload: Mapping[str, object],
) -> None:
    source = _calibration_mapping(
        payload.get("source_authority"), "calibration source authority"
    )
    topology = _calibration_mapping(
        payload.get("topology_contract"), "calibration topology"
    )
    display_model = _calibration_mapping(
        payload.get("display_model"), "calibration display model"
    )
    palette = _calibration_mapping(
        payload.get("front_palette"), "calibration front palette"
    )
    reference = _calibration_mapping(
        payload.get("reference_contract"), "calibration reference contract"
    )
    requirements = _calibration_mapping(
        payload.get("implementation_requirements"),
        "calibration implementation requirements",
    )
    expected_colors = [
        {"name": name, "srgb_hex": color} for name, color in FRONT_PALETTE
    ]
    if (
        payload.get("schema_id") != FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_ID
        or payload.get("schema_version")
        != FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_VERSION
        or payload.get("scope")
        != (
            "front receiver surface only; backside behavior intentionally deferred "
            "and unchanged"
        )
        or source.get("original_d1_experiment_status") != "complete_fail"
        or source.get("original_d1_acceptance_pass") is not False
        or source.get("original_d1_outcome_must_not_be_rewritten") is not True
        or source.get("repository_revision") != CALIBRATION_REPOSITORY_REVISION
        or source.get("configuration_sha256") != CALIBRATION_CONFIGURATION_SHA256
        or source.get("source_model_id") != CALIBRATION_SOURCE_MODEL_ID
        or source.get("source_definition_sha256")
        != CALIBRATION_SOURCE_DEFINITION_SHA256
        or source.get("topology_sha256") != CALIBRATION_TOPOLOGY_SHA256
        or source.get("receivers_sha256") != CALIBRATION_RECEIVERS_SHA256
        or source.get("material_plan_sha256") != CALIBRATION_MATERIAL_PLAN_SHA256
        or source.get("report_schema_id") != CALIBRATION_REPORT_SCHEMA_ID
        or source.get("report_schema_version")
        != CALIBRATION_REPORT_SCHEMA_VERSION
        or source.get("calibration_report_sha256")
        != "02e6f0dcdf056c68dfd974c31a1b7af9e2c844002de91f5f0be635ce8e18dcdf"
        or topology.get("plants") != 64
        or topology.get("local_patches_per_plant") != PATCHES_PER_PLANT
        or topology.get("global_patches") != 64 * PATCHES_PER_PLANT
        or topology.get("global_patch_equation")
        != "192 * plant_index + local_patch_index"
        or topology.get("front_receiver_equation")
        != "384 * plant_index + 2 * local_patch_index"
        or topology.get("coefficient_is_average_over_all_64_plants") is not True
        or topology.get("coefficient_position_dependence") is not False
        or display_model.get("equation")
        != "u[p,k,m] = q[p,k,m] / (gamma[quality_family,m,k] * R)"
        or display_model.get("variable") != "u"
        or display_model.get("raw_q_modified") is not False
        or display_model.get("per_run_fitting") is not False
        or display_model.get("per_system_fitting") is not False
        or display_model.get("plant_position_normalization") is not False
        or display_model.get("system_name_branching") is not False
        or reference.get("evaluated_run_R_policy")
        != (
            "unchanged from Phase 27G-D2: requested Stage A target for "
            "target-controlled runs; achieved plant-free Stage A mean for "
            "fixed-output runs"
        )
        or requirements.get("front_only") is not True
        or requirements.get("backside_deferred") is not True
        or requirements.get("published_float32_raw_q_artifact_semantics_unchanged")
        is not True
        or requirements.get("raw_float64_science_values_unchanged") is not True
        or requirements.get(
            "viewer_must_derive_bounded_display_values_after_authentication"
        )
        is not True
        or requirements.get("invalid_profile_or_hash_falls_back_to_neutral_material")
        is not True
        or palette.get("palette_id")
        != "surface-flux-front-neutral-local-patch-blue-to-red-8-v1"
        or palette.get("anchors_u") != list(FRONT_LOCAL_PATCH_ANCHORS)
        or palette.get("colors") != expected_colors
        or palette.get("green_interval_u") != [0.89, 1.14]
        or palette.get("continuous_interpolation") is not True
        or palette.get("shared_across_metrics_systems_and_quality_families")
        is not True
        or palette.get("per_run_extrema_normalization") is not False
    ):
        raise SurfaceFluxDisplayError(
            "front local-patch calibration identity or provenance is incompatible."
        )


_FRONT_LOCAL_PATCH_CALIBRATION = _load_front_local_patch_calibration()


class _ArtifactRecord(Protocol):
    sha256: str

    def to_dict(self) -> dict[str, object]: ...


class _AggregationPublication(Protocol):
    metadata: Mapping[str, object]
    metadata_artifact: _ArtifactRecord
    room_summary: Mapping[str, object]


class MultispectralArtifactPublication(Protocol):
    """Minimal validated Phase B/C artifact boundary consumed by D2."""

    metadata: Mapping[str, object]
    metadata_artifact: _ArtifactRecord
    scientific_aggregation: _AggregationPublication


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    profile_id: str
    family_id: str
    source_quality: str
    coefficients: Mapping[str, float]
    fit_basis: str
    front_local_patch_metrics: Mapping[str, FrontLocalPatchMetric]
    achieved_reference_ppfd_umol_m2_s: float | None = None

    def coefficient(self, metric: str, side: str) -> float:
        try:
            return float(self.coefficients[f"{side}_{metric.removesuffix('_par')}"])
        except KeyError as exc:
            raise SurfaceFluxDisplayError(
                "surface-flux metric or side is unsupported."
            ) from exc

    def front_metric(self, metric: str) -> FrontLocalPatchMetric:
        try:
            return self.front_local_patch_metrics[metric]
        except KeyError as exc:
            raise SurfaceFluxDisplayError(
                "front local-patch metric is unsupported."
            ) from exc

    def to_dict(self) -> dict[str, object]:
        scalar_beta: dict[str, object] = {
            "coefficients": dict(self.coefficients),
            "coefficient_equation": "z = q / (beta * R)",
            "fit_basis": self.fit_basis,
            "front_used_for_coloring": False,
            "back_used_for_coloring": True,
        }
        if self.achieved_reference_ppfd_umol_m2_s is not None:
            scalar_beta["achieved_reference_ppfd_umol_m2_s"] = (
                self.achieved_reference_ppfd_umol_m2_s
            )
        return {
            "profile_id": self.profile_id,
            "family_id": self.family_id,
            "calibration_source_quality": self.source_quality,
            "front_local_patch_metrics": {
                metric: self.front_local_patch_metrics[metric].to_dict()
                for metric in METRIC_ORDER
            },
            "scalar_beta_provenance": scalar_beta,
        }


STANDARD_PROFILE = CalibrationProfile(
    profile_id="neutral-upper-hemisphere-standard-front-local-patch-v1",
    family_id="neutral-upper-hemisphere-standard-family-v1",
    source_quality="standard",
    coefficients=MappingProxyType(
        {
            "front_incident": 0.4510905803216862,
            "back_incident": 0.058044697174435056,
            "front_absorbed": 0.3657821501976058,
            "back_absorbed": 0.04670307023408276,
        }
    ),
    fit_basis="five-level through-origin neutral-reference sweep",
    front_local_patch_metrics=_FRONT_LOCAL_PATCH_CALIBRATION.profiles[
        "standard"
    ],
)
QUALITY_PROFILE = CalibrationProfile(
    profile_id="neutral-upper-hemisphere-quality-front-local-patch-v1",
    family_id="neutral-upper-hemisphere-quality-family-v1",
    source_quality="quality",
    coefficients=MappingProxyType(
        {
            "front_incident": 0.448166736538762,
            "back_incident": 0.06098285511211222,
            "front_absorbed": 0.3632938753333456,
            "back_absorbed": 0.04909664459054215,
        }
    ),
    fit_basis="single held-quality neutral-reference verification level",
    front_local_patch_metrics=_FRONT_LOCAL_PATCH_CALIBRATION.profiles["quality"],
    achieved_reference_ppfd_umol_m2_s=500.0112,
)
CALIBRATION_PROFILES = MappingProxyType(
    {
        STANDARD_PROFILE.profile_id: STANDARD_PROFILE,
        QUALITY_PROFILE.profile_id: QUALITY_PROFILE,
    }
)


@dataclass(frozen=True, slots=True)
class QualityFamilySelection:
    evaluated_quality: str
    profile: CalibrationProfile
    proxy: bool
    proxy_rationale: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "evaluated_quality": self.evaluated_quality,
            "selected_profile_id": self.profile.profile_id,
            "calibration_source_quality": self.profile.source_quality,
            "family_id": self.profile.family_id,
            "proxy": self.proxy,
            "proxy_rationale": self.proxy_rationale,
        }


_QUALITY_FAMILY_POLICY = MappingProxyType(
    {
        "direct": QualityFamilySelection(
            "direct",
            STANDARD_PROFILE,
            True,
            "Direct uses the Standard development family as an explicitly declared proxy.",
        ),
        "standard": QualityFamilySelection(
            "standard", STANDARD_PROFILE, False, None
        ),
        "quality": QualityFamilySelection(
            "quality", QUALITY_PROFILE, False, None
        ),
        "rigorous": QualityFamilySelection(
            "rigorous",
            QUALITY_PROFILE,
            True,
            (
                "Rigorous uses the Quality high-fidelity family based on prior "
                "Quality-versus-Rigorous convergence evidence."
            ),
        ),
    }
)


def select_quality_family(quality: object) -> QualityFamilySelection | None:
    """Return the declared quality-family mapping, never a silent default."""

    if not isinstance(quality, str):
        return None
    return _QUALITY_FAMILY_POLICY.get(quality)


def quality_family_mapping_contract() -> dict[str, object]:
    """Return the complete generic quality-to-calibration mapping."""

    return {
        quality: {
            name: value
            for name, value in selection.to_dict().items()
            if name != "evaluated_quality"
        }
        for quality, selection in _QUALITY_FAMILY_POLICY.items()
    }


@dataclass(frozen=True, slots=True)
class SurfaceFluxReference:
    value_umol_m2_s: float
    operating_policy: str
    value_kind: str
    source_provenance: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "value": self.value_umol_m2_s,
            "units": "umol/m^2/s",
            "value_kind": self.value_kind,
            "operating_policy": self.operating_policy,
            "source_provenance": dict(self.source_provenance),
        }


def select_surface_flux_reference(
    *,
    operating_policy: str,
    requested_stage_a_target_ppfd_umol_m2_s: object = None,
    achieved_stage_a_mean_ppfd_umol_m2_s: object = None,
) -> SurfaceFluxReference:
    """Resolve R from generic Stage A operating-policy metadata."""

    if operating_policy == "target-controlled":
        value = _positive_finite(
            "requested Stage A target PPFD",
            requested_stage_a_target_ppfd_umol_m2_s,
        )
        if achieved_stage_a_mean_ppfd_umol_m2_s is not None:
            _positive_finite(
                "achieved Stage A mean PPFD",
                achieved_stage_a_mean_ppfd_umol_m2_s,
            )
        return SurfaceFluxReference(
            value,
            operating_policy,
            "requested",
            MappingProxyType(
                {
                    "stage_id": "baseline_ppfd",
                    "artifact": "request",
                    "field": "target_ppfd_umol_m2_s",
                }
            ),
        )
    if operating_policy == "fixed-output":
        if requested_stage_a_target_ppfd_umol_m2_s is not None:
            raise SurfaceFluxDisplayError(
                "fixed-output reference metadata must not declare a requested target."
            )
        value = _positive_finite(
            "achieved plant-free Stage A mean PPFD",
            achieved_stage_a_mean_ppfd_umol_m2_s,
        )
        return SurfaceFluxReference(
            value,
            operating_policy,
            "achieved",
            MappingProxyType(
                {
                    "stage_id": "baseline_ppfd",
                    "artifact": "ppfd.csv",
                    "field": "achieved_mean_ppfd_umol_m2_s",
                    "plant_free_reference_plane": True,
                }
            ),
        )
    raise SurfaceFluxDisplayError(
        "surface-flux reference operating policy is missing or ambiguous."
    )


def select_authenticated_achieved_surface_flux_reference(
    *,
    operating_policy: str,
    achieved_stage_a_mean_ppfd_umol_m2_s: object,
) -> SurfaceFluxReference:
    """Resolve metadata-v3 R exclusively from authenticated achieved Stage A."""

    if operating_policy not in {"target-controlled", "fixed-output"}:
        raise SurfaceFluxDisplayError(
            "authenticated achieved reference operating policy is invalid."
        )
    value = _positive_finite(
        "authenticated achieved Stage A mean PPFD",
        achieved_stage_a_mean_ppfd_umol_m2_s,
    )
    return SurfaceFluxReference(
        value,
        operating_policy,
        "achieved",
        MappingProxyType(
            {
                "stage_id": "baseline_ppfd",
                "artifact": "ppfd.csv",
                "field": "achieved_mean_ppfd_umol_m2_s",
                "authenticated": True,
                "plant_free_reference_plane": True,
            }
        ),
    )


@dataclass(frozen=True, slots=True)
class SurfaceFluxDisplayPublication:
    metadata: Mapping[str, object]
    viewer_artifacts: SurfaceFluxViewerArtifacts

    def manifest_payload(self) -> dict[str, object]:
        payload = {
            "schema_id": SURFACE_FLUX_DISPLAY_SCHEMA_ID,
            "schema_version": self.metadata["schema_version"],
            "metadata": _artifact_record(self.viewer_artifacts.metadata),
            "patch_values": _artifact_record(self.viewer_artifacts.patch_values),
            "quality_family": dict(self.metadata["quality_family"]),
            "reference": dict(self.metadata["reference"]),
            "display_only": True,
        }
        if self.viewer_artifacts.calibration_coefficients is not None:
            payload["display_calibration_coefficients"] = _artifact_record(
                self.viewer_artifacts.calibration_coefficients
            )
        return payload


def build_surface_flux_display_publication(
    root: Path,
    *,
    run_id: str,
    quality: str,
    natural_fit: NaturalFitLayoutPlan,
    reference: SurfaceFluxReference,
    multispectral: MultispectralArtifactPublication,
    display_calibration: AuthenticatedSurfaceFluxDisplayCalibration | None = None,
) -> SurfaceFluxDisplayPublication:
    """Validate Phase 27G-C and build one authenticated D2 viewer payload."""

    resolved_root = root.resolve(strict=True)
    legacy_selection = select_quality_family(quality)
    if legacy_selection is None:
        raise SurfaceFluxDisplayError(
            "unknown evaluated quality has no calibration family; keep neutral."
        )
    display_selection = (
        None
        if display_calibration is None
        else display_calibration.select_quality(quality)
    )
    sampling_profile_id = (
        REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID
        if display_calibration is None
        else D5_SAMPLING_PROFILE_ID
    )
    calibration_topology_sha256 = (
        CALIBRATION_TOPOLOGY_SHA256
        if display_calibration is None
        else D5_TOPOLOGY_SHA256
    )
    calibration_receivers_sha256 = (
        CALIBRATION_RECEIVERS_SHA256
        if display_calibration is None
        else D5_RECEIVERS_SHA256
    )
    if not isinstance(natural_fit, NaturalFitLayoutPlan):
        raise SurfaceFluxDisplayError("surface coloring requires Natural-fit identity.")
    plant_count = natural_fit.total_count
    if not 0 < plant_count <= MAX_DISPLAY_PLANT_COUNT:
        raise SurfaceFluxDisplayError("surface-flux display allocation is unbounded.")

    transport = _validated_json_artifact(
        resolved_root,
        multispectral.metadata_artifact.to_dict(),
        expected_role="multispectral_transport_metadata",
    )
    aggregation_publication = multispectral.scientific_aggregation
    aggregation = _validated_json_artifact(
        resolved_root,
        aggregation_publication.metadata_artifact.to_dict(),
        expected_role="fspm_scientific_aggregation_metadata",
    )
    if transport != dict(multispectral.metadata):
        raise SurfaceFluxDisplayError(
            "transport metadata memory and artifact authorities disagree."
        )
    if aggregation != dict(aggregation_publication.metadata):
        raise SurfaceFluxDisplayError(
            "aggregation metadata memory and artifact authorities disagree."
        )
    if (
        aggregation.get("transport_metadata_sha256")
        != multispectral.metadata_artifact.sha256
    ):
        raise SurfaceFluxDisplayError(
            "aggregation does not reference the validated transport metadata hash."
        )
    compact_record = _mapping(transport.get("compact_receiver_index"), "compact index")
    if (
        compact_record.get("path")
        != "fspm-transport/scene/receiver-index.v1.json"
        or compact_record.get("media_type") != "application/json"
    ):
        raise SurfaceFluxDisplayError("compact receiver-index artifact is incompatible.")
    compact = _validated_json_artifact(
        resolved_root, compact_record, expected_role="compact_receiver_index"
    )
    patch_record = _inventory_record(
        aggregation, "fspm_patch_surface_light"
    )
    aggregation_schema_version = aggregation.get("schema_version")
    expected_patch_path = (
        "fspm-aggregation/patch-surface-light.v1.f64le.bin"
        if aggregation_schema_version == LEGACY_FSPM_AGGREGATION_SCHEMA_VERSION
        else "fspm-aggregation/patch-surface-light.v2.f64le.bin"
    )
    if (
        patch_record.get("path")
        != expected_patch_path
        or patch_record.get("media_type") != "application/octet-stream"
    ):
        raise SurfaceFluxDisplayError("Phase 27G-C patch artifact is incompatible.")
    patch_path = _validated_file_artifact(
        resolved_root, patch_record, expected_role="fspm_patch_surface_light"
    )
    room_record = _inventory_record(
        aggregation, "fspm_room_surface_light_summary"
    )
    expected_room_path = (
        "fspm-aggregation/room-summary.v1.json"
        if aggregation_schema_version == LEGACY_FSPM_AGGREGATION_SCHEMA_VERSION
        else "fspm-aggregation/room-summary.v2.json"
    )
    if (
        room_record.get("path") != expected_room_path
        or room_record.get("media_type") != "application/json"
    ):
        raise SurfaceFluxDisplayError("Phase 27G-C room artifact is incompatible.")
    room_summary = _validated_json_artifact(
        resolved_root,
        room_record,
        expected_role="fspm_room_surface_light_summary",
    )
    if room_summary != dict(aggregation_publication.room_summary):
        raise SurfaceFluxDisplayError(
            "room summary memory and artifact authorities disagree."
        )

    expected_counts = {
        "plants": plant_count,
        "leaves": plant_count * LEAVES_PER_PLANT,
        "faces": plant_count * FACES_PER_PLANT,
        "patches": plant_count * PATCHES_PER_PLANT,
        "receivers": plant_count * RECEIVERS_PER_PLANT,
    }
    material_authorities = _validate_scientific_contract(
        run_id=run_id,
        quality=quality,
        natural_fit=natural_fit,
        expected_counts=expected_counts,
        transport=transport,
        aggregation=aggregation,
        compact=compact,
        patch_record=patch_record,
        sampling_profile_id=sampling_profile_id,
        calibration_topology_sha256=calibration_topology_sha256,
        calibration_receivers_sha256=calibration_receivers_sha256,
    )
    executed_band_order = tuple(transport.get("band_order", ()))
    patch_struct, _index_fields, patch_float_fields = (
        binary_contract_for_band_order(executed_band_order)["patch"]
    )
    patch_values, statistics = _convert_patch_values(
        patch_path,
        expected_counts=expected_counts,
        selection=legacy_selection,
        reference=reference,
        display_calibration=display_calibration,
        display_family=(
            None if display_selection is None else display_selection.coefficient_family
        ),
        sampling_profile_id=sampling_profile_id,
        patch_struct=patch_struct,
        patch_float_fields=patch_float_fields,
    )
    _validate_room_summary(
        room_summary,
        run_id=run_id,
        expected_counts=expected_counts,
        legends=statistics,
    )
    values_artifact = _artifact(SURFACE_FLUX_VALUES_FILENAME, patch_values)
    calibration_artifact = (
        None
        if display_calibration is None
        else _artifact(VIEWER_CALIBRATION_PAYLOAD_NAME, display_calibration.payload)
    )
    metadata: dict[str, object] = {
        "schema_id": SURFACE_FLUX_DISPLAY_SCHEMA_ID,
        "schema_version": SURFACE_FLUX_DISPLAY_SCHEMA_VERSION,
        "availability": "available",
        "run_id": run_id,
        "profile_id": PROFILE_ID,
        "quality_family": legacy_selection.to_dict(),
        "quality_family_mapping": quality_family_mapping_contract(),
        "selected_calibration_profile": legacy_selection.profile.to_dict(),
        "calibration_provenance": calibration_provenance(),
        "reference": reference.to_dict(),
        "display_equation": {
            "front": {
                "formula": (
                    "u[p,k,m] = q[p,k,m] / "
                    "(gamma[quality_family,m,k] * R)"
                ),
                "variable": "u",
                "q_authority": (
                    "Phase 27G-C raw Float64 PAR surface-light density"
                ),
                "gamma_index": (
                    "canonical local_patch_index k in [0, 191], reused across plants"
                ),
                "u_equals_one_meaning": (
                    "Expected-equivalent exposure for the same canonical local "
                    "patch under the fixed neutral upper-hemisphere calibration; "
                    "not a leaf target and not raw-q uniformity."
                ),
                "front_beta_used_for_coloring": False,
            },
            "back": {
                "formula": "z = q / (beta * R)",
                "variable": "z",
                "q_authority": (
                    "Phase 27G-C raw Float64 PAR surface-light density"
                ),
                "z_equals_one_meaning": (
                    "expected-equivalent neutral-reference exposure; not a leaf target"
                ),
                "back_beta_used_for_coloring": True,
            },
            "transport_correction": False,
            "raw_q_modified": False,
        },
        "metrics": {
            "available": list(METRIC_ORDER),
            "incident_par": "blue + green + orange + red incident density",
            "absorbed_par": "blue + green + orange + red absorbed density",
            "far_red_excluded": True,
        },
        "palettes": palette_contract(),
        "legends": statistics,
        "calibration_limitations": list(
            _FRONT_LOCAL_PATCH_CALIBRATION.limitations
        ),
        "scientific_artifact_boundary": {
            "aggregation_schema_id": FSPM_AGGREGATION_SCHEMA_ID,
            "aggregation_schema_version": aggregation_schema_version,
            "aggregation_metadata_sha256": (
                aggregation_publication.metadata_artifact.sha256
            ),
            "patch_float64_artifact": dict(patch_record),
            "room_summary_artifact": dict(room_record),
            "transport_schema_id": JUVENILE_MULTISPECTRAL_SCHEMA_ID,
            "transport_schema_version": transport.get("schema_version"),
            "transport_metadata_sha256": multispectral.metadata_artifact.sha256,
            "executed_band_order": list(executed_band_order),
            "far_red_executed": executed_band_order == BAND_ORDER,
            "compact_receiver_index_sha256": compact_record["sha256"],
            "material_coefficient_authorities": material_authorities,
            "raw_float64_values_modified": False,
            "far_red_read_into_display_values": False,
        },
        "topology": {
            "canonical_profile_id": PROFILE_ID,
            "topology_sha256": CALIBRATION_TOPOLOGY_SHA256,
            "receivers_sha256": CALIBRATION_RECEIVERS_SHA256,
            "counts": expected_counts,
            "local_patches_per_plant": PATCHES_PER_PLANT,
            "global_patch_equation": (
                "global_patch_index = 192 * plant_index + local_patch_index"
            ),
            "front_receiver_equation": (
                "384 * plant_index + 2 * local_patch_index"
            ),
            "coefficient_is_average_over_all_64_plants": True,
            "coefficient_position_dependence": False,
            "instance_mapping": "instance_id equals canonical plant_index",
            "front_back_selection": "fragment gl_FrontFacing selects front or back",
            "layout_plan_hash": natural_fit.plan_hash,
            "ordering": "Y-major/X-minor plants; plant-major canonical patches",
        },
        "display_artifact": {
            **_artifact_record(values_artifact),
            "component_type": "float32",
            "byte_order": "little-endian",
            "stride_bytes": SURFACE_FLUX_VALUES_STRIDE_BYTES,
            "row_count": expected_counts["patches"],
            "record_layout": (
                "front_incident_par, back_incident_par, "
                "front_absorbed_par, back_absorbed_par"
            ),
            "texture_layout": {
                "format": "RGBA32F",
                "width": PATCHES_PER_PLANT,
                "height": plant_count,
                "texel_count": expected_counts["patches"],
                "bounded_maximum_plant_count": MAX_DISPLAY_PLANT_COUNT,
            },
            "channel_semantics": "raw q; neither front u nor back z",
            "channel_units": "umol/m^2/s",
            "raw_q_float32_derivative": True,
            "raw_q_modified": False,
            "normalization_applied_to_artifact": False,
        },
        "failure_policy": {
            "invalid_or_unavailable": "neutral plant material",
            "clear_stale_gpu_resources": True,
            "partial_scientific_coloring_allowed": False,
        },
    }
    if display_calibration is not None:
        if display_selection is None or calibration_artifact is None:
            raise SurfaceFluxDisplayError(
                "authenticated display-calibration selection is incomplete."
            )
        manifest = display_calibration.manifest
        source = _mapping(manifest.get("source_authority"), "C3 source authority")
        metadata.update(
            {
                "schema_version": AUTHENTICATED_SURFACE_FLUX_DISPLAY_SCHEMA_VERSION,
                "quality_family": {
                    **display_selection.to_dict(),
                    "selected_profile_id": DISPLAY_CALIBRATION_RESOURCE_ID,
                    "calibration_source_quality": display_selection.coefficient_family,
                    "family_id": (
                        "d5-c3-standard-display-family-v1"
                        if display_selection.coefficient_family == "standard"
                        else "d5-c3-quality-display-family-v1"
                    ),
                    "proxy": display_selection.display_only_proxy,
                    "proxy_rationale": (
                        None
                        if not display_selection.display_only_proxy
                        else (
                            f"{quality.title()} uses {display_selection.coefficient_family.title()} "
                            "coefficients as a display-only proxy; raw transport is unchanged."
                        )
                    ),
                },
                "quality_family_mapping": display_quality_dispatch_contract(),
                "selected_calibration_profile": {
                    "resource_id": DISPLAY_CALIBRATION_RESOURCE_ID,
                    "coefficient_family": display_selection.coefficient_family,
                    "estimator": DISPLAY_CALIBRATION_ESTIMATOR_ID,
                    "coefficient_count_per_block": PATCHES_PER_PLANT,
                    "block_order": [
                        f"{side}_{metric}"
                        for side, metric in DISPLAY_CALIBRATION_BLOCK_ORDER
                    ],
                    "all_cells_available": True,
                    "threshold_masking_applied": False,
                },
                "calibration_provenance": {
                    "resource_schema_id": manifest["schema_id"],
                    "resource_schema_version": manifest["schema_version"],
                    "resource_id": manifest["resource_id"],
                    "purpose": manifest["purpose"],
                    "estimator": manifest["estimator"],
                    "c2_report_sha256": source["c2_report"]["sha256"],
                    "c2_completion_sha256": source["c2_completion"]["sha256"],
                    "topology_sha256": D5_TOPOLOGY_SHA256,
                    "receivers_sha256": D5_RECEIVERS_SHA256,
                    "sampling_profile_id": D5_SAMPLING_PROFILE_ID,
                    "d5_b1_modified": False,
                },
                "display_equation": {
                    "formula": (
                        "u[p,k,s,m] = q[p,k,s,m] / "
                        "(gamma[coefficient_family,s,m,k] * R)"
                    ),
                    "variable": "u",
                    "q_authority": "Phase 27G-C raw Float64 PAR surface-light density",
                    "gamma_estimator": DISPLAY_CALIBRATION_ESTIMATOR_ID,
                    "reference": "authenticated achieved Stage A mean",
                    "raw_rgba_order": [
                        "front_incident", "back_incident",
                        "front_absorbed", "back_absorbed",
                    ],
                    "coefficient_block_order": [
                        "front_incident", "front_absorbed",
                        "back_incident", "back_absorbed",
                    ],
                    "explicit_order_remapping": [0, 2, 1, 3],
                    "transport_correction": False,
                    "raw_q_modified": False,
                },
                "palettes": authenticated_palette_contract(),
                "calibration_resource": {
                    **_artifact_record(calibration_artifact),
                    "resource_id": DISPLAY_CALIBRATION_RESOURCE_ID,
                    "component_type": "float64",
                    "byte_order": "little-endian",
                    "stride_bytes": 8,
                    "coefficient_count": DISPLAY_CALIBRATION_COEFFICIENT_COUNT,
                    "family_order": ["standard", "quality"],
                    "block_order": [
                        "front_incident", "front_absorbed",
                        "back_incident", "back_absorbed",
                    ],
                    "values_per_block": PATCHES_PER_PLANT,
                },
                "calibration_availability": {
                    "available_cell_count": DISPLAY_CALIBRATION_COEFFICIENT_COUNT,
                    "masked_cell_count": 0,
                    "all_authenticated_finite_positive_cells_available": True,
                    "repeatability_threshold_applied": False,
                    "a2_failure_mask_applied": False,
                },
                "calibration_limitations": [
                    "Display-only calibration; raw Radiance transport is unchanged.",
                    "Direct uses Standard and Rigorous uses Quality only as declared display proxies.",
                    "No repeatability, drift, R-squared, magnitude, or percentage mask is applied.",
                ],
            }
        )
        metadata["topology"].update(
            {
                "topology_sha256": D5_TOPOLOGY_SHA256,
                "receivers_sha256": D5_RECEIVERS_SHA256,
                "sampling_profile_id": D5_SAMPLING_PROFILE_ID,
                "back_receiver_equation": (
                    "384 * plant_index + 2 * local_patch_index + 1"
                ),
            }
        )
    metadata_artifact = _artifact(
        (
            SURFACE_FLUX_METADATA_FILENAME
            if display_calibration is None
            else AUTHENTICATED_SURFACE_FLUX_METADATA_FILENAME
        ),
        _json_bytes(metadata),
    )
    return SurfaceFluxDisplayPublication(
        metadata=metadata,
        viewer_artifacts=SurfaceFluxViewerArtifacts(
            metadata=metadata_artifact,
            patch_values=values_artifact,
            calibration_coefficients=calibration_artifact,
        ),
    )


def calibration_provenance() -> dict[str, object]:
    return {
        "report_schema_id": CALIBRATION_REPORT_SCHEMA_ID,
        "report_schema_version": CALIBRATION_REPORT_SCHEMA_VERSION,
        "original_d1_experiment_status": "complete_fail",
        "original_d1_acceptance_pass": False,
        "original_d1_outcome_must_not_be_rewritten": True,
        "original_cross_quality_convergence_rewritten": False,
        "repository_revision": CALIBRATION_REPOSITORY_REVISION,
        "configuration_sha256": CALIBRATION_CONFIGURATION_SHA256,
        "source_model_id": CALIBRATION_SOURCE_MODEL_ID,
        "source_definition_sha256": CALIBRATION_SOURCE_DEFINITION_SHA256,
        "calibration_scene_sha256": CALIBRATION_SCENE_SHA256,
        "topology_sha256": CALIBRATION_TOPOLOGY_SHA256,
        "receivers_sha256": CALIBRATION_RECEIVERS_SHA256,
        "material_plan_sha256": CALIBRATION_MATERIAL_PLAN_SHA256,
        "source_scope": "open-boundary uniform upper-hemisphere neutral field",
        "front_local_patch_calibration": {
            "schema_id": FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_ID,
            "schema_version": FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_VERSION,
            "resource_sha256": FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_SHA256,
            "combined_float64_little_endian_sha256": (
                FRONT_LOCAL_PATCH_COMBINED_SHA256
            ),
        },
    }


def palette_contract() -> dict[str, object]:
    return {
        "front": {
            "palette_id": (
                "surface-flux-front-neutral-local-patch-blue-to-red-8-v1"
            ),
            "variable": "u",
            "anchors_u": list(FRONT_LOCAL_PATCH_ANCHORS),
            "colors": [
                {"name": name, "srgb_hex": color}
                for name, color in FRONT_PALETTE
            ],
            "green_interval_u": [0.89, 1.14],
            "continuous_interpolation": True,
            "shared_across_metrics_systems_and_quality_families": True,
            "per_run_extrema_normalization": False,
        },
        "back": {
            "palette_id": "surface-flux-blue-to-red-8-v1",
            "variable": "z",
            "anchors_z": list(BACK_ANCHORS),
            "colors": [
                {"name": name, "srgb_hex": color}
                for name, color in BACK_PALETTE
            ],
            "continuous_interpolation": True,
            "shared_across_metrics_systems_and_quality_families": True,
            "per_run_extrema_normalization": False,
        },
    }


def authenticated_palette_contract() -> dict[str, object]:
    """Return the fixed, side-specific schema-v3 normalized-u palettes."""

    palettes = palette_contract()
    return {
        "front": palettes["front"],
        "back": {
            "palette_id": SCHEMA_V3_BACK_PALETTE_ID,
            "variable": "u",
            "anchors_u": list(SCHEMA_V3_BACK_ANCHORS),
            "colors": [
                {"name": name, "srgb_hex": color}
                for name, color in SCHEMA_V3_BACK_PALETTE
            ],
            "continuous_interpolation": True,
            "shared_across_metrics_systems_and_quality_families": True,
            "per_run_extrema_normalization": False,
        },
    }


def build_front_legend_contract(
    *,
    metric: str,
    profile: CalibrationProfile,
    reference: SurfaceFluxReference,
    raw_values: Sequence[float],
    physical_areas_m2: Sequence[float],
) -> dict[str, object]:
    """Build the local-patch front legend without changing raw q."""

    if metric not in METRIC_ORDER:
        raise SurfaceFluxDisplayError("front legend metric is unsupported.")
    if (
        len(raw_values) == 0
        or len(raw_values) != len(physical_areas_m2)
        or len(raw_values) % PATCHES_PER_PLANT != 0
    ):
        raise SurfaceFluxDisplayError(
            "front legend values are not complete canonical plant-major patches."
        )
    values = tuple(
        _nonnegative_finite("raw surface flux", item) for item in raw_values
    )
    areas = tuple(
        _positive_finite("physical patch area", item)
        for item in physical_areas_m2
    )
    gamma_metric = profile.front_metric(metric)
    gamma = gamma_metric.coefficients
    if len(gamma) != PATCHES_PER_PLANT:
        raise SurfaceFluxDisplayError(
            "front legend local-patch coefficient count is incompatible."
        )
    normalized = tuple(
        _nonnegative_finite(
            "front local-patch normalized surface flux",
            value
            / (
                gamma[global_patch_index % PATCHES_PER_PLANT]
                * reference.value_umol_m2_s
            ),
        )
        for global_patch_index, value in enumerate(values)
    )
    below = tuple(
        index
        for index, value in enumerate(normalized)
        if value < FRONT_LOCAL_PATCH_ANCHORS[0]
    )
    above = tuple(
        index
        for index, value in enumerate(normalized)
        if value > FRONT_LOCAL_PATCH_ANCHORS[-1]
    )
    total_area = math.fsum(areas)
    below_area = math.fsum(areas[index] for index in below)
    above_area = math.fsum(areas[index] for index in above)
    rule = "q_anchor[k] = u_anchor * gamma[k] * R"
    beta = profile.coefficient(metric, "front")
    return {
        "metric": metric,
        "side": "front",
        "units": "umol/m^2/s",
        "variable": "u",
        "gamma_profile_id": profile.profile_id,
        "coefficient_hash": gamma_metric.float64_little_endian_sha256,
        "reference": reference.to_dict(),
        "raw_minimum_q": min(values),
        "raw_maximum_q": max(values),
        "physical_threshold_rule": rule,
        "beta_provenance": {
            "beta": beta,
            "used_for_coloring": False,
        },
        "clipping": {
            "lower_bound_u": FRONT_LOCAL_PATCH_ANCHORS[0],
            "upper_bound_u": FRONT_LOCAL_PATCH_ANCHORS[-1],
            "below_count": len(below),
            "above_count": len(above),
            "below_physical_area_m2": below_area,
            "above_physical_area_m2": above_area,
            "below_physical_area_fraction": below_area / total_area,
            "above_physical_area_fraction": above_area / total_area,
            "total_physical_one_sided_area_m2": total_area,
            "raw_values_modified_by_clipping": False,
        },
        "anchors": [
            {
                "u": u_value,
                "color_name": FRONT_PALETTE[index][0],
                "srgb_hex": FRONT_PALETTE[index][1],
                "q_threshold_rule": rule,
                "q_minimum_umol_m2_s": (
                    u_value * min(gamma) * reference.value_umol_m2_s
                ),
                "q_maximum_umol_m2_s": (
                    u_value * max(gamma) * reference.value_umol_m2_s
                ),
            }
            for index, u_value in enumerate(FRONT_LOCAL_PATCH_ANCHORS)
        ],
        "u_equals_one_meaning": (
            "Expected-equivalent exposure for the same canonical local patch "
            "under the fixed neutral upper-hemisphere calibration; not a leaf "
            "target and not raw-q uniformity."
        ),
    }


def build_legend_contract(
    *,
    metric: str,
    side: str,
    beta: float,
    reference: SurfaceFluxReference,
    raw_values: Sequence[float],
    physical_areas_m2: Sequence[float],
) -> dict[str, object]:
    """Build one fixed-anchor physical legend without changing raw q."""

    if metric not in METRIC_ORDER or side not in SIDE_ORDER:
        raise SurfaceFluxDisplayError("legend metric or side is unsupported.")
    coefficient = _positive_finite("calibration beta", beta)
    if len(raw_values) == 0 or len(raw_values) != len(physical_areas_m2):
        raise SurfaceFluxDisplayError("legend values and areas are incomplete.")
    values = tuple(_nonnegative_finite("raw surface flux", item) for item in raw_values)
    areas = tuple(
        _positive_finite("physical patch area", item)
        for item in physical_areas_m2
    )
    anchors = LEGACY_FRONT_ANCHORS if side == "front" else BACK_ANCHORS
    thresholds = tuple(
        value * coefficient * reference.value_umol_m2_s for value in anchors
    )
    lower = thresholds[0]
    upper = thresholds[-1]
    below = tuple(index for index, value in enumerate(values) if value < lower)
    above = tuple(index for index, value in enumerate(values) if value > upper)
    total_area = math.fsum(areas)
    below_area = math.fsum(areas[index] for index in below)
    above_area = math.fsum(areas[index] for index in above)
    return {
        "metric": metric,
        "side": side,
        "units": "umol/m^2/s",
        "beta": coefficient,
        "reference": reference.to_dict(),
        "raw_minimum_q": min(values),
        "raw_maximum_q": max(values),
        "clipping": {
            "lower_bound_z": anchors[0],
            "upper_bound_z": anchors[-1],
            "below_count": len(below),
            "above_count": len(above),
            "below_physical_area_m2": below_area,
            "above_physical_area_m2": above_area,
            "below_physical_area_fraction": below_area / total_area,
            "above_physical_area_fraction": above_area / total_area,
            "total_physical_one_sided_area_m2": total_area,
            "raw_values_modified_by_clipping": False,
        },
        "anchors": [
            {
                "z": z_value,
                "q_umol_m2_s": threshold,
                "color_name": BACK_PALETTE[index][0],
                "srgb_hex": BACK_PALETTE[index][1],
            }
            for index, (z_value, threshold) in enumerate(
                zip(anchors, thresholds, strict=True)
            )
        ],
        "z_equals_one_meaning": (
            "expected-equivalent neutral-reference exposure; not a leaf target"
        ),
    }


def _validate_scientific_contract(
    *,
    run_id: str,
    quality: str,
    natural_fit: NaturalFitLayoutPlan,
    expected_counts: Mapping[str, int],
    transport: Mapping[str, object],
    aggregation: Mapping[str, object],
    compact: Mapping[str, object],
    patch_record: Mapping[str, object],
    sampling_profile_id: str = REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID,
    calibration_topology_sha256: str = CALIBRATION_TOPOLOGY_SHA256,
    calibration_receivers_sha256: str = CALIBRATION_RECEIVERS_SHA256,
) -> list[dict[str, object]]:
    transport_version = transport.get("schema_version")
    aggregation_version = aggregation.get("schema_version")
    if (
        transport.get("schema_id") != JUVENILE_MULTISPECTRAL_SCHEMA_ID
        or transport_version
        not in {
            LEGACY_JUVENILE_MULTISPECTRAL_SCHEMA_VERSION,
            JUVENILE_MULTISPECTRAL_SCHEMA_VERSION,
        }
        or aggregation.get("schema_id") != FSPM_AGGREGATION_SCHEMA_ID
        or aggregation_version
        not in {
            LEGACY_FSPM_AGGREGATION_SCHEMA_VERSION,
            FSPM_AGGREGATION_SCHEMA_VERSION,
        }
        or (transport_version, aggregation_version) not in {(2, 1), (3, 2)}
    ):
        raise SurfaceFluxDisplayError("surface-flux source schema is incompatible.")
    if (
        transport.get("run_id") != run_id
        or aggregation.get("run_id") != run_id
        or transport.get("source_state_id") != aggregation.get("source_state_id")
        or aggregation.get("transport_metadata_sha256") is None
    ):
        raise SurfaceFluxDisplayError("surface-flux run or source-state link is invalid.")
    source_planning = _mapping(transport.get("source_planning"), "source planning")
    if source_planning.get("quality_profile") != quality:
        raise SurfaceFluxDisplayError(
            "evaluated quality disagrees with Phase 27G-C transport."
        )
    executed_band_order = tuple(transport.get("band_order", ()))
    if (
        executed_band_order not in (PAR_BAND_ORDER, BAND_ORDER)
        or transport.get("par_band_order") != list(PAR_BAND_ORDER)
        or aggregation.get("band_order") != list(executed_band_order)
        or aggregation.get("par_band_order") != list(PAR_BAND_ORDER)
        or transport.get("far_red_preserved_separately") is not True
        or aggregation.get("far_red_preserved_separately") is not True
        or (
            transport_version == LEGACY_JUVENILE_MULTISPECTRAL_SCHEMA_VERSION
            and executed_band_order != BAND_ORDER
        )
        or (
            transport_version == JUVENILE_MULTISPECTRAL_SCHEMA_VERSION
            and (
                transport.get("executed_band_order")
                != list(executed_band_order)
                or aggregation.get("executed_band_order")
                != list(executed_band_order)
                or transport.get("include_far_red")
                is not (executed_band_order == BAND_ORDER)
                or aggregation.get("include_far_red")
                is not (executed_band_order == BAND_ORDER)
                or transport.get("far_red_executed")
                is not (executed_band_order == BAND_ORDER)
                or aggregation.get("far_red_executed")
                is not (executed_band_order == BAND_ORDER)
            )
        )
    ):
        raise SurfaceFluxDisplayError("PAR identity or far-red exclusion is invalid.")
    if aggregation.get("counts") != dict(expected_counts):
        raise SurfaceFluxDisplayError("aggregation counts do not match the viewer.")
    plant = _mapping(transport.get("plant"), "transport plant")
    if (
        plant.get("profile_id") != PROFILE_ID
        or plant.get("layout_plan_hash") != natural_fit.plan_hash
        or plant.get("global_counts") != dict(expected_counts)
        or plant.get("plant_count") != expected_counts["plants"]
        or plant.get("ordering")
        != "Y-major/X-minor plants; plant-major receivers; front then back"
        or plant.get("geometry_included_in_scientific_transport") is not True
    ):
        raise SurfaceFluxDisplayError("transport geometry-layout identity is invalid.")
    canonical_counts = {
        "leaves": LEAVES_PER_PLANT,
        "faces": FACES_PER_PLANT,
        "patches": PATCHES_PER_PLANT,
        "receivers": RECEIVERS_PER_PLANT,
    }
    if plant.get("canonical_counts_per_plant") != canonical_counts:
        raise SurfaceFluxDisplayError("canonical transport counts are invalid.")

    topology = _mapping(compact.get("canonical_topology"), "canonical topology")
    compact_scene = _mapping(compact.get("scene"), "compact scene")
    plant_sampling = plant.get("sampling_profile_id")
    topology_sampling = topology.get("sampling_profile_id")
    scene_sampling = compact_scene.get("sampling_profile_id")
    if any(
        value is not None
        and value != sampling_profile_id
        for value in (plant_sampling, topology_sampling, scene_sampling)
    ):
        raise SurfaceFluxDisplayError(
            "surface-flux display is unavailable: the sampling profile is not "
            "covered by the frozen Phase 27G-D2 calibration."
        )
    ordering = _mapping(compact.get("ordering"), "compact ordering")
    equations = _mapping(
        compact.get("global_index_equations"), "global index equations"
    )
    if (
        compact.get("schema_id") != COMPACT_RECEIVER_INDEX_SCHEMA_ID
        or compact.get("schema_version") != COMPACT_RECEIVER_INDEX_SCHEMA_VERSION
        or topology.get("counts") != canonical_counts
        or topology.get("topology_sha256") != calibration_topology_sha256
        or topology.get("receivers_sha256") != calibration_receivers_sha256
        or compact.get("counts") != dict(expected_counts)
        or compact_scene.get("profile_id") != PROFILE_ID
        or compact_scene.get("layout_plan_hash") != natural_fit.plan_hash
        or ordering.get("plants") != "Y-major/X-minor"
        or ordering.get("trace_row_mapping")
        != "trace row i equals global receiver index i"
        or "front then back" not in str(ordering.get("receivers"))
        or equations.get("patch")
        != "192 * plant_index + local_patch_index"
        or equations.get("front_receiver")
        != "384 * plant_index + 2 * local_patch_index"
        or equations.get("back_receiver")
        != "384 * plant_index + 2 * local_patch_index + 1"
    ):
        raise SurfaceFluxDisplayError(
            "topology, receiver identity, side order, or global mapping is invalid."
        )

    patch_schema = _mapping(
        _mapping(aggregation.get("binary_schemas"), "binary schemas").get("patch"),
        "patch binary schema",
    )
    patch_struct, _patch_indices, patch_float_fields = (
        binary_contract_for_band_order(executed_band_order)["patch"]
    )
    expected_patch_schema: dict[str, object] = {
        "byte_order": "little-endian",
        "stride_bytes": patch_struct.size,
        "index_component_type": "uint64",
        "index_fields": list(PATCH_INDEX_FIELDS),
        "value_component_type": "float64",
        "value_fields": list(patch_float_fields),
        "record_order": "canonical global identity order",
    }
    if aggregation_version == FSPM_AGGREGATION_SCHEMA_VERSION:
        expected_patch_schema["index_field_offsets_bytes"] = {
            field: index * 8 for index, field in enumerate(PATCH_INDEX_FIELDS)
        }
        expected_patch_schema["value_field_offsets_bytes"] = {
            field: (len(PATCH_INDEX_FIELDS) + index) * 8
            for index, field in enumerate(patch_float_fields)
        }
    if patch_schema != expected_patch_schema:
        raise SurfaceFluxDisplayError("Phase 27G-C patch binary schema is invalid.")
    if (
        patch_record.get("row_count") != expected_counts["patches"]
        or patch_record.get("stride_bytes") != patch_struct.size
        or patch_record.get("byte_length")
        != expected_counts["patches"] * patch_struct.size
    ):
        raise SurfaceFluxDisplayError("Phase 27G-C patch count or stride is invalid.")

    bands = transport.get("bands")
    authorities = aggregation.get("material_coefficient_authorities")
    raw_authorities = aggregation.get("raw_receiver_authorities")
    if (
        not isinstance(bands, list)
        or not isinstance(authorities, list)
        or not isinstance(raw_authorities, list)
        or len(bands) != len(executed_band_order)
        or len(authorities) != len(executed_band_order)
        or len(raw_authorities) != len(executed_band_order)
    ):
        raise SurfaceFluxDisplayError("material or receiver authority is incomplete.")
    validated: list[dict[str, object]] = []
    for expected_band, band, authority, raw in zip(
        executed_band_order, bands, authorities, raw_authorities, strict=True
    ):
        band_map = _mapping(band, "transport band")
        authority_map = _mapping(authority, "material authority")
        raw_map = _mapping(raw, "raw receiver authority")
        receiver = _mapping(band_map.get("receiver_values"), "band receivers")
        if (
            band_map.get("band_id") != expected_band
            or authority_map.get("band_id") != expected_band
            or authority_map.get("material_sha256")
            != band_map.get("material_sha256")
            or authority_map.get("raw_receiver_sha256") != receiver.get("sha256")
            or raw_map.get("sha256") != receiver.get("sha256")
            or receiver.get("row_count") != expected_counts["receivers"]
            or not _valid_sha256(authority_map.get("material_provenance_sha256"))
        ):
            raise SurfaceFluxDisplayError(
                "material authority or raw receiver hash is inconsistent."
            )
        validated.append(
            {
                "band_id": expected_band,
                "material_sha256": authority_map["material_sha256"],
                "material_provenance_sha256": authority_map[
                    "material_provenance_sha256"
                ],
                "raw_receiver_sha256": receiver["sha256"],
            }
        )
    return validated


def _convert_patch_values(
    path: Path,
    *,
    expected_counts: Mapping[str, int],
    selection: QualityFamilySelection,
    reference: SurfaceFluxReference,
    display_calibration: AuthenticatedSurfaceFluxDisplayCalibration | None = None,
    display_family: str | None = None,
    sampling_profile_id: str = REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID,
    patch_struct: struct.Struct = binary_contract_for_band_order(BAND_ORDER)[
        "patch"
    ][0],
    patch_float_fields: tuple[str, ...] = binary_contract_for_band_order(
        BAND_ORDER
    )["patch"][2],
) -> tuple[bytes, dict[str, object]]:
    field_positions = {
        key: patch_float_fields.index(field) + len(PATCH_INDEX_FIELDS)
        for key, field in _PATCH_VALUE_FIELDS.items()
    }
    # D2 coefficients and patch-area authentication are frozen to legacy sampling.
    canonical = generate_rex_juvenile_preheading_plant(
        legacy_rex_juvenile_preheading_config()
        if sampling_profile_id == REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID
        else RexJuvenilePreheadingConfig(sampling_profile_id=sampling_profile_id)
    )
    leaf_by_id = {leaf.leaf_id: index for index, leaf in enumerate(canonical.leaves)}
    expected_local_leaf = tuple(
        leaf_by_id[patch.leaf_id] for patch in canonical.patches
    )
    expected_areas = tuple(patch.area_m2 for patch in canonical.patches)
    raw_values: dict[str, list[float]] = {
        key: [] for key in _PATCH_VALUE_FIELDS
    }
    areas: list[float] = []
    raw_float32 = bytearray()
    with path.open("rb") as handle:
        for expected_global_patch in range(expected_counts["patches"]):
            chunk = handle.read(patch_struct.size)
            if len(chunk) != patch_struct.size:
                raise SurfaceFluxDisplayError("Phase 27G-C patch table is truncated.")
            record = patch_struct.unpack(chunk)
            global_patch, plant_index, global_leaf, local_patch = record[:4]
            expected_plant = expected_global_patch // PATCHES_PER_PLANT
            expected_local_patch = expected_global_patch % PATCHES_PER_PLANT
            if (
                global_patch != expected_global_patch
                or plant_index != expected_plant
                or local_patch != expected_local_patch
                or global_leaf
                != expected_plant * LEAVES_PER_PLANT
                + expected_local_leaf[expected_local_patch]
            ):
                raise SurfaceFluxDisplayError(
                    "Phase 27G-C canonical patch, plant, or leaf order is invalid."
                )
            area = _positive_finite("physical patch area", record[4])
            if not math.isclose(
                area,
                expected_areas[expected_local_patch],
                rel_tol=0.0,
                abs_tol=1e-15,
            ):
                raise SurfaceFluxDisplayError(
                    "Phase 27G-C physical patch area changed from canonical topology."
                )
            values = tuple(
                _nonnegative_finite("PAR patch density", record[field_positions[key]])
                for key in _PATCH_VALUE_FIELDS
            )
            try:
                rounded = struct.pack("<ffff", *values)
            except (OverflowError, struct.error) as exc:
                raise SurfaceFluxDisplayError(
                    "PAR patch density cannot be represented by the bounded display payload."
                ) from exc
            if not all(math.isfinite(value) for value in struct.unpack("<ffff", rounded)):
                raise SurfaceFluxDisplayError(
                    "Float32 display conversion produced a non-finite value."
                )
            raw_float32.extend(rounded)
            areas.append(area)
            for key, value in zip(_PATCH_VALUE_FIELDS, values, strict=True):
                raw_values[key].append(value)
        if handle.read(1):
            raise SurfaceFluxDisplayError("Phase 27G-C patch table has extra records.")
    legends: dict[str, object] = {}
    for metric in METRIC_ORDER:
        coefficient_metric = metric.removesuffix("_par")
        if display_calibration is not None:
            if display_family is None:
                raise SurfaceFluxDisplayError(
                    "display-calibration coefficient family is missing."
                )
            legends[metric] = {
                side: build_local_patch_legend_contract(
                    metric=metric,
                    side=side,
                    gamma=display_calibration.coefficients(
                        family=display_family,
                        side=side,
                        metric=coefficient_metric,
                    ),
                    reference=reference,
                    raw_values=raw_values[f"{side}_{coefficient_metric}"],
                    physical_areas_m2=areas,
                )
                for side in SIDE_ORDER
            }
            continue
        legends[metric] = {
            "front": build_front_legend_contract(
                metric=metric,
                profile=selection.profile,
                reference=reference,
                raw_values=raw_values[
                    f"front_{metric.removesuffix('_par')}"
                ],
                physical_areas_m2=areas,
            ),
            "back": build_legend_contract(
                metric=metric,
                side="back",
                beta=selection.profile.coefficient(metric, "back"),
                reference=reference,
                raw_values=raw_values[
                    f"back_{metric.removesuffix('_par')}"
                ],
                physical_areas_m2=areas,
            ),
        }
    return bytes(raw_float32), legends


def build_local_patch_legend_contract(
    *,
    metric: str,
    side: str,
    gamma: Sequence[float],
    reference: SurfaceFluxReference,
    raw_values: Sequence[float],
    physical_areas_m2: Sequence[float],
) -> dict[str, object]:
    """Build one v3 all-cell local-patch legend without modifying raw q."""

    if metric not in METRIC_ORDER or side not in SIDE_ORDER:
        raise SurfaceFluxDisplayError("v3 legend metric or side is unsupported.")
    coefficients = tuple(_positive_finite("local-patch gamma", value) for value in gamma)
    values = tuple(_nonnegative_finite("raw surface flux", value) for value in raw_values)
    areas = tuple(_positive_finite("physical patch area", value) for value in physical_areas_m2)
    if (
        len(coefficients) != PATCHES_PER_PLANT
        or not values
        or len(values) != len(areas)
        or len(values) % PATCHES_PER_PLANT != 0
    ):
        raise SurfaceFluxDisplayError("v3 legend coefficient/value scope is incomplete.")
    normalized = tuple(
        value
        / (
            coefficients[index % PATCHES_PER_PLANT]
            * reference.value_umol_m2_s
        )
        for index, value in enumerate(values)
    )
    anchors = (
        FRONT_LOCAL_PATCH_ANCHORS
        if side == "front"
        else SCHEMA_V3_BACK_ANCHORS
    )
    palette = FRONT_PALETTE if side == "front" else SCHEMA_V3_BACK_PALETTE
    below = tuple(index for index, value in enumerate(normalized) if value < anchors[0])
    above = tuple(index for index, value in enumerate(normalized) if value > anchors[-1])
    total_area = math.fsum(areas)
    below_area = math.fsum(areas[index] for index in below)
    above_area = math.fsum(areas[index] for index in above)
    rule = "q_anchor[k] = u_anchor * gamma[k] * R"
    return {
        "metric": metric,
        "side": side,
        "units": "umol/m^2/s",
        "variable": "u",
        "coefficient_estimator": DISPLAY_CALIBRATION_ESTIMATOR_ID,
        "coefficient_count": PATCHES_PER_PLANT,
        "all_cells_available": True,
        "availability_mask_applied": False,
        "reference": reference.to_dict(),
        "raw_minimum_q": min(values),
        "raw_maximum_q": max(values),
        "physical_threshold_rule": rule,
        "clipping": {
            "lower_bound_u": anchors[0],
            "upper_bound_u": anchors[-1],
            "below_count": len(below),
            "above_count": len(above),
            "below_physical_area_m2": below_area,
            "above_physical_area_m2": above_area,
            "below_physical_area_fraction": below_area / total_area,
            "above_physical_area_fraction": above_area / total_area,
            "total_physical_one_sided_area_m2": total_area,
            "raw_values_modified_by_clipping": False,
        },
        "anchors": [
            {
                "u": anchor,
                "color_name": palette[index][0],
                "srgb_hex": palette[index][1],
                "q_threshold_rule": rule,
                "q_minimum_umol_m2_s": (
                    anchor * min(coefficients) * reference.value_umol_m2_s
                ),
                "q_maximum_umol_m2_s": (
                    anchor * max(coefficients) * reference.value_umol_m2_s
                ),
            }
            for index, anchor in enumerate(anchors)
        ],
        "u_equals_one_meaning": (
            "expected-equivalent exposure for the same canonical local patch; "
            "not a leaf target"
        ),
    }


def _validate_room_summary(
    room_summary: Mapping[str, object],
    *,
    run_id: str,
    expected_counts: Mapping[str, int],
    legends: Mapping[str, object],
) -> None:
    if (
        room_summary.get("schema_id")
        != "fspm-optics.fspm-surface-light-room-summary"
        or room_summary.get("schema_version") not in {1, 2}
        or room_summary.get("run_id") != run_id
        or room_summary.get("counts") != dict(expected_counts)
        or room_summary.get("target_or_reference_cap_applied") is not False
        or room_summary.get("symmetry_reconstruction_applied") is not False
    ):
        raise SurfaceFluxDisplayError("Phase 27G-C room summary is incompatible.")
    expected_area = _positive_finite(
        "modeled physical one-sided leaf area",
        room_summary.get("modeled_physical_one_sided_leaf_area_m2"),
    )
    for metric in METRIC_ORDER:
        metric_legends = _mapping(legends.get(metric), f"{metric} legends")
        for side in SIDE_ORDER:
            legend = _mapping(metric_legends.get(side), f"{metric} {side} legend")
            clipping = _mapping(legend.get("clipping"), "legend clipping")
            legend_area = _positive_finite(
                "legend total physical one-sided area",
                clipping.get("total_physical_one_sided_area_m2"),
            )
            if not math.isclose(
                legend_area,
                expected_area,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise SurfaceFluxDisplayError(
                    "display legend area disagrees with the Phase 27G-C room summary."
                )


def _validated_json_artifact(
    root: Path,
    record: Mapping[str, object],
    *,
    expected_role: str,
) -> dict[str, object]:
    path = _validated_file_artifact(root, record, expected_role=expected_role)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SurfaceFluxDisplayError(
            f"{expected_role} is not valid UTF-8 JSON."
        ) from exc
    if not isinstance(payload, dict):
        raise SurfaceFluxDisplayError(f"{expected_role} must be a JSON object.")
    return payload


def _validated_file_artifact(
    root: Path,
    record: Mapping[str, object],
    *,
    expected_role: str,
) -> Path:
    if record.get("role") != expected_role:
        raise SurfaceFluxDisplayError(f"{expected_role} role is missing.")
    relative = record.get("path")
    if (
        not isinstance(relative, str)
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
        or not _valid_sha256(record.get("sha256"))
        or isinstance(record.get("byte_length"), bool)
        or not isinstance(record.get("byte_length"), int)
        or record["byte_length"] < 0
    ):
        raise SurfaceFluxDisplayError(f"{expected_role} record is invalid.")
    path = root / relative
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SurfaceFluxDisplayError(f"{expected_role} artifact is missing.") from exc
    if (
        not resolved.is_relative_to(root)
        or not resolved.is_file()
        or path.is_symlink()
        or resolved.stat().st_size != record["byte_length"]
        or _sha256_file(resolved) != record["sha256"]
    ):
        raise SurfaceFluxDisplayError(f"{expected_role} artifact hash failed.")
    return resolved


def _inventory_record(
    metadata: Mapping[str, object], role: str
) -> dict[str, object]:
    inventory = metadata.get("ordered_artifact_inventory")
    if not isinstance(inventory, list):
        raise SurfaceFluxDisplayError("aggregation artifact inventory is missing.")
    matches = [
        dict(item)
        for item in inventory
        if isinstance(item, Mapping) and item.get("role") == role
    ]
    if len(matches) != 1:
        raise SurfaceFluxDisplayError(f"aggregation role {role!r} is ambiguous.")
    return matches[0]


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SurfaceFluxDisplayError(f"{label} is missing or invalid.")
    return value


def _artifact(filename: str, data: bytes) -> BinaryDisplayArtifact:
    return BinaryDisplayArtifact(
        filename=filename,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _artifact_record(artifact: BinaryDisplayArtifact) -> dict[str, object]:
    return {
        "filename": artifact.filename,
        "byte_length": artifact.byte_size,
        "sha256": artifact.sha256,
    }


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            dict(payload),
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _positive_finite(label: str, value: object) -> float:
    number = _nonnegative_finite(label, value)
    if number <= 0.0:
        raise SurfaceFluxDisplayError(f"{label} must be positive.")
    return number


def _nonnegative_finite(label: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SurfaceFluxDisplayError(f"{label} must be finite and non-negative.")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise SurfaceFluxDisplayError(f"{label} must be finite and non-negative.")
    return number


__all__ = [
    "AUTHENTICATED_SURFACE_FLUX_DISPLAY_SCHEMA_VERSION",
    "AUTHENTICATED_SURFACE_FLUX_METADATA_FILENAME",
    "BACK_ANCHORS",
    "BACK_PALETTE",
    "CALIBRATION_PROFILES",
    "FRONT_ANCHORS",
    "FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_ID",
    "FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_VERSION",
    "FRONT_LOCAL_PATCH_COEFFICIENT_ORDER",
    "FRONT_LOCAL_PATCH_COMBINED_BYTE_LENGTH",
    "FRONT_LOCAL_PATCH_COMBINED_ORDER",
    "FRONT_LOCAL_PATCH_ANCHORS",
    "FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_NAME",
    "FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_SHA256",
    "FRONT_LOCAL_PATCH_COMBINED_SHA256",
    "FRONT_LOCAL_PATCH_PROFILE_HASHES",
    "FRONT_PALETTE",
    "LEGACY_FRONT_ANCHORS",
    "METRIC_ORDER",
    "PALETTE",
    "PATCHES_PER_PLANT",
    "QUALITY_PROFILE",
    "SCHEMA_V3_BACK_ANCHORS",
    "SCHEMA_V3_BACK_PALETTE",
    "SCHEMA_V3_BACK_PALETTE_ID",
    "STANDARD_PROFILE",
    "SURFACE_FLUX_DISPLAY_SCHEMA_ID",
    "SURFACE_FLUX_DISPLAY_SCHEMA_VERSION",
    "SURFACE_FLUX_METADATA_FILENAME",
    "SURFACE_FLUX_VALUES_FILENAME",
    "FrontLocalPatchMetric",
    "SurfaceFluxDisplayError",
    "SurfaceFluxDisplayPublication",
    "SurfaceFluxReference",
    "authenticated_palette_contract",
    "build_front_legend_contract",
    "build_legend_contract",
    "build_local_patch_legend_contract",
    "build_surface_flux_display_publication",
    "calibration_provenance",
    "palette_contract",
    "quality_family_mapping_contract",
    "select_quality_family",
    "select_authenticated_achieved_surface_flux_reference",
    "select_surface_flux_reference",
]
