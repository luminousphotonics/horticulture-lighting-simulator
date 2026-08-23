"""Strict Phase 27G-D5-B1 optimized coefficient-resource loading.

This module owns only the candidate coefficient resource.  It does not load a
packaged production resource, select palettes, publish surface-flux metadata,
or enable optimized coloring.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import struct
from types import MappingProxyType
from typing import Mapping, Sequence

from fspm_optics.application.surface_flux_calibration import (
    SurfaceFluxCalibrationError,
    _hash_json,
    _valid_sha256,
    _validated_timestamp,
)
from fspm_optics.application.fspm_science import (
    COEFFICIENT_CLOSURE_ABS_TOLERANCE,
)
from fspm_optics.application.surface_flux_recalibration import (
    D5_BAND_ORDER,
    D5_DEFERRED_DISPLAY_QUALITY_MAPPING,
    D5_EXPECTED_PLANT_COUNT,
    D5_EXPERIMENT_ID,
    D5_IDENTITY_VERSION,
    D5_PATCHES_PER_PLANT,
    D5_QUALITY_ORDER,
    D5_RECEIVERS_PER_PLANT,
    D5_RECEIVERS_SHA256,
    D5_REFERENCE_LEVELS_UMOL_M2_S,
    D5_SAMPLING_PROFILE_ID,
    D5_TOPOLOGY_SHA256,
    SurfaceFluxRecalibrationConfig,
    _build_scientific_inputs,
    _d5_scene_identity,
    _quality_option_identities,
    canonical_neutral_source_definition,
)
from fspm_optics.optics.rex_material_plan import (
    render_radiance_trans_material,
)
from fspm_optics.plants.radiance_scene_export import (
    DEFAULT_LEAF_MATERIAL_MODIFIER,
)


OPTIMIZED_CALIBRATION_RESOURCE_SCHEMA_ID = (
    "fspm-optics.optimized-surface-flux-local-patch-calibration-resource"
)
OPTIMIZED_CALIBRATION_RESOURCE_SCHEMA_VERSION = 1
OPTIMIZED_CALIBRATION_RESOURCE_ID = (
    "phase27g-d5-b1-optimized-surface-flux-local-patch-calibration-v1"
)
OPTIMIZED_CALIBRATION_MANIFEST_NAME = (
    "optimized-surface-flux-local-patch-calibration.v1.json"
)
OPTIMIZED_CALIBRATION_PAYLOAD_NAME = (
    "optimized-surface-flux-local-patch-coefficients.v1.f64le.bin"
)

COMPONENT_TYPE = "IEEE-754 binary64"
BYTE_ORDER = "little-endian"
FLOAT64_STRIDE_BYTES = 8
COEFFICIENT_UNITS = "dimensionless q/R"
COEFFICIENT_CHANNEL_ORDER = (
    ("front", "incident"),
    ("front", "absorbed"),
    ("back", "incident"),
    ("back", "absorbed"),
)
COEFFICIENT_ARRAY_COUNT = len(D5_QUALITY_ORDER) * len(
    COEFFICIENT_CHANNEL_ORDER
)
COEFFICIENT_VALUES_PER_ARRAY = D5_PATCHES_PER_PLANT
COEFFICIENT_ARRAY_BYTES = COEFFICIENT_VALUES_PER_ARRAY * FLOAT64_STRIDE_BYTES
COEFFICIENT_VALUE_COUNT = COEFFICIENT_ARRAY_COUNT * COEFFICIENT_VALUES_PER_ARRAY
COEFFICIENT_PAYLOAD_BYTES = COEFFICIENT_VALUE_COUNT * FLOAT64_STRIDE_BYTES
COEFFICIENT_SHAPE = [
    len(D5_QUALITY_ORDER),
    len(COEFFICIENT_CHANNEL_ORDER),
    D5_PATCHES_PER_PLANT,
]
COEFFICIENT_ORDERING = (
    "family-major Standard, Quality, Rigorous; within family front incident, "
    "front absorbed, back incident, back absorbed; within array canonical "
    "local_patch_index 0..191"
)

QUALITY_ORDER = ("direct", *D5_QUALITY_ORDER)
QUALITY_MAPPING = MappingProxyType(
    {
        "direct": MappingProxyType(
            {
                "coefficient_family": "standard",
                "calibrated_family": False,
                "proxy": True,
                "proxy_scope": "display-normalization-only",
                "raw_transport_proxied": False,
            }
        ),
        "standard": MappingProxyType(
            {
                "coefficient_family": "standard",
                "calibrated_family": True,
                "proxy": False,
                "proxy_scope": None,
                "raw_transport_proxied": False,
            }
        ),
        "quality": MappingProxyType(
            {
                "coefficient_family": "quality",
                "calibrated_family": True,
                "proxy": False,
                "proxy_scope": None,
                "raw_transport_proxied": False,
            }
        ),
        "rigorous": MappingProxyType(
            {
                "coefficient_family": "rigorous",
                "calibrated_family": True,
                "proxy": False,
                "proxy_scope": None,
                "raw_transport_proxied": False,
            }
        ),
    }
)

DEFERRED_SCOPE = (
    "palette selection",
    "near-zero availability policy",
    "surface-flux metadata v3 activation",
    "backend or viewer optimized coloring",
    "held-out Proposed acceptance",
    "held-out Conventional acceptance",
    "production packaging and enablement",
)


class OptimizedCalibrationResourceError(RuntimeError):
    """A candidate resource identity, byte, or lookup contract is invalid."""


@dataclass(frozen=True, slots=True)
class OptimizedDisplayQualitySelection:
    """One exact display-only evaluated-quality coefficient selection."""

    evaluated_quality: str
    coefficient_family: str
    calibrated_family: bool
    proxy: bool
    proxy_scope: str | None
    raw_transport_proxied: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "evaluated_quality": self.evaluated_quality,
            "coefficient_family": self.coefficient_family,
            "calibrated_family": self.calibrated_family,
            "proxy": self.proxy,
            "proxy_scope": self.proxy_scope,
            "raw_transport_proxied": self.raw_transport_proxied,
        }


@dataclass(frozen=True, slots=True)
class OptimizedSurfaceFluxCalibration:
    """Authenticated candidate manifest and immutable coefficient arrays."""

    manifest: Mapping[str, object]
    payload: bytes
    arrays: Mapping[tuple[str, str, str], tuple[float, ...]]

    def select_display_quality(
        self, quality: object
    ) -> OptimizedDisplayQualitySelection:
        """Resolve only the future display-normalization quality mapping."""

        if not isinstance(quality, str) or quality not in QUALITY_MAPPING:
            raise OptimizedCalibrationResourceError(
                "optimized coefficient quality identity is unsupported."
            )
        mapping = QUALITY_MAPPING[quality]
        return OptimizedDisplayQualitySelection(
            evaluated_quality=quality,
            coefficient_family=str(mapping["coefficient_family"]),
            calibrated_family=bool(mapping["calibrated_family"]),
            proxy=bool(mapping["proxy"]),
            proxy_scope=(
                None
                if mapping["proxy_scope"] is None
                else str(mapping["proxy_scope"])
            ),
            raw_transport_proxied=bool(mapping["raw_transport_proxied"]),
        )

    def coefficient_for_family(
        self,
        *,
        family: object,
        side: object,
        metric: object,
        local_patch_index: object,
    ) -> float:
        identity = _coefficient_identity(family, side, metric, local_patch_index)
        try:
            return self.arrays[identity[:3]][identity[3]]
        except KeyError as exc:
            raise OptimizedCalibrationResourceError(
                "optimized coefficient family, side, or metric is unsupported."
            ) from exc

    def coefficient_for_display_quality(
        self,
        *,
        quality: object,
        side: object,
        metric: object,
        local_patch_index: object,
    ) -> float:
        """Look up gamma through the authenticated display-only mapping."""

        selection = self.select_display_quality(quality)
        return self.coefficient_for_family(
            family=selection.coefficient_family,
            side=side,
            metric=metric,
            local_patch_index=local_patch_index,
        )


def expected_quality_mapping_payload() -> dict[str, object]:
    """Return the exact Direct/display and calibrated-family mapping."""

    return {
        "quality_order": list(QUALITY_ORDER),
        "mappings": {
            quality: {
                "evaluated_quality": quality,
                **dict(QUALITY_MAPPING[quality]),
            }
            for quality in QUALITY_ORDER
        },
        "direct_calibrated": False,
        "direct_future_display_mapping": dict(
            D5_DEFERRED_DISPLAY_QUALITY_MAPPING
        ),
        "direct_mapping_scope": "display-normalization-only",
        "raw_direct_transport_proxied": False,
    }


def expected_array_order() -> tuple[tuple[str, str, str], ...]:
    """Return the immutable family/channel order."""

    return tuple(
        (family, side, metric)
        for family in D5_QUALITY_ORDER
        for side, metric in COEFFICIENT_CHANNEL_ORDER
    )


def fixed_scientific_identity(
    material_coefficient_authorities: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Build fixed, non-Radiance identities recorded by a candidate resource."""

    config = SurfaceFluxRecalibrationConfig(output_directory=Path.cwd())
    scene, material_plan = _build_scientific_inputs(config)
    scene_identity = _d5_scene_identity(config, scene, material_plan)
    source = canonical_neutral_source_definition()
    authorities = _validate_material_authorities(
        material_coefficient_authorities,
        material_plan=material_plan,
    )
    return {
        "d5_identity_version": D5_IDENTITY_VERSION,
        "sampling_profile_id": D5_SAMPLING_PROFILE_ID,
        "topology_sha256": D5_TOPOLOGY_SHA256,
        "receivers_sha256": D5_RECEIVERS_SHA256,
        "scene": {
            "scene_hash": scene_identity["plant"]["scene_hash"],
            "layout_plan_hash": scene_identity["plant"]["layout_plan_hash"],
            "calibration_scene_sha256": scene_identity[
                "calibration_scene_sha256"
            ],
            "d5_scene_identity_sha256": scene_identity[
                "d5_scene_identity_sha256"
            ],
        },
        "source": {
            "source_model_id": source["source_model_id"],
            "source_definition_sha256": source["source_definition_sha256"],
        },
        "material": {
            "material_plan_sha256": _hash_json(material_plan.to_payload()),
            "coefficient_authorities": authorities,
        },
        "quality_option_identities": _quality_option_identities(),
        "quality_order": list(D5_QUALITY_ORDER),
        "reference_level_order_umol_m2_s": list(
            D5_REFERENCE_LEVELS_UMOL_M2_S
        ),
        "band_order": list(D5_BAND_ORDER),
        "counts": {
            "families": len(D5_QUALITY_ORDER),
            "channels_per_family": len(COEFFICIENT_CHANNEL_ORDER),
            "plants_in_calibration": D5_EXPECTED_PLANT_COUNT,
            "local_patches_per_plant": D5_PATCHES_PER_PLANT,
            "receivers_per_plant": D5_RECEIVERS_PER_PLANT,
            "scene": scene.counts.to_payload(),
        },
        "canonical_receiver_order": (
            "plant-major; local_patch_index 0..191; front then back"
        ),
    }


def load_optimized_surface_flux_calibration(
    resource_directory: str | Path,
) -> OptimizedSurfaceFluxCalibration:
    """Load exactly one two-file candidate resource from a real directory."""

    root = Path(resource_directory).expanduser()
    if root.is_symlink():
        raise OptimizedCalibrationResourceError(
            "optimized calibration resource root must not be a symbolic link."
        )
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise OptimizedCalibrationResourceError(
            "optimized calibration resource directory is unavailable."
        ) from exc
    if not root.is_dir() or root.is_symlink():
        raise OptimizedCalibrationResourceError(
            "optimized calibration resource root must be a real directory."
        )
    expected_names = {
        OPTIMIZED_CALIBRATION_MANIFEST_NAME,
        OPTIMIZED_CALIBRATION_PAYLOAD_NAME,
    }
    entries = tuple(root.iterdir())
    if {path.name for path in entries} != expected_names or any(
        path.is_symlink() or not path.is_file() for path in entries
    ):
        raise OptimizedCalibrationResourceError(
            "optimized calibration resource inventory is incompatible."
        )
    manifest_path = root / OPTIMIZED_CALIBRATION_MANIFEST_NAME
    payload_path = root / OPTIMIZED_CALIBRATION_PAYLOAD_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload = payload_path.read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OptimizedCalibrationResourceError(
            "optimized calibration resource is unreadable or malformed."
        ) from exc
    if not isinstance(manifest, dict):
        raise OptimizedCalibrationResourceError(
            "optimized calibration manifest must be a JSON object."
        )
    return validate_optimized_surface_flux_calibration(manifest, payload)


def validate_optimized_surface_flux_calibration(
    manifest: Mapping[str, object],
    payload: bytes,
) -> OptimizedSurfaceFluxCalibration:
    """Authenticate a candidate manifest and its canonical Float64 payload."""

    if not isinstance(payload, bytes):
        raise OptimizedCalibrationResourceError(
            "optimized coefficient payload must be immutable bytes."
        )

    expected_fields = {
        "schema_id",
        "schema_version",
        "resource_id",
        "status",
        "packaged_resource",
        "production_enabled",
        "source_authority",
        "scientific_identity",
        "coefficient_payload",
        "arrays",
        "quality_mapping",
        "derivation_scope",
        "byte_preservation",
        "deferred",
    }
    _require_fields(manifest, expected_fields, "candidate resource manifest")
    if (
        manifest.get("schema_id") != OPTIMIZED_CALIBRATION_RESOURCE_SCHEMA_ID
        or manifest.get("schema_version")
        != OPTIMIZED_CALIBRATION_RESOURCE_SCHEMA_VERSION
        or manifest.get("resource_id") != OPTIMIZED_CALIBRATION_RESOURCE_ID
        or manifest.get("status") != "candidate"
        or manifest.get("packaged_resource") is not False
        or manifest.get("production_enabled") is not False
    ):
        raise OptimizedCalibrationResourceError(
            "optimized calibration resource identity or candidate status is invalid."
        )

    _validate_source_authority(manifest.get("source_authority"))
    scientific = _mapping(
        manifest.get("scientific_identity"), "scientific identity"
    )
    material = _mapping(scientific.get("material"), "material identity")
    authorities = material.get("coefficient_authorities")
    if not isinstance(authorities, list):
        raise OptimizedCalibrationResourceError(
            "material coefficient authorities are missing."
        )
    if dict(scientific) != fixed_scientific_identity(authorities):
        raise OptimizedCalibrationResourceError(
            "optimized sampling, scene, source, material, or quality identity changed."
        )
    if manifest.get("quality_mapping") != expected_quality_mapping_payload():
        raise OptimizedCalibrationResourceError(
            "optimized quality mapping or Direct proxy scope is invalid."
        )
    if manifest.get("derivation_scope") != {
        "calibrated_families": list(D5_QUALITY_ORDER),
        "direct_calibration_present": False,
        "far_red_present": False,
        "held_out_systems_present": {
            "proposed": False,
            "conventional": False,
        },
        "evaluated_system_inputs_used": False,
    }:
        raise OptimizedCalibrationResourceError(
            "optimized calibration derivation scope is invalid."
        )
    if manifest.get("deferred") != list(DEFERRED_SCOPE):
        raise OptimizedCalibrationResourceError(
            "optimized calibration deferred scope is incompatible."
        )

    payload_record = _mapping(
        manifest.get("coefficient_payload"), "coefficient payload"
    )
    expected_payload_fields = {
        "role",
        "filename",
        "media_type",
        "schema",
        "component_type",
        "byte_order",
        "stride_bytes",
        "units",
        "shape",
        "value_count",
        "byte_length",
        "sha256",
        "ordering",
    }
    _require_fields(
        payload_record, expected_payload_fields, "coefficient payload"
    )
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    source_a2 = _mapping(
        _mapping(manifest.get("source_authority"), "source authority").get(
            "d5_a2"
        ),
        "D5-A2 authority",
    )
    source_combined = _mapping(
        source_a2.get("combined_coefficient_payload"),
        "D5-A2 combined coefficient payload authority",
    )
    if (
        payload_record.get("role")
        != "combined_canonical_local_patch_surface_flux_coefficients"
        or payload_record.get("filename")
        != OPTIMIZED_CALIBRATION_PAYLOAD_NAME
        or payload_record.get("media_type") != "application/octet-stream"
        or payload_record.get("schema")
        != "headerless fixed-stride Float64 array"
        or payload_record.get("component_type") != COMPONENT_TYPE
        or payload_record.get("byte_order") != BYTE_ORDER
        or payload_record.get("stride_bytes") != FLOAT64_STRIDE_BYTES
        or payload_record.get("units") != COEFFICIENT_UNITS
        or payload_record.get("shape") != COEFFICIENT_SHAPE
        or payload_record.get("value_count") != COEFFICIENT_VALUE_COUNT
        or payload_record.get("byte_length") != COEFFICIENT_PAYLOAD_BYTES
        or payload_record.get("sha256") != payload_sha256
        or payload_record.get("ordering") != COEFFICIENT_ORDERING
        or len(payload) != COEFFICIENT_PAYLOAD_BYTES
        or source_combined.get("byte_length") != COEFFICIENT_PAYLOAD_BYTES
        or source_combined.get("sha256") != payload_sha256
    ):
        raise OptimizedCalibrationResourceError(
            "optimized coefficient payload size, hash, shape, or ordering is invalid."
        )

    array_records = manifest.get("arrays")
    if not isinstance(array_records, list) or len(array_records) != COEFFICIENT_ARRAY_COUNT:
        raise OptimizedCalibrationResourceError(
            "optimized coefficient array inventory is incomplete."
        )
    decoded: dict[tuple[str, str, str], tuple[float, ...]] = {}
    expected_order = expected_array_order()
    for order_index, (record_value, identity) in enumerate(
        zip(array_records, expected_order, strict=True)
    ):
        record = _mapping(record_value, "coefficient array record")
        _require_fields(
            record,
            {
                "order_index",
                "family",
                "side",
                "metric",
                "byte_offset",
                "byte_length",
                "value_count",
                "units",
                "slice_sha256",
            },
            "coefficient array record",
        )
        family, side, metric = identity
        offset = order_index * COEFFICIENT_ARRAY_BYTES
        slice_bytes = payload[offset : offset + COEFFICIENT_ARRAY_BYTES]
        if (
            record.get("order_index") != order_index
            or record.get("family") != family
            or record.get("side") != side
            or record.get("metric") != metric
            or record.get("byte_offset") != offset
            or record.get("byte_length") != COEFFICIENT_ARRAY_BYTES
            or record.get("value_count") != COEFFICIENT_VALUES_PER_ARRAY
            or record.get("units") != COEFFICIENT_UNITS
            or record.get("slice_sha256")
            != hashlib.sha256(slice_bytes).hexdigest()
        ):
            raise OptimizedCalibrationResourceError(
                "optimized coefficient array order, offset, or slice hash is invalid."
            )
        values = struct.unpack(
            f"<{COEFFICIENT_VALUES_PER_ARRAY}d", slice_bytes
        )
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise OptimizedCalibrationResourceError(
                "optimized coefficient payload contains a negative or non-finite value."
            )
        decoded[identity] = tuple(values)

    preservation = manifest.get("byte_preservation")
    if preservation != {
        "source_combined_payload_sha256": payload_sha256,
        "candidate_payload_sha256": payload_sha256,
        "individual_arrays_concatenated_byte_for_byte": True,
        "combined_payload_copied_without_transformation": True,
        "coefficient_values_reencoded": False,
        "coefficient_values_modified": False,
    }:
        raise OptimizedCalibrationResourceError(
            "optimized coefficient byte-preservation declaration is invalid."
        )
    return OptimizedSurfaceFluxCalibration(
        manifest=MappingProxyType(dict(manifest)),
        payload=bytes(payload),
        arrays=MappingProxyType(decoded),
    )


def _coefficient_identity(
    family: object,
    side: object,
    metric: object,
    local_patch_index: object,
) -> tuple[str, str, str, int]:
    if (
        not isinstance(family, str)
        or family not in D5_QUALITY_ORDER
        or not isinstance(side, str)
        or not isinstance(metric, str)
        or (side, metric) not in COEFFICIENT_CHANNEL_ORDER
        or isinstance(local_patch_index, bool)
        or not isinstance(local_patch_index, int)
        or not 0 <= local_patch_index < D5_PATCHES_PER_PLANT
    ):
        raise OptimizedCalibrationResourceError(
            "optimized coefficient lookup identity is unsupported."
        )
    return family, side, metric, local_patch_index


def _validate_source_authority(value: object) -> None:
    authority = _mapping(value, "source authority")
    _require_fields(authority, {"d5_a1", "d5_a2"}, "source authority")
    d5_a1 = _mapping(authority.get("d5_a1"), "D5-A1 authority")
    _require_fields(
        d5_a1,
        {
            "experiment_id",
            "completion_sha256",
            "configuration_sha256",
            "completion_created_at_utc",
        },
        "D5-A1 authority",
    )
    if (
        d5_a1.get("experiment_id") != D5_EXPERIMENT_ID
        or not _valid_sha256(d5_a1.get("completion_sha256"))
        or not _valid_sha256(d5_a1.get("configuration_sha256"))
    ):
        raise OptimizedCalibrationResourceError(
            "D5-A1 completion authority is invalid."
        )
    try:
        _validated_timestamp(d5_a1.get("completion_created_at_utc"))
    except (SurfaceFluxCalibrationError, ValueError) as exc:
        raise OptimizedCalibrationResourceError(
            "D5-A1 completion timestamp is invalid."
        ) from exc
    d5_a2 = _mapping(authority.get("d5_a2"), "D5-A2 authority")
    _require_fields(
        d5_a2,
        {
            "analysis_id",
            "completion",
            "report",
            "coefficient_manifest",
            "normalized_neutral_manifest",
            "combined_coefficient_payload",
            "promotion_eligible",
        },
        "D5-A2 authority",
    )
    if (
        d5_a2.get("analysis_id")
        != "phase27g-d5-a2-coefficient-analysis-v1"
        or d5_a2.get("promotion_eligible") is not True
    ):
        raise OptimizedCalibrationResourceError(
            "D5-A2 completion authority is ineligible or incompatible."
        )
    for name, filename in (
        (
            "completion",
            "surface-flux-coefficient-analysis-completion.v1.json",
        ),
        ("report", "surface-flux-coefficient-analysis.v1.json"),
        ("coefficient_manifest", "coefficient-payload-manifest.v1.json"),
        (
            "normalized_neutral_manifest",
            "normalized-neutral-evidence.v1.json",
        ),
        (
            "combined_coefficient_payload",
            "coefficients/all-coefficients.v1.f64le.bin",
        ),
    ):
        record = _mapping(d5_a2.get(name), f"D5-A2 {name}")
        _require_fields(
            record, {"filename", "byte_length", "sha256"}, f"D5-A2 {name}"
        )
        if (
            record.get("filename") != filename
            or isinstance(record.get("byte_length"), bool)
            or not isinstance(record.get("byte_length"), int)
            or record["byte_length"] <= 0
            or not _valid_sha256(record.get("sha256"))
        ):
            raise OptimizedCalibrationResourceError(
                f"D5-A2 {name} authority is invalid."
            )


def _validate_material_authorities(
    value: Sequence[Mapping[str, object]],
    *,
    material_plan: object,
) -> list[dict[str, object]]:
    if len(value) != len(D5_BAND_ORDER):
        raise OptimizedCalibrationResourceError(
            "material coefficient authority count is incompatible."
        )
    validated: list[dict[str, object]] = []
    for expected_band, record_value in zip(D5_BAND_ORDER, value, strict=True):
        record = _mapping(record_value, "material coefficient authority")
        try:
            expected_material = material_plan.material(expected_band)
        except (AttributeError, KeyError, ValueError) as exc:
            raise OptimizedCalibrationResourceError(
                "authoritative material plan is incompatible."
            ) from exc
        expected_coefficients = expected_material.source_interval.coefficients
        expected_material_sha256 = hashlib.sha256(
            render_radiance_trans_material(
                DEFAULT_LEAF_MATERIAL_MODIFIER,
                expected_material.parameters,
            ).encode("utf-8")
        ).hexdigest()
        expected_provenance_sha256 = _hash_json(expected_material.to_dict())
        expected_fields = {
            "band_id",
            "absorptance",
            "transmittance",
            "reflectance",
            "coefficient_sum",
            "closure_abs_tolerance",
            "material_sha256",
            "material_provenance_sha256",
        }
        _require_fields(
            record, expected_fields, "material coefficient authority"
        )
        coefficients = tuple(
            _finite_nonnegative(record.get(name), f"material {name}")
            for name in ("absorptance", "transmittance", "reflectance")
        )
        tolerance = _finite_nonnegative(
            record.get("closure_abs_tolerance"), "material closure tolerance"
        )
        if (
            record.get("band_id") != expected_band
            or coefficients
            != (
                expected_coefficients.absorptance,
                expected_coefficients.transmittance,
                expected_coefficients.reflectance,
            )
            or tolerance != COEFFICIENT_CLOSURE_ABS_TOLERANCE
            or record.get("material_sha256") != expected_material_sha256
            or record.get("material_provenance_sha256")
            != expected_provenance_sha256
            or record.get("coefficient_sum") != math.fsum(coefficients)
            or abs(math.fsum(coefficients) - 1.0) > tolerance
        ):
            raise OptimizedCalibrationResourceError(
                "material coefficient authority identity or closure is invalid."
            )
        validated.append(dict(record))
    return validated


def _finite_nonnegative(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise OptimizedCalibrationResourceError(f"{label} must be finite.")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise OptimizedCalibrationResourceError(f"{label} must be nonnegative.")
    return number


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise OptimizedCalibrationResourceError(f"{label} must be an object.")
    return value


def _require_fields(
    value: Mapping[str, object], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        raise OptimizedCalibrationResourceError(
            f"{label} field inventory is incompatible."
        )


__all__ = [
    "BYTE_ORDER",
    "COEFFICIENT_ARRAY_BYTES",
    "COEFFICIENT_CHANNEL_ORDER",
    "COEFFICIENT_ORDERING",
    "COEFFICIENT_PAYLOAD_BYTES",
    "COEFFICIENT_SHAPE",
    "COEFFICIENT_UNITS",
    "OPTIMIZED_CALIBRATION_MANIFEST_NAME",
    "OPTIMIZED_CALIBRATION_PAYLOAD_NAME",
    "OPTIMIZED_CALIBRATION_RESOURCE_ID",
    "OPTIMIZED_CALIBRATION_RESOURCE_SCHEMA_ID",
    "OPTIMIZED_CALIBRATION_RESOURCE_SCHEMA_VERSION",
    "OptimizedCalibrationResourceError",
    "OptimizedDisplayQualitySelection",
    "OptimizedSurfaceFluxCalibration",
    "expected_array_order",
    "expected_quality_mapping_payload",
    "fixed_scientific_identity",
    "load_optimized_surface_flux_calibration",
    "validate_optimized_surface_flux_calibration",
]
