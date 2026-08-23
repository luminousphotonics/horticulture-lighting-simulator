from __future__ import annotations

import pytest

from fspm_optics.transport.basis.manifest import (
    ISOLATED_RTRACE_BACKEND,
    BasisManifest,
    legacy_manifest_control_zone_count_matches,
)
from fspm_optics.transport.scalar_ppfd import BASELINE_PPFD_RGB_DECODE_METHOD


def make_manifest(**overrides: object) -> BasisManifest:
    values: dict[str, object] = {
        "room_length_m": 3.048,
        "room_width_m": 3.048,
        "room_height_m": 3.048,
        "sensor_count": 441,
        "control_zone_count": 5,
        "matrix_shape": (441, 5),
        "radiance_options": ("-ab", "3", "-aa", "0.22"),
        "nthreads": 6,
        "reference_watts": 1.0,
        "layout_module_count": 61,
        "module_profile_id": "145led_clear_lid_ptfe_stack_v2",
        "ambient_cache_policy": "per_control_zone",
    }
    values.update(overrides)
    return BasisManifest(**values)  # type: ignore[arg-type]


def test_manifest_records_fixed_trace_contract_and_legacy_aliases() -> None:
    manifest = make_manifest()
    assert manifest.backend == ISOLATED_RTRACE_BACKEND
    assert manifest.scalar_ppfd_decode_policy == BASELINE_PPFD_RGB_DECODE_METHOD
    assert manifest.matrix_shape == (441, 5)
    assert manifest.n_vars == manifest.n_rings == manifest.control_zone_count == 5
    assert manifest.emitter_source_sha256 is None
    assert manifest.emitter_metadata_sha256 is None


def test_manifest_rejects_shape_that_is_not_sensors_by_control_zones() -> None:
    with pytest.raises(ValueError, match="sensors x control_zones"):
        make_manifest(matrix_shape=(5, 441))


def test_manifest_backend_is_fixed_to_isolated_trace() -> None:
    with pytest.raises(ValueError, match="fixed"):
        make_manifest(backend="other")


def test_legacy_ten_by_ten_counts_conceptually_match_five_control_zones() -> None:
    manifest = make_manifest()
    legacy_summary = {"n_vars": 5, "n_rings": 5, "layout_modules": 61}
    assert legacy_manifest_control_zone_count_matches(manifest, legacy_summary)
    assert not legacy_manifest_control_zone_count_matches(
        manifest, {"n_vars": 6, "n_rings": 5}
    )


def test_manifest_dict_round_trip_preserves_options_and_hash_placeholders() -> None:
    manifest = make_manifest(
        emitter_source_sha256="source-placeholder",
        emitter_metadata_sha256="metadata-placeholder",
    )
    assert BasisManifest.from_dict(manifest.to_dict()) == manifest
    assert manifest.to_dict()["radiance"]["options"] == ["-ab", "3", "-aa", "0.22"]  # type: ignore[index]
    assert manifest.to_dict()["layout_composition"] == {
        "proposed_layout_mode": "standalone_modules",
        "proposed_ring_mode": "full",
        "module_pattern_id": "centered_square_full_v1",
        "fixture_policy_id": (
            "proposed_standalone_module_instances_with_alignment_lattice_v2"
        ),
        "mechanical_envelope": {
            "id": "standalone_module_150x150mm_v1",
            "width_x_m": 0.15,
            "height_y_m": 0.15,
        },
        "fixture_asset_set_id": "proposed-led-module-v1",
    }


@pytest.mark.parametrize("missing", ("mechanical_envelope", "fixture_asset_set_id"))
def test_standalone_manifest_rejects_missing_composition_identity(
    missing: str,
) -> None:
    payload = make_manifest().to_dict()
    payload["layout_composition"].pop(missing)
    with pytest.raises(ValueError, match="explicitly authenticate"):
        BasisManifest.from_dict(payload)


def test_pre_room_authority_manifest_is_rejected_even_for_historical_layout() -> None:
    payload = make_manifest().to_dict()
    payload["schema_version"] = 1
    payload.pop("layout_composition")

    with pytest.raises(ValueError, match="pre-room-authority"):
        BasisManifest.from_dict(payload)


def test_manifest_rejects_changed_room_authority_identity() -> None:
    payload = make_manifest().to_dict()
    payload["room_model"]["identity_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="room model"):
        BasisManifest.from_dict(payload)
