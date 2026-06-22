"""Shared package, resource, and runtime paths for Radiance code."""

from __future__ import annotations

from rad_rebuild.radiance.settings import get_settings


_SETTINGS = get_settings()
_PATHS = _SETTINGS.paths

PACKAGE_ROOT = _PATHS.package_root
REPO_ROOT = _PATHS.repo_root

RADIANCE_DATA_ROOT = _PATHS.data_root
RADIANCE_CURVE_DATA_ROOT = _PATHS.curve_data_root
RADIANCE_IES_ROOT = _PATHS.ies_root
RADIANCE_ARCHIVES_ROOT = _PATHS.archives_root

RADIANCE_SCRIPTS_ROOT = _PATHS.scripts_root
RADIANCE_DOCKER_ROOT = _PATHS.docker_root

RADIANCE_OUTPUT_ROOT = _PATHS.output_root
RADIANCE_RUNTIME_STATE_ROOT = _PATHS.runtime_state_root
RADIANCE_BASIS_OUTPUT_ROOT = _PATHS.basis_output_root
RADIANCE_VISUALIZATION_OUTPUT_ROOT = _PATHS.visualization_output_root
RADIANCE_CACHE_ROOT = _PATHS.cache_root

RADIANCE_PACKAGE_ROOT = PACKAGE_ROOT / "radiance"
RADIANCE_BACKEND_PACKAGE_ROOT = RADIANCE_PACKAGE_ROOT / "backend"
RADIANCE_ENGINE_PACKAGE_ROOT = RADIANCE_PACKAGE_ROOT / "engine"
