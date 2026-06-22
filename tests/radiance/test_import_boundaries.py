from __future__ import annotations

import importlib
import unittest

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()


class RadianceImportBoundaryTests(unittest.TestCase):
    def assertImports(self, module_name: str) -> None:
        with self.subTest(module=module_name):
            self.assertIsNotNone(importlib.import_module(module_name))

    def test_backend_modules_import(self) -> None:
        modules = [
            "rad_rebuild.radiance.backend.server",
            "rad_rebuild.radiance.backend.models",
            "rad_rebuild.radiance.backend.env",
            "rad_rebuild.radiance.backend.jobs",
            "rad_rebuild.radiance.backend.workspace",
            "rad_rebuild.radiance.backend.runner",
            "rad_rebuild.radiance.backend.runtime",
            "rad_rebuild.radiance.backend.artifacts",
            "rad_rebuild.radiance.backend.metrics",
            "rad_rebuild.radiance.backend.costs",
        ]
        for module in modules:
            self.assertImports(module)

    def test_backend_route_modules_import(self) -> None:
        modules = [
            "rad_rebuild.radiance.backend.routes.artifacts",
            "rad_rebuild.radiance.backend.routes.costs",
            "rad_rebuild.radiance.backend.routes.docker",
            "rad_rebuild.radiance.backend.routes.health",
            "rad_rebuild.radiance.backend.routes.jobs",
            "rad_rebuild.radiance.backend.routes.metrics",
            "rad_rebuild.radiance.backend.routes.reproduce",
            "rad_rebuild.radiance.backend.routes.runs",
        ]
        for module in modules:
            self.assertImports(module)

    def test_smd_emitter_modules_import(self) -> None:
        modules = [
            "rad_rebuild.radiance.engine.emitters.generate_emitters_smd",
            "rad_rebuild.radiance.engine.emitters.smd_generation.config",
            "rad_rebuild.radiance.engine.emitters.smd_generation.module_profile",
            "rad_rebuild.radiance.engine.emitters.smd_generation.outputs",
            "rad_rebuild.radiance.engine.emitters.smd_generation.optical_stack",
            "rad_rebuild.radiance.engine.emitters.smd_generation.solution_metadata",
            "rad_rebuild.radiance.engine.emitters.smd_generation.source_variants",
        ]
        for module in modules:
            self.assertImports(module)

    def test_visualization_modules_import(self) -> None:
        modules = [
            "rad_rebuild.radiance.engine.visualization.data",
            "rad_rebuild.radiance.engine.visualization.heatmaps",
            "rad_rebuild.radiance.engine.visualization.overlays",
            "rad_rebuild.radiance.engine.visualization.scatter",
            "rad_rebuild.radiance.engine.visualization.style",
            "rad_rebuild.radiance.engine.visualization.visualize_ppfd",
        ]
        for module in modules:
            self.assertImports(module)

    def test_precomputed_playback_imports_after_pass_2(self) -> None:
        self.assertImports(
            "rad_rebuild.radiance.engine.simulation.precomputed_playback"
        )

    def test_precompute_sweep_imports_after_pass_2(self) -> None:
        self.assertImports("rad_rebuild.radiance.engine.simulation.precompute_sweep")


if __name__ == "__main__":
    unittest.main()
