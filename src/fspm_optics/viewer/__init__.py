"""Static scientific viewer publication for the juvenile Rex profile."""

from .artifacts import (
    ViewerArtifactSet,
    build_run_viewer_artifacts,
    validate_profile_artifacts,
    validate_run_scene_artifacts,
)
from .publish import publish_run_viewer, validate_output_directory
from .fixtures import ASSET_REGISTRY, build_fixture_publication

__all__ = [
    "ViewerArtifactSet",
    "ASSET_REGISTRY",
    "build_fixture_publication",
    "build_run_viewer_artifacts",
    "publish_run_viewer",
    "validate_output_directory",
    "validate_profile_artifacts",
    "validate_run_scene_artifacts",
]
