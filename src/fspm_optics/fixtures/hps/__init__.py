"""HPS measured-shape, declared-operating-point source APIs."""

from .angular import (
    HPS_ANGULAR_NORMALIZATION_POLICY,
    HPS_DERIVED_IES_FILENAME,
    HpsAngularDistribution,
    HpsDerivedIesDocument,
    build_hps_derived_ies,
    format_hps_angular_json,
    normalize_hps_angular_distribution,
)
from .errors import (
    HpsLayoutError,
    HpsPhotometryError,
    HpsProfileError,
    HpsResourceError,
    HpsSourceModelError,
    HpsSourcePlanError,
    NoLegalHpsLayoutError,
)
from .layout import (
    HPS_CENTER_PITCH_M,
    HPS_DEFAULT_MOUNT_HEIGHT_M,
    HPS_LAYOUT_POLICY_ID,
    HPS_WALL_CLEARANCE_M,
    HpsFixtureCounts,
    HpsFixtureLayoutInstance,
    HpsLayoutPlan,
    HpsMountPlanes,
    HpsRoomAxes,
    HpsRoomDimensionsM,
    align_hps_room_long_axis_to_x,
    format_hps_layout_json,
    plan_hps_layout,
    plan_hps_layout_from_feet,
)
from .lm63 import load_hps_lm63
from .profile import (
    DECLARED_OPERATING_POINT_BASIS,
    HPS_ANGULAR_DISTRIBUTION_ID,
    HPS_COMPARISON_PROFILE_ID,
    HPS_INITIAL_LAMP_PAR_PPF_UMOL_S,
    HPS_NOMINAL_LAMP_CLASS_POWER_W,
    HPS_SCENARIO_CLAIM_BOUNDARY,
    HPS_SOURCE_ID,
    HPS_SYSTEM_PPE_UMOL_PER_J,
    HPS_TESTED_SYSTEM_INPUT_POWER_W,
    HpsComparisonProfile,
    HpsDeclaredOperatingPoint,
    HpsFixtureDimensionsM,
    HpsIesTestMetadata,
    HpsMeasuredAsset,
    build_hps_comparison_profile,
    format_hps_profile_json,
)
from .radiance_source import (
    HPS_ANGULAR_MODIFIER_ID,
    HPS_FIXTURE_PPF_UMOL_S,
    HPS_FLAT_APERTURE_POLICY,
    HPS_LIGHT_MODIFIER_ID,
    HPS_RADIANCE_CARRIER_MULTIPLIER,
    RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W,
    HpsApertureSource,
    HpsCarrierScale,
    HpsConvertedIesOutputContract,
    HpsFixturePlacement,
    HpsRadianceSourcePlan,
    build_hps_radiance_source_plan,
    derive_hps_carrier_scale,
    format_hps_apertures_rad,
    format_hps_source_plan_json,
    validate_converted_hps_ies_output,
)
from .resources import (
    HPS_IES_RESOURCE_NAME,
    HPS_IES_SHA256,
    HPS_RESOURCE_HASHES,
    HPS_SPD_RESOURCE_NAME,
    HPS_SPD_ROW_COUNT,
    HPS_SPD_SHA256,
    hps_resource_bytes,
    load_hps_spd,
)
from .spectral import (
    HPS_SPD_NORMALIZATION_POLICY,
    HPS_SPECTRAL_SOURCE_ID,
    HpsSpectralDistribution,
    build_hps_spectral_distribution,
    format_hps_spectral_distribution_json,
    hps_spectral_distribution_id,
)
from .band_source import (
    HPS_BAND_ORDER,
    HPS_BAND_SOURCE_MODEL_ID,
    HpsBandPhotonBudget,
    HpsSpectralSourcePayload,
    build_hps_spectral_source_payload,
    format_hps_spectral_source_payload_json,
)

__all__ = [name for name in globals() if not name.startswith("_")]
