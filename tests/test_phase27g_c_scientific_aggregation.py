from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct

import pytest

from fspm_optics.application.fspm_science import (
    BAND_ORDER,
    FspmScientificAggregationError,
    PAR_BAND_ORDER,
    aggregate_juvenile_surface_light,
    binary_contract_for_band_order,
    compute_absorbed_par_metrics,
)
from fspm_optics.optics.rex_material_plan import (
    build_rex_radiance_trans_material_plan,
    render_radiance_trans_material,
)
from fspm_optics.plants.multi_scene import build_juvenile_natural_fit_scene
from fspm_optics.plants.natural_fit import (
    REX_JUVENILE_PREHEADING_LOCAL_XY_BOUNDS_M,
    plan_natural_fit_layout,
)
from fspm_optics.plants.radiance_scene_export import (
    DEFAULT_LEAF_MATERIAL_MODIFIER,
)


RUN_ID = "c" * 32
SOURCE_STATE_ID = "physical-source-state-v1-" + "d" * 64
TRANSPORT_SHA256 = "e" * 64
COMPACT_INDEX_SHA256 = "f" * 64
EMITTED_PAR_PPF_UMOL_S = 1000.0
MODELED_ELECTRICAL_POWER_W = 400.0
REPOSITORY = Path(__file__).parents[1]


def _one_plant_scene():
    bounds = REX_JUVENILE_PREHEADING_LOCAL_XY_BOUNDS_M
    layout = plan_natural_fit_layout(
        bounds.span_x_m + 0.02,
        bounds.span_y_m + 0.02,
    )
    return build_juvenile_natural_fit_scene(layout)


def _stage_authorities(root: Path, *, include_far_red: bool = True):
    scene = _one_plant_scene()
    material_plan = build_rex_radiance_trans_material_plan()
    records: list[dict[str, object]] = []
    band_order = BAND_ORDER if include_far_red else PAR_BAND_ORDER
    for order_index, band_id in enumerate(band_order):
        band_root = root / "fspm-transport" / "bands" / f"{order_index:02d}-{band_id}"
        band_root.mkdir(parents=True)
        material = material_plan.material(band_id)
        material_text = render_radiance_trans_material(
            DEFAULT_LEAF_MATERIAL_MODIFIER,
            material.parameters,
        )
        material_path = band_root / "leaf-material.rad"
        material_path.write_text(material_text, encoding="utf-8")
        front = float(order_index + 1)
        back = 2.0 * front
        raw = b"".join(
            struct.pack("<dd", front, back)
            for _patch_index in range(scene.counts.patch_count)
        )
        raw_path = band_root / "receiver-values.v1.f64le.bin"
        raw_path.write_bytes(raw)
        relative = raw_path.relative_to(root).as_posix()
        records.append(
            {
                "order_index": order_index,
                "band_id": band_id,
                "material": material.to_dict(),
                "material_sha256": hashlib.sha256(
                    material_text.encode("utf-8")
                ).hexdigest(),
                "receiver_values": {
                    "role": f"{band_id}_receiver_values",
                    "path": relative,
                    "media_type": "application/octet-stream",
                    "byte_length": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "row_count": scene.counts.receiver_count,
                },
            }
        )
    return scene, records, material_plan


def _aggregate(
    root: Path,
    *,
    chunk_bytes: int = 23,
    include_far_red: bool = True,
    system_id: str = "proposed",
):
    scene, records, material_plan = _stage_authorities(
        root, include_far_red=include_far_red
    )
    publication = aggregate_juvenile_surface_light(
        root=root,
        run_id=RUN_ID,
        system_id=system_id,
        source_state_id=SOURCE_STATE_ID,
        scene=scene,
        transport_metadata_path="fspm-transport/transport.v3.json",
        transport_metadata_sha256=TRANSPORT_SHA256,
        band_records=records,
        compact_receiver_index={
            "schema_id": "fspm-optics.juvenile-compact-receiver-index",
            "schema_version": 1,
            "scene_hash": scene.scene_hash,
        },
        compact_receiver_index_sha256=COMPACT_INDEX_SHA256,
        emitted_par_ppf_umol_s=EMITTED_PAR_PPF_UMOL_S,
        modeled_electrical_power_w=MODELED_ELECTRICAL_POWER_W,
        chunk_bytes=chunk_bytes,
        include_far_red=include_far_red,
    )
    return scene, records, material_plan, publication


def test_uniform_absorbed_exposures_have_zero_cv_and_unity_minimum_mean() -> None:
    metrics = compute_absorbed_par_metrics(
        modeled_physical_one_sided_leaf_area_m2=5.0,
        total_combined_absorbed_par_rate_umol_s=50.0,
        emitted_par_ppf_umol_s=100.0,
        modeled_electrical_power_w=20.0,
        plant_records=((0, 2.0, 20.0), (1, 3.0, 30.0)),
        leaf_records=(
            (0, 0, 1.0, 10.0),
            (1, 0, 1.0, 10.0),
            (2, 1, 1.0, 10.0),
            (3, 1, 2.0, 20.0),
        ),
    )

    assert metrics["plant_to_plant_absorbed_exposure_cv_percent"] == 0.0
    assert metrics["leaf_to_leaf_absorbed_exposure_cv_percent"] == 0.0
    assert metrics["plant_minimum_to_mean_absorbed_exposure_ratio"] == 1.0
    assert metrics["absorbed_capture_efficiency_percent"] == 50.0
    assert metrics["absorbed_par_per_electrical_watt_umol_per_j"] == 2.5


def test_unequal_absorbed_exposures_use_population_statistics_and_identity() -> None:
    metrics = compute_absorbed_par_metrics(
        modeled_physical_one_sided_leaf_area_m2=4.0,
        total_combined_absorbed_par_rate_umol_s=60.0,
        emitted_par_ppf_umol_s=120.0,
        modeled_electrical_power_w=30.0,
        plant_records=((0, 2.0, 20.0), (1, 2.0, 40.0)),
        leaf_records=(
            (0, 0, 1.0, 5.0),
            (1, 0, 1.0, 15.0),
            (2, 1, 1.0, 10.0),
            (3, 1, 1.0, 30.0),
        ),
    )

    assert metrics["plant_to_plant_absorbed_exposure_cv_percent"] == pytest.approx(
        100.0 / 3.0
    )
    assert metrics["plant_minimum_to_mean_absorbed_exposure_ratio"] == pytest.approx(
        2.0 / 3.0
    )
    assert metrics["leaf_to_leaf_absorbed_exposure_cv_percent"] == pytest.approx(
        (87.5**0.5) / 15.0 * 100.0
    )


def test_absorbed_metrics_use_area_weighted_rate_and_one_sided_area_once() -> None:
    metrics = compute_absorbed_par_metrics(
        modeled_physical_one_sided_leaf_area_m2=4.0,
        total_combined_absorbed_par_rate_umol_s=70.0,
        emitted_par_ppf_umol_s=140.0,
        modeled_electrical_power_w=35.0,
        plant_records=((0, 4.0, 70.0),),
        leaf_records=((0, 0, 1.0, 10.0), (1, 0, 3.0, 60.0)),
    )

    assert metrics["total_combined_absorbed_par_rate_umol_s"] == 70.0
    assert metrics["combined_absorbed_exposure_umol_m2_s"] == 17.5
    assert metrics["absorbed_capture_efficiency_percent"] == 50.0
    assert metrics["absorbed_par_per_electrical_watt_umol_per_j"] == 2.0


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"modeled_physical_one_sided_leaf_area_m2": 0.0}, "positive"),
        ({"emitted_par_ppf_umol_s": 0.0}, "positive"),
        ({"modeled_electrical_power_w": float("nan")}, "finite"),
        ({"leaf_records": ((0, 3, 1.0, 10.0),)}, "ownership"),
        ({"plant_records": ((0, 1.0, 0.0),)}, "conserve"),
    ],
)
def test_invalid_absorbed_metric_inputs_fail_closed(
    overrides: dict[str, object], match: str
) -> None:
    arguments: dict[str, object] = {
        "modeled_physical_one_sided_leaf_area_m2": 1.0,
        "total_combined_absorbed_par_rate_umol_s": 10.0,
        "emitted_par_ppf_umol_s": 20.0,
        "modeled_electrical_power_w": 5.0,
        "plant_records": ((0, 1.0, 10.0),),
        "leaf_records": ((0, 0, 1.0, 10.0),),
    }
    arguments.update(overrides)
    with pytest.raises(FspmScientificAggregationError, match=match):
        compute_absorbed_par_metrics(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize("system_id", ["proposed", "conventional", "hps"])
def test_all_systems_publish_the_same_absorbed_metric_contract(
    tmp_path: Path, system_id: str
) -> None:
    root = tmp_path / system_id
    root.mkdir()
    _scene, _records, _materials, publication = _aggregate(
        root, include_far_red=False, system_id=system_id
    )
    metrics = publication.room_summary["absorbed_par_metrics"]

    assert publication.room_summary["system_id"] == system_id
    assert set(metrics) == {
        "total_combined_absorbed_par_rate_umol_s",
        "combined_absorbed_exposure_umol_m2_s",
        "absorbed_capture_efficiency_percent",
        "absorbed_par_per_electrical_watt_umol_per_j",
        "plant_to_plant_absorbed_exposure_cv_percent",
        "plant_minimum_to_mean_absorbed_exposure_ratio",
        "leaf_to_leaf_absorbed_exposure_cv_percent",
        "authorities",
        "units",
        "method",
    }


@pytest.mark.parametrize("include_far_red", [False, True])
def test_patch_equations_apply_area_once_and_keep_optional_far_red_separate(
    tmp_path: Path,
    include_far_red: bool,
) -> None:
    scene, records, material_plan, publication = _aggregate(
        tmp_path, include_far_red=include_far_red
    )
    executed_band_order = BAND_ORDER if include_far_red else PAR_BAND_ORDER
    patch_struct, _index_fields, patch_float_fields = (
        binary_contract_for_band_order(executed_band_order)["patch"]
    )
    patch_path = tmp_path / publication.public_artifacts["patch_surface_light"]
    unpacked = patch_struct.unpack(patch_path.read_bytes()[: patch_struct.size])
    indices = unpacked[:4]
    values = dict(zip(patch_float_fields, unpacked[4:], strict=True))
    area = next(scene.iter_patches()).area_m2
    par_front_incident = 1.0 + 2.0 + 3.0 + 4.0
    par_back_incident = 2.0 * par_front_incident
    expected_par_front_absorbed = sum(
        (order_index + 1)
        * material_plan.material(band_id).source_interval.coefficients.absorptance
        for order_index, band_id in enumerate(BAND_ORDER[:4])
    )
    expected_par_front_transmitted = sum(
        (order_index + 1)
        * material_plan.material(band_id).source_interval.coefficients.transmittance
        for order_index, band_id in enumerate(BAND_ORDER[:4])
    )
    expected_par_front_reflected = sum(
        (order_index + 1)
        * material_plan.material(band_id).source_interval.coefficients.reflectance
        for order_index, band_id in enumerate(BAND_ORDER[:4])
    )
    expected_par_back_absorbed = 2.0 * expected_par_front_absorbed

    assert indices == (0, 0, 0, 0)
    assert values["physical_one_sided_patch_area_m2"] == area
    assert values["par_front_incident_photon_flux_density_umol_m2_s"] == 10.0
    assert values["par_back_incident_photon_flux_density_umol_m2_s"] == 20.0
    assert values["par_front_absorbed_photon_flux_density_umol_m2_s"] == pytest.approx(
        expected_par_front_absorbed
    )
    assert values[
        "par_front_transmitted_photon_flux_density_umol_m2_s"
    ] == pytest.approx(expected_par_front_transmitted)
    assert values[
        "par_front_reflected_photon_flux_density_umol_m2_s"
    ] == pytest.approx(expected_par_front_reflected)
    assert values["par_front_incident_photon_flux_density_umol_m2_s"] == pytest.approx(
        expected_par_front_absorbed
        + expected_par_front_transmitted
        + expected_par_front_reflected
    )
    assert values["par_combined_incident_photon_rate_umol_s"] == pytest.approx(
        (par_front_incident + par_back_incident) * area
    )
    assert values["par_combined_incident_photon_rate_umol_s"] != pytest.approx(
        (par_front_incident + par_back_incident) * (2.0 * area)
    )
    assert values["par_combined_absorbed_photon_rate_umol_s"] == pytest.approx(
        (expected_par_front_absorbed + expected_par_back_absorbed) * area
    )
    assert values["par_combined_absorbed_photon_rate_umol_s"] != pytest.approx(
        (expected_par_front_absorbed + expected_par_back_absorbed) * (2.0 * area)
    )
    assert publication.metadata["schema_version"] == 2
    assert publication.metadata["band_order"] == list(executed_band_order)
    assert publication.metadata["far_red_executed"] is include_far_red
    patch_schema = publication.metadata["binary_schemas"]["patch"]
    assert patch_schema["stride_bytes"] == patch_struct.size
    assert set(patch_schema["value_field_offsets_bytes"]) == set(
        patch_float_fields
    )
    if include_far_red:
        assert values["far_red_front_incident_photon_flux_density_umol_m2_s"] == 5.0
        assert values["far_red_back_incident_photon_flux_density_umol_m2_s"] == 10.0
        assert records[-1]["band_id"] == "far_red"
        assert "far_red" in publication.room_summary["surface_light"]
    else:
        assert all("far_red" not in field for field in patch_float_fields)
        assert "far_red" not in publication.room_summary["surface_light"]
        assert all(
            authority["band_id"] != "far_red"
            for authority in publication.metadata[
                "material_coefficient_authorities"
            ]
        )
        assert set(publication.room_summary["raw_receiver_sha256_by_band"]) == set(
            PAR_BAND_ORDER
        )


def test_room_rollup_is_area_weighted_conserved_and_deterministic_across_chunks(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    scene, records, _materials, first = _aggregate(first_root, chunk_bytes=7)
    _scene, _records, _materials, second = _aggregate(second_root, chunk_bytes=31)
    room = first.room_summary
    par = room["surface_light"]["par"]
    combined = par["combined_exposure_per_physical_one_sided_leaf_area"]

    assert room["modeled_physical_one_sided_leaf_area_m2"] == pytest.approx(
        sum(patch.area_m2 for patch in scene.iter_patches())
    )
    assert combined["incident_photon_flux_density_umol_m2_s"] == pytest.approx(30.0)
    assert par["photon_rates_umol_s"]["incident_photon_rate_umol_s"] == pytest.approx(
        30.0 * room["modeled_physical_one_sided_leaf_area_m2"]
    )
    absorbed = room["absorbed_par_metrics"]
    assert absorbed["total_combined_absorbed_par_rate_umol_s"] == pytest.approx(
        combined["absorbed_photon_flux_density_umol_m2_s"]
        * room["modeled_physical_one_sided_leaf_area_m2"]
    )
    assert absorbed["authorities"][
        "modeled_physical_one_sided_leaf_area_m2"
    ] == room["modeled_physical_one_sided_leaf_area_m2"]
    assert absorbed["method"]["area_weighting"] == (
        "physical_one_sided_receiver_area_applied_once"
    )
    assert max(room["closure"].values()) < 1e-6
    assert room["raw_receiver_sha256_by_band"] == {
        record["band_id"]: record["receiver_values"]["sha256"]
        for record in records
    }
    assert room["closure"]["maximum_patch_to_leaf_conservation_error_umol_s"] == 0.0
    assert room["closure"]["maximum_leaf_to_plant_conservation_error_umol_s"] == pytest.approx(0.0)
    assert room["closure"]["maximum_plant_to_room_conservation_error_umol_s"] == pytest.approx(0.0)
    assert first.room_summary["surface_light"] == second.room_summary["surface_light"]
    for role in (
        "aggregation_metadata",
        "patch_surface_light",
        "leaf_surface_light",
        "plant_surface_light",
        "room_surface_light_summary",
        "scientific_derivation_graph",
    ):
        assert (
            (first_root / first.public_artifacts[role]).read_bytes()
            == (second_root / second.public_artifacts[role]).read_bytes()
        )


def test_raw_hash_or_material_authority_disagreement_fails_closed(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    material_root = tmp_path / "material"
    raw_root.mkdir()
    material_root.mkdir()
    scene, raw_records, _material_plan = _stage_authorities(raw_root)
    raw_records[0]["receiver_values"]["sha256"] = "0" * 64
    with pytest.raises(FspmScientificAggregationError, match="hash changed"):
        aggregate_juvenile_surface_light(
            root=raw_root,
            run_id=RUN_ID,
            system_id="proposed",
            source_state_id=SOURCE_STATE_ID,
            scene=scene,
            transport_metadata_path="fspm-transport/transport.v3.json",
            transport_metadata_sha256=TRANSPORT_SHA256,
            band_records=raw_records,
            compact_receiver_index={"schema_version": 1},
            compact_receiver_index_sha256=COMPACT_INDEX_SHA256,
            emitted_par_ppf_umol_s=EMITTED_PAR_PPF_UMOL_S,
            modeled_electrical_power_w=MODELED_ELECTRICAL_POWER_W,
            chunk_bytes=13,
        )
    assert not any((raw_root / "fspm-aggregation").iterdir())

    scene, material_records, _material_plan = _stage_authorities(material_root)
    corrupted = deepcopy(material_records)
    original = corrupted[0]["material"]["original_atr"]
    original["absorptance"], original["reflectance"] = (
        original["reflectance"],
        original["absorptance"],
    )
    with pytest.raises(FspmScientificAggregationError, match="does not match"):
        aggregate_juvenile_surface_light(
            root=material_root,
            run_id=RUN_ID,
            system_id="proposed",
            source_state_id=SOURCE_STATE_ID,
            scene=scene,
            transport_metadata_path="fspm-transport/transport.v3.json",
            transport_metadata_sha256=TRANSPORT_SHA256,
            band_records=corrupted,
            compact_receiver_index={"schema_version": 1},
            compact_receiver_index_sha256=COMPACT_INDEX_SHA256,
            emitted_par_ppf_umol_s=EMITTED_PAR_PPF_UMOL_S,
            modeled_electrical_power_w=MODELED_ELECTRICAL_POWER_W,
        )


@pytest.mark.parametrize("invalid_value", [float("nan"), -1.0])
def test_nonfinite_or_negative_receiver_value_is_rejected(
    tmp_path: Path,
    invalid_value: float,
) -> None:
    scene, records, _material_plan = _stage_authorities(tmp_path)
    record = records[0]["receiver_values"]
    raw_path = tmp_path / record["path"]
    raw = bytearray(raw_path.read_bytes())
    raw[:8] = struct.pack("<d", invalid_value)
    raw_path.write_bytes(raw)
    record["sha256"] = hashlib.sha256(raw).hexdigest()

    with pytest.raises(FspmScientificAggregationError, match="receiver density"):
        aggregate_juvenile_surface_light(
            root=tmp_path,
            run_id=RUN_ID,
            system_id="proposed",
            source_state_id=SOURCE_STATE_ID,
            scene=scene,
            transport_metadata_path="fspm-transport/transport.v3.json",
            transport_metadata_sha256=TRANSPORT_SHA256,
            band_records=records,
            compact_receiver_index={"schema_version": 1},
            compact_receiver_index_sha256=COMPACT_INDEX_SHA256,
            emitted_par_ppf_umol_s=EMITTED_PAR_PPF_UMOL_S,
            modeled_electrical_power_w=MODELED_ELECTRICAL_POWER_W,
            chunk_bytes=15,
        )


def test_truncated_or_extra_receiver_artifact_is_rejected(tmp_path: Path) -> None:
    for suffix, transform in (
        ("truncated", lambda value: value[:-8]),
        ("extra", lambda value: value + struct.pack("<d", 1.0)),
    ):
        root = tmp_path / suffix
        root.mkdir()
        scene, records, _material_plan = _stage_authorities(root)
        record = records[0]["receiver_values"]
        raw_path = root / record["path"]
        invalid = transform(raw_path.read_bytes())
        raw_path.write_bytes(invalid)
        record["byte_length"] = len(invalid)
        record["sha256"] = hashlib.sha256(invalid).hexdigest()
        with pytest.raises(FspmScientificAggregationError, match="authority is invalid"):
            aggregate_juvenile_surface_light(
                root=root,
                run_id=RUN_ID,
                system_id="proposed",
                source_state_id=SOURCE_STATE_ID,
                scene=scene,
                transport_metadata_path="fspm-transport/transport.v3.json",
                transport_metadata_sha256=TRANSPORT_SHA256,
                band_records=records,
                compact_receiver_index={"schema_version": 1},
                compact_receiver_index_sha256=COMPACT_INDEX_SHA256,
                emitted_par_ppf_umol_s=EMITTED_PAR_PPF_UMOL_S,
                modeled_electrical_power_w=MODELED_ELECTRICAL_POWER_W,
            )


def test_public_json_is_bounded_and_contains_no_patch_arrays(tmp_path: Path) -> None:
    _scene, _records, _materials, publication = _aggregate(tmp_path)
    metadata_path = tmp_path / publication.public_artifacts["aggregation_metadata"]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    serialized = metadata_path.read_text(encoding="utf-8")

    assert '"patches": [' not in serialized
    assert '"receivers": [' not in serialized
    assert "target_ppfd" not in serialized
    assert metadata["memory_complexity"]["resident_five_band_arrays"] is False
    assert metadata["far_red_preserved_separately"] is True


def test_ui_keeps_surface_light_separate_from_spatial_uniformity() -> None:
    html = (REPOSITORY / "src/fspm_optics/resources/web/index.html").read_text(
        encoding="utf-8"
    )
    script = (
        REPOSITORY / "src/fspm_optics/resources/web/result-view.js"
    ).read_text(encoding="utf-8")

    assert html.count("FSPM Surface-Light Metrics") == 1
    assert 'id="fspm-surface-section" hidden' in html
    assert "Area-weighted modeled plant surfaces</span>" in html
    assert "separate from spatial uniformity" not in html
    assert "Far-red combined incident exposure" in html
    assert "Scientific artifacts" not in html
    assert "fspm_scientific_artifact_urls" not in script
    assert "photon_rates_umol_s" not in script
    assert "fspmSurfaceSection.hidden = false" in script
    assert "fspmSurfaceSection.hidden = true" in script
    uniformity_block = html[html.index('id="uniformity-grid"') : html.index(
        'id="fspm-surface-section"'
    )]
    assert "fspm-par" not in uniformity_block
