from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeAlias, cast


Scalar: TypeAlias = bool | int | float | str
PointTuple: TypeAlias = tuple[float, float]
ModuleTuple: TypeAlias = tuple[str, str, list[PointTuple]]


@dataclass(frozen=True, order=True)
class LayoutPoint:
    x: float
    y: float

    def as_tuple(self) -> PointTuple:
        return (self.x, self.y)


@dataclass(frozen=True)
class ModuleGroup:
    module_type: str
    orientation: str
    points: tuple[LayoutPoint, ...]

    def as_tuple(self) -> ModuleTuple:
        return (
            self.module_type,
            self.orientation,
            [point.as_tuple() for point in self.points],
        )


@dataclass(frozen=True)
class ZoneGroup:
    zone: int
    kind: str
    points: tuple[LayoutPoint, ...]
    metadata: tuple[tuple[str, Scalar], ...] = ()

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"zone": self.zone, "kind": self.kind}
        payload.update(dict(self.metadata))
        payload["points"] = [point.as_tuple() for point in self.points]
        return payload


@dataclass(frozen=True)
class LayoutTopology:
    base_n: int
    square_tile_count: int
    connector_count: int
    has_rect_extension: bool
    rect_long_ft: float
    rect_offset: int
    zone_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "base_n": self.base_n,
            "square_tile_count": self.square_tile_count,
            "connector_count": self.connector_count,
            "has_rect_extension": self.has_rect_extension,
            "rect_long_ft": self.rect_long_ft,
            "rect_offset": self.rect_offset,
            "zone_count": self.zone_count,
        }


@dataclass(frozen=True)
class LayoutResult:
    all_positions: tuple[LayoutPoint, ...]
    module_groups: tuple[ModuleGroup, ...]
    zone_groups: tuple[ZoneGroup, ...]
    topology: LayoutTopology

    def as_legacy_dict(self, *, include_zones: bool) -> dict[str, object]:
        payload: dict[str, object] = {
            "all_positions": [point.as_tuple() for point in self.all_positions],
            "module_groups": [group.as_tuple() for group in self.module_groups],
        }
        if include_zones:
            payload["zone_groups"] = [zone.as_dict() for zone in self.zone_groups]
            payload["topology"] = self.topology.as_dict()
        return payload


def layout_point(value: object) -> LayoutPoint:
    x, y = cast(tuple[object, object], value)
    return LayoutPoint(float(cast(int | float | str, x)), float(cast(int | float | str, y)))


def module_group(value: object) -> ModuleGroup:
    module_type, orientation, points = cast(tuple[object, object, Iterable[object]], value)
    return ModuleGroup(
        str(module_type),
        str(orientation),
        tuple(layout_point(point) for point in points),
    )


def zone_group(value: dict[str, object]) -> ZoneGroup:
    metadata = tuple(
        (str(key), scalar)
        for key, scalar in value.items()
        if key not in {"zone", "kind", "points"} and isinstance(scalar, bool | int | float | str)
    )
    return ZoneGroup(
        zone=int(cast(int | float | str, value["zone"])),
        kind=str(value["kind"]),
        points=tuple(layout_point(point) for point in cast(Iterable[object], value.get("points", ()))),
        metadata=metadata,
    )


def layout_topology(value: dict[str, object]) -> LayoutTopology:
    return LayoutTopology(
        base_n=int(cast(int | float | str, value["base_n"])),
        square_tile_count=int(cast(int | float | str, value["square_tile_count"])),
        connector_count=int(cast(int | float | str, value["connector_count"])),
        has_rect_extension=bool(value["has_rect_extension"]),
        rect_long_ft=float(cast(int | float | str, value["rect_long_ft"])),
        rect_offset=int(cast(int | float | str, value["rect_offset"])),
        zone_count=int(cast(int | float | str, value["zone_count"])),
    )


def layout_result_from_mapping(value: dict[str, object]) -> LayoutResult:
    return LayoutResult(
        all_positions=tuple(layout_point(point) for point in cast(Iterable[object], value.get("all_positions", ()))),
        module_groups=tuple(module_group(group) for group in cast(Iterable[object], value.get("module_groups", ()))),
        zone_groups=tuple(zone_group(cast(dict[str, object], zone)) for zone in cast(Iterable[object], value.get("zone_groups", ()))),
        topology=layout_topology(cast(dict[str, object], value.get("topology", {}))),
    )
