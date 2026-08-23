"""Reusable Radiance body geometry planned from authenticated fixture GLBs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
from typing import Any, Final, Mapping, Protocol, Sequence

from fspm_optics.fixtures.smd.positions import SmdLayout
from fspm_optics.fixtures.smd.alignment_lattice import (
    ALIGNMENT_LATTICE_RADIUS_M,
    AUTHENTICATED_REAR_PLANE_OFFSET_M,
    validate_alignment_lattice_payload,
)
from fspm_optics.radiance.commands import (
    CommandSpec,
    build_oconv_command,
)
from fspm_optics.radiance.runner import RunnerResult
from fspm_optics.viewer.fixtures import (
    FixtureTransportPlacement,
    resolve_fixture_transport_placements,
    validate_hps_publication_transform,
)

from .gltf import Point3, Triangle
from .manifest import (
    FIXTURE_BODY_MATERIAL_ID,
    FIXTURE_BODY_MATERIAL_NAME,
    FIXTURE_BODY_MATERIAL_RAD,
    OCCLUSION_MODEL_VERSION,
    AuthenticatedFixtureAsset,
    classification_manifest_sha256,
    load_authenticated_fixture_asset,
)

OCCLUSION_VERSION_ID: Final = (
    f"glb-fixture-occlusion-v{OCCLUSION_MODEL_VERSION}"
)
TRANSFORM_POLICY_ID: Final = (
    "viewer-catalog-matrix-to-scientific-z-up-v1"
)
PROJECTED_AREA_POLICY_ID: Final = (
    "half-two-sided-horizontal-triangle-projection-v1"
)
EMITTING_BOUNDARY_POLICY_ID: Final = (
    "glb-light-face-lowest-plane-convex-boundary-v1"
)
_LIGHT_FACE_PATTERN = re.compile(r"/led_light_face_(\d+)$")


class FixtureOcclusionPlanningError(RuntimeError):
    """Authenticated fixture geometry cannot form a closed transport plan."""


class OcclusionCommandRunner(Protocol):
    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult: ...


@dataclass(frozen=True, slots=True)
class FixtureEmittingBoundary:
    fixture_id: str
    asset_id: str
    bar_index: int
    node_path: str
    vertices_m: tuple[Point3, ...]
    area_m2: float
    plane_z_m: float

    def to_payload(self) -> dict[str, object]:
        return {
            "fixture_id": self.fixture_id,
            "asset_id": self.asset_id,
            "bar_index": self.bar_index,
            "node_path": self.node_path,
            "vertices_m": [list(vertex) for vertex in self.vertices_m],
            "area_m2": self.area_m2,
            "plane_z_m": self.plane_z_m,
        }


@dataclass(frozen=True, slots=True)
class FixtureBodyShapePlan:
    shape_id: str
    asset_id: str
    source_path: Path
    octree_path: Path
    source_text: str
    source_sha256: str
    linear_transform_sha256: str
    primitive_count: int
    triangle_count: int
    projected_opaque_area_m2: float
    bounds_m: tuple[Point3, Point3]
    authenticated_full_glb_bounds_m: tuple[Point3, Point3]
    compile_command: CommandSpec

    def scientific_payload(self) -> dict[str, object]:
        return {
            "shape_id": self.shape_id,
            "asset_id": self.asset_id,
            "source_sha256": self.source_sha256,
            "linear_transform_sha256": self.linear_transform_sha256,
            "primitive_count": self.primitive_count,
            "triangle_count": self.triangle_count,
            "projected_opaque_area_m2": self.projected_opaque_area_m2,
            "bounds_m": [list(self.bounds_m[0]), list(self.bounds_m[1])],
            "authenticated_full_glb_bounds_m": [
                list(self.authenticated_full_glb_bounds_m[0]),
                list(self.authenticated_full_glb_bounds_m[1]),
            ],
            "authenticated_full_glb_dimensions_m": [
                self.authenticated_full_glb_bounds_m[1][axis]
                - self.authenticated_full_glb_bounds_m[0][axis]
                for axis in range(3)
            ],
        }


@dataclass(frozen=True, slots=True)
class FixtureBodyInstancePlan:
    instance_id: str
    fixture_id: str
    asset_id: str
    shape_id: str
    translation_m: Point3
    viewer_matrix_sha256: str

    def scientific_payload(self) -> dict[str, object]:
        return {
            "instance_id": self.instance_id,
            "fixture_id": self.fixture_id,
            "asset_id": self.asset_id,
            "shape_id": self.shape_id,
            "translation_m": list(self.translation_m),
            "viewer_matrix_sha256": self.viewer_matrix_sha256,
        }


@dataclass(frozen=True, slots=True)
class FixtureOcclusionPlan:
    system_id: str
    output_directory: Path
    shapes: tuple[FixtureBodyShapePlan, ...]
    instances: tuple[FixtureBodyInstancePlan, ...]
    emitting_boundaries: tuple[FixtureEmittingBoundary, ...]
    instance_source_path: Path
    metadata_path: Path
    instance_source_text: str
    identity_sha256: str
    transform_set_sha256: str
    classification_manifest_sha256: str
    asset_provenance: tuple[Mapping[str, object], ...]
    total_projected_opaque_area_m2: float
    total_external_triangle_instances: int
    alignment_lattice: Mapping[str, object] | None

    @property
    def emitting_boundary_area_m2(self) -> float:
        return math.fsum(item.area_m2 for item in self.emitting_boundaries)

    @property
    def compile_commands(self) -> tuple[CommandSpec, ...]:
        return tuple(item.compile_command for item in self.shapes)

    def scientific_payload(self) -> dict[str, object]:
        return {
            "occlusion_version": OCCLUSION_VERSION_ID,
            "system_id": self.system_id,
            "identity_sha256": self.identity_sha256,
            "classification_manifest_sha256": (
                self.classification_manifest_sha256
            ),
            "transform_policy_id": TRANSFORM_POLICY_ID,
            "transform_set_sha256": self.transform_set_sha256,
            "material": {
                "material_id": FIXTURE_BODY_MATERIAL_ID,
                "radiance_identifier": FIXTURE_BODY_MATERIAL_NAME,
                "radiance_text_sha256": _sha256_text(
                    FIXTURE_BODY_MATERIAL_RAD
                ),
                "assumption": (
                    "shared generic anodized extruded aluminum reference; "
                    "not a product-specific material claim"
                ),
                "evidence": (
                    "completed_fixture_body_optics_audit_70pct_metal_case"
                ),
                "shared_systems": ["proposed", "conventional", "hps"],
            },
            "projected_area_policy_id": PROJECTED_AREA_POLICY_ID,
            "emitting_boundary_policy_id": EMITTING_BOUNDARY_POLICY_ID,
            "assets": [dict(item) for item in self.asset_provenance],
            "shapes": [item.scientific_payload() for item in self.shapes],
            "instances": [
                item.scientific_payload() for item in self.instances
            ],
            "emitting_boundaries": [
                item.to_payload() for item in self.emitting_boundaries
            ],
            "counts": {
                "fixture_instances": len(self.instances),
                "unique_body_shapes": len(self.shapes),
                "external_triangle_instances": (
                    self.total_external_triangle_instances
                ),
                "emitting_boundaries": len(self.emitting_boundaries),
                **(
                    {}
                    if self.alignment_lattice is None
                    else {
                        "alignment_links": int(
                            self.alignment_lattice["counts"]["total"]  # type: ignore[index]
                        )
                    }
                ),
            },
            **(
                {}
                if self.alignment_lattice is None
                else {"alignment_lattice": dict(self.alignment_lattice)}
            ),
            "projected_opaque_area_m2": (
                self.total_projected_opaque_area_m2
            ),
            "emitting_boundary_area_m2": self.emitting_boundary_area_m2,
            "raw_glb_pbr_materials_used_for_transport": False,
            "optical_stack_geometry_replaced": False,
            "hps_included": self.system_id == "hps",
        }


def proposed_layout_transport_payload(layout: SmdLayout) -> dict[str, object]:
    """Return the viewer-authoritative subset needed for exact placement."""

    return {
        "room_length_m": layout.room_length_m,
        "room_width_m": layout.room_width_m,
        "axes_swapped": layout.axes_swapped,
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
        "modules": [
            {
                "module_index": module.module_index,
                "x_m": module.x_m,
                "y_m": module.y_m,
                "z_m": module.z_m,
                "control_zone_index": module.control_zone_index,
            }
            for module in layout.modules
        ],
        "fixtures": [
            {
                "fixture_id": fixture.fixture_id,
                "fixture_type": fixture.fixture_type,
                "display_fixture_type": fixture.display_fixture_type,
                "source_orientation": fixture.source_orientation,
                "orientation_degrees": fixture.orientation_degrees,
                "member_module_indices": list(
                    fixture.member_module_indices
                ),
                "connectors": [
                    {
                        "start_module_index": connector.start_module_index,
                        "end_module_index": connector.end_module_index,
                    }
                    for connector in fixture.connectors
                ],
            }
            for fixture in layout.fixtures
        ],
        **(
            {}
            if layout.alignment_lattice is None
            else {"alignment_lattice": layout.alignment_lattice.to_payload()}
        ),
    }


def plan_fixture_occlusion(
    *,
    system_id: str,
    layout_identity: Mapping[str, object],
    output_directory: str | Path,
    oconv_bin: str | Path = "oconv",
) -> FixtureOcclusionPlan:
    """Plan reusable reflective structures and GLB-derived luminous boundaries."""

    if system_id not in {"proposed", "conventional", "hps"}:
        raise FixtureOcclusionPlanningError(
            "fixture occlusion is limited to Proposed, Conventional, and HPS."
        )
    output = Path(output_directory).expanduser().resolve()
    placements = resolve_fixture_transport_placements(
        system_id, layout_identity
    )
    if not placements:
        raise FixtureOcclusionPlanningError(
            "fixture occlusion requires at least one placed fixture."
        )
    raw_lattice = layout_identity.get("alignment_lattice")
    alignment_lattice: Mapping[str, object] | None = None
    if (
        system_id == "proposed"
        and layout_identity.get("proposed_layout_mode")
        == "standalone_modules"
    ):
        alignment_lattice = validate_alignment_lattice_payload(
            raw_lattice,
            expected_module_count=len(placements),
            expected_modules=layout_identity.get("modules"),
        )
    elif raw_lattice is not None:
        raise FixtureOcclusionPlanningError(
            "alignment lattice is permitted only for standalone Proposed modules."
        )
    authenticated_by_id: dict[str, AuthenticatedFixtureAsset] = {}
    shapes_by_key: dict[
        tuple[str, str],
        tuple[
            str,
            tuple[Triangle, ...],
            int,
            float,
            tuple[Point3, Point3],
            tuple[Point3, Point3],
        ],
    ] = {}
    placement_shapes: list[
        tuple[
            FixtureTransportPlacement,
            str,
            Point3,
        ]
    ] = []
    boundaries: list[FixtureEmittingBoundary] = []
    transform_records: list[dict[str, object]] = []
    for placement in placements:
        authenticated = authenticated_by_id.setdefault(
            placement.asset.asset_id,
            load_authenticated_fixture_asset(placement.asset.asset_id),
        )
        linear, translation = _scientific_linear_translation(
            placement.matrix_row_major
        )
        linear_hash = hashlib.sha256(
            struct.pack("<9d", *linear)
        ).hexdigest()
        shape_key = (placement.asset.asset_id, linear_hash)
        if shape_key not in shapes_by_key:
            triangles: list[Triangle] = []
            for classified in authenticated.external_primitives:
                triangles.extend(
                    _transform_triangle_linear(triangle, linear)
                    for triangle in authenticated.decoded.triangles(
                        classified.inventory
                    )
                )
            cleaned = tuple(
                triangle
                for triangle in triangles
                if _triangle_area_squared(triangle) > 1.0e-28
            )
            if not cleaned:
                raise FixtureOcclusionPlanningError(
                    f"fixture has no external body triangles: "
                    f"{placement.asset.asset_id}"
                )
            full_triangles = tuple(
                _transform_triangle_linear(triangle, linear)
                for classified in authenticated.primitives
                for triangle in authenticated.decoded.triangles(
                    classified.inventory
                )
            )
            full_bounds = _bounds(full_triangles)
            _validate_catalog_dimensions(
                system_id,
                layout_identity,
                full_bounds,
            )
            if system_id == "hps":
                _validate_hps_asset_transform_contract(
                    authenticated,
                    full_bounds,
                )
            if (
                system_id == "proposed"
                and placement.asset.asset_id == "proposed-led-module-v1"
                and not math.isclose(
                    full_bounds[1][2],
                    AUTHENTICATED_REAR_PLANE_OFFSET_M,
                    rel_tol=0.0,
                    abs_tol=1.0e-15,
                )
            ):
                raise FixtureOcclusionPlanningError(
                    "standalone alignment attachment plane no longer matches "
                    "authenticated geometry."
                )
            shape_id = (
                "fixture-body-shape-"
                + _hash_payload(
                    {
                        "asset_id": placement.asset.asset_id,
                        "linear_transform_sha256": linear_hash,
                        "classification_sha256": (
                            authenticated.classification_sha256
                        ),
                    }
                )[:24]
            )
            shapes_by_key[shape_key] = (
                shape_id,
                cleaned,
                len(authenticated.external_primitives),
                _projected_opaque_area(cleaned),
                _bounds(cleaned),
                full_bounds,
            )
        shape_id = shapes_by_key[shape_key][0]
        placement_shapes.append((placement, shape_id, translation))
        if system_id == "hps":
            _validate_hps_placement_registration(
                placement,
                translation,
                layout_identity,
            )
        transform_record: dict[str, object] = {
            "fixture_id": placement.fixture_id,
            "asset_id": placement.asset.asset_id,
            "viewer_matrix_sha256": (
                placement.matrix_column_major_sha256
            ),
            "matrix_row_major": list(placement.matrix_row_major),
            "scientific_linear_sha256": linear_hash,
            "scientific_translation_m": list(translation),
        }
        if placement.placement_contract_sha256 is not None:
            transform_record["placement_contract_sha256"] = (
                placement.placement_contract_sha256
            )
        transform_records.append(transform_record)
        if system_id == "conventional":
            boundaries.extend(
                _emitting_boundaries(
                    placement,
                    authenticated,
                )
            )
    transform_set_sha256 = _hash_payload(transform_records)

    shape_plans: list[FixtureBodyShapePlan] = []
    shape_by_id: dict[str, FixtureBodyShapePlan] = {}
    for asset_id, linear_hash in sorted(shapes_by_key):
        (
            shape_id,
            triangles,
            primitive_count,
            area,
            bounds,
            full_bounds,
        ) = shapes_by_key[
            (asset_id, linear_hash)
        ]
        source_path = output / "shapes" / f"{shape_id}.rad"
        octree_path = output / "shapes" / f"{shape_id}.oct"
        source_text = _shape_radiance_text(shape_id, triangles)
        compile_command = build_oconv_command(
            (source_path,),
            output_octree=octree_path,
            cwd=output,
            oconv_bin=oconv_bin,
            label=f"compile_{shape_id}",
        )
        shape = FixtureBodyShapePlan(
            shape_id=shape_id,
            asset_id=asset_id,
            source_path=source_path,
            octree_path=octree_path,
            source_text=source_text,
            source_sha256=_sha256_text(source_text),
            linear_transform_sha256=linear_hash,
            primitive_count=primitive_count,
            triangle_count=len(triangles),
            projected_opaque_area_m2=area,
            bounds_m=bounds,
            authenticated_full_glb_bounds_m=full_bounds,
            compile_command=compile_command,
        )
        shape_plans.append(shape)
        shape_by_id[shape_id] = shape

    instances: list[FixtureBodyInstancePlan] = []
    for index, (placement, shape_id, translation) in enumerate(
        placement_shapes
    ):
        instances.append(
            FixtureBodyInstancePlan(
                instance_id=f"fixture_body_instance_{index:05d}",
                fixture_id=placement.fixture_id,
                asset_id=placement.asset.asset_id,
                shape_id=shape_id,
                translation_m=translation,
                viewer_matrix_sha256=(
                    placement.matrix_column_major_sha256
                ),
            )
        )
    instance_source_text = _instance_radiance_text(
        tuple(instances), shape_by_id, alignment_lattice=alignment_lattice
    )
    asset_provenance = tuple(
        {
            "asset_id": item.asset.asset_id,
            "fixture_type": item.asset.fixture_type,
            "glb_sha256": item.asset.sha256,
            "glb_byte_size": item.asset.byte_size,
            "node_primitive_inventory_sha256": item.inventory_sha256,
            "classification_sha256": item.classification_sha256,
            "classification_counts": item.classification_counts,
            "proposed_aperture_registration": (
                None
                if item.proposed_aperture_registration is None
                else item.proposed_aperture_registration.to_payload()
            ),
            **(
                {}
                if item.asset.system_id != "hps"
                else {
                    "placement_contract_sha256": next(
                        placement.placement_contract_sha256
                        for placement in placements
                        if placement.asset.asset_id == item.asset.asset_id
                    )
                }
            ),
        }
        for item in (
            authenticated_by_id[asset_id]
            for asset_id in sorted(authenticated_by_id)
        )
    )
    shape_instance_counts = {
        shape.shape_id: sum(
            item.shape_id == shape.shape_id for item in instances
        )
        for shape in shape_plans
    }
    total_area = math.fsum(
        shape.projected_opaque_area_m2
        * shape_instance_counts[shape.shape_id]
        for shape in shape_plans
    )
    total_triangles = sum(
        shape.triangle_count * shape_instance_counts[shape.shape_id]
        for shape in shape_plans
    )
    scientific_identity_payload = {
        "occlusion_version": OCCLUSION_VERSION_ID,
        "system_id": system_id,
        "classification_manifest_sha256": classification_manifest_sha256(),
        "transform_policy_id": TRANSFORM_POLICY_ID,
        "transform_set_sha256": transform_set_sha256,
        "material_id": FIXTURE_BODY_MATERIAL_ID,
        "material_rad_sha256": _sha256_text(FIXTURE_BODY_MATERIAL_RAD),
        "shapes": [
            item.scientific_payload() for item in shape_plans
        ],
        "instances": [
            item.scientific_payload() for item in instances
        ],
        "emitting_boundaries": [
            item.to_payload() for item in boundaries
        ],
        "asset_provenance": [dict(item) for item in asset_provenance],
        "projected_opaque_area_m2": total_area,
        "external_triangle_instances": total_triangles,
        **(
            {}
            if alignment_lattice is None
            else {"alignment_lattice": dict(alignment_lattice)}
        ),
    }
    identity = _hash_payload(scientific_identity_payload)
    return FixtureOcclusionPlan(
        system_id=system_id,
        output_directory=output,
        shapes=tuple(shape_plans),
        instances=tuple(instances),
        emitting_boundaries=tuple(boundaries),
        instance_source_path=output / "fixture_body_instances.rad",
        metadata_path=output / "fixture_occlusion.json",
        instance_source_text=instance_source_text,
        identity_sha256=identity,
        transform_set_sha256=transform_set_sha256,
        classification_manifest_sha256=classification_manifest_sha256(),
        asset_provenance=asset_provenance,
        total_projected_opaque_area_m2=total_area,
        total_external_triangle_instances=total_triangles,
        alignment_lattice=alignment_lattice,
    )


def extract_conventional_emitting_boundaries(
    layout_identity: Mapping[str, object],
) -> tuple[FixtureEmittingBoundary, ...]:
    """Extract the eight GLB faces per fixture without planning output paths."""

    boundaries: list[FixtureEmittingBoundary] = []
    for placement in resolve_fixture_transport_placements(
        "conventional", layout_identity
    ):
        boundaries.extend(
            _emitting_boundaries(
                placement,
                load_authenticated_fixture_asset(placement.asset.asset_id),
            )
        )
    return tuple(boundaries)


def materialize_fixture_occlusion(plan: FixtureOcclusionPlan) -> None:
    """Write deterministic shape sources, instances, and provenance."""

    payload = plan.scientific_payload() | {
        "runtime_paths": {
            "instance_source": str(plan.instance_source_path),
            "shape_sources": [
                str(shape.source_path) for shape in plan.shapes
            ],
            "shape_octrees": [
                str(shape.octree_path) for shape in plan.shapes
            ],
        },
        "instance_source_sha256": _sha256_text(
            plan.instance_source_text
        ),
    }
    expected = [
        *(
            (shape.source_path, shape.source_text)
            for shape in plan.shapes
        ),
        (plan.instance_source_path, plan.instance_source_text),
        (
            plan.metadata_path,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        ),
    ]
    for path, text in expected:
        if path.exists():
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                raise FixtureOcclusionPlanningError(
                    f"stale fixture occlusion artifact is incompatible: {path}"
                )
    for path, text in expected:
        if not path.exists():
            _atomic_write_text(path, text)


def compile_fixture_occlusion(
    plan: FixtureOcclusionPlan,
    runner: OcclusionCommandRunner,
    *,
    oconv_executable: str | Path | None = None,
    timeout_s: float | None = None,
) -> tuple[RunnerResult, ...]:
    """Compile each unique body shape once before any complete scene."""

    results: list[RunnerResult] = []
    for shape in plan.shapes:
        if _compiled_octree_is_current(shape):
            continue
        command = (
            shape.compile_command
            if oconv_executable is None
            else _replace_executable(
                shape.compile_command, Path(oconv_executable)
            )
        )
        result = runner.run(command, timeout_s=timeout_s)
        results.append(result)
        if not result.success:
            raise FixtureOcclusionPlanningError(
                result.failure_message
                or f"fixture body compilation failed: {shape.shape_id}"
            )
        if (
            not shape.octree_path.is_file()
            or shape.octree_path.stat().st_size <= 0
        ):
            raise FixtureOcclusionPlanningError(
                f"fixture body compilation did not create: {shape.octree_path}"
            )
        _atomic_write_text(
            _compiled_octree_marker_path(shape),
            json.dumps(
                {
                    "schema_id": (
                        "fspm-optics.fixture-body-compiled-octree"
                    ),
                    "schema_version": 1,
                    "shape_id": shape.shape_id,
                    "derived_mesh_source_sha256": shape.source_sha256,
                    "compiled_octree_sha256": _sha256_file(
                        shape.octree_path
                    ),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
    return tuple(results)


def validate_compiled_fixture_occlusion(plan: FixtureOcclusionPlan) -> None:
    """Fail closed unless all materialized and compiled body artifacts match."""

    payload = plan.scientific_payload() | {
        "runtime_paths": {
            "instance_source": str(plan.instance_source_path),
            "shape_sources": [
                str(shape.source_path) for shape in plan.shapes
            ],
            "shape_octrees": [
                str(shape.octree_path) for shape in plan.shapes
            ],
        },
        "instance_source_sha256": _sha256_text(
            plan.instance_source_text
        ),
    }
    expected_text = (
        *(
            (shape.source_path, shape.source_text)
            for shape in plan.shapes
        ),
        (plan.instance_source_path, plan.instance_source_text),
        (
            plan.metadata_path,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        ),
    )
    for path, text in expected_text:
        try:
            actual = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise FixtureOcclusionPlanningError(
                f"fixture occlusion artifact is missing or unreadable: {path}"
            ) from exc
        if actual != text:
            raise FixtureOcclusionPlanningError(
                f"fixture occlusion artifact differs from its plan: {path}"
            )
    try:
        root_names = {path.name for path in plan.output_directory.iterdir()}
    except OSError as exc:
        raise FixtureOcclusionPlanningError(
            "fixture occlusion output directory is missing or unreadable."
        ) from exc
    if root_names != {
        plan.instance_source_path.name,
        plan.metadata_path.name,
        "shapes",
    }:
        raise FixtureOcclusionPlanningError(
            "fixture occlusion artifact inventory changed."
        )
    shape_directory = plan.output_directory / "shapes"
    expected_shape_names: set[str] = set()
    for shape in plan.shapes:
        marker = _compiled_octree_marker_path(shape)
        expected_shape_names.update(
            {shape.source_path.name, shape.octree_path.name, marker.name}
        )
        if not _compiled_octree_is_current(shape):
            raise FixtureOcclusionPlanningError(
                f"compiled fixture body identity is stale: {shape.shape_id}"
            )
    try:
        shape_names = {path.name for path in shape_directory.iterdir()}
    except OSError as exc:
        raise FixtureOcclusionPlanningError(
            "fixture body shape directory is missing or unreadable."
        ) from exc
    if shape_names != expected_shape_names:
        raise FixtureOcclusionPlanningError(
            "fixture body shape artifact inventory changed."
        )


def _compiled_octree_marker_path(shape: FixtureBodyShapePlan) -> Path:
    return shape.octree_path.with_name(
        shape.octree_path.name + ".identity.json"
    )


def _compiled_octree_is_current(shape: FixtureBodyShapePlan) -> bool:
    marker = _compiled_octree_marker_path(shape)
    if (
        not shape.octree_path.is_file()
        or shape.octree_path.stat().st_size <= 0
        or not marker.is_file()
    ):
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return payload == {
        "schema_id": "fspm-optics.fixture-body-compiled-octree",
        "schema_version": 1,
        "shape_id": shape.shape_id,
        "derived_mesh_source_sha256": shape.source_sha256,
        "compiled_octree_sha256": _sha256_file(shape.octree_path),
    }


def _emitting_boundaries(
    placement: FixtureTransportPlacement,
    authenticated: AuthenticatedFixtureAsset,
) -> tuple[FixtureEmittingBoundary, ...]:
    output: list[FixtureEmittingBoundary] = []
    for classified in authenticated.emitter_primitives:
        match = _LIGHT_FACE_PATTERN.search(
            classified.inventory.node_path
        )
        if match is None:
            raise FixtureOcclusionPlanningError(
                "Conventional emitter classification must name a numbered light face."
            )
        triangles = tuple(
            _transform_triangle_full(
                triangle, placement.matrix_row_major
            )
            for triangle in authenticated.decoded.triangles(
                classified.inventory
            )
        )
        minimum_z = min(
            vertex[2] for triangle in triangles for vertex in triangle
        )
        tolerance = 1.0e-8
        points = _unique_points_xy(
            vertex
            for triangle in triangles
            for vertex in triangle
            if abs(vertex[2] - minimum_z) <= tolerance
        )
        hull = _convex_hull(points)
        if len(hull) != 4:
            raise FixtureOcclusionPlanningError(
                "Conventional GLB light face must resolve to one rectangular "
                "lowest-plane aperture."
            )
        area = abs(_signed_area(hull))
        if area <= 0.0:
            raise FixtureOcclusionPlanningError(
                "Conventional GLB light-face area must be positive."
            )
        clockwise = tuple(reversed(hull))
        vertices = tuple((x, y, minimum_z) for x, y in clockwise)
        output.append(
            FixtureEmittingBoundary(
                fixture_id=placement.fixture_id,
                asset_id=placement.asset.asset_id,
                bar_index=int(match.group(1)) - 1,
                node_path=classified.inventory.node_path,
                vertices_m=vertices,
                area_m2=area,
                plane_z_m=minimum_z,
            )
        )
    output.sort(key=lambda item: item.bar_index)
    if (
        len(output) != 8
        or tuple(item.bar_index for item in output) != tuple(range(8))
        or len({round(item.plane_z_m, 9) for item in output}) != 1
    ):
        raise FixtureOcclusionPlanningError(
            "Conventional fixture must expose exactly eight coplanar GLB apertures."
        )
    return tuple(output)


def _scientific_linear_translation(
    viewer_matrix: tuple[float, ...],
) -> tuple[tuple[float, ...], Point3]:
    if len(viewer_matrix) != 16:
        raise FixtureOcclusionPlanningError(
            "viewer placement matrix must contain 16 values."
        )
    # Inverse of catalog scientific_to_viewer: (x, y, z) -> (x, z, -y).
    return (
        (
            viewer_matrix[0],
            viewer_matrix[1],
            viewer_matrix[2],
            -viewer_matrix[8],
            -viewer_matrix[9],
            -viewer_matrix[10],
            viewer_matrix[4],
            viewer_matrix[5],
            viewer_matrix[6],
        ),
        (
            viewer_matrix[3],
            -viewer_matrix[11],
            viewer_matrix[7],
        ),
    )


def _transform_triangle_linear(
    triangle: Triangle,
    linear: tuple[float, ...],
) -> Triangle:
    return tuple(
        (
            linear[0] * point[0]
            + linear[1] * point[1]
            + linear[2] * point[2],
            linear[3] * point[0]
            + linear[4] * point[1]
            + linear[5] * point[2],
            linear[6] * point[0]
            + linear[7] * point[1]
            + linear[8] * point[2],
        )
        for point in triangle
    )  # type: ignore[return-value]


def _transform_triangle_full(
    triangle: Triangle,
    viewer_matrix: tuple[float, ...],
) -> Triangle:
    linear, translation = _scientific_linear_translation(viewer_matrix)
    transformed = _transform_triangle_linear(triangle, linear)
    return tuple(
        (
            point[0] + translation[0],
            point[1] + translation[1],
            point[2] + translation[2],
        )
        for point in transformed
    )  # type: ignore[return-value]


def _shape_radiance_text(
    shape_id: str,
    triangles: tuple[Triangle, ...],
) -> str:
    lines = [
        f"# {OCCLUSION_VERSION_ID}",
        f"# shape_id={shape_id}",
        FIXTURE_BODY_MATERIAL_RAD.rstrip(),
        "",
    ]
    for index, triangle in enumerate(triangles):
        lines.extend(
            (
                f"{FIXTURE_BODY_MATERIAL_NAME} polygon "
                f"{shape_id.replace('-', '_')}_t{index:06d}",
                "0",
                "0",
                "9",
                *(
                    " ".join(_number(value) for value in point)
                    for point in triangle
                ),
                "",
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _instance_radiance_text(
    instances: tuple[FixtureBodyInstancePlan, ...],
    shape_by_id: Mapping[str, FixtureBodyShapePlan],
    *,
    alignment_lattice: Mapping[str, object] | None,
) -> str:
    lines = [
        f"# {OCCLUSION_VERSION_ID}",
        "# Reusable authenticated body sub-octrees; emission remains separate.",
        "",
    ]
    for instance in instances:
        shape = shape_by_id[instance.shape_id]
        lines.extend(
            (
                f"void instance {instance.instance_id}",
                "5 "
                + " ".join(
                    (
                        str(shape.octree_path),
                        "-t",
                        *(
                            _number(value)
                            for value in instance.translation_m
                        ),
                    )
                ),
                "0",
                "0",
                "",
            )
        )
    if alignment_lattice is not None:
        lines.extend((
            "# Permanent standalone alignment lattice; non-emitting and occluding.",
            FIXTURE_BODY_MATERIAL_RAD.rstrip(),
            "",
        ))
        for link in alignment_lattice["links"]:  # type: ignore[index]
            start = link["start_xyz_m"]
            end = link["end_xyz_m"]
            lines.extend((
                f"{FIXTURE_BODY_MATERIAL_NAME} cylinder "
                f"alignment_link_{link['link_index']:05d}",
                "0",
                "0",
                "7 " + " ".join(
                    _number(float(value))
                    for value in (*start, *end, ALIGNMENT_LATTICE_RADIUS_M)
                ),
                "",
            ))
    return "\n".join(lines).rstrip() + "\n"


def _projected_opaque_area(triangles: tuple[Triangle, ...]) -> float:
    # Closed two-sided shells contribute top and bottom projections; halving
    # preserves frame openings and yields the horizontal opaque footprint.
    return 0.25 * math.fsum(
        abs(
            (triangle[1][0] - triangle[0][0])
            * (triangle[2][1] - triangle[0][1])
            - (triangle[1][1] - triangle[0][1])
            * (triangle[2][0] - triangle[0][0])
        )
        for triangle in triangles
    )


def _triangle_area_squared(triangle: Triangle) -> float:
    first = tuple(
        triangle[1][index] - triangle[0][index]
        for index in range(3)
    )
    second = tuple(
        triangle[2][index] - triangle[0][index]
        for index in range(3)
    )
    cross = (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
    return math.fsum(value * value for value in cross) * 0.25


def _bounds(
    triangles: tuple[Triangle, ...],
) -> tuple[Point3, Point3]:
    points = tuple(point for triangle in triangles for point in triangle)
    return (
        tuple(min(point[index] for point in points) for index in range(3)),
        tuple(max(point[index] for point in points) for index in range(3)),
    )  # type: ignore[return-value]


def _validate_catalog_dimensions(
    system_id: str,
    layout_identity: Mapping[str, object],
    full_bounds: tuple[Point3, Point3],
) -> None:
    dimensions = tuple(
        full_bounds[1][axis] - full_bounds[0][axis]
        for axis in range(3)
    )
    if any(not math.isfinite(value) or value <= 0.0 for value in dimensions):
        raise FixtureOcclusionPlanningError(
            "authenticated GLB world-space dimensions are invalid."
        )
    if system_id != "conventional":
        # Proposed catalog authority is the per-module anchor set. The viewer
        # resolver already rejects placements above its 0.25 mm residual gate.
        return
    raw = layout_identity.get("fixture_dimensions_m")
    if not isinstance(raw, Mapping):
        raise FixtureOcclusionPlanningError(
            "Conventional fixture catalog dimensions are missing."
        )
    try:
        expected = (
            float(raw["length_m"]),
            float(raw["width_m"]),
            float(raw["height_m"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FixtureOcclusionPlanningError(
            "Conventional fixture catalog dimensions are malformed."
        ) from exc
    if (
        any(not math.isfinite(value) or value <= 0.0 for value in expected)
        or any(
            not math.isclose(actual, catalog, rel_tol=0.0, abs_tol=2.0e-6)
            for actual, catalog in zip(
                sorted(dimensions[:2]),
                sorted(expected[:2]),
                strict=True,
            )
        )
        or not math.isclose(
            dimensions[2],
            expected[2],
            rel_tol=0.0,
            abs_tol=2.0e-6,
        )
    ):
        raise FixtureOcclusionPlanningError(
            "Conventional authenticated GLB bounds disagree with catalog dimensions."
        )


def _validate_hps_asset_transform_contract(
    authenticated: AuthenticatedFixtureAsset,
    full_bounds: tuple[Point3, Point3],
) -> None:
    asset = authenticated.asset
    if (
        asset.asset_id != "hps-housing-v3"
        or asset.meters_per_asset_unit != 0.001
        or asset.dimension_correction_scale_xyz != (1.0, 1.0, 1.0)
        or asset.placement_plane_local_y_mm != -248.92
        or asset.placement_correction_local_x_mm != 0.0
        or asset.placement_correction_local_y_mm != 248.92
        or asset.placement_correction_local_z_mm != 0.0
        or asset.pivot_contract
        != "housing_bottom_shifted_to_separate_scientific_luminous_aperture_plane"
    ):
        raise FixtureOcclusionPlanningError(
            "HPS full-scale placement contract changed."
        )
    dimensions = tuple(
        full_bounds[1][axis] - full_bounds[0][axis]
        for axis in range(3)
    )
    expected = (
        0.8948975219726563,
        0.6426199951171875,
        0.2489199981689453,
    )
    if any(
        not math.isclose(actual, expected_value, rel_tol=0.0, abs_tol=5.0e-8)
        for actual, expected_value in zip(dimensions, expected, strict=True)
    ):
        raise FixtureOcclusionPlanningError(
            "HPS authenticated full-scale bounds changed."
        )


def _validate_hps_placement_registration(
    placement: FixtureTransportPlacement,
    translation: Point3,
    layout_identity: Mapping[str, object],
) -> None:
    fixtures = layout_identity.get("fixtures")
    if not isinstance(fixtures, list):
        raise FixtureOcclusionPlanningError(
            "HPS authoritative fixture order is missing."
        )
    matches = [
        item
        for item in fixtures
        if isinstance(item, Mapping)
        and item.get("fixture_id") == placement.fixture_id
    ]
    if len(matches) != 1:
        raise FixtureOcclusionPlanningError(
            "HPS placement does not match one authoritative fixture."
        )
    center = matches[0].get("center_m")
    if not isinstance(center, Mapping):
        raise FixtureOcclusionPlanningError(
            "HPS authoritative fixture center is missing."
        )
    try:
        expected_translation = (
            float(center["aligned_x"]),
            float(center["aligned_y"]),
        )
        aperture_z = float(center["aperture_z"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FixtureOcclusionPlanningError(
            "HPS authoritative fixture center is malformed."
        ) from exc
    expected_center = (*expected_translation, aperture_z)
    try:
        validation = validate_hps_publication_transform(
            asset=placement.asset,
            authoritative_matrix_row_major=(
                placement.authoritative_matrix_row_major
            ),
            published_matrix_row_major=placement.matrix_row_major,
            scientific_center_m=expected_center,
            placement_contract_sha256=str(
                placement.placement_contract_sha256
            ),
        )
    except ValueError as exc:
        raise FixtureOcclusionPlanningError(
            "HPS published matrix no longer registers the local "
            "Y=-248.92 mm plane to the scientific aperture plane."
        ) from exc
    expected_translation_from_matrix = (
        placement.matrix_row_major[3],
        -placement.matrix_row_major[11],
        placement.matrix_row_major[7],
    )
    if (
        translation != expected_translation_from_matrix
        or validation.published_outward_normal_scientific
        != (0.0, 0.0, -1.0)
    ):
        raise FixtureOcclusionPlanningError(
            "HPS published matrix no longer registers the local "
            "Y=-248.92 mm plane to the scientific aperture plane."
        )


def _unique_points_xy(
    points: Sequence[Point3] | Any,
) -> tuple[tuple[float, float], ...]:
    output: list[tuple[float, float]] = []
    for point in points:
        xy = (float(point[0]), float(point[1]))
        if not any(math.dist(xy, existing) <= 1.0e-9 for existing in output):
            output.append(xy)
    return tuple(output)


def _convex_hull(
    points: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    ordered = sorted(set(points))
    if len(ordered) < 3:
        return ()

    def cross(
        origin: tuple[float, float],
        left: tuple[float, float],
        right: tuple[float, float],
    ) -> float:
        return (
            (left[0] - origin[0]) * (right[1] - origin[1])
            - (left[1] - origin[1]) * (right[0] - origin[0])
        )

    lower: list[tuple[float, float]] = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 1.0e-15:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 1.0e-15:
            upper.pop()
        upper.append(point)
    return tuple(lower[:-1] + upper[:-1])


def _signed_area(points: tuple[tuple[float, float], ...]) -> float:
    return 0.5 * math.fsum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(points, points[1:] + points[:1])
    )


def _replace_executable(
    command: CommandSpec,
    executable: Path,
) -> CommandSpec:
    return CommandSpec(
        argv=(str(executable), *command.argv[1:]),
        stdin_path=command.stdin_path,
        stdout_path=command.stdout_path,
        stdout_mode=command.stdout_mode,
        cwd=command.cwd,
        env=command.env,
        label=command.label,
    )


def _number(value: float) -> str:
    if abs(value) < 5.0e-15:
        value = 0.0
    return format(value, ".15g")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    "EMITTING_BOUNDARY_POLICY_ID",
    "FixtureBodyInstancePlan",
    "FixtureBodyShapePlan",
    "FixtureEmittingBoundary",
    "FixtureOcclusionPlan",
    "FixtureOcclusionPlanningError",
    "OCCLUSION_VERSION_ID",
    "PROJECTED_AREA_POLICY_ID",
    "TRANSFORM_POLICY_ID",
    "compile_fixture_occlusion",
    "extract_conventional_emitting_boundaries",
    "materialize_fixture_occlusion",
    "plan_fixture_occlusion",
    "proposed_layout_transport_payload",
]
