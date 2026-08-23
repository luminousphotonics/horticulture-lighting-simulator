import {
  farRedRequestFields,
  MULTISPECTRAL_FSPM_SCOPE,
  synchronizeFarRedAnalysis,
} from "./analysis-scope-ui.js";
import {
  CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS,
  proposedSpectralBasisRequestFields,
  synchronizeProposedSpectralBasis,
} from "./spectral-basis-ui.js";
import {
  layoutModeRequestFields,
  synchronizeLayoutMode,
} from "./layout-mode-ui.js";
import {
  proposedControlModeRequestFields,
  synchronizeProposedControlMode,
} from "./proposed-control-mode-ui.js";
import {
  REDUCED_ONE_RING,
  proposedRingModeRequestFields,
  synchronizeProposedRingMode,
} from "./proposed-ring-mode-ui.js";
import {
  proposedSourceModeRequestFields,
  synchronizeProposedSourceMode,
} from "./proposed-source-mode-ui.js";
import {
  lightingTargetModeRequestFields,
  synchronizeLightingTargetMode,
  TARGET_CAPPED,
} from "./lighting-target-mode-ui.js";
import {capitalize, createResultView} from "./result-view.js";
import {systemDisplayLabel} from "./system-labels.js";

const form = document.querySelector("#run-form");
const runButton = document.querySelector("#run-button");
const runButtonLabel = document.querySelector("#run-button-label");
const executionModeSelect = document.querySelector("#execution-mode");
const precomputedContractNote = document.querySelector("#precomputed-contract-note");
const precomputedContractSummary = document.querySelector("#precomputed-contract-summary");
const systemSelect = document.querySelector("#system");
const layoutModeField = document.querySelector("#layout-mode-field");
const layoutModeSelect = document.querySelector("#layout-mode");
const proposedControlModeField = document.querySelector("#proposed-control-mode-field");
const proposedRingModeField = document.querySelector("#proposed-ring-mode-field");
const proposedRingModeSelect = document.querySelector("#proposed-ring-mode");
const disableBasisMatrixSolverInput = document.querySelector("#disable-basis-matrix-solver");
const proposedSourceModeField = document.querySelector("#proposed-source-mode-field");
const cobModeInput = document.querySelector("#cob-mode");
const analysisScopeSelect = document.querySelector("#analysis-scope");
const analysisScopeDescription = document.querySelector("#analysis-scope-description");
const farRedAnalysisField = document.querySelector("#far-red-analysis-field");
const includeFarRedInput = document.querySelector("#include-far-red");
const spectralBasisField = document.querySelector("#spectral-basis-field");
const spectralBasisSelect = document.querySelector("#spectral-basis");
const targetInput = document.querySelector("#target-ppfd");
const lightingTargetModeField = document.querySelector("#lighting-target-mode-field");
const lightingTargetModeSelect = document.querySelector("#lighting-target-mode");
const lightingTargetLabel = document.querySelector("#lighting-target-label");
const lightingTargetNote = document.querySelector("#lighting-target-note");
const mountingHeightInput = document.querySelector("#mounting-height");
const qualityFieldset = document.querySelector("#quality-fieldset");
const aisleModeInput = document.querySelector("#aisle-mode");
const targetField = document.querySelector("#lighting-target-field");
const systemOperationNote = document.querySelector("#system-operation-note");
const systemHeading = document.querySelector("#system-heading");
const modeSelect = document.querySelector("#fspm-target-mode");
const fspmTarget = document.querySelector("#fspm-target-ppfd");
const cursorLabel = document.querySelector("#cursor-label");
const jobIdentity = document.querySelector("#job-identity");
const eventLogKicker = document.querySelector("#event-log-kicker");
const eventLogHeading = document.querySelector("#event-log-heading");
const resultView = createResultView();
const {appendLog, loadResults, setMetric, setStatus} = resultView;

let activeJob = null;
let activeRun = null;
let cursor = 0;
let ledTargetValue = targetInput.value || "500";
let activeSystemId = systemSelect.value;
let precomputedAvailability = null;
let precomputedAvailabilityError = null;
let applicationCapabilities = null;
let applicationCapabilitiesError = null;

const mountingHeightBySystem = {
  proposed: "18",
  conventional: "18",
  hps: "24",
};

const systemUi = {
  proposed: {
    heading: systemDisplayLabel("proposed"),
    note: `${systemDisplayLabel("proposed")} uses target-controlled global output.`,
  },
  conventional: {
    heading: systemDisplayLabel("conventional"),
    note: `${systemDisplayLabel("conventional")} uses the selected target-independent fixture layout and one capped global source-dimming factor.`,
  },
  hps: {
    heading: systemDisplayLabel("hps"),
    note: `${systemDisplayLabel("hps")} operates only at its declared full output; Lighting Target PPFD is not accepted.`,
  },
};

const analysisScopeUi = {
  baseline_ppfd: {
    label: "Baseline PPFD",
    description: "Plant-free horizontal PPFD, uniformity metrics, CSV, heatmaps, fixture overlays, and 3D scatter.",
  },
  [MULTISPECTRAL_FSPM_SCOPE]: {
    label: "Baseline + Multispectral FSPM",
    description: "Runs the same plant-free baseline first, then performs four-band PAR transport through the populated plant scene and publishes authoritative surface-light aggregation. Far-red is optional.",
  },
};

synchronizeSystemState();
synchronizeFspmTargetState();
synchronizeAnalysisScopeState();
synchronizeExecutionModeState();
void refreshApplicationCapabilities();
if (isPrecomputedMode()) void refreshPrecomputedAvailability();
window.addEventListener("pageshow", () => {
  synchronizeSystemState();
  synchronizeFspmTargetState();
  synchronizeAnalysisScopeState();
  synchronizeExecutionModeState();
});
window.addEventListener("popstate", synchronizeAnalysisScopeState);
analysisScopeSelect.addEventListener("input", synchronizeAnalysisScopeState);
analysisScopeSelect.addEventListener("change", () => {
  synchronizeAnalysisScopeState();
  resetRunUi();
  jobIdentity.textContent = "No active job";
});
if (executionModeSelect) {
  executionModeSelect.addEventListener("change", async () => {
    synchronizeSystemState();
    synchronizeAnalysisScopeState();
    synchronizeExecutionModeState();
    resetRunUi();
    if (isPrecomputedMode()) await refreshPrecomputedAvailability();
  });
}
form.addEventListener("reset", () => queueMicrotask(synchronizeAnalysisScopeState));
systemSelect.addEventListener("change", () => {
  synchronizeSystemState();
  synchronizeAnalysisScopeState();
  synchronizeExecutionModeState();
  updatePrecomputedAvailabilityState();
  resetRunUi();
  jobIdentity.textContent = "No active job";
});
lightingTargetModeSelect.addEventListener("change", () => {
  synchronizeLightingTargetState();
  resetRunUi();
});

modeSelect.addEventListener("change", synchronizeFspmTargetState);
for (const element of [layoutModeSelect, aisleModeInput,
  document.querySelector("#room-length"), document.querySelector("#room-width")]) {
  element.addEventListener("change", updatePrecomputedAvailabilityState);
  element.addEventListener("input", updatePrecomputedAvailabilityState);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  synchronizeAnalysisScopeState();
  const mountingHeightText = mountingHeightInput.value.trim();
  const mountingHeightIn = Number(mountingHeightText);
  if (mountingHeightText === "" || !Number.isFinite(mountingHeightIn) ||
      mountingHeightIn <= 0 || mountingHeightIn > 119.8) {
    mountingHeightInput.setCustomValidity(
      "Mounting height must be a positive finite value no greater than 119.8 inches.",
    );
    mountingHeightInput.reportValidity();
    return;
  }
  mountingHeightInput.setCustomValidity("");
  resetRunUi();
  const quality = form.querySelector('input[name="quality"]:checked').value;
  const payload = {
    system: systemSelect.value,
    room_length_ft: Number(document.querySelector("#room-length").value),
    room_width_ft: Number(document.querySelector("#room-width").value),
    quality,
    analysis_scope: analysisScopeSelect.value,
    fspm_target_mode: "automatic",
    fspm_target_tolerance: Number(document.querySelector("#fspm-tolerance").value),
    mounting_height_in: mountingHeightIn,
    aisle_mode: aisleModeInput.checked,
  };
  if (systemSelect.value !== "hps") {
    payload.target_ppfd = Number(targetInput.value);
  }
  Object.assign(
    payload,
    lightingTargetModeRequestFields(
      systemSelect.value,
      lightingTargetModeSelect.value,
    ),
  );
  Object.assign(
    payload,
    layoutModeRequestFields(systemSelect.value, layoutModeSelect.value),
  );
  Object.assign(
    payload,
    proposedRingModeRequestFields(
      systemSelect.value,
      REDUCED_ONE_RING,
    ),
  );
  Object.assign(
    payload,
    proposedControlModeRequestFields(
      systemSelect.value,
      true,
    ),
  );
  Object.assign(
    payload,
    proposedSourceModeRequestFields(systemSelect.value, false),
  );
  Object.assign(
    payload,
    farRedRequestFields(analysisScopeSelect.value, includeFarRedInput.checked),
  );
  Object.assign(
    payload,
    proposedSpectralBasisRequestFields(
      systemSelect.value,
      analysisScopeSelect.value,
      spectralBasisSelect.value,
    ),
  );
  const precomputedMode = isPrecomputedMode();
  const requestBody = precomputedMode ? {
    system: payload.system,
    room_length_ft: payload.room_length_ft,
    room_width_ft: payload.room_width_ft,
    aisle_mode: payload.aisle_mode,
    ...(payload.system === "conventional" ? {layout_mode: payload.layout_mode} : {}),
    ...(payload.system === "hps" ? {} : {
      target_ppfd: payload.target_ppfd,
      lighting_target_mode: payload.lighting_target_mode,
    }),
  } : payload;
  setStatus("running", precomputedMode ? "Loading" : "Submitting");
  runButton.disabled = true;
  systemSelect.disabled = true;
  analysisScopeSelect.disabled = true;
  try {
    const response = await fetch(
      precomputedMode ? "/api/precomputed/playbacks" : "/api/runs",
      {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(requestBody),
      },
    );
    const body = await response.json();
    if (!response.ok) throw new Error(body.error?.message || "Run request failed.");
    if (precomputedMode) {
      activeJob = null;
      activeRun = body.result.run_id;
      resultView.setActiveRun(activeRun);
      jobIdentity.textContent = `Bundle case ${body.result.case_id}`;
      cursorLabel.textContent = "Validated compact bundle";
      appendLog("Validated bundle loaded without native execution.");
      setStatus("succeeded", "Loaded");
      await loadResults(body.result);
      runButton.disabled = false;
      systemSelect.disabled = false;
      analysisScopeSelect.disabled = true;
      updatePrecomputedAvailabilityState();
      return;
    }
    activeJob = body.job_id;
    activeRun = body.run_id;
    resultView.setActiveRun(activeRun);
    jobIdentity.textContent = `Job ${activeJob.slice(0, 10)} · Run ${activeRun.slice(0, 10)}`;
    await pollJob();
  } catch (error) {
    setStatus("failed", precomputedMode ? "Playback unavailable" : "Request failed");
    appendLog(error.message);
    jobIdentity.textContent = "No active job";
    runButton.disabled = false;
    systemSelect.disabled = false;
    analysisScopeSelect.disabled = false;
    synchronizeExecutionModeState();
    updatePrecomputedAvailabilityState();
  }
});

async function pollJob() {
  if (!activeJob) return;
  try {
    const response = await fetch(`/api/jobs/${activeJob}?cursor=${cursor}`, {cache: "no-store"});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error?.message || "Job polling failed.");
    body.logs.forEach((entry) => appendLog(`[${entry.cursor}] ${entry.line}`));
    cursor = body.next_cursor;
    cursorLabel.textContent = `Cursor ${cursor}`;
    setMetric("status", capitalize(body.state));
    if (body.state === "succeeded") {
      setStatus("succeeded", "Completed");
      await loadResults(body.result);
      runButton.disabled = false;
      systemSelect.disabled = false;
      analysisScopeSelect.disabled = false;
      return;
    }
    if (body.state === "failed") {
      setStatus("failed", "Failed");
      if (body.error?.message) appendLog(`Failure: ${body.error.message}`);
      runButton.disabled = false;
      systemSelect.disabled = false;
      analysisScopeSelect.disabled = false;
      return;
    }
    setStatus("running", capitalize(body.state));
    window.setTimeout(pollJob, 800);
  } catch (error) {
    setStatus("failed", "Polling failed");
    appendLog(error.message);
    runButton.disabled = false;
    systemSelect.disabled = false;
    analysisScopeSelect.disabled = false;
  }
}

function resetRunUi() {
  activeJob = null;
  activeRun = null;
  cursor = 0;
  cursorLabel.textContent = "Cursor 0";
  jobIdentity.textContent = "Allocating run";
  resultView.reset({fspmLabel: pendingFspmLabel()});
}

function synchronizeSystemState() {
  mountingHeightBySystem[activeSystemId] = mountingHeightInput.value.trim();
  const selected = systemUi[systemSelect.value];
  if (!selected) throw new Error("Unsupported lighting system selection.");
  mountingHeightInput.value = mountingHeightBySystem[systemSelect.value];
  mountingHeightInput.setCustomValidity("");
  activeSystemId = systemSelect.value;
  const fixedOutput = systemSelect.value === "hps";
  if (fixedOutput) {
    if (targetInput.value) ledTargetValue = targetInput.value;
    targetInput.value = "";
    targetInput.disabled = true;
    targetInput.required = false;
    targetField.dataset.enabled = "false";
  } else {
    if (targetInput.value) ledTargetValue = targetInput.value;
    targetInput.disabled = false;
    targetInput.required = true;
    targetInput.value = ledTargetValue || "500";
    targetField.dataset.enabled = "true";
  }
  systemHeading.textContent = selected.heading;
  systemOperationNote.textContent = selected.note;
  systemOperationNote.dataset.system = systemSelect.value;
  synchronizeLayoutMode({
    system: systemSelect.value,
    field: layoutModeField,
    select: layoutModeSelect,
  });
  synchronizeProposedControlMode({
    system: systemSelect.value,
    field: proposedControlModeField,
    input: disableBasisMatrixSolverInput,
  });
  synchronizeProposedRingMode({
    system: systemSelect.value,
    field: proposedRingModeField,
    select: proposedRingModeSelect,
  });
  synchronizeProposedSourceMode({
    system: systemSelect.value,
    field: proposedSourceModeField,
    input: cobModeInput,
  });
  synchronizeLightingTargetState();
}

function synchronizeLightingTargetState() {
  synchronizeLightingTargetMode({
    system: systemSelect.value,
    field: lightingTargetModeField,
    select: lightingTargetModeSelect,
    targetLabel: lightingTargetLabel,
    targetNote: lightingTargetNote,
  });
  const capped = systemSelect.value !== "hps"
    && lightingTargetModeSelect.value === TARGET_CAPPED;
  document.querySelector("#requested-target-result-label").textContent = capped
    ? "Maximum PPFD cap"
    : "Target mean PPFD";
  document.querySelector("#target-status-result-label").textContent = capped
    ? "Sampled cap status"
    : "Target feasible";
}

function synchronizeFspmTargetState() {
  modeSelect.value = "automatic";
  modeSelect.disabled = true;
  fspmTarget.value = "";
  fspmTarget.disabled = true;
  fspmTarget.required = false;
}

function synchronizeAnalysisScopeState() {
  const selected = analysisScopeUi[analysisScopeSelect.value];
  if (!selected) throw new Error("Unsupported analysis scope selection.");
  analysisScopeDescription.textContent = selected.description;
  synchronizeFarRedAnalysis({
    scope: analysisScopeSelect.value,
    field: farRedAnalysisField,
    input: includeFarRedInput,
  });
  synchronizeProposedSpectralBasis({
    system: systemSelect.value,
    scope: analysisScopeSelect.value,
    field: spectralBasisField,
    select: spectralBasisSelect,
  });
}

function isPrecomputedMode() {
  return executionModeSelect.value === "precomputed";
}

function synchronizeExecutionModeState() {
  const playback = isPrecomputedMode();
  const advertisedSystems = applicationCapabilities
    ? (playback
      ? applicationCapabilities.precomputed_playback.systems
      : applicationCapabilities.live_simulation.systems)
    : [];
  for (const option of systemSelect.options) {
    option.disabled = !advertisedSystems.includes(option.value);
  }
  if (advertisedSystems.length > 0 && !advertisedSystems.includes(systemSelect.value)) {
    systemSelect.value = advertisedSystems[0];
    synchronizeSystemState();
  }
  precomputedContractNote.hidden = !playback;
  runButtonLabel.textContent = playback
    ? "Load Precomputed Result"
    : "Run analysis";
  eventLogKicker.textContent = playback ? "Playback log" : "Run log";
  eventLogHeading.textContent = playback
    ? "Bundle validation events"
    : "Native execution events";
  targetInput.min = playback ? "0" : "0.000001";
  qualityFieldset.disabled = playback;
  mountingHeightInput.readOnly = playback;
  analysisScopeSelect.disabled = playback;
  modeSelect.disabled = true;
  document.querySelector("#fspm-tolerance").readOnly = playback;
  lightingTargetModeSelect.disabled = systemSelect.value === "hps";

  if (applicationCapabilitiesError || !applicationCapabilities) {
    runButton.disabled = true;
    if (applicationCapabilitiesError) {
      precomputedContractNote.hidden = false;
      precomputedContractSummary.textContent =
        `Application capabilities failed closed: ${applicationCapabilitiesError}`;
    }
    return;
  }

  if (!playback) {
    targetField.hidden = false;
    synchronizeLightingTargetState();
    precomputedAvailabilityError = null;
    runButton.disabled = false;
    return;
  }

  analysisScopeSelect.value = MULTISPECTRAL_FSPM_SCOPE;
  modeSelect.value = "automatic";
  document.querySelector("#fspm-tolerance").value = "75";
  mountingHeightInput.value = mountingHeightBySystem[systemSelect.value];
  form.querySelector('input[name="quality"][value="standard"]').checked = true;
  synchronizeAnalysisScopeState();
  includeFarRedInput.checked = true;
  includeFarRedInput.disabled = true;
  fspmTarget.value = "";
  fspmTarget.disabled = true;
  fspmTarget.required = false;

  const proposed = systemSelect.value === "proposed";
  if (proposed) {
    proposedRingModeSelect.value = "reduced_one_ring";
    proposedRingModeSelect.disabled = true;
    disableBasisMatrixSolverInput.checked = true;
    disableBasisMatrixSolverInput.disabled = true;
    cobModeInput.checked = false;
    cobModeInput.disabled = true;
    spectralBasisSelect.value = CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS;
    spectralBasisSelect.disabled = true;
  }
  const hps = systemSelect.value === "hps";
  targetField.hidden = hps;
  lightingTargetModeField.hidden = hps;
  synchronizeLightingTargetState();
  updatePrecomputedAvailabilityState();
}

async function refreshApplicationCapabilities() {
  applicationCapabilities = null;
  applicationCapabilitiesError = null;
  try {
    const response = await fetch("/api/capabilities", {cache: "no-store"});
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error?.message || "Application capabilities could not be loaded.");
    }
    if (body.application_mode !== "trusted_local_live"
        || body.precomputed_playback?.enabled !== true
        || body.live_simulation?.enabled !== true
        || !Array.isArray(body.precomputed_playback.systems)
        || !Array.isArray(body.live_simulation.systems)) {
      throw new Error("Application capability response is incompatible.");
    }
    applicationCapabilities = body;
  } catch (error) {
    applicationCapabilitiesError = error.message;
  }
  synchronizeExecutionModeState();
}

async function refreshPrecomputedAvailability() {
  precomputedAvailability = null;
  precomputedAvailabilityError = null;
  precomputedContractSummary.textContent = "Checking authenticated bundles…";
  runButton.disabled = true;
  try {
    const response = await fetch("/api/precomputed/availability", {cache: "no-store"});
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error?.message || "Bundle availability could not be loaded.");
    }
    precomputedAvailability = body;
  } catch (error) {
    precomputedAvailabilityError = error.message;
  }
  updatePrecomputedAvailabilityState();
}

function selectedPrecomputedCase() {
  if (!precomputedAvailability?.cases) return null;
  const length = Number(document.querySelector("#room-length").value);
  const width = Number(document.querySelector("#room-width").value);
  const expectedLayout = systemSelect.value === "conventional"
    ? layoutModeSelect.value
    : (systemSelect.value === "proposed"
      ? "proposed_reduced_one_ring"
      : "fixed_full_output");
  return precomputedAvailability.cases.find((candidate) => (
    candidate.system === systemSelect.value
    && candidate.layout === expectedLayout
    && candidate.aisle_mode === aisleModeInput.checked
    && candidate.supported_room_orders_ft.some((room) => (
      room.length === length && room.width === width
    ))
  )) || null;
}

function updatePrecomputedAvailabilityState() {
  if (!isPrecomputedMode()) return;
  if (applicationCapabilitiesError || !applicationCapabilities) {
    runButton.disabled = true;
    return;
  }
  if (precomputedAvailabilityError) {
    precomputedContractSummary.textContent =
      `Playback catalog failed closed: ${precomputedAvailabilityError}`;
    runButton.disabled = true;
    return;
  }
  if (!precomputedAvailability) {
    precomputedContractSummary.textContent = "Checking authenticated bundles…";
    runButton.disabled = true;
    return;
  }
  const selected = selectedPrecomputedCase();
  if (!selected) {
    precomputedContractSummary.textContent =
      "This room, system, layout, and aisle combination is outside the fixed catalog.";
    runButton.disabled = true;
    return;
  }
  const settings = selected.controlled_settings;
  const solver = settings.solver.radiance_options.join(" ");
  const displayRoom = selected.canonical_display_room_ft;
  const availability = selected.available
    ? "Available — validated compact bundle"
    : `Unavailable — ${selected.status}: ${selected.message}`;
  precomputedContractSummary.textContent =
    `${availability}. Canonical display ${displayRoom.length} × `
    + `${displayRoom.width} ft · Standard · ${settings.mounting_height_in} in mounting · `
    + "Baseline + Multispectral FSPM · far-red enabled · automatic FSPM reference · "
    + `authenticated solver ${solver}`;
  runButton.disabled = !selected.available;
}

function pendingFspmLabel() {
  return "Automatic — resolves after baseline";
}
