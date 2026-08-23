"""Shared juvenile multi-plant four-band PAR plus optional far-red transport.

System-specific adapters stop at source and material text.  Scene expansion,
native execution, strict equal-grey decoding, and raw artifact publication are
shared so all lighting systems preserve the same receiver and failure model.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile
from typing import Callable, Mapping, Protocol

from fspm_optics.fixtures.conventional_led.band_source import (
    build_conventional_spectral_source_payload,
)
from fspm_optics.fixtures.conventional_led.radiance_source import (
    CONVENTIONAL_SHARED_ANGULAR_MODIFIER,
    CONVENTIONAL_SHARED_LIGHT_MODIFIER,
    RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W as CONVENTIONAL_179,
    format_conventional_apertures_rad,
)
from fspm_optics.fixtures.hps.band_source import (
    build_hps_spectral_source_payload,
)
from fspm_optics.fixtures.hps.radiance_source import (
    HPS_ANGULAR_MODIFIER_ID,
    HPS_LIGHT_MODIFIER_ID,
    RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W as HPS_179,
)
from fspm_optics.fixtures.occlusion import FixtureOcclusionPlan
from fspm_optics.fixtures.proposed_cob.source import (
    COB_SOURCE_MODE,
    NATIVE_SOURCE_MODE,
    native_proposed_spd_identity_sha256,
    resolve_proposed_source_authority,
)
from fspm_optics.fixtures.smd.band_writer import (
    SmdBandEmitterAssumptions,
    build_smd_band_radiance_document,
)
from fspm_optics.fixtures.smd.optical_stack import (
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
)
from fspm_optics.fixtures.smd.positions import SmdLayout
from fspm_optics.fixtures.smd.power_schedule import (
    ModulePowerSchedule,
    build_optimized_module_schedule,
    build_uniform_module_schedule,
)
from fspm_optics.geometry.active_domain import ActiveRoomDomain
from fspm_optics.geometry.room import (
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    production_room_model_payload,
    validate_production_room_radiance_text,
)
from fspm_optics.optics.conventional_rex import (
    build_conventional_rex_radiance_material_plan,
    build_conventional_rex_weighted_atr_payload,
)
from fspm_optics.optics.hps import (
    build_hps_rex_radiance_material_plan,
    build_hps_rex_weighted_atr_payload,
)
from fspm_optics.optics.rex_material_plan import (
    build_rex_radiance_trans_intervals,
    build_rex_radiance_trans_material_plan,
    render_radiance_trans_material,
)
from fspm_optics.optics.rex_weighting import (
    RexSourceWeightingInput,
    compute_rex_source_weighted_interval_set,
)
from fspm_optics.plants.multi_scene import (
    JuvenileScientificScene,
    build_juvenile_natural_fit_scene,
)
from fspm_optics.plants.natural_fit import plan_natural_fit_layout_from_feet
from fspm_optics.plants.radiance_scene_export import (
    DEFAULT_LEAF_MATERIAL_MODIFIER,
    JuvenileRadianceArtifactMetadata,
    materialize_juvenile_radiance_export,
    plan_juvenile_radiance_export,
)
from fspm_optics.radiance.commands import (
    CommandSpec,
    build_oconv_command,
    build_plant_receiver_rtrace_command,
)
from fspm_optics.radiance.options import radiance_options
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.spectral.bands import FIXED_TRANSPORT_BANDS
from fspm_optics.sources.smd.profile import build_nominal_smd_source_model
from fspm_optics.sources.smd.spectral_control import (
    build_controlled_conventional_led_proposed_source_model,
    proposed_spectral_basis_payload,
)
from fspm_optics.transport.basis.atomic import atomic_write_text
from fspm_optics.transport.conventional_scalar import (
    ConventionalScalarTransportResult,
)
from fspm_optics.transport.five_band import (
    build_five_band_source_plans,
    validate_five_band_material_mapping,
)
from fspm_optics.transport.hps_scalar import HpsScalarTransportResult
from fspm_optics.transport.scalar_ppfd import decode_grey_channel_ppfd

from .domain import (
    ANALYSIS_SCOPE_SCHEMA_ID,
    ANALYSIS_SCOPE_SCHEMA_VERSION,
    AnalysisScope,
    ProposedControlMode,
    ProposedSpectralBasis,
)
from .fspm_science import (
    FspmScientificPublication,
    aggregate_juvenile_surface_light,
)
from .source_state import PhysicalSourceState

JUVENILE_MULTISPECTRAL_SCHEMA_ID = (
    "fspm-optics.juvenile-multi-plant-five-band-receivers"
)
JUVENILE_MULTISPECTRAL_SCHEMA_VERSION = 4
RAW_VALUE_UNITS = "umol/m^2/s"
RAW_VALUE_COMPONENT_TYPE = "float64"
RAW_VALUE_BYTE_ORDER = "little-endian"
RAW_VALUE_RECORD_LAYOUT = "one band photon-flux-density value per global receiver"
BAND_ORDER = tuple(band.band_id for band in FIXED_TRANSPORT_BANDS)
PAR_BAND_ORDER = BAND_ORDER[:4]
CANONICAL_COUNTS_PER_PLANT = {
    "leaves": 12,
    "faces": 1920,
    "patches": 192,
    "receivers": 384,
}

EventSink = Callable[[str, str, Mapping[str, object] | None], None]


class JuvenileMultispectralError(RuntimeError):
    """Stage B failed a scientific, native, or artifact boundary."""


class CommandRunner(Protocol):
    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult: ...


@dataclass(frozen=True, slots=True)
class RawArtifactRecord:
    role: str
    path: str
    media_type: str
    byte_length: int
    sha256: str
    row_count: int | None = None

    def __post_init__(self) -> None:
        if (
            not self.role
            or not self.path
            or Path(self.path).is_absolute()
            or ".." in Path(self.path).parts
            or not self.media_type
            or isinstance(self.byte_length, bool)
            or not isinstance(self.byte_length, int)
            or self.byte_length < 0
            or not _valid_sha256(self.sha256)
            or (
                self.row_count is not None
                and (
                    isinstance(self.row_count, bool)
                    or not isinstance(self.row_count, int)
                    or self.row_count <= 0
                )
            )
        ):
            raise JuvenileMultispectralError("raw artifact record is invalid.")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "role": self.role,
            "path": self.path,
            "media_type": self.media_type,
            "byte_length": self.byte_length,
            "sha256": self.sha256,
        }
        if self.row_count is not None:
            payload["row_count"] = self.row_count
        return payload


@dataclass(frozen=True, slots=True)
class JuvenileBandInput:
    band_id: str
    source_text: str | Callable[[], str]
    material_text: str
    source_provenance: Mapping[str, object]
    material_provenance: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.band_id not in BAND_ORDER:
            raise JuvenileMultispectralError("band input identity is invalid.")
        if (
            (
                not isinstance(self.source_text, str)
                and not callable(self.source_text)
            )
            or (
                isinstance(self.source_text, str)
                and not self.source_text.strip()
            )
            or not self.material_text.strip()
        ):
            raise JuvenileMultispectralError("band source and material text are required.")
        if self.material_text.count(
            f"void trans {DEFAULT_LEAF_MATERIAL_MODIFIER}\n"
        ) != 1:
            raise JuvenileMultispectralError(
                "juvenile geometry material slot is not bound exactly once."
            )
        if not self.source_provenance or not self.material_provenance:
            raise JuvenileMultispectralError(
                "band source and material provenance are required."
            )

    def render_source_text(self) -> str:
        text = self.source_text() if callable(self.source_text) else self.source_text
        if not isinstance(text, str) or not text.strip():
            raise JuvenileMultispectralError(
                f"{self.band_id} source factory returned invalid text."
            )
        return text


@dataclass(frozen=True, slots=True)
class JuvenileSourceAdapter:
    system_id: str
    source_state_id: str
    room_text: str
    bands: tuple[JuvenileBandInput, ...]
    source_policy: Mapping[str, object]
    quality_profile: str
    threads: int
    oconv_bin: str
    rtrace_bin: str
    fixture_body_source_path: Path | None = None
    fixture_occlusion: Mapping[str, object] | None = None
    spectral_basis: Mapping[str, object] | None = None
    source_auxiliary_files: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        validate_production_room_radiance_text(self.room_text)
        if tuple(item.band_id for item in self.bands) != BAND_ORDER:
            raise JuvenileMultispectralError(
                "source adapter must contain the five canonical bands in order."
            )
        if (
            not self.system_id
            or not self.source_state_id.startswith("physical-source-state-v1-")
            or not self.room_text.strip()
            or not self.source_policy
            or not self.oconv_bin
            or not self.rtrace_bin
            or isinstance(self.threads, bool)
            or not isinstance(self.threads, int)
            or self.threads <= 0
        ):
            raise JuvenileMultispectralError("source adapter is incomplete.")
        if self.spectral_basis is not None and (
            not isinstance(self.spectral_basis, Mapping)
            or not isinstance(self.spectral_basis.get("id"), str)
            or not self.spectral_basis.get("id")
        ):
            raise JuvenileMultispectralError("spectral basis identity is incomplete.")
        if self.source_auxiliary_files is not None:
            for name, text in self.source_auxiliary_files.items():
                if (
                    not isinstance(name, str)
                    or not name
                    or Path(name).name != name
                    or not isinstance(text, str)
                    or not text
                ):
                    raise JuvenileMultispectralError(
                        "source auxiliary artifact is invalid."
                    )
        if self.system_id in {"proposed", "conventional", "hps"}:
            if (
                self.fixture_body_source_path is None
                or not self.fixture_body_source_path.is_file()
                or not self.fixture_occlusion
                or self.fixture_occlusion.get("system_id") != self.system_id
                or not _valid_sha256(
                    self.fixture_occlusion.get("identity_sha256")
                )
            ):
                raise JuvenileMultispectralError(
                    "Stage B fixture occlusion handoff is incomplete."
                )
        elif (
            self.fixture_body_source_path is not None
            or self.fixture_occlusion is not None
        ):
            raise JuvenileMultispectralError(
                "unsupported systems must remain outside fixture-body occlusion."
            )

    def planning_payload(
        self, executed_band_order: tuple[str, ...] = BAND_ORDER
    ) -> dict[str, object]:
        """Return target-reference-independent Stage B source planning."""

        if executed_band_order not in (PAR_BAND_ORDER, BAND_ORDER):
            raise JuvenileMultispectralError(
                "executed Stage B band order is not canonical."
            )
        selected = self.bands[: len(executed_band_order)]

        payload: dict[str, object] = {
            "system_id": self.system_id,
            "source_state_id": self.source_state_id,
            "source_policy": dict(self.source_policy),
            "quality_profile": self.quality_profile,
            "threads": self.threads,
            "bands": [
                {
                    "band_id": item.band_id,
                    "source_sha256": _sha256_text(item.render_source_text()),
                    "material_sha256": _sha256_text(item.material_text),
                    "source_provenance": dict(item.source_provenance),
                    "material_provenance": dict(item.material_provenance),
                }
                for item in selected
            ],
            "post_trace_scaling": False,
            "symmetry_reconstruction": False,
            "fixture_occlusion": (
                None
                if self.fixture_occlusion is None
                else dict(self.fixture_occlusion)
            ),
            "source_auxiliary_files": {
                name: _sha256_text(text)
                for name, text in sorted(
                    (self.source_auxiliary_files or {}).items()
                )
            },
        }
        if self.spectral_basis is not None:
            payload["spectral_basis"] = dict(self.spectral_basis)
        return payload


@dataclass(frozen=True, slots=True)
class JuvenileMultispectralPublication:
    metadata: Mapping[str, object]
    metadata_artifact: RawArtifactRecord
    public_artifacts: Mapping[str, str]
    scientific_aggregation: FspmScientificPublication
    juvenile_scene: JuvenileScientificScene

    def manifest_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "metadata_artifact": self.metadata_artifact.to_dict(),
            "public_artifacts": dict(self.public_artifacts),
            "source_state_id": self.metadata.get("source_state_id"),
            "receiver_count": self.metadata.get("receiver_count"),
            "band_order": self.metadata.get("band_order"),
            "include_far_red": self.metadata.get("include_far_red"),
            "far_red_executed": self.metadata.get("far_red_executed"),
            "transport_schema_id": self.metadata.get("schema_id"),
            "transport_schema_version": self.metadata.get("schema_version"),
            "room_model_identity_sha256": self.metadata.get(
                "room_model_identity_sha256"
            ),
            "room_text_sha256": self.metadata.get("room_text_sha256"),
        }
        if self.metadata.get("spectral_basis") is not None:
            payload["spectral_basis"] = self.metadata["spectral_basis"]
        return payload


def build_proposed_juvenile_source_adapter(
    *,
    layout: SmdLayout,
    full_output_schedule: ModulePowerSchedule,
    dimming_factor: float,
    room_text: str,
    source_state: PhysicalSourceState,
    quality_profile: str,
    threads: int,
    oconv_bin: str | Path,
    rtrace_bin: str | Path,
    spectral_basis: ProposedSpectralBasis = ProposedSpectralBasis.NATIVE_PROPOSED,
    control_mode: ProposedControlMode = ProposedControlMode.BASIS_MATRIX_OPTIMIZED,
    source_mode: str = NATIVE_SOURCE_MODE,
    fixture_occlusion: FixtureOcclusionPlan,
) -> JuvenileSourceAdapter:
    """Bind the exact Stage A Proposed schedule to five source bands."""

    source_operation = source_state.payload().get("source_operation")
    source_authority = resolve_proposed_source_authority(source_mode)
    source_record = (
        source_operation.get("proposed_source")
        if isinstance(source_operation, Mapping)
        else None
    )
    cob_record_matches = True
    if source_authority.source_mode == COB_SOURCE_MODE:
        law = source_authority.angular_law
        angular_record = (
            source_record.get("authenticated_angular_law")
            if isinstance(source_record, Mapping)
            else None
        )
        angular_input = (
            angular_record.get("input")
            if isinstance(angular_record, Mapping)
            else None
        )
        angular_normalization = (
            angular_record.get("normalization")
            if isinstance(angular_record, Mapping)
            else None
        )
        controlled_spd = (
            source_record.get("controlled_spd")
            if isinstance(source_record, Mapping)
            else None
        )
        characterization = (
            source_record.get("completed_aperture_characterization")
            if isinstance(source_record, Mapping)
            else None
        )
        cob_record_matches = (
            law is not None
            and isinstance(angular_input, Mapping)
            and angular_input.get("sha256") == law.ies_sha256
            and isinstance(angular_normalization, Mapping)
            and angular_normalization.get("identity_sha256")
            == law.normalized_identity_sha256
            and isinstance(controlled_spd, Mapping)
            and controlled_spd.get("identity_sha256")
            == native_proposed_spd_identity_sha256()
            and isinstance(characterization, Mapping)
            and characterization.get("characterization_identity_sha256")
            == source_authority.to_dict()["completed_aperture_characterization"][
                "characterization_identity_sha256"
            ]
        )
    if (
        not isinstance(source_operation, Mapping)
        or source_operation.get("proposed_layout_mode")
        != layout.proposed_layout_mode.value
        or source_operation.get("proposed_ring_mode", "full")
        != layout.proposed_ring_mode.value
        or source_operation.get(
            "module_pattern_id", "centered_square_full_v1"
        )
        != layout.module_pattern_id
        or source_operation.get("fixture_policy_id")
        != layout.fixture_policy_id
        or source_operation.get("mechanical_envelope")
        != {
            "id": layout.mechanical_envelope_id,
            "width_x_m": layout.module_footprint_x_m,
            "height_y_m": layout.module_footprint_y_m,
        }
        or source_operation.get("fixture_asset_set_id")
        != layout.fixture_asset_set_id
        or (
            source_record is not None
            and (
                not isinstance(source_record, Mapping)
                or source_record.get("source_mode")
                != source_authority.source_mode
            )
        )
        or (
            source_authority.source_mode == COB_SOURCE_MODE
            and (
                not isinstance(source_record, Mapping)
                or not cob_record_matches
            )
        )
    ):
        raise JuvenileMultispectralError(
            "Stage B Proposed layout composition does not match the "
            "authenticated Stage A source state."
        )
    factor = _positive_closed_unit("dimming_factor", dimming_factor)
    if control_mode is ProposedControlMode.UNIFORM_MODULE_DIMMING:
        if (
            len(set(full_output_schedule.watts_by_module)) != 1
            or full_output_schedule.module_count != len(layout.modules)
        ):
            raise JuvenileMultispectralError(
                "uniform Stage A handoff must contain one equal module wattage."
            )
        effective_schedule = build_uniform_module_schedule(
            layout,
            full_output_schedule.watts_by_module[0] * factor,
        )
    elif control_mode is ProposedControlMode.BASIS_MATRIX_OPTIMIZED:
        effective_schedule = build_optimized_module_schedule(
            layout,
            tuple(
                value * factor
                for value in full_output_schedule.watts_by_control_zone
            ),
        )
    else:  # pragma: no cover - enum validation guards callers
        raise JuvenileMultispectralError("unsupported Proposed control mode.")
    if spectral_basis is ProposedSpectralBasis.NATIVE_PROPOSED:
        source_model = build_nominal_smd_source_model()
    elif spectral_basis is ProposedSpectralBasis.CONVENTIONAL_LED_CONTROL:
        source_model = build_controlled_conventional_led_proposed_source_model()
    else:
        raise JuvenileMultispectralError(
            "unsupported Proposed spectral basis; native fallback is prohibited."
        )
    spectral_basis_identity = proposed_spectral_basis_payload(source_model)
    if source_authority.source_mode == COB_SOURCE_MODE:
        spectral_basis_identity["identity_sha256"] = (
            native_proposed_spd_identity_sha256()
        )
    source_plans = build_five_band_source_plans(
        source_model,
        layout,
        effective_schedule,
        source_authority=source_authority,
    )
    if spectral_basis is ProposedSpectralBasis.NATIVE_PROPOSED:
        materials = build_rex_radiance_trans_material_plan()
        ordered_materials = validate_five_band_material_mapping(materials)
    else:
        intervals = compute_rex_source_weighted_interval_set(
            RexSourceWeightingInput(
                source_model_id=source_model.source_model_id,
                normalization_policy=source_model.normalization_policy,
                resource_hashes=tuple(sorted(source_model.resource_hashes.items())),
                photon_distribution=source_model.photon_distribution,
            )
        )
        ordered_materials = build_rex_radiance_trans_intervals(
            intervals.bands,
            material_prefix="rex_leaf_trans",
        )
    bands: list[JuvenileBandInput] = []
    for source, material in zip(source_plans, ordered_materials, strict=True):
        def source_text_factory(
            *,
            band_id: str = source.band_id,
            photon_yield: float = source.internal_band_photon_yield_umol_per_j,
        ) -> str:
            return build_smd_band_radiance_document(
                layout,
                effective_schedule,
                band_id=band_id,
                internal_band_photon_yield_umol_per_j=photon_yield,
                assumptions=SmdBandEmitterAssumptions(
                    source_mode=source_authority.source_mode
                ),
            ).radiance_text

        bands.append(
            JuvenileBandInput(
                source.band_id,
                source_text_factory,
                render_radiance_trans_material(
                    DEFAULT_LEAF_MATERIAL_MODIFIER,
                    material.parameters,
                ),
                source.to_dict()
                | {
                    "stage_a_global_dimming_factor": factor,
                    "proposed_control_mode": control_mode.value,
                    "proposed_layout_mode": layout.proposed_layout_mode.value,
                    "proposed_ring_mode": layout.proposed_ring_mode.value,
                    "module_pattern_id": layout.module_pattern_id,
                    "fixture_policy_id": layout.fixture_policy_id,
                    "mechanical_envelope_id": layout.mechanical_envelope_id,
                    "fixture_asset_set_id": layout.fixture_asset_set_id,
                    "effective_schedule_total_w": effective_schedule.total_watts,
                    "spectral_basis": spectral_basis_identity,
                    "proposed_source": source_authority.to_dict(),
                    "completed_aperture_band_budget": {
                        "authority_boundary": "proposed_completed_fixture_aperture",
                        "is_par": source.band_id != "far_red",
                        "photon_fraction_relative_to_par": (
                            source.fraction_relative_to_par
                        ),
                        "par_ppe_contribution_umol_per_j": (
                            None
                            if source.band_id == "far_red"
                            else COMPLETED_APERTURE_PPE_UMOL_PER_J
                            * source.fraction_relative_to_par
                        ),
                        "far_red_outside_par_and_ppe": source.band_id == "far_red",
                    },
                },
                material.to_dict()
                | {
                    "juvenile_geometry_binding": (
                        "explicit_mature_rex_optics_proxy_on_frozen_juvenile_geometry"
                    )
                },
            )
        )
    return JuvenileSourceAdapter(
        system_id="proposed",
        source_state_id=source_state.source_state_id,
        room_text=room_text,
        bands=tuple(bands),
        source_policy={
            "policy_id": (
                (
                    "proposed_uniform_stage_a_schedule_five_band_handoff_v1"
                    if control_mode
                    is ProposedControlMode.UNIFORM_MODULE_DIMMING
                    else "proposed_stage_a_schedule_five_band_handoff_v1"
                )
                if spectral_basis is ProposedSpectralBasis.NATIVE_PROPOSED
                else (
                    "proposed_uniform_stage_a_schedule_conventional_led_spd_control_handoff_v1"
                    if control_mode
                    is ProposedControlMode.UNIFORM_MODULE_DIMMING
                    else "proposed_stage_a_schedule_conventional_led_spd_control_handoff_v1"
                )
            ),
            "proposed_control_mode": control_mode.value,
            "proposed_source": source_authority.to_dict(),
            "proposed_layout_mode": layout.proposed_layout_mode.value,
            "proposed_ring_mode": layout.proposed_ring_mode.value,
            "module_pattern_id": layout.module_pattern_id,
            "fixture_policy_id": layout.fixture_policy_id,
            "mechanical_envelope": {
                "id": layout.mechanical_envelope_id,
                "width_x_m": layout.module_footprint_x_m,
                "height_y_m": layout.module_footprint_y_m,
            },
            "fixture_asset_set_id": layout.fixture_asset_set_id,
            "basis_matrix_solver_enabled": (
                control_mode is ProposedControlMode.BASIS_MATRIX_OPTIMIZED
            ),
            "all_effective_module_watts_equal": (
                len(set(effective_schedule.watts_by_module)) == 1
            ),
            "target_control_recomputed": False,
            "lighting_target_mode": source_operation.get(
                "lighting_target_mode", "mean_target"
            ),
            "global_dimming_factor": factor,
            "full_output_schedule_total_w": full_output_schedule.total_watts,
            "effective_schedule_total_w": effective_schedule.total_watts,
            "maximum_declared_module_w": 100.0,
            "final_composite_trace": False,
            "spectral_basis": spectral_basis_identity,
        },
        quality_profile=quality_profile,
        threads=threads,
        oconv_bin=str(oconv_bin),
        rtrace_bin=str(rtrace_bin),
        fixture_body_source_path=fixture_occlusion.instance_source_path,
        fixture_occlusion=fixture_occlusion.scientific_payload(),
        spectral_basis=spectral_basis_identity,
        source_auxiliary_files=(
            {}
            if source_authority.angular_law is None
            else {
                source_authority.angular_law.to_dict()["radiance"][
                    "dat_filename"
                ]: source_authority.angular_law.dat_text
            }
        ),
    )


def build_conventional_juvenile_source_adapter(
    *,
    root: Path,
    result: ConventionalScalarTransportResult,
    source_state: PhysicalSourceState,
) -> JuvenileSourceAdapter:
    """Bind the final Stage A Conventional carrier state to five bands."""

    source_operation = source_state.payload().get("source_operation")
    if not isinstance(source_operation, Mapping):
        raise JuvenileMultispectralError(
            "Conventional Stage A source operation is missing."
        )
    raw_factor = source_operation.get("global_dimming_factor")
    if (
        isinstance(raw_factor, bool)
        or not isinstance(raw_factor, int | float)
        or not math.isfinite(float(raw_factor))
        or not 0.0 < float(raw_factor) <= 1.0
    ):
        raise JuvenileMultispectralError(
            "Conventional Stage A global output fraction is invalid."
        )
    factor = float(raw_factor)
    if result.plan.request.global_dimming_factor != 1.0:
        raise JuvenileMultispectralError(
            "Conventional Stage B requires the authenticated full-output "
            "Stage A transport authority."
        )
    source_payload = build_conventional_spectral_source_payload(result.plan.layout)
    optics = build_conventional_rex_weighted_atr_payload(source_payload)
    materials = build_conventional_rex_radiance_material_plan(optics)
    aperture_text = format_conventional_apertures_rad(result.plan.source)
    dat_path = result.plan.paths.converted_dat
    dat_reference = _run_relative(root, dat_path)
    dat_sha256 = _sha256_file(dat_path)
    aperture_area = result.plan.source.emitting_boundary_area_m2_per_fixture
    bands: list[JuvenileBandInput] = []
    for budget in source_payload.bands:
        effective_ppf = budget.per_fixture_ppf_umol_s * factor
        carrier = effective_ppf * CONVENTIONAL_179
        flatcorr = carrier / aperture_area
        angular_modifier = f"juvenile_conventional_{budget.band_id}_dist"
        light_modifier = f"juvenile_conventional_{budget.band_id}_light"
        def source_text_factory(
            *,
            interval_id: str = budget.band_id,
            band_flatcorr: float = flatcorr,
            band_angular_modifier: str = angular_modifier,
            band_light_modifier: str = light_modifier,
        ) -> str:
            return _directional_source_text(
                interval_id=interval_id,
                dat_reference=dat_reference,
                flatcorr=band_flatcorr,
                angular_modifier=band_angular_modifier,
                light_modifier=band_light_modifier,
                boundary_type="illum",
                aperture_text=aperture_text.replace(
                    CONVENTIONAL_SHARED_LIGHT_MODIFIER,
                    band_light_modifier,
                ).replace(
                    CONVENTIONAL_SHARED_ANGULAR_MODIFIER,
                    band_angular_modifier,
                ),
            )

        material = materials.material(budget.band_id)
        bands.append(
            JuvenileBandInput(
                budget.band_id,
                source_text_factory,
                render_radiance_trans_material(
                    DEFAULT_LEAF_MATERIAL_MODIFIER,
                    material.parameters,
                ),
                budget.to_payload()
                | {
                    "stage_a_global_dimming_factor": factor,
                    "effective_per_fixture_ppf_umol_s": effective_ppf,
                    "carrier_multiplier": carrier,
                    "flat_source_correction": flatcorr,
                    "angular_dat_sha256": dat_sha256,
                },
                material.to_dict()
                | {
                    "juvenile_geometry_binding": (
                        "explicit_mature_rex_optics_proxy_on_frozen_juvenile_geometry"
                    )
                },
            )
        )
    return JuvenileSourceAdapter(
        system_id="conventional",
        source_state_id=source_state.source_state_id,
        room_text=result.plan.room_text,
        bands=tuple(bands),
        source_policy={
            "policy_id": "conventional_stage_a_carrier_five_band_handoff_v1",
            "layout_policy": result.plan.layout.policy.name,
            "source_plan_id": result.plan.source.source_plan_id,
            "global_dimming_factor": factor,
            "lighting_target_mode": source_operation.get(
                "lighting_target_mode", "mean_target"
            ),
            "target_control_recomputed": False,
            "carrier_applied_before_trace": True,
            "stage_a_global_dimming_applied_after_trace": True,
            "post_trace_179_conversion": False,
            "angular_dat_sha256": dat_sha256,
        },
        quality_profile=result.plan.request.quality_profile,
        threads=result.plan.request.threads,
        oconv_bin=result.plan.oconv_command.argv[0],
        rtrace_bin=result.plan.rtrace_command.argv[0],
        fixture_body_source_path=(
            result.plan.fixture_occlusion.instance_source_path
        ),
        fixture_occlusion=(
            result.plan.fixture_occlusion.scientific_payload()
        ),
    )


def build_hps_juvenile_source_adapter(
    *,
    root: Path,
    result: HpsScalarTransportResult,
    source_state: PhysicalSourceState,
) -> JuvenileSourceAdapter:
    """Bind the sole full-output HPS Stage A state to five bands."""

    source_payload = build_hps_spectral_source_payload(result.plan.layout)
    optics = build_hps_rex_weighted_atr_payload(source_payload)
    materials = build_hps_rex_radiance_material_plan(optics)
    dat_path = result.plan.paths.converted_dat
    dat_reference = _run_relative(root, dat_path)
    dat_sha256 = _sha256_file(dat_path)
    aperture_area = (
        result.plan.layout.fixture_length_x_m
        * result.plan.layout.fixture_width_y_m
    )
    aperture_text = _hps_aperture_text(result)
    bands: list[JuvenileBandInput] = []
    for budget in source_payload.bands:
        carrier = budget.per_fixture_ppf_umol_s * HPS_179
        flatcorr = carrier / aperture_area
        def source_text_factory(
            *,
            interval_id: str = budget.band_id,
            band_flatcorr: float = flatcorr,
        ) -> str:
            return _directional_source_text(
                interval_id=interval_id,
                dat_reference=dat_reference,
                flatcorr=band_flatcorr,
                angular_modifier=HPS_ANGULAR_MODIFIER_ID,
                light_modifier=HPS_LIGHT_MODIFIER_ID,
                aperture_text=aperture_text,
            )

        material = materials.material(budget.band_id)
        bands.append(
            JuvenileBandInput(
                budget.band_id,
                source_text_factory,
                render_radiance_trans_material(
                    DEFAULT_LEAF_MATERIAL_MODIFIER,
                    material.parameters,
                ),
                budget.to_payload()
                | {
                    "operation": "fixed_full_output",
                    "carrier_multiplier": carrier,
                    "flat_source_correction": flatcorr,
                    "angular_dat_sha256": dat_sha256,
                },
                material.to_dict()
                | {
                    "juvenile_geometry_binding": (
                        "explicit_mature_rex_optics_proxy_on_frozen_juvenile_geometry"
                    )
                },
            )
        )
    return JuvenileSourceAdapter(
        system_id="hps",
        source_state_id=source_state.source_state_id,
        room_text=result.plan.room_text,
        bands=tuple(bands),
        source_policy={
            "policy_id": "hps_fixed_full_output_five_band_handoff_v2",
            "layout_policy": result.plan.layout.policy_id,
            "source_plan_id": result.plan.source.source_plan_id,
            "operation": "fixed_full_output",
            "dimming_supported": False,
            "target_control_recomputed": False,
            "post_trace_scaling": False,
            "ppe_matching": False,
            "peak_cap_control": False,
            "output_equalization": False,
            "source_side_ppf_times_179": True,
            "angular_dat_sha256": dat_sha256,
        },
        quality_profile=result.plan.request.quality_profile,
        threads=result.plan.request.threads,
        oconv_bin=result.plan.oconv_command.argv[0],
        rtrace_bin=result.plan.rtrace_command.argv[0],
        fixture_body_source_path=(
            result.plan.fixture_occlusion.instance_source_path
        ),
        fixture_occlusion=(
            result.plan.fixture_occlusion.scientific_payload()
        ),
    )


def execute_juvenile_multispectral_transport(
    *,
    root: Path,
    run_id: str,
    room_length_ft: float,
    room_width_ft: float,
    source_state: PhysicalSourceState,
    adapter: JuvenileSourceAdapter,
    emitted_par_ppf_umol_s: float,
    modeled_electrical_power_w: float,
    fspm_reference: Mapping[str, object],
    event_sink: EventSink,
    active_domain: ActiveRoomDomain | None = None,
    include_far_red: bool = False,
    runner: CommandRunner | None = None,
) -> JuvenileMultispectralPublication:
    """Execute Stage B sequentially and publish only requested raw bands."""

    if not isinstance(include_far_red, bool):
        raise JuvenileMultispectralError("include_far_red must be a Boolean.")
    emitted_ppf = _positive_finite(
        "authenticated Stage A emitted PAR PPF", emitted_par_ppf_umol_s
    )
    electrical_power = _positive_finite(
        "authenticated Stage A modeled electrical power",
        modeled_electrical_power_w,
    )
    executed_band_order = BAND_ORDER if include_far_red else PAR_BAND_ORDER
    executed_bands = adapter.bands[: len(executed_band_order)]

    if (
        source_state.source_state_id != adapter.source_state_id
        or source_state.payload().get("system_id") != adapter.system_id
    ):
        raise JuvenileMultispectralError(
            "Stage B adapter is not bound to the explicit immutable Stage A source state."
        )
    if adapter.system_id == "proposed":
        source_operation = source_state.payload().get("source_operation")
        if (
            not isinstance(source_operation, Mapping)
            or source_operation.get("proposed_layout_mode")
            != adapter.source_policy.get("proposed_layout_mode")
            or source_operation.get("proposed_ring_mode", "full")
            != adapter.source_policy.get("proposed_ring_mode", "full")
            or source_operation.get(
                "module_pattern_id", "centered_square_full_v1"
            )
            != adapter.source_policy.get(
                "module_pattern_id", "centered_square_full_v1"
            )
            or source_operation.get("fixture_policy_id")
            != adapter.source_policy.get("fixture_policy_id")
        ):
            raise JuvenileMultispectralError(
                "Stage B Proposed layout composition is not authenticated by "
                "the Stage A source state."
            )
    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise JuvenileMultispectralError("Stage B run root must be a directory.")
    stage_root = resolved_root / "fspm-transport"
    if stage_root.exists() and (
        not stage_root.is_dir() or any(stage_root.iterdir())
    ):
        raise JuvenileMultispectralError(
            "Stage B artifact root must be absent or empty."
        )
    stage_root.mkdir(parents=True, exist_ok=True)
    scene_root = stage_root / "scene"
    try:
        natural_fit = plan_natural_fit_layout_from_feet(
            room_length_ft,
            room_width_ft,
            active_domain=active_domain,
        )
        scene = build_juvenile_natural_fit_scene(natural_fit)
        if (
            scene.topology.leaf_count != CANONICAL_COUNTS_PER_PLANT["leaves"]
            or scene.topology.face_count != CANONICAL_COUNTS_PER_PLANT["faces"]
            or scene.topology.patch_count != CANONICAL_COUNTS_PER_PLANT["patches"]
            or scene.topology.receiver_count
            != CANONICAL_COUNTS_PER_PLANT["receivers"]
        ):
            raise JuvenileMultispectralError(
                "frozen juvenile canonical counts changed."
            )
        export = materialize_juvenile_radiance_export(
            plan_juvenile_radiance_export(scene),
            scene_root,
        )
        _validate_export_artifacts(export.artifacts, scene_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise JuvenileMultispectralError(
            f"juvenile scene export failed: {exc}"
        ) from exc

    atomic_write_text(stage_root / "room.rad", adapter.room_text)
    for name, text in sorted((adapter.source_auxiliary_files or {}).items()):
        atomic_write_text(resolved_root / name, text)
    native_runner = runner or LocalRunner()
    band_records: list[dict[str, object]] = []
    public_artifacts: dict[str, str] = {
        "compact_receiver_index": _run_relative(
            resolved_root, export.compact_receiver_index_path
        ),
        "plant_origins": _run_relative(
            resolved_root, export.plant_origins_path
        ),
        "scene_export_manifest": _run_relative(
            resolved_root, export.manifest_path
        ),
    }
    for name in sorted((adapter.source_auxiliary_files or {})):
        public_artifacts[f"source_auxiliary_{Path(name).stem}"] = name
    for order_index, band in enumerate(executed_bands):
        _emit(
            event_sink,
            "analysis.stage.fspm.band.started",
            f"Tracing juvenile receivers for the {band.band_id} band.",
            {
                "logical_stage": "multispectral_fspm",
                "band_id": band.band_id,
                "order_index": order_index,
            },
        )
        band_root = stage_root / "bands" / f"{order_index:02d}-{band.band_id}"
        band_root.mkdir(parents=True)
        source_path = band_root / "source.rad"
        material_path = band_root / "leaf-material.rad"
        octree_path = band_root / "scene.oct"
        raw_rgb_path = band_root / "receiver.rgb"
        values_path = band_root / "receiver-values.v1.f64le.bin"
        oconv_stderr = band_root / "oconv.stderr.log"
        rtrace_stderr = band_root / "rtrace.stderr.log"
        atomic_write_text(source_path, band.render_source_text())
        atomic_write_text(material_path, band.material_text)
        occlusion_identity = (
            None
            if adapter.fixture_occlusion is None
            else str(adapter.fixture_occlusion["identity_sha256"])
        )
        component_roles = ["room", "hps_emitters"]
        if adapter.system_id != "hps":
            component_roles[1] = "fixture_emitters"
        if occlusion_identity is not None:
            component_roles.append("fixture_bodies")
        component_roles.append("rex_plant")
        ordered_input_roles = [*component_roles[:-1], "leaf_material", "rex_plant"]
        scene_identity = "juvenile-multispectral-scene-v2-" + _hash_payload(
            {
                "system_id": adapter.system_id,
                "band_id": band.band_id,
                "ordered_component_roles": component_roles,
                "ordered_scene_input_roles": ordered_input_roles,
                "room_text_sha256": _sha256_file(stage_root / "room.rad"),
                "source_text_sha256": _sha256_file(source_path),
                "material_text_sha256": _sha256_file(material_path),
                "plant_geometry_sha256": _sha256_file(export.geometry_path),
                "fixture_occlusion_identity": occlusion_identity,
            }
        )
        octree_identity = "juvenile-multispectral-octree-v2-" + _hash_payload(
            {
                "scene_identity": scene_identity,
                "fixture_occlusion_identity": occlusion_identity,
            }
        )
        ambient_identity = "juvenile-multispectral-ambient-v2-" + _hash_payload(
            {
                "octree_identity": octree_identity,
                "quality_profile": adapter.quality_profile,
                "threads": adapter.threads,
            }
        )
        ambient_path = band_root / f"scene.{ambient_identity[-16:]}.amb"
        options = tuple(
            radiance_options(
                adapter.quality_profile,
                ambient_cache=(
                    None
                    if adapter.quality_profile == "direct"
                    else ambient_path
                ),
            )
        )
        scene_inputs = [
            stage_root / "room.rad",
            source_path,
        ]
        if adapter.fixture_body_source_path is not None:
            scene_inputs.append(adapter.fixture_body_source_path)
        scene_inputs.extend((material_path, export.geometry_path))
        oconv = build_oconv_command(
            tuple(scene_inputs),
            output_octree=octree_path,
            cwd=resolved_root,
            oconv_bin=adapter.oconv_bin,
            label=f"compile_juvenile_multispectral_{band.band_id}",
        )
        raw_rtrace = build_plant_receiver_rtrace_command(
            octree=octree_path,
            receiver_input=export.receiver_input_path,
            rgb_output=raw_rgb_path,
            options=options,
            nthreads=adapter.threads,
            cwd=resolved_root,
            rtrace_bin=adapter.rtrace_bin,
        )
        rtrace = CommandSpec(
            argv=raw_rtrace.argv,
            stdin_path=raw_rtrace.stdin_path,
            stdout_path=raw_rtrace.stdout_path,
            cwd=raw_rtrace.cwd,
            env=raw_rtrace.env,
            label=f"trace_juvenile_multispectral_{band.band_id}",
        )
        _run_required(native_runner, oconv, oconv_stderr)
        _require_nonempty(octree_path, f"{band.band_id} octree")
        _run_required(native_runner, rtrace, rtrace_stderr)
        _require_nonempty(raw_rgb_path, f"{band.band_id} raw receiver output")
        if adapter.quality_profile != "direct":
            _require_nonempty(ambient_path, f"{band.band_id} ambient cache")
        artifact = _decode_rgb_to_f64le(
            raw_rgb_path,
            values_path,
            expected_count=scene.counts.receiver_count,
            band_id=band.band_id,
            root=resolved_root,
        )
        public_artifacts[f"band_{band.band_id}_receiver_values"] = artifact.path
        band_records.append(
            {
                "order_index": order_index,
                "band_id": band.band_id,
                "definition": FIXED_TRANSPORT_BANDS[order_index].to_dict(),
                "is_par": band.band_id != "far_red",
                "units": RAW_VALUE_UNITS,
                "source": dict(band.source_provenance),
                "material": dict(band.material_provenance),
                "source_sha256": _sha256_file(source_path),
                "material_sha256": _sha256_file(material_path),
                "raw_rgb_sha256": _sha256_file(raw_rgb_path),
                "receiver_values": artifact.to_dict(),
                "scene": {
                    "component_roles": component_roles,
                    "ordered_scene_input_roles": ordered_input_roles,
                    "scene_identity": scene_identity,
                    "octree_identity": octree_identity,
                    "ambient_cache_identity": ambient_identity,
                    "fixture_occlusion_identity": occlusion_identity,
                },
                "commands": {
                    "oconv": _command_payload(oconv, resolved_root),
                    "rtrace": _command_payload(rtrace, resolved_root),
                },
                "post_trace_transforms": [],
            }
        )
        _emit(
            event_sink,
            "analysis.stage.fspm.band.completed",
            f"Validated raw juvenile receiver values for {band.band_id}.",
            {
                "logical_stage": "multispectral_fspm",
                "band_id": band.band_id,
                "receiver_count": scene.counts.receiver_count,
            },
        )

    compact_receiver_index_meta = _export_record(
        export.artifacts,
        role="compact_receiver_index",
        root=resolved_root,
        path=export.compact_receiver_index_path,
    )
    plant_origins_meta = _export_record(
        export.artifacts,
        role="plant_origins",
        root=resolved_root,
        path=export.plant_origins_path,
    )
    export_manifest_meta = RawArtifactRecord(
        role="scene_export_manifest",
        path=_run_relative(resolved_root, export.manifest_path),
        media_type="application/json",
        byte_length=export.manifest_artifact.byte_length,
        sha256=export.manifest_artifact.sha256,
    )
    metadata: dict[str, object] = {
        "schema_id": JUVENILE_MULTISPECTRAL_SCHEMA_ID,
        "schema_version": JUVENILE_MULTISPECTRAL_SCHEMA_VERSION,
        "run_id": run_id,
        "system_id": adapter.system_id,
        "analysis_scope": {
            "schema_id": ANALYSIS_SCOPE_SCHEMA_ID,
            "schema_version": ANALYSIS_SCOPE_SCHEMA_VERSION,
            "value": AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM.value,
        },
        "logical_stage": {
            "stage_id": "multispectral_fspm",
            "role": "FSPM",
            "plants_in_scientific_transport": True,
            "baseline_recomputed": False,
        },
        "source_state_id": adapter.source_state_id,
        "room_model": production_room_model_payload(),
        "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
        "room_text_sha256": hashlib.sha256(
            adapter.room_text.encode("utf-8")
        ).hexdigest(),
        "stage_a_operating_point_authority": {
            "emitted_par_ppf_umol_s": emitted_ppf,
            "modeled_electrical_power_w": electrical_power,
            **(
                {}
                if adapter.source_policy.get("lighting_target_mode") is None
                else {
                    "lighting_target_mode": adapter.source_policy[
                        "lighting_target_mode"
                    ]
                }
            ),
            **(
                {}
                if adapter.source_policy.get("proposed_control_mode") is None
                else {
                    "proposed_control_mode": adapter.source_policy[
                        "proposed_control_mode"
                    ],
                    "proposed_layout_mode": adapter.source_policy[
                        "proposed_layout_mode"
                    ],
                    "proposed_ring_mode": adapter.source_policy.get(
                        "proposed_ring_mode", "full"
                    ),
                    "module_pattern_id": adapter.source_policy.get(
                        "module_pattern_id", "centered_square_full_v1"
                    ),
                    "fixture_policy_id": adapter.source_policy[
                        "fixture_policy_id"
                    ],
                    "basis_matrix_solver_enabled": adapter.source_policy[
                        "basis_matrix_solver_enabled"
                    ],
                }
            ),
        },
        "source_planning": adapter.planning_payload(executed_band_order),
        **(
            {}
            if adapter.spectral_basis is None
            else {"spectral_basis": dict(adapter.spectral_basis)}
        ),
        "room": {
            "requested_length_ft": room_length_ft,
            "requested_width_ft": room_width_ft,
            "requested_length_m": natural_fit.requested_room_m.length_m,
            "requested_width_m": natural_fit.requested_room_m.width_m,
            "aligned_simulation_length_m": natural_fit.aligned_room_m.length_m,
            "aligned_simulation_width_m": natural_fit.aligned_room_m.width_m,
            "orientation": "aligned_simulation_long_axis_x_y_cross_z_up",
            "coordinate_frame": natural_fit.coordinate_frame.to_payload(),
            **(
                {}
                if active_domain is None
                else {"active_domain": active_domain.to_payload()}
            ),
        },
        "plant": {
            "profile_id": scene.profile_id,
            "sampling_profile_id": scene.sampling_profile_id,
            "layout_policy_id": scene.layout_policy_id,
            "layout_plan_hash": scene.layout_plan_hash,
            "scene_id": scene.scene_id,
            "scene_hash": scene.scene_hash,
            "plant_count": scene.counts.plant_count,
            "canonical_counts_per_plant": dict(CANONICAL_COUNTS_PER_PLANT),
            "global_counts": scene.counts.to_payload(),
            "ordering": "Y-major/X-minor plants; plant-major receivers; front then back",
            "geometry_included_in_scientific_transport": True,
            "clipped_scaled_clamped_or_capped": False,
        },
        "receiver_count": scene.counts.receiver_count,
        "compact_receiver_index": compact_receiver_index_meta.to_dict(),
        "plant_origins": plant_origins_meta.to_dict(),
        "scene_export_manifest": export_manifest_meta.to_dict(),
        "trace_row_mapping": "trace row i equals global receiver index i",
        "include_far_red": include_far_red,
        "band_order": list(executed_band_order),
        "executed_band_order": list(executed_band_order),
        "par_band_order": list(PAR_BAND_ORDER),
        "far_red_executed": include_far_red,
        "far_red_preserved_separately": True,
        "units": RAW_VALUE_UNITS,
        "component_type": RAW_VALUE_COMPONENT_TYPE,
        "byte_order": RAW_VALUE_BYTE_ORDER,
        "stride_bytes": 8,
        "record_layout": RAW_VALUE_RECORD_LAYOUT,
        "bands": band_records,
        "fspm_reference": dict(fspm_reference),
        "reference_resolution_executes_transport": False,
        "run_includes_juvenile_fspm_transport": True,
        "fspm_reference_controls_transport": False,
        "scientific_stage_boundary": {
            "raw_transport_authoritative": True,
            "surface_light_aggregation_published_separately": True,
            "surface_coloring_performed": False,
            "symmetry_reconstruction_applied": False,
        },
        "memory_complexity": {
            "scene": "Theta(canonical_topology + plant_count)",
            "source": "O(one band source document + fixture/module layout)",
            "export": "O(1) expanded-record working memory",
            "decode": "O(1) values plus one line; one band processed at a time",
            "resident_five_band_arrays": False,
            "expanded_receiver_identity_materialized": False,
        },
        "ordered_artifact_inventory": [
            compact_receiver_index_meta.to_dict(),
            plant_origins_meta.to_dict(),
            export_manifest_meta.to_dict(),
            *[record["receiver_values"] for record in band_records],
        ],
    }
    metadata_path = stage_root / "transport.v4.json"
    atomic_write_text(
        metadata_path,
        json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )
    metadata_artifact = RawArtifactRecord(
        role="multispectral_transport_metadata",
        path=_run_relative(resolved_root, metadata_path),
        media_type="application/json",
        byte_length=metadata_path.stat().st_size,
        sha256=_sha256_file(metadata_path),
    )
    public_artifacts = {
        "transport_metadata": metadata_artifact.path,
        **public_artifacts,
    }
    try:
        compact_receiver_index = json.loads(
            export.compact_receiver_index_path.read_text(encoding="utf-8")
        )
        if not isinstance(compact_receiver_index, dict):
            raise JuvenileMultispectralError(
                "compact receiver index must be a JSON object."
            )
        _emit(
            event_sink,
            "analysis.stage.fspm.aggregation.started",
            "Deriving authoritative patch, leaf, plant, and room surface-light metrics.",
            {
                "logical_stage": "multispectral_fspm_aggregation",
                "receiver_count": scene.counts.receiver_count,
            },
        )
        scientific_aggregation = aggregate_juvenile_surface_light(
            root=resolved_root,
            run_id=run_id,
            system_id=adapter.system_id,
            source_state_id=adapter.source_state_id,
            scene=scene,
            transport_metadata_path=metadata_artifact.path,
            transport_metadata_sha256=metadata_artifact.sha256,
            band_records=band_records,
            compact_receiver_index=compact_receiver_index,
            compact_receiver_index_sha256=(
                compact_receiver_index_meta.sha256
            ),
            emitted_par_ppf_umol_s=emitted_ppf,
            modeled_electrical_power_w=electrical_power,
            include_far_red=include_far_red,
            spectral_basis=adapter.spectral_basis,
        )
        _emit(
            event_sink,
            "analysis.stage.fspm.aggregation.completed",
            "Published conserved FSPM surface-light metrics from immutable raw bands.",
            {
                "logical_stage": "multispectral_fspm_aggregation",
                "patch_count": scene.counts.patch_count,
                "plant_count": scene.counts.plant_count,
            },
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise JuvenileMultispectralError(
            f"scientific surface-light aggregation failed: {exc}"
        ) from exc
    return JuvenileMultispectralPublication(
        metadata=metadata,
        metadata_artifact=metadata_artifact,
        public_artifacts=public_artifacts,
        scientific_aggregation=scientific_aggregation,
        juvenile_scene=scene,
    )


def _hps_aperture_text(result: HpsScalarTransportResult) -> str:
    lines: list[str] = []
    for aperture in result.plan.source.apertures:
        lines.extend(
            (
                f"{HPS_LIGHT_MODIFIER_ID} polygon {aperture.polygon_id}",
                "0",
                "0",
                "12",
                *(
                    " ".join(format(value, ".17g") for value in vertex)
                    for vertex in aperture.vertices_m
                ),
                "",
            )
        )
    return "\n".join(lines)


def _directional_source_text(
    *,
    interval_id: str,
    dat_reference: str,
    flatcorr: float,
    angular_modifier: str,
    light_modifier: str,
    aperture_text: str,
    boundary_type: str = "light",
) -> str:
    if Path(dat_reference).is_absolute() or ".." in Path(dat_reference).parts:
        raise JuvenileMultispectralError("angular DAT reference is unsafe.")
    if boundary_type not in {"light", "illum"}:
        raise JuvenileMultispectralError(
            "directional source boundary type is unsupported."
        )
    return (
        f"# isolated_interval={interval_id}\n"
        f"void brightdata {angular_modifier}\n"
        f"5 flatcorr {dat_reference} source.cal src_phi src_theta\n"
        "0\n"
        f"1 {flatcorr:.17g}\n\n"
        f"{angular_modifier} {boundary_type} {light_modifier}\n"
        "0\n"
        "0\n"
        "3 1 1 1\n\n"
        + aperture_text
    )


def _decode_rgb_to_f64le(
    raw_rgb_path: Path,
    output_path: Path,
    *,
    expected_count: int,
    band_id: str,
    root: Path,
) -> RawArtifactRecord:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
    )
    temporary = Path(temporary_name)
    row_count = 0
    digest = hashlib.sha256()
    try:
        with raw_rgb_path.open("r", encoding="utf-8", newline="") as source, os.fdopen(
            descriptor, "wb"
        ) as destination:
            for row_count, line in enumerate(source, start=1):
                if row_count > expected_count:
                    raise JuvenileMultispectralError(
                        f"{band_id} produced extra receiver rows."
                    )
                parts = line.split()
                if len(parts) != 3:
                    raise JuvenileMultispectralError(
                        f"{band_id} receiver row {row_count} must contain three channels."
                    )
                try:
                    red, green, blue = (float(value) for value in parts)
                    value = decode_grey_channel_ppfd(
                        red,
                        green,
                        blue,
                        row_number=row_count,
                    )
                except ValueError as exc:
                    raise JuvenileMultispectralError(
                        f"{band_id} receiver row {row_count} is invalid: {exc}"
                    ) from exc
                encoded = struct.pack("<d", value)
                destination.write(encoded)
                digest.update(encoded)
            destination.flush()
            os.fsync(destination.fileno())
        if row_count != expected_count:
            raise JuvenileMultispectralError(
                f"{band_id} receiver row count mismatch: expected {expected_count}, "
                f"got {row_count}."
            )
        os.replace(temporary, output_path)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
    return RawArtifactRecord(
        role=f"{band_id}_receiver_values",
        path=_run_relative(root, output_path),
        media_type="application/octet-stream",
        byte_length=expected_count * 8,
        sha256=digest.hexdigest(),
        row_count=expected_count,
    )


def _run_required(
    runner: CommandRunner,
    command: CommandSpec,
    stderr_path: Path,
) -> None:
    result = runner.run(command, stderr_path=stderr_path)
    if not result.success:
        detail = (result.stderr_text or "").strip() or (
            f"return code {result.returncode}"
        )
        raise JuvenileMultispectralError(
            f"{command.label or command.argv[0]} failed: {detail}"
        )


def _validate_export_artifacts(
    artifacts: tuple[JuvenileRadianceArtifactMetadata, ...],
    scene_root: Path,
) -> None:
    expected_roles = (
        "plant_geometry",
        "receiver_input",
        "plant_origins",
        "compact_receiver_index",
    )
    if tuple(item.role for item in artifacts) != expected_roles:
        raise JuvenileMultispectralError(
            "juvenile scene export inventory is missing or reordered."
        )
    for artifact in artifacts:
        path = scene_root / artifact.logical_name
        if (
            not path.is_file()
            or path.stat().st_size != artifact.byte_length
            or _sha256_file(path) != artifact.sha256
        ):
            raise JuvenileMultispectralError(
                f"juvenile scene export hash failed: {artifact.logical_name}"
            )


def _export_record(
    artifacts: tuple[JuvenileRadianceArtifactMetadata, ...],
    *,
    role: str,
    root: Path,
    path: Path,
    row_count: int | None = None,
) -> RawArtifactRecord:
    try:
        artifact = next(item for item in artifacts if item.role == role)
    except StopIteration as exc:
        raise JuvenileMultispectralError(
            f"juvenile export role is missing: {role}"
        ) from exc
    return RawArtifactRecord(
        role=role,
        path=_run_relative(root, path),
        media_type=artifact.media_type,
        byte_length=artifact.byte_length,
        sha256=artifact.sha256,
        row_count=row_count,
    )


def _command_payload(command: CommandSpec, root: Path) -> dict[str, object]:
    def relative(path: Path | None) -> str | None:
        if path is None:
            return None
        if path.resolve() == root.resolve():
            return "."
        return _run_relative(root, path)

    return {
        "executable_role": Path(command.argv[0]).name,
        "argv_after_executable": list(command.argv[1:]),
        "stdin": relative(command.stdin_path),
        "stdout": relative(command.stdout_path),
        "cwd": relative(command.cwd),
        "shell": False,
    }


def _require_nonempty(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise JuvenileMultispectralError(f"required {label} is missing or empty.")


def _run_relative(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise JuvenileMultispectralError(
            "multispectral artifact escaped the run root."
        ) from exc
    if not relative.parts or ".." in relative.parts:
        raise JuvenileMultispectralError("multispectral artifact path is unsafe.")
    return relative.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _positive_closed_unit(name: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or not 0.0 < float(value) <= 1.0
    ):
        raise JuvenileMultispectralError(
            f"{name} must be finite in the interval (0, 1]."
        )
    return float(value)


def _positive_finite(name: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise JuvenileMultispectralError(f"{name} must be finite and positive.")
    return float(value)


def _emit(
    sink: EventSink,
    event_type: str,
    message: str,
    data: Mapping[str, object] | None = None,
) -> None:
    sink(event_type, message, data)


__all__ = [
    "BAND_ORDER",
    "PAR_BAND_ORDER",
    "CANONICAL_COUNTS_PER_PLANT",
    "JUVENILE_MULTISPECTRAL_SCHEMA_ID",
    "JUVENILE_MULTISPECTRAL_SCHEMA_VERSION",
    "JuvenileBandInput",
    "JuvenileMultispectralError",
    "JuvenileMultispectralPublication",
    "JuvenileSourceAdapter",
    "RawArtifactRecord",
    "build_conventional_juvenile_source_adapter",
    "build_hps_juvenile_source_adapter",
    "build_proposed_juvenile_source_adapter",
    "execute_juvenile_multispectral_transport",
]
