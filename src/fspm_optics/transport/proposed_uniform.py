"""One-pass complete-scene transport for uniform Proposed module dimming."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from numpy.typing import NDArray

from fspm_optics.fixtures.smd.positions import SmdLayout
from fspm_optics.fixtures.occlusion import (
    FixtureOcclusionPlan,
    compile_fixture_occlusion,
    materialize_fixture_occlusion,
    plan_fixture_occlusion,
    proposed_layout_transport_payload,
)
from fspm_optics.fixtures.smd.power_schedule import (
    ModulePowerSchedule,
    PROPOSED_RATED_REFERENCE_WATTS_PER_MODULE,
    build_uniform_module_schedule,
)
from fspm_optics.fixtures.smd.radiance_writer import (
    SmdEmitterAssumptions,
    SmdRadianceDocument,
    build_smd_radiance_document,
    smd_source_identity_sha256,
)
from fspm_optics.geometry.room import (
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    RoomDimensions,
    production_room_model_payload,
    room_radiance_text,
)
from fspm_optics.geometry.sensor_grid import (
    BASELINE_REFERENCE_PLANE_Z_M,
    AdaptiveSensorGrid,
    AdaptiveSensorGridPolicy,
    SensorGridSpec,
    build_adaptive_sensor_grid,
    format_rtrace_receivers,
    generate_sensor_points,
)
from fspm_optics.radiance.commands import (
    LOCAL_DEFAULT_NTHREADS,
    CommandSpec,
    build_baseline_rtrace_command,
    build_oconv_command,
)
from fspm_optics.radiance.options import replace_radiance_option_value
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.radiance.versioning import (
    RadianceInstallation,
    discover_radiance_installation,
)
from fspm_optics.transport.basis.atomic import atomic_save_npy, atomic_write_text
from fspm_optics.transport.basis.parsing import parse_basis_column

FloatArray = NDArray[np.float64]
UNIFORM_STAGE_A_SCHEMA_ID = "fspm-optics.proposed-uniform-stage-a"
UNIFORM_STAGE_A_SCHEMA_VERSION = 5


class UniformStageAError(RuntimeError):
    """A complete equal-module Proposed trace failed closed."""


@dataclass(frozen=True, slots=True)
class UniformStageAPaths:
    room: Path
    sensors: Path
    source: Path
    source_variant_cal: Path | None
    source_angular_data: Path | None
    octree: Path
    rgb: Path
    ambient_cache: Path | None
    reference_field: Path
    manifest: Path
    execution: Path


@dataclass(frozen=True, slots=True)
class UniformStageAPlan:
    workspace: Path
    layout: SmdLayout
    room_text: str
    sensor_text: str
    sensor_grid_spec: SensorGridSpec
    adaptive_sensor_grid: AdaptiveSensorGrid
    schedule: ModulePowerSchedule
    emitter_document: SmdRadianceDocument
    fixture_occlusion: FixtureOcclusionPlan
    reference_watts_per_module: float
    radiance_options: tuple[str, ...]
    nthreads: int
    paths: UniformStageAPaths
    oconv_command: CommandSpec
    rtrace_command: CommandSpec
    identity_sha256: str


@dataclass(frozen=True, slots=True)
class UniformStageAResult:
    reference_field: FloatArray
    plan: UniformStageAPlan
    oconv_result: RunnerResult
    rtrace_result: RunnerResult
    radiance_installation: RadianceInstallation
    execution_metadata: dict[str, object]


def plan_uniform_proposed_stage_a(
    *,
    layout: SmdLayout,
    output_directory: str | Path,
    radiance_options: Sequence[str],
    reference_watts_per_module: float = PROPOSED_RATED_REFERENCE_WATTS_PER_MODULE,
    room_height_m: float = 3.048,
    physical_room: RoomDimensions | None = None,
    sensor_room: RoomDimensions | None = None,
    sensor_height_m: float = BASELINE_REFERENCE_PLANE_Z_M,
    adaptive_policy: AdaptiveSensorGridPolicy = AdaptiveSensorGridPolicy(),
    nthreads: int = LOCAL_DEFAULT_NTHREADS,
    use_ambient_cache: bool = True,
    emitter_assumptions: SmdEmitterAssumptions = SmdEmitterAssumptions(),
) -> UniformStageAPlan:
    """Plan exactly one complete equal-amplitude Proposed scalar scene."""

    reference_watts = _positive(
        "reference_watts_per_module", reference_watts_per_module
    )
    if isinstance(nthreads, bool) or not isinstance(nthreads, int) or nthreads <= 0:
        raise ValueError("nthreads must be a positive integer.")
    options = tuple(str(value) for value in radiance_options)
    if any(not value for value in options):
        raise ValueError("radiance_options must contain non-empty tokens.")
    workspace = Path(output_directory).expanduser().resolve()
    room = physical_room or RoomDimensions(
        layout.room_length_m,
        layout.room_width_m,
        _positive("room_height_m", room_height_m),
    )
    receiver_room = sensor_room or room
    if (
        not math.isclose(
            receiver_room.height_m,
            room.height_m,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
        or receiver_room.length_m > room.length_m + 1.0e-12
        or receiver_room.width_m > room.width_m + 1.0e-12
    ):
        raise ValueError("sensor room must be contained by the physical room.")
    adaptive = build_adaptive_sensor_grid(
        receiver_room,
        canopy_height_m=_positive("sensor_height_m", sensor_height_m),
        policy=adaptive_policy,
    )
    room_text = room_radiance_text(room)
    sensor_text = format_rtrace_receivers(
        generate_sensor_points(adaptive.spec)
    )
    schedule = build_uniform_module_schedule(layout, reference_watts)
    source = build_smd_radiance_document(
        layout, schedule, assumptions=emitter_assumptions
    )
    source_identity = smd_source_identity_sha256(source.metadata)
    fixture_occlusion = plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=proposed_layout_transport_payload(layout),
        output_directory=workspace / "fixture_occlusion",
    )
    cal_path = (
        workspace / "smd_source_variant.cal"
        if source.source_variant_cal_text is not None
        else None
    )
    paths = UniformStageAPaths(
        room=workspace / "room.rad",
        sensors=workspace / "sensors.pts",
        source=workspace / "uniform_complete_source.rad",
        source_variant_cal=cal_path,
        source_angular_data=(
            workspace / source.source_angular_data_filename
            if source.source_angular_data_filename is not None
            else None
        ),
        octree=workspace / "uniform_complete_scene.oct",
        rgb=workspace / "uniform_complete_reference.rgb",
        ambient_cache=(
            workspace
            / (
                "uniform_complete_scene."
                f"{layout.proposed_layout_mode.value}"
                f".{layout.proposed_ring_mode.value}"
                + (
                    ".cob_source_shape_surrogate"
                    if source.metadata.source_mode
                    == "cob_source_shape_surrogate"
                    else ""
                )
                + f".{source_identity[:16]}"
                + f".{fixture_occlusion.identity_sha256[:16]}"
                + f".{PRODUCTION_ROOM_MODEL_IDENTITY_SHA256[:16]}.amb"
            )
            if use_ambient_cache
            else None
        ),
        reference_field=workspace / "uniform_complete_reference_ppfd.npy",
        manifest=workspace / "uniform_stage_a_manifest.json",
        execution=workspace / "uniform_stage_a_execution.json",
    )
    trace_options = list(options)
    if paths.ambient_cache is not None:
        trace_options = replace_radiance_option_value(
            trace_options, "-af", str(paths.ambient_cache)
    )
    scene_sources = [
        paths.room,
        paths.source,
        fixture_occlusion.instance_source_path,
    ]
    oconv = build_oconv_command(
        scene_sources,
        output_octree=paths.octree,
        cwd=workspace,
        label="compile_proposed_uniform_complete_scene",
    )
    raw_trace = build_baseline_rtrace_command(
        octree=paths.octree,
        receiver_input=paths.sensors,
        rgb_output=paths.rgb,
        options=trace_options,
        nthreads=nthreads,
        cwd=workspace,
    )
    rtrace = CommandSpec(
        argv=raw_trace.argv,
        stdin_path=raw_trace.stdin_path,
        stdout_path=raw_trace.stdout_path,
        stdout_mode=raw_trace.stdout_mode,
        cwd=raw_trace.cwd,
        env=raw_trace.env,
        label="trace_proposed_uniform_complete_scene",
    )
    identity_payload = {
        "schema_id": UNIFORM_STAGE_A_SCHEMA_ID,
        "schema_version": UNIFORM_STAGE_A_SCHEMA_VERSION,
        "control_mode": "uniform_module_dimming",
        "proposed_layout_mode": layout.proposed_layout_mode.value,
        "proposed_ring_mode": layout.proposed_ring_mode.value,
        "module_pattern_id": layout.module_pattern_id,
        "fixture_policy_id": layout.fixture_policy_id,
        "room_model": production_room_model_payload(),
        "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
        "room_text_sha256": _sha256_text(room_text),
        "sensor_text_sha256": _sha256_text(sensor_text),
        "source_text_sha256": _sha256_text(source.radiance_text),
        "proposed_source_mode": source.metadata.source_mode,
        "source_metadata": {
            "classification": source.metadata.source_classification,
            "emitter_shape": source.metadata.emitter_shape,
            "emitter_area_per_module_m2": source.metadata.emitter_area_per_module_m2,
            "authenticated_ies_sha256": source.metadata.authenticated_ies_sha256,
            "normalized_angular_identity_sha256": (
                source.metadata.normalized_angular_identity_sha256
            ),
            "angular_data_sha256": source.metadata.normalized_angular_dat_sha256,
            "angular_normalization_policy": (
                source.metadata.angular_normalization_policy
            ),
            "completed_aperture_characterization_identity_sha256": (
                source.metadata.completed_aperture_characterization_identity_sha256
            ),
            "controlled_spd_identity_sha256": (
                source.metadata.controlled_spd_identity_sha256
            ),
            "completed_aperture_transmission": (
                source.metadata.accepted_fixture_transmission
            ),
            "completed_aperture_ppe_umol_per_j": (
                source.metadata.completed_aperture_fixture_ppe_umol_per_j
            ),
        },
        "fixture_occlusion_identity": fixture_occlusion.identity_sha256,
        "module_count": len(layout.modules),
        "reference_watts_per_module": reference_watts,
        "radiance_options": list(options),
        "nthreads": nthreads,
        "ambient_cache_policy": (
            "single_complete_scene" if use_ambient_cache else "disabled"
        ),
    }
    identity = _hash_json(identity_payload)
    return UniformStageAPlan(
        workspace=workspace,
        layout=layout,
        room_text=room_text,
        sensor_text=sensor_text,
        sensor_grid_spec=adaptive.spec,
        adaptive_sensor_grid=adaptive,
        schedule=schedule,
        emitter_document=source,
        fixture_occlusion=fixture_occlusion,
        reference_watts_per_module=reference_watts,
        radiance_options=options,
        nthreads=nthreads,
        paths=paths,
        oconv_command=oconv,
        rtrace_command=rtrace,
        identity_sha256=identity,
    )


def materialize_uniform_proposed_stage_a(plan: UniformStageAPlan) -> None:
    """Write only complete-scene inputs and a mode-specific identity manifest."""

    plan.workspace.mkdir(parents=True, exist_ok=True)
    materialize_fixture_occlusion(plan.fixture_occlusion)
    expected = [
        (plan.paths.room, plan.room_text),
        (plan.paths.sensors, plan.sensor_text),
        (plan.paths.source, plan.emitter_document.radiance_text),
    ]
    if plan.paths.source_variant_cal is not None:
        cal_text = plan.emitter_document.source_variant_cal_text
        if cal_text is None:  # pragma: no cover - paired by planner
            raise UniformStageAError("source variant CAL planning is inconsistent.")
        expected.append((plan.paths.source_variant_cal, cal_text))
    if plan.paths.source_angular_data is not None:
        angular_text = plan.emitter_document.source_angular_data_text
        if angular_text is None:
            raise UniformStageAError("source angular data planning is inconsistent.")
        expected.append((plan.paths.source_angular_data, angular_text))
    manifest = {
        "schema_id": UNIFORM_STAGE_A_SCHEMA_ID,
        "schema_version": UNIFORM_STAGE_A_SCHEMA_VERSION,
        "control_mode": "uniform_module_dimming",
        "proposed_layout_mode": plan.layout.proposed_layout_mode.value,
        "proposed_ring_mode": plan.layout.proposed_ring_mode.value,
        "module_pattern_id": plan.layout.module_pattern_id,
        "fixture_policy_id": plan.layout.fixture_policy_id,
        "room_model": production_room_model_payload(),
        "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
        "room_text_sha256": _sha256_text(plan.room_text),
        "basis_matrix_solver_enabled": False,
        "proposed_source_mode": plan.emitter_document.metadata.source_mode,
        "source_metadata": {
            "classification": plan.emitter_document.metadata.source_classification,
            "emitter_shape": plan.emitter_document.metadata.emitter_shape,
            "authenticated_ies_sha256": (
                plan.emitter_document.metadata.authenticated_ies_sha256
            ),
            "normalized_angular_identity_sha256": (
                plan.emitter_document.metadata.normalized_angular_identity_sha256
            ),
            "angular_data_sha256": (
                plan.emitter_document.metadata.normalized_angular_dat_sha256
            ),
            "angular_normalization_policy": (
                plan.emitter_document.metadata.angular_normalization_policy
            ),
            "completed_aperture_characterization_identity_sha256": (
                plan.emitter_document.metadata
                .completed_aperture_characterization_identity_sha256
            ),
            "controlled_spd_identity_sha256": (
                plan.emitter_document.metadata.controlled_spd_identity_sha256
            ),
        },
        "identity_sha256": plan.identity_sha256,
        "module_count": len(plan.layout.modules),
        "sensor_count": plan.sensor_grid_spec.point_count,
        "reference_watts_per_module": plan.reference_watts_per_module,
        "all_module_reference_watts_equal": (
            len(set(plan.schedule.watts_by_module)) == 1
        ),
        "complete_scene_trace_count": 1,
        "basis_column_count": 0,
        "fixture_occlusion": plan.fixture_occlusion.scientific_payload(),
        "radiance_options": list(plan.radiance_options),
        "nthreads": plan.nthreads,
    }
    expected.append(
        (
            plan.paths.manifest,
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        )
    )
    for path, text in expected:
        if path.exists():
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                raise UniformStageAError(
                    f"existing uniform Stage A artifact is incompatible: {path}"
                )
            continue
        atomic_write_text(path, text)


def execute_uniform_proposed_stage_a(
    plan: UniformStageAPlan,
    runner: LocalRunner,
    *,
    radiance_installation: RadianceInstallation | None = None,
    oconv_timeout_s: float | None = None,
    rtrace_timeout_s: float | None = None,
) -> UniformStageAResult:
    """Compile and trace the complete field once, then authenticate its samples."""

    installation = radiance_installation or discover_radiance_installation()
    compile_fixture_occlusion(
        plan.fixture_occlusion,
        runner,
        oconv_executable=installation.oconv.path,
        timeout_s=oconv_timeout_s,
    )
    oconv = _replace_executable(plan.oconv_command, installation.oconv.path)
    rtrace = _replace_executable(plan.rtrace_command, installation.rtrace.path)
    compiled = runner.run(oconv, timeout_s=oconv_timeout_s)
    if not compiled.success:
        raise UniformStageAError(
            compiled.failure_message or "uniform complete scene compilation failed."
        )
    if not plan.paths.octree.is_file():
        raise UniformStageAError("uniform scene compilation did not create an octree.")
    traced = runner.run(rtrace, timeout_s=rtrace_timeout_s)
    if not traced.success:
        raise UniformStageAError(
            traced.failure_message or "uniform complete scene trace failed."
        )
    if not plan.paths.rgb.is_file():
        raise UniformStageAError("uniform scene trace did not create RGB output.")
    try:
        field = parse_basis_column(
            plan.paths.rgb.read_text(encoding="utf-8"),
            expected_sensor_count=plan.sensor_grid_spec.point_count,
        )
    except ValueError as exc:
        raise UniformStageAError(
            f"uniform complete scene PPFD validation failed: {exc}"
        ) from exc
    atomic_save_npy(plan.paths.reference_field, field)
    metadata: dict[str, object] = {
        "schema_id": UNIFORM_STAGE_A_SCHEMA_ID,
        "schema_version": UNIFORM_STAGE_A_SCHEMA_VERSION,
        "control_mode": "uniform_module_dimming",
        "proposed_layout_mode": plan.layout.proposed_layout_mode.value,
        "proposed_ring_mode": plan.layout.proposed_ring_mode.value,
        "module_pattern_id": plan.layout.module_pattern_id,
        "fixture_policy_id": plan.layout.fixture_policy_id,
        "room_model": production_room_model_payload(),
        "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
        "basis_matrix_solver_enabled": False,
        "identity_sha256": plan.identity_sha256,
        "complete_scene_trace_count": 1,
        "basis_column_count": 0,
        "module_count": len(plan.layout.modules),
        "sensor_count": len(field),
        "reference_watts_per_module": plan.reference_watts_per_module,
        "all_module_reference_watts_equal": (
            len(set(plan.schedule.watts_by_module)) == 1
        ),
        "field_sha256": _sha256_file(plan.paths.reference_field),
        "room_sha256": _sha256_file(plan.paths.room),
        "sensor_sha256": _sha256_file(plan.paths.sensors),
        "source_sha256": _sha256_file(plan.paths.source),
        "proposed_source_mode": plan.emitter_document.metadata.source_mode,
        "source_angular_data_sha256": (
            None
            if plan.paths.source_angular_data is None
            else _sha256_file(plan.paths.source_angular_data)
        ),
        "completed_aperture_characterization_identity_sha256": (
            plan.emitter_document.metadata
            .completed_aperture_characterization_identity_sha256
        ),
        "controlled_spd_identity_sha256": (
            plan.emitter_document.metadata.controlled_spd_identity_sha256
        ),
        "fixture_occlusion": plan.fixture_occlusion.scientific_payload(),
        "commands": {
            "oconv": list(oconv.argv),
            "rtrace": list(rtrace.argv),
        },
        "wall_times_s": {
            "oconv": compiled.wall_time_s,
            "rtrace": traced.wall_time_s,
        },
        "radiance_installation": installation.to_dict(),
    }
    atomic_write_text(
        plan.paths.execution,
        json.dumps(metadata, sort_keys=True, indent=2) + "\n",
    )
    return UniformStageAResult(
        reference_field=np.asarray(field, dtype=float),
        plan=plan,
        oconv_result=compiled,
        rtrace_result=traced,
        radiance_installation=installation,
        execution_metadata=metadata,
    )


def _replace_executable(command: CommandSpec, executable: Path) -> CommandSpec:
    return CommandSpec(
        argv=(str(executable), *command.argv[1:]),
        stdin_path=command.stdin_path,
        stdout_path=command.stdout_path,
        cwd=command.cwd,
        env=command.env,
        label=command.label,
        stdout_mode=command.stdout_mode,
    )


def _positive(name: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise ValueError(f"{name} must be finite and positive.")
    return float(value)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_json(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "UNIFORM_STAGE_A_SCHEMA_ID",
    "UNIFORM_STAGE_A_SCHEMA_VERSION",
    "UniformStageAError",
    "UniformStageAPlan",
    "UniformStageAResult",
    "execute_uniform_proposed_stage_a",
    "materialize_uniform_proposed_stage_a",
    "plan_uniform_proposed_stage_a",
]
