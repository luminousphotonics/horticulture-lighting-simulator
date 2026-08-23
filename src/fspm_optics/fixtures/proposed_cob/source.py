"""Fail-closed Citizen angular input and controlled Proposed COB authority.

The Citizen file authenticates angular shape only. Its zero source dimensions
and zero input-watt field are retained as evidence that neither emitting area
nor efficacy may be inferred from the LM-63 record.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
from importlib import resources
import json
import math
from pathlib import Path
from typing import Final

NATIVE_SOURCE_MODE: Final = "native_smd"
COB_SOURCE_MODE: Final = "cob_source_shape_surrogate"
SUPPORTED_PROPOSED_SOURCE_MODES: Final = (NATIVE_SOURCE_MODE, COB_SOURCE_MODE)

COB_IES_RESOURCE_NAME: Final = (
    "Ref_CE-1138-202209_CLU04Q-1812E1-302M2X2.ies"
)
COB_IES_SHA256: Final = (
    "365c1615905475a9b728cdbfc058805504c26fdadfcc1c59741f5fbc456af0e2"
)
COB_IES_IDENTITY: Final = "Citizen CLU04Q-1812E1 3000 K / CE-1138-202209"
COB_NORMALIZATION_POLICY: Final = (
    "type_c_c0_c90_gamma0_gamma90_bilinear_quadrant_symmetry_unit_flux_v1"
)
COB_ANGULAR_MODEL_ID: Final = "citizen_clu04q_1812e1_3000k_authenticated_ies"
COB_ANGULAR_DAT_FILENAME: Final = "proposed_cob_authenticated_angular_law.dat"
COB_ANGULAR_MODIFIER_NAME: Final = "proposed_cob_authenticated_angular_law"
COB_LES_DIAMETER_M: Final = 0.022
COB_LES_RADIUS_M: Final = COB_LES_DIAMETER_M / 2.0
COB_LES_AREA_M2: Final = math.pi * COB_LES_RADIUS_M**2
COB_LES_SHAPE: Final = "centered_circular_les"
COB_LES_GEOMETRY_SOURCE: Final = (
    "explicit controlled geometry; not inferred from zero-dimension IES fields"
)
COB_EMITTER_Z_OFFSET_M: Final = 0.011
COMPLETED_APERTURE_PPE_UMOL_PER_J: Final = 2.6

# Frozen normalization values come from the deterministic forward far-field
# source-response closure for this exact source law and optical stack.
COB_COMPLETED_APERTURE_TRANSMISSION: Final = 0.8413553469218158
COB_COMPLETED_APERTURE_FWHM_DEG: Final = 113.68314253513745
COB_COMPLETED_APERTURE_MEASURED_PPF_UMOL_S: Final = 2.6
COB_CHARACTERIZATION_METHOD_ID: Final = (
    "deterministic_forward_rtrace_integrated_flux_v2"
)
COB_FORWARD_BARE_PPF_UMOL_S: Final = 1.0000391796183936
COB_NORMALIZED_ANGULAR_IDENTITY_SHA256: Final = (
    "bdff179f96f7ee079c2c554984cbd1574c25c4a3f9176e55744691722656333b"
)
COB_ANGULAR_DAT_SHA256: Final = (
    "38145def01b2e9801353d1b2d3a86c32885e64b686a4bfaaea3454c54c4c0b91"
)
COB_COMPLETED_APERTURE_PROFILE_SHA256: Final = (
    "9147605bfe464362ea9b2070e968c0a5163899ad1416fd687788a49fecd5e65b"
)
# Historical v1 backward-matrix identity retained only so the immutable audit
# and certified archive can still be authenticated.  Production metadata uses
# COB_FORWARD_CHARACTERIZATION_IDENTITY_SHA256 below.
COB_CHARACTERIZATION_IDENTITY_SHA256: Final = (
    "f68459ea40476114398a13ad4aaeb7f0a97dd755c5a3d28f4dd397a7881d903e"
)
COB_FORWARD_CHARACTERIZATION_IDENTITY_SHA256: Final = (
    "2b3bef2d7a1371a2941bcbdb3ee7d3bae7566e84e4123b7e9c203967ea859936"
)
COB_COMPLETED_PROFILE_ANGLES_DEG: Final = tuple(
    float(value) for value in range(0, 91, 5)
)
COB_COMPLETED_PROFILE_NORMALIZED_INTENSITY: Final = (
    1.0,
    0.9980078167487838,
    0.9901726086682431,
    0.9718391303341031,
    0.9458661548320807,
    0.9117542621288791,
    0.8696539426084615,
    0.8204939173074642,
    0.7621441791599289,
    0.6946296668436627,
    0.6184825917583002,
    0.5341814054325557,
    0.43768751033176406,
    0.31567822502065257,
    0.19718522753694614,
    0.07143498751860813,
    0.009878299763637728,
    0.0,
    0.0,
)

_RESOURCE_PARTS: Final = (
    "resources",
    "data",
    "proposed_cob",
    "citizen_clu04q_1812e1",
)


class CobSourceError(RuntimeError):
    """The controlled COB source cannot be authenticated or reconstructed."""


@dataclass(frozen=True, slots=True)
class CobAngularLaw:
    ies_sha256: str
    normalized_identity_sha256: str
    vertical_angles_deg: tuple[float, ...]
    horizontal_angles_deg: tuple[float, ...]
    normalized_intensity_by_horizontal_plane: tuple[tuple[float, ...], ...]
    unit_flux_integral: float
    dat_text: str
    dat_sha256: str
    bare_profile_max_relative_error_tolerance: float = 0.01

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": COB_ANGULAR_MODEL_ID,
            "input": {
                "identity": COB_IES_IDENTITY,
                "resource_name": COB_IES_RESOURCE_NAME,
                "sha256": self.ies_sha256,
                "declared_source_dimensions": [0.0, 0.0, 0.0],
                "declared_input_watts": 0.0,
                "use": "authenticated_angular_distribution_only",
            },
            "normalization": {
                "policy": COB_NORMALIZATION_POLICY,
                "identity_sha256": self.normalized_identity_sha256,
                "solid_angle_integral": self.unit_flux_integral,
                "performed_before_fixture_and_room_transport": True,
                "post_scale_traced_ppfd": False,
            },
            "radiance": {
                "modifier": COB_ANGULAR_MODIFIER_NAME,
                "dat_filename": COB_ANGULAR_DAT_FILENAME,
                "dat_sha256": self.dat_sha256,
                "cal_reference": "source.cal",
                "correction": "flatcorr",
                "coordinates": ["src_phi4", "src_theta"],
            },
            "bare_far_field_validation": {
                "reference": "normalized C0-C90, gamma0-gamma90 IES grid",
                "maximum_relative_error_tolerance": (
                    self.bare_profile_max_relative_error_tolerance
                ),
            },
        }


@dataclass(frozen=True, slots=True)
class ProposedSourceAuthority:
    source_mode: str
    classification: str
    emitter_shape: str
    emitter_area_m2: float
    emitter_z_offset_from_aperture_m: float
    completed_aperture_transmission: float
    completed_aperture_ppe_umol_per_j: float
    internal_source_ppe_umol_per_j: float
    angular_model_id: str
    angular_law: CobAngularLaw | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "source_mode": self.source_mode,
            "classification": self.classification,
            "scientific_claim": (
                "controlled emitter-geometry experiment; not a Citizen "
                "product-efficacy model or completed hybrid COB/red-ring product"
                if self.source_mode == COB_SOURCE_MODE
                else "native Proposed SMD emitter"
            ),
            "emitter_geometry": {
                "shape": self.emitter_shape,
                "area_m2": self.emitter_area_m2,
                "diameter_m": (
                    COB_LES_DIAMETER_M
                    if self.source_mode == COB_SOURCE_MODE
                    else None
                ),
                "centered_on_existing_module_axis": True,
                "z_offset_from_completed_aperture_m": (
                    self.emitter_z_offset_from_aperture_m
                ),
                "normal": [0.0, 0.0, -1.0],
                "geometry_source": (
                    COB_LES_GEOMETRY_SOURCE
                    if self.source_mode == COB_SOURCE_MODE
                    else "native Proposed optical-stack authority"
                ),
            },
            "angular_model_id": self.angular_model_id,
            "completed_aperture_characterization": {
                "transmission": self.completed_aperture_transmission,
                "ppe_umol_per_j": self.completed_aperture_ppe_umol_per_j,
                "internal_source_ppe_umol_per_j": self.internal_source_ppe_umol_per_j,
                "radiant_intensity_fwhm_deg": (
                    COB_COMPLETED_APERTURE_FWHM_DEG
                    if self.source_mode == COB_SOURCE_MODE
                    else None
                ),
                "integrated_ppf_umol_s_at_one_input_w": (
                    COB_COMPLETED_APERTURE_MEASURED_PPF_UMOL_S
                    if self.source_mode == COB_SOURCE_MODE
                    else None
                ),
                "angular_profile": (
                    {
                        "angle_deg": list(COB_COMPLETED_PROFILE_ANGLES_DEG),
                        "normalized_radiant_intensity": list(
                            COB_COMPLETED_PROFILE_NORMALIZED_INTENSITY
                        ),
                        "full_0p5_degree_profile_sha256": (
                            COB_COMPLETED_APERTURE_PROFILE_SHA256
                        ),
                        "published_sampling": "5 degree subset",
                    }
                    if self.source_mode == COB_SOURCE_MODE
                    else None
                ),
                "characterization_identity_sha256": (
                    COB_FORWARD_CHARACTERIZATION_IDENTITY_SHA256
                    if self.source_mode == COB_SOURCE_MODE
                    else None
                ),
                "normalization_boundary": (
                    "internal emitter radiance before optical-stack and room transport"
                ),
                "traced_ppfd_post_scale": False,
            },
        }
        if self.angular_law is not None:
            payload["authenticated_angular_law"] = self.angular_law.to_dict()
        return payload


def cob_resource_bytes(*, data_root: str | Path | None = None) -> bytes:
    """Read and authenticate the sole approved Citizen angular resource."""

    try:
        if data_root is None:
            node = resources.files("fspm_optics").joinpath(
                *_RESOURCE_PARTS, COB_IES_RESOURCE_NAME
            )
            raw = node.read_bytes()
        else:
            root = Path(data_root).expanduser()
            direct = root / COB_IES_RESOURCE_NAME
            nested = root.joinpath(*_RESOURCE_PARTS[-2:], COB_IES_RESOURCE_NAME)
            raw = (direct if direct.is_file() else nested).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise CobSourceError(
            f"required authenticated COB IES is missing: {COB_IES_RESOURCE_NAME}"
        ) from exc
    actual = hashlib.sha256(raw).hexdigest()
    if actual != COB_IES_SHA256:
        raise CobSourceError(
            f"COB IES hash mismatch: expected {COB_IES_SHA256}, got {actual}."
        )
    return raw


def load_cob_angular_law(
    *, data_root: str | Path | None = None
) -> CobAngularLaw:
    """Parse the authenticated zero-dimension IES into a normalized DAT law."""

    raw = cob_resource_bytes(data_root=data_root)
    try:
        text = raw.decode("latin-1")
        lines = text.splitlines()
        if not lines or lines[0].strip() != "IESNA:LM-63-2002":
            raise ValueError("expected exact LM-63-2002 declaration")
        tilt_index = next(
            index
            for index, line in enumerate(lines)
            if line.strip().upper() == "TILT=NONE"
        )
        numeric = tuple(
            _finite_float(token)
            for token in " ".join(lines[tilt_index + 1 :]).split()
        )
        if len(numeric) < 13:
            raise ValueError("numeric header is incomplete")
        lamp_count = _exact_int(numeric[0])
        vertical_count = _exact_int(numeric[3])
        horizontal_count = _exact_int(numeric[4])
        photometric_type = _exact_int(numeric[5])
        units_type = _exact_int(numeric[6])
        if (
            lamp_count != 1
            or vertical_count != 181
            or horizontal_count != 94
            or photometric_type != 1
            or units_type != 2
        ):
            raise ValueError("unexpected LM-63 structural declaration")
        if tuple(numeric[7:10]) != (0.0, 0.0, 0.0):
            raise ValueError("Citizen angular authority must retain zero dimensions")
        if tuple(numeric[10:12]) != (1.0, 1.0) or numeric[12] != 0.0:
            raise ValueError("unexpected factors or nonzero IES input watts")
        expected = 13 + vertical_count + horizontal_count + (
            vertical_count * horizontal_count
        )
        if len(numeric) != expected:
            raise ValueError(
                f"declared LM-63 structure requires {expected} values, "
                f"found {len(numeric)}"
            )
        cursor = 13
        vertical = tuple(numeric[cursor : cursor + vertical_count])
        cursor += vertical_count
        horizontal = tuple(numeric[cursor : cursor + horizontal_count])
        cursor += horizontal_count
        if vertical != tuple(float(value) for value in range(181)):
            raise ValueError("vertical grid must be exact gamma 0-180 in 1 degree")
        if horizontal != tuple(float(value) for value in range(94)):
            raise ValueError("horizontal grid must be exact C0-C93 in 1 degree")
        flat = numeric[cursor:]
        if any(value < 0.0 for value in flat):
            raise ValueError("candela values must be non-negative")
        planes = tuple(
            tuple(flat[index : index + vertical_count])
            for index in range(0, len(flat), vertical_count)
        )
    except (StopIteration, ValueError) as exc:
        raise CobSourceError(f"malformed authenticated COB IES: {exc}") from exc

    quadrant_vertical = vertical[:91]
    quadrant_horizontal = horizontal[:91]
    quadrant = tuple(tuple(plane[:91]) for plane in planes[:91])
    integral = _quadrant_symmetric_integral(
        quadrant_vertical, quadrant_horizontal, quadrant
    )
    if not math.isfinite(integral) or integral <= 0.0:
        raise CobSourceError("authenticated COB angular integral is not positive.")
    normalized = tuple(
        tuple(value / integral for value in plane) for plane in quadrant
    )
    closure = _quadrant_symmetric_integral(
        quadrant_vertical, quadrant_horizontal, normalized
    )
    if not math.isclose(closure, 1.0, rel_tol=0.0, abs_tol=2.0e-14):
        raise CobSourceError("normalized COB angular law does not close to unit flux.")
    identity_payload = {
        "input_sha256": COB_IES_SHA256,
        "policy": COB_NORMALIZATION_POLICY,
        "horizontal_angles_deg": quadrant_horizontal,
        "vertical_angles_deg": quadrant_vertical,
        "normalized_intensity": normalized,
    }
    normalized_identity = _hash_json(identity_payload)
    dat_text = _format_dat(quadrant_horizontal, quadrant_vertical, normalized)
    return CobAngularLaw(
        ies_sha256=COB_IES_SHA256,
        normalized_identity_sha256=normalized_identity,
        vertical_angles_deg=quadrant_vertical,
        horizontal_angles_deg=quadrant_horizontal,
        normalized_intensity_by_horizontal_plane=normalized,
        unit_flux_integral=closure,
        dat_text=dat_text,
        dat_sha256=hashlib.sha256(dat_text.encode("utf-8")).hexdigest(),
    )


def resolve_proposed_source_authority(
    source_mode: str = NATIVE_SOURCE_MODE,
    *,
    data_root: str | Path | None = None,
) -> ProposedSourceAuthority:
    """Resolve one explicit source authority; COB fails closed on its IES."""

    mode = str(source_mode)
    if mode == NATIVE_SOURCE_MODE:
        return ProposedSourceAuthority(
            source_mode=mode,
            classification="native_proposed_smd",
            emitter_shape="centered_square_emitting_window",
            emitter_area_m2=0.120**2,
            emitter_z_offset_from_aperture_m=COB_EMITTER_Z_OFFSET_M,
            completed_aperture_transmission=_native_transmission(),
            completed_aperture_ppe_umol_per_j=(
                COMPLETED_APERTURE_PPE_UMOL_PER_J
            ),
            internal_source_ppe_umol_per_j=(
                COMPLETED_APERTURE_PPE_UMOL_PER_J / _native_transmission()
            ),
            angular_model_id="native_lambertian",
        )
    if mode != COB_SOURCE_MODE:
        raise ValueError(
            f"unsupported Proposed source mode {mode!r}; expected one of "
            f"{SUPPORTED_PROPOSED_SOURCE_MODES!r}."
        )
    law = load_cob_angular_law(data_root=data_root)
    transmission = _positive(
        "COB_COMPLETED_APERTURE_TRANSMISSION",
        COB_COMPLETED_APERTURE_TRANSMISSION,
    )
    internal_ppe = COMPLETED_APERTURE_PPE_UMOL_PER_J / transmission
    return ProposedSourceAuthority(
        source_mode=mode,
        classification=COB_SOURCE_MODE,
        emitter_shape=COB_LES_SHAPE,
        emitter_area_m2=COB_LES_AREA_M2,
        emitter_z_offset_from_aperture_m=COB_EMITTER_Z_OFFSET_M,
        completed_aperture_transmission=transmission,
        completed_aperture_ppe_umol_per_j=COMPLETED_APERTURE_PPE_UMOL_PER_J,
        internal_source_ppe_umol_per_j=internal_ppe,
        angular_model_id=COB_ANGULAR_MODEL_ID,
        angular_law=law,
    )


@lru_cache(maxsize=1)
def native_proposed_spd_identity_sha256() -> str:
    """Hash the controlled native Proposed SPD/five-band authority."""

    from fspm_optics.sources.smd.profile import build_nominal_smd_source_model
    from fspm_optics.sources.smd.spectral_control import (
        proposed_spectral_basis_payload,
    )

    return _hash_json(
        proposed_spectral_basis_payload(build_nominal_smd_source_model())
    )


def cob_characterization_identity_payload() -> dict[str, object]:
    """Return the complete frozen forward-flux normalization identity."""

    return {
        "method_id": COB_CHARACTERIZATION_METHOD_ID,
        "source_response_schema_id": "fspm-optics.proposed-source-response",
        "source_response_schema_version": 1,
        "authenticated_ies_sha256": COB_IES_SHA256,
        "normalized_angular_identity_sha256": (
            COB_NORMALIZED_ANGULAR_IDENTITY_SHA256
        ),
        "angular_dat_sha256": COB_ANGULAR_DAT_SHA256,
        "bare_forward_ppf_umol_s": COB_FORWARD_BARE_PPF_UMOL_S,
        "completed_aperture_transmission": (
            COB_COMPLETED_APERTURE_TRANSMISSION
        ),
        "completed_aperture_ppe_umol_per_j": (
            COMPLETED_APERTURE_PPE_UMOL_PER_J
        ),
        "internal_source_ppe_umol_per_j": (
            COMPLETED_APERTURE_PPE_UMOL_PER_J
            / COB_COMPLETED_APERTURE_TRANSMISSION
        ),
        "theta_step_deg": 0.5,
        "phi_step_deg": 5.0,
        "distance_m": 20.0,
        "options": [
            "-ab",
            "0",
            "-u-",
            "-dj",
            "0",
            "-ds",
            "0",
            "-dt",
            "0",
            "-dc",
            "1",
        ],
    }


def _quadrant_symmetric_integral(
    vertical: tuple[float, ...],
    horizontal: tuple[float, ...],
    values: tuple[tuple[float, ...], ...],
) -> float:
    """Bilinear-grid trapezoid integral over four symmetric Type-C quadrants."""

    phi_integrals: list[float] = []
    for plane in values:
        theta_integral = 0.0
        for index in range(1, len(vertical)):
            t0 = math.radians(vertical[index - 1])
            t1 = math.radians(vertical[index])
            theta_integral += 0.5 * (
                plane[index - 1] * math.sin(t0)
                + plane[index] * math.sin(t1)
            ) * (t1 - t0)
        phi_integrals.append(theta_integral)
    quadrant = 0.0
    for index in range(1, len(horizontal)):
        p0 = math.radians(horizontal[index - 1])
        p1 = math.radians(horizontal[index])
        quadrant += 0.5 * (
            phi_integrals[index - 1] + phi_integrals[index]
        ) * (p1 - p0)
    return 4.0 * quadrant


def _format_dat(
    horizontal: tuple[float, ...],
    vertical: tuple[float, ...],
    values: tuple[tuple[float, ...], ...],
) -> str:
    if horizontal != tuple(float(value) for value in range(91)):
        raise CobSourceError("COB DAT formatter requires an exact C0-C90 grid.")
    if vertical != tuple(float(value) for value in range(91)):
        raise CobSourceError("COB DAT formatter requires an exact gamma0-gamma90 grid.")
    rows = ["2", "0 90 91", "0 90 91", ""]
    for plane in values:
        rows.extend(
            "\t".join(f"{value:.15g}" for value in plane[index : index + 4])
            for index in range(0, len(plane), 4)
        )
    return "\n".join(rows) + "\n"


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError("numeric data contains a non-finite value")
    return value


def _exact_int(value: float) -> int:
    if not value.is_integer():
        raise ValueError("declared count is not an integer")
    return int(value)


def _positive(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CobSourceError(f"{name} must be finite and positive.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise CobSourceError(f"{name} must be finite and positive.")
    return number


def _native_transmission() -> float:
    # Lazy import avoids a package-initialization cycle: smd.__init__ exposes
    # the writer, while the writer consumes this source authority.
    from fspm_optics.fixtures.smd.optical_stack import (
        ACCEPTED_FIXTURE_TRANSMISSION,
    )

    return ACCEPTED_FIXTURE_TRANSMISSION


def _hash_json(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
