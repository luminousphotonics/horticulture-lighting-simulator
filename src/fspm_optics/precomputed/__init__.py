"""Versioned compact precomputed-playback bundles."""

from importlib import import_module

from .compact_bundle import (
    COMPACT_BUNDLE_SCHEMA_ID,
    COMPACT_BUNDLE_SCHEMA_VERSION,
    CompactBundleError,
    CompactBundleExport,
    CompactBundleStatus,
    CompactBundleValidation,
    CompactPlayback,
    BundleSizeReport,
    export_compact_bundle,
    load_compact_bundle,
    materialize_compact_playback,
    measure_compact_bundle,
    normalize_live_public_payload,
    validate_compact_bundle,
)
from .committed_playback import (
    HISTORICAL_PLAN_IDENTITY_SHA256,
    CommittedPlaybackCase,
    CommittedPlaybackPlan,
    build_committed_playback_plan,
    inspect_committed_playback_catalog,
)
from .contracts import (
    FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S,
    canonical_room_domain,
)
from .playback import (
    FixedPlaybackResolution,
    RequestedCompactPlayback,
    load_fixed_playback,
    resolve_fixed_case,
)
from .target_adjustment import (
    TARGET_ADJUSTMENT_CAPABILITY,
    TARGET_ADJUSTMENT_SCHEMA_ID,
    TARGET_ADJUSTMENT_SCHEMA_VERSION,
    TargetAdjustedCompactPlayback,
    TargetAdjustment,
    derive_target_adjusted_playback,
    validate_requested_target_ppfd,
)

__all__ = [
    "COMPACT_BUNDLE_SCHEMA_ID",
    "COMPACT_BUNDLE_SCHEMA_VERSION",
    "BundleSizeReport",
    "CompactBundleError",
    "CompactBundleExport",
    "CompactBundleStatus",
    "CompactBundleValidation",
    "CompactPlayback",
    "export_compact_bundle",
    "load_compact_bundle",
    "materialize_compact_playback",
    "measure_compact_bundle",
    "normalize_live_public_payload",
    "validate_compact_bundle",
    "FixedSweepCase",
    "FixedSweepPlan",
    "FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S",
    "build_fixed_sweep_plan",
    "CommittedPlaybackCase",
    "CommittedPlaybackPlan",
    "HISTORICAL_PLAN_IDENTITY_SHA256",
    "build_committed_playback_plan",
    "inspect_committed_playback_catalog",
    "canonical_room_domain",
    "FixedPlaybackResolution",
    "RequestedCompactPlayback",
    "load_fixed_playback",
    "resolve_fixed_case",
    "TARGET_ADJUSTMENT_CAPABILITY",
    "TARGET_ADJUSTMENT_SCHEMA_ID",
    "TARGET_ADJUSTMENT_SCHEMA_VERSION",
    "TargetAdjustedCompactPlayback",
    "TargetAdjustment",
    "derive_target_adjusted_playback",
    "validate_requested_target_ppfd",
]


_GENERATION_EXPORTS = frozenset(
    {"FixedSweepCase", "FixedSweepPlan", "build_fixed_sweep_plan"}
)


def __getattr__(name: str) -> object:
    if name not in _GENERATION_EXPORTS:
        raise AttributeError(name)
    module = import_module("fspm_optics.precomputed.fixed_plan")
    value = getattr(module, name)
    globals()[name] = value
    return value
