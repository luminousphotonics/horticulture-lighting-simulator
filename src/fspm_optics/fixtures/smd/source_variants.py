"""Completed-aperture angular contracts for the Proposed SMD fixture.

Production remains the calibrated native source.  Altered variants are only
usable when a completed-aperture characterization produced by the dedicated
Stage A angular experiment is supplied explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Final, Sequence

from .optical_stack import COMPLETED_APERTURE_PPE_UMOL_PER_J

NATIVE_SMD_SOURCE_VARIANT: Final = "native"
WIDE_SMD_SOURCE_VARIANT: Final = "altered_cosine_140deg"
NARROW_SMD_SOURCE_VARIANT: Final = "altered_cosine_100deg"
ANGULAR_VARIANT_KEYS: Final = (
    NATIVE_SMD_SOURCE_VARIANT,
    WIDE_SMD_SOURCE_VARIANT,
    NARROW_SMD_SOURCE_VARIANT,
)
ALTERED_FWHM_TOLERANCE_DEG: Final = 0.5
REFERENCE_FLUX_RELATIVE_TOLERANCE: Final = 0.005
OPTICAL_AXIS_PEAK_RELATIVE_TOLERANCE: Final = 0.001
ANGULAR_CAL_FILENAME: Final = "smd_source_variant.cal"
ANGULAR_FUNCTION_NAME: Final = "smd_source_variant"
ANGULAR_MODIFIER_NAME: Final = "smd_experiment_angular_modifier"


@dataclass(frozen=True, slots=True)
class SmdSourceVariant:
    key: str
    description: str
    target_completed_aperture_fwhm_deg: float | None = None

    @property
    def is_native(self) -> bool:
        return self.key == NATIVE_SMD_SOURCE_VARIANT

    @property
    def ideal_intensity_exponent(self) -> float:
        if self.target_completed_aperture_fwhm_deg is None:
            return 1.0
        return ideal_intensity_exponent(self.target_completed_aperture_fwhm_deg)

    @property
    def ideal_aperture_radiance_exponent(self) -> float:
        return self.ideal_intensity_exponent - 1.0


@dataclass(frozen=True, slots=True)
class CompletedApertureAngularCalibration:
    """Validated experiment-only authority for one physical-stack source.

    ``internal_radiance_exponent`` applies to the planar internal emitter.
    Radiant intensity at that plane therefore has exponent ``q + 1`` because
    projected area contributes one additional cosine.
    """

    variant_key: str
    internal_radiance_exponent: float
    internal_modifier_gain: float
    measured_completed_aperture_fwhm_deg: float
    completed_aperture_transmission: float
    reference_input_w: float
    measured_reference_completed_aperture_ppf_umol_s: float
    target_completed_aperture_fwhm_deg: float | None
    fwhm_tolerance_deg: float
    reference_flux_relative_tolerance: float
    profile_sha256: str
    characterization_identity_sha256: str

    def __post_init__(self) -> None:
        variant = get_smd_source_variant(self.variant_key)
        object.__setattr__(self, "variant_key", variant.key)
        exponent = _finite(
            "internal_radiance_exponent", self.internal_radiance_exponent
        )
        if exponent <= -1.0:
            raise ValueError(
                "internal_radiance_exponent must exceed -1 so planar emitted "
                "flux is integrable."
            )
        object.__setattr__(self, "internal_radiance_exponent", exponent)
        gain = _positive("internal_modifier_gain", self.internal_modifier_gain)
        expected_gain = normalized_planar_radiance_gain(exponent)
        if not math.isclose(gain, expected_gain, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(
                "internal modifier gain must normalize the complete planar "
                "hemispherical flux before transport."
            )
        object.__setattr__(self, "internal_modifier_gain", gain)
        measured_fwhm = _positive(
            "measured_completed_aperture_fwhm_deg",
            self.measured_completed_aperture_fwhm_deg,
        )
        if measured_fwhm >= 180.0:
            raise ValueError("measured completed-aperture FWHM must be below 180 degrees.")
        object.__setattr__(
            self, "measured_completed_aperture_fwhm_deg", measured_fwhm
        )
        transmission = _positive(
            "completed_aperture_transmission",
            self.completed_aperture_transmission,
        )
        if transmission > 1.0:
            raise ValueError("completed-aperture transmission must not exceed one.")
        object.__setattr__(self, "completed_aperture_transmission", transmission)
        reference_watts = _positive("reference_input_w", self.reference_input_w)
        object.__setattr__(self, "reference_input_w", reference_watts)
        reference_ppf = _positive(
            "measured_reference_completed_aperture_ppf_umol_s",
            self.measured_reference_completed_aperture_ppf_umol_s,
        )
        object.__setattr__(
            self,
            "measured_reference_completed_aperture_ppf_umol_s",
            reference_ppf,
        )
        tolerance = _positive("fwhm_tolerance_deg", self.fwhm_tolerance_deg)
        object.__setattr__(self, "fwhm_tolerance_deg", tolerance)
        flux_tolerance = _positive(
            "reference_flux_relative_tolerance",
            self.reference_flux_relative_tolerance,
        )
        if flux_tolerance >= 1.0:
            raise ValueError("reference flux relative tolerance must be below one.")
        object.__setattr__(
            self, "reference_flux_relative_tolerance", flux_tolerance
        )
        target = self.target_completed_aperture_fwhm_deg
        if variant.is_native:
            if target is not None:
                raise ValueError("native characterization must not declare a target FWHM.")
            if not math.isclose(exponent, 0.0, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError(
                    "native characterization must retain the unmodified "
                    "Lambertian internal source."
                )
        else:
            if target is None:
                raise ValueError("altered characterization requires a target FWHM.")
            target = _positive("target_completed_aperture_fwhm_deg", target)
            if target != variant.target_completed_aperture_fwhm_deg:
                raise ValueError("altered characterization target disagrees with its label.")
            if abs(measured_fwhm - target) > tolerance:
                raise ValueError(
                    f"measured completed-aperture FWHM {measured_fwhm:.6g} misses "
                    f"target {target:.6g} by more than {tolerance:.6g} degrees."
                )
        object.__setattr__(self, "target_completed_aperture_fwhm_deg", target)
        for name in ("profile_sha256", "characterization_identity_sha256"):
            _sha256(name, getattr(self, name))
        expected_ppf = reference_watts * COMPLETED_APERTURE_PPE_UMOL_PER_J
        relative_error = abs(reference_ppf - expected_ppf) / expected_ppf
        if relative_error > flux_tolerance:
            raise ValueError(
                "reference-input completed-aperture PPF misses the preregistered "
                f"relative tolerance: {relative_error:.6g} > {flux_tolerance:.6g}."
            )

    @property
    def internal_source_ppe_umol_per_j(self) -> float:
        return (
            COMPLETED_APERTURE_PPE_UMOL_PER_J
            / self.completed_aperture_transmission
        )

    @property
    def completed_aperture_ppe_umol_per_j(self) -> float:
        return (
            self.measured_reference_completed_aperture_ppf_umol_s
            / self.reference_input_w
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "variant": self.variant_key,
            "internal_radiance_exponent": self.internal_radiance_exponent,
            "internal_modifier_gain": self.internal_modifier_gain,
            "projected_area_cosine_included": True,
            "measured_completed_aperture_fwhm_deg": (
                self.measured_completed_aperture_fwhm_deg
            ),
            "target_completed_aperture_fwhm_deg": (
                self.target_completed_aperture_fwhm_deg
            ),
            "fwhm_tolerance_deg": self.fwhm_tolerance_deg,
            "completed_aperture_transmission": (
                self.completed_aperture_transmission
            ),
            "internal_source_ppe_umol_per_j": (
                self.internal_source_ppe_umol_per_j
            ),
            "reference_input_w": self.reference_input_w,
            "measured_reference_completed_aperture_ppf_umol_s": (
                self.measured_reference_completed_aperture_ppf_umol_s
            ),
            "completed_aperture_ppe_umol_per_j": (
                self.completed_aperture_ppe_umol_per_j
            ),
            "reference_flux_relative_tolerance": (
                self.reference_flux_relative_tolerance
            ),
            "profile_sha256": self.profile_sha256,
            "characterization_identity_sha256": (
                self.characterization_identity_sha256
            ),
        }

    @classmethod
    def from_dict(
        cls, payload: dict[str, object]
    ) -> "CompletedApertureAngularCalibration":
        return cls(
            variant_key=str(payload["variant"]),
            internal_radiance_exponent=float(
                payload["internal_radiance_exponent"]
            ),
            internal_modifier_gain=float(payload["internal_modifier_gain"]),
            measured_completed_aperture_fwhm_deg=float(
                payload["measured_completed_aperture_fwhm_deg"]
            ),
            completed_aperture_transmission=float(
                payload["completed_aperture_transmission"]
            ),
            reference_input_w=float(payload["reference_input_w"]),
            measured_reference_completed_aperture_ppf_umol_s=float(
                payload["measured_reference_completed_aperture_ppf_umol_s"]
            ),
            target_completed_aperture_fwhm_deg=(
                None
                if payload.get("target_completed_aperture_fwhm_deg") is None
                else float(payload["target_completed_aperture_fwhm_deg"])
            ),
            fwhm_tolerance_deg=float(payload["fwhm_tolerance_deg"]),
            reference_flux_relative_tolerance=float(
                payload["reference_flux_relative_tolerance"]
            ),
            profile_sha256=str(payload["profile_sha256"]),
            characterization_identity_sha256=str(
                payload["characterization_identity_sha256"]
            ),
        )


def get_smd_source_variant(value: str | None = None) -> SmdSourceVariant:
    key = normalize_smd_source_variant(value)
    if key == NATIVE_SMD_SOURCE_VARIANT:
        return SmdSourceVariant(
            key=key,
            description=(
                "Native Lambertian internal SMD source transported through the "
                "physical completed-aperture stack."
            ),
        )
    fwhm = 140.0 if key == WIDE_SMD_SOURCE_VARIANT else 100.0
    return SmdSourceVariant(
        key=key,
        description=(
            "Research-only physical-stack angular sensitivity source, uncalibrated "
            "until the experiment characterizes it, targeting "
            f"{fwhm:.0f} degree completed-aperture radiant-intensity FWHM."
        ),
        target_completed_aperture_fwhm_deg=fwhm,
    )


def normalize_smd_source_variant(value: str | None) -> str:
    token = str(value or NATIVE_SMD_SOURCE_VARIANT).strip().lower()
    if token in {"", "native", "baseline", "default"}:
        return NATIVE_SMD_SOURCE_VARIANT
    if token in {"altered_cosine_140deg", "cosine_140deg", "wide_cosine_140deg"}:
        return WIDE_SMD_SOURCE_VARIANT
    if token in {"altered_cosine_100deg", "cosine_100deg", "narrow_cosine_100deg"}:
        return NARROW_SMD_SOURCE_VARIANT
    raise ValueError(f"Unsupported SMD source variant {value!r}.")


def ideal_intensity_exponent(fwhm_deg: float) -> float:
    """Return ``m`` for the boundary intensity law ``I ∝ cos(theta)^m``."""

    fwhm = _positive("fwhm_deg", fwhm_deg)
    if fwhm >= 180.0:
        raise ValueError("fwhm_deg must be below 180 degrees.")
    return math.log(0.5) / math.log(math.cos(math.radians(fwhm / 2.0)))


def ideal_aperture_radiance_exponent(fwhm_deg: float) -> float:
    """Return the planar radiance exponent, including projected-area cosine."""

    return ideal_intensity_exponent(fwhm_deg) - 1.0


def normalized_planar_radiance_gain(radiance_exponent: float) -> float:
    """Gain preserving hemispherical flux relative to constant radiance."""

    exponent = _finite("radiance_exponent", radiance_exponent)
    if exponent <= -1.0:
        raise ValueError("radiance_exponent must exceed -1.")
    return (exponent + 2.0) / 2.0


def planar_radiant_intensity(
    theta_deg: float, radiance_exponent: float
) -> float:
    """Normalized planar intensity with the projected-area cosine included."""

    theta = _finite("theta_deg", theta_deg)
    exponent = _finite("radiance_exponent", radiance_exponent)
    if not 0.0 <= theta <= 90.0:
        raise ValueError("theta_deg must lie in [0, 90].")
    cosine = max(0.0, math.cos(math.radians(theta)))
    return cosine ** (exponent + 1.0)


def measured_fwhm_deg(
    angles_deg: Sequence[float], normalized_radiant_intensity: Sequence[float]
) -> float:
    """Measure full width by linear interpolation at half peak.

    The supplied profile is one side of a symmetric distribution, beginning at
    the completed-aperture normal.  The returned FWHM is twice the interpolated
    half-angle.
    """

    angles = tuple(float(value) for value in angles_deg)
    intensities = tuple(float(value) for value in normalized_radiant_intensity)
    if len(angles) != len(intensities) or len(angles) < 2:
        raise ValueError("angular profile requires equal-length sequences.")
    if angles[0] != 0.0 or any(
        not math.isfinite(value) for value in (*angles, *intensities)
    ):
        raise ValueError("angular profile must begin at zero with finite values.")
    if any(second <= first for first, second in zip(angles, angles[1:])):
        raise ValueError("angular profile angles must be strictly increasing.")
    peak = max(intensities)
    if peak <= 0.0:
        raise ValueError("angular profile must contain positive intensity.")
    off_axis_excess = (peak - intensities[0]) / peak
    if off_axis_excess > OPTICAL_AXIS_PEAK_RELATIVE_TOLERANCE:
        raise ValueError(
            "symmetric completed-aperture profile must peak on the optical axis."
        )
    half = 0.5 * peak
    for index in range(1, len(angles)):
        current = intensities[index]
        if current > half:
            continue
        previous = intensities[index - 1]
        if previous == current:
            half_angle = angles[index]
        else:
            fraction = (half - previous) / (current - previous)
            half_angle = angles[index - 1] + fraction * (
                angles[index] - angles[index - 1]
            )
        return 2.0 * half_angle
    raise ValueError("angular profile does not cross half maximum.")


def measured_optical_axis_reference_fwhm_deg(
    angles_deg: Sequence[float],
    radiant_intensity: Sequence[float],
) -> float:
    """Measure FWHM at half the optical-axis intensity.

    Characterization-only validation uses this numerical definition after
    separately enforcing its filtered optical-axis plateau contract.  Unlike
    :func:`measured_fwhm_deg`, this helper deliberately makes no raw-sample
    global-maximum assertion.
    """

    angles = tuple(float(value) for value in angles_deg)
    intensities = tuple(float(value) for value in radiant_intensity)
    if len(angles) != len(intensities) or len(angles) < 2:
        raise ValueError("angular profile requires equal-length sequences.")
    if angles[0] != 0.0 or any(
        not math.isfinite(value) for value in (*angles, *intensities)
    ):
        raise ValueError("angular profile must begin at zero with finite values.")
    if any(second <= first for first, second in zip(angles, angles[1:])):
        raise ValueError("angular profile angles must be strictly increasing.")
    optical_axis = intensities[0]
    if optical_axis <= 0.0 or any(value < 0.0 for value in intensities):
        raise ValueError(
            "angular profile must have positive optical-axis intensity and "
            "non-negative samples."
        )
    half = 0.5 * optical_axis
    for index in range(1, len(angles)):
        current = intensities[index]
        if current > half:
            continue
        previous = intensities[index - 1]
        if previous == current:
            half_angle = angles[index]
        else:
            fraction = (half - previous) / (current - previous)
            half_angle = angles[index - 1] + fraction * (
                angles[index] - angles[index - 1]
            )
        return 2.0 * half_angle
    raise ValueError("angular profile does not cross half optical-axis intensity.")


def normalized_profile(
    intensity: Sequence[float],
) -> tuple[float, ...]:
    values = tuple(float(value) for value in intensity)
    if not values or any(not math.isfinite(value) or value < 0.0 for value in values):
        raise ValueError("profile intensities must be finite and non-negative.")
    maximum = max(values)
    if maximum <= 0.0:
        raise ValueError("profile intensities must contain a positive value.")
    return tuple(value / maximum for value in values)


def axisymmetric_integrated_flux(
    angles_deg: Sequence[float], normalized_intensity: Sequence[float]
) -> float:
    """Trapezoidal solid-angle integral over the downward hemisphere."""

    angles = tuple(float(value) for value in angles_deg)
    values = tuple(float(value) for value in normalized_intensity)
    if len(angles) != len(values) or len(angles) < 2:
        raise ValueError("flux integration requires equal-length profiles.")
    total = 0.0
    for theta0, theta1, value0, value1 in zip(
        angles, angles[1:], values, values[1:]
    ):
        if theta1 <= theta0:
            raise ValueError("profile angles must be strictly increasing.")
        solid_angle = 2.0 * math.pi * (
            math.cos(math.radians(theta0)) - math.cos(math.radians(theta1))
        )
        total += 0.5 * (value0 + value1) * solid_angle
    if not math.isfinite(total) or total <= 0.0:
        raise ValueError("integrated polar flux must be finite and positive.")
    return total


def source_variant_cal_text(
    variant: SmdSourceVariant,
    calibration: CompletedApertureAngularCalibration | None = None,
) -> str | None:
    """Return CAL for an altered source; native retains no CAL artifact."""

    if variant.is_native:
        if calibration is not None and calibration.variant_key != variant.key:
            raise ValueError("native calibration variant mismatch.")
        return None
    if calibration is None:
        raise ValueError(
            f"source variant {variant.key!r} requires completed-aperture "
            "characterization; uncalibrated CAL artifacts are prohibited."
        )
    if calibration.variant_key != variant.key:
        raise ValueError("source variant calibration mismatch.")
    exponent = calibration.internal_radiance_exponent
    gain = calibration.internal_modifier_gain
    return (
        "{ smd_source_variant.cal: active modifier for internal emitter radiance }\n"
        "eps = 1e-9;\n"
        "c = max(abs(Dz),eps);\n"
        "pwr(x,y) = exp(y*log(x));\n"
        f"{ANGULAR_FUNCTION_NAME} = {gain:.15g} * pwr(c, {exponent:.15g});\n"
    )


def angular_modifier_radiance_text(
    variant: SmdSourceVariant,
    calibration: CompletedApertureAngularCalibration | None,
) -> str | None:
    if variant.is_native:
        return None
    if calibration is None:
        raise ValueError(
            f"source variant {variant.key!r} requires completed-aperture calibration."
        )
    if source_variant_cal_text(variant, calibration) is None:
        raise AssertionError("altered source unexpectedly omitted its CAL.")
    return (
        f"void brightfunc {ANGULAR_MODIFIER_NAME}\n"
        f"2 {ANGULAR_FUNCTION_NAME} {ANGULAR_CAL_FILENAME}\n"
        "0\n"
        "0\n"
    )


def write_source_variant_cal(
    path: str | Path,
    variant: SmdSourceVariant,
    calibration: CompletedApertureAngularCalibration | None = None,
) -> Path | None:
    text = source_variant_cal_text(variant, calibration)
    if text is None:
        return None
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return output


def profile_sha256(
    angles_deg: Sequence[float], normalized_intensity: Sequence[float]
) -> str:
    payload = {
        "angles_deg": [float(value) for value in angles_deg],
        "normalized_radiant_intensity": [
            float(value) for value in normalized_intensity
        ],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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


def _sha256(name: str, value: str) -> str:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256.")
    return value
