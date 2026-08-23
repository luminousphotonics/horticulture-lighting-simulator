from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest

from fspm_optics.diagnostics.proposed_cob_audit import (
    AngularSampleGrid,
    ProposedCobAuditError,
    angular_metrics,
    artifact_with_identity,
    current_source_identities,
    inventory_tree,
    receiver_capture_for_modules,
    validate_direct_pairs,
    verify_artifact_identity,
)
from fspm_optics.fixtures.proposed_cob.source import (
    COB_SOURCE_MODE,
    NATIVE_SOURCE_MODE,
    resolve_proposed_source_authority,
)


def _grid(
    *,
    polar_step_deg: int = 1,
    azimuths: tuple[float, ...] = (0.0, 45.0, 90.0),
    modulation: float = 0.0,
) -> AngularSampleGrid:
    angles = tuple(float(value) for value in range(0, 91, polar_step_deg))
    if angles[-1] != 90.0:
        angles = (*angles, 90.0)
    weights = (
        (0.25, 0.5, 0.25)
        if len(azimuths) == 3
        else tuple(1.0 / len(azimuths) for _ in azimuths)
    )
    rows = tuple(
        tuple(
            max(math.cos(math.radians(theta)), 0.0)
            * (
                1.0
                + modulation
                * math.cos(4.0 * math.radians(phi))
            )
            for phi in azimuths
        )
        for theta in angles
    )
    return AngularSampleGrid(
        source_mode=NATIVE_SOURCE_MODE,
        angles_deg=angles,
        azimuths_deg=azimuths,
        azimuth_weights=weights,
        far_field_distance_m=20.0,
        raw_radiant_intensity=rows,
        boundary_policy="synthetic",
    )


def test_exact_solid_angle_integration_moments_and_fwhm() -> None:
    metrics = angular_metrics(_grid())
    assert metrics["integrated_angular_ppf_umol_s"] == pytest.approx(
        math.pi, rel=2.0e-4
    )
    assert metrics["completed_aperture_fwhm_deg"] == pytest.approx(
        120.0, abs=0.02
    )
    assert metrics["flux_weighted_expected_cos_theta"] == pytest.approx(
        2.0 / 3.0, abs=2.0e-4
    )
    assert metrics["flux_weighted_expected_cos2_theta"] == pytest.approx(
        0.5, abs=2.0e-4
    )


def test_off_axis_fractions_and_containment_angles() -> None:
    metrics = angular_metrics(_grid())
    fractions = metrics["energy_fraction_beyond_polar_angle_deg"]
    assert fractions["30.0"] == pytest.approx(0.75, abs=2.0e-4)
    assert fractions["45.0"] == pytest.approx(0.5, abs=2.0e-4)
    assert fractions["60.0"] == pytest.approx(0.25, abs=2.0e-4)
    assert fractions["75.0"] == pytest.approx(
        math.cos(math.radians(75.0)) ** 2, abs=2.0e-4
    )
    containment = metrics["containment_polar_angle_deg"]
    for percent in (50, 80, 90, 95):
        expected = math.degrees(
            math.acos(math.sqrt(1.0 - percent / 100.0))
        )
        assert containment[str(percent)] == pytest.approx(expected, abs=0.03)


def test_azimuthal_metrics_retain_non_axisymmetric_samples() -> None:
    metrics = angular_metrics(_grid(modulation=0.2))
    variation = metrics["azimuthal_variation"]
    assert variation["intensity_weighted_mean_cv"] > 0.1
    assert variation["maximum_finite_maximum_to_minimum"] == pytest.approx(
        1.5
    )
    assert metrics["integration"]["axisymmetry_assumed"] is False


def test_receiver_acceptance_synthetic_distribution() -> None:
    grid = _grid()
    broad = receiver_capture_for_modules(
        grid,
        ((0.0, 0.0),),
        bounds_xy=(-100.0, 100.0, -100.0, 100.0),
        mounting_height_m=0.4572,
        edge_flags=(False,),
    )
    restricted = receiver_capture_for_modules(
        grid,
        ((0.0, 0.0),),
        bounds_xy=(-0.25, 0.25, -0.25, 0.25),
        mounting_height_m=0.4572,
        edge_flags=(True,),
    )
    assert broad["geometric_receiver_domain_capture_fraction"] == pytest.approx(
        1.0
    )
    assert 0.0 < restricted["geometric_receiver_domain_capture_fraction"] < 1.0
    assert (
        restricted["predicted_completed_aperture_ppf_for_750_umol_m2_s"]
        > 750.0 * 0.25
    )
    assert restricted["edge_modules"]["count"] == 1


def test_native_and_cob_authorities_remain_separate() -> None:
    native = resolve_proposed_source_authority(NATIVE_SOURCE_MODE)
    cob = resolve_proposed_source_authority(COB_SOURCE_MODE)
    assert native.source_mode != cob.source_mode
    assert native.angular_law is None
    assert cob.angular_law is not None
    assert native.emitter_shape != cob.emitter_shape
    identities = current_source_identities()
    assert identities["ies_sha256"].startswith("365c1615")
    assert identities["frozen_completed_profile_sha256"].startswith("9147605")
    assert identities["frozen_characterization_identity_sha256"].startswith(
        "f68459"
    )


def test_read_only_inventory_and_symlink_rejection(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    evidence = archive / "evidence.bin"
    evidence.write_bytes(b"certified")
    evidence.chmod(0o444)
    before = inventory_tree(archive)
    after = inventory_tree(archive)
    assert before.identity_sha256 == after.identity_sha256
    assert before.records == after.records
    link = archive / "unsafe-link"
    link.symlink_to(evidence)
    with pytest.raises(ProposedCobAuditError, match="symlink"):
        inventory_tree(archive)


def test_artifact_identity_rejects_tampering() -> None:
    artifact = artifact_with_identity(
        {"schema_id": "test", "schema_version": 1, "value": 2.6}
    )
    verify_artifact_identity(artifact)
    tampered = copy.deepcopy(artifact)
    tampered["value"] = 2.7
    with pytest.raises(ProposedCobAuditError, match="identity mismatch"):
        verify_artifact_identity(tampered)


def _direct_case(scale: float) -> dict[str, object]:
    return {
        "manifest": {
            "layout": {"identity": "same"},
            "proposed_layout": {"module_centers": [[0.0, 0.0]]},
            "active_domain": {"identity": "same"},
            "mounting_height": {"height": 18.0},
            "room": {"length": 10.0},
            "quality": {"id": "direct"},
            "proposed_control": {
                "mode": "uniform_module_dimming",
                "basis_matrix_solver_enabled": False,
            },
            "engine_provenance": {
                "fixture_occlusion": {"identity_sha256": "a" * 64}
            },
        },
        "metrics": {
            "full_output_mean_ppfd_umol_m2_s": 1000.0 / scale,
            "dimming_factor": 0.75 * scale,
            "minimum_ppfd_umol_m2_s": 700.0,
            "maximum_ppfd_umol_m2_s": 800.0,
            "standard_deviation_ppfd_umol_m2_s": 20.0,
            "cv_percent": 2.0,
            "achieved_mean_ppfd_umol_m2_s": 750.0,
            "ppf": {"emitted_umol_s": 5000.0 * scale},
            "power": {"effective_w": 5000.0 * scale / 2.6},
        },
        "validator": {
            "coordinate_quadrature": {
                "integrated_receiver_flux_umol_s": 4000.0
            },
            "flux_closure": {
                "receiver_to_emitted_ppf_ratio": 0.8 / scale
            },
        },
    }


def test_direct_paired_case_invariants_and_source_difference() -> None:
    records = {}
    for room in ("10x10_aisle_off", "30x50_aisle_on"):
        records[f"{room}__{NATIVE_SOURCE_MODE}"] = _direct_case(1.0)
        records[f"{room}__{COB_SOURCE_MODE}"] = _direct_case(1.1)
    result = validate_direct_pairs(records)
    assert all(
        all(room["invariants"].values()) for room in result.values()
    )
    assert result["10x10_aisle_off"]["metrics"][
        "emitted_completed_aperture_ppf_umol_s"
    ]["cob_to_native_ratio"] == pytest.approx(1.1)
    changed = copy.deepcopy(records)
    changed[f"10x10_aisle_off__{COB_SOURCE_MODE}"]["manifest"]["room"] = {
        "length": 11.0
    }
    with pytest.raises(ProposedCobAuditError, match="paired invariants"):
        validate_direct_pairs(changed)
