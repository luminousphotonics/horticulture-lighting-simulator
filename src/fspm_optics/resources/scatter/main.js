import {
  buildPresentationSvg,
  colorForPpfd,
  createSvgBlob,
  displayHeight as exportedDisplayHeight,
  downloadBlob,
  presentationFilename,
  rasterizeSvgBlobToPng,
} from "./presentation-export.js";

const status = document.querySelector("#status");
const fieldInfo = document.querySelector("#field-info");
const resetButton = document.querySelector("#reset-view");
const reloadButton = document.querySelector("#reload-scatter");
const retryButton = document.querySelector("#retry-scatter");
const gateMessage = document.querySelector("#gate-message");
const emptyState = document.querySelector("#empty-state");
const viewport = document.querySelector("#viewport");
const legendMinimum = document.querySelector("#legend-minimum");
const legendCenter = document.querySelector("#legend-center");
const legendMaximum = document.querySelector("#legend-maximum");
const legendGradient = document.querySelector("#legend-gradient");
const zRangeMinimum = document.querySelector("#z-range-minimum");
const zRangeTarget = document.querySelector("#z-range-target");
const zRangeMaximum = document.querySelector("#z-range-maximum");
const exportTitle = document.querySelector("#export-title");
const exportSvgButton = document.querySelector("#export-svg");
const exportPngButton = document.querySelector("#export-png");
const exportStatus = document.querySelector("#export-status");

let metadata = null;
let current = null;
let initialCamera = null;

initializeMetadata();
resetButton.addEventListener("click", resetView);
reloadButton.addEventListener("click", loadScatter);
exportSvgButton.addEventListener("click", () => exportPresentation("svg"));
exportPngButton.addEventListener("click", () => exportPresentation("png"));
retryButton.addEventListener("click", () => {
  if (metadata) loadScatter();
  else initializeMetadata();
});
window.addEventListener("pagehide", disposeCurrent, {once: true});

async function initializeMetadata() {
  try {
    const {runId, artifactRoot} = artifactContextFromPath();
    const response = await fetch(`${artifactRoot}/visualization.json`, {cache: "no-store"});
    if (!response.ok) throw new Error("Visualization metadata is unavailable.");
    const value = await response.json();
    validateMetadata(value, runId);
    metadata = value;
    const limits = value.display.color_limits_ppfd_umol_m2_s;
    const vertical = value.scatter.vertical_display_transform;
    fieldInfo.textContent = [
      `Run ${runId}`,
      `Field ${value.field.identity_sha256}`,
      `${value.field.sample_count} samples`,
      `${value.grid.resolution.x} × ${value.grid.resolution.y} regular grid`,
      `PPFD ${format(limits[0])}–${format(limits[1])} µmol/m²/s`,
      `Raw PPFD ${format(vertical.raw_minimum_ppfd_umol_m2_s)}–${format(vertical.raw_maximum_ppfd_umol_m2_s)} µmol/m²/s`,
      "Origin lower · aspect equal · no interpolation",
    ].join("\n");
    legendMinimum.textContent = format(limits[0]);
    if (value.display.reference.kind === "requested_lighting_target") {
      legendCenter.textContent = format(value.display.requested_lighting_target_ppfd_umol_m2_s);
    } else if (value.display.reference.kind === "requested_sampled_ppfd_cap") {
      legendCenter.textContent = format(value.display.requested_sampled_ppfd_cap_umol_m2_s);
    } else {
      legendCenter.textContent = format(value.display.reference_ppfd_umol_m2_s);
    }
    legendMaximum.textContent = format(limits[1]);
    zRangeMinimum.textContent = format(vertical.declared_vmin_ppfd_umol_m2_s);
    if (vertical.reference_kind === "requested_lighting_target") {
      zRangeTarget.textContent = format(vertical.requested_lighting_target_ppfd_umol_m2_s);
    } else if (vertical.reference_kind === "requested_sampled_ppfd_cap") {
      zRangeTarget.textContent = format(vertical.requested_sampled_ppfd_cap_umol_m2_s);
    } else {
      zRangeTarget.textContent = format(vertical.reference_ppfd_umol_m2_s);
    }
    zRangeMaximum.textContent = format(vertical.declared_vmax_ppfd_umol_m2_s);
    legendGradient.style.background = `linear-gradient(to top, ${value.display.colormap.anchors_srgb_8bit.map((color) => `rgb(${color.join(",")})`).join(",")})`;
    status.textContent = "Metadata validated · loading scatter";
    await loadScatter();
  } catch (error) {
    unavailable(error);
  }
}

async function loadScatter() {
  if (!metadata) return;
  setExportEnabled(false);
  const blocking = current === null;
  if (blocking) {
    emptyState.hidden = false;
    gateMessage.textContent = "Loading and validating the compact PPFD field…";
    retryButton.hidden = true;
  }
  reloadButton.disabled = true;
  status.textContent = "Fetching and validating compact Float32 field…";
  try {
    const {artifactRoot} = artifactContextFromPath();
    const response = await fetch(`${artifactRoot}/${metadata.scatter.filename}`, {cache: "no-store"});
    if (!response.ok) throw new Error("Scatter data is unavailable.");
    const buffer = await response.arrayBuffer();
    await verifyHash(buffer, metadata.scatter.sha256);
    const points = parseScatter(buffer, metadata.scatter);
    const [THREE, {OrbitControls}] = await Promise.all([
      import("three"),
      import("three/addons/controls/OrbitControls.js"),
    ]);
    disposeCurrent();
    current = createViewer(THREE, OrbitControls, points, metadata);
    emptyState.hidden = true;
    resetButton.disabled = false;
    reloadButton.disabled = false;
    setExportEnabled(true);
    setExportStatus("Presentation export ready.");
    status.textContent = `Validated ${metadata.field.sample_count} samples · interactive`;
  } catch (error) {
    unavailable(error);
    reloadButton.disabled = current === null;
    setExportEnabled(current !== null);
  }
}

function createViewer(THREE, OrbitControls, points, value) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x07100d);
  const renderer = new THREE.WebGLRenderer({antialias: true, powerPreference: "high-performance"});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  viewport.replaceChildren(renderer.domElement);

  const camera = new THREE.PerspectiveCamera(42, 1, 0.01, 1000);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = false;
  controls.enablePan = true;
  controls.enableZoom = true;

  const xMin = Math.min(...points.x);
  const xMax = Math.max(...points.x);
  const yMin = Math.min(...points.y);
  const yMax = Math.max(...points.y);
  const limits = value.display.color_limits_ppfd_umol_m2_s;
  const vertical = value.scatter.vertical_display_transform;
  const horizontalSpan = vertical.horizontal_reference_span_m;
  const verticalSpan = vertical.display_vertical_span;
  const sceneFloor = vertical.scene_floor_height;
  const frameLower = vertical.camera_frame_lower_height;
  const frameUpper = vertical.camera_frame_upper_height;
  const frameVerticalSpan = frameUpper - frameLower;
  const worldSpan = Math.max(horizontalSpan, frameVerticalSpan);
  const positions = new Float32Array(points.count * 3);
  const colors = new Float32Array(points.count * 3);
  for (let index = 0; index < points.count; index += 1) {
    const color = colorForPpfd(points.ppfd[index], limits, value.display.colormap.anchors_srgb_8bit);
    positions[index * 3] = points.x[index];
    positions[index * 3 + 1] = points.y[index];
    positions[index * 3 + 2] = displayHeight(points.ppfd[index], vertical);
    colors[index * 3] = color[0] / 255;
    colors[index * 3 + 1] = color[1] / 255;
    colors[index * 3 + 2] = color[2] / 255;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  const material = new THREE.PointsMaterial({size: 4, vertexColors: true, sizeAttenuation: false});
  const cloud = new THREE.Points(geometry, material);
  scene.add(cloud);

  const grid = new THREE.GridHelper(horizontalSpan, Math.max(2, Math.min(20, value.grid.resolution.x - 1)), 0x52645a, 0x26342d);
  grid.rotation.x = Math.PI / 2;
  grid.position.z = sceneFloor;
  scene.add(grid);
  const axes = new THREE.AxesHelper(Math.max(horizontalSpan * .28, verticalSpan * .28));
  axes.position.set(xMin, yMin, sceneFloor);
  scene.add(axes);

  const center = new THREE.Vector3((xMin + xMax) / 2, (yMin + yMax) / 2, (frameLower + frameUpper) / 2);
  camera.far = Math.max(1000, worldSpan * 20);
  camera.updateProjectionMatrix();
  camera.position.set(center.x + worldSpan * 1.15, center.y - worldSpan * 1.35, center.z + worldSpan * 1.15);
  controls.target.copy(center);
  controls.update();
  initialCamera = {position: camera.position.clone(), target: controls.target.clone()};

  const render = () => renderer.render(scene, camera);
  const resize = () => {
    const bounds = viewport.getBoundingClientRect();
    renderer.setSize(Math.max(1, bounds.width), Math.max(1, bounds.height), false);
    camera.aspect = Math.max(1, bounds.width) / Math.max(1, bounds.height);
    camera.updateProjectionMatrix();
    render();
  };
  controls.addEventListener("change", render);
  window.addEventListener("resize", resize);
  resize();
  return {THREE, points, scene, renderer, camera, controls, cloud, grid, axes, resize, render};
}

function parseScatter(buffer, schema) {
  if (schema.component_type !== "float32" || schema.byte_order !== "little-endian" || schema.stride_bytes !== 12) {
    throw new Error("Scatter binary schema is incompatible.");
  }
  if (buffer.byteLength !== schema.byte_length || buffer.byteLength !== schema.count * schema.stride_bytes) {
    throw new Error("Scatter binary byte length is invalid.");
  }
  const view = new DataView(buffer);
  const x = new Float32Array(schema.count);
  const y = new Float32Array(schema.count);
  const ppfd = new Float32Array(schema.count);
  for (let index = 0; index < schema.count; index += 1) {
    const offset = index * schema.stride_bytes;
    x[index] = view.getFloat32(offset, true);
    y[index] = view.getFloat32(offset + 4, true);
    ppfd[index] = view.getFloat32(offset + 8, true);
    if (![x[index], y[index], ppfd[index]].every(Number.isFinite)) throw new Error("Scatter binary contains non-finite values.");
  }
  return {x, y, ppfd, count: schema.count};
}

async function verifyHash(buffer, expected) {
  if (!/^[0-9a-f]{64}$/.test(expected)) throw new Error("Scatter SHA-256 is malformed.");
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  const actual = [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
  if (actual !== expected) throw new Error("Scatter data failed SHA-256 validation.");
}

function validateMetadata(value, runId) {
  if (value?.schema_id !== "fspm-optics.ppfd-visualization" || value?.schema_version !== 1 || value?.run_id !== runId) throw new Error("Visualization metadata identity is incompatible.");
  if (!/^[0-9a-f]{64}$/.test(value?.field?.identity_sha256) || !Number.isInteger(value?.field?.sample_count) || value.field.sample_count < 1) throw new Error("Visualization field identity is invalid.");
  if (value?.grid?.kind !== "regular" || value?.grid?.interpolation?.applied !== false || value?.grid?.image_origin !== "lower" || value?.grid?.aspect !== "equal") throw new Error("Visualization grid contract is incompatible.");
  const anchors = value?.display?.colormap?.anchors_srgb_8bit;
  if (!Array.isArray(anchors) || anchors.length < 2 || anchors.some((color) => !Array.isArray(color) || color.length !== 3 || color.some((channel) => !Number.isInteger(channel) || channel < 0 || channel > 255))) throw new Error("Visualization colormap is incompatible.");
  const transforms = value?.transforms;
  for (const key of ["physics_recomputed", "symmetrization", "smoothing", "clipping", "correction", "hidden_normalization", "sample_exclusion"]) if (transforms?.[key] !== false) throw new Error("Visualization transform contract is incompatible.");
  if (typeof transforms?.target_rescaling !== "boolean") throw new Error("Visualization target-rescaling contract is incompatible.");
  const target = value?.display?.reference_ppfd_umol_m2_s;
  const referenceKind = value?.display?.reference?.kind;
  const limits = value?.display?.color_limits_ppfd_umol_m2_s;
  const policies = {
    requested_lighting_target: {
      color: "requested_lighting_target_plus_or_minus_200",
      scatter: "requested_target_window_ppfd_to_room_span_v1",
    },
    requested_sampled_ppfd_cap: {
      color: "requested_sampled_ppfd_cap_plus_or_minus_200",
      scatter: "requested_sampled_cap_window_ppfd_to_room_span_v1",
    },
    achieved_final_baseline_mean: {
      color: "achieved_final_baseline_mean_plus_or_minus_200",
      scatter: "achieved_baseline_window_ppfd_to_room_span_v1",
    },
  };
  const policy = policies[referenceKind];
  if (!policy || !Number.isFinite(target) || value?.display?.reference?.center_ppfd_umol_m2_s !== target || !Array.isArray(limits) || limits.length !== 2 || limits[0] !== target - 200 || limits[1] !== target + 200 || value?.display?.color_limit_policy !== policy.color) throw new Error("Visualization display limits are incompatible.");
  if (referenceKind === "requested_lighting_target" && value?.display?.requested_lighting_target_ppfd_umol_m2_s !== target) throw new Error("Requested-target compatibility metadata is invalid.");
  if (referenceKind === "requested_sampled_ppfd_cap" && value?.display?.requested_sampled_ppfd_cap_umol_m2_s !== target) throw new Error("Requested-cap compatibility metadata is invalid.");
  if (referenceKind === "achieved_final_baseline_mean" && "requested_lighting_target_ppfd_umol_m2_s" in value.display) throw new Error("Full-output HPS metadata must not declare a requested lighting target.");
  const vertical = value?.scatter?.vertical_display_transform;
  if (value?.scatter?.count !== value.field.sample_count || value?.scatter?.source_field_identity_sha256 !== value.field.identity_sha256 || value?.scatter?.load_policy !== "main_application_action_opens_viewer_then_fetch_and_sha256_validate") throw new Error("Visualization scatter identity is incompatible.");
  if (vertical?.policy_id !== policy.scatter || vertical?.reference_kind !== referenceKind || vertical?.reference_ppfd_umol_m2_s !== target || vertical?.rendering_only !== true || vertical?.raw_ppfd_authoritative !== true || vertical?.declared_z_range_matches_color_limits !== true || vertical?.color_and_vertical_normalizations_are_separate !== true || vertical?.samples_clipped_corrected_or_removed !== false || vertical?.raw_minimum_ppfd_umol_m2_s !== value.field.observed_minimum_ppfd_umol_m2_s || vertical?.raw_maximum_ppfd_umol_m2_s !== value.field.observed_maximum_ppfd_umol_m2_s) throw new Error("Visualization vertical transform identity is incompatible.");
  for (const key of ["reference_ppfd_umol_m2_s", "declared_vmin_ppfd_umol_m2_s", "declared_vmax_ppfd_umol_m2_s", "horizontal_reference_span_m", "display_vertical_span", "display_lower_height", "display_upper_height", "scale", "offset", "actual_transformed_minimum_height", "actual_transformed_maximum_height", "floor_padding_ratio_to_horizontal_span", "floor_padding_height", "scene_floor_height", "camera_frame_lower_height", "camera_frame_upper_height"]) if (!Number.isFinite(vertical?.[key])) throw new Error("Visualization vertical transform contains non-finite values.");
  if (referenceKind === "requested_lighting_target" && vertical.requested_lighting_target_ppfd_umol_m2_s !== target) throw new Error("Requested-target vertical compatibility metadata is invalid.");
  if (referenceKind === "requested_sampled_ppfd_cap" && vertical.requested_sampled_ppfd_cap_umol_m2_s !== target) throw new Error("Requested-cap vertical compatibility metadata is invalid.");
  if (vertical.declared_vmin_ppfd_umol_m2_s !== limits[0] || vertical.declared_vmax_ppfd_umol_m2_s !== limits[1]) throw new Error("Visualization vertical target window is incompatible.");
  if (!nearlyEqual(vertical.display_vertical_span, vertical.horizontal_reference_span_m * .72) || vertical.display_lower_height !== 0 || !nearlyEqual(vertical.display_upper_height, vertical.display_vertical_span)) throw new Error("Visualization vertical transform bounds are incompatible.");
  if (!nearlyEqual(displayHeight(vertical.declared_vmin_ppfd_umol_m2_s, vertical), vertical.display_lower_height) || !nearlyEqual(displayHeight(target, vertical), vertical.display_vertical_span * .5) || !nearlyEqual(displayHeight(vertical.declared_vmax_ppfd_umol_m2_s, vertical), vertical.display_upper_height) || !nearlyEqual(rawPpfdFromDisplayHeight(vertical.display_upper_height, vertical), vertical.declared_vmax_ppfd_umol_m2_s)) throw new Error("Visualization vertical transform mapping is incompatible.");
  const expectedFloorPadding = vertical.horizontal_reference_span_m * .04;
  const expectedSceneFloor = Math.min(0, vertical.actual_transformed_minimum_height) - expectedFloorPadding;
  if (vertical.scene_floor_policy_id !== "declared_and_observed_union_floor_v1" || !nearlyEqual(vertical.floor_padding_ratio_to_horizontal_span, .04) || !nearlyEqual(vertical.floor_padding_height, expectedFloorPadding) || !nearlyEqual(vertical.scene_floor_height, expectedSceneFloor) || vertical.scene_floor_height >= vertical.actual_transformed_minimum_height || vertical.scene_floor_strictly_below_all_samples !== true || JSON.stringify(vertical.floor_anchored_helpers) !== JSON.stringify(["grid", "axes"])) throw new Error("Visualization scene floor is incompatible.");
  if (vertical.observed_range_normalization !== false || vertical.point_cloud_translation_applied !== false || vertical.low_variation_field_stretch !== false) throw new Error("Visualization sample geometry policy is incompatible.");
  if (vertical.out_of_range?.behavior !== "linear_extrapolation_beyond_declared_display_band" || vertical.out_of_range?.clamped !== false || vertical.out_of_range?.discarded !== false || !nearlyEqual(vertical.actual_transformed_minimum_height, displayHeight(vertical.raw_minimum_ppfd_umol_m2_s, vertical)) || !nearlyEqual(vertical.actual_transformed_maximum_height, displayHeight(vertical.raw_maximum_ppfd_umol_m2_s, vertical)) || !nearlyEqual(vertical.camera_frame_lower_height, vertical.scene_floor_height) || !nearlyEqual(vertical.camera_frame_upper_height, Math.max(vertical.display_upper_height, vertical.actual_transformed_maximum_height)) || JSON.stringify(vertical.camera_frame_union) !== JSON.stringify(["scene_floor", "declared_reference_band", "actual_transformed_samples"])) throw new Error("Visualization vertical framing is incompatible.");
}

function displayHeight(rawPpfd, vertical) {
  return exportedDisplayHeight(rawPpfd, vertical);
}

function rawPpfdFromDisplayHeight(displayHeightValue, vertical) {
  return (displayHeightValue - vertical.offset) / vertical.scale;
}

function nearlyEqual(left, right) {
  return Math.abs(left - right) <= 1e-10 * Math.max(1, Math.abs(left), Math.abs(right));
}

function resetView() {
  if (!current || !initialCamera) return;
  current.camera.position.copy(initialCamera.position);
  current.controls.target.copy(initialCamera.target);
  current.controls.update();
  current.render();
}

function disposeCurrent() {
  if (!current) return;
  setExportEnabled(false);
  window.removeEventListener("resize", current.resize);
  current.controls.dispose();
  current.cloud.geometry.dispose();
  current.cloud.material.dispose();
  current.grid.geometry.dispose();
  current.grid.material.dispose();
  current.axes.geometry.dispose();
  current.axes.material.dispose();
  current.renderer.dispose();
  current.renderer.domElement.remove();
  current = null;
}

async function exportPresentation(kind) {
  if (!metadata || !current) return;
  setExportEnabled(false);
  setExportStatus(kind === "svg" ? "Preparing vector SVG…" : "Preparing matching 4K PNG…");
  try {
    const svg = buildPresentationSvg({
      THREE: current.THREE,
      camera: current.camera,
      target: current.controls.target,
      points: current.points,
      metadata,
      title: exportTitle.value,
    });
    const svgBlob = createSvgBlob(svg);
    if (kind === "svg") {
      await downloadBlob(svgBlob, presentationFilename(metadata.run_id, "svg"));
      setExportStatus("SVG download created.", "success");
    } else {
      const pngBlob = await rasterizeSvgBlobToPng(svgBlob);
      await downloadBlob(pngBlob, presentationFilename(metadata.run_id, "png"));
      setExportStatus("3840 × 2160 PNG download created.", "success");
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    setExportStatus(`Export failed: ${message}`, "error");
  } finally {
    setExportEnabled(current !== null);
  }
}

function setExportEnabled(enabled) {
  exportSvgButton.disabled = !enabled;
  exportPngButton.disabled = !enabled;
}

function setExportStatus(message, state = "") {
  exportStatus.textContent = message;
  if (state) exportStatus.dataset.state = state;
  else delete exportStatus.dataset.state;
}

function artifactContextFromPath() {
  const live = window.location.pathname.match(
    /^\/runs\/([0-9a-f]{32})\/scatter\/index\.html$/,
  );
  if (live) {
    return {runId: live[1], artifactRoot: `/api/runs/${live[1]}/artifacts`};
  }
  const playback = window.location.pathname.match(
    /^\/precomputed\/([0-9a-f]{32})\/scatter\/index\.html$/,
  );
  if (playback) {
    return {
      runId: playback[1],
      artifactRoot: `/api/precomputed/playbacks/${playback[1]}/artifacts`,
    };
  }
  throw new Error("Run-scoped scatter path is invalid.");
}

function unavailable(error) {
  status.textContent = "Scatter unavailable";
  fieldInfo.textContent = error instanceof Error ? error.message : String(error);
  if (!current) {
    emptyState.hidden = false;
    gateMessage.textContent = error instanceof Error ? error.message : String(error);
    retryButton.hidden = false;
  }
}

function format(value) {
  return Number(value).toLocaleString(undefined, {maximumFractionDigits: 2});
}
