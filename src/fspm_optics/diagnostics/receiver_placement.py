"""Measurement-only diagnostics for the frozen juvenile receiver topology.

This module consumes the authoritative generated :class:`PlantMesh`.  It never
mutates that mesh, constructs replacement patches, changes receivers, or writes
artifacts.  Candidate layouts are transient measurements over the existing
indivisible mesh cells.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import json
import math
from typing import Iterable, Literal, Mapping, Sequence, cast

from fspm_optics.plants.generator import generate_rex_juvenile_preheading_plant
from fspm_optics.plants.models import (
    LeafSurface,
    PlantMesh,
    ScientificMeshFace,
    Vector3,
)
from fspm_optics.plants.multi_scene import build_juvenile_natural_fit_scene
from fspm_optics.plants.natural_fit import plan_natural_fit_layout_from_feet
from fspm_optics.plants.rex_juvenile import RexJuvenilePreheadingConfig
from fspm_optics.plants.surface_geometry import (
    ClosestPointResult,
    FLOAT64_DISTANCE_ABSOLUTE_TOLERANCE_M2,
    FLOAT64_DISTANCE_RELATIVE_TOLERANCE,
    FaceClosestPointResult,
    SurfaceGeometryError,
    closest_point_on_faces as _production_closest_point_on_faces,
    closest_point_on_triangle as _production_closest_point_on_triangle,
)
from fspm_optics.receivers.samples import (
    MeshPatchReceiverSample,
    build_two_sided_patch_receivers,
)

DIAGNOSTIC_SCHEMA_ID = "fspm-optics.receiver-placement-diagnostic"
DIAGNOSTIC_SCHEMA_VERSION = 3
LAYOUT_PATCH_COUNT = 16
LAYOUT_BAND_COUNT = 4
FLOAT_COMPARISON_TOLERANCE = FLOAT64_DISTANCE_RELATIVE_TOLERANCE
DISTANCE_ABSOLUTE_TOLERANCE_M2 = FLOAT64_DISTANCE_ABSOLUTE_TOLERANCE_M2
SEGMENT_PARAMETER_TOLERANCE = 1e-10
SEGMENT_SPATIAL_TOLERANCE_M = 1e-12
CARRIER_SIDE_CLASSIFICATION_TOLERANCE_M = 1e-12
_NOMINAL_CUTS = (0.25, 0.5, 0.75)

AreaGrid = tuple[tuple[float, ...], ...]
CutTriple = tuple[int, int, int]
CellCoordinate = tuple[int, int]
SideClassification = Literal[
    "front_side",
    "back_side",
    "on_plane_within_tolerance",
]


class ReceiverPlacementDiagnosticError(RuntimeError):
    """Authoritative geometry or a diagnostic invariant is inconsistent."""


@dataclass(frozen=True, slots=True)
class CarrierPlaneBracketingMeasurement:
    """Signed receiver relationship to one winding-defined carrier plane."""

    centroid_signed_distance_m: float
    front_signed_distance_m: float
    back_signed_distance_m: float
    front_direction_alignment: float
    back_direction_alignment: float
    front_side_classification: SideClassification
    back_side_classification: SideClassification
    front_origin_on_expected_side: bool
    back_origin_on_expected_side: bool
    front_direction_alignment_correct: bool
    back_direction_alignment_correct: bool
    pair_brackets_carrier_plane: bool
    pair_origins_on_same_side: bool
    any_origin_on_plane_within_tolerance: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "centroid_signed_carrier_plane_distance_m": (
                self.centroid_signed_distance_m
            ),
            "front_signed_carrier_plane_distance_m": self.front_signed_distance_m,
            "back_signed_carrier_plane_distance_m": self.back_signed_distance_m,
            "front_direction_alignment": self.front_direction_alignment,
            "back_direction_alignment": self.back_direction_alignment,
            "front_origin_side_classification": self.front_side_classification,
            "back_origin_side_classification": self.back_side_classification,
            "front_origin_on_expected_side": self.front_origin_on_expected_side,
            "back_origin_on_expected_side": self.back_origin_on_expected_side,
            "front_direction_alignment_correct": (
                self.front_direction_alignment_correct
            ),
            "back_direction_alignment_correct": (
                self.back_direction_alignment_correct
            ),
            "pair_brackets_carrier_plane": self.pair_brackets_carrier_plane,
            "pair_origins_on_same_side": self.pair_origins_on_same_side,
            "any_origin_on_plane_within_tolerance": (
                self.any_origin_on_plane_within_tolerance
            ),
        }


@dataclass(frozen=True, slots=True)
class DiagnosticCell:
    u_index: int
    v_index: int
    faces: tuple[ScientificMeshFace, ...]
    area_m2: float


@dataclass(frozen=True, slots=True)
class AreaDistribution:
    minimum_area_m2: float
    maximum_area_m2: float
    minimum_to_maximum_area_ratio: float
    coefficient_of_variation: float
    maximum_absolute_normalized_deviation: float
    rms_normalized_deviation: float

    def to_dict(self) -> dict[str, float]:
        return {
            "minimum_patch_area_m2": self.minimum_area_m2,
            "maximum_patch_area_m2": self.maximum_area_m2,
            "minimum_to_maximum_area_ratio": self.minimum_to_maximum_area_ratio,
            "coefficient_of_variation": self.coefficient_of_variation,
            "maximum_absolute_normalized_area_deviation": (
                self.maximum_absolute_normalized_deviation
            ),
            "rms_normalized_area_deviation": self.rms_normalized_deviation,
        }


@dataclass(frozen=True, slots=True)
class CandidateRegion:
    patch_index: int
    u_band: int
    v_band: int
    cell_coordinates: tuple[CellCoordinate, ...]
    area_m2: float


@dataclass(frozen=True, slots=True)
class LayoutCandidate:
    layout: Literal[
        "current_fixed",
        "declared_sampling",
        "global_rectilinear",
        "longitudinal_band_adaptive",
    ]
    u_segments: int
    v_segments: int
    u_cuts: CutTriple
    v_cuts_by_u_band: tuple[CutTriple, CutTriple, CutTriple, CutTriple]
    regions: tuple[CandidateRegion, ...]
    area_distribution: AreaDistribution
    normalized_areas: tuple[float, ...]
    cut_deviation: float
    lateral_step_change: float
    complete_cut_tuple: tuple[int, ...]

    @property
    def objective(self) -> tuple[float, float, float, float, tuple[int, ...]]:
        return (
            self.area_distribution.maximum_absolute_normalized_deviation,
            self.area_distribution.rms_normalized_deviation,
            self.cut_deviation,
            self.lateral_step_change,
            self.complete_cut_tuple,
        )


@dataclass(frozen=True, slots=True)
class _LateralOption:
    cuts: CutTriple
    areas: tuple[float, float, float, float]
    maximum_deviation: float
    squared_deviation_sum: float
    cut_deviation: float


@dataclass(frozen=True, slots=True)
class _SegmentTriangleResult:
    kind: Literal["none", "hit", "coplanar"]
    parameter: float | None = None
    point: Vector3 | None = None


def _add(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[0] + right[0],
        left[1] + right[1],
        left[2] + right[2],
    )


def _subtract(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[0] - right[0],
        left[1] - right[1],
        left[2] - right[2],
    )


def _scale(vector: Vector3, factor: float) -> Vector3:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def _dot(left: Vector3, right: Vector3) -> float:
    return math.fsum(left[index] * right[index] for index in range(3))


def _cross(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _squared_length(vector: Vector3) -> float:
    return _dot(vector, vector)


def _squared_distance(left: Vector3, right: Vector3) -> float:
    return _squared_length(_subtract(left, right))


def _finite_vector(vector: Vector3) -> bool:
    return len(vector) == 3 and all(math.isfinite(value) for value in vector)


def _float_tolerance(left: float, right: float) -> float:
    return FLOAT_COMPARISON_TOLERANCE * max(1.0, abs(left), abs(right))


def _floats_equivalent(left: float, right: float) -> bool:
    return abs(left - right) <= _float_tolerance(left, right)


def classify_carrier_side(signed_distance_m: float) -> SideClassification:
    """Classify a signed carrier-plane distance with the declared tolerance."""

    if not math.isfinite(signed_distance_m):
        raise ReceiverPlacementDiagnosticError(
            "Carrier-plane signed distance must be finite."
        )
    if signed_distance_m > CARRIER_SIDE_CLASSIFICATION_TOLERANCE_M:
        return "front_side"
    if signed_distance_m < -CARRIER_SIDE_CLASSIFICATION_TOLERANCE_M:
        return "back_side"
    return "on_plane_within_tolerance"


def measure_carrier_plane_bracketing(
    *,
    current_centroid: Vector3,
    surface_anchor: Vector3,
    carrier_normal: Vector3,
    front_origin: Vector3,
    back_origin: Vector3,
    front_direction: Vector3,
    back_direction: Vector3,
) -> CarrierPlaneBracketingMeasurement:
    """Measure signed origin sides and direction alignment at a carrier plane."""

    vectors = (
        current_centroid,
        surface_anchor,
        carrier_normal,
        front_origin,
        back_origin,
        front_direction,
        back_direction,
    )
    if not all(_finite_vector(vector) for vector in vectors):
        raise ReceiverPlacementDiagnosticError(
            "Carrier-plane measurement vectors must be finite three-vectors."
        )
    normal_squared_length = _squared_length(carrier_normal)
    if not _floats_equivalent(normal_squared_length, 1.0):
        raise ReceiverPlacementDiagnosticError(
            "Carrier-plane normal must be a winding-defined unit vector."
        )

    centroid_signed = _dot(
        _subtract(current_centroid, surface_anchor),
        carrier_normal,
    )
    front_signed = _dot(_subtract(front_origin, surface_anchor), carrier_normal)
    back_signed = _dot(_subtract(back_origin, surface_anchor), carrier_normal)
    front_alignment = _dot(front_direction, carrier_normal)
    back_alignment = _dot(back_direction, carrier_normal)
    front_classification = classify_carrier_side(front_signed)
    back_classification = classify_carrier_side(back_signed)
    front_expected = front_classification == "front_side"
    back_expected = back_classification == "back_side"
    on_plane = "on_plane_within_tolerance"
    return CarrierPlaneBracketingMeasurement(
        centroid_signed_distance_m=centroid_signed,
        front_signed_distance_m=front_signed,
        back_signed_distance_m=back_signed,
        front_direction_alignment=front_alignment,
        back_direction_alignment=back_alignment,
        front_side_classification=front_classification,
        back_side_classification=back_classification,
        front_origin_on_expected_side=front_expected,
        back_origin_on_expected_side=back_expected,
        front_direction_alignment_correct=front_alignment > 0.0,
        back_direction_alignment_correct=back_alignment < 0.0,
        pair_brackets_carrier_plane=front_expected and back_expected,
        pair_origins_on_same_side=(
            front_classification == back_classification
            and front_classification != on_plane
        ),
        any_origin_on_plane_within_tolerance=(
            front_classification == on_plane or back_classification == on_plane
        ),
    )


def summarize_carrier_plane_bracketing(
    measurements: Sequence[CarrierPlaneBracketingMeasurement],
) -> dict[str, object]:
    """Aggregate signed-side counts and extrema without inferring intersections."""

    values = tuple(measurements)
    if not values:
        raise ReceiverPlacementDiagnosticError(
            "Carrier-plane summary requires at least one receiver pair."
        )
    correctly_bracketed = sum(
        measurement.pair_brackets_carrier_plane for measurement in values
    )
    correct_side_separations = [
        separation
        for measurement in values
        for separation in (
            (
                measurement.front_signed_distance_m
                if measurement.front_origin_on_expected_side
                else None
            ),
            (
                -measurement.back_signed_distance_m
                if measurement.back_origin_on_expected_side
                else None
            ),
        )
        if separation is not None
    ]
    wrong_side_penetrations = [
        penetration
        for measurement in values
        for penetration in (
            (
                -measurement.front_signed_distance_m
                if measurement.front_side_classification == "back_side"
                else None
            ),
            (
                measurement.back_signed_distance_m
                if measurement.back_side_classification == "front_side"
                else None
            ),
        )
        if penetration is not None
    ]
    return {
        "receiver_pair_count": len(values),
        "correctly_bracketed_pair_count": correctly_bracketed,
        "non_bracketed_pair_count": len(values) - correctly_bracketed,
        "same_side_pair_count": sum(
            measurement.pair_origins_on_same_side for measurement in values
        ),
        "front_origin_wrong_side_count": sum(
            measurement.front_side_classification == "back_side"
            for measurement in values
        ),
        "back_origin_wrong_side_count": sum(
            measurement.back_side_classification == "front_side"
            for measurement in values
        ),
        "on_plane_origin_count": sum(
            measurement.front_side_classification == "on_plane_within_tolerance"
            for measurement in values
        )
        + sum(
            measurement.back_side_classification == "on_plane_within_tolerance"
            for measurement in values
        ),
        "direction_alignment_failure_count": sum(
            not measurement.front_direction_alignment_correct
            for measurement in values
        )
        + sum(
            not measurement.back_direction_alignment_correct
            for measurement in values
        ),
        "minimum_correct_side_separation_m": (
            min(correct_side_separations) if correct_side_separations else None
        ),
        "maximum_wrong_side_penetration_distance_m": (
            max(wrong_side_penetrations) if wrong_side_penetrations else 0.0
        ),
    }


def closest_point_on_triangle(
    point: Vector3,
    first: Vector3,
    second: Vector3,
    third: Vector3,
    *,
    triangle_id: str = "triangle",
) -> ClosestPointResult:
    """Use the shared production triangle-surface closest-point utility."""

    try:
        return _production_closest_point_on_triangle(
            point,
            first,
            second,
            third,
            triangle_id=triangle_id,
        )
    except SurfaceGeometryError as exc:
        raise ReceiverPlacementDiagnosticError(str(exc)) from exc


def closest_point_on_faces(
    point: Vector3,
    faces: Iterable[ScientificMeshFace],
) -> FaceClosestPointResult:
    """Use the shared production face-set closest-point utility."""

    try:
        return _production_closest_point_on_faces(point, faces)
    except SurfaceGeometryError as exc:
        raise ReceiverPlacementDiagnosticError(str(exc)) from exc


def _area_distribution(
    areas: Sequence[float],
    *,
    normalized_areas: Sequence[float] | None = None,
) -> AreaDistribution:
    values = tuple(float(value) for value in areas)
    if not values or any(not math.isfinite(value) or value <= 0.0 for value in values):
        raise ReceiverPlacementDiagnosticError(
            "Area-distribution values must be finite and positive."
        )
    mean = math.fsum(values) / len(values)
    variance = math.fsum((value - mean) ** 2 for value in values) / len(values)
    normalized = (
        tuple(value / mean for value in values)
        if normalized_areas is None
        else tuple(float(value) for value in normalized_areas)
    )
    if len(normalized) != len(values) or any(
        not math.isfinite(value) or value <= 0.0 for value in normalized
    ):
        raise ReceiverPlacementDiagnosticError(
            "Normalized area values must be finite, positive, and complete."
        )
    deviations = tuple(value - 1.0 for value in normalized)
    minimum = min(values)
    maximum = max(values)
    return AreaDistribution(
        minimum_area_m2=minimum,
        maximum_area_m2=maximum,
        minimum_to_maximum_area_ratio=minimum / maximum,
        coefficient_of_variation=math.sqrt(variance) / mean,
        maximum_absolute_normalized_deviation=max(abs(value) for value in deviations),
        rms_normalized_deviation=math.sqrt(
            math.fsum(value * value for value in deviations) / len(deviations)
        ),
    )


def _validated_area_grid(area_grid: Sequence[Sequence[float]]) -> AreaGrid:
    grid = tuple(tuple(float(value) for value in row) for row in area_grid)
    if (
        len(grid) < LAYOUT_BAND_COUNT
        or not grid
        or len(grid[0]) < LAYOUT_BAND_COUNT
        or any(len(row) != len(grid[0]) for row in grid)
        or any(
            not math.isfinite(value) or value <= 0.0
            for row in grid
            for value in row
        )
    ):
        raise ValueError(
            "Cell-area grid must be rectangular, at least 4x4, finite, and positive."
        )
    return grid


def _cuts_with_bounds(cuts: CutTriple, segment_count: int) -> tuple[int, ...]:
    if not (0 < cuts[0] < cuts[1] < cuts[2] < segment_count):
        raise ValueError("Cut indices must be increasing internal cell boundaries.")
    return (0, *cuts, segment_count)


def _cut_deviation(cuts: CutTriple, segment_count: int) -> float:
    return math.fsum(
        abs(cut / segment_count - nominal)
        for cut, nominal in zip(cuts, _NOMINAL_CUTS, strict=True)
    )


def _candidate_from_cuts(
    area_grid: AreaGrid,
    *,
    layout: Literal[
        "current_fixed",
        "declared_sampling",
        "global_rectilinear",
        "longitudinal_band_adaptive",
    ],
    u_cuts: CutTriple,
    v_cuts_by_u_band: tuple[CutTriple, CutTriple, CutTriple, CutTriple],
) -> LayoutCandidate:
    u_segments = len(area_grid)
    v_segments = len(area_grid[0])
    u_bounds = _cuts_with_bounds(u_cuts, u_segments)
    regions: list[CandidateRegion] = []
    patch_areas: list[float] = []
    for u_band in range(LAYOUT_BAND_COUNT):
        v_bounds = _cuts_with_bounds(v_cuts_by_u_band[u_band], v_segments)
        for v_band in range(LAYOUT_BAND_COUNT):
            coordinates = tuple(
                (u_index, v_index)
                for u_index in range(u_bounds[u_band], u_bounds[u_band + 1])
                for v_index in range(v_bounds[v_band], v_bounds[v_band + 1])
            )
            if not coordinates:
                raise ReceiverPlacementDiagnosticError(
                    "Candidate layout produced an empty cell region."
                )
            area = math.fsum(area_grid[u][v] for u, v in coordinates)
            patch_index = LAYOUT_BAND_COUNT * u_band + v_band
            regions.append(
                CandidateRegion(
                    patch_index=patch_index,
                    u_band=u_band,
                    v_band=v_band,
                    cell_coordinates=coordinates,
                    area_m2=area,
                )
            )
            patch_areas.append(area)
    leaf_area = math.fsum(patch_areas)
    normalized = tuple(
        LAYOUT_PATCH_COUNT * area / leaf_area for area in patch_areas
    )
    area_metrics = _area_distribution(
        patch_areas,
        normalized_areas=normalized,
    )
    u_deviation = _cut_deviation(u_cuts, u_segments)
    if layout == "global_rectilinear":
        cut_deviation = u_deviation + _cut_deviation(
            v_cuts_by_u_band[0], v_segments
        )
        step_change = 0.0
        complete_cuts = (*u_cuts, *v_cuts_by_u_band[0])
    elif layout in {"current_fixed", "declared_sampling"}:
        cut_deviation = 0.0
        step_change = 0.0
        complete_cuts = (*u_cuts, *v_cuts_by_u_band[0])
    else:
        cut_deviation = u_deviation + math.fsum(
            _cut_deviation(cuts, v_segments) for cuts in v_cuts_by_u_band
        )
        step_change = math.fsum(
            abs(left[index] - right[index]) / v_segments
            for left, right in zip(
                v_cuts_by_u_band,
                v_cuts_by_u_band[1:],
                strict=False,
            )
            for index in range(3)
        )
        complete_cuts = (
            *u_cuts,
            *(cut for cuts in v_cuts_by_u_band for cut in cuts),
        )
    return LayoutCandidate(
        layout=layout,
        u_segments=u_segments,
        v_segments=v_segments,
        u_cuts=u_cuts,
        v_cuts_by_u_band=v_cuts_by_u_band,
        regions=tuple(regions),
        area_distribution=area_metrics,
        normalized_areas=normalized,
        cut_deviation=cut_deviation,
        lateral_step_change=step_change,
        complete_cut_tuple=complete_cuts,
    )


def _compare_float(left: float, right: float) -> int:
    if _floats_equivalent(left, right):
        return 0
    return -1 if left < right else 1


def _candidate_is_better(
    candidate: LayoutCandidate,
    incumbent: LayoutCandidate | None,
) -> bool:
    if incumbent is None:
        return True
    candidate_values = candidate.objective[:4]
    incumbent_values = incumbent.objective[:4]
    for left, right in zip(candidate_values, incumbent_values, strict=True):
        order = _compare_float(cast(float, left), cast(float, right))
        if order:
            return order < 0
    return candidate.complete_cut_tuple < incumbent.complete_cut_tuple


def current_fixed_candidate(area_grid: Sequence[Sequence[float]]) -> LayoutCandidate:
    """Measure the current nominal quarter-boundary grouping."""

    grid = _validated_area_grid(area_grid)
    u_segments = len(grid)
    v_segments = len(grid[0])
    if u_segments % 4 or v_segments % 4:
        raise ValueError("Current fixed grouping requires segment counts divisible by four.")
    u_cuts = cast(CutTriple, tuple(u_segments * index // 4 for index in range(1, 4)))
    v_cuts = cast(CutTriple, tuple(v_segments * index // 4 for index in range(1, 4)))
    return _candidate_from_cuts(
        grid,
        layout="current_fixed",
        u_cuts=u_cuts,
        v_cuts_by_u_band=(v_cuts, v_cuts, v_cuts, v_cuts),
    )


def declared_sampling_candidate(
    area_grid: Sequence[Sequence[float]],
    *,
    u_cuts: CutTriple,
    v_cuts: CutTriple,
) -> LayoutCandidate:
    """Measure one installed, declared cell-aligned sampling profile."""

    grid = _validated_area_grid(area_grid)
    return _candidate_from_cuts(
        grid,
        layout="declared_sampling",
        u_cuts=u_cuts,
        v_cuts_by_u_band=(v_cuts, v_cuts, v_cuts, v_cuts),
    )


def best_global_rectilinear_candidate(
    area_grid: Sequence[Sequence[float]],
) -> LayoutCandidate:
    """Find the exact deterministic best global cell-aligned 4x4 grid."""

    grid = _validated_area_grid(area_grid)
    incumbent: LayoutCandidate | None = None
    for u_cuts_raw in combinations(range(1, len(grid)), 3):
        u_cuts = cast(CutTriple, u_cuts_raw)
        for v_cuts_raw in combinations(range(1, len(grid[0])), 3):
            v_cuts = cast(CutTriple, v_cuts_raw)
            candidate = _candidate_from_cuts(
                grid,
                layout="global_rectilinear",
                u_cuts=u_cuts,
                v_cuts_by_u_band=(v_cuts, v_cuts, v_cuts, v_cuts),
            )
            if _candidate_is_better(candidate, incumbent):
                incumbent = candidate
    if incumbent is None:  # pragma: no cover - grid validation guarantees choices
        raise ReceiverPlacementDiagnosticError("Global grid optimization found no layout.")
    return incumbent


def _lateral_options(
    area_grid: AreaGrid,
    u_start: int,
    u_stop: int,
    leaf_target_area: float,
) -> tuple[_LateralOption, ...]:
    options: list[_LateralOption] = []
    v_segments = len(area_grid[0])
    for raw_cuts in combinations(range(1, v_segments), 3):
        cuts = cast(CutTriple, raw_cuts)
        bounds = _cuts_with_bounds(cuts, v_segments)
        areas = cast(
            tuple[float, float, float, float],
            tuple(
                math.fsum(
                    area_grid[u][v]
                    for u in range(u_start, u_stop)
                    for v in range(bounds[band], bounds[band + 1])
                )
                for band in range(4)
            ),
        )
        deviations = tuple(area / leaf_target_area - 1.0 for area in areas)
        options.append(
            _LateralOption(
                cuts=cuts,
                areas=areas,
                maximum_deviation=max(abs(value) for value in deviations),
                squared_deviation_sum=math.fsum(value * value for value in deviations),
                cut_deviation=_cut_deviation(cuts, v_segments),
            )
        )
    return tuple(options)


def _minimum_float(values: Iterable[float]) -> float:
    iterator = iter(values)
    try:
        minimum = next(iterator)
    except StopIteration as exc:
        raise ReceiverPlacementDiagnosticError("Optimization option set is empty.") from exc
    for value in iterator:
        if _compare_float(value, minimum) < 0:
            minimum = value
    return minimum


def _eligible_at_minimum(
    options: Sequence[_LateralOption],
    attribute: Literal[
        "maximum_deviation", "squared_deviation_sum", "cut_deviation"
    ],
) -> tuple[_LateralOption, ...]:
    minimum = _minimum_float(float(getattr(option, attribute)) for option in options)
    return tuple(
        option
        for option in options
        if _floats_equivalent(float(getattr(option, attribute)), minimum)
    )


def _best_adaptive_for_u_cuts(
    area_grid: AreaGrid,
    u_cuts: CutTriple,
) -> LayoutCandidate:
    u_bounds = _cuts_with_bounds(u_cuts, len(area_grid))
    target = math.fsum(value for row in area_grid for value in row) / 16.0
    option_sets = tuple(
        _lateral_options(area_grid, u_bounds[band], u_bounds[band + 1], target)
        for band in range(4)
    )

    fixed_u_maximum = max(
        _minimum_float(option.maximum_deviation for option in options)
        for options in option_sets
    )
    eligible_by_band: list[tuple[_LateralOption, ...]] = []
    for options in option_sets:
        maximum_eligible = tuple(
            option
            for option in options
            if option.maximum_deviation
            <= fixed_u_maximum
            + _float_tolerance(option.maximum_deviation, fixed_u_maximum)
        )
        rms_eligible = _eligible_at_minimum(
            maximum_eligible,
            "squared_deviation_sum",
        )
        cut_eligible = _eligible_at_minimum(rms_eligible, "cut_deviation")
        eligible_by_band.append(tuple(sorted(cut_eligible, key=lambda item: item.cuts)))

    states: dict[CutTriple, tuple[float, tuple[CutTriple, ...]]] = {
        option.cuts: (0.0, (option.cuts,)) for option in eligible_by_band[0]
    }
    v_segments = len(area_grid[0])
    for band in range(1, 4):
        next_states: dict[CutTriple, tuple[float, tuple[CutTriple, ...]]] = {}
        for option in eligible_by_band[band]:
            best: tuple[float, tuple[CutTriple, ...]] | None = None
            for previous_cuts, (cost, path) in states.items():
                step = math.fsum(
                    abs(previous_cuts[index] - option.cuts[index]) / v_segments
                    for index in range(3)
                )
                candidate = (cost + step, (*path, option.cuts))
                if best is None:
                    best = candidate
                else:
                    order = _compare_float(candidate[0], best[0])
                    if order < 0 or (order == 0 and candidate[1] < best[1]):
                        best = candidate
            if best is None:
                raise ReceiverPlacementDiagnosticError(
                    "Adaptive grid dynamic program lost all predecessor states."
                )
            next_states[option.cuts] = best
        states = next_states
    best_path: tuple[float, tuple[CutTriple, ...]] | None = None
    for value in states.values():
        if best_path is None:
            best_path = value
            continue
        order = _compare_float(value[0], best_path[0])
        if order < 0 or (order == 0 and value[1] < best_path[1]):
            best_path = value
    if best_path is None:
        raise ReceiverPlacementDiagnosticError(
            "Adaptive grid optimization found no lateral-cut sequence."
        )
    return _candidate_from_cuts(
        area_grid,
        layout="longitudinal_band_adaptive",
        u_cuts=u_cuts,
        v_cuts_by_u_band=cast(
            tuple[CutTriple, CutTriple, CutTriple, CutTriple],
            best_path[1],
        ),
    )


def best_band_adaptive_candidate(
    area_grid: Sequence[Sequence[float]],
) -> LayoutCandidate:
    """Find the exact deterministic best band-adaptive cell grouping.

    For fixed longitudinal cuts, the lexicographic objective decomposes by band
    through maximum deviation, additive squared deviation, and additive cut
    displacement.  A final four-stage dynamic program minimizes the only
    coupled term: lateral boundary step change.
    """

    grid = _validated_area_grid(area_grid)
    incumbent: LayoutCandidate | None = None
    for raw_cuts in combinations(range(1, len(grid)), 3):
        candidate = _best_adaptive_for_u_cuts(grid, cast(CutTriple, raw_cuts))
        if _candidate_is_better(candidate, incumbent):
            incumbent = candidate
    if incumbent is None:  # pragma: no cover - grid validation guarantees choices
        raise ReceiverPlacementDiagnosticError("Adaptive grid optimization found no layout.")
    return incumbent


def _leaf_cells(
    leaf: LeafSurface,
    *,
    u_segments: int,
    v_segments: int,
) -> tuple[DiagnosticCell, ...]:
    cells: list[DiagnosticCell] = []
    cursor = 0
    for u_index in range(u_segments):
        triangle_count = 1 if u_index in {0, u_segments - 1} else 2
        for v_index in range(v_segments):
            faces = leaf.faces[cursor : cursor + triangle_count]
            if len(faces) != triangle_count:
                raise ReceiverPlacementDiagnosticError(
                    f"Leaf {leaf.leaf_id} face order ended inside cell "
                    f"({u_index}, {v_index})."
                )
            cells.append(
                DiagnosticCell(
                    u_index=u_index,
                    v_index=v_index,
                    faces=faces,
                    area_m2=math.fsum(face.area_m2 for face in faces),
                )
            )
            cursor += triangle_count
    if cursor != len(leaf.faces):
        raise ReceiverPlacementDiagnosticError(
            f"Leaf {leaf.leaf_id} cell reconstruction did not consume every face."
        )
    return tuple(cells)


def _cell_grid(
    cells: Sequence[DiagnosticCell],
    *,
    u_segments: int,
    v_segments: int,
) -> AreaGrid:
    by_coordinate = {(cell.u_index, cell.v_index): cell for cell in cells}
    if len(by_coordinate) != u_segments * v_segments:
        raise ReceiverPlacementDiagnosticError("Cell reconstruction is incomplete or duplicated.")
    return tuple(
        tuple(by_coordinate[(u, v)].area_m2 for v in range(v_segments))
        for u in range(u_segments)
    )


def _region_faces(
    region: CandidateRegion,
    cell_by_coordinate: Mapping[CellCoordinate, DiagnosticCell],
) -> tuple[ScientificMeshFace, ...]:
    return tuple(
        face
        for coordinate in region.cell_coordinates
        for face in cell_by_coordinate[coordinate].faces
    )


def _verify_current_assignment(
    leaf: LeafSurface,
    current: LayoutCandidate,
    cell_by_coordinate: Mapping[CellCoordinate, DiagnosticCell],
) -> None:
    if (
        len(leaf.patches) != LAYOUT_PATCH_COUNT
        or len(current.regions) != LAYOUT_PATCH_COUNT
    ):
        raise ReceiverPlacementDiagnosticError("Current patch inventory is incomplete.")
    for patch, region in zip(leaf.patches, current.regions, strict=True):
        reconstructed = tuple(
            face.face_id for face in _region_faces(region, cell_by_coordinate)
        )
        if (
            patch.patch_index != region.patch_index
            or patch.patch_u_index != region.u_band
            or patch.patch_v_index != region.v_band
            or reconstructed != patch.triangle_face_ids
        ):
            raise ReceiverPlacementDiagnosticError(
                f"Diagnostic cell reconstruction disagrees with {patch.patch_id}."
            )


def _segment_triangle_intersection(
    start: Vector3,
    end: Vector3,
    face: ScientificMeshFace,
) -> _SegmentTriangleResult:
    first, second, third = face.vertices
    direction = _subtract(end, start)
    edge_one = _subtract(second, first)
    edge_two = _subtract(third, first)
    p_vector = _cross(direction, edge_two)
    determinant = _dot(edge_one, p_vector)
    scale = math.sqrt(
        max(0.0, _squared_length(edge_one))
        * max(0.0, _squared_length(edge_two))
        * max(0.0, _squared_length(direction))
    )
    determinant_tolerance = FLOAT_COMPARISON_TOLERANCE * max(scale, 1e-30)
    if abs(determinant) <= determinant_tolerance:
        start_distance = _dot(_subtract(start, first), face.unit_normal)
        end_distance = _dot(_subtract(end, first), face.unit_normal)
        if (
            abs(start_distance) <= SEGMENT_SPATIAL_TOLERANCE_M
            and abs(end_distance) <= SEGMENT_SPATIAL_TOLERANCE_M
        ):
            return _SegmentTriangleResult("coplanar")
        return _SegmentTriangleResult("none")
    inverse = 1.0 / determinant
    t_vector = _subtract(start, first)
    first_barycentric = _dot(t_vector, p_vector) * inverse
    if not -FLOAT_COMPARISON_TOLERANCE <= first_barycentric <= (
        1.0 + FLOAT_COMPARISON_TOLERANCE
    ):
        return _SegmentTriangleResult("none")
    q_vector = _cross(t_vector, edge_one)
    second_barycentric = _dot(direction, q_vector) * inverse
    if (
        second_barycentric < -FLOAT_COMPARISON_TOLERANCE
        or first_barycentric + second_barycentric
        > 1.0 + FLOAT_COMPARISON_TOLERANCE
    ):
        return _SegmentTriangleResult("none")
    parameter = _dot(edge_two, q_vector) * inverse
    if not -SEGMENT_PARAMETER_TOLERANCE <= parameter <= (
        1.0 + SEGMENT_PARAMETER_TOLERANCE
    ):
        return _SegmentTriangleResult("none")
    clamped = min(1.0, max(0.0, parameter))
    point = _add(start, _scale(direction, clamped))
    return _SegmentTriangleResult("hit", clamped, point)


def _segment_measurement(
    anchor: Vector3,
    origin: Vector3,
    faces: Sequence[ScientificMeshFace],
) -> dict[str, object]:
    anchor_contacts: list[str] = []
    unintended: list[str] = []
    unavailable_coplanar: list[str] = []
    for face in faces:
        result = _segment_triangle_intersection(anchor, origin, face)
        if result.kind == "none":
            continue
        anchor_distance = closest_point_on_faces(anchor, (face,)).squared_distance
        is_anchor_contact = anchor_distance <= DISTANCE_ABSOLUTE_TOLERANCE_M2
        if result.kind == "coplanar":
            if is_anchor_contact and _squared_distance(anchor, origin) <= (
                SEGMENT_SPATIAL_TOLERANCE_M**2
            ):
                anchor_contacts.append(face.face_id)
            else:
                unavailable_coplanar.append(face.face_id)
            continue
        if (
            result.parameter is not None
            and result.parameter <= SEGMENT_PARAMETER_TOLERANCE
            and result.point is not None
            and _squared_distance(result.point, anchor)
            <= SEGMENT_SPATIAL_TOLERANCE_M**2
        ):
            anchor_contacts.append(face.face_id)
        else:
            unintended.append(face.face_id)
    available = not unavailable_coplanar
    return {
        "measurement_available": available,
        "expected_anchor_contact_face_ids": sorted(set(anchor_contacts)),
        "unintended_intersection_face_ids": (
            sorted(set(unintended)) if available else None
        ),
        "unintended_intersection_count": (
            len(set(unintended)) if available else None
        ),
        "unresolved_coplanar_face_ids": sorted(set(unavailable_coplanar)),
    }


def _normal_angle_degrees(first: Vector3, second: Vector3) -> float:
    cosine = max(-1.0, min(1.0, _dot(first, second)))
    return math.degrees(math.acos(cosine))


def _receiver_measurement(
    receiver: MeshPatchReceiverSample,
    *,
    anchor: Vector3,
    own_face_ids: frozenset[str],
    plant_faces: tuple[ScientificMeshFace, ...],
) -> dict[str, object]:
    closest = closest_point_on_faces(receiver.point_m, plant_faces)
    non_own_faces = tuple(
        face for face in plant_faces if face.face_id not in own_face_ids
    )
    non_own = closest_point_on_faces(receiver.point_m, non_own_faces)
    segment = _segment_measurement(anchor, receiver.point_m, plant_faces)
    return {
        "receiver_id": receiver.receiver_id,
        "side": receiver.side,
        "origin_m": list(receiver.point_m),
        "direction": list(receiver.normal),
        "origin_to_mesh": {
            "distance_m": math.sqrt(closest.squared_distance),
            "closest_point_m": list(closest.point),
            "closest_face_id": closest.face.face_id,
            "closest_face_belongs_to_own_patch": (
                closest.face.face_id in own_face_ids
            ),
        },
        "minimum_non_own_face_clearance_m": math.sqrt(non_own.squared_distance),
        "anchor_to_current_origin_segment": segment,
    }


def _candidate_face_payload(
    candidate: LayoutCandidate,
    *,
    cells: Sequence[DiagnosticCell],
    leaf: LeafSurface,
    current: LayoutCandidate,
) -> dict[str, object]:
    cell_by_coordinate = {(cell.u_index, cell.v_index): cell for cell in cells}
    regions: list[dict[str, object]] = []
    assigned: list[str] = []
    for region, normalized in zip(
        candidate.regions,
        candidate.normalized_areas,
        strict=True,
    ):
        faces = _region_faces(region, cell_by_coordinate)
        face_ids = [face.face_id for face in faces]
        assigned.extend(face_ids)
        regions.append(
            {
                "local_patch_index": region.patch_index,
                "u_band": region.u_band,
                "v_band": region.v_band,
                "cell_coordinates": [list(value) for value in region.cell_coordinates],
                "face_ids": face_ids,
                "triangle_count": len(face_ids),
                "physical_area_m2": region.area_m2,
                "normalized_area": normalized,
                "relative_deviation_from_equal_area": normalized - 1.0,
            }
        )
    leaf_face_ids = [face.face_id for face in leaf.faces]
    total_candidate_area = math.fsum(region.area_m2 for region in candidate.regions)
    total_leaf_area = math.fsum(face.area_m2 for face in leaf.faces)
    current_maximum = current.area_distribution.maximum_absolute_normalized_deviation
    step_changes = [
        {
            "between_u_bands": [band, band + 1],
            "v_cut_index_differences": [
                candidate.v_cuts_by_u_band[band + 1][index]
                - candidate.v_cuts_by_u_band[band][index]
                for index in range(3)
            ],
            "normalized_absolute_step_changes": [
                abs(
                    candidate.v_cuts_by_u_band[band + 1][index]
                    - candidate.v_cuts_by_u_band[band][index]
                )
                / candidate.v_segments
                for index in range(3)
            ],
        }
        for band in range(3)
    ]
    payload: dict[str, object] = {
        "layout": candidate.layout,
        "objective": {
            "maximum_absolute_normalized_area_deviation": candidate.objective[0],
            "rms_normalized_area_deviation": candidate.objective[1],
            "total_absolute_nominal_cut_deviation": candidate.objective[2],
            "summed_lateral_boundary_step_change": candidate.objective[3],
            "lexicographic_complete_cut_index_tuple": list(candidate.objective[4]),
        },
        "u_cut_indices": list(candidate.u_cuts),
        "u_parameter_boundaries": [
            0.0,
            *(cut / candidate.u_segments for cut in candidate.u_cuts),
            1.0,
        ],
        "v_cut_indices_by_u_band": [
            list(cuts) for cuts in candidate.v_cuts_by_u_band
        ],
        "v_parameter_boundaries_by_u_band": [
            [
                -1.0,
                *(-1.0 + 2.0 * cut / candidate.v_segments for cut in cuts),
                1.0,
            ]
            for cuts in candidate.v_cuts_by_u_band
        ],
        "v_cell_fraction_boundaries_by_u_band": [
            [0.0, *(cut / candidate.v_segments for cut in cuts), 1.0]
            for cuts in candidate.v_cuts_by_u_band
        ],
        "lateral_boundary_step_changes": step_changes,
        "area_distribution": candidate.area_distribution.to_dict(),
        "regions": regions,
        "face_coverage": {
            "assigned_face_count": len(assigned),
            "unique_assigned_face_count": len(set(assigned)),
            "authoritative_face_count": len(leaf_face_ids),
            "complete": set(assigned) == set(leaf_face_ids),
            "unique": len(assigned) == len(set(assigned)),
        },
        "total_leaf_area_conserved": math.isclose(
            total_candidate_area,
            total_leaf_area,
            rel_tol=FLOAT_COMPARISON_TOLERANCE,
            abs_tol=1e-18,
        ),
        "improvement_over_current": {
            "coefficient_of_variation": (
                current.area_distribution.coefficient_of_variation
                - candidate.area_distribution.coefficient_of_variation
            ),
            "maximum_absolute_normalized_area_deviation": (
                current.area_distribution.maximum_absolute_normalized_deviation
                - candidate.area_distribution.maximum_absolute_normalized_deviation
            ),
            "rms_normalized_area_deviation": (
                current.area_distribution.rms_normalized_deviation
                - candidate.area_distribution.rms_normalized_deviation
            ),
            "any_patch_worse_than_current_maximum": any(
                abs(value - 1.0)
                > current_maximum + _float_tolerance(abs(value - 1.0), current_maximum)
                for value in candidate.normalized_areas
            ),
        },
    }
    return payload


def _rms(values: Sequence[float]) -> float:
    if not values:
        raise ReceiverPlacementDiagnosticError("RMS requires at least one value.")
    return math.sqrt(math.fsum(value * value for value in values) / len(values))


def _current_summary(
    patch_records: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    areas = [float(record["physical_area_m2"]) for record in patch_records]
    normalized = [float(record["normalized_area"]) for record in patch_records]
    centroid_distances = [
        float(record["centroid_to_patch_surface_distance_m"])
        for record in patch_records
    ]
    normal_angles = [
        float(record["patch_average_to_carrier_normal_angle_degrees"])
        for record in patch_records
    ]
    receiver_records = [
        cast(Mapping[str, object], receiver)
        for record in patch_records
        for receiver in cast(Sequence[Mapping[str, object]], record["receivers"])
    ]
    outside = sum(
        not bool(
            cast(Mapping[str, object], receiver["origin_to_mesh"])[
                "closest_face_belongs_to_own_patch"
            ]
        )
        for receiver in receiver_records
    )
    segment_records = [
        cast(Mapping[str, object], receiver["anchor_to_current_origin_segment"])
        for receiver in receiver_records
    ]
    segment_available = all(
        bool(record["measurement_available"]) for record in segment_records
    )
    intersection_count = (
        sum(int(record["unintended_intersection_count"]) for record in segment_records)
        if segment_available
        else None
    )
    clearances = [
        float(receiver["minimum_non_own_face_clearance_m"])
        for receiver in receiver_records
    ]
    return {
        **_area_distribution(areas, normalized_areas=normalized).to_dict(),
        "maximum_centroid_to_surface_distance_m": max(centroid_distances),
        "rms_centroid_to_surface_distance_m": _rms(centroid_distances),
        "maximum_patch_average_to_local_normal_angle_degrees": max(normal_angles),
        "rms_patch_average_to_local_normal_angle_degrees": _rms(normal_angles),
        "receiver_origin_closest_face_outside_own_patch_count": outside,
        "unintended_front_back_segment_intersection_count": intersection_count,
        "segment_intersection_measurements_complete": segment_available,
        "segment_intersection_unavailable_receiver_count": sum(
            not bool(record["measurement_available"]) for record in segment_records
        ),
        "minimum_measured_non_own_face_clearance_m": min(clearances),
    }


def _measure_leaf(
    plant: PlantMesh,
    leaf: LeafSurface,
    *,
    leaf_index: int,
    receivers_by_patch: Mapping[
        str, tuple[MeshPatchReceiverSample, MeshPatchReceiverSample]
    ],
) -> tuple[
    dict[str, object],
    dict[str, LayoutCandidate],
    tuple[CarrierPlaneBracketingMeasurement, ...],
]:
    u_segments, v_segments = plant.config.mesh_segments_for_layer(leaf.leaf_layer)
    cells = _leaf_cells(leaf, u_segments=u_segments, v_segments=v_segments)
    area_grid = _cell_grid(
        cells,
        u_segments=u_segments,
        v_segments=v_segments,
    )
    if not isinstance(plant.config, RexJuvenilePreheadingConfig):
        raise ReceiverPlacementDiagnosticError(
            "Receiver-placement diagnostic requires the juvenile profile."
        )
    installed_u_cuts, installed_v_cuts = (
        plant.config.patch_cell_cuts_for_leaf_rank(
            leaf.leaf_rank,
            u_segments=u_segments,
            v_segments=v_segments,
        )
    )
    current = declared_sampling_candidate(
        area_grid,
        u_cuts=installed_u_cuts,
        v_cuts=installed_v_cuts,
    )
    legacy = current_fixed_candidate(area_grid)
    global_candidate = best_global_rectilinear_candidate(area_grid)
    adaptive_candidate = best_band_adaptive_candidate(area_grid)
    cell_by_coordinate = {(cell.u_index, cell.v_index): cell for cell in cells}
    _verify_current_assignment(leaf, current, cell_by_coordinate)

    leaf_area = math.fsum(face.area_m2 for face in leaf.faces)
    plant_faces = plant.faces
    face_by_id = {face.face_id: face for face in leaf.faces}
    patch_records: list[dict[str, object]] = []
    bracketing_measurements: list[CarrierPlaneBracketingMeasurement] = []
    for local_patch_index, patch in enumerate(leaf.patches):
        patch_faces = tuple(face_by_id[face_id] for face_id in patch.triangle_face_ids)
        closest = closest_point_on_faces(patch.centroid, patch_faces)
        if patch.surface_anchor is not None:
            if (
                patch.carrier_face_id != closest.face.face_id
                or patch.carrier_barycentric != closest.barycentric
                or patch.carrier_unit_normal != closest.face.unit_normal
                or any(
                    not _floats_equivalent(left, right)
                    for left, right in zip(
                        patch.surface_anchor,
                        closest.point,
                        strict=True,
                    )
                )
            ):
                raise ReceiverPlacementDiagnosticError(
                    f"Stored carrier geometry disagrees with {patch.patch_id}."
                )
            surface_anchor = patch.surface_anchor
            carrier_face = closest.face
            carrier_barycentric = patch.carrier_barycentric
            carrier_normal = patch.carrier_unit_normal
        else:
            surface_anchor = closest.point
            carrier_face = closest.face
            carrier_barycentric = closest.barycentric
            carrier_normal = closest.face.unit_normal
        if carrier_barycentric is None or carrier_normal is None:
            raise ReceiverPlacementDiagnosticError(
                f"Carrier geometry is incomplete for {patch.patch_id}."
            )
        angle = _normal_angle_degrees(patch.unit_normal, carrier_normal)
        normalized_area = LAYOUT_PATCH_COUNT * patch.area_m2 / leaf_area
        try:
            front, back = receivers_by_patch[patch.patch_id]
        except KeyError as exc:
            raise ReceiverPlacementDiagnosticError(
                f"Receiver pair is missing for {patch.patch_id}."
            ) from exc
        if front.side != "front" or back.side != "back":
            raise ReceiverPlacementDiagnosticError(
                f"Receiver pair order is invalid for {patch.patch_id}."
            )
        bracketing = measure_carrier_plane_bracketing(
            current_centroid=patch.centroid,
            surface_anchor=surface_anchor,
            carrier_normal=carrier_normal,
            front_origin=front.point_m,
            back_origin=back.point_m,
            front_direction=front.normal,
            back_direction=back.normal,
        )
        bracketing_measurements.append(bracketing)
        own_face_ids = frozenset(patch.triangle_face_ids)
        receiver_records = [
            _receiver_measurement(
                receiver,
                anchor=surface_anchor,
                own_face_ids=own_face_ids,
                plant_faces=plant_faces,
            )
            for receiver in (front, back)
        ]
        patch_records.append(
            {
                "leaf_index": leaf_index,
                "leaf_rank": leaf.leaf_rank,
                "local_patch_index": local_patch_index,
                "patch_index_within_leaf": patch.patch_index,
                "patch_id": patch.patch_id,
                "u_band": patch.patch_u_index,
                "v_band": patch.patch_v_index,
                "face_ids": list(patch.triangle_face_ids),
                "triangle_count": len(patch.triangle_face_ids),
                "physical_area_m2": patch.area_m2,
                "normalized_area": normalized_area,
                "relative_deviation_from_equal_area": normalized_area - 1.0,
                "current_area_weighted_euclidean_centroid_m": list(patch.centroid),
                "closest_patch_surface_point_m": list(surface_anchor),
                "centroid_to_patch_surface_distance_m": math.sqrt(
                    closest.squared_distance
                ),
                "carrier_face_id": carrier_face.face_id,
                "carrier_face_barycentric_coordinates": list(
                    carrier_barycentric
                ),
                "current_patch_average_unit_normal": list(patch.unit_normal),
                "carrier_triangle_unit_normal": list(carrier_normal),
                "receiver_normal_basis": patch.receiver_normal_basis,
                "patch_average_to_carrier_normal_angle_degrees": angle,
                "carrier_plane_bracketing": bracketing.to_dict(),
                "receivers": receiver_records,
            }
        )

    layout_payloads = {
        "current_authoritative_sampling_layout": _candidate_face_payload(
            current,
            cells=cells,
            leaf=leaf,
            current=current,
        ),
        "current_fixed_layout": _candidate_face_payload(
            legacy,
            cells=cells,
            leaf=leaf,
            current=current,
        ),
        "legacy_uv_quarter_layout": _candidate_face_payload(
            legacy,
            cells=cells,
            leaf=leaf,
            current=current,
        ),
        "best_global_rectilinear_layout": _candidate_face_payload(
            global_candidate,
            cells=cells,
            leaf=leaf,
            current=current,
        ),
        "best_longitudinal_band_adaptive_layout": _candidate_face_payload(
            adaptive_candidate,
            cells=cells,
            leaf=leaf,
            current=current,
        ),
    }
    payload = {
        "leaf_index": leaf_index,
        "leaf_rank": leaf.leaf_rank,
        "leaf_id": leaf.leaf_id,
        "leaf_layer": leaf.leaf_layer,
        "mesh_cell_lattice": {
            "u_segments": u_segments,
            "v_segments": v_segments,
            "cell_count": len(cells),
            "cells_are_indivisible": True,
            "triangles_split": False,
        },
        "physical_one_sided_leaf_area_m2": leaf_area,
        "current_patches": patch_records,
        "current_summary": {
            **_current_summary(patch_records),
            **summarize_carrier_plane_bracketing(bracketing_measurements),
        },
        **layout_payloads,
    }
    return (
        payload,
        {
            "current": current,
            "legacy": legacy,
            "global": global_candidate,
            "adaptive": adaptive_candidate,
        },
        tuple(bracketing_measurements),
    )


def _receiver_pairs(
    receivers: Sequence[MeshPatchReceiverSample],
) -> dict[str, tuple[MeshPatchReceiverSample, MeshPatchReceiverSample]]:
    if len(receivers) % 2:
        raise ReceiverPlacementDiagnosticError(
            "Canonical receiver inventory contains an incomplete pair."
        )
    result: dict[str, tuple[MeshPatchReceiverSample, MeshPatchReceiverSample]] = {}
    for index in range(0, len(receivers), 2):
        front, back = receivers[index : index + 2]
        if (
            front.patch_id != back.patch_id
            or front.side != "front"
            or back.side != "back"
            or front.patch_id in result
        ):
            raise ReceiverPlacementDiagnosticError(
                "Canonical receiver pairs are reordered or duplicated."
            )
        result[front.patch_id] = (front, back)
    return result


def _plant_candidate_summary(
    candidates: Sequence[LayoutCandidate],
) -> dict[str, object]:
    areas = [region.area_m2 for candidate in candidates for region in candidate.regions]
    normalized = [value for candidate in candidates for value in candidate.normalized_areas]
    distributions = [candidate.area_distribution for candidate in candidates]
    return {
        **_area_distribution(areas, normalized_areas=normalized).to_dict(),
        "mean_leaf_coefficient_of_variation": math.fsum(
            value.coefficient_of_variation for value in distributions
        )
        / len(distributions),
        "maximum_leaf_coefficient_of_variation": max(
            value.coefficient_of_variation for value in distributions
        ),
        "maximum_leaf_absolute_normalized_area_deviation": max(
            value.maximum_absolute_normalized_deviation for value in distributions
        ),
        "maximum_leaf_rms_normalized_area_deviation": max(
            value.rms_normalized_deviation for value in distributions
        ),
    }


def _assert_finite_json(value: object, path: str = "root") -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReceiverPlacementDiagnosticError(
                f"Diagnostic JSON contains a non-finite value at {path}."
            )
    elif isinstance(value, Mapping):
        for name, item in value.items():
            _assert_finite_json(item, f"{path}.{name}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_finite_json(item, f"{path}[{index}]")


def build_receiver_placement_diagnostic(
    plant: PlantMesh | None = None,
) -> dict[str, object]:
    """Build a deterministic JSON-compatible measurement of current geometry."""

    canonical = (
        generate_rex_juvenile_preheading_plant() if plant is None else plant
    )
    receivers = build_two_sided_patch_receivers(canonical)
    if not receivers:
        raise ReceiverPlacementDiagnosticError("Canonical plant has no receivers.")
    offsets = {receiver.normal_offset_m for receiver in receivers}
    if len(offsets) != 1:
        raise ReceiverPlacementDiagnosticError(
            "Canonical receiver offset is not profile-wide and uniform."
        )
    # The public scene builder is the authority for topology and receiver hashes.
    # The 10x10 layout is used only to expose that canonical identity; no expanded
    # plant or transport result is measured by this diagnostic.
    scene = build_juvenile_natural_fit_scene(
        plan_natural_fit_layout_from_feet(10.0, 10.0),
        canonical_plant=canonical,
    )
    pairs = _receiver_pairs(receivers)
    leaf_payloads: list[dict[str, object]] = []
    candidate_sets: list[dict[str, LayoutCandidate]] = []
    bracketing_measurements: list[CarrierPlaneBracketingMeasurement] = []
    for leaf_index, leaf in enumerate(canonical.leaves):
        payload, candidates, leaf_bracketing = _measure_leaf(
            canonical,
            leaf,
            leaf_index=leaf_index,
            receivers_by_patch=pairs,
        )
        leaf_payloads.append(payload)
        candidate_sets.append(candidates)
        bracketing_measurements.extend(leaf_bracketing)
    current_patch_records = [
        cast(Mapping[str, object], patch)
        for leaf in leaf_payloads
        for patch in cast(Sequence[Mapping[str, object]], leaf["current_patches"])
    ]
    payload: dict[str, object] = {
        "schema_id": DIAGNOSTIC_SCHEMA_ID,
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "diagnostic_scope": "measurement_only_current_authoritative_geometry",
        "profile": {
            "profile_id": scene.profile_id,
            "sampling_profile_id": scene.sampling_profile_id,
            "sampling_calibration_status": (
                canonical.config.sampling_calibration_status
            ),
            "canonical_plant_id": scene.topology.canonical_plant_id,
            "juvenile_scene_schema_id": scene.schema_id,
            "juvenile_scene_schema_version": scene.schema_version,
            "topology_sha256": scene.topology.topology_sha256,
            "receivers_sha256": scene.topology.receivers_sha256,
            "canonical_hash_context": (
                "public juvenile scene builder with canonical 10x10-ft Natural-fit "
                "layout; only canonical topology/receiver hashes are reported"
            ),
        },
        "counts": {
            "leaves": len(canonical.leaves),
            "faces": canonical.face_count,
            "patches": canonical.patch_count,
            "receivers": len(receivers),
        },
        "receiver_offset_epsilon_m": next(iter(offsets)),
        "comparison_tolerances": {
            "float64_relative_or_unit_scale": FLOAT_COMPARISON_TOLERANCE,
            "closest_distance_absolute_m2": DISTANCE_ABSOLUTE_TOLERANCE_M2,
            "segment_parameter": SEGMENT_PARAMETER_TOLERANCE,
            "segment_spatial_m": SEGMENT_SPATIAL_TOLERANCE_M,
            "carrier_side_classification_absolute_m": (
                CARRIER_SIDE_CLASSIFICATION_TOLERANCE_M
            ),
        },
        "carrier_plane_side_contract": {
            "surface_basis": (
                "selected authoritative piecewise-planar carrier triangle"
            ),
            "normal_basis": "carrier_triangle_winding_defined_unit_normal",
            "front_origin_expected_classification": "front_side",
            "back_origin_expected_classification": "back_side",
            "front_direction_alignment_required": "strictly_positive",
            "back_direction_alignment_required": "strictly_negative",
            "segment_intersection_safety_is_independent": True,
        },
        "candidate_optimization": {
            "atomic_region": "authoritative_existing_mesh_cell",
            "triangles_may_be_split": False,
            "objective_order": [
                "minimum_maximum_absolute_normalized_area_deviation",
                "minimum_rms_normalized_area_deviation",
                "minimum_total_absolute_nominal_quarter_cut_deviation",
                "minimum_summed_lateral_boundary_step_change_for_adaptive_layout",
                "lexicographically_smallest_complete_cut_index_tuple",
            ],
            "float64_objective_comparison_tolerance": (
                FLOAT_COMPARISON_TOLERANCE
            ),
            "scientific_acceptability_or_topology_change_decision": "not_made",
        },
        "leaves": leaf_payloads,
        "plant_wide_current_summary": {
            **_current_summary(current_patch_records),
            **summarize_carrier_plane_bracketing(bracketing_measurements),
        },
        "plant_wide_candidate_summaries": {
            "current_authoritative_sampling_layout": _plant_candidate_summary(
                [values["current"] for values in candidate_sets]
            ),
            "current_fixed_layout": _plant_candidate_summary(
                [values["legacy"] for values in candidate_sets]
            ),
            "legacy_uv_quarter_layout": _plant_candidate_summary(
                [values["legacy"] for values in candidate_sets]
            ),
            "best_global_rectilinear_layout": _plant_candidate_summary(
                [values["global"] for values in candidate_sets]
            ),
            "best_longitudinal_band_adaptive_layout": _plant_candidate_summary(
                [values["adaptive"] for values in candidate_sets]
            ),
        },
        "limitations": [
            "Candidate layouts measure existing indivisible mesh cells only.",
            (
                "The current authoritative declared sampling layout is measured; "
                "optimizer candidates and the legacy comparison are not installed."
            ),
            (
                "Closest points and intersections are measured against the "
                "piecewise-planar mesh, not the unsampled analytic leaf equation."
            ),
            (
                "A nontrivial coplanar segment/triangle case is marked unavailable "
                "instead of approximated."
            ),
            (
                "Carrier-plane bracketing and segment-intersection safety are "
                "independent measurements."
            ),
            "Lower area imbalance does not establish transport or quadrature accuracy.",
            "Stage C remains correct because it uses actual physical patch areas.",
        ],
        "measurements_not_performed": [
            "Radiance transport",
            "receiver-count convergence",
            "triangle splitting or retriangulation",
            "candidate topology or receiver hashing",
            "calibration fitting or reuse",
            "viewer or publication generation",
            "system-specific or global-position-specific receiver evaluation",
            "far-red scope changes",
        ],
        "production_changes_made": False,
    }
    _assert_finite_json(payload)
    return payload


def format_receiver_placement_diagnostic(
    payload: Mapping[str, object],
    *,
    indent: int | None = 2,
) -> str:
    """Serialize a diagnostic with stable keys and strict finite JSON values."""

    _assert_finite_json(payload)
    return (
        json.dumps(
            payload,
            sort_keys=True,
            indent=indent,
            separators=(",", ":") if indent is None else None,
            allow_nan=False,
        )
        + "\n"
    )


__all__ = [
    "CARRIER_SIDE_CLASSIFICATION_TOLERANCE_M",
    "DIAGNOSTIC_SCHEMA_ID",
    "DIAGNOSTIC_SCHEMA_VERSION",
    "FLOAT_COMPARISON_TOLERANCE",
    "AreaDistribution",
    "CarrierPlaneBracketingMeasurement",
    "ClosestPointResult",
    "FaceClosestPointResult",
    "LayoutCandidate",
    "ReceiverPlacementDiagnosticError",
    "best_band_adaptive_candidate",
    "best_global_rectilinear_candidate",
    "build_receiver_placement_diagnostic",
    "classify_carrier_side",
    "closest_point_on_faces",
    "closest_point_on_triangle",
    "current_fixed_candidate",
    "declared_sampling_candidate",
    "format_receiver_placement_diagnostic",
    "measure_carrier_plane_bracketing",
    "summarize_carrier_plane_bracketing",
]
