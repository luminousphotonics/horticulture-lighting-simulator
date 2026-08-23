"""Side-effect-free authority for the fixed 24-case production sweep."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Final, Mapping

from fspm_optics import __version__
from fspm_optics.application.domain import (
    AnalysisScope,
    CONVENTIONAL_SYSTEM_ID,
    DEFAULT_FSPM_TOLERANCE_UMOL_M2_S,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    ProposedControlMode,
    ProposedSourceMode,
    ProposedSpectralBasis,
    RunRequest,
    parse_run_request,
)
from fspm_optics.application.publication import (
    NATIVE_BASELINE_PUBLICATION_SCHEMA_VERSION,
)
from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_COMPARISON_PROFILE_ID,
    CONVENTIONAL_IES_SHA256,
    CONVENTIONAL_SOURCE_ID,
    CONVENTIONAL_SPD_SHA256,
    PRACTICAL_LAYOUT_POLICY,
    ROLLING_BENCH_LAYOUT_POLICY,
)
from fspm_optics.fixtures.hps import (
    HPS_COMPARISON_PROFILE_ID,
    HPS_IES_SHA256,
    HPS_SOURCE_ID,
    HPS_SPD_SHA256,
)
from fspm_optics.fixtures.occlusion import (
    EMITTING_BOUNDARY_POLICY_ID,
    OCCLUSION_MODEL_ID,
    OCCLUSION_MODEL_VERSION,
    OCCLUSION_VERSION_ID,
    PROJECTED_AREA_POLICY_ID,
    TRANSFORM_POLICY_ID,
)
from fspm_optics.fixtures.proposed_cob.source import (
    native_proposed_spd_identity_sha256,
    resolve_proposed_source_authority,
)
from fspm_optics.geometry.active_domain import ACTIVE_DOMAIN_POLICY_ID
from fspm_optics.geometry.coordinate_frame import ROOM_FRAME_POLICY_ID
from fspm_optics.geometry.room import (
    DEFAULT_ROOM_HEIGHT_M,
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    production_room_model_payload,
)
from fspm_optics.geometry.sensor_grid import (
    BASELINE_REFERENCE_PLANE_Z_M,
    AdaptiveSensorGridPolicy,
)
from fspm_optics.layout.mode import ProposedLayoutMode
from fspm_optics.layout.ring import ProposedRingMode, proposed_module_pattern_id
from fspm_optics.plants.natural_fit import NaturalFitPolicy
from fspm_optics.plants.rex_juvenile import (
    REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID,
    REX_JUVENILE_PREHEADING_PROFILE_ID,
)
from fspm_optics.precomputed.compact_bundle import (
    COMPACT_BUNDLE_SCHEMA_ID,
    COMPACT_BUNDLE_SCHEMA_VERSION,
)
from fspm_optics.precomputed.contracts import (
    CANONICAL_DOMAIN_SCHEMA_ID,
    CANONICAL_DOMAIN_SCHEMA_VERSION,
    FIXED_CASE_BINDING_SCHEMA_ID,
    FIXED_CASE_BINDING_SCHEMA_VERSION,
    FIXED_OUTPUT_TARGET_SEMANTICS,
    FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S,
    MEAN_TARGET_SEMANTICS,
    PHYSICAL_ROOM_PAIRS_FT,
    canonical_room_domain,
)
from fspm_optics.radiance.options import radiance_options
from fspm_optics.viewer.artifacts import (
    PPFD_HEATMAP_VIEWER_RESOURCE_VERSION,
    SCENE_SCHEMA_VERSION,
    VIEWER_RESOURCE_VERSION,
)
from fspm_optics.viewer.fixtures import ASSET_REGISTRY, CATALOG_SCHEMA_VERSION


FIXED_SWEEP_SCHEMA_ID: Final = "fspm-optics.fixed-precomputed-sweep-plan"
FIXED_SWEEP_SCHEMA_VERSION: Final = 1
FIXED_QUALITY: Final = "standard"
FIXED_ANALYSIS_SCOPE: Final = AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM
FIXED_INCLUDE_FAR_RED: Final = True
COMPACT_BUNDLE_SUFFIX: Final = ".fspm-compact"
HPS_PRECOMPUTED_STORAGE_DIRECTORY: Final = "hps"

def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def canonical_requested_order(
    requested_length_ft: int | float,
    requested_width_ft: int | float,
) -> tuple[float, float]:
    domain = canonical_room_domain(requested_length_ft, requested_width_ft)
    aligned = domain["canonical_aligned_room"]
    assert isinstance(aligned, Mapping)
    return float(aligned["length_x_ft"]), float(aligned["width_y_ft"])


def _fixture_assets(system_id: str) -> list[dict[str, object]]:
    return [
        {
            "asset_id": asset.asset_id,
            "fixture_type": asset.fixture_type,
            "byte_size": asset.byte_size,
            "sha256": asset.sha256,
            "resource_path": asset.resource_path,
        }
        for asset in ASSET_REGISTRY
        if asset.system_id == system_id
    ]


def _source_compatibility(system_id: str) -> dict[str, object]:
    if system_id == PROPOSED_SYSTEM_ID:
        return {
            "stage_a_source_authority": resolve_proposed_source_authority().to_dict(),
            "stage_a_native_spd_identity_sha256": (
                native_proposed_spd_identity_sha256()
            ),
            "stage_b_control_spd_sha256": CONVENTIONAL_SPD_SHA256,
            "stage_b_spectral_basis": (
                ProposedSpectralBasis.CONVENTIONAL_LED_CONTROL.value
            ),
        }
    if system_id == CONVENTIONAL_SYSTEM_ID:
        return {
            "profile_id": CONVENTIONAL_COMPARISON_PROFILE_ID,
            "source_id": CONVENTIONAL_SOURCE_ID,
            "ies_sha256": CONVENTIONAL_IES_SHA256,
            "spd_sha256": CONVENTIONAL_SPD_SHA256,
        }
    if system_id == HPS_SYSTEM_ID:
        return {
            "profile_id": HPS_COMPARISON_PROFILE_ID,
            "source_id": HPS_SOURCE_ID,
            "ies_sha256": HPS_IES_SHA256,
            "spd_sha256": HPS_SPD_SHA256,
        }
    raise ValueError(f"unsupported fixed-sweep system: {system_id}")


def _fixture_compatibility(
    request: RunRequest, *, layout_variant: str
) -> dict[str, object]:
    fixture: dict[str, object] = {
        "layout_variant": layout_variant,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "assets": _fixture_assets(request.system),
    }
    if request.system == PROPOSED_SYSTEM_ID:
        fixture["proposed_module_pattern_id"] = proposed_module_pattern_id(
            request.proposed_ring_mode
        )
    return fixture


def _compatibility_inputs(
    request: RunRequest,
    *,
    layout_variant: str,
) -> dict[str, object]:
    """Materialize immutable inputs that can invalidate a cached result."""

    return {
        "engine": {
            "package": "fspm-optics",
            "version": __version__,
            "native_publication_schema_version": (
                NATIVE_BASELINE_PUBLICATION_SCHEMA_VERSION
            ),
        },
        "room": {
            "model": production_room_model_payload(),
            "model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
            "height_m": DEFAULT_ROOM_HEIGHT_M,
            "coordinate_frame_policy_id": ROOM_FRAME_POLICY_ID,
            "active_domain_policy_id": ACTIVE_DOMAIN_POLICY_ID,
        },
        "receiver": {
            "reference_plane_z_m": BASELINE_REFERENCE_PLANE_Z_M,
            "adaptive_sensor_grid_policy": asdict(AdaptiveSensorGridPolicy()),
        },
        "plants": {
            "profile_id": REX_JUVENILE_PREHEADING_PROFILE_ID,
            "sampling_profile_id": (
                REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
            ),
            "natural_fit_policy": NaturalFitPolicy().to_payload(),
        },
        "source": _source_compatibility(request.system),
        "fixture": _fixture_compatibility(
            request, layout_variant=layout_variant
        ),
        "fixture_occlusion": {
            "model_id": OCCLUSION_MODEL_ID,
            "model_version": OCCLUSION_MODEL_VERSION,
            "version_id": OCCLUSION_VERSION_ID,
            "transform_policy_id": TRANSFORM_POLICY_ID,
            "projected_area_policy_id": PROJECTED_AREA_POLICY_ID,
            "emitting_boundary_policy_id": EMITTING_BOUNDARY_POLICY_ID,
        },
        "solver": {
            "radiance_quality": FIXED_QUALITY,
            "radiance_options": radiance_options(FIXED_QUALITY),
            "proposed_basis_matrix_enabled": (
                request.system == PROPOSED_SYSTEM_ID
                and getattr(request, "control_mode", None)
                is ProposedControlMode.BASIS_MATRIX_OPTIMIZED
            ),
        },
        "spectral": {
            "analysis_scope": FIXED_ANALYSIS_SCOPE.value,
            "include_far_red": FIXED_INCLUDE_FAR_RED,
        },
        "publication": {
            "compact_schema_id": COMPACT_BUNDLE_SCHEMA_ID,
            "compact_schema_version": COMPACT_BUNDLE_SCHEMA_VERSION,
            "scene_schema_version": SCENE_SCHEMA_VERSION,
            "viewer_resource_version": VIEWER_RESOURCE_VERSION,
            "ppfd_heatmap_viewer_resource_version": (
                PPFD_HEATMAP_VIEWER_RESOURCE_VERSION
            ),
        },
    }


@dataclass(frozen=True, slots=True)
class FixedSweepCase:
    ordinal: int
    case_id: str
    system_id: str
    layout_variant: str
    aisle_enabled: bool
    canonical_domain: Mapping[str, object]
    request: RunRequest
    resolved_configuration: Mapping[str, object]
    configuration_identity_sha256: str
    compatibility_inputs: Mapping[str, object]
    compatibility_inputs_sha256: str
    output_relative_path: str

    def binding(self, plan_identity_sha256: str) -> dict[str, object]:
        target_semantics = str(self.resolved_configuration["target_semantics"])
        return {
            "schema_id": FIXED_CASE_BINDING_SCHEMA_ID,
            "schema_version": FIXED_CASE_BINDING_SCHEMA_VERSION,
            "plan_identity_sha256": plan_identity_sha256,
            "case_id": self.case_id,
            "case_configuration_identity_sha256": (
                self.configuration_identity_sha256
            ),
            "target_ppfd_umol_m2_s": (
                FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S
            ),
            "target_semantics": target_semantics,
            "compatibility_inputs_sha256": self.compatibility_inputs_sha256,
            "canonical_domain": dict(self.canonical_domain),
        }

    def expected_run_configuration(
        self, plan_identity_sha256: str
    ) -> dict[str, object]:
        return {
            "system_id": self.system_id,
            "request": self.request.to_dict(),
            "canonical_domain": dict(self.canonical_domain),
            "fixed_plan": self.binding(plan_identity_sha256),
        }

    def expected_authenticated_identities(self) -> dict[str, object]:
        return {
            "fixed_plan_inputs": {
                "compatibility_inputs_sha256": self.compatibility_inputs_sha256,
                "compatibility_inputs": dict(self.compatibility_inputs),
            }
        }

    def bundle_validation_expectations(
        self, plan_identity_sha256: str
    ) -> dict[str, object]:
        """Return generation-time expectations derived from current authorities."""

        return {
            "expected_run_configuration": self.expected_run_configuration(
                plan_identity_sha256
            ),
            "expected_authenticated_identities": (
                self.expected_authenticated_identities()
            ),
        }

    def output_path(self, output_root: str | Path) -> Path:
        root = Path(output_root).expanduser().resolve()
        relative = self.storage_relative_path()
        return root.joinpath(*relative.parts)

    def storage_relative_path(self) -> PurePosixPath:
        """Resolve physical storage without changing authenticated plan data."""

        authenticated = PurePosixPath(self.output_relative_path)
        if self.system_id != HPS_SYSTEM_ID:
            return authenticated
        if not authenticated.parts or authenticated.parts[0] != HPS_SYSTEM_ID:
            raise ValueError("HPS authenticated output path has an invalid system root.")
        return PurePosixPath(
            HPS_PRECOMPUTED_STORAGE_DIRECTORY,
            *authenticated.parts[1:],
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "case_id": self.case_id,
            "system_id": self.system_id,
            "layout_variant": self.layout_variant,
            "aisle_enabled": self.aisle_enabled,
            "target_ppfd_umol_m2_s": (
                FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S
            ),
            "canonical_domain": dict(self.canonical_domain),
            "resolved_configuration": dict(self.resolved_configuration),
            "configuration_identity_sha256": (
                self.configuration_identity_sha256
            ),
            "compatibility_inputs": dict(self.compatibility_inputs),
            "compatibility_inputs_sha256": self.compatibility_inputs_sha256,
            "compact_schema": {
                "schema_id": COMPACT_BUNDLE_SCHEMA_ID,
                "schema_version": COMPACT_BUNDLE_SCHEMA_VERSION,
            },
            "output_relative_path": self.output_relative_path,
        }


@dataclass(frozen=True, slots=True)
class FixedSweepPlan:
    cases: tuple[FixedSweepCase, ...]
    plan_identity_sha256: str

    @property
    def case_count(self) -> int:
        return len(self.cases)

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_id": FIXED_SWEEP_SCHEMA_ID,
            "schema_version": FIXED_SWEEP_SCHEMA_VERSION,
            "plan_identity_sha256": self.plan_identity_sha256,
            "case_count": self.case_count,
            "target_ppfd_umol_m2_s": (
                FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S
            ),
            "execution_policy": {
                "default_concurrency": 1,
                "completion_authority": "fresh_compact_bundle_validation",
                "requested_orientation_creates_case": False,
            },
            "cases": [case.to_payload() for case in self.cases],
        }


def _request_for(
    system_id: str,
    *,
    long_ft: float,
    short_ft: float,
    aisle_enabled: bool,
    layout_variant: str,
) -> RunRequest:
    payload: dict[str, object] = {
        "system": system_id,
        "room_length_ft": long_ft,
        "room_width_ft": short_ft,
        "quality": FIXED_QUALITY,
        "analysis_scope": FIXED_ANALYSIS_SCOPE.value,
        "include_far_red": FIXED_INCLUDE_FAR_RED,
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": DEFAULT_FSPM_TOLERANCE_UMOL_M2_S,
        "aisle_mode": aisle_enabled,
        "mounting_height_in": (
            24.0 if system_id == HPS_SYSTEM_ID else 18.0
        ),
    }
    if system_id != HPS_SYSTEM_ID:
        payload["target_ppfd"] = FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S
    if system_id == PROPOSED_SYSTEM_ID:
        payload.update(
            {
                "spectral_basis": (
                    ProposedSpectralBasis.CONVENTIONAL_LED_CONTROL.value
                ),
                "proposed_control_mode": (
                    ProposedControlMode.UNIFORM_MODULE_DIMMING.value
                ),
                "proposed_ring_mode": ProposedRingMode.REDUCED_ONE_RING.value,
                "proposed_source_mode": ProposedSourceMode.NATIVE_SMD.value,
            }
        )
        return parse_run_request(
            payload,
            proposed_layout_mode=ProposedLayoutMode.STANDALONE_MODULES,
        )
    if system_id == CONVENTIONAL_SYSTEM_ID:
        payload["layout_mode"] = layout_variant
    return parse_run_request(payload)


def _case_groups() -> tuple[tuple[str, str], ...]:
    return (
        (PROPOSED_SYSTEM_ID, "proposed_reduced_one_ring"),
        (CONVENTIONAL_SYSTEM_ID, PRACTICAL_LAYOUT_POLICY),
        (CONVENTIONAL_SYSTEM_ID, ROLLING_BENCH_LAYOUT_POLICY),
        (HPS_SYSTEM_ID, "fixed_full_output"),
    )


def build_fixed_sweep_plan() -> FixedSweepPlan:
    """Build and exhaustively validate the sole production sweep plan."""

    cases: list[FixedSweepCase] = []
    target_slug = f"ppfd-{FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S:g}"
    for system_id, layout_variant in _case_groups():
        for physical_length_ft, physical_width_ft in PHYSICAL_ROOM_PAIRS_FT:
            long_ft = float(max(physical_length_ft, physical_width_ft))
            short_ft = float(min(physical_length_ft, physical_width_ft))
            domain = canonical_room_domain(long_ft, short_ft)
            for aisle_enabled in (False, True):
                request = _request_for(
                    system_id,
                    long_ft=long_ft,
                    short_ft=short_ft,
                    aisle_enabled=aisle_enabled,
                    layout_variant=layout_variant,
                )
                compatibility = _compatibility_inputs(
                    request, layout_variant=layout_variant
                )
                resolved = {
                    "target_ppfd_umol_m2_s": (
                        FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S
                    ),
                    "target_semantics": (
                        FIXED_OUTPUT_TARGET_SEMANTICS
                        if system_id == HPS_SYSTEM_ID
                        else MEAN_TARGET_SEMANTICS
                    ),
                    "request": request.to_dict(),
                    "canonical_domain": domain,
                    "room_model": {
                        "payload": production_room_model_payload(),
                        "identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
                    },
                    "compatibility_inputs": compatibility,
                }
                config_sha = _sha256_json(resolved)
                aisle_slug = "aisle-on" if aisle_enabled else "aisle-off"
                room_slug = f"{int(long_ft)}x{int(short_ft)}"
                system_slug = (
                    system_id
                    if system_id != CONVENTIONAL_SYSTEM_ID
                    else f"{system_id}-{layout_variant.replace('_', '-')}"
                )
                case_id = (
                    f"{system_slug}-{room_slug}-{target_slug}-{aisle_slug}-"
                    f"{config_sha[:16]}"
                )
                output_relative = (
                    f"{system_id}/{layout_variant}/{room_slug}/{target_slug}/"
                    f"{aisle_slug}{COMPACT_BUNDLE_SUFFIX}"
                )
                cases.append(
                    FixedSweepCase(
                        ordinal=len(cases) + 1,
                        case_id=case_id,
                        system_id=system_id,
                        layout_variant=layout_variant,
                        aisle_enabled=aisle_enabled,
                        canonical_domain=domain,
                        request=request,
                        resolved_configuration=resolved,
                        configuration_identity_sha256=config_sha,
                        compatibility_inputs=compatibility,
                        compatibility_inputs_sha256=_sha256_json(compatibility),
                        output_relative_path=output_relative,
                    )
                )

    _validate_case_matrix(tuple(cases))
    preimage = {
        "schema_id": FIXED_SWEEP_SCHEMA_ID,
        "schema_version": FIXED_SWEEP_SCHEMA_VERSION,
        "target_ppfd_umol_m2_s": FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S,
        "compact_schema": {
            "schema_id": COMPACT_BUNDLE_SCHEMA_ID,
            "schema_version": COMPACT_BUNDLE_SCHEMA_VERSION,
        },
        "cases": [case.to_payload() for case in cases],
    }
    return FixedSweepPlan(tuple(cases), _sha256_json(preimage))


def _validate_case_matrix(cases: tuple[FixedSweepCase, ...]) -> None:
    if len(cases) != 24:
        raise ValueError("fixed production plan must contain exactly 24 cases.")
    if [case.ordinal for case in cases] != list(range(1, 25)):
        raise ValueError("fixed production case ordering is not contiguous.")
    case_ids = [case.case_id for case in cases]
    outputs = [case.output_relative_path for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("fixed production plan contains duplicate case IDs.")
    if len(set(outputs)) != len(outputs):
        raise ValueError("fixed production plan contains duplicate output paths.")
    target_slug = f"ppfd-{FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S:g}"
    for case in cases:
        if case.request.quality != FIXED_QUALITY or FIXED_QUALITY != "standard":
            raise ValueError(
                "every fixed production request must explicitly use Standard."
            )
        solver = case.compatibility_inputs.get("solver")
        if not isinstance(solver, Mapping) or solver.get(
            "radiance_quality"
        ) != FIXED_QUALITY or solver.get("radiance_options") != radiance_options(
            FIXED_QUALITY
        ):
            raise ValueError(
                "every fixed production case must authenticate Standard solver options."
            )
        target = case.resolved_configuration.get("target_ppfd_umol_m2_s")
        if target != FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S:
            raise ValueError("every fixed production case must authenticate 250 PPFD.")
        if (
            target_slug not in case.case_id
            or target_slug not in case.output_relative_path
        ):
            raise ValueError("case IDs and paths must explicitly bind the 250 target.")
        requested_target = getattr(case.request, "target_ppfd_umol_m2_s", None)
        if case.system_id == HPS_SYSTEM_ID:
            if requested_target is not None:
                raise ValueError("HPS must remain targetless and fixed-output.")
        elif requested_target != FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S:
            raise ValueError("controlled lighting cases must request the 250 mean target.")
        if _contains_retired_target(case.to_payload()):
            raise ValueError("the retired 1000 PPFD target cannot enter the plan.")
    for output in outputs:
        path = PurePosixPath(output)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("fixed production output path is unsafe.")
    expected_groups = {
        (PROPOSED_SYSTEM_ID, "proposed_reduced_one_ring"): 6,
        (CONVENTIONAL_SYSTEM_ID, PRACTICAL_LAYOUT_POLICY): 6,
        (CONVENTIONAL_SYSTEM_ID, ROLLING_BENCH_LAYOUT_POLICY): 6,
        (HPS_SYSTEM_ID, "fixed_full_output"): 6,
    }
    observed_groups = {
        group: sum(
            case.system_id == group[0] and case.layout_variant == group[1]
            for case in cases
        )
        for group in expected_groups
    }
    if observed_groups != expected_groups:
        raise ValueError("fixed production system/layout matrix is incomplete.")
    observed_domains = {
        (
            float(
                case.canonical_domain["canonical_aligned_room"]["length_x_ft"]  # type: ignore[index]
            ),
            float(
                case.canonical_domain["canonical_aligned_room"]["width_y_ft"]  # type: ignore[index]
            ),
        )
        for case in cases
    }
    if observed_domains != {(10.0, 10.0), (30.0, 15.0), (50.0, 30.0)}:
        raise ValueError("fixed production room matrix is incomplete.")


def _contains_retired_target(value: object, *, key: str = "") -> bool:
    """Reject 1000 only where it is represented as a target authority.

    Opaque source text can legitimately contain the digit sequence (for example
    the room floor material contains ``0.1000``), so substring matching would
    reject unrelated authenticated physics inputs.
    """

    if isinstance(value, Mapping):
        return any(
            _contains_retired_target(item, key=str(item_key))
            for item_key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_retired_target(item, key=key) for item in value)
    return "target" in key.lower() and value == 1000


__all__ = [
    "CANONICAL_DOMAIN_SCHEMA_ID",
    "CANONICAL_DOMAIN_SCHEMA_VERSION",
    "COMPACT_BUNDLE_SUFFIX",
    "FIXED_ANALYSIS_SCOPE",
    "FIXED_CASE_BINDING_SCHEMA_ID",
    "FIXED_CASE_BINDING_SCHEMA_VERSION",
    "FIXED_INCLUDE_FAR_RED",
    "FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S",
    "FIXED_QUALITY",
    "FIXED_SWEEP_SCHEMA_ID",
    "FIXED_SWEEP_SCHEMA_VERSION",
    "HPS_PRECOMPUTED_STORAGE_DIRECTORY",
    "PHYSICAL_ROOM_PAIRS_FT",
    "FixedSweepCase",
    "FixedSweepPlan",
    "build_fixed_sweep_plan",
    "canonical_requested_order",
    "canonical_room_domain",
]
