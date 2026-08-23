"""Deterministic modular horticultural-light layouts."""

from .domain import (
    ControlZone,
    FixtureAssembly,
    FixtureConnector,
    LayoutPoint,
    LayoutTopology,
    ModularLayout,
)
from .modular import generate_modular_layout
from .mode import (
    DEFAULT_PROPOSED_LAYOUT_MODE,
    PROPOSED_LINEAR_LAYOUT_ENV_VAR,
    PROPOSED_LAYOUT_MODE_ENV_VAR,
    ProposedLayoutMode,
    resolve_proposed_layout_mode,
    resolve_proposed_layout_startup_environment,
)
from .overlay import (
    AuthoritativeOverlayPlan,
    OverlayLine,
    OverlayPlanError,
    OverlayRectangle,
    OverlayStrokeColorRole,
)
from .ring import (
    DEFAULT_PROPOSED_RING_MODE,
    PROPOSED_MODULE_PATTERN_IDS,
    ProposedRingMode,
    effective_topology_order,
    proposed_module_pattern_id,
    resolve_proposed_ring_mode,
)

__all__ = [
    "ControlZone",
    "FixtureAssembly",
    "FixtureConnector",
    "LayoutPoint",
    "LayoutTopology",
    "ModularLayout",
    "DEFAULT_PROPOSED_LAYOUT_MODE",
    "DEFAULT_PROPOSED_RING_MODE",
    "PROPOSED_LINEAR_LAYOUT_ENV_VAR",
    "PROPOSED_LAYOUT_MODE_ENV_VAR",
    "ProposedLayoutMode",
    "PROPOSED_MODULE_PATTERN_IDS",
    "ProposedRingMode",
    "AuthoritativeOverlayPlan",
    "OverlayLine",
    "OverlayPlanError",
    "OverlayRectangle",
    "OverlayStrokeColorRole",
    "generate_modular_layout",
    "effective_topology_order",
    "proposed_module_pattern_id",
    "resolve_proposed_layout_mode",
    "resolve_proposed_ring_mode",
    "resolve_proposed_layout_startup_environment",
]
