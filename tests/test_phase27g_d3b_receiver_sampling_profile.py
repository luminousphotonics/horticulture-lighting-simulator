from __future__ import annotations

import math

import pytest

from fspm_optics.plants import (
    JUVENILE_SCIENTIFIC_LEGACY_SCENE_SCHEMA_VERSION,
    JUVENILE_SCIENTIFIC_SCENE_SCHEMA_VERSION,
    REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID,
    REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID,
    REX_JUVENILE_OPTIMIZED_U_CUTS_BY_LEAF_RANK,
    REX_JUVENILE_OPTIMIZED_V_CUTS,
    RexJuvenilePreheadingConfig,
    barycentric_point,
    build_juvenile_natural_fit_scene,
    generate_rex_juvenile_preheading_plant,
    legacy_rex_juvenile_preheading_config,
    plan_natural_fit_layout_from_feet,
)
from fspm_optics.receivers.samples import build_two_sided_patch_receivers


LEGACY_TOPOLOGY_SHA256 = (
    "b199a43c98aa899657aae4fbda0b04fee72290d05636aa39d51240598c95fa09"
)
LEGACY_RECEIVERS_SHA256 = (
    "e9631120fea1672e2047e0db7fdb8297ca9e4a9ba89cd27b947dd89af99038fc"
)
OPTIMIZED_TOPOLOGY_SHA256 = (
    "d59c27614c9b4fde3d60f8ebf090b56bbc6692d77baeed67753e8081ff70138f"
)
OPTIMIZED_RECEIVERS_SHA256 = (
    "e2f6606ba78642ffe6f9de0601d87c8f4e4197b66cb3ef9d284f8324e1ee305f"
)


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return math.fsum(left[index] * right[index] for index in range(3))


def _subtract(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _band(cell: int, cuts: tuple[int, int, int], segments: int) -> int:
    bounds = (0, *cuts, segments)
    return next(
        index
        for index in range(4)
        if bounds[index] <= cell < bounds[index + 1]
    )


def _expected_face_ids_by_patch(leaf, u_cuts, v_cuts, u_segments, v_segments):
    grouped = [[] for _ in range(16)]
    cursor = 0
    for u_cell in range(u_segments):
        triangle_count = 1 if u_cell in {0, u_segments - 1} else 2
        for v_cell in range(v_segments):
            patch_index = 4 * _band(u_cell, u_cuts, u_segments) + _band(
                v_cell, v_cuts, v_segments
            )
            grouped[patch_index].extend(
                face.face_id for face in leaf.faces[cursor : cursor + triangle_count]
            )
            cursor += triangle_count
    assert cursor == len(leaf.faces)
    return tuple(tuple(values) for values in grouped)


def test_sampling_profiles_are_separate_from_morphology_and_default_is_optimized() -> None:
    default = RexJuvenilePreheadingConfig()
    legacy = legacy_rex_juvenile_preheading_config()

    assert default.profile_id == legacy.profile_id
    assert (
        default.sampling_profile_id
        == REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
    )
    assert (
        legacy.sampling_profile_id
        == REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID
    )
    assert default.uses_surface_constrained_receivers is True
    assert legacy.uses_surface_constrained_receivers is False
    assert default.sampling_calibration_status.startswith("uncalibrated")


def test_declared_cut_table_is_exact_and_not_optimizer_generated() -> None:
    config = RexJuvenilePreheadingConfig()
    assert REX_JUVENILE_OPTIMIZED_V_CUTS == (2, 4, 6)
    assert REX_JUVENILE_OPTIMIZED_U_CUTS_BY_LEAF_RANK == {
        1: (4, 6, 8), 2: (4, 6, 8), 3: (4, 6, 8),
        4: (4, 6, 8), 5: (4, 6, 8),
        6: (3, 5, 8), 7: (3, 5, 8), 8: (3, 5, 8),
        9: (3, 5, 7), 10: (2, 3, 5),
        11: (2, 3, 4), 12: (2, 3, 5),
    }
    for rank in range(1, 13):
        u_segments = 12 if rank <= 9 else 8
        assert config.patch_cell_cuts_for_leaf_rank(
            rank, u_segments=u_segments, v_segments=8
        ) == (
            REX_JUVENILE_OPTIMIZED_U_CUTS_BY_LEAF_RANK[rank],
            REX_JUVENILE_OPTIMIZED_V_CUTS,
        )


def test_optimized_grouping_preserves_authoritative_mesh_and_installs_exact_cuts() -> None:
    optimized = generate_rex_juvenile_preheading_plant()
    legacy = generate_rex_juvenile_preheading_plant(
        legacy_rex_juvenile_preheading_config()
    )

    assert optimized.plant_id == legacy.plant_id
    assert (len(optimized.leaves), optimized.face_count, optimized.patch_count) == (
        12, 1920, 192
    )
    for optimized_leaf, legacy_leaf in zip(
        optimized.leaves, legacy.leaves, strict=True
    ):
        assert optimized_leaf.vertices == legacy_leaf.vertices
        assert optimized_leaf.triangle_indices == legacy_leaf.triangle_indices
        assert optimized_leaf.faces == legacy_leaf.faces
        u_segments, v_segments = optimized.config.mesh_segments_for_layer(
            optimized_leaf.leaf_layer
        )
        u_cuts = REX_JUVENILE_OPTIMIZED_U_CUTS_BY_LEAF_RANK[
            optimized_leaf.leaf_rank
        ]
        expected = _expected_face_ids_by_patch(
            optimized_leaf,
            u_cuts,
            REX_JUVENILE_OPTIMIZED_V_CUTS,
            u_segments,
            v_segments,
        )
        assert tuple(
            patch.triangle_face_ids for patch in optimized_leaf.patches
        ) == expected
        assert [patch.patch_index for patch in optimized_leaf.patches] == list(
            range(16)
        )
        assert [
            (patch.patch_u_index, patch.patch_v_index)
            for patch in optimized_leaf.patches
        ] == [(u, v) for u in range(4) for v in range(4)]
        assigned = [
            face_id
            for patch in optimized_leaf.patches
            for face_id in patch.triangle_face_ids
        ]
        assert len(assigned) == len(set(assigned)) == len(optimized_leaf.faces)
        assert set(assigned) == {face.face_id for face in optimized_leaf.faces}
        assert math.fsum(patch.area_m2 for patch in optimized_leaf.patches) == (
            pytest.approx(math.fsum(face.area_m2 for face in optimized_leaf.faces))
        )


def test_surface_anchors_carriers_and_receivers_satisfy_signed_contract() -> None:
    plant = generate_rex_juvenile_preheading_plant()
    receivers = build_two_sided_patch_receivers(plant)
    face_by_id = {face.face_id: face for face in plant.faces}

    assert len(receivers) == 384
    for patch_index, patch in enumerate(plant.patches):
        assert patch.surface_anchor is not None
        assert patch.carrier_face_id in patch.triangle_face_ids
        assert patch.carrier_barycentric is not None
        assert patch.carrier_unit_normal is not None
        assert patch.receiver_normal_basis == (
            "carrier_triangle_winding_defined_unit_normal"
        )
        carrier = face_by_id[patch.carrier_face_id]
        assert patch.carrier_unit_normal == carrier.unit_normal
        assert barycentric_point(
            carrier.vertices, patch.carrier_barycentric
        ) == pytest.approx(patch.surface_anchor, abs=1e-12)

        front, back = receivers[2 * patch_index : 2 * patch_index + 2]
        assert (front.side, back.side) == ("front", "back")
        assert front.surface_anchor_m == back.surface_anchor_m == patch.surface_anchor
        assert front.carrier_face_id == back.carrier_face_id == patch.carrier_face_id
        assert front.normal == patch.carrier_unit_normal
        assert back.normal == tuple(-value for value in patch.carrier_unit_normal)
        assert _dot(
            _subtract(front.point_m, patch.surface_anchor),
            patch.carrier_unit_normal,
        ) == pytest.approx(5e-5, abs=1e-12)
        assert _dot(
            _subtract(back.point_m, patch.surface_anchor),
            patch.carrier_unit_normal,
        ) == pytest.approx(-5e-5, abs=1e-12)
        assert _dot(front.normal, patch.carrier_unit_normal) == pytest.approx(1.0)
        assert _dot(back.normal, patch.carrier_unit_normal) == pytest.approx(-1.0)
        assert front.to_dict()["normal_generation_basis"] == (
            "carrier_triangle_winding_defined_unit_normal"
        )


def test_legacy_replay_hashes_are_exact_and_optimized_hashes_are_dynamic() -> None:
    layout = plan_natural_fit_layout_from_feet(10, 10)
    legacy = build_juvenile_natural_fit_scene(
        layout,
        sampling_profile_id=REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID,
    )
    optimized_first = build_juvenile_natural_fit_scene(layout)
    optimized_second = build_juvenile_natural_fit_scene(layout)

    assert legacy.schema_version == JUVENILE_SCIENTIFIC_LEGACY_SCENE_SCHEMA_VERSION
    assert "sampling_profile_id" not in legacy.identity_payload()
    assert "sampling_profile_id" not in legacy.identity_payload()[
        "canonical_topology"
    ]
    assert legacy.topology.topology_sha256 == LEGACY_TOPOLOGY_SHA256
    assert legacy.topology.receivers_sha256 == LEGACY_RECEIVERS_SHA256
    assert optimized_first.schema_version == JUVENILE_SCIENTIFIC_SCENE_SCHEMA_VERSION
    assert optimized_first.identity_payload()["sampling_profile_id"] == (
        REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
    )
    assert optimized_first.topology.topology_sha256 == (
        optimized_second.topology.topology_sha256
    )
    assert optimized_first.topology.receivers_sha256 == (
        optimized_second.topology.receivers_sha256
    )
    assert optimized_first.topology.topology_sha256 == OPTIMIZED_TOPOLOGY_SHA256
    assert optimized_first.topology.receivers_sha256 == OPTIMIZED_RECEIVERS_SHA256
    assert optimized_first.topology.topology_sha256 != LEGACY_TOPOLOGY_SHA256
    assert optimized_first.topology.receivers_sha256 != LEGACY_RECEIVERS_SHA256
    assert len(optimized_first.topology.topology_sha256) == 64
    assert len(optimized_first.topology.receivers_sha256) == 64


def test_optimized_partition_improves_each_leaf_area_distribution() -> None:
    optimized = generate_rex_juvenile_preheading_plant()
    legacy = generate_rex_juvenile_preheading_plant(
        legacy_rex_juvenile_preheading_config()
    )

    def coefficient_of_variation(areas: tuple[float, ...]) -> float:
        mean = math.fsum(areas) / len(areas)
        return math.sqrt(
            math.fsum((area / mean - 1.0) ** 2 for area in areas) / len(areas)
        )

    for optimized_leaf, legacy_leaf in zip(
        optimized.leaves, legacy.leaves, strict=True
    ):
        optimized_cv = coefficient_of_variation(
            tuple(patch.area_m2 for patch in optimized_leaf.patches)
        )
        legacy_cv = coefficient_of_variation(
            tuple(patch.area_m2 for patch in legacy_leaf.patches)
        )
        assert optimized_cv < legacy_cv


def test_constant_density_area_rollup_is_sampling_partition_invariant() -> None:
    optimized = generate_rex_juvenile_preheading_plant()
    legacy = generate_rex_juvenile_preheading_plant(
        legacy_rex_juvenile_preheading_config()
    )
    density = 317.25

    optimized_area = math.fsum(patch.area_m2 for patch in optimized.patches)
    legacy_area = math.fsum(patch.area_m2 for patch in legacy.patches)
    authoritative_area = math.fsum(face.area_m2 for face in optimized.faces)
    optimized_rate = math.fsum(
        density * patch.area_m2 for patch in optimized.patches
    )
    legacy_rate = math.fsum(density * patch.area_m2 for patch in legacy.patches)

    assert optimized_area == pytest.approx(authoritative_area, rel=1e-14)
    assert legacy_area == pytest.approx(authoritative_area, rel=1e-14)
    assert optimized_rate == pytest.approx(density * authoritative_area, rel=1e-14)
    assert legacy_rate == pytest.approx(density * authoritative_area, rel=1e-14)
