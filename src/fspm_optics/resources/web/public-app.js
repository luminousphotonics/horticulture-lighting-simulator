import {createResultView} from "./result-view.js";
import {SYSTEM_DISPLAY_LABELS, systemDisplayLabel} from "./system-labels.js";

const EXPECTED_PLAN_ID =
  "114d6171d46a12284c9c7c3de8fae776851dbd7cb806be55b64aa1053d693416";
const PENDING_PLAYBACK_KEY = "fspm-optics.pending-playback.v1";
const ROOM_BY_VALUE = Object.freeze({
  "10x10": {length: 10, width: 10},
  "15x30": {length: 15, width: 30},
  "30x50": {length: 30, width: 50},
});
const form = required("#run-form");
const systemSelect = required("#system");
const layoutField = required("#layout-mode-field");
const layoutSelect = required("#layout-mode");
const policyField = required("#lighting-target-mode-field");
const policySelect = required("#lighting-target-mode");
const targetField = required("#lighting-target-field");
const targetInput = required("#target-ppfd");
const targetLabel = required("#lighting-target-label");
const targetNote = required("#lighting-target-note");
const toleranceInput = required("#fspm-tolerance");
const roomSelect = required("#room-size");
const aisleInput = required("#aisle-mode");
const runButton = required("#run-button");
const contractSummary = required("#precomputed-contract-summary");
const jobIdentity = required("#job-identity");
const cursorLabel = required("#cursor-label");
const resultView = createResultView();

let capabilities = null;
let availability = null;
let startupError = null;
let playbackPending = false;

required("#event-log-kicker").textContent = "Playback log";
required("#event-log-heading").textContent = "Bundle validation events";
resultView.reset();
resultView.appendLog("Ready for authenticated precomputed playback.");
resultView.setStatus("running", "Checking catalog");
jobIdentity.textContent = "No active playback";
cursorLabel.textContent = "Authenticated catalog";

for (const element of [systemSelect, layoutSelect, policySelect, roomSelect, aisleInput]) {
  element.addEventListener("change", () => synchronizeControls({resetResult: true}));
}
targetInput.addEventListener("input", () => updateAvailabilityState());
toleranceInput.addEventListener("input", () => updateAvailabilityState());
window.addEventListener("pageshow", () => synchronizeControls());
form.addEventListener("submit", submitPlayback);

void initialize();

async function initialize() {
  try {
    const [capabilityPayload, availabilityPayload] = await Promise.all([
      fetchJson("/api/capabilities"),
      fetchJson("/api/precomputed/availability"),
    ]);
    validateCapabilities(capabilityPayload);
    validateAvailability(availabilityPayload);
    capabilities = capabilityPayload.precomputed_playback;
    availability = availabilityPayload;
    synchronizeControls();
    resultView.appendLog("Authenticated catalog validated: 24 of 24 bundles available.");
    const saved = readPendingPlayback();
    if (saved) {
      toleranceInput.value = String(saved.selector.fspm_target_tolerance);
      void runPendingPlayback(saved, {recovering: true});
    } else {
      resultView.setStatus("idle", "Ready");
    }
  } catch (error) {
    startupError = error.message;
    contractSummary.textContent = `Playback catalog failed closed: ${startupError}`;
    resultView.setStatus("failed", "Unavailable");
    resultView.appendLog(startupError);
    setInterfaceDisabled(true);
  }
}

function validateCapabilities(payload) {
  const playback = payload?.precomputed_playback;
  const tolerance = playback?.fspm_target_tolerance;
  if (payload?.application_mode !== "public_precomputed"
      || playback?.enabled !== true
      || payload.live_simulation?.enabled !== false
      || !Array.isArray(payload.live_simulation?.systems)
      || payload.live_simulation.systems.length !== 0
      || !Array.isArray(playback.systems)
      || tolerance?.enabled !== true
      || tolerance.type !== "number"
      || tolerance.default !== 75.0
      || tolerance.finite !== true
      || tolerance.strictly_positive !== true
      || !Array.isArray(tolerance.supported_systems)
      || typeof playback.lighting_target_modes !== "object"
      || typeof playback.fixture_layout_modes !== "object") {
    throw new Error("Server capability response is incompatible with public playback.");
  }
  const expectedSystems = Object.keys(SYSTEM_DISPLAY_LABELS);
  if (playback.systems.length !== expectedSystems.length
      || expectedSystems.some((system) => !playback.systems.includes(system))) {
    throw new Error("Public playback must advertise all three lighting systems.");
  }
  if (tolerance.supported_systems.length !== expectedSystems.length
      || expectedSystems.some(
        (system) => !tolerance.supported_systems.includes(system),
      )
      || !isFiniteStrictlyPositiveNumber(tolerance.default)
      || Number(toleranceInput.defaultValue) !== tolerance.default) {
    throw new Error("Public FSPM tolerance capability is incompatible.");
  }
  const targetModes = new Set(["mean_target", "target_capped"]);
  const layoutModes = new Set(["rolling_bench", "practical"]);
  for (const system of expectedSystems) {
    const targets = playback.lighting_target_modes[system];
    const layouts = playback.fixture_layout_modes[system];
    if (!Array.isArray(targets) || targets.some((mode) => !targetModes.has(mode))
        || !Array.isArray(layouts) || layouts.some((mode) => !layoutModes.has(mode))) {
      throw new Error("A system capability contains an unsupported control value.");
    }
  }
}

function validateAvailability(payload) {
  if (payload?.plan_identity_sha256 !== EXPECTED_PLAN_ID
      || payload.case_count !== 24
      || payload.valid_case_count !== 24
      || payload.missing_case_count !== 0
      || payload.invalid_case_count !== 0
      || !Array.isArray(payload.cases)
      || payload.cases.length !== 24
      || payload.cases.some((item) => item.available !== true)) {
    throw new Error("The fixed authenticated 24-bundle catalog is incomplete or invalid.");
  }
}

function synchronizeControls({resetResult = false} = {}) {
  if (!capabilities || !availability || startupError) return;
  const supportedSystems = capabilities.systems;
  for (const option of systemSelect.options) {
    option.disabled = !supportedSystems.includes(option.value);
  }
  if (!supportedSystems.includes(systemSelect.value)) {
    systemSelect.value = supportedSystems[0] || "";
  }
  if (!systemSelect.value) {
    startupError = "No authenticated playback systems are advertised.";
    setInterfaceDisabled(true);
    return;
  }

  const system = systemSelect.value;
  const targetModes = capabilityValues(capabilities.lighting_target_modes, system);
  const layoutModes = capabilityValues(capabilities.fixture_layout_modes, system);
  synchronizeSelect(policySelect, targetModes);
  synchronizeSelect(layoutSelect, layoutModes, {preserveWhenUnsupported: true});

  const targetSupported = targetModes.length > 0;
  policyField.hidden = !targetSupported;
  policyField.setAttribute("aria-hidden", String(!targetSupported));
  targetField.hidden = !targetSupported;
  targetNote.hidden = !targetSupported;
  targetInput.required = targetSupported;
  targetInput.disabled = !targetSupported;
  policySelect.disabled = !targetSupported;

  const layoutSupported = layoutModes.length > 0;
  layoutField.hidden = !layoutSupported;
  layoutField.setAttribute("aria-hidden", String(!layoutSupported));
  layoutSelect.disabled = !layoutSupported;
  toleranceInput.disabled = !capabilities.fspm_target_tolerance
    .supported_systems.includes(system);

  const capped = policySelect.value === "target_capped";
  targetLabel.textContent = capped ? "Maximum PPFD cap" : "Target mean PPFD";
  targetNote.textContent = capped
    ? "A ceiling over authenticated Stage A sensor-grid samples; the achieved mean may be lower."
    : "The requested authenticated-playback mean PPFD.";
  required("#requested-target-result-label").textContent = capped
    ? "Maximum PPFD cap"
    : "Target mean PPFD";
  required("#target-status-result-label").textContent = capped
    ? "Sampled cap status"
    : "Target feasible";
  required("#system-heading").textContent = systemDisplayLabel(system);

  setInterfaceDisabled(playbackPending);
  if (resetResult) {
    resultView.reset();
    resultView.appendLog("Ready for authenticated precomputed playback.");
    jobIdentity.textContent = "No active playback";
    cursorLabel.textContent = "Authenticated catalog";
  }
  updateAvailabilityState();
}

function synchronizeSelect(
  select,
  allowedValues,
  {preserveWhenUnsupported = false} = {},
) {
  if (preserveWhenUnsupported && allowedValues.length === 0) {
    for (const option of select.options) option.disabled = true;
    return;
  }
  for (const option of select.options) {
    option.disabled = !allowedValues.includes(option.value);
    if (option === select.selectedOptions[0] && option.disabled) option.selected = false;
  }
  if (!allowedValues.includes(select.value) && allowedValues.length > 0) {
    select.value = allowedValues[0];
  }
}

function capabilityValues(mapping, system) {
  const values = mapping?.[system];
  return Array.isArray(values) ? values : [];
}

function selectedCase() {
  const room = ROOM_BY_VALUE[roomSelect.value];
  if (!room) return null;
  const layouts = capabilityValues(capabilities.fixture_layout_modes, systemSelect.value);
  return availability.cases.find((candidate) => (
    candidate.system === systemSelect.value
    && candidate.aisle_mode === aisleInput.checked
    && (layouts.length === 0 || candidate.layout === layoutSelect.value)
    && candidate.supported_room_orders_ft.some((supportedRoom) => (
      supportedRoom.length === room.length && supportedRoom.width === room.width
    ))
  )) || null;
}

function updateAvailabilityState() {
  if (!capabilities || !availability || startupError) return;
  const selected = selectedCase();
  if (!selected) {
    contractSummary.textContent =
      "This system, fixture layout, room size, and aisle combination is outside the authenticated catalog.";
    runButton.disabled = true;
    return;
  }
  const displayRoom = selected.canonical_display_room_ft;
  contractSummary.textContent =
    `Available — validated compact bundle. Canonical display ${displayRoom.length} × `
    + `${displayRoom.width} ft · Standard quality · authenticated fixed settings.`;
  runButton.disabled = playbackPending;
}

async function submitPlayback(event) {
  event.preventDefault();
  if (playbackPending) return;
  const selected = selectedCase();
  if (!selected) {
    updateAvailabilityState();
    return;
  }
  const room = ROOM_BY_VALUE[roomSelect.value];
  const targetModes = capabilityValues(
    capabilities.lighting_target_modes,
    systemSelect.value,
  );
  const layoutModes = capabilityValues(
    capabilities.fixture_layout_modes,
    systemSelect.value,
  );
  const payload = {
    system: systemSelect.value,
    room_length_ft: room.length,
    room_width_ft: room.width,
    aisle_mode: aisleInput.checked,
    fspm_target_tolerance: Number(toleranceInput.value),
    ...(layoutModes.length > 0 ? {layout_mode: layoutSelect.value} : {}),
    ...(targetModes.length > 0 ? {
      target_ppfd: Number(targetInput.value),
      lighting_target_mode: policySelect.value,
    } : {}),
  };

  await runPendingPlayback({
    schema_version: 1,
    idempotency_key: newIdempotencyKey(),
    selector: payload,
    request_id: null,
    status_url: null,
  });
}

async function runPendingPlayback(pending, {recovering = false} = {}) {
  if (playbackPending) return;
  playbackPending = true;
  resultView.reset();
  setInterfaceDisabled(true);
  if (recovering) {
    showProgress("queued", "Resuming your playback request…", pending.request_id);
  } else {
    showProgress("queued", "Queued — your playback will begin automatically.");
  }
  writePendingPlayback(pending);
  try {
    const body = pending.status_url
      ? await pollPlayback(pending)
      : await admitPlayback(pending);
    await renderCompletedPlayback(body);
    clearPendingPlayback();
  } catch (error) {
    clearPendingPlayback();
    resultView.setStatus("failed", "Playback unavailable");
    resultView.appendLog(error.message || "Playback could not be completed.");
    jobIdentity.textContent = "No active playback";
  } finally {
    playbackPending = false;
    setInterfaceDisabled(false);
    updateAvailabilityState();
  }
}

async function admitPlayback(pending) {
  let retryAttempt = 0;
  while (true) {
    let response;
    let body;
    try {
      response = await fetch("/api/precomputed/playbacks", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": pending.idempotency_key,
        },
        body: JSON.stringify(pending.selector),
      });
      body = await response.json();
    } catch (_error) {
      await wait(retryDelay(1000, retryAttempt++));
      continue;
    }
    if (response.status === 429 || response.status === 503) {
      await wait(retryDelay(retryAfterMs(response), retryAttempt++));
      continue;
    }
    if (!response.ok) {
      throw new Error(body?.error?.message || `Request failed (${response.status}).`);
    }
    validateRequestRecord(body);
    pending.request_id = body.request_id;
    pending.status_url = body.status_url;
    writePendingPlayback(pending);
    return await resolveRequestRecord(body, pending);
  }
}

async function pollPlayback(pending, initialRecord = null) {
  let record = initialRecord;
  let pollAttempt = 0;
  let lastState = null;
  while (true) {
    if (record) {
      const resolved = await resolveRequestRecord(record, pending, {
        poll: false,
        display: record.state !== lastState,
      });
      if (resolved) return resolved;
      if (record.state !== lastState) pollAttempt = 0;
      lastState = record.state;
    }
    const baseDelay = Math.max(1000, Number(record?.poll_after_ms) || 1500);
    await wait(retryDelay(baseDelay, pollAttempt++));
    let response;
    try {
      response = await fetch(pending.status_url, {cache: "no-store"});
    } catch (_error) {
      record = null;
      continue;
    }
    if (response.status === 404) {
      pending.request_id = null;
      pending.status_url = null;
      writePendingPlayback(pending);
      return await admitPlayback(pending);
    }
    if (response.status === 429 || response.status === 503) {
      record = null;
      continue;
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body?.error?.message || `Request failed (${response.status}).`);
    }
    validateRequestRecord(body);
    record = body;
  }
}

async function resolveRequestRecord(
  record,
  pending,
  {poll = true, display = true} = {},
) {
  if (record.state === "completed") return record;
  if (record.state === "failed") {
    throw new Error(record.error?.message || "Playback preparation failed.");
  }
  if (record.state === "expired") {
    pending.idempotency_key = newIdempotencyKey();
    pending.request_id = null;
    pending.status_url = null;
    writePendingPlayback(pending);
    return await admitPlayback(pending);
  }
  if (record.state === "queued" && display) {
    showProgress(
      "queued",
      "Queued — your playback will begin automatically.",
      record.request_id,
    );
  } else if (record.state === "running" && display) {
    showProgress("running", "Preparing playback…", record.request_id);
  } else if (!["queued", "running"].includes(record.state)) {
    throw new Error("Playback request returned an unsupported state.");
  }
  return poll ? await pollPlayback(pending, record) : null;
}

async function renderCompletedPlayback(body) {
  if (body?.execution_mode !== "precomputed"
      || body.result?.source_runtime_required !== false) {
    throw new Error("Playback response crossed the public execution boundary.");
  }
  resultView.setActiveRun(body.result.run_id);
  jobIdentity.textContent = `Bundle case ${body.result.case_id}`;
  cursorLabel.textContent = "Validated compact bundle";
  resultView.appendLog("Validated bundle loaded without native execution.");
  resultView.setStatus("succeeded", "Loaded");
  await resultView.loadResults(body.result);
}

function showProgress(state, message, requestId = null) {
  resultView.setStatus("idle", state === "running" ? "Preparing" : "Queued");
  resultView.appendLog(message);
  if (requestId) {
    jobIdentity.textContent = `Playback request ${requestId.slice(0, 8)}`;
  } else {
    jobIdentity.textContent = "Playback request pending";
  }
  cursorLabel.textContent = "Authenticated queue";
}

function validateRequestRecord(body) {
  if (body?.schema_id !== "fspm-optics.precomputed-playback-request"
      || body.schema_version !== 1
      || !/^[0-9a-f]{32}$/.test(body.request_id)
      || !["queued", "running", "completed", "failed", "expired"].includes(body.state)
      || typeof body.status_url !== "string"
      || !body.status_url.startsWith("/api/precomputed/playback-requests/")) {
    throw new Error("Server playback queue response is incompatible.");
  }
}

function readPendingPlayback() {
  try {
    const value = JSON.parse(sessionStorage.getItem(PENDING_PLAYBACK_KEY));
    if (value?.schema_version !== 1
        || typeof value.idempotency_key !== "string"
        || !value.selector || typeof value.selector !== "object"
        || !isFiniteStrictlyPositiveNumber(
          value.selector.fspm_target_tolerance,
        )) return null;
    if (value.request_id !== null && !/^[0-9a-f]{32}$/.test(value.request_id)) return null;
    if (value.status_url !== null
        && (typeof value.status_url !== "string"
          || !value.status_url.startsWith("/api/precomputed/playback-requests/"))) return null;
    return value;
  } catch (_error) {
    return null;
  }
}

function writePendingPlayback(pending) {
  try {
    sessionStorage.setItem(PENDING_PLAYBACK_KEY, JSON.stringify(pending));
  } catch (_error) {
    // Recovery is best-effort when browser storage is unavailable.
  }
}

function clearPendingPlayback() {
  try {
    sessionStorage.removeItem(PENDING_PLAYBACK_KEY);
  } catch (_error) {
    // Browser privacy settings can disable session storage.
  }
}

function newIdempotencyKey() {
  return crypto.randomUUID().replaceAll("-", "");
}

function retryAfterMs(response) {
  const seconds = Number(response.headers.get("Retry-After"));
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 1000;
}

function retryDelay(baseMs, attempt) {
  const exponential = Math.min(8000, baseMs * (1.45 ** Math.min(attempt, 8)));
  return exponential * (0.8 + Math.random() * 0.4);
}

function isFiniteStrictlyPositiveNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function setInterfaceDisabled(disabled) {
  const active = disabled || !capabilities || !availability || startupError;
  systemSelect.disabled = active;
  roomSelect.disabled = active;
  aisleInput.disabled = active;
  if (active) {
    layoutSelect.disabled = true;
    policySelect.disabled = true;
    targetInput.disabled = true;
    toleranceInput.disabled = true;
    runButton.disabled = true;
    return;
  }
  layoutSelect.disabled = layoutField.hidden;
  policySelect.disabled = policyField.hidden;
  targetInput.disabled = targetField.hidden;
  toleranceInput.disabled = !capabilities.fspm_target_tolerance
    .supported_systems.includes(systemSelect.value);
}

async function fetchJson(url, options = {cache: "no-store"}) {
  let attempt = 0;
  while (true) {
    let response;
    let body;
    try {
      response = await fetch(url, options);
      body = await response.json();
    } catch (_error) {
      await wait(retryDelay(1000, attempt++));
      continue;
    }
    if (response.status === 429 || response.status === 503) {
      await wait(retryDelay(retryAfterMs(response), attempt++));
      continue;
    }
    if (!response.ok) {
      throw new Error(body.error?.message || `Request failed (${response.status}).`);
    }
    return body;
  }
}

function required(selector) {
  const element = document.querySelector(selector);
  if (!element) throw new Error(`Public application is missing ${selector}.`);
  return element;
}
