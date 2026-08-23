from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from fspm_optics.diagnostics.stage_a_validator import (
    StageAValidationError,
    _validate_conventional,
    _validate_lighting_target_policy,
    coordinate_quadrature,
    reconstruct_proposed_field,
    validate_stage_a_run,
)
from fspm_optics.transport.conventional_scalar import format_ppfd_values_npz
from fspm_optics.transport.scalar_ppfd import PpfdMapSample


def test_coordinate_quadrature_uses_inferred_control_volume_area() -> None:
    x = np.asarray([0.5, 1.5, 0.5, 1.5], dtype=float)
    y = np.asarray([1.0, 1.0, 3.0, 3.0], dtype=float)
    values = np.asarray([2.0, 2.0, 4.0, 4.0], dtype=float)

    closure = coordinate_quadrature(x, y, values)

    assert closure["grid_shape_y_x"] == [2, 2]
    assert closure["x_bounds_m"] == [0.0, 2.0]
    assert closure["y_bounds_m"] == [0.0, 4.0]
    assert closure["area_m2"] == pytest.approx(8.0)
    assert closure["integrated_receiver_flux_umol_s"] == pytest.approx(24.0)
    assert closure["area_weighted_mean_ppfd_umol_m2_s"] == pytest.approx(3.0)


def test_coordinate_quadrature_rejects_incomplete_or_duplicate_grid() -> None:
    with pytest.raises(StageAValidationError, match="complete rectilinear"):
        coordinate_quadrature(
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [1.0, 2.0, 3.0],
        )


def test_proposed_reconstruction_applies_reference_and_schedule_once() -> None:
    # Columns already contain the response of every module in their zone to
    # the manifest's 2 W/module reference.  The schedule remains W/module.
    basis = np.asarray(
        [
            [20.0, 8.0, 6.0],
            [10.0, 4.0, 12.0],
        ],
        dtype=float,
    )

    field = reconstruct_proposed_field(
        basis_matrix=basis,
        reference_watts_per_module=2.0,
        watts_by_control_zone=(1.0, 3.0, 5.0),
        dimming_factor=0.25,
    )

    assert field == pytest.approx(np.asarray([9.25, 10.25]))


def test_proposed_reconstruction_rejects_zone_count_mismatch() -> None:
    with pytest.raises(StageAValidationError, match="column count"):
        reconstruct_proposed_field(
            basis_matrix=np.ones((2, 3)),
            reference_watts_per_module=1.0,
            watts_by_control_zone=(1.0, 2.0),
            dimming_factor=1.0,
        )


def test_validator_rejects_report_output_inside_supplied_run(tmp_path) -> None:
    run = tmp_path / "persisted-run"
    run.mkdir()

    with pytest.raises(StageAValidationError, match="outside the supplied"):
        validate_stage_a_run(run, run / "validator-output")

    assert tuple(run.iterdir()) == ()


def test_sampled_cap_validator_authenticates_limiting_sample_and_rejects_tamper() -> None:
    request = {
        "lighting_target_mode": "target_capped",
        "target_ppfd_umol_m2_s": 100.0,
    }
    control = {
        "lighting_target_mode": "target_capped",
        "requested_target_ppfd_umol_m2_s": 100.0,
        "achieved_maximum_ppfd_umol_m2_s": 100.0,
        "full_output_maximum_ppfd_umol_m2_s": 200.0,
        "raw_factor": 0.5,
        "cap_binding": True,
        "cap_compliant": True,
        "compliance_tolerance_umol_m2_s": 1e-6,
        "limiting_sample": {
            "index": 1,
            "x_m": 1.0,
            "y_m": 0.0,
            "z_m": 0.1,
            "achieved_ppfd_umol_m2_s": 100.0,
        },
    }
    coordinates = (
        np.asarray([0.0, 1.0]),
        np.asarray([0.0, 0.0]),
        np.asarray([0.1, 0.1]),
    )
    field = np.asarray([80.0, 100.0])

    result = _validate_lighting_target_policy(
        request=request,
        target_control=control,
        coordinates=coordinates,
        published_ppfd=field,
    )
    assert result["cap_compliant"] is True
    assert result["limiting_sample"]["index"] == 1

    tampered = control | {
        "limiting_sample": control["limiting_sample"] | {"index": 0}
    }
    with pytest.raises(StageAValidationError, match="limiting-sample"):
        _validate_lighting_target_policy(
            request=request,
            target_control=tampered,
            coordinates=coordinates,
            published_ppfd=field,
        )


def test_conventional_validator_authenticates_full_output_float64_scaling(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    full_root = run / "engine/conventional-full-output/conventional_scalar"
    scaled_root = run / "engine/conventional-achieved"
    full_root.mkdir(parents=True)
    scaled_root.mkdir(parents=True)
    full_summary = full_root / "scalar_transport_summary.json"
    full_summary.write_text('{"success":true}\n', encoding="utf-8")
    command_summary = full_root / "command_provenance_summary.json"
    command_summary.write_text(
        json.dumps(
            {
                "commands": [
                    {"label": "convert_conventional_unit_downward_flux_ies"},
                    {"label": "compile_conventional_scalar_scene"},
                    {"label": "baseline_scalar_ppfd_rtrace"},
                ]
            }
        ),
        encoding="utf-8",
    )
    coordinates = ((-1.0, 0.0), (-0.5, 0.5), (0.005, 0.005))
    full_samples = tuple(
        PpfdMapSample(x, y, z, value)
        for x, y, z, value in zip(
            *coordinates,
            (600.0, 1000.0),
            strict=True,
        )
    )
    scaled_samples = tuple(
        PpfdMapSample(sample.x_m, sample.y_m, sample.z_m, sample.ppfd_umol_m2_s * 0.5)
        for sample in full_samples
    )
    full_npz = full_root / "ppfd_values.npz"
    scaled_npz = scaled_root / "ppfd_values.npz"
    full_npz.write_bytes(format_ppfd_values_npz(full_samples))
    scaled_npz.write_bytes(format_ppfd_values_npz(scaled_samples))

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    summary = {
        "schema_id": "fspm-optics.conventional-stage-a-scaled-result",
        "schema_version": 1,
        "success": True,
        "policy_id": "conventional_full_output_float64_global_scaling_v1",
        "authority": {"global_output_fraction": 0.5},
        "full_output_transport": {
            "scalar_transport_summary": full_summary.relative_to(run).as_posix(),
            "scalar_transport_summary_sha256": digest(full_summary),
            "command_provenance_summary": (
                command_summary.relative_to(run).as_posix()
            ),
            "command_provenance_summary_sha256": digest(command_summary),
            "ppfd_values_npz": full_npz.relative_to(run).as_posix(),
            "ppfd_values_npz_sha256": digest(full_npz),
        },
        "scaled_result": {
            "ppfd_values_npz": scaled_npz.relative_to(run).as_posix(),
            "ppfd_values_npz_sha256": digest(scaled_npz),
        },
        "native_stage_a_command_counts": {
            "ies2rad_conversion": 1,
            "fixture_shape_compilation": 1,
            "scalar_scene_compilation": 1,
            "baseline_rtrace": 1,
        },
        "native_commands_after_full_output_trace": [],
    }
    summary["derivation_identity_sha256"] = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    scaled_summary = scaled_root / "scaled_result_summary.json"
    scaled_summary.write_text(json.dumps(summary), encoding="utf-8")
    manifest = {
        "artifacts": {
            "conventional_final_summary": (
                scaled_summary.relative_to(run).as_posix()
            )
        }
    }
    metrics = {"counts": {"fixtures": 4}}
    schedule = {
        "fixture_count": 4,
        "rated_fixture_power_w": 660.0,
        "full_output_fixture_ppf_umol_s": 1716.0,
    }
    operating = {
        "global_dimming_factor": 0.5,
        "power": {"effective_w": 1320.0},
        "ppf": {"emitted_umol_s": 3432.0},
    }
    coordinate_arrays = tuple(
        np.asarray(values, dtype=np.float64) for values in coordinates
    )
    published = np.asarray([300.0, 500.0], dtype=np.float64)

    closure = _validate_conventional(
        run_path=run,
        manifest=manifest,
        metrics=metrics,
        schedule=schedule,
        operating=operating,
        coordinates=coordinate_arrays,  # type: ignore[arg-type]
        published_ppfd=published,
    )

    assert closure["reconstruction"]["native_complete_scene_trace_count"] == 1
    assert closure["reconstruction"]["maximum_absolute_error_umol_m2_s"] == 0.0
    assert closure["reconstruction"]["scaled_artifact_reconstruction"][
        "maximum_absolute_error_umol_m2_s"
    ] == 0.0
