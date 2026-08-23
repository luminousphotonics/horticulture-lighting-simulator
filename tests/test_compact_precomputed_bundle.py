from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
import shutil
import struct
from typing import Callable
import zipfile

import pytest

from fspm_optics.application.domain import (
    CONVENTIONAL_SYSTEM_ID,
    EMITTED_PPF_BOUNDARIES,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    parse_run_request,
)
from fspm_optics.application.proposed import _layout_identity
from fspm_optics.application.publication import (
    RunSciencePublication,
    publish_native_baseline_run,
)
from fspm_optics.application.source_state import PhysicalSourceState
from fspm_optics.application.visualization import VisualizationReference
from fspm_optics.fixtures.conventional_led.layout import (
    MountReferencePlaneSemantics,
    plan_conventional_layout_from_feet,
)
from fspm_optics.fixtures.hps.layout import plan_hps_layout_from_feet
from fspm_optics.fixtures.proposed_cob.source import (
    resolve_proposed_source_authority,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.precomputed import (
    CompactBundleError,
    CompactBundleStatus,
    export_compact_bundle,
    load_compact_bundle,
    materialize_compact_playback,
    measure_compact_bundle,
    normalize_live_public_payload,
    validate_compact_bundle,
)
from fspm_optics.precomputed import compact_bundle
from fspm_optics.precomputed import playback as fixed_playback
from fspm_optics.precomputed.fixed_plan import build_fixed_sweep_plan
from fspm_optics.precomputed.playback import RequestedCompactPlayback
from fspm_optics.precomputed.target_adjustment import (
    TARGET_ADJUSTMENT_CAPABILITY,
    TARGET_CAPPED_ADJUSTMENT_CAPABILITY,
    derive_target_adjusted_playback,
    validate_requested_target_ppfd,
)
from fspm_optics.radiance.options import radiance_options
from fspm_optics.transport.conventional_scalar import (
    CONVENTIONAL_FIXTURE_POWER_W,
    CONVENTIONAL_FIXTURE_PPF_UMOL_S,
)
from fspm_optics.transport.hps_scalar import (
    HPS_FIXTURE_POWER_W,
    HPS_FIXTURE_PPF_UMOL_S,
)
from fspm_optics.transport.scalar_ppfd import PpfdMapSample


SYSTEM_CASES = (
    (PROPOSED_SYSTEM_ID, "a" * 32, 18.0),
    (CONVENTIONAL_SYSTEM_ID, "c" * 32, 18.0),
    (HPS_SYSTEM_ID, "f" * 32, 24.0),
)


def _publish_completed_runtime(
    root: Path,
    system_id: str,
    run_id: str,
    *,
    requested_target: float = 900.0,
    target_tolerance: float = 20.0,
    conventional_layout_mode: str = "practical",
) -> Path:
    request_payload: dict[str, object] = {
        "system": system_id,
        "room_length_ft": 10.0,
        "room_width_ft": 10.0,
        "mounting_height_in": 24.0 if system_id == HPS_SYSTEM_ID else 18.0,
        "quality": "direct",
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": target_tolerance,
    }
    if system_id != HPS_SYSTEM_ID:
        request_payload["target_ppfd"] = requested_target
    if system_id == CONVENTIONAL_SYSTEM_ID:
        request_payload["layout_mode"] = conventional_layout_mode
    request = parse_run_request(request_payload)
    mounting = request.mounting_geometry
    samples = tuple(
        PpfdMapSample(x_m, y_m, mounting.reference_plane_z_m, value)
        for (x_m, y_m), value in zip(
            ((-0.5, -0.5), (0.5, -0.5), (-0.5, 0.5), (0.5, 0.5)),
            (
                (200.0, 225.0, 275.0, 300.0)
                if requested_target == 250.0
                else (850.0, 875.0, 925.0, 950.0)
            ),
            strict=True,
        )
    )

    proposed_source: dict[str, object] | None = None
    if system_id == PROPOSED_SYSTEM_ID:
        layout = generate_proposed_led_layout(
            10.0,
            10.0,
            mount_z_m=mounting.emitting_aperture_plane_z_m,
        )
        layout_identity = _layout_identity(layout)
        full_output_power = 100.0
        effective_power = 25.0 if requested_target == 250.0 else 90.0
        output_fraction = effective_power / full_output_power
        ppf = {
            "emitted_umol_s": 260.0 * output_fraction,
            "emission_boundary_id": EMITTED_PPF_BOUNDARIES[system_id]["id"],
            "emission_boundary_description": EMITTED_PPF_BOUNDARIES[system_id][
                "description"
            ],
            "internal_source_ppe_umol_per_j": 3.120711832761085,
            "completed_aperture_fixture_ppe_umol_per_j": 2.6,
            "full_output_internal_par_ppf_umol_s": 320.9706636558342,
            "effective_internal_par_ppf_umol_s": (
                320.9706636558342 * output_fraction
            ),
            "full_output_modeled_completed_aperture_par_ppf_umol_s": 260.0,
            "effective_modeled_completed_aperture_par_ppf_umol_s": (
                260.0 * output_fraction
            ),
        }
        proposed_source = resolve_proposed_source_authority().to_dict()
        source_operation = {
            "policy_id": "test_stage_a_source",
            "lighting_target_mode": request.lighting_target_mode.value,
            "proposed_source": proposed_source,
            "proposed_layout_mode": layout.proposed_layout_mode.value,
            "proposed_ring_mode": layout.proposed_ring_mode.value,
            "module_pattern_id": layout.module_pattern_id,
            "fixture_policy_id": layout.fixture_policy_id,
            "mechanical_envelope": {
                "id": layout.mechanical_envelope_id,
                "width_x_m": layout.module_footprint_x_m,
                "height_y_m": layout.module_footprint_y_m,
            },
            "fixture_asset_set_id": layout.fixture_asset_set_id,
            "mounting_height": mounting.to_payload(),
        }
        counts = {
            "fixture_groups": len(layout.fixtures),
            "fixtures": len(layout.fixtures),
            "modules": len(layout.modules),
            "control_zones": layout.control_zone_count,
        }
        target_control = {
            "requested_target_ppfd_umol_m2_s": requested_target,
            "full_output_mean_ppfd_umol_m2_s": 1000.0,
            "full_output_maximum_ppfd_umol_m2_s": (
                max(sample.ppfd_umol_m2_s for sample in samples)
                / output_fraction
            ),
            "achieved_mean_ppfd_umol_m2_s": requested_target,
            "achieved_maximum_ppfd_umol_m2_s": max(
                sample.ppfd_umol_m2_s for sample in samples
            ),
            "dimming_factor": output_fraction,
            "feasible": True,
            "infeasibility": None,
        }
        target_feasible: bool | None = True
        reference = VisualizationReference.requested_target(requested_target)
    elif system_id == CONVENTIONAL_SYSTEM_ID:
        layout = plan_conventional_layout_from_feet(
            10.0,
            10.0,
            policy=conventional_layout_mode,
            mount=MountReferencePlaneSemantics(
                reference_plane_z_m=mounting.reference_plane_z_m,
                mount_height_m=mounting.mounting_height_m,
            ),
        )
        layout_identity = layout.to_payload() | {
            "active_domain": request.active_domain.to_payload()
        }
        fixture_count = len(layout.fixtures)
        full_output_power = fixture_count * CONVENTIONAL_FIXTURE_POWER_W
        output_fraction = 0.25 if requested_target == 250.0 else 1.0
        effective_power = full_output_power * output_fraction
        emitted_ppf = fixture_count * CONVENTIONAL_FIXTURE_PPF_UMOL_S
        ppf = {
            "emitted_umol_s": emitted_ppf * output_fraction,
            "emission_boundary_id": EMITTED_PPF_BOUNDARIES[system_id]["id"],
            "emission_boundary_description": EMITTED_PPF_BOUNDARIES[system_id][
                "description"
            ],
            "ppe_umol_per_j": (
                CONVENTIONAL_FIXTURE_PPF_UMOL_S
                / CONVENTIONAL_FIXTURE_POWER_W
            ),
            "full_output_umol_s": emitted_ppf,
            "effective_umol_s": emitted_ppf * output_fraction,
        }
        source_operation = {
            "policy_id": "test_conventional_stage_a_source",
            "mounting_height": mounting.to_payload(),
        }
        counts = {
            "fixture_groups": fixture_count,
            "fixtures": fixture_count,
            "modules": 0,
            "control_zones": 1,
        }
        target_control = {
            "requested_target_ppfd_umol_m2_s": requested_target,
            "full_output_mean_ppfd_umol_m2_s": 1000.0,
            "full_output_maximum_ppfd_umol_m2_s": (
                max(sample.ppfd_umol_m2_s for sample in samples)
                / output_fraction
            ),
            "achieved_mean_ppfd_umol_m2_s": requested_target,
            "achieved_maximum_ppfd_umol_m2_s": max(
                sample.ppfd_umol_m2_s for sample in samples
            ),
            "dimming_factor": output_fraction,
            "feasible": True,
            "infeasibility": None,
            "full_output_power_w": full_output_power,
            "effective_power_w": effective_power,
            "native_carrier_numeric_format": "%g (six significant digits)",
        }
        target_feasible = True
        reference = VisualizationReference.requested_target(requested_target)
    else:
        layout = plan_hps_layout_from_feet(
            10.0,
            10.0,
            reference_plane_z_m=mounting.reference_plane_z_m,
            mount_height_m=mounting.mounting_height_m,
        )
        layout_identity = layout.to_payload() | {
            "active_domain": request.active_domain.to_payload()
        }
        fixture_count = len(layout.fixtures)
        full_output_power = fixture_count * HPS_FIXTURE_POWER_W
        effective_power = full_output_power
        emitted_ppf = fixture_count * HPS_FIXTURE_PPF_UMOL_S
        ppf = {
            "emitted_umol_s": emitted_ppf,
            "emission_boundary_id": EMITTED_PPF_BOUNDARIES[system_id]["id"],
            "emission_boundary_description": EMITTED_PPF_BOUNDARIES[system_id][
                "description"
            ],
            "ppe_umol_per_j": HPS_FIXTURE_PPF_UMOL_S / HPS_FIXTURE_POWER_W,
            "full_output_umol_s": emitted_ppf,
            "effective_umol_s": emitted_ppf,
        }
        source_operation = {
            "policy_id": "test_hps_stage_a_source",
            "mounting_height": mounting.to_payload(),
        }
        counts = {
            "fixture_groups": fixture_count,
            "fixtures": fixture_count,
            "modules": 0,
            "control_zones": 0,
        }
        target_control = {
            "schema_id": "fspm-optics.hps-fixed-output-control",
            "schema_version": 2,
            "lighting_target_supported": False,
            "operation": "full_output_only",
            "full_output_mean_ppfd_umol_m2_s": 900.0,
            "achieved_mean_ppfd_umol_m2_s": 900.0,
            "dimming_supported": False,
            "post_trace_scaling": False,
            "post_trace_symmetrization": False,
        }
        target_feasible = None
        reference = VisualizationReference.achieved_baseline_mean(900.0)

    schedule = {
        "policy": "test_full_output",
        "fixture_count": counts["fixtures"],
        **(
            {"full_output_power_w": full_output_power}
            if system_id == CONVENTIONAL_SYSTEM_ID
            else {}
        ),
    }
    operating_point = {
        "power": {
            "full_output_w": full_output_power,
            "effective_w": effective_power,
        },
        "ppf": ppf,
        **({"proposed_source": proposed_source} if proposed_source else {}),
    }
    source_state = PhysicalSourceState.create(
        system_id=system_id,
        layout_identity=layout_identity,
        full_output_schedule=schedule,
        operating_point=operating_point,
        source_operation=source_operation,
    )
    science = RunSciencePublication(
        system_id=system_id,
        samples=samples,
        layout_identity=layout_identity,
        overlay_plan=layout.authoritative_overlay_plan(),
        visualization_reference=reference,
        quality_options=tuple(radiance_options(request.quality)),
        target_control=target_control,
        full_output_schedule=schedule,
        operating_point=operating_point,
        counts=counts,
        transport_policy={"backend": f"test_{system_id}_basis"},
        runtime_provenance={"runtime": "test"},
        engine_provenance={
            "engine": "test",
            "mounting_height": mounting.to_payload(),
        },
        engine_artifacts={},
        target_feasible=target_feasible,
        target_infeasibility=None,
        physical_source_state=source_state,
        mounting_height=mounting.to_payload(),
    )
    root.mkdir()
    publish_native_baseline_run(
        root,
        run_id=run_id,
        request=request,
        science=science,
        event_sink=lambda *_args: None,
    )
    (root / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "run.log").write_text("published\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(("system_id", "run_id", "mounting_height_in"), SYSTEM_CASES)
def test_all_systems_export_and_play_back_without_native_runtime(
    tmp_path: Path,
    system_id: str,
    run_id: str,
    mounting_height_in: float,
) -> None:
    runtime = _publish_completed_runtime(tmp_path / "completed", system_id, run_id)
    expected_public = normalize_live_public_payload(runtime)
    expected_csv = (runtime / "ppfd.csv").read_bytes()
    expected_scatter = (runtime / "ppfd-scatter.f32le.bin").read_bytes()
    expected_controls = {
        name: (runtime / name).read_bytes()
        for name in (
            "target_control.json",
            "full_output_schedule.json",
            "operating-point.json",
        )
    }
    expected_bundle_payloads = {
        "baseline_leaf_uniformity": (
            runtime / "baseline-leaf-position-uniformity.v1.json"
        ).read_bytes(),
        "natural_fit_layout": (runtime / "natural_fit_layout.json").read_bytes(),
        "physical_source_state": (
            runtime / "physical-source-state.json"
        ).read_bytes(),
        "visualization_metadata": (runtime / "visualization.json").read_bytes(),
        "heatmap": (runtime / "ppfd-heatmap.png").read_bytes(),
        "heatmap_overlay": (runtime / "ppfd-heatmap-overlay.png").read_bytes(),
        "viewer_instances": (
            runtime / "plant-layout-viewer/instances.f32le.bin"
        ).read_bytes(),
        "fixture_catalog": (
            runtime / "plant-layout-viewer/fixtures/catalog.v1.json"
        ).read_bytes(),
        "plant_profile": (
            runtime
            / "plant-layout-viewer/profiles/rex_juvenile_preheading_12leaf_v1/"
            "profile.v1.json"
        ).read_bytes(),
        "plant_geometry": (
            runtime
            / "plant-layout-viewer/profiles/rex_juvenile_preheading_12leaf_v1/"
            "geometry.glb"
        ).read_bytes(),
        "plant_identity": (
            runtime
            / "plant-layout-viewer/profiles/rex_juvenile_preheading_12leaf_v1/"
            "identity-map.v1.json"
        ).read_bytes(),
        "plant_receivers": (
            runtime
            / "plant-layout-viewer/profiles/rex_juvenile_preheading_12leaf_v1/"
            "receivers.f32le.bin"
        ).read_bytes(),
    }
    fixture_catalog = json.loads(expected_bundle_payloads["fixture_catalog"])
    for index, group in enumerate(fixture_catalog["asset_groups"]):
        expected_bundle_payloads[f"fixture_transforms_{index:02d}"] = (
            runtime
            / "plant-layout-viewer/fixtures"
            / group["instance_matrices"]["filename"]
        ).read_bytes()
    bundle = tmp_path / f"{system_id}.fspm-compact"
    exported = export_compact_bundle(runtime, bundle)
    size = measure_compact_bundle(runtime, bundle)

    assert exported.byte_size == bundle.stat().st_size
    assert size.compact_bundle_bytes < size.source_runtime_bytes
    assert size.bytes_removed == size.source_runtime_bytes - exported.byte_size
    assert size.percent_removed > 90.0
    shutil.rmtree(runtime)

    playback = load_compact_bundle(bundle)
    assert playback.public_payload == expected_public
    assert all(
        playback.payloads[name] == expected
        for name, expected in expected_bundle_payloads.items()
    )
    assert playback.system_id == system_id
    assert playback.manifest["run_configuration"]["mounting_height"][
        "mounting_height_in"
    ] == mounting_height_in
    assert playback.final_baseline_ppfd_csv() == (
        "ppfd.csv",
        "text/csv; charset=utf-8",
        expected_csv,
    )
    scatter_metadata, scatter = playback.validated_ppfd_scatter()
    assert scatter == expected_scatter
    assert scatter_metadata["scatter"]["count"] == len(playback.samples)
    assert playback.public_payload["public_leaf_coloring_modes"] == [
        "target_coverage"
    ]
    assert playback.public_payload[
        "per_leaf_absorbed_par_coloring_retained"
    ] is False
    assert "surface_flux" not in playback.viewer_scene
    assert "target_coverage" in playback.viewer_scene["ppfd_heatmap"]
    result = playback.result_payload()
    assert result["metrics_url"] == f"/api/runs/{run_id}/metrics"
    assert result["ppfd_csv_url"] == f"/api/runs/{run_id}/artifacts/ppfd.csv"
    assert result["plant_layout_viewer_url"] == (
        f"/runs/{run_id}/viewer/index.html"
    )

    materialized = materialize_compact_playback(
        playback, tmp_path / f"{system_id}-playback"
    )
    assert (materialized / "ppfd.csv").read_bytes() == expected_csv
    assert (
        materialized / "ppfd-scatter.f32le.bin"
    ).read_bytes() == expected_scatter
    for name, expected in expected_controls.items():
        assert (materialized / name).read_bytes() == expected


def test_export_is_deterministic_authenticated_atomic_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _publish_completed_runtime(
        tmp_path / "completed", PROPOSED_SYSTEM_ID, "b" * 32
    )
    (runtime / "new-unlisted-debug-audit.tmp").write_bytes(
        b"must not enter consumer-driven bundle inventory"
    )
    first = tmp_path / "first.fspm-compact"
    second = tmp_path / "second.fspm-compact"
    observed_temporary_paths: list[Path] = []
    real_validator = compact_bundle.validate_compact_bundle

    def observe_fresh_validation(path: str | Path, **kwargs: object):
        candidate = Path(path)
        assert candidate.is_file()
        assert candidate.name.endswith(".partial")
        assert not first.exists()
        validation = real_validator(candidate, **kwargs)
        assert validation.valid
        observed_temporary_paths.append(candidate)
        return validation

    monkeypatch.setattr(
        compact_bundle, "validate_compact_bundle", observe_fresh_validation
    )
    first_result = export_compact_bundle(runtime, first)
    monkeypatch.setattr(compact_bundle, "validate_compact_bundle", real_validator)
    second_result = export_compact_bundle(runtime, second)

    assert observed_temporary_paths
    assert all(not path.exists() for path in observed_temporary_paths)
    assert first.read_bytes() == second.read_bytes()
    assert (
        first_result.bundle_identity_sha256
        == second_result.bundle_identity_sha256
    )
    with zipfile.ZipFile(first, "r") as archive:
        archive_names = set(archive.namelist())
        assert "ppfd.csv" not in archive_names
        assert compact_bundle.STAGE_A_PATH in archive_names
        assert all("new-unlisted-debug-audit" not in name for name in archive_names)
        assert all("surface-flux" not in name for name in archive_names)
        assert all(not name.endswith((".oct", ".amb", ".log", ".jsonl")) for name in archive_names)
        assert all(
            not name.startswith("payload/viewer/fixtures/assets/")
            for name in archive_names
        )
    validation = validate_compact_bundle(first)
    assert validation.status is CompactBundleStatus.VALID
    assert validation.bundle_identity_sha256 == first_result.bundle_identity_sha256
    assert validate_compact_bundle(
        first,
        expected_run_configuration={"system_id": CONVENTIONAL_SYSTEM_ID},
    ).status is CompactBundleStatus.CONFIGURATION_MISMATCH
    assert validate_compact_bundle(
        first,
        expected_authenticated_identities={
            "scientific": {"scientific_run_sha256": "0" * 64}
        },
    ).status is CompactBundleStatus.IDENTITY_MISMATCH

    failed = tmp_path / "failed.fspm-compact"
    monkeypatch.setattr(
        compact_bundle,
        "validate_compact_bundle",
        lambda *_args, **_kwargs: compact_bundle.CompactBundleValidation(
            CompactBundleStatus.CORRUPT, "injected validation failure"
        ),
    )
    with pytest.raises(CompactBundleError):
        export_compact_bundle(runtime, failed)
    assert not failed.exists()
    assert not list(tmp_path.glob(f".{failed.name}.*.partial"))


def test_fixed_plan_context_authenticates_250_without_adding_hps_dimming(
    tmp_path: Path,
) -> None:
    runtime = _publish_completed_runtime(
        tmp_path / "completed", HPS_SYSTEM_ID, "9" * 32
    )
    plan = build_fixed_sweep_plan()
    case = next(
        item
        for item in plan.cases
        if item.system_id == HPS_SYSTEM_ID
        and item.aisle_enabled is False
        and item.canonical_domain["canonical_aligned_room"]["length_x_ft"] == 10.0
    )
    bundle = tmp_path / "fixed-hps-context.fspm-compact"
    export_compact_bundle(
        runtime,
        bundle,
        fixed_case_binding=case.binding(plan.plan_identity_sha256),
        fixed_plan_inputs=case.compatibility_inputs,
    )

    playback = load_compact_bundle(bundle)
    binding = playback.manifest["run_configuration"]["fixed_plan"]
    request = playback.manifest["run_configuration"]["request"]
    assert binding["target_ppfd_umol_m2_s"] == 250.0
    assert binding["target_semantics"] == (
        "fixed_output_comparison_reference_no_dimming"
    )
    assert "target_ppfd_umol_m2_s" not in request
    assert "lighting_target_mode" not in request
    assert validate_compact_bundle(
        bundle,
        expected_run_configuration=case.expected_run_configuration(
            plan.plan_identity_sha256
        ),
        expected_authenticated_identities=case.expected_authenticated_identities(),
    ).status is CompactBundleStatus.CONFIGURATION_MISMATCH


def _rewrite_archive(
    source: Path,
    destination: Path,
    transform: Callable[[dict[str, bytes]], dict[str, bytes]],
) -> None:
    with zipfile.ZipFile(source, "r") as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    transformed = transform(entries)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(transformed):
            archive.writestr(name, transformed[name])


def test_validator_distinguishes_missing_partial_corrupt_tampered_and_legacy(
    tmp_path: Path,
) -> None:
    runtime = _publish_completed_runtime(
        tmp_path / "completed", PROPOSED_SYSTEM_ID, "d" * 32
    )
    bundle = tmp_path / "valid.fspm-compact"
    export_compact_bundle(runtime, bundle)

    assert validate_compact_bundle(tmp_path / "missing").status is (
        CompactBundleStatus.MISSING
    )
    truncated = tmp_path / "truncated.fspm-compact"
    data = bundle.read_bytes()
    truncated.write_bytes(data[: len(data) // 2])
    assert validate_compact_bundle(truncated).status is CompactBundleStatus.PARTIAL

    random_bytes = tmp_path / "corrupt.fspm-compact"
    random_bytes.write_bytes(b"not a compact bundle")
    assert validate_compact_bundle(random_bytes).status is CompactBundleStatus.CORRUPT

    partial = tmp_path / "partial.fspm-compact"
    with zipfile.ZipFile(partial, "w") as archive:
        archive.writestr("payload/orphan.bin", b"orphan")
    assert validate_compact_bundle(partial).status is CompactBundleStatus.PARTIAL

    tampered = tmp_path / "tampered.fspm-compact"

    def mutate_payload(entries: dict[str, bytes]) -> dict[str, bytes]:
        stage_a = bytearray(entries[compact_bundle.STAGE_A_PATH])
        stage_a[0] ^= 1
        entries[compact_bundle.STAGE_A_PATH] = bytes(stage_a)
        return entries

    _rewrite_archive(bundle, tampered, mutate_payload)
    assert validate_compact_bundle(tampered).status is CompactBundleStatus.CORRUPT
    with pytest.raises(CompactBundleError):
        load_compact_bundle(tampered)

    incompatible = tmp_path / "incompatible.fspm-compact"

    def change_schema(entries: dict[str, bytes]) -> dict[str, bytes]:
        manifest = json.loads(entries["manifest.json"])
        manifest["schema_version"] = 999
        entries["manifest.json"] = json.dumps(manifest).encode("utf-8")
        return entries

    _rewrite_archive(bundle, incompatible, change_schema)
    assert validate_compact_bundle(incompatible).status is (
        CompactBundleStatus.INCOMPATIBLE_SCHEMA
    )

    historical = tmp_path / "historical-directory-bundle"
    historical.mkdir()
    (historical / "manifest.json").write_text(
        json.dumps({"schema_id": "fspm-optics.precomputed-bundle", "version": 1}),
        encoding="utf-8",
    )
    assert validate_compact_bundle(historical).status is (
        CompactBundleStatus.INCOMPATIBLE_SCHEMA
    )


@pytest.mark.parametrize("include_far_red", [False, True])
def test_metrics_allowlist_preserves_optional_far_red_and_absorption_aggregates(
    include_far_red: bool,
) -> None:
    source = {
        field: None for field in compact_bundle.METRICS_FIELDS
    }
    source.update(
        {
            "schema_id": "fspm-optics.native-baseline-metrics",
            "schema_version": 4,
            "run_id": "e" * 32,
            "system_id": PROPOSED_SYSTEM_ID,
            "system": "Proposed LED System",
            "power": {"full_output_w": 100.0, "effective_w": 90.0},
            "ppf": {
                "emitted_umol_s": 234.0,
                "emission_boundary_id": "completed_aperture",
                "emission_boundary_description": "test boundary",
            },
            "fspm_surface_light_metrics": {
                "modeled_physical_one_sided_leaf_area_m2": 2.0,
                "counts": {"plants": 4},
                "surface_light": {
                    "par": {"combined_exposure_per_physical_one_sided_leaf_area": {}},
                    **(
                        {
                            "far_red": {
                                "combined_exposure_per_physical_one_sided_leaf_area": {}
                            }
                        }
                        if include_far_red
                        else {}
                    ),
                },
                "absorbed_par_metrics": {
                    "total_combined_absorbed_par_rate_umol_s": 10.0,
                    "absorbed_capture_efficiency_percent": 20.0,
                    "absorbed_par_per_electrical_watt_umol_per_j": 0.1,
                    "plant_to_plant_absorbed_exposure_cv_percent": 3.0,
                    "plant_minimum_to_mean_absorbed_exposure_ratio": 0.9,
                    "leaf_to_leaf_absorbed_exposure_cv_percent": 5.0,
                    "per_leaf_absorbed_par_coloring": [1.0, 2.0],
                },
                "band_order": (
                    ["blue", "green", "orange", "red", "far_red"]
                    if include_far_red
                    else ["blue", "green", "orange", "red"]
                ),
                "far_red_executed": include_far_red,
            },
        }
    )

    metrics = compact_bundle._metrics_payload(source)
    surface = metrics["fspm_surface_light_metrics"]
    assert surface["far_red_executed"] is include_far_red
    assert ("far_red" in surface["surface_light"]) is include_far_red
    assert surface["absorbed_par_metrics"] == {
        "total_combined_absorbed_par_rate_umol_s": 10.0,
        "absorbed_capture_efficiency_percent": 20.0,
        "absorbed_par_per_electrical_watt_umol_per_j": 0.1,
        "plant_to_plant_absorbed_exposure_cv_percent": 3.0,
        "plant_minimum_to_mean_absorbed_exposure_ratio": 0.9,
        "leaf_to_leaf_absorbed_exposure_cv_percent": 5.0,
    }
    capabilities = compact_bundle._capabilities(metrics)
    assert "aggregate_absorbed_par_metrics_v1" in capabilities
    assert (
        "optional_far_red_aggregate_analysis_v1" in capabilities
    ) is include_far_red


@pytest.mark.parametrize("location", ["transport_policy", "engine_provenance"])
def test_solver_configuration_retains_occlusion_identity_not_geometry(
    location: str,
) -> None:
    occlusion = {
        "occlusion_version": "glb-fixture-occlusion-v4",
        "system_id": PROPOSED_SYSTEM_ID,
        "identity_sha256": "1" * 64,
        "classification_manifest_sha256": "2" * 64,
        "transform_policy_id": "viewer-catalog-matrix-to-scientific-z-up-v1",
        "transform_set_sha256": "3" * 64,
        "counts": {"fixture_instances": 61},
        "assets": [
            {
                "asset_id": "proposed-led-module-v1",
                "fixture_type": "standalone_module",
                "glb_sha256": "4" * 64,
                "glb_byte_size": 88_540,
                "node_primitive_inventory_sha256": "5" * 64,
                "classification_sha256": "6" * 64,
            }
        ],
        "shapes": [{"large_geometry_record": [1.0] * 1_000}],
        "instances": [{"translation_m": [0.0, 0.0, 0.0]}],
        "emitting_boundaries": [{"vertices_m": [[0.0, 0.0, 0.0]]}],
    }
    manifest = {
        "transport_policy": {
            "backend": "test_basis",
            **({"fixture_occlusion": occlusion} if location == "transport_policy" else {}),
        },
        "engine_provenance": (
            {"fixture_occlusion": occlusion}
            if location == "engine_provenance"
            else {}
        ),
    }

    solver = compact_bundle._solver_configuration(manifest)
    retained = solver["fixture_occlusion"]
    assert retained["identity_sha256"] == "1" * 64
    assert retained["assets"][0]["glb_sha256"] == "4" * 64
    assert "shapes" not in retained
    assert "instances" not in retained
    assert "emitting_boundaries" not in retained
    assert solver["policy_identity_sha256"] == compact_bundle._hash_json(
        manifest["transport_policy"]
    )


@pytest.fixture(
    params=(
        (PROPOSED_SYSTEM_ID, "practical", "1" * 32),
        (CONVENTIONAL_SYSTEM_ID, "practical", "2" * 32),
        (CONVENTIONAL_SYSTEM_ID, "rolling_bench", "3" * 32),
    )
)
def target_adjustment_base(request, tmp_path: Path):
    system_id, layout_mode, run_id = request.param
    runtime = _publish_completed_runtime(
        tmp_path / "completed-250",
        system_id,
        run_id,
        requested_target=250.0,
        target_tolerance=75.0,
        conventional_layout_mode=layout_mode,
    )
    bundle = tmp_path / f"{system_id}-{layout_mode}.fspm-compact"
    export_compact_bundle(runtime, bundle)
    base = load_compact_bundle(bundle)

    # Compact Stage B-shaped aggregates exercise linear, squared, invariant,
    # absorbed-light, and optional far-red classifications without transport.
    public = deepcopy(dict(base.public_payload))
    metrics = deepcopy(dict(base.metrics))
    metrics["synthetic_intensity_statistics"] = {
        "mean_ppfd_umol_m2_s": 20.0,
        "population_variance_ppfd_umol_m2_s_squared": 16.0,
        "cv_percent": 20.0,
    }
    metrics["fspm_surface_light_metrics"] = {
        "modeled_physical_one_sided_leaf_area_m2": 2.0,
        "counts": {"plants": 4},
        "surface_light": {
            "par": {
                "combined_exposure_per_physical_one_sided_leaf_area": {
                    "incident_photon_flux_density_umol_m2_s": 100.0,
                    "absorbed_photon_flux_density_umol_m2_s": 60.0,
                },
                "photon_rates_umol_s": {
                    "incident_photon_rate_umol_s": 200.0,
                    "absorbed_photon_rate_umol_s": 120.0,
                },
                "absorbed_fraction_of_incident": 0.6,
            },
            "far_red": {
                "combined_exposure_per_physical_one_sided_leaf_area": {
                    "incident_photon_flux_density_umol_m2_s": 11.0,
                },
                "absorbed_fraction_of_incident": 0.5,
            },
        },
        "absorbed_par_metrics": {
            "total_combined_absorbed_par_rate_umol_s": 120.0,
            "absorbed_capture_efficiency_percent": 40.0,
            "absorbed_par_per_electrical_watt_umol_per_j": 1.2,
            "plant_to_plant_absorbed_exposure_cv_percent": 3.0,
            "plant_minimum_to_mean_absorbed_exposure_ratio": 0.9,
            "leaf_to_leaf_absorbed_exposure_cv_percent": 5.0,
        },
        "band_order": ["blue", "green", "orange", "red", "far_red"],
        "far_red_executed": True,
    }
    public["metrics"] = metrics
    payloads = dict(base.payloads)
    baseline_leaf = json.loads(payloads["baseline_leaf_uniformity"])
    baseline_leaf.update(
        {
            "available": True,
            "unavailable_reason_code": None,
            "unavailable_details": None,
            "physical_leaf_count": 4,
            "records": [
                {
                    "leaf_id": f"synthetic-leaf-{index}",
                    "interpolated_ppfd_umol_m2_s": value,
                    "classification": "target_range",
                }
                for index, value in enumerate((200.0, 225.0, 275.0, 300.0))
            ],
        }
    )
    payloads["baseline_leaf_uniformity"] = (
        json.dumps(baseline_leaf, sort_keys=True, indent=2) + "\n"
    ).encode()
    return compact_bundle.CompactPlayback(
        bundle_path=base.bundle_path,
        manifest=base.manifest,
        public_payload=public,
        payloads=payloads,
        samples=base.samples,
    )


def test_led_target_adjustment_matrix_capacity_identity_and_outputs(
    target_adjustment_base,
) -> None:
    base = target_adjustment_base
    base_identity = base.manifest["bundle_identity_sha256"]
    base_path = base.bundle_path
    views = {
        target: derive_target_adjusted_playback(base, target)
        for target in (0.0, 125.0, 250.0, 500.0, 1000.0, 1500.0)
    }

    assert views[250.0].samples is base.samples
    assert views[250.0].payloads is base.payloads
    assert views[250.0].metrics == base.metrics
    assert all(view.manifest is base.manifest for view in views.values())
    assert all(view.bundle_path == base_path for view in views.values())
    assert all(
        view.manifest["bundle_identity_sha256"] == base_identity
        for view in views.values()
    )
    assert len(
        {view.derived_playback_identity_sha256 for view in views.values()}
    ) == len(views)
    assert (
        derive_target_adjusted_playback(base, 500.0)
        .derived_playback_identity_sha256
        == views[500.0].derived_playback_identity_sha256
    )

    for target, expected_scale, expected_output, expected_mean in (
        (0.0, 0.0, 0.0, 0.0),
        (125.0, 0.5, 0.125, 125.0),
        (250.0, 1.0, 0.25, 250.0),
        (500.0, 2.0, 0.5, 500.0),
        (1000.0, 4.0, 1.0, 1000.0),
        (1500.0, 4.0, 1.0, 1000.0),
    ):
        adjustment = views[target].adjustment
        assert adjustment.applied_scale == expected_scale
        assert adjustment.output_fraction == expected_output
        assert adjustment.actual_achieved_mean_ppfd_umol_m2_s == expected_mean
        assert adjustment.maximum_achievable_ppfd_umol_m2_s == 1000.0
        assert (
            TARGET_ADJUSTMENT_CAPABILITY
            in views[target].public_payload["capabilities"]
        )
    assert views[1000.0].adjustment.output_limited is False
    assert views[1500.0].adjustment.output_limited is True
    assert views[1500.0].adjustment.limit_reason == "maximum_output"
    assert views[1500.0].metrics["requested_target_ppfd_umol_m2_s"] == 1500.0
    assert views[1500.0].metrics["achieved_mean_ppfd_umol_m2_s"] == 1000.0
    assert views[1500.0].metrics["target_infeasibility"]["reason"] == (
        "maximum_output"
    )
    saturated_summary = views[1500.0].metrics[
        "baseline_leaf_position_uniformity"
    ]["summary"]
    assert saturated_summary["under_lit_leaves"]["count"] == (
        saturated_summary["denominator_leaf_count"]
    )

    half = views[125.0]
    assert [sample.ppfd_umol_m2_s for sample in half.samples] == [
        100.0,
        112.5,
        137.5,
        150.0,
    ]
    surface = half.metrics["fspm_surface_light_metrics"]
    assert surface["surface_light"]["par"][
        "combined_exposure_per_physical_one_sided_leaf_area"
    ]["absorbed_photon_flux_density_umol_m2_s"] == 30.0
    assert surface["surface_light"]["far_red"][
        "combined_exposure_per_physical_one_sided_leaf_area"
    ]["incident_photon_flux_density_umol_m2_s"] == 5.5
    assert surface["surface_light"]["par"]["absorbed_fraction_of_incident"] == 0.6
    assert surface["absorbed_par_metrics"][
        "total_combined_absorbed_par_rate_umol_s"
    ] == 60.0
    assert surface["absorbed_par_metrics"][
        "absorbed_capture_efficiency_percent"
    ] == 40.0
    assert surface["absorbed_par_metrics"][
        "absorbed_par_per_electrical_watt_umol_per_j"
    ] == 1.2
    assert half.metrics["power"]["effective_w"] == pytest.approx(
        base.metrics["power"]["effective_w"] * 0.5
    )
    assert half.metrics["ppf"]["emitted_umol_s"] == pytest.approx(
        base.metrics["ppf"]["emitted_umol_s"] * 0.5
    )
    assert half.metrics["synthetic_intensity_statistics"] == {
        "mean_ppfd_umol_m2_s": 10.0,
        "population_variance_ppfd_umol_m2_s_squared": 4.0,
        "cv_percent": 20.0,
    }
    assert half.metrics["fspm_target_policy"]["resolved_target_umol_m2_s"] == 125.0
    base_operating = json.loads(base.payloads["operating_point"])
    half_operating = json.loads(half.payloads["operating_point"])
    assert half_operating["power"]["full_output_w"] == (
        base_operating["power"]["full_output_w"]
    )
    assert half_operating["power"]["effective_w"] == pytest.approx(
        base_operating["power"]["effective_w"] * 0.5
    )
    assert half_operating["ppf"]["emitted_umol_s"] == pytest.approx(
        base_operating["ppf"]["emitted_umol_s"] * 0.5
    )

    filename, mime, csv_data = half.final_baseline_ppfd_csv()
    assert (filename, mime) == ("ppfd.csv", "text/csv; charset=utf-8")
    assert csv_data.decode().splitlines()[-1].endswith(",150")
    scatter_metadata, scatter = half.validated_ppfd_scatter()
    assert struct.unpack("<12f", scatter)[2::3] == pytest.approx(
        (100.0, 112.5, 137.5, 150.0)
    )
    assert scatter_metadata["transforms"]["target_rescaling"] is True
    assert half.payloads["heatmap"].startswith(b"\x89PNG\r\n\x1a\n")
    assert half.payloads["heatmap"] != base.payloads["heatmap"]
    assert json.loads(half.payloads["public_result"])["target_adjustment"] == (
        half.adjustment.to_dict()
    )
    coverage = half.viewer_scene["ppfd_heatmap"]["target_coverage"]
    assert coverage["reference"]["ppfd_umol_m2_s"] == 125.0
    assert coverage["tolerance_ppfd_umol_m2_s"] == 75.0

    zero = views[0.0]
    assert all(sample.ppfd_umol_m2_s == 0.0 for sample in zero.samples)
    assert zero.metrics["power"]["effective_w"] == 0.0
    assert zero.metrics["ppf"]["emitted_umol_s"] == 0.0
    assert zero.metrics["spatial_uniformity"]["values"][
        "coefficient_of_variation"
    ] == 0.0
    zero_summary = zero.metrics["baseline_leaf_position_uniformity"]["summary"]
    assert zero_summary["target_range_leaves"]["count"] == (
        zero_summary["denominator_leaf_count"]
    )
    assert zero_summary["leaf_position_ppfd_coefficient_of_variation"] == {
        "available": False,
        "value_percent": None,
        "reason_code": "leaf_position_ppfd_mean_zero",
    }
    assert zero.viewer_scene["ppfd_heatmap"]["target_coverage"]["reference"][
        "ppfd_umol_m2_s"
    ] == 0.0
    json.dumps(zero.public_payload, allow_nan=False)
    json.dumps(zero.viewer_scene, allow_nan=False)
    assert all(
        math.isfinite(value)
        for record in struct.iter_unpack("<3f", zero.validated_ppfd_scatter()[1])
        for value in record
    )


def test_target_capped_derivative_scales_classified_public_results(
    target_adjustment_base,
) -> None:
    base = target_adjustment_base
    run_configuration = base.public_payload["run_configuration"]
    canonical_domain = run_configuration["canonical_domain"]
    context = {
        "fixed_case_binding": {
            "schema_id": "synthetic-fixed-case-binding",
            "schema_version": 1,
            "case_id": f"synthetic-{base.system_id}",
        },
        "canonical_domain": canonical_domain,
        "presentation_identity_sha256": "a" * 64,
    }
    capped = derive_target_adjusted_playback(
        base,
        150.0,
        lighting_target_mode="target_capped",
        **context,
    )
    repeated = derive_target_adjusted_playback(
        base,
        150.0,
        lighting_target_mode="target_capped",
        **context,
    )
    mean = derive_target_adjusted_playback(base, 150.0)

    assert capped.manifest is base.manifest
    assert capped.adjustment.base_bundle_identity_sha256 == (
        mean.adjustment.base_bundle_identity_sha256
    )
    assert capped.derived_playback_identity_sha256 == (
        repeated.derived_playback_identity_sha256
    )
    assert capped.derived_playback_identity_sha256 != (
        mean.derived_playback_identity_sha256
    )
    assert TARGET_CAPPED_ADJUSTMENT_CAPABILITY in capped.public_payload[
        "capabilities"
    ]
    assert capped.adjustment.actual_achieved_maximum_ppfd_umol_m2_s <= (
        150.0 + 1e-6
    )
    assert capped.adjustment.cap_compliant is True
    expected_scale = capped.adjustment.intensity_scale
    assert [sample.ppfd_umol_m2_s for sample in capped.samples] == pytest.approx(
        [sample.ppfd_umol_m2_s * expected_scale for sample in base.samples]
    )
    statistics = capped.metrics["synthetic_intensity_statistics"]
    assert statistics["mean_ppfd_umol_m2_s"] == pytest.approx(
        20.0 * expected_scale
    )
    assert statistics[
        "population_variance_ppfd_umol_m2_s_squared"
    ] == pytest.approx(16.0 * expected_scale * expected_scale)
    assert statistics["cv_percent"] == 20.0
    surface = capped.metrics["fspm_surface_light_metrics"]
    assert surface["surface_light"]["par"]["photon_rates_umol_s"][
        "absorbed_photon_rate_umol_s"
    ] == pytest.approx(120.0 * expected_scale)
    assert surface["surface_light"]["par"][
        "absorbed_fraction_of_incident"
    ] == 0.6
    assert surface["absorbed_par_metrics"][
        "absorbed_par_per_electrical_watt_umol_per_j"
    ] == 1.2
    assert capped.metrics["power"]["effective_w"] == pytest.approx(
        base.metrics["power"]["effective_w"] * expected_scale
    )
    assert capped.metrics["ppf"]["emitted_umol_s"] == pytest.approx(
        base.metrics["ppf"]["emitted_umol_s"] * expected_scale
    )
    assert capped.metrics["fspm_surface_light_metrics"]["surface_light"][
        "far_red"
    ]["combined_exposure_per_physical_one_sided_leaf_area"][
        "incident_photon_flux_density_umol_m2_s"
    ] == pytest.approx(11.0 * expected_scale)
    visualization = capped.visualization
    assert visualization["display"]["reference"]["kind"] == (
        "requested_sampled_ppfd_cap"
    )
    coverage = capped.viewer_scene["ppfd_heatmap"]["target_coverage"]
    assert coverage["reference"] == {
        "ppfd_umol_m2_s": 150.0,
        "source": "requested_lighting_target",
        "policy_mode": "automatic",
    }


@pytest.mark.parametrize(
    "value",
    (-1, True, False, "250", None, float("nan"), float("inf"), -float("inf")),
)
def test_target_adjustment_rejects_only_invalid_target_classes(value: object) -> None:
    with pytest.raises(ValueError, match="finite non-negative"):
        validate_requested_target_ppfd(value)


def test_hps_exposes_no_adjustment_capability_and_rejects_override(
    tmp_path: Path,
) -> None:
    runtime = _publish_completed_runtime(
        tmp_path / "hps-completed",
        HPS_SYSTEM_ID,
        "4" * 32,
    )
    bundle = tmp_path / "hps.fspm-compact"
    export_compact_bundle(runtime, bundle)
    playback = load_compact_bundle(bundle)
    assert TARGET_ADJUSTMENT_CAPABILITY not in playback.public_payload["capabilities"]
    with pytest.raises(ValueError, match="fixed-output.*rejects"):
        derive_target_adjusted_playback(playback, 250.0)


def test_target_adjustment_precedes_canonical_presentation_and_reuses_bundle(
    target_adjustment_base,
) -> None:
    base = target_adjustment_base
    layout_variant = (
        "proposed_reduced_one_ring"
        if base.system_id == PROPOSED_SYSTEM_ID
        else json.loads(base.payloads["natural_fit_layout"])
        .get("layout_policy_id", "practical")
    )
    if base.system_id == CONVENTIONAL_SYSTEM_ID:
        # The compact fixture's run configuration is the clearest layout key.
        layout_variant = base.public_payload["run_configuration"]["request"].get(
            "layout_mode", "practical"
        )
    case = next(
        case
        for case in build_fixed_sweep_plan().cases
        if case.system_id == base.system_id
        and case.layout_variant == layout_variant
        and case.aisle_enabled is False
        and case.canonical_domain["canonical_aligned_room"]["length_x_ft"]
        == 30.0
    )
    adjusted = derive_target_adjusted_playback(base, 125.0)
    portrait = RequestedCompactPlayback.create(
        adjusted,
        case,
        requested_length_ft=15.0,
        requested_width_ft=30.0,
    )
    landscape = RequestedCompactPlayback.create(
        adjusted,
        case,
        requested_length_ft=30.0,
        requested_width_ft=15.0,
    )
    assert portrait.canonical_bundle_identity_sha256 == (
        landscape.canonical_bundle_identity_sha256
    )
    assert portrait.derived_playback_identity_sha256 == (
        landscape.derived_playback_identity_sha256
    )
    assert portrait.presentation_identity_sha256 != (
        landscape.presentation_identity_sha256
    )
    assert portrait.samples == landscape.samples
    assert portrait.final_baseline_ppfd_csv() == (
        landscape.final_baseline_ppfd_csv()
    )
    portrait_metadata, portrait_scatter = portrait.validated_ppfd_scatter()
    landscape_metadata, landscape_scatter = landscape.validated_ppfd_scatter()
    portrait_metadata.pop("requested_orientation")
    landscape_metadata.pop("requested_orientation")
    assert portrait_metadata == landscape_metadata
    assert portrait_scatter == landscape_scatter
    assert portrait.plant_instance_translations() == (
        landscape.plant_instance_translations()
    )
    assert portrait.fixture_transform_payloads() == (
        landscape.fixture_transform_payloads()
    )
    assert portrait.heatmap_payloads()["plain_png"] == (
        landscape.heatmap_payloads()["plain_png"]
    )


def test_fixed_loader_selects_the_same_250_bundle_for_alternate_and_zero_targets(
    target_adjustment_base,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base = target_adjustment_base
    request_payload: dict[str, object] = {
        "system": base.system_id,
        "room_length_ft": 15.0,
        "room_width_ft": 30.0,
        "mounting_height_in": 18.0,
        "quality": "standard",
        "analysis_scope": "baseline_plus_multispectral_fspm",
        "include_far_red": True,
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 75.0,
        "aisle_mode": False,
        "target_ppfd": 500.0,
    }
    if base.system_id == CONVENTIONAL_SYSTEM_ID:
        request_payload["layout_mode"] = base.public_payload["run_configuration"][
            "request"
        ].get("layout_mode", "practical")
    else:
        request_payload.update(
            {
                "spectral_basis": "conventional_led_control",
                "proposed_control_mode": "uniform_module_dimming",
                "proposed_ring_mode": "reduced_one_ring",
                "proposed_source_mode": "native_smd",
            }
        )
    request = parse_run_request(request_payload)
    loads: list[Path] = []
    plan = build_fixed_sweep_plan()

    def fake_load(path: str | Path, *_args: object, **_kwargs: object):
        loads.append(Path(path))
        return replace(base, bundle_path=Path(path).resolve())

    monkeypatch.setattr(fixed_playback, "load_committed_case_bundle", fake_load)
    alternate = fixed_playback.load_fixed_playback(tmp_path, request, plan=plan)
    zero = fixed_playback.load_fixed_playback(
        tmp_path,
        request,
        plan=plan,
        target_ppfd_umol_m2_s=0.0,
    )
    assert loads == [alternate.bundle_path, zero.bundle_path]
    assert alternate.bundle_path == zero.bundle_path
    assert "/ppfd-250/" in alternate.bundle_path.as_posix()
    assert alternate.playback.canonical.adjustment.requested_target_ppfd_umol_m2_s == 500.0
    assert zero.playback.canonical.adjustment.requested_target_ppfd_umol_m2_s == 0.0
    assert alternate.playback.canonical_bundle_identity_sha256 == (
        zero.playback.canonical_bundle_identity_sha256
    )
