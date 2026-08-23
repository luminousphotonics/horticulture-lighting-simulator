"""Isolated fixture-body geometry and optical-material diagnostics.

This module deliberately sits outside production transport.  It reuses the
production planners and authenticated GLB classifications, but it never
changes the production fixture material or classification manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Literal, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

from fspm_optics.fixtures.occlusion import (
    FIXTURE_BODY_MATERIAL_NAME,
    FIXTURE_BODY_MATERIAL_RAD,
    FixtureOcclusionPlan,
)
from fspm_optics.fixtures.occlusion.gltf import Point3, Triangle
from fspm_optics.fixtures.occlusion.manifest import (
    AuthenticatedFixtureAsset,
    load_authenticated_fixture_asset,
)
from fspm_optics.radiance.commands import (
    CommandSpec,
    build_baseline_rtrace_command,
    build_oconv_command,
)
from fspm_optics.radiance.runner import LocalRunner
from fspm_optics.transport.basis.parsing import parse_basis_column

FloatArray = NDArray[np.float64]
GeometryVariant = Literal[
    "no_bodies",
    "all_bodies",
    "centerpiece_only",
    "linear2_only",
]

AUDIT_SCHEMA_ID = "fspm-optics.fixture-body-optics-audit"
AUDIT_SCHEMA_VERSION = 1
MATERIAL_MODIFIER = FIXTURE_BODY_MATERIAL_NAME


class FixtureBodyOpticsAuditError(RuntimeError):
    """A diagnostic fixture-body audit failed a scientific or integrity gate."""


@dataclass(frozen=True, slots=True)
class DiagnosticMaterial:
    """One controlled Radiance material used only by this audit."""

    material_id: str
    primitive: Literal["plastic", "metal"]
    reflectance_rgb: tuple[float, float, float]
    specular_fraction: float
    roughness: float
    description: str

    def __post_init__(self) -> None:
        if (
            not self.material_id
            or self.primitive not in {"plastic", "metal"}
            or not self.description
        ):
            raise ValueError("diagnostic material identity is incomplete.")
        values = (*self.reflectance_rgb, self.specular_fraction, self.roughness)
        if any(not math.isfinite(float(value)) for value in values):
            raise ValueError("diagnostic material values must be finite.")
        if any(not 0.0 <= value <= 1.0 for value in self.reflectance_rgb):
            raise ValueError("diagnostic RGB reflectance must lie in [0, 1].")
        if not 0.0 <= self.specular_fraction <= 1.0:
            raise ValueError("diagnostic specular fraction must lie in [0, 1].")
        if not 0.0 <= self.roughness <= 1.0:
            raise ValueError("diagnostic roughness must lie in [0, 1].")
        if self.primitive == "metal" and self.specular_fraction < 0.5:
            raise ValueError("diagnostic metal must remain predominantly specular.")

    @property
    def radiance_text(self) -> str:
        r, g, b = self.reflectance_rgb
        return (
            f"# Diagnostic-only material: {self.material_id}\n"
            f"# {self.description}\n"
            f"void {self.primitive} {MATERIAL_MODIFIER}\n"
            "0\n"
            "0\n"
            f"5 {r:.12g} {g:.12g} {b:.12g} "
            f"{self.specular_fraction:.12g} {self.roughness:.12g}\n"
        )

    @property
    def declared_total_reflectance(self) -> float:
        return math.fsum(self.reflectance_rgb) / 3.0

    def to_payload(self) -> dict[str, object]:
        return {
            "material_id": self.material_id,
            "radiance_primitive": self.primitive,
            "reflectance_rgb": list(self.reflectance_rgb),
            "declared_total_reflectance": self.declared_total_reflectance,
            "specular_fraction": self.specular_fraction,
            "roughness": self.roughness,
            "description": self.description,
            "scientific_authority": "controlled_diagnostic_sweep_not_product_data",
            "radiance_text_sha256": sha256_text(self.radiance_text),
        }


ZERO_ABSORBER = DiagnosticMaterial(
    material_id="zero_absorber",
    primitive="plastic",
    reflectance_rgb=(0.0, 0.0, 0.0),
    specular_fraction=0.0,
    roughness=0.0,
    description="Historical zero-reflectance audit baseline.",
)

DIAGNOSTIC_MATERIALS: tuple[DiagnosticMaterial, ...] = (
    ZERO_ABSORBER,
    DiagnosticMaterial(
        "neutral_diffuse_20",
        "plastic",
        (0.2, 0.2, 0.2),
        0.0,
        0.0,
        "Neutral diffuse coated-surface sensitivity at 20% reflectance.",
    ),
    DiagnosticMaterial(
        "neutral_diffuse_50",
        "plastic",
        (0.5, 0.5, 0.5),
        0.0,
        0.0,
        "Neutral diffuse coated-surface sensitivity at 50% reflectance.",
    ),
    DiagnosticMaterial(
        "neutral_diffuse_80",
        "plastic",
        (0.8, 0.8, 0.8),
        0.0,
        0.0,
        "Neutral diffuse coated-surface sensitivity at 80% reflectance.",
    ),
    DiagnosticMaterial(
        "aluminum_like_70",
        "metal",
        (0.7, 0.7, 0.7),
        0.9,
        0.10,
        "Neutral aluminum-like sensitivity at 70% total reflectance.",
    ),
    DiagnosticMaterial(
        "aluminum_like_90",
        "metal",
        (0.9, 0.9, 0.9),
        0.95,
        0.05,
        "Neutral aluminum-like sensitivity at 90% total reflectance.",
    ),
)


@dataclass(frozen=True, slots=True)
class TraceSampling:
    sampling_id: str
    ambient_divisions: int
    ambient_super_samples: int
    ambient_accuracy: float
    ambient_resolution: int

    def __post_init__(self) -> None:
        if not self.sampling_id:
            raise ValueError("sampling_id must be non-empty.")
        for name in (
            "ambient_divisions",
            "ambient_super_samples",
            "ambient_resolution",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        if not 0.0 < self.ambient_accuracy <= 1.0:
            raise ValueError("ambient_accuracy must lie in (0, 1].")


COARSE_SAMPLING = TraceSampling("coarse", 64, 16, 0.25, 24)
FINE_SAMPLING = TraceSampling("fine", 256, 64, 0.15, 64)


@dataclass(frozen=True, slots=True)
class DiagnosticBodySources:
    system_id: str
    material: DiagnosticMaterial
    root: Path
    shape_sources: Mapping[str, Path]
    shape_octrees: Mapping[str, Path]
    compile_commands: tuple[CommandSpec, ...]


@dataclass(frozen=True, slots=True)
class FieldStatistics:
    sensor_count: int
    integrated_receiver_plane_ppfd_umol_s: float
    mean: float
    standard_deviation: float
    cv_percent: float
    minimum: float
    maximum: float
    exact_center: float
    mean_within_0_5m: float
    mean_annulus_0_75_to_1_25m: float
    annulus_minus_center: float
    annulus_minus_center_percent: float
    maximum_location_m: tuple[float, float, float]
    minimum_location_m: tuple[float, float, float]
    minimum_to_mean: float
    minimum_to_maximum: float
    symmetry_180: Mapping[str, float]
    symmetry_90: Mapping[str, float] | None

    def to_payload(self) -> dict[str, object]:
        return {
            "sensor_count": self.sensor_count,
            "integrated_receiver_plane_ppfd_umol_s": (
                self.integrated_receiver_plane_ppfd_umol_s
            ),
            "mean": self.mean,
            "standard_deviation": self.standard_deviation,
            "cv_percent": self.cv_percent,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "exact_center": self.exact_center,
            "mean_within_0_5m": self.mean_within_0_5m,
            "mean_annulus_0_75_to_1_25m": (
                self.mean_annulus_0_75_to_1_25m
            ),
            "annulus_minus_center": self.annulus_minus_center,
            "annulus_minus_center_percent": (
                self.annulus_minus_center_percent
            ),
            "maximum_location_m": list(self.maximum_location_m),
            "minimum_location_m": list(self.minimum_location_m),
            "minimum_to_mean": self.minimum_to_mean,
            "minimum_to_maximum": self.minimum_to_maximum,
            "symmetry_180": dict(self.symmetry_180),
            "symmetry_90": (
                None if self.symmetry_90 is None else dict(self.symmetry_90)
            ),
        }


def diagnostic_material_by_id(material_id: str) -> DiagnosticMaterial:
    matches = tuple(
        material for material in DIAGNOSTIC_MATERIALS
        if material.material_id == material_id
    )
    if len(matches) != 1:
        raise ValueError(f"unknown diagnostic material: {material_id!r}")
    return matches[0]


def replace_shape_material(
    production_shape_text: str,
    material: DiagnosticMaterial,
) -> str:
    """Replace only the authenticated common body-material definition."""

    if production_shape_text.count(FIXTURE_BODY_MATERIAL_RAD) != 1:
        raise FixtureBodyOpticsAuditError(
            "fixture shape does not contain exactly one authenticated material block."
        )
    replaced = production_shape_text.replace(
        FIXTURE_BODY_MATERIAL_RAD,
        material.radiance_text,
        1,
    )
    production_geometry = production_shape_text.replace(
        FIXTURE_BODY_MATERIAL_RAD, "", 1
    )
    diagnostic_geometry = replaced.replace(material.radiance_text, "", 1)
    if production_geometry != diagnostic_geometry:
        raise FixtureBodyOpticsAuditError(
            "diagnostic material replacement changed fixture geometry."
        )
    return replaced


def plan_diagnostic_body_sources(
    plan: FixtureOcclusionPlan,
    *,
    material: DiagnosticMaterial,
    output_directory: str | Path,
    oconv_bin: str | Path,
) -> DiagnosticBodySources:
    root = Path(output_directory).expanduser().resolve()
    shape_sources: dict[str, Path] = {}
    shape_octrees: dict[str, Path] = {}
    commands: list[CommandSpec] = []
    for shape in plan.shapes:
        source = root / "shapes" / f"{shape.shape_id}.rad"
        octree = root / "shapes" / f"{shape.shape_id}.oct"
        shape_sources[shape.shape_id] = source
        shape_octrees[shape.shape_id] = octree
        commands.append(
            build_oconv_command(
                (source,),
                output_octree=octree,
                cwd=root,
                oconv_bin=oconv_bin,
                label=(
                    f"compile_{plan.system_id}_{material.material_id}_"
                    f"{shape.shape_id}"
                ),
            )
        )
    return DiagnosticBodySources(
        system_id=plan.system_id,
        material=material,
        root=root,
        shape_sources=shape_sources,
        shape_octrees=shape_octrees,
        compile_commands=tuple(commands),
    )


def materialize_and_compile_diagnostic_body_sources(
    production_plan: FixtureOcclusionPlan,
    diagnostic: DiagnosticBodySources,
    runner: LocalRunner,
) -> tuple[dict[str, object], ...]:
    diagnostic.root.mkdir(parents=True, exist_ok=False)
    (diagnostic.root / "shapes").mkdir()
    records: list[dict[str, object]] = []
    shape_by_id = {shape.shape_id: shape for shape in production_plan.shapes}
    for shape, command in zip(
        production_plan.shapes,
        diagnostic.compile_commands,
        strict=True,
    ):
        source = diagnostic.shape_sources[shape.shape_id]
        text = replace_shape_material(shape.source_text, diagnostic.material)
        source.write_text(text, encoding="utf-8")
        result = runner.run(command)
        octree = diagnostic.shape_octrees[shape.shape_id]
        if (
            not result.success
            or not octree.is_file()
            or octree.stat().st_size <= 0
        ):
            raise FixtureBodyOpticsAuditError(
                result.failure_message
                or f"diagnostic shape compilation failed: {shape.shape_id}"
            )
        records.append(
            {
                "shape_id": shape.shape_id,
                "asset_id": shape.asset_id,
                "source_path": str(source),
                "source_sha256": sha256_file(source),
                "octree_path": str(octree),
                "octree_sha256": sha256_file(octree),
                "command": list(command.argv),
                "wall_time_s": result.wall_time_s,
            }
        )
    if set(shape_by_id) != set(diagnostic.shape_sources):
        raise FixtureBodyOpticsAuditError(
            "diagnostic body source identity separation failed."
        )
    return tuple(records)


def geometry_variant_instance_text(
    production_plan: FixtureOcclusionPlan,
    diagnostic: DiagnosticBodySources,
    variant: GeometryVariant,
) -> str:
    """Create a body-instance file selecting only the requested fixtures."""

    if variant not in {
        "no_bodies",
        "all_bodies",
        "centerpiece_only",
        "linear2_only",
    }:
        raise ValueError(f"unsupported geometry variant: {variant!r}")
    if production_plan.system_id == "conventional" and variant not in {
        "no_bodies",
        "all_bodies",
    }:
        raise ValueError("Conventional supports only no-body and all-body variants.")

    def include(asset_id: str) -> bool:
        if variant == "no_bodies":
            return False
        if variant == "all_bodies":
            return True
        if variant == "centerpiece_only":
            return asset_id == "proposed-centerpiece-v1"
        return asset_id == "proposed-linear2-v1"

    selected = tuple(
        instance for instance in production_plan.instances
        if include(instance.asset_id)
    )
    if variant != "no_bodies" and not selected:
        raise FixtureBodyOpticsAuditError(
            f"geometry variant selected no fixture instances: {variant}"
        )
    lines = [
        f"# {AUDIT_SCHEMA_ID}-v{AUDIT_SCHEMA_VERSION}",
        f"# system={production_plan.system_id}",
        f"# geometry_variant={variant}",
        f"# diagnostic_material={diagnostic.material.material_id}",
        "",
    ]
    for instance in selected:
        octree = diagnostic.shape_octrees.get(instance.shape_id)
        if octree is None:
            raise FixtureBodyOpticsAuditError(
                f"diagnostic shape identity is missing: {instance.shape_id}"
            )
        tx, ty, tz = instance.translation_m
        lines.extend(
            [
                f"void instance {instance.instance_id}",
                f"5 {octree} -t {tx:.15g} {ty:.15g} {tz:.15g}",
                "0",
                "0",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def deterministic_trace_options(
    *,
    ambient_bounces: int,
    sampling: TraceSampling,
    ambient_cache: str | Path | None,
) -> tuple[str, ...]:
    if (
        isinstance(ambient_bounces, bool)
        or not isinstance(ambient_bounces, int)
        or ambient_bounces not in {0, 1, 2, 5}
    ):
        raise ValueError("ambient_bounces must be one of 0, 1, 2, or 5.")
    common = (
        "-ab", str(ambient_bounces),
        "-u-",
        "-dj", "0",
        "-ds", "0",
        "-dt", "0",
        "-dc", "1",
    )
    if ambient_bounces == 0:
        return common
    options = (
        *common,
        "-ad", str(sampling.ambient_divisions),
        "-as", str(sampling.ambient_super_samples),
        "-aa", f"{sampling.ambient_accuracy:.12g}",
        "-ar", str(sampling.ambient_resolution),
        "-dr", "3",
        "-lr", "12",
        "-lw", "5e-5",
    )
    if ambient_cache is None:
        return options
    return (*options, "-af", str(ambient_cache))


def build_diagnostic_trace_commands(
    *,
    scene_sources: Sequence[str | Path],
    receiver_input: str | Path,
    case_directory: str | Path,
    scene_cwd: str | Path,
    ambient_bounces: int,
    sampling: TraceSampling,
    oconv_bin: str | Path,
    rtrace_bin: str | Path,
) -> tuple[CommandSpec, CommandSpec, Path | None]:
    case_root = Path(case_directory).expanduser().resolve()
    octree = case_root / "scene.oct"
    rgb = case_root / "field.rgb"
    ambient = (
        None if ambient_bounces == 0 else case_root / "scene.amb"
    )
    options = deterministic_trace_options(
        ambient_bounces=ambient_bounces,
        sampling=sampling,
        ambient_cache=ambient,
    )
    oconv = build_oconv_command(
        scene_sources,
        output_octree=octree,
        cwd=scene_cwd,
        oconv_bin=oconv_bin,
        label=f"compile_fixture_body_diagnostic_{case_root.name}",
    )
    rtrace = build_baseline_rtrace_command(
        octree=octree,
        receiver_input=receiver_input,
        rgb_output=rgb,
        options=options,
        nthreads=1,
        cwd=scene_cwd,
        rtrace_bin=rtrace_bin,
    )
    return oconv, rtrace, ambient


def execute_diagnostic_trace(
    *,
    oconv_command: CommandSpec,
    rtrace_command: CommandSpec,
    expected_sensor_count: int,
    runner: LocalRunner,
    timeout_s: float | None = None,
) -> tuple[FloatArray, dict[str, object]]:
    case_root = Path(oconv_command.stdout_path or "").parent
    case_root.mkdir(parents=True, exist_ok=False)
    if rtrace_command.stdout_path is None:
        raise FixtureBodyOpticsAuditError("diagnostic trace output path is missing.")
    ambient_path = _option_value(rtrace_command.argv, "-af")
    if ambient_path is not None and Path(ambient_path).exists():
        raise FixtureBodyOpticsAuditError(
            "diagnostic ambient cache existed before its isolated trace."
        )
    compiled = runner.run(oconv_command, timeout_s=timeout_s)
    octree = Path(oconv_command.stdout_path or "")
    if not compiled.success or not octree.is_file() or octree.stat().st_size <= 0:
        raise FixtureBodyOpticsAuditError(
            compiled.failure_message or "diagnostic scene compilation failed."
        )
    traced = runner.run(rtrace_command, timeout_s=timeout_s)
    rgb = Path(rtrace_command.stdout_path)
    if not traced.success or not rgb.is_file() or rgb.stat().st_size <= 0:
        raise FixtureBodyOpticsAuditError(
            traced.failure_message or "diagnostic scene trace failed."
        )
    field = parse_basis_column(
        rgb.read_text(encoding="utf-8"),
        expected_sensor_count=expected_sensor_count,
    )
    if (
        field.ndim != 1
        or field.size != expected_sensor_count
        or not np.all(np.isfinite(field))
        or np.any(field < 0.0)
    ):
        raise FixtureBodyOpticsAuditError(
            "diagnostic trace produced an invalid PPFD field."
        )
    record = {
        "oconv": {
            "argv": list(oconv_command.argv),
            "cwd": str(oconv_command.cwd),
            "wall_time_s": compiled.wall_time_s,
            "octree_sha256": sha256_file(octree),
        },
        "rtrace": {
            "argv": list(rtrace_command.argv),
            "cwd": str(rtrace_command.cwd),
            "nthreads": 1,
            "wall_time_s": traced.wall_time_s,
            "raw_rgb_sha256": sha256_file(rgb),
        },
        "ambient_cache": (
            None
            if ambient_path is None
            else {
                "path": ambient_path,
                "sha256": sha256_file(Path(ambient_path)),
            }
        ),
    }
    return np.asarray(field, dtype=np.float64), record


def compute_field_statistics(
    field: Sequence[float] | FloatArray,
    coordinates_m: Sequence[Sequence[float]],
    *,
    resolution_x: int,
    resolution_y: int,
    receiver_length_m: float,
    receiver_width_m: float,
) -> FieldStatistics:
    values = np.asarray(field, dtype=np.float64)
    coordinates = np.asarray(coordinates_m, dtype=np.float64)
    if (
        values.ndim != 1
        or coordinates.shape != (values.size, 3)
        or values.size != resolution_x * resolution_y
        or not np.all(np.isfinite(values))
        or not np.all(np.isfinite(coordinates))
        or np.any(values < 0.0)
    ):
        raise FixtureBodyOpticsAuditError(
            "field statistics require finite, aligned non-negative samples."
        )
    if receiver_length_m <= 0.0 or receiver_width_m <= 0.0:
        raise ValueError("receiver-plane dimensions must be positive.")
    radii = np.hypot(coordinates[:, 0], coordinates[:, 1])
    center_indices = np.flatnonzero(radii <= 1.0e-9)
    if center_indices.size != 1:
        raise FixtureBodyOpticsAuditError(
            "receiver grid must contain exactly one exact-center sample."
        )
    inner = values[radii <= 0.5 + 1.0e-12]
    annulus = values[
        (radii >= 0.75 - 1.0e-12)
        & (radii <= 1.25 + 1.0e-12)
    ]
    if inner.size == 0 or annulus.size == 0:
        raise FixtureBodyOpticsAuditError(
            "receiver grid does not resolve the declared radial regions."
        )
    mean = float(np.mean(values))
    standard_deviation = float(np.std(values))
    minimum_index = int(np.argmin(values))
    maximum_index = int(np.argmax(values))
    center = float(values[int(center_indices[0])])
    center_region_mean = float(np.mean(inner))
    annulus_mean = float(np.mean(annulus))
    annulus_delta = annulus_mean - center_region_mean
    matrix = values.reshape((resolution_y, resolution_x))
    symmetry_180 = symmetry_residual(matrix, np.flip(matrix, axis=(0, 1)))
    symmetry_90 = (
        symmetry_residual(matrix, np.rot90(matrix))
        if resolution_x == resolution_y
        else None
    )
    cell_area = (
        receiver_length_m / resolution_x
        * receiver_width_m / resolution_y
    )
    maximum = float(values[maximum_index])
    minimum = float(values[minimum_index])
    return FieldStatistics(
        sensor_count=values.size,
        integrated_receiver_plane_ppfd_umol_s=float(
            math.fsum(float(value) for value in values) * cell_area
        ),
        mean=mean,
        standard_deviation=standard_deviation,
        cv_percent=100.0 * standard_deviation / mean,
        minimum=minimum,
        maximum=maximum,
        exact_center=center,
        mean_within_0_5m=center_region_mean,
        mean_annulus_0_75_to_1_25m=annulus_mean,
        annulus_minus_center=annulus_delta,
        annulus_minus_center_percent=(
            100.0 * annulus_delta / center_region_mean
        ),
        maximum_location_m=tuple(
            float(value) for value in coordinates[maximum_index]
        ),
        minimum_location_m=tuple(
            float(value) for value in coordinates[minimum_index]
        ),
        minimum_to_mean=minimum / mean,
        minimum_to_maximum=minimum / maximum,
        symmetry_180=symmetry_180,
        symmetry_90=symmetry_90,
    )


def symmetry_residual(
    field: Sequence[Sequence[float]] | FloatArray,
    transformed: Sequence[Sequence[float]] | FloatArray,
) -> dict[str, float]:
    left = np.asarray(field, dtype=np.float64)
    right = np.asarray(transformed, dtype=np.float64)
    if (
        left.shape != right.shape
        or left.size == 0
        or not np.all(np.isfinite(left))
        or not np.all(np.isfinite(right))
    ):
        raise FixtureBodyOpticsAuditError(
            "symmetry residual inputs must be finite and shape-aligned."
        )
    difference = left - right
    return {
        "mean_absolute": float(np.mean(np.abs(difference))),
        "rms": float(np.sqrt(np.mean(np.square(difference)))),
        "maximum_absolute": float(np.max(np.abs(difference))),
        "mean_absolute_percent_of_field_mean": (
            100.0
            * float(np.mean(np.abs(difference)))
            / float(np.mean(left))
        ),
    }


def signed_field_symmetry_maps(
    field: Sequence[float] | FloatArray,
    *,
    resolution_x: int,
    resolution_y: int,
) -> dict[str, FloatArray]:
    """Return original-minus-transformed residual maps on the source grid."""

    values = np.asarray(field, dtype=np.float64)
    if (
        values.ndim != 1
        or values.size != resolution_x * resolution_y
        or resolution_x <= 0
        or resolution_y <= 0
        or not np.all(np.isfinite(values))
    ):
        raise FixtureBodyOpticsAuditError(
            "signed symmetry maps require a finite field matching the grid."
        )
    matrix = values.reshape((resolution_y, resolution_x))
    maps = {
        "x_mirror": matrix - np.flip(matrix, axis=1),
        "y_mirror": matrix - np.flip(matrix, axis=0),
        "rotation_180": matrix - np.flip(matrix, axis=(0, 1)),
    }
    if resolution_x == resolution_y:
        maps["rotation_90"] = matrix - np.rot90(matrix)
    return {
        name: np.asarray(residual, dtype=np.float64)
        for name, residual in maps.items()
    }


def field_symmetry_diagnostics(
    field: Sequence[float] | FloatArray,
    coordinates_m: Sequence[Sequence[float]] | FloatArray,
    *,
    resolution_x: int,
    resolution_y: int,
) -> dict[str, object]:
    """Measure signed half-plane bias and exact grid-transform residuals."""

    values = np.asarray(field, dtype=np.float64)
    coordinates = np.asarray(coordinates_m, dtype=np.float64)
    if (
        values.ndim != 1
        or coordinates.shape != (values.size, 3)
        or values.size != resolution_x * resolution_y
        or not np.all(np.isfinite(values))
        or not np.all(np.isfinite(coordinates))
    ):
        raise FixtureBodyOpticsAuditError(
            "field symmetry diagnostics require finite aligned grid samples."
        )
    positive_x = values[coordinates[:, 0] > 0.0]
    negative_x = values[coordinates[:, 0] < 0.0]
    positive_y = values[coordinates[:, 1] > 0.0]
    negative_y = values[coordinates[:, 1] < 0.0]
    if any(
        subset.size == 0
        for subset in (positive_x, negative_x, positive_y, negative_y)
    ):
        raise FixtureBodyOpticsAuditError(
            "field symmetry diagnostics require samples in all half-planes."
        )
    residual_maps = signed_field_symmetry_maps(
        values,
        resolution_x=resolution_x,
        resolution_y=resolution_y,
    )

    def residual_statistics(residual: FloatArray) -> dict[str, float]:
        return {
            "mean_absolute": float(np.mean(np.abs(residual))),
            "rms": float(np.sqrt(np.mean(np.square(residual)))),
            "maximum_absolute": float(np.max(np.abs(residual))),
        }

    maximum_index = int(np.argmax(values))
    minimum_index = int(np.argmin(values))
    return {
        "upper_minus_lower_mean": float(
            np.mean(positive_y) - np.mean(negative_y)
        ),
        "right_minus_left_mean": float(
            np.mean(positive_x) - np.mean(negative_x)
        ),
        "x_mirror": residual_statistics(residual_maps["x_mirror"]),
        "y_mirror": residual_statistics(residual_maps["y_mirror"]),
        "rotation_180": residual_statistics(
            residual_maps["rotation_180"]
        ),
        "rotation_90": (
            None
            if "rotation_90" not in residual_maps
            else residual_statistics(residual_maps["rotation_90"])
        ),
        "maximum": float(values[maximum_index]),
        "maximum_location_m": [
            float(value) for value in coordinates[maximum_index]
        ],
        "minimum": float(values[minimum_index]),
        "minimum_location_m": [
            float(value) for value in coordinates[minimum_index]
        ],
    }


def difference_statistics(
    field: Sequence[float] | FloatArray,
    reference: Sequence[float] | FloatArray,
) -> dict[str, float]:
    values = np.asarray(field, dtype=np.float64)
    baseline = np.asarray(reference, dtype=np.float64)
    if (
        values.shape != baseline.shape
        or values.ndim != 1
        or values.size == 0
        or not np.all(np.isfinite(values))
        or not np.all(np.isfinite(baseline))
    ):
        raise FixtureBodyOpticsAuditError(
            "difference fields must be finite and shape-aligned."
        )
    delta = values - baseline
    return {
        "mean": float(np.mean(delta)),
        "minimum": float(np.min(delta)),
        "maximum": float(np.max(delta)),
        "mean_absolute": float(np.mean(np.abs(delta))),
        "rms": float(np.sqrt(np.mean(np.square(delta)))),
        "mean_percent_of_reference_mean": (
            100.0 * float(np.mean(delta)) / float(np.mean(baseline))
        ),
        "rms_percent_of_reference_mean": (
            100.0
            * float(np.sqrt(np.mean(np.square(delta))))
            / float(np.mean(baseline))
        ),
    }


def convergence_statistics(
    coarse: Sequence[float] | FloatArray,
    fine: Sequence[float] | FloatArray,
) -> dict[str, float | bool]:
    values = difference_statistics(coarse, fine)
    fine_array = np.asarray(fine, dtype=np.float64)
    relative_mean = abs(values["mean"]) / float(np.mean(fine_array))
    relative_rms = values["rms"] / float(np.mean(fine_array))
    return values | {
        "absolute_relative_mean": relative_mean,
        "relative_rms": relative_rms,
        "declared_mean_threshold": 0.01,
        "declared_pointwise_rms_threshold": 0.03,
        "passed": relative_mean <= 0.01 and relative_rms <= 0.03,
    }


def field_csv_text(
    coordinates_m: Sequence[Sequence[float]],
    field: Sequence[float] | FloatArray,
) -> str:
    coordinates = np.asarray(coordinates_m, dtype=np.float64)
    values = np.asarray(field, dtype=np.float64)
    if coordinates.shape != (values.size, 3):
        raise ValueError("coordinate and field lengths differ.")
    lines = ["x_m,y_m,z_m,ppfd_umol_m2_s"]
    lines.extend(
        f"{x:.12g},{y:.12g},{z:.12g},{value:.12g}"
        for (x, y, z), value in zip(coordinates, values, strict=True)
    )
    return "\n".join(lines) + "\n"


def audit_authenticated_asset_geometry(
    asset_id: str,
) -> dict[str, object]:
    authenticated = load_authenticated_fixture_asset(asset_id)
    components: list[dict[str, object]] = []
    external_triangles: list[Triangle] = []
    all_triangles: list[Triangle] = []
    for item in authenticated.primitives:
        triangles = authenticated.decoded.triangles(item.inventory)
        all_triangles.extend(triangles)
        if item.classification == "external_occluder":
            external_triangles.extend(triangles)
        components.append(
            _component_geometry_payload(authenticated, item, triangles)
        )
    external = tuple(external_triangles)
    complete = tuple(all_triangles)
    duplicate = _duplicate_triangle_payload(external)
    winding = _winding_payload(external)
    return {
        "asset": {
            "asset_id": authenticated.asset.asset_id,
            "system_id": authenticated.asset.system_id,
            "fixture_type": authenticated.asset.fixture_type,
            "resource_path": authenticated.asset.resource_path,
            "glb_byte_size": authenticated.asset.byte_size,
            "glb_sha256": authenticated.asset.sha256,
            "node_primitive_inventory_sha256": authenticated.inventory_sha256,
            "classification_sha256": authenticated.classification_sha256,
            "classification_counts": authenticated.classification_counts,
        },
        "proposed_aperture_registration": (
            None
            if authenticated.proposed_aperture_registration is None
            else authenticated.proposed_aperture_registration.to_payload()
        ),
        "full_bounds_local_mm": _bounds_payload(complete),
        "external_bounds_local_mm": _bounds_payload(external),
        "external_triangle_count": len(external),
        "external_projected_area_m2_by_direction": (
            _projected_area_payload(external)
        ),
        "triangle_quality": {
            "degenerate_triangle_count_area_le_1e-14_mm2": sum(
                _triangle_area(triangle) <= 1.0e-14 for triangle in external
            ),
            **duplicate,
            **winding,
        },
        "components": components,
        "classification_review_conclusion": (
            "Geometry diagnostics alone do not establish optical authority for "
            "reclassification; every exclusion would require component-specific "
            "mechanical evidence."
        ),
    }


def write_projected_silhouette_svgs(
    asset_id: str,
    output_directory: str | Path,
) -> tuple[dict[str, object], ...]:
    authenticated = load_authenticated_fixture_asset(asset_id)
    triangles = tuple(
        triangle
        for item in authenticated.external_primitives
        for triangle in authenticated.decoded.triangles(item.inventory)
    )
    root = Path(output_directory)
    root.mkdir(parents=True, exist_ok=True)
    directions = {
        "receiver_vertical": (0.0, 1.0, 0.0),
        "wall_x": (1.0, 0.0, 0.0),
        "wall_z": (0.0, 0.0, 1.0),
        "ceiling_diagonal": _unit((1.0, -1.0, 1.0)),
    }
    records: list[dict[str, object]] = []
    for name, direction in directions.items():
        projected = _project_triangles(triangles, direction)
        path = root / f"{asset_id}.{name}.svg"
        path.write_text(_silhouette_svg(projected), encoding="utf-8")
        records.append(
            {
                "asset_id": asset_id,
                "view": name,
                "projection_direction_local": list(direction),
                "path": str(path),
                "sha256": sha256_file(path),
                "area_note": (
                    "SVG is the exact projected triangle set; overlap is rendered "
                    "as an opaque union by the SVG viewer."
                ),
            }
        )
    return tuple(records)


def preflight_new_artifact_root(path: str | Path) -> Path:
    root = Path(path).expanduser().resolve()
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise FixtureBodyOpticsAuditError("artifact root must not be a symlink.")
    if root.exists():
        raise FixtureBodyOpticsAuditError(
            f"refusing existing audit artifact root: {root}"
        )
    parent = root.parent
    if not parent.is_dir() or parent.is_symlink():
        raise FixtureBodyOpticsAuditError(
            "artifact parent must be an existing non-symlink directory."
        )
    return root


def sha256_inventory(root: str | Path) -> tuple[tuple[str, str], ...]:
    directory = Path(root).resolve()
    if not directory.is_dir() or directory.is_symlink():
        raise FixtureBodyOpticsAuditError(
            "SHA-256 inventory root must be a non-symlink directory."
        )
    records: list[tuple[str, str]] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise FixtureBodyOpticsAuditError(
                f"artifact inventory rejects symlink: {path}"
            )
        if path.is_file() and path.name != "SHA256SUMS":
            records.append((path.relative_to(directory).as_posix(), sha256_file(path)))
    return tuple(records)


def write_sha256_inventory(root: str | Path) -> Path:
    directory = Path(root).resolve()
    records = sha256_inventory(directory)
    path = directory / "SHA256SUMS"
    if path.exists() or path.is_symlink():
        raise FixtureBodyOpticsAuditError(
            "refusing existing SHA256SUMS inventory."
        )
    path.write_text(
        "".join(f"{digest}  {relative}\n" for relative, digest in records),
        encoding="utf-8",
    )
    return path


def verify_sha256_inventory(root: str | Path) -> None:
    directory = Path(root).resolve()
    inventory = directory / "SHA256SUMS"
    if not inventory.is_file() or inventory.is_symlink():
        raise FixtureBodyOpticsAuditError("SHA256SUMS is missing or unsafe.")
    expected: list[tuple[str, str]] = []
    for line_number, line in enumerate(
        inventory.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if len(line) < 67 or line[64:66] != "  ":
            raise FixtureBodyOpticsAuditError(
                f"malformed SHA256SUMS line {line_number}."
            )
        digest, relative = line[:64], line[66:]
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise FixtureBodyOpticsAuditError(
                f"unsafe SHA256SUMS line {line_number}."
            )
        expected.append((relative, digest))
    actual = sha256_inventory(directory)
    if tuple(expected) != actual:
        raise FixtureBodyOpticsAuditError(
            "artifact SHA-256 inventory does not match current bytes."
        )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_text(payload: object) -> str:
    return json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        allow_nan=False,
        ensure_ascii=False,
    ) + "\n"


def _option_value(argv: Sequence[str], name: str) -> str | None:
    for index, token in enumerate(argv):
        if token == name and index + 1 < len(argv):
            return str(argv[index + 1])
    return None


def _component_geometry_payload(
    authenticated: AuthenticatedFixtureAsset,
    classified: object,
    triangles: Sequence[Triangle],
) -> dict[str, object]:
    inventory = classified.inventory  # type: ignore[attr-defined]
    classification = classified.classification  # type: ignore[attr-defined]
    return {
        "node_index": inventory.node_index,
        "node_path": inventory.node_path,
        "mesh_index": inventory.mesh_index,
        "primitive_index": inventory.primitive_index,
        "classification": classification,
        "triangle_count": len(triangles),
        "bounds_local_mm": _bounds_payload(triangles),
        "projected_area_m2_by_direction": _projected_area_payload(triangles),
        "degenerate_triangle_count_area_le_1e-14_mm2": sum(
            _triangle_area(triangle) <= 1.0e-14 for triangle in triangles
        ),
        "asset_id": authenticated.asset.asset_id,
    }


def _bounds_payload(triangles: Sequence[Triangle]) -> dict[str, list[float]]:
    if not triangles:
        return {"minimum": [], "maximum": [], "dimensions": []}
    points = np.asarray(
        [point for triangle in triangles for point in triangle],
        dtype=np.float64,
    )
    minimum = np.min(points, axis=0)
    maximum = np.max(points, axis=0)
    return {
        "minimum": minimum.tolist(),
        "maximum": maximum.tolist(),
        "dimensions": (maximum - minimum).tolist(),
    }


def _triangle_area(triangle: Triangle) -> float:
    a = np.asarray(triangle[0], dtype=np.float64)
    b = np.asarray(triangle[1], dtype=np.float64)
    c = np.asarray(triangle[2], dtype=np.float64)
    return 0.5 * float(np.linalg.norm(np.cross(b - a, c - a)))


def _projected_area(
    triangles: Sequence[Triangle],
    direction: Sequence[float],
) -> float:
    unit = np.asarray(_unit(direction), dtype=np.float64)
    total = 0.0
    for triangle in triangles:
        a, b, c = (np.asarray(point, dtype=np.float64) for point in triangle)
        total += 0.5 * abs(float(np.dot(np.cross(b - a, c - a), unit)))
    # Authored GLB coordinates are millimetres.  Half the two-sided shell sum
    # is the established projected opaque-area metric.
    return 0.5 * total * 1.0e-6


def _projected_area_payload(
    triangles: Sequence[Triangle],
) -> dict[str, float]:
    return {
        "receiver_vertical_local_y": _projected_area(triangles, (0.0, 1.0, 0.0)),
        "wall_local_x": _projected_area(triangles, (1.0, 0.0, 0.0)),
        "wall_local_z": _projected_area(triangles, (0.0, 0.0, 1.0)),
        "ceiling_diagonal": _projected_area(
            triangles, _unit((1.0, -1.0, 1.0))
        ),
    }


def _vertex_key(point: Point3) -> tuple[float, float, float]:
    return tuple(round(float(value), 9) for value in point)


def _duplicate_triangle_payload(
    triangles: Sequence[Triangle],
) -> dict[str, int]:
    seen: dict[tuple[tuple[float, float, float], ...], int] = {}
    oriented: dict[
        tuple[tuple[float, float, float], ...],
        set[tuple[tuple[float, float, float], ...]],
    ] = {}
    for triangle in triangles:
        order = tuple(_vertex_key(point) for point in triangle)
        key = tuple(sorted(order))
        seen[key] = seen.get(key, 0) + 1
        oriented.setdefault(key, set()).add(order)
    duplicate_groups = tuple(count for count in seen.values() if count > 1)
    return {
        "exact_duplicate_triangle_groups": len(duplicate_groups),
        "exact_duplicate_triangle_excess_count": sum(
            count - 1 for count in duplicate_groups
        ),
        "duplicate_groups_with_multiple_windings": sum(
            len(oriented[key]) > 1 for key, count in seen.items() if count > 1
        ),
    }


def _winding_payload(triangles: Sequence[Triangle]) -> dict[str, int]:
    edges: dict[
        tuple[tuple[float, float, float], tuple[float, float, float]],
        list[int],
    ] = {}
    for triangle in triangles:
        points = tuple(_vertex_key(point) for point in triangle)
        for start, end in (
            (points[0], points[1]),
            (points[1], points[2]),
            (points[2], points[0]),
        ):
            canonical = tuple(sorted((start, end)))
            direction = 1 if (start, end) == canonical else -1
            edges.setdefault(canonical, []).append(direction)
    return {
        "unique_edge_count": len(edges),
        "boundary_edge_count": sum(len(values) == 1 for values in edges.values()),
        "manifold_edge_count": sum(len(values) == 2 for values in edges.values()),
        "nonmanifold_edge_count": sum(len(values) > 2 for values in edges.values()),
        "same_direction_manifold_edge_count": sum(
            len(values) == 2 and values[0] == values[1]
            for values in edges.values()
        ),
    }


def _unit(vector: Sequence[float]) -> tuple[float, float, float]:
    values = np.asarray(vector, dtype=np.float64)
    magnitude = float(np.linalg.norm(values))
    if values.shape != (3,) or not math.isfinite(magnitude) or magnitude <= 0.0:
        raise ValueError("projection direction must be a finite nonzero vector.")
    return tuple(float(value) for value in values / magnitude)


def _projection_basis(
    direction: Sequence[float],
) -> tuple[np.ndarray, np.ndarray]:
    view = np.asarray(_unit(direction), dtype=np.float64)
    helper = (
        np.asarray((0.0, 0.0, 1.0))
        if abs(float(view[2])) < 0.9
        else np.asarray((0.0, 1.0, 0.0))
    )
    horizontal = np.cross(view, helper)
    horizontal /= np.linalg.norm(horizontal)
    vertical = np.cross(view, horizontal)
    return horizontal, vertical


def _project_triangles(
    triangles: Sequence[Triangle],
    direction: Sequence[float],
) -> tuple[tuple[tuple[float, float], ...], ...]:
    horizontal, vertical = _projection_basis(direction)
    return tuple(
        tuple(
            (
                float(np.dot(np.asarray(point), horizontal)),
                float(np.dot(np.asarray(point), vertical)),
            )
            for point in triangle
        )
        for triangle in triangles
    )


def _silhouette_svg(
    triangles: Sequence[Sequence[Sequence[float]]],
) -> str:
    points = np.asarray(
        [point for triangle in triangles for point in triangle],
        dtype=np.float64,
    )
    minimum = np.min(points, axis=0)
    maximum = np.max(points, axis=0)
    dimensions = np.maximum(maximum - minimum, 1.0e-12)
    margin = 10.0
    width = 800.0
    height = 800.0
    scale = min(
        (width - 2.0 * margin) / dimensions[0],
        (height - 2.0 * margin) / dimensions[1],
    )
    rendered: list[str] = []
    for triangle in triangles:
        coordinates = " ".join(
            f"{margin + (point[0] - minimum[0]) * scale:.6f},"
            f"{height - margin - (point[1] - minimum[1]) * scale:.6f}"
            for point in triangle
        )
        rendered.append(f'<polygon points="{coordinates}"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{int(width)}" height="{int(height)}" '
        f'viewBox="0 0 {int(width)} {int(height)}">\n'
        '<rect width="100%" height="100%" fill="white"/>\n'
        '<g fill="black" stroke="none" fill-rule="nonzero">\n'
        + "\n".join(rendered)
        + "\n</g>\n</svg>\n"
    )


__all__ = [
    "AUDIT_SCHEMA_ID",
    "AUDIT_SCHEMA_VERSION",
    "COARSE_SAMPLING",
    "DIAGNOSTIC_MATERIALS",
    "DiagnosticBodySources",
    "DiagnosticMaterial",
    "FINE_SAMPLING",
    "FieldStatistics",
    "FixtureBodyOpticsAuditError",
    "GeometryVariant",
    "TraceSampling",
    "ZERO_ABSORBER",
    "audit_authenticated_asset_geometry",
    "build_diagnostic_trace_commands",
    "canonical_json_text",
    "compute_field_statistics",
    "convergence_statistics",
    "deterministic_trace_options",
    "diagnostic_material_by_id",
    "difference_statistics",
    "execute_diagnostic_trace",
    "field_symmetry_diagnostics",
    "field_csv_text",
    "geometry_variant_instance_text",
    "materialize_and_compile_diagnostic_body_sources",
    "plan_diagnostic_body_sources",
    "preflight_new_artifact_root",
    "replace_shape_material",
    "sha256_file",
    "sha256_inventory",
    "signed_field_symmetry_maps",
    "sha256_text",
    "symmetry_residual",
    "verify_sha256_inventory",
    "write_projected_silhouette_svgs",
    "write_sha256_inventory",
]
