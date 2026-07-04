import { currentRunKey } from "./forms.js";
import {
  appState,
  els,
  precomputedDownloadCommand,
  precomputedSizeText,
} from "./state.js";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");
const PLACEHOLDER_SRC = "/static/img/transparent-placeholder.svg";
const ASSEMBLY_SUPPORTED_MODES = new Set(["SMD", "Competitor", "1000W HPS"]);
const MODAL_FRAME_SANDBOX = "allow-scripts allow-same-origin allow-downloads";
const dialogStack = [];

export function appendOutput(el, text) {
  if (!el) {
    return;
  }
  el.textContent += `${text}\n`;
  el.scrollTop = el.scrollHeight;
}

export function sanitizePublicText(value) {
  if (typeof value !== "string") {
    return value;
  }
  return value
    .replace(/ppfd_visualizations_spydr3/g, "ppfd_visualizations_conventional")
    .replace(/ppfd_visualizations(?!_(?:proposed|conventional|hps|cob))/g, "ppfd_visualizations_proposed")
    .replace(/\bSPYDR 3\b/g, "Conventional LED System")
    .replace(/\bSPYDR3\b/g, "Conventional LED System")
    .replace(/\bSPYDR\b/g, "Conventional")
    .replace(/spydr3/g, "conventional")
    .replace(/spydr/g, "conventional");
}

export function sanitizePublicData(value) {
  if (Array.isArray(value)) {
    return value.map((item) => sanitizePublicData(item));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, entryValue]) => [key, sanitizePublicData(entryValue)]),
    );
  }
  return sanitizePublicText(value);
}

export function setStatusNote(text, tone = "") {
  if (!els.radStatusNote) {
    return;
  }
  els.radStatusNote.textContent = text;
  if (tone) {
    els.radStatusNote.dataset.tone = tone;
  } else {
    delete els.radStatusNote.dataset.tone;
  }
}

export function setRadianceButtonsEnabled(enabled) {
  els.btnRadAll.disabled = !enabled;
  els.btnRadScatter.disabled = !enabled;
  els.btnRadMetrics.disabled = !enabled;
  els.btnRadManifest.disabled = !enabled;
  if (enabled) {
    syncAssemblyButtonState();
  } else if (els.btnRadAssembly) {
    els.btnRadAssembly.disabled = true;
  }
}

export function pulseElectricalEstimateButton() {
  if (!els.btnRadElectricalCost) {
    return;
  }
  els.btnRadElectricalCost.classList.remove("btn--attention");
  void els.btnRadElectricalCost.offsetWidth;
  els.btnRadElectricalCost.classList.add("btn--attention");
  window.setTimeout(() => {
    els.btnRadElectricalCost.classList.remove("btn--attention");
  }, 1000);
}

export function syncElectricalEstimateButtonState() {
  if (!els.btnRadElectricalCost) {
    return;
  }
  let ready = false;
  try {
    ready = Boolean(appState.lastCompletedRunKey) && appState.lastCompletedRunKey === currentRunKey();
  } catch (_err) {
    ready = false;
  }
  els.btnRadElectricalCost.dataset.ready = ready ? "true" : "false";
  els.btnRadElectricalCost.title = ready
    ? "Estimate stage-by-stage electrical cost for this rendered layout."
    : "Run this exact layout first, then estimate electrical cost.";
}

export function syncPpfdCsvButtonState() {
  if (!els.btnRadPpfdCsv) {
    return;
  }
  if (appState.lastCompletedCsvHref) {
    els.btnRadPpfdCsv.href = appState.lastCompletedCsvHref;
    els.btnRadPpfdCsv.setAttribute("download", "ppfd_map.csv");
    els.btnRadPpfdCsv.setAttribute("aria-disabled", "false");
    els.btnRadPpfdCsv.title = "Download the PPFD grid CSV from the latest completed run.";
  } else {
    els.btnRadPpfdCsv.href = "#";
    els.btnRadPpfdCsv.removeAttribute("download");
    els.btnRadPpfdCsv.setAttribute("aria-disabled", "true");
    els.btnRadPpfdCsv.title = "Run a simulation first to download the PPFD CSV.";
  }
}

export function syncAssemblyButtonState() {
  if (!els.btnRadAssembly) {
    return;
  }
  let ready = false;
  let supportedMode = false;
  try {
    supportedMode = ASSEMBLY_SUPPORTED_MODES.has(els.radMode?.value || "");
    ready = Boolean(appState.lastCompletedRunKey)
      && appState.lastCompletedRunKey === currentRunKey()
      && Boolean(appState.currentArtifactToken)
      && supportedMode;
  } catch (_err) {
    ready = false;
  }
  els.btnRadAssembly.disabled = !ready;
  els.btnRadAssembly.dataset.ready = ready ? "true" : "false";
  els.btnRadAssembly.title = supportedMode
    ? ready
      ? "Open the 3D assembly viewer for the latest completed run."
      : "Run this exact lighting layout first to view the 3D assembly."
    : "3D assembly viewing is not available for the selected lighting mode.";
}

export function setRadianceProgress(value, label) {
  appState.progressValue = value;
  if (els.radProgressFill) {
    els.radProgressFill.style.width = `${value}%`;
  }
  if (label && els.radProgressLabel) {
    els.radProgressLabel.textContent = label;
  }
}

export function startRadianceProgress(label) {
  if (!els.radProgress) {
    return;
  }
  if (appState.progressTimer) {
    clearInterval(appState.progressTimer);
    appState.progressTimer = null;
  }
  els.radProgress.classList.remove("hidden");
  if (els.radProgressText) {
    els.radProgressText.textContent = "Simulation is running...";
  }
  setRadianceProgress(5, label || "Starting simulation...");
  appState.progressTimer = setInterval(() => {
    const remaining = 92 - appState.progressValue;
    if (remaining <= 0.2) {
      return;
    }
    const bump = Math.max(0.4, remaining * 0.08);
    setRadianceProgress(Math.min(92, appState.progressValue + bump));
  }, 800);
}

export function finishRadianceProgress(status) {
  if (!els.radProgress) {
    return;
  }
  if (appState.progressTimer) {
    clearInterval(appState.progressTimer);
    appState.progressTimer = null;
  }
  const failed = status === "failed";
  setRadianceProgress(100, failed ? "Failed." : "Complete.");
  if (els.radProgressText) {
    els.radProgressText.textContent = failed ? "Simulation failed." : "Simulation complete.";
  }
}

function focusableElements(dialog) {
  return Array.from(dialog.querySelectorAll(FOCUSABLE_SELECTOR))
    .filter((element) => element instanceof HTMLElement && !element.hasAttribute("hidden") && element.offsetParent !== null);
}

function activateDialog(dialog, opener = document.activeElement) {
  if (!dialog) {
    return;
  }
  const existing = dialogStack.find((entry) => entry.dialog === dialog);
  if (!existing) {
    dialogStack.push({
      dialog,
      opener: opener instanceof HTMLElement ? opener : null,
    });
  }
  dialog.classList.remove("hidden");
  window.setTimeout(() => {
    const focusTarget = focusableElements(dialog)[0] || dialog;
    if (focusTarget instanceof HTMLElement) {
      focusTarget.focus({ preventScroll: true });
    }
  }, 0);
}

function deactivateDialog(dialog) {
  if (!dialog) {
    return;
  }
  dialog.classList.add("hidden");
  const index = dialogStack.findIndex((entry) => entry.dialog === dialog);
  const entry = index >= 0 ? dialogStack.splice(index, 1)[0] : null;
  if (entry?.opener?.isConnected) {
    entry.opener.focus({ preventScroll: true });
  }
}

export function closeTopDialog() {
  const entry = dialogStack.at(-1);
  if (!entry) {
    return false;
  }
  if (entry.dialog === els.metricsModal) {
    closeMetricsGuide();
  } else if (entry.dialog === els.aboutModal) {
    closeAboutModal();
  } else if (entry.dialog === els.electricalModal) {
    closeElectricalModal();
  } else if (entry.dialog === els.missingBundleModal) {
    closeMissingBundleModal();
  } else if (entry.dialog === els.liveRuntimeModal) {
    closeLiveRuntimeModal();
  } else if (entry.dialog === els.modal) {
    closeModal();
  } else {
    deactivateDialog(entry.dialog);
  }
  return true;
}

export function trapDialogFocus(event) {
  if (event.key !== "Tab") {
    return false;
  }
  const entry = dialogStack.at(-1);
  if (!entry) {
    return false;
  }
  const candidates = focusableElements(entry.dialog);
  if (!candidates.length) {
    event.preventDefault();
    entry.dialog.focus({ preventScroll: true });
    return true;
  }
  const first = candidates[0];
  const last = candidates[candidates.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus({ preventScroll: true });
    return true;
  }
  if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus({ preventScroll: true });
    return true;
  }
  return false;
}

/**
 * @param {{title?: string, imageSrc?: string, frameSrc?: string, text?: string, fullscreen?: boolean}} options
 */
export function openModal({ title, imageSrc, frameSrc, text, fullscreen }) {
  els.modalTitle.textContent = title || "Visualization";
  els.modal.classList.toggle("radiance-modal--fullscreen", Boolean(fullscreen));
  if (imageSrc) {
    els.modalImage.src = imageSrc;
    els.modalImage.classList.remove("hidden");
  } else {
    els.modalImage.src = PLACEHOLDER_SRC;
    els.modalImage.classList.add("hidden");
  }
  if (frameSrc) {
    els.modalFrame.setAttribute("sandbox", MODAL_FRAME_SANDBOX);
    els.modalFrame.src = frameSrc;
    els.modalFrame.title = title || "Visualization";
    els.modalFrame.classList.remove("hidden");
  } else {
    els.modalFrame.src = "";
    els.modalFrame.classList.add("hidden");
  }
  if (text) {
    els.modalText.textContent = text;
    els.modalText.classList.remove("hidden");
  } else {
    els.modalText.textContent = "";
    els.modalText.classList.add("hidden");
  }
  activateDialog(els.modal);
}

export function closeModal() {
  deactivateDialog(els.modal);
  els.modal.classList.remove("radiance-modal--fullscreen");
  els.modalImage.src = PLACEHOLDER_SRC;
  els.modalFrame.src = "";
  els.modalText.textContent = "";
}

export function openMetricsGuide() {
  if (!els.metricsModal) {
    return;
  }
  activateDialog(els.metricsModal);
}

export function closeMetricsGuide() {
  if (!els.metricsModal) {
    return;
  }
  deactivateDialog(els.metricsModal);
}

export function openAboutModal() {
  if (!els.aboutModal) {
    return;
  }
  activateDialog(els.aboutModal);
}

export function closeAboutModal() {
  if (!els.aboutModal) {
    return;
  }
  deactivateDialog(els.aboutModal);
}

export function openMissingBundleModal(detail = {}) {
  if (!els.missingBundleModal) {
    return;
  }
  const dims = detail?.dimensions || {};
  const modeLabel = detail?.mode_label || detail?.mode || "selected system";
  const slug = dims.slug || `${dims.length_ft || "-"}x${dims.width_ft || "-"}`;
  if (els.missingBundleTitle) {
    els.missingBundleTitle.textContent = detail.title || "Precomputed bundle not installed";
  }
  if (els.missingBundleMessage) {
    els.missingBundleMessage.textContent = detail.message || "The selected precomputed bundle is not installed in this checkout.";
  }
  if (els.missingBundleRequest) {
    els.missingBundleRequest.textContent = `${modeLabel} at ${slug} ft`;
  }
  if (els.missingBundleSize) {
    els.missingBundleSize.textContent = detail.estimated_size || precomputedSizeText;
  }
  if (els.missingBundleCommand) {
    els.missingBundleCommand.textContent = detail.download_command || precomputedDownloadCommand;
  }
  activateDialog(els.missingBundleModal);
}

export function closeMissingBundleModal() {
  if (!els.missingBundleModal) {
    return;
  }
  deactivateDialog(els.missingBundleModal);
}

export async function copyMissingBundleCommand() {
  const command = els.missingBundleCommand?.textContent || precomputedDownloadCommand;
  try {
    await navigator.clipboard.writeText(command);
    setStatusNote("Download command copied.", "success");
  } catch (_err) {
    setStatusNote("Select the command in the dialog and copy it manually.", "error");
  }
}

function setHidden(el, hidden) {
  if (el) {
    el.hidden = hidden;
  }
}

function replaceChildren(el) {
  if (el) {
    el.replaceChildren();
  }
}

function addFact(label, value) {
  if (!els.liveRuntimeFacts) {
    return;
  }
  const wrapper = document.createElement("div");
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = label;
  dd.textContent = value || "-";
  wrapper.append(dt, dd);
  els.liveRuntimeFacts.appendChild(wrapper);
}

function addDiagnostic(label, value) {
  if (!els.liveRuntimeDiagnostics || !value) {
    return;
  }
  const row = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = `${label}: `;
  row.append(strong, document.createTextNode(value));
  els.liveRuntimeDiagnostics.appendChild(row);
}

function executionLabel(executionMode) {
  if (executionMode === "live_local") {
    return "Live Local Radiance";
  }
  if (executionMode === "live_docker") {
    return "Live Docker";
  }
  return "Live Radiance";
}

function reasonLabel(reason) {
  if (!reason) {
    return "Runtime check did not pass";
  }
  return String(reason).replaceAll("_", " ");
}

function setupCommandsFor(runtime) {
  return Array.isArray(runtime?.setup_commands) ? runtime.setup_commands : [];
}

export function renderRuntimeCommands(runtime) {
  replaceChildren(els.liveRuntimeCommands);
  const commands = setupCommandsFor(runtime);
  if (!els.liveRuntimeCommands || commands.length === 0) {
    return;
  }
  commands.forEach((entry) => {
    const row = document.createElement("div");
    row.className = "radiance-command-row";
    const code = document.createElement("code");
    code.textContent = entry.command || "";
    const button = document.createElement("button");
    button.className = "btn btn--secondary";
    button.type = "button";
    button.textContent = "Copy";
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(code.textContent || "");
        setStatusNote(`${entry.label || "Setup"} command copied.`, "success");
      } catch (_err) {
        setStatusNote("Select the command in the dialog and copy it manually.", "error");
      }
    });
    row.append(code, button);
    if (entry.label) {
      const label = document.createElement("strong");
      label.textContent = entry.label;
      const wrap = document.createElement("div");
      wrap.className = "radiance-runtime__commands";
      wrap.append(label, row);
      els.liveRuntimeCommands.appendChild(wrap);
    } else {
      els.liveRuntimeCommands.appendChild(row);
    }
  });
}

export function openUnsupportedLiveModeModal({ executionMode, status }) {
  if (!els.liveRuntimeModal) {
    return;
  }
  const systemLabel = els.radMode?.selectedOptions?.[0]?.textContent || "Selected system";
  els.liveRuntimeEyebrow.textContent = "Public Live Mode";
  els.liveRuntimeTitle.textContent = "Use Precomputed for this system";
  els.liveRuntimeMessage.textContent = status?.live_unsupported_mode_message
    || "Live Radiance mode is currently available only for the Proposed LED System in the public GitHub release. Conventional LED and 1000W HPS are available through precomputed mode only.";
  replaceChildren(els.liveRuntimeFacts);
  addFact("Selected", systemLabel);
  addFact("Requested", executionLabel(executionMode));
  addFact("Available", "Precomputed mode, 3D assembly, and PPFD heatmap");
  replaceChildren(els.liveRuntimeDiagnostics);
  addDiagnostic(
    "Why",
    "Conventional LED and 1000W HPS rely on IES photometry assets that are not included in the public repository.",
  );
  replaceChildren(els.liveRuntimeCommands);
  setHidden(els.liveRuntimeNote, true);
  setHidden(els.liveRuntimeRecheck, true);
  activateDialog(els.liveRuntimeModal);
}

export function openRuntimeSetupModal({ executionMode, runtime }) {
  if (!els.liveRuntimeModal) {
    return;
  }
  els.liveRuntimeEyebrow.textContent = "Runtime Setup";
  els.liveRuntimeTitle.textContent = `${executionLabel(executionMode)} is not ready yet`;
  els.liveRuntimeMessage.textContent = `${executionLabel(executionMode)} can run the Proposed LED System once the runtime check passes.`;
  replaceChildren(els.liveRuntimeFacts);
  addFact("Selected", "Proposed LED System");
  addFact("Runtime", executionLabel(executionMode));
  addFact("Status", reasonLabel(runtime?.reason));
  replaceChildren(els.liveRuntimeDiagnostics);
  if (executionMode === "live_local") {
    const missing = Array.isArray(runtime?.missing_executables) ? runtime.missing_executables.join(", ") : "";
    addDiagnostic("Missing executables", missing || "None reported");
    addDiagnostic("Radiance home", runtime?.radiance_home || "Not detected");
    addDiagnostic("Radiance bin", runtime?.radiance_bin_dir || "Not detected");
    addDiagnostic("Radiance lib", runtime?.radiance_lib_dir || "Not detected");
  } else {
    addDiagnostic("Docker CLI", runtime?.docker_cli || "Not detected");
    addDiagnostic("Daemon", runtime?.daemon_available ? "Reachable" : "Not reachable");
    addDiagnostic("Image", runtime?.image_available ? "Available" : "Will be built when Docker is ready");
  }
  renderRuntimeCommands(runtime);
  setHidden(els.liveRuntimeNote, false);
  setHidden(els.liveRuntimeRecheck, false);
  activateDialog(els.liveRuntimeModal);
}

export function closeLiveRuntimeModal() {
  if (!els.liveRuntimeModal) {
    return;
  }
  deactivateDialog(els.liveRuntimeModal);
}

function formatMetricLine(label, value, unit = "") {
  if (value === undefined || value === null || Number.isNaN(value)) {
    return null;
  }
  const suffix = unit ? ` ${unit}` : "";
  return `${label}: ${value}${suffix}`;
}

function formatPercent(value, digits = 1) {
  if (value === undefined || value === null || Number.isNaN(value)) {
    return null;
  }
  return `${(value * 100).toFixed(digits)}%`;
}


function formatNumber(value, digits = 2) {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return null;
  }
  return num.toFixed(digits);
}

function formatPlantPhotonAbsorption(metrics, used) {
  const scaffold = metrics.plant_incident_surface_flux || metrics.plant_photon_absorption;
  if (!scaffold || typeof scaffold !== "object") {
    return [];
  }
  if (metrics.plant_incident_surface_flux) {
    used.add("plant_incident_surface_flux");
  }
  used.add("plant_photon_absorption");

  const hasAbsorbedFlux = Number.isFinite(Number(scaffold.total_absorbed_photon_flux_umol_s));
  const lines = [
    hasAbsorbedFlux ? "INCIDENT LEAF-SURFACE FLUX" : "PLANT SURFACE REGISTRY",
  ];
  const status = String(scaffold.status || "scaffold_only").replaceAll("_", " ");
  const source = scaffold.source_artifact ? ` · ${scaffold.source_artifact}` : "";
  lines.push(`status: ${status}${source}`);

  const targetPpfd = formatNumber(scaffold.target_ppfd_umol_m2_s, 0);
  const targetTolerance = formatNumber(scaffold.target_tolerance_umol_m2_s, 0);
  if (targetPpfd && targetTolerance) {
    lines.push(`target_ppfd: ${targetPpfd} umol/m2/s +/- ${targetTolerance}`);
  }
  const targetLowerValue =
    scaffold.target_lower_threshold_umol_m2_s ??
    (Number.isFinite(Number(scaffold.target_ppfd_umol_m2_s)) &&
    Number.isFinite(Number(scaffold.target_tolerance_umol_m2_s))
      ? Number(scaffold.target_ppfd_umol_m2_s) -
        Number(scaffold.target_tolerance_umol_m2_s)
      : undefined);
  const targetUpperValue =
    scaffold.target_upper_threshold_umol_m2_s ??
    (Number.isFinite(Number(scaffold.target_ppfd_umol_m2_s)) &&
    Number.isFinite(Number(scaffold.target_tolerance_umol_m2_s))
      ? Number(scaffold.target_ppfd_umol_m2_s) +
        Number(scaffold.target_tolerance_umol_m2_s)
      : undefined);
  const targetLower = formatNumber(targetLowerValue, 0);
  const targetUpper = formatNumber(targetUpperValue, 0);
  if (targetLower && targetUpper) {
    lines.push(`target_range: ${targetLower}-${targetUpper} umol/m2/s`);
  }
  const targetBasis =
    scaffold.target_classification_basis_label ||
    scaffold.target_classification_basis ||
    scaffold.target_basis_label ||
    scaffold.target_basis;
  if (targetBasis) {
    lines.push(`target_classification_basis: ${String(targetBasis).replaceAll("_", " ")}`);
  }
  if (scaffold.target_classification_source) {
    lines.push(
      `target_classification_source: ${String(scaffold.target_classification_source).replaceAll("_", " ")}`,
    );
  }

  const counts = [];
  const plantCount = Number(scaffold.plant_count);
  const leafCount = Number(scaffold.leaf_count);
  const surfaceCount = Number(scaffold.surface_count);
  if (Number.isFinite(plantCount)) counts.push(`${plantCount} plants`);
  if (Number.isFinite(leafCount)) counts.push(`${leafCount} leaves`);
  if (Number.isFinite(surfaceCount)) counts.push(`${surfaceCount} surfaces`);
  if (counts.length) {
    lines.push(`registry: ${counts.join(" · ")}`);
  }

  const leafArea = formatNumber(scaffold.one_sided_leaf_area_m2, 4);
  if (leafArea) {
    lines.push(`one_sided_leaf_area: ${leafArea} m^2`);
  }

  const optical = scaffold.optical_assumptions;
  if (optical && typeof optical === "object") {
    const reflectance = formatPercent(optical.reflectance, 1);
    const transmittance = formatPercent(optical.transmittance, 1);
    const absorptance = formatPercent(optical.absorptance, 1);
    const opticalParts = [];
    if (reflectance) opticalParts.push(`reflectance=${reflectance}`);
    if (transmittance) opticalParts.push(`transmittance=${transmittance}`);
    if (absorptance) opticalParts.push(`absorptance=${absorptance}`);
    if (opticalParts.length) {
      lines.push(`optical_assumptions: ${opticalParts.join(" · ")}`);
    }
  }

  if (hasAbsorbedFlux) {
    const targetClassificationMean = formatNumber(
      scaffold.target_classification_mean_ppfd_umol_m2_s,
      1,
    );
    const targetCapped = formatNumber(
      scaffold.target_capped_incident_flux_total_umol_s ?? scaffold.target_capped_flux_total_umol_s,
      3,
    );
    const excess = formatNumber(
      scaffold.excess_incident_flux_above_target_umol_s ?? scaffold.excess_flux_above_target_umol_s,
      3,
    );
    const deficit = formatNumber(
      scaffold.deficit_to_target_incident_flux_umol_s ?? scaffold.under_target_deficit_umol_s,
      3,
    );
    const targetCv = formatPercent(
      scaffold.plant_to_plant_target_capped_incident_flux_cv ??
        scaffold.plant_to_plant_target_capped_flux_cv,
      1,
    );
    const rawMean = formatNumber(scaffold.raw_mean_flux_density_umol_m2_s, 1);
    const cappedMean = formatNumber(
      scaffold.target_capped_incident_mean_flux_density_umol_m2_s ??
        scaffold.target_capped_mean_flux_density_umol_m2_s,
      1,
    );
    const incident = formatNumber(scaffold.total_incident_photon_flux_umol_s, 3);
    lines.push("classification_counts: leaf aggregates");
    lines.push(`target_range_leaves: ${Number(scaffold.target_range_leaf_count ?? scaffold.target_range_leaves ?? 0)}`);
    lines.push(`under_lit_leaves: ${Number(scaffold.under_lit_leaf_count ?? scaffold.under_lit_leaves ?? 0)}`);
    lines.push(`over_lit_leaves: ${Number(scaffold.over_lit_leaf_count ?? scaffold.over_lit_leaves ?? 0)}`);
    if (targetClassificationMean) {
      lines.push(`target_classification_mean_ppfd: ${targetClassificationMean} umol/m2/s`);
    }
    if (targetCapped) lines.push(`target_capped_incident_flux_total: ${targetCapped} umol/s`);
    if (excess) lines.push(`excess_incident_flux_above_target: ${excess} umol/s`);
    if (deficit) lines.push(`deficit_to_target_incident_flux: ${deficit} umol/s`);
    if (targetCv) lines.push(`plant_to_plant_target_capped_incident_CV: ${targetCv}`);
    if (cappedMean) lines.push(`target_capped_incident_mean_density: ${cappedMean} umol/m2/s`);
    if (rawMean) lines.push(`raw_mean_flux_density: ${rawMean} umol/m2/s`);
    if (incident) lines.push(`raw_incident_flux_total: ${incident} umol/s`);
  } else {
    lines.push("incident_leaf_surface_flux: not computed");
  }
  if (scaffold.note) {
    lines.push(`note: ${scaffold.note}`);
  } else {
    lines.push("note: Surface registry only. Incident leaf-surface flux requires a reviewed Radiance surface-flux mapping method.");
  }

  return lines;
}

function formatPlantSpectralAbsorption(metrics, used) {
  const spectral = metrics.plant_spectral_absorption;
  if (!spectral || typeof spectral !== "object") {
    return [];
  }
  used.add("plant_spectral_absorption");

  const lines = ["MODELED SPECTRAL LEAF ABSORPTION"];
  const status = String(spectral.status || "computed").replaceAll("_", " ");
  const source = spectral.source_artifact ? ` · ${spectral.source_artifact}` : "";
  lines.push(`status: ${status}${source}`);
  if (spectral.optical_profile_id) {
    lines.push(`optical_profile: ${spectral.optical_profile_id}`);
  }
  if (spectral.source_spectral_basis) {
    lines.push(`source_spectrum_basis: ${String(spectral.source_spectral_basis).replaceAll("_", " ")}`);
  }
  if (spectral.scalar_flux_basis) {
    lines.push(`scalar_flux_basis: ${String(spectral.scalar_flux_basis).replaceAll("_", " ")}`);
  }

  const incidentPar = formatNumber(spectral.scalar_incident_par_ppfd_umol_m2_s, 1);
  const targetCappedPar = formatNumber(
    spectral.target_capped_absorbed_par_ppfd ??
      spectral.target_capped_absorbed_par_ppfd_umol_m2_s,
    1,
  );
  const targetCappedEpar = formatNumber(
    spectral.target_capped_absorbed_epar_ppfd ??
      spectral.target_capped_absorbed_epar_ppfd_umol_m2_s,
    1,
  );
  const targetCappedParFraction = formatPercent(
    spectral.target_capped_absorbed_par_fraction_of_raw,
    1,
  );
  const targetCappedEparFraction = formatPercent(
    spectral.target_capped_absorbed_epar_fraction_of_raw,
    1,
  );
  const targetEffectiveFraction = formatPercent(spectral.target_effective_absorbed_fraction, 1);
  const overTargetFraction = formatPercent(
    spectral.over_target_absorbed_par_fraction_of_raw,
    1,
  );
  const underTargetLeafFraction = formatPercent(spectral.under_target_leaf_fraction, 1);
  const inTargetLeafFraction = formatPercent(spectral.in_target_leaf_fraction, 1);
  const overTargetLeafFraction = formatPercent(spectral.over_target_leaf_fraction, 1);
  const absorbedPar = formatNumber(spectral.absorbed_par_ppfd_umol_m2_s, 1);
  const absorbedEpar = formatNumber(spectral.absorbed_epar_ppfd_umol_m2_s, 1);
  if (incidentPar) lines.push(`incident_PAR_PPFD: ${incidentPar} umol/m2/s`);
  if (targetCappedPar) {
    lines.push(`target_capped_modeled_absorbed_PAR_PPFD: ${targetCappedPar} umol/m2/s`);
  }
  if (targetCappedEpar) {
    lines.push(`target_capped_modeled_absorbed_ePAR_PPFD: ${targetCappedEpar} umol/m2/s`);
  }
  if (targetCappedParFraction) {
    lines.push(`target_capped_absorbed_PAR_fraction_of_raw: ${targetCappedParFraction}`);
  }
  if (targetCappedEparFraction) {
    lines.push(`target_capped_absorbed_ePAR_fraction_of_raw: ${targetCappedEparFraction}`);
  }
  if (targetEffectiveFraction) {
    lines.push(`target_effective_absorbed_fraction: ${targetEffectiveFraction}`);
  }
  if (overTargetFraction) {
    lines.push(`over_target_absorbed_PAR_fraction_of_raw: ${overTargetFraction}`);
  }
  const targetFractions = [];
  if (underTargetLeafFraction) targetFractions.push(`under=${underTargetLeafFraction}`);
  if (inTargetLeafFraction) targetFractions.push(`in=${inTargetLeafFraction}`);
  if (overTargetLeafFraction) targetFractions.push(`over=${overTargetLeafFraction}`);
  if (targetFractions.length) {
    lines.push(`target_leaf_fractions: ${targetFractions.join(" · ")}`);
  }
  if (absorbedPar) lines.push(`raw_modeled_absorbed_PAR_PPFD: ${absorbedPar} umol/m2/s`);
  if (absorbedEpar) lines.push(`raw_modeled_absorbed_ePAR_PPFD: ${absorbedEpar} umol/m2/s`);

  const bandValues = [
    ["blue", spectral.absorbed_blue_ppfd_umol_m2_s],
    ["green", spectral.absorbed_green_ppfd_umol_m2_s],
    ["orange", spectral.absorbed_orange_ppfd_umol_m2_s],
    ["red", spectral.absorbed_red_ppfd_umol_m2_s],
    ["far_red", spectral.absorbed_far_red_ppfd_umol_m2_s],
  ]
    .map(([band, value]) => {
      const formatted = formatNumber(value, 1);
      return formatted ? `${band}=${formatted}` : null;
    })
    .filter(Boolean);
  if (bandValues.length) {
    lines.push(`modeled_absorbed_band_PPFD: ${bandValues.join(" · ")} umol/m2/s`);
  }

  const absorbedFraction = formatPercent(spectral.absorbed_fraction, 1);
  const reflectedFraction = formatPercent(spectral.reflected_fraction, 1);
  const transmittedFraction = formatPercent(spectral.transmitted_fraction, 1);
  const fractions = [];
  if (absorbedFraction) fractions.push(`absorbed=${absorbedFraction}`);
  if (reflectedFraction) fractions.push(`reflected=${reflectedFraction}`);
  if (transmittedFraction) fractions.push(`transmitted=${transmittedFraction}`);
  if (fractions.length) {
    lines.push(`modeled_flux_fractions: ${fractions.join(" · ")}`);
  }
  if (spectral.note) {
    lines.push(`note: ${spectral.note}`);
  }
  return lines;
}


function formatCurrency(value, digits = 0) {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return "N/A";
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(num);
}

export function resetCostEstimate(message = "Run a simulation to load the estimate.") {
  if (els.radCostCard) {
    els.radCostCard.classList.add("radiance-cost-card--empty");
  }
  if (els.radCostTitle) {
    els.radCostTitle.textContent = "Rendered Hardware Estimate";
  }
  if (els.radCostTotal) {
    els.radCostTotal.textContent = "—";
  }
  if (els.radCostSubtitle) {
    els.radCostSubtitle.textContent = message;
  }
  if (els.radCostSummary) {
    els.radCostSummary.textContent = "Pricing appears here after a simulation completes.";
  }
  if (els.radCostBreakdown) {
    els.radCostBreakdown.replaceChildren();
  }
}

export function renderCostEstimate(cost) {
  if (!cost || typeof cost !== "object") {
    resetCostEstimate("Cost estimate unavailable.");
    return;
  }
  if (els.radCostCard) {
    els.radCostCard.classList.remove("radiance-cost-card--empty");
  }
  if (els.radCostTitle) {
    els.radCostTitle.textContent = `${cost.system_label || "Rendered Hardware"} Cost Estimate`;
  }
  if (els.radCostTotal) {
    els.radCostTotal.textContent = formatCurrency(cost.total_cost_usd);
  }
  if (els.radCostSubtitle) {
    const bits = [];
    const fixtureCount = Number(cost.fixture_count || 0);
    if (fixtureCount > 0) {
      bits.push(`${fixtureCount} fixtures`);
    }
    const areaSqft = Number(cost.area_sqft || 0);
    if (areaSqft > 0) {
      bits.push(`${areaSqft.toFixed(0)} sq ft`);
    }
    const costPerSqft = Number(cost.cost_per_sqft_usd);
    if (Number.isFinite(costPerSqft) && costPerSqft > 0) {
      bits.push(`${formatCurrency(costPerSqft, 2)} / sq ft`);
    }
    els.radCostSubtitle.textContent = bits.join(" · ") || "Rendered system estimate";
  }
  if (els.radCostSummary) {
    els.radCostSummary.textContent = cost.summary || "Estimated hardware pricing for the rendered layout.";
  }
  if (els.radCostBreakdown) {
    els.radCostBreakdown.replaceChildren();
    const items = Array.isArray(cost.line_items) ? cost.line_items : [];
    items.forEach((item) => {
      const card = document.createElement("article");
      card.className = "radiance-cost-line";
      const label = document.createElement("strong");
      label.textContent = item.label || "Line Item";
      const meta = document.createElement("span");
      meta.textContent = item.detail || "";
      const value = document.createElement("em");
      if (item.value_text) {
        value.textContent = item.value_text;
      } else if (item.amount_usd !== undefined && item.amount_usd !== null) {
        value.textContent = formatCurrency(item.amount_usd);
      } else if (item.subtotal_usd !== undefined && item.subtotal_usd !== null) {
        value.textContent = formatCurrency(item.subtotal_usd);
      } else {
        value.textContent = "";
      }
      card.append(label, meta, value);
      els.radCostBreakdown.appendChild(card);
    });
  }
}

export function resetElectricalEstimate() {
  if (els.elecTotalCost) {
    els.elecTotalCost.textContent = "—";
  }
  if (els.elecTotalMeta) {
    els.elecTotalMeta.textContent = "Run the estimate to calculate cycle kWh and cost.";
  }
  if (els.elecSummary) {
    els.elecSummary.textContent = "Commercial cultivation defaults are prefilled so you can get a quick estimate immediately, including a realistic default utility rate of $0.128 / kWh.";
  }
  if (els.elecStageResults) {
    els.elecStageResults.replaceChildren();
  }
  if (els.elecNotes) {
    els.elecNotes.replaceChildren();
  }
}

export function openElectricalModal() {
  if (!els.electricalModal) {
    return;
  }
  let ready = false;
  try {
    ready = Boolean(appState.lastCompletedRunKey) && appState.lastCompletedRunKey === currentRunKey();
  } catch (_err) {
    ready = false;
  }
  if (!ready) {
    pulseElectricalEstimateButton();
    setStatusNote("Run this exact layout successfully first, then estimate electrical cost.", "error");
    return;
  }
  activateDialog(els.electricalModal);
}

export function closeElectricalModal() {
  if (!els.electricalModal) {
    return;
  }
  deactivateDialog(els.electricalModal);
}

export function renderElectricalEstimate(data) {
  if (!data || typeof data !== "object") {
    resetElectricalEstimate();
    return;
  }
  if (els.elecTotalCost) {
    els.elecTotalCost.textContent = formatCurrency(data.cycle_cost_usd);
  }
  if (els.elecTotalMeta) {
    const totalKwh = Number(data.cycle_kwh || 0);
    const rate = Number(data.utility_rate_kwh || 0);
    els.elecTotalMeta.textContent = `${totalKwh.toFixed(0)} kWh total · ${formatCurrency(rate, 3)} / kWh`;
  }
  if (els.elecSummary) {
    els.elecSummary.textContent = "Stage costs are derived from the simulator’s stage-specific wattage, not nominal fixture PPE, so the estimate respects dimming behavior and fixed-output HPS assumptions.";
  }
  if (els.elecStageResults) {
    els.elecStageResults.replaceChildren();
    const stages = Array.isArray(data.stages) ? data.stages : [];
    stages.forEach((stage) => {
      const card = document.createElement("article");
      card.className = "radiance-cost-line";
      const label = document.createElement("strong");
      label.textContent = stage.name || "Stage";
      const meta = document.createElement("span");
      const avgPpfd = Number(stage.avg_ppfd || 0);
      const simulatedMean = Number(stage.simulated_mean_ppfd || 0);
      const watts = Number(stage.stage_watts || 0);
      const hours = Number(stage.stage_hours || 0);
      const kwh = Number(stage.stage_kwh || 0);
      const eff = Number(stage.usable_efficacy_umol_j);
      const effText = Number.isFinite(eff) && eff > 0 ? ` · DEUC ${eff.toFixed(3)} umol/J` : "";
      meta.textContent = `${avgPpfd.toFixed(0)} PPFD target · ${simulatedMean.toFixed(0)} simulated mean · ${watts.toFixed(0)} W · ${hours.toFixed(0)} h · ${kwh.toFixed(1)} kWh${effText}`;
      const value = document.createElement("em");
      value.textContent = formatCurrency(stage.stage_cost_usd);
      card.append(label, meta, value);
      els.elecStageResults.appendChild(card);
    });
  }
  if (els.elecNotes) {
    els.elecNotes.replaceChildren();
    const notes = Array.isArray(data.notes) ? data.notes : [];
    notes.forEach((note) => {
      const p = document.createElement("p");
      p.textContent = note;
      els.elecNotes.appendChild(p);
    });
  }
}

export function formatMetrics(metrics) {
  if (!metrics || typeof metrics !== "object") {
    return "Metrics unavailable.";
  }
  const lines = [];
  const used = new Set();

  const push = (line) => {
    if (line) {
      lines.push(line);
    }
  };

  lines.push("STATS");
  push(formatMetricLine("mean", metrics.mean?.toFixed?.(2) ?? metrics.mean, "umol/m^2/s"));
  push(formatMetricLine("min", metrics.min?.toFixed?.(2) ?? metrics.min, "umol/m^2/s"));
  push(formatMetricLine("max", metrics.max?.toFixed?.(2) ?? metrics.max, "umol/m^2/s"));
  push(formatMetricLine("p05", metrics.p05?.toFixed?.(2) ?? metrics.p05, "umol/m^2/s"));
  push(formatMetricLine("p50", metrics.p50?.toFixed?.(2) ?? metrics.p50, "umol/m^2/s"));
  push(formatMetricLine("p95", metrics.p95?.toFixed?.(2) ?? metrics.p95, "umol/m^2/s"));
  ["mean", "min", "max", "p05", "p50", "p95"].forEach((key) => used.add(key));

  if (metrics.mode_note) {
    lines.push("");
    lines.push("NOTE");
    push(String(metrics.mode_note));
    used.add("mode_note");
  }

  if ("ppf_out" in metrics || "setpoint_ppfd" in metrics) {
    lines.push("");
    lines.push("SUMMARY");
    if ("ppf_out" in metrics) {
      push(formatMetricLine("ppf_out", metrics.ppf_out?.toFixed?.(1) ?? metrics.ppf_out, "umol/s"));
      used.add("ppf_out");
    }
    if ("setpoint_ppfd" in metrics) {
      const cap = metrics.setpoint_ppfd;
      const capScale = metrics.cap_scale;
      const meanAtCap = metrics.mean_at_cap;
      const util = metrics.utilization_at_cap;
      const ppfAtCap = metrics.ppf_at_cap;
      const deucCap = metrics.capped_deuc_elec ?? metrics.deuc_elec ?? metrics.deuc;
      const parts = [];
      if (cap !== undefined) parts.push(`cap=${Number(cap).toFixed(0)}`);
      if (capScale !== undefined) parts.push(`cap_scale=${Number(capScale).toFixed(3)}`);
      if (meanAtCap !== undefined) parts.push(`mean@cap=${Number(meanAtCap).toFixed(2)}`);
      if (util !== undefined) parts.push(`util@cap=${formatPercent(util, 1)}`);
      if (ppfAtCap !== undefined) parts.push(`ppf@cap=${Number(ppfAtCap).toFixed(1)} umol/s`);
      if (deucCap !== undefined) parts.push(`DEUC_elec(cap)=${Number(deucCap).toFixed(3)} umol/J`);
      push(parts.length ? parts.join(" ") : null);
      [
        "setpoint_ppfd",
        "cap_scale",
        "mean_at_cap",
        "utilization_at_cap",
        "ppf_at_cap",
        "capped_deuc_elec",
        "full_run_deuc_elec",
        "deuc_elec",
        "deuc",
      ].forEach((key) => used.add(key));
    }
  }

  lines.push("");
  lines.push("RATIOS");
  push(formatMetricLine("peak/mean", metrics.peak_over_mean?.toFixed?.(3) ?? metrics.peak_over_mean));
  push(formatMetricLine("min/mean", metrics.min_over_mean?.toFixed?.(3) ?? metrics.min_over_mean));
  push(formatMetricLine("min/max", metrics.min_over_max?.toFixed?.(3) ?? metrics.min_over_max));
  push(formatMetricLine("mean/peak", metrics.mean_over_peak?.toFixed?.(3) ?? metrics.mean_over_peak));
  ["peak_over_mean", "min_over_mean", "min_over_max", "mean_over_peak"].forEach((key) => used.add(key));

  if ("ppf_out" in metrics || "ppf_emitted" in metrics || "capture_frac" in metrics || "plane_utilization" in metrics) {
    lines.push("");
    lines.push("PHOTONS");
    push(formatMetricLine("ppf_out", metrics.ppf_out?.toFixed?.(1) ?? metrics.ppf_out, "umol/s"));
    push(formatMetricLine("ppf_emitted", metrics.ppf_emitted?.toFixed?.(1) ?? metrics.ppf_emitted, "umol/s"));
    const capture = formatPercent(metrics.capture_frac, 1);
    push(capture ? `capture_frac: ${capture}` : null);
    const planeUtilization = formatPercent(metrics.plane_utilization ?? metrics.capture_frac, 1);
    push(planeUtilization ? `plane_utilization: ${planeUtilization}` : null);
    ["ppf_out", "ppf_emitted", "capture_frac", "plane_utilization"].forEach((key) => used.add(key));
  }

  if (
    "watts_in" in metrics ||
    "watts_at_cap" in metrics ||
    "full_run_deuc_elec" in metrics ||
    "capped_deuc_elec" in metrics ||
    "deuc_elec" in metrics ||
    "deuc" in metrics
  ) {
    lines.push("");
    lines.push("ELECTRICAL");
    push(formatMetricLine("watts_in", metrics.watts_in?.toFixed?.(1) ?? metrics.watts_in, "W"));
    push(formatMetricLine("watts_at_cap", metrics.watts_at_cap?.toFixed?.(1) ?? metrics.watts_at_cap, "W"));
    push(
      formatMetricLine(
        "full_run_deuc_elec",
        metrics.full_run_deuc_elec?.toFixed?.(3) ?? metrics.full_run_deuc_elec,
        "umol/J",
      ),
    );
    push(
      formatMetricLine(
        "capped_deuc_elec",
        metrics.capped_deuc_elec?.toFixed?.(3) ?? metrics.capped_deuc_elec ?? metrics.deuc_elec ?? metrics.deuc,
        "umol/J",
      ),
    );
    [
      "watts_in",
      "watts_at_cap",
      "full_run_deuc_elec",
      "capped_deuc_elec",
      "deuc_elec",
      "deuc",
    ].forEach((key) => used.add(key));
  }

  if ("setpoint_ppfd" in metrics) {
    lines.push("");
    lines.push("CAP");
    push(formatMetricLine("cap", metrics.setpoint_ppfd?.toFixed?.(0) ?? metrics.setpoint_ppfd, "umol/m^2/s"));
    push(formatMetricLine("cap_scale", metrics.cap_scale?.toFixed?.(3) ?? metrics.cap_scale));
    push(formatMetricLine("dim_penalty", metrics.dim_penalty?.toFixed?.(3) ?? metrics.dim_penalty));
    push(formatMetricLine("mean_at_cap", metrics.mean_at_cap?.toFixed?.(2) ?? metrics.mean_at_cap, "umol/m^2/s"));
    push(formatMetricLine("min_at_cap", metrics.min_at_cap?.toFixed?.(2) ?? metrics.min_at_cap, "umol/m^2/s"));
    push(formatMetricLine("p05_at_cap", metrics.p05_at_cap?.toFixed?.(2) ?? metrics.p05_at_cap, "umol/m^2/s"));
    const util = formatPercent(metrics.utilization_at_cap, 1);
    push(util ? `utilization_at_cap: ${util}` : null);
    push(formatMetricLine("ppf_at_cap", metrics.ppf_at_cap?.toFixed?.(1) ?? metrics.ppf_at_cap, "umol/s"));
    [
      "setpoint_ppfd",
      "cap_scale",
      "dim_penalty",
      "mean_at_cap",
      "min_at_cap",
      "p05_at_cap",
      "utilization_at_cap",
      "ppf_at_cap",
    ].forEach((key) => used.add(key));
  }


  const plantAbsorptionLines = formatPlantPhotonAbsorption(metrics, used);
  if (plantAbsorptionLines.length) {
    lines.push("");
    lines.push(...plantAbsorptionLines);
  }

  const plantSpectralAbsorptionLines = formatPlantSpectralAbsorption(metrics, used);
  if (plantSpectralAbsorptionLines.length) {
    lines.push("");
    lines.push(...plantSpectralAbsorptionLines);
  }

  if (metrics.legacy && typeof metrics.legacy === "object") {
    lines.push("");
    lines.push("COMPAT");
    const legacy = metrics.legacy;
    push(formatMetricLine("std", legacy.std?.toFixed?.(2) ?? legacy.std, "umol/m^2/s"));
    push(formatMetricLine("cv_percent", legacy.cv_percent?.toFixed?.(2) ?? legacy.cv_percent, "%"));
    push(formatMetricLine("dou_percent", legacy.dou_percent?.toFixed?.(2) ?? legacy.dou_percent, "%"));
    push(formatMetricLine("rmse", legacy.rmse?.toFixed?.(2) ?? legacy.rmse, "umol/m^2/s"));
    push(formatMetricLine("mad", legacy.mad?.toFixed?.(2) ?? legacy.mad, "umol/m^2/s"));
    push(formatMetricLine("min_over_avg", legacy.min_over_avg?.toFixed?.(3) ?? legacy.min_over_avg));
    push(formatMetricLine("min_over_max", legacy.min_over_max?.toFixed?.(3) ?? legacy.min_over_max));
    used.add("legacy");
  }

  const extras = Object.keys(metrics).filter((key) => !used.has(key));
  if (extras.length) {
    lines.push("");
    lines.push("OTHER");
    extras.sort().forEach((key) => {
      const val = metrics[key];
      if (val === undefined) {
        return;
      }
      if (typeof val === "number") {
        push(formatMetricLine(key, Number.isInteger(val) ? val : val.toFixed(4)));
      } else {
        push(`${key}: ${String(val)}`);
      }
    });
  }

  return lines.join("\n").trim() + "\n";
}
