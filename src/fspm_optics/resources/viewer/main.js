import * as THREE from "three";
import {systemDisplayLabel} from "./system-labels.js";
import { loadValidatedViewerArtifacts } from "./artifacts.js";
import { createCameraSystem } from "./camera.js";
import {
  combineAuthoritativeBounds,
  loadValidatedFixtureArtifacts,
} from "./fixture-artifacts.js";
import {
  createFixtureDisplay,
  FIXTURE_RENDER_LAYER,
} from "./fixture-renderer.js";
import { createFixtureHeightController } from "./fixture-height-controller.js";
import { createPpfdHeatmapController } from "./ppfd-heatmap.js";
import {
  resolveSurfaceIdentity,
  validateGeometryIdentity,
  validateIdentityMap,
} from "./identity.js";
import {
  applyInstanceTranslations,
  createBoundsDisplay,
  createFootprintDisplay,
  createInspectionEnvironment,
  createInspectionLightRig,
  createPlantSurface,
  createReceiverLayer,
  createReferencePlaneDisplay,
  createRoomDisplay,
  createScientificRenderer,
  disposeObjectTree,
  pickSurfaceIdentity,
  setLeafScientificOverlayVisible,
} from "./renderer.js";
import {
  clearSurfaceFluxColoring,
  createSurfaceFluxColorController,
  loadValidatedSurfaceFluxArtifacts,
} from "./surface-flux.js";
import {
  createTargetCoverageColorController,
  validateTargetCoverageSceneReference,
} from "./target-coverage.js";

const canvas = document.querySelector("#viewport");
const status = document.querySelector("#status");
const inspection = document.querySelector("#inspection");
const fixtureHeightOutput = document.querySelector("#fixture-height-output");
const fixtureHeightState = document.querySelector("#fixture-height-state");
const ppfdHeatmapState = document.querySelector("#ppfd-heatmap-state");
const ppfdHeatmapOpacityOutput = document.querySelector(
  "#ppfd-heatmap-opacity-output",
);
const ppfdHeatmapLegend = document.querySelector("#ppfd-heatmap-legend");
const ppfdHeatmapLegendMinimum = document.querySelector(
  "#ppfd-heatmap-legend-minimum",
);
const ppfdHeatmapLegendMaximum = document.querySelector(
  "#ppfd-heatmap-legend-maximum",
);
const ppfdHeatmapTooltip = document.querySelector("#ppfd-heatmap-tooltip");
const surfaceFluxState = document.querySelector("#surface-flux-state");
const surfaceFluxLegends = document.querySelector("#surface-flux-legends");
const controls = Object.fromEntries(
  [...document.querySelectorAll("[data-control]")]
    .map((element) => [element.dataset.control, element]),
);

let disposeActiveSession = () => {};
window.addEventListener("pagehide", () => disposeActiveSession(), { once: true });

start().catch((error) => {
  disposeActiveSession();
  status.dataset.state = "error";
  status.setAttribute("role", "alert");
  status.setAttribute("aria-busy", "false");
  status.textContent = `Scientific viewer rejected: ${error.message}`;
  canvas.hidden = true;
});

async function start() {
  const renderer = createScientificRenderer(canvas);
  const scene = new THREE.Scene();
  let cameraSystem = null;
  let fixtureDisplay = null;
  let fixtureHeightController = null;
  let ppfdHeatmapController = null;
  let inspectionEnvironment = null;
  let inspectionLightRig = null;
  let receiverLayer = null;
  let receiverNormals = null;
  let resizeObserver = null;
  let frameRequest = null;
  let surfaceFluxController = null;
  let surfaceFluxPayload = null;
  let targetCoverageController = null;
  let targetCoverageContract = null;
  let targetCoverageDiagnostic = "Target Coverage is unavailable.";
  let invalidate = () => {};
  const eventAbort = new AbortController();
  const listen = (target, type, listener) => target.addEventListener(
    type, listener, { signal: eventAbort.signal },
  );
  let disposed = false;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    if (frameRequest !== null) cancelAnimationFrame(frameRequest);
    eventAbort.abort();
    resizeObserver?.disconnect();
    cameraSystem?.controls.removeEventListener("change", invalidate);
    cameraSystem?.controls.dispose();
    inspectionEnvironment?.dispose();
    inspectionLightRig?.dispose();
    fixtureHeightController?.dispose();
    targetCoverageController?.dispose();
    targetCoverageController = null;
    ppfdHeatmapController?.dispose();
    fixtureDisplay?.dispose();
    surfaceFluxController?.dispose();
    surfaceFluxController = null;
    surfaceFluxPayload?.release?.();
    surfaceFluxPayload = null;
    disposeObjectTree(scene);
    renderer.dispose();
  };
  disposeActiveSession = dispose;
  inspectionEnvironment = createInspectionEnvironment(renderer, scene);

  status.textContent = "Validating scene data…";
  const artifacts = await loadValidatedViewerArtifacts("./scene.v1.json");
  if (disposed) return;
  try {
    targetCoverageContract = validateTargetCoverageSceneReference(artifacts.scene);
    targetCoverageDiagnostic = targetCoverageContract.availability === "available"
      ? "Loading the authenticated baseline PPFD texture…"
      : `Target Coverage unavailable: ${targetCoverageContract.unavailable_reason_code}.`;
  } catch (error) {
    targetCoverageContract = null;
    targetCoverageDiagnostic = `Target Coverage unavailable: ${error.message}`;
  }
  const identity = validateIdentityMap(artifacts.identity);
  validateGeometryIdentity(artifacts.scene, artifacts.geometry, identity);
  status.textContent = "Validating fixture and surface artifacts…";
  surfaceFluxState.textContent = "Validating run-scoped Phase 27G-C surface-light artifacts…";
  const surfaceFluxPromise = loadValidatedSurfaceFluxArtifacts("./scene.v1.json", {
    scene: artifacts.scene,
    profile: artifacts.profile,
    identity,
    signal: eventAbort.signal,
  }).then((result) => {
    surfaceFluxPayload = result.payload ?? null;
    return result;
  });
  let fixtureArtifacts;
  let surfaceFluxResult;
  try {
    [fixtureArtifacts, surfaceFluxResult] = await Promise.all([
      loadValidatedFixtureArtifacts(artifacts.scene, "./scene.v1.json"),
      surfaceFluxPromise,
    ]);
  } catch (error) {
    eventAbort.abort();
    await surfaceFluxPromise.catch(() => null);
    surfaceFluxPayload?.release?.();
    surfaceFluxPayload = null;
    throw error;
  }
  if (disposed) {
    surfaceFluxPayload?.release?.();
    surfaceFluxPayload = null;
    return;
  }
  fixtureDisplay = await createFixtureDisplay(
    fixtureArtifacts,
    artifacts.scene.run.information?.proposed_source,
  );
  if (disposed) {
    fixtureDisplay.dispose();
    surfaceFluxPayload?.release?.();
    surfaceFluxPayload = null;
    return;
  }

  const camera = new THREE.PerspectiveCamera(42, 1, 0.001, 30);
  camera.layers.enable(FIXTURE_RENDER_LAYER);
  inspectionLightRig = createInspectionLightRig(camera);
  const combinedBounds = combineAuthoritativeBounds(
    artifacts.scene,
    fixtureDisplay.bounds,
  );
  cameraSystem = createCameraSystem(camera, canvas, combinedBounds);
  const surface = createPlantSurface(
    artifacts.geometry,
    artifacts.scene.natural_fit.plant_count,
  );
  const updateLeafScientificOverlayState = () => {
    setLeafScientificOverlayVisible(
      surface,
      Boolean(receiverLayer?.points.visible || receiverNormals?.visible),
    );
  };
  applyInstanceTranslations(surface, artifacts.translations);
  if (surfaceFluxResult.available) {
    try {
      surfaceFluxController = createSurfaceFluxColorController(
        surface,
        surfaceFluxResult.payload,
        surfaceFluxResult.metadata,
        surfaceFluxResult.validated,
      );
      surfaceFluxController.setEnabled(false);
      surfaceFluxController.setMetric("absorbed_par");
      surfaceFluxController.setSides("both");
    } catch (error) {
      clearSurfaceFluxColoring(surface);
      surfaceFluxController = null;
      surfaceFluxPayload?.release?.();
      surfaceFluxPayload = null;
    }
  }
  scene.add(camera, surface, fixtureDisplay.root, inspectionLightRig.root);

  const roomDisplay = createRoomDisplay(artifacts.scene.reference_plane);
  const helpers = {
    bounds: createBoundsDisplay(artifacts.scene.plant_bounds),
    footprint: createFootprintDisplay(artifacts.scene.projected_footprint_bounds),
    reference: createReferencePlaneDisplay(artifacts.scene.reference_plane),
  };
  helpers.bounds.visible = controls.bounds.checked;
  helpers.footprint.visible = false;
  helpers.reference.visible = false;
  fixtureDisplay.root.visible = controls.fixtures.checked;
  scene.add(roomDisplay, helpers.bounds, helpers.footprint, helpers.reference);

  const room = artifacts.scene.requested_room;
  const fit = artifacts.scene.natural_fit;
  status.dataset.state = "ready";
  status.setAttribute("aria-busy", "false");
  status.textContent = [
    `${systemDisplayLabel(artifacts.scene.run.system_id)} layout`,
    artifacts.scene.run.information?.proposed_control?.label,
    proposedRingModeLabel(
      artifacts.scene.run.information?.proposed_layout?.ring_mode,
    ),
    `${room.length_ft} x ${room.width_ft} ft`,
    `${fit.plant_count} plants`,
    `${fixtureDisplay.fixtureCount} fixtures`,
  ].filter(Boolean).join(" · ");

  let selectedInstanceId = null;
  const render = () => {
    frameRequest = null;
    if (disposed) return;
    const controlsChanged = cameraSystem.controls.update();
    renderer.render(scene, camera);
    if (controlsChanged) invalidate();
  };
  invalidate = () => {
    if (!disposed && frameRequest === null) {
      frameRequest = requestAnimationFrame(render);
    }
  };

  fixtureHeightController = createFixtureHeightController({
    scene: artifacts.scene,
    fixtureBounds: fixtureDisplay.bounds,
    fixtureRoot: fixtureDisplay.root,
    slider: controls.fixtureHeight,
    output: fixtureHeightOutput,
    resetButton: controls.resetFixtureHeight,
    state: fixtureHeightState,
    onDisplayChange: ({ translatedFixtureBounds }) => {
      cameraSystem.setBounds(
        combineAuthoritativeBounds(artifacts.scene, translatedFixtureBounds),
        false,
      );
      invalidate();
    },
  });
  ppfdHeatmapController = createPpfdHeatmapController({
    sceneManifest: artifacts.scene,
    sceneUrl: "./scene.v1.json",
    renderer,
    camera,
    sceneGraph: scene,
    canvas,
    toggle: controls.ppfdHeatmap,
    opacity: controls.ppfdHeatmapOpacity,
    opacityOutput: ppfdHeatmapOpacityOutput,
    status: ppfdHeatmapState,
    retry: controls.retryPpfdHeatmap,
    legend: ppfdHeatmapLegend,
    legendMinimum: ppfdHeatmapLegendMinimum,
    legendMaximum: ppfdHeatmapLegendMaximum,
    tooltip: ppfdHeatmapTooltip,
    invalidate,
    onResourcesAvailable: ({ resources, validated }) => {
      try {
        const authenticatedContract = validateTargetCoverageSceneReference(
          artifacts.scene, validated.metadata,
        );
        if (authenticatedContract.availability !== "available") {
          throw new Error(authenticatedContract.unavailable_reason_code);
        }
        targetCoverageContract = authenticatedContract;
        if (targetCoverageController) {
          targetCoverageController.setScalarResources(resources);
        } else {
          targetCoverageController = createTargetCoverageColorController({
            surface,
            resources,
            contract: targetCoverageContract,
            translations: artifacts.translations,
          });
        }
        targetCoverageDiagnostic = null;
      } catch (error) {
        targetCoverageController?.dispose();
        targetCoverageController = null;
        targetCoverageDiagnostic = `Target Coverage unavailable: ${error.message}`;
      }
      syncLeafColoring();
    },
    onResourcesUnavailable: (error) => {
      targetCoverageController?.dispose();
      targetCoverageController = null;
      targetCoverageDiagnostic = `Target Coverage unavailable: ${error.message}`;
      syncLeafColoring();
    },
  });
  syncLeafColoring();

  listen(controls.surface, "change", () => {
    surface.visible = controls.surface.checked;
    invalidate();
  });
  listen(controls.fixtures, "change", () => {
    fixtureDisplay.root.visible = controls.fixtures.checked;
    invalidate();
  });
  listen(controls.bounds, "change", () => {
    helpers.bounds.visible = controls.bounds.checked;
    invalidate();
  });
  listen(controls.receivers, "change", () => {
    if (selectedInstanceId === null) {
      controls.receivers.checked = false;
      return;
    }
    if (!receiverLayer) {
      receiverLayer = createReceiverLayer(artifacts.receiverBytes);
      scene.add(receiverLayer.points);
    }
    const offset = selectedInstanceId * 3;
    receiverLayer.setTranslation(
      artifacts.translations.values[offset],
      artifacts.translations.values[offset + 1],
      artifacts.translations.values[offset + 2],
    );
    receiverLayer.points.visible = controls.receivers.checked;
    controls.normals.disabled = !controls.receivers.checked;
    if (!controls.receivers.checked) {
      controls.normals.checked = false;
      if (receiverNormals) receiverNormals.visible = false;
    }
    updateLeafScientificOverlayState();
    invalidate();
  });
  listen(controls.normals, "change", () => {
    if (!receiverLayer || !controls.receivers.checked) return;
    receiverNormals ??= receiverLayer.ensureNormals();
    if (!receiverNormals.parent) scene.add(receiverNormals);
    receiverNormals.visible = controls.normals.checked;
    updateLeafScientificOverlayState();
    invalidate();
  });
  listen(controls.surfaceFlux, "change", () => {
    syncLeafColoring();
    invalidate();
  });
  listen(controls.reset, "click", () => {
    cameraSystem.reset();
    invalidate();
  });
  listen(controls.top, "click", () => {
    cameraSystem.top();
    invalidate();
  });
  listen(controls.side, "click", () => {
    cameraSystem.side();
    invalidate();
  });
  cameraSystem.controls.addEventListener("change", invalidate);

  const raycaster = new THREE.Raycaster();
  raycaster.layers.set(0);
  const pointer = new THREE.Vector2();
  let pointerDown = null;
  let suppressSelectionClick = false;
  listen(canvas, "pointerdown", (event) => {
    pointerDown = [event.clientX, event.clientY];
    suppressSelectionClick = false;
  });
  listen(canvas, "pointermove", (event) => {
    if (!pointerDown || event.buttons === 0) return;
    const distance = Math.hypot(
      event.clientX - pointerDown[0],
      event.clientY - pointerDown[1],
    );
    if (distance > 3) suppressSelectionClick = true;
  });
  listen(canvas, "pointercancel", () => {
    pointerDown = null;
    suppressSelectionClick = true;
  });
  listen(canvas, "pointerup", () => {
    pointerDown = null;
  });
  listen(canvas, "click", (event) => {
    if (suppressSelectionClick) {
      suppressSelectionClick = false;
      return;
    }
    if (!surface.visible) {
      return;
    }
    const rectangle = canvas.getBoundingClientRect();
    pointer.set(
      ((event.clientX - rectangle.left) / rectangle.width) * 2 - 1,
      -((event.clientY - rectangle.top) / rectangle.height) * 2 + 1,
    );
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObject(surface, false)[0];
    if (!hit) {
      return;
    }
    const compact = pickSurfaceIdentity(surface, hit);
    const resolved = resolveSurfaceIdentity(
      identity,
      compact,
      artifacts.scene.plant_instances.plant_ids,
    );
    selectedInstanceId = compact.instanceId;
    controls.receivers.disabled = false;
    if (receiverLayer && controls.receivers.checked) {
      const offset = selectedInstanceId * 3;
      receiverLayer.setTranslation(
        artifacts.translations.values[offset],
        artifacts.translations.values[offset + 1],
        artifacts.translations.values[offset + 2],
      );
    }
    inspection.textContent = [
      `Plant: ${resolved.plantId}`,
      `Instance: ${compact.instanceId}`,
      `Leaf: ${resolved.leafId}`,
      `Face: ${resolved.faceId}`,
      `Patch: ${resolved.patchId}`,
      ...rawSurfaceFluxInspection(
        surfaceFluxController?.getRawPatchValues(compact.instanceId, compact.patchIndex),
      ),
    ].join("\n");
    invalidate();
  });

  const resize = () => {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    renderer.setSize(width, height, false);
    camera.aspect = width / Math.max(height, 1);
    camera.updateProjectionMatrix();
    invalidate();
  };
  resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(canvas);
  resize();
  invalidate();

  function syncLeafColoring() {
    const enabled = controls.surfaceFlux.checked;
    targetCoverageController?.setEnabled(false);
    surfaceFluxController?.setEnabled(false);
    surfaceFluxLegends.replaceChildren();
    hideLeafColoringStatus();
    if (enabled && targetCoverageController) {
      targetCoverageController.setEnabled(true);
    }
    renderTargetCoverageContract();
    if (!enabled) {
      targetCoverageController?.setEnabled(false);
    }
    invalidate();
  }

  function renderTargetCoverageContract() {
    if (!targetCoverageContract || !targetCoverageController) {
      showLeafColoringUnavailable(
        "Target Coverage is unavailable because its baseline data could not be validated.",
        targetCoverageDiagnostic,
      );
      return;
    }
    const contract = targetCoverageContract;
    renderTargetCoverageLegend(contract);
  }

  function renderTargetCoverageLegend(contract) {
    const container = createCompactLegend({
      anchors: contract.palette.anchors,
      coordinate: "deviation",
      labels: ["Under target", "Target range", "Over target"],
      accessibleLabel: "Target Coverage from under target through target range to over target",
    });
    const reference = document.createElement("p");
    reference.className = "leaf-color-reference";
    reference.textContent = `Target: ${formatNumber(
      contract.reference.ppfd_umol_m2_s,
    )} ± ${formatNumber(contract.tolerance_ppfd_umol_m2_s)} µmol·m⁻²·s⁻¹`;
    const source = document.createElement("p");
    source.className = "leaf-color-source";
    source.textContent = targetReferenceSourceLabel(contract.reference.source);
    container.append(reference, source);
    surfaceFluxLegends.append(container);
  }

  function hideLeafColoringStatus() {
    surfaceFluxState.hidden = true;
    surfaceFluxState.textContent = "";
    surfaceFluxState.removeAttribute("data-state");
    surfaceFluxState.removeAttribute("title");
  }

  function showLeafColoringUnavailable(message, diagnostic) {
    surfaceFluxState.hidden = false;
    surfaceFluxState.dataset.state = "unavailable";
    surfaceFluxState.textContent = message;
    if (diagnostic) surfaceFluxState.title = diagnostic;
  }
}

function createCompactLegend({ anchors, coordinate, labels, accessibleLabel }) {
  const container = document.createElement("div");
  container.className = "leaf-color-legend";
  const bar = document.createElement("div");
  bar.className = "leaf-color-bar";
  bar.style.backgroundImage = continuousGradient(anchors, coordinate);
  bar.setAttribute("role", "img");
  bar.setAttribute("aria-label", accessibleLabel);
  const scale = document.createElement("div");
  scale.className = "leaf-color-scale";
  for (const label of labels) {
    const item = document.createElement("span");
    item.textContent = label;
    scale.append(item);
  }
  container.append(bar, scale);
  return container;
}

function continuousGradient(anchors, coordinate) {
  const minimum = anchors[0][coordinate];
  const maximum = anchors[anchors.length - 1][coordinate];
  const span = maximum - minimum;
  const stops = anchors.map((anchor) => {
    const percentage = span === 0 ? 0 : ((anchor[coordinate] - minimum) / span) * 100;
    return `${anchor.srgb_hex} ${formatNumber(percentage)}%`;
  });
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}

function targetReferenceSourceLabel(source) {
  return {
    requested_lighting_target: "Requested target",
    achieved_stage_a_baseline_mean: "Achieved Stage A mean",
    authenticated_fspm_override: "FSPM override",
  }[source] ?? "";
}

function formatNumber(value) {
  if (!Number.isFinite(value)) return "—";
  if (value === 0) return "0";
  if (Math.abs(value) < 0.1) return value.toPrecision(3);
  return value.toFixed(2).replace(/\.00$/, "");
}

function proposedRingModeLabel(mode) {
  return {
    full: "Full ring",
    reduced_one_ring: "Reduced by one ring",
  }[mode] ?? "";
}

function rawSurfaceFluxInspection(values) {
  if (!values) return [];
  return [
    `Raw q front incident: ${formatNumber(values.front_incident_par)} µmol·m⁻²·s⁻¹`,
    `Raw q back incident: ${formatNumber(values.back_incident_par)} µmol·m⁻²·s⁻¹`,
    `Raw q front absorbed: ${formatNumber(values.front_absorbed_par)} µmol·m⁻²·s⁻¹`,
    `Raw q back absorbed: ${formatNumber(values.back_absorbed_par)} µmol·m⁻²·s⁻¹`,
  ];
}
