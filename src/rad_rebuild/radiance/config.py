"""Central Radiance configuration constants and lightweight validators."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from rad_rebuild.radiance.domain import (
    ExecutionMode,
    LayoutMode,
    QualityPreset,
    SystemMode,
    canonicalize_execution_mode as _domain_canonicalize_execution_mode,
    canonicalize_quality_preset as _domain_canonicalize_quality_preset,
    canonicalize_system_mode,
    overlay_for_mode as _domain_overlay_for_mode,
)

PROJECT_SLUG = "horticulture-lighting-simulator"
BACKEND_SERVICE_NAME = "horticulture-lighting-simulator-api"
BACKEND_API_TITLE = "Horticulture Lighting Simulator API"
BACKEND_VERSION = "0.1.0"

ENV_RADIANCE_IMAGE = "RADIANCE_IMAGE"
ENV_RADIANCE_HOST = "RADIANCE_HOST"
ENV_RADIANCE_PORT = "RADIANCE_PORT"
ENV_RADIANCE_CORS_ALLOW_ORIGINS = "RADIANCE_CORS_ALLOW_ORIGINS"
ENV_RADIANCE_ENABLE_LIVE_EXECUTION = "RADIANCE_ENABLE_LIVE_EXECUTION"
ENV_RADIANCE_MAX_REQUEST_BYTES = "RADIANCE_MAX_REQUEST_BYTES"
ENV_RESEARCH_WEB_PORT = "RADIANCE_RESEARCH_WEB_PORT"
ENV_RESEARCH_BACKEND_HOST = "RADIANCE_RESEARCH_BACKEND_HOST"
ENV_RESEARCH_BACKEND_PORT = "RADIANCE_RESEARCH_BACKEND_PORT"
ENV_RESEARCH_API_BASE = "RADIANCE_RESEARCH_API_BASE"
ENV_DEPLOYMENT_MODE = "RAD_REBUILD_DEPLOYMENT_MODE"

DEPLOYMENT_MODE_PRODUCTION = "production"

DEFAULT_DOCKER_IMAGE = "rad-rebuild-radiance:local"
DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8786
DEFAULT_WEB_PORT = 5001
DEFAULT_MAX_REQUEST_BYTES = 64 * 1024

PUBLIC_PRECOMPUTED_MIN_FT = 10
PUBLIC_PRECOMPUTED_MAX_FT = 20
PUBLIC_DEFAULT_LENGTH_FT = 10
PUBLIC_DEFAULT_WIDTH_FT = 10
PRECOMPUTED_FULL_DATASET_SIZE_TEXT = "about 310 MB total"
PRECOMPUTED_DOWNLOAD_COMMAND = "python scripts/radiance/download_precomputed.py --dataset full"

EXECUTION_MODE_PRECOMPUTED = ExecutionMode.PRECOMPUTED.value
EXECUTION_MODE_LIVE_DOCKER = ExecutionMode.LIVE_DOCKER.value
EXECUTION_MODE_LIVE_LOCAL = ExecutionMode.LIVE_LOCAL.value
DEFAULT_EXECUTION_MODE = EXECUTION_MODE_PRECOMPUTED

EXECUTION_MODE_ALIASES = {
    "": DEFAULT_EXECUTION_MODE,
    "precomputed": EXECUTION_MODE_PRECOMPUTED,
    "cached": EXECUTION_MODE_PRECOMPUTED,
    "demo": EXECUTION_MODE_PRECOMPUTED,
    "live": EXECUTION_MODE_LIVE_DOCKER,
    "docker": EXECUTION_MODE_LIVE_DOCKER,
    "live_docker": EXECUTION_MODE_LIVE_DOCKER,
    "local": EXECUTION_MODE_LIVE_LOCAL,
    "live_local": EXECUTION_MODE_LIVE_LOCAL,
    "local_radiance": EXECUTION_MODE_LIVE_LOCAL,
    "live_radiance": EXECUTION_MODE_LIVE_LOCAL,
}
VALID_EXECUTION_MODES = (
    *(mode.value for mode in ExecutionMode),
)

MODE_SMD = SystemMode.SMD.value
MODE_COMPETITOR = SystemMode.COMPETITOR.value
MODE_HPS = SystemMode.HPS.value

ACTIVE_RADIANCE_MODES = tuple(mode.value for mode in SystemMode)
ACCEPTED_RADIANCE_MODES = ACTIVE_RADIANCE_MODES
PUBLIC_LIVE_SUPPORTED_MODES = (MODE_SMD,)
PUBLIC_LIVE_UNSUPPORTED_MODE_MESSAGE = (
    "Live Radiance modes in the public repository support only the Proposed LED System. "
    "Conventional LED and 1000W HPS remain available in precomputed mode because public "
    "live execution does not include proprietary IES assets."
)

RADIANCE_MODE_ALIASES = {
    "smd": MODE_SMD,
    "proposed": MODE_SMD,
    "proposed_led": MODE_SMD,
    "proposed_led_system": MODE_SMD,
    "our_led": MODE_SMD,
    "our_led_system": MODE_SMD,
    "conventional": MODE_COMPETITOR,
    "conventional_led": MODE_COMPETITOR,
    "conventional_led_system": MODE_COMPETITOR,
    "competitor": MODE_COMPETITOR,
    "spydr": MODE_COMPETITOR,
    "spydr3": MODE_COMPETITOR,
    "1000w_hps": MODE_HPS,
    "1000w_de_hps": MODE_HPS,
    "1000w de hps": MODE_HPS,
    "1000w hps": MODE_HPS,
    "de_hps": MODE_HPS,
    "de hps": MODE_HPS,
    "hps": MODE_HPS,
    "gavita": MODE_HPS,
    "gavita_hps": MODE_HPS,
    "gavita hps": MODE_HPS,
}

RADIANCE_MODE_LABELS = {
    MODE_SMD: "Proposed LED System",
    MODE_COMPETITOR: "Conventional LED System",
    MODE_HPS: MODE_HPS,
}

MODE_OUTPUT_DIRS = {
    MODE_SMD: "ppfd_visualizations_proposed",
    MODE_COMPETITOR: "ppfd_visualizations_conventional",
    MODE_HPS: "ppfd_visualizations_hps",
}

MODE_OVERLAYS = {
    MODE_SMD: "smd",
    MODE_COMPETITOR: "spydr3",
    MODE_HPS: "hps",
}

OVERLAY_ALIASES = {
    "conventional": "spydr3",
    "competitor": "spydr3",
    "spydr": "spydr3",
    "spydr3": "spydr3",
    "hps": "hps",
    "de_hps": "hps",
    "1000w_de_hps": "hps",
    "1000w de hps": "hps",
    "smd": "smd",
}

QUALITY_PRESET_STANDARD = QualityPreset.STANDARD.value
QUALITY_PRESET_DIRECT = QualityPreset.DIRECT.value
QUALITY_PRESET_QUALITY = QualityPreset.QUALITY.value
QUALITY_PRESET_RIGOROUS = QualityPreset.RIGOROUS.value

COMPETITOR_LAYOUT_FULL = LayoutMode.FULL.value
COMPETITOR_LAYOUT_PRACTICAL = LayoutMode.PRACTICAL.value

COMPETITOR_FIXTURE_INPUT_W = 800.0
COMPETITOR_FIXTURE_PPE_UMOL_PER_J = 2.8
COMPETITOR_FIXTURE_PPF_UMOL_S = COMPETITOR_FIXTURE_INPUT_W * COMPETITOR_FIXTURE_PPE_UMOL_PER_J


@dataclass(frozen=True)
class SelectOption:
    value: str
    label: str


ACTIVE_RADIANCE_MODE_OPTIONS = tuple(
    SelectOption(value=mode, label=RADIANCE_MODE_LABELS[mode]) for mode in ACTIVE_RADIANCE_MODES
)
EXECUTION_MODE_OPTIONS = (
    SelectOption(value=EXECUTION_MODE_PRECOMPUTED, label="Precomputed"),
    SelectOption(value=EXECUTION_MODE_LIVE_DOCKER, label="Live - Docker"),
    SelectOption(value=EXECUTION_MODE_LIVE_LOCAL, label="Live - Local Radiance"),
)
PUBLIC_EXECUTION_MODE_OPTIONS = (
    SelectOption(value=EXECUTION_MODE_PRECOMPUTED, label="Precomputed"),
)

RUNTIME_ENV_VARS = (
    "RADIANCE_DATA_ROOT",
    "RADIANCE_CURVE_DATA_ROOT",
    "RADIANCE_IES_ROOT",
    "RADIANCE_OUTPUT_ROOT",
    "RADIANCE_BASIS_OUTPUT_ROOT",
    "RADIANCE_RUNTIME_STATE_ROOT",
    "RADIANCE_VISUALIZATION_OUTPUT_ROOT",
    "RADIANCE_CACHE_ROOT",
)


def _token(value: str | None) -> str:
    return (value or "").strip().lower().replace(" ", "_").replace("-", "_")


def is_production_deployment(env: Mapping[str, str] | None = None) -> bool:
    if env is None:
        import os

        env = os.environ
    return _token(env.get(ENV_DEPLOYMENT_MODE)) in {DEPLOYMENT_MODE_PRODUCTION, "prod"}


def canonicalize_execution_mode(raw: str | None) -> str:
    try:
        return _domain_canonicalize_execution_mode(raw).value
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def canonicalize_quality_preset(raw: str | None) -> str:
    try:
        return _domain_canonicalize_quality_preset(raw).value
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def canonicalize_radiance_mode(raw: str | None, *, include_legacy: bool = True) -> str:
    try:
        return canonicalize_system_mode(raw).value
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def output_dir_for_mode(mode: str) -> str:
    return MODE_OUTPUT_DIRS[canonicalize_radiance_mode(mode)]


def overlay_for_mode(mode: str, overlay: str) -> str:
    try:
        return _domain_overlay_for_mode(mode, overlay).value
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
