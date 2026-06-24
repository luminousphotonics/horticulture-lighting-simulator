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


def test_ppfd_map_field_bilinear_interpolation(tmp_path) -> None:
    ppfd_path = tmp_path / "ppfd_map.txt"
    _write_gradient_ppfd_map(ppfd_path)

    field = read_ppfd_map_field(ppfd_path)

    assert field.rectangular is True
    assert field.sample(-1, -1) == pytest.approx(200)
    assert field.sample(1, 1) == pytest.approx(1000)
    assert field.sample(0, 0) == pytest.approx(550)


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
    )

    sampled_values = {round(float(row["sampled_ppfd_umol_m2_s"]), 6) for row in rows}

    assert len(sampled_values) > 1
    assert payload["method"] == SPATIAL_PPFD_PROXY_METHOD
    assert payload["status"] == "proxy"
    assert payload["ppfd_field_summary"]["rectangular"] is True
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
    assert payload["ppfd_field_summary"]["two_sided"] is True
    assert payload["total_absorbed_photon_flux_umol_s"] > 0
