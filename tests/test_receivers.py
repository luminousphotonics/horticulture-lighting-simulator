from __future__ import annotations

import json

import pytest

from fspm_optics.plants import (
    PlantGeometryConfig,
    RexPlantConfig,
    generate_plant_scene,
    generate_rex_butterhead_plant,
    leaf_absorption_surfaces,
)
from fspm_optics.receivers.aggregation import (
    RADIANCE_RECEIVER_METHOD,
    build_plant_surface_flux_payload,
    build_radiance_receiver_surface_flux_rows,
)
from fspm_optics.receivers.artifacts import (
    build_patch_receiver_artifact_payload,
    write_plant_surface_flux_artifact,
)
from fspm_optics.receivers.parsing import parse_rtrace_receiver_output
from fspm_optics.receivers.samples import (
    RECEIVER_GRANULARITY_LEAF_CENTROID,
    RECEIVER_GRANULARITY_LEAF_QUADRATURE_4,
    RECEIVER_GRANULARITY_MESH_PATCH,
    build_radiance_receiver_samples,
    build_two_sided_patch_receivers,
    receiver_sample_input_text,
)


def scene():
    return generate_plant_scene(
        PlantGeometryConfig(
            seed=19,
            plant_grid_rows=1,
            plant_grid_columns=1,
            leaf_count_per_plant=2,
        )
    )


def rex_plant():
    return generate_rex_butterhead_plant(
        RexPlantConfig(
            leaf_count=24,
            leaf_patch_u=2,
            leaf_patch_v=2,
        )
    )


def test_default_rex_receiver_policy_produces_exactly_1024_rows() -> None:
    plant = generate_rex_butterhead_plant(RexPlantConfig())
    samples = build_two_sided_patch_receivers(plant)

    assert plant.patch_count == 512
    assert len(samples) == plant.receiver_count == 1024


def test_rex_patch_receivers_are_stable_unique_and_exactly_two_sided() -> None:
    plant = rex_plant()
    samples = build_two_sided_patch_receivers(plant, normal_offset_m=1e-4)

    assert len(samples) == 2 * plant.patch_count
    assert samples == build_two_sided_patch_receivers(
        plant,
        normal_offset_m=1e-4,
    )
    assert len({sample.receiver_id for sample in samples}) == len(samples)
    for patch_index, patch in enumerate(plant.patches):
        front = samples[2 * patch_index]
        back = samples[2 * patch_index + 1]
        assert front.receiver_id == f"{patch.patch_id}_front"
        assert back.receiver_id == f"{patch.patch_id}_back"
        assert front.patch_id == patch.patch_id
        assert front.normal == patch.unit_normal
        assert back.normal == tuple(-component for component in front.normal)
        assert front.area_m2 == back.area_m2 == patch.area_m2
        assert front.point_m == tuple(
            patch.centroid[index] + patch.unit_normal[index] * 1e-4
            for index in range(3)
        )
        assert back.point_m == tuple(
            patch.centroid[index] - patch.unit_normal[index] * 1e-4
            for index in range(3)
        )


def test_rex_receiver_rows_and_artifact_preserve_scientific_metadata() -> None:
    plant = rex_plant()
    typed_samples = build_two_sided_patch_receivers(plant)
    samples = build_radiance_receiver_samples(plant)
    payload = build_patch_receiver_artifact_payload(plant, typed_samples)

    assert len(samples) == 2 * plant.patch_count
    assert len(receiver_sample_input_text(samples).splitlines()) == len(samples)
    assert samples[0]["receiver_id"] == typed_samples[0].receiver_id
    assert samples[0]["leaf_layer"] in {"outer", "mid", "inner"}
    assert payload["face_count"] == plant.face_count
    assert payload["patch_count"] == plant.patch_count
    assert payload["receiver_count"] == 2 * plant.patch_count


@pytest.mark.parametrize(
    ("granularity", "samples_per_leaf"),
    [
        (RECEIVER_GRANULARITY_LEAF_CENTROID, 1),
        (RECEIVER_GRANULARITY_LEAF_QUADRATURE_4, 4),
        (RECEIVER_GRANULARITY_MESH_PATCH, 32),
    ],
)
def test_receiver_sample_counts_and_formatting(granularity: str, samples_per_leaf: int) -> None:
    samples = build_radiance_receiver_samples(scene(), receiver_granularity=granularity)
    assert len(samples) == 2 * samples_per_leaf
    text = receiver_sample_input_text(samples)
    assert len(text.splitlines()) == len(samples)
    assert all(len(line.split()) == 6 for line in text.splitlines())


def test_mesh_samples_are_two_sided_and_traceable() -> None:
    generated = scene()
    samples = build_radiance_receiver_samples(
        generated, receiver_granularity=RECEIVER_GRANULARITY_MESH_PATCH
    )
    assert len(samples) == 2 * len(leaf_absorption_surfaces(generated))
    by_surface: dict[str, set[str]] = {}
    for sample in samples:
        by_surface.setdefault(sample["surface_id"], set()).add(sample["side"])
    assert all(sides == {"front", "back"} for sides in by_surface.values())


def test_receiver_output_parser_uses_rgb_mean_and_validates_rows() -> None:
    assert parse_rtrace_receiver_output("1 2 3\n4 4 4\n") == [2.0, 4.0]
    with pytest.raises(ValueError, match="Malformed"):
        parse_rtrace_receiver_output("1 2\n")
    with pytest.raises(ValueError, match="Invalid"):
        parse_rtrace_receiver_output("1 -1 1\n")


def test_two_sided_receiver_rows_sum_side_densities() -> None:
    generated = scene()
    samples = build_radiance_receiver_samples(
        generated, receiver_granularity=RECEIVER_GRANULARITY_MESH_PATCH
    )
    rows = build_radiance_receiver_surface_flux_rows(
        generated, samples, [10.0] * len(samples)
    )
    assert len(rows) == len(leaf_absorption_surfaces(generated))
    assert all(row["incident_photon_flux_density_umol_m2_s"] == pytest.approx(20.0) for row in rows)
    assert all(len(row["side_summaries"]) == 2 for row in rows)


def test_representative_receiver_rows_are_area_weighted() -> None:
    generated = scene()
    samples = build_radiance_receiver_samples(
        generated, receiver_granularity=RECEIVER_GRANULARITY_LEAF_QUADRATURE_4
    )
    values = [50.0 + index for index in range(len(samples))]
    rows = build_radiance_receiver_surface_flux_rows(generated, samples, values)
    assert len(rows) == len(leaf_absorption_surfaces(generated))
    assert all(row["incident_photon_flux_density_umol_m2_s"] > 0.0 for row in rows)


def test_surface_flux_payload_aggregates_area_and_absorption() -> None:
    generated = scene()
    samples = build_radiance_receiver_samples(
        generated, receiver_granularity=RECEIVER_GRANULARITY_LEAF_CENTROID
    )
    rows = build_radiance_receiver_surface_flux_rows(
        generated, samples, [100.0] * len(samples)
    )
    payload = build_plant_surface_flux_payload(
        generated,
        rows,
        method=RADIANCE_RECEIVER_METHOD,
        target_ppfd_umol_m2_s=100.0,
        target_tolerance_umol_m2_s=5.0,
    )
    expected_incident = payload["one_sided_leaf_area_m2"] * 100.0
    assert payload["total_incident_photon_flux_umol_s"] == pytest.approx(expected_incident)
    assert payload["total_absorbed_photon_flux_umol_s"] == pytest.approx(
        expected_incident * generated.config.optical.absorptance
    )
    assert payload["plant_count"] == 1
    assert payload["leaf_count"] == 2
    assert payload["target_range_leaf_count"] == 2
    assert all(
        row["absorptance"] == generated.config.optical.absorptance
        for row in payload["surface_summaries"]
    )
    assert all(
        row["lighting_region"] == "target_range"
        for row in payload["surface_summaries"]
    )


def test_artifact_writer_is_deterministic(tmp_path) -> None:
    generated = scene()
    samples = build_radiance_receiver_samples(
        generated, receiver_granularity=RECEIVER_GRANULARITY_LEAF_CENTROID
    )
    rows = build_radiance_receiver_surface_flux_rows(
        generated, samples, [100.0] * len(samples)
    )
    payload = build_plant_surface_flux_payload(
        generated, rows, method=RADIANCE_RECEIVER_METHOD
    )
    path = write_plant_surface_flux_artifact(tmp_path, payload, compact=False)
    first = path.read_text(encoding="utf-8")
    path = write_plant_surface_flux_artifact(tmp_path, payload, compact=False)
    assert path.read_text(encoding="utf-8") == first
    assert json.loads(first)["surface_count"] == len(
        leaf_absorption_surfaces(generated)
    )
