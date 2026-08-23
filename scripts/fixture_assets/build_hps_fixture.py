#!/usr/bin/env python3
"""Build and authenticate the production HPS housing STEP/GLBs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import struct
import subprocess
import sys
from typing import Any, Iterable

from build123d import Axis, export_step, import_step
import OCP
from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
from OCP.gce import gce_MakeLin

SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

import hps_1000w_fixture as cad  # noqa: E402
from fspm_optics.fixtures.occlusion.gltf import (  # noqa: E402
    DecodedFixtureGlb,
    decode_fixture_glb,
    node_inventory_payload,
)

SUPPLIED_SOURCE_SHA256 = (
    "5bc9ca6670440a3d39e331063930b85eb6c67b9ab5c52569e30f7ee4a91596c4"
)
ASSET_ID = "hps-housing-v3"
CLASSIFICATION_MANIFEST_SHA256 = (
    "bdbd6c4ca708bcd446a0535490a642c56a321a8d1f7ef4122fee54a4d15eab7e"
)
CLASSIFICATION_MANIFEST_BYTES = 692_248
LINEAR_TOLERANCE_MM = 0.10
ANGULAR_TOLERANCE_RAD = 0.25
CAD_BOUNDS = (
    (-472.04394483199064, -321.3100001, -248.92000010000007),
    (422.9100001, 321.3100001, 0.0),
)
GLTF_BOUNDS = (
    (-471.9875183105469, -248.9199981689453, -321.30999755859375),
    (422.9100036621094, 0.0, 321.30999755859375),
)
HOOD_BOUNDS = (
    (-422.9100001, -321.3100001, -248.92000010000007),
    (422.9100001, 321.3100001, 0.0),
)
TOP_BOX_BOUNDS = ((-355.6, -165.0, -78.0), (355.6, 165.0, 0.0))
TOP_BOX_VOLUME_MM3 = 18_306_288.0
FLANGE_BOUNDS = (
    (-472.04394483199064, -81.2, -237.29161050604375),
    (-353.8901797325293, 81.2, -67.02154895432216),
)
REAR_WALL_CENTER_CAD_MM = (-389.255, 0.0, -163.46)
REAR_OUTWARD_NORMAL_CAD = (
    -0.9304494532673857,
    0.0,
    0.3664202708836165,
)
EXPECTED_HIERARCHY = {
    "competitor_hps_1000w": {
        "reflector_hood": [
            "reflector_hood_top_box",
            "reflector_hood_flared_skirt",
        ],
        "exhaust_flange": None,
        "socket_bracket_assembly": ["socket_bracket", "ceramic_socket"],
        "hps_bulb_base": None,
        "hps_bulb_glass": None,
        "hps_inner_arc_tube": None,
    }
}
EXPECTED_LEAVES = (
    "reflector_hood_top_box",
    "reflector_hood_flared_skirt",
    "exhaust_flange",
    "socket_bracket",
    "ceramic_socket",
    "hps_bulb_base",
    "hps_bulb_glass",
    "hps_inner_arc_tube",
)
EXPECTED_PATHS = (
    "competitor_hps_1000w/reflector_hood/reflector_hood_top_box",
    "competitor_hps_1000w/reflector_hood/reflector_hood_flared_skirt",
    "competitor_hps_1000w/exhaust_flange",
    "competitor_hps_1000w/socket_bracket_assembly/socket_bracket",
    "competitor_hps_1000w/socket_bracket_assembly/ceramic_socket",
    "competitor_hps_1000w/hps_bulb_base",
    "competitor_hps_1000w/hps_bulb_glass",
    "competitor_hps_1000w/hps_inner_arc_tube",
)
EXPECTED_ZERO_AREA_TESSELLATION_TRIANGLES = {"hps_bulb_glass": 4}
RAY_ORIGIN_CAD_MM = (0.0, 0.0, -138.0)
REAR_BORE_AXIS_ORIGIN_CAD_MM = tuple(
    REAR_WALL_CENTER_CAD_MM[index]
    - 150.0 * REAR_OUTWARD_NORMAL_CAD[index]
    for index in range(3)
)


def _direction_to(point: tuple[float, float, float]) -> tuple[float, float, float]:
    vector = tuple(point[index] - RAY_ORIGIN_CAD_MM[index] for index in range(3))
    length = math.sqrt(math.fsum(value * value for value in vector))
    return tuple(value / length for value in vector)  # type: ignore[return-value]


HOOD_RAY_SPECS = {
    "sealed_roof_straight_up": (RAY_ORIGIN_CAD_MM, (0.0, 0.0, 1.0), True),
    "sealed_roof_upward_positive_y": (RAY_ORIGIN_CAD_MM, (0.0, 1.0, 1.0), True),
    "sealed_roof_upward_negative_y": (RAY_ORIGIN_CAD_MM, (0.0, -1.0, 1.0), True),
    "sealed_roof_upward_positive_x": (RAY_ORIGIN_CAD_MM, (1.0, 0.0, 1.0), True),
    "closed_positive_y_side": (RAY_ORIGIN_CAD_MM, (0.0, 1.0, 0.0), True),
    "closed_negative_y_side": (RAY_ORIGIN_CAD_MM, (0.0, -1.0, 0.0), True),
    "horizontal_positive_x": (RAY_ORIGIN_CAD_MM, (1.0, 0.0, 0.0), True),
    "rear_outside_bore_positive_y": (
        RAY_ORIGIN_CAD_MM,
        _direction_to((REAR_WALL_CENTER_CAD_MM[0], 120.0, REAR_WALL_CENTER_CAD_MM[2])),
        True,
    ),
    "rear_outside_bore_negative_y": (
        RAY_ORIGIN_CAD_MM,
        _direction_to((REAR_WALL_CENTER_CAD_MM[0], -120.0, REAR_WALL_CENTER_CAD_MM[2])),
        True,
    ),
    "rear_exhaust_bore_axis": (
        REAR_BORE_AXIS_ORIGIN_CAD_MM,
        REAR_OUTWARD_NORMAL_CAD,
        False,
    ),
    "luminous_axis_to_rear_exhaust": (
        RAY_ORIGIN_CAD_MM,
        _direction_to(REAR_WALL_CENTER_CAD_MM),
        False,
    ),
    "optical_opening_straight_down": (RAY_ORIGIN_CAD_MM, (0.0, 0.0, -1.0), False),
}


def main() -> None:
    arguments = _parser().parse_args()
    gltfpack = arguments.gltfpack.expanduser().resolve()
    if not gltfpack.is_file():
        raise SystemExit(f"gltfpack executable is missing: {gltfpack}")
    version = subprocess.run(
        [str(gltfpack), "-v"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if version != "gltfpack 0.20":
        raise SystemExit(f"gltfpack must be pinned to 0.20; received {version!r}.")

    supplied = arguments.supplied_source.expanduser().resolve()
    repository_source = SCRIPT_DIR / "hps_1000w_fixture.py"
    supplied_hash = _sha256_file(supplied)
    repository_hash = _sha256_file(repository_source)
    if supplied_hash != SUPPLIED_SOURCE_SHA256:
        raise SystemExit("supplied hps_1000w_fixture.py failed SHA-256 authentication.")
    if repository_hash != SUPPLIED_SOURCE_SHA256:
        raise SystemExit("repository hps_1000w_fixture.py failed SHA-256 authentication.")
    if supplied.read_bytes() != repository_source.read_bytes():
        raise SystemExit("supplied and repository HPS sources differ byte-for-byte.")
    classification_manifest = (
        REPOSITORY_ROOT
        / "src/fspm_optics/resources/data/fixture_occlusion/classification.v3.json"
    )
    if (
        classification_manifest.stat().st_size != CLASSIFICATION_MANIFEST_BYTES
        or _sha256_file(classification_manifest) != CLASSIFICATION_MANIFEST_SHA256
    ):
        raise SystemExit("fixture-occlusion classification.v3.json changed unexpectedly.")

    output = arguments.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    final_glb = arguments.final_glb.expanduser().resolve()
    final_glb.parent.mkdir(parents=True, exist_ok=True)
    step_path = output / "hps_1000w_fixture.step"
    raw_glb = output / "hps_1000w_fixture.raw.glb"
    provenance_path = output / "hps_1000w_fixture.provenance.json"

    assembly = cad.make_hps_assembly()
    mechanical = validate_cad_assembly(assembly)
    _clear_step_presentation(assembly)
    export_step(assembly, step_path)
    _normalize_step_header(step_path)
    roundtrip = import_step(step_path)
    roundtrip_mechanical = validate_cad_assembly(roundtrip)
    if _hierarchy(assembly) != _hierarchy(roundtrip):
        raise ValueError("STEP round-trip changed the named component hierarchy.")

    write_raw_glb(roundtrip, raw_glb)
    raw_validation = validate_glb(
        raw_glb,
        expected_generator="fspm-optics OpenCascade HPS named-leaf GLB writer v1",
    )
    command = (
        str(gltfpack), "-i", str(raw_glb), "-o", str(final_glb),
        "-kn", "-km", "-ke", "-noq",
    )
    subprocess.run(command, check=True)
    packed_validation = validate_glb(final_glb, expected_generator=None)
    agreement = compare_raw_packed(raw_validation, packed_validation)

    provenance = {
        "schema_id": "fspm-optics.hps-housing-asset-provenance",
        "schema_version": 1,
        "asset_id": ASSET_ID,
        "fixture_type": "hps_1000w_fixture",
        "supersession": {
            "supersedes_active_asset_id": "hps-housing-v2",
            "reason": "sealed-roof and negative-X rear-exhaust correction",
            "historical_v2_identity_reused": False,
        },
        "source": {
            "supplied_path": str(supplied),
            "supplied_sha256": supplied_hash,
            "repository_source": "scripts/fixture_assets/hps_1000w_fixture.py",
            "repository_source_sha256": repository_hash,
            "byte_for_byte_equal": True,
        },
        "toolchain": {
            "python": sys.version.split()[0],
            "build123d": importlib.metadata.version("build123d"),
            "opencascade": OCP.__version__,
            "gltfpack": version,
            "blender_used": False,
        },
        "commands": {
            "build_argv": [
                sys.executable,
                "scripts/fixture_assets/build_hps_fixture.py",
                "--gltfpack",
                str(gltfpack),
            ],
            "pack_argv": list(command),
        },
        "step_export": {
            "named_assembly_hierarchy": True,
            "presentation_colors_omitted_for_determinism": True,
            "normalized_header_timestamp": "1970-01-01T00:00:00",
            "trailing_horizontal_whitespace_removed": True,
            "viewer_materials_authored_in_glb_only": True,
        },
        "tessellation": {
            "engine": "OpenCascade via Build123d import_step/tessellate",
            "linear_tolerance_mm": LINEAR_TOLERANCE_MM,
            "angular_tolerance_rad": ANGULAR_TOLERANCE_RAD,
            "normals_policy": "explicit flat per-face normals from triangle winding",
            "zero_area_triangle_policy": (
                "omit only the four authenticated exact-zero-area triangles emitted "
                "at the fused HPS glass seam"
            ),
            "omitted_zero_area_triangles": {"hps_bulb_glass": 4},
        },
        "coordinate_contract": {
            "cad_units": "millimetres",
            "gltf_units": "millimetres",
            "meters_per_asset_unit": 0.001,
            "cad_to_gltf": "(X,Y,Z)->(X,Z,-Y)",
            "cad_bounds_mm": [list(CAD_BOUNDS[0]), list(CAD_BOUNDS[1])],
            "gltf_bounds_mm": [list(GLTF_BOUNDS[0]), list(GLTF_BOUNDS[1])],
            "local_aperture_placement_plane_y_mm": -248.92,
            "placement_correction_local_xyz_mm": [0.0, 248.92, 0.0],
            "dimension_correction_scale_xyz": [1.0, 1.0, 1.0],
            "registered_gltf_up_axis": "+Y",
            "auto_center": False,
            "auto_fit": False,
            "simplification": False,
            "draco": False,
            "shear": False,
            "browser_geometry_correction": False,
        },
        "rear_exhaust_contract": {
            "former_vertical_roof_bore_removed": True,
            "sealed_top_box_volume_mm3": TOP_BOX_VOLUME_MM3,
            "rear_wall_center_cad_mm": list(REAR_WALL_CENTER_CAD_MM),
            "rear_outward_unit_normal_cad": list(REAR_OUTWARD_NORMAL_CAD),
            "single_exhaust_location": "negative-X flared end wall",
            "collar_axis_normal_to_sloped_panel": True,
            "intended_downward_optical_opening_preserved": True,
        },
        "scientific_boundary": {
            "physical_housing_dimensions_are_display_only": True,
            "luminous_aperture_m": [0.798576, 0.603504, 0.0],
            "housing_fitted_to_luminous_aperture": False,
            "viewer_pbr_materials_are_scientific_transport_coefficients": False,
            "hps_fixture_occlusion_enabled": False,
            "classification_manifest_changed": False,
            "classification_manifest_sha256": CLASSIFICATION_MANIFEST_SHA256,
            "classification_manifest_byte_length": CLASSIFICATION_MANIFEST_BYTES,
        },
        "hashes": {
            "step_sha256": _sha256_file(step_path),
            "raw_glb_sha256": _sha256_file(raw_glb),
            "packed_glb_sha256": _sha256_file(final_glb),
            "raw_inventory_sha256": raw_validation["inventory_sha256"],
            "packed_inventory_sha256": packed_validation["inventory_sha256"],
        },
        "byte_lengths": {
            "step": step_path.stat().st_size,
            "raw_glb": raw_glb.stat().st_size,
            "packed_glb": final_glb.stat().st_size,
        },
        "mechanical_assertions": mechanical,
        "step_roundtrip_assertions": roundtrip_mechanical,
        "raw_validation": raw_validation,
        "packed_validation": packed_validation,
        "raw_packed_agreement": agreement,
        "classification_next_phase": {
            "asset_id": ASSET_ID,
            "inventory_sha256": packed_validation["inventory_sha256"],
            "node_primitive_inventory": packed_validation["node_primitive_inventory"],
            "document_inventory": packed_validation["document_inventory"],
            "primitive_inventory": packed_validation["primitive_inventory"],
            "hps_fixture_occlusion_enabled": False,
        },
    }
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(provenance, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gltfpack", required=True, type=Path)
    parser.add_argument(
        "--supplied-source", type=Path,
        default=Path("/home/austin/Desktop/build123d-modeling/models/hps_1000w_fixture.py"),
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=REPOSITORY_ROOT / "src/fspm_optics/resources/cad/hps",
    )
    parser.add_argument(
        "--final-glb", type=Path,
        default=REPOSITORY_ROOT / "src/fspm_optics/resources/viewer/fixtures/hps/hps.glb",
    )
    return parser


def validate_cad_assembly(assembly: Any) -> dict[str, object]:
    if _hierarchy(assembly) != EXPECTED_HIERARCHY:
        raise ValueError("constructed HPS hierarchy, names, or component order changed.")
    leaves = _leaves(assembly)
    names = tuple(item.label for item in leaves)
    if names != EXPECTED_LEAVES or len(set(names)) != 8:
        raise ValueError("HPS assembly must contain eight ordered unique named leaves.")
    if len(assembly.solids()) != 8:
        raise ValueError("HPS assembly must contain exactly eight solids.")
    leaf_solid_counts: dict[str, int] = {}
    leaf_volumes_mm3: dict[str, float] = {}
    for leaf in leaves:
        solid_count = len(leaf.solids())
        leaf_solid_counts[leaf.label] = solid_count
        leaf_volumes_mm3[leaf.label] = float(leaf.volume)
        if (
            not leaf.is_valid or solid_count != 1
            or not math.isfinite(float(leaf.volume)) or float(leaf.volume) <= 0.0
        ):
            raise ValueError(f"invalid constructed HPS solid: {leaf.label}")
    top_box, skirt, flange = leaves[:3]
    if skirt.label != "reflector_hood_flared_skirt" or len(skirt.solids()) != 1 or not skirt.is_valid:
        raise ValueError("corrected reflector skirt is not one valid solid.")
    if flange.label != "exhaust_flange" or len(flange.solids()) != 1 or not flange.is_valid:
        raise ValueError("rear exhaust flange is not one valid solid.")
    actual_bounds = _shape_bounds(assembly)
    _assert_bounds(actual_bounds, CAD_BOUNDS, 2.0e-6, "CAD assembly")
    hood = assembly.children[0]
    hood_bounds = _shape_bounds(hood)
    _assert_bounds(hood_bounds, HOOD_BOUNDS, 2.0e-6, "reflector hood")
    top_box_bounds = _shape_bounds(top_box)
    _assert_bounds(top_box_bounds, TOP_BOX_BOUNDS, 2.0e-6, "sealed top box")
    if not math.isclose(
        float(top_box.volume), TOP_BOX_VOLUME_MM3, rel_tol=0.0, abs_tol=1.0e-6
    ):
        raise ValueError("sealed top-box volume proves the former roof bore remains absent.")
    flange_bounds = _shape_bounds(flange)
    _assert_bounds(flange_bounds, FLANGE_BOUNDS, 2.0e-6, "rear exhaust flange")
    rear_center, rear_normal = cad.rear_exhaust_geometry()
    _assert_vector(rear_center, REAR_WALL_CENTER_CAD_MM, 1.0e-12, "rear wall center")
    _assert_vector(rear_normal, REAR_OUTWARD_NORMAL_CAD, 1.0e-12, "rear wall normal")
    if (
        rear_center[0] >= 0.0
        or rear_normal[0] >= -0.9
        or rear_normal[2] <= 0.0
        or rear_center[2] >= cad.REFLECTOR_TOP_Z
    ):
        raise ValueError("rear exhaust location or outward orientation changed.")
    hood_ray_results = _validate_cad_rays(hood)
    complete_ray_results = _validate_complete_cad_rays(leaves)
    return {
        "bounds_cad_mm": [list(actual_bounds[0]), list(actual_bounds[1])],
        "dimensions_mm": [
            actual_bounds[1][axis] - actual_bounds[0][axis] for axis in range(3)
        ],
        "reflector_hood_bounds_cad_mm": [list(hood_bounds[0]), list(hood_bounds[1])],
        "solid_count": 8,
        "unique_named_leaf_count": 8,
        "leaf_solid_counts": leaf_solid_counts,
        "leaf_volumes_mm3": leaf_volumes_mm3,
        "sealed_top_box": {
            "bounds_cad_mm": [list(top_box_bounds[0]), list(top_box_bounds[1])],
            "volume_mm3": float(top_box.volume),
            "unperforated_single_valid_solid": True,
            "former_vertical_roof_bore_absent": True,
        },
        "rear_exhaust": {
            "wall_center_cad_mm": list(rear_center),
            "outward_unit_normal_cad": list(rear_normal),
            "flange_bounds_cad_mm": [list(flange_bounds[0]), list(flange_bounds[1])],
            "outer_diameter_mm": cad.EXHAUST_OD,
            "inner_bore_diameter_mm": 2.0 * (cad.EXHAUST_OD / 2.0 - cad.EXHAUST_WALL),
            "wall_thickness_mm": cad.EXHAUST_WALL,
            "rear_wall_center_below_roof_seam": True,
            "negative_x_end_only": True,
            "flange_one_valid_positive_volume_solid": True,
        },
        "reflector_skirt_one_valid_solid": True,
        "complete_named_hierarchy": True,
        "finite_positive_volume_solids": True,
        "hood_opening_ray_assertions": hood_ray_results,
        "complete_assembly_ray_assertions": complete_ray_results,
    }


def _validate_cad_rays(hood: Any) -> dict[str, object]:
    results: dict[str, object] = {}
    for name, (origin, direction, expected_hit) in HOOD_RAY_SPECS.items():
        distances = _cad_ray_distances(hood, origin, direction)
        if bool(distances) is not expected_hit:
            outcome = "hit" if distances else "miss"
            raise ValueError(f"CAD hood ray {name} produced unexpected {outcome}.")
        results[name] = {
            "origin_cad_mm": list(origin),
            "direction_cad": list(direction),
            "expected": "hit" if expected_hit else "miss",
            "hit": bool(distances),
            **({} if not distances else {"first_distance_mm": distances[0]}),
        }
    return results


def _validate_complete_cad_rays(leaves: list[Any]) -> dict[str, object]:
    direction = _direction_to(REAR_WALL_CENTER_CAD_MM)
    hits = {
        leaf.label: _cad_ray_distances(leaf, RAY_ORIGIN_CAD_MM, direction)
        for leaf in leaves
    }
    required_internal = ("socket_bracket", "ceramic_socket", "hps_bulb_base")
    if any(not hits[name] for name in required_internal):
        raise ValueError(
            "bulb base and socket must remain between the luminous axis and rear exhaust."
        )
    if hits["reflector_hood_top_box"] or hits["reflector_hood_flared_skirt"] or hits["exhaust_flange"]:
        raise ValueError("rear exhaust bore is obstructed by hood or flange geometry.")
    return {
        "luminous_axis_to_rear_exhaust": {
            "origin_cad_mm": list(RAY_ORIGIN_CAD_MM),
            "direction_cad": list(direction),
            "hood_and_flange_clear": True,
            "intervening_internal_leaf_first_distances_mm": {
                name: hits[name][0] for name in required_internal
            },
        }
    }


def _cad_ray_distances(
    shape: Any,
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
) -> list[float]:
    axis = Axis(origin, direction)
    intersection = BRepIntCurveSurface_Inter()
    intersection.Init(shape.wrapped, gce_MakeLin(axis.wrapped).Value(), 1.0e-7)
    distances: list[float] = []
    while intersection.More():
        parameter = float(intersection.W())
        if parameter > 1.0e-6:
            distances.append(parameter)
        intersection.Next()
    return sorted(distances)


def write_raw_glb(assembly: Any, path: Path) -> None:
    leaves_by_name = {item.label: item for item in _leaves(assembly)}
    binary = bytearray()
    buffer_views: list[dict[str, object]] = []
    accessors: list[dict[str, object]] = []
    meshes: list[dict[str, object]] = []

    def append_view(data: bytes, target: int) -> int:
        while len(binary) % 4:
            binary.append(0)
        offset = len(binary)
        binary.extend(data)
        buffer_views.append({
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(data),
            "target": target,
        })
        return len(buffer_views) - 1

    for name in EXPECTED_LEAVES:
        vertices, faces = leaves_by_name[name].tessellate(
            LINEAR_TOLERANCE_MM, ANGULAR_TOLERANCE_RAD
        )
        canonical: list[tuple[tuple[float, float, float], ...]] = []
        omitted_zero_area = 0
        for face in faces:
            points = tuple(_cad_to_gltf(tuple(vertices[index])) for index in face)
            if _triangle_area_squared(points) <= 1.0e-20:
                omitted_zero_area += 1
                continue
            minimum = min(range(3), key=points.__getitem__)
            canonical.append(points[minimum:] + points[:minimum])
        canonical.sort()
        if omitted_zero_area != EXPECTED_ZERO_AREA_TESSELLATION_TRIANGLES.get(name, 0):
            raise ValueError(
                f"OpenCascade zero-area tessellation inventory changed for {name}: "
                f"{omitted_zero_area}."
            )
        positions: list[tuple[float, float, float]] = []
        normals: list[tuple[float, float, float]] = []
        for triangle in canonical:
            normal = _normal(triangle)
            positions.extend(triangle)
            normals.extend((normal, normal, normal))
        indices = tuple(range(len(positions)))
        position_view = append_view(
            b"".join(struct.pack("<3f", *item) for item in positions), 34962
        )
        normal_view = append_view(
            b"".join(struct.pack("<3f", *item) for item in normals), 34962
        )
        index_view = append_view(
            struct.pack(f"<{len(indices)}I", *indices), 34963
        )
        position_accessor = len(accessors)
        accessors.append({
            "bufferView": position_view,
            "componentType": 5126,
            "count": len(positions),
            "type": "VEC3",
            "min": [min(item[axis] for item in positions) for axis in range(3)],
            "max": [max(item[axis] for item in positions) for axis in range(3)],
        })
        normal_accessor = len(accessors)
        accessors.append({
            "bufferView": normal_view,
            "componentType": 5126,
            "count": len(normals),
            "type": "VEC3",
            "min": [min(item[axis] for item in normals) for axis in range(3)],
            "max": [max(item[axis] for item in normals) for axis in range(3)],
        })
        index_accessor = len(accessors)
        accessors.append({
            "bufferView": index_view,
            "componentType": 5125,
            "count": len(indices),
            "type": "SCALAR",
            "min": [0],
            "max": [len(indices) - 1],
        })
        meshes.append({
            "name": name,
            "extras": {"cad_leaf_name": name},
            "primitives": [{
                "attributes": {"POSITION": position_accessor, "NORMAL": normal_accessor},
                "indices": index_accessor,
                "material": EXPECTED_LEAVES.index(name),
                "mode": 4,
                "extras": {"cad_leaf_name": name},
            }],
        })

    nodes: list[dict[str, object]] = []

    def add_node(
        name: str, *, children: list[int] | None = None, mesh: int | None = None
    ) -> int:
        node: dict[str, object] = {"name": name, "extras": {"cad_label": name}}
        if children is not None:
            node["children"] = children
        if mesh is not None:
            node["mesh"] = mesh
        nodes.append(node)
        return len(nodes) - 1

    mesh_by_name = {name: index for index, name in enumerate(EXPECTED_LEAVES)}
    top_box = add_node(EXPECTED_LEAVES[0], mesh=mesh_by_name[EXPECTED_LEAVES[0]])
    skirt = add_node(EXPECTED_LEAVES[1], mesh=mesh_by_name[EXPECTED_LEAVES[1]])
    hood = add_node("reflector_hood", children=[top_box, skirt])
    flange = add_node(EXPECTED_LEAVES[2], mesh=mesh_by_name[EXPECTED_LEAVES[2]])
    bracket = add_node(EXPECTED_LEAVES[3], mesh=mesh_by_name[EXPECTED_LEAVES[3]])
    socket = add_node(EXPECTED_LEAVES[4], mesh=mesh_by_name[EXPECTED_LEAVES[4]])
    bracket_assembly = add_node("socket_bracket_assembly", children=[bracket, socket])
    base = add_node(EXPECTED_LEAVES[5], mesh=mesh_by_name[EXPECTED_LEAVES[5]])
    glass = add_node(EXPECTED_LEAVES[6], mesh=mesh_by_name[EXPECTED_LEAVES[6]])
    arc = add_node(EXPECTED_LEAVES[7], mesh=mesh_by_name[EXPECTED_LEAVES[7]])
    root = add_node(
        "competitor_hps_1000w",
        children=[hood, flange, bracket_assembly, base, glass, arc],
    )
    materials = [
        _material("reflector_hood_top_box", (0.72, 0.72, 0.72, 1.0), 0.85, 0.28),
        _material("reflector_hood_flared_skirt", (0.88, 0.88, 0.82, 1.0), 0.75, 0.22),
        _material("exhaust_flange", (0.58, 0.58, 0.58, 1.0), 0.90, 0.32),
        _material("socket_bracket", (0.35, 0.35, 0.35, 1.0), 0.80, 0.38),
        _material("ceramic_socket", (0.85, 0.82, 0.72, 1.0), 0.0, 0.65),
        _material("hps_bulb_base", (0.72, 0.64, 0.45, 1.0), 0.65, 0.36),
        _material("hps_bulb_glass", (0.75, 0.90, 1.0, 0.35), 0.0, 0.12, blend=True),
        _material("hps_inner_arc_tube", (1.0, 0.78, 0.35, 1.0), 0.15, 0.30),
    ]
    document = {
        "asset": {
            "version": "2.0",
            "generator": "fspm-optics OpenCascade HPS named-leaf GLB writer v1",
            "extras": {
                "cad_units": "millimetres",
                "cad_to_gltf_axis_conversion": "(X,Y,Z)->(X,Z,-Y)",
                "linear_tolerance_mm": LINEAR_TOLERANCE_MM,
                "angular_tolerance_rad": ANGULAR_TOLERANCE_RAD,
                "viewer_materials_only": True,
            },
        },
        "scene": 0,
        "scenes": [{"name": "competitor_hps_1000w", "nodes": [root]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(binary)}],
    }
    _write_glb(path, document, bytes(binary))


def _material(
    name: str,
    color: tuple[float, float, float, float],
    metallic: float,
    roughness: float,
    *,
    blend: bool = False,
) -> dict[str, object]:
    return {
        "name": name,
        "extras": {"viewer_only": True, "cad_leaf_name": name},
        "pbrMetallicRoughness": {
            "baseColorFactor": list(color),
            "metallicFactor": metallic,
            "roughnessFactor": roughness,
        },
        "alphaMode": "BLEND" if blend else "OPAQUE",
        "doubleSided": True,
    }


def validate_glb(path: Path, *, expected_generator: str | None) -> dict[str, object]:
    data = path.read_bytes()
    decoded = decode_fixture_glb(data)
    document = decoded.document
    used = tuple(document.get("extensionsUsed", ()))
    required = tuple(document.get("extensionsRequired", ()))
    if used or required:
        raise ValueError(f"{path.name} must not declare GLB extensions under -noq.")
    if (
        document.get("animations") or document.get("cameras")
        or _contains_key(document, "uri")
        or _contains_key(document, "KHR_lights_punctual")
    ):
        raise ValueError(f"{path.name} contains prohibited GLB content.")
    if expected_generator is not None and document.get("asset", {}).get("generator") != expected_generator:
        raise ValueError(f"{path.name} generator identity changed.")
    transformed_nodes = [
        node for node in document["nodes"]
        if any(key in node for key in ("matrix", "translation", "rotation", "scale"))
    ]
    if transformed_nodes:
        raise ValueError(f"{path.name} contains an unexpected geometry correction transform.")

    inventory = decoded.primitive_inventory
    paths = tuple(item.node_path for item in inventory)
    if len(inventory) != 8 or paths != EXPECTED_PATHS or len(set(paths)) != 8:
        raise ValueError(f"{path.name} named primitive hierarchy changed.")
    meshes = document.get("meshes")
    if (
        not isinstance(meshes, list) or len(meshes) != 8
        or any(len(mesh.get("primitives", ())) != 1 for mesh in meshes)
    ):
        raise ValueError(f"{path.name} must retain one mesh and primitive per leaf.")
    materials = document.get("materials")
    if not isinstance(materials, list) or len(materials) != 8:
        raise ValueError(f"{path.name} material inventory changed.")
    if any(material.get("doubleSided") is not True for material in materials):
        raise ValueError(f"{path.name} materials must all be double-sided.")

    glass_material_indices: set[int] = set()
    primitive_payload: list[dict[str, object]] = []
    triangles_by_path: dict[str, tuple[Any, ...]] = {}
    all_triangles: list[Any] = []
    for primitive in inventory:
        raw = meshes[primitive.mesh_index]["primitives"][primitive.primitive_index]
        if not isinstance(raw.get("indices"), int) or raw.get("mode", 4) != 4:
            raise ValueError(f"{path.name} primitive is not explicitly indexed triangles.")
        normal_index = raw.get("attributes", {}).get("NORMAL")
        if not isinstance(normal_index, int):
            raise ValueError(f"{path.name} primitive normals are missing.")
        normals = decoded._accessor_values(normal_index)
        if any(
            len(item) != 3 or not all(math.isfinite(float(value)) for value in item)
            or not math.isclose(
                math.sqrt(math.fsum(float(value) ** 2 for value in item)),
                1.0, rel_tol=0.0, abs_tol=5.0e-3,
            )
            for item in normals
        ):
            raise ValueError(f"{path.name} normals are invalid.")
        material_index = raw.get("material")
        if not isinstance(material_index, int) or not 0 <= material_index < 8:
            raise ValueError(f"{path.name} primitive material is invalid.")
        material = materials[material_index]
        alpha = material.get("pbrMetallicRoughness", {}).get("baseColorFactor", [1, 1, 1, 1])
        is_glass = primitive.node_path.endswith("/hps_bulb_glass")
        if is_glass:
            glass_material_indices.add(material_index)
            if (
                material.get("alphaMode") != "BLEND"
                or not math.isclose(float(alpha[3]), 0.35, rel_tol=0.0, abs_tol=1.0e-8)
            ):
                raise ValueError(f"{path.name} HPS glass alpha contract changed.")
        elif material.get("alphaMode", "OPAQUE") != "OPAQUE" or alpha[3] != 1.0:
            raise ValueError(f"{path.name} non-glass material is not opaque.")
        triangles = tuple(decoded.triangles(primitive))
        if any(
            not all(math.isfinite(value) for point in triangle for value in point)
            or _triangle_area_squared(triangle) <= 1.0e-20
            for triangle in triangles
        ):
            raise ValueError(f"{path.name} contains invalid or degenerate triangles.")
        triangles_by_path[primitive.node_path] = triangles
        all_triangles.extend(triangles)
        primitive_bounds = _bounds(triangles)
        primitive_payload.append({
            "node_index": primitive.node_index,
            "mesh_index": primitive.mesh_index,
            "primitive_index": primitive.primitive_index,
            "node_path": primitive.node_path,
            "triangle_count": len(triangles),
            "geometry_sha256": _triangle_geometry_sha256(triangles),
            "material_index": material_index,
            "alpha_mode": material.get("alphaMode", "OPAQUE"),
            "base_color_alpha": float(alpha[3]),
            "bounds_gltf_mm": [list(primitive_bounds[0]), list(primitive_bounds[1])],
            "decoded_closed_mesh_volume_mm3": _triangle_mesh_volume(triangles),
        })
    if len(glass_material_indices) != 1:
        raise ValueError(f"{path.name} must contain one transparent HPS bulb material.")

    bounds = _bounds(all_triangles)
    _assert_bounds(bounds, GLTF_BOUNDS, 2.0e-4, path.name)
    top_box_triangles = triangles_by_path[EXPECTED_PATHS[0]]
    top_box_bounds = _bounds(top_box_triangles)
    expected_top_box_gltf_bounds = (
        (-355.6, -78.0, -165.0),
        (355.6, 0.0, 165.0),
    )
    _assert_bounds(top_box_bounds, expected_top_box_gltf_bounds, 2.0e-4, "decoded sealed top box")
    top_box_volume = _triangle_mesh_volume(top_box_triangles)
    if not math.isclose(top_box_volume, TOP_BOX_VOLUME_MM3, rel_tol=0.0, abs_tol=2.0):
        raise ValueError(f"{path.name} decoded top-box volume no longer proves a sealed roof.")
    hood_ray_results, complete_ray_results = _validate_decoded_rays(triangles_by_path)
    surface_area = math.fsum(math.sqrt(_triangle_area_squared(t)) for t in all_triangles)
    projected_area = 0.5 * math.fsum(
        abs(
            (triangle[1][0] - triangle[0][0]) * (triangle[2][2] - triangle[0][2])
            - (triangle[1][2] - triangle[0][2]) * (triangle[2][0] - triangle[0][0])
        )
        for triangle in all_triangles
    )
    inventory_payload = node_inventory_payload(decoded)
    document_inventory = {
        "nodes": document["nodes"],
        "accessors": document["accessors"],
        "meshes": meshes,
        "materials": materials,
    }
    return {
        "byte_length": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "node_count": len(document["nodes"]),
        "mesh_count": len(meshes),
        "primitive_count": len(inventory),
        "triangle_count": len(all_triangles),
        "inventory_sha256": _hash_json(inventory_payload),
        "node_primitive_inventory": inventory_payload,
        "document_inventory": document_inventory,
        "primitive_inventory": primitive_payload,
        "extensions_used": list(used),
        "extensions_required": list(required),
        "bounds_gltf_mm": [list(bounds[0]), list(bounds[1])],
        "surface_area_mm2": surface_area,
        "projected_area_mm2": projected_area,
        "full_geometry_sha256": _triangle_geometry_sha256(all_triangles),
        "indexed_finite_triangles": True,
        "finite_unit_normals": True,
        "embedded_data_only": True,
        "unexpected_transforms": False,
        "degenerate_triangle_count": 0,
        "sealed_top_box": {
            "bounds_gltf_mm": [list(top_box_bounds[0]), list(top_box_bounds[1])],
            "decoded_closed_mesh_volume_mm3": top_box_volume,
            "straight_up_ray_hits": True,
            "former_vertical_roof_bore_absent": True,
        },
        "material_contract": {
            "material_count": 8,
            "all_double_sided": True,
            "glass_alpha_mode": "BLEND",
            "authored_glass_base_color_alpha": 0.35,
            "stored_glass_base_color_alpha": float(
                materials[next(iter(glass_material_indices))]["pbrMetallicRoughness"]["baseColorFactor"][3]
            ),
            "all_other_alpha_modes": "OPAQUE",
            "viewer_only_not_scientific_transport": True,
        },
        "hood_opening_ray_assertions": hood_ray_results,
        "complete_assembly_ray_assertions": complete_ray_results,
    }


def _validate_decoded_rays(
    triangles_by_path: dict[str, tuple[Any, ...]],
) -> tuple[dict[str, object], dict[str, object]]:
    hood = tuple(
        triangle
        for path in EXPECTED_PATHS[:2]
        for triangle in triangles_by_path[path]
    )
    results: dict[str, object] = {}
    for name, (cad_origin, cad_direction, expected_hit) in HOOD_RAY_SPECS.items():
        origin = _cad_to_gltf(cad_origin)
        direction = _cad_to_gltf(cad_direction)
        distances = _triangle_ray_distances(hood, origin, direction)
        if bool(distances) is not expected_hit:
            outcome = "hit" if distances else "miss"
            raise ValueError(f"decoded GLB hood ray {name} produced unexpected {outcome}.")
        results[name] = {
            "origin_cad_mm": list(cad_origin),
            "direction_cad": list(cad_direction),
            "expected": "hit" if expected_hit else "miss",
            "hit": bool(distances),
            **({} if not distances else {"first_distance_mm": distances[0]}),
        }

    cad_direction = _direction_to(REAR_WALL_CENTER_CAD_MM)
    origin = _cad_to_gltf(RAY_ORIGIN_CAD_MM)
    direction = _cad_to_gltf(cad_direction)
    required_paths = {
        "socket_bracket": EXPECTED_PATHS[3],
        "ceramic_socket": EXPECTED_PATHS[4],
        "hps_bulb_base": EXPECTED_PATHS[5],
    }
    internal_hits = {
        name: _triangle_ray_distances(triangles_by_path[path], origin, direction)
        for name, path in required_paths.items()
    }
    hood_and_flange = tuple(
        triangle
        for path in EXPECTED_PATHS[:3]
        for triangle in triangles_by_path[path]
    )
    if any(not values for values in internal_hits.values()):
        raise ValueError("decoded bulb base and socket no longer intervene before rear exhaust.")
    if _triangle_ray_distances(hood_and_flange, origin, direction):
        raise ValueError("decoded rear exhaust bore is obstructed by hood or flange.")
    complete = {
        "luminous_axis_to_rear_exhaust": {
            "origin_cad_mm": list(RAY_ORIGIN_CAD_MM),
            "direction_cad": list(cad_direction),
            "hood_and_flange_clear": True,
            "intervening_internal_leaf_first_distances_mm": {
                name: values[0] for name, values in internal_hits.items()
            },
        }
    }
    return results, complete


def _triangle_ray_distances(
    triangles: Iterable[Any],
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
) -> list[float]:
    length = math.sqrt(math.fsum(value * value for value in direction))
    unit = tuple(value / length for value in direction)
    hits: list[float] = []
    for triangle in triangles:
        edge1 = _subtract(triangle[1], triangle[0])
        edge2 = _subtract(triangle[2], triangle[0])
        pvec = _cross(unit, edge2)
        determinant = _dot(edge1, pvec)
        if abs(determinant) <= 1.0e-10:
            continue
        inverse = 1.0 / determinant
        tvec = _subtract(origin, triangle[0])
        u = _dot(tvec, pvec) * inverse
        if u < -1.0e-9 or u > 1.0 + 1.0e-9:
            continue
        qvec = _cross(tvec, edge1)
        v = _dot(unit, qvec) * inverse
        if v < -1.0e-9 or u + v > 1.0 + 1.0e-9:
            continue
        distance = _dot(edge2, qvec) * inverse
        if distance > 1.0e-6:
            hits.append(distance)
    return sorted(hits)


def compare_raw_packed(raw: dict[str, object], packed: dict[str, object]) -> dict[str, object]:
    for key in ("mesh_count", "primitive_count", "triangle_count"):
        if raw[key] != packed[key]:
            raise ValueError(f"packed GLB changed {key}.")
    raw_bounds = raw["bounds_gltf_mm"]
    packed_bounds = packed["bounds_gltf_mm"]
    max_residual = max(
        abs(float(left) - float(right))
        for raw_row, packed_row in zip(raw_bounds, packed_bounds, strict=True)
        for left, right in zip(raw_row, packed_row, strict=True)
    )
    surface_relative = abs(
        float(packed["surface_area_mm2"]) - float(raw["surface_area_mm2"])
    ) / float(raw["surface_area_mm2"])
    projected_relative = abs(
        float(packed["projected_area_mm2"]) - float(raw["projected_area_mm2"])
    ) / float(raw["projected_area_mm2"])
    geometry_equal = raw["full_geometry_sha256"] == packed["full_geometry_sha256"]
    if max_residual > 2.0e-4 or surface_relative > 2.0e-5 or projected_relative > 2.0e-5:
        raise ValueError("raw/packed HPS GLB geometry agreement exceeded tolerance.")
    if not geometry_equal:
        raise ValueError("raw/packed HPS GLBs changed decoded triangle geometry.")
    return {
        "mesh_count_equal": True,
        "primitive_count_equal": True,
        "triangle_count_equal": True,
        "triangle_geometry_sha256_equal": True,
        "bounds_max_residual_mm": max_residual,
        "surface_area_relative_error": surface_relative,
        "projected_area_relative_error": projected_relative,
        "hood_opening_rays_equal": (
            raw["hood_opening_ray_assertions"]
            == packed["hood_opening_ray_assertions"]
        ),
        "complete_assembly_rays_equal": (
            raw["complete_assembly_ray_assertions"]
            == packed["complete_assembly_ray_assertions"]
        ),
    }


def _hierarchy(node: Any) -> dict[str, object]:
    def children_payload(item: Any) -> object:
        if not item.children:
            return None
        return {
            child.label: (
                [leaf.label for leaf in child.children]
                if child.children else None
            )
            for child in item.children
        }

    return {node.label: children_payload(node)}


def _clear_step_presentation(node: Any) -> None:
    node.color = None
    for child in node.children:
        _clear_step_presentation(child)


def _normalize_step_header(path: Path) -> None:
    data = path.read_bytes()
    normalized, count = re.subn(
        rb"(FILE_NAME\('[^']*',)'[^']*'",
        rb"\1'1970-01-01T00:00:00'",
        data,
        count=1,
    )
    if count != 1:
        raise ValueError("STEP FILE_NAME header timestamp could not be normalized.")
    path.write_bytes(
        b"\n".join(line.rstrip(b" \t\r") for line in normalized.split(b"\n"))
    )


def _leaves(node: Any) -> list[Any]:
    output: list[Any] = []
    for child in node.children:
        if child.children:
            output.extend(_leaves(child))
        else:
            output.append(child)
    return output


def _cad_to_gltf(
    point: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (float(point[0]), float(point[2]), -float(point[1]))


def _normal(
    triangle: tuple[tuple[float, float, float], ...],
) -> tuple[float, float, float]:
    cross = _cross(_subtract(triangle[1], triangle[0]), _subtract(triangle[2], triangle[0]))
    length = math.sqrt(math.fsum(value * value for value in cross))
    if length <= 1.0e-12:
        raise ValueError("cannot compute a normal for a degenerate triangle.")
    return tuple(value / length for value in cross)  # type: ignore[return-value]


def _triangle_area_squared(triangle: Iterable[Iterable[float]]) -> float:
    points = tuple(tuple(float(value) for value in point) for point in triangle)
    cross = _cross(_subtract(points[1], points[0]), _subtract(points[2], points[0]))
    return 0.25 * math.fsum(value * value for value in cross)


def _triangle_mesh_volume(triangles: Iterable[Any]) -> float:
    signed = math.fsum(
        _dot(triangle[0], _cross(triangle[1], triangle[2])) / 6.0
        for triangle in triangles
    )
    return abs(signed)


def _subtract(left: Iterable[float], right: Iterable[float]) -> tuple[float, float, float]:
    return tuple(float(a) - float(b) for a, b in zip(left, right, strict=True))  # type: ignore[return-value]


def _cross(left: Iterable[float], right: Iterable[float]) -> tuple[float, float, float]:
    a = tuple(left)
    b = tuple(right)
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(left: Iterable[float], right: Iterable[float]) -> float:
    return math.fsum(float(a) * float(b) for a, b in zip(left, right, strict=True))


def _bounds(
    triangles: Iterable[Any],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    points = tuple(point for triangle in triangles for point in triangle)
    return (
        tuple(min(float(point[axis]) for point in points) for axis in range(3)),
        tuple(max(float(point[axis]) for point in points) for axis in range(3)),
    )  # type: ignore[return-value]


def _shape_bounds(
    shape: Any,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    bounds = shape.bounding_box()
    return (
        (float(bounds.min.X), float(bounds.min.Y), float(bounds.min.Z)),
        (float(bounds.max.X), float(bounds.max.Y), float(bounds.max.Z)),
    )


def _assert_bounds(
    actual: tuple[tuple[float, float, float], tuple[float, float, float]],
    expected: tuple[tuple[float, float, float], tuple[float, float, float]],
    tolerance: float,
    label: str,
) -> None:
    if any(
        not math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)
        for actual_row, expected_row in zip(actual, expected, strict=True)
        for left, right in zip(actual_row, expected_row, strict=True)
    ):
        raise ValueError(f"{label} full bounds changed: {actual!r}.")


def _assert_vector(
    actual: Iterable[float],
    expected: tuple[float, float, float],
    tolerance: float,
    label: str,
) -> None:
    values = tuple(float(value) for value in actual)
    if any(
        not math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)
        for left, right in zip(values, expected, strict=True)
    ):
        raise ValueError(f"{label} changed: {values!r}.")


def _triangle_geometry_sha256(triangles: Iterable[Any]) -> str:
    canonical = []
    for triangle in triangles:
        points = tuple(tuple(float(value) for value in point) for point in triangle)
        canonical.append(tuple(sorted(points)))
    canonical.sort()
    digest = hashlib.sha256()
    for triangle in canonical:
        for point in triangle:
            digest.update(struct.pack("<3f", *point))
    return digest.hexdigest()


def _write_glb(path: Path, document: dict[str, object], binary: bytes) -> None:
    json_chunk = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    json_chunk += b" " * (-len(json_chunk) % 4)
    binary += b"\x00" * (-len(binary) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(binary)
    path.write_bytes(
        struct.pack("<III", 0x46546C67, 2, total)
        + struct.pack("<II", len(json_chunk), 0x4E4F534A)
        + json_chunk
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
