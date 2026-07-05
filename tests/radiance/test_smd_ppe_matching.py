from __future__ import annotations

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.backend.env import _env_for_mode  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.config import COMPETITOR_FIXTURE_PPE_UMOL_PER_J  # noqa: E402


def _smd_request(*, match_system_ppe: bool) -> RadianceRunRequest:
    return RadianceRunRequest(
        action="all",
        mode="SMD",
        execution_mode="live_local",
        length_ft=10,
        width_ft=10,
        target_ppfd=275,
        match_system_ppe=match_system_ppe,
    )


def test_smd_ppe_matching_uses_conventional_fixture_ppe() -> None:
    env = _env_for_mode(_smd_request(match_system_ppe=True))

    assert env["SMD_MODEL"] == "legacy"
    assert env["PPE_IS_SYSTEM"] == "1"
    assert float(env["SMD_TARGET_PPE_UMOL_PER_J"]) == COMPETITOR_FIXTURE_PPE_UMOL_PER_J
    assert env["SMD_PPE_REFERENCE_MODE"] == "matched_conventional_fixture_ppe"
    assert env["EFF_SCALE"] == "1.0"
    assert env["DROOP_K"] == "0.0"


def test_smd_native_curve_model_remains_available_when_ppe_matching_is_off() -> None:
    env = _env_for_mode(_smd_request(match_system_ppe=False))

    assert env["SMD_MODEL"] == "curve"
    assert env["PPE_IS_SYSTEM"] == "0"
    assert env["SMD_TARGET_PPE_UMOL_PER_J"] == "0.0"
