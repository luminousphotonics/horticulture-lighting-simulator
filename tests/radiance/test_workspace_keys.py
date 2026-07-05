from __future__ import annotations

import math
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi import HTTPException
from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.config import (  # noqa: E402
    EXECUTION_MODE_LIVE_DOCKER,
    MODE_COMPETITOR,
    MODE_HPS,
    MODE_SMD,
)
from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.backend.workspace import (  # noqa: E402
    LEGACY_WORKSPACE_KEY_POLICY,
    PLANT_REQUEST_FINGERPRINT_FIELDS,
    REQUEST_FINGERPRINT_FIELDS,
    artifact_key_from_request,
    canonical_request_fingerprint_payload,
    canonical_request_fingerprint_json,
    legacy_artifact_key_from_request,
    request_fingerprint,
    _workspace_key,
    _workspace_root_for_request,
)
from rad_rebuild.radiance.engine.simulation.basis_backends import (  # noqa: E402
    DEFAULT_SMD_BASIS_BACKEND,
    BASIS_BACKEND_RCONTRIB_LEGACY,
)


class RadianceWorkspaceKeyTests(unittest.TestCase):
    def _base_request(self, **overrides: object) -> RadianceRunRequest:
        data: dict[str, Any] = {
            "action": "all",
            "mode": MODE_SMD,
            "length_ft": 10.0,
            "width_ft": 12.0,
            "target_ppfd": 1000.0,
            "peak_capping_enabled": False,
            "sim_mode": "standard",
            "execution_mode": EXECUTION_MODE_LIVE_DOCKER,
            "mount_z_m": 0.4572,
            "basis_backend": DEFAULT_SMD_BASIS_BACKEND,
        }
        data.update(overrides)
        return RadianceRunRequest(**data)

    def _unchecked_request(self, **overrides: object) -> RadianceRunRequest:
        data = self._base_request().model_dump()
        data.update(overrides)
        return RadianceRunRequest.model_construct(**data)

    def test_smd_workspace_key_is_stable_for_representative_request(self) -> None:
        key = _workspace_key(
            MODE_SMD,
            length_ft=10,
            width_ft=12,
            target_ppfd=1000,
            peak_capping_enabled=False,
            hps_coverage_ft=4,
            sim_mode="standard",
            execution_mode=EXECUTION_MODE_LIVE_DOCKER,
            mount_z_m=0.4572,
            basis_backend=DEFAULT_SMD_BASIS_BACKEND,
        )
        self.assertRegex(key, r"^fp1_[0-9a-f]{64}$")
        self.assertNotEqual(
            key, "smd_12x10_t1000_cap0_mh18_bbrtrace_execlive_docker_qstandard"
        )

    def test_competitor_workspace_key_is_stable_for_conventional_led(self) -> None:
        key = _workspace_key(
            MODE_COMPETITOR,
            length_ft=10,
            width_ft=12,
            target_ppfd=1000,
            peak_capping_enabled=False,
            hps_coverage_ft=4,
            competitor_layout="practical",
            sim_mode="quality",
            execution_mode=EXECUTION_MODE_LIVE_DOCKER,
            sp_z_m=0.6096,
        )
        self.assertRegex(key, r"^fp1_[0-9a-f]{64}$")
        self.assertNotIn("_cap0_mh24_ledpractical_", key)

    def test_hps_workspace_key_includes_ies_variant(self) -> None:
        key = _workspace_key(
            MODE_HPS,
            length_ft=10,
            width_ft=12,
            target_ppfd=1000,
            peak_capping_enabled=False,
            hps_coverage_ft=5,
            sim_mode="standard",
            execution_mode=EXECUTION_MODE_LIVE_DOCKER,
            hps_z_m=1.0668,
            hps_ies_variant="karma",
        )
        self.assertRegex(key, r"^fp1_[0-9a-f]{64}$")

    def test_removed_hps_ies_variants_are_rejected(self) -> None:
        base_kwargs: dict[str, Any] = dict(
            mode=MODE_HPS,
            length_ft=10,
            width_ft=12,
            target_ppfd=1000,
            peak_capping_enabled=False,
            hps_coverage_ft=5,
            sim_mode="standard",
            execution_mode=EXECUTION_MODE_LIVE_DOCKER,
            hps_z_m=1.0668,
        )
        for variant in ("og", "hde", "legacy", "wide"):
            with (
                self.subTest(variant=variant),
                self.assertRaisesRegex(ValueError, "Unsupported HPS IES variant"),
            ):
                _workspace_key(hps_ies_variant=variant, **base_kwargs)

    def test_all_output_affecting_fields_participate_in_fingerprint(self) -> None:
        self.assertEqual(
            set(REQUEST_FINGERPRINT_FIELDS),
            {
                "mode",
                "execution_mode",
                "length_ft",
                "width_ft",
                "target_ppfd",
                "peak_capping_enabled",
                "run_basis",
                "w_min",
                "w_max",
                "sim_mode",
                "subpatch_grid",
                "mount_z_m",
                "overlay",
                "smd_base_ring",
                "basis_backend",
                "match_system_ppe",
                "sp_ppf",
                "sp_z_m",
                "sp_ppe",
                "competitor_layout",
                "hps_coverage_ft",
                "hps_z_m",
                "hps_fixture_ppf",
                "hps_input_watts",
                "hps_ies_variant",
                "dialux_sensor_grid",
            },
        )
        base = self._base_request()
        base_fingerprint = request_fingerprint(base)
        changes = {
            "mode": MODE_COMPETITOR,
            "execution_mode": "precomputed",
            "length_ft": 10.1,
            "width_ft": 11.9,
            "target_ppfd": 1000.5,
            "peak_capping_enabled": True,
            "run_basis": False,
            "w_min": 1.25,
            "w_max": 99.5,
            "sim_mode": "quality",
            "subpatch_grid": 2,
            "mount_z_m": 0.5,
            "overlay": "hps",
            "smd_base_ring": 2,
            "basis_backend": BASIS_BACKEND_RCONTRIB_LEGACY,
            "match_system_ppe": True,
            "sp_ppf": 2200.5,
            "sp_z_m": 0.61,
            "sp_ppe": 2.75,
            "competitor_layout": "practical",
            "hps_coverage_ft": 5.0,
            "hps_z_m": 1.1,
            "hps_fixture_ppf": 1700.0,
            "hps_input_watts": 1000.0,
            "dialux_sensor_grid": True,
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                changed = self._base_request(**{field: value})
                self.assertNotEqual(request_fingerprint(changed), base_fingerprint)

    def test_disabled_plant_fields_do_not_change_default_fingerprint(self) -> None:
        base = self._base_request()
        disabled = self._base_request(
            plants_enabled=False,
            plant_seed=99,
            plant_rows=1,
            plant_columns=1,
            plant_spacing_m=0.4,
            plant_height_m=0.2,
            plant_canopy_radius_m=0.21,
            plant_leaf_count=8,
            plant_growth_stage=0.7,
            fspm_target_ppfd_umol_m2_s=320.0,
            fspm_target_tolerance_umol_m2_s=25.0,
        )
        payload = canonical_request_fingerprint_payload(disabled)
        fields = cast(dict[str, object], payload["fields"])
        self.assertIsInstance(fields, dict)

        self.assertEqual(request_fingerprint(disabled), request_fingerprint(base))
        for field in PLANT_REQUEST_FINGERPRINT_FIELDS:
            self.assertNotIn(field, fields)

    def test_enabled_plant_fields_participate_in_fingerprint(self) -> None:
        self.assertEqual(
            set(PLANT_REQUEST_FINGERPRINT_FIELDS),
            {
                "plants_enabled",
                "plant_seed",
                "plant_rows",
                "plant_columns",
                "plant_spacing_m",
                "plant_height_m",
                "plant_canopy_radius_m",
                "plant_leaf_count",
                "plant_growth_stage",
                "fspm_receiver_granularity",
                "fspm_leaf_optical_profile_id",
                "fspm_leaf_radiance_material_mode",
                "fspm_spectral_transport_mode",
                "fspm_target_ppfd_umol_m2_s",
                "fspm_target_tolerance_umol_m2_s",
            },
        )
        base = self._base_request(plants_enabled=True)
        payload = canonical_request_fingerprint_payload(base)
        fields = cast(dict[str, object], payload["fields"])
        self.assertIsInstance(fields, dict)
        for field in PLANT_REQUEST_FINGERPRINT_FIELDS:
            self.assertIn(field, fields)

        base_fingerprint = request_fingerprint(base)
        changes = {
            "plants_enabled": False,
            "plant_seed": 42,
            "plant_rows": 1,
            "plant_columns": 3,
            "plant_spacing_m": 0.4,
            "plant_height_m": 0.2,
            "plant_canopy_radius_m": 0.24,
            "plant_leaf_count": 8,
            "plant_growth_stage": 0.5,
            "fspm_receiver_granularity": "mesh_patch",
            "fspm_leaf_radiance_material_mode": "opaque_occluder",
            "fspm_spectral_transport_mode": "scalar_source_weighted",
            "fspm_target_ppfd_umol_m2_s": 320.0,
            "fspm_target_tolerance_umol_m2_s": 25.0,
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                overrides = {field: value}
                if field != "plants_enabled":
                    overrides["plants_enabled"] = True
                changed = self._base_request(**overrides)
                self.assertNotEqual(request_fingerprint(changed), base_fingerprint)

    def test_fspm_target_fields_validate_as_positive_numbers(self) -> None:
        req = self._base_request(
            plants_enabled=True,
            fspm_target_ppfd_umol_m2_s=275.0,
            fspm_target_tolerance_umol_m2_s=20.0,
        )
        self.assertEqual(req.fspm_target_ppfd_umol_m2_s, 275.0)
        self.assertEqual(req.fspm_target_tolerance_umol_m2_s, 20.0)

        for field in ("fspm_target_ppfd_umol_m2_s", "fspm_target_tolerance_umol_m2_s"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self._base_request(**{field: 0.0})

    def test_equivalent_normalized_requests_share_fingerprint(self) -> None:
        canonical = self._base_request(
            mode="SMD", length_ft=12.0, width_ft=10.0, execution_mode="precomputed"
        )
        equivalent = self._base_request(
            mode="proposed",
            length_ft=10,
            width_ft=12,
            execution_mode="demo",
            sim_mode="fast",
        )
        self.assertEqual(
            request_fingerprint(canonical), request_fingerprint(equivalent)
        )
        self.assertEqual(
            artifact_key_from_request(canonical), artifact_key_from_request(equivalent)
        )

    def test_canonical_fingerprint_json_is_deterministic(self) -> None:
        req = self._base_request()
        payload = {
            "width_ft": req.width_ft,
            "length_ft": req.length_ft,
            "mode": req.mode,
            "target_ppfd": req.target_ppfd,
            "peak_capping_enabled": req.peak_capping_enabled,
            "execution_mode": req.execution_mode,
            "basis_backend": req.basis_backend,
            "mount_z_m": req.mount_z_m,
            "sim_mode": req.sim_mode,
        }
        reversed_payload = dict(reversed(list(payload.items())))
        self.assertEqual(
            canonical_request_fingerprint_json(payload),
            canonical_request_fingerprint_json(reversed_payload),
        )

    def test_non_finite_values_are_rejected_before_workspace_creation(self) -> None:
        req = self._unchecked_request(length_ft=math.nan)
        with self.assertRaises(HTTPException):
            artifact_key_from_request(req)
        with self.assertRaises(HTTPException):
            _workspace_root_for_request("finite-check", req)

    def test_dimension_collision_and_omitted_field_collision_are_impossible(
        self,
    ) -> None:
        req_10_1 = self._base_request(length_ft=10.1, width_ft=10)
        req_10_4 = self._base_request(length_ft=10.4, width_ft=10)
        self.assertNotEqual(
            artifact_key_from_request(req_10_1), artifact_key_from_request(req_10_4)
        )

        base = self._base_request()
        changed = self._base_request(
            run_basis=False, w_min=5, overlay="hps", dialux_sensor_grid=True
        )
        self.assertNotEqual(
            artifact_key_from_request(base), artifact_key_from_request(changed)
        )
        self.assertNotEqual(
            _workspace_root_for_request("shared-session", base),
            _workspace_root_for_request("shared-session", changed),
        )

    def test_legacy_key_policy_is_explicit_and_not_used_for_workspace_root(
        self,
    ) -> None:
        req = self._base_request()
        legacy_key = legacy_artifact_key_from_request(req)
        current_key = artifact_key_from_request(req)
        self.assertEqual(
            LEGACY_WORKSPACE_KEY_POLICY, "explicit-read-only-migration-only"
        )
        self.assertNotEqual(current_key, legacy_key)
        self.assertNotIn(
            legacy_key, str(_workspace_root_for_request("legacy-policy", req))
        )
        self.assertIsInstance(_workspace_root_for_request("legacy-policy", req), Path)


if __name__ == "__main__":
    unittest.main()
