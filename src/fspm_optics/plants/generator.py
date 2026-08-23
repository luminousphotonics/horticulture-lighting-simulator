"""Deterministic leafy-green / lettuce-style plant generator."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import random
from typing import Mapping

from fspm_optics.plants.config import PlantGeometryConfig
from fspm_optics.plants.layout import (
    canonical_room_dimensions_ft,
    fit_plant_grid,
)
from fspm_optics.plants.mesh import (
    build_curved_leaf_mesh,
    build_rex_leaf_mesh,
    scientific_mesh_faces,
    scientific_mesh_patches,
)
from fspm_optics.plants.models import (
    LeafGeometry,
    LeafLayer,
    LeafSurface,
    LeafWorldTransform,
    PlantGeometry,
    PlantMesh,
    PlantScene,
    Vector3,
)
from fspm_optics.plants.rex import RexPlantConfig, rex_layer_counts
from fspm_optics.plants.rex_juvenile import RexJuvenilePreheadingConfig
from fspm_optics.plants.validation import validate_plant_mesh

LEAF_MATERIAL_ID = "plant_leaf_material"


@dataclass(frozen=True)
class _RexLeafDraft:
    leaf_id: str
    leaf_rank: int
    leaf_layer: LeafLayer
    azimuth_rad: float
    elevation_rad: float
    origin_m: Vector3
    length_m: float
    width_m: float
    vertices: tuple[Vector3, ...]
    triangle_indices: tuple[tuple[int, int, int], ...]
    u_segments: int
    v_segments: int


@dataclass(frozen=True)
class PlantLocalXYFootprint:
    """Maximum unmodified local XY extent for a generated plant set."""

    half_extent_x_m: float
    half_extent_y_m: float


@dataclass(frozen=True)
class PlantRenderOverride:
    """Optional per-plant visual parameters for canonical plant generation."""

    plant_height_m: float | None = None
    canopy_radius_m: float | None = None
    leaf_count_per_plant: int | None = None
    growth_stage: float | None = None
    seed_offset: int | None = None
    variation_strength: float | None = None


def generate_rex_butterhead_plant(
    config: RexPlantConfig | None = None,
    *,
    seed: int | None = None,
) -> PlantMesh:
    """Generate one deterministic mature Rex butterhead scientific mesh."""

    resolved = config or RexPlantConfig()
    resolved_seed = resolved.seed if seed is None else seed
    if isinstance(resolved_seed, bool) or not isinstance(resolved_seed, int):
        raise ValueError("seed must be an integer.")
    if resolved_seed != resolved.seed:
        resolved = replace(resolved, seed=resolved_seed)
    layer_counts = rex_layer_counts(resolved.leaf_count)
    drafts: list[_RexLeafDraft] = []
    for rank_index in range(resolved.leaf_count):
        leaf_rank = rank_index + 1
        layer, layer_index, layer_count = _rex_layer_assignment(
            rank_index,
            layer_counts,
        )
        rng = random.Random(resolved_seed * 1_000_003 + leaf_rank * 97_409)
        progress = layer_index / max(1, layer_count - 1)
        azimuth_rad = math.radians(
            resolved.base_angle_deg
            + rank_index * resolved.golden_angle_deg
            + rng.uniform(
                -resolved.angular_jitter_deg,
                resolved.angular_jitter_deg,
            )
        )
        variation = 1.0 + rng.uniform(
            -resolved.size_variation_fraction,
            resolved.size_variation_fraction,
        )
        parameters = _rex_leaf_parameters(
            resolved,
            layer,
            progress,
            variation,
            rng,
        )
        elevation_rad = math.radians(parameters["elevation_deg"])
        base_radius_m = parameters["base_radius_m"]
        origin_m = (
            base_radius_m * math.cos(azimuth_rad),
            base_radius_m * math.sin(azimuth_rad),
            parameters["base_height_m"],
        )
        u_segments, v_segments = resolved.mesh_segments_for_layer(layer)
        mesh = build_rex_leaf_mesh(
            origin_m=origin_m,
            azimuth_rad=azimuth_rad,
            elevation_rad=elevation_rad,
            length_m=parameters["length_m"],
            width_m=parameters["width_m"],
            arch_strength_m=parameters["arch_strength_m"],
            cup_strength_m=parameters["cup_strength_m"],
            edge_curl_m=parameters["edge_curl_m"],
            tip_sag_m=parameters["tip_sag_m"],
            margin_wave_amplitude=parameters["margin_wave_amplitude"],
            margin_wave_phase_rad=rng.uniform(0.0, 2.0 * math.pi),
            u_segments=u_segments,
            v_segments=v_segments,
        )
        drafts.append(
            _RexLeafDraft(
                leaf_id=f"{resolved.plant_id}_leaf_{leaf_rank:03d}",
                leaf_rank=leaf_rank,
                leaf_layer=layer,
                azimuth_rad=azimuth_rad,
                elevation_rad=elevation_rad,
                origin_m=origin_m,
                length_m=parameters["length_m"],
                width_m=parameters["width_m"],
                vertices=mesh.vertices,
                triangle_indices=mesh.faces,
                u_segments=u_segments,
                v_segments=v_segments,
            )
        )

    horizontal_scale, vertical_scale, vertical_offset = _rex_envelope_transform(
        drafts,
        target_diameter_m=resolved.projected_diameter_m,
        target_height_m=resolved.plant_height_m,
    )
    leaves: list[LeafSurface] = []
    for draft in drafts:
        vertices = tuple(
            (
                vertex[0] * horizontal_scale,
                vertex[1] * horizontal_scale,
                (vertex[2] + vertical_offset) * vertical_scale,
            )
            for vertex in draft.vertices
        )
        origin = (
            draft.origin_m[0] * horizontal_scale,
            draft.origin_m[1] * horizontal_scale,
            (draft.origin_m[2] + vertical_offset) * vertical_scale,
        )
        faces = scientific_mesh_faces(
            plant_id=resolved.plant_id,
            leaf_id=draft.leaf_id,
            leaf_rank=draft.leaf_rank,
            leaf_layer=draft.leaf_layer,
            vertices=vertices,
            triangle_indices=draft.triangle_indices,
        )
        patches = scientific_mesh_patches(
            plant_id=resolved.plant_id,
            leaf_id=draft.leaf_id,
            leaf_rank=draft.leaf_rank,
            leaf_layer=draft.leaf_layer,
            faces=faces,
            u_segments=draft.u_segments,
            v_segments=draft.v_segments,
            patch_u_count=resolved.leaf_patch_u,
            patch_v_count=resolved.leaf_patch_v,
        )
        leaves.append(
            LeafSurface(
                plant_id=resolved.plant_id,
                leaf_id=draft.leaf_id,
                leaf_rank=draft.leaf_rank,
                leaf_layer=draft.leaf_layer,
                world_transform=LeafWorldTransform(
                    origin_m=origin,
                    azimuth_rad=draft.azimuth_rad,
                    elevation_rad=draft.elevation_rad,
                    horizontal_scale=horizontal_scale,
                    vertical_scale=vertical_scale,
                    vertical_offset_m=vertical_offset * vertical_scale,
                ),
                azimuth_rad=draft.azimuth_rad,
                elevation_rad=draft.elevation_rad,
                length_m=draft.length_m * horizontal_scale,
                width_m=draft.width_m * horizontal_scale,
                nominal_thickness_m=resolved.nominal_leaf_thickness_m,
                vertices=vertices,
                triangle_indices=draft.triangle_indices,
                faces=faces,
                patches=patches,
                radiance_material_id=resolved.radiance_material_id,
            )
        )
    plant = PlantMesh(
        plant_id=resolved.plant_id,
        config=resolved,
        seed=resolved_seed,
        leaves=tuple(leaves),
    )
    validate_plant_mesh(plant)
    return plant


def generate_rex_juvenile_preheading_plant(
    config: RexJuvenilePreheadingConfig | None = None,
) -> PlantMesh:
    """Generate the deterministic static juvenile pre-heading Rex profile."""

    resolved = config or RexJuvenilePreheadingConfig()
    leaves: list[LeafSurface] = []
    for rank_index in range(resolved.leaf_count):
        leaf_rank = rank_index + 1
        layer, layer_index, layer_count = _rex_layer_assignment(
            rank_index,
            resolved.cohort_counts,
        )
        progress = layer_index / max(1, layer_count - 1)
        parameters = _rex_juvenile_leaf_parameters(
            layer,
            progress,
            layer_index,
        )
        azimuth_rad = math.radians(
            resolved.base_angle_deg + rank_index * resolved.golden_angle_deg
        )
        elevation_rad = math.radians(parameters["elevation_deg"])
        base_radius_m = parameters["base_radius_m"]
        origin_m = (
            base_radius_m * math.cos(azimuth_rad),
            base_radius_m * math.sin(azimuth_rad),
            parameters["base_height_m"],
        )
        u_segments, v_segments = resolved.mesh_segments_for_layer(layer)
        mesh = build_rex_leaf_mesh(
            origin_m=origin_m,
            azimuth_rad=azimuth_rad,
            elevation_rad=elevation_rad,
            length_m=parameters["length_m"],
            width_m=parameters["width_m"],
            arch_strength_m=parameters["arch_strength_m"],
            cup_strength_m=parameters["cup_strength_m"],
            edge_curl_m=parameters["edge_curl_m"],
            tip_sag_m=parameters["tip_sag_m"],
            margin_wave_amplitude=parameters["margin_wave_amplitude"],
            margin_wave_phase_rad=rank_index * math.pi / 7.0,
            u_segments=u_segments,
            v_segments=v_segments,
            distal_lift_m=parameters["distal_lift_m"],
        )
        leaf_id = f"{resolved.plant_id}_leaf_{leaf_rank:03d}"
        faces = scientific_mesh_faces(
            plant_id=resolved.plant_id,
            leaf_id=leaf_id,
            leaf_rank=leaf_rank,
            leaf_layer=layer,
            vertices=mesh.vertices,
            triangle_indices=mesh.faces,
        )
        u_cuts, v_cuts = resolved.patch_cell_cuts_for_leaf_rank(
            leaf_rank,
            u_segments=u_segments,
            v_segments=v_segments,
        )
        patches = scientific_mesh_patches(
            plant_id=resolved.plant_id,
            leaf_id=leaf_id,
            leaf_rank=leaf_rank,
            leaf_layer=layer,
            faces=faces,
            u_segments=u_segments,
            v_segments=v_segments,
            patch_u_count=resolved.leaf_patch_u,
            patch_v_count=resolved.leaf_patch_v,
            u_cuts=u_cuts,
            v_cuts=v_cuts,
            surface_constrained_receivers=(
                resolved.uses_surface_constrained_receivers
            ),
        )
        leaves.append(
            LeafSurface(
                plant_id=resolved.plant_id,
                leaf_id=leaf_id,
                leaf_rank=leaf_rank,
                leaf_layer=layer,
                world_transform=LeafWorldTransform(
                    origin_m=origin_m,
                    azimuth_rad=azimuth_rad,
                    elevation_rad=elevation_rad,
                    horizontal_scale=1.0,
                    vertical_scale=1.0,
                    vertical_offset_m=0.0,
                ),
                azimuth_rad=azimuth_rad,
                elevation_rad=elevation_rad,
                length_m=parameters["length_m"],
                width_m=parameters["width_m"],
                nominal_thickness_m=resolved.nominal_leaf_thickness_m,
                vertices=mesh.vertices,
                triangle_indices=mesh.faces,
                faces=faces,
                patches=patches,
                radiance_material_id=resolved.radiance_material_id,
            )
        )
    plant = PlantMesh(
        plant_id=resolved.plant_id,
        config=resolved,
        seed=resolved.seed,
        leaves=tuple(leaves),
    )
    validate_plant_mesh(plant)
    return plant


def generate_plant_scene(
    config: PlantGeometryConfig | None = None,
    *,
    per_plant_overrides: Mapping[str, PlantRenderOverride] | None = None,
) -> PlantScene:
    """Generate a deterministic plant scene from explicit configuration."""

    resolved_config = config or PlantGeometryConfig()
    rng = random.Random(resolved_config.seed)
    plants: list[PlantGeometry] = []

    for row in range(resolved_config.plant_grid_rows):
        for column in range(resolved_config.plant_grid_columns):
            plant_id = _plant_id(row, column)
            center_m = _plant_center(resolved_config, row, column)
            plant_config = _config_for_plant(
                resolved_config,
                plant_id,
                per_plant_overrides,
            )
            plant_rng = _rng_for_plant(
                rng,
                resolved_config,
                plant_id,
                per_plant_overrides,
            )
            leaves = _generate_leaves(
                plant_config,
                plant_rng,
                plant_id,
                center_m,
                variation_strength=_variation_strength(
                    plant_id,
                    per_plant_overrides,
                ),
            )
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


def _config_for_plant(
    config: PlantGeometryConfig,
    plant_id: str,
    per_plant_overrides: Mapping[str, PlantRenderOverride] | None,
) -> PlantGeometryConfig:
    override = _plant_override(plant_id, per_plant_overrides)
    if override is None:
        return config
    updates: dict[str, object] = {}
    if override.plant_height_m is not None:
        updates["plant_height_m"] = override.plant_height_m
    if override.canopy_radius_m is not None:
        updates["canopy_radius_m"] = override.canopy_radius_m
    if override.leaf_count_per_plant is not None:
        updates["leaf_count_per_plant"] = override.leaf_count_per_plant
    if override.growth_stage is not None:
        updates["growth_stage"] = override.growth_stage
    return replace(config, **updates) if updates else config


def _rng_for_plant(
    rng: random.Random,
    config: PlantGeometryConfig,
    plant_id: str,
    per_plant_overrides: Mapping[str, PlantRenderOverride] | None,
) -> random.Random:
    override = _plant_override(plant_id, per_plant_overrides)
    if override is None or override.seed_offset is None:
        return rng
    return random.Random(config.seed + override.seed_offset)


def _plant_override(
    plant_id: str,
    per_plant_overrides: Mapping[str, PlantRenderOverride] | None,
) -> PlantRenderOverride | None:
    if per_plant_overrides is None:
        return None
    override = per_plant_overrides.get(plant_id)
    if override is None:
        return None
    if not isinstance(override, PlantRenderOverride):
        raise ValueError("per_plant_overrides values must be PlantRenderOverride instances.")
    return override


def _variation_strength(
    plant_id: str,
    per_plant_overrides: Mapping[str, PlantRenderOverride] | None,
) -> float:
    override = _plant_override(plant_id, per_plant_overrides)
    if override is None or override.variation_strength is None:
        return 1.0
    value = float(override.variation_strength)
    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError("variation_strength must be finite and in 0..1.")
    return value


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
    *,
    variation_strength: float = 1.0,
) -> list[LeafGeometry]:
    leaves: list[LeafGeometry] = []
    growth_scale = 0.25 + 0.75 * config.growth_stage
    leaf_step_rad = (math.pi * 2.0) / config.leaf_count_per_plant
    jitter_limit_rad = leaf_step_rad * 0.28 * variation_strength

    for leaf_index in range(config.leaf_count_per_plant):
        base_azimuth_rad = leaf_index * leaf_step_rad
        azimuth_rad = base_azimuth_rad + rng.uniform(
            -jitter_limit_rad, jitter_limit_rad
        )
        tilt_deg = _varied_range_value(
            rng,
            config.leaf_tilt_range_deg,
            variation_strength=variation_strength,
        )
        tilt_rad = math.radians(tilt_deg)
        raw_length_m = (
            _varied_range_value(
                rng,
                config.leaf_length_range_m,
                variation_strength=variation_strength,
            )
            * growth_scale
        )
        width_m = (
            _varied_range_value(
                rng,
                config.leaf_width_range_m,
                variation_strength=variation_strength,
            )
            * growth_scale
        )
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


def _varied_range_value(
    rng: random.Random,
    value_range: tuple[float, float],
    *,
    variation_strength: float,
) -> float:
    midpoint = (float(value_range[0]) + float(value_range[1])) / 2.0
    sampled = rng.uniform(*value_range)
    return midpoint + (sampled - midpoint) * variation_strength


def _max_leaf_length_for_canopy(
    config: PlantGeometryConfig,
    tilt_rad: float,
) -> float:
    horizontal_scale = max(math.cos(tilt_rad), 0.15)
    return config.canopy_radius_m / horizontal_scale


def _plant_id(row: int, column: int) -> str:
    return f"plant_r{row:03d}_c{column:03d}"


def _rex_layer_assignment(
    rank_index: int,
    layer_counts: tuple[int, int, int],
) -> tuple[LeafLayer, int, int]:
    outer_count, mid_count, inner_count = layer_counts
    if rank_index < outer_count:
        return "outer", rank_index, outer_count
    if rank_index < outer_count + mid_count:
        return "mid", rank_index - outer_count, mid_count
    return "inner", rank_index - outer_count - mid_count, inner_count


def _rex_leaf_parameters(
    config: RexPlantConfig,
    layer: LeafLayer,
    progress: float,
    variation: float,
    rng: random.Random,
) -> dict[str, float]:
    radius = config.projected_diameter_m / 2.0
    height = config.plant_height_m
    if layer == "outer":
        elevation_deg = 8.0 + 9.0 * progress + rng.uniform(-3.0, 3.0)
        length = radius * (1.08 - 0.12 * progress) * variation
        width_ratio = 0.74 + rng.uniform(-0.035, 0.035)
        return {
            "elevation_deg": elevation_deg,
            "base_radius_m": radius
            * (0.020 + 0.018 * progress + rng.uniform(-0.006, 0.006)),
            "base_height_m": height
            * (0.030 + 0.008 * progress + rng.uniform(-0.012, 0.012)),
            "length_m": length,
            "width_m": length * width_ratio,
            "arch_strength_m": height * (0.070 + rng.uniform(-0.008, 0.008)),
            "cup_strength_m": height * 0.035,
            "edge_curl_m": height * 0.014,
            "tip_sag_m": height
            * (0.065 + 0.035 * progress + rng.uniform(0.0, 0.012)),
            "margin_wave_amplitude": 0.040,
        }
    if layer == "mid":
        elevation_deg = 30.0 + 17.0 * progress + rng.uniform(-5.0, 5.0)
        length = radius * (0.88 - 0.13 * progress) * variation
        width_ratio = 0.75 + rng.uniform(-0.030, 0.030)
        return {
            "elevation_deg": elevation_deg,
            "base_radius_m": radius
            * (0.022 + 0.022 * progress + rng.uniform(-0.008, 0.008)),
            "base_height_m": height
            * (0.055 + 0.025 * progress + rng.uniform(-0.025, 0.025)),
            "length_m": length,
            "width_m": length * width_ratio,
            "arch_strength_m": height * (0.100 + rng.uniform(-0.012, 0.012)),
            "cup_strength_m": height * 0.075,
            "edge_curl_m": height * 0.020,
            "tip_sag_m": height * 0.012,
            "margin_wave_amplitude": 0.030,
        }
    elevation_deg = 68.0 + 14.0 * progress + rng.uniform(-3.0, 3.0)
    elevation_rad = math.radians(elevation_deg)
    length = (
        height
        * (0.72 - 0.08 * progress)
        / max(math.sin(elevation_rad), 0.70)
        * variation
    )
    width_ratio = 0.76 + rng.uniform(-0.025, 0.025)
    return {
        "elevation_deg": elevation_deg,
        "base_radius_m": radius
        * (0.010 + 0.012 * progress + rng.uniform(-0.004, 0.004)),
        "base_height_m": height
        * (0.080 + 0.045 * progress + rng.uniform(-0.015, 0.015)),
        "length_m": length,
        "width_m": length * width_ratio,
        "arch_strength_m": height * 0.120,
        "cup_strength_m": height * 0.120,
        "edge_curl_m": height * 0.050,
        "tip_sag_m": 0.0,
        "margin_wave_amplitude": 0.015,
    }


def _rex_juvenile_leaf_parameters(
    layer: LeafLayer,
    progress: float,
    layer_index: int,
) -> dict[str, float]:
    """Return direct, fixed-rank morphology for the static juvenile profile."""

    if layer == "outer":
        length_m = 0.087 - 0.0015 * progress
        return {
            "elevation_deg": 11.0 + 5.0 * progress,
            "base_radius_m": 0.0005 + 0.0002 * progress,
            "base_height_m": 0.0004 + 0.0006 * progress,
            "length_m": length_m,
            "width_m": length_m * (0.70 - 0.02 * progress),
            "arch_strength_m": 0.008 + 0.002 * progress,
            "cup_strength_m": 0.0035,
            "edge_curl_m": 0.0015,
            "tip_sag_m": 0.011 - 0.002 * progress,
            "distal_lift_m": 0.0,
            "margin_wave_amplitude": 0.025,
        }
    if layer == "mid":
        middle_ranks = (
            (0.0700, 0.66, 28.0, 0.0090, 0.0018, 0.0013, 0.0018, 0.0030),
            (0.0675, 0.65, 36.0, 0.0115, 0.0026, 0.0016, 0.0008, 0.0045),
            (0.0685, 0.67, 32.0, 0.0085, 0.0016, 0.0012, 0.0020, 0.0025),
            (0.0660, 0.64, 40.0, 0.0125, 0.0030, 0.0018, 0.0010, 0.0050),
        )
        (
            length_m,
            width_ratio,
            elevation_deg,
            arch_strength_m,
            cup_strength_m,
            edge_curl_m,
            tip_sag_m,
            distal_lift_m,
        ) = middle_ranks[layer_index]
        return {
            "elevation_deg": elevation_deg,
            "base_radius_m": 0.0008 + 0.0004 * progress,
            "base_height_m": 0.0015 + 0.0012 * progress,
            "length_m": length_m,
            "width_m": length_m * width_ratio,
            "arch_strength_m": arch_strength_m,
            "cup_strength_m": cup_strength_m,
            "edge_curl_m": edge_curl_m,
            "tip_sag_m": tip_sag_m,
            "distal_lift_m": distal_lift_m,
            "margin_wave_amplitude": 0.020,
        }
    inner_ranks = (
        (0.052, 0.80, 44.0, 0.0002, 0.0034, 0.015, 0.0025, 0.0012, 0.0012, 0.010),
        (0.049, 0.76, 52.0, 0.0007, 0.0046, 0.017, 0.0030, 0.0016, 0.0008, 0.012),
        (0.047, 0.83, 47.0, 0.0004, 0.0038, 0.013, 0.0023, 0.0011, 0.0015, 0.009),
    )
    (
        length_m,
        width_ratio,
        elevation_deg,
        base_radius_m,
        base_height_m,
        arch_strength_m,
        cup_strength_m,
        edge_curl_m,
        tip_sag_m,
        distal_lift_m,
    ) = inner_ranks[layer_index]
    return {
        "elevation_deg": elevation_deg,
        "base_radius_m": base_radius_m,
        "base_height_m": base_height_m,
        "length_m": length_m,
        "width_m": length_m * width_ratio,
        "arch_strength_m": arch_strength_m,
        "cup_strength_m": cup_strength_m,
        "edge_curl_m": edge_curl_m,
        "tip_sag_m": tip_sag_m,
        "distal_lift_m": distal_lift_m,
        "margin_wave_amplitude": 0.012,
    }


def _rex_envelope_transform(
    drafts: list[_RexLeafDraft],
    *,
    target_diameter_m: float,
    target_height_m: float,
) -> tuple[float, float, float]:
    vertices = [vertex for draft in drafts for vertex in draft.vertices]
    if not vertices:
        raise ValueError("Rex plant generation produced no vertices.")
    max_radius = max(math.hypot(vertex[0], vertex[1]) for vertex in vertices)
    minimum_z = min(vertex[2] for vertex in vertices)
    maximum_z = max(vertex[2] for vertex in vertices)
    if max_radius <= 0.0 or maximum_z <= minimum_z:
        raise ValueError("Rex plant generation produced an invalid envelope.")
    return (
        (target_diameter_m / 2.0) / max_radius,
        target_height_m / (maximum_z - minimum_z),
        -minimum_z,
    )
