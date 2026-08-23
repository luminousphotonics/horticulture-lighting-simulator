"""Current-production compatibility checks for persisted Proposed bases."""

from __future__ import annotations

import math

from fspm_optics.fixtures.proposed_cob.source import (
    COB_LES_DIAMETER_M,
    COB_SOURCE_MODE,
    resolve_proposed_source_authority,
)
from fspm_optics.fixtures.smd.radiance_writer import (
    current_proposed_source_identity_payload,
    current_proposed_source_identity_sha256,
)

from .manifest import BasisManifest


class CurrentSourceCompatibilityError(ValueError):
    """A historically valid basis is not reusable by current production."""


def require_current_proposed_source_manifest(
    manifest: BasisManifest,
) -> None:
    """Reject persisted bases that predate the forward-flux authority."""

    payload = current_proposed_source_identity_payload(
        manifest.proposed_source_mode
    )
    authority = resolve_proposed_source_authority(
        manifest.proposed_source_mode
    )
    law = authority.angular_law
    expected = {
        "proposed_source_identity_sha256": (
            current_proposed_source_identity_sha256(
                manifest.proposed_source_mode
            )
        ),
        "source_classification": payload["classification"],
        "emitter_shape": payload["emitter_shape"],
        "emitter_area_per_module_m2": (
            payload["emitter_area_per_module_m2"]
        ),
        "emitter_diameter_m": (
            COB_LES_DIAMETER_M
            if manifest.proposed_source_mode == COB_SOURCE_MODE
            else None
        ),
        "authenticated_ies_sha256": payload["authenticated_ies_sha256"],
        "normalized_angular_identity_sha256": (
            payload["normalized_angular_identity_sha256"]
        ),
        "angular_data_sha256": None if law is None else law.dat_sha256,
        "angular_normalization_policy": (
            payload["angular_normalization_policy"]
        ),
        "completed_aperture_characterization_identity_sha256": (
            payload[
                "completed_aperture_characterization_identity_sha256"
            ]
        ),
        "controlled_spd_identity_sha256": (
            payload["controlled_spd_identity_sha256"]
        ),
    }
    mismatches = [
        name
        for name, expected_value in expected.items()
        if not _equal(getattr(manifest, name), expected_value)
    ]
    if mismatches:
        raise CurrentSourceCompatibilityError(
            "basis manifest is historically authentic but incompatible with "
            "the current deterministic forward-flux source authority; "
            f"mismatched fields: {', '.join(mismatches)}."
        )


def _equal(actual: object, expected: object) -> bool:
    if isinstance(actual, float) and isinstance(expected, float):
        return math.isclose(
            actual,
            expected,
            rel_tol=0.0,
            abs_tol=1.0e-15,
        )
    return actual == expected
