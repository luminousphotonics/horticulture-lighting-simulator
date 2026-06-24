from __future__ import annotations

from pathlib import Path

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.cli import scripts  # noqa: E402


def _write_stale_plant_artifacts(runtime_state_root: Path) -> None:
    runtime_state_root.mkdir(parents=True, exist_ok=True)
    for name in scripts.PLANT_RUNTIME_ARTIFACT_NAMES:
        (runtime_state_root / name).write_text("stale\n", encoding="utf-8")


def test_fspm_basis_extraction_active_requires_explicit_internal_flag() -> None:
    assert scripts._fspm_basis_extraction_active({"FSPM_SKIP_DURING_BASIS": "1"})


def test_stale_smd_basis_keys_do_not_disable_fspm_by_themselves() -> None:
    assert not scripts._fspm_basis_extraction_active({"SMD_BASIS_MODE": "1"})
    assert not scripts._fspm_basis_extraction_active({"BASIS_MODE": "1"})
    assert not scripts._fspm_basis_extraction_active({"SMD_BASIS_RING": "2"})
    assert not scripts._fspm_basis_extraction_active({"SMD_BASIS_MODULE_IDX": "10"})
    assert not scripts._fspm_basis_extraction_active({"SMD_BASIS_OUTER_MODULE_IDX": "10"})


def test_run_basis_alone_does_not_disable_final_fspm_analysis() -> None:
    assert not scripts._fspm_basis_extraction_active({"RUN_BASIS": "1"})


def test_optional_plant_artifacts_clear_stale_files_during_internal_smd_basis_extraction(tmp_path) -> None:
    config = scripts._runtime_config(
        {
            "RADIANCE_OUTPUT_ROOT": str(tmp_path / "radiance"),
            "FSPM_PLANTS_ENABLED": "1",
            "FSPM_SKIP_DURING_BASIS": "1",
            "SMD_BASIS_MODE": "1",
        }
    )
    _write_stale_plant_artifacts(config.runtime_state_root)

    result = scripts._prepare_optional_plant_artifacts(config)

    assert result is None
    for name in scripts.PLANT_RUNTIME_ARTIFACT_NAMES:
        assert not (config.runtime_state_root / name).exists()


def test_optional_plant_artifacts_clear_stale_files_when_fspm_disabled(tmp_path) -> None:
    config = scripts._runtime_config(
        {
            "RADIANCE_OUTPUT_ROOT": str(tmp_path / "radiance"),
            "FSPM_PLANTS_ENABLED": "0",
        }
    )
    _write_stale_plant_artifacts(config.runtime_state_root)

    result = scripts._prepare_optional_plant_artifacts(config)

    assert result is None
    for name in scripts.PLANT_RUNTIME_ARTIFACT_NAMES:
        assert not (config.runtime_state_root / name).exists()


def test_final_smd_simulation_strips_basis_environment_keys(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_run_command(argv, *, cwd, env):
        captured["argv"] = list(argv)
        captured["cwd"] = cwd
        captured["env"] = dict(env)
        return int(scripts.RadianceScriptExit.OK)

    monkeypatch.setattr(scripts, "_run_command", fake_run_command)

    config = scripts._build_uniformity_config(
        {
            "RADIANCE_OUTPUT_ROOT": str(tmp_path / "radiance"),
            "RADIANCE_BASIS_OUTPUT_ROOT": str(tmp_path / "basis"),
            "FSPM_PLANTS_ENABLED": "1",
            "FSPM_SKIP_DURING_BASIS": "1",
            "SMD_BASIS_MODE": "1",
            "BASIS_MODE": "1",
            "SMD_BASIS_RING": "3",
            "SMD_BASIS_MODULE_IDX": "4",
            "SMD_BASIS_OUTER_MODULE_IDX": "5",
        }
    )

    exit_code = scripts._run_smd_simulation(config)

    assert exit_code == int(scripts.RadianceScriptExit.OK)
    env = captured["env"]
    for key in (
        "FSPM_SKIP_DURING_BASIS",
        "SMD_BASIS_MODE",
        "BASIS_MODE",
        "SMD_BASIS_RING",
        "SMD_BASIS_MODULE_IDX",
        "SMD_BASIS_OUTER_MODULE_IDX",
    ):
        assert key not in env
    assert env["FSPM_PLANTS_ENABLED"] == "1"
