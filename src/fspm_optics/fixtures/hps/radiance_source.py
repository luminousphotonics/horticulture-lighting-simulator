"""Pure derived-IES carrier and flat-aperture planning for HPS fixtures."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Final

from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.native_ies import (
    NativeFlatcorrOutputContract,
    NativeIesOutputError,
    validate_native_zero_height_flatcorr_output,
)
from fspm_optics.transport.scalar_ppfd import (
    BASELINE_SOURCE_CHANNEL_POLICY,
    PPFD_CONVERSION_BASIS,
)

from .angular import (
    HPS_ANGULAR_NORMALIZATION_POLICY,
    HpsDerivedIesDocument,
    build_hps_derived_ies,
)
from .errors import HpsSourcePlanError
from .lm63 import load_hps_lm63
from .profile import (
    HPS_INITIAL_LAMP_PAR_PPF_UMOL_S,
    HPS_SCENARIO_CLAIM_BOUNDARY,
    build_hps_comparison_profile,
)

RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W: Final = 179.0
HPS_FIXTURE_PPF_UMOL_S: Final = HPS_INITIAL_LAMP_PAR_PPF_UMOL_S
HPS_RADIANCE_CARRIER_MULTIPLIER: Final = 313250.0
HPS_IES2RAD_OUTPUT_ROOT: Final = "hps_unit_downward_flux"
HPS_ANGULAR_MODIFIER_ID: Final = "hps_unit_downward_flux_dist"
HPS_LIGHT_MODIFIER_ID: Final = "hps_unit_downward_flux_light"
HPS_FLAT_APERTURE_POLICY: Final = "one_zero_height_downward_rectangle_per_fixture"
HpsConvertedIesOutputContract = NativeFlatcorrOutputContract


@dataclass(frozen=True, slots=True)
class HpsCarrierScale:
    fixture_ppf_umol_s: float
    white_efficacy_lm_per_radiance_carrier_w: float
    ies2rad_multiplier: float
    decode_basis: str

    def __post_init__(self) -> None:
        if (
            self.fixture_ppf_umol_s != HPS_FIXTURE_PPF_UMOL_S
            or self.white_efficacy_lm_per_radiance_carrier_w
            != RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W
            or self.ies2rad_multiplier != HPS_RADIANCE_CARRIER_MULTIPLIER
            or not math.isclose(
                self.ies2rad_multiplier,
                self.fixture_ppf_umol_s
                * self.white_efficacy_lm_per_radiance_carrier_w,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ):
            raise HpsSourcePlanError("HPS Radiance carrier derivation is invalid.")
        if self.decode_basis != PPFD_CONVERSION_BASIS:
            raise HpsSourcePlanError("HPS scalar decode basis is invalid.")

    def to_payload(self) -> dict[str, object]:
        return {
            "fixture_ppf_umol_s": self.fixture_ppf_umol_s,
            "radiance_white_efficacy_lm_per_carrier_w": (
                self.white_efficacy_lm_per_radiance_carrier_w
            ),
            "ies2rad_multiplier": self.ies2rad_multiplier,
            "derivation": "declared_fixture_PAR_PPF_umol_s * 179_lm_per_carrier_w",
            "decode_basis": self.decode_basis,
            "post_trace_179_conversion": False,
        }


@dataclass(frozen=True, slots=True)
class HpsFixturePlacement:
    fixture_id: str
    center_x_m: float
    center_y_m: float
    aperture_plane_z_m: float

    def __post_init__(self) -> None:
        if not self.fixture_id:
            raise HpsSourcePlanError("HPS fixture_id must be non-empty.")
        for name in ("center_x_m", "center_y_m", "aperture_plane_z_m"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise HpsSourcePlanError(f"{name} must be numeric.")
            if not math.isfinite(float(value)):
                raise HpsSourcePlanError(f"{name} must be finite.")
            object.__setattr__(self, name, float(value))


@dataclass(frozen=True, slots=True)
class HpsApertureSource:
    source_id: str
    polygon_id: str
    fixture_id: str
    angular_light_modifier_id: str
    vertices_m: tuple[tuple[float, float, float], ...]
    normal: tuple[float, float, float]
    length_x_m: float
    width_y_m: float
    emitting_height_m: float
    transported_downward_ppf_umol_s: float

    def __post_init__(self) -> None:
        if len(self.vertices_m) != 4:
            raise HpsSourcePlanError("HPS aperture must have four vertices.")
        if _polygon_unit_normal(self.vertices_m) != (0.0, 0.0, -1.0):
            raise HpsSourcePlanError("HPS aperture winding must face negative Z.")
        if self.normal != (0.0, 0.0, -1.0):
            raise HpsSourcePlanError("HPS aperture normal must face negative Z.")
        if (self.length_x_m, self.width_y_m, self.emitting_height_m) != (
            0.798576,
            0.603504,
            0.0,
        ):
            raise HpsSourcePlanError("HPS aperture dimensions are invalid.")
        if self.transported_downward_ppf_umol_s != HPS_FIXTURE_PPF_UMOL_S:
            raise HpsSourcePlanError("HPS aperture PPF must remain 1750 umol/s.")

    def to_payload(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "polygon_id": self.polygon_id,
            "fixture_id": self.fixture_id,
            "angular_light_modifier_id": self.angular_light_modifier_id,
            "vertices_m": [list(vertex) for vertex in self.vertices_m],
            "normal": list(self.normal),
            "length_x_m": self.length_x_m,
            "width_y_m": self.width_y_m,
            "emitting_height_m": self.emitting_height_m,
            "transported_downward_ppf_umol_s": self.transported_downward_ppf_umol_s,
        }


@dataclass(frozen=True, slots=True)
class HpsIes2radPlan:
    command: CommandSpec
    derived_ies_path: Path
    converted_rad_path: Path
    converted_dat_path: Path

    def scientific_payload(self) -> dict[str, object]:
        return {
            "executable_role": "ies2rad",
            "argv_after_executable": list(self.command.argv[1:]),
            "derived_ies_filename": self.derived_ies_path.name,
            "converted_rad_filename": self.converted_rad_path.name,
            "converted_dat_filename": self.converted_dat_path.name,
            "shell": False,
            "execution_performed": False,
        }

    def runtime_payload(self) -> dict[str, str]:
        return {
            "workspace": str(self.command.cwd),
            "derived_ies": str(self.derived_ies_path),
            "converted_rad": str(self.converted_rad_path),
            "converted_dat": str(self.converted_dat_path),
        }


@dataclass(frozen=True, slots=True)
class HpsRadianceSourcePlan:
    profile_id: str
    profile_sha256: str
    angular_distribution_id: str
    angular_normalization_policy: str
    derived_ies: HpsDerivedIesDocument
    carrier_scale: HpsCarrierScale
    ies2rad: HpsIes2radPlan
    angular_modifier_id: str
    light_modifier_id: str
    apertures: tuple[HpsApertureSource, ...]
    channel_policy: str
    flat_aperture_policy: str
    claim_boundary: str
    prohibited_scale_or_shape_paths: tuple[str, ...]
    source_plan_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.apertures:
            raise HpsSourcePlanError("HPS source plan requires at least one fixture.")
        if (
            self.angular_modifier_id != HPS_ANGULAR_MODIFIER_ID
            or self.light_modifier_id != HPS_LIGHT_MODIFIER_ID
        ):
            raise HpsSourcePlanError("HPS converted angular/light identities are invalid.")
        if len({item.fixture_id for item in self.apertures}) != len(self.apertures):
            raise HpsSourcePlanError("HPS fixture identities must be unique.")
        if self.channel_policy != BASELINE_SOURCE_CHANNEL_POLICY:
            raise HpsSourcePlanError("HPS source requires equal grey channels.")
        if self.flat_aperture_policy != HPS_FLAT_APERTURE_POLICY:
            raise HpsSourcePlanError("HPS source must preserve flat-aperture semantics.")
        if self.claim_boundary != HPS_SCENARIO_CLAIM_BOUNDARY:
            raise HpsSourcePlanError("HPS source claim boundary is inconsistent.")
        object.__setattr__(
            self,
            "source_plan_id",
            "hps-radiance-source-v2-" + _hash_payload(self.scientific_payload())[:24],
        )

    @property
    def whole_plan_downward_ppf_umol_s(self) -> float:
        return len(self.apertures) * HPS_FIXTURE_PPF_UMOL_S

    def scientific_payload(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "profile_id": self.profile_id,
            "profile_sha256": self.profile_sha256,
            "angular_distribution_id": self.angular_distribution_id,
            "angular_normalization_policy": self.angular_normalization_policy,
            "derived_ies": self.derived_ies.to_payload(),
            "carrier_scale": self.carrier_scale.to_payload(),
            "ies2rad": self.ies2rad.scientific_payload(),
            "shared_angular_source": {
                "angular_modifier_id": self.angular_modifier_id,
                "light_modifier_id": self.light_modifier_id,
                "aperture_correction": "flatcorr",
                "transported_hemisphere": "type_c_vertical_0_to_90_degrees",
            },
            "apertures": [item.to_payload() for item in self.apertures],
            "fixture_count": len(self.apertures),
            "whole_plan_downward_ppf_umol_s": self.whole_plan_downward_ppf_umol_s,
            "channel_policy": self.channel_policy,
            "flat_aperture_policy": self.flat_aperture_policy,
            "claim_boundary": self.claim_boundary,
            "post_trace_scale": None,
            "converted_generated_geometry_enters_final_scene": False,
            "prohibited_scale_or_shape_paths": list(self.prohibited_scale_or_shape_paths),
        }

    def to_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {
            "source_plan_id": self.source_plan_id,
            "runtime_paths": self.ies2rad.runtime_payload(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    def aperture_radiance_text(self) -> str:
        header = (
            f"# source_plan_id={self.source_plan_id}\n"
            "# one zero-height downward 0.798576 x 0.603504 m aperture per HPS fixture\n"
        )
        return header + "\n".join(_format_aperture(item).rstrip() for item in self.apertures) + "\n"


def derive_hps_carrier_scale() -> HpsCarrierScale:
    return HpsCarrierScale(
        fixture_ppf_umol_s=HPS_FIXTURE_PPF_UMOL_S,
        white_efficacy_lm_per_radiance_carrier_w=(
            RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W
        ),
        ies2rad_multiplier=HPS_RADIANCE_CARRIER_MULTIPLIER,
        decode_basis=PPFD_CONVERSION_BASIS,
    )


def build_hps_radiance_source_plan(
    *,
    workspace: str | Path,
    placements: tuple[HpsFixturePlacement, ...] | None = None,
    ies2rad_bin: str | Path = "ies2rad",
    data_root: str | Path | None = None,
) -> HpsRadianceSourcePlan:
    """Build a deterministic, non-executing source plan for explicit placements."""

    fixture_placements = (
        (HpsFixturePlacement("hps_fixture_000", 0, 0, 0),)
        if placements is None
        else tuple(placements)
    )
    if not fixture_placements:
        raise HpsSourcePlanError("HPS source plan placements must not be empty.")
    profile = build_hps_comparison_profile(data_root=data_root)
    photometry = load_hps_lm63(data_root=data_root)
    derived = build_hps_derived_ies(photometry)
    carrier = derive_hps_carrier_scale()
    workspace_path = Path(workspace)
    executable = str(ies2rad_bin)
    if not executable:
        raise HpsSourcePlanError("ies2rad executable token must be non-empty.")
    derived_path = workspace_path / derived.filename
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
            _number(carrier.ies2rad_multiplier),
            "-o",
            HPS_IES2RAD_OUTPUT_ROOT,
            derived.filename,
        ),
        cwd=workspace_path,
        label="convert_hps_unit_downward_flux_ies",
    )
    conversion = HpsIes2radPlan(
        command=command,
        derived_ies_path=derived_path,
        converted_rad_path=workspace_path / f"{HPS_IES2RAD_OUTPUT_ROOT}.rad",
        converted_dat_path=workspace_path / f"{HPS_IES2RAD_OUTPUT_ROOT}.dat",
    )
    apertures = tuple(_aperture_from_placement(item) for item in fixture_placements)
    return HpsRadianceSourcePlan(
        profile_id=profile.profile_id,
        profile_sha256=profile.profile_sha256,
        angular_distribution_id=profile.angular_distribution_id,
        angular_normalization_policy=HPS_ANGULAR_NORMALIZATION_POLICY,
        derived_ies=derived,
        carrier_scale=carrier,
        ies2rad=conversion,
        angular_modifier_id=HPS_ANGULAR_MODIFIER_ID,
        light_modifier_id=HPS_LIGHT_MODIFIER_ID,
        apertures=apertures,
        channel_policy=BASELINE_SOURCE_CHANNEL_POLICY,
        flat_aperture_policy=HPS_FLAT_APERTURE_POLICY,
        claim_boundary=profile.claim_boundary,
        prohibited_scale_or_shape_paths=(
            "legacy_lumen_integration",
            "legacy_371_25_degree_horizontal_weights",
            "d4_post_trace_symmetrization",
            "ies_lumen_calibration",
            "relative_spd_umol_per_lumen_absolute_bridge",
            "raw_candela_absolute_PAR_bridge",
            "post_trace_179_conversion",
            "post_trace_PPF_rescaling",
            "target_based_dimming",
        ),
    )


def format_hps_source_plan_json(plan: HpsRadianceSourcePlan) -> str:
    return plan.to_json()


def format_hps_apertures_rad(plan: HpsRadianceSourcePlan) -> str:
    return plan.aperture_radiance_text()


def validate_converted_hps_ies_output(
    text: str,
    *,
    expected_dat_reference: str = f"{HPS_IES2RAD_OUTPUT_ROOT}.dat",
) -> HpsConvertedIesOutputContract:
    """Validate the exact native zero-height HPS ``ies2rad`` source subset."""

    try:
        return validate_native_zero_height_flatcorr_output(
            text,
            output_root=HPS_IES2RAD_OUTPUT_ROOT,
            angular_modifier_id=HPS_ANGULAR_MODIFIER_ID,
            light_modifier_id=HPS_LIGHT_MODIFIER_ID,
            dat_reference=expected_dat_reference,
            carrier_multiplier=HPS_RADIANCE_CARRIER_MULTIPLIER,
            length_x_m=0.798576,
            width_y_m=0.603504,
        )
    except NativeIesOutputError as exc:
        raise HpsSourcePlanError(str(exc)) from exc


def _aperture_from_placement(placement: HpsFixturePlacement) -> HpsApertureSource:
    half_x = 0.798576 / 2.0
    half_y = 0.603504 / 2.0
    x = placement.center_x_m
    y = placement.center_y_m
    z = placement.aperture_plane_z_m
    vertices = (
        (x - half_x, y - half_y, z),
        (x - half_x, y + half_y, z),
        (x + half_x, y + half_y, z),
        (x + half_x, y - half_y, z),
    )
    digest = _hash_payload(
        {
            "fixture_id": placement.fixture_id,
            "vertices_m": [list(vertex) for vertex in vertices],
            "angular_light_modifier_id": HPS_LIGHT_MODIFIER_ID,
            "transported_downward_ppf_umol_s": HPS_FIXTURE_PPF_UMOL_S,
        }
    )[:20]
    return HpsApertureSource(
        source_id=f"hps-aperture-source-v2-{digest}",
        polygon_id=f"hps_aperture_{digest}",
        fixture_id=placement.fixture_id,
        angular_light_modifier_id=HPS_LIGHT_MODIFIER_ID,
        vertices_m=vertices,
        normal=(0.0, 0.0, -1.0),
        length_x_m=0.798576,
        width_y_m=0.603504,
        emitting_height_m=0.0,
        transported_downward_ppf_umol_s=HPS_FIXTURE_PPF_UMOL_S,
    )


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
    magnitude = math.sqrt(math.fsum(value * value for value in cross))
    if magnitude == 0.0:
        raise HpsSourcePlanError("HPS aperture vertices are degenerate.")
    return tuple(0.0 if abs(value / magnitude) < 1e-15 else value / magnitude for value in cross)


def _format_aperture(aperture: HpsApertureSource) -> str:
    lines = [
        f"{aperture.angular_light_modifier_id} polygon {aperture.polygon_id}",
        "0",
        "0",
        "12",
    ]
    lines.extend(" ".join(_number(value) for value in vertex) for vertex in aperture.vertices_m)
    return "\n".join(lines) + "\n"


def _hash_payload(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _number(value: float | int) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise HpsSourcePlanError("HPS source-plan values must be finite.")
    return format(number, ".15g")
