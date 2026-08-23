from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fspm_optics.plants.generator import generate_rex_butterhead_plant
from fspm_optics.receivers.samples import build_two_sided_patch_receivers
from fspm_optics.transport.five_band import (
    EXPECTED_FAR_RED_RESULT_UNITS,
    FIVE_BAND_ORDER,
    plan_rex_five_band_transport,
)
from fspm_optics.transport.five_band_execution import (
    FIVE_BAND_RECEIVER_SCIENTIFIC_CLAIM,
    FiveBandReceiverSmokeError,
    compute_five_band_incident_metrics,
    execute_rex_five_band_receiver_smoke,
    parse_five_band_receiver_rgb,
    run_rex_five_band_receiver_smoke,
)
from tests.support.five_band import (
    RecordingFiveBandRunner,
    fake_installation,
    solved_workspace,
)


def test_native_run_is_fixed_sequential_and_routes_all_streams(tmp_path: Path) -> None:
    plan = plan_rex_five_band_transport(solved_workspace(tmp_path), nthreads=3)
    runner = RecordingFiveBandRunner()

    result = execute_rex_five_band_receiver_smoke(
        plan,
        runner,  # type: ignore[arg-type]
        radiance_installation=fake_installation(),
    )

    assert result.band_order == FIVE_BAND_ORDER
    assert len(runner.calls) == 10
    assert [call.label for call in runner.calls] == [
        label
        for band_id in FIVE_BAND_ORDER
        for label in (
            f"compile_rex_{band_id}_receiver",
            f"trace_rex_{band_id}_receiver",
        )
    ]
    for band, oconv, rtrace in zip(
        plan.band_plans, runner.calls[::2], runner.calls[1::2], strict=True
    ):
        assert oconv.argv[0] == "/fake/oconv"
        assert oconv.stdout_mode == "binary"
        assert oconv.stdout_path == band.paths.octree_path
        assert oconv.stdin_path is None
        assert rtrace.argv[0] == "/fake/rtrace"
        assert rtrace.stdout_mode == "text"
        assert rtrace.stdin_path == plan.receiver_path
        assert rtrace.stdout_path == band.paths.rgb_output_path
        assert rtrace.argv[rtrace.argv.index("-n") + 1] == "3"
    assert len({band.paths.ambient_cache_path for band in plan.band_plans}) == 5

    for band_index, (band, record) in enumerate(
        zip(plan.band_plans, result.band_runs, strict=True)
    ):
        saved = np.load(record.decoded_npy_path, allow_pickle=False)
        assert saved.dtype == np.float64
        assert saved.shape == (1024,)
        assert np.array_equal(saved[0::2], np.full(512, 10 + band_index))
        assert np.array_equal(saved[1::2], np.full(512, 2 + band_index))
        assert record.metrics.front_mean_incident_band_pfd == 10 + band_index
        assert record.metrics.back_mean_incident_band_pfd == 2 + band_index
        assert record.metrics.mean_incident_band_pfd == 6 + band_index
        assert record.metrics.area_weighted_mean_incident_band_pfd == pytest.approx(
            6 + band_index
        )
    assert result.scientific_claim == FIVE_BAND_RECEIVER_SCIENTIFIC_CLAIM
    assert result.transport_plan is plan


def test_summary_is_deterministic_explicit_and_incident_only(tmp_path: Path) -> None:
    plan = plan_rex_five_band_transport(solved_workspace(tmp_path))
    first = execute_rex_five_band_receiver_smoke(
        plan,
        RecordingFiveBandRunner(),  # type: ignore[arg-type]
        radiance_installation=fake_installation(),
    )
    first_text = first.summary_path.read_text(encoding="utf-8")
    second = execute_rex_five_band_receiver_smoke(
        plan,
        RecordingFiveBandRunner(),  # type: ignore[arg-type]
        radiance_installation=fake_installation(),
    )
    second_text = second.summary_path.read_text(encoding="utf-8")
    payload = json.loads(first_text)

    assert first_text == second_text
    assert payload["band_order"] == list(FIVE_BAND_ORDER)
    assert payload["receiver_counts"] == {
        "all": 1024,
        "back": 512,
        "front": 512,
        "physical_patches": 512,
    }
    assert payload["scalar_par_policy"].startswith("scalar_PAR_is_separate")
    assert "front and back are not summed" in payload["area_weighted_mean_definition"]
    assert "absorb" not in first_text.lower()
    assert all(
        command["shell"] is False
        for band in payload["bands"]
        for command in band["commands"].values()
    )
    far_red = payload["bands"][-1]["metrics"]
    assert far_red["quantity"] == "far_red_photon_flux_density"
    assert far_red["units"] == EXPECTED_FAR_RED_RESULT_UNITS
    assert "ppfd" not in far_red["quantity"].lower()
    assert second.summary_path == plan.output_root / "five_band_receiver_summary.json"


def test_preflight_removes_all_derived_outputs_and_preserves_inputs(
    tmp_path: Path,
) -> None:
    plan = plan_rex_five_band_transport(solved_workspace(tmp_path))
    summary_path = plan.output_root / "five_band_receiver_summary.json"
    derived_paths = [summary_path]
    deterministic_inputs = [
        plan.manifest_path,
        plan.source_model_path,
        plan.material_plan_path,
        plan.receiver_path,
    ]
    for band in plan.band_plans:
        derived_paths.extend(
            (
                band.paths.octree_path,
                band.paths.rgb_output_path,
                band.paths.decoded_pfd_path,
                band.paths.ambient_cache_path,
            )
        )
        deterministic_inputs.extend(
            (band.paths.emitter_path, band.paths.plant_path)
        )
    for path in derived_paths:
        path.write_bytes(f"stale:{path.name}".encode("utf-8"))
    input_bytes = {path: path.read_bytes() for path in deterministic_inputs}
    preflight_checks: list[bool] = []

    def assert_preflight_cleanup() -> None:
        assert all(not path.exists() for path in derived_paths)
        assert all(path.read_bytes() == content for path, content in input_bytes.items())
        preflight_checks.append(True)

    runner = RecordingFiveBandRunner(before_first_run=assert_preflight_cleanup)
    execute_rex_five_band_receiver_smoke(
        plan,
        runner,  # type: ignore[arg-type]
        radiance_installation=fake_installation(),
    )

    assert preflight_checks == [True]
    assert len(runner.calls) == 10
    assert all(path.read_bytes() == content for path, content in input_bytes.items())
    assert all(not band.paths.ambient_cache_path.exists() for band in plan.band_plans)


@pytest.mark.parametrize(
    ("rows", "match"),
    [
        ("1 1 1\n" * 1023, "expected 1024, got 1023"),
        ("1 1 1\n" * 1025, "expected 1024, got 1025"),
        ("nan nan nan\n" + "1 1 1\n" * 1023, "must be finite"),
        ("-1 -1 -1\n" + "1 1 1\n" * 1023, "must be non-negative"),
        ("1 1.1 1\n" + "1 1 1\n" * 1023, "R=G=B"),
        ("1 1\n" + "1 1 1\n" * 1023, "exactly three"),
        ("0 1 1 1\n" + "1 1 1\n" * 1023, "exactly three"),
    ],
)
def test_strict_receiver_validation_rejects_malformed_rows(
    rows: str, match: str
) -> None:
    with pytest.raises(FiveBandReceiverSmokeError, match=match):
        parse_five_band_receiver_rgb(rows, band_id="blue")


def test_metrics_use_explicit_side_metadata_and_physical_patch_areas(
    tmp_path: Path,
) -> None:
    plan = plan_rex_five_band_transport(solved_workspace(tmp_path))
    samples = build_two_sided_patch_receivers(generate_rex_butterhead_plant())
    values = np.asarray(
        [
            (sample.area_m2 * 1e6 if sample.side == "front" else 2.0)
            for sample in samples
        ],
        dtype=np.float64,
    )

    metrics = compute_five_band_incident_metrics(
        plan.band_plans[0], values, samples
    )
    front = np.asarray([index for index, item in enumerate(samples) if item.side == "front"])
    back = np.asarray([index for index, item in enumerate(samples) if item.side == "back"])
    weights = np.asarray([item.area_m2 for item in samples])

    assert metrics.front_receiver_count == metrics.back_receiver_count == 512
    assert metrics.front_mean_incident_band_pfd == pytest.approx(values[front].mean())
    assert metrics.back_mean_incident_band_pfd == pytest.approx(values[back].mean())
    assert metrics.area_weighted_mean_incident_band_pfd == pytest.approx(
        np.average(values, weights=weights)
    )


def test_failure_stops_later_bands_and_does_not_write_summary(tmp_path: Path) -> None:
    plan = plan_rex_five_band_transport(solved_workspace(tmp_path))
    runner = RecordingFiveBandRunner(fail_band="green", fail_stage="rtrace")
    summary_path = plan.output_root / "five_band_receiver_summary.json"
    summary_path.write_text("stale success\n", encoding="utf-8")
    for band in plan.band_plans:
        for path in (
            band.paths.octree_path,
            band.paths.rgb_output_path,
            band.paths.decoded_pfd_path,
            band.paths.ambient_cache_path,
        ):
            path.write_bytes(b"stale derived data")

    with pytest.raises(FiveBandReceiverSmokeError, match="green native execution stopped"):
        execute_rex_five_band_receiver_smoke(
            plan,
            runner,  # type: ignore[arg-type]
            radiance_installation=fake_installation(),
        )

    assert [call.label for call in runner.calls] == [
        "compile_rex_blue_receiver",
        "trace_rex_blue_receiver",
        "compile_rex_green_receiver",
        "trace_rex_green_receiver",
    ]
    assert plan.band_plans[0].paths.decoded_pfd_path.is_file()
    assert not plan.band_plans[1].paths.decoded_pfd_path.exists()
    assert not plan.band_plans[1].paths.rgb_output_path.exists()
    assert not plan.band_plans[1].paths.ambient_cache_path.exists()
    for band in plan.band_plans[2:]:
        assert not band.paths.octree_path.exists()
        assert not band.paths.rgb_output_path.exists()
        assert not band.paths.decoded_pfd_path.exists()
        assert not band.paths.ambient_cache_path.exists()
    assert not summary_path.exists()


def test_invalid_band_output_stops_before_later_bands(tmp_path: Path) -> None:
    plan = plan_rex_five_band_transport(solved_workspace(tmp_path))
    runner = RecordingFiveBandRunner(malformed_band="green")

    with pytest.raises(FiveBandReceiverSmokeError, match="green receiver row count mismatch"):
        execute_rex_five_band_receiver_smoke(
            plan,
            runner,  # type: ignore[arg-type]
            radiance_installation=fake_installation(),
        )

    assert len(runner.calls) == 4
    assert not plan.band_plans[1].paths.decoded_pfd_path.exists()


def test_convenience_api_builds_plan_and_runs_through_injected_runner(
    tmp_path: Path,
) -> None:
    root = solved_workspace(tmp_path)
    runner = RecordingFiveBandRunner()

    result = run_rex_five_band_receiver_smoke(
        root,
        runner,  # type: ignore[arg-type]
        radiance_installation=fake_installation(),
        nthreads=2,
    )

    assert result.transport_plan.manifest_path.is_file()
    assert len(runner.calls) == 10
    assert all(
        call.argv[call.argv.index("-n") + 1] == "2"
        for call in runner.calls
        if call.argv[0] == "/fake/rtrace"
    )
