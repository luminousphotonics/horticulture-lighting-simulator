from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import fspm_optics.web.precomputed as web_precomputed
import pytest
from fspm_optics.application.domain import PROPOSED_SYSTEM_ID
from fspm_optics.precomputed import fixed_plan as live_fixed_plan
from fspm_optics.precomputed.committed_playback import (
    HISTORICAL_BUNDLE_SEQUENCE_IDENTITY_SHA256,
    HISTORICAL_CASE_SEQUENCE_IDENTITY_SHA256,
    HISTORICAL_PLAN_IDENTITY_SHA256,
    CommittedPlaybackRole,
    build_committed_playback_plan,
    inspect_committed_playback_catalog,
    load_committed_case_bundle,
)
from fspm_optics.precomputed.compact_bundle import (
    CompactBundleStatus,
    load_compact_bundle,
    validate_compact_bundle,
)
from fspm_optics.web.precomputed import PrecomputedCatalog


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRECOMPUTED_ROOT = REPOSITORY_ROOT / "precomputed"


def _sequence_identity(values: list[str]) -> str:
    payload = json.dumps(values, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_committed_catalog_is_exact_and_all_artifacts_load() -> None:
    plan = build_committed_playback_plan()
    status = inspect_committed_playback_catalog(PRECOMPUTED_ROOT, plan=plan)

    assert plan.plan_identity_sha256 == HISTORICAL_PLAN_IDENTITY_SHA256
    assert plan.case_count == 24
    assert status.valid_count == 24
    assert status.invalid_count == 0
    assert status.remaining_count == 0
    assert _sequence_identity(
        [case.bundle_identity_sha256 for case in plan.cases]
    ) == HISTORICAL_BUNDLE_SEQUENCE_IDENTITY_SHA256

    loaded_identities = []
    loaded_case_ids = []
    for case in plan.cases:
        playback = load_committed_case_bundle(
            case.output_path(PRECOMPUTED_ROOT),
            case,
            plan.plan_identity_sha256,
        )
        loaded_identities.append(
            playback.manifest["bundle_identity_sha256"]
        )
        loaded_case_ids.append(
            playback.manifest["run_configuration"]["fixed_plan"]["case_id"]
        )
    assert loaded_identities == [
        case.bundle_identity_sha256 for case in plan.cases
    ]
    assert _sequence_identity(loaded_case_ids) == (
        HISTORICAL_CASE_SEQUENCE_IDENTITY_SHA256
    )


def test_committed_catalog_ignores_mutable_live_source_authorities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committed_before = build_committed_playback_plan()

    monkeypatch.setattr(
        live_fixed_plan,
        "_source_compatibility",
        lambda system_id: {"mutated_live_source": system_id},
    )
    monkeypatch.setattr(
        live_fixed_plan,
        "_fixture_assets",
        lambda _system_id: [],
    )
    mutated_generation_plan = live_fixed_plan.build_fixed_sweep_plan()
    assert mutated_generation_plan.plan_identity_sha256 != (
        committed_before.plan_identity_sha256
    )

    committed_after = build_committed_playback_plan()
    assert committed_after == committed_before
    status = inspect_committed_playback_catalog(
        PRECOMPUTED_ROOT, plan=committed_after
    )
    assert status.valid_count == 24

    proposed = next(
        case
        for case in committed_after.cases
        if case.role is CommittedPlaybackRole.PROPOSED
    )
    playback = load_compact_bundle(
        proposed.output_path(PRECOMPUTED_ROOT),
        **proposed.bundle_validation_expectations(
            committed_after.plan_identity_sha256
        ),
    )
    assert playback.manifest["bundle_identity_sha256"] == (
        proposed.bundle_identity_sha256
    )


def test_committed_viewer_asset_resolves_by_identity_not_historical_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = PrecomputedCatalog(
        PRECOMPUTED_ROOT,
        repository_root=REPOSITORY_ROOT,
    )
    record = catalog.load(
        {
            "system": PROPOSED_SYSTEM_ID,
            "room_length_ft": 10.0,
            "room_width_ft": 10.0,
            "aisle_mode": False,
            "target_ppfd": 250.0,
        }
    )
    reference = record.playback.canonical.manifest["catalog_assets"][0]
    viewer_path = str(reference["materialized_path"]).removeprefix(
        "plant-layout-viewer/"
    )

    def reject_historical_path(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("historical resource path resolver was used")

    monkeypatch.setattr(web_precomputed, "_resource_bytes", reject_historical_path)
    artifact = record.viewer_artifact(viewer_path)
    assert len(artifact.data) == reference["byte_size"]
    assert hashlib.sha256(artifact.data).hexdigest() == reference["sha256"]


def test_committed_contract_rejects_copied_tampered_and_substituted_bundles(
    tmp_path: Path,
) -> None:
    plan = build_committed_playback_plan()
    expected = plan.cases[0]
    other = plan.cases[1]
    expectations = expected.bundle_validation_expectations(
        plan.plan_identity_sha256
    )

    tampered = tmp_path / "tampered.fspm-compact"
    shutil.copyfile(expected.output_path(PRECOMPUTED_ROOT), tampered)
    data = bytearray(tampered.read_bytes())
    data[len(data) // 2] ^= 1
    tampered.write_bytes(data)
    assert not validate_compact_bundle(tampered, **expectations).valid

    substituted = tmp_path / "substituted.fspm-compact"
    shutil.copyfile(other.output_path(PRECOMPUTED_ROOT), substituted)
    validation = validate_compact_bundle(substituted, **expectations)
    assert validation.status is CompactBundleStatus.IDENTITY_MISMATCH
