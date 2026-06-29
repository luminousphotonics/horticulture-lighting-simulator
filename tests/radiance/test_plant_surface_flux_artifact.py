from __future__ import annotations

import json

import pytest

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.engine.plants import PlantGeometryConfig, generate_plant_scene  # noqa: E402
from rad_rebuild.radiance.engine.plants.surface_flux import (  # noqa: E402
    BASELINE_PPFD_PROXY_METHOD,
    SPATIAL_PPFD_PROXY_METHOD,
    RADIANCE_RECEIVER_METHOD,
    PLANT_SURFACE_FLUX_SCHEMA,
    build_baseline_proxy_surface_flux_rows,
    build_radiance_receiver_samples,
    build_radiance_receiver_surface_flux_rows,
    build_spatial_proxy_surface_flux_rows,
    build_plant_surface_flux_payload,
    parse_rtrace_receiver_output,
    read_ppfd_map_field,
    receiver_sample_input_text,
    write_radiance_receiver_plant_surface_flux_artifact,
    write_baseline_proxy_plant_surface_flux_artifact,
    write_spatial_proxy_plant_surface_flux_artifact,
)


def _scene():
    return generate_plant_scene(
        PlantGeometryConfig(
            seed=19,
            plant_grid_rows=2,
            plant_grid_columns=1,
            leaf_count_per_plant=4,
        )
    )


def _single_plant_scene(leaf_count: int = 4):
    return generate_plant_scene(
        PlantGeometryConfig(
            seed=19,
            plant_grid_rows=1,
            plant_grid_columns=1,
            leaf_count_per_plant=leaf_count,
        )
    )


def _with_density(row: dict[str, object], density: float) -> dict[str, object]:
    area = float(row["area_m2"])
    return {
        **row,
        "incident_photon_flux_density_umol_m2_s": density,
        "incident_photon_flux_umol_s": density * area,
    }


def _rows_for_leaf_densities(scene, densities_by_leaf_index: dict[int, float]) -> list[dict[str, object]]:
    rows = build_baseline_proxy_surface_flux_rows(scene, 1.0)
    return [
        _with_density(row, densities_by_leaf_index[int(row["leaf_index"])])
        for row in rows
    ]


def test_surface_flux_payload_aggregates_leaf_and_plant_absorption() -> None:
    scene = _scene()
    rows = build_baseline_proxy_surface_flux_rows(scene, 1000.0)
    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=BASELINE_PPFD_PROXY_METHOD,
        source_ppfd_map="ppfd_map.txt",
        baseline_ppfd_mean_umol_m2_s=1000.0,
    )

    assert payload["schema"] == PLANT_SURFACE_FLUX_SCHEMA
    assert payload["status"] == "proxy"
    assert payload["plant_count"] == 2
    assert payload["leaf_count"] == 8
    assert payload["surface_count"] == len(rows)
    assert payload["total_absorbed_photon_flux_umol_s"] > 0
    assert payload["plant_to_plant_absorbed_photon_flux_cv"] >= 0
    assert len(payload["plant_summaries"]) == 2
    assert len(payload["leaf_summaries"]) == 8
    assert all("plant_id" in row for row in payload["leaf_summaries"])
    assert all("leaf_index" in row for row in payload["leaf_summaries"])
    assert payload["visualization"]["color_metric"] == "absorbed_photon_flux_density_umol_m2_s"
    assert payload["visualization"]["leaf_values"]
    assert all("plant_id" in row for row in payload["visualization"]["leaf_values"])


def test_surface_flux_target_threshold_boundaries() -> None:
    scene = _single_plant_scene(leaf_count=4)
    rows = _rows_for_leaf_densities(
        scene,
        {
            0: 254.0,
            1: 255.0,
            2: 295.0,
            3: 296.0,
        },
    )

    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=BASELINE_PPFD_PROXY_METHOD,
        target_ppfd_umol_m2_s=275.0,
        target_tolerance_umol_m2_s=20.0,
    )

    regions = {
        int(row["leaf_index"]): row["lighting_region"]
        for row in payload["leaf_summaries"]
    }
    assert regions == {
        0: "under_lit",
        1: "target_range",
        2: "target_range",
        3: "over_lit",
    }
    assert payload["under_lit_leaf_count"] == 1
    assert payload["target_range_leaf_count"] == 2
    assert payload["over_lit_leaf_count"] == 1
    assert (
        payload["target"]["target_classification_basis"]
        == "plant_surface_receiver_incident_ppfd_fallback"
    )
    assert (
        payload["target"]["target_classification_note"]
        == "Fallback target classification uses plant-surface receiver incident PPFD "
        "and is not directly equivalent to a horizontal canopy-plane target PPFD."
    )


def test_surface_flux_leaf_classification_uses_area_weighted_ppfd() -> None:
    scene = _single_plant_scene(leaf_count=1)
    rows = build_baseline_proxy_surface_flux_rows(scene, 1.0)
    smallest = min(rows, key=lambda row: float(row["area_m2"]))
    hotspot_id = smallest["surface_id"]
    weighted_rows = [_with_density(row, 1.0) for row in rows]
    target_classification = {
        str(row["surface_id"]): 5000.0 if row["surface_id"] == hotspot_id else 0.0
        for row in rows
    }
    unweighted_mean = sum(
        target_classification[str(row["surface_id"])] for row in weighted_rows
    ) / len(weighted_rows)

    payload = build_plant_surface_flux_payload(
        scene,
        weighted_rows,
        method=BASELINE_PPFD_PROXY_METHOD,
        target_ppfd_umol_m2_s=275.0,
        target_tolerance_umol_m2_s=20.0,
        target_classification_ppfd_by_surface_id=target_classification,
        target_classification_metadata={
            "target_classification_basis": "test_canopy_plane_equivalent_ppfd",
            "target_classification_basis_label": "test canopy-plane equivalent PPFD",
            "target_classification_source": "unit_test_surface_mapping",
            "target_classification_note": "Unit-test classification values keep raw flux separate.",
        },
    )

    leaf = payload["leaf_summaries"][0]
    assert unweighted_mean > payload["target_upper_threshold_umol_m2_s"]
    assert leaf["target_classification_ppfd_umol_m2_s"] < payload["target_lower_threshold_umol_m2_s"]
    assert leaf["incident_photon_flux_density_umol_m2_s"] == pytest.approx(1.0)
    assert leaf["lighting_region"] == "under_lit"
    assert payload["under_lit_leaf_count"] == 1


def test_target_capped_metrics_are_not_inflated_by_hotspots() -> None:
    scene = _single_plant_scene(leaf_count=1)
    base_rows = build_baseline_proxy_surface_flux_rows(scene, 1.0)
    total_area = sum(float(row["area_m2"]) for row in base_rows)
    sorted_rows = sorted(base_rows, key=lambda row: float(row["area_m2"]))
    hotspot_rows = sorted_rows[: len(sorted_rows) // 2]
    hotspot_area = sum(float(row["area_m2"]) for row in hotspot_rows)
    hotspot_density = 275.0 * total_area / hotspot_area
    hotspot_ids = {row["surface_id"] for row in hotspot_rows}

    uniform_payload = build_plant_surface_flux_payload(
        scene,
        [_with_density(row, 275.0) for row in base_rows],
        method=BASELINE_PPFD_PROXY_METHOD,
        target_ppfd_umol_m2_s=275.0,
        target_tolerance_umol_m2_s=20.0,
    )
    hotspot_payload = build_plant_surface_flux_payload(
        scene,
        [
            _with_density(row, hotspot_density if row["surface_id"] in hotspot_ids else 0.0)
            for row in base_rows
        ],
        method=BASELINE_PPFD_PROXY_METHOD,
        target_ppfd_umol_m2_s=275.0,
        target_tolerance_umol_m2_s=20.0,
    )

    assert hotspot_payload["raw_total_flux_umol_s"] == pytest.approx(
        uniform_payload["raw_total_flux_umol_s"]
    )
    assert hotspot_payload["target_capped_incident_flux_total_umol_s"] < uniform_payload[
        "target_capped_incident_flux_total_umol_s"
    ]
    assert hotspot_payload["target_capped_flux_total_umol_s"] == pytest.approx(
        hotspot_payload["target_capped_incident_flux_total_umol_s"]
    )
    assert hotspot_payload["excess_incident_flux_above_target_umol_s"] > 0
    assert hotspot_payload["deficit_to_target_incident_flux_umol_s"] > 0


def test_surface_flux_artifact_export_is_deterministic(tmp_path) -> None:
    scene = _scene()

    first_path = write_baseline_proxy_plant_surface_flux_artifact(
        tmp_path,
        scene,
        baseline_ppfd_mean_umol_m2_s=1000.0,
    )
    first = first_path.read_text(encoding="utf-8")
    second_path = write_baseline_proxy_plant_surface_flux_artifact(
        tmp_path,
        scene,
        baseline_ppfd_mean_umol_m2_s=1000.0,
    )
    second = second_path.read_text(encoding="utf-8")

    assert first_path == second_path
    assert first == second


def test_surface_flux_rejects_missing_surface_ids() -> None:
    scene = _scene()
    rows = build_baseline_proxy_surface_flux_rows(scene, 1000.0)

    with pytest.raises(ValueError, match="Missing incident photon flux"):
        build_plant_surface_flux_payload(
            scene,
            rows[:-1],
            method=BASELINE_PPFD_PROXY_METHOD,
        )


def test_surface_flux_rejects_invalid_leaf_ids() -> None:
    scene = _scene()
    rows = build_baseline_proxy_surface_flux_rows(scene, 1000.0)
    rows[0] = {**rows[0], "leaf_id": "not_the_surface_leaf"}

    with pytest.raises(ValueError, match="belongs to leaf_id"):
        build_plant_surface_flux_payload(
            scene,
            rows,
            method=BASELINE_PPFD_PROXY_METHOD,
        )


def test_surface_flux_summaries_do_not_use_crop_output_language() -> None:
    scene = _scene()
    rows = build_baseline_proxy_surface_flux_rows(scene, 1000.0)
    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=BASELINE_PPFD_PROXY_METHOD,
    )

    summary_text = json.dumps(
        {
            "plant_summaries": payload["plant_summaries"],
            "leaf_summaries": payload["leaf_summaries"],
            "visualization": payload["visualization"],
        },
        sort_keys=True,
    ).lower()

    for forbidden in ("yield", "biomass", "crop_output"):
        assert forbidden not in summary_text


def _write_gradient_ppfd_map(path):
    path.write_text(
        "\n".join(
            [
                "-1 -1 0 200",
                "1 -1 0 600",
                "-1 1 0 400",
                "1 1 0 1000",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_uniform_ppfd_map(path, ppfd: float) -> None:
    path.write_text(
        "\n".join(
            [
                f"-1 -1 0 {ppfd}",
                f"1 -1 0 {ppfd}",
                f"-1 1 0 {ppfd}",
                f"1 1 0 {ppfd}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_target_classification_gradient_map(path) -> None:
    path.write_text(
        "\n".join(
            [
                "-1 0 0 125",
                "0 0 0 275",
                "1 0 0 425",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_ppfd_map_field_bilinear_interpolation(tmp_path) -> None:
    ppfd_path = tmp_path / "ppfd_map.txt"
    _write_gradient_ppfd_map(ppfd_path)

    field = read_ppfd_map_field(ppfd_path)

    assert field.rectangular is True
    assert field.sample(-1, -1) == pytest.approx(200)
    assert field.sample(1, 1) == pytest.approx(1000)
    assert field.sample(0, 0) == pytest.approx(550)


def test_ppfd_map_target_classification_keeps_all_values_in_range(tmp_path) -> None:
    scene = _single_plant_scene(leaf_count=4)
    ppfd_path = tmp_path / "ppfd_map.txt"
    _write_uniform_ppfd_map(ppfd_path, 275.0)
    raw_receiver_rows = [
        _with_density(row, 1000.0)
        for row in build_baseline_proxy_surface_flux_rows(scene, 1.0)
    ]

    payload = build_plant_surface_flux_payload(
        scene,
        raw_receiver_rows,
        method=RADIANCE_RECEIVER_METHOD,
        target_ppfd_umol_m2_s=275.0,
        target_tolerance_umol_m2_s=20.0,
        target_classification_ppfd_map_path=ppfd_path,
    )

    assert payload["target_classification_basis"] == "canopy_plane_equivalent_incident_ppfd"
    assert payload["target_classification_source"] == "interpolated_runtime_ppfd_map"
    assert payload["target_range_leaf_count"] == payload["leaf_count"]
    assert payload["under_lit_leaf_count"] == 0
    assert payload["over_lit_leaf_count"] == 0
    assert payload["raw_mean_flux_density_umol_m2_s"] > payload[
        "target_upper_threshold_umol_m2_s"
    ]
    assert payload["target_classification_mean_ppfd_umol_m2_s"] == pytest.approx(275.0)
    assert payload["target_capped_incident_flux_total_umol_s"] == pytest.approx(
        payload["target_capped_flux_total_umol_s"]
    )
    assert payload["target_capped_incident_mean_flux_density_umol_m2_s"] == pytest.approx(
        payload["target_capped_mean_flux_density_umol_m2_s"]
    )
    assert all(
        row["target_classification_ppfd_umol_m2_s"] == pytest.approx(275.0)
        for row in payload["surface_summaries"]
    )


def test_ppfd_map_target_classification_finds_below_inside_and_above(tmp_path) -> None:
    scene = generate_plant_scene(
        PlantGeometryConfig(
            seed=19,
            plant_grid_rows=1,
            plant_grid_columns=3,
            plant_spacing_m=0.5,
            leaf_count_per_plant=1,
        )
    )
    ppfd_path = tmp_path / "ppfd_map.txt"
    _write_target_classification_gradient_map(ppfd_path)
    raw_receiver_rows = [
        _with_density(row, 900.0)
        for row in build_baseline_proxy_surface_flux_rows(scene, 1.0)
    ]

    payload = build_plant_surface_flux_payload(
        scene,
        raw_receiver_rows,
        method=RADIANCE_RECEIVER_METHOD,
        target_ppfd_umol_m2_s=275.0,
        target_tolerance_umol_m2_s=20.0,
        target_classification_ppfd_map_path=ppfd_path,
    )

    leaf_regions = {
        row["plant_id"]: row["lighting_region"] for row in payload["leaf_summaries"]
    }
    assert leaf_regions == {
        "plant_r000_c000": "under_lit",
        "plant_r000_c001": "target_range",
        "plant_r000_c002": "over_lit",
    }
    assert payload["under_lit_leaf_count"] == 1
    assert payload["target_range_leaf_count"] == 1
    assert payload["over_lit_leaf_count"] == 1
    assert payload["target"]["target_classification_source"] == "interpolated_runtime_ppfd_map"


def test_spatial_proxy_samples_leaf_centroids_from_ppfd_map(tmp_path) -> None:
    scene = _scene()
    ppfd_path = tmp_path / "ppfd_map.txt"
    _write_gradient_ppfd_map(ppfd_path)

    rows = build_spatial_proxy_surface_flux_rows(scene, ppfd_path)
    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=SPATIAL_PPFD_PROXY_METHOD,
        source_ppfd_map="ppfd_map.txt",
        ppfd_field_summary=read_ppfd_map_field(ppfd_path).summary(),
        target_classification_ppfd_map_path=ppfd_path,
    )

    sampled_values = {round(float(row["sampled_ppfd_umol_m2_s"]), 6) for row in rows}

    assert len(sampled_values) > 1
    assert payload["method"] == SPATIAL_PPFD_PROXY_METHOD
    assert payload["status"] == "proxy"
    assert payload["ppfd_field_summary"]["rectangular"] is True
    assert payload["target_classification_source"] == "interpolated_runtime_ppfd_map"
    assert payload["total_absorbed_photon_flux_umol_s"] > 0


def test_spatial_proxy_artifact_export_is_deterministic(tmp_path) -> None:
    scene = _scene()
    ppfd_path = tmp_path / "ppfd_map.txt"
    _write_gradient_ppfd_map(ppfd_path)

    first_path = write_spatial_proxy_plant_surface_flux_artifact(
        tmp_path,
        scene,
        ppfd_map_path=ppfd_path,
    )
    first = first_path.read_text(encoding="utf-8")
    second_path = write_spatial_proxy_plant_surface_flux_artifact(
        tmp_path,
        scene,
        ppfd_map_path=ppfd_path,
    )
    second = second_path.read_text(encoding="utf-8")

    assert first_path == second_path
    assert first == second


def test_radiance_receiver_samples_are_two_sided_and_traceable() -> None:
    scene = _scene()
    samples = build_radiance_receiver_samples(scene, two_sided=True)

    surface_ids = {sample["surface_id"] for sample in samples}

    assert len(samples) == len(surface_ids) * 2
    assert {sample["side"] for sample in samples} == {"front", "back"}
    assert receiver_sample_input_text(samples).count("\n") == len(samples)


def test_receiver_output_parser_uses_rgb_mean_density() -> None:
    values = parse_rtrace_receiver_output("1 2 3\n4 5 6\n")

    assert values == pytest.approx([2.0, 5.0])


def test_radiance_receiver_surface_flux_rows_sum_two_sided_density() -> None:
    scene = _scene()
    samples = build_radiance_receiver_samples(scene, two_sided=True)
    densities = [100.0 if sample["side"] == "front" else 25.0 for sample in samples]

    rows = build_radiance_receiver_surface_flux_rows(scene, samples, densities)

    assert len(rows) * 2 == len(samples)
    assert all(row["receiver_sample_count"] == 2 for row in rows)
    assert all(set(row["receiver_sides"]) == {"front", "back"} for row in rows)
    assert all(row["incident_photon_flux_density_umol_m2_s"] == pytest.approx(125.0) for row in rows)


def test_radiance_receiver_surface_flux_payload_is_computed(tmp_path) -> None:
    scene = _scene()
    samples = build_radiance_receiver_samples(scene, two_sided=True)
    densities = [100.0 if sample["side"] == "front" else 25.0 for sample in samples]

    path = write_radiance_receiver_plant_surface_flux_artifact(
        tmp_path,
        scene,
        samples,
        densities,
        source_octree="test.oct",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["status"] == "computed"
    assert payload["method"] == RADIANCE_RECEIVER_METHOD
    assert payload["baseline_transport_scene"] == "room_emitters_only"
    assert payload["fspm_receiver_transport_scene"] == "room_emitters_plants"
    assert payload["receiver_trace_count"] == 1
    assert payload["ppfd_field_summary"]["two_sided"] is True
    assert payload["total_absorbed_photon_flux_umol_s"] > 0


def test_radiance_receiver_target_classification_prefers_ppfd_map(tmp_path) -> None:
    scene = _single_plant_scene(leaf_count=2)
    samples = build_radiance_receiver_samples(scene, two_sided=True)
    densities = [600.0 for _sample in samples]
    ppfd_path = tmp_path / "ppfd_map.txt"
    _write_uniform_ppfd_map(ppfd_path, 275.0)

    path = write_radiance_receiver_plant_surface_flux_artifact(
        tmp_path,
        scene,
        samples,
        densities,
        source_octree="test.oct",
        target_ppfd_umol_m2_s=275.0,
        target_tolerance_umol_m2_s=20.0,
        target_classification_ppfd_map_path=ppfd_path,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["raw_mean_flux_density_umol_m2_s"] > payload[
        "target_upper_threshold_umol_m2_s"
    ]
    assert payload["target_classification_source"] == "interpolated_runtime_ppfd_map"
    assert payload["target_range_leaf_count"] == payload["leaf_count"]
    assert payload["under_lit_leaf_count"] == 0
    assert payload["over_lit_leaf_count"] == 0
