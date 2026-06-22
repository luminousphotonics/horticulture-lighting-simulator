"""Validated process settings for Radiance adapters.

Settings are the only Radiance layer that reads process environment. Pure
domain/config modules consume typed values passed from here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from rad_rebuild.radiance.config import (
    DEFAULT_BACKEND_HOST,
    DEFAULT_BACKEND_PORT,
    DEFAULT_DOCKER_IMAGE,
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_WEB_PORT,
    ENV_RADIANCE_CORS_ALLOW_ORIGINS,
    ENV_RADIANCE_ENABLE_LIVE_EXECUTION,
    ENV_RADIANCE_HOST,
    ENV_RADIANCE_IMAGE,
    ENV_RADIANCE_MAX_REQUEST_BYTES,
    ENV_RADIANCE_PORT,
    ENV_RESEARCH_API_BASE,
    ENV_RESEARCH_BACKEND_HOST,
    ENV_RESEARCH_BACKEND_PORT,
    ENV_RESEARCH_WEB_PORT,
    is_production_deployment,
)
from rad_rebuild.radiance.domain import PrecomputedMode, canonicalize_precomputed_mode


PRECOMPUTED_MODE_ENV = "RADIANCE_PRECOMPUTED_MODE"
PRECOMPUTED_ROOT_ENV = "RADIANCE_PRECOMPUTED_ROOT"


@dataclass(frozen=True)
class RadiancePathSettings:
    repo_root: Path
    package_root: Path
    data_root: Path
    curve_data_root: Path
    ies_root: Path
    archives_root: Path
    scripts_root: Path
    docker_root: Path
    output_root: Path
    runtime_state_root: Path
    basis_output_root: Path
    visualization_output_root: Path
    cache_root: Path
    precomputed_root: Path


@dataclass(frozen=True)
class RadianceSettings:
    paths: RadiancePathSettings
    docker_image: str
    backend_host: str
    backend_port: int
    web_port: int
    max_request_bytes: int
    cors_allow_origins: tuple[str, ...]
    live_execution_enabled: bool
    session_artifact_retention_s: int
    runtime_cleanup_interval_s: int
    completed_job_retention_s: int
    job_queue_limit: int
    job_worker_count: int
    job_log_max_lines: int
    job_log_max_bytes: int
    job_timeout_s: float
    job_sse_client_limit: int
    file_cache_max_entries: int
    precomputed_mode: PrecomputedMode | None
    render_disk_path: Path | None
    research_backend_host: str
    research_backend_port: int
    research_api_base: str
    proxy_timeout_s: float
    proxy_stream_timeout_s: float
    radiance_python: str | None
    docker_bin: str | None
    docker_platform: str | None
    docker_user: str | None
    docker_workdir: str
    docker_image_tar: str | None
    use_docker: bool
    smd_white_vf_csv: Path
    smd_white_ppe_csv: Path
    smd_red_vf_csv: Path
    smd_red_ppe_csv: Path


def _find_repo_root(package_root: Path, env: Mapping[str, str]) -> Path:
    env_root = env.get("RAD_REBUILD_REPO_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()
    here = package_root.resolve()
    for parent in [here, *here.parents]:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "rad_rebuild").is_dir():
            return parent
    return here.parents[1]


def _path(env: Mapping[str, str], name: str, default: Path) -> Path:
    raw = env.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default.resolve()


def _path_with_legacy(env: Mapping[str, str], name: str, legacy_name: str, default: Path) -> Path:
    raw = env.get(name, "").strip() or env.get(legacy_name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default.resolve()


def _optional_path(env: Mapping[str, str], name: str) -> Path | None:
    raw = env.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def _int(env: Mapping[str, str], name: str, default: int, *, minimum: int = 0) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return value


def _float(env: Mapping[str, str], name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum:g}.")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean flag.")


def _csv_tuple(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip() and item.strip() != "*")


def _optional_str(env: Mapping[str, str], name: str) -> str | None:
    raw = env.get(name, "").strip()
    return raw or None


def load_settings(env: Mapping[str, str] | None = None) -> RadianceSettings:
    source = dict(os.environ if env is None else env)
    production_deployment = is_production_deployment(source)
    package_root = Path(__file__).resolve().parents[1]
    repo_root = _find_repo_root(package_root, source)
    data_root = _path(source, "RADIANCE_DATA_ROOT", repo_root / "data" / "radiance")
    output_root = _path_with_legacy(source, "RADIANCE_OUTPUT_ROOT", "RADIANCE_ROOT", repo_root / "outputs" / "radiance")
    paths = RadiancePathSettings(
        repo_root=repo_root,
        package_root=package_root,
        data_root=data_root,
        curve_data_root=_path(source, "RADIANCE_CURVE_DATA_ROOT", data_root / "curve_data"),
        ies_root=_path(source, "RADIANCE_IES_ROOT", data_root / "ies_sources"),
        archives_root=_path(source, "RADIANCE_ARCHIVES_ROOT", data_root / "archives"),
        scripts_root=_path(source, "RADIANCE_SCRIPTS_ROOT", repo_root / "scripts" / "radiance"),
        docker_root=_path(source, "RADIANCE_DOCKER_ROOT", repo_root / "docker" / "radiance"),
        output_root=output_root,
        runtime_state_root=_path(source, "RADIANCE_RUNTIME_STATE_ROOT", output_root / "runtime_state"),
        basis_output_root=_path(source, "RADIANCE_BASIS_OUTPUT_ROOT", output_root / "basis"),
        visualization_output_root=_path(source, "RADIANCE_VISUALIZATION_OUTPUT_ROOT", output_root / "visualizations"),
        cache_root=_path(source, "RADIANCE_CACHE_ROOT", output_root / "cache"),
        precomputed_root=_path(source, PRECOMPUTED_ROOT_ENV, data_root / "precomputed"),
    )
    precomputed_raw = source.get(PRECOMPUTED_MODE_ENV, "").strip()
    precomputed_mode = canonicalize_precomputed_mode(precomputed_raw) if precomputed_raw else None
    if production_deployment:
        precomputed_mode = PrecomputedMode.ONLY
    configured_origins = _csv_tuple(source.get(ENV_RADIANCE_CORS_ALLOW_ORIGINS, ""))
    return RadianceSettings(
        paths=paths,
        docker_image=source.get(ENV_RADIANCE_IMAGE, DEFAULT_DOCKER_IMAGE).strip() or DEFAULT_DOCKER_IMAGE,
        backend_host=source.get(ENV_RADIANCE_HOST, DEFAULT_BACKEND_HOST).strip() or DEFAULT_BACKEND_HOST,
        backend_port=_int(source, ENV_RADIANCE_PORT, DEFAULT_BACKEND_PORT, minimum=1),
        web_port=_int(source, ENV_RESEARCH_WEB_PORT, DEFAULT_WEB_PORT, minimum=1),
        max_request_bytes=_int(source, ENV_RADIANCE_MAX_REQUEST_BYTES, DEFAULT_MAX_REQUEST_BYTES, minimum=1),
        cors_allow_origins=configured_origins,
        live_execution_enabled=False if production_deployment else _bool(source, ENV_RADIANCE_ENABLE_LIVE_EXECUTION),
        session_artifact_retention_s=_int(source, "RADIANCE_SESSION_ARTIFACT_RETENTION_S", 86400, minimum=0),
        runtime_cleanup_interval_s=_int(source, "RADIANCE_RUNTIME_CLEANUP_INTERVAL_S", 300, minimum=0),
        completed_job_retention_s=_int(source, "RADIANCE_COMPLETED_JOB_RETENTION_S", 7200, minimum=0),
        job_queue_limit=_int(source, "RADIANCE_JOB_QUEUE_LIMIT", 8, minimum=1),
        job_worker_count=_int(source, "RADIANCE_JOB_WORKER_COUNT", 2, minimum=1),
        job_log_max_lines=_int(source, "RADIANCE_JOB_LOG_MAX_LINES", 2000, minimum=1),
        job_log_max_bytes=_int(source, "RADIANCE_JOB_LOG_MAX_BYTES", 2_000_000, minimum=1024),
        job_timeout_s=_float(source, "RADIANCE_JOB_TIMEOUT_S", 900.0, minimum=0.0),
        job_sse_client_limit=_int(source, "RADIANCE_JOB_SSE_CLIENT_LIMIT", 16, minimum=1),
        file_cache_max_entries=_int(source, "RADIANCE_FILE_CACHE_MAX_ENTRIES", 4096, minimum=1),
        precomputed_mode=precomputed_mode,
        render_disk_path=_optional_path(source, "RENDER_DISK_PATH"),
        research_backend_host=source.get(ENV_RESEARCH_BACKEND_HOST, DEFAULT_BACKEND_HOST).strip() or DEFAULT_BACKEND_HOST,
        research_backend_port=_int(source, ENV_RESEARCH_BACKEND_PORT, DEFAULT_BACKEND_PORT, minimum=1),
        research_api_base=source.get(ENV_RESEARCH_API_BASE, "").strip().rstrip("/"),
        proxy_timeout_s=_float(source, "RADIANCE_PROXY_TIMEOUT_S", 30.0, minimum=0.0),
        proxy_stream_timeout_s=_float(source, "RADIANCE_PROXY_STREAM_TIMEOUT_S", 300.0, minimum=0.0),
        radiance_python=_optional_str(source, "RADIANCE_PY") or _optional_str(source, "PYTHON"),
        docker_bin=_optional_str(source, "DOCKER_BIN"),
        docker_platform=_optional_str(source, "RADIANCE_DOCKER_PLATFORM"),
        docker_user=_optional_str(source, "RADIANCE_DOCKER_USER"),
        docker_workdir=source.get("RADIANCE_DOCKER_WORKDIR", "/tmp/radiance-work").strip() or "/tmp/radiance-work",
        docker_image_tar=_optional_str(source, "RADIANCE_IMAGE_TAR"),
        use_docker=False if production_deployment else _bool(source, "RADIANCE_USE_DOCKER", True),
        smd_white_vf_csv=_path(source, "SMD_WHITE_VF_CSV", paths.curve_data_root / "smd" / "white_fC_vs_fV.csv"),
        smd_white_ppe_csv=_path(source, "SMD_WHITE_PPE_CSV", paths.curve_data_root / "smd" / "white_ppe_vs_fC.csv"),
        smd_red_vf_csv=_path(source, "SMD_RED_VF_CSV", paths.curve_data_root / "smd" / "red_fC_vs_fV.csv"),
        smd_red_ppe_csv=_path(source, "SMD_RED_PPE_CSV", paths.curve_data_root / "smd" / "red_ppe_vs_fC.csv"),
    )


@lru_cache(maxsize=1)
def get_settings() -> RadianceSettings:
    return load_settings()
