export const PRESENTATION_EXPORTER_ID = "fspm-optics.ppfd-presentation-export";
export const PRESENTATION_EXPORTER_VERSION = 1;
export const SVG_WIDTH = 1920;
export const SVG_HEIGHT = 1080;
export const PNG_WIDTH = 3840;
export const PNG_HEIGHT = 2160;

const PLOT = Object.freeze({x: 96, y: 145, width: 1440, height: 810});
const AXIS_COLORS = Object.freeze({
  x: "#ff5d5d",
  y: "#60d979",
  z: "#5c8dff",
});
const DEFAULT_TITLE = "PPFD distribution";

export function escapeXml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

export function displayHeight(rawPpfd, vertical) {
  return rawPpfd * vertical.scale + vertical.offset;
}

export function colorForPpfd(rawPpfd, limits, anchors) {
  const rawFraction = limits[1] === limits[0]
    ? .5
    : (rawPpfd - limits[0]) / (limits[1] - limits[0]);
  const fraction = Math.max(0, Math.min(1, rawFraction));
  const scaled = fraction * (anchors.length - 1);
  const lower = Math.min(Math.floor(scaled), anchors.length - 2);
  const local = scaled - lower;
  return anchors[lower].map(
    (start, channel) => start + local * (anchors[lower + 1][channel] - start),
  );
}

export function presentationFilename(runId, kind) {
  if (!/^[0-9a-f]{32}$/.test(runId)) throw new Error("Export run ID is invalid.");
  if (kind === "svg") return `ppfd-scatter-${runId}.svg`;
  if (kind === "png") return `ppfd-scatter-${runId}-4k.png`;
  throw new Error("Export file kind is invalid.");
}

export function buildPresentationSvg({THREE, camera, target, points, metadata, title}) {
  validateExportInputs(THREE, camera, target, points, metadata);
  const figureTitle = String(title ?? "").trim() || DEFAULT_TITLE;
  const limits = metadata.display.color_limits_ppfd_umol_m2_s;
  const anchors = metadata.display.colormap.anchors_srgb_8bit;
  const vertical = metadata.scatter.vertical_display_transform;
  const projectionCamera = camera.clone();
  projectionCamera.aspect = PLOT.width / PLOT.height;
  projectionCamera.updateProjectionMatrix();
  projectionCamera.updateMatrixWorld(true);

  const project = createProjector(THREE, projectionCamera);
  const projectedPoints = [];
  let xMin = Infinity;
  let yMin = Infinity;
  for (let index = 0; index < points.count; index += 1) {
    const x = points.x[index];
    const y = points.y[index];
    const ppfd = points.ppfd[index];
    xMin = Math.min(xMin, x);
    yMin = Math.min(yMin, y);
    const projected = project(x, y, displayHeight(ppfd, vertical));
    projectedPoints.push({
      ...projected,
      index,
      fill: rgb(colorForPpfd(ppfd, limits, anchors)),
    });
  }
  projectedPoints.sort((left, right) => right.depth - left.depth || left.index - right.index);

  const horizontalSpan = vertical.horizontal_reference_span_m;
  const divisions = Math.max(2, Math.min(20, metadata.grid.resolution.x - 1));
  const floor = vertical.scene_floor_height;
  const gridLines = projectGrid(project, horizontalSpan, divisions, floor);
  const axesLength = Math.max(horizontalSpan * .28, vertical.display_vertical_span * .28);
  const axes = [
    {id: "x", color: AXIS_COLORS.x, points: [project(xMin, yMin, floor), project(xMin + axesLength, yMin, floor)]},
    {id: "y", color: AXIS_COLORS.y, points: [project(xMin, yMin, floor), project(xMin, yMin + axesLength, floor)]},
    {id: "z", color: AXIS_COLORS.z, points: [project(xMin, yMin, floor), project(xMin, yMin, floor + axesLength)]},
  ];
  const legendX = 1595;
  const legendY = 270;
  const legendHeight = 390;
  const referencePpfd = metadata.display.reference_ppfd_umol_m2_s;
  const referenceY = legendY + legendHeight * (1 - (referencePpfd - limits[0]) / (limits[1] - limits[0]));
  const referenceLabel = metadata.display.reference.kind === "requested_lighting_target"
    ? "Requested mean target"
    : (metadata.display.reference.kind === "requested_sampled_ppfd_cap"
      ? "Requested sampled cap"
      : "Reference · achieved mean");
  const exportMetadata = buildExportMetadata({
    metadata,
    projectionCamera,
    target,
    title: figureTitle,
  });
  const gradientStops = anchors.map((color, index) => (
    `<stop offset="${formatNumber(index / (anchors.length - 1) * 100)}%" stop-color="${rgb(color)}"/>`
  )).join("");
  const pointElements = projectedPoints.map((point) => (
    `<circle data-sample-index="${point.index}" cx="${formatNumber(point.x)}" cy="${formatNumber(point.y)}" r="3.8" fill="${point.fill}" stroke="#25322d" stroke-width="0.8" opacity="0.96"/>`
  )).join("");
  const gridElements = gridLines.map((line, index) => (
    `<line data-grid-line="${index}" x1="${formatNumber(line[0].x)}" y1="${formatNumber(line[0].y)}" x2="${formatNumber(line[1].x)}" y2="${formatNumber(line[1].y)}" stroke="${index % 2 === 0 ? "#c8cbc8" : "#d9dbd8"}" stroke-width="1"/>`
  )).join("");
  const axisElements = axes.map((axis) => (
    `<line data-axis="${axis.id}" x1="${formatNumber(axis.points[0].x)}" y1="${formatNumber(axis.points[0].y)}" x2="${formatNumber(axis.points[1].x)}" y2="${formatNumber(axis.points[1].y)}" stroke="${axis.color}" stroke-width="4" stroke-linecap="round"/>`
  )).join("");
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${SVG_WIDTH} ${SVG_HEIGHT}" width="${SVG_WIDTH}" height="${SVG_HEIGHT}" role="img" aria-labelledby="figure-title figure-description">`,
    `<title id="figure-title">${escapeXml(figureTitle)}</title>`,
    '<desc id="figure-description">Validated PPFD raw samples projected from the current interactive camera without interpolation.</desc>',
    `<metadata id="fspm-optics-export-metadata">${escapeXml(JSON.stringify(exportMetadata))}</metadata>`,
    '<defs>',
    `<clipPath id="plot-clip"><rect x="${PLOT.x}" y="${PLOT.y}" width="${PLOT.width}" height="${PLOT.height}" rx="8"/></clipPath>`,
    `<linearGradient id="ppfd-colormap" x1="0" y1="100%" x2="0" y2="0%">${gradientStops}</linearGradient>`,
    '</defs>',
    `<rect width="${SVG_WIDTH}" height="${SVG_HEIGHT}" fill="#fbfaf7"/>`,
    `<text x="${PLOT.x}" y="91" fill="#17211d" font-family="Arial, Helvetica, sans-serif" font-size="42" font-weight="700">${escapeXml(figureTitle)}</text>`,
    `<rect x="${PLOT.x}" y="${PLOT.y}" width="${PLOT.width}" height="${PLOT.height}" rx="8" fill="#fffefb"/>`,
    '<g id="projected-floor-grid" clip-path="url(#plot-clip)">',
    gridElements,
    '</g>',
    '<g id="projected-axes" clip-path="url(#plot-clip)">',
    axisElements,
    '</g>',
    `<g id="validated-scatter-points" data-sample-count="${points.count}" clip-path="url(#plot-clip)">`,
    pointElements,
    '</g>',
    `<rect x="${PLOT.x}" y="${PLOT.y}" width="${PLOT.width}" height="${PLOT.height}" rx="8" fill="none" stroke="#aeb5b0" stroke-width="1.5"/>`,
    `<g id="ppfd-legend" font-family="Arial, Helvetica, sans-serif" fill="#24302b">`,
    `<text x="${legendX}" y="210" font-size="22" font-weight="700">PPFD</text>`,
    `<text x="${legendX}" y="238" font-size="15" fill="#5d6762">µmol·m⁻²·s⁻¹</text>`,
    `<rect x="${legendX}" y="${legendY}" width="38" height="${legendHeight}" rx="3" fill="url(#ppfd-colormap)" stroke="#9ca49f" stroke-width="1"/>`,
    `<text x="${legendX + 54}" y="${legendY + 7}" font-size="17" dominant-baseline="middle">Maximum · ${escapeXml(formatLegendValue(limits[1]))}</text>`,
    `<line x1="${legendX - 7}" y1="${formatNumber(referenceY)}" x2="${legendX + 45}" y2="${formatNumber(referenceY)}" stroke="#17211d" stroke-width="2"/>`,
    `<text x="${legendX + 54}" y="${formatNumber(referenceY)}" font-size="17" dominant-baseline="middle">${escapeXml(referenceLabel)} · ${escapeXml(formatLegendValue(referencePpfd))}</text>`,
    `<text x="${legendX + 54}" y="${legendY + legendHeight}" font-size="17" dominant-baseline="middle">Minimum · ${escapeXml(formatLegendValue(limits[0]))}</text>`,
    `<g id="axis-labels" font-size="16">`,
    axisKey(legendX, 750, AXIS_COLORS.x, "X position (m)"),
    axisKey(legendX, 792, AXIS_COLORS.y, "Y position (m)"),
    axisKey(legendX, 834, AXIS_COLORS.z, "Z PPFD (µmol·m⁻²·s⁻¹)"),
    '</g>',
    '</g>',
    `<text x="${PLOT.x}" y="1011" fill="#66716b" font-family="Arial, Helvetica, sans-serif" font-size="16">Validated raw samples · no interpolation</text>`,
    '</svg>',
  ].join("");
}

export function createSvgBlob(svg, BlobCtor = globalThis.Blob) {
  return new BlobCtor([svg], {type: "image/svg+xml;charset=utf-8"});
}

export async function rasterizeSvgBlobToPng(svgBlob, environment = {}) {
  const documentRef = environment.documentRef ?? globalThis.document;
  const UrlApi = environment.UrlApi ?? globalThis.URL;
  const ImageCtor = environment.ImageCtor ?? globalThis.Image;
  if (!documentRef || !UrlApi || !ImageCtor) throw new Error("PNG export is unavailable in this browser.");
  const sourceUrl = UrlApi.createObjectURL(svgBlob);
  const canvas = documentRef.createElement("canvas");
  canvas.width = PNG_WIDTH;
  canvas.height = PNG_HEIGHT;
  let image;
  try {
    image = await loadImage(sourceUrl, ImageCtor);
    const context = canvas.getContext("2d");
    if (!context) throw new Error("PNG canvas could not be created.");
    context.drawImage(image, 0, 0, PNG_WIDTH, PNG_HEIGHT);
    return await canvasToBlob(canvas, "image/png");
  } finally {
    if (image) image.src = "";
    canvas.width = 0;
    canvas.height = 0;
    UrlApi.revokeObjectURL(sourceUrl);
  }
}

export async function downloadBlob(blob, filename, environment = {}) {
  const documentRef = environment.documentRef ?? globalThis.document;
  const UrlApi = environment.UrlApi ?? globalThis.URL;
  const schedule = environment.schedule ?? globalThis.setTimeout;
  if (!documentRef || !UrlApi || !schedule) throw new Error("Browser downloads are unavailable.");
  const downloadUrl = UrlApi.createObjectURL(blob);
  const anchor = documentRef.createElement("a");
  try {
    anchor.href = downloadUrl;
    anchor.download = filename;
    anchor.hidden = true;
    documentRef.body.append(anchor);
    anchor.click();
    await new Promise((resolve) => schedule(resolve, 0));
  } finally {
    anchor.remove();
    UrlApi.revokeObjectURL(downloadUrl);
  }
}

function validateExportInputs(THREE, camera, target, points, metadata) {
  if (!THREE?.Vector3 || typeof camera?.clone !== "function") throw new Error("Export camera is unavailable.");
  if (![target?.x, target?.y, target?.z].every(Number.isFinite)) throw new Error("Export camera target is invalid.");
  if (!Number.isInteger(points?.count) || points.count < 1 || points.count !== metadata?.field?.sample_count) throw new Error("Export sample count is invalid.");
  for (const component of [points.x, points.y, points.ppfd]) {
    if (!component || component.length !== points.count) throw new Error("Export point field is incomplete.");
  }
  const limits = metadata?.display?.color_limits_ppfd_umol_m2_s;
  const anchors = metadata?.display?.colormap?.anchors_srgb_8bit;
  if (!Array.isArray(limits) || limits.length !== 2 || !limits.every(Number.isFinite) || limits[1] <= limits[0]) throw new Error("Export color limits are invalid.");
  if (!Array.isArray(anchors) || anchors.length < 2 || anchors.some((color) => !Array.isArray(color) || color.length !== 3 || color.some((channel) => !Number.isInteger(channel) || channel < 0 || channel > 255))) throw new Error("Export colormap anchors are invalid.");
  const vertical = metadata?.scatter?.vertical_display_transform;
  if (![vertical?.scale, vertical?.offset, vertical?.scene_floor_height, vertical?.horizontal_reference_span_m, vertical?.display_vertical_span].every(Number.isFinite)) throw new Error("Export display transform is invalid.");
  for (let index = 0; index < points.count; index += 1) {
    if (![points.x[index], points.y[index], points.ppfd[index]].every(Number.isFinite)) throw new Error("Export point field contains non-finite values.");
  }
}

function createProjector(THREE, camera) {
  return (x, y, z) => {
    const world = new THREE.Vector3(x, y, z);
    const depth = -world.clone().applyMatrix4(camera.matrixWorldInverse).z;
    world.project(camera);
    const projected = {
      x: PLOT.x + (world.x + 1) * .5 * PLOT.width,
      y: PLOT.y + (1 - (world.y + 1) * .5) * PLOT.height,
      depth,
    };
    if (![projected.x, projected.y, projected.depth].every(Number.isFinite)) throw new Error("A sample could not be projected through the current camera.");
    return projected;
  };
}

function projectGrid(project, size, divisions, floor) {
  const half = size / 2;
  const result = [];
  for (let index = 0; index <= divisions; index += 1) {
    const offset = -half + size * index / divisions;
    result.push([project(-half, offset, floor), project(half, offset, floor)]);
    result.push([project(offset, -half, floor), project(offset, half, floor)]);
  }
  return result;
}

function buildExportMetadata({metadata, projectionCamera, target, title}) {
  return {
    exporter: {
      id: PRESENTATION_EXPORTER_ID,
      version: PRESENTATION_EXPORTER_VERSION,
    },
    title,
    run_id: metadata.run_id,
    field_identity_sha256: metadata.field.identity_sha256,
    sample_count: metadata.field.sample_count,
    declared_ppfd_color_limits_umol_m2_s: [...metadata.display.color_limits_ppfd_umol_m2_s],
    reference: {
      kind: metadata.display.reference.kind,
      ppfd_umol_m2_s: metadata.display.reference_ppfd_umol_m2_s,
    },
    vertical_display_transform_policy_id: metadata.scatter.vertical_display_transform.policy_id,
    camera: {
      position: vectorArray(projectionCamera.position),
      target: vectorArray(target),
      up: vectorArray(projectionCamera.up),
      fov_degrees: projectionCamera.fov,
      projection_aspect: projectionCamera.aspect,
    },
    scientific_authority: "Raw PPFD remains authoritative.",
    export_transform_policy: "No interpolation, smoothing, correction, or scientific rescaling is applied.",
  };
}

function vectorArray(vector) {
  return [vector.x, vector.y, vector.z];
}

function rgb(color) {
  return `#${color.map((channel) => Math.round(channel).toString(16).padStart(2, "0")).join("")}`;
}

function axisKey(x, y, color, label) {
  return `<line x1="${x}" y1="${y}" x2="${x + 30}" y2="${y}" stroke="${color}" stroke-width="4" stroke-linecap="round"/><text x="${x + 44}" y="${y}" dominant-baseline="middle">${escapeXml(label)}</text>`;
}

function formatNumber(value) {
  return Number(value.toFixed(4)).toString();
}

function formatLegendValue(value) {
  return Number(value).toLocaleString("en-US", {maximumFractionDigits: 2, useGrouping: false});
}

function loadImage(sourceUrl, ImageCtor) {
  return new Promise((resolve, reject) => {
    const image = new ImageCtor();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("The SVG could not be loaded for PNG export."));
    image.src = sourceUrl;
  });
}

function canvasToBlob(canvas, type) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("The 4K PNG could not be encoded."));
    }, type);
  });
}
