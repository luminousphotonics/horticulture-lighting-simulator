"""Non-authoritative backward-matrix aperture-flux diagnostic."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
from typing import Callable, Final, Literal, Protocol, Sequence, cast

from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.executables import resolve_executable
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.radiance.versioning import probe_radiance_version
from fspm_optics.transport.basis.atomic import atomic_write_text

from .optical_stack import (
    ACCEPTED_FIXTURE_TRANSMISSION,
    APERTURE_AREA_M2,
    APERTURE_SIDE_M,
    BEZEL_OUTER_SIDE_M,
    CALIBRATION_EPSILON_M,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    EMITTER_AREA_M2,
    EMITTER_SIDE_M,
    EMITTER_Z_M,
    INTERNAL_SOURCE_PPE_UMOL_PER_J,
    PMMA_BOTTOM_Z_M,
    PMMA_IOR,
    PMMA_SIDE_M,
    PMMA_TOP_Z_M,
    PROPOSED_FIXTURE_OPTICAL_STACK_ID,
    PTFE_PHYSICAL_THICKNESS_M,
    PTFE_REFLECTANCE,
    polygon_radiance_text,
    proposed_fixture_materials_radiance_text,
    proposed_fixture_stack_radiance_text,
)


DEFAULT_OUTPUT_DIRECTORY: Final = Path(
    ".proposed-fixture-calibration/aperture-ppe-plan"
)
RESULT_FILENAME: Final = "aperture_ppe_calibration.json"
EPSILON_M: Final = CALIBRATION_EPSILON_M
TARGET_COMPLETED_APERTURE_PPE_UMOL_PER_J: Final = (
    COMPLETED_APERTURE_PPE_UMOL_PER_J
)
ANALYTIC_ERROR_LIMIT: Final = 1.0e-6
RELATIVE_DIFFERENCE_LIMIT: Final = 0.01
RGB_REL_TOL: Final = 1.0e-12
RGB_ABS_TOL: Final = 1.0e-15

CheckKey = Literal["analytic", "A", "B", "C"]
Progress = Callable[[str], None]


class AperturePpeCalibrationError(RuntimeError):
    """The backward-matrix diagnostic could not be planned, run, or parsed."""


class CommandRunner(Protocol):
    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult: ...


@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    output_directory: Path = DEFAULT_OUTPUT_DIRECTORY
    nproc: int = 1

    def __post_init__(self) -> None:
        output = Path(self.output_directory).expanduser()
        if not output.is_absolute():
            output = Path.cwd() / output
        object.__setattr__(self, "output_directory", output.resolve())
        if isinstance(self.nproc, bool) or not isinstance(self.nproc, int) or self.nproc <= 0:
            raise ValueError("nproc must be a positive integer.")


@dataclass(frozen=True, slots=True)
class CalibrationAction:
    key: CheckKey
    label: str
    options: tuple[str, ...]
    command: CommandSpec
    stderr_path: Path


def unit_emitter_radiance() -> float:
    """Equal-RGB radiance for exactly 1.0 umol/s hemispherical flux."""

    value = 1.0 / (math.pi * EMITTER_AREA_M2)
    assert math.isclose(
        math.pi * EMITTER_AREA_M2 * value,
        1.0,
        rel_tol=1.0e-15,
        abs_tol=1.0e-15,
    )
    return value


def parse_equal_rgb(text: str) -> tuple[float, float, float]:
    tokens = text.split()
    if len(tokens) != 3:
        raise AperturePpeCalibrationError(
            f"expected one RGB triple, received {len(tokens)} token(s)."
        )
    try:
        return _equal_rgb(tuple(float(token) for token in tokens))
    except ValueError as exc:
        raise AperturePpeCalibrationError("rfluxmtx RGB output is not numeric.") from exc


def aperture_flux_from_rgb(rgb: Sequence[float]) -> float:
    """Convert rfluxmtx h=u/-V+ output using pi times aperture area."""

    values = _equal_rgb(tuple(float(value) for value in rgb))
    flux = math.pi * APERTURE_AREA_M2 * (sum(values) / 3.0)
    if flux < 0.0:
        raise AperturePpeCalibrationError("aperture flux must be non-negative.")
    return flux


def _equal_rgb(values: Sequence[float]) -> tuple[float, float, float]:
    if len(values) != 3 or any(not math.isfinite(value) for value in values):
        raise AperturePpeCalibrationError("RGB output must be three finite channels.")
    if not all(
        math.isclose(values[0], value, rel_tol=RGB_REL_TOL, abs_tol=RGB_ABS_TOL)
        for value in values[1:]
    ):
        raise AperturePpeCalibrationError(
            f"rfluxmtx RGB channels disagree beyond the strict tolerance: {tuple(values)!r}."
        )
    return cast(tuple[float, float, float], tuple(values))


_CHECK_PARAMETERS: Final = {
    "A": ("5000", "6", "2048", "0.05", "-24", "1e-06"),
    "B": ("20000", "6", "2048", "0.05", "-24", "1e-06"),
    "C": ("20000", "8", "4096", "0.025", "-32", "1e-07"),
}


def check_options(check: Literal["A", "B", "C"], nproc: int) -> tuple[str, ...]:
    if isinstance(nproc, bool) or not isinstance(nproc, int) or nproc <= 0:
        raise ValueError("nproc must be a positive integer.")
    try:
        c_value, ab, ad, ds, lr, lw = _CHECK_PARAMETERS[check]
    except KeyError as exc:
        raise ValueError(f"unknown calibration check: {check!r}") from exc
    return (
        "-V+", "-h", "-u-", "-n", str(nproc), "-c", c_value,
        "-ab", ab, "-ad", ad, "-ds", ds, "-dj", "1",
        "-lr", lr, "-lw", lw, "-st", "0",
        "-av", "0", "0", "0", "-aw", "0",
    )


def calibration_scene_texts() -> dict[str, str]:
    """Return the fixed five-file physical model."""

    return {
        "materials.rad": proposed_fixture_materials_radiance_text(),
        "system.rad": _system_text(),
        "aperture_sender.rad": _aperture_sender_text(),
        "internal_emitter_receiver.rad": _emitter_receiver_text(),
        "analytic_uniform_hemisphere_receiver.rad": _analytic_receiver_text(),
    }


def build_calibration_actions(
    config: CalibrationConfig,
    *,
    rfluxmtx_command: str | Path = "rfluxmtx",
) -> tuple[CalibrationAction, ...]:
    scenes = config.output_directory / "scenes"
    raw = config.output_directory / "raw"
    stack_paths = (
        scenes / "aperture_sender.rad",
        scenes / "internal_emitter_receiver.rad",
        scenes / "materials.rad",
        scenes / "system.rad",
    )
    definitions = (
        (
            "analytic", "analytic unit-Lambertian h=u validation", "C",
            (scenes / "aperture_sender.rad", scenes / "analytic_uniform_hemisphere_receiver.rad"),
        ),
        ("A", "sampling check A", "A", stack_paths),
        ("B", "sampling check B", "B", stack_paths),
        ("C", "transport check C", "C", stack_paths),
    )
    actions = []
    for key, label, option_key, paths in definitions:
        options = check_options(cast(Literal["A", "B", "C"], option_key), config.nproc)
        actions.append(
            CalibrationAction(
                key=cast(CheckKey, key),
                label=label,
                options=options,
                command=CommandSpec(
                    argv=(str(rfluxmtx_command), *options, *(str(path) for path in paths)),
                    stdout_path=raw / f"{key}.stdout",
                    cwd=config.output_directory,
                    label=f"aperture-ppe-{key}",
                ),
                stderr_path=raw / f"{key}.stderr",
            )
        )
    return tuple(actions)


def evaluate_observations(
    *,
    analytic_flux: float | None,
    flux_a: float | None,
    flux_b: float | None,
    flux_c: float | None,
) -> dict[str, object]:
    analytic_error = None if analytic_flux is None else abs(analytic_flux - 1.0)
    a_to_b = _relative_difference(flux_a, flux_b)
    b_to_c = _relative_difference(flux_b, flux_c)
    analytic_pass = None if analytic_error is None else analytic_error < ANALYTIC_ERROR_LIMIT
    a_to_b_pass = None if a_to_b is None else a_to_b < RELATIVE_DIFFERENCE_LIMIT
    b_to_c_pass = None if b_to_c is None else b_to_c < RELATIVE_DIFFERENCE_LIMIT
    accepted = (
        analytic_pass is True
        and a_to_b_pass is True
        and b_to_c_pass is True
        and flux_c is not None
        and math.isfinite(flux_c)
        and flux_c > 0.0
    )
    candidate = flux_c if accepted else None
    return {
        "analytic": {
            "expected_flux_umol_per_s": 1.0,
            "observed_flux_umol_per_s": analytic_flux,
            "absolute_error": analytic_error,
            "maximum_error_exclusive": ANALYTIC_ERROR_LIMIT,
            "pass": analytic_pass,
        },
        "aperture_flux_observations_umol_per_s": {"A": flux_a, "B": flux_b, "C": flux_c},
        "relative_differences": {"A_to_B": a_to_b, "B_to_C": b_to_c},
        "acceptance_checks": {
            "analytic_unit_flux_error_below_1e-6": analytic_pass,
            "A_to_B_relative_difference_below_1_percent": a_to_b_pass,
            "B_to_C_relative_difference_below_1_percent": b_to_c_pass,
        },
        "t_fixture_candidate": candidate,
        "candidate_is_production_authority": False,
        "candidate_disposition": (
            "diagnostic_only_never_publish_as_normalization_authority"
        ),
        "required_internal_ppe_umol_per_j": (
            None if candidate is None else TARGET_COMPLETED_APERTURE_PPE_UMOL_PER_J / candidate
        ),
    }


def plan_calibration(config: CalibrationConfig) -> Path:
    """Write a planned non-authoritative diagnostic without locating Radiance."""

    _write_scenes(config)
    actions = build_calibration_actions(config)
    payload = _base_result(config, actions, status="planned", radiance_version=None)
    payload.update(
        evaluate_observations(analytic_flux=None, flux_a=None, flux_b=None, flux_c=None)
    )
    return _publish(config, payload)


def execute_calibration(
    config: CalibrationConfig,
    *,
    runner: CommandRunner | None = None,
    rfluxmtx_command: str | Path | None = None,
    radiance_version: str | None = None,
    progress: Progress = print,
) -> Path:
    """Run four backward checks and publish diagnostic observations only."""

    _write_scenes(config)
    if rfluxmtx_command is None:
        try:
            executable = resolve_executable("rfluxmtx", cwd=Path.cwd(), label="rfluxmtx")
        except (OSError, RuntimeError, ValueError) as exc:
            raise AperturePpeCalibrationError(f"rfluxmtx is unavailable: {exc}") from exc
        version = probe_radiance_version(executable)
    else:
        executable = Path(rfluxmtx_command)
        version = radiance_version
    actions = build_calibration_actions(config, rfluxmtx_command=executable)
    payload = _base_result(config, actions, status="running", radiance_version=version)
    result_path = _publish(config, payload)
    records = cast(list[dict[str, object]], payload["actions"])
    observations: dict[CheckKey, float] = {}
    native_runner = runner or LocalRunner()

    for index, action in enumerate(actions, start=1):
        progress(f"[{index}/4] {action.label} ...")
        result = native_runner.run(action.command, stderr_path=action.stderr_path)
        records[index - 1].update(
            status="completed" if result.success else "failed",
            wall_time_s=result.wall_time_s,
            returncode=result.returncode,
        )
        progress(f"[{index}/4] {action.label}: {result.wall_time_s:.3f} s")
        if not result.success:
            payload["status"] = "failed"
            _publish(config, payload)
            raise AperturePpeCalibrationError(result.failure_message or f"{action.label} failed.")
        assert action.command.stdout_path is not None
        observations[action.key] = aperture_flux_from_rgb(
            parse_equal_rgb(action.command.stdout_path.read_text(encoding="utf-8"))
        )
        _publish(config, payload)

    payload.update(
        evaluate_observations(
            analytic_flux=observations["analytic"],
            flux_a=observations["A"],
            flux_b=observations["B"],
            flux_c=observations["C"],
        )
    )
    payload["status"] = "completed"
    return _publish(config, payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m fspm_optics.fixtures.smd.aperture_ppe_calibration",
        description=(
            "Non-authoritative backward rfluxmtx aperture-flux diagnostic; "
            "never a production normalization calibration."
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--nproc", type=int, default=1)
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        config = CalibrationConfig(arguments.output_dir, arguments.nproc)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        result = execute_calibration(config) if arguments.execute else plan_calibration(config)
    except (AperturePpeCalibrationError, OSError, ValueError) as exc:
        print(f"Aperture PPE calibration failed: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


def _base_result(
    config: CalibrationConfig,
    actions: Sequence[CalibrationAction],
    *,
    status: str,
    radiance_version: str | None,
) -> dict[str, object]:
    by_key = {action.key: action for action in actions}
    return {
        "schema_id": "fspm-optics.backward-rfluxmtx-aperture-diagnostic",
        "schema_version": 2,
        "status": status,
        "authority_scope": {
            "classification": "diagnostic_only",
            "production_normalization_authority": False,
            "current_authority_method": (
                "deterministic_forward_rtrace_integrated_flux"
            ),
            "backward_candidate_may_update_constants_or_identities": False,
        },
        "physical_model": {
            "optical_stack_id": PROPOSED_FIXTURE_OPTICAL_STACK_ID,
            "emitter": {
                "shape": "single macro-scale square Lambertian emitter",
                "side_m": EMITTER_SIDE_M,
                "area_m2": EMITTER_AREA_M2,
                "z_m": EMITTER_Z_M,
                "normal": [0.0, 0.0, -1.0],
                "internal_hemispherical_photon_flux_umol_per_s": 1.0,
                "equal_rgb_radiance_umol_per_s_m2_sr": unit_emitter_radiance(),
            },
            "pmma": {
                "side_m": PMMA_SIDE_M,
                "thickness_m": PMMA_TOP_Z_M - PMMA_BOTTOM_Z_M,
                "z_m": [PMMA_BOTTOM_Z_M, PMMA_TOP_Z_M],
                "refractive_index": PMMA_IOR,
                "radiance_dielectric_arguments": [1.0, 1.0, 1.0, 1.49, 0.0],
                "absorption_fit_used": False,
            },
            "emitter_to_pmma_internal_surface_m": EMITTER_Z_M - PMMA_TOP_Z_M,
            "ptfe": {
                "model": "infinitesimally thin ideal diffuse interior boundary",
                "rgb_reflectance": [PTFE_REFLECTANCE] * 3,
                "radiance_plastic_arguments": [0.90, 0.90, 0.90, 0.0, 0.0],
                "physical_thickness_m_metadata": PTFE_PHYSICAL_THICKNESS_M,
            },
            "opaque_black_bezel": {
                "outer_side_m": BEZEL_OUTER_SIDE_M,
                "radiance_plastic_arguments": [0.0, 0.0, 0.0, 0.0, 0.0],
            },
            "completed_aperture": {
                "side_m": APERTURE_SIDE_M,
                "area_m2": APERTURE_AREA_M2,
                "plane_z_m": 0.0,
                "measurement_z_m": -EPSILON_M,
            },
            "outward_transport_direction": [0.0, 0.0, -1.0],
            "transmission_definition": (
                "diagnostic backward matrix estimate: completed-aperture "
                "receiver flux / 1.0 umol/s internal flux"
            ),
        },
        "radiance_version": radiance_version,
        "nproc": config.nproc,
        "relative_difference_definition": "abs(current - previous) / abs(previous)",
        "exact_options": {key: list(by_key[key].options) for key in ("A", "B", "C")},
        "actions": [
            {
                "name": action.key,
                "description": action.label,
                "argv": list(action.command.argv),
                "stdout": _relative(action.command.stdout_path, config.output_directory),
                "stderr": _relative(action.stderr_path, config.output_directory),
                "status": "planned" if status == "planned" else "pending",
                "wall_time_s": None,
                "returncode": None,
            }
            for action in actions
        ],
        "target_completed_aperture_ppe_umol_per_j": TARGET_COMPLETED_APERTURE_PPE_UMOL_PER_J,
        "t_fixture_candidate": None,
        "required_internal_ppe_umol_per_j": None,
        "current_forward_flux_production_authority_reference": {
            "optical_stack_id": PROPOSED_FIXTURE_OPTICAL_STACK_ID,
            "t_fixture": ACCEPTED_FIXTURE_TRANSMISSION,
            "completed_aperture_ppe_umol_per_j": (
                COMPLETED_APERTURE_PPE_UMOL_PER_J
            ),
            "internal_source_ppe_umol_per_j": INTERNAL_SOURCE_PPE_UMOL_PER_J,
        },
    }


def _write_scenes(config: CalibrationConfig) -> None:
    (config.output_directory / "scenes").mkdir(parents=True, exist_ok=True)
    (config.output_directory / "raw").mkdir(parents=True, exist_ok=True)
    for name, text in calibration_scene_texts().items():
        atomic_write_text(config.output_directory / "scenes" / name, text)


def _publish(config: CalibrationConfig, payload: object) -> Path:
    path = config.output_directory / RESULT_FILENAME
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return path


def _relative_difference(previous: float | None, current: float | None) -> float | None:
    if previous is None or current is None or previous == 0.0:
        return None
    if not math.isfinite(previous) or not math.isfinite(current):
        return None
    return abs(current - previous) / abs(previous)


def _relative(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _aperture_sender_text() -> str:
    half = APERTURE_SIDE_M / 2.0
    return (
        "# Completed aperture sender immediately below z=0; normal -Z\n"
        "#@rfluxmtx h=u u=Y\n"
        "void glow calibration_aperture_sender_basis\n0\n0\n4 1 1 1 0\n\n"
        + _polygon(
            "calibration_aperture_sender_basis",
            "calibration_aperture_sender",
            ((-half, -half, -EPSILON_M), (-half, half, -EPSILON_M),
             (half, half, -EPSILON_M), (half, -half, -EPSILON_M)),
        )
    )


def _emitter_receiver_text() -> str:
    half = EMITTER_SIDE_M / 2.0
    value = _number(unit_emitter_radiance())
    return (
        "# 1.0 umol/s internal Lambertian emitter; normal -Z\n"
        "#@rfluxmtx h=u u=Y\n"
        "void light calibration_unit_flux_emitter\n0\n0\n"
        f"3 {value} {value} {value}\n\n"
        + _polygon(
            "calibration_unit_flux_emitter",
            "calibration_internal_emitter_receiver",
            ((-half, -half, EMITTER_Z_M), (-half, half, EMITTER_Z_M),
             (half, half, EMITTER_Z_M), (half, -half, EMITTER_Z_M)),
        )
    )


def _analytic_receiver_text() -> str:
    value = _number(1.0 / (math.pi * APERTURE_AREA_M2))
    return (
        "# Uniform h=u reference: pi * aperture area * C = 1\n"
        "#@rfluxmtx h=u u=Y\n"
        "void glow calibration_uniform_hemisphere_glow\n"
        f"0\n0\n4 {value} {value} {value} 0\n\n"
        "calibration_uniform_hemisphere_glow source calibration_uniform_hemisphere\n"
        "0\n0\n4 0 0 1 180\n"
    )


def _system_text() -> str:
    return proposed_fixture_stack_radiance_text(
        0,
        0.0,
        0.0,
        0.0,
        name_prefix="calibration",
    )


def _polygon(
    modifier: str,
    name: str,
    vertices: Sequence[tuple[float, float, float]],
) -> str:
    return polygon_radiance_text(modifier, name, vertices)


def _number(value: int | float) -> str:
    return format(float(value), ".15g")


if __name__ == "__main__":
    raise SystemExit(main())
