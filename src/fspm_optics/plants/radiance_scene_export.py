"""Source-neutral, bounded-memory Radiance export for juvenile plant scenes."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
from typing import Iterable, Iterator, Mapping

from fspm_optics.plants.multi_scene import (
    JUVENILE_SCIENTIFIC_COORDINATE_SYSTEM,
    ExpandedJuvenileFace,
    ExpandedJuvenileReceiver,
    JuvenileScientificScene,
)
from fspm_optics.plants.rex_juvenile import (
    REX_JUVENILE_PREHEADING_PROFILE_ID,
    REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS,
)

JUVENILE_RADIANCE_EXPORT_SCHEMA_ID = (
    "fspm-optics.juvenile-radiance-export"
)
JUVENILE_RADIANCE_EXPORT_SCHEMA_VERSION = 2
JUVENILE_RADIANCE_EXPORTER_ID = "fspm_optics_juvenile_radiance_export_v2"
DEFAULT_LEAF_MATERIAL_MODIFIER = "fspm_leaf_material_slot"
PLANT_GEOMETRY_LOGICAL_NAME = "plant-geometry.rad"
RECEIVER_INPUT_LOGICAL_NAME = "receivers.pts"
PLANT_ORIGINS_LOGICAL_NAME = "plant-origins.v1.f64le.bin"
COMPACT_RECEIVER_INDEX_LOGICAL_NAME = "receiver-index.v1.json"
EXPORT_MANIFEST_LOGICAL_NAME = "export-manifest.v2.json"
COMPACT_RECEIVER_INDEX_SCHEMA_ID = (
    "fspm-optics.juvenile-compact-receiver-index"
)
COMPACT_RECEIVER_INDEX_SCHEMA_VERSION = 1
PLANT_ORIGIN_COMPONENT_TYPE = "float64"
PLANT_ORIGIN_BYTE_ORDER = "little-endian"
PLANT_ORIGIN_STRIDE_BYTES = 24
PLANT_ORIGIN_FIELDS = ("origin_x_m", "origin_y_m", "origin_z_m")
GEOMETRY_ORDERING = "plant-major; canonical-face order within each plant"
RECEIVER_ORDERING = (
    "global-receiver order; canonical front then back within each patch"
)
RECEIVER_INPUT_SHAPE = "x y z nx ny nz"
NUMERIC_FORMAT = ".17g round-trip-safe finite decimal"

_SAFE_RADIANCE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


class JuvenileRadianceExportError(ValueError):
    """The scientific scene cannot be exported without violating its contract."""


@dataclass(frozen=True, slots=True)
class JuvenileRadianceGeometryRecord:
    """One exact Phase 26G triangle and its globally unique Radiance identity."""

    global_face_index: int
    plant_index: int
    plant_id: str
    global_leaf_index: int
    canonical_leaf_id: str
    primitive_id: str
    canonical_face_id: str
    modifier: str
    vertex_indices: tuple[int, int, int]
    vertices: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]
    unit_normal: tuple[float, float, float]
    area_m2: float


@dataclass(frozen=True, slots=True)
class JuvenileRadianceReceiverRecord:
    """One native trace row plus complete scientific receiver identity."""

    row_index: int
    global_receiver_index: int
    local_receiver_index: int
    receiver_id: str
    canonical_receiver_id: str
    plant_index: int
    plant_id: str
    local_leaf_index: int
    global_leaf_index: int
    leaf_id: str
    canonical_leaf_id: str
    leaf_rank: int
    leaf_layer: str
    local_patch_index: int
    global_patch_index: int
    patch_id: str
    canonical_patch_id: str
    face_id: str
    canonical_face_id: str
    side: str
    point_m: tuple[float, float, float]
    normal: tuple[float, float, float]
    area_m2: float
    normal_offset_m: float
    sampling_profile_id: str | None
    surface_anchor_m: tuple[float, float, float] | None
    carrier_face_id: str | None
    canonical_carrier_face_id: str | None
    carrier_barycentric: tuple[float, float, float] | None
    normal_generation_basis_id: str

    def identity_payload(self) -> dict[str, object]:
        """Return a path-free row mapping suitable for streamed JSON output."""

        payload: dict[str, object] = {
            "row_index": self.row_index,
            "global_receiver_index": self.global_receiver_index,
            "receiver": {
                "id": self.receiver_id,
                "canonical_id": self.canonical_receiver_id,
                "local_index": self.local_receiver_index,
                "side": self.side,
            },
            "plant": {"id": self.plant_id, "index": self.plant_index},
            "leaf": {
                "id": self.leaf_id,
                "canonical_id": self.canonical_leaf_id,
                "local_index": self.local_leaf_index,
                "global_index": self.global_leaf_index,
                "rank": self.leaf_rank,
                "layer": self.leaf_layer,
            },
            "patch": {
                "id": self.patch_id,
                "canonical_id": self.canonical_patch_id,
                "local_index": self.local_patch_index,
                "global_index": self.global_patch_index,
            },
            "face": {
                "id": self.face_id,
                "canonical_id": self.canonical_face_id,
            },
            "point_m": list(self.point_m),
            "normal": list(self.normal),
            "area_m2": self.area_m2,
            "normal_offset_m": self.normal_offset_m,
            "sampling_profile_id": self.sampling_profile_id,
            "normal_generation_basis": self.normal_generation_basis_id,
        }
        if self.surface_anchor_m is not None:
            payload["carrier"] = {
                "surface_anchor_m": list(self.surface_anchor_m),
                "face_id": self.carrier_face_id,
                "canonical_face_id": self.canonical_carrier_face_id,
                "barycentric": list(self.carrier_barycentric or ()),
            }
        return payload


@dataclass(frozen=True, slots=True)
class JuvenileRadianceArtifactMetadata:
    """Path-free identity and integrity metadata for one serialized artifact."""

    role: str
    logical_name: str
    media_type: str
    byte_length: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            not self.role
            or not self.logical_name
            or "/" in self.logical_name
            or "\\" in self.logical_name
            or not self.media_type
            or isinstance(self.byte_length, bool)
            or not isinstance(self.byte_length, int)
            or self.byte_length < 0
            or not _valid_sha256(self.sha256)
        ):
            raise JuvenileRadianceExportError("artifact metadata is invalid.")

    def to_payload(self) -> dict[str, object]:
        return {
            "logical_name": self.logical_name,
            "media_type": self.media_type,
            "byte_length": self.byte_length,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class JuvenileRadianceExportPlan:
    """Immutable export contract retaining the compact Phase 26G scene."""

    scene: JuvenileScientificScene = field(repr=False, compare=True)
    leaf_material_modifier: str = DEFAULT_LEAF_MATERIAL_MODIFIER
    export_plan_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_scene(self.scene)
        modifier = _safe_radiance_id(
            self.leaf_material_modifier,
            label="leaf material modifier",
        )
        object.__setattr__(self, "leaf_material_modifier", modifier)
        object.__setattr__(
            self,
            "export_plan_hash",
            _hash_payload(self.identity_payload()),
        )

    @property
    def geometry_count(self) -> int:
        return self.scene.counts.face_count

    @property
    def receiver_count(self) -> int:
        return self.scene.counts.receiver_count

    def identity_payload(self) -> dict[str, object]:
        """Return the canonical, path-free inputs to ``export_plan_hash``."""

        return {
            "schema_id": JUVENILE_RADIANCE_EXPORT_SCHEMA_ID,
            "schema_version": JUVENILE_RADIANCE_EXPORT_SCHEMA_VERSION,
            "exporter_id": JUVENILE_RADIANCE_EXPORTER_ID,
            "scene": {
                "scene_id": self.scene.scene_id,
                "scene_hash": self.scene.scene_hash,
                "profile_id": self.scene.profile_id,
                "sampling_profile_id": self.scene.sampling_profile_id,
                "layout_schema_id": self.scene.layout_schema_id,
                "layout_policy_id": self.scene.layout_policy_id,
                "layout_plan_hash": self.scene.layout_plan_hash,
            },
            "coordinate_system": self.scene.coordinate_system,
            "ordering": {
                "geometry": GEOMETRY_ORDERING,
                "receivers": RECEIVER_ORDERING,
                "trace_row_mapping": (
                    "trace row i equals global receiver index i"
                ),
            },
            "numeric_format": NUMERIC_FORMAT,
            "receiver_input_shape": RECEIVER_INPUT_SHAPE,
            "counts": self.scene.counts.to_payload(),
            "leaf_material": {
                "modifier_slot": self.leaf_material_modifier,
                "definition_embedded": False,
                "binding": "external orchestration must define this modifier",
            },
            "artifacts": _planned_artifact_payload(),
            "manifest": {
                "logical_name": EXPORT_MANIFEST_LOGICAL_NAME,
                "media_type": "application/json",
            },
        }

    def iter_geometry_records(self) -> Iterator[JuvenileRadianceGeometryRecord]:
        """Yield validated geometry without collecting expanded scene faces."""

        observed = 0
        for expected_index, face in enumerate(self.scene.iter_faces()):
            record = _geometry_record(face, self.leaf_material_modifier)
            expected_plant_index, expected_local_index = divmod(
                expected_index,
                self.scene.topology.face_count,
            )
            if (
                record.global_face_index != expected_index
                or record.plant_index != expected_plant_index
                or face.local_face_index != expected_local_index
            ):
                raise JuvenileRadianceExportError(
                    "geometry order does not match global face identity."
                )
            observed += 1
            yield record
        if observed != self.geometry_count:
            raise JuvenileRadianceExportError(
                "geometry iterator count does not match the scene."
            )

    def iter_geometry_bytes(self) -> Iterator[bytes]:
        """Yield one UTF-8 Radiance polygon chunk per expanded triangle."""

        for record in self.iter_geometry_records():
            yield _geometry_bytes(record)

    def iter_receiver_records(self) -> Iterator[JuvenileRadianceReceiverRecord]:
        """Yield validated native rows in exact global receiver order."""

        observed = 0
        for expected_index, receiver in enumerate(self.scene.iter_receivers()):
            if not 0 <= receiver.local_leaf_index < self.scene.topology.leaf_count:
                raise JuvenileRadianceExportError(
                    "receiver leaf identity is outside the canonical topology."
                )
            canonical_leaf = self.scene.canonical_plant.leaves[
                receiver.local_leaf_index
            ]
            if canonical_leaf.leaf_id != receiver.canonical_leaf_id:
                raise JuvenileRadianceExportError(
                    "receiver leaf identity does not match the canonical topology."
                )
            record = _receiver_record(
                receiver,
                leaf_rank=canonical_leaf.leaf_rank,
                leaf_layer=canonical_leaf.leaf_layer,
            )
            expected_plant_index, expected_local_index = divmod(
                expected_index,
                self.scene.topology.receiver_count,
            )
            if (
                record.row_index != expected_index
                or record.global_receiver_index != expected_index
                or record.plant_index != expected_plant_index
                or record.local_receiver_index != expected_local_index
                or record.global_patch_index
                != (
                    expected_plant_index * self.scene.topology.patch_count
                    + expected_local_index // 2
                )
            ):
                raise JuvenileRadianceExportError(
                    "receiver row order does not match global receiver identity."
                )
            observed += 1
            yield record
        if observed != self.receiver_count:
            raise JuvenileRadianceExportError(
                "receiver iterator count does not match the scene."
            )

    def iter_receiver_input_bytes(self) -> Iterator[bytes]:
        """Yield one six-column native trace row at a time."""

        for record in self.iter_receiver_records():
            values = (*record.point_m, *record.normal)
            yield (" ".join(_format_number(value) for value in values) + "\n").encode(
                "ascii"
            )

    def iter_receiver_identity_bytes(self) -> Iterator[bytes]:
        """Yield the legacy expanded identity for small equivalence tests only.

        New run publication never materializes this representation.  It remains
        available so tests can prove that the compact index reconstructs every
        legacy row without approximate spatial matching.
        """

        prefix = {
            "schema_id": f"{JUVENILE_RADIANCE_EXPORT_SCHEMA_ID}.receiver-identity",
            "schema_version": 1,
            "export_plan_hash": self.export_plan_hash,
            "scene_id": self.scene.scene_id,
            "scene_hash": self.scene.scene_hash,
            "layout_plan_hash": self.scene.layout_plan_hash,
            "profile_id": self.scene.profile_id,
            "sampling_profile_id": self.scene.sampling_profile_id,
            "receiver_count": self.receiver_count,
            "ordering": RECEIVER_ORDERING,
            "trace_row_mapping": "trace row i equals global receiver index i",
        }
        prefix_text = _canonical_json(prefix)
        yield (prefix_text[:-1] + ',"receivers":[').encode("utf-8")
        for index, record in enumerate(self.iter_receiver_records()):
            if index:
                yield b","
            yield _canonical_json(record.identity_payload()).encode("utf-8")
        yield b"]}\n"

    def iter_plant_origin_bytes(self) -> Iterator[bytes]:
        """Yield one exact little-endian XYZ translation per plant."""

        for expected_index, plant in enumerate(self.scene.plants):
            if plant.plant_index != expected_index:
                raise JuvenileRadianceExportError(
                    "plant origin order does not match plant identity."
                )
            yield struct.pack("<ddd", *plant.origin_m)

    def compact_receiver_index_payload(
        self,
        artifacts: Iterable[JuvenileRadianceArtifactMetadata],
    ) -> dict[str, object]:
        """Describe exact receiver-row reconstruction without expanded rows."""

        by_role = {artifact.role: artifact for artifact in artifacts}
        required = {"receiver_input", "plant_origins"}
        if not required <= set(by_role):
            raise JuvenileRadianceExportError(
                "compact receiver index requires receiver input and plant origins."
            )
        origins = by_role["plant_origins"]
        receiver_input = by_role["receiver_input"]
        if origins.byte_length != (
            self.scene.counts.plant_count * PLANT_ORIGIN_STRIDE_BYTES
        ):
            raise JuvenileRadianceExportError(
                "plant origin artifact length does not match the scene."
            )
        return {
            "schema_id": COMPACT_RECEIVER_INDEX_SCHEMA_ID,
            "schema_version": COMPACT_RECEIVER_INDEX_SCHEMA_VERSION,
            "export_plan_hash": self.export_plan_hash,
            "scene": {
                "scene_id": self.scene.scene_id,
                "scene_hash": self.scene.scene_hash,
                "profile_id": self.scene.profile_id,
                "sampling_profile_id": self.scene.sampling_profile_id,
                "layout_plan_hash": self.scene.layout_plan_hash,
            },
            "canonical_topology": self.scene.topology.to_payload(),
            "counts": self.scene.counts.to_payload(),
            "ordering": {
                "plants": "Y-major/X-minor",
                "receivers": RECEIVER_ORDERING,
                "trace_row_mapping": (
                    "trace row i equals global receiver index i"
                ),
            },
            "global_index_equations": {
                "leaf": "12 * plant_index + local_leaf_index",
                "face": "1920 * plant_index + local_face_index",
                "patch": "192 * plant_index + local_patch_index",
                "receiver": "384 * plant_index + local_receiver_index",
                "front_receiver": "384 * plant_index + 2 * local_patch_index",
                "back_receiver": (
                    "384 * plant_index + 2 * local_patch_index + 1"
                ),
            },
            "canonical_mapping": {
                "local_receiver_to_patch": "local_receiver_index // 2",
                "receiver_side": "even=front; odd=back",
                "patch_to_leaf_and_faces": (
                    "canonical topology identified by topology_sha256"
                ),
                "receiver_geometry": (
                    "canonical receiver identified by receivers_sha256"
                ),
                "sampling_profile_id": self.scene.sampling_profile_id,
                "normal_basis": (
                    "carrier triangle for optimized sampling; area-weighted "
                    "patch normal for explicit legacy replay"
                ),
            },
            "reconstruction": {
                "plant_origin_source": PLANT_ORIGINS_LOGICAL_NAME,
                "point_m": "canonical_receiver_point_m + plant_origin_m",
                "normal": "canonical_receiver_normal",
                "area_m2": "canonical_patch_one_sided_area_m2",
                "normal_offset_m": "canonical receiver normal offset",
                "approximate_spatial_matching": False,
            },
            "plant_origins": {
                "artifact": origins.to_payload(),
                "component_type": PLANT_ORIGIN_COMPONENT_TYPE,
                "byte_order": PLANT_ORIGIN_BYTE_ORDER,
                "stride_bytes": PLANT_ORIGIN_STRIDE_BYTES,
                "fields": list(PLANT_ORIGIN_FIELDS),
                "row_count": self.scene.counts.plant_count,
            },
            "receiver_input": {
                "artifact": receiver_input.to_payload(),
                "shape": RECEIVER_INPUT_SHAPE,
                "numeric_format": NUMERIC_FORMAT,
                "row_count": self.receiver_count,
            },
        }

    def compact_receiver_index_bytes(
        self,
        artifacts: Iterable[JuvenileRadianceArtifactMetadata],
    ) -> bytes:
        return (
            json.dumps(
                self.compact_receiver_index_payload(tuple(artifacts)),
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
            )
            + "\n"
        ).encode("utf-8")

    def manifest_payload(
        self,
        artifacts: Iterable[JuvenileRadianceArtifactMetadata] = (),
    ) -> dict[str, object]:
        """Return a deterministic manifest, enriched when artifacts exist."""

        ordered_artifacts = tuple(artifacts)
        by_role = {artifact.role: artifact for artifact in ordered_artifacts}
        if len(by_role) != len(ordered_artifacts):
            raise JuvenileRadianceExportError("artifact roles must be unique.")
        contracts = _artifact_contracts()
        expected_roles = set(contracts)
        if by_role and set(by_role) != expected_roles:
            raise JuvenileRadianceExportError(
                "materialized metadata must cover every planned artifact."
            )
        if by_role and any(
            (
                by_role[role].logical_name,
                by_role[role].media_type,
            )
            != contracts[role]
            for role in contracts
        ):
            raise JuvenileRadianceExportError(
                "materialized metadata does not match the artifact contract."
            )
        payload = self.identity_payload() | {
            "export_plan_hash": self.export_plan_hash,
        }
        if by_role:
            payload["artifacts"] = {
                role: by_role[role].to_payload()
                for role in _artifact_contracts()
            }
        return payload

    def manifest_bytes(
        self,
        artifacts: Iterable[JuvenileRadianceArtifactMetadata] = (),
    ) -> bytes:
        """Serialize the deterministic export manifest as UTF-8 JSON."""

        return (
            json.dumps(
                self.manifest_payload(tuple(artifacts)),
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
            )
            + "\n"
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class MaterializedJuvenileRadianceExport:
    """Paths and path-free integrity records for one materialized export."""

    plan: JuvenileRadianceExportPlan
    root: Path
    geometry_path: Path
    receiver_input_path: Path
    plant_origins_path: Path
    compact_receiver_index_path: Path
    manifest_path: Path
    artifacts: tuple[JuvenileRadianceArtifactMetadata, ...]
    manifest_artifact: JuvenileRadianceArtifactMetadata


def plan_juvenile_radiance_export(
    scene: JuvenileScientificScene,
    *,
    leaf_material_modifier: str = DEFAULT_LEAF_MATERIAL_MODIFIER,
) -> JuvenileRadianceExportPlan:
    """Create a source-neutral plan without expanding or serializing the scene."""

    return JuvenileRadianceExportPlan(
        scene=scene,
        leaf_material_modifier=leaf_material_modifier,
    )


def materialize_juvenile_radiance_export(
    plan: JuvenileRadianceExportPlan,
    target_dir: str | Path,
) -> MaterializedJuvenileRadianceExport:
    """Atomically stage deterministic artifacts using bounded-memory streams."""

    if not isinstance(plan, JuvenileRadianceExportPlan):
        raise JuvenileRadianceExportError(
            "plan must be an immutable JuvenileRadianceExportPlan."
        )
    root = Path(target_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise JuvenileRadianceExportError("export target must be a directory.")

    staged: list[tuple[Path, Path]] = []
    metadata: list[JuvenileRadianceArtifactMetadata] = []
    try:
        for role, logical_name, media_type, chunks in (
            (
                "plant_geometry",
                PLANT_GEOMETRY_LOGICAL_NAME,
                "model/vnd.radiance",
                plan.iter_geometry_bytes(),
            ),
            (
                "receiver_input",
                RECEIVER_INPUT_LOGICAL_NAME,
                "text/plain",
                plan.iter_receiver_input_bytes(),
            ),
            (
                "plant_origins",
                PLANT_ORIGINS_LOGICAL_NAME,
                "application/octet-stream",
                plan.iter_plant_origin_bytes(),
            ),
        ):
            final_path = _artifact_path(root, logical_name)
            temporary_path, artifact = _stage_artifact(
                final_path,
                role=role,
                logical_name=logical_name,
                media_type=media_type,
                chunks=chunks,
            )
            staged.append((temporary_path, final_path))
            metadata.append(artifact)

        compact_identity_path = _artifact_path(
            root, COMPACT_RECEIVER_INDEX_LOGICAL_NAME
        )
        temporary_identity, identity_artifact = _stage_artifact(
            compact_identity_path,
            role="compact_receiver_index",
            logical_name=COMPACT_RECEIVER_INDEX_LOGICAL_NAME,
            media_type="application/json",
            chunks=(plan.compact_receiver_index_bytes(metadata),),
        )
        staged.append((temporary_identity, compact_identity_path))
        metadata.append(identity_artifact)

        manifest_bytes = plan.manifest_bytes(metadata)
        manifest_path = _artifact_path(root, EXPORT_MANIFEST_LOGICAL_NAME)
        temporary_manifest, manifest_artifact = _stage_artifact(
            manifest_path,
            role="export_manifest",
            logical_name=EXPORT_MANIFEST_LOGICAL_NAME,
            media_type="application/json",
            chunks=(manifest_bytes,),
        )
        staged.append((temporary_manifest, manifest_path))
        for temporary_path, final_path in staged:
            os.replace(temporary_path, final_path)
    finally:
        for temporary_path, _final_path in staged:
            temporary_path.unlink(missing_ok=True)

    by_role = {item.role: item for item in metadata}
    return MaterializedJuvenileRadianceExport(
        plan=plan,
        root=root,
        geometry_path=_artifact_path(root, PLANT_GEOMETRY_LOGICAL_NAME),
        receiver_input_path=_artifact_path(root, RECEIVER_INPUT_LOGICAL_NAME),
        plant_origins_path=_artifact_path(root, PLANT_ORIGINS_LOGICAL_NAME),
        compact_receiver_index_path=_artifact_path(
            root, COMPACT_RECEIVER_INDEX_LOGICAL_NAME
        ),
        manifest_path=_artifact_path(root, EXPORT_MANIFEST_LOGICAL_NAME),
        artifacts=tuple(by_role[role] for role in _artifact_contracts()),
        manifest_artifact=manifest_artifact,
    )


def _geometry_record(
    face: ExpandedJuvenileFace,
    modifier: str,
) -> JuvenileRadianceGeometryRecord:
    if not isinstance(face, ExpandedJuvenileFace):
        raise JuvenileRadianceExportError("geometry record is not a Phase 26G face.")
    primitive_id = _safe_radiance_id(face.face_id, label="primitive identifier")
    if (
        primitive_id
        != _composite_id(face.plant_id, face.canonical_face_id)
        or face.global_face_index < 0
        or face.global_leaf_index < 0
    ):
        raise JuvenileRadianceExportError("geometry identity is inconsistent.")
    _validate_triangle(face)
    return JuvenileRadianceGeometryRecord(
        global_face_index=face.global_face_index,
        plant_index=face.plant_index,
        plant_id=face.plant_id,
        global_leaf_index=face.global_leaf_index,
        canonical_leaf_id=face.canonical_leaf_id,
        primitive_id=primitive_id,
        canonical_face_id=face.canonical_face_id,
        modifier=modifier,
        vertex_indices=face.vertex_indices,
        vertices=face.vertices,
        unit_normal=face.unit_normal,
        area_m2=face.area_m2,
    )


def _receiver_record(
    receiver: ExpandedJuvenileReceiver,
    *,
    leaf_rank: int,
    leaf_layer: str,
) -> JuvenileRadianceReceiverRecord:
    if not isinstance(receiver, ExpandedJuvenileReceiver):
        raise JuvenileRadianceExportError(
            "receiver record is not a Phase 26G receiver."
        )
    for value, label in (
        (receiver.receiver_id, "receiver identifier"),
        (receiver.plant_id, "plant identifier"),
        (receiver.canonical_leaf_id, "leaf identifier"),
        (receiver.canonical_patch_id, "patch identifier"),
        (receiver.face_id, "face identifier"),
    ):
        _safe_radiance_id(value, label=label)
    leaf_id = _composite_id(receiver.plant_id, receiver.canonical_leaf_id)
    patch_id = _composite_id(receiver.plant_id, receiver.canonical_patch_id)
    carrier_values = (
        receiver.surface_anchor_m,
        receiver.carrier_face_id,
        receiver.canonical_carrier_face_id,
        receiver.carrier_barycentric,
    )
    if (
        receiver.receiver_id
        != _composite_id(receiver.plant_id, receiver.canonical_receiver_id)
        or receiver.face_id
        != _composite_id(receiver.plant_id, receiver.canonical_face_id)
        or receiver.local_leaf_index != leaf_rank - 1
        or receiver.side not in ("front", "back")
        or receiver.local_patch_index != receiver.local_receiver_index // 2
        or (receiver.local_receiver_index % 2 == 0) != (receiver.side == "front")
        or receiver.global_receiver_index < 0
        or receiver.global_leaf_index < 0
        or receiver.global_patch_index < 0
        or not _finite_vector(receiver.point_m)
        or not _finite_vector(receiver.normal)
        or not _unit_vector(receiver.normal)
        or not _positive_finite_number(receiver.area_m2)
        or not _finite_number(receiver.normal_offset_m)
        or float(receiver.normal_offset_m) < 0.0
        or receiver.sampling_profile_id
        not in REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS
        or (any(value is not None for value in carrier_values) and not all(
            value is not None for value in carrier_values
        ))
        or not receiver.normal_generation_basis_id
    ):
        raise JuvenileRadianceExportError(
            "receiver identity, ordering, or geometry is invalid."
        )
    return JuvenileRadianceReceiverRecord(
        row_index=receiver.global_receiver_index,
        global_receiver_index=receiver.global_receiver_index,
        local_receiver_index=receiver.local_receiver_index,
        receiver_id=receiver.receiver_id,
        canonical_receiver_id=receiver.canonical_receiver_id,
        plant_index=receiver.plant_index,
        plant_id=receiver.plant_id,
        local_leaf_index=receiver.local_leaf_index,
        global_leaf_index=receiver.global_leaf_index,
        leaf_id=leaf_id,
        canonical_leaf_id=receiver.canonical_leaf_id,
        leaf_rank=leaf_rank,
        leaf_layer=leaf_layer,
        local_patch_index=receiver.local_patch_index,
        global_patch_index=receiver.global_patch_index,
        patch_id=patch_id,
        canonical_patch_id=receiver.canonical_patch_id,
        face_id=receiver.face_id,
        canonical_face_id=receiver.canonical_face_id,
        side=receiver.side,
        point_m=receiver.point_m,
        normal=receiver.normal,
        area_m2=receiver.area_m2,
        normal_offset_m=receiver.normal_offset_m,
        sampling_profile_id=receiver.sampling_profile_id,
        surface_anchor_m=receiver.surface_anchor_m,
        carrier_face_id=receiver.carrier_face_id,
        canonical_carrier_face_id=receiver.canonical_carrier_face_id,
        carrier_barycentric=receiver.carrier_barycentric,
        normal_generation_basis_id=receiver.normal_generation_basis_id,
    )


def _geometry_bytes(record: JuvenileRadianceGeometryRecord) -> bytes:
    lines = [
        f"{record.modifier} polygon {record.primitive_id}",
        "0",
        "0",
        "9",
        *(
            " ".join(_format_number(value) for value in vertex)
            for vertex in record.vertices
        ),
        "",
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


def _validate_scene(scene: JuvenileScientificScene) -> None:
    if not isinstance(scene, JuvenileScientificScene):
        raise JuvenileRadianceExportError(
            "scene must be an immutable Phase 26G JuvenileScientificScene."
        )
    if (
        scene.profile_id != REX_JUVENILE_PREHEADING_PROFILE_ID
        or scene.sampling_profile_id != scene.topology.sampling_profile_id
        or scene.coordinate_system != JUVENILE_SCIENTIFIC_COORDINATE_SYSTEM
        or scene.counts.plant_count != len(scene.plants)
        or scene.counts.face_count
        != scene.counts.plant_count * scene.topology.face_count
        or scene.counts.receiver_count
        != scene.counts.plant_count * scene.topology.receiver_count
        or scene.counts.face_count <= 0
        or scene.counts.receiver_count <= 0
        or not _valid_sha256(scene.scene_hash)
        or not _valid_sha256(scene.layout_plan_hash)
        or _hash_payload(scene.identity_payload()) != scene.scene_hash
    ):
        raise JuvenileRadianceExportError(
            "scene/profile identity or dynamic counts are inconsistent."
        )


def _validate_triangle(face: ExpandedJuvenileFace) -> None:
    if (
        len(face.vertices) != 3
        or not all(_finite_vector(vertex) for vertex in face.vertices)
        or not _finite_vector(face.unit_normal)
        or not _unit_vector(face.unit_normal)
        or not _positive_finite_number(face.area_m2)
    ):
        raise JuvenileRadianceExportError("face geometry is non-finite or degenerate.")
    a, b, c = face.vertices
    ab = tuple(b[index] - a[index] for index in range(3))
    ac = tuple(c[index] - a[index] for index in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    magnitude = math.sqrt(sum(value * value for value in cross))
    winding_dot = sum(
        cross[index] * face.unit_normal[index] for index in range(3)
    )
    if (
        not math.isfinite(magnitude)
        or magnitude <= 0.0
        or not math.isfinite(winding_dot)
        or winding_dot <= 0.0
        or not math.isclose(
            magnitude / 2.0,
            face.area_m2,
            rel_tol=1e-12,
            abs_tol=1e-18,
        )
    ):
        raise JuvenileRadianceExportError(
            "face winding, normal, or area is inconsistent."
        )


def _artifact_contracts() -> dict[str, tuple[str, str]]:
    return {
        "plant_geometry": (
            PLANT_GEOMETRY_LOGICAL_NAME,
            "model/vnd.radiance",
        ),
        "receiver_input": (RECEIVER_INPUT_LOGICAL_NAME, "text/plain"),
        "plant_origins": (
            PLANT_ORIGINS_LOGICAL_NAME,
            "application/octet-stream",
        ),
        "compact_receiver_index": (
            COMPACT_RECEIVER_INDEX_LOGICAL_NAME,
            "application/json",
        ),
    }


def _planned_artifact_payload() -> dict[str, dict[str, object]]:
    return {
        role: {
            "logical_name": logical_name,
            "media_type": media_type,
            "byte_length": None,
            "sha256": None,
        }
        for role, (logical_name, media_type) in _artifact_contracts().items()
    }


def _stage_artifact(
    final_path: Path,
    *,
    role: str,
    logical_name: str,
    media_type: str,
    chunks: Iterable[bytes],
) -> tuple[Path, JuvenileRadianceArtifactMetadata]:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{final_path.name}.",
        suffix=".tmp",
        dir=final_path.parent,
    )
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    byte_length = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise JuvenileRadianceExportError(
                        "artifact iterators must yield bytes."
                    )
                handle.write(chunk)
                digest.update(chunk)
                byte_length += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path, JuvenileRadianceArtifactMetadata(
        role=role,
        logical_name=logical_name,
        media_type=media_type,
        byte_length=byte_length,
        sha256=digest.hexdigest(),
    )


def _artifact_path(root: Path, logical_name: str) -> Path:
    if (
        not logical_name
        or Path(logical_name).name != logical_name
        or "/" in logical_name
        or "\\" in logical_name
    ):
        raise JuvenileRadianceExportError("artifact logical name is unsafe.")
    path = root / logical_name
    if path.parent.resolve() != root:
        raise JuvenileRadianceExportError("artifact path escapes the export root.")
    return path


def _format_number(value: float) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise JuvenileRadianceExportError("numeric export value must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise JuvenileRadianceExportError("numeric export value must be finite.")
    return f"{number:.17g}"


def _safe_radiance_id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_RADIANCE_ID.fullmatch(value):
        raise JuvenileRadianceExportError(f"{label} is not Radiance-safe.")
    return value


def _composite_id(plant_id: str, canonical_id: str) -> str:
    return _safe_radiance_id(
        f"{plant_id}__{canonical_id}",
        label="composite scientific identifier",
    )


def _finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
    )


def _positive_finite_number(value: object) -> bool:
    return _finite_number(value) and float(value) > 0.0


def _finite_vector(value: tuple[float, float, float]) -> bool:
    return len(value) == 3 and all(_finite_number(component) for component in value)


def _unit_vector(value: tuple[float, float, float]) -> bool:
    magnitude = math.sqrt(sum(component * component for component in value))
    return math.isclose(magnitude, 1.0, rel_tol=1e-12, abs_tol=1e-12)


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _hash_payload(payload: object) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )
