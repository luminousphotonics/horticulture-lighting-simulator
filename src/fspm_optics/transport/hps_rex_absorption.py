"""Pure Phase 25D HPS Rex incident-to-absorbed photon post-processing."""

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

from fspm_optics.fixtures.hps import (
    HPS_SPECTRAL_SOURCE_ID,
    format_hps_spectral_source_payload_json,
    validate_converted_hps_ies_output,
)
from fspm_optics.geometry.room import (
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    production_room_model_payload,
)
from fspm_optics.optics.hps import (
    format_hps_rex_material_plan_json,
    format_hps_rex_weighted_atr_json,
)
from fspm_optics.transport.basis.atomic import atomic_write_bytes, atomic_write_text
from fspm_optics.transport.five_band_absorption import (
    ComputedAbsorbedMetrics,
    SourceNeutralAbsorbedMetricsInput,
    compute_source_neutral_absorbed_metrics,
    pair_two_sided_receivers,
)
from fspm_optics.transport.hps import (
    HPS_REX_RUN_ORDER,
    format_hps_isolated_transport_bundle_json,
)
from fspm_optics.transport.hps_rex_execution import (
    HPS_REX_EXECUTION_PAYLOAD_TYPE,
    HPS_REX_EXECUTION_SCHEMA_VERSION,
    HPS_REX_EXECUTION_LIMITATIONS,
    HPS_REX_INCIDENT_ARRAY_ORDER,
    HPS_REX_NATIVE_COMMAND_COUNT,
    HpsRexExecutables,
    HpsRexExecutionPlan,
    HpsRexTransportRequest,
    compute_hps_incident_run_metrics,
    compute_hps_scalar_four_band_diagnostics,
    format_hps_rex_execution_plan_json,
    parse_hps_rex_receiver_rgb,
    plan_hps_rex_execution,
)
from fspm_optics.transport.hps_scalar import validate_hps_converted_dat

FloatArray = NDArray[np.float64]

HPS_ABSORBED_SCHEMA_VERSION: Final = 1
HPS_ABSORBED_PAYLOAD_TYPE: Final = "fspm_optics_hps_rex_absorbed_metrics"
HPS_ABSORBED_CLAIM: Final = (
    "Optical incident-photon partitions at the validated HPS Rex leaf surface; "
    "no photosynthesis, growth, yield, or electrical-efficiency claim."
)
HPS_ABSORBED_LIMITATIONS: Final[tuple[str, ...]] = (
    "Transmitted and reflected terms are local interaction partitions, not net escaped light.",
    "Four-band PAR uses blue, green, orange, and red only.",
    "Far-red remains separate from PAR.",
    "Scalar absorbed PAR is a validation diagnostic and is not added to four-band PAR.",
    "Positive 400-403 nm HPS source mass is unsupported by the Rex grid and is not redistributed.",
    "No reconciliation scale or scientific pass threshold is applied.",
)
HPS_ABSORBED_NPZ_ORDER: Final[tuple[str, ...]] = (
    "patch_index", "leaf_index", "front_receiver_index", "back_receiver_index",
    "patch_area_m2", "absorptance", "transmittance", "reflectance",
    "front_incident_pfd", "back_incident_pfd", "combined_incident_pfd",
    "front_absorbed_pfd", "back_absorbed_pfd", "combined_absorbed_pfd",
    "front_transmitted_pfd", "back_transmitted_pfd", "combined_transmitted_pfd",
    "front_reflected_pfd", "back_reflected_pfd", "combined_reflected_pfd",
    "incident_flux", "absorbed_flux", "transmitted_flux", "reflected_flux",
    "local_closure_error_flux", "four_band_front_incident_pfd",
    "four_band_back_incident_pfd", "four_band_combined_incident_pfd",
    "four_band_front_absorbed_pfd", "four_band_back_absorbed_pfd",
    "four_band_combined_absorbed_pfd", "far_red_combined_incident_pfd",
    "far_red_combined_absorbed_pfd", "scalar_front_incident_pfd",
    "scalar_back_incident_pfd", "scalar_combined_incident_pfd",
    "scalar_front_absorbed_pfd", "scalar_back_absorbed_pfd",
    "scalar_combined_absorbed_pfd", "scalar_minus_four_band_absorbed_pfd",
)


class HpsRexAbsorbedMetricsError(RuntimeError):
    """A HPS native authority or absorbed artifact failed closed validation."""


@dataclass(frozen=True, slots=True)
class LoadedHpsRexIncidentWorkspace:
    workspace_root: Path
    artifact_root: Path
    plan: HpsRexExecutionPlan
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
        raise KeyError(f"unknown HPS incident interval: {interval_id!r}")


@dataclass(frozen=True, slots=True)
class HpsAbsorbedPatchArrays:
    arrays: tuple[tuple[str, NDArray[Any]], ...]

    def __post_init__(self) -> None:
        if tuple(name for name, _ in self.arrays) != HPS_ABSORBED_NPZ_ORDER:
            raise ValueError("HPS absorbed NPZ arrays are incomplete or out of order.")
        frozen: list[tuple[str, NDArray[Any]]] = []
        for name, value in self.arrays:
            array = np.array(value, copy=True)
            if array.dtype.hasobject or not np.all(np.isfinite(array)):
                raise ValueError(f"absorbed array {name} must be finite numeric data.")
            array.setflags(write=False)
            frozen.append((name, array))
        object.__setattr__(self, "arrays", tuple(frozen))

    def as_dict(self) -> dict[str, NDArray[Any]]:
        return dict(self.arrays)


@dataclass(frozen=True, slots=True)
class HpsScalarAbsorbedValidationDiagnostic:
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
            raise ValueError("HPS scalar absorptance must be a fraction.")
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
                raise ValueError(f"{name} must be finite and nonnegative.")
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
                raise ValueError(f"{name} must be nonnegative when present.")
        if self.reconciliation_scale_applied or self.scientific_pass_threshold_applied:
            raise ValueError("HPS absorption may not scale or invent a pass threshold.")

    def to_payload(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class ComputedHpsRexAbsorbedMetrics:
    spectral: ComputedAbsorbedMetrics
    scalar_diagnostic: HpsScalarAbsorbedValidationDiagnostic
    patch_arrays: HpsAbsorbedPatchArrays
    maximum_local_closure_error_flux: float
    maximum_local_closure_error_pfd: float


@dataclass(frozen=True, slots=True)
class HpsRexAbsorbedMetricsResult:
    loaded: LoadedHpsRexIncidentWorkspace
    computed: ComputedHpsRexAbsorbedMetrics
    summary: Mapping[str, Any]
    summary_path: Path
    patch_npz_path: Path
    summary_id: str
    patch_npz_sha256: str


def load_hps_rex_incident_workspace(
    workspace: str | Path,
) -> LoadedHpsRexIncidentWorkspace:
    """Reconstruct and strictly validate one successful Phase 25D workspace."""

    root = Path(workspace).expanduser().resolve()
    artifact_root = root / "hps_rex_transport"
    identity = _load_json(artifact_root / "input_identity.json", "input identity")
    if identity.get("schema_version") != 2 or identity.get("runtime_workspace") != str(root):
        raise HpsRexAbsorbedMetricsError("HPS input workspace identity is invalid.")
    request_payload = _mapping(identity, "request")
    dimensions = request_payload.get("room_dimensions_m")
    if (
        not isinstance(dimensions, list)
        or len(dimensions) != 2
        or any(isinstance(value, bool) or not isinstance(value, int | float) for value in dimensions)
        or request_payload.get("layout_policy") != "coverage_4ft_center_pitch_v1"
        or request_payload.get("plant_policy")
        != "fixed_mature_rex_seed_1_default_4x4_patches"
    ):
        raise HpsRexAbsorbedMetricsError("HPS input request is invalid.")
    try:
        request = HpsRexTransportRequest.from_meters(
            workspace=root,
            room_length_m=float(dimensions[0]),
            room_width_m=float(dimensions[1]),
            threads=_integer(request_payload, "threads"),
            quality_profile=_string(request_payload, "quality_profile"),
            reference_plane_z_m=_number(request_payload, "reference_plane_z_m"),
            mount_height_m=_number(request_payload, "mount_height_m"),
        )
        plan = plan_hps_rex_execution(
            request,
            executables=HpsRexExecutables(Path("ies2rad"), Path("oconv"), Path("rtrace")),
        )
    except (ValueError, RuntimeError) as exc:
        raise HpsRexAbsorbedMetricsError(
            f"could not reconstruct the typed Phase 25D plan: {exc}"
        ) from exc
    expected_identity = {
        "schema_version": 2,
        "execution_plan_id": plan.execution_plan_id,
        "bundle_id": plan.bundle.bundle_id,
        "fixture_occlusion_identity": plan.fixture_occlusion.identity_sha256,
        "request": plan.request.scientific_payload(),
        "runtime_workspace": str(root),
    }
    if identity != expected_identity:
        raise HpsRexAbsorbedMetricsError("HPS scientific input identity mismatch.")

    _validate_exact_text(
        plan.paths.execution_plan_summary,
        format_hps_rex_execution_plan_json(plan),
        "Phase 25D execution plan",
    )
    _validate_exact_text(
        plan.paths.bundle_manifest,
        format_hps_isolated_transport_bundle_json(plan.bundle),
        "Phase 25B bundle",
    )
    _validate_exact_text(
        plan.paths.source_payload_json,
        format_hps_spectral_source_payload_json(plan.bundle.source_payload),
        "HPS source payload",
    )
    _validate_exact_text(
        plan.paths.optical_payload_json,
        format_hps_rex_weighted_atr_json(plan.bundle.optical_payload),
        "HPS Rex optical payload",
    )
    _validate_exact_text(
        plan.paths.material_plan_json,
        format_hps_rex_material_plan_json(plan.bundle.material_plan),
        "HPS Rex material plan",
    )
    for path, expected, label in (
        (plan.paths.derived_ies, plan.source_plan.derived_ies.text, "derived IES"),
        (plan.paths.room_rad, plan.room_text, "room RAD"),
        (plan.paths.generic_plant_rad, plan.generic_plant_text, "generic plant RAD"),
        (plan.paths.receiver_rays, plan.receiver_text, "receiver rays"),
        (plan.paths.receiver_metadata, plan.receiver_metadata_text, "receiver metadata"),
    ):
        _validate_exact_text(path, expected, label)
    _validate_fixture_occlusion_artifacts(plan)
    for run in plan.runs:
        _validate_exact_text(run.paths.source_rad, run.source_text, f"{run.interval_id} source RAD")
        _validate_exact_text(run.paths.plant_rad, run.plant_text, f"{run.interval_id} plant RAD")
        _validate_scene_manifest(plan, run)

    if plan.paths.failure_summary.exists():
        raise HpsRexAbsorbedMetricsError(
            "Phase 25D failure summary exists; absorbed metrics require an unambiguously successful workspace."
        )
    _validate_workspace_shape(plan)
    if tuple(run.interval_id for run in plan.runs) != HPS_REX_RUN_ORDER:
        raise HpsRexAbsorbedMetricsError("HPS run order changed.")
    pairs = pair_two_sided_receivers(plan.receiver_samples, expected_patch_count=512)
    if (
        len(pairs) != 512
        or len(plan.receiver_samples) != 1024
        or tuple(item.receiver_id for item in plan.receiver_samples)
        != plan.bundle.ordered_receiver_ids
    ):
        raise HpsRexAbsorbedMetricsError("HPS receiver pairing identity changed.")

    source_id = plan.bundle.source_payload.spectral_source_id
    joined_ids = " ".join(
        (
            source_id,
            plan.bundle.source_payload.source_payload_id,
            plan.bundle.optical_payload.optical_payload_id,
            plan.bundle.material_plan.material_plan_id,
        )
    ).lower()
    if source_id != HPS_SPECTRAL_SOURCE_ID or "smd" in joined_ids or "conventional" in joined_ids:
        raise HpsRexAbsorbedMetricsError("non-HPS source identity entered HPS absorption.")

    conversion = _load_json(plan.paths.source_conversion_summary, "source conversion summary")
    _validate_source_conversion(conversion, plan)
    provenance = _load_json(plan.paths.command_provenance_summary, "command provenance summary")
    _validate_command_provenance(provenance, plan)
    incident = _load_json(plan.paths.incident_transport_summary, "incident transport summary")
    _validate_incident_summary(incident, plan)
    incident_path = plan.paths.incident_receiver_values_npz
    incident_hash = _sha256_file(incident_path)
    authority = _mapping(incident, "incident_arrays")
    if (
        set(authority) != {
            "path", "sha256", "keys", "far_red_included_in_par",
            "post_trace_179_conversion", "post_trace_spectral_scaling",
        }
        or authority.get("path") != _relative(incident_path, artifact_root)
        or authority.get("sha256") != incident_hash
        or tuple(authority.get("keys", ())) != HPS_REX_INCIDENT_ARRAY_ORDER
        or authority.get("far_red_included_in_par") is not False
        or authority.get("post_trace_179_conversion") is not False
        or authority.get("post_trace_spectral_scaling") is not False
    ):
        raise HpsRexAbsorbedMetricsError("HPS incident archive authority is invalid.")
    arrays = _load_incident_npz(incident_path)
    native_hashes = _validate_native_artifacts(incident, plan, arrays)
    _validate_incident_metrics(incident, plan, arrays)
    return LoadedHpsRexIncidentWorkspace(
        root,
        artifact_root,
        plan,
        incident,
        _sha256_file(plan.paths.incident_transport_summary),
        incident_hash,
        arrays,
        native_hashes,
    )


def _validate_scene_manifest(plan: HpsRexExecutionPlan, run: Any) -> None:
    expected = {
        "schema_version": 2,
        "interval_id": run.interval_id,
        "scene_id": run.native_scene_id,
        "source_definition_id": run.part25b.source_definition_id,
        "material_identifier": run.part25b.material.material_identifier,
        "octree_identity": run.part25b.fspm_scene.octree_identity,
        "ambient_cache_identity": run.part25b.ambient_cache_identity,
        "fixture_occlusion_identity": plan.fixture_occlusion.identity_sha256,
        "room_model": production_room_model_payload(),
        "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
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
    }
    if _load_json(run.paths.scene_manifest, f"{run.interval_id} scene manifest") != expected:
        raise HpsRexAbsorbedMetricsError(f"{run.interval_id} scene identity mismatch.")


def _validate_fixture_occlusion_artifacts(plan: HpsRexExecutionPlan) -> None:
    occlusion = plan.fixture_occlusion
    payload = occlusion.scientific_payload() | {
        "runtime_paths": {
            "instance_source": str(occlusion.instance_source_path),
            "shape_sources": [str(shape.source_path) for shape in occlusion.shapes],
            "shape_octrees": [str(shape.octree_path) for shape in occlusion.shapes],
        },
        "instance_source_sha256": hashlib.sha256(
            occlusion.instance_source_text.encode("utf-8")
        ).hexdigest(),
    }
    _validate_exact_text(
        occlusion.instance_source_path,
        occlusion.instance_source_text,
        "fixture body instances",
    )
    _validate_exact_text(
        occlusion.metadata_path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        "fixture occlusion metadata",
    )
    if _directory_names(occlusion.output_directory) != {
        occlusion.instance_source_path.name,
        occlusion.metadata_path.name,
        "shapes",
    }:
        raise HpsRexAbsorbedMetricsError(
            "fixture occlusion artifact inventory changed."
        )
    shape_directory = occlusion.output_directory / "shapes"
    expected_shape_files: set[str] = set()
    for shape in occlusion.shapes:
        marker = shape.octree_path.with_name(shape.octree_path.name + ".identity.json")
        expected_shape_files.update(
            {shape.source_path.name, shape.octree_path.name, marker.name}
        )
        _validate_exact_text(
            shape.source_path,
            shape.source_text,
            f"{shape.shape_id} source",
        )
        octree_sha256 = _sha256_nonempty_file(shape.octree_path)
        if _load_json(marker, f"{shape.shape_id} compiled identity") != {
            "schema_id": "fspm-optics.fixture-body-compiled-octree",
            "schema_version": 1,
            "shape_id": shape.shape_id,
            "derived_mesh_source_sha256": shape.source_sha256,
            "compiled_octree_sha256": octree_sha256,
        }:
            raise HpsRexAbsorbedMetricsError(
                f"{shape.shape_id} compiled fixture body identity changed."
            )
    if _directory_names(shape_directory) != expected_shape_files:
        raise HpsRexAbsorbedMetricsError(
            "fixture body shape artifact inventory changed."
        )


def _validate_workspace_shape(plan: HpsRexExecutionPlan) -> None:
    root = plan.paths.artifact_root
    expected_root = {
        plan.paths.source_directory.name,
        plan.paths.shared_directory.name,
        plan.paths.receiver_directory.name,
        plan.paths.log_directory.name,
        plan.paths.input_identity.name,
        plan.paths.bundle_manifest.name,
        plan.paths.execution_plan_summary.name,
        plan.paths.source_payload_json.name,
        plan.paths.optical_payload_json.name,
        plan.paths.material_plan_json.name,
        plan.paths.source_conversion_summary.name,
        plan.paths.command_provenance_summary.name,
        plan.paths.incident_transport_summary.name,
        plan.paths.incident_receiver_values_npz.name,
        *(run.paths.directory.name for run in plan.runs),
    }
    actual_root = _directory_names(root)
    if "absorbed" in actual_root:
        actual_root.remove("absorbed")
    if actual_root != expected_root:
        raise HpsRexAbsorbedMetricsError("Phase 25D workspace contains partial or unknown artifacts.")
    expected_sets = (
        (
            plan.paths.source_directory,
            {plan.paths.derived_ies.name, plan.paths.raw_converted_rad.name, plan.paths.converted_dat.name},
        ),
        (
            plan.paths.shared_directory,
            {
                plan.paths.room_rad.name,
                plan.paths.generic_plant_rad.name,
                "fixture_occlusion",
            },
        ),
        (
            plan.paths.receiver_directory,
            {plan.paths.receiver_rays.name, plan.paths.receiver_metadata.name},
        ),
        (
            plan.paths.log_directory,
            {
                plan.paths.ies2rad_stderr.name,
                *(run.paths.oconv_stderr.name for run in plan.runs),
                *(run.paths.rtrace_stderr.name for run in plan.runs),
            },
        ),
    )
    for directory, expected in expected_sets:
        if _directory_names(directory) != expected:
            raise HpsRexAbsorbedMetricsError(f"Phase 25D directory closure changed: {directory.name}.")
    for run in plan.runs:
        expected = {
            run.paths.source_rad.name,
            run.paths.plant_rad.name,
            run.paths.scene_manifest.name,
            run.paths.octree.name,
            run.paths.ambient_cache.name,
            run.paths.raw_rgb.name,
            run.paths.decoded_npy.name,
        }
        if _directory_names(run.paths.directory) != expected:
            raise HpsRexAbsorbedMetricsError(
                f"{run.interval_id} native artifact set is partial or unknown."
            )


def _directory_names(path: Path) -> set[str]:
    try:
        return {item.name for item in path.iterdir()}
    except OSError as exc:
        raise HpsRexAbsorbedMetricsError(f"required Phase 25D directory is unreadable: {path}") from exc


def _validate_incident_summary(
    summary: Mapping[str, Any], plan: HpsRexExecutionPlan
) -> None:
    if set(summary) != {
        "schema_version", "payload_type", "success", "execution_plan_id",
        "bundle_id", "fixture_occlusion", "room_model",
        "room_model_identity_sha256", "run_order",
        "receiver_contract", "shared_source",
        "incident_arrays", "runs", "run_bindings", "run_artifacts", "four_band_par",
        "scalar_versus_four_band", "native_commands", "scientific_pass_threshold",
        "reconciliation_scale", "absorbed_photon_processing", "limitations",
    }:
        raise HpsRexAbsorbedMetricsError("incident summary schema contains missing or unknown fields.")
    if (
        summary.get("schema_version") != HPS_REX_EXECUTION_SCHEMA_VERSION
        or summary.get("payload_type") != HPS_REX_EXECUTION_PAYLOAD_TYPE
        or summary.get("success") is not True
        or summary.get("execution_plan_id") != plan.execution_plan_id
        or summary.get("bundle_id") != plan.bundle.bundle_id
        or summary.get("fixture_occlusion")
        != plan.fixture_occlusion.scientific_payload()
        or summary.get("room_model") != production_room_model_payload()
        or summary.get("room_model_identity_sha256")
        != PRODUCTION_ROOM_MODEL_IDENTITY_SHA256
        or tuple(summary.get("run_order", ())) != HPS_REX_RUN_ORDER
        or summary.get("absorbed_photon_processing") is not False
        or summary.get("limitations") != list(HPS_REX_EXECUTION_LIMITATIONS)
    ):
        raise HpsRexAbsorbedMetricsError("incident summary is not the successful Phase 25D authority.")
    if _mapping(summary, "receiver_contract") != {
        "receiver_identity": plan.bundle.receiver_identity,
        "receiver_count": 1024,
        "front_receiver_count": 512,
        "back_receiver_count": 512,
        "physical_patch_count": 512,
        "ordering": "stable_patch_order_then_front_back",
        "combined_area_policy": "one physical patch area multiplies front plus back incident PFD once",
    }:
        raise HpsRexAbsorbedMetricsError("incident receiver contract mismatch.")
    shared = _mapping(summary, "shared_source")
    if shared != {
        "angular_dat_identity": plan.bundle.shared_angular_dat_identity,
        "angular_dat_sha256": _sha256_file(plan.paths.converted_dat),
        "converted_once": True,
        "source_cal_reference": "source.cal",
    }:
        raise HpsRexAbsorbedMetricsError("shared HPS angular DAT identity mismatch.")
    if _mapping(summary, "native_commands") != {
        "expected": 13, "recorded": 13, "ies2rad": 1, "oconv": 6,
        "rtrace": 6, "shell": False,
    }:
        raise HpsRexAbsorbedMetricsError("native command count or shell contract changed.")
    bindings = summary.get("run_bindings")
    if not isinstance(bindings, list) or len(bindings) != 6:
        raise HpsRexAbsorbedMetricsError("HPS run bindings are incomplete.")
    for raw, run in zip(bindings, plan.runs, strict=True):
        expected = {
            "interval_id": run.interval_id,
            "per_fixture_ppf_umol_s": run.part25b.per_fixture_ppf_umol_s,
            "whole_layout_ppf_umol_s": run.part25b.whole_layout_ppf_umol_s,
            "carrier_multiplier": run.part25b.carrier_multiplier,
            "material_identifier": run.part25b.material.material_identifier,
            "source_definition_id": run.part25b.source_definition_id,
            "octree_identity": run.part25b.fspm_scene.octree_identity,
            "ambient_cache_identity": run.part25b.ambient_cache_identity,
            "fixture_occlusion_identity": plan.fixture_occlusion.identity_sha256,
        }
        if not isinstance(raw, Mapping) or dict(raw) != expected:
            raise HpsRexAbsorbedMetricsError(f"{run.interval_id} run binding changed.")
    if summary.get("scientific_pass_threshold") is not None or summary.get("reconciliation_scale") is not None:
        raise HpsRexAbsorbedMetricsError("Phase 25D summary contains prohibited reconciliation.")


def _validate_source_conversion(
    payload: Mapping[str, Any], plan: HpsRexExecutionPlan
) -> None:
    try:
        raw_text = plan.paths.raw_converted_rad.read_text(encoding="ascii")
        contract = validate_converted_hps_ies_output(raw_text)
        dat_bytes = plan.paths.converted_dat.read_bytes()
        dat_metadata = validate_hps_converted_dat(dat_bytes)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        raise HpsRexAbsorbedMetricsError(f"HPS native source is invalid: {exc}") from exc
    expected = {
        "schema_version": 1,
        "source_plan_id": plan.source_plan.source_plan_id,
        "fixture_occlusion": plan.fixture_occlusion.scientific_payload(),
        "derived_ies_sha256": plan.source_plan.derived_ies.sha256,
        "raw_converted_rad_sha256": _sha256_file(plan.paths.raw_converted_rad),
        "raw_converted_geometry_included": False,
        "shared_dat": {
            "path": _relative(plan.paths.converted_dat, plan.paths.artifact_root),
            "execution_reference": plan.paths.converted_dat_execution_reference,
            "sha256": _sha256_bytes(dat_bytes),
            "metadata": dict(dat_metadata),
            "generated_once": True,
        },
        "canonical_cal_reference": contract.cal_reference,
        "run_source_hashes": {run.interval_id: run.source_text_sha256 for run in plan.runs},
    }
    if dict(payload) != expected:
        raise HpsRexAbsorbedMetricsError("HPS source conversion authority changed.")


def _validate_command_provenance(
    payload: Mapping[str, Any], plan: HpsRexExecutionPlan
) -> None:
    if set(payload) != {
        "schema_version", "success", "execution_plan_id",
        "expected_native_command_count", "recorded_native_command_count",
        "execution_order", "radiance_version", "commands",
    }:
        raise HpsRexAbsorbedMetricsError("native command provenance schema changed.")
    versions = payload.get("radiance_version")
    if (
        not isinstance(versions, Mapping)
        or set(versions) != {"rtrace_version", "oconv_version"}
        or versions.get("oconv_version") is not None
        or not (
            versions.get("rtrace_version") is None
            or isinstance(versions.get("rtrace_version"), str)
        )
    ):
        raise HpsRexAbsorbedMetricsError("native Radiance version provenance is malformed.")
    if (
        payload.get("schema_version") != 1
        or payload.get("success") is not True
        or payload.get("execution_plan_id") != plan.execution_plan_id
        or payload.get("expected_native_command_count") != HPS_REX_NATIVE_COMMAND_COUNT
        or payload.get("recorded_native_command_count") != HPS_REX_NATIVE_COMMAND_COUNT
    ):
        raise HpsRexAbsorbedMetricsError("native command provenance is incomplete.")
    expected = [
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
    if payload.get("execution_order") != [command.label for command, _, _ in expected]:
        raise HpsRexAbsorbedMetricsError("native command execution order changed.")
    records = payload.get("commands")
    if not isinstance(records, list) or len(records) != 13:
        raise HpsRexAbsorbedMetricsError("native command records are incomplete.")
    for index, (record, expected_item) in enumerate(zip(records, expected, strict=True)):
        command, interval_id, stderr_path = expected_item
        expected_argv = [_relative_token(token, plan.paths.artifact_root) for token in command.argv]
        if (
            not isinstance(record, Mapping)
            or set(record) != {
                "sequence_index", "interval_id", "label", "argv", "cwd",
                "stdin", "stdout", "stdout_mode", "return_code", "success",
                "stderr_sha256", "shell",
            }
            or record.get("sequence_index") != index
            or record.get("interval_id") != interval_id
            or record.get("label") != command.label
            or record.get("return_code") != 0
            or record.get("success") is not True
            or record.get("shell") is not False
            or not isinstance(record.get("argv"), list)
            or Path(str(record.get("argv", [""])[0])).name != Path(expected_argv[0]).name
            or record.get("argv", [])[1:] != expected_argv[1:]
            or record.get("cwd") != _optional_relative(command.cwd, plan.paths.artifact_root)
            or record.get("stdin") != _optional_relative(command.stdin_path, plan.paths.artifact_root)
            or record.get("stdout") != _optional_relative(command.stdout_path, plan.paths.artifact_root)
            or record.get("stdout_mode") != command.stdout_mode
            or record.get("stderr_sha256") != _sha256_file(stderr_path)
            or not _is_sha256(record.get("stderr_sha256"))
        ):
            raise HpsRexAbsorbedMetricsError(f"native command provenance mismatch at {index}.")


def _load_incident_npz(path: Path) -> tuple[tuple[str, FloatArray], ...]:
    try:
        with np.load(path, allow_pickle=False) as archive:
            if tuple(archive.files) != HPS_REX_INCIDENT_ARRAY_ORDER:
                raise HpsRexAbsorbedMetricsError("incident NPZ members are invalid.")
            raw_arrays = tuple((name, np.array(archive[name], copy=True)) for name in archive.files)
    except FileNotFoundError as exc:
        raise HpsRexAbsorbedMetricsError(f"incident NPZ is missing: {path}") from exc
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise HpsRexAbsorbedMetricsError(f"incident NPZ is malformed: {path}") from exc
    arrays: list[tuple[str, FloatArray]] = []
    for name, raw in raw_arrays:
        if (
            raw.shape != (1024,)
            or raw.dtype.hasobject
            or np.issubdtype(raw.dtype, np.bool_)
            or not (np.issubdtype(raw.dtype, np.integer) or np.issubdtype(raw.dtype, np.floating))
        ):
            raise HpsRexAbsorbedMetricsError(f"incident array {name} must be numeric shape (1024,).")
        value = np.asarray(raw, dtype=np.float64)
        if not np.all(np.isfinite(value)) or np.any(value < 0.0):
            raise HpsRexAbsorbedMetricsError(f"incident array {name} must be finite and nonnegative.")
        value.setflags(write=False)
        arrays.append((name, value))
    return tuple(arrays)


def _validate_native_artifacts(
    summary: Mapping[str, Any],
    plan: HpsRexExecutionPlan,
    arrays: Sequence[tuple[str, FloatArray]],
) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    records = summary.get("run_artifacts")
    if not isinstance(records, list) or len(records) != len(plan.runs):
        raise HpsRexAbsorbedMetricsError(
            "Phase 25D per-run artifact hash closure is missing or incomplete."
        )
    array_map = dict(arrays)
    accepted: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for record, run in zip(records, plan.runs, strict=True):
        if not isinstance(record, Mapping) or set(record) != {
            "interval_id",
            "source_rad_sha256",
            "plant_rad_sha256",
            "scene_manifest_sha256",
            "octree_sha256",
            "ambient_cache_sha256",
            "raw_rgb_sha256",
            "decoded_npy_sha256",
        }:
            raise HpsRexAbsorbedMetricsError(
                f"{run.interval_id} per-run artifact digest schema is incomplete."
            )
        if record.get("interval_id") != run.interval_id:
            raise HpsRexAbsorbedMetricsError(
                f"{run.interval_id} per-run artifact digest order changed."
            )
        paths = (
            ("source_rad_sha256", run.paths.source_rad),
            ("plant_rad_sha256", run.paths.plant_rad),
            ("scene_manifest_sha256", run.paths.scene_manifest),
            ("octree_sha256", run.paths.octree),
            ("ambient_cache_sha256", run.paths.ambient_cache),
            ("raw_rgb_sha256", run.paths.raw_rgb),
            ("decoded_npy_sha256", run.paths.decoded_npy),
        )
        hashes: list[tuple[str, str]] = []
        for name, path in paths:
            digest = _sha256_nonempty_file(path)
            if record.get(name) != digest or not _is_sha256(record.get(name)):
                raise HpsRexAbsorbedMetricsError(
                    f"{run.interval_id} native artifact digest mismatch: {name}."
                )
            hashes.append((name, digest))
        try:
            decoded = np.load(run.paths.decoded_npy, allow_pickle=False)
        except (OSError, ValueError) as exc:
            raise HpsRexAbsorbedMetricsError(f"{run.interval_id} decoded NPY is malformed.") from exc
        expected = array_map[f"{run.interval_id}_umol_m2_s"]
        if decoded.dtype != np.float64 or decoded.shape != (1024,) or not np.array_equal(decoded, expected):
            raise HpsRexAbsorbedMetricsError(
                f"{run.interval_id} decoded NPY differs from the final incident archive."
            )
        try:
            raw_values = parse_hps_rex_receiver_rgb(
                run.paths.raw_rgb.read_text(encoding="ascii"),
                interval_id=run.interval_id,
            )
        except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
            raise HpsRexAbsorbedMetricsError(
                f"{run.interval_id} raw RGB artifact is malformed."
            ) from exc
        if not np.array_equal(raw_values, expected):
            raise HpsRexAbsorbedMetricsError(
                f"{run.interval_id} raw RGB differs from the final incident archive."
            )
        accepted.append((run.interval_id, tuple(hashes)))
    return tuple(accepted)


def _validate_incident_metrics(
    summary: Mapping[str, Any],
    plan: HpsRexExecutionPlan,
    arrays: Sequence[tuple[str, FloatArray]],
) -> None:
    by_name = {name.removesuffix("_umol_m2_s"): value for name, value in arrays}
    expected_runs = [
        compute_hps_incident_run_metrics(name, by_name[name], plan.receiver_samples).to_payload()
        for name in HPS_REX_RUN_ORDER
    ]
    if summary.get("runs") != expected_runs:
        raise HpsRexAbsorbedMetricsError("persisted HPS run metrics are stale or changed.")
    four_band = np.add.reduce(
        [by_name[name] for name in ("blue", "green", "orange", "red")]
    ).astype(np.float64, copy=False)
    expected_four = compute_hps_incident_run_metrics(
        "four_band_par", four_band, plan.receiver_samples
    ).to_payload()
    expected_scalar = compute_hps_scalar_four_band_diagnostics(
        by_name["scalar_par"], four_band, plan.receiver_samples
    ).to_payload()
    if summary.get("four_band_par") != expected_four or summary.get("scalar_versus_four_band") != expected_scalar:
        raise HpsRexAbsorbedMetricsError("persisted HPS scalar/four-band diagnostics changed.")


def compute_hps_rex_absorbed_metrics(
    loaded: LoadedHpsRexIncidentWorkspace,
) -> ComputedHpsRexAbsorbedMetrics:
    """Apply the immutable Phase 25B HPS A/T/R authority in memory."""

    plan = loaded.plan
    if loaded.artifact_root != plan.paths.artifact_root:
        raise HpsRexAbsorbedMetricsError("loaded HPS artifact root does not match its plan.")
    source = SourceNeutralAbsorbedMetricsInput(
        source_family="hps",
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
            for band_id in HPS_REX_RUN_ORDER[1:]
        ),
    )
    joined = f"{source.source_payload_id} {source.optics_payload_id}".lower()
    if "smd" in joined or "conventional" in joined or "hps" not in joined:
        raise HpsRexAbsorbedMetricsError("cross-source authority entered HPS absorption.")
    spectral = compute_source_neutral_absorbed_metrics(
        source,
        plan.receiver_samples,
        {band_id: loaded.array(band_id) for band_id in HPS_REX_RUN_ORDER[1:]},
    )
    if len(spectral.patch_pairs) != 512 or len(spectral.leaves) != 32:
        raise HpsRexAbsorbedMetricsError("HPS patch/leaf aggregation closure failed.")

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
    closure_pfd = combined_absorbed + combined_transmitted + combined_reflected - combined_incident
    if any(
        not np.allclose(partitions, incident, rtol=1e-12, atol=1e-12)
        for partitions, incident in (
            (front_absorbed + front_transmitted + front_reflected, front_incident),
            (back_absorbed + back_transmitted + back_reflected, back_incident),
            (
                combined_absorbed + combined_transmitted + combined_reflected,
                combined_incident,
            ),
        )
    ):
        raise HpsRexAbsorbedMetricsError("local HPS A/T/R PFD closure failed.")

    four_front_incident = front_incident[:, :4].sum(axis=1)
    four_back_incident = back_incident[:, :4].sum(axis=1)
    four_combined_incident = four_front_incident + four_back_incident
    four_front_absorbed = front_absorbed[:, :4].sum(axis=1)
    four_back_absorbed = back_absorbed[:, :4].sum(axis=1)
    four_combined_absorbed = four_front_absorbed + four_back_absorbed

    scalar = loaded.array("scalar_par")
    scalar_front_incident = scalar[front_indices]
    scalar_back_incident = scalar[back_indices]
    scalar_combined_incident = scalar_front_incident + scalar_back_incident
    scalar_a = plan.bundle.material_plan.material("scalar_par").source_interval.coefficients.absorptance
    scalar_front_absorbed = scalar_front_incident * scalar_a
    scalar_back_absorbed = scalar_back_incident * scalar_a
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
    patch_rmse = _rmse(scalar_combined_absorbed, four_combined_absorbed)
    receiver_rms = _rms(receiver_scalar)
    patch_rms = _rms(scalar_combined_absorbed)
    diagnostic = HpsScalarAbsorbedValidationDiagnostic(
        scalar_absorptance=scalar_a,
        scalar_whole_plant_absorbed_flux=scalar_flux,
        four_band_whole_plant_absorbed_flux=four_flux,
        whole_plant_signed_difference=scalar_flux - four_flux,
        whole_plant_absolute_difference=abs(scalar_flux - four_flux),
        whole_plant_relative_difference=_relative_absolute_difference(scalar_flux, four_flux),
        scalar_area_weighted_absorbed_pfd=scalar_mean,
        four_band_area_weighted_absorbed_pfd=four_mean,
        area_weighted_signed_difference=scalar_mean - four_mean,
        area_weighted_absolute_difference=abs(scalar_mean - four_mean),
        area_weighted_relative_difference=_relative_absolute_difference(scalar_mean, four_mean),
        front_absorbed_flux_signed_difference=scalar_front_flux - four_front_flux,
        back_absorbed_flux_signed_difference=scalar_back_flux - four_back_flux,
        receiver_level_rmse=receiver_rmse,
        receiver_level_relative_rmse=None if receiver_rms == 0.0 else receiver_rmse / receiver_rms,
        patch_level_rmse=patch_rmse,
        patch_level_relative_rmse=None if patch_rms == 0.0 else patch_rmse / patch_rms,
    )

    patch_arrays = HpsAbsorbedPatchArrays(
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
    return ComputedHpsRexAbsorbedMetrics(
        spectral,
        diagnostic,
        patch_arrays,
        float(np.max(np.abs(base["energy_closure_error_flux"]))),
        float(np.max(np.abs(closure_pfd))),
    )


def format_hps_absorbed_patch_npz(arrays: HpsAbsorbedPatchArrays) -> bytes:
    """Serialize the fixed-order HPS patch schema without timestamps."""

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


def read_hps_absorbed_patch_npz(path: str | Path) -> dict[str, NDArray[Any]]:
    """Read and validate one deterministic HPS absorbed patch archive."""

    source = Path(path)
    try:
        with np.load(source, allow_pickle=False) as archive:
            if tuple(archive.files) != HPS_ABSORBED_NPZ_ORDER:
                raise HpsRexAbsorbedMetricsError("absorbed NPZ members are incomplete or out of order.")
            arrays = {name: np.array(archive[name], copy=True) for name in archive.files}
    except FileNotFoundError as exc:
        raise HpsRexAbsorbedMetricsError(f"absorbed NPZ is missing: {source}") from exc
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise HpsRexAbsorbedMetricsError(f"absorbed NPZ is malformed: {source}") from exc
    for name, array in arrays.items():
        if array.dtype.hasobject or not (
            np.issubdtype(array.dtype, np.integer) or np.issubdtype(array.dtype, np.floating)
        ) or not np.all(np.isfinite(array)):
            raise HpsRexAbsorbedMetricsError(f"absorbed array {name} must be finite numeric data.")
    if any(arrays[name].shape != (512,) for name in HPS_ABSORBED_NPZ_ORDER[:5]):
        raise HpsRexAbsorbedMetricsError("absorbed patch vectors have invalid shape.")
    if any(arrays[name].shape != (5,) for name in HPS_ABSORBED_NPZ_ORDER[5:8]):
        raise HpsRexAbsorbedMetricsError("absorbed coefficient vectors have invalid shape.")
    if any(arrays[name].shape != (512, 5) for name in HPS_ABSORBED_NPZ_ORDER[8:25]):
        raise HpsRexAbsorbedMetricsError("absorbed band matrices have invalid shape.")
    if any(arrays[name].shape != (512,) for name in HPS_ABSORBED_NPZ_ORDER[25:]):
        raise HpsRexAbsorbedMetricsError("absorbed aggregate vectors have invalid shape.")
    return arrays


def format_hps_rex_absorbed_patch_npz(arrays: HpsAbsorbedPatchArrays) -> bytes:
    """Explicit Rex-named compatibility spelling for the HPS formatter."""

    return format_hps_absorbed_patch_npz(arrays)


def read_hps_rex_absorbed_patch_npz(
    path: str | Path,
) -> dict[str, NDArray[Any]]:
    """Explicit Rex-named compatibility spelling for the HPS reader."""

    return read_hps_absorbed_patch_npz(path)


def calculate_hps_rex_absorbed_metrics(
    workspace: str | Path,
) -> HpsRexAbsorbedMetricsResult:
    """Validate Phase 25D, calculate absorption, and atomically persist two artifacts."""

    loaded = load_hps_rex_incident_workspace(workspace)
    computed = compute_hps_rex_absorbed_metrics(loaded)
    absorbed_root = loaded.artifact_root / "absorbed"
    summary_path = absorbed_root / "absorbed_photon_summary.json"
    patch_path = absorbed_root / "absorbed_patch_metrics.npz"
    npz_bytes = format_hps_absorbed_patch_npz(computed.patch_arrays)
    npz_hash = _sha256_bytes(npz_bytes)
    scientific = _absorbed_scientific_payload(loaded, computed, npz_hash)
    summary_id = "hps-rex-absorbed-summary-v1-" + _hash_payload(scientific)
    summary = scientific | {
        "schema_version": HPS_ABSORBED_SCHEMA_VERSION,
        "payload_type": HPS_ABSORBED_PAYLOAD_TYPE,
        "success": True,
        "summary_id": summary_id,
        "source_artifacts": {
            "incident_summary": _relative(loaded.plan.paths.incident_transport_summary, loaded.artifact_root),
            "incident_receiver_values": _relative(loaded.plan.paths.incident_receiver_values_npz, loaded.artifact_root),
            "receiver_metadata": _relative(loaded.plan.paths.receiver_metadata, loaded.artifact_root),
        },
        "output_artifacts": {
            "patch_metrics_npz": _relative(patch_path, loaded.artifact_root),
            "patch_metrics_npz_sha256": npz_hash,
            "npz_array_order": list(HPS_ABSORBED_NPZ_ORDER),
        },
        "patch_order": [item.to_dict() for item in computed.spectral.patch_pairs],
        "leaf_summaries": [item.to_dict() for item in computed.spectral.leaves],
        "whole_plant_summary": computed.spectral.plant.to_dict(),
    }
    summary_text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    _write_or_validate_absorbed_artifacts(
        absorbed_root, summary_path, summary_text, patch_path, npz_bytes
    )
    _assert_array_mapping_equal(computed.patch_arrays.as_dict(), read_hps_absorbed_patch_npz(patch_path))
    if _sha256_file(patch_path) != npz_hash or _load_json(summary_path, "absorbed summary") != summary:
        raise HpsRexAbsorbedMetricsError("HPS absorbed artifact round-trip changed.")
    return HpsRexAbsorbedMetricsResult(
        loaded, computed, summary, summary_path, patch_path, summary_id, npz_hash
    )


def _absorbed_scientific_payload(
    loaded: LoadedHpsRexIncidentWorkspace,
    computed: ComputedHpsRexAbsorbedMetrics,
    patch_npz_sha256: str,
) -> dict[str, object]:
    plan = loaded.plan
    optics = plan.bundle.optical_payload
    coefficients = {
        interval_id: plan.bundle.material_plan.material(interval_id).source_interval.coefficients.to_dict()
        for interval_id in HPS_REX_RUN_ORDER
    }
    return {
        "scientific_claim": HPS_ABSORBED_CLAIM,
        "identities": {
            "layout_id": plan.bundle.layout.layout_id,
            "phase25a_source_plan_id": plan.bundle.phase25a_source_plan.source_plan_id,
            "fixture_occlusion_identity": plan.fixture_occlusion.identity_sha256,
            "hps_source_payload_id": plan.bundle.source_payload.source_payload_id,
            "hps_rex_optics_id": optics.optical_payload_id,
            "material_plan_id": plan.bundle.material_plan.material_plan_id,
            "room_identity": plan.bundle.room_identity,
            "plant_identity": plan.bundle.plant_identity,
            "receiver_identity": plan.bundle.receiver_identity,
            "angular_dat_identity": plan.bundle.shared_angular_dat_identity,
            "phase25b_bundle_id": plan.bundle.bundle_id,
            "phase25d_execution_plan_id": plan.execution_plan_id,
        },
        "input_hashes": {
            "incident_summary_sha256": loaded.incident_summary_sha256,
            "incident_receiver_values_sha256": loaded.incident_npz_sha256,
            "source_conversion_summary_sha256": _sha256_file(plan.paths.source_conversion_summary),
            "command_provenance_summary_sha256": _sha256_file(plan.paths.command_provenance_summary),
            "phase25b_bundle_sha256": _sha256_file(plan.paths.bundle_manifest),
            "phase25d_execution_plan_sha256": _sha256_file(plan.paths.execution_plan_summary),
            "source_payload_sha256": _sha256_file(plan.paths.source_payload_json),
            "optical_payload_sha256": _sha256_file(plan.paths.optical_payload_json),
            "material_plan_sha256": _sha256_file(plan.paths.material_plan_json),
            "derived_ies_sha256": _sha256_file(plan.paths.derived_ies),
            "raw_converted_rad_sha256": _sha256_file(plan.paths.raw_converted_rad),
            "angular_dat_sha256": _sha256_file(plan.paths.converted_dat),
            "room_rad_sha256": _sha256_file(plan.paths.room_rad),
            "generic_plant_rad_sha256": _sha256_file(plan.paths.generic_plant_rad),
            "receiver_rays_sha256": _sha256_file(plan.paths.receiver_rays),
            "receiver_metadata_sha256": _sha256_file(plan.paths.receiver_metadata),
            "fixture_occlusion_manifest_sha256": _sha256_file(
                plan.fixture_occlusion.metadata_path
            ),
            "fixture_body_instances_sha256": _sha256_file(
                plan.fixture_occlusion.instance_source_path
            ),
        },
        "native_run_artifact_hashes": {
            interval_id: dict(hashes)
            for interval_id, hashes in loaded.native_artifact_hashes
        },
        "absorbed_patch_metrics_sha256": patch_npz_sha256,
        "band_order": list(HPS_REX_RUN_ORDER[1:]),
        "par_band_order": ["blue", "green", "orange", "red"],
        "scalar_par_policy": "validation_diagnostic_only_not_added_to_four_band_PAR",
        "far_red_policy": "separate_from_PAR",
        "coefficients": coefficients,
        "source_coverage": optics.scientific_payload()["source_coverage"],
        "unsupported_source_mass_redistributed": False,
        "counts": {"receivers": 1024, "physical_patches": 512, "leaves": 32},
        "total_physical_leaf_area_m2": computed.spectral.plant.physical_area_m2,
        "definitions": {
            "combined_incident_pfd": "front_incident_pfd + back_incident_pfd",
            "absorbed_pfd": "absorptance * combined_incident_pfd",
            "transmitted_pfd": "transmittance * combined_incident_pfd",
            "reflected_pfd": "reflectance * combined_incident_pfd",
            "partition_flux": "partition_pfd * one_physical_patch_area",
            "local_partition_interpretation": "transmitted and reflected terms are local interactions, not net escaped light",
        },
        "primary_four_band_PAR": computed.spectral.plant.par.to_dict(),
        "far_red": computed.spectral.plant.far_red.to_dict(),
        "scalar_absorbed_validation": computed.scalar_diagnostic.to_payload(),
        "local_closure": {
            "maximum_absolute_error_flux_umol_s": computed.maximum_local_closure_error_flux,
            "maximum_absolute_error_pfd_umol_m2_s": computed.maximum_local_closure_error_pfd,
            "relative_tolerance": 1e-12,
            "absolute_tolerance": 1e-12,
        },
        "reconciliation_scale": None,
        "scientific_pass_threshold": None,
        "native_incident_artifacts_modified": False,
        "limitations": list(HPS_ABSORBED_LIMITATIONS),
    }


def _write_or_validate_absorbed_artifacts(
    root: Path,
    summary_path: Path,
    summary_text: str,
    patch_path: Path,
    patch_bytes: bytes,
) -> None:
    expected = {summary_path.name, patch_path.name}
    if root.exists():
        if not root.is_dir():
            raise HpsRexAbsorbedMetricsError("absorbed output root is not a directory.")
        existing = {item.name for item in root.iterdir()}
        if existing and existing != expected:
            raise HpsRexAbsorbedMetricsError(
                "absorbed output directory contains partial, stale, or unknown artifacts."
            )
        if existing == expected:
            try:
                matches = summary_path.read_text(encoding="utf-8") == summary_text and patch_path.read_bytes() == patch_bytes
            except (OSError, UnicodeError) as exc:
                raise HpsRexAbsorbedMetricsError("existing absorbed artifacts are unreadable.") from exc
            if not matches:
                raise HpsRexAbsorbedMetricsError(
                    "existing absorbed artifacts differ from deterministic results."
                )
            return
    root.mkdir(parents=True, exist_ok=True)
    wrote_patch = False
    try:
        atomic_write_bytes(patch_path, patch_bytes)
        wrote_patch = True
        atomic_write_text(summary_path, summary_text)
    except BaseException:
        if wrote_patch and not summary_path.exists():
            patch_path.unlink(missing_ok=True)
        raise


def _validate_exact_text(path: Path, expected: str, label: str) -> None:
    try:
        actual = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HpsRexAbsorbedMetricsError(f"{label} is missing: {path}") from exc
    except (OSError, UnicodeError) as exc:
        raise HpsRexAbsorbedMetricsError(f"{label} is unreadable: {path}") from exc
    if actual != expected:
        raise HpsRexAbsorbedMetricsError(f"{label} differs from its reconstructed typed plan.")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HpsRexAbsorbedMetricsError(f"{label} is missing: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HpsRexAbsorbedMetricsError(f"{label} is malformed: {path}") from exc
    if not isinstance(payload, dict):
        raise HpsRexAbsorbedMetricsError(f"{label} must be a JSON object.")
    return payload


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise HpsRexAbsorbedMetricsError(f"{key} must be an object.")
    return value


def _string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise HpsRexAbsorbedMetricsError(f"{key} must be a non-empty string.")
    return value


def _integer(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise HpsRexAbsorbedMetricsError(f"{key} must be an integer.")
    return value


def _number(payload: Mapping[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
        raise HpsRexAbsorbedMetricsError(f"{key} must be finite numeric data.")
    return float(value)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise HpsRexAbsorbedMetricsError(f"artifact escapes HPS workspace: {path}") from exc


def _optional_relative(path: Path | None, root: Path) -> str | None:
    return None if path is None else _relative(path, root)


def _relative_token(token: str, root: Path) -> str:
    path = Path(token)
    if not path.is_absolute():
        return token
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return token


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _rmse(left: FloatArray, right: FloatArray) -> float:
    difference = np.asarray(left, dtype=np.float64) - np.asarray(right, dtype=np.float64)
    return float(np.sqrt(np.mean(np.square(difference))))


def _rms(value: FloatArray) -> float:
    return float(np.sqrt(np.mean(np.square(np.asarray(value, dtype=np.float64)))))


def _relative_absolute_difference(reference: float, candidate: float) -> float | None:
    return None if reference == 0.0 else abs(reference - candidate) / abs(reference)


def _assert_array_mapping_equal(
    expected: Mapping[str, NDArray[Any]], actual: Mapping[str, NDArray[Any]]
) -> None:
    if tuple(actual) != tuple(expected):
        raise HpsRexAbsorbedMetricsError("absorbed array order changed.")
    for name in expected:
        if expected[name].dtype != actual[name].dtype or not np.array_equal(expected[name], actual[name]):
            raise HpsRexAbsorbedMetricsError(f"absorbed array round-trip changed: {name}.")


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise HpsRexAbsorbedMetricsError(f"required artifact is unreadable: {path}") from exc


def _sha256_nonempty_file(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HpsRexAbsorbedMetricsError(f"required native artifact is unreadable: {path}") from exc
    if not data:
        raise HpsRexAbsorbedMetricsError(f"required native artifact is empty: {path}")
    return _sha256_bytes(data)
