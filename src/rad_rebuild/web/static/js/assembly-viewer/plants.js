// @ts-check

// @ts-ignore Static vendor module is served by Flask.
import * as THREE from "/static/vendor/three/three.module.js";

const PLANT_VIEWER_SCHEMA = "rad_rebuild.fspm.plants.viewer.v1";
const DEFAULT_LEAF_COLOR = 0x3fa66f;
const DEFAULT_ABSORPTION_INTENSITY = 0.5;
const DEFAULT_LEAF_THREE_COLOR = new THREE.Color(DEFAULT_LEAF_COLOR);
const SURFACE_FLUX_METRIC = "incident_photon_flux_density_umol_m2_s";
const TARGET_CLASSIFICATION_METRIC = "target_classification_ppfd_umol_m2_s";
const UNDER_TARGET_FLOOR_DEFICIT_UMOL_M2_S = 200;
const FALLBACK_UNDER_TARGET_FLOOR_DEVIATION = -10;
const DISPLAY_DEVIATION_K = 0.45;
const DISPLAY_DEVIATION_MAX_TAIL = 20;
const UNDER_TARGET_RAMP_ANCHORS = [
  { position: 0, color: "#3FA66F" },
  { position: 0.1388888889, color: "#41AA70" },
  { position: 0.2777777778, color: "#43AE71" },
  { position: 0.4166666667, color: "#43B670" },
  { position: 0.5555555556, color: "#43BE6E" },
  { position: 0.6944444444, color: "#44C66C" },
  { position: 0.8333333333, color: "#44C66C" },
  { position: 1, color: "#46CB6A" },
];
const ABOVE_TARGET_COLOR_ANCHORS = [
  { deviation: 0, color: "#4BCF6A" },
  { deviation: 0.1, color: "#59D16A" },
  { deviation: 0.25, color: "#6ED866" },
  { deviation: 0.5, color: "#90DD58" },
  { deviation: 0.75, color: "#AAE14E" },
  { deviation: 1, color: "#B7E455" },
  { deviation: 1.5, color: "#C9DF4F" },
  { deviation: 2, color: "#D8D747" },
  { deviation: 4, color: "#E6C33F" },
  { deviation: 8, color: "#F0A63A" },
  { deviation: 20, color: "#E26E26" },
];

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function finiteCoordinateTriple(value) {
  if (!Array.isArray(value) || value.length !== 3) {
    return null;
  }
  const x = finiteNumber(value[0]);
  const y = finiteNumber(value[1]);
  const z = finiteNumber(value[2]);
  return x === null || y === null || z === null ? null : [x, y, z];
}

function radianceVertexToWorld(vertex) {
  return [vertex[0], vertex[2], vertex[1]];
}

function normalizedFaceIndices(face, vertexCount) {
  if (!Array.isArray(face) || face.length < 3) {
    return null;
  }
  const indices = face.map((value) => Number(value));
  if (!indices.every((index) => Number.isInteger(index) && index >= 0 && index < vertexCount)) {
    return null;
  }
  return indices;
}

function triangulateFaces(faces, vertexCount) {
  const triangles = [];
  if (!Array.isArray(faces)) {
    return triangles;
  }
  for (const face of faces) {
    const indices = normalizedFaceIndices(face, vertexCount);
    if (!indices) {
      continue;
    }
    for (let index = 1; index < indices.length - 1; index += 1) {
      triangles.push(indices[0], indices[index], indices[index + 1]);
    }
  }
  return triangles;
}

function leafMaterialOpacity(plantPayload) {
  const transmittance = finiteNumber(plantPayload?.material?.transmittance);
  if (transmittance === null) {
    return 0.88;
  }
  return Math.min(0.96, Math.max(0.62, 0.9 - transmittance * 0.4));
}

function clamp01(value, fallback = DEFAULT_ABSORPTION_INTENSITY) {
  const number = finiteNumber(value);
  if (number === null) {
    return fallback;
  }
  return Math.min(1.0, Math.max(0.0, number));
}

function clamp(value, minValue, maxValue) {
  return Math.min(maxValue, Math.max(minValue, value));
}

function interpolatedColor(startColor, endColor, value) {
  return new THREE.Color().lerpColors(startColor, endColor, clamp01(value, 0));
}

function colorInterpolationPosition(value) {
  const number = finiteNumber(value);
  if (number === null || number === 0) {
    return 0;
  }
  const magnitude = Math.abs(number);
  if (magnitude <= 1) {
    return number;
  }
  const tailMagnitude = Math.asinh(DISPLAY_DEVIATION_K * (magnitude - 1))
    / Math.asinh(DISPLAY_DEVIATION_K * (DISPLAY_DEVIATION_MAX_TAIL - 1));
  return Math.sign(number) * (1 + (DISPLAY_DEVIATION_MAX_TAIL - 1) * clamp(tailMagnitude, 0, 1));
}

function underTargetFloorDeviation(surfaceFlux = {}) {
  const target = surfaceFluxTarget(surfaceFlux);
  const tolerance = target?.tolerance;
  if (tolerance && tolerance > 0) {
    return -UNDER_TARGET_FLOOR_DEFICIT_UMOL_M2_S / tolerance;
  }
  return FALLBACK_UNDER_TARGET_FLOOR_DEVIATION;
}

function anchorInterpolationPosition(anchor) {
  const displayDeviation = finiteNumber(anchor?.displayDeviation);
  if (displayDeviation !== null) {
    return displayDeviation;
  }
  return colorInterpolationPosition(anchor?.deviation);
}

function hexToRgb(hex) {
  const value = typeof hex === "string" ? hex.replace("#", "") : "";
  if (!/^[0-9a-fA-F]{6}$/.test(value)) {
    return { r: 0, g: 0, b: 0 };
  }
  return {
    r: Number.parseInt(value.slice(0, 2), 16),
    g: Number.parseInt(value.slice(2, 4), 16),
    b: Number.parseInt(value.slice(4, 6), 16),
  };
}

function rgbToHex({ r, g, b }) {
  return `#${
    [r, g, b]
      .map((value) => clamp(Math.round(value), 0, 255).toString(16).padStart(2, "0"))
      .join("")
      .toUpperCase()
  }`;
}

function interpolatedHexColor(startHex, endHex, value) {
  const t = clamp01(value, 0);
  const start = hexToRgb(startHex);
  const end = hexToRgb(endHex);
  return rgbToHex({
    r: start.r + (end.r - start.r) * t,
    g: start.g + (end.g - start.g) * t,
    b: start.b + (end.b - start.b) * t,
  });
}

function underTargetColorHex(value, surfaceFlux = {}) {
  const deviation = finiteNumber(value);
  const floorDeviation = underTargetFloorDeviation(surfaceFlux);
  if (deviation === null || deviation <= floorDeviation) {
    return UNDER_TARGET_RAMP_ANCHORS[0].color;
  }
  if (deviation >= -1) {
    return interpolatedHexColor("#46CB6A", "#4BCF6A", deviation + 1);
  }

  const rampPosition = clamp((deviation - floorDeviation) / (-1 - floorDeviation), 0, 1);
  for (let index = 1; index < UNDER_TARGET_RAMP_ANCHORS.length; index += 1) {
    const previous = UNDER_TARGET_RAMP_ANCHORS[index - 1];
    const next = UNDER_TARGET_RAMP_ANCHORS[index];
    if (rampPosition <= next.position) {
      return interpolatedHexColor(
        previous.color,
        next.color,
        (rampPosition - previous.position) / (next.position - previous.position),
      );
    }
  }
  return UNDER_TARGET_RAMP_ANCHORS[UNDER_TARGET_RAMP_ANCHORS.length - 1].color;
}

export function surfaceFluxColorHexForTargetDeviation(value, surfaceFlux = {}) {
  const deviation = finiteNumber(value);
  if (deviation === null) {
    return "#4BCF6A";
  }
  if (deviation < 0) {
    return underTargetColorHex(deviation, surfaceFlux);
  }

  const displayDeviation = colorInterpolationPosition(deviation);
  const firstAnchor = ABOVE_TARGET_COLOR_ANCHORS[0];
  const lastAnchor = ABOVE_TARGET_COLOR_ANCHORS[ABOVE_TARGET_COLOR_ANCHORS.length - 1];
  if (displayDeviation <= anchorInterpolationPosition(firstAnchor)) {
    return firstAnchor.color;
  }
  for (let index = 1; index < ABOVE_TARGET_COLOR_ANCHORS.length; index += 1) {
    const previous = ABOVE_TARGET_COLOR_ANCHORS[index - 1];
    const next = ABOVE_TARGET_COLOR_ANCHORS[index];
    const previousPosition = anchorInterpolationPosition(previous);
    const nextPosition = anchorInterpolationPosition(next);
    if (displayDeviation <= nextPosition) {
      return interpolatedHexColor(
        previous.color,
        next.color,
        (displayDeviation - previousPosition) / (nextPosition - previousPosition),
      );
    }
  }
  return lastAnchor.color;
}

function targetDeviationColor(value, surfaceFlux = {}) {
  return new THREE.Color(surfaceFluxColorHexForTargetDeviation(value, surfaceFlux));
}

function surfaceFluxTarget(surfaceFlux) {
  const target = finiteNumber(surfaceFlux?.target_ppfd_umol_m2_s);
  const tolerance = finiteNumber(surfaceFlux?.target_tolerance_umol_m2_s);
  if (target !== null && tolerance !== null && target > 0 && tolerance > 0) {
    return { target, tolerance };
  }

  const lower = finiteNumber(surfaceFlux?.target_lower_threshold_umol_m2_s);
  const upper = finiteNumber(surfaceFlux?.target_upper_threshold_umol_m2_s);
  if (lower !== null && upper !== null && upper > lower) {
    return {
      target: (lower + upper) / 2,
      tolerance: (upper - lower) / 2,
    };
  }
  return null;
}

function surfaceFluxLeafMetric(row, colorMetric) {
  const classificationValue = finiteNumber(row?.[TARGET_CLASSIFICATION_METRIC]);
  if (classificationValue !== null) {
    return classificationValue;
  }
  const incidentValue = finiteNumber(row?.[SURFACE_FLUX_METRIC]);
  if (incidentValue !== null) {
    return incidentValue;
  }
  const metricValue = typeof colorMetric === "string" && colorMetric
    ? finiteNumber(row?.[colorMetric])
    : null;
  return metricValue;
}

export function surfaceFluxTargetDeviationForLeafValue(row, surfaceFlux = {}, colorMetric = "") {
  const rowDeviation = finiteNumber(row?.target_deviation);
  if (rowDeviation !== null) {
    return rowDeviation;
  }

  const target = surfaceFluxTarget(surfaceFlux);
  const value = surfaceFluxLeafMetric(row, colorMetric);
  if (!target || value === null) {
    return null;
  }
  return (value - target.target) / target.tolerance;
}

function colorForLightingRegion(row) {
  const intensity = clamp01(row?.visual_intensity_0_1);
  const region = typeof row?.lighting_region === "string" ? row.lighting_region : "";
  if (region === "in_target" || region === "in_range" || region === "target_range") {
    return targetDeviationColor(0);
  }
  if (region === "over_target" || region === "over_lit" || region === "above_target") {
    return targetDeviationColor(1 + intensity * 7);
  }
  return interpolatedColor(DEFAULT_LEAF_THREE_COLOR, targetDeviationColor(-1), intensity);
}

function absorptionColorForLeaf(row, surfaceFlux, colorMetric) {
  if (!row) {
    return DEFAULT_LEAF_THREE_COLOR;
  }

  const targetDeviation = surfaceFluxTargetDeviationForLeafValue(row, surfaceFlux, colorMetric);
  if (targetDeviation === null) {
    return colorForLightingRegion(row);
  }

  return targetDeviationColor(targetDeviation, surfaceFlux);
}

function createLeafMaterial({ plantPayload, color, vertexColors = false }) {
  return new THREE.MeshStandardMaterial({
    color,
    vertexColors,
    roughness: 0.78,
    metalness: 0.0,
    side: THREE.DoubleSide,
    transparent: true,
    opacity: leafMaterialOpacity(plantPayload),
  });
}

function createAbsorptionLeafMaterial(plantPayload) {
  const material = new THREE.MeshBasicMaterial({
    color: 0xffffff,
    vertexColors: true,
    side: THREE.DoubleSide,
    transparent: true,
    opacity: leafMaterialOpacity(plantPayload),
  });
  material.toneMapped = false;
  return material;
}

function surfaceFluxLeafValues(plantPayload) {
  const values = plantPayload?.surface_flux?.visualization?.leaf_values;
  return Array.isArray(values) ? values : [];
}

function leafFluxById(plantPayload) {
  const map = new Map();
  for (const row of surfaceFluxLeafValues(plantPayload)) {
    const leafId = typeof row?.leaf_id === "string" ? row.leaf_id : "";
    if (!leafId) {
      continue;
    }
    map.set(leafId, row);
  }
  return map;
}

function leafFluxRow(leaf, fluxByLeafId) {
  const leafId = typeof leaf?.leaf_id === "string" ? leaf.leaf_id : "";
  return leafId ? fluxByLeafId.get(leafId) || null : null;
}

function plantCounts(plants) {
  const plantCount = plants.length;
  let leafCount = 0;
  for (const plant of plants) {
    leafCount += Array.isArray(plant?.leaves) ? plant.leaves.length : 0;
  }
  return { plantCount, leafCount };
}

export function hasPlantPayload(scenePayload) {
  return scenePayload?.plants?.schema === PLANT_VIEWER_SCHEMA
    && Array.isArray(scenePayload.plants.plants)
    && scenePayload.plants.plants.length > 0;
}

export function summarizePlantPayload(scenePayload) {
  if (!hasPlantPayload(scenePayload)) {
    return { plantCount: 0, leafCount: 0 };
  }
  return plantCounts(scenePayload.plants.plants);
}

export function createPlantVisibilityController(plantGroup) {
  if (!plantGroup || typeof plantGroup !== "object") {
    throw new TypeError("A plant group object is required.");
  }
  plantGroup.visible = plantGroup.visible !== false;
  const hasAbsorptionColor = Boolean(plantGroup.userData?.hasAbsorptionColor);
  let absorptionColor = hasAbsorptionColor;

  function applyAbsorptionColor() {
    plantGroup.traverse((child) => {
      if (!(child instanceof THREE.Mesh)) {
        return;
      }
      const defaultColorAttribute = child.userData?.defaultColorAttribute;
      const absorptionColorAttribute = child.userData?.absorptionColorAttribute;
      if (defaultColorAttribute && absorptionColorAttribute && child.geometry instanceof THREE.BufferGeometry) {
        child.geometry.setAttribute("color", absorptionColor ? absorptionColorAttribute : defaultColorAttribute);
        child.geometry.attributes.color.needsUpdate = true;
        const defaultMaterial = child.userData?.defaultMaterial;
        const absorptionMaterial = child.userData?.absorptionMaterial;
        if (defaultMaterial && absorptionMaterial) {
          child.material = absorptionColor ? absorptionMaterial : defaultMaterial;
        }
        return;
      }
      const defaultMaterial = child.userData?.defaultMaterial;
      const absorptionMaterial = child.userData?.absorptionMaterial;
      if (!defaultMaterial || !absorptionMaterial) {
        return;
      }
      child.material = absorptionColor ? absorptionMaterial : defaultMaterial;
    });
  }

  function setVisible(value) {
    plantGroup.visible = Boolean(value);
    return getState();
  }

  function setAbsorptionColor(value) {
    absorptionColor = hasAbsorptionColor && Boolean(value);
    applyAbsorptionColor();
    return getState();
  }

  function getState() {
    return {
      visible: plantGroup.visible !== false,
      absorptionColor,
      hasAbsorptionColor,
      plantCount: Number(plantGroup.userData?.plantCount || 0),
      leafCount: Number(plantGroup.userData?.leafCount || 0),
      colorMetric: plantGroup.userData?.colorMetric || "",
    };
  }

  applyAbsorptionColor();
  return { setVisible, setAbsorptionColor, getState };
}

export function createLeafGeometry(leaf) {
  const sourceVertices = Array.isArray(leaf?.mesh?.vertices) ? leaf.mesh.vertices : [];
  const vertices = sourceVertices.map(finiteCoordinateTriple);
  if (vertices.some((vertex) => vertex === null)) {
    return null;
  }
  const triangles = triangulateFaces(leaf?.mesh?.faces, vertices.length);
  if (!vertices.length || triangles.length < 3) {
    return null;
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(vertices.flatMap((vertex) => radianceVertexToWorld(vertex)), 3),
  );
  geometry.setIndex(triangles);
  geometry.computeVertexNormals();
  geometry.computeBoundingBox();
  return geometry;
}

function appendLeafGeometryBuffers({ leaf, absorptionColor, positions, indices, defaultColors, absorptionColors }) {
  const sourceVertices = Array.isArray(leaf?.mesh?.vertices) ? leaf.mesh.vertices : [];
  const vertices = sourceVertices.map(finiteCoordinateTriple);
  if (vertices.some((vertex) => vertex === null)) {
    return false;
  }
  const triangles = triangulateFaces(leaf?.mesh?.faces, vertices.length);
  if (!vertices.length || triangles.length < 3) {
    return false;
  }

  const vertexOffset = positions.length / 3;
  for (const vertex of vertices) {
    positions.push(...radianceVertexToWorld(vertex));
    defaultColors.push(DEFAULT_LEAF_THREE_COLOR.r, DEFAULT_LEAF_THREE_COLOR.g, DEFAULT_LEAF_THREE_COLOR.b);
    absorptionColors.push(absorptionColor.r, absorptionColor.g, absorptionColor.b);
  }
  for (const index of triangles) {
    indices.push(vertexOffset + index);
  }
  return true;
}

export function createPlantGroup(scenePayload) {
  const group = new THREE.Group();
  group.name = "plant-geometry";
  if (!hasPlantPayload(scenePayload)) {
    group.visible = false;
    group.userData = { plantCount: 0, leafCount: 0, renderedLeafCount: 0, warnings: [] };
    return group;
  }

  const plantPayload = scenePayload.plants;
  const counts = summarizePlantPayload(scenePayload);
  const warnings = [];
  const defaultMaterial = createLeafMaterial({
    plantPayload,
    color: 0xffffff,
    vertexColors: true,
  });
  const absorptionMaterial = createAbsorptionLeafMaterial(plantPayload);
  const fluxByLeafId = leafFluxById(plantPayload);
  const colorMetric = plantPayload?.surface_flux?.visualization?.color_metric || "";
  const surfaceFlux = plantPayload?.surface_flux || {};
  let renderedLeafCount = 0;
  let absorptionColoredLeafCount = 0;
  let matchedLeafCount = 0;
  let unmatchedLeafCount = 0;
  let targetDeviationLeafCount = 0;
  let legacyFallbackLeafCount = 0;
  let defaultColorLeafCount = 0;
  const positions = [];
  const indices = [];
  const defaultColors = [];
  const absorptionColors = [];

  for (const plant of plantPayload.plants) {
    const leaves = Array.isArray(plant?.leaves) ? plant.leaves : [];
    for (const leaf of leaves) {
      const fluxRow = leafFluxRow(leaf, fluxByLeafId);
      const targetDeviation = surfaceFluxTargetDeviationForLeafValue(fluxRow, surfaceFlux, colorMetric);
      const absorptionColor = absorptionColorForLeaf(fluxRow, surfaceFlux, colorMetric);
      const rendered = appendLeafGeometryBuffers({
        leaf,
        absorptionColor,
        positions,
        indices,
        defaultColors,
        absorptionColors,
      });
      if (!rendered) {
        warnings.push(`${leaf?.leaf_id || "unknown leaf"} has invalid plant mesh data.`);
        continue;
      }
      if (fluxRow !== null) {
        matchedLeafCount += 1;
        absorptionColoredLeafCount += 1;
        if (targetDeviation !== null) {
          targetDeviationLeafCount += 1;
        } else {
          legacyFallbackLeafCount += 1;
        }
      } else {
        unmatchedLeafCount += 1;
        defaultColorLeafCount += 1;
      }
      renderedLeafCount += 1;
    }
  }

  if (renderedLeafCount > 0) {
    const geometry = new THREE.BufferGeometry();
    const defaultColorAttribute = new THREE.Float32BufferAttribute(defaultColors, 3);
    const absorptionColorAttribute = new THREE.Float32BufferAttribute(absorptionColors, 3);
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute("color", defaultColorAttribute);
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();

    const mesh = new THREE.Mesh(geometry, defaultMaterial);
    mesh.name = "plant-leaves-batched";
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.userData = {
      batched: true,
      renderedLeafCount,
      defaultMaterial,
      absorptionMaterial,
      defaultColorAttribute,
      absorptionColorAttribute,
    };
    group.add(mesh);
  }

  group.userData = {
    plantCount: counts.plantCount,
    leafCount: counts.leafCount,
    renderedLeafCount,
    absorptionColoredLeafCount,
    matchedLeafCount,
    unmatchedLeafCount,
    targetDeviationLeafCount,
    legacyFallbackLeafCount,
    defaultColorLeafCount,
    hasAbsorptionColor: absorptionColoredLeafCount > 0,
    colorMetric,
    warnings,
  };
  return group;
}
