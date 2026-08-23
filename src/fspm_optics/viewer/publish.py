"""Atomic publication of the self-contained juvenile scientific viewer."""

from __future__ import annotations

import hashlib
from importlib import resources
import os
from pathlib import Path
import shutil
from typing import Any, Mapping

from fspm_optics.plants import (
    NaturalFitLayoutPlan,
    REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID,
)
from fspm_optics.plants.models import PlantMesh
from fspm_optics.runtime_paths import is_default_managed_runtime_descendant
from fspm_optics.viewer.artifacts import (
    PROFILE_ID,
    build_run_viewer_artifacts,
    validate_profile_artifacts,
    validate_run_scene_artifacts,
)
from fspm_optics.viewer.fixtures import (
    build_fixture_publication,
    validate_fixture_publication,
)
from fspm_optics.viewer.models import (
    PpfdHeatmapViewerArtifacts,
    SurfaceFluxViewerArtifacts,
)

_REQUIRED_STATIC_FILES = (
    "index.html",
    "styles.css",
    "main.js",
    "system-labels.js",
    "artifacts.js",
    "fixture-artifacts.js",
    "fixture-height-controller.js",
    "fixture-renderer.js",
    "ppfd-heatmap.js",
    "renderer.js",
    "camera.js",
    "identity.js",
    "receivers.js",
    "surface-flux.js",
    "target-coverage.js",
    "vendor/three.module.js",
    "vendor/three.core.js",
    "vendor/addons/controls/OrbitControls.js",
    "vendor/addons/environments/RoomEnvironment.js",
    "vendor/addons/loaders/GLTFLoader.js",
    "vendor/addons/utils/BufferGeometryUtils.js",
    "vendor/addons/utils/SkeletonUtils.js",
    "vendor/THREE-LICENSE.txt",
)


def validate_output_directory(output: str | Path) -> Path:
    """Return a safe absolute external output path that does not yet exist."""

    candidate = Path(output).expanduser()
    if not candidate.is_absolute():
        raise ValueError("viewer output must be an explicit absolute path.")
    resolved = candidate.resolve()
    repository = _repository_root()
    if (
        (resolved == repository or resolved.is_relative_to(repository))
        and not is_default_managed_runtime_descendant(resolved, repository)
    ):
        raise ValueError("viewer output must be outside the repository.")
    if resolved.exists():
        raise FileExistsError(
            f"refusing to overwrite existing viewer output: {resolved}"
        )
    staging = resolved.with_name(f".{resolved.name}.staging")
    if staging.exists():
        raise FileExistsError(f"refusing to overwrite staging path: {staging}")
    return resolved


def publish_run_viewer(
    output: str | Path,
    *,
    run_id: str,
    system_id: str,
    requested_length_ft: float,
    requested_width_ft: float,
    natural_fit: NaturalFitLayoutPlan,
    natural_fit_artifact_sha256: str,
    layout_identity: Mapping[str, object],
    mounting_height: Mapping[str, object] | None = None,
    run_information: Mapping[str, object] | None = None,
    surface_flux: SurfaceFluxViewerArtifacts | None = None,
    ppfd_heatmap: PpfdHeatmapViewerArtifacts | None = None,
    sampling_profile_id: str = (
        REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
    ),
    canonical_plant: PlantMesh | None = None,
) -> Path:
    """Publish one run-specific viewer bundle through staged promotion."""

    destination = validate_output_directory(output)
    source = resources.files("fspm_optics").joinpath("resources", "viewer")
    _validate_static_resources(source)
    fixture_publication = build_fixture_publication(
        run_id=run_id,
        system_id=system_id,
        requested_length_ft=requested_length_ft,
        requested_width_ft=requested_width_ft,
        layout_identity=layout_identity,
        mounting_height=mounting_height,
    )
    artifacts = build_run_viewer_artifacts(
        run_id=run_id,
        system_id=system_id,
        requested_length_ft=requested_length_ft,
        requested_width_ft=requested_width_ft,
        natural_fit=natural_fit,
        natural_fit_artifact_sha256=natural_fit_artifact_sha256,
        fixture_catalog_sha256=fixture_publication.catalog.sha256,
        fixture_catalog_byte_length=fixture_publication.catalog.byte_size,
        fixture_authoritative_layout_sha256=(
            fixture_publication.authoritative_layout_sha256
        ),
        fixture_plan_sha256=fixture_publication.fixture_plan_sha256,
        fixture_count=fixture_publication.fixture_count,
        fixture_asset_group_count=fixture_publication.asset_group_count,
        mounting_height=mounting_height,
        run_information=run_information,
        surface_flux=surface_flux,
        ppfd_heatmap=ppfd_heatmap,
        sampling_profile_id=sampling_profile_id,
        canonical_plant=canonical_plant,
    )
    staging = destination.with_name(f".{destination.name}.staging")
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        _copy_resource_tree(source, staging, excluded_names={"fixtures"})
        profile_root = staging / "profiles" / PROFILE_ID
        profile_root.mkdir(parents=True)
        for artifact in artifacts.profile_files:
            (profile_root / artifact.filename).write_bytes(artifact.data)
        for artifact in artifacts.scene_files:
            path = staging / artifact.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(artifact.data)
        fixture_root = staging / "fixtures"
        fixture_root.mkdir()
        (fixture_root / fixture_publication.catalog.filename).write_bytes(
            fixture_publication.catalog.data
        )
        for artifact in fixture_publication.files:
            path = staging / artifact.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(artifact.data)
        _validate_published_bundle(
            staging,
            run_id=run_id,
            system_id=system_id,
            requested_length_ft=requested_length_ft,
            requested_width_ft=requested_width_ft,
            natural_fit=natural_fit,
            natural_fit_artifact_sha256=natural_fit_artifact_sha256,
            layout_identity=layout_identity,
            mounting_height=mounting_height,
            run_information=run_information,
            fixture_catalog_sha256=fixture_publication.catalog.sha256,
            fixture_catalog_byte_length=fixture_publication.catalog.byte_size,
            fixture_authoritative_layout_sha256=(
                fixture_publication.authoritative_layout_sha256
            ),
            fixture_plan_sha256=fixture_publication.fixture_plan_sha256,
            fixture_count=fixture_publication.fixture_count,
            fixture_asset_group_count=fixture_publication.asset_group_count,
            surface_flux=surface_flux,
            ppfd_heatmap=ppfd_heatmap,
            sampling_profile_id=sampling_profile_id,
        )
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def _validate_published_bundle(
    root: Path,
    *,
    run_id: str,
    system_id: str,
    requested_length_ft: float,
    requested_width_ft: float,
    natural_fit: NaturalFitLayoutPlan,
    natural_fit_artifact_sha256: str,
    layout_identity: Mapping[str, object],
    mounting_height: Mapping[str, object] | None,
    run_information: Mapping[str, object] | None,
    fixture_catalog_sha256: str,
    fixture_catalog_byte_length: int,
    fixture_authoritative_layout_sha256: str,
    fixture_plan_sha256: str,
    fixture_count: int,
    fixture_asset_group_count: int,
    surface_flux: SurfaceFluxViewerArtifacts | None,
    ppfd_heatmap: PpfdHeatmapViewerArtifacts | None,
    sampling_profile_id: str,
) -> None:
    scene_path = root / "scene.v1.json"
    scene_bytes = scene_path.read_bytes()
    scene_files = {"instances.f32le.bin": (root / "instances.f32le.bin").read_bytes()}
    if surface_flux is not None:
        scene_files.update(
            {
                artifact.filename: (root / artifact.filename).read_bytes()
                for artifact in surface_flux.files
            }
        )
    if ppfd_heatmap is not None:
        scene_files.update(
            {
                artifact.filename: (root / artifact.filename).read_bytes()
                for artifact in ppfd_heatmap.files
            }
        )
    scene = validate_run_scene_artifacts(
        scene_bytes,
        scene_files,
        expected_run_id=run_id,
        expected_system_id=system_id,
        expected_requested_length_ft=requested_length_ft,
        expected_requested_width_ft=requested_width_ft,
        expected_natural_fit=natural_fit,
        expected_natural_fit_artifact_sha256=natural_fit_artifact_sha256,
        expected_fixture_catalog_sha256=fixture_catalog_sha256,
        expected_fixture_catalog_byte_length=fixture_catalog_byte_length,
        expected_fixture_authoritative_layout_sha256=(
            fixture_authoritative_layout_sha256
        ),
        expected_fixture_plan_sha256=fixture_plan_sha256,
        expected_fixture_count=fixture_count,
        expected_fixture_asset_group_count=fixture_asset_group_count,
        expected_mounting_height=mounting_height,
        expected_run_information=run_information,
        expected_surface_flux=surface_flux,
        expected_ppfd_heatmap=ppfd_heatmap,
        expected_sampling_profile_id=sampling_profile_id,
    )
    catalog_path = root / "fixtures/catalog.v1.json"
    catalog_bytes = catalog_path.read_bytes()
    if hashlib.sha256(catalog_bytes).hexdigest() != fixture_catalog_sha256:
        raise ValueError("published fixture catalog failed SHA-256 validation.")
    fixture_root = root / "fixtures"
    if {path.name for path in fixture_root.iterdir()} != {
        "assets",
        "catalog.v1.json",
        "transforms",
    }:
        raise ValueError("published fixture root inventory is incompatible.")
    for directory_name in ("assets", "transforms"):
        directory = fixture_root / directory_name
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError("published fixture artifact directory is unsafe.")
    fixture_files = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for directory in (root / "fixtures/assets", root / "fixtures/transforms")
        for path in directory.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    fixture_entry_count = sum(
        1
        for directory in (fixture_root / "assets", fixture_root / "transforms")
        for _ in directory.iterdir()
    )
    if fixture_entry_count != len(fixture_files):
        raise ValueError("published fixture artifact inventory is unsafe.")
    validate_fixture_publication(
        catalog_bytes,
        fixture_files,
        expected_run_id=run_id,
        expected_system_id=system_id,
        expected_requested_length_ft=requested_length_ft,
        expected_requested_width_ft=requested_width_ft,
        expected_layout_identity=layout_identity,
        expected_mounting_height=mounting_height,
    )
    profile = scene.get("profile")
    if not isinstance(profile, dict) or profile.get("profile_id") != PROFILE_ID:
        raise ValueError("published scene profile reference is incompatible.")
    manifest_relative = profile.get("manifest")
    if manifest_relative != f"profiles/{PROFILE_ID}/profile.v1.json":
        raise ValueError("published scene profile path is incompatible.")
    manifest_path = root / manifest_relative
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != profile.get("manifest_sha256"):
        raise ValueError("published profile manifest failed SHA-256 validation.")
    profile_root = manifest_path.parent
    validate_profile_artifacts(
        manifest_bytes,
        {
            "geometry.glb": (profile_root / "geometry.glb").read_bytes(),
            "identity-map.v1.json": (
                profile_root / "identity-map.v1.json"
            ).read_bytes(),
            "receivers.f32le.bin": (
                profile_root / "receivers.f32le.bin"
            ).read_bytes(),
        },
    )
    for relative in _REQUIRED_STATIC_FILES:
        if not (root / relative).is_file():
            raise ValueError(f"published viewer resource is missing: {relative}")


def _validate_static_resources(source: Any) -> None:
    for relative in _REQUIRED_STATIC_FILES:
        node = source.joinpath(*relative.split("/"))
        if not node.is_file():
            raise FileNotFoundError(
                f"viewer resource {relative!r} is missing; run npm run vendor:three"
            )


def _copy_resource_tree(
    source: Any,
    destination: Path,
    *,
    excluded_names: set[str] | None = None,
) -> None:
    excluded = excluded_names or set()
    for child in source.iterdir():
        if child.name in excluded:
            continue
        target = destination / child.name
        if child.is_dir():
            target.mkdir()
            _copy_resource_tree(child, target)
        elif child.is_file():
            target.write_bytes(child.read_bytes())


def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / ".git").exists():
            return parent
    raise RuntimeError("unable to locate the fspm-optics repository root.")
