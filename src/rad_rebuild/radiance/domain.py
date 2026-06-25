"""Canonical typed Radiance domain contracts.

This module is intentionally pure: it validates and serializes request
contracts without reading process environment or touching runtime paths.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from rad_rebuild.radiance.engine.plants.config import PlantGeometryConfig


class DomainValueError(ValueError):
    """Raised when an external contract value cannot be canonicalized."""


class SystemMode(str, Enum):
    SMD = "SMD"
    COMPETITOR = "Competitor"
    HPS = "1000W HPS"


class ExecutionMode(str, Enum):
    PRECOMPUTED = "precomputed"
    LIVE_DOCKER = "live_docker"
    LIVE_LOCAL = "live_local"


class QualityPreset(str, Enum):
    DIRECT = "direct"
    STANDARD = "standard"
    QUALITY = "quality"
    RIGOROUS = "rigorous"


class LayoutMode(str, Enum):
    FULL = "full"
    PRACTICAL = "practical"
    SQUARE = "square"
    RECT_RECT = "rect_rect"
    EXACT_TILED = "exact_tiled"
    GRID = "grid"


class BasisBackend(str, Enum):
    RTRACE = "rtrace"
    RCONTRIB_LEGACY = "rcontrib_legacy"
    RCONTRIB_MCPT = "rcontrib_mcpt"


class RadianceAction(str, Enum):
    UNIFORMITY = "uniformity"
    COMPETITOR = "competitor"
    VISUALIZE = "visualize"
    ALL = "all"
    METRICS = "metrics"


class Overlay(str, Enum):
    AUTO = "auto"
    SMD = "smd"
    SPYDR3 = "spydr3"
    HPS = "hps"


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class PrecomputedMode(str, Enum):
    OFF = "off"
    PREFER = "prefer"
    ONLY = "only"


MODE_SMD = SystemMode.SMD.value
MODE_COMPETITOR = SystemMode.COMPETITOR.value
MODE_HPS = SystemMode.HPS.value

EXECUTION_MODE_PRECOMPUTED = ExecutionMode.PRECOMPUTED.value
EXECUTION_MODE_LIVE_DOCKER = ExecutionMode.LIVE_DOCKER.value
EXECUTION_MODE_LIVE_LOCAL = ExecutionMode.LIVE_LOCAL.value
DEFAULT_EXECUTION_MODE = EXECUTION_MODE_PRECOMPUTED

QUALITY_PRESET_STANDARD = QualityPreset.STANDARD.value
QUALITY_PRESET_DIRECT = QualityPreset.DIRECT.value
QUALITY_PRESET_QUALITY = QualityPreset.QUALITY.value
QUALITY_PRESET_RIGOROUS = QualityPreset.RIGOROUS.value

COMPETITOR_LAYOUT_FULL = LayoutMode.FULL.value
COMPETITOR_LAYOUT_PRACTICAL = LayoutMode.PRACTICAL.value

PUBLIC_PRECOMPUTED_MIN_FT = 10
PUBLIC_PRECOMPUTED_MAX_FT = 30
PUBLIC_DEFAULT_LENGTH_FT = 10
PUBLIC_DEFAULT_WIDTH_FT = 10

MAX_DIMENSION_FT = float(PUBLIC_PRECOMPUTED_MAX_FT)
MAX_TARGET_PPFD = 3000.0
MAX_MODULE_POWER_W = 200.0
MAX_FIXTURE_PPF_UMOL_S = 10000.0
MAX_FIXTURE_PPE_UMOL_PER_J = 10.0
MAX_MOUNT_Z_M = 3.0
MAX_HPS_COVERAGE_FT = 12.0
MAX_HPS_INPUT_WATTS = 2000.0
MAX_UTILITY_RATE_KWH = 5.0
MAX_STAGE_DAYS = 365.0
MAX_STAGE_HOURS_PER_DAY = 24.0
MAX_COST_STAGES = 8

FinitePositiveDimensionFt: TypeAlias = Annotated[float, Field(gt=0.0, le=MAX_DIMENSION_FT)]
FiniteTargetPpfd: TypeAlias = Annotated[float, Field(gt=0.0, le=MAX_TARGET_PPFD)]
FiniteModulePowerW: TypeAlias = Annotated[float, Field(ge=0.0, le=MAX_MODULE_POWER_W)]
FiniteFixturePpfUmolS: TypeAlias = Annotated[float, Field(gt=0.0, le=MAX_FIXTURE_PPF_UMOL_S)]
FinitePpeUmolJ: TypeAlias = Annotated[float, Field(gt=0.0, le=MAX_FIXTURE_PPE_UMOL_PER_J)]
FiniteMountHeightM: TypeAlias = Annotated[float, Field(gt=0.0, le=MAX_MOUNT_Z_M)]
FiniteHpsCoverageFt: TypeAlias = Annotated[float, Field(gt=0.0, le=MAX_HPS_COVERAGE_FT)]
FiniteHpsInputWatts: TypeAlias = Annotated[float, Field(gt=0.0, le=MAX_HPS_INPUT_WATTS)]
FiniteUtilityRateKwh: TypeAlias = Annotated[float, Field(ge=0.0, le=MAX_UTILITY_RATE_KWH)]
StageDays: TypeAlias = Annotated[float, Field(gt=0.0, le=MAX_STAGE_DAYS)]
StageHoursPerDay: TypeAlias = Annotated[float, Field(gt=0.0, le=MAX_STAGE_HOURS_PER_DAY)]
ActionValue: TypeAlias = Annotated[str, Field(json_schema_extra={"enum": [action.value for action in RadianceAction]})]
SystemModeValue: TypeAlias = Annotated[str, Field(json_schema_extra={"enum": [mode.value for mode in SystemMode]})]
ExecutionModeValue: TypeAlias = Annotated[str, Field(json_schema_extra={"enum": [mode.value for mode in ExecutionMode]})]
QualityPresetValue: TypeAlias = Annotated[str, Field(json_schema_extra={"enum": [preset.value for preset in QualityPreset]})]
LayoutModeValue: TypeAlias = Annotated[str, Field(json_schema_extra={"enum": [mode.value for mode in LayoutMode]})]
BasisBackendValue: TypeAlias = Annotated[str, Field(json_schema_extra={"enum": [backend.value for backend in BasisBackend]})]
OverlayValue: TypeAlias = Annotated[str, Field(json_schema_extra={"enum": [overlay.value for overlay in Overlay]})]
JobStateValue: TypeAlias = Annotated[str, Field(json_schema_extra={"enum": [state.value for state in JobState]})]

_SYSTEM_MODE_ALIASES = {
    "smd": SystemMode.SMD,
    "proposed": SystemMode.SMD,
    "proposed_led": SystemMode.SMD,
    "proposed_led_system": SystemMode.SMD,
    "our_led": SystemMode.SMD,
    "our_led_system": SystemMode.SMD,
    "conventional": SystemMode.COMPETITOR,
    "conventional_led": SystemMode.COMPETITOR,
    "conventional_led_system": SystemMode.COMPETITOR,
    "competitor": SystemMode.COMPETITOR,
    "spydr": SystemMode.COMPETITOR,
    "spydr3": SystemMode.COMPETITOR,
    "1000w_hps": SystemMode.HPS,
    "1000w_de_hps": SystemMode.HPS,
    "de_hps": SystemMode.HPS,
    "hps": SystemMode.HPS,
    "gavita": SystemMode.HPS,
    "gavita_hps": SystemMode.HPS,
}

_EXECUTION_MODE_ALIASES = {
    "": ExecutionMode.PRECOMPUTED,
    "precomputed": ExecutionMode.PRECOMPUTED,
    "cached": ExecutionMode.PRECOMPUTED,
    "demo": ExecutionMode.PRECOMPUTED,
    "live": ExecutionMode.LIVE_DOCKER,
    "docker": ExecutionMode.LIVE_DOCKER,
    "live_docker": ExecutionMode.LIVE_DOCKER,
    "local": ExecutionMode.LIVE_LOCAL,
    "live_local": ExecutionMode.LIVE_LOCAL,
    "local_radiance": ExecutionMode.LIVE_LOCAL,
    "live_radiance": ExecutionMode.LIVE_LOCAL,
}

_QUALITY_PRESET_ALIASES = {
    "": QualityPreset.STANDARD,
    "instant": QualityPreset.STANDARD,
    "fast": QualityPreset.STANDARD,
    "standard": QualityPreset.STANDARD,
    "direct": QualityPreset.DIRECT,
    "quality": QualityPreset.QUALITY,
    "rigorous": QualityPreset.RIGOROUS,
}

_BASIS_BACKEND_ALIASES = {
    "": BasisBackend.RTRACE,
    "default": BasisBackend.RTRACE,
    "rtrace": BasisBackend.RTRACE,
    "legacy": BasisBackend.RCONTRIB_LEGACY,
    "rcontrib_legacy": BasisBackend.RCONTRIB_LEGACY,
    "mcpt": BasisBackend.RCONTRIB_MCPT,
    "rcontrib_mcpt": BasisBackend.RCONTRIB_MCPT,
}

_LAYOUT_MODE_ALIASES = {
    "": LayoutMode.FULL,
    "full": LayoutMode.FULL,
    "practical": LayoutMode.PRACTICAL,
    "practical_coverage": LayoutMode.PRACTICAL,
    "square": LayoutMode.SQUARE,
    "rect_rect": LayoutMode.RECT_RECT,
    "grid": LayoutMode.GRID,
    "uniform": LayoutMode.GRID,
    "matrix": LayoutMode.GRID,
    "exact_tiled": LayoutMode.EXACT_TILED,
    "tiled": LayoutMode.EXACT_TILED,
    "exact": LayoutMode.EXACT_TILED,
    "modular": LayoutMode.EXACT_TILED,
}

_OVERLAY_ALIASES = {
    "auto": Overlay.AUTO,
    "smd": Overlay.SMD,
    "spydr3": Overlay.SPYDR3,
    "conventional": Overlay.SPYDR3,
    "competitor": Overlay.SPYDR3,
    "spydr": Overlay.SPYDR3,
    "hps": Overlay.HPS,
    "de_hps": Overlay.HPS,
    "1000w_de_hps": Overlay.HPS,
}

_PRECOMPUTED_MODE_ALIASES = {
    "off": PrecomputedMode.OFF,
    "prefer": PrecomputedMode.PREFER,
    "only": PrecomputedMode.ONLY,
}

_JOB_STATE_ALIASES = {state.value: state for state in JobState}
_JOB_STATE_ALIASES["completed"] = JobState.SUCCEEDED


def _token(value: str | None) -> str:
    return (value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _raw_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _unsupported(label: str, raw: object, expected: tuple[str, ...]) -> DomainValueError:
    valid = ", ".join(expected)
    return DomainValueError(f"Unsupported {label} {raw!r}. Expected one of: {valid}.")


def canonicalize_system_mode(raw: str | None, *, default: SystemMode | None = SystemMode.SMD) -> SystemMode:
    token = _token(raw)
    if not token and default is not None:
        return default
    try:
        return _SYSTEM_MODE_ALIASES[token]
    except KeyError as exc:
        raise _unsupported("mode", raw, tuple(mode.value for mode in SystemMode)) from exc


def canonicalize_execution_mode(raw: str | None) -> ExecutionMode:
    token = _token(raw)
    try:
        return _EXECUTION_MODE_ALIASES[token]
    except KeyError as exc:
        raise _unsupported("execution_mode", raw, tuple(mode.value for mode in ExecutionMode)) from exc


def canonicalize_quality_preset(raw: str | None) -> QualityPreset:
    token = _token(raw)
    try:
        return _QUALITY_PRESET_ALIASES[token]
    except KeyError as exc:
        raise _unsupported("simulation quality preset", raw, tuple(preset.value for preset in QualityPreset)) from exc


def canonicalize_basis_backend(raw: str | None) -> BasisBackend:
    token = _token(raw)
    try:
        return _BASIS_BACKEND_ALIASES[token]
    except KeyError as exc:
        raise _unsupported("SMD basis backend", raw, tuple(backend.value for backend in BasisBackend)) from exc


def canonicalize_layout_mode(raw: str | None) -> LayoutMode:
    token = _token((raw or "").replace("’", "'"))
    try:
        return _LAYOUT_MODE_ALIASES[token]
    except KeyError as exc:
        raise _unsupported("layout mode", raw, tuple(mode.value for mode in LayoutMode)) from exc


def canonicalize_competitor_layout(raw: str | None) -> LayoutMode:
    mode = canonicalize_layout_mode(raw)
    if mode not in {LayoutMode.FULL, LayoutMode.PRACTICAL}:
        raise _unsupported("competitor layout", raw, (LayoutMode.FULL.value, LayoutMode.PRACTICAL.value))
    return mode


def canonicalize_overlay(raw: str | None) -> Overlay:
    token = _token(raw)
    try:
        return _OVERLAY_ALIASES[token]
    except KeyError as exc:
        raise _unsupported("overlay", raw, tuple(overlay.value for overlay in Overlay)) from exc


def canonicalize_action(raw: str | None) -> RadianceAction:
    token = _token(raw)
    try:
        return RadianceAction(token)
    except ValueError as exc:
        raise _unsupported("action", raw, tuple(action.value for action in RadianceAction)) from exc


def canonicalize_job_state(raw: str | None) -> JobState:
    token = _token(raw)
    try:
        return _JOB_STATE_ALIASES[token]
    except KeyError as exc:
        raise _unsupported("job status", raw, tuple(state.value for state in JobState)) from exc


def canonicalize_precomputed_mode(raw: str | None) -> PrecomputedMode:
    token = _token(raw)
    try:
        return _PRECOMPUTED_MODE_ALIASES[token]
    except KeyError as exc:
        raise _unsupported("precomputed mode", raw, tuple(mode.value for mode in PrecomputedMode)) from exc


def default_overlay_for_mode(mode: str | SystemMode) -> Overlay:
    canonical = canonicalize_system_mode(mode.value if isinstance(mode, SystemMode) else str(mode))
    if canonical == SystemMode.COMPETITOR:
        return Overlay.SPYDR3
    if canonical == SystemMode.HPS:
        return Overlay.HPS
    return Overlay.SMD


def overlay_for_mode(mode: str | SystemMode, overlay: str | Overlay) -> Overlay:
    canonical_overlay = overlay if isinstance(overlay, Overlay) else canonicalize_overlay(str(overlay))
    if canonical_overlay == Overlay.AUTO:
        return default_overlay_for_mode(mode)
    return canonical_overlay


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or isinstance(value, str) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a JSON number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite.")
    if number == 0.0 and math.copysign(1.0, number) < 0:
        raise ValueError(f"{field_name} may not be negative zero.")
    return number


def _finite_int(value: object, field_name: str) -> int:
    number = _finite_number(value, field_name)
    if not number.is_integer():
        raise ValueError(f"{field_name} must be an integer.")
    return int(number)


class StrictBoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True, validate_default=True)


JsonValue: TypeAlias = object
JsonObject: TypeAlias = dict[str, JsonValue]


class LayoutRequest(StrictBoundaryModel):
    length_ft: FinitePositiveDimensionFt
    width_ft: FinitePositiveDimensionFt
    cv_enabled: bool = False
    include_svg: bool = True

    @field_validator("length_ft", "width_ft", mode="before")
    @classmethod
    def finite_dimensions(cls, value: object, info: ValidationInfo) -> float:
        return _finite_number(value, str(info.field_name))


class RadianceRunRequest(StrictBoundaryModel):
    action: ActionValue
    mode: SystemModeValue = MODE_SMD
    execution_mode: ExecutionModeValue = EXECUTION_MODE_PRECOMPUTED
    length_ft: FinitePositiveDimensionFt = float(PUBLIC_DEFAULT_LENGTH_FT)
    width_ft: FinitePositiveDimensionFt = float(PUBLIC_DEFAULT_WIDTH_FT)
    target_ppfd: FiniteTargetPpfd = 1000.0
    peak_capping_enabled: bool = False
    run_basis: bool = True
    w_min: FiniteModulePowerW = 0.0
    w_max: FiniteModulePowerW = 100.0
    sim_mode: QualityPresetValue = QUALITY_PRESET_STANDARD
    subpatch_grid: int = Field(default=1, ge=1, le=8)
    mount_z_m: FiniteMountHeightM = 0.4572
    overlay: OverlayValue = Overlay.AUTO.value
    smd_base_ring: int = Field(default=0, ge=0, le=16)
    basis_backend: BasisBackendValue = BasisBackend.RTRACE.value
    match_system_ppe: bool = False
    sp_ppf: FiniteFixturePpfUmolS = 2240.0
    sp_z_m: FiniteMountHeightM = 0.4572
    sp_ppe: FinitePpeUmolJ = 2.8
    competitor_layout: LayoutModeValue = LayoutMode.FULL.value
    hps_coverage_ft: FiniteHpsCoverageFt = 4.0
    hps_z_m: FiniteMountHeightM = 0.4572
    hps_fixture_ppf: FiniteFixturePpfUmolS = 1797.3999999999999
    hps_input_watts: FiniteHpsInputWatts = 1045.0
    hps_ies_variant: str = Field(default="karma", max_length=32)
    dialux_sensor_grid: bool = False
    plants_enabled: bool = False
    plant_seed: int | None = None
    plant_rows: int | None = None
    plant_columns: int | None = None
    plant_spacing_m: float | None = None
    plant_height_m: float | None = None
    plant_canopy_radius_m: float | None = None
    plant_leaf_count: int | None = None
    plant_growth_stage: float | None = None

    @field_validator(
        "length_ft",
        "width_ft",
        "target_ppfd",
        "w_min",
        "w_max",
        "mount_z_m",
        "sp_ppf",
        "sp_z_m",
        "sp_ppe",
        "hps_coverage_ft",
        "hps_z_m",
        "hps_fixture_ppf",
        "hps_input_watts",
        mode="before",
    )
    @classmethod
    def finite_numbers(cls, value: object, info: ValidationInfo) -> float:
        return _finite_number(value, str(info.field_name))

    @field_validator("subpatch_grid", "smd_base_ring", mode="before")
    @classmethod
    def finite_ints(cls, value: object, info: ValidationInfo) -> int:
        return _finite_int(value, str(info.field_name))

    @field_validator("plant_seed", "plant_rows", "plant_columns", "plant_leaf_count", mode="before")
    @classmethod
    def finite_optional_plant_ints(cls, value: object, info: ValidationInfo) -> int | None:
        if value is None:
            return None
        return _finite_int(value, str(info.field_name))

    @field_validator(
        "plant_spacing_m",
        "plant_height_m",
        "plant_canopy_radius_m",
        "plant_growth_stage",
        mode="before",
    )
    @classmethod
    def finite_optional_plant_numbers(cls, value: object, info: ValidationInfo) -> float | None:
        if value is None:
            return None
        return _finite_number(value, str(info.field_name))

    @field_validator("action", mode="before")
    @classmethod
    def valid_action(cls, value: object) -> str:
        return canonicalize_action(_raw_str(value)).value

    @field_validator("mode", mode="before")
    @classmethod
    def valid_mode(cls, value: object) -> str:
        return canonicalize_system_mode(_raw_str(value)).value

    @field_validator("execution_mode", mode="before")
    @classmethod
    def valid_execution_mode(cls, value: object) -> str:
        return canonicalize_execution_mode(_raw_str(value)).value

    @field_validator("sim_mode", mode="before")
    @classmethod
    def valid_sim_mode(cls, value: object) -> str:
        return canonicalize_quality_preset(_raw_str(value)).value

    @field_validator("basis_backend", mode="before")
    @classmethod
    def valid_basis_backend(cls, value: object) -> str:
        return canonicalize_basis_backend(_raw_str(value)).value

    @field_validator("competitor_layout", mode="before")
    @classmethod
    def valid_competitor_layout(cls, value: object) -> str:
        return canonicalize_competitor_layout(_raw_str(value)).value

    @field_validator("overlay", mode="before")
    @classmethod
    def valid_overlay(cls, value: object) -> str:
        return canonicalize_overlay(_raw_str(value)).value

    @field_validator("hps_ies_variant", mode="before")
    @classmethod
    def valid_hps_ies_variant(cls, value: object) -> str:
        variant = ("karma" if value is None else str(_raw_str(value)).strip().lower()) or "karma"
        if variant != "karma":
            raise ValueError("Unsupported HPS IES variant; expected one of karma.")
        return variant

    @model_validator(mode="after")
    def validate_cross_fields(self) -> "RadianceRunRequest":
        if self.w_max <= self.w_min:
            raise ValueError("w_max must be greater than w_min.")
        if self.mode != MODE_SMD and self.basis_backend != BasisBackend.RTRACE.value:
            raise ValueError("basis_backend applies only to SMD mode.")
        if self.mode == MODE_HPS and self.peak_capping_enabled:
            raise ValueError("peak_capping_enabled is not valid for 1000W HPS mode.")
        if self.mode == MODE_HPS and self.match_system_ppe:
            raise ValueError("match_system_ppe is not valid for 1000W HPS mode.")
        if self.mode == MODE_HPS and self.hps_coverage_ft not in {4.0, 5.0}:
            raise ValueError("hps_coverage_ft must be one of: 4.0, 5.0.")
        if self.plants_enabled:
            try:
                plant_geometry_config_from_request(self)
            except ValueError as exc:
                raise ValueError(f"Invalid plant geometry config: {exc}") from exc
        return self


def _request_value(source: Any, field_name: str, default: Any) -> Any:
    getter = source.get if isinstance(source, dict) else lambda name, fallback=None: getattr(source, name, fallback)
    value = getter(field_name, None)
    return default if value is None else value


def plant_geometry_config_from_request(source: Any) -> PlantGeometryConfig:
    """Resolve optional backend request plant fields into the Phase 01 config."""

    defaults = PlantGeometryConfig()
    return PlantGeometryConfig(
        seed=_request_value(source, "plant_seed", defaults.seed),
        plant_grid_rows=_request_value(source, "plant_rows", defaults.plant_grid_rows),
        plant_grid_columns=_request_value(
            source,
            "plant_columns",
            defaults.plant_grid_columns,
        ),
        plant_spacing_m=_request_value(source, "plant_spacing_m", defaults.plant_spacing_m),
        plant_height_m=_request_value(source, "plant_height_m", defaults.plant_height_m),
        canopy_radius_m=_request_value(
            source,
            "plant_canopy_radius_m",
            defaults.canopy_radius_m,
        ),
        leaf_count_per_plant=_request_value(
            source,
            "plant_leaf_count",
            defaults.leaf_count_per_plant,
        ),
        growth_stage=_request_value(source, "plant_growth_stage", defaults.growth_stage),
        optical=defaults.optical,
    )


class ElectricalCostStage(StrictBoundaryModel):
    name: str = Field(min_length=1, max_length=64)
    days: StageDays
    hours_per_day: StageHoursPerDay
    avg_ppfd: FiniteTargetPpfd

    @field_validator("days", "hours_per_day", "avg_ppfd", mode="before")
    @classmethod
    def finite_stage_numbers(cls, value: object, info: ValidationInfo) -> float:
        return _finite_number(value, str(info.field_name))


class ElectricalCostRequest(RadianceRunRequest):
    action: ActionValue = RadianceAction.METRICS.value
    peak_capping_enabled: bool = False
    utility_rate_kwh: FiniteUtilityRateKwh = 0.128
    stages: list[ElectricalCostStage] = Field(min_length=1, max_length=MAX_COST_STAGES)

    @field_validator("utility_rate_kwh", mode="before")
    @classmethod
    def finite_utility_rate(cls, value: object, info: ValidationInfo) -> float:
        return _finite_number(value, str(info.field_name))


class JobStatus(StrictBoundaryModel):
    job_id: str
    status: JobStateValue
    exit_code: int | None

    @field_validator("status", mode="before")
    @classmethod
    def valid_status(cls, value: object) -> str:
        return canonicalize_job_state(_raw_str(value)).value


class RequestFingerprintPayload(StrictBoundaryModel):
    schema_version: int
    fields: dict[str, str | bool | int]


class PublicErrorResponse(StrictBoundaryModel):
    ok: Literal[False]
    error: str
    message: str
    correlation_id: str
    detail: JsonObject | None = None


class LayoutResponse(StrictBoundaryModel):
    length_ft: float
    width_ft: float
    module_count: int
    module_counts: dict[str, int]
    total_cost: int
    all_positions: list[tuple[float, float]]
    svg: str
    svg_download_name: str
    png_download_name: str


class RadianceRunResponse(StrictBoundaryModel):
    job_id: str
    status: JobStateValue
    outdir: str
    artifact_token: str


class JobTailResponse(StrictBoundaryModel):
    job_id: str
    status: JobStateValue
    lines: list[str]
    next_cursor: int
    done: bool


class RadianceManifestResponse(StrictBoundaryModel):
    manifest: JsonObject
    path: str


class RadianceImagesResponse(StrictBoundaryModel):
    overlay: str | None
    annot: str | None


class AssemblyPhotometricBoundsResponse(StrictBoundaryModel):
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_m: float


class AssemblyPhotometricColormapResponse(StrictBoundaryModel):
    name: Literal["viridis"]
    source: Literal["matplotlib"]
    normalization: Literal["linear-clamped"]


class AssemblyPhotometricColorScaleResponse(StrictBoundaryModel):
    vmin: float
    vmax: float
    source: Literal["simulation-visualization-mean-ppfd"]
    clamp: Literal[True]


class AssemblyPhotometricOrientationResponse(StrictBoundaryModel):
    plane: Literal["xy"]
    column_axis: Literal["x"]
    row_axis: Literal["y"]
    column_order: Literal["ascending"]
    row_order: Literal["ascending"]
    storage_order: Literal["row-major"]
    value_index: Literal["row * grid_width + column"]


class AssemblyPhotometricLayerResponse(StrictBoundaryModel):
    schema_version: Literal[1]
    mode: SystemModeValue
    mode_label: str
    units: Literal["µmol/m²/s"]
    grid_width: int
    grid_height: int
    value_count: int
    bounds_m: AssemblyPhotometricBoundsResponse
    min_ppfd: float
    max_ppfd: float
    mean_ppfd: float
    target_ppfd: float
    encoding: Literal["float32-le"]
    colormap: AssemblyPhotometricColormapResponse
    color_scale: AssemblyPhotometricColorScaleResponse
    orientation: AssemblyPhotometricOrientationResponse
    warnings: list[str]


class RadianceMetricsResponse(StrictBoundaryModel):
    metrics: JsonObject
    cost_estimate: JsonObject | None


class AssemblyRoomResponse(StrictBoundaryModel):
    length_ft: float
    width_ft: float
    length_m: float
    width_m: float
    mount_z_m: float


class AssemblyAssetsResponse(StrictBoundaryModel):
    manifest: str
    anchors: str | None = None


class AssemblyAxisMappingResponse(StrictBoundaryModel):
    cad_horizontal: list[Literal["x", "z"]]
    cad_vertical: Literal["y"]
    layout_horizontal: list[Literal["x", "y"]]
    layout_vertical: Literal["z"]


class AssemblyModuleAssetResponse(StrictBoundaryModel):
    high: str | None
    medium: str | None
    proxy: str | None
    anchor_source: Literal["module_nodes"] | None = None
    optional: bool = False
    fallback_asset_key: str | None = None


class AssemblyFixturePointResponse(StrictBoundaryModel):
    x: float
    y: float
    z: float


class AssemblyAssetFallbackResponse(StrictBoundaryModel):
    asset_key: str
    fallback_asset_key: str
    instance_ids: list[str]
    reason: str


class AssemblyInstanceResponse(StrictBoundaryModel):
    id: str
    layout_type: str
    orient: str | None
    module_count: int
    shape: Literal["linear", "corner", "centerpiece", "fixture", "unknown"]
    asset_key: str
    asset_fallback_key: str | None
    points: list[AssemblyFixturePointResponse]
    position: AssemblyFixturePointResponse | None = None
    yaw_deg: float | None = None
    layout_source: JsonObject | None = None
    warnings: list[str]


class AssemblySceneResponse(StrictBoundaryModel):
    schema_version: Literal[3]
    system: Literal["proposed_led_system", "conventional_led_system", "hps_1000w_system"]
    mode: SystemModeValue
    mode_label: str
    display_name: str
    units: Literal["meters"]
    source_units: Literal["millimeters"]
    viewer_scale: float
    axis_mapping: AssemblyAxisMappingResponse
    placement_strategy: Literal["anchor_fit", "single_fixture_center"] = "anchor_fit"
    room: AssemblyRoomResponse
    assets: AssemblyAssetsResponse
    module_assets: dict[str, AssemblyModuleAssetResponse]
    instances: list[AssemblyInstanceResponse]
    fixture_counts_by_layout_type: dict[str, int]
    fixture_counts_by_asset_key: dict[str, int]
    missing_asset_keys: list[str]
    asset_fallbacks_used: list[AssemblyAssetFallbackResponse]
    warnings: list[str]
    plants: JsonObject | None = None
    fspm_metrics: JsonObject | None = None


class ElectricalEstimateStageResponse(StrictBoundaryModel):
    name: str
    days: float
    hours_per_day: float
    avg_ppfd: float
    simulated_mean_ppfd: float
    stage_watts: float
    stage_hours: float
    stage_kwh: float
    stage_cost_usd: float
    usable_efficacy_umol_j: JsonValue
    watts_basis: str
    fixed_output: bool


class ElectricalEstimateResponse(StrictBoundaryModel):
    ok: Literal[True]
    mode: SystemModeValue
    utility_rate_kwh: float
    cycle_kwh: float
    cycle_cost_usd: float
    stages: list[ElectricalEstimateStageResponse]
    notes: list[str]


class DockerStatusResponse(StrictBoundaryModel):
    available: bool
    detail: str


class RuntimeSetupCommandResponse(StrictBoundaryModel):
    label: str
    command: str


class RuntimePrecomputedModeStatusResponse(StrictBoundaryModel):
    available: Literal[True]


class RuntimeDockerModeStatusResponse(StrictBoundaryModel):
    available: bool
    reason: str | None = None
    supported_lighting_modes: list[SystemModeValue]
    docker_cli: str | None = None
    daemon_available: bool
    image_available: bool
    can_build_image: bool
    setup_commands: list[RuntimeSetupCommandResponse]


class RuntimeLocalModeStatusResponse(StrictBoundaryModel):
    available: bool
    reason: str | None = None
    supported_lighting_modes: list[SystemModeValue]
    radiance_home: str | None = None
    radiance_bin_dir: str | None = None
    radiance_lib_dir: str | None = None
    detected_executables: dict[str, str]
    missing_executables: list[str]
    env_values: dict[str, str | None]
    setup_commands: list[RuntimeSetupCommandResponse]



class RuntimePrivatePhotometryStatusResponse(StrictBoundaryModel):
    enabled: bool
    available: bool
    reason: str | None = None
    supported_private_modes: list[SystemModeValue]
    missing_env_vars: list[str]
    missing_files: list[SystemModeValue]


class RuntimeModesStatusResponse(StrictBoundaryModel):
    precomputed: RuntimePrecomputedModeStatusResponse
    live_docker: RuntimeDockerModeStatusResponse
    live_local: RuntimeLocalModeStatusResponse


class RuntimeStatusResponse(StrictBoundaryModel):
    live_execution_enabled: bool
    live_supported_modes: list[SystemModeValue]
    live_unsupported_mode_message: str
    private_photometry: RuntimePrivatePhotometryStatusResponse
    modes: RuntimeModesStatusResponse


class ReproduceResponse(StrictBoundaryModel):
    job_id: str
    status: JobStateValue


def request_with_updates(req: RadianceRunRequest, **updates: Any) -> RadianceRunRequest:
    data = req.model_dump()
    data.update(updates)
    return type(req).model_validate(data)


def request_json_dict(req: RadianceRunRequest) -> dict[str, Any]:
    return req.model_dump(mode="json")
