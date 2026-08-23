"""Bounded scientific aggregation of immutable juvenile receiver transport.

This module never executes Radiance and never rewrites raw receiver values.  It
derives coefficient-based surface accounting in canonical patch order, writes
fixed-stride binary artifacts, and retains only one patch plus current
compensated accumulators and bounded leaf/plant exposure summaries in memory.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile
from typing import BinaryIO, Callable, Iterable, Iterator, Mapping, Protocol

from fspm_optics.optics.rex_material_plan import (
    RadianceTransParameters,
    reconstruct_radiance_trans,
    render_radiance_trans_material,
)
from fspm_optics.optics.rex_weighting import (
    WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE,
    AtrCoefficients,
)
from fspm_optics.plants.multi_scene import (
    ExpandedJuvenilePatch,
    JuvenileScientificScene,
)
from fspm_optics.plants.radiance_scene_export import (
    DEFAULT_LEAF_MATERIAL_MODIFIER,
)
from fspm_optics.spectral.bands import FIXED_TRANSPORT_BANDS
from fspm_optics.transport.basis.atomic import atomic_write_text

FSPM_AGGREGATION_SCHEMA_ID = "fspm-optics.fspm-surface-light-aggregation"
FSPM_AGGREGATION_SCHEMA_VERSION = 2
FSPM_ROOM_SUMMARY_SCHEMA_ID = "fspm-optics.fspm-surface-light-room-summary"
FSPM_ROOM_SUMMARY_SCHEMA_VERSION = 2
FSPM_DERIVATION_GRAPH_SCHEMA_ID = "fspm-optics.fspm-derivation-graph"
FSPM_DERIVATION_GRAPH_SCHEMA_VERSION = 2

AGGREGATION_ROOT = "fspm-aggregation"
AGGREGATION_METADATA_NAME = "aggregation.v2.json"
PATCH_ARTIFACT_NAME = "patch-surface-light.v2.f64le.bin"
LEAF_ARTIFACT_NAME = "leaf-surface-light.v2.f64le.bin"
PLANT_ARTIFACT_NAME = "plant-surface-light.v2.f64le.bin"
ROOM_SUMMARY_NAME = "room-summary.v2.json"
DERIVATION_GRAPH_NAME = "derivation-graph.v2.json"

BAND_ORDER = tuple(band.band_id for band in FIXED_TRANSPORT_BANDS)
PAR_BAND_ORDER = BAND_ORDER[:4]
SPECTRAL_GROUPS = ("par", "far_red")
SIDES = ("front", "back")
QUANTITIES = ("incident", "absorbed", "transmitted", "reflected")
COEFFICIENT_CLOSURE_ABS_TOLERANCE = WEIGHTED_ATR_CLOSURE_ABS_TOLERANCE
LOCAL_CLOSURE_REL_TOLERANCE = 1e-12
LOCAL_CLOSURE_ABS_TOLERANCE = 1e-9
DEFAULT_RAW_CHUNK_BYTES = 64 * 1024 + 7

PATCH_INDEX_FIELDS = (
    "global_patch_index",
    "plant_index",
    "global_leaf_index",
    "local_patch_index",
)
LEAF_INDEX_FIELDS = (
    "global_leaf_index",
    "plant_index",
    "local_leaf_index",
)
PLANT_INDEX_FIELDS = ("plant_index",)


def spectral_groups_for_band_order(
    band_order: tuple[str, ...],
) -> tuple[str, ...]:
    if band_order == PAR_BAND_ORDER:
        return ("par",)
    if band_order == BAND_ORDER:
        return SPECTRAL_GROUPS
    raise FspmScientificAggregationError(
        "aggregation requires four ordered PAR bands with optional far-red."
    )


def patch_float_fields_for_band_order(
    band_order: tuple[str, ...],
) -> tuple[str, ...]:
    fields = ["physical_one_sided_patch_area_m2"]
    for group in spectral_groups_for_band_order(band_order):
        for side in SIDES:
            for quantity in QUANTITIES:
                fields.append(
                    f"{group}_{side}_{quantity}_photon_flux_density_umol_m2_s"
                )
        for quantity in QUANTITIES:
            fields.append(
                f"{group}_combined_{quantity}_photon_rate_umol_s"
            )
    return tuple(fields)


def summary_float_fields_for_band_order(
    band_order: tuple[str, ...],
) -> tuple[str, ...]:
    fields = ["physical_one_sided_leaf_area_m2"]
    for group in spectral_groups_for_band_order(band_order):
        for side in SIDES:
            for quantity in QUANTITIES:
                fields.append(
                    f"{group}_{side}_{quantity}_photon_flux_density_umol_m2_s"
                )
        for quantity in QUANTITIES:
            fields.append(
                f"{group}_combined_{quantity}_exposure_per_one_sided_area_umol_m2_s"
            )
        for quantity in QUANTITIES:
            fields.append(f"{group}_total_{quantity}_photon_rate_umol_s")
    return tuple(fields)


PATCH_FLOAT_FIELDS = patch_float_fields_for_band_order(BAND_ORDER)
SUMMARY_FLOAT_FIELDS = summary_float_fields_for_band_order(BAND_ORDER)
PATCH_STRUCT = struct.Struct("<QQQQ" + "d" * len(PATCH_FLOAT_FIELDS))
LEAF_STRUCT = struct.Struct("<QQQ" + "d" * len(SUMMARY_FLOAT_FIELDS))
PLANT_STRUCT = struct.Struct("<Q" + "d" * len(SUMMARY_FLOAT_FIELDS))


def binary_contract_for_band_order(
    band_order: tuple[str, ...],
) -> dict[str, tuple[struct.Struct, tuple[str, ...], tuple[str, ...]]]:
    patch_fields = patch_float_fields_for_band_order(band_order)
    summary_fields = summary_float_fields_for_band_order(band_order)
    return {
        "patch": (
            struct.Struct("<QQQQ" + "d" * len(patch_fields)),
            PATCH_INDEX_FIELDS,
            patch_fields,
        ),
        "leaf": (
            struct.Struct("<QQQ" + "d" * len(summary_fields)),
            LEAF_INDEX_FIELDS,
            summary_fields,
        ),
        "plant": (
            struct.Struct("<Q" + "d" * len(summary_fields)),
            PLANT_INDEX_FIELDS,
            summary_fields,
        ),
    }


class FspmScientificAggregationError(RuntimeError):
    """Raw authority, material closure, or aggregation publication failed."""


@dataclass(frozen=True, slots=True)
class ScientificArtifactRecord:
    role: str
    path: str
    media_type: str
    byte_length: int
    sha256: str
    row_count: int | None = None
    stride_bytes: int | None = None

    def __post_init__(self) -> None:
        if (
            not self.role
            or not self.path
            or Path(self.path).is_absolute()
            or ".." in Path(self.path).parts
            or not self.media_type
            or isinstance(self.byte_length, bool)
            or not isinstance(self.byte_length, int)
            or self.byte_length < 0
            or not _valid_sha256(self.sha256)
            or (
                self.row_count is not None
                and (
                    isinstance(self.row_count, bool)
                    or not isinstance(self.row_count, int)
                    or self.row_count <= 0
                )
            )
            or (
                self.stride_bytes is not None
                and (
                    isinstance(self.stride_bytes, bool)
                    or not isinstance(self.stride_bytes, int)
                    or self.stride_bytes <= 0
                )
            )
            or ((self.row_count is None) != (self.stride_bytes is None))
            or (
                self.row_count is not None
                and self.byte_length != self.row_count * self.stride_bytes
            )
        ):
            raise FspmScientificAggregationError(
                "scientific artifact record is invalid."
            )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "role": self.role,
            "path": self.path,
            "media_type": self.media_type,
            "byte_length": self.byte_length,
            "sha256": self.sha256,
        }
        if self.row_count is not None:
            payload["row_count"] = self.row_count
            payload["stride_bytes"] = self.stride_bytes
        return payload


@dataclass(frozen=True, slots=True)
class FspmScientificPublication:
    metadata: Mapping[str, object]
    metadata_artifact: ScientificArtifactRecord
    room_summary: Mapping[str, object]
    public_artifacts: Mapping[str, str]

    def manifest_payload(self) -> dict[str, object]:
        counts = self.metadata.get("counts")
        payload: dict[str, object] = {
            "schema_id": FSPM_AGGREGATION_SCHEMA_ID,
            "schema_version": FSPM_AGGREGATION_SCHEMA_VERSION,
            "metadata_artifact": self.metadata_artifact.to_dict(),
            "public_artifacts": dict(self.public_artifacts),
            "source_state_id": self.metadata.get("source_state_id"),
            "transport_metadata_sha256": self.metadata.get(
                "transport_metadata_sha256"
            ),
            "include_far_red": self.metadata.get("include_far_red"),
            "band_order": self.metadata.get("band_order"),
            "far_red_executed": self.metadata.get("far_red_executed"),
            "counts": counts,
            "aggregation_executed": True,
        }
        if self.metadata.get("spectral_basis") is not None:
            payload["spectral_basis"] = self.metadata["spectral_basis"]
        return payload


@dataclass(frozen=True, slots=True)
class _BandAuthority:
    band_id: str
    raw_path: Path
    raw_record: Mapping[str, object]
    raw_sha256: str
    material_sha256: str
    material_provenance_sha256: str
    absorptance: float
    transmittance: float
    reflectance: float

    def coefficient_payload(self) -> dict[str, object]:
        return {
            "band_id": self.band_id,
            "absorptance": self.absorptance,
            "transmittance": self.transmittance,
            "reflectance": self.reflectance,
            "coefficient_sum": math.fsum(
                (self.absorptance, self.transmittance, self.reflectance)
            ),
            "closure_abs_tolerance": COEFFICIENT_CLOSURE_ABS_TOLERANCE,
            "material_sha256": self.material_sha256,
            "material_provenance_sha256": self.material_provenance_sha256,
            "raw_receiver_sha256": self.raw_sha256,
        }


@dataclass(frozen=True, slots=True)
class _TotalsSnapshot:
    area_m2: float
    side_rates: Mapping[str, Mapping[str, Mapping[str, float]]]


@dataclass(frozen=True, slots=True)
class _DerivationResult:
    room: _TotalsSnapshot
    leaf_absorbed_records: tuple[tuple[int, int, float, float], ...]
    plant_absorbed_records: tuple[tuple[int, float, float], ...]
    leaf_count: int
    plant_count: int
    max_band_density_closure_error: float
    max_band_rate_closure_error: float
    max_group_density_closure_error: float
    max_group_rate_closure_error: float
    max_patch_to_leaf_conservation_error: float
    max_leaf_to_plant_conservation_error: float
    max_plant_to_room_conservation_error: float
    raw_hashes: Mapping[str, str]
    band_order: tuple[str, ...]
    spectral_groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParPatchSurfaceLight:
    """One canonical patch's four display-calibration input densities."""

    global_patch_index: int
    plant_index: int
    global_leaf_index: int
    local_patch_index: int
    physical_one_sided_patch_area_m2: float
    front_incident_photon_flux_density_umol_m2_s: float
    back_incident_photon_flux_density_umol_m2_s: float
    front_absorbed_photon_flux_density_umol_m2_s: float
    back_absorbed_photon_flux_density_umol_m2_s: float


@dataclass(frozen=True, slots=True)
class ParSurfaceLightValidation:
    """Bounded four-band reuse result from the authoritative derivation kernel."""

    counts: Mapping[str, int]
    modeled_physical_one_sided_leaf_area_m2: float
    closure: Mapping[str, float]
    raw_receiver_sha256_by_band: Mapping[str, str]
    material_coefficient_authorities: tuple[Mapping[str, object], ...]
    derivation_digests: Mapping[str, Mapping[str, object]]

    def to_dict(self) -> dict[str, object]:
        return {
            "counts": dict(self.counts),
            "modeled_physical_one_sided_leaf_area_m2": (
                self.modeled_physical_one_sided_leaf_area_m2
            ),
            "closure": dict(self.closure),
            "raw_receiver_sha256_by_band": dict(
                self.raw_receiver_sha256_by_band
            ),
            "material_coefficient_authorities": [
                dict(value) for value in self.material_coefficient_authorities
            ],
            "derivation_digests": {
                name: dict(value)
                for name, value in self.derivation_digests.items()
            },
            "par_band_order": list(PAR_BAND_ORDER),
            "far_red_executed_or_aggregated": False,
            "authoritative_phase27g_c_equations_reused": True,
        }


class _WritableSink(Protocol):
    def write(self, value: bytes) -> None: ...


class CompensatedNonnegativeSum:
    """Deterministic Neumaier summation in canonical identity order."""

    __slots__ = ("_sum", "_correction")

    def __init__(self) -> None:
        self._sum = 0.0
        self._correction = 0.0

    def add(self, value: float) -> None:
        number = _finite_nonnegative("summand", value)
        candidate = self._sum + number
        if abs(self._sum) >= abs(number):
            self._correction += (self._sum - candidate) + number
        else:
            self._correction += (number - candidate) + self._sum
        self._sum = candidate

    @property
    def value(self) -> float:
        result = self._sum + self._correction
        if not math.isfinite(result) or result < 0.0:
            raise FspmScientificAggregationError(
                "compensated aggregation produced an invalid value."
            )
        return result


class _TotalsAccumulator:
    def __init__(self, spectral_groups: tuple[str, ...]) -> None:
        if spectral_groups not in (("par",), SPECTRAL_GROUPS):
            raise FspmScientificAggregationError(
                "aggregation spectral groups are not canonical."
            )
        self.spectral_groups = spectral_groups
        self.area = CompensatedNonnegativeSum()
        self.side_rates = {
            group: {
                side: {
                    quantity: CompensatedNonnegativeSum()
                    for quantity in QUANTITIES
                }
                for side in SIDES
            }
            for group in self.spectral_groups
        }

    def add_patch(
        self,
        area_m2: float,
        densities: Mapping[str, Mapping[str, Mapping[str, float]]],
    ) -> None:
        area = _positive("patch area", area_m2)
        self.area.add(area)
        if tuple(densities) != self.spectral_groups:
            raise FspmScientificAggregationError(
                "patch spectral groups disagree with the accumulator."
            )
        for group in self.spectral_groups:
            for side in SIDES:
                for quantity in QUANTITIES:
                    self.side_rates[group][side][quantity].add(
                        densities[group][side][quantity] * area
                    )

    def add_totals(self, snapshot: _TotalsSnapshot) -> None:
        self.area.add(snapshot.area_m2)
        if tuple(snapshot.side_rates) != self.spectral_groups:
            raise FspmScientificAggregationError(
                "summary spectral groups disagree with the accumulator."
            )
        for group in self.spectral_groups:
            for side in SIDES:
                for quantity in QUANTITIES:
                    self.side_rates[group][side][quantity].add(
                        snapshot.side_rates[group][side][quantity]
                    )

    def snapshot(self) -> _TotalsSnapshot:
        return _TotalsSnapshot(
            area_m2=self.area.value,
            side_rates={
                group: {
                    side: {
                        quantity: self.side_rates[group][side][quantity].value
                        for quantity in QUANTITIES
                    }
                    for side in SIDES
                }
                for group in self.spectral_groups
            },
        )


class _ReceiverPairStream:
    """Stream Float64 front/back pairs across arbitrary byte boundaries."""

    def __init__(
        self,
        path: Path,
        *,
        expected_receiver_count: int,
        expected_sha256: str,
        chunk_bytes: int,
    ) -> None:
        if expected_receiver_count <= 0 or expected_receiver_count % 2:
            raise FspmScientificAggregationError(
                "receiver count must contain complete front/back pairs."
            )
        if chunk_bytes <= 0:
            raise FspmScientificAggregationError(
                "raw aggregation chunk size must be positive."
            )
        self.path = path
        self.expected_receiver_count = expected_receiver_count
        self.expected_sha256 = expected_sha256
        self.chunk_bytes = chunk_bytes
        self.sha256: str | None = None

    def pairs(self) -> Iterator[tuple[float, float]]:
        digest = hashlib.sha256()
        carry = b""
        observed_pairs = 0
        with self.path.open("rb") as handle:
            while True:
                chunk = handle.read(self.chunk_bytes)
                if not chunk:
                    break
                digest.update(chunk)
                data = carry + chunk
                complete = (len(data) // 16) * 16
                for offset in range(0, complete, 16):
                    front, back = struct.unpack_from("<dd", data, offset)
                    _finite_nonnegative("front receiver density", front)
                    _finite_nonnegative("back receiver density", back)
                    observed_pairs += 1
                    yield (front, back)
                carry = data[complete:]
        if carry:
            raise FspmScientificAggregationError(
                "raw receiver artifact ends inside a front/back pair."
            )
        if observed_pairs * 2 != self.expected_receiver_count:
            raise FspmScientificAggregationError(
                "raw receiver count does not match the compact identity."
            )
        self.sha256 = digest.hexdigest()
        if self.sha256 != self.expected_sha256:
            raise FspmScientificAggregationError(
                "raw receiver hash changed during aggregation."
            )


class _AtomicBinaryWriter:
    def __init__(
        self,
        final_path: Path,
        *,
        role: str,
        root: Path,
        stride_bytes: int,
    ) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{final_path.name}.",
            suffix=".tmp",
            dir=final_path.parent,
        )
        self.final_path = final_path
        self.temporary_path = Path(temporary_name)
        self.role = role
        self.root = root
        self.stride_bytes = stride_bytes
        self.handle: BinaryIO = os.fdopen(descriptor, "wb")
        self.digest = hashlib.sha256()
        self.byte_length = 0
        self.row_count = 0
        self.closed = False

    def write(self, value: bytes) -> None:
        if self.closed or len(value) != self.stride_bytes:
            raise FspmScientificAggregationError(
                f"{self.role} binary record has the wrong stride."
            )
        self.handle.write(value)
        self.digest.update(value)
        self.byte_length += len(value)
        self.row_count += 1

    def finish(self) -> ScientificArtifactRecord:
        if not self.closed:
            self.handle.flush()
            os.fsync(self.handle.fileno())
            self.handle.close()
            self.closed = True
        return ScientificArtifactRecord(
            role=self.role,
            path=_relative(self.root, self.final_path),
            media_type="application/octet-stream",
            byte_length=self.byte_length,
            sha256=self.digest.hexdigest(),
            row_count=self.row_count,
            stride_bytes=self.stride_bytes,
        )

    def commit(self) -> None:
        if not self.closed:
            raise FspmScientificAggregationError(
                "binary artifact must be finished before commit."
            )
        os.replace(self.temporary_path, self.final_path)

    def abort(self) -> None:
        if not self.closed:
            self.handle.close()
            self.closed = True
        self.temporary_path.unlink(missing_ok=True)


class _DigestWriter:
    def __init__(self, stride_bytes: int) -> None:
        self.stride_bytes = stride_bytes
        self.digest = hashlib.sha256()
        self.byte_length = 0
        self.row_count = 0

    def write(self, value: bytes) -> None:
        if len(value) != self.stride_bytes:
            raise FspmScientificAggregationError(
                "recomputed binary record has the wrong stride."
            )
        self.digest.update(value)
        self.byte_length += len(value)
        self.row_count += 1


def aggregate_juvenile_surface_light(
    *,
    root: Path,
    run_id: str,
    system_id: str,
    source_state_id: str,
    scene: JuvenileScientificScene,
    transport_metadata_path: str,
    transport_metadata_sha256: str,
    band_records: Iterable[Mapping[str, object]],
    compact_receiver_index: Mapping[str, object],
    compact_receiver_index_sha256: str,
    emitted_par_ppf_umol_s: float,
    modeled_electrical_power_w: float,
    chunk_bytes: int = DEFAULT_RAW_CHUNK_BYTES,
    include_far_red: bool | None = None,
    spectral_basis: Mapping[str, object] | None = None,
) -> FspmScientificPublication:
    """Publish authoritative optical accounting from immutable raw receivers."""

    resolved_root = root.resolve(strict=True)
    emitted_ppf = _positive("emitted PAR PPF", emitted_par_ppf_umol_s)
    electrical_power = _positive(
        "modeled electrical power", modeled_electrical_power_w
    )
    if not _valid_sha256(compact_receiver_index_sha256):
        raise FspmScientificAggregationError(
            "compact receiver index artifact hash is invalid."
        )
    stage_root = resolved_root / AGGREGATION_ROOT
    if _contains_symlink(resolved_root, stage_root) or (
        stage_root.exists()
        and (not stage_root.is_dir() or any(stage_root.iterdir()))
    ):
        raise FspmScientificAggregationError(
            "scientific aggregation root must be absent or empty."
        )
    stage_root.mkdir(parents=True, exist_ok=True)
    records = tuple(band_records)
    if spectral_basis is not None and (
        not isinstance(spectral_basis, Mapping)
        or not isinstance(spectral_basis.get("id"), str)
        or not spectral_basis.get("id")
    ):
        raise FspmScientificAggregationError(
            "spectral basis provenance is incomplete."
        )
    band_order = tuple(
        str(record.get("band_id")) if isinstance(record, Mapping) else ""
        for record in records
    )
    spectral_groups = spectral_groups_for_band_order(band_order)
    resolved_include_far_red = band_order == BAND_ORDER
    if include_far_red is not None and (
        not isinstance(include_far_red, bool)
        or include_far_red is not resolved_include_far_red
    ):
        raise FspmScientificAggregationError(
            "requested far-red state disagrees with executed transport bands."
        )
    authorities = _material_authorities(
        resolved_root,
        scene=scene,
        band_records=records,
        band_order=band_order,
    )
    binary_contract = binary_contract_for_band_order(band_order)
    patch_struct, _patch_indices, patch_fields = binary_contract["patch"]
    leaf_struct, _leaf_indices, summary_fields = binary_contract["leaf"]
    plant_struct, _plant_indices, _plant_fields = binary_contract["plant"]
    patch_writer = _AtomicBinaryWriter(
        stage_root / PATCH_ARTIFACT_NAME,
        role="fspm_patch_surface_light",
        root=resolved_root,
        stride_bytes=patch_struct.size,
    )
    leaf_writer = _AtomicBinaryWriter(
        stage_root / LEAF_ARTIFACT_NAME,
        role="fspm_leaf_surface_light",
        root=resolved_root,
        stride_bytes=leaf_struct.size,
    )
    plant_writer = _AtomicBinaryWriter(
        stage_root / PLANT_ARTIFACT_NAME,
        role="fspm_plant_surface_light",
        root=resolved_root,
        stride_bytes=plant_struct.size,
    )
    writers = (patch_writer, leaf_writer, plant_writer)
    try:
        result = _derive(
            scene,
            authorities,
            patch_writer=patch_writer,
            leaf_writer=leaf_writer,
            plant_writer=plant_writer,
            chunk_bytes=chunk_bytes,
        )
        patch_artifact = patch_writer.finish()
        leaf_artifact = leaf_writer.finish()
        plant_artifact = plant_writer.finish()
        if (
            patch_artifact.row_count != scene.counts.patch_count
            or leaf_artifact.row_count != scene.counts.leaf_count
            or plant_artifact.row_count != scene.counts.plant_count
        ):
            raise FspmScientificAggregationError(
                "aggregation binary row counts do not match the scene."
            )
        for writer in writers:
            writer.commit()
    except BaseException:
        for writer in writers:
            writer.abort()
        raise

    binary_artifacts = (patch_artifact, leaf_artifact, plant_artifact)
    room_summary = _room_summary_payload(
        run_id=run_id,
        system_id=system_id,
        source_state_id=source_state_id,
        scene=scene,
        result=result,
        emitted_par_ppf_umol_s=emitted_ppf,
        modeled_electrical_power_w=electrical_power,
    )
    room_path = stage_root / ROOM_SUMMARY_NAME
    _write_json(room_path, room_summary)
    room_artifact = _json_artifact(
        resolved_root, room_path, role="fspm_room_surface_light_summary"
    )
    graph = _derivation_graph_payload(
        run_id=run_id,
        system_id=system_id,
        source_state_id=source_state_id,
        transport_metadata_path=transport_metadata_path,
        transport_metadata_sha256=transport_metadata_sha256,
        compact_receiver_index=compact_receiver_index,
        compact_receiver_index_sha256=compact_receiver_index_sha256,
        authorities=authorities,
        binary_artifacts=binary_artifacts,
        room_artifact=room_artifact,
    )
    graph_path = stage_root / DERIVATION_GRAPH_NAME
    _write_json(graph_path, graph)
    graph_artifact = _json_artifact(
        resolved_root, graph_path, role="fspm_scientific_derivation_graph"
    )
    ordered = (*binary_artifacts, room_artifact, graph_artifact)
    metadata: dict[str, object] = {
        "schema_id": FSPM_AGGREGATION_SCHEMA_ID,
        "schema_version": FSPM_AGGREGATION_SCHEMA_VERSION,
        "run_id": run_id,
        "system_id": system_id,
        "source_state_id": source_state_id,
        **(
            {}
            if spectral_basis is None
            else {"spectral_basis": dict(spectral_basis)}
        ),
        "transport_metadata": {
            "path": transport_metadata_path,
            "sha256": transport_metadata_sha256,
        },
        "transport_metadata_sha256": transport_metadata_sha256,
        "run_includes_juvenile_fspm_transport": True,
        "aggregation_executed": True,
        "reference_resolution_executes_transport": False,
        "reference_controls_aggregation": False,
        "counts": scene.counts.to_payload(),
        "include_far_red": resolved_include_far_red,
        "band_order": list(band_order),
        "executed_band_order": list(band_order),
        "par_band_order": list(PAR_BAND_ORDER),
        "far_red_executed": resolved_include_far_red,
        "far_red_preserved_separately": True,
        "stage_a_operating_point_authority": {
            "emitted_par_ppf_umol_s": emitted_ppf,
            "modeled_electrical_power_w": electrical_power,
            "emitted_ppf_role": "authenticated completed Stage A operating point",
            "power_role": "authenticated modeled Stage A electrical power",
        },
        "raw_receiver_authorities": [
            dict(authority.raw_record) for authority in authorities
        ],
        "material_coefficient_authorities": [
            authority.coefficient_payload() for authority in authorities
        ],
        "binary_schemas": {
            "patch": _binary_schema(
                patch_struct.size, PATCH_INDEX_FIELDS, patch_fields
            ),
            "leaf": _binary_schema(
                leaf_struct.size, LEAF_INDEX_FIELDS, summary_fields
            ),
            "plant": _binary_schema(
                plant_struct.size, PLANT_INDEX_FIELDS, summary_fields
            ),
        },
        "equations": _equation_payload(),
        "summation_policy": _summation_policy_payload(band_order),
        "conservation_policy": {
            "patch_to_leaf": (
                "each canonical patch contributes exactly once to its current "
                "authoritative leaf accumulator"
            ),
            "leaf_to_plant": (
                "independently compared with direct patch-to-plant sum"
            ),
            "plant_to_room": (
                "independently compared with leaf-to-room and patch-to-room sums"
            ),
            "relative_tolerance": LOCAL_CLOSURE_REL_TOLERANCE,
            "absolute_rate_tolerance_umol_s": LOCAL_CLOSURE_ABS_TOLERANCE,
        },
        "scientific_claim": (
            "coefficient-based immediate surface interaction accounting; "
            "transmitted and reflected rates are not directional outgoing "
            "transport simulations"
        ),
        "scientific_exclusions": {
            "surface_coloring_performed": False,
            "target_scaling_applied": False,
            "display_normalization_applied": False,
            "symmetry_reconstruction_applied": False,
            "per_face_value_duplication_performed": False,
            "growth_or_photosynthesis_modeled": False,
        },
        "memory_complexity": {
            "peak": (
                "Theta(canonical_topology + plant_origins + bounded_raw_chunks "
                "+ current_accumulators + leaf_and_plant_exposure_summaries)"
            ),
            "resident_five_band_arrays": False,
            "expanded_receiver_identity_materialized": False,
            "nested_patch_objects_materialized": False,
        },
        "closure": room_summary["closure"],
        "room_metrics": room_summary["surface_light"],
        "absorbed_par_metrics": room_summary["absorbed_par_metrics"],
        "ordered_artifact_inventory": [item.to_dict() for item in ordered],
        "derivation_graph_sha256": graph_artifact.sha256,
    }
    metadata_path = stage_root / AGGREGATION_METADATA_NAME
    _write_json(metadata_path, metadata)
    metadata_artifact = _json_artifact(
        resolved_root,
        metadata_path,
        role="fspm_scientific_aggregation_metadata",
    )
    public_artifacts = {
        "aggregation_metadata": metadata_artifact.path,
        "patch_surface_light": patch_artifact.path,
        "leaf_surface_light": leaf_artifact.path,
        "plant_surface_light": plant_artifact.path,
        "room_surface_light_summary": room_artifact.path,
        "scientific_derivation_graph": graph_artifact.path,
    }
    return FspmScientificPublication(
        metadata=metadata,
        metadata_artifact=metadata_artifact,
        room_summary=room_summary,
        public_artifacts=public_artifacts,
    )


def recompute_aggregation_digests(
    *,
    root: Path,
    scene: JuvenileScientificScene,
    band_records: Iterable[Mapping[str, object]],
    chunk_bytes: int = DEFAULT_RAW_CHUNK_BYTES,
) -> tuple[_DerivationResult, Mapping[str, Mapping[str, object]]]:
    """Independently recompute deterministic binary identities for promotion."""

    records = tuple(band_records)
    band_order = tuple(
        str(record.get("band_id")) if isinstance(record, Mapping) else ""
        for record in records
    )
    authorities = _material_authorities(
        root.resolve(strict=True),
        scene=scene,
        band_records=records,
        band_order=band_order,
    )
    binary_contract = binary_contract_for_band_order(band_order)
    patch = _DigestWriter(binary_contract["patch"][0].size)
    leaf = _DigestWriter(binary_contract["leaf"][0].size)
    plant = _DigestWriter(binary_contract["plant"][0].size)
    result = _derive(
        scene,
        authorities,
        patch_writer=patch,
        leaf_writer=leaf,
        plant_writer=plant,
        chunk_bytes=chunk_bytes,
    )
    records = {
        "patch": _digest_payload(patch),
        "leaf": _digest_payload(leaf),
        "plant": _digest_payload(plant),
    }
    return result, records


def stream_juvenile_par_surface_light(
    *,
    root: Path,
    scene: JuvenileScientificScene,
    band_records: Iterable[Mapping[str, object]],
    patch_sink: Callable[[ParPatchSurfaceLight], None],
    chunk_bytes: int = DEFAULT_RAW_CHUNK_BYTES,
) -> ParSurfaceLightValidation:
    """Validate and stream four-band PAR through the Phase 27G-C equations.

    The callback receives one patch at a time in canonical identity order.  No
    receiver dictionary, receiver array, or nested patch document is created.
    Far-red is neither required nor synthesized, and no far-red fields are
    present in the four-band derivation records.
    """

    if not callable(patch_sink):
        raise FspmScientificAggregationError("patch_sink must be callable.")
    resolved_root = root.resolve(strict=True)
    authorities = _material_authorities(
        resolved_root,
        scene=scene,
        band_records=tuple(band_records),
        band_order=PAR_BAND_ORDER,
    )
    binary_contract = binary_contract_for_band_order(PAR_BAND_ORDER)
    patch = _DigestWriter(binary_contract["patch"][0].size)
    leaf = _DigestWriter(binary_contract["leaf"][0].size)
    plant = _DigestWriter(binary_contract["plant"][0].size)

    def observe(
        expanded_patch: ExpandedJuvenilePatch,
        densities: Mapping[str, Mapping[str, Mapping[str, float]]],
    ) -> None:
        par = densities["par"]
        patch_sink(
            ParPatchSurfaceLight(
                global_patch_index=expanded_patch.global_patch_index,
                plant_index=expanded_patch.plant_index,
                global_leaf_index=expanded_patch.global_leaf_index,
                local_patch_index=expanded_patch.local_patch_index,
                physical_one_sided_patch_area_m2=expanded_patch.area_m2,
                front_incident_photon_flux_density_umol_m2_s=(
                    par["front"]["incident"]
                ),
                back_incident_photon_flux_density_umol_m2_s=(
                    par["back"]["incident"]
                ),
                front_absorbed_photon_flux_density_umol_m2_s=(
                    par["front"]["absorbed"]
                ),
                back_absorbed_photon_flux_density_umol_m2_s=(
                    par["back"]["absorbed"]
                ),
            )
        )

    result = _derive(
        scene,
        authorities,
        patch_writer=patch,
        leaf_writer=leaf,
        plant_writer=plant,
        chunk_bytes=chunk_bytes,
        patch_observer=observe,
    )
    if (
        patch.row_count != scene.counts.patch_count
        or leaf.row_count != scene.counts.leaf_count
        or plant.row_count != scene.counts.plant_count
    ):
        raise FspmScientificAggregationError(
            "four-band derivation counts do not match the canonical scene."
        )
    return ParSurfaceLightValidation(
        counts=scene.counts.to_payload(),
        modeled_physical_one_sided_leaf_area_m2=result.room.area_m2,
        closure=_closure_payload(result),
        raw_receiver_sha256_by_band=dict(result.raw_hashes),
        material_coefficient_authorities=tuple(
            authority.coefficient_payload() for authority in authorities
        ),
        derivation_digests={
            "patch": _digest_payload(patch),
            "leaf": _digest_payload(leaf),
            "plant": _digest_payload(plant),
        },
    )


def build_room_summary_payload(
    *,
    run_id: str,
    system_id: str,
    source_state_id: str,
    scene: JuvenileScientificScene,
    result: _DerivationResult,
    emitted_par_ppf_umol_s: float,
    modeled_electrical_power_w: float,
) -> dict[str, object]:
    """Build the canonical room summary for independent promotion checks."""

    return _room_summary_payload(
        run_id=run_id,
        system_id=system_id,
        source_state_id=source_state_id,
        scene=scene,
        result=result,
        emitted_par_ppf_umol_s=emitted_par_ppf_umol_s,
        modeled_electrical_power_w=modeled_electrical_power_w,
    )


def _derive(
    scene: JuvenileScientificScene,
    authorities: tuple[_BandAuthority, ...],
    *,
    patch_writer: _WritableSink,
    leaf_writer: _WritableSink,
    plant_writer: _WritableSink,
    chunk_bytes: int,
    patch_observer: Callable[
        [ExpandedJuvenilePatch, Mapping[str, Mapping[str, Mapping[str, float]]]],
        None,
    ]
    | None = None,
) -> _DerivationResult:
    band_order = tuple(authority.band_id for authority in authorities)
    spectral_groups = spectral_groups_for_band_order(band_order)
    binary_contract = binary_contract_for_band_order(band_order)
    patch_struct = binary_contract["patch"][0]
    leaf_struct = binary_contract["leaf"][0]
    plant_struct = binary_contract["plant"][0]
    streams = tuple(
        _ReceiverPairStream(
            authority.raw_path,
            expected_receiver_count=scene.counts.receiver_count,
            expected_sha256=authority.raw_sha256,
            chunk_bytes=chunk_bytes,
        )
        for authority in authorities
    )
    iterators = tuple(iter(stream.pairs()) for stream in streams)
    current_leaf_index: int | None = None
    current_plant_index: int | None = None
    leaf_accumulator: _TotalsAccumulator | None = None
    plant_from_leaves: _TotalsAccumulator | None = None
    plant_from_patches: _TotalsAccumulator | None = None
    room_from_plants = _TotalsAccumulator(spectral_groups)
    room_from_leaves = _TotalsAccumulator(spectral_groups)
    room_from_patches = _TotalsAccumulator(spectral_groups)
    leaf_count = 0
    plant_count = 0
    max_band_density_closure = 0.0
    max_band_rate_closure = 0.0
    max_group_density_closure = 0.0
    max_group_rate_closure = 0.0
    max_patch_leaf_error = 0.0
    max_leaf_plant_error = 0.0
    leaf_absorbed_records: list[tuple[int, int, float, float]] = []
    plant_absorbed_records: list[tuple[int, float, float]] = []

    def finish_leaf() -> None:
        nonlocal leaf_accumulator, leaf_count
        nonlocal max_group_density_closure, max_group_rate_closure
        if (
            leaf_accumulator is None
            or current_leaf_index is None
            or current_plant_index is None
        ):
            return
        snapshot = leaf_accumulator.snapshot()
        density_error, rate_error = _max_snapshot_closure_error(snapshot)
        max_group_density_closure = max(
            max_group_density_closure,
            density_error,
        )
        max_group_rate_closure = max(max_group_rate_closure, rate_error)
        if plant_from_leaves is None:
            raise FspmScientificAggregationError(
                "leaf aggregation lost its parent plant."
            )
        plant_from_leaves.add_totals(snapshot)
        room_from_leaves.add_totals(snapshot)
        leaf_writer.write(
            _pack_summary_record(
                leaf_struct,
                (
                    current_leaf_index,
                    current_plant_index,
                    current_leaf_index % scene.topology.leaf_count,
                ),
                snapshot,
            )
        )
        leaf_absorbed_records.append(
            (
                current_leaf_index,
                current_plant_index,
                snapshot.area_m2,
                _combined_absorbed_par_rate(snapshot),
            )
        )
        leaf_count += 1
        leaf_accumulator = None

    def finish_plant() -> None:
        nonlocal plant_count, max_leaf_plant_error
        nonlocal max_group_density_closure, max_group_rate_closure
        if (
            current_plant_index is None
            or plant_from_leaves is None
            or plant_from_patches is None
        ):
            return
        from_leaves = plant_from_leaves.snapshot()
        from_patches = plant_from_patches.snapshot()
        density_error, rate_error = _max_snapshot_closure_error(from_leaves)
        max_group_density_closure = max(
            max_group_density_closure,
            density_error,
        )
        max_group_rate_closure = max(max_group_rate_closure, rate_error)
        leaf_plant_error = _max_snapshot_difference(from_leaves, from_patches)
        _require_snapshot_conservation(
            from_leaves,
            from_patches,
            leaf_plant_error,
            "patch-to-leaf-to-plant",
        )
        max_leaf_plant_error = max(max_leaf_plant_error, leaf_plant_error)
        room_from_plants.add_totals(from_leaves)
        plant_writer.write(
            _pack_summary_record(
                plant_struct,
                (current_plant_index,),
                from_leaves,
            )
        )
        plant_absorbed_records.append(
            (
                current_plant_index,
                from_leaves.area_m2,
                _combined_absorbed_par_rate(from_leaves),
            )
        )
        plant_count += 1

    observed_patches = 0
    for expected_patch_index, patch in enumerate(scene.iter_patches()):
        if patch.global_patch_index != expected_patch_index:
            raise FspmScientificAggregationError(
                "patch iterator order does not match global patch identity."
            )
        if current_plant_index is None:
            current_plant_index = patch.plant_index
            current_leaf_index = patch.global_leaf_index
            leaf_accumulator = _TotalsAccumulator(spectral_groups)
            plant_from_leaves = _TotalsAccumulator(spectral_groups)
            plant_from_patches = _TotalsAccumulator(spectral_groups)
        elif patch.plant_index != current_plant_index:
            finish_leaf()
            finish_plant()
            current_plant_index = patch.plant_index
            current_leaf_index = patch.global_leaf_index
            leaf_accumulator = _TotalsAccumulator(spectral_groups)
            plant_from_leaves = _TotalsAccumulator(spectral_groups)
            plant_from_patches = _TotalsAccumulator(spectral_groups)
        elif patch.global_leaf_index != current_leaf_index:
            finish_leaf()
            current_leaf_index = patch.global_leaf_index
            leaf_accumulator = _TotalsAccumulator(spectral_groups)

        pairs = []
        for iterator in iterators:
            try:
                pairs.append(next(iterator))
            except StopIteration as exc:
                raise FspmScientificAggregationError(
                    "raw receiver band ended before the patch inventory."
                ) from exc
        densities, diagnostics = _patch_densities(
            authorities,
            tuple(pairs),
            patch.area_m2,
        )
        max_band_density_closure = max(
            max_band_density_closure,
            diagnostics["band_density"],
        )
        max_band_rate_closure = max(
            max_band_rate_closure,
            diagnostics["band_rate"],
        )
        max_group_density_closure = max(
            max_group_density_closure,
            diagnostics["group_density"],
        )
        max_group_rate_closure = max(
            max_group_rate_closure,
            diagnostics["group_rate"],
        )
        patch_writer.write(
            _pack_patch_record(patch, densities, patch_struct)
        )
        if patch_observer is not None:
            patch_observer(patch, densities)
        if (
            leaf_accumulator is None
            or plant_from_patches is None
        ):
            raise FspmScientificAggregationError(
                "patch aggregation state is incomplete."
            )
        leaf_accumulator.add_patch(patch.area_m2, densities)
        plant_from_patches.add_patch(patch.area_m2, densities)
        room_from_patches.add_patch(patch.area_m2, densities)
        observed_patches += 1

    finish_leaf()
    finish_plant()
    if observed_patches != scene.counts.patch_count:
        raise FspmScientificAggregationError(
            "patch aggregation count does not match the scene."
        )
    for iterator in iterators:
        try:
            next(iterator)
        except StopIteration:
            pass
        else:
            raise FspmScientificAggregationError(
                "raw receiver band contains extra patch pairs."
            )
    if any(stream.sha256 is None for stream in streams):
        raise FspmScientificAggregationError(
            "raw receiver hash validation did not complete."
        )
    room_plants = room_from_plants.snapshot()
    room_leaves = room_from_leaves.snapshot()
    room_patches = room_from_patches.snapshot()
    plant_leaf_room_error = _max_snapshot_difference(room_plants, room_leaves)
    plant_patch_room_error = _max_snapshot_difference(room_plants, room_patches)
    _require_snapshot_conservation(
        room_plants,
        room_leaves,
        plant_leaf_room_error,
        "leaf-to-plant-to-room",
    )
    _require_snapshot_conservation(
        room_plants,
        room_patches,
        plant_patch_room_error,
        "patch-to-plant-to-room",
    )
    max_plant_room_error = max(
        plant_leaf_room_error,
        plant_patch_room_error,
    )
    density_error, rate_error = _max_snapshot_closure_error(room_plants)
    max_group_density_closure = max(max_group_density_closure, density_error)
    max_group_rate_closure = max(max_group_rate_closure, rate_error)
    return _DerivationResult(
        room=room_plants,
        leaf_absorbed_records=tuple(leaf_absorbed_records),
        plant_absorbed_records=tuple(plant_absorbed_records),
        leaf_count=leaf_count,
        plant_count=plant_count,
        max_band_density_closure_error=max_band_density_closure,
        max_band_rate_closure_error=max_band_rate_closure,
        max_group_density_closure_error=max_group_density_closure,
        max_group_rate_closure_error=max_group_rate_closure,
        max_patch_to_leaf_conservation_error=max_patch_leaf_error,
        max_leaf_to_plant_conservation_error=max_leaf_plant_error,
        max_plant_to_room_conservation_error=max_plant_room_error,
        raw_hashes={
            authority.band_id: str(stream.sha256)
            for authority, stream in zip(authorities, streams, strict=True)
        },
        band_order=band_order,
        spectral_groups=spectral_groups,
    )


def _patch_densities(
    authorities: tuple[_BandAuthority, ...],
    pairs: tuple[tuple[float, float], ...],
    area_m2: float,
) -> tuple[
    dict[str, dict[str, dict[str, float]]],
    dict[str, float],
]:
    authority_order = tuple(authority.band_id for authority in authorities)
    if (
        authority_order not in (PAR_BAND_ORDER, BAND_ORDER)
        or len(pairs) != len(authorities)
    ):
        raise FspmScientificAggregationError(
            "patch derivation requires four ordered PAR bands with optional far-red."
        )
    area = _positive("patch area", area_m2)
    band_values: dict[str, dict[str, dict[str, float]]] = {}
    max_band_density = 0.0
    max_band_rate = 0.0
    for authority, pair in zip(authorities, pairs, strict=True):
        sides: dict[str, dict[str, float]] = {}
        for side, incident in zip(SIDES, pair, strict=True):
            values = {
                "incident": incident,
                "absorbed": authority.absorptance * incident,
                "transmitted": authority.transmittance * incident,
                "reflected": authority.reflectance * incident,
            }
            closure = abs(
                values["incident"]
                - math.fsum(
                    (
                        values["absorbed"],
                        values["transmitted"],
                        values["reflected"],
                    )
                )
            )
            rate_closure = abs(
                values["incident"] * area
                - math.fsum(
                    (
                        values["absorbed"] * area,
                        values["transmitted"] * area,
                        values["reflected"] * area,
                    )
                )
            )
            _require_closure(values["incident"], closure, "band density")
            _require_closure(values["incident"] * area, rate_closure, "band rate")
            max_band_density = max(max_band_density, closure)
            max_band_rate = max(max_band_rate, rate_closure)
            sides[side] = values
        band_values[authority.band_id] = sides

    groups: dict[str, dict[str, dict[str, float]]] = {}
    max_group_density = 0.0
    max_group_rate = 0.0
    group_bands = [("par", PAR_BAND_ORDER)]
    if authority_order == BAND_ORDER:
        group_bands.append(("far_red", ("far_red",)))
    for group, bands in group_bands:
        groups[group] = {}
        for side in SIDES:
            values = {
                quantity: math.fsum(
                    band_values[band][side][quantity] for band in bands
                )
                for quantity in QUANTITIES
            }
            closure = abs(
                values["incident"]
                - math.fsum(
                    (
                        values["absorbed"],
                        values["transmitted"],
                        values["reflected"],
                    )
                )
            )
            rate_closure = abs(
                values["incident"] * area
                - math.fsum(
                    (
                        values["absorbed"] * area,
                        values["transmitted"] * area,
                        values["reflected"] * area,
                    )
                )
            )
            _require_closure(values["incident"], closure, "group density")
            _require_closure(values["incident"] * area, rate_closure, "group rate")
            max_group_density = max(max_group_density, closure)
            max_group_rate = max(max_group_rate, rate_closure)
            groups[group][side] = values
    return groups, {
        "band_density": max_band_density,
        "band_rate": max_band_rate,
        "group_density": max_group_density,
        "group_rate": max_group_rate,
    }


def _material_authorities(
    root: Path,
    *,
    scene: JuvenileScientificScene,
    band_records: tuple[Mapping[str, object], ...],
    band_order: tuple[str, ...] = BAND_ORDER,
) -> tuple[_BandAuthority, ...]:
    if band_order not in (PAR_BAND_ORDER, BAND_ORDER) or len(
        band_records
    ) != len(band_order):
        raise FspmScientificAggregationError(
            "transport metadata does not contain the required ordered band records."
        )
    authorities: list[_BandAuthority] = []
    for order_index, (band_id, record) in enumerate(
        zip(band_order, band_records, strict=True)
    ):
        if record.get("band_id") != band_id or record.get("order_index") != order_index:
            raise FspmScientificAggregationError(
                "transport band order is incompatible with aggregation."
            )
        material = record.get("material")
        material_sha256 = record.get("material_sha256")
        raw_record = record.get("receiver_values")
        if (
            not isinstance(material, Mapping)
            or not isinstance(material_sha256, str)
            or not _valid_sha256(material_sha256)
            or not isinstance(raw_record, Mapping)
        ):
            raise FspmScientificAggregationError(
                f"{band_id} material or raw authority is missing."
            )
        original = material.get("original_atr")
        arguments = material.get("radiance_arguments")
        reconstruction = material.get("reconstruction")
        if (
            not isinstance(original, Mapping)
            or not isinstance(arguments, Mapping)
            or not isinstance(reconstruction, Mapping)
        ):
            raise FspmScientificAggregationError(
                f"{band_id} material coefficient provenance is incomplete."
            )
        try:
            coefficients = AtrCoefficients.from_dict(original)
            parameters = RadianceTransParameters.from_dict(arguments)
            rebuilt = reconstruct_radiance_trans(parameters)
        except (TypeError, ValueError) as exc:
            raise FspmScientificAggregationError(
                f"{band_id} material coefficient authority is invalid: {exc}"
            ) from exc
        if (
            rebuilt.to_dict() != dict(reconstruction)
            or not math.isclose(
                coefficients.absorptance,
                rebuilt.absorptance,
                rel_tol=0.0,
                abs_tol=COEFFICIENT_CLOSURE_ABS_TOLERANCE,
            )
            or not math.isclose(
                coefficients.transmittance,
                rebuilt.total_transmittance,
                rel_tol=0.0,
                abs_tol=COEFFICIENT_CLOSURE_ABS_TOLERANCE,
            )
            or not math.isclose(
                coefficients.reflectance,
                rebuilt.total_reflectance,
                rel_tol=0.0,
                abs_tol=COEFFICIENT_CLOSURE_ABS_TOLERANCE,
            )
            or not math.isclose(
                coefficients.sum,
                1.0,
                rel_tol=0.0,
                abs_tol=COEFFICIENT_CLOSURE_ABS_TOLERANCE,
            )
        ):
            raise FspmScientificAggregationError(
                f"{band_id} material A/T/R does not match its Radiance mapping."
            )
        material_artifact = record.get("material_artifact")
        if material_artifact is None and band_order in (PAR_BAND_ORDER, BAND_ORDER):
            material_path = (
                root
                / "fspm-transport"
                / "bands"
                / f"{order_index:02d}-{band_id}"
                / "leaf-material.rad"
            )
        elif isinstance(material_artifact, Mapping) and isinstance(
            material_artifact.get("path"), str
        ):
            material_path = root / str(material_artifact["path"])
            if (
                material_artifact.get("sha256") != material_sha256
                or material_artifact.get("byte_length")
                != len(
                    render_radiance_trans_material(
                        DEFAULT_LEAF_MATERIAL_MODIFIER,
                        parameters,
                    ).encode("utf-8")
                )
            ):
                raise FspmScientificAggregationError(
                    f"{band_id} material artifact authority is invalid."
                )
        else:
            raise FspmScientificAggregationError(
                f"{band_id} material artifact authority is missing."
            )
        expected_material_text = render_radiance_trans_material(
            DEFAULT_LEAF_MATERIAL_MODIFIER,
            parameters,
        )
        if (
            _sha256_text(expected_material_text) != material_sha256
            or _contains_symlink(root, material_path)
            or not material_path.is_file()
            or not material_path.resolve().is_relative_to(root)
            or _sha256_file(material_path) != material_sha256
        ):
            raise FspmScientificAggregationError(
                f"{band_id} coefficient/material hash disagreement."
            )
        raw_path_value = raw_record.get("path")
        raw_sha256 = raw_record.get("sha256")
        raw_rows = raw_record.get("row_count")
        raw_length = raw_record.get("byte_length")
        if (
            not isinstance(raw_path_value, str)
            or not isinstance(raw_sha256, str)
            or not _valid_sha256(raw_sha256)
            or raw_rows != scene.counts.receiver_count
            or raw_length != scene.counts.receiver_count * 8
        ):
            raise FspmScientificAggregationError(
                f"{band_id} raw receiver authority is invalid."
            )
        raw_path = root / raw_path_value
        if (
            _contains_symlink(root, raw_path)
            or not raw_path.is_file()
            or not raw_path.resolve().is_relative_to(root)
            or raw_path.stat().st_size != raw_length
        ):
            raise FspmScientificAggregationError(
                f"{band_id} raw receiver artifact is missing or unsafe."
            )
        authorities.append(
            _BandAuthority(
                band_id=band_id,
                raw_path=raw_path,
                raw_record=dict(raw_record),
                raw_sha256=raw_sha256,
                material_sha256=material_sha256,
                material_provenance_sha256=_hash_json(material),
                absorptance=coefficients.absorptance,
                transmittance=coefficients.transmittance,
                reflectance=coefficients.reflectance,
            )
        )
    return tuple(authorities)


def _pack_patch_record(
    patch: object,
    densities: Mapping[str, Mapping[str, Mapping[str, float]]],
    binary_struct: struct.Struct,
) -> bytes:
    values = [float(getattr(patch, "area_m2"))]
    for group in densities:
        for side in SIDES:
            values.extend(densities[group][side][quantity] for quantity in QUANTITIES)
        for quantity in QUANTITIES:
            values.append(
                math.fsum(
                    densities[group][side][quantity]
                    * float(getattr(patch, "area_m2"))
                    for side in SIDES
                )
            )
    return binary_struct.pack(
        int(getattr(patch, "global_patch_index")),
        int(getattr(patch, "plant_index")),
        int(getattr(patch, "global_leaf_index")),
        int(getattr(patch, "local_patch_index")),
        *values,
    )


def _pack_summary_record(
    binary_struct: struct.Struct,
    indices: tuple[int, ...],
    snapshot: _TotalsSnapshot,
) -> bytes:
    values = [snapshot.area_m2]
    for group in snapshot.side_rates:
        for side in SIDES:
            for quantity in QUANTITIES:
                values.append(
                    snapshot.side_rates[group][side][quantity]
                    / snapshot.area_m2
                )
        for quantity in QUANTITIES:
            values.append(
                math.fsum(
                    snapshot.side_rates[group][side][quantity]
                    for side in SIDES
                )
                / snapshot.area_m2
            )
        for quantity in QUANTITIES:
            values.append(
                math.fsum(
                    snapshot.side_rates[group][side][quantity]
                    for side in SIDES
                )
            )
    return binary_struct.pack(*indices, *values)


def _surface_group_summary(
    snapshot: _TotalsSnapshot,
    group: str,
) -> dict[str, object]:
    area = _positive("modeled physical one-sided leaf area", snapshot.area_m2)
    side_payload = {
        side: {
            f"{quantity}_photon_flux_density_umol_m2_s": (
                snapshot.side_rates[group][side][quantity] / area
            )
            for quantity in QUANTITIES
        }
        for side in SIDES
    }
    rates = {
        quantity: math.fsum(
            snapshot.side_rates[group][side][quantity] for side in SIDES
        )
        for quantity in QUANTITIES
    }
    return {
        "front_area_weighted_density": side_payload["front"],
        "back_area_weighted_density": side_payload["back"],
        "combined_exposure_per_physical_one_sided_leaf_area": {
            f"{quantity}_photon_flux_density_umol_m2_s": (
                rates[quantity] / area
            )
            for quantity in QUANTITIES
        },
        "photon_rates_umol_s": {
            f"{quantity}_photon_rate_umol_s": value
            for quantity, value in rates.items()
        },
        "absorbed_fraction_of_incident": (
            None
            if rates["incident"] == 0.0
            else rates["absorbed"] / rates["incident"]
        ),
        "area_denominator": {
            "value_m2": area,
            "meaning": (
                "sum of modeled physical one-sided patch areas; front and back "
                "are incident hemispheres of the same physical area"
            ),
        },
    }


def compute_absorbed_par_metrics(
    *,
    modeled_physical_one_sided_leaf_area_m2: float,
    total_combined_absorbed_par_rate_umol_s: float,
    emitted_par_ppf_umol_s: float,
    modeled_electrical_power_w: float,
    plant_records: Iterable[tuple[int, float, float]],
    leaf_records: Iterable[tuple[int, int, float, float]],
) -> dict[str, object]:
    """Validate identity-owned aggregates and derive Stage B PAR metrics."""

    room_area = _positive(
        "modeled physical one-sided leaf area",
        modeled_physical_one_sided_leaf_area_m2,
    )
    room_rate = _positive(
        "total combined absorbed PAR rate",
        total_combined_absorbed_par_rate_umol_s,
    )
    emitted_ppf = _positive("emitted PAR PPF", emitted_par_ppf_umol_s)
    electrical_power = _positive(
        "modeled electrical power", modeled_electrical_power_w
    )
    plants = tuple(plant_records)
    leaves = tuple(leaf_records)
    if not plants or not leaves:
        raise FspmScientificAggregationError(
            "absorbed-exposure distributions require plants and leaves."
        )

    plant_by_id: dict[int, tuple[float, float]] = {}
    for identity, area_value, rate_value in plants:
        if (
            isinstance(identity, bool)
            or not isinstance(identity, int)
            or identity < 0
            or identity in plant_by_id
        ):
            raise FspmScientificAggregationError(
                "plant absorbed-exposure ownership is incomplete or duplicated."
            )
        plant_by_id[identity] = (
            _positive("plant physical one-sided leaf area", area_value),
            _finite_nonnegative("plant absorbed PAR rate", rate_value),
        )
    if set(plant_by_id) != set(range(len(plants))):
        raise FspmScientificAggregationError(
            "plant absorbed-exposure identities are not complete and canonical."
        )

    leaf_ids: set[int] = set()
    leaf_area_by_plant = {
        identity: CompensatedNonnegativeSum() for identity in plant_by_id
    }
    leaf_rate_by_plant = {
        identity: CompensatedNonnegativeSum() for identity in plant_by_id
    }
    leaf_exposures: list[float] = []
    for identity, plant_identity, area_value, rate_value in leaves:
        if (
            isinstance(identity, bool)
            or not isinstance(identity, int)
            or identity < 0
            or identity in leaf_ids
            or isinstance(plant_identity, bool)
            or not isinstance(plant_identity, int)
            or plant_identity not in plant_by_id
        ):
            raise FspmScientificAggregationError(
                "leaf absorbed-exposure ownership is missing, duplicated, or cross-plant."
            )
        leaf_ids.add(identity)
        area = _positive("leaf physical one-sided area", area_value)
        rate = _finite_nonnegative("leaf absorbed PAR rate", rate_value)
        leaf_area_by_plant[plant_identity].add(area)
        leaf_rate_by_plant[plant_identity].add(rate)
        leaf_exposures.append(rate / area)
    if leaf_ids != set(range(len(leaves))):
        raise FspmScientificAggregationError(
            "leaf absorbed-exposure identities are not complete and canonical."
        )

    for identity, (plant_area, plant_rate) in plant_by_id.items():
        if not _close(leaf_area_by_plant[identity].value, plant_area) or not _close(
            leaf_rate_by_plant[identity].value, plant_rate
        ):
            raise FspmScientificAggregationError(
                "leaf absorbed-exposure ownership does not conserve to its plant."
            )
    total_plant_area = math.fsum(area for area, _rate in plant_by_id.values())
    total_plant_rate = math.fsum(rate for _area, rate in plant_by_id.values())
    if not _close(total_plant_area, room_area) or not _close(
        total_plant_rate, room_rate
    ):
        raise FspmScientificAggregationError(
            "plant absorbed-exposure totals do not conserve to the crop."
        )

    plant_exposures = [rate / area for area, rate in plant_by_id.values()]
    plant_mean, plant_cv = _population_exposure_statistics(
        plant_exposures, "plant"
    )
    _leaf_mean, leaf_cv = _population_exposure_statistics(
        leaf_exposures, "leaf"
    )
    combined_exposure = room_rate / room_area
    return {
        "total_combined_absorbed_par_rate_umol_s": room_rate,
        "combined_absorbed_exposure_umol_m2_s": combined_exposure,
        "absorbed_capture_efficiency_percent": room_rate / emitted_ppf * 100.0,
        "absorbed_par_per_electrical_watt_umol_per_j": (
            room_rate / electrical_power
        ),
        "plant_to_plant_absorbed_exposure_cv_percent": plant_cv,
        "plant_minimum_to_mean_absorbed_exposure_ratio": (
            min(plant_exposures) / plant_mean
        ),
        "leaf_to_leaf_absorbed_exposure_cv_percent": leaf_cv,
        "authorities": {
            "modeled_physical_one_sided_leaf_area_m2": room_area,
            "emitted_par_ppf_umol_s": emitted_ppf,
            "modeled_electrical_power_w": electrical_power,
            "plant_observation_count": len(plant_exposures),
            "leaf_observation_count": len(leaf_exposures),
        },
        "units": {
            "total_combined_absorbed_par_rate_umol_s": "umol/s",
            "combined_absorbed_exposure_umol_m2_s": "umol/m2/s",
            "absorbed_capture_efficiency_percent": "percent",
            "absorbed_par_per_electrical_watt_umol_per_j": "umol/J",
            "plant_to_plant_absorbed_exposure_cv_percent": "percent",
            "plant_minimum_to_mean_absorbed_exposure_ratio": "1",
            "leaf_to_leaf_absorbed_exposure_cv_percent": "percent",
        },
        "method": {
            "combined_sides": "front_plus_back_absorbed_PAR_flux_density",
            "area_weighting": "physical_one_sided_receiver_area_applied_once",
            "crop_total": "sum_of_area_integrated_patch_absorbed_PAR_rates",
            "plant_distribution": "one_area_weighted_exposure_per_plant",
            "leaf_distribution": "one_area_weighted_exposure_per_leaf_instance",
            "variability": "population_standard_deviation_divided_by_mean",
        },
    }


def _room_summary_payload(
    *,
    run_id: str,
    system_id: str,
    source_state_id: str,
    scene: JuvenileScientificScene,
    result: _DerivationResult,
    emitted_par_ppf_umol_s: float,
    modeled_electrical_power_w: float,
) -> dict[str, object]:
    closure = _closure_payload(result)
    absorbed_rate = _combined_absorbed_par_rate(result.room)
    absorbed_metrics = compute_absorbed_par_metrics(
        modeled_physical_one_sided_leaf_area_m2=result.room.area_m2,
        total_combined_absorbed_par_rate_umol_s=absorbed_rate,
        emitted_par_ppf_umol_s=emitted_par_ppf_umol_s,
        modeled_electrical_power_w=modeled_electrical_power_w,
        plant_records=result.plant_absorbed_records,
        leaf_records=result.leaf_absorbed_records,
    )
    par_summary = _surface_group_summary(result.room, "par")
    par_exposure = par_summary[
        "combined_exposure_per_physical_one_sided_leaf_area"
    ]
    if not isinstance(par_exposure, Mapping) or not _close(
        absorbed_metrics["combined_absorbed_exposure_umol_m2_s"],
        par_exposure["absorbed_photon_flux_density_umol_m2_s"],
    ):
        raise FspmScientificAggregationError(
            "combined absorbed exposure disagrees with integrated absorbed PAR."
        )
    return {
        "schema_id": FSPM_ROOM_SUMMARY_SCHEMA_ID,
        "schema_version": FSPM_ROOM_SUMMARY_SCHEMA_VERSION,
        "run_id": run_id,
        "system_id": system_id,
        "source_state_id": source_state_id,
        "scope": "all modeled juvenile plant surfaces; not room-floor totals",
        "counts": scene.counts.to_payload(),
        "modeled_physical_one_sided_leaf_area_m2": result.room.area_m2,
        "include_far_red": result.band_order == BAND_ORDER,
        "band_order": list(result.band_order),
        "executed_band_order": list(result.band_order),
        "par_band_order": list(PAR_BAND_ORDER),
        "far_red_executed": result.band_order == BAND_ORDER,
        "surface_light": {
            group: (
                par_summary
                if group == "par"
                else _surface_group_summary(result.room, group)
            )
            for group in result.spectral_groups
        },
        "absorbed_par_metrics": absorbed_metrics,
        "closure": closure,
        "raw_receiver_sha256_by_band": dict(result.raw_hashes),
        "target_or_reference_cap_applied": False,
        "symmetry_reconstruction_applied": False,
    }


def _closure_payload(result: _DerivationResult) -> dict[str, float]:
    return {
        "coefficient_abs_tolerance": COEFFICIENT_CLOSURE_ABS_TOLERANCE,
        "local_rel_tolerance": LOCAL_CLOSURE_REL_TOLERANCE,
        "local_abs_tolerance": LOCAL_CLOSURE_ABS_TOLERANCE,
        "maximum_band_density_closure_error_umol_m2_s": (
            result.max_band_density_closure_error
        ),
        "maximum_band_rate_closure_error_umol_s": (
            result.max_band_rate_closure_error
        ),
        "maximum_par_or_far_red_density_closure_error_umol_m2_s": (
            result.max_group_density_closure_error
        ),
        "maximum_par_or_far_red_rate_closure_error_umol_s": (
            result.max_group_rate_closure_error
        ),
        "maximum_patch_to_leaf_conservation_error_umol_s": (
            result.max_patch_to_leaf_conservation_error
        ),
        "maximum_leaf_to_plant_conservation_error_umol_s": (
            result.max_leaf_to_plant_conservation_error
        ),
        "maximum_plant_to_room_conservation_error_umol_s": (
            result.max_plant_to_room_conservation_error
        ),
    }


def _derivation_graph_payload(
    *,
    run_id: str,
    system_id: str,
    source_state_id: str,
    transport_metadata_path: str,
    transport_metadata_sha256: str,
    compact_receiver_index: Mapping[str, object],
    compact_receiver_index_sha256: str,
    authorities: tuple[_BandAuthority, ...],
    binary_artifacts: tuple[ScientificArtifactRecord, ...],
    room_artifact: ScientificArtifactRecord,
) -> dict[str, object]:
    return {
        "schema_id": FSPM_DERIVATION_GRAPH_SCHEMA_ID,
        "schema_version": FSPM_DERIVATION_GRAPH_SCHEMA_VERSION,
        "run_id": run_id,
        "system_id": system_id,
        "source_state_id": source_state_id,
        "authorities": {
            "transport_metadata": {
                "path": transport_metadata_path,
                "sha256": transport_metadata_sha256,
            },
            "compact_receiver_index": {
                "artifact_sha256": compact_receiver_index_sha256,
                "canonical_payload_sha256": _hash_json(
                    compact_receiver_index
                ),
            },
            "raw_bands": [
                {
                    "band_id": authority.band_id,
                    "artifact": dict(authority.raw_record),
                    "material_sha256": authority.material_sha256,
                    "material_provenance_sha256": (
                        authority.material_provenance_sha256
                    ),
                }
                for authority in authorities
            ],
        },
        "band_order": [authority.band_id for authority in authorities],
        "far_red_executed": any(
            authority.band_id == "far_red" for authority in authorities
        ),
        "ordered_derivation": [
            "raw_receiver_side_density",
            "coefficient_partition_per_band_and_side",
            "physical_patch_rate_with_area_applied_once_per_side",
            "four_band_par_sum_with_far_red_separate",
            "canonical_patch_to_leaf_sum",
            "canonical_leaf_to_plant_sum",
            "canonical_plant_to_modeled_surface_room_sum",
        ],
        "outputs": [
            *(artifact.to_dict() for artifact in binary_artifacts),
            room_artifact.to_dict(),
        ],
        "prohibited_transforms": {
            "spectral_fraction_reapplication": False,
            "source_dimming_reapplication": False,
            "post_trace_179_conversion": False,
            "target_scaling": False,
            "display_normalization": False,
            "symmetry_reconstruction": False,
        },
    }


def _equation_payload() -> dict[str, object]:
    return {
        "per_side_density": {
            "incident": "I_bqs",
            "absorbed": "alpha_b * I_bqs",
            "transmitted": "tau_b * I_bqs",
            "reflected": "rho_b * I_bqs",
        },
        "per_side_rate": "quantity_density_bqs * physical_one_sided_patch_area_q",
        "combined_patch_rate": "front_rate + back_rate",
        "combined_incident_density": "front_density + back_density",
        "physical_area_application_count_per_side": 1,
        "par": "blue + green + orange + red",
        "far_red_combined_with_par": False,
        "higher_level_density": (
            "sum(side_density * physical_one_sided_patch_area) / "
            "sum(physical_one_sided_patch_area)"
        ),
    }


def _summation_policy_payload(
    band_order: tuple[str, ...],
) -> dict[str, object]:
    return {
        "identity_order": (
            "plant-major, canonical leaf, canonical patch, front then back, "
            + "/".join(band_order)
        ),
        "within_patch_band_sum": "math.fsum in canonical band order",
        "hierarchical_sum": "Neumaier compensated sum in canonical identity order",
        "density_aggregation": "area-weighted only",
        "naive_density_sum_or_mean": False,
    }


def _binary_schema(
    stride_bytes: int,
    index_fields: tuple[str, ...],
    float_fields: tuple[str, ...],
) -> dict[str, object]:
    return {
        "byte_order": "little-endian",
        "stride_bytes": stride_bytes,
        "index_component_type": "uint64",
        "index_fields": list(index_fields),
        "index_field_offsets_bytes": {
            field: index * 8 for index, field in enumerate(index_fields)
        },
        "value_component_type": "float64",
        "value_fields": list(float_fields),
        "value_field_offsets_bytes": {
            field: (len(index_fields) + index) * 8
            for index, field in enumerate(float_fields)
        },
        "record_order": "canonical global identity order",
    }


def _max_snapshot_difference(
    left: _TotalsSnapshot,
    right: _TotalsSnapshot,
) -> float:
    if not math.isclose(
        left.area_m2,
        right.area_m2,
        rel_tol=LOCAL_CLOSURE_REL_TOLERANCE,
        abs_tol=1e-15,
    ):
        raise FspmScientificAggregationError(
            "hierarchical aggregation changed modeled physical area."
        )
    errors: list[float] = []
    if tuple(left.side_rates) != tuple(right.side_rates):
        raise FspmScientificAggregationError(
            "hierarchical aggregation changed spectral groups."
        )
    for group in left.side_rates:
        for side in SIDES:
            for quantity in QUANTITIES:
                errors.append(
                    abs(
                        left.side_rates[group][side][quantity]
                        - right.side_rates[group][side][quantity]
                    )
                )
    return max(errors)


def _max_snapshot_closure_error(
    snapshot: _TotalsSnapshot,
) -> tuple[float, float]:
    area = _positive("aggregation closure area", snapshot.area_m2)
    density_errors: list[float] = []
    rate_errors: list[float] = []
    for group in snapshot.side_rates:
        for side in SIDES:
            rates = snapshot.side_rates[group][side]
            rate_error = abs(
                rates["incident"]
                - math.fsum(
                    (
                        rates["absorbed"],
                        rates["transmitted"],
                        rates["reflected"],
                    )
                )
            )
            density_error = rate_error / area
            _require_closure(rates["incident"], rate_error, "aggregate rate")
            _require_closure(
                rates["incident"] / area,
                density_error,
                "aggregate density",
            )
            rate_errors.append(rate_error)
            density_errors.append(density_error)
    return max(density_errors), max(rate_errors)


def _require_snapshot_conservation(
    left: _TotalsSnapshot,
    right: _TotalsSnapshot,
    error: float,
    label: str,
) -> None:
    reference = max(
        value
        for snapshot in (left, right)
        for group in snapshot.side_rates
        for side in SIDES
        for value in snapshot.side_rates[group][side].values()
    )
    if error > max(
        LOCAL_CLOSURE_ABS_TOLERANCE,
        reference * LOCAL_CLOSURE_REL_TOLERANCE,
    ):
        raise FspmScientificAggregationError(
            f"{label} aggregation does not conserve photon rate."
        )


def _require_closure(reference: float, error: float, label: str) -> None:
    if error > max(
        LOCAL_CLOSURE_ABS_TOLERANCE,
        abs(reference) * LOCAL_CLOSURE_REL_TOLERANCE,
    ):
        raise FspmScientificAggregationError(
            f"{label} does not satisfy incident = absorbed + transmitted + reflected."
        )


def _digest_payload(writer: _DigestWriter) -> dict[str, object]:
    return {
        "sha256": writer.digest.hexdigest(),
        "byte_length": writer.byte_length,
        "row_count": writer.row_count,
        "stride_bytes": writer.stride_bytes,
    }


def _json_artifact(
    root: Path,
    path: Path,
    *,
    role: str,
) -> ScientificArtifactRecord:
    return ScientificArtifactRecord(
        role=role,
        path=_relative(root, path),
        media_type="application/json",
        byte_length=path.stat().st_size,
        sha256=_sha256_file(path),
    )


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    atomic_write_text(
        path,
        json.dumps(dict(payload), indent=2, sort_keys=True, allow_nan=False) + "\n",
    )


def _relative(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise FspmScientificAggregationError(
            "scientific artifact escaped the run root."
        ) from exc
    if not relative.parts or ".." in relative.parts:
        raise FspmScientificAggregationError(
            "scientific artifact path is unsafe."
        )
    return relative.as_posix()


def _finite_nonnegative(name: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise FspmScientificAggregationError(
            f"{name} must be finite and non-negative."
        )
    return float(value)


def _positive(name: str, value: object) -> float:
    number = _finite_nonnegative(name, value)
    if number <= 0.0:
        raise FspmScientificAggregationError(f"{name} must be positive.")
    return number


def _combined_absorbed_par_rate(snapshot: _TotalsSnapshot) -> float:
    try:
        value = math.fsum(
            snapshot.side_rates["par"][side]["absorbed"] for side in SIDES
        )
    except (KeyError, TypeError) as exc:
        raise FspmScientificAggregationError(
            "PAR absorbed-rate authority is incomplete."
        ) from exc
    return _finite_nonnegative("combined absorbed PAR rate", value)


def _population_exposure_statistics(
    values: Iterable[float], label: str
) -> tuple[float, float]:
    observations = tuple(
        _finite_nonnegative(f"{label} absorbed exposure", value)
        for value in values
    )
    if not observations:
        raise FspmScientificAggregationError(
            f"{label} absorbed-exposure distribution is empty."
        )
    mean = math.fsum(observations) / len(observations)
    if not math.isfinite(mean) or mean <= 0.0:
        raise FspmScientificAggregationError(
            f"{label} mean absorbed exposure must be positive."
        )
    variance = math.fsum((value - mean) ** 2 for value in observations) / len(
        observations
    )
    standard_deviation = math.sqrt(variance)
    return mean, standard_deviation / mean * 100.0


def _close(left: object, right: object) -> bool:
    left_number = _finite_nonnegative("conservation value", left)
    right_number = _finite_nonnegative("conservation value", right)
    return math.isclose(
        left_number,
        right_number,
        rel_tol=LOCAL_CLOSURE_REL_TOLERANCE,
        abs_tol=LOCAL_CLOSURE_ABS_TOLERANCE,
    )


def _hash_json(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_symlink(root: Path, path: Path) -> bool:
    resolved_root = root.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = resolved_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


__all__ = [
    "AGGREGATION_METADATA_NAME",
    "AGGREGATION_ROOT",
    "BAND_ORDER",
    "COEFFICIENT_CLOSURE_ABS_TOLERANCE",
    "CompensatedNonnegativeSum",
    "DEFAULT_RAW_CHUNK_BYTES",
    "DERIVATION_GRAPH_NAME",
    "FSPM_AGGREGATION_SCHEMA_ID",
    "FSPM_AGGREGATION_SCHEMA_VERSION",
    "FSPM_DERIVATION_GRAPH_SCHEMA_ID",
    "FSPM_DERIVATION_GRAPH_SCHEMA_VERSION",
    "FSPM_ROOM_SUMMARY_SCHEMA_ID",
    "FSPM_ROOM_SUMMARY_SCHEMA_VERSION",
    "FspmScientificAggregationError",
    "FspmScientificPublication",
    "LEAF_ARTIFACT_NAME",
    "LEAF_INDEX_FIELDS",
    "LEAF_STRUCT",
    "LOCAL_CLOSURE_ABS_TOLERANCE",
    "LOCAL_CLOSURE_REL_TOLERANCE",
    "PAR_BAND_ORDER",
    "ParPatchSurfaceLight",
    "ParSurfaceLightValidation",
    "PATCH_ARTIFACT_NAME",
    "PATCH_FLOAT_FIELDS",
    "PATCH_INDEX_FIELDS",
    "PATCH_STRUCT",
    "PLANT_ARTIFACT_NAME",
    "PLANT_INDEX_FIELDS",
    "PLANT_STRUCT",
    "ROOM_SUMMARY_NAME",
    "SUMMARY_FLOAT_FIELDS",
    "ScientificArtifactRecord",
    "aggregate_juvenile_surface_light",
    "binary_contract_for_band_order",
    "build_room_summary_payload",
    "compute_absorbed_par_metrics",
    "patch_float_fields_for_band_order",
    "recompute_aggregation_digests",
    "spectral_groups_for_band_order",
    "stream_juvenile_par_surface_light",
    "summary_float_fields_for_band_order",
]
