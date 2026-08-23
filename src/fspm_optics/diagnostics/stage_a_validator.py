"""Read-only flux-closure validation for one persisted Stage A run.

The validator deliberately sits outside the production execution path.  It
authenticates the published run, reconstructs its achieved scalar field from
the closest persisted native authority, and writes reports only to a separate
caller-selected output directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

from fspm_optics.application.domain import (
    CONVENTIONAL_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    ProposedControlMode,
)
from fspm_optics.application.proposed import validate_success_artifacts
from fspm_optics.application.target_control import (
    TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S,
    scale_global_output_field_float64,
)
from fspm_optics.transport.basis.artifacts import load_basis_workspace_artifacts
from fspm_optics.transport.basis.atomic import atomic_write_text

SCHEMA_ID = "fspm-optics.stage-a-flux-closure"
SCHEMA_VERSION = 2
REPORT_JSON_NAME = "stage-a-flux-closure.json"
REPORT_MARKDOWN_NAME = "stage-a-flux-closure.md"
FIELD_REL_TOLERANCE = 5e-12
FIELD_ABS_TOLERANCE = 1e-9
SCALAR_REL_TOLERANCE = 5e-12
SCALAR_ABS_TOLERANCE = 1e-8


class StageAValidationError(RuntimeError):
    """A persisted Stage A run failed authentication or reconstruction."""


def coordinate_quadrature(
    x_m: Sequence[float] | NDArray[np.float64],
    y_m: Sequence[float] | NDArray[np.float64],
    values: Sequence[float] | NDArray[np.float64],
) -> dict[str, object]:
    """Integrate a complete rectilinear point field using inferred cell widths."""

    x = _finite_vector("x_m", x_m)
    y = _finite_vector("y_m", y_m)
    scalar = _finite_vector("values", values, nonnegative=True)
    if not (x.size == y.size == scalar.size):
        raise StageAValidationError("coordinate and scalar vectors differ in length.")
    unique_x = np.unique(x)
    unique_y = np.unique(y)
    if unique_x.size < 2 or unique_y.size < 2:
        raise StageAValidationError(
            "coordinate-derived 2D quadrature requires at least two x and y values."
        )
    if unique_x.size * unique_y.size != scalar.size:
        raise StageAValidationError(
            "PPFD coordinates do not form one complete rectilinear grid."
        )
    x_index = {float(value): index for index, value in enumerate(unique_x)}
    y_index = {float(value): index for index, value in enumerate(unique_y)}
    grid = np.full((unique_y.size, unique_x.size), np.nan, dtype=np.float64)
    for x_value, y_value, scalar_value in zip(x, y, scalar, strict=True):
        row = y_index[float(y_value)]
        column = x_index[float(x_value)]
        if math.isfinite(float(grid[row, column])):
            raise StageAValidationError("PPFD coordinates contain a duplicate point.")
        grid[row, column] = scalar_value
    if not np.all(np.isfinite(grid)):
        raise StageAValidationError("PPFD coordinate grid contains a missing point.")
    x_widths, x_bounds = _coordinate_widths(unique_x)
    y_widths, y_bounds = _coordinate_widths(unique_y)
    weights = y_widths[:, np.newaxis] * x_widths[np.newaxis, :]
    integrated = float(np.sum(grid * weights, dtype=np.float64))
    area = float(np.sum(weights, dtype=np.float64))
    return {
        "method": (
            "rectilinear midpoint control volumes with exterior half-spacing "
            "extrapolation"
        ),
        "grid_shape_y_x": [int(unique_y.size), int(unique_x.size)],
        "x_bounds_m": [float(x_bounds[0]), float(x_bounds[1])],
        "y_bounds_m": [float(y_bounds[0]), float(y_bounds[1])],
        "area_m2": area,
        "integrated_receiver_flux_umol_s": integrated,
        "area_weighted_mean_ppfd_umol_m2_s": integrated / area,
    }


def reconstruct_proposed_field(
    *,
    basis_matrix: Sequence[Sequence[float]] | NDArray[np.float64],
    reference_watts_per_module: float,
    watts_by_control_zone: Sequence[float],
    dimming_factor: float,
) -> NDArray[np.float64]:
    """Apply the persisted per-module basis and schedule exactly once."""

    matrix = np.asarray(basis_matrix, dtype=np.float64)
    if matrix.ndim != 2 or not np.all(np.isfinite(matrix)) or np.any(matrix < 0.0):
        raise StageAValidationError("basis matrix must be finite and non-negative.")
    reference = _positive("reference_watts_per_module", reference_watts_per_module)
    dimming = _closed_unit_interval("dimming_factor", dimming_factor)
    coefficients = _finite_vector(
        "watts_by_control_zone",
        watts_by_control_zone,
        nonnegative=True,
    )
    if matrix.shape[1] != coefficients.size:
        raise StageAValidationError(
            "basis column count does not match the control-zone schedule."
        )
    return np.asarray(
        (matrix / reference) @ coefficients * dimming,
        dtype=np.float64,
    )


def validate_stage_a_run(
    run_directory: str | Path,
    output_directory: str | Path,
) -> dict[str, object]:
    """Authenticate and independently close one persisted Stage A result."""

    run_path = _safe_existing_directory(run_directory, "run directory")
    output_path = _safe_output_directory(output_directory, run_path)
    manifest = _read_json(run_path / "manifest.json")
    run_id = _required_string(manifest, "run_id")
    system_id = _required_string(manifest, "system_id")
    request = _required_mapping(manifest, "request")
    try:
        validate_success_artifacts(
            run_path,
            expected_run_id=run_id,
            expected_system_id=system_id,
            expected_request=request,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise StageAValidationError(
            f"public Stage A artifact authentication failed: {exc}"
        ) from exc

    metrics = _read_json(run_path / "metrics.json")
    schedule = _read_json(run_path / "full_output_schedule.json")
    operating = _read_json(run_path / "operating-point.json")
    target_control = _read_json(run_path / "target_control.json")
    x, y, z, published_ppfd = _read_ppfd_csv(run_path / "ppfd.csv")

    if system_id == PROPOSED_SYSTEM_ID:
        native = _validate_proposed(
            run_path=run_path,
            manifest=manifest,
            metrics=metrics,
            schedule=schedule,
            operating=operating,
            published_ppfd=published_ppfd,
        )
    elif system_id == CONVENTIONAL_SYSTEM_ID:
        native = _validate_conventional(
            run_path=run_path,
            manifest=manifest,
            metrics=metrics,
            schedule=schedule,
            operating=operating,
            coordinates=(x, y, z),
            published_ppfd=published_ppfd,
        )
    else:
        raise StageAValidationError(
            "the Stage A flux validator supports Proposed and Conventional runs."
        )

    statistics_payload = _statistics(published_ppfd)
    _check_published_metrics(metrics, statistics_payload)
    lighting_target = _validate_lighting_target_policy(
        request=request,
        target_control=target_control,
        coordinates=(x, y, z),
        published_ppfd=published_ppfd,
    )
    quadrature = coordinate_quadrature(x, y, published_ppfd)
    effective_power = float(native["effective_power_w"])
    emitted_ppf = float(native["emitted_ppf_umol_s"])
    closure = {
        "receiver_to_emitted_ppf_ratio": (
            float(quadrature["integrated_receiver_flux_umol_s"]) / emitted_ppf
        ),
        "interpretation": (
            "diagnostic only: the production upward-facing reference plane is "
            "a nonterminal downwelling irradiance tally above reflective geometry"
        ),
        "terminal_conservation_bound_applies": False,
    }
    payload: dict[str, object] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "read_only_input": True,
        "run": {
            "directory": str(run_path),
            "run_id": run_id,
            "system_id": system_id,
            "request_sha256": _hash_json(request),
            "proposed_control_mode": (
                _required_mapping(manifest, "proposed_control").get("mode")
                if system_id == PROPOSED_SYSTEM_ID
                else None
            ),
        },
        "authentication": {
            "public_success_artifacts": "pass",
            "native_authority": native["authority"],
            "native_hashes": native["hashes"],
        },
        "reconstruction": native["reconstruction"],
        "electrical_and_source": {
            "effective_power_w": effective_power,
            "emitted_ppf_umol_s": emitted_ppf,
            "effective_ppe_umol_per_j": emitted_ppf / effective_power,
        },
        "sample_statistics": statistics_payload,
        "lighting_target_policy": lighting_target,
        "coordinate_quadrature": quadrature,
        "flux_closure": closure,
    }
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / REPORT_JSON_NAME
    markdown_path = output_path / REPORT_MARKDOWN_NAME
    _reject_unsafe_output_file(json_path)
    _reject_unsafe_output_file(markdown_path)
    atomic_write_text(
        json_path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(markdown_path, format_markdown_report(payload))
    return payload


def _validate_lighting_target_policy(
    *,
    request: Mapping[str, object],
    target_control: Mapping[str, object],
    coordinates: tuple[
        NDArray[np.float64],
        NDArray[np.float64],
        NDArray[np.float64],
    ],
    published_ppfd: NDArray[np.float64],
) -> dict[str, object]:
    """Authenticate sampled-cap semantics against the raw published field."""

    mode = request.get("lighting_target_mode", "mean_target")
    if mode not in {"mean_target", "target_capped"}:
        raise StageAValidationError("request lighting-target mode is invalid.")
    if target_control.get("lighting_target_mode", "mean_target") != mode:
        raise StageAValidationError(
            "target-control lighting mode disagrees with the request."
        )
    requested = _number(request, "target_ppfd_umol_m2_s")
    _assert_close(
        "target-control requested value",
        requested,
        _number(target_control, "requested_target_ppfd_umol_m2_s"),
    )
    if mode == "mean_target":
        return {
            "mode": mode,
            "requested_value_semantics": "requested_mean_stage_a_ppfd",
            "sampled_cap_applicable": False,
        }

    maximum = float(np.max(published_ppfd))
    _assert_close(
        "target-control achieved maximum",
        maximum,
        _number(target_control, "achieved_maximum_ppfd_umol_m2_s"),
    )
    tolerance = _number(
        target_control, "compliance_tolerance_umol_m2_s"
    )
    if tolerance != TARGET_CAP_COMPLIANCE_TOLERANCE_UMOL_M2_S:
        raise StageAValidationError(
            "sampled-cap compliance tolerance is not the declared authority."
        )
    if maximum > requested + tolerance:
        raise StageAValidationError(
            "published Stage A maximum exceeds the requested sampled cap."
        )
    if target_control.get("cap_compliant") is not True:
        raise StageAValidationError(
            "sampled-cap compliance declaration is contradictory."
        )
    limiting_index = int(np.argmax(published_ppfd))
    limiting = _required_mapping(target_control, "limiting_sample")
    expected = {
        "index": limiting_index,
        "x_m": float(coordinates[0][limiting_index]),
        "y_m": float(coordinates[1][limiting_index]),
        "z_m": float(coordinates[2][limiting_index]),
        "achieved_ppfd_umol_m2_s": maximum,
    }
    if dict(limiting) != expected:
        raise StageAValidationError(
            "sampled-cap limiting-sample provenance is invalid."
        )
    full_maximum = _number(
        target_control, "full_output_maximum_ppfd_umol_m2_s"
    )
    expected_binding = full_maximum > requested
    if target_control.get("cap_binding") is not expected_binding:
        raise StageAValidationError(
            "sampled-cap binding declaration is contradictory."
        )
    raw_factor = requested / full_maximum
    _assert_close(
        "sampled-cap raw factor",
        raw_factor,
        _number(target_control, "raw_factor"),
    )
    return {
        "mode": mode,
        "requested_value_semantics": "requested_sampled_stage_a_maximum_ppfd",
        "sampled_cap_applicable": True,
        "requested_cap_umol_m2_s": requested,
        "achieved_maximum_umol_m2_s": maximum,
        "compliance_tolerance_umol_m2_s": tolerance,
        "cap_binding": expected_binding,
        "cap_compliant": True,
        "limiting_sample": expected,
    }


def format_markdown_report(payload: Mapping[str, object]) -> str:
    """Format the machine-readable closure payload as a compact audit report."""

    run = _required_mapping(payload, "run")
    authentication = _required_mapping(payload, "authentication")
    reconstruction = _required_mapping(payload, "reconstruction")
    source = _required_mapping(payload, "electrical_and_source")
    stats = _required_mapping(payload, "sample_statistics")
    quadrature = _required_mapping(payload, "coordinate_quadrature")
    closure = _required_mapping(payload, "flux_closure")
    return "\n".join(
        (
            "# Stage A flux-closure validation",
            "",
            f"- Status: `{payload['status']}`",
            f"- Run ID: `{run['run_id']}`",
            f"- System: `{run['system_id']}`",
            f"- Public artifact authentication: `{authentication['public_success_artifacts']}`",
            f"- Native authority: `{authentication['native_authority']}`",
            f"- Field reconstruction maximum absolute error: "
            f"`{float(reconstruction['maximum_absolute_error_umol_m2_s']):.12g}` µmol/m²/s",
            f"- Effective electrical power: `{float(source['effective_power_w']):.12g}` W",
            f"- Emitted PPF: `{float(source['emitted_ppf_umol_s']):.12g}` µmol/s",
            f"- Arithmetic mean PPFD: "
            f"`{float(stats['mean_ppfd_umol_m2_s']):.12g}` µmol/m²/s",
            f"- Population CV: `{float(stats['cv_percent']):.12g}`%",
            f"- Coordinate-derived integration area: "
            f"`{float(quadrature['area_m2']):.12g}` m²",
            f"- Integrated downwelling receiver tally: "
            f"`{float(quadrature['integrated_receiver_flux_umol_s']):.12g}` µmol/s",
            f"- Receiver/emitted ratio: "
            f"`{float(closure['receiver_to_emitted_ppf_ratio']):.12g}`",
            "",
            "The ratio is diagnostic, not a terminal conservation test: the "
            "production receiver is a conceptual upward-facing irradiance plane "
            "above reflective room geometry.",
            "",
        )
    )


def _validate_proposed(
    *,
    run_path: Path,
    manifest: Mapping[str, object],
    metrics: Mapping[str, object],
    schedule: Mapping[str, object],
    operating: Mapping[str, object],
    published_ppfd: NDArray[np.float64],
) -> dict[str, object]:
    proposed_control = _required_mapping(manifest, "proposed_control")
    control_mode = _required_string(proposed_control, "mode")
    if control_mode == ProposedControlMode.UNIFORM_MODULE_DIMMING.value:
        return _validate_proposed_uniform(
            run_path=run_path,
            manifest=manifest,
            metrics=metrics,
            schedule=schedule,
            operating=operating,
            published_ppfd=published_ppfd,
        )
    if control_mode != ProposedControlMode.BASIS_MATRIX_OPTIMIZED.value:
        raise StageAValidationError("Proposed control mode is unsupported.")
    basis_root = run_path / "basis"
    _reject_symlink_chain(run_path, basis_root)
    for relative in (
        "basis_manifest.json",
        "basis_matrix.npy",
        "basis_matrix.execution.json",
    ):
        _safe_relative_file(basis_root, relative)
    try:
        basis = load_basis_workspace_artifacts(basis_root)
    except (OSError, ValueError) as exc:
        raise StageAValidationError(
            f"Proposed basis authentication failed: {exc}"
        ) from exc
    hashes = _authenticate_basis_inputs(basis_root, basis.manifest)
    engine_provenance = _required_mapping(manifest, "engine_provenance")
    published_basis_manifest = _required_mapping(
        engine_provenance,
        "basis_manifest",
    )
    if published_basis_manifest != basis.manifest.to_dict():
        raise StageAValidationError(
            "persisted basis manifest disagrees with the authenticated public "
            "engine provenance."
        )
    published_matrix_hash = _required_string(
        engine_provenance,
        "matrix_sha256",
    )
    if hashes["basis_matrix_sha256"] != published_matrix_hash:
        raise StageAValidationError(
            "basis matrix hash disagrees with the authenticated public "
            "engine provenance."
        )
    layout = _required_mapping(manifest, "layout")
    modules = _required_sequence(layout, "modules")
    watts_by_zone = _number_sequence(schedule, "watts_by_control_zone")
    watts_by_module = _number_sequence(schedule, "watts_by_module")
    if (
        len(modules) != basis.manifest.layout_module_count
        or len(watts_by_module) != len(modules)
        or len(watts_by_zone) != basis.manifest.control_zone_count
    ):
        raise StageAValidationError(
            "Proposed layout, basis, and schedule multiplicities disagree."
        )
    reconstructed_modules: list[float] = []
    for index, module in enumerate(modules):
        if not isinstance(module, Mapping):
            raise StageAValidationError("Proposed module record must be an object.")
        if module.get("module_index") != index:
            raise StageAValidationError("Proposed modules are not index ordered.")
        zone = module.get("control_zone_index")
        if isinstance(zone, bool) or not isinstance(zone, int):
            raise StageAValidationError("Proposed module zone index is invalid.")
        if zone < 0 or zone >= len(watts_by_zone):
            raise StageAValidationError("Proposed module zone index is out of range.")
        reconstructed_modules.append(watts_by_zone[zone])
    if not np.allclose(
        np.asarray(watts_by_module),
        np.asarray(reconstructed_modules),
        rtol=0.0,
        atol=1e-12,
    ):
        raise StageAValidationError(
            "per-module powers are not the single expansion of zone coefficients."
        )
    dimming = _number(operating, "dimming_factor")
    reconstructed = reconstruct_proposed_field(
        basis_matrix=basis.matrix,
        reference_watts_per_module=basis.reference_watts_per_module,
        watts_by_control_zone=watts_by_zone,
        dimming_factor=dimming,
    )
    error = _field_error(reconstructed, published_ppfd)
    effective_power = math.fsum(watts_by_module) * dimming
    power = _required_mapping(operating, "power")
    _assert_close("Proposed effective power", effective_power, _number(power, "effective_w"))
    ppf = _required_mapping(operating, "ppf")
    completed_ppe = _number(ppf, "completed_aperture_fixture_ppe_umol_per_j")
    emitted_ppf = effective_power * completed_ppe
    _assert_close("Proposed emitted PPF", emitted_ppf, _number(ppf, "emitted_umol_s"))
    counts = _required_mapping(metrics, "counts")
    if (
        _integer(counts, "modules") != len(modules)
        or _integer(counts, "control_zones") != len(watts_by_zone)
    ):
        raise StageAValidationError("published Proposed counts disagree with the basis.")
    return {
        "authority": "authenticated basis_matrix.npy / reference W per module",
        "hashes": hashes,
        "effective_power_w": effective_power,
        "emitted_ppf_umol_s": emitted_ppf,
        "reconstruction": {
            **error,
            "formula": (
                "(basis_matrix / reference_watts_per_module) "
                "@ watts_by_control_zone * global_dimming_factor"
            ),
            "reference_watts_per_module": basis.reference_watts_per_module,
            "module_count": len(modules),
            "control_zone_count": len(watts_by_zone),
        },
    }


def _validate_proposed_uniform(
    *,
    run_path: Path,
    manifest: Mapping[str, object],
    metrics: Mapping[str, object],
    schedule: Mapping[str, object],
    operating: Mapping[str, object],
    published_ppfd: NDArray[np.float64],
) -> dict[str, object]:
    uniform_root = run_path / "uniform-stage-a"
    _reject_symlink_chain(run_path, uniform_root)
    paths = {
        name: _safe_relative_file(uniform_root, relative)
        for name, relative in {
            "manifest": "uniform_stage_a_manifest.json",
            "execution": "uniform_stage_a_execution.json",
            "reference_field": "uniform_complete_reference_ppfd.npy",
            "room": "room.rad",
            "sensors": "sensors.pts",
            "source": "uniform_complete_source.rad",
        }.items()
    }
    native_manifest = _read_json(paths["manifest"])
    execution = _read_json(paths["execution"])
    engine = _required_mapping(manifest, "engine_provenance")
    identity = _required_string(native_manifest, "identity_sha256")
    if (
        native_manifest.get("control_mode")
        != ProposedControlMode.UNIFORM_MODULE_DIMMING.value
        or native_manifest.get("basis_matrix_solver_enabled") is not False
        or native_manifest.get("complete_scene_trace_count") != 1
        or native_manifest.get("basis_column_count") != 0
        or execution.get("identity_sha256") != identity
        or execution.get("control_mode")
        != ProposedControlMode.UNIFORM_MODULE_DIMMING.value
        or execution.get("basis_matrix_solver_enabled") is not False
        or execution.get("complete_scene_trace_count") != 1
        or execution.get("basis_column_count") != 0
        or engine.get("uniform_stage_a_identity_sha256") != identity
        or engine.get("basis_matrix_solver_enabled") is not False
        or engine.get("complete_scene_trace_count") != 1
        or engine.get("basis_column_count") != 0
    ):
        raise StageAValidationError(
            "uniform Proposed transport identity or one-pass policy is invalid."
        )
    try:
        reference_field = np.asarray(
            np.load(paths["reference_field"], allow_pickle=False),
            dtype=np.float64,
        )
    except (OSError, ValueError) as exc:
        raise StageAValidationError(
            f"uniform Proposed reference field could not be loaded: {exc}"
        ) from exc
    if reference_field.ndim != 1 or not np.all(np.isfinite(reference_field)):
        raise StageAValidationError("uniform Proposed reference field is invalid.")
    field_hash = _sha256_file(paths["reference_field"])
    if (
        execution.get("field_sha256") != field_hash
        or engine.get("uniform_reference_field_sha256") != field_hash
    ):
        raise StageAValidationError(
            "uniform Proposed reference field hash authentication failed."
        )
    watts_by_module = _number_sequence(schedule, "watts_by_module")
    watts_by_zone = _number_sequence(schedule, "watts_by_control_zone")
    reference_watts = _number(schedule, "uniform_reference_watts_per_module")
    if (
        not watts_by_module
        or not watts_by_zone
        or any(
            not math.isclose(value, reference_watts, abs_tol=1e-12)
            for value in (*watts_by_module, *watts_by_zone)
        )
    ):
        raise StageAValidationError(
            "uniform Proposed reference schedule is not equal per module."
        )
    dimming = _closed_unit_interval(
        "dimming_factor", _number(operating, "dimming_factor")
    )
    reconstructed = reference_field * dimming
    error = _field_error(reconstructed, published_ppfd)
    effective_power = len(watts_by_module) * reference_watts * dimming
    power = _required_mapping(operating, "power")
    _assert_close(
        "uniform Proposed effective power",
        effective_power,
        _number(power, "effective_w"),
    )
    ppf = _required_mapping(operating, "ppf")
    completed_ppe = _number(
        ppf, "completed_aperture_fixture_ppe_umol_per_j"
    )
    emitted_ppf = effective_power * completed_ppe
    _assert_close(
        "uniform Proposed emitted PPF",
        emitted_ppf,
        _number(ppf, "emitted_umol_s"),
    )
    counts = _required_mapping(metrics, "counts")
    if _integer(counts, "modules") != len(watts_by_module):
        raise StageAValidationError(
            "published uniform Proposed module count is inconsistent."
        )
    hashes = {
        "uniform_stage_a_identity_sha256": identity,
        "uniform_reference_field_sha256": field_hash,
        "room_sha256": _sha256_file(paths["room"]),
        "sensor_sha256": _sha256_file(paths["sensors"]),
        "source_sha256": _sha256_file(paths["source"]),
    }
    if any(execution.get(name) != value for name, value in hashes.items() if name in execution):
        raise StageAValidationError(
            "uniform Proposed complete-scene input authentication failed."
        )
    return {
        "authority": (
            "authenticated complete-scene uniform reference field at equal "
            "reference watts per module"
        ),
        "hashes": hashes,
        "effective_power_w": effective_power,
        "emitted_ppf_umol_s": emitted_ppf,
        "reconstruction": {
            **error,
            "formula": (
                "complete_uniform_reference_field * global_dimming_factor"
            ),
            "reference_watts_per_module": reference_watts,
            "module_count": len(watts_by_module),
            "control_zone_count": len(watts_by_zone),
            "complete_scene_trace_count": 1,
            "basis_column_count": 0,
        },
    }


def _validate_conventional(
    *,
    run_path: Path,
    manifest: Mapping[str, object],
    metrics: Mapping[str, object],
    schedule: Mapping[str, object],
    operating: Mapping[str, object],
    coordinates: tuple[
        NDArray[np.float64],
        NDArray[np.float64],
        NDArray[np.float64],
    ],
    published_ppfd: NDArray[np.float64],
) -> dict[str, object]:
    artifacts = _required_mapping(manifest, "artifacts")
    summary_relative = _required_string(artifacts, "conventional_final_summary")
    summary_path = _safe_relative_file(run_path, summary_relative)
    summary = _read_json(summary_path)
    if (
        summary.get("schema_id")
        != "fspm-optics.conventional-stage-a-scaled-result"
        or summary.get("schema_version") != 1
        or summary.get("policy_id")
        != "conventional_full_output_float64_global_scaling_v1"
    ):
        raise StageAValidationError(
            "Conventional final summary does not declare the one-trace "
            "Float64 scaling contract."
        )
    declared_identity = _required_string(
        summary, "derivation_identity_sha256"
    )
    identity_preimage = dict(summary)
    del identity_preimage["derivation_identity_sha256"]
    if _hash_json(identity_preimage) != declared_identity:
        raise StageAValidationError(
            "Conventional scaled-result derivation identity is invalid."
        )
    command_counts = _required_mapping(
        summary, "native_stage_a_command_counts"
    )
    if command_counts != {
        "ies2rad_conversion": 1,
        "fixture_shape_compilation": 1,
        "scalar_scene_compilation": 1,
        "baseline_rtrace": 1,
    } or summary.get("native_commands_after_full_output_trace") != []:
        raise StageAValidationError(
            "Conventional native Stage A command-count contract is invalid."
        )

    authority = _required_mapping(summary, "authority")
    dimming = _number(operating, "global_dimming_factor")
    global_output_fraction = _number(authority, "global_output_fraction")
    if global_output_fraction != dimming:
        raise StageAValidationError(
            "Conventional scaling authority disagrees with the operating point."
        )
    full_record = _required_mapping(summary, "full_output_transport")
    scaled_record = _required_mapping(summary, "scaled_result")
    full_summary_path = _safe_relative_file(
        run_path,
        _required_string(full_record, "scalar_transport_summary"),
    )
    if _sha256_file(full_summary_path) != _required_string(
        full_record, "scalar_transport_summary_sha256"
    ):
        raise StageAValidationError(
            "Conventional full-output scalar summary hash is invalid."
        )
    command_summary_path = _safe_relative_file(
        run_path,
        _required_string(full_record, "command_provenance_summary"),
    )
    if _sha256_file(command_summary_path) != _required_string(
        full_record, "command_provenance_summary_sha256"
    ):
        raise StageAValidationError(
            "Conventional full-output command provenance hash is invalid."
        )
    command_payload = _read_json(command_summary_path)
    native_commands = _required_sequence(command_payload, "commands")
    if [
        command.get("label") if isinstance(command, Mapping) else None
        for command in native_commands
    ] != [
        "convert_conventional_unit_downward_flux_ies",
        "compile_conventional_scalar_scene",
        "baseline_scalar_ppfd_rtrace",
    ]:
        raise StageAValidationError(
            "Conventional full-output native command provenance is invalid."
        )
    full_npz_path = _safe_relative_file(
        run_path, _required_string(full_record, "ppfd_values_npz")
    )
    scaled_npz_path = _safe_relative_file(
        run_path, _required_string(scaled_record, "ppfd_values_npz")
    )
    observed_full_npz_hash = _sha256_file(full_npz_path)
    observed_scaled_npz_hash = _sha256_file(scaled_npz_path)
    if observed_full_npz_hash != _required_string(
        full_record, "ppfd_values_npz_sha256"
    ) or observed_scaled_npz_hash != _required_string(
        scaled_record, "ppfd_values_npz_sha256"
    ):
        raise StageAValidationError(
            "Conventional Float64 PPFD NPZ authentication failed."
        )
    full_native = _load_conventional_npz(full_npz_path)
    scaled_native = _load_conventional_npz(scaled_npz_path)
    for label, expected, full_coordinate, scaled_coordinate in zip(
        ("x", "y", "z"),
        coordinates,
        full_native[:3],
        scaled_native[:3],
        strict=True,
    ):
        if (
            expected.shape != full_coordinate.shape
            or not np.array_equal(expected, full_coordinate)
            or not np.array_equal(full_coordinate, scaled_coordinate)
        ):
            raise StageAValidationError(
                f"Conventional public, full-output, and scaled {label} "
                "coordinates disagree."
            )
    reconstructed = np.asarray(
        scale_global_output_field_float64(
            full_native[3], global_output_fraction
        ),
        dtype=np.float64,
    )
    scaling_error = _field_error(reconstructed, scaled_native[3])
    error = _field_error(scaled_native[3], published_ppfd)
    fixture_count = _integer(schedule, "fixture_count")
    rated_watts = _number(schedule, "rated_fixture_power_w")
    fixture_ppf = _number(schedule, "full_output_fixture_ppf_umol_s")
    effective_power = fixture_count * rated_watts * dimming
    emitted_ppf = fixture_count * fixture_ppf * dimming
    power = _required_mapping(operating, "power")
    ppf = _required_mapping(operating, "ppf")
    _assert_close(
        "Conventional effective power",
        effective_power,
        _number(power, "effective_w"),
    )
    _assert_close(
        "Conventional emitted PPF",
        emitted_ppf,
        _number(ppf, "emitted_umol_s"),
        abs_tol=1e-6,
    )
    counts = _required_mapping(metrics, "counts")
    if _integer(counts, "fixtures") != fixture_count:
        raise StageAValidationError(
            "published Conventional fixture count disagrees with its schedule."
        )
    return {
        "authority": (
            "hashed full-output native Conventional Float64 NPZ scaled by "
            "the authenticated global output fraction"
        ),
        "hashes": {
            "scaled_result_summary_sha256": _sha256_file(summary_path),
            "scaled_result_derivation_identity_sha256": declared_identity,
            "full_output_scalar_transport_summary_sha256": (
                _sha256_file(full_summary_path)
            ),
            "full_output_command_provenance_summary_sha256": (
                _sha256_file(command_summary_path)
            ),
            "full_output_ppfd_values_npz_sha256": observed_full_npz_hash,
            "scaled_ppfd_values_npz_sha256": observed_scaled_npz_hash,
        },
        "effective_power_w": effective_power,
        "emitted_ppf_umol_s": emitted_ppf,
        "reconstruction": {
            **error,
            "scaled_artifact_reconstruction": scaling_error,
            "formula": (
                "float64(full_output_ppfd[i]) * "
                "float64(global_output_fraction)"
            ),
            "global_output_fraction": global_output_fraction,
            "native_complete_scene_trace_count": 1,
            "fixture_count": fixture_count,
        },
    }


def _load_conventional_npz(
    path: Path,
) -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
]:
    try:
        with np.load(path, allow_pickle=False) as archive:
            expected_names = {"x_m", "y_m", "z_m", "ppfd_umol_m2_s"}
            if set(archive.files) != expected_names:
                raise StageAValidationError(
                    "Conventional Float64 NPZ inventory is invalid."
                )
            arrays = tuple(
                np.asarray(archive[name], dtype=np.float64)
                for name in ("x_m", "y_m", "z_m", "ppfd_umol_m2_s")
            )
    except (OSError, ValueError) as exc:
        raise StageAValidationError(
            f"Conventional Float64 NPZ could not be loaded: {exc}"
        ) from exc
    if len({array.shape for array in arrays}) != 1:
        raise StageAValidationError(
            "Conventional Float64 NPZ arrays differ in shape."
        )
    for name, array in zip(
        ("x_m", "y_m", "z_m", "ppfd_umol_m2_s"),
        arrays,
        strict=True,
    ):
        _finite_vector(
            name,
            array,
            nonnegative=name == "ppfd_umol_m2_s",
        )
    return arrays  # type: ignore[return-value]


def _authenticate_basis_inputs(basis_root: Path, manifest: Any) -> dict[str, object]:
    records = (
        ("room_text_sha256", basis_root / "room.rad", manifest.room_text_sha256),
        ("sensor_text_sha256", basis_root / "sensors.pts", manifest.sensor_text_sha256),
    )
    hashes: dict[str, object] = {}
    for role, path, expected in records:
        if not isinstance(expected, str) or not expected:
            raise StageAValidationError(f"basis manifest is missing {role}.")
        observed = _sha256_file(_safe_relative_file(basis_root, path.name))
        if observed != expected:
            raise StageAValidationError(f"basis {role} authentication failed.")
        hashes[role] = observed
    emitter_hashes = tuple(manifest.emitter_text_sha256_by_control_zone)
    if len(emitter_hashes) != manifest.control_zone_count:
        raise StageAValidationError("basis manifest emitter hash inventory is incomplete.")
    observed_emitters: list[str] = []
    emitter_texts: list[str] = []
    for index, expected in enumerate(emitter_hashes):
        emitter = _safe_relative_file(
            basis_root,
            f"basis_control_zone_{index:03d}.rad",
        )
        observed = _sha256_file(emitter)
        if observed != expected:
            raise StageAValidationError(
                f"basis emitter hash authentication failed for zone {index}."
            )
        observed_emitters.append(observed)
        emitter_texts.append(emitter.read_text(encoding="utf-8"))
    hashes["emitter_text_sha256_by_control_zone"] = observed_emitters
    combined_emitter_hash = hashlib.sha256(
        "\0".join(emitter_texts).encode("utf-8")
    ).hexdigest()
    if combined_emitter_hash != manifest.emitter_source_sha256:
        raise StageAValidationError(
            "basis aggregate emitter-source hash authentication failed."
        )
    hashes["emitter_source_sha256"] = combined_emitter_hash
    matrix_path = _safe_relative_file(basis_root, "basis_matrix.npy")
    hashes["basis_matrix_sha256"] = _sha256_file(matrix_path)
    return hashes


def _field_error(
    reconstructed: NDArray[np.float64],
    published: NDArray[np.float64],
) -> dict[str, object]:
    if reconstructed.shape != published.shape:
        raise StageAValidationError(
            "native reconstruction and published PPFD vectors differ in shape."
        )
    absolute = np.abs(reconstructed - published)
    maximum_absolute = float(absolute.max(initial=0.0))
    denominator = np.maximum(np.abs(published), FIELD_ABS_TOLERANCE)
    maximum_relative = float(np.max(absolute / denominator, initial=0.0))
    if not np.allclose(
        reconstructed,
        published,
        rtol=FIELD_REL_TOLERANCE,
        atol=FIELD_ABS_TOLERANCE,
    ):
        raise StageAValidationError(
            "native Stage A field does not reconstruct published ppfd.csv: "
            f"maximum absolute error={maximum_absolute:.12g}."
        )
    return {
        "sample_count": int(published.size),
        "maximum_absolute_error_umol_m2_s": maximum_absolute,
        "maximum_relative_error": maximum_relative,
        "relative_tolerance": FIELD_REL_TOLERANCE,
        "absolute_tolerance_umol_m2_s": FIELD_ABS_TOLERANCE,
    }


def _statistics(values: NDArray[np.float64]) -> dict[str, object]:
    vector = _finite_vector("PPFD", values, nonnegative=True)
    mean = float(np.mean(vector, dtype=np.float64))
    standard_deviation = float(np.std(vector, ddof=0, dtype=np.float64))
    return {
        "sample_count": int(vector.size),
        "mean_ppfd_umol_m2_s": mean,
        "population_standard_deviation_ppfd_umol_m2_s": standard_deviation,
        "cv_percent": 100.0 * standard_deviation / mean,
        "minimum_ppfd_umol_m2_s": float(np.min(vector)),
        "maximum_ppfd_umol_m2_s": float(np.max(vector)),
    }


def _check_published_metrics(
    metrics: Mapping[str, object],
    calculated: Mapping[str, object],
) -> None:
    pairs = (
        ("achieved_mean_ppfd_umol_m2_s", "mean_ppfd_umol_m2_s"),
        (
            "standard_deviation_ppfd_umol_m2_s",
            "population_standard_deviation_ppfd_umol_m2_s",
        ),
        ("cv_percent", "cv_percent"),
        ("minimum_ppfd_umol_m2_s", "minimum_ppfd_umol_m2_s"),
        ("maximum_ppfd_umol_m2_s", "maximum_ppfd_umol_m2_s"),
    )
    for published_name, calculated_name in pairs:
        _assert_close(
            f"published metric {published_name}",
            float(calculated[calculated_name]),
            _number(metrics, published_name),
        )


def _coordinate_widths(
    coordinates: NDArray[np.float64],
) -> tuple[NDArray[np.float64], tuple[float, float]]:
    deltas = np.diff(coordinates)
    if np.any(deltas <= 0.0):
        raise StageAValidationError("quadrature coordinates must increase strictly.")
    boundaries = np.empty(coordinates.size + 1, dtype=np.float64)
    boundaries[1:-1] = (coordinates[:-1] + coordinates[1:]) * 0.5
    boundaries[0] = coordinates[0] - deltas[0] * 0.5
    boundaries[-1] = coordinates[-1] + deltas[-1] * 0.5
    return np.diff(boundaries), (float(boundaries[0]), float(boundaries[-1]))


def _read_ppfd_csv(
    path: Path,
) -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
]:
    safe = _safe_relative_file(path.parent, path.name)
    try:
        with safe.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != ["x_m", "y_m", "z_m", "ppfd_umol_m2_s"]:
                raise StageAValidationError("ppfd.csv has an incompatible header.")
            rows = tuple(reader)
        arrays = tuple(
            np.asarray([float(row[name]) for row in rows], dtype=np.float64)
            for name in ("x_m", "y_m", "z_m", "ppfd_umol_m2_s")
        )
    except (KeyError, OSError, ValueError) as exc:
        raise StageAValidationError(f"ppfd.csv could not be parsed: {exc}") from exc
    if not rows:
        raise StageAValidationError("ppfd.csv contains no samples.")
    for name, array in zip(
        ("x_m", "y_m", "z_m", "ppfd_umol_m2_s"),
        arrays,
        strict=True,
    ):
        _finite_vector(name, array, nonnegative=name == "ppfd_umol_m2_s")
    return arrays  # type: ignore[return-value]


def _safe_existing_directory(value: str | Path, label: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise StageAValidationError(f"{label} must not be a symbolic link.")
    resolved = raw.resolve()
    if not resolved.is_dir():
        raise StageAValidationError(f"{label} does not exist: {resolved}")
    return resolved


def _safe_output_directory(value: str | Path, run_path: Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise StageAValidationError("output directory must not be a symbolic link.")
    resolved = raw.resolve()
    if resolved == run_path or resolved.is_relative_to(run_path):
        raise StageAValidationError(
            "output directory must be outside the supplied Stage A run."
        )
    if resolved.exists() and not resolved.is_dir():
        raise StageAValidationError("output path exists and is not a directory.")
    return resolved


def _safe_relative_file(root: Path, relative: str | Path) -> Path:
    candidate = root / relative
    _reject_symlink_chain(root, candidate)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise StageAValidationError(f"required local artifact is missing: {relative}")
    return resolved


def _reject_symlink_chain(root: Path, candidate: Path) -> None:
    resolved_root = root.resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise StageAValidationError("artifact path escapes its declared root.") from exc
    current = resolved_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise StageAValidationError(f"artifact path contains a symlink: {candidate}")


def _reject_unsafe_output_file(path: Path) -> None:
    if path.is_symlink():
        raise StageAValidationError(f"output report must not be a symlink: {path}")


def _read_json(path: Path) -> dict[str, object]:
    safe = _safe_relative_file(path.parent, path.name)
    try:
        payload = json.loads(safe.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageAValidationError(f"invalid JSON artifact {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StageAValidationError(f"JSON artifact root must be an object: {path.name}")
    return payload


def _required_mapping(
    payload: Mapping[str, object],
    name: str,
) -> Mapping[str, object]:
    value = payload.get(name)
    if not isinstance(value, Mapping):
        raise StageAValidationError(f"{name} must be a JSON object.")
    return value


def _required_sequence(
    payload: Mapping[str, object],
    name: str,
) -> Sequence[object]:
    value = payload.get(name)
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise StageAValidationError(f"{name} must be a JSON array.")
    return value


def _required_string(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise StageAValidationError(f"{name} must be a non-empty string.")
    return value


def _number(payload: Mapping[str, object], name: str) -> float:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StageAValidationError(f"{name} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise StageAValidationError(f"{name} must be finite.")
    return number


def _integer(payload: Mapping[str, object], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StageAValidationError(f"{name} must be an integer.")
    return value


def _number_sequence(payload: Mapping[str, object], name: str) -> tuple[float, ...]:
    values = _required_sequence(payload, name)
    result: list[float] = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise StageAValidationError(f"{name}[{index}] must be numeric.")
        number = float(value)
        if not math.isfinite(number) or number < 0.0:
            raise StageAValidationError(
                f"{name}[{index}] must be finite and non-negative."
            )
        result.append(number)
    return tuple(result)


def _finite_vector(
    name: str,
    values: Sequence[float] | NDArray[np.float64],
    *,
    nonnegative: bool = False,
) -> NDArray[np.float64]:
    try:
        vector = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise StageAValidationError(f"{name} must be numeric.") from exc
    if vector.ndim != 1 or vector.size == 0 or not np.all(np.isfinite(vector)):
        raise StageAValidationError(f"{name} must be a nonempty finite vector.")
    if nonnegative and np.any(vector < 0.0):
        raise StageAValidationError(f"{name} must be non-negative.")
    return vector


def _positive(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise StageAValidationError(f"{name} must be finite and positive.")
    return number


def _closed_unit_interval(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise StageAValidationError(f"{name} must lie in [0, 1].")
    return number


def _assert_close(
    label: str,
    calculated: float,
    published: float,
    *,
    rel_tol: float = SCALAR_REL_TOLERANCE,
    abs_tol: float = SCALAR_ABS_TOLERANCE,
) -> None:
    if not math.isclose(calculated, published, rel_tol=rel_tol, abs_tol=abs_tol):
        raise StageAValidationError(
            f"{label} mismatch: calculated={calculated:.17g}, "
            f"published={published:.17g}."
        )


def _hash_json(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m fspm_optics.diagnostics.stage_a_validator",
        description=(
            "Read-only authentication, reconstruction, and coordinate-quadrature "
            "closure for one persisted Proposed or Conventional Stage A run."
        ),
    )
    parser.add_argument("run_directory", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Separate directory for JSON and Markdown reports.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        payload = validate_stage_a_run(
            arguments.run_directory,
            arguments.output_dir,
        )
    except (OSError, StageAValidationError, ValueError) as exc:
        print(f"Stage A validation failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": payload["status"],
                "json": str(arguments.output_dir.resolve() / REPORT_JSON_NAME),
                "markdown": str(
                    arguments.output_dir.resolve() / REPORT_MARKDOWN_NAME
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "REPORT_JSON_NAME",
    "REPORT_MARKDOWN_NAME",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "StageAValidationError",
    "build_parser",
    "coordinate_quadrature",
    "format_markdown_report",
    "main",
    "reconstruct_proposed_field",
    "validate_stage_a_run",
]
