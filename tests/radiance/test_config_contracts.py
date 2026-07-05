from __future__ import annotations

import unittest

from rad_rebuild.radiance import config
from rad_rebuild.radiance.settings import load_settings


class RadianceConfigContractTests(unittest.TestCase):
    def test_active_modes_are_current_public_modes(self) -> None:
        self.assertEqual(
            config.ACTIVE_RADIANCE_MODES,
            (config.MODE_SMD, config.MODE_COMPETITOR, config.MODE_HPS),
        )
        self.assertEqual(
            config.RADIANCE_MODE_LABELS[config.MODE_COMPETITOR],
            "Conventional LED System",
        )

    def test_accepted_modes_are_current_public_modes(self) -> None:
        self.assertEqual(config.ACCEPTED_RADIANCE_MODES, config.ACTIVE_RADIANCE_MODES)
        self.assertNotIn("MODE_COB", vars(config))
        with self.assertRaisesRegex(ValueError, "Unsupported mode"):
            config.canonicalize_radiance_mode("COB")

    def test_quantum_board_is_not_active_or_accepted(self) -> None:
        self.assertNotIn("Quantum Board", config.ACTIVE_RADIANCE_MODES)
        self.assertNotIn("MODE_QB", vars(config))
        with self.assertRaisesRegex(ValueError, "Unsupported mode"):
            config.canonicalize_radiance_mode("Quantum Board")

    def test_precomputed_execution_mode_remains_available(self) -> None:
        self.assertIn(config.EXECUTION_MODE_PRECOMPUTED, config.VALID_EXECUTION_MODES)
        self.assertEqual(
            config.DEFAULT_EXECUTION_MODE, config.EXECUTION_MODE_PRECOMPUTED
        )

    def test_public_execution_options_hide_live_modes(self) -> None:
        self.assertEqual(
            tuple(option.value for option in config.PUBLIC_EXECUTION_MODE_OPTIONS),
            (config.EXECUTION_MODE_PRECOMPUTED,),
        )

    def test_public_precomputed_dimension_contract(self) -> None:
        self.assertEqual(config.PUBLIC_PRECOMPUTED_MIN_FT, 10)
        self.assertEqual(config.PUBLIC_PRECOMPUTED_MAX_FT, 20)
        self.assertEqual(config.PUBLIC_DEFAULT_LENGTH_FT, 10)
        self.assertEqual(config.PUBLIC_DEFAULT_WIDTH_FT, 10)

    def test_execution_mode_canonicalization_accepts_valid_aliases(self) -> None:
        cases = {
            "precomputed": config.EXECUTION_MODE_PRECOMPUTED,
            "demo": config.EXECUTION_MODE_PRECOMPUTED,
            "live_docker": config.EXECUTION_MODE_LIVE_DOCKER,
            "docker": config.EXECUTION_MODE_LIVE_DOCKER,
            "live_local": config.EXECUTION_MODE_LIVE_LOCAL,
            "local": config.EXECUTION_MODE_LIVE_LOCAL,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(config.canonicalize_execution_mode(raw), expected)

    def test_invalid_execution_mode_raises_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported execution_mode"):
            config.canonicalize_execution_mode("definitely_not_a_mode")

    def test_docker_default_image_is_stable(self) -> None:
        self.assertEqual(config.DEFAULT_DOCKER_IMAGE, "rad-rebuild-radiance:local")

    def test_local_live_job_timeout_defaults_to_existing_job_timeout(self) -> None:
        settings = load_settings({})

        self.assertEqual(settings.job_timeout_s, 900.0)
        self.assertEqual(settings.local_live_job_timeout_s, 900.0)

    def test_local_live_job_timeout_follows_configured_job_timeout_by_default(self) -> None:
        settings = load_settings({"RADIANCE_JOB_TIMEOUT_S": "123"})

        self.assertEqual(settings.job_timeout_s, 123.0)
        self.assertEqual(settings.local_live_job_timeout_s, 123.0)

    def test_local_live_job_timeout_accepts_override_and_disable_values(self) -> None:
        self.assertEqual(
            load_settings({"RAD_REBUILD_LIVE_JOB_TIMEOUT_SECONDS": "1800"}).local_live_job_timeout_s,
            1800.0,
        )
        self.assertIsNone(
            load_settings({"RAD_REBUILD_LIVE_JOB_TIMEOUT_SECONDS": "0"}).local_live_job_timeout_s
        )
        self.assertIsNone(
            load_settings({"RAD_REBUILD_LIVE_JOB_TIMEOUT_SECONDS": "none"}).local_live_job_timeout_s
        )

    def test_local_live_job_timeout_rejects_invalid_values(self) -> None:
        for raw in ("-1", "1.5", "not-a-timeout"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(
                    ValueError,
                    "RAD_REBUILD_LIVE_JOB_TIMEOUT_SECONDS must be a positive integer",
                ):
                    load_settings({"RAD_REBUILD_LIVE_JOB_TIMEOUT_SECONDS": raw})


if __name__ == "__main__":
    unittest.main()
