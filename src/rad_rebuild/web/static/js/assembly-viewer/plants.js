// @ts-check

// @ts-ignore Static vendor module is served by Flask.
import * as THREE from "/static/vendor/three/three.module.js";

const PLANT_VIEWER_SCHEMA = "rad_rebuild.fspm.plants.viewer.v1";
const DEFAULT_LEAF_COLOR = 0x3fa66f;
const DEFAULT_ABSORPTION_INTENSITY = 0.5;
const DEFAULT_LEAF_THREE_COLOR = new THREE.Color(DEFAULT_LEAF_COLOR);
const SURFACE_FLUX_METRIC = "incident_photon_flux_density_umol_m2_s";
const TARGET_CLASSIFICATION_METRIC = "target_classification_ppfd_umol_m2_s";
export const PLANT_COLOR_MODE_TARGET_RANGE = "target_range";
export const PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX = "raw_leaf_surface_flux";
export const SURFACE_FLUX_COLOR_PALETTE = Object.freeze({
  blue: "#2563EB",
  cyan: "#06B6D4",
  teal: "#14B8A6",
  green: "#22C55E",
  yellowGreen: "#A3E635",
  yellowOrange: "#F59E0B",
  red: "#DC2626",
});
const TARGET_RANGE_COLOR_ANCHORS = [
  { deviation: -10, color: SURFACE_FLUX_COLOR_PALETTE.blue },
  { deviation: -6, color: SURFACE_FLUX_COLOR_PALETTE.cyan },
  { deviation: -3, color: SURFACE_FLUX_COLOR_PALETTE.teal },
  { deviation: -1, color: SURFACE_FLUX_COLOR_PALETTE.green },
  { deviation: 1, color: SURFACE_FLUX_COLOR_PALETTE.green },
  { deviation: 2, color: SURFACE_FLUX_COLOR_PALETTE.yellowGreen },
  { deviation: 4, color: SURFACE_FLUX_COLOR_PALETTE.yellowOrange },
  { deviation: 6, color: SURFACE_FLUX_COLOR_PALETTE.red },
];
export const TARGET_RANGE_DEVIATION_ANCHORS = Object.freeze([
  { deviation: -10, color: SURFACE_FLUX_COLOR_PALETTE.blue },
  { deviation: -6, color: SURFACE_FLUX_COLOR_PALETTE.cyan },
  { deviation: -3, color: SURFACE_FLUX_COLOR_PALETTE.teal },
  { deviation: -1, color: SURFACE_FLUX_COLOR_PALETTE.green },
  { deviation: 0, color: SURFACE_FLUX_COLOR_PALETTE.green },
  { deviation: 1, color: SURFACE_FLUX_COLOR_PALETTE.green },
  { deviation: 2, color: SURFACE_FLUX_COLOR_PALETTE.yellowGreen },
  { deviation: 4, color: SURFACE_FLUX_COLOR_PALETTE.yellowOrange },
  { deviation: 6, color: SURFACE_FLUX_COLOR_PALETTE.red },
]);

function finiteNumber(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
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

function centroidOfVertices(vertices) {
  const centroid = [0, 0, 0];
  if (!vertices.length) {
    return centroid;
  }
  for (const vertex of vertices) {
    centroid[0] += vertex[0];
    centroid[1] += vertex[1];
    centroid[2] += vertex[2];
  }
  return centroid.map((value) => value / vertices.length);
}

function distanceSquared3(a, b) {
  return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2;
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

export function surfaceFluxColorHexForTargetDeviation(value, _surfaceFlux = {}) {
  const deviation = finiteNumber(value);
  if (deviation === null) {
    return SURFACE_FLUX_COLOR_PALETTE.green;
  }
  if (deviation >= -1 && deviation <= 1) {
    return SURFACE_FLUX_COLOR_PALETTE.green;
  }

  const firstAnchor = TARGET_RANGE_COLOR_ANCHORS[0];
  const lastAnchor = TARGET_RANGE_COLOR_ANCHORS[TARGET_RANGE_COLOR_ANCHORS.length - 1];
  if (deviation <= firstAnchor.deviation) {
    return firstAnchor.color;
  }
  for (let index = 1; index < TARGET_RANGE_COLOR_ANCHORS.length; index += 1) {
    const previous = TARGET_RANGE_COLOR_ANCHORS[index - 1];
    const next = TARGET_RANGE_COLOR_ANCHORS[index];
    if (deviation <= next.deviation) {
      const span = next.deviation - previous.deviation;
      return interpolatedHexColor(
        previous.color,
        next.color,
        span > 0 ? (deviation - previous.deviation) / span : 0,
      );
    }
  }
  return lastAnchor.color;
}

function targetDeviationColor(value, surfaceFlux = {}) {
  return new THREE.Color(surfaceFluxColorHexForTargetDeviation(value, surfaceFlux));
}

const RAW_LEAF_SURFACE_FLUX_ANCHORS = [
  { ratio: 0.00, percent: 0, color: "#2563EB" },
  { ratio: 0.20, percent: 20, color: "#06B6D4" },
  { ratio: 0.40, percent: 40, color: "#14B8A6" },
  { ratio: 0.55, percent: 55, color: "#22C55E" },
  { ratio: 0.80, percent: 80, color: "#22C55E" },
  { ratio: 1.00, percent: 100, color: "#A3E635" },
  { ratio: 1.20, percent: 120, color: "#F59E0B" },
  { ratio: 1.50, percent: 150, color: "#DC2626" },
];

const RAW_LEAF_SURFACE_FLUX_BACK_ANCHORS = [
  { ratio: 0.00, percent: 0, color: "#2563EB" },
  { ratio: 0.02, percent: 2, color: "#06B6D4" },
  { ratio: 0.05, percent: 5, color: "#14B8A6" },
  { ratio: 0.10, percent: 10, color: "#22C55E" },
  { ratio: 0.20, percent: 20, color: "#22C55E" },
  { ratio: 0.35, percent: 35, color: "#A3E635" },
  { ratio: 0.50, percent: 50, color: "#F59E0B" },
  { ratio: 0.75, percent: 75, color: "#DC2626" },
];

function rawLeafSurfaceFluxTarget(surfaceFlux = {}) {
  const detail = surfaceFlux?.visualization?.raw_leaf_surface_flux_detail
    || surfaceFlux?.visualization?.raw_surface_detail;
  const detailTarget = finiteNumber(detail?.target_ppfd_umol_m2_s);
  if (detailTarget !== null && detailTarget > 0) {
    return {
      target: detailTarget,
      source: detail?.scale_basis || "fspm_target_ppfd_umol_m2_s",
    };
  }

  const explicitScale = surfaceFlux?.visualization?.raw_leaf_surface_flux_scale
    || surfaceFlux?.raw_leaf_surface_flux_scale;
  const explicitTarget = finiteNumber(explicitScale?.target_ppfd_umol_m2_s);
  if (explicitTarget !== null && explicitTarget > 0) {
    return {
      target: explicitTarget,
      source: explicitScale?.target_source || "fspm_target_ppfd_umol_m2_s",
    };
  }

  for (const [source, value] of [
    ["fspm_target_ppfd_umol_m2_s", surfaceFlux?.fspm_target_ppfd_umol_m2_s],
    ["fspm_target_ppfd_umol_m2_s", surfaceFlux?.target?.target_ppfd_umol_m2_s],
    ["fspm_target_ppfd_umol_m2_s", surfaceFlux?.target_ppfd_umol_m2_s],
    ["lighting_target_ppfd", surfaceFlux?.lighting_target_ppfd_umol_m2_s],
    ["lighting_target_ppfd", surfaceFlux?.target_ppfd],
  ]) {
    const target = finiteNumber(value);
    if (target !== null && target > 0) {
      return { target, source };
    }
  }

  return { target: null, source: "unavailable" };
}

function rawLeafSurfaceFluxAnchorEntries(targetPpfd, anchors = RAW_LEAF_SURFACE_FLUX_ANCHORS) {
  return anchors.map((anchor, index) => ({
    ...anchor,
    ppfd_umol_m2_s: targetPpfd === null ? null : anchor.ratio * targetPpfd,
    label: index === anchors.length - 1
      ? `${anchor.percent}%+`
      : `${anchor.percent}%`,
  }));
}

function rawLeafSurfaceFluxLegendFromScale(scale) {
  return {
    title: "Raw leaf-surface incident PPFD",
    units: scale.units,
    scale: "% of FSPM target",
    target_ppfd_umol_m2_s: scale.targetPpfd,
    target_source: scale.targetSource,
    side: scale.side || "front",
    role: scale.role || "primary_exposure_comparison",
    note: scale.note || (
      scale.side === "back"
        ? "Bottom/back scale = underside/reflected-light diagnostic"
        : "Top/front scale = primary exposure comparison"
    ),
    anchors: scale.anchors,
  };
}

function normalizeRawLeafSurfaceFluxLegend(legend, scale) {
  if (legend && Array.isArray(legend.anchors) && legend.anchors.length > 0) {
    return {
      title: legend.title || "Raw leaf-surface incident PPFD",
      units: legend.units || scale.units,
      scale: legend.scale || "% of FSPM target",
      target_ppfd_umol_m2_s: finiteNumber(legend.target_ppfd_umol_m2_s) ?? scale.targetPpfd,
      target_source: legend.target_source || scale.targetSource,
      side: legend.side || scale.side || "front",
      role: legend.role || scale.role || "primary_exposure_comparison",
      note: legend.note || scale.note || (
        (legend.side || scale.side) === "back"
          ? "Bottom/back scale = underside/reflected-light diagnostic"
          : "Top/front scale = primary exposure comparison"
      ),
      anchors: legend.anchors,
    };
  }
  return rawLeafSurfaceFluxLegendFromScale(scale);
}

function surfaceFluxLeafValues(plantPayload) {
  const values = plantPayload?.surface_flux?.visualization?.leaf_values;
  return Array.isArray(values) ? values : [];
}

function rawSurfaceDetail(plantPayload) {
  const detail = plantPayload?.surface_flux?.visualization?.raw_leaf_surface_flux_detail
    || plantPayload?.surface_flux?.visualization?.raw_surface_detail;
  return detail && typeof detail === "object" ? detail : null;
}

function rawLeafSurfaceFluxScale(surfaceFlux = {}, side = "front") {
  const detail = surfaceFlux?.visualization?.raw_leaf_surface_flux_detail
    || surfaceFlux?.visualization?.raw_surface_detail;
  const sideScales = surfaceFlux?.visualization?.raw_leaf_surface_flux_side_scales
    || surfaceFlux?.raw_leaf_surface_flux_side_scales
    || detail?.side_scales;
  const frontScale = surfaceFlux?.visualization?.raw_leaf_surface_flux_scale
    || surfaceFlux?.raw_leaf_surface_flux_scale;
  const explicitScale = sideScales?.[side] || (side === "front" ? frontScale : null);
  const { target, source } = rawLeafSurfaceFluxTarget(surfaceFlux);
  const units = explicitScale?.units || "umol/m²/s";
  const defaultAnchors = side === "back"
    ? RAW_LEAF_SURFACE_FLUX_BACK_ANCHORS
    : RAW_LEAF_SURFACE_FLUX_ANCHORS;
  const anchors = Array.isArray(detail?.color_anchors) && detail.color_anchors.length > 0
    ? (side === "front" ? detail.color_anchors : defaultAnchors)
    : (
      Array.isArray(explicitScale?.anchors) && explicitScale.anchors.length > 0
        ? explicitScale.anchors
        : rawLeafSurfaceFluxAnchorEntries(target, defaultAnchors)
    );
  const ratioMin = finiteNumber(explicitScale?.ratio_min) ?? 0;
  const ratioMax = finiteNumber(explicitScale?.ratio_max) ?? (side === "back" ? 0.75 : 1.5);
  return {
    mode: PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX,
    side,
    role: explicitScale?.role || (side === "back"
      ? "underside_reflected_light_diagnostic"
      : "primary_exposure_comparison"),
    scaleType: "target_normalized_ratio",
    targetPpfd: target,
    targetSource: source,
    ratioMin,
    ratioMax,
    clampMinRatio: finiteNumber(explicitScale?.clamp_min_ratio) ?? ratioMin,
    clampMaxRatio: finiteNumber(explicitScale?.clamp_max_ratio) ?? ratioMax,
    anchors,
    units,
    ratioUnits: explicitScale?.ratio_units || "fraction_of_target",
  };
}

export function rawLeafSurfaceFluxColorHexForValue(value, scale = {}) {
  const rawValue = finiteNumber(value);
  if (rawValue === null) {
    return `#${DEFAULT_LEAF_COLOR.toString(16).toUpperCase().padStart(6, "0")}`;
  }
  const targetPpfd = finiteNumber(scale?.targetPpfd ?? scale?.target_ppfd_umol_m2_s);
  if (targetPpfd === null || targetPpfd <= 0) {
    return `#${DEFAULT_LEAF_COLOR.toString(16).toUpperCase().padStart(6, "0")}`;
  }
  const clampMinRatio = finiteNumber(scale?.clampMinRatio ?? scale?.clamp_min_ratio) ?? 0;
  const clampMaxRatio = finiteNumber(scale?.clampMaxRatio ?? scale?.clamp_max_ratio) ?? 1.5;
  const ratio = clamp(rawValue / targetPpfd, clampMinRatio, clampMaxRatio);
  const anchors = Array.isArray(scale?.anchors) && scale.anchors.length > 0
    ? scale.anchors
    : RAW_LEAF_SURFACE_FLUX_ANCHORS;
  for (let index = 1; index < anchors.length; index += 1) {
    const previous = anchors[index - 1];
    const next = anchors[index];
    const previousRatio = finiteNumber(previous.ratio) ?? 0;
    const nextRatio = finiteNumber(next.ratio) ?? previousRatio;
    if (ratio <= nextRatio) {
      const span = nextRatio - previousRatio;
      return interpolatedHexColor(
        previous.color,
        next.color,
        span > 0 ? (ratio - previousRatio) / span : 0,
      );
    }
  }
  return anchors[anchors.length - 1].color;
}

function rawLeafSurfaceFluxColor(row, scale) {
  return new THREE.Color(rawLeafSurfaceFluxColorHexForValue(row?.[SURFACE_FLUX_METRIC], scale));
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

function targetRangeGradient() {
  const minDeviation = TARGET_RANGE_COLOR_ANCHORS[0].deviation;
  const maxDeviation = TARGET_RANGE_COLOR_ANCHORS[TARGET_RANGE_COLOR_ANCHORS.length - 1].deviation;
  const span = maxDeviation - minDeviation;
  const stops = TARGET_RANGE_COLOR_ANCHORS.map((anchor) => {
    const position = span > 0 ? ((anchor.deviation - minDeviation) / span) * 100 : 0;
    return `${anchor.color} ${position.toFixed(3)}%`;
  });
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}

function targetRangeAnchorLabel(deviation, ppfd) {
  const toleranceLabel = deviation === 0
    ? "0"
    : `${deviation > 0 ? "+" : ""}${deviation}\u03c3`;
  const ppfdLabel = ppfd === null ? "-" : String(Math.round(ppfd));
  return `${toleranceLabel} ${ppfdLabel}`;
}

export function targetRangeLegendForSurfaceFlux(surfaceFlux = {}) {
  const target = surfaceFluxTarget(surfaceFlux);
  const anchors = TARGET_RANGE_DEVIATION_ANCHORS.map((anchor) => {
    const ppfd = target ? target.target + anchor.deviation * target.tolerance : null;
    return {
      ...anchor,
      tolerance_units: anchor.deviation,
      ppfd_umol_m2_s: ppfd,
      label: targetRangeAnchorLabel(anchor.deviation, ppfd),
    };
  });
  return {
    title: "Plant-location target coverage",
    subtitle: "Canopy-reference PPFD fit",
    units: "umol/m²/s",
    target_ppfd_umol_m2_s: target?.target ?? null,
    target_tolerance_umol_m2_s: target?.tolerance ?? null,
    gradient: targetRangeGradient(),
    anchors,
  };
}

function targetCoveragePpfdForLeaf(row) {
  return finiteNumber(row?.[TARGET_CLASSIFICATION_METRIC]);
}

export function surfaceFluxTargetDeviationForLeafValue(row, surfaceFlux = {}, _colorMetric = "") {
  const rowDeviation = finiteNumber(row?.target_deviation);
  if (rowDeviation !== null) {
    return rowDeviation;
  }

  const target = surfaceFluxTarget(surfaceFlux);
  const value = targetCoveragePpfdForLeaf(row);
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

function createRawDetailMaterial(plantPayload) {
  const material = new THREE.MeshBasicMaterial({
    color: 0xffffff,
    vertexColors: true,
    side: THREE.FrontSide,
    transparent: true,
    opacity: leafMaterialOpacity(plantPayload),
  });
  material.toneMapped = false;
  return material;
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

function rawDetailRowsByLeaf(plantPayload) {
  const detail = rawSurfaceDetail(plantPayload);
  const rows = detail?.samples;
  const map = new Map();
  if (Array.isArray(rows)) {
    for (const row of rows) {
      const leafId = typeof row?.leaf_id === "string" ? row.leaf_id : "";
      const rawValue = finiteNumber(row?.[SURFACE_FLUX_METRIC]);
      if (!leafId || rawValue === null) {
        continue;
      }
      if (!map.has(leafId)) {
        map.set(leafId, []);
      }
      map.get(leafId).push(row);
    }
    return map;
  }

  if (detail?.encoding !== "leaf_major_dense") {
    return map;
  }
  const leafIds = Array.isArray(detail.leaf_ids) ? detail.leaf_ids : [];
  const visualGranularity = String(detail.visual_granularity || "leaf_average");
  const valuesPpfd = detail.values_ppfd;
  if (visualGranularity === "leaf_average") {
    return map;
  }

  if (visualGranularity === "quadrature_mapped" && Array.isArray(valuesPpfd)) {
    const faceMaps = Array.isArray(detail.quadrature_face_sample_indices)
      ? detail.quadrature_face_sample_indices
      : [];
    for (let leafIndex = 0; leafIndex < leafIds.length; leafIndex += 1) {
      const leafId = typeof leafIds[leafIndex] === "string" ? leafIds[leafIndex] : "";
      const leafValues = Array.isArray(valuesPpfd[leafIndex]) ? valuesPpfd[leafIndex] : [];
      if (!leafId || leafValues.length === 0) {
        continue;
      }
      const faceMap = Array.isArray(faceMaps[leafIndex]) ? faceMaps[leafIndex] : [];
      const rowsForLeaf = [];
      for (let sampleIndex = 0; sampleIndex < leafValues.length; sampleIndex += 1) {
        const rawValue = finiteNumber(leafValues[sampleIndex]);
        if (rawValue === null) {
          continue;
        }
        const mappedFaceIndices = [];
        for (let faceIndex = 0; faceIndex < faceMap.length; faceIndex += 1) {
          const mappedSampleIndex = finiteNumber(faceMap[faceIndex]);
          if (mappedSampleIndex === sampleIndex) {
            mappedFaceIndices.push(faceIndex);
          }
        }
        rowsForLeaf.push({
          sample_id: `${leafId}_quadrature_${sampleIndex + 1}`,
          leaf_id: leafId,
          side: "front",
          quadrature_index: sampleIndex,
          mapped_face_indices: mappedFaceIndices,
          mapped_surface_ids: mappedFaceIndices.map((faceIndex) => surfaceIdForFace(leafId, faceIndex)),
          [SURFACE_FLUX_METRIC]: rawValue,
        });
      }
      if (rowsForLeaf.length > 0) {
        map.set(leafId, rowsForLeaf);
      }
    }
    return map;
  }

  if (visualGranularity === "mesh_patch" && valuesPpfd && typeof valuesPpfd === "object") {
    const patchFaceIndices = Array.isArray(detail.patch_face_indices)
      ? detail.patch_face_indices
      : [];
    const sides = Array.isArray(detail.sides) && detail.sides.length > 0
      ? detail.sides
      : Object.keys(valuesPpfd);
    for (let leafIndex = 0; leafIndex < leafIds.length; leafIndex += 1) {
      const leafId = typeof leafIds[leafIndex] === "string" ? leafIds[leafIndex] : "";
      const faceIndices = Array.isArray(patchFaceIndices[leafIndex]) ? patchFaceIndices[leafIndex] : [];
      if (!leafId || faceIndices.length === 0) {
        continue;
      }
      const rowsForLeaf = [];
      for (const side of sides) {
        const sideValues = valuesPpfd[side];
        const leafValues = Array.isArray(sideValues) && Array.isArray(sideValues[leafIndex])
          ? sideValues[leafIndex]
          : [];
        for (let patchIndex = 0; patchIndex < faceIndices.length; patchIndex += 1) {
          const faceIndex = Number(faceIndices[patchIndex]);
          const rawValue = finiteNumber(leafValues[patchIndex]);
          if (!Number.isInteger(faceIndex) || rawValue === null) {
            continue;
          }
          const surfaceId = surfaceIdForFace(leafId, faceIndex);
          rowsForLeaf.push({
            sample_id: `${surfaceId}_${side}`,
            leaf_id: leafId,
            surface_id: surfaceId,
            face_index: faceIndex,
            side,
            [SURFACE_FLUX_METRIC]: rawValue,
          });
        }
      }
      if (rowsForLeaf.length > 0) {
        map.set(leafId, rowsForLeaf);
      }
    }
    return map;
  }

  return map;
}

function rawPrimaryLeafAverageRowsByLeaf(plantPayload) {
  const detail = rawSurfaceDetail(plantPayload);
  const map = new Map();
  if (
    String(detail?.visual_granularity || "") !== "mesh_patch"
    || detail?.top_bottom_support !== true
    || !detail?.values_ppfd
    || typeof detail.values_ppfd !== "object"
  ) {
    return map;
  }
  const leafIds = Array.isArray(detail.leaf_ids) ? detail.leaf_ids : [];
  const frontValues = Array.isArray(detail.values_ppfd.front) ? detail.values_ppfd.front : [];
  for (let leafIndex = 0; leafIndex < leafIds.length; leafIndex += 1) {
    const leafId = typeof leafIds[leafIndex] === "string" ? leafIds[leafIndex] : "";
    const values = Array.isArray(frontValues[leafIndex])
      ? frontValues[leafIndex].map(finiteNumber).filter((value) => value !== null)
      : [];
    if (!leafId || values.length === 0) {
      continue;
    }
    map.set(leafId, {
      leaf_id: leafId,
      side: "front",
      [SURFACE_FLUX_METRIC]: values.reduce((total, value) => total + value, 0) / values.length,
    });
  }
  return map;
}

function rowHasFace(row, faceIndex) {
  if (Number(row?.face_index) === faceIndex) {
    return true;
  }
  const mappedFaceIndices = row?.mapped_face_indices;
  return Array.isArray(mappedFaceIndices)
    && mappedFaceIndices.some((value) => Number(value) === faceIndex);
}

function surfaceIdForFace(leafId, faceIndex) {
  return `${leafId}_face_${String(faceIndex).padStart(4, "0")}`;
}

function rowHasSurface(row, leafId, faceIndex) {
  const surfaceId = surfaceIdForFace(leafId, faceIndex);
  if (row?.surface_id === surfaceId) {
    return true;
  }
  const mappedSurfaceIds = row?.mapped_surface_ids;
  return Array.isArray(mappedSurfaceIds)
    && mappedSurfaceIds.some((value) => value === surfaceId);
}

function rowOrigin(row) {
  return finiteCoordinateTriple(row?.origin_m);
}

function nearestRawDetailRow(rows, centroid) {
  let best = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  let originCount = 0;
  for (const row of rows) {
    const origin = rowOrigin(row);
    if (!origin) {
      continue;
    }
    originCount += 1;
    const distance = distanceSquared3(origin, centroid);
    if (distance < bestDistance) {
      best = row;
      bestDistance = distance;
    }
  }
  return originCount > 0 ? best : null;
}

function rawDetailRowForFace(rows, leafId, faceIndex, centroid, side = "") {
  if (!rows.length) {
    return null;
  }
  const sideRows = side
    ? rows.filter((row) => String(row?.side || "") === side)
    : rows;
  const candidates = sideRows.length ? sideRows : rows;
  return candidates.find((row) => rowHasSurface(row, leafId, faceIndex))
    || candidates.find((row) => rowHasFace(row, faceIndex))
    || nearestRawDetailRow(candidates, centroid);
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
  const hasSurfaceDetail = Boolean(plantGroup.userData?.hasSurfaceDetail);
  let absorptionColor = hasAbsorptionColor;
  let colorMode = plantGroup.userData?.colorMode || PLANT_COLOR_MODE_TARGET_RANGE;
  let surfaceDetail = hasSurfaceDetail && Boolean(plantGroup.userData?.surfaceDetail);

  function activeColorAttribute(child) {
    if (colorMode === PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX) {
      return child.userData?.rawFluxColorAttribute || child.userData?.absorptionColorAttribute;
    }
    return child.userData?.targetColorAttribute || child.userData?.absorptionColorAttribute;
  }

  function applyAbsorptionColor() {
    const rawDetailActive = absorptionColor
      && colorMode === PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX
      && surfaceDetail
      && hasSurfaceDetail;
    plantGroup.traverse((child) => {
      if (!(child instanceof THREE.Mesh)) {
        return;
      }
      if (child.userData?.rawDetailMesh) {
        child.visible = rawDetailActive;
        return;
      }
      child.visible = !rawDetailActive;
      const defaultColorAttribute = child.userData?.defaultColorAttribute;
      const absorptionColorAttribute = activeColorAttribute(child);
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

  function setColorMode(value) {
    colorMode = value === PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX
      ? PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX
      : PLANT_COLOR_MODE_TARGET_RANGE;
    plantGroup.userData.colorMode = colorMode;
    applyAbsorptionColor();
    return getState();
  }

  function setSurfaceDetail(value) {
    surfaceDetail = hasSurfaceDetail && Boolean(value);
    plantGroup.userData.surfaceDetail = surfaceDetail;
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
      colorMode,
      rawLeafSurfaceFluxScale: plantGroup.userData?.rawLeafSurfaceFluxScale || null,
      rawLeafSurfaceFluxSideScales: plantGroup.userData?.rawLeafSurfaceFluxSideScales || null,
      rawLeafSurfaceFluxLegend: plantGroup.userData?.rawLeafSurfaceFluxLegend || null,
      rawLeafSurfaceFluxSideLegends: plantGroup.userData?.rawLeafSurfaceFluxSideLegends || null,
      targetRangeLegend: plantGroup.userData?.targetRangeLegend || null,
      hasSurfaceDetail,
      surfaceDetail,
      surfaceDetailMode: surfaceDetail && hasSurfaceDetail
        ? plantGroup.userData?.surfaceDetailMode || "surface_detail"
        : "leaf_average",
      rawSurfaceDetail: plantGroup.userData?.rawSurfaceDetail || null,
      rawSurfaceDetailSampleCount: Number(plantGroup.userData?.rawSurfaceDetailSampleCount || 0),
      surfaceFluxAvailable: Boolean(plantGroup.userData?.surfaceFluxAvailable),
      surfaceFluxUnavailableReason: plantGroup.userData?.surfaceFluxUnavailableReason || "",
    };
  }

  applyAbsorptionColor();
  return { setVisible, setAbsorptionColor, setColorMode, setSurfaceDetail, getState };
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

function appendLeafGeometryBuffers({
  leaf,
  targetColor,
  rawFluxColor,
  positions,
  indices,
  defaultColors,
  targetColors,
  rawFluxColors,
}) {
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
    targetColors.push(targetColor.r, targetColor.g, targetColor.b);
    rawFluxColors.push(rawFluxColor.r, rawFluxColor.g, rawFluxColor.b);
  }
  for (const index of triangles) {
    indices.push(vertexOffset + index);
  }
  return true;
}

function appendTriangle({
  vertices,
  order,
  color,
  positions,
  colors,
}) {
  for (const index of order) {
    positions.push(...radianceVertexToWorld(vertices[index]));
    colors.push(color.r, color.g, color.b);
  }
}

function appendRawDetailLeafGeometryBuffers({
  leaf,
  rawRows,
  aggregateRawRow,
  rawScale,
  rawBackScale,
  topBottomSupport,
  positions,
  colors,
  sampleIds,
}) {
  const leafId = typeof leaf?.leaf_id === "string" ? leaf.leaf_id : "";
  const sourceVertices = Array.isArray(leaf?.mesh?.vertices) ? leaf.mesh.vertices : [];
  const vertices = sourceVertices.map(finiteCoordinateTriple);
  if (!leafId || vertices.some((vertex) => vertex === null)) {
    return false;
  }
  if (!vertices.length || !Array.isArray(leaf?.mesh?.faces)) {
    return false;
  }

  let rendered = false;
  for (let faceIndex = 0; faceIndex < leaf.mesh.faces.length; faceIndex += 1) {
    const indices = normalizedFaceIndices(leaf.mesh.faces[faceIndex], vertices.length);
    if (!indices) {
      continue;
    }
    const faceVertices = indices.map((index) => vertices[index]);
    const centroid = centroidOfVertices(faceVertices);
    const frontDetailRow = rawDetailRowForFace(rawRows, leafId, faceIndex, centroid, "front");
    const anySideDetailRow = topBottomSupport
      ? null
      : rawDetailRowForFace(rawRows, leafId, faceIndex, centroid);
    const frontRow = frontDetailRow || anySideDetailRow || aggregateRawRow;
    const backRow = topBottomSupport
      ? rawDetailRowForFace(rawRows, leafId, faceIndex, centroid, "back") || aggregateRawRow || frontRow
      : frontRow;
    if (!frontRow) {
      continue;
    }
    const frontColor = rawLeafSurfaceFluxColor(frontRow, rawScale);
    const backColor = rawLeafSurfaceFluxColor(backRow, topBottomSupport ? rawBackScale : rawScale);
    sampleIds.add(String(frontRow.sample_id || frontRow.surface_id || `${leafId}:${faceIndex}:front`));
    if (backRow) {
      sampleIds.add(String(backRow.sample_id || backRow.surface_id || `${leafId}:${faceIndex}:back`));
    }
    for (let index = 1; index < indices.length - 1; index += 1) {
      const triangleOrder = [indices[0], indices[index], indices[index + 1]];
      // The Radiance-to-Three axis swap flips handedness, so reverse front
      // triangles to keep receiver-front values on the visible front surface.
      appendTriangle({
        vertices,
        order: [triangleOrder[2], triangleOrder[1], triangleOrder[0]],
        color: frontColor,
        positions,
        colors,
      });
      appendTriangle({
        vertices,
        order: triangleOrder,
        color: backColor,
        positions,
        colors,
      });
      rendered = true;
    }
  }
  return rendered;
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
  const rawDetailMaterial = createRawDetailMaterial(plantPayload);
  const leafFluxValues = surfaceFluxLeafValues(plantPayload);
  const fluxByLeafId = leafFluxById(plantPayload);
  const rawDetail = rawSurfaceDetail(plantPayload);
  const rawDetailByLeafId = rawDetailRowsByLeaf(plantPayload);
  const rawPrimaryLeafAverageByLeafId = rawPrimaryLeafAverageRowsByLeaf(plantPayload);
  const rawDetailVisualGranularity = String(rawDetail?.visual_granularity || "leaf_average");
  const rawDetailTopBottomSupport = Boolean(rawDetail?.top_bottom_support);
  const colorMetric = plantPayload?.surface_flux?.visualization?.color_metric || "";
  const surfaceFlux = plantPayload?.surface_flux || {};
  const rawScale = rawLeafSurfaceFluxScale(surfaceFlux, "front");
  const rawBackScale = rawLeafSurfaceFluxScale(surfaceFlux, "back");
  const sideLegends = surfaceFlux?.visualization?.raw_leaf_surface_flux_side_legends
    || surfaceFlux?.raw_leaf_surface_flux_side_legends
    || rawDetail?.side_legends
    || {};
  const rawLegend = normalizeRawLeafSurfaceFluxLegend(
    sideLegends.front
      || surfaceFlux?.visualization?.raw_leaf_surface_flux_legend
      || surfaceFlux?.raw_leaf_surface_flux_legend,
    rawScale,
  );
  const rawBackLegend = normalizeRawLeafSurfaceFluxLegend(sideLegends.back, rawBackScale);
  const hasRawSideLegends = rawDetailTopBottomSupport || Boolean(sideLegends.back);
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
  const targetColors = [];
  const rawFluxColors = [];
  const rawDetailPositions = [];
  const rawDetailColors = [];
  const rawDetailSampleIds = new Set();
  let rawDetailLeafCount = 0;

  for (const plant of plantPayload.plants) {
    const leaves = Array.isArray(plant?.leaves) ? plant.leaves : [];
    for (const leaf of leaves) {
      const fluxRow = leafFluxRow(leaf, fluxByLeafId);
      const targetDeviation = surfaceFluxTargetDeviationForLeafValue(fluxRow, surfaceFlux, colorMetric);
      const targetColor = absorptionColorForLeaf(fluxRow, surfaceFlux, colorMetric);
      const primaryRawRow = rawPrimaryLeafAverageByLeafId.get(leaf?.leaf_id) || fluxRow;
      const rawFluxColor = rawLeafSurfaceFluxColor(primaryRawRow, rawScale);
      const rendered = appendLeafGeometryBuffers({
        leaf,
        targetColor,
        rawFluxColor,
        positions,
        indices,
        defaultColors,
        targetColors,
        rawFluxColors,
      });
      if (!rendered) {
        warnings.push(`${leaf?.leaf_id || "unknown leaf"} has invalid plant mesh data.`);
        continue;
      }
      const rawRows = rawDetailByLeafId.get(leaf?.leaf_id) || [];
      if (rawRows.length > 0 && rawDetailVisualGranularity !== "leaf_average") {
        const detailRendered = appendRawDetailLeafGeometryBuffers({
          leaf,
          rawRows,
          aggregateRawRow: fluxRow,
          rawScale,
          rawBackScale,
          topBottomSupport: rawDetailTopBottomSupport,
          positions: rawDetailPositions,
          colors: rawDetailColors,
          sampleIds: rawDetailSampleIds,
        });
        if (detailRendered) {
          rawDetailLeafCount += 1;
        }
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
    const targetColorAttribute = new THREE.Float32BufferAttribute(targetColors, 3);
    const rawFluxColorAttribute = new THREE.Float32BufferAttribute(rawFluxColors, 3);
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
      targetColorAttribute,
      rawFluxColorAttribute,
      absorptionColorAttribute: targetColorAttribute,
    };
    group.add(mesh);
  }

  if (rawDetailPositions.length > 0) {
    const detailGeometry = new THREE.BufferGeometry();
    const rawFluxDetailColorAttribute = new THREE.Float32BufferAttribute(rawDetailColors, 3);
    detailGeometry.setAttribute("position", new THREE.Float32BufferAttribute(rawDetailPositions, 3));
    detailGeometry.setAttribute("color", rawFluxDetailColorAttribute);
    detailGeometry.computeVertexNormals();
    detailGeometry.computeBoundingBox();

    const detailMesh = new THREE.Mesh(detailGeometry, rawDetailMaterial);
    detailMesh.name = "plant-leaves-raw-surface-detail";
    detailMesh.castShadow = true;
    detailMesh.receiveShadow = true;
    detailMesh.visible = false;
    detailMesh.userData = {
      rawDetailMesh: true,
      rawFluxDetailColorAttribute,
      rawDetailVisualGranularity,
      rawDetailSampleCount: rawDetailSampleIds.size,
      rawDetailLeafCount,
      absorptionMaterial: rawDetailMaterial,
    };
    group.add(detailMesh);
  } else if (rawDetailVisualGranularity !== "leaf_average") {
    warnings.push("Raw surface-detail data was advertised but did not map to renderable leaf faces; using leaf-average raw colors.");
  }

  if (rawDetailVisualGranularity === "mesh_patch" && !rawDetailTopBottomSupport) {
    warnings.push("Mesh-patch raw detail lacks front/back receiver values; top/bottom inspection falls back to available patch values.");
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
    surfaceFluxAvailable: leafFluxValues.length > 0,
    surfaceFluxUnavailableReason: leafFluxValues.length > 0
      ? ""
      : "Plant geometry is available, but plant_surface_flux.json did not provide leaf color rows for this run.",
    colorMetric,
    colorMode: PLANT_COLOR_MODE_TARGET_RANGE,
    colorModes: [PLANT_COLOR_MODE_TARGET_RANGE, PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX],
    rawLeafSurfaceFluxScale: rawScale,
    rawLeafSurfaceFluxSideScales: hasRawSideLegends
      ? { front: rawScale, back: rawBackScale }
      : { front: rawScale },
    rawLeafSurfaceFluxLegend: rawLegend,
    rawLeafSurfaceFluxSideLegends: hasRawSideLegends
      ? { front: rawLegend, back: rawBackLegend }
      : { front: rawLegend },
    targetRangeLegend: targetRangeLegendForSurfaceFlux(surfaceFlux),
    rawSurfaceDetail: rawDetail,
    hasSurfaceDetail: rawDetailPositions.length > 0,
    surfaceDetail: rawDetailPositions.length > 0,
    surfaceDetailMode: rawDetailPositions.length > 0 ? rawDetailVisualGranularity : "leaf_average",
    rawSurfaceDetailSampleCount: rawDetailSampleIds.size,
    rawSurfaceDetailLeafCount: rawDetailLeafCount,
    warnings,
  };
  return group;
}
