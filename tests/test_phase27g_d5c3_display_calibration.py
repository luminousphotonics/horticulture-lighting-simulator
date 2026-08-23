from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

import pytest

from fspm_optics.application.surface_flux_display_calibration import (
    BLOCK_ORDER,
    COEFFICIENT_COUNT,
    DISPLAY_CALIBRATION_MANIFEST_NAME,
    DISPLAY_CALIBRATION_PAYLOAD_NAME,
    D5_C2_ANALYSIS_ID,
    D5_C2_COMPLETION_NAME,
    D5_C2_COMPLETION_SCHEMA_ID,
    D5_C2_REPORT_NAME,
    D5_C2_SCHEMA_ID,
    D5_C2_SCHEMA_VERSION,
    D5_PATCHES_PER_PLANT,
    D5_RECEIVERS_SHA256,
    D5_SAMPLING_PROFILE_ID,
    D5_TOPOLOGY_SHA256,
    FAMILY_ORDER,
    PAYLOAD_BYTE_LENGTH,
    PINNED_C2_COMPLETION_SHA256,
    PINNED_C2_REPORT_SHA256,
    C2Authority,
    SurfaceFluxDisplayCalibrationConfig,
    SurfaceFluxDisplayCalibrationError,
    generate_surface_flux_display_calibration,
    load_surface_flux_display_calibration,
    c2_quality_option_identity,
    quality_dispatch_contract,
)
from fspm_optics.application.surface_flux_display import (
    BACK_ANCHORS,
    BACK_PALETTE,
    FRONT_LOCAL_PATCH_ANCHORS,
    SCHEMA_V3_BACK_ANCHORS,
    SCHEMA_V3_BACK_PALETTE,
    SCHEMA_V3_BACK_PALETTE_ID,
    SurfaceFluxDisplayError,
    authenticated_palette_contract,
    build_local_patch_legend_contract,
    palette_contract,
    select_authenticated_achieved_surface_flux_reference,
)
from fspm_optics.surface_flux_display_calibration_cli import build_parser
from fspm_optics.viewer.models import BinaryDisplayArtifact, SurfaceFluxViewerArtifacts


def _authority_record(path: str) -> dict[str, object]:
    return {"path": path, "byte_length": 1, "sha256": "a" * 64}


def _source_authorities() -> dict[str, object]:
    return {
        "d5_a1": {
            "experiment_id": "phase27g-d5-a1-optimized-surface-flux-sweep-v3",
            "status": "complete",
            "completion": _authority_record(
                "surface-flux-recalibration-completion.v1.json"
            ),
        },
        "d5_a2": {
            "analysis_id": "phase27g-d5-a2-coefficient-analysis-v1",
            "status": "complete",
            "completion": _authority_record(
                "surface-flux-coefficient-analysis-completion.v1.json"
            ),
            "report": _authority_record("surface-flux-coefficient-analysis.v1.json"),
            "coefficient_manifest": _authority_record(
                "coefficient-payload-manifest.v1.json"
            ),
        },
        "d5_c1": {
            "experiment_id": "phase27g-d5-c1-endpoint-replication-v1",
            "status": "complete",
            "stage_a_trace_count": 0,
            "stage_b_trace_count": 16,
            "nonidentical_repeated_artifact_count": 16,
            "independently_seeded_sample_claimed": False,
            "configuration": _authority_record(
                "endpoint-replication-configuration.v1.json"
            ),
            "outcome": _authority_record(
                "surface-flux-endpoint-replication-outcome.v1.json"
            ),
        },
    }


def _gamma(family: str, side: str, metric: str, patch: int) -> float:
    family_index = FAMILY_ORDER.index(family)
    block_index = BLOCK_ORDER.index((side, metric))
    return 1.0 + family_index * 10.0 + block_index + patch / 1000.0


def _fixture(tmp_path: Path) -> tuple[Path, C2Authority]:
    root = tmp_path / "c2"
    root.mkdir(parents=True)
    authorities = _source_authorities()
    cells = [
        {
            "family": family,
            "side": side,
            "metric": metric,
            "local_patch_index": patch,
            "coefficient_estimates": {
                "pooled_four_observation_through_origin_gamma": _gamma(
                    family, side, metric, patch
                )
            },
            # Deliberately adverse descriptive evidence must never mask a cell.
            "a2_source_failed_cell": True,
            "a2_source_failure_reasons": [
                "through_origin_r_squared_below_minimum",
                "relative_ratio_drift_above_maximum",
            ],
        }
        for family in ("quality", "standard")
        for side, metric in BLOCK_ORDER
        for patch in range(D5_PATCHES_PER_PLANT)
    ]
    report = {
        "schema_id": D5_C2_SCHEMA_ID,
        "schema_version": D5_C2_SCHEMA_VERSION,
        "analysis_id": D5_C2_ANALYSIS_ID,
        "status": "complete",
        "source_authorities": authorities,
        "input_authentication": {
            "status": "all_sources_authenticated_before_receiver_value_decode",
            "d5_a1_complete_authority_and_original_artifacts_validated": True,
            "d5_a2_report_completion_inventory_and_coefficients_validated": True,
            "d5_c1_configuration_outcome_jobs_and_traces_validated": True,
            "declared_byte_lengths_and_sha256_validated": True,
            "achieved_references_and_source_amplitudes_validated": True,
            "original_to_repeat_pairing_validated": True,
            "quality_option_identities_validated": True,
            "sampling_topology_receiver_count_and_order_validated": True,
            "stage_a_trace_count": 0,
            "stage_b_repetition_count": 16,
        },
        "fixed_identity": {
            "families": ["quality", "standard"],
            "sides": ["front", "back"],
            "metrics": ["incident", "absorbed"],
            "band_order": ["blue", "green", "orange", "red"],
            "plant_indices": {"first": 0, "last": 63, "count": 64},
            "local_patch_indices": {"first": 0, "last": 191, "count": 192},
            "canonical_receiver_order": (
                "plant-major; local_patch_index 0..191; front then back"
            ),
            "receiver_count_per_band": 24_576,
            "receiver_byte_length_per_band": 196_608,
            "replicates": ["original_a1", "repeated_c1"],
            "requested_levels_umol_m2_s": [250.0, 500.0],
            "sampling_profile_id": D5_SAMPLING_PROFILE_ID,
            "topology_sha256": D5_TOPOLOGY_SHA256,
            "receivers_sha256": D5_RECEIVERS_SHA256,
            "quality_option_identities": [
                c2_quality_option_identity("quality"),
                c2_quality_option_identity("standard"),
            ],
        },
        "explicit_non_claims": {
            "availability_masks": False,
            "calibration_promotion_decision": False,
            "clipping_policy": False,
            "independent_random_seeding": False,
            "new_acceptance_thresholds": False,
            "palette_anchors": False,
            "production_calibration_suitability_decision": False,
            "production_resource_generated": False,
            "radiance_invoked": False,
        },
        "scope": {
            "analyzed_families": ["quality", "standard"],
            "direct_raw_transport_changed": False,
            "rigorous_raw_transport_changed": False,
            "raw_stage_c_science_changed": False,
        },
        "aggregate_summaries": {"cell_count": COEFFICIENT_COUNT},
        "detailed_cells": cells,
    }
    report_bytes = (
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()
    report_hash = hashlib.sha256(report_bytes).hexdigest()
    report_record = {
        "path": D5_C2_REPORT_NAME,
        "byte_length": len(report_bytes),
        "sha256": report_hash,
    }
    completion = {
        "schema_id": D5_C2_COMPLETION_SCHEMA_ID,
        "schema_version": D5_C2_SCHEMA_VERSION,
        "analysis_id": D5_C2_ANALYSIS_ID,
        "status": "complete",
        "report": report_record,
        "source_authorities": authorities,
        "ordered_artifact_inventory": [
            {**report_record, "media_type": "application/json"}
        ],
        "atomic_publication": (
            "both JSON artifacts fsynced before same-filesystem directory rename"
        ),
        "input_directories_modified": False,
        "radiance_invoked": False,
        "production_calibration_resource_generated": False,
        "calibration_decision_made": False,
        "independent_random_seeding_claimed": False,
    }
    completion_bytes = (
        json.dumps(completion, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()
    (root / D5_C2_REPORT_NAME).write_bytes(report_bytes)
    (root / D5_C2_COMPLETION_NAME).write_bytes(completion_bytes)
    return root, C2Authority(
        report_sha256=report_hash,
        completion_sha256=hashlib.sha256(completion_bytes).hexdigest(),
    )


def _generate(tmp_path: Path, name: str = "resource"):
    source, authority = _fixture(tmp_path)
    output = tmp_path / name
    loaded = generate_surface_flux_display_calibration(
        SurfaceFluxDisplayCalibrationConfig(source, output),
        authority=authority,
    )
    return source, authority, output, loaded


def test_pinned_production_c2_identities_are_exact() -> None:
    assert PINNED_C2_REPORT_SHA256 == (
        "df53a6425f6a6ee17667ccce04533f09c7f16c291a8f0894e5bc6d8e5c75e3ce"
    )
    assert PINNED_C2_COMPLETION_SHA256 == (
        "221c963fb34b86380624ee1daa88a29bdcd94f31730cb096fc2b2f24ca75e06a"
    )


def test_generator_module_has_no_simulation_or_native_execution_dependency() -> None:
    source = (
        Path(__file__).parents[1]
        / "src/fspm_optics/application/surface_flux_display_calibration.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "surface_flux_recalibration import",
        "surface_flux_repeatability_analysis import",
        "radiance.runner",
        "LocalRunner",
        "subprocess",
    ):
        assert forbidden not in source


def test_generator_packages_every_pooled_cell_in_explicit_float64_order(
    tmp_path: Path,
) -> None:
    _source, _authority, output, loaded = _generate(tmp_path)
    assert {path.name for path in output.iterdir()} == {
        DISPLAY_CALIBRATION_MANIFEST_NAME,
        DISPLAY_CALIBRATION_PAYLOAD_NAME,
    }
    assert len(loaded.payload) == PAYLOAD_BYTE_LENGTH == 12_288
    values = struct.unpack(f"<{COEFFICIENT_COUNT}d", loaded.payload)
    expected = tuple(
        _gamma(family, side, metric, patch)
        for family in FAMILY_ORDER
        for side, metric in BLOCK_ORDER
        for patch in range(D5_PATCHES_PER_PLANT)
    )
    assert values == expected
    assert loaded.manifest["coefficient_payload"]["component_type"] == (
        "IEEE-754 binary64"
    )
    assert loaded.manifest["coefficient_payload"]["byte_order"] == "little-endian"
    assert loaded.manifest["availability"] == {
        "policy": "all_authenticated_finite_positive_cells_available",
        "available_cell_count": 1536,
        "masked_cell_count": 0,
        "threshold_masking_applied": False,
    }


def test_generation_is_deterministic_and_atomic(tmp_path: Path) -> None:
    source, authority = _fixture(tmp_path)
    outputs = [tmp_path / "first", tmp_path / "second"]
    for output in outputs:
        generate_surface_flux_display_calibration(
            SurfaceFluxDisplayCalibrationConfig(source, output),
            authority=authority,
        )
    for filename in (
        DISPLAY_CALIBRATION_MANIFEST_NAME,
        DISPLAY_CALIBRATION_PAYLOAD_NAME,
    ):
        assert (outputs[0] / filename).read_bytes() == (outputs[1] / filename).read_bytes()
    with pytest.raises(SurfaceFluxDisplayCalibrationError, match="initially be absent"):
        generate_surface_flux_display_calibration(
            SurfaceFluxDisplayCalibrationConfig(source, outputs[0]),
            authority=authority,
        )


def test_c2_and_resource_hash_corruption_fail_closed(tmp_path: Path) -> None:
    source, authority, output, _loaded = _generate(tmp_path)
    report = source / D5_C2_REPORT_NAME
    report.write_bytes(report.read_bytes() + b" ")
    with pytest.raises(SurfaceFluxDisplayCalibrationError, match="SHA-256"):
        generate_surface_flux_display_calibration(
            SurfaceFluxDisplayCalibrationConfig(source, tmp_path / "rejected"),
            authority=authority,
        )
    assert not (tmp_path / "rejected").exists()
    completion_source, completion_authority = _fixture(tmp_path / "completion-case")
    completion = completion_source / D5_C2_COMPLETION_NAME
    completion.write_bytes(completion.read_bytes() + b" ")
    with pytest.raises(SurfaceFluxDisplayCalibrationError, match="SHA-256"):
        generate_surface_flux_display_calibration(
            SurfaceFluxDisplayCalibrationConfig(
                completion_source, tmp_path / "completion-rejected"
            ),
            authority=completion_authority,
        )
    assert not (tmp_path / "completion-rejected").exists()
    payload = output / DISPLAY_CALIBRATION_PAYLOAD_NAME
    changed = bytearray(payload.read_bytes())
    changed[0] ^= 1
    payload.write_bytes(changed)
    with pytest.raises(SurfaceFluxDisplayCalibrationError, match="payload contract"):
        load_surface_flux_display_calibration(output, authority=authority)


def test_dispatch_is_display_only_and_never_proxies_transport(tmp_path: Path) -> None:
    _source, _authority, _output, loaded = _generate(tmp_path)
    expected = {
        "direct": ("standard", True),
        "standard": ("standard", False),
        "quality": ("quality", False),
        "rigorous": ("quality", True),
    }
    assert loaded.manifest["quality_dispatch"] == quality_dispatch_contract()
    for quality, (family, proxy) in expected.items():
        selection = loaded.select_quality(quality)
        assert selection.coefficient_family == family
        assert selection.display_only_proxy is proxy
        assert selection.raw_transport_proxied is False


def test_metadata_v3_uses_authenticated_achieved_stage_a_mean_for_every_policy() -> None:
    for operating_policy in ("target-controlled", "fixed-output"):
        reference = select_authenticated_achieved_surface_flux_reference(
            operating_policy=operating_policy,
            achieved_stage_a_mean_ppfd_umol_m2_s=487.25,
        ).to_dict()
        assert reference["value"] == 487.25
        assert reference["value_kind"] == "achieved"
        assert reference["source_provenance"] == {
            "stage_id": "baseline_ppfd",
            "artifact": "ppfd.csv",
            "field": "achieved_mean_ppfd_umol_m2_s",
            "authenticated": True,
            "plant_free_reference_plane": True,
        }
    with pytest.raises(SurfaceFluxDisplayError, match="achieved"):
        select_authenticated_achieved_surface_flux_reference(
            operating_policy="target-controlled",
            achieved_stage_a_mean_ppfd_umol_m2_s=0,
        )


def test_cli_requires_only_c2_input_and_output_arguments() -> None:
    args = build_parser().parse_args(
        ["--c2-input-dir", "/c2", "--output-dir", "/output"]
    )
    assert args.c2_input_dir == Path("/c2")
    assert args.output_dir == Path("/output")
    project = (Path(__file__).parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert (
        "fspm-optics-generate-surface-flux-display-calibration = "
        '"fspm_optics.surface_flux_display_calibration_cli:main"'
    ) in project
    assert '"resources/calibration/d5-c3/*"' in project


def _display_artifact(filename: str, data: bytes) -> BinaryDisplayArtifact:
    return BinaryDisplayArtifact(filename, data, hashlib.sha256(data).hexdigest())


def test_metadata_v1_v2_routes_are_preserved_and_v3_is_strictly_additive() -> None:
    raw = _display_artifact(
        "surface-flux/patch-values.v1.f32le.bin", struct.pack("<4f", 1, 2, 3, 4)
    )
    for version in (1, 2):
        metadata = _display_artifact(
            f"surface-flux/metadata.v{version}.json",
            json.dumps(
                {
                    "schema_id": "fspm-optics.surface-flux-display",
                    "schema_version": version,
                }
            ).encode(),
        )
        historical = SurfaceFluxViewerArtifacts(metadata, raw)
        assert historical.schema_version == version
        assert historical.files == (metadata, raw)
    metadata_v3 = _display_artifact(
        "surface-flux/metadata.v3.json",
        json.dumps(
            {
                "schema_id": "fspm-optics.surface-flux-display",
                "schema_version": 3,
            }
        ).encode(),
    )
    with pytest.raises(ValueError, match="requires its coefficient payload"):
        SurfaceFluxViewerArtifacts(metadata_v3, raw)
    coefficients = _display_artifact(
        "surface-flux/display-calibration-coefficients.v1.f64le.bin",
        struct.pack("<d", 1.0),
    )
    current = SurfaceFluxViewerArtifacts(metadata_v3, raw, coefficients)
    assert current.schema_version == 3
    assert current.files == (metadata_v3, raw, coefficients)
    historical_back = palette_contract()["back"]
    assert historical_back == {
        "palette_id": "surface-flux-blue-to-red-8-v1",
        "variable": "z",
        "anchors_z": list(BACK_ANCHORS),
        "colors": [
            {"name": name, "srgb_hex": color}
            for name, color in BACK_PALETTE
        ],
        "continuous_interpolation": True,
        "shared_across_metrics_systems_and_quality_families": True,
        "per_run_extrema_normalization": False,
    }
    authenticated_back = authenticated_palette_contract()["back"]
    assert authenticated_back["variable"] == "u"
    assert len(authenticated_back["anchors_u"]) == 8
    assert "anchors_z" not in authenticated_back


def test_schema_v3_back_palette_is_fixed_shared_and_covers_observed_maxima() -> None:
    expected_colors = [
        {"name": name, "srgb_hex": color}
        for name, color in SCHEMA_V3_BACK_PALETTE
    ]
    palettes = authenticated_palette_contract()
    assert palettes["front"]["anchors_u"] == list(FRONT_LOCAL_PATCH_ANCHORS)
    assert palettes["front"] == palette_contract()["front"]
    assert palettes["back"] == {
        "palette_id": SCHEMA_V3_BACK_PALETTE_ID,
        "variable": "u",
        "anchors_u": list(SCHEMA_V3_BACK_ANCHORS),
        "colors": expected_colors,
        "continuous_interpolation": True,
        "shared_across_metrics_systems_and_quality_families": True,
        "per_run_extrema_normalization": False,
    }
    assert SCHEMA_V3_BACK_ANCHORS[4] == 1.0
    assert SCHEMA_V3_BACK_PALETTE[4] == ("green_reference", "#22C55E")

    observed_maxima_u = {
        "incident_par": 501.0217,
        "absorbed_par": 501.6332,
    }
    assert max(observed_maxima_u.values()) <= SCHEMA_V3_BACK_ANCHORS[-1]
    reference = select_authenticated_achieved_surface_flux_reference(
        operating_policy="target-controlled",
        achieved_stage_a_mean_ppfd_umol_m2_s=500.0,
    )
    signatures = []
    for metric, maximum_u in observed_maxima_u.items():
        legend = build_local_patch_legend_contract(
            metric=metric,
            side="back",
            gamma=[1.0] * D5_PATCHES_PER_PLANT,
            reference=reference,
            raw_values=[maximum_u * reference.value_umol_m2_s]
            * D5_PATCHES_PER_PLANT,
            physical_areas_m2=[1.0] * D5_PATCHES_PER_PLANT,
        )
        assert legend["all_cells_available"] is True
        assert legend["availability_mask_applied"] is False
        assert legend["clipping"]["upper_bound_u"] == 512.0
        assert legend["clipping"]["above_count"] == 0
        signature = [
            (anchor["u"], anchor["color_name"], anchor["srgb_hex"])
            for anchor in legend["anchors"]
        ]
        assert signature == [
            (anchor, name, color)
            for anchor, (name, color) in zip(
                SCHEMA_V3_BACK_ANCHORS,
                SCHEMA_V3_BACK_PALETTE,
                strict=True,
            )
        ]
        signatures.append(signature)
    assert signatures[0] == signatures[1]
