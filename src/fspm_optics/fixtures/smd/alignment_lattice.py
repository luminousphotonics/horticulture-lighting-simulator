"""Permanent alignment lattice for standalone Proposed LED modules.

Topology is authored on the exact pre-scale integer module lattice.  Physical
free spans are then derived from final module centers and the immutable
0.150 x 0.150 m mechanical envelope.  Every link lies on the authenticated
rear-most plane of ``proposed-led-module-v1``; this is deliberately separate
from the calibrated light-side aperture plane and is non-emitting.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Final, Literal, Mapping, Protocol, Sequence

from fspm_optics.fixtures.structure_material import FIXTURE_BODY_MATERIAL_ID
from fspm_optics.layout.domain import LayoutPoint


ALIGNMENT_LATTICE_SCHEMA_ID: Final = (
    "fspm-optics.proposed-standalone-alignment-lattice"
)
ALIGNMENT_LATTICE_SCHEMA_VERSION: Final = 1
ALIGNMENT_LATTICE_TOPOLOGY_ID: Final = (
    "standalone_module_integer_staggered_triangular_graph_v1"
)
ALIGNMENT_LATTICE_GEOMETRY_ID: Final = (
    "straight_taut_free_span_cylinders_v1"
)
ALIGNMENT_LATTICE_ATTACHMENT_POLICY_ID: Final = (
    "proposed_led_module_authenticated_rear_plane_v1"
)
ALIGNMENT_LATTICE_DIAMETER_M: Final = 0.0015875
ALIGNMENT_LATTICE_RADIUS_M: Final = ALIGNMENT_LATTICE_DIAMETER_M / 2.0
AUTHENTICATED_MODULE_ASSET_ID: Final = "proposed-led-module-v1"
AUTHENTICATED_MODULE_GLB_SHA256: Final = (
    "f910a7fc512635bcb3a48d7b546175ef13623135836a76eeb625b2ca12cd5445"
)
# Authenticated full-GLB bounds after the fixed catalog linear transform.
AUTHENTICATED_REAR_PLANE_OFFSET_M: Final = 0.034800000889971816

LinkKind = Literal["horizontal", "diagonal"]


class ModulePosition(Protocol):
    module_index: int
    x_m: float
    y_m: float
    z_m: float


@dataclass(frozen=True, slots=True)
class AlignmentLatticeLink:
    link_index: int
    kind: LinkKind
    start_module_index: int
    end_module_index: int
    start_xyz_m: tuple[float, float, float]
    end_xyz_m: tuple[float, float, float]

    def to_payload(self) -> dict[str, object]:
        return {
            "link_index": self.link_index,
            "kind": self.kind,
            "start_module_index": self.start_module_index,
            "end_module_index": self.end_module_index,
            "start_xyz_m": list(self.start_xyz_m),
            "end_xyz_m": list(self.end_xyz_m),
        }


@dataclass(frozen=True, slots=True)
class AlignmentLattice:
    links: tuple[AlignmentLatticeLink, ...]
    module_count: int
    attachment_plane_z_m: float
    module_envelope_x_m: float
    module_envelope_y_m: float
    identity_sha256: str

    @property
    def horizontal_count(self) -> int:
        return sum(link.kind == "horizontal" for link in self.links)

    @property
    def diagonal_count(self) -> int:
        return sum(link.kind == "diagonal" for link in self.links)

    def to_payload(self) -> dict[str, object]:
        return _payload_without_identity(self) | {
            "identity_sha256": self.identity_sha256,
        }


def build_alignment_lattice(
    modules: Sequence[ModulePosition],
    modular_points: Sequence[LayoutPoint],
    *,
    envelope_x_m: float,
    envelope_y_m: float,
) -> AlignmentLattice:
    """Build exact staggered adjacency and transformed free-span cylinders."""

    if len(modules) != len(modular_points) or not modules:
        raise ValueError("alignment lattice requires one modular point per module.")
    if envelope_x_m != 0.15 or envelope_y_m != 0.15:
        raise ValueError(
            "alignment lattice requires the authenticated 0.150 m envelope."
        )
    if tuple(module.module_index for module in modules) != tuple(range(len(modules))):
        raise ValueError("alignment lattice modules must retain authoritative order.")
    aperture_planes = {module.z_m for module in modules}
    if len(aperture_planes) != 1:
        raise ValueError("alignment lattice modules must be coplanar.")
    attachment_z = next(iter(aperture_planes)) + AUTHENTICATED_REAR_PLANE_OFFSET_M
    lattice_coordinates = tuple(
        (point.x + point.y, point.x - point.y) for point in modular_points
    )
    rows: dict[float, list[tuple[float, int]]] = {}
    for module_index, (horizontal, row) in enumerate(lattice_coordinates):
        rows.setdefault(row, []).append((horizontal, module_index))
    ordered_rows = tuple(sorted(rows))
    if any(right - left != 1.0 for left, right in zip(ordered_rows, ordered_rows[1:])):
        raise ValueError("alignment lattice physical rows must be exactly adjacent.")

    pairs: list[tuple[LinkKind, int, int]] = []
    for row in ordered_rows:
        ordered = sorted(rows[row])
        if any(right[0] - left[0] != 2.0 for left, right in zip(ordered, ordered[1:])):
            raise ValueError(
                "alignment lattice row modules must be exactly consecutive."
            )
        pairs.extend(
            ("horizontal", left[1], right[1])
            for left, right in zip(ordered, ordered[1:])
        )
    for lower_row, upper_row in zip(ordered_rows, ordered_rows[1:]):
        lower = sorted(rows[lower_row])
        upper = sorted(rows[upper_row])
        pairs.extend(
            ("diagonal", lower_index, upper_index)
            for lower_horizontal, lower_index in lower
            for upper_horizontal, upper_index in upper
            if abs(lower_horizontal - upper_horizontal) == 1.0
        )

    if len({tuple(sorted(pair[1:])) for pair in pairs}) != len(pairs):
        raise ValueError("alignment lattice contains a duplicate module link.")
    links = tuple(
        _physical_link(
            link_index,
            kind,
            modules[start_index],
            modules[end_index],
            attachment_z=attachment_z,
            half_x=envelope_x_m / 2.0,
            half_y=envelope_y_m / 2.0,
        )
        for link_index, (kind, start_index, end_index) in enumerate(pairs)
    )
    _validate_connected(len(modules), links)
    draft = AlignmentLattice(
        links=links,
        module_count=len(modules),
        attachment_plane_z_m=attachment_z,
        module_envelope_x_m=envelope_x_m,
        module_envelope_y_m=envelope_y_m,
        identity_sha256="",
    )
    identity = _hash_payload(_payload_without_identity(draft))
    return AlignmentLattice(
        links=links,
        module_count=draft.module_count,
        attachment_plane_z_m=draft.attachment_plane_z_m,
        module_envelope_x_m=draft.module_envelope_x_m,
        module_envelope_y_m=draft.module_envelope_y_m,
        identity_sha256=identity,
    )


def validate_alignment_lattice_payload(
    raw: object,
    *,
    expected_module_count: int | None = None,
    expected_modules: object = None,
) -> Mapping[str, object]:
    """Validate the complete public geometry/identity contract without inference."""

    if not isinstance(raw, Mapping):
        raise ValueError("standalone alignment lattice payload is missing.")
    required = {
        "schema_id", "schema_version", "topology_id", "geometry_id",
        "attachment_policy", "diameter_m", "radius_m", "material_id",
        "transport_role", "module_count", "counts", "links", "identity_sha256",
    }
    if set(raw) != required:
        raise ValueError("standalone alignment lattice schema fields are incompatible.")
    if (
        raw.get("schema_id") != ALIGNMENT_LATTICE_SCHEMA_ID
        or raw.get("schema_version") != ALIGNMENT_LATTICE_SCHEMA_VERSION
        or raw.get("topology_id") != ALIGNMENT_LATTICE_TOPOLOGY_ID
        or raw.get("geometry_id") != ALIGNMENT_LATTICE_GEOMETRY_ID
        or raw.get("diameter_m") != ALIGNMENT_LATTICE_DIAMETER_M
        or raw.get("radius_m") != ALIGNMENT_LATTICE_RADIUS_M
        or raw.get("material_id") != FIXTURE_BODY_MATERIAL_ID
        or raw.get("transport_role") != "non_emitting_occluding_geometry"
    ):
        raise ValueError("standalone alignment lattice policy is incompatible.")
    attachment = raw.get("attachment_policy")
    if attachment != {
        "id": ALIGNMENT_LATTICE_ATTACHMENT_POLICY_ID,
        "authenticated_asset_id": AUTHENTICATED_MODULE_ASSET_ID,
        "authenticated_glb_sha256": AUTHENTICATED_MODULE_GLB_SHA256,
        "surface": "rear_most_authenticated_full_glb_plane",
        "rear_plane_offset_from_aperture_m": AUTHENTICATED_REAR_PLANE_OFFSET_M,
        "module_envelope_m": [0.15, 0.15],
        "endpoint_contact_only": True,
    }:
        raise ValueError(
            "standalone alignment lattice attachment policy is incompatible."
        )
    module_count = raw.get("module_count")
    links = raw.get("links")
    counts = raw.get("counts")
    if (
        isinstance(module_count, bool)
        or not isinstance(module_count, int)
        or module_count <= 0
        or (expected_module_count is not None and module_count != expected_module_count)
        or not isinstance(links, list)
        or not isinstance(counts, Mapping)
    ):
        raise ValueError("standalone alignment lattice counts are malformed.")
    kinds: list[str] = []
    pairs: set[tuple[int, int]] = set()
    attachment_planes: set[float] = set()
    for index, link in enumerate(links):
        if not isinstance(link, Mapping) or set(link) != {
            "link_index", "kind", "start_module_index", "end_module_index",
            "start_xyz_m", "end_xyz_m",
        }:
            raise ValueError("standalone alignment lattice link is malformed.")
        kind = link.get("kind")
        start_index = link.get("start_module_index")
        end_index = link.get("end_module_index")
        endpoints = (link.get("start_xyz_m"), link.get("end_xyz_m"))
        if (
            link.get("link_index") != index
            or kind not in {"horizontal", "diagonal"}
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in (start_index, end_index)
            )
            or not (0 <= start_index < module_count and 0 <= end_index < module_count)
            or start_index == end_index
            or any(
                not isinstance(point, list)
                or len(point) != 3
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, int | float)
                    or not math.isfinite(float(value))
                    for value in point
                )
                for point in endpoints
            )
        ):
            raise ValueError("standalone alignment lattice link values are invalid.")
        pair = tuple(sorted((start_index, end_index)))
        if pair in pairs:
            raise ValueError("standalone alignment lattice repeats a module pair.")
        pairs.add(pair)
        kinds.append(kind)
        attachment_planes.update(float(point[2]) for point in endpoints)
    expected_counts = {
        "total": len(links),
        "horizontal": kinds.count("horizontal"),
        "diagonal": kinds.count("diagonal"),
    }
    if dict(counts) != expected_counts or len(attachment_planes) != 1:
        raise ValueError("standalone alignment lattice link counts or plane changed.")
    neighbors = {index: set() for index in range(module_count)}
    for start_index, end_index in pairs:
        neighbors[start_index].add(end_index)
        neighbors[end_index].add(start_index)
    visited = {0}
    pending = [0]
    while pending:
        for neighbor in neighbors[pending.pop()]:
            if neighbor not in visited:
                visited.add(neighbor)
                pending.append(neighbor)
    if len(visited) != module_count:
        raise ValueError("standalone alignment lattice graph is disconnected.")
    if expected_modules is not None:
        _validate_payload_endpoints(raw, expected_modules)
    without_identity = dict(raw)
    identity = without_identity.pop("identity_sha256")
    if not isinstance(identity, str) or identity != _hash_payload(without_identity):
        raise ValueError("standalone alignment lattice identity is incompatible.")
    return raw


def _validate_payload_endpoints(
    lattice: Mapping[str, object], modules_raw: object
) -> None:
    if not isinstance(modules_raw, list) or len(modules_raw) != lattice["module_count"]:
        raise ValueError("standalone alignment lattice module records are incomplete.")
    modules: list[Mapping[str, object]] = []
    for index, module in enumerate(modules_raw):
        if not isinstance(module, Mapping) or module.get("module_index") != index:
            raise ValueError("standalone alignment lattice module order changed.")
        modules.append(module)
    for link in lattice["links"]:  # type: ignore[union-attr]
        start = modules[link["start_module_index"]]
        end = modules[link["end_module_index"]]
        start_center = tuple(
            float(start[name]) for name in ("x_m", "y_m", "z_m")
        )
        end_center = tuple(
            float(end[name]) for name in ("x_m", "y_m", "z_m")
        )
        dx = end_center[0] - start_center[0]
        dy = end_center[1] - start_center[1]
        kind = link["kind"]
        if (
            dx == 0.0
            or (kind == "horizontal" and dy != 0.0)
            or (kind == "diagonal" and dy == 0.0)
        ):
            raise ValueError("standalone alignment lattice link direction changed.")
        offset_x = math.copysign(0.075, dx)
        offset_y = 0.0 if kind == "horizontal" else math.copysign(0.075, dy)
        expected_start = [
            start_center[0] + offset_x,
            start_center[1] + offset_y,
            start_center[2] + AUTHENTICATED_REAR_PLANE_OFFSET_M,
        ]
        expected_end = [
            end_center[0] - offset_x,
            end_center[1] - offset_y,
            end_center[2] + AUTHENTICATED_REAR_PLANE_OFFSET_M,
        ]
        if (
            link["start_xyz_m"] != expected_start
            or link["end_xyz_m"] != expected_end
        ):
            raise ValueError(
                "standalone alignment lattice free-span endpoints changed."
            )


def _physical_link(
    link_index: int,
    kind: LinkKind,
    start: ModulePosition,
    end: ModulePosition,
    *,
    attachment_z: float,
    half_x: float,
    half_y: float,
) -> AlignmentLatticeLink:
    dx = end.x_m - start.x_m
    dy = end.y_m - start.y_m
    if kind == "horizontal" and (dy != 0.0 or dx == 0.0):
        raise ValueError("horizontal alignment links must join one physical row.")
    if kind == "diagonal" and (dx == 0.0 or dy == 0.0):
        raise ValueError("diagonal alignment links must join staggered rows.")
    sx = math.copysign(half_x, dx)
    sy = 0.0 if kind == "horizontal" else math.copysign(half_y, dy)
    start_xyz = (start.x_m + sx, start.y_m + sy, attachment_z)
    end_xyz = (end.x_m - sx, end.y_m - sy, attachment_z)
    if math.dist(start_xyz, end_xyz) <= 0.0:
        raise ValueError("alignment lattice free span must be positive.")
    return AlignmentLatticeLink(
        link_index=link_index,
        kind=kind,
        start_module_index=start.module_index,
        end_module_index=end.module_index,
        start_xyz_m=start_xyz,
        end_xyz_m=end_xyz,
    )


def _validate_connected(
    module_count: int, links: Sequence[AlignmentLatticeLink]
) -> None:
    neighbors = {index: set() for index in range(module_count)}
    for link in links:
        neighbors[link.start_module_index].add(link.end_module_index)
        neighbors[link.end_module_index].add(link.start_module_index)
    visited = {0}
    pending = [0]
    while pending:
        for neighbor in neighbors[pending.pop()]:
            if neighbor not in visited:
                visited.add(neighbor)
                pending.append(neighbor)
    if len(visited) != module_count:
        raise ValueError("alignment lattice graph must be connected.")


def _payload_without_identity(lattice: AlignmentLattice) -> dict[str, object]:
    return {
        "schema_id": ALIGNMENT_LATTICE_SCHEMA_ID,
        "schema_version": ALIGNMENT_LATTICE_SCHEMA_VERSION,
        "topology_id": ALIGNMENT_LATTICE_TOPOLOGY_ID,
        "geometry_id": ALIGNMENT_LATTICE_GEOMETRY_ID,
        "attachment_policy": {
            "id": ALIGNMENT_LATTICE_ATTACHMENT_POLICY_ID,
            "authenticated_asset_id": AUTHENTICATED_MODULE_ASSET_ID,
            "authenticated_glb_sha256": AUTHENTICATED_MODULE_GLB_SHA256,
            "surface": "rear_most_authenticated_full_glb_plane",
            "rear_plane_offset_from_aperture_m": AUTHENTICATED_REAR_PLANE_OFFSET_M,
            "module_envelope_m": [
                lattice.module_envelope_x_m,
                lattice.module_envelope_y_m,
            ],
            "endpoint_contact_only": True,
        },
        "diameter_m": ALIGNMENT_LATTICE_DIAMETER_M,
        "radius_m": ALIGNMENT_LATTICE_RADIUS_M,
        "material_id": FIXTURE_BODY_MATERIAL_ID,
        "transport_role": "non_emitting_occluding_geometry",
        "module_count": lattice.module_count,
        "counts": {
            "total": len(lattice.links),
            "horizontal": lattice.horizontal_count,
            "diagonal": lattice.diagonal_count,
        },
        "links": [link.to_payload() for link in lattice.links],
    }


def _hash_payload(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "ALIGNMENT_LATTICE_ATTACHMENT_POLICY_ID",
    "ALIGNMENT_LATTICE_DIAMETER_M",
    "ALIGNMENT_LATTICE_GEOMETRY_ID",
    "ALIGNMENT_LATTICE_RADIUS_M",
    "ALIGNMENT_LATTICE_SCHEMA_ID",
    "ALIGNMENT_LATTICE_SCHEMA_VERSION",
    "ALIGNMENT_LATTICE_TOPOLOGY_ID",
    "AUTHENTICATED_MODULE_ASSET_ID",
    "AUTHENTICATED_MODULE_GLB_SHA256",
    "AUTHENTICATED_REAR_PLANE_OFFSET_M",
    "AlignmentLattice",
    "AlignmentLatticeLink",
    "build_alignment_lattice",
    "validate_alignment_lattice_payload",
]
