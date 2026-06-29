from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


PLANTS_JS = Path("src/rad_rebuild/web/static/js/assembly-viewer/plants.js")
THREE_JS = Path("src/rad_rebuild/web/static/vendor/three/three.module.js")


def test_assembly_viewer_plant_helpers(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for assembly viewer plant tests")

    test_module = tmp_path / "plants-test.mjs"
    test_module.write_text(
        PLANTS_JS.read_text(encoding="utf-8").replace(
            '"/static/vendor/three/three.module.js"',
            repr(THREE_JS.resolve().as_uri()),
        ),
        encoding="utf-8",
    )

    script = f"""
import assert from "node:assert/strict";
import {{
  createLeafGeometry,
  createPlantGroup,
  createPlantVisibilityController,
  hasPlantPayload,
  summarizePlantPayload,
}} from {test_module.as_uri()!r};

const scenePayload = {{
  plants: {{
    schema: "rad_rebuild.fspm.plants.viewer.v1",
    units: "meters",
    material: {{
      id: "plant_leaf_material",
      reflectance: 0.22,
      transmittance: 0.08,
      absorptance: 0.7,
    }},
    surface_flux: {{
      visualization: {{
        color_metric: "incident_photon_flux_density_umol_m2_s",
        color_quantity: "incident_leaf_surface_ppfd",
        leaf_values: [
          {{
            leaf_id: "plant_r000_c000_leaf_000",
            plant_id: "plant_r000_c000",
            incident_photon_flux_density_umol_m2_s: 250,
            visual_intensity_0_1: 0.75,
          }},
        ],
      }},
    }},
    plants: [
      {{
        plant_id: "plant_r000_c000",
        row: 0,
        column: 0,
        center_m: [0, 0, 0],
        leaves: [
          {{
            plant_id: "plant_r000_c000",
            leaf_id: "plant_r000_c000_leaf_000",
            radiance_material_id: "plant_leaf_material",
            mesh: {{
              vertices: [
                [0, 0, 0.02],
                [0.1, 0, 0.03],
                [0, 0.2, 0.04],
              ],
              faces: [[0, 1, 2]],
            }},
          }},
        ],
      }},
    ],
  }},
}};

assert.equal(hasPlantPayload(scenePayload), true);
assert.deepEqual(summarizePlantPayload(scenePayload), {{ plantCount: 1, leafCount: 1 }});

const geometry = createLeafGeometry(scenePayload.plants.plants[0].leaves[0]);
assert.ok(geometry);
const positions = Array.from(geometry.getAttribute("position").array).map((value) => Number(value.toFixed(4)));
assert.deepEqual(positions, [0, 0.02, 0, 0.1, 0.03, 0, 0, 0.04, 0.2]);

const group = createPlantGroup(scenePayload);
assert.equal(group.name, "plant-geometry");
assert.equal(group.userData.plantCount, 1);
assert.equal(group.userData.leafCount, 1);
assert.equal(group.userData.renderedLeafCount, 1);
assert.equal(group.userData.hasAbsorptionColor, true);
assert.equal(group.userData.colorMetric, "incident_photon_flux_density_umol_m2_s");
assert.equal(group.children.length, 1);
assert.equal(group.children[0].children.length, 1);
assert.equal(group.children[0].children[0].userData.leafId, "plant_r000_c000_leaf_000");
assert.equal(group.children[0].children[0].userData.visualIntensity, 0.75);

const controller = createPlantVisibilityController(group);
assert.deepEqual(controller.getState(), {{
  visible: true,
  absorptionColor: true,
  hasAbsorptionColor: true,
  plantCount: 1,
  leafCount: 1,
  colorMetric: "incident_photon_flux_density_umol_m2_s",
}});
controller.setVisible(false);
assert.equal(group.visible, false);
controller.setVisible(true);
assert.equal(group.visible, true);

assert.equal(hasPlantPayload({{}}), false);
assert.equal(createLeafGeometry({{ mesh: {{ vertices: [[0, 0, 0]], faces: [[0, 1, 2]] }} }}), null);
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_assembly_viewer_plants_do_not_reference_backend_state() -> None:
    source = PLANTS_JS.read_text(encoding="utf-8")
    forbidden_terms = [
        "fetch(",
        "lastCompletedRunKey",
        "radiance-api",
        "workspace",
        "runtime_state",
    ]
    for term in forbidden_terms:
        assert term not in source
