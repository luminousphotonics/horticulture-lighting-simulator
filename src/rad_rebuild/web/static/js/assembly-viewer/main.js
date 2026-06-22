// @ts-check

// @ts-ignore Static vendor module is served by Flask.
import * as THREE from "/static/vendor/three/three.module.js";
import { createCameraRig } from "./camera.js";
import {
  DEFAULT_FIXTURE_HEIGHT_LIMITS,
  createFixtureArrayController,
  formatVisualMountHeightM,
} from "./fixture-controls.js";
import { clampHeatmapOpacity, fetchPhotometricLayer, formatPpfdTooltipValue, lookupPpfdAtUv } from "./heatmap.js";
import { createPerfOverlay } from "./perf.js";
import { buildAssemblyWorld, createAssemblyScene, createPhotometricHeatmapPlane } from "./renderer.js";
import { loadAssemblyScene, loadFixtureAssets, sceneUrlFromQuery } from "./scene-loader.js";

const root = document.querySelector("[data-viewer-root]");
const canvas = document.getElementById("assembly-canvas");
const statusEl = document.getElementById("assembly-status");
const modeEl = document.getElementById("assembly-mode");
const roomEl = document.getElementById("assembly-room");
const countEl = document.getElementById("assembly-count");
const resetCameraButton = document.getElementById("assembly-reset-camera");
const debugToggleButton = document.getElementById("assembly-debug-toggle");
const heatmapToggle = document.getElementById("assembly-heatmap-toggle");
const heatmapOpacityInput = document.getElementById("assembly-heatmap-opacity");
const heatmapStatusEl = document.getElementById("assembly-heatmap-status");
const heatmapTooltipEl = document.getElementById("assembly-heatmap-tooltip");
const fixturesToggle = document.getElementById("assembly-fixtures-toggle");
const fixtureHeightInput = document.getElementById("assembly-fixture-height");
const fixtureHeightValueEl = document.getElementById("assembly-fixture-height-value");
const fixtureHeightResetButton = document.getElementById("assembly-fixture-height-reset");
const perfEl = document.getElementById("assembly-perf");
const devPanelEl = document.getElementById("assembly-dev-panel");
const assetCountsEl = document.getElementById("assembly-asset-counts");
const sceneBoundsEl = document.getElementById("assembly-scene-bounds");
const fitDiagnosticsEl = document.getElementById("assembly-fit-diagnostics");
const warningsEl = document.getElementById("assembly-warnings");
let diagnosticsExpanded = false;
let warningCount = 0;
const heatmapRaycaster = new THREE.Raycaster();
const heatmapPointer = new THREE.Vector2();
const heatmapIntersections = [];
const heatmapState = {
  sceneUrl: "",
  world: null,
  layer: null,
  renderLayer: null,
  loadPromise: null,
};

function setText(el, text) {
  if (el) {
    el.textContent = text;
  }
}

function setState(state, message) {
  if (root instanceof HTMLElement) {
    root.dataset.state = state;
  }
  setText(statusEl, message);
}

function setHeatmapStatus(state, message) {
  if (heatmapStatusEl instanceof HTMLElement) {
    heatmapStatusEl.dataset.state = state;
    heatmapStatusEl.textContent = message;
  }
}

function formatNumber(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "-";
}

function displayModeLabel(scene) {
  const label = String(scene?.display_name || scene?.mode_label || scene?.mode || "-");
  return label === "1000W HPS" ? "1000W HPS System" : label;
}

function renderSummary(scene, instanceCount) {
  const room = scene.room || {};
  setText(modeEl, displayModeLabel(scene));
  setText(
    roomEl,
    `${formatNumber(room.length_m)} m x ${formatNumber(room.width_m)} m, mount ${formatNumber(room.mount_z_m)} m`,
  );
  setText(countEl, `${instanceCount} fixture instances`);
}

function setFixtureControlsEnabled(enabled) {
  for (const control of [fixturesToggle, fixtureHeightInput, fixtureHeightResetButton]) {
    if (control instanceof window.HTMLInputElement || control instanceof window.HTMLButtonElement) {
      control.disabled = !enabled;
    }
  }
}

function renderFixtureHeightValue(controller, baseMountZ) {
  if (!(fixtureHeightValueEl instanceof HTMLElement)) {
    return;
  }
  const state = controller.getState();
  fixtureHeightValueEl.textContent = formatVisualMountHeightM(baseMountZ, state.offsetM);
}

function wireFixtureControls(fixtureGroup, scenePayload) {
  if (!fixtureGroup) {
    setFixtureControlsEnabled(false);
    return null;
  }
  const controller = createFixtureArrayController(fixtureGroup);
  const baseMountZ = scenePayload?.room?.mount_z_m;
  const state = controller.getState();
  if (fixturesToggle instanceof window.HTMLInputElement) {
    fixturesToggle.checked = state.visible;
    fixturesToggle.addEventListener("change", () => {
      controller.setVisible(fixturesToggle.checked);
    });
  }
  if (fixtureHeightInput instanceof window.HTMLInputElement) {
    const limits = state.limits || DEFAULT_FIXTURE_HEIGHT_LIMITS;
    fixtureHeightInput.min = String(limits.minOffsetM);
    fixtureHeightInput.max = String(limits.maxOffsetM);
    fixtureHeightInput.step = String(limits.stepM);
    fixtureHeightInput.value = state.offsetM.toFixed(2);
    fixtureHeightInput.addEventListener("input", () => {
      const nextState = controller.setHeightOffsetM(fixtureHeightInput.value);
      fixtureHeightInput.value = nextState.offsetM.toFixed(2);
      renderFixtureHeightValue(controller, baseMountZ);
    });
  }
  fixtureHeightResetButton?.addEventListener("click", () => {
    const nextState = controller.resetHeightOffset();
    if (fixtureHeightInput instanceof window.HTMLInputElement) {
      fixtureHeightInput.value = nextState.offsetM.toFixed(2);
    }
    renderFixtureHeightValue(controller, baseMountZ);
  });
  renderFixtureHeightValue(controller, baseMountZ);
  setFixtureControlsEnabled(true);
  return controller;
}

function clearElement(el) {
  if (el) {
    el.replaceChildren();
  }
}

function updateDiagnosticsPanel() {
  if (devPanelEl instanceof HTMLElement) {
    devPanelEl.hidden = !diagnosticsExpanded;
  }
  if (debugToggleButton instanceof HTMLButtonElement) {
    debugToggleButton.setAttribute("aria-expanded", diagnosticsExpanded ? "true" : "false");
    const suffix = warningCount ? ` (${warningCount})` : "";
    debugToggleButton.textContent = `${diagnosticsExpanded ? "Hide" : "Show"} Diagnostics${suffix}`;
  }
}

function toggleDiagnostics() {
  diagnosticsExpanded = !diagnosticsExpanded;
  updateDiagnosticsPanel();
}

function renderAssetCounts(scene) {
  if (!(devPanelEl instanceof HTMLElement) || !(assetCountsEl instanceof HTMLElement)) {
    return;
  }
  clearElement(assetCountsEl);
  const counts = scene?.fixture_counts_by_asset_key && typeof scene.fixture_counts_by_asset_key === "object"
    ? scene.fixture_counts_by_asset_key
    : {};
  for (const [assetKey, count] of Object.entries(counts)) {
    const item = document.createElement("li");
    const label = document.createElement("span");
    const value = document.createElement("strong");
    label.textContent = assetKey;
    value.textContent = String(count);
    item.append(label, value);
    assetCountsEl.append(item);
  }
  updateDiagnosticsPanel();
}

function formatBoundsNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "-";
}

function appendDebugMetric(parent, labelText, valueText) {
  const item = document.createElement("li");
  const label = document.createElement("span");
  const value = document.createElement("strong");
  label.textContent = labelText;
  value.textContent = valueText;
  item.append(label, value);
  parent.append(item);
}

function renderSceneBounds(buildResult) {
  if (!(sceneBoundsEl instanceof HTMLElement)) {
    return;
  }
  clearElement(sceneBoundsEl);
  const bounds = buildResult?.assemblyBoundsSummary;
  const shadow = buildResult?.shadowCameraBounds;
  if (bounds?.size) {
    appendDebugMetric(
      sceneBoundsEl,
      "Assembly size",
      `${formatBoundsNumber(bounds.size.x)} x ${formatBoundsNumber(bounds.size.y)} x ${formatBoundsNumber(bounds.size.z)} m`,
    );
    appendDebugMetric(
      sceneBoundsEl,
      "Assembly center",
      `${formatBoundsNumber(bounds.center.x)}, ${formatBoundsNumber(bounds.center.y)}, ${formatBoundsNumber(bounds.center.z)} m`,
    );
  }
  if (shadow) {
    appendDebugMetric(
      sceneBoundsEl,
      "Shadow camera",
      [
        `L ${formatBoundsNumber(shadow.left)}`,
        `R ${formatBoundsNumber(shadow.right)}`,
        `T ${formatBoundsNumber(shadow.top)}`,
        `B ${formatBoundsNumber(shadow.bottom)}`,
        `N ${formatBoundsNumber(shadow.near)}`,
        `F ${formatBoundsNumber(shadow.far)}`,
      ].join(" "),
    );
  }
  appendDebugMetric(sceneBoundsEl, "Fixture culling", "instanced fixture batches disabled");
  updateDiagnosticsPanel();
}

function fallbackWarnings(scene) {
  const fallbacks = Array.isArray(scene?.asset_fallbacks_used) ? scene.asset_fallbacks_used : [];
  return fallbacks.map((fallback) => {
    const assetKey = fallback?.asset_key || "unknown";
    const fallbackAssetKey = fallback?.fallback_asset_key || "unknown";
    return `Using ${fallbackAssetKey} for missing optional ${assetKey}.`;
  });
}

function renderFitDiagnostics(diagnostics) {
  if (!(fitDiagnosticsEl instanceof HTMLElement)) {
    return;
  }
  clearElement(fitDiagnosticsEl);
  const safeDiagnostics = Array.isArray(diagnostics) ? diagnostics : [];
  for (const diagnostic of safeDiagnostics.slice(0, 16)) {
    const item = document.createElement("li");
    const label = document.createElement("span");
    const value = document.createElement("strong");
    const residual = Number(diagnostic?.residual?.max);
    const scale = Number(diagnostic?.scale);
    label.textContent = `${diagnostic?.id || "fixture"} ${diagnostic?.assetKey || ""}`.trim();
    value.textContent = [
      Number.isFinite(residual) ? `${residual.toFixed(3)} m` : "-",
      Number.isFinite(scale) ? `s ${scale.toFixed(2)}` : "",
      diagnostic?.pointCorrespondenceInferred ? "perm" : "",
    ].filter(Boolean).join(" ");
    item.append(label, value);
    fitDiagnosticsEl.append(item);
  }
  if (safeDiagnostics.length > 16) {
    const item = document.createElement("li");
    item.textContent = `${safeDiagnostics.length - 16} more fixture fits`;
    fitDiagnosticsEl.append(item);
  }
  updateDiagnosticsPanel();
}

function renderWarnings(warnings) {
  if (!(warningsEl instanceof HTMLElement)) {
    return;
  }
  clearElement(warningsEl);
  const uniqueWarnings = Array.from(new Set(warnings.filter((warning) => typeof warning === "string" && warning)));
  warningCount = uniqueWarnings.length;
  warningsEl.hidden = uniqueWarnings.length === 0;
  for (const warning of uniqueWarnings.slice(0, 8)) {
    const item = document.createElement("p");
    item.textContent = warning;
    warningsEl.append(item);
  }
  if (uniqueWarnings.length > 8) {
    const item = document.createElement("p");
    item.textContent = `${uniqueWarnings.length - 8} more warnings.`;
    warningsEl.append(item);
  }
  updateDiagnosticsPanel();
}

function wireLodInteraction(controls, lodController) {
  if (!controls || !lodController) {
    return;
  }
  let idleTimer = 0;
  const setInteractive = () => {
    window.clearTimeout(idleTimer);
    lodController.setInteractive(true);
  };
  const setIdleSoon = () => {
    window.clearTimeout(idleTimer);
    idleTimer = window.setTimeout(() => {
      lodController.setInteractive(false);
    }, 180);
  };
  controls.addEventListener("start", setInteractive);
  controls.addEventListener("end", setIdleSoon);
}

function heatmapOpacityValue() {
  const value = heatmapOpacityInput instanceof window.HTMLInputElement ? heatmapOpacityInput.value : 0.72;
  return clampHeatmapOpacity(value);
}

function setHeatmapControlsReady(ready) {
  if (heatmapToggle instanceof window.HTMLInputElement) {
    heatmapToggle.disabled = !ready;
  }
}

function setHeatmapOpacityEnabled(enabled) {
  if (heatmapOpacityInput instanceof window.HTMLInputElement) {
    heatmapOpacityInput.disabled = !enabled;
  }
}

function hideHeatmapTooltip() {
  if (heatmapTooltipEl instanceof HTMLElement) {
    heatmapTooltipEl.hidden = true;
    heatmapTooltipEl.textContent = "";
  }
}

function heatmapInspectable() {
  return Boolean(
    heatmapToggle instanceof window.HTMLInputElement
      && heatmapToggle.checked
      && heatmapState.layer
      && heatmapState.renderLayer?.mesh?.visible,
  );
}

function moveHeatmapTooltip(event, text) {
  if (!(heatmapTooltipEl instanceof HTMLElement) || !(canvas instanceof window.HTMLCanvasElement)) {
    return;
  }
  const rect = canvas.getBoundingClientRect();
  heatmapTooltipEl.textContent = text;
  heatmapTooltipEl.style.transform = `translate(${event.clientX - rect.left + 14}px, ${event.clientY - rect.top + 14}px)`;
  heatmapTooltipEl.hidden = false;
}

function handleHeatmapPointerMove(event) {
  if (!(canvas instanceof window.HTMLCanvasElement) || !heatmapInspectable()) {
    hideHeatmapTooltip();
    return;
  }
  const world = heatmapState.world;
  const mesh = heatmapState.renderLayer?.mesh;
  const layer = heatmapState.layer;
  if (!world?.camera || !mesh || !layer) {
    hideHeatmapTooltip();
    return;
  }

  const rect = canvas.getBoundingClientRect();
  heatmapPointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  heatmapPointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  heatmapIntersections.length = 0;
  heatmapRaycaster.setFromCamera(heatmapPointer, world.camera);
  heatmapRaycaster.intersectObject(mesh, false, heatmapIntersections);
  const uv = heatmapIntersections[0]?.uv;
  if (!uv) {
    hideHeatmapTooltip();
    return;
  }

  const text = formatPpfdTooltipValue(lookupPpfdAtUv(layer, uv));
  if (!text) {
    hideHeatmapTooltip();
    return;
  }
  moveHeatmapTooltip(event, text);
}

async function ensureHeatmapLayer() {
  if (heatmapState.renderLayer) {
    return heatmapState.renderLayer;
  }
  if (heatmapState.loadPromise) {
    return await heatmapState.loadPromise;
  }
  if (!heatmapState.world || !heatmapState.sceneUrl) {
    throw new Error("The assembly scene is not ready.");
  }
  heatmapState.loadPromise = (async () => {
    const layer = await fetchPhotometricLayer(heatmapState.sceneUrl);
    const renderLayer = createPhotometricHeatmapPlane(heatmapState.world, layer, heatmapOpacityValue());
    heatmapState.layer = layer;
    heatmapState.renderLayer = renderLayer;
    return renderLayer;
  })();
  try {
    return await heatmapState.loadPromise;
  } catch (err) {
    heatmapState.loadPromise = null;
    throw err;
  }
}

function applyHeatmapOpacity() {
  const opacity = heatmapOpacityValue();
  if (heatmapOpacityInput instanceof window.HTMLInputElement) {
    heatmapOpacityInput.value = opacity.toFixed(2);
  }
  if (heatmapState.renderLayer?.material) {
    heatmapState.renderLayer.material.opacity = opacity;
    heatmapState.renderLayer.material.needsUpdate = true;
  }
}

async function handleHeatmapToggle() {
  if (!(heatmapToggle instanceof window.HTMLInputElement)) {
    return;
  }
  if (!heatmapToggle.checked) {
    hideHeatmapTooltip();
    if (heatmapState.renderLayer?.mesh) {
      heatmapState.renderLayer.mesh.visible = false;
      setHeatmapStatus("idle", "Off");
    } else {
      setHeatmapStatus("idle", "Idle");
    }
    return;
  }

  heatmapToggle.disabled = true;
  setHeatmapStatus("loading", "Loading");
  try {
    const renderLayer = await ensureHeatmapLayer();
    renderLayer.mesh.visible = heatmapToggle.checked;
    setHeatmapOpacityEnabled(true);
    setHeatmapStatus("ready", heatmapToggle.checked ? "On" : "Off");
  } catch (err) {
    heatmapToggle.checked = false;
    setHeatmapOpacityEnabled(false);
    hideHeatmapTooltip();
    setHeatmapStatus("error", err instanceof Error ? err.message : "Error");
  } finally {
    heatmapToggle.disabled = false;
  }
}

function wireHeatmapControls(sceneUrl, world) {
  heatmapState.sceneUrl = sceneUrl;
  heatmapState.world = world;
  setHeatmapControlsReady(true);
  setHeatmapStatus("idle", "Idle");
  heatmapToggle?.addEventListener("change", () => {
    handleHeatmapToggle();
  });
  heatmapOpacityInput?.addEventListener("input", applyHeatmapOpacity);
  canvas?.addEventListener("pointermove", handleHeatmapPointerMove);
  canvas?.addEventListener("pointerleave", hideHeatmapTooltip);
}

async function boot() {
  if (!(canvas instanceof window.HTMLCanvasElement)) {
    setState("error", "Unable to initialize the assembly canvas.");
    return;
  }

  try {
    setState("loading", "Loading assembly scene...");
    const sceneUrl = sceneUrlFromQuery(window.location.search);
    const scenePayload = await loadAssemblyScene(window.location.search);
    const instanceCount = Array.isArray(scenePayload.instances) ? scenePayload.instances.length : 0;
    renderSummary(scenePayload, instanceCount);
    renderAssetCounts(scenePayload);

    setState("loading", "Loading fixture assets...");
    const fixtureAssets = await loadFixtureAssets(scenePayload);
    const world = createAssemblyScene(canvas);
    const buildResult = buildAssemblyWorld(world, scenePayload, fixtureAssets.assetBundles);
    renderSceneBounds(buildResult);
    wireFixtureControls(buildResult.group, scenePayload);
    wireHeatmapControls(sceneUrl, world);
    window.addEventListener("pagehide", () => {
      hideHeatmapTooltip();
      world.stop();
    });
    const warnings = [
      ...(Array.isArray(scenePayload.warnings) ? scenePayload.warnings : []),
      ...fallbackWarnings(scenePayload),
      ...fixtureAssets.warnings,
      ...buildResult.warnings,
    ];
    renderWarnings(warnings);
    renderFitDiagnostics(buildResult.diagnostics);

    const cameraRig = createCameraRig(world, scenePayload);
    resetCameraButton?.addEventListener("click", () => cameraRig.reset());
    debugToggleButton?.addEventListener("click", toggleDiagnostics);
    window.addEventListener("keydown", (event) => {
      if (event.key.toLowerCase() === "d" && !event.altKey && !event.ctrlKey && !event.metaKey) {
        toggleDiagnostics();
      }
    });
    wireLodInteraction(cameraRig.controls, buildResult.lodController);

    const perf = createPerfOverlay(perfEl, {
      backend: world.rendererBackend,
      instanceCount: world.instanceCount,
    });

    setState(
      "ready",
      warnings.length
        ? "3D assembly loaded with development warnings. Drag to orbit, scroll to zoom, right-drag to pan."
        : "3D assembly loaded. Drag to orbit, scroll to zoom, right-drag to pan.",
    );
    world.start((deltaSeconds) => {
      cameraRig.controls.update(deltaSeconds);
      perf.frame();
    });
  } catch (err) {
    setText(modeEl, "-");
    setText(roomEl, "-");
    setText(countEl, "-");
    setState("error", `Unable to load the assembly scene. ${err instanceof Error ? err.message : String(err)}`);
  }
}

boot();
