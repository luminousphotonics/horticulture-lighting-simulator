"""Authenticated Phase 27G-D5-C3 display-calibration resources.

This module is deliberately independent of the historical D5-B1 promotion
decision.  It authenticates the pinned D5-C2 report and completion, extracts
every positive pooled Standard/Quality coefficient, and publishes a compact
display-only resource without invoking Radiance or changing raw transport.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import resources
import json
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile
from types import MappingProxyType
from typing import Mapping

DISPLAY_CALIBRATION_SCHEMA_ID = (
    "fspm-optics.authenticated-surface-flux-display-calibration-resource"
)
DISPLAY_CALIBRATION_SCHEMA_VERSION = 1
DISPLAY_CALIBRATION_RESOURCE_ID = (
    "phase27g-d5-c3-authenticated-surface-flux-display-calibration-v1"
)
DISPLAY_CALIBRATION_MANIFEST_NAME = (
    "surface-flux-display-calibration.v1.json"
)
DISPLAY_CALIBRATION_PAYLOAD_NAME = (
    "surface-flux-display-calibration-coefficients.v1.f64le.bin"
)
VIEWER_CALIBRATION_PAYLOAD_NAME = (
    "surface-flux/display-calibration-coefficients.v1.f64le.bin"
)

PINNED_C2_REPORT_SHA256 = (
    "df53a6425f6a6ee17667ccce04533f09c7f16c291a8f0894e5bc6d8e5c75e3ce"
)
PINNED_C2_COMPLETION_SHA256 = (
    "221c963fb34b86380624ee1daa88a29bdcd94f31730cb096fc2b2f24ca75e06a"
)

D5_SAMPLING_PROFILE_ID = (
    "rex_juvenile_surface_sampling_equal_area_cell_aligned_4x4_v1"
)
D5_TOPOLOGY_SHA256 = (
    "d59c27614c9b4fde3d60f8ebf090b56bbc6692d77baeed67753e8081ff70138f"
)
D5_RECEIVERS_SHA256 = (
    "e2f6606ba78642ffe6f9de0601d87c8f4e4197b66cb3ef9d284f8324e1ee305f"
)
D5_PATCHES_PER_PLANT = 192
D5_C2_ANALYSIS_ID = "phase27g-d5-c2-endpoint-repeatability-analysis-v1"
D5_C2_SCHEMA_ID = "fspm-optics.surface-flux-endpoint-repeatability-analysis"
D5_C2_COMPLETION_SCHEMA_ID = (
    "fspm-optics.surface-flux-endpoint-repeatability-analysis-completion"
)
D5_C2_SCHEMA_VERSION = 1
D5_C2_REPORT_NAME = "surface-flux-endpoint-repeatability-analysis.v1.json"
D5_C2_COMPLETION_NAME = (
    "surface-flux-endpoint-repeatability-analysis-completion.v1.json"
)

_C2_QUALITY_OPTION_IDENTITIES = MappingProxyType(
    {
        "quality": MappingProxyType(
            {
                "ambient_cache_policy": "job-and-trace-local",
                "ambient_evaluation_required": True,
                "base_radiance_options": (
                    "-ab", "5", "-ad", "2048", "-as", "512", "-aa", "0.12",
                    "-ar", "96", "-dj", "0.65", "-ds", "0.20", "-dt", "0.03",
                    "-dc", "0.85", "-dr", "3", "-lr", "12", "-lw", "5e-5",
                ),
                "fallback": False,
                "option_identity_sha256": (
                    "7efb5db9ea57963d6a6f56699ecaf0bec06e118e00f859676bc729362920695a"
                ),
                "proxy": False,
                "quality": "quality",
            }
        ),
        "standard": MappingProxyType(
            {
                "ambient_cache_policy": "job-and-trace-local",
                "ambient_evaluation_required": True,
                "base_radiance_options": (
                    "-ab", "3", "-ad", "512", "-as", "128", "-aa", "0.22",
                    "-ar", "48", "-dj", "0.35", "-ds", "0.40", "-dt", "0.08",
                    "-dc", "0.50", "-dr", "1", "-lr", "6", "-lw", "2e-4",
                ),
                "fallback": False,
                "option_identity_sha256": (
                    "349ee0216d50da7507e600e99fa7dfd457684200287f3928b6f5b27ce0cb7fc8"
                ),
                "proxy": False,
                "quality": "standard",
            }
        ),
    }
)

FAMILY_ORDER = ("standard", "quality")
BLOCK_ORDER = (
    ("front", "incident"),
    ("front", "absorbed"),
    ("back", "incident"),
    ("back", "absorbed"),
)
ESTIMATOR_ID = "pooled_four_observation_through_origin_gamma"
COMPONENT_TYPE = "IEEE-754 binary64"
BYTE_ORDER = "little-endian"
STRIDE_BYTES = 8
COEFFICIENT_COUNT = len(FAMILY_ORDER) * len(BLOCK_ORDER) * D5_PATCHES_PER_PLANT
PAYLOAD_BYTE_LENGTH = COEFFICIENT_COUNT * STRIDE_BYTES
COEFFICIENT_UNITS = "dimensionless q/R"
DISPLAY_PURPOSE = (
    "display-only normalized PAR surface-flux coloring; not scientific transport"
)
COEFFICIENT_ORDERING = (
    "family-major Standard, Quality; within family front incident, front absorbed, "
    "back incident, back absorbed; within block canonical local_patch_index 0..191"
)

QUALITY_DISPATCH = MappingProxyType(
    {
        "direct": MappingProxyType(
            {
                "coefficient_family": "standard",
                "exact_family_calibration": False,
                "display_only_proxy": True,
                "raw_transport_proxied": False,
            }
        ),
        "standard": MappingProxyType(
            {
                "coefficient_family": "standard",
                "exact_family_calibration": True,
                "display_only_proxy": False,
                "raw_transport_proxied": False,
            }
        ),
        "quality": MappingProxyType(
            {
                "coefficient_family": "quality",
                "exact_family_calibration": True,
                "display_only_proxy": False,
                "raw_transport_proxied": False,
            }
        ),
        "rigorous": MappingProxyType(
            {
                "coefficient_family": "quality",
                "exact_family_calibration": False,
                "display_only_proxy": True,
                "raw_transport_proxied": False,
            }
        ),
    }
)


class SurfaceFluxDisplayCalibrationError(RuntimeError):
    """C2 authentication, resource generation, or resource loading failed."""


@dataclass(frozen=True, slots=True)
class C2Authority:
    report_sha256: str = PINNED_C2_REPORT_SHA256
    completion_sha256: str = PINNED_C2_COMPLETION_SHA256


@dataclass(frozen=True, slots=True)
class SurfaceFluxDisplayCalibrationConfig:
    c2_input_directory: Path
    output_directory: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "c2_input_directory", Path(self.c2_input_directory))
        object.__setattr__(self, "output_directory", Path(self.output_directory))


@dataclass(frozen=True, slots=True)
class DisplayQualitySelection:
    evaluated_quality: str
    coefficient_family: str
    exact_family_calibration: bool
    display_only_proxy: bool
    raw_transport_proxied: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "evaluated_quality": self.evaluated_quality,
            "coefficient_family": self.coefficient_family,
            "exact_family_calibration": self.exact_family_calibration,
            "display_only_proxy": self.display_only_proxy,
            "raw_transport_proxied": self.raw_transport_proxied,
            "proxy_scope": (
                "display-calibration-only" if self.display_only_proxy else None
            ),
        }


@dataclass(frozen=True, slots=True)
class AuthenticatedSurfaceFluxDisplayCalibration:
    manifest: Mapping[str, object]
    payload: bytes
    arrays: Mapping[tuple[str, str, str], tuple[float, ...]]

    def select_quality(self, quality: object) -> DisplayQualitySelection:
        if not isinstance(quality, str) or quality not in QUALITY_DISPATCH:
            raise SurfaceFluxDisplayCalibrationError(
                "surface-flux display quality dispatch is invalid."
            )
        record = QUALITY_DISPATCH[quality]
        return DisplayQualitySelection(
            evaluated_quality=quality,
            coefficient_family=str(record["coefficient_family"]),
            exact_family_calibration=bool(record["exact_family_calibration"]),
            display_only_proxy=bool(record["display_only_proxy"]),
            raw_transport_proxied=bool(record["raw_transport_proxied"]),
        )

    def coefficients(
        self, *, family: str, side: str, metric: str
    ) -> tuple[float, ...]:
        try:
            return self.arrays[(family, side, metric)]
        except KeyError as exc:
            raise SurfaceFluxDisplayCalibrationError(
                "surface-flux display coefficient identity is invalid."
            ) from exc


def quality_dispatch_contract() -> dict[str, object]:
    return {
        quality: DisplayQualitySelection(
            quality,
            str(record["coefficient_family"]),
            bool(record["exact_family_calibration"]),
            bool(record["display_only_proxy"]),
            bool(record["raw_transport_proxied"]),
        ).to_dict()
        for quality, record in QUALITY_DISPATCH.items()
    }


def c2_quality_option_identity(quality: str) -> dict[str, object]:
    """Return the pinned C2 quality identity without importing execution code."""

    try:
        record = _C2_QUALITY_OPTION_IDENTITIES[quality]
    except KeyError as exc:
        raise SurfaceFluxDisplayCalibrationError(
            "D5-C2 quality-option identity is unsupported."
        ) from exc
    return {
        **record,
        "base_radiance_options": list(record["base_radiance_options"]),
    }


def generate_surface_flux_display_calibration(
    config: SurfaceFluxDisplayCalibrationConfig,
    *,
    authority: C2Authority = C2Authority(),
) -> AuthenticatedSurfaceFluxDisplayCalibration:
    """Authenticate C2 and atomically publish the two-file C3 resource."""

    input_root, output = _validated_locations(config)
    report_bytes = _authenticated_file(
        input_root / D5_C2_REPORT_NAME,
        authority.report_sha256,
        "D5-C2 report",
    )
    completion_bytes = _authenticated_file(
        input_root / D5_C2_COMPLETION_NAME,
        authority.completion_sha256,
        "D5-C2 completion",
    )
    report = _json_object(report_bytes, "D5-C2 report")
    completion = _json_object(completion_bytes, "D5-C2 completion")
    arrays, source_authorities = _authenticate_c2(
        report,
        completion,
        report_sha256=authority.report_sha256,
        report_byte_length=len(report_bytes),
    )
    payload = b"".join(
        struct.pack(f"<{D5_PATCHES_PER_PLANT}d", *arrays[identity])
        for identity in _expected_array_order()
    )
    if len(payload) != PAYLOAD_BYTE_LENGTH:
        raise SurfaceFluxDisplayCalibrationError(
            "C3 coefficient payload byte count is incomplete."
        )
    manifest = _build_manifest(
        payload,
        source_authorities=source_authorities,
        report_sha256=authority.report_sha256,
        completion_sha256=authority.completion_sha256,
    )
    stage = Path(tempfile.mkdtemp(prefix=".d5-c3-display-", dir=output.parent))
    try:
        _write_fsynced(stage / DISPLAY_CALIBRATION_PAYLOAD_NAME, payload)
        _write_fsynced(
            stage / DISPLAY_CALIBRATION_MANIFEST_NAME,
            format_surface_flux_display_calibration_json(manifest).encode("utf-8"),
        )
        _fsync_directory(stage)
        if output.exists() or output.is_symlink():
            raise SurfaceFluxDisplayCalibrationError(
                "C3 output appeared during atomic publication."
            )
        os.replace(stage, output)
        _fsync_directory(output.parent)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return load_surface_flux_display_calibration(output, authority=authority)


def load_surface_flux_display_calibration(
    resource_directory: str | Path,
    *,
    authority: C2Authority = C2Authority(),
) -> AuthenticatedSurfaceFluxDisplayCalibration:
    root = Path(resource_directory).expanduser()
    if root.is_symlink():
        raise SurfaceFluxDisplayCalibrationError(
            "display-calibration resource root must not be a symbolic link."
        )
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise SurfaceFluxDisplayCalibrationError(
            "display-calibration resource directory is unavailable."
        ) from exc
    expected = {DISPLAY_CALIBRATION_MANIFEST_NAME, DISPLAY_CALIBRATION_PAYLOAD_NAME}
    entries = tuple(root.iterdir()) if root.is_dir() else ()
    if {path.name for path in entries} != expected or any(
        path.is_symlink() or not path.is_file() for path in entries
    ):
        raise SurfaceFluxDisplayCalibrationError(
            "display-calibration resource inventory is incompatible."
        )
    try:
        manifest = json.loads(
            (root / DISPLAY_CALIBRATION_MANIFEST_NAME).read_text(encoding="utf-8")
        )
        payload = (root / DISPLAY_CALIBRATION_PAYLOAD_NAME).read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SurfaceFluxDisplayCalibrationError(
            "display-calibration resource is unreadable or malformed."
        ) from exc
    return validate_surface_flux_display_calibration(
        manifest, payload, authority=authority
    )


def load_packaged_surface_flux_display_calibration(
) -> AuthenticatedSurfaceFluxDisplayCalibration:
    """Load the generated package resource, failing closed when it is absent."""

    root = resources.files("fspm_optics").joinpath(
        "resources", "calibration", "d5-c3"
    )
    try:
        names = {entry.name for entry in root.iterdir()}
        if names != {
            DISPLAY_CALIBRATION_MANIFEST_NAME,
            DISPLAY_CALIBRATION_PAYLOAD_NAME,
        }:
            raise SurfaceFluxDisplayCalibrationError(
                "packaged D5-C3 display-calibration inventory is incompatible."
            )
        manifest_bytes = root.joinpath(DISPLAY_CALIBRATION_MANIFEST_NAME).read_bytes()
        payload = root.joinpath(DISPLAY_CALIBRATION_PAYLOAD_NAME).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise SurfaceFluxDisplayCalibrationError(
            "packaged D5-C3 display-calibration resource is unavailable."
        ) from exc
    manifest = _json_object(manifest_bytes, "packaged C3 manifest")
    return validate_surface_flux_display_calibration(manifest, payload)


def validate_surface_flux_display_calibration(
    manifest: object,
    payload: object,
    *,
    authority: C2Authority = C2Authority(),
) -> AuthenticatedSurfaceFluxDisplayCalibration:
    if not isinstance(manifest, Mapping) or not isinstance(payload, bytes):
        raise SurfaceFluxDisplayCalibrationError(
            "display-calibration manifest or payload type is invalid."
        )
    required = {
        "schema_id", "schema_version", "resource_id", "status", "purpose",
        "estimator", "source_authority", "scientific_identity",
        "quality_dispatch", "coefficient_payload", "arrays", "availability",
        "raw_transport", "historical_decisions",
    }
    if set(manifest) != required:
        raise SurfaceFluxDisplayCalibrationError(
            "display-calibration manifest field inventory is incompatible."
        )
    scientific = _mapping(manifest.get("scientific_identity"), "scientific identity")
    payload_record = _mapping(manifest.get("coefficient_payload"), "payload record")
    if (
        manifest.get("schema_id") != DISPLAY_CALIBRATION_SCHEMA_ID
        or manifest.get("schema_version") != DISPLAY_CALIBRATION_SCHEMA_VERSION
        or manifest.get("resource_id") != DISPLAY_CALIBRATION_RESOURCE_ID
        or manifest.get("status") != "authenticated_complete"
        or manifest.get("purpose") != DISPLAY_PURPOSE
        or manifest.get("estimator") != ESTIMATOR_ID
        or manifest.get("quality_dispatch") != quality_dispatch_contract()
        or manifest.get("availability") != {
            "policy": "all_authenticated_finite_positive_cells_available",
            "available_cell_count": COEFFICIENT_COUNT,
            "masked_cell_count": 0,
            "threshold_masking_applied": False,
        }
        or manifest.get("raw_transport") != {
            "proxied": False,
            "modified": False,
            "calibration_applies_to_display_only": True,
        }
        or manifest.get("historical_decisions") != {
            "d5_b1_modified": False,
            "d5_a2_failure_status_bypassed": False,
            "c3_is_additive_display_resource": True,
        }
        or scientific.get("sampling_profile_id") != D5_SAMPLING_PROFILE_ID
        or scientific.get("topology_sha256") != D5_TOPOLOGY_SHA256
        or scientific.get("receivers_sha256") != D5_RECEIVERS_SHA256
        or scientific.get("family_order") != list(FAMILY_ORDER)
        or scientific.get("block_order")
        != [f"{side}_{metric}" for side, metric in BLOCK_ORDER]
        or scientific.get("local_patch_indices")
        != {"first": 0, "last": 191, "count": D5_PATCHES_PER_PLANT}
        or payload_record.get("filename") != DISPLAY_CALIBRATION_PAYLOAD_NAME
        or payload_record.get("component_type") != COMPONENT_TYPE
        or payload_record.get("byte_order") != BYTE_ORDER
        or payload_record.get("stride_bytes") != STRIDE_BYTES
        or payload_record.get("units") != COEFFICIENT_UNITS
        or payload_record.get("value_count") != COEFFICIENT_COUNT
        or payload_record.get("byte_length") != PAYLOAD_BYTE_LENGTH
        or payload_record.get("ordering") != COEFFICIENT_ORDERING
        or payload_record.get("sha256") != hashlib.sha256(payload).hexdigest()
        or len(payload) != PAYLOAD_BYTE_LENGTH
    ):
        raise SurfaceFluxDisplayCalibrationError(
            "display-calibration identity, dispatch, or payload contract failed."
        )
    records = manifest.get("arrays")
    expected_order = _expected_array_order()
    if not isinstance(records, list) or len(records) != len(expected_order):
        raise SurfaceFluxDisplayCalibrationError(
            "display-calibration array inventory is incomplete."
        )
    arrays: dict[tuple[str, str, str], tuple[float, ...]] = {}
    block_bytes = D5_PATCHES_PER_PLANT * STRIDE_BYTES
    for index, (record_value, identity) in enumerate(
        zip(records, expected_order, strict=True)
    ):
        record = _mapping(record_value, "coefficient array")
        offset = index * block_bytes
        data = payload[offset : offset + block_bytes]
        values = struct.unpack(f"<{D5_PATCHES_PER_PLANT}d", data)
        family, side, metric = identity
        if (
            record != {
                "order_index": index,
                "family": family,
                "side": side,
                "metric": metric,
                "byte_offset": offset,
                "byte_length": block_bytes,
                "value_count": D5_PATCHES_PER_PLANT,
                "slice_sha256": hashlib.sha256(data).hexdigest(),
                "all_cells_available": True,
            }
            or any(not math.isfinite(value) or value <= 0.0 for value in values)
        ):
            raise SurfaceFluxDisplayCalibrationError(
                "display-calibration array order, hash, or positive-value gate failed."
            )
        arrays[identity] = tuple(values)
    _validate_manifest_source_authority(
        manifest.get("source_authority"), authority=authority
    )
    return AuthenticatedSurfaceFluxDisplayCalibration(
        manifest=MappingProxyType(dict(manifest)),
        payload=bytes(payload),
        arrays=MappingProxyType(arrays),
    )


def _authenticate_c2(
    report: Mapping[str, object],
    completion: Mapping[str, object],
    *,
    report_sha256: str,
    report_byte_length: int,
) -> tuple[dict[tuple[str, str, str], tuple[float, ...]], Mapping[str, object]]:
    expected_completion_fields = {
        "schema_id", "schema_version", "analysis_id", "status", "report",
        "source_authorities", "ordered_artifact_inventory", "atomic_publication",
        "input_directories_modified", "radiance_invoked",
        "production_calibration_resource_generated", "calibration_decision_made",
        "independent_random_seeding_claimed",
    }
    report_record = {
        "path": D5_C2_REPORT_NAME,
        "byte_length": report_byte_length,
        "sha256": report_sha256,
    }
    if (
        set(completion) != expected_completion_fields
        or completion.get("schema_id") != D5_C2_COMPLETION_SCHEMA_ID
        or completion.get("schema_version") != D5_C2_SCHEMA_VERSION
        or completion.get("analysis_id") != D5_C2_ANALYSIS_ID
        or completion.get("status") != "complete"
        or completion.get("report") != report_record
        or completion.get("ordered_artifact_inventory")
        != [{**report_record, "media_type": "application/json"}]
        or completion.get("atomic_publication")
        != "both JSON artifacts fsynced before same-filesystem directory rename"
        or completion.get("input_directories_modified") is not False
        or completion.get("radiance_invoked") is not False
        or completion.get("production_calibration_resource_generated") is not False
        or completion.get("calibration_decision_made") is not False
        or completion.get("independent_random_seeding_claimed") is not False
    ):
        raise SurfaceFluxDisplayCalibrationError(
            "D5-C2 completion identity or report authority is invalid."
        )
    authorities = _mapping(completion.get("source_authorities"), "C2 authorities")
    fixed = _mapping(report.get("fixed_identity"), "C2 fixed identity")
    authentication = _mapping(report.get("input_authentication"), "C2 authentication")
    summaries = _mapping(report.get("aggregate_summaries"), "C2 summaries")
    nonclaims = _mapping(report.get("explicit_non_claims"), "C2 non-claims")
    scope = _mapping(report.get("scope"), "C2 scope")
    if (
        report.get("schema_id") != D5_C2_SCHEMA_ID
        or report.get("schema_version") != D5_C2_SCHEMA_VERSION
        or report.get("analysis_id") != D5_C2_ANALYSIS_ID
        or report.get("status") != "complete"
        or report.get("source_authorities") != authorities
        or authentication.get("status")
        != "all_sources_authenticated_before_receiver_value_decode"
        or authentication.get("d5_a1_complete_authority_and_original_artifacts_validated")
        is not True
        or authentication.get("d5_a2_report_completion_inventory_and_coefficients_validated")
        is not True
        or authentication.get("d5_c1_configuration_outcome_jobs_and_traces_validated")
        is not True
        or authentication.get("declared_byte_lengths_and_sha256_validated") is not True
        or authentication.get("achieved_references_and_source_amplitudes_validated")
        is not True
        or authentication.get("original_to_repeat_pairing_validated") is not True
        or authentication.get("quality_option_identities_validated") is not True
        or authentication.get("sampling_topology_receiver_count_and_order_validated")
        is not True
        or authentication.get("stage_a_trace_count") != 0
        or authentication.get("stage_b_repetition_count") != 16
        or fixed.get("families") != ["quality", "standard"]
        or fixed.get("sides") != ["front", "back"]
        or fixed.get("metrics") != ["incident", "absorbed"]
        or fixed.get("band_order") != ["blue", "green", "orange", "red"]
        or fixed.get("plant_indices") != {"first": 0, "last": 63, "count": 64}
        or fixed.get("local_patch_indices")
        != {"first": 0, "last": 191, "count": D5_PATCHES_PER_PLANT}
        or fixed.get("canonical_receiver_order")
        != "plant-major; local_patch_index 0..191; front then back"
        or fixed.get("receiver_count_per_band") != 24_576
        or fixed.get("receiver_byte_length_per_band") != 196_608
        or fixed.get("replicates") != ["original_a1", "repeated_c1"]
        or fixed.get("requested_levels_umol_m2_s") != [250.0, 500.0]
        or fixed.get("sampling_profile_id") != D5_SAMPLING_PROFILE_ID
        or fixed.get("topology_sha256") != D5_TOPOLOGY_SHA256
        or fixed.get("receivers_sha256") != D5_RECEIVERS_SHA256
        or fixed.get("quality_option_identities")
        != [c2_quality_option_identity(family) for family in ("quality", "standard")]
        or summaries.get("cell_count") != COEFFICIENT_COUNT
        or nonclaims != {
            "availability_masks": False,
            "calibration_promotion_decision": False,
            "clipping_policy": False,
            "independent_random_seeding": False,
            "new_acceptance_thresholds": False,
            "palette_anchors": False,
            "production_calibration_suitability_decision": False,
            "production_resource_generated": False,
            "radiance_invoked": False,
        }
        or scope.get("analyzed_families") != ["quality", "standard"]
        or scope.get("direct_raw_transport_changed") is not False
        or scope.get("rigorous_raw_transport_changed") is not False
        or scope.get("raw_stage_c_science_changed") is not False
    ):
        raise SurfaceFluxDisplayCalibrationError(
            "D5-C2 schema, authentication, topology, receiver, or coefficient scope failed."
        )
    _validate_embedded_authorities(authorities)
    cells = report.get("detailed_cells")
    expected_cells = tuple(
        (family, side, metric, patch)
        for family in ("quality", "standard")
        for side, metric in BLOCK_ORDER
        for patch in range(D5_PATCHES_PER_PLANT)
    )
    if not isinstance(cells, list) or len(cells) != len(expected_cells):
        raise SurfaceFluxDisplayCalibrationError(
            "D5-C2 detailed coefficient cell count is incomplete."
        )
    decoded: dict[tuple[str, str, str], list[float]] = {
        identity: [] for identity in _expected_array_order()
    }
    for value, expected in zip(cells, expected_cells, strict=True):
        cell = _mapping(value, "D5-C2 detailed cell")
        family, side, metric, patch = expected
        estimates = _mapping(cell.get("coefficient_estimates"), "C2 estimates")
        coefficient = estimates.get(ESTIMATOR_ID)
        if (
            cell.get("family") != family
            or cell.get("side") != side
            or cell.get("metric") != metric
            or cell.get("local_patch_index") != patch
            or isinstance(coefficient, bool)
            or not isinstance(coefficient, int | float)
            or not math.isfinite(float(coefficient))
            or float(coefficient) <= 0.0
        ):
            raise SurfaceFluxDisplayCalibrationError(
                "D5-C2 coefficient identity or pooled positive-value gate failed."
            )
        decoded[(family, side, metric)].append(float(coefficient))
    return (
        {identity: tuple(values) for identity, values in decoded.items()},
        authorities,
    )


def _validate_embedded_authorities(authorities: Mapping[str, object]) -> None:
    if set(authorities) != {"d5_a1", "d5_a2", "d5_c1"}:
        raise SurfaceFluxDisplayCalibrationError(
            "D5-C2 embedded A1/A2/C1 authority chain is incomplete."
        )
    a1 = _mapping(authorities.get("d5_a1"), "D5-A1 authority")
    a2 = _mapping(authorities.get("d5_a2"), "D5-A2 authority")
    c1 = _mapping(authorities.get("d5_c1"), "D5-C1 authority")
    if (
        a1.get("experiment_id")
        != "phase27g-d5-a1-optimized-surface-flux-sweep-v3"
        or a1.get("status") != "complete"
        or a2.get("analysis_id") != "phase27g-d5-a2-coefficient-analysis-v1"
        or a2.get("status") != "complete"
        or c1.get("experiment_id")
        != "phase27g-d5-c1-endpoint-replication-v1"
        or c1.get("status") != "complete"
        or c1.get("stage_a_trace_count") != 0
        or c1.get("stage_b_trace_count") != 16
        or c1.get("nonidentical_repeated_artifact_count") != 16
        or c1.get("independently_seeded_sample_claimed") is not False
    ):
        raise SurfaceFluxDisplayCalibrationError(
            "D5-C2 embedded A1/A2/C1 authority identities are invalid."
        )
    for record, label in (
        (a1.get("completion"), "A1 completion"),
        (a2.get("completion"), "A2 completion"),
        (a2.get("report"), "A2 report"),
        (a2.get("coefficient_manifest"), "A2 coefficient manifest"),
        (c1.get("configuration"), "C1 configuration"),
        (c1.get("outcome"), "C1 outcome"),
    ):
        authority = _mapping(record, label)
        if not _valid_sha256(authority.get("sha256")):
            raise SurfaceFluxDisplayCalibrationError(
                f"D5-C2 embedded {label} hash is invalid."
            )


def _validate_manifest_source_authority(
    value: object, *, authority: C2Authority
) -> None:
    source = _mapping(value, "manifest source authority")
    if (
        source.get("c2_report")
        != {"filename": D5_C2_REPORT_NAME, "sha256": authority.report_sha256}
        or source.get("c2_completion")
        != {
            "filename": D5_C2_COMPLETION_NAME,
            "sha256": authority.completion_sha256,
        }
        or not isinstance(source.get("embedded_a1_a2_c1_authorities"), Mapping)
    ):
        raise SurfaceFluxDisplayCalibrationError(
            "display-calibration pinned C2 source authority is invalid."
        )
    _validate_embedded_authorities(source["embedded_a1_a2_c1_authorities"])


def _build_manifest(
    payload: bytes,
    *,
    source_authorities: Mapping[str, object],
    report_sha256: str,
    completion_sha256: str,
) -> dict[str, object]:
    block_bytes = D5_PATCHES_PER_PLANT * STRIDE_BYTES
    arrays = []
    for index, identity in enumerate(_expected_array_order()):
        offset = index * block_bytes
        family, side, metric = identity
        arrays.append(
            {
                "order_index": index,
                "family": family,
                "side": side,
                "metric": metric,
                "byte_offset": offset,
                "byte_length": block_bytes,
                "value_count": D5_PATCHES_PER_PLANT,
                "slice_sha256": hashlib.sha256(
                    payload[offset : offset + block_bytes]
                ).hexdigest(),
                "all_cells_available": True,
            }
        )
    return {
        "schema_id": DISPLAY_CALIBRATION_SCHEMA_ID,
        "schema_version": DISPLAY_CALIBRATION_SCHEMA_VERSION,
        "resource_id": DISPLAY_CALIBRATION_RESOURCE_ID,
        "status": "authenticated_complete",
        "purpose": DISPLAY_PURPOSE,
        "estimator": ESTIMATOR_ID,
        "source_authority": {
            "c2_report": {"filename": D5_C2_REPORT_NAME, "sha256": report_sha256},
            "c2_completion": {
                "filename": D5_C2_COMPLETION_NAME,
                "sha256": completion_sha256,
            },
            "embedded_a1_a2_c1_authorities": dict(source_authorities),
        },
        "scientific_identity": {
            "sampling_profile_id": D5_SAMPLING_PROFILE_ID,
            "topology_sha256": D5_TOPOLOGY_SHA256,
            "receivers_sha256": D5_RECEIVERS_SHA256,
            "family_order": list(FAMILY_ORDER),
            "block_order": [f"{side}_{metric}" for side, metric in BLOCK_ORDER],
            "local_patch_indices": {
                "first": 0,
                "last": 191,
                "count": D5_PATCHES_PER_PLANT,
            },
        },
        "quality_dispatch": quality_dispatch_contract(),
        "coefficient_payload": {
            "filename": DISPLAY_CALIBRATION_PAYLOAD_NAME,
            "media_type": "application/octet-stream",
            "schema": "headerless fixed-stride Float64 array",
            "component_type": COMPONENT_TYPE,
            "byte_order": BYTE_ORDER,
            "stride_bytes": STRIDE_BYTES,
            "units": COEFFICIENT_UNITS,
            "value_count": COEFFICIENT_COUNT,
            "byte_length": PAYLOAD_BYTE_LENGTH,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "ordering": COEFFICIENT_ORDERING,
        },
        "arrays": arrays,
        "availability": {
            "policy": "all_authenticated_finite_positive_cells_available",
            "available_cell_count": COEFFICIENT_COUNT,
            "masked_cell_count": 0,
            "threshold_masking_applied": False,
        },
        "raw_transport": {
            "proxied": False,
            "modified": False,
            "calibration_applies_to_display_only": True,
        },
        "historical_decisions": {
            "d5_b1_modified": False,
            "d5_a2_failure_status_bypassed": False,
            "c3_is_additive_display_resource": True,
        },
    }


def _validated_locations(
    config: SurfaceFluxDisplayCalibrationConfig,
) -> tuple[Path, Path]:
    if not isinstance(config, SurfaceFluxDisplayCalibrationConfig):
        raise TypeError("config must be SurfaceFluxDisplayCalibrationConfig.")
    source = config.c2_input_directory.expanduser()
    if source.is_symlink():
        raise SurfaceFluxDisplayCalibrationError(
            "C2 input must be a real non-symbolic-link directory."
        )
    try:
        source = source.resolve(strict=True)
    except OSError as exc:
        raise SurfaceFluxDisplayCalibrationError(
            "C2 input directory is unavailable."
        ) from exc
    requested = config.output_directory.expanduser()
    if requested.exists() or requested.is_symlink():
        raise SurfaceFluxDisplayCalibrationError(
            "C3 output directory must initially be absent."
        )
    parent = requested.parent.resolve(strict=True)
    output = parent / requested.name
    if not source.is_dir() or output == source or output.is_relative_to(source):
        raise SurfaceFluxDisplayCalibrationError(
            "C3 input/output directory relationship is unsafe."
        )
    return source, output


def _authenticated_file(path: Path, expected_hash: str, label: str) -> bytes:
    if not _valid_sha256(expected_hash) or not path.is_file() or path.is_symlink():
        raise SurfaceFluxDisplayCalibrationError(f"{label} is missing or unsafe.")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise SurfaceFluxDisplayCalibrationError(f"{label} SHA-256 failed.")
    return data


def _expected_array_order() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (family, side, metric)
        for family in FAMILY_ORDER
        for side, metric in BLOCK_ORDER
    )


def _json_object(data: bytes, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SurfaceFluxDisplayCalibrationError(f"{label} is malformed.") from exc
    return _mapping(value, label)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SurfaceFluxDisplayCalibrationError(f"{label} must be an object.")
    return value


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _write_fsynced(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def format_surface_flux_display_calibration_json(value: Mapping[str, object]) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"


__all__ = [
    "BLOCK_ORDER",
    "BYTE_ORDER",
    "COEFFICIENT_COUNT",
    "COEFFICIENT_ORDERING",
    "C2Authority",
    "D5_C2_ANALYSIS_ID",
    "D5_C2_COMPLETION_NAME",
    "D5_C2_COMPLETION_SCHEMA_ID",
    "D5_C2_REPORT_NAME",
    "D5_C2_SCHEMA_ID",
    "D5_C2_SCHEMA_VERSION",
    "D5_PATCHES_PER_PLANT",
    "D5_RECEIVERS_SHA256",
    "D5_SAMPLING_PROFILE_ID",
    "D5_TOPOLOGY_SHA256",
    "DISPLAY_CALIBRATION_MANIFEST_NAME",
    "DISPLAY_CALIBRATION_PAYLOAD_NAME",
    "DISPLAY_CALIBRATION_RESOURCE_ID",
    "DISPLAY_CALIBRATION_SCHEMA_ID",
    "DISPLAY_CALIBRATION_SCHEMA_VERSION",
    "ESTIMATOR_ID",
    "FAMILY_ORDER",
    "PAYLOAD_BYTE_LENGTH",
    "PINNED_C2_COMPLETION_SHA256",
    "PINNED_C2_REPORT_SHA256",
    "AuthenticatedSurfaceFluxDisplayCalibration",
    "DisplayQualitySelection",
    "SurfaceFluxDisplayCalibrationConfig",
    "SurfaceFluxDisplayCalibrationError",
    "VIEWER_CALIBRATION_PAYLOAD_NAME",
    "c2_quality_option_identity",
    "format_surface_flux_display_calibration_json",
    "generate_surface_flux_display_calibration",
    "load_surface_flux_display_calibration",
    "load_packaged_surface_flux_display_calibration",
    "quality_dispatch_contract",
    "validate_surface_flux_display_calibration",
]
