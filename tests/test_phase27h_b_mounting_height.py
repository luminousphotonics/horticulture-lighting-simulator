from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from fspm_optics.application.domain import (
    CONVENTIONAL_SYSTEM_ID,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    ConventionalRunRequest,
    HpsRunRequest,
    ProposedRunRequest,
    RequestValidationError,
)
from fspm_optics.application.proposed import _layout_identity
from fspm_optics.application.source_state import PhysicalSourceState, hash_json
from fspm_optics.fixtures.conventional_led.layout import (
    MountReferencePlaneSemantics,
    plan_conventional_layout_from_feet,
)
from fspm_optics.fixtures.hps.layout import plan_hps_layout_from_feet
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.geometry.mounting import (
    INCH_TO_METERS,
    MOUNTING_HEIGHT_DEFINITION,
    MountingGeometry,
)
from fspm_optics.geometry.room import DEFAULT_ROOM_HEIGHT_M
from fspm_optics.geometry.sensor_grid import BASELINE_REFERENCE_PLANE_Z_M
from fspm_optics.viewer.fixtures import build_fixture_publication

RUN_ID = "7" * 32


def _common(system: str) -> dict[str, object]:
    return {
        "system": system,
        "room_length_ft": 10.0,
        "room_width_ft": 10.0,
        "quality": "direct",
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 20.0,
    }


def test_family_defaults_are_canonical_inches_and_hps_remains_targetless() -> None:
    proposed = ProposedRunRequest.from_payload(
        _common(PROPOSED_SYSTEM_ID) | {"target_ppfd": 500.0}
    )
    conventional = ConventionalRunRequest.from_payload(
        _common(CONVENTIONAL_SYSTEM_ID) | {"target_ppfd": 500.0}
    )
    hps = HpsRunRequest.from_payload(_common(HPS_SYSTEM_ID))

    assert [
        proposed.mounting_height_in,
        conventional.mounting_height_in,
        hps.mounting_height_in,
    ] == [18.0, 18.0, 24.0]
    assert proposed.to_dict()["mounting_height_in"] == 18.0
    assert conventional.to_dict()["mounting_height_in"] == 18.0
    assert hps.to_dict()["mounting_height_in"] == 24.0
    controller = (
        Path(__file__).resolve().parents[1]
        / "src/fspm_optics/resources/viewer/fixture-height-controller.js"
    ).read_text(encoding="utf-8")
    assert "simulatedHeightIn = finiteNumber(mounting.mounting_height_in)" in controller
    assert "applyIndex(contract.simulatedIndex, false)" in controller
    assert PROPOSED_SYSTEM_ID not in controller
    assert CONVENTIONAL_SYSTEM_ID not in controller
    assert HPS_SYSTEM_ID not in controller
    assert "target_ppfd_umol_m2_s" not in hps.to_dict()
    with pytest.raises(RequestValidationError):
        HpsRunRequest.from_payload(
            _common(HPS_SYSTEM_ID) | {"target_ppfd": 500.0}
        )


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "",
        "18",
        [],
        {},
        float("nan"),
        float("inf"),
        0.0,
        -1.0,
        120.0,
    ],
)
def test_mounting_height_validation_fails_closed(value: object) -> None:
    with pytest.raises(RequestValidationError) as caught:
        ProposedRunRequest.from_payload(
            _common(PROPOSED_SYSTEM_ID)
            | {"target_ppfd": 500.0, "mounting_height_in": value}
        )
    assert caught.value.field == "mounting_height_in"


def test_request_rejects_a_second_user_supplied_meter_height() -> None:
    with pytest.raises(RequestValidationError) as caught:
        ProposedRunRequest.from_payload(
            _common(PROPOSED_SYSTEM_ID)
            | {
                "target_ppfd": 500.0,
                "mounting_height_in": 18.0,
                "mounting_height_m": 0.4572,
            }
        )
    assert caught.value.field == "request"


def test_exact_conversion_plane_semantics_and_ceiling_boundary() -> None:
    led = MountingGeometry.resolve(18.0)
    hps = MountingGeometry.resolve(24.0)

    assert INCH_TO_METERS == 0.0254
    assert led.reference_plane_z_m == BASELINE_REFERENCE_PLANE_Z_M == 0.005
    assert led.mounting_height_m == 0.4572
    assert led.emitting_aperture_plane_z_m == 0.4622
    assert hps.mounting_height_m == 0.6096
    assert hps.emitting_aperture_plane_z_m == 0.6146
    assert led.to_payload() == {
        "schema_id": "fspm-optics.mounting-height-provenance",
        "schema_version": 1,
        "mounting_height_in": 18.0,
        "input_unit": "inch",
        "meters_per_inch": 0.0254,
        "definition": MOUNTING_HEIGHT_DEFINITION,
        "reference_plane_z_m": 0.005,
        "mounting_height_m": 0.4572,
        "emitting_aperture_plane_z_m": 0.4622,
        "room_ceiling_z_m": DEFAULT_ROOM_HEIGHT_M,
    }
    reaching_or_exceeding_ceiling_in = (
        DEFAULT_ROOM_HEIGHT_M - BASELINE_REFERENCE_PLANE_Z_M
    ) / INCH_TO_METERS + 1.0e-9
    with pytest.raises(ValueError, match="below the fixed room ceiling"):
        MountingGeometry.resolve(reaching_or_exceeding_ceiling_in)


@pytest.mark.parametrize(
    ("system", "height_in", "expected_aperture_z_m"),
    [
        (PROPOSED_SYSTEM_ID, 18.0, 0.4622),
        (CONVENTIONAL_SYSTEM_ID, 18.0, 0.4622),
        (HPS_SYSTEM_ID, 24.0, 0.6146),
    ],
)
def test_layouts_and_viewer_matrices_share_simulated_mounting_geometry(
    system: str,
    height_in: float,
    expected_aperture_z_m: float,
) -> None:
    mounting = MountingGeometry.resolve(height_in)
    if system == PROPOSED_SYSTEM_ID:
        layout = generate_proposed_led_layout(
            10.0,
            10.0,
            mount_z_m=mounting.emitting_aperture_plane_z_m,
        )
        layout_identity = _layout_identity(layout)
        assert {module.z_m for module in layout.modules} == {
            expected_aperture_z_m
        }
    elif system == CONVENTIONAL_SYSTEM_ID:
        layout = plan_conventional_layout_from_feet(
            10.0,
            10.0,
            mount=MountReferencePlaneSemantics(
                reference_plane_z_m=mounting.reference_plane_z_m,
                mount_height_m=mounting.mounting_height_m,
            ),
        )
        layout_identity = layout.to_payload()
        assert {fixture.aperture_center_m.z_m for fixture in layout.fixtures} == {
            expected_aperture_z_m
        }
    else:
        layout = plan_hps_layout_from_feet(
            10.0,
            10.0,
            reference_plane_z_m=mounting.reference_plane_z_m,
            mount_height_m=mounting.mounting_height_m,
        )
        layout_identity = layout.to_payload()
        assert {fixture.aperture_z_m for fixture in layout.fixtures} == {
            expected_aperture_z_m
        }

    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id=system,
        requested_length_ft=10.0,
        requested_width_ft=10.0,
        layout_identity=layout_identity,
        mounting_height=mounting.to_payload(),
    )
    catalog = json.loads(publication.catalog.data)
    provenance_sha256 = hashlib.sha256(
        json.dumps(
            mounting.to_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()

    assert catalog["schema_version"] == 2
    assert catalog["mounting_height"] == mounting.to_payload()
    assert catalog["fixture_plan"]["mounting_height_sha256"] == provenance_sha256
    assert all(
        record["scientific_translation_m"]["z"]
        == pytest.approx(expected_aperture_z_m, abs=1.0e-12)
        for record in catalog["fixture_plan"]["fixtures"]
    )


def test_request_layout_and_source_identities_change_with_mounting_height() -> None:
    request_18 = ProposedRunRequest.from_payload(
        _common(PROPOSED_SYSTEM_ID)
        | {"target_ppfd": 500.0, "mounting_height_in": 18.0}
    )
    request_20 = ProposedRunRequest.from_payload(
        _common(PROPOSED_SYSTEM_ID)
        | {"target_ppfd": 500.0, "mounting_height_in": 20.0}
    )
    layout_18 = _layout_identity(
        generate_proposed_led_layout(
            10.0,
            10.0,
            mount_z_m=request_18.mounting_geometry.emitting_aperture_plane_z_m,
        )
    )
    layout_20 = _layout_identity(
        generate_proposed_led_layout(
            10.0,
            10.0,
            mount_z_m=request_20.mounting_geometry.emitting_aperture_plane_z_m,
        )
    )
    shared = {
        "full_output_schedule": {"policy": "test"},
        "operating_point": {"power_w": 1.0},
    }
    source_18 = PhysicalSourceState.create(
        system_id=PROPOSED_SYSTEM_ID,
        layout_identity=layout_18,
        source_operation={
            "policy_id": "test",
            "mounting_height": request_18.mounting_geometry.to_payload(),
        },
        **shared,
    )
    source_20 = PhysicalSourceState.create(
        system_id=PROPOSED_SYSTEM_ID,
        layout_identity=layout_20,
        source_operation={
            "policy_id": "test",
            "mounting_height": request_20.mounting_geometry.to_payload(),
        },
        **shared,
    )

    assert request_18 != request_20
    assert hash_json(request_18.to_dict()) != hash_json(request_20.to_dict())
    assert hash_json(layout_18) != hash_json(layout_20)
    assert source_18.source_state_id != source_20.source_state_id


def test_legacy_fixture_catalog_schema_remains_valid_without_new_provenance() -> None:
    layout = plan_hps_layout_from_feet(10.0, 10.0).to_payload()
    publication = build_fixture_publication(
        run_id=RUN_ID,
        system_id=HPS_SYSTEM_ID,
        requested_length_ft=10.0,
        requested_width_ft=10.0,
        layout_identity=layout,
    )
    catalog = json.loads(publication.catalog.data)

    assert catalog["schema_version"] == 1
    assert "mounting_height" not in catalog
    assert "mounting_height_sha256" not in catalog["fixture_plan"]
