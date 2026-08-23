"""Pure isolated scalar/five-band HPS Rex transport bundle planning."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Final

from fspm_optics.fixtures.hps.band_source import (
    HPS_BAND_ORDER,
    HPS_BAND_SOURCE_MODEL_ID,
    HpsSpectralSourcePayload,
    build_hps_spectral_source_payload,
)
from fspm_optics.fixtures.hps.layout import (
    HPS_DEFAULT_MOUNT_HEIGHT_M,
    HpsLayoutPlan,
    plan_hps_layout_from_feet,
)
from fspm_optics.fixtures.hps.radiance_source import (
    HPS_ANGULAR_MODIFIER_ID,
    HPS_LIGHT_MODIFIER_ID,
    RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W,
    HpsFixturePlacement,
    HpsRadianceSourcePlan,
    build_hps_radiance_source_plan,
)
from fspm_optics.fixtures.occlusion import (
    FixtureOcclusionPlan,
    plan_fixture_occlusion,
)
from fspm_optics.geometry.room import (
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    production_room_model_payload,
)
from fspm_optics.optics.hps import (
    HpsRexRadianceMaterialPlan,
    HpsRexWeightedAtrPayload,
    build_hps_rex_radiance_material_plan,
    build_hps_rex_weighted_atr_payload,
)
from fspm_optics.optics.rex_material_plan import RexRadianceTransIntervalPlan
from fspm_optics.plants.generator import generate_rex_butterhead_plant
from fspm_optics.plants.radiance_export import export_plant_mesh_to_radiance
from fspm_optics.plants.rex import RexPlantConfig
from fspm_optics.radiance.commands import (
    CommandSpec,
    build_plant_receiver_rtrace_command,
)
from fspm_optics.receivers.samples import (
    build_two_sided_patch_receivers,
    receiver_sample_input_text,
)
from fspm_optics.transport.five_band import (
    EXPECTED_FAR_RED_RESULT_UNITS,
    EXPECTED_PAR_BAND_RESULT_UNITS,
    PHYSICAL_PATCH_AREA_POLICY,
    RECEIVER_HEMISPHERE_POLICY,
    IsolatedTransportEmitterGroup,
    SourceNeutralIsolatedTransportInput,
)
from fspm_optics.transport.scenes import ScenePlan

HPS_REX_RUN_ORDER: Final = ("scalar_par", *HPS_BAND_ORDER)
HPS_ANGULAR_MODEL_ID: Final = "hps_downward_type_c_flat_aperture_v2"
HPS_TRANSPORT_CLAIM: Final = (
    "Planned HPS-source Rex incident photon transport with scalar PAR and "
    "five isolated equal-grey intervals."
)
HPS_TRANSPORT_LIMITATIONS: Final = (
    "Planning only; no Radiance command is executed and no runtime artifact is written.",
    "Scalar PAR is a separate cross-check and is not added to band results.",
    "Absolute interval photon flux is applied before trace.",
    "No post-trace 179 conversion, spectral scale, target match, dimming, or symmetrization.",
    "Room optical behavior is wavelength-neutral.",
)


@dataclass(frozen=True, slots=True)
class HpsTransportRunPaths:
    directory: Path
    source_rad: Path
    plant_rad: Path
    baseline_octree: Path
    fspm_octree: Path
    ambient_cache: Path
    receivers: Path
    raw_rgb: Path


@dataclass(frozen=True, slots=True)
class HpsSceneComposition:
    role: str
    component_roles: tuple[str, ...]
    scene: ScenePlan
    scene_id: str
    octree_identity: str
    oconv_command: CommandSpec

    def __post_init__(self) -> None:
        expected = (
            ("room", "hps_emitters", "fixture_bodies")
            if self.role == "baseline_ppfd"
            else ("room", "hps_emitters", "fixture_bodies", "rex_plant")
        )
        if self.role not in ("baseline_ppfd", "fspm_receiver"):
            raise ValueError("HPS scene role is invalid.")
        if self.component_roles != expected:
            raise ValueError("HPS baseline/FSPM scene separation is invalid.")
        if self.scene.role != self.role or len(self.scene.source_files) != len(expected):
            raise ValueError("HPS wrapped ScenePlan is inconsistent.")
        if not self.scene_id or not self.octree_identity:
            raise ValueError("HPS scene and octree identities are required.")


@dataclass(frozen=True, slots=True)
class HpsIsolatedRunPlan:
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
    source_text_sha256: str
    plant_material_text_sha256: str
    baseline_scene: HpsSceneComposition
    fspm_scene: HpsSceneComposition
    ambient_cache_identity: str
    rtrace_command: CommandSpec
    expected_result_units: str
    paths: HpsTransportRunPaths

    def __post_init__(self) -> None:
        if self.interval_id != self.source_input.interval_id or (
            self.interval_id != self.material.interval_id
        ):
            raise ValueError("HPS run/source/material interval mismatch.")
        if self.order_index != HPS_REX_RUN_ORDER.index(self.interval_id):
            raise ValueError("HPS isolated run order is invalid.")
        if not math.isclose(
            self.whole_layout_ppf_umol_s,
            self.source_input.total_photon_flux_umol_s,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("HPS isolated source total does not close.")
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
            raise ValueError("HPS run carrier multiplier is inconsistent.")
        if not math.isclose(
            self.flat_source_correction,
            expected_carrier / self.aperture_area_m2,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("HPS flat-source correction is inconsistent.")
        if self.interval_id == "far_red":
            if self.expected_result_units != EXPECTED_FAR_RED_RESULT_UNITS:
                raise ValueError("HPS far-red result units are invalid.")
        elif self.expected_result_units != EXPECTED_PAR_BAND_RESULT_UNITS:
            raise ValueError("HPS PAR interval result units are invalid.")
        if self.baseline_scene.scene_id == self.fspm_scene.scene_id:
            raise ValueError("HPS baseline and FSPM scenes must remain distinct.")

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
            "source_text_sha256": self.source_text_sha256,
            "plant_material_text_sha256": self.plant_material_text_sha256,
            "baseline_scene": _scene_payload(self.baseline_scene),
            "fspm_scene": _scene_payload(self.fspm_scene),
            "ambient_cache_identity": self.ambient_cache_identity,
            "expected_result_units": self.expected_result_units,
            "absolute_carrier_applied_before_trace": True,
            "post_trace_179_conversion": False,
            "post_trace_spectral_scaling": False,
            "target_matching": False,
            "dimming": False,
            "spatial_symmetrization": False,
        }


@dataclass(frozen=True, slots=True)
class HpsIsolatedTransportBundlePlan:
    workspace: Path
    output_root: Path
    layout: HpsLayoutPlan
    phase25a_source_plan: HpsRadianceSourcePlan
    fixture_occlusion: FixtureOcclusionPlan
    source_payload: HpsSpectralSourcePayload
    optical_payload: HpsRexWeightedAtrPayload
    material_plan: HpsRexRadianceMaterialPlan
    room_identity: str
    plant_identity: str
    plant_geometry_sha256: str
    plant_polygon_count: int
    leaf_count: int
    patch_count: int
    leaf_patch_grid: tuple[int, int]
    receiver_identity: str
    receiver_text_sha256: str
    receiver_count: int
    ordered_receiver_ids: tuple[str, ...]
    shared_angular_dat_identity: str
    runs: tuple[HpsIsolatedRunPlan, ...]
    claim: str = HPS_TRANSPORT_CLAIM
    limitations: tuple[str, ...] = HPS_TRANSPORT_LIMITATIONS
    bundle_id: str = field(init=False)

    def __post_init__(self) -> None:
        if tuple(item.interval_id for item in self.runs) != HPS_REX_RUN_ORDER:
            raise ValueError("HPS bundle run order is invalid.")
        if len(self.phase25a_source_plan.apertures) != len(self.layout.fixtures):
            raise ValueError("HPS source plan must contain one aperture per layout fixture.")
        if (self.leaf_count, self.plant_polygon_count, self.patch_count) != (
            32,
            5248,
            512,
        ) or self.leaf_patch_grid != (4, 4) or self.receiver_count != 1024:
            raise ValueError("HPS bundle changed the mature Rex geometry/receiver contract.")
        if len(set(self.ordered_receiver_ids)) != self.receiver_count:
            raise ValueError("HPS receiver identities must be unique and ordered.")
        for attribute in (
            "source_definition_id",
            "ambient_cache_identity",
        ):
            if len({getattr(item, attribute) for item in self.runs}) != 6:
                raise ValueError(f"HPS run {attribute} values must be isolated.")
        if len({item.material.material_identifier for item in self.runs}) != 6:
            raise ValueError("HPS run materials must be isolated.")
        if len({item.fspm_scene.scene_id for item in self.runs}) != 6:
            raise ValueError("HPS FSPM scenes must be isolated.")
        if len({item.baseline_scene.scene_id for item in self.runs}) != 6:
            raise ValueError("HPS baseline scenes must be isolated.")
        if len({item.fspm_scene.octree_identity for item in self.runs}) != 6:
            raise ValueError("HPS FSPM octrees must be isolated.")
        if len({item.baseline_scene.octree_identity for item in self.runs}) != 6:
            raise ValueError("HPS baseline octrees must be isolated.")
        if any(
            item.source_input.shared_angular_data_identity
            != self.shared_angular_dat_identity
            for item in self.runs
        ):
            raise ValueError("HPS runs must share exactly one angular DAT identity.")
        object.__setattr__(
            self,
            "bundle_id",
            "hps-isolated-transport-v2-" + _hash_payload(self.scientific_payload()),
        )

    @property
    def scalar_par(self) -> HpsIsolatedRunPlan:
        return self.runs[0]

    @property
    def band_runs(self) -> tuple[HpsIsolatedRunPlan, ...]:
        return self.runs[1:]

    def scientific_payload(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "claim": self.claim,
            "layout_id": self.layout.layout_id,
            "phase25a_source_plan_id": self.phase25a_source_plan.source_plan_id,
            "fixture_occlusion": self.fixture_occlusion.scientific_payload(),
            "source_payload_id": self.source_payload.source_payload_id,
            "optical_payload_id": self.optical_payload.optical_payload_id,
            "material_plan_id": self.material_plan.material_plan_id,
            "room_identity": self.room_identity,
            "room_model": production_room_model_payload(),
            "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
            "plant": {
                "identity": self.plant_identity,
                "geometry_sha256": self.plant_geometry_sha256,
                "leaf_count": self.leaf_count,
                "polygon_count": self.plant_polygon_count,
                "patch_count": self.patch_count,
                "leaf_patch_grid": list(self.leaf_patch_grid),
                "one_infinitely_thin_surface": True,
                "coincident_front_back_polygons": False,
            },
            "receivers": {
                "identity": self.receiver_identity,
                "text_sha256": self.receiver_text_sha256,
                "count": self.receiver_count,
                "ordered_ids": list(self.ordered_receiver_ids),
                "hemisphere_policy": RECEIVER_HEMISPHERE_POLICY,
                "physical_patch_area_policy": PHYSICAL_PATCH_AREA_POLICY,
            },
            "shared_angular_dat_identity": self.shared_angular_dat_identity,
            "run_order": list(HPS_REX_RUN_ORDER),
            "runs": [
                item.scientific_payload()
                | {
                    "commands": {
                        "baseline_oconv": _command_payload(
                            item.baseline_scene.oconv_command,
                            self.workspace,
                        ),
                        "fspm_oconv": _command_payload(
                            item.fspm_scene.oconv_command,
                            self.workspace,
                        ),
                        "fspm_rtrace": _command_payload(
                            item.rtrace_command,
                            self.workspace,
                        ),
                    }
                }
                for item in self.runs
            ],
            "execution_performed": False,
            "absorbed_metrics_in_scope": False,
            "scalar_plus_band_summation": False,
            "limitations": list(self.limitations),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.scientific_payload() | {"bundle_id": self.bundle_id},
            indent=2,
            sort_keys=True,
        ) + "\n"


def plan_hps_isolated_transport_bundle(
    workspace: str | Path,
    *,
    output_root_name: str = "hps_isolated_transport",
    layout: HpsLayoutPlan | None = None,
    phase25a_source_plan: HpsRadianceSourcePlan | None = None,
    room_length_ft: float = 10.0,
    room_width_ft: float = 10.0,
    reference_plane_z_m: float = 0.0,
    mount_height_m: float = HPS_DEFAULT_MOUNT_HEIGHT_M,
    seed: int = 1,
    normal_offset_m: float = 5e-5,
    threads: int = 6,
    oconv_bin: str | Path = "oconv",
) -> HpsIsolatedTransportBundlePlan:
    """Plan isolated HPS scalar/five-band scenes and commands without writes."""

    root = Path(workspace).expanduser().resolve()
    if (
        not output_root_name
        or Path(output_root_name).is_absolute()
        or len(Path(output_root_name).parts) != 1
        or output_root_name in (".", "..")
    ):
        raise ValueError("HPS output_root_name must be one safe directory name.")
    output_root = root / output_root_name
    selected_layout = layout or plan_hps_layout_from_feet(
        room_length_ft,
        room_width_ft,
        reference_plane_z_m=reference_plane_z_m,
        mount_height_m=mount_height_m,
    )
    fixture_occlusion = plan_fixture_occlusion(
        system_id="hps",
        layout_identity=selected_layout.to_payload(),
        output_directory=output_root / "shared" / "fixture_occlusion",
        oconv_bin=oconv_bin,
    )
    placements = tuple(
        HpsFixturePlacement(
            item.fixture_id,
            item.aligned_x_m,
            item.aligned_y_m,
            item.aperture_z_m,
        )
        for item in selected_layout.fixtures
    )
    source_plan = phase25a_source_plan or build_hps_radiance_source_plan(
        workspace=output_root / "shared_source",
        placements=placements,
    )
    if tuple(item.fixture_id for item in source_plan.apertures) != tuple(
        item.fixture_id for item in selected_layout.fixtures
    ):
        raise ValueError("Phase 25A source apertures do not match the HPS layout.")
    if source_plan.profile_id != selected_layout.profile_id:
        raise ValueError("Phase 25A source profile does not match the HPS layout.")
    for aperture, fixture in zip(
        source_plan.apertures,
        selected_layout.fixtures,
        strict=True,
    ):
        center = tuple(
            math.fsum(vertex[index] for vertex in aperture.vertices_m) / 4.0
            for index in range(3)
        )
        if not all(
            math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12)
            for actual, expected in zip(
                center,
                (fixture.aligned_x_m, fixture.aligned_y_m, fixture.aperture_z_m),
                strict=True,
            )
        ):
            raise ValueError("Phase 25A aperture centers do not match the HPS layout.")
    spectral = build_hps_spectral_source_payload(selected_layout)
    optics = build_hps_rex_weighted_atr_payload(spectral)
    materials = build_hps_rex_radiance_material_plan(optics)

    plant = generate_rex_butterhead_plant(RexPlantConfig(seed=seed))
    receivers = build_two_sided_patch_receivers(plant, normal_offset_m=normal_offset_m)
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
    room_identity = "hps-production-room-v2-" + _hash_payload(
        {
            "requested_room_m": {
                "length": selected_layout.room_axes.requested.length_m,
                "width": selected_layout.room_axes.requested.width_m,
            },
            "room_model": production_room_model_payload(),
            "room_model_identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
        }
    )
    plant_identity = "hps-rex-plant-v1-" + _hash_payload(
        {
            "plant_id": plant.plant_id,
            "seed": plant.seed,
            "geometry_sha256": geometry_sha,
            "leaf_count": len(plant.leaves),
            "patch_count": plant.patch_count,
        }
    )
    ordered_receiver_ids = tuple(item.receiver_id for item in receivers)
    receiver_identity = "hps-rex-receivers-v1-" + _hash_payload(
        {
            "plant_identity": plant_identity,
            "receiver_text_sha256": receiver_sha,
            "ordered_receiver_ids": list(ordered_receiver_ids),
            "normal_offset_m": float(normal_offset_m),
        }
    )
    shared_dat_identity = "hps-shared-angular-dat-v1-" + _hash_payload(
        {
            "derived_ies_sha256": source_plan.derived_ies.sha256,
            "angular_distribution_id": source_plan.angular_distribution_id,
            "angular_modifier_id": source_plan.angular_modifier_id,
        }
    )
    room_path = output_root / "shared" / "room.rad"
    receivers_path = output_root / "shared" / "rex_receivers.pts"
    aperture_area = (
        selected_layout.fixture_length_x_m * selected_layout.fixture_width_y_m
    )
    runs: list[HpsIsolatedRunPlan] = []
    for order_index, interval_id in enumerate(HPS_REX_RUN_ORDER):
        if interval_id == "scalar_par":
            per_fixture = spectral.scalar_par_ppf_umol_s_per_fixture
            whole_layout = spectral.fixture_count * per_fixture
            label, start_nm, end_nm = "Scalar PAR", 400, 699
        else:
            budget = spectral.band(interval_id)
            per_fixture = budget.per_fixture_ppf_umol_s
            whole_layout = budget.whole_layout_ppf_umol_s
            label, start_nm, end_nm = budget.label, budget.start_nm, budget.end_nm
        source_input = _source_input(
            spectral,
            interval_id=interval_id,
            label=label,
            start_nm=start_nm,
            end_nm=end_nm,
            per_fixture_ppf_umol_s=per_fixture,
            whole_layout_ppf_umol_s=whole_layout,
            shared_angular_dat_identity=shared_dat_identity,
        )
        carrier = per_fixture * RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W
        flatcorr = carrier / aperture_area
        material = materials.material(interval_id)
        source_text = _interval_source_text(source_plan, flatcorr, interval_id)
        source_sha = _sha256_text(source_text)
        source_id = "hps-isolated-source-v1-" + _hash_payload(
            {
                "source_input_id": source_input.source_input_id,
                "source_text_sha256": source_sha,
                "carrier_multiplier": carrier,
            }
        )
        plant_text = export_plant_mesh_to_radiance(
            plant,
            material_name=material.material_identifier,
            material_definition=material.radiance_text,
        )
        plant_material_sha = _sha256_text(plant_text)
        directory = output_root / f"{order_index:02d}_{interval_id}"
        paths = HpsTransportRunPaths(
            directory=directory,
            source_rad=directory / "hps_source.rad",
            plant_rad=directory / "rex_plant.rad",
            baseline_octree=directory / "baseline_scene.oct",
            fspm_octree=directory / "fspm_scene.oct",
            ambient_cache=directory / (
                "fspm_scene."
                f"{PRODUCTION_ROOM_MODEL_IDENTITY_SHA256[:16]}.amb"
            ),
            receivers=receivers_path,
            raw_rgb=directory / "receiver.rgb",
        )
        baseline = _scene_composition(
            role="baseline_ppfd",
            component_roles=("room", "hps_emitters", "fixture_bodies"),
            source_files=(
                room_path,
                paths.source_rad,
                fixture_occlusion.instance_source_path,
            ),
            output_octree=paths.baseline_octree,
            cwd=directory,
            identity_inputs={
                "room_identity": room_identity,
                "source_definition_id": source_id,
                "fixture_occlusion_identity": (
                    fixture_occlusion.identity_sha256
                ),
            },
        )
        fspm = _scene_composition(
            role="fspm_receiver",
            component_roles=(
                "room",
                "hps_emitters",
                "fixture_bodies",
                "rex_plant",
            ),
            source_files=(
                room_path,
                paths.source_rad,
                fixture_occlusion.instance_source_path,
                paths.plant_rad,
            ),
            output_octree=paths.fspm_octree,
            cwd=directory,
            identity_inputs={
                "room_identity": room_identity,
                "source_definition_id": source_id,
                "plant_identity": plant_identity,
                "plant_material_text_sha256": plant_material_sha,
                "fixture_occlusion_identity": (
                    fixture_occlusion.identity_sha256
                ),
            },
        )
        ambient_identity = "hps-ambient-v2-" + _hash_payload(
            {
                "fspm_octree_identity": fspm.octree_identity,
                "receiver_identity": receiver_identity,
                "threads": threads,
            }
        )
        rtrace = build_plant_receiver_rtrace_command(
            octree=paths.fspm_octree,
            receiver_input=paths.receivers,
            rgb_output=paths.raw_rgb,
            options=("-af", str(paths.ambient_cache)),
            nthreads=threads,
            cwd=directory,
        )
        runs.append(
            HpsIsolatedRunPlan(
                order_index=order_index,
                interval_id=interval_id,
                source_input=source_input,
                material=material,
                per_fixture_ppf_umol_s=per_fixture,
                whole_layout_ppf_umol_s=whole_layout,
                carrier_multiplier=carrier,
                aperture_area_m2=aperture_area,
                flat_source_correction=flatcorr,
                source_definition_id=source_id,
                source_text_sha256=source_sha,
                plant_material_text_sha256=plant_material_sha,
                baseline_scene=baseline,
                fspm_scene=fspm,
                ambient_cache_identity=ambient_identity,
                rtrace_command=rtrace,
                expected_result_units=(
                    EXPECTED_FAR_RED_RESULT_UNITS
                    if interval_id == "far_red"
                    else EXPECTED_PAR_BAND_RESULT_UNITS
                ),
                paths=paths,
            )
        )
    return HpsIsolatedTransportBundlePlan(
        workspace=root,
        output_root=output_root,
        layout=selected_layout,
        phase25a_source_plan=source_plan,
        fixture_occlusion=fixture_occlusion,
        source_payload=spectral,
        optical_payload=optics,
        material_plan=materials,
        room_identity=room_identity,
        plant_identity=plant_identity,
        plant_geometry_sha256=geometry_sha,
        plant_polygon_count=plant.face_count,
        leaf_count=len(plant.leaves),
        patch_count=plant.patch_count,
        leaf_patch_grid=plant.config.leaf_patch_grid,
        receiver_identity=receiver_identity,
        receiver_text_sha256=receiver_sha,
        receiver_count=len(receivers),
        ordered_receiver_ids=ordered_receiver_ids,
        shared_angular_dat_identity=shared_dat_identity,
        runs=tuple(runs),
    )


def format_hps_isolated_transport_bundle_json(
    plan: HpsIsolatedTransportBundlePlan,
) -> str:
    return plan.to_json()


def format_hps_isolated_run_source_rad(
    plan: HpsIsolatedTransportBundlePlan,
    interval_id: str,
    *,
    dat_reference: str = "hps_unit_downward_flux.dat",
) -> str:
    """Format one Phase 25B source with a safe caller-selected DAT reference."""

    if Path(dat_reference).is_absolute() or ".." in Path(dat_reference).parts:
        raise ValueError("HPS DAT reference must be safe and relative.")
    try:
        run = next(item for item in plan.runs if item.interval_id == interval_id)
    except StopIteration as exc:
        raise KeyError(f"unknown HPS isolated interval: {interval_id!r}") from exc
    text = _interval_source_text(
        plan.phase25a_source_plan,
        run.flat_source_correction,
        run.interval_id,
    )
    original = "hps_unit_downward_flux.dat"
    if text.split().count(original) != 1:
        raise ValueError("Phase 25B source must contain exactly one angular DAT reference.")
    if dat_reference == original and _sha256_text(text) != run.source_text_sha256:
        raise ValueError("Phase 25B source serialization identity changed.")
    if dat_reference != original:
        text = text.replace(original, dat_reference, 1)
    if text.split().count(dat_reference) != 1:
        raise ValueError("formatted HPS source DAT reference is inconsistent.")
    return text


def _source_input(
    source: HpsSpectralSourcePayload,
    *,
    interval_id: str,
    label: str,
    start_nm: int,
    end_nm: int,
    per_fixture_ppf_umol_s: float,
    whole_layout_ppf_umol_s: float,
    shared_angular_dat_identity: str,
) -> SourceNeutralIsolatedTransportInput:
    if source.scientific_payload()["model_id"] != HPS_BAND_SOURCE_MODEL_ID:
        raise ValueError("HPS isolated transport rejects non-HPS source identity.")
    group = IsolatedTransportEmitterGroup(
        group_id="hps_fixture_array",
        emitter_count=source.fixture_count,
        photon_flux_per_emitter_umol_s=per_fixture_ppf_umol_s,
        total_photon_flux_umol_s=whole_layout_ppf_umol_s,
    )
    return SourceNeutralIsolatedTransportInput(
        interval_id=interval_id,
        label=label,
        start_nm=start_nm,
        end_nm=end_nm,
        source_family="hps",
        source_model_id=HPS_BAND_SOURCE_MODEL_ID,
        source_payload_id=source.source_payload_id,
        normalization_policy=source.normalization_policy,
        emitter_groups=(group,),
        total_photon_flux_umol_s=whole_layout_ppf_umol_s,
        angular_model_id=HPS_ANGULAR_MODEL_ID,
        shared_angular_data_identity=shared_angular_dat_identity,
    )


def _scene_composition(
    *,
    role: str,
    component_roles: tuple[str, ...],
    source_files: tuple[Path, ...],
    output_octree: Path,
    cwd: Path,
    identity_inputs: dict[str, str],
) -> HpsSceneComposition:
    scene = ScenePlan(role=role, source_files=source_files)
    scene_id = "hps-scene-v1-" + _hash_payload(
        {"role": role, **identity_inputs}
    )
    octree_identity = "hps-octree-v1-" + _hash_payload(
        {"scene_id": scene_id}
    )
    return HpsSceneComposition(
        role=role,
        component_roles=component_roles,
        scene=scene,
        scene_id=scene_id,
        octree_identity=octree_identity,
        oconv_command=scene.compilation_command(output_octree, cwd=cwd),
    )


def _interval_source_text(
    source_plan: HpsRadianceSourcePlan,
    flat_source_correction: float,
    interval_id: str,
) -> str:
    lines = [
        f"# isolated_interval={interval_id}",
        f"void brightdata {HPS_ANGULAR_MODIFIER_ID}",
        "5 flatcorr hps_unit_downward_flux.dat source.cal src_phi src_theta",
        "0",
        f"1 {format(flat_source_correction, '.17g')}",
        "",
        f"{HPS_ANGULAR_MODIFIER_ID} light {HPS_LIGHT_MODIFIER_ID}",
        "0",
        "0",
        "3 1 1 1",
        "",
    ]
    for aperture in source_plan.apertures:
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


def _scene_payload(scene: HpsSceneComposition) -> dict[str, object]:
    return {
        "role": scene.role,
        "component_roles": list(scene.component_roles),
        "scene_id": scene.scene_id,
        "octree_identity": scene.octree_identity,
        "rebuild_from_sources": scene.scene.rebuild_from_sources,
    }


def _command_payload(command: CommandSpec, workspace: Path) -> dict[str, object]:
    root = str(workspace)

    def normalized(value: object) -> str | None:
        if value is None:
            return None
        return str(value).replace(root, "$WORKSPACE")

    return {
        "argv": [normalized(token) for token in command.argv],
        "stdin_path": normalized(command.stdin_path),
        "stdout_path": normalized(command.stdout_path),
        "cwd": normalized(command.cwd),
        "label": command.label,
        "stdout_mode": command.stdout_mode,
        "execution_performed": False,
    }


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
