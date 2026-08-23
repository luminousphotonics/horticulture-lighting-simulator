from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct

import pytest

from fspm_optics.application.proposed import _layout_identity as _run_layout_identity
from fspm_optics.geometry.mounting import MountingGeometry
from fspm_optics.plants import (
    REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID,
    REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID,
    generate_rex_juvenile_preheading_plant,
    plan_natural_fit_layout_from_feet,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.receivers.samples import build_two_sided_patch_receivers
from fspm_optics.transport.scalar_ppfd import PpfdMapSample
from fspm_optics.viewer.artifacts import (
    D3_VIEWER_RESOURCE_VERSION,
    D4_VIEWER_RESOURCE_VERSION,
    LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION,
    MOUNTING_VIEWER_RESOURCE_VERSION,
    PPFD_HEATMAP_VIEWER_RESOURCE_VERSION,
    PROFILE_ID,
    VIEWER_RESOURCE_VERSION,
    build_run_viewer_artifacts,
    _validate_run_context,
    validate_profile_artifacts,
    validate_run_scene_artifacts,
)
from fspm_optics.viewer.gltf import scientific_to_display
from fspm_optics.viewer.models import (
    BinaryDisplayArtifact,
    PpfdHeatmapViewerArtifacts,
    SurfaceFluxViewerArtifacts,
)
from fspm_optics.viewer.ppfd_heatmap import bind_ppfd_heatmap_viewer_artifacts
from fspm_optics.viewer.publish import publish_run_viewer, validate_output_directory

RUN_ID = "a" * 32
LENGTH_FT = 12.5
WIDTH_FT = 7.75


def _run_artifacts(
    system_id: str = "proposed",
    surface_flux: SurfaceFluxViewerArtifacts | None = None,
    mounting_height: dict[str, object] | None = None,
    ppfd_heatmap: PpfdHeatmapViewerArtifacts | None = None,
):
    plan = plan_natural_fit_layout_from_feet(LENGTH_FT, WIDTH_FT)
    natural_fit_sha256 = hashlib.sha256(plan.to_json().encode("utf-8")).hexdigest()
    return (
        build_run_viewer_artifacts(
            run_id=RUN_ID,
            system_id=system_id,
            requested_length_ft=LENGTH_FT,
            requested_width_ft=WIDTH_FT,
            natural_fit=plan,
            natural_fit_artifact_sha256=natural_fit_sha256,
            fixture_catalog_sha256="f" * 64,
            fixture_catalog_byte_length=1234,
            fixture_authoritative_layout_sha256="8" * 64,
            fixture_plan_sha256="e" * 64,
            fixture_count=1,
            fixture_asset_group_count=1,
            mounting_height=mounting_height,
            surface_flux=surface_flux,
            ppfd_heatmap=ppfd_heatmap,
            sampling_profile_id=(
                REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID
                if surface_flux is not None and surface_flux.schema_version < 3
                else REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
            ),
        ),
        plan,
        natural_fit_sha256,
    )


def test_run_viewer_accepts_room_dimensions_above_thirty_feet() -> None:
    plan = plan_natural_fit_layout_from_feet(30.0, 50.0)

    _validate_run_context(
        run_id=RUN_ID,
        system_id="conventional",
        requested_length_ft=30.0,
        requested_width_ft=50.0,
        natural_fit=plan,
        natural_fit_artifact_sha256=hashlib.sha256(
            plan.to_json().encode("utf-8")
        ).hexdigest(),
    )


def _surface_flux_artifacts(
    schema_version: int = 2,
) -> SurfaceFluxViewerArtifacts:
    def artifact(filename: str, data: bytes) -> BinaryDisplayArtifact:
        return BinaryDisplayArtifact(
            filename=filename,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
        )

    patch_values = artifact(
        "surface-flux/patch-values.v1.f32le.bin",
        struct.pack("<4f", 1.0, 2.0, 3.0, 4.0),
    )
    metadata = (
        json.dumps(
            {
                "schema_id": "fspm-optics.surface-flux-display",
                "schema_version": schema_version,
                "display_artifact": {
                    "byte_length": patch_values.byte_size,
                    "filename": patch_values.filename,
                    "sha256": patch_values.sha256,
                },
            },
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    calibration = (
        None
        if schema_version < 3
        else artifact(
            "surface-flux/display-calibration-coefficients.v1.f64le.bin",
            struct.pack("<1536d", *([1.0] * 1536)),
        )
    )
    return SurfaceFluxViewerArtifacts(
        metadata=artifact(
            f"surface-flux/metadata.v{schema_version}.json", metadata
        ),
        patch_values=patch_values,
        calibration_coefficients=calibration,
    )


def _ppfd_heatmap_artifacts(
    *, include_target_coverage: bool = False
) -> PpfdHeatmapViewerArtifacts:
    identity = "1" * 64
    samples = (
        PpfdMapSample(-1.0, -2.0, 0.005, 100.0),
        PpfdMapSample(1.0, -2.0, 0.005, 200.0),
        PpfdMapSample(-1.0, 2.0, 0.005, 300.0),
        PpfdMapSample(1.0, 2.0, 0.005, 400.0),
    )
    scatter = b"".join(
        struct.pack("<3f", item.x_m, item.y_m, item.ppfd_umol_m2_s)
        for item in samples
    )
    scatter_sha256 = hashlib.sha256(scatter).hexdigest()
    metadata = {
        "schema_id": "fspm-optics.ppfd-visualization",
        "schema_version": 1,
        "run_id": RUN_ID,
        "annotations": {},
        "field": {
            "identity_sha256": identity,
            "sample_count": 4,
            "coordinate_system": "right-handed scientific XY, meters, Z-up",
            "reference_plane_z_m": 0.005,
            "value_units": "micromole_per_square_meter_per_second",
        },
        "grid": {
            "kind": "regular",
            "resolution": {"x": 2, "y": 2},
            "x_centers_m": [-1.0, 1.0],
            "y_centers_m": [-2.0, 2.0],
            "reshape_order": "rows_ascending_y_columns_ascending_x",
            "image_origin": "lower",
            "extent_m": {
                "x_min": -2.0,
                "x_max": 2.0,
                "y_min": -4.0,
                "y_max": 4.0,
            },
            "interpolation": {
                "applied": False,
                "method": None,
                "resolution": None,
                "fill_behavior": None,
                "original_sample_identity_sha256": identity,
            },
        },
        "orientation": {
            "sample_coordinates_transformed": False,
            "positive_y_is_image_up": True,
            "layout_axes_swapped_from_requested_room": False,
        },
        "overlay": {},
        "display": {
            "color_limits_ppfd_umol_m2_s": [800.0, 1200.0],
            "colormap": {
                "name": "fspm-viridis-8-linear-srgb-v1",
                "anchors_srgb_8bit": [
                    [68, 1, 84],
                    [70, 50, 126],
                    [54, 92, 141],
                    [39, 127, 142],
                    [31, 161, 135],
                    [74, 193, 109],
                    [160, 218, 57],
                    [253, 231, 37],
                ],
            },
        },
        "scatter": {
            "filename": "ppfd-scatter.f32le.bin",
            "sha256": scatter_sha256,
            "byte_length": len(scatter),
            "byte_order": "little-endian",
            "component_type": "float32",
            "record_layout": "x_m,y_m,ppfd_umol_m2_s",
            "stride_bytes": 12,
            "count": 4,
            "source_field_identity_sha256": identity,
        },
        "artifacts": {
            "ppfd-scatter.f32le.bin": {
                "sha256": scatter_sha256,
                "byte_length": len(scatter),
            }
        },
        "transforms": {
            "physics_recomputed": False,
            "target_rescaling": False,
            "symmetrization": False,
            "smoothing": False,
            "clipping": False,
            "correction": False,
            "hidden_normalization": False,
            "sample_exclusion": False,
        },
    }
    metadata_bytes = json.dumps(metadata, sort_keys=True).encode("utf-8")
    return bind_ppfd_heatmap_viewer_artifacts(
        metadata_bytes=metadata_bytes,
        scatter_bytes=scatter,
        expected_field_identity_sha256=identity,
        target_coverage=(
            _target_coverage_contract(identity)
            if include_target_coverage
            else None
        ),
    )


def _target_coverage_contract(identity: str) -> dict[str, object]:
    return {
        "schema_id": "fspm-optics.viewer-target-coverage",
        "schema_version": 1,
        "availability": "available",
        "unavailable_reason_code": None,
        "target_classification_basis": (
            "canopy_plane_equivalent_incident_ppfd"
        ),
        "target_classification_source": "interpolated_runtime_ppfd_map",
        "scientific_limitation": (
            "Baseline canopy-plane coverage evaluated at authenticated leaf "
            "locations—not measured leaf incident PAR."
        ),
        "system_id": "proposed",
        "reference": {
            "ppfd_umol_m2_s": 1000.0,
            "source": "requested_lighting_target",
            "policy_mode": "automatic",
        },
        "tolerance_ppfd_umol_m2_s": 20.0,
        "deviation_formula": (
            "(coverage_ppfd - reference_ppfd) / tolerance_ppfd"
        ),
        "target_band_deviation": {
            "minimum": -1.0,
            "maximum": 1.0,
            "bounds": "inclusive",
        },
        "palette": {
            "palette_id": "target-coverage-deviation-8-anchor-v1",
            "continuous_interpolation": True,
            "clamp_below_deviation": -4.0,
            "clamp_above_deviation": 6.0,
            "anchors": [
                {"deviation": -4.0, "color_name": "blue", "srgb_hex": "#2563EB"},
                {"deviation": -2.0, "color_name": "cyan", "srgb_hex": "#06B6D4"},
                {"deviation": -1.5, "color_name": "teal", "srgb_hex": "#14B8A6"},
                {"deviation": -1.0, "color_name": "green", "srgb_hex": "#22C55E"},
                {"deviation": 1.0, "color_name": "green", "srgb_hex": "#22C55E"},
                {"deviation": 2.0, "color_name": "yellow-green", "srgb_hex": "#A3E635"},
                {
                    "deviation": 4.0,
                    "color_name": "yellow-orange",
                    "srgb_hex": "#F59E0B",
                },
                {"deviation": 6.0, "color_name": "red", "srgb_hex": "#DC2626"},
            ],
        },
        "sampling": {
            "interpolation_policy_id": (
                "stage_a_regular_grid_bilinear_half_cell_clamp_v1"
            ),
            "method": "manual_four_cell_bilinear",
            "supported_extent": (
                "sample-center rectangle expanded by half a grid step"
            ),
            "edge_band_behavior": "clamp to nearest center",
            "outside_behavior": "target coverage unavailable",
            "support_bounds_m": {
                "x_min": -2.0,
                "x_max": 2.0,
                "y_min": -4.0,
                "y_max": 4.0,
                "bounds": "inclusive",
            },
            "coordinate_transform_policy_id": (
                "requested_room_to_long_axis_x_rigid_rotation_v1"
            ),
            "axes_swapped_from_requested_room": False,
            "field_sampling_mapping": {
                "x": "aligned_x",
                "y": "aligned_y",
            },
        },
        "canonical_leaf_geometry": {
            "centroid_policy_id": (
                "one_sided_triangle_area_weighted_leaf_centroid_v1"
            ),
            "leaf_ordering": "canonical-leaf order",
            "displayed_leaf_ordering": "plant-major; canonical-leaf order",
            "canonical_leaf_count": 12,
            "canonical_leaf_ids": [f"leaf-{index}" for index in range(12)],
            "representative_centroids_simulation_xy_m": [
                [0.0, 0.0] for _ in range(12)
            ],
            "canonical_topology_sha256": "6" * 64,
            "leaf_geometry_identity_sha256": "7" * 64,
            "instance_translation_source": (
                "scene.plant_instances.instance_translations"
            ),
        },
        "parent_source_field_identity_sha256": identity,
    }


def test_run_scene_is_deterministic_and_preserves_the_juvenile_profile() -> None:
    first, _, _ = _run_artifacts()
    second, _, _ = _run_artifacts()

    assert first == second
    assert first.profile_id == PROFILE_ID
    assert (
        first.sampling_profile_id
        == REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
    )
    profile = json.loads(first.profile_manifest.data)
    identity = json.loads(first.identity_map.data)
    scene = json.loads(first.scene_manifest.data)
    assert profile["schema_version"] == 3
    assert identity["schema_version"] == 2
    assert scene["schema_version"] == 2
    assert profile["sampling_profile_id"] == first.sampling_profile_id
    assert identity["sampling_profile_id"] == first.sampling_profile_id
    assert scene["profile"]["sampling_profile_id"] == first.sampling_profile_id
    assert profile["counts"] == {
        "faces": 1920,
        "leaves": 12,
        "patches": 192,
        "receivers": 384,
    }
    assert profile["development"]["approximate_days_after_transplant"] == 9
    assert profile["development"]["phenological_boundary"] == "BBCH 19/pre-41"
    assert "leaf-local area-weighted smooth" in (
        profile["geometry_glb"]["attributes"]["NORMAL"]
    )
    assert "structured leaf parameters" in (
        profile["geometry_glb"]["attributes"]["TEXCOORD_0"]
    )
    assert len(first.receivers.data) == 384 * 6 * 4
    assert scene["viewer_resource_version"] == D4_VIEWER_RESOURCE_VERSION


def test_current_scene_authenticates_resolved_simulation_mounting_height() -> None:
    mounting = MountingGeometry.resolve(18.0).to_payload()
    artifacts, _, _ = _run_artifacts(mounting_height=mounting)
    scene = json.loads(artifacts.scene_manifest.data)
    mounting_sha256 = hashlib.sha256(
        json.dumps(
            mounting,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    assert scene["schema_version"] == 3
    assert scene["viewer_resource_version"] == MOUNTING_VIEWER_RESOURCE_VERSION
    assert scene["mounting_height"] == mounting
    assert scene["fixtures"]["mounting_height_sha256"] == mounting_sha256

    with pytest.raises(ValueError, match="mounting-height provenance"):
        _run_artifacts(
            mounting_height=mounting | {"reference_plane_z_m": 0.0}
        )


def test_historical_heatmap_scene_binds_exact_stage_a_bytes_and_replays() -> None:
    mounting = MountingGeometry.resolve(18.0).to_payload()
    heatmap = _ppfd_heatmap_artifacts()
    artifacts, plan, natural_fit_sha256 = _run_artifacts(
        mounting_height=mounting,
        ppfd_heatmap=heatmap,
    )
    scene = json.loads(artifacts.scene_manifest.data)

    assert scene["schema_version"] == 4
    assert (
        scene["viewer_resource_version"]
        == PPFD_HEATMAP_VIEWER_RESOURCE_VERSION
    )
    assert scene["ppfd_heatmap"] == heatmap.scene_reference
    assert artifacts.ppfd_heatmap is heatmap
    assert heatmap.metadata.data == json.dumps(
        json.loads(heatmap.metadata.data), sort_keys=True
    ).encode("utf-8")
    assert heatmap.scalar_field.data == b"".join(
        struct.pack("<3f", x, y, value)
        for x, y, value in (
            (-1.0, -2.0, 100.0),
            (1.0, -2.0, 200.0),
            (-1.0, 2.0, 300.0),
            (1.0, 2.0, 400.0),
        )
    )
    files = {
        artifacts.instance_translations.filename: artifacts.instance_translations.data,
        heatmap.metadata.filename: heatmap.metadata.data,
        heatmap.scalar_field.filename: heatmap.scalar_field.data,
    }
    validated = validate_run_scene_artifacts(
        artifacts.scene_manifest.data,
        files,
        expected_run_id=RUN_ID,
        expected_system_id="proposed",
        expected_requested_length_ft=LENGTH_FT,
        expected_requested_width_ft=WIDTH_FT,
        expected_natural_fit=plan,
        expected_natural_fit_artifact_sha256=natural_fit_sha256,
        expected_fixture_catalog_sha256="f" * 64,
        expected_fixture_catalog_byte_length=1234,
        expected_fixture_authoritative_layout_sha256="8" * 64,
        expected_fixture_plan_sha256="e" * 64,
        expected_fixture_count=1,
        expected_fixture_asset_group_count=1,
        expected_mounting_height=mounting,
        expected_ppfd_heatmap=heatmap,
    )
    assert validated["ppfd_heatmap"]["source_field_identity_sha256"] == "1" * 64
    altered = dict(files)
    altered[heatmap.scalar_field.filename] = heatmap.scalar_field.data[:-1] + b"x"
    with pytest.raises(ValueError, match="PPFD heatmap artifact"):
        validate_run_scene_artifacts(
            artifacts.scene_manifest.data,
            altered,
            expected_run_id=RUN_ID,
            expected_system_id="proposed",
            expected_requested_length_ft=LENGTH_FT,
            expected_requested_width_ft=WIDTH_FT,
            expected_natural_fit=plan,
            expected_natural_fit_artifact_sha256=natural_fit_sha256,
            expected_fixture_catalog_sha256="f" * 64,
            expected_fixture_catalog_byte_length=1234,
            expected_fixture_authoritative_layout_sha256="8" * 64,
            expected_fixture_plan_sha256="e" * 64,
            expected_fixture_count=1,
            expected_fixture_asset_group_count=1,
            expected_mounting_height=mounting,
            expected_ppfd_heatmap=heatmap,
        )
    wrong_run_metadata = json.loads(heatmap.metadata.data)
    wrong_run_metadata["run_id"] = "0" * 32
    wrong_run_heatmap = bind_ppfd_heatmap_viewer_artifacts(
        metadata_bytes=json.dumps(
            wrong_run_metadata, sort_keys=True
        ).encode("utf-8"),
        scatter_bytes=heatmap.scalar_field.data,
        expected_field_identity_sha256="1" * 64,
    )
    with pytest.raises(ValueError, match="PPFD heatmap run identity"):
        _run_artifacts(
            mounting_height=mounting,
            ppfd_heatmap=wrong_run_heatmap,
        )


def test_target_coverage_heatmap_contract_is_authenticated_and_versioned() -> None:
    target = _target_coverage_contract("1" * 64)
    heatmap = _ppfd_heatmap_artifacts(include_target_coverage=True)
    assert heatmap.scene_reference["target_coverage"] == target
    mounting = MountingGeometry.resolve(18.0).to_payload()
    artifacts, _, _ = _run_artifacts(
        mounting_height=mounting,
        ppfd_heatmap=heatmap,
    )
    scene = json.loads(artifacts.scene_manifest.data)
    assert scene["viewer_resource_version"] == VIEWER_RESOURCE_VERSION
    assert scene["ppfd_heatmap"]["target_coverage"] == target

    metadata = heatmap.metadata.data
    scatter = heatmap.scalar_field.data
    for mutate, message in (
        (
            lambda value: value.update(
                {"parent_source_field_identity_sha256": "0" * 64}
            ),
            "identity",
        ),
        (
            lambda value: value["palette"]["anchors"][4].update(
                {"srgb_hex": "#000000"}
            ),
            "palette",
        ),
        (
            lambda value: value["sampling"]["support_bounds_m"].update(
                {"x_max": 1.0}
            ),
            "interpolation",
        ),
    ):
        altered = json.loads(json.dumps(target))
        mutate(altered)
        with pytest.raises(ValueError, match=message):
            bind_ppfd_heatmap_viewer_artifacts(
                metadata_bytes=metadata,
                scatter_bytes=scatter,
                expected_field_identity_sha256="1" * 64,
                target_coverage=altered,
            )


def test_glb_and_identity_preserve_geometry_picking_contract() -> None:
    artifacts, _, _ = _run_artifacts()
    document, binary_offset = _glb_document(artifacts.geometry.data)
    primitive = document["meshes"][0]["primitives"][0]
    assert primitive["mode"] == 4
    assert "indices" not in primitive
    assert set(primitive["attributes"]) == {
        "NORMAL", "POSITION", "TEXCOORD_0", "_FACE_INDEX", "_LEAF_INDEX",
        "_PATCH_INDEX",
    }
    assert document["materials"][0]["doubleSided"] is True
    plant = generate_rex_juvenile_preheading_plant()
    positions = _attribute_values(
        artifacts.geometry.data, document, binary_offset,
        primitive["attributes"]["POSITION"], count=plant.face_count * 9,
        format_character="f",
    )
    normals = _attribute_values(
        artifacts.geometry.data, document, binary_offset,
        primitive["attributes"]["NORMAL"], count=plant.face_count * 9,
        format_character="f",
    )
    texcoords = _attribute_values(
        artifacts.geometry.data, document, binary_offset,
        primitive["attributes"]["TEXCOORD_0"], count=plant.face_count * 6,
        format_character="f",
    )
    patch_indices = _attribute_values(
        artifacts.geometry.data, document, binary_offset,
        primitive["attributes"]["_PATCH_INDEX"], count=plant.face_count * 3,
        format_character="I",
    )
    face_index = 0
    for leaf in plant.leaves:
        expected_uvs = _expected_leaf_uvs(plant, leaf)
        expected_normals = _expected_leaf_smooth_normals(leaf)
        duplicate_normals: dict[int, tuple[float | int, ...]] = {}
        duplicate_uvs: dict[int, tuple[float | int, ...]] = {}
        for face in leaf.faces:
            start = face_index * 9
            uv_start = face_index * 6
            expected_positions = tuple(
                component for vertex in face.vertices
                for component in scientific_to_display(vertex)
            )
            assert positions[start : start + 9] == pytest.approx(
                expected_positions, abs=1e-8
            )
            edge_a = tuple(
                positions[start + 3 + axis] - positions[start + axis]
                for axis in range(3)
            )
            edge_b = tuple(
                positions[start + 6 + axis] - positions[start + axis]
                for axis in range(3)
            )
            cross = (
                edge_a[1] * edge_b[2] - edge_a[2] * edge_b[1],
                edge_a[2] * edge_b[0] - edge_a[0] * edge_b[2],
                edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0],
            )
            for corner, source_index in enumerate(face.vertex_indices):
                corner_normal = normals[
                    start + corner * 3 : start + corner * 3 + 3
                ]
                corner_uv = texcoords[
                    uv_start + corner * 2 : uv_start + corner * 2 + 2
                ]
                assert corner_normal == pytest.approx(
                    scientific_to_display(expected_normals[source_index]),
                    abs=1e-7,
                )
                assert corner_uv == struct.unpack(
                    "<2f", struct.pack("<2f", *expected_uvs[source_index])
                )
                assert math.isfinite(math.fsum(corner_normal))
                normal_length = math.sqrt(
                    math.fsum(value * value for value in corner_normal)
                )
                assert normal_length == pytest.approx(1.0, abs=2e-7)
                assert math.fsum(
                    left * right for left, right in zip(cross, corner_normal)
                ) > 0.0
                if source_index in duplicate_normals:
                    assert corner_normal == duplicate_normals[source_index]
                    assert corner_uv == duplicate_uvs[source_index]
                duplicate_normals[source_index] = corner_normal
                duplicate_uvs[source_index] = corner_uv
            face_index += 1
        assert duplicate_uvs[0] == (0.0, 0.5)
        assert duplicate_uvs[len(leaf.vertices) - 1] == (1.0, 0.5)
        assert len(duplicate_normals) == len(leaf.vertices)
        assert len(duplicate_uvs) == len(leaf.vertices)
    assert face_index == plant.face_count
    identity = json.loads(artifacts.identity_map.data)
    assert patch_indices == tuple(
        patch_index
        for patch_index in identity["face_to_patch"]
        for _ in range(3)
    )
    receivers = build_two_sided_patch_receivers(plant)
    assert identity["face_ids"] == [face.face_id for face in plant.faces]
    assert identity["patch_ids"] == [patch.patch_id for patch in plant.patches]
    assert identity["receiver_ids"] == [item.receiver_id for item in receivers]
    assert identity["receiver_side"] == ["front", "back"] * 192


def test_profile_hash_validation_and_lazy_receiver_source_are_preserved() -> None:
    artifacts, _, _ = _run_artifacts()
    profile = json.loads(artifacts.profile_manifest.data)
    files = {
        artifacts.geometry.filename: artifacts.geometry.data,
        artifacts.identity_map.filename: artifacts.identity_map.data,
        artifacts.receivers.filename: artifacts.receivers.data,
    }
    assert validate_profile_artifacts(artifacts.profile_manifest.data, files) == profile
    altered = dict(files)
    altered["geometry.glb"] = altered["geometry.glb"][:-1] + b"x"
    with pytest.raises(ValueError, match="SHA-256"):
        validate_profile_artifacts(artifacts.profile_manifest.data, altered)
    records = tuple(struct.iter_unpack("<6f", artifacts.receivers.data))
    assert len(records) == 384
    scientific_receivers = build_two_sided_patch_receivers(
        generate_rex_juvenile_preheading_plant()
    )
    for record, receiver in zip(records, scientific_receivers, strict=True):
        assert record[:3] == pytest.approx(
            scientific_to_display(receiver.point_m), abs=1e-8
        )
        assert record[3:] == pytest.approx(
            scientific_to_display(receiver.normal), abs=1e-7
        )


@pytest.mark.parametrize("system_id", ("proposed", "conventional", "hps"))
def test_scene_ties_exact_run_request_system_and_natural_fit_identity(system_id: str) -> None:
    artifacts, plan, natural_fit_sha256 = _run_artifacts(system_id)
    scene = json.loads(artifacts.scene_manifest.data)
    assert scene["run"] == {"run_id": RUN_ID, "system_id": system_id}
    assert scene["fixtures"] == {
        "asset_group_count": 1,
        "authoritative_layout_sha256": "8" * 64,
        "catalog": "fixtures/catalog.v1.json",
        "catalog_byte_length": 1234,
        "catalog_sha256": "f" * 64,
        "fixture_count": 1,
        "fixture_plan_sha256": "e" * 64,
    }
    assert scene["requested_room"] == {
        "length_ft": LENGTH_FT,
        "length_m": LENGTH_FT * 0.3048,
        "width_ft": WIDTH_FT,
        "width_m": WIDTH_FT * 0.3048,
    }
    assert scene["natural_fit"] == {
        "artifact_role": "natural_fit_layout",
        "artifact_sha256": natural_fit_sha256,
        "ordering": "Y-major/X-minor",
        "plan_hash": plan.plan_hash,
        "plant_count": plan.total_count,
        "policy_id": plan.policy.policy_id,
        "profile_id": plan.profile_id,
    }
    assert scene["plant_instances"]["plant_ids"] == [item.plant_id for item in plan.plants]


@pytest.mark.parametrize("schema_version", (1, 2))
def test_scene_manifest_authenticates_v1_v2_surface_metadata_and_raw_values(
    schema_version: int,
) -> None:
    surface_flux = _surface_flux_artifacts(schema_version=schema_version)
    artifacts, plan, natural_fit_sha256 = _run_artifacts(
        surface_flux=surface_flux
    )
    scene = json.loads(artifacts.scene_manifest.data)

    assert VIEWER_RESOURCE_VERSION == "phase27g-d6-target-coverage-v1"
    assert (
        PPFD_HEATMAP_VIEWER_RESOURCE_VERSION
        == "phase27h-g-ppfd-heatmap-v1"
    )
    assert MOUNTING_VIEWER_RESOURCE_VERSION == "phase27h-b-mounting-height-v1"
    assert D4_VIEWER_RESOURCE_VERSION == "phase27g-d4-viewer-leaf-materials-v1"
    assert D3_VIEWER_RESOURCE_VERSION == "phase27g-d3b-sampling-profile-v1"
    assert LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION == (
        "phase27g-d2-surface-flux-v1"
    )
    assert scene["viewer_resource_version"] == D4_VIEWER_RESOURCE_VERSION
    assert scene["surface_flux"] == {
        "availability": "available",
        "metadata": {
            "byte_length": surface_flux.metadata.byte_size,
            "filename": f"surface-flux/metadata.v{schema_version}.json",
            "sha256": surface_flux.metadata.sha256,
        },
    }
    assert json.loads(surface_flux.metadata.data)["display_artifact"] == {
        "byte_length": surface_flux.patch_values.byte_size,
        "filename": surface_flux.patch_values.filename,
        "sha256": surface_flux.patch_values.sha256,
    }
    assert set(artifacts.scene_files) == {
        artifacts.scene_manifest,
        artifacts.instance_translations,
        surface_flux.metadata,
        surface_flux.patch_values,
    }

    files = {
        artifacts.instance_translations.filename: artifacts.instance_translations.data,
        surface_flux.metadata.filename: surface_flux.metadata.data,
        surface_flux.patch_values.filename: surface_flux.patch_values.data,
    }
    arguments = {
        "expected_run_id": RUN_ID,
        "expected_system_id": "proposed",
        "expected_requested_length_ft": LENGTH_FT,
        "expected_requested_width_ft": WIDTH_FT,
        "expected_natural_fit": plan,
        "expected_natural_fit_artifact_sha256": natural_fit_sha256,
        "expected_fixture_catalog_sha256": "f" * 64,
        "expected_fixture_catalog_byte_length": 1234,
        "expected_fixture_authoritative_layout_sha256": "8" * 64,
        "expected_fixture_plan_sha256": "e" * 64,
        "expected_fixture_count": 1,
        "expected_fixture_asset_group_count": 1,
        "expected_surface_flux": surface_flux,
        "expected_sampling_profile_id": (
            REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID
        ),
    }
    validated = validate_run_scene_artifacts(
        artifacts.scene_manifest.data,
        files,
        **arguments,
    )
    assert validated["surface_flux"] == scene["surface_flux"]
    historical_version = (
        LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION
        if schema_version == 1
        else D3_VIEWER_RESOURCE_VERSION
    )
    historical_scene = scene | {"viewer_resource_version": historical_version}
    assert validate_run_scene_artifacts(
        json.dumps(historical_scene).encode("utf-8"),
        files,
        **arguments,
    )["viewer_resource_version"] == historical_version
    wrong_historical_version = (
        D3_VIEWER_RESOURCE_VERSION
        if schema_version == 1
        else LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION
    )
    with pytest.raises(ValueError, match="historical surface-flux"):
        validate_run_scene_artifacts(
            json.dumps(
                scene | {"viewer_resource_version": wrong_historical_version}
            ).encode("utf-8"),
            files,
            **arguments,
        )

    for filename in (
        surface_flux.metadata.filename,
        surface_flux.patch_values.filename,
    ):
        altered = dict(files)
        altered[filename] = altered[filename][:-1] + b"x"
        with pytest.raises(ValueError, match="surface-flux artifact"):
            validate_run_scene_artifacts(
                artifacts.scene_manifest.data,
                altered,
                **arguments,
            )


def test_optimized_sampling_fails_closed_for_d2_surface_flux() -> None:
    surface_flux = _surface_flux_artifacts()
    plan = plan_natural_fit_layout_from_feet(LENGTH_FT, WIDTH_FT)
    natural_fit_sha256 = hashlib.sha256(plan.to_json().encode("utf-8")).hexdigest()

    with pytest.raises(ValueError, match="uncalibrated optimized"):
        build_run_viewer_artifacts(
            run_id=RUN_ID,
            system_id="proposed",
            requested_length_ft=LENGTH_FT,
            requested_width_ft=WIDTH_FT,
            natural_fit=plan,
            natural_fit_artifact_sha256=natural_fit_sha256,
            fixture_catalog_sha256="f" * 64,
            fixture_catalog_byte_length=1234,
            fixture_authoritative_layout_sha256="8" * 64,
            fixture_plan_sha256="e" * 64,
            fixture_count=1,
            fixture_asset_group_count=1,
            surface_flux=surface_flux,
        )


def test_current_optimized_scene_authenticates_additive_v3_surface_files() -> None:
    surface_flux = _surface_flux_artifacts(schema_version=3)
    mounting = MountingGeometry.resolve(18.0).to_payload()
    artifacts, _plan, _natural_fit_sha256 = _run_artifacts(
        surface_flux=surface_flux,
        mounting_height=mounting,
        ppfd_heatmap=_ppfd_heatmap_artifacts(include_target_coverage=True),
    )
    scene = json.loads(artifacts.scene_manifest.data)
    assert scene["schema_version"] == 4
    assert scene["viewer_resource_version"] == VIEWER_RESOURCE_VERSION
    assert scene["ppfd_heatmap"]["target_coverage"][
        "target_classification_source"
    ] == "interpolated_runtime_ppfd_map"
    assert scene["profile"]["sampling_profile_id"] == (
        REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
    )
    assert scene["surface_flux"]["metadata"]["filename"] == (
        "surface-flux/metadata.v3.json"
    )
    assert {artifact.filename for artifact in surface_flux.files} == {
        "surface-flux/metadata.v3.json",
        "surface-flux/patch-values.v1.f32le.bin",
        "surface-flux/display-calibration-coefficients.v1.f64le.bin",
    }


def test_decimal_rectangular_scene_has_exact_bounds_and_one_compact_buffer() -> None:
    artifacts, plan, natural_fit_sha256 = _run_artifacts()
    scene = validate_run_scene_artifacts(
        artifacts.scene_manifest.data,
        {"instances.f32le.bin": artifacts.instance_translations.data},
        expected_run_id=RUN_ID,
        expected_system_id="proposed",
        expected_requested_length_ft=LENGTH_FT,
        expected_requested_width_ft=WIDTH_FT,
        expected_natural_fit=plan,
        expected_natural_fit_artifact_sha256=natural_fit_sha256,
        expected_fixture_catalog_sha256="f" * 64,
        expected_fixture_catalog_byte_length=1234,
        expected_fixture_authoritative_layout_sha256="8" * 64,
        expected_fixture_plan_sha256="e" * 64,
        expected_fixture_count=1,
        expected_fixture_asset_group_count=1,
    )
    half_length = LENGTH_FT * 0.3048 / 2
    half_width = WIDTH_FT * 0.3048 / 2
    assert scene["room_bounds"] == {
        "minimum_xz": [-half_length, -half_width],
        "maximum_xz": [half_length, half_width],
    }
    assert scene["reference_plane"]["vertices_xyz"] == [
        [-half_length, 0.0, -half_width], [half_length, 0.0, -half_width],
        [half_length, 0.0, half_width], [-half_length, 0.0, half_width],
    ]
    assert scene["camera_bounds"]["minimum_xyz"] == [-half_length, 0.0, -half_width]
    assert scene["camera_bounds"]["maximum_xyz"][::2] == [half_length, half_width]
    assert set(artifacts.scene_files) == {artifacts.scene_manifest, artifacts.instance_translations}
    actual = tuple(struct.iter_unpack("<3f", artifacts.instance_translations.data))
    expected = tuple(
        scientific_to_display((item.requested_x_m, item.requested_y_m, 0.0))
        for item in plan.plants
    )
    assert len(actual) == plan.total_count
    for left, right in zip(actual, expected, strict=True):
        assert left == pytest.approx(right, abs=5e-7)


def test_scene_validation_rejects_wrong_run_plan_and_instance_hash() -> None:
    artifacts, plan, natural_fit_sha256 = _run_artifacts()
    arguments = {
        "expected_run_id": RUN_ID,
        "expected_system_id": "proposed",
        "expected_requested_length_ft": LENGTH_FT,
        "expected_requested_width_ft": WIDTH_FT,
        "expected_natural_fit": plan,
        "expected_natural_fit_artifact_sha256": natural_fit_sha256,
        "expected_fixture_catalog_sha256": "f" * 64,
        "expected_fixture_catalog_byte_length": 1234,
        "expected_fixture_authoritative_layout_sha256": "8" * 64,
        "expected_fixture_plan_sha256": "e" * 64,
        "expected_fixture_count": 1,
        "expected_fixture_asset_group_count": 1,
    }
    with pytest.raises(ValueError, match="identity"):
        validate_run_scene_artifacts(
            artifacts.scene_manifest.data,
            {"instances.f32le.bin": artifacts.instance_translations.data},
            **(arguments | {"expected_run_id": "b" * 32}),
        )
    with pytest.raises(ValueError, match="fixture catalog reference"):
        validate_run_scene_artifacts(
            artifacts.scene_manifest.data,
            {"instances.f32le.bin": artifacts.instance_translations.data},
            **(
                arguments
                | {"expected_fixture_catalog_byte_length": 1235}
            ),
        )
    with pytest.raises(ValueError, match="fixture catalog reference"):
        validate_run_scene_artifacts(
            artifacts.scene_manifest.data,
            {"instances.f32le.bin": artifacts.instance_translations.data},
            **(
                arguments
                | {"expected_fixture_authoritative_layout_sha256": "7" * 64}
            ),
        )
    altered = artifacts.instance_translations.data[:-1] + b"x"
    with pytest.raises(ValueError, match="SHA-256"):
        validate_run_scene_artifacts(
            artifacts.scene_manifest.data, {"instances.f32le.bin": altered}, **arguments
        )


def test_browser_resources_load_one_authoritative_fixture_scene() -> None:
    root = Path(__file__).resolve().parents[1] / "src/fspm_optics/resources/viewer"
    html = (root / "index.html").read_text(encoding="utf-8")
    javascript = (root / "main.js").read_text(encoding="utf-8")
    artifacts_js = (root / "artifacts.js").read_text(encoding="utf-8")
    fixture_artifacts_js = (root / "fixture-artifacts.js").read_text(encoding="utf-8")
    fixture_height_js = (root / "fixture-height-controller.js").read_text(
        encoding="utf-8"
    )
    ppfd_heatmap_js = (root / "ppfd-heatmap.js").read_text(encoding="utf-8")
    fixture_renderer_js = (root / "fixture-renderer.js").read_text(encoding="utf-8")
    surface_flux_js = (root / "surface-flux.js").read_text(encoding="utf-8")
    target_coverage_js = (root / "target-coverage.js").read_text(
        encoding="utf-8"
    )
    css = (root / "styles.css").read_text(encoding="utf-8")
    renderer_js = (root / "renderer.js").read_text(encoding="utf-8")
    combined = "\n".join(
        (
            html, javascript, artifacts_js, fixture_artifacts_js,
            fixture_height_js, ppfd_heatmap_js, fixture_renderer_js,
            renderer_js, surface_flux_js, target_coverage_js, css,
        )
    )
    assert "data-control=\"layout\"" not in html
    assert "populateLayoutSelector" not in javascript and "loadValidatedLayout" not in combined
    assert "scene.v1.json" in javascript and "scene.v2.json" not in combined
    assert "new THREE.InstancedMesh" not in javascript
    assert "createPlantSurface" in javascript and "applyInstanceTranslations" in javascript
    assert "crypto.subtle.digest" in artifacts_js
    assert "createReceiverLayer" in javascript and "ensureNormals" in javascript
    assert "10x10" not in combined and "20x20" not in combined and "30x30" not in combined
    assert "GLTFLoader" in fixture_renderer_js
    assert "loadValidatedFixtureArtifacts" in javascript
    assert "createInspectionEnvironment" in javascript
    assert "scene.background" not in javascript
    assert "fixtures/catalog.v1.json" in artifacts_js
    assert '"phase27g-d4-viewer-leaf-materials-v1"' in artifacts_js
    assert '"phase27h-g-ppfd-heatmap-v1"' in artifacts_js
    assert '"phase27g-d6-target-coverage-v1"' in artifacts_js
    assert '"phase27h-b-mounting-height-v1"' in artifacts_js
    assert '"phase27g-d3b-sampling-profile-v1"' in artifacts_js
    assert '"surface-flux/metadata.v2.json"' in artifacts_js
    assert "data-control=\"fixtures\"" in html
    assert html.count("Leaf Coloring") == 1
    assert "Target Coverage" in html
    assert "Absorbed PAR" not in html
    assert 'value="target_coverage"' not in html
    assert "Incident PAR" not in html
    assert 'data-control="surfaceMetric"' not in html
    assert 'data-control="surfaceSides"' not in html
    assert "Surface view" not in html
    assert ">Both<" not in html
    assert "controls.surfaceMetric" not in javascript
    assert "controls.surfaceSides" not in javascript
    assert 'id="surface-flux-state"' in html and "aria-live=\"polite\" hidden" in html
    assert "surface-flux-provenance" not in html
    assert 'labels: ["Under target", "Target range", "Over target"]' in javascript
    assert "surfaceFluxLegends.replaceChildren()" in javascript
    assert "Metadata schema:" not in javascript
    assert "Classification basis:" not in javascript
    assert "Spatial Uniformity" not in html
    assert "createSurfaceFluxColorController" in javascript
    assert "createTargetCoverageColorController" in javascript
    assert "targetCoverageScalarMap" in target_coverage_js
    assert "new THREE.DataTexture" not in target_coverage_js
    assert "authenticateSurfaceFluxMetadata" in combined
    assert "FRONT_LOCAL_PATCH_ANCHORS" in surface_flux_js
    assert "FRONT_LOCAL_PATCH_PALETTE" in surface_flux_js
    assert "BACK_PALETTE" in surface_flux_js
    assert "gl_InstanceID" in surface_flux_js
    assert "gl_FrontFacing" in surface_flux_js
    assert renderer_js.count("new THREE.InstancedMesh") == 1
    assert renderer_js.count("new THREE.MeshStandardMaterial") == 1
    assert "setLeafScientificOverlayVisible" in javascript
    assert "createPpfdHeatmapController" in javascript
    assert "ppfdHeatmapController?.dispose()" in javascript
    assert "getFloat32(offset, true)" in ppfd_heatmap_js
    assert "new THREE.PlaneGeometry" in ppfd_heatmap_js
    assert "new THREE.ShaderMaterial" in ppfd_heatmap_js
    assert "new THREE.Mesh" in ppfd_heatmap_js
    assert "readRenderTargetPixels" not in ppfd_heatmap_js
    assert "fixture-scale" not in combined
    assert "requestAnimationFrame(render)" in javascript
    assert "requestAnimationFrame(render);\n  };\n  requestAnimationFrame(render)" not in javascript


def test_display_only_fixture_height_controller_contract() -> None:
    root = Path(__file__).resolve().parents[1] / "src/fspm_optics/resources/viewer"
    html = (root / "index.html").read_text(encoding="utf-8")
    css = (root / "styles.css").read_text(encoding="utf-8")
    main = (root / "main.js").read_text(encoding="utf-8")
    controller = (root / "fixture-height-controller.js").read_text(
        encoding="utf-8"
    )
    renderer = (root / "fixture-renderer.js").read_text(encoding="utf-8")
    camera = (root / "camera.js").read_text(encoding="utf-8")

    for removed in (
        "Authorized run scene",
        'id="scene-info"',
        'id="performance"',
        'data-control="footprint"',
        'data-control="reference"',
    ):
        assert removed not in html
    for removed in (
        "performance.now()",
        "renderer.info.render.calls",
        "plantDrawCalls",
        "fixtureDrawCalls",
        "displayedTriangles",
        "displayedReceivers",
        "run_id.slice",
    ):
        assert removed not in main
    for public_layer in (
        "Plant surface",
        "Fixtures",
        "Local bounds",
        "Selected-plant receivers",
        "Receiver normals",
    ):
        assert public_layer in html
    assert "createFootprintDisplay" in main
    assert "createReferencePlaneDisplay" in main
    assert "helpers.footprint.visible = false" in main
    assert "helpers.reference.visible = false" in main

    assert 'for="fixture-height"' in html
    assert 'type="range"' in html and 'step="1"' in html
    assert 'aria-label="Reset fixture display height to simulated height"' in html
    assert "Current height" in html and "display only" in html
    assert ":focus-visible" in css
    assert "mounting.mounting_height_in" in controller
    assert "mounting.reference_plane_z_m" in controller
    assert "mounting.room_ceiling_z_m" in controller
    assert "fixtureBounds?.minimum_xyz" in controller
    assert "fixtureBounds?.maximum_xyz" in controller
    assert "displayHeightIn - contract.simulatedHeightIn" in controller
    assert "fixtureRoot.position.y = displayOffsetIn * METERS_PER_INCH" in controller
    assert 'fixtureRoot?.name !== "authoritative-run-fixtures"' in controller
    assert "METERS_PER_INCH = 0.0254" in controller
    assert "applyIndex(contract.simulatedIndex, false)" in controller
    assert "const reset = () => applyIndex(contract.simulatedIndex, true)" in controller
    assert (
        "const displayOffsetIn = boundedIndex === contract.simulatedIndex\n      ? 0"
        in controller
    )
    assert "absoluteHeightStops" in controller
    assert "simulated - 1" in controller and "simulated + 1" in controller
    assert "historical scene" in controller
    assert "slider.disabled = true" in controller

    assert 'root.name = "authoritative-run-fixtures"' in renderer
    assert "fixtureRoot.position.y" in controller
    assert "setMatrixAt" not in controller and "instanceMatrix" not in controller
    assert "translateFixtureBounds" in controller
    assert "minimum[1] += offsetM" in controller
    assert "maximum[1] += offsetM" in controller
    assert (
        "combineAuthoritativeBounds(artifacts.scene, translatedFixtureBounds)" in main
    )
    assert "cameraSystem.setBounds(" in main and "false," in main
    assert "camera.updateProjectionMatrix()" in camera

    assert 'addEventListener("input", handleInput)' in controller
    assert (
        'slider.dataset.displayHeightIn = formatHeight(displayHeightIn)' in controller
    )
    assert "keydown" not in controller
    assert 'removeEventListener("input", handleInput)' in controller
    assert 'removeEventListener("click", reset)' in controller
    assert "fixtureHeightController?.dispose()" in main
    assert main.count("requestAnimationFrame(") == 1
    for forbidden in (
        "fetch(", "localStorage", "sessionStorage", "XMLHttpRequest",
        'method: "POST"', 'method: "PATCH"', "crypto.subtle", "sha256",
    ):
        assert forbidden not in controller


def test_viewer_empty_click_and_orbit_preserve_plant_selection() -> None:
    main_js = (
        Path(__file__).resolve().parents[1]
        / "src/fspm_optics/resources/viewer/main.js"
    ).read_text(encoding="utf-8")
    click_handler = main_js.split(
        'listen(canvas, "click", (event) => {', 1
    )[1].split("const compact =", 1)[0]

    assert "clearSelection" not in main_js
    assert "selectedInstanceId = null" not in click_handler
    assert "if (suppressSelectionClick)" in click_handler
    assert "Math.hypot" in main_js
    assert "if (!surface.visible) {\n      return;" in click_handler
    assert "if (!hit) {\n      return;" in click_handler
    assert "selectedInstanceId = compact.instanceId" in main_js


def test_repository_and_package_data_have_no_generated_catalog() -> None:
    repository = Path(__file__).resolve().parents[1]
    assert not (repository / "scene.v1.json").exists()
    assert not (repository / "layouts").exists()
    package = (repository / "pyproject.toml").read_text(encoding="utf-8")
    for pattern in (
        "resources/viewer/*.html", "resources/viewer/*.css", "resources/viewer/*.js",
        "resources/viewer/vendor/*.js", "resources/viewer/vendor/addons/controls/*.js",
        "resources/viewer/vendor/addons/environments/*.js",
        "resources/viewer/vendor/addons/loaders/*.js",
        "resources/viewer/vendor/addons/utils/*.js",
        "resources/viewer/fixtures/proposed/*.glb",
        "resources/viewer/fixtures/conventional/*.glb",
        "resources/viewer/fixtures/hps/*.glb",
        "resources/calibration/*.json",
    ):
        assert pattern in package
    calibration = (
        repository
        / "src"
        / "fspm_optics"
        / "resources"
        / "calibration"
        / "surface-flux-front-local-patch-calibration.v1.json"
    )
    assert calibration.is_file()
    assert hashlib.sha256(calibration.read_bytes()).hexdigest() == (
        "6f22972a6ffe41db494f464abafdcad85bc4434bf7c6e326ebabfef6c1008bd3"
    )


def test_publication_output_is_external_atomic_and_has_one_scene_buffer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="absolute"):
        validate_output_directory(Path("viewer"))
    repository_output = Path(__file__).resolve().parents[1] / "viewer-output"
    with pytest.raises(ValueError, match="outside"):
        validate_output_directory(repository_output)
    artifacts, plan, natural_fit_sha256 = _run_artifacts()
    heatmap = _ppfd_heatmap_artifacts()
    mounting = MountingGeometry.resolve(18.0).to_payload()
    output = publish_run_viewer(
        tmp_path / "viewer", run_id=RUN_ID, system_id="proposed",
        requested_length_ft=LENGTH_FT, requested_width_ft=WIDTH_FT,
        natural_fit=plan, natural_fit_artifact_sha256=natural_fit_sha256,
        layout_identity=_proposed_layout_identity(),
        mounting_height=mounting,
        ppfd_heatmap=heatmap,
    )
    assert (output / "scene.v1.json").is_file()
    assert (output / "instances.f32le.bin").is_file()
    assert not (output / "layouts").exists()
    assert [
        path.relative_to(output).as_posix()
        for path in (output / "profiles").rglob("*.glb")
    ] == [f"profiles/{PROFILE_ID}/geometry.glb"]
    assert (output / "fixtures/catalog.v1.json").is_file()
    assert (output / heatmap.metadata.filename).read_bytes() == heatmap.metadata.data
    assert (
        output / heatmap.scalar_field.filename
    ).read_bytes() == heatmap.scalar_field.data
    assert json.loads((output / "scene.v1.json").read_bytes())[
        "ppfd_heatmap"
    ] == heatmap.scene_reference
    assert any((output / "fixtures/assets").glob("*.glb"))
    assert not any(
        "conventional" in path.name or "hps" in path.name
        for path in (output / "fixtures/assets").glob("*.glb")
    )
    import fspm_optics.viewer.publish as publisher
    monkeypatch.setattr(
        publisher, "_validate_published_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("incomplete")),
    )
    failed = tmp_path / "failed-viewer"
    with pytest.raises(ValueError, match="incomplete"):
        publish_run_viewer(
            failed, run_id=RUN_ID, system_id="proposed",
            requested_length_ft=LENGTH_FT, requested_width_ft=WIDTH_FT,
            natural_fit=plan, natural_fit_artifact_sha256=natural_fit_sha256,
            layout_identity=_proposed_layout_identity(),
        )
    assert not failed.exists() and not (tmp_path / ".failed-viewer.staging").exists()
    assert artifacts.scene_manifest.filename == "scene.v1.json"


def _proposed_layout_identity() -> dict[str, object]:
    return _run_layout_identity(generate_proposed_led_layout(LENGTH_FT, WIDTH_FT))


def _expected_leaf_uvs(plant, leaf) -> tuple[tuple[float, float], ...]:
    u_segments, v_segments = plant.config.mesh_segments_for_layer(
        leaf.leaf_layer
    )
    values = [(0.0, 0.5)]
    values.extend(
        (u_index / u_segments, v_index / v_segments)
        for u_index in range(1, u_segments)
        for v_index in range(v_segments + 1)
    )
    values.append((1.0, 0.5))
    assert len(values) == len(leaf.vertices)
    return tuple(values)


def _expected_leaf_smooth_normals(
    leaf,
) -> tuple[tuple[float, float, float], ...]:
    accumulated = [[0.0, 0.0, 0.0] for _ in leaf.vertices]
    for face in leaf.faces:
        first, second, third = (
            leaf.vertices[index] for index in face.vertex_indices
        )
        edge_ab = tuple(second[axis] - first[axis] for axis in range(3))
        edge_ac = tuple(third[axis] - first[axis] for axis in range(3))
        cross = (
            edge_ab[1] * edge_ac[2] - edge_ab[2] * edge_ac[1],
            edge_ab[2] * edge_ac[0] - edge_ab[0] * edge_ac[2],
            edge_ab[0] * edge_ac[1] - edge_ab[1] * edge_ac[0],
        )
        for vertex_index in face.vertex_indices:
            for axis in range(3):
                accumulated[vertex_index][axis] += cross[axis]
    result = []
    for vector in accumulated:
        magnitude = math.sqrt(math.fsum(value * value for value in vector))
        result.append(tuple(value / magnitude for value in vector))
    return tuple(result)


def _glb_document(data: bytes) -> tuple[dict[str, object], int]:
    magic, version, total_length = struct.unpack_from("<III", data)
    assert magic == 0x46546C67 and version == 2 and total_length == len(data)
    json_length, json_type = struct.unpack_from("<II", data, 12)
    assert json_type == 0x4E4F534A
    document = json.loads(data[20 : 20 + json_length].decode("utf-8"))
    binary_header = 20 + json_length
    binary_length, binary_type = struct.unpack_from("<II", data, binary_header)
    assert binary_type == 0x004E4942
    assert binary_header + 8 + binary_length == len(data)
    return document, binary_header + 8


def _attribute_values(
    data: bytes, document: dict[str, object], binary_offset: int,
    accessor_index: int, *, count: int, format_character: str,
) -> tuple[float | int, ...]:
    accessor = document["accessors"][accessor_index]
    view = document["bufferViews"][accessor["bufferView"]]
    offset = binary_offset + view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    return struct.unpack_from(f"<{count}{format_character}", data, offset)
