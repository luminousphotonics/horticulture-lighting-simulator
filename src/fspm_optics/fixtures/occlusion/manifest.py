"""Authenticated primitive classifications for GLB-derived fixture physics."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import resources
import json
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Final, Literal, Mapping

from fspm_optics.fixtures.structure_material import (
    FIXTURE_BODY_MATERIAL_ID,
    FIXTURE_BODY_MATERIAL_NAME,
    FIXTURE_BODY_MATERIAL_RAD,
)
from fspm_optics.viewer.fixtures import ASSET_REGISTRY, FixtureAsset

from .gltf import (
    DecodedFixtureGlb,
    FixtureGlbError,
    GlbPrimitiveInventory,
    decode_fixture_glb,
    node_inventory_payload,
)

OCCLUSION_MODEL_ID: Final = "fspm-optics.glb-fixture-occlusion"
OCCLUSION_MODEL_VERSION: Final = 4
CLASSIFICATION_MANIFEST_RESOURCE: Final = (
    "resources/data/fixture_occlusion/classification.v4.json"
)
PrimitiveClassification = Literal[
    "emitter",
    "external_occluder",
    "existing_optical_stack_duplicate",
    "decorative_exclusion",
]
ALLOWED_CLASSIFICATIONS: Final = frozenset(
    {
        "emitter",
        "external_occluder",
        "existing_optical_stack_duplicate",
        "decorative_exclusion",
    }
)


class FixtureClassificationError(FixtureGlbError):
    """Fixture GLB bytes or inventory do not match the reviewed manifest."""


@dataclass(frozen=True, slots=True)
class ClassifiedPrimitive:
    inventory: GlbPrimitiveInventory
    classification: PrimitiveClassification

    def to_payload(self) -> dict[str, object]:
        return self.inventory.to_payload() | {
            "classification": self.classification,
        }


@dataclass(frozen=True, slots=True)
class ProposedApertureRegistration:
    """Authenticated GLB plane registered to the calibrated aperture."""

    local_plane_y_mm: float
    local_cover_inner_y_mm: float
    cover_node_paths: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "reference_geometry": "opaque_bottom_cover",
            "light_side_selection": "maximum_authored_local_y",
            "local_plane_y_mm": self.local_plane_y_mm,
            "local_cover_inner_y_mm": self.local_cover_inner_y_mm,
            "cover_thickness_m": (
                self.local_plane_y_mm - self.local_cover_inner_y_mm
            )
            * 0.001,
            "local_outward_normal": [0.0, 1.0, 0.0],
            "scientific_outward_normal_after_viewer_transform": [
                0.0,
                0.0,
                -1.0,
            ],
            "source_boundary": (
                "calibrated completed-aperture plane replaces excluded GLB cover"
            ),
            "node_paths": list(self.cover_node_paths),
        }


@dataclass(frozen=True, slots=True)
class AuthenticatedFixtureAsset:
    asset: FixtureAsset
    decoded: DecodedFixtureGlb
    primitives: tuple[ClassifiedPrimitive, ...]
    inventory_sha256: str
    classification_sha256: str
    proposed_aperture_registration: ProposedApertureRegistration | None

    @property
    def external_primitives(self) -> tuple[ClassifiedPrimitive, ...]:
        return tuple(
            item
            for item in self.primitives
            if item.classification == "external_occluder"
        )

    @property
    def emitter_primitives(self) -> tuple[ClassifiedPrimitive, ...]:
        return tuple(
            item for item in self.primitives if item.classification == "emitter"
        )

    @property
    def classification_counts(self) -> dict[str, int]:
        return {
            name: sum(item.classification == name for item in self.primitives)
            for name in sorted(ALLOWED_CLASSIFICATIONS)
        }


@lru_cache(maxsize=1)
def load_classification_manifest() -> Mapping[str, Any]:
    node = resources.files("fspm_optics").joinpath(
        *CLASSIFICATION_MANIFEST_RESOURCE.split("/")
    )
    if not node.is_file():
        raise FixtureClassificationError(
            "fixture classification manifest is missing."
        )
    try:
        payload = json.loads(node.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureClassificationError(
            "fixture classification manifest is invalid JSON."
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_id") != OCCLUSION_MODEL_ID
        or payload.get("schema_version") != OCCLUSION_MODEL_VERSION
        or payload.get("occlusion_model_version")
        != f"glb-fixture-occlusion-v{OCCLUSION_MODEL_VERSION}"
        or payload.get("classification_categories")
        != sorted(ALLOWED_CLASSIFICATIONS)
        or payload.get("external_material")
        != {
            "material_id": FIXTURE_BODY_MATERIAL_ID,
            "radiance_identifier": FIXTURE_BODY_MATERIAL_NAME,
            "radiance_primitive": "metal",
            "reflectance_rgb": [0.7, 0.7, 0.7],
            "specularity": 0.9,
            "roughness": 0.1,
            "transport_role": (
                "shared_reflective_external_fixture_structure"
            ),
            "reference_assumption": (
                "generic anodized extruded aluminum; not a product-specific "
                "material claim"
            ),
            "evidence": "completed_fixture_body_optics_audit_70pct_metal_case",
            "shared_systems": ["proposed", "conventional", "hps"],
        }
    ):
        raise FixtureClassificationError(
            "fixture classification manifest header or material policy is incompatible."
        )
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise FixtureClassificationError(
            "fixture classification manifest asset inventory is missing."
        )
    return MappingProxyType(payload)


@lru_cache(maxsize=16)
def load_authenticated_fixture_asset(asset_id: str) -> AuthenticatedFixtureAsset:
    """Load one reviewed Proposed/Conventional/HPS GLB or fail closed."""

    asset_by_id = {
        item.asset_id: item
        for item in ASSET_REGISTRY
        if item.system_id in {"proposed", "conventional", "hps"}
    }
    asset = asset_by_id.get(asset_id)
    if asset is None:
        raise FixtureClassificationError(
            f"fixture asset is outside the occlusion model: {asset_id}"
        )
    manifest = load_classification_manifest()
    raw_assets = manifest["assets"]
    records = [
        item
        for item in raw_assets
        if isinstance(item, dict) and item.get("asset_id") == asset_id
    ]
    if len(records) != 1:
        raise FixtureClassificationError(
            f"fixture classification record is missing or duplicated: {asset_id}"
        )
    record = records[0]
    data = _asset_bytes(asset)
    if (
        len(data) != asset.byte_size
        or hashlib.sha256(data).hexdigest() != asset.sha256
        or record.get("system_id") != asset.system_id
        or record.get("fixture_type") != asset.fixture_type
        or record.get("resource_path") != asset.resource_path
        or record.get("glb_byte_size") != asset.byte_size
        or record.get("glb_sha256") != asset.sha256
    ):
        raise FixtureClassificationError(
            f"fixture GLB bytes or registry identity changed: {asset_id}"
        )
    decoded = decode_fixture_glb(data)
    inventory_payload = node_inventory_payload(decoded)
    inventory_sha256 = _hash_payload(inventory_payload)
    if record.get("node_primitive_inventory_sha256") != inventory_sha256:
        raise FixtureClassificationError(
            f"fixture node/primitive inventory changed: {asset_id}"
        )
    raw_classifications = record.get("classifications")
    if not isinstance(raw_classifications, list):
        raise FixtureClassificationError(
            f"fixture classifications are missing: {asset_id}"
        )
    by_key: dict[tuple[int, int], Mapping[str, Any]] = {}
    for raw in raw_classifications:
        if not isinstance(raw, dict):
            raise FixtureClassificationError(
                f"fixture classification entry is malformed: {asset_id}"
            )
        try:
            key = (int(raw["node_index"]), int(raw["primitive_index"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise FixtureClassificationError(
                f"fixture classification key is malformed: {asset_id}"
            ) from exc
        if key in by_key:
            raise FixtureClassificationError(
                f"fixture classification key is duplicated: {asset_id} {key}"
            )
        by_key[key] = raw
    expected_keys = {
        (item.node_index, item.primitive_index)
        for item in decoded.primitive_inventory
    }
    if set(by_key) != expected_keys:
        raise FixtureClassificationError(
            f"fixture classifications do not cover the exact inventory: {asset_id}"
        )
    classified: list[ClassifiedPrimitive] = []
    for primitive in decoded.primitive_inventory:
        raw = by_key[(primitive.node_index, primitive.primitive_index)]
        classification = raw.get("classification")
        expected = primitive.to_payload()
        actual = {
            name: raw.get(name)
            for name in expected
        }
        if actual != expected or classification not in ALLOWED_CLASSIFICATIONS:
            raise FixtureClassificationError(
                f"fixture classification no longer matches its primitive: "
                f"{asset_id} node {primitive.node_index}"
            )
        classified.append(
            ClassifiedPrimitive(
                primitive,
                classification,  # type: ignore[arg-type]
            )
        )
    classification_payload = [
        item.to_payload() for item in classified
    ]
    classification_sha256 = _hash_payload(classification_payload)
    if (
        record.get("classification_sha256") != classification_sha256
        or record.get("primitive_count") != len(classified)
    ):
        raise FixtureClassificationError(
            f"fixture classification hash changed: {asset_id}"
        )
    registration = _proposed_aperture_registration(
        asset,
        decoded,
        tuple(classified),
    )
    _validate_hps_classifications(asset, tuple(classified))
    return AuthenticatedFixtureAsset(
        asset=asset,
        decoded=decoded,
        primitives=tuple(classified),
        inventory_sha256=inventory_sha256,
        classification_sha256=classification_sha256,
        proposed_aperture_registration=registration,
    )


def validate_complete_classification_manifest() -> tuple[AuthenticatedFixtureAsset, ...]:
    expected = tuple(
        item.asset_id
        for item in ASSET_REGISTRY
        if item.system_id in {"proposed", "conventional", "hps"}
    )
    manifest = load_classification_manifest()
    actual = tuple(
        item.get("asset_id")
        for item in manifest["assets"]
        if isinstance(item, dict)
    )
    if actual != expected:
        raise FixtureClassificationError(
            "fixture classification asset order or coverage is incompatible."
        )
    return tuple(load_authenticated_fixture_asset(asset_id) for asset_id in expected)


def classification_manifest_sha256() -> str:
    node = resources.files("fspm_optics").joinpath(
        *CLASSIFICATION_MANIFEST_RESOURCE.split("/")
    )
    return hashlib.sha256(node.read_bytes()).hexdigest()


def _proposed_aperture_registration(
    asset: FixtureAsset,
    decoded: DecodedFixtureGlb,
    primitives: tuple[ClassifiedPrimitive, ...],
) -> ProposedApertureRegistration | None:
    if asset.system_id != "proposed":
        return None
    covers = tuple(
        item
        for item in primitives
        if "opaque_bottom_cover" in item.inventory.node_path
    )
    if (
        len(covers) != len(asset.anchors_m)
        or any(
            item.classification != "existing_optical_stack_duplicate"
            for item in covers
        )
        or any(
            item.classification == "existing_optical_stack_duplicate"
            and "opaque_bottom_cover" not in item.inventory.node_path
            for item in primitives
        )
        or any(
            "heatsink_base_plate" in item.inventory.node_path
            and item.classification != "external_occluder"
            for item in primitives
        )
    ):
        raise FixtureClassificationError(
            f"Proposed aperture/body classifications are incompatible: {asset.asset_id}"
        )
    planes: list[float] = []
    inner_planes: list[float] = []
    for item in covers:
        points = tuple(
            point
            for triangle in decoded.triangles(item.inventory)
            for point in triangle
        )
        planes.append(max(point[1] for point in points))
        inner_planes.append(min(point[1] for point in points))
    plane = max(planes)
    inner = min(inner_planes)
    if (
        max(planes) - min(planes) > 1.0e-9
        or max(inner_planes) - min(inner_planes) > 1.0e-9
        or abs(asset.placement_plane_local_y_mm - plane) > 5.0e-7
        or asset.pivot_contract
        != "opaque_bottom_cover_light_side_is_aperture_plane"
    ):
        raise FixtureClassificationError(
            f"Proposed GLB aperture registration changed: {asset.asset_id}"
        )
    return ProposedApertureRegistration(
        local_plane_y_mm=plane,
        local_cover_inner_y_mm=inner,
        cover_node_paths=tuple(
            item.inventory.node_path for item in covers
        ),
    )


def _validate_hps_classifications(
    asset: FixtureAsset,
    primitives: tuple[ClassifiedPrimitive, ...],
) -> None:
    if asset.system_id != "hps":
        return
    expected = {
        "competitor_hps_1000w/reflector_hood/reflector_hood_top_box": (
            "external_occluder"
        ),
        "competitor_hps_1000w/reflector_hood/reflector_hood_flared_skirt": (
            "external_occluder"
        ),
        "competitor_hps_1000w/exhaust_flange": "external_occluder",
        "competitor_hps_1000w/socket_bracket_assembly/socket_bracket": (
            "external_occluder"
        ),
        "competitor_hps_1000w/socket_bracket_assembly/ceramic_socket": (
            "external_occluder"
        ),
        "competitor_hps_1000w/hps_bulb_base": "external_occluder",
        "competitor_hps_1000w/hps_bulb_glass": (
            "existing_optical_stack_duplicate"
        ),
        "competitor_hps_1000w/hps_inner_arc_tube": "emitter",
    }
    actual = {
        item.inventory.node_path: item.classification for item in primitives
    }
    if (
        asset.asset_id != "hps-housing-v3"
        or asset.fixture_type != "hps_1000w_fixture"
        or actual != expected
    ):
        raise FixtureClassificationError(
            "HPS primitive classifications are incompatible."
        )


def _asset_bytes(asset: FixtureAsset) -> bytes:
    node = resources.files("fspm_optics").joinpath(
        "resources", "viewer", *asset.resource_path.split("/")
    )
    if not node.is_file():
        raise FixtureClassificationError(
            f"authenticated fixture GLB is missing: {asset.resource_path}"
        )
    return node.read_bytes()


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


__all__ = [
    "ALLOWED_CLASSIFICATIONS",
    "AuthenticatedFixtureAsset",
    "CLASSIFICATION_MANIFEST_RESOURCE",
    "ClassifiedPrimitive",
    "FIXTURE_BODY_MATERIAL_ID",
    "FIXTURE_BODY_MATERIAL_NAME",
    "FIXTURE_BODY_MATERIAL_RAD",
    "FixtureClassificationError",
    "OCCLUSION_MODEL_ID",
    "OCCLUSION_MODEL_VERSION",
    "PrimitiveClassification",
    "ProposedApertureRegistration",
    "classification_manifest_sha256",
    "load_authenticated_fixture_asset",
    "load_classification_manifest",
    "validate_complete_classification_manifest",
]
