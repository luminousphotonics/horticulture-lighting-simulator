// @ts-check

// @ts-ignore Static vendor module is served by Flask.
import * as THREE from "/static/vendor/three/three.module.js";

const PROPOSED_LED_SYSTEM = "proposed_led_system";
const DEFAULT_PROPOSED_LED_TUNING = Object.freeze({
  fixtureBrightness: 1.35,
  emissiveLift: 0.08,
  maxMetalness: 0.35,
  maxRoughness: 0.58,
});
const MATERIAL_TUNING_KEY = "proposed-led-fixture-visibility-v1";

export function createCadMaterials() {
  return {
    floor: new THREE.MeshStandardMaterial({
      color: 0x1b252f,
      roughness: 0.82,
      metalness: 0.0,
      transparent: true,
      opacity: 0.72,
    }),
    canopy: new THREE.MeshStandardMaterial({
      color: 0x5ab6a4,
      roughness: 0.65,
      metalness: 0.0,
      transparent: true,
      opacity: 0.18,
      side: THREE.DoubleSide,
    }),
    boundary: new THREE.LineBasicMaterial({ color: 0x8fb7c9 }),
    grid: new THREE.LineBasicMaterial({ color: 0x31414f, transparent: true, opacity: 0.7 }),
  };
}

function finiteNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function positiveNumber(value, fallback) {
  const number = finiteNumber(value, fallback);
  return number > 0 ? number : fallback;
}

function nonNegativeNumber(value, fallback) {
  const number = finiteNumber(value, fallback);
  return number >= 0 ? number : fallback;
}

function proposedLedTuning(scenePayload) {
  const system = String(scenePayload?.system || "");
  const mode = String(scenePayload?.mode || "");
  if (system !== PROPOSED_LED_SYSTEM && mode !== "SMD") {
    return null;
  }
  const renderTuning = scenePayload?.render_tuning && typeof scenePayload.render_tuning === "object"
    ? scenePayload.render_tuning
    : {};
  return {
    fixtureBrightness: positiveNumber(renderTuning.fixture_brightness, DEFAULT_PROPOSED_LED_TUNING.fixtureBrightness),
    emissiveLift: nonNegativeNumber(renderTuning.emissive_lift, DEFAULT_PROPOSED_LED_TUNING.emissiveLift),
    maxMetalness: nonNegativeNumber(renderTuning.max_metalness, DEFAULT_PROPOSED_LED_TUNING.maxMetalness),
    maxRoughness: positiveNumber(renderTuning.max_roughness, DEFAULT_PROPOSED_LED_TUNING.maxRoughness),
  };
}

function tuningSignature(tuning) {
  return [
    MATERIAL_TUNING_KEY,
    tuning.fixtureBrightness,
    tuning.emissiveLift,
    tuning.maxMetalness,
    tuning.maxRoughness,
  ].join(":");
}

function tunedMaterial(material, tuning, signature) {
  if (!material || material.userData?.radRebuildMaterialTuning === signature) {
    return material;
  }
  const nextMaterial = typeof material.clone === "function" ? material.clone() : material;
  if (nextMaterial.userData?.radRebuildMaterialTuning === signature) {
    return nextMaterial;
  }
  nextMaterial.userData = {
    ...(nextMaterial.userData || {}),
    radRebuildMaterialTuning: signature,
  };
  if (nextMaterial.color?.isColor) {
    nextMaterial.color.r = Math.min(1, nextMaterial.color.r * tuning.fixtureBrightness);
    nextMaterial.color.g = Math.min(1, nextMaterial.color.g * tuning.fixtureBrightness);
    nextMaterial.color.b = Math.min(1, nextMaterial.color.b * tuning.fixtureBrightness);
  }
  if (nextMaterial.emissive?.isColor && tuning.emissiveLift > 0) {
    nextMaterial.emissive.r = Math.min(1, nextMaterial.emissive.r + tuning.emissiveLift);
    nextMaterial.emissive.g = Math.min(1, nextMaterial.emissive.g + tuning.emissiveLift);
    nextMaterial.emissive.b = Math.min(1, nextMaterial.emissive.b + tuning.emissiveLift);
    if ("emissiveIntensity" in nextMaterial) {
      nextMaterial.emissiveIntensity = Math.max(Number(nextMaterial.emissiveIntensity) || 1, 1);
    }
  }
  if ("metalness" in nextMaterial) {
    nextMaterial.metalness = Math.min(nextMaterial.metalness ?? DEFAULT_PROPOSED_LED_TUNING.maxMetalness, tuning.maxMetalness);
  }
  if ("roughness" in nextMaterial) {
    nextMaterial.roughness = Math.min(nextMaterial.roughness ?? tuning.maxRoughness, tuning.maxRoughness);
  }
  nextMaterial.needsUpdate = true;
  return nextMaterial;
}

export function tuneProposedLedFixtureMaterials(root, tuning = DEFAULT_PROPOSED_LED_TUNING) {
  const signature = tuningSignature(tuning);
  let materialCount = 0;
  root.traverse((node) => {
    if (!node.isMesh || !node.material) {
      return;
    }
    if (Array.isArray(node.material)) {
      node.material = node.material.map((material) => {
        const nextMaterial = tunedMaterial(material, tuning, signature);
        if (nextMaterial !== material || nextMaterial?.userData?.radRebuildMaterialTuning === signature) {
          materialCount += 1;
        }
        return nextMaterial;
      });
      return;
    }
    const originalMaterial = node.material;
    node.material = tunedMaterial(originalMaterial, tuning, signature);
    if (node.material !== originalMaterial || node.material?.userData?.radRebuildMaterialTuning === signature) {
      materialCount += 1;
    }
  });
  return { materialCount };
}

export function applySystemMaterialTuning(scenePayload, object3d) {
  const tuning = proposedLedTuning(scenePayload);
  if (!tuning || !object3d) {
    return { applied: false, materialCount: 0 };
  }
  const result = tuneProposedLedFixtureMaterials(object3d, tuning);
  return { applied: true, materialCount: result.materialCount };
}

export function normalizeFixtureMaterials(root) {
  root.traverse((node) => {
    if (!node.isMesh) {
      return;
    }
    node.castShadow = true;
    node.receiveShadow = true;
    const materials = Array.isArray(node.material) ? node.material : [node.material];
    for (const material of materials) {
      if (material && "metalness" in material) {
        material.metalness = Math.min(material.metalness ?? 0.2, 0.45);
        material.roughness = Math.max(material.roughness ?? 0.55, 0.48);
      }
    }
  });
}
