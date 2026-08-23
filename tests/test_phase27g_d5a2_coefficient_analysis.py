from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import struct

import pytest

from fspm_optics.application import surface_flux_coefficient_analysis as analysis
from fspm_optics.application.fspm_science import ParPatchSurfaceLight
from fspm_optics.application.surface_flux_calibration import CalibrationCriteria
from fspm_optics.application.surface_flux_recalibration import (
    D5_BAND_ORDER,
    D5_COMPLETION_NAME,
    D5_EXPERIMENT_ID,
    D5_JOBS_DIRECTORY,
    D5_QUALITY_ORDER,
    SurfaceFluxRecalibrationConfig,
    recalibration_jobs,
)
from fspm_optics.surface_flux_coefficient_analysis_cli import build_parser


def _derived_jobs(
    *,
    value: float,
    plant_count: int,
    patches_per_plant: int,
) -> tuple[analysis._DerivedJob, ...]:
    jobs = []
    for job in recalibration_jobs():
        count = plant_count * patches_per_plant
        channels = {
            channel: tuple(value * job.requested_level for _ in range(count))
            for _side, _metric, channel in analysis.COEFFICIENT_CHANNEL_ORDER
        }
        jobs.append(
            analysis._DerivedJob(
                job_id=job.job_id,
                family=job.quality,
                requested_level=job.requested_level,
                achieved_reference=job.requested_level,
                values_by_channel=channels,
                stage_c_validation={"authoritative_phase27g_c_equations_reused": True},
                source_receiver_sha256_by_band={
                    band: str(index) * 64
                    for index, band in enumerate(D5_BAND_ORDER, start=1)
                },
            )
        )
    return tuple(jobs)


def _coefficient_channels(
    value: float,
    *,
    patches_per_plant: int = 192,
) -> tuple[analysis._CoefficientChannel, ...]:
    return tuple(
        analysis._CoefficientChannel(
            family=family,
            side=side,
            metric=metric,
            channel=channel,
            values=tuple(value + order_index for _ in range(patches_per_plant)),
            patch_fits=(),
            distribution=analysis._distribution_summary(
                tuple(value + order_index for _ in range(patches_per_plant))
            ),
        )
        for order_index, (family, (side, metric, channel)) in enumerate(
            (
                (family, channel)
                for family in D5_QUALITY_ORDER
                for channel in analysis.COEFFICIENT_CHANNEL_ORDER
            )
        )
    )


def test_two_level_formula_reuses_established_through_origin_fit() -> None:
    criteria = CalibrationCriteria()
    result = analysis._fit_local_patch(
        requested_levels=(250.0, 500.0),
        achieved_levels=(247.5, 496.0),
        means=(99.0, 198.4),
        criteria=criteria,
    )

    assert result["coefficient_gamma"] == pytest.approx(0.4)
    assert result["r_squared"] == pytest.approx(1.0)
    assert result["relative_ratio_drift"] == pytest.approx(0.0)
    assert [level["response_ratio_mean_q_over_R"] for level in result["levels"]] == pytest.approx(
        [0.4, 0.4]
    )
    assert [level["signed_residual_umol_m2_s"] for level in result["levels"]] == pytest.approx(
        [0.0, 0.0]
    )
    assert result["promotion_eligible"] is True


def test_fit_reports_drift_residual_and_centered_r_squared_failure() -> None:
    result = analysis._fit_local_patch(
        requested_levels=(250.0, 500.0),
        achieved_levels=(250.0, 500.0),
        means=(100.0, 260.0),
        criteria=CalibrationCriteria(),
    )

    assert result["coefficient_gamma"] == pytest.approx(0.496)
    assert result["relative_ratio_drift"] > 0.01
    assert result["r_squared"] < 0.999
    assert result["residual_sum_squares"] > 0.0
    assert result["promotion_eligible"] is False
    assert result["promotion_reasons"] == [
        "through_origin_r_squared_below_minimum",
        "relative_ratio_drift_above_maximum",
    ]


def test_plant_mean_uses_math_fsum_in_canonical_64_plant_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(analysis, "D5_PATCHES_PER_PLANT", 1)
    base_samples = (1e16, *(1.0 for _ in range(63)))
    jobs = []
    for job in recalibration_jobs():
        scale = job.requested_level / 250.0
        samples = tuple(value * scale for value in base_samples)
        jobs.append(
            analysis._DerivedJob(
                job_id=job.job_id,
                family=job.quality,
                requested_level=job.requested_level,
                achieved_reference=job.requested_level,
                values_by_channel={
                    channel: samples
                    for _side, _metric, channel in analysis.COEFFICIENT_CHANNEL_ORDER
                },
                stage_c_validation={},
                source_receiver_sha256_by_band={},
            )
        )

    channels, failures = analysis._analyze_channels(
        tuple(jobs), criteria=CalibrationCriteria()
    )
    expected = (math.fsum(base_samples) / 64.0) / 250.0
    naive_total = 0.0
    for value in base_samples:
        naive_total += value
    naive = (naive_total / 64.0) / 250.0

    assert channels[0].values[0] == pytest.approx(expected, rel=1e-15)
    assert channels[0].values[0] != naive
    assert failures["failed_patch_fit_count"] == 0


def test_exact_coefficient_binary_order_and_combined_bytes_are_deterministic() -> None:
    channels = _coefficient_channels(0.25)
    first_payloads, first_manifest = analysis._coefficient_payloads(
        channels,
        source_completion_sha256="a" * 64,
    )
    second_payloads, second_manifest = analysis._coefficient_payloads(
        channels,
        source_completion_sha256="a" * 64,
    )

    expected_order = [
        (family, side, metric)
        for family in D5_QUALITY_ORDER
        for side, metric, _channel in analysis.COEFFICIENT_CHANNEL_ORDER
    ]
    observed_order = [
        (record["family"], record["side"], record["metric"])
        for record in first_manifest["array_order"]
    ]
    assert observed_order == expected_order
    assert len(first_payloads) == 13
    assert [payload.data for payload in first_payloads] == [
        payload.data for payload in second_payloads
    ]
    assert first_manifest == second_manifest
    combined = first_payloads[-1].data
    assert combined == b"".join(payload.data for payload in first_payloads[:-1])
    assert len(combined) == 12 * 192 * 8
    assert struct.unpack("<d", combined[:8])[0] == 0.25
    assert struct.unpack("<d", combined[192 * 8 : 192 * 8 + 8])[0] == 1.25
    assert analysis.format_surface_flux_coefficient_analysis_json(first_manifest) == (
        analysis.format_surface_flux_coefficient_analysis_json(second_manifest)
    )


def test_coefficient_distributions_report_exact_zeros_and_minimum_positive() -> None:
    summary = analysis._distribution_summary((0.0, 0.0, 1e-300, 2.0, 4.0))

    assert summary["all_finite"] is True
    assert summary["exact_zero_count"] == 2
    assert summary["positive_count"] == 3
    assert summary["minimum"] == 0.0
    assert summary["minimum_positive"] == 1e-300
    assert summary["maximum"] == 4.0
    assert summary["negative_count"] == 0


def test_stage_c_kernel_is_called_once_per_job_and_order_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(analysis, "D5_EXPECTED_PLANT_COUNT", 2)
    monkeypatch.setattr(analysis, "D5_PATCHES_PER_PLANT", 3)
    monkeypatch.setattr(analysis, "NEUTRAL_ARRAY_VALUE_COUNT", 6)
    calls: list[str] = []

    class _Validation:
        def to_dict(self):
            return {"authoritative_phase27g_c_equations_reused": True}

    def fake_stage_c(*, root, scene, band_records, patch_sink, chunk_bytes=0):
        del scene, chunk_bytes
        calls.append(root.name)
        assert [band["band_id"] for band in band_records] == list(D5_BAND_ORDER)
        for global_patch_index in range(6):
            plant_index, local_patch_index = divmod(global_patch_index, 3)
            patch_sink(
                ParPatchSurfaceLight(
                    global_patch_index=global_patch_index,
                    plant_index=plant_index,
                    global_leaf_index=plant_index,
                    local_patch_index=local_patch_index,
                    physical_one_sided_patch_area_m2=1.0,
                    front_incident_photon_flux_density_umol_m2_s=float(
                        global_patch_index
                    ),
                    back_incident_photon_flux_density_umol_m2_s=float(
                        global_patch_index + 10
                    ),
                    front_absorbed_photon_flux_density_umol_m2_s=float(
                        global_patch_index + 20
                    ),
                    back_absorbed_photon_flux_density_umol_m2_s=float(
                        global_patch_index + 30
                    ),
                )
            )
        return _Validation()

    monkeypatch.setattr(
        analysis, "stream_juvenile_par_surface_light", fake_stage_c
    )
    results = []
    for job in recalibration_jobs():
        bands = [
            {
                "order_index": index,
                "band_id": band,
                "receiver_values": {"sha256": str(index + 1) * 64},
            }
            for index, band in enumerate(D5_BAND_ORDER)
        ]
        results.append(
            {
                "plant_transport": {"bands": bands},
                "reference": {
                    "amplitude_resolution": {
                        "final": {
                            "metrics": {
                                "mean_ppfd_umol_m2_s": job.requested_level
                            }
                        }
                    }
                },
            }
        )
    authenticated = analysis._AuthenticatedSweep(
        input_root=tmp_path,
        configuration=SurfaceFluxRecalibrationConfig(output_directory=tmp_path),
        configuration_identity={},
        completion={},
        completion_sha256="a" * 64,
        scene=None,
        results=tuple(results),
    )

    derived = analysis._derive_all_jobs(authenticated)

    assert calls == [job.job_id for job in recalibration_jobs()]
    assert derived[0].values_by_channel["front_incident"] == (
        0.0,
        1.0,
        2.0,
        3.0,
        4.0,
        5.0,
    )
    assert all(
        job.stage_c_validation["authoritative_phase27g_c_equations_reused"]
        is True
        for job in derived
    )


def test_stage_c_reordered_patch_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(analysis, "D5_EXPECTED_PLANT_COUNT", 1)
    monkeypatch.setattr(analysis, "D5_PATCHES_PER_PLANT", 1)
    monkeypatch.setattr(analysis, "NEUTRAL_ARRAY_VALUE_COUNT", 1)

    def reordered(*, patch_sink, **_kwargs):
        patch_sink(
            ParPatchSurfaceLight(
                global_patch_index=0,
                plant_index=0,
                global_leaf_index=0,
                local_patch_index=1,
                physical_one_sided_patch_area_m2=1.0,
                front_incident_photon_flux_density_umol_m2_s=1.0,
                back_incident_photon_flux_density_umol_m2_s=1.0,
                front_absorbed_photon_flux_density_umol_m2_s=1.0,
                back_absorbed_photon_flux_density_umol_m2_s=1.0,
            )
        )
        raise AssertionError("collector should reject before this line")

    monkeypatch.setattr(analysis, "stream_juvenile_par_surface_light", reordered)
    job = recalibration_jobs()[0]
    result = {
        "plant_transport": {
            "bands": [
                {
                    "band_id": band,
                    "receiver_values": {"sha256": str(index + 1) * 64},
                }
                for index, band in enumerate(D5_BAND_ORDER)
            ]
        },
        "reference": {
            "amplitude_resolution": {
                "final": {"metrics": {"mean_ppfd_umol_m2_s": 250.0}}
            }
        },
    }
    authenticated = analysis._AuthenticatedSweep(
        input_root=tmp_path,
        configuration=SurfaceFluxRecalibrationConfig(output_directory=tmp_path),
        configuration_identity={},
        completion={},
        completion_sha256="a" * 64,
        scene=None,
        results=(result,),
    )
    monkeypatch.setattr(analysis, "recalibration_jobs", lambda: (job,))

    with pytest.raises(
        analysis.SurfaceFluxCoefficientAnalysisError,
        match="canonical plant/patch order",
    ):
        analysis._derive_all_jobs(authenticated)


def test_inventory_hash_disagreement_rejects_before_job_or_value_processing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / analysis.D5_CONFIGURATION_NAME).write_text("{}\n", encoding="utf-8")
    (input_root / D5_JOBS_DIRECTORY).mkdir()
    (input_root / D5_COMPLETION_NAME).write_text("{}\n", encoding="utf-8")
    config = SurfaceFluxRecalibrationConfig(output_directory=input_root)
    completion = {
        "ordered_artifact_inventory": [
            {
                "path": analysis.D5_CONFIGURATION_NAME,
                "media_type": "application/json",
                "byte_length": 3,
                "sha256": "0" * 64,
            }
        ]
    }
    reads = iter(({}, completion))
    monkeypatch.setattr(analysis, "_read_json_object", lambda _path: next(reads))
    monkeypatch.setattr(
        analysis,
        "_validate_configuration",
        lambda _root, _state: (
            config,
            {"configuration_sha256": "a" * 64, "plan_sha256": "b" * 64},
            None,
        ),
    )
    monkeypatch.setattr(analysis, "_validate_completion_manifest", lambda *a, **k: None)
    monkeypatch.setattr(analysis, "_validate_completion_identity", lambda *a, **k: None)
    monkeypatch.setattr(
        analysis,
        "_validate_completed_job",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("job processing must occur after whole-tree inventory auth")
        ),
    )

    with pytest.raises(
        analysis.SurfaceFluxCoefficientAnalysisError,
        match="completion inventory",
    ):
        analysis._authenticate_completed_sweep(input_root)


def test_wrong_completion_version_is_rejected() -> None:
    with pytest.raises(
        analysis.SurfaceFluxCoefficientAnalysisError,
        match="required fully completed v3 contract",
    ):
        analysis._validate_completion_identity(
            {
                "schema_id": analysis.D5_COMPLETION_SCHEMA_ID,
                "schema_version": 0,
                "experiment_id": D5_EXPERIMENT_ID,
            },
            "a" * 64,
        )


def test_pre_v3_experiment_identity_is_rejected_before_scene_or_values(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        analysis.SurfaceFluxCoefficientAnalysisError,
        match="required v3 schema and experiment",
    ):
        analysis._validate_configuration(
            tmp_path,
            {
                "schema_id": analysis.D5_CONFIGURATION_SCHEMA_ID,
                "schema_version": analysis.D5_CONFIGURATION_SCHEMA_VERSION,
                "experiment_id": "phase27g-d5-a1-optimized-surface-flux-sweep-v2",
            },
        )


def test_exact_zero_is_preserved_and_normalized_evidence_uses_canonical_nan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(analysis, "D5_EXPECTED_PLANT_COUNT", 1)
    monkeypatch.setattr(analysis, "D5_PATCHES_PER_PLANT", 2)
    monkeypatch.setattr(analysis, "NEUTRAL_ARRAY_VALUE_COUNT", 2)
    monkeypatch.setattr(analysis, "NEUTRAL_ARRAY_BYTES", 16)
    channels = _coefficient_channels(0.0, patches_per_plant=2)
    channels = tuple(replace(channel, values=(0.0, 0.0)) for channel in channels)
    jobs = _derived_jobs(value=0.0, plant_count=1, patches_per_plant=2)
    coefficient_payloads = tuple(
        analysis._BinaryPayload(
            relative_path=f"coefficient-{index}.bin",
            data=b"",
            record={
                "family": channel.family,
                "side": channel.side,
                "metric": channel.metric,
                "artifact": {"sha256": format(index, "064x")},
            },
        )
        for index, channel in enumerate(channels, start=1)
    ) + (
        analysis._BinaryPayload("combined.bin", b"", {"artifact": {"sha256": "f" * 64}}),
    )

    payloads, manifest = analysis._neutral_payloads(
        jobs,
        channels,
        coefficient_payloads=coefficient_payloads,
        source_completion_sha256="a" * 64,
    )

    assert len(payloads) == 24
    assert payloads[0].data == analysis.CANONICAL_QNAN_BYTES * 2
    assert manifest["artifacts"][0]["undefined_exact_zero_gamma_count"] == 2
    assert manifest["artifacts"][0]["undefined_flat_indices"] == [0, 1]
    assert manifest["palette_anchors_selected"] is False


def test_zero_and_linearity_concerns_report_promotion_false_without_abort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(analysis, "D5_EXPECTED_PLANT_COUNT", 1)
    monkeypatch.setattr(analysis, "D5_PATCHES_PER_PLANT", 2)
    jobs = _derived_jobs(value=0.0, plant_count=1, patches_per_plant=2)

    channels, failures = analysis._analyze_channels(
        jobs,
        criteria=CalibrationCriteria(),
    )

    assert len(channels) == 12
    assert all(channel.values == (0.0, 0.0) for channel in channels)
    assert failures["failed_patch_fit_count"] == 24
    assert failures["reason_counts"]["exact_zero_coefficient"] == 24
    assert all(
        fit["promotion_eligible"] is False
        for channel in channels
        for fit in channel.patch_fits
    )
    authenticated = analysis._AuthenticatedSweep(
        input_root=tmp_path,
        configuration=SurfaceFluxRecalibrationConfig(output_directory=tmp_path),
        configuration_identity={},
        completion={
            "configuration_sha256": "a" * 64,
            "created_at_utc": "2026-07-15T12:00:00Z",
        },
        completion_sha256="b" * 64,
        scene=None,
        results=(),
    )
    report = analysis._analysis_report(
        authenticated,
        (),
        channels,
        coefficient_manifest={"array_order": []},
        neutral_manifest={},
        criteria=CalibrationCriteria(),
        failure_summary=failures,
        promotion_eligible=False,
    )
    assert report["promotion_eligible"] is False
    assert report["promotion_reasons"]["reason_counts"][
        "exact_zero_coefficient"
    ] == 24


def test_report_keeps_direct_absent_with_future_display_only_proxy(
    tmp_path: Path,
) -> None:
    config = SurfaceFluxRecalibrationConfig(output_directory=tmp_path)
    authenticated = analysis._AuthenticatedSweep(
        input_root=tmp_path,
        configuration=config,
        configuration_identity={},
        completion={
            "configuration_sha256": "a" * 64,
            "created_at_utc": "2026-07-15T12:00:00Z",
        },
        completion_sha256="b" * 64,
        scene=None,
        results=(),
    )
    report = analysis._analysis_report(
        authenticated,
        (),
        (),
        coefficient_manifest={"array_order": []},
        neutral_manifest={},
        criteria=CalibrationCriteria(),
        failure_summary={"failed_patch_fit_count": 0},
        promotion_eligible=True,
    )

    assert report["scope"] == {
        "calibrated_families": ["standard", "quality", "rigorous"],
        "direct_calibrated": False,
        "direct_future_display_only_mapping": {"direct": "standard"},
        "direct_future_display_mapping_implemented": False,
        "direct_raw_transport_proxy": False,
        "far_red_present": False,
        "proposed_present": False,
        "conventional_present": False,
    }
    assert "production calibration-resource generation" in report["deferred"]
    assert report["zero_policy"]["near_zero_threshold_defined"] is False


def test_cli_has_only_required_read_input_and_separate_output_controls() -> None:
    parser = build_parser()
    arguments = parser.parse_args(
        ["--input-dir", "/persistent/d5-a1", "--output-dir", "/persistent/d5-a2"]
    )
    assert arguments.input_dir == Path("/persistent/d5-a1")
    assert arguments.output_dir == Path("/persistent/d5-a2")
    assert {action.dest for action in parser._actions} == {
        "help",
        "input_dir",
        "output_dir",
    }


def test_output_cannot_overlap_read_only_input(tmp_path: Path) -> None:
    input_root = tmp_path / "completed-d5-a1"
    input_root.mkdir()

    with pytest.raises(
        analysis.SurfaceFluxCoefficientAnalysisError,
        match="separate from and non-overlapping",
    ):
        analysis._validate_locations(input_root, input_root / "analysis")
