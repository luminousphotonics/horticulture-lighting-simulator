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
  PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX,
  PLANT_COLOR_MODE_TARGET_RANGE,
  createLeafGeometry,
  createPlantGroup,
  createPlantVisibilityController,
  hasPlantPayload,
  rawLeafSurfaceFluxColorHexForValue,
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

function relativeLuminance(hex) {{
  const [r, g, b] = hexRgb(hex).map((value) => {{
    const channel = value / 255;
    return channel <= 0.04045
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4;
  }});
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
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
assert.equal(surfaceFluxColorHexForTargetDeviation(-40), "#3FA66F");
assert.equal(surfaceFluxColorHexForTargetDeviation(-32), "#3FA66F");
assert.equal(surfaceFluxColorHexForTargetDeviation(-28), "#3FA66F");
assert.equal(surfaceFluxColorHexForTargetDeviation(-20), "#3FA66F");
assert.equal(surfaceFluxColorHexForTargetDeviation(-12), "#3FA66F");
assert.equal(surfaceFluxColorHexForTargetDeviation(-10), "#3FA66F");
assert.equal(surfaceFluxColorHexForTargetDeviation(-8.75), "#41AA70");
assert.equal(surfaceFluxColorHexForTargetDeviation(-7.5), "#43AE71");
assert.equal(surfaceFluxColorHexForTargetDeviation(-6.25), "#43B670");
assert.equal(surfaceFluxColorHexForTargetDeviation(-5), "#43BE6E");
assert.equal(surfaceFluxColorHexForTargetDeviation(-3.75), "#44C66C");
assert.equal(surfaceFluxColorHexForTargetDeviation(-2.5), "#44C66C");
assert.equal(surfaceFluxColorHexForTargetDeviation(-1), "#46CB6A");
assert.equal(surfaceFluxColorHexForTargetDeviation(-0.9), "#47CB6A");
assert.equal(surfaceFluxColorHexForTargetDeviation(-0.75), "#47CC6A");
assert.equal(surfaceFluxColorHexForTargetDeviation(-0.5), "#49CD6A");
assert.equal(surfaceFluxColorHexForTargetDeviation(0), "#4BCF6A");
assert.equal(surfaceFluxColorHexForTargetDeviation(0.5), "#90DD58");
assert.equal(surfaceFluxColorHexForTargetDeviation(0.72), "#A7E14F");
assert.equal(surfaceFluxColorHexForTargetDeviation(1), "#B7E455");
assert.equal(surfaceFluxColorHexForTargetDeviation(1.5), "#C9DF4F");
assert.equal(surfaceFluxColorHexForTargetDeviation(2), "#D8D747");
assert.equal(surfaceFluxColorHexForTargetDeviation(4), "#E6C33F");
assert.equal(surfaceFluxColorHexForTargetDeviation(8), "#F0A63A");
assert.equal(surfaceFluxColorHexForTargetDeviation(-0.85), "#47CC6A");
assert.equal(surfaceFluxColorHexForTargetDeviation(0.7), "#A5E050");
function ppfdColor(ppfd, surfaceFlux = targetContext) {{
  const deviation = surfaceFluxTargetDeviationForLeafValue(
    {{ incident_photon_flux_density_umol_m2_s: ppfd }},
    surfaceFlux,
    "incident_photon_flux_density_umol_m2_s",
  );
  return surfaceFluxColorHexForTargetDeviation(deviation, surfaceFlux);
}}
assert.equal(ppfdColor(33), "#3FA66F");
assert.equal(ppfdColor(47), "#3FA66F");
assert.equal(ppfdColor(68), "#3FA66F");
assert.equal(ppfdColor(75), "#3FA66F");
assert.equal(ppfdColor(100), "#41AA70");
assert.equal(ppfdColor(125), "#43AE71");
assert.equal(ppfdColor(145), "#43B470");
assert.equal(ppfdColor(175), "#43BE6E");
assert.equal(ppfdColor(200), "#44C66C");
assert.equal(ppfdColor(225), "#44C66C");
assert.equal(ppfdColor(255), "#46CB6A");
assert.equal(ppfdColor(258), "#47CC6A");
assert.equal(ppfdColor(275), "#4BCF6A");
assert.equal(ppfdColor(289), "#A5E050");
assert.equal(surfaceFluxColorHexForTargetDeviation(-5, {{
  target_ppfd_umol_m2_s: 275,
  target_tolerance_umol_m2_s: 40,
}}), "#3FA66F");
assert.equal(surfaceFluxColorHexForTargetDeviation(-4, {{
  target_ppfd_umol_m2_s: 275,
  target_tolerance_umol_m2_s: 40,
}}), "#43AD71");
assert.equal(surfaceFluxColorHexForTargetDeviation(-10, {{}}), "#3FA66F");
assert.equal(surfaceFluxColorHexForTargetDeviation(-8.75, {{}}), "#41AA70");
assert.equal(new Set(targetBandHexes).size, targetBandHexes.length);
assert.equal(new Set([-1, -0.75, -0.5, -0.25, 0, 0.25, 0.5, 0.75, 1].map(
  surfaceFluxColorHexForTargetDeviation,
)).size, 9);
for (const hex of [-32, -28, -20, -12, -10, -8.75, -7.5, -6.25, -5, -3.75, -2.5, -1].map(
  surfaceFluxColorHexForTargetDeviation,
)) {{
  assert.notEqual(hex, "#102A1B");
  assert.ok(relativeLuminance(hex) + 0.001 >= relativeLuminance("#3FA66F"));
  assert.ok(relativeLuminance(hex) < relativeLuminance("#4BCF6A"));
  assert.ok(hexRgb(hex)[1] > hexRgb(hex)[0]);
  assert.ok(hexRgb(hex)[1] > hexRgb(hex)[2]);
}}
assert.ok(hexDistance(ppfdColor(145), surfaceFluxColorHexForTargetDeviation(0)) > 40);
assert.ok(hexDistance(ppfdColor(145), surfaceFluxColorHexForTargetDeviation(0.7)) > 120);
assert.ok(hexDistance("#3FA66F", surfaceFluxColorHexForTargetDeviation(-0.85)) > 10);
assert.ok(hexDistance("#3FA66F", surfaceFluxColorHexForTargetDeviation(0.7)) > 55);
assert.ok(relativeLuminance(surfaceFluxColorHexForTargetDeviation(-1)) >= relativeLuminance("#3FA66F"));
assert.ok(relativeLuminance(surfaceFluxColorHexForTargetDeviation(-1)) < relativeLuminance("#4BCF6A"));
assert.ok(hexDistance("#3FA66F", surfaceFluxColorHexForTargetDeviation(0)) > 55);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(0))[0] > hexRgb(surfaceFluxColorHexForTargetDeviation(-1))[0]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(0))[1] > hexRgb(surfaceFluxColorHexForTargetDeviation(-1))[1]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(-1))[1] < hexRgb(surfaceFluxColorHexForTargetDeviation(0))[1]);
assert.ok(hexDistance(surfaceFluxColorHexForTargetDeviation(-2.5), surfaceFluxColorHexForTargetDeviation(-1)) < 12);
assert.ok(hexDistance(surfaceFluxColorHexForTargetDeviation(-1), surfaceFluxColorHexForTargetDeviation(-0.75)) < 8);
assert.ok(hexDistance(surfaceFluxColorHexForTargetDeviation(-0.75), surfaceFluxColorHexForTargetDeviation(-0.5)) < 8);
assert.ok(hexDistance(surfaceFluxColorHexForTargetDeviation(-0.5), surfaceFluxColorHexForTargetDeviation(0)) < 35);
assert.ok(hexDistance(surfaceFluxColorHexForTargetDeviation(-0.5), surfaceFluxColorHexForTargetDeviation(0)) >= 4);
assert.ok(hexDistance(surfaceFluxColorHexForTargetDeviation(0.5), surfaceFluxColorHexForTargetDeviation(0)) > 70);
assert.ok(hexDistance(surfaceFluxColorHexForTargetDeviation(0.5), surfaceFluxColorHexForTargetDeviation(1)) > 45);
assert.equal(new Set([-0.9, -0.5, 0, 0.5, 0.72].map(surfaceFluxColorHexForTargetDeviation)).size, 5);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(1))[0] > hexRgb(surfaceFluxColorHexForTargetDeviation(0))[0]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(1))[1] > hexRgb(surfaceFluxColorHexForTargetDeviation(0))[1]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(2))[0] > hexRgb(surfaceFluxColorHexForTargetDeviation(1))[0]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(2))[2] < hexRgb(surfaceFluxColorHexForTargetDeviation(1))[2]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(8))[0] > hexRgb(surfaceFluxColorHexForTargetDeviation(8))[1]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(8))[1] > hexRgb(surfaceFluxColorHexForTargetDeviation(8))[2]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(-5))[1] > hexRgb(surfaceFluxColorHexForTargetDeviation(-10))[1]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(-2.5))[1] >= hexRgb(surfaceFluxColorHexForTargetDeviation(-5))[1]);
assert.ok(hexRgb(surfaceFluxColorHexForTargetDeviation(-2.5))[0] >= hexRgb(surfaceFluxColorHexForTargetDeviation(-5))[0]);

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
assert.ok(colorDistance(strongUnderColor, moderateUnderColor) > 0.02);
assert.ok(colorDistance(moderateUnderColor, lowEdgeColor) > 0.02);
assert.ok(lowEdgeColor[1] < exactTargetColor[1]);
assert.ok(nearLowColor[1] > lowEdgeColor[1]);

assert.ok(exactTargetColor[0] > lowEdgeColor[0]);
assert.ok(exactTargetColor[0] > nearLowColor[0]);
assert.ok(exactTargetColor[1] >= nearLowColor[1]);
assert.ok(exactTargetColor[1] >= nearPeakLowColor[1]);
assert.ok(colorDistance(exactTargetColor, nearPeakLowColor) > 0.005);
assert.ok(colorDistance(exactTargetColor, nearPeakHighColor) > 0.02);
assert.ok(colorDistance(lowEdgeColor, exactTargetColor) > 0.01);
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
const targetSampleExpectedHexes = ["#46CB6A", "#48CC6A", "#49CD6A", "#4ACE6A", "#4BCF6A", "#6CD766", "#86DC5C", "#A7E14F"];
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
      ppfd_map: {{ fake_canopy_value_that_raw_mode_must_ignore: 999999 }},
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
  const hex = surfaceFluxColorHexForTargetDeviation(deviation, targetSampleScene.plants.surface_flux);
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
assert.ok(hexDistance(targetSampleExpectedHexes[0], targetSampleExpectedHexes[4]) >= 8);
assert.ok(hexDistance(targetSampleExpectedHexes[1], targetSampleExpectedHexes[4]) >= 6);
assert.ok(hexDistance(targetSampleExpectedHexes[2], targetSampleExpectedHexes[4]) >= 4);
assert.ok(hexDistance(targetSampleExpectedHexes[3], targetSampleExpectedHexes[4]) >= 2);
assert.ok(hexDistance(targetSampleExpectedHexes[5], targetSampleExpectedHexes[4]) >= 25);
assert.ok(hexDistance(targetSampleExpectedHexes[6], targetSampleExpectedHexes[4]) >= 45);
assert.ok(hexDistance(targetSampleExpectedHexes[7], targetSampleExpectedHexes[4]) >= 65);
assert.ok(hexRgb(targetSampleExpectedHexes[0])[0] < hexRgb(targetSampleExpectedHexes[4])[0]);
assert.ok(hexRgb(targetSampleExpectedHexes[0])[1] < hexRgb(targetSampleExpectedHexes[4])[1]);
assert.ok(hexRgb(targetSampleExpectedHexes[0])[2] === hexRgb(targetSampleExpectedHexes[4])[2]);
assert.ok(hexRgb(targetSampleExpectedHexes[1])[0] < hexRgb(targetSampleExpectedHexes[4])[0]);
assert.ok(hexRgb(targetSampleExpectedHexes[2])[0] < hexRgb(targetSampleExpectedHexes[4])[0]);
assert.ok(hexRgb(targetSampleExpectedHexes[7])[0] > hexRgb(targetSampleExpectedHexes[4])[0]);
assert.ok(hexRgb(targetSampleExpectedHexes[7])[1] > hexRgb(targetSampleExpectedHexes[4])[1]);
assert.ok(hexRgb(targetSampleExpectedHexes[7])[2] < hexRgb(targetSampleExpectedHexes[4])[2]);
assert.notEqual(surfaceFluxColorHexForTargetDeviation(-0.85), surfaceFluxColorHexForTargetDeviation(0));
assert.notEqual(surfaceFluxColorHexForTargetDeviation(0.5), surfaceFluxColorHexForTargetDeviation(0));
assert.notEqual(surfaceFluxColorHexForTargetDeviation(0.7), surfaceFluxColorHexForTargetDeviation(1));

const rawModeRows = [
  {{
    leaf_id: "plant_r000_c000_leaf_000",
    plant_id: "plant_r000_c000",
    lighting_region: "over_lit",
    incident_photon_flux_density_umol_m2_s: 0,
    target_classification_ppfd_umol_m2_s: 900,
    target_deviation: 31.25,
    visual_intensity_0_1: 1,
  }},
  {{
    leaf_id: "plant_r000_c000_leaf_001",
    plant_id: "plant_r000_c000",
    lighting_region: "under_lit",
    incident_photon_flux_density_umol_m2_s: 100,
    target_classification_ppfd_umol_m2_s: 275,
    target_deviation: 0,
    visual_intensity_0_1: 0,
  }},
  {{
    leaf_id: "plant_r000_c000_leaf_002",
    plant_id: "plant_r000_c000",
    lighting_region: "target_range",
    incident_photon_flux_density_umol_m2_s: 200,
    target_classification_ppfd_umol_m2_s: 275,
    target_deviation: 0,
    visual_intensity_0_1: 0,
  }},
  {{
    leaf_id: "plant_r000_c000_leaf_003",
    plant_id: "plant_r000_c000",
    lighting_region: "under_lit",
    incident_photon_flux_density_umol_m2_s: 300,
    target_classification_ppfd_umol_m2_s: 275,
    target_deviation: 0,
    visual_intensity_0_1: 0,
  }},
  {{
    leaf_id: "plant_r000_c000_leaf_004",
    plant_id: "plant_r000_c000",
    lighting_region: "under_lit",
    incident_photon_flux_density_umol_m2_s: 1000,
    target_classification_ppfd_umol_m2_s: 0,
    target_deviation: -13.75,
    visual_intensity_0_1: 0,
  }},
];
const rawModeScale = {{
  mode: "raw_leaf_surface_flux",
  scale_type: "target_normalized_ratio",
  target_ppfd_umol_m2_s: 275,
  target_source: "fspm_target_ppfd_umol_m2_s",
  ratio_min: 0,
  ratio_max: 1.5,
  clamp_min_ratio: 0,
  clamp_max_ratio: 1.5,
  anchors: [
    {{ ratio: 0.00, percent: 0, ppfd_umol_m2_s: 0, color: "#2563EB" }},
    {{ ratio: 0.20, percent: 20, ppfd_umol_m2_s: 55, color: "#06B6D4" }},
    {{ ratio: 0.40, percent: 40, ppfd_umol_m2_s: 110, color: "#14B8A6" }},
    {{ ratio: 0.55, percent: 55, ppfd_umol_m2_s: 151.25, color: "#22C55E" }},
    {{ ratio: 0.80, percent: 80, ppfd_umol_m2_s: 220, color: "#22C55E" }},
    {{ ratio: 1.00, percent: 100, ppfd_umol_m2_s: 275, color: "#A3E635" }},
    {{ ratio: 1.20, percent: 120, ppfd_umol_m2_s: 330, color: "#F59E0B" }},
    {{ ratio: 1.50, percent: 150, ppfd_umol_m2_s: 412.5, color: "#DC2626" }},
  ],
  units: "umol/m²/s",
  ratio_units: "fraction_of_target",
}};
const rawModeScene = {{
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
        raw_leaf_surface_flux_scale: rawModeScale,
        raw_leaf_surface_flux_legend: {{
          title: "Raw leaf-surface incident PPFD",
          units: "umol/m²/s",
          scale: "% of FSPM target",
          target_ppfd_umol_m2_s: 275,
          target_source: "fspm_target_ppfd_umol_m2_s",
          anchors: rawModeScale.anchors,
        }},
        leaf_values: rawModeRows,
      }},
    }},
    plants: [
      {{
        plant_id: "plant_r000_c000",
        row: 0,
        column: 0,
        center_m: [0, 0, 0],
        leaves: rawModeRows.map((row, index) => sceneLeaf(row.plant_id, row.leaf_id, index)),
      }},
    ],
  }},
}};
assert.equal(rawLeafSurfaceFluxColorHexForValue(-50, rawModeScale), rawLeafSurfaceFluxColorHexForValue(0, rawModeScale));
assert.equal(rawLeafSurfaceFluxColorHexForValue(1200, rawModeScale), rawLeafSurfaceFluxColorHexForValue(412.5, rawModeScale));
assert.notEqual(rawLeafSurfaceFluxColorHexForValue(55, rawModeScale), rawLeafSurfaceFluxColorHexForValue(412.5, rawModeScale));
assert.equal(rawLeafSurfaceFluxColorHexForValue(183, rawModeScale), "#22C55E");
assert.equal(rawLeafSurfaceFluxColorHexForValue(275, rawModeScale), "#A3E635");
assert.notEqual(rawLeafSurfaceFluxColorHexForValue(0.95 * 275, rawModeScale), "#F59E0B");
assert.notEqual(rawLeafSurfaceFluxColorHexForValue(1.05 * 275, rawModeScale), "#DC2626");
assert.ok(hexDistance(rawLeafSurfaceFluxColorHexForValue(1.25 * 275, rawModeScale), "#F59E0B") < 80);
assert.equal(rawLeafSurfaceFluxColorHexForValue(2.0 * 275, rawModeScale), "#DC2626");
const sameTargetDifferentSystemScale = {{
  ...rawModeScale,
  p05: 9000,
  p95: 12000,
  min: 9000,
  max: 12000,
}};
assert.equal(
  rawLeafSurfaceFluxColorHexForValue(275, rawModeScale),
  rawLeafSurfaceFluxColorHexForValue(275, sameTargetDifferentSystemScale),
);
assert.notEqual(
  rawLeafSurfaceFluxColorHexForValue(275, rawModeScale),
  rawLeafSurfaceFluxColorHexForValue(275, {{ ...rawModeScale, target_ppfd_umol_m2_s: 550, targetPpfd: 550 }}),
);
for (const hex of [0, 100, 200, 300, 1000].map((value) => rawLeafSurfaceFluxColorHexForValue(value, rawModeScale))) {{
  assert.notEqual(hex, "#FFFFFF");
  assert.notEqual(hex, "#800080");
}}
const rawModeGroup = createPlantGroup(rawModeScene);
const rawModeMesh = rawModeGroup.children[0];
const rawController = createPlantVisibilityController(rawModeGroup);
const rawTargetBefore = leafColor(rawModeMesh.userData.targetColorAttribute, 0);
const rawLowBefore = leafColor(rawModeMesh.userData.rawFluxColorAttribute, 0);
const rawMidBefore = leafColor(rawModeMesh.userData.rawFluxColorAttribute, 2);
const rawHighBefore = leafColor(rawModeMesh.userData.rawFluxColorAttribute, 4);
assertColorNearHex(rawTargetBefore, surfaceFluxColorHexForTargetDeviation(31.25, rawModeScene.plants.surface_flux));
assertColorNearHex(rawLowBefore, rawLeafSurfaceFluxColorHexForValue(0, rawModeScale));
assertColorNearHex(rawMidBefore, rawLeafSurfaceFluxColorHexForValue(200, rawModeScale));
assertColorNearHex(rawHighBefore, rawLeafSurfaceFluxColorHexForValue(1000, rawModeScale));
assert.ok(colorDistance(rawTargetBefore, rawLowBefore) > 0.2);
assert.ok(colorDistance(rawLowBefore, rawMidBefore) > 0.1);
assert.ok(colorDistance(rawMidBefore, rawHighBefore) > 0.1);
rawController.setColorMode(PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX);
assert.equal(rawModeMesh.geometry.getAttribute("color"), rawModeMesh.userData.rawFluxColorAttribute);
assert.equal(rawController.getState().colorMode, PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX);
assert.deepEqual(leafColor(rawModeMesh.geometry.getAttribute("color"), 0), rawLowBefore);
rawController.setAbsorptionColor(false);
assert.equal(rawModeMesh.geometry.getAttribute("color"), rawModeMesh.userData.defaultColorAttribute);
rawController.setAbsorptionColor(true);
assert.equal(rawModeMesh.geometry.getAttribute("color"), rawModeMesh.userData.rawFluxColorAttribute);
assert.equal(rawModeGroup.userData.hasSurfaceDetail, false);

function fourFaceLeaf(leafId) {{
  return {{
    plant_id: "plant_r000_c000",
    leaf_id: leafId,
    radiance_material_id: "plant_leaf_material",
    mesh: {{
      vertices: [
        [0, 0, 0.02],
        [0.12, 0, 0.03],
        [0.12, 0.12, 0.04],
        [0, 0.12, 0.05],
        [-0.08, 0.06, 0.03],
      ],
      faces: [[0, 1, 2], [0, 2, 3], [0, 3, 4], [0, 4, 1]],
    }},
  }};
}}

function meshByName(group, name) {{
  let match = null;
  group.traverse((child) => {{
    if (child.isMesh && child.name === name) {{
      match = child;
    }}
  }});
  return match;
}}

function uniqueColors(attribute) {{
  const values = [];
  const array = Array.from(attribute.array);
  for (let index = 0; index < array.length; index += 3) {{
    values.push(array.slice(index, index + 3).map((value) => Number(value.toFixed(4))).join(","));
  }}
  return new Set(values);
}}

function firstColor(attribute, vertexIndex = 0) {{
  const array = Array.from(attribute.array);
  const offset = vertexIndex * 3;
  return array.slice(offset, offset + 3);
}}

const quadratureScene = {{
  plants: {{
    schema: "rad_rebuild.fspm.plants.viewer.v1",
    units: "meters",
    material: {{ id: "plant_leaf_material", transmittance: 0.08 }},
    surface_flux: {{
      target_ppfd_umol_m2_s: 275,
      target_tolerance_umol_m2_s: 20,
      visualization: {{
        color_metric: "incident_photon_flux_density_umol_m2_s",
        raw_leaf_surface_flux_scale: rawModeScale,
        leaf_values: [
          {{
            leaf_id: "plant_r000_c000_leaf_detail",
            plant_id: "plant_r000_c000",
            lighting_region: "over_lit",
            target_classification_ppfd_umol_m2_s: 0,
            target_deviation: -999,
            incident_photon_flux_density_umol_m2_s: 275,
          }},
        ],
        raw_leaf_surface_flux_detail: {{
          mode: "raw_leaf_surface_flux",
          visual_granularity: "quadrature_mapped",
          receiver_granularity: "leaf_quadrature_4",
          encoding: "leaf_major_dense",
          leaf_count: 1,
          leaf_ids: ["plant_r000_c000_leaf_detail"],
          true_sample_count_per_leaf: 4,
          samples_per_leaf: 4,
          patches_per_leaf: 4,
          mesh_surface_rows_per_leaf: 4,
          sides: ["front"],
          side_policy: "single_light_facing_side",
          top_bottom_support: false,
          value_field: "incident_photon_flux_density_umol_m2_s",
          values_ppfd: [[0, 100, 275, 1000]],
          quadrature_face_sample_indices: [[0, 1, 2, 3]],
        }},
      }},
    }},
    plants: [
      {{
        plant_id: "plant_r000_c000",
        leaves: [fourFaceLeaf("plant_r000_c000_leaf_detail")],
      }},
    ],
  }},
}};
const quadratureGroup = createPlantGroup(quadratureScene);
const quadratureDetailMesh = meshByName(quadratureGroup, "plant-leaves-raw-surface-detail");
assert.ok(quadratureDetailMesh);
assert.equal(quadratureGroup.userData.hasSurfaceDetail, true);
assert.equal(quadratureGroup.userData.surfaceDetailMode, "quadrature_mapped");
assert.equal(quadratureGroup.userData.rawSurfaceDetail.encoding, "leaf_major_dense");
assert.equal("samples" in quadratureGroup.userData.rawSurfaceDetail, false);
assert.equal(quadratureGroup.userData.rawSurfaceDetailSampleCount, 4);
assert.equal(uniqueColors(quadratureDetailMesh.geometry.getAttribute("color")).size, 4);
const quadratureController = createPlantVisibilityController(quadratureGroup);
assert.equal(quadratureDetailMesh.visible, false);
quadratureController.setColorMode(PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX);
assert.equal(quadratureDetailMesh.visible, true);
assert.equal(meshByName(quadratureGroup, "plant-leaves-batched").visible, false);
assert.equal(quadratureController.getState().surfaceDetailMode, "quadrature_mapped");
quadratureController.setSurfaceDetail(false);
assert.equal(quadratureDetailMesh.visible, false);
assert.equal(meshByName(quadratureGroup, "plant-leaves-batched").visible, true);
assert.equal(
  meshByName(quadratureGroup, "plant-leaves-batched").geometry.getAttribute("color"),
  meshByName(quadratureGroup, "plant-leaves-batched").userData.rawFluxColorAttribute,
);
quadratureController.setColorMode(PLANT_COLOR_MODE_TARGET_RANGE);
assert.equal(quadratureDetailMesh.visible, false);
assert.equal(
  meshByName(quadratureGroup, "plant-leaves-batched").geometry.getAttribute("color"),
  meshByName(quadratureGroup, "plant-leaves-batched").userData.targetColorAttribute,
);

const meshPatchScene = structuredClone(quadratureScene);
meshPatchScene.plants.surface_flux.visualization.leaf_values[0] = {{
  leaf_id: "plant_r000_c000_leaf_detail",
  plant_id: "plant_r000_c000",
  lighting_region: "target_range",
  target_classification_ppfd_umol_m2_s: 999,
  target_deviation: 999,
  incident_photon_flux_density_umol_m2_s: 275,
}};
meshPatchScene.plants.surface_flux.visualization.raw_leaf_surface_flux_detail = {{
  mode: "raw_leaf_surface_flux",
  visual_granularity: "mesh_patch",
  receiver_granularity: "mesh_patch",
  encoding: "leaf_major_dense",
  leaf_count: 1,
  leaf_ids: ["plant_r000_c000_leaf_detail"],
  true_sample_count_per_leaf: 8,
  samples_per_leaf: 8,
  patches_per_leaf: 4,
  mesh_surface_rows_per_leaf: 4,
  sides: ["front", "back"],
  side_policy: "front_and_back_per_mesh_surface_row",
  top_bottom_support: true,
  value_field: "incident_photon_flux_density_umol_m2_s",
  values_ppfd: {{
    front: [[100, 100, 100, 100]],
    back: [[400, 400, 400, 400]],
  }},
  patch_face_indices: [[0, 1, 2, 3]],
}};
const meshPatchGroup = createPlantGroup(meshPatchScene);
const meshPatchDetailMesh = meshByName(meshPatchGroup, "plant-leaves-raw-surface-detail");
assert.ok(meshPatchDetailMesh);
assert.equal(meshPatchGroup.userData.surfaceDetailMode, "mesh_patch");
assert.equal(meshPatchGroup.userData.rawSurfaceDetail.encoding, "leaf_major_dense");
assert.equal("samples" in meshPatchGroup.userData.rawSurfaceDetail, false);
assert.equal(meshPatchGroup.userData.rawSurfaceDetailSampleCount, 8);
assert.ok(uniqueColors(meshPatchDetailMesh.geometry.getAttribute("color")).size >= 2);
assertColorNearHex(
  firstColor(meshPatchDetailMesh.geometry.getAttribute("color"), 0),
  rawLeafSurfaceFluxColorHexForValue(100, rawModeScale),
);
assertColorNearHex(
  firstColor(meshPatchDetailMesh.geometry.getAttribute("color"), 3),
  rawLeafSurfaceFluxColorHexForValue(400, rawModeScale),
);

const detailTargetScene = structuredClone(meshPatchScene);
detailTargetScene.plants.surface_flux.target_ppfd_umol_m2_s = 1000;
detailTargetScene.plants.surface_flux.visualization.raw_leaf_surface_flux_scale = {{
  ...rawModeScale,
  target_ppfd_umol_m2_s: 1000,
  target_source: "lighting_target_ppfd",
}};
detailTargetScene.plants.surface_flux.visualization.raw_leaf_surface_flux_detail.target_ppfd_umol_m2_s = 275;
detailTargetScene.plants.surface_flux.visualization.raw_leaf_surface_flux_detail.scale_basis = "fspm_target_ppfd_umol_m2_s";
detailTargetScene.plants.surface_flux.visualization.raw_leaf_surface_flux_detail.color_anchors = rawModeScale.anchors;
detailTargetScene.plants.surface_flux.visualization.raw_leaf_surface_flux_detail.values_ppfd = {{
  front: [[183, null, 266, 90]],
  back: [[10, 20, 30, 40]],
}};
detailTargetScene.plants.surface_flux.visualization.leaf_values[0].incident_photon_flux_density_umol_m2_s = 183;
const detailTargetGroup = createPlantGroup(detailTargetScene);
const detailTargetMesh = meshByName(detailTargetGroup, "plant-leaves-raw-surface-detail");
assert.ok(detailTargetMesh);
assert.equal(detailTargetGroup.userData.rawLeafSurfaceFluxScale.targetPpfd, 275);
assert.equal(detailTargetGroup.userData.rawLeafSurfaceFluxScale.targetSource, "fspm_target_ppfd_umol_m2_s");
assert.equal(rawLeafSurfaceFluxColorHexForValue(183, detailTargetGroup.userData.rawLeafSurfaceFluxScale), "#22C55E");
assert.notEqual(
  rawLeafSurfaceFluxColorHexForValue(183, detailTargetGroup.userData.rawLeafSurfaceFluxScale),
  rawLeafSurfaceFluxColorHexForValue(183, {{ ...rawModeScale, target_ppfd_umol_m2_s: 1000, targetPpfd: 1000 }}),
);
assert.ok(hexDistance(rawLeafSurfaceFluxColorHexForValue(90, detailTargetGroup.userData.rawLeafSurfaceFluxScale), "#2563EB") > 100);
assert.ok(hexDistance(rawLeafSurfaceFluxColorHexForValue(266, detailTargetGroup.userData.rawLeafSurfaceFluxScale), "#2563EB") > 300);
assertColorNearHex(
  firstColor(detailTargetMesh.geometry.getAttribute("color"), 0),
  rawLeafSurfaceFluxColorHexForValue(183, detailTargetGroup.userData.rawLeafSurfaceFluxScale),
);
assertColorNearHex(
  firstColor(detailTargetMesh.geometry.getAttribute("color"), 3),
  rawLeafSurfaceFluxColorHexForValue(10, detailTargetGroup.userData.rawLeafSurfaceFluxScale),
);
assertColorNearHex(
  firstColor(detailTargetMesh.geometry.getAttribute("color"), 6),
  rawLeafSurfaceFluxColorHexForValue(183, detailTargetGroup.userData.rawLeafSurfaceFluxScale),
);
assertColorNearHex(
  firstColor(detailTargetMesh.geometry.getAttribute("color"), 12),
  rawLeafSurfaceFluxColorHexForValue(266, detailTargetGroup.userData.rawLeafSurfaceFluxScale),
);

const invalidDetailScene = structuredClone(detailTargetScene);
invalidDetailScene.plants.surface_flux.visualization.raw_leaf_surface_flux_detail.values_ppfd = {{
  front: [[null, null, null, null]],
  back: [[null, null, null, null]],
}};
const invalidDetailGroup = createPlantGroup(invalidDetailScene);
assert.equal(meshByName(invalidDetailGroup, "plant-leaves-raw-surface-detail"), null);
const invalidDetailController = createPlantVisibilityController(invalidDetailGroup);
invalidDetailController.setColorMode(PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX);
assert.equal(invalidDetailController.getState().surfaceDetailMode, "leaf_average");
assert.equal(
  meshByName(invalidDetailGroup, "plant-leaves-batched").geometry.getAttribute("color"),
  meshByName(invalidDetailGroup, "plant-leaves-batched").userData.rawFluxColorAttribute,
);
assertColorNearHex(
  leafColor(meshByName(invalidDetailGroup, "plant-leaves-batched").userData.rawFluxColorAttribute, 0),
  rawLeafSurfaceFluxColorHexForValue(183, invalidDetailGroup.userData.rawLeafSurfaceFluxScale),
);

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

const geometryOnlyScene = {{
  plants: {{
    schema: "rad_rebuild.fspm.plants.viewer.v1",
    units: "meters",
    material: {{ id: "plant_leaf_material", transmittance: 0.08 }},
    plants: [
      {{
        plant_id: "plant_r000_c000",
        row: 0,
        column: 0,
        center_m: [0, 0, 0],
        leaves: [
          sceneLeaf("plant_r000_c000", "plant_r000_c000_leaf_000", 0),
        ],
      }},
    ],
  }},
}};
const geometryOnlyController = createPlantVisibilityController(createPlantGroup(geometryOnlyScene));
assert.equal(geometryOnlyController.getState().hasAbsorptionColor, false);
assert.equal(geometryOnlyController.getState().surfaceFluxAvailable, false);
assert.match(
  geometryOnlyController.getState().surfaceFluxUnavailableReason,
  /plant_surface_flux\\.json did not provide leaf color rows/,
);

const controller = createPlantVisibilityController(group);
const state = controller.getState();
assert.equal(state.visible, true);
assert.equal(state.absorptionColor, true);
assert.equal(state.hasAbsorptionColor, true);
assert.equal(state.plantCount, 1);
assert.equal(state.leafCount, 11);
assert.equal(state.colorMetric, "incident_photon_flux_density_umol_m2_s");
assert.equal(state.colorMode, "target_range");
assert.equal(state.rawLeafSurfaceFluxScale.mode, "raw_leaf_surface_flux");
assert.equal(state.rawLeafSurfaceFluxScale.scaleType, "target_normalized_ratio");
assert.equal(state.rawLeafSurfaceFluxScale.targetPpfd, 275);
assert.equal(state.rawLeafSurfaceFluxScale.targetSource, "fspm_target_ppfd_umol_m2_s");
assert.deepEqual(
  state.rawLeafSurfaceFluxScale.anchors.map((anchor) => anchor.ratio),
  [0, 0.2, 0.4, 0.55, 0.8, 1, 1.2, 1.5],
);
assert.deepEqual(
  state.rawLeafSurfaceFluxScale.anchors.map((anchor) => anchor.ppfd_umol_m2_s),
  [0, 55, 110, 151.25, 220, 275, 330, 412.5],
);
assert.equal(state.rawLeafSurfaceFluxLegend.title, "Raw leaf-surface incident PPFD");
assert.equal(state.rawLeafSurfaceFluxLegend.scale, "% of FSPM target");
assert.equal(state.rawLeafSurfaceFluxLegend.target_ppfd_umol_m2_s, 275);
assert.equal(state.rawLeafSurfaceFluxLegend.anchors.at(-1).label, "150%+");
assert.equal(state.surfaceFluxAvailable, true);
assert.equal(state.surfaceFluxUnavailableReason, "");

const target550Scene = structuredClone(scenePayload);
target550Scene.plants.surface_flux.target_ppfd_umol_m2_s = 550;
target550Scene.plants.surface_flux.target_tolerance_umol_m2_s = 20;
const target550State = createPlantVisibilityController(createPlantGroup(target550Scene)).getState();
assert.deepEqual(
  target550State.rawLeafSurfaceFluxLegend.anchors.map((anchor) => anchor.ppfd_umol_m2_s),
  [0, 110, 220, 302.5, 440, 550, 660, 825],
);
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
