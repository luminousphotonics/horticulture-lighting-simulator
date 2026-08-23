from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from itertools import combinations, product
import json
import math
from pathlib import Path

import pytest

from fspm_optics.diagnostics import receiver_placement as diagnostic
from fspm_optics.diagnostics import receiver_placement_cli as cli
from fspm_optics.plants import (
    build_juvenile_natural_fit_scene,
    generate_rex_juvenile_preheading_plant,
    plan_natural_fit_layout_from_feet,
)
from fspm_optics.plants.models import ScientificMeshFace
from fspm_optics.receivers.samples import build_two_sided_patch_receivers


ROOT = Path(__file__).parents[1]


def _face(
    face_id: str,
    face_index: int = 0,
    *,
    z_m: float = 0.0,
) -> ScientificMeshFace:
    return ScientificMeshFace(
        plant_id="plant",
        leaf_id="leaf",
        leaf_rank=1,
        leaf_layer="outer",
        face_id=face_id,
        face_index=face_index,
        vertex_indices=(0, 1, 2),
        vertices=(
            (0.0, 0.0, z_m),
            (1.0, 0.0, z_m),
            (0.0, 1.0, z_m),
        ),
        centroid=(1.0 / 3.0, 1.0 / 3.0, z_m),
        unit_normal=(0.0, 0.0, 1.0),
        area_m2=0.5,
    )


def _assert_candidate_partition(
    candidate: diagnostic.LayoutCandidate,
    grid: tuple[tuple[float, ...], ...],
) -> None:
    assert len(candidate.regions) == 16
    assert [region.patch_index for region in candidate.regions] == list(range(16))
    assert [
        (region.u_band, region.v_band) for region in candidate.regions
    ] == [(u_band, v_band) for u_band in range(4) for v_band in range(4)]

    assigned = [
        coordinate
        for region in candidate.regions
        for coordinate in region.cell_coordinates
    ]
    expected = {
        (u_index, v_index)
        for u_index in range(len(grid))
        for v_index in range(len(grid[0]))
    }
    assert len(assigned) == len(set(assigned))
    assert set(assigned) == expected

    for region in candidate.regions:
        assert region.cell_coordinates
        u_indices = {coordinate[0] for coordinate in region.cell_coordinates}
        v_indices = {coordinate[1] for coordinate in region.cell_coordinates}
        assert u_indices == set(range(min(u_indices), max(u_indices) + 1))
        assert v_indices == set(range(min(v_indices), max(v_indices) + 1))
        assert set(region.cell_coordinates) == set(product(u_indices, v_indices))

    source_area = math.fsum(value for row in grid for value in row)
    candidate_area = math.fsum(region.area_m2 for region in candidate.regions)
    assert candidate_area == pytest.approx(source_area, rel=1e-15, abs=1e-18)


def _artifact_snapshot(path: Path) -> dict[str, bytes]:
    return {
        str(file.relative_to(path)): file.read_bytes()
        for file in sorted(path.rglob("*"))
        if file.is_file()
    }


def _bracketing(
    *,
    centroid_distance_m: float = 0.002,
    front_distance_m: float = 0.01,
    back_distance_m: float = -0.02,
    front_direction: tuple[float, float, float] = (0.0, 0.0, 1.0),
    back_direction: tuple[float, float, float] = (0.0, 0.0, -1.0),
) -> diagnostic.CarrierPlaneBracketingMeasurement:
    anchor = (0.25, -0.5, 0.75)
    return diagnostic.measure_carrier_plane_bracketing(
        current_centroid=(anchor[0], anchor[1], anchor[2] + centroid_distance_m),
        surface_anchor=anchor,
        carrier_normal=(0.0, 0.0, 1.0),
        front_origin=(anchor[0], anchor[1], anchor[2] + front_distance_m),
        back_origin=(anchor[0], anchor[1], anchor[2] + back_distance_m),
        front_direction=front_direction,
        back_direction=back_direction,
    )


def _small_representative_report() -> dict[str, object]:
    measurement = _bracketing()
    grid = tuple(tuple(1.0 for _ in range(4)) for _ in range(4))
    candidate = diagnostic.best_global_rectilinear_candidate(grid)
    return {
        "schema_id": diagnostic.DIAGNOSTIC_SCHEMA_ID,
        "schema_version": diagnostic.DIAGNOSTIC_SCHEMA_VERSION,
        "carrier_plane_bracketing": measurement.to_dict(),
        "carrier_plane_summary": (
            diagnostic.summarize_carrier_plane_bracketing((measurement,))
        ),
        "representative_candidate": {
            "u_cuts": list(candidate.u_cuts),
            "v_cuts_by_u_band": [
                list(cuts) for cuts in candidate.v_cuts_by_u_band
            ],
            "objective": [*candidate.objective[:4], list(candidate.objective[4])],
        },
    }


@pytest.fixture(scope="module")
def canonical_diagnostic() -> dict[str, object]:
    plant = generate_rex_juvenile_preheading_plant()
    plant_before = deepcopy(plant)
    receivers_before = build_two_sided_patch_receivers(plant)
    layout = plan_natural_fit_layout_from_feet(10.0, 10.0)
    scene_before = build_juvenile_natural_fit_scene(
        layout,
        canonical_plant=plant,
    )
    protected_before = {
        "calibration": _artifact_snapshot(
            ROOT / "src" / "fspm_optics" / "resources" / "calibration"
        ),
        "viewer": _artifact_snapshot(
            ROOT / "src" / "fspm_optics" / "resources" / "viewer"
        ),
    }

    payload = diagnostic.build_receiver_placement_diagnostic(plant)

    scene_after = build_juvenile_natural_fit_scene(
        layout,
        canonical_plant=plant,
    )
    protected_after = {
        "calibration": _artifact_snapshot(
            ROOT / "src" / "fspm_optics" / "resources" / "calibration"
        ),
        "viewer": _artifact_snapshot(
            ROOT / "src" / "fspm_optics" / "resources" / "viewer"
        ),
    }
    assert plant == plant_before
    assert build_two_sided_patch_receivers(plant) == receivers_before
    assert scene_after.topology.topology_sha256 == scene_before.topology.topology_sha256
    assert scene_after.topology.receivers_sha256 == scene_before.topology.receivers_sha256
    assert protected_after == protected_before
    return payload


def test_closest_point_on_triangle_interior() -> None:
    result = diagnostic.closest_point_on_triangle(
        (0.25, 0.25, 1.0),
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
    )

    assert result.point == pytest.approx((0.25, 0.25, 0.0))
    assert result.barycentric == pytest.approx((0.5, 0.25, 0.25))
    assert result.squared_distance == pytest.approx(1.0)


def test_closest_point_on_triangle_edge() -> None:
    result = diagnostic.closest_point_on_triangle(
        (0.5, -0.2, 0.4),
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
    )

    assert result.point == pytest.approx((0.5, 0.0, 0.0))
    assert result.barycentric == pytest.approx((0.5, 0.5, 0.0))
    assert result.squared_distance == pytest.approx(0.2)


def test_closest_point_on_triangle_vertex_and_degenerate_failure() -> None:
    result = diagnostic.closest_point_on_triangle(
        (-1.0, -2.0, 0.5),
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
    )
    assert result.point == (0.0, 0.0, 0.0)
    assert result.barycentric == (1.0, 0.0, 0.0)
    assert result.squared_distance == pytest.approx(5.25)

    with pytest.raises(
        diagnostic.ReceiverPlacementDiagnosticError,
        match="degenerate",
    ):
        diagnostic.closest_point_on_triangle(
            (0.0, 0.0, 1.0),
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            triangle_id="flat",
        )


def test_closest_face_tie_uses_lowest_canonical_face_id() -> None:
    result = diagnostic.closest_point_on_faces(
        (0.2, 0.2, 1.0),
        (_face("face_z", 1), _face("face_a", 0, z_m=-1e-13)),
    )

    assert result.face.face_id == "face_a"
    assert result.point == pytest.approx((0.2, 0.2, -1e-13))


def test_carrier_plane_signed_distances_and_correct_bracketing() -> None:
    anchor = (1.0, 2.0, 3.0)
    normal = (0.0, 0.6, 0.8)
    measurement = diagnostic.measure_carrier_plane_bracketing(
        current_centroid=(1.2, 2.0 + 0.6 * 0.003, 3.0 + 0.8 * 0.003),
        surface_anchor=anchor,
        carrier_normal=normal,
        front_origin=(0.7, 2.0 + 0.6 * 0.011, 3.0 + 0.8 * 0.011),
        back_origin=(1.4, 2.0 - 0.6 * 0.017, 3.0 - 0.8 * 0.017),
        front_direction=normal,
        back_direction=(0.0, -0.6, -0.8),
    )

    assert measurement.centroid_signed_distance_m == pytest.approx(0.003)
    assert measurement.front_signed_distance_m == pytest.approx(0.011)
    assert measurement.back_signed_distance_m == pytest.approx(-0.017)
    assert measurement.front_direction_alignment == pytest.approx(1.0)
    assert measurement.back_direction_alignment == pytest.approx(-1.0)
    assert measurement.front_side_classification == "front_side"
    assert measurement.back_side_classification == "back_side"
    assert measurement.front_origin_on_expected_side is True
    assert measurement.back_origin_on_expected_side is True
    assert measurement.pair_brackets_carrier_plane is True
    assert measurement.pair_origins_on_same_side is False
    assert measurement.any_origin_on_plane_within_tolerance is False


def test_carrier_plane_detects_both_origins_on_back_side() -> None:
    measurement = _bracketing(
        front_distance_m=-0.03,
        back_distance_m=-0.04,
    )

    assert measurement.front_side_classification == "back_side"
    assert measurement.back_side_classification == "back_side"
    assert measurement.front_origin_on_expected_side is False
    assert measurement.back_origin_on_expected_side is True
    assert measurement.pair_brackets_carrier_plane is False
    assert measurement.pair_origins_on_same_side is True


def test_carrier_plane_detects_both_origins_on_front_side() -> None:
    measurement = _bracketing(
        front_distance_m=0.05,
        back_distance_m=0.06,
    )

    assert measurement.front_side_classification == "front_side"
    assert measurement.back_side_classification == "front_side"
    assert measurement.front_origin_on_expected_side is True
    assert measurement.back_origin_on_expected_side is False
    assert measurement.pair_brackets_carrier_plane is False
    assert measurement.pair_origins_on_same_side is True


def test_carrier_plane_classifies_origin_within_side_tolerance() -> None:
    measurement = _bracketing(
        front_distance_m=(
            0.5 * diagnostic.CARRIER_SIDE_CLASSIFICATION_TOLERANCE_M
        ),
        back_distance_m=-0.07,
    )

    assert measurement.front_side_classification == "on_plane_within_tolerance"
    assert measurement.front_origin_on_expected_side is False
    assert measurement.back_origin_on_expected_side is True
    assert measurement.any_origin_on_plane_within_tolerance is True
    assert measurement.pair_brackets_carrier_plane is False
    assert measurement.pair_origins_on_same_side is False


def test_carrier_plane_separates_origin_and_direction_contracts() -> None:
    measurement = _bracketing(
        front_distance_m=0.08,
        back_distance_m=-0.09,
        front_direction=(0.0, 0.0, -1.0),
        back_direction=(0.0, 0.0, 1.0),
    )

    assert measurement.pair_brackets_carrier_plane is True
    assert measurement.front_direction_alignment == pytest.approx(-1.0)
    assert measurement.back_direction_alignment == pytest.approx(1.0)
    assert measurement.front_direction_alignment_correct is False
    assert measurement.back_direction_alignment_correct is False


def test_carrier_plane_aggregate_counts_and_extrema() -> None:
    measurements = (
        _bracketing(front_distance_m=0.01, back_distance_m=-0.02),
        _bracketing(front_distance_m=-0.03, back_distance_m=-0.04),
        _bracketing(front_distance_m=0.05, back_distance_m=0.06),
        _bracketing(
            front_distance_m=(
                0.5 * diagnostic.CARRIER_SIDE_CLASSIFICATION_TOLERANCE_M
            ),
            back_distance_m=-0.07,
        ),
        _bracketing(
            front_distance_m=0.08,
            back_distance_m=-0.09,
            front_direction=(0.0, 0.0, -1.0),
            back_direction=(0.0, 0.0, 1.0),
        ),
    )

    summary = diagnostic.summarize_carrier_plane_bracketing(measurements)

    assert summary == {
        "receiver_pair_count": 5,
        "correctly_bracketed_pair_count": 2,
        "non_bracketed_pair_count": 3,
        "same_side_pair_count": 2,
        "front_origin_wrong_side_count": 1,
        "back_origin_wrong_side_count": 1,
        "on_plane_origin_count": 1,
        "direction_alignment_failure_count": 2,
        "minimum_correct_side_separation_m": pytest.approx(0.01),
        "maximum_wrong_side_penetration_distance_m": pytest.approx(0.06),
    }


def test_uniform_grid_candidates_are_complete_contiguous_and_nominal() -> None:
    grid = tuple(tuple(1.0 for _ in range(8)) for _ in range(8))
    candidates = (
        diagnostic.current_fixed_candidate(grid),
        diagnostic.best_global_rectilinear_candidate(grid),
        diagnostic.best_band_adaptive_candidate(grid),
    )

    for candidate in candidates:
        _assert_candidate_partition(candidate, grid)
        assert candidate.u_cuts == (2, 4, 6)
        assert candidate.v_cuts_by_u_band == ((2, 4, 6),) * 4
        assert candidate.normalized_areas == pytest.approx((1.0,) * 16)


def test_optimizer_objective_and_lexicographic_tie_breaking() -> None:
    grid = tuple(tuple(1.0 for _ in range(5)) for _ in range(5))

    global_candidate = diagnostic.best_global_rectilinear_candidate(grid)
    adaptive_candidate = diagnostic.best_band_adaptive_candidate(grid)

    assert global_candidate.u_cuts == (1, 2, 4)
    assert global_candidate.v_cuts_by_u_band == ((1, 2, 4),) * 4
    assert adaptive_candidate.u_cuts == (1, 2, 4)
    assert adaptive_candidate.v_cuts_by_u_band == ((1, 2, 4),) * 4


def test_band_adaptive_optimizer_can_select_independent_lateral_cuts() -> None:
    grid = (
        (4.0, 4.0, 4.0, 0.8, 0.8, 0.8, 0.8, 0.8),
        (2.0, 2.0, 4.0, 4.0, 1.0, 1.0, 1.0, 1.0),
        (1.0, 1.0, 2.0, 4.0, 2.0, 2.0, 2.0, 2.0),
        (0.8, 0.8, 0.8, 0.8, 0.8, 4.0, 4.0, 4.0),
    )

    adaptive = diagnostic.best_band_adaptive_candidate(grid)

    assert adaptive.u_cuts == (1, 2, 3)
    assert adaptive.v_cuts_by_u_band == (
        (1, 2, 3),
        (2, 3, 4),
        (3, 4, 6),
        (5, 6, 7),
    )
    assert adaptive.area_distribution.maximum_absolute_normalized_deviation == (
        pytest.approx(0.0, abs=1e-15)
    )
    assert adaptive.lateral_step_change > 0.0
    _assert_candidate_partition(adaptive, grid)


def test_optimizers_match_exhaustive_synthetic_objective_oracles() -> None:
    grid = (
        (8.0, 1.0, 3.0, 2.0, 5.0),
        (2.0, 7.0, 1.0, 4.0, 3.0),
        (5.0, 2.0, 6.0, 1.0, 4.0),
        (1.0, 4.0, 2.0, 8.0, 3.0),
        (3.0, 5.0, 4.0, 2.0, 7.0),
    )
    cut_choices = tuple(combinations(range(1, 5), 3))

    global_oracle = min(
        (
            diagnostic._candidate_from_cuts(
                grid,
                layout="global_rectilinear",
                u_cuts=u_cuts,
                v_cuts_by_u_band=(v_cuts,) * 4,
            )
            for u_cuts in cut_choices
            for v_cuts in cut_choices
        ),
        key=lambda candidate: candidate.objective,
    )
    adaptive_oracle = min(
        (
            diagnostic._candidate_from_cuts(
                grid,
                layout="longitudinal_band_adaptive",
                u_cuts=u_cuts,
                v_cuts_by_u_band=v_cut_sets,
            )
            for u_cuts in cut_choices
            for v_cut_sets in product(cut_choices, repeat=4)
        ),
        key=lambda candidate: candidate.objective,
    )

    first_global = diagnostic.best_global_rectilinear_candidate(grid)
    second_global = diagnostic.best_global_rectilinear_candidate(grid)
    first_adaptive = diagnostic.best_band_adaptive_candidate(grid)
    second_adaptive = diagnostic.best_band_adaptive_candidate(grid)

    assert first_global == second_global == global_oracle
    assert first_adaptive == second_adaptive == adaptive_oracle
    assert len(set(first_global.v_cuts_by_u_band)) == 1
    assert all(
        0 < cuts[0] < cuts[1] < cuts[2] < 5
        for cuts in first_adaptive.v_cuts_by_u_band
    )
    _assert_candidate_partition(first_global, grid)
    _assert_candidate_partition(first_adaptive, grid)


def test_small_representative_report_construction_is_deterministic() -> None:
    first = _small_representative_report()
    second = _small_representative_report()
    first_json = diagnostic.format_receiver_placement_diagnostic(first)
    second_json = diagnostic.format_receiver_placement_diagnostic(second)

    assert first == second
    assert first_json == second_json
    assert json.loads(first_json) == first
    assert "NaN" not in first_json
    assert "Infinity" not in first_json


def test_full_canonical_diagnostic_is_complete_strict_json(
    canonical_diagnostic: dict[str, object],
) -> None:
    first_json = diagnostic.format_receiver_placement_diagnostic(
        canonical_diagnostic
    )
    second_json = diagnostic.format_receiver_placement_diagnostic(
        canonical_diagnostic
    )

    assert first_json == second_json
    assert json.loads(first_json) == canonical_diagnostic
    assert "NaN" not in first_json
    assert "Infinity" not in first_json


def test_diagnostic_preserves_geometry_receivers_hashes_and_protected_artifacts(
    canonical_diagnostic: dict[str, object],
) -> None:
    # The fixture snapshots and compares every named boundary around its one build.
    assert canonical_diagnostic["production_changes_made"] is False


def test_full_diagnostic_schema_face_assignments_and_finite_values(
    canonical_diagnostic: dict[str, object],
) -> None:
    payload = canonical_diagnostic

    assert payload["schema_id"] == diagnostic.DIAGNOSTIC_SCHEMA_ID
    assert diagnostic.DIAGNOSTIC_SCHEMA_VERSION == 3
    assert payload["schema_version"] == 3
    assert payload["production_changes_made"] is False
    assert payload["counts"] == {
        "leaves": 12,
        "faces": 1920,
        "patches": 192,
        "receivers": 384,
    }
    assert isinstance(payload["profile"], Mapping)
    assert len(payload["profile"]["topology_sha256"]) == 64
    assert len(payload["profile"]["receivers_sha256"]) == 64
    assert payload["receiver_offset_epsilon_m"] == pytest.approx(5e-5)
    assert payload["comparison_tolerances"][
        "carrier_side_classification_absolute_m"
    ] == pytest.approx(diagnostic.CARRIER_SIDE_CLASSIFICATION_TOLERANCE_M)
    assert payload["carrier_plane_side_contract"][
        "segment_intersection_safety_is_independent"
    ] is True

    leaves = payload["leaves"]
    assert isinstance(leaves, Sequence)
    assert len(leaves) == 12
    for leaf in leaves:
        assert isinstance(leaf, Mapping)
        patches = leaf["current_patches"]
        current_regions = leaf["current_authoritative_sampling_layout"]["regions"]
        assert len(patches) == len(current_regions) == 16
        current_summary = leaf["current_summary"]
        assert current_summary["receiver_pair_count"] == 16
        assert (
            current_summary["correctly_bracketed_pair_count"]
            + current_summary["non_bracketed_pair_count"]
            == 16
        )
        assert [patch["local_patch_index"] for patch in patches] == list(range(16))
        for patch, region in zip(patches, current_regions, strict=True):
            assert patch["face_ids"] == region["face_ids"]
            bracketing = patch["carrier_plane_bracketing"]
            assert bracketing["front_origin_side_classification"] in {
                "front_side",
                "back_side",
                "on_plane_within_tolerance",
            }
            assert bracketing["back_origin_side_classification"] in {
                "front_side",
                "back_side",
                "on_plane_within_tolerance",
            }
        for name in (
            "current_authoritative_sampling_layout",
            "current_fixed_layout",
            "legacy_uv_quarter_layout",
            "best_global_rectilinear_layout",
            "best_longitudinal_band_adaptive_layout",
        ):
            candidate = leaf[name]
            assert candidate["face_coverage"]["complete"] is True
            assert candidate["face_coverage"]["unique"] is True
            assert candidate["total_leaf_area_conserved"] is True
            assert all(region["cell_coordinates"] for region in candidate["regions"])

    plant_summary = payload["plant_wide_current_summary"]
    assert plant_summary["receiver_pair_count"] == 192
    assert plant_summary["correctly_bracketed_pair_count"] == 192
    assert plant_summary["non_bracketed_pair_count"] == 0

    def assert_finite(value: object) -> None:
        if isinstance(value, float):
            assert math.isfinite(value)
        elif isinstance(value, Mapping):
            for item in value.values():
                assert_finite(item)
        elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
            for item in value:
                assert_finite(item)

    assert_finite(payload)


def test_cli_defaults_to_stdout_and_writes_only_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    payload: dict[str, object] = {
        "schema_id": diagnostic.DIAGNOSTIC_SCHEMA_ID,
        "schema_version": diagnostic.DIAGNOSTIC_SCHEMA_VERSION,
    }
    monkeypatch.setattr(cli, "build_receiver_placement_diagnostic", lambda: payload)

    assert cli.main([]) == 0
    stdout = capsys.readouterr().out
    assert json.loads(stdout) == payload

    output = tmp_path / "diagnostic.json"
    assert cli.main(["--compact", "--output", str(output)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert json.loads(output.read_text(encoding="utf-8")) == payload


def test_cli_entry_point_is_registered_without_packaged_result_path() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    package_data = pyproject.split("[tool.setuptools.package-data]", 1)[1]

    assert (
        'fspm-optics-diagnose-receiver-placement = '
        '"fspm_optics.diagnostics.receiver_placement_cli:main"'
    ) in pyproject
    assert "receiver-placement-diagnostic" not in package_data
