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
            "FSPM_RECEIVER_GRANULARITY": "mesh_patch",
            "FSPM_LEAF_OPTICAL_PROFILE_ID": "rex_green_butterhead_mature_leaf_optics_v1",
            "FSPM_LEAF_RADIANCE_MATERIAL_MODE": "rex_source_weighted_trans",
            "FSPM_SPECTRAL_TRANSPORT_MODE": "banded_5",
            "NOT_ALLOWED_SECRET": "do-not-keep",
        }
    )

    assert clean["FSPM_PLANTS_ENABLED"] == "1"
    assert clean["FSPM_PLANT_SEED"] == "42"
    assert clean["FSPM_PLANT_ROWS"] == "2"
    assert clean["FSPM_PLANT_COLUMNS"] == "2"
    assert clean["FSPM_PLANT_LEAF_COUNT"] == "12"
    assert clean["FSPM_RECEIVER_GRANULARITY"] == "mesh_patch"
    assert clean["FSPM_LEAF_OPTICAL_PROFILE_ID"] == "rex_green_butterhead_mature_leaf_optics_v1"
    assert clean["FSPM_LEAF_RADIANCE_MATERIAL_MODE"] == "rex_source_weighted_trans"
    assert clean["FSPM_SPECTRAL_TRANSPORT_MODE"] == "banded_5"
    assert "NOT_ALLOWED_SECRET" not in clean
