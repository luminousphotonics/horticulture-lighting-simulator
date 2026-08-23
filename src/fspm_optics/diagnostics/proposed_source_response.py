"""Isolated Radiance source-response closure for Proposed emitters.

This module is diagnostic-only.  It compares an analytic one-micromole source
contract with deterministic forward ``rtrace -I+`` integration and
``rfluxmtx`` integration before considering any production normalization
change.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from typing import Final, Iterable, Literal, Sequence

import numpy as np

from fspm_optics.fixtures.proposed_cob.source import (
    COB_ANGULAR_DAT_FILENAME,
    COB_LES_AREA_M2,
    load_cob_angular_law,
)
from fspm_optics.fixtures.smd.aperture_ppe_calibration import (
    aperture_flux_from_rgb,
    calibration_scene_texts,
    parse_equal_rgb,
)
from fspm_optics.fixtures.smd.optical_stack import (
    APERTURE_SIDE_M,
    CALIBRATION_EPSILON_M,
    EMITTER_AREA_M2,
    EMITTER_Z_M,
    proposed_fixture_materials_radiance_text,
    proposed_fixture_stack_radiance_text,
)
from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.executables import resolve_executable
from fspm_optics.radiance.options import radiance_options
from fspm_optics.radiance.runner import LocalRunner
from fspm_optics.radiance.versioning import (
    discover_radiance_installation,
    probe_radiance_version,
)
from fspm_optics.transport.basis.atomic import atomic_write_text
from fspm_optics.transport.scalar_ppfd import parse_rtrace_rgb_rows


SCHEMA_ID: Final = "fspm-optics.proposed-source-response"
SCHEMA_VERSION: Final = 1
INTERNAL_PPF_UMOL_S: Final = 1.0
DEFAULT_THETA_STEP_DEG: Final = 0.5
DEFAULT_PHI_STEP_DEG: Final = 5.0
DEFAULT_DISTANCE_M: Final = 20.0
UNITY_DAT_FILENAME: Final = "unity-angular-law.dat"
DETERMINISTIC_DIRECT_OPTIONS: Final = (
    "-ab",
    "0",
    "-u-",
    "-dj",
    "0",
    "-ds",
    "0",
    "-dt",
    "0",
    "-dc",
    "1",
)

Shape = Literal["square", "ring"]
AngularLaw = Literal["lambertian", "unity_flatcorr", "citizen_flatcorr"]


class ProposedSourceResponseError(RuntimeError):
    """The isolated source-response experiment did not complete safely."""


@dataclass(frozen=True, slots=True)
class SourceCase:
    case_id: str
    shape: Shape
    area_m2: float
    angular_law: AngularLaw
    scientific_role: str

    @property
    def side_m(self) -> float:
        return math.sqrt(self.area_m2)

    @property
    def radius_m(self) -> float:
        return math.sqrt(self.area_m2 / math.pi)

    @property
    def base_radiance(self) -> float:
        if self.angular_law == "lambertian":
            return INTERNAL_PPF_UMOL_S / (math.pi * self.area_m2)
        if self.angular_law == "unity_flatcorr":
            return INTERNAL_PPF_UMOL_S / (2.0 * math.pi * self.area_m2)
        if self.angular_law == "citizen_flatcorr":
            return INTERNAL_PPF_UMOL_S / self.area_m2
        raise AssertionError(self.angular_law)

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "shape": self.shape,
            "area_m2": self.area_m2,
            "side_m": self.side_m if self.shape == "square" else None,
            "radius_m": self.radius_m if self.shape == "ring" else None,
            "angular_law": self.angular_law,
            "base_radiance_umol_s_m2_sr": self.base_radiance,
            "analytic_internal_ppf_umol_s": INTERNAL_PPF_UMOL_S,
            "scientific_role": self.scientific_role,
        }


def source_cases() -> tuple[SourceCase, ...]:
    """Return the preregistered primitive/modifier isolation sequence."""

    equal_area = EMITTER_AREA_M2
    return (
        SourceCase(
            "native-square-lambertian",
            "square",
            equal_area,
            "lambertian",
            "native square Lambertian control",
        ),
        SourceCase(
            "equal-area-ring-lambertian",
            "ring",
            equal_area,
            "lambertian",
            "ring primitive isolated at the native square area",
        ),
        SourceCase(
            "equal-area-ring-unity-flatcorr",
            "ring",
            equal_area,
            "unity_flatcorr",
            "flatcorr isolated with constant unit DAT",
        ),
        SourceCase(
            "equal-area-ring-citizen-flatcorr",
            "ring",
            equal_area,
            "citizen_flatcorr",
            "authenticated Citizen law on the equal-area ring",
        ),
        SourceCase(
            "equal-area-square-citizen-flatcorr",
            "square",
            equal_area,
            "citizen_flatcorr",
            "Citizen modifier isolated from the ring primitive",
        ),
        SourceCase(
            "production-cob-ring-citizen-flatcorr",
            "ring",
            COB_LES_AREA_M2,
            "citizen_flatcorr",
            "exact production COB LES geometry and angular law",
        ),
    )


def unity_dat_text() -> str:
    """Return a constant DAT over the complete quadrilateral-symmetry domain."""

    return "2\n0 90 2\n0 90 2\n\n1 1 1 1\n"


def analytic_intensity(case: SourceCase, theta_deg: float, phi_deg: float) -> float:
    """Return analytic radiant intensity in micromoles/second/steradian."""

    theta = math.radians(_bounded_angle("theta_deg", theta_deg, 90.0))
    phi = _bounded_angle("phi_deg", phi_deg, 90.0)
    if case.angular_law == "lambertian":
        return math.cos(theta) / math.pi
    if case.angular_law == "unity_flatcorr":
        return 1.0 / (2.0 * math.pi)
    law = _cob_law()
    return _bilinear_unit_grid(
        law.normalized_intensity_by_horizontal_plane,
        horizontal_deg=phi,
        vertical_deg=math.degrees(theta),
    )


def analytic_flux(case: SourceCase) -> float:
    """Evaluate the declared source formula independently of Radiance."""

    if case.angular_law == "lambertian":
        return math.pi * case.base_radiance * case.area_m2
    if case.angular_law == "unity_flatcorr":
        return (
            case.area_m2
            * case.base_radiance
            * 2.0
            * math.pi
        )
    law = _cob_law()
    return case.area_m2 * case.base_radiance * law.unit_flux_integral


def analytic_empty_aperture_flux(
    case: SourceCase,
    *,
    theta_step_deg: float = 0.5,
    phi_step_deg: float = 2.5,
) -> float:
    """Integrate source positions/directions that reach the bare aperture."""

    theta_step = _divisor_step("theta_step_deg", theta_step_deg)
    phi_step = _divisor_step("phi_step_deg", phi_step_deg)
    distance = EMITTER_Z_M + CALIBRATION_EPSILON_M
    weighted: list[float] = []
    for theta, phi in midpoint_angles(theta_step, phi_step):
        theta_rad = math.radians(theta)
        phi_rad = math.radians(phi)
        radial_shift = distance * math.tan(theta_rad)
        weighted.append(
            analytic_intensity(case, theta, phi)
            * _aperture_overlap_fraction(
                case,
                dx=radial_shift * math.cos(phi_rad),
                dy=radial_shift * math.sin(phi_rad),
            )
        )
    return integrate_midpoint_intensity(
        weighted,
        theta_step_deg=theta_step,
        phi_step_deg=phi_step,
    )


def integrate_midpoint_intensity(
    values: Sequence[float],
    *,
    theta_step_deg: float,
    phi_step_deg: float,
) -> float:
    """Integrate a 0–90 degree midpoint grid using exact polar cell solid angle."""

    theta_step = _divisor_step("theta_step_deg", theta_step_deg)
    phi_step = _divisor_step("phi_step_deg", phi_step_deg)
    theta_count = round(90.0 / theta_step)
    phi_count = round(90.0 / phi_step)
    if len(values) != theta_count * phi_count:
        raise ValueError(
            "midpoint intensity count does not match theta/phi grid dimensions."
        )
    total = 0.0
    cursor = 0
    delta_phi = math.radians(phi_step)
    for theta_index in range(theta_count):
        theta0 = math.radians(theta_index * theta_step)
        theta1 = math.radians((theta_index + 1) * theta_step)
        solid_angle_per_phi_cell = (
            4.0 * delta_phi * (math.cos(theta0) - math.cos(theta1))
        )
        for _phi_index in range(phi_count):
            total += float(values[cursor]) * solid_angle_per_phi_cell
            cursor += 1
    return total


def execute(
    output_directory: str | Path,
    *,
    theta_step_deg: float = DEFAULT_THETA_STEP_DEG,
    phi_step_deg: float = DEFAULT_PHI_STEP_DEG,
    distance_m: float = DEFAULT_DISTANCE_M,
    rflux_samples: int = 50_000,
) -> Path:
    """Execute the complete isolated source-response boundary experiment."""

    root = Path(output_directory).expanduser().resolve()
    if root.exists() or root.is_symlink():
        raise ProposedSourceResponseError(
            f"source-response output must not already exist: {root}"
        )
    if isinstance(rflux_samples, bool) or rflux_samples <= 0:
        raise ValueError("rflux_samples must be a positive integer.")
    theta_step = _divisor_step("theta_step_deg", theta_step_deg)
    phi_step = _divisor_step("phi_step_deg", phi_step_deg)
    distance = _positive("distance_m", distance_m)
    root.mkdir(parents=True)

    installation = discover_radiance_installation()
    rfluxmtx = resolve_executable("rfluxmtx", cwd=Path.cwd(), label="rfluxmtx")
    runner = LocalRunner()
    records: list[dict[str, object]] = []
    for case in source_cases():
        case_root = root / "cases" / case.case_id
        case_root.mkdir(parents=True)
        _write_case_resources(case_root, case)
        analytic_grid = tuple(
            analytic_intensity(case, theta, phi)
            for theta, phi in midpoint_angles(theta_step, phi_step)
        )
        analytic_grid_flux = integrate_midpoint_intensity(
            analytic_grid,
            theta_step_deg=theta_step,
            phi_step_deg=phi_step,
        )
        analytic_aperture_flux = analytic_empty_aperture_flux(case)
        bare_forward = _execute_forward(
            case_root / "bare-forward",
            case=case,
            through_stack=False,
            theta_step_deg=theta_step,
            phi_step_deg=phi_step,
            distance_m=distance,
            ambient_bounces=0,
            runner=runner,
            oconv=installation.oconv.path,
            rtrace=installation.rtrace.path,
        )
        stack_forward_direct = _execute_forward(
            case_root / "stack-forward-ab0",
            case=case,
            through_stack=True,
            theta_step_deg=theta_step,
            phi_step_deg=phi_step,
            distance_m=distance,
            ambient_bounces=0,
            runner=runner,
            oconv=installation.oconv.path,
            rtrace=installation.rtrace.path,
        )
        bare_flux = _execute_bare_rfluxmtx(
            case_root / "bare-rfluxmtx",
            case=case,
            samples=rflux_samples,
            runner=runner,
            rfluxmtx=rfluxmtx,
        )
        stack_flux_ab0 = _execute_stack_rfluxmtx(
            case_root / "stack-rfluxmtx-ab0",
            case=case,
            samples=rflux_samples,
            ambient_bounces=0,
            runner=runner,
            rfluxmtx=rfluxmtx,
        )
        stack_flux_ab8 = _execute_stack_rfluxmtx(
            case_root / "stack-rfluxmtx-ab8",
            case=case,
            samples=rflux_samples,
            ambient_bounces=8,
            runner=runner,
            rfluxmtx=rfluxmtx,
        )
        selected_boundary_case = case.case_id in {
            "native-square-lambertian",
            "production-cob-ring-citizen-flatcorr",
        }
        stack_flux_converged = (
            _execute_stack_rfluxmtx(
                case_root / "stack-rfluxmtx-converged",
                case=case,
                samples=rflux_samples,
                ambient_bounces=8,
                subdivide_sources=True,
                runner=runner,
                rfluxmtx=rfluxmtx,
            )
            if selected_boundary_case
            else None
        )
        component_boundaries: dict[str, object] = {}
        convergence: dict[str, object] = {}
        production_option_sensitivity: dict[str, object] = {}
        if selected_boundary_case:
            for stack_variant in ("empty", "pmma_only", "cavity_only"):
                component_flux = _execute_stack_rfluxmtx(
                    case_root / f"{stack_variant}-rfluxmtx",
                    case=case,
                    samples=rflux_samples,
                    ambient_bounces=0,
                    subdivide_sources=True,
                    stack_variant=stack_variant,
                    runner=runner,
                    rfluxmtx=rfluxmtx,
                )
                component_record: dict[str, object] = {
                    "rfluxmtx": component_flux,
                }
                if stack_variant == "empty":
                    component_record["analytic_aperture_flux_umol_s"] = (
                        analytic_aperture_flux
                    )
                    component_record["rfluxmtx_to_analytic_ratio"] = (
                        component_flux["integrated_ppf_umol_s"]
                        / analytic_aperture_flux
                    )
                else:
                    component_forward = _execute_forward(
                        case_root / f"{stack_variant}-forward-ab0",
                        case=case,
                        through_stack=True,
                        stack_variant=stack_variant,
                        theta_step_deg=theta_step,
                        phi_step_deg=phi_step,
                        distance_m=distance,
                        ambient_bounces=0,
                        runner=runner,
                        oconv=installation.oconv.path,
                        rtrace=installation.rtrace.path,
                    )
                    component_record["forward_rtrace"] = component_forward
                    component_record["forward_to_rfluxmtx_ratio"] = (
                        component_forward["integrated_ppf_umol_s"]
                        / component_flux["integrated_ppf_umol_s"]
                    )
                component_boundaries[stack_variant] = component_record
            distance_records = {
                f"{probe_distance:.15g}": _execute_forward(
                    case_root
                    / f"stack-forward-distance-{probe_distance:.15g}m",
                    case=case,
                    through_stack=True,
                    theta_step_deg=theta_step,
                    phi_step_deg=phi_step,
                    distance_m=probe_distance,
                    ambient_bounces=0,
                    runner=runner,
                    oconv=installation.oconv.path,
                    rtrace=installation.rtrace.path,
                )
                for probe_distance in (5.0, 80.0)
            }
            distance_records[f"{distance:.15g}"] = stack_forward_direct
            angular_records = {
                f"{probe_step:.15g}": _execute_forward(
                    case_root / f"stack-forward-theta-step-{probe_step:.15g}",
                    case=case,
                    through_stack=True,
                    theta_step_deg=probe_step,
                    phi_step_deg=phi_step,
                    distance_m=distance,
                    ambient_bounces=0,
                    runner=runner,
                    oconv=installation.oconv.path,
                    rtrace=installation.rtrace.path,
                )
                for probe_step in (2.0, 1.0)
            }
            angular_records[f"{theta_step:.15g}"] = stack_forward_direct
            convergence = {
                "distance_m": distance_records,
                "theta_step_deg": angular_records,
            }
            production_direct = _execute_forward(
                case_root / "stack-forward-production-direct-options",
                case=case,
                through_stack=True,
                theta_step_deg=theta_step,
                phi_step_deg=phi_step,
                distance_m=distance,
                ambient_bounces=0,
                option_override=radiance_options("direct"),
                runner=runner,
                oconv=installation.oconv.path,
                rtrace=installation.rtrace.path,
            )
            production_option_sensitivity = {
                "deterministic_required": stack_forward_direct,
                "production_direct": production_direct,
                "production_to_deterministic_ratio": (
                    production_direct["integrated_ppf_umol_s"]
                    / stack_forward_direct["integrated_ppf_umol_s"]
                ),
            }
            if case.case_id == "production-cob-ring-citizen-flatcorr":
                rflux_count_records = {
                    str(sample_count): _execute_stack_rfluxmtx(
                        case_root / f"stack-rfluxmtx-samples-{sample_count}",
                        case=case,
                        samples=sample_count,
                        ambient_bounces=8,
                        subdivide_sources=True,
                        runner=runner,
                        rfluxmtx=rfluxmtx,
                    )
                    for sample_count in (5_000, 10_000, 50_000)
                }
                assert stack_flux_converged is not None
                rflux_count_records[str(rflux_samples)] = (
                    stack_flux_converged
                )
                convergence["rfluxmtx_sample_count"] = rflux_count_records
        records.append(
            {
                "case": case.to_dict(),
                "analytic": {
                    "formula_flux_umol_s": analytic_flux(case),
                    "midpoint_grid_flux_umol_s": analytic_grid_flux,
                    "midpoint_grid_relative_error": (
                        analytic_grid_flux / analytic_flux(case) - 1.0
                    ),
                    "empty_completed_aperture_flux_umol_s": (
                        analytic_aperture_flux
                    ),
                },
                "bare": {
                    "forward_rtrace": bare_forward,
                    "hemisphere_receiver_rfluxmtx_semantic_control": bare_flux,
                    "forward_to_analytic_ratio": (
                        bare_forward["integrated_ppf_umol_s"]
                        / analytic_flux(case)
                    ),
                    "hemisphere_control_to_expected_ratio": (
                        bare_flux["integrated_ppf_umol_s"]
                        / _hemisphere_control_expected_flux(case)
                    ),
                },
                "unchanged_proposed_stack": {
                    "forward_rtrace_ab0": stack_forward_direct,
                    "rfluxmtx_ab0": stack_flux_ab0,
                    "forward_to_rfluxmtx_ab0_ratio": (
                        stack_forward_direct["integrated_ppf_umol_s"]
                        / stack_flux_ab0["integrated_ppf_umol_s"]
                    ),
                    "rfluxmtx_ab8": stack_flux_ab8,
                    "rfluxmtx_subdivided_ab8": stack_flux_converged,
                    "required_forward_boundary_uses_ab0": True,
                },
                "component_boundaries": component_boundaries,
                "convergence": convergence,
                "production_option_sensitivity": (
                    production_option_sensitivity
                ),
            }
        )

    payload: dict[str, object] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "completed",
        "non_production_diagnostic": True,
        "quality_transport_cases_executed": 0,
        "configuration": {
            "internal_ppf_umol_s": INTERNAL_PPF_UMOL_S,
            "theta_step_deg": theta_step,
            "phi_step_deg": phi_step,
            "far_field_distance_m": distance,
            "rfluxmtx_samples": rflux_samples,
            "direct_options": list(DETERMINISTIC_DIRECT_OPTIONS),
            "identical_geometry_orientation_units": True,
            "emitter_z_m": EMITTER_Z_M,
        },
        "radiance": {
            "oconv": installation.oconv.to_dict(),
            "rtrace": installation.rtrace.to_dict(),
            "rfluxmtx": {
                "path": str(rfluxmtx),
                "version_text": probe_radiance_version(rfluxmtx),
            },
        },
        "source_cal_semantics": {
            "source_cal_path": "/opt/radiance/lib/source.cal",
            "src_theta": "acos(Dz) in degrees; theta from the emission -Z axis",
            "src_phi4": "fourfold fold of emission azimuth into 0..90 degrees",
            "flatcorr": "corr(v)/Rdot removes planar projected-area cosine",
            "directional_intensity": "I(theta,phi)=A*L*Rdot=A*base_radiance*DAT",
        },
        "cases": records,
    }
    payload["identity_sha256"] = _identity(payload)
    report = root / "proposed-source-response.v1.json"
    atomic_write_text(report, _json_text(payload))
    return report


def compare_corrected_direct_runs(
    old_report: str | Path,
    corrected_report: str | Path,
    output_path: str | Path,
) -> Path:
    """Compare old/new Direct fields and prove the source-scene edit scalar."""

    old_path = Path(old_report).expanduser().resolve()
    new_path = Path(corrected_report).expanduser().resolve()
    destination = Path(output_path).expanduser().resolve()
    if destination.exists() or destination.is_symlink():
        raise ProposedSourceResponseError(
            f"Direct comparison output already exists: {destination}"
        )
    old_payload = json.loads(old_path.read_text(encoding="utf-8"))
    new_payload = json.loads(new_path.read_text(encoding="utf-8"))
    old_cases = old_payload.get("cases")
    new_cases = new_payload.get("cases")
    if (
        not isinstance(old_cases, dict)
        or not isinstance(new_cases, dict)
        or set(old_cases) != set(new_cases)
    ):
        raise ProposedSourceResponseError(
            "old and corrected Direct case sets do not match."
        )

    records: dict[str, object] = {}
    source_scales_by_mode: dict[str, list[float]] = {}
    all_source_scenes_scalar = True
    all_coordinates_equal = True
    for case_id in sorted(old_cases):
        old_case = old_cases[case_id]
        new_case = new_cases[case_id]
        if not isinstance(old_case, dict) or not isinstance(new_case, dict):
            raise ProposedSourceResponseError("Direct case record is malformed.")
        old_root = Path(str(old_case["run_directory"]))
        new_root = Path(str(new_case["run_directory"]))
        old_full = np.asarray(
            np.load(
                old_root
                / "uniform-stage-a"
                / "uniform_complete_reference_ppfd.npy"
            ),
            dtype=np.float64,
        )
        new_full = np.asarray(
            np.load(
                new_root
                / "uniform-stage-a"
                / "uniform_complete_reference_ppfd.npy"
            ),
            dtype=np.float64,
        )
        if old_full.shape != new_full.shape or old_full.ndim != 1:
            raise ProposedSourceResponseError(
                f"Direct full-output field shape changed for {case_id}."
            )
        old_source = (
            old_root
            / "uniform-stage-a"
            / "uniform_complete_source.rad"
        )
        new_source = (
            new_root
            / "uniform-stage-a"
            / "uniform_complete_source.rad"
        )
        old_structure, old_radiances = _radiance_source_structure(old_source)
        new_structure, new_radiances = _radiance_source_structure(new_source)
        if (
            old_structure != new_structure
            or old_radiances.shape != new_radiances.shape
            or np.any(old_radiances <= 0.0)
        ):
            source_scalar = False
            source_scale = math.nan
            radiance_ratio_spread = math.inf
        else:
            ratios = new_radiances / old_radiances
            source_scale = float(np.mean(ratios))
            radiance_ratio_spread = float(np.max(ratios) - np.min(ratios))
            source_scalar = bool(
                np.allclose(
                    ratios,
                    source_scale,
                    rtol=0.0,
                    atol=2.0e-9,
                )
            )
        all_source_scenes_scalar &= source_scalar
        source_mode = case_id.rsplit("__", 1)[-1]
        source_scales_by_mode.setdefault(source_mode, []).append(source_scale)

        fitted_scale = float(np.dot(old_full, new_full) / np.dot(old_full, old_full))
        expected = source_scale * old_full
        full_residual = new_full - expected
        old_normalized = old_full / float(np.mean(old_full))
        new_normalized = new_full / float(np.mean(new_full))
        normalized_residual = new_normalized - old_normalized
        point_ratios = new_full / old_full

        old_csv = np.loadtxt(
            old_root / "ppfd.csv", delimiter=",", skiprows=1
        )
        new_csv = np.loadtxt(
            new_root / "ppfd.csv", delimiter=",", skiprows=1
        )
        if old_csv.shape != new_csv.shape or old_csv.shape[1] != 4:
            raise ProposedSourceResponseError(
                f"Direct effective PPFD CSV shape changed for {case_id}."
            )
        coordinates_equal = bool(
            np.array_equal(old_csv[:, :3], new_csv[:, :3])
        )
        all_coordinates_equal &= coordinates_equal
        effective_old = old_csv[:, 3]
        effective_new = new_csv[:, 3]
        effective_residual = effective_new - effective_old

        old_metrics = old_case.get("metrics")
        new_metrics = new_case.get("metrics")
        if not isinstance(old_metrics, dict) or not isinstance(new_metrics, dict):
            raise ProposedSourceResponseError("Direct metrics are malformed.")
        old_ppf = float(_nested_json(old_metrics, "ppf", "emitted_umol_s"))
        new_ppf = float(_nested_json(new_metrics, "ppf", "emitted_umol_s"))
        old_power = float(_nested_json(old_metrics, "power", "effective_w"))
        new_power = float(_nested_json(new_metrics, "power", "effective_w"))
        records[case_id] = {
            "source_scene": {
                "primitive_structure_sha256_old": _identity(old_structure),
                "primitive_structure_sha256_corrected": _identity(
                    new_structure
                ),
                "primitive_structure_equal": old_structure == new_structure,
                "light_material_count": int(old_radiances.size),
                "corrected_to_old_radiance_scale": source_scale,
                "radiance_ratio_spread": radiance_ratio_spread,
                "strictly_scalar": source_scalar,
            },
            "full_output_field": {
                "point_count": int(old_full.size),
                "least_squares_corrected_to_old_scale": fitted_scale,
                "expected_source_scale": source_scale,
                "point_ratio_minimum": float(np.min(point_ratios)),
                "point_ratio_maximum": float(np.max(point_ratios)),
                "point_ratio_standard_deviation": float(
                    np.std(point_ratios)
                ),
                "residual_rms_ppfd": float(
                    math.sqrt(float(np.mean(full_residual**2)))
                ),
                "residual_max_abs_ppfd": float(
                    np.max(np.abs(full_residual))
                ),
                "normalized_shape_residual_rms": float(
                    math.sqrt(float(np.mean(normalized_residual**2)))
                ),
                "normalized_shape_residual_max_abs": float(
                    np.max(np.abs(normalized_residual))
                ),
                "independent_rerun_pointwise_exact": bool(
                    np.array_equal(new_full, expected)
                ),
                "sampling_note": (
                    "Direct uses uncorrelated Monte Carlo sampling (-u+); "
                    "independent reruns need not be pointwise identical."
                ),
            },
            "effective_target_field": {
                "coordinates_bitwise_equal": coordinates_equal,
                "old_mean_ppfd": float(np.mean(effective_old)),
                "corrected_mean_ppfd": float(np.mean(effective_new)),
                "corrected_minus_old_rms_ppfd": float(
                    math.sqrt(float(np.mean(effective_residual**2)))
                ),
                "corrected_minus_old_max_abs_ppfd": float(
                    np.max(np.abs(effective_residual))
                ),
            },
            "reported_flux_and_power": {
                "old_completed_aperture_ppf_umol_s": old_ppf,
                "corrected_completed_aperture_ppf_umol_s": new_ppf,
                "old_power_w": old_power,
                "corrected_power_w": new_power,
                "old_ppf_over_2p6_w": old_ppf / 2.6,
                "corrected_ppf_over_2p6_w": new_ppf / 2.6,
                "corrected_power_closure_abs_w": abs(
                    new_power - new_ppf / 2.6
                ),
            },
            "uniformity": {
                "old_cv_percent": float(old_metrics["cv_percent"]),
                "corrected_cv_percent": float(new_metrics["cv_percent"]),
                "old_minimum_to_mean": (
                    float(old_metrics["minimum_ppfd_umol_m2_s"])
                    / float(old_metrics["achieved_mean_ppfd_umol_m2_s"])
                ),
                "corrected_minimum_to_mean": (
                    float(new_metrics["minimum_ppfd_umol_m2_s"])
                    / float(new_metrics["achieved_mean_ppfd_umol_m2_s"])
                ),
            },
        }

    collapsed_scales = {
        mode: math.fsum(values) / len(values)
        for mode, values in source_scales_by_mode.items()
    }
    payload: dict[str, object] = {
        "schema_id": "fspm-optics.proposed-source-response-direct-correction",
        "schema_version": 1,
        "old_report": str(old_path),
        "corrected_report": str(new_path),
        "quality_transport_cases_executed": 0,
        "direct_cases_compared": len(records),
        "correction_is_strictly_scalar_at_source_scene_boundary": (
            all_source_scenes_scalar
        ),
        "independent_direct_realizations_are_pointwise_exact": False,
        "receiver_coordinates_all_equal": all_coordinates_equal,
        "source_radiance_correction_scales": collapsed_scales,
        "derived_historical_quality_estimates": (
            _derived_historical_quality_estimates(collapsed_scales)
        ),
        "cases": records,
    }
    payload["identity_sha256"] = _identity(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(destination, _json_text(payload))
    return destination


def midpoint_angles(
    theta_step_deg: float, phi_step_deg: float
) -> Iterable[tuple[float, float]]:
    theta_step = _divisor_step("theta_step_deg", theta_step_deg)
    phi_step = _divisor_step("phi_step_deg", phi_step_deg)
    for theta_index in range(round(90.0 / theta_step)):
        theta = (theta_index + 0.5) * theta_step
        for phi_index in range(round(90.0 / phi_step)):
            yield theta, (phi_index + 0.5) * phi_step


def _execute_forward(
    output: Path,
    *,
    case: SourceCase,
    through_stack: bool,
    stack_variant: str = "full",
    theta_step_deg: float,
    phi_step_deg: float,
    distance_m: float,
    ambient_bounces: int,
    option_override: Sequence[str] | None = None,
    runner: LocalRunner,
    oconv: Path,
    rtrace: Path,
) -> dict[str, object]:
    output.mkdir()
    _write_case_resources(output, case)
    source_path = output / "source.rad"
    atomic_write_text(source_path, _source_text(case, receiver=False))
    scene_paths: list[Path] = []
    if through_stack:
        fixture_path = output / "fixture.rad"
        atomic_write_text(
            fixture_path,
            proposed_fixture_materials_radiance_text()
            + "\n"
            + _stack_variant_text(
                stack_variant,
                name_prefix=f"response_{case.case_id}",
            ),
        )
        scene_paths.append(fixture_path)
    scene_paths.append(source_path)
    octree = output / "scene.oct"
    _run(
        runner,
        CommandSpec(
            argv=(str(oconv), "-f", *(str(path) for path in scene_paths)),
            stdout_path=octree,
            stdout_mode="binary",
            cwd=output,
            label=f"{case.case_id}-forward-oconv",
        ),
        output / "oconv.stderr",
    )
    center_z = 0.0 if through_stack else EMITTER_Z_M
    sensors, distances = _far_field_sensors(
        theta_step_deg=theta_step_deg,
        phi_step_deg=phi_step_deg,
        distance_m=distance_m,
        center_z_m=center_z,
    )
    sensors_path = output / "far-field.pts"
    rgb_path = output / "far-field.rgb"
    atomic_write_text(sensors_path, sensors)
    options = (
        list(option_override)
        if option_override is not None
        else list(DETERMINISTIC_DIRECT_OPTIONS)
    )
    if option_override is None:
        options[1] = str(ambient_bounces)
    if option_override is None and ambient_bounces > 0:
        options.extend(
            (
                "-ad",
                "4096",
                "-as",
                "1024",
                "-aa",
                "0",
                "-ar",
                "128",
                "-lr",
                "-32",
                "-lw",
                "1e-7",
            )
        )
    _run(
        runner,
        CommandSpec(
            argv=(
                str(rtrace),
                "-h",
                "-I+",
                "-n",
                "1",
                *options,
                str(octree),
            ),
            stdin_path=sensors_path,
            stdout_path=rgb_path,
            cwd=output,
            label=f"{case.case_id}-forward-rtrace",
        ),
        output / "rtrace.stderr",
    )
    irradiances = parse_rtrace_rgb_rows(rgb_path.read_text(encoding="utf-8"))
    if len(irradiances) != len(distances):
        raise ProposedSourceResponseError(
            f"{case.case_id} forward sample count changed."
        )
    intensities = tuple(
        irradiance * distance**2
        for irradiance, distance in zip(irradiances, distances, strict=True)
    )
    integrated = integrate_midpoint_intensity(
        intensities,
        theta_step_deg=theta_step_deg,
        phi_step_deg=phi_step_deg,
    )
    return {
        "integrated_ppf_umol_s": integrated,
        "sample_count": len(intensities),
        "minimum_intensity_umol_s_sr": min(intensities),
        "maximum_intensity_umol_s_sr": max(intensities),
        "rgb_sha256": _sha256_file(rgb_path),
        "ambient_bounces": ambient_bounces,
        "options": options,
    }


def _execute_bare_rfluxmtx(
    output: Path,
    *,
    case: SourceCase,
    samples: int,
    runner: LocalRunner,
    rfluxmtx: Path,
) -> dict[str, object]:
    output.mkdir()
    _write_case_resources(output, case)
    sender = output / "sender.rad"
    receiver = output / "receiver.rad"
    atomic_write_text(sender, _sender_text(case))
    atomic_write_text(receiver, _hemisphere_receiver_text(case))
    rgb_path = output / "bare-flux.rgb"
    options = _rflux_options(samples=samples, ambient_bounces=0)
    _run(
        runner,
        CommandSpec(
            argv=(
                str(rfluxmtx),
                *options,
                str(sender),
                str(receiver),
            ),
            stdout_path=rgb_path,
            cwd=output,
            label=f"{case.case_id}-bare-rfluxmtx",
        ),
        output / "rfluxmtx.stderr",
    )
    rgb = parse_equal_rgb(rgb_path.read_text(encoding="utf-8"))
    integrated = math.pi * case.area_m2 * sum(rgb) / 3.0
    return {
        "integrated_ppf_umol_s": integrated,
        "rgb": list(rgb),
        "rgb_sha256": _sha256_file(rgb_path),
        "samples": samples,
        "options": options,
    }


def _execute_stack_rfluxmtx(
    output: Path,
    *,
    case: SourceCase,
    samples: int,
    ambient_bounces: int,
    subdivide_sources: bool = False,
    stack_variant: str = "full",
    runner: LocalRunner,
    rfluxmtx: Path,
) -> dict[str, object]:
    output.mkdir()
    _write_case_resources(output, case)
    scenes = calibration_scene_texts()
    for filename in ("materials.rad", "aperture_sender.rad"):
        atomic_write_text(output / filename, scenes[filename])
    atomic_write_text(
        output / "system.rad",
        _stack_variant_text(stack_variant, name_prefix="calibration"),
    )
    receiver = output / "source-receiver.rad"
    atomic_write_text(receiver, _source_text(case, receiver=True))
    rgb_path = output / "stack-flux.rgb"
    options = _rflux_options(
        samples=samples,
        ambient_bounces=ambient_bounces,
        subdivide_sources=subdivide_sources,
    )
    _run(
        runner,
        CommandSpec(
            argv=(
                str(rfluxmtx),
                *options,
                str(output / "aperture_sender.rad"),
                str(receiver),
                str(output / "materials.rad"),
                str(output / "system.rad"),
            ),
            stdout_path=rgb_path,
            cwd=output,
            label=f"{case.case_id}-stack-rfluxmtx-ab{ambient_bounces}",
        ),
        output / "rfluxmtx.stderr",
    )
    rgb = parse_equal_rgb(rgb_path.read_text(encoding="utf-8"))
    return {
        "integrated_ppf_umol_s": aperture_flux_from_rgb(rgb),
        "rgb": list(rgb),
        "rgb_sha256": _sha256_file(rgb_path),
        "samples": samples,
        "ambient_bounces": ambient_bounces,
        "subdivide_sources": subdivide_sources,
        "stack_variant": stack_variant,
        "options": options,
    }


def _source_text(case: SourceCase, *, receiver: bool) -> str:
    modifier = _modifier_text(case)
    value = case.base_radiance
    directive = "#@rfluxmtx h=u u=Y\n" if receiver else ""
    material = (
        f"{directive}{_modifier_name(case)} light response_source_light\n"
        "0\n0\n"
        f"3 {value:.15g} {value:.15g} {value:.15g}\n\n"
    )
    return modifier + material + _geometry_text(
        case, modifier="response_source_light", name="response_source"
    )


def _sender_text(case: SourceCase) -> str:
    return (
        "#@rfluxmtx h=u u=Y\n"
        "void glow response_sender_glow\n"
        "0\n0\n4 1 1 1 0\n\n"
        + _geometry_text(
            case, modifier="response_sender_glow", name="response_sender"
        )
    )


def _hemisphere_receiver_text(case: SourceCase) -> str:
    modifier = _modifier_text(case)
    value = case.base_radiance
    return (
        modifier
        + "#@rfluxmtx h=u u=Y\n"
        + f"{_modifier_name(case)} glow response_hemisphere_glow\n"
        + "0\n0\n"
        + f"4 {value:.15g} {value:.15g} {value:.15g} 0\n\n"
        + "response_hemisphere_glow source response_hemisphere\n"
        + "0\n0\n4 0 0 1 180\n"
    )


def _modifier_text(case: SourceCase) -> str:
    if case.angular_law == "lambertian":
        return ""
    filename = (
        UNITY_DAT_FILENAME
        if case.angular_law == "unity_flatcorr"
        else COB_ANGULAR_DAT_FILENAME
    )
    return (
        f"void brightdata {_modifier_name(case)}\n"
        f"5 flatcorr {filename} source.cal src_phi4 src_theta\n"
        "0\n0\n\n"
    )


def _modifier_name(case: SourceCase) -> str:
    return "void" if case.angular_law == "lambertian" else "response_angular_law"


def _geometry_text(
    case: SourceCase,
    *,
    modifier: str,
    name: str,
    z_m: float = EMITTER_Z_M,
) -> str:
    if case.shape == "ring":
        return (
            f"{modifier} ring {name}\n"
            "0\n0\n"
            f"8 0 0 {z_m:.15g} 0 0 -1 0 {case.radius_m:.15g}\n"
        )
    half = case.side_m / 2.0
    return (
        f"{modifier} polygon {name}\n"
        "0\n0\n12\n"
        f" {-half:.15g} {half:.15g} {z_m:.15g}\n"
        f" {half:.15g} {half:.15g} {z_m:.15g}\n"
        f" {half:.15g} {-half:.15g} {z_m:.15g}\n"
        f" {-half:.15g} {-half:.15g} {z_m:.15g}\n"
    )


def _write_case_resources(directory: Path, case: SourceCase) -> None:
    if case.angular_law == "unity_flatcorr":
        atomic_write_text(directory / UNITY_DAT_FILENAME, unity_dat_text())
    elif case.angular_law == "citizen_flatcorr":
        atomic_write_text(
            directory / COB_ANGULAR_DAT_FILENAME,
            _cob_law().dat_text,
        )


def _stack_variant_text(stack_variant: str, *, name_prefix: str) -> str:
    full = proposed_fixture_stack_radiance_text(
        0, 0.0, 0.0, 0.0, name_prefix=name_prefix
    )
    if stack_variant == "full":
        return full
    if stack_variant == "empty":
        return "# Deliberately empty optical system for source/aperture closure.\n"
    modifiers = {
        "pmma_only": {"proposed_pmma"},
        "cavity_only": {"proposed_ptfe", "proposed_black_bezel"},
    }
    try:
        selected = modifiers[stack_variant]
    except KeyError as exc:
        raise ValueError(f"unknown stack variant: {stack_variant!r}.") from exc
    blocks: list[str] = []
    current: list[str] = []
    for line in full.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "polygon":
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    blocks = [
        block for block in blocks if block.split(maxsplit=1)[0] in selected
    ]
    if not blocks:
        raise ProposedSourceResponseError(
            f"stack variant {stack_variant!r} selected no primitives."
        )
    return "\n\n".join(blocks) + "\n"


def _far_field_sensors(
    *,
    theta_step_deg: float,
    phi_step_deg: float,
    distance_m: float,
    center_z_m: float,
) -> tuple[str, tuple[float, ...]]:
    rows: list[str] = []
    distances: list[float] = []
    for theta_deg, phi_deg in midpoint_angles(
        theta_step_deg, phi_step_deg
    ):
        theta = math.radians(theta_deg)
        phi = math.radians(phi_deg)
        x = distance_m * math.sin(theta) * math.cos(phi)
        y = distance_m * math.sin(theta) * math.sin(phi)
        z = center_z_m - distance_m * math.cos(theta)
        dx = -x
        dy = -y
        dz = center_z_m - z
        actual_distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        rows.append(
            f"{x:.15g} {y:.15g} {z:.15g} "
            f"{dx / actual_distance:.15g} {dy / actual_distance:.15g} "
            f"{dz / actual_distance:.15g}\n"
        )
        distances.append(actual_distance)
    return "".join(rows), tuple(distances)


@lru_cache(maxsize=1)
def _cob_law():
    return load_cob_angular_law()


def _rflux_options(
    *,
    samples: int,
    ambient_bounces: int,
    subdivide_sources: bool = False,
) -> tuple[str, ...]:
    return (
        "-V+",
        "-h",
        "-u-",
        "-n",
        "1",
        "-c",
        str(samples),
        "-ab",
        str(ambient_bounces),
        "-ad",
        "4096",
        "-as",
        "1024",
        "-aa",
        "0",
        "-ar",
        "128",
        "-dj",
        "1" if subdivide_sources else "0",
        "-ds",
        "0.025" if subdivide_sources else "0",
        "-dt",
        "0",
        "-dc",
        "1",
        "-dr",
        "0",
        "-lr",
        "-32",
        "-lw",
        "1e-7",
        "-st",
        "0",
        "-av",
        "0",
        "0",
        "0",
        "-aw",
        "0",
    )


def _run(runner: LocalRunner, command: CommandSpec, stderr: Path) -> None:
    result = runner.run(command, stderr_path=stderr)
    if not result.success:
        raise ProposedSourceResponseError(
            result.failure_message or f"{command.label} failed."
        )


def _radiance_source_structure(
    path: Path,
) -> tuple[list[dict[str, object]], np.ndarray]:
    tokens: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        tokens.extend(line.split("#", 1)[0].split())
    cursor = 0
    structure: list[dict[str, object]] = []
    light_values: list[float] = []
    while cursor < len(tokens):
        if cursor + 3 > len(tokens):
            raise ProposedSourceResponseError(
                f"truncated Radiance primitive header: {path}"
            )
        modifier, primitive_type, identifier = tokens[cursor : cursor + 3]
        cursor += 3
        string_count = int(tokens[cursor])
        cursor += 1
        strings = tokens[cursor : cursor + string_count]
        cursor += string_count
        integer_count = int(tokens[cursor])
        cursor += 1
        integers = [int(value) for value in tokens[cursor : cursor + integer_count]]
        cursor += integer_count
        real_count = int(tokens[cursor])
        cursor += 1
        reals = [
            float(value) for value in tokens[cursor : cursor + real_count]
        ]
        cursor += real_count
        if primitive_type == "light":
            if len(reals) != 3 or not (
                reals[0] == reals[1] == reals[2]
            ):
                raise ProposedSourceResponseError(
                    f"non-scalar light material in {path}: {identifier}"
                )
            light_values.append(reals[0])
            recorded_reals: object = ["SCALAR_LIGHT_RADIANCE"]
        else:
            recorded_reals = reals
        structure.append(
            {
                "modifier": modifier,
                "primitive_type": primitive_type,
                "identifier": identifier,
                "strings": strings,
                "integers": integers,
                "reals": recorded_reals,
            }
        )
    return structure, np.asarray(light_values, dtype=np.float64)


def _nested_json(payload: dict[str, object], *path: str) -> object:
    value: object = payload
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise ProposedSourceResponseError(
                f"required JSON path is missing: {'.'.join(path)}"
            )
        value = value[key]
    return value


def _derived_historical_quality_estimates(
    scales: dict[str, float],
) -> dict[str, object]:
    historical = {
        "10x10_aisle_off": {
            "native_smd": {
                "ppf_umol_s": 5624.81,
                "power_w": 2163.39,
                "dimming_factor": 0.35465,
            },
            "cob_source_shape_surrogate": {
                "ppf_umol_s": 6212.81,
                "power_w": 2389.54,
                "dimming_factor": 0.39173,
            },
        },
        "30x50_aisle_on": {
            "native_smd": {
                "ppf_umol_s": 61026.72,
                "power_w": 23471.81,
                "dimming_factor": 0.36963,
            },
            "cob_source_shape_surrogate": {
                "ppf_umol_s": 67600.82,
                "power_w": 26000.31,
                "dimming_factor": 0.40945,
            },
        },
    }
    rooms: dict[str, object] = {}
    for room_id, modes in historical.items():
        corrected_modes: dict[str, object] = {}
        for mode, values in modes.items():
            scale = scales[mode]
            corrected_modes[mode] = {
                key: float(value) / scale for key, value in values.items()
            }
        native = corrected_modes["native_smd"]
        cob = corrected_modes["cob_source_shape_surrogate"]
        assert isinstance(native, dict) and isinstance(cob, dict)
        rooms[room_id] = {
            "source_modes": corrected_modes,
            "derived_cob_to_native_ppf_ratio": (
                float(cob["ppf_umol_s"]) / float(native["ppf_umol_s"])
            ),
        }
    return {
        "status": "derived_estimate_not_rerun_or_recertified",
        "quality_transport_cases_executed": 0,
        "formula": (
            "corrected target dimming, PPF, and power = historical value / "
            "source-radiance correction scale"
        ),
        "rooms": rooms,
    }


def _bilinear_unit_grid(
    values: Sequence[Sequence[float]],
    *,
    horizontal_deg: float,
    vertical_deg: float,
) -> float:
    horizontal = _bounded_angle("horizontal_deg", horizontal_deg, 90.0)
    vertical = _bounded_angle("vertical_deg", vertical_deg, 90.0)
    h0 = min(int(math.floor(horizontal)), 89)
    v0 = min(int(math.floor(vertical)), 89)
    h1 = h0 + 1
    v1 = v0 + 1
    hf = horizontal - h0
    vf = vertical - v0
    return (
        (1.0 - hf) * (1.0 - vf) * float(values[h0][v0])
        + hf * (1.0 - vf) * float(values[h1][v0])
        + (1.0 - hf) * vf * float(values[h0][v1])
        + hf * vf * float(values[h1][v1])
    )


def _hemisphere_control_expected_flux(case: SourceCase) -> float:
    """Expected rflux control when a source primitive makes ``Rdot`` unity."""

    if case.angular_law == "lambertian":
        return analytic_flux(case)
    theta_step = 0.25
    phi_step = 2.5
    values = tuple(
        analytic_intensity(case, theta, phi)
        * math.cos(math.radians(theta))
        for theta, phi in midpoint_angles(theta_step, phi_step)
    )
    return integrate_midpoint_intensity(
        values,
        theta_step_deg=theta_step,
        phi_step_deg=phi_step,
    )


def _aperture_overlap_fraction(
    case: SourceCase, *, dx: float, dy: float
) -> float:
    half_aperture = APERTURE_SIDE_M / 2.0
    if case.shape == "square":
        half_source = case.side_m / 2.0
        overlap_x = max(
            0.0,
            min(half_source, half_aperture - dx)
            - max(-half_source, -half_aperture - dx),
        )
        overlap_y = max(
            0.0,
            min(half_source, half_aperture - dy)
            - max(-half_source, -half_aperture - dy),
        )
        return overlap_x * overlap_y / case.area_m2

    radius = case.radius_m
    x0 = max(-radius, -half_aperture - dx)
    x1 = min(radius, half_aperture - dx)
    if x1 <= x0:
        return 0.0
    interval_count = 128
    step = (x1 - x0) / interval_count
    area = 0.0
    aperture_y0 = -half_aperture - dy
    aperture_y1 = half_aperture - dy
    for index in range(interval_count):
        x = x0 + (index + 0.5) * step
        circle_half_height = math.sqrt(max(0.0, radius * radius - x * x))
        area += max(
            0.0,
            min(circle_half_height, aperture_y1)
            - max(-circle_half_height, aperture_y0),
        )
    return min(1.0, area * step / case.area_m2)


def _divisor_step(name: str, value: float) -> float:
    step = _positive(name, value)
    count = round(90.0 / step)
    if count <= 0 or not math.isclose(
        count * step, 90.0, rel_tol=0.0, abs_tol=1e-12
    ):
        raise ValueError(f"{name} must divide 90 degrees exactly.")
    return step


def _bounded_angle(name: str, value: float, maximum: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > maximum:
        raise ValueError(f"{name} must be within 0..{maximum}.")
    return number


def _positive(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive.")
    return number


def _identity(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_text(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"


__all__ = [
    "DETERMINISTIC_DIRECT_OPTIONS",
    "INTERNAL_PPF_UMOL_S",
    "ProposedSourceResponseError",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "SourceCase",
    "analytic_empty_aperture_flux",
    "analytic_flux",
    "analytic_intensity",
    "compare_corrected_direct_runs",
    "execute",
    "integrate_midpoint_intensity",
    "midpoint_angles",
    "source_cases",
    "unity_dat_text",
]
