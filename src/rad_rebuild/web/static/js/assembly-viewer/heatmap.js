// @ts-check

const ASSEMBLY_SCENE_ROUTE = "/radiance-api/radiance/assembly-scene?";
const PHOTOMETRIC_LAYER_ROUTE = "/radiance-api/radiance/assembly-photometric-layer";
const PHOTOMETRIC_LAYER_BINARY_ROUTE = "/radiance-api/radiance/assembly-photometric-layer.bin";
const VIRIDIS_HEX =
  "44015444025645045745055946075a46085c460a5d460b5e470d60470e6147106347116447136548146748166848176948186a481a6c481b6d481c6e481d6f481f70482071482173482374482475482576482677482878482979472a7a472c7a472d7b472e7c472f7d46307e46327e46337f463480453581453781453882443983443a83443b84433d84433e85423f854240864241864142874144874045884046883f47883f48893e49893e4a893e4c8a3d4d8a3d4e8a3c4f8a3c508b3b518b3b528b3a538b3a548c39558c39568c38588c38598c375a8c375b8d365c8d365d8d355e8d355f8d34608d34618d33628d33638d32648e32658e31668e31678e31688e30698e306a8e2f6b8e2f6c8e2e6d8e2e6e8e2e6f8e2d708e2d718e2c718e2c728e2c738e2b748e2b758e2a768e2a778e2a788e29798e297a8e297b8e287c8e287d8e277e8e277f8e27808e26818e26828e26828e25838e25848e25858e24868e24878e23888e23898e238a8d228b8d228c8d228d8d218e8d218f8d21908d21918c20928c20928c20938c1f948c1f958b1f968b1f978b1f988b1f998a1f9a8a1e9b8a1e9c891e9d891f9e891f9f881fa0881fa1881fa1871fa28720a38620a48621a58521a68522a78522a88423a98324aa8325ab8225ac8226ad8127ad8128ae8029af7f2ab07f2cb17e2db27d2eb37c2fb47c31b57b32b67a34b67935b77937b87838b9773aba763bbb753dbc743fbc7340bd7242be7144bf7046c06f48c16e4ac16d4cc26c4ec36b50c46a52c56954c56856c66758c7655ac8645cc8635ec96260ca6063cb5f65cb5e67cc5c69cd5b6ccd5a6ece5870cf5773d05675d05477d1537ad1517cd2507fd34e81d34d84d44b86d54989d5488bd6468ed64590d74393d74195d84098d83e9bd93c9dd93ba0da39a2da37a5db36a8db34aadc32addc30b0dd2fb2dd2db5de2bb8de29bade28bddf26c0df25c2df23c5e021c8e020cae11fcde11dd0e11cd2e21bd5e21ad8e219dae319dde318dfe318e2e418e5e419e7e419eae51aece51befe51cf1e51df4e61ef6e620f8e621fbe723fde725";

let viridisTable = null;

function fail(message) {
  throw new Error(message);
}

function finiteNumber(value, label) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    fail(`Photometric layer ${label} must be finite.`);
  }
  return number;
}

function positiveInteger(value, label) {
  if (!Number.isInteger(value) || value <= 0) {
    fail(`Photometric layer ${label} must be a positive integer.`);
  }
  return value;
}

function parseViridisTable() {
  if (viridisTable) {
    return viridisTable;
  }
  const table = new Uint8Array(256 * 3);
  for (let index = 0; index < 256; index += 1) {
    const offset = index * 6;
    const pixelOffset = index * 3;
    table[pixelOffset] = Number.parseInt(VIRIDIS_HEX.slice(offset, offset + 2), 16);
    table[pixelOffset + 1] = Number.parseInt(VIRIDIS_HEX.slice(offset + 2, offset + 4), 16);
    table[pixelOffset + 2] = Number.parseInt(VIRIDIS_HEX.slice(offset + 4, offset + 6), 16);
  }
  viridisTable = table;
  return table;
}

function clamp01(value) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.min(1, Math.max(0, value));
}

export function clampHeatmapOpacity(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return 0.72;
  }
  return Math.min(1, Math.max(0.15, number));
}

export function formatPpfdTooltipValue(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "";
  }
  return `${Math.round(number)} µmol/m²/s`;
}

function clampIndex(value, maxIndex) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.min(maxIndex, Math.max(0, Math.round(value)));
}

function validateOrientation(orientation) {
  if (!orientation || typeof orientation !== "object") {
    fail("Photometric layer orientation is missing.");
  }
  if (
    orientation.plane !== "xy" ||
    orientation.column_axis !== "x" ||
    orientation.row_axis !== "y" ||
    orientation.column_order !== "ascending" ||
    orientation.row_order !== "ascending" ||
    orientation.storage_order !== "row-major" ||
    orientation.value_index !== "row * grid_width + column"
  ) {
    fail("Photometric layer orientation is not supported by the assembly viewer.");
  }
}

export function photometricLayerUrlFromSceneUrl(sceneUrl, kind = "metadata") {
  if (typeof sceneUrl !== "string" || !sceneUrl.startsWith(ASSEMBLY_SCENE_ROUTE)) {
    fail("The assembly scene URL is missing or not allowed.");
  }
  const queryString = sceneUrl.slice(ASSEMBLY_SCENE_ROUTE.length);
  if (!queryString) {
    fail("The assembly scene URL must include authorization query parameters.");
  }
  if (kind === "metadata" || kind === "json") {
    return `${PHOTOMETRIC_LAYER_ROUTE}?${queryString}`;
  }
  if (kind === "binary" || kind === "bin") {
    return `${PHOTOMETRIC_LAYER_BINARY_ROUTE}?${queryString}`;
  }
  fail(`Unsupported photometric layer sidecar kind: ${String(kind)}`);
}

export function validatePhotometricMetadata(metadata) {
  if (!metadata || typeof metadata !== "object") {
    fail("Photometric layer metadata must be an object.");
  }
  if (metadata.schema_version !== 1) {
    fail("Photometric layer schema_version must be 1.");
  }
  if (metadata.encoding !== "float32-le") {
    fail("Photometric layer encoding must be float32-le.");
  }
  const gridWidth = positiveInteger(metadata.grid_width, "grid_width");
  const gridHeight = positiveInteger(metadata.grid_height, "grid_height");
  const valueCount = positiveInteger(metadata.value_count, "value_count");
  if (gridWidth * gridHeight !== valueCount) {
    fail("Photometric layer dimensions do not match value_count.");
  }

  const bounds = metadata.bounds_m;
  if (!bounds || typeof bounds !== "object") {
    fail("Photometric layer bounds_m is missing.");
  }
  finiteNumber(bounds.x_min, "bounds_m.x_min");
  finiteNumber(bounds.x_max, "bounds_m.x_max");
  finiteNumber(bounds.y_min, "bounds_m.y_min");
  finiteNumber(bounds.y_max, "bounds_m.y_max");
  finiteNumber(bounds.z_m, "bounds_m.z_m");

  finiteNumber(metadata.min_ppfd, "min_ppfd");
  finiteNumber(metadata.max_ppfd, "max_ppfd");
  finiteNumber(metadata.mean_ppfd, "mean_ppfd");
  finiteNumber(metadata.target_ppfd, "target_ppfd");

  const colormap = metadata.colormap;
  if (!colormap || typeof colormap !== "object" || colormap.name !== "viridis") {
    fail("Photometric layer colormap must be viridis.");
  }

  const colorScale = metadata.color_scale;
  if (!colorScale || typeof colorScale !== "object") {
    fail("Photometric layer color_scale is missing.");
  }
  const vmin = finiteNumber(colorScale.vmin, "color_scale.vmin");
  const vmax = finiteNumber(colorScale.vmax, "color_scale.vmax");
  if (!(vmin < vmax)) {
    fail("Photometric layer color_scale.vmin must be less than color_scale.vmax.");
  }

  validateOrientation(metadata.orientation);
  return metadata;
}

export function validatePhotometricBinary(arrayBuffer, metadata) {
  validatePhotometricMetadata(metadata);
  if (!(arrayBuffer instanceof ArrayBuffer)) {
    fail("Photometric layer binary response must be an ArrayBuffer.");
  }
  const expectedBytes = metadata.value_count * Float32Array.BYTES_PER_ELEMENT;
  if (arrayBuffer.byteLength !== expectedBytes) {
    fail(`Photometric layer binary length must be ${expectedBytes} bytes.`);
  }
  const values = new Float32Array(arrayBuffer);
  for (let index = 0; index < values.length; index += 1) {
    if (!Number.isFinite(values[index])) {
      fail("Photometric layer binary values must be finite.");
    }
  }
  return values;
}

export function normalizePpfdForViridis(value, colorScale) {
  if (!colorScale || typeof colorScale !== "object") {
    fail("Photometric layer color_scale is missing.");
  }
  const vmin = finiteNumber(colorScale.vmin, "color_scale.vmin");
  const vmax = finiteNumber(colorScale.vmax, "color_scale.vmax");
  if (!(vmin < vmax)) {
    fail("Photometric layer color_scale.vmin must be less than color_scale.vmax.");
  }
  return clamp01((Number(value) - vmin) / (vmax - vmin));
}

export function viridisRgbaAt(t) {
  const table = parseViridisTable();
  const index = Math.min(255, Math.max(0, Math.round(clamp01(Number(t)) * 255)));
  const offset = index * 3;
  return [table[offset], table[offset + 1], table[offset + 2], 255];
}

export function mapPpfdToViridisRgba(value, metadata) {
  validatePhotometricMetadata(metadata);
  return viridisRgbaAt(normalizePpfdForViridis(value, metadata.color_scale));
}

export function buildHeatmapTexturePixels(values, metadata) {
  validatePhotometricMetadata(metadata);
  if (!(values instanceof Float32Array)) {
    fail("Photometric layer values must be a Float32Array.");
  }
  if (values.length !== metadata.value_count) {
    fail("Photometric layer values length does not match metadata value_count.");
  }
  const pixels = new Uint8Array(values.length * 4);
  const table = parseViridisTable();
  const colorScale = metadata.color_scale;
  for (let valueIndex = 0; valueIndex < values.length; valueIndex += 1) {
    const colorIndex = Math.min(255, Math.max(0, Math.round(normalizePpfdForViridis(values[valueIndex], colorScale) * 255)));
    const colorOffset = colorIndex * 3;
    const pixelOffset = valueIndex * 4;
    pixels[pixelOffset] = table[colorOffset];
    pixels[pixelOffset + 1] = table[colorOffset + 1];
    pixels[pixelOffset + 2] = table[colorOffset + 2];
    pixels[pixelOffset + 3] = 255;
  }
  return pixels;
}

export function createHeatmapDataTexture(values, metadata, three) {
  const pixels = buildHeatmapTexturePixels(values, metadata);
  if (!three?.DataTexture || !three?.RGBAFormat || !three?.LinearFilter) {
    fail("A THREE module with DataTexture, RGBAFormat, and LinearFilter is required.");
  }
  const texture = new three.DataTexture(pixels, metadata.grid_width, metadata.grid_height, three.RGBAFormat);
  texture.minFilter = three.LinearFilter;
  texture.magFilter = three.LinearFilter;
  if (three.SRGBColorSpace) {
    texture.colorSpace = three.SRGBColorSpace;
  }
  texture.flipY = false;
  texture.needsUpdate = true;
  return texture;
}

export function lookupPpfdAtUv(layer, uv) {
  const metadata = layer?.metadata;
  const values = layer?.values;
  if (!metadata || !(values instanceof Float32Array)) {
    fail("Photometric layer lookup requires metadata and Float32Array values.");
  }
  const gridWidth = metadata.grid_width;
  const gridHeight = metadata.grid_height;
  if (!Number.isInteger(gridWidth) || !Number.isInteger(gridHeight) || gridWidth <= 0 || gridHeight <= 0) {
    fail("Photometric layer lookup requires valid grid dimensions.");
  }
  validateOrientation(metadata.orientation);
  if (values.length !== gridWidth * gridHeight) {
    fail("Photometric layer lookup values length does not match grid dimensions.");
  }
  const column = clampIndex(Number(uv?.x) * (gridWidth - 1), gridWidth - 1);
  const row = clampIndex(Number(uv?.y) * (gridHeight - 1), gridHeight - 1);
  return values[row * gridWidth + column];
}

export async function fetchPhotometricLayer(sceneUrl, options = {}) {
  const fetchImpl = options.fetch || globalThis.fetch;
  if (typeof fetchImpl !== "function") {
    fail("A fetch implementation is required to load the photometric layer.");
  }

  const metadataResponse = await fetchImpl(photometricLayerUrlFromSceneUrl(sceneUrl, "metadata"), { cache: "no-store" });
  if (!metadataResponse?.ok) {
    fail(`Photometric layer metadata request failed: ${metadataResponse?.statusText || "request failed"}`);
  }
  const metadata = validatePhotometricMetadata(await metadataResponse.json());

  const binaryResponse = await fetchImpl(photometricLayerUrlFromSceneUrl(sceneUrl, "binary"), { cache: "no-store" });
  if (!binaryResponse?.ok) {
    fail(`Photometric layer binary request failed: ${binaryResponse?.statusText || "request failed"}`);
  }
  const values = validatePhotometricBinary(await binaryResponse.arrayBuffer(), metadata);
  return { metadata, values };
}
