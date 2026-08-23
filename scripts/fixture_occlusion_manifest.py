#!/usr/bin/env python3
"""Regenerate or verify the explicit GLB fixture-physics classification manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from fspm_optics.fixtures.occlusion.gltf import (  # noqa: E402
    decode_fixture_glb,
    node_inventory_payload,
)
from fspm_optics.fixtures.occlusion.manifest import (  # noqa: E402
    ALLOWED_CLASSIFICATIONS,
    FIXTURE_BODY_MATERIAL_ID,
    FIXTURE_BODY_MATERIAL_NAME,
    CLASSIFICATION_MANIFEST_RESOURCE,
    OCCLUSION_MODEL_ID,
    OCCLUSION_MODEL_VERSION,
)
from fspm_optics.viewer.fixtures import ASSET_REGISTRY  # noqa: E402

MANIFEST_PATH = (
    SOURCE_ROOT
    / "fspm_optics"
    / CLASSIFICATION_MANIFEST_RESOURCE
)


def classification_for(system_id: str, node_path: str) -> str:
    """Return the reviewed category written explicitly for every primitive."""

    lowered = node_path.lower()
    if system_id == "hps":
        hps_classifications = {
            "competitor_hps_1000w/hps_bulb_glass": (
                "existing_optical_stack_duplicate"
            ),
            "competitor_hps_1000w/hps_inner_arc_tube": "emitter",
        }
        return hps_classifications.get(node_path, "external_occluder")
    if system_id == "conventional":
        return "emitter" if "/led_light_face_" in lowered else "external_occluder"
    if "opaque_bottom_cover" in lowered:
        return "existing_optical_stack_duplicate"
    if "spec_label" in lowered or "logo_plate" in lowered:
        return "decorative_exclusion"
    return "external_occluder"


def build_manifest() -> dict[str, object]:
    records: list[dict[str, object]] = []
    for asset in ASSET_REGISTRY:
        if asset.system_id not in {"proposed", "conventional", "hps"}:
            continue
        path = (
            SOURCE_ROOT
            / "fspm_optics"
            / "resources"
            / "viewer"
            / asset.resource_path
        )
        data = path.read_bytes()
        decoded = decode_fixture_glb(data)
        classifications = [
            primitive.to_payload()
            | {
                "classification": classification_for(
                    asset.system_id, primitive.node_path
                )
            }
            for primitive in decoded.primitive_inventory
        ]
        records.append(
            {
                "asset_id": asset.asset_id,
                "system_id": asset.system_id,
                "fixture_type": asset.fixture_type,
                "resource_path": asset.resource_path,
                "glb_byte_size": len(data),
                "glb_sha256": hashlib.sha256(data).hexdigest(),
                "node_primitive_inventory_sha256": _hash(
                    node_inventory_payload(decoded)
                ),
                "primitive_count": len(classifications),
                "classification_sha256": _hash(classifications),
                "classifications": classifications,
            }
        )
    return {
        "schema_id": OCCLUSION_MODEL_ID,
        "schema_version": OCCLUSION_MODEL_VERSION,
        "occlusion_model_version": (
            f"glb-fixture-occlusion-v{OCCLUSION_MODEL_VERSION}"
        ),
        "classification_categories": sorted(ALLOWED_CLASSIFICATIONS),
        "classification_policy": {
            "emitter": (
                "GLB geometry representing a luminous component; excluded "
                "from opaque body geometry and never substituted for or used "
                "to derive an independently authenticated scientific source"
            ),
            "external_occluder": (
                "physical fixture structure outside the independently "
                "authenticated scientific source model; included as opaque "
                "reflective geometry"
            ),
            "existing_optical_stack_duplicate": (
                "GLB optical or enclosure geometry already represented by a "
                "separately authenticated source/optical boundary, or lacking "
                "authenticated scientific transport coefficients; excluded "
                "to prevent duplicate or unsupported transport"
            ),
            "decorative_exclusion": (
                "label/logo-only surface with no independent structural volume; "
                "excluded while its supporting body remains"
            ),
        },
        "external_material": {
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
        },
        "assets": records,
    }


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write",
        action="store_true",
        help="replace the checked-in manifest with the reviewed classification",
    )
    arguments = parser.parse_args()
    text = json.dumps(build_manifest(), indent=2, sort_keys=True) + "\n"
    if arguments.write:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(text, encoding="utf-8")
        print(MANIFEST_PATH)
        return 0
    if not MANIFEST_PATH.is_file() or MANIFEST_PATH.read_text(
        encoding="utf-8"
    ) != text:
        print(
            "fixture occlusion manifest differs; review GLB classifications "
            "before rerunning with --write",
            file=sys.stderr,
        )
        return 1
    print(f"fixture occlusion manifest verified: {_hash(json.loads(text))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
