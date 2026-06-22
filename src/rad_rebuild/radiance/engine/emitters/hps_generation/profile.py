from __future__ import annotations

from dataclasses import asdict, dataclass
import math


IN2M = 0.0254
FT2M = 0.3048

PROFILE_VERSION = "glh_karma_8_hps1000_research_primary_v1"
PROFILE_LABEL = "1000W HPS"
PROFILE_ARCHETYPE = "GLH-KARMA-8-HPS1000 primary research IES comparator"
REFERENCE_GEOMETRY_ARCHETYPE = (
    "Barron Growlite KARMA-8 horizontal-lamp horticultural reflector"
)

FIXTURE_LENGTH_IN = 31.44
FIXTURE_WIDTH_IN = 23.76
FIXTURE_HEIGHT_IN = 0.0

FIXTURE_LENGTH_M = FIXTURE_LENGTH_IN * IN2M
FIXTURE_WIDTH_M = FIXTURE_WIDTH_IN * IN2M
FIXTURE_HEIGHT_M = FIXTURE_HEIGHT_IN * IN2M

DEFAULT_OUTER_MARGIN_IN = 1.0
DEFAULT_MOUNT_Z_M = 0.4572
DEFAULT_COVERAGE_FT = 4.0
VALID_COVERAGE_FT = (4.0, 5.0)
SHARED_DEFAULT_FIXTURE_PPE_UMOL_PER_J = 1.72

# Default research HPS normalization:
# - The whole-fixture angular distribution comes from GLH-KARMA-8-HPS1000.IES
# - All HPS modes share the same default fixture efficacy target so cross-mode
#   comparisons start from a consistent wall-plug efficacy assumption.
DEFAULT_IES_FILE = "ies_sources/GLH-KARMA-8-HPS1000.IES"
DEFAULT_SPD_FILE = "curve_data/hps/DEhps_1000w_spd.csv"
IES_HEADER_LUMENS_PER_LAMP_LM = 150000.0
IES_TOTAL_LUMINAIRE_LUMENS_LM = 118214.63252874288
IES_HEADER_INPUT_WATTS = 1045.0
BARE_LAMP_INITIAL_PPF_UMOL_S = 2000.0
BARE_LAMP_TOTAL_LUMINOUS_FLUX_LM = 144400.0
NOMINAL_INPUT_WATTS = IES_HEADER_INPUT_WATTS
NOMINAL_FIXTURE_PPF_UMOL_S = NOMINAL_INPUT_WATTS * SHARED_DEFAULT_FIXTURE_PPE_UMOL_PER_J
NOMINAL_FIXTURE_PPE_UMOL_PER_J = NOMINAL_FIXTURE_PPF_UMOL_S / NOMINAL_INPUT_WATTS

KARMA_PROFILE_VERSION = PROFILE_VERSION
KARMA_PROFILE_LABEL = "1000W HPS KARMA research comparator"
KARMA_PROFILE_ARCHETYPE = (
    "GLH-KARMA-8-HPS1000 horizontal-lamp horticultural research comparator"
)
KARMA_REFERENCE_GEOMETRY_ARCHETYPE = (
    "Barron Growlite KARMA-8 horizontal-lamp horticultural reflector"
)
KARMA_FIXTURE_LENGTH_IN = 31.44
KARMA_FIXTURE_WIDTH_IN = 23.76
KARMA_FIXTURE_HEIGHT_IN = 0.0
KARMA_FIXTURE_LENGTH_M = KARMA_FIXTURE_LENGTH_IN * IN2M
KARMA_FIXTURE_WIDTH_M = KARMA_FIXTURE_WIDTH_IN * IN2M
KARMA_FIXTURE_HEIGHT_M = KARMA_FIXTURE_HEIGHT_IN * IN2M
KARMA_DEFAULT_IES_FILE = "ies_sources/GLH-KARMA-8-HPS1000.IES"
KARMA_IES_HEADER_LUMENS_PER_LAMP_LM = 150000.0
KARMA_IES_TOTAL_LUMINAIRE_LUMENS_LM = 118214.63252874288
KARMA_IES_HEADER_INPUT_WATTS = 1045.0
KARMA_NOMINAL_INPUT_WATTS = KARMA_IES_HEADER_INPUT_WATTS
KARMA_NOMINAL_FIXTURE_PPF_UMOL_S = (
    KARMA_NOMINAL_INPUT_WATTS * SHARED_DEFAULT_FIXTURE_PPE_UMOL_PER_J
)
KARMA_NOMINAL_FIXTURE_PPE_UMOL_PER_J = (
    KARMA_NOMINAL_FIXTURE_PPF_UMOL_S / KARMA_NOMINAL_INPUT_WATTS
)
DEFAULT_IES_VARIANT = "karma"

LAMP_ARC_LENGTH_M = 0.160
LAMP_ARC_DIAMETER_M = 0.014
LAMP_RADIAL_FACES = 8

# Relative to the lamp centerline height.
REFLECTOR_CROSS_SECTION_POS_Y_M = (
    (0.000, 0.072),
    (0.028, 0.061),
    (0.076, 0.029),
    (0.108, -0.004),
    (0.12319, -0.034),
)
TOP_COVER_Z_M = 0.09906
END_KICKER_DEPTH_M = 0.0508
REFLECTOR_THICKNESS_M = 0.0007

REFLECTOR_REFLECTANCE_RGB = (0.92, 0.91, 0.87)
REFLECTOR_SPECULARITY = 0.16
REFLECTOR_ROUGHNESS = 0.03
HOUSING_REFLECTANCE_RGB = (0.12, 0.12, 0.12)
HOUSING_SPECULARITY = 0.02
HOUSING_ROUGHNESS = 0.08

LAMP_CCT_DESCRIPTION = "1000W double-ended high-pressure sodium lamp surrogate"


@dataclass(frozen=True)
class HpsProfile:
    version: str = PROFILE_VERSION
    label: str = PROFILE_LABEL
    archetype: str = PROFILE_ARCHETYPE
    reference_geometry_archetype: str = REFERENCE_GEOMETRY_ARCHETYPE
    fixture_length_in: float = FIXTURE_LENGTH_IN
    fixture_width_in: float = FIXTURE_WIDTH_IN
    fixture_height_in: float = FIXTURE_HEIGHT_IN
    default_ies_file: str = DEFAULT_IES_FILE
    default_spd_file: str = DEFAULT_SPD_FILE
    ies_header_lumens_per_lamp_lm: float = IES_HEADER_LUMENS_PER_LAMP_LM
    ies_total_luminaire_lumens_lm: float = IES_TOTAL_LUMINAIRE_LUMENS_LM
    ies_header_input_watts: float = IES_HEADER_INPUT_WATTS
    bare_lamp_initial_ppf_umol_s: float = BARE_LAMP_INITIAL_PPF_UMOL_S
    bare_lamp_total_luminous_flux_lm: float = BARE_LAMP_TOTAL_LUMINOUS_FLUX_LM
    nominal_fixture_ppf_umol_s: float = NOMINAL_FIXTURE_PPF_UMOL_S
    nominal_input_watts: float = NOMINAL_INPUT_WATTS
    nominal_fixture_ppe_umol_per_j: float = NOMINAL_FIXTURE_PPE_UMOL_PER_J
    default_ies_variant: str = DEFAULT_IES_VARIANT
    default_mount_z_m: float = DEFAULT_MOUNT_Z_M
    default_coverage_ft: float = DEFAULT_COVERAGE_FT
    lamp_arc_length_m: float = LAMP_ARC_LENGTH_M
    lamp_arc_diameter_m: float = LAMP_ARC_DIAMETER_M
    lamp_radial_faces: int = LAMP_RADIAL_FACES
    reflector_cross_section_pos_y_m: tuple[tuple[float, float], ...] = (
        REFLECTOR_CROSS_SECTION_POS_Y_M
    )
    top_cover_z_m: float = TOP_COVER_Z_M
    end_kicker_depth_m: float = END_KICKER_DEPTH_M
    reflector_thickness_m: float = REFLECTOR_THICKNESS_M
    reflector_reflectance_rgb: tuple[float, float, float] = REFLECTOR_REFLECTANCE_RGB
    reflector_specularity: float = REFLECTOR_SPECULARITY
    reflector_roughness: float = REFLECTOR_ROUGHNESS
    housing_reflectance_rgb: tuple[float, float, float] = HOUSING_REFLECTANCE_RGB
    housing_specularity: float = HOUSING_SPECULARITY
    housing_roughness: float = HOUSING_ROUGHNESS
    lamp_description: str = LAMP_CCT_DESCRIPTION


@dataclass(frozen=True)
class HpsIesComparatorProfile:
    variant: str
    profile_version: str
    label: str
    archetype: str
    reference_geometry_archetype: str
    fixture_length_in: float
    fixture_width_in: float
    fixture_height_in: float
    default_ies_file: str
    default_spd_file: str
    ies_header_lumens_per_lamp_lm: float
    ies_total_luminaire_lumens_lm: float
    ies_header_input_watts: float
    nominal_fixture_ppf_umol_s: float
    nominal_input_watts: float
    nominal_fixture_ppe_umol_per_j: float
    lamp_orientation: str

    @property
    def fixture_length_m(self) -> float:
        return self.fixture_length_in * IN2M

    @property
    def fixture_width_m(self) -> float:
        return self.fixture_width_in * IN2M

    @property
    def fixture_height_m(self) -> float:
        return self.fixture_height_in * IN2M


IES_COMPARATOR_PROFILES: dict[str, HpsIesComparatorProfile] = {
    "karma": HpsIesComparatorProfile(
        variant="karma",
        profile_version=KARMA_PROFILE_VERSION,
        label=KARMA_PROFILE_LABEL,
        archetype=KARMA_PROFILE_ARCHETYPE,
        reference_geometry_archetype=KARMA_REFERENCE_GEOMETRY_ARCHETYPE,
        fixture_length_in=KARMA_FIXTURE_LENGTH_IN,
        fixture_width_in=KARMA_FIXTURE_WIDTH_IN,
        fixture_height_in=KARMA_FIXTURE_HEIGHT_IN,
        default_ies_file=KARMA_DEFAULT_IES_FILE,
        default_spd_file=DEFAULT_SPD_FILE,
        ies_header_lumens_per_lamp_lm=KARMA_IES_HEADER_LUMENS_PER_LAMP_LM,
        ies_total_luminaire_lumens_lm=KARMA_IES_TOTAL_LUMINAIRE_LUMENS_LM,
        ies_header_input_watts=KARMA_IES_HEADER_INPUT_WATTS,
        nominal_fixture_ppf_umol_s=KARMA_NOMINAL_FIXTURE_PPF_UMOL_S,
        nominal_input_watts=KARMA_NOMINAL_INPUT_WATTS,
        nominal_fixture_ppe_umol_per_j=KARMA_NOMINAL_FIXTURE_PPE_UMOL_PER_J,
        lamp_orientation="horizontal_x",
    ),
}


def normalize_ies_variant(value: str | None) -> str:
    variant = (value or DEFAULT_IES_VARIANT).strip().lower()
    if variant not in IES_COMPARATOR_PROFILES:
        expected = ", ".join(sorted(IES_COMPARATOR_PROFILES))
        raise ValueError(
            f"Unsupported HPS IES variant {value!r}; expected one of {expected}"
        )
    return variant


def get_ies_comparator_profile(value: str | None = None) -> HpsIesComparatorProfile:
    return IES_COMPARATOR_PROFILES[normalize_ies_variant(value)]


def ies_profile_manifest_dict(value: str | None = None) -> dict[str, object]:
    return asdict(get_ies_comparator_profile(value))


def profile_manifest_dict() -> dict[str, object]:
    return asdict(HpsProfile())


def calibration_sensitive_elements() -> list[str]:
    return [
        "default_ies_variant",
        "nominal_fixture_ppf_umol_s",
        "nominal_input_watts",
        "default_mount_z_m",
    ]


def aligned_dims_ft(length_ft: float, width_ft: float) -> tuple[float, float]:
    if width_ft > length_ft:
        return width_ft, length_ft
    return length_ft, width_ft


def validate_coverage_ft(value: float) -> float:
    for option in VALID_COVERAGE_FT:
        if abs(float(value) - option) <= 1e-6:
            return option
    raise ValueError(
        f"Unsupported HPS coverage footprint {value!r}; expected one of {VALID_COVERAGE_FT}"
    )


def coverage_grid_counts(
    length_ft: float,
    width_ft: float,
    coverage_ft: float,
    margin_in: float = DEFAULT_OUTER_MARGIN_IN,
) -> tuple[int, int]:
    coverage = validate_coverage_ft(coverage_ft)
    length_ft, width_ft = aligned_dims_ft(length_ft, width_ft)
    # Count fixtures by nominal coverage footprint. The wall inset is enforced
    # as a physical clearance check, not by shrinking the nominal coverage grid.
    nx = max(1, int(math.floor((length_ft + 1e-9) / coverage)))
    ny = max(1, int(math.floor((width_ft + 1e-9) / coverage)))
    return nx, ny


def coverage_centers_m(
    length_ft: float,
    width_ft: float,
    coverage_ft: float,
    margin_in: float = DEFAULT_OUTER_MARGIN_IN,
) -> list[tuple[float, float]]:
    nx, ny = coverage_grid_counts(length_ft, width_ft, coverage_ft, margin_in)
    spacing_m = validate_coverage_ft(coverage_ft) * FT2M
    span_x = spacing_m * max(nx - 1, 0)
    span_y = spacing_m * max(ny - 1, 0)
    usable_half_x = 0.5 * length_ft * FT2M - margin_in * IN2M
    usable_half_y = 0.5 * width_ft * FT2M - margin_in * IN2M
    max_center_x = 0.5 * span_x
    max_center_y = 0.5 * span_y
    fixture_half_x = 0.5 * FIXTURE_LENGTH_M
    fixture_half_y = 0.5 * FIXTURE_WIDTH_M
    if max_center_x + fixture_half_x > usable_half_x + 1e-9:
        raise ValueError("HPS fixture grid violates the X wall inset clearance.")
    if max_center_y + fixture_half_y > usable_half_y + 1e-9:
        raise ValueError("HPS fixture grid violates the Y wall inset clearance.")
    xs = [(-0.5 * span_x) + i * spacing_m for i in range(nx)]
    ys = [(-0.5 * span_y) + i * spacing_m for i in range(ny)]
    return [(x, y) for y in ys for x in xs]
