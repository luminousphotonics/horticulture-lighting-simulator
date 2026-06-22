from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.radiance.runtime_env import configure_test_runtime

from rad_rebuild.radiance.engine.emitters.smd_generation.power_schedule import (
    DEFAULT_RING_POWER_W_BY_RING,
    basis_ring_power_schedule,
    default_ring_power_schedule,
    expand_ring_power_schedule,
    module_basis_ring_power_schedule,
)


configure_test_runtime()


class SmdPowerScheduleTests(unittest.TestCase):
    def test_default_ring_schedule_values_are_documented(self) -> None:
        expected = {
            0: 31.827,
            1: 28.284,
            2: 33.154,
            3: 30.241,
            4: 25.900,
            5: 44.723,
            6: 3.596,
            7: 70.760,
            8: 70.760,
        }
        self.assertEqual(dict(DEFAULT_RING_POWER_W_BY_RING), expected)
        self.assertEqual(dict(default_ring_power_schedule().watts_by_ring), expected)

    def test_expanded_schedule_clamps_outer_rings_to_last_known_power(self) -> None:
        expanded = expand_ring_power_schedule(default_ring_power_schedule(), 12)
        self.assertEqual(expanded[8], 70.760)
        self.assertEqual(expanded[9], 70.760)
        self.assertEqual(expanded[10], 70.760)
        self.assertEqual(expanded[11], 70.760)

    def test_basis_ring_schedule_has_one_active_ring(self) -> None:
        schedule = basis_ring_power_schedule(
            required_rings=5, basis_ring=2, basis_unit_w=1.25
        )
        self.assertEqual(schedule, {0: 0.0, 1: 0.0, 2: 1.25, 3: 0.0, 4: 0.0})

    def test_module_basis_schedule_zeroes_ring_powers(self) -> None:
        self.assertEqual(
            module_basis_ring_power_schedule(4), {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
        )

    def test_generator_does_not_define_default_ring_power_table(self) -> None:
        source = Path(
            "src/rad_rebuild/radiance/engine/emitters/generate_emitters_smd.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("31.827", source)
        self.assertNotIn("70.760", source)
        self.assertNotIn("DEFAULT_RING_POWER_W_BY_RING", source)

    def test_generator_basis_ring_mode_sets_one_active_ring(self) -> None:
        module = self._import_generator_with_env(
            {
                "SMD_BASIS_MODE": "1",
                "SMD_BASIS_RING": "2",
                "SMD_BASIS_UNIT_W": "5.5",
                "SMD_BASIS_MODULE_IDX": "-1",
                "SMD_RING_N": "4",
            }
        )
        self.assertEqual(
            {ring: module.RING_POWER_BY_RING[ring] for ring in range(5)},
            {0: 0.0, 1: 0.0, 2: 5.5, 3: 0.0, 4: 0.0},
        )
        self.assertEqual(module.RING_POWERS_SOURCE, "basis_mode_ring_2")

    def test_generator_module_basis_mode_zeroes_rings_and_keeps_selected_module(
        self,
    ) -> None:
        module = self._import_generator_with_env(
            {
                "SMD_BASIS_MODE": "1",
                "SMD_BASIS_RING": "-1",
                "SMD_BASIS_MODULE_IDX": "3",
                "SMD_RING_N": "4",
            }
        )
        self.assertEqual(
            {ring: module.RING_POWER_BY_RING[ring] for ring in range(5)},
            {ring: 0.0 for ring in range(5)},
        )
        self.assertEqual(module.BASIS_MODULE_IDX, 3)
        self.assertEqual(module.RING_POWERS_SOURCE, "basis_mode_module_idx_3")

    def _import_generator_with_env(self, env: dict[str, str]):
        module_name = "rad_rebuild.radiance.engine.emitters.generate_emitters_smd"
        previous_module = sys.modules.pop(module_name, None)
        with tempfile.TemporaryDirectory(
            prefix="smd_power_schedule_test_"
        ) as runtime_dir:
            test_env = {
                "RADIANCE_RUNTIME_STATE_ROOT": runtime_dir,
                "RADIANCE_OUTPUT_ROOT": runtime_dir,
                "RADIANCE_BASIS_OUTPUT_ROOT": os.path.join(runtime_dir, "basis"),
                "USE_RING_POWERS_JSON": "0",
                "RING_POWERS_STRICT": "0",
                "SMD_MODEL": "legacy",
            }
            test_env.update(env)
            with patch.dict(os.environ, test_env, clear=False):
                try:
                    return importlib.import_module(module_name)
                finally:
                    imported_module = sys.modules.pop(module_name, None)
                    if previous_module is not None:
                        sys.modules[module_name] = previous_module
                    elif imported_module is not None:
                        sys.modules.pop(module_name, None)


if __name__ == "__main__":
    unittest.main()
