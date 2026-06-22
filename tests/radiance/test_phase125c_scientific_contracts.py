from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from rad_rebuild.radiance.engine.photometry import symmetrize_ppfd as sym
from rad_rebuild.radiance.engine.photometry.ppfd_metrics import (
    compute_ppfd_metrics,
    format_ppfd_metrics_line,
)
from rad_rebuild.radiance.engine.photometry.smd_curve_model import (
    _float_from_source,
    validate_curve_model,
)
from rad_rebuild.radiance.engine.photometry.symmetrize_ppfd import (
    SymmetrizeOptions,
    build_grid,
    compute_symmetry_stats,
    load_ppfd,
    main as symmetrize_main,
    run_symmetrization,
    symmetrize_grid,
)
from rad_rebuild.radiance.engine.validation.results import emit_validation_result
from rad_rebuild.radiance.engine.validation import validate_hps_ies as hps
import rad_rebuild.radiance.engine.validation.validate_smd_power_solution as power
from rad_rebuild.radiance.engine.validation import validate_smd_spd_integration as smd
from rad_rebuild.radiance.engine.validation.validate_hps_ies import (
    run_hps_ies_validation,
)
from rad_rebuild.radiance.engine.validation.validate_smd_spd_integration import (
    run_smd_spd_integration_validation,
)


def _write_grid(path: Path, values: list[list[float]]) -> None:
    rows: list[str] = []
    for y_index, row in enumerate(values):
        y = float(y_index - 1)
        for x_index, value in enumerate(row):
            x = float(x_index - 1)
            rows.append(f"{x:.6f} {y:.6f} 0.000000 {value:.6f}\n")
    path.write_text("".join(rows), encoding="utf-8")


def test_symmetrization_d4_averages_orbits_and_preserves_row_order(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "ppfd_map.txt"
    output_path = tmp_path / "symmetrized.txt"
    _write_grid(
        input_path,
        [
            [10.0, 20.0, 30.0],
            [40.0, 50.0, 60.0],
            [70.0, 80.0, 90.0],
        ],
    )

    result = run_symmetrization(
        SymmetrizeOptions(input_path=input_path, output_path=output_path, lam=1.0)
    )

    rows = load_ppfd(output_path)
    values = [row.ppfd_umol_m2_s for row in rows]
    assert values == [50.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0]
    assert [row.x_m for row in rows[:3]] == [-1.0, 0.0, 1.0]
    assert result.after.mean_ppfd == result.before.mean_ppfd
    assert result.after.cv <= result.before.cv


def test_symmetrization_rejects_malformed_rows(tmp_path: Path) -> None:
    input_path = tmp_path / "bad_ppfd.txt"
    input_path.write_text("0 0 0 not-a-number\n", encoding="utf-8")

    with pytest.raises(ValueError, match="numeric PPFD row"):
        load_ppfd(input_path)


def test_symmetrization_rejects_nonfinite_duplicate_and_empty_inputs(
    tmp_path: Path,
) -> None:
    nonfinite = tmp_path / "nonfinite.txt"
    duplicate = tmp_path / "duplicate.txt"
    empty = tmp_path / "empty.txt"
    nonfinite.write_text("0 0 0 nan\n", encoding="utf-8")
    duplicate.write_text("0 0 0 1\n0 0 0 2\n", encoding="utf-8")
    empty.write_text("\n", encoding="utf-8")

    with pytest.raises(ValueError, match="non-finite ppfd"):
        load_ppfd(nonfinite)
    with pytest.raises(ValueError, match="duplicate PPFD coordinate"):
        build_grid(load_ppfd(duplicate))
    with pytest.raises(ValueError, match="no PPFD rows"):
        load_ppfd(empty)
    with pytest.raises(ValueError, match="positive finite"):
        build_grid([sym.PpfdRow(0.0, 0.0, 0.0, 1.0)], tol=0.0)
    with pytest.raises(ValueError, match="empty finite PPFD field"):
        compute_symmetry_stats(np.asarray([np.nan], dtype=np.float64))


def test_symmetrization_axes_only_blend_and_cli_paths(tmp_path: Path) -> None:
    input_path = tmp_path / "ppfd_map.txt"
    output_path = tmp_path / "symmetrized.txt"
    _write_grid(input_path, [[1.0, 2.0], [3.0, 4.0]])
    grid = build_grid(load_ppfd(input_path))

    blended = symmetrize_grid(grid, lam=0.5, axes_only=True)
    full = symmetrize_grid(grid, lam=1.0, axes_only=False)

    assert float(blended[0, 0]) == 1.75
    assert np.allclose(full, np.full((2, 2), 2.5))
    assert (
        symmetrize_main(
            ["--input", str(input_path), "--output", str(output_path), "--verbose"]
        )
        == 0
    )
    assert output_path.exists()
    assert symmetrize_main(["--input", str(tmp_path / "missing.txt")]) == 1


def test_ppfd_metrics_payload_and_log_line_are_stable() -> None:
    metrics = compute_ppfd_metrics(
        np.asarray([[800.0, 1000.0], [1200.0, 1000.0]], dtype=np.float64),
        setpoint_ppfd=1000.0,
        canopy_area_m2=4.0,
        total_input_watts=500.0,
        emitted_ppf_umol_s=6000.0,
        legacy_metrics=True,
    )

    assert metrics["mean"] == pytest.approx(1000.0)
    assert metrics["p05"] == pytest.approx(830.0)
    assert metrics["p50"] == pytest.approx(1000.0)
    assert metrics["p95"] == pytest.approx(1170.0)
    assert metrics["cap_scale"] == pytest.approx(5.0 / 6.0)
    assert metrics["mean_at_cap"] == pytest.approx(833.3333333333334)
    assert metrics["ppf_ge_90_at_cap"] == pytest.approx(1000.0)
    assert metrics["deuc_ge_95_at_cap"] == pytest.approx(2.4)
    legacy_metrics = metrics["legacy"]
    assert isinstance(legacy_metrics, dict)
    assert legacy_metrics["dou_percent"] == pytest.approx(85.85786437626905)
    assert format_ppfd_metrics_line(metrics) == "\n".join(
        [
            "stats: mean=1000.00 min=800.00 max=1200.00 p05=830.00 p95=1170.00",
            "ratios: peak/mean=1.200 min/mean=0.800 min/max=0.667",
            "ppf: out=4000.0 umol/s",
            "cap: cap=1000 cap_scale=0.833 mean@cap=833.33 util@cap=83.3% "
            "ppf@cap=3333.3 umol/s DEUC_elec(cap)=8.000 umol/J "
            "cov±5%mean=50.0% score_hmean_5=62.5",
            "efficacy: full=8.000 umol/J (4000.0/500.0) cap=8.000 umol/J "
            "(3333.3/416.7)",
            "compat: std=141.42 CV=14.14% DOU=85.86%",
        ]
    )


def test_smd_curve_model_source_coercion_and_validation_payload_are_stable() -> None:
    assert _float_from_source({"DRIVER_EFF": "0.95"}, "DRIVER_EFF", 0.96) == 0.95
    assert _float_from_source({"DRIVER_EFF": ""}, "DRIVER_EFF", 0.96) == 0.96
    assert _float_from_source({"DRIVER_EFF": None}, "DRIVER_EFF", 0.96) == 0.96
    assert _float_from_source({"DRIVER_EFF": 1}, "DRIVER_EFF", 0.96) == 1.0
    with pytest.raises(ValueError):
        _float_from_source({"DRIVER_EFF": "not-a-number"}, "DRIVER_EFF", 0.96)

    payload = validate_curve_model()

    assert payload["curve_model_version"] == "curve_vf_rel_ppe_v1"
    numeric_keys = {
        "nominal_input_w",
        "nominal_led_supply_w",
        "nominal_source_umol_s",
        "nominal_output_umol_s",
        "nominal_wall_plug_ppe",
        "nominal_source_ppe",
        "nominal_output_ppe",
        "thermal_multiplier",
        "white_nominal_current_ma",
        "red_nominal_current_ma",
        "white_spectral_par_umol_per_radiant_w",
        "red_spectral_par_umol_per_radiant_w",
    }
    assert numeric_keys < payload.keys()
    assert all(isinstance(payload[key], float) for key in numeric_keys)


def test_hps_validator_is_importable_json_service_without_running_real_simulation() -> (
    None
):
    def fake_case(
        name: str,
        *,
        length_ft: float,
        width_ft: float,
        coverage_ft: float,
        hps_z_m: float,
    ) -> dict[str, object]:
        cv = (
            20.0
            if name == "multi_overlap"
            else (40.0 if name == "single_low" else 30.0)
        )
        corner = (
            0.5 if name == "multi_overlap" else (0.2 if name == "single_low" else 0.3)
        )
        peak = 2.0 if name == "single_high" else 3.0
        return {
            "name": name,
            "length_ft": length_ft,
            "width_ft": width_ft,
            "coverage_ft": coverage_ft,
            "hps_z_m": hps_z_m,
            "fixture_count": 1,
            "mean_ppfd": 100.0,
            "min_ppfd": 50.0,
            "max_ppfd": 300.0,
            "cv_percent": cv,
            "center_ppfd": 100.0,
            "corner_mean_ppfd": corner * 100.0,
            "edge_mid_mean_ppfd": 100.0,
            "peak_over_mean": peak,
            "corner_over_center": corner,
            "layout_profile": "test",
            "model_label": "test",
            "pre_normalization_fixture_ppf_umol_s": 1.0,
            "default_fixture_anchor_umol_s": 1.0,
            "effective_aperture_length_m": 1.0,
            "effective_aperture_width_m": 1.0,
        }

    result = run_hps_ies_validation(
        run_case=fake_case,
        emission_audit=lambda: {
            "single_polygon_emitter": True,
            "no_sphere_source": True,
            "uses_flatcorr": True,
        },
        logging_audit=lambda: {
            "required_summary_tokens_present": True,
            "required_power_keys_present": True,
        },
    )
    buffer = io.StringIO()
    emit_validation_result(result, stream=buffer)
    payload = json.loads(buffer.getvalue())

    assert payload["validator"] == "hps_ies"
    assert payload["passed"] is True
    assert set(payload["metrics"]["cases"]) == {
        "single_low",
        "single_high",
        "multi_overlap",
    }
    assert all(payload["checks"].values())


def test_hps_runner_and_audits_use_files_without_launching_radiance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime_state"
    scripts = tmp_path / "scripts"
    runtime.mkdir()
    scripts.mkdir()
    (tmp_path / "ppfd_map.txt").write_text(
        "\n".join(
            f"{x} {y} 0 {100 + x * 10 + y}"
            for y in (-1.0, 0.0, 1.0)
            for x in (-1.0, 0.0, 1.0)
        ),
        encoding="utf-8",
    )
    (runtime / "hps_layout.json").write_text(
        json.dumps({"profile": "test", "fixtures": [{"id": "fixture-1"}]}),
        encoding="utf-8",
    )
    power_text = "\n".join(
        [
            "model_label=test",
            "ies_lumens_per_lamp_lm=1",
            "ies_total_luminaire_lumens_lm=1",
            "ies_input_watts=1",
            "spd_umol_per_lumen=1",
            "pre_normalization_fixture_ppf_umol_s=1",
            "default_fixture_anchor_umol_s=1",
            "fixture_anchor_authority=test",
            "final_scale_multiplier=1",
            "effective_aperture_length_m=1",
            "effective_aperture_width_m=1",
        ]
    )
    (runtime / "hps_power.txt").write_text(power_text, encoding="utf-8")
    (runtime / "hps_summary.txt").write_text(
        "\n".join(
            [
                "ies_lumens_per_lamp_lm=1",
                "ies_input_watts=1",
                "spd_umol_per_lumen=1",
                "pre_normalization_fixture_ppf_umol_s=1",
                "default_fixture_anchor_umol_s=1",
                "final_scale_multiplier=1",
                "effective_aperture_length_m=1",
                "effective_aperture_width_m=1",
            ]
        ),
        encoding="utf-8",
    )
    (runtime / "GLH-KARMA-8-HPS1000.rad").write_text(
        "void polygon hps_emitter\nvoid brightfunc flatcorr\n",
        encoding="utf-8",
    )

    called: list[list[str]] = []

    def fake_check_call(command: list[str], **_kwargs: object) -> int:
        called.append(command)
        return 0

    monkeypatch.setattr(hps, "ROOT", tmp_path)
    monkeypatch.setattr(hps, "OUT_DIR", runtime)
    monkeypatch.setattr(hps, "HPS_RAD", runtime / "GLH-KARMA-8-HPS1000.rad")
    monkeypatch.setattr(hps, "RADIANCE_SCRIPTS_ROOT", scripts)
    hps_module = cast(Any, hps)
    monkeypatch.setattr(hps_module.subprocess, "check_call", fake_check_call)

    case = hps._run_case(
        "case", length_ft=8.0, width_ft=8.0, coverage_ft=5.0, hps_z_m=1.0
    )
    emission = hps._emission_audit()
    logging_payload = hps._logging_audit()

    assert called and Path(called[0][0]).is_absolute()
    assert case["fixture_count"] == 1
    assert case["layout_profile"] == "test"
    assert emission["single_polygon_emitter"] is True
    assert logging_payload["required_power_keys_present"] is True


def test_hps_main_returns_json_failure_on_validation_exception(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def raise_validation() -> Any:
        raise ValueError("bad validation input")

    monkeypatch.setattr(hps, "run_hps_ies_validation", raise_validation)

    assert hps.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["validator"] == "hps_ies"
    assert payload["checks"]["validator_completed"] is False


def test_smd_spd_validator_is_importable_json_service_without_running_real_simulation() -> (
    None
):
    def fake_curve_model() -> dict[str, Any]:
        return {
            "nominal_source_umol_s": 266.34360599999997,
            "nominal_output_umol_s": 245.03611751999998,
        }

    def fake_scene(name: str, extra_env: dict[str, str]) -> dict[str, float]:
        baseline = {
            "square_12x12_direct": {
                "mean": 718.9125826530612,
                "min": 528.83375,
                "max": 800.00695,
                "p05": 618.132725,
                "p95": 794.973175,
                "ppf_out": 9617.639676871775,
                "cv_percent": 8.710250129308092,
            },
            "rect_16x12_direct": {
                "mean": 605.081433734694,
                "min": 98.06816,
                "max": 801.30845,
                "p05": 135.7346,
                "p95": 794.271075,
                "ppf_out": 10793.069691170233,
                "cv_percent": 35.450122268143005,
            },
        }
        assert extra_env
        return dict(baseline[name])

    result = run_smd_spd_integration_validation(
        curve_model=fake_curve_model, run_scene=fake_scene
    )
    buffer = io.StringIO()
    emit_validation_result(result, stream=buffer)
    payload = json.loads(buffer.getvalue())

    assert payload["validator"] == "smd_spd_integration"
    assert payload["passed"] is True
    assert payload["metrics"]["nominal"]["nominal_output_umol_s"] == 245.03611751999998
    assert all(payload["checks"].values())


def test_smd_spd_validator_reports_drift_without_exiting() -> None:
    def bad_curve_model() -> dict[str, Any]:
        return {
            "nominal_source_umol_s": 0.0,
            "nominal_output_umol_s": 245.03611751999998,
        }

    def fake_scene(_name: str, _extra_env: dict[str, str]) -> dict[str, float]:
        return {
            "mean": float(np.nan),
            "min": 0.0,
            "max": 0.0,
            "p05": 0.0,
            "p95": 0.0,
            "ppf_out": 0.0,
            "cv_percent": 0.0,
        }

    result = run_smd_spd_integration_validation(
        curve_model=bad_curve_model, run_scene=fake_scene
    )

    assert result.passed is False
    assert result.exit_code == 1
    assert not all(result.checks.values())


def test_smd_runner_uses_files_without_launching_radiance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime_state"
    scripts = tmp_path / "scripts"
    runtime.mkdir()
    scripts.mkdir()
    ppfd_values = [500.0, 600.0, 700.0, 800.0]
    (tmp_path / "ppfd_map.txt").write_text(
        "\n".join(f"{idx} 0 0 {value}" for idx, value in enumerate(ppfd_values)),
        encoding="utf-8",
    )
    (runtime / "smd_summary.txt").write_text(
        "Total electrical input: 120 W\nTotal emitted photons: 2400 umol/s\n",
        encoding="utf-8",
    )
    called: list[list[str]] = []

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        called.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(smd, "ROOT", tmp_path)
    monkeypatch.setattr(smd, "SUMMARY_PATH", runtime / "smd_summary.txt")
    monkeypatch.setattr(smd, "PPFD_PATH", tmp_path / "ppfd_map.txt")
    monkeypatch.setattr(smd, "RADIANCE_SCRIPTS_ROOT", scripts)
    smd_module = cast(Any, smd)
    monkeypatch.setattr(smd_module.subprocess, "run", fake_run)

    metrics = smd._run_scene("scene", {"LENGTH_FT": "2", "WIDTH_FT": "2"})

    assert called and Path(called[0][0]).is_absolute()
    assert metrics["mean"] == 650.0
    assert metrics["min"] == 500.0
    assert metrics["max"] == 800.0


def test_smd_helpers_reject_missing_tools_and_malformed_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime_state"
    runtime.mkdir()
    summary_path = runtime / "smd_summary.txt"
    ppfd_path = tmp_path / "ppfd_map.txt"
    summary_path.write_text("summary without numeric power tokens\n", encoding="utf-8")
    ppfd_path.write_text("0 0 0\n", encoding="utf-8")
    smd_module = cast(Any, smd)

    monkeypatch.setattr(smd, "SUMMARY_PATH", summary_path)
    monkeypatch.setattr(smd, "PPFD_PATH", ppfd_path)
    monkeypatch.setattr(smd_module.shutil, "which", lambda _name: None)

    assert smd._parse_summary_power() == (None, None)
    with pytest.raises(FileNotFoundError, match="bash executable"):
        smd._bash_path()
    with pytest.raises(ValueError, match="No PPFD samples"):
        smd._load_ppfd_values()
    with pytest.raises(TypeError, match="value must be numeric"):
        smd._float_from_mapping({"value": "wrong"}, "value")

    original_baseline = smd.BASELINE
    monkeypatch.setattr(
        smd,
        "BASELINE",
        {
            **original_baseline,
            "bad_metrics": {"metrics": "wrong"},
            "bad_env": {"env": "wrong"},
        },
    )
    with pytest.raises(TypeError, match="metrics baseline"):
        smd._baseline_metrics("bad_metrics")
    with pytest.raises(TypeError, match="env baseline"):
        smd._baseline_env("bad_env")


def test_smd_main_returns_json_failure_on_validation_exception(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def raise_validation() -> Any:
        raise ValueError("bad validation input")

    monkeypatch.setattr(smd, "run_smd_spd_integration_validation", raise_validation)

    assert smd.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["validator"] == "smd_spd_integration"
    assert payload["checks"]["validator_completed"] is False


def test_smd_power_solution_service_and_cli_are_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    basis_path = tmp_path / "basis_A.npy"
    manifest_path = tmp_path / "basis_manifest.json"
    stale_path = tmp_path / "ring_powers_optimized.json"
    basis_path.write_bytes(b"placeholder")
    manifest_path.write_text(
        json.dumps({"emitter_env": {"SMD_BASE_RING_N": 12}}),
        encoding="utf-8",
    )
    stale_path.write_text("{}", encoding="utf-8")
    observed_envs: list[dict[str, str]] = []

    def fake_solver(
        *, out_json: Path, solve_env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        assert solve_env["SMD_BASE_RING_N"] == "12"
        out_json.write_text(
            json.dumps(
                {
                    "ring_powers_W_per_module": [1.0, 2.0, 3.0],
                    "smd_solution_metadata": {"status": "ok"},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(["solver"], 0, "", "")

    def fake_field_validation(
        *,
        manifest: object,
        solve_env: dict[str, str],
        solved: object,
    ) -> float:
        assert manifest == {"emitter_env": {"SMD_BASE_RING_N": 12}}
        assert solve_env["LAYOUT_MODE"] == "exact_tiled"
        assert solved == {
            "ring_powers_W_per_module": [1.0, 2.0, 3.0],
            "smd_solution_metadata": {"status": "ok"},
        }
        return 1000.0

    def fake_generator(*, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        observed_envs.append(env)
        if env["LAYOUT_MODE"] == "square":
            return subprocess.CompletedProcess(
                ["generator"], 1, "", "Incompatible ring powers JSON"
            )
        output = "\n".join(
            [
                "Applied validated power overrides",
                "ring powers status       : compatible",
                "smd_model   : curve",
                "nominal reference:",
                "schedule note: deterministic",
                "run-average wall-plug PPE",
            ]
        )
        return subprocess.CompletedProcess(["generator"], 0, output, "")

    monkeypatch.setattr(power, "ROOT", tmp_path)
    monkeypatch.setattr(power, "BASIS_PATH", basis_path)
    monkeypatch.setattr(power, "BASIS_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(power, "STALE_JSON_PATH", stale_path)
    monkeypatch.setattr(power, "_run_solver", fake_solver)
    monkeypatch.setattr(power, "_validate_solved_field", fake_field_validation)
    monkeypatch.setattr(power, "_run_generator", fake_generator)

    result = power.run_smd_power_solution_validation()

    assert result.basis_mean_ppfd == 1000.0
    assert result.fresh_run_excerpt == (
        "Applied validated power overrides",
        "ring powers status       : compatible",
        "smd_model   : curve",
        "nominal reference:",
        "schedule note: deterministic",
        "run-average wall-plug PPE",
    )
    assert [env["LAYOUT_MODE"] for env in observed_envs] == ["square", "exact_tiled"]

    monkeypatch.setattr(power, "run_smd_power_solution_validation", lambda: result)
    power.main()
    assert capsys.readouterr().out.splitlines() == [
        "basis_mean_ppfd=1000.000000",
        "stale_json_rejected=yes",
        "fresh_json_validated=yes",
        "fresh_run_excerpt:",
        "Applied validated power overrides",
        "ring powers status       : compatible",
        "smd_model   : curve",
        "nominal reference:",
        "schedule note: deterministic",
        "run-average wall-plug PPE",
    ]


def test_smd_power_solution_rejects_malformed_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    basis_path = tmp_path / "basis_A.npy"
    manifest_path = tmp_path / "basis_manifest.json"
    basis_path.write_bytes(b"placeholder")

    monkeypatch.setattr(power, "BASIS_PATH", basis_path)
    monkeypatch.setattr(power, "BASIS_MANIFEST_PATH", manifest_path)

    with pytest.raises(SystemExit, match="Missing basis_A.npy"):
        monkeypatch.setattr(power, "BASIS_PATH", tmp_path / "missing_basis.npy")
        power.run_smd_power_solution_validation()

    monkeypatch.setattr(power, "BASIS_PATH", basis_path)
    manifest_path.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit, match="basis_manifest.json did not contain"):
        power._load_manifest()

    manifest_path.write_text(json.dumps({"emitter_env": []}), encoding="utf-8")
    assert power._emitter_env_value(power._load_manifest(), "SMD_BASE_RING_N") == 0


def test_smd_power_solution_rejects_child_process_and_payload_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    basis_path = tmp_path / "basis_A.npy"
    manifest_path = tmp_path / "basis_manifest.json"
    basis_path.write_bytes(b"placeholder")
    manifest_path.write_text(
        json.dumps({"emitter_env": {"SMD_BASE_RING_N": 7}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(power, "BASIS_PATH", basis_path)
    monkeypatch.setattr(power, "BASIS_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(
        power,
        "_run_solver",
        lambda *, out_json, solve_env: subprocess.CompletedProcess(
            ["solver"], 1, "solver stdout", "solver stderr"
        ),
    )
    with pytest.raises(SystemExit, match="solver stderr"):
        power.run_smd_power_solution_validation()

    out_json = tmp_path / "solution.json"
    out_json.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit, match="Solve output did not contain"):
        power._load_solution(out_json)

    monkeypatch.setattr(
        power,
        "load_basis",
        lambda path: np.ones((2, 2), dtype=np.float64),
    )
    with pytest.raises(SystemExit, match="Solve output did not contain ring powers"):
        power._validate_solved_field(
            manifest={"emitter_env": {"SMD_BASE_RING_N": 7}},
            solve_env=power._solve_env({"emitter_env": {"SMD_BASE_RING_N": 7}}),
            solved={"smd_solution_metadata": {}},
        )


def test_smd_power_solution_field_validation_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        power,
        "load_basis",
        lambda path: np.eye(2, dtype=np.float64),
    )
    monkeypatch.setattr(
        power,
        "build_basis_solve_transform",
        lambda *, basis_manifest, runtime_env: {"transform": "stable"},
    )

    def coefficients(solved: object, *, transform: object) -> np.ndarray[Any, Any]:
        assert transform == {"transform": "stable"}
        solved_map = cast(dict[str, object], solved)
        return np.asarray(solved_map["coefficients"], dtype=np.float64)

    monkeypatch.setattr(power, "solution_coefficients_from_json", coefficients)
    manifest: dict[str, object] = {"emitter_env": {"SMD_BASE_RING_N": 7}}
    solve_env = power._solve_env(manifest)

    assert (
        power._validate_solved_field(
            manifest=manifest,
            solve_env=solve_env,
            solved={
                "ring_powers_W_per_module": [1.0],
                "coefficients": [1000.0, 1000.0],
                "smd_solution_metadata": {},
            },
        )
        == 1000.0
    )
    with pytest.raises(SystemExit, match="Basis solve mean drifted"):
        power._validate_solved_field(
            manifest=manifest,
            solve_env=solve_env,
            solved={
                "ring_powers_W_per_module": [1.0],
                "coefficients": [900.0, 900.0],
                "smd_solution_metadata": {},
            },
        )
    with pytest.raises(SystemExit, match="missing smd_solution_metadata"):
        power._validate_solved_field(
            manifest=manifest,
            solve_env=solve_env,
            solved={
                "ring_powers_W_per_module": [1.0],
                "coefficients": [1000.0, 1000.0],
            },
        )


def test_smd_power_solution_rejects_generator_contract_breaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = {"emitter_env": {"SMD_BASE_RING_N": 7}}

    monkeypatch.setattr(
        power,
        "_run_generator",
        lambda *, env: subprocess.CompletedProcess(["generator"], 0, "", ""),
    )
    with pytest.raises(SystemExit, match="Stale ring-power JSON was not rejected"):
        power._validate_stale_json_rejected()

    monkeypatch.setattr(
        power,
        "_run_generator",
        lambda *, env: subprocess.CompletedProcess(["generator"], 1, "", "failed"),
    )
    with pytest.raises(SystemExit, match="failed"):
        power._fresh_run_output(tmp_path=tmp_path / "ring.json", manifest=manifest)

    monkeypatch.setattr(
        power,
        "_run_generator",
        lambda *, env: subprocess.CompletedProcess(
            ["generator"],
            0,
            "Applied validated power overrides\nlegacy:\n",
            "",
        ),
    )
    with pytest.raises(SystemExit, match="Missing expected output marker"):
        power._fresh_run_output(tmp_path=tmp_path / "ring.json", manifest=manifest)

    markers = "\n".join(power._required_output_markers())
    monkeypatch.setattr(
        power,
        "_run_generator",
        lambda *, env: subprocess.CompletedProcess(
            ["generator"], 0, f"{markers}\nlegacy:\n", ""
        ),
    )
    with pytest.raises(SystemExit, match="stale legacy metrics"):
        power._fresh_run_output(tmp_path=tmp_path / "ring.json", manifest=manifest)
