"""Sequential native execution for the isolated five-band Rex receiver plan.

All process execution remains behind :class:`LocalRunner`.  This module does
not combine bands and produces incident-light diagnostics only.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from numpy.typing import NDArray

from fspm_optics.plants.generator import generate_rex_butterhead_plant
from fspm_optics.plants.radiance_export import export_plant_mesh_to_radiance
from fspm_optics.plants.rex import RexPlantConfig
from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.radiance.versioning import RadianceInstallation
from fspm_optics.receivers.samples import (
    MeshPatchReceiverSample,
    build_two_sided_patch_receivers,
    receiver_sample_input_text,
)
from fspm_optics.transport.basis.atomic import atomic_save_npy, atomic_write_text
from fspm_optics.transport.five_band import (
    EXPECTED_FAR_RED_RESULT_UNITS,
    EXPECTED_PAR_BAND_RESULT_UNITS,
    FIVE_BAND_ORDER,
    FIVE_BAND_PLAN_PAYLOAD_TYPE,
    FIVE_BAND_PLAN_SCHEMA_VERSION,
    RexBandTransportPlan,
    RexFiveBandTransportPlan,
    format_rex_five_band_transport_plan_json,
    plan_rex_five_band_transport,
)
from fspm_optics.transport.scalar_ppfd import decode_grey_channel_ppfd

FloatArray = NDArray[np.float64]

FIVE_BAND_NATIVE_RUN_SCHEMA_VERSION = 1
FIVE_BAND_NATIVE_RUN_PAYLOAD_TYPE = "fspm_optics_rex_five_band_receiver_run"
FIVE_BAND_NATIVE_BACKEND = "repeated_isolated_scalar_rtrace_native_execution"
FIVE_BAND_RECEIVER_SCIENTIFIC_CLAIM = (
    "Band-resolved incident photon flux density at ordered two-sided Rex leaf "
    "receivers under the Phase 18 source and material model."
)
FIVE_BAND_SUMMARY_FILENAME = "five_band_receiver_summary.json"
RECEIVER_INTERPRETATION = (
    "Front records measure the front incident hemisphere and paired back records "
    "measure the back incident hemisphere.",
    "One physical patch area corresponds to each ordered front/back receiver pair.",
    "Transmitted outgoing light is not counted as opposite-side incident light.",
    "Light genuinely returned by later reflection may subsequently be incident.",
    "Front and back means are separate diagnostics and are not summed or reported "
    "as a conventional horizontal PPFD measurement.",
)
SCALAR_PAR_RESULT_POLICY = (
    "scalar_PAR_is_separate_and_is_not_included_in_or_added_to_five_band_results"
)
INCIDENT_ONLY_POLICY = "incident_light_diagnostics_only_no_leaf_uptake_calculation"


class FiveBandReceiverSmokeError(RuntimeError):
    """A five-band native receiver run failed its execution contract."""


@dataclass(frozen=True, slots=True)
class FiveBandIncidentMetrics:
    """Incident band photon-flux-density diagnostics for one ordered trace."""

    band_id: str
    quantity: str
    units: str
    receiver_count: int
    front_receiver_count: int
    back_receiver_count: int
    minimum_incident_band_pfd: float
    maximum_incident_band_pfd: float
    mean_incident_band_pfd: float
    front_mean_incident_band_pfd: float
    back_mean_incident_band_pfd: float
    area_weighted_mean_incident_band_pfd: float

    def __post_init__(self) -> None:
        if self.band_id not in FIVE_BAND_ORDER:
            raise ValueError(f"unsupported five-band metric identity: {self.band_id!r}.")
        expected_quantity = (
            "far_red_photon_flux_density"
            if self.band_id == "far_red"
            else f"{self.band_id}_PAR_band_photon_flux_density"
        )
        expected_units = (
            EXPECTED_FAR_RED_RESULT_UNITS
            if self.band_id == "far_red"
            else EXPECTED_PAR_BAND_RESULT_UNITS
        )
        if self.quantity != expected_quantity or self.units != expected_units:
            raise ValueError("band quantity or units do not match the fixed band identity.")
        if (
            self.receiver_count != 1024
            or self.front_receiver_count != 512
            or self.back_receiver_count != 512
        ):
            raise ValueError("five-band metrics require 1024 ordered two-sided receivers.")
        for name in (
            "minimum_incident_band_pfd",
            "maximum_incident_band_pfd",
            "mean_incident_band_pfd",
            "front_mean_incident_band_pfd",
            "back_mean_incident_band_pfd",
            "area_weighted_mean_incident_band_pfd",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative.")
        if self.minimum_incident_band_pfd > self.maximum_incident_band_pfd:
            raise ValueError("minimum incident band PFD exceeds maximum.")

    def to_dict(self) -> dict[str, str | int | float]:
        return {
            "band_id": self.band_id,
            "quantity": self.quantity,
            "units": self.units,
            "receiver_count": self.receiver_count,
            "front_receiver_count": self.front_receiver_count,
            "back_receiver_count": self.back_receiver_count,
            "min_incident_band_pfd": self.minimum_incident_band_pfd,
            "max_incident_band_pfd": self.maximum_incident_band_pfd,
            "mean_incident_band_pfd": self.mean_incident_band_pfd,
            "front_mean_incident_band_pfd": self.front_mean_incident_band_pfd,
            "back_mean_incident_band_pfd": self.back_mean_incident_band_pfd,
            "area_weighted_mean_incident_band_pfd": (
                self.area_weighted_mean_incident_band_pfd
            ),
        }


@dataclass(frozen=True, slots=True)
class FiveBandCommandRunRecord:
    """Commands, runner records, paths, hashes, and metrics for one band."""

    band_id: str
    oconv_command: CommandSpec
    rtrace_command: CommandSpec
    oconv_result: RunnerResult
    rtrace_result: RunnerResult
    octree_path: Path
    raw_rgb_path: Path
    decoded_npy_path: Path
    metrics: FiveBandIncidentMetrics
    input_hashes: tuple[tuple[str, str], ...]
    output_hashes: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "band_id": self.band_id,
            "metrics": self.metrics.to_dict(),
            "paths": {
                "octree": str(self.octree_path),
                "raw_rgb": str(self.raw_rgb_path),
                "decoded_npy": str(self.decoded_npy_path),
            },
            "hashes": {
                "inputs": dict(self.input_hashes),
                "outputs": dict(self.output_hashes),
            },
            "commands": {
                "oconv": _command_payload(self.oconv_command),
                "rtrace": _command_payload(self.rtrace_command),
            },
            "runs": {
                "oconv": _runner_result_payload(self.oconv_result),
                "rtrace": _runner_result_payload(self.rtrace_result),
            },
        }


@dataclass(frozen=True, slots=True)
class RexFiveBandReceiverSmokeResult:
    """Immutable identity and records for one complete sequential native run."""

    workspace_root: Path
    artifact_root: Path
    transport_plan: RexFiveBandTransportPlan
    transport_plan_sha256: str
    band_order: tuple[str, ...]
    band_runs: tuple[FiveBandCommandRunRecord, ...]
    summary_path: Path
    receiver_count: int
    front_receiver_count: int
    back_receiver_count: int
    radiance_installation: RadianceInstallation
    scientific_claim: str = FIVE_BAND_RECEIVER_SCIENTIFIC_CLAIM
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.band_order != FIVE_BAND_ORDER:
            raise ValueError("native result must preserve the fixed five-band order.")
        if tuple(item.band_id for item in self.band_runs) != FIVE_BAND_ORDER:
            raise ValueError("native band run records are incomplete or out of order.")
        if (
            self.workspace_root != self.transport_plan.workspace
            or self.artifact_root != self.transport_plan.output_root
            or self.summary_path
            != self.transport_plan.output_root / FIVE_BAND_SUMMARY_FILENAME
        ):
            raise ValueError("native result paths do not match the transport plan.")
        if self.limitations != self.transport_plan.limitations:
            raise ValueError("native result must preserve all Phase 18 limitations.")
        if (
            self.receiver_count != 1024
            or self.front_receiver_count != 512
            or self.back_receiver_count != 512
        ):
            raise ValueError("native result receiver counts are invalid.")
        _validate_sha256("transport_plan_sha256", self.transport_plan_sha256)

    def to_summary_payload(self) -> dict[str, Any]:
        plan = self.transport_plan
        return {
            "schema_version": FIVE_BAND_NATIVE_RUN_SCHEMA_VERSION,
            "payload_type": FIVE_BAND_NATIVE_RUN_PAYLOAD_TYPE,
            "success": True,
            "backend": FIVE_BAND_NATIVE_BACKEND,
            "workspace_root": str(self.workspace_root),
            "artifact_root": str(self.artifact_root),
            "transport_plan_identity": {
                "payload_type": FIVE_BAND_PLAN_PAYLOAD_TYPE,
                "schema_version": FIVE_BAND_PLAN_SCHEMA_VERSION,
                "manifest_path": str(plan.manifest_path),
                "manifest_sha256": self.transport_plan_sha256,
                "layout_sha256": plan.layout_sha256,
                "geometry_sha256": plan.geometry_sha256,
                "receiver_text_sha256": plan.receiver_text_sha256,
                "source_model_id": plan.source_model_id,
                "source_model_json_sha256": plan.source_model_json_sha256,
                "material_policy_id": plan.material_policy_id,
                "material_plan_json_sha256": plan.material_plan_json_sha256,
            },
            "band_order": list(self.band_order),
            "receiver_counts": {
                "all": self.receiver_count,
                "front": self.front_receiver_count,
                "back": self.back_receiver_count,
                "physical_patches": plan.patch_count,
            },
            "receiver_interpretation": list(RECEIVER_INTERPRETATION),
            "physical_patch_area_policy": plan.physical_patch_area_policy,
            "receiver_hemisphere_policy": plan.receiver_hemisphere_policy,
            "area_weighted_mean_definition": (
                "receiver-record mean weighted by the shared physical patch area; "
                "front and back are not summed"
            ),
            "result_scope": INCIDENT_ONLY_POLICY,
            "scalar_par_policy": SCALAR_PAR_RESULT_POLICY,
            "scientific_claim": self.scientific_claim,
            "phase18_scientific_claim": plan.scientific_claim,
            "limitations": list(self.limitations),
            "shared_input_hashes": {
                "transport_plan_sha256": self.transport_plan_sha256,
                "source_model_json_sha256": _sha256_file(plan.source_model_path),
                "material_plan_json_sha256": _sha256_file(plan.material_plan_path),
                "room_sha256": _sha256_file(plan.room_path),
                "receiver_text_sha256": _sha256_file(plan.receiver_path),
            },
            "radiance_installation": self.radiance_installation.to_dict(),
            "bands": [item.to_dict() for item in self.band_runs],
        }


def run_rex_five_band_receiver_smoke(
    plan_or_workspace: RexFiveBandTransportPlan | str | Path,
    runner: LocalRunner,
    *,
    radiance_installation: RadianceInstallation,
    output_root: str | Path | None = None,
    seed: int = 1,
    normal_offset_m: float = 5e-5,
    nthreads: int | None = None,
) -> RexFiveBandReceiverSmokeResult:
    """Build or accept the Phase 18 plan, then execute it sequentially."""

    plan = (
        plan_or_workspace
        if isinstance(plan_or_workspace, RexFiveBandTransportPlan)
        else plan_rex_five_band_transport(
            plan_or_workspace,
            output_root=output_root,
            seed=seed,
            normal_offset_m=normal_offset_m,
            nthreads=nthreads,
        )
    )
    return execute_rex_five_band_receiver_smoke(
        plan,
        runner,
        radiance_installation=radiance_installation,
    )


def execute_rex_five_band_receiver_smoke(
    plan: RexFiveBandTransportPlan,
    runner: LocalRunner,
    *,
    radiance_installation: RadianceInstallation,
) -> RexFiveBandReceiverSmokeResult:
    """Execute five isolated ``oconv``/``rtrace`` pairs in fixed order."""

    samples = _validate_materialized_plan(plan)
    _prepare_output_paths(plan)
    front_indices, back_indices, area_weights = _receiver_groups(samples)
    plan_sha256 = _sha256_file(plan.manifest_path)
    band_records: list[FiveBandCommandRunRecord] = []

    for band in plan.band_plans:
        oconv_command = _replace_executable(
            band.oconv_command, radiance_installation.oconv.path
        )
        rtrace_command = _replace_executable(
            band.rtrace_command, radiance_installation.rtrace.path
        )
        oconv_result = runner.run(oconv_command)
        _require_successful_run(band.band_id, "oconv", oconv_command, oconv_result)
        _require_output(band.band_id, "binary octree", band.paths.octree_path)

        rtrace_result = runner.run(rtrace_command)
        _require_successful_run(band.band_id, "rtrace", rtrace_command, rtrace_result)
        _require_output(band.band_id, "ASCII RGB", band.paths.rgb_output_path)
        try:
            rgb_text = band.paths.rgb_output_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise FiveBandReceiverSmokeError(
                f"{band.band_id} receiver RGB output is not valid UTF-8 text: "
                f"{band.paths.rgb_output_path}"
            ) from exc
        values = parse_five_band_receiver_rgb(
            rgb_text,
            expected_receiver_count=plan.receiver_count,
            band_id=band.band_id,
        )
        metrics = compute_five_band_incident_metrics(
            band,
            values,
            samples,
            front_indices=front_indices,
            back_indices=back_indices,
            area_weights=area_weights,
        )
        atomic_save_npy(band.paths.decoded_pfd_path, values)
        _validate_saved_npy(band.band_id, band.paths.decoded_pfd_path, values)
        band_records.append(
            FiveBandCommandRunRecord(
                band_id=band.band_id,
                oconv_command=oconv_command,
                rtrace_command=rtrace_command,
                oconv_result=oconv_result,
                rtrace_result=rtrace_result,
                octree_path=band.paths.octree_path,
                raw_rgb_path=band.paths.rgb_output_path,
                decoded_npy_path=band.paths.decoded_pfd_path,
                metrics=metrics,
                input_hashes=(
                    ("emitters_rad_sha256", _sha256_file(band.paths.emitter_path)),
                    ("rex_plant_rad_sha256", _sha256_file(band.paths.plant_path)),
                ),
                output_hashes=(
                    ("scene_oct_sha256", _sha256_file(band.paths.octree_path)),
                    ("receiver_rgb_sha256", _sha256_file(band.paths.rgb_output_path)),
                    (
                        "receiver_band_pfd_npy_sha256",
                        _sha256_file(band.paths.decoded_pfd_path),
                    ),
                ),
            )
        )

    result = RexFiveBandReceiverSmokeResult(
        workspace_root=plan.workspace,
        artifact_root=plan.output_root,
        transport_plan=plan,
        transport_plan_sha256=plan_sha256,
        band_order=FIVE_BAND_ORDER,
        band_runs=tuple(band_records),
        summary_path=plan.output_root / FIVE_BAND_SUMMARY_FILENAME,
        receiver_count=plan.receiver_count,
        front_receiver_count=len(front_indices),
        back_receiver_count=len(back_indices),
        radiance_installation=radiance_installation,
        limitations=plan.limitations,
    )
    atomic_write_text(
        result.summary_path,
        json.dumps(result.to_summary_payload(), indent=2, sort_keys=True) + "\n",
    )
    return result


def parse_five_band_receiver_rgb(
    rgb_text: str,
    *,
    expected_receiver_count: int = 1024,
    band_id: str = "receiver",
) -> FloatArray:
    """Strictly decode exactly three equal grayscale channels per receiver row."""

    if (
        isinstance(expected_receiver_count, bool)
        or not isinstance(expected_receiver_count, int)
        or expected_receiver_count <= 0
    ):
        raise ValueError("expected_receiver_count must be a positive integer.")
    values: list[float] = []
    for row_number, line in enumerate(rgb_text.splitlines(), start=1):
        parts = line.split()
        if len(parts) != 3:
            raise FiveBandReceiverSmokeError(
                f"{band_id} receiver row {row_number} must contain exactly three "
                f"RGB channels; got {len(parts)}."
            )
        try:
            red, green, blue = (float(value) for value in parts)
            decoded = decode_grey_channel_ppfd(
                red, green, blue, row_number=row_number
            )
        except ValueError as exc:
            raise FiveBandReceiverSmokeError(
                f"{band_id} receiver row {row_number} failed strict grayscale "
                f"photon decoding: {exc}"
            ) from exc
        values.append(decoded)
    if len(values) != expected_receiver_count:
        raise FiveBandReceiverSmokeError(
            f"{band_id} receiver row count mismatch: expected "
            f"{expected_receiver_count}, got {len(values)}."
        )
    array = np.asarray(values, dtype=np.float64)
    if array.shape != (expected_receiver_count,):
        raise FiveBandReceiverSmokeError(
            f"{band_id} decoded receiver shape mismatch: {array.shape}."
        )
    if array.dtype != np.dtype(np.float64) or not np.all(np.isfinite(array)):
        raise FiveBandReceiverSmokeError(
            f"{band_id} decoded receiver values must be finite float64."
        )
    if np.any(array < 0.0):
        raise FiveBandReceiverSmokeError(
            f"{band_id} decoded receiver values must be non-negative."
        )
    return array


def compute_five_band_incident_metrics(
    band: RexBandTransportPlan,
    values: Sequence[float] | FloatArray,
    samples: Sequence[MeshPatchReceiverSample],
    *,
    front_indices: NDArray[np.int64] | None = None,
    back_indices: NDArray[np.int64] | None = None,
    area_weights: FloatArray | None = None,
) -> FiveBandIncidentMetrics:
    """Compute explicit front/back incident diagnostics from side metadata."""

    vector = np.asarray(values, dtype=np.float64)
    if vector.shape != (len(samples),) or not np.all(np.isfinite(vector)):
        raise ValueError("incident band PFD must match finite receiver sample rows.")
    if np.any(vector < 0.0):
        raise ValueError("incident band PFD must be non-negative.")
    if front_indices is None or back_indices is None or area_weights is None:
        front_indices, back_indices, area_weights = _receiver_groups(samples)
    return FiveBandIncidentMetrics(
        band_id=band.band_id,
        quantity=(
            "far_red_photon_flux_density"
            if band.band_id == "far_red"
            else f"{band.band_id}_PAR_band_photon_flux_density"
        ),
        units=band.expected_result_units,
        receiver_count=len(vector),
        front_receiver_count=len(front_indices),
        back_receiver_count=len(back_indices),
        minimum_incident_band_pfd=float(vector.min()),
        maximum_incident_band_pfd=float(vector.max()),
        mean_incident_band_pfd=float(vector.mean()),
        front_mean_incident_band_pfd=float(vector[front_indices].mean()),
        back_mean_incident_band_pfd=float(vector[back_indices].mean()),
        area_weighted_mean_incident_band_pfd=float(
            np.average(vector, weights=area_weights)
        ),
    )


def _validate_materialized_plan(
    plan: RexFiveBandTransportPlan,
) -> tuple[MeshPatchReceiverSample, ...]:
    expected_manifest = format_rex_five_band_transport_plan_json(plan)
    required_hashes = (
        (plan.manifest_path, _sha256_text(expected_manifest), "transport plan"),
        (plan.source_model_path, plan.source_model_json_sha256, "source model"),
        (plan.material_plan_path, plan.material_plan_json_sha256, "material plan"),
        (plan.room_path, plan.room_sha256, "room"),
        (plan.receiver_path, plan.receiver_text_sha256, "receivers"),
    )
    for path, expected_hash, label in required_hashes:
        _validate_input_hash(path, expected_hash, label)
    for band in plan.band_plans:
        _validate_input_hash(
            band.paths.emitter_path, band.emitter_text_sha256, f"{band.band_id} emitters"
        )
        _validate_input_hash(
            band.paths.plant_path, band.plant_text_sha256, f"{band.band_id} plant"
        )
        if band.oconv_command.stdout_mode != "binary":
            raise FiveBandReceiverSmokeError(
                f"{band.band_id} oconv stdout must be routed as binary."
            )
        if band.rtrace_command.stdout_mode != "text":
            raise FiveBandReceiverSmokeError(
                f"{band.band_id} rtrace stdout must be routed as text."
            )

    plant = generate_rex_butterhead_plant(RexPlantConfig(seed=plan.plant_seed))
    geometry_hash = _sha256_text(export_plant_mesh_to_radiance(plant))
    if geometry_hash != plan.geometry_sha256:
        raise FiveBandReceiverSmokeError("reconstructed Rex geometry identity mismatch.")
    samples = build_two_sided_patch_receivers(
        plant, normal_offset_m=plan.normal_offset_m
    )
    receiver_text = receiver_sample_input_text(item.to_dict() for item in samples)
    if (
        tuple(item.receiver_id for item in samples) != plan.ordered_receiver_ids
        or _sha256_text(receiver_text) != plan.receiver_text_sha256
    ):
        raise FiveBandReceiverSmokeError(
            "receiver metadata does not match the ordered Phase 18 receiver identity."
        )
    _receiver_groups(samples)
    return samples


def _receiver_groups(
    samples: Sequence[MeshPatchReceiverSample],
) -> tuple[NDArray[np.int64], NDArray[np.int64], FloatArray]:
    if len(samples) != 1024:
        raise FiveBandReceiverSmokeError(
            f"expected 1024 receiver metadata rows, got {len(samples)}."
        )
    front = np.asarray(
        [index for index, sample in enumerate(samples) if sample.side == "front"],
        dtype=np.int64,
    )
    back = np.asarray(
        [index for index, sample in enumerate(samples) if sample.side == "back"],
        dtype=np.int64,
    )
    if front.shape != (512,) or back.shape != (512,):
        raise FiveBandReceiverSmokeError(
            "receiver metadata must contain 512 front and 512 back records."
        )
    for pair_index in range(0, len(samples), 2):
        front_sample = samples[pair_index]
        back_sample = samples[pair_index + 1]
        if (
            front_sample.side != "front"
            or back_sample.side != "back"
            or front_sample.patch_id != back_sample.patch_id
            or not math.isclose(
                front_sample.area_m2,
                back_sample.area_m2,
                rel_tol=0.0,
                abs_tol=0.0,
            )
        ):
            raise FiveBandReceiverSmokeError(
                f"receiver metadata pair {pair_index // 2} is not stable front/back."
            )
    weights = np.asarray([sample.area_m2 for sample in samples], dtype=np.float64)
    if not np.all(np.isfinite(weights)) or np.any(weights <= 0.0):
        raise FiveBandReceiverSmokeError(
            "receiver physical patch areas must be finite and positive."
        )
    return front, back, weights


def _validate_input_hash(path: Path, expected_hash: str, label: str) -> None:
    if not path.is_file():
        raise FiveBandReceiverSmokeError(f"materialized {label} input missing: {path}")
    if _sha256_file(path) != expected_hash:
        raise FiveBandReceiverSmokeError(f"materialized {label} hash mismatch: {path}")


def _prepare_output_paths(plan: RexFiveBandTransportPlan) -> None:
    paths = [plan.output_root / FIVE_BAND_SUMMARY_FILENAME]
    for band in plan.band_plans:
        paths.extend(
            (
                band.paths.octree_path,
                band.paths.rgb_output_path,
                band.paths.decoded_pfd_path,
                band.paths.ambient_cache_path,
            )
        )
    try:
        for path in paths:
            path.unlink(missing_ok=True)
    except OSError as exc:
        raise FiveBandReceiverSmokeError(
            f"could not prepare planned five-band output path: {path}"
        ) from exc


def _require_successful_run(
    band_id: str,
    stage: str,
    command: CommandSpec,
    result: RunnerResult,
) -> None:
    if result.argv != command.argv or result.stdout_path != command.stdout_path:
        raise FiveBandReceiverSmokeError(
            f"{band_id} {stage} runner record does not match its CommandSpec."
        )
    if not result.success or result.returncode != 0:
        detail = result.failure_message or f"{stage} failed"
        raise FiveBandReceiverSmokeError(f"{band_id} native execution stopped: {detail}")


def _require_output(band_id: str, label: str, path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise FiveBandReceiverSmokeError(
            f"{band_id} runner did not materialize planned {label} output: {path}"
        )


def _validate_saved_npy(band_id: str, path: Path, expected: FloatArray) -> None:
    try:
        saved = np.load(path, allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise FiveBandReceiverSmokeError(
            f"{band_id} decoded NPY could not be read safely: {path}"
        ) from exc
    if (
        saved.dtype != np.dtype(np.float64)
        or saved.shape != (1024,)
        or not np.array_equal(saved, expected)
    ):
        raise FiveBandReceiverSmokeError(
            f"{band_id} decoded NPY failed float64 shape or round-trip validation."
        )


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


def _command_payload(command: CommandSpec) -> dict[str, Any]:
    return {
        "argv": list(command.argv),
        "stdin_path": None if command.stdin_path is None else str(command.stdin_path),
        "stdout_path": None if command.stdout_path is None else str(command.stdout_path),
        "stdout_mode": command.stdout_mode,
        "cwd": None if command.cwd is None else str(command.cwd),
        "env": dict(sorted(command.env.items())),
        "label": command.label,
        "shell": False,
    }


def _runner_result_payload(result: RunnerResult) -> dict[str, Any]:
    return {
        "command_label": result.command_label,
        "argv": list(result.argv),
        "returncode": result.returncode,
        "stdout_path": None if result.stdout_path is None else str(result.stdout_path),
        "stderr_text": result.stderr_text,
        "stderr_path": None if result.stderr_path is None else str(result.stderr_path),
        "wall_time_s": result.wall_time_s,
        "success": result.success,
    }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_sha256(name: str, value: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest.")
