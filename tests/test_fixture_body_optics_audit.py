from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from fspm_optics.diagnostics.fixture_body_optics_audit import (
    COARSE_SAMPLING,
    FINE_SAMPLING,
    FixtureBodyOpticsAuditError,
    ZERO_ABSORBER,
    compute_field_statistics,
    convergence_statistics,
    deterministic_trace_options,
    diagnostic_material_by_id,
    field_symmetry_diagnostics,
    geometry_variant_instance_text,
    plan_diagnostic_body_sources,
    preflight_new_artifact_root,
    replace_shape_material,
    signed_field_symmetry_maps,
    symmetry_residual,
    verify_sha256_inventory,
    write_sha256_inventory,
)
from fspm_optics.fixtures.occlusion import (
    FIXTURE_BODY_MATERIAL_RAD,
    plan_fixture_occlusion,
    proposed_layout_transport_payload,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout


def _proposed_plan(tmp_path: Path):
    layout = generate_proposed_led_layout(
        10.0,
        10.0,
        proposed_layout_mode="linear",
    )
    return plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=proposed_layout_transport_payload(layout),
        output_directory=tmp_path / "production",
    )


def test_diagnostic_material_replacement_changes_only_material_block(
    tmp_path: Path,
) -> None:
    plan = _proposed_plan(tmp_path)
    production = plan.shapes[0].source_text
    material = diagnostic_material_by_id("aluminum_like_70")
    changed = replace_shape_material(production, material)

    assert FIXTURE_BODY_MATERIAL_RAD not in changed
    assert material.radiance_text in changed
    assert production.replace(FIXTURE_BODY_MATERIAL_RAD, "") == changed.replace(
        material.radiance_text, ""
    )
    assert changed.count(" polygon ") == production.count(" polygon ")


def test_diagnostic_shape_paths_are_separated_by_material_identity(
    tmp_path: Path,
) -> None:
    plan = _proposed_plan(tmp_path)
    black = plan_diagnostic_body_sources(
        plan,
        material=ZERO_ABSORBER,
        output_directory=tmp_path / "black",
        oconv_bin="oconv",
    )
    diffuse = plan_diagnostic_body_sources(
        plan,
        material=diagnostic_material_by_id("neutral_diffuse_50"),
        output_directory=tmp_path / "diffuse",
        oconv_bin="oconv",
    )

    assert black.root != diffuse.root
    assert set(black.shape_sources) == set(diffuse.shape_sources)
    assert all(
        black.shape_sources[shape_id] != diffuse.shape_sources[shape_id]
        for shape_id in black.shape_sources
    )
    assert all(
        black.shape_octrees[shape_id] != diffuse.shape_octrees[shape_id]
        for shape_id in black.shape_octrees
    )


def test_geometry_variants_isolate_centerpiece_and_linear_bodies(
    tmp_path: Path,
) -> None:
    plan = _proposed_plan(tmp_path)
    diagnostic = plan_diagnostic_body_sources(
        plan,
        material=ZERO_ABSORBER,
        output_directory=tmp_path / "diagnostic",
        oconv_bin="oconv",
    )
    no_body = geometry_variant_instance_text(plan, diagnostic, "no_bodies")
    all_body = geometry_variant_instance_text(plan, diagnostic, "all_bodies")
    centerpiece = geometry_variant_instance_text(
        plan, diagnostic, "centerpiece_only"
    )
    linear2 = geometry_variant_instance_text(plan, diagnostic, "linear2_only")

    assert no_body.count("void instance") == 0
    assert all_body.count("void instance") == 29
    assert centerpiece.count("void instance") == 1
    assert linear2.count("void instance") == 28
    assert "fixture_body_instance_00000" in centerpiece
    assert "fixture_body_instance_00000" not in linear2


def test_trace_options_match_declared_deterministic_direct_contract() -> None:
    assert deterministic_trace_options(
        ambient_bounces=0,
        sampling=FINE_SAMPLING,
        ambient_cache=None,
    ) == (
        "-ab",
        "0",
        "-u-",
        "-dj",
        "0",
        "-ds",
        "0",
        "-dt",
        "0",
        "-dc",
        "1",
    )
    indirect = deterministic_trace_options(
        ambient_bounces=5,
        sampling=COARSE_SAMPLING,
        ambient_cache="/tmp/isolated.amb",
    )
    assert indirect[:13] == (
        "-ab",
        "5",
        "-u-",
        "-dj",
        "0",
        "-ds",
        "0",
        "-dt",
        "0",
        "-dc",
        "1",
        "-ad",
        "64",
    )
    assert indirect[-2:] == ("-af", "/tmp/isolated.amb")


def test_field_symmetry_diagnostics_preserve_signed_direction() -> None:
    coordinates = np.asarray(
        [
            (x, y, 0.1)
            for y in (-1.0, 0.0, 1.0)
            for x in (-1.0, 0.0, 1.0)
        ],
        dtype=np.float64,
    )
    field = np.asarray(
        [
            1.0, 2.0, 3.0,
            4.0, 5.0, 6.0,
            9.0, 10.0, 11.0,
        ],
        dtype=np.float64,
    )

    maps = signed_field_symmetry_maps(
        field, resolution_x=3, resolution_y=3
    )
    diagnostics = field_symmetry_diagnostics(
        field,
        coordinates,
        resolution_x=3,
        resolution_y=3,
    )

    assert diagnostics["upper_minus_lower_mean"] == pytest.approx(8.0)
    assert diagnostics["right_minus_left_mean"] == pytest.approx(2.0)
    assert diagnostics["maximum_location_m"] == [1.0, 1.0, 0.1]
    assert diagnostics["minimum_location_m"] == [-1.0, -1.0, 0.1]
    assert maps["x_mirror"][0].tolist() == [-2.0, 0.0, 2.0]
    assert maps["y_mirror"][:, 0].tolist() == [-8.0, 0.0, 8.0]
    assert diagnostics["rotation_180"]["rms"] == pytest.approx(
        np.sqrt(np.mean(np.square(maps["rotation_180"])))
    )


def test_field_statistics_report_radial_and_symmetry_metrics() -> None:
    axis = np.linspace(-1.0, 1.0, 5)
    coordinates = np.asarray(
        [(x, y, 0.005) for y in axis for x in axis], dtype=float
    )
    radius = np.hypot(coordinates[:, 0], coordinates[:, 1])
    field = 100.0 + 10.0 * radius
    statistics = compute_field_statistics(
        field,
        coordinates,
        resolution_x=5,
        resolution_y=5,
        receiver_length_m=2.0,
        receiver_width_m=2.0,
    )

    assert statistics.sensor_count == 25
    assert statistics.exact_center == pytest.approx(100.0)
    assert statistics.annulus_minus_center > 0.0
    assert statistics.symmetry_180["maximum_absolute"] == pytest.approx(0.0)
    assert statistics.symmetry_90 is not None
    assert statistics.symmetry_90["maximum_absolute"] == pytest.approx(0.0)
    assert statistics.integrated_receiver_plane_ppfd_umol_s == pytest.approx(
        np.mean(field) * 4.0
    )


def test_symmetry_residual_and_convergence_reject_unconverged_field() -> None:
    left = np.asarray([[1.0, 2.0], [3.0, 4.0]])
    residual = symmetry_residual(left, np.flip(left, axis=(0, 1)))
    assert residual["mean_absolute"] == pytest.approx(2.0)
    convergence = convergence_statistics(
        np.asarray([80.0, 80.0]),
        np.asarray([100.0, 100.0]),
    )
    assert convergence["passed"] is False


def test_artifact_preflight_rejects_existing_output_and_symlink(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FixtureBodyOpticsAuditError, match="existing"):
        preflight_new_artifact_root(existing)
    target = tmp_path / "target"
    target.mkdir()
    symlink = tmp_path / "link"
    symlink.symlink_to(target, target_is_directory=True)
    with pytest.raises(FixtureBodyOpticsAuditError, match="symlink"):
        preflight_new_artifact_root(symlink)


def test_artifact_inventory_rejects_tampering_and_symlinks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifact"
    root.mkdir()
    payload = root / "report.json"
    payload.write_text('{"ok": true}\n', encoding="utf-8")
    write_sha256_inventory(root)
    verify_sha256_inventory(root)

    payload.write_text('{"ok": false}\n', encoding="utf-8")
    with pytest.raises(FixtureBodyOpticsAuditError, match="does not match"):
        verify_sha256_inventory(root)

    (root / "SHA256SUMS").unlink()
    payload.write_text('{"ok": true}\n', encoding="utf-8")
    (root / "unsafe").symlink_to(payload)
    with pytest.raises(FixtureBodyOpticsAuditError, match="symlink"):
        write_sha256_inventory(root)
