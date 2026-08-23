"""Shared versioned identities for fixed-plan compact publication."""

from __future__ import annotations

from typing import Final

from fspm_optics.geometry.coordinate_frame import (
    ROOM_FRAME_POLICY_ID,
    RoomCoordinateFrame,
)


FIXED_CASE_BINDING_SCHEMA_ID: Final = (
    "fspm-optics.fixed-precomputed-sweep-case-binding"
)
FIXED_CASE_BINDING_SCHEMA_VERSION: Final = 1
CANONICAL_DOMAIN_SCHEMA_ID: Final = "fspm-optics.canonical-room-domain"
CANONICAL_DOMAIN_SCHEMA_VERSION: Final = 1
FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S: Final = 250.0
FIXED_OUTPUT_TARGET_SEMANTICS: Final = (
    "fixed_output_comparison_reference_no_dimming"
)
MEAN_TARGET_SEMANTICS: Final = "requested_mean_target"
PHYSICAL_ROOM_PAIRS_FT: Final = ((10, 10), (15, 30), (30, 50))


def canonical_room_domain(
    requested_length_ft: int | float,
    requested_width_ft: int | float,
) -> dict[str, object]:
    """Return the shared fixed-catalog domain for one supported room pair."""

    length = float(requested_length_ft)
    width = float(requested_width_ft)
    unordered = tuple(sorted((length, width)))
    expected = {tuple(sorted(map(float, pair))) for pair in PHYSICAL_ROOM_PAIRS_FT}
    if unordered not in expected:
        raise ValueError("room dimensions are outside the fixed production matrix.")
    frame = RoomCoordinateFrame(length * 0.3048, width * 0.3048)
    long_ft = max(length, width)
    short_ft = min(length, width)
    supported = (
        ((long_ft, short_ft),)
        if long_ft == short_ft
        else ((short_ft, long_ft), (long_ft, short_ft))
    )
    return {
        "schema_id": CANONICAL_DOMAIN_SCHEMA_ID,
        "schema_version": CANONICAL_DOMAIN_SCHEMA_VERSION,
        "coordinate_frame_policy_id": ROOM_FRAME_POLICY_ID,
        "canonical_aligned_room": {
            "length_x_ft": long_ft,
            "width_y_ft": short_ft,
            "length_x_m": frame.simulation_length_m,
            "width_y_m": frame.simulation_width_m,
        },
        "physical_room_label_ft": {
            "length": min(length, width),
            "width": max(length, width),
        },
        "supported_requested_room_orders_ft": [
            {"length": item[0], "width": item[1]} for item in supported
        ],
        "mapping": {
            "square_rotation_degrees_about_z": 0,
            "portrait_requested_to_simulation_rotation_degrees_about_z": -90,
            "simulation_to_portrait_requested_rotation_degrees_about_z": 90,
            "determinant": 1,
            "translation_m": [0.0, 0.0, 0.0],
            "reflection": False,
            "scaling": False,
        },
    }


__all__ = [
    "CANONICAL_DOMAIN_SCHEMA_ID",
    "CANONICAL_DOMAIN_SCHEMA_VERSION",
    "FIXED_CASE_BINDING_SCHEMA_ID",
    "FIXED_CASE_BINDING_SCHEMA_VERSION",
    "FIXED_OUTPUT_TARGET_SEMANTICS",
    "FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S",
    "MEAN_TARGET_SEMANTICS",
    "PHYSICAL_ROOM_PAIRS_FT",
    "canonical_room_domain",
]
