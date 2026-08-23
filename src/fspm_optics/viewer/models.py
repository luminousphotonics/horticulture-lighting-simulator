"""Immutable models for deterministic viewer display artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping


@dataclass(frozen=True, slots=True)
class BinaryDisplayArtifact:
    """One named immutable display derivative."""

    filename: str
    data: bytes
    sha256: str

    @property
    def byte_size(self) -> int:
        return len(self.data)


@dataclass(frozen=True, slots=True)
class SurfaceFluxViewerArtifacts:
    """Authenticated metadata, raw RGBA values, and optional v3 calibration."""

    metadata: BinaryDisplayArtifact
    patch_values: BinaryDisplayArtifact
    calibration_coefficients: BinaryDisplayArtifact | None = None

    def __post_init__(self) -> None:
        metadata_versions = {
            "surface-flux/metadata.v1.json": 1,
            "surface-flux/metadata.v2.json": 2,
            "surface-flux/metadata.v3.json": 3,
        }
        expected_version = metadata_versions.get(self.metadata.filename)
        if (
            expected_version is None
            or self.patch_values.filename
            != "surface-flux/patch-values.v1.f32le.bin"
        ):
            raise ValueError("surface-flux viewer filenames are incompatible.")
        if expected_version == 3:
            if (
                self.calibration_coefficients is None
                or self.calibration_coefficients.filename
                != "surface-flux/display-calibration-coefficients.v1.f64le.bin"
            ):
                raise ValueError(
                    "surface-flux metadata v3 requires its coefficient payload."
                )
        elif self.calibration_coefficients is not None:
            raise ValueError(
                "historical surface-flux metadata must not add coefficient bytes."
            )
        artifacts = (self.metadata, self.patch_values) + (
            ()
            if self.calibration_coefficients is None
            else (self.calibration_coefficients,)
        )
        for artifact in artifacts:
            if (
                not artifact.data
                or hashlib.sha256(artifact.data).hexdigest() != artifact.sha256
            ):
                raise ValueError("surface-flux viewer artifact hash is invalid.")
        try:
            payload = json.loads(self.metadata.data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("surface-flux metadata is not valid JSON.") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_id") != "fspm-optics.surface-flux-display"
            or payload.get("schema_version") != expected_version
        ):
            raise ValueError(
                "surface-flux metadata filename and schema version disagree."
            )

    @property
    def schema_version(self) -> int:
        """Return the authenticated metadata schema selected by its fixed route."""

        return {
            "surface-flux/metadata.v1.json": 1,
            "surface-flux/metadata.v2.json": 2,
            "surface-flux/metadata.v3.json": 3,
        }[self.metadata.filename]

    @property
    def files(self) -> tuple[BinaryDisplayArtifact, ...]:
        return (self.metadata, self.patch_values) + (
            ()
            if self.calibration_coefficients is None
            else (self.calibration_coefficients,)
        )


@dataclass(frozen=True, slots=True)
class PpfdHeatmapViewerArtifacts:
    """Exact Stage A metadata/scatter bytes plus their viewer scene binding."""

    metadata: BinaryDisplayArtifact
    scalar_field: BinaryDisplayArtifact
    scene_reference: Mapping[str, object]

    def __post_init__(self) -> None:
        if (
            self.metadata.filename != "ppfd-heatmap/visualization.json"
            or self.scalar_field.filename
            != "ppfd-heatmap/ppfd-scatter.f32le.bin"
        ):
            raise ValueError("PPFD heatmap viewer filenames are incompatible.")
        for artifact in (self.metadata, self.scalar_field):
            if (
                not artifact.data
                or hashlib.sha256(artifact.data).hexdigest() != artifact.sha256
            ):
                raise ValueError("PPFD heatmap viewer artifact hash is invalid.")

    @property
    def files(self) -> tuple[BinaryDisplayArtifact, ...]:
        return (self.metadata, self.scalar_field)


@dataclass(frozen=True, slots=True)
class ViewerArtifactSet:
    """Canonical juvenile profile plus one run-specific plant scene."""

    profile_id: str
    sampling_profile_id: str
    geometry: BinaryDisplayArtifact
    identity_map: BinaryDisplayArtifact
    receivers: BinaryDisplayArtifact
    profile_manifest: BinaryDisplayArtifact
    scene_manifest: BinaryDisplayArtifact
    instance_translations: BinaryDisplayArtifact
    surface_flux: SurfaceFluxViewerArtifacts | None = None
    ppfd_heatmap: PpfdHeatmapViewerArtifacts | None = None

    @property
    def profile_files(self) -> tuple[BinaryDisplayArtifact, ...]:
        return (
            self.profile_manifest,
            self.geometry,
            self.identity_map,
            self.receivers,
        )

    @property
    def scene_files(self) -> tuple[BinaryDisplayArtifact, ...]:
        surface = () if self.surface_flux is None else self.surface_flux.files
        heatmap = () if self.ppfd_heatmap is None else self.ppfd_heatmap.files
        return (
            self.scene_manifest,
            self.instance_translations,
            *surface,
            *heatmap,
        )
