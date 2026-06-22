from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from rad_rebuild.radiance.engine.emitters.smd_generation.config import (
    load_optical_stack_config,
    load_source_config,
    validate_optics_mode,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.module_profile import (
    CHANNEL_COUNTS,
    MODULE_PROFILE_VERSION,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.optical_stack import (
    write_pmma_lid,
    write_stack_cavity,
)


class SmdOpticsContractTests(unittest.TestCase):
    def test_default_smd_optics_mode_is_stack(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_source_config().optics_mode, "stack")

    def test_stack_optics_mode_is_accepted(self) -> None:
        with patch.dict(os.environ, {"OPTICS": "stack"}, clear=True):
            self.assertEqual(validate_optics_mode(os.environ["OPTICS"]), "stack")
            self.assertEqual(load_source_config().optics_mode, "stack")

    def test_lens_optics_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported SMD OPTICS='lens'"):
            validate_optics_mode("lens")

    def test_none_optics_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported SMD OPTICS='none'"):
            validate_optics_mode("none")

    def test_lens_config_env_is_not_consumed_for_stack_mode(self) -> None:
        with patch.dict(
            os.environ,
            {"OPTICS": "stack", "LENS_CONFIG": "/missing/lens.json"},
            clear=True,
        ):
            self.assertEqual(load_source_config().optics_mode, "stack")

    def test_pmma_ptfe_stack_config_and_helpers_remain_available(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_optical_stack_config()
        self.assertTrue(config.pmma_mode)
        self.assertTrue(config.ptfe_mode)
        self.assertAlmostEqual(config.pmma_t, 0.92)
        self.assertAlmostEqual(config.pmma_thk_m, 0.003)
        self.assertAlmostEqual(config.pmma_ior, 1.49)
        self.assertAlmostEqual(config.ptfe_reflectance, 0.97)
        self.assertAlmostEqual(config.ptfe_thk_m, 0.000508)

        handle = io.StringIO()
        write_stack_cavity(
            handle,
            0.0,
            0.0,
            0.0,
            ptfe_mode=config.ptfe_mode,
            aperture_m=config.stack_aperture_m,
            liner_thk_m=config.ptfe_thk_m,
            stack_height_m=config.pmma_lid_offset_m,
        )
        write_pmma_lid(
            handle,
            0,
            0.0,
            0.0,
            0.0,
            side_m=config.pmma_side_m,
            lid_offset_m=config.pmma_lid_offset_m,
            lid_thk_m=config.pmma_thk_m,
        )
        stack_rad = handle.getvalue()
        self.assertIn("ptfe_liner polygon", stack_rad)
        self.assertIn("pmma_lid polygon", stack_rad)

    def test_active_module_profile_counts(self) -> None:
        self.assertEqual(MODULE_PROFILE_VERSION, "145led_clear_lid_ptfe_stack_v2")
        self.assertEqual(CHANNEL_COUNTS["WW"], 52)
        self.assertEqual(CHANNEL_COUNTS["CW"], 52)
        self.assertEqual(CHANNEL_COUNTS["R"], 41)


if __name__ == "__main__":
    unittest.main()
