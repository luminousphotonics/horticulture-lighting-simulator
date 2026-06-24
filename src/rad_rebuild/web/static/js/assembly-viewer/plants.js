// @ts-check

// @ts-ignore Static vendor module is served by Flask.
import * as THREE from "/static/vendor/three/three.module.js";

const PLANT_VIEWER_SCHEMA = "rad_rebuild.fspm.plants.viewer.v1";
const DEFAULT_LEAF_COLOR = 0x3fa66f;
const DEFAULT_ABSORPTION_INTENSITY = 0.5;

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

function absorptionColorForIntensity(value) {
  const intensity = clamp01(value);
  const color = new THREE.Color();
  // Low absorption: deeper green/teal. High absorption: brighter yellow-green.
  color.setHSL(0.40 - 0.26 * intensity, 0.72, 0.32 + 0.22 * intensity);
  return color;
}

function createLeafMaterial({ plantPayload, color }) {
  return new THREE.MeshStandardMaterial({
    color,
    roughness: 0.78,
    metalness: 0.0,
    side: THREE.DoubleSide,
    transparent: true,
    opacity: leafMaterialOpacity(plantPayload),
  });
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

function leafVisualIntensity(leaf, fluxByLeafId) {
  const leafId = typeof leaf?.leaf_id === "string" ? leaf.leaf_id : "";
  const row = leafId ? fluxByLeafId.get(leafId) : null;
  if (!row) {
    return null;
  }
  return clamp01(row.visual_intensity_0_1);
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
    color: DEFAULT_LEAF_COLOR,
  });
  const fluxByLeafId = leafFluxById(plantPayload);
  const colorMetric = plantPayload?.surface_flux?.visualization?.color_metric || "";
  let renderedLeafCount = 0;
  let absorptionColoredLeafCount = 0;

  for (const plant of plantPayload.plants) {
    const plantGroup = new THREE.Group();
    plantGroup.name = `plant-${plant?.plant_id || "unknown"}`;
    plantGroup.userData = {
      plantId: plant?.plant_id || null,
      row: plant?.row ?? null,
      column: plant?.column ?? null,
      centerM: Array.isArray(plant?.center_m) ? plant.center_m.slice(0, 3) : null,
    };
    const leaves = Array.isArray(plant?.leaves) ? plant.leaves : [];
    for (const leaf of leaves) {
      const geometry = createLeafGeometry(leaf);
      if (!geometry) {
        warnings.push(`${leaf?.leaf_id || "unknown leaf"} has invalid plant mesh data.`);
        continue;
      }
      const visualIntensity = leafVisualIntensity(leaf, fluxByLeafId);
      const absorptionMaterial = visualIntensity === null
        ? null
        : createLeafMaterial({
          plantPayload,
          color: absorptionColorForIntensity(visualIntensity),
        });
      if (absorptionMaterial) {
        absorptionColoredLeafCount += 1;
      }
      const mesh = new THREE.Mesh(geometry, absorptionMaterial || defaultMaterial);
      mesh.name = `plant-leaf-${leaf?.leaf_id || renderedLeafCount}`;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData = {
        plantId: leaf?.plant_id || plant?.plant_id || null,
        leafId: leaf?.leaf_id || null,
        radianceMaterialId: leaf?.radiance_material_id || null,
        visualIntensity,
        defaultMaterial,
        absorptionMaterial,
      };
      plantGroup.add(mesh);
      renderedLeafCount += 1;
    }
    group.add(plantGroup);
  }

  group.userData = {
    plantCount: counts.plantCount,
    leafCount: counts.leafCount,
    renderedLeafCount,
    absorptionColoredLeafCount,
    hasAbsorptionColor: absorptionColoredLeafCount > 0,
    colorMetric,
    warnings,
  };
  return group;
}
