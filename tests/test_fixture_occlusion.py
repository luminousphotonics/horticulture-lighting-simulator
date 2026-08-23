from __future__ import annotations

import math
from pathlib import Path
import shutil

import numpy as np
import pytest

from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_FIXTURE_PPF_UMOL_S,
    build_conventional_radiance_source_plan,
    plan_conventional_layout_from_feet,
)
from fspm_optics.fixtures.hps import plan_hps_layout_from_feet
from fspm_optics.fixtures.occlusion import (
    FIXTURE_BODY_MATERIAL_ID,
    FIXTURE_BODY_MATERIAL_NAME,
    FIXTURE_BODY_MATERIAL_RAD,
    FixtureOcclusionPlanningError,
    OCCLUSION_VERSION_ID,
    compile_fixture_occlusion,
    load_authenticated_fixture_asset,
    materialize_fixture_occlusion,
    plan_fixture_occlusion,
    proposed_layout_transport_payload,
    validate_compiled_fixture_occlusion,
    validate_complete_classification_manifest,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.optical_stack import APERTURE_SIDE_M
from fspm_optics.geometry.active_domain import ActiveRoomDomain
from fspm_optics.radiance.runner import LocalRunner, RunnerResult
from fspm_optics.transport.basis.planning import plan_isolated_rtrace_basis
from fspm_optics.transport.proposed_uniform import (
    execute_uniform_proposed_stage_a,
    materialize_uniform_proposed_stage_a,
    plan_uniform_proposed_stage_a,
)
from fspm_optics.viewer.fixtures import (
    ASSET_REGISTRY,
    resolve_fixture_transport_placements,
    validate_hps_publication_transform,
)
from fspm_optics.fixtures.occlusion.planning import (
    _scientific_linear_translation,
)


EXPECTED_CLASSIFICATION_COUNTS = {
    "proposed-centerpiece-v1": {
        "external_occluder": 299,
        "existing_optical_stack_duplicate": 5,
        "decorative_exclusion": 4,
        "emitter": 0,
    },
    "proposed-linear2-v1": {
        "external_occluder": 122,
        "existing_optical_stack_duplicate": 2,
        "decorative_exclusion": 2,
        "emitter": 0,
    },
    "proposed-linear3-v1": {
        "external_occluder": 173,
        "existing_optical_stack_duplicate": 3,
        "decorative_exclusion": 2,
        "emitter": 0,
    },
    "proposed-corner3-v1": {
        "external_occluder": 179,
        "existing_optical_stack_duplicate": 3,
        "decorative_exclusion": 2,
        "emitter": 0,
    },
    "proposed-linear4-v1": {
        "external_occluder": 250,
        "existing_optical_stack_duplicate": 4,
        "decorative_exclusion": 2,
        "emitter": 0,
    },
    "proposed-l-v1": {
        "external_occluder": 258,
        "existing_optical_stack_duplicate": 4,
        "decorative_exclusion": 4,
        "emitter": 0,
    },
    "proposed-reverse-l-v1": {
        "external_occluder": 258,
        "existing_optical_stack_duplicate": 4,
        "decorative_exclusion": 4,
        "emitter": 0,
    },
    "proposed-led-module-v1": {
        "external_occluder": 45,
        "existing_optical_stack_duplicate": 1,
        "decorative_exclusion": 0,
        "emitter": 0,
    },
    "conventional-led-8-bar-v1": {
        "external_occluder": 55,
        "existing_optical_stack_duplicate": 0,
        "decorative_exclusion": 0,
        "emitter": 8,
    },
    "hps-housing-v3": {
        "external_occluder": 6,
        "existing_optical_stack_duplicate": 1,
        "decorative_exclusion": 0,
        "emitter": 1,
    },
}


class _BodyCompileRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, command, *, timeout_s=None, stderr_path=None):
        del timeout_s, stderr_path
        self.calls.append(command)
        assert command.stdout_path is not None
        command.stdout_path.write_bytes(
            f"compiled-{len(self.calls)}".encode("ascii")
        )
        return RunnerResult(
            command_label=command.label,
            argv=command.argv,
            returncode=0,
            stdout_path=command.stdout_path,
            stderr_text="",
            stderr_path=None,
            wall_time_s=0.0,
            success=True,
        )


def test_v4_manifest_authenticates_every_supported_asset_and_node() -> None:
    assets = validate_complete_classification_manifest()

    assert tuple(item.asset.asset_id for item in assets) == tuple(
        item.asset_id
        for item in ASSET_REGISTRY
        if item.system_id in {"proposed", "conventional", "hps"}
    )
    assert {
        item.asset.asset_id: item.classification_counts for item in assets
    } == EXPECTED_CLASSIFICATION_COUNTS
    assert assets[-1].asset.asset_id == "hps-housing-v3"

    for asset in assets:
        assert len(asset.primitives) == len(
            asset.decoded.primitive_inventory
        )
        assert {
            (
                item.inventory.node_index,
                item.inventory.primitive_index,
            )
            for item in asset.primitives
        } == {
            (item.node_index, item.primitive_index)
            for item in asset.decoded.primitive_inventory
        }


def test_proposed_deduplication_and_structural_classification_are_explicit() -> None:
    proposed = tuple(
        item
        for item in validate_complete_classification_manifest()
        if item.asset.system_id == "proposed"
    )

    for asset in proposed:
        duplicates = tuple(
            item.inventory.node_path
            for item in asset.primitives
            if item.classification == "existing_optical_stack_duplicate"
        )
        decorative = tuple(
            item.inventory.node_path
            for item in asset.primitives
            if item.classification == "decorative_exclusion"
        )
        external = tuple(
            item.inventory.node_path for item in asset.external_primitives
        )
        assert duplicates
        assert all("opaque_bottom_cover" in path for path in duplicates)
        assert len(duplicates) == len(asset.asset.anchors_m)
        assert any("heatsink_base_plate" in path for path in external)
        if asset.asset.asset_id == "proposed-led-module-v1":
            assert decorative == ()
        else:
            assert decorative
            assert all(
                "spec_label" in path or "logo_plate" in path
                for path in decorative
            )
        assert any("heatsink" in path for path in external)
        assert any("mount" in path for path in external)
        assert any("frame" in path for path in external)
        if asset.asset.asset_id != "proposed-led-module-v1":
            assert any("driver" in path for path in external)


def test_every_proposed_asset_registers_its_glb_light_side_to_the_aperture() -> None:
    proposed = tuple(
        item
        for item in validate_complete_classification_manifest()
        if item.asset.system_id == "proposed"
    )

    for asset in proposed:
        registration = asset.proposed_aperture_registration
        assert registration is not None
        assert asset.asset.pivot_contract == (
            "opaque_bottom_cover_light_side_is_aperture_plane"
        )
        assert asset.asset.placement_plane_local_y_mm == pytest.approx(
            registration.local_plane_y_mm,
            abs=5.0e-7,
        )
        assert len(registration.cover_node_paths) == len(
            asset.asset.anchors_m
        )
        assert registration.local_plane_y_mm > (
            registration.local_cover_inner_y_mm
        )
        assert (
            registration.local_plane_y_mm
            - registration.local_cover_inner_y_mm
        ) == pytest.approx(3.0, abs=0.08)


def test_corrected_registration_keeps_historical_aperture_prisms_clear() -> None:
    proposed = tuple(
        item
        for item in validate_complete_classification_manifest()
        if (
            item.asset.system_id == "proposed"
            and item.asset.asset_id != "proposed-led-module-v1"
        )
    )
    half = APERTURE_SIDE_M / 2.0

    for asset in proposed:
        registration = asset.proposed_aperture_registration
        assert registration is not None
        old_anchor_plane_y_mm = (
            math.fsum(anchor[1] for anchor in asset.asset.anchors_m)
            * 1000.0
            / len(asset.asset.anchors_m)
        )
        pre_repair_intrusions = 0
        post_repair_intrusions = 0
        for primitive in asset.external_primitives:
            for triangle in asset.decoded.triangles(
                primitive.inventory
            ):
                for plane_y_mm, bucket in (
                    (old_anchor_plane_y_mm, "pre"),
                    (registration.local_plane_y_mm, "post"),
                ):
                    if max(point[1] for point in triangle) <= (
                        plane_y_mm + 1.0e-9
                    ):
                        continue
                    for anchor in asset.asset.anchors_m:
                        xs = tuple(
                            point[0] * 0.001 - anchor[0]
                            for point in triangle
                        )
                        ys = tuple(
                            point[2] * 0.001 - anchor[2]
                            for point in triangle
                        )
                        bounds_overlap_aperture = (
                            min(xs) <= half
                            and max(xs) >= -half
                            and min(ys) <= half
                            and max(ys) >= -half
                        )
                        if bounds_overlap_aperture:
                            if bucket == "pre":
                                pre_repair_intrusions += 1
                            else:
                                post_repair_intrusions += 1
                            break
        assert pre_repair_intrusions > 0
        assert post_repair_intrusions == 0


def test_common_body_material_is_exact_shared_anodized_aluminum() -> None:
    assert FIXTURE_BODY_MATERIAL_ID == "fixture_body_anodized_aluminum_v2"
    assert FIXTURE_BODY_MATERIAL_NAME == "fixture_body_anodized_aluminum"
    assert FIXTURE_BODY_MATERIAL_RAD.splitlines()[-4:] == [
        "void metal fixture_body_anodized_aluminum",
        "0",
        "0",
        "5 0.70 0.70 0.70 0.90 0.10",
    ]
    assert "not a product-specific material claim" in (
        FIXTURE_BODY_MATERIAL_RAD
    )


def test_all_supported_systems_emit_one_identical_body_material(
    tmp_path: Path,
) -> None:
    proposed_layout = generate_proposed_led_layout(6.0, 6.0)
    proposed = plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=proposed_layout_transport_payload(proposed_layout),
        output_directory=tmp_path / "proposed",
    )
    conventional_layout = plan_conventional_layout_from_feet(6.0, 6.0)
    conventional = plan_fixture_occlusion(
        system_id="conventional",
        layout_identity=conventional_layout.to_payload(),
        output_directory=tmp_path / "conventional",
    )
    hps_layout = plan_hps_layout_from_feet(6.0, 6.0)
    hps = plan_fixture_occlusion(
        system_id="hps",
        layout_identity=hps_layout.to_payload(),
        output_directory=tmp_path / "hps",
    )

    assert {
        str(plan.scientific_payload()["material"])
        for plan in (proposed, conventional, hps)
    } == {str(proposed.scientific_payload()["material"])}
    for plan in (proposed, conventional, hps):
        assert plan.scientific_payload()["occlusion_version"] == (
            OCCLUSION_VERSION_ID
        )
        assert all(
            shape.source_text.count(FIXTURE_BODY_MATERIAL_RAD) == 1
            for shape in plan.shapes
        )


def test_legacy_absorber_source_cannot_authenticate_as_reflective(
    tmp_path: Path,
) -> None:
    layout = plan_conventional_layout_from_feet(6.0, 6.0)
    plan = plan_fixture_occlusion(
        system_id="conventional",
        layout_identity=layout.to_payload(),
        output_directory=tmp_path / "occlusion",
    )
    shape = plan.shapes[0]
    legacy_absorber = (
        "void plastic fixture_body_absorber\n"
        "0\n"
        "0\n"
        "5 0 0 0 0 0\n"
    )
    stale_source = shape.source_text.replace(
        FIXTURE_BODY_MATERIAL_RAD,
        legacy_absorber,
        1,
    )
    assert stale_source != shape.source_text
    shape.source_path.parent.mkdir(parents=True)
    shape.source_path.write_text(stale_source, encoding="utf-8")

    with pytest.raises(
        FixtureOcclusionPlanningError,
        match="stale fixture occlusion artifact is incompatible",
    ):
        materialize_fixture_occlusion(plan)


def test_conventional_geometry_uses_real_bar_area_and_not_a_bounding_box(
    tmp_path: Path,
) -> None:
    layout = plan_conventional_layout_from_feet(10, 10)
    occlusion = plan_fixture_occlusion(
        system_id="conventional",
        layout_identity=layout.to_payload(),
        output_directory=tmp_path / "occlusion",
    )
    source = build_conventional_radiance_source_plan(
        layout,
        workspace=tmp_path / "source",
        emitting_boundaries=occlusion.emitting_boundaries,
    )

    assert len(occlusion.instances) == len(layout.fixtures) == 4
    assert len(occlusion.emitting_boundaries) == 32
    assert len(source.apertures) == 32
    assert source.emitting_boundary_area_m2_per_fixture == pytest.approx(
        0.17822200689268405
    )
    assert math.fsum(
        item.transported_downward_ppf_umol_s for item in source.apertures
    ) == pytest.approx(
        len(layout.fixtures) * CONVENTIONAL_FIXTURE_PPF_UMOL_S
    )
    assert source.scientific_payload()["transport_approximation"][
        "photometric_boundary"
    ] == "illum"
    assert source.aperture_radiance_text().count(" polygon ") == 32

    shape = occlusion.shapes[0]
    full_dimensions = tuple(
        shape.authenticated_full_glb_bounds_m[1][axis]
        - shape.authenticated_full_glb_bounds_m[0][axis]
        for axis in range(3)
    )
    assert full_dimensions == pytest.approx((1.190, 1.087, 0.108), abs=2e-6)
    body_bbox_area = (
        shape.bounds_m[1][0] - shape.bounds_m[0][0]
    ) * (shape.bounds_m[1][1] - shape.bounds_m[0][1])
    assert 0.0 < shape.projected_opaque_area_m2 < body_bbox_area
    assert shape.projected_opaque_area_m2 != pytest.approx(
        1.190 * 1.087
    )
    assert occlusion.total_external_triangle_instances == 4 * 3528


def test_reusable_body_octree_requires_source_bound_compiled_identity(
    tmp_path: Path,
) -> None:
    layout = plan_conventional_layout_from_feet(10, 10)
    occlusion = plan_fixture_occlusion(
        system_id="conventional",
        layout_identity=layout.to_payload(),
        output_directory=tmp_path / "occlusion",
    )
    materialize_fixture_occlusion(occlusion)
    shape = occlusion.shapes[0]
    shape.octree_path.write_bytes(b"untrusted-stale-octree")
    runner = _BodyCompileRunner()

    assert len(compile_fixture_occlusion(occlusion, runner)) == 1
    assert len(runner.calls) == 1
    assert compile_fixture_occlusion(occlusion, runner) == ()
    assert len(runner.calls) == 1
    validate_compiled_fixture_occlusion(occlusion)

    shape.octree_path.write_bytes(b"tampered-after-compile")
    with pytest.raises(
        FixtureOcclusionPlanningError,
        match="compiled fixture body identity is stale",
    ):
        validate_compiled_fixture_occlusion(occlusion)
    assert len(compile_fixture_occlusion(occlusion, runner)) == 1
    assert len(runner.calls) == 2


@pytest.mark.parametrize(
    ("length_ft", "width_ft", "mode"),
    [
        (10.0, 10.0, "standalone_modules"),
        (12.0, 8.0, "standalone_modules"),
        (8.0, 12.0, "standalone_modules"),
        (6.0, 6.0, "standalone_modules"),
        (10.0, 10.0, "linear"),
        (12.0, 8.0, "linear"),
        (8.0, 12.0, "linear"),
        (6.0, 6.0, "linear"),
        (10.0, 10.0, "legacy"),
        (12.0, 8.0, "legacy"),
    ],
)
def test_proposed_layout_variants_reuse_exact_viewer_transforms(
    tmp_path: Path,
    length_ft: float,
    width_ft: float,
    mode: str,
) -> None:
    layout = generate_proposed_led_layout(
        length_ft,
        width_ft,
        proposed_layout_mode=mode,
    )
    identity = proposed_layout_transport_payload(layout)
    viewer = resolve_fixture_transport_placements("proposed", identity)
    occlusion = plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=identity,
        output_directory=(
            tmp_path / f"{length_ft:g}x{width_ft:g}-{mode}"
        ),
    )

    assert len(occlusion.instances) == len(viewer) == len(layout.fixtures)
    assert [item.fixture_id for item in occlusion.instances] == [
        item.fixture_id for item in viewer
    ]
    assert [item.viewer_matrix_sha256 for item in occlusion.instances] == [
        item.matrix_column_major_sha256 for item in viewer
    ]
    assert occlusion.total_external_triangle_instances > 0
    assert occlusion.total_projected_opaque_area_m2 > 0.0
    assert all(shape.triangle_count > 0 for shape in occlusion.shapes)
    if mode == "standalone_modules":
        assert len(layout.modules) == len(occlusion.instances)
        assert len(occlusion.shapes) == 1
        assert occlusion.shapes[0].asset_id == "proposed-led-module-v1"
        assert occlusion.shapes[0].primitive_count == 45
        assert occlusion.shapes[0].triangle_count == 1292
        assert occlusion.total_external_triangle_instances == (
            len(layout.modules) * occlusion.shapes[0].triangle_count
        )


def test_proposed_aperture_plane_uses_the_viewer_matrix_exactly_once(
    tmp_path: Path,
) -> None:
    layout = generate_proposed_led_layout(10.0, 10.0)
    identity = proposed_layout_transport_payload(layout)
    viewer = resolve_fixture_transport_placements("proposed", identity)
    occlusion = plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=identity,
        output_directory=tmp_path / "exactly-once",
    )
    z_by_fixture = {
        fixture.fixture_id: layout.modules[
            fixture.member_module_indices[0]
        ].z_m
        for fixture in layout.fixtures
    }

    for placement, instance in zip(
        viewer, occlusion.instances, strict=True
    ):
        authenticated = load_authenticated_fixture_asset(
            placement.asset.asset_id
        )
        registration = authenticated.proposed_aperture_registration
        assert registration is not None
        linear, translation = _scientific_linear_translation(
            placement.matrix_row_major
        )
        transformed_plane_z = (
            linear[7] * registration.local_plane_y_mm
            + translation[2]
        )
        assert transformed_plane_z == pytest.approx(
            z_by_fixture[placement.fixture_id],
            abs=1.0e-8,
        )
        assert instance.translation_m == pytest.approx(
            translation, abs=1.0e-15
        )
        assert instance.viewer_matrix_sha256 == (
            placement.matrix_column_major_sha256
        )


def test_aisle_mode_active_layout_keeps_every_proposed_fixture_body(
    tmp_path: Path,
) -> None:
    active_domain = ActiveRoomDomain.from_feet(10.0, 10.0, enabled=True)
    layout = generate_proposed_led_layout(
        active_domain.active_requested_length_ft,
        active_domain.active_requested_width_ft,
    )
    identity = proposed_layout_transport_payload(layout)
    viewer = resolve_fixture_transport_placements("proposed", identity)
    occlusion = plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=identity,
        output_directory=tmp_path / "aisle",
    )

    assert active_domain.active_requested_length_ft == pytest.approx(6.0)
    assert active_domain.active_requested_width_ft == pytest.approx(6.0)
    assert len(occlusion.instances) == len(viewer) == len(layout.fixtures)
    assert [item.viewer_matrix_sha256 for item in occlusion.instances] == [
        item.matrix_column_major_sha256 for item in viewer
    ]
    assert occlusion.total_external_triangle_instances > 0


def test_every_basis_octree_and_uniform_scene_include_the_same_bodies(
    tmp_path: Path,
) -> None:
    layout = generate_proposed_led_layout(8.0, 8.0)
    basis = plan_isolated_rtrace_basis(
        layout=layout,
        sensor_count=4,
        room_height_m=3.048,
        room_source_path=tmp_path / "room.rad",
        sensor_input_path=tmp_path / "sensors.pts",
        output_directory=tmp_path / "basis",
    )
    body_path = str(basis.fixture_occlusion.instance_source_path)
    assert all(
        column.oconv_command.argv[-1] == body_path
        for column in basis.columns
    )
    assert len(
        {
            column.oconv_command.argv[-1]
            for column in basis.columns
        }
    ) == 1

    uniform = plan_uniform_proposed_stage_a(
        layout=layout,
        output_directory=tmp_path / "uniform",
        radiance_options=("-ab", "0"),
    )
    assert str(uniform.fixture_occlusion.instance_source_path) in (
        uniform.oconv_command.argv
    )
    assert (
        uniform.fixture_occlusion.identity_sha256
        == basis.fixture_occlusion.identity_sha256
    )


@pytest.mark.skipif(
    shutil.which("oconv") is None or shutil.which("rtrace") is None,
    reason="Radiance executables are unavailable",
)
def test_exact_10x10_direct_field_is_feasible_without_periodic_mass_blockage(
    tmp_path: Path,
) -> None:
    layout = generate_proposed_led_layout(10.0, 10.0, mount_z_m=0.4622)
    plan = plan_uniform_proposed_stage_a(
        layout=layout,
        output_directory=tmp_path / "exact-10x10-direct",
        radiance_options=(
            "-ab", "0",
            "-aa", "0",
            "-u+",
            "-dc", "1.0",
            "-dj", "0.60",
            "-ds", "0.08",
            "-dt", "0",
            "-dr", "0",
            "-lr", "0",
            "-lw", "1e-5",
        ),
        nthreads=2,
        use_ambient_cache=False,
    )
    materialize_uniform_proposed_stage_a(plan)
    result = execute_uniform_proposed_stage_a(plan, LocalRunner())
    field = result.reference_field
    side = math.isqrt(len(field))
    assert side * side == len(field) == 441
    rows = field.reshape(side, side)
    mean = float(np.mean(field))
    even_row_mean = float(np.mean(rows[::2]))
    odd_row_mean = float(np.mean(rows[1::2]))

    assert mean > 500.0
    assert 0.0 < 500.0 / mean < 1.0
    assert np.count_nonzero(field == 0.0) == 0
    assert min(even_row_mean, odd_row_mean) / max(
        even_row_mean, odd_row_mean
    ) > 0.9


@pytest.mark.parametrize(("length_ft", "width_ft"), [(10.0, 8.0), (8.0, 10.0)])
def test_hps_uses_exact_viewer_placements_and_one_reusable_body_shape(
    tmp_path: Path,
    length_ft: float,
    width_ft: float,
) -> None:
    layout = plan_hps_layout_from_feet(length_ft, width_ft)
    viewer = resolve_fixture_transport_placements("hps", layout.to_payload())
    plan = plan_fixture_occlusion(
        system_id="hps",
        layout_identity=layout.to_payload(),
        output_directory=tmp_path / f"{length_ft:g}x{width_ft:g}",
    )

    assert len(plan.shapes) == 1
    assert len(plan.instances) == len(viewer) == len(layout.fixtures)
    assert plan.shapes[0].primitive_count == 6
    assert plan.shapes[0].triangle_count == 4018
    assert plan.total_external_triangle_instances == 4018 * len(plan.instances)
    assert plan.shapes[0].projected_opaque_area_m2 == pytest.approx(
        0.5712107992482934
    )
    np.testing.assert_allclose(
        np.asarray(plan.shapes[0].bounds_m),
        np.asarray(
            (
                (-0.47198754072875104, -0.3213100128199997, -0.2489200099920108),
                (0.4229100237492567, 0.3213100128199997, 0.0),
            )
        ),
        rtol=0.0,
        atol=1.0e-12,
    )
    assert plan.emitting_boundaries == ()
    assert plan.scientific_payload()["hps_included"] is True
    assert [item.viewer_matrix_sha256 for item in plan.instances] == [
        item.matrix_column_major_sha256 for item in viewer
    ]
    assert plan.shapes[0].source_text.count(FIXTURE_BODY_MATERIAL_RAD) == 1
    assert "hps_bulb_glass" not in plan.shapes[0].source_text
    assert "hps_inner_arc_tube" not in plan.shapes[0].source_text
    authenticated = load_authenticated_fixture_asset("hps-housing-v3")
    triangle_counts = {
        item.inventory.node_path: len(
            authenticated.decoded.triangles(item.inventory)
        )
        for item in authenticated.external_primitives
    }
    assert set(triangle_counts) == {
        "competitor_hps_1000w/reflector_hood/reflector_hood_top_box",
        "competitor_hps_1000w/reflector_hood/reflector_hood_flared_skirt",
        "competitor_hps_1000w/exhaust_flange",
        "competitor_hps_1000w/socket_bracket_assembly/socket_bracket",
        "competitor_hps_1000w/socket_bracket_assembly/ceramic_socket",
        "competitor_hps_1000w/hps_bulb_base",
    }
    assert all(count > 0 for count in triangle_counts.values())
    assert sum(triangle_counts.values()) == plan.shapes[0].triangle_count
    recessed_minimums = {}
    for classified in authenticated.primitives:
        if classified.inventory.node_path.endswith(
            ("/hps_bulb_glass", "/hps_inner_arc_tube")
        ):
            minimum_y = min(
                point[1]
                for triangle in authenticated.decoded.triangles(
                    classified.inventory
                )
                for point in triangle
            )
            recessed_minimums[classified.inventory.node_path] = (
                minimum_y - authenticated.asset.placement_plane_local_y_mm
            ) * 0.001
    assert recessed_minimums == pytest.approx(
        {
            "competitor_hps_1000w/hps_bulb_glass": 0.08592,
            "competitor_hps_1000w/hps_inner_arc_tube": 0.10492,
        },
        abs=1.0e-12,
    )


@pytest.mark.parametrize(
    ("length_ft", "width_ft", "reference_z_m", "mount_height_m"),
    [
        (10.0, 10.0, 0.0, 0.6096),
        (8.0, 12.0, 0.0, 0.6096),
        (12.0, 8.0, 0.0, 0.9144),
        (10.0, 10.0, 0.005, 0.9144),
    ],
)
def test_hps_local_placement_plane_registers_to_every_scientific_aperture(
    tmp_path: Path,
    length_ft: float,
    width_ft: float,
    reference_z_m: float,
    mount_height_m: float,
) -> None:
    layout = plan_hps_layout_from_feet(
        length_ft,
        width_ft,
        reference_plane_z_m=reference_z_m,
        mount_height_m=mount_height_m,
    )
    placements = resolve_fixture_transport_placements(
        "hps", layout.to_payload()
    )
    plan = plan_fixture_occlusion(
        system_id="hps",
        layout_identity=layout.to_payload(),
        output_directory=(
            tmp_path / f"registered-{length_ft:g}x{width_ft:g}-{mount_height_m:g}"
        ),
    )
    scientific_center_by_id = {
        fixture.fixture_id: (
            fixture.aligned_x_m,
            fixture.aligned_y_m,
            fixture.aperture_z_m,
        )
        for fixture in layout.fixtures
    }
    for placement, instance in zip(placements, plan.instances, strict=True):
        linear, translation = _scientific_linear_translation(
            placement.matrix_row_major
        )
        assert linear == pytest.approx(
            (
                0.0010000000474974513,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0010000000474974513,
                0.0,
                0.0010000000474974513,
                0.0,
            ),
            abs=0.0,
        )
        assert translation == pytest.approx(instance.translation_m, abs=0.0)
        validation = validate_hps_publication_transform(
            asset=placement.asset,
            authoritative_matrix_row_major=(
                placement.authoritative_matrix_row_major
            ),
            published_matrix_row_major=placement.matrix_row_major,
            scientific_center_m=scientific_center_by_id[placement.fixture_id],
            placement_contract_sha256=str(
                placement.placement_contract_sha256
            ),
        )
        registered_z = translation[2] + linear[7] * -248.92
        assert abs(
            registered_z - scientific_center_by_id[placement.fixture_id][2]
        ) <= validation.maximum_float32_error_bound_m
        assert validation.published_outward_normal_scientific == (
            0.0,
            0.0,
            -1.0,
        )
    assert plan.shapes[0].bounds_m[0][0] < -plan.shapes[0].bounds_m[1][0]


def test_hps_publication_is_stable_across_cold_warm_and_reordered_planning(
    tmp_path: Path,
) -> None:
    default_layout = plan_hps_layout_from_feet(10.0, 10.0)
    raised_layout = plan_hps_layout_from_feet(
        10.0,
        10.0,
        mount_height_m=0.9144,
    )
    load_authenticated_fixture_asset.cache_clear()
    cold = plan_fixture_occlusion(
        system_id="hps",
        layout_identity=raised_layout.to_payload(),
        output_directory=tmp_path / "cold-raised",
    )
    default = plan_fixture_occlusion(
        system_id="hps",
        layout_identity=default_layout.to_payload(),
        output_directory=tmp_path / "warm-default",
    )
    warm = plan_fixture_occlusion(
        system_id="hps",
        layout_identity=raised_layout.to_payload(),
        output_directory=tmp_path / "warm-raised",
    )
    load_authenticated_fixture_asset.cache_clear()
    cold_again = plan_fixture_occlusion(
        system_id="hps",
        layout_identity=raised_layout.to_payload(),
        output_directory=tmp_path / "cold-again-raised",
    )

    assert cold.transform_set_sha256 == warm.transform_set_sha256 == (
        cold_again.transform_set_sha256
    )
    assert cold.identity_sha256 == warm.identity_sha256 == (
        cold_again.identity_sha256
    )
    assert cold.instances == warm.instances == cold_again.instances
    assert cold.identity_sha256 != default.identity_sha256
    assert cold.asset_provenance[0]["placement_contract_sha256"] == (
        "d83577f6bb7503ff8865dbe55122bfd37d5ea0e0f2c1d9dc130a7a98f9598293"
    )
