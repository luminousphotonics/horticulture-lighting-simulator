from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
import zlib

import numpy as np
import pytest

from fspm_optics.application.visualization import (
    COMPARATOR_OVERLAY_COLOR_SRGB_8BIT,
    HEATMAP_HEIGHT_PX,
    HEATMAP_GRIDLINE_COLOR_SRGB_8BIT,
    HEATMAP_GRIDLINE_POLICY_ID,
    HEATMAP_WIDTH_PX,
    OVERLAY_STROKE_SCALE,
    SCATTER_FLOOR_PADDING_RATIO,
    SCATTER_RECORD_STRIDE_BYTES,
    VisualizationReference,
    VisualizationValidationError,
    annotation_indices,
    build_scatter_vertical_display_transform,
    build_ppfd_visualization_artifacts,
    detect_regular_grid,
    effective_overlay_stroke_width,
    heatmap_cell_boundary_pixel_positions,
    heatmap_gridline_policy_metadata,
    overlay_style_policy_metadata,
    reference_plane_field_identity,
)
from fspm_optics.fixtures.smd.positions import SmdLayout, generate_proposed_led_layout
from fspm_optics.layout.overlay import AuthoritativeOverlayPlan, OverlayRectangle
from fspm_optics.transport.scalar_ppfd import PpfdMapSample
from fspm_optics.viewer.ppfd_heatmap import bind_ppfd_heatmap_viewer_artifacts


RUN_ID = "d" * 32


def _decode_png_rgb(data: bytes) -> np.ndarray:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    width = height = 0
    compressed = bytearray()
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height = struct.unpack(">II", payload[:8])
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
    raw = zlib.decompress(bytes(compressed))
    stride = width * 3
    rows = []
    for row in range(height):
        start = row * (stride + 1)
        assert raw[start] == 0
        rows.append(np.frombuffer(raw[start + 1 : start + 1 + stride], dtype=np.uint8))
    return np.stack(rows).reshape((height, width, 3))


def _samples() -> tuple[PpfdMapSample, ...]:
    return (
        PpfdMapSample(1.0, 2.0, 0.005, 40.0),
        PpfdMapSample(-1.0, -2.0, 0.005, 10.0),
        PpfdMapSample(1.0, -2.0, 0.005, 20.0),
        PpfdMapSample(-1.0, 2.0, 0.005, 30.0),
    )


def _layout_identity() -> tuple[SmdLayout, dict[str, object]]:
    layout = generate_proposed_led_layout(10.0, 10.0)
    identity: dict[str, object] = {
        "room_length_m": layout.room_length_m,
        "room_width_m": layout.room_width_m,
        "pitch_x_m": layout.pitch_x_m,
        "pitch_y_m": layout.pitch_y_m,
        "axes_swapped": layout.axes_swapped,
        "topology": {
            "control_zone_count": layout.control_zone_count,
            "square_tile_count": layout.topology.square_tile_count,
        },
        "modules": [
            {
                "module_index": module.module_index,
                "x_m": module.x_m,
                "y_m": module.y_m,
                "z_m": module.z_m,
                "control_zone_index": module.control_zone_index,
            }
            for module in layout.modules
        ],
    }
    return layout, identity


@pytest.fixture(scope="module")
def artifact_set():
    layout, identity = _layout_identity()
    return build_ppfd_visualization_artifacts(
        run_id=RUN_ID,
        samples=_samples(),
        layout=layout,
        layout_identity=identity,
        requested_target_ppfd_umol_m2_s=1000.0,
    )


def test_regular_grid_detection_reshape_and_orientation_are_explicit() -> None:
    grid = detect_regular_grid(_samples())

    assert grid.x_centers_m == (-1.0, 1.0)
    assert grid.y_centers_m == (-2.0, 2.0)
    assert grid.extent == (-2.0, 2.0, -4.0, 4.0)
    assert grid.values.tolist() == [[10.0, 20.0], [30.0, 40.0]]


def test_irregular_or_incomplete_grid_is_rejected_without_interpolation() -> None:
    with pytest.raises(VisualizationValidationError, match="interpolation is disabled"):
        detect_regular_grid(_samples()[:-1])


def test_source_identity_and_sample_count_are_shared_by_all_derivatives(
    artifact_set,
) -> None:
    metadata = artifact_set.metadata
    identity = reference_plane_field_identity(_samples())

    assert artifact_set.field_sha256 == identity
    assert metadata["field"]["identity_sha256"] == identity
    assert metadata["field"]["sample_count"] == len(_samples())
    assert metadata["scatter"]["count"] == len(_samples())
    assert metadata["grid"]["interpolation"] == {
        "applied": False,
        "method": None,
        "resolution": None,
        "fill_behavior": None,
        "original_sample_identity_sha256": identity,
    }
    artifacts = {artifact.filename: artifact for artifact in artifact_set.artifacts}
    viewer = bind_ppfd_heatmap_viewer_artifacts(
        metadata_bytes=artifacts["visualization.json"].data,
        scatter_bytes=artifacts["ppfd-scatter.f32le.bin"].data,
        expected_field_identity_sha256=identity,
    )
    assert viewer.metadata.data == artifacts["visualization.json"].data
    assert viewer.scalar_field.data == artifacts["ppfd-scatter.f32le.bin"].data
    assert viewer.scene_reference["source_field_identity_sha256"] == identity
    assert viewer.scene_reference["interpolation_policy_id"] == (
        "bilinear_scalar_edge_clamp_v1"
    )


def test_thirty_by_fifty_foot_receiver_grid_binds_for_viewer_publication() -> None:
    x_centers = tuple((index - 30) * 0.25 for index in range(61))
    y_centers = tuple((index - 18) * 0.25 for index in range(37))
    samples = tuple(
        PpfdMapSample(x_m, y_m, 0.005, 1000.0)
        for y_m in y_centers
        for x_m in x_centers
    )
    identity = reference_plane_field_identity(samples)
    layout, layout_identity = _layout_identity()
    artifacts = build_ppfd_visualization_artifacts(
        run_id=RUN_ID,
        samples=samples,
        layout=layout,
        layout_identity=layout_identity,
        requested_target_ppfd_umol_m2_s=1000.0,
    )
    by_name = {artifact.filename: artifact.data for artifact in artifacts.artifacts}

    viewer = bind_ppfd_heatmap_viewer_artifacts(
        metadata_bytes=by_name["visualization.json"],
        scatter_bytes=by_name["ppfd-scatter.f32le.bin"],
        expected_field_identity_sha256=identity,
    )

    assert viewer.scene_reference["grid"]["width"] == 61
    assert viewer.scene_reference["grid"]["height"] == 37
    assert viewer.scene_reference["scalar_field"]["count"] == 2_257
    assert viewer.scene_reference["scalar_field"]["byte_length"] == 27_084


def test_extent_origin_aspect_units_and_color_metadata_are_deterministic(
    artifact_set,
) -> None:
    metadata = artifact_set.metadata
    assert metadata["grid"]["extent_m"] == {
        "x_min": -2.0,
        "x_max": 2.0,
        "y_min": -4.0,
        "y_max": 4.0,
    }
    assert metadata["grid"]["image_origin"] == "lower"
    assert metadata["grid"]["aspect"] == "equal"
    assert metadata["display"]["color_limits_ppfd_umol_m2_s"] == [800.0, 1200.0]
    assert metadata["display"]["requested_lighting_target_ppfd_umol_m2_s"] == 1000.0
    assert metadata["display"]["color_limit_policy"] == "requested_lighting_target_plus_or_minus_200"
    assert metadata["field"]["observed_minimum_ppfd_umol_m2_s"] == 10.0
    assert metadata["field"]["observed_maximum_ppfd_umol_m2_s"] == 40.0
    assert metadata["display"]["normalization"]["ppfd_values_modified"] is False
    assert metadata["display"]["normalization"]["under_range_behavior"] == "saturate_to_colormap_minimum_color"
    assert metadata["display"]["normalization"]["over_range_behavior"] == "saturate_to_colormap_maximum_color"
    assert metadata["display"]["normalization"]["points_discarded"] is False
    assert metadata["display"]["visible_legend"] == {
        "minimum_ppfd_umol_m2_s": 800.0,
        "center_ppfd_umol_m2_s": 1000.0,
        "maximum_ppfd_umol_m2_s": 1200.0,
        "units": "umol/m^2/s",
    }
    assert metadata["display"]["color_limit_consumers"] == [
        "ppfd-heatmap.png cells and visible colorbar",
        "ppfd-heatmap-overlay.png cells and visible colorbar",
        "ppfd scatter point colors and visible legend",
    ]
    assert metadata["display"]["rasterization"]["width_px"] == HEATMAP_WIDTH_PX
    assert metadata["display"]["rasterization"]["height_px"] == HEATMAP_HEIGHT_PX


def test_cell_gridline_policy_uses_exact_regular_grid_boundaries_for_both_maps(
    artifact_set,
) -> None:
    grid = detect_regular_grid(_samples())
    expected = heatmap_gridline_policy_metadata(grid)
    pixels = heatmap_cell_boundary_pixel_positions(grid)
    policy = artifact_set.metadata["grid"]["cell_gridlines"]

    assert policy == expected
    assert policy["policy_id"] == HEATMAP_GRIDLINE_POLICY_ID
    assert policy["applies_to"] == [
        "ppfd-heatmap.png",
        "ppfd-heatmap-overlay.png",
    ]
    assert policy["x_boundaries_m"] == list(grid.x_edges_m)
    assert policy["y_boundaries_m"] == list(grid.y_edges_m)
    assert policy["x_boundary_pixels"] == list(pixels["x"])
    assert policy["y_boundary_pixels"] == list(pixels["y"])
    assert policy["value_transform"] is False
    assert policy["orientation_transform"] is False
    assert grid.values.tolist() == [[10.0, 20.0], [30.0, 40.0]]
    assert grid.extent == (-2.0, 2.0, -4.0, 4.0)
    assert annotation_indices(grid) == (
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
    )
    assert policy["render_order"] == [
        "heatmap_cells",
        "cell_boundary_gridlines",
        "plain_annotations_if_enabled",
        "plot_border",
        "authoritative_overlay_if_present",
    ]
    assert artifact_set.metadata["display"]["rasterization"][
        "cell_gridlines_policy_id"
    ] == HEATMAP_GRIDLINE_POLICY_ID


def test_authoritative_overlay_uses_layout_identity_modules_and_control_zones(
    artifact_set,
) -> None:
    layout, identity = _layout_identity()
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    overlay = artifact_set.metadata["overlay"]

    assert overlay["coordinate_source"] == "run manifest layout.modules"
    assert overlay["layout_identity_sha256"] == hashlib.sha256(encoded).hexdigest()
    assert overlay["module_count"] == len(layout.modules)  # type: ignore[union-attr]
    assert overlay["control_zone_count"] == layout.control_zone_count  # type: ignore[union-attr]
    assert overlay["module_control_zone_coloring"] is True
    assert overlay["fixture_policy_id"] == layout.fixture_policy_id
    assert overlay["fixture_count"] == len(layout.fixtures)
    assert overlay["control_zone_membership_generates_connectors"] is False
    assert overlay["fixtures"] == [
        {
            "fixture_id": fixture.fixture_id,
            "fixture_type": fixture.fixture_type,
            "display_fixture_type": fixture.display_fixture_type,
            "source_orientation": fixture.source_orientation,
            "orientation_degrees": fixture.orientation_degrees,
            "member_module_indices": list(fixture.member_module_indices),
            "connectors": [
                {
                    "start_module_index": connector.start_module_index,
                    "end_module_index": connector.end_module_index,
                }
                for connector in fixture.connectors
            ],
        }
        for fixture in layout.fixtures
    ]
    assert overlay["image_coordinate_inference"] is False


def test_proposed_overlay_explicitly_preserves_phase27d_stroke_semantics() -> None:
    layout, _identity = _layout_identity()
    plan = layout.authoritative_overlay_plan()

    assert plan.rectangles
    assert plan.lines
    assert {
        (rectangle.stroke_color_role, rectangle.stroke_width_px)
        for rectangle in plan.rectangles
    } == {("color_group", 2)}
    assert {
        (line.primitive_kind, line.stroke_color_role, line.stroke_width_px)
        for line in plan.lines
    } == {("alignment_lattice_link", "plot_border", 1)}
    serialized = plan.to_dict()
    assert {
        (item["stroke"]["color_role"], item["stroke"]["width_px"])
        for item in serialized["rectangles"]
    } == {("color_group", 2)}
    assert {
        (item["stroke"]["color_role"], item["stroke"]["width_px"])
        for item in serialized["lines"]
    } == {("plot_border", 1)}
    assert serialized["geometry_inference"] is False
    style = overlay_style_policy_metadata("proposed")
    assert style["color"]["mode"] == "authoritative_primitive_roles"
    assert style["color"]["rectangle_role"] == "control_zone_color_group"
    assert style["stroke"]["scale"] == OVERLAY_STROKE_SCALE == 1.5
    assert effective_overlay_stroke_width(2) == 3
    assert effective_overlay_stroke_width(1) == 2


@pytest.mark.parametrize("system_id", ["conventional", "hps"])
def test_comparator_overlays_use_exact_red_above_shared_gridlines(
    system_id: str,
) -> None:
    plan = AuthoritativeOverlayPlan(
        system_id=system_id,
        policy_id=f"{system_id}-test-overlay",
        coordinate_source="test_authoritative_geometry",
        room_length_m=4.0,
        room_width_m=8.0,
        axes_swapped=False,
        rectangles=(
            OverlayRectangle(
                primitive_id="fixture-edge-on-gridline",
                fixture_id="fixture-0",
                primitive_kind="fixture",
                center_x_m=0.5,
                center_y_m=0.0,
                width_x_m=1.0,
                height_y_m=6.0,
                orientation_degrees=0.0,
                color_group_index=4,
            ),
        ),
        fixture_metadata=({"fixture_id": "fixture-0"},),
    )
    reference = (
        VisualizationReference.achieved_baseline_mean(25.0)
        if system_id == "hps"
        else VisualizationReference.requested_target(1000.0)
    )
    artifacts = build_ppfd_visualization_artifacts(
        run_id=RUN_ID,
        samples=_samples(),
        layout=None,
        layout_identity={"system_id": system_id},
        overlay_plan=plan,
        reference=reference,
    )
    by_name = {item.filename: item.data for item in artifacts.artifacts}
    plain = _decode_png_rgb(by_name["ppfd-heatmap.png"])
    overlay = _decode_png_rgb(by_name["ppfd-heatmap-overlay.png"])
    grid = detect_regular_grid(_samples())
    boundary_pixels = heatmap_cell_boundary_pixel_positions(grid)
    x = boundary_pixels["x"][1]
    ordered_y = sorted(boundary_pixels["y"])
    y = (ordered_y[0] + ordered_y[1]) // 2

    assert tuple(plain[y, x]) == HEATMAP_GRIDLINE_COLOR_SRGB_8BIT
    assert tuple(overlay[y, x]) == COMPARATOR_OVERLAY_COLOR_SRGB_8BIT
    style = artifacts.metadata["overlay"]["style_policy"]
    assert style == overlay_style_policy_metadata(system_id)
    assert style["color"]["fixed_color_hex"] == "#ff2d2d"
    assert style["color"]["fixed_color_srgb_8bit"] == [255, 45, 45]
    assert style["color"]["primitive_color_groups_ignored"] is True
    assert style["stroke"]["scale"] == 1.5
    assert style["z_order"] == "above_heatmap_cells_and_cell_boundary_gridlines"


def test_inventory_png_and_scatter_hashes_lengths_and_finite_values(artifact_set) -> None:
    artifacts = {artifact.filename: artifact for artifact in artifact_set.artifacts}
    inventory = artifact_set.inventory()
    for filename, artifact in artifacts.items():
        assert inventory[filename] == {
            "sha256": hashlib.sha256(artifact.data).hexdigest(),
            "byte_length": len(artifact.data),
        }
    for filename in ("ppfd-heatmap.png", "ppfd-heatmap-overlay.png"):
        assert artifacts[filename].data.startswith(b"\x89PNG\r\n\x1a\n")
    assert "ppfd-scatter-viewer/presentation-export.js" in artifacts
    assert b"buildPresentationSvg" in artifacts[
        "ppfd-scatter-viewer/presentation-export.js"
    ].data
    assert b'from "./presentation-export.js"' in artifacts[
        "ppfd-scatter-viewer/main.js"
    ].data
    scatter = artifacts["ppfd-scatter.f32le.bin"].data
    assert len(scatter) == len(_samples()) * SCATTER_RECORD_STRIDE_BYTES
    values = struct.unpack(f"<{len(_samples()) * 3}f", scatter)
    assert all(math.isfinite(value) for value in values)
    assert np.asarray(values).reshape((-1, 3))[:, 2].tolist() == [40.0, 10.0, 20.0, 30.0]
    assert artifact_set.metadata["scatter"]["sha256"] == hashlib.sha256(scatter).hexdigest()
    vertical = artifact_set.metadata["scatter"]["vertical_display_transform"]
    assert vertical["policy_id"] == "requested_target_window_ppfd_to_room_span_v1"
    assert vertical["requested_lighting_target_ppfd_umol_m2_s"] == 1000.0
    assert vertical["declared_vmin_ppfd_umol_m2_s"] == 800.0
    assert vertical["declared_vmax_ppfd_umol_m2_s"] == 1200.0
    assert vertical["raw_minimum_ppfd_umol_m2_s"] == 10.0
    assert vertical["raw_maximum_ppfd_umol_m2_s"] == 40.0
    assert vertical["horizontal_reference_span_m"] == 4.0
    assert vertical["display_vertical_span"] == pytest.approx(2.88)
    assert vertical["scale"] == pytest.approx(0.0072)
    assert vertical["offset"] == pytest.approx(-5.76)
    assert vertical["rendering_only"] is True
    assert vertical["declared_z_range_matches_color_limits"] is True
    assert vertical["color_and_vertical_normalizations_are_separate"] is True
    assert vertical["samples_clipped_corrected_or_removed"] is False
    assert vertical["observed_range_normalization"] is False
    assert vertical["point_cloud_translation_applied"] is False
    assert vertical["low_variation_field_stretch"] is False
    assert vertical["floor_padding_ratio_to_horizontal_span"] == (
        SCATTER_FLOOR_PADDING_RATIO
    )
    assert vertical["scene_floor_height"] < vertical[
        "actual_transformed_minimum_height"
    ]
    assert vertical["camera_frame_lower_height"] == vertical["scene_floor_height"]
    assert vertical["camera_frame_union"] == [
        "scene_floor",
        "declared_reference_band",
        "actual_transformed_samples",
    ]
    assert vertical["out_of_range"] == {
        "behavior": "linear_extrapolation_beyond_declared_display_band",
        "clamped": False,
        "discarded": False,
    }


def test_scatter_vertical_transform_maps_raw_range_linearly_and_inverts() -> None:
    transform = build_scatter_vertical_display_transform(
        requested_target_ppfd_umol_m2_s=1000.0,
        raw_minimum_ppfd_umol_m2_s=800.0,
        raw_maximum_ppfd_umol_m2_s=1200.0,
        horizontal_reference_span_m=4.0,
    )

    assert transform.display_vertical_span == pytest.approx(2.88)
    assert transform.forward(800.0) == pytest.approx(0.0)
    assert transform.forward(1200.0) == pytest.approx(2.88)
    middle_height = transform.forward(1000.0)
    assert middle_height == pytest.approx(1.44)
    assert transform.inverse(middle_height) == pytest.approx(1000.0)
    assert transform.forward(948.31) / transform.display_vertical_span == pytest.approx(
        0.370775
    )
    assert transform.forward(1046.17) / transform.display_vertical_span == pytest.approx(
        0.615425
    )


def test_constant_target_field_is_flat_at_declared_midpoint() -> None:
    transform = build_scatter_vertical_display_transform(
        requested_target_ppfd_umol_m2_s=1000.0,
        raw_minimum_ppfd_umol_m2_s=1000.0,
        raw_maximum_ppfd_umol_m2_s=1000.0,
        horizontal_reference_span_m=3.0,
    )

    assert transform.display_vertical_span == pytest.approx(2.16)
    assert transform.scale == pytest.approx(2.16 / 400.0)
    assert math.isfinite(transform.offset)
    assert transform.forward(1000.0) == pytest.approx(1.08)
    assert transform.inverse(1.08) == pytest.approx(1000.0)


def test_observed_extrema_do_not_determine_scale_offset_or_shared_value_height() -> None:
    first = build_scatter_vertical_display_transform(
        requested_target_ppfd_umol_m2_s=1000.0,
        raw_minimum_ppfd_umol_m2_s=948.31,
        raw_maximum_ppfd_umol_m2_s=1046.17,
        horizontal_reference_span_m=4.0,
    )
    second = build_scatter_vertical_display_transform(
        requested_target_ppfd_umol_m2_s=1000.0,
        raw_minimum_ppfd_umol_m2_s=700.0,
        raw_maximum_ppfd_umol_m2_s=1300.0,
        horizontal_reference_span_m=4.0,
    )

    assert first.scale == second.scale
    assert first.offset == second.offset
    assert first.forward(975.0) == second.forward(975.0)
    observed_fraction = (
        first.forward(1046.17) - first.forward(948.31)
    ) / first.display_vertical_span
    assert observed_fraction == pytest.approx(0.24465)


def test_all_system_references_retain_the_same_target_centered_height_scale() -> None:
    proposed = build_scatter_vertical_display_transform(
        requested_target_ppfd_umol_m2_s=1000.0,
        raw_minimum_ppfd_umol_m2_s=900.0,
        raw_maximum_ppfd_umol_m2_s=1100.0,
        horizontal_reference_span_m=4.0,
    )
    conventional = build_scatter_vertical_display_transform(
        requested_target_ppfd_umol_m2_s=1000.0,
        raw_minimum_ppfd_umol_m2_s=700.0,
        raw_maximum_ppfd_umol_m2_s=1300.0,
        horizontal_reference_span_m=4.0,
    )
    hps = build_scatter_vertical_display_transform(
        requested_target_ppfd_umol_m2_s=1000.0,
        raw_minimum_ppfd_umol_m2_s=200.0,
        raw_maximum_ppfd_umol_m2_s=1800.0,
        horizontal_reference_span_m=4.0,
        reference_kind="achieved_final_baseline_mean",
        policy_id="achieved_baseline_window_ppfd_to_room_span_v1",
    )

    for transform in (proposed, conventional, hps):
        assert transform.display_vertical_span == pytest.approx(4.0 * 0.72)
        assert transform.scale == pytest.approx((4.0 * 0.72) / 400.0)
        assert transform.forward(800.0) == pytest.approx(0.0)
        assert transform.forward(1000.0) == pytest.approx(1.44)
        assert transform.forward(1200.0) == pytest.approx(2.88)
    assert (
        hps.actual_transformed_maximum_height
        - hps.actual_transformed_minimum_height
    ) > (
        conventional.actual_transformed_maximum_height
        - conventional.actual_transformed_minimum_height
    ) > (
        proposed.actual_transformed_maximum_height
        - proposed.actual_transformed_minimum_height
    )


def test_out_of_range_values_use_a_padded_floor_and_expand_camera_frame() -> None:
    transform = build_scatter_vertical_display_transform(
        requested_target_ppfd_umol_m2_s=1000.0,
        raw_minimum_ppfd_umol_m2_s=700.0,
        raw_maximum_ppfd_umol_m2_s=1300.0,
        horizontal_reference_span_m=4.0,
    )
    metadata = transform.to_metadata()

    assert transform.forward(700.0) < transform.display_lower_height
    assert transform.forward(1300.0) > transform.display_upper_height
    expected_floor = min(0.0, transform.forward(700.0)) - (4.0 * 0.04)
    assert metadata["scene_floor_height"] == pytest.approx(expected_floor)
    assert metadata["scene_floor_height"] < transform.forward(700.0)
    assert metadata["floor_anchored_helpers"] == ["grid", "axes"]
    assert metadata["camera_frame_lower_height"] == pytest.approx(
        expected_floor
    )
    assert metadata["camera_frame_upper_height"] == pytest.approx(
        transform.forward(1300.0)
    )
    assert metadata["out_of_range"] == {
        "behavior": "linear_extrapolation_beyond_declared_display_band",
        "clamped": False,
        "discarded": False,
    }
    assert metadata["observed_range_normalization"] is False
    assert metadata["point_cloud_translation_applied"] is False
    assert metadata["low_variation_field_stretch"] is False


def test_target_window_change_does_not_change_raw_scatter_or_field_hash() -> None:
    layout, identity = _layout_identity()
    other_target = build_ppfd_visualization_artifacts(
        run_id=RUN_ID,
        samples=_samples(),
        layout=layout,
        layout_identity=identity,
        requested_target_ppfd_umol_m2_s=1500.0,
    )
    original = build_ppfd_visualization_artifacts(
        run_id=RUN_ID,
        samples=_samples(),
        layout=layout,
        layout_identity=identity,
        requested_target_ppfd_umol_m2_s=1000.0,
    )
    original_files = {item.filename: item.data for item in original.artifacts}
    other_files = {item.filename: item.data for item in other_target.artifacts}

    assert original.field_sha256 == other_target.field_sha256
    assert original_files["ppfd-scatter.f32le.bin"] == other_files[
        "ppfd-scatter.f32le.bin"
    ]
    assert original.metadata["scatter"]["sha256"] == other_target.metadata[
        "scatter"
    ]["sha256"]
    assert original.metadata["scatter"]["vertical_display_transform"] != (
        other_target.metadata["scatter"]["vertical_display_transform"]
    )
    assert other_target.metadata["display"]["color_limits_ppfd_umol_m2_s"] == [
        1300.0,
        1700.0,
    ]


def test_plain_annotations_use_center_phased_staggered_raw_indices() -> None:
    samples = tuple(
        PpfdMapSample(float(column), float(row), 0.005, row * 10.0 + column + 0.5)
        for row in range(21)
        for column in range(4)
    )
    grid = detect_regular_grid(samples)
    selected = annotation_indices(grid)

    assert tuple(column for row, column in selected if row == 0) == (0, 2)
    assert tuple(column for row, column in selected if row == 1) == (1, 3)
    assert tuple(column for row, column in selected if row == 20) == (0, 2)
    for row, column in selected:
        raw = float(grid.values[row, column])
        assert format(raw, ".0f") == format(row * 10.0 + column + 0.5, ".0f")


def test_plain_annotation_metadata_excludes_overlay_annotations(artifact_set) -> None:
    annotations = artifact_set.metadata["annotations"]
    assert annotations["plain_heatmap"] is True
    assert annotations["overlay_heatmap"] is False
    assert annotations["row_behavior"] == "annotation columns alternate on successive rows"
    assert annotations["format"] == "fixed_point_0_decimal_places"
    assert annotations["value_transform"] is False


def test_visualization_sources_have_no_physics_execution_or_salvage_coupling() -> None:
    repository = Path(__file__).parents[1]
    source = (
        repository / "src/fspm_optics/application/visualization.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        ".salvage_source",
        "horticulture-lighting-simulator",
        "LocalRunner",
        "CommandSpec",
        "subprocess",
        "docker",
        "precomputed",
        "rtrace",
        "oconv",
        "scale_ppfd_map",
        "apply_target_control",
        "griddata",
        "centroid",
        "modules_by_zone",
    )
    assert not any(value.lower() in source.lower() for value in forbidden)
    assert "layout.control_zones" not in source
    assert "symmetr" not in source.lower().replace('"symmetrization": false', "")


def test_web_ui_only_activates_visuals_from_succeeded_results_and_supports_full_size() -> None:
    repository = Path(__file__).parents[1]
    html = (repository / "src/fspm_optics/resources/web/index.html").read_text(encoding="utf-8")
    javascript = (repository / "src/fspm_optics/resources/web/app.js").read_text(encoding="utf-8")

    assert 'id="heatmap-dialog"' in html
    assert 'id="heatmap-open"' in html and 'id="overlay-open"' in html
    assert "Validated PPFD scatter" in html
    assert "await loadResults(body.result)" in javascript
    assert "await loadVisualizations(result, metrics.visualization)" in javascript
    assert "resetVisualizations();" in javascript
    assert "heatmapDialog.showModal()" in javascript


def test_scatter_viewer_anchors_helpers_to_floor_without_moving_samples() -> None:
    repository = Path(__file__).parents[1]
    javascript = (
        repository / "src/fspm_optics/resources/scatter/main.js"
    ).read_text(encoding="utf-8")

    assert "positions[index * 3] = points.x[index];" in javascript
    assert "positions[index * 3 + 1] = points.y[index];" in javascript
    assert (
        "positions[index * 3 + 2] = displayHeight(points.ppfd[index], vertical);"
        in javascript
    )
    assert "grid.position.z = sceneFloor;" in javascript
    assert "axes.position.set(xMin, yMin, sceneFloor);" in javascript
    assert "cloud.position" not in javascript
    assert "cloud.scale" not in javascript
    assert "const frameLower = vertical.camera_frame_lower_height;" in javascript
    assert "const frameUpper = vertical.camera_frame_upper_height;" in javascript
    assert 'controls.addEventListener("change", render);' in javascript
    assert 'window.addEventListener("resize", resize);' in javascript
    assert "await verifyHash(buffer, metadata.scatter.sha256);" in javascript
    assert "disposeCurrent();" in javascript
