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
import {
  PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX,
  PLANT_COLOR_MODE_TARGET_RANGE,
  createPlantVisibilityController,
} from "./plants.js";
import { buildAssemblyWorld, createAssemblyScene, createPhotometricHeatmapPlane } from "./renderer.js";
import {
  fspmCsvUrlFromSceneUrl,
  loadAssemblyScene,
  loadFixtureAssets,
  sceneUrlFromQuery,
} from "./scene-loader.js";

const root = document.querySelector("[data-viewer-root]");
const canvas = document.getElementById("assembly-canvas");
const statusEl = document.getElementById("assembly-status");
const titleEl = document.getElementById("assembly-title");
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
const plantsColorModeSelect = document.getElementById("assembly-plants-color-mode");
const plantsDetailControl = document.getElementById("assembly-plants-detail-control");
const plantsDetailModeSelect = document.getElementById("assembly-plants-detail-mode");
const plantsStatusEl = document.getElementById("assembly-plants-status");
const plantsLegendEl = document.getElementById("assembly-plants-legend");
const perfEl = document.getElementById("assembly-perf");
const fspmPanelEl = document.getElementById("assembly-fspm-panel");
const fspmContentEl = document.getElementById("assembly-fspm-content");
const fspmExportButton = document.getElementById("assembly-fspm-export");
const fspmExportStatusEl = document.getElementById("assembly-fspm-export-status");
let fspmPanelExpanded = false;
let fspmPanelAvailable = false;
let fspmCsvUrl = "";
let rawLegendPerspective = "front";
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
  const modeLabel = displayModeLabel(scene);
  setText(titleEl, modeLabel);
  setText(modeEl, modeLabel);
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

function setPlantControlsEnabled(enabled, colorEnabled = false, detailEnabled = false) {
  if (plantsControlEl instanceof HTMLElement) {
    plantsControlEl.hidden = !enabled;
  }
  if (plantsToggle instanceof window.HTMLInputElement) {
    plantsToggle.disabled = !enabled;
  }
  if (plantsColorToggle instanceof window.HTMLInputElement) {
    plantsColorToggle.disabled = !(enabled && colorEnabled);
  }
  if (plantsColorModeSelect instanceof window.HTMLSelectElement) {
    plantsColorModeSelect.disabled = !(enabled && colorEnabled);
  }
  if (plantsDetailControl instanceof HTMLElement) {
    plantsDetailControl.hidden = !(enabled && detailEnabled);
  }
  if (plantsDetailModeSelect instanceof window.HTMLSelectElement) {
    plantsDetailModeSelect.disabled = !(enabled && colorEnabled && detailEnabled);
  }
}

function formatPlantLegendAnchor(anchor, isLast = false) {
  const ratio = Number(anchor?.ratio);
  const percent = Number.isFinite(Number(anchor?.percent))
    ? Number(anchor.percent)
    : ratio * 100;
  const ppfd = Number(anchor?.ppfd_umol_m2_s);
  const suffix = isLast ? "+" : "";
  const percentLabel = Number.isFinite(percent) ? `${Math.round(percent)}%${suffix}` : "-";
  const ppfdLabel = Number.isFinite(ppfd) ? `${Math.round(ppfd)}${suffix}` : "-";
  return `${percentLabel} ${ppfdLabel}`;
}

function appendLegendHeader(parent, title, subtitle) {
  const header = document.createElement("div");
  header.className = "assembly-viewer__plant-legend-header";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const sub = document.createElement("span");
  sub.textContent = subtitle;
  header.append(heading, sub);
  parent.append(header);
}

function renderRawPlantLegend(parent, state) {
  const sideLegends = state.rawLeafSurfaceFluxSideLegends || {};
  const sideScales = state.rawLeafSurfaceFluxSideScales || {};
  const hasBackLegend = Boolean(sideLegends.back || sideScales.back);
  const activeSide = rawLegendPerspective === "back" && hasBackLegend ? "back" : "front";
  rawLegendPerspective = activeSide;
  if (hasBackLegend) {
    const label = document.createElement("label");
    label.className = "assembly-viewer__plant-legend-control";
    const text = document.createElement("span");
    text.textContent = "Legend perspective";
    const select = document.createElement("select");
    for (const [value, optionText] of [
      ["front", "Top/front"],
      ["back", "Bottom/back"],
    ]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = optionText;
      option.selected = activeSide === value;
      select.append(option);
    }
    select.addEventListener("change", () => {
      rawLegendPerspective = select.value === "back" ? "back" : "front";
      renderPlantLegend(state);
    });
    label.append(text, select);
    parent.append(label);
  }
  const legend = sideLegends[activeSide] || state.rawLeafSurfaceFluxLegend || {};
  const scale = sideScales[activeSide] || state.rawLeafSurfaceFluxScale || {};
  const anchors = Array.isArray(legend.anchors) && legend.anchors.length > 0
    ? legend.anchors
    : scale.anchors;
  appendLegendHeader(
    parent,
    legend.title || "Raw leaf-surface incident PPFD",
    `${legend.note || (activeSide === "back"
      ? "Bottom/back scale = underside/reflected-light diagnostic"
      : "Top/front scale = primary exposure comparison")} · ${legend.scale || "% of FSPM target"} · ${legend.units || scale.units || "umol/m²/s"}`,
  );
  const bar = document.createElement("div");
  bar.className = "assembly-viewer__plant-legend-gradient";
  parent.append(bar);
  const ticks = document.createElement("div");
  ticks.className = "assembly-viewer__plant-legend-ticks";
  if (Array.isArray(anchors)) {
    for (const [index, anchor] of anchors.entries()) {
      const tick = document.createElement("span");
      tick.textContent = formatPlantLegendAnchor(anchor, index === anchors.length - 1);
      ticks.append(tick);
    }
  }
  parent.append(ticks);
}

function renderTargetRangePlantLegend(parent) {
  appendLegendHeader(
    parent,
    "Plant-location target coverage",
    "Coverage colors use canopy-reference target fit",
  );
  const chips = document.createElement("div");
  chips.className = "assembly-viewer__plant-legend-chips";
  for (const [label, color] of [
    ["Under-lit", "#3FA66F"],
    ["Target-range", "#4BCF6A"],
    ["Over-lit", "#E26E26"],
  ]) {
    const chip = document.createElement("span");
    const swatch = document.createElement("i");
    swatch.style.background = color;
    chip.append(swatch, document.createTextNode(label));
    chips.append(chip);
  }
  parent.append(chips);
}

function renderPlantLegend(state) {
  if (!(plantsLegendEl instanceof HTMLElement)) {
    return;
  }
  if (!state || !state.absorptionColor) {
    plantsLegendEl.hidden = true;
    clearElement(plantsLegendEl);
    return;
  }
  clearElement(plantsLegendEl);
  if (state.colorMode === PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX) {
    renderRawPlantLegend(plantsLegendEl, state);
  } else if (state.colorMode === PLANT_COLOR_MODE_TARGET_RANGE) {
    renderTargetRangePlantLegend(plantsLegendEl);
  } else {
    plantsLegendEl.hidden = true;
    return;
  }
  plantsLegendEl.hidden = false;
}

function renderPlantStatus(controller) {
  if (!(plantsStatusEl instanceof HTMLElement)) {
    return;
  }
  if (!controller) {
    plantsStatusEl.textContent = "0 leaves";
    renderPlantLegend(null);
    return;
  }
  const state = controller.getState();
  const leafText = `${state.leafCount} ${state.leafCount === 1 ? "leaf" : "leaves"}`;
  const colorText = state.hasAbsorptionColor
    ? (state.absorptionColor
      ? ` · ${state.colorMode === PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX
        ? `raw flux ${state.surfaceDetail ? String(state.surfaceDetailMode).replaceAll("_", " ") : "leaf average"}`
        : "target color"}`
      : " · geometry color")
    : (state.surfaceFluxUnavailableReason ? " · surface flux unavailable" : "");
  plantsStatusEl.textContent = `${leafText}${colorText}`;
  if (state.surfaceFluxUnavailableReason) {
    plantsStatusEl.title = state.surfaceFluxUnavailableReason;
  } else {
    plantsStatusEl.removeAttribute("title");
  }
  renderPlantLegend(state);
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
      const nextState = controller.setAbsorptionColor(plantsColorToggle.checked);
      if (plantsColorModeSelect instanceof window.HTMLSelectElement) {
        plantsColorModeSelect.disabled = !(nextState.hasAbsorptionColor && nextState.absorptionColor);
      }
      if (plantsDetailModeSelect instanceof window.HTMLSelectElement) {
        plantsDetailModeSelect.disabled = !(
          nextState.hasAbsorptionColor
          && nextState.absorptionColor
          && nextState.hasSurfaceDetail
        );
      }
      renderPlantStatus(controller);
    });
  }
  if (plantsColorModeSelect instanceof window.HTMLSelectElement) {
    plantsColorModeSelect.value = state.colorMode;
    plantsColorModeSelect.disabled = !state.hasAbsorptionColor;
    plantsColorModeSelect.addEventListener("change", () => {
      const nextState = controller.setColorMode(plantsColorModeSelect.value);
      if (plantsDetailModeSelect instanceof window.HTMLSelectElement) {
        plantsDetailModeSelect.disabled = !(
          nextState.hasAbsorptionColor
          && nextState.absorptionColor
          && nextState.hasSurfaceDetail
          && nextState.colorMode === PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX
        );
      }
      renderPlantStatus(controller);
    });
  }
  if (plantsDetailModeSelect instanceof window.HTMLSelectElement) {
    plantsDetailModeSelect.value = state.surfaceDetail ? "surface_detail" : "leaf_average";
    plantsDetailModeSelect.disabled = !(
      state.hasAbsorptionColor
      && state.absorptionColor
      && state.hasSurfaceDetail
      && state.colorMode === PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX
    );
    plantsDetailModeSelect.addEventListener("change", () => {
      controller.setSurfaceDetail(plantsDetailModeSelect.value === "surface_detail");
      renderPlantStatus(controller);
    });
  }
  setPlantControlsEnabled(true, state.hasAbsorptionColor, state.hasSurfaceDetail);
  if (plantsDetailModeSelect instanceof window.HTMLSelectElement) {
    plantsDetailModeSelect.disabled = !(
      state.hasAbsorptionColor
      && state.absorptionColor
      && state.hasSurfaceDetail
      && state.colorMode === PLANT_COLOR_MODE_RAW_LEAF_SURFACE_FLUX
    );
  }
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
  syncFspmExportButton();
}

function setFspmExportStatus(state, message) {
  if (!(fspmExportStatusEl instanceof HTMLElement)) {
    return;
  }
  fspmExportStatusEl.dataset.state = state;
  fspmExportStatusEl.textContent = message;
  fspmExportStatusEl.hidden = !message;
}

function syncFspmExportButton() {
  if (!(fspmExportButton instanceof HTMLButtonElement)) {
    return;
  }
  const ready = Boolean(fspmPanelAvailable && fspmCsvUrl);
  fspmExportButton.hidden = !fspmPanelAvailable;
  fspmExportButton.disabled = !ready;
  fspmExportButton.title = ready
    ? "Download compact FSPM metrics CSV for this assembly scene."
    : "FSPM export data is unavailable for this assembly scene.";
}

function filenameFromContentDisposition(header) {
  const match = String(header || "").match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  return match ? decodeURIComponent(match[1]) : "fspm_metrics.csv";
}

async function responseErrorMessage(response) {
  try {
    const payload = await response.json();
    const detail = payload?.detail;
    if (typeof detail === "string" && detail) {
      return detail;
    }
    if (detail?.message) {
      return String(detail.message);
    }
  } catch (_err) {
    // Fall through to the status text.
  }
  return response.statusText || `HTTP ${response.status}`;
}

async function handleFspmExport() {
  if (!(fspmExportButton instanceof HTMLButtonElement) || !fspmCsvUrl) {
    setFspmExportStatus("error", "FSPM export is unavailable.");
    return;
  }
  fspmExportButton.disabled = true;
  setFspmExportStatus("loading", "Preparing CSV...");
  try {
    const response = await fetch(fspmCsvUrl, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(await responseErrorMessage(response));
    }
    const blob = await response.blob();
    const href = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = href;
    link.download = filenameFromContentDisposition(response.headers.get("content-disposition"));
    document.body.append(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(href);
    setFspmExportStatus("ready", "CSV download started.");
  } catch (err) {
    setFspmExportStatus("error", `Export unavailable: ${err instanceof Error ? err.message : String(err)}`);
  } finally {
    syncFspmExportButton();
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
    }
    if (section.rows) {
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
    fspmCsvUrl = fspmCsvUrlFromSceneUrl(sceneUrl);
    const scenePayload = await loadAssemblyScene(window.location.search);
    const instanceCount = Array.isArray(scenePayload.instances) ? scenePayload.instances.length : 0;
    renderSummary(scenePayload, instanceCount);
    renderFspmPanel(scenePayload);
    fspmExportButton?.addEventListener("click", () => {
      handleFspmExport();
    });

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
