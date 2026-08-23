"""Pure planning for Conventional scalar-PAR and isolated five-band Rex runs."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Final

from fspm_optics.fixtures.conventional_led.band_source import (
    CONVENTIONAL_BAND_ORDER,
    CONVENTIONAL_BAND_SOURCE_MODEL_ID,
    ConventionalSpectralSourcePayload,
    build_conventional_spectral_source_payload,
)
from fspm_optics.fixtures.conventional_led.layout import DEFAULT_MOUNT_HEIGHT_M
from fspm_optics.fixtures.conventional_led.radiance_source import (
    RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W,
)
from fspm_optics.geometry.room import (
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    production_room_model_payload,
)
from fspm_optics.optics.conventional_rex import (
    ConventionalRexRadianceMaterialPlan,
    ConventionalRexWeightedAtrPayload,
    build_conventional_rex_radiance_material_plan,
    build_conventional_rex_weighted_atr_payload,
)
from fspm_optics.optics.rex_material_plan import RexRadianceTransIntervalPlan
from fspm_optics.plants.generator import generate_rex_butterhead_plant
from fspm_optics.plants.radiance_export import export_plant_mesh_to_radiance
from fspm_optics.plants.rex import RexPlantConfig
from fspm_optics.receivers.samples import (
    build_two_sided_patch_receivers,
    receiver_sample_input_text,
)
from fspm_optics.transport.conventional_scalar import (
    DEFAULT_REFERENCE_PLANE_Z_M,
    ConventionalScalarTransportRequest,
    plan_conventional_scalar_transport,
)
from fspm_optics.transport.five_band import (
    EXPECTED_FAR_RED_RESULT_UNITS,
    EXPECTED_PAR_BAND_RESULT_UNITS,
    PHYSICAL_PATCH_AREA_POLICY,
    RECEIVER_HEMISPHERE_POLICY,
    IsolatedTransportEmitterGroup,
    SourceNeutralIsolatedTransportInput,
)

CONVENTIONAL_REX_RUN_ORDER: Final = ("scalar_par", *CONVENTIONAL_BAND_ORDER)
CONVENTIONAL_REX_TRANSPORT_CLAIM: Final = (
    "Planned Conventional-source Rex incident photon transport with scalar PAR "
    "and five isolated grayscale intervals."
)
CONVENTIONAL_REX_TRANSPORT_LIMITATIONS: Final[tuple[str, ...]] = (
    "Planning only; no Radiance command or scientific runtime artifact is produced.",
    "Scalar PAR is a separate cross-check and is never added to band results.",
    "Far-red is outside PAR and is never labeled PPFD.",
    "Room optical behavior is wavelength-neutral.",
    "Rex leaf optics are source-weighted diffuse symmetric interval averages.",
    "One-sided Conventional source transport excludes original upward IES leakage.",
)


@dataclass(frozen=True, slots=True)
class ConventionalRexRunPaths:
    directory: Path
    source_rad: Path
    plant_rad: Path
    octree: Path
    ambient_cache: Path
    raw_rgb: Path
    decoded_values: Path

    def to_payload(self) -> dict[str, str]:
        return {
            "directory": str(self.directory),
            "source_rad": str(self.source_rad),
            "plant_rad": str(self.plant_rad),
            "octree": str(self.octree),
            "ambient_cache": str(self.ambient_cache),
            "raw_rgb": str(self.raw_rgb),
            "decoded_values": str(self.decoded_values),
        }


@dataclass(frozen=True, slots=True)
class ConventionalRexSharedPaths:
    source_payload_json: Path
    optical_payload_json: Path
    material_plan_json: Path
    room_rad: Path
    plant_geometry_rad: Path
    receivers: Path
    shared_angular_dat: Path
    bundle_manifest: Path

    def to_payload(self) -> dict[str, str]:
        return {
            "source_payload_json": str(self.source_payload_json),
            "optical_payload_json": str(self.optical_payload_json),
            "material_plan_json": str(self.material_plan_json),
            "room_rad": str(self.room_rad),
            "plant_geometry_rad": str(self.plant_geometry_rad),
            "receivers": str(self.receivers),
            "shared_angular_dat": str(self.shared_angular_dat),
            "bundle_manifest": str(self.bundle_manifest),
        }


@dataclass(frozen=True, slots=True)
class ConventionalRexIsolatedRunPlan:
    order_index: int
    interval_id: str
    source_input: SourceNeutralIsolatedTransportInput
    material: RexRadianceTransIntervalPlan
    per_fixture_ppf_umol_s: float
    whole_layout_ppf_umol_s: float
    carrier_multiplier: float
    aperture_area_m2: float
    flat_source_correction: float
    source_definition_id: str
    scene_id: str
    octree_identity: str
    ambient_cache_identity: str
    expected_result_units: str
    paths: ConventionalRexRunPaths

    def __post_init__(self) -> None:
        if self.interval_id != self.source_input.interval_id:
            raise ValueError("Conventional Rex run/source interval mismatch.")
        if self.interval_id != self.material.interval_id:
            raise ValueError("Conventional Rex run/material interval mismatch.")
        if self.order_index != CONVENTIONAL_REX_RUN_ORDER.index(self.interval_id):
            raise ValueError("Conventional Rex execution order is invalid.")
        if not math.isclose(
            self.whole_layout_ppf_umol_s,
            self.source_input.total_photon_flux_umol_s,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("Conventional Rex source total PPF is inconsistent.")
        expected_carrier = (
            self.per_fixture_ppf_umol_s
            * RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W
        )
        if not math.isclose(
            self.carrier_multiplier,
            expected_carrier,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("Conventional Rex carrier multiplier is inconsistent.")
        if not math.isfinite(self.aperture_area_m2) or self.aperture_area_m2 <= 0.0:
            raise ValueError("Conventional Rex aperture area must be finite and positive.")
        expected_flat = expected_carrier / self.aperture_area_m2
        if not math.isclose(
            self.flat_source_correction,
            expected_flat,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("Conventional Rex flat-source correction is inconsistent.")
        if self.interval_id == "far_red":
            if self.expected_result_units != EXPECTED_FAR_RED_RESULT_UNITS:
                raise ValueError("far-red result units are invalid.")
        elif self.expected_result_units != EXPECTED_PAR_BAND_RESULT_UNITS:
            raise ValueError("PAR/scalar result units are invalid.")
        identities = (
            self.source_definition_id,
            self.scene_id,
            self.octree_identity,
            self.ambient_cache_identity,
        )
        if any(not value for value in identities):
            raise ValueError("Conventional Rex run identities must be non-empty.")

    def scientific_payload(self) -> dict[str, object]:
        return {
            "order_index": self.order_index,
            "interval_id": self.interval_id,
            "source_input": self.source_input.to_payload(),
            "source_input_id": self.source_input.source_input_id,
            "material_identifier": self.material.material_identifier,
            "material_atr": self.material.source_interval.coefficients.to_dict(),
            "per_fixture_ppf_umol_s": self.per_fixture_ppf_umol_s,
            "whole_layout_ppf_umol_s": self.whole_layout_ppf_umol_s,
            "carrier_multiplier": self.carrier_multiplier,
            "aperture_area_m2": self.aperture_area_m2,
            "flat_source_correction": self.flat_source_correction,
            "source_definition_id": self.source_definition_id,
            "scene_id": self.scene_id,
            "octree_identity": self.octree_identity,
            "ambient_cache_identity": self.ambient_cache_identity,
            "expected_result_units": self.expected_result_units,
            "rgb_band_packing": False,
            "post_trace_fraction_application": False,
        }

    def to_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {"future_paths": self.paths.to_payload()}


@dataclass(frozen=True, slots=True)
class SourceNeutralAbsorptionCompatibility:
    source_model_id: str
    source_payload_id: str
    optical_payload_id: str
    material_plan_id: str
    receiver_identity: str
    receiver_count: int
    band_order: tuple[str, ...]
    band_material_ids: tuple[tuple[str, str], ...]
    scalar_par_result_policy: str = "separate_cross_check_not_added_to_band_results"
    absorbed_execution_in_scope: bool = False

    def __post_init__(self) -> None:
        if self.band_order != CONVENTIONAL_BAND_ORDER:
            raise ValueError("absorption compatibility band order is invalid.")
        if self.receiver_count != 1024:
            raise ValueError("absorption compatibility requires 1024 receivers.")
        if tuple(name for name, _ in self.band_material_ids) != self.band_order:
            raise ValueError("absorption compatibility materials are out of order.")
        if self.absorbed_execution_in_scope is not False:
            raise ValueError("absorbed execution is deferred to Phase 23B Part 2.")

    def to_payload(self) -> dict[str, object]:
        return {
            "source_model_id": self.source_model_id,
            "source_payload_id": self.source_payload_id,
            "optical_payload_id": self.optical_payload_id,
            "material_plan_id": self.material_plan_id,
            "receiver_identity": self.receiver_identity,
            "receiver_count": self.receiver_count,
            "band_order": list(self.band_order),
            "band_material_ids": dict(self.band_material_ids),
            "scalar_par_result_policy": self.scalar_par_result_policy,
            "absorbed_execution_in_scope": self.absorbed_execution_in_scope,
        }


@dataclass(frozen=True, slots=True)
class ConventionalRexTransportBundlePlan:
    workspace: Path
    output_root: Path
    source_payload: ConventionalSpectralSourcePayload
    optical_payload: ConventionalRexWeightedAtrPayload
    material_plan: ConventionalRexRadianceMaterialPlan
    conventional_layout_id: str
    phase23a_source_plan_id: str
    fixture_occlusion_identity: str
    room_identity: str
    plant_identity: str
    plant_geometry_sha256: str
    plant_polygon_count: int
    receiver_identity: str
    receiver_text_sha256: str
    receiver_count: int
    ordered_receiver_ids: tuple[str, ...]
    shared_angular_dat_identity: str
    shared_paths: ConventionalRexSharedPaths
    runs: tuple[ConventionalRexIsolatedRunPlan, ...]
    absorption_compatibility: SourceNeutralAbsorptionCompatibility
    claim: str = CONVENTIONAL_REX_TRANSPORT_CLAIM
    limitations: tuple[str, ...] = CONVENTIONAL_REX_TRANSPORT_LIMITATIONS
    bundle_id: str = field(init=False)

    def __post_init__(self) -> None:
        if tuple(item.interval_id for item in self.runs) != CONVENTIONAL_REX_RUN_ORDER:
            raise ValueError("Conventional Rex bundle run order is invalid.")
        if tuple(item.order_index for item in self.runs) != tuple(range(6)):
            raise ValueError("Conventional Rex bundle order indices are invalid.")
        if self.plant_polygon_count != 5248 or self.receiver_count != 1024:
            raise ValueError("Conventional Rex geometry/receiver counts are invalid.")
        if len(self.ordered_receiver_ids) != self.receiver_count or len(
            set(self.ordered_receiver_ids)
        ) != self.receiver_count:
            raise ValueError("Conventional Rex receiver ordering is invalid.")
        if len({item.source_definition_id for item in self.runs}) != 6:
            raise ValueError("Conventional Rex source definitions must be distinct.")
        if len({item.scene_id for item in self.runs}) != 6:
            raise ValueError("Conventional Rex scenes must be distinct.")
        if len({item.octree_identity for item in self.runs}) != 6:
            raise ValueError("Conventional Rex octrees must be distinct.")
        if len({item.ambient_cache_identity for item in self.runs}) != 6:
            raise ValueError("Conventional Rex ambient caches must be distinct.")
        if any(
            item.source_input.shared_angular_data_identity
            != self.shared_angular_dat_identity
            for item in self.runs
        ):
            raise ValueError("Conventional Rex runs must share one angular DAT identity.")
        shared_path_values = tuple(
            Path(getattr(self.shared_paths, name))
            for name in self.shared_paths.__dataclass_fields__
        )
        run_path_values = tuple(
            Path(getattr(run.paths, name))
            for run in self.runs
            for name in run.paths.__dataclass_fields__
        )
        if any(
            path != self.output_root and self.output_root not in path.parents
            for path in (*shared_path_values, *run_path_values)
        ):
            raise ValueError("future Conventional Rex paths must remain in output_root.")
        object.__setattr__(
            self,
            "bundle_id",
            "conventional-rex-transport-bundle-v4-"
            + _hash_payload(self.scientific_payload()),
        )

    @property
    def scalar_par(self) -> ConventionalRexIsolatedRunPlan:
        return self.runs[0]

    @property
    def shared_angular_dat_path(self) -> Path:
        return self.shared_paths.shared_angular_dat

    @property
    def band_plans(self) -> tuple[ConventionalRexIsolatedRunPlan, ...]:
        return self.runs[1:]

    def scientific_payload(self) -> dict[str, object]:
        return {
            "schema_version": 4,
            "claim": self.claim,
            "source_payload_id": self.source_payload.source_payload_id,
            "optical_payload_id": self.optical_payload.optical_payload_id,
            "material_plan_id": self.material_plan.material_plan_id,
            "conventional_layout_id": self.conventional_layout_id,
            "phase23a_source_plan_id": self.phase23a_source_plan_id,
            "fixture_occlusion_identity": self.fixture_occlusion_identity,
            "room_identity": self.room_identity,
            "room_model": production_room_model_payload(),
            "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
            "plant_identity": self.plant_identity,
            "plant_geometry_sha256": self.plant_geometry_sha256,
            "plant_polygon_count": self.plant_polygon_count,
            "receiver_identity": self.receiver_identity,
            "receiver_text_sha256": self.receiver_text_sha256,
            "receiver_count": self.receiver_count,
            "ordered_receiver_ids": list(self.ordered_receiver_ids),
            "receiver_hemisphere_policy": RECEIVER_HEMISPHERE_POLICY,
            "physical_patch_area_policy": PHYSICAL_PATCH_AREA_POLICY,
            "shared_angular_dat_identity": self.shared_angular_dat_identity,
            "run_order": list(CONVENTIONAL_REX_RUN_ORDER),
            "runs": [item.scientific_payload() for item in self.runs],
            "absorption_compatibility": self.absorption_compatibility.to_payload(),
            "execution_performed": False,
            "scalar_plus_band_summation": False,
            "limitations": list(self.limitations),
        }

    def to_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {
            "bundle_id": self.bundle_id,
            "future_workspace": {
                "workspace": str(self.workspace),
                "output_root": str(self.output_root),
                "shared": self.shared_paths.to_payload(),
                "runs": [item.to_payload() for item in self.runs],
            },
        }


def plan_conventional_rex_transport_bundle(
    workspace: str | Path,
    *,
    room_length_ft: float = 10.0,
    room_width_ft: float = 10.0,
    reference_plane_z_m: float = DEFAULT_REFERENCE_PLANE_Z_M,
    mount_height_m: float = DEFAULT_MOUNT_HEIGHT_M,
    quality_profile: str = "standard",
    seed: int = 1,
    normal_offset_m: float = 5e-5,
    threads: int = 6,
) -> ConventionalRexTransportBundlePlan:
    """Plan six future local runs without writing files or executing commands."""

    root = Path(workspace).expanduser().resolve()
    output_root = root / "conventional_rex_transport"
    phase23a = plan_conventional_scalar_transport(
        ConventionalScalarTransportRequest.from_feet(
            workspace=root,
            room_length_ft=room_length_ft,
            room_width_ft=room_width_ft,
            reference_plane_z_m=reference_plane_z_m,
            mount_height_m=mount_height_m,
            quality_profile=quality_profile,
            threads=threads,
        )
    )
    source_payload = build_conventional_spectral_source_payload(phase23a.layout)
    optical_payload = build_conventional_rex_weighted_atr_payload(source_payload)
    material_plan = build_conventional_rex_radiance_material_plan(optical_payload)
    aperture_area_m2 = (
        phase23a.source.emitting_boundary_area_m2_per_fixture
    )

    plant = generate_rex_butterhead_plant(RexPlantConfig(seed=seed))
    receivers = build_two_sided_patch_receivers(
        plant,
        normal_offset_m=normal_offset_m,
    )
    if (
        len(plant.leaves) != 32
        or plant.face_count != 5248
        or plant.patch_count != 512
        or len(receivers) != 1024
        or plant.config.leaf_patch_grid != (4, 4)
    ):
        raise ValueError("default mature Rex geometry contract is invalid.")
    generic_geometry = export_plant_mesh_to_radiance(plant)
    receiver_text = receiver_sample_input_text(item.to_dict() for item in receivers)
    geometry_sha = _sha256_text(generic_geometry)
    receiver_sha = _sha256_text(receiver_text)
    plant_identity = "conventional-rex-plant-v1-" + _hash_payload(
        {
            "plant_id": plant.plant_id,
            "seed": plant.seed,
            "geometry_sha256": geometry_sha,
            "leaf_count": len(plant.leaves),
            "patch_count": plant.patch_count,
            "one_infinitely_thin_surface": True,
            "coincident_front_back_polygons": False,
        }
    )
    ordered_receiver_ids = tuple(item.receiver_id for item in receivers)
    receiver_identity = "conventional-rex-receivers-v1-" + _hash_payload(
        {
            "plant_identity": plant_identity,
            "receiver_text_sha256": receiver_sha,
            "ordered_receiver_ids": list(ordered_receiver_ids),
            "normal_offset_m": float(normal_offset_m),
        }
    )
    shared_dat_identity = "conventional-shared-angular-dat-v1-" + _hash_payload(
        {
            "derived_ies_sha256": phase23a.source.derived_ies.sha256,
            "shared_angular_source_identity": (
                phase23a.source.shared_angular_source.identity
            ),
        }
    )
    shared_paths = ConventionalRexSharedPaths(
        source_payload_json=output_root / "conventional_source_payload.json",
        optical_payload_json=output_root / "conventional_rex_atr.json",
        material_plan_json=output_root / "conventional_rex_materials.json",
        room_rad=output_root / "shared" / "room.rad",
        plant_geometry_rad=output_root / "shared" / "rex_geometry.rad",
        receivers=output_root / "receivers" / "rex_receiver_rays.pts",
        shared_angular_dat=(
            output_root / "source" / "conventional_led_unit_downward_flux.dat"
        ),
        bundle_manifest=output_root / "conventional_rex_transport_plan.json",
    )

    runs: list[ConventionalRexIsolatedRunPlan] = []
    for order_index, interval_id in enumerate(CONVENTIONAL_REX_RUN_ORDER):
        if interval_id == "scalar_par":
            per_fixture_ppf = source_payload.scalar_par_ppf_umol_s_per_fixture
            whole_layout_ppf = source_payload.fixture_count * per_fixture_ppf
            label, start_nm, end_nm = "Scalar PAR", 400, 699
        else:
            budget = source_payload.band(interval_id)
            per_fixture_ppf = budget.per_fixture_ppf_umol_s
            whole_layout_ppf = budget.whole_layout_ppf_umol_s
            label, start_nm, end_nm = budget.label, budget.start_nm, budget.end_nm
        source_input = _adapt_conventional_source_input(
            source_payload,
            interval_id=interval_id,
            label=label,
            start_nm=start_nm,
            end_nm=end_nm,
            per_fixture_ppf_umol_s=per_fixture_ppf,
            whole_layout_ppf_umol_s=whole_layout_ppf,
            shared_angular_dat_identity=shared_dat_identity,
        )
        carrier = (
            per_fixture_ppf * RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W
        )
        flatcorr = carrier / aperture_area_m2
        material = material_plan.material(interval_id)
        source_definition_id = "conventional-rex-source-v4-" + _hash_payload(
            {
                "source_input_id": source_input.source_input_id,
                "carrier_multiplier": carrier,
                "flat_source_correction": flatcorr,
            }
        )
        plant_text = export_plant_mesh_to_radiance(
            plant,
            material_name=material.material_identifier,
            material_definition=material.radiance_text,
        )
        scene_id = "conventional-rex-scene-v4-" + _hash_payload(
            {
                "room_identity": phase23a.room_identity,
                "source_definition_id": source_definition_id,
                "plant_identity": plant_identity,
                "plant_material_text_sha256": _sha256_text(plant_text),
                "receiver_identity": receiver_identity,
                "raw_ies2rad_box_included": False,
                "fixture_occlusion_identity": (
                    phase23a.fixture_occlusion.identity_sha256
                ),
            }
        )
        octree_identity = "conventional-rex-octree-v4-" + _hash_payload(
            {"scene_id": scene_id}
        )
        ambient_identity = "conventional-rex-ambient-v4-" + _hash_payload(
            {
                "octree_identity": octree_identity,
                "quality_identity": phase23a.quality_identity,
                "receiver_identity": receiver_identity,
                "threads": threads,
            }
        )
        directory = output_root / f"{order_index:02d}_{interval_id}"
        paths = ConventionalRexRunPaths(
            directory=directory,
            source_rad=directory / "source.rad",
            plant_rad=directory / "rex_plant.rad",
            octree=directory / "scene.oct",
            ambient_cache=directory / (
                "scene."
                f"{PRODUCTION_ROOM_MODEL_IDENTITY_SHA256[:16]}.amb"
            ),
            raw_rgb=directory / "receiver.rgb",
            decoded_values=directory / "receiver_pfd.npy",
        )
        runs.append(
            ConventionalRexIsolatedRunPlan(
                order_index=order_index,
                interval_id=interval_id,
                source_input=source_input,
                material=material,
                per_fixture_ppf_umol_s=per_fixture_ppf,
                whole_layout_ppf_umol_s=whole_layout_ppf,
                carrier_multiplier=carrier,
                aperture_area_m2=aperture_area_m2,
                flat_source_correction=flatcorr,
                source_definition_id=source_definition_id,
                scene_id=scene_id,
                octree_identity=octree_identity,
                ambient_cache_identity=ambient_identity,
                expected_result_units=(
                    EXPECTED_FAR_RED_RESULT_UNITS
                    if interval_id == "far_red"
                    else EXPECTED_PAR_BAND_RESULT_UNITS
                ),
                paths=paths,
            )
        )

    compatibility = SourceNeutralAbsorptionCompatibility(
        source_model_id=CONVENTIONAL_BAND_SOURCE_MODEL_ID,
        source_payload_id=source_payload.source_payload_id,
        optical_payload_id=optical_payload.optical_payload_id,
        material_plan_id=material_plan.material_plan_id,
        receiver_identity=receiver_identity,
        receiver_count=len(receivers),
        band_order=CONVENTIONAL_BAND_ORDER,
        band_material_ids=tuple(
            (band_id, material_plan.material(band_id).material_identifier)
            for band_id in CONVENTIONAL_BAND_ORDER
        ),
    )
    return ConventionalRexTransportBundlePlan(
        workspace=root,
        output_root=output_root,
        source_payload=source_payload,
        optical_payload=optical_payload,
        material_plan=material_plan,
        conventional_layout_id=phase23a.layout.layout_id,
        phase23a_source_plan_id=phase23a.source.source_plan_id,
        fixture_occlusion_identity=(
            phase23a.fixture_occlusion.identity_sha256
        ),
        room_identity=phase23a.room_identity,
        plant_identity=plant_identity,
        plant_geometry_sha256=geometry_sha,
        plant_polygon_count=plant.face_count,
        receiver_identity=receiver_identity,
        receiver_text_sha256=receiver_sha,
        receiver_count=len(receivers),
        ordered_receiver_ids=ordered_receiver_ids,
        shared_angular_dat_identity=shared_dat_identity,
        shared_paths=shared_paths,
        runs=tuple(runs),
        absorption_compatibility=compatibility,
    )


def format_conventional_rex_transport_bundle_json(
    plan: ConventionalRexTransportBundlePlan,
) -> str:
    return json.dumps(plan.to_payload(), indent=2, sort_keys=True) + "\n"


def _adapt_conventional_source_input(
    source: ConventionalSpectralSourcePayload,
    *,
    interval_id: str,
    label: str,
    start_nm: int,
    end_nm: int,
    per_fixture_ppf_umol_s: float,
    whole_layout_ppf_umol_s: float,
    shared_angular_dat_identity: str,
) -> SourceNeutralIsolatedTransportInput:
    group = IsolatedTransportEmitterGroup(
        group_id="conventional_fixture_array",
        emitter_count=source.fixture_count,
        photon_flux_per_emitter_umol_s=per_fixture_ppf_umol_s,
        total_photon_flux_umol_s=whole_layout_ppf_umol_s,
    )
    return SourceNeutralIsolatedTransportInput(
        interval_id=interval_id,
        label=label,
        start_nm=start_nm,
        end_nm=end_nm,
        source_family="conventional_led",
        source_model_id=CONVENTIONAL_BAND_SOURCE_MODEL_ID,
        source_payload_id=source.source_payload_id,
        normalization_policy=source.normalization_policy,
        emitter_groups=(group,),
        total_photon_flux_umol_s=whole_layout_ppf_umol_s,
        angular_model_id="phase22c_downward_type_c_flat_aperture",
        shared_angular_data_identity=shared_angular_dat_identity,
    )


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
