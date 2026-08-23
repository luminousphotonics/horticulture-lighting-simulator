"""Shared non-emitting external fixture-structure material policy."""

from typing import Final


FIXTURE_BODY_MATERIAL_ID: Final = "fixture_body_anodized_aluminum_v2"
FIXTURE_BODY_MATERIAL_NAME: Final = "fixture_body_anodized_aluminum"
FIXTURE_BODY_MATERIAL_RAD: Final = (
    "# Shared project reference for generic anodized extruded aluminum.\n"
    "# Identical for Proposed and Conventional; not a product-specific material claim.\n"
    "void metal fixture_body_anodized_aluminum\n"
    "0\n"
    "0\n"
    "5 0.70 0.70 0.70 0.90 0.10\n"
)


__all__ = [
    "FIXTURE_BODY_MATERIAL_ID",
    "FIXTURE_BODY_MATERIAL_NAME",
    "FIXTURE_BODY_MATERIAL_RAD",
]
