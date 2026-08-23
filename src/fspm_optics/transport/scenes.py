"""Deterministic scene-source planning for baseline and plant receivers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fspm_optics.radiance.commands import (
    CommandSpec,
    build_oconv_command,
    require_input_file,
)

SceneRole = Literal["baseline_ppfd", "fspm_receiver"]


@dataclass(frozen=True, slots=True)
class ScenePlan:
    """Source files for one independently compiled Radiance scene."""

    role: SceneRole
    source_files: tuple[Path, ...]
    rebuild_from_sources: bool = True

    def __post_init__(self) -> None:
        if not self.source_files:
            raise ValueError("A scene plan requires at least one source file.")
        object.__setattr__(
            self,
            "source_files",
            tuple(Path(path) for path in self.source_files),
        )
        if not self.rebuild_from_sources:
            raise ValueError("Scene plans must compile from complete source files.")

    def compilation_command(
        self,
        output_octree: str | Path,
        *,
        cwd: str | Path | None = None,
    ) -> CommandSpec:
        return build_oconv_command(
            self.source_files,
            output_octree=output_octree,
            cwd=cwd,
            label=f"compile_{self.role}",
        )


def plan_baseline_ppfd_scene(
    *,
    room: str | Path,
    emitters: str | Path,
) -> ScenePlan:
    """Plan the fixture-only baseline field; plant geometry is not accepted."""

    return ScenePlan(
        role="baseline_ppfd",
        source_files=(
            require_input_file(room, label="Room geometry"),
            require_input_file(emitters, label="Emitter geometry"),
        ),
    )


def plan_fspm_receiver_scene(
    *,
    room: str | Path,
    emitters: str | Path,
    plant_geometry: str | Path,
) -> ScenePlan:
    """Plan a fresh full-source receiver octree including plant geometry."""

    return ScenePlan(
        role="fspm_receiver",
        source_files=(
            require_input_file(room, label="Room geometry"),
            require_input_file(emitters, label="Emitter geometry"),
            require_input_file(plant_geometry, label="FSPM plant geometry"),
        ),
    )
