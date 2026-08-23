"""Immutable fixture assets and server-resolved run display transforms."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import resources
import json
import math
from pathlib import PurePosixPath
import struct
from types import MappingProxyType
from typing import Final, Mapping

from fspm_optics.fixtures.conventional_led.profile import (
    CONVENTIONAL_COMPARISON_PROFILE_ID,
)
from fspm_optics.fixtures.smd.alignment_lattice import (
    validate_alignment_lattice_payload,
)
from fspm_optics.fixtures.hps.profile import HPS_COMPARISON_PROFILE_ID
from fspm_optics.geometry.mounting import MountingGeometry
from fspm_optics.layout.fixture_plan import (
    LEGACY_FIXTURE_POLICY_ID,
    STANDALONE_FIXTURE_POLICY_ID,
    fixture_policy_id,
    resolve_ordered_display_fixture_type,
)
from fspm_optics.layout.mode import ProposedLayoutMode, resolve_proposed_layout_mode
from fspm_optics.layout.ring import (
    ProposedRingMode,
    proposed_module_pattern_id,
    resolve_proposed_ring_mode,
)
from fspm_optics.viewer.models import BinaryDisplayArtifact

CATALOG_SCHEMA_ID: Final = "fspm-optics.run-fixture-catalog"
CATALOG_SCHEMA_VERSION: Final = 2
LEGACY_CATALOG_SCHEMA_VERSION: Final = 1
MATRIX_STRIDE_BYTES: Final = 64
ANCHOR_TOLERANCE_M: Final = 2.5e-4
APPROVED_GLB_EXTENSIONS: Final = ("KHR_mesh_quantization",)
_SPATIAL_ORTHOGONAL_TOL: Final = 1.0e-5
HPS_PLACEMENT_CONTRACT_SCHEMA_ID: Final = (
    "fspm-optics.hps-publication-placement"
)
HPS_PLACEMENT_CONTRACT_SCHEMA_VERSION: Final = 1
HPS_LOCAL_APERTURE_PLANE_Y_MM: Final = -248.92
HPS_LOCAL_APERTURE_OUTWARD_NORMAL: Final = (0.0, -1.0, 0.0)
_HPS_APERTURE_PLANE_BASIS_POINTS_MM: Final = (
    (0.0, HPS_LOCAL_APERTURE_PLANE_Y_MM, 0.0),
    (1000.0, HPS_LOCAL_APERTURE_PLANE_Y_MM, 0.0),
    (0.0, HPS_LOCAL_APERTURE_PLANE_Y_MM, 1000.0),
)


@dataclass(frozen=True, slots=True)
class FixtureAsset:
    asset_id: str
    system_id: str
    fixture_type: str
    resource_path: str
    byte_size: int
    sha256: str
    meters_per_asset_unit: float
    local_up_axis: str
    local_forward_axis: str
    pivot_contract: str
    placement_plane_local_y_mm: float
    placement_correction_local_x_mm: float
    placement_correction_local_y_mm: float
    placement_correction_local_z_mm: float
    material_alpha_modes: tuple[str, ...]
    approved_extensions: tuple[str, ...]
    dimension_correction_scale_xyz: tuple[float, float, float] = (1.0, 1.0, 1.0)
    approved_base_color_alpha_factors: tuple[float, ...] = (1.0,)
    materials_double_sided: bool = True
    anchors_m: tuple[tuple[float, float, float], ...] = ()

    def public_metadata(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "approved_system_id": self.system_id,
            "approved_display_fixture_type": self.fixture_type,
            "packaged_resource_path": self.resource_path,
            "expected_byte_size": self.byte_size,
            "expected_sha256": self.sha256,
            "meters_per_asset_unit": self.meters_per_asset_unit,
            "dimension_correction_scale_xyz": list(
                self.dimension_correction_scale_xyz
            ),
            "asset_local_axes": {
                "up": self.local_up_axis,
                "forward": self.local_forward_axis,
            },
            "pivot": {
                "contract": self.pivot_contract,
                "placement_plane_local_y_mm": self.placement_plane_local_y_mm,
                "placement_correction_local_x_mm": (
                    self.placement_correction_local_x_mm
                ),
                "placement_correction_local_y_mm": (
                    self.placement_correction_local_y_mm
                ),
                "placement_correction_local_z_mm": (
                    self.placement_correction_local_z_mm
                ),
            },
            "material_alpha_modes": list(self.material_alpha_modes),
            "approved_base_color_alpha_factors": list(
                self.approved_base_color_alpha_factors
            ),
            "materials_double_sided": self.materials_double_sided,
            "approved_glb_extensions": list(self.approved_extensions),
        }


@dataclass(frozen=True, slots=True)
class FixturePublishedFile:
    relative_path: str
    data: bytes
    sha256: str

    @property
    def byte_size(self) -> int:
        return len(self.data)


@dataclass(frozen=True, slots=True)
class FixturePublication:
    catalog: BinaryDisplayArtifact
    files: tuple[FixturePublishedFile, ...]
    authoritative_layout_sha256: str
    fixture_plan_sha256: str
    fixture_count: int
    asset_group_count: int


@dataclass(frozen=True, slots=True)
class FixtureTransportPlacement:
    """Exact server-authored GLB placement exposed to scientific transport."""

    fixture_id: str
    asset: FixtureAsset
    authoritative_matrix_row_major: tuple[float, ...]
    matrix_row_major: tuple[float, ...]
    matrix_column_major_sha256: str
    placement_contract_sha256: str | None


@dataclass(frozen=True, slots=True)
class HpsPublicationTransformValidation:
    """Evidence that one published HPS matrix preserves its aperture plane."""

    authoritative_points_scientific_m: tuple[tuple[float, float, float], ...]
    published_points_scientific_m: tuple[tuple[float, float, float], ...]
    published_outward_normal_scientific: tuple[float, float, float]
    maximum_publication_error_m: float
    maximum_float32_error_bound_m: float


# Salvaged CAD anchors are stored here in authoritative member-module order.
# The centerpiece swaps CAD modules 3/4, while both four-module corner assets
# reverse their CAD name order. The three-module corner preserves salvaged
# module_1/module_2/module_3 order, so no runtime correspondence inference is
# necessary.
_CENTERPIECE = (
    (-0.000007832, -0.003547298, 0.000000001),
    (0.280002422, -0.003547298, -0.280010253),
    (0.280002422, -0.003547298, 0.280010255),
    (-0.279994372, -0.003547298, 0.280010255),
    (-0.279994372, -0.003547298, -0.280010253),
)
_LINEAR2 = (
    (-0.283210611, -0.008045624, 0.000005741),
    (0.283210595, -0.008045624, 0.000005741),
)
_LINEAR3 = (
    (-0.559977965, -0.003584776, -0.000027559),
    (-0.000000015, -0.003584776, -0.000027559),
    (0.559977936, -0.003584776, -0.000027559),
)
_CORNER3 = (
    (0.000014092, -0.008038842, 0.000010835),
    (0.51399358, -0.008038842, 0.000010835),
    (0.51399358, -0.008038842, -0.514010832),
)
_LINEAR4 = (
    (-0.770962273, -0.008029901, 0.000003214),
    (-0.257022287, -0.008029901, 0.000003214),
    (0.257022242, -0.008029901, 0.000003214),
    (0.770962228, -0.008029901, 0.000003214),
)
_L = (
    (0.559977936, -0.003558005, -0.560005509),
    (0.559977936, -0.003558005, -0.000027559),
    (-0.000000015, -0.003558005, -0.000027559),
    (-0.559977965, -0.003558005, -0.000027559),
)
_REVERSE_L = (
    (0.559977936, -0.003558005, -0.000027559),
    (-0.000000015, -0.003558005, -0.000027559),
    (-0.559977965, -0.003558005, -0.000027559),
    (-0.559977965, -0.003558005, -0.560005509),
)


def _asset(
    asset_id: str,
    system_id: str,
    fixture_type: str,
    resource_path: str,
    byte_size: int,
    sha256: str,
    *,
    anchors: tuple[tuple[float, float, float], ...] = (),
    placement_plane_local_y_mm: float | None = None,
    placement_correction_local_x_mm: float = 0.0,
    placement_correction_local_y_mm: float = 0.0,
    placement_correction_local_z_mm: float = 0.0,
    alpha_modes: tuple[str, ...] = ("OPAQUE",),
    alpha_factors: tuple[float, ...] = (1.0,),
    dimension_correction_scale_xyz: tuple[float, float, float] = (1.0, 1.0, 1.0),
    pivot_contract: str = "module_anchor_plane_reflected_for_leds_down",
) -> FixtureAsset:
    resolved_plane_y_mm = (
        sum(anchor[1] for anchor in anchors) * 1000.0 / len(anchors)
        if placement_plane_local_y_mm is None and anchors
        else float(placement_plane_local_y_mm or 0.0)
    )
    return FixtureAsset(
        asset_id=asset_id,
        system_id=system_id,
        fixture_type=fixture_type,
        resource_path=resource_path,
        byte_size=byte_size,
        sha256=sha256,
        meters_per_asset_unit=0.001,
        local_up_axis="positive_y_after_authored_glb_scene_transform",
        local_forward_axis="negative_z_after_authored_glb_scene_transform",
        pivot_contract=pivot_contract,
        placement_plane_local_y_mm=resolved_plane_y_mm,
        placement_correction_local_x_mm=placement_correction_local_x_mm,
        placement_correction_local_y_mm=placement_correction_local_y_mm,
        placement_correction_local_z_mm=placement_correction_local_z_mm,
        material_alpha_modes=alpha_modes,
        approved_extensions=APPROVED_GLB_EXTENSIONS,
        dimension_correction_scale_xyz=dimension_correction_scale_xyz,
        approved_base_color_alpha_factors=alpha_factors,
        anchors_m=anchors,
    )


ASSET_REGISTRY: Final = (
    _asset(
        "proposed-centerpiece-v1", "proposed", "centerpiece",
        "fixtures/proposed/centerpiece.glb", 362744,
        "76daa0290eafb3947b30489d304287b25dab998f847ba5d5a8163775cb38c1ba",
        anchors=_CENTERPIECE,
        placement_plane_local_y_mm=27.382621983,
        pivot_contract="opaque_bottom_cover_light_side_is_aperture_plane",
    ),
    _asset(
        "proposed-linear2-v1", "proposed", "linear2",
        "fixtures/proposed/linear2.glb", 165832,
        "814de19659cff0130b508f197a2194383748eee191f10927524229aabd5c288d",
        anchors=_LINEAR2,
        placement_plane_local_y_mm=27.398212731,
        pivot_contract="opaque_bottom_cover_light_side_is_aperture_plane",
    ),
    _asset(
        "proposed-linear3-v1", "proposed", "linear3",
        "fixtures/proposed/linear3.glb", 233288,
        "e9cb018d9ce5bb6babe59044d88639c6620d6018eb22e71f6fc404c250145d93",
        anchors=_LINEAR3,
        placement_plane_local_y_mm=27.388845602,
        pivot_contract="opaque_bottom_cover_light_side_is_aperture_plane",
    ),
    _asset(
        "proposed-corner3-v1", "proposed", "corner3",
        "fixtures/proposed/corner3.glb", 238648,
        "e30f98457b1af1725843ed0c42edfc9d22516445c602f4edcc8b8ed116c7d243",
        anchors=_CORNER3,
        placement_plane_local_y_mm=27.396988109,
        pivot_contract="opaque_bottom_cover_light_side_is_aperture_plane",
    ),
    _asset(
        "proposed-linear4-v1", "proposed", "linear4",
        "fixtures/proposed/linear4.glb", 353128,
        "1f77749ae1c3dda08cf8a521734f9aea5e54b64df68b963341bdca5407ca6809",
        anchors=_LINEAR4,
        placement_plane_local_y_mm=27.376385716,
        pivot_contract="opaque_bottom_cover_light_side_is_aperture_plane",
    ),
    _asset(
        "proposed-l-v1", "proposed", "L",
        "fixtures/proposed/l.glb", 363232,
        "ea1c9ea94aa08c30a655458c0a01e74a3687e2e4333203d10961520d6634730e",
        anchors=_L,
        placement_plane_local_y_mm=27.421794448,
        pivot_contract="opaque_bottom_cover_light_side_is_aperture_plane",
    ),
    _asset(
        "proposed-reverse-l-v1", "proposed", "reverse_L",
        "fixtures/proposed/reverse_l.glb", 363064,
        "eb87a439e99ae95ac5ad73ea96ba4de9bbcd03609e45355d4e92a8210034badd",
        anchors=_REVERSE_L,
        placement_plane_local_y_mm=27.421794448,
        pivot_contract="opaque_bottom_cover_light_side_is_aperture_plane",
    ),
    _asset(
        "proposed-led-module-v1", "proposed", "standalone_module",
        "fixtures/proposed/led_module.glb", 88540,
        "f910a7fc512635bcb3a48d7b546175ef13623135836a76eeb625b2ca12cd5445",
        anchors=((0.0, 0.0, 0.0),),
        placement_plane_local_y_mm=27.4,
        pivot_contract="opaque_bottom_cover_light_side_is_aperture_plane",
    ),
    _asset(
        "conventional-led-8-bar-v1", "conventional", "conventional_led_8_bar",
        "fixtures/conventional/conventional_led_8_bar.glb", 109392,
        "0d640d8e20bfdc213d3722dc44652979366b7c19ef153fff10372fe66051af53",
        placement_correction_local_x_mm=0.0000024865,
        placement_correction_local_z_mm=0.0008827926,
        dimension_correction_scale_xyz=(
            1.0000000041789914,
            1.1552917718844276,
            0.999998375729409,
        ),
        pivot_contract="asset_bottom_is_luminous_aperture_placement_plane",
    ),
    _asset(
        "hps-housing-v3", "hps", "hps_1000w_fixture",
        "fixtures/hps/hps.glb", 353160,
        "083df1475e84e9552d5ec548fc34669092b5ba4284fcf33ebf7dbb2f26439d7d",
        placement_plane_local_y_mm=-248.92,
        placement_correction_local_y_mm=248.92,
        alpha_modes=("BLEND", "OPAQUE"),
        alpha_factors=(0.349999994, 1.0),
        pivot_contract=(
            "housing_bottom_shifted_to_separate_scientific_luminous_aperture_plane"
        ),
    ),
)

ASSET_BY_ID: Final = MappingProxyType(
    {asset.asset_id: asset for asset in ASSET_REGISTRY}
)
ASSET_BY_SYSTEM_TYPE: Final = MappingProxyType(
    {(asset.system_id, asset.fixture_type): asset for asset in ASSET_REGISTRY}
)


def _validate_registry() -> None:
    if len(ASSET_BY_ID) != 10 or len(ASSET_BY_SYSTEM_TYPE) != 10:
        raise ValueError("fixture asset registry identities must be unique.")
    for asset in ASSET_REGISTRY:
        physical_system = "hps" if asset.system_id == "hps" else asset.system_id
        expected_prefix = f"fixtures/{physical_system}/"
        resource_path = PurePosixPath(asset.resource_path)
        if (
            not asset.asset_id
            or asset.system_id not in {"proposed", "conventional", "hps"}
            or not asset.resource_path.startswith(expected_prefix)
            or resource_path.is_absolute()
            or any(part in {"", ".", ".."} for part in resource_path.parts)
            or len(asset.sha256) != 64
            or any(character not in "0123456789abcdef" for character in asset.sha256)
            or asset.byte_size <= 0
            or asset.meters_per_asset_unit != 0.001
        ):
            raise ValueError(f"fixture asset registry record is invalid: {asset.asset_id}")


_validate_registry()


def hps_placement_contract_payload(asset: FixtureAsset) -> dict[str, object]:
    """Return every authenticated input to the HPS publication transform."""

    return {
        "schema_id": HPS_PLACEMENT_CONTRACT_SCHEMA_ID,
        "schema_version": HPS_PLACEMENT_CONTRACT_SCHEMA_VERSION,
        "asset_id": asset.asset_id,
        "authenticated_glb_sha256": asset.sha256,
        "meters_per_asset_unit": asset.meters_per_asset_unit,
        "dimension_correction_scale_xyz": list(
            asset.dimension_correction_scale_xyz
        ),
        "asset_local_axes": {
            "up": asset.local_up_axis,
            "forward": asset.local_forward_axis,
        },
        "pivot_contract": asset.pivot_contract,
        "placement_plane_local_y_mm": asset.placement_plane_local_y_mm,
        "placement_correction_local_xyz_mm": [
            asset.placement_correction_local_x_mm,
            asset.placement_correction_local_y_mm,
            asset.placement_correction_local_z_mm,
        ],
        "matrix_composition": (
            "scale_local_axes_then_apply_local_correction_then_"
            "scientific_translation_v1"
        ),
        "publication_component_type": "float32",
        "publication_record_layout": "matrix4_column_major",
        "scientific_to_viewer": "(x, y, z) -> (x, z, -y)",
    }


def hps_placement_contract_sha256(asset: FixtureAsset) -> str:
    """Bind cache and publication compatibility to all HPS placement inputs."""

    return _hash_json(hps_placement_contract_payload(asset))


def build_fixture_publication(
    *,
    run_id: str,
    system_id: str,
    requested_length_ft: float,
    requested_width_ft: float,
    layout_identity: Mapping[str, object],
    mounting_height: Mapping[str, object] | None = None,
) -> FixturePublication:
    publication = _build_fixture_publication(
        run_id=run_id,
        system_id=system_id,
        requested_length_ft=requested_length_ft,
        requested_width_ft=requested_width_ft,
        layout_identity=layout_identity,
        mounting_height=mounting_height,
    )
    validate_fixture_publication(
        publication.catalog.data,
        {item.relative_path: item.data for item in publication.files},
        expected_run_id=run_id,
        expected_system_id=system_id,
        expected_requested_length_ft=requested_length_ft,
        expected_requested_width_ft=requested_width_ft,
        expected_layout_identity=layout_identity,
        expected_mounting_height=mounting_height,
    )
    return publication


def _build_fixture_publication(
    *,
    run_id: str,
    system_id: str,
    requested_length_ft: float,
    requested_width_ft: float,
    layout_identity: Mapping[str, object],
    mounting_height: Mapping[str, object] | None = None,
) -> FixturePublication:
    """Build one run catalog and complete server-authored matrix buffers."""

    if (
        not isinstance(run_id, str)
        or len(run_id) != 32
        or any(character not in "0123456789abcdef" for character in run_id)
    ):
        raise ValueError("fixture catalog run ID is incompatible.")
    for label, value in (
        ("requested length", requested_length_ft),
        ("requested width", requested_width_ft),
    ):
        if not 0.0 < _finite(value, label):
            raise ValueError("fixture catalog room dimensions must be positive.")
    layout = dict(layout_identity)
    _validate_layout_room(
        system_id, layout, requested_length_ft, requested_width_ft
    )
    layout_sha256 = _hash_json(layout)
    mounting = (
        None
        if mounting_height is None
        else _validated_mounting_height(mounting_height)
    )
    if mounting is not None:
        _validate_layout_mounting(system_id, layout, mounting)
    placements = _resolve_fixture_placements(system_id, layout)
    if not placements:
        raise ValueError("fixture display plan must contain at least one fixture.")
    proposed_composition: dict[str, object] = {}
    alignment_lattice_payload: dict[str, object] | None = None
    if system_id == "proposed":
        raw_mode = layout.get("proposed_layout_mode")
        if (
            raw_mode is None
            and layout.get("fixture_policy_id") == LEGACY_FIXTURE_POLICY_ID
        ):
            raw_mode = "legacy"
        mode = resolve_proposed_layout_mode(
            raw_mode
        )
        expected_policy_id = fixture_policy_id(mode)
        if layout.get("fixture_policy_id") != expected_policy_id:
            raise ValueError(
                "Proposed fixture policy does not match its layout mode."
            )
        if layout.get("proposed_layout_mode") is not None:
            proposed_composition = {
                "proposed_layout_mode": mode.value,
                "fixture_policy_id": expected_policy_id,
            }
            raw_ring_mode = layout.get("proposed_ring_mode")
            raw_pattern_id = layout.get("module_pattern_id")
            if (raw_ring_mode is None) != (raw_pattern_id is None):
                raise ValueError(
                    "Proposed ring mode and module-pattern identity must be paired."
                )
            if raw_ring_mode is not None:
                ring_mode = resolve_proposed_ring_mode(raw_ring_mode)
                expected_pattern_id = proposed_module_pattern_id(ring_mode)
                if raw_pattern_id != expected_pattern_id:
                    raise ValueError(
                        "Proposed module-pattern identity does not match its ring mode."
                    )
                if (
                    ring_mode is ProposedRingMode.REDUCED_ONE_RING
                    and mode is not ProposedLayoutMode.STANDALONE_MODULES
                ):
                    raise ValueError(
                        "Reduced Proposed rings require standalone module fixtures."
                    )
                proposed_composition |= {
                    "proposed_ring_mode": ring_mode.value,
                    "module_pattern_id": expected_pattern_id,
                }
            if mode is ProposedLayoutMode.STANDALONE_MODULES:
                alignment_lattice_payload = dict(
                    validate_alignment_lattice_payload(
                        layout.get("alignment_lattice"),
                        expected_module_count=len(placements),
                        expected_modules=layout.get("modules"),
                    )
                )
                proposed_composition["alignment_lattice_identity_sha256"] = (
                    alignment_lattice_payload["identity_sha256"]
                )
            elif "alignment_lattice" in layout:
                raise ValueError(
                    "alignment lattice is limited to standalone Proposed modules."
                )
    fixture_plan = {
        "run_id": run_id,
        "system_id": system_id,
        "requested_room_ft": {
            "length": requested_length_ft,
            "width": requested_width_ft,
        },
        "authoritative_layout_sha256": layout_sha256,
        **proposed_composition,
        "ordering": "authoritative fixture order",
        "placement_inference": False,
        "display_classification_policy": (
            (
                "proposed_standalone_module_with_alignment_lattice_identity_v2"
                if proposed_composition.get("fixture_policy_id")
                == STANDALONE_FIXTURE_POLICY_ID
                else "proposed_member_connector_topology_v2"
            )
            if system_id == "proposed"
            else "system_fixed_display_asset_v1"
        ),
        "fixtures": [placement["record"] for placement in placements],
    }
    if mounting is not None:
        fixture_plan["mounting_height_sha256"] = _hash_json(mounting)
    fixture_plan_sha256 = _hash_json(fixture_plan)
    used_asset_ids = tuple(
        asset.asset_id
        for asset in ASSET_REGISTRY
        if any(item["asset"].asset_id == asset.asset_id for item in placements)
    )
    files: list[FixturePublishedFile] = []
    groups: list[dict[str, object]] = []
    for asset_id in used_asset_ids:
        asset = ASSET_BY_ID[asset_id]
        asset_bytes = _load_and_validate_asset(asset)
        asset_filename = f"assets/{asset.asset_id}-{asset.sha256[:16]}.glb"
        files.append(_published_file(f"fixtures/{asset_filename}", asset_bytes))
        selected = tuple(item for item in placements if item["asset"] == asset)
        matrix_values = tuple(
            component for item in selected for component in item["matrix_column_major"]
        )
        matrix_bytes = struct.pack(f"<{len(matrix_values)}f", *matrix_values)
        matrix_sha256 = hashlib.sha256(matrix_bytes).hexdigest()
        matrix_filename = (
            f"transforms/{asset.asset_id}-{fixture_plan_sha256[:16]}.f32le.bin"
        )
        files.append(_published_file(f"fixtures/{matrix_filename}", matrix_bytes))
        groups.append(
            {
                "display_asset_id": asset.asset_id,
                "display_fixture_type": asset.fixture_type,
                "asset": {
                    "filename": asset_filename,
                    "byte_size": len(asset_bytes),
                    "sha256": asset.sha256,
                },
                "registry": asset.public_metadata(),
                "ordered_fixture_ids": [item["record"]["fixture_id"] for item in selected],
                "instance_matrices": {
                    "filename": matrix_filename,
                    "component_type": "float32",
                    "byte_order": "little-endian",
                    "count": len(selected),
                    "stride_bytes": MATRIX_STRIDE_BYTES,
                    "byte_length": len(matrix_bytes),
                    "record_layout": "matrix4_column_major",
                    "matrix_convention": (
                        "column vectors; viewer_position = matrix * packaged_glb_scene_position"
                    ),
                    "coordinate_space": "viewer right-handed meters, Y-up",
                    "scientific_to_viewer": "(x, y, z) -> (x, z, -y)",
                    "sha256": matrix_sha256,
                },
            }
        )
    catalog_payload = {
        "schema_id": CATALOG_SCHEMA_ID,
        "schema_version": (
            CATALOG_SCHEMA_VERSION
            if mounting is not None
            else LEGACY_CATALOG_SCHEMA_VERSION
        ),
        "run": {"run_id": run_id, "system_id": system_id},
        "requested_room_ft": {
            "length": requested_length_ft,
            "width": requested_width_ft,
        },
        "authoritative_layout_sha256": layout_sha256,
        **proposed_composition,
        "fixture_plan_sha256": fixture_plan_sha256,
        "ordering": "authoritative fixture order within each stable asset group",
        "transform_authority": (
            "server_resolved_from_authoritative_layout_and_immutable_asset_registry"
        ),
        "browser_transform_inference": False,
        "fixture_count": len(placements),
        "asset_group_count": len(groups),
        "fixture_plan": fixture_plan,
        "asset_groups": groups,
        **(
            {}
            if alignment_lattice_payload is None
            else {"alignment_lattice": alignment_lattice_payload}
        ),
    }
    if mounting is not None:
        catalog_payload["mounting_height"] = mounting
    catalog_bytes = _json_bytes(catalog_payload)
    publication = FixturePublication(
        catalog=BinaryDisplayArtifact(
            "catalog.v1.json",
            catalog_bytes,
            hashlib.sha256(catalog_bytes).hexdigest(),
        ),
        files=tuple(files),
        authoritative_layout_sha256=layout_sha256,
        fixture_plan_sha256=fixture_plan_sha256,
        fixture_count=len(placements),
        asset_group_count=len(groups),
    )
    return publication


def validate_fixture_publication(
    catalog_bytes: bytes,
    files: Mapping[str, bytes],
    *,
    expected_run_id: str,
    expected_system_id: str,
    expected_requested_length_ft: float,
    expected_requested_width_ft: float,
    expected_layout_identity: Mapping[str, object],
    expected_mounting_height: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Rebuild and compare the run fixture plan, assets, matrices, and hashes."""

    payload = _json_object(catalog_bytes, "fixture catalog")
    expected_schema_version = (
        CATALOG_SCHEMA_VERSION
        if expected_mounting_height is not None
        else LEGACY_CATALOG_SCHEMA_VERSION
    )
    if (
        payload.get("schema_id") != CATALOG_SCHEMA_ID
        or payload.get("schema_version") != expected_schema_version
        or payload.get("run")
        != {"run_id": expected_run_id, "system_id": expected_system_id}
        or payload.get("requested_room_ft")
        != {
            "length": expected_requested_length_ft,
            "width": expected_requested_width_ft,
        }
        or payload.get("authoritative_layout_sha256")
        != _hash_json(dict(expected_layout_identity))
    ):
        raise ValueError("fixture catalog run, request, system, or layout is incompatible.")
    expected = _build_fixture_publication(
        run_id=expected_run_id,
        system_id=expected_system_id,
        requested_length_ft=expected_requested_length_ft,
        requested_width_ft=expected_requested_width_ft,
        layout_identity=expected_layout_identity,
        mounting_height=expected_mounting_height,
    )
    if catalog_bytes != expected.catalog.data:
        raise ValueError("fixture catalog does not match the authoritative fixture plan.")
    expected_files = {item.relative_path: item.data for item in expected.files}
    if set(files) != set(expected_files):
        raise ValueError("fixture catalog file inventory is incomplete or undeclared.")
    for relative, expected_data in expected_files.items():
        data = files[relative]
        if data != expected_data:
            raise ValueError(f"fixture artifact failed exact validation: {relative}")
    return payload


def _validated_mounting_height(
    payload: Mapping[str, object],
) -> dict[str, object]:
    mounting = dict(payload)
    try:
        expected = MountingGeometry.resolve(
            mounting.get("mounting_height_in")
        ).to_payload()
    except ValueError as exc:
        raise ValueError(
            "fixture mounting-height provenance is incompatible."
        ) from exc
    if mounting != expected:
        raise ValueError("fixture mounting-height provenance is incompatible.")
    return mounting


def _validate_layout_mounting(
    system_id: str,
    layout: Mapping[str, object],
    mounting: Mapping[str, object],
) -> None:
    reference = mounting["reference_plane_z_m"]
    distance = mounting["mounting_height_m"]
    aperture = mounting["emitting_aperture_plane_z_m"]
    definition = mounting["definition"]
    if system_id == "proposed":
        modules = layout.get("modules")
        if (
            not isinstance(modules, list)
            or not modules
            or any(
                not isinstance(module, dict) or module.get("z_m") != aperture
                for module in modules
            )
        ):
            raise ValueError(
                "Proposed layout and mounting-height provenance disagree."
            )
        return
    mount = layout.get("mount")
    if not isinstance(mount, dict):
        raise ValueError("fixture layout mounting geometry is missing.")
    expected = (
        {
            "reference_plane_z_m": reference,
            "mount_height_m": distance,
            "mount_height_definition": definition,
            "aperture_z_m": aperture,
        }
        if system_id == "conventional"
        else {
            "reference_plane_z_m": reference,
            "mount_height_m": distance,
            "definition": definition,
            "aperture_plane_z_m": aperture,
        }
    )
    if system_id not in {"conventional", "hps"} or any(
        mount.get(name) != value for name, value in expected.items()
    ):
        raise ValueError("fixture layout and mounting-height provenance disagree.")


def _resolve_fixture_placements(
    system_id: str,
    layout: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    if system_id == "proposed":
        return _proposed_placements(layout)
    if system_id == "conventional":
        return _direct_placements(layout, system_id, "conventional_led_8_bar")
    if system_id == "hps":
        return _direct_placements(layout, system_id, "hps_1000w_fixture")
    raise ValueError("fixture catalog system is unsupported.")


def resolve_fixture_transport_placements(
    system_id: str,
    layout_identity: Mapping[str, object],
) -> tuple[FixtureTransportPlacement, ...]:
    """Return the same GLB matrices used by fixture publication.

    Each matrix maps authored GLB scene positions to viewer right-handed
    metres.  Transport applies the catalog's declared coordinate mapping back
    to scientific Z-up space; it does not infer a second placement.
    """

    output: list[FixtureTransportPlacement] = []
    for item in _resolve_fixture_placements(system_id, dict(layout_identity)):
        record = item["record"]
        fixture_id = record.get("fixture_id")
        asset = item["asset"]
        column_major = item["matrix_column_major"]
        if (
            not isinstance(fixture_id, str)
            or not isinstance(asset, FixtureAsset)
            or not isinstance(column_major, tuple)
            or len(column_major) != 16
        ):
            raise ValueError("fixture transport placement is malformed.")
        published_matrix_bytes = struct.pack("<16f", *column_major)
        published_column_major = struct.unpack(
            "<16f", published_matrix_bytes
        )
        if hashlib.sha256(published_matrix_bytes).hexdigest() != str(
            record["matrix4_column_major_sha256"]
        ):
            raise ValueError(
                "fixture transport matrix differs from viewer publication."
            )
        row_major = tuple(
            float(published_column_major[column * 4 + row])
            for row in range(4)
            for column in range(4)
        )
        authoritative_row_major = tuple(
            float(column_major[column * 4 + row])
            for row in range(4)
            for column in range(4)
        )
        placement_contract_sha256 = record.get(
            "placement_contract_sha256"
        )
        if placement_contract_sha256 is not None and not isinstance(
            placement_contract_sha256, str
        ):
            raise ValueError(
                "fixture transport placement contract identity is malformed."
            )
        expected_placement_contract_sha256 = (
            hps_placement_contract_sha256(asset)
            if system_id == "hps"
            else None
        )
        if placement_contract_sha256 != expected_placement_contract_sha256:
            raise ValueError(
                "fixture transport placement contract identity is incompatible."
            )
        output.append(
            FixtureTransportPlacement(
                fixture_id=fixture_id,
                asset=asset,
                authoritative_matrix_row_major=authoritative_row_major,
                matrix_row_major=row_major,
                matrix_column_major_sha256=str(
                    record["matrix4_column_major_sha256"]
                ),
                placement_contract_sha256=placement_contract_sha256,
            )
        )
    return tuple(output)


def _validate_layout_room(
    system_id: str,
    layout: Mapping[str, object],
    requested_length_ft: float,
    requested_width_ft: float,
) -> None:
    expected = (requested_length_ft * 0.3048, requested_width_ft * 0.3048)
    active_domain = layout.get("active_domain")
    if active_domain is not None:
        if not isinstance(active_domain, dict):
            raise ValueError("fixture active-domain identity is invalid.")
        outer = active_domain.get("outer_requested_m")
        active = active_domain.get("active_requested_m")
        if not isinstance(outer, dict) or not isinstance(active, dict):
            raise ValueError("fixture active-domain dimensions are missing.")
        declared_outer = (
            _finite(outer.get("length"), "outer active-domain length"),
            _finite(outer.get("width"), "outer active-domain width"),
        )
        if any(
            not math.isclose(left, right, rel_tol=0.0, abs_tol=1.0e-12)
            for left, right in zip(declared_outer, expected, strict=True)
        ):
            raise ValueError(
                "fixture active domain does not match the authorized outer room."
            )
        expected = (
            _finite(active.get("length"), "active-domain length"),
            _finite(active.get("width"), "active-domain width"),
        )
    if system_id == "proposed":
        actual = (
            _finite(layout.get("room_length_m"), "Proposed room length"),
            _finite(layout.get("room_width_m"), "Proposed room width"),
        )
        if layout.get("axes_swapped") is True:
            actual = (actual[1], actual[0])
    else:
        axes = layout.get("room_axes")
        requested = axes.get("requested_m") if isinstance(axes, dict) else None
        if not isinstance(requested, dict):
            raise ValueError("fixture layout requested-room identity is missing.")
        if system_id == "conventional":
            actual = (
                _finite(requested.get("length_m"), "Conventional room length"),
                _finite(requested.get("width_m"), "Conventional room width"),
            )
        elif system_id == "hps":
            actual = (
                _finite(requested.get("length"), "HPS room length"),
                _finite(requested.get("width"), "HPS room width"),
            )
        else:
            raise ValueError("fixture catalog system is unsupported.")
    if any(
        not math.isclose(left, right, rel_tol=0.0, abs_tol=1.0e-12)
        for left, right in zip(actual, expected, strict=True)
    ):
        raise ValueError("fixture layout room does not match the authorized request.")


def _proposed_placements(
    layout: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    if (
        resolve_proposed_layout_mode(layout.get("proposed_layout_mode"))
        is ProposedLayoutMode.STANDALONE_MODULES
    ):
        return _standalone_proposed_placements(layout)
    modules = layout.get("modules")
    fixtures = layout.get("fixtures")
    swapped = layout.get("axes_swapped")
    if (
        not isinstance(modules, list)
        or not isinstance(fixtures, list)
        or not isinstance(swapped, bool)
    ):
        raise ValueError("Proposed authoritative layout is missing display inputs.")
    module_by_index: dict[int, Mapping[str, object]] = {}
    for expected_index, module in enumerate(modules):
        if not isinstance(module, dict) or module.get("module_index") != expected_index:
            raise ValueError("Proposed modules must retain contiguous authoritative order.")
        module_by_index[expected_index] = module
    output: list[dict[str, object]] = []
    for fixture_index, fixture in enumerate(fixtures):
        if not isinstance(fixture, dict):
            raise ValueError("Proposed fixture record is malformed.")
        fixture_id = fixture.get("fixture_id")
        fixture_type = fixture.get("fixture_type")
        display_fixture_type = fixture.get("display_fixture_type")
        member_indices = fixture.get("member_module_indices")
        orientation = fixture.get("orientation_degrees")
        if (
            fixture_id != f"proposed-fixture-{fixture_index:04d}"
            or not isinstance(fixture_type, str)
            or not isinstance(display_fixture_type, str)
            or not isinstance(member_indices, list)
            or isinstance(orientation, bool)
            or not isinstance(orientation, int | float)
        ):
            raise ValueError("Proposed fixture identity or ordering is incompatible.")
        if any(
            isinstance(member_index, bool)
            or not isinstance(member_index, int)
            or member_index not in module_by_index
            for member_index in member_indices
        ):
            raise ValueError("Proposed member module identity is invalid.")
        resolved_display_fixture_type = _proposed_display_fixture_type(
            fixture_type,
            member_indices,
            fixture.get("connectors"),
            module_by_index,
        )
        if display_fixture_type != resolved_display_fixture_type:
            raise ValueError(
                "Proposed display fixture type disagrees with member topology."
            )
        asset = ASSET_BY_SYSTEM_TYPE.get(("proposed", display_fixture_type))
        if asset is None or len(member_indices) != len(asset.anchors_m):
            raise ValueError(
                "Proposed display fixture type or anchor count is unsupported."
            )
        targets: list[tuple[float, float, float]] = []
        scientific: list[tuple[float, float, float]] = []
        for member_index in member_indices:
            if (
                isinstance(member_index, bool)
                or not isinstance(member_index, int)
                or member_index not in module_by_index
            ):
                raise ValueError("Proposed member module identity is invalid.")
            module = module_by_index[member_index]
            aligned_x = _finite(module.get("x_m"), "Proposed module x")
            aligned_y = _finite(module.get("y_m"), "Proposed module y")
            z_m = _finite(module.get("z_m"), "Proposed module z")
            scientific.append((aligned_x, aligned_y, z_m))
            targets.append((aligned_x, z_m, -aligned_y))
        matrix = _proposed_matrix(asset, targets)
        aligned_yaw, requested_yaw = _validate_proposed_orientation(
            fixture,
            module_by_index,
            member_indices,
            swapped,
            float(orientation),
        )
        output.append(
            {
                "asset": asset,
                "matrix_column_major": _column_major(matrix),
                "record": {
                    "fixture_id": fixture_id,
                    "assembly_id": fixture_id,
                    "fixture_type": fixture_type,
                    "display_fixture_type": display_fixture_type,
                    "display_asset_id": asset.asset_id,
                    "member_module_indices": list(member_indices),
                    "scientific_translation_m": {
                        "x": sum(item[0] for item in scientific) / len(scientific),
                        "y": sum(item[1] for item in scientific) / len(scientific),
                        "z": sum(item[2] for item in scientific) / len(scientific),
                    },
                    "orientation": {
                        "authoritative_aligned_degrees": aligned_yaw,
                        "requested_scientific_degrees": requested_yaw,
                    },
                    "matrix4_column_major_sha256": _matrix_sha256(matrix),
                    "anchor_max_residual_m": _anchor_max_residual(asset, matrix, targets),
                },
            }
        )
    return tuple(output)


def _standalone_proposed_placements(
    layout: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    """Place one full-scale CAD module at each authoritative module center."""

    if layout.get("fixture_policy_id") != STANDALONE_FIXTURE_POLICY_ID:
        raise ValueError("standalone Proposed layout policy is incompatible.")
    if layout.get("fixture_asset_set_id") != "proposed-led-module-v1":
        raise ValueError("standalone Proposed asset identity is incompatible.")
    envelope = layout.get("mechanical_envelope")
    if envelope != {
        "id": "standalone_module_150x150mm_v1",
        "width_x_m": 0.15,
        "height_y_m": 0.15,
    }:
        raise ValueError("standalone Proposed mechanical envelope is incompatible.")
    modules = layout.get("modules")
    fixtures = layout.get("fixtures")
    swapped = layout.get("axes_swapped")
    if (
        not isinstance(modules, list)
        or not isinstance(fixtures, list)
        or not isinstance(swapped, bool)
        or len(fixtures) != len(modules)
    ):
        raise ValueError("standalone Proposed display inputs are incomplete.")
    asset = ASSET_BY_SYSTEM_TYPE[("proposed", "standalone_module")]
    output: list[dict[str, object]] = []
    for index, (module, fixture) in enumerate(
        zip(modules, fixtures, strict=True)
    ):
        if (
            not isinstance(module, dict)
            or module.get("module_index") != index
            or not isinstance(fixture, dict)
            or fixture.get("fixture_id") != f"proposed-fixture-{index:04d}"
            or fixture.get("fixture_type") != "standalone_module"
            or fixture.get("display_fixture_type") != "standalone_module"
            or fixture.get("source_orientation") != "fixed"
            or fixture.get("orientation_degrees") != 0.0
            or fixture.get("member_module_indices") != [index]
            or fixture.get("connectors") != []
        ):
            raise ValueError(
                "standalone Proposed singleton ownership or ordering changed."
            )
        x_m = _finite(module.get("x_m"), "standalone Proposed module x")
        y_m = _finite(module.get("y_m"), "standalone Proposed module y")
        z_m = _finite(module.get("z_m"), "standalone Proposed module z")
        matrix = _standalone_proposed_matrix(asset, x_m, y_m, z_m)
        output.append(
            {
                "asset": asset,
                "matrix_column_major": _column_major(matrix),
                "record": {
                    "fixture_id": fixture["fixture_id"],
                    "assembly_id": fixture["fixture_id"],
                    "fixture_type": "standalone_module",
                    "display_fixture_type": "standalone_module",
                    "display_asset_id": asset.asset_id,
                    "member_module_indices": [index],
                    "scientific_translation_m": {
                        "x": x_m,
                        "y": y_m,
                        "z": z_m,
                    },
                    "orientation": {
                        "authoritative_aligned_degrees": 0.0,
                        "requested_scientific_degrees": 0.0,
                        "fixed": True,
                    },
                    "matrix4_column_major_sha256": _matrix_sha256(matrix),
                    "anchor_max_residual_m": 0.0,
                    "placement_plane": (
                        "opaque_bottom_cover_light_side_is_aperture_plane"
                    ),
                },
            }
        )
    return tuple(output)


def _standalone_proposed_matrix(
    asset: FixtureAsset,
    x_m: float,
    y_m: float,
    z_m: float,
) -> tuple[float, ...]:
    if (
        asset.asset_id != "proposed-led-module-v1"
        or asset.meters_per_asset_unit != 0.001
        or asset.placement_plane_local_y_mm != 27.4
    ):
        raise ValueError("standalone Proposed asset transform contract changed.")
    scale = 0.001
    matrix = (
        scale, 0.0, 0.0, x_m,
        0.0, -scale, 0.0, z_m + 27.4 * scale,
        0.0, 0.0, scale, -y_m,
        0.0, 0.0, 0.0, 1.0,
    )
    validate_standalone_proposed_transform(
        matrix,
        module_center_m=(x_m, y_m, z_m),
        placement_plane_local_y_mm=27.4,
    )
    return matrix


def validate_standalone_proposed_transform(
    matrix: tuple[float, ...],
    *,
    module_center_m: tuple[float, float, float],
    placement_plane_local_y_mm: float,
) -> None:
    """Prove exact rigid millimetre conversion and cover-plane registration."""

    _validate_affine_matrix(matrix)
    basis = (
        (matrix[0], matrix[4], matrix[8]),
        (matrix[1], matrix[5], matrix[9]),
        (matrix[2], matrix[6], matrix[10]),
    )
    lengths = tuple(
        math.sqrt(math.fsum(value * value for value in axis))
        for axis in basis
    )
    if any(
        not math.isclose(length, 0.001, rel_tol=0.0, abs_tol=1.0e-15)
        for length in lengths
    ):
        raise ValueError("standalone transform basis lengths must equal 0.001.")
    if not math.isclose(
        abs(_normalized_spatial_determinant(matrix)),
        1.0,
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise ValueError(
            "standalone transform normalized determinant magnitude must equal one."
        )
    if matrix[:12] != (
        0.001, 0.0, 0.0, module_center_m[0],
        0.0, -0.001, 0.0,
        module_center_m[2] + placement_plane_local_y_mm * 0.001,
        0.0, 0.0, 0.001, -module_center_m[1],
    ):
        raise ValueError(
            "standalone transform may contain only fixed axis conversion and translation."
        )
    registered = _transform(
        matrix,
        (0.0, placement_plane_local_y_mm, 0.0),
    )
    expected_viewer = (
        module_center_m[0],
        module_center_m[2],
        -module_center_m[1],
    )
    if any(
        not math.isclose(left, right, rel_tol=0.0, abs_tol=1.0e-15)
        for left, right in zip(registered, expected_viewer, strict=True)
    ):
        raise ValueError(
            "standalone cover plane and authoritative source center disagree."
        )


def _direct_placements(
    layout: Mapping[str, object], system_id: str, fixture_type: str
) -> tuple[dict[str, object], ...]:
    fixtures = layout.get("fixtures")
    if not isinstance(fixtures, list):
        raise ValueError("direct fixture layout is missing fixtures.")
    conventional_height_m: float | None = None
    hps_aperture_z_m: float | None = None
    if system_id == "conventional":
        dimensions = layout.get("fixture_dimensions_m")
        if (
            layout.get("conventional_profile_id")
            != CONVENTIONAL_COMPARISON_PROFILE_ID
            or not isinstance(dimensions, dict)
            or not math.isclose(
                _finite(dimensions.get("length_m"), "Conventional LED length"),
                1.19,
                abs_tol=5.0e-6,
            )
            or not math.isclose(
                _finite(dimensions.get("width_m"), "Conventional LED width"),
                1.087,
                abs_tol=5.0e-6,
            )
        ):
            raise ValueError(
                "Conventional LED display model and authoritative fixture dimensions disagree."
            )
        conventional_height_m = _finite(
            dimensions.get("height_m"), "Conventional LED height"
        )
        if conventional_height_m != 0.108:
            raise ValueError("Conventional LED display housing height is incompatible.")
    else:
        footprint = layout.get("fixture_footprint_m")
        mount = layout.get("mount")
        if (
            layout.get("profile_id") != HPS_COMPARISON_PROFILE_ID
            or not isinstance(footprint, dict)
            or footprint.get("length_x") != 0.798576
            or footprint.get("width_y") != 0.603504
            or not isinstance(mount, dict)
            or _finite(mount.get("mount_height_m"), "HPS mount height")
            <= 0.0
            or not math.isclose(
                _finite(
                    mount.get("aperture_plane_z_m"),
                    "HPS aperture plane z",
                )
                - _finite(
                    mount.get("reference_plane_z_m"),
                    "HPS reference plane z",
                ),
                _finite(mount.get("mount_height_m"), "HPS mount height"),
                rel_tol=0.0,
                abs_tol=1.0e-12,
            )
        ):
            raise ValueError(
                "HPS housing and authoritative aperture contract disagree."
            )
        hps_aperture_z_m = _finite(
            mount.get("aperture_plane_z_m"),
            "HPS aperture plane z",
        )
    _validate_direct_order(layout, fixtures, system_id)
    asset = ASSET_BY_SYSTEM_TYPE[(system_id, fixture_type)]
    output: list[dict[str, object]] = []
    for fixture in fixtures:
        if not isinstance(fixture, dict) or not isinstance(
            fixture.get("fixture_id"), str
        ):
            raise ValueError("direct fixture identity is malformed.")
        fixture_id = fixture["fixture_id"]
        if system_id == "conventional":
            if (
                fixture.get("conventional_profile_id")
                != CONVENTIONAL_COMPARISON_PROFILE_ID
            ):
                raise ValueError(
                    "Conventional fixture profile identity is incompatible."
                )
            center = fixture.get("aperture_center_m")
            fixture_center = fixture.get("fixture_center_m")
            transform = fixture.get("transform")
            if (
                not isinstance(center, dict)
                or not isinstance(fixture_center, dict)
                or not isinstance(transform, dict)
            ):
                raise ValueError("Conventional fixture center or transform is missing.")
            x_m = _finite(center.get("aligned_x_m"), "Conventional aligned x")
            y_m = _finite(center.get("aligned_y_m"), "Conventional aligned y")
            z_m = _finite(center.get("z_m"), "Conventional aperture z")
            shared_center_keys = (
                "aligned_x_m", "aligned_y_m", "requested_x_m", "requested_y_m"
            )
            if any(
                fixture_center.get(key) != center.get(key)
                for key in shared_center_keys
            ):
                raise ValueError(
                    "Conventional housing and aperture horizontal centers must align."
                )
            expected_center_z = z_m + float(conventional_height_m) / 2.0
            if not math.isclose(
                _finite(fixture_center.get("z_m"), "Conventional fixture center z"),
                expected_center_z,
                rel_tol=0.0,
                abs_tol=1.0e-12,
            ):
                raise ValueError(
                    "Conventional housing center and aperture plane disagree."
                )
            yaw = _finite(
                transform.get("fixture_rotation_degrees"), "Conventional yaw"
            )
        else:
            if fixture.get("profile_id") != HPS_COMPARISON_PROFILE_ID:
                raise ValueError("HPS fixture profile identity is incompatible.")
            center = fixture.get("center_m")
            if not isinstance(center, dict):
                raise ValueError("HPS fixture center is missing.")
            x_m = _finite(center.get("aligned_x"), "HPS aligned x")
            y_m = _finite(center.get("aligned_y"), "HPS aligned y")
            z_m = _finite(center.get("aperture_z"), "HPS aperture z")
            if (
                hps_aperture_z_m is None
                or not math.isclose(
                    z_m,
                    hps_aperture_z_m,
                    rel_tol=0.0,
                    abs_tol=1.0e-12,
                )
                or not math.isclose(
                    _finite(
                        center.get("fixture_center_z"),
                        "HPS fixture center z",
                    ),
                    z_m,
                    rel_tol=0.0,
                    abs_tol=1.0e-12,
                )
            ):
                raise ValueError(
                    "HPS housing and run-specific aperture plane disagree."
                )
            yaw = 0.0
        matrix = _direct_matrix(asset, x_m, y_m, z_m, yaw)
        placement_contract_sha256: str | None = None
        if system_id == "hps":
            placement_contract_sha256 = hps_placement_contract_sha256(asset)
            validate_hps_publication_transform(
                asset=asset,
                authoritative_matrix_row_major=matrix,
                published_matrix_row_major=_float32_matrix(matrix),
                scientific_center_m=(x_m, y_m, z_m),
                placement_contract_sha256=placement_contract_sha256,
            )
        record = {
            "fixture_id": fixture_id,
            "assembly_id": fixture_id,
            "fixture_type": fixture_type,
            "display_fixture_type": fixture_type,
            "display_asset_id": asset.asset_id,
            "scientific_translation_m": {"x": x_m, "y": y_m, "z": z_m},
            "orientation": {"authoritative_aligned_degrees": yaw},
            "matrix4_column_major_sha256": _matrix_sha256(matrix),
            "placement_plane": (
                "luminous_aperture_plane_separate_from_housing"
                if system_id == "hps"
                else "emitting_aperture_plane"
            ),
        }
        if placement_contract_sha256 is not None:
            record["placement_contract_sha256"] = placement_contract_sha256
        output.append(
            {
                "asset": asset,
                "matrix_column_major": _column_major(matrix),
                "record": record,
            }
        )
    return tuple(output)


def _proposed_display_fixture_type(
    fixture_type: str,
    member_indices: list[object],
    connectors: object,
    module_by_index: Mapping[int, Mapping[str, object]],
) -> str:
    if fixture_type not in {
        "centerpiece",
        "linear2",
        "linear3",
        "linear4",
        "L",
        "reverse_L",
    }:
        raise ValueError("Proposed scientific fixture type is unsupported.")
    expected_count = {
        "centerpiece": 5,
        "linear2": 2,
        "linear3": 3,
        "linear4": 4,
        "L": 4,
        "reverse_L": 4,
    }[fixture_type]
    if len(member_indices) != expected_count or not isinstance(connectors, list):
        raise ValueError(f"{fixture_type} display topology is incomplete.")
    if fixture_type == "centerpiece":
        expected_pairs = tuple(
            (member_indices[0], member_index)
            for member_index in member_indices[1:]
        )
    elif fixture_type.startswith("linear"):
        expected_pairs = tuple(zip(member_indices, member_indices[1:]))
    elif fixture_type == "L":
        expected_pairs = (
            (member_indices[1], member_indices[2]),
            (member_indices[2], member_indices[3]),
            (member_indices[0], member_indices[1]),
        )
    else:
        expected_pairs = (
            (member_indices[0], member_indices[1]),
            (member_indices[1], member_indices[2]),
            (member_indices[2], member_indices[3]),
        )
    actual_pairs: list[tuple[object, object]] = []
    for connector in connectors:
        if not isinstance(connector, dict):
            raise ValueError("Proposed display connector is malformed.")
        actual_pairs.append(
            (
                connector.get("start_module_index"),
                connector.get("end_module_index"),
            )
        )
    if tuple(actual_pairs) != expected_pairs:
        raise ValueError("Proposed display connectors do not match member topology.")
    points = []
    for member_index in member_indices:
        module = module_by_index[int(member_index)]
        points.append(
            (
                _finite(module.get("x_m"), "Proposed display module x"),
                _finite(module.get("y_m"), "Proposed display module y"),
            )
        )
    return resolve_ordered_display_fixture_type(
        fixture_type,
        tuple(points),
    )


def _validate_proposed_orientation(
    fixture: Mapping[str, object],
    module_by_index: Mapping[int, Mapping[str, object]],
    member_indices: list[object],
    axes_swapped: bool,
    declared_aligned_yaw: float,
) -> tuple[float, float]:
    connectors = fixture.get("connectors")
    if not isinstance(connectors, list) or not connectors:
        raise ValueError(
            "Proposed fixture orientation requires an authoritative connector."
        )
    connector = connectors[0]
    if not isinstance(connector, dict):
        raise ValueError("Proposed fixture connector is malformed.")
    start_index = connector.get("start_module_index")
    end_index = connector.get("end_module_index")
    if (
        isinstance(start_index, bool)
        or not isinstance(start_index, int)
        or isinstance(end_index, bool)
        or not isinstance(end_index, int)
        or start_index not in member_indices
        or end_index not in member_indices
        or start_index not in module_by_index
        or end_index not in module_by_index
    ):
        raise ValueError("Proposed fixture connector membership is incompatible.")
    start = module_by_index[start_index]
    end = module_by_index[end_index]
    aligned_start = (
        _finite(start.get("x_m"), "Proposed connector start x"),
        _finite(start.get("y_m"), "Proposed connector start y"),
    )
    aligned_end = (
        _finite(end.get("x_m"), "Proposed connector end x"),
        _finite(end.get("y_m"), "Proposed connector end y"),
    )
    resolved_aligned_yaw = _yaw(aligned_start, aligned_end)
    if not math.isclose(
        resolved_aligned_yaw,
        declared_aligned_yaw % 360.0,
        rel_tol=0.0,
        abs_tol=1.0e-6,
    ):
        raise ValueError(
            "Proposed fixture yaw disagrees with its authoritative connector."
        )
    requested_start = (
        (-aligned_start[1], aligned_start[0])
        if axes_swapped
        else aligned_start
    )
    requested_end = (
        (-aligned_end[1], aligned_end[0])
        if axes_swapped
        else aligned_end
    )
    return resolved_aligned_yaw, _yaw(requested_start, requested_end)


def _validate_direct_order(
    layout: Mapping[str, object], fixtures: list[object], system_id: str
) -> None:
    counts_key = "resolved_counts" if system_id == "conventional" else "counts"
    counts = layout.get(counts_key)
    if not isinstance(counts, dict):
        raise ValueError("fixture grid count identity is missing.")
    columns = counts.get("columns_x")
    rows = counts.get("rows_y")
    if (
        isinstance(columns, bool)
        or not isinstance(columns, int)
        or columns <= 0
        or isinstance(rows, bool)
        or not isinstance(rows, int)
        or rows <= 0
        or len(fixtures) != columns * rows
        or counts.get("total") != columns * rows
    ):
        raise ValueError("fixture grid count identity is incompatible.")
    expected = [(row, column) for row in range(rows) for column in range(columns)]
    actual: list[tuple[object, object]] = []
    for fixture in fixtures:
        grid = fixture.get("grid") if isinstance(fixture, dict) else None
        if not isinstance(grid, dict):
            raise ValueError("fixture grid identity is missing.")
        actual.append((grid.get("row_y"), grid.get("column_x")))
    if actual != expected:
        raise ValueError("fixtures must retain authoritative Y-major/X-minor order.")


def _proposed_matrix(
    asset: FixtureAsset, targets: list[tuple[float, float, float]]
) -> tuple[float, ...]:
    sources = [(item[0], item[2]) for item in asset.anchors_m]
    target_horizontal = [(item[0], item[2]) for item in targets]
    a00, a01, a10, a11, tx, tz = _horizontal_affine(sources, target_horizontal)
    target_y = sum(item[1] for item in targets) / len(targets)
    placement_plane_y = asset.placement_plane_local_y_mm * 0.001
    matrix = (
        a00 * 0.001, 0.0, a01 * 0.001, tx,
        0.0, -0.001, 0.0, target_y + placement_plane_y,
        a10 * 0.001, 0.0, a11 * 0.001, tz,
        0.0, 0.0, 0.0, 1.0,
    )
    _validate_affine_matrix(matrix)
    _validate_anchor_residuals(asset, matrix, targets)
    placement_residual = abs(
        matrix[5] * asset.placement_plane_local_y_mm
        + matrix[7]
        - target_y
    )
    if placement_residual > 1.0e-12:
        raise ValueError(
            f"{asset.asset_id} aperture placement-plane residual "
            f"{placement_residual:.9g} m exceeds 1e-12 m."
        )
    return matrix


def _horizontal_affine(
    sources: list[tuple[float, float]], targets: list[tuple[float, float]]
) -> tuple[float, float, float, float, float, float]:
    origin_s = sources[0]
    origin_t = targets[0]
    best: tuple[int, int, float] | None = None
    for left in range(1, len(sources)):
        for right in range(left + 1, len(sources)):
            sx1 = sources[left][0] - origin_s[0]
            sy1 = sources[left][1] - origin_s[1]
            sx2 = sources[right][0] - origin_s[0]
            sy2 = sources[right][1] - origin_s[1]
            determinant = sx1 * sy2 - sy1 * sx2
            if best is None or abs(determinant) > abs(best[2]):
                best = (left, right, determinant)
    if best is not None and abs(best[2]) > 1.0e-12:
        left, right, determinant = best
        sx1 = sources[left][0] - origin_s[0]
        sy1 = sources[left][1] - origin_s[1]
        sx2 = sources[right][0] - origin_s[0]
        sy2 = sources[right][1] - origin_s[1]
        tx1 = targets[left][0] - origin_t[0]
        ty1 = targets[left][1] - origin_t[1]
        tx2 = targets[right][0] - origin_t[0]
        ty2 = targets[right][1] - origin_t[1]
        a00 = (tx1 * sy2 - tx2 * sy1) / determinant
        a01 = (-tx1 * sx2 + tx2 * sx1) / determinant
        a10 = (ty1 * sy2 - ty2 * sy1) / determinant
        a11 = (-ty1 * sx2 + ty2 * sx1) / determinant
    else:
        dxs = sources[-1][0] - origin_s[0]
        dys = sources[-1][1] - origin_s[1]
        dxt = targets[-1][0] - origin_t[0]
        dyt = targets[-1][1] - origin_t[1]
        source_length = math.hypot(dxs, dys)
        target_length = math.hypot(dxt, dyt)
        if source_length <= 1.0e-12 or target_length <= 1.0e-12:
            raise ValueError("fixture anchors cannot resolve a stable transform.")
        scale = target_length / source_length
        cosine = (dxs * dxt + dys * dyt) / (source_length * target_length)
        sine = (dxs * dyt - dys * dxt) / (source_length * target_length)
        a00, a01 = scale * cosine, -scale * sine
        a10, a11 = scale * sine, scale * cosine
    tx = origin_t[0] - a00 * origin_s[0] - a01 * origin_s[1]
    tz = origin_t[1] - a10 * origin_s[0] - a11 * origin_s[1]
    return a00, a01, a10, a11, tx, tz


def _direct_matrix(
    asset: FixtureAsset, x_m: float, y_m: float, z_m: float, yaw_degrees: float
) -> tuple[float, ...]:
    radians = math.radians(yaw_degrees)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    scale = asset.meters_per_asset_unit
    correction_x, correction_y, correction_z = asset.dimension_correction_scale_xyz
    matrix = (
        scale * correction_x * cosine, 0.0, -scale * correction_z * sine,
        x_m + scale * (
            correction_x * cosine * asset.placement_correction_local_x_mm
            - correction_z * sine * asset.placement_correction_local_z_mm
        ),
        0.0, scale * correction_y, 0.0,
        z_m + scale * correction_y * asset.placement_correction_local_y_mm,
        -scale * correction_x * sine, 0.0, -scale * correction_z * cosine,
        -y_m - scale * (
            correction_x * sine * asset.placement_correction_local_x_mm
            + correction_z * cosine * asset.placement_correction_local_z_mm
        ),
        0.0, 0.0, 0.0, 1.0,
    )
    _validate_affine_matrix(matrix)
    return matrix


def validate_hps_publication_transform(
    *,
    asset: FixtureAsset,
    authoritative_matrix_row_major: tuple[float, ...],
    published_matrix_row_major: tuple[float, ...],
    scientific_center_m: tuple[float, float, float],
    placement_contract_sha256: str,
) -> HpsPublicationTransformValidation:
    """Fail closed unless Float32 publication preserves the HPS aperture plane.

    The double-precision matrix is the server construction authority.  The
    Float32 matrix is the byte-exact viewer and Radiance transport matrix.  Its
    allowance is derived component-by-component from that one quantization;
    no mesh bound or fixed world-space tolerance participates in placement.
    """

    _validate_hps_asset_placement_contract(asset)
    _validate_affine_matrix(authoritative_matrix_row_major)
    _validate_affine_matrix(published_matrix_row_major)
    if (
        placement_contract_sha256 != hps_placement_contract_sha256(asset)
        or published_matrix_row_major
        != _float32_matrix(authoritative_matrix_row_major)
    ):
        raise ValueError(
            "HPS publication matrix or placement identity is incompatible."
        )
    x_m, y_m, aperture_z_m = scientific_center_m
    expected_authoritative = (
        0.001, 0.0, 0.0, x_m,
        0.0, 0.001, 0.0, aperture_z_m + 0.24892,
        0.0, 0.0, -0.001, -y_m,
        0.0, 0.0, 0.0, 1.0,
    )
    if authoritative_matrix_row_major != expected_authoritative:
        raise ValueError(
            "HPS authoritative publication matrix composition changed."
        )

    authoritative_points: list[tuple[float, float, float]] = []
    published_points: list[tuple[float, float, float]] = []
    maximum_error = 0.0
    maximum_bound = 0.0
    for point_mm in _HPS_APERTURE_PLANE_BASIS_POINTS_MM:
        expected = (
            x_m + point_mm[0] * 0.001,
            y_m + point_mm[2] * 0.001,
            aperture_z_m,
        )
        authoritative = _scientific_transform_point(
            authoritative_matrix_row_major,
            point_mm,
        )
        published = _scientific_transform_point(
            published_matrix_row_major,
            point_mm,
        )
        authoritative_tolerance = _binary64_transform_tolerance(
            authoritative,
            expected,
        )
        if any(
            abs(actual - target) > authoritative_tolerance
            for actual, target in zip(authoritative, expected, strict=True)
        ):
            raise ValueError(
                "HPS authoritative matrix does not register its aperture plane."
            )
        bound = _float32_transform_error_bound(
            authoritative_matrix_row_major,
            published_matrix_row_major,
            point_mm,
        ) + authoritative_tolerance
        error = max(
            abs(actual - target)
            for actual, target in zip(published, expected, strict=True)
        )
        if error > bound:
            raise ValueError(
                "HPS Float32 publication exceeds its quantization error bound."
            )
        authoritative_points.append(authoritative)
        published_points.append(published)
        maximum_error = max(maximum_error, error)
        maximum_bound = max(maximum_bound, bound)

    normal = _scientific_transform_vector(
        published_matrix_row_major,
        HPS_LOCAL_APERTURE_OUTWARD_NORMAL,
    )
    normal_length = math.sqrt(math.fsum(value * value for value in normal))
    normalized = tuple(value / normal_length for value in normal)
    if normalized != (0.0, 0.0, -1.0):
        raise ValueError(
            "HPS published aperture-plane normal orientation changed."
        )
    return HpsPublicationTransformValidation(
        authoritative_points_scientific_m=tuple(authoritative_points),
        published_points_scientific_m=tuple(published_points),
        published_outward_normal_scientific=normalized,
        maximum_publication_error_m=maximum_error,
        maximum_float32_error_bound_m=maximum_bound,
    )


def _validate_hps_asset_placement_contract(asset: FixtureAsset) -> None:
    if (
        asset.asset_id != "hps-housing-v3"
        or asset.system_id != "hps"
        or asset.fixture_type != "hps_1000w_fixture"
        or asset.meters_per_asset_unit != 0.001
        or asset.dimension_correction_scale_xyz != (1.0, 1.0, 1.0)
        or asset.placement_plane_local_y_mm != HPS_LOCAL_APERTURE_PLANE_Y_MM
        or asset.placement_correction_local_x_mm != 0.0
        or asset.placement_correction_local_y_mm != 248.92
        or asset.placement_correction_local_z_mm != 0.0
        or asset.pivot_contract
        != "housing_bottom_shifted_to_separate_scientific_luminous_aperture_plane"
    ):
        raise ValueError("HPS authenticated placement contract changed.")


def _float32_matrix(matrix: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(struct.unpack("<16f", struct.pack("<16f", *matrix)))


def _scientific_transform_point(
    matrix: tuple[float, ...],
    point_mm: tuple[float, float, float],
) -> tuple[float, float, float]:
    viewer = _transform(matrix, point_mm)
    return (viewer[0], -viewer[2], viewer[1])


def _scientific_transform_vector(
    matrix: tuple[float, ...],
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    x, y, z = vector
    viewer = (
        matrix[0] * x + matrix[1] * y + matrix[2] * z,
        matrix[4] * x + matrix[5] * y + matrix[6] * z,
        matrix[8] * x + matrix[9] * y + matrix[10] * z,
    )
    return (viewer[0], -viewer[2], viewer[1])


def _binary64_transform_tolerance(
    actual: tuple[float, float, float],
    expected: tuple[float, float, float],
) -> float:
    magnitude = max(1.0, *(abs(value) for value in (*actual, *expected)))
    return 16.0 * math.ulp(magnitude)


def _float32_transform_error_bound(
    authoritative: tuple[float, ...],
    published: tuple[float, ...],
    point_mm: tuple[float, float, float],
) -> float:
    homogeneous = (*point_mm, 1.0)
    viewer_axis_bounds = tuple(
        math.fsum(
            abs(
                published[row * 4 + column]
                - authoritative[row * 4 + column]
            )
            * abs(homogeneous[column])
            for column in range(4)
        )
        for row in range(3)
    )
    magnitude = max(
        1.0,
        *(
            abs(matrix[row * 4 + column] * homogeneous[column])
            for matrix in (authoritative, published)
            for row in range(3)
            for column in range(4)
        ),
    )
    arithmetic_tolerance = 16.0 * math.ulp(magnitude)
    return max(viewer_axis_bounds) + arithmetic_tolerance


def _validate_affine_matrix(matrix: tuple[float, ...]) -> None:
    if (
        len(matrix) != 16
        or not all(math.isfinite(value) for value in matrix)
        or matrix[12:] != (0.0, 0.0, 0.0, 1.0)
    ):
        raise ValueError(
            "fixture placement matrix must contain finite affine values."
        )
    basis = (
        (matrix[0], matrix[4], matrix[8]),
        (matrix[1], matrix[5], matrix[9]),
        (matrix[2], matrix[6], matrix[10]),
    )
    lengths = tuple(math.sqrt(sum(value * value for value in axis)) for axis in basis)
    determinant = _matrix3_determinant(matrix)
    if any(length <= 1.0e-15 for length in lengths) or abs(determinant) <= 1.0e-15:
        raise ValueError("fixture placement matrix must be nonsingular.")
    for left in range(3):
        for right in range(left + 1, 3):
            normalized_dot = sum(
                basis[left][index] * basis[right][index]
                for index in range(3)
            ) / (lengths[left] * lengths[right])
            if abs(normalized_dot) > _SPATIAL_ORTHOGONAL_TOL:
                raise ValueError(
                    "fixture placement matrix spatial basis must be orthogonal."
                )
    normalized_determinant = _normalized_spatial_determinant(matrix)
    if not math.isclose(
        abs(normalized_determinant),
        1.0,
        rel_tol=0.0,
        abs_tol=_SPATIAL_ORTHOGONAL_TOL,
    ):
        raise ValueError(
            "fixture placement matrix normalized determinant must have unit magnitude."
        )


def _matrix3_determinant(matrix: tuple[float, ...]) -> float:
    return (
        matrix[0] * (matrix[5] * matrix[10] - matrix[6] * matrix[9])
        - matrix[1] * (matrix[4] * matrix[10] - matrix[6] * matrix[8])
        + matrix[2] * (matrix[4] * matrix[9] - matrix[5] * matrix[8])
    )


def _normalized_spatial_determinant(matrix: tuple[float, ...]) -> float:
    basis_lengths = (
        math.hypot(matrix[0], matrix[4], matrix[8]),
        math.hypot(matrix[1], matrix[5], matrix[9]),
        math.hypot(matrix[2], matrix[6], matrix[10]),
    )
    if any(length <= 1.0e-15 for length in basis_lengths):
        raise ValueError("fixture placement matrix must be nonsingular.")
    return _matrix3_determinant(matrix) / math.prod(basis_lengths)


def _transform(matrix: tuple[float, ...], point_mm: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = point_mm
    return (
        matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
        matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
        matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
    )


def _anchor_max_residual(
    asset: FixtureAsset, matrix: tuple[float, ...], targets: list[tuple[float, float, float]]
) -> float:
    residuals = []
    for source, target in zip(asset.anchors_m, targets, strict=True):
        transformed = _transform(
            matrix, tuple(value * 1000.0 for value in source)
        )
        residuals.append(
            math.hypot(
                transformed[0] - target[0],
                transformed[2] - target[2],
            )
        )
    return max(residuals)


def _validate_anchor_residuals(
    asset: FixtureAsset, matrix: tuple[float, ...], targets: list[tuple[float, float, float]]
) -> None:
    residual = _anchor_max_residual(asset, matrix, targets)
    if residual > ANCHOR_TOLERANCE_M:
        raise ValueError(
            f"{asset.asset_id} anchor residual {residual:.9g} m exceeds "
            f"{ANCHOR_TOLERANCE_M:.9g} m."
        )


def _yaw(start: tuple[float, float], end: tuple[float, float]) -> float:
    if start == end:
        raise ValueError("fixture orientation connector has zero length.")
    return (
        round(
            math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))
            % 360.0,
            6,
        )
        % 360.0
    )


def _column_major(matrix: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(matrix[row * 4 + column] for column in range(4) for row in range(4))


def _matrix_sha256(matrix: tuple[float, ...]) -> str:
    values = _column_major(matrix)
    return hashlib.sha256(struct.pack("<16f", *values)).hexdigest()


def _load_and_validate_asset(asset: FixtureAsset) -> bytes:
    node = resources.files("fspm_optics").joinpath("resources", "viewer", *asset.resource_path.split("/"))
    if not node.is_file():
        raise FileNotFoundError(f"approved fixture asset is missing: {asset.resource_path}")
    data = node.read_bytes()
    if len(data) != asset.byte_size or hashlib.sha256(data).hexdigest() != asset.sha256:
        raise ValueError(f"approved fixture asset bytes are incompatible: {asset.asset_id}")
    validate_glb_content(data, asset)
    return data


def validate_glb_content(data: bytes, asset: FixtureAsset) -> dict[str, object]:
    """Reject unsafe or unsupported content even when registry hashes are updated."""

    if len(data) < 28:
        raise ValueError("fixture GLB is truncated.")
    magic, version, total_length = struct.unpack_from("<III", data)
    if magic != 0x46546C67 or version != 2 or total_length != len(data):
        raise ValueError("fixture GLB header is incompatible.")
    json_length, json_type = struct.unpack_from("<II", data, 12)
    if json_type != 0x4E4F534A or 20 + json_length + 8 > len(data):
        raise ValueError("fixture GLB JSON chunk is incompatible.")
    payload = _json_object(data[20 : 20 + json_length].rstrip(b" \x00"), "fixture GLB")
    binary_offset = 20 + json_length
    binary_length, binary_type = struct.unpack_from("<II", data, binary_offset)
    if binary_type != 0x004E4942 or binary_offset + 8 + binary_length != len(data):
        raise ValueError("fixture GLB binary chunk is incompatible.")
    extensions_used_value = payload.get("extensionsUsed", [])
    extensions_required_value = payload.get("extensionsRequired", [])
    if (
        not isinstance(extensions_used_value, list)
        or not all(isinstance(value, str) for value in extensions_used_value)
        or not isinstance(extensions_required_value, list)
        or not all(isinstance(value, str) for value in extensions_required_value)
    ):
        raise ValueError("fixture GLB extension declarations are malformed.")
    extensions_used = tuple(extensions_used_value)
    extensions_required = tuple(extensions_required_value)
    embedded_extensions = _embedded_extension_names(payload)
    if "KHR_lights_punctual" in embedded_extensions:
        raise ValueError("fixture GLB lights are prohibited.")
    if (
        any(value not in asset.approved_extensions for value in extensions_used)
        or any(value not in asset.approved_extensions for value in extensions_required)
        or any(value not in asset.approved_extensions for value in embedded_extensions)
    ):
        raise ValueError("fixture GLB uses an unapproved extension.")
    if payload.get("animations") or payload.get("cameras"):
        raise ValueError("fixture GLB animations or cameras are prohibited.")
    if _find_uri(payload):
        raise ValueError("fixture GLB external URIs are prohibited.")
    buffers = payload.get("buffers")
    if not isinstance(buffers, list) or len(buffers) != 1 or buffers[0] != {"byteLength": binary_length}:
        raise ValueError("fixture GLB must contain one declared embedded buffer.")
    materials = payload.get("materials")
    if not isinstance(materials, list) or not materials:
        raise ValueError("fixture GLB materials are missing.")
    alpha_modes = tuple(sorted({item.get("alphaMode", "OPAQUE") for item in materials if isinstance(item, dict)}))
    if alpha_modes != tuple(sorted(asset.material_alpha_modes)):
        raise ValueError("fixture GLB material alpha modes are incompatible.")
    if any(
        not isinstance(item, dict)
        or item.get("doubleSided") is not asset.materials_double_sided
        for item in materials
    ):
        raise ValueError("fixture GLB materials must preserve approved double-sided rendering.")
    alpha_factors = tuple(sorted({
        float(item.get("pbrMetallicRoughness", {}).get("baseColorFactor", [1, 1, 1, 1])[-1])
        for item in materials
        if isinstance(item, dict)
    }))
    if alpha_factors != tuple(sorted(asset.approved_base_color_alpha_factors)):
        raise ValueError("fixture GLB material alpha factors are incompatible.")
    return payload


def _find_uri(value: object) -> bool:
    if isinstance(value, dict):
        return "uri" in value or any(_find_uri(item) for item in value.values())
    if isinstance(value, list):
        return any(_find_uri(item) for item in value)
    return False


def _embedded_extension_names(value: object) -> frozenset[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "extensions":
                if not isinstance(item, dict) or not all(
                    isinstance(name, str) for name in item
                ):
                    raise ValueError("fixture GLB extension object is malformed.")
                names.update(item)
            names.update(_embedded_extension_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(_embedded_extension_names(item))
    return frozenset(names)


def _published_file(relative_path: str, data: bytes) -> FixturePublishedFile:
    return FixturePublishedFile(relative_path, data, hashlib.sha256(data).hexdigest())


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be finite.")
    return float(value)


def _hash_json(payload: object) -> str:
    data = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(dict(payload), indent=2, sort_keys=True) + "\n").encode("utf-8")


def _json_object(data: bytes, label: str) -> dict[str, object]:
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return payload


__all__ = [
    "ANCHOR_TOLERANCE_M",
    "APPROVED_GLB_EXTENSIONS",
    "ASSET_REGISTRY",
    "CATALOG_SCHEMA_ID",
    "CATALOG_SCHEMA_VERSION",
    "FixtureAsset",
    "FixturePublication",
    "FixtureTransportPlacement",
    "HPS_LOCAL_APERTURE_OUTWARD_NORMAL",
    "HPS_LOCAL_APERTURE_PLANE_Y_MM",
    "HPS_PLACEMENT_CONTRACT_SCHEMA_ID",
    "HPS_PLACEMENT_CONTRACT_SCHEMA_VERSION",
    "HpsPublicationTransformValidation",
    "MATRIX_STRIDE_BYTES",
    "build_fixture_publication",
    "hps_placement_contract_payload",
    "hps_placement_contract_sha256",
    "resolve_fixture_transport_placements",
    "validate_hps_publication_transform",
    "validate_standalone_proposed_transform",
    "validate_fixture_publication",
    "validate_glb_content",
]
