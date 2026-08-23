import * as THREE from "three";
import {
  sampleBilinearScalarGrid,
  validatePpfdHeatmapSceneReference,
} from "./ppfd-heatmap.js";

export const TARGET_COVERAGE_SCHEMA_ID = "fspm-optics.viewer-target-coverage";
export const TARGET_COVERAGE_SCHEMA_VERSION = 1;
export const TARGET_CLASSIFICATION_BASIS = "canopy_plane_equivalent_incident_ppfd";
export const TARGET_CLASSIFICATION_SOURCE = "interpolated_runtime_ppfd_map";
export const TARGET_COVERAGE_LIMITATION =
  "Baseline canopy-plane coverage evaluated at authenticated leaf locations—not measured leaf incident PAR.";
export const TARGET_COVERAGE_PALETTE = Object.freeze([
  Object.freeze({ deviation: -4, color_name: "blue", srgb_hex: "#2563EB" }),
  Object.freeze({ deviation: -2, color_name: "cyan", srgb_hex: "#06B6D4" }),
  Object.freeze({ deviation: -1.5, color_name: "teal", srgb_hex: "#14B8A6" }),
  Object.freeze({ deviation: -1, color_name: "green", srgb_hex: "#22C55E" }),
  Object.freeze({ deviation: 1, color_name: "green", srgb_hex: "#22C55E" }),
  Object.freeze({ deviation: 2, color_name: "yellow-green", srgb_hex: "#A3E635" }),
  Object.freeze({ deviation: 4, color_name: "yellow-orange", srgb_hex: "#F59E0B" }),
  Object.freeze({ deviation: 6, color_name: "red", srgb_hex: "#DC2626" }),
]);

const CONTRACT_FIELDS = Object.freeze([
  "availability",
  "canonical_leaf_geometry",
  "deviation_formula",
  "palette",
  "parent_source_field_identity_sha256",
  "reference",
  "sampling",
  "schema_id",
  "schema_version",
  "scientific_limitation",
  "system_id",
  "target_band_deviation",
  "target_classification_basis",
  "target_classification_source",
  "tolerance_ppfd_umol_m2_s",
  "unavailable_reason_code",
]);

export function validateTargetCoverageSceneReference(scene, metadata = null) {
  const source = validatePpfdHeatmapSceneReference(scene);
  const contract = source.target_coverage;
  assertExactObject(contract, CONTRACT_FIELDS, "Target Coverage contract");
  const available = contract.availability === "available";
  if (
    contract.schema_id !== TARGET_COVERAGE_SCHEMA_ID
    || contract.schema_version !== TARGET_COVERAGE_SCHEMA_VERSION
    || !["available", "unavailable"].includes(contract.availability)
    || (available && contract.unavailable_reason_code !== null)
    || (!available && contract.unavailable_reason_code
      !== "leaf_representative_position_outside_stage_a_support")
    || contract.target_classification_basis !== TARGET_CLASSIFICATION_BASIS
    || contract.target_classification_source !== TARGET_CLASSIFICATION_SOURCE
    || contract.scientific_limitation !== TARGET_COVERAGE_LIMITATION
    || contract.deviation_formula
      !== "(coverage_ppfd - reference_ppfd) / tolerance_ppfd"
    || contract.parent_source_field_identity_sha256
      !== source.source_field_identity_sha256
  ) {
    throw new Error("Target Coverage identity is incompatible.");
  }

  validateReference(contract);
  assertExactObject(
    contract.target_band_deviation,
    ["bounds", "maximum", "minimum"],
    "Target Coverage target band",
  );
  if (contract.target_band_deviation.minimum !== -1
      || contract.target_band_deviation.maximum !== 1
      || contract.target_band_deviation.bounds !== "inclusive") {
    throw new Error("Target Coverage target band is incompatible.");
  }
  validatePalette(contract.palette);
  validateSampling(contract.sampling, source, metadata);
  validateCanonicalGeometry(contract.canonical_leaf_geometry);
  return contract;
}

export function resolveTargetCoverageSamplePosition(contract, translation, leafIndex) {
  if (!Number.isSafeInteger(leafIndex) || leafIndex < 0 || leafIndex >= 12
      || !Array.isArray(translation) || translation.length !== 3
      || translation.some((value) => !Number.isFinite(value))) {
    throw new Error("Target Coverage leaf transform is incompatible.");
  }
  const centroid = contract.canonical_leaf_geometry
    .representative_centroids_simulation_xy_m[leafIndex];
  const simulationX = centroid[0] + translation[0];
  const simulationY = centroid[1] - translation[2];
  const fieldX = simulationX;
  const fieldY = simulationY;
  const requestedX = contract.sampling.axes_swapped_from_requested_room
    ? -simulationY : simulationX;
  const requestedY = contract.sampling.axes_swapped_from_requested_room
    ? simulationX : simulationY;
  const bounds = contract.sampling.support_bounds_m;
  return Object.freeze({
    fieldX,
    fieldY,
    insideSupport: fieldX >= bounds.x_min && fieldX <= bounds.x_max
      && fieldY >= bounds.y_min && fieldY <= bounds.y_max,
    requestedX, requestedY, simulationX, simulationY,
  });
}

export function sampleTargetCoverageAtLeaf(grid, contract, translation, leafIndex) {
  const position = resolveTargetCoverageSamplePosition(contract, translation, leafIndex);
  if (!position.insideSupport) {
    throw new Error("Target Coverage leaf position is outside authenticated support.");
  }
  const coveragePpfd = sampleBilinearScalarGrid(grid, position.fieldX, position.fieldY);
  const deviation = (coveragePpfd - contract.reference.ppfd_umol_m2_s)
    / contract.tolerance_ppfd_umol_m2_s;
  return Object.freeze({ coveragePpfd, deviation, position });
}

export function targetCoverageColorAtDeviation(deviation) {
  if (!Number.isFinite(deviation)) {
    throw new Error("Target Coverage deviation must be finite.");
  }
  const bounded = Math.min(6, Math.max(-4, deviation));
  let lower = TARGET_COVERAGE_PALETTE[0];
  for (const upper of TARGET_COVERAGE_PALETTE.slice(1)) {
    if (bounded <= upper.deviation) {
      const fraction = (bounded - lower.deviation) / (upper.deviation - lower.deviation);
      const color = new THREE.Color(lower.srgb_hex).lerp(
        new THREE.Color(upper.srgb_hex), fraction,
      );
      return Object.freeze(hexIntegerToRgb(color.getHex(THREE.SRGBColorSpace)));
    }
    lower = upper;
  }
  return Object.freeze(hexIntegerToRgb(
    new THREE.Color(TARGET_COVERAGE_PALETTE.at(-1).srgb_hex)
      .getHex(THREE.SRGBColorSpace),
  ));
}

export function createTargetCoverageColorController({
  surface,
  resources,
  contract,
  translations,
}) {
  clearTargetCoverageColoring(surface);
  validateControllerInputs(surface, resources, contract, translations);
  const material = surface.material;
  const geometry = contract.canonical_leaf_geometry;
  const bounds = contract.sampling.support_bounds_m;
  const uniforms = {
    targetCoverageEnabled: { value: 1 },
    targetCoverageScalarMap: { value: resources.scalarTexture },
    targetCoverageScalarDimensions: {
      value: new THREE.Vector2(resources.grid.width, resources.grid.height),
    },
    targetCoverageSupportBounds: {
      value: new THREE.Vector4(bounds.x_min, bounds.x_max, bounds.y_min, bounds.y_max),
    },
    targetCoverageReference: { value: contract.reference.ppfd_umol_m2_s },
    targetCoverageTolerance: { value: contract.tolerance_ppfd_umol_m2_s },
    targetCoverageLeafCentroids: {
      value: geometry.representative_centroids_simulation_xy_m.map(
        ([x, y]) => new THREE.Vector2(x, y),
      ),
    },
    targetCoverageAnchors: {
      value: TARGET_COVERAGE_PALETTE.map((anchor) => anchor.deviation),
    },
    targetCoveragePalette: {
      value: TARGET_COVERAGE_PALETTE.map(
        (anchor) => new THREE.Color(anchor.srgb_hex),
      ),
    },
  };
  const originalCompile = material.onBeforeCompile;
  const originalCacheKey = material.customProgramCacheKey;
  const hasLeafMaterial = Boolean(surface.userData.leafMaterialController);
  const fragmentColoring = hasLeafMaterial
    ? FRAGMENT_COLORING_D4 : FRAGMENT_COLORING;
  const compile = (shader, renderer) => {
    originalCompile.call(material, shader, renderer);
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = injectAfter(
      shader.vertexShader,
      "#include <common>",
      `${hasLeafMaterial ? "" : "attribute float _leaf_index;"}\n${VERTEX_DECLARATIONS}`,
    );
    shader.vertexShader = injectAfter(
      shader.vertexShader, "#include <begin_vertex>", VERTEX_LOOKUP,
    );
    shader.fragmentShader = injectAfter(
      shader.fragmentShader, "#include <common>", FRAGMENT_DECLARATIONS,
    );
    shader.fragmentShader = injectAfter(
      shader.fragmentShader, "#include <color_fragment>", fragmentColoring,
    );
    material.userData.targetCoverageShader = shader;
  };
  material.onBeforeCompile = compile;
  material.customProgramCacheKey = () => (
    `${originalCacheKey.call(material)}|phase27g-d6-target-coverage-v1`
  );
  material.needsUpdate = true;

  let disposed = false;
  const controller = Object.freeze({
    setEnabled(value) {
      if (!disposed) uniforms.targetCoverageEnabled.value = value ? 1 : 0;
    },
    setScalarResources(nextResources) {
      if (disposed) return;
      validateSharedScalarResources(nextResources, contract);
      uniforms.targetCoverageScalarMap.value = nextResources.scalarTexture;
      uniforms.targetCoverageScalarDimensions.value.set(
        nextResources.grid.width, nextResources.grid.height,
      );
      const shader = material.userData.targetCoverageShader;
      if (shader?.uniforms?.targetCoverageScalarMap) {
        shader.uniforms.targetCoverageScalarMap.value = nextResources.scalarTexture;
      }
    },
    getState() {
      return Object.freeze({
        disposed,
        enabled: uniforms.targetCoverageEnabled.value === 1,
        sourceFieldIdentity: contract.parent_source_field_identity_sha256,
      });
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      uniforms.targetCoverageEnabled.value = 0;
      uniforms.targetCoverageScalarMap.value = null;
      const shader = material.userData.targetCoverageShader;
      if (shader?.uniforms?.targetCoverageScalarMap) {
        shader.uniforms.targetCoverageScalarMap.value = null;
      }
      uniforms.targetCoverageLeafCentroids.value = null;
      uniforms.targetCoveragePalette.value = null;
      if (material.onBeforeCompile === compile) material.onBeforeCompile = originalCompile;
      material.customProgramCacheKey = originalCacheKey;
      delete material.userData.targetCoverageShader;
      if (surface.userData.targetCoverageController === controller) {
        delete surface.userData.targetCoverageController;
      }
      material.needsUpdate = true;
    },
  });
  surface.userData.targetCoverageController = controller;
  return controller;
}

export function clearTargetCoverageColoring(surface) {
  surface?.userData?.targetCoverageController?.dispose?.();
}

function validateControllerInputs(surface, resources, contract, translations) {
  if (!(surface instanceof THREE.InstancedMesh)
      || !(materialIsSupported(surface.material))
      || surface.count !== translations?.count
      || !(translations.values instanceof Float32Array)
      || translations.values.length !== translations.count * 3
      || contract.availability !== "available") {
    throw new Error("Target Coverage GPU inputs are incompatible.");
  }
  validateSharedScalarResources(resources, contract);
  for (let plant = 0; plant < translations.count; plant += 1) {
    const offset = plant * 3;
    const translation = [
      translations.values[offset],
      translations.values[offset + 1],
      translations.values[offset + 2],
    ];
    for (let leaf = 0; leaf < 12; leaf += 1) {
      if (!resolveTargetCoverageSamplePosition(contract, translation, leaf).insideSupport) {
        throw new Error(
          "Target Coverage displayed leaf position is outside authenticated support.",
        );
      }
    }
  }
}

function validateSharedScalarResources(resources, contract) {
  const texture = resources?.scalarTexture;
  const grid = resources?.grid;
  const source = resources?.source;
  const bounds = contract.sampling.support_bounds_m;
  if (!texture?.isDataTexture || texture.image?.data !== grid?.values
      || texture.image?.width !== grid.width || texture.image?.height !== grid.height
      || source?.source_field_identity_sha256
        !== contract.parent_source_field_identity_sha256
      || !recordsEqual(grid.bounds, {
        x_min: bounds.x_min, x_max: bounds.x_max,
        y_min: bounds.y_min, y_max: bounds.y_max,
      })) {
    throw new Error("Target Coverage shared PPFD texture is incompatible.");
  }
}

function validateReference(contract) {
  const reference = contract.reference;
  assertExactObject(
    reference, ["policy_mode", "ppfd_umol_m2_s", "source"],
    "Target Coverage reference",
  );
  const automaticSource = contract.system_id === "hps"
    ? "achieved_stage_a_baseline_mean" : "requested_lighting_target";
  const expectedSource = reference.policy_mode === "override"
    ? "authenticated_fspm_override" : automaticSource;
  if (!["proposed", "conventional", "hps"].includes(contract.system_id)
      || !["automatic", "override"].includes(reference.policy_mode)
      || reference.source !== expectedSource
      || !Number.isFinite(reference.ppfd_umol_m2_s)
      || reference.ppfd_umol_m2_s < 0
      || (reference.ppfd_umol_m2_s === 0 && contract.system_id === "hps")
      || !Number.isFinite(contract.tolerance_ppfd_umol_m2_s)
      || contract.tolerance_ppfd_umol_m2_s <= 0) {
    throw new Error("Target Coverage reference policy is incompatible.");
  }
}

function validatePalette(palette) {
  assertExactObject(palette, [
    "anchors", "clamp_above_deviation", "clamp_below_deviation",
    "continuous_interpolation", "palette_id",
  ], "Target Coverage palette");
  const anchorsValid = Array.isArray(palette.anchors)
    && palette.anchors.length === TARGET_COVERAGE_PALETTE.length
    && palette.anchors.every((anchor, index) => {
      const expected = TARGET_COVERAGE_PALETTE[index];
      try {
        assertExactObject(
          anchor, ["color_name", "deviation", "srgb_hex"],
          "Target Coverage palette anchor",
        );
      } catch {
        return false;
      }
      return anchor.deviation === expected.deviation
        && anchor.color_name === expected.color_name
        && anchor.srgb_hex === expected.srgb_hex;
    });
  if (palette.palette_id !== "target-coverage-deviation-8-anchor-v1"
      || palette.continuous_interpolation !== true
      || palette.clamp_below_deviation !== -4
      || palette.clamp_above_deviation !== 6
      || !anchorsValid) {
    throw new Error("Target Coverage palette is incompatible.");
  }
}

function validateSampling(sampling, source, metadata) {
  assertExactObject(sampling, [
    "axes_swapped_from_requested_room", "coordinate_transform_policy_id",
    "edge_band_behavior", "field_sampling_mapping", "interpolation_policy_id",
    "method", "outside_behavior", "support_bounds_m", "supported_extent",
  ], "Target Coverage sampling");
  assertExactObject(sampling.support_bounds_m, [
    "bounds", "x_max", "x_min", "y_max", "y_min",
  ], "Target Coverage support");
  const axesSwapped = sampling.axes_swapped_from_requested_room;
  const mapping = { x: "aligned_x", y: "aligned_y" };
  const support = sampling.support_bounds_m;
  if (typeof axesSwapped !== "boolean"
      || sampling.coordinate_transform_policy_id
        !== "requested_room_to_long_axis_x_rigid_rotation_v1"
      || sampling.interpolation_policy_id
        !== "stage_a_regular_grid_bilinear_half_cell_clamp_v1"
      || sampling.method !== "manual_four_cell_bilinear"
      || sampling.supported_extent
        !== "sample-center rectangle expanded by half a grid step"
      || sampling.edge_band_behavior !== "clamp to nearest center"
      || sampling.outside_behavior !== "target coverage unavailable"
      || !recordsEqual(sampling.field_sampling_mapping, mapping)
      || support.bounds !== "inclusive"
      || !recordsEqual(source.grid.cell_edge_bounds_m, {
        x_min: support.x_min, x_max: support.x_max,
        y_min: support.y_min, y_max: support.y_max,
      })
      || (metadata !== null
        && metadata.orientation?.layout_axes_swapped_from_requested_room
          !== axesSwapped)) {
    throw new Error("Target Coverage interpolation policy is incompatible.");
  }
}

function validateCanonicalGeometry(geometry) {
  assertExactObject(geometry, [
    "canonical_leaf_count", "canonical_leaf_ids", "canonical_topology_sha256",
    "centroid_policy_id", "displayed_leaf_ordering", "instance_translation_source",
    "leaf_geometry_identity_sha256", "leaf_ordering",
    "representative_centroids_simulation_xy_m",
  ], "Target Coverage canonical geometry");
  const ids = geometry.canonical_leaf_ids;
  const centroids = geometry.representative_centroids_simulation_xy_m;
  if (geometry.canonical_leaf_count !== 12
      || geometry.centroid_policy_id
        !== "one_sided_triangle_area_weighted_leaf_centroid_v1"
      || geometry.leaf_ordering !== "canonical-leaf order"
      || geometry.displayed_leaf_ordering
        !== "plant-major; canonical-leaf order"
      || geometry.instance_translation_source
        !== "scene.plant_instances.instance_translations"
      || !Array.isArray(ids) || ids.length !== 12
      || ids.some((value) => typeof value !== "string" || !value)
      || new Set(ids).size !== 12
      || !Array.isArray(centroids) || centroids.length !== 12
      || centroids.some((value) => !Array.isArray(value) || value.length !== 2
        || value.some((component) => !Number.isFinite(component)))) {
    throw new Error("Target Coverage canonical leaf geometry is incompatible.");
  }
  assertSha256(geometry.canonical_topology_sha256, "canonical topology");
  assertSha256(geometry.leaf_geometry_identity_sha256, "leaf geometry identity");
}

function materialIsSupported(material) {
  return material instanceof THREE.MeshStandardMaterial;
}

function hexIntegerToRgb(value) {
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function injectAfter(source, marker, addition) {
  if (typeof source !== "string" || !source.includes(marker)) {
    throw new Error(`Target Coverage shader marker is missing: ${marker}`);
  }
  return source.replace(marker, `${marker}\n${addition}`);
}

function assertExactObject(value, fields, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).sort().join(",") !== fields.slice().sort().join(",")) {
    throw new Error(`${label} field inventory is incompatible.`);
  }
}

function assertSha256(value, label) {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
    throw new Error(`${label} SHA-256 is incompatible.`);
  }
}

function recordsEqual(left, right) {
  return left && typeof left === "object" && !Array.isArray(left)
    && Object.keys(left).sort().join(",") === Object.keys(right).sort().join(",")
    && Object.entries(right).every(([name, value]) => left[name] === value);
}

const VERTEX_DECLARATIONS = `
uniform sampler2D targetCoverageScalarMap;
uniform vec2 targetCoverageScalarDimensions;
uniform vec4 targetCoverageSupportBounds;
uniform float targetCoverageReference;
uniform float targetCoverageTolerance;
uniform vec2 targetCoverageLeafCentroids[12];
varying float vTargetCoverageDeviation;
varying float vTargetCoverageValid;

float targetCoverageScalarAt(vec2 cell) {
  vec2 bounded = clamp(
    cell, vec2(0.0), targetCoverageScalarDimensions - vec2(1.0)
  );
  return texture2D(
    targetCoverageScalarMap,
    (bounded + vec2(0.5)) / targetCoverageScalarDimensions
  ).r;
}
`;

const VERTEX_LOOKUP = `
int targetCoverageLeafIndex = int(floor(_leaf_index + 0.5));
vec2 targetCoverageSimulationXY = targetCoverageLeafCentroids[targetCoverageLeafIndex]
  + vec2(instanceMatrix[3][0], -instanceMatrix[3][2]);
vec2 targetCoverageFieldXY = targetCoverageSimulationXY;
bool targetCoverageInside =
  targetCoverageFieldXY.x >= targetCoverageSupportBounds.x
  && targetCoverageFieldXY.x <= targetCoverageSupportBounds.y
  && targetCoverageFieldXY.y >= targetCoverageSupportBounds.z
  && targetCoverageFieldXY.y <= targetCoverageSupportBounds.w;
if (targetCoverageInside) {
  vec2 targetCoverageUv = (
    targetCoverageFieldXY
    - vec2(targetCoverageSupportBounds.x, targetCoverageSupportBounds.z)
  ) / vec2(
    targetCoverageSupportBounds.y - targetCoverageSupportBounds.x,
    targetCoverageSupportBounds.w - targetCoverageSupportBounds.z
  );
  vec2 targetCoverageGridPosition =
    targetCoverageUv * targetCoverageScalarDimensions - vec2(0.5);
  vec2 targetCoverageLowerCell = floor(targetCoverageGridPosition);
  vec2 targetCoverageFraction = fract(targetCoverageGridPosition);
  float targetCoverageLower = mix(
    targetCoverageScalarAt(targetCoverageLowerCell),
    targetCoverageScalarAt(targetCoverageLowerCell + vec2(1.0, 0.0)),
    targetCoverageFraction.x
  );
  float targetCoverageUpper = mix(
    targetCoverageScalarAt(targetCoverageLowerCell + vec2(0.0, 1.0)),
    targetCoverageScalarAt(targetCoverageLowerCell + vec2(1.0, 1.0)),
    targetCoverageFraction.x
  );
  float targetCoveragePpfd = mix(
    targetCoverageLower, targetCoverageUpper, targetCoverageFraction.y
  );
  vTargetCoverageDeviation =
    (targetCoveragePpfd - targetCoverageReference) / targetCoverageTolerance;
  vTargetCoverageValid = 1.0;
} else {
  vTargetCoverageDeviation = 0.0;
  vTargetCoverageValid = 0.0;
}
`;

const FRAGMENT_DECLARATIONS = `
uniform int targetCoverageEnabled;
uniform float targetCoverageAnchors[8];
uniform vec3 targetCoveragePalette[8];
varying float vTargetCoverageDeviation;
varying float vTargetCoverageValid;

vec3 mapTargetCoverageColor(float deviation) {
  if (deviation <= targetCoverageAnchors[0]) return targetCoveragePalette[0];
  if (deviation <= targetCoverageAnchors[1]) return mix(
    targetCoveragePalette[0], targetCoveragePalette[1],
    (deviation-targetCoverageAnchors[0])
      /(targetCoverageAnchors[1]-targetCoverageAnchors[0])
  );
  if (deviation <= targetCoverageAnchors[2]) return mix(
    targetCoveragePalette[1], targetCoveragePalette[2],
    (deviation-targetCoverageAnchors[1])
      /(targetCoverageAnchors[2]-targetCoverageAnchors[1])
  );
  if (deviation <= targetCoverageAnchors[3]) return mix(
    targetCoveragePalette[2], targetCoveragePalette[3],
    (deviation-targetCoverageAnchors[2])
      /(targetCoverageAnchors[3]-targetCoverageAnchors[2])
  );
  if (deviation <= targetCoverageAnchors[4]) return mix(
    targetCoveragePalette[3], targetCoveragePalette[4],
    (deviation-targetCoverageAnchors[3])
      /(targetCoverageAnchors[4]-targetCoverageAnchors[3])
  );
  if (deviation <= targetCoverageAnchors[5]) return mix(
    targetCoveragePalette[4], targetCoveragePalette[5],
    (deviation-targetCoverageAnchors[4])
      /(targetCoverageAnchors[5]-targetCoverageAnchors[4])
  );
  if (deviation <= targetCoverageAnchors[6]) return mix(
    targetCoveragePalette[5], targetCoveragePalette[6],
    (deviation-targetCoverageAnchors[5])
      /(targetCoverageAnchors[6]-targetCoverageAnchors[5])
  );
  if (deviation <= targetCoverageAnchors[7]) return mix(
    targetCoveragePalette[6], targetCoveragePalette[7],
    (deviation-targetCoverageAnchors[6])
      /(targetCoverageAnchors[7]-targetCoverageAnchors[6])
  );
  return targetCoveragePalette[7];
}
`;

const FRAGMENT_COLORING = `
if (targetCoverageEnabled == 1 && vTargetCoverageValid > 0.5) {
  diffuseColor.rgb = mapTargetCoverageColor(vTargetCoverageDeviation);
}
`;

const FRAGMENT_COLORING_D4 = `
if (targetCoverageEnabled == 1 && vTargetCoverageValid > 0.5) {
  diffuseColor.rgb = mapTargetCoverageColor(vTargetCoverageDeviation);
  leafFluxColoredSide = true;
}
`;
