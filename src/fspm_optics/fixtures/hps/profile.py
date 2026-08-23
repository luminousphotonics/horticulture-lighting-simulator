"""Immutable measured-asset and modeled-operating-point HPS identity."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Final

from .errors import HpsProfileError
from .lm63 import load_hps_lm63
from .resources import (
    HPS_IES_RESOURCE_NAME,
    HPS_IES_SHA256,
    HPS_SPD_RESOURCE_NAME,
    HPS_SPD_SHA256,
    load_hps_spd,
)

HPS_TESTED_SYSTEM_INPUT_POWER_W: Final = 1045.0
HPS_INITIAL_LAMP_PAR_PPF_UMOL_S: Final = 1750.0
HPS_SYSTEM_PPE_UMOL_PER_J: Final = (
    HPS_INITIAL_LAMP_PAR_PPF_UMOL_S / HPS_TESTED_SYSTEM_INPUT_POWER_W
)
HPS_NOMINAL_LAMP_CLASS_POWER_W: Final = 1000.0
HPS_COMPARISON_PROFILE_ID: Final = "hps_1000w_source_authority_v3"
HPS_SOURCE_ID: Final = "hps_fixed_initial_par_source_v3"
HPS_ANGULAR_DISTRIBUTION_ID: Final = (
    "hps_unit_downward_type_c_v3_" + HPS_IES_SHA256[:20]
)
HPS_SPECTRAL_ASSET_ID: Final = "hps_1000w_relative_spd_v3"
DECLARED_OPERATING_POINT_BASIS: Final = (
    "documented_initial_lamp_ppf_and_tested_system_input"
)
HPS_SCENARIO_CLAIM_BOUNDARY: Final = (
    "A fixed-output 1000 W-class HPS comparator using a sanitized measured "
    "Type-C angular shape and luminous-opening footprint, with a declared "
    "initial lamp PAR PPF of 1750 µmol/s and 1045 W tested system input "
    "defining 1.6746411483253588 µmol/J system PPE. The nominal 1000 W "
    "lamp class is provenance only. The IES does not supply absolute PAR "
    "calibration."
)


def _positive(name: str, value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise HpsProfileError(f"{name} must be finite and positive.")
    return float(value)


@dataclass(frozen=True, slots=True)
class HpsMeasuredAsset:
    asset_id: str
    resource_name: str
    sha256: str
    role: str
    provenance: str
    license_name: str | None
    acquisition_history: str | None
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if not all((self.asset_id, self.resource_name, self.sha256, self.role, self.provenance)):
            raise HpsProfileError("HPS asset identity fields must be non-empty.")
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise HpsProfileError("HPS asset SHA-256 is malformed.")
        if self.license_name is not None or self.acquisition_history is not None:
            raise HpsProfileError(
                "HPS asset license and acquisition history are not established."
            )
        if not self.limitations or any(not value for value in self.limitations):
            raise HpsProfileError("HPS asset limitations must be non-empty.")

    def to_payload(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "resource_name": self.resource_name,
            "sha256": self.sha256,
            "role": self.role,
            "provenance": self.provenance,
            "license_name": self.license_name,
            "acquisition_history": self.acquisition_history,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class HpsFixtureDimensionsM:
    length_x_m: float
    width_y_m: float
    emitting_height_m: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "length_x_m", _positive("length_x_m", self.length_x_m))
        object.__setattr__(self, "width_y_m", _positive("width_y_m", self.width_y_m))
        if self.emitting_height_m != 0.0:
            raise HpsProfileError("HPS emitting height must preserve the zero-height IES.")

    def to_payload(self) -> dict[str, float | str]:
        return {
            "length_x_m": self.length_x_m,
            "width_y_m": self.width_y_m,
            "emitting_height_m": self.emitting_height_m,
            "semantics": "flat_zero_height_luminous_opening_not_housing_depth",
        }


@dataclass(frozen=True, slots=True)
class HpsIesTestMetadata:
    lm63_version: str
    test_id: str
    test_laboratory: str
    issue_date_text: str
    manufacturer: str
    catalog_id: str
    luminaire_description: str
    lamp_description: str
    ballast_description: str
    lamp_count: int
    nominal_lamp_power_w: float
    lumens_per_lamp_field: float
    tested_input_watts: float

    def __post_init__(self) -> None:
        text_fields = (
            self.lm63_version,
            self.test_id,
            self.test_laboratory,
            self.issue_date_text,
            self.manufacturer,
            self.catalog_id,
            self.luminaire_description,
            self.lamp_description,
            self.ballast_description,
        )
        if any(not value for value in text_fields):
            raise HpsProfileError("HPS IES test metadata fields must be non-empty.")
        if self.lamp_count != 1:
            raise HpsProfileError("HPS IES must retain one tested lamp.")
        if self.nominal_lamp_power_w != 1000.0:
            raise HpsProfileError(
                "HPS nominal lamp class must remain 1000 W provenance."
            )
        if self.lumens_per_lamp_field != 150000.0:
            raise HpsProfileError("HPS IES lumen field must remain provenance at 150000 lm.")
        if self.tested_input_watts != 1045.0:
            raise HpsProfileError("HPS IES tested input must remain 1045 W.")

    def to_payload(self) -> dict[str, object]:
        return {
            "lm63_version": self.lm63_version,
            "test_id": self.test_id,
            "test_laboratory": self.test_laboratory,
            "issue_date_text": self.issue_date_text,
            "manufacturer": self.manufacturer,
            "catalog_id": self.catalog_id,
            "luminaire_description": self.luminaire_description,
            "lamp_description": self.lamp_description,
            "ballast_description": self.ballast_description,
            "lamp_count": self.lamp_count,
            "nominal_lamp_power_w": self.nominal_lamp_power_w,
            "lumens_per_lamp_field": self.lumens_per_lamp_field,
            "tested_input_watts": self.tested_input_watts,
            "role": "measured_photometric_test_provenance",
            "sets_tested_system_input_power_authority": True,
            "sets_initial_lamp_PAR_PPF_authority": False,
            "sets_absolute_PAR_from_photometric_magnitude": False,
        }


@dataclass(frozen=True, slots=True)
class HpsDeclaredOperatingPoint:
    electrical_power_w_per_fixture: float
    par_ppe_umol_per_j: float
    par_ppf_umol_s_per_fixture: float
    basis: str
    fixed_output: bool = True

    def __post_init__(self) -> None:
        for name in (
            "electrical_power_w_per_fixture",
            "par_ppe_umol_per_j",
            "par_ppf_umol_s_per_fixture",
        ):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        if self.basis != DECLARED_OPERATING_POINT_BASIS or not self.fixed_output:
            raise HpsProfileError("HPS source authority must remain fixed-output.")
        if not math.isclose(
            self.electrical_power_w_per_fixture * self.par_ppe_umol_per_j,
            self.par_ppf_umol_s_per_fixture,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise HpsProfileError("HPS power × PAR PPE must equal declared PAR PPF.")

    def to_payload(self) -> dict[str, object]:
        return {
            "electrical_power_w_per_fixture": self.electrical_power_w_per_fixture,
            "par_ppe_umol_per_j": self.par_ppe_umol_per_j,
            "par_ppf_umol_s_per_fixture": self.par_ppf_umol_s_per_fixture,
            "basis": self.basis,
            "initial_lamp_par_ppf_umol_s": self.par_ppf_umol_s_per_fixture,
            "tested_system_input_power_w": self.electrical_power_w_per_fixture,
            "computed_system_ppe_umol_per_j": self.par_ppe_umol_per_j,
            "nominal_lamp_class_power_w": HPS_NOMINAL_LAMP_CLASS_POWER_W,
            "nominal_lamp_class_is_provenance_only": True,
            "fixed_output": self.fixed_output,
        }


@dataclass(frozen=True, slots=True)
class HpsComparisonProfile:
    profile_id: str
    source_id: str
    display_name: str
    claim_boundary: str
    photometric_asset: HpsMeasuredAsset
    spectral_asset: HpsMeasuredAsset
    fixture_dimensions_m: HpsFixtureDimensionsM
    ies_test: HpsIesTestMetadata
    modeled_operating_point: HpsDeclaredOperatingPoint
    angular_distribution_id: str

    def __post_init__(self) -> None:
        if (
            self.profile_id != HPS_COMPARISON_PROFILE_ID
            or self.source_id != HPS_SOURCE_ID
            or self.claim_boundary != HPS_SCENARIO_CLAIM_BOUNDARY
            or self.angular_distribution_id != HPS_ANGULAR_DISTRIBUTION_ID
        ):
            raise HpsProfileError("HPS profile scientific identity is inconsistent.")
        if self.fixture_dimensions_m != HpsFixtureDimensionsM(0.798576, 0.603504, 0.0):
            raise HpsProfileError("HPS fixture dimensions must preserve the IES footprint.")
        if self.ies_test.tested_input_watts != 1045.0:
            raise HpsProfileError("HPS tested input must remain distinct provenance at 1045 W.")
        modeled = self.modeled_operating_point
        if (
            modeled.electrical_power_w_per_fixture,
            modeled.par_ppe_umol_per_j,
            modeled.par_ppf_umol_s_per_fixture,
        ) != (
            HPS_TESTED_SYSTEM_INPUT_POWER_W,
            HPS_SYSTEM_PPE_UMOL_PER_J,
            HPS_INITIAL_LAMP_PAR_PPF_UMOL_S,
        ):
            raise HpsProfileError(
                "HPS source authority must remain 1750 umol/s initial lamp PAR PPF / "
                "1045 W tested system input / computed system PPE."
            )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "profile_id": self.profile_id,
            "source_id": self.source_id,
            "display_name": self.display_name,
            "claim_boundary": self.claim_boundary,
            "assets": {
                "measured_photometric_shape_and_footprint": self.photometric_asset.to_payload(),
                "relative_spectral_shape": self.spectral_asset.to_payload(),
            },
            "fixture_dimensions_m": self.fixture_dimensions_m.to_payload(),
            "ies_test_provenance": self.ies_test.to_payload(),
            "source_operating_point": self.modeled_operating_point.to_payload(),
            "angular_distribution_id": self.angular_distribution_id,
            "calibration_boundaries": {
                "ies_supplies_angular_shape_and_footprint": True,
                "documented_initial_lamp_PAR_PPF_is_absolute_authority": True,
                "tested_system_input_power_is_absolute_authority": True,
                "system_PPE_is_computed_from_PPF_over_power": True,
                "fixed_output": True,
                "ies_lumens_or_raw_candela_define_PAR_PPF": False,
                "ies_test_watts_measure_PAR_efficacy": False,
                "spd_amplitude_defines_PAR_PPF_or_power": False,
            },
        }

    @property
    def profile_sha256(self) -> str:
        canonical = json.dumps(
            self.to_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


def build_hps_comparison_profile(
    *,
    data_root: str | Path | None = None,
) -> HpsComparisonProfile:
    ies = load_hps_lm63(data_root=data_root)
    spd = load_hps_spd(data_root=data_root)
    return HpsComparisonProfile(
        profile_id=HPS_COMPARISON_PROFILE_ID,
        source_id=HPS_SOURCE_ID,
        display_name="1000 W HPS comparator",
        claim_boundary=HPS_SCENARIO_CLAIM_BOUNDARY,
        photometric_asset=HpsMeasuredAsset(
            asset_id="hps_1000w_lm63_2002_v3",
            resource_name=HPS_IES_RESOURCE_NAME,
            sha256=HPS_IES_SHA256,
            role="measured_type_c_angular_shape_and_luminous_opening_footprint",
            provenance="sanitized source-neutral LM-63 photometric record",
            license_name=None,
            acquisition_history=None,
            limitations=(
                "The IES does not measure PAR PPF or PPE.",
                "The bracketed source metadata is intentionally anonymized.",
                "The zero-height opening does not establish housing depth or lamp geometry.",
            ),
        ),
        spectral_asset=HpsMeasuredAsset(
            asset_id=HPS_SPECTRAL_ASSET_ID,
            resource_name=HPS_SPD_RESOURCE_NAME,
            sha256=HPS_SPD_SHA256,
            role="approved_relative_radiant_spectral_shape",
            provenance="hps_1000w_spd.csv relative SPD resource",
            license_name=None,
            acquisition_history=None,
            limitations=(
                "Not verified as spectroradiometry of the tested HPS lamp.",
                "Relative amplitude does not set PAR PPF, PPE, or electrical power.",
                "The resource identifies relative spectral shape only.",
            ),
        ),
        fixture_dimensions_m=HpsFixtureDimensionsM(
            length_x_m=ies.fixture_length_m,
            width_y_m=ies.fixture_width_m,
            emitting_height_m=ies.fixture_height_m,
        ),
        ies_test=HpsIesTestMetadata(
            lm63_version=ies.version,
            test_id=_required_keyword(ies, "TEST"),
            test_laboratory=_required_keyword(ies, "TESTLAB"),
            issue_date_text=_required_keyword(ies, "ISSUEDATE"),
            manufacturer=_required_keyword(ies, "MANUFAC"),
            catalog_id=_required_keyword(ies, "LUMCAT"),
            luminaire_description=_required_keyword(ies, "LUMINAIRE"),
            lamp_description=_required_keyword(ies, "LAMP"),
            ballast_description=_required_keyword(ies, "OTHER"),
            lamp_count=ies.lamp_count,
            nominal_lamp_power_w=HPS_NOMINAL_LAMP_CLASS_POWER_W,
            lumens_per_lamp_field=ies.lumens_per_lamp,
            tested_input_watts=ies.input_watts,
        ),
        modeled_operating_point=HpsDeclaredOperatingPoint(
            electrical_power_w_per_fixture=HPS_TESTED_SYSTEM_INPUT_POWER_W,
            par_ppe_umol_per_j=HPS_SYSTEM_PPE_UMOL_PER_J,
            par_ppf_umol_s_per_fixture=HPS_INITIAL_LAMP_PAR_PPF_UMOL_S,
            basis=DECLARED_OPERATING_POINT_BASIS,
        ),
        angular_distribution_id=HPS_ANGULAR_DISTRIBUTION_ID,
    )


def format_hps_profile_json(profile: HpsComparisonProfile) -> str:
    return json.dumps(
        profile.to_payload() | {"profile_sha256": profile.profile_sha256},
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ) + "\n"


def _required_keyword(ies, name: str) -> str:
    value = ies.keyword(name)
    if value is None or not value.strip():
        raise HpsProfileError(f"approved HPS IES is missing [{name}] metadata.")
    return value.strip()
