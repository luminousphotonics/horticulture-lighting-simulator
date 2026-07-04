from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from rad_rebuild.radiance.assembly.fspm_panel import build_fspm_panel_metrics
from rad_rebuild.radiance.assembly.classification import PLACEHOLDER_ASSET_KEY, classify_fixture_group
from rad_rebuild.radiance.backend.models import RadianceRunRequest
from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_HPS, MODE_SMD, RADIANCE_MODE_LABELS
from rad_rebuild.radiance.engine.plants.artifacts import PLANTS_VIEWER_FILENAME
from rad_rebuild.radiance.engine.plants.surface_flux import (
    PLANT_SURFACE_FLUX_FILENAME,
    PLANT_SURFACE_FLUX_SCHEMA,
)

SCENE_SCHEMA_VERSION = 3
SYSTEM_KEY = "proposed_led_system"
MODE_LABEL = "Proposed LED System"
STATIC_ROOT_URL = f"/static/viewer/{SYSTEM_KEY}"
VIEWER_STATIC_ROOT_PATH = Path(__file__).resolve().parents[2] / "web" / "static" / "viewer"
ASSET_ROOT_PATH = VIEWER_STATIC_ROOT_PATH / SYSTEM_KEY
SIMPLE_SYSTEMS: dict[str, dict[str, str]] = {
    MODE_COMPETITOR: {
        "system": "conventional_led_system",
        "layout_filename": "spydr3_layout.json",
        "layout_label": "Conventional LED",
    },
    MODE_HPS: {
        "system": "hps_1000w_system",
        "layout_filename": "hps_layout.json",
        "layout_label": "1000W HPS",
    },
}
PLANT_VIEWER_RELATIVE_PATH = Path("runtime_state") / PLANTS_VIEWER_FILENAME
PLANT_SURFACE_FLUX_RELATIVE_PATH = Path("runtime_state") / PLANT_SURFACE_FLUX_FILENAME

ASSET_URLS: dict[str, str] = {
    "manifest": f"{STATIC_ROOT_URL}/manifest.json",
    "anchors": f"{STATIC_ROOT_URL}/anchors.json",
}

AXIS_MAPPING: dict[str, object] = {
    "cad_horizontal": ["x", "z"],
    "cad_vertical": "y",
    "layout_horizontal": ["x", "y"],
    "layout_vertical": "z",
}

MODULE_ASSETS: dict[str, dict[str, object]] = {
    "centerpiece": {
        "high": "fixture_centerpiece.high.glb",
        "medium": "fixture_centerpiece.medium.glb",
        "proxy": "fixture_centerpiece.proxy.glb",
        "anchor_source": "module_nodes",
    },
    "linear2": {
        "high": "fixture_L2_linear.high.glb",
        "medium": "fixture_L2_linear.medium.glb",
        "proxy": "fixture_L2_linear.proxy.glb",
        "anchor_source": "module_nodes",
    },
    "linear3_linear": {
        "high": "fixture_L3_linear.high.glb",
        "medium": "fixture_L3_linear.medium.glb",
        "proxy": "fixture_L3_linear.proxy.glb",
        "anchor_source": "module_nodes",
    },
    "linear3_corner": {
        "high": "fixture_L3.high.glb",
        "medium": "fixture_L3.medium.glb",
        "proxy": "fixture_L3.proxy.glb",
        "anchor_source": "module_nodes",
        "optional": True,
        "fallback_asset_key": "linear3_linear",
    },
    "linear4_linear": {
        "high": "fixture_L4_linear.high.glb",
        "medium": "fixture_L4_linear.medium.glb",
        "proxy": "fixture_L4_linear.proxy.glb",
        "anchor_source": "module_nodes",
    },
    "l4_corner": {
        "high": "fixture_L4.high.glb",
        "medium": "fixture_L4.medium.glb",
        "proxy": "fixture_L4.proxy.glb",
        "anchor_source": "module_nodes",
    },
    "l4_reverse_corner": {
        "high": "fixture_L4_reverse.high.glb",
        "medium": "fixture_L4_reverse.medium.glb",
        "proxy": "fixture_L4_reverse.proxy.glb",
        "anchor_source": "module_nodes",
    },
}


class AssemblySceneError(ValueError):
    """Public-safe assembly scene construction failure."""

    def __init__(self, message: str, *, status_code: int = 422, error: str = "invalid_assembly_scene") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message


def _finite_float(value: object, label: str, *, error_prefix: str = "Malformed SMD layout") -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AssemblySceneError(f"{error_prefix}: {label} must be a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise AssemblySceneError(f"{error_prefix}: {label} must be a finite number.")
    return number


def _optional_finite_float(
    value: object,
    label: str,
    *,
    error_prefix: str = "Malformed SMD layout",
) -> float | None:
    if value is None:
        return None
    return _finite_float(value, label, error_prefix=error_prefix)


def _load_layout(
    layout_path: Path,
    *,
    missing_message: str = "SMD assembly layout was not found.",
    malformed_prefix: str = "SMD assembly layout",
) -> dict[str, Any]:
    if not layout_path.is_file():
        raise AssemblySceneError(missing_message, status_code=404, error="assembly_layout_not_found")
    try:
        raw = json.loads(layout_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssemblySceneError(f"{malformed_prefix} is malformed.") from exc
    if not isinstance(raw, dict):
        raise AssemblySceneError(f"{malformed_prefix} is malformed.")
    if raw.get("units") != "meters":
        raise AssemblySceneError(f"{malformed_prefix} must use meters.")
    return raw


def _layout_room(
    layout: dict[str, Any],
    req: RadianceRunRequest,
    *,
    mount_fallback_m: float | None = None,
    error_prefix: str = "Malformed SMD layout",
) -> dict[str, float]:
    raw_room = layout.get("room")
    room = raw_room if isinstance(raw_room, dict) else {}
    length_m = _optional_finite_float(room.get("L"), "room.L", error_prefix=error_prefix)
    width_m = _optional_finite_float(room.get("W"), "room.W", error_prefix=error_prefix)
    mount_z_m = _optional_finite_float(layout.get("z"), "z", error_prefix=error_prefix)
    return {
        "length_ft": float(req.length_ft),
        "width_ft": float(req.width_ft),
        "length_m": length_m if length_m is not None else float(req.length_ft) * 0.3048,
        "width_m": width_m if width_m is not None else float(req.width_ft) * 0.3048,
        "mount_z_m": mount_z_m if mount_z_m is not None else float(mount_fallback_m or req.mount_z_m),
    }


def _fixture_points(group: dict[str, Any], group_index: int) -> list[dict[str, Any]]:
    raw_points = group.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        raise AssemblySceneError(f"Malformed SMD layout: fixture group {group_index} has no points.")
    points: list[dict[str, Any]] = []
    for point_index, raw_point in enumerate(raw_points):
        if not isinstance(raw_point, dict):
            raise AssemblySceneError(f"Malformed SMD layout: fixture group {group_index} point {point_index} is invalid.")
        points.append(raw_point)
    return points


def _public_module_assets() -> dict[str, dict[str, object]]:
    module_assets: dict[str, dict[str, object]] = {}
    for asset_key, asset in MODULE_ASSETS.items():
        public_asset: dict[str, object] = {}
        for quality in ("high", "medium", "proxy"):
            value = asset.get(quality)
            if isinstance(value, str):
                asset_path = ASSET_ROOT_PATH / value
                public_asset[quality] = f"{STATIC_ROOT_URL}/{value}" if asset_path.is_file() else None
        public_asset["anchor_source"] = asset.get("anchor_source", "module_nodes")
        if asset.get("optional") is True:
            public_asset["optional"] = True
        fallback_asset_key = asset.get("fallback_asset_key")
        if isinstance(fallback_asset_key, str):
            public_asset["fallback_asset_key"] = fallback_asset_key
        module_assets[asset_key] = public_asset
    return module_assets


def _simple_static_root_url(system_key: str) -> str:
    return f"/static/viewer/{system_key}"


def _simple_asset_root_path(system_key: str) -> Path:
    return VIEWER_STATIC_ROOT_PATH / system_key


def _simple_asset_urls(system_key: str) -> dict[str, dict[str, object]]:
    static_root_url = _simple_static_root_url(system_key)
    asset_root_path = _simple_asset_root_path(system_key)
    asset: dict[str, object] = {}
    for quality in ("high", "medium", "proxy"):
        filename = f"fixture.{quality}.glb"
        asset[quality] = f"{static_root_url}/{filename}" if (asset_root_path / filename).is_file() else None
    return {"fixture": asset}


def _simple_asset_high_exists(system_key: str, asset_key: str) -> bool:
    if asset_key != "fixture":
        return False
    return (_simple_asset_root_path(system_key) / "fixture.high.glb").is_file()


def _asset_high_path(asset_key: str) -> Path | None:
    asset = MODULE_ASSETS.get(asset_key)
    if asset is None:
        return None
    high = asset.get("high")
    if not isinstance(high, str):
        return None
    return ASSET_ROOT_PATH / high


def _asset_high_exists(asset_key: str) -> bool:
    high_path = _asset_high_path(asset_key)
    return high_path is not None and high_path.is_file()


def _fallback_for_asset(asset_key: str) -> str | None:
    asset = MODULE_ASSETS.get(asset_key)
    if asset is None or asset.get("optional") is not True:
        return None
    fallback_asset_key = asset.get("fallback_asset_key")
    if isinstance(fallback_asset_key, str) and _asset_high_exists(fallback_asset_key):
        return fallback_asset_key
    return None


def _resolved_asset_fallback(asset_key: str) -> tuple[str | None, str | None]:
    if asset_key == PLACEHOLDER_ASSET_KEY:
        return None, "placeholder fixture asset key has no static GLB."
    if asset_key not in MODULE_ASSETS:
        return None, f"fixture asset key {asset_key!r} is not defined in the viewer manifest."
    if _asset_high_exists(asset_key):
        return None, None
    fallback_asset_key = _fallback_for_asset(asset_key)
    if fallback_asset_key is not None:
        return fallback_asset_key, f"optional fixture asset {asset_key!r} is missing; using {fallback_asset_key!r}."
    return None, f"fixture asset {asset_key!r} is missing its high-detail GLB."


def _direct_fixture_point(
    raw_point: object,
    *,
    label: str,
    z_m: float,
    error_prefix: str,
) -> dict[str, float]:
    if not isinstance(raw_point, list) or len(raw_point) < 2:
        raise AssemblySceneError(f"{error_prefix}: {label} must be a two-number coordinate.")
    return {
        "x": _finite_float(raw_point[0], f"{label}[0]", error_prefix=error_prefix),
        "y": _finite_float(raw_point[1], f"{label}[1]", error_prefix=error_prefix),
        "z": z_m,
    }


def _direct_fixture_points(
    raw_fixture: dict[str, Any],
    *,
    fixture_index: int,
    z_m: float,
    error_prefix: str,
) -> list[dict[str, float]]:
    raw_corners = raw_fixture.get("body_corners")
    if not isinstance(raw_corners, list) or len(raw_corners) < 4:
        raise AssemblySceneError(f"{error_prefix}: fixtures[{fixture_index}].body_corners must contain four corners.")
    return [
        _direct_fixture_point(
            raw_corner,
            label=f"fixtures[{fixture_index}].body_corners[{corner_index}]",
            z_m=z_m,
            error_prefix=error_prefix,
        )
        for corner_index, raw_corner in enumerate(raw_corners)
    ]


def _yaw_from_lamp_line(
    raw_fixture: dict[str, Any],
    *,
    fixture_index: int,
    error_prefix: str,
) -> float | None:
    raw_lamp_line = raw_fixture.get("lamp_line")
    if raw_lamp_line is None:
        return None
    if not isinstance(raw_lamp_line, list) or len(raw_lamp_line) < 2:
        raise AssemblySceneError(f"{error_prefix}: fixtures[{fixture_index}].lamp_line must contain two points.")
    points: list[tuple[float, float]] = []
    for point_index, raw_point in enumerate(raw_lamp_line[:2]):
        if not isinstance(raw_point, list) or len(raw_point) < 2:
            raise AssemblySceneError(
                f"{error_prefix}: fixtures[{fixture_index}].lamp_line[{point_index}] must be a coordinate."
            )
        points.append(
            (
                _finite_float(
                    raw_point[0],
                    f"fixtures[{fixture_index}].lamp_line[{point_index}][0]",
                    error_prefix=error_prefix,
                ),
                _finite_float(
                    raw_point[1],
                    f"fixtures[{fixture_index}].lamp_line[{point_index}][1]",
                    error_prefix=error_prefix,
                ),
            )
        )
    dx = points[1][0] - points[0][0]
    dy = points[1][1] - points[0][1]
    if dx == 0.0 and dy == 0.0:
        raise AssemblySceneError(f"{error_prefix}: fixtures[{fixture_index}].lamp_line must span a non-zero vector.")
    return round(math.degrees(math.atan2(dy, dx)), 9)


def _instance_from_classification(
    raw_group: dict[str, Any],
    classification: dict[str, Any],
    fixture_id: str,
) -> tuple[dict[str, Any], str | None]:
    asset_key = str(classification["asset_key"])
    asset_fallback_key, asset_warning = _resolved_asset_fallback(asset_key)
    warnings = list(classification.get("warnings", []))
    if asset_warning is not None:
        warnings.append(asset_warning)
    orient = raw_group.get("orient") or raw_group.get("orientation")
    return (
        {
            "id": fixture_id,
            "layout_type": classification["layout_type"],
            "orient": orient if isinstance(orient, str) and orient else None,
            "module_count": classification["module_count"],
            "shape": classification["shape"],
            "asset_key": asset_key,
            "asset_fallback_key": asset_fallback_key,
            "points": classification["points"],
            "warnings": warnings,
        },
        asset_fallback_key,
    )


def _instances_from_fixture_groups(layout: dict[str, Any]) -> list[dict[str, Any]]:
    raw_groups = layout.get("fixture_groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        return []
    instances: list[dict[str, Any]] = []
    for group_index, raw_group in enumerate(raw_groups):
        if not isinstance(raw_group, dict):
            raise AssemblySceneError(f"Malformed SMD layout: fixture group {group_index} is invalid.")
        classification = classify_fixture_group(raw_group, group_index=group_index)
        instance, _asset_fallback_key = _instance_from_classification(
            raw_group,
            classification,
            f"fixture-{group_index + 1:04d}",
        )
        instances.append(instance)
    return instances


def _instances_from_positions(layout: dict[str, Any]) -> list[dict[str, Any]]:
    raw_positions = layout.get("positions")
    if not isinstance(raw_positions, list) or not raw_positions:
        raise AssemblySceneError("Malformed SMD layout: positions are required.")
    instances: list[dict[str, Any]] = []
    for index, raw_position in enumerate(raw_positions):
        if not isinstance(raw_position, dict):
            raise AssemblySceneError(f"Malformed SMD layout: position {index} is invalid.")
        x_m = _finite_float(raw_position.get("x"), f"positions[{index}].x")
        y_m = _finite_float(raw_position.get("y"), f"positions[{index}].y")
        z_m = _finite_float(raw_position.get("z"), f"positions[{index}].z")
        instances.append(
            {
                "id": f"fixture-{index + 1:04d}",
                "layout_type": "position",
                "orient": None,
                "module_count": 1,
                "shape": "unknown",
                "asset_key": PLACEHOLDER_ASSET_KEY,
                "asset_fallback_key": None,
                "points": [{"x": x_m, "y": y_m, "z": z_m}],
                "warnings": ["SMD layout did not include fixture_groups; rendering position placeholders."],
            }
        )
    return instances


def _direct_fixture_yaw(
    layout: dict[str, Any],
    raw_fixture: dict[str, Any],
    *,
    fixture_index: int,
    error_prefix: str,
) -> tuple[float, str | None]:
    raw_rot_deg = raw_fixture.get("rot_deg", layout.get("rot_deg"))
    if raw_rot_deg is not None:
        return _finite_float(raw_rot_deg, "rot_deg", error_prefix=error_prefix), None
    yaw_from_lamp = _yaw_from_lamp_line(raw_fixture, fixture_index=fixture_index, error_prefix=error_prefix)
    if yaw_from_lamp is not None:
        return yaw_from_lamp, None
    return 0.0, "layout did not include fixture orientation; defaulting yaw_deg to 0.0."


def _direct_fixture_instances(
    layout: dict[str, Any],
    *,
    mode: str,
    layout_filename: str,
    error_prefix: str,
) -> list[dict[str, Any]]:
    raw_fixtures = layout.get("fixtures")
    if not isinstance(raw_fixtures, list) or not raw_fixtures:
        raise AssemblySceneError(f"{error_prefix}: fixtures are required.")
    z_m = _finite_float(layout.get("z"), "z", error_prefix=error_prefix)
    instances: list[dict[str, Any]] = []
    for fixture_index, raw_fixture in enumerate(raw_fixtures):
        if not isinstance(raw_fixture, dict):
            raise AssemblySceneError(f"{error_prefix}: fixture {fixture_index} is invalid.")
        x_m = _finite_float(raw_fixture.get("cx"), f"fixtures[{fixture_index}].cx", error_prefix=error_prefix)
        y_m = _finite_float(raw_fixture.get("cy"), f"fixtures[{fixture_index}].cy", error_prefix=error_prefix)
        points = _direct_fixture_points(
            raw_fixture,
            fixture_index=fixture_index,
            z_m=z_m,
            error_prefix=error_prefix,
        )
        yaw_deg, yaw_warning = _direct_fixture_yaw(
            layout,
            raw_fixture,
            fixture_index=fixture_index,
            error_prefix=error_prefix,
        )
        warnings = [yaw_warning] if yaw_warning is not None else []
        position = {"x": x_m, "y": y_m, "z": z_m}
        instances.append(
            {
                "id": f"fixture-{fixture_index + 1:04d}",
                "layout_type": "single_fixture_center",
                "orient": f"yaw_deg:{yaw_deg:g}",
                "module_count": 1,
                "shape": "fixture",
                "asset_key": "fixture",
                "asset_fallback_key": None,
                "points": points,
                "position": position,
                "yaw_deg": yaw_deg,
                "layout_source": {
                    "mode": mode,
                    "path": f"runtime_state/{layout_filename}",
                    "fixture_index": fixture_index,
                    "position_fields": ["cx", "cy", "z"],
                    "orientation_source": "rot_deg" if raw_fixture.get("rot_deg", layout.get("rot_deg")) is not None else (
                        "lamp_line" if raw_fixture.get("lamp_line") is not None else "default"
                    ),
                },
                "warnings": warnings,
            }
        )
    return instances


def _count_by_key(instances: list[dict[str, Any]], field_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for instance in instances:
        value = instance.get(field_name)
        key = value if isinstance(value, str) and value else "unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _scene_asset_fallbacks(instances: list[dict[str, Any]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for instance in instances:
        asset_key = instance.get("asset_key")
        fallback_asset_key = instance.get("asset_fallback_key")
        if not isinstance(asset_key, str) or not isinstance(fallback_asset_key, str):
            continue
        grouped.setdefault((asset_key, fallback_asset_key), []).append(str(instance["id"]))
    return [
        {
            "asset_key": asset_key,
            "fallback_asset_key": fallback_asset_key,
            "instance_ids": instance_ids,
            "reason": f"optional fixture asset {asset_key!r} is missing; using {fallback_asset_key!r}.",
        }
        for (asset_key, fallback_asset_key), instance_ids in sorted(grouped.items())
    ]


def _scene_missing_asset_keys(
    instances: list[dict[str, Any]],
    *,
    asset_exists: Any = _asset_high_exists,
) -> list[str]:
    missing: set[str] = set()
    for instance in instances:
        asset_key = instance.get("asset_key")
        if not isinstance(asset_key, str) or instance.get("asset_fallback_key"):
            continue
        if asset_key == PLACEHOLDER_ASSET_KEY or not asset_exists(asset_key):
            missing.add(asset_key)
    return sorted(missing)


def _scene_warnings(instances: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for instance in instances:
        for warning in instance.get("warnings", []):
            if isinstance(warning, str):
                warnings.append(f"{instance['id']}: {warning}")
    return warnings


def _optional_plant_viewer_payload(workspace_root: Path) -> dict[str, Any] | None:
    plant_path = workspace_root / PLANT_VIEWER_RELATIVE_PATH
    if not plant_path.is_file():
        return None
    try:
        payload = json.loads(plant_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssemblySceneError("Plant viewer payload is malformed.") from exc
    if not isinstance(payload, dict):
        raise AssemblySceneError("Plant viewer payload is malformed.")
    if payload.get("schema") != "rad_rebuild.fspm.plants.viewer.v1":
        raise AssemblySceneError("Plant viewer payload has an unsupported schema.")
    return payload


def _optional_plant_surface_flux_payload(workspace_root: Path) -> dict[str, Any] | None:
    path = workspace_root / PLANT_SURFACE_FLUX_RELATIVE_PATH
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssemblySceneError("Plant surface-flux payload is malformed.") from exc
    if not isinstance(payload, dict):
        raise AssemblySceneError("Plant surface-flux payload is malformed.")
    if payload.get("schema") != PLANT_SURFACE_FLUX_SCHEMA:
        raise AssemblySceneError("Plant surface-flux payload has an unsupported schema.")
    visualization = payload.get("visualization")
    compact_visualization: dict[str, Any] = {}
    if isinstance(visualization, dict):
        compact_visualization = {
            key: visualization.get(key)
            for key in (
                "color_metric",
                "color_quantity",
                "normalization",
                "leaf_scale",
                "leaf_values",
                "plant_values",
            )
            if key in visualization
        }

    metadata_keys = (
        "artifact_role",
        "baseline_transport_scene",
        "fspm_receiver_transport_scene",
        "receiver_trace_count",
        "receiver_sample_count",
        "receiver_granularity",
        "receiver_samples_per_leaf",
        "receiver_generation_basis",
        "receiver_represented_area_m2",
        "receiver_sample_area_sum_m2",
        "receiver_area_basis",
        "receiver_side_policy",
        "receiver_rows_per_mesh_surface_row",
        "normal_generation_basis",
        "receiver_granularity_role",
        "plant_count",
        "leaf_count",
        "surface_count",
        "one_sided_leaf_area_m2",
        "target",
        "target_ppfd_umol_m2_s",
        "target_tolerance_umol_m2_s",
        "target_classification_basis",
        "target_classification_basis_label",
        "target_classification_source",
        "target_classification_note",
        "target_basis",
        "target_basis_label",
        "target_lower_threshold_umol_m2_s",
        "target_upper_threshold_umol_m2_s",
        "target_capping_enabled",
        "runtime_source",
    )
    compact = {
        "schema": payload.get("schema"),
        "schema_version": payload.get("schema_version"),
        "status": payload.get("status"),
        "method": payload.get("method"),
        **{key: payload.get(key) for key in metadata_keys if key in payload},
    }
    if compact_visualization:
        compact["visualization"] = compact_visualization
    return compact


def _attach_optional_plants(scene: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    plant_payload = _optional_plant_viewer_payload(workspace_root)
    if plant_payload is not None:
        surface_flux = _optional_plant_surface_flux_payload(workspace_root)
        if surface_flux is not None:
            plant_payload = {**plant_payload, "surface_flux": surface_flux}
        scene["plants"] = plant_payload
    fspm_metrics = build_fspm_panel_metrics(workspace_root)
    if fspm_metrics is not None:
        scene["fspm_metrics"] = fspm_metrics
    return scene


def _build_smd_scene(workspace_root: Path, req: RadianceRunRequest) -> dict[str, Any]:
    layout = _load_layout(workspace_root / "runtime_state" / "smd_layout.json")
    instances = _instances_from_fixture_groups(layout) or _instances_from_positions(layout)
    return _attach_optional_plants({
        "schema_version": SCENE_SCHEMA_VERSION,
        "system": SYSTEM_KEY,
        "mode": MODE_SMD,
        "mode_label": MODE_LABEL,
        "display_name": MODE_LABEL,
        "units": "meters",
        "source_units": "millimeters",
        "viewer_scale": 0.001,
        "axis_mapping": dict(AXIS_MAPPING),
        "placement_strategy": "anchor_fit",
        "room": _layout_room(layout, req),
        "assets": dict(ASSET_URLS),
        "module_assets": _public_module_assets(),
        "instances": instances,
        "fixture_counts_by_layout_type": _count_by_key(instances, "layout_type"),
        "fixture_counts_by_asset_key": _count_by_key(instances, "asset_key"),
        "missing_asset_keys": _scene_missing_asset_keys(instances),
        "asset_fallbacks_used": _scene_asset_fallbacks(instances),
        "warnings": _scene_warnings(instances),
    }, workspace_root)


def _build_simple_fixture_scene(workspace_root: Path, req: RadianceRunRequest) -> dict[str, Any]:
    config = SIMPLE_SYSTEMS[req.mode]
    system_key = config["system"]
    layout_filename = config["layout_filename"]
    layout_label = config["layout_label"]
    malformed_prefix = f"{layout_label} assembly layout"
    layout = _load_layout(
        workspace_root / "runtime_state" / layout_filename,
        missing_message=f"{layout_label} assembly layout was not found.",
        malformed_prefix=malformed_prefix,
    )
    instances = _direct_fixture_instances(
        layout,
        mode=req.mode,
        layout_filename=layout_filename,
        error_prefix=f"Malformed {layout_label} layout",
    )
    assets = {"manifest": f"{_simple_static_root_url(system_key)}/manifest.json"}
    return _attach_optional_plants({
        "schema_version": SCENE_SCHEMA_VERSION,
        "system": system_key,
        "mode": req.mode,
        "mode_label": RADIANCE_MODE_LABELS.get(req.mode, req.mode),
        "display_name": RADIANCE_MODE_LABELS.get(req.mode, req.mode),
        "units": "meters",
        "source_units": "millimeters",
        "viewer_scale": 0.001,
        "axis_mapping": dict(AXIS_MAPPING),
        "placement_strategy": "single_fixture_center",
        "room": _layout_room(
            layout,
            req,
            mount_fallback_m=req.hps_z_m if req.mode == MODE_HPS else req.sp_z_m,
            error_prefix=f"Malformed {layout_label} layout",
        ),
        "assets": assets,
        "module_assets": _simple_asset_urls(system_key),
        "instances": instances,
        "fixture_counts_by_layout_type": _count_by_key(instances, "layout_type"),
        "fixture_counts_by_asset_key": _count_by_key(instances, "asset_key"),
        "missing_asset_keys": _scene_missing_asset_keys(
            instances,
            asset_exists=lambda asset_key: _simple_asset_high_exists(system_key, asset_key),
        ),
        "asset_fallbacks_used": [],
        "warnings": _scene_warnings(instances),
    }, workspace_root)


def build_assembly_scene(workspace_root: Path, req: RadianceRunRequest) -> dict[str, Any]:
    if req.mode == MODE_SMD:
        return _build_smd_scene(workspace_root, req)
    if req.mode in SIMPLE_SYSTEMS:
        return _build_simple_fixture_scene(workspace_root, req)
    raise AssemblySceneError(
        f"3D assembly view is not available for mode {req.mode!r}.",
        status_code=422,
        error="assembly_view_unsupported",
    )
