from __future__ import annotations

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.backend.jobs import _allowlisted_env  # noqa: E402


def test_fspm_job_environment_keys_survive_job_sanitization() -> None:
    clean = _allowlisted_env(
        {
            "PATH": "/usr/bin",
            "FSPM_PLANTS_ENABLED": "1",
            "FSPM_PLANT_SEED": "42",
            "FSPM_PLANT_ROWS": "2",
            "FSPM_PLANT_COLUMNS": "2",
            "FSPM_PLANT_LEAF_COUNT": "12",
            "NOT_ALLOWED_SECRET": "do-not-keep",
        }
    )

    assert clean["FSPM_PLANTS_ENABLED"] == "1"
    assert clean["FSPM_PLANT_SEED"] == "42"
    assert clean["FSPM_PLANT_ROWS"] == "2"
    assert clean["FSPM_PLANT_COLUMNS"] == "2"
    assert clean["FSPM_PLANT_LEAF_COUNT"] == "12"
    assert "NOT_ALLOWED_SECRET" not in clean
