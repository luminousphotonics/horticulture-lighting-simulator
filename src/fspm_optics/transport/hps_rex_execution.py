"""Native six-run HPS Rex incident transport."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Final, Mapping, Protocol, Sequence

import numpy as np

from fspm_optics.fixtures.hps import (
    HPS_DEFAULT_MOUNT_HEIGHT_M,
    HpsFixturePlacement,
    HpsRadianceSourcePlan,
    build_hps_radiance_source_plan,
    format_hps_spectral_source_payload_json,
    plan_hps_layout,
    validate_converted_hps_ies_output,
)
from fspm_optics.fixtures.occlusion import (
    FixtureOcclusionPlan,
    compile_fixture_occlusion,
    materialize_fixture_occlusion,
    validate_compiled_fixture_occlusion,
)
from fspm_optics.geometry.room import (
    FEET_TO_METERS,
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    RoomDimensions,
    production_room_model_payload,
    room_radiance_text,
)
from fspm_optics.optics.hps import (
    format_hps_rex_material_plan_json,
    format_hps_rex_weighted_atr_json,
)
from fspm_optics.plants.generator import generate_rex_butterhead_plant
from fspm_optics.plants.radiance_export import export_plant_mesh_to_radiance
from fspm_optics.plants.rex import RexPlantConfig
from fspm_optics.radiance.commands import (
    LOCAL_DEFAULT_NTHREADS,
    CommandSpec,
    build_oconv_command,
    build_plant_receiver_rtrace_command,
)
from fspm_optics.radiance.executables import resolve_executable
from fspm_optics.radiance.native_ies import NativeFlatcorrOutputContract
from fspm_optics.radiance.options import radiance_options
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.radiance.versioning import probe_radiance_version
from fspm_optics.runtime_paths import is_default_managed_runtime_descendant
from fspm_optics.receivers.samples import (
    MeshPatchReceiverSample,
    build_two_sided_patch_receivers,
    receiver_sample_input_text,
)
from fspm_optics.transport.basis.atomic import (
    atomic_save_npy,
    atomic_write_bytes,
    atomic_write_text,
)
from fspm_optics.transport.hps import (
    HPS_REX_RUN_ORDER,
    HpsIsolatedRunPlan,
    HpsIsolatedTransportBundlePlan,
    format_hps_isolated_run_source_rad,
    format_hps_isolated_transport_bundle_json,
    plan_hps_isolated_transport_bundle,
)
from fspm_optics.transport.hps_scalar import (
    DEFAULT_REFERENCE_PLANE_Z_M,
    DEFAULT_ROOM_HEIGHT_M,
    QUALITY_PROFILES,
    validate_hps_converted_dat,
)
from fspm_optics.transport.native_rex_data import (
    FloatArray,
    NativeIncidentComparisonValues,
    NativeRexReceiverGroups,
    build_native_rex_receiver_groups,
    compute_native_incident_metrics,
    compute_native_scalar_four_band_diagnostics,
    decode_native_rex_receiver_rgb,
    format_native_rex_incident_npz,
)

HPS_REX_EXECUTION_SCHEMA_VERSION: Final = 2
HPS_REX_EXECUTION_PAYLOAD_TYPE: Final = (
    "fspm_optics_hps_rex_incident_transport"
)
HPS_REX_RUNTIME_ROOT: Final = "hps_rex_transport"
HPS_REX_NATIVE_COMMAND_COUNT: Final = 13
HPS_REX_INCIDENT_ARRAY_ORDER: Final = tuple(
    f"{interval_id}_umol_m2_s" for interval_id in HPS_REX_RUN_ORDER
)
HPS_REX_EXECUTION_LIMITATIONS: Final = (
    "Incident photon transport only; absorbed-photon calculation is out of scope.",
    "Scalar PAR is diagnostic and is not added to four-band PAR.",
    "Far-red remains separate from PAR.",
    "No post-trace conversion, spectral scaling, reconciliation, or symmetrization.",
)


class HpsRexTransportError(RuntimeError):
    """The native HPS Rex workflow failed a scientific or runtime gate."""


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
class HpsRexExecutables:
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
class HpsRexTransportRequest:
    workspace: Path
    room_length_m: float
    room_width_m: float
    threads: int = LOCAL_DEFAULT_NTHREADS
    quality_profile: str = "standard"
    reference_plane_z_m: float = DEFAULT_REFERENCE_PLANE_Z_M
    mount_height_m: float = HPS_DEFAULT_MOUNT_HEIGHT_M

    def __post_init__(self) -> None:
        raw = Path(self.workspace).expanduser()
        if not raw.is_absolute():
            raise ValueError("workspace must be an absolute path.")
        workspace = raw.resolve()
        _reject_repository_workspace(workspace)
        object.__setattr__(self, "workspace", workspace)
        for name in ("room_length_m", "room_width_m", "mount_height_m"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        reference = _finite("reference_plane_z_m", self.reference_plane_z_m)
        if reference < 0.0:
            raise ValueError("reference_plane_z_m must be non-negative.")
        object.__setattr__(self, "reference_plane_z_m", reference)
        if reference + self.mount_height_m >= DEFAULT_ROOM_HEIGHT_M:
            raise ValueError("the aperture plane must remain below the room ceiling.")
        if isinstance(self.threads, bool) or not isinstance(self.threads, int) or self.threads <= 0:
            raise ValueError("threads must be a positive integer.")
        quality = str(self.quality_profile).strip().lower()
        if quality not in QUALITY_PROFILES:
            raise ValueError(f"quality_profile must be one of {QUALITY_PROFILES!r}.")
        object.__setattr__(self, "quality_profile", quality)

    @classmethod
    def from_feet(
        cls,
        *,
        workspace: str | Path,
        room_length_ft: float = 10.0,
        room_width_ft: float = 10.0,
        **kwargs: Any,
    ) -> "HpsRexTransportRequest":
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
    ) -> "HpsRexTransportRequest":
        return cls(
            workspace=Path(workspace),
            room_length_m=room_length_m,
            room_width_m=room_width_m,
            **kwargs,
        )

    def scientific_payload(self) -> dict[str, object]:
        return {
            "room_dimensions_m": [self.room_length_m, self.room_width_m],
            "threads": self.threads,
            "quality_profile": self.quality_profile,
            "reference_plane_z_m": self.reference_plane_z_m,
            "mount_height_m": self.mount_height_m,
            "layout_policy": "coverage_4ft_center_pitch_v1",
            "plant_policy": "fixed_mature_rex_seed_1_default_4x4_patches",
        }


@dataclass(frozen=True, slots=True)
class HpsRexRunPaths:
    directory: Path
    source_rad: Path
    plant_rad: Path
    scene_manifest: Path
    octree: Path
    ambient_cache: Path
    raw_rgb: Path
    decoded_npy: Path
    oconv_stderr: Path
    rtrace_stderr: Path


@dataclass(frozen=True, slots=True)
class HpsRexWorkspace:
    workspace: Path
    artifact_root: Path
    source_directory: Path
    shared_directory: Path
    receiver_directory: Path
    log_directory: Path
    input_identity: Path
    bundle_manifest: Path
    execution_plan_summary: Path
    source_payload_json: Path
    optical_payload_json: Path
    material_plan_json: Path
    source_conversion_summary: Path
    command_provenance_summary: Path
    incident_transport_summary: Path
    incident_receiver_values_npz: Path
    failure_summary: Path
    derived_ies: Path
    raw_converted_rad: Path
    converted_dat: Path
    room_rad: Path
    generic_plant_rad: Path
    receiver_rays: Path
    receiver_metadata: Path
    ies2rad_stderr: Path

    @property
    def converted_dat_execution_reference(self) -> str:
        return self.converted_dat.relative_to(self.artifact_root).as_posix()


@dataclass(frozen=True, slots=True)
class HpsRexNativeRunPlan:
    part25b: HpsIsolatedRunPlan
    paths: HpsRexRunPaths
    native_scene_id: str
    source_text: str
    source_text_sha256: str
    plant_text: str
    plant_text_sha256: str
    oconv_command: CommandSpec
    rtrace_command: CommandSpec

    @property
    def interval_id(self) -> str:
        return self.part25b.interval_id


@dataclass(frozen=True, slots=True)
class HpsRexExecutionPlan:
    request: HpsRexTransportRequest
    paths: HpsRexWorkspace
    bundle: HpsIsolatedTransportBundlePlan
    source_plan: HpsRadianceSourcePlan
    room_text: str
    generic_plant_text: str
    receiver_samples: tuple[MeshPatchReceiverSample, ...]
    receiver_text: str
    receiver_metadata_text: str
    receiver_metadata_sha256: str
    ies2rad_command: CommandSpec
    runs: tuple[HpsRexNativeRunPlan, ...]
    execution_plan_id: str

    @property
    def fixture_occlusion(self) -> FixtureOcclusionPlan:
        return self.bundle.fixture_occlusion

    @property
    def commands(self) -> tuple[CommandSpec, ...]:
        return (
            self.ies2rad_command,
            *(command for run in self.runs for command in (run.oconv_command, run.rtrace_command)),
        )

    def scientific_payload(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "payload_type": HPS_REX_EXECUTION_PAYLOAD_TYPE,
            "request": self.request.scientific_payload(),
            "bundle_id": self.bundle.bundle_id,
            "source_plan_id": self.source_plan.source_plan_id,
            "fixture_occlusion": self.fixture_occlusion.scientific_payload(),
            "room_identity": self.bundle.room_identity,
            "room_model": production_room_model_payload(),
            "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
            "plant_identity": self.bundle.plant_identity,
            "receiver_identity": self.bundle.receiver_identity,
            "receiver_metadata_sha256": self.receiver_metadata_sha256,
            "shared_angular_dat_identity": self.bundle.shared_angular_dat_identity,
            "run_order": list(HPS_REX_RUN_ORDER),
            "run_bindings": [
                {
                    "interval_id": run.interval_id,
                    "source_definition_id": run.part25b.source_definition_id,
                    "material_identifier": run.part25b.material.material_identifier,
                    "per_fixture_ppf_umol_s": run.part25b.per_fixture_ppf_umol_s,
                    "carrier_multiplier": run.part25b.carrier_multiplier,
                    "source_text_sha256": run.source_text_sha256,
                    "plant_text_sha256": run.plant_text_sha256,
                }
                for run in self.runs
            ],
            "native_command_count": HPS_REX_NATIVE_COMMAND_COUNT,
            "absorbed_photon_processing": False,
        }


@dataclass(frozen=True, slots=True)
class HpsIncidentRunMetrics:
    interval_id: str
    units: str
    receiver_count: int
    front_receiver_count: int
    back_receiver_count: int
    physical_patch_count: int
    minimum_incident_pfd: float
    maximum_incident_pfd: float
    front_area_weighted_mean_incident_pfd: float
    back_area_weighted_mean_incident_pfd: float
    combined_area_weighted_mean_incident_pfd: float
    whole_plant_incident_photon_flux_umol_s: float

    def __post_init__(self) -> None:
        if self.interval_id not in (*HPS_REX_RUN_ORDER, "four_band_par"):
            raise ValueError("HPS incident metric interval is invalid.")
        expected_units = (
            "far_red_photon_flux_density_umol_m2_s"
            if self.interval_id == "far_red"
            else "PAR_photon_flux_density_umol_m2_s"
        )
        if self.units != expected_units:
            raise ValueError("HPS incident metric units are invalid.")
        if (
            self.receiver_count,
            self.front_receiver_count,
            self.back_receiver_count,
            self.physical_patch_count,
        ) != (1024, 512, 512, 512):
            raise ValueError("HPS incident metrics require the fixed receiver contract.")
        numeric = (
            self.minimum_incident_pfd,
            self.maximum_incident_pfd,
            self.front_area_weighted_mean_incident_pfd,
            self.back_area_weighted_mean_incident_pfd,
            self.combined_area_weighted_mean_incident_pfd,
            self.whole_plant_incident_photon_flux_umol_s,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in numeric):
            raise ValueError("HPS incident metric values must be finite and nonnegative.")

    def to_payload(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class HpsIncidentComparison:
    scope: str
    scalar_area_weighted_mean: float
    four_band_area_weighted_mean: float
    signed_difference: float
    absolute_difference: float
    relative_difference: float | None

    def __post_init__(self) -> None:
        if not all(
            math.isfinite(value)
            for value in (
                self.scalar_area_weighted_mean,
                self.four_band_area_weighted_mean,
                self.signed_difference,
                self.absolute_difference,
            )
        ) or self.absolute_difference < 0.0:
            raise ValueError("HPS incident comparison values are invalid.")
        if not math.isclose(
            abs(self.signed_difference),
            self.absolute_difference,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("signed and absolute incident differences diverged.")

    def to_payload(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class HpsScalarFourBandDiagnostics:
    front: HpsIncidentComparison
    back: HpsIncidentComparison
    combined: HpsIncidentComparison
    receiver_level_rmse: float
    receiver_level_relative_rmse: float | None
    reconciliation_scale_applied: bool = False
    scientific_pass_threshold_applied: bool = False

    def __post_init__(self) -> None:
        if not math.isfinite(self.receiver_level_rmse) or self.receiver_level_rmse < 0.0:
            raise ValueError("receiver RMSE must be finite and nonnegative.")
        if self.reconciliation_scale_applied or self.scientific_pass_threshold_applied:
            raise ValueError("reconciliation and pass thresholds are prohibited.")

    def to_payload(self) -> dict[str, object]:
        return {
            "front": self.front.to_payload(),
            "back": self.back.to_payload(),
            "combined": self.combined.to_payload(),
            "receiver_level_rmse": self.receiver_level_rmse,
            "receiver_level_relative_rmse": self.receiver_level_relative_rmse,
            "reconciliation_scale_applied": False,
            "scientific_pass_threshold_applied": False,
        }


@dataclass(frozen=True, slots=True)
class HpsRexTransportResult:
    plan: HpsRexExecutionPlan
    arrays: tuple[tuple[str, FloatArray], ...]
    run_metrics: tuple[HpsIncidentRunMetrics, ...]
    four_band_par_metrics: HpsIncidentRunMetrics
    scalar_four_band_diagnostics: HpsScalarFourBandDiagnostics
    incident_npz_sha256: str
    command_count: int


@dataclass(frozen=True, slots=True)
class _CommandRecord:
    sequence_index: int
    interval_id: str | None
    command: CommandSpec
    result: RunnerResult
    stderr_sha256: str


def plan_hps_rex_workspace(request: HpsRexTransportRequest) -> HpsRexWorkspace:
    root = request.workspace / HPS_REX_RUNTIME_ROOT
    source = root / "source"
    shared = root / "shared"
    receivers = root / "receivers"
    logs = root / "logs"
    return HpsRexWorkspace(
        workspace=request.workspace,
        artifact_root=root,
        source_directory=source,
        shared_directory=shared,
        receiver_directory=receivers,
        log_directory=logs,
        input_identity=root / "input_identity.json",
        bundle_manifest=root / "phase25b_bundle.json",
        execution_plan_summary=root / "phase25d_execution_plan.json",
        source_payload_json=root / "hps_source_payload.json",
        optical_payload_json=root / "hps_rex_atr.json",
        material_plan_json=root / "hps_rex_materials.json",
        source_conversion_summary=root / "source_conversion_summary.json",
        command_provenance_summary=root / "command_provenance_summary.json",
        incident_transport_summary=root / "incident_transport_summary.json",
        incident_receiver_values_npz=root / "incident_receiver_values.npz",
        failure_summary=root / "failure_summary.json",
        derived_ies=source / "hps_unit_downward_flux.ies",
        raw_converted_rad=source / "hps_unit_downward_flux.rad",
        converted_dat=source / "hps_unit_downward_flux.dat",
        room_rad=shared / "room.rad",
        generic_plant_rad=shared / "rex_geometry.rad",
        receiver_rays=receivers / "rex_receiver_rays.pts",
        receiver_metadata=receivers / "rex_receiver_metadata.json",
        ies2rad_stderr=logs / "ies2rad.stderr.log",
    )


def resolve_hps_rex_executables(
    *,
    resolver: ExecutableResolver = resolve_executable,
    version_probe: VersionProbe = probe_radiance_version,
) -> HpsRexExecutables:
    ies2rad = resolver("ies2rad", label="ies2rad")
    oconv = resolver("oconv", label="oconv")
    rtrace = resolver("rtrace", label="rtrace")
    try:
        version = version_probe(rtrace)
    except Exception:
        version = None
    return HpsRexExecutables(ies2rad, oconv, rtrace, version)


def plan_hps_rex_execution(
    request: HpsRexTransportRequest,
    *,
    executables: HpsRexExecutables | None = None,
) -> HpsRexExecutionPlan:
    bins = executables or HpsRexExecutables(Path("ies2rad"), Path("oconv"), Path("rtrace"))
    paths = plan_hps_rex_workspace(request)
    bundle = plan_hps_isolated_transport_bundle(
        request.workspace,
        output_root_name=HPS_REX_RUNTIME_ROOT,
        layout=plan_hps_layout(
            request.room_length_m,
            request.room_width_m,
            reference_plane_z_m=request.reference_plane_z_m,
            mount_height_m=request.mount_height_m,
        ),
        threads=request.threads,
        oconv_bin=bins.oconv,
    )
    placements = tuple(
        HpsFixturePlacement(
            fixture.fixture_id,
            fixture.aligned_x_m,
            fixture.aligned_y_m,
            fixture.aperture_z_m,
        )
        for fixture in bundle.layout.fixtures
    )
    source_plan = build_hps_radiance_source_plan(
        workspace=paths.source_directory,
        placements=placements,
        ies2rad_bin=bins.ies2rad,
    )
    if source_plan.source_plan_id != bundle.phase25a_source_plan.source_plan_id:
        raise HpsRexTransportError("Phase 25A source identity diverged from Phase 25B.")
    room = RoomDimensions(
        bundle.layout.room_axes.aligned.length_m,
        bundle.layout.room_axes.aligned.width_m,
        DEFAULT_ROOM_HEIGHT_M,
    )
    room_text = room_radiance_text(room)
    plant = generate_rex_butterhead_plant(RexPlantConfig(seed=1))
    receivers = build_two_sided_patch_receivers(plant, normal_offset_m=5e-5)
    receiver_text = receiver_sample_input_text(item.to_dict() for item in receivers)
    generic_plant = export_plant_mesh_to_radiance(plant)
    if (
        len(plant.leaves) != 32
        or plant.face_count != 5248
        or plant.patch_count != 512
        or len(receivers) != 1024
        or _sha256_text(generic_plant) != bundle.plant_geometry_sha256
        or _sha256_text(receiver_text) != bundle.receiver_text_sha256
        or tuple(item.receiver_id for item in receivers) != bundle.ordered_receiver_ids
    ):
        raise HpsRexTransportError("Phase 25B plant/receiver identity diverged.")
    build_native_rex_receiver_groups(receivers, error_factory=HpsRexTransportError)
    receiver_metadata = _receiver_metadata_json(bundle, receivers)
    native_runs: list[HpsRexNativeRunPlan] = []
    dat_reference = paths.converted_dat_execution_reference
    for run in bundle.runs:
        run_paths = _run_paths(paths, run)
        source_text = format_hps_isolated_run_source_rad(
            bundle, run.interval_id, dat_reference=dat_reference
        )
        plant_text = export_plant_mesh_to_radiance(
            plant,
            material_name=run.material.material_identifier,
            material_definition=run.material.radiance_text,
        )
        if _sha256_text(plant_text) != run.plant_material_text_sha256:
            raise HpsRexTransportError(
                f"{run.interval_id} Phase 25B plant material binding changed."
            )
        options = tuple(
            radiance_options(
                request.quality_profile,
                ambient_cache=run_paths.ambient_cache,
            )
        )
        oconv = build_oconv_command(
            (
                paths.room_rad,
                run_paths.source_rad,
                bundle.fixture_occlusion.instance_source_path,
                run_paths.plant_rad,
            ),
            output_octree=run_paths.octree,
            cwd=paths.artifact_root,
            oconv_bin=bins.oconv,
            label=f"compile_hps_rex_{run.interval_id}",
        )
        base_trace = build_plant_receiver_rtrace_command(
            octree=run_paths.octree,
            receiver_input=paths.receiver_rays,
            rgb_output=run_paths.raw_rgb,
            options=options,
            nthreads=request.threads,
            cwd=paths.artifact_root,
            rtrace_bin=bins.rtrace,
        )
        trace = CommandSpec(
            argv=base_trace.argv,
            stdin_path=base_trace.stdin_path,
            stdout_path=base_trace.stdout_path,
            stdout_mode=base_trace.stdout_mode,
            cwd=base_trace.cwd,
            env=base_trace.env,
            label=f"trace_hps_rex_{run.interval_id}",
        )
        native_runs.append(
            HpsRexNativeRunPlan(
                part25b=run,
                paths=run_paths,
                native_scene_id="hps-rex-native-scene-v2-"
                + _hash_payload(
                    {
                        "part25b_scene": run.fspm_scene.scene_id,
                        "quality": request.quality_profile,
                        "fixture_occlusion_identity": (
                            bundle.fixture_occlusion.identity_sha256
                        ),
                    }
                ),
                source_text=source_text,
                source_text_sha256=_sha256_text(source_text),
                plant_text=plant_text,
                plant_text_sha256=_sha256_text(plant_text),
                oconv_command=oconv,
                rtrace_command=trace,
            )
        )
    provisional = HpsRexExecutionPlan(
        request,
        paths,
        bundle,
        source_plan,
        room_text,
        generic_plant,
        receivers,
        receiver_text,
        receiver_metadata,
        _sha256_text(receiver_metadata),
        source_plan.ies2rad.command,
        tuple(native_runs),
        "pending",
    )
    plan = HpsRexExecutionPlan(
        request,
        paths,
        bundle,
        source_plan,
        room_text,
        generic_plant,
        receivers,
        receiver_text,
        receiver_metadata,
        provisional.receiver_metadata_sha256,
        source_plan.ies2rad.command,
        tuple(native_runs),
        "hps-rex-execution-plan-v2-"
        + _hash_payload(provisional.scientific_payload()),
    )
    _validate_plan(plan)
    return plan


def validate_hps_rex_execution_plan(
    plan: HpsRexExecutionPlan,
) -> HpsRexExecutionPlan:
    """Revalidate the immutable scientific and native-command closure."""

    _validate_plan(plan)
    return plan


def format_hps_rex_execution_plan_json(plan: HpsRexExecutionPlan) -> str:
    """Serialize the path-free Phase 25D execution identity deterministically."""

    return json.dumps(
        plan.scientific_payload() | {"execution_plan_id": plan.execution_plan_id},
        indent=2,
        sort_keys=True,
    ) + "\n"


def _run_paths(workspace: HpsRexWorkspace, run: HpsIsolatedRunPlan) -> HpsRexRunPaths:
    directory = run.paths.directory
    return HpsRexRunPaths(
        directory=directory,
        source_rad=run.paths.source_rad,
        plant_rad=run.paths.plant_rad,
        scene_manifest=directory / "scene_manifest.json",
        octree=run.paths.fspm_octree,
        ambient_cache=run.paths.ambient_cache,
        raw_rgb=run.paths.raw_rgb,
        decoded_npy=directory / "incident_values.npy",
        oconv_stderr=(
            workspace.log_directory
            / f"{run.order_index:02d}_{run.interval_id}.oconv.stderr.log"
        ),
        rtrace_stderr=(
            workspace.log_directory
            / f"{run.order_index:02d}_{run.interval_id}.rtrace.stderr.log"
        ),
    )


def execute_hps_rex_transport(
    request: HpsRexTransportRequest,
    runner: CommandRunner | None = None,
    *,
    executables: HpsRexExecutables | None = None,
    resolver: ExecutableResolver = resolve_executable,
    version_probe: VersionProbe = probe_radiance_version,
) -> HpsRexTransportResult:
    resolved = executables or resolve_hps_rex_executables(
        resolver=resolver, version_probe=version_probe
    )
    plan = plan_hps_rex_execution(request, executables=resolved)
    local_runner = runner or LocalRunner()
    records: list[_CommandRecord] = []
    current_label = "materialize_workspace"
    current_interval: str | None = None
    _assert_empty(plan.paths.artifact_root)
    try:
        _materialize(plan)
        current_label = "compile_hps_fixture_body"
        compile_fixture_occlusion(
            plan.fixture_occlusion,
            local_runner,
            oconv_executable=resolved.oconv,
        )
        validate_compiled_fixture_occlusion(plan.fixture_occlusion)
        current_label = plan.ies2rad_command.label
        result = _run_recorded(
            plan,
            local_runner,
            plan.ies2rad_command,
            plan.paths.ies2rad_stderr,
            None,
            records,
        )
        _require_success(plan.ies2rad_command, result)
        dat_hash, dat_metadata, contract = _validate_conversion(plan)
        _write_json(
            plan.paths.source_conversion_summary,
            _source_conversion_payload(plan, dat_hash, dat_metadata, contract),
        )
        arrays: list[tuple[str, FloatArray]] = []
        metrics: list[HpsIncidentRunMetrics] = []
        groups = build_native_rex_receiver_groups(
            plan.receiver_samples, error_factory=HpsRexTransportError
        )
        for run in plan.runs:
            current_interval = run.interval_id
            current_label = run.oconv_command.label
            _validate_native_inputs(plan, run, dat_hash)
            result = _run_recorded(
                plan,
                local_runner,
                run.oconv_command,
                run.paths.oconv_stderr,
                run.interval_id,
                records,
            )
            _require_success(run.oconv_command, result)
            _require_nonempty(run.paths.octree, f"{run.interval_id} octree")
            current_label = run.rtrace_command.label
            _validate_native_inputs(plan, run, dat_hash)
            result = _run_recorded(
                plan,
                local_runner,
                run.rtrace_command,
                run.paths.rtrace_stderr,
                run.interval_id,
                records,
            )
            _require_success(run.rtrace_command, result)
            _require_nonempty(run.paths.raw_rgb, f"{run.interval_id} raw RGB")
            _require_nonempty(run.paths.ambient_cache, f"{run.interval_id} ambient cache")
            values = parse_hps_rex_receiver_rgb(
                _read_ascii(run.paths.raw_rgb, f"{run.interval_id} raw RGB"),
                interval_id=run.interval_id,
            )
            atomic_save_npy(run.paths.decoded_npy, values)
            arrays.append((run.interval_id, values))
            metrics.append(
                compute_hps_incident_run_metrics(
                    run.interval_id,
                    values,
                    plan.receiver_samples,
                    groups=groups,
                )
            )
        if len(records) != HPS_REX_NATIVE_COMMAND_COUNT:
            raise HpsRexTransportError(
                f"native command closure failed: expected 13, got {len(records)}."
            )
        by_interval = dict(arrays)
        four_band = np.add.reduce(
            [by_interval[name] for name in ("blue", "green", "orange", "red")]
        ).astype(np.float64, copy=False)
        four_band_metrics = compute_hps_incident_run_metrics(
            "four_band_par", four_band, plan.receiver_samples, groups=groups
        )
        diagnostics = compute_hps_scalar_four_band_diagnostics(
            by_interval["scalar_par"], four_band, plan.receiver_samples, groups=groups
        )
        named = tuple(
            (f"{name}_umol_m2_s", by_interval[name]) for name in HPS_REX_RUN_ORDER
        )
        npz = format_hps_rex_incident_npz(named)
        atomic_write_bytes(plan.paths.incident_receiver_values_npz, npz)
        npz_hash = _sha256_bytes(npz)
        _validate_incident_npz(plan.paths.incident_receiver_values_npz, named)
        _write_provenance(plan, records, resolved, success=True)
        _write_json(
            plan.paths.incident_transport_summary,
            _incident_summary(
                plan,
                records,
                tuple(metrics),
                four_band_metrics,
                diagnostics,
                npz_hash,
                dat_hash,
            ),
        )
        return HpsRexTransportResult(
            plan,
            named,
            tuple(metrics),
            four_band_metrics,
            diagnostics,
            npz_hash,
            len(records),
        )
    except BaseException as exc:
        plan.paths.incident_transport_summary.unlink(missing_ok=True)
        plan.paths.incident_receiver_values_npz.unlink(missing_ok=True)
        for run in plan.runs:
            run.paths.decoded_npy.unlink(missing_ok=True)
        try:
            _write_provenance(plan, records, resolved, success=False)
            _write_json(
                plan.paths.failure_summary,
                {
                    "schema_version": 1,
                    "success": False,
                    "execution_plan_id": plan.execution_plan_id,
                    "failing_command_label": current_label,
                    "failing_interval_id": current_interval,
                    "recorded_native_command_count": len(records),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "retry_policy": "new_empty_workspace_required",
                },
            )
        except Exception:
            pass
        raise


def parse_hps_rex_receiver_rgb(
    text: str,
    *,
    interval_id: str = "receiver",
    expected_receiver_count: int = 1024,
) -> FloatArray:
    return decode_native_rex_receiver_rgb(
        text,
        interval_id=interval_id,
        expected_receiver_count=expected_receiver_count,
        error_factory=HpsRexTransportError,
    )


def compute_hps_incident_run_metrics(
    interval_id: str,
    values: Sequence[float] | FloatArray,
    samples: Sequence[MeshPatchReceiverSample],
    *,
    groups: NativeRexReceiverGroups | None = None,
) -> HpsIncidentRunMetrics:
    native = compute_native_incident_metrics(
        values, samples, groups=groups, label=interval_id
    )
    units = (
        "far_red_photon_flux_density_umol_m2_s"
        if interval_id == "far_red"
        else "PAR_photon_flux_density_umol_m2_s"
    )
    return HpsIncidentRunMetrics(
        interval_id=interval_id,
        units=units,
        **{
            name: getattr(native, name)
            for name in native.__dataclass_fields__
        },
    )


def compute_hps_scalar_four_band_diagnostics(
    scalar: Sequence[float] | FloatArray,
    four_band: Sequence[float] | FloatArray,
    samples: Sequence[MeshPatchReceiverSample],
    *,
    groups: NativeRexReceiverGroups | None = None,
) -> HpsScalarFourBandDiagnostics:
    native = compute_native_scalar_four_band_diagnostics(
        scalar, four_band, samples, groups=groups
    )
    return HpsScalarFourBandDiagnostics(
        front=_comparison("front", native.front),
        back=_comparison("back", native.back),
        combined=_comparison("combined_front_plus_back", native.combined),
        receiver_level_rmse=native.receiver_level_rmse,
        receiver_level_relative_rmse=native.receiver_level_relative_rmse,
    )


def format_hps_rex_incident_npz(
    arrays: Sequence[tuple[str, Sequence[float] | FloatArray]],
) -> bytes:
    return format_native_rex_incident_npz(
        arrays, expected_names=HPS_REX_INCIDENT_ARRAY_ORDER
    )


def _validate_incident_npz(
    path: Path,
    expected: Sequence[tuple[str, FloatArray]],
) -> None:
    try:
        with np.load(path, allow_pickle=False) as archive:
            if tuple(archive.files) != HPS_REX_INCIDENT_ARRAY_ORDER:
                raise HpsRexTransportError("incident NPZ key order changed.")
            for name, values in expected:
                actual = archive[name]
                if (
                    actual.dtype != np.dtype(np.float64)
                    or actual.shape != (1024,)
                    or not np.array_equal(actual, values)
                ):
                    raise HpsRexTransportError(
                        f"incident NPZ array {name} failed round-trip validation."
                    )
    except (OSError, ValueError) as exc:
        raise HpsRexTransportError("incident NPZ could not be read safely.") from exc


def _comparison(
    scope: str, native: NativeIncidentComparisonValues
) -> HpsIncidentComparison:
    return HpsIncidentComparison(
        scope=scope,
        scalar_area_weighted_mean=native.scalar_area_weighted_mean,
        four_band_area_weighted_mean=native.four_band_area_weighted_mean,
        signed_difference=native.signed_difference,
        absolute_difference=native.absolute_difference,
        relative_difference=native.relative_difference,
    )


def _materialize(plan: HpsRexExecutionPlan) -> None:
    _assert_empty(plan.paths.artifact_root)
    directories = {
        plan.request.workspace,
        plan.paths.artifact_root,
        plan.paths.source_directory,
        plan.paths.shared_directory,
        plan.paths.receiver_directory,
        plan.paths.log_directory,
        *(run.paths.directory for run in plan.runs),
    }
    for directory in sorted(directories, key=str):
        directory.mkdir(parents=True, exist_ok=True)
    _write_text(plan.paths.derived_ies, plan.source_plan.derived_ies.text)
    if _sha256_file(plan.paths.derived_ies) != plan.source_plan.derived_ies.sha256:
        raise HpsRexTransportError("staged derived IES hash mismatch.")
    materialize_fixture_occlusion(plan.fixture_occlusion)
    _write_json(
        plan.paths.input_identity,
        {
            "schema_version": 2,
            "execution_plan_id": plan.execution_plan_id,
            "bundle_id": plan.bundle.bundle_id,
            "fixture_occlusion_identity": (
                plan.fixture_occlusion.identity_sha256
            ),
            "request": plan.request.scientific_payload(),
            "runtime_workspace": str(plan.request.workspace),
        },
    )
    _write_text(
        plan.paths.bundle_manifest,
        format_hps_isolated_transport_bundle_json(plan.bundle),
    )
    _write_text(
        plan.paths.execution_plan_summary,
        format_hps_rex_execution_plan_json(plan),
    )
    _write_text(
        plan.paths.source_payload_json,
        format_hps_spectral_source_payload_json(plan.bundle.source_payload),
    )
    _write_text(
        plan.paths.optical_payload_json,
        format_hps_rex_weighted_atr_json(plan.bundle.optical_payload),
    )
    _write_text(
        plan.paths.material_plan_json,
        format_hps_rex_material_plan_json(plan.bundle.material_plan),
    )
    _write_text(plan.paths.room_rad, plan.room_text)
    _write_text(plan.paths.generic_plant_rad, plan.generic_plant_text)
    _write_text(plan.paths.receiver_rays, plan.receiver_text)
    _write_text(plan.paths.receiver_metadata, plan.receiver_metadata_text)
    for run in plan.runs:
        _validate_material(run)
        _write_text(run.paths.source_rad, run.source_text)
        _write_text(run.paths.plant_rad, run.plant_text)
        _write_json(
            run.paths.scene_manifest,
            {
                "schema_version": 2,
                "interval_id": run.interval_id,
                "scene_id": run.native_scene_id,
                "source_definition_id": run.part25b.source_definition_id,
                "material_identifier": run.part25b.material.material_identifier,
                "octree_identity": run.part25b.fspm_scene.octree_identity,
                "ambient_cache_identity": run.part25b.ambient_cache_identity,
                "fixture_occlusion_identity": (
                    plan.fixture_occlusion.identity_sha256
                ),
                "room_model": production_room_model_payload(),
                "room_model_identity_sha256": (
                    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256
                ),
                "ordered_scene_inputs": [
                    _relative(Path(token), plan.paths.artifact_root)
                    for token in run.oconv_command.argv[2:]
                ],
                "ordered_component_roles": [
                    "room",
                    "hps_emitters",
                    "fixture_bodies",
                    "rex_plant",
                ],
                "raw_converted_geometry_included": False,
                "fixture_bodies_included": True,
            },
        )


def _validate_conversion(
    plan: HpsRexExecutionPlan,
) -> tuple[str, Mapping[str, object], NativeFlatcorrOutputContract]:
    expected = {
        plan.paths.derived_ies.name,
        plan.paths.raw_converted_rad.name,
        plan.paths.converted_dat.name,
    }
    actual = {path.name for path in plan.paths.source_directory.iterdir()}
    if actual != expected:
        raise HpsRexTransportError(
            f"ies2rad output set mismatch: expected {sorted(expected)}, got {sorted(actual)}."
        )
    raw = _read_ascii(plan.paths.raw_converted_rad, "raw converted RAD")
    try:
        contract = validate_converted_hps_ies_output(raw)
    except Exception as exc:
        raise HpsRexTransportError(f"invalid native HPS source: {exc}") from exc
    metadata = validate_hps_converted_dat(plan.paths.converted_dat.read_bytes())
    return _sha256_file(plan.paths.converted_dat), metadata, contract


def _validate_native_inputs(
    plan: HpsRexExecutionPlan,
    run: HpsRexNativeRunPlan,
    dat_hash: str,
) -> None:
    validate_compiled_fixture_occlusion(plan.fixture_occlusion)
    for path, expected, label in (
        (plan.paths.converted_dat, dat_hash, "DAT"),
        (run.paths.source_rad, run.source_text_sha256, "source"),
        (run.paths.plant_rad, run.plant_text_sha256, "plant"),
        (
            plan.fixture_occlusion.instance_source_path,
            _sha256_text(plan.fixture_occlusion.instance_source_text),
            "fixture body instances",
        ),
        (plan.paths.receiver_rays, plan.bundle.receiver_text_sha256, "receiver rays"),
        (
            plan.paths.receiver_metadata,
            plan.receiver_metadata_sha256,
            "receiver metadata",
        ),
    ):
        _require_nonempty(path, label)
        if _sha256_file(path) != expected:
            raise HpsRexTransportError(f"{run.interval_id} {label} hash mismatch.")
    reference = plan.paths.converted_dat_execution_reference
    if _read_ascii(run.paths.source_rad, "run source").split().count(reference) != 1:
        raise HpsRexTransportError(
            f"{run.interval_id} source must reference the shared DAT exactly once."
        )
    for command in (run.oconv_command, run.rtrace_command):
        if command.cwd != plan.paths.artifact_root:
            raise HpsRexTransportError("oconv/rtrace cwd must be the run root.")
        if (command.cwd / reference).resolve() != plan.paths.converted_dat.resolve():
            raise HpsRexTransportError("shared DAT cannot resolve from command cwd.")


def _validate_material(run: HpsRexNativeRunPlan) -> None:
    material = run.part25b.material
    source = material.source_interval.coefficients
    reconstruction = material.reconstruction
    for actual, expected in (
        (reconstruction.absorptance, source.absorptance),
        (reconstruction.total_transmittance, source.transmittance),
        (reconstruction.total_reflectance, source.reflectance),
    ):
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12):
            raise HpsRexTransportError(
                f"{run.interval_id} material reconstruction failed."
            )
    if run.plant_text.count(material.radiance_text.strip()) != 1:
        raise HpsRexTransportError(
            f"{run.interval_id} material text must occur exactly once."
        )
    if run.plant_text.count(" polygon ") != 5248:
        raise HpsRexTransportError(f"{run.interval_id} plant polygon count changed.")


def _validate_plan(plan: HpsRexExecutionPlan) -> None:
    if tuple(run.interval_id for run in plan.runs) != HPS_REX_RUN_ORDER:
        raise HpsRexTransportError("HPS native run order changed.")
    if len(plan.commands) != 13 or len(plan.receiver_samples) != 1024:
        raise HpsRexTransportError("native command or receiver closure failed.")
    if len(plan.source_plan.apertures) != len(plan.bundle.layout.fixtures):
        raise HpsRexTransportError("one-aperture-per-fixture closure failed.")
    if plan.ies2rad_command.cwd != plan.paths.source_directory:
        raise HpsRexTransportError("ies2rad cwd must be the source directory.")
    if (
        plan.ies2rad_command.stdin_path is not None
        or plan.ies2rad_command.stdout_path is not None
    ):
        raise HpsRexTransportError("ies2rad must use its native planned output set.")
    if any(command.env for command in plan.commands):
        raise HpsRexTransportError("native commands require empty explicit environments.")
    expected_labels = (
        "convert_hps_unit_downward_flux_ies",
        *(
            label
            for interval_id in HPS_REX_RUN_ORDER
            for label in (
                f"compile_hps_rex_{interval_id}",
                f"trace_hps_rex_{interval_id}",
            )
        ),
    )
    if tuple(command.label for command in plan.commands) != expected_labels:
        raise HpsRexTransportError("native command sequence changed.")
    if len({run.paths.octree for run in plan.runs}) != 6 or len(
        {run.paths.ambient_cache for run in plan.runs}
    ) != 6:
        raise HpsRexTransportError("octrees and ambient caches must be isolated.")
    if len({run.source_text_sha256 for run in plan.runs}) != 6 or len(
        {run.part25b.material.material_identifier for run in plan.runs}
    ) != 6:
        raise HpsRexTransportError("run sources and materials must be isolated.")
    material_ids = {
        run.part25b.material.material_identifier for run in plan.runs
    }
    for run in plan.runs:
        _validate_material(run)
        if any(
            identifier in run.plant_text
            for identifier in material_ids
            if identifier != run.part25b.material.material_identifier
        ):
            raise HpsRexTransportError(
                f"{run.interval_id} plant contains another run material."
            )
        text = run.source_text
        if (
            text.split().count(plan.paths.converted_dat_execution_reference) != 1
            or text.split().count("source.cal") != 1
            or text.count(" polygon ") != len(plan.source_plan.apertures)
            or "boxcorr" in text
            or "smd" in text.lower()
            or "conventional" in text.lower()
            or f"1 {run.part25b.flat_source_correction:.17g}" not in text
        ):
            raise HpsRexTransportError(f"{run.interval_id} source binding is invalid.")
        expected_carrier = run.part25b.per_fixture_ppf_umol_s * 179.0
        if not math.isclose(
            run.part25b.carrier_multiplier,
            expected_carrier,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise HpsRexTransportError(f"{run.interval_id} carrier closure failed.")
        if (
            run.part25b.source_input.source_family != "hps"
            or not math.isclose(
                run.part25b.whole_layout_ppf_umol_s,
                len(plan.source_plan.apertures)
                * run.part25b.per_fixture_ppf_umol_s,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ):
            raise HpsRexTransportError(
                f"{run.interval_id} source identity or layout PPF closure failed."
            )
        if (
            run.oconv_command.cwd != plan.paths.artifact_root
            or run.rtrace_command.cwd != plan.paths.artifact_root
        ):
            raise HpsRexTransportError("oconv/rtrace cwd must be the run root.")
        if run.rtrace_command.stdin_path != plan.paths.receiver_rays:
            raise HpsRexTransportError("all traces must share ordered receiver rays.")
        if (
            run.oconv_command.stdout_path != run.paths.octree
            or run.oconv_command.stdout_mode != "binary"
            or run.rtrace_command.stdout_path != run.paths.raw_rgb
            or run.rtrace_command.stdout_mode != "text"
            or run.rtrace_command.argv[1:5]
            != ("-h", "-I+", "-n", str(plan.request.threads))
        ):
            raise HpsRexTransportError("native command streams or flags changed.")
        if tuple(Path(token) for token in run.oconv_command.argv[2:]) != (
            plan.paths.room_rad,
            run.paths.source_rad,
            plan.fixture_occlusion.instance_source_path,
            run.paths.plant_rad,
        ) or plan.paths.raw_converted_rad in tuple(
            Path(token) for token in run.oconv_command.argv[2:]
        ):
            raise HpsRexTransportError("final HPS Rex scene composition changed.")
    if _is_default(plan) and (
        len(plan.bundle.layout.fixtures) != 4
        or plan.bundle.source_payload.fixture_count
        * plan.bundle.source_payload.scalar_par_ppf_umol_s_per_fixture
        != 7000.0
    ):
        raise HpsRexTransportError("default 10x10 HPS closure failed.")


def _run_recorded(
    plan: HpsRexExecutionPlan,
    runner: CommandRunner,
    command: CommandSpec,
    stderr_path: Path,
    interval_id: str | None,
    records: list[_CommandRecord],
) -> RunnerResult:
    result = runner.run(command, stderr_path=stderr_path)
    if (
        result.command_label != command.label
        or result.argv != command.argv
        or result.stdout_path != command.stdout_path
    ):
        raise HpsRexTransportError(
            f"{command.label} runner result does not match CommandSpec."
        )
    if not stderr_path.is_file():
        atomic_write_text(stderr_path, result.stderr_text or "")
    records.append(
        _CommandRecord(
            len(records),
            interval_id,
            command,
            result,
            _sha256_file(stderr_path),
        )
    )
    _write_provenance(plan, records, None, success=False)
    return result


def _require_success(command: CommandSpec, result: RunnerResult) -> None:
    if not result.success or result.returncode != 0:
        raise HpsRexTransportError(
            result.failure_message or f"{command.label} failed."
        )


def _write_provenance(
    plan: HpsRexExecutionPlan,
    records: Sequence[_CommandRecord],
    executables: HpsRexExecutables | None,
    *,
    success: bool,
) -> None:
    root = plan.paths.artifact_root
    _write_json(
        plan.paths.command_provenance_summary,
        {
            "schema_version": 1,
            "success": success,
            "execution_plan_id": plan.execution_plan_id,
            "expected_native_command_count": 13,
            "recorded_native_command_count": len(records),
            "execution_order": [record.command.label for record in records],
            "radiance_version": {
                "rtrace_version": (
                    None if executables is None else executables.rtrace_version_text
                ),
                "oconv_version": None,
            },
            "commands": [
                {
                    "sequence_index": record.sequence_index,
                    "interval_id": record.interval_id,
                    "label": record.command.label,
                    "argv": [
                        _relative_token(token, root) for token in record.command.argv
                    ],
                    "cwd": _relative(record.command.cwd, root),
                    "stdin": _relative(record.command.stdin_path, root),
                    "stdout": _relative(record.command.stdout_path, root),
                    "stdout_mode": record.command.stdout_mode,
                    "return_code": record.result.returncode,
                    "success": record.result.success,
                    "stderr_sha256": record.stderr_sha256,
                    "shell": False,
                }
                for record in records
            ],
        },
    )


def _source_conversion_payload(
    plan: HpsRexExecutionPlan,
    dat_hash: str,
    metadata: Mapping[str, object],
    contract: NativeFlatcorrOutputContract,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source_plan_id": plan.source_plan.source_plan_id,
        "fixture_occlusion": plan.fixture_occlusion.scientific_payload(),
        "derived_ies_sha256": plan.source_plan.derived_ies.sha256,
        "raw_converted_rad_sha256": _sha256_file(plan.paths.raw_converted_rad),
        "raw_converted_geometry_included": False,
        "shared_dat": {
            "path": _relative(plan.paths.converted_dat, plan.paths.artifact_root),
            "execution_reference": plan.paths.converted_dat_execution_reference,
            "sha256": dat_hash,
            "metadata": dict(metadata),
            "generated_once": True,
        },
        "canonical_cal_reference": contract.cal_reference,
        "run_source_hashes": {
            run.interval_id: run.source_text_sha256 for run in plan.runs
        },
    }


def _incident_summary(
    plan: HpsRexExecutionPlan,
    records: Sequence[_CommandRecord],
    metrics: tuple[HpsIncidentRunMetrics, ...],
    four_band: HpsIncidentRunMetrics,
    diagnostics: HpsScalarFourBandDiagnostics,
    npz_hash: str,
    dat_hash: str,
) -> dict[str, object]:
    return {
        "schema_version": HPS_REX_EXECUTION_SCHEMA_VERSION,
        "payload_type": HPS_REX_EXECUTION_PAYLOAD_TYPE,
        "success": True,
        "execution_plan_id": plan.execution_plan_id,
        "bundle_id": plan.bundle.bundle_id,
        "fixture_occlusion": plan.fixture_occlusion.scientific_payload(),
        "room_model": production_room_model_payload(),
        "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
        "run_order": list(HPS_REX_RUN_ORDER),
        "receiver_contract": {
            "receiver_identity": plan.bundle.receiver_identity,
            "receiver_count": 1024,
            "front_receiver_count": 512,
            "back_receiver_count": 512,
            "physical_patch_count": 512,
            "ordering": "stable_patch_order_then_front_back",
            "combined_area_policy": (
                "one physical patch area multiplies front plus back incident PFD once"
            ),
        },
        "shared_source": {
            "angular_dat_identity": plan.bundle.shared_angular_dat_identity,
            "angular_dat_sha256": dat_hash,
            "converted_once": True,
            "source_cal_reference": "source.cal",
        },
        "incident_arrays": {
            "path": _relative(
                plan.paths.incident_receiver_values_npz, plan.paths.artifact_root
            ),
            "sha256": npz_hash,
            "keys": list(HPS_REX_INCIDENT_ARRAY_ORDER),
            "far_red_included_in_par": False,
            "post_trace_179_conversion": False,
            "post_trace_spectral_scaling": False,
        },
        "runs": [item.to_payload() for item in metrics],
        "run_bindings": [
            {
                "interval_id": run.interval_id,
                "per_fixture_ppf_umol_s": run.part25b.per_fixture_ppf_umol_s,
                "whole_layout_ppf_umol_s": run.part25b.whole_layout_ppf_umol_s,
                "carrier_multiplier": run.part25b.carrier_multiplier,
                "material_identifier": run.part25b.material.material_identifier,
                "source_definition_id": run.part25b.source_definition_id,
                "octree_identity": run.part25b.fspm_scene.octree_identity,
                "ambient_cache_identity": run.part25b.ambient_cache_identity,
                "fixture_occlusion_identity": (
                    plan.fixture_occlusion.identity_sha256
                ),
            }
            for run in plan.runs
        ],
        "run_artifacts": [_run_artifact_hashes(run) for run in plan.runs],
        "four_band_par": four_band.to_payload(),
        "scalar_versus_four_band": diagnostics.to_payload(),
        "native_commands": {
            "expected": 13,
            "recorded": len(records),
            "ies2rad": 1,
            "oconv": 6,
            "rtrace": 6,
            "shell": False,
        },
        "scientific_pass_threshold": None,
        "reconciliation_scale": None,
        "absorbed_photon_processing": False,
        "limitations": list(HPS_REX_EXECUTION_LIMITATIONS),
    }


def _run_artifact_hashes(run: HpsRexNativeRunPlan) -> dict[str, str]:
    """Close one successful native run over every final opaque artifact."""

    return {
        "interval_id": run.interval_id,
        "source_rad_sha256": _sha256_nonempty_file(run.paths.source_rad),
        "plant_rad_sha256": _sha256_nonempty_file(run.paths.plant_rad),
        "scene_manifest_sha256": _sha256_nonempty_file(run.paths.scene_manifest),
        "octree_sha256": _sha256_nonempty_file(run.paths.octree),
        "ambient_cache_sha256": _sha256_nonempty_file(run.paths.ambient_cache),
        "raw_rgb_sha256": _sha256_nonempty_file(run.paths.raw_rgb),
        "decoded_npy_sha256": _sha256_nonempty_file(run.paths.decoded_npy),
    }


def _receiver_metadata_json(
    bundle: HpsIsolatedTransportBundlePlan,
    samples: Sequence[MeshPatchReceiverSample],
) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "receiver_identity": bundle.receiver_identity,
            "receiver_text_sha256": bundle.receiver_text_sha256,
            "receiver_count": len(samples),
            "physical_patch_count": len(samples) // 2,
            "ordering": "stable_patch_order_then_front_back",
            "physical_patch_area_policy": bundle.scientific_payload()["receivers"][
                "physical_patch_area_policy"
            ],
            "receivers": [item.to_dict() for item in samples],
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


def _assert_empty(root: Path) -> None:
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise HpsRexTransportError(
            f"HPS Rex artifact root must be absent or empty: {root}"
        )


def _is_default(plan: HpsRexExecutionPlan) -> bool:
    return math.isclose(
        plan.request.room_length_m, 10.0 * FEET_TO_METERS, abs_tol=1e-12
    ) and math.isclose(
        plan.request.room_width_m, 10.0 * FEET_TO_METERS, abs_tol=1e-12
    )


def _write_text(path: Path, text: str) -> None:
    if path.exists():
        raise HpsRexTransportError(f"refusing to overwrite artifact: {path}")
    atomic_write_text(path, text)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )


def _require_nonempty(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise HpsRexTransportError(f"{label} is missing or empty: {path}")


def _read_ascii(path: Path, label: str) -> str:
    _require_nonempty(path, label)
    try:
        return path.read_text(encoding="ascii")
    except UnicodeDecodeError as exc:
        raise HpsRexTransportError(f"{label} must be ASCII.") from exc


def _relative(path: str | Path | None, root: Path) -> str | None:
    if path is None:
        return None
    candidate = Path(path)
    try:
        return candidate.relative_to(root).as_posix() or "."
    except ValueError:
        return str(candidate)


def _relative_token(token: str, root: Path) -> str:
    candidate = Path(token)
    return _relative(candidate, root) if candidate.is_absolute() else token


def _reject_repository_workspace(workspace: Path) -> None:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "pyproject.toml").is_file():
            if (
                (workspace == parent or parent in workspace.parents)
                and not is_default_managed_runtime_descendant(workspace, parent)
            ):
                raise ValueError("HPS runtime workspace must be outside the repository.")
            return


def _positive(name: str, value: object) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be positive.")
    return number


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be finite.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    return number


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_nonempty_file(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HpsRexTransportError(
            f"required successful-run artifact is unreadable: {path}"
        ) from exc
    if not data:
        raise HpsRexTransportError(
            f"required successful-run artifact is empty: {path}"
        )
    return _sha256_bytes(data)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
