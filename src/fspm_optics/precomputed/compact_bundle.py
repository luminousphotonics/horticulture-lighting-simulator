"""Deterministic, authenticated compact playback bundle contract.

The completed native runtime is an export source, never the bundle inventory.
Only payloads used by a public UI consumer are selected below.  Large immutable
fixture assets and application JavaScript are authenticated catalog references
and are materialized from packaged resources during playback.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import csv
import hashlib
from importlib import resources
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
from typing import Any, Iterable, Mapping, Sequence
import uuid
import zipfile

from fspm_optics.geometry.coordinate_frame import (
    ROOM_FRAME_POLICY_ID,
    RoomCoordinateFrame,
)
from fspm_optics.precomputed.contracts import (
    CANONICAL_DOMAIN_SCHEMA_ID,
    CANONICAL_DOMAIN_SCHEMA_VERSION,
    FIXED_CASE_BINDING_SCHEMA_ID,
    FIXED_CASE_BINDING_SCHEMA_VERSION,
    FIXED_OUTPUT_TARGET_SEMANTICS,
    FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S,
    MEAN_TARGET_SEMANTICS,
)
from fspm_optics.transport.scalar_ppfd import PpfdMapSample
from fspm_optics.viewer.artifacts import VIEWER_RESOURCE_VERSION

COMPACT_BUNDLE_SCHEMA_ID = "fspm-optics.compact-precomputed-playback-bundle"
COMPACT_BUNDLE_SCHEMA_VERSION = 1
PUBLIC_RESULT_SCHEMA_ID = "fspm-optics.compact-public-playback-result"
PUBLIC_RESULT_SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
PUBLIC_RESULT_PATH = "payload/public-result.v1.json"
STAGE_A_PATH = "payload/stage-a-grid.v1.f64le.bin"
STAGE_A_RECORD = struct.Struct("<dddd")
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
RUN_ID = re.compile(r"^[0-9a-f]{32}$")

CAPABILITIES_BASE = (
    "final_baseline_ppfd_csv_v1",
    "metrics_panel_v1",
    "plant_layout_3d_target_coverage_v1",
    "ppfd_heatmap_2d_v1",
    "ppfd_heatmap_overlay_2d_v1",
    "validated_ppfd_scatter_3d_v1",
)

# Exact top-level fields read by resources/web/app.js.  Nested aggregate groups
# are pruned separately below.  This is intentionally not manifest-driven.
METRICS_FIELDS = (
    "schema_id",
    "schema_version",
    "run_id",
    "system_id",
    "system",
    "lighting_target_mode",
    "mounting_height",
    "analysis_scope",
    "runtime_status",
    "requested_target_ppfd_umol_m2_s",
    "achieved_mean_ppfd_umol_m2_s",
    "achieved_maximum_ppfd_umol_m2_s",
    "cap_binding",
    "cap_compliant",
    "dimming_factor",
    "target_feasible",
    "target_infeasibility",
    "power",
    "ppf",
    "counts",
    "room",
    "aisle_mode",
    "quality",
    "fspm_target_policy",
    "baseline_leaf_position_uniformity",
    "spatial_uniformity",
    "fspm_surface_light_metrics",
    "visualization",
)

OPTIONAL_METRICS_FIELDS = (
    "proposed_layout",
    "spectral_basis",
    "proposed_control",
    "proposed_source",
)

ABSORBED_METRIC_FIELDS = (
    "total_combined_absorbed_par_rate_umol_s",
    "absorbed_capture_efficiency_percent",
    "absorbed_par_per_electrical_watt_umol_per_j",
    "plant_to_plant_absorbed_exposure_cv_percent",
    "plant_minimum_to_mean_absorbed_exposure_ratio",
    "leaf_to_leaf_absorbed_exposure_cv_percent",
)

SOURCE_FILE_PAYLOADS = {
    "target_control": (
        "target_control.json",
        "payload/target-control.json",
        "public target-control artifact and exact playback metadata",
    ),
    "full_output_schedule": (
        "full_output_schedule.json",
        "payload/full-output-schedule.json",
        "public full-output source schedule and exact playback metadata",
    ),
    "operating_point": (
        "operating-point.json",
        "payload/operating-point.json",
        "public power, PPF, and source operating-point artifact",
    ),
    "baseline_leaf_uniformity": (
        "baseline-leaf-position-uniformity.v1.json",
        "payload/baseline-leaf-position-uniformity.v1.json",
        "metrics panel and authenticated Target Coverage derivation",
    ),
    "natural_fit_layout": (
        "natural_fit_layout.json",
        "payload/natural-fit-layout.json",
        "3D room, plant ordering, and placement identity",
    ),
    "physical_source_state": (
        "physical-source-state.json",
        "payload/physical-source-state.json",
        "scientific source and result compatibility",
    ),
    "visualization_metadata": (
        "visualization.json",
        "payload/visualization.json",
        "2D heatmap and Validated PPFD Scatter labels and normalization",
    ),
    "heatmap": (
        "ppfd-heatmap.png",
        "payload/ppfd-heatmap.png",
        "public PPFD Heatmap",
    ),
    "heatmap_overlay": (
        "ppfd-heatmap-overlay.png",
        "payload/ppfd-heatmap-overlay.png",
        "public PPFD Heatmap with authoritative overlay",
    ),
    "viewer_scene": (
        "plant-layout-viewer/scene.v1.json",
        "payload/viewer/scene.v1.json",
        "3D room, fixture, plant, Target Coverage, and interaction contract",
    ),
    "viewer_instances": (
        "plant-layout-viewer/instances.f32le.bin",
        "payload/viewer/instances.f32le.bin",
        "ordered 3D plant transforms",
    ),
    "fixture_catalog": (
        "plant-layout-viewer/fixtures/catalog.v1.json",
        "payload/viewer/fixtures/catalog.v1.json",
        "fixture identities, asset references, and transform inventory",
    ),
    "plant_profile": (
        "plant-layout-viewer/profiles/rex_juvenile_preheading_12leaf_v1/"
        "profile.v1.json",
        "payload/viewer/profile/profile.v1.json",
        "3D plant geometry and identity inventory",
    ),
    "plant_geometry": (
        "plant-layout-viewer/profiles/rex_juvenile_preheading_12leaf_v1/"
        "geometry.glb",
        "payload/viewer/profile/geometry.glb",
        "public 3D canonical plant surface",
    ),
    "plant_identity": (
        "plant-layout-viewer/profiles/rex_juvenile_preheading_12leaf_v1/"
        "identity-map.v1.json",
        "payload/viewer/profile/identity-map.v1.json",
        "plant, leaf, face, patch, and receiver interaction identities",
    ),
    "plant_receivers": (
        "plant-layout-viewer/profiles/rex_juvenile_preheading_12leaf_v1/"
        "receivers.f32le.bin",
        "payload/viewer/profile/receivers.f32le.bin",
        "public 3D receiver and normal overlay",
    ),
}

EXCLUDED_CATEGORIES = (
    "Radiance octrees and ambient caches",
    "raw matrices and basis matrices",
    "temporary RAD scene fragments and worker scratch files",
    "solver logs, command transcripts, locks, and process metadata",
    "raw Stage B receiver streams and intermediate Stage A/Stage B files",
    "per-patch and per-leaf Absorbed PAR coloring arrays",
    "duplicated viewer JavaScript, Three.js, and immutable fixture GLBs",
    "absolute filesystem paths and timestamps",
)


class CompactBundleStatus(str, Enum):
    """Authoritative future sweep-resume classification."""

    VALID = "valid_completed_bundle"
    MISSING = "missing_bundle"
    PARTIAL = "partial_bundle"
    CORRUPT = "corrupt_bundle"
    INCOMPATIBLE_SCHEMA = "incompatible_schema"
    CONFIGURATION_MISMATCH = "mismatched_run_configuration"
    IDENTITY_MISMATCH = "mismatched_authenticated_identities"


class CompactBundleError(ValueError):
    """A compact bundle failed closed before playback data was returned."""

    def __init__(self, validation: "CompactBundleValidation") -> None:
        self.validation = validation
        super().__init__(f"{validation.status.value}: {validation.message}")


@dataclass(frozen=True, slots=True)
class CompactBundleValidation:
    status: CompactBundleStatus
    message: str
    bundle_identity_sha256: str | None = None
    manifest: Mapping[str, object] | None = None

    @property
    def valid(self) -> bool:
        return self.status is CompactBundleStatus.VALID


@dataclass(frozen=True, slots=True)
class CompactBundleExport:
    path: Path
    bundle_identity_sha256: str
    byte_size: int
    payload_count: int


@dataclass(frozen=True, slots=True)
class BundleSizeReport:
    source_runtime_bytes: int
    compact_bundle_bytes: int
    bytes_removed: int
    percent_removed: float
    largest_retained_payloads: tuple[tuple[str, int], ...]
    largest_excluded_categories: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class CompactPlayback:
    """Fully validated playback data independent of its source runtime."""

    bundle_path: Path
    manifest: Mapping[str, object]
    public_payload: Mapping[str, object]
    payloads: Mapping[str, bytes]
    samples: tuple[PpfdMapSample, ...]

    @property
    def run_id(self) -> str:
        return str(self.public_payload["run_id"])

    @property
    def system_id(self) -> str:
        return str(self.public_payload["system_id"])

    @property
    def metrics(self) -> Mapping[str, object]:
        value = self.public_payload["metrics"]
        assert isinstance(value, Mapping)
        return value

    @property
    def visualization(self) -> Mapping[str, object]:
        return _json_object_bytes(
            self.payloads["visualization_metadata"], "visualization metadata"
        )

    @property
    def viewer_scene(self) -> Mapping[str, object]:
        return _json_object_bytes(self.payloads["viewer_scene"], "viewer scene")

    def final_baseline_ppfd_csv(self) -> tuple[str, str, bytes]:
        """Return the exact live download contract generated from Float64 data."""

        return (
            "ppfd.csv",
            "text/csv; charset=utf-8",
            _ppfd_csv(self.samples).encode("utf-8"),
        )

    def validated_ppfd_scatter(self) -> tuple[Mapping[str, object], bytes]:
        """Return live-compatible Float32 scatter bytes plus display metadata."""

        return self.visualization, _scatter_bytes(self.samples)

    def result_payload(self) -> dict[str, object]:
        """Build the URL contract consumed by the existing browser application."""

        run_id = self.run_id
        metrics = self.metrics
        return {
            "run_id": run_id,
            "system_id": self.system_id,
            "analysis_scope": metrics["analysis_scope"]["value"],  # type: ignore[index]
            "lighting_target_mode": metrics.get("lighting_target_mode"),
            "metrics_url": f"/api/runs/{run_id}/metrics",
            "manifest_url": f"/api/runs/{run_id}/manifest",
            "ppfd_csv_url": f"/api/runs/{run_id}/artifacts/ppfd.csv",
            "plant_layout_viewer_url": f"/runs/{run_id}/viewer/index.html",
            "ppfd_heatmap_url": (
                f"/api/runs/{run_id}/artifacts/ppfd-heatmap.png"
            ),
            "ppfd_heatmap_overlay_url": (
                f"/api/runs/{run_id}/artifacts/ppfd-heatmap-overlay.png"
            ),
            "visualization_metadata_url": (
                f"/api/runs/{run_id}/artifacts/visualization.json"
            ),
            "ppfd_scatter_viewer_url": f"/runs/{run_id}/scatter/index.html",
            **{
                key: metrics[key]
                for key in (
                    "proposed_control",
                    "proposed_layout",
                    "proposed_source",
                    "spectral_basis",
                )
                if metrics.get(key) is not None
            },
        }


def export_compact_bundle(
    completed_runtime: str | Path,
    destination: str | Path,
    *,
    fixed_case_binding: Mapping[str, object] | None = None,
    fixed_plan_inputs: Mapping[str, object] | None = None,
) -> CompactBundleExport:
    """Export one completed native run through a validated atomic ZIP publish."""

    from fspm_optics.application.proposed import validate_success_artifacts

    source_candidate = Path(completed_runtime).expanduser()
    if source_candidate.is_symlink():
        raise ValueError("completed runtime source must not be a symlink.")
    source = source_candidate.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("completed runtime source must be a real directory.")
    manifest = _read_json(source / "manifest.json", "native manifest")
    request = _mapping(manifest.get("request"), "native request")
    run_id = _nonempty_text(manifest.get("run_id"), "native run ID")
    system_id = _nonempty_text(manifest.get("system_id"), "native system ID")
    validate_success_artifacts(
        source,
        expected_run_id=run_id,
        expected_system_id=system_id,
        expected_request=request,
    )

    public_payload = normalize_live_public_payload(
        source,
        fixed_case_binding=fixed_case_binding,
        fixed_plan_inputs=fixed_plan_inputs,
    )
    samples = _read_ppfd_csv(source / "ppfd.csv")
    payloads, consumers = _collect_payloads(source, public_payload, samples)
    bundle_manifest = _build_bundle_manifest(
        public_payload=public_payload,
        payloads=payloads,
        consumers=consumers,
    )
    manifest_bytes = _canonical_json_bytes(bundle_manifest)

    target_candidate = Path(destination).expanduser()
    if target_candidate.is_symlink():
        raise ValueError("compact bundle destination must not be a symlink.")
    target = target_candidate.resolve()
    if target.exists() and target.is_dir():
        raise IsADirectoryError(f"compact bundle destination is a directory: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    try:
        _write_deterministic_zip(temporary, manifest_bytes, payloads)
        validation = validate_compact_bundle(temporary)
        if not validation.valid:
            raise CompactBundleError(validation)
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return CompactBundleExport(
        path=target,
        bundle_identity_sha256=str(bundle_manifest["bundle_identity_sha256"]),
        byte_size=target.stat().st_size,
        payload_count=len(payloads),
    )


def normalize_live_public_payload(
    completed_runtime: str | Path,
    *,
    fixed_case_binding: Mapping[str, object] | None = None,
    fixed_plan_inputs: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the consumer-driven live payload used for equivalence testing."""

    root = Path(completed_runtime).resolve(strict=True)
    manifest = _read_json(root / "manifest.json", "native manifest")
    metrics = _metrics_payload(_read_json(root / "metrics.json", "native metrics"))
    source_state = _read_json(
        root / "physical-source-state.json", "physical source state"
    )
    visualization = _read_json(root / "visualization.json", "visualization")
    scene = _normalized_viewer_scene(
        _read_json(
            root / "plant-layout-viewer/scene.v1.json", "viewer scene"
        )
    )
    catalog = _read_json(
        root / "plant-layout-viewer/fixtures/catalog.v1.json",
        "fixture catalog",
    )
    run_configuration = _run_configuration(manifest, metrics)
    identities = _authenticated_identities(
        manifest=manifest,
        source_state=source_state,
        visualization=visualization,
        scene=scene,
        catalog=catalog,
    )
    _bind_fixed_case_context(
        run_configuration=run_configuration,
        identities=identities,
        fixed_case_binding=fixed_case_binding,
        fixed_plan_inputs=fixed_plan_inputs,
    )
    payload: dict[str, object] = {
        "schema_id": PUBLIC_RESULT_SCHEMA_ID,
        "schema_version": PUBLIC_RESULT_SCHEMA_VERSION,
        "run_id": manifest.get("run_id"),
        "system_id": manifest.get("system_id"),
        "system": manifest.get("system"),
        "run_configuration": run_configuration,
        "metrics": metrics,
        "result_metadata": {
            key: manifest[key]
            for key in (
                "analysis_scope",
                "lighting_target_mode",
                "logical_stages",
                "mounting_height",
                "room",
                "spectral_basis",
                "proposed_control",
                "proposed_layout",
                "proposed_source",
                "fspm_target_policy",
            )
            if key in manifest
        },
        "authenticated_identities": identities,
        "capabilities": list(_capabilities(metrics)),
        "public_leaf_coloring_modes": ["target_coverage"],
        "per_leaf_absorbed_par_coloring_retained": False,
    }
    _assert_portable(payload)
    return payload


def validate_compact_bundle(
    path: str | Path,
    *,
    expected_bundle_identity_sha256: str | None = None,
    expected_run_configuration: Mapping[str, object] | None = None,
    expected_authenticated_identities: Mapping[str, object] | None = None,
) -> CompactBundleValidation:
    """Classify a bundle without returning unvalidated playback data."""

    if (
        expected_bundle_identity_sha256 is not None
        and not _hex_sha256(expected_bundle_identity_sha256)
    ):
        raise ValueError("expected bundle identity must be a SHA-256 digest.")

    candidate = Path(path).expanduser()
    if not candidate.exists():
        return _validation(CompactBundleStatus.MISSING, "bundle does not exist.")
    if candidate.is_dir():
        return _validate_historical_directory(candidate)
    if candidate.is_symlink() or not candidate.is_file():
        return _validation(
            CompactBundleStatus.CORRUPT,
            "bundle is not a regular file.",
        )
    try:
        with zipfile.ZipFile(candidate, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                return _validation(
                    CompactBundleStatus.CORRUPT,
                    "archive contains duplicate entries.",
                )
            if MANIFEST_NAME not in names:
                return _validation(
                    CompactBundleStatus.PARTIAL,
                    "archive manifest is missing.",
                )
            if any(not _safe_archive_name(name) for name in names):
                return _validation(
                    CompactBundleStatus.CORRUPT,
                    "archive contains an unsafe entry name.",
                )
            if any(_zip_entry_is_link(info) for info in infos):
                return _validation(
                    CompactBundleStatus.CORRUPT,
                    "archive contains a link entry.",
                )
            try:
                manifest = _json_object_bytes(
                    archive.read(MANIFEST_NAME), "compact manifest"
                )
            except (KeyError, OSError, ValueError, zipfile.BadZipFile) as exc:
                return _validation(
                    CompactBundleStatus.CORRUPT,
                    f"manifest cannot be decoded: {exc}",
                )
            if (
                manifest.get("schema_id") != COMPACT_BUNDLE_SCHEMA_ID
                or manifest.get("schema_version")
                != COMPACT_BUNDLE_SCHEMA_VERSION
            ):
                return _validation(
                    CompactBundleStatus.INCOMPATIBLE_SCHEMA,
                    "bundle schema is not the compact schema-v1 contract.",
                    manifest=manifest,
                )
            structural = _validate_manifest_structure(manifest, set(names))
            if structural is not None:
                return structural
            records = manifest["payload_inventory"]
            assert isinstance(records, list)
            payloads: dict[str, bytes] = {}
            for raw in records:
                assert isinstance(raw, Mapping)
                name = str(raw["name"])
                payload_path = str(raw["path"])
                try:
                    data = archive.read(payload_path)
                except KeyError as exc:
                    return _validation(
                        CompactBundleStatus.PARTIAL,
                        f"payload {name!r} cannot be read: {exc}",
                        manifest=manifest,
                    )
                except (OSError, zipfile.BadZipFile) as exc:
                    return _validation(
                        CompactBundleStatus.CORRUPT,
                        f"payload {name!r} failed archive integrity: {exc}",
                        manifest=manifest,
                    )
                if len(data) != raw["byte_size"]:
                    return _validation(
                        CompactBundleStatus.CORRUPT,
                        f"payload {name!r} byte size changed.",
                        manifest=manifest,
                    )
                if _sha256(data) != raw["sha256"]:
                    return _validation(
                        CompactBundleStatus.CORRUPT,
                        f"payload {name!r} hash changed.",
                        manifest=manifest,
                    )
                payloads[name] = data
            try:
                public_payload = _json_object_bytes(
                    payloads["public_result"], "public result"
                )
                _validate_public_contract(manifest, public_payload, payloads)
            except (KeyError, TypeError, ValueError) as exc:
                return _validation(
                    CompactBundleStatus.CORRUPT,
                    f"authenticated public contract is invalid: {exc}",
                    manifest=manifest,
                )
            expected_identity = _bundle_identity(manifest)
            actual_identity = manifest.get("bundle_identity_sha256")
            if actual_identity != expected_identity:
                return _validation(
                    CompactBundleStatus.CORRUPT,
                    "overall bundle identity changed.",
                    manifest=manifest,
                )
            if (
                expected_bundle_identity_sha256 is not None
                and expected_identity != expected_bundle_identity_sha256
            ):
                return _validation(
                    CompactBundleStatus.IDENTITY_MISMATCH,
                    "bundle identity is not authorized for this artifact.",
                    bundle_identity=expected_identity,
                    manifest=manifest,
                )
            if expected_run_configuration is not None and not _mapping_contains(
                _mapping(manifest["run_configuration"], "run configuration"),
                expected_run_configuration,
            ):
                return _validation(
                    CompactBundleStatus.CONFIGURATION_MISMATCH,
                    "authenticated run configuration does not match.",
                    bundle_identity=expected_identity,
                    manifest=manifest,
                )
            if expected_authenticated_identities is not None and not (
                _mapping_contains(
                    _mapping(
                        manifest["authenticated_identities"],
                        "authenticated identities",
                    ),
                    expected_authenticated_identities,
                )
            ):
                return _validation(
                    CompactBundleStatus.IDENTITY_MISMATCH,
                    "authenticated scientific identities do not match.",
                    bundle_identity=expected_identity,
                    manifest=manifest,
                )
            return _validation(
                CompactBundleStatus.VALID,
                "compact bundle is complete and authenticated.",
                bundle_identity=expected_identity,
                manifest=manifest,
            )
    except (OSError, EOFError, zipfile.BadZipFile) as exc:
        status = (
            CompactBundleStatus.PARTIAL
            if _looks_like_truncated_zip(candidate)
            else CompactBundleStatus.CORRUPT
        )
        return _validation(
            status,
            f"bundle archive cannot be opened: {exc}",
        )


def load_compact_bundle(
    path: str | Path,
    *,
    expected_bundle_identity_sha256: str | None = None,
    expected_run_configuration: Mapping[str, object] | None = None,
    expected_authenticated_identities: Mapping[str, object] | None = None,
) -> CompactPlayback:
    """Validate every contract layer, then return standalone playback data."""

    validation = validate_compact_bundle(
        path,
        expected_bundle_identity_sha256=expected_bundle_identity_sha256,
        expected_run_configuration=expected_run_configuration,
        expected_authenticated_identities=expected_authenticated_identities,
    )
    if not validation.valid or validation.manifest is None:
        raise CompactBundleError(validation)
    bundle_path = Path(path).expanduser().resolve(strict=True)
    with zipfile.ZipFile(bundle_path, "r") as archive:
        records = validation.manifest["payload_inventory"]
        assert isinstance(records, list)
        payloads = {
            str(record["name"]): archive.read(str(record["path"]))
            for record in records
            if isinstance(record, Mapping)
        }
    public_payload = _json_object_bytes(
        payloads["public_result"], "public result"
    )
    samples = _decode_stage_a(payloads["stage_a_grid"])
    return CompactPlayback(
        bundle_path=bundle_path,
        manifest=dict(validation.manifest),
        public_payload=public_payload,
        payloads=payloads,
        samples=samples,
    )


def materialize_compact_playback(
    playback_or_path: CompactPlayback | str | Path,
    destination: str | Path,
) -> Path:
    """Materialize only public runtime artifacts through an atomic directory rename."""

    playback = (
        playback_or_path
        if isinstance(playback_or_path, CompactPlayback)
        else load_compact_bundle(playback_or_path)
    )
    target_candidate = Path(destination).expanduser()
    if target_candidate.is_symlink():
        raise ValueError("playback destination must not be a symlink.")
    target = target_candidate.resolve()
    if target.exists():
        raise FileExistsError(f"playback destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    temporary.mkdir()
    try:
        _materialize_payloads(playback, temporary)
        os.replace(temporary, target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target


def measure_compact_bundle(
    completed_runtime: str | Path,
    bundle_path: str | Path,
    *,
    limit: int = 5,
) -> BundleSizeReport:
    """Measure retained and excluded data without generating a sweep case."""

    source = Path(completed_runtime).resolve(strict=True)
    bundle = Path(bundle_path).resolve(strict=True)
    source_files = [path for path in source.rglob("*") if path.is_file()]
    source_size = sum(path.stat().st_size for path in source_files)
    bundle_size = bundle.stat().st_size
    playback = load_compact_bundle(bundle)
    retained = sorted(
        ((name, len(data)) for name, data in playback.payloads.items()),
        key=lambda item: (-item[1], item[0]),
    )
    retained_sources = {
        source_relative
        for source_relative, _payload_path, _consumer in SOURCE_FILE_PAYLOADS.values()
    }
    catalog = _json_object_bytes(
        playback.payloads["fixture_catalog"], "fixture catalog"
    )
    for group in _list(catalog.get("asset_groups"), "fixture asset groups"):
        if isinstance(group, Mapping):
            matrices = group.get("instance_matrices")
            asset = group.get("asset")
            if isinstance(matrices, Mapping):
                retained_sources.add(
                    "plant-layout-viewer/fixtures/" + str(matrices.get("filename"))
                )
            if isinstance(asset, Mapping):
                # Asset is deliberately a catalog reference, not retained bytes.
                pass
    categories: dict[str, int] = {}
    for path in source_files:
        relative = path.relative_to(source).as_posix()
        if relative in retained_sources or relative == "ppfd.csv":
            continue
        category = _excluded_category(relative)
        categories[category] = categories.get(category, 0) + path.stat().st_size
    excluded = sorted(categories.items(), key=lambda item: (-item[1], item[0]))
    removed = source_size - bundle_size
    return BundleSizeReport(
        source_runtime_bytes=source_size,
        compact_bundle_bytes=bundle_size,
        bytes_removed=removed,
        percent_removed=(100.0 * removed / source_size if source_size else 0.0),
        largest_retained_payloads=tuple(retained[:limit]),
        largest_excluded_categories=tuple(excluded[:limit]),
    )


def _collect_payloads(
    source: Path,
    public_payload: Mapping[str, object],
    samples: Sequence[PpfdMapSample],
) -> tuple[dict[str, tuple[str, bytes]], dict[str, str]]:
    payloads: dict[str, tuple[str, bytes]] = {
        "public_result": (
            PUBLIC_RESULT_PATH,
            _canonical_json_bytes(public_payload),
        ),
        "stage_a_grid": (STAGE_A_PATH, _encode_stage_a(samples)),
    }
    consumers = {
        "public_result": "metrics panel, labels, configuration, and compatibility",
        "stage_a_grid": (
            "Final Baseline PPFD CSV, heatmaps, scatter, and Target Coverage"
        ),
    }
    for name, (source_relative, payload_path, consumer) in SOURCE_FILE_PAYLOADS.items():
        data = (source / source_relative).read_bytes()
        if name == "viewer_scene":
            data = _canonical_json_bytes(
                _normalized_viewer_scene(
                    _json_object_bytes(data, "viewer scene")
                )
            )
        payloads[name] = (payload_path, data)
        consumers[name] = consumer

    catalog = _json_object_bytes(payloads["fixture_catalog"][1], "fixture catalog")
    groups = _list(catalog.get("asset_groups"), "fixture asset groups")
    for index, raw_group in enumerate(groups):
        group = _mapping(raw_group, f"fixture asset group {index}")
        matrices = _mapping(group.get("instance_matrices"), "fixture transforms")
        relative = _safe_relative_text(matrices.get("filename"), "fixture transforms")
        source_relative = f"plant-layout-viewer/fixtures/{relative}"
        name = f"fixture_transforms_{index:02d}"
        payloads[name] = (
            f"payload/viewer/fixtures/{relative}",
            (source / source_relative).read_bytes(),
        )
        consumers[name] = "authenticated 3D fixture transforms"

    if _scatter_bytes(samples) != _expected_scatter_bytes(source):
        raise ValueError("authoritative Float64 Stage A data cannot reproduce scatter.")
    for name, (payload_path, data) in payloads.items():
        if payload_path.endswith(".json"):
            _assert_portable(_json_object_bytes(data, f"{name} payload"))
    return payloads, consumers


def _build_bundle_manifest(
    *,
    public_payload: Mapping[str, object],
    payloads: Mapping[str, tuple[str, bytes]],
    consumers: Mapping[str, str],
) -> dict[str, object]:
    records = [
        {
            "name": name,
            "path": payloads[name][0],
            "byte_size": len(payloads[name][1]),
            "sha256": _sha256(payloads[name][1]),
            "compression": "zip-deflate-lossless",
            "consumer": consumers[name],
        }
        for name in sorted(payloads)
    ]
    manifest: dict[str, object] = {
        "schema_id": COMPACT_BUNDLE_SCHEMA_ID,
        "schema_version": COMPACT_BUNDLE_SCHEMA_VERSION,
        "run_id": public_payload["run_id"],
        "system_id": public_payload["system_id"],
        "run_configuration": public_payload["run_configuration"],
        "authenticated_identities": public_payload["authenticated_identities"],
        "capabilities": public_payload["capabilities"],
        "payload_inventory": records,
        "catalog_assets": _catalog_asset_references(payloads),
        "storage": {
            "container": "deterministic-zip",
            "compression": "deflate-level-9",
            "scientific_numeric_quantization": False,
            "stage_a_record": (
                "little-endian Float64 x_m,y_m,z_m,ppfd_umol_m2_s"
            ),
            "numeric_arrays_stored_once": True,
        },
        "public_leaf_coloring_modes": ["target_coverage"],
        "per_leaf_absorbed_par_coloring_retained": False,
        "excluded_runtime_categories": list(EXCLUDED_CATEGORIES),
    }
    manifest["bundle_identity_sha256"] = _bundle_identity(manifest)
    return manifest


def _catalog_asset_references(
    payloads: Mapping[str, tuple[str, bytes]],
) -> list[dict[str, object]]:
    from fspm_optics.viewer.fixtures import ASSET_REGISTRY

    catalog = _json_object_bytes(payloads["fixture_catalog"][1], "fixture catalog")
    references: list[dict[str, object]] = []
    registry_by_id = {asset.asset_id: asset for asset in ASSET_REGISTRY}
    for raw_group in _list(catalog.get("asset_groups"), "fixture asset groups"):
        group = _mapping(raw_group, "fixture asset group")
        registry = _mapping(group.get("registry"), "fixture asset registry")
        asset_record = _mapping(group.get("asset"), "fixture asset")
        asset_id = _nonempty_text(registry.get("asset_id"), "fixture asset ID")
        approved = registry_by_id.get(asset_id)
        if approved is None:
            raise ValueError(f"fixture catalog asset is not packaged: {asset_id}")
        if (
            registry.get("packaged_resource_path") != approved.resource_path
            or asset_record.get("byte_size") != approved.byte_size
            or asset_record.get("sha256") != approved.sha256
            or registry.get("expected_byte_size") != approved.byte_size
            or registry.get("expected_sha256") != approved.sha256
        ):
            raise ValueError("fixture catalog asset identity is incompatible.")
        references.append(
            {
                "asset_id": approved.asset_id,
                "package": "fspm_optics.resources.viewer",
                "resource_path": approved.resource_path,
                "materialized_path": "plant-layout-viewer/fixtures/"
                + _safe_relative_text(
                    asset_record.get("filename"), "fixture materialized asset"
                ),
                "byte_size": approved.byte_size,
                "sha256": approved.sha256,
            }
        )
    return sorted(references, key=lambda item: str(item["asset_id"]))


def _write_deterministic_zip(
    path: Path,
    manifest_bytes: bytes,
    payloads: Mapping[str, tuple[str, bytes]],
) -> None:
    entries = [(MANIFEST_NAME, manifest_bytes)] + sorted(
        ((record[0], record[1]) for record in payloads.values()),
        key=lambda item: item[0],
    )
    with path.open("xb") as handle:
        with zipfile.ZipFile(
            handle,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for name, data in entries:
                info = zipfile.ZipInfo(name, ZIP_EPOCH)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        handle.flush()
        os.fsync(handle.fileno())


def _validate_manifest_structure(
    manifest: Mapping[str, object],
    archive_names: set[str],
) -> CompactBundleValidation | None:
    identity = manifest.get("bundle_identity_sha256")
    if not isinstance(identity, str) or not HEX_SHA256.fullmatch(identity):
        return _validation(
            CompactBundleStatus.CORRUPT,
            "bundle identity is missing or malformed.",
            manifest=manifest,
        )
    if manifest.get("storage") != {
        "container": "deterministic-zip",
        "compression": "deflate-level-9",
        "scientific_numeric_quantization": False,
        "stage_a_record": (
            "little-endian Float64 x_m,y_m,z_m,ppfd_umol_m2_s"
        ),
        "numeric_arrays_stored_once": True,
    }:
        return _validation(
            CompactBundleStatus.INCOMPATIBLE_SCHEMA,
            "bundle storage contract is unsupported.",
            manifest=manifest,
        )
    if manifest.get("excluded_runtime_categories") != list(EXCLUDED_CATEGORIES):
        return _validation(
            CompactBundleStatus.INCOMPATIBLE_SCHEMA,
            "bundle exclusion contract is unsupported.",
            manifest=manifest,
        )
    records = manifest.get("payload_inventory")
    if not isinstance(records, list) or not records:
        return _validation(
            CompactBundleStatus.PARTIAL,
            "payload inventory is missing.",
            manifest=manifest,
        )
    declared_names: set[str] = set()
    declared_paths: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            return _validation(
                CompactBundleStatus.CORRUPT,
                "payload inventory contains a non-object record.",
                manifest=manifest,
            )
        name = record.get("name")
        path = record.get("path")
        byte_size = record.get("byte_size")
        digest = record.get("sha256")
        if (
            not isinstance(name, str)
            or not name
            or name in declared_names
            or not isinstance(path, str)
            or not _safe_archive_name(path)
            or path in declared_paths
            or isinstance(byte_size, bool)
            or not isinstance(byte_size, int)
            or byte_size < 0
            or not isinstance(digest, str)
            or not HEX_SHA256.fullmatch(digest)
            or record.get("compression") != "zip-deflate-lossless"
            or not isinstance(record.get("consumer"), str)
        ):
            return _validation(
                CompactBundleStatus.CORRUPT,
                "payload inventory record is malformed.",
                manifest=manifest,
            )
        declared_names.add(name)
        declared_paths.add(path)
    required = {"public_result", "stage_a_grid", *SOURCE_FILE_PAYLOADS}
    if not required.issubset(declared_names):
        return _validation(
            CompactBundleStatus.PARTIAL,
            "one or more required payloads are missing from inventory.",
            manifest=manifest,
        )
    expected_archive = {MANIFEST_NAME, *declared_paths}
    if archive_names != expected_archive:
        status = (
            CompactBundleStatus.PARTIAL
            if expected_archive - archive_names
            else CompactBundleStatus.CORRUPT
        )
        return _validation(
            status,
            "archive and authenticated payload inventory disagree.",
            manifest=manifest,
        )
    return None


def _validate_public_contract(
    manifest: Mapping[str, object],
    public: Mapping[str, object],
    payloads: Mapping[str, bytes],
) -> None:
    if (
        public.get("schema_id") != PUBLIC_RESULT_SCHEMA_ID
        or public.get("schema_version") != PUBLIC_RESULT_SCHEMA_VERSION
        or public.get("run_id") != manifest.get("run_id")
        or public.get("system_id") != manifest.get("system_id")
        or public.get("run_configuration") != manifest.get("run_configuration")
        or public.get("authenticated_identities")
        != manifest.get("authenticated_identities")
        or public.get("capabilities") != manifest.get("capabilities")
        or public.get("public_leaf_coloring_modes") != ["target_coverage"]
        or public.get("per_leaf_absorbed_par_coloring_retained") is not False
        or manifest.get("public_leaf_coloring_modes") != ["target_coverage"]
        or manifest.get("per_leaf_absorbed_par_coloring_retained") is not False
    ):
        raise ValueError("manifest and public-result identities disagree.")
    if not RUN_ID.fullmatch(str(public.get("run_id", ""))):
        raise ValueError("public run ID is malformed.")
    metrics = _mapping(public.get("metrics"), "public metrics")
    if metrics.get("run_id") != public.get("run_id") or metrics.get(
        "system_id"
    ) != public.get("system_id"):
        raise ValueError("metrics identity disagrees with public result.")
    _validate_run_configuration(
        _mapping(public.get("run_configuration"), "run configuration"),
        system_id=str(public["system_id"]),
    )
    if public.get("capabilities") != list(_capabilities(metrics)):
        raise ValueError("capability declarations disagree with public metrics.")
    identities = _mapping(
        public.get("authenticated_identities"), "authenticated identities"
    )
    _validate_fixed_case_context(
        _mapping(public.get("run_configuration"), "run configuration"),
        identities,
    )
    scientific = _mapping(identities.get("scientific"), "scientific identity")
    raw_identity_payloads = (
        (
            "baseline_leaf_uniformity",
            "baseline_leaf_position_uniformity_artifact_sha256",
        ),
        ("natural_fit_layout", "natural_fit_layout_sha256"),
        ("physical_source_state", "physical_source_state_sha256"),
    )
    for payload_name, identity_name in raw_identity_payloads:
        if _sha256(payloads[payload_name]) != scientific.get(identity_name):
            raise ValueError(f"{payload_name} scientific identity disagrees.")
    json_identity_payloads = (
        ("target_control", "target_control_sha256"),
        ("full_output_schedule", "full_output_schedule_sha256"),
        ("operating_point", "operating_point_sha256"),
    )
    decoded_json_payloads: dict[str, dict[str, object]] = {}
    for payload_name, identity_name in json_identity_payloads:
        decoded = _json_object_bytes(payloads[payload_name], payload_name)
        decoded_json_payloads[payload_name] = decoded
        if _hash_json(decoded) != scientific.get(identity_name):
            raise ValueError(f"{payload_name} scientific identity disagrees.")
    target_control = decoded_json_payloads["target_control"]
    for key in (
        "lighting_target_mode",
        "requested_target_ppfd_umol_m2_s",
        "achieved_mean_ppfd_umol_m2_s",
        "achieved_maximum_ppfd_umol_m2_s",
        "dimming_factor",
        "feasible",
        "infeasibility",
    ):
        metrics_key = (
            "target_feasible"
            if key == "feasible"
            else "target_infeasibility"
            if key == "infeasibility"
            else key
        )
        if key in target_control and metrics.get(metrics_key) != target_control[key]:
            raise ValueError("target-control artifact and metrics disagree.")
    samples = _decode_stage_a(payloads["stage_a_grid"])
    visualization = _json_object_bytes(
        payloads["visualization_metadata"], "visualization metadata"
    )
    expected_field = hashlib.sha256(payloads["stage_a_grid"]).hexdigest()
    field = _mapping(visualization.get("field"), "visualization field")
    scatter = _mapping(visualization.get("scatter"), "visualization scatter")
    scatter_bytes = _scatter_bytes(samples)
    if (
        field.get("identity_sha256") != expected_field
        or field.get("sample_count") != len(samples)
        or scatter.get("count") != len(samples)
        or scatter.get("sha256") != _sha256(scatter_bytes)
        or scatter.get("byte_length") != len(scatter_bytes)
        or scatter.get("source_field_identity_sha256") != expected_field
    ):
        raise ValueError("Stage A data and visualization identity disagree.")
    scene = _json_object_bytes(payloads["viewer_scene"], "viewer scene")
    if "surface_flux" in scene:
        raise ValueError("compact viewer scene retains Absorbed PAR coloring.")
    heatmap = _mapping(scene.get("ppfd_heatmap"), "scene PPFD heatmap")
    if heatmap.get("target_coverage") is None:
        raise ValueError("Target Coverage contract is missing.")
    if heatmap.get("source_field_identity_sha256") != expected_field:
        raise ValueError("viewer Target Coverage field identity disagrees.")
    target_coverage = _mapping(
        heatmap.get("target_coverage"), "Target Coverage contract"
    )
    if (
        target_coverage.get("system_id") != public.get("system_id")
        or target_coverage.get("parent_source_field_identity_sha256")
        != expected_field
    ):
        raise ValueError("Target Coverage result identity disagrees.")
    baseline_leaf = _json_object_bytes(
        payloads["baseline_leaf_uniformity"], "baseline leaf uniformity"
    )
    baseline_field = _mapping(
        baseline_leaf.get("stage_a_field"), "baseline leaf Stage A field"
    )
    if (
        baseline_leaf.get("run_id") != public.get("run_id")
        or baseline_leaf.get("system_id") != public.get("system_id")
        or baseline_field.get("field_identity_sha256") != expected_field
        or baseline_field.get("sample_count") != len(samples)
    ):
        raise ValueError("baseline leaf-position field identity disagrees.")
    _validate_viewer_payloads(
        manifest=manifest,
        public=public,
        payloads=payloads,
        scene=scene,
    )
    _assert_portable(public)


def _validate_run_configuration(
    configuration: Mapping[str, object],
    *,
    system_id: str,
) -> None:
    request = _mapping(configuration.get("request"), "configured request")
    dimensions = _list(
        configuration.get("ordered_room_dimensions"), "ordered room dimensions"
    )
    aisle = _mapping(configuration.get("aisle"), "configured aisle")
    mounting = _mapping(
        configuration.get("mounting_height"), "configured mounting height"
    )
    spectral = _mapping(configuration.get("spectral"), "configured spectral state")
    quality = _mapping(
        configuration.get("radiance_quality"), "configured Radiance quality"
    )
    far_red = _mapping(configuration.get("far_red"), "configured far-red state")
    if (
        configuration.get("system_id") != system_id
        or request.get("system") != system_id
        or configuration.get("dimension_units")
        != {"input": "foot", "scientific": "meter"}
        or len(dimensions) != 2
        or [
            _mapping(record, "ordered room dimension").get("axis")
            for record in dimensions
        ]
        != ["length", "width"]
        or _mapping(dimensions[0], "length dimension").get("feet")
        != request.get("room_length_ft")
        or _mapping(dimensions[1], "width dimension").get("feet")
        != request.get("room_width_ft")
        or aisle.get("enabled") != request.get("aisle_mode")
        or not isinstance(aisle.get("active_domain_identity_sha256"), str)
        or mounting.get("mounting_height_in")
        != request.get("mounting_height_in")
        or mounting.get("input_unit") != "inch"
        or mounting.get("definition")
        != "emitting_aperture_plane_to_receiver_reference_plane"
        or not isinstance(configuration.get("solver"), Mapping)
        or not configuration.get("solver")
        or quality.get("name") != request.get("quality")
        or not isinstance(quality.get("radiance_options"), list)
        or spectral.get("analysis_scope") != request.get("analysis_scope")
        or spectral.get("include_far_red")
        != request.get("include_far_red", False)
        or far_red.get("requested") != request.get("include_far_red", False)
        or not isinstance(far_red.get("executed"), bool)
    ):
        raise ValueError("run configuration is incomplete or contradictory.")
    for record in dimensions:
        dimension = _mapping(record, "ordered room dimension")
        for key in ("feet", "meters"):
            value = dimension.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(float(value))
                or float(value) <= 0.0
            ):
                raise ValueError("ordered room dimension is not finite and positive.")
    canonical = configuration.get("canonical_domain")
    if canonical is not None:
        expected_canonical = _canonical_domain_from_request(request)
        if canonical != expected_canonical:
            raise ValueError(
                "canonical room domain disagrees with the ordered request."
            )


def _validate_fixed_case_context(
    run_configuration: Mapping[str, object],
    identities: Mapping[str, object],
) -> None:
    raw_binding = run_configuration.get("fixed_plan")
    raw_inputs = identities.get("fixed_plan_inputs")
    if raw_binding is None and raw_inputs is None:
        return
    binding = _mapping(raw_binding, "fixed plan binding")
    fixed_inputs = _mapping(raw_inputs, "fixed plan inputs")
    compatibility = _mapping(
        fixed_inputs.get("compatibility_inputs"), "fixed compatibility inputs"
    )
    actual_sha256 = _hash_json(compatibility)
    if (
        binding.get("schema_id")
        != FIXED_CASE_BINDING_SCHEMA_ID
        or binding.get("schema_version") != FIXED_CASE_BINDING_SCHEMA_VERSION
        or binding.get("canonical_domain")
        != run_configuration.get("canonical_domain")
        or not isinstance(binding.get("case_id"), str)
        or not str(binding.get("case_id"))
        or not _hex_sha256(binding.get("plan_identity_sha256"))
        or not _hex_sha256(
            binding.get("case_configuration_identity_sha256")
        )
        or binding.get("target_ppfd_umol_m2_s")
        != FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S
        or not _fixed_target_matches_request(binding, run_configuration)
        or binding.get("compatibility_inputs_sha256") != actual_sha256
        or fixed_inputs.get("compatibility_inputs_sha256") != actual_sha256
    ):
        raise ValueError("fixed production case context is inconsistent.")


def _validate_viewer_payloads(
    *,
    manifest: Mapping[str, object],
    public: Mapping[str, object],
    payloads: Mapping[str, bytes],
    scene: Mapping[str, object],
) -> None:
    identities = _mapping(
        public.get("authenticated_identities"), "authenticated identities"
    )
    fixture_identity = _mapping(
        identities.get("fixture_asset_and_occlusion"), "fixture identity"
    )
    solver = _mapping(
        _mapping(public.get("run_configuration"), "run configuration").get(
            "solver"
        ),
        "solver configuration",
    )
    publication = _mapping(
        identities.get("publication_compatibility"), "publication compatibility"
    )
    if (
        identities.get("source_state_sha256")
        != _sha256(payloads["physical_source_state"])
        or identities.get("scene_compatibility_sha256") != _hash_json(scene)
        or scene.get("viewer_resource_version") != VIEWER_RESOURCE_VERSION
        or publication.get("viewer_resource_version") != VIEWER_RESOURCE_VERSION
        or solver.get("policy_identity_sha256")
        != identities.get("solver_policy_sha256")
        or solver.get("fixture_occlusion")
        != fixture_identity.get("scientific_occlusion")
    ):
        raise ValueError("source-state or scene compatibility identity disagrees.")

    profile = _json_object_bytes(payloads["plant_profile"], "plant profile")
    identity_map = _json_object_bytes(payloads["plant_identity"], "plant identity")
    scene_profile = _mapping(scene.get("profile"), "scene profile")
    if (
        scene_profile.get("manifest_sha256") != _sha256(payloads["plant_profile"])
        or scene_profile.get("profile_id") != profile.get("profile_id")
        or scene_profile.get("sampling_profile_id")
        != profile.get("sampling_profile_id")
        or identity_map.get("profile_id") != profile.get("profile_id")
        or identity_map.get("sampling_profile_id")
        != profile.get("sampling_profile_id")
    ):
        raise ValueError("plant profile and interaction identities disagree.")
    profile_artifacts = _mapping(profile.get("artifacts"), "profile artifacts")
    for name, artifact_name in (
        ("plant_geometry", "geometry"),
        ("plant_identity", "identity_map"),
        ("plant_receivers", "receivers"),
    ):
        _validate_embedded_artifact(
            _mapping(profile_artifacts.get(artifact_name), artifact_name),
            payloads[name],
            size_key="byte_size",
            label=name,
        )

    plant_instances = _mapping(scene.get("plant_instances"), "plant instances")
    instance_record = _mapping(
        plant_instances.get("instance_translations"), "plant transforms"
    )
    _validate_embedded_artifact(
        instance_record,
        payloads["viewer_instances"],
        size_key="byte_length",
        label="plant transforms",
    )
    if (
        instance_record.get("stride_bytes") != 12
        or instance_record.get("count")
        != len(_list(plant_instances.get("plant_ids"), "scene plant IDs"))
        or instance_record.get("byte_length")
        != int(instance_record.get("count", -1)) * 12
    ):
        raise ValueError("plant transform count or stride is inconsistent.")

    catalog_bytes = payloads["fixture_catalog"]
    catalog = _json_object_bytes(catalog_bytes, "fixture catalog")
    scene_fixtures = _mapping(scene.get("fixtures"), "scene fixtures")
    groups = _list(catalog.get("asset_groups"), "fixture asset groups")
    if (
        scene_fixtures.get("catalog_sha256") != _sha256(catalog_bytes)
        or scene_fixtures.get("catalog_byte_length") != len(catalog_bytes)
        or scene_fixtures.get("asset_group_count") != len(groups)
        or fixture_identity.get("catalog_sha256") != _sha256(catalog_bytes)
        or fixture_identity.get("authoritative_layout_sha256")
        != catalog.get("authoritative_layout_sha256")
        or fixture_identity.get("fixture_plan_sha256")
        != catalog.get("fixture_plan_sha256")
    ):
        raise ValueError("fixture catalog identity disagrees.")
    payload_records = _payload_records(manifest)
    asset_hashes = _mapping(
        fixture_identity.get("asset_sha256_by_id"), "fixture asset hashes"
    )
    references = {
        str(_mapping(record, "catalog asset reference").get("asset_id")): _mapping(
            record, "catalog asset reference"
        )
        for record in _list(manifest.get("catalog_assets"), "catalog assets")
    }
    if len(references) != len(groups):
        raise ValueError("fixture catalog asset references are incomplete.")
    for index, raw_group in enumerate(groups):
        group = _mapping(raw_group, "fixture asset group")
        matrices = _mapping(group.get("instance_matrices"), "fixture transforms")
        transform_name = f"fixture_transforms_{index:02d}"
        transform_record = _mapping(
            payload_records.get(transform_name), "fixture transform payload"
        )
        relative = _safe_relative_text(matrices.get("filename"), "fixture transforms")
        if transform_record.get("path") != f"payload/viewer/fixtures/{relative}":
            raise ValueError("fixture transform path disagrees with catalog.")
        _validate_embedded_artifact(
            matrices,
            payloads[transform_name],
            size_key="byte_length",
            label=transform_name,
        )
        if (
            matrices.get("stride_bytes") != 64
            or matrices.get("byte_length")
            != int(matrices.get("count", -1)) * 64
        ):
            raise ValueError("fixture transform count or stride is inconsistent.")
        registry = _mapping(group.get("registry"), "fixture registry")
        asset = _mapping(group.get("asset"), "fixture asset")
        asset_id = _nonempty_text(registry.get("asset_id"), "fixture asset ID")
        reference = _mapping(references.get(asset_id), "catalog asset reference")
        if (
            asset_hashes.get(str(group.get("display_asset_id")))
            != asset.get("sha256")
            or reference.get("sha256") != asset.get("sha256")
            or reference.get("byte_size") != asset.get("byte_size")
            or reference.get("resource_path")
            != registry.get("packaged_resource_path")
            or reference.get("materialized_path")
            != "plant-layout-viewer/fixtures/"
            + _safe_relative_text(asset.get("filename"), "fixture asset")
            or reference.get("package") != "fspm_optics.resources.viewer"
        ):
            raise ValueError("fixture catalog asset reference disagrees.")


def _validate_embedded_artifact(
    record: Mapping[str, object],
    data: bytes,
    *,
    size_key: str,
    label: str,
) -> None:
    if record.get(size_key) != len(data) or record.get("sha256") != _sha256(data):
        raise ValueError(f"{label} embedded identity disagrees.")


def _solver_configuration(manifest: Mapping[str, object]) -> dict[str, object]:
    policy = _mapping(manifest.get("transport_policy"), "transport policy")
    compact = {
        key: value for key, value in policy.items() if key != "fixture_occlusion"
    }
    compact["policy_identity_sha256"] = _hash_json(policy)
    occlusion = _fixture_occlusion_identity(manifest)
    if occlusion is not None:
        compact["fixture_occlusion"] = occlusion
    return compact


def _fixture_occlusion_identity(
    manifest: Mapping[str, object],
) -> dict[str, object] | None:
    policy = _mapping(manifest.get("transport_policy"), "transport policy")
    engine = _mapping(manifest.get("engine_provenance"), "engine provenance")
    candidates = (policy.get("fixture_occlusion"), engine.get("fixture_occlusion"))
    raw = next((value for value in candidates if isinstance(value, Mapping)), None)
    if raw is None:
        return None
    occlusion = dict(raw)
    assets: list[dict[str, object]] = []
    for value in _list(occlusion.get("assets"), "scientific occlusion assets"):
        asset = _mapping(value, "scientific occlusion asset")
        assets.append(
            {
                key: asset[key]
                for key in (
                    "asset_id",
                    "fixture_type",
                    "glb_sha256",
                    "glb_byte_size",
                    "node_primitive_inventory_sha256",
                    "classification_sha256",
                    "placement_contract_sha256",
                )
                if key in asset
            }
        )
    return {
        key: occlusion[key]
        for key in (
            "occlusion_version",
            "system_id",
            "identity_sha256",
            "classification_manifest_sha256",
            "transform_policy_id",
            "transform_set_sha256",
            "projected_area_policy_id",
            "emitting_boundary_policy_id",
            "counts",
            "projected_opaque_area_m2",
            "emitting_boundary_area_m2",
            "raw_glb_pbr_materials_used_for_transport",
            "optical_stack_geometry_replaced",
            "hps_included",
        )
        if key in occlusion
    } | {"assets": assets}


def _run_configuration(
    manifest: Mapping[str, object], metrics: Mapping[str, object]
) -> dict[str, object]:
    request = _mapping(manifest.get("request"), "native request")
    room = _mapping(manifest.get("room"), "native room")
    mounting = _mapping(manifest.get("mounting_height"), "mounting height")
    active_domain = _mapping(manifest.get("active_domain"), "active domain")
    result: dict[str, object] = {
        "system_id": manifest.get("system_id"),
        "request": dict(request),
        "ordered_room_dimensions": [
            {
                "axis": "length",
                "feet": request.get("room_length_ft"),
                "meters": room.get("length_m"),
            },
            {
                "axis": "width",
                "feet": request.get("room_width_ft"),
                "meters": room.get("width_m"),
            },
        ],
        "dimension_units": {"input": "foot", "scientific": "meter"},
        "canonical_domain": _canonical_domain_from_request(request),
        "aisle": {
            "enabled": request.get("aisle_mode"),
            "active_domain_identity_sha256": active_domain.get(
                "identity_sha256"
            ),
        },
        "layout_mode": {
            key: request[key]
            for key in (
                "layout_mode",
                "proposed_layout_mode",
                "proposed_ring_mode",
            )
            if key in request
        },
        "mounting_height": dict(mounting),
        "solver": _solver_configuration(manifest),
        "spectral": {
            "analysis_scope": request.get("analysis_scope"),
            "include_far_red": request.get("include_far_red", False),
            "spectral_basis": request.get("spectral_basis"),
        },
        "radiance_quality": manifest.get("quality"),
        "far_red": {
            "requested": request.get("include_far_red", False),
            "executed": (
                _mapping(
                    metrics.get("fspm_surface_light_metrics"),
                    "FSPM metrics",
                    allow_none=True,
                ).get("far_red_executed")
                if metrics.get("fspm_surface_light_metrics") is not None
                else False
            ),
        },
    }
    return result


def _canonical_domain_from_request(
    request: Mapping[str, object],
) -> dict[str, object]:
    length_ft = _positive_number(
        request.get("room_length_ft"), "requested room length"
    )
    width_ft = _positive_number(
        request.get("room_width_ft"), "requested room width"
    )
    frame = RoomCoordinateFrame(length_ft * 0.3048, width_ft * 0.3048)
    long_ft = max(length_ft, width_ft)
    short_ft = min(length_ft, width_ft)
    supported = (
        ((long_ft, short_ft),)
        if long_ft == short_ft
        else ((short_ft, long_ft), (long_ft, short_ft))
    )
    return {
        "schema_id": CANONICAL_DOMAIN_SCHEMA_ID,
        "schema_version": CANONICAL_DOMAIN_SCHEMA_VERSION,
        "coordinate_frame_policy_id": ROOM_FRAME_POLICY_ID,
        "canonical_aligned_room": {
            "length_x_ft": long_ft,
            "width_y_ft": short_ft,
            "length_x_m": frame.simulation_length_m,
            "width_y_m": frame.simulation_width_m,
        },
        "physical_room_label_ft": {
            "length": short_ft,
            "width": long_ft,
        },
        "supported_requested_room_orders_ft": [
            {"length": item[0], "width": item[1]} for item in supported
        ],
        "mapping": {
            "square_rotation_degrees_about_z": 0,
            "portrait_requested_to_simulation_rotation_degrees_about_z": -90,
            "simulation_to_portrait_requested_rotation_degrees_about_z": 90,
            "determinant": 1,
            "translation_m": [0.0, 0.0, 0.0],
            "reflection": False,
            "scaling": False,
        },
    }


def _bind_fixed_case_context(
    *,
    run_configuration: dict[str, object],
    identities: dict[str, object],
    fixed_case_binding: Mapping[str, object] | None,
    fixed_plan_inputs: Mapping[str, object] | None,
) -> None:
    if (fixed_case_binding is None) != (fixed_plan_inputs is None):
        raise ValueError(
            "fixed case binding and compatibility inputs must be supplied together."
        )
    if fixed_case_binding is None or fixed_plan_inputs is None:
        return
    binding = dict(fixed_case_binding)
    inputs = dict(fixed_plan_inputs)
    expected_domain = run_configuration.get("canonical_domain")
    if (
        binding.get("schema_id")
        != FIXED_CASE_BINDING_SCHEMA_ID
        or binding.get("schema_version") != FIXED_CASE_BINDING_SCHEMA_VERSION
        or not isinstance(binding.get("case_id"), str)
        or not str(binding.get("case_id"))
        or binding.get("canonical_domain") != expected_domain
        or not _hex_sha256(binding.get("plan_identity_sha256"))
        or not _hex_sha256(
            binding.get("case_configuration_identity_sha256")
        )
        or binding.get("target_ppfd_umol_m2_s")
        != FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S
        or not _fixed_target_matches_request(binding, run_configuration)
        or not _hex_sha256(binding.get("compatibility_inputs_sha256"))
    ):
        raise ValueError("fixed production case binding is invalid.")
    actual_inputs_sha256 = _hash_json(inputs)
    if binding["compatibility_inputs_sha256"] != actual_inputs_sha256:
        raise ValueError(
            "fixed production compatibility inputs disagree with their identity."
        )
    _assert_portable(binding)
    _assert_portable(inputs)
    run_configuration["fixed_plan"] = binding
    identities["fixed_plan_inputs"] = {
        "compatibility_inputs_sha256": actual_inputs_sha256,
        "compatibility_inputs": inputs,
    }


def _fixed_target_matches_request(
    binding: Mapping[str, object],
    run_configuration: Mapping[str, object],
) -> bool:
    request = _mapping(run_configuration.get("request"), "fixed plan request")
    system_id = run_configuration.get("system_id")
    semantics = binding.get("target_semantics")
    historical_fixed_output_system = (
        isinstance(system_id, str)
        and _sha256(system_id.encode("utf-8"))
        == "2a68e08ab13ad9b27e1d6d1c8663e474737ea809c3ec953efd9ad5d6084689f3"
    )
    if system_id == "hps" or historical_fixed_output_system:
        return (
            "target_ppfd_umol_m2_s" not in request
            and "lighting_target_mode" not in request
            and semantics == FIXED_OUTPUT_TARGET_SEMANTICS
        )
    return (
        system_id in {"proposed", "conventional"}
        and request.get("target_ppfd_umol_m2_s")
        == FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S
        and request.get("lighting_target_mode") == "mean_target"
        and semantics == MEAN_TARGET_SEMANTICS
    )


def _authenticated_identities(
    *,
    manifest: Mapping[str, object],
    source_state: Mapping[str, object],
    visualization: Mapping[str, object],
    scene: Mapping[str, object],
    catalog: Mapping[str, object],
) -> dict[str, object]:
    scientific = dict(
        _mapping(manifest.get("scientific_identity"), "scientific identity")
    )
    transport_policy = _mapping(
        manifest.get("transport_policy"), "transport policy"
    )
    transport = manifest.get("multispectral_transport")
    aggregation = manifest.get("fspm_scientific_aggregation")
    return {
        "scientific": scientific,
        "source_state_id": source_state.get("source_state_id"),
        "source_state_sha256": _sha256(
            _canonical_json_bytes(source_state, newline=True, indent=2)
        ),
        "fixture_asset_and_occlusion": {
            "catalog_sha256": _sha256(
                _canonical_json_bytes(catalog, newline=True, indent=2)
            ),
            "authoritative_layout_sha256": catalog.get(
                "authoritative_layout_sha256"
            ),
            "fixture_plan_sha256": catalog.get("fixture_plan_sha256"),
            "asset_sha256_by_id": {
                str(_mapping(group, "fixture group").get("display_asset_id")): (
                    _mapping(
                        _mapping(group, "fixture group").get("asset"),
                        "fixture asset",
                    ).get("sha256")
                )
                for group in _list(catalog.get("asset_groups"), "fixture groups")
            },
            "scientific_occlusion": _fixture_occlusion_identity(manifest),
        },
        "solver_policy_sha256": _hash_json(transport_policy),
        "scene_compatibility_sha256": _hash_json(scene),
        "transport_compatibility_sha256": (
            _hash_json(transport) if transport is not None else None
        ),
        "aggregation_compatibility_sha256": (
            _hash_json(aggregation) if aggregation is not None else None
        ),
        "result_compatibility_sha256": scientific.get("scientific_run_sha256"),
        "publication_compatibility": {
            "native_schema_id": manifest.get("schema_id"),
            "native_schema_version": manifest.get("schema_version"),
            "viewer_resource_version": _mapping(
                manifest.get("viewer_publication"), "viewer publication"
            ).get("resource_version"),
            "visualization_schema_id": visualization.get("schema_id"),
            "visualization_schema_version": visualization.get("schema_version"),
        },
    }


def _metrics_payload(source: Mapping[str, object]) -> dict[str, object]:
    missing = [field for field in METRICS_FIELDS if field not in source]
    if missing:
        raise ValueError(f"native metrics omit required fields: {missing}")
    result = {field: source[field] for field in METRICS_FIELDS}
    result.update(
        {
            field: source[field]
            for field in OPTIONAL_METRICS_FIELDS
            if field in source
        }
    )
    power = _mapping(result["power"], "metrics power")
    ppf = _mapping(result["ppf"], "metrics PPF")
    result["power"] = {
        key: power[key]
        for key in ("full_output_w", "effective_w")
        if key in power
    }
    result["ppf"] = {
        key: ppf[key]
        for key in (
            "emitted_umol_s",
            "emission_boundary_id",
            "emission_boundary_description",
        )
        if key in ppf
    }
    surface = result.get("fspm_surface_light_metrics")
    if surface is not None:
        surface_map = _mapping(surface, "FSPM surface-light metrics")
        surface_light = _mapping(surface_map.get("surface_light"), "surface light")
        groups = {
            group: surface_light[group]
            for group in ("par", "far_red")
            if group in surface_light
        }
        absorbed = _mapping(
            surface_map.get("absorbed_par_metrics"), "absorbed PAR metrics"
        )
        result["fspm_surface_light_metrics"] = {
            "modeled_physical_one_sided_leaf_area_m2": surface_map.get(
                "modeled_physical_one_sided_leaf_area_m2"
            ),
            "counts": surface_map.get("counts"),
            "surface_light": groups,
            "absorbed_par_metrics": {
                field: absorbed[field]
                for field in ABSORBED_METRIC_FIELDS
                if field in absorbed
            },
            "band_order": surface_map.get("band_order"),
            "far_red_executed": surface_map.get("far_red_executed"),
        }
    return result


def _normalized_viewer_scene(scene: Mapping[str, object]) -> dict[str, object]:
    normalized = dict(scene)
    normalized.pop("surface_flux", None)
    return normalized


def _capabilities(metrics: Mapping[str, object]) -> tuple[str, ...]:
    capabilities = list(CAPABILITIES_BASE)
    surface = metrics.get("fspm_surface_light_metrics")
    if surface is not None:
        capabilities.append("aggregate_absorbed_par_metrics_v1")
        if _mapping(surface, "FSPM metrics").get("far_red_executed") is True:
            capabilities.append("optional_far_red_aggregate_analysis_v1")
    return tuple(sorted(capabilities))


def _materialize_payloads(playback: CompactPlayback, root: Path) -> None:
    public = playback.public_payload
    metrics = playback.metrics
    result_metadata = _mapping(public.get("result_metadata"), "result metadata")
    _write_json(root / "metrics.json", metrics, indent=2)
    _write_json(
        root / "manifest.json",
        {
            "schema_id": "fspm-optics.compact-precomputed-playback",
            "schema_version": 1,
            "run_id": playback.run_id,
            "system_id": playback.system_id,
            "bundle_identity_sha256": playback.manifest[
                "bundle_identity_sha256"
            ],
            "run_configuration": public["run_configuration"],
            "authenticated_identities": public["authenticated_identities"],
            "capabilities": public["capabilities"],
            "result_metadata": result_metadata,
            "source_runtime_required": False,
        },
        indent=2,
    )
    filename, _mime, csv_data = playback.final_baseline_ppfd_csv()
    (root / filename).write_bytes(csv_data)
    for name, destination in (
        ("baseline_leaf_uniformity", "baseline-leaf-position-uniformity.v1.json"),
        ("natural_fit_layout", "natural_fit_layout.json"),
        ("physical_source_state", "physical-source-state.json"),
        ("visualization_metadata", "visualization.json"),
        ("heatmap", "ppfd-heatmap.png"),
        ("heatmap_overlay", "ppfd-heatmap-overlay.png"),
    ):
        (root / destination).write_bytes(playback.payloads[name])
    for name, destination in (
        ("target_control", "target_control.json"),
        ("full_output_schedule", "full_output_schedule.json"),
        ("operating_point", "operating-point.json"),
    ):
        (root / destination).write_bytes(playback.payloads[name])
    scatter = _scatter_bytes(playback.samples)
    visualization = playback.visualization
    scatter_record = _mapping(visualization.get("scatter"), "scatter")
    if _sha256(scatter) != scatter_record.get("sha256"):
        raise ValueError("materialized scatter fails authenticated identity.")
    (root / "ppfd-scatter.f32le.bin").write_bytes(scatter)

    viewer_root = root / "plant-layout-viewer"
    viewer_root.mkdir()
    _copy_resource_tree(
        resources.files("fspm_optics").joinpath("resources", "viewer"),
        viewer_root,
        exclude={"fixtures"},
    )
    (viewer_root / "scene.v1.json").write_bytes(playback.payloads["viewer_scene"])
    (viewer_root / "instances.f32le.bin").write_bytes(
        playback.payloads["viewer_instances"]
    )
    profile_root = (
        viewer_root / "profiles" / "rex_juvenile_preheading_12leaf_v1"
    )
    profile_root.mkdir(parents=True)
    for name, filename_value in (
        ("plant_profile", "profile.v1.json"),
        ("plant_geometry", "geometry.glb"),
        ("plant_identity", "identity-map.v1.json"),
        ("plant_receivers", "receivers.f32le.bin"),
    ):
        (profile_root / filename_value).write_bytes(playback.payloads[name])
    fixture_root = viewer_root / "fixtures"
    fixture_root.mkdir()
    (fixture_root / "catalog.v1.json").write_bytes(
        playback.payloads["fixture_catalog"]
    )
    for name, record in _payload_records(playback.manifest).items():
        if name.startswith("fixture_transforms_"):
            relative = PurePosixPath(str(record["path"])).relative_to(
                "payload/viewer/fixtures"
            )
            destination = fixture_root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(playback.payloads[name])
    for raw in _list(playback.manifest.get("catalog_assets"), "catalog assets"):
        record = _mapping(raw, "catalog asset")
        source = resources.files("fspm_optics").joinpath(
            "resources", "viewer", *_safe_parts(record["resource_path"])
        )
        data = source.read_bytes()
        if len(data) != record["byte_size"] or _sha256(data) != record["sha256"]:
            raise ValueError("packaged fixture catalog asset failed authentication.")
        destination = root.joinpath(*_safe_parts(record["materialized_path"]))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    heatmap_root = viewer_root / "ppfd-heatmap"
    heatmap_root.mkdir()
    (heatmap_root / "visualization.json").write_bytes(
        playback.payloads["visualization_metadata"]
    )
    (heatmap_root / "ppfd-scatter.f32le.bin").write_bytes(scatter)

    scatter_root = root / "ppfd-scatter-viewer"
    scatter_root.mkdir()
    _copy_resource_tree(
        resources.files("fspm_optics").joinpath("resources", "scatter"),
        scatter_root,
    )


def _copy_resource_tree(
    source: Any,
    destination: Path,
    *,
    exclude: set[str] | None = None,
) -> None:
    excluded = exclude or set()
    for child in source.iterdir():
        if child.name in excluded:
            continue
        target = destination / child.name
        if child.is_dir():
            target.mkdir(exist_ok=True)
            _copy_resource_tree(child, target)
        elif child.is_file():
            target.write_bytes(child.read_bytes())


def _validate_historical_directory(path: Path) -> CompactBundleValidation:
    manifest_path = path / MANIFEST_NAME
    if not manifest_path.is_file():
        return _validation(
            CompactBundleStatus.PARTIAL,
            "directory bundle has no manifest.",
        )
    try:
        manifest = _read_json(manifest_path, "directory bundle manifest")
    except (OSError, ValueError) as exc:
        return _validation(
            CompactBundleStatus.CORRUPT,
            f"directory manifest is invalid: {exc}",
        )
    return _validation(
        CompactBundleStatus.INCOMPATIBLE_SCHEMA,
        "historical directory bundles require an explicit legacy loader and "
        "are not compact schema v1.",
        manifest=manifest,
    )


def _encode_stage_a(samples: Sequence[PpfdMapSample]) -> bytes:
    if not samples:
        raise ValueError("Stage A payload requires at least one sample.")
    data = b"".join(
        STAGE_A_RECORD.pack(
            float(sample.x_m),
            float(sample.y_m),
            float(sample.z_m),
            float(sample.ppfd_umol_m2_s),
        )
        for sample in samples
    )
    if any(not math.isfinite(value) for sample in samples for value in (
        sample.x_m, sample.y_m, sample.z_m, sample.ppfd_umol_m2_s
    )):
        raise ValueError("Stage A payload contains a non-finite value.")
    return data


def _decode_stage_a(data: bytes) -> tuple[PpfdMapSample, ...]:
    if not data or len(data) % STAGE_A_RECORD.size:
        raise ValueError("Stage A payload is empty or truncated.")
    samples = tuple(
        PpfdMapSample(*STAGE_A_RECORD.unpack_from(data, offset))
        for offset in range(0, len(data), STAGE_A_RECORD.size)
    )
    if any(not math.isfinite(value) for sample in samples for value in (
        sample.x_m, sample.y_m, sample.z_m, sample.ppfd_umol_m2_s
    )):
        raise ValueError("Stage A payload contains a non-finite value.")
    return samples


def _read_ppfd_csv(path: Path) -> tuple[PpfdMapSample, ...]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["x_m", "y_m", "z_m", "ppfd_umol_m2_s"]:
            raise ValueError("Final Baseline PPFD CSV header is incompatible.")
        samples = tuple(
            PpfdMapSample(
                float(row["x_m"]),
                float(row["y_m"]),
                float(row["z_m"]),
                float(row["ppfd_umol_m2_s"]),
            )
            for row in reader
        )
    if not samples:
        raise ValueError("Final Baseline PPFD CSV is empty.")
    return samples


def _ppfd_csv(samples: Sequence[PpfdMapSample]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("x_m", "y_m", "z_m", "ppfd_umol_m2_s"))
    for sample in samples:
        writer.writerow(
            tuple(
                format(value, ".17g")
                for value in (
                    sample.x_m,
                    sample.y_m,
                    sample.z_m,
                    sample.ppfd_umol_m2_s,
                )
            )
        )
    return stream.getvalue()


def _scatter_bytes(samples: Sequence[PpfdMapSample]) -> bytes:
    return b"".join(
        struct.pack(
            "<fff",
            float(sample.x_m),
            float(sample.y_m),
            float(sample.ppfd_umol_m2_s),
        )
        for sample in samples
    )


def _expected_scatter_bytes(source: Path) -> bytes:
    return (source / "ppfd-scatter.f32le.bin").read_bytes()


def _bundle_identity(manifest: Mapping[str, object]) -> str:
    preimage = dict(manifest)
    preimage.pop("bundle_identity_sha256", None)
    return _hash_json(preimage)


def _payload_records(manifest: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    return {
        str(record["name"]): record
        for record in _list(manifest.get("payload_inventory"), "payload inventory")
        if isinstance(record, Mapping)
    }


def _validation(
    status: CompactBundleStatus,
    message: str,
    *,
    bundle_identity: str | None = None,
    manifest: Mapping[str, object] | None = None,
) -> CompactBundleValidation:
    return CompactBundleValidation(status, message, bundle_identity, manifest)


def _mapping_contains(
    actual: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    for key, value in expected.items():
        if key not in actual:
            return False
        observed = actual[key]
        if isinstance(value, Mapping):
            if not isinstance(observed, Mapping) or not _mapping_contains(
                observed, value
            ):
                return False
        elif observed != value:
            return False
    return True


def _safe_archive_name(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and not value.startswith("/")
        and "\\" not in value
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _zip_entry_is_link(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)


def _looks_like_truncated_zip(path: Path) -> bool:
    """Distinguish interrupted ZIP publication from unrelated/corrupt bytes."""

    try:
        size = path.stat().st_size
        if size < 4:
            return True
        with path.open("rb") as handle:
            prefix = handle.read(4)
            tail_size = min(size, 65_557)
            handle.seek(size - tail_size)
            tail = handle.read(tail_size)
    except OSError:
        return False
    return prefix.startswith(b"PK") and b"PK\x05\x06" not in tail


def _safe_parts(value: object) -> tuple[str, ...]:
    text = _safe_relative_text(value, "resource path")
    return PurePosixPath(text).parts


def _safe_relative_text(value: object, label: str) -> str:
    text = _nonempty_text(value, label)
    if not _safe_archive_name(text):
        raise ValueError(f"{label} is not a safe relative path.")
    return text


def _assert_portable(value: object, *, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if lowered in {
                "timestamp",
                "created_at",
                "completed_at",
                "temporary_path",
                "absolute_path",
                "process_id",
                "pid",
            }:
                raise ValueError(f"{path}.{key_text} is nondeterministic or local.")
            _assert_portable(item, path=f"{path}.{key_text}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _assert_portable(item, path=f"{path}[{index}]")
    elif isinstance(value, str):
        if value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value):
            raise ValueError(f"{path} contains an absolute filesystem path.")


def _excluded_category(relative: str) -> str:
    lowered = relative.lower()
    if "/vendor/" in lowered or lowered.endswith((".js", ".css", ".html")):
        return "duplicated viewer/browser resources"
    if "/fixtures/assets/" in lowered or lowered.endswith(".glb"):
        return "duplicate immutable geometry/catalog assets"
    if lowered.endswith((".oct", ".amb", ".mtx", ".npy", ".npz")):
        return "octrees, caches, and raw matrices"
    if "fspm-transport/" in lowered or "fspm-aggregation/" in lowered:
        return "raw/intermediate multispectral and aggregation artifacts"
    if lowered.endswith((".log", ".jsonl")) or "command" in lowered:
        return "logs, events, and command transcripts"
    if lowered.endswith((".rad", ".dat", ".pts")):
        return "temporary scene and solver inputs"
    return "other non-playback runtime artifacts"


def _read_json(path: Path, label: str) -> dict[str, object]:
    return _json_object_bytes(path.read_bytes(), label)


def _json_object_bytes(data: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root is not an object.")
    return value


def _write_json(path: Path, payload: Mapping[str, object], *, indent: int) -> None:
    path.write_bytes(_canonical_json_bytes(payload, newline=True, indent=indent))


def _canonical_json_bytes(
    value: object,
    *,
    newline: bool = False,
    indent: int | None = None,
) -> bytes:
    text = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":") if indent is None else None,
        ensure_ascii=False,
        allow_nan=False,
        indent=indent,
    )
    return (text + ("\n" if newline else "")).encode("utf-8")


def _hash_json(value: object) -> str:
    return _sha256(_canonical_json_bytes(value))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mapping(
    value: object,
    label: str,
    *,
    allow_none: bool = False,
) -> dict[str, object]:
    if value is None and allow_none:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is not an object.")
    return dict(value)


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} is not an array.")
    return value


def _nonempty_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is not non-empty text.")
    return value


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be a finite positive number.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} must be a finite positive number.")
    return number


def _hex_sha256(value: object) -> bool:
    return isinstance(value, str) and HEX_SHA256.fullmatch(value) is not None


__all__ = [
    "COMPACT_BUNDLE_SCHEMA_ID",
    "COMPACT_BUNDLE_SCHEMA_VERSION",
    "BundleSizeReport",
    "CompactBundleError",
    "CompactBundleExport",
    "CompactBundleStatus",
    "CompactBundleValidation",
    "CompactPlayback",
    "export_compact_bundle",
    "load_compact_bundle",
    "materialize_compact_playback",
    "measure_compact_bundle",
    "normalize_live_public_payload",
    "validate_compact_bundle",
]
