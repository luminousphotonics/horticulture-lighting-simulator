"""Angular-research characterization with non-authoritative backward flux checks."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Callable, Final, Mapping, Protocol, Sequence

from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.executables import resolve_executable
from fspm_optics.radiance.options import radiance_options
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.radiance.versioning import (
    discover_radiance_installation,
    probe_radiance_version,
)
from fspm_optics.transport.basis.atomic import atomic_write_text
from fspm_optics.transport.scalar_ppfd import parse_rtrace_rgb_rows

from .angular_validation import (
    AngularProfileValidationError,
    evaluate_axis_plateau,
)

from .aperture_ppe_calibration import (
    aperture_flux_from_rgb,
    calibration_scene_texts,
    check_options,
    parse_equal_rgb,
    unit_emitter_radiance,
)
from .optical_stack import (
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    EMITTER_SIDE_M,
    EMITTER_Z_M,
    downward_square_radiance_text,
)
from .source_variants import (
    ALTERED_FWHM_TOLERANCE_DEG,
    ANGULAR_CAL_FILENAME,
    ANGULAR_FUNCTION_NAME,
    ANGULAR_MODIFIER_NAME,
    ANGULAR_VARIANT_KEYS,
    NATIVE_SMD_SOURCE_VARIANT,
    REFERENCE_FLUX_RELATIVE_TOLERANCE,
    CompletedApertureAngularCalibration,
    axisymmetric_integrated_flux,
    get_smd_source_variant,
    measured_fwhm_deg,
    measured_optical_axis_reference_fwhm_deg,
    normalized_planar_radiance_gain,
    normalized_profile,
    profile_sha256,
)

LEGACY_CHARACTERIZATION_SCHEMA_ID: Final = (
    "fspm-optics.completed-aperture-angular-characterization"
)
LEGACY_CHARACTERIZATION_SCHEMA_VERSION: Final = 1
CHARACTERIZATION_SCHEMA_ID: Final = (
    "fspm-optics.backward-rfluxmtx-angular-research-characterization"
)
CHARACTERIZATION_SCHEMA_VERSION: Final = 3
CHARACTERIZATION_FILENAME: Final = "angular-characterization.json"
DEFAULT_ANGLE_STEP_DEG: Final = 0.5
DEFAULT_AZIMUTH_COUNT: Final = 4
DEFAULT_FAR_FIELD_DISTANCE_M: Final = 20.0
MAX_EXPONENT_SEARCH_ITERATIONS: Final = 18
# Minimum odd replication that rejects one transient Standard-quality
# direct-sampling firefly without hiding a persistent directional signal.
CHARACTERIZATION_DIRECTION_REPLICATE_COUNT: Final = 3

Progress = Callable[[str], None]


class AngularCharacterizationError(RuntimeError):
    """The non-authoritative angular research characterization failed closed."""


class CommandRunner(Protocol):
    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult: ...


@dataclass(frozen=True, slots=True)
class AngularCharacterizationConfig:
    output_directory: Path
    quality: str = "quality"
    nthreads: int = 1
    angle_step_deg: float = DEFAULT_ANGLE_STEP_DEG
    azimuth_count: int = DEFAULT_AZIMUTH_COUNT
    far_field_distance_m: float = DEFAULT_FAR_FIELD_DISTANCE_M
    fwhm_tolerance_deg: float = ALTERED_FWHM_TOLERANCE_DEG
    reference_flux_relative_tolerance: float = (
        REFERENCE_FLUX_RELATIVE_TOLERANCE
    )

    def __post_init__(self) -> None:
        output = Path(self.output_directory).expanduser()
        if not output.is_absolute():
            output = Path.cwd() / output
        object.__setattr__(self, "output_directory", output.resolve())
        if (
            isinstance(self.nthreads, bool)
            or not isinstance(self.nthreads, int)
            or self.nthreads <= 0
        ):
            raise ValueError("nthreads must be a positive integer.")
        if (
            isinstance(self.azimuth_count, bool)
            or not isinstance(self.azimuth_count, int)
            or self.azimuth_count <= 0
        ):
            raise ValueError("azimuth_count must be a positive integer.")
        for name in (
            "angle_step_deg",
            "far_field_distance_m",
            "fwhm_tolerance_deg",
            "reference_flux_relative_tolerance",
        ):
            value = _positive(name, getattr(self, name))
            object.__setattr__(self, name, value)
        if self.angle_step_deg > self.fwhm_tolerance_deg:
            raise ValueError(
                "angle_step_deg must not exceed the declared FWHM tolerance."
            )
        if self.angle_step_deg >= 90.0:
            raise ValueError("angle_step_deg must be below 90 degrees.")
        if self.reference_flux_relative_tolerance >= 1.0:
            raise ValueError("reference flux relative tolerance must be below one.")
        # Unknown quality strings silently map to standard in production; the
        # experiment fails closed instead.
        normalized_quality = str(self.quality).strip().lower()
        if normalized_quality not in {"direct", "standard", "quality", "rigorous"}:
            raise ValueError(
                "quality must be direct, standard, quality, or rigorous."
            )
        object.__setattr__(self, "quality", normalized_quality)


@dataclass(frozen=True, slots=True)
class MeasuredAngularProfile:
    angles_deg: tuple[float, ...]
    normalized_radiant_intensity: tuple[float, ...]
    measured_fwhm_deg: float
    normalized_solid_angle_integral_sr: float
    profile_sha256: str
    raw_radiant_intensity: tuple[float, ...] | None = None
    normalization_reference: str = "global_maximum"
    axis_plateau_contract: Mapping[str, object] | None = None
    direction_replicate_count: int = 1

    def to_dict(self) -> dict[str, object]:
        unit_flux_profile = tuple(
            value / self.normalized_solid_angle_integral_sr
            for value in self.normalized_radiant_intensity
        )
        reintegrated_unit_flux = axisymmetric_integrated_flux(
            self.angles_deg, unit_flux_profile
        )
        closure: dict[str, object] = {
            "normalized_solid_angle_integral_sr": (
                self.normalized_solid_angle_integral_sr
            ),
            "unit_flux_scale_per_sr": (
                1.0 / self.normalized_solid_angle_integral_sr
            ),
            "reintegrated_unit_flux": reintegrated_unit_flux,
            "absolute_closure_error": abs(reintegrated_unit_flux - 1.0),
            "profile_peak_normalized_to_one": (
                self.normalization_reference == "global_maximum"
            ),
            "integration": (
                "azimuth-averaged trapezoid over exact polar solid-angle cells"
            ),
        }
        if self.normalization_reference != "global_maximum":
            closure["normalization_reference"] = self.normalization_reference
        payload = {
            "angle_deg": list(self.angles_deg),
            "normalized_radiant_intensity": list(
                self.normalized_radiant_intensity
            ),
            "measured_fwhm_deg": self.measured_fwhm_deg,
            "integrated_flux_closure": closure,
            "profile_sha256": self.profile_sha256,
            "normalization_reference": self.normalization_reference,
            "sampling_contract": {
                "direction_replicate_count": self.direction_replicate_count,
                "direction_replicate_aggregation": (
                    "single_sample"
                    if self.direction_replicate_count == 1
                    else "per_direction_median"
                ),
                "azimuth_aggregation": "weighted_arithmetic_mean",
                "sample_order": "angle_then_azimuth_then_replicate",
            },
        }
        if self.raw_radiant_intensity is not None:
            payload["raw_azimuth_averaged_radiant_intensity"] = list(
                self.raw_radiant_intensity
            )
        if self.axis_plateau_contract is not None:
            payload["axis_plateau_contract"] = dict(
                self.axis_plateau_contract
            )
        return payload


def angular_sample_angles(angle_step_deg: float) -> tuple[float, ...]:
    step = _positive("angle_step_deg", angle_step_deg)
    count = int(math.floor(90.0 / step))
    values = tuple(index * step for index in range(count + 1))
    if values[-1] < 90.0:
        values = (*values, 90.0)
    elif values[-1] > 90.0:
        values = (*values[:-1], 90.0)
    return values


def format_far_field_receivers(
    *,
    angles_deg: Sequence[float],
    azimuth_count: int,
    distance_m: float,
    azimuth_quadrature: str = "midpoint",
    direction_replicate_count: int = 1,
) -> tuple[str, tuple[tuple[float, float], ...]]:
    """Return rtrace irradiance receivers and their angle/azimuth index map."""

    if azimuth_count <= 0:
        raise ValueError("azimuth_count must be positive.")
    if (
        isinstance(direction_replicate_count, bool)
        or not isinstance(direction_replicate_count, int)
        or direction_replicate_count <= 0
    ):
        raise ValueError("direction_replicate_count must be positive.")
    distance = _positive("distance_m", distance_m)
    rows: list[str] = []
    keys: list[tuple[float, float]] = []
    azimuths, _weights = far_field_azimuth_quadrature(
        azimuth_count, method=azimuth_quadrature
    )
    for theta_deg in angles_deg:
        theta = math.radians(float(theta_deg))
        if theta_deg >= 90.0:
            continue
        for phi_deg in azimuths:
            phi = math.radians(phi_deg)
            x = distance * math.sin(theta) * math.cos(phi)
            y = distance * math.sin(theta) * math.sin(phi)
            z = -distance * math.cos(theta)
            nx, ny, nz = -x / distance, -y / distance, -z / distance
            row = (
                f"{x:.12g} {y:.12g} {z:.12g} "
                f"{nx:.12g} {ny:.12g} {nz:.12g}\n"
            )
            for _replicate in range(direction_replicate_count):
                rows.append(row)
                keys.append((float(theta_deg), phi_deg))
    return "".join(rows), tuple(keys)


def far_field_azimuth_quadrature(
    azimuth_count: int,
    *,
    method: str = "midpoint",
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Return deterministic nodes and unit-sum weights over 0–90° symmetry."""

    if azimuth_count <= 0:
        raise ValueError("azimuth_count must be positive.")
    if method == "midpoint":
        return (
            tuple(
                90.0 * (index + 0.5) / azimuth_count
                for index in range(azimuth_count)
            ),
            tuple(1.0 / azimuth_count for _ in range(azimuth_count)),
        )
    if method == "square_symmetry_endpoint_trapezoid":
        if azimuth_count < 2:
            raise ValueError(
                "endpoint trapezoid azimuth quadrature requires two samples."
            )
        interval_count = azimuth_count - 1
        return (
            tuple(
                90.0 * index / interval_count
                for index in range(azimuth_count)
            ),
            tuple(
                (0.5 if index in {0, azimuth_count - 1} else 1.0)
                / interval_count
                for index in range(azimuth_count)
            ),
        )
    raise ValueError(f"unknown far-field azimuth quadrature: {method!r}.")


def build_internal_source_text(radiance_exponent: float) -> tuple[str, str | None]:
    """Build a unit-hemispherical-flux internal source and its active CAL."""

    exponent = _finite("radiance_exponent", radiance_exponent)
    if exponent <= -1.0:
        raise ValueError("radiance_exponent must exceed -1.")
    gain = normalized_planar_radiance_gain(exponent)
    cal_text = None
    light_modifier = "void"
    modifier = ""
    if exponent != 0.0:
        cal_text = _cal_text(exponent, gain)
        modifier = (
            f"void brightfunc {ANGULAR_MODIFIER_NAME}\n"
            f"2 {ANGULAR_FUNCTION_NAME} {ANGULAR_CAL_FILENAME}\n"
            "0\n"
            "0\n\n"
        )
        light_modifier = ANGULAR_MODIFIER_NAME
    value = unit_emitter_radiance()
    source = (
        modifier
        + f"{light_modifier} light angular_characterization_unit_emitter\n"
        "0\n"
        "0\n"
        f"3 {value:.15g} {value:.15g} {value:.15g}\n\n"
        + downward_square_radiance_text(
            "angular_characterization_unit_emitter",
            "angular_characterization_internal_emitter",
            0.0,
            0.0,
            EMITTER_Z_M,
            EMITTER_SIDE_M,
        )
    )
    return source, cal_text


def build_flux_receiver_text(
    radiance_exponent: float,
    *,
    internal_flux_umol_s: float,
) -> tuple[str, str | None]:
    """Build the rfluxmtx receiver at a declared internal hemispherical flux."""

    source, cal_text = build_internal_source_text(radiance_exponent)
    unit_value = unit_emitter_radiance()
    scaled_value = unit_value * _positive(
        "internal_flux_umol_s", internal_flux_umol_s
    )
    source = source.replace(
        f"3 {unit_value:.15g} {unit_value:.15g} {unit_value:.15g}",
        f"3 {scaled_value:.15g} {scaled_value:.15g} {scaled_value:.15g}",
        1,
    )
    light_modifier = "void" if radiance_exponent == 0.0 else ANGULAR_MODIFIER_NAME
    marker = f"{light_modifier} light angular_characterization_unit_emitter"
    if marker not in source:
        raise AssertionError("angular flux receiver light material is missing.")
    source = source.replace(marker, "#@rfluxmtx h=u u=Y\n" + marker, 1)
    return (
        source,
        cal_text,
    )


def profile_from_azimuth_samples(
    *,
    angles_deg: Sequence[float],
    sample_keys: Sequence[tuple[float, float]],
    irradiance_values: Sequence[float],
    normalize_to_optical_axis: bool = False,
    sample_weights: Sequence[float] | None = None,
    required_direction_replicate_count: int | None = None,
    validate_filtered_axis_plateau: bool = False,
) -> MeasuredAngularProfile:
    """Median repeated directions, then authenticate the averaged polar axis."""

    if len(sample_keys) != len(irradiance_values):
        raise ValueError("far-field keys and irradiance values must have equal length.")
    if validate_filtered_axis_plateau and not normalize_to_optical_axis:
        raise ValueError(
            "filtered axis-plateau validation requires optical-axis normalization."
        )
    if sample_weights is None:
        weights = tuple(1.0 for _ in sample_keys)
    else:
        weights = tuple(float(value) for value in sample_weights)
        if len(weights) != len(sample_keys) or any(
            not math.isfinite(value) or value <= 0.0 for value in weights
        ):
            raise ValueError(
                "far-field sample weights must be finite, positive, and "
                "equal in length to sample keys."
            )
    expected_angles = {
        float(angle) for angle in angles_deg if float(angle) < 90.0
    }
    by_direction: dict[
        tuple[float, float], list[tuple[float, float]]
    ] = {}
    for (angle, azimuth), value, weight in zip(
        sample_keys, irradiance_values, weights, strict=True
    ):
        resolved_angle = float(angle)
        resolved_azimuth = float(azimuth)
        if resolved_angle not in expected_angles:
            raise ValueError(
                f"far-field sample declares unexpected angle {resolved_angle}."
            )
        if not math.isfinite(resolved_azimuth):
            raise ValueError("far-field azimuth must be finite.")
        if not math.isfinite(value) or value < 0.0:
            raise ValueError("far-field irradiance must be finite and non-negative.")
        by_direction.setdefault(
            (resolved_angle, resolved_azimuth), []
        ).append((float(value), weight))

    counts = {len(samples) for samples in by_direction.values()}
    if len(counts) != 1:
        raise ValueError(
            "every far-field direction must have the same replicate count."
        )
    direction_replicate_count = next(iter(counts), 0)
    if direction_replicate_count <= 0:
        raise ValueError("far-field profile has no direction samples.")
    if (
        required_direction_replicate_count is not None
        and direction_replicate_count != required_direction_replicate_count
    ):
        raise ValueError(
            "far-field direction replicate count does not match the "
            f"characterization contract: {direction_replicate_count} != "
            f"{required_direction_replicate_count}."
        )

    azimuths_by_angle = {
        angle: {
            azimuth
            for candidate_angle, azimuth in by_direction
            if candidate_angle == angle
        }
        for angle in expected_angles
    }
    expected_azimuths = next(iter(azimuths_by_angle.values()), set())
    if not expected_azimuths or any(
        azimuths != expected_azimuths
        for azimuths in azimuths_by_angle.values()
    ):
        raise ValueError(
            "every far-field angle must have the same azimuth samples."
        )

    by_angle: dict[float, list[tuple[float, float]]] = {
        angle: [] for angle in expected_angles
    }
    for (angle, _azimuth), samples in sorted(by_direction.items()):
        sample_values = [value for value, _weight in samples]
        sample_direction_weights = [weight for _value, weight in samples]
        if any(
            not math.isclose(
                weight,
                sample_direction_weights[0],
                rel_tol=0.0,
                abs_tol=0.0,
            )
            for weight in sample_direction_weights[1:]
        ):
            raise ValueError(
                "replicates of one far-field direction must have equal weights."
            )
        by_angle[angle].append(
            (
                _median(sample_values),
                sample_direction_weights[0],
            )
        )

    averaged: list[float] = []
    for angle in angles_deg:
        resolved_angle = float(angle)
        if resolved_angle >= 90.0:
            averaged.append(0.0)
            continue
        values = by_angle[resolved_angle]
        if not values:
            raise ValueError(
                f"far-field profile has no azimuth samples at {angle}."
            )
        total_weight = math.fsum(weight for _value, weight in values)
        averaged.append(
            math.fsum(value * weight for value, weight in values)
            / total_weight
        )

    axis_plateau_contract: Mapping[str, object] | None = None
    if normalize_to_optical_axis:
        optical_axis = averaged[0]
        if not math.isfinite(optical_axis) or optical_axis <= 0.0:
            raise ValueError(
                "far-field optical-axis intensity must be finite and positive."
            )
        normalized = tuple(value / optical_axis for value in averaged)
        if validate_filtered_axis_plateau:
            try:
                axis_plateau_contract = evaluate_axis_plateau(
                    angles_deg,
                    normalized,
                    context="completed-aperture calibration profile",
                )
            except AngularProfileValidationError as error:
                raise AngularCharacterizationError(str(error)) from error
        measured = measured_optical_axis_reference_fwhm_deg(
            angles_deg, normalized
        )
        normalization_reference = "optical_axis_intensity"
    else:
        normalized = normalized_profile(averaged)
        measured = measured_fwhm_deg(angles_deg, normalized)
        normalization_reference = "global_maximum"
    integral = axisymmetric_integrated_flux(angles_deg, normalized)
    identity = profile_sha256(angles_deg, normalized)
    return MeasuredAngularProfile(
        angles_deg=tuple(float(value) for value in angles_deg),
        normalized_radiant_intensity=normalized,
        measured_fwhm_deg=measured,
        normalized_solid_angle_integral_sr=integral,
        profile_sha256=identity,
        raw_radiant_intensity=(
            tuple(averaged) if normalize_to_optical_axis else None
        ),
        normalization_reference=normalization_reference,
        axis_plateau_contract=axis_plateau_contract,
        direction_replicate_count=direction_replicate_count,
    )


def _median(values: Sequence[float]) -> float:
    ordered = sorted(float(value) for value in values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return 0.5 * (ordered[midpoint - 1] + ordered[midpoint])


def execute_completed_aperture_characterization(
    config: AngularCharacterizationConfig,
    *,
    runner: CommandRunner | None = None,
    progress: Progress = print,
) -> Path:
    """Characterize research variants without creating production authority."""

    root = config.output_directory
    root.mkdir(parents=True, exist_ok=True)
    installation = discover_radiance_installation()
    rfluxmtx = resolve_executable(
        "rfluxmtx", cwd=Path.cwd(), label="rfluxmtx"
    )
    local_runner = runner or LocalRunner()
    profiles: dict[str, MeasuredAngularProfile] = {}
    calibrations: dict[str, CompletedApertureAngularCalibration] = {}
    evaluations: dict[str, list[dict[str, object]]] = {
        key: [] for key in ANGULAR_VARIANT_KEYS
    }

    progress("Characterizing native completed-aperture angular distribution.")
    native_profile = _execute_profile(
        config,
        exponent=0.0,
        evaluation_directory=root / "native" / "profile",
        runner=local_runner,
        oconv_path=installation.oconv.path,
        rtrace_path=installation.rtrace.path,
        normalize_to_optical_axis=True,
        validate_filtered_axis_plateau=True,
        direction_replicate_count=(
            CHARACTERIZATION_DIRECTION_REPLICATE_COUNT
        ),
    )
    profiles[NATIVE_SMD_SOURCE_VARIANT] = native_profile
    evaluations[NATIVE_SMD_SOURCE_VARIANT].append(
        {
            "internal_radiance_exponent": 0.0,
            "measured_completed_aperture_fwhm_deg": (
                native_profile.measured_fwhm_deg
            ),
            "profile_sha256": native_profile.profile_sha256,
        }
    )

    for variant_key in ANGULAR_VARIANT_KEYS[1:]:
        variant = get_smd_source_variant(variant_key)
        assert variant.target_completed_aperture_fwhm_deg is not None
        progress(
            f"Calibrating {variant.target_completed_aperture_fwhm_deg:.0f} degree "
            "completed-aperture FWHM through the physical stack."
        )
        exponent, measured_profile, records = _calibrate_exponent(
            config,
            variant_key=variant_key,
            target_fwhm_deg=variant.target_completed_aperture_fwhm_deg,
            runner=local_runner,
            oconv_path=installation.oconv.path,
            rtrace_path=installation.rtrace.path,
        )
        profiles[variant_key] = measured_profile
        evaluations[variant_key].extend(records)

    for variant_key in ANGULAR_VARIANT_KEYS:
        variant = get_smd_source_variant(variant_key)
        profile = profiles[variant_key]
        if variant.is_native:
            exponent = 0.0
        else:
            exponent = float(
                evaluations[variant_key][-1]["internal_radiance_exponent"]
            )
        variant_root = root / variant_key
        progress(f"Measuring completed-aperture flux for {variant_key}.")
        transmission = _execute_flux_measurement(
            exponent=exponent,
            internal_flux_umol_s=1.0,
            directory=variant_root / "unit-flux",
            nthreads=config.nthreads,
            runner=local_runner,
            rfluxmtx_path=rfluxmtx,
        )
        required_internal_ppe = (
            COMPLETED_APERTURE_PPE_UMOL_PER_J / transmission
        )
        reference_ppf = _execute_flux_measurement(
            exponent=exponent,
            internal_flux_umol_s=required_internal_ppe,
            directory=variant_root / "reference-watt-validation",
            nthreads=config.nthreads,
            runner=local_runner,
            rfluxmtx_path=rfluxmtx,
        )
        identity_payload = {
            "variant": variant_key,
            "internal_radiance_exponent": exponent,
            "internal_modifier_gain": normalized_planar_radiance_gain(exponent),
            "profile_sha256": profile.profile_sha256,
            "measured_completed_aperture_fwhm_deg": profile.measured_fwhm_deg,
            "completed_aperture_transmission": transmission,
            "reference_input_w": 1.0,
            "measured_reference_completed_aperture_ppf_umol_s": reference_ppf,
            "optical_representation": "physical_internal_source_stack",
        }
        calibration = CompletedApertureAngularCalibration(
            variant_key=variant_key,
            internal_radiance_exponent=exponent,
            internal_modifier_gain=normalized_planar_radiance_gain(exponent),
            measured_completed_aperture_fwhm_deg=profile.measured_fwhm_deg,
            completed_aperture_transmission=transmission,
            reference_input_w=1.0,
            measured_reference_completed_aperture_ppf_umol_s=reference_ppf,
            target_completed_aperture_fwhm_deg=(
                variant.target_completed_aperture_fwhm_deg
            ),
            fwhm_tolerance_deg=config.fwhm_tolerance_deg,
            reference_flux_relative_tolerance=(
                config.reference_flux_relative_tolerance
            ),
            profile_sha256=profile.profile_sha256,
            characterization_identity_sha256=_identity(identity_payload),
        )
        calibrations[variant_key] = calibration

    payload = {
        "schema_id": CHARACTERIZATION_SCHEMA_ID,
        "schema_version": CHARACTERIZATION_SCHEMA_VERSION,
        "status": "completed",
        "authority_scope": {
            "classification": "angular_research_only",
            "production_normalization_authority": False,
            "backward_rfluxmtx_transmission_is_diagnostic_only": True,
            "current_authority_method": (
                "deterministic_forward_rtrace_integrated_flux"
            ),
            "production_manifest_compatibility": False,
        },
        "contract": {
            "fwhm_boundary": "completed_aperture_radiant_intensity",
            "ideal_intensity_law": "I(theta) proportional to cos(theta)^m",
            "ideal_intensity_exponent_equation": (
                "m = ln(0.5) / ln(cos(FWHM/2))"
            ),
            "planar_radiance_exponent_equation": "q = m - 1",
            "projected_area_cosine_included": True,
            "optical_representation": "physical_internal_source_stack",
            "same_representation_for_all_modularized_profiles": True,
            "post_trace_source_flux_scaling": False,
        },
        "configuration": {
            "quality": config.quality,
            "radiance_options": _deterministic_options(config.quality),
            "nthreads": config.nthreads,
            "angle_step_deg": config.angle_step_deg,
            "azimuth_count_over_one_square-symmetry_quadrant": (
                config.azimuth_count
            ),
            "direction_replicate_count": (
                CHARACTERIZATION_DIRECTION_REPLICATE_COUNT
            ),
            "direction_replicate_aggregation": "per_direction_median",
            "sample_order": "angle_then_azimuth_then_replicate",
            "far_field_distance_m": config.far_field_distance_m,
            "fwhm_tolerance_deg": config.fwhm_tolerance_deg,
            "reference_flux_relative_tolerance": (
                config.reference_flux_relative_tolerance
            ),
        },
        "radiance_provenance": {
            "oconv": installation.oconv.to_dict(),
            "rtrace": installation.rtrace.to_dict(),
            "rfluxmtx": {
                "path": str(rfluxmtx),
                "version_text": probe_radiance_version(rfluxmtx),
            },
        },
        "profiles": {
            key: profiles[key].to_dict() for key in ANGULAR_VARIANT_KEYS
        },
        "calibrations": {
            key: calibrations[key].to_dict() for key in ANGULAR_VARIANT_KEYS
        },
        "calibration_evaluations": evaluations,
    }
    payload["characterization_identity_sha256"] = _identity(payload)
    path = root / CHARACTERIZATION_FILENAME
    atomic_write_text(path, _json_text(payload))
    return path


def load_completed_aperture_characterization(
    path: str | Path,
) -> tuple[
    dict[str, CompletedApertureAngularCalibration],
    dict[str, dict[str, object]],
    dict[str, object],
]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise AngularCharacterizationError(
            "completed-aperture characterization artifact is missing or unsafe."
        )
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AngularCharacterizationError(
            "completed-aperture angular characterization is malformed."
        )
    declared_identity = payload.get("characterization_identity_sha256")
    without_identity = dict(payload)
    without_identity.pop("characterization_identity_sha256", None)
    if declared_identity != _identity(without_identity):
        raise AngularCharacterizationError(
            "completed-aperture characterization identity is stale."
        )
    schema = (
        payload.get("schema_id"),
        payload.get("schema_version"),
    )
    if schema not in {
        (
            LEGACY_CHARACTERIZATION_SCHEMA_ID,
            LEGACY_CHARACTERIZATION_SCHEMA_VERSION,
        ),
        (CHARACTERIZATION_SCHEMA_ID, CHARACTERIZATION_SCHEMA_VERSION),
    } or payload.get("status") != "completed":
        raise AngularCharacterizationError(
            "completed-aperture angular characterization is incomplete or incompatible."
        )
    uses_authenticated_optical_axis = schema == (
        CHARACTERIZATION_SCHEMA_ID,
        CHARACTERIZATION_SCHEMA_VERSION,
    )
    raw_calibrations = payload.get("calibrations")
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_calibrations, dict) or not isinstance(raw_profiles, dict):
        raise AngularCharacterizationError(
            "completed-aperture characterization payload is malformed."
        )
    if set(raw_calibrations) != set(ANGULAR_VARIANT_KEYS) or set(
        raw_profiles
    ) != set(ANGULAR_VARIANT_KEYS):
        raise AngularCharacterizationError(
            "completed-aperture characterization must contain exactly three variants."
        )
    calibrations: dict[str, CompletedApertureAngularCalibration] = {}
    profiles: dict[str, dict[str, object]] = {}
    for key in ANGULAR_VARIANT_KEYS:
        calibration_payload = raw_calibrations[key]
        profile_payload = raw_profiles[key]
        if not isinstance(calibration_payload, dict) or not isinstance(
            profile_payload, dict
        ):
            raise AngularCharacterizationError("angular variant payload is malformed.")
        calibration = CompletedApertureAngularCalibration.from_dict(
            calibration_payload
        )
        angles = profile_payload.get("angle_deg")
        intensity = profile_payload.get("normalized_radiant_intensity")
        if not isinstance(angles, list) or not isinstance(intensity, list):
            raise AngularCharacterizationError("angular polar profile is malformed.")
        actual_profile_hash = profile_sha256(angles, intensity)
        if (
            actual_profile_hash != calibration.profile_sha256
            or profile_payload.get("profile_sha256") != actual_profile_hash
        ):
            raise AngularCharacterizationError(
                f"angular polar profile hash mismatch for {key}."
            )
        closure = profile_payload.get("integrated_flux_closure")
        if not isinstance(closure, Mapping):
            raise AngularCharacterizationError(
                f"angular flux closure is missing for {key}."
            )
        integrated_flux = axisymmetric_integrated_flux(angles, intensity)
        unit_flux_profile = [value / integrated_flux for value in intensity]
        reintegrated_unit_flux = axisymmetric_integrated_flux(
            angles, unit_flux_profile
        )
        try:
            declared_integral = float(
                closure["normalized_solid_angle_integral_sr"]
            )
            declared_reintegrated = float(
                closure["reintegrated_unit_flux"]
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AngularCharacterizationError(
                f"angular flux closure is malformed for {key}."
            ) from error
        if (
            not math.isclose(
                declared_integral,
                integrated_flux,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                declared_reintegrated,
                reintegrated_unit_flux,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                reintegrated_unit_flux, 1.0, rel_tol=0.0, abs_tol=1e-12
            )
        ):
            raise AngularCharacterizationError(
                f"angular flux closure mismatch for {key}."
            )
        expected_characterization_identity = _identity(
            {
                "variant": key,
                "internal_radiance_exponent": (
                    calibration.internal_radiance_exponent
                ),
                "internal_modifier_gain": calibration.internal_modifier_gain,
                "profile_sha256": calibration.profile_sha256,
                "measured_completed_aperture_fwhm_deg": (
                    calibration.measured_completed_aperture_fwhm_deg
                ),
                "completed_aperture_transmission": (
                    calibration.completed_aperture_transmission
                ),
                "reference_input_w": calibration.reference_input_w,
                "measured_reference_completed_aperture_ppf_umol_s": (
                    calibration.measured_reference_completed_aperture_ppf_umol_s
                ),
                "optical_representation": "physical_internal_source_stack",
            }
        )
        if (
            expected_characterization_identity
            != calibration.characterization_identity_sha256
        ):
            raise AngularCharacterizationError(
                f"angular characterization identity mismatch for {key}."
            )
        if uses_authenticated_optical_axis:
            sampling = profile_payload.get("sampling_contract")
            if (
                profile_payload.get("normalization_reference")
                != "optical_axis_intensity"
                or not isinstance(sampling, Mapping)
                or sampling.get("direction_replicate_count")
                != CHARACTERIZATION_DIRECTION_REPLICATE_COUNT
                or sampling.get("direction_replicate_aggregation")
                != "per_direction_median"
                or sampling.get("azimuth_aggregation")
                != "weighted_arithmetic_mean"
                or sampling.get("sample_order")
                != "angle_then_azimuth_then_replicate"
            ):
                raise AngularCharacterizationError(
                    f"angular sampling contract mismatch for {key}."
                )
            try:
                plateau = evaluate_axis_plateau(
                    angles,
                    intensity,
                    context=f"authenticated {key} profile",
                )
            except AngularProfileValidationError as error:
                raise AngularCharacterizationError(str(error)) from error
            if profile_payload.get("axis_plateau_contract") != plateau:
                raise AngularCharacterizationError(
                    f"angular axis-plateau authentication mismatch for {key}."
                )
            measured = measured_optical_axis_reference_fwhm_deg(
                angles, intensity
            )
        else:
            measured = measured_fwhm_deg(angles, intensity)
        if not math.isclose(
            measured,
            calibration.measured_completed_aperture_fwhm_deg,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise AngularCharacterizationError(
                f"angular measured FWHM disagrees with calibration for {key}."
            )
        calibrations[key] = calibration
        profiles[key] = profile_payload
    return calibrations, profiles, payload


def _execute_profile(
    config: AngularCharacterizationConfig,
    *,
    exponent: float,
    evaluation_directory: Path,
    runner: CommandRunner,
    oconv_path: Path,
    rtrace_path: Path,
    authenticated_cal_text: str | None = None,
    normalize_to_optical_axis: bool = False,
    validate_filtered_axis_plateau: bool = False,
    direction_replicate_count: int = 1,
    azimuth_quadrature: str = "midpoint",
) -> MeasuredAngularProfile:
    evaluation_directory.mkdir(parents=True, exist_ok=False)
    scenes = calibration_scene_texts()
    source_text, cal_text = build_internal_source_text(exponent)
    if authenticated_cal_text is not None:
        if cal_text is None or authenticated_cal_text != cal_text:
            raise AngularCharacterizationError(
                "authenticated case CAL does not match the fixed angular "
                "characterization source."
            )
        cal_text = authenticated_cal_text
    scene_path = evaluation_directory / "fixture.rad"
    source_path = evaluation_directory / "internal-source.rad"
    cal_path = evaluation_directory / ANGULAR_CAL_FILENAME
    receiver_path = evaluation_directory / "far-field.pts"
    octree_path = evaluation_directory / "fixture.oct"
    rgb_path = evaluation_directory / "far-field.rgb"
    stderr_path = evaluation_directory / "far-field.stderr"
    atomic_write_text(
        scene_path,
        scenes["materials.rad"] + "\n" + scenes["system.rad"],
    )
    atomic_write_text(source_path, source_text)
    if cal_text is not None:
        atomic_write_text(cal_path, cal_text)
    angles = angular_sample_angles(config.angle_step_deg)
    receivers, keys = format_far_field_receivers(
        angles_deg=angles,
        azimuth_count=config.azimuth_count,
        distance_m=config.far_field_distance_m,
        azimuth_quadrature=azimuth_quadrature,
        direction_replicate_count=direction_replicate_count,
    )
    _azimuths, azimuth_weights = far_field_azimuth_quadrature(
        config.azimuth_count, method=azimuth_quadrature
    )
    sample_weights = tuple(
        weight
        for angle in angles
        if angle < 90.0
        for weight in azimuth_weights
        for _replicate in range(direction_replicate_count)
    )
    atomic_write_text(receiver_path, receivers)
    compile_result = runner.run(
        CommandSpec(
            argv=(str(oconv_path), "-f", str(scene_path), str(source_path)),
            stdout_path=octree_path,
            stdout_mode="binary",
            cwd=evaluation_directory,
            label="angular-characterization-oconv",
        ),
        stderr_path=evaluation_directory / "oconv.stderr",
    )
    if not compile_result.success:
        raise AngularCharacterizationError(
            compile_result.failure_message or "angular scene compilation failed."
        )
    trace_result = runner.run(
        CommandSpec(
            argv=(
                str(rtrace_path),
                "-h",
                "-I+",
                "-n",
                str(config.nthreads),
                *_deterministic_options(config.quality),
                str(octree_path),
            ),
            stdin_path=receiver_path,
            stdout_path=rgb_path,
            cwd=evaluation_directory,
            label="angular-characterization-rtrace",
        ),
        stderr_path=stderr_path,
    )
    if not trace_result.success:
        raise AngularCharacterizationError(
            trace_result.failure_message or "angular far-field trace failed."
        )
    values = parse_rtrace_rgb_rows(rgb_path.read_text(encoding="utf-8"))
    profile = profile_from_azimuth_samples(
        angles_deg=angles,
        sample_keys=keys,
        irradiance_values=values,
        normalize_to_optical_axis=normalize_to_optical_axis,
        sample_weights=sample_weights,
        required_direction_replicate_count=direction_replicate_count,
        validate_filtered_axis_plateau=validate_filtered_axis_plateau,
    )
    atomic_write_text(
        evaluation_directory / "profile.json",
        _json_text(
            {
                **profile.to_dict(),
                "internal_radiance_exponent": exponent,
                "internal_modifier_gain": normalized_planar_radiance_gain(
                    exponent
                ),
                "azimuth_quadrature": azimuth_quadrature,
            }
        ),
    )
    return profile


def execute_fixed_completed_aperture_profile(
    config: AngularCharacterizationConfig,
    *,
    exponent: float,
    evaluation_directory: str | Path,
    authenticated_cal_text: str | None,
    runner: CommandRunner | None = None,
    oconv_path: str | Path | None = None,
    rtrace_path: str | Path | None = None,
) -> MeasuredAngularProfile:
    """Measure one fixed, authenticated source without calibration or flux work.

    This deliberately exposes only the far-field ``oconv``/``rtrace`` path.
    It cannot tune an exponent, invoke ``rfluxmtx``, or change the source
    normalization used by transport.
    """

    if exponent == 0.0 and authenticated_cal_text is not None:
        raise AngularCharacterizationError(
            "native fixed-source validation must not supply a CAL artifact."
        )
    installation = None
    if oconv_path is None or rtrace_path is None:
        installation = discover_radiance_installation()
    resolved_oconv = (
        Path(oconv_path)
        if oconv_path is not None
        else installation.oconv.path
    )
    resolved_rtrace = (
        Path(rtrace_path)
        if rtrace_path is not None
        else installation.rtrace.path
    )
    return _execute_profile(
        config,
        exponent=exponent,
        evaluation_directory=Path(evaluation_directory),
        runner=runner or LocalRunner(),
        oconv_path=resolved_oconv,
        rtrace_path=resolved_rtrace,
        authenticated_cal_text=authenticated_cal_text,
        normalize_to_optical_axis=True,
        validate_filtered_axis_plateau=True,
        azimuth_quadrature="square_symmetry_endpoint_trapezoid",
    )


def _calibrate_exponent(
    config: AngularCharacterizationConfig,
    *,
    variant_key: str,
    target_fwhm_deg: float,
    runner: CommandRunner,
    oconv_path: Path,
    rtrace_path: Path,
) -> tuple[float, MeasuredAngularProfile, list[dict[str, object]]]:
    root = config.output_directory / variant_key / "exponent-search"
    root.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, object]] = []
    cache: dict[float, MeasuredAngularProfile] = {}

    def evaluate(exponent: float) -> MeasuredAngularProfile:
        rounded = round(float(exponent), 12)
        if rounded in cache:
            return cache[rounded]
        profile = _execute_profile(
            config,
            exponent=rounded,
            evaluation_directory=root / f"evaluation-{len(records):03d}",
            runner=runner,
            oconv_path=oconv_path,
            rtrace_path=rtrace_path,
            normalize_to_optical_axis=True,
            validate_filtered_axis_plateau=True,
            direction_replicate_count=(
                CHARACTERIZATION_DIRECTION_REPLICATE_COUNT
            ),
        )
        cache[rounded] = profile
        records.append(
            {
                "internal_radiance_exponent": rounded,
                "measured_completed_aperture_fwhm_deg": (
                    profile.measured_fwhm_deg
                ),
                "absolute_target_error_deg": abs(
                    profile.measured_fwhm_deg - target_fwhm_deg
                ),
                "profile_sha256": profile.profile_sha256,
            }
        )
        return profile

    lower_exponent = -0.95
    upper_exponent = 8.0
    lower_profile = evaluate(lower_exponent)
    upper_profile = evaluate(upper_exponent)
    if (
        lower_profile.measured_fwhm_deg < target_fwhm_deg
        or upper_profile.measured_fwhm_deg > target_fwhm_deg
    ):
        raise AngularCharacterizationError(
            f"{variant_key} target FWHM is not bracketed by the preregistered "
            "internal radiance exponent range."
        )
    best_exponent = lower_exponent
    best_profile = lower_profile
    for _iteration in range(MAX_EXPONENT_SEARCH_ITERATIONS):
        midpoint = 0.5 * (lower_exponent + upper_exponent)
        profile = evaluate(midpoint)
        if abs(profile.measured_fwhm_deg - target_fwhm_deg) < abs(
            best_profile.measured_fwhm_deg - target_fwhm_deg
        ):
            best_exponent, best_profile = midpoint, profile
        if (
            abs(profile.measured_fwhm_deg - target_fwhm_deg)
            <= config.fwhm_tolerance_deg
        ):
            break
        if profile.measured_fwhm_deg > target_fwhm_deg:
            lower_exponent = midpoint
        else:
            upper_exponent = midpoint
    if (
        abs(best_profile.measured_fwhm_deg - target_fwhm_deg)
        > config.fwhm_tolerance_deg
    ):
        raise AngularCharacterizationError(
            f"{variant_key} measured FWHM {best_profile.measured_fwhm_deg:.6g} "
            f"misses target {target_fwhm_deg:.6g} beyond "
            f"{config.fwhm_tolerance_deg:.6g} degrees."
        )
    # Make the accepted profile the final record so downstream selection cannot
    # accidentally use the last rejected bisection evaluation.
    accepted = {
        "internal_radiance_exponent": round(best_exponent, 12),
        "measured_completed_aperture_fwhm_deg": best_profile.measured_fwhm_deg,
        "absolute_target_error_deg": abs(
            best_profile.measured_fwhm_deg - target_fwhm_deg
        ),
        "profile_sha256": best_profile.profile_sha256,
        "accepted": True,
    }
    records.append(accepted)
    return round(best_exponent, 12), best_profile, records


def _execute_flux_measurement(
    *,
    exponent: float,
    internal_flux_umol_s: float,
    directory: Path,
    nthreads: int,
    runner: CommandRunner,
    rfluxmtx_path: Path,
) -> float:
    directory.mkdir(parents=True, exist_ok=False)
    scenes = calibration_scene_texts()
    for name in ("materials.rad", "system.rad", "aperture_sender.rad"):
        atomic_write_text(directory / name, scenes[name])
    receiver_text, cal_text = build_flux_receiver_text(
        exponent,
        internal_flux_umol_s=internal_flux_umol_s,
    )
    receiver_path = directory / "internal-emitter-receiver.rad"
    atomic_write_text(receiver_path, receiver_text)
    if cal_text is not None:
        atomic_write_text(directory / ANGULAR_CAL_FILENAME, cal_text)
    output_path = directory / "aperture-flux.rgb"
    command = CommandSpec(
        argv=(
            str(rfluxmtx_path),
            *check_options("C", nthreads),
            str(directory / "aperture_sender.rad"),
            str(receiver_path),
            str(directory / "materials.rad"),
            str(directory / "system.rad"),
        ),
        stdout_path=output_path,
        cwd=directory,
        label="completed-aperture-angular-flux",
    )
    result = runner.run(
        command,
        stderr_path=directory / "aperture-flux.stderr",
    )
    if not result.success:
        raise AngularCharacterizationError(
            result.failure_message
            or "completed-aperture angular flux measurement failed."
        )
    return aperture_flux_from_rgb(
        parse_equal_rgb(output_path.read_text(encoding="utf-8"))
    )


def _cal_text(exponent: float, gain: float) -> str:
    return (
        "{ smd_source_variant.cal: active modifier for internal emitter radiance }\n"
        "eps = 1e-9;\n"
        "c = max(abs(Dz),eps);\n"
        "pwr(x,y) = exp(y*log(x));\n"
        f"{ANGULAR_FUNCTION_NAME} = {gain:.15g} * pwr(c, {exponent:.15g});\n"
    )


def _deterministic_options(quality: str) -> tuple[str, ...]:
    options = [
        option
        for option in radiance_options(quality)
        if option not in {"-u+", "-u-"}
    ]
    options.append("-u-")
    return tuple(options)


def _identity(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _json_text(payload: object) -> str:
    return (
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


def _positive(name: str, value: object) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be finite and positive.")
    return number


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be finite.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    return number
