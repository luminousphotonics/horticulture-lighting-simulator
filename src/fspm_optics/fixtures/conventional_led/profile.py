"""Immutable scientific identity for the declared Conventional comparator."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Final

from .angular import angular_distribution_id
from .errors import ConventionalProfileError
from .lm63 import Lm63Photometry, load_approved_lm63
from .resources import (
    CONVENTIONAL_IES_RESOURCE_NAME,
    CONVENTIONAL_IES_SHA256,
    CONVENTIONAL_SPD_RESOURCE_NAME,
    CONVENTIONAL_SPD_SHA256,
    load_conventional_spd,
)
from .spectral import spectral_distribution_id

CONVENTIONAL_FIXTURE_POWER_W: Final = 660.0
CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J: Final = 2.6
CONVENTIONAL_FIXTURE_PPF_UMOL_S: Final = 1716.0
CONVENTIONAL_COMPARISON_PROFILE_ID: Final = (
    "rated_conventional_led_8_bar_shape_660w_2p6_v2"
)
CONVENTIONAL_SOURCE_ID: Final = "conventional_led_rated_660w_source_v2"
CONVENTIONAL_IES_ASSET_ID: Final = "conventional_led_8_bar_lm63_2019"
CONVENTIONAL_SPD_ASSET_ID: Final = "conventional_led_relative_spd_asset_v2"
SCENARIO_CLAIM_BOUNDARY: Final = (
    "A rated Conventional LED system using the normalized "
    "angular-distribution characteristics and footprint of approved 8-bar "
    "photometry, with a declared 660 W, 2.6 µmol/J, 1,716 µmol/s "
    "operating point. The LM-63 input watts are provenance only, not the "
    "rated electrical authority."
)
DECLARED_OPERATING_POINT_BASIS: Final = "frozen_rated_fixture_authority"
USER_CONFIRMED_AUTHORIZATION: Final = "user_confirmed_use_and_redistribution"


def _positive(name: str, value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise ConventionalProfileError(f"{name} must be finite and positive.")
    return float(value)


@dataclass(frozen=True, slots=True)
class ApprovedSourceAsset:
    asset_id: str
    resource_name: str
    sha256: str
    role: str
    source_identity: str
    authorization_status: str
    user_authorized_use: bool
    user_authorized_redistribution: bool
    license_name: str | None
    acquisition_history: str | None
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "asset_id",
            "resource_name",
            "sha256",
            "role",
            "source_identity",
            "authorization_status",
        ):
            if not getattr(self, name):
                raise ConventionalProfileError(f"asset {name} must be non-empty.")
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ConventionalProfileError("asset sha256 is malformed.")
        if (
            not isinstance(self.user_authorized_use, bool)
            or not isinstance(self.user_authorized_redistribution, bool)
            or not self.user_authorized_use
            or not self.user_authorized_redistribution
        ):
            raise ConventionalProfileError(
                "approved Conventional assets require user-confirmed use and redistribution."
            )
        if self.authorization_status != USER_CONFIRMED_AUTHORIZATION:
            raise ConventionalProfileError("asset authorization status is inconsistent.")
        if self.license_name is not None or self.acquisition_history is not None:
            raise ConventionalProfileError(
                "no license name or acquisition history is established for approved assets."
            )
        if isinstance(self.limitations, str):
            raise ConventionalProfileError("asset limitations must be a tuple of strings.")
        limitations = tuple(self.limitations)
        if not limitations or any(not value for value in limitations):
            raise ConventionalProfileError("asset limitations must be non-empty strings.")
        object.__setattr__(self, "limitations", limitations)

    def to_dict(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "resource_name": self.resource_name,
            "sha256": self.sha256,
            "role": self.role,
            "source_identity": self.source_identity,
            "authorization": {
                "status": self.authorization_status,
                "use": self.user_authorized_use,
                "redistribution": self.user_authorized_redistribution,
                "license_name": self.license_name,
                "acquisition_history": self.acquisition_history,
            },
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class FixtureDimensionsM:
    width_m: float
    length_m: float
    height_m: float

    def __post_init__(self) -> None:
        for name in ("width_m", "length_m", "height_m"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))

    def to_dict(self) -> dict[str, float]:
        return {
            "width_m": self.width_m,
            "length_m": self.length_m,
            "height_m": self.height_m,
        }


@dataclass(frozen=True, slots=True)
class IesTestMetadata:
    lm63_version: str
    product_id: str
    test_id: str
    tested_input_watts: float
    lamp_count: int
    lumens_per_lamp_field: float

    def __post_init__(self) -> None:
        for name in (
            "lm63_version",
            "product_id",
            "test_id",
        ):
            if not getattr(self, name):
                raise ConventionalProfileError(f"IES test {name} must be non-empty.")
        object.__setattr__(
            self,
            "tested_input_watts",
            _positive("tested_input_watts", self.tested_input_watts),
        )
        if (
            isinstance(self.lamp_count, bool)
            or not isinstance(self.lamp_count, int)
            or self.lamp_count <= 0
        ):
            raise ConventionalProfileError("IES lamp count must be positive.")
        if self.lumens_per_lamp_field != -1.0:
            raise ConventionalProfileError(
                "approved absolute-photometry IES must preserve lumens field -1.0."
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "lm63_version": self.lm63_version,
            "product_id": self.product_id,
            "test_id": self.test_id,
            "tested_input_watts": self.tested_input_watts,
            "lamp_count": self.lamp_count,
            "lumens_per_lamp_field": self.lumens_per_lamp_field,
            "role": "photometric_test_provenance_only",
            "sets_modeled_operating_point": False,
        }


@dataclass(frozen=True, slots=True)
class DeclaredModeledOperatingPoint:
    electrical_power_w_per_fixture: float
    par_ppe_umol_per_j: float
    par_ppf_umol_s_per_fixture: float
    basis: str

    def __post_init__(self) -> None:
        for name in (
            "electrical_power_w_per_fixture",
            "par_ppe_umol_per_j",
            "par_ppf_umol_s_per_fixture",
        ):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        if not self.basis:
            raise ConventionalProfileError("modeled operating-point basis is required.")
        calculated = self.electrical_power_w_per_fixture * self.par_ppe_umol_per_j
        if not math.isclose(
            calculated,
            self.par_ppf_umol_s_per_fixture,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ConventionalProfileError(
                "inconsistent Conventional calibration: electrical power × PAR PPE "
                "must equal fixture PAR PPF."
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "electrical_power_w_per_fixture": self.electrical_power_w_per_fixture,
            "par_ppe_umol_per_j": self.par_ppe_umol_per_j,
            "par_ppf_umol_s_per_fixture": self.par_ppf_umol_s_per_fixture,
            "basis": self.basis,
            "calibration_equation": (
                "electrical_power_w_per_fixture * par_ppe_umol_per_j = "
                "par_ppf_umol_s_per_fixture"
            ),
        }


@dataclass(frozen=True, slots=True)
class ConventionalComparisonProfile:
    profile_id: str
    source_id: str
    display_name: str
    scenario_claim_boundary: str
    ies_asset: ApprovedSourceAsset
    spd_asset: ApprovedSourceAsset
    ies_test: IesTestMetadata
    modeled_operating_point: DeclaredModeledOperatingPoint
    fixture_dimensions_m: FixtureDimensionsM
    angular_distribution_id: str
    spectral_distribution_id: str

    def __post_init__(self) -> None:
        for name in (
            "profile_id",
            "source_id",
            "display_name",
            "scenario_claim_boundary",
            "angular_distribution_id",
            "spectral_distribution_id",
        ):
            if not getattr(self, name):
                raise ConventionalProfileError(f"profile {name} must be non-empty.")
        if self.source_id == "proposed_led_smd_nominal_source_v1":
            raise ConventionalProfileError(
                "Conventional source identity must remain distinct from SMD."
            )
        if self.profile_id != CONVENTIONAL_COMPARISON_PROFILE_ID:
            raise ConventionalProfileError("Conventional comparison profile ID is invalid.")
        if self.source_id != CONVENTIONAL_SOURCE_ID:
            raise ConventionalProfileError("Conventional source ID is invalid.")
        if self.scenario_claim_boundary != SCENARIO_CLAIM_BOUNDARY:
            raise ConventionalProfileError("Conventional scenario claim boundary is invalid.")
        if self.display_name != "Conventional LED comparator":
            raise ConventionalProfileError("Conventional display name is invalid.")
        if (
            self.ies_asset.asset_id,
            self.ies_asset.resource_name,
            self.spd_asset.asset_id,
            self.spd_asset.resource_name,
        ) != (
            CONVENTIONAL_IES_ASSET_ID,
            CONVENTIONAL_IES_RESOURCE_NAME,
            CONVENTIONAL_SPD_ASSET_ID,
            CONVENTIONAL_SPD_RESOURCE_NAME,
        ):
            raise ConventionalProfileError("profile asset identities are not approved.")
        if (
            self.ies_asset.role,
            self.spd_asset.role,
        ) != (
            "normalized_angular_distribution_shape_and_fixture_dimensions",
            "approved_relative_modeled_spectral_power_shape",
        ):
            raise ConventionalProfileError("profile asset roles are not approved.")
        if self.ies_asset.sha256 != CONVENTIONAL_IES_SHA256:
            raise ConventionalProfileError("profile IES hash is not the approved asset.")
        if self.spd_asset.sha256 != CONVENTIONAL_SPD_SHA256:
            raise ConventionalProfileError("profile SPD hash is not the approved asset.")
        if self.angular_distribution_id != angular_distribution_id(self.ies_asset.sha256):
            raise ConventionalProfileError("profile angular distribution identity is stale.")
        if self.spectral_distribution_id != spectral_distribution_id(self.spd_asset.sha256):
            raise ConventionalProfileError("profile spectral distribution identity is stale.")
        expected_dimensions = (1.087, 1.190, 0.108)
        actual_dimensions = (
            self.fixture_dimensions_m.width_m,
            self.fixture_dimensions_m.length_m,
            self.fixture_dimensions_m.height_m,
        )
        if actual_dimensions != expected_dimensions:
            raise ConventionalProfileError(
                "approved profile fixture dimensions must preserve the IES metric header."
            )
        if not math.isclose(
            self.ies_test.tested_input_watts,
            663.20,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ConventionalProfileError("approved IES test watts must remain 663.20 W.")
        if (
            self.ies_test.product_id,
            self.ies_test.test_id,
        ) != (
            "CONVENTIONAL-LED-8-BAR",
            "GENERIC-CONVENTIONAL-LED-PHOTOMETRY",
        ):
            raise ConventionalProfileError("approved IES test identity is inconsistent.")
        modeled = self.modeled_operating_point
        if (
            modeled.electrical_power_w_per_fixture,
            modeled.par_ppe_umol_per_j,
            modeled.par_ppf_umol_s_per_fixture,
            modeled.basis,
        ) != (
            CONVENTIONAL_FIXTURE_POWER_W,
            CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J,
            CONVENTIONAL_FIXTURE_PPF_UMOL_S,
            DECLARED_OPERATING_POINT_BASIS,
        ):
            raise ConventionalProfileError(
                "approved rated operating point must remain 660 W / 2.6 umol/J / "
                "1716 umol/s and separate from IES test watts."
            )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 3,
            "profile_id": self.profile_id,
            "source_id": self.source_id,
            "display_name": self.display_name,
            "scenario_claim_boundary": self.scenario_claim_boundary,
            "assets": {
                "angular_and_footprint_ies": self.ies_asset.to_dict(),
                "relative_spectral_shape": self.spd_asset.to_dict(),
            },
            "ies_test_provenance": self.ies_test.to_dict(),
            "modeled_operating_point": self.modeled_operating_point.to_dict(),
            "fixture_dimensions_m": self.fixture_dimensions_m.to_dict(),
            "angular_distribution_id": self.angular_distribution_id,
            "spectral_distribution_id": self.spectral_distribution_id,
            "calibration_boundaries": {
                "absolute_PAR_anchor_applied_exactly_once": True,
                "ies_test_watts_define_modeled_watts": False,
                "ies_lumens_or_candela_define_modeled_PAR_PPF": False,
                "spd_amplitude_defines_modeled_PAR_PPF": False,
                "spd_defines_relative_photon_shape": True,
                "rated_fixture_authority": True,
            },
        }

    @property
    def profile_sha256(self) -> str:
        canonical = json.dumps(
            self.to_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


def build_conventional_comparison_profile(
    *,
    data_root: str | Path | None = None,
) -> ConventionalComparisonProfile:
    """Build the single approved fixed comparison profile from packaged assets."""

    ies = load_approved_lm63(data_root=data_root)
    spd = load_conventional_spd(data_root=data_root)
    test_metadata = _test_metadata(ies)
    return ConventionalComparisonProfile(
        profile_id=CONVENTIONAL_COMPARISON_PROFILE_ID,
        source_id=CONVENTIONAL_SOURCE_ID,
        display_name="Conventional LED comparator",
        scenario_claim_boundary=SCENARIO_CLAIM_BOUNDARY,
        ies_asset=ApprovedSourceAsset(
            asset_id=CONVENTIONAL_IES_ASSET_ID,
            resource_name=CONVENTIONAL_IES_RESOURCE_NAME,
            sha256=CONVENTIONAL_IES_SHA256,
            role="normalized_angular_distribution_shape_and_fixture_dimensions",
            source_identity="user-authorized generic Conventional LED LM-63 asset",
            authorization_status=USER_CONFIRMED_AUTHORIZATION,
            user_authorized_use=True,
            user_authorized_redistribution=True,
            license_name=None,
            acquisition_history=None,
            limitations=(
                "IES test input power is provenance only and does not set modeled power.",
                "Luminous magnitude does not set modeled PAR PPF.",
                "The asset does not establish individual bar geometry or source areas.",
            ),
        ),
        spd_asset=ApprovedSourceAsset(
            asset_id=CONVENTIONAL_SPD_ASSET_ID,
            resource_name=CONVENTIONAL_SPD_RESOURCE_NAME,
            sha256=CONVENTIONAL_SPD_SHA256,
            role="approved_relative_modeled_spectral_power_shape",
            source_identity="user-supplied Conventional LED relative SPD",
            authorization_status=USER_CONFIRMED_AUTHORIZATION,
            user_authorized_use=True,
            user_authorized_redistribution=True,
            license_name=None,
            acquisition_history=None,
            limitations=(
                "Digitized relative shape; missing tail is explicit zero, not extrapolated.",
                "SPD amplitude does not set PAR PPF, PPE, or electrical power.",
            ),
        ),
        ies_test=test_metadata,
        modeled_operating_point=DeclaredModeledOperatingPoint(
            electrical_power_w_per_fixture=CONVENTIONAL_FIXTURE_POWER_W,
            par_ppe_umol_per_j=CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J,
            par_ppf_umol_s_per_fixture=CONVENTIONAL_FIXTURE_PPF_UMOL_S,
            basis=DECLARED_OPERATING_POINT_BASIS,
        ),
        fixture_dimensions_m=FixtureDimensionsM(
            width_m=ies.fixture_width_m,
            length_m=ies.fixture_length_m,
            height_m=ies.fixture_height_m,
        ),
        angular_distribution_id=angular_distribution_id(CONVENTIONAL_IES_SHA256),
        spectral_distribution_id=spectral_distribution_id(spd.sha256),
    )


def format_conventional_profile_json(profile: ConventionalComparisonProfile) -> str:
    payload = profile.to_payload() | {"profile_sha256": profile.profile_sha256}
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _test_metadata(ies: Lm63Photometry) -> IesTestMetadata:
    return IesTestMetadata(
        lm63_version=ies.version,
        product_id=_required_keyword(ies, "LUMCAT"),
        test_id=_required_keyword(ies, "TEST"),
        tested_input_watts=ies.input_watts,
        lamp_count=ies.lamp_count,
        lumens_per_lamp_field=ies.lumens_per_lamp,
    )


def _required_keyword(ies: Lm63Photometry, name: str) -> str:
    value = ies.keyword(name)
    if value is None or not value.strip():
        raise ConventionalProfileError(
            f"approved IES is missing required [{name}] metadata."
        )
    return value.strip()
