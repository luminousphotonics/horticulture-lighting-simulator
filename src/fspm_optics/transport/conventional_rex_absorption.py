"""Pure Conventional Rex incident-to-absorbed optical post-processing."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Final, Mapping, Sequence
import zipfile

import numpy as np
from numpy.typing import NDArray

from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_SPECTRAL_SOURCE_ID,
    format_conventional_spectral_source_payload_json,
)
from fspm_optics.geometry.room import (
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    production_room_model_payload,
)
from fspm_optics.optics.conventional_rex import (
    format_conventional_rex_material_plan_json,
    format_conventional_rex_weighted_atr_json,
)
from fspm_optics.transport.basis.atomic import atomic_write_bytes, atomic_write_text
from fspm_optics.transport.conventional_rex import (
    CONVENTIONAL_BAND_ORDER,
    CONVENTIONAL_REX_RUN_ORDER,
    format_conventional_rex_transport_bundle_json,
)
from fspm_optics.transport.conventional_rex_execution import (
    CONVENTIONAL_REX_EXECUTION_PAYLOAD_TYPE,
    CONVENTIONAL_REX_EXECUTION_SCHEMA_VERSION,
    CONVENTIONAL_REX_INCIDENT_ARRAY_ORDER,
    CONVENTIONAL_REX_NATIVE_COMMAND_COUNT,
    ConventionalRexExecutables,
    ConventionalRexExecutionPlan,
    ConventionalRexTransportRequest,
    plan_conventional_rex_execution,
)
from fspm_optics.transport.conventional_scalar import validate_converted_dat
from fspm_optics.transport.five_band_absorption import (
    ComputedAbsorbedMetrics,
    SourceNeutralAbsorbedMetricsInput,
    compute_source_neutral_absorbed_metrics,
)

FloatArray = NDArray[np.float64]

CONVENTIONAL_ABSORBED_SCHEMA_VERSION: Final = 1
CONVENTIONAL_ABSORBED_PAYLOAD_TYPE: Final = (
    "fspm_optics_conventional_rex_absorbed_metrics"
)
CONVENTIONAL_ABSORBED_CLAIM: Final = (
    "Optical incident-photon partitions at the validated Conventional Rex leaf "
    "surface; no photosynthesis, growth, yield, or electrical-efficiency claim."
)
CONVENTIONAL_ABSORBED_LIMITATIONS: Final[tuple[str, ...]] = (
    "Transmitted and reflected terms are local interaction partitions, not net escaped light.",
    "Four-band PAR uses blue, green, orange, and red only.",
    "Far-red remains separate from PAR.",
    "Scalar absorbed PAR is a validation diagnostic and is not added to four-band PAR.",
    "No reconciliation scale or scientific pass threshold is applied.",
)
CONVENTIONAL_ABSORBED_NPZ_ORDER: Final[tuple[str, ...]] = (
    "patch_index",
    "leaf_index",
    "front_receiver_index",
    "back_receiver_index",
    "patch_area_m2",
    "absorptance",
    "transmittance",
    "reflectance",
    "front_incident_pfd",
    "back_incident_pfd",
    "combined_incident_pfd",
    "front_absorbed_pfd",
    "back_absorbed_pfd",
    "combined_absorbed_pfd",
    "front_transmitted_pfd",
    "back_transmitted_pfd",
    "combined_transmitted_pfd",
    "front_reflected_pfd",
    "back_reflected_pfd",
    "combined_reflected_pfd",
    "incident_flux",
    "absorbed_flux",
    "transmitted_flux",
    "reflected_flux",
    "local_closure_error_flux",
    "four_band_front_incident_pfd",
    "four_band_back_incident_pfd",
    "four_band_combined_incident_pfd",
    "four_band_front_absorbed_pfd",
    "four_band_back_absorbed_pfd",
    "four_band_combined_absorbed_pfd",
    "far_red_combined_incident_pfd",
    "far_red_combined_absorbed_pfd",
    "scalar_front_incident_pfd",
    "scalar_back_incident_pfd",
    "scalar_combined_incident_pfd",
    "scalar_front_absorbed_pfd",
    "scalar_back_absorbed_pfd",
    "scalar_combined_absorbed_pfd",
    "scalar_minus_four_band_absorbed_pfd",
)


class ConventionalRexAbsorbedMetricsError(RuntimeError):
    """Conventional native inputs or absorbed outputs failed strict validation."""


@dataclass(frozen=True, slots=True)
class LoadedConventionalRexIncidentWorkspace:
    workspace_root: Path
    artifact_root: Path
    plan: ConventionalRexExecutionPlan
    incident_summary: Mapping[str, Any]
    incident_summary_sha256: str
    incident_npz_sha256: str
    incident_arrays: tuple[tuple[str, FloatArray], ...]
    native_artifact_hashes: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]

    def array(self, interval_id: str) -> FloatArray:
        key = f"{interval_id}_umol_m2_s"
        for name, value in self.incident_arrays:
            if name == key:
                return value
        raise KeyError(f"unknown Conventional incident interval: {interval_id!r}")


@dataclass(frozen=True, slots=True)
class ConventionalAbsorbedPatchArrays:
    arrays: tuple[tuple[str, NDArray[Any]], ...]

    def __post_init__(self) -> None:
        if tuple(name for name, _ in self.arrays) != CONVENTIONAL_ABSORBED_NPZ_ORDER:
            raise ValueError("Conventional absorbed NPZ arrays are incomplete or out of order.")
        frozen: list[tuple[str, NDArray[Any]]] = []
        for name, value in self.arrays:
            array = np.array(value, copy=True)
            if array.dtype.hasobject:
                raise ValueError(f"absorbed array {name} may not use object dtype.")
            if not np.all(np.isfinite(array)):
                raise ValueError(f"absorbed array {name} must be finite.")
            array.setflags(write=False)
            frozen.append((name, array))
        object.__setattr__(self, "arrays", tuple(frozen))

    def as_dict(self) -> dict[str, NDArray[Any]]:
        return dict(self.arrays)


@dataclass(frozen=True, slots=True)
class ScalarAbsorbedValidationDiagnostic:
    scalar_absorptance: float
    scalar_whole_plant_absorbed_flux: float
    four_band_whole_plant_absorbed_flux: float
    whole_plant_signed_difference: float
    whole_plant_absolute_difference: float
    whole_plant_relative_difference: float | None
    scalar_area_weighted_absorbed_pfd: float
    four_band_area_weighted_absorbed_pfd: float
    area_weighted_signed_difference: float
    area_weighted_absolute_difference: float
    area_weighted_relative_difference: float | None
    front_absorbed_flux_signed_difference: float
    back_absorbed_flux_signed_difference: float
    receiver_level_rmse: float
    receiver_level_relative_rmse: float | None
    patch_level_rmse: float
    patch_level_relative_rmse: float | None
    relative_rmse_normalization_basis: str = "RMS of scalar absorbed-PFD reference"
    reconciliation_scale_applied: bool = False
    scientific_pass_threshold_applied: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.scalar_absorptance <= 1.0:
            raise ValueError("scalar absorptance must be a fraction.")
        for name in (
            "scalar_whole_plant_absorbed_flux",
            "four_band_whole_plant_absorbed_flux",
            "whole_plant_absolute_difference",
            "scalar_area_weighted_absorbed_pfd",
            "four_band_area_weighted_absorbed_pfd",
            "area_weighted_absolute_difference",
            "receiver_level_rmse",
            "patch_level_rmse",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative.")
        for name in (
            "whole_plant_signed_difference",
            "area_weighted_signed_difference",
            "front_absorbed_flux_signed_difference",
            "back_absorbed_flux_signed_difference",
        ):
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError(f"{name} must be finite.")
        for name in (
            "whole_plant_relative_difference",
            "area_weighted_relative_difference",
            "receiver_level_relative_rmse",
            "patch_level_relative_rmse",
        ):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0.0):
                raise ValueError(f"{name} must be non-negative when present.")
        if self.reconciliation_scale_applied or self.scientific_pass_threshold_applied:
            raise ValueError("absorbed validation may not scale or invent a pass threshold.")

    def to_payload(self) -> dict[str, object]:
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class ComputedConventionalRexAbsorbedMetrics:
    spectral: ComputedAbsorbedMetrics
    scalar_diagnostic: ScalarAbsorbedValidationDiagnostic
    patch_arrays: ConventionalAbsorbedPatchArrays
    maximum_local_closure_error_flux: float
    maximum_local_closure_error_pfd: float


@dataclass(frozen=True, slots=True)
class ConventionalRexAbsorbedMetricsResult:
    loaded: LoadedConventionalRexIncidentWorkspace
    computed: ComputedConventionalRexAbsorbedMetrics
    summary: Mapping[str, Any]
    summary_path: Path
    patch_npz_path: Path
    summary_id: str
    patch_npz_sha256: str


def load_conventional_rex_incident_workspace(
    workspace: str | Path,
) -> LoadedConventionalRexIncidentWorkspace:
    """Strictly load a successful, immutable Phase 23B native workspace."""

    root = Path(workspace).expanduser().resolve()
    artifact_root = root / "conventional_rex_transport"
    input_identity = _load_json(artifact_root / "input_identity.json", "input identity")
    if input_identity.get("schema_version") != 1:
        raise ConventionalRexAbsorbedMetricsError("unsupported input identity schema.")
    request_payload = _mapping(input_identity, "request")
    dimensions = request_payload.get("room_dimensions_m")
    if (
        not isinstance(dimensions, list)
        or len(dimensions) != 2
        or any(isinstance(item, bool) or not isinstance(item, int | float) for item in dimensions)
    ):
        raise ConventionalRexAbsorbedMetricsError("input room dimensions are invalid.")
    if request_payload.get("layout_policy") != "practical":
        raise ConventionalRexAbsorbedMetricsError("Conventional layout policy is invalid.")
    if input_identity.get("runtime_workspace") != str(root):
        raise ConventionalRexAbsorbedMetricsError("input workspace identity mismatch.")
    try:
        request = ConventionalRexTransportRequest.from_meters(
            workspace=root,
            room_length_m=float(dimensions[0]),
            room_width_m=float(dimensions[1]),
            threads=_integer(request_payload, "threads"),
            quality_profile=_string(request_payload, "quality_profile"),
            reference_plane_z_m=_number(request_payload, "reference_plane_z_m"),
            mount_height_m=_number(request_payload, "mount_height_m"),
        )
        plan = plan_conventional_rex_execution(
            request,
            executables=ConventionalRexExecutables(
                Path("ies2rad"), Path("oconv"), Path("rtrace")
            ),
        )
    except (ValueError, RuntimeError) as exc:
        raise ConventionalRexAbsorbedMetricsError(
            f"could not reconstruct the typed native plan: {exc}"
        ) from exc
    if (
        input_identity.get("execution_plan_id") != plan.execution_plan_id
        or input_identity.get("bundle_id") != plan.bundle.bundle_id
        or request_payload != plan.request.scientific_payload()
    ):
        raise ConventionalRexAbsorbedMetricsError("input scientific identity mismatch.")

    _validate_exact_json(
        plan.paths.transport_plan_summary,
        plan.to_payload(),
        "execution plan summary",
    )
    _validate_exact_text(
        plan.paths.bundle_manifest,
        format_conventional_rex_transport_bundle_json(plan.bundle),
        "Part 1 bundle manifest",
    )
    _validate_exact_text(
        plan.paths.source_payload_json,
        format_conventional_spectral_source_payload_json(plan.bundle.source_payload),
        "Conventional source payload",
    )
    _validate_exact_text(
        plan.paths.optical_payload_json,
        format_conventional_rex_weighted_atr_json(plan.bundle.optical_payload),
        "Conventional optical payload",
    )
    _validate_exact_text(
        plan.paths.material_plan_json,
        format_conventional_rex_material_plan_json(plan.bundle.material_plan),
        "Conventional material plan",
    )
    for path, expected, label in (
        (plan.paths.room_rad, plan.room_text, "room RAD"),
        (plan.paths.generic_plant_rad, plan.generic_plant_text, "generic plant RAD"),
        (plan.paths.receiver_rays, plan.receiver_text, "receiver rays"),
        (
            plan.paths.receiver_metadata,
            plan.receiver_metadata_text,
            "receiver metadata",
        ),
    ):
        _validate_exact_text(path, expected, label)
    for run in plan.runs:
        _validate_exact_json(
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
            f"{run.interval_id} scene manifest",
        )
        _validate_exact_text(
            run.paths.source_rad,
            run.source_text,
            f"{run.interval_id} source RAD",
        )
        _validate_exact_text(
            run.paths.plant_rad,
            run.plant_text,
            f"{run.interval_id} plant RAD",
        )

    failure_path = plan.paths.failure_summary
    if failure_path.exists():
        failure = _load_json(failure_path, "failure summary")
        if failure.get("success") is False:
            raise ConventionalRexAbsorbedMetricsError(
                "native workspace contains an incomplete failure summary."
            )
        raise ConventionalRexAbsorbedMetricsError("unexpected native failure artifact exists.")

    incident_path = plan.paths.incident_transport_summary
    incident = _load_json(incident_path, "incident transport summary")
    _validate_incident_summary(incident, plan)
    source_conversion = _load_json(
        plan.paths.source_conversion_summary,
        "source conversion summary",
    )
    _validate_source_conversion_summary(source_conversion, plan)
    provenance = _load_json(
        plan.paths.command_provenance_summary,
        "command provenance summary",
    )
    _validate_command_provenance(provenance, plan)

    incident_npz_path = plan.paths.incident_receiver_values_npz
    incident_npz_hash = _sha256_file(incident_npz_path)
    incident_artifact = _mapping(incident, "incident_arrays")
    if (
        incident_artifact.get("sha256") != incident_npz_hash
        or tuple(incident_artifact.get("keys", ()))
        != CONVENTIONAL_REX_INCIDENT_ARRAY_ORDER
        or incident_artifact.get("post_trace_179_conversion") is not False
        or incident_artifact.get("post_trace_spectral_fraction") is not False
        or incident_artifact.get("scalar_par_included_in_four_band_sum") is not False
        or incident_artifact.get("far_red_included_in_par") is not False
    ):
        raise ConventionalRexAbsorbedMetricsError("incident NPZ authority is invalid.")
    arrays = _load_incident_npz(incident_npz_path)
    native_hashes = _validate_native_run_artifacts(incident, plan, arrays)

    source_model_id = plan.bundle.source_payload.spectral_source_id
    if source_model_id != CONVENTIONAL_SPECTRAL_SOURCE_ID or "smd" in source_model_id.lower():
        raise ConventionalRexAbsorbedMetricsError("Conventional workspace has an invalid source identity.")
    if "smd" in plan.bundle.optical_payload.intervals.source.source_model_id.lower():
        raise ConventionalRexAbsorbedMetricsError("SMD optics identity entered the Conventional workspace.")

    return LoadedConventionalRexIncidentWorkspace(
        workspace_root=root,
        artifact_root=artifact_root,
        plan=plan,
        incident_summary=incident,
        incident_summary_sha256=_sha256_file(incident_path),
        incident_npz_sha256=incident_npz_hash,
        incident_arrays=arrays,
        native_artifact_hashes=native_hashes,
    )


def _validate_incident_summary(
    summary: Mapping[str, Any],
    plan: ConventionalRexExecutionPlan,
) -> None:
    if (
        summary.get("schema_version") != CONVENTIONAL_REX_EXECUTION_SCHEMA_VERSION
        or summary.get("payload_type") != CONVENTIONAL_REX_EXECUTION_PAYLOAD_TYPE
        or summary.get("success") is not True
    ):
        raise ConventionalRexAbsorbedMetricsError("incident summary is not a successful supported payload.")
    if (
        summary.get("execution_plan_id") != plan.execution_plan_id
        or summary.get("bundle_id") != plan.bundle.bundle_id
        or tuple(summary.get("run_order", ())) != CONVENTIONAL_REX_RUN_ORDER
    ):
        raise ConventionalRexAbsorbedMetricsError("incident execution or run-order identity mismatch.")
    if (
        summary.get("room_model") != production_room_model_payload()
        or summary.get("room_model_identity_sha256")
        != PRODUCTION_ROOM_MODEL_IDENTITY_SHA256
    ):
        raise ConventionalRexAbsorbedMetricsError(
            "incident room authority mismatch."
        )
    receiver = _mapping(summary, "receiver_contract")
    expected_receiver = {
        "receiver_identity": plan.bundle.receiver_identity,
        "receiver_count": 1024,
        "front_receiver_count": 512,
        "back_receiver_count": 512,
        "physical_patch_count": 512,
        "receiver_rays_path": plan.paths.run_root_relative_reference(plan.paths.receiver_rays),
        "receiver_metadata_path": plan.paths.run_root_relative_reference(plan.paths.receiver_metadata),
        "receiver_rays_sha256": plan.bundle.receiver_text_sha256,
        "receiver_metadata_sha256": plan.receiver_metadata_sha256,
        "ordering": "stable_patch_order_then_front_back",
        "combined_area_policy": (
            "one physical patch area multiplies front plus back incident PFD once"
        ),
    }
    if receiver != expected_receiver:
        raise ConventionalRexAbsorbedMetricsError("incident receiver contract mismatch.")
    shared = _mapping(summary, "shared_source")
    if (
        shared.get("angular_dat_identity") != plan.bundle.shared_angular_dat_identity
        or shared.get("angular_dat_sha256") != _sha256_file(plan.paths.converted_dat)
        or shared.get("converted_once") is not True
        or shared.get("source_cal_reference") != "source.cal"
    ):
        raise ConventionalRexAbsorbedMetricsError("shared angular DAT identity mismatch.")
    commands = _mapping(summary, "native_commands")
    if commands != {
        "expected": CONVENTIONAL_REX_NATIVE_COMMAND_COUNT,
        "recorded": CONVENTIONAL_REX_NATIVE_COMMAND_COUNT,
        "ies2rad": 1,
        "oconv": 7,
        "rtrace": 6,
        "sequential_local_execution": True,
        "shell": False,
    }:
        raise ConventionalRexAbsorbedMetricsError("native command closure is invalid.")
    bindings = summary.get("run_bindings")
    if not isinstance(bindings, list) or len(bindings) != 6:
        raise ConventionalRexAbsorbedMetricsError("incident run bindings are incomplete.")
    for raw, run in zip(bindings, plan.runs, strict=True):
        if not isinstance(raw, Mapping) or raw != {
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
        }:
            raise ConventionalRexAbsorbedMetricsError(
                f"{run.interval_id} incident binding identity mismatch."
            )


def _validate_command_provenance(
    payload: Mapping[str, Any],
    plan: ConventionalRexExecutionPlan,
) -> None:
    if (
        payload.get("success") is not True
        or payload.get("execution_plan_id") != plan.execution_plan_id
        or payload.get("expected_native_command_count")
        != CONVENTIONAL_REX_NATIVE_COMMAND_COUNT
        or payload.get("recorded_native_command_count")
        != CONVENTIONAL_REX_NATIVE_COMMAND_COUNT
    ):
        raise ConventionalRexAbsorbedMetricsError("native command provenance is incomplete.")
    expected_commands = [
        *(
            (
                shape.compile_command,
                None,
                plan.paths.log_directory
                / f"{shape.shape_id}.oconv.stderr.log",
            )
            for shape in plan.fixture_occlusion.shapes
        ),
        (plan.ies2rad_command, None, plan.paths.ies2rad_stderr),
        *[
            item
            for run in plan.runs
            for item in (
                (run.oconv_command, run.interval_id, run.paths.oconv_stderr),
                (run.rtrace_command, run.interval_id, run.paths.rtrace_stderr),
            )
        ],
    ]
    expected_labels = [item[0].label for item in expected_commands]
    if payload.get("execution_order") != expected_labels:
        raise ConventionalRexAbsorbedMetricsError("native command execution order mismatch.")
    commands = payload.get("commands")
    if (
        not isinstance(commands, list)
        or len(commands) != CONVENTIONAL_REX_NATIVE_COMMAND_COUNT
    ):
        raise ConventionalRexAbsorbedMetricsError("native command records are incomplete.")
    for index, (record, expected) in enumerate(
        zip(commands, expected_commands, strict=True)
    ):
        command, interval_id, stderr_path = expected
        expected_argv = [_relative_token(token, plan.paths.artifact_root) for token in command.argv]
        if (
            not isinstance(record, Mapping)
            or record.get("sequence_index") != index
            or record.get("interval_id") != interval_id
            or record.get("label") != command.label
            or record.get("return_code") != 0
            or record.get("success") is not True
            or record.get("shell") is not False
            or not isinstance(record.get("argv"), list)
            or Path(str(record.get("argv", [""])[0])).name
            != Path(expected_argv[0]).name
            or record.get("argv", [])[1:] != expected_argv[1:]
            or record.get("cwd") != _optional_relative(command.cwd, plan.paths.artifact_root)
            or record.get("stdin")
            != _optional_relative(command.stdin_path, plan.paths.artifact_root)
            or record.get("stdout")
            != _optional_relative(command.stdout_path, plan.paths.artifact_root)
            or record.get("stdout_mode") != command.stdout_mode
            or record.get("stderr") != _relative(stderr_path, plan.paths.artifact_root)
            or record.get("stderr_sha256") != _sha256_file(stderr_path)
            or not _is_sha256(record.get("stderr_sha256"))
        ):
            raise ConventionalRexAbsorbedMetricsError(
                f"native command provenance mismatch at sequence {index}."
            )


def _validate_source_conversion_summary(
    payload: Mapping[str, Any],
    plan: ConventionalRexExecutionPlan,
) -> None:
    if payload.get("schema_version") != 1 or payload.get(
        "source_plan_id"
    ) != plan.source_plan.source_plan_id:
        raise ConventionalRexAbsorbedMetricsError("source conversion identity mismatch.")
    derived = _mapping(payload, "derived_ies")
    raw = _mapping(payload, "raw_converted_rad")
    dat = _mapping(payload, "shared_converted_dat")
    dat_bytes = plan.paths.converted_dat.read_bytes()
    expected_dat_hash = _sha256_bytes(dat_bytes)
    if derived != {
        "path": plan.paths.run_root_relative_reference(plan.paths.derived_ies),
        "sha256": plan.source_plan.derived_ies.sha256,
    }:
        raise ConventionalRexAbsorbedMetricsError("derived IES authority mismatch.")
    if raw != {
        "path": plan.paths.run_root_relative_reference(plan.paths.raw_converted_rad),
        "sha256": _sha256_file(plan.paths.raw_converted_rad),
        "role": "provenance_only_full_box_excluded_from_all_final_scenes",
    }:
        raise ConventionalRexAbsorbedMetricsError("raw converted RAD provenance mismatch.")
    if dat != {
        "path": plan.paths.run_root_relative_reference(plan.paths.converted_dat),
        "execution_reference": plan.paths.converted_dat_execution_reference,
        "sha256": expected_dat_hash,
        "metadata": validate_converted_dat(dat_bytes),
        "generated_once": True,
        "copied_per_run": False,
    }:
        raise ConventionalRexAbsorbedMetricsError("shared converted DAT summary mismatch.")
    if (
        payload.get("canonical_cal_reference") != "source.cal"
        or payload.get("run_source_count") != 6
        or payload.get("run_source_hashes")
        != {run.interval_id: run.source_text_sha256 for run in plan.runs}
    ):
        raise ConventionalRexAbsorbedMetricsError("adapted source conversion summary mismatch.")


def _load_incident_npz(path: Path) -> tuple[tuple[str, FloatArray], ...]:
    try:
        with np.load(path, allow_pickle=False) as archive:
            if tuple(archive.files) != CONVENTIONAL_REX_INCIDENT_ARRAY_ORDER:
                raise ConventionalRexAbsorbedMetricsError("incident NPZ members are invalid.")
            loaded = tuple(
                (name, np.array(archive[name], copy=True)) for name in archive.files
            )
    except FileNotFoundError as exc:
        raise ConventionalRexAbsorbedMetricsError(f"incident NPZ is missing: {path}") from exc
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise ConventionalRexAbsorbedMetricsError(f"incident NPZ is malformed: {path}") from exc
    normalized: list[tuple[str, FloatArray]] = []
    for name, raw in loaded:
        if (
            raw.shape != (1024,)
            or raw.dtype.hasobject
            or np.issubdtype(raw.dtype, np.bool_)
            or not (
                np.issubdtype(raw.dtype, np.integer)
                or np.issubdtype(raw.dtype, np.floating)
            )
        ):
            raise ConventionalRexAbsorbedMetricsError(
                f"incident array {name} must be a real numeric (1024,) vector."
            )
        array = np.asarray(raw, dtype=np.float64)
        if not np.all(np.isfinite(array)) or np.any(array < 0.0):
            raise ConventionalRexAbsorbedMetricsError(
                f"incident array {name} must be finite and non-negative."
            )
        array.setflags(write=False)
        normalized.append((name, array))
    return tuple(normalized)


def _validate_native_run_artifacts(
    summary: Mapping[str, Any],
    plan: ConventionalRexExecutionPlan,
    arrays: Sequence[tuple[str, FloatArray]],
) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    raw_records = summary.get("run_artifacts")
    if not isinstance(raw_records, list) or len(raw_records) != 6:
        raise ConventionalRexAbsorbedMetricsError("native run artifact hashes are incomplete.")
    array_map = dict(arrays)
    accepted: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for raw, run in zip(raw_records, plan.runs, strict=True):
        if not isinstance(raw, Mapping) or raw.get("interval_id") != run.interval_id:
            raise ConventionalRexAbsorbedMetricsError("native run artifact order mismatch.")
        paths = (
            ("source_rad_sha256", run.paths.source_rad),
            ("plant_rad_sha256", run.paths.plant_rad),
            ("octree_sha256", run.paths.octree),
            ("ambient_cache_sha256", run.paths.ambient_cache),
            ("raw_rgb_sha256", run.paths.raw_rgb),
            ("decoded_npy_sha256", run.paths.decoded_npy),
        )
        hashes: list[tuple[str, str]] = []
        for name, path in paths:
            digest = _sha256_file(path)
            if raw.get(name) != digest:
                raise ConventionalRexAbsorbedMetricsError(
                    f"{run.interval_id} native artifact hash mismatch: {name}."
                )
            hashes.append((name, digest))
        try:
            decoded = np.load(run.paths.decoded_npy, allow_pickle=False)
        except (OSError, ValueError) as exc:
            raise ConventionalRexAbsorbedMetricsError(
                f"{run.interval_id} decoded NPY is malformed."
            ) from exc
        expected = array_map[f"{run.interval_id}_umol_m2_s"]
        if decoded.dtype != np.float64 or decoded.shape != (1024,) or not np.array_equal(decoded, expected):
            raise ConventionalRexAbsorbedMetricsError(
                f"{run.interval_id} decoded NPY differs from the incident archive."
            )
        accepted.append((run.interval_id, tuple(hashes)))
    return tuple(accepted)


def compute_conventional_rex_absorbed_metrics(
    loaded: LoadedConventionalRexIncidentWorkspace,
) -> ComputedConventionalRexAbsorbedMetrics:
    """Apply persisted Conventional-weighted A/T/R to immutable incident arrays."""

    plan = loaded.plan
    if loaded.artifact_root != plan.paths.artifact_root:
        raise ConventionalRexAbsorbedMetricsError("loaded artifact root does not match its plan.")
    source_input = SourceNeutralAbsorbedMetricsInput(
        source_family="conventional_led",
        source_payload_id=plan.bundle.source_payload.source_payload_id,
        optics_payload_id=plan.bundle.optical_payload.optical_payload_id,
        material_plan_id=plan.bundle.material_plan.material_plan_id,
        plant_id=plan.receiver_samples[0].plant_id,
        receiver_identity=plan.bundle.receiver_identity,
        patch_count=512,
        receiver_count=1024,
        ordered_receiver_ids=plan.bundle.ordered_receiver_ids,
        band_coefficients=tuple(
            (
                band_id,
                plan.bundle.material_plan.material(band_id).source_interval.coefficients,
            )
            for band_id in CONVENTIONAL_BAND_ORDER
        ),
    )
    if "smd" in source_input.source_payload_id.lower() or "smd" in source_input.optics_payload_id.lower():
        raise ConventionalRexAbsorbedMetricsError("SMD authority entered Conventional absorption.")
    incident_by_band = {
        band_id: loaded.array(band_id) for band_id in CONVENTIONAL_BAND_ORDER
    }
    spectral = compute_source_neutral_absorbed_metrics(
        source_input,
        plan.receiver_samples,
        incident_by_band,
    )
    patch_pairs = spectral.patch_pairs
    if len(patch_pairs) != 512 or len(spectral.leaves) != 32:
        raise ConventionalRexAbsorbedMetricsError("absorbed patch/leaf count closure failed.")

    base = spectral.patch_arrays.as_dict()
    area = np.asarray(base["patch_area_m2"], dtype=np.float64)
    front_indices = np.asarray(base["front_receiver_index"], dtype=np.int64)
    back_indices = np.asarray(base["back_receiver_index"], dtype=np.int64)
    absorptance = np.asarray(base["absorptance"], dtype=np.float64)
    transmittance = np.asarray(base["transmittance"], dtype=np.float64)
    reflectance = np.asarray(base["reflectance"], dtype=np.float64)
    front_incident = np.asarray(base["front_incident_pfd"], dtype=np.float64)
    back_incident = np.asarray(base["back_incident_pfd"], dtype=np.float64)
    combined_incident = front_incident + back_incident
    front_absorbed = front_incident * absorptance
    back_absorbed = back_incident * absorptance
    combined_absorbed = front_absorbed + back_absorbed
    front_transmitted = front_incident * transmittance
    back_transmitted = back_incident * transmittance
    combined_transmitted = front_transmitted + back_transmitted
    front_reflected = front_incident * reflectance
    back_reflected = back_incident * reflectance
    combined_reflected = front_reflected + back_reflected
    closure_pfd = (
        combined_absorbed
        + combined_transmitted
        + combined_reflected
        - combined_incident
    )
    if not np.allclose(
        combined_absorbed + combined_transmitted + combined_reflected,
        combined_incident,
        rtol=1e-12,
        atol=1e-12,
    ):
        raise ConventionalRexAbsorbedMetricsError("local Conventional A/T/R PFD closure failed.")

    par_slice = slice(0, 4)
    four_front_incident = front_incident[:, par_slice].sum(axis=1)
    four_back_incident = back_incident[:, par_slice].sum(axis=1)
    four_combined_incident = four_front_incident + four_back_incident
    four_front_absorbed = front_absorbed[:, par_slice].sum(axis=1)
    four_back_absorbed = back_absorbed[:, par_slice].sum(axis=1)
    four_combined_absorbed = four_front_absorbed + four_back_absorbed

    scalar_array = loaded.array("scalar_par")
    scalar_front_incident = scalar_array[front_indices]
    scalar_back_incident = scalar_array[back_indices]
    scalar_combined_incident = scalar_front_incident + scalar_back_incident
    scalar_coefficients = plan.bundle.material_plan.material(
        "scalar_par"
    ).source_interval.coefficients
    scalar_absorptance = scalar_coefficients.absorptance
    scalar_front_absorbed = scalar_front_incident * scalar_absorptance
    scalar_back_absorbed = scalar_back_incident * scalar_absorptance
    scalar_combined_absorbed = scalar_front_absorbed + scalar_back_absorbed
    scalar_difference = scalar_combined_absorbed - four_combined_absorbed

    scalar_front_flux = float(np.dot(scalar_front_absorbed, area))
    scalar_back_flux = float(np.dot(scalar_back_absorbed, area))
    scalar_flux = scalar_front_flux + scalar_back_flux
    four_front_flux = float(np.dot(four_front_absorbed, area))
    four_back_flux = float(np.dot(four_back_absorbed, area))
    four_flux = four_front_flux + four_back_flux
    physical_area = float(area.sum())
    scalar_mean = scalar_flux / physical_area
    four_mean = four_flux / physical_area
    receiver_scalar = np.concatenate((scalar_front_absorbed, scalar_back_absorbed))
    receiver_four = np.concatenate((four_front_absorbed, four_back_absorbed))
    receiver_rmse = _rmse(receiver_scalar, receiver_four)
    receiver_reference_rms = _rms(receiver_scalar)
    patch_rmse = _rmse(scalar_combined_absorbed, four_combined_absorbed)
    patch_reference_rms = _rms(scalar_combined_absorbed)
    diagnostic = ScalarAbsorbedValidationDiagnostic(
        scalar_absorptance=scalar_absorptance,
        scalar_whole_plant_absorbed_flux=scalar_flux,
        four_band_whole_plant_absorbed_flux=four_flux,
        whole_plant_signed_difference=scalar_flux - four_flux,
        whole_plant_absolute_difference=abs(scalar_flux - four_flux),
        whole_plant_relative_difference=_relative_absolute_difference(
            scalar_flux, four_flux
        ),
        scalar_area_weighted_absorbed_pfd=scalar_mean,
        four_band_area_weighted_absorbed_pfd=four_mean,
        area_weighted_signed_difference=scalar_mean - four_mean,
        area_weighted_absolute_difference=abs(scalar_mean - four_mean),
        area_weighted_relative_difference=_relative_absolute_difference(
            scalar_mean, four_mean
        ),
        front_absorbed_flux_signed_difference=scalar_front_flux - four_front_flux,
        back_absorbed_flux_signed_difference=scalar_back_flux - four_back_flux,
        receiver_level_rmse=receiver_rmse,
        receiver_level_relative_rmse=(
            None if receiver_reference_rms == 0.0 else receiver_rmse / receiver_reference_rms
        ),
        patch_level_rmse=patch_rmse,
        patch_level_relative_rmse=(
            None if patch_reference_rms == 0.0 else patch_rmse / patch_reference_rms
        ),
    )

    patch_arrays = ConventionalAbsorbedPatchArrays(
        arrays=(
            ("patch_index", np.arange(512, dtype=np.int64)),
            ("leaf_index", base["leaf_index"]),
            ("front_receiver_index", front_indices),
            ("back_receiver_index", back_indices),
            ("patch_area_m2", area),
            ("absorptance", absorptance),
            ("transmittance", transmittance),
            ("reflectance", reflectance),
            ("front_incident_pfd", front_incident),
            ("back_incident_pfd", back_incident),
            ("combined_incident_pfd", combined_incident),
            ("front_absorbed_pfd", front_absorbed),
            ("back_absorbed_pfd", back_absorbed),
            ("combined_absorbed_pfd", combined_absorbed),
            ("front_transmitted_pfd", front_transmitted),
            ("back_transmitted_pfd", back_transmitted),
            ("combined_transmitted_pfd", combined_transmitted),
            ("front_reflected_pfd", front_reflected),
            ("back_reflected_pfd", back_reflected),
            ("combined_reflected_pfd", combined_reflected),
            ("incident_flux", base["incident_flux"]),
            ("absorbed_flux", base["combined_absorbed_flux"]),
            ("transmitted_flux", base["transmitted_flux"]),
            ("reflected_flux", base["reflected_flux"]),
            ("local_closure_error_flux", base["energy_closure_error_flux"]),
            ("four_band_front_incident_pfd", four_front_incident),
            ("four_band_back_incident_pfd", four_back_incident),
            ("four_band_combined_incident_pfd", four_combined_incident),
            ("four_band_front_absorbed_pfd", four_front_absorbed),
            ("four_band_back_absorbed_pfd", four_back_absorbed),
            ("four_band_combined_absorbed_pfd", four_combined_absorbed),
            ("far_red_combined_incident_pfd", combined_incident[:, 4]),
            ("far_red_combined_absorbed_pfd", combined_absorbed[:, 4]),
            ("scalar_front_incident_pfd", scalar_front_incident),
            ("scalar_back_incident_pfd", scalar_back_incident),
            ("scalar_combined_incident_pfd", scalar_combined_incident),
            ("scalar_front_absorbed_pfd", scalar_front_absorbed),
            ("scalar_back_absorbed_pfd", scalar_back_absorbed),
            ("scalar_combined_absorbed_pfd", scalar_combined_absorbed),
            ("scalar_minus_four_band_absorbed_pfd", scalar_difference),
        )
    )
    return ComputedConventionalRexAbsorbedMetrics(
        spectral=spectral,
        scalar_diagnostic=diagnostic,
        patch_arrays=patch_arrays,
        maximum_local_closure_error_flux=float(
            np.max(np.abs(base["energy_closure_error_flux"]))
        ),
        maximum_local_closure_error_pfd=float(np.max(np.abs(closure_pfd))),
    )


def format_conventional_absorbed_patch_npz(
    arrays: ConventionalAbsorbedPatchArrays,
) -> bytes:
    """Serialize deterministic fixed-order numeric NPY members."""

    output = BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for name, array in arrays.arrays:
            member = BytesIO()
            np.save(member, array, allow_pickle=False)
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            archive.writestr(info, member.getvalue())
    return output.getvalue()


def calculate_conventional_rex_absorbed_metrics(
    workspace: str | Path,
) -> ConventionalRexAbsorbedMetricsResult:
    """Validate native inputs, compute optical partitions, and write absorbed outputs."""

    loaded = load_conventional_rex_incident_workspace(workspace)
    computed = compute_conventional_rex_absorbed_metrics(loaded)
    absorbed_root = loaded.artifact_root / "absorbed"
    summary_path = absorbed_root / "absorbed_photon_summary.json"
    patch_npz_path = absorbed_root / "absorbed_patch_metrics.npz"
    npz_bytes = format_conventional_absorbed_patch_npz(computed.patch_arrays)
    npz_sha256 = _sha256_bytes(npz_bytes)
    scientific = _absorbed_scientific_payload(
        loaded,
        computed,
        patch_npz_sha256=npz_sha256,
    )
    summary_id = "conventional-rex-absorbed-summary-v1-" + _hash_payload(scientific)
    summary = scientific | {
        "schema_version": CONVENTIONAL_ABSORBED_SCHEMA_VERSION,
        "payload_type": CONVENTIONAL_ABSORBED_PAYLOAD_TYPE,
        "success": True,
        "summary_id": summary_id,
        "source_artifacts": {
            "incident_summary": _relative(
                loaded.plan.paths.incident_transport_summary,
                loaded.artifact_root,
            ),
            "incident_receiver_values": _relative(
                loaded.plan.paths.incident_receiver_values_npz,
                loaded.artifact_root,
            ),
            "receiver_metadata": _relative(
                loaded.plan.paths.receiver_metadata,
                loaded.artifact_root,
            ),
        },
        "output_artifacts": {
            "patch_metrics_npz": _relative(patch_npz_path, loaded.artifact_root),
            "patch_metrics_npz_sha256": npz_sha256,
            "npz_array_order": list(CONVENTIONAL_ABSORBED_NPZ_ORDER),
        },
        "patch_order": [item.to_dict() for item in computed.spectral.patch_pairs],
        "leaf_summaries": [item.to_dict() for item in computed.spectral.leaves],
        "whole_plant_summary": computed.spectral.plant.to_dict(),
    }
    summary_text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    _write_or_validate_absorbed_artifacts(
        absorbed_root,
        summary_path=summary_path,
        summary_text=summary_text,
        patch_npz_path=patch_npz_path,
        patch_npz_bytes=npz_bytes,
    )
    actual_arrays = read_conventional_absorbed_patch_npz(patch_npz_path)
    _assert_array_mapping_equal(computed.patch_arrays.as_dict(), actual_arrays)
    if _sha256_file(patch_npz_path) != npz_sha256:
        raise ConventionalRexAbsorbedMetricsError("absorbed NPZ hash changed after writing.")
    if _load_json(summary_path, "absorbed photon summary") != summary:
        raise ConventionalRexAbsorbedMetricsError("absorbed summary round-trip changed.")
    return ConventionalRexAbsorbedMetricsResult(
        loaded=loaded,
        computed=computed,
        summary=summary,
        summary_path=summary_path,
        patch_npz_path=patch_npz_path,
        summary_id=summary_id,
        patch_npz_sha256=npz_sha256,
    )


def _absorbed_scientific_payload(
    loaded: LoadedConventionalRexIncidentWorkspace,
    computed: ComputedConventionalRexAbsorbedMetrics,
    *,
    patch_npz_sha256: str,
) -> dict[str, object]:
    plan = loaded.plan
    coefficients = {
        interval_id: plan.bundle.material_plan.material(
            interval_id
        ).source_interval.coefficients.to_dict()
        for interval_id in CONVENTIONAL_REX_RUN_ORDER
    }
    return {
        "scientific_claim": CONVENTIONAL_ABSORBED_CLAIM,
        "identities": {
            "conventional_source_payload_id": plan.bundle.source_payload.source_payload_id,
            "conventional_rex_optics_id": plan.bundle.optical_payload.optical_payload_id,
            "material_plan_id": plan.bundle.material_plan.material_plan_id,
            "plant_identity": plan.bundle.plant_identity,
            "receiver_identity": plan.bundle.receiver_identity,
            "bundle_id": plan.bundle.bundle_id,
            "native_execution_plan_id": plan.execution_plan_id,
        },
        "input_hashes": {
            "incident_summary_sha256": loaded.incident_summary_sha256,
            "incident_receiver_values_sha256": loaded.incident_npz_sha256,
            "source_conversion_summary_sha256": _sha256_file(
                plan.paths.source_conversion_summary
            ),
            "command_provenance_summary_sha256": _sha256_file(
                plan.paths.command_provenance_summary
            ),
            "bundle_manifest_sha256": _sha256_file(plan.paths.bundle_manifest),
            "execution_plan_summary_sha256": _sha256_file(
                plan.paths.transport_plan_summary
            ),
            "source_payload_sha256": _sha256_file(plan.paths.source_payload_json),
            "optical_payload_sha256": _sha256_file(plan.paths.optical_payload_json),
            "material_plan_sha256": _sha256_file(plan.paths.material_plan_json),
            "receiver_metadata_sha256": _sha256_file(plan.paths.receiver_metadata),
        },
        "absorbed_patch_metrics_sha256": patch_npz_sha256,
        "band_order": list(CONVENTIONAL_BAND_ORDER),
        "par_band_order": list(CONVENTIONAL_BAND_ORDER[:4]),
        "scalar_par_policy": "validation_diagnostic_only_not_added_to_four_band_PAR",
        "far_red_policy": "separate_from_PAR",
        "coefficients": coefficients,
        "counts": {
            "receivers": 1024,
            "physical_patches": 512,
            "leaves": 32,
        },
        "total_physical_leaf_area_m2": computed.spectral.plant.physical_area_m2,
        "definitions": {
            "combined_incident_pfd": "front_incident_pfd + back_incident_pfd",
            "absorbed_pfd": "absorptance * combined_incident_pfd",
            "transmitted_pfd": "transmittance * combined_incident_pfd",
            "reflected_pfd": "reflectance * combined_incident_pfd",
            "partition_flux": "partition_pfd * one_physical_patch_area",
            "local_partition_interpretation": (
                "transmitted and reflected terms are local interactions, not net escaped light"
            ),
        },
        "primary_four_band_PAR": computed.spectral.plant.par.to_dict(),
        "far_red": computed.spectral.plant.far_red.to_dict(),
        "scalar_absorbed_validation": computed.scalar_diagnostic.to_payload(),
        "local_closure": {
            "maximum_absolute_error_flux_umol_s": (
                computed.maximum_local_closure_error_flux
            ),
            "maximum_absolute_error_pfd_umol_m2_s": (
                computed.maximum_local_closure_error_pfd
            ),
            "relative_tolerance": 1e-12,
            "absolute_tolerance": 1e-12,
        },
        "reconciliation_scale": None,
        "scientific_pass_threshold": None,
        "native_incident_artifacts_modified": False,
        "limitations": list(CONVENTIONAL_ABSORBED_LIMITATIONS),
    }


def read_conventional_absorbed_patch_npz(
    path: str | Path,
) -> dict[str, NDArray[Any]]:
    source = Path(path)
    try:
        with np.load(source, allow_pickle=False) as archive:
            if tuple(archive.files) != CONVENTIONAL_ABSORBED_NPZ_ORDER:
                raise ConventionalRexAbsorbedMetricsError(
                    "absorbed NPZ members are incomplete or out of order."
                )
            arrays = {name: np.array(archive[name], copy=True) for name in archive.files}
    except FileNotFoundError as exc:
        raise ConventionalRexAbsorbedMetricsError(f"absorbed NPZ is missing: {source}") from exc
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise ConventionalRexAbsorbedMetricsError(f"absorbed NPZ is malformed: {source}") from exc
    for name, array in arrays.items():
        if array.dtype.hasobject or not (
            np.issubdtype(array.dtype, np.integer)
            or np.issubdtype(array.dtype, np.floating)
        ):
            raise ConventionalRexAbsorbedMetricsError(f"absorbed array {name} must be numeric.")
        if not np.all(np.isfinite(array)):
            raise ConventionalRexAbsorbedMetricsError(f"absorbed array {name} must be finite.")
    vector_names = CONVENTIONAL_ABSORBED_NPZ_ORDER[:5]
    coefficient_names = CONVENTIONAL_ABSORBED_NPZ_ORDER[5:8]
    matrix_names = CONVENTIONAL_ABSORBED_NPZ_ORDER[8:25]
    remaining_vectors = CONVENTIONAL_ABSORBED_NPZ_ORDER[25:]
    if any(arrays[name].shape != (512,) for name in vector_names):
        raise ConventionalRexAbsorbedMetricsError("absorbed patch vectors have invalid shape.")
    if any(arrays[name].shape != (5,) for name in coefficient_names):
        raise ConventionalRexAbsorbedMetricsError("absorbed coefficient vectors have invalid shape.")
    if any(arrays[name].shape != (512, 5) for name in matrix_names):
        raise ConventionalRexAbsorbedMetricsError("absorbed band matrices have invalid shape.")
    if any(arrays[name].shape != (512,) for name in remaining_vectors):
        raise ConventionalRexAbsorbedMetricsError("absorbed aggregate vectors have invalid shape.")
    return arrays


def _write_or_validate_absorbed_artifacts(
    absorbed_root: Path,
    *,
    summary_path: Path,
    summary_text: str,
    patch_npz_path: Path,
    patch_npz_bytes: bytes,
) -> None:
    expected_names = {summary_path.name, patch_npz_path.name}
    if absorbed_root.exists():
        if not absorbed_root.is_dir():
            raise ConventionalRexAbsorbedMetricsError("absorbed output root is not a directory.")
        existing_names = {item.name for item in absorbed_root.iterdir()}
        if existing_names and existing_names != expected_names:
            raise ConventionalRexAbsorbedMetricsError(
                "absorbed output directory contains stale or incomplete artifacts."
            )
        if existing_names == expected_names:
            if (
                summary_path.read_text(encoding="utf-8") != summary_text
                or patch_npz_path.read_bytes() != patch_npz_bytes
            ):
                raise ConventionalRexAbsorbedMetricsError(
                    "existing absorbed artifacts differ from deterministic results."
                )
            return
    absorbed_root.mkdir(parents=True, exist_ok=True)
    wrote_npz = False
    try:
        atomic_write_bytes(patch_npz_path, patch_npz_bytes)
        wrote_npz = True
        atomic_write_text(summary_path, summary_text)
    except BaseException:
        if wrote_npz and not summary_path.exists():
            patch_npz_path.unlink(missing_ok=True)
        raise


def _validate_exact_json(path: Path, expected: Mapping[str, Any], label: str) -> None:
    if _load_json(path, label) != expected:
        raise ConventionalRexAbsorbedMetricsError(f"{label} differs from its typed plan.")


def _validate_exact_text(path: Path, expected: str, label: str) -> None:
    try:
        actual = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConventionalRexAbsorbedMetricsError(f"{label} is missing: {path}") from exc
    except (OSError, UnicodeError) as exc:
        raise ConventionalRexAbsorbedMetricsError(f"{label} is unreadable: {path}") from exc
    if actual != expected:
        raise ConventionalRexAbsorbedMetricsError(f"{label} differs from its typed plan.")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConventionalRexAbsorbedMetricsError(f"{label} is missing: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConventionalRexAbsorbedMetricsError(f"{label} is malformed: {path}") from exc
    if not isinstance(payload, dict):
        raise ConventionalRexAbsorbedMetricsError(f"{label} root must be an object.")
    return payload


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ConventionalRexAbsorbedMetricsError(f"{key} must be an object.")
    return value


def _string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ConventionalRexAbsorbedMetricsError(f"{key} must be a non-empty string.")
    return value


def _integer(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConventionalRexAbsorbedMetricsError(f"{key} must be an integer.")
    return value


def _number(payload: Mapping[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConventionalRexAbsorbedMetricsError(f"{key} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ConventionalRexAbsorbedMetricsError(f"{key} must be finite.")
    return number


def _rmse(left: FloatArray, right: FloatArray) -> float:
    if left.shape != right.shape:
        raise ValueError("RMSE arrays must have matching shapes.")
    return float(np.sqrt(np.mean(np.square(left - right))))


def _rms(value: FloatArray) -> float:
    return float(np.sqrt(np.mean(np.square(value))))


def _relative_absolute_difference(reference: float, candidate: float) -> float | None:
    return None if reference == 0.0 else abs(reference - candidate) / reference


def _assert_array_mapping_equal(
    expected: Mapping[str, NDArray[Any]],
    actual: Mapping[str, NDArray[Any]],
) -> None:
    if tuple(expected) != tuple(actual):
        raise ConventionalRexAbsorbedMetricsError("absorbed NPZ array order changed.")
    for name in expected:
        if expected[name].dtype != actual[name].dtype or not np.array_equal(
            expected[name], actual[name]
        ):
            raise ConventionalRexAbsorbedMetricsError(
                f"absorbed NPZ round-trip changed {name}."
            )


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ConventionalRexAbsorbedMetricsError(
            "artifact path escaped the Conventional native root."
        ) from exc


def _optional_relative(path: Path | None, root: Path) -> str | None:
    return None if path is None else _relative(path, root)


def _relative_token(token: str, root: Path) -> str:
    candidate = Path(token)
    if candidate.is_absolute():
        try:
            return candidate.relative_to(root).as_posix()
        except ValueError:
            return str(candidate)
    return token


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except FileNotFoundError as exc:
        raise ConventionalRexAbsorbedMetricsError(f"required artifact is missing: {path}") from exc
