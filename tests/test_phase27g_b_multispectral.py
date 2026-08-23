from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import fspm_optics.application.multispectral as multispectral_module

from fspm_optics.application.baseline_leaf_uniformity import (
    build_baseline_leaf_uniformity_publication,
    build_baseline_physical_leaf_scene,
)
from fspm_optics.application.multispectral import (
    BAND_ORDER,
    CANONICAL_COUNTS_PER_PLANT,
    PAR_BAND_ORDER,
    JuvenileBandInput,
    JuvenileMultispectralError,
    JuvenileMultispectralPublication,
    JuvenileSourceAdapter,
    _decode_rgb_to_f64le,
    execute_juvenile_multispectral_transport,
)
from fspm_optics.application.source_state import PhysicalSourceState
from fspm_optics.geometry.room import (
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    RoomDimensions,
    production_room_model_payload,
    room_radiance_text,
)
from fspm_optics.application.publication import (
    NATIVE_BASELINE_PUBLICATION_SCHEMA_VERSION,
)
from fspm_optics.application.proposed import (
    ProposedRunError,
    _validate_stage_b_source_state_reuse,
    _validate_multispectral_transport_artifacts,
)
from fspm_optics.plants.radiance_scene_export import (
    DEFAULT_LEAF_MATERIAL_MODIFIER,
)
from fspm_optics.plants.natural_fit import plan_natural_fit_layout_from_feet
from fspm_optics.optics.rex_material_plan import (
    build_rex_radiance_trans_material_plan,
    render_radiance_trans_material,
)
from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.runner import RunnerResult
from fspm_optics.transport.scalar_ppfd import PpfdMapSample
from fspm_optics.web.app import create_app


RUN_ID = "b" * 32
EMITTED_PAR_PPF_UMOL_S = 1000.0
MODELED_ELECTRICAL_POWER_W = 400.0
NATIVE_SPECTRAL_BASIS = {
    "id": "native_proposed",
    "label": "Native Proposed spectrum",
    "source_model_id": "proposed_led_smd_relative_spectral_mix_v2",
}


class _GreyRunner:
    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult:
        del timeout_s
        assert command.stdout_path is not None
        command.stdout_path.parent.mkdir(parents=True, exist_ok=True)
        if command.label.startswith("compile_juvenile_multispectral_"):
            command.stdout_path.write_bytes(b"fake-octree\n")
        else:
            assert command.label.startswith("trace_juvenile_multispectral_")
            assert command.stdin_path is not None
            with command.stdin_path.open("r", encoding="ascii") as receivers:
                with command.stdout_path.open("w", encoding="ascii") as output:
                    for row_index, _line in enumerate(receivers):
                        value = float(row_index + 1)
                        output.write(f"{value} {value} {value}\n")
        resolved_stderr = None if stderr_path is None else Path(stderr_path)
        if resolved_stderr is not None:
            resolved_stderr.write_text("", encoding="utf-8")
        return RunnerResult(
            command_label=command.label,
            argv=command.argv,
            returncode=0,
            stdout_path=command.stdout_path,
            stderr_text="",
            stderr_path=resolved_stderr,
            wall_time_s=0.0,
            success=True,
        )


def _state_and_adapter(
    root: Path,
) -> tuple[PhysicalSourceState, JuvenileSourceAdapter]:
    state = PhysicalSourceState.create(
        system_id="proposed",
        layout_identity={"layout_id": "test-layout"},
        full_output_schedule={"policy": "test-final-schedule"},
        operating_point={"dimming_factor": 0.5},
        source_operation={
            "global_linear_dimming_factor": 0.5,
            "effective_watts_by_control_zone": [50.0],
            "effective_watts_by_module": [50.0],
        },
    )
    material_plan = build_rex_radiance_trans_material_plan()
    fixture_body = root / "fixture_body_instances.rad"
    fixture_body.parent.mkdir(parents=True, exist_ok=True)
    fixture_body.write_text("# authenticated fixture body\n", encoding="utf-8")
    adapter = JuvenileSourceAdapter(
        system_id="proposed",
        source_state_id=state.source_state_id,
        room_text=room_radiance_text(RoomDimensions(0.6096, 0.6096, 3.048)),
        bands=tuple(
            JuvenileBandInput(
                band_id=band_id,
                source_text=f"# fake isolated source: {band_id}\n",
                material_text=render_radiance_trans_material(
                    DEFAULT_LEAF_MATERIAL_MODIFIER,
                    material_plan.material(band_id).parameters,
                ),
                source_provenance={
                    "band_id": band_id,
                    "stage_a_global_dimming_factor": 0.5,
                    "zone_amplitudes": [
                        {
                            "control_zone_index": 0,
                            "module_count": 1,
                            "module_wattage": 50.0,
                        }
                    ],
                },
                material_provenance=material_plan.material(band_id).to_dict(),
            )
            for band_id in BAND_ORDER
        ),
        source_policy={
            "target_control_recomputed": False,
            "global_dimming_factor": 0.5,
        },
        quality_profile="direct",
        threads=1,
        oconv_bin="oconv",
        rtrace_bin="rtrace",
        fixture_body_source_path=fixture_body,
        fixture_occlusion={
            "system_id": "proposed",
            "identity_sha256": "a" * 64,
        },
        spectral_basis=NATIVE_SPECTRAL_BASIS,
    )
    return state, adapter


def _promotion_manifest(
    publication: JuvenileMultispectralPublication,
    *,
    include_far_red: bool,
) -> dict[str, object]:
    public = publication.public_artifacts
    return {
        "request": {
            "include_far_red": include_far_red,
            "spectral_basis": "native_proposed",
        },
        "spectral_basis": NATIVE_SPECTRAL_BASIS
        | {
            "applied_to_multispectral_transport": True,
            "stage_a_scalar_baseline_is_spd_independent": True,
        },
        "multispectral_transport": publication.manifest_payload(),
        "fspm_scientific_aggregation": (
            publication.scientific_aggregation.manifest_payload()
        ),
        "artifacts": {
            "multispectral_transport_metadata": public["transport_metadata"],
            "multispectral_compact_receiver_index": public[
                "compact_receiver_index"
            ],
            "multispectral_plant_origins": public["plant_origins"],
            **{
                key: value
                for key, value in public.items()
                if key.startswith("band_")
            },
            **publication.scientific_aggregation.public_artifacts,
        },
        "room": {"length_ft": 2.0, "width_ft": 2.0},
        "layout": {"modules": [{"control_zone_index": 0}]},
        "available_visual_outputs": {
            "raw_multispectral_fspm_receivers": True,
            "fspm_surface_light_metrics": True,
        },
    }


def test_equal_grey_rows_publish_exact_ordered_float64_values(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "receiver.rgb"
    values = tmp_path / "receiver-values.v1.f64le.bin"
    raw.write_text("1 1 1\n2.5 2.5 2.5\n0 0 0\n", encoding="utf-8")

    record = _decode_rgb_to_f64le(
        raw,
        values,
        expected_count=3,
        band_id="blue",
        root=tmp_path,
    )

    payload = values.read_bytes()
    assert struct.unpack("<3d", payload) == (1.0, 2.5, 0.0)
    assert record.row_count == 3
    assert record.byte_length == 24
    assert record.sha256 == hashlib.sha256(payload).hexdigest()
    assert record.path == "receiver-values.v1.f64le.bin"


@pytest.mark.parametrize(
    ("rows", "expected_count", "message"),
    [
        ("1 2 1\n", 1, "invalid"),
        ("1 1 1\n", 2, "row count mismatch"),
        ("1 1 1\n2 2 2\n", 1, "extra receiver rows"),
        ("nan nan nan\n", 1, "invalid"),
        ("-1 -1 -1\n", 1, "invalid"),
    ],
)
def test_receiver_decode_fails_closed_without_publishing_partial_values(
    tmp_path: Path,
    rows: str,
    expected_count: int,
    message: str,
) -> None:
    raw = tmp_path / "receiver.rgb"
    values = tmp_path / "receiver-values.v1.f64le.bin"
    raw.write_text(rows, encoding="utf-8")

    with pytest.raises(JuvenileMultispectralError, match=message):
        _decode_rgb_to_f64le(
            raw,
            values,
            expected_count=expected_count,
            band_id="red",
            root=tmp_path,
        )

    assert not values.exists()
    assert not tuple(tmp_path.glob(".receiver-values.v1.f64le.bin.*.tmp"))


def test_shared_transport_enforces_order_counts_and_raw_only_outputs(
    tmp_path: Path,
) -> None:
    state, adapter = _state_and_adapter(tmp_path)
    events: list[tuple[str, dict[str, object]]] = []
    publication = execute_juvenile_multispectral_transport(
        root=tmp_path,
        run_id=RUN_ID,
        room_length_ft=2.0,
        room_width_ft=2.0,
        source_state=state,
        adapter=adapter,
        emitted_par_ppf_umol_s=EMITTED_PAR_PPF_UMOL_S,
        modeled_electrical_power_w=MODELED_ELECTRICAL_POWER_W,
        fspm_reference={
            "mode": "override",
            "resolved_target_umol_m2_s": 1200.0,
        },
        event_sink=lambda kind, _message, data=None: events.append(
            (kind, dict(data or {}))
        ),
        runner=_GreyRunner(),
    )

    metadata = publication.metadata
    plant = metadata["plant"]
    assert isinstance(plant, dict)
    assert plant["canonical_counts_per_plant"] == CANONICAL_COUNTS_PER_PLANT
    assert metadata["schema_version"] == 4
    assert metadata["room_model"] == production_room_model_payload()
    assert (
        metadata["room_model_identity_sha256"]
        == PRODUCTION_ROOM_MODEL_IDENTITY_SHA256
    )
    assert publication.metadata_artifact.path == "fspm-transport/transport.v4.json"
    assert metadata["include_far_red"] is False
    assert metadata["band_order"] == list(PAR_BAND_ORDER)
    assert metadata["executed_band_order"] == list(PAR_BAND_ORDER)
    assert metadata["par_band_order"] == list(PAR_BAND_ORDER)
    assert metadata["far_red_executed"] is False
    assert metadata["far_red_preserved_separately"] is True
    assert metadata["stage_a_operating_point_authority"] == {
        "emitted_par_ppf_umol_s": EMITTED_PAR_PPF_UMOL_S,
        "modeled_electrical_power_w": MODELED_ELECTRICAL_POWER_W,
    }
    absorbed_metrics = publication.scientific_aggregation.room_summary[
        "absorbed_par_metrics"
    ]
    assert absorbed_metrics["authorities"]["emitted_par_ppf_umol_s"] == (
        EMITTED_PAR_PPF_UMOL_S
    )
    assert absorbed_metrics["authorities"]["modeled_electrical_power_w"] == (
        MODELED_ELECTRICAL_POWER_W
    )
    assert metadata["trace_row_mapping"] == (
        "trace row i equals global receiver index i"
    )
    assert metadata["fspm_reference_controls_transport"] is False
    assert metadata["scientific_stage_boundary"] == {
        "raw_transport_authoritative": True,
        "surface_light_aggregation_published_separately": True,
        "surface_coloring_performed": False,
        "symmetry_reconstruction_applied": False,
    }
    bands = metadata["bands"]
    assert isinstance(bands, list)
    assert [record["band_id"] for record in bands] == list(PAR_BAND_ORDER)
    assert all(record["post_trace_transforms"] == [] for record in bands)
    assert all(
        record["scene"]["component_roles"]
        == ["room", "fixture_emitters", "fixture_bodies", "rex_plant"]
        for record in bands
    )
    assert all(
        record["scene"]["ordered_scene_input_roles"]
        == [
            "room",
            "fixture_emitters",
            "fixture_bodies",
            "leaf_material",
            "rex_plant",
        ]
        for record in bands
    )
    assert all(
        record["scene"]["fixture_occlusion_identity"] == "a" * 64
        and record["scene"]["scene_identity"].startswith(
            "juvenile-multispectral-scene-v2-"
        )
        and record["scene"]["octree_identity"].startswith(
            "juvenile-multispectral-octree-v2-"
        )
        and record["scene"]["ambient_cache_identity"].startswith(
            "juvenile-multispectral-ambient-v2-"
        )
        for record in bands
    )
    assert all(
        record["receiver_values"]["row_count"] == metadata["receiver_count"]
        for record in bands
    )
    assert [
        data["band_id"]
        for kind, data in events
        if kind == "analysis.stage.fspm.band.started"
    ] == list(PAR_BAND_ORDER)
    assert "band_far_red_receiver_values" not in publication.public_artifacts
    assert not (tmp_path / "fspm-transport/bands/04-far_red").exists()
    assert all(
        "far_red" not in record["role"] and "far_red" not in record["path"]
        for record in metadata["ordered_artifact_inventory"]
    )
    assert "far_red" not in publication.scientific_aggregation.room_summary[
        "surface_light"
    ]
    assert publication.scientific_aggregation.room_summary["counts"] == plant[
        "global_counts"
    ]
    assert any(
        kind == "analysis.stage.fspm.aggregation.completed"
        for kind, _data in events
    )


def test_far_red_selection_changes_stage_b_and_science_identity_without_changing_stage_a_handoff(
    tmp_path: Path,
) -> None:
    state, adapter = _state_and_adapter(tmp_path)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()

    first = execute_juvenile_multispectral_transport(
        root=first_root,
        run_id="1" * 32,
        room_length_ft=2.0,
        room_width_ft=2.0,
        source_state=state,
        adapter=adapter,
        emitted_par_ppf_umol_s=EMITTED_PAR_PPF_UMOL_S,
        modeled_electrical_power_w=MODELED_ELECTRICAL_POWER_W,
        fspm_reference={"resolved_target_umol_m2_s": 800.0},
        event_sink=lambda *_args: None,
        include_far_red=True,
        runner=_GreyRunner(),
    )
    second = execute_juvenile_multispectral_transport(
        root=second_root,
        run_id="1" * 32,
        room_length_ft=2.0,
        room_width_ft=2.0,
        source_state=state,
        adapter=adapter,
        emitted_par_ppf_umol_s=EMITTED_PAR_PPF_UMOL_S,
        modeled_electrical_power_w=MODELED_ELECTRICAL_POWER_W,
        fspm_reference={"resolved_target_umol_m2_s": 800.0},
        event_sink=lambda *_args: None,
        include_far_red=False,
        runner=_GreyRunner(),
    )

    assert first.metadata["source_state_id"] == second.metadata["source_state_id"]
    assert first.metadata["source_planning"]["bands"][:-1] == (
        second.metadata["source_planning"]["bands"]
    )
    assert first.metadata["source_planning"]["source_policy"] == (
        second.metadata["source_planning"]["source_policy"]
    )
    assert first.metadata["band_order"] == list(BAND_ORDER)
    assert second.metadata["band_order"] == list(PAR_BAND_ORDER)
    assert first.metadata["far_red_executed"] is True
    assert second.metadata["far_red_executed"] is False
    assert "far_red" in first.scientific_aggregation.room_summary["surface_light"]
    assert "far_red" not in second.scientific_aggregation.room_summary[
        "surface_light"
    ]
    assert [
        record["receiver_values"]["sha256"]
        for record in first.metadata["bands"][:-1]
    ] == [
        record["receiver_values"]["sha256"]
        for record in second.metadata["bands"]
    ]
    assert (
        first.scientific_aggregation.room_summary["surface_light"]["par"]
        == second.scientific_aggregation.room_summary["surface_light"]["par"]
    )
    natural_fit = plan_natural_fit_layout_from_feet(2.0, 2.0)
    stage_a_samples = tuple(
        PpfdMapSample(x, y, 0.005, 800.0)
        for y in (-1.0, 1.0)
        for x in (-1.0, 1.0)
    )
    baseline_arguments = {
        "run_id": "1" * 32,
        "system_id": "proposed",
        "analysis_scope": "baseline_plus_multispectral_fspm",
        "samples": stage_a_samples,
        "overlay_payload": {
            "schema_id": "test-authoritative-overlay",
            "room": {"axes_swapped_from_request": False},
        },
        "requested_lighting_target_umol_m2_s": 800.0,
        "achieved_stage_a_mean_umol_m2_s": 800.0,
        "target_mode": "automatic",
        "target_override_umol_m2_s": None,
        "target_tolerance_umol_m2_s": 20.0,
    }
    five_band_baseline_leaf = build_baseline_leaf_uniformity_publication(
        scene=build_baseline_physical_leaf_scene(
            natural_fit,
            canonical_plant=first.juvenile_scene.canonical_plant,
        ),
        **baseline_arguments,
    )
    four_band_baseline_leaf = build_baseline_leaf_uniformity_publication(
        scene=build_baseline_physical_leaf_scene(
            natural_fit,
            canonical_plant=second.juvenile_scene.canonical_plant,
        ),
        **baseline_arguments,
    )
    assert five_band_baseline_leaf.derivation_identity_sha256 == (
        four_band_baseline_leaf.derivation_identity_sha256
    )
    assert NATIVE_BASELINE_PUBLICATION_SCHEMA_VERSION == 4
    assert five_band_baseline_leaf.payload == four_band_baseline_leaf.payload
    assert five_band_baseline_leaf.payload["scientific_dependencies"][
        "far_red_selection"
    ] is False
    assert first.metadata_artifact.sha256 != second.metadata_artifact.sha256
    assert (
        first.scientific_aggregation.metadata_artifact.sha256
        != second.scientific_aggregation.metadata_artifact.sha256
    )
    assert (
        first.scientific_aggregation.public_artifacts["patch_surface_light"]
        == second.scientific_aggregation.public_artifacts["patch_surface_light"]
    )
    assert (
        first_root
        / first.scientific_aggregation.public_artifacts["patch_surface_light"]
    ).read_bytes() != (
        second_root
        / second.scientific_aggregation.public_artifacts["patch_surface_light"]
    ).read_bytes()

    assert _validate_multispectral_transport_artifacts(
        first_root,
        manifest=_promotion_manifest(first, include_far_red=True),
        expected_run_id="1" * 32,
        expected_system_id="proposed",
        expected_source_state_id=state.source_state_id,
        expected_source_state=state.to_dict(),
        expected_fspm_reference={"resolved_target_umol_m2_s": 800.0},
        transport_expected=True,
    ) == (
        first.metadata_artifact.sha256,
        first.scientific_aggregation.metadata_artifact.sha256,
    )


def test_aggregation_failure_prevents_transport_publication_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, adapter = _state_and_adapter(tmp_path)

    def fail_aggregation(**_kwargs):
        raise RuntimeError("forced aggregation failure")

    monkeypatch.setattr(
        multispectral_module,
        "aggregate_juvenile_surface_light",
        fail_aggregation,
    )
    with pytest.raises(
        JuvenileMultispectralError,
        match="scientific surface-light aggregation failed",
    ):
        execute_juvenile_multispectral_transport(
            root=tmp_path,
            run_id=RUN_ID,
            room_length_ft=2.0,
            room_width_ft=2.0,
            source_state=state,
            adapter=adapter,
            emitted_par_ppf_umol_s=EMITTED_PAR_PPF_UMOL_S,
            modeled_electrical_power_w=MODELED_ELECTRICAL_POWER_W,
            fspm_reference={"resolved_target_umol_m2_s": 900.0},
            event_sink=lambda *_args: None,
            runner=_GreyRunner(),
        )


def test_conditional_validator_rejects_corrupt_or_partial_band_inventory(
    tmp_path: Path,
) -> None:
    state, adapter = _state_and_adapter(tmp_path)
    reference = {"mode": "automatic", "resolved_target_umol_m2_s": 900.0}
    publication = execute_juvenile_multispectral_transport(
        root=tmp_path,
        run_id=RUN_ID,
        room_length_ft=2.0,
        room_width_ft=2.0,
        source_state=state,
        adapter=adapter,
        emitted_par_ppf_umol_s=EMITTED_PAR_PPF_UMOL_S,
        modeled_electrical_power_w=MODELED_ELECTRICAL_POWER_W,
        fspm_reference=reference,
        event_sink=lambda *_args: None,
        runner=_GreyRunner(),
    )
    public = publication.public_artifacts
    manifest = _promotion_manifest(publication, include_far_red=False)

    assert _validate_multispectral_transport_artifacts(
        tmp_path,
        manifest=manifest,
        expected_run_id=RUN_ID,
        expected_system_id="proposed",
        expected_source_state_id=state.source_state_id,
        expected_source_state=state.to_dict(),
        expected_fspm_reference=reference,
        transport_expected=True,
    ) == (
        publication.metadata_artifact.sha256,
        publication.scientific_aggregation.metadata_artifact.sha256,
    )

    compact_path = tmp_path / public["compact_receiver_index"]
    compact_bytes = compact_path.read_bytes()
    compact_path.write_bytes(compact_bytes + b" ")
    with pytest.raises(ProposedRunError, match="integrity failed"):
        _validate_multispectral_transport_artifacts(
            tmp_path,
            manifest=manifest,
            expected_run_id=RUN_ID,
            expected_system_id="proposed",
            expected_source_state_id=state.source_state_id,
            expected_source_state=state.to_dict(),
            expected_fspm_reference=reference,
            transport_expected=True,
        )
    compact_path.write_bytes(compact_bytes)

    patch_path = tmp_path / publication.scientific_aggregation.public_artifacts[
        "patch_surface_light"
    ]
    patch_bytes = patch_path.read_bytes()
    patch_path.write_bytes(patch_bytes[:-8])
    with pytest.raises(ProposedRunError, match="scientific artifact integrity failed"):
        _validate_multispectral_transport_artifacts(
            tmp_path,
            manifest=manifest,
            expected_run_id=RUN_ID,
            expected_system_id="proposed",
            expected_source_state_id=state.source_state_id,
            expected_source_state=state.to_dict(),
            expected_fspm_reference=reference,
            transport_expected=True,
        )
    patch_path.write_bytes(patch_bytes)

    blue_path = tmp_path / public["band_blue_receiver_values"]
    blue_path.write_bytes(blue_path.read_bytes() + b"corruption")
    with pytest.raises(ProposedRunError, match="integrity failed"):
        _validate_multispectral_transport_artifacts(
            tmp_path,
            manifest=manifest,
            expected_run_id=RUN_ID,
            expected_system_id="proposed",
            expected_source_state_id=state.source_state_id,
            expected_source_state=state.to_dict(),
            expected_fspm_reference=reference,
            transport_expected=True,
        )


def test_baseline_only_validator_requires_no_stage_b_artifacts(tmp_path: Path) -> None:
    manifest: dict[str, object] = {
        "multispectral_transport": None,
        "fspm_scientific_aggregation": None,
        "artifacts": {},
        "available_visual_outputs": {
            "raw_multispectral_fspm_receivers": False,
            "fspm_surface_light_metrics": False,
        },
    }
    assert _validate_multispectral_transport_artifacts(
        tmp_path,
        manifest=manifest,
        expected_run_id=RUN_ID,
        expected_system_id="proposed",
        expected_source_state_id="physical-source-state-v1-" + "0" * 64,
        expected_source_state={"source_operation": {"unused": True}},
        expected_fspm_reference={},
        transport_expected=False,
    ) == (None, None)

    manifest["artifacts"] = {"band_blue_receiver_values": "unexpected.bin"}
    with pytest.raises(ProposedRunError, match="contains FSPM transport artifacts"):
        _validate_multispectral_transport_artifacts(
            tmp_path,
            manifest=manifest,
            expected_run_id=RUN_ID,
            expected_system_id="proposed",
            expected_source_state_id="physical-source-state-v1-" + "0" * 64,
            expected_source_state={"source_operation": {"unused": True}},
            expected_fspm_reference={},
            transport_expected=False,
        )


@pytest.mark.parametrize("system_id", ["proposed", "conventional", "hps"])
def test_validator_enforces_each_systems_exact_stage_a_source_reuse(
    system_id: str,
) -> None:
    if system_id == "proposed":
        source_state = {
            "source_operation": {
                "global_linear_dimming_factor": 0.5,
                "effective_watts_by_control_zone": [50.0],
                "effective_watts_by_module": [50.0, 50.0],
            }
        }
        layout: object = {
            "modules": [
                {"control_zone_index": 0},
                {"control_zone_index": 0},
            ]
        }
        source_policy = {
            "global_dimming_factor": 0.5,
            "target_control_recomputed": False,
        }
        sources = [
            {
                "band_id": band_id,
                "stage_a_global_dimming_factor": 0.5,
                "zone_amplitudes": [
                    {"module_count": 2, "module_wattage": 50.0}
                ],
            }
            for band_id in BAND_ORDER
        ]
    elif system_id == "conventional":
        source_state = {
            "source_operation": {
                "global_dimming_factor": 0.5,
                "effective_fixture_ppf_umol_s": 858.0,
                "effective_carrier_multiplier_per_fixture": 153582.0,
            }
        }
        layout = {}
        source_policy = {
            "global_dimming_factor": 0.5,
            "target_control_recomputed": False,
        }
        sources = [
            {
                "band_id": band_id,
                "stage_a_global_dimming_factor": 0.5,
                "effective_per_fixture_ppf_umol_s": (
                    214.5 if band_id != "far_red" else 10.0
                ),
                "carrier_multiplier": (
                    38395.5 if band_id != "far_red" else 1790.0
                ),
            }
            for band_id in BAND_ORDER
        ]
    else:
        source_state = {
            "source_operation": {
                "operation": "fixed_full_output",
                "dimming_supported": False,
                "fixture_ppf_umol_s": 1750.0,
            }
        }
        layout = {}
        source_policy = {
            "operation": "fixed_full_output",
            "dimming_supported": False,
            "target_control_recomputed": False,
            "post_trace_scaling": False,
        }
        sources = [
            {
                "band_id": band_id,
                "operation": "fixed_full_output",
                "per_fixture_ppf_umol_s": {
                    "blue": 89.2116024205964,
                    "green": 883.0134958872596,
                    "orange": 425.62729704396367,
                    "red": 352.1476046481803,
                    "far_red": 86.74954838701679,
                }[band_id],
            }
            for band_id in BAND_ORDER
        ]
    planning = {"source_policy": source_policy}
    bands: list[object] = [{"source": source} for source in sources]

    _validate_stage_b_source_state_reuse(
        system_id,
        source_state,
        layout,
        planning,
        bands,
    )

    source_policy["target_control_recomputed"] = True
    with pytest.raises(ProposedRunError, match="does not reuse"):
        _validate_stage_b_source_state_reuse(
            system_id,
            source_state,
            layout,
            planning,
            bands,
        )


def test_fspm_route_serves_only_exact_manifest_declared_artifacts(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    app = create_app(runtime_root=runtime)
    with TestClient(app) as client:
        run_root = runtime / "completed" / RUN_ID
        stage_root = run_root / "fspm-transport"
        aggregation_root = run_root / "fspm-aggregation"
        stage_root.mkdir(parents=True)
        aggregation_root.mkdir()
        (stage_root / "transport.v1.json").write_text("{}\n", encoding="utf-8")
        (stage_root / "not-declared.json").write_text("{}\n", encoding="utf-8")
        (aggregation_root / "room-summary.v1.json").write_text(
            '{"scope":"modeled surfaces"}\n',
            encoding="utf-8",
        )
        (run_root / "manifest.json").write_text(
            "{\n"
            '  "multispectral_transport": {\n'
            '    "public_artifacts": {\n'
            '      "transport_metadata": "fspm-transport/transport.v1.json"\n'
            "    }\n"
            "  }\n"
            '  ,"fspm_scientific_aggregation": {\n'
            '    "public_artifacts": {\n'
            '      "room_surface_light_summary": "fspm-aggregation/room-summary.v1.json"\n'
            "    }\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

        allowed = client.get(
            f"/api/runs/{RUN_ID}/fspm/transport.v1.json"
        )
        undeclared = client.get(
            f"/api/runs/{RUN_ID}/fspm/not-declared.json"
        )
        aggregation = client.get(
            f"/api/runs/{RUN_ID}/fspm/room-summary.v1.json"
        )

        assert allowed.status_code == 200
        assert allowed.json() == {}
        assert aggregation.status_code == 200
        assert aggregation.json() == {"scope": "modeled surfaces"}
        assert undeclared.status_code == 404
