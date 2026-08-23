"""Reproducible basis workspace planning, hashing, and materialization."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
from typing import Sequence

from fspm_optics.fixtures.occlusion import (
    FixtureOcclusionPlan,
    materialize_fixture_occlusion,
    plan_fixture_occlusion,
    proposed_layout_transport_payload,
)
from fspm_optics.fixtures.smd.artifacts import format_smd_emitter_metadata_json
from fspm_optics.fixtures.smd.positions import SmdLayout
from fspm_optics.fixtures.smd.radiance_writer import SmdEmitterAssumptions
from fspm_optics.geometry.room import RoomDimensions, room_radiance_text
from fspm_optics.geometry.sensor_grid import (
    AdaptiveSensorGrid,
    AdaptiveSensorGridPolicy,
    BASELINE_REFERENCE_PLANE_Z_M,
    SensorGridSpec,
    build_adaptive_sensor_grid,
    format_rtrace_receivers,
    generate_sensor_points,
)
from fspm_optics.radiance.commands import LOCAL_DEFAULT_NTHREADS, CommandSpec

from .atomic import commit_staged, stage_bytes
from .artifacts import format_basis_manifest_json
from .manifest import BasisManifest
from .planning import BasisGenerationPlan, plan_isolated_rtrace_basis


class BasisWorkspaceConflictError(RuntimeError):
    """An existing workspace artifact differs from the planned content."""


@dataclass(frozen=True, slots=True)
class BasisWorkspacePlan:
    generation_plan: BasisGenerationPlan
    room_text: str
    sensor_text: str
    sensor_grid_spec: SensorGridSpec
    adaptive_sensor_grid: AdaptiveSensorGrid | None
    manifest_path: Path

    @property
    def manifest(self) -> BasisManifest:
        return self.generation_plan.manifest


@dataclass(frozen=True, slots=True)
class MaterializedBasisWorkspace:
    manifest: BasisManifest
    generation_plan: BasisGenerationPlan
    room_path: Path
    sensor_path: Path
    emitter_paths: tuple[Path, ...]
    octree_paths: tuple[Path, ...]
    rtrace_output_paths: tuple[Path, ...]
    manifest_path: Path
    source_variant_cal_path: Path | None = None
    source_angular_data_path: Path | None = None
    fixture_occlusion: FixtureOcclusionPlan | None = None


def plan_basis_workspace(
    *,
    layout: SmdLayout,
    output_directory: str | Path,
    room_height_m: float = 3.048,
    physical_room: RoomDimensions | None = None,
    sensor_room: RoomDimensions | None = None,
    sensor_grid: SensorGridSpec | AdaptiveSensorGrid | None = None,
    adaptive_policy: AdaptiveSensorGridPolicy = AdaptiveSensorGridPolicy(),
    sensor_height_m: float = BASELINE_REFERENCE_PLANE_Z_M,
    reference_watts: float = 1.0,
    radiance_options: Sequence[str] | None = None,
    nthreads: int = LOCAL_DEFAULT_NTHREADS,
    use_ambient_cache: bool = True,
    emitter_assumptions: SmdEmitterAssumptions = SmdEmitterAssumptions(),
) -> BasisWorkspacePlan:
    """Build a fully hashed workspace plan without writing any files."""

    output_dir = Path(output_directory).expanduser().resolve()
    room = physical_room or RoomDimensions(
        layout.room_length_m,
        layout.room_width_m,
        room_height_m,
    )
    receiver_room = sensor_room or room
    if not math.isclose(
        receiver_room.height_m, room.height_m, rel_tol=0.0, abs_tol=1.0e-12
    ):
        raise ValueError("sensor and physical room heights must match.")
    if (
        receiver_room.length_m > room.length_m + 1.0e-12
        or receiver_room.width_m > room.width_m + 1.0e-12
    ):
        raise ValueError("sensor room must be contained by the physical room.")
    adaptive: AdaptiveSensorGrid | None
    if sensor_grid is None:
        adaptive = build_adaptive_sensor_grid(
            receiver_room,
            canopy_height_m=sensor_height_m,
            policy=adaptive_policy,
        )
        grid_spec = adaptive.spec
    elif isinstance(sensor_grid, AdaptiveSensorGrid):
        adaptive = sensor_grid
        grid_spec = sensor_grid.spec
    else:
        adaptive = None
        grid_spec = sensor_grid
    _validate_grid_room(grid_spec, receiver_room)

    room_text = room_radiance_text(room)
    sensor_text = format_rtrace_receivers(generate_sensor_points(grid_spec))
    fixture_occlusion = plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=proposed_layout_transport_payload(layout),
        output_directory=output_dir / "fixture_occlusion",
    )
    generation_plan = plan_isolated_rtrace_basis(
        layout=layout,
        sensor_count=grid_spec.point_count,
        room_height_m=room.height_m,
        room_source_path=output_dir / "room.rad",
        sensor_input_path=output_dir / "sensors.pts",
        output_directory=output_dir,
        reference_watts=reference_watts,
        radiance_options=radiance_options,
        nthreads=nthreads,
        use_ambient_cache=use_ambient_cache,
        emitter_assumptions=emitter_assumptions,
        fixture_occlusion=fixture_occlusion,
    )
    emitter_texts = tuple(
        column.emitter_document.radiance_text for column in generation_plan.columns
    )
    emitter_metadata = tuple(
        format_smd_emitter_metadata_json(column.emitter_document)
        for column in generation_plan.columns
    )
    command_policy = _command_policy_payload(generation_plan)
    hashed_manifest = replace(
        generation_plan.manifest,
        room_text_sha256=sha256_text(room_text),
        sensor_text_sha256=sha256_text(sensor_text),
        emitter_text_sha256_by_control_zone=tuple(
            sha256_text(text) for text in emitter_texts
        ),
        command_policy_sha256=sha256_text(
            json.dumps(command_policy, sort_keys=True, separators=(",", ":"))
        ),
        emitter_source_sha256=sha256_text("\0".join(emitter_texts)),
        emitter_metadata_sha256=sha256_text("\0".join(emitter_metadata)),
    )
    generation_plan = replace(generation_plan, manifest=hashed_manifest)
    return BasisWorkspacePlan(
        generation_plan=generation_plan,
        room_text=room_text,
        sensor_text=sensor_text,
        sensor_grid_spec=grid_spec,
        adaptive_sensor_grid=adaptive,
        manifest_path=output_dir / "basis_manifest.json",
    )


def materialize_basis_workspace(
    workspace: BasisWorkspacePlan,
) -> MaterializedBasisWorkspace:
    """Atomically materialize inputs, refusing incompatible existing content."""

    plan = workspace.generation_plan
    expected: list[tuple[Path, bytes]] = [
        (plan.room_source_path, workspace.room_text.encode("utf-8")),
        (plan.sensor_input_path, workspace.sensor_text.encode("utf-8")),
    ]
    expected.extend(
        (
            column.emitter_source_path,
            column.emitter_document.radiance_text.encode("utf-8"),
        )
        for column in plan.columns
    )
    if (
        plan.source_variant_cal_path is not None
        and plan.source_variant_cal_text is not None
    ):
        expected.append(
            (
                plan.source_variant_cal_path,
                plan.source_variant_cal_text.encode("utf-8"),
            )
        )
    if (
        plan.source_angular_data_path is not None
        and plan.source_angular_data_text is not None
    ):
        expected.append(
            (
                plan.source_angular_data_path,
                plan.source_angular_data_text.encode("utf-8"),
            )
        )
    expected.append(
        (
            workspace.manifest_path,
            format_basis_manifest_json(workspace.manifest).encode("utf-8"),
        )
    )
    _preflight_workspace(expected)
    missing = [(path, data) for path, data in expected if not path.exists()]
    staged: list[tuple[Path, Path]] = []
    committed: list[Path] = []
    try:
        for final_path, data in missing:
            staged.append((stage_bytes(final_path, data), final_path))
        for temporary_path, final_path in staged:
            if final_path.exists():
                raise BasisWorkspaceConflictError(
                    f"workspace artifact appeared during materialization: {final_path}"
                )
            commit_staged(temporary_path, final_path)
            committed.append(final_path)
        materialize_fixture_occlusion(plan.fixture_occlusion)
    except BaseException:
        for final_path in reversed(committed):
            final_path.unlink(missing_ok=True)
        raise
    finally:
        for temporary_path, _final_path in staged:
            temporary_path.unlink(missing_ok=True)
    return MaterializedBasisWorkspace(
        manifest=workspace.manifest,
        generation_plan=plan,
        room_path=plan.room_source_path,
        sensor_path=plan.sensor_input_path,
        emitter_paths=tuple(column.emitter_source_path for column in plan.columns),
        octree_paths=tuple(column.octree_path for column in plan.columns),
        rtrace_output_paths=tuple(
            column.rgb_output_path for column in plan.columns
        ),
        manifest_path=workspace.manifest_path,
        source_variant_cal_path=plan.source_variant_cal_path,
        source_angular_data_path=plan.source_angular_data_path,
        fixture_occlusion=plan.fixture_occlusion,
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _preflight_workspace(expected: Sequence[tuple[Path, bytes]]) -> None:
    seen: set[Path] = set()
    for path, data in expected:
        if path in seen:
            raise ValueError(f"duplicate workspace artifact path: {path}")
        seen.add(path)
        if not path.exists():
            continue
        if not path.is_file() or path.read_bytes() != data:
            raise BasisWorkspaceConflictError(
                f"existing workspace artifact is incompatible: {path}"
            )


def _validate_grid_room(spec: SensorGridSpec, room: RoomDimensions) -> None:
    dimensions = (
        (spec.room.length_m, room.length_m),
        (spec.room.width_m, room.width_m),
        (spec.room.height_m, room.height_m),
    )
    if any(abs(actual - expected) > 1e-9 for actual, expected in dimensions):
        raise ValueError("sensor grid room dimensions must match the SMD layout room.")


def _command_policy_payload(plan: BasisGenerationPlan) -> dict[str, object]:
    root = plan.output_directory
    return {
        "backend": plan.manifest.backend,
        "radiance_options": list(plan.manifest.radiance_options),
        "nthreads": plan.manifest.nthreads,
        "proposed_layout_mode": plan.manifest.proposed_layout_mode.value,
        "proposed_ring_mode": plan.manifest.proposed_ring_mode.value,
        "module_pattern_id": plan.manifest.module_pattern_id,
        "fixture_policy_id": plan.manifest.fixture_policy_id,
        "fixture_occlusion_identity": (
            plan.fixture_occlusion.identity_sha256
        ),
        "ambient_cache_policy": plan.manifest.ambient_cache_policy,
        "columns": [
            {
                "control_zone_index": column.control_zone_index,
                "oconv": _command_payload(column.oconv_command, root),
                "rtrace": _command_payload(column.rtrace_command, root),
            }
            for column in plan.columns
        ],
    }


def _command_payload(command: CommandSpec, root: Path) -> dict[str, object]:
    return {
        "argv": [_normalize_token(token, root) for token in command.argv],
        "stdin": _normalize_optional_path(command.stdin_path, root),
        "stdout": _normalize_optional_path(command.stdout_path, root),
        "stdout_mode": command.stdout_mode,
        "cwd": _normalize_optional_path(command.cwd, root),
        "env": dict(sorted(command.env.items())),
        "label": command.label,
    }


def _normalize_token(token: str, root: Path) -> str:
    candidate = Path(token)
    if not candidate.is_absolute():
        return token
    return _relative_path_text(candidate, root)


def _normalize_optional_path(path: Path | None, root: Path) -> str | None:
    return None if path is None else _relative_path_text(path, root)


def _relative_path_text(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix() or "."
    except ValueError:
        return str(path)
