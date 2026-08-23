from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from fspm_optics.application import surface_flux_repeatability_analysis as analysis
from fspm_optics.application import surface_flux_repeatability_analysis_cli as cli
from fspm_optics.application.surface_flux_recalibration import D5_BAND_ORDER


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _complete_outcome() -> dict[str, object]:
    return {
        "status": "complete",
        "execution_complete": True,
        "stage_a_trace_count": 0,
        "stage_b_trace_count_planned": 16,
        "stage_b_trace_count_executed": 16,
        "stage_b_trace_count_remaining": 0,
        "job_order": [job.job_id for job in analysis.c1.endpoint_replication_jobs()],
        "independently_seeded_sample_claimed": False,
        "nonidentical_bytes_prove_independent_seed": False,
    }


def _fake_c1_tree(tmp_path: Path) -> tuple[Path, SimpleNamespace]:
    root = tmp_path / "c1"
    root.mkdir()
    _write_json(root / analysis.c1.D5_C1_CONFIGURATION_NAME, {"state": "fake"})
    _write_json(root / analysis.c1.D5_C1_OUTCOME_NAME, _complete_outcome())
    sources = []
    for job in analysis.c1.endpoint_replication_jobs():
        sources.append(SimpleNamespace(job=job))
        traces = [
            {
                "band_id": band_id,
                "byte_identical_to_original": False,
                "independence_interpretation": (
                    "nonidentical bytes do not prove an independently seeded sample"
                ),
            }
            for band_id in D5_BAND_ORDER
        ]
        _write_json(
            root
            / analysis.c1.D5_C1_JOBS_DIRECTORY
            / job.job_id
            / analysis.c1.D5_C1_JOB_RESULT_NAME,
            {
                "status": "complete",
                "stage_a_trace_count": 0,
                "stage_b_trace_count": 4,
                "ordered_traces": traces,
            },
        )
    upstream = SimpleNamespace(
        a1=SimpleNamespace(input_root=tmp_path / "a1"),
        a2=SimpleNamespace(root=tmp_path / "a2"),
        source_jobs=tuple(sources),
    )
    return root, upstream


def test_completed_c1_authentication_requires_all_sixteen_nonidentical_pairings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, upstream = _fake_c1_tree(tmp_path)
    monkeypatch.setattr(analysis.c1, "_build_plan", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        analysis.c1,
        "_validate_stored_identity_without_discovery",
        lambda *_args, **_kwargs: {"identity": "fake"},
    )
    monkeypatch.setattr(
        analysis.c1,
        "_validate_outcome",
        lambda *_args, **_kwargs: _complete_outcome(),
    )

    authenticated = analysis._authenticate_completed_c1(root, upstream)

    assert len(authenticated.job_results) == 4
    assert authenticated.outcome["stage_b_trace_count_executed"] == 16


def test_completed_c1_authentication_rejects_pairing_identity_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, upstream = _fake_c1_tree(tmp_path)
    first = (
        root
        / analysis.c1.D5_C1_JOBS_DIRECTORY
        / "quality-250"
        / analysis.c1.D5_C1_JOB_RESULT_NAME
    )
    value = json.loads(first.read_text(encoding="utf-8"))
    value["ordered_traces"][0]["band_id"] = "substituted"
    _write_json(first, value)
    monkeypatch.setattr(analysis.c1, "_build_plan", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        analysis.c1,
        "_validate_stored_identity_without_discovery",
        lambda *_args, **_kwargs: {"identity": "fake"},
    )
    monkeypatch.setattr(
        analysis.c1,
        "_validate_outcome",
        lambda *_args, **_kwargs: _complete_outcome(),
    )

    with pytest.raises(
        analysis.SurfaceFluxRepeatabilityAnalysisError,
        match="pairing/nonidentity",
    ):
        analysis._authenticate_completed_c1(root, upstream)


def test_authorized_float64_payload_rejects_corruption_and_unsafe_substitution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "a2"
    root.mkdir()
    path = root / "coefficients.bin"
    data = b"12345678"
    path.write_bytes(data)
    record = {
        "path": "coefficients.bin",
        "byte_length": len(data),
        "sha256": _sha(data),
    }
    assert analysis._authorized_bytes(root, record, "coefficient") == data

    path.write_bytes(b"87654321")
    with pytest.raises(analysis.SurfaceFluxRepeatabilityAnalysisError):
        analysis._authorized_bytes(root, record, "coefficient")
    with pytest.raises(
        analysis.SurfaceFluxRepeatabilityAnalysisError, match="unsafe"
    ):
        analysis._authorized_bytes(
            root,
            {**record, "path": "../substituted.bin"},
            "coefficient",
        )


def test_repeated_band_records_preserve_exact_original_repeat_pairing(
    tmp_path: Path,
) -> None:
    repeated_root = tmp_path / "job"
    material = b"material\n"
    material_sha = _sha(material)
    bands = []
    traces = []
    for index, band_id in enumerate(D5_BAND_ORDER):
        workspace = f"traces/{index:02d}-{band_id}"
        material_path = repeated_root / workspace / "leaf-material.rad"
        material_path.parent.mkdir(parents=True, exist_ok=True)
        material_path.write_bytes(material)
        bands.append(
            {
                "order_index": index,
                "band_id": band_id,
                "material_sha256": material_sha,
                "material": {"authority": "fake"},
            }
        )
        traces.append(
            {
                "job_id": "quality-250",
                "band_order_index": index,
                "band_id": band_id,
                "repeated": {
                    "workspace": workspace,
                    "receiver_values": {
                        "path": f"{workspace}/receiver-values.v1.f64le.bin",
                        "row_count": analysis.D5_EXPECTED_RECEIVER_COUNT_PER_BAND,
                        "byte_length": analysis.D5_EXPECTED_RECEIVER_BYTES_PER_BAND,
                    },
                },
            }
        )
    source = SimpleNamespace(
        job=SimpleNamespace(job_id="quality-250"),
        bands=tuple(bands),
    )

    records = analysis._repeated_band_records(
        source, {"ordered_traces": traces}, repeated_root
    )

    assert tuple(record["band_id"] for record in records) == D5_BAND_ORDER
    assert records[0]["receiver_values"] == traces[0]["repeated"]["receiver_values"]
    assert records[0]["material_artifact"]["sha256"] == material_sha


def test_repeatability_formulas_use_fsum_and_explicit_zero_handling() -> None:
    assert analysis._pooled_within_level_variance((1.0, 2.0), (1.2, 1.8)) == pytest.approx(
        0.02
    )
    effect = analysis._descriptive_standardized_effect(0.8, 0.02)
    assert effect == {
        "value": pytest.approx(0.8 / math.sqrt(0.02)),
        "status": "defined",
    }
    assert analysis._descriptive_standardized_effect(1.0, 0.0) == {
        "value": None,
        "status": "undefined_exact_zero_pooled_within_level_variance",
    }
    relative = analysis._symmetric_relative_difference(1.0, 3.0)
    assert relative["value"] == 1.0
    assert analysis._symmetric_relative_difference(0.0, 0.0) == {
        "value": None,
        "status": "undefined_exact_zero_denominator",
        "absolute_difference": 0.0,
        "denominator_mean_absolute": 0.0,
    }
    assert analysis._through_origin_slope((2.0, 4.0), (6.0, 8.0)) == 2.2


@pytest.mark.parametrize(
    ("original", "repeated", "expected"),
    [
        (1.0, 2.0, "same_nonzero_sign"),
        (-1.0, 2.0, "reversed_nonzero_sign"),
        (0.0, 0.0, "both_exact_zero"),
        (0.0, -1.0, "original_zero_repeated_nonzero"),
        (1.0, 0.0, "original_nonzero_repeated_zero"),
    ],
)
def test_endpoint_sign_agreement_and_reversal(
    original: float, repeated: float, expected: str
) -> None:
    assert analysis._sign_relation(original, repeated) == expected


def _failed_cell(
    family: str,
    side: str,
    metric: str,
    patch: int,
    sign_relation: str,
    *,
    smaller: bool,
) -> dict[str, object]:
    return {
        "family": family,
        "side": side,
        "metric": metric,
        "local_patch_index": patch,
        "a2_source_failed_cell": True,
        "a2_source_failure_reasons": ["relative_ratio_drift_above_maximum"],
        "endpoint_drift": {"sign_relation": sign_relation},
        "endpoint_drift_vs_within_level_repeat_difference": {
            "original_endpoint_not_larger_than_maximum_repeat_difference": smaller,
            "original_relative_endpoint_drift_not_larger_than_maximum_symmetric_relative_repeat_difference": smaller,
        },
        "coefficient_estimates": {"original_a2_gamma": 0.25 + patch},
    }


def test_a2_failed_set_reproduction_summaries_keep_overlap_correlated() -> None:
    cells = (
        _failed_cell(
            "quality", "front", "incident", 3, "same_nonzero_sign", smaller=True
        ),
        _failed_cell(
            "quality", "front", "absorbed", 3, "reversed_nonzero_sign", smaller=False
        ),
        _failed_cell(
            "standard", "back", "incident", 9, "same_nonzero_sign", smaller=True
        ),
    )
    all_a2 = (
        {"family": "quality", "local_patch_index": 3},
        {"family": "standard", "local_patch_index": 9},
        {"family": "rigorous", "local_patch_index": 4},
    )

    evidence = analysis._failed_cell_evidence(cells, all_a2)
    scope = evidence["quality_standard_endpoint_scope_failed_set"]

    assert evidence["authenticated_a2_full_failed_set"]["failed_cell_count"] == 3
    assert scope["failed_cell_count"] == 3
    assert scope["same_endpoint_drift_sign_count"] == 2
    assert scope["reversed_endpoint_drift_sign_count"] == 1
    assert scope[
        "original_endpoint_not_larger_than_maximum_within_level_repeat_count"
    ] == 2
    overlap = scope["incident_absorbed_overlap"]
    assert overlap["independent_replication_interpretation"] is False
    assert overlap["strata"][0]["overlap_local_patch_indices"] == [3]
    assert [
        value["stratum"] for value in scope["presentation_critical_quality_strata"]
    ] == ["quality_front", "quality_back"]


def test_a2_failed_set_validator_requires_exact_reason_sets() -> None:
    identity = {
        "family": "quality",
        "side": "front",
        "metric": "incident",
        "local_patch_index": 7,
    }
    failed = (
        {
            **identity,
            "a2_source_failure_reasons": [
                "through_origin_r_squared_below_minimum",
                "relative_ratio_drift_above_maximum",
            ],
            "original_a2_gamma": 0.5,
        },
    )
    affected = {
        "exact_zero_coefficient": [],
        "through_origin_r_squared_below_minimum": [identity],
        "relative_ratio_drift_above_maximum": [identity],
    }
    report = {
        "promotion_reasons": {
            "failed_patch_fit_count": 1,
            "reason_counts": {
                "exact_zero_coefficient": 0,
                "through_origin_r_squared_below_minimum": 1,
                "relative_ratio_drift_above_maximum": 1,
            },
            "affected_local_patches": affected,
        }
    }

    analysis._validate_a2_failed_set(report, failed)
    report["promotion_reasons"]["failed_patch_fit_count"] = 2
    with pytest.raises(
        analysis.SurfaceFluxRepeatabilityAnalysisError, match="failed-cell sets"
    ):
        analysis._validate_a2_failed_set(report, failed)


def test_analysis_authenticates_before_derivation_and_keeps_inputs_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = [tmp_path / name for name in ("a1", "a2", "c1")]
    for index, root in enumerate(inputs):
        root.mkdir()
        (root / "sentinel.bin").write_bytes(bytes([index]))
    before = [_tree_snapshot(root) for root in inputs]
    calls: list[str] = []
    fake = SimpleNamespace(authorities={"fake": {"status": "complete"}})

    def authenticate(*_args):
        calls.append("authenticate")
        return fake

    def derive(value):
        assert value is fake
        assert calls == ["authenticate"]
        calls.append("derive")
        return ()

    def report(value, observations):
        assert value is fake
        assert observations == ()
        calls.append("report")
        return {"schema_id": analysis.D5_C2_SCHEMA_ID, "status": "complete"}

    monkeypatch.setattr(analysis, "_authenticate_sources", authenticate)
    monkeypatch.setattr(analysis, "_derive_observations", derive)
    monkeypatch.setattr(analysis, "_analysis_report", report)
    output = tmp_path / "out"
    publication = analysis.analyze_surface_flux_endpoint_repeatability(
        analysis.SurfaceFluxRepeatabilityAnalysisConfig(
            a1_input_directory=inputs[0],
            a2_input_directory=inputs[1],
            c1_input_directory=inputs[2],
            output_directory=output,
        )
    )

    assert calls == ["authenticate", "derive", "report"]
    assert publication.report_path.is_file()
    assert publication.completion_path.is_file()
    assert [_tree_snapshot(root) for root in inputs] == before


def test_publication_is_deterministic_and_atomic(tmp_path: Path) -> None:
    report = {"schema_id": analysis.D5_C2_SCHEMA_ID, "values": [1.0, None]}
    authorities = {"d5_a1": {"sha256": "a" * 64}}
    first = analysis._publish(tmp_path / "first", report, authorities)
    second = analysis._publish(tmp_path / "second", report, authorities)

    assert first.report_path.read_bytes() == second.report_path.read_bytes()
    assert first.completion_path.read_bytes() == second.completion_path.read_bytes()
    assert set(path.name for path in first.output_directory.iterdir()) == {
        analysis.D5_C2_REPORT_NAME,
        analysis.D5_C2_COMPLETION_NAME,
    }


def test_failed_publication_leaves_no_output_or_staged_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    original = analysis._write_fsynced
    calls = 0

    def fail_second(path: Path, data: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("bounded completion failure")
        original(path, data)

    monkeypatch.setattr(analysis, "_write_fsynced", fail_second)
    with pytest.raises(OSError, match="bounded completion failure"):
        analysis._publish(output, {"status": "complete"}, {})

    assert not output.exists()
    assert not tuple(tmp_path.glob(".d5-c2-repeatability-*"))


def test_module_has_no_native_or_radiance_execution_path() -> None:
    source = Path(analysis.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "subprocess",
        "LocalRunner",
        "discover_radiance_installation",
        "os.system",
        "os.exec",
    ):
        assert forbidden not in source


def test_cli_exposes_all_four_directory_arguments(tmp_path: Path) -> None:
    paths = [tmp_path / name for name in ("a1", "a2", "c1", "out")]
    arguments = cli.build_parser().parse_args(
        [
            "--a1-input-dir",
            str(paths[0]),
            "--a2-input-dir",
            str(paths[1]),
            "--c1-input-dir",
            str(paths[2]),
            "--output-dir",
            str(paths[3]),
        ]
    )
    config = cli.config_from_namespace(arguments)

    assert config.a1_input_directory == paths[0]
    assert config.a2_input_directory == paths[1]
    assert config.c1_input_directory == paths[2]
    assert config.output_directory == paths[3]
