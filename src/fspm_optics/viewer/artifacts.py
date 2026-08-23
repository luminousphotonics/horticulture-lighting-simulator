"""Deterministic juvenile display artifacts and integrity validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from typing import Mapping

from fspm_optics.geometry.mounting import MountingGeometry
from fspm_optics.plants import (
    NaturalFitLayoutPlan,
    REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID,
    REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS,
    RexJuvenilePreheadingConfig,
    generate_rex_juvenile_preheading_plant,
)
from fspm_optics.plants.models import PlantMesh
from fspm_optics.receivers.samples import build_two_sided_patch_receivers
from fspm_optics.viewer.gltf import (
    build_juvenile_geometry_glb,
    scientific_to_display,
)
from fspm_optics.viewer.models import (
    BinaryDisplayArtifact,
    PpfdHeatmapViewerArtifacts,
    SurfaceFluxViewerArtifacts,
    ViewerArtifactSet,
)

PROFILE_SCHEMA_ID = "fspm-optics.juvenile-rex-viewer-profile"
PROFILE_SCHEMA_VERSION = 3
IDENTITY_SCHEMA_ID = "fspm-optics.juvenile-rex-identity-map"
IDENTITY_SCHEMA_VERSION = 2
SCENE_SCHEMA_ID = "fspm-optics.juvenile-rex-run-scene"
SCENE_SCHEMA_VERSION = 4
VIEWER_RESOURCE_VERSION = "phase27g-d6-target-coverage-v1"
PPFD_HEATMAP_VIEWER_RESOURCE_VERSION = "phase27h-g-ppfd-heatmap-v1"
MOUNTING_SCENE_SCHEMA_VERSION = 3
MOUNTING_VIEWER_RESOURCE_VERSION = "phase27h-b-mounting-height-v1"
D4_VIEWER_RESOURCE_VERSION = "phase27g-d4-viewer-leaf-materials-v1"
D3_VIEWER_RESOURCE_VERSION = "phase27g-d3b-sampling-profile-v1"
LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION = "phase27g-d2-surface-flux-v1"
PROFILE_ID = "rex_juvenile_preheading_12leaf_v1"
INSTANCE_TRANSLATION_STRIDE_BYTES = 12
SUPPORTED_SYSTEM_IDS = frozenset(("proposed", "conventional", "hps"))
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")


def build_run_viewer_artifacts(
    *,
    run_id: str,
    system_id: str,
    requested_length_ft: float,
    requested_width_ft: float,
    natural_fit: NaturalFitLayoutPlan,
    natural_fit_artifact_sha256: str,
    fixture_catalog_sha256: str,
    fixture_catalog_byte_length: int,
    fixture_authoritative_layout_sha256: str,
    fixture_plan_sha256: str,
    fixture_count: int,
    fixture_asset_group_count: int,
    mounting_height: Mapping[str, object] | None = None,
    run_information: Mapping[str, object] | None = None,
    surface_flux: SurfaceFluxViewerArtifacts | None = None,
    ppfd_heatmap: PpfdHeatmapViewerArtifacts | None = None,
    sampling_profile_id: str = (
        REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
    ),
    canonical_plant: PlantMesh | None = None,
) -> ViewerArtifactSet:
    """Build the canonical juvenile profile and one authorized run scene."""

    _validate_run_context(
        run_id=run_id,
        system_id=system_id,
        requested_length_ft=requested_length_ft,
        requested_width_ft=requested_width_ft,
        natural_fit=natural_fit,
        natural_fit_artifact_sha256=natural_fit_artifact_sha256,
    )
    _validate_fixture_reference(
        fixture_catalog_sha256,
        fixture_catalog_byte_length,
        fixture_authoritative_layout_sha256,
        fixture_plan_sha256,
        fixture_count,
        fixture_asset_group_count,
    )

    plant = canonical_plant or generate_rex_juvenile_preheading_plant(
        RexJuvenilePreheadingConfig(sampling_profile_id=sampling_profile_id)
    )
    if plant.config.sampling_profile_id != sampling_profile_id:
        raise ValueError(
            "canonical plant and viewer sampling profile identities disagree."
        )
    _validate_juvenile_contract(plant)
    if surface_flux is not None:
        optimized = (
            plant.config.sampling_profile_id
            == REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
        )
        if optimized and surface_flux.schema_version != 3:
            raise ValueError(
                "surface-flux display is unavailable for the uncalibrated optimized "
                "receiver sampling profile."
            )
        if not optimized and surface_flux.schema_version == 3:
            raise ValueError(
                "surface-flux metadata and receiver sampling profiles are incompatible."
            )
    identity_payload, face_to_patch = _identity_payload(plant)
    geometry_bytes, geometry_position_error, geometry_normal_error = (
        build_juvenile_geometry_glb(plant, face_to_patch)
    )
    receiver_bytes, receiver_position_error, receiver_normal_error = (
        _receiver_bytes(plant)
    )
    identity_bytes = _json_bytes(identity_payload)
    geometry = _artifact("geometry.glb", geometry_bytes)
    identity = _artifact("identity-map.v1.json", identity_bytes)
    receivers = _artifact("receivers.f32le.bin", receiver_bytes)
    profile_payload = _profile_payload(
        plant,
        (geometry, identity, receivers),
        max_position_error=max(
            geometry_position_error,
            receiver_position_error,
        ),
        max_normal_error=max(geometry_normal_error, receiver_normal_error),
    )
    profile_manifest = _artifact(
        "profile.v1.json",
        _json_bytes(profile_payload),
    )
    scene_payload, translations = _run_scene_payload(
        run_id=run_id,
        system_id=system_id,
        requested_length_ft=requested_length_ft,
        requested_width_ft=requested_width_ft,
        natural_fit=natural_fit,
        natural_fit_artifact_sha256=natural_fit_artifact_sha256,
        profile_manifest=profile_manifest,
        plant=plant,
        fixture_catalog_sha256=fixture_catalog_sha256,
        fixture_catalog_byte_length=fixture_catalog_byte_length,
        fixture_authoritative_layout_sha256=fixture_authoritative_layout_sha256,
        fixture_plan_sha256=fixture_plan_sha256,
        fixture_count=fixture_count,
        fixture_asset_group_count=fixture_asset_group_count,
        mounting_height=mounting_height,
        run_information=run_information,
        surface_flux=surface_flux,
        ppfd_heatmap=ppfd_heatmap,
    )
    scene_manifest = _artifact(
        "scene.v1.json",
        _json_bytes(scene_payload),
    )
    artifact_set = ViewerArtifactSet(
        profile_id=PROFILE_ID,
        sampling_profile_id=plant.config.sampling_profile_id,
        geometry=geometry,
        identity_map=identity,
        receivers=receivers,
        profile_manifest=profile_manifest,
        scene_manifest=scene_manifest,
        instance_translations=translations,
        surface_flux=surface_flux,
        ppfd_heatmap=ppfd_heatmap,
    )
    validate_profile_artifacts(
        profile_manifest.data,
        {
            geometry.filename: geometry.data,
            identity.filename: identity.data,
            receivers.filename: receivers.data,
        },
    )
    scene_files = {translations.filename: translations.data}
    if surface_flux is not None:
        scene_files.update(
            {artifact.filename: artifact.data for artifact in surface_flux.files}
        )
    if ppfd_heatmap is not None:
        scene_files.update(
            {artifact.filename: artifact.data for artifact in ppfd_heatmap.files}
        )
    validate_run_scene_artifacts(
        scene_manifest.data,
        scene_files,
        expected_run_id=run_id,
        expected_system_id=system_id,
        expected_requested_length_ft=requested_length_ft,
        expected_requested_width_ft=requested_width_ft,
        expected_natural_fit=natural_fit,
        expected_natural_fit_artifact_sha256=natural_fit_artifact_sha256,
        expected_fixture_catalog_sha256=fixture_catalog_sha256,
        expected_fixture_catalog_byte_length=fixture_catalog_byte_length,
        expected_fixture_authoritative_layout_sha256=(
            fixture_authoritative_layout_sha256
        ),
        expected_fixture_plan_sha256=fixture_plan_sha256,
        expected_fixture_count=fixture_count,
        expected_fixture_asset_group_count=fixture_asset_group_count,
        expected_mounting_height=mounting_height,
        expected_run_information=run_information,
        expected_surface_flux=surface_flux,
        expected_ppfd_heatmap=ppfd_heatmap,
        expected_sampling_profile_id=plant.config.sampling_profile_id,
    )
    return artifact_set


def validate_run_scene_artifacts(
    scene_bytes: bytes,
    files: Mapping[str, bytes],
    *,
    expected_run_id: str,
    expected_system_id: str,
    expected_requested_length_ft: float,
    expected_requested_width_ft: float,
    expected_natural_fit: NaturalFitLayoutPlan,
    expected_natural_fit_artifact_sha256: str,
    expected_fixture_catalog_sha256: str,
    expected_fixture_catalog_byte_length: int,
    expected_fixture_authoritative_layout_sha256: str,
    expected_fixture_plan_sha256: str,
    expected_fixture_count: int,
    expected_fixture_asset_group_count: int,
    expected_mounting_height: Mapping[str, object] | None = None,
    expected_run_information: Mapping[str, object] | None = None,
    expected_surface_flux: SurfaceFluxViewerArtifacts | None = None,
    expected_ppfd_heatmap: PpfdHeatmapViewerArtifacts | None = None,
    expected_sampling_profile_id: str = (
        REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
    ),
) -> dict[str, object]:
    """Validate one run scene and its sole compact instance buffer."""

    if expected_sampling_profile_id not in REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS:
        raise ValueError("expected viewer sampling profile is not declared.")

    _validate_run_context(
        run_id=expected_run_id,
        system_id=expected_system_id,
        requested_length_ft=expected_requested_length_ft,
        requested_width_ft=expected_requested_width_ft,
        natural_fit=expected_natural_fit,
        natural_fit_artifact_sha256=expected_natural_fit_artifact_sha256,
    )
    _validate_fixture_reference(
        expected_fixture_catalog_sha256,
        expected_fixture_catalog_byte_length,
        expected_fixture_authoritative_layout_sha256,
        expected_fixture_plan_sha256,
        expected_fixture_count,
        expected_fixture_asset_group_count,
    )
    if expected_mounting_height is not None:
        try:
            canonical_mounting = MountingGeometry.resolve(
                expected_mounting_height.get("mounting_height_in")
            ).to_payload()
        except (AttributeError, ValueError) as exc:
            raise ValueError(
                "expected run scene mounting-height provenance is incompatible."
            ) from exc
        if dict(expected_mounting_height) != canonical_mounting:
            raise ValueError(
                "expected run scene mounting-height provenance is incompatible."
            )
    payload = _json_object(scene_bytes, "run scene")
    if (
        payload.get("schema_id") != SCENE_SCHEMA_ID
        or payload.get("schema_version")
        not in {1, 2, MOUNTING_SCENE_SCHEMA_VERSION, SCENE_SCHEMA_VERSION}
        or payload.get("viewer_resource_version")
        not in {
            VIEWER_RESOURCE_VERSION,
            PPFD_HEATMAP_VIEWER_RESOURCE_VERSION,
            MOUNTING_VIEWER_RESOURCE_VERSION,
            D4_VIEWER_RESOURCE_VERSION,
            D3_VIEWER_RESOURCE_VERSION,
            LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION,
        }
    ):
        raise ValueError("run scene schema or resource version is incompatible.")
    expected_fields = {
        "aligned_simulation_room",
        "camera_bounds",
        "fixtures",
        "natural_fit",
        "plant_bounds",
        "plant_instances",
        "profile",
        "projected_footprint_bounds",
        "reference_plane",
        "requested_room",
        "room_bounds",
        "run",
        "schema_id",
        "schema_version",
        "viewer_resource_version",
    }
    if expected_surface_flux is not None:
        expected_fields.add("surface_flux")
    if expected_mounting_height is not None:
        expected_fields.add("mounting_height")
    if expected_ppfd_heatmap is not None:
        expected_fields.add("ppfd_heatmap")
    if expected_natural_fit.active_domain is not None:
        expected_fields.add("active_domain")
    if set(payload) != expected_fields:
        raise ValueError("run scene field inventory is incompatible.")
    expected_files = {"instances.f32le.bin"}
    if expected_surface_flux is not None:
        expected_files.update(
            artifact.filename for artifact in expected_surface_flux.files
        )
    if expected_ppfd_heatmap is not None:
        expected_files.update(
            artifact.filename for artifact in expected_ppfd_heatmap.files
        )
    if set(files) != expected_files:
        raise ValueError("run scene file inventory is incompatible.")
    expected_run = {
        "run_id": expected_run_id,
        "system_id": expected_system_id,
    }
    if expected_run_information is not None:
        expected_run["information"] = dict(expected_run_information)
    if payload.get("run") != expected_run:
        raise ValueError("run scene identity is incompatible.")
    expected_fixture_record = {
        "asset_group_count": expected_fixture_asset_group_count,
        "authoritative_layout_sha256": (
            expected_fixture_authoritative_layout_sha256
        ),
        "catalog": "fixtures/catalog.v1.json",
        "catalog_byte_length": expected_fixture_catalog_byte_length,
        "catalog_sha256": expected_fixture_catalog_sha256,
        "fixture_count": expected_fixture_count,
        "fixture_plan_sha256": expected_fixture_plan_sha256,
    }
    if expected_mounting_height is not None:
        expected_fixture_record["mounting_height_sha256"] = _hash_json(
            dict(expected_mounting_height)
        )
    if payload.get("fixtures") != expected_fixture_record:
        raise ValueError("run scene fixture catalog reference is incompatible.")
    if expected_mounting_height is not None:
        expected_schema_version = (
            SCENE_SCHEMA_VERSION
            if expected_ppfd_heatmap is not None
            else MOUNTING_SCENE_SCHEMA_VERSION
        )
        expected_resource_version = (
            _ppfd_viewer_resource_version(expected_ppfd_heatmap)
            if expected_ppfd_heatmap is not None
            else MOUNTING_VIEWER_RESOURCE_VERSION
        )
        if (
            payload.get("schema_version") != expected_schema_version
            or payload.get("viewer_resource_version") != expected_resource_version
            or payload.get("mounting_height") != dict(expected_mounting_height)
        ):
            raise ValueError("run scene mounting-height provenance is incompatible.")
    if expected_mounting_height is None and (
        payload.get("schema_version")
        in {MOUNTING_SCENE_SCHEMA_VERSION, SCENE_SCHEMA_VERSION}
        or payload.get("viewer_resource_version")
        in {
            MOUNTING_VIEWER_RESOURCE_VERSION,
            VIEWER_RESOURCE_VERSION,
            PPFD_HEATMAP_VIEWER_RESOURCE_VERSION,
        }
    ):
        raise ValueError("historical run scene mounting schema is incompatible.")
    if expected_ppfd_heatmap is not None:
        if payload.get("ppfd_heatmap") != dict(
            expected_ppfd_heatmap.scene_reference
        ):
            raise ValueError("run scene PPFD heatmap reference is incompatible.")
        for heatmap_artifact in expected_ppfd_heatmap.files:
            data = files.get(heatmap_artifact.filename)
            if (
                data is None
                or len(data) != heatmap_artifact.byte_size
                or hashlib.sha256(data).hexdigest() != heatmap_artifact.sha256
            ):
                raise ValueError(
                    f"PPFD heatmap artifact {heatmap_artifact.filename} "
                    "failed validation."
                )
        heatmap_metadata = _json_object(
            files[expected_ppfd_heatmap.metadata.filename],
            "PPFD heatmap metadata",
        )
        if heatmap_metadata.get("run_id") != expected_run_id:
            raise ValueError("PPFD heatmap run identity is incompatible.")
    elif (
        payload.get("schema_version") == SCENE_SCHEMA_VERSION
        or payload.get("viewer_resource_version")
        in {VIEWER_RESOURCE_VERSION, PPFD_HEATMAP_VIEWER_RESOURCE_VERSION}
    ):
        raise ValueError("current run scene PPFD heatmap source is missing.")
    if expected_surface_flux is not None:
        resource_version = payload.get("viewer_resource_version")
        optimized = (
            expected_sampling_profile_id
            == REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
        )
        if (
            resource_version == LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION
            and expected_surface_flux.schema_version != 1
        ) or (
            resource_version == D3_VIEWER_RESOURCE_VERSION
            and expected_surface_flux.schema_version != 2
        ) or (optimized and expected_surface_flux.schema_version != 3) or (
            not optimized and expected_surface_flux.schema_version == 3
        ):
            raise ValueError(
                "run scene and historical surface-flux resources are incompatible."
            )
        expected_surface_record = {
            "availability": "available",
            "metadata": {
                "byte_length": expected_surface_flux.metadata.byte_size,
                "filename": expected_surface_flux.metadata.filename,
                "sha256": expected_surface_flux.metadata.sha256,
            },
        }
        if payload.get("surface_flux") != expected_surface_record:
            raise ValueError("run scene surface-flux reference is incompatible.")
        for surface_artifact in expected_surface_flux.files:
            data = files.get(surface_artifact.filename)
            if (
                data is None
                or len(data) != surface_artifact.byte_size
                or hashlib.sha256(data).hexdigest() != surface_artifact.sha256
            ):
                raise ValueError(
                    f"surface-flux artifact {surface_artifact.filename} failed validation."
                )
    if payload.get("requested_room") != {
        "length_ft": expected_requested_length_ft,
        "length_m": expected_natural_fit.requested_room_m.length_m,
        "width_ft": expected_requested_width_ft,
        "width_m": expected_natural_fit.requested_room_m.width_m,
    }:
        raise ValueError("run scene requested room is incompatible.")
    if payload.get("aligned_simulation_room") != {
        "length_m": expected_natural_fit.aligned_room_m.length_m,
        "width_m": expected_natural_fit.aligned_room_m.width_m,
        "long_axis": "x",
        "coordinate_frame": expected_natural_fit.coordinate_frame.to_payload(),
    }:
        raise ValueError("run scene aligned simulation room is incompatible.")
    expected_active_domain = (
        None
        if expected_natural_fit.active_domain is None
        else expected_natural_fit.active_domain.to_payload()
    )
    if payload.get("active_domain") != expected_active_domain:
        raise ValueError("run scene active domain is incompatible.")
    profile = payload.get("profile")
    if (
        not isinstance(profile, dict)
        or profile.get("profile_id") != PROFILE_ID
        or profile.get("sampling_profile_id") != expected_sampling_profile_id
        or profile.get("manifest") != f"profiles/{PROFILE_ID}/profile.v1.json"
        or not _valid_sha256(profile.get("manifest_sha256"))
    ):
        raise ValueError("run scene profile reference is incompatible.")
    natural_fit_record = payload.get("natural_fit")
    if natural_fit_record != {
        "artifact_role": "natural_fit_layout",
        "artifact_sha256": expected_natural_fit_artifact_sha256,
        "ordering": "Y-major/X-minor",
        "plan_hash": expected_natural_fit.plan_hash,
        "plant_count": expected_natural_fit.total_count,
        "policy_id": expected_natural_fit.policy.policy_id,
        "profile_id": expected_natural_fit.profile_id,
    }:
        raise ValueError("run scene Natural-fit reference is incompatible.")
    instances = payload.get("plant_instances")
    if not isinstance(instances, dict):
        raise ValueError("run scene plant instances are missing.")
    plant_ids = instances.get("plant_ids")
    expected_count = expected_natural_fit.total_count
    if (
        not isinstance(plant_ids, list)
        or len(plant_ids) != expected_count
        or any(not isinstance(value, str) or not value for value in plant_ids)
        or len(set(plant_ids)) != expected_count
        or plant_ids != [item.plant_id for item in expected_natural_fit.plants]
    ):
        raise ValueError("run scene plant ordering does not match Natural-fit.")
    artifact = instances.get("instance_translations")
    if not isinstance(artifact, dict):
        raise ValueError("run scene instance artifact record is missing.")
    expected_filename = "instances.f32le.bin"
    expected_length = expected_count * INSTANCE_TRANSLATION_STRIDE_BYTES
    expected_record = {
        "byte_length": expected_length,
        "byte_order": "little-endian",
        "component_type": "float32",
        "coordinate_space": "viewer right-handed meters, Y-up",
        "count": expected_count,
        "filename": expected_filename,
        "record_layout": "translation_x_y_z",
        "scientific_to_viewer": "(x, y, z) -> (x, z, -y)",
        "stride_bytes": INSTANCE_TRANSLATION_STRIDE_BYTES,
    }
    if any(artifact.get(name) != value for name, value in expected_record.items()):
        raise ValueError("run scene instance artifact contract is incompatible.")
    if not _valid_sha256(artifact.get("sha256")):
        raise ValueError("run scene instance artifact SHA-256 is malformed.")
    data = files.get(expected_filename)
    if data is None or len(data) != expected_length:
        raise ValueError("run scene instance artifact is missing or truncated.")
    if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
        raise ValueError("run scene instance artifact failed SHA-256 validation.")
    expected_values = tuple(
        component
        for placement in expected_natural_fit.plants
        for component in scientific_to_display(
            (placement.aligned_x_m, placement.aligned_y_m, 0.0)
        )
    )
    if data != struct.pack(f"<{len(expected_values)}f", *expected_values):
        raise ValueError("run scene translations do not match Natural-fit.")
    if any(
        not math.isfinite(value)
        for value in struct.unpack(f"<{expected_count * 3}f", data)
    ):
        raise ValueError("run scene instance translations must be finite.")
    for name, axes in (
        ("camera_bounds", "xyz"),
        ("plant_bounds", "xyz"),
        ("projected_footprint_bounds", "xz"),
        ("room_bounds", "xz"),
    ):
        _validate_bounds_payload(payload.get(name), name, axes)
    reference = payload.get("reference_plane")
    if (
        not isinstance(reference, dict)
        or reference.get("coordinate_system") != "viewer Y-up"
        or not _finite_vectors(reference.get("vertices_xyz"), 4, 3)
    ):
        raise ValueError("run scene reference plane is incompatible.")
    expected_bounds = _display_bounds(
        expected_natural_fit,
        generate_rex_juvenile_preheading_plant(
            RexJuvenilePreheadingConfig(
                sampling_profile_id=expected_sampling_profile_id
            )
        ),
    )
    for name, expected in expected_bounds.items():
        if payload.get(name) != expected:
            raise ValueError(f"run scene {name} does not match the authorized run.")
    return payload


def validate_profile_artifacts(
    manifest_bytes: bytes,
    files: Mapping[str, bytes],
) -> dict[str, object]:
    """Reject incomplete, altered, substituted, or schema-incompatible artifacts."""

    try:
        payload = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("profile manifest is not valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("profile manifest must be a JSON object.")
    if (
        payload.get("schema_id") != PROFILE_SCHEMA_ID
        or payload.get("schema_version") != PROFILE_SCHEMA_VERSION
        or payload.get("profile_id") != PROFILE_ID
        or payload.get("sampling_profile_id")
        not in REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS
    ):
        raise ValueError("profile manifest schema or profile is incompatible.")
    expected_sampling_calibration_status = (
        "uncalibrated_for_phase27g_d2_surface_flux_display"
        if payload["sampling_profile_id"]
        == REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
        else "legacy_phase27g_d2_surface_flux_calibration_compatible"
    )
    if (
        payload.get("sampling_calibration_status")
        != expected_sampling_calibration_status
    ):
        raise ValueError("profile sampling calibration status is incompatible.")
    counts = payload.get("counts")
    if counts != {
        "faces": 1920,
        "leaves": 12,
        "patches": 192,
        "receivers": 384,
    }:
        raise ValueError("profile manifest counts are incompatible.")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "geometry",
        "identity_map",
        "receivers",
    }:
        raise ValueError("profile manifest artifact table is incompatible.")
    expected_filenames = {
        "geometry": "geometry.glb",
        "identity_map": "identity-map.v1.json",
        "receivers": "receivers.f32le.bin",
    }
    for role, filename in expected_filenames.items():
        record = artifacts.get(role)
        if not isinstance(record, dict) or record.get("filename") != filename:
            raise ValueError(f"profile artifact {role} has an invalid filename.")
        data = files.get(filename)
        if data is None:
            raise ValueError(f"profile artifact {filename} is missing.")
        if record.get("byte_size") != len(data):
            raise ValueError(f"profile artifact {filename} has an invalid byte size.")
        if record.get("sha256") != hashlib.sha256(data).hexdigest():
            raise ValueError(f"profile artifact {filename} failed SHA-256 validation.")
    if len(files["receivers.f32le.bin"]) != 384 * 6 * 4:
        raise ValueError("receiver artifact byte length is incompatible.")
    identity = _json_object(files["identity-map.v1.json"], "identity map")
    _validate_identity_payload(identity)
    if identity.get("sampling_profile_id") != payload.get("sampling_profile_id"):
        raise ValueError("profile and identity-map sampling profiles disagree.")
    return payload


def _validate_juvenile_contract(plant: PlantMesh) -> None:
    if (
        plant.config.profile_id != PROFILE_ID
        or plant.config.sampling_profile_id
        not in REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS
    ):
        raise ValueError("viewer publication accepts only the juvenile profile.")
    if (
        len(plant.leaves),
        plant.face_count,
        plant.patch_count,
        plant.receiver_count,
    ) != (12, 1920, 192, 384):
        raise ValueError("juvenile viewer geometry contract is invalid.")


def _identity_payload(
    plant: PlantMesh,
) -> tuple[dict[str, object], tuple[int, ...]]:
    leaf_index_by_id = {
        leaf.leaf_id: index for index, leaf in enumerate(plant.leaves)
    }
    patch_index_by_id = {
        patch.patch_id: index for index, patch in enumerate(plant.patches)
    }
    face_to_patch_by_id: dict[str, int] = {}
    for patch_index, patch in enumerate(plant.patches):
        for face_id in patch.triangle_face_ids:
            if face_id in face_to_patch_by_id:
                raise ValueError(f"face {face_id} belongs to more than one patch.")
            face_to_patch_by_id[face_id] = patch_index
    face_to_patch = tuple(
        face_to_patch_by_id[face.face_id] for face in plant.faces
    )
    receivers = build_two_sided_patch_receivers(plant)
    payload: dict[str, object] = {
        "schema_id": IDENTITY_SCHEMA_ID,
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "profile_id": PROFILE_ID,
        "sampling_profile_id": plant.config.sampling_profile_id,
        "plant_ids": [plant.plant_id],
        "leaf_ids": [leaf.leaf_id for leaf in plant.leaves],
        "face_ids": [face.face_id for face in plant.faces],
        "patch_ids": [patch.patch_id for patch in plant.patches],
        "receiver_ids": [receiver.receiver_id for receiver in receivers],
        "face_to_leaf": [face.leaf_rank - 1 for face in plant.faces],
        "face_to_patch": list(face_to_patch),
        "patch_to_leaf": [
            leaf_index_by_id[patch.leaf_id] for patch in plant.patches
        ],
        "receiver_to_patch": [
            patch_index_by_id[receiver.patch_id] for receiver in receivers
        ],
        "receiver_side": [receiver.side for receiver in receivers],
    }
    _validate_identity_payload(payload)
    return payload, face_to_patch


def _validate_identity_payload(payload: Mapping[str, object]) -> None:
    if (
        payload.get("schema_id") != IDENTITY_SCHEMA_ID
        or payload.get("schema_version") != IDENTITY_SCHEMA_VERSION
        or payload.get("profile_id") != PROFILE_ID
        or payload.get("sampling_profile_id")
        not in REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS
    ):
        raise ValueError("identity-map schema or profile is incompatible.")
    expected_lengths = {
        "plant_ids": 1,
        "leaf_ids": 12,
        "face_ids": 1920,
        "patch_ids": 192,
        "receiver_ids": 384,
        "face_to_leaf": 1920,
        "face_to_patch": 1920,
        "patch_to_leaf": 192,
        "receiver_to_patch": 384,
        "receiver_side": 384,
    }
    for name, expected in expected_lengths.items():
        value = payload.get(name)
        if not isinstance(value, list) or len(value) != expected:
            raise ValueError(f"identity-map {name} length is incompatible.")
    if payload["receiver_side"] != ["front", "back"] * 192:
        raise ValueError("receiver sides must preserve patch-major front/back order.")
    if any(
        not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 12
        for index in payload["face_to_leaf"]
    ):
        raise ValueError("face-to-leaf mapping is invalid.")
    if any(
        not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 192
        for index in payload["face_to_patch"]
    ):
        raise ValueError("face-to-patch mapping is invalid.")
    if any(
        not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 12
        for index in payload["patch_to_leaf"]
    ):
        raise ValueError("patch-to-leaf mapping is invalid.")
    if payload["receiver_to_patch"] != [index // 2 for index in range(384)]:
        raise ValueError("receiver-to-patch mapping is invalid.")
    for name in ("plant_ids", "leaf_ids", "face_ids", "patch_ids", "receiver_ids"):
        values = payload[name]
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError(f"identity-map {name} contains an invalid ID.")
        if len(values) != len(set(values)):
            raise ValueError(f"identity-map {name} IDs must be unique.")


def _receiver_bytes(plant: PlantMesh) -> tuple[bytes, float, float]:
    values: list[float] = []
    max_position_error = 0.0
    max_normal_error = 0.0
    for receiver in build_two_sided_patch_receivers(plant):
        position = scientific_to_display(receiver.point_m)
        normal = scientific_to_display(receiver.normal)
        values.extend((*position, *normal))
        max_position_error = max(max_position_error, _float32_error(position))
        max_normal_error = max(max_normal_error, _float32_error(normal))
    return (
        struct.pack(f"<{len(values)}f", *values),
        max_position_error,
        max_normal_error,
    )


def _profile_payload(
    plant: PlantMesh,
    artifacts: tuple[BinaryDisplayArtifact, ...],
    *,
    max_position_error: float,
    max_normal_error: float,
) -> dict[str, object]:
    by_name = {artifact.filename: artifact for artifact in artifacts}
    return {
        "schema_id": PROFILE_SCHEMA_ID,
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": PROFILE_ID,
        "sampling_profile_id": plant.config.sampling_profile_id,
        "development": {
            "approximate_days_after_transplant": (
                plant.config.approximate_days_after_transplant
            ),
            "cultivar": plant.config.cultivar,
            "description": "juvenile, post-transplant, pre-heading",
            "phenological_boundary": "BBCH 19/pre-41",
        },
        "calibration_status": plant.config.calibration_status,
        "sampling_calibration_status": plant.config.sampling_calibration_status,
        "model_scope": {
            "growth_response_model": False,
            "measured_reconstruction": False,
            "procedural_reference_geometry": True,
        },
        "units": "meters",
        "scientific_coordinate_system": "right-handed, meters, z-up",
        "viewer_coordinate_system": "right-handed, meters, Y-up",
        "coordinate_transform": {
            "formula": "(x, y, z) -> (x, z, -y)",
            "matrix_row_major": [1, 0, 0, 0, 0, 1, 0, -1, 0],
            "proper_rotation": True,
            "winding_reversed": False,
        },
        "counts": {
            "faces": plant.face_count,
            "leaves": len(plant.leaves),
            "patches": plant.patch_count,
            "receivers": plant.receiver_count,
        },
        "ordering": {
            "faces": "leaf rank, then face index within leaf",
            "leaves": "oldest outer through youngest inner rank",
            "patches": "leaf rank, then patch u, then patch v",
            "receivers": "patch-major, front then back",
        },
        "artifacts": {
            "geometry": _artifact_record(by_name["geometry.glb"]),
            "identity_map": _artifact_record(
                by_name["identity-map.v1.json"]
            ),
            "receivers": _artifact_record(by_name["receivers.f32le.bin"]),
        },
        "geometry_glb": {
            "attributes": {
                "NORMAL": (
                    "float32 leaf-local area-weighted smooth display normal; "
                    "viewer-only derivative"
                ),
                "POSITION": "float32 display position in meters",
                "TEXCOORD_0": (
                    "float32 exact structured leaf parameters (u, (v + 1) / 2)"
                ),
                "_FACE_INDEX": "uint32 compact global face index",
                "_LEAF_INDEX": "uint32 compact leaf index",
                "_PATCH_INDEX": "uint32 compact global patch index",
            },
            "indexed": False,
            "triangle_mode": 4,
        },
        "receiver_binary": {
            "component_type": "float32 little-endian",
            "record_layout": "display_position_xyz, display_normal_xyz",
            "values_per_receiver": 6,
        },
        "display_derivative": (
            "GLB and receiver float32 values are display derivatives; Python "
            "scientific PlantMesh geometry remains authoritative."
        ),
        "maximum_float32_conversion_error": {
            "normal_absolute": max_normal_error,
            "position_m_absolute": max_position_error,
        },
    }


def _display_bounds(
    plan: NaturalFitLayoutPlan,
    plant: PlantMesh,
) -> dict[str, object]:
    display_vertices = [
        scientific_to_display(vertex)
        for leaf in plant.leaves
        for vertex in leaf.vertices
    ]
    local_minimum = [
        min(vertex[axis] for vertex in display_vertices) for axis in range(3)
    ]
    local_maximum = [
        max(vertex[axis] for vertex in display_vertices) for axis in range(3)
    ]
    display_translations = tuple(
        scientific_to_display(
            (placement.aligned_x_m, placement.aligned_y_m, 0.0)
        )
        for placement in plan.plants
    )
    minimum = [
        local_minimum[axis]
        + min(translation[axis] for translation in display_translations)
        for axis in range(3)
    ]
    maximum = [
        local_maximum[axis]
        + max(translation[axis] for translation in display_translations)
        for axis in range(3)
    ]
    footprint_min = [minimum[0], minimum[2]]
    footprint_max = [maximum[0], maximum[2]]
    half_length = plan.aligned_room_m.length_m / 2.0
    half_width = plan.aligned_room_m.width_m / 2.0
    room_minimum = [-half_length, -half_width]
    room_maximum = [half_length, half_width]
    reference_vertices = [
        [room_minimum[0], 0.0, room_minimum[1]],
        [room_maximum[0], 0.0, room_minimum[1]],
        [room_maximum[0], 0.0, room_maximum[1]],
        [room_minimum[0], 0.0, room_maximum[1]],
    ]
    return {
        "camera_bounds": {
            "minimum_xyz": [room_minimum[0], 0.0, room_minimum[1]],
            "maximum_xyz": [room_maximum[0], maximum[1], room_maximum[1]],
        },
        "plant_bounds": {"maximum_xyz": maximum, "minimum_xyz": minimum},
        "projected_footprint_bounds": {
            "maximum_xz": footprint_max,
            "minimum_xz": footprint_min,
        },
        "room_bounds": {
            "maximum_xz": room_maximum,
            "minimum_xz": room_minimum,
        },
        "reference_plane": {
            "coordinate_system": "viewer Y-up",
            "vertices_xyz": reference_vertices,
        },
    }


def _run_scene_payload(
    *,
    run_id: str,
    system_id: str,
    requested_length_ft: float,
    requested_width_ft: float,
    natural_fit: NaturalFitLayoutPlan,
    natural_fit_artifact_sha256: str,
    profile_manifest: BinaryDisplayArtifact,
    plant: PlantMesh,
    fixture_catalog_sha256: str,
    fixture_catalog_byte_length: int,
    fixture_authoritative_layout_sha256: str,
    fixture_plan_sha256: str,
    fixture_count: int,
    fixture_asset_group_count: int,
    mounting_height: Mapping[str, object] | None,
    run_information: Mapping[str, object] | None,
    surface_flux: SurfaceFluxViewerArtifacts | None,
    ppfd_heatmap: PpfdHeatmapViewerArtifacts | None,
) -> tuple[dict[str, object], BinaryDisplayArtifact]:
    translation_values = tuple(
        component
        for placement in natural_fit.plants
        for component in scientific_to_display(
            (placement.aligned_x_m, placement.aligned_y_m, 0.0)
        )
    )
    translations = _artifact(
        "instances.f32le.bin",
        struct.pack(f"<{len(translation_values)}f", *translation_values),
    )
    if ppfd_heatmap is not None and mounting_height is None:
        raise ValueError("current PPFD heatmap scenes require mounting provenance.")
    schema_version = (
        SCENE_SCHEMA_VERSION
        if ppfd_heatmap is not None
        else MOUNTING_SCENE_SCHEMA_VERSION
        if mounting_height is not None
        else 2
    )
    resource_version = (
        _ppfd_viewer_resource_version(ppfd_heatmap)
        if ppfd_heatmap is not None
        else MOUNTING_VIEWER_RESOURCE_VERSION
        if mounting_height is not None
        else D4_VIEWER_RESOURCE_VERSION
    )
    payload = {
        "schema_id": SCENE_SCHEMA_ID,
        "schema_version": schema_version,
        "viewer_resource_version": resource_version,
        "run": {
            "run_id": run_id,
            "system_id": system_id,
            **(
                {}
                if run_information is None
                else {"information": dict(run_information)}
            ),
        },
        "fixtures": {
            "asset_group_count": fixture_asset_group_count,
            "authoritative_layout_sha256": fixture_authoritative_layout_sha256,
            "catalog": "fixtures/catalog.v1.json",
            "catalog_byte_length": fixture_catalog_byte_length,
            "catalog_sha256": fixture_catalog_sha256,
            "fixture_count": fixture_count,
            "fixture_plan_sha256": fixture_plan_sha256,
        },
        "requested_room": {
            "length_ft": requested_length_ft,
            "length_m": natural_fit.requested_room_m.length_m,
            "width_ft": requested_width_ft,
            "width_m": natural_fit.requested_room_m.width_m,
        },
        "aligned_simulation_room": {
            "length_m": natural_fit.aligned_room_m.length_m,
            "width_m": natural_fit.aligned_room_m.width_m,
            "long_axis": "x",
            "coordinate_frame": natural_fit.coordinate_frame.to_payload(),
        },
        **(
            {}
            if natural_fit.active_domain is None
            else {"active_domain": natural_fit.active_domain.to_payload()}
        ),
        "profile": {
            "manifest": f"profiles/{PROFILE_ID}/profile.v1.json",
            "manifest_sha256": profile_manifest.sha256,
            "profile_id": PROFILE_ID,
            "sampling_profile_id": plant.config.sampling_profile_id,
        },
        "natural_fit": {
            "artifact_role": "natural_fit_layout",
            "artifact_sha256": natural_fit_artifact_sha256,
            "ordering": "Y-major/X-minor",
            "plan_hash": natural_fit.plan_hash,
            "plant_count": natural_fit.total_count,
            "policy_id": natural_fit.policy.policy_id,
            "profile_id": natural_fit.profile_id,
        },
        "plant_instances": {
            "plant_ids": [placement.plant_id for placement in natural_fit.plants],
            "instance_translations": {
                "filename": translations.filename,
                "record_layout": "translation_x_y_z",
                "coordinate_space": "viewer right-handed meters, Y-up",
                "scientific_to_viewer": "(x, y, z) -> (x, z, -y)",
                "component_type": "float32",
                "byte_order": "little-endian",
                "stride_bytes": INSTANCE_TRANSLATION_STRIDE_BYTES,
                "count": natural_fit.total_count,
                "byte_length": translations.byte_size,
                "sha256": translations.sha256,
            },
        },
        **_display_bounds(natural_fit, plant),
    }
    if mounting_height is not None:
        payload["mounting_height"] = dict(mounting_height)
        payload["fixtures"]["mounting_height_sha256"] = _hash_json(
            dict(mounting_height)
        )
    if surface_flux is not None:
        payload["surface_flux"] = {
            "availability": "available",
            "metadata": {
                "filename": surface_flux.metadata.filename,
                "byte_length": surface_flux.metadata.byte_size,
                "sha256": surface_flux.metadata.sha256,
            },
        }
    if ppfd_heatmap is not None:
        payload["ppfd_heatmap"] = dict(ppfd_heatmap.scene_reference)
    return payload, translations


def _ppfd_viewer_resource_version(
    ppfd_heatmap: PpfdHeatmapViewerArtifacts,
) -> str:
    return (
        VIEWER_RESOURCE_VERSION
        if "target_coverage" in ppfd_heatmap.scene_reference
        else PPFD_HEATMAP_VIEWER_RESOURCE_VERSION
    )


def _validate_run_context(
    *,
    run_id: str,
    system_id: str,
    requested_length_ft: float,
    requested_width_ft: float,
    natural_fit: NaturalFitLayoutPlan,
    natural_fit_artifact_sha256: str,
) -> None:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("run scene requires a canonical 32-character run ID.")
    if system_id not in SUPPORTED_SYSTEM_IDS:
        raise ValueError("run scene system identity is unsupported.")
    dimensions = (requested_length_ft, requested_width_ft)
    if any(
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or not 0.0 < float(value)
        for value in dimensions
    ):
        raise ValueError("run scene room dimensions must be finite and positive.")
    if (
        natural_fit.profile_id != PROFILE_ID
        or natural_fit.requested_room_m.length_m
        != float(requested_length_ft) * 0.3048
        or natural_fit.requested_room_m.width_m
        != float(requested_width_ft) * 0.3048
    ):
        raise ValueError("run scene request and Natural-fit room disagree.")
    if not _valid_sha256(natural_fit_artifact_sha256):
        raise ValueError("Natural-fit artifact SHA-256 is malformed.")


def _validate_fixture_reference(
    catalog_sha256: str,
    catalog_byte_length: int,
    authoritative_layout_sha256: str,
    plan_sha256: str,
    fixture_count: int,
    group_count: int,
) -> None:
    if (
        not _valid_sha256(catalog_sha256)
        or not _valid_sha256(authoritative_layout_sha256)
        or not _valid_sha256(plan_sha256)
    ):
        raise ValueError("fixture catalog identity SHA-256 is malformed.")
    if (
        isinstance(catalog_byte_length, bool)
        or not isinstance(catalog_byte_length, int)
        or catalog_byte_length <= 0
    ):
        raise ValueError("fixture catalog byte length is incompatible.")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in (fixture_count, group_count)
    ) or group_count > fixture_count:
        raise ValueError("fixture catalog counts are incompatible.")


def _artifact(filename: str, data: bytes) -> BinaryDisplayArtifact:
    return BinaryDisplayArtifact(
        filename=filename,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _artifact_record(artifact: BinaryDisplayArtifact) -> dict[str, object]:
    return {
        "byte_size": artifact.byte_size,
        "filename": artifact.filename,
        "sha256": artifact.sha256,
    }


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def _hash_json(payload: object) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_object(data: bytes, label: str) -> dict[str, object]:
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return payload


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _finite_vectors(value: object, count: int, components: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == count
        and all(
            isinstance(vector, list)
            and len(vector) == components
            and all(
                isinstance(component, int | float)
                and not isinstance(component, bool)
                and math.isfinite(float(component))
                for component in vector
            )
            for vector in value
        )
    )


def _validate_bounds_payload(value: object, label: str, axes: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"run scene {label} is missing.")
    minimum = value.get(f"minimum_{axes}")
    maximum = value.get(f"maximum_{axes}")
    if (
        not _finite_vectors([minimum, maximum], 2, len(axes))
        or any(left > right for left, right in zip(minimum, maximum, strict=True))
    ):
        raise ValueError(f"run scene {label} is incompatible.")


def _float32_error(values: tuple[float, float, float]) -> float:
    rounded = struct.unpack("<3f", struct.pack("<3f", *values))
    error = max(abs(source - target) for source, target in zip(values, rounded))
    if not math.isfinite(error):
        raise ValueError("float32 conversion error must be finite.")
    return error
