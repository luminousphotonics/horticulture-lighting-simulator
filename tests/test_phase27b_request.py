from __future__ import annotations

import hashlib
import json
import pytest

from fspm_optics.application.domain import (
    AnalysisScope,
    ConventionalRunRequest,
    HpsRunRequest,
    LightingTargetMode,
    ProposedRunRequest,
    ProposedLayoutMode,
    ProposedSpectralBasis,
    RequestValidationError,
    parse_run_request,
)
from fspm_optics.layout.mode import (
    PROPOSED_LINEAR_LAYOUT_ENV_VAR,
    PROPOSED_LAYOUT_MODE_ENV_VAR,
    resolve_proposed_layout_mode,
    resolve_proposed_layout_startup_environment,
)


def _payload() -> dict[str, object]:
    return {
        "system": "proposed",
        "target_ppfd": 900.5,
        "room_length_ft": 9.25,
        "room_width_ft": 30.0,
        "mounting_height_in": 18.0,
        "quality": "standard",
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 20.0,
    }


def test_accepts_proposed_decimal_rooms_without_a_default_upper_bound() -> None:
    request = ProposedRunRequest.from_payload(
        _payload() | {"room_length_ft": 30.0, "room_width_ft": 50.0}
    )
    assert request.room_length_ft == 30.0
    assert request.room_width_ft == 50.0
    assert request.mounting_height_in == 18.0
    assert request.mounting_geometry.mounting_height_m == 18.0 * 0.0254
    assert request.mounting_geometry.emitting_aperture_plane_z_m == 0.4622
    assert request.fspm_target_override_umol_m2_s is None
    assert request.spectral_basis is ProposedSpectralBasis.NATIVE_PROPOSED
    assert request.proposed_layout_mode is ProposedLayoutMode.STANDALONE_MODULES
    assert request.to_dict()["proposed_layout_mode"] == "standalone_modules"
    assert "spectral_basis" not in request.to_dict()


def test_proposed_multispectral_spectral_basis_defaults_native_and_accepts_control() -> None:
    native = ProposedRunRequest.from_payload(
        _payload()
        | {"analysis_scope": AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM.value}
    )
    controlled = ProposedRunRequest.from_payload(
        _payload()
        | {
            "analysis_scope": AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM.value,
            "spectral_basis": "conventional_led_control",
        }
    )
    assert native.spectral_basis is ProposedSpectralBasis.NATIVE_PROPOSED
    assert native.to_dict()["spectral_basis"] == "native_proposed"
    assert controlled.spectral_basis is ProposedSpectralBasis.CONVENTIONAL_LED_CONTROL
    assert controlled.to_dict()["spectral_basis"] == "conventional_led_control"


def test_legacy_vendor_control_basis_is_rejected_without_an_alias() -> None:
    legacy_basis = "conventional_" + "qu" + "be_control"
    with pytest.raises(RequestValidationError, match="spectral_basis"):
        ProposedRunRequest.from_payload(
            _payload()
            | {
                "analysis_scope": (
                    AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM.value
                ),
                "spectral_basis": legacy_basis,
            }
        )
    assert legacy_basis not in {item.value for item in ProposedSpectralBasis}


def test_spectral_control_is_rejected_outside_proposed_multispectral_scope() -> None:
    with pytest.raises(RequestValidationError, match="baseline-only"):
        ProposedRunRequest.from_payload(
            _payload() | {"spectral_basis": "conventional_led_control"}
        )
    with pytest.raises(RequestValidationError, match="unsupported request fields"):
        ConventionalRunRequest.from_payload(
            _payload()
            | {
                "system": "conventional",
                "analysis_scope": "baseline_plus_multispectral_fspm",
                "spectral_basis": "conventional_led_control",
            }
        )
    with pytest.raises(RequestValidationError, match="unsupported request fields"):
        HpsRunRequest.from_payload(
            {
                key: value
                for key, value in (
                    _payload()
                    | {
                        "system": "hps",
                        "analysis_scope": "baseline_plus_multispectral_fspm",
                        "spectral_basis": "conventional_led_control",
                    }
                ).items()
                if key != "target_ppfd"
            }
        )


def test_accepts_explicit_positive_fspm_override() -> None:
    payload = _payload() | {
        "fspm_target_mode": "override",
        "fspm_target_ppfd": 875.25,
    }
    request = ProposedRunRequest.from_payload(payload)
    assert request.fspm_target_override_umol_m2_s == 875.25


def test_lighting_target_mode_defaults_historical_led_requests_and_serializes() -> None:
    historical = ProposedRunRequest.from_payload(_payload())
    capped = ConventionalRunRequest.from_payload(
        _payload()
        | {
            "system": "conventional",
            "lighting_target_mode": "target_capped",
        }
    )

    assert historical.lighting_target_mode is LightingTargetMode.MEAN_TARGET
    assert historical.to_dict()["lighting_target_mode"] == "mean_target"
    assert capped.lighting_target_mode is LightingTargetMode.TARGET_CAPPED
    assert capped.to_dict()["lighting_target_mode"] == "target_capped"


def test_lighting_target_mode_rejects_invalid_values_and_hps() -> None:
    with pytest.raises(RequestValidationError) as invalid:
        ProposedRunRequest.from_payload(
            _payload() | {"lighting_target_mode": "automatic"}
        )
    assert invalid.value.field == "lighting_target_mode"

    hps = {
        key: value
        for key, value in (_payload() | {"system": "hps"}).items()
        if key != "target_ppfd"
    }
    with pytest.raises(RequestValidationError) as rejected:
        HpsRunRequest.from_payload(
            hps | {"lighting_target_mode": "mean_target"}
        )
    assert rejected.value.field == "lighting_target_mode"


def test_fspm_reference_tolerance_defaults_to_75() -> None:
    payload = _payload()
    payload.pop("fspm_target_tolerance")
    request = ProposedRunRequest.from_payload(payload)
    assert request.fspm_target_tolerance_umol_m2_s == 75.0


def test_lighting_target_mode_changes_canonical_request_identity() -> None:
    mean = ProposedRunRequest.from_payload(_payload()).to_dict()
    capped = ProposedRunRequest.from_payload(
        _payload() | {"lighting_target_mode": "target_capped"}
    ).to_dict()
    identity = lambda payload: hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert mean["lighting_target_mode"] == "mean_target"
    assert capped["lighting_target_mode"] == "target_capped"
    assert identity(mean) != identity(capped)


def test_conventional_rolling_bench_request_serializes_stable_mode_id() -> None:
    request = parse_run_request(
        _payload()
        | {
            "system": "conventional",
            "layout_mode": "rolling_bench",
        }
    )

    assert isinstance(request, ConventionalRunRequest)
    assert request.layout_mode == "rolling_bench"
    assert request.to_dict()["layout_mode"] == "rolling_bench"


def test_proposed_rolling_bench_combination_fails_with_clear_validation() -> None:
    with pytest.raises(
        RequestValidationError,
        match="supported only for Conventional LED System",
    ) as caught:
        parse_run_request(_payload() | {"layout_mode": "rolling_bench"})

    assert caught.value.field == "layout_mode"


def test_server_injects_legacy_proposed_layout_mode_but_request_fields_are_rejected() -> None:
    request = parse_run_request(
        _payload(),
        proposed_layout_mode=ProposedLayoutMode.LEGACY,
    )
    assert isinstance(request, ProposedRunRequest)
    assert request.proposed_layout_mode is ProposedLayoutMode.LEGACY
    assert request.to_dict()["proposed_layout_mode"] == "legacy"

    for field in ("proposed_layout_mode", "layout_mode"):
        with pytest.raises(RequestValidationError, match="unsupported request fields"):
            parse_run_request(_payload() | {field: "legacy"})


def test_proposed_layout_mode_startup_resolver_is_pure_and_closed() -> None:
    assert resolve_proposed_layout_mode() is ProposedLayoutMode.STANDALONE_MODULES
    assert resolve_proposed_layout_mode("") is ProposedLayoutMode.STANDALONE_MODULES
    assert (
        resolve_proposed_layout_mode("standalone_modules")
        is ProposedLayoutMode.STANDALONE_MODULES
    )
    assert resolve_proposed_layout_mode(" LINEAR ") is ProposedLayoutMode.LINEAR
    assert resolve_proposed_layout_mode("legacy") is ProposedLayoutMode.LEGACY
    with pytest.raises(
        ValueError,
        match=PROPOSED_LAYOUT_MODE_ENV_VAR,
    ):
        resolve_proposed_layout_mode("unsupported")


@pytest.mark.parametrize(
    ("selector", "flag", "expected"),
    (
        (None, None, ProposedLayoutMode.STANDALONE_MODULES),
        ("", "", ProposedLayoutMode.STANDALONE_MODULES),
        (None, "0", ProposedLayoutMode.STANDALONE_MODULES),
        ("legacy", None, ProposedLayoutMode.LEGACY),
        ("legacy", "0", ProposedLayoutMode.LEGACY),
        (None, "1", ProposedLayoutMode.LINEAR),
        ("", "1", ProposedLayoutMode.LINEAR),
    ),
)
def test_startup_environment_migration_contract(
    selector: str | None,
    flag: str | None,
    expected: ProposedLayoutMode,
) -> None:
    assert (
        resolve_proposed_layout_startup_environment(selector, flag)
        is expected
    )


@pytest.mark.parametrize(
    ("selector", "flag", "match"),
    (
        ("linear", None, PROPOSED_LINEAR_LAYOUT_ENV_VAR),
        ("linear", "1", PROPOSED_LINEAR_LAYOUT_ENV_VAR),
        ("legacy", "1", "conflicts"),
        ("standalone_modules", None, PROPOSED_LAYOUT_MODE_ENV_VAR),
        ("standalone_modules", "1", PROPOSED_LAYOUT_MODE_ENV_VAR),
        ("unsupported", None, PROPOSED_LAYOUT_MODE_ENV_VAR),
        (None, "true", PROPOSED_LINEAR_LAYOUT_ENV_VAR),
        (None, "2", PROPOSED_LINEAR_LAYOUT_ENV_VAR),
    ),
)
def test_startup_environment_rejects_conflicts_and_malformed_values(
    selector: str | None,
    flag: str | None,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        resolve_proposed_layout_startup_environment(selector, flag)


@pytest.mark.parametrize(
    "change",
    [
        {"system": "another"},
        {"target_ppfd": 0},
        {"target_ppfd": float("nan")},
        {"room_length_ft": 0},
        {"room_width_ft": float("inf")},
        {"mounting_height_in": None},
        {"mounting_height_in": True},
        {"mounting_height_in": ""},
        {"mounting_height_in": "18"},
        {"mounting_height_in": float("nan")},
        {"mounting_height_in": float("inf")},
        {"mounting_height_in": 0},
        {"mounting_height_in": -1},
        {"mounting_height_in": 120},
        {"quality": "unknown"},
        {"fspm_target_tolerance": -1},
        {"fspm_target_mode": "override"},
        {"fspm_target_ppfd": 800},
        {"workspace_path": "/tmp/arbitrary"},
    ],
)
def test_invalid_and_expansive_requests_fail_closed(change: dict[str, object]) -> None:
    with pytest.raises(RequestValidationError):
        ProposedRunRequest.from_payload(_payload() | change)
