"""Vendor-neutral presentation views over authenticated committed data."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

from fspm_optics.application.domain import (
    CONVENTIONAL_SYSTEM_ID,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    SYSTEM_DISPLAY_NAMES,
)
from fspm_optics.precomputed.committed_playback import CommittedPlaybackCase


PUBLIC_HPS_ASSET_ID = "hps-housing-v3"
PUBLIC_HPS_FIXTURE_TYPE = "hps_1000w_fixture"
PUBLIC_HPS_RESOURCE_PATH = "fixtures/hps/hps.glb"
PUBLIC_HPS_GLB_BYTE_SIZE = 353160
PUBLIC_HPS_GLB_SHA256 = (
    "083df1475e84e9552d5ec548fc34669092b5ba4284fcf33ebf7dbb2f26439d7d"
)
PUBLIC_HPS_PLACEMENT_CONTRACT_SHA256 = (
    "d83577f6bb7503ff8865dbe55122bfd37d5ea0e0f2c1d9dc130a7a98f9598293"
)
PUBLIC_HPS_IES_RESOURCE = "hps_1000w.ies"
PUBLIC_HPS_SPD_RESOURCE = "hps_1000w_spd.csv"
PUBLIC_CONVENTIONAL_ASSET_ID = "conventional-led-8-bar-v1"
PUBLIC_CONVENTIONAL_FIXTURE_TYPE = "conventional_led_8_bar"
PUBLIC_CONVENTIONAL_RESOURCE_PATH = (
    "fixtures/conventional/conventional_led_8_bar.glb"
)
PUBLIC_CONVENTIONAL_GLB_BYTE_SIZE = 109392
PUBLIC_CONVENTIONAL_GLB_SHA256 = (
    "0d640d8e20bfdc213d3722dc44652979366b7c19ef153fff10372fe66051af53"
)
PUBLIC_CONVENTIONAL_SPD_RESOURCE = "conventional_led_spd.csv"
PUBLIC_CONVENTIONAL_SPECTRAL_BASIS_ID = "conventional_led_control"
PUBLIC_CONVENTIONAL_SPECTRAL_BASIS_LABEL = (
    "Conventional LED spectrum (controlled A/B)"
)
PUBLIC_CONVENTIONAL_SPECTRAL_DISTRIBUTION_ID = (
    "conventional_led_spectral_v2_51f274bf97d7d5707519"
)
PUBLIC_PROPOSED_CONTROL_SOURCE_MODEL_ID = (
    "proposed_conventional_led_spd_completed_aperture_control_v1"
)


@dataclass(frozen=True, slots=True)
class ViewerPaths:
    asset: str
    transforms: str


HpsViewerPaths = ViewerPaths


def project_committed_result(
    source: Mapping[str, object],
    system_id: str,
) -> dict[str, object]:
    """Project only result fields with a historical presentation vocabulary."""

    payload = deepcopy(dict(source))
    payload["system_id"] = system_id
    if system_id == PROPOSED_SYSTEM_ID and "spectral_basis" in payload:
        payload["spectral_basis"] = _project_proposed_spectral_basis(
            _object(payload["spectral_basis"], "result spectral basis")
        )
    return payload


def project_committed_metrics(
    source: Mapping[str, object],
    system_id: str,
) -> dict[str, object]:
    """Project one validated metrics schema without changing numeric fields."""

    if system_id == HPS_SYSTEM_ID:
        return project_hps_metrics(source)
    payload = deepcopy(dict(source))
    if system_id == PROPOSED_SYSTEM_ID and "spectral_basis" in payload:
        payload["spectral_basis"] = _project_proposed_spectral_basis(
            _object(payload["spectral_basis"], "metrics spectral basis")
        )
    return payload


def project_committed_run_configuration(
    source: Mapping[str, object],
    case: CommittedPlaybackCase,
) -> dict[str, object]:
    """Build the closed public run-configuration schema for any committed case."""

    if case.system_id == HPS_SYSTEM_ID:
        return project_hps_run_configuration(source, case)
    safe_fields = (
        "aisle",
        "canonical_domain",
        "dimension_units",
        "far_red",
        "layout_mode",
        "mounting_height",
        "ordered_room_dimensions",
        "radiance_quality",
    )
    payload = {
        key: deepcopy(source[key]) for key in safe_fields if key in source
    }
    request = case.request.to_dict()
    request_fields = (
        "system",
        "room_length_ft",
        "room_width_ft",
        "quality",
        "analysis_scope",
        "mounting_height_in",
        "aisle_mode",
        "active_domain",
        "include_far_red",
        "target_ppfd_umol_m2_s",
        "lighting_target_mode",
        "layout_mode",
        "spectral_basis",
        "proposed_control_mode",
        "proposed_layout_mode",
        "proposed_ring_mode",
        "proposed_source_mode",
    )
    payload["request"] = {
        key: deepcopy(request[key]) for key in request_fields if key in request
    }
    raw_fixed = _object(source.get("fixed_plan"), "fixed plan")
    payload["fixed_plan"] = {
        "schema_id": raw_fixed.get("schema_id"),
        "schema_version": raw_fixed.get("schema_version"),
        "plan_identity_sha256": raw_fixed.get("plan_identity_sha256"),
        "public_case_id": case.case_id,
        "artifact_case_id_sha256": case.artifact_case_id_sha256,
        "target_semantics": raw_fixed.get("target_semantics"),
    }
    raw_spectral = source.get("spectral")
    if raw_spectral is not None:
        spectral = _object(raw_spectral, "run spectral configuration")
        payload["spectral"] = {
            key: deepcopy(spectral[key])
            for key in ("analysis_scope", "include_far_red")
            if key in spectral
        }
        if case.system_id == PROPOSED_SYSTEM_ID:
            payload["spectral"]["spectral_basis"] = (
                PUBLIC_CONVENTIONAL_SPECTRAL_BASIS_ID
            )
    payload["solver"] = {
        "backend": "authenticated_compact_playback",
        "source_runtime_required": False,
        "canonical_solver_identity_sha256": _json_identity(
            _object(source.get("solver"), "solver")
        ),
    }
    payload["system_id"] = case.system_id
    return payload


def project_committed_authenticated_identities(
    source: Mapping[str, object],
    case: CommittedPlaybackCase,
    bundle_identity_sha256: str,
) -> dict[str, object]:
    """Expose authentication evidence without raw historical source metadata."""

    return project_hps_authenticated_identities(
        source, case, bundle_identity_sha256
    )


def project_committed_json_artifact(
    name: str,
    data: bytes,
    system_id: str,
) -> bytes:
    """Project one explicitly supported JSON artifact schema."""

    if system_id == HPS_SYSTEM_ID:
        return project_hps_json_artifact(name, data)
    payload = _json_object(data, name)
    if name == "target-control.json":
        if system_id == CONVENTIONAL_SYSTEM_ID:
            payload["scaling_policy_id"] = (
                "conventional_led_full_output_float64_global_scaling_v1"
            )
    elif name == "full-output-schedule.json":
        pass
    elif name == "operating-point.json":
        if system_id == CONVENTIONAL_SYSTEM_ID:
            payload["post_trace_scaling_policy_id"] = (
                "conventional_led_full_output_float64_global_scaling_v1"
            )
    elif name == "physical-source-state.json":
        payload["system_id"] = system_id
        if system_id == CONVENTIONAL_SYSTEM_ID:
            operation = _object(payload.get("source_operation"), "source operation")
            operation["layout_id"] = _public_identity(
                "conventional-led-layout-v1", operation.get("layout_id")
            )
            operation["policy_id"] = _public_identity(
                "conventional-led-source-operation-v1", operation.get("policy_id")
            )
            operation["post_trace_scaling_policy_id"] = (
                "conventional_led_full_output_float64_global_scaling_v1"
            )
            payload["source_operation"] = operation
    elif name == "baseline-leaf-position-uniformity.v1.json":
        payload["system_id"] = system_id
        target_policy = payload.get("target_policy")
        if target_policy is not None:
            projected_target = _object(target_policy, "target policy")
            projected_target["system_id"] = system_id
            payload["target_policy"] = projected_target
    elif name not in {"natural-fit-layout.json"}:
        raise ValueError("unsupported committed presentation artifact.")
    return _json_bytes(payload)


def project_committed_visualization(
    source: Mapping[str, object],
    system_id: str,
) -> dict[str, object]:
    """Project the system-specific fixture overlay identifiers."""

    if system_id == HPS_SYSTEM_ID:
        return project_hps_visualization(source)
    payload = deepcopy(dict(source))
    if system_id != CONVENTIONAL_SYSTEM_ID:
        return payload
    overlay = _object(payload.get("overlay"), "visualization overlay")
    fixtures = _object_list(overlay.get("fixtures"), "overlay fixtures")
    for fixture in fixtures:
        fixture["fixture_type"] = PUBLIC_CONVENTIONAL_FIXTURE_TYPE
    overlay["fixtures"] = fixtures
    overlay["fixture_policy_id"] = (
        "conventional_led_8_bar_equal_width_equal_gap_overlay_v1"
    )
    overlay["policy_id"] = (
        "conventional_led_8_bar_equal_width_equal_gap_overlay_v1"
    )
    payload["overlay"] = overlay
    return payload


def project_committed_fixture_catalog(data: bytes, system_id: str) -> bytes:
    """Project public fixture identifiers while authenticating retained bytes."""

    if system_id == HPS_SYSTEM_ID:
        return project_hps_fixture_catalog(data)
    if system_id != CONVENTIONAL_SYSTEM_ID:
        return data
    payload = _json_object(data, "fixture catalog")
    groups = _object_list(payload.get("asset_groups"), "fixture asset groups")
    if len(groups) != 1:
        raise ValueError("Conventional fixture catalog must contain one asset group.")
    group = groups[0]
    asset = _object(group.get("asset"), "fixture asset")
    if (
        asset.get("byte_size") != PUBLIC_CONVENTIONAL_GLB_BYTE_SIZE
        or asset.get("sha256") != PUBLIC_CONVENTIONAL_GLB_SHA256
    ):
        raise ValueError("Conventional fixture binary identity is not authorized.")
    asset["filename"] = (
        f"assets/{PUBLIC_CONVENTIONAL_ASSET_ID}-"
        f"{PUBLIC_CONVENTIONAL_GLB_SHA256[:16]}.glb"
    )
    group["asset"] = asset
    group["display_asset_id"] = PUBLIC_CONVENTIONAL_ASSET_ID
    group["display_fixture_type"] = PUBLIC_CONVENTIONAL_FIXTURE_TYPE

    plan = _object(payload.get("fixture_plan"), "fixture plan")
    fixtures = _object_list(plan.get("fixtures"), "fixture plan fixtures")
    fixture_ids: list[str] = []
    for fixture in fixtures:
        fixture_id = fixture.get("fixture_id")
        if not isinstance(fixture_id, str) or not fixture_id:
            raise ValueError("Conventional fixture identity is malformed.")
        fixture_ids.append(fixture_id)
        fixture["assembly_id"] = fixture_id
        fixture["display_asset_id"] = PUBLIC_CONVENTIONAL_ASSET_ID
        fixture["display_fixture_type"] = PUBLIC_CONVENTIONAL_FIXTURE_TYPE
        fixture["fixture_type"] = PUBLIC_CONVENTIONAL_FIXTURE_TYPE
    plan["fixtures"] = fixtures
    plan["system_id"] = CONVENTIONAL_SYSTEM_ID
    group["ordered_fixture_ids"] = fixture_ids

    matrices = _object(group.get("instance_matrices"), "fixture matrices")
    matrices["filename"] = (
        f"transforms/{PUBLIC_CONVENTIONAL_ASSET_ID}-"
        f"{str(payload.get('fixture_plan_sha256'))[:16]}.f32le.bin"
    )
    group["instance_matrices"] = matrices
    registry = _object(group.get("registry"), "fixture registry")
    registry["approved_display_fixture_type"] = (
        PUBLIC_CONVENTIONAL_FIXTURE_TYPE
    )
    registry["approved_system_id"] = CONVENTIONAL_SYSTEM_ID
    registry["asset_id"] = PUBLIC_CONVENTIONAL_ASSET_ID
    registry["packaged_resource_path"] = PUBLIC_CONVENTIONAL_RESOURCE_PATH
    group["registry"] = registry
    payload["asset_groups"] = [group]
    payload["fixture_plan"] = plan
    run = _object(payload.get("run"), "fixture catalog run")
    run["system_id"] = CONVENTIONAL_SYSTEM_ID
    payload["run"] = run
    return _json_bytes(payload)


def project_committed_viewer_scene(
    source: Mapping[str, object],
    system_id: str,
    *,
    fixture_catalog: bytes,
    visualization: bytes,
) -> dict[str, object]:
    """Bind a viewer scene to its projected JSON dependencies."""

    if system_id == HPS_SYSTEM_ID:
        return project_hps_viewer_scene(
            source,
            fixture_catalog=fixture_catalog,
            visualization=visualization,
        )
    payload = deepcopy(dict(source))
    run = payload.get("run")
    if run is not None:
        projected_run = _object(run, "viewer run")
        projected_run["system_id"] = system_id
        payload["run"] = projected_run
    fixtures = _object(payload.get("fixtures"), "viewer fixtures")
    fixtures["catalog_byte_length"] = len(fixture_catalog)
    fixtures["catalog_sha256"] = hashlib.sha256(fixture_catalog).hexdigest()
    payload["fixtures"] = fixtures
    heatmap = _object(payload.get("ppfd_heatmap"), "viewer PPFD heatmap")
    metadata = _object(heatmap.get("metadata"), "viewer PPFD metadata")
    metadata["byte_length"] = len(visualization)
    metadata["sha256"] = hashlib.sha256(visualization).hexdigest()
    heatmap["metadata"] = metadata
    target = heatmap.get("target_coverage")
    if target is not None:
        projected_target = _object(target, "target coverage")
        projected_target["system_id"] = system_id
        heatmap["target_coverage"] = projected_target
    payload["ppfd_heatmap"] = heatmap
    return payload


def committed_viewer_paths(projected_catalog: bytes) -> ViewerPaths:
    """Return the two public fixture paths declared by a projected catalog."""

    payload = _json_object(projected_catalog, "projected fixture catalog")
    groups = _object_list(payload.get("asset_groups"), "fixture asset groups")
    if len(groups) != 1:
        raise ValueError("fixture catalog must contain one asset group.")
    group = groups[0]
    asset = _object(group.get("asset"), "fixture asset")
    matrices = _object(group.get("instance_matrices"), "fixture matrices")
    asset_name = asset.get("filename")
    matrix_name = matrices.get("filename")
    if not isinstance(asset_name, str) or not isinstance(matrix_name, str):
        raise ValueError("fixture public paths are malformed.")
    return ViewerPaths(f"fixtures/{asset_name}", f"fixtures/{matrix_name}")


def project_hps_metrics(source: Mapping[str, object]) -> dict[str, object]:
    payload = deepcopy(dict(source))
    payload["system_id"] = HPS_SYSTEM_ID
    payload["system"] = SYSTEM_DISPLAY_NAMES[HPS_SYSTEM_ID]
    uniformity = payload.get("baseline_leaf_position_uniformity")
    if isinstance(uniformity, dict):
        target_policy = uniformity.get("target_policy")
        if isinstance(target_policy, dict):
            target_policy["system_id"] = HPS_SYSTEM_ID
    return payload


def project_hps_run_configuration(
    source: Mapping[str, object],
    case: CommittedPlaybackCase,
) -> dict[str, object]:
    safe_fields = (
        "aisle",
        "canonical_domain",
        "dimension_units",
        "far_red",
        "layout_mode",
        "mounting_height",
        "ordered_room_dimensions",
        "radiance_quality",
        "spectral",
    )
    payload = {
        key: deepcopy(source[key]) for key in safe_fields if key in source
    }
    request = case.request.to_dict()
    payload["request"] = {
        key: deepcopy(request[key])
        for key in (
            "system",
            "room_length_ft",
            "room_width_ft",
            "quality",
            "analysis_scope",
            "mounting_height_in",
            "aisle_mode",
            "active_domain",
            "include_far_red",
        )
        if key in request
    }
    raw_fixed = _object(source.get("fixed_plan"), "fixed plan")
    payload["fixed_plan"] = {
        "schema_id": raw_fixed.get("schema_id"),
        "schema_version": raw_fixed.get("schema_version"),
        "plan_identity_sha256": raw_fixed.get("plan_identity_sha256"),
        "public_case_id": case.case_id,
        "artifact_case_id_sha256": case.artifact_case_id_sha256,
        "target_semantics": raw_fixed.get("target_semantics"),
    }
    payload["solver"] = {
        "backend": "authenticated_compact_playback",
        "source_runtime_required": False,
        "canonical_solver_identity_sha256": _json_identity(
            _object(source.get("solver"), "solver")
        ),
    }
    payload["system_id"] = HPS_SYSTEM_ID
    return payload


def project_hps_authenticated_identities(
    source: Mapping[str, object],
    case: CommittedPlaybackCase,
    bundle_identity_sha256: str,
) -> dict[str, object]:
    return {
        "canonical_bundle_identity_sha256": bundle_identity_sha256,
        "canonical_authenticated_identities_sha256": _json_identity(source),
        "artifact_case_id_sha256": case.artifact_case_id_sha256,
    }


def project_hps_json_artifact(name: str, data: bytes) -> bytes:
    payload = _json_object(data, name)
    if name == "target-control.json":
        payload["schema_id"] = "fspm-optics.hps-fixed-output-control"
    elif name == "full-output-schedule.json":
        payload["schema_id"] = "fspm-optics.hps-full-output-schedule"
        authority = _object(payload.get("source_authority"), "source authority")
        authority["ies_angular_resource"] = PUBLIC_HPS_IES_RESOURCE
        authority["relative_spd_resource"] = PUBLIC_HPS_SPD_RESOURCE
        payload["source_authority"] = authority
    elif name == "physical-source-state.json":
        payload["system_id"] = HPS_SYSTEM_ID
        operation = _object(payload.get("source_operation"), "source operation")
        for field, prefix in (
            ("layout_id", "hps-layout-v1"),
            ("policy_id", "hps-fixed-stage-a-source-operation-v2"),
            ("source_plan_id", "hps-radiance-source-v2"),
        ):
            operation[field] = _public_identity(prefix, operation.get(field))
        payload["source_operation"] = operation
    elif name == "baseline-leaf-position-uniformity.v1.json":
        payload["system_id"] = HPS_SYSTEM_ID
        target_policy = _object(payload.get("target_policy"), "target policy")
        target_policy["system_id"] = HPS_SYSTEM_ID
        payload["target_policy"] = target_policy
    elif name not in {"operating-point.json", "natural-fit-layout.json"}:
        raise ValueError("unsupported HPS presentation artifact.")
    return _json_bytes(payload)


def project_hps_visualization(source: Mapping[str, object]) -> dict[str, object]:
    payload = deepcopy(dict(source))
    overlay = _object(payload.get("overlay"), "visualization overlay")
    fixtures = _object_list(overlay.get("fixtures"), "overlay fixtures")
    rectangles = _object_list(overlay.get("rectangles"), "overlay rectangles")
    if len(fixtures) != len(rectangles):
        raise ValueError("HPS visualization fixture ordering is inconsistent.")
    for index, fixture in enumerate(fixtures):
        fixture_id = _fixture_id(index)
        fixture["fixture_id"] = fixture_id
        fixture["aperture_primitive_id"] = f"{fixture_id}-aperture"
        fixture["fixture_type"] = PUBLIC_HPS_FIXTURE_TYPE
    for index, rectangle in enumerate(rectangles):
        fixture_id = _fixture_id(index)
        rectangle["fixture_id"] = fixture_id
        rectangle["primitive_id"] = f"{fixture_id}-aperture"
    layout_id = _public_identity("hps-layout-v1", overlay.get("layout_id"))
    overlay["coordinate_source"] = "HpsLayoutPlan.authoritative_overlay_plan"
    overlay["fixtures"] = fixtures
    overlay["rectangles"] = rectangles
    overlay["layout_id"] = layout_id
    overlay["system_id"] = HPS_SYSTEM_ID
    metadata = _object(overlay.get("metadata"), "overlay metadata")
    metadata["layout_id"] = layout_id
    overlay["metadata"] = metadata
    style = _object(overlay.get("style_policy"), "overlay style policy")
    style["system_id"] = HPS_SYSTEM_ID
    color = _object(style.get("color"), "overlay color policy")
    color["legend"] = "Authoritative 1000W HPS fixtures — red (#ff2d2d)"
    style["color"] = color
    overlay["style_policy"] = style
    payload["overlay"] = overlay
    return payload


def project_hps_fixture_catalog(data: bytes) -> bytes:
    payload = _json_object(data, "fixture catalog")
    groups = _object_list(payload.get("asset_groups"), "fixture asset groups")
    if len(groups) != 1:
        raise ValueError("HPS fixture catalog must contain one asset group.")
    group = groups[0]
    asset = _object(group.get("asset"), "fixture asset")
    if (
        asset.get("byte_size") != PUBLIC_HPS_GLB_BYTE_SIZE
        or asset.get("sha256") != PUBLIC_HPS_GLB_SHA256
    ):
        raise ValueError("HPS fixture binary identity is not authorized.")
    asset["filename"] = (
        f"assets/{PUBLIC_HPS_ASSET_ID}-{PUBLIC_HPS_GLB_SHA256[:16]}.glb"
    )
    group["asset"] = asset
    group["display_asset_id"] = PUBLIC_HPS_ASSET_ID
    group["display_fixture_type"] = PUBLIC_HPS_FIXTURE_TYPE

    plan = _object(payload.get("fixture_plan"), "fixture plan")
    fixtures = _object_list(plan.get("fixtures"), "fixture plan fixtures")
    fixture_ids = [_fixture_id(index) for index in range(len(fixtures))]
    for fixture_id, fixture in zip(fixture_ids, fixtures, strict=True):
        fixture["assembly_id"] = fixture_id
        fixture["display_asset_id"] = PUBLIC_HPS_ASSET_ID
        fixture["display_fixture_type"] = PUBLIC_HPS_FIXTURE_TYPE
        fixture["fixture_id"] = fixture_id
        fixture["fixture_type"] = PUBLIC_HPS_FIXTURE_TYPE
        fixture["placement_contract_sha256"] = (
            PUBLIC_HPS_PLACEMENT_CONTRACT_SHA256
        )
    plan["fixtures"] = fixtures
    plan["system_id"] = HPS_SYSTEM_ID
    group["ordered_fixture_ids"] = fixture_ids

    matrices = _object(group.get("instance_matrices"), "fixture matrices")
    matrices["filename"] = (
        f"transforms/{PUBLIC_HPS_ASSET_ID}-"
        f"{str(payload.get('fixture_plan_sha256'))[:16]}.f32le.bin"
    )
    group["instance_matrices"] = matrices
    registry = _object(group.get("registry"), "fixture registry")
    registry["approved_display_fixture_type"] = PUBLIC_HPS_FIXTURE_TYPE
    registry["approved_system_id"] = HPS_SYSTEM_ID
    registry["asset_id"] = PUBLIC_HPS_ASSET_ID
    registry["packaged_resource_path"] = PUBLIC_HPS_RESOURCE_PATH
    group["registry"] = registry

    payload["asset_groups"] = [group]
    payload["fixture_plan"] = plan
    run = _object(payload.get("run"), "fixture catalog run")
    run["system_id"] = HPS_SYSTEM_ID
    payload["run"] = run
    return _json_bytes(payload)


def project_hps_viewer_scene(
    source: Mapping[str, object],
    *,
    fixture_catalog: bytes,
    visualization: bytes,
) -> dict[str, object]:
    payload = deepcopy(dict(source))
    run = _object(payload.get("run"), "viewer run")
    run["system_id"] = HPS_SYSTEM_ID
    payload["run"] = run
    fixtures = _object(payload.get("fixtures"), "viewer fixtures")
    fixtures["catalog_byte_length"] = len(fixture_catalog)
    fixtures["catalog_sha256"] = hashlib.sha256(fixture_catalog).hexdigest()
    payload["fixtures"] = fixtures
    heatmap = _object(payload.get("ppfd_heatmap"), "viewer PPFD heatmap")
    metadata = _object(heatmap.get("metadata"), "viewer PPFD metadata")
    metadata["byte_length"] = len(visualization)
    metadata["sha256"] = hashlib.sha256(visualization).hexdigest()
    heatmap["metadata"] = metadata
    target = _object(heatmap.get("target_coverage"), "target coverage")
    target["system_id"] = HPS_SYSTEM_ID
    heatmap["target_coverage"] = target
    payload["ppfd_heatmap"] = heatmap
    return payload


def hps_viewer_paths(projected_catalog: bytes) -> HpsViewerPaths:
    payload = _json_object(projected_catalog, "projected fixture catalog")
    groups = _object_list(payload.get("asset_groups"), "fixture asset groups")
    if len(groups) != 1:
        raise ValueError("HPS fixture catalog must contain one asset group.")
    group = groups[0]
    asset = _object(group.get("asset"), "fixture asset")
    matrices = _object(group.get("instance_matrices"), "fixture matrices")
    asset_name = asset.get("filename")
    matrix_name = matrices.get("filename")
    if not isinstance(asset_name, str) or not isinstance(matrix_name, str):
        raise ValueError("HPS fixture public paths are malformed.")
    return HpsViewerPaths(
        f"fixtures/{asset_name}",
        f"fixtures/{matrix_name}",
    )


def _project_proposed_spectral_basis(
    source: Mapping[str, object],
) -> dict[str, object]:
    payload = deepcopy(dict(source))
    payload["id"] = PUBLIC_CONVENTIONAL_SPECTRAL_BASIS_ID
    payload["label"] = PUBLIC_CONVENTIONAL_SPECTRAL_BASIS_LABEL
    payload["source_model_id"] = PUBLIC_PROPOSED_CONTROL_SOURCE_MODEL_ID
    payload["spectral_distribution_id"] = (
        PUBLIC_CONVENTIONAL_SPECTRAL_DISTRIBUTION_ID
    )
    resource_hashes = _object(
        payload.get("resource_hashes"), "spectral resource hashes"
    )
    if len(resource_hashes) != 1:
        raise ValueError("controlled spectrum must authenticate one SPD resource.")
    resource_sha256 = next(iter(resource_hashes.values()))
    if not isinstance(resource_sha256, str) or len(resource_sha256) != 64:
        raise ValueError("controlled spectrum SPD identity is malformed.")
    payload["resource_hashes"] = {
        PUBLIC_CONVENTIONAL_SPD_RESOURCE: resource_sha256
    }
    provenance = _object(
        payload.get("control_provenance"), "spectral control provenance"
    )
    authority = _object(
        provenance.get("relative_spd_authority"), "relative SPD authority"
    )
    if authority.get("sha256") != resource_sha256:
        raise ValueError("controlled spectrum SPD identities disagree.")
    authority["resource"] = PUBLIC_CONVENTIONAL_SPD_RESOURCE
    authority["spectral_distribution_id"] = (
        PUBLIC_CONVENTIONAL_SPECTRAL_DISTRIBUTION_ID
    )
    provenance["relative_spd_authority"] = authority
    payload["control_provenance"] = provenance
    return payload


def _fixture_id(index: int) -> str:
    return f"hps_fixture_{index:04d}"


def _public_identity(prefix: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("canonical HPS identity is missing.")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


def _json_identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _json_object(data: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON.") from exc
    return _object(value, label)


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object.")
    return deepcopy(dict(value))


def _object_list(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise ValueError(f"{label} must be an object list.")
    return [deepcopy(dict(item)) for item in value]


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


__all__ = [
    "HpsViewerPaths",
    "ViewerPaths",
    "PUBLIC_CONVENTIONAL_ASSET_ID",
    "PUBLIC_CONVENTIONAL_FIXTURE_TYPE",
    "PUBLIC_CONVENTIONAL_GLB_BYTE_SIZE",
    "PUBLIC_CONVENTIONAL_GLB_SHA256",
    "PUBLIC_HPS_GLB_BYTE_SIZE",
    "PUBLIC_HPS_GLB_SHA256",
    "committed_viewer_paths",
    "hps_viewer_paths",
    "project_committed_authenticated_identities",
    "project_committed_fixture_catalog",
    "project_committed_json_artifact",
    "project_committed_metrics",
    "project_committed_result",
    "project_committed_run_configuration",
    "project_committed_viewer_scene",
    "project_committed_visualization",
    "project_hps_authenticated_identities",
    "project_hps_fixture_catalog",
    "project_hps_json_artifact",
    "project_hps_metrics",
    "project_hps_run_configuration",
    "project_hps_viewer_scene",
    "project_hps_visualization",
]
