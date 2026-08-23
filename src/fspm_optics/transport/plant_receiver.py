"""Scalar-only Rex receiver transport planning and local smoke execution.

This module validates geometry-to-receiver transport plumbing. Its opaque,
neutral leaf material is deliberately not a biological Rex optical model.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from numpy.typing import NDArray

from fspm_optics.plants.generator import generate_rex_butterhead_plant
from fspm_optics.plants.models import PlantMesh
from fspm_optics.plants.radiance_export import export_plant_mesh_to_radiance
from fspm_optics.plants.rex import RexPlantConfig
from fspm_optics.geometry.room import PRODUCTION_ROOM_MODEL_IDENTITY_SHA256
from fspm_optics.radiance.commands import (
    CommandSpec,
    build_oconv_command,
    build_plant_receiver_rtrace_command,
)
from fspm_optics.radiance.materials import (
    REX_LEAF_SCALAR_TRANSPORT_PLACEHOLDER,
    RadiancePlasticMaterial,
    validate_scalar_par_surface_material,
)
from fspm_optics.radiance.options import replace_radiance_option_value
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.radiance.versioning import (
    RadianceInstallation,
    discover_radiance_installation,
)
from fspm_optics.receivers.samples import (
    MeshPatchReceiverSample,
    build_two_sided_patch_receivers,
    receiver_sample_input_text,
)
from fspm_optics.transport.basis.artifacts import (
    load_basis_workspace_artifacts,
    save_json_artifact,
)
from fspm_optics.transport.basis.atomic import atomic_save_npy, atomic_write_text
from fspm_optics.transport.basis.composite import plan_composite_validation
from fspm_optics.transport.basis.parsing import parse_basis_column
from fspm_optics.transport.basis.source_compatibility import (
    require_current_proposed_source_manifest,
)

FloatArray = NDArray[np.float64]


class RexReceiverSmokeError(RuntimeError):
    """A Rex scalar receiver smoke plan or execution failed validation."""


@dataclass(frozen=True, slots=True)
class RexReceiverSmokePaths:
    room_path: Path
    emitter_path: Path
    plant_path: Path
    receiver_path: Path
    octree_path: Path
    rgb_output_path: Path
    ambient_cache_path: Path | None
    flux_path: Path
    metrics_path: Path
    execution_path: Path


@dataclass(frozen=True, slots=True)
class RexReceiverSmokePlan:
    workspace: Path
    plant: PlantMesh
    receiver_samples: tuple[MeshPatchReceiverSample, ...]
    leaf_material: RadiancePlasticMaterial
    paths: RexReceiverSmokePaths
    oconv_command: CommandSpec
    rtrace_command: CommandSpec

    @property
    def receiver_count(self) -> int:
        return len(self.receiver_samples)


@dataclass(frozen=True, slots=True)
class RexReceiverMetrics:
    receiver_count: int
    leaf_count: int
    patch_count: int
    front_receiver_count: int
    back_receiver_count: int
    mean_incident_ppfd: float
    minimum_incident_ppfd: float
    maximum_incident_ppfd: float
    front_mean_incident_ppfd: float
    back_mean_incident_ppfd: float
    front_back_ratio: float | None
    area_weighted_mean_incident_ppfd: float

    def to_dict(self) -> dict[str, int | float | None]:
        return {
            "receiver_count": self.receiver_count,
            "leaf_count": self.leaf_count,
            "patch_count": self.patch_count,
            "front_receiver_count": self.front_receiver_count,
            "back_receiver_count": self.back_receiver_count,
            "mean_incident_ppfd": self.mean_incident_ppfd,
            "min_incident_ppfd": self.minimum_incident_ppfd,
            "max_incident_ppfd": self.maximum_incident_ppfd,
            "front_mean_incident_ppfd": self.front_mean_incident_ppfd,
            "back_mean_incident_ppfd": self.back_mean_incident_ppfd,
            "front_back_ratio": self.front_back_ratio,
            "area_weighted_mean_incident_ppfd": (
                self.area_weighted_mean_incident_ppfd
            ),
        }


@dataclass(frozen=True, slots=True)
class RexReceiverSmokeResult:
    flux: FloatArray
    metrics: RexReceiverMetrics
    plan: RexReceiverSmokePlan
    oconv_result: RunnerResult
    rtrace_result: RunnerResult
    radiance_installation: RadianceInstallation


def plan_rex_receiver_smoke(
    workspace: str | Path,
    *,
    seed: int = 1,
    normal_offset_m: float = 5e-5,
) -> RexReceiverSmokePlan:
    """Materialize deterministic Rex RAD/PTS inputs and return command specs."""

    root = Path(workspace).expanduser().resolve()
    basis = load_basis_workspace_artifacts(root)
    manifest = basis.manifest
    require_current_proposed_source_manifest(manifest)
    composite = plan_composite_validation(root)

    plant = generate_rex_butterhead_plant(RexPlantConfig(seed=seed))
    samples = build_two_sided_patch_receivers(
        plant,
        normal_offset_m=normal_offset_m,
    )
    if len(samples) != plant.receiver_count:
        raise RexReceiverSmokeError(
            "generated Rex receiver count does not match the scientific patch mesh: "
            f"expected {plant.receiver_count}, got {len(samples)}."
        )

    material = REX_LEAF_SCALAR_TRANSPORT_PLACEHOLDER
    validate_scalar_par_surface_material(material)
    plant_text = export_plant_mesh_to_radiance(
        plant,
        material_name=material.name,
        material_definition=material.to_radiance(),
    )
    receiver_text = receiver_sample_input_text(
        sample.to_dict() for sample in samples
    )
    plant_path = root / "rex_plant.rad"
    receiver_path = root / "rex_plant_receivers.pts"
    atomic_write_text(plant_path, plant_text)
    atomic_write_text(receiver_path, receiver_text)

    octree_path = root / "plant_receiver.oct"
    rgb_path = root / "plant_receiver.rgb"
    input_hash = _sha256_text(
        "\0".join(
            (
                _sha256_file(composite.paths.room_path),
                _sha256_file(composite.paths.emitter_path),
                _sha256_text(plant_text),
                _sha256_text(receiver_text),
            )
        )
    )
    ambient_path = (
        None
        if manifest.ambient_cache_policy == "disabled"
        else root / (
            f"plant_receiver_{input_hash[:16]}_"
            f"{PRODUCTION_ROOM_MODEL_IDENTITY_SHA256[:16]}.amb"
        )
    )
    radiance_options = list(manifest.radiance_options)
    if ambient_path is not None:
        radiance_options = replace_radiance_option_value(
            radiance_options,
            "-af",
            str(ambient_path),
        )

    oconv_command = build_oconv_command(
        (
            composite.paths.room_path,
            composite.paths.emitter_path,
            plant_path,
        ),
        output_octree=octree_path,
        cwd=root,
        label="compile_rex_plant_receiver",
    )
    rtrace_command = build_plant_receiver_rtrace_command(
        octree=octree_path,
        receiver_input=receiver_path,
        rgb_output=rgb_path,
        options=radiance_options,
        nthreads=manifest.nthreads,
        cwd=root,
    )
    paths = RexReceiverSmokePaths(
        room_path=composite.paths.room_path,
        emitter_path=composite.paths.emitter_path,
        plant_path=plant_path,
        receiver_path=receiver_path,
        octree_path=octree_path,
        rgb_output_path=rgb_path,
        ambient_cache_path=ambient_path,
        flux_path=root / "rex_plant_receiver_flux.npy",
        metrics_path=root / "rex_plant_receiver_metrics.json",
        execution_path=root / "rex_plant_receiver.execution.json",
    )
    return RexReceiverSmokePlan(
        workspace=root,
        plant=plant,
        receiver_samples=samples,
        leaf_material=material,
        paths=paths,
        oconv_command=oconv_command,
        rtrace_command=rtrace_command,
    )


def execute_rex_receiver_smoke(
    plan: RexReceiverSmokePlan,
    runner: LocalRunner,
    *,
    radiance_installation: RadianceInstallation | None = None,
    oconv_timeout_s: float | None = None,
    rtrace_timeout_s: float | None = None,
) -> RexReceiverSmokeResult:
    """Execute one planned local Rex scalar receiver smoke calculation."""

    installation = radiance_installation or discover_radiance_installation()
    oconv_command = _replace_executable(plan.oconv_command, installation.oconv.path)
    rtrace_command = _replace_executable(plan.rtrace_command, installation.rtrace.path)
    oconv_result = runner.run(oconv_command, timeout_s=oconv_timeout_s)
    if not oconv_result.success:
        raise RexReceiverSmokeError(
            oconv_result.failure_message or "Rex plant receiver scene compilation failed."
        )
    if not plan.paths.octree_path.is_file():
        raise RexReceiverSmokeError(
            f"Rex plant receiver compilation did not create: {plan.paths.octree_path}"
        )
    rtrace_result = runner.run(rtrace_command, timeout_s=rtrace_timeout_s)
    if not rtrace_result.success:
        raise RexReceiverSmokeError(
            rtrace_result.failure_message or "Rex scalar receiver trace failed."
        )
    if not plan.paths.rgb_output_path.is_file():
        raise RexReceiverSmokeError(
            f"Rex scalar receiver trace did not create: {plan.paths.rgb_output_path}"
        )
    try:
        flux = parse_basis_column(
            plan.paths.rgb_output_path.read_text(encoding="utf-8"),
            expected_sensor_count=plan.receiver_count,
        )
    except ValueError as exc:
        raise RexReceiverSmokeError(
            f"Rex receiver output validation failed: {exc}"
        ) from exc
    metrics = compute_rex_receiver_metrics(
        flux,
        plan.receiver_samples,
        leaf_count=len(plan.plant.leaves),
        patch_count=plan.plant.patch_count,
    )

    atomic_save_npy(plan.paths.flux_path, flux)
    save_json_artifact(
        plan.paths.metrics_path,
        {
            "schema_version": 1,
            "artifact_type": "fspm_optics_rex_scalar_receiver_metrics",
            "transport_scope": "geometry_transport_smoke_only",
            "leaf_optics_policy": "opaque_neutral_scalar_placeholder_not_rex_rta",
            **metrics.to_dict(),
        },
    )
    save_json_artifact(
        plan.paths.execution_path,
        _execution_payload(
            plan,
            metrics,
            oconv_command,
            rtrace_command,
            oconv_result,
            rtrace_result,
            installation,
        ),
    )
    return RexReceiverSmokeResult(
        flux=flux,
        metrics=metrics,
        plan=plan,
        oconv_result=oconv_result,
        rtrace_result=rtrace_result,
        radiance_installation=installation,
    )


def compute_rex_receiver_metrics(
    flux: Sequence[float] | FloatArray,
    receiver_samples: Sequence[MeshPatchReceiverSample],
    *,
    leaf_count: int,
    patch_count: int,
) -> RexReceiverMetrics:
    """Compute scalar receiver statistics while preserving front/back groups."""

    values = _validated_flux(flux)
    if len(values) != len(receiver_samples):
        raise ValueError(
            "receiver flux row count does not match receiver samples: "
            f"expected {len(receiver_samples)}, got {len(values)}."
        )
    if leaf_count <= 0 or patch_count <= 0:
        raise ValueError("leaf_count and patch_count must be positive.")
    if len(receiver_samples) != 2 * patch_count:
        raise ValueError(
            "two-sided receiver samples must contain exactly two rows per patch."
        )
    front_indices = [
        index for index, sample in enumerate(receiver_samples) if sample.side == "front"
    ]
    back_indices = [
        index for index, sample in enumerate(receiver_samples) if sample.side == "back"
    ]
    if len(front_indices) != patch_count or len(back_indices) != patch_count:
        raise ValueError(
            "receiver samples must contain one front and one back row per patch."
        )
    if any(
        receiver_samples[index].patch_id != receiver_samples[index + 1].patch_id
        or receiver_samples[index].side != "front"
        or receiver_samples[index + 1].side != "back"
        for index in range(0, len(receiver_samples), 2)
    ):
        raise ValueError("receiver samples must be ordered as stable front/back pairs.")
    weights = np.asarray([sample.area_m2 for sample in receiver_samples], dtype=float)
    if not np.all(np.isfinite(weights)) or np.any(weights <= 0.0):
        raise ValueError("receiver sample areas must be finite and positive.")
    front_mean = float(values[front_indices].mean())
    back_mean = float(values[back_indices].mean())
    ratio = None if back_mean == 0.0 else front_mean / back_mean
    return RexReceiverMetrics(
        receiver_count=len(values),
        leaf_count=leaf_count,
        patch_count=patch_count,
        front_receiver_count=len(front_indices),
        back_receiver_count=len(back_indices),
        mean_incident_ppfd=float(values.mean()),
        minimum_incident_ppfd=float(values.min()),
        maximum_incident_ppfd=float(values.max()),
        front_mean_incident_ppfd=front_mean,
        back_mean_incident_ppfd=back_mean,
        front_back_ratio=ratio,
        area_weighted_mean_incident_ppfd=float(np.average(values, weights=weights)),
    )


def _validated_flux(values: Sequence[float] | FloatArray) -> FloatArray:
    try:
        vector = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("receiver flux must be a numeric vector.") from exc
    if vector.ndim != 1 or vector.size == 0:
        raise ValueError("receiver flux must be a non-empty one-dimensional vector.")
    if not np.all(np.isfinite(vector)):
        raise ValueError("receiver flux contains non-finite values.")
    if np.any(vector < 0.0):
        raise ValueError("receiver flux contains negative PPFD values.")
    return np.asarray(vector, dtype=float)


def _replace_executable(command: CommandSpec, executable: Path) -> CommandSpec:
    return CommandSpec(
        argv=(str(executable), *command.argv[1:]),
        stdin_path=command.stdin_path,
        stdout_path=command.stdout_path,
        stdout_mode=command.stdout_mode,
        cwd=command.cwd,
        env=command.env,
        label=command.label,
    )


def _command_dict(command: CommandSpec) -> dict[str, Any]:
    return {
        "argv": list(command.argv),
        "stdin_path": None if command.stdin_path is None else str(command.stdin_path),
        "stdout_path": None if command.stdout_path is None else str(command.stdout_path),
        "stdout_mode": command.stdout_mode,
        "cwd": None if command.cwd is None else str(command.cwd),
        "env": dict(sorted(command.env.items())),
        "label": command.label,
    }


def _execution_payload(
    plan: RexReceiverSmokePlan,
    metrics: RexReceiverMetrics,
    oconv_command: CommandSpec,
    rtrace_command: CommandSpec,
    oconv_result: RunnerResult,
    rtrace_result: RunnerResult,
    installation: RadianceInstallation,
) -> dict[str, Any]:
    paths = plan.paths
    return {
        "schema_version": 1,
        "artifact_type": "fspm_optics_rex_scalar_receiver_execution",
        "success": True,
        "transport_scope": "geometry_transport_smoke_only",
        "leaf_optics_policy": "opaque_neutral_scalar_placeholder_not_rex_rta",
        "metrics": metrics.to_dict(),
        "leaf_material": {
            "name": plan.leaf_material.name,
            "rgb_reflectance": [
                plan.leaf_material.red_reflectance,
                plan.leaf_material.green_reflectance,
                plan.leaf_material.blue_reflectance,
            ],
            "provenance": plan.leaf_material.provenance,
            "notes": plan.leaf_material.notes,
        },
        "wall_times_s": {
            "oconv": oconv_result.wall_time_s,
            "rtrace": rtrace_result.wall_time_s,
            "total": oconv_result.wall_time_s + rtrace_result.wall_time_s,
        },
        "paths": {
            "room": str(paths.room_path),
            "emitters": str(paths.emitter_path),
            "plant": str(paths.plant_path),
            "receivers": str(paths.receiver_path),
            "octree": str(paths.octree_path),
            "rgb_output": str(paths.rgb_output_path),
            "flux": str(paths.flux_path),
            "metrics": str(paths.metrics_path),
            "execution": str(paths.execution_path),
        },
        "hashes": {
            "room_sha256": _sha256_file(paths.room_path),
            "emitter_sha256": _sha256_file(paths.emitter_path),
            "plant_sha256": _sha256_file(paths.plant_path),
            "receivers_sha256": _sha256_file(paths.receiver_path),
            "octree_sha256": _sha256_file(paths.octree_path),
            "rgb_output_sha256": _sha256_file(paths.rgb_output_path),
            "flux_sha256": _sha256_file(paths.flux_path),
            "metrics_sha256": _sha256_file(paths.metrics_path),
        },
        "commands": {
            "oconv": _command_dict(oconv_command),
            "rtrace": _command_dict(rtrace_command),
        },
        "radiance_installation": installation.to_dict(),
    }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
