"""Bind an existing Stage A PPFD visualization into the run-local viewer."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from typing import Mapping, Sequence

from fspm_optics.viewer.models import (
    BinaryDisplayArtifact,
    PpfdHeatmapViewerArtifacts,
)

SCALAR_STRIDE_BYTES = 12
INTERPOLATION_POLICY_ID = "bilinear_scalar_edge_clamp_v1"
COLORMAP_ID = "fspm-viridis-8-linear-srgb-v1"
COLORMAP_ANCHORS_SRGB_8BIT = [
    [68, 1, 84],
    [70, 50, 126],
    [54, 92, 141],
    [39, 127, 142],
    [31, 161, 135],
    [74, 193, 109],
    [160, 218, 57],
    [253, 231, 37],
]
_TRANSFORM_FIELDS = frozenset(
    (
        "physics_recomputed",
        "target_rescaling",
        "symmetrization",
        "smoothing",
        "clipping",
        "correction",
        "hidden_normalization",
        "sample_exclusion",
    )
)


def bind_ppfd_heatmap_viewer_artifacts(
    *,
    metadata_bytes: bytes,
    scatter_bytes: bytes,
    expected_field_identity_sha256: str,
    target_coverage: Mapping[str, object] | None = None,
) -> PpfdHeatmapViewerArtifacts:
    """Validate and bind exact existing visualization/scatter bytes."""

    if not _valid_sha256(expected_field_identity_sha256):
        raise ValueError("PPFD heatmap field identity is incompatible.")
    try:
        metadata = json.loads(metadata_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("PPFD heatmap metadata is not valid UTF-8 JSON.") from exc
    if (
        not isinstance(metadata, dict)
        or set(metadata)
        != {
            "annotations",
            "artifacts",
            "display",
            "field",
            "grid",
            "orientation",
            "overlay",
            "run_id",
            "scatter",
            "schema_id",
            "schema_version",
            "transforms",
        }
        or metadata.get("schema_id") != "fspm-optics.ppfd-visualization"
        or metadata.get("schema_version") != 1
    ):
        raise ValueError("PPFD heatmap metadata schema is incompatible.")

    field = _mapping(metadata.get("field"), "PPFD heatmap field")
    grid = _mapping(metadata.get("grid"), "PPFD heatmap grid")
    display = _mapping(metadata.get("display"), "PPFD heatmap display")
    scatter = _mapping(metadata.get("scatter"), "PPFD heatmap scatter")
    artifacts = _mapping(metadata.get("artifacts"), "visualization inventory")
    orientation = _mapping(metadata.get("orientation"), "PPFD heatmap orientation")
    transforms = _mapping(metadata.get("transforms"), "PPFD heatmap transforms")
    resolution = _mapping(grid.get("resolution"), "PPFD heatmap resolution")
    extent = _mapping(grid.get("extent_m"), "PPFD heatmap extent")
    interpolation = _mapping(
        grid.get("interpolation"), "PPFD heatmap interpolation"
    )

    width = _positive_int(resolution.get("x"), "PPFD heatmap width")
    height = _positive_int(resolution.get("y"), "PPFD heatmap height")
    count = _positive_int(scatter.get("count"), "PPFD heatmap count")
    if (
        width < 2
        or height < 2
        or count != width * height
    ):
        raise ValueError("PPFD heatmap grid inventory is incompatible.")
    if (
        not _valid_run_id(metadata.get("run_id"))
        or set(resolution) != {"x", "y"}
        or set(extent) != {"x_max", "x_min", "y_max", "y_min"}
        or grid.get("kind") != "regular"
        or grid.get("reshape_order")
        != "rows_ascending_y_columns_ascending_x"
        or grid.get("image_origin") != "lower"
        or set(interpolation) != {
            "applied",
            "fill_behavior",
            "method",
            "original_sample_identity_sha256",
            "resolution",
        }
        or interpolation.get("applied") is not False
        or interpolation.get("method") is not None
        or interpolation.get("resolution") is not None
        or interpolation.get("fill_behavior") is not None
        or interpolation.get("original_sample_identity_sha256")
        != expected_field_identity_sha256
        or orientation.get("sample_coordinates_transformed") is not False
        or orientation.get("positive_y_is_image_up") is not True
        or set(transforms) != _TRANSFORM_FIELDS
        or any(
            value is not False
            for name, value in transforms.items()
            if name != "target_rescaling"
        )
        or not isinstance(transforms.get("target_rescaling"), bool)
        or scatter.get("filename") != "ppfd-scatter.f32le.bin"
        or scatter.get("byte_order") != "little-endian"
        or scatter.get("component_type") != "float32"
        or scatter.get("record_layout") != "x_m,y_m,ppfd_umol_m2_s"
        or scatter.get("stride_bytes") != SCALAR_STRIDE_BYTES
        or scatter.get("byte_length") != count * SCALAR_STRIDE_BYTES
        or len(scatter_bytes) != count * SCALAR_STRIDE_BYTES
    ):
        raise ValueError("PPFD heatmap scatter layout is incompatible.")

    scatter_sha256 = hashlib.sha256(scatter_bytes).hexdigest()
    inventory_scatter = _mapping(
        artifacts.get("ppfd-scatter.f32le.bin"),
        "PPFD heatmap scatter inventory",
    )
    field_identity = field.get("identity_sha256")
    if (
        scatter.get("sha256") != scatter_sha256
        or inventory_scatter.get("sha256") != scatter_sha256
        or inventory_scatter.get("byte_length") != len(scatter_bytes)
        or scatter.get("source_field_identity_sha256") != field_identity
        or field_identity != expected_field_identity_sha256
        or field.get("sample_count") != count
        or field.get("coordinate_system")
        != "right-handed scientific XY, meters, Z-up"
        or field.get("value_units")
        != "micromole_per_square_meter_per_second"
    ):
        raise ValueError("PPFD heatmap Stage A identity is incompatible.")

    x_centers = _finite_axis(grid.get("x_centers_m"), width, "X")
    y_centers = _finite_axis(grid.get("y_centers_m"), height, "Y")
    _validate_regular_axis(x_centers, "X")
    _validate_regular_axis(y_centers, "Y")
    expected_extent = {
        "x_min": x_centers[0] - (x_centers[1] - x_centers[0]) * 0.5,
        "x_max": x_centers[-1] + (x_centers[-1] - x_centers[-2]) * 0.5,
        "y_min": y_centers[0] - (y_centers[1] - y_centers[0]) * 0.5,
        "y_max": y_centers[-1] + (y_centers[-1] - y_centers[-2]) * 0.5,
    }
    if any(extent.get(name) != value for name, value in expected_extent.items()):
        raise ValueError("PPFD heatmap authenticated cell-edge extent is incompatible.")

    reference_plane_z_m = _finite_number(
        field.get("reference_plane_z_m"), "PPFD heatmap reference plane"
    )
    color_limits = display.get("color_limits_ppfd_umol_m2_s")
    colormap = _mapping(display.get("colormap"), "PPFD heatmap colormap")
    if (
        not isinstance(color_limits, list)
        or len(color_limits) != 2
        or any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            for value in color_limits
        )
        or float(color_limits[1]) <= float(color_limits[0])
        or colormap.get("name") != COLORMAP_ID
        or colormap.get("anchors_srgb_8bit") != COLORMAP_ANCHORS_SRGB_8BIT
    ):
        raise ValueError("PPFD heatmap display scale is incompatible.")

    float32_x = {
        struct.unpack("<f", struct.pack("<f", value))[0]: index
        for index, value in enumerate(x_centers)
    }
    float32_y = {
        struct.unpack("<f", struct.pack("<f", value))[0]: index
        for index, value in enumerate(y_centers)
    }
    if len(float32_x) != width or len(float32_y) != height:
        raise ValueError("PPFD heatmap centers collapse after Float32 conversion.")
    occupied: set[tuple[int, int]] = set()
    for x_m, y_m, ppfd in struct.iter_unpack("<3f", scatter_bytes):
        if (
            not all(math.isfinite(value) for value in (x_m, y_m, ppfd))
            or ppfd < 0.0
            or x_m not in float32_x
            or y_m not in float32_y
        ):
            raise ValueError("PPFD heatmap scatter contains an invalid record.")
        key = (float32_x[x_m], float32_y[y_m])
        if key in occupied:
            raise ValueError("PPFD heatmap scatter contains a duplicate cell.")
        occupied.add(key)
    if len(occupied) != count:
        raise ValueError("PPFD heatmap scatter has a missing cell.")

    metadata_sha256 = hashlib.sha256(metadata_bytes).hexdigest()
    metadata_artifact = BinaryDisplayArtifact(
        filename="ppfd-heatmap/visualization.json",
        data=metadata_bytes,
        sha256=metadata_sha256,
    )
    scalar_artifact = BinaryDisplayArtifact(
        filename="ppfd-heatmap/ppfd-scatter.f32le.bin",
        data=scatter_bytes,
        sha256=scatter_sha256,
    )
    scene_reference: dict[str, object] = {
        "availability": "available",
        "metadata": {
            "filename": metadata_artifact.filename,
            "sha256": metadata_sha256,
            "byte_length": len(metadata_bytes),
        },
        "scalar_field": {
            "filename": scalar_artifact.filename,
            "sha256": scatter_sha256,
            "byte_length": len(scatter_bytes),
            "component_type": "float32",
            "byte_order": "little-endian",
            "record_layout": "x_m,y_m,ppfd_umol_m2_s",
            "stride_bytes": SCALAR_STRIDE_BYTES,
            "count": count,
        },
        "grid": {
            "width": width,
            "height": height,
            "x_centers_m": list(x_centers),
            "y_centers_m": list(y_centers),
            "cell_edge_bounds_m": expected_extent,
            "reference_plane_z_m": reference_plane_z_m,
            "coordinate_system": "right-handed scientific XY, meters, Z-up",
            "scientific_to_viewer": "(x, y, z) -> (x, z, -y)",
        },
        "scalar_units": "micromole_per_square_meter_per_second",
        "display_scale": {
            "minimum_ppfd_umol_m2_s": float(color_limits[0]),
            "maximum_ppfd_umol_m2_s": float(color_limits[1]),
            "colormap": COLORMAP_ID,
        },
        "source_field_identity_sha256": field_identity,
        "interpolation_policy_id": INTERPOLATION_POLICY_ID,
    }
    if target_coverage is not None:
        scene_reference["target_coverage"] = _validate_target_coverage_contract(
            target_coverage,
            expected_field_identity_sha256=field_identity,
            expected_support_bounds=expected_extent,
            expected_axes_swapped=(
                orientation.get("layout_axes_swapped_from_requested_room")
            ),
        )
    return PpfdHeatmapViewerArtifacts(
        metadata=metadata_artifact,
        scalar_field=scalar_artifact,
        scene_reference=scene_reference,
    )


def _validate_target_coverage_contract(
    value: object,
    *,
    expected_field_identity_sha256: str,
    expected_support_bounds: Mapping[str, float],
    expected_axes_swapped: object,
) -> dict[str, object]:
    contract = _mapping(value, "Target Coverage contract")
    if set(contract) != {
        "availability",
        "canonical_leaf_geometry",
        "deviation_formula",
        "palette",
        "parent_source_field_identity_sha256",
        "reference",
        "sampling",
        "schema_id",
        "schema_version",
        "scientific_limitation",
        "system_id",
        "target_band_deviation",
        "target_classification_basis",
        "target_classification_source",
        "tolerance_ppfd_umol_m2_s",
        "unavailable_reason_code",
    }:
        raise ValueError("Target Coverage contract inventory is incompatible.")
    availability = contract.get("availability")
    unavailable_reason = contract.get("unavailable_reason_code")
    if (
        contract.get("schema_id") != "fspm-optics.viewer-target-coverage"
        or contract.get("schema_version") != 1
        or availability not in {"available", "unavailable"}
        or (
            availability == "available" and unavailable_reason is not None
        )
        or (
            availability == "unavailable"
            and unavailable_reason
            != "leaf_representative_position_outside_stage_a_support"
        )
        or contract.get("target_classification_basis")
        != "canopy_plane_equivalent_incident_ppfd"
        or contract.get("target_classification_source")
        != "interpolated_runtime_ppfd_map"
        or contract.get("scientific_limitation")
        != (
            "Baseline canopy-plane coverage evaluated at authenticated leaf "
            "locations—not measured leaf incident PAR."
        )
        or contract.get("deviation_formula")
        != "(coverage_ppfd - reference_ppfd) / tolerance_ppfd"
        or contract.get("parent_source_field_identity_sha256")
        != expected_field_identity_sha256
    ):
        raise ValueError("Target Coverage identity is incompatible.")

    system_id = contract.get("system_id")
    reference = _mapping(contract.get("reference"), "Target Coverage reference")
    tolerance = _finite_number(
        contract.get("tolerance_ppfd_umol_m2_s"),
        "Target Coverage tolerance",
    )
    reference_ppfd = _finite_number(
        reference.get("ppfd_umol_m2_s"), "Target Coverage reference PPFD"
    )
    source = reference.get("source")
    policy_mode = reference.get("policy_mode")
    if (
        set(reference) != {"policy_mode", "ppfd_umol_m2_s", "source"}
        or system_id not in {"proposed", "conventional", "hps"}
        or tolerance <= 0.0
        or reference_ppfd < 0.0
        or (reference_ppfd == 0.0 and system_id == "hps")
        or policy_mode not in {"automatic", "override"}
        or (
            policy_mode == "override"
            and source != "authenticated_fspm_override"
        )
        or (
            policy_mode == "automatic"
            and system_id == "hps"
            and source != "achieved_stage_a_baseline_mean"
        )
        or (
            policy_mode == "automatic"
            and system_id != "hps"
            and source != "requested_lighting_target"
        )
    ):
        raise ValueError("Target Coverage reference policy is incompatible.")

    target_band = _mapping(
        contract.get("target_band_deviation"), "Target Coverage target band"
    )
    if dict(target_band) != {
        "minimum": -1.0,
        "maximum": 1.0,
        "bounds": "inclusive",
    }:
        raise ValueError("Target Coverage target band is incompatible.")
    palette = _mapping(contract.get("palette"), "Target Coverage palette")
    expected_anchors = [
        {"deviation": -4.0, "color_name": "blue", "srgb_hex": "#2563EB"},
        {"deviation": -2.0, "color_name": "cyan", "srgb_hex": "#06B6D4"},
        {"deviation": -1.5, "color_name": "teal", "srgb_hex": "#14B8A6"},
        {"deviation": -1.0, "color_name": "green", "srgb_hex": "#22C55E"},
        {"deviation": 1.0, "color_name": "green", "srgb_hex": "#22C55E"},
        {
            "deviation": 2.0,
            "color_name": "yellow-green",
            "srgb_hex": "#A3E635",
        },
        {
            "deviation": 4.0,
            "color_name": "yellow-orange",
            "srgb_hex": "#F59E0B",
        },
        {"deviation": 6.0, "color_name": "red", "srgb_hex": "#DC2626"},
    ]
    if dict(palette) != {
        "palette_id": "target-coverage-deviation-8-anchor-v1",
        "continuous_interpolation": True,
        "clamp_below_deviation": -4.0,
        "clamp_above_deviation": 6.0,
        "anchors": expected_anchors,
    }:
        raise ValueError("Target Coverage palette is incompatible.")

    sampling = _mapping(contract.get("sampling"), "Target Coverage sampling")
    support = _mapping(
        sampling.get("support_bounds_m"), "Target Coverage support"
    )
    axes_swapped = sampling.get("axes_swapped_from_requested_room")
    expected_mapping = {"x": "aligned_x", "y": "aligned_y"}
    if (
        set(sampling)
        != {
            "axes_swapped_from_requested_room",
            "coordinate_transform_policy_id",
            "edge_band_behavior",
            "field_sampling_mapping",
            "interpolation_policy_id",
            "method",
            "outside_behavior",
            "support_bounds_m",
            "supported_extent",
        }
        or sampling.get("interpolation_policy_id")
        != "stage_a_regular_grid_bilinear_half_cell_clamp_v1"
        or sampling.get("method") != "manual_four_cell_bilinear"
        or sampling.get("supported_extent")
        != "sample-center rectangle expanded by half a grid step"
        or sampling.get("edge_band_behavior") != "clamp to nearest center"
        or sampling.get("outside_behavior") != "target coverage unavailable"
        or sampling.get("coordinate_transform_policy_id")
        != "requested_room_to_long_axis_x_rigid_rotation_v1"
        or not isinstance(axes_swapped, bool)
        or axes_swapped is not expected_axes_swapped
        or sampling.get("field_sampling_mapping") != expected_mapping
        or dict(support) != {**dict(expected_support_bounds), "bounds": "inclusive"}
    ):
        raise ValueError("Target Coverage interpolation policy is incompatible.")

    geometry = _mapping(
        contract.get("canonical_leaf_geometry"),
        "Target Coverage canonical leaf geometry",
    )
    centroids = geometry.get("representative_centroids_simulation_xy_m")
    leaf_ids = geometry.get("canonical_leaf_ids")
    if (
        set(geometry)
        != {
            "canonical_leaf_count",
            "canonical_leaf_ids",
            "canonical_topology_sha256",
            "centroid_policy_id",
            "displayed_leaf_ordering",
            "instance_translation_source",
            "leaf_geometry_identity_sha256",
            "leaf_ordering",
            "representative_centroids_simulation_xy_m",
        }
        or geometry.get("centroid_policy_id")
        != "one_sided_triangle_area_weighted_leaf_centroid_v1"
        or geometry.get("leaf_ordering") != "canonical-leaf order"
        or geometry.get("displayed_leaf_ordering")
        != "plant-major; canonical-leaf order"
        or geometry.get("canonical_leaf_count") != 12
        or not isinstance(leaf_ids, list)
        or len(leaf_ids) != 12
        or any(not isinstance(item, str) or not item for item in leaf_ids)
        or len(set(leaf_ids)) != 12
        or not isinstance(centroids, list)
        or len(centroids) != 12
        or any(
            not isinstance(item, list)
            or len(item) != 2
            or any(
                isinstance(component, bool)
                or not isinstance(component, int | float)
                or not math.isfinite(float(component))
                for component in item
            )
            for item in centroids
        )
        or not _valid_sha256(geometry.get("canonical_topology_sha256"))
        or not _valid_sha256(geometry.get("leaf_geometry_identity_sha256"))
        or geometry.get("instance_translation_source")
        != "scene.plant_instances.instance_translations"
    ):
        raise ValueError("Target Coverage leaf geometry is incompatible.")
    return json.loads(json.dumps(contract, allow_nan=False))


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is incompatible.")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} is incompatible.")
    return value


def _finite_number(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{label} is incompatible.")
    return float(value)


def _finite_axis(value: object, count: int, label: str) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"PPFD heatmap {label} centers are incompatible.")
    return tuple(
        _finite_number(item, f"PPFD heatmap {label} center") for item in value
    )


def _validate_regular_axis(values: Sequence[float], label: str) -> None:
    differences = tuple(right - left for left, right in zip(values, values[1:]))
    if (
        not differences
        or any(value <= 0.0 for value in differences)
        or any(
            not math.isclose(value, differences[0], rel_tol=1e-10, abs_tol=1e-12)
            for value in differences[1:]
        )
    ):
        raise ValueError(f"PPFD heatmap {label} centers are not a regular axis.")


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_run_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )
