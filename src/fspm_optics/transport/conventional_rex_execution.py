"""Native Conventional scalar/five-band Rex incident transport.

Planning is pure. Runtime materialization and native execution are explicit and
remain behind :class:`CommandSpec` plus an injected runner. Absorbed-photon
post-processing is intentionally outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Final, Mapping, Protocol, Sequence

import numpy as np
from numpy.typing import NDArray

from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_FULL_OUTPUT_CARRIER_MULTIPLIER,
    CONVENTIONAL_SHARED_ANGULAR_MODIFIER,
    CONVENTIONAL_SHARED_LIGHT_MODIFIER,
    DEFAULT_MOUNT_HEIGHT_M,
    ConventionalRadianceSourcePlan,
    ConvertedIesOutputContract,
    build_conventional_radiance_source_plan,
    format_conventional_apertures_rad,
    format_conventional_spectral_source_payload_json,
    validate_converted_ies_output,
)
from fspm_optics.fixtures.occlusion import (
    FixtureOcclusionPlan,
    materialize_fixture_occlusion,
    plan_fixture_occlusion,
)
from fspm_optics.geometry.room import (
    FEET_TO_METERS,
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    production_room_model_payload,
)
from fspm_optics.optics.conventional_rex import (
    format_conventional_rex_material_plan_json,
    format_conventional_rex_weighted_atr_json,
)
from fspm_optics.plants.generator import generate_rex_butterhead_plant
from fspm_optics.plants.models import PlantMesh
from fspm_optics.plants.radiance_export import export_plant_mesh_to_radiance
from fspm_optics.plants.rex import RexPlantConfig
from fspm_optics.radiance.commands import (
    LOCAL_DEFAULT_NTHREADS,
    CommandSpec,
    build_oconv_command,
    build_plant_receiver_rtrace_command,
)
from fspm_optics.radiance.executables import resolve_executable
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
from fspm_optics.transport.conventional_rex import (
    CONVENTIONAL_REX_RUN_ORDER,
    ConventionalRexIsolatedRunPlan,
    ConventionalRexTransportBundlePlan,
    format_conventional_rex_transport_bundle_json,
    plan_conventional_rex_transport_bundle,
)
from fspm_optics.transport.conventional_scalar import (
    DEFAULT_REFERENCE_PLANE_Z_M,
    QUALITY_PROFILES,
    ConventionalScalarExecutables,
    ConventionalScalarTransportRequest,
    plan_conventional_scalar_transport,
    validate_converted_dat,
)
from fspm_optics.transport.native_rex_data import (
    NativeRexReceiverGroups,
    build_native_rex_receiver_groups,
    compute_native_incident_metrics,
    compute_native_scalar_four_band_diagnostics,
    decode_native_rex_receiver_rgb,
    format_native_rex_incident_npz,
)

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]

CONVENTIONAL_REX_EXECUTION_SCHEMA_VERSION: Final = 4
CONVENTIONAL_REX_EXECUTION_PAYLOAD_TYPE: Final = (
    "fspm_optics_conventional_rex_incident_transport"
)
CONVENTIONAL_REX_INCIDENT_ARRAY_ORDER: Final = (
    "scalar_par_umol_m2_s",
    "blue_umol_m2_s",
    "green_umol_m2_s",
    "orange_umol_m2_s",
    "red_umol_m2_s",
    "far_red_umol_m2_s",
)
CONVENTIONAL_REX_NATIVE_COMMAND_COUNT: Final = 14
CONVENTIONAL_REX_EXECUTION_LIMITATIONS: Final[tuple[str, ...]] = (
    "Incident photon transport only; no absorbed-photon calculation is performed.",
    "Scalar PAR is a validation trace and is never added to four-band PAR.",
    "Far-red remains outside PAR.",
    "The room optical material is wavelength-neutral.",
    "The source is one-sided and excludes original upward IES leakage.",
    "Scalar-versus-four-band differences are recorded without reconciliation scaling.",
)


class ConventionalRexTransportError(RuntimeError):
    """The Conventional Rex native workflow failed a scientific or runtime gate."""


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
class ConventionalRexExecutables:
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
class ConventionalRexTransportRequest:
    """User-controlled runtime inputs; scientific source and plant inputs are fixed."""

    workspace: Path
    room_length_m: float
    room_width_m: float
    threads: int = LOCAL_DEFAULT_NTHREADS
    quality_profile: str = "standard"
    reference_plane_z_m: float = DEFAULT_REFERENCE_PLANE_Z_M
    mount_height_m: float = DEFAULT_MOUNT_HEIGHT_M

    def __post_init__(self) -> None:
        raw_workspace = Path(self.workspace).expanduser()
        if not raw_workspace.is_absolute():
            raise ValueError("workspace must be an absolute path.")
        workspace = raw_workspace.resolve()
        _reject_repository_workspace(workspace)
        object.__setattr__(self, "workspace", workspace)
        for name in ("room_length_m", "room_width_m", "mount_height_m"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        reference = _finite("reference_plane_z_m", self.reference_plane_z_m)
        if reference < 0.0:
            raise ValueError("reference_plane_z_m must be non-negative.")
        object.__setattr__(self, "reference_plane_z_m", reference)
        if reference + self.mount_height_m >= 10.0 * FEET_TO_METERS:
            raise ValueError("the aperture plane must remain below the room ceiling.")
        if isinstance(self.threads, bool) or not isinstance(self.threads, int):
            raise ValueError("threads must be a positive integer.")
        if self.threads <= 0:
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
    ) -> "ConventionalRexTransportRequest":
        return cls(
            workspace=Path(workspace),
            room_length_m=_positive("room_length_ft", room_length_ft)
            * FEET_TO_METERS,
            room_width_m=_positive("room_width_ft", room_width_ft)
            * FEET_TO_METERS,
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
    ) -> "ConventionalRexTransportRequest":
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
            "layout_policy": "practical",
            "plant_policy": "fixed_mature_rex_seed_1_default_4x4_patches",
        }


@dataclass(frozen=True, slots=True)
class ConventionalRexExecutionRunPaths:
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

    def to_payload(self, root: Path) -> dict[str, str]:
        return {
            name: _relative(Path(getattr(self, name)), root)
            for name in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class ConventionalRexExecutionWorkspace:
    workspace: Path
    artifact_root: Path
    source_directory: Path
    receiver_directory: Path
    log_directory: Path
    input_identity: Path
    bundle_manifest: Path
    transport_plan_summary: Path
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

    def run_root_relative_reference(self, path: str | Path) -> str:
        candidate = Path(path)
        try:
            relative = candidate.relative_to(self.artifact_root)
        except ValueError as exc:
            raise ValueError("runtime resource must remain inside artifact_root.") from exc
        if not relative.parts or ".." in relative.parts:
            raise ValueError("runtime resource reference is unsafe.")
        return relative.as_posix()

    @property
    def converted_dat_execution_reference(self) -> str:
        return self.run_root_relative_reference(self.converted_dat)


@dataclass(frozen=True, slots=True)
class ConventionalRexNativeRunPlan:
    part1: ConventionalRexIsolatedRunPlan
    paths: ConventionalRexExecutionRunPaths
    native_scene_id: str
    angular_modifier_id: str
    light_modifier_id: str
    source_text: str
    source_text_sha256: str
    plant_text: str
    plant_text_sha256: str
    oconv_command: CommandSpec
    rtrace_command: CommandSpec

    @property
    def interval_id(self) -> str:
        return self.part1.interval_id

    def scientific_payload(self, root: Path) -> dict[str, object]:
        return {
            "part1": self.part1.scientific_payload(),
            "native_scene_id": self.native_scene_id,
            "angular_modifier_id": self.angular_modifier_id,
            "light_modifier_id": self.light_modifier_id,
            "source_text_sha256": self.source_text_sha256,
            "plant_text_sha256": self.plant_text_sha256,
            "scene_inputs": [
                _relative(self.oconv_command.argv[index], root)
                for index in range(2, len(self.oconv_command.argv))
            ],
            "paths": self.paths.to_payload(root),
        }


@dataclass(frozen=True, slots=True)
class ConventionalRexExecutionPlan:
    request: ConventionalRexTransportRequest
    paths: ConventionalRexExecutionWorkspace
    bundle: ConventionalRexTransportBundlePlan
    source_plan: ConventionalRadianceSourcePlan
    fixture_occlusion: FixtureOcclusionPlan
    room_text: str
    generic_plant_text: str
    receiver_samples: tuple[MeshPatchReceiverSample, ...]
    receiver_text: str
    receiver_metadata_text: str
    receiver_metadata_sha256: str
    ies2rad_command: CommandSpec
    runs: tuple[ConventionalRexNativeRunPlan, ...]
    execution_plan_id: str

    def __post_init__(self) -> None:
        if tuple(item.interval_id for item in self.runs) != CONVENTIONAL_REX_RUN_ORDER:
            raise ValueError("native Conventional Rex run order is invalid.")
        if len(self.receiver_samples) != 1024:
            raise ValueError("native Conventional Rex plan requires 1024 receivers.")

    def scientific_payload(self) -> dict[str, object]:
        return {
            "schema_version": CONVENTIONAL_REX_EXECUTION_SCHEMA_VERSION,
            "payload_type": CONVENTIONAL_REX_EXECUTION_PAYLOAD_TYPE,
            "request": self.request.scientific_payload(),
            "bundle_id": self.bundle.bundle_id,
            "source_plan_id": self.source_plan.source_plan_id,
            "fixture_occlusion": self.fixture_occlusion.scientific_payload(),
            "room_identity": self.bundle.room_identity,
            "room_model": production_room_model_payload(),
            "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
            "plant_identity": self.bundle.plant_identity,
            "receiver_identity": self.bundle.receiver_identity,
            "receiver_text_sha256": self.bundle.receiver_text_sha256,
            "receiver_metadata_sha256": self.receiver_metadata_sha256,
            "shared_angular_dat_identity": self.bundle.shared_angular_dat_identity,
            "run_order": list(CONVENTIONAL_REX_RUN_ORDER),
            "runs": [item.scientific_payload(self.paths.artifact_root) for item in self.runs],
            "native_command_count": CONVENTIONAL_REX_NATIVE_COMMAND_COUNT,
            "absorbed_photon_processing": False,
            "limitations": list(CONVENTIONAL_REX_EXECUTION_LIMITATIONS),
        }

    def to_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {
            "execution_plan_id": self.execution_plan_id,
            "runtime": {
                "workspace": str(self.paths.workspace),
                "artifact_root": str(self.paths.artifact_root),
            },
        }


@dataclass(frozen=True, slots=True)
class IncidentRunMetrics:
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
        if self.interval_id not in (*CONVENTIONAL_REX_RUN_ORDER, "four_band_par"):
            raise ValueError("incident metrics interval identity is invalid.")
        expected_units = (
            "far_red_photon_flux_density_umol_m2_s"
            if self.interval_id == "far_red"
            else "PAR_photon_flux_density_umol_m2_s"
        )
        if self.units != expected_units:
            raise ValueError("incident metrics units do not match the interval.")
        if (
            self.receiver_count,
            self.front_receiver_count,
            self.back_receiver_count,
            self.physical_patch_count,
        ) != (1024, 512, 512, 512):
            raise ValueError("incident metrics require the fixed Rex receiver contract.")
        for name in (
            "minimum_incident_pfd",
            "maximum_incident_pfd",
            "front_area_weighted_mean_incident_pfd",
            "back_area_weighted_mean_incident_pfd",
            "combined_area_weighted_mean_incident_pfd",
            "whole_plant_incident_photon_flux_umol_s",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative.")
        if self.minimum_incident_pfd > self.maximum_incident_pfd:
            raise ValueError("incident minimum exceeds maximum.")

    def to_payload(self) -> dict[str, object]:
        return {
            "interval_id": self.interval_id,
            "units": self.units,
            "receiver_count": self.receiver_count,
            "front_receiver_count": self.front_receiver_count,
            "back_receiver_count": self.back_receiver_count,
            "physical_patch_count": self.physical_patch_count,
            "minimum_incident_pfd": self.minimum_incident_pfd,
            "maximum_incident_pfd": self.maximum_incident_pfd,
            "front_area_weighted_mean_incident_pfd": self.front_area_weighted_mean_incident_pfd,
            "back_area_weighted_mean_incident_pfd": self.back_area_weighted_mean_incident_pfd,
            "combined_area_weighted_mean_incident_pfd": self.combined_area_weighted_mean_incident_pfd,
            "whole_plant_incident_photon_flux_umol_s": self.whole_plant_incident_photon_flux_umol_s,
        }


@dataclass(frozen=True, slots=True)
class IncidentComparison:
    scope: str
    scalar_area_weighted_mean: float
    four_band_area_weighted_mean: float
    absolute_difference: float
    relative_difference: float | None

    def __post_init__(self) -> None:
        values = (
            self.scalar_area_weighted_mean,
            self.four_band_area_weighted_mean,
            self.absolute_difference,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise ValueError("incident comparison values must be finite and non-negative.")
        if self.relative_difference is not None and (
            not math.isfinite(self.relative_difference)
            or self.relative_difference < 0.0
        ):
            raise ValueError("relative incident difference must be non-negative.")

    def to_payload(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "scalar_area_weighted_mean": self.scalar_area_weighted_mean,
            "four_band_area_weighted_mean": self.four_band_area_weighted_mean,
            "absolute_difference": self.absolute_difference,
            "relative_difference": self.relative_difference,
        }


@dataclass(frozen=True, slots=True)
class ScalarFourBandDiagnostics:
    front: IncidentComparison
    back: IncidentComparison
    combined: IncidentComparison
    receiver_level_rmse: float
    receiver_level_relative_rmse: float | None
    reconciliation_scale_applied: bool = False
    scientific_pass_threshold_applied: bool = False

    def __post_init__(self) -> None:
        if not math.isfinite(self.receiver_level_rmse) or self.receiver_level_rmse < 0.0:
            raise ValueError("receiver RMSE must be finite and non-negative.")
        if self.receiver_level_relative_rmse is not None and (
            not math.isfinite(self.receiver_level_relative_rmse)
            or self.receiver_level_relative_rmse < 0.0
        ):
            raise ValueError("relative receiver RMSE must be non-negative.")
        if self.reconciliation_scale_applied or self.scientific_pass_threshold_applied:
            raise ValueError("reconciliation scaling and invented pass thresholds are prohibited.")

    def to_payload(self) -> dict[str, object]:
        return {
            "front": self.front.to_payload(),
            "back": self.back.to_payload(),
            "combined": self.combined.to_payload(),
            "receiver_level_rmse": self.receiver_level_rmse,
            "receiver_level_relative_rmse": self.receiver_level_relative_rmse,
            "reconciliation_scale_applied": self.reconciliation_scale_applied,
            "scientific_pass_threshold_applied": self.scientific_pass_threshold_applied,
        }


@dataclass(frozen=True, slots=True)
class ConventionalRexTransportResult:
    plan: ConventionalRexExecutionPlan
    arrays: tuple[tuple[str, FloatArray], ...]
    run_metrics: tuple[IncidentRunMetrics, ...]
    four_band_par_metrics: IncidentRunMetrics
    scalar_four_band_diagnostics: ScalarFourBandDiagnostics
    incident_npz_sha256: str
    command_count: int


@dataclass(frozen=True, slots=True)
class _CommandRecord:
    sequence_index: int
    interval_id: str | None
    command: CommandSpec
    result: RunnerResult
    stderr_sha256: str

    def to_payload(self, root: Path) -> dict[str, object]:
        return {
            "sequence_index": self.sequence_index,
            "interval_id": self.interval_id,
            "label": self.command.label,
            "argv": [_relative_token(token, root) for token in self.command.argv],
            "cwd": None if self.command.cwd is None else _relative(self.command.cwd, root),
            "stdin": None if self.command.stdin_path is None else _relative(self.command.stdin_path, root),
            "stdout": None if self.command.stdout_path is None else _relative(self.command.stdout_path, root),
            "stdout_mode": self.command.stdout_mode,
            "return_code": self.result.returncode,
            "success": self.result.success,
            "stderr": (
                None
                if self.result.stderr_path is None
                else _relative(self.result.stderr_path, root)
            ),
            "stderr_sha256": self.stderr_sha256,
            "shell": False,
            "executable_identity": self.command.argv[0],
        }


def plan_conventional_rex_execution_workspace(
    request: ConventionalRexTransportRequest,
) -> ConventionalRexExecutionWorkspace:
    root = request.workspace / "conventional_rex_transport"
    source = root / "source"
    receivers = root / "receivers"
    logs = root / "logs"
    return ConventionalRexExecutionWorkspace(
        workspace=request.workspace,
        artifact_root=root,
        source_directory=source,
        receiver_directory=receivers,
        log_directory=logs,
        input_identity=root / "input_identity.json",
        bundle_manifest=root / "conventional_rex_transport_plan.json",
        transport_plan_summary=root / "transport_plan_summary.json",
        source_payload_json=root / "conventional_source_payload.json",
        optical_payload_json=root / "conventional_rex_atr.json",
        material_plan_json=root / "conventional_rex_materials.json",
        source_conversion_summary=root / "source_conversion_summary.json",
        command_provenance_summary=root / "command_provenance_summary.json",
        incident_transport_summary=root / "incident_transport_summary.json",
        incident_receiver_values_npz=root / "incident_receiver_values.npz",
        failure_summary=root / "failure_summary.json",
        derived_ies=source / "conventional_led_unit_downward_flux.ies",
        raw_converted_rad=source / "conventional_led_unit_downward_flux.rad",
        converted_dat=source / "conventional_led_unit_downward_flux.dat",
        room_rad=root / "shared" / "room.rad",
        generic_plant_rad=root / "shared" / "rex_geometry.rad",
        receiver_rays=receivers / "rex_receiver_rays.pts",
        receiver_metadata=receivers / "rex_receiver_metadata.json",
        ies2rad_stderr=logs / "ies2rad.stderr.log",
    )


def resolve_conventional_rex_executables(
    *,
    resolver: ExecutableResolver = resolve_executable,
    version_probe: VersionProbe = probe_radiance_version,
) -> ConventionalRexExecutables:
    """Resolve native tools and probe only the supported rtrace version boundary."""

    ies2rad = resolver("ies2rad", label="ies2rad")
    oconv = resolver("oconv", label="oconv")
    rtrace = resolver("rtrace", label="rtrace")
    try:
        version = version_probe(rtrace)
    except Exception:
        version = None
    return ConventionalRexExecutables(ies2rad, oconv, rtrace, version)


def plan_conventional_rex_execution(
    request: ConventionalRexTransportRequest,
    *,
    executables: ConventionalRexExecutables | None = None,
) -> ConventionalRexExecutionPlan:
    """Plan the complete 13-command native workflow without writing or executing."""

    bins = executables or ConventionalRexExecutables(
        Path("ies2rad"), Path("oconv"), Path("rtrace")
    )
    paths = plan_conventional_rex_execution_workspace(request)
    room_length_ft = request.room_length_m / FEET_TO_METERS
    room_width_ft = request.room_width_m / FEET_TO_METERS
    bundle = plan_conventional_rex_transport_bundle(
        request.workspace,
        room_length_ft=room_length_ft,
        room_width_ft=room_width_ft,
        reference_plane_z_m=request.reference_plane_z_m,
        mount_height_m=request.mount_height_m,
        quality_profile=request.quality_profile,
        threads=request.threads,
    )
    if bundle.output_root != paths.artifact_root:
        raise ConventionalRexTransportError("Part 1 and runtime workspace roots diverge.")

    phase23a = plan_conventional_scalar_transport(
        ConventionalScalarTransportRequest.from_meters(
            workspace=request.workspace,
            room_length_m=request.room_length_m,
            room_width_m=request.room_width_m,
            reference_plane_z_m=request.reference_plane_z_m,
            mount_height_m=request.mount_height_m,
            quality_profile=request.quality_profile,
            threads=request.threads,
        ),
        executables=ConventionalScalarExecutables(
            bins.ies2rad,
            bins.oconv,
            bins.rtrace,
            bins.rtrace_version_text,
        ),
    )
    source_plan = build_conventional_radiance_source_plan(
        phase23a.layout,
        workspace=paths.source_directory,
        ies2rad_bin=bins.ies2rad,
    )
    fixture_occlusion = plan_fixture_occlusion(
        system_id="conventional",
        layout_identity=phase23a.layout.to_payload(),
        output_directory=paths.artifact_root / "fixture_occlusion",
        oconv_bin=bins.oconv,
    )
    if (
        source_plan.source_plan_id != bundle.phase23a_source_plan_id
        or phase23a.layout.layout_id != bundle.conventional_layout_id
        or phase23a.room_identity != bundle.room_identity
        or fixture_occlusion.identity_sha256
        != bundle.fixture_occlusion_identity
        or source_plan.ies2rad.expected_paths.derived_ies != paths.derived_ies
        or source_plan.ies2rad.expected_paths.converted_rad != paths.raw_converted_rad
        or source_plan.ies2rad.expected_paths.converted_dat != paths.converted_dat
    ):
        raise ConventionalRexTransportError("Phase 23A source/workspace identity mismatch.")

    plant = generate_rex_butterhead_plant(RexPlantConfig(seed=1))
    receiver_samples = build_two_sided_patch_receivers(plant, normal_offset_m=5e-5)
    receiver_text = receiver_sample_input_text(
        item.to_dict() for item in receiver_samples
    )
    generic_plant_text = export_plant_mesh_to_radiance(plant)
    if (
        plant.face_count != 5248
        or plant.patch_count != 512
        or len(receiver_samples) != 1024
        or _sha256_text(generic_plant_text) != bundle.plant_geometry_sha256
        or _sha256_text(receiver_text) != bundle.receiver_text_sha256
        or tuple(item.receiver_id for item in receiver_samples)
        != bundle.ordered_receiver_ids
    ):
        raise ConventionalRexTransportError("Part 1 plant/receiver identity mismatch.")
    _receiver_groups(receiver_samples)
    receiver_metadata_text = _receiver_metadata_json(bundle, receiver_samples)
    receiver_metadata_sha256 = _sha256_text(receiver_metadata_text)

    dat_reference = paths.converted_dat_execution_reference
    aperture_text = format_conventional_apertures_rad(source_plan)
    native_runs: list[ConventionalRexNativeRunPlan] = []
    for part1 in bundle.runs:
        paths_for_run = _execution_run_paths(paths, part1)
        angular_modifier_id = f"conventional_rex_{part1.interval_id}_dist"
        light_modifier_id = f"conventional_rex_{part1.interval_id}_light"
        source_text = _render_run_source(
            part1,
            aperture_text=aperture_text,
            dat_reference=dat_reference,
            angular_modifier_id=angular_modifier_id,
            light_modifier_id=light_modifier_id,
        )
        plant_text = export_plant_mesh_to_radiance(
            plant,
            material_name=part1.material.material_identifier,
            material_definition=part1.material.radiance_text,
        )
        if _sha256_text(plant_text) != _planned_part1_plant_hash(plant, part1):
            raise ConventionalRexTransportError(
                f"{part1.interval_id} Part 1 plant material binding changed."
            )
        native_scene_id = "conventional-rex-native-scene-v4-" + _hash_payload(
            {
                "part1_scene_id": part1.scene_id,
                "source_payload_id": bundle.source_payload.source_payload_id,
                "material_plan_id": bundle.material_plan.material_plan_id,
                "layout_id": bundle.conventional_layout_id,
                "room_identity": bundle.room_identity,
                "plant_identity": bundle.plant_identity,
                "receiver_identity": bundle.receiver_identity,
                "fixture_occlusion_identity": fixture_occlusion.identity_sha256,
                "quality_profile": request.quality_profile,
                "quality_options": radiance_options(request.quality_profile),
            }
        )
        options = tuple(
            radiance_options(
                request.quality_profile,
                ambient_cache=paths_for_run.ambient_cache,
            )
        )
        oconv = build_oconv_command(
            (
                paths.room_rad,
                paths_for_run.source_rad,
                fixture_occlusion.instance_source_path,
                paths_for_run.plant_rad,
            ),
            output_octree=paths_for_run.octree,
            cwd=paths.artifact_root,
            oconv_bin=bins.oconv,
            label=f"compile_conventional_rex_{part1.interval_id}",
        )
        base_rtrace = build_plant_receiver_rtrace_command(
            octree=paths_for_run.octree,
            receiver_input=paths.receiver_rays,
            rgb_output=paths_for_run.raw_rgb,
            options=options,
            nthreads=request.threads,
            cwd=paths.artifact_root,
            rtrace_bin=bins.rtrace,
        )
        rtrace = CommandSpec(
            argv=base_rtrace.argv,
            stdin_path=base_rtrace.stdin_path,
            stdout_path=base_rtrace.stdout_path,
            stdout_mode=base_rtrace.stdout_mode,
            cwd=base_rtrace.cwd,
            env=base_rtrace.env,
            label=f"trace_conventional_rex_{part1.interval_id}",
        )
        native_runs.append(
            ConventionalRexNativeRunPlan(
                part1=part1,
                paths=paths_for_run,
                native_scene_id=native_scene_id,
                angular_modifier_id=angular_modifier_id,
                light_modifier_id=light_modifier_id,
                source_text=source_text,
                source_text_sha256=_sha256_text(source_text),
                plant_text=plant_text,
                plant_text_sha256=_sha256_text(plant_text),
                oconv_command=oconv,
                rtrace_command=rtrace,
            )
        )

    provisional = ConventionalRexExecutionPlan(
        request=request,
        paths=paths,
        bundle=bundle,
        source_plan=source_plan,
        fixture_occlusion=fixture_occlusion,
        room_text=phase23a.room_text,
        generic_plant_text=generic_plant_text,
        receiver_samples=receiver_samples,
        receiver_text=receiver_text,
        receiver_metadata_text=receiver_metadata_text,
        receiver_metadata_sha256=receiver_metadata_sha256,
        ies2rad_command=source_plan.ies2rad.command,
        runs=tuple(native_runs),
        execution_plan_id="pending",
    )
    identity = "conventional-rex-execution-plan-v4-" + _hash_payload(
        provisional.scientific_payload()
    )
    plan = ConventionalRexExecutionPlan(
        request=request,
        paths=paths,
        bundle=bundle,
        source_plan=source_plan,
        fixture_occlusion=fixture_occlusion,
        room_text=phase23a.room_text,
        generic_plant_text=generic_plant_text,
        receiver_samples=receiver_samples,
        receiver_text=receiver_text,
        receiver_metadata_text=receiver_metadata_text,
        receiver_metadata_sha256=receiver_metadata_sha256,
        ies2rad_command=source_plan.ies2rad.command,
        runs=tuple(native_runs),
        execution_plan_id=identity,
    )
    _validate_planned_execution(plan)
    return plan


def _execution_run_paths(
    workspace: ConventionalRexExecutionWorkspace,
    part1: ConventionalRexIsolatedRunPlan,
) -> ConventionalRexExecutionRunPaths:
    expected = part1.paths
    logs = workspace.log_directory
    return ConventionalRexExecutionRunPaths(
        directory=expected.directory,
        source_rad=expected.source_rad,
        plant_rad=expected.plant_rad,
        scene_manifest=expected.directory / "scene_manifest.json",
        octree=expected.octree,
        ambient_cache=expected.ambient_cache,
        raw_rgb=expected.raw_rgb,
        decoded_npy=expected.decoded_values,
        oconv_stderr=logs / f"{part1.order_index:02d}_{part1.interval_id}.oconv.stderr.log",
        rtrace_stderr=logs / f"{part1.order_index:02d}_{part1.interval_id}.rtrace.stderr.log",
    )


def _render_run_source(
    run: ConventionalRexIsolatedRunPlan,
    *,
    aperture_text: str,
    dat_reference: str,
    angular_modifier_id: str,
    light_modifier_id: str,
) -> str:
    if Path(dat_reference).is_absolute() or ".." in Path(dat_reference).parts:
        raise ValueError("DAT reference must be safe and run-root-relative.")
    adapted_apertures = aperture_text.replace(
        CONVENTIONAL_SHARED_LIGHT_MODIFIER,
        light_modifier_id,
    ).replace(CONVENTIONAL_SHARED_ANGULAR_MODIFIER, angular_modifier_id)
    return (
        f"# source_definition_id={run.source_definition_id}\n"
        f"# source_input_id={run.source_input.source_input_id}\n"
        f"void brightdata {angular_modifier_id}\n"
        f"5 flatcorr {dat_reference} source.cal src_phi src_theta\n"
        "0\n"
        f"1 {run.flat_source_correction:.17g}\n\n"
        f"{angular_modifier_id} illum {light_modifier_id}\n"
        "0\n"
        "0\n"
        "3 1 1 1\n\n"
        + adapted_apertures
    )


def _planned_part1_plant_hash(
    plant: PlantMesh,
    run: ConventionalRexIsolatedRunPlan,
) -> str:
    return _sha256_text(
        export_plant_mesh_to_radiance(
            plant,
            material_name=run.material.material_identifier,
            material_definition=run.material.radiance_text,
        )
    )


def execute_conventional_rex_transport(
    request: ConventionalRexTransportRequest,
    runner: CommandRunner | None = None,
    *,
    executables: ConventionalRexExecutables | None = None,
    resolver: ExecutableResolver = resolve_executable,
    version_probe: VersionProbe = probe_radiance_version,
) -> ConventionalRexTransportResult:
    """Materialize and execute one shared conversion plus six isolated traces."""

    resolved = executables or resolve_conventional_rex_executables(
        resolver=resolver,
        version_probe=version_probe,
    )
    plan = plan_conventional_rex_execution(request, executables=resolved)
    local_runner = runner or LocalRunner()
    records: list[_CommandRecord] = []
    current_label = "materialize_workspace"
    current_interval: str | None = None
    _assert_empty_workspace(plan.paths.artifact_root)
    materialized = True
    try:
        _materialize_workspace(plan)

        for shape in plan.fixture_occlusion.shapes:
            current_label = shape.compile_command.label
            shape_result = _run_recorded(
                plan,
                local_runner,
                shape.compile_command,
                stderr_path=(
                    plan.paths.log_directory
                    / f"{shape.shape_id}.oconv.stderr.log"
                ),
                interval_id=None,
                records=records,
            )
            _require_success(shape.compile_command, shape_result)
            _require_nonempty(
                shape.octree_path,
                f"{shape.shape_id} fixture body octree",
            )

        current_label = plan.ies2rad_command.label
        ies_result = _run_recorded(
            plan,
            local_runner,
            plan.ies2rad_command,
            stderr_path=plan.paths.ies2rad_stderr,
            interval_id=None,
            records=records,
        )
        _require_success(plan.ies2rad_command, ies_result)
        dat_sha256, dat_metadata, converted_contract = _validate_shared_conversion(plan)
        _materialize_run_sources(plan, converted_contract, dat_sha256)
        _write_json(
            plan.paths.source_conversion_summary,
            _source_conversion_payload(
                plan,
                dat_sha256=dat_sha256,
                dat_metadata=dat_metadata,
                contract=converted_contract,
            ),
        )

        arrays: list[tuple[str, FloatArray]] = []
        metrics: list[IncidentRunMetrics] = []
        front, back, patch_area = _receiver_groups(plan.receiver_samples)
        for run in plan.runs:
            current_interval = run.interval_id
            current_label = run.oconv_command.label
            _validate_native_inputs(plan, run, dat_sha256=dat_sha256)
            oconv_result = _run_recorded(
                plan,
                local_runner,
                run.oconv_command,
                stderr_path=run.paths.oconv_stderr,
                interval_id=run.interval_id,
                records=records,
            )
            _require_success(run.oconv_command, oconv_result)
            _require_nonempty(run.paths.octree, f"{run.interval_id} octree")

            current_label = run.rtrace_command.label
            _validate_native_inputs(plan, run, dat_sha256=dat_sha256)
            rtrace_result = _run_recorded(
                plan,
                local_runner,
                run.rtrace_command,
                stderr_path=run.paths.rtrace_stderr,
                interval_id=run.interval_id,
                records=records,
            )
            _require_success(run.rtrace_command, rtrace_result)
            _require_nonempty(run.paths.raw_rgb, f"{run.interval_id} raw RGB")
            _require_nonempty(
                run.paths.ambient_cache,
                f"{run.interval_id} dedicated ambient cache",
            )
            values = parse_conventional_rex_receiver_rgb(
                _read_ascii(run.paths.raw_rgb, f"{run.interval_id} raw RGB"),
                interval_id=run.interval_id,
            )
            atomic_save_npy(run.paths.decoded_npy, values)
            _validate_decoded_npy(run.paths.decoded_npy, values, run.interval_id)
            arrays.append((run.interval_id, values))
            metrics.append(
                compute_incident_run_metrics(
                    run.interval_id,
                    values,
                    plan.receiver_samples,
                    front_indices=front,
                    back_indices=back,
                    patch_area=patch_area,
                )
            )

        if len(records) != CONVENTIONAL_REX_NATIVE_COMMAND_COUNT:
            raise ConventionalRexTransportError(
                "native command closure failed: expected "
                f"{CONVENTIONAL_REX_NATIVE_COMMAND_COUNT}, got {len(records)}."
            )
        array_by_interval = dict(arrays)
        four_band = np.add.reduce(
            [array_by_interval[name] for name in CONVENTIONAL_REX_RUN_ORDER[1:5]]
        ).astype(np.float64, copy=False)
        four_band_metrics = compute_incident_run_metrics(
            "four_band_par",
            four_band,
            plan.receiver_samples,
            front_indices=front,
            back_indices=back,
            patch_area=patch_area,
        )
        diagnostics = compute_scalar_four_band_diagnostics(
            array_by_interval["scalar_par"],
            four_band,
            plan.receiver_samples,
            front_indices=front,
            back_indices=back,
            patch_area=patch_area,
        )
        named_arrays = tuple(
            (
                f"{interval_id}_umol_m2_s",
                array_by_interval[interval_id],
            )
            for interval_id in CONVENTIONAL_REX_RUN_ORDER
        )
        npz_bytes = format_conventional_rex_incident_npz(named_arrays)
        atomic_write_bytes(plan.paths.incident_receiver_values_npz, npz_bytes)
        npz_sha256 = _sha256_bytes(npz_bytes)
        _validate_incident_npz(plan.paths.incident_receiver_values_npz, named_arrays)
        _write_command_provenance(plan, records, resolved, success=True)
        _write_json(
            plan.paths.incident_transport_summary,
            _incident_summary_payload(
                plan,
                records=records,
                run_metrics=tuple(metrics),
                four_band_metrics=four_band_metrics,
                diagnostics=diagnostics,
                npz_sha256=npz_sha256,
                dat_sha256=dat_sha256,
            ),
        )
        return ConventionalRexTransportResult(
            plan=plan,
            arrays=named_arrays,
            run_metrics=tuple(metrics),
            four_band_par_metrics=four_band_metrics,
            scalar_four_band_diagnostics=diagnostics,
            incident_npz_sha256=npz_sha256,
            command_count=len(records),
        )
    except BaseException as exc:
        if materialized:
            plan.paths.incident_transport_summary.unlink(missing_ok=True)
            plan.paths.incident_receiver_values_npz.unlink(missing_ok=True)
            try:
                _write_command_provenance(plan, records, resolved, success=False)
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


def parse_conventional_rex_receiver_rgb(
    text: str,
    *,
    interval_id: str = "receiver",
    expected_receiver_count: int = 1024,
) -> FloatArray:
    """Decode exactly 1,024 finite, nonnegative, equal-channel rows directly."""

    return decode_native_rex_receiver_rgb(
        text,
        interval_id=interval_id,
        expected_receiver_count=expected_receiver_count,
        error_factory=ConventionalRexTransportError,
    )


def compute_incident_run_metrics(
    interval_id: str,
    values: Sequence[float] | FloatArray,
    samples: Sequence[MeshPatchReceiverSample],
    *,
    front_indices: IntArray | None = None,
    back_indices: IntArray | None = None,
    patch_area: FloatArray | None = None,
) -> IncidentRunMetrics:
    if front_indices is None or back_indices is None or patch_area is None:
        front_indices, back_indices, patch_area = _receiver_groups(samples)
    groups = NativeRexReceiverGroups(front_indices, back_indices, patch_area)
    native = compute_native_incident_metrics(
        values, samples, groups=groups, label=interval_id
    )
    units = (
        "far_red_photon_flux_density_umol_m2_s"
        if interval_id == "far_red"
        else "PAR_photon_flux_density_umol_m2_s"
    )
    return IncidentRunMetrics(
        interval_id=interval_id,
        units=units,
        **{name: getattr(native, name) for name in native.__dataclass_fields__},
    )


def compute_scalar_four_band_diagnostics(
    scalar: Sequence[float] | FloatArray,
    four_band: Sequence[float] | FloatArray,
    samples: Sequence[MeshPatchReceiverSample],
    *,
    front_indices: IntArray | None = None,
    back_indices: IntArray | None = None,
    patch_area: FloatArray | None = None,
) -> ScalarFourBandDiagnostics:
    if front_indices is None or back_indices is None or patch_area is None:
        front_indices, back_indices, patch_area = _receiver_groups(samples)
    groups = NativeRexReceiverGroups(front_indices, back_indices, patch_area)
    native = compute_native_scalar_four_band_diagnostics(
        scalar, four_band, samples, groups=groups
    )
    return ScalarFourBandDiagnostics(
        front=_comparison(
            "front",
            native.front.scalar_area_weighted_mean,
            native.front.four_band_area_weighted_mean,
        ),
        back=_comparison(
            "back",
            native.back.scalar_area_weighted_mean,
            native.back.four_band_area_weighted_mean,
        ),
        combined=_comparison(
            "combined_front_plus_back",
            native.combined.scalar_area_weighted_mean,
            native.combined.four_band_area_weighted_mean,
        ),
        receiver_level_rmse=native.receiver_level_rmse,
        receiver_level_relative_rmse=native.receiver_level_relative_rmse,
    )


def format_conventional_rex_incident_npz(
    arrays: Sequence[tuple[str, Sequence[float] | FloatArray]],
) -> bytes:
    return format_native_rex_incident_npz(
        arrays, expected_names=CONVENTIONAL_REX_INCIDENT_ARRAY_ORDER
    )


def _materialize_workspace(plan: ConventionalRexExecutionPlan) -> None:
    root = plan.paths.artifact_root
    _assert_empty_workspace(root)
    directories = {
        plan.request.workspace,
        root,
        plan.paths.source_directory,
        plan.paths.receiver_directory,
        plan.paths.log_directory,
        plan.paths.room_rad.parent,
        *(run.paths.directory for run in plan.runs),
    }
    for directory in sorted(directories, key=str):
        directory.mkdir(parents=True, exist_ok=True)
    materialize_fixture_occlusion(plan.fixture_occlusion)
    _write_text(plan.paths.derived_ies, plan.source_plan.derived_ies.text)
    if _sha256_file(plan.paths.derived_ies) != plan.source_plan.derived_ies.sha256:
        raise ConventionalRexTransportError("staged derived IES hash mismatch.")
    _write_json(
        plan.paths.input_identity,
        {
            "schema_version": 1,
            "execution_plan_id": plan.execution_plan_id,
            "bundle_id": plan.bundle.bundle_id,
            "request": plan.request.scientific_payload(),
            "runtime_workspace": str(plan.request.workspace),
        },
    )
    _write_text(
        plan.paths.bundle_manifest,
        format_conventional_rex_transport_bundle_json(plan.bundle),
    )
    _write_text(
        plan.paths.transport_plan_summary,
        json.dumps(plan.to_payload(), indent=2, sort_keys=True) + "\n",
    )
    _write_text(
        plan.paths.source_payload_json,
        format_conventional_spectral_source_payload_json(plan.bundle.source_payload),
    )
    _write_text(
        plan.paths.optical_payload_json,
        format_conventional_rex_weighted_atr_json(plan.bundle.optical_payload),
    )
    _write_text(
        plan.paths.material_plan_json,
        format_conventional_rex_material_plan_json(plan.bundle.material_plan),
    )
    _write_text(plan.paths.room_rad, plan.room_text)
    _write_text(plan.paths.generic_plant_rad, plan.generic_plant_text)
    _write_text(plan.paths.receiver_rays, plan.receiver_text)
    _write_text(plan.paths.receiver_metadata, plan.receiver_metadata_text)
    for run in plan.runs:
        _validate_material_reconstruction(run)
        _write_text(run.paths.plant_rad, run.plant_text)
        _write_json(
            run.paths.scene_manifest,
            {
                "schema_version": 2,
                "interval_id": run.interval_id,
                "scene_id": run.native_scene_id,
                "part1_scene_id": run.part1.scene_id,
                "source_definition_id": run.part1.source_definition_id,
                "material_identifier": run.part1.material.material_identifier,
                "octree_identity": run.part1.octree_identity,
                "ambient_cache_identity": run.part1.ambient_cache_identity,
                "room_model": production_room_model_payload(),
                "room_model_identity_sha256": (
                    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256
                ),
                "ordered_scene_inputs": [
                    _relative(Path(token), plan.paths.artifact_root)
                    for token in run.oconv_command.argv[2:]
                ],
                "raw_ies2rad_full_box_included": False,
                "horizontal_sensor_grid_included": False,
            },
        )


def _assert_empty_workspace(root: Path) -> None:
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ConventionalRexTransportError(
            f"Conventional Rex artifact root must be absent or empty: {root}"
        )


def _validate_shared_conversion(
    plan: ConventionalRexExecutionPlan,
) -> tuple[str, Mapping[str, object], ConvertedIesOutputContract]:
    expected_names = {
        plan.paths.derived_ies.name,
        plan.paths.raw_converted_rad.name,
        plan.paths.converted_dat.name,
    }
    actual_names = {path.name for path in plan.paths.source_directory.iterdir()}
    if actual_names != expected_names:
        raise ConventionalRexTransportError(
            "ies2rad output set mismatch: "
            f"expected {sorted(expected_names)}, got {sorted(actual_names)}."
        )
    _require_nonempty(plan.paths.raw_converted_rad, "raw converted RAD")
    _require_nonempty(plan.paths.converted_dat, "converted DAT")
    raw_rad = _read_ascii(plan.paths.raw_converted_rad, "raw converted RAD")
    aperture_area = plan.bundle.runs[0].aperture_area_m2
    if not math.isclose(
        aperture_area,
        plan.source_plan.emitting_boundary_area_m2_per_fixture,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ConventionalRexTransportError("typed aperture area changed before conversion validation.")
    contract = validate_converted_ies_output(
        raw_rad,
        expected_dat_reference=plan.paths.converted_dat.name,
        expected_carrier_multiplier=CONVENTIONAL_FULL_OUTPUT_CARRIER_MULTIPLIER,
        emitting_boundary_area_m2=aperture_area,
    )
    dat_bytes = plan.paths.converted_dat.read_bytes()
    dat_metadata = validate_converted_dat(dat_bytes)
    dat_sha256 = _sha256_bytes(dat_bytes)
    if contract.cal_reference != "source.cal":
        raise ConventionalRexTransportError("source.cal must remain canonical.")
    return dat_sha256, dat_metadata, contract


def _materialize_run_sources(
    plan: ConventionalRexExecutionPlan,
    contract: ConvertedIesOutputContract,
    dat_sha256: str,
) -> None:
    if contract.clean_aperture_brightdata_scale <= 0.0:
        raise ConventionalRexTransportError("validated shared source scale is invalid.")
    for run in plan.runs:
        _validate_source_text(plan, run)
        _write_text(run.paths.source_rad, run.source_text)
        _validate_runtime_dat_binding(plan, run, dat_sha256=dat_sha256)


def _validate_native_inputs(
    plan: ConventionalRexExecutionPlan,
    run: ConventionalRexNativeRunPlan,
    *,
    dat_sha256: str,
) -> None:
    expected_hashes = (
        (plan.paths.room_rad, _sha256_text(plan.room_text), "room"),
        (plan.paths.receiver_rays, plan.bundle.receiver_text_sha256, "receiver rays"),
        (
            plan.paths.receiver_metadata,
            plan.receiver_metadata_sha256,
            "receiver metadata",
        ),
        (run.paths.source_rad, run.source_text_sha256, f"{run.interval_id} source"),
        (run.paths.plant_rad, run.plant_text_sha256, f"{run.interval_id} plant"),
        (
            plan.fixture_occlusion.instance_source_path,
            _sha256_text(plan.fixture_occlusion.instance_source_text),
            "fixture body instances",
        ),
    )
    for path, expected_hash, label in expected_hashes:
        _require_nonempty(path, label)
        if _sha256_file(path) != expected_hash:
            raise ConventionalRexTransportError(f"{label} hash mismatch before native execution.")
    _validate_runtime_dat_binding(plan, run, dat_sha256=dat_sha256)
    _validate_source_text(plan, run)
    _validate_material_reconstruction(run)
    _receiver_groups(plan.receiver_samples)
    if _sha256_text(plan.receiver_text) != plan.bundle.receiver_text_sha256:
        raise ConventionalRexTransportError("in-memory receiver ordering hash mismatch.")
    if tuple(run.oconv_command.argv[2:]) != (
        str(plan.paths.room_rad),
        str(run.paths.source_rad),
        str(plan.fixture_occlusion.instance_source_path),
        str(run.paths.plant_rad),
    ):
        raise ConventionalRexTransportError(f"{run.interval_id} scene input order changed.")
    if plan.paths.raw_converted_rad in tuple(Path(token) for token in run.oconv_command.argv):
        raise ConventionalRexTransportError("raw ies2rad full-box geometry entered a scene.")


def _validate_runtime_dat_binding(
    plan: ConventionalRexExecutionPlan,
    run: ConventionalRexNativeRunPlan,
    *,
    dat_sha256: str,
) -> None:
    reference = plan.paths.converted_dat_execution_reference
    for command in (run.oconv_command, run.rtrace_command):
        if command.cwd != plan.paths.artifact_root:
            raise ConventionalRexTransportError(
                f"{command.label} cwd must be the Conventional Rex run root."
            )
        if (command.cwd / reference).resolve() != plan.paths.converted_dat.resolve():
            raise ConventionalRexTransportError(
                f"{command.label} cannot resolve the shared angular DAT."
            )
    _require_nonempty(plan.paths.converted_dat, "shared converted DAT")
    if _sha256_file(plan.paths.converted_dat) != dat_sha256:
        raise ConventionalRexTransportError("shared converted DAT hash changed.")
    source_text = _read_ascii(run.paths.source_rad, f"{run.interval_id} source RAD")
    if source_text.split().count(reference) != 1:
        raise ConventionalRexTransportError(
            f"{run.interval_id} source does not reference the planned shared DAT exactly once."
        )


def _validate_source_text(
    plan: ConventionalRexExecutionPlan,
    run: ConventionalRexNativeRunPlan,
) -> None:
    text = run.source_text
    expected_reference = plan.paths.converted_dat_execution_reference
    if text.split().count(expected_reference) != 1 or "source.cal" not in text:
        raise ConventionalRexTransportError(f"{run.interval_id} source resource binding is invalid.")
    if text.count(" polygon ") != len(plan.source_plan.apertures):
        raise ConventionalRexTransportError(f"{run.interval_id} aperture count is invalid.")
    if text.split().count(run.angular_modifier_id) < 2 or text.split().count(run.light_modifier_id) < 2:
        raise ConventionalRexTransportError(f"{run.interval_id} source modifiers are incomplete.")
    prohibited = ("!", "boxcorr", ".d\n", ".u\n", "xform", "smd")
    if any(token in text.lower() for token in prohibited):
        raise ConventionalRexTransportError(f"{run.interval_id} source contains prohibited geometry or commands.")
    expected_total = (
        run.part1.per_fixture_ppf_umol_s * plan.source_plan.fixture_count
    )
    if not math.isclose(
        expected_total,
        run.part1.whole_layout_ppf_umol_s,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ConventionalRexTransportError(f"{run.interval_id} source PPF closure failed.")
    expected_scale = run.part1.carrier_multiplier / run.part1.aperture_area_m2
    if not math.isclose(
        expected_scale,
        run.part1.flat_source_correction,
        rel_tol=0.0,
        abs_tol=1e-9,
    ) or f"1 {run.part1.flat_source_correction:.17g}" not in text:
        raise ConventionalRexTransportError(f"{run.interval_id} flat-source correction changed.")


def _validate_material_reconstruction(run: ConventionalRexNativeRunPlan) -> None:
    material = run.part1.material
    source = material.source_interval.coefficients
    reconstructed = material.reconstruction
    for label, actual, expected in (
        ("absorptance", reconstructed.absorptance, source.absorptance),
        ("transmittance", reconstructed.total_transmittance, source.transmittance),
        ("reflectance", reconstructed.total_reflectance, source.reflectance),
    ):
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12):
            raise ConventionalRexTransportError(
                f"{run.interval_id} material {label} reconstruction failed."
            )
    if run.plant_text.count(material.radiance_text.strip()) != 1:
        raise ConventionalRexTransportError(
            f"{run.interval_id} committed material text is not present exactly once."
        )
    if run.plant_text.count(" polygon ") != 5248:
        raise ConventionalRexTransportError(f"{run.interval_id} plant polygon count changed.")


def _validate_planned_execution(plan: ConventionalRexExecutionPlan) -> None:
    is_default_10x10 = math.isclose(
        plan.request.room_length_m, 10.0 * FEET_TO_METERS, abs_tol=1e-12
    ) and math.isclose(
        plan.request.room_width_m, 10.0 * FEET_TO_METERS, abs_tol=1e-12
    )
    if is_default_10x10 and (
        plan.source_plan.fixture_count != 4
        or len(plan.source_plan.apertures) != 32
    ):
        raise ConventionalRexTransportError(
            "initial 10 ft x 10 ft Conventional Rex scenario requires "
            "four fixtures and 32 GLB bar apertures."
        )
    if plan.paths.converted_dat != plan.bundle.shared_paths.shared_angular_dat:
        raise ConventionalRexTransportError("shared DAT path diverged from Part 1.")
    if (
        plan.paths.source_payload_json != plan.bundle.shared_paths.source_payload_json
        or plan.paths.optical_payload_json != plan.bundle.shared_paths.optical_payload_json
        or plan.paths.material_plan_json != plan.bundle.shared_paths.material_plan_json
        or plan.paths.room_rad != plan.bundle.shared_paths.room_rad
        or plan.paths.generic_plant_rad != plan.bundle.shared_paths.plant_geometry_rad
        or plan.paths.receiver_rays != plan.bundle.shared_paths.receivers
        or plan.paths.bundle_manifest != plan.bundle.shared_paths.bundle_manifest
    ):
        raise ConventionalRexTransportError("runtime inputs diverged from Part 1 paths.")
    if plan.ies2rad_command.cwd != plan.paths.source_directory:
        raise ConventionalRexTransportError("ies2rad cwd must be the shared source directory.")
    if plan.ies2rad_command.stdout_path is not None:
        raise ConventionalRexTransportError("ies2rad must use its native planned output set.")
    if tuple(run.part1.order_index for run in plan.runs) != tuple(range(6)):
        raise ConventionalRexTransportError("native run order indices changed.")
    if len({run.angular_modifier_id for run in plan.runs}) != 6 or len(
        {run.light_modifier_id for run in plan.runs}
    ) != 6:
        raise ConventionalRexTransportError("run-specific source modifiers must be distinct.")
    if len({run.native_scene_id for run in plan.runs}) != 6:
        raise ConventionalRexTransportError("run-specific native scenes must be distinct.")
    if len({run.paths.octree for run in plan.runs}) != 6 or len(
        {run.paths.ambient_cache for run in plan.runs}
    ) != 6:
        raise ConventionalRexTransportError("octree and ambient-cache paths must be distinct.")
    material_ids = {run.part1.material.material_identifier for run in plan.runs}
    if len(material_ids) != 6:
        raise ConventionalRexTransportError("run-specific Rex materials must be distinct.")
    for run in plan.runs:
        _validate_source_text(plan, run)
        _validate_material_reconstruction(run)
        foreign = material_ids - {run.part1.material.material_identifier}
        if any(identifier in run.plant_text for identifier in foreign):
            raise ConventionalRexTransportError(
                f"{run.interval_id} plant contains another run's material."
            )
        if run.oconv_command.stdout_mode != "binary":
            raise ConventionalRexTransportError("oconv stdout must be binary.")
        if run.rtrace_command.stdout_mode != "text":
            raise ConventionalRexTransportError("rtrace stdout must be text.")
        if run.rtrace_command.stdin_path != plan.paths.receiver_rays:
            raise ConventionalRexTransportError("all traces must share receiver rays.")
        if run.rtrace_command.argv[1:5] != (
            "-h",
            "-I+",
            "-n",
            str(plan.request.threads),
        ):
            raise ConventionalRexTransportError("rtrace irradiance/thread flags changed.")
        if "-af" not in run.rtrace_command.argv or Path(
            run.rtrace_command.argv[run.rtrace_command.argv.index("-af") + 1]
        ) != run.paths.ambient_cache:
            raise ConventionalRexTransportError("rtrace ambient-cache binding changed.")
        if run.oconv_command.cwd != plan.paths.artifact_root or run.rtrace_command.cwd != plan.paths.artifact_root:
            raise ConventionalRexTransportError("oconv/rtrace cwd must be the run root.")
        argv = (*run.oconv_command.argv, *run.rtrace_command.argv)
        command_names = {Path(token).name.lower() for token in argv}
        if command_names.intersection({"xform"}):
            raise ConventionalRexTransportError("prohibited native command entered the plan.")
        if any("smd" in name for name in command_names):
            raise ConventionalRexTransportError("SMD command input entered the Conventional plan.")


def _receiver_metadata_json(
    bundle: ConventionalRexTransportBundlePlan,
    samples: Sequence[MeshPatchReceiverSample],
) -> str:
    payload = {
        "schema_version": 1,
        "receiver_identity": bundle.receiver_identity,
        "receiver_text_sha256": bundle.receiver_text_sha256,
        "receiver_count": len(samples),
        "physical_patch_count": len(samples) // 2,
        "ordering": "stable_patch_order_then_front_back",
        "physical_patch_area_policy": bundle.scientific_payload()[
            "physical_patch_area_policy"
        ],
        "receivers": [item.to_dict() for item in samples],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _receiver_groups(
    samples: Sequence[MeshPatchReceiverSample],
) -> tuple[IntArray, IntArray, FloatArray]:
    if len(samples) != 1024:
        raise ConventionalRexTransportError(
            f"expected 1024 receiver rows, got {len(samples)}."
        )
    groups = build_native_rex_receiver_groups(
        samples, error_factory=ConventionalRexTransportError
    )
    return (
        groups.front_indices,
        groups.back_indices,
        groups.physical_patch_area_m2,
    )


def _run_recorded(
    plan: ConventionalRexExecutionPlan,
    runner: CommandRunner,
    command: CommandSpec,
    *,
    stderr_path: Path,
    interval_id: str | None,
    records: list[_CommandRecord],
) -> RunnerResult:
    result = runner.run(command, stderr_path=stderr_path)
    if result.argv != command.argv or result.stdout_path != command.stdout_path:
        raise ConventionalRexTransportError(
            f"{command.label} runner result does not match CommandSpec."
        )
    if not stderr_path.is_file():
        atomic_write_text(stderr_path, result.stderr_text or "")
    record = _CommandRecord(
        sequence_index=len(records),
        interval_id=interval_id,
        command=command,
        result=result,
        stderr_sha256=_sha256_file(stderr_path),
    )
    records.append(record)
    _write_command_provenance(plan, records, None, success=False)
    return result


def _require_success(command: CommandSpec, result: RunnerResult) -> None:
    if not result.success or result.returncode != 0:
        raise ConventionalRexTransportError(
            result.failure_message or f"{command.label} failed."
        )


def _write_command_provenance(
    plan: ConventionalRexExecutionPlan,
    records: Sequence[_CommandRecord],
    executables: ConventionalRexExecutables | None,
    *,
    success: bool,
) -> None:
    root = plan.paths.artifact_root
    payload = {
        "schema_version": 1,
        "success": success,
        "execution_plan_id": plan.execution_plan_id,
        "expected_native_command_count": CONVENTIONAL_REX_NATIVE_COMMAND_COUNT,
        "recorded_native_command_count": len(records),
        "execution_order": [item.command.label for item in records],
        "radiance_version": {
            "rtrace_version": (
                None if executables is None else executables.rtrace_version_text
            ),
            "oconv_version": None,
        },
        "commands": [item.to_payload(root) for item in records],
    }
    atomic_write_text(
        plan.paths.command_provenance_summary,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def _source_conversion_payload(
    plan: ConventionalRexExecutionPlan,
    *,
    dat_sha256: str,
    dat_metadata: Mapping[str, object],
    contract: ConvertedIesOutputContract,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source_plan_id": plan.source_plan.source_plan_id,
        "derived_ies": {
            "path": _relative(plan.paths.derived_ies, plan.paths.artifact_root),
            "sha256": plan.source_plan.derived_ies.sha256,
        },
        "raw_converted_rad": {
            "path": _relative(plan.paths.raw_converted_rad, plan.paths.artifact_root),
            "sha256": _sha256_file(plan.paths.raw_converted_rad),
            "role": "provenance_only_full_box_excluded_from_all_final_scenes",
        },
        "shared_converted_dat": {
            "path": _relative(plan.paths.converted_dat, plan.paths.artifact_root),
            "execution_reference": plan.paths.converted_dat_execution_reference,
            "sha256": dat_sha256,
            "metadata": dict(dat_metadata),
            "generated_once": True,
            "copied_per_run": False,
        },
        "canonical_cal_reference": contract.cal_reference,
        "run_source_count": len(plan.runs),
        "run_source_hashes": {
            run.interval_id: run.source_text_sha256 for run in plan.runs
        },
    }


def _incident_summary_payload(
    plan: ConventionalRexExecutionPlan,
    *,
    records: Sequence[_CommandRecord],
    run_metrics: tuple[IncidentRunMetrics, ...],
    four_band_metrics: IncidentRunMetrics,
    diagnostics: ScalarFourBandDiagnostics,
    npz_sha256: str,
    dat_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": CONVENTIONAL_REX_EXECUTION_SCHEMA_VERSION,
        "payload_type": CONVENTIONAL_REX_EXECUTION_PAYLOAD_TYPE,
        "success": True,
        "execution_plan_id": plan.execution_plan_id,
        "bundle_id": plan.bundle.bundle_id,
        "room_model": production_room_model_payload(),
        "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
        "run_order": list(CONVENTIONAL_REX_RUN_ORDER),
        "receiver_contract": {
            "receiver_identity": plan.bundle.receiver_identity,
            "receiver_count": 1024,
            "front_receiver_count": 512,
            "back_receiver_count": 512,
            "physical_patch_count": 512,
            "receiver_rays_path": _relative(
                plan.paths.receiver_rays, plan.paths.artifact_root
            ),
            "receiver_metadata_path": _relative(
                plan.paths.receiver_metadata, plan.paths.artifact_root
            ),
            "receiver_rays_sha256": plan.bundle.receiver_text_sha256,
            "receiver_metadata_sha256": plan.receiver_metadata_sha256,
            "ordering": "stable_patch_order_then_front_back",
            "combined_area_policy": (
                "one physical patch area multiplies front plus back incident PFD once"
            ),
        },
        "shared_source": {
            "angular_dat_identity": plan.bundle.shared_angular_dat_identity,
            "angular_dat_sha256": dat_sha256,
            "converted_once": True,
            "source_cal_reference": "source.cal",
        },
        "incident_arrays": {
            "path": _relative(
                plan.paths.incident_receiver_values_npz,
                plan.paths.artifact_root,
            ),
            "sha256": npz_sha256,
            "keys": list(CONVENTIONAL_REX_INCIDENT_ARRAY_ORDER),
            "scalar_par_included_in_four_band_sum": False,
            "far_red_included_in_par": False,
            "post_trace_179_conversion": False,
            "post_trace_spectral_fraction": False,
        },
        "runs": [item.to_payload() for item in run_metrics],
        "run_bindings": [
            {
                "interval_id": run.interval_id,
                "per_fixture_ppf_umol_s": run.part1.per_fixture_ppf_umol_s,
                "whole_layout_ppf_umol_s": run.part1.whole_layout_ppf_umol_s,
                "carrier_multiplier": run.part1.carrier_multiplier,
                "aperture_area_m2": run.part1.aperture_area_m2,
                "flat_source_correction": run.part1.flat_source_correction,
                "source_definition_id": run.part1.source_definition_id,
                "material_identifier": run.part1.material.material_identifier,
                "scene_id": run.native_scene_id,
                "octree_identity": run.part1.octree_identity,
                "ambient_cache_identity": run.part1.ambient_cache_identity,
            }
            for run in plan.runs
        ],
        "run_artifacts": [
            {
                "interval_id": run.interval_id,
                "source_rad_sha256": _sha256_file(run.paths.source_rad),
                "plant_rad_sha256": _sha256_file(run.paths.plant_rad),
                "octree_sha256": _sha256_file(run.paths.octree),
                "ambient_cache_sha256": _sha256_file(run.paths.ambient_cache),
                "raw_rgb_sha256": _sha256_file(run.paths.raw_rgb),
                "decoded_npy_sha256": _sha256_file(run.paths.decoded_npy),
            }
            for run in plan.runs
        ],
        "four_band_par": four_band_metrics.to_payload(),
        "scalar_versus_four_band": diagnostics.to_payload(),
        "native_commands": {
            "expected": CONVENTIONAL_REX_NATIVE_COMMAND_COUNT,
            "recorded": len(records),
            "ies2rad": sum(
                record.command.label.startswith("convert_") for record in records
            ),
            "oconv": sum(
                record.command.label.startswith("compile_") for record in records
            ),
            "rtrace": sum(
                record.command.label.startswith("trace_") for record in records
            ),
            "sequential_local_execution": True,
            "shell": False,
        },
        "manual_validation": {
            "scientific_pass_threshold": None,
            "reconciliation_scale": None,
            "required": True,
        },
        "absorbed_photon_processing": False,
        "limitations": list(CONVENTIONAL_REX_EXECUTION_LIMITATIONS),
    }


def _comparison(scope: str, scalar: float, four_band: float) -> IncidentComparison:
    absolute = abs(scalar - four_band)
    return IncidentComparison(
        scope=scope,
        scalar_area_weighted_mean=scalar,
        four_band_area_weighted_mean=four_band,
        absolute_difference=absolute,
        relative_difference=(None if scalar == 0.0 else absolute / scalar),
    )


def _validate_decoded_npy(path: Path, expected: FloatArray, interval_id: str) -> None:
    try:
        actual = np.load(path, allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise ConventionalRexTransportError(
            f"{interval_id} decoded NPY could not be read safely."
        ) from exc
    if (
        actual.dtype != np.dtype(np.float64)
        or actual.shape != (1024,)
        or not np.array_equal(actual, expected)
    ):
        raise ConventionalRexTransportError(
            f"{interval_id} decoded NPY failed deterministic round-trip validation."
        )


def _validate_incident_npz(
    path: Path,
    expected: Sequence[tuple[str, FloatArray]],
) -> None:
    try:
        with np.load(path, allow_pickle=False) as archive:
            if tuple(archive.files) != CONVENTIONAL_REX_INCIDENT_ARRAY_ORDER:
                raise ConventionalRexTransportError("incident NPZ key order is invalid.")
            for name, values in expected:
                if not np.array_equal(archive[name], values):
                    raise ConventionalRexTransportError(
                        f"incident NPZ array mismatch: {name}."
                    )
    except (OSError, ValueError) as exc:
        raise ConventionalRexTransportError("incident NPZ validation failed.") from exc


def _write_text(path: Path, text: str) -> None:
    if path.exists():
        raise ConventionalRexTransportError(f"refusing to overwrite artifact: {path}")
    atomic_write_text(path, text)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise ConventionalRexTransportError(f"refusing to overwrite artifact: {path}")
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _require_nonempty(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ConventionalRexTransportError(f"{label} is missing or empty: {path}")


def _read_ascii(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise ConventionalRexTransportError(f"{label} must be readable ASCII: {path}") from exc


def _relative_token(token: str, root: Path) -> str:
    candidate = Path(token)
    if candidate.is_absolute():
        try:
            return candidate.relative_to(root).as_posix()
        except ValueError:
            return str(candidate)
    return token


def _relative(path: str | Path, root: Path) -> str:
    candidate = Path(path)
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return str(candidate)


def _reject_repository_workspace(workspace: Path) -> None:
    repository = Path(__file__).resolve().parents[3]
    if (
        (workspace == repository or repository in workspace.parents)
        and not is_default_managed_runtime_descendant(workspace, repository)
    ):
        raise ValueError("workspace must be outside the repository.")


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


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())
