// @ts-check

// @ts-ignore Static vendor module is served by Flask.
import * as THREE from "/static/vendor/three/three.module.js";
import { createCameraRig } from "./camera.js";
import {
  DEFAULT_FIXTURE_HEIGHT_LIMITS,
  createFixtureArrayController,
  formatVisualMountHeightM,
} from "./fixture-controls.js";
import { buildFspmPanelSections, hasFspmPanelData } from "./fspm-panel.js";
import { clampHeatmapOpacity, fetchPhotometricLayer, formatPpfdTooltipValue, lookupPpfdAtUv } from "./heatmap.js";
import { createPerfOverlay } from "./perf.js";
import { createPlantVisibilityController } from "./plants.js";
import { buildAssemblyWorld, createAssemblyScene, createPhotometricHeatmapPlane } from "./renderer.js";
import { loadAssemblyScene, loadFixtureAssets, sceneUrlFromQuery } from "./scene-loader.js";

const root = document.querySelector("[data-viewer-root]");
const canvas = document.getElementById("assembly-canvas");
const statusEl = document.getElementById("assembly-status");
const modeEl = document.getElementById("assembly-mode");
const roomEl = document.getElementById("assembly-room");
const countEl = document.getElementById("assembly-count");
const resetCameraButton = document.getElementById("assembly-reset-camera");
const fspmToggleButton = document.getElementById("assembly-fspm-toggle");
const heatmapToggle = document.getElementById("assembly-heatmap-toggle");
const heatmapOpacityInput = document.getElementById("assembly-heatmap-opacity");
const heatmapStatusEl = document.getElementById("assembly-heatmap-status");
const heatmapTooltipEl = document.getElementById("assembly-heatmap-tooltip");
const fixturesToggle = document.getElementById("assembly-fixtures-toggle");
const fixtureHeightInput = document.getElementById("assembly-fixture-height");
const fixtureHeightValueEl = document.getElementById("assembly-fixture-height-value");
const fixtureHeightResetButton = document.getElementById("assembly-fixture-height-reset");
const plantsControlEl = document.getElementById("assembly-plants-control");
const plantsToggle = document.getElementById("assembly-plants-toggle");
const plantsColorToggle = document.getElementById("assembly-plants-color-toggle");
const plantsStatusEl = document.getElementById("assembly-plants-status");
const perfEl = document.getElementById("assembly-perf");
const fspmPanelEl = document.getElementById("assembly-fspm-panel");
const fspmContentEl = document.getElementById("assembly-fspm-content");
let fspmPanelExpanded = false;
let fspmPanelAvailable = false;
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

function setPlantControlsEnabled(enabled, colorEnabled = false) {
  if (plantsControlEl instanceof HTMLElement) {
    plantsControlEl.hidden = !enabled;
  }
  if (plantsToggle instanceof window.HTMLInputElement) {
    plantsToggle.disabled = !enabled;
  }
  if (plantsColorToggle instanceof window.HTMLInputElement) {
    plantsColorToggle.disabled = !(enabled && colorEnabled);
  }
}

function renderPlantStatus(controller) {
  if (!(plantsStatusEl instanceof HTMLElement)) {
    return;
  }
  if (!controller) {
    plantsStatusEl.textContent = "0 leaves";
    return;
  }
  const state = controller.getState();
  const leafText = `${state.leafCount} ${state.leafCount === 1 ? "leaf" : "leaves"}`;
  const colorText = state.hasAbsorptionColor
    ? (state.absorptionColor ? " · absorption color" : " · geometry color")
    : "";
  plantsStatusEl.textContent = `${leafText}${colorText}`;
}

function wirePlantControls(plantGroup) {
  const hasPlants = Number(plantGroup?.userData?.renderedLeafCount || 0) > 0;
  if (!hasPlants) {
    setPlantControlsEnabled(false);
    renderPlantStatus(null);
    return null;
  }
  const controller = createPlantVisibilityController(plantGroup);
  const state = controller.getState();
  if (plantsToggle instanceof window.HTMLInputElement) {
    plantsToggle.checked = state.visible;
    plantsToggle.addEventListener("change", () => {
      controller.setVisible(plantsToggle.checked);
      renderPlantStatus(controller);
    });
  }
  if (plantsColorToggle instanceof window.HTMLInputElement) {
    plantsColorToggle.checked = Boolean(state.hasAbsorptionColor && state.absorptionColor);
    plantsColorToggle.disabled = !state.hasAbsorptionColor;
    plantsColorToggle.addEventListener("change", () => {
      controller.setAbsorptionColor(plantsColorToggle.checked);
      renderPlantStatus(controller);
    });
  }
  setPlantControlsEnabled(true, state.hasAbsorptionColor);
  renderPlantStatus(controller);
  return controller;
}

function clearElement(el) {
  if (el) {
    el.replaceChildren();
  }
}

function updateFspmPanel() {
  if (fspmPanelEl instanceof HTMLElement) {
    fspmPanelEl.hidden = !(fspmPanelAvailable && fspmPanelExpanded);
  }
  if (fspmToggleButton instanceof HTMLButtonElement) {
    fspmToggleButton.hidden = !fspmPanelAvailable;
    fspmToggleButton.setAttribute("aria-expanded", fspmPanelExpanded ? "true" : "false");
    fspmToggleButton.textContent = `${fspmPanelExpanded ? "Hide" : "Show"} FSPM Panel`;
  }
}

function toggleFspmPanel() {
  if (!fspmPanelAvailable) {
    return;
  }
  fspmPanelExpanded = !fspmPanelExpanded;
  updateFspmPanel();
}

function appendPanelMetric(parent, labelText, valueText) {
  const item = document.createElement("li");
  const label = document.createElement("span");
  const value = document.createElement("strong");
  label.textContent = labelText;
  value.textContent = valueText;
  item.append(label, value);
  parent.append(item);
}

function fallbackWarnings(scene) {
  const fallbacks = Array.isArray(scene?.asset_fallbacks_used) ? scene.asset_fallbacks_used : [];
  return fallbacks.map((fallback) => {
    const assetKey = fallback?.asset_key || "unknown";
    const fallbackAssetKey = fallback?.fallback_asset_key || "unknown";
    return `Using ${fallbackAssetKey} for missing optional ${assetKey}.`;
  });
}

function renderFspmPanel(scene) {
  fspmPanelAvailable = hasFspmPanelData(scene);
  if (!(fspmContentEl instanceof HTMLElement)) {
    updateFspmPanel();
    return;
  }
  clearElement(fspmContentEl);
  if (!fspmPanelAvailable) {
    fspmPanelExpanded = false;
    updateFspmPanel();
    return;
  }
  for (const section of buildFspmPanelSections(scene)) {
    const sectionEl = document.createElement("section");
    sectionEl.className = "assembly-viewer__fspm-section";
    const title = document.createElement("h3");
    title.textContent = section.title;
    sectionEl.append(title);
    if (section.note) {
      const note = document.createElement("p");
      note.className = "assembly-viewer__fspm-note";
      note.textContent = section.note;
      sectionEl.append(note);
    } else {
      const list = document.createElement("ul");
      for (const [label, value] of section.rows || []) {
        appendPanelMetric(list, label, value);
      }
      sectionEl.append(list);
    }
    fspmContentEl.append(sectionEl);
  }
  updateFspmPanel();
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
    renderFspmPanel(scenePayload);

    setState("loading", "Loading fixture assets...");
    const fixtureAssets = await loadFixtureAssets(scenePayload);
    const world = createAssemblyScene(canvas);
    const buildResult = buildAssemblyWorld(world, scenePayload, fixtureAssets.assetBundles);
    wireFixtureControls(buildResult.group, scenePayload);
    wirePlantControls(buildResult.plantGroup);
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

    const cameraRig = createCameraRig(world, scenePayload);
    resetCameraButton?.addEventListener("click", () => cameraRig.reset());
    fspmToggleButton?.addEventListener("click", toggleFspmPanel);
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
