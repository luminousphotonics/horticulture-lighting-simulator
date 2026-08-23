"""Authenticated Stage A PPFD metrics at deterministic physical-leaf positions."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Mapping, Sequence

from fspm_optics.geometry.coordinate_frame import (
    ROOM_FRAME_POLICY_ID,
    RoomCoordinateFrame,
)
from fspm_optics.plants.generator import generate_rex_juvenile_preheading_plant
from fspm_optics.plants.models import PlantMesh, ScientificMeshFace, Vector3
from fspm_optics.plants.natural_fit import NaturalFitLayoutPlan
from fspm_optics.plants.rex_juvenile import (
    REX_JUVENILE_PREHEADING_PROFILE_ID,
    RexJuvenilePreheadingConfig,
)
from fspm_optics.transport.scalar_ppfd import PpfdMapSample

from .domain import (
    CONVENTIONAL_SYSTEM_ID,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
)
from .visualization import detect_regular_grid, reference_plane_field_identity

BASELINE_LEAF_UNIFORMITY_SCHEMA_ID = (
    "fspm-optics.baseline-leaf-position-uniformity"
)
BASELINE_LEAF_UNIFORMITY_SCHEMA_VERSION = 1
BASELINE_LEAF_UNIFORMITY_FILENAME = (
    "baseline-leaf-position-uniformity.v1.json"
)
CENTROID_POLICY_ID = "one_sided_triangle_area_weighted_leaf_centroid_v1"
INTERPOLATION_POLICY_ID = (
    "stage_a_regular_grid_bilinear_half_cell_clamp_v1"
)
COORDINATE_TRANSFORM_POLICY_ID = (
    ROOM_FRAME_POLICY_ID
)
LEAF_ORDERING = "plant-major; canonical-leaf order"
OUTSIDE_SUPPORT_REASON = "leaf_representative_position_outside_stage_a_support"
ZERO_MEAN_REASON = "leaf_position_ppfd_mean_zero"
BASELINE_PHYSICAL_LEAF_SCENE_SCHEMA_ID = (
    "fspm-optics.baseline-physical-leaf-scene"
)
BASELINE_PHYSICAL_LEAF_SCENE_SCHEMA_VERSION = 1
TARGET_COVERAGE_SCHEMA_ID = "fspm-optics.viewer-target-coverage"
TARGET_COVERAGE_SCHEMA_VERSION = 1
TARGET_CLASSIFICATION_BASIS = "canopy_plane_equivalent_incident_ppfd"
TARGET_CLASSIFICATION_SOURCE = "interpolated_runtime_ppfd_map"
TARGET_COVERAGE_LIMITATION = (
    "Baseline canopy-plane coverage evaluated at authenticated leaf locations—"
    "not measured leaf incident PAR."
)
TARGET_COVERAGE_DEVIATION_FORMULA = (
    "(coverage_ppfd - reference_ppfd) / tolerance_ppfd"
)
TARGET_COVERAGE_PALETTE = (
    (-4.0, "blue", "#2563EB"),
    (-2.0, "cyan", "#06B6D4"),
    (-1.5, "teal", "#14B8A6"),
    (-1.0, "green", "#22C55E"),
    (1.0, "green", "#22C55E"),
    (2.0, "yellow-green", "#A3E635"),
    (4.0, "yellow-orange", "#F59E0B"),
    (6.0, "red", "#DC2626"),
)


class BaselineLeafUniformityError(ValueError):
    """An authenticated baseline-leaf derivative cannot be constructed."""


class LeafPositionOutsideSupport(BaselineLeafUniformityError):
    """A representative leaf position lies outside the Stage A support."""


@dataclass(frozen=True, slots=True)
class BaselineLeafTargetPolicy:
    system_id: str
    mode: str
    source: str
    requested_lighting_target_umol_m2_s: float | None
    achieved_stage_a_mean_umol_m2_s: float
    override_umol_m2_s: float | None
    resolved_target_umol_m2_s: float
    tolerance_umol_m2_s: float
    inclusive_lower_umol_m2_s: float
    inclusive_upper_umol_m2_s: float

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_id": "fspm-optics.baseline-leaf-target-policy",
            "schema_version": 1,
            "system_id": self.system_id,
            "mode": self.mode,
            "source": self.source,
            "inputs": {
                "requested_lighting_target_umol_m2_s": (
                    self.requested_lighting_target_umol_m2_s
                ),
                "achieved_stage_a_mean_umol_m2_s": (
                    self.achieved_stage_a_mean_umol_m2_s
                ),
                "override_umol_m2_s": self.override_umol_m2_s,
                "tolerance_umol_m2_s": self.tolerance_umol_m2_s,
            },
            "resolved_target_umol_m2_s": self.resolved_target_umol_m2_s,
            "classification_range": {
                "lower_umol_m2_s": self.inclusive_lower_umol_m2_s,
                "upper_umol_m2_s": self.inclusive_upper_umol_m2_s,
                "bounds": "inclusive",
            },
            "classification_comparisons": {
                "under_lit": "ppfd < lower",
                "target_range": "lower <= ppfd <= upper",
                "over_lit": "ppfd > upper",
                "epsilon_applied": False,
            },
            "affects_stage_a_transport": False,
            "depends_on_stage_b": False,
        }


@dataclass(frozen=True, slots=True)
class LeafRepresentativePosition:
    plant_index: int
    plant_id: str
    local_leaf_index: int
    leaf_id: str
    canonical_leaf_id: str
    leaf_rank: int
    leaf_layer: str
    global_leaf_index: int
    aligned_x_m: float
    aligned_y_m: float
    requested_x_m: float
    requested_y_m: float


@dataclass(frozen=True, slots=True)
class BaselinePhysicalLeafScene:
    """Natural-fit physical geometry before any receiver expansion."""

    natural_fit: NaturalFitLayoutPlan
    canonical_plant: PlantMesh
    canonical_topology_sha256: str
    leaf_geometry_identity_sha256: str
    scene_hash: str
    scene_id: str

    @property
    def profile_id(self) -> str:
        return self.natural_fit.profile_id

    @property
    def layout_plan_hash(self) -> str:
        return self.natural_fit.plan_hash

    @property
    def layout_policy_id(self) -> str:
        return self.natural_fit.policy.policy_id

    @property
    def plant_count(self) -> int:
        return self.natural_fit.total_count

    @property
    def leaf_count(self) -> int:
        return self.plant_count * len(self.canonical_plant.leaves)


def build_baseline_physical_leaf_scene(
    natural_fit: NaturalFitLayoutPlan,
    *,
    canonical_plant: PlantMesh | None = None,
) -> BaselinePhysicalLeafScene:
    """Build physical-leaf sampling geometry without constructing receivers."""

    if not isinstance(natural_fit, NaturalFitLayoutPlan):
        raise BaselineLeafUniformityError(
            "baseline-leaf geometry requires an authenticated Natural-fit plan."
        )
    if natural_fit.profile_id != REX_JUVENILE_PREHEADING_PROFILE_ID:
        raise BaselineLeafUniformityError(
            "Natural-fit profile is not the authorized juvenile geometry."
        )
    plant = canonical_plant or generate_rex_juvenile_preheading_plant(
        RexJuvenilePreheadingConfig()
    )
    if (
        not isinstance(plant, PlantMesh)
        or getattr(plant.config, "profile_id", None) != natural_fit.profile_id
        or not plant.leaves
    ):
        raise BaselineLeafUniformityError(
            "canonical plant does not match the authenticated Natural-fit profile."
        )
    canonical_leaves = _canonical_leaf_geometry_payload(plant)
    canonical_topology_sha256 = _hash_json(
        {
            "profile_id": natural_fit.profile_id,
            "ordered_leaves": canonical_leaves,
        }
    )
    leaf_geometry_identity_sha256 = _hash_json(
        {
            "profile_id": natural_fit.profile_id,
            "layout_plan_hash": natural_fit.plan_hash,
            "ordering": LEAF_ORDERING,
            "canonical_leaves": canonical_leaves,
            "plants": [
                {
                    "plant_index": plant_index,
                    "plant_id": placement.plant_id,
                    "origin_m": [
                        placement.aligned_x_m,
                        placement.aligned_y_m,
                        0.0,
                    ],
                }
                for plant_index, placement in enumerate(natural_fit.plants)
            ],
        }
    )
    scene_hash = _hash_json(
        {
            "schema_id": BASELINE_PHYSICAL_LEAF_SCENE_SCHEMA_ID,
            "schema_version": BASELINE_PHYSICAL_LEAF_SCENE_SCHEMA_VERSION,
            "profile_id": natural_fit.profile_id,
            "layout_plan_hash": natural_fit.plan_hash,
            "canonical_topology_sha256": canonical_topology_sha256,
            "leaf_geometry_identity_sha256": leaf_geometry_identity_sha256,
            "plant_count": natural_fit.total_count,
            "physical_leaf_count": (
                natural_fit.total_count * len(plant.leaves)
            ),
            "ordering": LEAF_ORDERING,
        }
    )
    return BaselinePhysicalLeafScene(
        natural_fit=natural_fit,
        canonical_plant=plant,
        canonical_topology_sha256=canonical_topology_sha256,
        leaf_geometry_identity_sha256=leaf_geometry_identity_sha256,
        scene_hash=scene_hash,
        scene_id=f"baseline-physical-leaf-scene-v1-{scene_hash}",
    )


@dataclass(frozen=True, slots=True)
class StageAInterpolationField:
    x_centers_m: tuple[float, ...]
    y_centers_m: tuple[float, ...]
    values: tuple[tuple[float, ...], ...]
    reference_plane_z_m: float
    x_step_m: float
    y_step_m: float
    supported_extent_m: tuple[float, float, float, float]
    field_identity_sha256: str
    sample_count: int

    @classmethod
    def from_samples(
        cls, samples: Sequence[PpfdMapSample]
    ) -> "StageAInterpolationField":
        ordered = tuple(samples)
        try:
            grid = detect_regular_grid(ordered)
            identity = reference_plane_field_identity(ordered)
        except ValueError as exc:
            raise BaselineLeafUniformityError(str(exc)) from exc
        xs = tuple(grid.x_centers_m)
        ys = tuple(grid.y_centers_m)
        if len(xs) < 2 or len(ys) < 2:
            raise BaselineLeafUniformityError(
                "baseline-leaf interpolation requires at least two centers per axis."
            )
        expected_xy = tuple((x, y) for y in ys for x in xs)
        actual_xy = tuple((sample.x_m, sample.y_m) for sample in ordered)
        if actual_xy != expected_xy:
            raise BaselineLeafUniformityError(
                "Stage A samples must retain deterministic Y-major/X-minor order."
            )
        x_step = (xs[-1] - xs[0]) / (len(xs) - 1)
        y_step = (ys[-1] - ys[0]) / (len(ys) - 1)
        if (
            not math.isfinite(x_step)
            or not math.isfinite(y_step)
            or x_step <= 0.0
            or y_step <= 0.0
        ):
            raise BaselineLeafUniformityError(
                "Stage A interpolation steps must be finite and positive."
            )
        return cls(
            x_centers_m=xs,
            y_centers_m=ys,
            values=tuple(
                tuple(float(value) for value in row)
                for row in grid.values.tolist()
            ),
            reference_plane_z_m=float(grid.z_m),
            x_step_m=x_step,
            y_step_m=y_step,
            supported_extent_m=(
                xs[0] - x_step / 2.0,
                xs[-1] + x_step / 2.0,
                ys[0] - y_step / 2.0,
                ys[-1] + y_step / 2.0,
            ),
            field_identity_sha256=identity,
            sample_count=len(ordered),
        )

    def sample(self, x_m: float, y_m: float) -> float:
        x = _finite("field sample x_m", x_m)
        y = _finite("field sample y_m", y_m)
        x_min, x_max, y_min, y_max = self.supported_extent_m
        if x < x_min or x > x_max or y < y_min or y > y_max:
            raise LeafPositionOutsideSupport(
                "leaf representative position is outside Stage A support."
            )
        clamped_x = min(max(x, self.x_centers_m[0]), self.x_centers_m[-1])
        clamped_y = min(max(y, self.y_centers_m[0]), self.y_centers_m[-1])
        x_index = bisect_left(self.x_centers_m, clamped_x)
        y_index = bisect_left(self.y_centers_m, clamped_y)
        if (
            x_index < len(self.x_centers_m)
            and y_index < len(self.y_centers_m)
            and self.x_centers_m[x_index] == clamped_x
            and self.y_centers_m[y_index] == clamped_y
        ):
            return self.values[y_index][x_index]
        x0_index, x1_index, x_fraction = _axis_bracket(
            self.x_centers_m, clamped_x
        )
        y0_index, y1_index, y_fraction = _axis_bracket(
            self.y_centers_m, clamped_y
        )
        lower = math.fsum(
            (
                self.values[y0_index][x0_index] * (1.0 - x_fraction),
                self.values[y0_index][x1_index] * x_fraction,
            )
        )
        upper = math.fsum(
            (
                self.values[y1_index][x0_index] * (1.0 - x_fraction),
                self.values[y1_index][x1_index] * x_fraction,
            )
        )
        return math.fsum(
            (lower * (1.0 - y_fraction), upper * y_fraction)
        )

    def to_dict(self) -> dict[str, object]:
        x_min, x_max, y_min, y_max = self.supported_extent_m
        return {
            "source_stage": "stage_a_baseline_ppfd",
            "source_artifact": "ppfd.csv",
            "field_identity_sha256": self.field_identity_sha256,
            "identity_encoding": (
                "authoritative-order little-endian Float64 x_m,y_m,z_m,ppfd_umol_m2_s"
            ),
            "sample_count": self.sample_count,
            "reference_plane_z_m": self.reference_plane_z_m,
            "x_centers_m": list(self.x_centers_m),
            "y_centers_m": list(self.y_centers_m),
            "x_step_m": self.x_step_m,
            "y_step_m": self.y_step_m,
            "supported_extent_m": {
                "x_min": x_min,
                "x_max": x_max,
                "y_min": y_min,
                "y_max": y_max,
                "bounds": "inclusive",
            },
            "sample_ordering": "Y-major/X-minor",
            "value_units": "umol/m^2/s",
        }


@dataclass(frozen=True, slots=True)
class BaselineLeafUniformityPublication:
    payload: Mapping[str, object]
    data: bytes
    sha256: str
    target_coverage: Mapping[str, object]

    @property
    def derivation_identity_sha256(self) -> str:
        return str(self.payload["derivation_identity_sha256"])

    def metrics_payload(self) -> dict[str, object]:
        return {
            "available": self.payload["available"],
            "unavailable_reason_code": self.payload["unavailable_reason_code"],
            "artifact": BASELINE_LEAF_UNIFORMITY_FILENAME,
            "artifact_sha256": self.sha256,
            "derivation_identity_sha256": self.derivation_identity_sha256,
            "physical_leaf_count": self.payload["physical_leaf_count"],
            "target_policy": self.payload["target_policy"],
            "summary": self.payload["summary"],
        }

    def manifest_payload(self) -> dict[str, object]:
        return {
            "schema_id": BASELINE_LEAF_UNIFORMITY_SCHEMA_ID,
            "schema_version": BASELINE_LEAF_UNIFORMITY_SCHEMA_VERSION,
            "artifact": BASELINE_LEAF_UNIFORMITY_FILENAME,
            "artifact_sha256": self.sha256,
            "artifact_byte_length": len(self.data),
            "derivation_identity_sha256": self.derivation_identity_sha256,
            "available": self.payload["available"],
            "unavailable_reason_code": self.payload["unavailable_reason_code"],
            "physical_leaf_count": self.payload["physical_leaf_count"],
        }


def resolve_baseline_leaf_target_policy(
    *,
    system_id: str,
    mode: str,
    requested_lighting_target_umol_m2_s: float | None,
    achieved_stage_a_mean_umol_m2_s: float,
    tolerance_umol_m2_s: float,
    override_umol_m2_s: float | None,
) -> BaselineLeafTargetPolicy:
    if system_id not in {
        PROPOSED_SYSTEM_ID,
        CONVENTIONAL_SYSTEM_ID,
        HPS_SYSTEM_ID,
    }:
        raise BaselineLeafUniformityError("baseline-leaf system is unsupported.")
    achieved = _positive(
        "achieved_stage_a_mean_umol_m2_s", achieved_stage_a_mean_umol_m2_s
    )
    tolerance = _positive("tolerance_umol_m2_s", tolerance_umol_m2_s)
    normalized_mode = str(mode).strip().lower()
    requested = (
        None
        if requested_lighting_target_umol_m2_s is None
        else _positive(
            "requested_lighting_target_umol_m2_s",
            requested_lighting_target_umol_m2_s,
        )
    )
    if normalized_mode == "override":
        if override_umol_m2_s is None:
            raise BaselineLeafUniformityError(
                "baseline-leaf override mode requires a target."
            )
        override = _positive("override_umol_m2_s", override_umol_m2_s)
        resolved = override
        source = "authenticated_fspm_override"
    elif normalized_mode == "automatic":
        if override_umol_m2_s is not None:
            raise BaselineLeafUniformityError(
                "automatic baseline-leaf mode must not include an override."
            )
        override = None
        if system_id == HPS_SYSTEM_ID:
            if requested is not None:
                raise BaselineLeafUniformityError(
                    "targetless HPS must not include a requested lighting target."
                )
            resolved = achieved
            source = "achieved_stage_a_baseline_mean"
        else:
            if requested is None:
                raise BaselineLeafUniformityError(
                    "automatic LED baseline-leaf policy requires the requested target."
                )
            resolved = requested
            source = "requested_lighting_target"
    else:
        raise BaselineLeafUniformityError(
            "baseline-leaf target mode must be automatic or override."
        )
    return BaselineLeafTargetPolicy(
        system_id=system_id,
        mode=normalized_mode,
        source=source,
        requested_lighting_target_umol_m2_s=requested,
        achieved_stage_a_mean_umol_m2_s=achieved,
        override_umol_m2_s=override,
        resolved_target_umol_m2_s=resolved,
        tolerance_umol_m2_s=tolerance,
        inclusive_lower_umol_m2_s=max(0.0, resolved - tolerance),
        inclusive_upper_umol_m2_s=resolved + tolerance,
    )


def leaf_representative_positions(
    scene: BaselinePhysicalLeafScene,
) -> tuple[LeafRepresentativePosition, ...]:
    canonical_centroids = canonical_leaf_representative_centroids(
        scene.canonical_plant
    )
    positions: list[LeafRepresentativePosition] = []
    leaves_per_plant = len(scene.canonical_plant.leaves)
    for plant_index, placement in enumerate(scene.natural_fit.plants):
        for local_leaf_index, leaf in enumerate(scene.canonical_plant.leaves):
            centroid = canonical_centroids[local_leaf_index]
            aligned_x = centroid[0] + placement.aligned_x_m
            aligned_y = centroid[1] + placement.aligned_y_m
            requested_x, requested_y, _ = (
                scene.natural_fit.coordinate_frame.simulation_to_requested_position(
                    (aligned_x, aligned_y, centroid[2])
                )
            )
            leaf_id = f"{placement.plant_id}__{leaf.leaf_id}"
            if not math.isfinite(requested_x) or not math.isfinite(requested_y):
                raise BaselineLeafUniformityError(
                    f"leaf representative position is non-finite: {leaf_id}"
                )
            positions.append(
                LeafRepresentativePosition(
                    plant_index=plant_index,
                    plant_id=placement.plant_id,
                    local_leaf_index=local_leaf_index,
                    leaf_id=leaf_id,
                    canonical_leaf_id=leaf.leaf_id,
                    leaf_rank=leaf.leaf_rank,
                    leaf_layer=leaf.leaf_layer,
                    global_leaf_index=(
                        plant_index * leaves_per_plant + local_leaf_index
                    ),
                    aligned_x_m=aligned_x,
                    aligned_y_m=aligned_y,
                    requested_x_m=requested_x,
                    requested_y_m=requested_y,
                )
            )
    if len(positions) != scene.leaf_count or tuple(
        item.global_leaf_index for item in positions
    ) != tuple(range(scene.leaf_count)):
        raise BaselineLeafUniformityError(
            "physical-leaf count or plant-major ordering is inconsistent."
        )
    return tuple(positions)


def canonical_leaf_representative_centroids(
    plant: PlantMesh,
) -> tuple[Vector3, ...]:
    """Return authoritative representative centroids in canonical-leaf order."""

    return tuple(
        one_sided_area_weighted_centroid(leaf.faces, leaf_id=leaf.leaf_id)
        for leaf in plant.leaves
    )


def one_sided_area_weighted_centroid(
    faces: Sequence[ScientificMeshFace],
    *,
    leaf_id: str = "leaf",
) -> Vector3:
    """Return one physical leaf centroid from its authoritative triangles."""

    ordered = tuple(faces)
    if not ordered:
        raise BaselineLeafUniformityError(
            f"authoritative leaf has no triangles: {leaf_id}"
        )
    areas = tuple(_positive("triangle area_m2", face.area_m2) for face in ordered)
    total_area = math.fsum(areas)
    if not math.isfinite(total_area) or total_area <= 0.0:
        raise BaselineLeafUniformityError(
            f"authoritative leaf has invalid one-sided area: {leaf_id}"
        )
    return tuple(
        math.fsum(
            area * _finite("triangle centroid", face.centroid[axis])
            for area, face in zip(areas, ordered, strict=True)
        )
        / total_area
        for axis in range(3)
    )


def build_baseline_leaf_uniformity_publication(
    *,
    run_id: str,
    system_id: str,
    analysis_scope: str,
    samples: Sequence[PpfdMapSample],
    scene: BaselinePhysicalLeafScene,
    overlay_payload: Mapping[str, object],
    requested_lighting_target_umol_m2_s: float | None,
    achieved_stage_a_mean_umol_m2_s: float,
    target_mode: str,
    target_override_umol_m2_s: float | None,
    target_tolerance_umol_m2_s: float,
) -> BaselineLeafUniformityPublication:
    if analysis_scope not in {
        "baseline_ppfd",
        "baseline_plus_multispectral_fspm",
    }:
        raise BaselineLeafUniformityError(
            "baseline-leaf analysis scope is unsupported."
        )
    if not isinstance(scene, BaselinePhysicalLeafScene):
        raise BaselineLeafUniformityError(
            "baseline-leaf physical scene is invalid."
        )
    field = StageAInterpolationField.from_samples(samples)
    target = resolve_baseline_leaf_target_policy(
        system_id=system_id,
        mode=target_mode,
        requested_lighting_target_umol_m2_s=(
            requested_lighting_target_umol_m2_s
        ),
        achieved_stage_a_mean_umol_m2_s=achieved_stage_a_mean_umol_m2_s,
        tolerance_umol_m2_s=target_tolerance_umol_m2_s,
        override_umol_m2_s=target_override_umol_m2_s,
    )
    positions = leaf_representative_positions(scene)
    transform = _coordinate_transform(
        overlay_payload,
        scene.natural_fit.coordinate_frame,
    )
    leaf_geometry_identity = scene.leaf_geometry_identity_sha256
    records: list[dict[str, object]] = []
    outside: dict[str, object] | None = None
    values: list[float] = []
    for position in positions:
        field_x, field_y = position.aligned_x_m, position.aligned_y_m
        try:
            ppfd = field.sample(field_x, field_y)
        except LeafPositionOutsideSupport:
            outside = {
                "global_leaf_index": position.global_leaf_index,
                "leaf_id": position.leaf_id,
                "requested_representative_xy_m": [
                    position.requested_x_m,
                    position.requested_y_m,
                ],
                "aligned_simulation_xy_m": [
                    position.aligned_x_m,
                    position.aligned_y_m,
                ],
                "field_sampling_xy_m": [field_x, field_y],
            }
            records = []
            values = []
            break
        classification = classify_baseline_leaf_ppfd(ppfd, target)
        values.append(ppfd)
        records.append(
            {
                "plant_index": position.plant_index,
                "plant_id": position.plant_id,
                "local_leaf_index": position.local_leaf_index,
                "local_leaf_id": position.canonical_leaf_id,
                "leaf_id": position.leaf_id,
                "canonical_leaf_id": position.canonical_leaf_id,
                "leaf_rank": position.leaf_rank,
                "leaf_layer": position.leaf_layer,
                "global_leaf_index": position.global_leaf_index,
                "requested_representative_xy_m": [
                    position.requested_x_m,
                    position.requested_y_m,
                ],
                "aligned_simulation_xy_m": [
                    position.aligned_x_m,
                    position.aligned_y_m,
                ],
                "field_sampling_xy_m": [field_x, field_y],
                "interpolated_ppfd_umol_m2_s": ppfd,
                "classification": classification,
            }
        )
    available = outside is None
    summary = (
        aggregate_leaf_position_ppfd(values, target)
        if available
        else _unavailable_summary(len(positions), OUTSIDE_SUPPORT_REASON)
    )
    interpolation_policy = {
        "policy_id": INTERPOLATION_POLICY_ID,
        "version": 1,
        "value_basis": "Float64 scalar PPFD",
        "method": "bilinear",
        "exact_grid_centers_preserved": True,
        "supported_extent": "sample-center rectangle expanded by half a grid step",
        "edge_band_behavior": "clamp to nearest center",
        "outside_behavior": "complete metric group unavailable",
        "epsilon_extrapolation": False,
        "color_interpolation": False,
    }
    centroid_policy = {
        "policy_id": CENTROID_POLICY_ID,
        "version": 1,
        "formula": "sum(triangle_area * triangle_centroid) / sum(triangle_area)",
        "summation": "math.fsum",
        "area_basis": "physical one-sided authoritative triangles",
        "projected_axes": "requested-scene scientific XY",
        "receiver_or_patch_geometry_used": False,
    }
    derivation_payload: dict[str, object] = {
        "stage_a_field_identity_sha256": field.field_identity_sha256,
        "leaf_geometry_identity_sha256": leaf_geometry_identity,
        "coordinate_transform": transform,
        "centroid_policy": centroid_policy,
        "interpolation_policy": interpolation_policy,
        "target_policy": target.to_dict(),
        "leaf_ordering": LEAF_ORDERING,
        "physical_leaf_count": len(positions),
        "available": available,
        "unavailable_reason_code": None if available else OUTSIDE_SUPPORT_REASON,
        "ordered_leaf_values": records if available else [],
        "unavailable_details": outside,
    }
    payload: dict[str, object] = {
        "schema_id": BASELINE_LEAF_UNIFORMITY_SCHEMA_ID,
        "schema_version": BASELINE_LEAF_UNIFORMITY_SCHEMA_VERSION,
        "run_id": run_id,
        "system_id": system_id,
        "analysis_scope": analysis_scope,
        "available": available,
        "unavailable_reason_code": None if available else OUTSIDE_SUPPORT_REASON,
        "unavailable_details": outside,
        "stage_a_field": field.to_dict(),
        "natural_fit": {
            "layout_plan_hash": scene.layout_plan_hash,
            "layout_policy_id": scene.layout_policy_id,
        },
        "juvenile_scene": {
            "schema_id": BASELINE_PHYSICAL_LEAF_SCENE_SCHEMA_ID,
            "schema_version": BASELINE_PHYSICAL_LEAF_SCENE_SCHEMA_VERSION,
            "scene_id": scene.scene_id,
            "scene_hash": scene.scene_hash,
            "profile_id": scene.profile_id,
            "canonical_topology_sha256": scene.canonical_topology_sha256,
            "receiver_expansion_used_by_derivative": False,
        },
        "leaf_geometry_identity_sha256": leaf_geometry_identity,
        "physical_leaf_count": len(positions),
        "coordinate_transform": transform,
        "centroid_policy": centroid_policy,
        "interpolation_policy": interpolation_policy,
        "target_policy": target.to_dict(),
        "aggregation": {
            "weighting": "equal_physical_leaf_weight",
            "denominator_policy": "all_authenticated_physical_leaves",
            "denominator_leaf_count": len(positions),
            "partial_denominator_allowed": False,
            "leaf_ordering": LEAF_ORDERING,
        },
        "summary": summary,
        "records": records,
        "derivation_identity_sha256": _hash_json(derivation_payload),
        "scientific_dependencies": {
            "stage_b": False,
            "multispectral_bands": False,
            "far_red_selection": False,
            "leaf_surface_receivers": False,
            "phase27g_c_surface_light": False,
            "surface_flux_calibration": False,
        },
    }
    data = _json_bytes(payload)
    target_coverage = _target_coverage_contract(
        scene=scene,
        field=field,
        target=target,
        transform=transform,
        available=available,
    )
    return BaselineLeafUniformityPublication(
        payload=payload,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        target_coverage=target_coverage,
    )


def _target_coverage_contract(
    *,
    scene: BaselinePhysicalLeafScene,
    field: StageAInterpolationField,
    target: BaselineLeafTargetPolicy,
    transform: Mapping[str, object],
    available: bool,
) -> dict[str, object]:
    centroids = canonical_leaf_representative_centroids(scene.canonical_plant)
    return {
        "schema_id": TARGET_COVERAGE_SCHEMA_ID,
        "schema_version": TARGET_COVERAGE_SCHEMA_VERSION,
        "availability": "available" if available else "unavailable",
        "unavailable_reason_code": None if available else OUTSIDE_SUPPORT_REASON,
        "target_classification_basis": TARGET_CLASSIFICATION_BASIS,
        "target_classification_source": TARGET_CLASSIFICATION_SOURCE,
        "scientific_limitation": TARGET_COVERAGE_LIMITATION,
        "system_id": target.system_id,
        "reference": {
            "ppfd_umol_m2_s": target.resolved_target_umol_m2_s,
            "source": target.source,
            "policy_mode": target.mode,
        },
        "tolerance_ppfd_umol_m2_s": target.tolerance_umol_m2_s,
        "deviation_formula": TARGET_COVERAGE_DEVIATION_FORMULA,
        "target_band_deviation": {
            "minimum": -1.0,
            "maximum": 1.0,
            "bounds": "inclusive",
        },
        "palette": {
            "palette_id": "target-coverage-deviation-8-anchor-v1",
            "continuous_interpolation": True,
            "clamp_below_deviation": -4.0,
            "clamp_above_deviation": 6.0,
            "anchors": [
                {
                    "deviation": deviation,
                    "color_name": color_name,
                    "srgb_hex": srgb_hex,
                }
                for deviation, color_name, srgb_hex in TARGET_COVERAGE_PALETTE
            ],
        },
        "sampling": {
            "interpolation_policy_id": INTERPOLATION_POLICY_ID,
            "method": "manual_four_cell_bilinear",
            "supported_extent": (
                "sample-center rectangle expanded by half a grid step"
            ),
            "edge_band_behavior": "clamp to nearest center",
            "outside_behavior": "target coverage unavailable",
            "support_bounds_m": {
                "x_min": field.x_centers_m[0]
                - (field.x_centers_m[1] - field.x_centers_m[0]) * 0.5,
                "x_max": field.x_centers_m[-1]
                + (field.x_centers_m[-1] - field.x_centers_m[-2]) * 0.5,
                "y_min": field.y_centers_m[0]
                - (field.y_centers_m[1] - field.y_centers_m[0]) * 0.5,
                "y_max": field.y_centers_m[-1]
                + (field.y_centers_m[-1] - field.y_centers_m[-2]) * 0.5,
                "bounds": "inclusive",
            },
            "coordinate_transform_policy_id": transform["policy_id"],
            "axes_swapped_from_requested_room": transform["axes_swapped"],
            "field_sampling_mapping": transform["field_sampling_mapping"],
        },
        "canonical_leaf_geometry": {
            "centroid_policy_id": CENTROID_POLICY_ID,
            "leaf_ordering": "canonical-leaf order",
            "displayed_leaf_ordering": LEAF_ORDERING,
            "canonical_leaf_count": len(centroids),
            "canonical_leaf_ids": [
                leaf.leaf_id for leaf in scene.canonical_plant.leaves
            ],
            "representative_centroids_simulation_xy_m": [
                [centroid[0], centroid[1]] for centroid in centroids
            ],
            "canonical_topology_sha256": scene.canonical_topology_sha256,
            "leaf_geometry_identity_sha256": (
                scene.leaf_geometry_identity_sha256
            ),
            "instance_translation_source": (
                "scene.plant_instances.instance_translations"
            ),
        },
        "parent_source_field_identity_sha256": field.field_identity_sha256,
    }


def aggregate_leaf_position_ppfd(
    values: Sequence[float], target: BaselineLeafTargetPolicy
) -> dict[str, object]:
    validated = tuple(_finite("leaf-position PPFD", value) for value in values)
    if not validated:
        raise BaselineLeafUniformityError(
            "baseline-leaf aggregation requires physical leaves."
        )
    if any(value < 0.0 for value in validated):
        raise BaselineLeafUniformityError(
            "leaf-position PPFD must be non-negative."
        )
    total = len(validated)
    classifications = tuple(
        classify_baseline_leaf_ppfd(value, target) for value in validated
    )
    counts = {
        name: sum(1 for value in classifications if value == name)
        for name in ("under_lit", "target_range", "over_lit")
    }
    if math.fsum(counts.values()) != total:
        raise BaselineLeafUniformityError(
            "baseline-leaf classification counts do not close."
        )
    mean = math.fsum(validated) / total
    variance = math.fsum((value - mean) ** 2 for value in validated) / total
    standard_deviation = math.sqrt(variance)
    mad = (
        math.fsum(
            abs(value - target.resolved_target_umol_m2_s)
            for value in validated
        )
        / total
    )
    count_metrics = {
        name: {
            "available": True,
            "count": count,
            "percentage": count / total * 100.0,
        }
        for name, count in counts.items()
    }
    cv = (
        {
            "available": False,
            "value_percent": None,
            "reason_code": ZERO_MEAN_REASON,
        }
        if mean == 0.0
        else {
            "available": True,
            "value_percent": 100.0 * standard_deviation / mean,
            "reason_code": None,
        }
    )
    return {
        "available": True,
        "unavailable_reason_code": None,
        "denominator_leaf_count": total,
        "target_range_leaves": count_metrics["target_range"],
        "under_lit_leaves": count_metrics["under_lit"],
        "over_lit_leaves": count_metrics["over_lit"],
        "mean_absolute_deviation_from_target": {
            "available": True,
            "value_umol_m2_s": mad,
            "reason_code": None,
        },
        "leaf_position_ppfd_coefficient_of_variation": cv,
        "calculation_components": {
            "mean_leaf_position_ppfd_umol_m2_s": mean,
            "population_standard_deviation_ppfd_umol_m2_s": standard_deviation,
            "population_variance_denominator": total,
        },
    }


def _unavailable_summary(total: int, reason: str) -> dict[str, object]:
    unavailable_count = {
        "available": False,
        "count": None,
        "percentage": None,
        "reason_code": reason,
    }
    unavailable_value = {
        "available": False,
        "value_umol_m2_s": None,
        "reason_code": reason,
    }
    return {
        "available": False,
        "unavailable_reason_code": reason,
        "denominator_leaf_count": total,
        "target_range_leaves": dict(unavailable_count),
        "under_lit_leaves": dict(unavailable_count),
        "over_lit_leaves": dict(unavailable_count),
        "mean_absolute_deviation_from_target": unavailable_value,
        "leaf_position_ppfd_coefficient_of_variation": {
            "available": False,
            "value_percent": None,
            "reason_code": reason,
        },
        "calculation_components": None,
    }


def classify_baseline_leaf_ppfd(
    value: float, target: BaselineLeafTargetPolicy
) -> str:
    ppfd = _finite("leaf-position PPFD", value)
    if ppfd < 0.0:
        raise BaselineLeafUniformityError(
            "leaf-position PPFD must be non-negative."
        )
    if ppfd < target.inclusive_lower_umol_m2_s:
        return "under_lit"
    if ppfd > target.inclusive_upper_umol_m2_s:
        return "over_lit"
    return "target_range"


def _coordinate_transform(
    overlay_payload: Mapping[str, object],
    frame: RoomCoordinateFrame,
) -> dict[str, object]:
    room = overlay_payload.get("room")
    if not isinstance(room, Mapping):
        raise BaselineLeafUniformityError(
            "authoritative overlay room transform is missing."
        )
    axes_swapped = room.get("axes_swapped_from_request")
    if not isinstance(axes_swapped, bool):
        raise BaselineLeafUniformityError(
            "authoritative overlay axis transform is invalid."
        )
    if axes_swapped is not frame.axes_swapped:
        raise BaselineLeafUniformityError(
            "Stage A overlay and natural-fit room frames disagree."
        )
    overlay_identity = _hash_json(dict(overlay_payload))
    return {
        "policy_id": COORDINATE_TRANSFORM_POLICY_ID,
        "version": 1,
        "overlay_identity_sha256": overlay_identity,
        "axes_swapped": axes_swapped,
        "rotation_degrees_about_z": frame.rotation_degrees_about_z,
        "rotation_matrix_row_major": list(frame.rotation_matrix_row_major),
        "determinant": 1,
        "requested_scene_axes": {"x": "requested_length", "y": "requested_width"},
        "field_sampling_mapping": {"x": "aligned_x", "y": "aligned_y"},
        "direction_transform": "rotation_only",
        "orientation_inferred_from_room_dimensions": False,
    }


def _canonical_leaf_geometry_payload(plant: PlantMesh) -> list[dict[str, object]]:
    return [
        {
            "leaf_id": leaf.leaf_id,
            "leaf_rank": leaf.leaf_rank,
            "leaf_layer": leaf.leaf_layer,
            "vertices": [list(vertex) for vertex in leaf.vertices],
            "triangle_indices": [list(face) for face in leaf.triangle_indices],
        }
        for leaf in plant.leaves
    ]


def _axis_bracket(
    axis: tuple[float, ...], value: float
) -> tuple[int, int, float]:
    if value <= axis[0]:
        return (0, 0, 0.0)
    if value >= axis[-1]:
        last = len(axis) - 1
        return (last, last, 0.0)
    upper = bisect_left(axis, value)
    lower = upper - 1
    return (lower, upper, (value - axis[lower]) / (axis[upper] - axis[lower]))


def _finite(name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BaselineLeafUniformityError(f"{name} must be finite.")
    number = float(value)
    if not math.isfinite(number):
        raise BaselineLeafUniformityError(f"{name} must be finite.")
    return number


def _positive(name: str, value: float | int) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise BaselineLeafUniformityError(f"{name} must be positive.")
    return number


def _hash_json(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            dict(payload),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


__all__ = [
    "BASELINE_LEAF_UNIFORMITY_FILENAME",
    "BASELINE_LEAF_UNIFORMITY_SCHEMA_ID",
    "BASELINE_LEAF_UNIFORMITY_SCHEMA_VERSION",
    "OUTSIDE_SUPPORT_REASON",
    "ZERO_MEAN_REASON",
    "BaselinePhysicalLeafScene",
    "BaselineLeafTargetPolicy",
    "BaselineLeafUniformityError",
    "BaselineLeafUniformityPublication",
    "LeafPositionOutsideSupport",
    "StageAInterpolationField",
    "aggregate_leaf_position_ppfd",
    "build_baseline_physical_leaf_scene",
    "build_baseline_leaf_uniformity_publication",
    "classify_baseline_leaf_ppfd",
    "leaf_representative_positions",
    "one_sided_area_weighted_centroid",
    "resolve_baseline_leaf_target_policy",
]
