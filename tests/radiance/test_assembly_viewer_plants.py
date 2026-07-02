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
  surfaceFluxColorHexForTargetDeviation,
  surfaceFluxTargetDeviationForLeafValue,
  summarizePlantPayload,
}} from {test_module.as_uri()!r};

const leafFluxValues = [
  {{ leaf_id: "plant_r000_c000_leaf_000", lighting_region: "under_lit", incident_photon_flux_density_umol_m2_s: 75 }},
  {{ leaf_id: "plant_r000_c000_leaf_001", lighting_region: "under_lit", incident_photon_flux_density_umol_m2_s: 200 }},
  {{ leaf_id: "plant_r000_c000_leaf_002", lighting_region: "target_range", incident_photon_flux_density_umol_m2_s: 255 }},
  {{ leaf_id: "plant_r000_c000_leaf_003", lighting_region: "target_range", incident_photon_flux_density_umol_m2_s: 265 }},
  {{ leaf_id: "plant_r000_c000_leaf_004", lighting_region: "target_range", incident_photon_flux_density_umol_m2_s: 270 }},
  {{ leaf_id: "plant_r000_c000_leaf_005", lighting_region: "target_range", incident_photon_flux_density_umol_m2_s: 275 }},
  {{ leaf_id: "plant_r000_c000_leaf_006", lighting_region: "target_range", incident_photon_flux_density_umol_m2_s: 280 }},
  {{ leaf_id: "plant_r000_c000_leaf_007", lighting_region: "target_range", incident_photon_flux_density_umol_m2_s: 295 }},
  {{ leaf_id: "plant_r000_c000_leaf_008", lighting_region: "over_lit", incident_photon_flux_density_umol_m2_s: 315 }},
  {{ leaf_id: "plant_r000_c000_leaf_009", lighting_region: "over_lit", incident_photon_flux_density_umol_m2_s: 375 }},
  {{ leaf_id: "plant_r000_c000_leaf_010", lighting_region: "over_lit", incident_photon_flux_density_umol_m2_s: 575 }},
];

function testLeaf(row, index) {{
  const x = index * 0.2;
  return {{
    plant_id: "plant_r000_c000",
    leaf_id: row.leaf_id,
    radiance_material_id: "plant_leaf_material",
    mesh: {{
      vertices: [
        [x, 0, 0.02],
        [x + 0.1, 0, 0.03],
        [x, 0.2, 0.04],
      ],
      faces: [[0, 1, 2]],
    }},
  }};
}}

function sceneLeaf(plantId, leafId, index) {{
  const x = index * 0.2;
  return {{
    plant_id: plantId,
    leaf_id: leafId,
    radiance_material_id: "plant_leaf_material",
    mesh: {{
      vertices: [
        [x, 0, 0.02],
        [x + 0.1, 0, 0.03],
        [x, 0.2, 0.04],
      ],
      faces: [[0, 1, 2]],
    }},
  }};
}}

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
      target_lower_threshold_umol_m2_s: 255,
      target_upper_threshold_umol_m2_s: 295,
      target_ppfd_umol_m2_s: 275,
      target_tolerance_umol_m2_s: 20,
      visualization: {{
        color_metric: "incident_photon_flux_density_umol_m2_s",
        color_quantity: "incident_leaf_surface_ppfd",
        leaf_values: leafFluxValues.map((row, index) => ({{
          ...row,
          plant_id: "plant_r000_c000",
          visual_intensity_0_1: index / (leafFluxValues.length - 1),
        }})),
      }},
    }},
    plants: [
      {{
        plant_id: "plant_r000_c000",
        row: 0,
        column: 0,
        center_m: [0, 0, 0],
        leaves: leafFluxValues.map(testLeaf),
      }},
    ],
  }},
}};

assert.equal(hasPlantPayload(scenePayload), true);
assert.deepEqual(summarizePlantPayload(scenePayload), {{ plantCount: 1, leafCount: 11 }});

const geometry = createLeafGeometry(scenePayload.plants.plants[0].leaves[0]);
assert.ok(geometry);
const positions = Array.from(geometry.getAttribute("position").array).map((value) => Number(value.toFixed(4)));
assert.deepEqual(positions, [0, 0.02, 0, 0.1, 0.03, 0, 0, 0.04, 0.2]);

const group = createPlantGroup(scenePayload);
assert.equal(group.name, "plant-geometry");
assert.equal(group.userData.plantCount, 1);
assert.equal(group.userData.leafCount, 11);
assert.equal(group.userData.renderedLeafCount, 11);
assert.equal(group.userData.absorptionColoredLeafCount, 11);
assert.equal(group.userData.matchedLeafCount, 11);
assert.equal(group.userData.unmatchedLeafCount, 0);
assert.equal(group.userData.targetDeviationLeafCount, 11);
assert.equal(group.userData.legacyFallbackLeafCount, 0);
assert.equal(group.userData.defaultColorLeafCount, 0);
assert.equal(group.userData.hasAbsorptionColor, true);
assert.equal(group.userData.colorMetric, "incident_photon_flux_density_umol_m2_s");
assert.equal(group.children.length, 1);
const meshChildren = [];
group.traverse((child) => {{
  if (child.isMesh) {{
    meshChildren.push(child);
  }}
}});
assert.equal(meshChildren.length, 1);
assert.ok(meshChildren.length < group.userData.leafCount / 2);
const plantMesh = meshChildren[0];
assert.equal(plantMesh.name, "plant-leaves-batched");
assert.equal(plantMesh.userData.batched, true);
assert.equal(plantMesh.userData.renderedLeafCount, 11);
assert.equal(plantMesh.material.vertexColors, true);
assert.equal(plantMesh.geometry.getAttribute("position").count, 33);
assert.equal(plantMesh.geometry.index.count, 33);
assert.equal(plantMesh.geometry.getAttribute("color"), plantMesh.userData.defaultColorAttribute);

function leafColor(attribute, leafIndex) {{
  const array = Array.from(attribute.array);
  const offset = leafIndex * 9;
  return array.slice(offset, offset + 3);
}}

function colorDistance(first, second) {{
  return Math.abs(first[0] - second[0]) + Math.abs(first[1] - second[1]) + Math.abs(first[2] - second[2]);
}}

function hexRgb(hex) {{
  const value = hex.replace("#", "");
  return [
    Number.parseInt(value.slice(0, 2), 16),
    Number.parseInt(value.slice(2, 4), 16),
    Number.parseInt(value.slice(4, 6), 16),
  ];
}}

function hexDistance(first, second) {{
  const a = hexRgb(first);
  const b = hexRgb(second);
  return Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2]);
}}

function normalizedHexColor(hex) {{
  return hexRgb(hex).map((value) => {{
    const component = value / 255;
    return component <= 0.04045
      ? component / 12.92
      : ((component + 0.055) / 1.055) ** 2.4;
  }});
}}

function assertColorNearHex(color, hex, tolerance = 0.01) {{
  const expected = normalizedHexColor(hex);
  assert.ok(Math.abs(color[0] - expected[0]) <= tolerance, `${{color[0]}} not near red ${{expected[0]}}`);
  assert.ok(Math.abs(color[1] - expected[1]) <= tolerance, `${{color[1]}} not near green ${{expected[1]}}`);
  assert.ok(Math.abs(color[2] - expected[2]) <= tolerance, `${{color[2]}} not near blue ${{expected[2]}}`);
}}

const targetContext = {{
  target_ppfd_umol_m2_s: 275,
  target_tolerance_umol_m2_s: 20,
}};
assert.equal(
  surfaceFluxTargetDeviationForLeafValue(
    {{ target_deviation: -0.85, incident_photon_flux_density_umol_m2_s: 1 }},
    targetContext,
    "incident_photon_flux_density_umol_m2_s",
  ),
  -0.85,
);
assert.equal(
  surfaceFluxTargetDeviationForLeafValue(
    {{ target_classification_ppfd_umol_m2_s: 289, incident_photon_flux_density_umol_m2_s: 1 }},
    targetContext,
    "incident_photon_flux_density_umol_m2_s",
  ),
  0.7,
);
assert.equal(
  surfaceFluxTargetDeviationForLeafValue(
    {{ incident_photon_flux_density_umol_m2_s: 289 }},
    targetContext,
    "incident_photon_flux_density_umol_m2_s",
  ),
  0.7,
);
assert.equal(surfaceFluxTargetDeviationForLeafValue({{}}, targetContext), null);

const targetBandHexes = [-1, -0.5, 0, 0.5, 1].map(surfaceFluxColorHexForTargetDeviation);
assert.equal(surfaceFluxColorHexForTargetDeviation(-8), "#173E29");
assert.equal(surfaceFluxColorHexForTargetDeviation(-2), "#236F49");
assert.equal(surfaceFluxColorHexForTargetDeviation(-1.25), "#11704F");
assert.equal(surfaceFluxColorHexForTargetDeviation(-1), "#147A56");
assert.equal(surfaceFluxColorHexForTargetDeviation(-0.9), "#188458");
assert.equal(surfaceFluxColorHexForTargetDeviation(-0.5), "#2DAA60");
assert.equal(surfaceFluxColorHexForTargetDeviation(0), "#4BCF6A");
assert.equal(surfaceFluxColorHexForTargetDeviation(0.5), "#90DD58");
assert.equal(surfaceFluxColorHexForTargetDeviation(0.72), "#A7E14F");
assert.equal(surfaceFluxColorHexForTargetDeviation(1), "#B7E455");
assert.equal(surfaceFluxColorHexForTargetDeviation(1.5), "#C9DF4F");
assert.equal(surfaceFluxColorHexForTargetDeviation(2), "#D8D747");
assert.equal(surfaceFluxColorHexForTargetDeviation(8), "#F0A63A");
assert.equal(surfaceFluxColorHexForTargetDeviation(-0.85), "#1B8859");
assert.equal(surfaceFluxColorHexForTargetDeviation(0.7), "#A5E050");
assert.equal(new Set(targetBandHexes).size, targetBandHexes.length);
assert.equal(new Set([-1, -0.75, -0.5, -0.25, 0, 0.25, 0.5, 0.75, 1].map(
  surfaceFluxColorHexForTargetDeviation,
)).size, 9);
assert.ok(hexDistance("#3FA66F", surfaceFluxColorHexForTargetDeviation(-0.85)) > 25);
assert.ok(hexDistance("#3FA66F", surfaceFluxColorHexForTargetDeviation(0.7)) > 55);
assert.ok(hexDistance("#3FA66F", surfaceFluxColorHexForTargetDeviation(-1)) > 30);
assert.ok(hexDistance("#3FA66F", surfaceFluxColorHexForTargetDeviation(0)) > 55);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(0))[0] > hexRgb(surfaceFluxColorHexForTargetDeviation(-1))[0]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(0))[1] > hexRgb(surfaceFluxColorHexForTargetDeviation(-1))[1]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(-1))[1] < hexRgb(surfaceFluxColorHexForTargetDeviation(0))[1]);
assert.ok(hexDistance(surfaceFluxColorHexForTargetDeviation(-0.5), surfaceFluxColorHexForTargetDeviation(-1)) > 70);
assert.ok(hexDistance(surfaceFluxColorHexForTargetDeviation(-0.5), surfaceFluxColorHexForTargetDeviation(0)) > 70);
assert.ok(hexDistance(surfaceFluxColorHexForTargetDeviation(0.5), surfaceFluxColorHexForTargetDeviation(0)) > 70);
assert.ok(hexDistance(surfaceFluxColorHexForTargetDeviation(0.5), surfaceFluxColorHexForTargetDeviation(1)) > 45);
assert.equal(new Set([-0.9, -0.5, 0, 0.5, 0.72].map(surfaceFluxColorHexForTargetDeviation)).size, 5);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(1))[0] > hexRgb(surfaceFluxColorHexForTargetDeviation(0))[0]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(1))[1] > hexRgb(surfaceFluxColorHexForTargetDeviation(0))[1]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(2))[0] > hexRgb(surfaceFluxColorHexForTargetDeviation(1))[0]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(2))[2] < hexRgb(surfaceFluxColorHexForTargetDeviation(1))[2]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(8))[0] > hexRgb(surfaceFluxColorHexForTargetDeviation(8))[1]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(8))[1] > hexRgb(surfaceFluxColorHexForTargetDeviation(8))[2]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(-8))[1] < hexRgb(surfaceFluxColorHexForTargetDeviation(-2))[1]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(-8))[0] < hexRgb(surfaceFluxColorHexForTargetDeviation(-2))[0]);

const defaultLeafColor = leafColor(plantMesh.userData.defaultColorAttribute, 0);
const strongUnderColor = leafColor(plantMesh.userData.absorptionColorAttribute, 0);
const moderateUnderColor = leafColor(plantMesh.userData.absorptionColorAttribute, 1);
const lowEdgeColor = leafColor(plantMesh.userData.absorptionColorAttribute, 2);
const nearLowColor = leafColor(plantMesh.userData.absorptionColorAttribute, 3);
const nearPeakLowColor = leafColor(plantMesh.userData.absorptionColorAttribute, 4);
const exactTargetColor = leafColor(plantMesh.userData.absorptionColorAttribute, 5);
const nearPeakHighColor = leafColor(plantMesh.userData.absorptionColorAttribute, 6);
const highEdgeColor = leafColor(plantMesh.userData.absorptionColorAttribute, 7);
const overModerateColor = leafColor(plantMesh.userData.absorptionColorAttribute, 8);
const overStrongColor = leafColor(plantMesh.userData.absorptionColorAttribute, 9);
const overExtremeColor = leafColor(plantMesh.userData.absorptionColorAttribute, 10);

assert.deepEqual(defaultLeafColor, leafColor(plantMesh.userData.defaultColorAttribute, 10));
assert.ok(colorDistance(defaultLeafColor, exactTargetColor) > 0.15);

assert.ok(strongUnderColor[1] > strongUnderColor[0]);
assert.ok(moderateUnderColor[1] > moderateUnderColor[0]);
assert.ok(moderateUnderColor[1] > strongUnderColor[1]);
assert.ok(colorDistance(strongUnderColor, moderateUnderColor) > 0.08);
assert.ok(lowEdgeColor[1] < exactTargetColor[1]);
assert.ok(nearLowColor[1] > lowEdgeColor[1]);

assert.ok(exactTargetColor[0] > lowEdgeColor[0]);
assert.ok(exactTargetColor[0] > nearLowColor[0]);
assert.ok(exactTargetColor[1] >= nearLowColor[1]);
assert.ok(exactTargetColor[1] >= nearPeakLowColor[1]);
assert.ok(colorDistance(exactTargetColor, nearPeakLowColor) > 0.02);
assert.ok(colorDistance(exactTargetColor, nearPeakHighColor) > 0.02);
assert.ok(colorDistance(lowEdgeColor, exactTargetColor) > 0.05);
assert.ok(colorDistance(highEdgeColor, exactTargetColor) > 0.05);
assert.ok(highEdgeColor[1] > highEdgeColor[0]);
assert.ok(highEdgeColor[0] > exactTargetColor[0]);
assert.ok(highEdgeColor[2] > overExtremeColor[2]);

assert.ok(overModerateColor[0] > highEdgeColor[0]);
assert.ok(overStrongColor[1] < overModerateColor[1]);
assert.ok(overStrongColor[2] < overModerateColor[2]);
assert.ok(overExtremeColor[0] > overExtremeColor[1]);
assert.ok(overExtremeColor[1] < overStrongColor[1]);
assert.ok(overExtremeColor[2] < overStrongColor[2]);
assert.ok(colorDistance(overStrongColor, overExtremeColor) > 0.08);

const targetSampleValues = [256.9, 261.3, 267.3, 272.4, 275, 279.7, 283.5, 289.4];
const targetSampleExpectedHexes = ["#188358", "#23985C", "#34B462", "#45C867", "#4BCF6A", "#6CD766", "#86DC5C", "#A7E14F"];
const targetSampleRows = targetSampleValues.map((value, index) => {{
  const plantId = index < 4 ? "plant_r000_c000" : "plant_r000_c001";
  const leafId = `${{plantId}}_leaf_${{String(index % 4).padStart(3, "0")}}`;
  return {{
    leaf_id: leafId,
    plant_id: plantId,
    lighting_region: "target_range",
    incident_photon_flux_density_umol_m2_s: 1,
    target_classification_ppfd_umol_m2_s: value,
    target_deviation: (value - 275) / 20,
    visual_intensity_0_1: 0,
  }};
}});
const targetSampleScene = {{
  plants: {{
    schema: "rad_rebuild.fspm.plants.viewer.v1",
    units: "meters",
    material: {{ id: "plant_leaf_material", transmittance: 0.08 }},
    surface_flux: {{
      target_ppfd_umol_m2_s: 275,
      target_tolerance_umol_m2_s: 20,
      visualization: {{
        color_metric: "incident_photon_flux_density_umol_m2_s",
        color_quantity: "incident_leaf_surface_ppfd",
        leaf_values: targetSampleRows,
      }},
    }},
    plants: [
      {{
        plant_id: "plant_r000_c000",
        row: 0,
        column: 0,
        center_m: [0, 0, 0],
        leaves: targetSampleRows.slice(0, 4).map((row, index) => sceneLeaf(row.plant_id, row.leaf_id, index)),
      }},
      {{
        plant_id: "plant_r000_c001",
        row: 0,
        column: 1,
        center_m: [1, 0, 0],
        leaves: targetSampleRows.slice(4).map((row, index) => sceneLeaf(row.plant_id, row.leaf_id, index + 4)),
      }},
    ],
  }},
}};
const targetSampleGroup = createPlantGroup(targetSampleScene);
assert.equal(targetSampleGroup.userData.plantCount, 2);
assert.equal(targetSampleGroup.userData.leafCount, 8);
assert.equal(targetSampleGroup.userData.renderedLeafCount, 8);
assert.equal(targetSampleGroup.userData.matchedLeafCount, 8);
assert.equal(targetSampleGroup.userData.unmatchedLeafCount, 0);
assert.equal(targetSampleGroup.userData.targetDeviationLeafCount, 8);
assert.equal(targetSampleGroup.userData.legacyFallbackLeafCount, 0);
assert.equal(targetSampleGroup.userData.defaultColorLeafCount, 0);
const targetSampleMesh = targetSampleGroup.children[0];
const targetSampleHexes = new Set();
for (let index = 0; index < targetSampleValues.length; index += 1) {{
  const deviation = (targetSampleValues[index] - 275) / 20;
  const hex = surfaceFluxColorHexForTargetDeviation(deviation);
  assert.equal(hex, targetSampleExpectedHexes[index]);
  targetSampleHexes.add(hex);
  const color = leafColor(targetSampleMesh.userData.absorptionColorAttribute, index);
  assertColorNearHex(color, hex);
  assert.ok(colorDistance(color, normalizedHexColor("#3FA66F")) > 0.12);
  assert.ok(color[1] > color[0] * 0.9);
  assert.ok(color[1] > color[2]);
  assert.ok(hexRgb(hex)[1] >= hexRgb(hex)[0]);
  assert.ok(hexRgb(hex)[0] < 200);
  assert.ok(hexRgb(hex)[1] >= 120);
  assert.ok(hexRgb(hex)[2] >= 70);
}}
assert.equal(targetSampleHexes.size, targetSampleValues.length);
assert.ok(hexDistance(targetSampleExpectedHexes[0], targetSampleExpectedHexes[4]) >= 70);
assert.ok(hexDistance(targetSampleExpectedHexes[1], targetSampleExpectedHexes[4]) >= 55);
assert.ok(hexDistance(targetSampleExpectedHexes[2], targetSampleExpectedHexes[4]) >= 35);
assert.ok(hexDistance(targetSampleExpectedHexes[3], targetSampleExpectedHexes[4]) >= 12);
assert.ok(hexDistance(targetSampleExpectedHexes[5], targetSampleExpectedHexes[4]) >= 25);
assert.ok(hexDistance(targetSampleExpectedHexes[6], targetSampleExpectedHexes[4]) >= 45);
assert.ok(hexDistance(targetSampleExpectedHexes[7], targetSampleExpectedHexes[4]) >= 65);
assert.ok(hexRgb(targetSampleExpectedHexes[0])[0] < hexRgb(targetSampleExpectedHexes[4])[0]);
assert.ok(hexRgb(targetSampleExpectedHexes[0])[1] < hexRgb(targetSampleExpectedHexes[4])[1]);
assert.ok(hexRgb(targetSampleExpectedHexes[0])[2] < hexRgb(targetSampleExpectedHexes[4])[2]);
assert.ok(hexRgb(targetSampleExpectedHexes[1])[0] < hexRgb(targetSampleExpectedHexes[4])[0]);
assert.ok(hexRgb(targetSampleExpectedHexes[2])[0] < hexRgb(targetSampleExpectedHexes[4])[0]);
assert.ok(hexRgb(targetSampleExpectedHexes[7])[0] > hexRgb(targetSampleExpectedHexes[4])[0]);
assert.ok(hexRgb(targetSampleExpectedHexes[7])[1] > hexRgb(targetSampleExpectedHexes[4])[1]);
assert.ok(hexRgb(targetSampleExpectedHexes[7])[2] < hexRgb(targetSampleExpectedHexes[4])[2]);
assert.notEqual(surfaceFluxColorHexForTargetDeviation(-0.85), surfaceFluxColorHexForTargetDeviation(0));
assert.notEqual(surfaceFluxColorHexForTargetDeviation(0.5), surfaceFluxColorHexForTargetDeviation(0));
assert.notEqual(surfaceFluxColorHexForTargetDeviation(0.7), surfaceFluxColorHexForTargetDeviation(1));

const legacyScene = {{
  plants: {{
    schema: "rad_rebuild.fspm.plants.viewer.v1",
    units: "meters",
    material: {{ id: "plant_leaf_material", transmittance: 0.08 }},
    surface_flux: {{
      visualization: {{
        color_metric: "incident_photon_flux_density_umol_m2_s",
        color_quantity: "incident_leaf_surface_ppfd",
        leaf_values: [
          {{
            leaf_id: "plant_r000_c000_leaf_000",
            plant_id: "plant_r000_c000",
            lighting_region: "target_range",
            visual_intensity_0_1: 0,
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
          sceneLeaf("plant_r000_c000", "plant_r000_c000_leaf_000", 0),
          sceneLeaf("plant_r000_c000", "plant_r000_c000_leaf_001", 1),
        ],
      }},
    ],
  }},
}};
const legacyGroup = createPlantGroup(legacyScene);
assert.equal(legacyGroup.userData.renderedLeafCount, 2);
assert.equal(legacyGroup.userData.matchedLeafCount, 1);
assert.equal(legacyGroup.userData.unmatchedLeafCount, 1);
assert.equal(legacyGroup.userData.targetDeviationLeafCount, 0);
assert.equal(legacyGroup.userData.legacyFallbackLeafCount, 1);
assert.equal(legacyGroup.userData.defaultColorLeafCount, 1);
const legacyMesh = legacyGroup.children[0];
assertColorNearHex(leafColor(legacyMesh.userData.absorptionColorAttribute, 0), "#4BCF6A");
assert.deepEqual(
  leafColor(legacyMesh.userData.absorptionColorAttribute, 1),
  leafColor(legacyMesh.userData.defaultColorAttribute, 1),
);

const controller = createPlantVisibilityController(group);
assert.deepEqual(controller.getState(), {{
  visible: true,
  absorptionColor: true,
  hasAbsorptionColor: true,
  plantCount: 1,
  leafCount: 11,
  colorMetric: "incident_photon_flux_density_umol_m2_s",
}});
assert.equal(plantMesh.geometry.getAttribute("color"), plantMesh.userData.absorptionColorAttribute);
assert.equal(plantMesh.material, plantMesh.userData.absorptionMaterial);
assert.equal(plantMesh.material.isMeshBasicMaterial, true);
assert.equal(plantMesh.material.toneMapped, false);
controller.setAbsorptionColor(false);
assert.equal(plantMesh.geometry.getAttribute("color"), plantMesh.userData.defaultColorAttribute);
assert.equal(plantMesh.material, plantMesh.userData.defaultMaterial);
assert.equal(plantMesh.material.isMeshStandardMaterial, true);
assert.equal(controller.getState().absorptionColor, false);
controller.setAbsorptionColor(true);
assert.equal(plantMesh.geometry.getAttribute("color"), plantMesh.userData.absorptionColorAttribute);
assert.equal(plantMesh.material, plantMesh.userData.absorptionMaterial);
assert.equal(controller.getState().absorptionColor, true);
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
