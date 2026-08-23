"""Validated display derivatives for one completed reference-plane PPFD field."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import resources
import json
import math
from pathlib import Path
import struct
from typing import Mapping, Protocol, Sequence
import zlib

import numpy as np
from numpy.typing import NDArray

from fspm_optics.layout.overlay import AuthoritativeOverlayPlan
from fspm_optics.transport.basis.atomic import atomic_write_bytes
from fspm_optics.transport.scalar_ppfd import PpfdMapSample

VISUALIZATION_SCHEMA_ID = "fspm-optics.ppfd-visualization"
VISUALIZATION_SCHEMA_VERSION = 1
SCATTER_RECORD_STRIDE_BYTES = 12
SCATTER_VERTICAL_POLICY_ID = "requested_target_window_ppfd_to_room_span_v1"
TARGET_CAP_SCATTER_VERTICAL_POLICY_ID = (
    "requested_sampled_cap_window_ppfd_to_room_span_v1"
)
HPS_SCATTER_VERTICAL_POLICY_ID = "achieved_baseline_window_ppfd_to_room_span_v1"
SCATTER_VERTICAL_SPAN_RATIO = 0.72
SCATTER_FLOOR_POLICY_ID = "declared_and_observed_union_floor_v1"
SCATTER_FLOOR_PADDING_RATIO = 0.04
HEATMAP_WIDTH_PX = 1200
HEATMAP_HEIGHT_PX = 900
HEATMAP_GRIDLINE_POLICY_ID = "regular_grid_cell_boundaries_v1"
HEATMAP_GRIDLINE_COLOR_SRGB_8BIT = (142, 148, 145)
HEATMAP_GRIDLINE_WIDTH_PX = 1
OVERLAY_STYLE_POLICY_ID = "system_overlay_contrast_and_1_5x_stroke_v1"
OVERLAY_STROKE_SCALE = 1.5
COMPARATOR_OVERLAY_COLOR_SRGB_8BIT = (255, 45, 45)
_PLOT_MARGIN_PX = 48
_COLORBAR_GAP_PX = 24
_COLORBAR_WIDTH_PX = 28
_BACKGROUND = (7, 16, 13)
_PLOT_BORDER = (224, 238, 226)
_ROOM_BORDER = (255, 255, 255)
_COLORMAP_NAME = "fspm-viridis-8-linear-srgb-v1"
_COLORMAP_ANCHORS = (
    (68, 1, 84),
    (70, 50, 126),
    (54, 92, 141),
    (39, 127, 142),
    (31, 161, 135),
    (74, 193, 109),
    (160, 218, 57),
    (253, 231, 37),
)
_ZONE_COLORS = (
    (255, 255, 255),
    (255, 181, 71),
    (116, 220, 255),
    (255, 119, 168),
    (176, 255, 128),
    (199, 157, 255),
)
_VIEWER_FILES = ("index.html", "styles.css", "main.js", "presentation-export.js")
_GLYPHS = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "-": ("000", "000", "111", "000", "000"),
    ".": ("000", "000", "000", "000", "010"),
}


class VisualizationValidationError(ValueError):
    """A source field cannot be represented without violating its contracts."""


class OverlayPlanProvider(Protocol):
    """Layout-engine boundary consumed without any geometry inference."""

    def authoritative_overlay_plan(self) -> AuthoritativeOverlayPlan: ...


@dataclass(frozen=True, slots=True)
class VisualizationReference:
    """Display-only center and provenance for the shared ±200 window."""

    center_ppfd_umol_m2_s: float
    kind: str
    color_limit_policy: str
    scatter_policy_id: str

    def __post_init__(self) -> None:
        if (
            not math.isfinite(float(self.center_ppfd_umol_m2_s))
            or self.center_ppfd_umol_m2_s < 0.0
            or (
                self.center_ppfd_umol_m2_s == 0.0
                and self.kind
                not in {
                    "requested_lighting_target",
                    "requested_sampled_ppfd_cap",
                }
            )
        ):
            raise VisualizationValidationError(
                "visualization reference must be finite and non-negative; "
                "zero is supported only for a requested lighting target or cap."
            )
        supported = {
            "requested_lighting_target": (
                "requested_lighting_target_plus_or_minus_200",
                SCATTER_VERTICAL_POLICY_ID,
            ),
            "requested_sampled_ppfd_cap": (
                "requested_sampled_ppfd_cap_plus_or_minus_200",
                TARGET_CAP_SCATTER_VERTICAL_POLICY_ID,
            ),
            "achieved_final_baseline_mean": (
                "achieved_final_baseline_mean_plus_or_minus_200",
                HPS_SCATTER_VERTICAL_POLICY_ID,
            ),
        }
        if supported.get(self.kind) != (
            self.color_limit_policy,
            self.scatter_policy_id,
        ):
            raise VisualizationValidationError(
                "visualization reference policy is unsupported or contradictory."
            )

    @classmethod
    def requested_target(cls, value: float) -> "VisualizationReference":
        return cls(
            float(value),
            "requested_lighting_target",
            "requested_lighting_target_plus_or_minus_200",
            SCATTER_VERTICAL_POLICY_ID,
        )

    @classmethod
    def requested_sampled_cap(cls, value: float) -> "VisualizationReference":
        return cls(
            float(value),
            "requested_sampled_ppfd_cap",
            "requested_sampled_ppfd_cap_plus_or_minus_200",
            TARGET_CAP_SCATTER_VERTICAL_POLICY_ID,
        )

    @classmethod
    def achieved_baseline_mean(cls, value: float) -> "VisualizationReference":
        return cls(
            float(value),
            "achieved_final_baseline_mean",
            "achieved_final_baseline_mean_plus_or_minus_200",
            HPS_SCATTER_VERTICAL_POLICY_ID,
        )


@dataclass(frozen=True, slots=True)
class RegularPpfdGrid:
    """Exact regular XY grid with values reshaped as ascending Y then X."""

    x_centers_m: tuple[float, ...]
    y_centers_m: tuple[float, ...]
    z_m: float
    values: NDArray[np.float64]
    x_edges_m: tuple[float, ...]
    y_edges_m: tuple[float, ...]

    @property
    def sample_count(self) -> int:
        return len(self.x_centers_m) * len(self.y_centers_m)

    @property
    def extent(self) -> tuple[float, float, float, float]:
        return (
            self.x_edges_m[0],
            self.x_edges_m[-1],
            self.y_edges_m[0],
            self.y_edges_m[-1],
        )


@dataclass(frozen=True, slots=True)
class VisualizationArtifact:
    filename: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def byte_length(self) -> int:
        return len(self.data)


@dataclass(frozen=True, slots=True)
class VisualizationArtifactSet:
    field_sha256: str
    sample_count: int
    artifacts: tuple[VisualizationArtifact, ...]
    metadata: dict[str, object]

    def inventory(self) -> dict[str, dict[str, object]]:
        return {
            artifact.filename: {
                "sha256": artifact.sha256,
                "byte_length": artifact.byte_length,
            }
            for artifact in self.artifacts
        }


@dataclass(frozen=True, slots=True)
class ScatterVerticalDisplayTransform:
    """Invertible target-window rendering transform for raw PPFD."""

    requested_target_ppfd_umol_m2_s: float
    declared_vmin_ppfd_umol_m2_s: float
    declared_vmax_ppfd_umol_m2_s: float
    raw_minimum_ppfd_umol_m2_s: float
    raw_maximum_ppfd_umol_m2_s: float
    horizontal_reference_span_m: float
    display_vertical_span: float
    display_lower_height: float
    display_upper_height: float
    scale: float
    offset: float
    reference_kind: str = "requested_lighting_target"
    policy_id: str = SCATTER_VERTICAL_POLICY_ID

    def forward(self, raw_ppfd_umol_m2_s: float) -> float:
        return raw_ppfd_umol_m2_s * self.scale + self.offset

    def inverse(self, display_height: float) -> float:
        return (display_height - self.offset) / self.scale

    @property
    def actual_transformed_minimum_height(self) -> float:
        return self.forward(self.raw_minimum_ppfd_umol_m2_s)

    @property
    def actual_transformed_maximum_height(self) -> float:
        return self.forward(self.raw_maximum_ppfd_umol_m2_s)

    @property
    def floor_padding_height(self) -> float:
        return self.horizontal_reference_span_m * SCATTER_FLOOR_PADDING_RATIO

    @property
    def scene_floor_height(self) -> float:
        return (
            min(0.0, self.actual_transformed_minimum_height)
            - self.floor_padding_height
        )

    def to_metadata(self) -> dict[str, object]:
        metadata: dict[str, object] = {
            "policy_id": self.policy_id,
            "rendering_only": True,
            "raw_ppfd_authoritative": True,
            "input_units": "umol/m^2/s",
            "output_units": "three_world_display_units",
            "reference_kind": self.reference_kind,
            "reference_ppfd_umol_m2_s": self.requested_target_ppfd_umol_m2_s,
            "declared_vmin_ppfd_umol_m2_s": self.declared_vmin_ppfd_umol_m2_s,
            "declared_vmax_ppfd_umol_m2_s": self.declared_vmax_ppfd_umol_m2_s,
            "raw_minimum_ppfd_umol_m2_s": self.raw_minimum_ppfd_umol_m2_s,
            "raw_maximum_ppfd_umol_m2_s": self.raw_maximum_ppfd_umol_m2_s,
            "horizontal_reference_span_m": self.horizontal_reference_span_m,
            "display_vertical_span_ratio_to_horizontal": (
                SCATTER_VERTICAL_SPAN_RATIO
            ),
            "display_vertical_span": self.display_vertical_span,
            "display_lower_height": self.display_lower_height,
            "display_upper_height": self.display_upper_height,
            "scale": self.scale,
            "offset": self.offset,
            "forward_formula": "display_height = raw_ppfd * scale + offset",
            "inverse_formula": "raw_ppfd = (display_height - offset) / scale",
            "declared_z_range_matches_color_limits": True,
            "color_and_vertical_normalizations_are_separate": True,
            "samples_clipped_corrected_or_removed": False,
            "observed_range_normalization": False,
            "point_cloud_translation_applied": False,
            "low_variation_field_stretch": False,
            "out_of_range": {
                "behavior": "linear_extrapolation_beyond_declared_display_band",
                "clamped": False,
                "discarded": False,
            },
            "actual_transformed_minimum_height": (
                self.actual_transformed_minimum_height
            ),
            "actual_transformed_maximum_height": (
                self.actual_transformed_maximum_height
            ),
            "scene_floor_policy_id": SCATTER_FLOOR_POLICY_ID,
            "floor_padding_ratio_to_horizontal_span": (
                SCATTER_FLOOR_PADDING_RATIO
            ),
            "floor_padding_height": self.floor_padding_height,
            "scene_floor_height": self.scene_floor_height,
            "scene_floor_formula": (
                "min(0, actual_transformed_minimum_height) "
                "- horizontal_reference_span_m * 0.04"
            ),
            "scene_floor_strictly_below_all_samples": True,
            "floor_anchored_helpers": ["grid", "axes"],
            "camera_frame_lower_height": self.scene_floor_height,
            "camera_frame_upper_height": max(
                self.display_upper_height,
                self.actual_transformed_maximum_height,
            ),
            "camera_frame_union": [
                "scene_floor",
                "declared_reference_band",
                "actual_transformed_samples",
            ],
        }
        if self.reference_kind == "requested_lighting_target":
            metadata["requested_lighting_target_ppfd_umol_m2_s"] = (
                self.requested_target_ppfd_umol_m2_s
            )
        elif self.reference_kind == "requested_sampled_ppfd_cap":
            metadata["requested_sampled_ppfd_cap_umol_m2_s"] = (
                self.requested_target_ppfd_umol_m2_s
            )
        return metadata


def build_scatter_vertical_display_transform(
    *,
    requested_target_ppfd_umol_m2_s: float,
    raw_minimum_ppfd_umol_m2_s: float,
    raw_maximum_ppfd_umol_m2_s: float,
    horizontal_reference_span_m: float,
    reference_kind: str = "requested_lighting_target",
    policy_id: str = SCATTER_VERTICAL_POLICY_ID,
) -> ScatterVerticalDisplayTransform:
    """Declare a fixed target-window rendering transform from raw PPFD."""

    requested_target = float(requested_target_ppfd_umol_m2_s)
    raw_minimum = float(raw_minimum_ppfd_umol_m2_s)
    raw_maximum = float(raw_maximum_ppfd_umol_m2_s)
    horizontal_span_input = float(horizontal_reference_span_m)
    if (
        not math.isfinite(requested_target)
        or requested_target < 0.0
        or (
            requested_target == 0.0
            and reference_kind
            not in {
                "requested_lighting_target",
                "requested_sampled_ppfd_cap",
            }
        )
        or not math.isfinite(raw_minimum)
        or not math.isfinite(raw_maximum)
        or raw_maximum < raw_minimum
        or not math.isfinite(horizontal_span_input)
        or horizontal_span_input < 0.0
    ):
        raise VisualizationValidationError("scatter vertical transform is invalid.")
    horizontal_span = max(horizontal_span_input, 0.1)
    display_span = horizontal_span * SCATTER_VERTICAL_SPAN_RATIO
    declared_vmin = requested_target - 200.0
    declared_vmax = requested_target + 200.0
    scale = display_span / (declared_vmax - declared_vmin)
    offset = -declared_vmin * scale
    return ScatterVerticalDisplayTransform(
        requested_target_ppfd_umol_m2_s=requested_target,
        declared_vmin_ppfd_umol_m2_s=declared_vmin,
        declared_vmax_ppfd_umol_m2_s=declared_vmax,
        raw_minimum_ppfd_umol_m2_s=raw_minimum,
        raw_maximum_ppfd_umol_m2_s=raw_maximum,
        horizontal_reference_span_m=horizontal_span,
        display_vertical_span=display_span,
        display_lower_height=0.0,
        display_upper_height=display_span,
        scale=scale,
        offset=offset,
        reference_kind=reference_kind,
        policy_id=policy_id,
    )


def reference_plane_field_identity(samples: Sequence[PpfdMapSample]) -> str:
    """Hash exact Float64 XY/Z/PPFD values in authoritative sample order."""

    validated = _validated_samples(samples)
    packed = b"".join(
        struct.pack(
            "<dddd",
            sample.x_m,
            sample.y_m,
            sample.z_m,
            sample.ppfd_umol_m2_s,
        )
        for sample in validated
    )
    return hashlib.sha256(packed).hexdigest()


def detect_regular_grid(samples: Sequence[PpfdMapSample]) -> RegularPpfdGrid:
    """Validate and reshape a complete regular grid without interpolation."""

    validated = _validated_samples(samples)
    x_centers = tuple(sorted({sample.x_m for sample in validated}))
    y_centers = tuple(sorted({sample.y_m for sample in validated}))
    z_values = {sample.z_m for sample in validated}
    if len(z_values) != 1:
        raise VisualizationValidationError(
            "PPFD visualization requires one horizontal reference plane."
        )
    if len(x_centers) * len(y_centers) != len(validated):
        raise VisualizationValidationError(
            "PPFD samples do not form a complete regular XY grid; interpolation is disabled."
        )
    _validate_regular_axis(x_centers, "X")
    _validate_regular_axis(y_centers, "Y")
    by_xy: dict[tuple[float, float], float] = {}
    for sample in validated:
        key = (sample.x_m, sample.y_m)
        if key in by_xy:
            raise VisualizationValidationError("PPFD grid contains duplicate XY samples.")
        by_xy[key] = sample.ppfd_umol_m2_s
    if any((x, y) not in by_xy for y in y_centers for x in x_centers):
        raise VisualizationValidationError(
            "PPFD grid has missing XY samples; interpolation is disabled."
        )
    values = np.asarray(
        [[by_xy[(x, y)] for x in x_centers] for y in y_centers],
        dtype=np.float64,
    )
    return RegularPpfdGrid(
        x_centers_m=x_centers,
        y_centers_m=y_centers,
        z_m=next(iter(z_values)),
        values=values,
        x_edges_m=_cell_edges(x_centers),
        y_edges_m=_cell_edges(y_centers),
    )


def heatmap_cell_boundary_pixel_positions(
    grid: RegularPpfdGrid,
) -> dict[str, tuple[int, ...]]:
    """Map authoritative regular-grid edges to deterministic raster boundaries."""

    plot = _plot_rectangle(grid.extent)
    return {
        "x": tuple(_map_x(value, grid.extent, plot) for value in grid.x_edges_m),
        "y": tuple(_map_y(value, grid.extent, plot) for value in grid.y_edges_m),
    }


def heatmap_gridline_policy_metadata(
    grid: RegularPpfdGrid,
) -> dict[str, object]:
    pixels = heatmap_cell_boundary_pixel_positions(grid)
    return {
        "policy_id": HEATMAP_GRIDLINE_POLICY_ID,
        "rendered": True,
        "applies_to": ["ppfd-heatmap.png", "ppfd-heatmap-overlay.png"],
        "coordinate_source": "RegularPpfdGrid.x_edges_m/y_edges_m",
        "x_boundaries_m": list(grid.x_edges_m),
        "y_boundaries_m": list(grid.y_edges_m),
        "x_boundary_pixels": list(pixels["x"]),
        "y_boundary_pixels": list(pixels["y"]),
        "color_srgb_8bit": list(HEATMAP_GRIDLINE_COLOR_SRGB_8BIT),
        "stroke_width_px": HEATMAP_GRIDLINE_WIDTH_PX,
        "value_transform": False,
        "orientation_transform": False,
        "render_order": [
            "heatmap_cells",
            "cell_boundary_gridlines",
            "plain_annotations_if_enabled",
            "plot_border",
            "authoritative_overlay_if_present",
        ],
    }


def effective_overlay_stroke_width(nominal_width_px: int) -> int:
    """Apply the centralized 1.5x policy with whole-pixel raster rounding."""

    if (
        isinstance(nominal_width_px, bool)
        or not isinstance(nominal_width_px, int)
        or nominal_width_px <= 0
    ):
        raise VisualizationValidationError(
            "overlay nominal stroke width must be a positive integer."
        )
    return int(math.ceil(nominal_width_px * OVERLAY_STROKE_SCALE))


def overlay_style_policy_metadata(system_id: str) -> dict[str, object]:
    """Return the sole system-aware contrast and stroke policy."""

    if system_id == "proposed":
        color_policy: dict[str, object] = {
            "mode": "authoritative_primitive_roles",
            "rectangle_role": "control_zone_color_group",
            "connector_role": "plot_border",
            "control_zone_palette_srgb_8bit": [
                list(color) for color in _ZONE_COLORS
            ],
            "legend": "Proposed modules retain authoritative control-zone colors.",
        }
    elif system_id in {"conventional", "hps"}:
        color_policy = {
            "mode": "fixed_system_fixture_color",
            "fixed_color_hex": "#ff2d2d",
            "fixed_color_srgb_8bit": list(
                COMPARATOR_OVERLAY_COLOR_SRGB_8BIT
            ),
            "primitive_color_groups_ignored": True,
            "legend": (
                "Authoritative Conventional fixtures — red (#ff2d2d)"
                if system_id == "conventional"
                else "Authoritative HPS fixtures — red (#ff2d2d)"
            ),
        }
    else:
        raise VisualizationValidationError(
            "overlay style policy system identifier is unsupported."
        )
    return {
        "policy_id": OVERLAY_STYLE_POLICY_ID,
        "system_id": system_id,
        "color": color_policy,
        "stroke": {
            "nominal_width_source": "authoritative_overlay_primitive",
            "scale": OVERLAY_STROKE_SCALE,
            "raster_rounding": "ceil_to_whole_pixel",
            "minimum_rendered_width_px": effective_overlay_stroke_width(1),
        },
        "z_order": "above_heatmap_cells_and_cell_boundary_gridlines",
        "geometry_modified": False,
        "geometry_inference": False,
    }


def build_ppfd_visualization_artifacts(
    *,
    run_id: str,
    samples: Sequence[PpfdMapSample],
    layout: OverlayPlanProvider | None,
    layout_identity: Mapping[str, object],
    requested_target_ppfd_umol_m2_s: float | None = None,
    overlay_plan: AuthoritativeOverlayPlan | None = None,
    reference: VisualizationReference | None = None,
) -> VisualizationArtifactSet:
    """Build deterministic PNG, metadata, Float32, and viewer derivatives."""

    if (
        not isinstance(run_id, str)
        or len(run_id) != 32
        or any(character not in "0123456789abcdef" for character in run_id)
    ):
        raise VisualizationValidationError("visualization run ID is invalid.")
    validated = _validated_samples(samples)
    grid = detect_regular_grid(validated)
    if grid.sample_count != len(validated):
        raise VisualizationValidationError("grid and source sample counts disagree.")
    field_sha256 = reference_plane_field_identity(validated)
    values = tuple(sample.ppfd_umol_m2_s for sample in validated)
    resolved_overlay = _resolve_overlay_plan(layout, overlay_plan)
    if reference is None:
        if requested_target_ppfd_umol_m2_s is None:
            raise VisualizationValidationError(
                "visualization reference policy is required."
            )
        reference = VisualizationReference.requested_target(
            requested_target_ppfd_umol_m2_s
        )
    elif requested_target_ppfd_umol_m2_s is not None:
        raise VisualizationValidationError(
            "provide either a visualization reference or the legacy requested target."
        )
    reference_center = reference.center_ppfd_umol_m2_s
    observed_min = min(values)
    observed_max = max(values)
    horizontal_reference_span = max(
        grid.x_centers_m[-1] - grid.x_centers_m[0],
        grid.y_centers_m[-1] - grid.y_centers_m[0],
    )
    vertical_display_transform = build_scatter_vertical_display_transform(
        requested_target_ppfd_umol_m2_s=reference_center,
        raw_minimum_ppfd_umol_m2_s=observed_min,
        raw_maximum_ppfd_umol_m2_s=observed_max,
        horizontal_reference_span_m=horizontal_reference_span,
        reference_kind=reference.kind,
        policy_id=reference.scatter_policy_id,
    )
    color_min = reference_center - 200.0
    color_max = reference_center + 200.0
    plain_png = _render_heatmap(
        grid, color_min, color_max, overlay_plan=None, annotate=True
    )
    overlay_png = _render_heatmap(
        grid,
        color_min,
        color_max,
        overlay_plan=resolved_overlay,
        annotate=False,
    )
    scatter_source_values = tuple(
        value
        for sample in validated
        for value in (sample.x_m, sample.y_m, sample.ppfd_umol_m2_s)
    )
    scatter_values = np.asarray(scatter_source_values, dtype="<f4")
    if not np.all(np.isfinite(scatter_values)):
        raise VisualizationValidationError(
            "PPFD scatter values must remain finite after Float32 conversion."
        )
    scatter_data = scatter_values.tobytes(order="C")
    if len(scatter_data) != len(validated) * SCATTER_RECORD_STRIDE_BYTES:
        raise VisualizationValidationError("scatter serialization length is invalid.")
    viewer_artifacts = _viewer_artifacts()
    derivatives = (
        VisualizationArtifact("ppfd-heatmap.png", plain_png),
        VisualizationArtifact("ppfd-heatmap-overlay.png", overlay_png),
        VisualizationArtifact("ppfd-scatter.f32le.bin", scatter_data),
        *viewer_artifacts,
    )
    derivative_inventory = {
        artifact.filename: {
            "sha256": artifact.sha256,
            "byte_length": artifact.byte_length,
        }
        for artifact in derivatives
    }
    float32_error_by_component = {
        name: max(
            abs(float(scatter_values[index * 3 + component]) - source_value)
            for index, source_value in enumerate(
                getattr(sample, attribute) for sample in validated
            )
        )
        for component, (name, attribute) in enumerate(
            (
                ("x_m", "x_m"),
                ("y_m", "y_m"),
                ("ppfd_umol_m2_s", "ppfd_umol_m2_s"),
            )
        )
    }
    layout_sha256 = _hash_json(layout_identity)
    overlay_payload = resolved_overlay.to_dict()
    overlay_metadata = dict(resolved_overlay.metadata or {})
    overlay_payload.update(overlay_metadata)
    overlay_payload.update(
        {
            "layout_identity_sha256": layout_sha256,
            "fixture_policy_id": resolved_overlay.policy_id,
            "fixture_count": len(resolved_overlay.fixture_metadata),
            "fixtures": [
                dict(item) for item in resolved_overlay.fixture_metadata
            ],
            "image_coordinate_inference": False,
            "geometry_inference": False,
            "source_field_identity_sha256": field_sha256,
            "source_sample_count": len(validated),
            "style_policy": overlay_style_policy_metadata(
                resolved_overlay.system_id
            ),
        }
    )
    display_payload: dict[str, object] = {
        "plane": "xy",
        "x_axis": {"label": "X position", "units": "m"},
        "y_axis": {"label": "Y position", "units": "m"},
        "color_axis": {"label": "PPFD", "units": "umol/m^2/s"},
        "colormap": {
            "name": _COLORMAP_NAME,
            "anchors_srgb_8bit": [list(color) for color in _COLORMAP_ANCHORS],
        },
        "reference": {
            "kind": reference.kind,
            "center_ppfd_umol_m2_s": reference_center,
            "independent_of_fspm_classification_target": True,
        },
        "reference_ppfd_umol_m2_s": reference_center,
        "color_limits_ppfd_umol_m2_s": [color_min, color_max],
        "color_limit_policy": reference.color_limit_policy,
        "color_limit_consumers": [
            "ppfd-heatmap.png cells and visible colorbar",
            "ppfd-heatmap-overlay.png cells and visible colorbar",
            "ppfd scatter point colors and visible legend",
        ],
        "normalization": {
            "method": "linear_to_declared_reference_centered_limits",
            "display_only": True,
            "ppfd_values_modified": False,
            "under_range_behavior": "saturate_to_colormap_minimum_color",
            "over_range_behavior": "saturate_to_colormap_maximum_color",
            "points_discarded": False,
        },
        "visible_legend": {
            "minimum_ppfd_umol_m2_s": color_min,
            "center_ppfd_umol_m2_s": reference_center,
            "maximum_ppfd_umol_m2_s": color_max,
            "units": "umol/m^2/s",
        },
        "rasterization": {
            "width_px": HEATMAP_WIDTH_PX,
            "height_px": HEATMAP_HEIGHT_PX,
            "sample_cells": "flat_fill",
            "image_resampling": "nearest_cell_no_scientific_interpolation",
            "cell_gridlines_policy_id": HEATMAP_GRIDLINE_POLICY_ID,
        },
    }
    if reference.kind == "requested_lighting_target":
        display_payload["requested_lighting_target_ppfd_umol_m2_s"] = (
            reference_center
        )
    elif reference.kind == "requested_sampled_ppfd_cap":
        display_payload["requested_sampled_ppfd_cap_umol_m2_s"] = (
            reference_center
        )
    metadata: dict[str, object] = {
        "schema_id": VISUALIZATION_SCHEMA_ID,
        "schema_version": VISUALIZATION_SCHEMA_VERSION,
        "run_id": run_id,
        "field": {
            "identity_sha256": field_sha256,
            "identity_encoding": (
                "authoritative-order records of little-endian Float64 x_m,y_m,z_m,ppfd_umol_m2_s"
            ),
            "sample_count": len(validated),
            "source_artifact": "ppfd.csv",
            "source_stage": (
                "final_full_output_reference_plane"
                if reference.kind == "achieved_final_baseline_mean"
                else "final_achieved_target_controlled_reference_plane"
            ),
            "coordinate_system": "right-handed scientific XY, meters, Z-up",
            "reference_plane_z_m": grid.z_m,
            "value_units": "micromole_per_square_meter_per_second",
            "observed_minimum_ppfd_umol_m2_s": observed_min,
            "observed_maximum_ppfd_umol_m2_s": observed_max,
        },
        "grid": {
            "kind": "regular",
            "resolution": {
                "x": len(grid.x_centers_m),
                "y": len(grid.y_centers_m),
            },
            "x_centers_m": list(grid.x_centers_m),
            "y_centers_m": list(grid.y_centers_m),
            "reshape_order": "rows_ascending_y_columns_ascending_x",
            "image_origin": "lower",
            "extent_m": {
                "x_min": grid.extent[0],
                "x_max": grid.extent[1],
                "y_min": grid.extent[2],
                "y_max": grid.extent[3],
            },
            "aspect": "equal",
            "cell_gridlines": heatmap_gridline_policy_metadata(grid),
            "interpolation": {
                "applied": False,
                "method": None,
                "resolution": None,
                "fill_behavior": None,
                "original_sample_identity_sha256": field_sha256,
            },
        },
        "orientation": {
            "sample_coordinates_transformed": False,
            "scientific_x_axis": "layout room length axis",
            "scientific_y_axis": "layout room width axis",
            "layout_axes_swapped_from_requested_room": (
                resolved_overlay.axes_swapped
            ),
            "room_span_x_m": resolved_overlay.room_length_m,
            "room_span_y_m": resolved_overlay.room_width_m,
            "positive_y_is_image_up": True,
        },
        "display": display_payload,
        "overlay": overlay_payload,
        "annotations": {
            "plain_heatmap": True,
            "overlay_heatmap": False,
            "source": "authoritative_regular_grid_samples",
            "selection": (
                "all_cells_when_max_dimension_below_20_otherwise_center_phased_checkerboard"
            ),
            "checkerboard_formula": "((row - rows//2) + (column - columns//2)) % 2 == 0",
            "row_behavior": "annotation columns alternate on successive rows",
            "format": "fixed_point_0_decimal_places",
            "rounding": "Python_format_round_half_even",
            "annotation_count": len(annotation_indices(grid)),
            "value_transform": False,
        },
        "scatter": {
            "filename": "ppfd-scatter.f32le.bin",
            "sha256": derivative_inventory["ppfd-scatter.f32le.bin"]["sha256"],
            "byte_length": len(scatter_data),
            "byte_order": "little-endian",
            "component_type": "float32",
            "record_layout": "x_m,y_m,ppfd_umol_m2_s",
            "stride_bytes": SCATTER_RECORD_STRIDE_BYTES,
            "count": len(validated),
            "max_absolute_float32_conversion_error_by_component": (
                float32_error_by_component
            ),
            "source_field_identity_sha256": field_sha256,
            "axes": {
                "x": "X position (m)",
                "y": "Y position (m)",
                "z": "PPFD (umol/m^2/s)",
                "color": "PPFD (umol/m^2/s)",
            },
            "vertical_display_transform": vertical_display_transform.to_metadata(),
            "load_policy": (
                "main_application_action_opens_viewer_then_fetch_and_sha256_validate"
            ),
        },
        "transforms": {
            "physics_recomputed": False,
            "target_rescaling": False,
            "symmetrization": False,
            "smoothing": False,
            "clipping": False,
            "correction": False,
            "hidden_normalization": False,
            "sample_exclusion": False,
        },
        "artifacts": derivative_inventory,
    }
    metadata_artifact = VisualizationArtifact(
        "visualization.json",
        (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return VisualizationArtifactSet(
        field_sha256=field_sha256,
        sample_count=len(validated),
        artifacts=(*derivatives, metadata_artifact),
        metadata=metadata,
    )


def publish_ppfd_visualizations(
    root: Path,
    *,
    run_id: str,
    samples: Sequence[PpfdMapSample],
    layout: OverlayPlanProvider | None,
    layout_identity: Mapping[str, object],
    requested_target_ppfd_umol_m2_s: float | None = None,
    overlay_plan: AuthoritativeOverlayPlan | None = None,
    reference: VisualizationReference | None = None,
) -> VisualizationArtifactSet:
    """Atomically write every validated derivative inside one staging run."""

    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise VisualizationValidationError("visualization root must be a directory.")
    artifacts = build_ppfd_visualization_artifacts(
        run_id=run_id,
        samples=samples,
        layout=layout,
        layout_identity=layout_identity,
        requested_target_ppfd_umol_m2_s=requested_target_ppfd_umol_m2_s,
        overlay_plan=overlay_plan,
        reference=reference,
    )
    for artifact in artifacts.artifacts:
        destination = resolved_root / artifact.filename
        if not destination.resolve().is_relative_to(resolved_root):
            raise VisualizationValidationError("visualization artifact escaped its run.")
        atomic_write_bytes(destination, artifact.data)
    return artifacts


def _validated_samples(samples: Sequence[PpfdMapSample]) -> tuple[PpfdMapSample, ...]:
    values = tuple(samples)
    if not values:
        raise VisualizationValidationError("PPFD visualization requires samples.")
    for sample in values:
        if not isinstance(sample, PpfdMapSample):
            raise VisualizationValidationError("PPFD samples have an invalid type.")
        fields = (sample.x_m, sample.y_m, sample.z_m, sample.ppfd_umol_m2_s)
        if any(not math.isfinite(value) for value in fields):
            raise VisualizationValidationError("PPFD samples must be finite.")
        if sample.ppfd_umol_m2_s < 0.0:
            raise VisualizationValidationError("PPFD samples must be non-negative.")
    return values


def _resolve_overlay_plan(
    layout: OverlayPlanProvider | None,
    overlay_plan: AuthoritativeOverlayPlan | None,
) -> AuthoritativeOverlayPlan:
    if overlay_plan is not None and layout is not None:
        raise VisualizationValidationError(
            "provide either a layout overlay provider or an overlay plan."
        )
    if overlay_plan is not None:
        return overlay_plan
    if layout is None:
        raise VisualizationValidationError("authoritative overlay plan is required.")
    provider = getattr(layout, "authoritative_overlay_plan", None)
    if not callable(provider):
        raise VisualizationValidationError(
            "layout engine does not emit authoritative overlay primitives."
        )
    resolved = provider()
    if not isinstance(resolved, AuthoritativeOverlayPlan):
        raise VisualizationValidationError(
            "layout engine emitted an invalid authoritative overlay plan."
        )
    return resolved


def _validate_regular_axis(values: tuple[float, ...], label: str) -> None:
    if not values:
        raise VisualizationValidationError(f"{label} grid axis is empty.")
    if len(values) < 3:
        return
    differences = np.diff(np.asarray(values, dtype=np.float64))
    if np.any(differences <= 0.0) or not np.allclose(
        differences,
        differences[0],
        rtol=1e-10,
        atol=1e-12,
    ):
        raise VisualizationValidationError(
            f"{label} coordinates are not regularly spaced; interpolation is disabled."
        )


def _cell_edges(centers: tuple[float, ...]) -> tuple[float, ...]:
    if len(centers) == 1:
        return (centers[0] - 0.5, centers[0] + 0.5)
    middles = tuple((left + right) * 0.5 for left, right in zip(centers, centers[1:]))
    return (
        centers[0] - (centers[1] - centers[0]) * 0.5,
        *middles,
        centers[-1] + (centers[-1] - centers[-2]) * 0.5,
    )


def _render_heatmap(
    grid: RegularPpfdGrid,
    color_min: float,
    color_max: float,
    *,
    overlay_plan: AuthoritativeOverlayPlan | None,
    annotate: bool,
) -> bytes:
    image = np.empty((HEATMAP_HEIGHT_PX, HEATMAP_WIDTH_PX, 3), dtype=np.uint8)
    image[:, :, :] = _BACKGROUND
    plot = _plot_rectangle(grid.extent)
    left, top, right, bottom = plot
    for y_index in range(len(grid.y_centers_m)):
        py0 = _map_y(grid.y_edges_m[y_index + 1], grid.extent, plot)
        py1 = _map_y(grid.y_edges_m[y_index], grid.extent, plot)
        for x_index in range(len(grid.x_centers_m)):
            px0 = _map_x(grid.x_edges_m[x_index], grid.extent, plot)
            px1 = _map_x(grid.x_edges_m[x_index + 1], grid.extent, plot)
            color = _color_for_value(
                float(grid.values[y_index, x_index]), color_min, color_max
            )
            _fill_rectangle(image, px0, py0, px1, py1, color)
    _draw_cell_gridlines(image, grid, plot)
    if annotate:
        _draw_annotations(image, grid, plot)
    _draw_rectangle(image, left, top, right, bottom, _PLOT_BORDER, thickness=2)
    if overlay_plan is not None:
        _draw_layout_overlay(image, grid.extent, plot, overlay_plan)
    bar_left = right + _COLORBAR_GAP_PX
    bar_right = bar_left + _COLORBAR_WIDTH_PX
    for pixel_y in range(top, bottom + 1):
        fraction = 1.0 - (pixel_y - top) / max(1, bottom - top)
        value = color_min + fraction * (color_max - color_min)
        _fill_rectangle(
            image,
            bar_left,
            pixel_y,
            bar_right,
            pixel_y + 1,
            _color_for_value(value, color_min, color_max),
        )
    _draw_rectangle(image, bar_left, top, bar_right, bottom, _PLOT_BORDER, thickness=1)
    _draw_colorbar_labels(
        image,
        bar_right + 9,
        top,
        bottom,
        color_min,
        color_max,
    )
    return _encode_png_rgb(image)


def _draw_cell_gridlines(
    image: NDArray[np.uint8],
    grid: RegularPpfdGrid,
    plot: tuple[int, int, int, int],
) -> None:
    positions = heatmap_cell_boundary_pixel_positions(grid)
    for x in positions["x"]:
        _draw_stroked_line(
            image,
            x,
            plot[1],
            x,
            plot[3],
            HEATMAP_GRIDLINE_COLOR_SRGB_8BIT,
            width_px=HEATMAP_GRIDLINE_WIDTH_PX,
        )
    for y in positions["y"]:
        _draw_stroked_line(
            image,
            plot[0],
            y,
            plot[2],
            y,
            HEATMAP_GRIDLINE_COLOR_SRGB_8BIT,
            width_px=HEATMAP_GRIDLINE_WIDTH_PX,
        )


def annotation_indices(grid: RegularPpfdGrid) -> tuple[tuple[int, int], ...]:
    """Return the exact raw-sample indices selected for plain-map labels."""

    rows = len(grid.y_centers_m)
    columns = len(grid.x_centers_m)
    if max(rows, columns) < 20:
        return tuple((row, column) for row in range(rows) for column in range(columns))
    center_row = rows // 2
    center_column = columns // 2
    return tuple(
        (row, column)
        for row in range(rows)
        for column in range(columns)
        if ((row - center_row) + (column - center_column)) % 2 == 0
    )


def _draw_annotations(
    image: NDArray[np.uint8],
    grid: RegularPpfdGrid,
    plot: tuple[int, int, int, int],
) -> None:
    for row, column in annotation_indices(grid):
        text = format(float(grid.values[row, column]), ".0f")
        x = _map_x(grid.x_centers_m[column], grid.extent, plot)
        y = _map_y(grid.y_centers_m[row], grid.extent, plot)
        cell_width = abs(
            _map_x(grid.x_edges_m[column + 1], grid.extent, plot)
            - _map_x(grid.x_edges_m[column], grid.extent, plot)
        )
        cell_height = abs(
            _map_y(grid.y_edges_m[row + 1], grid.extent, plot)
            - _map_y(grid.y_edges_m[row], grid.extent, plot)
        )
        base_width = len(text) * 4 - 1
        scale = 2 if base_width * 2 + 4 <= cell_width and 14 <= cell_height else 1
        width = base_width * scale
        height = 5 * scale
        _fill_rectangle(
            image,
            x - width // 2 - 2,
            y - height // 2 - 2,
            x + (width + 1) // 2 + 2,
            y + (height + 1) // 2 + 2,
            (240, 244, 240),
        )
        _draw_text(
            image,
            text,
            x - width // 2,
            y - height // 2,
            (8, 16, 13),
            scale=scale,
        )


def _draw_colorbar_labels(
    image: NDArray[np.uint8],
    x: int,
    top: int,
    bottom: int,
    minimum: float,
    maximum: float,
) -> None:
    center = (minimum + maximum) * 0.5
    for value, y in ((maximum, top), (center, (top + bottom) // 2), (minimum, bottom - 5)):
        _draw_text(
            image,
            _format_legend_number(value),
            x,
            y,
            (224, 238, 226),
            scale=2,
        )


def _format_legend_number(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _draw_text(
    image: NDArray[np.uint8],
    text: str,
    x: int,
    y: int,
    color: tuple[int, int, int],
    *,
    scale: int,
) -> None:
    cursor = x
    for character in text:
        glyph = _GLYPHS.get(character)
        if glyph is None:
            cursor += 4 * scale
            continue
        for row, pattern in enumerate(glyph):
            for column, enabled in enumerate(pattern):
                if enabled == "1":
                    _fill_rectangle(
                        image,
                        cursor + column * scale,
                        y + row * scale,
                        cursor + (column + 1) * scale,
                        y + (row + 1) * scale,
                        color,
                    )
        cursor += 4 * scale


def _plot_rectangle(
    extent: tuple[float, float, float, float]
) -> tuple[int, int, int, int]:
    available_width = (
        HEATMAP_WIDTH_PX
        - 2 * _PLOT_MARGIN_PX
        - _COLORBAR_GAP_PX
        - _COLORBAR_WIDTH_PX
    )
    available_height = HEATMAP_HEIGHT_PX - 2 * _PLOT_MARGIN_PX
    x_span = max(extent[1] - extent[0], 1e-12)
    y_span = max(extent[3] - extent[2], 1e-12)
    scale = min(available_width / x_span, available_height / y_span)
    plot_width = max(1, int(round(x_span * scale)))
    plot_height = max(1, int(round(y_span * scale)))
    left = _PLOT_MARGIN_PX + (available_width - plot_width) // 2
    top = _PLOT_MARGIN_PX + (available_height - plot_height) // 2
    return left, top, left + plot_width, top + plot_height


def _draw_layout_overlay(
    image: NDArray[np.uint8],
    extent: tuple[float, float, float, float],
    plot: tuple[int, int, int, int],
    overlay_plan: AuthoritativeOverlayPlan,
) -> None:
    for line in overlay_plan.lines:
        color = _overlay_stroke_color(
            overlay_plan.system_id,
            line.stroke_color_role,
            line.color_group_index,
        )
        _draw_stroked_line(
            image,
            _map_x(line.start_x_m, extent, plot),
            _map_y(line.start_y_m, extent, plot),
            _map_x(line.end_x_m, extent, plot),
            _map_y(line.end_y_m, extent, plot),
            color,
            width_px=effective_overlay_stroke_width(line.stroke_width_px),
        )
    for rectangle in overlay_plan.rectangles:
        half_width = rectangle.width_x_m * 0.5
        half_height = rectangle.height_y_m * 0.5
        angle = math.radians(rectangle.orientation_degrees)
        cosine = math.cos(angle)
        sine = math.sin(angle)
        corners_m = tuple(
            (
                rectangle.center_x_m + local_x * cosine - local_y * sine,
                rectangle.center_y_m + local_x * sine + local_y * cosine,
            )
            for local_x, local_y in (
                (-half_width, -half_height),
                (-half_width, half_height),
                (half_width, half_height),
                (half_width, -half_height),
            )
        )
        corners_px = tuple(
            (_map_x(x, extent, plot), _map_y(y, extent, plot))
            for x, y in corners_m
        )
        color = _overlay_stroke_color(
            overlay_plan.system_id,
            rectangle.stroke_color_role,
            rectangle.color_group_index,
        )
        rendered_width = effective_overlay_stroke_width(
            rectangle.stroke_width_px
        )
        if rectangle.orientation_degrees % 180.0 == 0.0:
            _draw_rectangle(
                image,
                min(point[0] for point in corners_px),
                min(point[1] for point in corners_px),
                max(point[0] for point in corners_px),
                max(point[1] for point in corners_px),
                color,
                thickness=rendered_width,
            )
        else:
            for start, end in zip(
                corners_px, (*corners_px[1:], corners_px[0]), strict=True
            ):
                _draw_stroked_line(
                    image,
                    start[0],
                    start[1],
                    end[0],
                    end[1],
                    color,
                    width_px=rendered_width,
                )
        cx = _map_x(rectangle.center_x_m, extent, plot)
        cy = _map_y(rectangle.center_y_m, extent, plot)
        _draw_stroked_line(
            image, cx - 2, cy, cx + 2, cy, color, width_px=rendered_width
        )
        _draw_stroked_line(
            image, cx, cy - 2, cx, cy + 2, color, width_px=rendered_width
        )
    room_left = _map_x(-overlay_plan.room_length_m * 0.5, extent, plot)
    room_right = _map_x(overlay_plan.room_length_m * 0.5, extent, plot)
    room_top = _map_y(overlay_plan.room_width_m * 0.5, extent, plot)
    room_bottom = _map_y(-overlay_plan.room_width_m * 0.5, extent, plot)
    _draw_rectangle(
        image,
        room_left,
        room_top,
        room_right,
        room_bottom,
        _ROOM_BORDER,
        thickness=1,
    )


def _overlay_stroke_color(
    system_id: str,
    color_role: str,
    color_group_index: int,
) -> tuple[int, int, int]:
    if system_id in {"conventional", "hps"}:
        return COMPARATOR_OVERLAY_COLOR_SRGB_8BIT
    if system_id != "proposed":
        raise VisualizationValidationError(
            "overlay stroke system identifier is unsupported."
        )
    if color_role == "plot_border":
        return _PLOT_BORDER
    if color_role == "color_group":
        return _ZONE_COLORS[color_group_index % len(_ZONE_COLORS)]
    raise VisualizationValidationError("overlay stroke color role is unsupported.")


def _map_x(
    value: float,
    extent: tuple[float, float, float, float],
    plot: tuple[int, int, int, int],
) -> int:
    fraction = (value - extent[0]) / max(extent[1] - extent[0], 1e-12)
    return int(round(plot[0] + fraction * (plot[2] - plot[0])))


def _map_y(
    value: float,
    extent: tuple[float, float, float, float],
    plot: tuple[int, int, int, int],
) -> int:
    fraction = (value - extent[2]) / max(extent[3] - extent[2], 1e-12)
    return int(round(plot[3] - fraction * (plot[3] - plot[1])))


def _color_for_value(value: float, minimum: float, maximum: float) -> tuple[int, int, int]:
    fraction = 0.5 if maximum == minimum else (value - minimum) / (maximum - minimum)
    fraction = min(1.0, max(0.0, fraction))
    scaled = fraction * (len(_COLORMAP_ANCHORS) - 1)
    lower = min(int(math.floor(scaled)), len(_COLORMAP_ANCHORS) - 2)
    local = scaled - lower
    start = _COLORMAP_ANCHORS[lower]
    end = _COLORMAP_ANCHORS[lower + 1]
    return tuple(
        int(round(start[channel] + local * (end[channel] - start[channel])))
        for channel in range(3)
    )  # type: ignore[return-value]


def _fill_rectangle(
    image: NDArray[np.uint8],
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
) -> None:
    left = max(0, min(x0, x1))
    right = min(image.shape[1], max(x0, x1))
    top = max(0, min(y0, y1))
    bottom = min(image.shape[0], max(y0, y1))
    if right > left and bottom > top:
        image[top:bottom, left:right, :] = color


def _draw_rectangle(
    image: NDArray[np.uint8],
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
    *,
    thickness: int,
) -> None:
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    for offset in range(thickness):
        _draw_line(image, left + offset, top + offset, right - offset, top + offset, color)
        _draw_line(image, right - offset, top + offset, right - offset, bottom - offset, color)
        _draw_line(image, right - offset, bottom - offset, left + offset, bottom - offset, color)
        _draw_line(image, left + offset, bottom - offset, left + offset, top + offset, color)


def _draw_line(
    image: NDArray[np.uint8],
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
) -> None:
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        if 0 <= x0 < image.shape[1] and 0 <= y0 < image.shape[0]:
            image[y0, x0, :] = color
        if x0 == x1 and y0 == y1:
            break
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x0 += sx
        if doubled <= dx:
            error += dx
            y0 += sy


def _draw_stroked_line(
    image: NDArray[np.uint8],
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
    *,
    width_px: int,
) -> None:
    if width_px == 1:
        _draw_line(image, x0, y0, x1, y1, color)
        return
    dx = x1 - x0
    dy = y1 - y0
    length = math.hypot(dx, dy)
    if length == 0.0:
        lower = -(width_px // 2)
        upper = lower + width_px
        _fill_rectangle(
            image,
            x0 + lower,
            y0 + lower,
            x0 + upper,
            y0 + upper,
            color,
        )
        return
    normal_x = -dy / length
    normal_y = dx / length
    first_offset = -(width_px // 2)
    for offset in range(first_offset, first_offset + width_px):
        offset_x = int(round(normal_x * offset))
        offset_y = int(round(normal_y * offset))
        _draw_line(
            image,
            x0 + offset_x,
            y0 + offset_y,
            x1 + offset_x,
            y1 + offset_y,
            color,
        )


def _encode_png_rgb(image: NDArray[np.uint8]) -> bytes:
    height, width, channels = image.shape
    if channels != 3:
        raise VisualizationValidationError("PNG source must be RGB.")
    scanlines = b"".join(
        b"\x00" + image[row].tobytes(order="C") for row in range(height)
    )
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return signature + _png_chunk(b"IHDR", header) + _png_chunk(
        b"IDAT", zlib.compress(scanlines, level=9)
    ) + _png_chunk(b"IEND", b"")


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _viewer_artifacts() -> tuple[VisualizationArtifact, ...]:
    root = resources.files("fspm_optics").joinpath("resources", "scatter")
    artifacts = []
    for name in _VIEWER_FILES:
        source = root.joinpath(name)
        if not source.is_file():
            raise FileNotFoundError(f"scatter viewer resource is missing: {name}")
        artifacts.append(
            VisualizationArtifact(f"ppfd-scatter-viewer/{name}", source.read_bytes())
        )
    return tuple(artifacts)


def _hash_json(payload: object) -> str:
    data = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()
