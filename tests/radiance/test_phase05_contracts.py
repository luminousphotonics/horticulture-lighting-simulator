from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance import config  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest, request_with_updates  # noqa: E402
from rad_rebuild.radiance.backend.server import app  # noqa: E402
from rad_rebuild.radiance.domain import (  # noqa: E402
    PrecomputedMode,
    canonicalize_precomputed_mode,
)
from rad_rebuild.radiance.engine.simulation import basis_backends  # noqa: E402
from rad_rebuild.radiance.engine.simulation.precomputed_dataset import (  # noqa: E402
    canonical_competitor_layout,
    canonical_mode,
)
from rad_rebuild.radiance.settings import load_settings  # noqa: E402


class Phase05DomainContractTests(unittest.TestCase):
    def test_unknown_quality_presets_fail_instead_of_becoming_standard(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "Unsupported simulation quality preset"
        ):
            config.canonicalize_quality_preset("surprise")
        with self.assertRaises(ValidationError):
            RadianceRunRequest(action="all", sim_mode="surprise")
        with self.assertRaisesRegex(
            ValueError, "Unsupported simulation quality preset"
        ):
            basis_backends.rtrace_mode_preset("surprise")

    def test_unknown_mode_layout_overlay_and_precomputed_values_fail(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported mode"):
            canonical_mode("mystery-system")
        with self.assertRaisesRegex(ValueError, "Unsupported layout mode"):
            canonical_competitor_layout("dense")
        with self.assertRaises(ValidationError):
            RadianceRunRequest(action="all", overlay="mystery-overlay")
        with self.assertRaisesRegex(ValueError, "Unsupported precomputed mode"):
            canonicalize_precomputed_mode("sometimes")

    def test_request_records_are_immutable_and_copy_with_updates_revalidates(
        self,
    ) -> None:
        req = RadianceRunRequest(action="all", mode="SMD", length_ft=10, width_ft=10)
        with self.assertRaises(ValidationError):
            req.mode = "Competitor"  # type: ignore[misc]

        updated = request_with_updates(req, mode="Competitor")
        self.assertEqual(req.mode, config.MODE_SMD)
        self.assertEqual(updated.mode, config.MODE_COMPETITOR)

        with self.assertRaises(ValidationError):
            request_with_updates(req, w_min=100, w_max=10)

    def test_request_serialization_round_trips_with_canonical_values(self) -> None:
        req = RadianceRunRequest(
            action="all",
            mode="proposed led",
            execution_mode="demo",
            sim_mode="fast",
            length_ft=10,
            width_ft=12,
        )
        self.assertEqual(req.mode, config.MODE_SMD)
        self.assertEqual(req.execution_mode, config.EXECUTION_MODE_PRECOMPUTED)
        self.assertEqual(req.sim_mode, config.QUALITY_PRESET_STANDARD)

        serialized = req.model_dump_json()
        round_trip = RadianceRunRequest.model_validate_json(serialized)
        self.assertEqual(round_trip, req)

    def test_extra_fields_are_rejected_at_external_boundary(self) -> None:
        with self.assertRaises(ValidationError):
            RadianceRunRequest.model_validate(
                {"action": "all", "length_ft": 10, "width_ft": 10, "surprise": True}
            )

    def test_settings_parse_environment_into_typed_values(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_phase05_settings_") as tmp:
            settings = load_settings(
                {
                    "RADIANCE_OUTPUT_ROOT": tmp,
                    "RADIANCE_PORT": "9000",
                    "RADIANCE_ENABLE_LIVE_EXECUTION": "true",
                    "RADIANCE_PRECOMPUTED_MODE": "only",
                    "RADIANCE_CORS_ALLOW_ORIGINS": "https://example.test,*",
                }
            )

        self.assertEqual(settings.paths.output_root, Path(tmp).resolve())
        self.assertEqual(settings.backend_port, 9000)
        self.assertTrue(settings.live_execution_enabled)
        self.assertEqual(settings.precomputed_mode, PrecomputedMode.ONLY)
        self.assertEqual(settings.cors_allow_origins, ("https://example.test",))

    def test_invalid_settings_fail_during_load(self) -> None:
        with self.assertRaisesRegex(ValueError, "RADIANCE_PORT must be an integer"):
            load_settings({"RADIANCE_PORT": "not-a-port"})
        with self.assertRaisesRegex(ValueError, "Unsupported precomputed mode"):
            load_settings({"RADIANCE_PRECOMPUTED_MODE": "sometimes"})

    def test_openapi_schema_snapshot_exposes_canonical_enums_and_forbids_extra_fields(
        self,
    ) -> None:
        schemas = app.openapi()["components"]["schemas"]
        properties = schemas["RadianceRunRequest"]["properties"]
        self.assertEqual(properties["mode"]["enum"], ["SMD", "Competitor", "1000W HPS"])
        self.assertEqual(
            properties["execution_mode"]["enum"],
            ["precomputed", "live_docker", "live_local"],
        )
        self.assertEqual(
            properties["sim_mode"]["enum"],
            ["direct", "standard", "quality", "rigorous"],
        )
        self.assertEqual(
            properties["basis_backend"]["enum"],
            ["rtrace", "rcontrib_legacy", "rcontrib_mcpt"],
        )
        self.assertEqual(
            properties["overlay"]["enum"], ["auto", "smd", "spydr3", "hps"]
        )
        self.assertEqual(
            properties["action"]["enum"],
            ["uniformity", "competitor", "visualize", "all", "metrics"],
        )
        self.assertFalse(schemas["RadianceRunRequest"]["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
