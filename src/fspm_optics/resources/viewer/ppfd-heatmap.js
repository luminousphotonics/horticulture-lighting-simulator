import * as THREE from "three";
import { decodeJson, verifyHash } from "./artifacts.js";

export const SCALAR_STRIDE_BYTES = 12;
export const DEFAULT_OPACITY = 0.70;
export const INTERPOLATION_POLICY_ID = "bilinear_scalar_edge_clamp_v1";
export const COLORMAP_ID = "fspm-viridis-8-linear-srgb-v1";
export const VIRIDIS_ANCHOR_COUNT = 8;

export const VIRIDIS_ANCHORS_RGBA = new Uint8Array([
  68, 1, 84, 255,
  70, 50, 126, 255,
  54, 92, 141, 255,
  39, 127, 142, 255,
  31, 161, 135, 255,
  74, 193, 109, 255,
  160, 218, 57, 255,
  253, 231, 37, 255,
]);

if (VIRIDIS_ANCHORS_RGBA.length !== VIRIDIS_ANCHOR_COUNT * 4) {
  throw new Error("Viridis anchor inventory is incompatible.");
}

const VERTEX_SHADER = `
  varying vec2 scalarUv;
  void main() {
    scalarUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

export const FRAGMENT_SHADER = `
  precision highp float;
  uniform sampler2D scalarMap;
  uniform sampler2D viridisMap;
  uniform vec2 scalarDimensions;
  uniform vec2 displayScale;
  uniform float displayOpacity;
  varying vec2 scalarUv;

  float scalarAt(vec2 cell) {
    vec2 bounded = clamp(cell, vec2(0.0), scalarDimensions - vec2(1.0));
    return texture2D(scalarMap, (bounded + vec2(0.5)) / scalarDimensions).r;
  }

  void main() {
    vec2 gridPosition = scalarUv * scalarDimensions - vec2(0.5);
    vec2 lowerCell = floor(gridPosition);
    vec2 fraction = fract(gridPosition);
    float lowerLeft = scalarAt(lowerCell);
    float lowerRight = scalarAt(lowerCell + vec2(1.0, 0.0));
    float upperLeft = scalarAt(lowerCell + vec2(0.0, 1.0));
    float upperRight = scalarAt(lowerCell + vec2(1.0, 1.0));
    float lower = mix(lowerLeft, lowerRight, fraction.x);
    float upper = mix(upperLeft, upperRight, fraction.x);
    float interpolatedScalar = mix(lower, upper, fraction.y);
    float normalized = clamp(
      (interpolatedScalar - displayScale.x) / (displayScale.y - displayScale.x),
      0.0,
      1.0
    );
    float viridisUv = (
      normalized * ${VIRIDIS_ANCHOR_COUNT - 1}.0 + 0.5
    ) / ${VIRIDIS_ANCHOR_COUNT}.0;
    vec3 viridis = texture2D(viridisMap, vec2(viridisUv, 0.5)).rgb;
    gl_FragColor = vec4(viridis, displayOpacity);
    #include <colorspace_fragment>
  }
`;

export function normalizePpfdForDisplay(value, displayScale) {
  const minimum = displayScale?.minimum_ppfd_umol_m2_s;
  const maximum = displayScale?.maximum_ppfd_umol_m2_s;
  if (
    !Number.isFinite(value)
    || !Number.isFinite(minimum)
    || !Number.isFinite(maximum)
    || maximum <= minimum
  ) {
    throw new Error("PPFD display normalization inputs are incompatible.");
  }
  return clamp((value - minimum) / (maximum - minimum), 0, 1);
}

export function viridisSrgbAtNormalized(value) {
  if (!Number.isFinite(value)) {
    throw new Error("Viridis normalized value must be finite.");
  }
  const position = clamp(value, 0, 1) * (VIRIDIS_ANCHOR_COUNT - 1);
  const lowerIndex = Math.min(Math.floor(position), VIRIDIS_ANCHOR_COUNT - 2);
  const upperIndex = lowerIndex + 1;
  const fraction = position - lowerIndex;
  const color = [];
  for (let channel = 0; channel < 3; channel += 1) {
    const lower = srgb8ToLinear(
      VIRIDIS_ANCHORS_RGBA[lowerIndex * 4 + channel],
    );
    const upper = srgb8ToLinear(
      VIRIDIS_ANCHORS_RGBA[upperIndex * 4 + channel],
    );
    color.push(linearToSrgb8(mix(lower, upper, fraction)));
  }
  return Object.freeze(color);
}

export function ppfdSrgbAtDisplayScale(value, displayScale) {
  return viridisSrgbAtNormalized(normalizePpfdForDisplay(value, displayScale));
}

export function viridisLegendCssGradient() {
  const stops = Array.from({ length: 65 }, (_, index) => {
    const normalized = index / 64;
    const [red, green, blue] = viridisSrgbAtNormalized(normalized);
    return `rgb(${red} ${green} ${blue}) ${normalized * 100}%`;
  });
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}

class PpfdHeatmapCorruptError extends Error {}

export function validatePpfdHeatmapSceneReference(scene) {
  const source = scene?.ppfd_heatmap;
  const fields = [
    "availability",
    "display_scale",
    "grid",
    "interpolation_policy_id",
    "metadata",
    "scalar_field",
    "scalar_units",
    "source_field_identity_sha256",
  ];
  if (source?.target_coverage !== undefined) fields.push("target_coverage");
  assertExactObject(source, fields, "PPFD heatmap scene reference");
  if (source.availability !== "available") {
    throw new Error("PPFD heatmap scene availability is incompatible.");
  }
  validateArtifactRecord(
    source.metadata,
    "ppfd-heatmap/visualization.json",
    "PPFD heatmap metadata",
    ["byte_length", "filename", "sha256"],
  );
  validateArtifactRecord(
    source.scalar_field,
    "ppfd-heatmap/ppfd-scatter.f32le.bin",
    "PPFD heatmap scalar field",
    [
      "byte_length",
      "byte_order",
      "component_type",
      "count",
      "filename",
      "record_layout",
      "sha256",
      "stride_bytes",
    ],
  );
  const scalar = source.scalar_field;
  if (
    scalar.component_type !== "float32"
    || scalar.byte_order !== "little-endian"
    || scalar.record_layout !== "x_m,y_m,ppfd_umol_m2_s"
    || scalar.stride_bytes !== SCALAR_STRIDE_BYTES
    || !Number.isSafeInteger(scalar.count)
    || scalar.count < 4
    || scalar.byte_length !== scalar.count * SCALAR_STRIDE_BYTES
  ) {
    throw new Error("PPFD heatmap scalar-field contract is incompatible.");
  }
  const grid = source.grid;
  assertExactObject(grid, [
    "cell_edge_bounds_m",
    "coordinate_system",
    "height",
    "reference_plane_z_m",
    "scientific_to_viewer",
    "width",
    "x_centers_m",
    "y_centers_m",
  ], "PPFD heatmap grid");
  if (
    !Number.isSafeInteger(grid.width)
    || !Number.isSafeInteger(grid.height)
    || grid.width < 2
    || grid.height < 2
    || grid.width * grid.height !== scalar.count
    || grid.coordinate_system !== "right-handed scientific XY, meters, Z-up"
    || grid.scientific_to_viewer !== "(x, y, z) -> (x, z, -y)"
    || !Number.isFinite(grid.reference_plane_z_m)
  ) {
    throw new Error("PPFD heatmap grid contract is incompatible.");
  }
  const xCenters = validateRegularAxis(grid.x_centers_m, grid.width, "X");
  const yCenters = validateRegularAxis(grid.y_centers_m, grid.height, "Y");
  const expectedBounds = cellEdgeBounds(xCenters, yCenters);
  assertExactObject(
    grid.cell_edge_bounds_m,
    ["x_max", "x_min", "y_max", "y_min"],
    "PPFD heatmap cell-edge bounds",
  );
  if (!recordsEqual(grid.cell_edge_bounds_m, expectedBounds)) {
    throw new Error("PPFD heatmap cell-edge bounds are incompatible.");
  }
  assertExactObject(source.display_scale, [
    "colormap",
    "maximum_ppfd_umol_m2_s",
    "minimum_ppfd_umol_m2_s",
  ], "PPFD heatmap display scale");
  if (
    source.scalar_units !== "micromole_per_square_meter_per_second"
    || source.display_scale.colormap !== COLORMAP_ID
    || !Number.isFinite(source.display_scale.minimum_ppfd_umol_m2_s)
    || !Number.isFinite(source.display_scale.maximum_ppfd_umol_m2_s)
    || source.display_scale.maximum_ppfd_umol_m2_s
      <= source.display_scale.minimum_ppfd_umol_m2_s
    || source.interpolation_policy_id !== INTERPOLATION_POLICY_ID
  ) {
    throw new Error("PPFD heatmap scalar display contract is incompatible.");
  }
  assertSha256(source.source_field_identity_sha256, "PPFD heatmap field identity");
  return source;
}

export function validatePpfdHeatmapMetadata(metadata, scene, source) {
  assertExactObject(metadata, [
    "annotations",
    "artifacts",
    "display",
    "field",
    "grid",
    "orientation",
    "overlay",
    "run_id",
    "scatter",
    "schema_id",
    "schema_version",
    "transforms",
  ], "Stage A visualization metadata");
  if (
    metadata.schema_id !== "fspm-optics.ppfd-visualization"
    || metadata.schema_version !== 1
    || metadata.run_id !== scene.run.run_id
  ) {
    throw new PpfdHeatmapCorruptError(
      "Stage A visualization schema or run identity is incompatible.",
    );
  }
  const field = metadata.field;
  const grid = metadata.grid;
  const scatter = metadata.scatter;
  const display = metadata.display;
  const sceneGrid = source.grid;
  const sceneScalar = source.scalar_field;
  const interpolation = grid?.interpolation;
  const expectedAnchors = Array.from(
    { length: VIRIDIS_ANCHORS_RGBA.length / 4 },
    (_, index) => [...VIRIDIS_ANCHORS_RGBA.slice(index * 4, index * 4 + 3)],
  );
  const transformFields = [
    "clipping",
    "correction",
    "hidden_normalization",
    "physics_recomputed",
    "sample_exclusion",
    "smoothing",
    "symmetrization",
    "target_rescaling",
  ];
  if (
    field?.identity_sha256 !== source.source_field_identity_sha256
    || scatter?.source_field_identity_sha256 !== source.source_field_identity_sha256
    || field.sample_count !== sceneScalar.count
    || field.coordinate_system !== sceneGrid.coordinate_system
    || field.reference_plane_z_m !== sceneGrid.reference_plane_z_m
    || field.value_units !== source.scalar_units
    || grid?.kind !== "regular"
    || grid.resolution?.x !== sceneGrid.width
    || grid.resolution?.y !== sceneGrid.height
    || !recordsEqual(grid.x_centers_m, sceneGrid.x_centers_m)
    || !recordsEqual(grid.y_centers_m, sceneGrid.y_centers_m)
    || !recordsEqual(grid.extent_m, sceneGrid.cell_edge_bounds_m)
    || grid.reshape_order !== "rows_ascending_y_columns_ascending_x"
    || grid.image_origin !== "lower"
    || !interpolation
    || Object.keys(interpolation).sort().join(",")
      !== [
        "applied",
        "fill_behavior",
        "method",
        "original_sample_identity_sha256",
        "resolution",
      ].join(",")
    || interpolation.applied !== false
    || interpolation.method !== null
    || interpolation.resolution !== null
    || interpolation.fill_behavior !== null
    || interpolation.original_sample_identity_sha256
      !== source.source_field_identity_sha256
    || metadata.orientation?.sample_coordinates_transformed !== false
    || metadata.orientation?.positive_y_is_image_up !== true
    || scatter.filename !== "ppfd-scatter.f32le.bin"
    || scatter.sha256 !== sceneScalar.sha256
    || scatter.byte_length !== sceneScalar.byte_length
    || scatter.byte_order !== sceneScalar.byte_order
    || scatter.component_type !== sceneScalar.component_type
    || scatter.record_layout !== sceneScalar.record_layout
    || scatter.stride_bytes !== sceneScalar.stride_bytes
    || scatter.count !== sceneScalar.count
    || !recordsEqual(
      display?.color_limits_ppfd_umol_m2_s,
      [
        source.display_scale.minimum_ppfd_umol_m2_s,
        source.display_scale.maximum_ppfd_umol_m2_s,
      ],
    )
    || display.colormap?.name !== COLORMAP_ID
    || !recordsEqual(display.colormap?.anchors_srgb_8bit, expectedAnchors)
    || !metadata.transforms
    || Object.keys(metadata.transforms).sort().join(",")
      !== transformFields.join(",")
    || Object.entries(metadata.transforms).some(
      ([key, value]) => key === "target_rescaling"
        ? typeof value !== "boolean" : value !== false,
    )
    || metadata.artifacts?.["ppfd-scatter.f32le.bin"]?.sha256
      !== sceneScalar.sha256
    || metadata.artifacts?.["ppfd-scatter.f32le.bin"]?.byte_length
      !== sceneScalar.byte_length
  ) {
    throw new PpfdHeatmapCorruptError(
      "Stage A visualization metadata disagrees with the authenticated scene.",
    );
  }
  return metadata;
}

export function parsePpfdScatter(bytes, source) {
  const scalar = source.scalar_field;
  const grid = source.grid;
  if (!(bytes instanceof ArrayBuffer) || bytes.byteLength !== scalar.byte_length) {
    throw new PpfdHeatmapCorruptError("PPFD scalar bytes are truncated.");
  }
  const xIndices = float32CenterIndices(grid.x_centers_m, "X");
  const yIndices = float32CenterIndices(grid.y_centers_m, "Y");
  const values = new Float32Array(scalar.count);
  const occupied = new Uint8Array(scalar.count);
  const view = new DataView(bytes);
  for (let record = 0; record < scalar.count; record += 1) {
    const offset = record * SCALAR_STRIDE_BYTES;
    const x = view.getFloat32(offset, true);
    const y = view.getFloat32(offset + 4, true);
    const ppfd = view.getFloat32(offset + 8, true);
    const xIndex = xIndices.get(x);
    const yIndex = yIndices.get(y);
    if (
      !Number.isFinite(x)
      || !Number.isFinite(y)
      || !Number.isFinite(ppfd)
      || ppfd < 0
      || xIndex === undefined
      || yIndex === undefined
    ) {
      throw new PpfdHeatmapCorruptError(
        "PPFD scalar field contains an invalid record.",
      );
    }
    const gridIndex = yIndex * grid.width + xIndex;
    if (occupied[gridIndex]) {
      throw new PpfdHeatmapCorruptError("PPFD scalar field contains a duplicate cell.");
    }
    occupied[gridIndex] = 1;
    values[gridIndex] = ppfd;
  }
  if (occupied.some((value) => value !== 1)) {
    throw new PpfdHeatmapCorruptError("PPFD scalar field has a missing cell.");
  }
  return Object.freeze({
    bounds: source.grid.cell_edge_bounds_m,
    height: grid.height,
    values,
    width: grid.width,
  });
}

export function sampleBilinearScalarGrid(grid, scientificX, scientificY) {
  const { x_min: minX, x_max: maxX, y_min: minY, y_max: maxY } = grid.bounds;
  const u = clamp((scientificX - minX) / (maxX - minX), 0, 1);
  const v = clamp((scientificY - minY) / (maxY - minY), 0, 1);
  const gridX = u * grid.width - 0.5;
  const gridY = v * grid.height - 0.5;
  const x0 = Math.floor(gridX);
  const y0 = Math.floor(gridY);
  const fractionX = gridX - x0;
  const fractionY = gridY - y0;
  const scalarAt = (x, y) => grid.values[
    clamp(y, 0, grid.height - 1) * grid.width
      + clamp(x, 0, grid.width - 1)
  ];
  const lower = mix(scalarAt(x0, y0), scalarAt(x0 + 1, y0), fractionX);
  const upper = mix(
    scalarAt(x0, y0 + 1),
    scalarAt(x0 + 1, y0 + 1),
    fractionX,
  );
  return mix(lower, upper, fractionY);
}

export async function loadValidatedPpfdHeatmap(
  scene,
  sceneUrl = "./scene.v1.json",
  signal,
) {
  const source = validatePpfdHeatmapSceneReference(scene);
  const sceneBase = new URL("./", new URL(sceneUrl, location.href));
  const metadataUrl = runLocalUrl(sceneBase, source.metadata.filename);
  const metadataBytes = await fetchBytesWithSignal(metadataUrl, signal);
  validateFetchedLength(metadataBytes, source.metadata, "PPFD heatmap metadata");
  await verifyHash(
    metadataBytes,
    source.metadata.sha256,
    "PPFD heatmap metadata",
  );
  const metadata = validatePpfdHeatmapMetadata(
    decodeJson(metadataBytes, "PPFD heatmap metadata"),
    scene,
    source,
  );

  const scalarUrl = runLocalUrl(sceneBase, source.scalar_field.filename);
  const scalarBytes = await fetchBytesWithSignal(scalarUrl, signal);
  validateFetchedLength(scalarBytes, source.scalar_field, "PPFD scalar field");
  await verifyHash(
    scalarBytes,
    source.scalar_field.sha256,
    "PPFD scalar field",
  );
  const grid = parsePpfdScatter(scalarBytes, source);
  return Object.freeze({ grid, metadata, source });
}

export function createPpfdHeatmapResources({ validated, renderer }) {
  const { grid, source } = validated;
  const maxTextureSize = renderer?.capabilities?.maxTextureSize;
  if (
    !Number.isSafeInteger(maxTextureSize)
    || maxTextureSize <= 0
    || grid.width > maxTextureSize
    || grid.height > maxTextureSize
  ) {
    throw new Error("GPU MAX_TEXTURE_SIZE cannot support the PPFD heatmap grid.");
  }
  let geometry;
  let material;
  let scalarTexture;
  let viridisTexture;
  let mesh;
  try {
    scalarTexture = new THREE.DataTexture(
      grid.values,
      grid.width,
      grid.height,
      THREE.RedFormat,
      THREE.FloatType,
    );
    scalarTexture.magFilter = THREE.NearestFilter;
    scalarTexture.minFilter = THREE.NearestFilter;
    scalarTexture.generateMipmaps = false;
    scalarTexture.flipY = false;
    scalarTexture.unpackAlignment = 1;
    scalarTexture.needsUpdate = true;

    viridisTexture = new THREE.DataTexture(
      VIRIDIS_ANCHORS_RGBA,
      VIRIDIS_ANCHORS_RGBA.length / 4,
      1,
      THREE.RGBAFormat,
      THREE.UnsignedByteType,
    );
    viridisTexture.magFilter = THREE.LinearFilter;
    viridisTexture.minFilter = THREE.LinearFilter;
    viridisTexture.generateMipmaps = false;
    // The lookup bytes are authored in sRGB. WebGL decodes them once to the
    // linear working space; colorspace_fragment performs the one output encode.
    viridisTexture.colorSpace = THREE.SRGBColorSpace;
    viridisTexture.needsUpdate = true;

    const bounds = source.grid.cell_edge_bounds_m;
    const planeWidth = bounds.x_max - bounds.x_min;
    const planeHeight = bounds.y_max - bounds.y_min;
    geometry = new THREE.PlaneGeometry(planeWidth, planeHeight, 1, 1);
    material = new THREE.ShaderMaterial({
      depthWrite: false,
      fragmentShader: FRAGMENT_SHADER,
      polygonOffset: true,
      polygonOffsetFactor: -1,
      polygonOffsetUnits: -1,
      side: THREE.DoubleSide,
      toneMapped: false,
      transparent: true,
      uniforms: {
        displayOpacity: { value: DEFAULT_OPACITY },
        displayScale: {
          value: new THREE.Vector2(
            source.display_scale.minimum_ppfd_umol_m2_s,
            source.display_scale.maximum_ppfd_umol_m2_s,
          ),
        },
        scalarDimensions: { value: new THREE.Vector2(grid.width, grid.height) },
        scalarMap: { value: scalarTexture },
        viridisMap: { value: viridisTexture },
      },
      vertexShader: VERTEX_SHADER,
    });
    material.forceSinglePass = true;
    mesh = new THREE.Mesh(geometry, material);
    mesh.name = "authenticated-stage-a-ppfd-heatmap";
    mesh.rotation.x = -Math.PI / 2;
    const viewerCenterZ = -(bounds.y_min + bounds.y_max) * 0.5;
    mesh.position.set(
      (bounds.x_min + bounds.x_max) * 0.5,
      source.grid.reference_plane_z_m,
      viewerCenterZ === 0 ? 0 : viewerCenterZ,
    );
    mesh.renderOrder = 1;
    mesh.updateMatrixWorld(true);
  } catch (error) {
    geometry?.dispose();
    material?.dispose();
    scalarTexture?.dispose();
    viridisTexture?.dispose();
    throw error;
  }
  let disposed = false;
  return Object.freeze({
    grid,
    material,
    mesh,
    scalarTexture,
    source,
    viridisTexture,
    dispose() {
      if (disposed) return;
      disposed = true;
      mesh.removeFromParent();
      geometry.dispose();
      material.dispose();
      scalarTexture.dispose();
      viridisTexture.dispose();
    },
  });
}

export function createPpfdHeatmapController({
  sceneManifest,
  sceneUrl = "./scene.v1.json",
  renderer,
  camera,
  sceneGraph,
  canvas,
  toggle,
  opacity,
  opacityOutput,
  status,
  retry,
  legend,
  legendMinimum,
  legendMaximum,
  tooltip,
  invalidate,
  onResourcesAvailable = () => {},
  onResourcesUnavailable = () => {},
}) {
  let resources = null;
  let loadAbort = null;
  let loadGeneration = 0;
  let disposed = false;
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const notifyUnavailable = (error) => {
    try {
      onResourcesUnavailable(error);
    } catch (callbackError) {
      console.error("Target Coverage resource callback failed.", callbackError);
    }
  };
  const notifyAvailable = (payload) => {
    try {
      onResourcesAvailable(payload);
    } catch (error) {
      notifyUnavailable(error);
    }
  };

  const hideTooltip = () => {
    tooltip.hidden = true;
    tooltip.textContent = "";
  };
  const setControlsEnabled = (enabled) => {
    toggle.disabled = !enabled;
    opacity.disabled = !enabled;
  };
  const setStatus = (state, message) => {
    status.dataset.state = state;
    status.textContent = message;
  };
  const updateOpacityOutput = () => {
    opacityOutput.value = `${opacity.value}%`;
    opacityOutput.textContent = `${opacity.value}%`;
  };
  const handleToggle = () => {
    if (!resources) return;
    resources.mesh.visible = toggle.checked;
    if (!toggle.checked) hideTooltip();
    setStatus(
      toggle.checked ? "available" : "hidden",
      toggle.checked ? "Authenticated Stage A PPFD heatmap." : "Heatmap hidden.",
    );
    invalidate();
  };
  const handleOpacity = () => {
    if (!resources) return;
    const value = Number(opacity.value) / 100;
    if (!Number.isFinite(value) || value < 0.15 || value > 1) return;
    resources.material.uniforms.displayOpacity.value = value;
    updateOpacityOutput();
    invalidate();
  };
  const handlePointerMove = (event) => {
    if (!resources?.mesh.visible) {
      hideTooltip();
      return;
    }
    const rectangle = canvas.getBoundingClientRect();
    pointer.set(
      ((event.clientX - rectangle.left) / rectangle.width) * 2 - 1,
      -((event.clientY - rectangle.top) / rectangle.height) * 2 + 1,
    );
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObject(resources.mesh, false)[0];
    if (!hit) {
      hideTooltip();
      return;
    }
    const value = sampleBilinearScalarGrid(
      resources.grid,
      hit.point.x,
      -hit.point.z,
    );
    tooltip.textContent = `PPFD: ${Math.round(value)} µmol/m²/s`;
    tooltip.style.left = `${event.clientX + 12}px`;
    tooltip.style.top = `${event.clientY + 12}px`;
    tooltip.hidden = false;
  };
  const handlePointerLeave = () => hideTooltip();

  const load = async (retrying = false) => {
    const generation = ++loadGeneration;
    loadAbort?.abort();
    loadAbort = new AbortController();
    setControlsEnabled(false);
    retry.hidden = true;
    hideTooltip();
    setStatus(
      retrying ? "loading" : "loading",
      retrying ? "Retry in progress…" : "Loading authenticated PPFD heatmap…",
    );
    let nextResources = null;
    try {
      const validated = await loadValidatedPpfdHeatmap(
        sceneManifest,
        sceneUrl,
        loadAbort.signal,
      );
      if (disposed || generation !== loadGeneration || loadAbort.signal.aborted) return;
      nextResources = createPpfdHeatmapResources({ validated, renderer });
      if (disposed || generation !== loadGeneration || loadAbort.signal.aborted) {
        nextResources.dispose();
        return;
      }
      const previousResources = resources;
      resources = nextResources;
      nextResources = null;
      sceneGraph.add(resources.mesh);
      resources.mesh.visible = toggle.checked;
      resources.material.uniforms.displayOpacity.value = Number(opacity.value) / 100;
      legendMinimum.textContent = formatLegendValue(
        validated.source.display_scale.minimum_ppfd_umol_m2_s,
      );
      legendMaximum.textContent = formatLegendValue(
        validated.source.display_scale.maximum_ppfd_umol_m2_s,
      );
      const legendGradient = legend.querySelector(".ppfd-legend-gradient");
      if (legendGradient) {
        legendGradient.style.backgroundImage = viridisLegendCssGradient();
      }
      legend.dataset.minimumPpfd = String(
        validated.source.display_scale.minimum_ppfd_umol_m2_s,
      );
      legend.dataset.maximumPpfd = String(
        validated.source.display_scale.maximum_ppfd_umol_m2_s,
      );
      legend.hidden = false;
      notifyAvailable({ resources, validated });
      previousResources?.dispose();
      setControlsEnabled(true);
      setStatus(
        toggle.checked ? "available" : "hidden",
        toggle.checked ? "Authenticated Stage A PPFD heatmap." : "Heatmap hidden.",
      );
      invalidate();
    } catch (error) {
      nextResources?.dispose();
      if (disposed || generation !== loadGeneration || error?.name === "AbortError") return;
      notifyUnavailable(error);
      resources?.dispose();
      resources = null;
      legend.hidden = true;
      setControlsEnabled(false);
      const corrupt = error instanceof PpfdHeatmapCorruptError
        || /SHA-256|incompatible|invalid|truncated|duplicate|missing/.test(error.message);
      setStatus(
        corrupt ? "corrupt" : "unavailable",
        corrupt
          ? `Heatmap corrupt: ${error.message}`
          : `Heatmap unavailable: ${error.message}`,
      );
      retry.hidden = corrupt;
      invalidate();
    }
  };
  const handleRetry = () => load(true);

  toggle.addEventListener("change", handleToggle);
  opacity.addEventListener("input", handleOpacity);
  retry.addEventListener("click", handleRetry);
  canvas.addEventListener("pointermove", handlePointerMove);
  canvas.addEventListener("pointerleave", handlePointerLeave);
  toggle.checked = true;
  opacity.value = "70";
  updateOpacityOutput();
  legend.hidden = true;
  retry.hidden = true;

  if (sceneManifest.ppfd_heatmap === undefined) {
    setControlsEnabled(false);
    setStatus(
      "unavailable",
      "Authenticated PPFD heatmap data is unavailable for this historical scene.",
    );
  } else {
    try {
      validatePpfdHeatmapSceneReference(sceneManifest);
      void load(false);
    } catch (error) {
      setControlsEnabled(false);
      setStatus("corrupt", `Heatmap source is invalid: ${error.message}`);
      notifyUnavailable(error);
    }
  }

  return Object.freeze({
    dispose() {
      if (disposed) return;
      disposed = true;
      loadGeneration += 1;
      loadAbort?.abort();
      toggle.removeEventListener("change", handleToggle);
      opacity.removeEventListener("input", handleOpacity);
      retry.removeEventListener("click", handleRetry);
      canvas.removeEventListener("pointermove", handlePointerMove);
      canvas.removeEventListener("pointerleave", handlePointerLeave);
      notifyUnavailable(new Error("PPFD heatmap resources were disposed."));
      resources?.dispose();
      resources = null;
      hideTooltip();
      invalidate();
    },
  });
}

function validateArtifactRecord(record, filename, label, fields) {
  if (
    !record
    || typeof record !== "object"
    || Array.isArray(record)
    || Object.keys(record).sort().join(",") !== fields.slice().sort().join(",")
    || record.filename !== filename
    || !Number.isSafeInteger(record.byte_length)
    || record.byte_length <= 0
  ) {
    throw new Error(`${label} artifact record is incompatible.`);
  }
  assertSha256(record.sha256, label);
}

function validateRegularAxis(values, expectedCount, label) {
  if (
    !Array.isArray(values)
    || values.length !== expectedCount
    || values.some((value) => !Number.isFinite(value))
  ) {
    throw new Error(`PPFD heatmap ${label} centers are incompatible.`);
  }
  const differences = values.slice(1).map((value, index) => value - values[index]);
  if (
    differences.some((value) => value <= 0)
    || differences.slice(1).some(
      (value) => Math.abs(value - differences[0])
        > Math.max(1e-12, Math.abs(differences[0]) * 1e-10),
    )
  ) {
    throw new Error(`PPFD heatmap ${label} centers are not a regular axis.`);
  }
  return values;
}

function cellEdgeBounds(xCenters, yCenters) {
  return {
    x_min: xCenters[0] - (xCenters[1] - xCenters[0]) * 0.5,
    x_max: xCenters.at(-1) + (xCenters.at(-1) - xCenters.at(-2)) * 0.5,
    y_min: yCenters[0] - (yCenters[1] - yCenters[0]) * 0.5,
    y_max: yCenters.at(-1) + (yCenters.at(-1) - yCenters.at(-2)) * 0.5,
  };
}

function float32CenterIndices(centers, label) {
  const indices = new Map();
  centers.forEach((value, index) => indices.set(Math.fround(value), index));
  if (indices.size !== centers.length) {
    throw new PpfdHeatmapCorruptError(
      `PPFD ${label} centers collapse after Float32 conversion.`,
    );
  }
  return indices;
}

async function fetchBytesWithSignal(url, signal) {
  const response = await fetch(url, { cache: "no-store", signal });
  if (!response.ok) {
    throw new Error(`Unable to load ${url.pathname}: HTTP ${response.status}.`);
  }
  return response.arrayBuffer();
}

function runLocalUrl(sceneBase, filename) {
  const url = new URL(filename, sceneBase);
  if (url.origin !== sceneBase.origin || !url.pathname.startsWith(sceneBase.pathname)) {
    throw new Error("PPFD heatmap artifact path escaped the completed run viewer.");
  }
  return url;
}

function validateFetchedLength(bytes, record, label) {
  if (!(bytes instanceof ArrayBuffer) || bytes.byteLength !== record.byte_length) {
    throw new PpfdHeatmapCorruptError(`${label} byte length is invalid.`);
  }
}

function assertExactObject(value, fields, label) {
  if (
    !value
    || typeof value !== "object"
    || Array.isArray(value)
    || Object.keys(value).sort().join(",") !== fields.slice().sort().join(",")
  ) {
    throw new Error(`${label} field inventory is incompatible.`);
  }
}

function assertSha256(value, label) {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
    throw new Error(`${label} SHA-256 is incompatible.`);
  }
}

function recordsEqual(left, right) {
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left)
      && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => recordsEqual(value, right[index]));
  }
  if (left && right && typeof left === "object" && typeof right === "object") {
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length
      && leftKeys.every((key, index) => (
        key === rightKeys[index] && recordsEqual(left[key], right[key])
      ));
  }
  return left === right;
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function mix(left, right, fraction) {
  return left * (1 - fraction) + right * fraction;
}

function srgb8ToLinear(value) {
  const encoded = value / 255;
  return encoded <= 0.04045
    ? encoded / 12.92
    : ((encoded + 0.055) / 1.055) ** 2.4;
}

function linearToSrgb8(value) {
  const encoded = value <= 0.0031308
    ? value * 12.92
    : 1.055 * value ** (1 / 2.4) - 0.055;
  return Math.round(clamp(encoded, 0, 1) * 255);
}

function formatLegendValue(value) {
  return Number(value.toFixed(1)).toString();
}
