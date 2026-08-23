"""Retired backward-matrix COB characterization helpers.

The immutable v1 audit imports the source/receiver text helpers below.  The
former executable workflow is retained only for historical readability and
cannot publish current normalization authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Sequence

from fspm_optics.fixtures.smd.angular_characterization import (
    angular_sample_angles,
    far_field_azimuth_quadrature,
    format_far_field_receivers,
    profile_from_azimuth_samples,
)
from fspm_optics.fixtures.smd.aperture_ppe_calibration import (
    aperture_flux_from_rgb,
    calibration_scene_texts,
    check_options,
    parse_equal_rgb,
)
from fspm_optics.fixtures.smd.optical_stack import (
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

from .source import (
    COB_ANGULAR_DAT_FILENAME,
    COB_ANGULAR_MODIFIER_NAME,
    COB_IES_SHA256,
    COB_LES_AREA_M2,
    COB_LES_DIAMETER_M,
    COB_NORMALIZATION_POLICY,
    load_cob_angular_law,
)

SCHEMA_ID = "fspm-optics.proposed-cob-completed-aperture-characterization"
SCHEMA_VERSION = 1
COMPLETED_PPE_UMOL_PER_J = 2.6
BARE_PROFILE_RELATIVE_TOLERANCE = 0.01
PPF_CLOSURE_RELATIVE_TOLERANCE = 0.005
EPSILON_M = 1.0e-6


class CobCharacterizationError(RuntimeError):
    """The retired backward-matrix COB workflow was requested."""


def execute_characterization(
    output_directory: str | Path,
    *,
    nthreads: int = 1,
    quality: str = "quality",
) -> Path:
    """Fail closed; deterministic forward rtrace is the production authority."""

    if nthreads != 1:
        raise CobCharacterizationError(
            "retired COB characterization requires nthreads=1 so its historical "
            "sampling identity remains deterministic."
        )
    raise CobCharacterizationError(
        "retired backward near-field flux-matrix characterization cannot execute "
        "or publish production authority; use the frozen deterministic forward "
        "source-response authority."
    )

    # Historical v1 implementation retained below as readable provenance for
    # the immutable audit.  It is unreachable from the public entry point.
    root = Path(output_directory).expanduser().resolve()
    if root.exists():
        raise CobCharacterizationError(
            f"characterization output already exists: {root}"
        )
    root.mkdir(parents=True)
    law = load_cob_angular_law()
    atomic_write_text(root / COB_ANGULAR_DAT_FILENAME, law.dat_text)
    source_text = unit_internal_source_text()
    atomic_write_text(root / "unit-internal-source.rad", source_text)
    atomic_write_text(
        root / "fixture.rad",
        proposed_fixture_materials_radiance_text()
        + "\n"
        + proposed_fixture_stack_radiance_text(
            0, 0.0, 0.0, 0.0, name_prefix="cob_characterization"
        ),
    )

    installation = discover_radiance_installation()
    flux_tool_name = "r" + "fluxmtx"
    flux_tool = resolve_executable(
        flux_tool_name, cwd=Path.cwd(), label=flux_tool_name
    )
    runner = LocalRunner()
    bare = _execute_bare_validation(
        root / "bare",
        source_text=source_text,
        law=law,
        runner=runner,
        oconv=installation.oconv.path,
        rtrace=installation.rtrace.path,
    )
    completed_profile = _execute_completed_profile(
        root / "completed-profile",
        source_text=source_text,
        nthreads=nthreads,
        quality=quality,
        runner=runner,
        oconv=installation.oconv.path,
        rtrace=installation.rtrace.path,
    )
    transmissions = {
        key: _execute_flux(
            root / f"flux-{key}",
            internal_flux_umol_s=1.0,
            option_key=key,
            nthreads=nthreads,
            runner=runner,
            flux_tool=flux_tool,
        )
        for key in ("A", "B", "C")
    }
    transmission = transmissions["C"]
    if abs(transmissions["C"] - transmissions["B"]) / transmissions["B"] > 0.005:
        raise CobCharacterizationError(
            "COB completed-aperture transmission B/C convergence exceeds 0.5%."
        )
    internal_ppe = COMPLETED_PPE_UMOL_PER_J / transmission
    measured_ppf = _execute_flux(
        root / "reference-watt",
        internal_flux_umol_s=internal_ppe,
        option_key="C",
        nthreads=nthreads,
        runner=runner,
        flux_tool=flux_tool,
    )
    ppf_relative_error = abs(measured_ppf - COMPLETED_PPE_UMOL_PER_J) / (
        COMPLETED_PPE_UMOL_PER_J
    )
    if ppf_relative_error > PPF_CLOSURE_RELATIVE_TOLERANCE:
        raise CobCharacterizationError(
            "COB completed-aperture PPF closure exceeds 0.5%."
        )
    payload: dict[str, object] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "completed",
        "classification": "cob_source_shape_surrogate",
        "scientific_claim": (
            "controlled emitter-geometry experiment; not measured Citizen PPE "
            "or a completed hybrid COB/red-ring product"
        ),
        "authenticated_input": {
            "ies_sha256": COB_IES_SHA256,
            "normalized_identity_sha256": law.normalized_identity_sha256,
            "normalization_policy": COB_NORMALIZATION_POLICY,
            "dat_sha256": law.dat_sha256,
        },
        "les_geometry": {
            "shape": "centered_circle",
            "diameter_m": COB_LES_DIAMETER_M,
            "area_m2": COB_LES_AREA_M2,
            "z_offset_from_completed_aperture_m": EMITTER_Z_M,
            "normal": [0.0, 0.0, -1.0],
            "dimensions_inferred_from_ies": False,
        },
        "bare_far_field": bare,
        "completed_aperture": {
            "transmission_definition": (
                "integrated completed-aperture PPF / integrated internal LES PPF"
            ),
            "transmission_observations": transmissions,
            "accepted_transmission": transmission,
            "internal_radiance_ppe_umol_per_j": internal_ppe,
            "reference_input_w": 1.0,
            "measured_integrated_ppf_umol_s": measured_ppf,
            "ppe_umol_per_j": measured_ppf,
            "target_ppe_umol_per_j": COMPLETED_PPE_UMOL_PER_J,
            "ppf_closure_relative_error": ppf_relative_error,
            "ppf_closure_tolerance": PPF_CLOSURE_RELATIVE_TOLERANCE,
            "angular_profile": completed_profile,
        },
        "normalization": {
            "boundary": "internal LES before optical-stack and room transport",
            "post_scale_traced_ppfd": False,
        },
        "radiance": {
            "oconv": installation.oconv.to_dict(),
            "rtrace": installation.rtrace.to_dict(),
            "flux_matrix_tool": {
                "executable": flux_tool_name,
                "path": str(flux_tool),
                "version_text": probe_radiance_version(flux_tool),
            },
            "quality": quality,
            "nthreads": nthreads,
        },
    }
    payload["characterization_identity_sha256"] = _identity(payload)
    path = root / "proposed-cob-characterization.json"
    atomic_write_text(path, _json_text(payload))
    return path


def unit_internal_source_text() -> str:
    """Return the authenticated, exactly unit-flux internal COB source.

    This is intentionally shared by the frozen characterization and
    diagnostic-only matched-source audits so those audits cannot introduce an
    alternate COB emitter model.
    """

    value = 1.0 / COB_LES_AREA_M2
    radius = COB_LES_DIAMETER_M / 2.0
    return (
        "# Exactly 1 umol/s normalized internal COB LES\n"
        f"void brightdata {COB_ANGULAR_MODIFIER_NAME}\n"
        f"5 flatcorr {COB_ANGULAR_DAT_FILENAME} source.cal src_phi4 src_theta\n"
        "0\n0\n\n"
        f"{COB_ANGULAR_MODIFIER_NAME} light cob_characterization_unit_flux\n"
        "0\n0\n"
        f"3 {value:.15g} {value:.15g} {value:.15g}\n\n"
        "cob_characterization_unit_flux ring cob_characterization_les\n"
        "0\n0\n"
        f"8 0 0 {EMITTER_Z_M:.15g} 0 0 -1 0 {radius:.15g}\n"
    )


def flux_receiver_text(
    *, internal_flux_umol_s: float
) -> str:
    """Return the COB flux-matrix receiver at a declared internal PPF."""

    value = float(internal_flux_umol_s)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError("internal_flux_umol_s must be finite and positive.")
    unit_value = 1.0 / COB_LES_AREA_M2
    scaled_value = value / COB_LES_AREA_M2
    source = unit_internal_source_text().replace(
        "# Exactly 1 umol/s normalized internal COB LES\n",
        f"# Exactly {value:.15g} umol/s internal COB LES\n",
        1,
    )
    source = source.replace(
        f"3 {unit_value:.15g} {unit_value:.15g} {unit_value:.15g}",
        f"3 {scaled_value:.15g} {scaled_value:.15g} {scaled_value:.15g}",
        1,
    )
    marker = (
        f"{COB_ANGULAR_MODIFIER_NAME} light "
        "cob_characterization_unit_flux"
    )
    if marker not in source:
        raise CobCharacterizationError(
            "authenticated COB characterization light material is missing."
        )
    return source.replace(
        marker, "#@r" + "fluxmtx h=u u=Y\n" + marker, 1
    )


def _execute_bare_validation(
    directory: Path,
    *,
    source_text: str,
    law: object,
    runner: LocalRunner,
    oconv: Path,
    rtrace: Path,
) -> dict[str, object]:
    directory.mkdir()
    source_path = directory / "source.rad"
    dat_path = directory / COB_ANGULAR_DAT_FILENAME
    receiver_path = directory / "far-field.pts"
    octree_path = directory / "bare.oct"
    rgb_path = directory / "far-field.rgb"
    atomic_write_text(source_path, source_text)
    atomic_write_text(dat_path, law.dat_text)
    distance = 20.0
    rows: list[str] = []
    keys: list[tuple[int, int]] = []
    for phi_deg in range(91):
        phi = math.radians(phi_deg)
        for theta_deg in range(90):
            theta = math.radians(theta_deg)
            x = distance * math.sin(theta) * math.cos(phi)
            y = distance * math.sin(theta) * math.sin(phi)
            z = EMITTER_Z_M - distance * math.cos(theta)
            nx, ny, nz = (
                -x / distance,
                -y / distance,
                (EMITTER_Z_M - z) / distance,
            )
            rows.append(
                f"{x:.12g} {y:.12g} {z:.12g} "
                f"{nx:.12g} {ny:.12g} {nz:.12g}\n"
            )
            keys.append((phi_deg, theta_deg))
    atomic_write_text(receiver_path, "".join(rows))
    _run(
        runner,
        CommandSpec(
            argv=(str(oconv), "-f", str(source_path)),
            stdout_path=octree_path,
            stdout_mode="binary",
            cwd=directory,
            label="cob-bare-oconv",
        ),
        directory / "oconv.stderr",
    )
    _run(
        runner,
        CommandSpec(
            argv=(str(rtrace), "-h", "-I+", "-ab", "0", str(octree_path)),
            stdin_path=receiver_path,
            stdout_path=rgb_path,
            cwd=directory,
            label="cob-bare-rtrace",
        ),
        directory / "rtrace.stderr",
    )
    values = parse_rtrace_rgb_rows(rgb_path.read_text(encoding="utf-8"))
    reference = values[0]
    relative_errors: list[float] = []
    absolute_errors: list[float] = []
    for (phi, theta), measured in zip(keys, values, strict=True):
        expected = law.normalized_intensity_by_horizontal_plane[phi][theta]
        expected /= law.normalized_intensity_by_horizontal_plane[0][0]
        observed = measured / reference
        absolute_errors.append(abs(observed - expected))
        if expected >= 0.01:
            relative_errors.append(abs(observed - expected) / expected)
    maximum_relative_error = max(relative_errors)
    if maximum_relative_error > BARE_PROFILE_RELATIVE_TOLERANCE:
        raise CobCharacterizationError(
            "bare COB Radiance far field misses normalized IES by more than 1%."
        )
    return {
        "grid": "C0-C90 and gamma0-gamma89 at 1 degree",
        "distance_m": distance,
        "maximum_relative_error_where_normalized_intensity_at_least_0p01": (
            maximum_relative_error
        ),
        "maximum_absolute_normalized_error": max(absolute_errors),
        "relative_error_tolerance": BARE_PROFILE_RELATIVE_TOLERANCE,
        "pass": True,
    }


def _execute_completed_profile(
    directory: Path,
    *,
    source_text: str,
    nthreads: int,
    quality: str,
    runner: LocalRunner,
    oconv: Path,
    rtrace: Path,
) -> dict[str, object]:
    directory.mkdir()
    source_path = directory / "source.rad"
    fixture_path = directory / "fixture.rad"
    dat_path = directory / COB_ANGULAR_DAT_FILENAME
    receiver_path = directory / "far-field.pts"
    octree_path = directory / "completed.oct"
    rgb_path = directory / "far-field.rgb"
    atomic_write_text(source_path, source_text)
    atomic_write_text(
        fixture_path,
        proposed_fixture_materials_radiance_text()
        + "\n"
        + proposed_fixture_stack_radiance_text(
            0, 0.0, 0.0, 0.0, name_prefix="cob_characterization"
        ),
    )
    atomic_write_text(dat_path, load_cob_angular_law().dat_text)
    angles = angular_sample_angles(0.5)
    receivers, keys = format_far_field_receivers(
        angles_deg=angles,
        azimuth_count=19,
        distance_m=20.0,
        azimuth_quadrature="square_symmetry_endpoint_trapezoid",
    )
    _, weights = far_field_azimuth_quadrature(
        19, method="square_symmetry_endpoint_trapezoid"
    )
    sample_weights = tuple(
        weight for angle in angles if angle < 90.0 for weight in weights
    )
    atomic_write_text(receiver_path, receivers)
    _run(
        runner,
        CommandSpec(
            argv=(str(oconv), "-f", str(fixture_path), str(source_path)),
            stdout_path=octree_path,
            stdout_mode="binary",
            cwd=directory,
            label="cob-completed-oconv",
        ),
        directory / "oconv.stderr",
    )
    _run(
        runner,
        CommandSpec(
            argv=(
                str(rtrace),
                "-h",
                "-I+",
                "-u-",
                "-n",
                str(nthreads),
                *radiance_options(quality),
                str(octree_path),
            ),
            stdin_path=receiver_path,
            stdout_path=rgb_path,
            cwd=directory,
            label="cob-completed-rtrace",
        ),
        directory / "rtrace.stderr",
    )
    values = parse_rtrace_rgb_rows(rgb_path.read_text(encoding="utf-8"))
    profile = profile_from_azimuth_samples(
        angles_deg=angles,
        sample_keys=keys,
        irradiance_values=values,
        normalize_to_optical_axis=True,
        sample_weights=sample_weights,
    )
    return profile.to_dict()


def _execute_flux(
    directory: Path,
    *,
    internal_flux_umol_s: float,
    option_key: str,
    nthreads: int,
    runner: LocalRunner,
    flux_tool: Path,
) -> float:
    directory.mkdir()
    scenes = calibration_scene_texts()
    for name in ("materials.rad", "system.rad", "aperture_sender.rad"):
        atomic_write_text(directory / name, scenes[name])
    law = load_cob_angular_law()
    atomic_write_text(directory / COB_ANGULAR_DAT_FILENAME, law.dat_text)
    receiver_path = directory / "internal-cob-receiver.rad"
    atomic_write_text(
        receiver_path,
        flux_receiver_text(internal_flux_umol_s=internal_flux_umol_s),
    )
    output_path = directory / "aperture-flux.rgb"
    _run(
        runner,
        CommandSpec(
            argv=(
                str(flux_tool),
                *check_options(option_key, nthreads),
                str(directory / "aperture_sender.rad"),
                str(receiver_path),
                str(directory / "materials.rad"),
                str(directory / "system.rad"),
            ),
            stdout_path=output_path,
            cwd=directory,
            label=f"cob-flux-{option_key}",
        ),
        directory / "flux-matrix.stderr",
    )
    return aperture_flux_from_rgb(
        parse_equal_rgb(output_path.read_text(encoding="utf-8"))
    )


def _run(runner: LocalRunner, command: CommandSpec, stderr: Path) -> None:
    result = runner.run(command, stderr_path=stderr)
    if not result.success:
        raise CobCharacterizationError(
            result.failure_message or f"{command.label} failed."
        )


def _identity(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def _json_text(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--nthreads", type=int, default=1)
    parser.add_argument("--quality", default="quality")
    arguments = parser.parse_args(argv)
    try:
        path = execute_characterization(
            arguments.output_dir,
            nthreads=arguments.nthreads,
            quality=arguments.quality,
        )
    except (CobCharacterizationError, OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
