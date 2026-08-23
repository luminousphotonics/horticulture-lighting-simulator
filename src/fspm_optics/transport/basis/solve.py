"""Solve persisted isolated SMD bases into explicit optimized watt schedules."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from fspm_optics.fixtures.smd.positions import (
    SmdLayout,
    generate_proposed_led_layout,
)
from fspm_optics.fixtures.smd.power_schedule import (
    DEFAULT_OPTIMIZED_MAX_WATTS_PER_MODULE,
    ModulePowerSchedule,
    build_optimized_module_schedule,
)
from fspm_optics.geometry.room import FEET_TO_METERS
from fspm_optics.optimization.uniformity import UniformityResult, solve_uniformity

from .artifacts import (
    BasisWorkspaceArtifacts,
    load_basis_workspace_artifacts,
    save_json_artifact,
)
from .atomic import atomic_save_npy
from .source_compatibility import require_current_proposed_source_manifest

FloatArray = NDArray[np.float64]


class BasisSolveError(RuntimeError):
    """A valid persisted basis could not produce a safe optimized schedule."""


@dataclass(frozen=True, slots=True)
class BasisSolutionArtifactPaths:
    solution_path: Path
    schedule_path: Path
    predicted_ppfd_path: Path
    metrics_path: Path


@dataclass(frozen=True, slots=True)
class BasisSolveResult:
    uniformity: UniformityResult
    schedule: ModulePowerSchedule
    predicted_ppfd: FloatArray
    workspace_artifacts: BasisWorkspaceArtifacts
    artifact_paths: BasisSolutionArtifactPaths


def solve_basis_workspace(
    workspace: str | Path,
    *,
    target_ppfd: float,
    min_watts: float = 0.0,
    max_watts: float = DEFAULT_OPTIMIZED_MAX_WATTS_PER_MODULE,
    reference_watts_per_module: float | None = None,
) -> BasisSolveResult:
    """Load, normalize, solve, and persist one SMD basis workspace.

    Persisted columns represent their manifest reference power. Dividing each
    column by that reference power produces PPFD per watt/module, so solver
    coefficients and the optimized schedule are both expressed in watts per
    module for each control zone.
    """

    lower = _finite_watt_bound("min_watts", min_watts)
    upper = _finite_watt_bound("max_watts", max_watts)
    if lower > upper:
        raise ValueError("min_watts must not exceed max_watts.")
    artifacts = load_basis_workspace_artifacts(
        workspace,
        reference_watts_per_module=reference_watts_per_module,
    )
    manifest = artifacts.manifest
    require_current_proposed_source_manifest(manifest)
    normalized_basis = np.asarray(
        artifacts.matrix / artifacts.reference_watts_per_module,
        dtype=float,
    )
    bounds_lower = (lower,) * manifest.control_zone_count
    bounds_upper = (upper,) * manifest.control_zone_count
    uniformity = solve_uniformity(
        normalized_basis,
        target_ppfd,
        bounds_lower,
        bounds_upper,
    )
    if not uniformity.success:
        raise BasisSolveError(
            uniformity.failure_reason or "basis uniformity solve failed."
        )

    layout = generate_proposed_led_layout(
        manifest.room_length_m / FEET_TO_METERS,
        manifest.room_width_m / FEET_TO_METERS,
        proposed_layout_mode=manifest.proposed_layout_mode,
        proposed_ring_mode=manifest.proposed_ring_mode,
    )
    if layout.fixture_policy_id != manifest.fixture_policy_id:
        raise BasisSolveError(
            "reconstructed layout fixture policy does not match the basis manifest."
        )
    if layout.control_zone_count != manifest.control_zone_count:
        raise BasisSolveError(
            "reconstructed layout control-zone count does not match the basis "
            f"manifest: expected {manifest.control_zone_count}, got "
            f"{layout.control_zone_count}."
        )
    if len(layout.modules) != manifest.layout_module_count:
        raise BasisSolveError(
            "reconstructed layout module count does not match the basis manifest: "
            f"expected {manifest.layout_module_count}, got {len(layout.modules)}."
        )
    schedule = build_optimized_module_schedule(layout, uniformity.coefficients)
    predicted = np.asarray(uniformity.predicted_ppfd, dtype=float)
    if predicted.shape != (manifest.sensor_count,):
        raise BasisSolveError(
            "solver predicted PPFD length does not match sensor_count: "
            f"expected {manifest.sensor_count}, got {predicted.shape}."
        )

    root = Path(workspace).expanduser().resolve()
    paths = BasisSolutionArtifactPaths(
        solution_path=root / "basis_solution.json",
        schedule_path=root / "optimized_module_schedule.json",
        predicted_ppfd_path=root / "predicted_ppfd.npy",
        metrics_path=root / "predicted_ppfd_metrics.json",
    )
    atomic_save_npy(paths.predicted_ppfd_path, predicted)
    save_json_artifact(
        paths.solution_path,
        _solution_payload(
            artifacts,
            uniformity,
            target_ppfd=float(target_ppfd),
            min_watts=lower,
            max_watts=upper,
        ),
    )
    save_json_artifact(paths.schedule_path, _schedule_payload(layout, schedule))
    save_json_artifact(
        paths.metrics_path,
        _metrics_payload(uniformity, target_ppfd=float(target_ppfd)),
    )
    return BasisSolveResult(
        uniformity=uniformity,
        schedule=schedule,
        predicted_ppfd=predicted,
        workspace_artifacts=artifacts,
        artifact_paths=paths,
    )


def _solution_payload(
    artifacts: BasisWorkspaceArtifacts,
    result: UniformityResult,
    *,
    target_ppfd: float,
    min_watts: float,
    max_watts: float,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "artifact_type": "fspm_optics_smd_basis_solution",
        "success": True,
        "target_ppfd": target_ppfd,
        "sensor_count": result.n_sensors,
        "control_zone_count": result.n_control_zones,
        "coefficient_units": "watts_per_module_by_control_zone",
        "watts_by_control_zone": list(result.coefficients),
        "bounds_watts_per_module": {"minimum": min_watts, "maximum": max_watts},
        "basis_reference_watts_per_module": artifacts.reference_watts_per_module,
        "basis_reference_watts_source": artifacts.reference_watts_source,
        "basis_column_normalization": "divide_by_reference_watts_per_module",
        "layout_composition": {
            "proposed_layout_mode": (
                artifacts.manifest.proposed_layout_mode.value
            ),
            "proposed_ring_mode": (
                artifacts.manifest.proposed_ring_mode.value
            ),
            "module_pattern_id": artifacts.manifest.module_pattern_id,
            "fixture_policy_id": artifacts.manifest.fixture_policy_id,
        },
        "inputs": {
            "basis_matrix": str(artifacts.matrix_path),
            "basis_manifest": str(artifacts.manifest_path),
            "basis_execution_summary": str(artifacts.execution_summary_path),
        },
    }


def _schedule_payload(
    layout: SmdLayout,
    schedule: ModulePowerSchedule,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "artifact_type": "fspm_optics_optimized_smd_module_schedule",
        "proposed_layout_mode": layout.proposed_layout_mode.value,
        "proposed_ring_mode": layout.proposed_ring_mode.value,
        "module_pattern_id": layout.module_pattern_id,
        "fixture_policy_id": layout.fixture_policy_id,
        "schedule_source": schedule.schedule_source,
        "control_zone_count": schedule.control_zone_count,
        "module_count": schedule.module_count,
        "min_watts": schedule.min_watts,
        "max_watts": schedule.max_watts,
        "total_watts": schedule.total_watts,
        "watts_by_control_zone": list(schedule.watts_by_control_zone),
        "control_zones": [
            {
                "control_zone_index": index,
                "watts_per_module": watts,
            }
            for index, watts in enumerate(schedule.watts_by_control_zone)
        ],
        "modules": [
            {
                "module_index": module.module_index,
                "control_zone_index": module.control_zone_index,
                "watts": watts,
            }
            for module, watts in zip(
                layout.modules,
                schedule.watts_by_module,
                strict=True,
            )
        ],
    }


def _metrics_payload(
    result: UniformityResult,
    *,
    target_ppfd: float,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_type": "fspm_optics_predicted_ppfd_metrics",
        "target_ppfd": target_ppfd,
        "sensor_count": result.n_sensors,
        "mean_ppfd": result.mean,
        "standard_deviation_ppfd": result.standard_deviation,
        "cv_percent": result.cv_percent,
        "rmse_ppfd": result.rmse,
    }


def _finite_watt_bound(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a finite non-negative number.")
    watts = float(value)
    if not math.isfinite(watts) or watts < 0.0:
        raise ValueError(f"{name} must be a finite non-negative number.")
    return watts
