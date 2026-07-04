"""Deterministic leafy-green / lettuce-style plant generator."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import random

from rad_rebuild.radiance.engine.plants.config import PlantGeometryConfig
from rad_rebuild.radiance.engine.plants.layout import (
    canonical_room_dimensions_ft,
    fit_plant_grid,
)
from rad_rebuild.radiance.engine.plants.mesh import build_curved_leaf_mesh
from rad_rebuild.radiance.engine.plants.models import (
    LeafGeometry,
    PlantGeometry,
    PlantScene,
    Vector3,
)

LEAF_MATERIAL_ID = "plant_leaf_material"


@dataclass(frozen=True)
class PlantLocalXYFootprint:
    """Maximum unmodified local XY extent for a generated plant set."""

    half_extent_x_m: float
    half_extent_y_m: float


def generate_plant_scene(config: PlantGeometryConfig | None = None) -> PlantScene:
    """Generate a deterministic plant scene from explicit configuration."""

    resolved_config = config or PlantGeometryConfig()
    rng = random.Random(resolved_config.seed)
    plants: list[PlantGeometry] = []

    for row in range(resolved_config.plant_grid_rows):
        for column in range(resolved_config.plant_grid_columns):
            plant_id = _plant_id(row, column)
            center_m = _plant_center(resolved_config, row, column)
            leaves = _generate_leaves(resolved_config, rng, plant_id, center_m)
            plants.append(
                PlantGeometry(
                    plant_id=plant_id,
                    row=row,
                    column=column,
                    center_m=center_m,
                    leaves=tuple(leaves),
                )
            )

    return PlantScene(config=resolved_config, plants=tuple(plants))


def fit_plant_geometry_config_to_room(
    config: PlantGeometryConfig,
    *,
    length_ft: float | int,
    width_ft: float | int,
    rows: int | None = None,
    columns: int | None = None,
) -> PlantGeometryConfig:
    """Return a room-fit config using measured unmodified plant geometry."""

    canonical_length_ft, canonical_width_ft = canonical_room_dimensions_ft(
        length_ft,
        width_ft,
    )
    layout = fit_plant_grid(
        canonical_length_ft,
        canonical_width_ft,
        target_spacing_m=config.plant_spacing_m,
        rows=rows,
        columns=columns,
    )
    row_count = layout.length.count
    column_count = layout.width.count

    while True:
        probe_config = replace(
            config,
            plant_grid_rows=row_count,
            plant_grid_columns=column_count,
            plant_row_spacing_m=None,
            plant_column_spacing_m=None,
            plant_row_margin_m=None,
            plant_column_margin_m=None,
            room_length_m=None,
            room_width_m=None,
        )
        footprint = measure_plant_local_xy_footprint(probe_config)
        fitted = fit_plant_grid(
            canonical_length_ft,
            canonical_width_ft,
            target_spacing_m=config.plant_spacing_m,
            local_half_extent_length_m=footprint.half_extent_x_m,
            local_half_extent_width_m=footprint.half_extent_y_m,
            rows=row_count,
            columns=column_count,
        )
        if (
            fitted.length.count == row_count
            and fitted.width.count == column_count
        ):
            return replace(
                config,
                plant_grid_rows=fitted.length.count,
                plant_grid_columns=fitted.width.count,
                plant_row_spacing_m=fitted.length.spacing_m,
                plant_column_spacing_m=fitted.width.spacing_m,
                plant_row_margin_m=fitted.length.edge_center_margin_m,
                plant_column_margin_m=fitted.width.edge_center_margin_m,
                room_length_m=fitted.length.axis_m,
                room_width_m=fitted.width.axis_m,
            )
        row_count = fitted.length.count
        column_count = fitted.width.count


def measure_plant_local_xy_footprint(
    config: PlantGeometryConfig,
) -> PlantLocalXYFootprint:
    """Measure generated plant meshes relative to their placement center."""

    rng = random.Random(config.seed)
    half_extent_x_m = 0.0
    half_extent_y_m = 0.0
    origin_m = (0.0, 0.0, 0.0)
    for row in range(config.plant_grid_rows):
        for column in range(config.plant_grid_columns):
            plant_id = _plant_id(row, column)
            leaves = _generate_leaves(config, rng, plant_id, origin_m)
            for leaf in leaves:
                for vertex_x, vertex_y, _vertex_z in leaf.mesh.vertices:
                    half_extent_x_m = max(half_extent_x_m, abs(vertex_x))
                    half_extent_y_m = max(half_extent_y_m, abs(vertex_y))
    return PlantLocalXYFootprint(
        half_extent_x_m=half_extent_x_m,
        half_extent_y_m=half_extent_y_m,
    )


def _plant_center(config: PlantGeometryConfig, row: int, column: int) -> Vector3:
    if config.room_length_m is not None and config.room_width_m is not None:
        x_m = _axis_center(
            axis_m=config.room_length_m,
            count=config.plant_grid_rows,
            spacing_m=config.plant_row_spacing_m,
            margin_m=config.plant_row_margin_m
            if config.plant_row_margin_m is not None
            else config.plant_edge_center_margin_m,
            index=row,
        )
        y_m = _axis_center(
            axis_m=config.room_width_m,
            count=config.plant_grid_columns,
            spacing_m=config.plant_column_spacing_m,
            margin_m=config.plant_column_margin_m
            if config.plant_column_margin_m is not None
            else config.plant_edge_center_margin_m,
            index=column,
        )
        return (x_m, y_m, 0.0)

    x_m = (column - (config.plant_grid_columns - 1) / 2.0) * config.plant_spacing_m
    y_m = (row - (config.plant_grid_rows - 1) / 2.0) * config.plant_spacing_m
    return (x_m, y_m, 0.0)


def _axis_center(
    *,
    axis_m: float,
    count: int,
    spacing_m: float | None,
    margin_m: float,
    index: int,
) -> float:
    if count == 1:
        return 0.0
    resolved_spacing = (
        (axis_m - 2.0 * margin_m) / (count - 1)
        if spacing_m is None
        else spacing_m
    )
    return -axis_m / 2.0 + margin_m + index * resolved_spacing


def _generate_leaves(
    config: PlantGeometryConfig,
    rng: random.Random,
    plant_id: str,
    center_m: Vector3,
) -> list[LeafGeometry]:
    leaves: list[LeafGeometry] = []
    growth_scale = 0.25 + 0.75 * config.growth_stage
    leaf_step_rad = (math.pi * 2.0) / config.leaf_count_per_plant
    jitter_limit_rad = leaf_step_rad * 0.28

    for leaf_index in range(config.leaf_count_per_plant):
        base_azimuth_rad = leaf_index * leaf_step_rad
        azimuth_rad = base_azimuth_rad + rng.uniform(
            -jitter_limit_rad, jitter_limit_rad
        )
        tilt_deg = rng.uniform(*config.leaf_tilt_range_deg)
        tilt_rad = math.radians(tilt_deg)
        raw_length_m = rng.uniform(*config.leaf_length_range_m) * growth_scale
        width_m = rng.uniform(*config.leaf_width_range_m) * growth_scale
        length_m = min(raw_length_m, _max_leaf_length_for_canopy(config, tilt_rad))
        curvature_m = config.leaf_curvature_m * growth_scale
        leaf_id = f"{plant_id}_leaf_{leaf_index:03d}"
        mesh = build_curved_leaf_mesh(
            origin_m=center_m,
            azimuth_rad=azimuth_rad,
            length_m=length_m,
            width_m=width_m,
            tilt_rad=tilt_rad,
            curvature_m=curvature_m,
            max_height_m=config.plant_height_m,
        )
        leaves.append(
            LeafGeometry(
                plant_id=plant_id,
                leaf_id=leaf_id,
                leaf_index=leaf_index,
                azimuth_rad=azimuth_rad,
                length_m=length_m,
                width_m=width_m,
                tilt_rad=tilt_rad,
                curvature_m=curvature_m,
                mesh=mesh,
                radiance_material_id=LEAF_MATERIAL_ID,
            )
        )

    return leaves


def _max_leaf_length_for_canopy(
    config: PlantGeometryConfig,
    tilt_rad: float,
) -> float:
    horizontal_scale = max(math.cos(tilt_rad), 0.15)
    return config.canopy_radius_m / horizontal_scale


def _plant_id(row: int, column: int) -> str:
    return f"plant_r{row:03d}_c{column:03d}"
