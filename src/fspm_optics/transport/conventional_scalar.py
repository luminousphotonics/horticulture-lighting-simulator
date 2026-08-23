"""Native fixed-output Conventional scalar horizontal-PPFD transport.

The planning API is pure.  Native execution is explicit and remains behind the
existing :class:`CommandSpec` and injected runner boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Final, Mapping, Protocol

from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_FIXTURE_POWER_W,
    CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J,
    CONVENTIONAL_FIXTURE_PPF_UMOL_S,
    CONVENTIONAL_IES_RESOURCE_NAME,
    CONVENTIONAL_IES_SHA256,
    CONVENTIONAL_SPD_RESOURCE_NAME,
    CONVENTIONAL_SPD_SHA256,
    DEFAULT_MOUNT_HEIGHT_M,
    PRACTICAL_LAYOUT_POLICY,
    ROLLING_BENCH_LAYOUT_POLICY,
    ConventionalLayoutPlan,
    ConventionalRadianceSourcePlan,
    MountReferencePlaneSemantics,
    build_conventional_radiance_source_plan,
    canonical_conventional_global_dimming_factor,
    format_conventional_apertures_rad,
    plan_conventional_layout,
    validate_converted_ies_output,
)
from fspm_optics.fixtures.conventional_led.layout import LayoutPolicyName
from fspm_optics.fixtures.occlusion import (
    FixtureOcclusionPlan,
    compile_fixture_occlusion,
    materialize_fixture_occlusion,
    plan_fixture_occlusion,
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
from fspm_optics.radiance.options import radiance_options
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.radiance.versioning import probe_radiance_version
from fspm_optics.runtime_paths import is_default_managed_runtime_descendant
from fspm_optics.transport.basis.atomic import atomic_write_bytes, atomic_write_text
from fspm_optics.transport.conventional_scenes import (
    ConventionalTransportScenePlan,
    plan_conventional_baseline_scene,
)
from fspm_optics.transport.scalar_ppfd import (
    PPFD_CONVERSION_BASIS,
    PpfdMapSample,
)
from fspm_optics.transport.native_scalar_data import (
    compute_native_scalar_metric_values,
    decode_exact_native_scalar_rgb_rows,
    format_native_scalar_ppfd_npz,
    validate_native_scalar_dat,
)

CONVENTIONAL_SCALAR_SCHEMA_VERSION: Final = 4
CONVENTIONAL_SCALAR_PAYLOAD_TYPE: Final = (
    "fspm_optics_conventional_scalar_horizontal_ppfd"
)
DEFAULT_REFERENCE_PLANE_Z_M: Final = BASELINE_REFERENCE_PLANE_Z_M
DEFAULT_ROOM_LENGTH_FT: Final = 10.0
DEFAULT_ROOM_WIDTH_FT: Final = 10.0
QUALITY_PROFILES: Final = ("direct", "standard", "quality", "rigorous")
PPFD_NPZ_ARRAY_ORDER: Final = ("x_m", "y_m", "z_m", "ppfd_umol_m2_s")
SOURCE_LIMITATIONS: Final = (
    "The one-sided model excludes measured upward IES leakage.",
    "The Conventional LED IES supplies angular shape and footprint, not the modeled operating point.",
    "No post-trace 179 conversion, rescaling, or target matching is applied.",
    "Optional application dimming is one global ies2rad carrier factor before trace.",
)


class ConventionalScalarTransportError(RuntimeError):
    """A planned Conventional native scalar run failed a scientific gate."""


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
class ConventionalScalarExecutables:
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
class ConventionalScalarTransportRequest:
    """All user-controlled inputs for one fixed-output scalar transport run."""

    workspace: Path
    room_length_m: float
    room_width_m: float
    layout_policy: LayoutPolicyName = PRACTICAL_LAYOUT_POLICY
    reference_plane_z_m: float = DEFAULT_REFERENCE_PLANE_Z_M
    mount_height_m: float = DEFAULT_MOUNT_HEIGHT_M
    global_dimming_factor: float = 1.0
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
        workspace = Path(self.workspace).expanduser().resolve()
        _reject_repository_workspace(workspace)
        object.__setattr__(self, "workspace", workspace)
        for name in ("room_length_m", "room_width_m", "mount_height_m"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        reference = _finite("reference_plane_z_m", self.reference_plane_z_m)
        if reference < 0.0:
            raise ValueError("reference_plane_z_m must be non-negative.")
        object.__setattr__(self, "reference_plane_z_m", reference)
        object.__setattr__(
            self,
            "global_dimming_factor",
            canonical_conventional_global_dimming_factor(
                _closed_unit_interval(
                    "global_dimming_factor", self.global_dimming_factor
                )
            ),
        )
        if reference + self.mount_height_m >= DEFAULT_ROOM_HEIGHT_M:
            raise ValueError("the aperture plane must lie below the fixed room ceiling.")
        if self.layout_policy not in (
            "practical",
            "full_fit",
            ROLLING_BENCH_LAYOUT_POLICY,
        ):
            raise ValueError(
                "layout_policy must be 'practical', 'full_fit', or "
                "'rolling_bench'."
            )
        quality = str(self.quality_profile).strip().lower()
        if quality not in QUALITY_PROFILES:
            raise ValueError(f"quality_profile must be one of {QUALITY_PROFILES!r}.")
        object.__setattr__(self, "quality_profile", quality)
        if isinstance(self.threads, bool) or not isinstance(self.threads, int) or self.threads <= 0:
            raise ValueError("threads must be a positive integer.")
        for name in ("ies2rad_command", "oconv_command", "rtrace_command"):
            if not str(getattr(self, name)):
                raise ValueError(f"{name} must be non-empty.")
        if not isinstance(self.sensor_grid, (SensorGridSpec, AdaptiveSensorGridPolicy)):
            raise ValueError(
                "sensor_grid must be a SensorGridSpec or AdaptiveSensorGridPolicy."
            )
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
    ) -> "ConventionalScalarTransportRequest":
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
    ) -> "ConventionalScalarTransportRequest":
        return cls(
            workspace=Path(workspace),
            room_length_m=room_length_m,
            room_width_m=room_width_m,
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class ConventionalScalarWorkspacePlan:
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
            raise ValueError(
                "runtime resource must be contained by the Conventional run root."
            ) from exc
        if not relative.parts or ".." in relative.parts:
            raise ValueError("runtime resource reference is unsafe.")
        return relative.as_posix()

    @property
    def converted_dat_execution_reference(self) -> str:
        return self.run_root_relative_reference(self.converted_dat)


@dataclass(frozen=True, slots=True)
class ConventionalScalarTransportPlan:
    request: ConventionalScalarTransportRequest
    paths: ConventionalScalarWorkspacePlan
    layout: ConventionalLayoutPlan
    source: ConventionalRadianceSourcePlan
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
    scene: ConventionalTransportScenePlan
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


@dataclass(frozen=True, slots=True)
class ConventionalScalarMetrics:
    sensor_count: int
    mean_ppfd_umol_m2_s: float
    minimum_ppfd_umol_m2_s: float
    maximum_ppfd_umol_m2_s: float
    standard_deviation_ppfd_umol_m2_s: float
    coefficient_of_variation: float
    coefficient_of_variation_percent: float
    minimum_to_mean_uniformity: float

    def to_payload(self) -> dict[str, float | int]:
        return {
            "sensor_count": self.sensor_count,
            "mean_ppfd_umol_m2_s": self.mean_ppfd_umol_m2_s,
            "minimum_ppfd_umol_m2_s": self.minimum_ppfd_umol_m2_s,
            "maximum_ppfd_umol_m2_s": self.maximum_ppfd_umol_m2_s,
            "standard_deviation_ppfd_umol_m2_s": (
                self.standard_deviation_ppfd_umol_m2_s
            ),
            "coefficient_of_variation": self.coefficient_of_variation,
            "coefficient_of_variation_percent": self.coefficient_of_variation_percent,
            "minimum_to_mean_uniformity": self.minimum_to_mean_uniformity,
        }


@dataclass(frozen=True, slots=True)
class ConventionalScalarTransportResult:
    plan: ConventionalScalarTransportPlan
    samples: tuple[PpfdMapSample, ...]
    metrics: ConventionalScalarMetrics
    source_conversion_summary: Mapping[str, Any]
    scalar_transport_summary: Mapping[str, Any]
    ppfd_npz_sha256: str


def plan_conventional_scalar_workspace(
    request: ConventionalScalarTransportRequest,
) -> ConventionalScalarWorkspacePlan:
    root = request.workspace / "conventional_scalar"
    source = root / "source"
    scene = root / "scene"
    native = root / "native"
    logs = root / "logs"
    return ConventionalScalarWorkspacePlan(
        workspace=request.workspace,
        artifact_root=root,
        source_directory=source,
        scene_directory=scene,
        native_directory=native,
        log_directory=logs,
        input_identity=root / "input_identity.json",
        derived_ies=source / "conventional_led_unit_downward_flux.ies",
        raw_converted_rad=source / "conventional_led_unit_downward_flux.rad",
        converted_dat=source / "conventional_led_unit_downward_flux.dat",
        shared_angular_rad=source / "conventional_shared_angular.rad",
        aperture_rad=scene / "conventional_apertures.rad",
        room_rad=scene / "room.rad",
        sensor_rays=scene / "horizontal_sensor_grid.pts",
        scene_manifest=scene / "scene_inputs.json",
        octree=native / "conventional_scalar.oct",
        ambient_cache=native / (
            "conventional_scalar."
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


def plan_conventional_scalar_transport(
    request: ConventionalScalarTransportRequest,
    *,
    executables: ConventionalScalarExecutables | None = None,
) -> ConventionalScalarTransportPlan:
    """Build all source, scene, command, and identity boundaries without writes."""

    paths = plan_conventional_scalar_workspace(request)
    domain = request.active_domain
    layout = plan_conventional_layout(
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
        policy=request.layout_policy,
        mount=MountReferencePlaneSemantics(
            reference_plane_z_m=request.reference_plane_z_m,
            mount_height_m=request.mount_height_m,
        ),
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
    bins = executables or ConventionalScalarExecutables(
        Path(request.ies2rad_command),
        Path(request.oconv_command),
        Path(request.rtrace_command),
    )
    fixture_occlusion = plan_fixture_occlusion(
        system_id="conventional",
        layout_identity=layout.to_payload(),
        output_directory=paths.scene_directory / "fixture_occlusion",
        oconv_bin=bins.oconv,
    )
    source = build_conventional_radiance_source_plan(
        layout,
        workspace=paths.source_directory,
        ies2rad_bin=bins.ies2rad,
        global_dimming_factor=request.global_dimming_factor,
        emitting_boundaries=fixture_occlusion.emitting_boundaries,
    )
    if source.ies2rad.expected_paths.derived_ies != paths.derived_ies:
        raise ConventionalScalarTransportError("derived IES workspace plan mismatch.")
    room_identity = "conventional-room-v2-" + _hash_payload(
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
    sensor_identity = "horizontal-sensor-grid-v1-" + _hash_payload(
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
    quality_identity = "radiance-quality-v1-" + _hash_payload(
        {"profile": request.quality_profile, "options": list(base_options)}
    )
    scene = plan_conventional_baseline_scene(
        source,
        room=paths.room_rad,
        room_identity=room_identity,
        shared_angular_source=paths.shared_angular_rad,
        fixture_apertures=paths.aperture_rad,
        fixture_bodies=fixture_occlusion.instance_source_path,
        horizontal_sensor_grid=paths.sensor_rays,
        sensor_grid_identity=sensor_identity,
    )
    transport_identity = "conventional-scalar-transport-v3-" + _hash_payload(
        {
            "source_plan_id": source.source_plan_id,
            "layout_id": layout.layout_id,
            "room_identity": room_identity,
            "sensor_identity": sensor_identity,
            "quality_identity": quality_identity,
            "threads": request.threads,
            "fixed_full_output": request.global_dimming_factor == 1.0,
            "global_source_dimming_factor": request.global_dimming_factor,
            "fixture_occlusion_identity": fixture_occlusion.identity_sha256,
        }
    )
    octree_identity = "conventional-scalar-octree-v2-" + _hash_payload(
        {
            "scene_id": scene.scene_id,
            "ordered_source_roles": [
                "room",
                "shared_angular_source",
                "fixture_apertures",
                "fixture_bodies",
            ],
            "fixture_occlusion_identity": fixture_occlusion.identity_sha256,
        }
    )
    ambient_identity = "conventional-scalar-ambient-v1-" + _hash_payload(
        {
            "octree_identity": octree_identity,
            "sensor_identity": sensor_identity,
            "quality_identity": quality_identity,
            "threads": request.threads,
            "enabled": request.quality_profile != "direct",
            **(
                {}
                if domain is None
                else {
                    "active_domain_identity_sha256": domain.identity_sha256
                }
            ),
        }
    )
    oconv = build_oconv_command(
        scene.scene.source_files,
        output_octree=paths.octree,
        cwd=paths.artifact_root,
        oconv_bin=bins.oconv,
        label="compile_conventional_scalar_scene",
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
    command_identity = "conventional-scalar-commands-v1-" + _hash_payload(
        {
            "ies2rad": _scientific_command_payload(
                source.ies2rad.command, paths.artifact_root
            ),
            "oconv": _scientific_command_payload(oconv, paths.artifact_root),
            "rtrace": _scientific_command_payload(rtrace, paths.artifact_root),
        }
    )
    result_identity = "conventional-scalar-result-v2-" + _hash_payload(
        {
            "transport_identity": transport_identity,
            "command_identity": command_identity,
            "octree_identity": octree_identity,
            "ambient_cache_identity": ambient_identity,
        }
    )
    plan = ConventionalScalarTransportPlan(
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


def resolve_conventional_scalar_executables(
    request: ConventionalScalarTransportRequest,
    *,
    resolver: ExecutableResolver = resolve_executable,
    version_probe: VersionProbe = probe_radiance_version,
) -> ConventionalScalarExecutables:
    """Resolve all native tools and probe only the supported rtrace boundary."""

    ies2rad = resolver(request.ies2rad_command, label="ies2rad")
    oconv = resolver(request.oconv_command, label="oconv")
    rtrace = resolver(request.rtrace_command, label="rtrace")
    try:
        version = version_probe(rtrace)
    except Exception:
        version = None
    return ConventionalScalarExecutables(ies2rad, oconv, rtrace, version)


def execute_conventional_scalar_transport(
    request: ConventionalScalarTransportRequest,
    runner: CommandRunner | None = None,
    *,
    executables: ConventionalScalarExecutables | None = None,
    resolver: ExecutableResolver = resolve_executable,
    version_probe: VersionProbe = probe_radiance_version,
) -> ConventionalScalarTransportResult:
    """Execute conversion, compilation, tracing, decoding, and artifact closure."""

    resolved = executables or resolve_conventional_scalar_executables(
        request, resolver=resolver, version_probe=version_probe
    )
    plan = plan_conventional_scalar_transport(request, executables=resolved)
    local_runner = runner or LocalRunner()
    _materialize_empty_workspace(plan)
    try:
        return _execute_materialized_plan(plan, local_runner, resolved)
    except BaseException as exc:
        _write_failure_summary(plan, exc)
        raise


def compute_conventional_scalar_metrics(
    samples: tuple[PpfdMapSample, ...],
) -> ConventionalScalarMetrics:
    values = compute_native_scalar_metric_values(samples)
    return ConventionalScalarMetrics(
        **{
            name: getattr(values, name)
            for name in ConventionalScalarMetrics.__dataclass_fields__
        }
    )


def format_ppfd_values_npz(samples: tuple[PpfdMapSample, ...]) -> bytes:
    return format_native_scalar_ppfd_npz(samples)


def _execute_materialized_plan(
    plan: ConventionalScalarTransportPlan,
    runner: CommandRunner,
    executables: ConventionalScalarExecutables,
) -> ConventionalScalarTransportResult:
    paths = plan.paths
    ies_result = runner.run(plan.ies2rad_command, stderr_path=paths.ies2rad_stderr)
    _require_success(ies_result, "ies2rad conversion")
    _validate_ies2rad_output_set(plan)
    raw_rad_text = _read_ascii(paths.raw_converted_rad, "converted RAD")
    dat_metadata = validate_converted_dat(paths.converted_dat.read_bytes())
    converted_dat_hash = _sha256_file(paths.converted_dat)
    contract = validate_converted_ies_output(
        raw_rad_text,
        expected_carrier_multiplier=plan.source.carrier_scale.ies2rad_multiplier,
        emitting_boundary_area_m2=(
            plan.source.emitting_boundary_area_m2_per_fixture
        ),
    )
    expected_flatcorr = (
        plan.source.carrier_scale.ies2rad_multiplier
        / plan.source.emitting_boundary_area_m2_per_fixture
    )
    if not math.isclose(
        contract.clean_aperture_brightdata_scale,
        expected_flatcorr,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ConventionalScalarTransportError("flatcorr aperture scale is invalid.")
    shared_definition_text = _adapt_shared_dat_reference(
        contract.shared_definition_text,
        raw_dat_reference=contract.dat_reference,
        execution_dat_reference=paths.converted_dat_execution_reference,
    )
    _write_new(paths.shared_angular_rad, shared_definition_text)
    _write_new(paths.aperture_rad, format_conventional_apertures_rad(plan.source))
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
    oconv_result = runner.run(plan.oconv_command, stderr_path=paths.oconv_stderr)
    _require_success(oconv_result, "oconv scene compilation")
    _require_nonempty(paths.octree, "compiled octree")
    octree_hash = _sha256_file(paths.octree)

    _validate_runtime_dat_binding(plan, expected_dat_sha256=converted_dat_hash)
    rtrace_result = runner.run(plan.rtrace_command, stderr_path=paths.rtrace_stderr)
    _require_success(rtrace_result, "rtrace scalar transport")
    _require_nonempty(paths.raw_rtrace, "raw rtrace output")
    if plan.ambient_cache_enabled:
        _require_nonempty(paths.ambient_cache, "dedicated Conventional ambient cache")
    raw_values = _parse_exact_rgb_rows(
        paths.raw_rtrace.read_text(encoding="utf-8"),
        expected_count=len(plan.sensor_points),
    )
    samples = tuple(
        PpfdMapSample(point.x_m, point.y_m, point.z_m, value)
        for point, value in zip(plan.sensor_points, raw_values, strict=True)
    )
    metrics = compute_conventional_scalar_metrics(samples)
    npz_bytes = format_ppfd_values_npz(samples)
    npz_hash = _sha256_bytes(npz_bytes)
    atomic_write_bytes(paths.ppfd_npz, npz_bytes)
    raw_rtrace_hash = _sha256_file(paths.raw_rtrace)
    cache_hash = (
        _sha256_file(paths.ambient_cache) if plan.ambient_cache_enabled else None
    )
    command_summary = _command_provenance_payload(
        plan,
        executables,
        (ies_result, oconv_result, rtrace_result),
    )
    _write_json_new(paths.command_provenance_summary, command_summary)
    scalar_summary = _scalar_summary_payload(
        plan,
        metrics,
        octree_hash=octree_hash,
        cache_hash=cache_hash,
        raw_rtrace_hash=raw_rtrace_hash,
        npz_hash=npz_hash,
        executables=executables,
    )
    _write_json_new(paths.scalar_transport_summary, scalar_summary)
    return ConventionalScalarTransportResult(
        plan=plan,
        samples=samples,
        metrics=metrics,
        source_conversion_summary=source_summary,
        scalar_transport_summary=scalar_summary,
        ppfd_npz_sha256=npz_hash,
    )


def validate_converted_dat(data: bytes) -> dict[str, object]:
    """Validate the deterministic two-dimensional Radiance data-table shape."""
    return validate_native_scalar_dat(
        data, error_factory=ConventionalScalarTransportError
    )


def _materialize_empty_workspace(plan: ConventionalScalarTransportPlan) -> None:
    root = plan.paths.artifact_root
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ConventionalScalarTransportError(
            f"Conventional scalar artifact root must be absent or empty: {root}"
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
        raise ConventionalScalarTransportError("staged derived IES hash mismatch.")
    materialize_fixture_occlusion(plan.fixture_occlusion)
    _write_json_new(plan.paths.input_identity, _input_identity_payload(plan))


def _validate_planned_closure(plan: ConventionalScalarTransportPlan) -> None:
    source = plan.source
    fixture_count = len(plan.layout.fixtures)
    modeled = source.declared_operating_point
    if len(source.apertures) != fixture_count * 8:
        raise ConventionalScalarTransportError(
            "eight-apertures-per-fixture closure failed."
        )
    if (
        source.carrier_scale.global_dimming_factor
        != plan.request.global_dimming_factor
        or source.whole_layout_transported_downward_ppf_umol_s
        != (
            fixture_count
            * source.transported_downward_ppf_umol_s_per_fixture
        )
        or modeled.electrical_power_w_per_fixture != CONVENTIONAL_FIXTURE_POWER_W
        or modeled.par_ppe_umol_per_j != CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J
        or modeled.par_ppf_umol_s_per_fixture != CONVENTIONAL_FIXTURE_PPF_UMOL_S
    ):
        raise ConventionalScalarTransportError("fixed Conventional operating-point closure failed.")
    if not math.isclose(
        source.derived_ies.derived_flux.downward_hemisphere_flux_cd_sr,
        1.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ConventionalScalarTransportError("derived downward angular closure failed.")
    scientific = source.scientific_payload()
    if scientific.get("post_trace_scale") is not None:
        raise ConventionalScalarTransportError("post-trace source scaling is prohibited.")
    if (
        _is_default_10x10(plan)
        and plan.request.global_dimming_factor == 1.0
        and (
        fixture_count != 4
        or source.whole_layout_transported_downward_ppf_umol_s
        != 4 * CONVENTIONAL_FIXTURE_PPF_UMOL_S
        or fixture_count * modeled.electrical_power_w_per_fixture
        != 4 * CONVENTIONAL_FIXTURE_POWER_W
        )
    ):
        raise ConventionalScalarTransportError("default 10x10 scientific closure failed.")


def _validate_ies2rad_output_set(plan: ConventionalScalarTransportPlan) -> None:
    expected = {
        plan.paths.derived_ies.name,
        plan.paths.raw_converted_rad.name,
        plan.paths.converted_dat.name,
    }
    actual = {path.name for path in plan.paths.source_directory.iterdir()}
    if actual != expected:
        raise ConventionalScalarTransportError(
            f"ies2rad output set mismatch: expected {sorted(expected)}, got {sorted(actual)}."
        )
    _require_nonempty(plan.paths.raw_converted_rad, "converted RAD")
    _require_nonempty(plan.paths.converted_dat, "converted DAT")


def _validate_scene_inputs(plan: ConventionalScalarTransportPlan) -> None:
    expected = plan.paths.final_scene_inputs
    if tuple(plan.scene.scene.source_files) != expected:
        raise ConventionalScalarTransportError("final scene source order is invalid.")
    if plan.paths.raw_converted_rad in expected:
        raise ConventionalScalarTransportError("raw ies2rad box geometry entered final scene.")
    for path in expected:
        _require_nonempty(path, "final scene input")
    aperture_text = plan.paths.aperture_rad.read_text(encoding="utf-8")
    if aperture_text.count(" polygon ") != len(plan.layout.fixtures) * 8:
        raise ConventionalScalarTransportError("fixture/aperture closure failed.")
    prohibited = ("!xform", "boxcorr", ".d\n", ".u\n", "smd")
    if any(token in aperture_text.lower() for token in prohibited):
        raise ConventionalScalarTransportError("prohibited source geometry entered final scene.")


def _adapt_shared_dat_reference(
    shared_definition_text: str,
    *,
    raw_dat_reference: str,
    execution_dat_reference: str,
) -> str:
    if not raw_dat_reference or not execution_dat_reference:
        raise ConventionalScalarTransportError("DAT references must be non-empty.")
    if Path(execution_dat_reference).is_absolute() or ".." in Path(
        execution_dat_reference
    ).parts:
        raise ConventionalScalarTransportError(
            "adapted DAT reference must be a safe run-root-relative path."
        )
    raw_token = f" {raw_dat_reference} "
    if shared_definition_text.count(raw_token) != 1:
        raise ConventionalScalarTransportError(
            "validated shared source does not contain exactly one raw DAT reference."
        )
    adapted = shared_definition_text.replace(
        raw_token,
        f" {execution_dat_reference} ",
        1,
    )
    tokens = adapted.split()
    if tokens.count(execution_dat_reference) != 1 or raw_dat_reference in tokens:
        raise ConventionalScalarTransportError(
            "adapted shared source DAT reference is inconsistent."
        )
    return adapted


def _validate_runtime_dat_binding(
    plan: ConventionalScalarTransportPlan,
    *,
    expected_dat_sha256: str,
) -> None:
    paths = plan.paths
    reference = paths.converted_dat_execution_reference
    if plan.oconv_command.cwd != paths.artifact_root:
        raise ConventionalScalarTransportError(
            "oconv cwd must be the Conventional run root."
        )
    if plan.rtrace_command.cwd != paths.artifact_root:
        raise ConventionalScalarTransportError(
            "rtrace cwd must be the Conventional run root."
        )
    resolved_from_oconv = (plan.oconv_command.cwd / reference).resolve()
    resolved_from_rtrace = (plan.rtrace_command.cwd / reference).resolve()
    expected_path = paths.converted_dat.resolve()
    if resolved_from_oconv != expected_path or resolved_from_rtrace != expected_path:
        raise ConventionalScalarTransportError(
            "planned DAT reference does not resolve to the validated converted DAT."
        )
    _require_nonempty(expected_path, "converted DAT before rtrace")
    if _sha256_file(expected_path) != expected_dat_sha256:
        raise ConventionalScalarTransportError(
            "converted DAT hash changed after validation and before rtrace."
        )
    shared_text = _read_ascii(paths.shared_angular_rad, "adapted shared angular RAD")
    tokens = shared_text.split()
    if tokens.count(reference) != 1:
        raise ConventionalScalarTransportError(
            "adapted shared angular RAD does not reference the planned DAT path."
        )
    if paths.raw_converted_rad in plan.scene.scene.source_files:
        raise ConventionalScalarTransportError(
            "raw ies2rad box geometry entered the final scene."
        )


def _source_conversion_payload(plan: ConventionalScalarTransportPlan, contract: Any, dat_metadata: Mapping[str, object]) -> dict[str, object]:
    source = plan.source
    flux = source.derived_ies.original_flux
    return {
        "schema_version": CONVENTIONAL_SCALAR_SCHEMA_VERSION,
        "payload_type": "fspm_optics_conventional_source_conversion",
        "success": True,
        "source_plan_id": source.source_plan_id,
        "hashes": {
            "original_ies_sha256": CONVENTIONAL_IES_SHA256,
            "derived_ies_sha256": _sha256_file(plan.paths.derived_ies),
            "raw_converted_rad_sha256": _sha256_file(plan.paths.raw_converted_rad),
            "converted_dat_sha256": _sha256_file(plan.paths.converted_dat),
        },
        "original_ies_resource": CONVENTIONAL_IES_RESOURCE_NAME,
        "authority": {
            "rated_fixture_power_w": CONVENTIONAL_FIXTURE_POWER_W,
            "full_output_par_ppe_umol_per_j": (
                CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J
            ),
            "full_output_par_ppf_umol_s_per_fixture": (
                CONVENTIONAL_FIXTURE_PPF_UMOL_S
            ),
            "tested_ies_input_w_provenance_only": (
                source.original_ies.tested_input_watts
            ),
            "ies_role": "downward_angular_distribution_footprint_orientation_and_test_provenance",
            "relative_spd_resource": CONVENTIONAL_SPD_RESOURCE_NAME,
            "relative_spd_sha256": CONVENTIONAL_SPD_SHA256,
            "spd_role": "relative_spectral_shape_only",
        },
        "original_flux_diagnostics": flux.to_payload(),
        "derived_downward_integral": source.derived_ies.derived_flux.downward_hemisphere_flux_cd_sr,
        "excluded_upward_fraction": flux.upward_fraction_of_full_sphere,
        "carrier_derivation": source.carrier_scale.to_payload(),
        "ies2rad": {
            "argv": _normalized_argv(plan.ies2rad_command, plan.paths.artifact_root),
            "cwd": "source",
            "shell": False,
        },
        "converted_dat_contract": dict(dat_metadata),
        "adaptation": {
            "converted_correction": "boxcorr",
            "final_correction": "flatcorr",
            "final_photometric_boundary": "illum",
            "flatcorr_factor": contract.clean_aperture_brightdata_scale,
            "emitting_boundary_area_m2": (
                source.emitting_boundary_area_m2_per_fixture
            ),
            "raw_box_footprint_area_m2": 1.190 * 1.087,
            "raw_dat_reference": contract.dat_reference,
            "execution_dat_reference": (
                plan.paths.converted_dat_execution_reference
            ),
            "raw_full_box_enters_final_scene": False,
        },
        "fixture_count": source.fixture_count,
        "aperture_count": len(source.apertures),
        "apertures_per_fixture": 8,
        "fixture_occlusion": plan.fixture_occlusion.scientific_payload(),
        "absolute_scale_exclusions": list(source.prohibited_absolute_scale_paths),
        "claim_boundary": source.claim_boundary,
        "limitations": list(SOURCE_LIMITATIONS),
    }


def _scalar_summary_payload(
    plan: ConventionalScalarTransportPlan,
    metrics: ConventionalScalarMetrics,
    *,
    octree_hash: str,
    cache_hash: str | None,
    raw_rtrace_hash: str,
    npz_hash: str,
    executables: ConventionalScalarExecutables,
) -> dict[str, object]:
    modeled = plan.source.declared_operating_point
    fixture_count = len(plan.layout.fixtures)
    dimming_factor = plan.request.global_dimming_factor
    effective_fixture_power_w = (
        modeled.electrical_power_w_per_fixture * dimming_factor
    )
    effective_fixture_ppf_umol_s = (
        plan.source.transported_downward_ppf_umol_s_per_fixture
    )
    operating_point: dict[str, object] = {
        "auto_dimming": False,
        "fixed_full_output": dimming_factor == 1.0,
        "rated_fixture_power_w": modeled.electrical_power_w_per_fixture,
        "tested_ies_input_w_provenance_only": plan.source.original_ies.tested_input_watts,
        "full_output_fixture_ppf_umol_s": modeled.par_ppf_umol_s_per_fixture,
        "full_output_par_ppe_umol_per_j": modeled.par_ppe_umol_per_j,
        "applied_global_dimming_factor": dimming_factor,
        "effective_fixture_power_w": effective_fixture_power_w,
        "effective_fixture_ppf_umol_s": effective_fixture_ppf_umol_s,
        "effective_par_ppe_umol_per_j": (
            effective_fixture_ppf_umol_s / effective_fixture_power_w
        ),
        "post_trace_scale": None,
        "target_ppfd": None,
        "full_output_total_power_w": (
            fixture_count * modeled.electrical_power_w_per_fixture
        ),
        "effective_total_power_w": fixture_count * effective_fixture_power_w,
        "full_output_total_ppf_umol_s": (
            fixture_count * modeled.par_ppf_umol_s_per_fixture
        ),
        "effective_total_ppf_umol_s": fixture_count * effective_fixture_ppf_umol_s,
        "dimming_stage": "ies2rad_carrier_before_trace",
        "post_trace_dimming": False,
    }
    payload = {
        "schema_version": CONVENTIONAL_SCALAR_SCHEMA_VERSION,
        "payload_type": CONVENTIONAL_SCALAR_PAYLOAD_TYPE,
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
            "requested": {
                "length": plan.request.room_length_m,
                "width": plan.request.room_width_m,
            },
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
            "policy": plan.layout.policy.name,
            **(
                {
                    "placement_provenance": (
                        plan.layout.placement_provenance_payload()
                    )
                }
                if plan.layout.policy.name == ROLLING_BENCH_LAYOUT_POLICY
                else {}
            ),
            "fixture_count": fixture_count,
            "resolved_counts": {
                "columns_x": plan.layout.resolved_counts.columns_x,
                "rows_y": plan.layout.resolved_counts.rows_y,
            },
            "target_practical_gaps_m": None if plan.layout.target_practical_gaps_m is None else {
                "x": plan.layout.target_practical_gaps_m.x_m,
                "y": plan.layout.target_practical_gaps_m.y_m,
            },
            "actual_centered_gaps_m": {
                "x": plan.layout.actual_centered_gaps_m.x_m,
                "y": plan.layout.actual_centered_gaps_m.y_m,
            },
        },
        "mount": {
            "reference_plane_z_m": plan.request.reference_plane_z_m,
            "mount_height_m": plan.request.mount_height_m,
            "aperture_plane_z_m": plan.layout.mount.aperture_z_m,
            "definition": plan.layout.mount.mount_height_definition,
        },
        "operating_point": operating_point,
        "source_authorities": {
            "ies": {
                "resource": CONVENTIONAL_IES_RESOURCE_NAME,
                "sha256": CONVENTIONAL_IES_SHA256,
                "role": "downward_angular_distribution_footprint_orientation_and_test_provenance",
                "tested_input_w_provenance_only": plan.source.original_ies.tested_input_watts,
                "excluded_upward_fraction": (
                    plan.source.derived_ies.original_flux.upward_fraction_of_full_sphere
                ),
            },
            "spd": {
                "resource": CONVENTIONAL_SPD_RESOURCE_NAME,
                "sha256": CONVENTIONAL_SPD_SHA256,
                "role": "relative_spectral_shape_only",
                "sets_absolute_output": False,
            },
            "carrier": plan.source.carrier_scale.to_payload(),
        },
        "radiance": {
            "quality_profile": plan.request.quality_profile,
            "options": [
                _relative_token(option, plan.paths.artifact_root)
                for option in plan.radiance_options
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
            "Horizontal room-plane PPFD only; no plants or plant-response claims.",
            "No Proposed SMD comparison is made by this artifact.",
        ],
    }
    if _is_default_10x10(plan) and (
        fixture_count != 4
        or payload["operating_point"]["full_output_total_power_w"]
        != 4 * CONVENTIONAL_FIXTURE_POWER_W
        or not math.isclose(
            payload["operating_point"]["effective_total_ppf_umol_s"],
            4 * CONVENTIONAL_FIXTURE_PPF_UMOL_S * dimming_factor,
            rel_tol=1e-15,
            abs_tol=1e-12,
        )
    ):
        raise ConventionalScalarTransportError("default 10x10 scientific closure failed.")
    return payload


def _command_provenance_payload(
    plan: ConventionalScalarTransportPlan,
    executables: ConventionalScalarExecutables,
    results: tuple[RunnerResult, RunnerResult, RunnerResult],
) -> dict[str, object]:
    commands = (plan.ies2rad_command, plan.oconv_command, plan.rtrace_command)
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
                "stderr_sha256": None if result.stderr_path is None else _sha256_file(result.stderr_path),
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


def _input_identity_payload(plan: ConventionalScalarTransportPlan) -> dict[str, object]:
    return {
        "schema_version": 2,
        "transport_identity": plan.transport_identity,
        "command_identity": plan.command_identity,
        "source_plan_id": plan.source.source_plan_id,
        "approved_resources": {
            "profile_id": plan.source.profile_id,
            "profile_sha256": plan.source.profile_sha256,
            "original_ies_resource": CONVENTIONAL_IES_RESOURCE_NAME,
            "original_ies_sha256": CONVENTIONAL_IES_SHA256,
            "relative_spd_resource": CONVENTIONAL_SPD_RESOURCE_NAME,
            "relative_spd_sha256": CONVENTIONAL_SPD_SHA256,
        },
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
        "global_source_dimming_factor": plan.request.global_dimming_factor,
        "post_trace_scale": None,
        "octree_identity": plan.octree_identity,
        "ambient_cache_identity": plan.ambient_cache_identity,
        "result_identity": plan.result_identity,
    }


def _scene_manifest_payload(plan: ConventionalScalarTransportPlan) -> dict[str, object]:
    return {
        "schema_version": 2,
        "scene_id": plan.scene.scene_id,
        "octree_identity": plan.octree_identity,
        "room_model": production_room_model_payload(),
        "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
        "ordered_inputs": [
            {"role": role, "path": _relative(path, plan.paths.artifact_root), "sha256": _sha256_file(path)}
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
        "raw_ies2rad_box_included": False,
        "fixture_bodies_included": True,
        "fixture_occlusion_identity": plan.fixture_occlusion.identity_sha256,
        "sensor_grid_is_scene_geometry": False,
    }


def _resolve_sensor_grid(
    request: ConventionalScalarTransportRequest,
    room: RoomDimensions,
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
        room,
        canopy_height_m=request.reference_plane_z_m,
        policy=request.sensor_grid,
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


def _parse_exact_rgb_rows(text: str, *, expected_count: int) -> tuple[float, ...]:
    return decode_exact_native_scalar_rgb_rows(
        text,
        expected_count=expected_count,
        error_factory=ConventionalScalarTransportError,
    )


def _write_failure_summary(plan: ConventionalScalarTransportPlan, exc: BaseException) -> None:
    if plan.paths.scalar_transport_summary.exists():
        return
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
        raise ConventionalScalarTransportError(f"refusing to overwrite artifact: {path}")
    atomic_write_text(path, text)


def _write_json_new(path: Path, payload: Mapping[str, Any]) -> None:
    _write_new(path, _json_text(payload))


def _require_success(result: RunnerResult, label: str) -> None:
    if not result.success:
        raise ConventionalScalarTransportError(
            result.failure_message or f"{label} failed."
        )


def _require_nonempty(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ConventionalScalarTransportError(f"{label} is missing or empty: {path}")


def _read_ascii(path: Path, label: str) -> str:
    _require_nonempty(path, label)
    try:
        return path.read_text(encoding="ascii")
    except UnicodeDecodeError as exc:
        raise ConventionalScalarTransportError(f"{label} must be ASCII.") from exc


def _normalized_argv(command: CommandSpec, root: Path) -> list[str]:
    return [Path(token).name if index == 0 else (_relative_token(token, root)) for index, token in enumerate(command.argv)]


def _scientific_command_payload(command: CommandSpec, root: Path) -> dict[str, object]:
    return {
        "role": command.label,
        "argv_after_executable": [
            _relative_token(token, root) for token in command.argv[1:]
        ],
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


def _is_default_10x10(plan: ConventionalScalarTransportPlan) -> bool:
    return (
        plan.layout.policy.name == "practical"
        and (
            plan.request.active_domain is None
            or not plan.request.active_domain.enabled
        )
        and math.isclose(plan.room.length_m, 10.0 * FEET_TO_METERS, abs_tol=1e-12)
        and math.isclose(plan.room.width_m, 10.0 * FEET_TO_METERS, abs_tol=1e-12)
    )


def _reject_repository_workspace(workspace: Path) -> None:
    repo = _repository_root()
    if (
        repo is not None
        and (workspace == repo or repo in workspace.parents)
        and not is_default_managed_runtime_descendant(workspace, repo)
    ):
        raise ValueError("Conventional runtime workspace must be outside the repository.")


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


def _closed_unit_interval(name: str, value: float | int) -> float:
    number = _positive(name, value)
    if number > 1.0:
        raise ValueError(f"{name} must be no greater than 1.")
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
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())
