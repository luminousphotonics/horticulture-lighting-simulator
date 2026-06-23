"""Deterministic leafy-green / lettuce-style plant generator."""

from __future__ import annotations

import math
import random

from rad_rebuild.radiance.engine.plants.config import PlantGeometryConfig
from rad_rebuild.radiance.engine.plants.mesh import build_curved_leaf_mesh
from rad_rebuild.radiance.engine.plants.models import (
    LeafGeometry,
    PlantGeometry,
    PlantScene,
    Vector3,
)

LEAF_MATERIAL_ID = "plant_leaf_material"


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


def _plant_center(config: PlantGeometryConfig, row: int, column: int) -> Vector3:
    x_m = (column - (config.plant_grid_columns - 1) / 2.0) * config.plant_spacing_m
    y_m = (row - (config.plant_grid_rows - 1) / 2.0) * config.plant_spacing_m
    return (x_m, y_m, 0.0)


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
