"""Native fixed-output HPS scalar horizontal-PPFD transport."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Final, Mapping, Protocol

from fspm_optics.fixtures.hps import (
    HPS_DEFAULT_MOUNT_HEIGHT_M,
    HPS_IES_RESOURCE_NAME,
    HPS_IES_SHA256,
    HPS_LAYOUT_POLICY_ID,
    HPS_LIGHT_MODIFIER_ID,
    HPS_RADIANCE_CARRIER_MULTIPLIER,
    HpsFixturePlacement,
    HpsLayoutPlan,
    HpsRadianceSourcePlan,
    build_hps_comparison_profile,
    build_hps_radiance_source_plan,
    format_hps_apertures_rad,
    plan_hps_layout,
    validate_converted_hps_ies_output,
)
from fspm_optics.fixtures.hps.profile import (
    HPS_INITIAL_LAMP_PAR_PPF_UMOL_S,
    HPS_NOMINAL_LAMP_CLASS_POWER_W,
    HPS_SYSTEM_PPE_UMOL_PER_J,
    HPS_TESTED_SYSTEM_INPUT_POWER_W,
)
from fspm_optics.fixtures.hps.resources import (
    HPS_SPD_RESOURCE_NAME,
    HPS_SPD_SHA256,
)
from fspm_optics.fixtures.occlusion import (
    FixtureOcclusionPlan,
    compile_fixture_occlusion,
    materialize_fixture_occlusion,
    plan_fixture_occlusion,
    validate_compiled_fixture_occlusion,
)
from fspm_optics.geometry.active_domain import ActiveRoomDomain
from fspm_optics.geometry.coordinate_frame import RoomCoordinateFrame
from fspm_optics.geometry.room import (
    DEFAULT_ROOM_HEIGHT_M,
    FEET_TO_METERS,
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    RoomDimensions,
    production_room_model_payload,
    room_radiance_text,
)
from fspm_optics.geometry.sensor_grid import (
    AdaptiveSensorGridPolicy,
    BASELINE_REFERENCE_PLANE_Z_M,
    SensorGridSpec,
    SensorPoint,
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
from fspm_optics.radiance.executables import resolve_executable
from fspm_optics.radiance.native_ies import NativeFlatcorrOutputContract
from fspm_optics.radiance.options import radiance_options
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.radiance.versioning import probe_radiance_version
from fspm_optics.runtime_paths import is_default_managed_runtime_descendant
from fspm_optics.transport.basis.atomic import atomic_write_bytes, atomic_write_text
from fspm_optics.transport.native_scalar_data import (
    compute_native_scalar_metric_values,
    decode_exact_native_scalar_rgb_rows,
    format_native_scalar_ppfd_npz,
    validate_native_scalar_dat,
)
from fspm_optics.transport.scalar_ppfd import (
    PPFD_CONVERSION_BASIS,
    PpfdMapSample,
)
from fspm_optics.transport.scenes import ScenePlan

HPS_SCALAR_SCHEMA_VERSION: Final = 3
HPS_SCALAR_PAYLOAD_TYPE: Final = "fspm_optics_hps_scalar_horizontal_ppfd"
HPS_SCALAR_RUNTIME_ROOT: Final = "hps_scalar"
DEFAULT_REFERENCE_PLANE_Z_M: Final = BASELINE_REFERENCE_PLANE_Z_M
DEFAULT_ROOM_LENGTH_FT: Final = 10.0
DEFAULT_ROOM_WIDTH_FT: Final = 10.0
HPS_FIXTURE_POWER_W: Final = HPS_TESTED_SYSTEM_INPUT_POWER_W
HPS_FIXTURE_PPE_UMOL_PER_J: Final = HPS_SYSTEM_PPE_UMOL_PER_J
HPS_FIXTURE_PPF_UMOL_S: Final = HPS_INITIAL_LAMP_PAR_PPF_UMOL_S
PPFD_NPZ_ARRAY_ORDER: Final = ("x_m", "y_m", "z_m", "ppfd_umol_m2_s")
QUALITY_PROFILES: Final = ("direct", "standard", "quality", "rigorous")
SOURCE_LIMITATIONS: Final = (
    "The IES supplies measured angular shape and footprint, not measured PAR output.",
    "Documented 1750 umol/s initial lamp PAR PPF and tested 1045 W system input are authoritative.",
    "No post-trace 179 conversion, lumen/SPD bridge, rescaling, target "
    "matching, dimming, or spatial symmetrization is applied.",
)


class HpsScalarTransportError(RuntimeError):
    """A planned HPS native scalar run failed a scientific gate."""


class CommandRunner(Protocol):
    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult: ...


ExecutableResolver = Callable[..., Path]
VersionProbe = Callable[[Path], str | None]


@dataclass(frozen=True, slots=True)
class HpsScalarExecutables:
    ies2rad: Path
    oconv: Path
    rtrace: Path
    rtrace_version_text: str | None = None

    def __post_init__(self) -> None:
        for name in ("ies2rad", "oconv", "rtrace"):
            path = Path(getattr(self, name)).expanduser()
            if str(path) in ("", "."):
                raise ValueError(f"{name} executable path must be non-empty.")
            object.__setattr__(self, name, path)


@dataclass(frozen=True, slots=True)
class HpsScalarTransportRequest:
    workspace: Path
    room_length_m: float
    room_width_m: float
    reference_plane_z_m: float = DEFAULT_REFERENCE_PLANE_Z_M
    mount_height_m: float = HPS_DEFAULT_MOUNT_HEIGHT_M
    active_domain: ActiveRoomDomain | None = None
    sensor_grid: SensorGridSpec | AdaptiveSensorGridPolicy = field(
        default_factory=AdaptiveSensorGridPolicy
    )
    quality_profile: str = "standard"
    threads: int = LOCAL_DEFAULT_NTHREADS
    ies2rad_command: str | Path = "ies2rad"
    oconv_command: str | Path = "oconv"
    rtrace_command: str | Path = "rtrace"

    def __post_init__(self) -> None:
        raw_workspace = Path(self.workspace).expanduser()
        if not raw_workspace.is_absolute():
            raise ValueError("HPS runtime workspace must be an absolute path.")
        workspace = raw_workspace.resolve()
        _reject_repository_workspace(workspace)
        object.__setattr__(self, "workspace", workspace)
        for name in ("room_length_m", "room_width_m", "mount_height_m"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        reference = _finite("reference_plane_z_m", self.reference_plane_z_m)
        if reference < 0.0:
            raise ValueError("reference_plane_z_m must be non-negative.")
        object.__setattr__(self, "reference_plane_z_m", reference)
        if reference + self.mount_height_m >= DEFAULT_ROOM_HEIGHT_M:
            raise ValueError("the aperture plane must lie below the fixed room ceiling.")
        quality = str(self.quality_profile).strip().lower()
        if quality not in QUALITY_PROFILES:
            raise ValueError(f"quality_profile must be one of {QUALITY_PROFILES!r}.")
        object.__setattr__(self, "quality_profile", quality)
        if isinstance(self.threads, bool) or not isinstance(self.threads, int) or self.threads <= 0:
            raise ValueError("threads must be a positive integer.")
        if not isinstance(self.sensor_grid, (SensorGridSpec, AdaptiveSensorGridPolicy)):
            raise ValueError("sensor_grid must be a SensorGridSpec or AdaptiveSensorGridPolicy.")
        for name in ("ies2rad_command", "oconv_command", "rtrace_command"):
            if not str(getattr(self, name)):
                raise ValueError(f"{name} must be non-empty.")
        if self.active_domain is not None:
            if not isinstance(self.active_domain, ActiveRoomDomain):
                raise ValueError("active_domain must be ActiveRoomDomain.")
            if any(
                not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1.0e-12)
                for actual, expected in (
                    (
                        self.active_domain.outer_requested_length_m,
                        self.room_length_m,
                    ),
                    (
                        self.active_domain.outer_requested_width_m,
                        self.room_width_m,
                    ),
                )
            ):
                raise ValueError(
                    "active_domain outer dimensions must match the transport request."
                )

    @classmethod
    def from_feet(
        cls,
        *,
        workspace: str | Path,
        room_length_ft: float = DEFAULT_ROOM_LENGTH_FT,
        room_width_ft: float = DEFAULT_ROOM_WIDTH_FT,
        **kwargs: Any,
    ) -> "HpsScalarTransportRequest":
        return cls(
            workspace=Path(workspace),
            room_length_m=_positive("room_length_ft", room_length_ft) * FEET_TO_METERS,
            room_width_m=_positive("room_width_ft", room_width_ft) * FEET_TO_METERS,
            **kwargs,
        )

    @classmethod
    def from_meters(
        cls,
        *,
        workspace: str | Path,
        room_length_m: float,
        room_width_m: float,
        **kwargs: Any,
    ) -> "HpsScalarTransportRequest":
        return cls(
            workspace=Path(workspace),
            room_length_m=room_length_m,
            room_width_m=room_width_m,
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class HpsScalarWorkspacePlan:
    workspace: Path
    artifact_root: Path
    source_directory: Path
    scene_directory: Path
    native_directory: Path
    log_directory: Path
    input_identity: Path
    derived_ies: Path
    raw_converted_rad: Path
    converted_dat: Path
    shared_angular_rad: Path
    aperture_rad: Path
    room_rad: Path
    sensor_rays: Path
    scene_manifest: Path
    octree: Path
    ambient_cache: Path
    raw_rtrace: Path
    ppfd_npz: Path
    source_conversion_summary: Path
    scalar_transport_summary: Path
    command_provenance_summary: Path
    failure_summary: Path
    ies2rad_stderr: Path
    oconv_stderr: Path
    rtrace_stderr: Path

    @property
    def fixture_body_instances_rad(self) -> Path:
        return (
            self.scene_directory
            / "fixture_occlusion"
            / "fixture_body_instances.rad"
        )

    @property
    def final_scene_inputs(self) -> tuple[Path, Path, Path, Path]:
        return (
            self.room_rad,
            self.shared_angular_rad,
            self.aperture_rad,
            self.fixture_body_instances_rad,
        )

    def run_root_relative_reference(self, path: str | Path) -> str:
        candidate = Path(path)
        try:
            relative = candidate.relative_to(self.artifact_root)
        except ValueError as exc:
            raise ValueError("runtime resource must be contained by the HPS run root.") from exc
        if not relative.parts or ".." in relative.parts:
            raise ValueError("runtime resource reference is unsafe.")
        return relative.as_posix()

    @property
    def converted_dat_execution_reference(self) -> str:
        return self.run_root_relative_reference(self.converted_dat)


@dataclass(frozen=True, slots=True)
class HpsScalarScenePlan:
    scene: ScenePlan
    scene_id: str
    source_plan_id: str
    room_identity: str
    sensor_identity: str


@dataclass(frozen=True, slots=True)
class HpsScalarTransportPlan:
    request: HpsScalarTransportRequest
    paths: HpsScalarWorkspacePlan
    layout: HpsLayoutPlan
    source: HpsRadianceSourcePlan
    fixture_occlusion: FixtureOcclusionPlan
    room: RoomDimensions
    sensor_grid_spec: SensorGridSpec
    sensor_points: tuple[SensorPoint, ...]
    room_text: str
    sensor_text: str
    room_identity: str
    sensor_identity: str
    quality_identity: str
    transport_identity: str
    command_identity: str
    scene: HpsScalarScenePlan
    ies2rad_command: CommandSpec
    oconv_command: CommandSpec
    rtrace_command: CommandSpec
    radiance_options: tuple[str, ...]
    octree_identity: str
    ambient_cache_identity: str
    result_identity: str

    @property
    def ambient_cache_enabled(self) -> bool:
        return self.request.quality_profile != "direct"

    @property
    def commands(self) -> tuple[CommandSpec, CommandSpec, CommandSpec]:
        return (self.ies2rad_command, self.oconv_command, self.rtrace_command)


@dataclass(frozen=True, slots=True)
class HpsScalarMetrics:
    sensor_count: int
    mean_ppfd_umol_m2_s: float
    minimum_ppfd_umol_m2_s: float
    maximum_ppfd_umol_m2_s: float
    standard_deviation_ppfd_umol_m2_s: float
    coefficient_of_variation: float
    coefficient_of_variation_percent: float
    minimum_to_mean_uniformity: float

    def to_payload(self) -> dict[str, float | int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class HpsScalarTransportResult:
    plan: HpsScalarTransportPlan
    samples: tuple[PpfdMapSample, ...]
    metrics: HpsScalarMetrics
    source_conversion_summary: Mapping[str, Any]
    scalar_transport_summary: Mapping[str, Any]
    ppfd_npz_sha256: str

    @property
    def command_count(self) -> int:
        return len(self.plan.commands)


def plan_hps_scalar_workspace(
    request: HpsScalarTransportRequest,
) -> HpsScalarWorkspacePlan:
    root = request.workspace / HPS_SCALAR_RUNTIME_ROOT
    source = root / "source"
    scene = root / "scene"
    native = root / "native"
    logs = root / "logs"
    output_root = "hps_unit_downward_flux"
    return HpsScalarWorkspacePlan(
        workspace=request.workspace,
        artifact_root=root,
        source_directory=source,
        scene_directory=scene,
        native_directory=native,
        log_directory=logs,
        input_identity=root / "input_identity.json",
        derived_ies=source / f"{output_root}.ies",
        raw_converted_rad=source / f"{output_root}.rad",
        converted_dat=source / f"{output_root}.dat",
        shared_angular_rad=source / "hps_shared_angular.rad",
        aperture_rad=scene / "hps_apertures.rad",
        room_rad=scene / "room.rad",
        sensor_rays=scene / "horizontal_sensor_grid.pts",
        scene_manifest=scene / "scene_inputs.json",
        octree=native / "hps_scalar.oct",
        ambient_cache=native / (
            "hps_scalar."
            f"{PRODUCTION_ROOM_MODEL_IDENTITY_SHA256[:16]}.amb"
        ),
        raw_rtrace=native / "rtrace_rgb.txt",
        ppfd_npz=root / "ppfd_values.npz",
        source_conversion_summary=root / "source_conversion_summary.json",
        scalar_transport_summary=root / "scalar_transport_summary.json",
        command_provenance_summary=root / "command_provenance_summary.json",
        failure_summary=root / "failure_summary.json",
        ies2rad_stderr=logs / "ies2rad.stderr.log",
        oconv_stderr=logs / "oconv.stderr.log",
        rtrace_stderr=logs / "rtrace.stderr.log",
    )


def plan_hps_scalar_transport(
    request: HpsScalarTransportRequest,
    *,
    executables: HpsScalarExecutables | None = None,
) -> HpsScalarTransportPlan:
    paths = plan_hps_scalar_workspace(request)
    domain = request.active_domain
    layout = plan_hps_layout(
        (
            request.room_length_m
            if domain is None
            else domain.active_requested_length_m
        ),
        (
            request.room_width_m
            if domain is None
            else domain.active_requested_width_m
        ),
        reference_plane_z_m=request.reference_plane_z_m,
        mount_height_m=request.mount_height_m,
    )
    outer_frame = RoomCoordinateFrame(
        request.room_length_m,
        request.room_width_m,
    )
    room = RoomDimensions(
        outer_frame.simulation_length_m,
        outer_frame.simulation_width_m,
        DEFAULT_ROOM_HEIGHT_M,
    )
    receiver_room = RoomDimensions(
        (
            layout.room_axes.aligned.length_m
            if domain is None
            else domain.active_aligned_length_m
        ),
        (
            layout.room_axes.aligned.width_m
            if domain is None
            else domain.active_aligned_width_m
        ),
        DEFAULT_ROOM_HEIGHT_M,
    )
    grid_spec = _resolve_sensor_grid(request, receiver_room)
    points = generate_sensor_points(grid_spec)
    sensor_text = format_rtrace_receivers(points)
    room_text = room_radiance_text(room)
    bins = executables or HpsScalarExecutables(
        Path(request.ies2rad_command), Path(request.oconv_command), Path(request.rtrace_command)
    )
    placements = tuple(
        HpsFixturePlacement(
            item.fixture_id,
            item.aligned_x_m,
            item.aligned_y_m,
            item.aperture_z_m,
        )
        for item in layout.fixtures
    )
    source = build_hps_radiance_source_plan(
        workspace=paths.source_directory,
        placements=placements,
        ies2rad_bin=bins.ies2rad,
    )
    fixture_occlusion = plan_fixture_occlusion(
        system_id="hps",
        layout_identity=layout.to_payload(),
        output_directory=paths.scene_directory / "fixture_occlusion",
        oconv_bin=bins.oconv,
    )
    if (
        source.ies2rad.derived_ies_path != paths.derived_ies
        or source.ies2rad.converted_rad_path != paths.raw_converted_rad
        or source.ies2rad.converted_dat_path != paths.converted_dat
    ):
        raise HpsScalarTransportError("Phase 25A source/workspace plan mismatch.")
    room_identity = "hps-room-v2-" + _hash_payload(
        {
            "dimensions_m": [room.length_m, room.width_m, room.height_m],
            "room_model": production_room_model_payload(),
            "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
            "room_text_sha256": _sha256_text(room_text),
            **(
                {}
                if domain is None
                else {"active_domain": domain.to_payload()}
            ),
        }
    )
    sensor_identity = "hps-horizontal-sensor-grid-v1-" + _hash_payload(
        {
            "room_identity": room_identity,
            "resolution": [grid_spec.resolution_x, grid_spec.resolution_y],
            "reference_plane_z_m": request.reference_plane_z_m,
            "inset_m": grid_spec.inset_m,
            "layout": grid_spec.layout,
            "sensor_text_sha256": _sha256_text(sensor_text),
            **(
                {}
                if domain is None
                else {
                    "active_domain_identity_sha256": domain.identity_sha256
                }
            ),
        }
    )
    base_options = tuple(radiance_options(request.quality_profile))
    options = tuple(
        radiance_options(
            request.quality_profile,
            ambient_cache=(
                None if request.quality_profile == "direct" else paths.ambient_cache
            ),
        )
    )
    quality_identity = "hps-radiance-quality-v1-" + _hash_payload(
        {"profile": request.quality_profile, "options": list(base_options)}
    )
    raw_scene = ScenePlan(role="baseline_ppfd", source_files=paths.final_scene_inputs)
    scene_id = "hps-baseline-scene-v2-" + _hash_payload(
        {
            "source_plan_id": source.source_plan_id,
            "room_identity": room_identity,
            "sensor_identity": sensor_identity,
            "ordered_roles": [
                "room",
                "shared_angular_source",
                "fixture_apertures",
                "fixture_bodies",
            ],
            "fixture_occlusion_identity": fixture_occlusion.identity_sha256,
        }
    )
    scene = HpsScalarScenePlan(
        raw_scene,
        scene_id,
        source.source_plan_id,
        room_identity,
        sensor_identity,
    )
    transport_identity = "hps-scalar-transport-v2-" + _hash_payload(
        {
            "source_plan_id": source.source_plan_id,
            "layout_id": layout.layout_id,
            "room_identity": room_identity,
            "sensor_identity": sensor_identity,
            "quality_identity": quality_identity,
            "threads": request.threads,
            "enabled": request.quality_profile != "direct",
            "fixed_full_output": True,
            "fixture_occlusion_identity": fixture_occlusion.identity_sha256,
            **(
                {}
                if domain is None
                else {
                    "active_domain_identity_sha256": domain.identity_sha256
                }
            ),
        }
    )
    octree_identity = "hps-scalar-octree-v2-" + _hash_payload(
        {
            "scene_id": scene_id,
            "ordered_source_roles": [
                "room",
                "shared_angular_source",
                "fixture_apertures",
                "fixture_bodies",
            ],
            "fixture_occlusion_identity": fixture_occlusion.identity_sha256,
        }
    )
    ambient_identity = "hps-scalar-ambient-v1-" + _hash_payload(
        {
            "octree_identity": octree_identity,
            "sensor_identity": sensor_identity,
            "quality_identity": quality_identity,
            "threads": request.threads,
        }
    )
    oconv = build_oconv_command(
        paths.final_scene_inputs,
        output_octree=paths.octree,
        cwd=paths.artifact_root,
        oconv_bin=bins.oconv,
        label="compile_hps_scalar_scene",
    )
    rtrace = build_baseline_rtrace_command(
        octree=paths.octree,
        receiver_input=paths.sensor_rays,
        rgb_output=paths.raw_rtrace,
        options=options,
        nthreads=request.threads,
        cwd=paths.artifact_root,
        rtrace_bin=bins.rtrace,
    )
    command_identity = "hps-scalar-commands-v1-" + _hash_payload(
        {
            "ies2rad": _scientific_command_payload(source.ies2rad.command, paths.artifact_root),
            "oconv": _scientific_command_payload(oconv, paths.artifact_root),
            "rtrace": _scientific_command_payload(rtrace, paths.artifact_root),
        }
    )
    result_identity = "hps-scalar-result-v1-" + _hash_payload(
        {
            "transport_identity": transport_identity,
            "command_identity": command_identity,
            "octree_identity": octree_identity,
            "ambient_cache_identity": ambient_identity,
        }
    )
    plan = HpsScalarTransportPlan(
        request=request,
        paths=paths,
        layout=layout,
        source=source,
        fixture_occlusion=fixture_occlusion,
        room=room,
        sensor_grid_spec=grid_spec,
        sensor_points=points,
        room_text=room_text,
        sensor_text=sensor_text,
        room_identity=room_identity,
        sensor_identity=sensor_identity,
        quality_identity=quality_identity,
        transport_identity=transport_identity,
        command_identity=command_identity,
        scene=scene,
        ies2rad_command=source.ies2rad.command,
        oconv_command=oconv,
        rtrace_command=rtrace,
        radiance_options=options,
        octree_identity=octree_identity,
        ambient_cache_identity=ambient_identity,
        result_identity=result_identity,
    )
    _validate_planned_closure(plan)
    return plan


def resolve_hps_scalar_executables(
    request: HpsScalarTransportRequest,
    *,
    resolver: ExecutableResolver = resolve_executable,
    version_probe: VersionProbe = probe_radiance_version,
) -> HpsScalarExecutables:
    ies2rad = resolver(request.ies2rad_command, label="ies2rad")
    oconv = resolver(request.oconv_command, label="oconv")
    rtrace = resolver(request.rtrace_command, label="rtrace")
    try:
        version = version_probe(rtrace)
    except Exception:
        version = None
    return HpsScalarExecutables(ies2rad, oconv, rtrace, version)


def execute_hps_scalar_transport(
    request: HpsScalarTransportRequest,
    runner: CommandRunner | None = None,
    *,
    executables: HpsScalarExecutables | None = None,
    resolver: ExecutableResolver = resolve_executable,
    version_probe: VersionProbe = probe_radiance_version,
) -> HpsScalarTransportResult:
    resolved = executables or resolve_hps_scalar_executables(
        request, resolver=resolver, version_probe=version_probe
    )
    plan = plan_hps_scalar_transport(request, executables=resolved)
    local_runner = runner or LocalRunner()
    _materialize_empty_workspace(plan)
    try:
        return _execute_materialized_plan(plan, local_runner, resolved)
    except BaseException as exc:
        _write_failure_summary(plan, exc)
        raise


def compute_hps_scalar_metrics(
    samples: tuple[PpfdMapSample, ...],
) -> HpsScalarMetrics:
    values = compute_native_scalar_metric_values(samples)
    return HpsScalarMetrics(
        **{
            name: getattr(values, name)
            for name in HpsScalarMetrics.__dataclass_fields__
        }
    )


def format_hps_ppfd_values_npz(samples: tuple[PpfdMapSample, ...]) -> bytes:
    return format_native_scalar_ppfd_npz(samples)


def validate_hps_converted_dat(data: bytes) -> dict[str, object]:
    return validate_native_scalar_dat(data, error_factory=HpsScalarTransportError)


def _execute_materialized_plan(
    plan: HpsScalarTransportPlan,
    runner: CommandRunner,
    executables: HpsScalarExecutables,
) -> HpsScalarTransportResult:
    paths = plan.paths
    ies_result = runner.run(plan.ies2rad_command, stderr_path=paths.ies2rad_stderr)
    _require_success(plan.ies2rad_command, ies_result, "ies2rad conversion")
    _validate_ies2rad_output_set(plan)
    raw_rad_text = _read_ascii(paths.raw_converted_rad, "converted RAD")
    dat_metadata = validate_hps_converted_dat(paths.converted_dat.read_bytes())
    converted_dat_hash = _sha256_file(paths.converted_dat)
    try:
        contract = validate_converted_hps_ies_output(raw_rad_text)
    except Exception as exc:
        raise HpsScalarTransportError(f"invalid native HPS source: {exc}") from exc
    shared_text = _adapt_shared_dat_reference(
        contract.shared_definition_text,
        raw_dat_reference=contract.dat_reference,
        execution_dat_reference=paths.converted_dat_execution_reference,
    )
    _write_new(paths.shared_angular_rad, shared_text)
    _write_new(paths.aperture_rad, format_hps_apertures_rad(plan.source))
    _write_new(paths.room_rad, plan.room_text)
    _write_new(paths.sensor_rays, plan.sensor_text)
    source_summary = _source_conversion_payload(plan, contract, dat_metadata)
    _write_json_new(paths.source_conversion_summary, source_summary)
    _write_json_new(paths.scene_manifest, _scene_manifest_payload(plan))
    _validate_scene_inputs(plan)
    _validate_runtime_dat_binding(plan, expected_dat_sha256=converted_dat_hash)

    compile_fixture_occlusion(
        plan.fixture_occlusion,
        runner,
        oconv_executable=executables.oconv,
    )
    validate_compiled_fixture_occlusion(plan.fixture_occlusion)
    oconv_result = runner.run(plan.oconv_command, stderr_path=paths.oconv_stderr)
    _require_success(plan.oconv_command, oconv_result, "oconv scene compilation")
    _require_nonempty(paths.octree, "compiled octree")
    octree_hash = _sha256_file(paths.octree)

    _validate_runtime_dat_binding(plan, expected_dat_sha256=converted_dat_hash)
    rtrace_result = runner.run(plan.rtrace_command, stderr_path=paths.rtrace_stderr)
    _require_success(plan.rtrace_command, rtrace_result, "rtrace scalar transport")
    _require_nonempty(paths.raw_rtrace, "raw rtrace output")
    if plan.ambient_cache_enabled:
        _require_nonempty(paths.ambient_cache, "dedicated HPS ambient cache")
    values = decode_hps_scalar_rgb_rows(
        paths.raw_rtrace.read_text(encoding="utf-8"), expected_count=len(plan.sensor_points)
    )
    samples = tuple(
        PpfdMapSample(point.x_m, point.y_m, point.z_m, value)
        for point, value in zip(plan.sensor_points, values, strict=True)
    )
    metrics = compute_hps_scalar_metrics(samples)
    npz_bytes = format_hps_ppfd_values_npz(samples)
    npz_hash = _sha256_bytes(npz_bytes)
    atomic_write_bytes(paths.ppfd_npz, npz_bytes)
    command_summary = _command_provenance_payload(
        plan, executables, (ies_result, oconv_result, rtrace_result)
    )
    _write_json_new(paths.command_provenance_summary, command_summary)
    scalar_summary = _scalar_summary_payload(
        plan,
        metrics,
        octree_hash=octree_hash,
        cache_hash=(
            _sha256_file(paths.ambient_cache) if plan.ambient_cache_enabled else None
        ),
        raw_rtrace_hash=_sha256_file(paths.raw_rtrace),
        npz_hash=npz_hash,
        executables=executables,
    )
    _write_json_new(paths.scalar_transport_summary, scalar_summary)
    return HpsScalarTransportResult(
        plan, samples, metrics, source_summary, scalar_summary, npz_hash
    )


def _materialize_empty_workspace(plan: HpsScalarTransportPlan) -> None:
    root = plan.paths.artifact_root
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise HpsScalarTransportError(
            f"HPS scalar artifact root must be absent or empty: {root}"
        )
    for directory in (
        plan.request.workspace,
        root,
        plan.paths.source_directory,
        plan.paths.scene_directory,
        plan.paths.native_directory,
        plan.paths.log_directory,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    _write_new(plan.paths.derived_ies, plan.source.derived_ies.text)
    if _sha256_file(plan.paths.derived_ies) != plan.source.derived_ies.sha256:
        raise HpsScalarTransportError("staged derived IES hash mismatch.")
    materialize_fixture_occlusion(plan.fixture_occlusion)
    _write_json_new(plan.paths.input_identity, _input_identity_payload(plan))


def _validate_planned_closure(plan: HpsScalarTransportPlan) -> None:
    count = len(plan.layout.fixtures)
    profile = build_hps_comparison_profile()
    modeled = profile.modeled_operating_point
    if len(plan.source.apertures) != count:
        raise HpsScalarTransportError("one-aperture-per-fixture closure failed.")
    if tuple(command.label for command in plan.commands) != (
        "convert_hps_unit_downward_flux_ies",
        "compile_hps_scalar_scene",
        "baseline_scalar_ppfd_rtrace",
    ):
        raise HpsScalarTransportError("HPS native command order is invalid.")
    if any(command.cwd is None or command.env for command in plan.commands):
        raise HpsScalarTransportError("HPS commands require explicit cwd and empty env.")
    if (
        plan.ies2rad_command.cwd != plan.paths.source_directory
        or plan.ies2rad_command.stdin_path is not None
        or plan.ies2rad_command.stdout_path is not None
        or plan.oconv_command.cwd != plan.paths.artifact_root
        or plan.oconv_command.stdout_path != plan.paths.octree
        or plan.oconv_command.stdout_mode != "binary"
        or plan.rtrace_command.cwd != plan.paths.artifact_root
        or plan.rtrace_command.stdin_path != plan.paths.sensor_rays
        or plan.rtrace_command.stdout_path != plan.paths.raw_rtrace
    ):
        raise HpsScalarTransportError("HPS native command stream/cwd closure failed.")
    if (
        tuple(plan.scene.scene.source_files) != plan.paths.final_scene_inputs
        or plan.paths.raw_converted_rad in plan.scene.scene.source_files
    ):
        raise HpsScalarTransportError("HPS baseline scene composition is invalid.")
    if (
        plan.layout.policy_id != HPS_LAYOUT_POLICY_ID
        or modeled.electrical_power_w_per_fixture != HPS_FIXTURE_POWER_W
        or modeled.par_ppe_umol_per_j != HPS_FIXTURE_PPE_UMOL_PER_J
        or modeled.par_ppf_umol_s_per_fixture != HPS_FIXTURE_PPF_UMOL_S
        or plan.source.carrier_scale.ies2rad_multiplier != HPS_RADIANCE_CARRIER_MULTIPLIER
        or plan.source.whole_plan_downward_ppf_umol_s != count * HPS_FIXTURE_PPF_UMOL_S
    ):
        raise HpsScalarTransportError("fixed HPS operating-point closure failed.")
    if not math.isclose(
        plan.source.derived_ies.derived_flux.downward_flux_cd_sr,
        1.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise HpsScalarTransportError("derived downward angular closure failed.")
    if plan.source.scientific_payload().get("post_trace_scale") is not None:
        raise HpsScalarTransportError("post-trace source scaling is prohibited.")
    if _is_default_10x10(plan) and (
        count != 4
        or count * HPS_FIXTURE_POWER_W != 4180.0
        or count * HPS_FIXTURE_PPF_UMOL_S != 7000.0
        or len(plan.sensor_points) != 441
    ):
        raise HpsScalarTransportError("default 10x10 scientific closure failed.")


def _validate_ies2rad_output_set(plan: HpsScalarTransportPlan) -> None:
    expected = {
        plan.paths.derived_ies.name,
        plan.paths.raw_converted_rad.name,
        plan.paths.converted_dat.name,
    }
    actual = {path.name for path in plan.paths.source_directory.iterdir()}
    if actual != expected:
        raise HpsScalarTransportError(
            f"ies2rad output set mismatch: expected {sorted(expected)}, got {sorted(actual)}."
        )
    _require_nonempty(plan.paths.raw_converted_rad, "converted RAD")
    _require_nonempty(plan.paths.converted_dat, "converted DAT")


def _validate_scene_inputs(plan: HpsScalarTransportPlan) -> None:
    if tuple(plan.scene.scene.source_files) != plan.paths.final_scene_inputs:
        raise HpsScalarTransportError("final scene source order is invalid.")
    if plan.paths.raw_converted_rad in plan.scene.scene.source_files:
        raise HpsScalarTransportError("raw ies2rad geometry entered the final scene.")
    for path in plan.paths.final_scene_inputs:
        _require_nonempty(path, "final scene input")
    aperture_text = _read_ascii(plan.paths.aperture_rad, "HPS apertures")
    if aperture_text.count(" polygon ") != len(plan.layout.fixtures):
        raise HpsScalarTransportError("fixture/aperture closure failed.")
    if aperture_text.count(f"{HPS_LIGHT_MODIFIER_ID} polygon ") != len(
        plan.layout.fixtures
    ):
        raise HpsScalarTransportError(
            "every HPS aperture must reuse the validated light definition."
        )
    prohibited = ("boxcorr", ".u\n", ".1\n", ".2\n", ".3\n", ".4\n", "smd", "conventional")
    if any(token in aperture_text.lower() for token in prohibited):
        raise HpsScalarTransportError("prohibited source geometry entered the final scene.")
    shared_text = _read_ascii(plan.paths.shared_angular_rad, "shared HPS source")
    if (
        shared_text.count(" brightdata ") != 1
        or shared_text.count(" light ") != 1
        or " polygon " in shared_text
        or shared_text.split().count("source.cal") != 1
        or shared_text.split().count("flatcorr") != 1
    ):
        raise HpsScalarTransportError("shared HPS source definition is invalid.")


def _adapt_shared_dat_reference(
    text: str, *, raw_dat_reference: str, execution_dat_reference: str
) -> str:
    if Path(execution_dat_reference).is_absolute() or ".." in Path(execution_dat_reference).parts:
        raise HpsScalarTransportError("adapted DAT reference must be run-root-relative.")
    raw_token = f" {raw_dat_reference} "
    if text.count(raw_token) != 1:
        raise HpsScalarTransportError(
            "validated source must contain exactly one raw DAT reference."
        )
    adapted = text.replace(raw_token, f" {execution_dat_reference} ", 1)
    if adapted.split().count(execution_dat_reference) != 1 or raw_dat_reference in adapted.split():
        raise HpsScalarTransportError("adapted shared source DAT reference is inconsistent.")
    return adapted


def _validate_runtime_dat_binding(
    plan: HpsScalarTransportPlan, *, expected_dat_sha256: str
) -> None:
    paths = plan.paths
    reference = paths.converted_dat_execution_reference
    if (
        plan.oconv_command.cwd != paths.artifact_root
        or plan.rtrace_command.cwd != paths.artifact_root
    ):
        raise HpsScalarTransportError("oconv and rtrace cwd must be the HPS run root.")
    expected_path = paths.converted_dat.resolve()
    if (
        (plan.oconv_command.cwd / reference).resolve() != expected_path
        or (plan.rtrace_command.cwd / reference).resolve() != expected_path
    ):
        raise HpsScalarTransportError(
            "planned DAT reference does not resolve to the validated DAT."
        )
    _require_nonempty(expected_path, "converted DAT before native command")
    if _sha256_file(expected_path) != expected_dat_sha256:
        raise HpsScalarTransportError(
            "converted DAT hash changed after validation and before trace."
        )
    shared_text = _read_ascii(paths.shared_angular_rad, "shared angular RAD")
    if shared_text.split().count(reference) != 1:
        raise HpsScalarTransportError(
            "shared angular RAD must reference the planned DAT exactly once."
        )
    if paths.raw_converted_rad in plan.scene.scene.source_files:
        raise HpsScalarTransportError("raw ies2rad geometry entered the final scene.")


def _source_conversion_payload(
    plan: HpsScalarTransportPlan,
    contract: NativeFlatcorrOutputContract,
    dat_metadata: Mapping[str, object],
) -> dict[str, object]:
    source = plan.source
    return {
        "schema_version": 1,
        "payload_type": "fspm_optics_hps_source_conversion",
        "success": True,
        "source_plan_id": source.source_plan_id,
        "hashes": {
            "original_ies_sha256": HPS_IES_SHA256,
            "derived_ies_sha256": _sha256_file(plan.paths.derived_ies),
            "raw_converted_rad_sha256": _sha256_file(plan.paths.raw_converted_rad),
            "converted_dat_sha256": _sha256_file(plan.paths.converted_dat),
        },
        "original_ies_resource": HPS_IES_RESOURCE_NAME,
        "original_flux_diagnostics": source.derived_ies.original_flux.to_payload(),
        "derived_downward_integral": source.derived_ies.derived_flux.downward_flux_cd_sr,
        "excluded_upward_ies_fraction": source.derived_ies.to_payload()[
            "excluded_upward_fraction"
        ],
        "carrier_derivation": source.carrier_scale.to_payload(),
        "ies2rad": {
            "argv": _normalized_argv(plan.ies2rad_command, plan.paths.artifact_root),
            "cwd": "source",
            "shell": False,
        },
        "converted_dat_contract": dict(dat_metadata),
        "native_contract": {
            "correction": "flatcorr",
            "canonical_cal_reference": contract.cal_reference,
            "neutral_rgb": list(contract.neutral_rgb),
            "flatcorr_factor": contract.aperture_brightdata_scale,
            "aperture_dimensions_m": {
                "length_x": contract.length_x_m,
                "width_y": contract.width_y_m,
                "height_z": 0.0,
            },
            "native_guard_offset_z_m": contract.native_polygon_z_m,
            "primitive_count": contract.primitive_count,
            "geometry_ids": list(contract.geometry_ids),
            "raw_dat_reference": contract.dat_reference,
            "execution_dat_reference": plan.paths.converted_dat_execution_reference,
            "raw_converted_geometry_enters_final_scene": False,
            "shared_source_definition_reused_by_all_apertures": True,
        },
        "fixture_count": len(source.apertures),
        "aperture_count": len(source.apertures),
        "fixture_occlusion": plan.fixture_occlusion.scientific_payload(),
        "claim_boundary": source.claim_boundary,
        "limitations": list(SOURCE_LIMITATIONS),
    }


def _scalar_summary_payload(
    plan: HpsScalarTransportPlan,
    metrics: HpsScalarMetrics,
    *,
    octree_hash: str,
    cache_hash: str | None,
    raw_rtrace_hash: str,
    npz_hash: str,
    executables: HpsScalarExecutables,
) -> dict[str, object]:
    count = len(plan.layout.fixtures)
    payload = {
        "schema_version": HPS_SCALAR_SCHEMA_VERSION,
        "payload_type": HPS_SCALAR_PAYLOAD_TYPE,
        "success": True,
        "room_model": production_room_model_payload(),
        "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
        "identities": {
            "transport": plan.transport_identity,
            "commands": plan.command_identity,
            "source": plan.source.source_plan_id,
            "fixture_occlusion": plan.fixture_occlusion.identity_sha256,
            "layout": plan.layout.layout_id,
            "room": plan.room_identity,
            "sensor": plan.sensor_identity,
            "quality": plan.quality_identity,
            "scene": plan.scene.scene_id,
            "octree": plan.octree_identity,
            "ambient_cache": plan.ambient_cache_identity,
            "result": plan.result_identity,
        },
        "metrics": metrics.to_payload(),
        "room_dimensions_m": {
            "requested": {"length": plan.request.room_length_m, "width": plan.request.room_width_m},
            "native_aligned": {
                "length_x": plan.room.length_m,
                "width_y": plan.room.width_m,
                "height_z": plan.room.height_m,
            },
            "axes_swapped": plan.layout.room_axes.axes_swapped,
            **(
                {}
                if plan.request.active_domain is None
                else {
                    "active_domain": plan.request.active_domain.to_payload()
                }
            ),
        },
        "layout": {
            "policy": plan.layout.policy_id,
            "fixture_count": count,
            "resolved_counts": {
                "columns_x": plan.layout.counts.columns_x,
                "rows_y": plan.layout.counts.rows_y,
            },
            "actual_wall_gaps_m": {
                "x": plan.layout.actual_wall_gap_x_m,
                "y": plan.layout.actual_wall_gap_y_m,
            },
        },
        "mount": {
            "reference_plane_z_m": plan.request.reference_plane_z_m,
            "mount_height_m": plan.request.mount_height_m,
            "aperture_plane_z_m": plan.layout.mount.aperture_plane_z_m,
            "definition": plan.layout.mount.definition,
        },
        "operating_point": {
            "fixed_full_output": True,
            "fixture_power_w": HPS_FIXTURE_POWER_W,
            "fixture_ppf_umol_s": HPS_FIXTURE_PPF_UMOL_S,
            "par_ppe_umol_per_j": HPS_FIXTURE_PPE_UMOL_PER_J,
            "total_tested_system_input_power_w": count * HPS_FIXTURE_POWER_W,
            "total_planned_ppf_umol_s": count * HPS_FIXTURE_PPF_UMOL_S,
            "initial_lamp_par_ppf_umol_s_per_fixture": HPS_FIXTURE_PPF_UMOL_S,
            "tested_system_input_power_w_per_fixture": HPS_FIXTURE_POWER_W,
            "computed_system_ppe_umol_per_j": HPS_FIXTURE_PPE_UMOL_PER_J,
            "nominal_lamp_class_power_w": HPS_NOMINAL_LAMP_CLASS_POWER_W,
            "nominal_lamp_class_is_provenance_only": True,
            "carrier_multiplier_per_fixture": HPS_RADIANCE_CARRIER_MULTIPLIER,
            "carrier_applied_exactly_once_before_trace": True,
            "post_trace_scale": None,
            "auto_dimming": False,
            "target_ppfd": None,
        },
        "radiance": {
            "quality_profile": plan.request.quality_profile,
            "options": [
                _relative_token(item, plan.paths.artifact_root)
                for item in plan.radiance_options
            ],
            "threads": plan.request.threads,
            "rtrace_version": executables.rtrace_version_text,
            "oconv_version": None,
            "oconv_version_basis": "unavailable; oconv -version is unsupported",
        },
        "hashes": {
            "octree_sha256": octree_hash,
            "ambient_cache_sha256": cache_hash,
            "raw_rtrace_sha256": raw_rtrace_hash,
            "ppfd_values_npz_sha256": npz_hash,
        },
        "artifacts": {
            "source_conversion_summary": "source_conversion_summary.json",
            "command_provenance_summary": "command_provenance_summary.json",
            "ppfd_values_npz": "ppfd_values.npz",
            "npz_array_order": list(PPFD_NPZ_ARRAY_ORDER),
        },
        "decode_basis": PPFD_CONVERSION_BASIS,
        "claim_boundary": plan.source.claim_boundary,
        "limitations": [
            *SOURCE_LIMITATIONS,
            "Horizontal room-plane scalar PPFD only; no plants, Rex optics, "
            "or absorbed metrics.",
        ],
    }
    if _is_default_10x10(plan) and (
        count != 4
        or payload["operating_point"]["total_tested_system_input_power_w"] != 4180.0
        or payload["operating_point"]["total_planned_ppf_umol_s"] != 7000.0
    ):
        raise HpsScalarTransportError("default 10x10 scientific closure failed.")
    return payload


def _command_provenance_payload(
    plan: HpsScalarTransportPlan,
    executables: HpsScalarExecutables,
    results: tuple[RunnerResult, RunnerResult, RunnerResult],
) -> dict[str, object]:
    commands = plan.commands
    return {
        "schema_version": 1,
        "success": True,
        "transport_identity": plan.transport_identity,
        "command_identity": plan.command_identity,
        "commands": [
            {
                "label": command.label,
                "argv": _normalized_argv(command, plan.paths.artifact_root),
                "stdin": _relative(command.stdin_path, plan.paths.artifact_root),
                "stdout": _relative(command.stdout_path, plan.paths.artifact_root),
                "stdout_mode": command.stdout_mode,
                "cwd": _relative(command.cwd, plan.paths.artifact_root),
                "env": {},
                "shell": False,
                "returncode": result.returncode,
                "success": result.success,
                "stderr_sha256": (
                    None
                    if result.stderr_path is None
                    else _sha256_file(result.stderr_path)
                ),
            }
            for command, result in zip(commands, results, strict=True)
        ],
        "resolved_executables": {
            "ies2rad": str(executables.ies2rad),
            "oconv": str(executables.oconv),
            "rtrace": str(executables.rtrace),
        },
        "radiance_version": {
            "canonical_rtrace_version": executables.rtrace_version_text,
            "oconv_version": None,
        },
    }


def _input_identity_payload(plan: HpsScalarTransportPlan) -> dict[str, object]:
    return {
        "schema_version": 2,
        "transport_identity": plan.transport_identity,
        "command_identity": plan.command_identity,
        "source_plan_id": plan.source.source_plan_id,
        "approved_resources": {
            "profile_id": plan.source.profile_id,
            "profile_sha256": plan.source.profile_sha256,
            "original_ies_resource": HPS_IES_RESOURCE_NAME,
            "original_ies_sha256": HPS_IES_SHA256,
            "relative_spd_resource": HPS_SPD_RESOURCE_NAME,
            "relative_spd_sha256": HPS_SPD_SHA256,
        },
        "layout_policy_id": HPS_LAYOUT_POLICY_ID,
        "layout_id": plan.layout.layout_id,
        "fixture_occlusion": plan.fixture_occlusion.scientific_payload(),
        **(
            {}
            if plan.request.active_domain is None
            else {"active_domain": plan.request.active_domain.to_payload()}
        ),
        "room_identity": plan.room_identity,
        "room_model": production_room_model_payload(),
        "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
        "sensor_identity": plan.sensor_identity,
        "quality_identity": plan.quality_identity,
        "octree_identity": plan.octree_identity,
        "ambient_cache_identity": plan.ambient_cache_identity,
        "result_identity": plan.result_identity,
    }


def _scene_manifest_payload(plan: HpsScalarTransportPlan) -> dict[str, object]:
    return {
        "schema_version": 2,
        "scene_id": plan.scene.scene_id,
        "octree_identity": plan.octree_identity,
        "room_model": production_room_model_payload(),
        "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
        "ordered_inputs": [
            {
                "role": role,
                "path": _relative(path, plan.paths.artifact_root),
                "sha256": _sha256_file(path),
            }
            for role, path in zip(
                (
                    "room",
                    "shared_angular_source",
                    "fixture_apertures",
                    "fixture_bodies",
                ),
                plan.paths.final_scene_inputs,
                strict=True,
            )
        ],
        "raw_ies2rad_geometry_included": False,
        "fixture_bodies_included": True,
        "fixture_occlusion_identity": plan.fixture_occlusion.identity_sha256,
        "plant_geometry_included": False,
        "foreign_emitters_included": False,
        "sensor_grid_is_scene_geometry": False,
    }


def _resolve_sensor_grid(
    request: HpsScalarTransportRequest, room: RoomDimensions
) -> SensorGridSpec:
    if isinstance(request.sensor_grid, SensorGridSpec):
        spec = request.sensor_grid
        if any(
            not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12)
            for actual, expected in (
                (spec.room.length_m, room.length_m),
                (spec.room.width_m, room.width_m),
                (spec.room.height_m, room.height_m),
                (spec.canopy_height_m, request.reference_plane_z_m),
            )
        ):
            raise ValueError("explicit sensor grid must match room and reference plane.")
        return spec
    adaptive = build_adaptive_sensor_grid(
        room, canopy_height_m=request.reference_plane_z_m, policy=request.sensor_grid
    )
    if not adaptive.axes_swapped:
        return adaptive.spec
    return SensorGridSpec(
        room=room,
        resolution_x=adaptive.spec.resolution_y,
        resolution_y=adaptive.spec.resolution_x,
        canopy_height_m=request.reference_plane_z_m,
        inset_m=adaptive.spec.inset_m,
        layout=adaptive.spec.layout,
    )


def decode_hps_scalar_rgb_rows(
    text: str, *, expected_count: int
) -> tuple[float, ...]:
    """Strictly decode ordered, finite, nonnegative equal-grey scalar rows."""
    return decode_exact_native_scalar_rgb_rows(
        text,
        expected_count=expected_count,
        error_factory=HpsScalarTransportError,
    )


def _write_failure_summary(plan: HpsScalarTransportPlan, exc: BaseException) -> None:
    if plan.paths.scalar_transport_summary.exists():
        return
    for successful_artifact in (
        plan.paths.source_conversion_summary,
        plan.paths.scalar_transport_summary,
        plan.paths.command_provenance_summary,
        plan.paths.ppfd_npz,
    ):
        try:
            successful_artifact.unlink(missing_ok=True)
        except OSError:
            pass
    payload = {
        "schema_version": 1,
        "success": False,
        "transport_identity": plan.transport_identity,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "successful_scientific_result_written": False,
    }
    try:
        atomic_write_text(plan.paths.failure_summary, _json_text(payload))
    except OSError:
        pass


def _write_new(path: Path, text: str) -> None:
    if path.exists():
        raise HpsScalarTransportError(f"refusing to overwrite artifact: {path}")
    atomic_write_text(path, text)


def _write_json_new(path: Path, payload: Mapping[str, Any]) -> None:
    _write_new(path, _json_text(payload))


def _require_success(
    command: CommandSpec, result: RunnerResult, label: str
) -> None:
    if (
        result.command_label != command.label
        or result.argv != command.argv
        or result.stdout_path != command.stdout_path
    ):
        raise HpsScalarTransportError(
            f"{label} runner record does not match its CommandSpec."
        )
    if not result.success:
        raise HpsScalarTransportError(result.failure_message or f"{label} failed.")


def _require_nonempty(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise HpsScalarTransportError(f"{label} is missing or empty: {path}")


def _read_ascii(path: Path, label: str) -> str:
    _require_nonempty(path, label)
    try:
        return path.read_text(encoding="ascii")
    except UnicodeDecodeError as exc:
        raise HpsScalarTransportError(f"{label} must be ASCII.") from exc


def _normalized_argv(command: CommandSpec, root: Path) -> list[str]:
    return [
        Path(token).name if index == 0 else _relative_token(token, root)
        for index, token in enumerate(command.argv)
    ]


def _scientific_command_payload(command: CommandSpec, root: Path) -> dict[str, object]:
    return {
        "role": command.label,
        "argv_after_executable": [_relative_token(token, root) for token in command.argv[1:]],
        "stdin": _relative(command.stdin_path, root),
        "stdout": _relative(command.stdout_path, root),
        "stdout_mode": command.stdout_mode,
        "cwd": _relative(command.cwd, root),
        "env": dict(sorted(command.env.items())),
        "shell": False,
    }


def _relative_token(token: str, root: Path) -> str:
    candidate = Path(token)
    return _relative(candidate, root) if candidate.is_absolute() else token


def _relative(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    try:
        return Path(path).relative_to(root).as_posix() or "."
    except ValueError:
        return str(path)


def _is_default_10x10(plan: HpsScalarTransportPlan) -> bool:
    return (
        plan.layout.policy_id == HPS_LAYOUT_POLICY_ID
        and (
            plan.request.active_domain is None
            or not plan.request.active_domain.enabled
        )
        and math.isclose(plan.request.room_length_m, 10.0 * FEET_TO_METERS, abs_tol=1e-12)
        and math.isclose(plan.request.room_width_m, 10.0 * FEET_TO_METERS, abs_tol=1e-12)
    )


def _reject_repository_workspace(workspace: Path) -> None:
    repo = _repository_root()
    if (
        repo is not None
        and (workspace == repo or repo in workspace.parents)
        and not is_default_managed_runtime_descendant(workspace, repo)
    ):
        raise ValueError("HPS runtime workspace must be outside the repository.")


def _repository_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "pyproject.toml").is_file():
            return parent
    return None


def _positive(name: str, value: float | int) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be positive.")
    return number


def _finite(name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number.")
    return number


def _json_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())
