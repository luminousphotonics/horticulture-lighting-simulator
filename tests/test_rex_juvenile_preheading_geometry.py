from __future__ import annotations

from collections import Counter
import math

import pytest

from fspm_optics.plants import (
    REX_JUVENILE_PREHEADING_COHORT_COUNTS,
    REX_JUVENILE_PREHEADING_PROFILE_ID,
    RexJuvenilePreheadingConfig,
    RexPlantConfig,
    export_plant_mesh_to_obj,
    generate_rex_butterhead_plant,
    generate_rex_juvenile_preheading_plant,
    validate_plant_mesh,
)
from fspm_optics.plants.generator import _rex_juvenile_leaf_parameters
from fspm_optics.receivers.samples import build_two_sided_patch_receivers
from fspm_optics.transport.five_band import _validate_default_receivers


def test_juvenile_profile_has_exact_geometry_and_cohort_contract() -> None:
    plant = generate_rex_juvenile_preheading_plant()

    assert plant.config.profile_id == REX_JUVENILE_PREHEADING_PROFILE_ID
    assert plant.config.approximate_days_after_transplant == 9
    assert plant.config.phenological_boundary == "BBCH_19_pre_41"
    assert plant.config.cohort_counts == REX_JUVENILE_PREHEADING_COHORT_COUNTS
    assert plant.config.plant_height_m == 0.065
    assert plant.plant_id == "rex_juvenile_preheading_12leaf_v1_plant_000"
    assert len(plant.leaves) == 12
    assert Counter(leaf.leaf_layer for leaf in plant.leaves) == {
        "outer": 5,
        "mid": 4,
        "inner": 3,
    }
    assert [len(leaf.faces) for leaf in plant.leaves] == [176] * 9 + [112] * 3
    assert plant.face_count == 1920
    assert plant.patch_count == 192
    assert plant.receiver_count == 384
    assert plant.config.leaf_patch_grid == (4, 4)


def test_juvenile_geometry_and_profile_specific_identities_are_deterministic() -> None:
    config = RexJuvenilePreheadingConfig()
    first = generate_rex_juvenile_preheading_plant(config)
    second = generate_rex_juvenile_preheading_plant(config)

    assert first == second
    assert [leaf.leaf_id for leaf in first.leaves] == [
        f"{first.plant_id}_leaf_{rank:03d}" for rank in range(1, 13)
    ]
    assert all(
        identifier.startswith(first.plant_id)
        for identifier in (
            *(face.face_id for face in first.faces),
            *(patch.patch_id for patch in first.patches),
        )
    )
    receivers = build_two_sided_patch_receivers(first)
    assert all(
        receiver.receiver_id.startswith(first.plant_id) for receiver in receivers
    )
    assert len({leaf.vertices for leaf in first.leaves}) == 12


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profile_id", "other_profile"),
        ("plant_id", "other_plant"),
        ("leaf_count", 13),
        ("projected_diameter_m", 0.19),
        ("plant_height_m", 0.09),
        ("leaf_patch_u", 5),
    ],
)
def test_juvenile_profile_contract_is_immutable(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="is fixed"):
        RexJuvenilePreheadingConfig(**{field: value})  # type: ignore[arg-type]


def test_juvenile_canonical_leaf_face_patch_and_receiver_ordering() -> None:
    plant = generate_rex_juvenile_preheading_plant()
    assert [leaf.leaf_rank for leaf in plant.leaves] == list(range(1, 13))

    for leaf in plant.leaves:
        assert [face.face_index for face in leaf.faces] == list(range(len(leaf.faces)))
        assert [
            (patch.patch_u_index, patch.patch_v_index)
            for patch in leaf.patches
        ] == [(u, v) for u in range(4) for v in range(4)]

    receivers = build_two_sided_patch_receivers(plant)
    for patch_index, patch in enumerate(plant.patches):
        front, back = receivers[2 * patch_index : 2 * patch_index + 2]
        assert front.receiver_id == f"{patch.patch_id}_front"
        assert back.receiver_id == f"{patch.patch_id}_back"
        assert front.side == "front" and back.side == "back"
        assert front.patch_id == back.patch_id == patch.patch_id
        assert back.normal == tuple(-component for component in front.normal)
        assert front.area_m2 == back.area_m2 == patch.area_m2


def test_juvenile_geometry_is_finite_positive_and_has_no_inverted_faces() -> None:
    plant = generate_rex_juvenile_preheading_plant()

    for leaf in plant.leaves:
        assert math.fsum(face.area_m2 for face in leaf.faces) > 0.0
        assert all(patch.area_m2 > 0.0 for patch in leaf.patches)
        assert all(
            math.isfinite(coordinate)
            for vertex in leaf.vertices
            for coordinate in vertex
        )
        for face in leaf.faces:
            assert face.area_m2 > 0.0 and math.isfinite(face.area_m2)
            assert all(math.isfinite(value) for value in face.unit_normal)
            assert face.unit_normal[2] > 0.0
        for patch in leaf.patches:
            assert all(math.isfinite(value) for value in patch.unit_normal)


def test_juvenile_envelope_is_generated_directly_with_no_whole_plant_scaling() -> None:
    plant = generate_rex_juvenile_preheading_plant()
    report = validate_plant_mesh(plant)

    assert 0.17 <= report.projected_diameter_m <= 0.19
    assert 0.055 <= report.height_m <= 0.075
    assert all(leaf.world_transform.horizontal_scale == 1.0 for leaf in plant.leaves)
    assert all(leaf.world_transform.vertical_scale == 1.0 for leaf in plant.leaves)
    assert all(leaf.world_transform.vertical_offset_m == 0.0 for leaf in plant.leaves)


def test_juvenile_cohorts_decrease_in_size_and_increase_in_elevation() -> None:
    plant = generate_rex_juvenile_preheading_plant()
    cohorts = {
        layer: tuple(leaf for leaf in plant.leaves if leaf.leaf_layer == layer)
        for layer in ("outer", "mid", "inner")
    }

    def mean(attribute: str, layer: str) -> float:
        values = [getattr(leaf, attribute) for leaf in cohorts[layer]]
        return math.fsum(values) / len(values)

    assert mean("length_m", "outer") > mean("length_m", "mid") > mean(
        "length_m", "inner"
    )
    assert mean("width_m", "outer") > mean("width_m", "mid") > mean(
        "width_m", "inner"
    )
    assert mean("elevation_rad", "inner") > mean(
        "elevation_rad", "mid"
    ) > mean("elevation_rad", "outer")
    assert math.degrees(
        mean("elevation_rad", "mid") - mean("elevation_rad", "outer")
    ) <= 25.0
    assert math.degrees(
        mean("elevation_rad", "inner") - mean("elevation_rad", "mid")
    ) <= 20.0


def test_middle_cohort_curvature_does_not_form_bowl_walls() -> None:
    parameters = tuple(
        _rex_juvenile_leaf_parameters("mid", index / 3, index)
        for index in range(4)
    )

    assert min(item["elevation_deg"] for item in parameters) >= 28.0
    assert max(item["elevation_deg"] for item in parameters) <= 40.0
    assert max(item["cup_strength_m"] for item in parameters) <= 0.003
    assert max(item["arch_strength_m"] for item in parameters) <= 0.0125
    assert len(
        {
            (
                item["elevation_deg"],
                item["arch_strength_m"],
                item["tip_sag_m"],
            )
            for item in parameters
        }
    ) == 4


def test_inner_cohort_remains_broad_inclined_and_visibly_unfolding() -> None:
    plant = generate_rex_juvenile_preheading_plant()
    middle = tuple(leaf for leaf in plant.leaves if leaf.leaf_layer == "mid")
    inner = tuple(leaf for leaf in plant.leaves if leaf.leaf_layer == "inner")

    assert min(leaf.width_m / leaf.length_m for leaf in inner) >= 0.75
    assert max(math.degrees(leaf.elevation_rad) for leaf in inner) <= 52.0
    assert min(leaf.elevation_rad for leaf in inner) > max(
        leaf.elevation_rad for leaf in middle
    )
    for leaf in inner:
        base = leaf.world_transform.origin_m
        tip = leaf.vertices[-1]
        outward_tip_reach = math.hypot(tip[0] - base[0], tip[1] - base[1])
        assert outward_tip_reach >= 0.020
        crest_to_tip_drop = max(vertex[2] for vertex in leaf.vertices) - tip[2]
        assert 0.0 <= crest_to_tip_drop <= 0.008


def test_inner_distal_midribs_continue_outward_without_downward_hooks() -> None:
    plant = generate_rex_juvenile_preheading_plant()
    inner = tuple(leaf for leaf in plant.leaves if leaf.leaf_layer == "inner")
    u_segments, v_segments = plant.config.mesh_segments_for_layer("inner")
    distal_midrib_index = 1 + (u_segments - 2) * (v_segments + 1) + v_segments // 2

    for leaf in inner:
        base = leaf.world_transform.origin_m
        distal = leaf.vertices[distal_midrib_index]
        tip = leaf.vertices[-1]
        distal_reach = math.hypot(distal[0] - base[0], distal[1] - base[1])
        tip_reach = math.hypot(tip[0] - base[0], tip[1] - base[1])

        assert tip_reach - distal_reach >= 0.002
        assert tip[2] >= distal[2] - 0.004


def test_inner_bases_close_the_center_and_tip_heights_vary_moderately() -> None:
    plant = generate_rex_juvenile_preheading_plant()
    inner = tuple(leaf for leaf in plant.leaves if leaf.leaf_layer == "inner")
    bases = tuple(leaf.world_transform.origin_m for leaf in inner)
    tip_heights = tuple(leaf.vertices[-1][2] for leaf in inner)
    pairwise_tip_differences = tuple(
        abs(left - right)
        for index, left in enumerate(tip_heights)
        for right in tip_heights[index + 1 :]
    )
    pairwise_base_distances = tuple(
        math.hypot(left[0] - right[0], left[1] - right[1])
        for index, left in enumerate(bases)
        for right in bases[index + 1 :]
    )

    assert max(math.hypot(base[0], base[1]) for base in bases) <= 0.001
    assert max(pairwise_base_distances) <= 0.0015
    assert 0.004 <= max(tip_heights) - min(tip_heights) <= 0.012
    assert min(pairwise_tip_differences) >= 0.002


def test_all_leaf_anchors_share_one_compact_crown() -> None:
    plant = generate_rex_juvenile_preheading_plant()
    anchors = tuple(leaf.vertices[0] for leaf in plant.leaves)
    anchors_by_layer = {
        layer: tuple(
            leaf.vertices[0]
            for leaf in plant.leaves
            if leaf.leaf_layer == layer
        )
        for layer in ("outer", "mid", "inner")
    }

    assert anchors == tuple(
        leaf.world_transform.origin_m for leaf in plant.leaves
    )
    assert all(math.hypot(anchor[0], anchor[1]) <= 0.002 for anchor in anchors)
    assert all(0.0 <= anchor[2] <= 0.005 for anchor in anchors)
    assert all(
        0.0 <= anchor[2] <= 0.0015
        for anchor in anchors_by_layer["outer"]
    )
    assert all(
        0.001 <= anchor[2] <= 0.003
        for anchor in anchors_by_layer["mid"]
    )
    assert all(
        0.002 <= anchor[2] <= 0.005
        for anchor in anchors_by_layer["inner"]
    )
    assert max(anchor[2] for anchor in anchors) - min(
        anchor[2] for anchor in anchors
    ) <= 0.005
    assert all(min(vertex[2] for vertex in leaf.vertices) <= 0.007 for leaf in plant.leaves)

    minimum_x = min(anchor[0] for anchor in anchors)
    maximum_x = max(anchor[0] for anchor in anchors)
    minimum_y = min(anchor[1] for anchor in anchors)
    maximum_y = max(anchor[1] for anchor in anchors)
    projected_anchor_box_area = (maximum_x - minimum_x) * (
        maximum_y - minimum_y
    )
    assert projected_anchor_box_area <= 16e-6


def test_proximal_midribs_rise_continuously_from_crown_without_jumps() -> None:
    plant = generate_rex_juvenile_preheading_plant()

    for leaf in plant.leaves:
        u_segments, v_segments = plant.config.mesh_segments_for_layer(
            leaf.leaf_layer
        )
        row_stride = v_segments + 1
        first_midrib = leaf.vertices[1 + v_segments // 2]
        second_midrib = leaf.vertices[1 + row_stride + v_segments // 2]
        anchor = leaf.vertices[0]
        first_vector = tuple(
            first_midrib[axis] - anchor[axis] for axis in range(3)
        )
        second_vector = tuple(
            second_midrib[axis] - first_midrib[axis] for axis in range(3)
        )
        first_length = math.sqrt(math.fsum(value * value for value in first_vector))
        second_length = math.sqrt(
            math.fsum(value * value for value in second_vector)
        )
        tangent_cosine = math.fsum(
            first_vector[axis] * second_vector[axis] for axis in range(3)
        ) / (first_length * second_length)

        assert u_segments in {8, 12}
        assert 0.002 <= first_length <= 0.015
        assert 0.002 <= second_length <= 0.015
        assert 0.5 <= first_length / second_length <= 2.0
        assert 0.0 < first_midrib[2] - anchor[2] <= 0.012
        assert 0.0 < second_midrib[2] - first_midrib[2] <= 0.012
        assert tangent_cosine >= 0.75
        assert all(
            0 in triangle for triangle in leaf.triangle_indices[:v_segments]
        )


def test_each_leaf_mesh_is_one_connected_positive_area_surface() -> None:
    plant = generate_rex_juvenile_preheading_plant()

    for leaf in plant.leaves:
        adjacency = {index: set() for index in range(len(leaf.vertices))}
        for first, second, third in leaf.triangle_indices:
            adjacency[first].update((second, third))
            adjacency[second].update((first, third))
            adjacency[third].update((first, second))
        visited = {0}
        frontier = [0]
        while frontier:
            current = frontier.pop()
            unseen = adjacency[current] - visited
            visited.update(unseen)
            frontier.extend(unseen)

        assert len(visited) == len(leaf.vertices)
        assert math.fsum(face.area_m2 for face in leaf.faces) > 0.0


def test_existing_obj_exporter_accepts_juvenile_plant_mesh() -> None:
    plant = generate_rex_juvenile_preheading_plant()
    text = export_plant_mesh_to_obj(plant)

    assert sum(line.startswith("g ") for line in text.splitlines()) == 12
    assert sum(line.startswith("f ") for line in text.splitlines()) == 1920


def test_mature_rex_contract_and_determinism_remain_unchanged() -> None:
    config = RexPlantConfig()
    first = generate_rex_butterhead_plant(config)
    second = generate_rex_butterhead_plant(config)

    assert first == second
    assert len(first.leaves) == 32
    assert first.face_count == 5248
    assert first.patch_count == 512
    assert first.receiver_count == 1024
    assert first.config.leaf_patch_grid == (4, 4)


def test_juvenile_profile_is_rejected_by_mature_only_transport_validation() -> None:
    plant = generate_rex_juvenile_preheading_plant()
    receivers = build_two_sided_patch_receivers(plant)

    with pytest.raises(ValueError, match="requires 32 leaves"):
        _validate_default_receivers(  # type: ignore[arg-type]
            plant.config,
            plant.patch_count,
            receivers,
        )
