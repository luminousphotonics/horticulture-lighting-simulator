from __future__ import annotations

import unittest

import numpy as np

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()


class VisualizationHeatmapTests(unittest.TestCase):
    def test_room_aligned_edges_snap_cell_center_grid_to_room_bounds(self) -> None:
        from rad_rebuild.radiance.engine.visualization.heatmaps import (
            _room_aligned_edges_1d,
        )

        centers = np.array([-1.446667 + (2.893334 / 20) * idx for idx in range(21)])

        edges = _room_aligned_edges_1d(centers, 3.048)

        self.assertAlmostEqual(edges[0], -1.524)
        self.assertAlmostEqual(edges[-1], 1.524)

    def test_room_aligned_edges_leave_partial_coverage_unchanged(self) -> None:
        from rad_rebuild.radiance.engine.visualization.heatmaps import (
            _room_aligned_edges_1d,
        )

        centers = np.array([-0.5, 0.0, 0.5])

        edges = _room_aligned_edges_1d(centers, 3.048)

        np.testing.assert_allclose(edges, np.array([-0.75, -0.25, 0.25, 0.75]))

    def test_edges_match_span_rejects_stale_overlay_room_bounds(self) -> None:
        from rad_rebuild.radiance.engine.visualization.heatmaps import (
            _edges_match_span,
            _room_aligned_edges_1d,
        )

        centers = np.array([-2.172381 + (4.344762 / 20) * idx for idx in range(21)])
        edges = _room_aligned_edges_1d(centers, 8.2296)

        self.assertFalse(_edges_match_span(edges, 8.2296))


if __name__ == "__main__":
    unittest.main()
