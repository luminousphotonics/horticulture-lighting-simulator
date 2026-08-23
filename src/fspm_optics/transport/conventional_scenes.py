"""Pure ScenePlan integration for Conventional baseline and FSPM receivers."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Literal

from fspm_optics.fixtures.conventional_led.radiance_source import (
    ConventionalRadianceSourcePlan,
)
from fspm_optics.transport.scenes import ScenePlan

ConventionalSceneRole = Literal["baseline_ppfd", "fspm_receiver"]


@dataclass(frozen=True, slots=True)
class ConventionalTransportScenePlan:
    role: ConventionalSceneRole
    scene: ScenePlan
    receiver_input_path: Path
    receiver_kind: Literal["horizontal_sensor_grid", "rex_plant_receivers"]
    source_plan_id: str
    room_identity: str
    receiver_identity: str
    plant_geometry_identity: str | None
    scene_id: str = field(init=False)
    future_octree_identity: str = field(init=False)
    future_ambient_cache_identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "receiver_input_path", Path(self.receiver_input_path))
        if self.scene.role != self.role:
            raise ValueError("wrapped ScenePlan role is inconsistent.")
        if not all((self.source_plan_id, self.room_identity, self.receiver_identity)):
            raise ValueError("scene scientific identities must be non-empty.")
        if self.role == "baseline_ppfd":
            if self.receiver_kind != "horizontal_sensor_grid":
                raise ValueError("baseline scene requires its horizontal sensor grid.")
            if self.plant_geometry_identity is not None or len(self.scene.source_files) != 4:
                raise ValueError("baseline scene must contain room and source physics only.")
        else:
            if self.receiver_kind != "rex_plant_receivers":
                raise ValueError("FSPM scene requires Rex plant receivers.")
            if not self.plant_geometry_identity or len(self.scene.source_files) != 5:
                raise ValueError("FSPM scene must include Rex plant geometry.")
        scene_id = "conventional-scene-v2-" + _hash_payload(self.scientific_payload())
        object.__setattr__(self, "scene_id", scene_id)
        object.__setattr__(
            self,
            "future_octree_identity",
            "conventional-octree-v2-" + _hash_payload({"scene_id": scene_id}),
        )
        object.__setattr__(
            self,
            "future_ambient_cache_identity",
            "conventional-ambient-cache-v2-"
            + _hash_payload({"scene_id": scene_id, "cache_role": self.role}),
        )

    def scientific_payload(self) -> dict[str, object]:
        return {
            "role": self.role,
            "source_plan_id": self.source_plan_id,
            "room_identity": self.room_identity,
            "receiver_identity": self.receiver_identity,
            "receiver_kind": self.receiver_kind,
            "plant_geometry_identity": self.plant_geometry_identity,
            "source_file_roles": (
                [
                    "room",
                    "shared_angular_source",
                    "fixture_apertures",
                    "fixture_bodies",
                ]
                if self.role == "baseline_ppfd"
                else [
                    "room",
                    "shared_angular_source",
                    "fixture_apertures",
                    "fixture_bodies",
                    "rex_plant_geometry",
                ]
            ),
            "rebuild_from_complete_sources": self.scene.rebuild_from_sources,
        }

    def to_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {
            "scene_id": self.scene_id,
            "future_octree_identity": self.future_octree_identity,
            "future_ambient_cache_identity": self.future_ambient_cache_identity,
            "runtime_paths": {
                "source_files": [str(path) for path in self.scene.source_files],
                "receiver_input": str(self.receiver_input_path),
            },
            "execution_performed": False,
        }


@dataclass(frozen=True, slots=True)
class ConventionalScenePlanBundle:
    baseline: ConventionalTransportScenePlan
    fspm: ConventionalTransportScenePlan

    def __post_init__(self) -> None:
        if self.baseline.role != "baseline_ppfd" or self.fspm.role != "fspm_receiver":
            raise ValueError("scene bundle roles are invalid.")
        if self.baseline.source_plan_id != self.fspm.source_plan_id:
            raise ValueError("scene bundle must share one source physics identity.")
        if self.baseline.scene_id == self.fspm.scene_id:
            raise ValueError("baseline and FSPM scenes must remain distinct.")


def plan_conventional_baseline_scene(
    source_plan: ConventionalRadianceSourcePlan,
    *,
    room: str | Path,
    room_identity: str,
    shared_angular_source: str | Path,
    fixture_apertures: str | Path,
    fixture_bodies: str | Path,
    horizontal_sensor_grid: str | Path,
    sensor_grid_identity: str,
) -> ConventionalTransportScenePlan:
    scene = ScenePlan(
        role="baseline_ppfd",
        source_files=(
            Path(room),
            Path(shared_angular_source),
            Path(fixture_apertures),
            Path(fixture_bodies),
        ),
    )
    return ConventionalTransportScenePlan(
        role="baseline_ppfd",
        scene=scene,
        receiver_input_path=Path(horizontal_sensor_grid),
        receiver_kind="horizontal_sensor_grid",
        source_plan_id=source_plan.source_plan_id,
        room_identity=room_identity,
        receiver_identity=sensor_grid_identity,
        plant_geometry_identity=None,
    )


def plan_conventional_fspm_scene(
    source_plan: ConventionalRadianceSourcePlan,
    *,
    room: str | Path,
    room_identity: str,
    shared_angular_source: str | Path,
    fixture_apertures: str | Path,
    fixture_bodies: str | Path,
    rex_plant_geometry: str | Path,
    rex_plant_geometry_identity: str,
    rex_receiver_input: str | Path,
    rex_receiver_identity: str,
) -> ConventionalTransportScenePlan:
    scene = ScenePlan(
        role="fspm_receiver",
        source_files=(
            Path(room),
            Path(shared_angular_source),
            Path(fixture_apertures),
            Path(fixture_bodies),
            Path(rex_plant_geometry),
        ),
    )
    return ConventionalTransportScenePlan(
        role="fspm_receiver",
        scene=scene,
        receiver_input_path=Path(rex_receiver_input),
        receiver_kind="rex_plant_receivers",
        source_plan_id=source_plan.source_plan_id,
        room_identity=room_identity,
        receiver_identity=rex_receiver_identity,
        plant_geometry_identity=rex_plant_geometry_identity,
    )


def plan_conventional_scene_bundle(
    source_plan: ConventionalRadianceSourcePlan,
    *,
    room: str | Path,
    room_identity: str,
    shared_angular_source: str | Path,
    fixture_apertures: str | Path,
    fixture_bodies: str | Path,
    horizontal_sensor_grid: str | Path,
    sensor_grid_identity: str,
    rex_plant_geometry: str | Path,
    rex_plant_geometry_identity: str,
    rex_receiver_input: str | Path,
    rex_receiver_identity: str,
) -> ConventionalScenePlanBundle:
    baseline = plan_conventional_baseline_scene(
        source_plan,
        room=room,
        room_identity=room_identity,
        shared_angular_source=shared_angular_source,
        fixture_apertures=fixture_apertures,
        fixture_bodies=fixture_bodies,
        horizontal_sensor_grid=horizontal_sensor_grid,
        sensor_grid_identity=sensor_grid_identity,
    )
    fspm = plan_conventional_fspm_scene(
        source_plan,
        room=room,
        room_identity=room_identity,
        shared_angular_source=shared_angular_source,
        fixture_apertures=fixture_apertures,
        fixture_bodies=fixture_bodies,
        rex_plant_geometry=rex_plant_geometry,
        rex_plant_geometry_identity=rex_plant_geometry_identity,
        rex_receiver_input=rex_receiver_input,
        rex_receiver_identity=rex_receiver_identity,
    )
    return ConventionalScenePlanBundle(baseline=baseline, fspm=fspm)


def _hash_payload(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
