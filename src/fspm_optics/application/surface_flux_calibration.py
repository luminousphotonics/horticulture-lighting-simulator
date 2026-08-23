"""System-neutral reference-field calibration for juvenile surface flux.

This experimental module is deliberately separate from normal run publication
and display code.  It executes an open-boundary, uniform upper-hemisphere
reference field, validates every raw receiver through the Phase 27G-C
derivation kernel, and retains only fixed histograms and compact statistics in
its attachable report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile
from typing import Callable, Mapping, Protocol, Sequence

from fspm_optics.application.fspm_science import (
    CompensatedNonnegativeSum,
    DEFAULT_RAW_CHUNK_BYTES,
    PAR_BAND_ORDER,
    ParPatchSurfaceLight,
    stream_juvenile_par_surface_light,
)
from fspm_optics.geometry.room import FEET_TO_METERS, RoomDimensions
from fspm_optics.geometry.sensor_grid import (
    SensorGridSpec,
    format_rtrace_receivers,
    generate_sensor_points,
)
from fspm_optics.optics.rex_material_plan import (
    RexRadianceTransMaterialPlan,
    build_rex_radiance_trans_material_plan,
    render_radiance_trans_material,
)
from fspm_optics.plants.multi_scene import (
    JuvenileScientificScene,
    build_juvenile_natural_fit_scene,
)
from fspm_optics.plants.natural_fit import plan_natural_fit_layout_from_feet
from fspm_optics.plants.rex_juvenile import (
    REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID,
)
from fspm_optics.plants.radiance_scene_export import (
    DEFAULT_LEAF_MATERIAL_MODIFIER,
    JuvenileRadianceArtifactMetadata,
    materialize_juvenile_radiance_export,
    plan_juvenile_radiance_export,
)
from fspm_optics.radiance.commands import (
    LOCAL_DEFAULT_NTHREADS,
    CommandSpec,
    build_baseline_rtrace_command,
    build_oconv_command,
    build_plant_receiver_rtrace_command,
)
from fspm_optics.radiance.options import (
    RADIANCE_QUALITY_NAMES,
    radiance_options,
)
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.radiance.versioning import (
    RadianceInstallation,
    discover_radiance_installation,
)
from fspm_optics.spectral.bands import FIXED_TRANSPORT_BANDS
from fspm_optics.transport.basis.atomic import atomic_write_text
from fspm_optics.transport.scalar_ppfd import decode_grey_channel_ppfd


CALIBRATION_SCHEMA_ID = "fspm-optics.surface-flux-calibration-report"
CALIBRATION_SCHEMA_VERSION = 1
CONFIGURATION_SCHEMA_ID = "fspm-optics.surface-flux-calibration-configuration"
CONFIGURATION_SCHEMA_VERSION = 1
LEVEL_SCHEMA_ID = "fspm-optics.surface-flux-calibration-level"
LEVEL_SCHEMA_VERSION = 1
COMPLETION_SCHEMA_ID = "fspm-optics.surface-flux-calibration-completion"
COMPLETION_SCHEMA_VERSION = 1
REPORT_NAME = "surface-flux-calibration-report.v1.json"
CONFIGURATION_NAME = "calibration-configuration.v1.json"
COMPLETION_NAME = "level-completion.v1.json"
LEVEL_RESULT_NAME = "level-result.v1.json"
DEFAULT_OUTPUT_DIRECTORY = Path(".surface-flux-calibration")
DEFAULT_REFERENCE_LEVELS = (250.0, 375.0, 500.0, 625.0, 750.0)
DEFAULT_ROOM_LENGTH_FT = 10.0
DEFAULT_ROOM_WIDTH_FT = 10.0
DEFAULT_ROOM_HEIGHT_FT = 10.0
DEFAULT_REFERENCE_PLANE_Z_M = 0.005
DEFAULT_REFERENCE_GRID = (21, 21)
DEFAULT_REFERENCE_INSET_M = 0.005
MAX_REFERENCE_SAMPLES = 1_000_000
PAR_BANDS = FIXED_TRANSPORT_BANDS[:4]
SIDE_METRICS = (
    "front_incident",
    "back_incident",
    "front_absorbed",
    "back_absorbed",
)
SOURCE_MODEL_ID = "neutral-uniform-upper-hemisphere-v1"
BOUNDARY_MODEL_ID = "open-boundary-reference-field-v1"
SPECTRUM_MODEL_ID = "equal-four-band-photon-fractions-v1"
SOURCE_GLOW_MODIFIER = "neutral_reference_hemisphere_glow"
SOURCE_PRIMITIVE = "neutral_reference_upper_hemisphere"
SOURCE_ANGLE_DEGREES = 180.0
SOURCE_DIRECTION = (0.0, 0.0, 1.0)
_QUALITY_RANK = {
    name: index for index, name in enumerate(RADIANCE_QUALITY_NAMES)
}

EventSink = Callable[[str, Mapping[str, object] | None], None]


class SurfaceFluxCalibrationError(RuntimeError):
    """The experiment failed an integrity, execution, or scientific gate."""


class CommandRunner(Protocol):
    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult: ...


@dataclass(frozen=True, slots=True)
class HistogramSpecification:
    """One predeclared common distribution grid for normalized density."""

    minimum: float = 0.0
    maximum: float = 2.0
    bin_count: int = 400

    def __post_init__(self) -> None:
        minimum = _finite("histogram minimum", self.minimum)
        maximum = _finite("histogram maximum", self.maximum)
        if minimum < 0.0 or maximum <= minimum:
            raise ValueError("histogram bounds must be finite, non-negative, and ordered.")
        if (
            isinstance(self.bin_count, bool)
            or not isinstance(self.bin_count, int)
            or self.bin_count <= 0
            or self.bin_count > 100_000
        ):
            raise ValueError("histogram bin_count must be in [1, 100000].")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    @property
    def bin_width(self) -> float:
        return (self.maximum - self.minimum) / self.bin_count

    @property
    def boundaries(self) -> tuple[float, ...]:
        width = self.bin_width
        return tuple(
            self.minimum + index * width
            for index in range(self.bin_count + 1)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "variable": "receiver_density / achieved_stage_a_mean_ppfd",
            "minimum": self.minimum,
            "maximum": self.maximum,
            "bin_count": self.bin_count,
            "bin_width": self.bin_width,
            "boundaries": list(self.boundaries),
            "interval_convention": (
                "bins are [lower, upper); x < minimum is underflow; "
                "x >= maximum is overflow"
            ),
            "selection_policy": "declared before any receiver extrema are read",
        }


@dataclass(frozen=True, slots=True)
class CalibrationCriteria:
    maximum_reference_plane_cv: float = 0.005
    minimum_through_origin_r_squared: float = 0.999
    maximum_beta_drift: float = 0.01
    maximum_verification_beta_difference: float = 0.01

    def __post_init__(self) -> None:
        for name in (
            "maximum_reference_plane_cv",
            "minimum_through_origin_r_squared",
            "maximum_beta_drift",
            "maximum_verification_beta_difference",
        ):
            value = _finite(name, getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a fraction in [0, 1].")
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, float]:
        return {
            "maximum_reference_plane_cv": self.maximum_reference_plane_cv,
            "minimum_through_origin_r_squared": (
                self.minimum_through_origin_r_squared
            ),
            "maximum_beta_drift": self.maximum_beta_drift,
            "maximum_verification_beta_difference": (
                self.maximum_verification_beta_difference
            ),
        }


@dataclass(frozen=True, slots=True)
class SurfaceFluxCalibrationConfig:
    """All accepted user inputs; intentionally no lighting-system discriminator."""

    output_directory: Path = DEFAULT_OUTPUT_DIRECTORY
    room_length_ft: float = DEFAULT_ROOM_LENGTH_FT
    room_width_ft: float = DEFAULT_ROOM_WIDTH_FT
    reference_plane_z_m: float = DEFAULT_REFERENCE_PLANE_Z_M
    reference_grid_x: int = DEFAULT_REFERENCE_GRID[0]
    reference_grid_y: int = DEFAULT_REFERENCE_GRID[1]
    reference_inset_m: float = DEFAULT_REFERENCE_INSET_M
    quality: str = "standard"
    reference_levels_umol_m2_s: tuple[float, ...] = DEFAULT_REFERENCE_LEVELS
    verification_level_umol_m2_s: float | None = None
    verification_quality: str | None = None
    threads: int = LOCAL_DEFAULT_NTHREADS
    oconv_command: str | Path = "oconv"
    rtrace_command: str | Path = "rtrace"
    resume: bool = False
    histogram: HistogramSpecification = field(
        default_factory=HistogramSpecification
    )
    criteria: CalibrationCriteria = field(default_factory=CalibrationCriteria)

    def __post_init__(self) -> None:
        output = Path(self.output_directory).expanduser()
        if not output.is_absolute():
            output = (Path.cwd() / output).resolve()
        else:
            output = output.resolve()
        object.__setattr__(self, "output_directory", output)
        for name in ("room_length_ft", "room_width_ft"):
            value = _positive(name, getattr(self, name))
            object.__setattr__(self, name, value)
        plane = _finite("reference_plane_z_m", self.reference_plane_z_m)
        inset = _finite("reference_inset_m", self.reference_inset_m)
        if plane < 0.0 or inset < 0.0:
            raise ValueError("reference plane height and inset must be non-negative.")
        if plane > DEFAULT_ROOM_HEIGHT_FT * FEET_TO_METERS:
            raise ValueError("reference plane must lie within the nominal room height.")
        if 2.0 * inset >= min(
            self.room_length_ft * FEET_TO_METERS,
            self.room_width_ft * FEET_TO_METERS,
        ):
            raise ValueError("reference inset leaves no usable room footprint.")
        object.__setattr__(self, "reference_plane_z_m", plane)
        object.__setattr__(self, "reference_inset_m", inset)
        for name in ("reference_grid_x", "reference_grid_y", "threads"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        if self.reference_grid_x * self.reference_grid_y > MAX_REFERENCE_SAMPLES:
            raise ValueError(
                f"reference grid exceeds the {MAX_REFERENCE_SAMPLES} sample limit."
            )
        quality = str(self.quality).strip().lower()
        if quality not in RADIANCE_QUALITY_NAMES:
            raise ValueError(f"quality must be one of {RADIANCE_QUALITY_NAMES!r}.")
        object.__setattr__(self, "quality", quality)
        levels = tuple(
            _positive("reference level", value)
            for value in self.reference_levels_umol_m2_s
        )
        if len(levels) < 2 or any(
            right <= left for left, right in zip(levels, levels[1:])
        ):
            raise ValueError(
                "reference levels must contain at least two strictly increasing values."
            )
        object.__setattr__(self, "reference_levels_umol_m2_s", levels)
        if self.verification_level_umol_m2_s is None:
            if self.verification_quality is not None:
                raise ValueError(
                    "verification_quality requires verification_level_umol_m2_s."
                )
        else:
            verification_level = _positive(
                "verification_level_umol_m2_s",
                self.verification_level_umol_m2_s,
            )
            if verification_level not in levels:
                raise ValueError("verification level must equal one primary level.")
            verification_quality = str(self.verification_quality or "rigorous").lower()
            if verification_quality not in RADIANCE_QUALITY_NAMES:
                raise ValueError(
                    "verification quality must be one of "
                    f"{RADIANCE_QUALITY_NAMES!r}."
                )
            if _QUALITY_RANK[verification_quality] <= _QUALITY_RANK[quality]:
                raise ValueError("verification quality must be higher than primary quality.")
            object.__setattr__(
                self, "verification_level_umol_m2_s", verification_level
            )
            object.__setattr__(self, "verification_quality", verification_quality)
        for name in ("oconv_command", "rtrace_command"):
            if not str(getattr(self, name)):
                raise ValueError(f"{name} must be non-empty.")
        if not isinstance(self.resume, bool):
            raise ValueError("resume must be boolean.")

    def scientific_payload(self) -> dict[str, object]:
        return {
            "room": {
                "length_ft": self.room_length_ft,
                "width_ft": self.room_width_ft,
                "nominal_height_ft": DEFAULT_ROOM_HEIGHT_FT,
            },
            "reference_plane": {
                "z_m": self.reference_plane_z_m,
                "grid": [self.reference_grid_x, self.reference_grid_y],
                "inset_m": self.reference_inset_m,
                "layout": "centered",
            },
            "quality": self.quality,
            "reference_levels_umol_m2_s": list(
                self.reference_levels_umol_m2_s
            ),
            "verification": (
                None
                if self.verification_level_umol_m2_s is None
                else {
                    "level_umol_m2_s": self.verification_level_umol_m2_s,
                    "quality": self.verification_quality,
                }
            ),
            "threads": self.threads,
            "radiance_commands": {
                "oconv": str(self.oconv_command),
                "rtrace": str(self.rtrace_command),
            },
            "histogram": self.histogram.to_dict(),
            "criteria": self.criteria.to_dict(),
        }

    def cli_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {
            "output_directory": str(self.output_directory),
            "resume_requested": self.resume,
        }


@dataclass(frozen=True, slots=True)
class CalibrationPublication:
    report_path: Path
    report: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _Job:
    job_id: str
    kind: str
    requested_level: float
    quality: str

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "requested_level_umol_m2_s": self.requested_level,
            "quality": self.quality,
        }


def canonical_neutral_source_definition() -> dict[str, object]:
    """Return the intensity-independent source document and its declarations."""

    fractions = {band_id: 0.25 for band_id in PAR_BAND_ORDER}
    normalized_text = render_neutral_source(1.0)
    payload: dict[str, object] = {
        "source_model_id": SOURCE_MODEL_ID,
        "field": {
            "spatial_domain": "distant environment",
            "radiance_over_domain": "constant",
            "direction": list(SOURCE_DIRECTION),
            "angular_diameter_degrees": SOURCE_ANGLE_DEGREES,
            "emitting_domain": "complete downward upper hemisphere",
            "incident_direction_domain_at_receivers": "upper hemisphere",
            "polar_radiance_distribution": "Lambertian/isotropic radiance",
            "azimuthal_distribution": "uniform",
        },
        "radiance_primitives": {
            "glow_modifier": SOURCE_GLOW_MODIFIER,
            "source_primitive": SOURCE_PRIMITIVE,
            "normalized_unit_radiance_text": normalized_text,
            "amplitude_binding": (
                "the three glow channels receive one common scalar amplitude"
            ),
        },
        "spectrum": {
            "model_id": SPECTRUM_MODEL_ID,
            "photon_fractions": fractions,
            "sum": math.fsum(fractions.values()),
            "included_band_order": list(PAR_BAND_ORDER),
            "excluded_intervals": ["far_red"],
        },
        "independence_declaration": {
            "modeled_product_resources_used": False,
            "source_layout_used": False,
            "source_distribution_file_used": False,
            "source_operating_program_used": False,
            "evaluated_run_used_to_estimate_coefficients": False,
        },
    }
    payload["source_definition_sha256"] = _hash_json(payload)
    return payload


def render_neutral_source(amplitude: float) -> str:
    """Render one grayscale upper-hemisphere source at a supplied amplitude."""

    value = _positive("source amplitude", amplitude)
    number = format(value, ".17g")
    return (
        f"# source_model_id={SOURCE_MODEL_ID}\n"
        f"void glow {SOURCE_GLOW_MODIFIER}\n"
        "0\n"
        "0\n"
        f"4 {number} {number} {number} 0\n\n"
        f"{SOURCE_GLOW_MODIFIER} source {SOURCE_PRIMITIVE}\n"
        "0\n"
        "0\n"
        "4 0 0 1 180\n"
    )


def canonical_calibration_boundary_text() -> str:
    """Return the deliberate no-geometry boundary document."""

    return (
        f"# boundary_model_id={BOUNDARY_MODEL_ID}\n"
        "# No room surface is present in the reference transport.\n"
        "# The open boundary exposes the complete upper-hemisphere source.\n"
        "# Room dimensions define only the sensor footprint and plant placement.\n"
    )


def calibration_scene_identity(
    config: SurfaceFluxCalibrationConfig,
    scene: JuvenileScientificScene,
    material_plan: RexRadianceTransMaterialPlan,
) -> dict[str, object]:
    """Build the intensity-invariant calibration scene identity."""

    material_payload = material_plan.to_payload()
    payload: dict[str, object] = {
        "boundary_model_id": BOUNDARY_MODEL_ID,
        "boundary_text_sha256": _sha256_text(
            canonical_calibration_boundary_text()
        ),
        "normal_room_boundary_included": False,
        "boundary_difference_scope": (
            "reference experiment only; plant geometry and receiver topology unchanged"
        ),
        "room_dimensions_define": [
            "horizontal reference-plane footprint",
            "frozen natural-fit juvenile placement",
        ],
        "room": {
            "length_ft": config.room_length_ft,
            "width_ft": config.room_width_ft,
            "nominal_height_ft": DEFAULT_ROOM_HEIGHT_FT,
        },
        "reference_plane": {
            "z_m": config.reference_plane_z_m,
            "resolution": [
                config.reference_grid_x,
                config.reference_grid_y,
            ],
            "inset_m": config.reference_inset_m,
            "layout": "centered",
            "receiver_direction": [0.0, 0.0, 1.0],
        },
        "plant": {
            "scene_hash": scene.scene_hash,
            "layout_plan_hash": scene.layout_plan_hash,
            "topology_sha256": scene.topology.topology_sha256,
            "receivers_sha256": scene.topology.receivers_sha256,
            "counts": scene.counts.to_payload(),
        },
        "source_definition_sha256": canonical_neutral_source_definition()[
            "source_definition_sha256"
        ],
        "material_plan_sha256": _hash_json(material_payload),
        "stage_a": {
            "plants_present": False,
            "ordered_scene_roles": ["open_boundary", "neutral_source"],
        },
        "stage_b": {
            "plants_present": True,
            "ordered_scene_roles": [
                "open_boundary",
                "neutral_source",
                "authoritative_leaf_material",
                "frozen_juvenile_geometry",
            ],
        },
    }
    payload["calibration_scene_sha256"] = _hash_json(payload)
    return payload


def configuration_identity(
    config: SurfaceFluxCalibrationConfig,
    *,
    scene: JuvenileScientificScene,
    material_plan: RexRadianceTransMaterialPlan,
    radiance_installation: RadianceInstallation,
    repository_revision: str | None,
) -> dict[str, object]:
    """Return the exact resumable identity, excluding only output location/mode."""

    payload: dict[str, object] = {
        "schema_id": CONFIGURATION_SCHEMA_ID,
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "scientific_configuration": config.scientific_payload(),
        "neutral_source": canonical_neutral_source_definition(),
        "calibration_scene": calibration_scene_identity(
            config, scene, material_plan
        ),
        "material_plan_sha256": _hash_json(material_plan.to_payload()),
        "radiance_installation": radiance_installation.to_dict(),
        "repository_revision": repository_revision,
    }
    payload["configuration_sha256"] = _hash_json(payload)
    return payload


class _FixedHistogram:
    def __init__(self, specification: HistogramSpecification) -> None:
        self.specification = specification
        self.counts = [0] * specification.bin_count
        self.areas = [
            CompensatedNonnegativeSum()
            for _ in range(specification.bin_count)
        ]
        self.underflow_count = 0
        self.overflow_count = 0
        self.underflow_area = CompensatedNonnegativeSum()
        self.overflow_area = CompensatedNonnegativeSum()

    def add(self, value: float, area_m2: float) -> None:
        x = _finite("normalized receiver density", value)
        area = _positive("histogram area", area_m2)
        if x < self.specification.minimum:
            self.underflow_count += 1
            self.underflow_area.add(area)
            return
        if x >= self.specification.maximum:
            self.overflow_count += 1
            self.overflow_area.add(area)
            return
        index = int(
            (x - self.specification.minimum) / self.specification.bin_width
        )
        index = min(self.specification.bin_count - 1, max(0, index))
        self.counts[index] += 1
        self.areas[index].add(area)

    def area_vector(self) -> tuple[float, ...]:
        return (
            self.underflow_area.value,
            *(value.value for value in self.areas),
            self.overflow_area.value,
        )

    def payload(self, total_area_m2: float) -> dict[str, object]:
        total = _positive("histogram total area", total_area_m2)
        areas = [value.value for value in self.areas]
        observed = math.fsum(
            (self.underflow_area.value, *areas, self.overflow_area.value)
        )
        if not math.isclose(observed, total, rel_tol=1e-12, abs_tol=1e-12):
            raise SurfaceFluxCalibrationError(
                "histogram area does not conserve modeled one-sided area."
            )
        return {
            "bin_counts": list(self.counts),
            "bin_areas_m2": areas,
            "bin_area_fractions": [area / total for area in areas],
            "underflow": {
                "count": self.underflow_count,
                "area_m2": self.underflow_area.value,
                "area_fraction": self.underflow_area.value / total,
            },
            "overflow": {
                "count": self.overflow_count,
                "area_m2": self.overflow_area.value,
                "area_fraction": self.overflow_area.value / total,
            },
            "fixed_quantile_estimates": self._quantiles(total),
        }

    def _quantiles(self, total_area_m2: float) -> dict[str, object]:
        results: dict[str, object] = {}
        boundaries = self.specification.boundaries
        for probability in (0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99):
            threshold = probability * total_area_m2
            cumulative = self.underflow_area.value
            key = format(probability, ".2f")
            if cumulative >= threshold:
                results[key] = {"status": "underflow", "estimate": None}
                continue
            resolved = False
            for index, area in enumerate(self.areas):
                next_cumulative = cumulative + area.value
                if next_cumulative >= threshold and area.value > 0.0:
                    fraction = (threshold - cumulative) / area.value
                    estimate = boundaries[index] + fraction * (
                        boundaries[index + 1] - boundaries[index]
                    )
                    results[key] = {
                        "status": "within_histogram",
                        "estimate": estimate,
                    }
                    resolved = True
                    break
                cumulative = next_cumulative
            if not resolved:
                results[key] = {"status": "overflow", "estimate": None}
        return results


class _MetricAccumulator:
    def __init__(
        self,
        reference_mean: float,
        histogram: HistogramSpecification,
    ) -> None:
        self.reference_mean = _positive("reference mean", reference_mean)
        self.count = 0
        self.area = CompensatedNonnegativeSum()
        self.weighted_sum = CompensatedNonnegativeSum()
        self.weighted_square_sum = CompensatedNonnegativeSum()
        self.minimum = math.inf
        self.maximum = -math.inf
        self.histogram = _FixedHistogram(histogram)

    def add(self, density: float, area_m2: float) -> None:
        value = _finite_nonnegative("receiver density", density)
        area = _positive("patch area", area_m2)
        self.count += 1
        self.area.add(area)
        self.weighted_sum.add(area * value)
        self.weighted_square_sum.add(area * value * value)
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)
        self.histogram.add(value / self.reference_mean, area)

    def payload(self) -> dict[str, object]:
        if self.count <= 0:
            raise SurfaceFluxCalibrationError("surface metric has no patches.")
        area = _positive("modeled one-sided area", self.area.value)
        mean = self.weighted_sum.value / area
        second_moment = self.weighted_square_sum.value / area
        variance = second_moment - mean * mean
        if variance < 0.0 and abs(variance) <= 1e-12 * max(1.0, second_moment):
            variance = 0.0
        if variance < 0.0 or not math.isfinite(variance):
            raise SurfaceFluxCalibrationError("weighted variance is invalid.")
        standard_deviation = math.sqrt(variance)
        return {
            "patch_count": self.count,
            "physical_one_sided_leaf_area_m2": area,
            "area_weighted_mean_density_umol_m2_s": mean,
            "area_weighted_population_standard_deviation_umol_m2_s": (
                standard_deviation
            ),
            "area_weighted_coefficient_of_variation": (
                0.0 if mean == 0.0 else standard_deviation / mean
            ),
            "minimum_raw_density_umol_m2_s": self.minimum,
            "maximum_raw_density_umol_m2_s": self.maximum,
            "beta_level": mean / self.reference_mean,
            "distribution": self.histogram.payload(area),
        }


class _ScalarMetrics:
    def __init__(self) -> None:
        self.count = 0
        self.total = CompensatedNonnegativeSum()
        self.square_total = CompensatedNonnegativeSum()
        self.minimum = math.inf
        self.maximum = -math.inf

    def add(self, value: float) -> None:
        number = _finite_nonnegative("reference-plane density", value)
        self.count += 1
        self.total.add(number)
        self.square_total.add(number * number)
        self.minimum = min(self.minimum, number)
        self.maximum = max(self.maximum, number)

    def payload(self) -> dict[str, object]:
        if self.count <= 0:
            raise SurfaceFluxCalibrationError("reference-plane trace has no rows.")
        mean = self.total.value / self.count
        if mean <= 0.0:
            raise SurfaceFluxCalibrationError(
                "reference-plane mean must be positive."
            )
        variance = self.square_total.value / self.count - mean * mean
        if variance < 0.0 and abs(variance) <= 1e-12 * max(1.0, mean * mean):
            variance = 0.0
        if variance < 0.0 or not math.isfinite(variance):
            raise SurfaceFluxCalibrationError(
                "reference-plane population variance is invalid."
            )
        standard_deviation = math.sqrt(variance)
        cv = standard_deviation / mean
        return {
            "sample_count": self.count,
            "mean_ppfd_umol_m2_s": mean,
            "minimum_ppfd_umol_m2_s": self.minimum,
            "maximum_ppfd_umol_m2_s": self.maximum,
            "population_standard_deviation_ppfd_umol_m2_s": standard_deviation,
            "coefficient_of_variation": cv,
            "coefficient_of_variation_percent": cv * 100.0,
            "degree_of_uniformity_percent": 100.0 - cv * 100.0,
            "minimum_to_mean": self.minimum / mean,
            "minimum_to_maximum": self.minimum / self.maximum,
        }


def run_surface_flux_calibration(
    config: SurfaceFluxCalibrationConfig,
    *,
    runner: CommandRunner | None = None,
    radiance_installation: RadianceInstallation | None = None,
    event_sink: EventSink | None = None,
    created_at_utc: str | None = None,
    repository_root: Path | None = None,
) -> CalibrationPublication:
    """Execute or exactly resume the complete calibration experiment."""

    if not isinstance(config, SurfaceFluxCalibrationConfig):
        raise TypeError("config must be SurfaceFluxCalibrationConfig.")
    sink = event_sink or (lambda _message, _data=None: None)
    output = config.output_directory
    repository = (
        _discover_repository_root()
        if repository_root is None
        else Path(repository_root).resolve()
    )
    _validate_output_location(output, repository)
    try:
        installation = radiance_installation or discover_radiance_installation(
            oconv_command=config.oconv_command,
            rtrace_command=config.rtrace_command,
            cwd=repository,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise SurfaceFluxCalibrationError(
            f"Radiance executable discovery failed: {exc}"
        ) from exc
    revision = _repository_revision(repository)
    try:
        layout = plan_natural_fit_layout_from_feet(
            config.room_length_ft,
            config.room_width_ft,
        )
        scene = build_juvenile_natural_fit_scene(
            layout,
            sampling_profile_id=(
                REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID
            ),
        )
        material_plan = build_rex_radiance_trans_material_plan()
    except (OSError, RuntimeError, ValueError) as exc:
        raise SurfaceFluxCalibrationError(
            f"canonical scene or material planning failed: {exc}"
        ) from exc
    identity = configuration_identity(
        config,
        scene=scene,
        material_plan=material_plan,
        radiance_installation=installation,
        repository_revision=revision,
    )
    configuration_sha256 = str(identity["configuration_sha256"])
    created = created_at_utc or _utc_now()
    state = _prepare_output_root(
        config,
        identity=identity,
        created_at_utc=created,
    )
    created = str(state["created_at_utc"])
    native_runner = runner or LocalRunner()
    levels_root = output / "levels"
    levels_root.mkdir(exist_ok=True)
    if config.resume:
        _clean_interrupted_level_stages(levels_root)
    jobs = _jobs(config)
    results: list[dict[str, object]] = []
    for index, job in enumerate(jobs):
        final_root = levels_root / job.job_id
        if final_root.exists():
            if not config.resume:
                raise SurfaceFluxCalibrationError(
                    f"completed level already exists without --resume: {job.job_id}"
                )
            sink(
                f"Validating completed calibration level {job.job_id}.",
                {"job": job.to_dict(), "order_index": index},
            )
            results.append(
                _validate_completed_level(
                    final_root,
                    job=job,
                    configuration_sha256=configuration_sha256,
                )
            )
            continue
        sink(
            f"Executing calibration level {job.job_id}.",
            {"job": job.to_dict(), "order_index": index},
        )
        stage = Path(
            tempfile.mkdtemp(
                prefix=f".{job.job_id}.", suffix=".tmp", dir=levels_root
            )
        )
        try:
            result = _execute_level(
                stage,
                config=config,
                job=job,
                scene=scene,
                material_plan=material_plan,
                runner=native_runner,
                installation=installation,
                configuration_sha256=configuration_sha256,
                event_sink=sink,
            )
            result_path = stage / LEVEL_RESULT_NAME
            atomic_write_text(result_path, _pretty_json(result))
            inventory = _inventory(stage, excluded={COMPLETION_NAME})
            completion = {
                "schema_id": COMPLETION_SCHEMA_ID,
                "schema_version": COMPLETION_SCHEMA_VERSION,
                "configuration_sha256": configuration_sha256,
                "job": job.to_dict(),
                "level_result": {
                    "path": LEVEL_RESULT_NAME,
                    "sha256": _sha256_file(result_path),
                },
                "ordered_artifact_inventory": inventory,
                "completion_policy": (
                    "all retained artifacts hashed before same-filesystem directory commit"
                ),
            }
            atomic_write_text(stage / COMPLETION_NAME, _pretty_json(completion))
            os.replace(stage, final_root)
            results.append(
                _validate_completed_level(
                    final_root,
                    job=job,
                    configuration_sha256=configuration_sha256,
                )
            )
        except BaseException:
            _remove_stage(stage, levels_root)
            raise
        sink(
            f"Completed calibration level {job.job_id}.",
            {"job": job.to_dict(), "order_index": index},
        )

    primary = [value for value in results if value["kind"] == "primary"]
    verification = next(
        (value for value in results if value["kind"] == "verification"),
        None,
    )
    analysis = analyze_calibration_sweep(
        primary,
        criteria=config.criteria,
    )
    convergence = _verification_analysis(
        primary,
        verification,
        criteria=config.criteria,
    )
    report = _build_report(
        config=config,
        identity=identity,
        created_at_utc=created,
        scene=scene,
        material_plan=material_plan,
        results=results,
        sweep_analysis=analysis,
        convergence=convergence,
        output=output,
    )
    report_path = output / REPORT_NAME
    atomic_write_text(report_path, _pretty_json(report))
    sink(
        "Published the compact surface-flux calibration report.",
        {
            "report": str(report_path),
            "overall_pass": report["acceptance"]["pass"],
        },
    )
    return CalibrationPublication(report_path=report_path, report=report)


def _execute_level(
    root: Path,
    *,
    config: SurfaceFluxCalibrationConfig,
    job: _Job,
    scene: JuvenileScientificScene,
    material_plan: RexRadianceTransMaterialPlan,
    runner: CommandRunner,
    installation: RadianceInstallation,
    configuration_sha256: str,
    event_sink: EventSink,
) -> dict[str, object]:
    boundary_path = root / "scene-boundary.rad"
    atomic_write_text(boundary_path, canonical_calibration_boundary_text())
    scene_root = root / "scene"
    try:
        export = materialize_juvenile_radiance_export(
            plan_juvenile_radiance_export(scene),
            scene_root,
        )
        _validate_export_artifacts(export.artifacts, scene_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SurfaceFluxCalibrationError(
            f"juvenile scene export failed: {exc}"
        ) from exc
    room = RoomDimensions.from_feet(
        length_ft=config.room_length_ft,
        width_ft=config.room_width_ft,
        height_ft=DEFAULT_ROOM_HEIGHT_FT,
    )
    grid = SensorGridSpec(
        room=room,
        resolution_x=config.reference_grid_x,
        resolution_y=config.reference_grid_y,
        canopy_height_m=config.reference_plane_z_m,
        inset_m=config.reference_inset_m,
        layout="centered",
    )
    points = generate_sensor_points(grid)
    reference_root = root / "reference"
    reference_root.mkdir()
    sensor_path = reference_root / "horizontal-reference-plane.pts"
    atomic_write_text(sensor_path, format_rtrace_receivers(points))
    commands: list[dict[str, object]] = []
    pilot = _execute_reference_trace(
        root,
        reference_root / "pilot",
        phase="pilot",
        amplitude=1.0,
        quality=job.quality,
        threads=config.threads,
        expected_count=len(points),
        boundary_path=boundary_path,
        sensor_path=sensor_path,
        runner=runner,
        installation=installation,
        job=job,
    )
    commands.extend(pilot.pop("commands"))
    pilot_mean = _positive(
        "unit-amplitude reference mean",
        pilot["metrics"]["mean_ppfd_umol_m2_s"],
    )
    resolved_amplitude = job.requested_level / pilot_mean
    final = _execute_reference_trace(
        root,
        reference_root / "final",
        phase="final",
        amplitude=resolved_amplitude,
        quality=job.quality,
        threads=config.threads,
        expected_count=len(points),
        boundary_path=boundary_path,
        sensor_path=sensor_path,
        runner=runner,
        installation=installation,
        job=job,
    )
    commands.extend(final.pop("commands"))
    achieved_mean = _positive(
        "achieved Stage A mean",
        final["metrics"]["mean_ppfd_umol_m2_s"],
    )
    reference_cv = _finite_nonnegative(
        "reference-plane CV",
        final["metrics"]["coefficient_of_variation"],
    )
    if reference_cv > config.criteria.maximum_reference_plane_cv:
        raise SurfaceFluxCalibrationError(
            "canonical open-boundary upper-hemisphere field failed the declared "
            f"reference-plane CV gate: {reference_cv:.9g} > "
            f"{config.criteria.maximum_reference_plane_cv:.9g}."
        )
    event_sink(
        f"Resolved plant-free reference amplitude for {job.job_id}.",
        {
            "requested_level_umol_m2_s": job.requested_level,
            "achieved_mean_umol_m2_s": achieved_mean,
            "reference_plane_cv": reference_cv,
        },
    )

    band_records: list[dict[str, object]] = []
    bands_root = root / "bands"
    bands_root.mkdir()
    band_amplitude = resolved_amplitude / len(PAR_BAND_ORDER)
    for order_index, band in enumerate(PAR_BANDS):
        band_root = bands_root / f"{order_index:02d}-{band.band_id}"
        band_root.mkdir()
        source_path = band_root / "source.rad"
        material_path = band_root / "leaf-material.rad"
        octree_path = band_root / "scene.oct"
        ambient_path = band_root / "scene.amb"
        raw_rgb_path = band_root / "receiver.rgb"
        values_path = band_root / "receiver-values.v1.f64le.bin"
        oconv_stderr = band_root / "oconv.stderr.log"
        rtrace_stderr = band_root / "rtrace.stderr.log"
        material = material_plan.material(band.band_id)
        source_text = render_neutral_source(band_amplitude)
        material_text = render_radiance_trans_material(
            DEFAULT_LEAF_MATERIAL_MODIFIER,
            material.parameters,
        )
        atomic_write_text(source_path, source_text)
        atomic_write_text(material_path, material_text)
        options = tuple(
            radiance_options(
                job.quality,
                ambient_cache=(
                    None if job.quality == "direct" else ambient_path
                ),
            )
        )
        oconv = build_oconv_command(
            (boundary_path, source_path, material_path, export.geometry_path),
            output_octree=octree_path,
            cwd=root,
            oconv_bin=installation.oconv.path,
            label=(
                f"surface_flux_calibration_{job.job_id}_{band.band_id}_oconv"
            ),
        )
        raw_rtrace = build_plant_receiver_rtrace_command(
            octree=octree_path,
            receiver_input=export.receiver_input_path,
            rgb_output=raw_rgb_path,
            options=options,
            nthreads=config.threads,
            cwd=root,
            rtrace_bin=installation.rtrace.path,
        )
        rtrace = _relabel(
            raw_rtrace,
            f"surface_flux_calibration_{job.job_id}_{band.band_id}_rtrace",
        )
        _run_required(runner, oconv, oconv_stderr)
        _require_nonempty(octree_path, f"{band.band_id} octree")
        _run_required(runner, rtrace, rtrace_stderr)
        _require_nonempty(raw_rgb_path, f"{band.band_id} receiver output")
        receiver_artifact = _decode_receiver_rgb(
            raw_rgb_path,
            values_path,
            expected_count=scene.counts.receiver_count,
            band_id=band.band_id,
            root=root,
        )
        material_artifact = _artifact(
            root, material_path, role=f"{band.band_id}_leaf_material"
        )
        band_records.append(
            {
                "order_index": order_index,
                "band_id": band.band_id,
                "definition": band.to_dict(),
                "source": {
                    "source_model_id": SOURCE_MODEL_ID,
                    "source_definition_sha256": (
                        canonical_neutral_source_definition()[
                            "source_definition_sha256"
                        ]
                    ),
                    "photon_fraction": 0.25,
                    "rendered_amplitude": band_amplitude,
                    "same_spatial_and_angular_field_for_every_band": True,
                },
                "source_sha256": _sha256_file(source_path),
                "material": material.to_dict(),
                "material_sha256": _sha256_file(material_path),
                "material_artifact": material_artifact,
                "receiver_values": receiver_artifact,
                "commands": {
                    "oconv": _command_payload(oconv, root),
                    "rtrace": _command_payload(rtrace, root),
                },
                "post_trace_transforms": [],
            }
        )
        commands.extend(
            (_command_payload(oconv, root), _command_payload(rtrace, root))
        )
        event_sink(
            f"Validated {band.band_id} receivers for {job.job_id}.",
            {"band_id": band.band_id, "receiver_count": scene.counts.receiver_count},
        )

    accumulators = {
        name: _MetricAccumulator(achieved_mean, config.histogram)
        for name in SIDE_METRICS
    }

    def consume(patch: ParPatchSurfaceLight) -> None:
        area = patch.physical_one_sided_patch_area_m2
        accumulators["front_incident"].add(
            patch.front_incident_photon_flux_density_umol_m2_s, area
        )
        accumulators["back_incident"].add(
            patch.back_incident_photon_flux_density_umol_m2_s, area
        )
        accumulators["front_absorbed"].add(
            patch.front_absorbed_photon_flux_density_umol_m2_s, area
        )
        accumulators["back_absorbed"].add(
            patch.back_absorbed_photon_flux_density_umol_m2_s, area
        )

    try:
        aggregation = stream_juvenile_par_surface_light(
            root=root,
            scene=scene,
            band_records=band_records,
            patch_sink=consume,
            chunk_bytes=DEFAULT_RAW_CHUNK_BYTES,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise SurfaceFluxCalibrationError(
            f"authoritative four-band surface aggregation failed: {exc}"
        ) from exc
    statistics = {name: value.payload() for name, value in accumulators.items()}
    expected_area = aggregation.modeled_physical_one_sided_leaf_area_m2
    if any(
        not math.isclose(
            result["physical_one_sided_leaf_area_m2"],
            expected_area,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        for result in statistics.values()
    ):
        raise SurfaceFluxCalibrationError(
            "surface statistics do not conserve authoritative modeled area."
        )
    return {
        "schema_id": LEVEL_SCHEMA_ID,
        "schema_version": LEVEL_SCHEMA_VERSION,
        "configuration_sha256": configuration_sha256,
        "job_id": job.job_id,
        "artifact_root_from_report": f"levels/{job.job_id}",
        "kind": job.kind,
        "quality": job.quality,
        "requested_reference_level_umol_m2_s": job.requested_level,
        "reference": {
            "plant_free": True,
            "horizontal_sensor_grid": _artifact(
                root, sensor_path, role="horizontal_reference_plane_receivers"
            ),
            "amplitude_resolution": {
                "method": "independent unit-amplitude pilot then independent final trace",
                "pilot": pilot,
                "resolved_total_source_radiance_amplitude": resolved_amplitude,
                "final": final,
                "requested_minus_achieved_umol_m2_s": (
                    job.requested_level - achieved_mean
                ),
                "relative_achievement_error": (
                    achieved_mean / job.requested_level - 1.0
                ),
            },
            "acceptance": {
                "maximum_cv": config.criteria.maximum_reference_plane_cv,
                "observed_cv": reference_cv,
                "pass": reference_cv
                <= config.criteria.maximum_reference_plane_cv,
            },
        },
        "plant_transport": {
            "independent_execution": True,
            "band_order": list(PAR_BAND_ORDER),
            "band_count": len(PAR_BAND_ORDER),
            "far_red_executed": False,
            "equal_photon_fraction_per_band": 0.25,
            "band_source_amplitude": band_amplitude,
            "receiver_count_per_band": scene.counts.receiver_count,
            "bands": band_records,
        },
        "scene_identity": calibration_scene_identity(
            config, scene, material_plan
        ),
        "juvenile_identity": {
            "scene_hash": scene.scene_hash,
            "layout_plan_hash": scene.layout_plan_hash,
            "topology_sha256": scene.topology.topology_sha256,
            "receivers_sha256": scene.topology.receivers_sha256,
            "export_plan_hash": export.plan.export_plan_hash,
            "export_manifest_sha256": export.manifest_artifact.sha256,
        },
        "statistics": statistics,
        "validation": aggregation.to_dict()
        | {
            "reference_plane_uniformity_pass": True,
            "all_receiver_values_finite_and_nonnegative": True,
            "receiver_identity_and_order_validated": True,
            "material_provenance_validated": True,
            "all_numerical_and_conservation_validations_pass": True,
        },
        "command_count": len(commands),
        "commands": commands,
        "memory_policy": {
            "raw_receiver_decode": "one text row and one Float64 at a time",
            "surface_derivation": "one canonical patch plus hierarchical accumulators",
            "distribution": (
                f"four fixed histograms of {config.histogram.bin_count} bins"
            ),
            "full_receiver_arrays_in_report": False,
            "nested_receiver_objects_in_report": False,
        },
    }


def _execute_reference_trace(
    level_root: Path,
    trace_root: Path,
    *,
    phase: str,
    amplitude: float,
    quality: str,
    threads: int,
    expected_count: int,
    boundary_path: Path,
    sensor_path: Path,
    runner: CommandRunner,
    installation: RadianceInstallation,
    job: _Job,
) -> dict[str, object]:
    trace_root.mkdir()
    source_path = trace_root / "source.rad"
    octree_path = trace_root / "scene.oct"
    ambient_path = trace_root / "scene.amb"
    raw_rgb_path = trace_root / "reference.rgb"
    oconv_stderr = trace_root / "oconv.stderr.log"
    rtrace_stderr = trace_root / "rtrace.stderr.log"
    atomic_write_text(source_path, render_neutral_source(amplitude))
    options = tuple(
        radiance_options(
            quality,
            ambient_cache=None if quality == "direct" else ambient_path,
        )
    )
    oconv = build_oconv_command(
        (boundary_path, source_path),
        output_octree=octree_path,
        cwd=level_root,
        oconv_bin=installation.oconv.path,
        label=f"surface_flux_calibration_{job.job_id}_reference_{phase}_oconv",
    )
    raw_rtrace = build_baseline_rtrace_command(
        octree=octree_path,
        receiver_input=sensor_path,
        rgb_output=raw_rgb_path,
        options=options,
        nthreads=threads,
        cwd=level_root,
        rtrace_bin=installation.rtrace.path,
    )
    rtrace = _relabel(
        raw_rtrace,
        f"surface_flux_calibration_{job.job_id}_reference_{phase}_rtrace",
    )
    _run_required(runner, oconv, oconv_stderr)
    _require_nonempty(octree_path, f"reference {phase} octree")
    _run_required(runner, rtrace, rtrace_stderr)
    _require_nonempty(raw_rgb_path, f"reference {phase} output")
    metrics = _decode_reference_metrics(raw_rgb_path, expected_count=expected_count)
    return {
        "source_amplitude": amplitude,
        "source_artifact": _artifact(
            level_root, source_path, role=f"reference_{phase}_source"
        ),
        "raw_reference_artifact": _artifact(
            level_root, raw_rgb_path, role=f"reference_{phase}_raw_rgb"
        ),
        "metrics": metrics,
        "commands": [
            _command_payload(oconv, level_root),
            _command_payload(rtrace, level_root),
        ],
        "plants_present": False,
    }


def _decode_reference_metrics(
    path: Path,
    *,
    expected_count: int,
) -> dict[str, object]:
    metrics = _ScalarMetrics()
    row_count = 0
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row_count, line in enumerate(handle, start=1):
                if row_count > expected_count:
                    raise SurfaceFluxCalibrationError(
                        "reference trace contains extra rows."
                    )
                parts = line.split()
                if len(parts) != 3:
                    raise SurfaceFluxCalibrationError(
                        f"reference row {row_count} must contain three channels."
                    )
                try:
                    metrics.add(
                        decode_grey_channel_ppfd(
                            *(float(value) for value in parts),
                            row_number=row_count,
                        )
                    )
                except ValueError as exc:
                    raise SurfaceFluxCalibrationError(
                        f"reference row {row_count} is invalid: {exc}"
                    ) from exc
    except (OSError, UnicodeError) as exc:
        raise SurfaceFluxCalibrationError(
            f"reference trace could not be decoded: {exc}"
        ) from exc
    if row_count != expected_count:
        raise SurfaceFluxCalibrationError(
            f"reference row count mismatch: expected {expected_count}, got {row_count}."
        )
    return metrics.payload()


def _decode_receiver_rgb(
    raw_rgb_path: Path,
    output_path: Path,
    *,
    expected_count: int,
    band_id: str,
    root: Path,
) -> dict[str, object]:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    temporary = Path(temporary_name)
    row_count = 0
    digest = hashlib.sha256()
    try:
        with raw_rgb_path.open("r", encoding="utf-8", newline="") as source, os.fdopen(
            descriptor, "wb"
        ) as destination:
            for row_count, line in enumerate(source, start=1):
                if row_count > expected_count:
                    raise SurfaceFluxCalibrationError(
                        f"{band_id} produced extra receiver rows."
                    )
                parts = line.split()
                if len(parts) != 3:
                    raise SurfaceFluxCalibrationError(
                        f"{band_id} row {row_count} must contain three channels."
                    )
                try:
                    value = decode_grey_channel_ppfd(
                        *(float(item) for item in parts),
                        row_number=row_count,
                    )
                except ValueError as exc:
                    raise SurfaceFluxCalibrationError(
                        f"{band_id} row {row_count} is invalid: {exc}"
                    ) from exc
                encoded = struct.pack("<d", value)
                destination.write(encoded)
                digest.update(encoded)
            destination.flush()
            os.fsync(destination.fileno())
        if row_count != expected_count:
            raise SurfaceFluxCalibrationError(
                f"{band_id} receiver row count mismatch: expected {expected_count}, "
                f"got {row_count}."
            )
        os.replace(temporary, output_path)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
    return {
        "role": f"{band_id}_receiver_values",
        "path": _relative(root, output_path),
        "media_type": "application/octet-stream",
        "byte_length": expected_count * 8,
        "sha256": digest.hexdigest(),
        "row_count": expected_count,
        "stride_bytes": 8,
    }


def analyze_calibration_sweep(
    level_results: Sequence[Mapping[str, object]],
    *,
    criteria: CalibrationCriteria | None = None,
) -> dict[str, object]:
    """Calculate declared regression and normalized-distribution diagnostics."""

    if criteria is None:
        criteria = CalibrationCriteria()

    levels = tuple(level_results)
    if len(levels) < 2:
        raise SurfaceFluxCalibrationError(
            "sweep analysis requires at least two completed primary levels."
        )
    requested = [
        _positive(
            "requested reference level",
            value.get("requested_reference_level_umol_m2_s"),
        )
        for value in levels
    ]
    if any(right <= left for left, right in zip(requested, requested[1:])):
        raise SurfaceFluxCalibrationError(
            "primary level results are not in strictly increasing order."
        )
    achieved = [
        _positive(
            "achieved reference mean",
            _nested(
                value,
                "reference",
                "amplitude_resolution",
                "final",
                "metrics",
                "mean_ppfd_umol_m2_s",
            ),
        )
        for value in levels
    ]
    analyses: dict[str, object] = {}
    for metric in SIDE_METRICS:
        means = [
            _positive_or_zero(
                "surface mean",
                _nested(
                    value,
                    "statistics",
                    metric,
                    "area_weighted_mean_density_umol_m2_s",
                ),
            )
            for value in levels
        ]
        betas = [
            _positive_or_zero(
                "beta_level",
                _nested(value, "statistics", metric, "beta_level"),
            )
            for value in levels
        ]
        through_slope = _through_origin_slope(achieved, means)
        through_r_squared = _r_squared(
            means,
            [through_slope * value for value in achieved],
        )
        unconstrained_slope, unconstrained_intercept = _linear_fit(
            achieved, means
        )
        unconstrained_r_squared = _r_squared(
            means,
            [
                unconstrained_slope * value + unconstrained_intercept
                for value in achieved
            ],
        )
        beta_mean = math.fsum(betas) / len(betas)
        beta_drift = (
            0.0
            if beta_mean == 0.0
            else (max(betas) - min(betas)) / beta_mean
        )
        maximum_fit_relative_deviation = (
            0.0
            if through_slope == 0.0
            else max(abs(value / through_slope - 1.0) for value in betas)
        )
        pairwise_drifts = _pairwise_distribution_drifts(levels, metric)
        mean_distribution_drift = (
            math.fsum(pairwise_drifts) / len(pairwise_drifts)
            if pairwise_drifts
            else 0.0
        )
        maximum_distribution_drift = max(pairwise_drifts, default=0.0)
        validation_pass = all(
            _nested(
                value,
                "validation",
                "all_numerical_and_conservation_validations_pass",
            )
            is True
            and _nested(value, "reference", "acceptance", "pass") is True
            for value in levels
        )
        metric_pass = (
            through_slope > 0.0
            and through_r_squared >= criteria.minimum_through_origin_r_squared
            and beta_drift <= criteria.maximum_beta_drift
            and validation_pass
        )
        analyses[metric] = {
            "through_origin": {
                "equation": "q_mean = beta * achieved_stage_a_mean",
                "slope_beta": through_slope,
                "r_squared": through_r_squared,
                "r_squared_definition": (
                    "1 - sum((observed - beta*x)^2) / "
                    "sum((observed - mean(observed))^2)"
                ),
            },
            "unconstrained_diagnostic": {
                "slope": unconstrained_slope,
                "intercept_umol_m2_s": unconstrained_intercept,
                "r_squared": unconstrained_r_squared,
                "used_for_acceptance": False,
            },
            "beta_level_values": betas,
            "beta_level_mean": beta_mean,
            "maximum_relative_beta_drift": beta_drift,
            "maximum_relative_deviation_from_fitted_beta": (
                maximum_fit_relative_deviation
            ),
            "normalized_distribution_drift": {
                "metric": "area-weighted total-variation distance",
                "pairing": "all unordered sweep-level pairs",
                "pairwise_values": pairwise_drifts,
                "mean": mean_distribution_drift,
                "maximum": maximum_distribution_drift,
            },
            "criteria": {
                "minimum_through_origin_r_squared": (
                    criteria.minimum_through_origin_r_squared
                ),
                "maximum_relative_beta_drift": criteria.maximum_beta_drift,
                "all_numerical_and_conservation_validations_must_pass": True,
            },
            "pass": metric_pass,
        }
    return {
        "level_order_umol_m2_s": requested,
        "achieved_stage_a_means_umol_m2_s": achieved,
        "metric_order": list(SIDE_METRICS),
        "metrics": analyses,
        "pass": all(value["pass"] for value in analyses.values()),
    }


def _verification_analysis(
    primary: Sequence[Mapping[str, object]],
    verification: Mapping[str, object] | None,
    *,
    criteria: CalibrationCriteria,
) -> dict[str, object]:
    if verification is None:
        return {
            "executed": False,
            "required_for_default_sweep": False,
            "pass": True,
        }
    level = _positive(
        "verification reference level",
        verification.get("requested_reference_level_umol_m2_s"),
    )
    baseline = next(
        (
            value
            for value in primary
            if value.get("requested_reference_level_umol_m2_s") == level
        ),
        None,
    )
    if baseline is None:
        raise SurfaceFluxCalibrationError(
            "verification result has no matching primary level."
        )
    comparisons: dict[str, object] = {}
    for metric in SIDE_METRICS:
        primary_beta = _positive_or_zero(
            "primary beta", _nested(baseline, "statistics", metric, "beta_level")
        )
        verification_beta = _positive_or_zero(
            "verification beta",
            _nested(verification, "statistics", metric, "beta_level"),
        )
        difference = (
            0.0
            if primary_beta == verification_beta == 0.0
            else abs(verification_beta - primary_beta) / primary_beta
            if primary_beta > 0.0
            else None
        )
        comparisons[metric] = {
            "primary_beta": primary_beta,
            "verification_beta": verification_beta,
            "relative_difference": difference,
            "maximum_allowed": criteria.maximum_verification_beta_difference,
            "pass": difference is not None
            and difference <= criteria.maximum_verification_beta_difference,
        }
    return {
        "executed": True,
        "level_umol_m2_s": level,
        "primary_quality": baseline.get("quality"),
        "verification_quality": verification.get("quality"),
        "metrics": comparisons,
        "pass": all(value["pass"] for value in comparisons.values()),
    }


def _build_report(
    *,
    config: SurfaceFluxCalibrationConfig,
    identity: Mapping[str, object],
    created_at_utc: str,
    scene: JuvenileScientificScene,
    material_plan: RexRadianceTransMaterialPlan,
    results: Sequence[Mapping[str, object]],
    sweep_analysis: Mapping[str, object],
    convergence: Mapping[str, object],
    output: Path,
) -> dict[str, object]:
    primary = [value for value in results if value["kind"] == "primary"]
    all_level_validations = all(
        _nested(
            value,
            "validation",
            "all_numerical_and_conservation_validations_pass",
        )
        is True
        and _nested(value, "reference", "acceptance", "pass") is True
        for value in results
    )
    overall_pass = bool(
        sweep_analysis["pass"]
        and convergence["pass"]
        and all_level_validations
    )
    material_payload = material_plan.to_payload()
    phase16_payload = material_payload["phase16_atr"]
    if not isinstance(phase16_payload, Mapping):
        raise SurfaceFluxCalibrationError("material provenance payload is invalid.")
    material_authorities = [
        {
            "band_id": band_id,
            "coefficient_payload": material_plan.material(band_id).to_dict(),
            "coefficient_provenance_sha256": _hash_json(
                material_plan.material(band_id).to_dict()
            ),
            "rendered_material_sha256": _sha256_text(
                render_radiance_trans_material(
                    DEFAULT_LEAF_MATERIAL_MODIFIER,
                    material_plan.material(band_id).parameters,
                )
            ),
        }
        for band_id in PAR_BAND_ORDER
    ]
    inventory = _inventory(output, excluded={REPORT_NAME})
    return {
        "schema_id": CALIBRATION_SCHEMA_ID,
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "created_at_utc": created_at_utc,
        "experiment_status": "complete_pass" if overall_pass else "complete_fail",
        "repository_revision": identity.get("repository_revision"),
        "configuration_sha256": identity["configuration_sha256"],
        "exact_cli_configuration": config.cli_payload(),
        "scientific_configuration": config.scientific_payload(),
        "radiance_installation": identity["radiance_installation"],
        "quality_configuration": {
            "primary_name": config.quality,
            "primary_options": radiance_options(config.quality),
            "verification_name": config.verification_quality,
            "verification_options": (
                None
                if config.verification_quality is None
                else radiance_options(config.verification_quality)
            ),
        },
        "neutral_source": identity["neutral_source"],
        "calibration_scene": identity["calibration_scene"],
        "juvenile_geometry_and_receivers": {
            "scene_id": scene.scene_id,
            "scene_hash": scene.scene_hash,
            "layout_plan_hash": scene.layout_plan_hash,
            "topology_sha256": scene.topology.topology_sha256,
            "receivers_sha256": scene.topology.receivers_sha256,
            "counts": scene.counts.to_payload(),
            "canonical_order": scene.ordering,
        },
        "material_authority": {
            "complete_plan_sha256": _hash_json(material_payload),
            "profile_and_source_provenance": {
                key: value
                for key, value in phase16_payload.items()
                if key not in {"scalar_par", "bands"}
            },
            "par_bands": material_authorities,
            "far_red_material_published": False,
        },
        "histogram_specification": config.histogram.to_dict(),
        "ordered_level_results": list(results),
        "primary_level_count": len(primary),
        "sweep_analysis": dict(sweep_analysis),
        "higher_quality_convergence": dict(convergence),
        "equations": {
            "display_model_under_evaluation": "z[i,s,m] = q[i,s,m] / (beta[s,m] * R)",
            "beta_level": (
                "sum(area_i * q_i) / sum(area_i) / achieved_stage_a_mean"
            ),
            "through_origin_beta": "sum(R_l * qbar_l) / sum(R_l^2)",
            "area_weighted_population_variance": (
                "sum(area_i * (q_i - qbar)^2) / sum(area_i)"
            ),
            "normalized_distribution": "x_i = q_i / achieved_stage_a_mean",
            "normalized_distribution_drift": (
                "0.5 * sum(abs(area_fraction_a - area_fraction_b)) "
                "including underflow and overflow"
            ),
            "additive_correction_used": False,
        },
        "acceptance": {
            "criteria": config.criteria.to_dict(),
            "all_level_validations_pass": all_level_validations,
            "sweep_pass": sweep_analysis["pass"],
            "convergence_pass": convergence["pass"],
            "pass": overall_pass,
            "failed_values_replaced_with_defaults": False,
        },
        "artifact_inventory": inventory,
        "artifact_inventory_policy": {
            "paths_relative_to_output_directory": True,
            "all_completed_level_artifacts_hash_checked_on_resume": True,
            "report_excludes_its_own_hash": True,
        },
        "scientific_limitations": [
            "This artifact is display-calibration evidence, not a transport correction.",
            "Raw physical transport values and Phase 27G-C aggregation values are not altered.",
            "The open-boundary field deliberately excludes room-surface reflection and occlusion.",
            "Leaf optical coefficients retain the existing authoritative Rex provenance.",
            "Acceptance establishes behavior only for the declared juvenile scene, quality, and levels.",
            "A passing experiment does not itself choose production color anchors.",
        ],
        "prohibited_interpretations": {
            "coefficients_estimated_from_an_evaluated_run": False,
            "raw_transport_rescaled_after_trace": False,
            "surface_values_clipped_or_replaced": False,
            "production_surface_coloring_implemented": False,
        },
        "compactness": {
            "full_receiver_arrays_present": False,
            "raw_receiver_values_retained_only_as_hash-inventoried_binary_artifacts": True,
            "histogram_memory_bounded_by_fixed_bin_count": True,
        },
    }


def _prepare_output_root(
    config: SurfaceFluxCalibrationConfig,
    *,
    identity: Mapping[str, object],
    created_at_utc: str,
) -> dict[str, object]:
    output = config.output_directory
    if output.exists() and (not output.is_dir() or output.is_symlink()):
        raise SurfaceFluxCalibrationError(
            "calibration output must be a real directory or an absent path."
        )
    if not output.exists():
        output.mkdir(parents=True)
    entries = tuple(output.iterdir())
    state_path = output / CONFIGURATION_NAME
    if not entries:
        state = {
            "schema_id": CONFIGURATION_SCHEMA_ID,
            "schema_version": CONFIGURATION_SCHEMA_VERSION,
            "created_at_utc": _validated_timestamp(created_at_utc),
            "initial_cli_configuration": config.cli_payload(),
            "identity": dict(identity),
        }
        atomic_write_text(state_path, _pretty_json(state))
        return state
    if not config.resume:
        raise SurfaceFluxCalibrationError(
            "calibration output is non-empty; pass --resume only for an exact configuration."
        )
    allowed = {CONFIGURATION_NAME, "levels", REPORT_NAME}
    unexpected = sorted(path.name for path in entries if path.name not in allowed)
    if unexpected:
        raise SurfaceFluxCalibrationError(
            f"calibration output contains unexpected entries: {unexpected!r}."
        )
    state = _read_json_object(state_path)
    if (
        state.get("schema_id") != CONFIGURATION_SCHEMA_ID
        or state.get("schema_version") != CONFIGURATION_SCHEMA_VERSION
        or state.get("identity") != dict(identity)
    ):
        raise SurfaceFluxCalibrationError(
            "stored calibration configuration does not match exactly; refusing to mix results."
        )
    _validated_timestamp(state.get("created_at_utc"))
    return state


def _validate_completed_level(
    root: Path,
    *,
    job: _Job,
    configuration_sha256: str,
) -> dict[str, object]:
    if not root.is_dir() or root.is_symlink():
        raise SurfaceFluxCalibrationError(
            f"completed level path is missing or unsafe: {root}"
        )
    completion = _read_json_object(root / COMPLETION_NAME)
    if (
        completion.get("schema_id") != COMPLETION_SCHEMA_ID
        or completion.get("schema_version") != COMPLETION_SCHEMA_VERSION
        or completion.get("configuration_sha256") != configuration_sha256
        or completion.get("job") != job.to_dict()
    ):
        raise SurfaceFluxCalibrationError(
            f"completed level identity is invalid: {job.job_id}"
        )
    inventory = completion.get("ordered_artifact_inventory")
    if not isinstance(inventory, list):
        raise SurfaceFluxCalibrationError("completed level inventory is missing.")
    inventory_paths = [
        record.get("path") if isinstance(record, Mapping) else None
        for record in inventory
    ]
    if any(not isinstance(value, str) for value in inventory_paths) or (
        inventory_paths != sorted(inventory_paths)
    ):
        raise SurfaceFluxCalibrationError(
            "completed level inventory order is invalid."
        )
    observed_paths: set[str] = set()
    for record in inventory:
        if not isinstance(record, Mapping):
            raise SurfaceFluxCalibrationError("completed artifact record is invalid.")
        relative = record.get("path")
        if not isinstance(relative, str) or relative in observed_paths:
            raise SurfaceFluxCalibrationError("completed artifact path is invalid.")
        observed_paths.add(relative)
        path = root / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
            or path.stat().st_size != record.get("byte_length")
            or _sha256_file(path) != record.get("sha256")
        ):
            raise SurfaceFluxCalibrationError(
                f"completed artifact failed exact hash validation: {relative}"
            )
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != COMPLETION_NAME
    }
    if actual_paths != observed_paths:
        raise SurfaceFluxCalibrationError(
            "completed level contains missing, extra, or reordered inventory artifacts."
        )
    result_authority = completion.get("level_result")
    result_path = root / LEVEL_RESULT_NAME
    if (
        not isinstance(result_authority, Mapping)
        or result_authority.get("path") != LEVEL_RESULT_NAME
        or result_authority.get("sha256") != _sha256_file(result_path)
    ):
        raise SurfaceFluxCalibrationError("completed level result authority is invalid.")
    result = _read_json_object(result_path)
    if (
        result.get("configuration_sha256") != configuration_sha256
        or result.get("job_id") != job.job_id
        or result.get("kind") != job.kind
        or result.get("quality") != job.quality
        or result.get("requested_reference_level_umol_m2_s")
        != job.requested_level
    ):
        raise SurfaceFluxCalibrationError("completed level result is inconsistent.")
    return result


def _jobs(config: SurfaceFluxCalibrationConfig) -> tuple[_Job, ...]:
    primary = tuple(
        _Job(
            job_id=f"primary-{_level_token(level)}-{config.quality}",
            kind="primary",
            requested_level=level,
            quality=config.quality,
        )
        for level in config.reference_levels_umol_m2_s
    )
    if config.verification_level_umol_m2_s is None:
        return primary
    return primary + (
        _Job(
            job_id=(
                f"verification-{_level_token(config.verification_level_umol_m2_s)}-"
                f"{config.verification_quality}"
            ),
            kind="verification",
            requested_level=config.verification_level_umol_m2_s,
            quality=str(config.verification_quality),
        ),
    )


def _pairwise_distribution_drifts(
    levels: Sequence[Mapping[str, object]],
    metric: str,
) -> list[float]:
    vectors: list[tuple[float, ...]] = []
    for level in levels:
        distribution = _nested(level, "statistics", metric, "distribution")
        if not isinstance(distribution, Mapping):
            raise SurfaceFluxCalibrationError("surface distribution is invalid.")
        bins = distribution.get("bin_area_fractions")
        underflow = distribution.get("underflow")
        overflow = distribution.get("overflow")
        if (
            not isinstance(bins, list)
            or not isinstance(underflow, Mapping)
            or not isinstance(overflow, Mapping)
        ):
            raise SurfaceFluxCalibrationError("surface distribution is incomplete.")
        vector = (
            _finite_nonnegative("underflow fraction", underflow.get("area_fraction")),
            *(
                _finite_nonnegative("bin fraction", value)
                for value in bins
            ),
            _finite_nonnegative("overflow fraction", overflow.get("area_fraction")),
        )
        if not math.isclose(math.fsum(vector), 1.0, rel_tol=1e-12, abs_tol=1e-12):
            raise SurfaceFluxCalibrationError(
                "normalized distribution does not conserve area fraction."
            )
        vectors.append(vector)
    return [
        0.5 * math.fsum(abs(left - right) for left, right in zip(a, b, strict=True))
        for index, a in enumerate(vectors)
        for b in vectors[index + 1 :]
    ]


def _through_origin_slope(x: Sequence[float], y: Sequence[float]) -> float:
    denominator = math.fsum(value * value for value in x)
    if denominator <= 0.0:
        raise SurfaceFluxCalibrationError("through-origin regression is singular.")
    return math.fsum(left * right for left, right in zip(x, y, strict=True)) / denominator


def _linear_fit(x: Sequence[float], y: Sequence[float]) -> tuple[float, float]:
    count = len(x)
    mean_x = math.fsum(x) / count
    mean_y = math.fsum(y) / count
    denominator = math.fsum((value - mean_x) ** 2 for value in x)
    if denominator <= 0.0:
        raise SurfaceFluxCalibrationError("unconstrained regression is singular.")
    slope = math.fsum(
        (left - mean_x) * (right - mean_y)
        for left, right in zip(x, y, strict=True)
    ) / denominator
    return slope, mean_y - slope * mean_x


def _r_squared(observed: Sequence[float], predicted: Sequence[float]) -> float:
    mean = math.fsum(observed) / len(observed)
    residual = math.fsum(
        (actual - estimate) ** 2
        for actual, estimate in zip(observed, predicted, strict=True)
    )
    total = math.fsum((value - mean) ** 2 for value in observed)
    if total == 0.0:
        return 1.0 if residual == 0.0 else 0.0
    return 1.0 - residual / total


def format_calibration_report(report: Mapping[str, object]) -> str:
    """Serialize a compact report deterministically with strict JSON numbers."""

    return _pretty_json(report)


def _run_required(
    runner: CommandRunner,
    command: CommandSpec,
    stderr_path: Path,
) -> None:
    try:
        result = runner.run(command, stderr_path=stderr_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SurfaceFluxCalibrationError(
            f"{command.label or command.argv[0]} could not execute: {exc}"
        ) from exc
    if not result.success:
        detail = (result.stderr_text or "").strip() or (
            f"return code {result.returncode}"
        )
        raise SurfaceFluxCalibrationError(
            f"{command.label or command.argv[0]} failed: {detail}"
        )


def _relabel(command: CommandSpec, label: str) -> CommandSpec:
    return CommandSpec(
        argv=command.argv,
        stdin_path=command.stdin_path,
        stdout_path=command.stdout_path,
        cwd=command.cwd,
        env=command.env,
        label=label,
        stdout_mode=command.stdout_mode,
    )


def _command_payload(command: CommandSpec, root: Path) -> dict[str, object]:
    return {
        "label": command.label,
        "argv": [_portable_token(value, root) for value in command.argv],
        "stdin_path": (
            None
            if command.stdin_path is None
            else _relative(root, command.stdin_path)
        ),
        "stdout_path": (
            None
            if command.stdout_path is None
            else _relative(root, command.stdout_path)
        ),
        "cwd": "." if command.cwd == root else str(command.cwd),
        "environment": dict(command.env),
        "shell": False,
    }


def _portable_token(value: str, root: Path) -> str:
    candidate = Path(value)
    if candidate.is_absolute() and candidate.resolve().is_relative_to(root.resolve()):
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    return value


def _artifact(root: Path, path: Path, *, role: str) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise SurfaceFluxCalibrationError(f"artifact is missing or unsafe: {path}")
    return {
        "role": role,
        "path": _relative(root, path),
        "media_type": _media_type(path),
        "byte_length": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _inventory(root: Path, *, excluded: set[str]) -> list[dict[str, object]]:
    resolved = root.resolve(strict=True)
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if path.name in excluded:
            continue
        if path.is_symlink():
            raise SurfaceFluxCalibrationError(
                f"artifact inventory rejects symbolic links: {path}"
            )
        if not path.is_file():
            continue
        resolved_path = path.resolve(strict=True)
        if not resolved_path.is_relative_to(resolved):
            raise SurfaceFluxCalibrationError("artifact escaped the output root.")
        records.append(
            {
                "path": resolved_path.relative_to(resolved).as_posix(),
                "media_type": _media_type(path),
                "byte_length": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return records


def _validate_export_artifacts(
    artifacts: tuple[JuvenileRadianceArtifactMetadata, ...],
    root: Path,
) -> None:
    if len(artifacts) != 4 or len({value.role for value in artifacts}) != 4:
        raise SurfaceFluxCalibrationError(
            "juvenile export artifact inventory is incomplete."
        )
    for artifact in artifacts:
        path = root / artifact.logical_name
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != artifact.byte_length
            or _sha256_file(path) != artifact.sha256
        ):
            raise SurfaceFluxCalibrationError(
                f"juvenile export artifact failed integrity: {artifact.role}"
            )


def _require_nonempty(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
        raise SurfaceFluxCalibrationError(f"{label} is missing or empty.")


def _validate_output_location(output: Path, repository: Path) -> None:
    resources = repository / "src" / "fspm_optics" / "resources"
    if output == resources or output.is_relative_to(resources):
        raise SurfaceFluxCalibrationError(
            "calibration runtime output cannot be placed in packaged resources."
        )
    for candidate in (output, *output.parents):
        if (candidate / "metrics.json").exists() and (
            candidate / "manifest.json"
        ).exists():
            raise SurfaceFluxCalibrationError(
                "calibration runtime output cannot be mixed into a published run."
            )


def _clean_interrupted_level_stages(levels_root: Path) -> None:
    for path in tuple(levels_root.iterdir()):
        if path.name.startswith(".") and path.name.endswith(".tmp"):
            _remove_stage(path, levels_root)


def _remove_stage(stage: Path, levels_root: Path) -> None:
    if not stage.exists():
        return
    if (
        stage.parent.resolve() != levels_root.resolve()
        or not stage.name.startswith(".")
        or not stage.name.endswith(".tmp")
        or stage.is_symlink()
    ):
        raise SurfaceFluxCalibrationError(
            "refusing to remove an unsafe interrupted stage path."
        )
    shutil.rmtree(stage)


def _read_json_object(path: Path) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise SurfaceFluxCalibrationError(f"required JSON artifact is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SurfaceFluxCalibrationError(
            f"JSON artifact is unreadable or malformed: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise SurfaceFluxCalibrationError(f"JSON artifact must be an object: {path}")
    return payload


def _repository_revision(repository: Path) -> str | None:
    git_path = repository / ".git"
    if git_path.is_file():
        try:
            marker = git_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return None
        if not marker.startswith("gitdir: "):
            return None
        git_path = (repository / marker.removeprefix("gitdir: ")).resolve()
    head_path = git_path / "HEAD"
    try:
        head = head_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    if _valid_git_revision(head):
        return head
    if not head.startswith("ref: "):
        return None
    reference = head.removeprefix("ref: ")
    loose = git_path / reference
    try:
        value = loose.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        value = ""
    if _valid_git_revision(value):
        return value
    try:
        packed_lines = (git_path / "packed-refs").read_text(
            encoding="ascii"
        ).splitlines()
    except (OSError, UnicodeError):
        return None
    for line in packed_lines:
        if line.startswith(("#", "^")):
            continue
        parts = line.split(" ", 1)
        if (
            len(parts) == 2
            and parts[1] == reference
            and _valid_git_revision(parts[0])
        ):
            return parts[0]
    return None


def _discover_repository_root() -> Path:
    candidate = Path(__file__).resolve()
    for parent in candidate.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src").is_dir():
            return parent
    return Path.cwd().resolve()


def _nested(payload: Mapping[str, object], *keys: str) -> object:
    value: object = payload
    for key in keys:
        if not isinstance(value, Mapping) or key not in value:
            raise SurfaceFluxCalibrationError(
                f"calibration result is missing {'.'.join(keys)}."
            )
        value = value[key]
    return value


def _level_token(value: float) -> str:
    return format(value, ".12g").replace(".", "p")


def _relative(root: Path, path: Path) -> str:
    resolved_root = root.resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    if not resolved_path.is_relative_to(resolved_root):
        raise SurfaceFluxCalibrationError("artifact path escaped its experiment root.")
    return resolved_path.relative_to(resolved_root).as_posix()


def _media_type(path: Path) -> str:
    if path.suffix == ".json":
        return "application/json"
    if path.suffix in {".rad", ".pts", ".rgb", ".log"}:
        return "text/plain"
    return "application/octet-stream"


def _pretty_json(payload: Mapping[str, object]) -> str:
    return json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
    ) + "\n"


def _hash_json(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _valid_git_revision(value: object) -> bool:
    return isinstance(value, str) and len(value) in (40, 64) and all(
        character in "0123456789abcdef" for character in value
    )


def _validated_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise SurfaceFluxCalibrationError("creation timestamp is invalid.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SurfaceFluxCalibrationError("creation timestamp is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise SurfaceFluxCalibrationError("creation timestamp must use UTC.")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number.")
    return number


def _positive(name: str, value: object) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be positive.")
    return number


def _positive_or_zero(name: str, value: object) -> float:
    number = _finite(name, value)
    if number < 0.0:
        raise SurfaceFluxCalibrationError(f"{name} must be non-negative.")
    return number


def _finite_nonnegative(name: str, value: object) -> float:
    number = _finite(name, value)
    if number < 0.0:
        raise SurfaceFluxCalibrationError(f"{name} must be non-negative.")
    return number


__all__ = [
    "CALIBRATION_SCHEMA_ID",
    "CALIBRATION_SCHEMA_VERSION",
    "CalibrationCriteria",
    "CalibrationPublication",
    "DEFAULT_OUTPUT_DIRECTORY",
    "DEFAULT_REFERENCE_LEVELS",
    "HistogramSpecification",
    "PAR_BAND_ORDER",
    "REPORT_NAME",
    "SIDE_METRICS",
    "SOURCE_MODEL_ID",
    "SurfaceFluxCalibrationConfig",
    "SurfaceFluxCalibrationError",
    "analyze_calibration_sweep",
    "calibration_scene_identity",
    "canonical_calibration_boundary_text",
    "canonical_neutral_source_definition",
    "configuration_identity",
    "format_calibration_report",
    "render_neutral_source",
    "run_surface_flux_calibration",
]
