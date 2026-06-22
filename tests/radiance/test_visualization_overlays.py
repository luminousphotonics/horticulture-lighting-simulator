from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()


def _write_smd_layout(path: Path, room_span: float, modules: int) -> None:
    positions = [
        {
            "x": float(idx),
            "y": 0.0,
            "z": 0.4572,
        }
        for idx in range(modules)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "units": "meters",
                "room": {"L": room_span, "W": room_span},
                "z": 0.4572,
                "patch_side": 0.12,
                "positions": positions,
            }
        ),
        encoding="utf-8",
    )


class VisualizationOverlayTests(unittest.TestCase):
    def test_workspace_runtime_state_wins_over_stale_env_root(self) -> None:
        from rad_rebuild.radiance.engine.visualization.overlays import (
            load_overlay_context,
        )

        with (
            tempfile.TemporaryDirectory(
                prefix="rad_overlay_workspace_"
            ) as workspace_tmp,
            tempfile.TemporaryDirectory(prefix="rad_overlay_global_") as global_tmp,
        ):
            workspace = Path(workspace_tmp)
            global_root = Path(global_tmp)
            _write_smd_layout(
                workspace / "runtime_state" / "smd_layout.json", 4.572, 113
            )
            _write_smd_layout(global_root / "smd_layout.json", 8.2296, 365)

            previous_cwd = Path.cwd()
            try:
                os.chdir(workspace)
                with patch.dict(
                    os.environ, {"RADIANCE_RUNTIME_STATE_ROOT": str(global_root)}
                ):
                    context = load_overlay_context("smd", "xy")
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(context.room_bounds, (4.572, 4.572))
        self.assertEqual(len(context.points[0][1]), 113)


if __name__ == "__main__":
    unittest.main()
