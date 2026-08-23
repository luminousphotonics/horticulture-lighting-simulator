"""Pure Radiance adapter and GLB eight-aperture Conventional source planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import math
from pathlib import Path
import re
from typing import TYPE_CHECKING, Final, Sequence

from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.transport.scalar_ppfd import (
    BASELINE_SOURCE_CHANNEL_POLICY,
    PPFD_CONVERSION_BASIS,
)

from .angular import ANGULAR_NORMALIZATION_POLICY, normalize_lm63_angular_distribution
from .errors import ConventionalLedError
from .ies_staging import (
    DerivedDownwardNormalizedIesDocument,
    OriginalIesIdentity,
    build_downward_normalized_derived_ies,
)
from .layout import ConventionalFixtureInstance, ConventionalLayoutPlan
from .lm63 import Lm63Photometry, load_approved_lm63
from .profile import (
    CONVENTIONAL_FIXTURE_PPF_UMOL_S,
    SCENARIO_CLAIM_BOUNDARY,
    build_conventional_comparison_profile,
)
from .resources import CONVENTIONAL_IES_RESOURCE_NAME, CONVENTIONAL_IES_SHA256

if TYPE_CHECKING:
    from fspm_optics.fixtures.occlusion import FixtureEmittingBoundary

# Radiance ies2rad.c writes candela data using 1/WHTEFFICACY.  Radiance
# color.h defines WHTEFFICACY as 179 lm per radiometric white-carrier watt.
RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W: Final = 179.0
CONVENTIONAL_FULL_OUTPUT_CARRIER_MULTIPLIER: Final = (
    CONVENTIONAL_FIXTURE_PPF_UMOL_S
    * RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W
)
IES2RAD_OUTPUT_SIGNIFICANT_DIGITS: Final = 6
CONVENTIONAL_IES2RAD_OUTPUT_ROOT: Final = "conventional_led_unit_downward_flux"
CONVENTIONAL_CONVERTED_ANGULAR_MODIFIER: Final = (
    f"{CONVENTIONAL_IES2RAD_OUTPUT_ROOT}_dist"
)
CONVENTIONAL_CONVERTED_LIGHT_MODIFIER: Final = (
    f"{CONVENTIONAL_IES2RAD_OUTPUT_ROOT}_light"
)
CONVENTIONAL_SHARED_ANGULAR_MODIFIER: Final = (
    f"{CONVENTIONAL_IES2RAD_OUTPUT_ROOT}_aperture_dist"
)
CONVENTIONAL_SHARED_LIGHT_MODIFIER: Final = (
    f"{CONVENTIONAL_IES2RAD_OUTPUT_ROOT}_aperture_light"
)
IES2RAD_CONTRACT_URL: Final = "https://floyd.lbl.gov/radiance/man_html/ies2rad.1.html"


class ConventionalRadianceSourceError(ConventionalLedError):
    """The pure Conventional Radiance source contract is invalid."""


class ConvertedIesOutputError(ConventionalRadianceSourceError):
    """Synthetic or future native ies2rad output violates the approved subset."""


@dataclass(frozen=True, slots=True)
class CarrierScaleDerivation:
    fixture_ppf_umol_s: float
    white_efficacy_lm_per_radiance_carrier_w: float
    ies2rad_multiplier: float
    derivation: str
    decode_basis: str

    @property
    def global_dimming_factor(self) -> float:
        return (
            self.ies2rad_multiplier
            / CONVENTIONAL_FULL_OUTPUT_CARRIER_MULTIPLIER
        )

    def __post_init__(self) -> None:
        expected_carrier = _canonical_ies2rad_output_number(
            self.fixture_ppf_umol_s
            * self.white_efficacy_lm_per_radiance_carrier_w
        )
        if (
            not math.isfinite(self.fixture_ppf_umol_s)
            or self.fixture_ppf_umol_s <= 0.0
            or self.fixture_ppf_umol_s > CONVENTIONAL_FIXTURE_PPF_UMOL_S
            or self.white_efficacy_lm_per_radiance_carrier_w
            != RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W
            or self.ies2rad_multiplier != expected_carrier
            or self.fixture_ppf_umol_s
            != (
                self.ies2rad_multiplier
                / self.white_efficacy_lm_per_radiance_carrier_w
            )
        ):
            raise ConventionalRadianceSourceError("carrier scale derivation is invalid.")
        if self.decode_basis != PPFD_CONVERSION_BASIS:
            raise ConventionalRadianceSourceError("scalar decode basis is invalid.")
        if self.derivation != "fixture_ppf_umol_s * radiance_white_efficacy_lm_per_w":
            raise ConventionalRadianceSourceError("carrier derivation label is invalid.")

    def to_payload(self) -> dict[str, object]:
        return {
            "fixture_ppf_umol_s": self.fixture_ppf_umol_s,
            "full_output_fixture_ppf_umol_s": CONVENTIONAL_FIXTURE_PPF_UMOL_S,
            "global_source_dimming_factor": self.global_dimming_factor,
            "radiance_white_efficacy_lm_per_carrier_w": (
                self.white_efficacy_lm_per_radiance_carrier_w
            ),
            "ies2rad_multiplier": self.ies2rad_multiplier,
            "derivation": self.derivation,
            "ies2rad_candela_conversion": "input_lumens / WHTEFFICACY",
            "ies2rad_output_numeric_format": "%g (six significant digits)",
            "carrier_quantized_to_native_ies2rad_output": True,
            "decode_basis": self.decode_basis,
            "carrier_applied_before_trace": True,
            "post_trace_179_conversion": False,
        }


@dataclass(frozen=True, slots=True)
class AngularNormalizationIdentity:
    distribution_id: str
    policy: str
    original_ies_sha256: str
    role: str = "original_full_sphere_diagnostic_only"

    @property
    def identity(self) -> str:
        return "conventional-angular-normalization-v1-" + _hash_payload(
            self.to_payload()
        )

    def to_payload(self) -> dict[str, str]:
        return {
            "distribution_id": self.distribution_id,
            "policy": self.policy,
            "original_ies_sha256": self.original_ies_sha256,
            "role": self.role,
        }


@dataclass(frozen=True, slots=True)
class DeclaredOperatingPointIdentity:
    profile_id: str
    profile_sha256: str
    electrical_power_w_per_fixture: float
    par_ppe_umol_per_j: float
    par_ppf_umol_s_per_fixture: float
    basis: str

    @property
    def identity(self) -> str:
        return "conventional-operating-point-v2-" + _hash_payload(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "profile_sha256": self.profile_sha256,
            "electrical_power_w_per_fixture": self.electrical_power_w_per_fixture,
            "par_ppe_umol_per_j": self.par_ppe_umol_per_j,
            "par_ppf_umol_s_per_fixture": self.par_ppf_umol_s_per_fixture,
            "basis": self.basis,
        }


def derive_conventional_carrier_scale(
    fixture_ppf_umol_s: float = CONVENTIONAL_FIXTURE_PPF_UMOL_S,
) -> CarrierScaleDerivation:
    requested_ppf = _positive("fixture_ppf_umol_s", fixture_ppf_umol_s)
    carrier = _canonical_ies2rad_output_number(
        requested_ppf * RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W
    )
    ppf = carrier / RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W
    return CarrierScaleDerivation(
        fixture_ppf_umol_s=ppf,
        white_efficacy_lm_per_radiance_carrier_w=(
            RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W
        ),
        ies2rad_multiplier=carrier,
        derivation="fixture_ppf_umol_s * radiance_white_efficacy_lm_per_w",
        decode_basis=PPFD_CONVERSION_BASIS,
    )


def canonical_conventional_global_dimming_factor(
    global_dimming_factor: float,
) -> float:
    """Return the factor represented by native ``ies2rad`` ``%g`` output."""

    requested = _closed_unit_interval(
        "global_dimming_factor", global_dimming_factor
    )
    carrier = _canonical_ies2rad_output_number(
        CONVENTIONAL_FULL_OUTPUT_CARRIER_MULTIPLIER * requested
    )
    canonical = carrier / CONVENTIONAL_FULL_OUTPUT_CARRIER_MULTIPLIER
    if canonical <= 0.0 or canonical > 1.0:
        raise ConventionalRadianceSourceError(
            "canonical global_dimming_factor is outside the supported range."
        )
    return canonical


def conservative_conventional_global_dimming_factor(
    requested_factor: float,
) -> float:
    """Resolve the native six-significant-digit factor without cap overshoot."""

    if (
        isinstance(requested_factor, bool)
        or not isinstance(requested_factor, int | float)
    ):
        raise ConventionalRadianceSourceError(
            "requested_factor must be finite and in the closed unit interval."
        )
    requested = float(requested_factor)
    if not math.isfinite(requested) or not 0.0 <= requested <= 1.0:
        raise ConventionalRadianceSourceError(
            "requested_factor must be finite and in the closed unit interval."
        )
    if requested == 0.0:
        return 0.0
    represented = canonical_conventional_global_dimming_factor(requested)
    if represented <= requested:
        return represented
    full_carrier = Decimal(
        str(CONVENTIONAL_FULL_OUTPUT_CARRIER_MULTIPLIER)
    )
    requested_carrier = full_carrier * Decimal(str(requested))
    quantum = Decimal(1).scaleb(requested_carrier.adjusted() - 5)
    conservative_carrier = requested_carrier.quantize(
        quantum,
        rounding=ROUND_FLOOR,
    )
    for _ in range(2):
        candidate = float(conservative_carrier / full_carrier)
        represented = canonical_conventional_global_dimming_factor(candidate)
        if represented <= requested:
            return represented
        conservative_carrier -= quantum
    raise ConventionalRadianceSourceError(
        "no conservative Conventional source factor was representable."
    )


@dataclass(frozen=True, slots=True)
class Ies2radExpectedPaths:
    workspace: Path
    derived_ies: Path
    converted_rad: Path
    converted_dat: Path
    source_cal_reference: str = "source.cal"

    def __post_init__(self) -> None:
        workspace = Path(self.workspace)
        paths = tuple(
            Path(value)
            for value in (self.derived_ies, self.converted_rad, self.converted_dat)
        )
        if any(path.parent != workspace for path in paths):
            raise ConventionalRadianceSourceError(
                "derived and converted paths must be direct children of the workspace."
            )
        if self.source_cal_reference != "source.cal":
            raise ConventionalRadianceSourceError("ies2rad CAL reference must be source.cal.")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "derived_ies", paths[0])
        object.__setattr__(self, "converted_rad", paths[1])
        object.__setattr__(self, "converted_dat", paths[2])

    def scientific_names_payload(self) -> dict[str, str]:
        return {
            "derived_ies": self.derived_ies.name,
            "converted_rad": self.converted_rad.name,
            "converted_dat": self.converted_dat.name,
            "source_cal_reference": self.source_cal_reference,
        }

    def runtime_payload(self) -> dict[str, str]:
        return {
            "workspace": str(self.workspace),
            "derived_ies": str(self.derived_ies),
            "converted_rad": str(self.converted_rad),
            "converted_dat": str(self.converted_dat),
        }


@dataclass(frozen=True, slots=True)
class Ies2radCommandPlan:
    command: CommandSpec
    expected_paths: Ies2radExpectedPaths
    output_root: str
    carrier_scale: CarrierScaleDerivation
    neutral_rgb: tuple[float, float, float] = (1.0, 1.0, 1.0)

    def __post_init__(self) -> None:
        if self.output_root != CONVENTIONAL_IES2RAD_OUTPUT_ROOT:
            raise ConventionalRadianceSourceError("ies2rad output root is fixed.")
        if self.neutral_rgb != (1.0, 1.0, 1.0):
            raise ConventionalRadianceSourceError("ies2rad color must be neutral RGB.")
        if self.command.cwd != self.expected_paths.workspace:
            raise ConventionalRadianceSourceError("ies2rad cwd must be explicit workspace.")
        if self.command.stdin_path is not None or self.command.stdout_path is not None:
            raise ConventionalRadianceSourceError(
                "ies2rad uses explicit input/output names, not redirected streams."
            )

    def scientific_payload(self) -> dict[str, object]:
        return {
            "executable_role": "ies2rad",
            "argv_after_executable": list(self.command.argv[1:]),
            "output_root": self.output_root,
            "neutral_rgb": list(self.neutral_rgb),
            "expected_paths": self.expected_paths.scientific_names_payload(),
            "carrier_scale": self.carrier_scale.to_payload(),
            "shell": False,
            "execution_performed": False,
        }


def build_ies2rad_command_plan(
    workspace: str | Path,
    *,
    derived_ies_filename: str,
    carrier_scale: CarrierScaleDerivation,
    ies2rad_bin: str | Path = "ies2rad",
) -> Ies2radCommandPlan:
    """Build the future ies2rad CommandSpec without locating or running it."""

    workspace_path = Path(workspace)
    if Path(derived_ies_filename).name != derived_ies_filename:
        raise ConventionalRadianceSourceError("derived IES filename must be a basename.")
    executable = str(ies2rad_bin)
    if not executable:
        raise ConventionalRadianceSourceError("ies2rad executable token is required.")
    paths = Ies2radExpectedPaths(
        workspace=workspace_path,
        derived_ies=workspace_path / derived_ies_filename,
        converted_rad=workspace_path / f"{CONVENTIONAL_IES2RAD_OUTPUT_ROOT}.rad",
        converted_dat=workspace_path / f"{CONVENTIONAL_IES2RAD_OUTPUT_ROOT}.dat",
    )
    multiplier = _number(carrier_scale.ies2rad_multiplier)
    command = CommandSpec(
        argv=(
            executable,
            "-dm",
            "-t",
            "default",
            "-c",
            "1",
            "1",
            "1",
            "-m",
            multiplier,
            "-o",
            CONVENTIONAL_IES2RAD_OUTPUT_ROOT,
            derived_ies_filename,
        ),
        cwd=workspace_path,
        label="convert_conventional_unit_downward_flux_ies",
    )
    return Ies2radCommandPlan(
        command=command,
        expected_paths=paths,
        output_root=CONVENTIONAL_IES2RAD_OUTPUT_ROOT,
        carrier_scale=carrier_scale,
    )


@dataclass(frozen=True, slots=True)
class RadiancePrimitive:
    modifier: str
    primitive_type: str
    identifier: str
    string_arguments: tuple[str, ...]
    integer_arguments: tuple[int, ...]
    real_arguments: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ConvertedIesOutputContract:
    converted_angular_modifier_id: str
    converted_light_modifier_id: str
    clean_angular_modifier_id: str
    clean_light_modifier_id: str
    clean_aperture_brightdata_scale: float
    dat_reference: str
    cal_reference: str
    length_x_m: float
    width_y_m: float
    height_z_m: float
    emitting_boundary_area_m2: float
    neutral_rgb: tuple[float, float, float]
    primitive_count: int
    geometry_ids: tuple[str, ...]
    shared_definition_text: str


def validate_converted_ies_output(
    text: str,
    *,
    expected_dat_reference: str = f"{CONVENTIONAL_IES2RAD_OUTPUT_ROOT}.dat",
    expected_carrier_multiplier: float = CONVENTIONAL_FULL_OUTPUT_CARRIER_MULTIPLIER,
    length_x_m: float = 1.190,
    width_y_m: float = 1.087,
    height_z_m: float = 0.108,
    emitting_boundary_area_m2: float | None = None,
) -> ConvertedIesOutputContract:
    """Validate only the approved centered Type-C rectangular ies2rad output."""

    primitives = _parse_radiance_primitives(text)
    if len(primitives) != 8:
        raise ConvertedIesOutputError("expected one brightdata, one light, and six box polygons.")
    brightdata = primitives[0]
    light = primitives[1]
    polygons = primitives[2:]
    if (
        brightdata.modifier != "void"
        or brightdata.primitive_type != "brightdata"
        or brightdata.identifier != CONVENTIONAL_CONVERTED_ANGULAR_MODIFIER
    ):
        raise ConvertedIesOutputError("expected deterministic angular brightdata modifier.")
    expected_strings = (
        "boxcorr",
        expected_dat_reference,
        "source.cal",
        "src_phi",
        "src_theta",
    )
    if brightdata.string_arguments != expected_strings:
        raise ConvertedIesOutputError("brightdata references unexpected DAT/CAL functions.")
    if brightdata.integer_arguments:
        raise ConvertedIesOutputError("brightdata integer arguments are unexpected.")
    expected_reals = (expected_carrier_multiplier, length_x_m, width_y_m, height_z_m)
    if len(brightdata.real_arguments) != 4 or any(
        not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-9)
        for actual, expected in zip(brightdata.real_arguments, expected_reals, strict=True)
    ):
        raise ConvertedIesOutputError("brightdata carrier or box dimensions are invalid.")
    if (
        light.modifier != CONVENTIONAL_CONVERTED_ANGULAR_MODIFIER
        or light.primitive_type != "light"
        or light.identifier != CONVENTIONAL_CONVERTED_LIGHT_MODIFIER
        or light.string_arguments
        or light.integer_arguments
        or light.real_arguments != (1.0, 1.0, 1.0)
    ):
        raise ConvertedIesOutputError("expected one neutral equal-channel light modifier.")
    _validate_centered_box_geometry(
        polygons,
        light_modifier=light.identifier,
        length_x_m=length_x_m,
        width_y_m=width_y_m,
        height_z_m=height_z_m,
    )
    boundary_area = (
        length_x_m * width_y_m
        if emitting_boundary_area_m2 is None
        else _positive(
            "emitting_boundary_area_m2", emitting_boundary_area_m2
        )
    )
    clean_brightdata = RadiancePrimitive(
        modifier="void",
        primitive_type="brightdata",
        identifier=CONVENTIONAL_SHARED_ANGULAR_MODIFIER,
        string_arguments=(
            "flatcorr",
            expected_dat_reference,
            "source.cal",
            "src_phi",
            "src_theta",
        ),
        integer_arguments=(),
        real_arguments=(
            brightdata.real_arguments[0] / boundary_area,
        ),
    )
    clean_light = RadiancePrimitive(
        modifier=CONVENTIONAL_SHARED_ANGULAR_MODIFIER,
        primitive_type="illum",
        identifier=CONVENTIONAL_SHARED_LIGHT_MODIFIER,
        string_arguments=(),
        integer_arguments=(),
        real_arguments=(1.0, 1.0, 1.0),
    )
    shared_text = (
        _format_primitive(clean_brightdata)
        + "\n"
        + _format_primitive(clean_light)
    )
    return ConvertedIesOutputContract(
        converted_angular_modifier_id=brightdata.identifier,
        converted_light_modifier_id=light.identifier,
        clean_angular_modifier_id=clean_brightdata.identifier,
        clean_light_modifier_id=clean_light.identifier,
        clean_aperture_brightdata_scale=clean_brightdata.real_arguments[0],
        dat_reference=expected_dat_reference,
        cal_reference="source.cal",
        length_x_m=length_x_m,
        width_y_m=width_y_m,
        height_z_m=height_z_m,
        emitting_boundary_area_m2=boundary_area,
        neutral_rgb=(1.0, 1.0, 1.0),
        primitive_count=len(primitives),
        geometry_ids=tuple(item.identifier for item in polygons),
        shared_definition_text=shared_text,
    )


@dataclass(frozen=True, slots=True)
class ApertureSource:
    source_id: str
    polygon_id: str
    fixture_id: str
    row_y: int
    column_x: int
    bar_index: int
    angular_light_modifier_id: str
    vertices_m: tuple[tuple[float, float, float], ...]
    normal: tuple[float, float, float]
    transported_downward_ppf_umol_s: float
    area_m2: float

    def __post_init__(self) -> None:
        if len(self.vertices_m) != 4:
            raise ConventionalRadianceSourceError("aperture must have four vertices.")
        calculated = _polygon_unit_normal(self.vertices_m)
        if calculated != (0.0, 0.0, -1.0) or self.normal != calculated:
            raise ConventionalRadianceSourceError("aperture winding must face negative Z.")
        if (
            self.transported_downward_ppf_umol_s <= 0.0
            or self.transported_downward_ppf_umol_s
            > CONVENTIONAL_FIXTURE_PPF_UMOL_S
        ):
            raise ConventionalRadianceSourceError(
                "per-fixture PPF must be in the source-dimmed 0-to-1716 range."
            )
        if self.bar_index not in range(8) or self.area_m2 <= 0.0:
            raise ConventionalRadianceSourceError(
                "aperture bar index and GLB area must be valid."
            )

    def to_payload(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "polygon_id": self.polygon_id,
            "fixture_id": self.fixture_id,
            "grid": {"row_y": self.row_y, "column_x": self.column_x},
            "bar_index": self.bar_index,
            "angular_light_modifier_id": self.angular_light_modifier_id,
            "vertices_m": [list(vertex) for vertex in self.vertices_m],
            "normal": list(self.normal),
            "transported_downward_ppf_umol_s": (
                self.transported_downward_ppf_umol_s
            ),
            "area_m2": self.area_m2,
        }


@dataclass(frozen=True, slots=True)
class SharedAngularSourceIdentity:
    angular_modifier_id: str
    light_modifier_id: str
    original_angular_distribution_id: str
    derived_ies_sha256: str
    aperture_correction: str = (
        "flatcorr_one_sided_downward_glb_eight_bar_illum"
    )
    transported_hemisphere: str = "type_c_vertical_0_to_90_degrees"

    @property
    def identity(self) -> str:
        return "conventional-shared-angular-v2-" + _hash_payload(self.to_payload())

    def to_payload(self) -> dict[str, str]:
        return {
            "angular_modifier_id": self.angular_modifier_id,
            "light_modifier_id": self.light_modifier_id,
            "original_angular_distribution_id": self.original_angular_distribution_id,
            "derived_ies_sha256": self.derived_ies_sha256,
            "aperture_correction": self.aperture_correction,
            "transported_hemisphere": self.transported_hemisphere,
        }


@dataclass(frozen=True, slots=True)
class ConventionalRadianceSourcePlan:
    profile_id: str
    profile_sha256: str
    layout_id: str
    original_ies: OriginalIesIdentity
    derived_ies: DerivedDownwardNormalizedIesDocument
    angular_normalization: AngularNormalizationIdentity
    declared_operating_point: DeclaredOperatingPointIdentity
    carrier_scale: CarrierScaleDerivation
    ies2rad: Ies2radCommandPlan
    shared_angular_source: SharedAngularSourceIdentity
    apertures: tuple[ApertureSource, ...]
    fixture_count: int
    emitting_boundary_area_m2_per_fixture: float
    transported_downward_ppf_umol_s_per_fixture: float
    whole_layout_transported_downward_ppf_umol_s: float
    channel_policy: str
    claim_boundary: str
    prohibited_absolute_scale_paths: tuple[str, ...]
    source_plan_id: str = field(init=False)

    def __post_init__(self) -> None:
        if len(self.apertures) == 0 or len(self.apertures) != self.fixture_count * 8:
            raise ConventionalRadianceSourceError("source plan requires fixture apertures.")
        if not (
            0.0
            < self.transported_downward_ppf_umol_s_per_fixture
            <= CONVENTIONAL_FIXTURE_PPF_UMOL_S
        ):
            raise ConventionalRadianceSourceError("per-fixture PPF invariant failed.")
        if self.whole_layout_transported_downward_ppf_umol_s != (
            self.fixture_count
            * self.transported_downward_ppf_umol_s_per_fixture
        ):
            raise ConventionalRadianceSourceError("whole-layout PPF invariant failed.")
        if self.channel_policy != BASELINE_SOURCE_CHANNEL_POLICY:
            raise ConventionalRadianceSourceError("equal-channel scalar policy is required.")
        if self.claim_boundary != SCENARIO_CLAIM_BOUNDARY:
            raise ConventionalRadianceSourceError("claim boundary is inconsistent.")
        if self.emitting_boundary_area_m2_per_fixture <= 0.0:
            raise ConventionalRadianceSourceError(
                "GLB emitting boundary area must be positive."
            )
        grids = tuple(
            (item.row_y, item.column_x, item.bar_index)
            for item in self.apertures
        )
        if grids != tuple(sorted(grids)):
            raise ConventionalRadianceSourceError("apertures must retain Y-major order.")
        if len({item.source_id for item in self.apertures}) != len(self.apertures):
            raise ConventionalRadianceSourceError("aperture source identities must be unique.")
        object.__setattr__(
            self,
            "source_plan_id",
            "conventional-radiance-source-v3-" + _hash_payload(self.scientific_payload()),
        )

    def scientific_payload(self) -> dict[str, object]:
        return {
            "schema_version": 3,
            "profile_id": self.profile_id,
            "profile_sha256": self.profile_sha256,
            "layout_id": self.layout_id,
            "original_ies": self.original_ies.to_payload(),
            "derived_ies": self.derived_ies.to_payload(),
            "angular_normalization": self.angular_normalization.to_payload()
            | {"identity": self.angular_normalization.identity},
            "declared_operating_point": self.declared_operating_point.to_payload()
            | {"identity": self.declared_operating_point.identity},
            "carrier_scale": self.carrier_scale.to_payload(),
            "ies2rad": self.ies2rad.scientific_payload(),
            "shared_angular_source": self.shared_angular_source.to_payload()
            | {"identity": self.shared_angular_source.identity},
            "apertures": [item.to_payload() for item in self.apertures],
            "fixture_count": self.fixture_count,
            "aperture_count": len(self.apertures),
            "apertures_per_fixture": 8,
            "emitting_boundary_area_m2_per_fixture": (
                self.emitting_boundary_area_m2_per_fixture
            ),
            "transported_downward_ppf_umol_s_per_fixture": (
                self.transported_downward_ppf_umol_s_per_fixture
            ),
            "whole_layout_transported_downward_ppf_umol_s": (
                self.whole_layout_transported_downward_ppf_umol_s
            ),
            "global_source_dimming_factor": (
                self.carrier_scale.global_dimming_factor
            ),
            "dimming_stage": "ies2rad_carrier_before_trace",
            "channel_policy": self.channel_policy,
            "post_trace_scale": None,
            "transport_approximation": {
                "geometry": "eight_glb_derived_downward_bar_apertures_per_fixture",
                "photometric_boundary": "illum",
                "transported_hemisphere": "type_c_vertical_0_to_90_degrees",
                "converted_full_box_enters_final_scene": False,
                "upward_or_lateral_faces_enter_final_scene": False,
                "downward_carrier_closure_is_exact_by_construction": True,
            },
            "claim_boundary": self.claim_boundary,
            "prohibited_absolute_scale_paths": list(self.prohibited_absolute_scale_paths),
        }

    def to_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {
            "source_plan_id": self.source_plan_id,
            "runtime_paths": self.ies2rad.expected_paths.runtime_payload(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    def aperture_radiance_text(self) -> str:
        header = (
            f"# source_plan_id={self.source_plan_id}\n"
            "# eight GLB-derived downward illum apertures per Conventional fixture\n"
        )
        return header + "\n".join(_format_aperture(item).rstrip() for item in self.apertures) + "\n"


def build_conventional_radiance_source_plan(
    layout: ConventionalLayoutPlan,
    *,
    workspace: str | Path,
    ies2rad_bin: str | Path = "ies2rad",
    data_root: str | Path | None = None,
    global_dimming_factor: float = 1.0,
    emitting_boundaries: Sequence[FixtureEmittingBoundary] | None = None,
) -> ConventionalRadianceSourcePlan:
    """Connect approved profile and layout models to a non-executing source plan."""

    profile = build_conventional_comparison_profile(data_root=data_root)
    if layout.conventional_profile_id != profile.profile_id:
        raise ConventionalRadianceSourceError("layout/profile identity mismatch.")
    photometry = load_approved_lm63(data_root=data_root)
    original = OriginalIesIdentity(
        resource_name=CONVENTIONAL_IES_RESOURCE_NAME,
        sha256=CONVENTIONAL_IES_SHA256,
        product_id=_required_keyword(photometry, "LUMCAT"),
        test_id=_required_keyword(photometry, "TEST"),
        tested_input_watts=photometry.input_watts,
        candela_multiplier=photometry.candela_multiplier,
        ballast_factor=photometry.ballast_factor,
        future_use_factor_2019=photometry.future_use_factor,
    )
    derived = build_downward_normalized_derived_ies(
        photometry,
        original_ies=original,
    )
    angular = normalize_lm63_angular_distribution(
        photometry,
        asset_sha256=CONVENTIONAL_IES_SHA256,
    )
    dimming_factor = _closed_unit_interval(
        "global_dimming_factor", global_dimming_factor
    )
    carrier = derive_conventional_carrier_scale(
        profile.modeled_operating_point.par_ppf_umol_s_per_fixture
        * dimming_factor
    )
    command = build_ies2rad_command_plan(
        workspace,
        derived_ies_filename=derived.filename,
        carrier_scale=carrier,
        ies2rad_bin=ies2rad_bin,
    )
    shared = SharedAngularSourceIdentity(
        angular_modifier_id=CONVENTIONAL_SHARED_ANGULAR_MODIFIER,
        light_modifier_id=CONVENTIONAL_SHARED_LIGHT_MODIFIER,
        original_angular_distribution_id=angular.distribution_id,
        derived_ies_sha256=derived.sha256,
    )
    if emitting_boundaries is None:
        from fspm_optics.fixtures.occlusion import (
            extract_conventional_emitting_boundaries,
        )

        boundaries = extract_conventional_emitting_boundaries(
            layout.to_payload()
        )
    else:
        boundaries = tuple(emitting_boundaries)
    boundary_by_fixture: dict[str, list[FixtureEmittingBoundary]] = {}
    for boundary in boundaries:
        boundary_by_fixture.setdefault(boundary.fixture_id, []).append(boundary)
    if (
        set(boundary_by_fixture)
        != {item.identity.fixture_id for item in layout.fixtures}
        or any(len(items) != 8 for items in boundary_by_fixture.values())
    ):
        raise ConventionalRadianceSourceError(
            "GLB emitting boundaries do not cover eight bars per fixture."
        )
    per_fixture_areas = tuple(
        math.fsum(item.area_m2 for item in boundary_by_fixture[fixture.identity.fixture_id])
        for fixture in layout.fixtures
    )
    if not all(
        math.isclose(area, per_fixture_areas[0], rel_tol=0.0, abs_tol=1.0e-10)
        for area in per_fixture_areas
    ):
        raise ConventionalRadianceSourceError(
            "all Conventional LED GLB instances must preserve one emitting boundary area."
        )
    fixture_by_id = {
        item.identity.fixture_id: item for item in layout.fixtures
    }
    apertures = tuple(
        _aperture_from_boundary(
            boundary,
            fixture_by_id[boundary.fixture_id],
            shared,
            transported_downward_ppf_umol_s=(
                carrier.fixture_ppf_umol_s / 8.0
            ),
        )
        for boundary in boundaries
    )
    angular_identity = AngularNormalizationIdentity(
        distribution_id=angular.distribution_id,
        policy=ANGULAR_NORMALIZATION_POLICY,
        original_ies_sha256=CONVENTIONAL_IES_SHA256,
    )
    modeled = profile.modeled_operating_point
    operating_identity = DeclaredOperatingPointIdentity(
        profile_id=profile.profile_id,
        profile_sha256=profile.profile_sha256,
        electrical_power_w_per_fixture=modeled.electrical_power_w_per_fixture,
        par_ppe_umol_per_j=modeled.par_ppe_umol_per_j,
        par_ppf_umol_s_per_fixture=modeled.par_ppf_umol_s_per_fixture,
        basis=modeled.basis,
    )
    return ConventionalRadianceSourcePlan(
        profile_id=profile.profile_id,
        profile_sha256=profile.profile_sha256,
        layout_id=layout.layout_id,
        original_ies=original,
        derived_ies=derived,
        angular_normalization=angular_identity,
        declared_operating_point=operating_identity,
        carrier_scale=carrier,
        ies2rad=command,
        shared_angular_source=shared,
        apertures=apertures,
        fixture_count=len(layout.fixtures),
        emitting_boundary_area_m2_per_fixture=per_fixture_areas[0],
        transported_downward_ppf_umol_s_per_fixture=carrier.fixture_ppf_umol_s,
        whole_layout_transported_downward_ppf_umol_s=(
            len(layout.fixtures) * carrier.fixture_ppf_umol_s
        ),
        channel_policy=BASELINE_SOURCE_CHANNEL_POLICY,
        claim_boundary=profile.scenario_claim_boundary,
        prohibited_absolute_scale_paths=(
            "legacy_lumen_integration",
            "relative_spd_umol_per_lumen_absolute_bridge",
            "raw_ies_candela_absolute_par",
            "ies_lamp_lumens_as_ppf",
            "ies_test_watts_as_modeled_power",
            "post_normalization_lm63_uniform_factors",
            "second_source_multiplier",
            "post_trace_179_conversion",
            "post_trace_ppf_rescaling",
            "fixture_level_independent_dimming",
            "eight_bar_source_duplication",
        ),
    )


def format_conventional_source_plan_json(plan: ConventionalRadianceSourcePlan) -> str:
    return plan.to_json()


def format_conventional_apertures_rad(plan: ConventionalRadianceSourcePlan) -> str:
    """Return eight explicit GLB-derived bar apertures per fixture."""

    return plan.aperture_radiance_text()


def _aperture_from_boundary(
    boundary: FixtureEmittingBoundary,
    fixture: ConventionalFixtureInstance,
    shared: SharedAngularSourceIdentity,
    *,
    transported_downward_ppf_umol_s: float,
) -> ApertureSource:
    identity = fixture.identity
    vertices = boundary.vertices_m
    key = {
        "fixture_id": identity.fixture_id,
        "bar_index": boundary.bar_index,
        "shared_angular_identity": shared.identity,
        "vertices_m": [list(vertex) for vertex in vertices],
        "transported_downward_ppf_umol_s": transported_downward_ppf_umol_s,
    }
    digest = _hash_payload(key)[:20]
    grid = identity.grid_index
    return ApertureSource(
        source_id=f"conventional-aperture-source-v3-{digest}",
        polygon_id=(
            f"conventional_aperture_r{grid.row_y:03d}_c{grid.column_x:03d}"
            f"_b{boundary.bar_index:02d}_{digest}"
        ),
        fixture_id=identity.fixture_id,
        row_y=grid.row_y,
        column_x=grid.column_x,
        bar_index=boundary.bar_index,
        angular_light_modifier_id=shared.light_modifier_id,
        vertices_m=vertices,
        normal=(0.0, 0.0, -1.0),
        transported_downward_ppf_umol_s=transported_downward_ppf_umol_s,
        area_m2=boundary.area_m2,
    )


def _parse_radiance_primitives(text: str) -> tuple[RadiancePrimitive, ...]:
    tokens: list[str] = []
    for line in text.splitlines():
        content = line.partition("#")[0].strip()
        if content:
            tokens.extend(
                _unquote(token)
                for token in re.findall(r"'[^']*'|\"[^\"]*\"|\S+", content)
            )
    primitives: list[RadiancePrimitive] = []
    cursor = 0
    try:
        while cursor < len(tokens):
            modifier, primitive_type, identifier = tokens[cursor : cursor + 3]
            cursor += 3
            string_count = int(tokens[cursor])
            cursor += 1
            strings = tuple(tokens[cursor : cursor + string_count])
            cursor += string_count
            integer_count = int(tokens[cursor])
            cursor += 1
            integers = tuple(int(value) for value in tokens[cursor : cursor + integer_count])
            cursor += integer_count
            real_count = int(tokens[cursor])
            cursor += 1
            reals = tuple(float(value) for value in tokens[cursor : cursor + real_count])
            cursor += real_count
            if any(not math.isfinite(value) for value in reals):
                raise ValueError
            primitives.append(
                RadiancePrimitive(
                    modifier,
                    primitive_type,
                    identifier,
                    strings,
                    integers,
                    reals,
                )
            )
    except (IndexError, ValueError) as exc:
        raise ConvertedIesOutputError("malformed limited ies2rad RAD output.") from exc
    if not primitives:
        raise ConvertedIesOutputError("converted RAD output contains no primitives.")
    return tuple(primitives)


def _validate_centered_box_geometry(
    polygons: tuple[RadiancePrimitive, ...],
    *,
    light_modifier: str,
    length_x_m: float,
    width_y_m: float,
    height_z_m: float,
) -> None:
    expected_ids = tuple(
        f"{CONVENTIONAL_IES2RAD_OUTPUT_ROOT}{suffix}"
        for suffix in (".d", ".u", ".1", ".2", ".3", ".4")
    )
    if tuple(item.identifier for item in polygons) != expected_ids:
        raise ConvertedIesOutputError("converted box geometry IDs are unexpected.")
    expected_vertices = {
        (x, y, z)
        for x in (-length_x_m / 2.0, length_x_m / 2.0)
        for y in (-width_y_m / 2.0, width_y_m / 2.0)
        for z in (-height_z_m / 2.0, height_z_m / 2.0)
    }
    seen: set[tuple[float, float, float]] = set()
    for polygon in polygons:
        if (
            polygon.modifier != light_modifier
            or polygon.primitive_type != "polygon"
            or polygon.string_arguments
            or polygon.integer_arguments
            or len(polygon.real_arguments) != 12
        ):
            raise ConvertedIesOutputError("converted output contains unknown emitting geometry.")
        vertices = tuple(
            tuple(polygon.real_arguments[index : index + 3])
            for index in range(0, 12, 3)
        )
        for vertex in vertices:
            if not any(
                all(
                    math.isclose(a, b, rel_tol=0.0, abs_tol=1e-9)
                    for a, b in zip(vertex, expected, strict=True)
                )
                for expected in expected_vertices
            ):
                raise ConvertedIesOutputError(
                    "converted box is not centered with length-X/width-Y dimensions."
                )
            seen.add(vertex)
    if len(seen) != 8:
        raise ConvertedIesOutputError("converted box does not contain all centered corners.")
    downward = tuple(
        tuple(polygons[0].real_arguments[index : index + 3])
        for index in range(0, 12, 3)
    )
    if _polygon_unit_normal(downward) != (0.0, 0.0, -1.0):
        raise ConvertedIesOutputError("converted lower aperture is not aimed toward negative Z.")


def _polygon_unit_normal(
    vertices: tuple[tuple[float, float, float], ...],
) -> tuple[float, float, float]:
    a, b, c = vertices[:3]
    first = tuple(b[index] - a[index] for index in range(3))
    second = tuple(c[index] - a[index] for index in range(3))
    cross = (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
    magnitude = math.sqrt(sum(value * value for value in cross))
    if magnitude == 0.0:
        raise ConventionalRadianceSourceError("polygon vertices are degenerate.")
    return tuple(0.0 if abs(value / magnitude) < 1e-15 else value / magnitude for value in cross)


def _format_primitive(primitive: RadiancePrimitive) -> str:
    strings = " ".join(primitive.string_arguments)
    integers = " ".join(str(value) for value in primitive.integer_arguments)
    reals = " ".join(_number(value) for value in primitive.real_arguments)
    return (
        f"{primitive.modifier} {primitive.primitive_type} {primitive.identifier}\n"
        f"{len(primitive.string_arguments)}{(' ' + strings) if strings else ''}\n"
        f"{len(primitive.integer_arguments)}{(' ' + integers) if integers else ''}\n"
        f"{len(primitive.real_arguments)}{(' ' + reals) if reals else ''}\n"
    )


def _format_aperture(aperture: ApertureSource) -> str:
    lines = [
        f"{aperture.angular_light_modifier_id} polygon {aperture.polygon_id}",
        "0",
        "0",
        "12",
    ]
    lines.extend(" ".join(_number(value) for value in vertex) for vertex in aperture.vertices_m)
    return "\n".join(lines) + "\n"


def _required_keyword(photometry: Lm63Photometry, name: str) -> str:
    value = photometry.keyword(name)
    if value is None or not value.strip():
        raise ConventionalRadianceSourceError(f"approved IES is missing [{name}].")
    return value.strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _hash_payload(payload: object) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _number(value: float | int) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ConventionalRadianceSourceError("source values must be finite.")
    return format(number, ".15g")


def _canonical_ies2rad_output_number(value: float | int) -> float:
    """Mirror the C ``%g`` serialization used in native ``ies2rad`` output."""

    number = _positive("ies2rad carrier", value)
    canonical = float(format(number, f".{IES2RAD_OUTPUT_SIGNIFICANT_DIGITS}g"))
    if not math.isfinite(canonical) or canonical <= 0.0:
        raise ConventionalRadianceSourceError(
            "ies2rad carrier is not representable at native output precision."
        )
    return canonical


def _positive(name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConventionalRadianceSourceError(f"{name} must be numeric.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ConventionalRadianceSourceError(f"{name} must be finite and positive.")
    return number


def _closed_unit_interval(name: str, value: float | int) -> float:
    number = _positive(name, value)
    if number > 1.0:
        raise ConventionalRadianceSourceError(f"{name} must be no greater than 1.")
    return number
