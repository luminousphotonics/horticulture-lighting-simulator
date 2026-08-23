import {
  proposedControlModesAgree,
  proposedLayoutIdentitiesAgree,
  proposedSourceModesAgree,
} from "./result-contracts.js";
import {systemDisplayLabel} from "./system-labels.js";

const RUN_ID_PATTERN = /^[0-9a-f]{32}$/;
const CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS = "conventional_led_control";
const TARGET_CAPPED = "target_capped";
const IMAGE_OBJECT_URLS = new WeakMap();

export function createResultView() {
  const runtimeStatus = required("#runtime-status");
  const runLog = required("#run-log");
  const csvCard = required("#csv-card");
  const viewerCard = required("#viewer-card");
  const scatterCard = required("#scatter-card");
  const baselineLeafUniformityContext = required("#baseline-leaf-uniformity-context");
  const fspmSurfaceSection = required("#fspm-surface-section");
  const farRedIncidentRow = required("#fspm-fr-incident-row");
  const farRedAbsorbedRow = required("#fspm-fr-absorbed-row");
  const fspmLeafAbsorbedRow = required("#fspm-leaf-absorbed-row");
  const visualStage = required("#visual-stage");
  const visualPlaceholder = required("#visual-placeholder");
  const heatmapGrid = required("#heatmap-grid");
  const heatmapCard = required("#heatmap-card");
  const overlayCard = required("#overlay-card");
  const heatmapImage = required("#heatmap-image");
  const overlayImage = required("#overlay-image");
  const heatmapOpen = required("#heatmap-open");
  const overlayOpen = required("#overlay-open");
  const visualContext = required("#visual-context");
  const heatmapDialog = required("#heatmap-dialog");
  const heatmapDialogImage = required("#heatmap-dialog-image");
  const heatmapDialogTitle = required("#heatmap-dialog-title");
  const heatmapDialogContext = required("#heatmap-dialog-context");
  let activeRun = null;

  heatmapOpen.addEventListener("click", () => openHeatmapDialog(
    "PPFD Heatmap", heatmapImage.src,
  ));
  overlayOpen.addEventListener("click", () => openHeatmapDialog(
    "PPFD Heatmap with authoritative overlay", overlayImage.src,
  ));
  required("#heatmap-dialog-close").addEventListener(
    "click", () => heatmapDialog.close(),
  );
  heatmapDialog.addEventListener("click", (event) => {
    if (event.target === heatmapDialog) heatmapDialog.close();
  });

  function setActiveRun(runId) {
    activeRun = runId;
  }

  function reset({fspmLabel = "Automatic — resolves after baseline"} = {}) {
    activeRun = null;
    runLog.textContent = "";
    disableArtifactTile(csvCard);
    disableArtifactTile(viewerCard);
    disableArtifactTile(scatterCard);
    fspmSurfaceSection.hidden = true;
    farRedIncidentRow.hidden = true;
    farRedAbsorbedRow.hidden = true;
    baselineLeafUniformityContext.textContent =
      "Plant-free Stage A field sampled at authoritative physical-leaf centroids";
    document.querySelectorAll("[data-metric]").forEach((element) => {
      element.textContent = element.dataset.metric === "status" ? "Idle" : "—";
    });
    setMetric("fspm", fspmLabel);
    setStatus("idle", "Idle");
    resetVisualizations();
  }

  async function loadResults(result) {
    const response = await fetchWithTransientRetry(
      result.metrics_url,
      {cache: "no-store"},
    );
    const metrics = await response.json();
    if (!response.ok) throw new Error(metrics.error?.message || "Metrics could not be loaded.");
    if (metrics.system_id !== result.system_id) throw new Error("Result system identity is inconsistent.");
    if (metrics.analysis_scope?.value !== result.analysis_scope) throw new Error("Result analysis scope is inconsistent.");
    if (metrics.lighting_target_mode !== result.lighting_target_mode) throw new Error("Result lighting target mode is inconsistent.");
    if (!proposedControlModesAgree(metrics.proposed_control, result.proposed_control)) {
      throw new Error("Result Proposed control mode is inconsistent.");
    }
    if (!proposedLayoutIdentitiesAgree(metrics.proposed_layout, result.proposed_layout)) {
      throw new Error("Result Proposed module arrangement is inconsistent.");
    }
    if (!proposedSourceModesAgree(metrics.proposed_source, result.proposed_source)) {
      throw new Error("Result Proposed source mode is inconsistent.");
    }
    const emittedPpf = requireFiniteResultMetric(metrics.ppf?.emitted_umol_s, "ppf.emitted_umol_s");
    requireResultContractText(metrics.ppf?.emission_boundary_id, "ppf.emission_boundary_id");
    requireResultContractText(metrics.ppf?.emission_boundary_description, "ppf.emission_boundary_description");
    const modeledPower = requireFiniteResultMetric(metrics.power?.effective_w, "power.effective_w");
    setMetric("system", systemDisplayLabel(metrics.system_id));
    setMetric("analysis-scope", metrics.analysis_scope.label);
    renderSpectralBasis(metrics.spectral_basis);
    const capped = metrics.lighting_target_mode === TARGET_CAPPED;
    required("#requested-target-result-label").textContent = capped
      ? "Maximum PPFD cap"
      : "Target mean PPFD";
    required("#target-status-result-label").textContent = capped
      ? "Sampled cap status"
      : "Target feasible";
    setMetric("requested", metrics.requested_target_ppfd_umol_m2_s == null
      ? "Not applicable — full output"
      : `${format(metrics.requested_target_ppfd_umol_m2_s)} µmol/m²/s`);
    setMetric("achieved", `${format(metrics.achieved_mean_ppfd_umol_m2_s)} µmol/m²/s`);
    setMetric("achieved-maximum", `${format(metrics.achieved_maximum_ppfd_umol_m2_s)} µmol/m²/s`);
    setMetric("dimming", metrics.dimming_factor == null ? "Not applicable" : format(metrics.dimming_factor, 5));
    setMetric(
      "feasible",
      metrics.target_feasible == null
        ? "Not applicable — fixed full output"
        : (capped
          ? `${metrics.cap_binding ? "Binding" : "Non-binding"} · ${metrics.cap_compliant ? "compliant" : "non-compliant"}`
          : (metrics.target_feasible ? "Yes" : "No — maximum field returned")),
    );
    setMetric("emitted-ppf", `${format(emittedPpf)} µmol/s`);
    setMetric("modeled-power", `${format(modeledPower)} W`);
    setMetric("modules", String(metrics.counts.modules));
    setMetric("room", `${format(metrics.room.length_ft)} × ${format(metrics.room.width_ft)} ft`);
    setMetric("quality", capitalize(metrics.quality));
    setMetric("status", "Succeeded");
    const policy = metrics.fspm_target_policy;
    const range = policy.classification_range;
    setMetric("fspm", `${capitalize(policy.mode)} · ${format(policy.resolved_target_umol_m2_s)} µmol/m²/s · inclusive ${format(range.lower_umol_m2_s)}–${format(range.upper_umol_m2_s)}`);
    const uniformity = metrics.spatial_uniformity.values;
    setMetric("uniformity-mean", `${formatFixed(uniformity.mean_ppfd, 3)} µmol/m²/s`);
    setMetric("uniformity-minimum", `${formatFixed(uniformity.minimum_ppfd, 3)} µmol/m²/s`);
    setMetric("uniformity-maximum", `${formatFixed(uniformity.maximum_ppfd, 3)} µmol/m²/s`);
    setMetric("uniformity-standard-deviation", `${formatFixed(uniformity.population_standard_deviation_ppfd, 3)} µmol/m²/s`);
    setMetric("uniformity-cv-percent", `${formatFixed(uniformity.coefficient_of_variation_percent, 3)}%`);
    setMetric("uniformity-degree-percent", `${formatFixed(uniformity.degree_of_uniformity_percent, 3)}%`);
    setMetric("uniformity-minimum-mean", formatFixed(uniformity.minimum_to_mean_uniformity, 3));
    setMetric("uniformity-minimum-maximum", formatFixed(uniformity.minimum_to_maximum_ppfd_ratio, 3));
    setMetric("uniformity-sample-count", String(uniformity.sample_count));
    renderBaselineLeafUniformity(metrics.baseline_leaf_position_uniformity);

    activateArtifactTile(
      csvCard,
      result.ppfd_csv_url,
      activeRun,
      artifactPathForResult(result, "csv"),
    );
    activateArtifactTile(
      viewerCard,
      result.plant_layout_viewer_url,
      activeRun,
      artifactPathForResult(result, "viewer"),
    );
    renderSurfaceMetrics(metrics.fspm_surface_light_metrics);
    try {
      await loadVisualizations(result, metrics.visualization);
    } catch (error) {
      appendLog(`Visualizations unavailable: ${error.message}`);
      resetVisualizations();
    }
  }

  function renderSurfaceMetrics(surfaceMetrics) {
    if (!surfaceMetrics) return;
    const parExposure = surfaceMetrics.surface_light.par
      .combined_exposure_per_physical_one_sided_leaf_area;
    const fourBandOrder = ["blue", "green", "orange", "red"];
    const fiveBandOrder = [...fourBandOrder, "far_red"];
    const bandOrder = surfaceMetrics.band_order;
    const farRedAvailable = surfaceMetrics.far_red_executed === true
      && Array.isArray(bandOrder) && bandOrder.length === fiveBandOrder.length
      && bandOrder.every((band, index) => band === fiveBandOrder[index]);
    const fourBandOnly = surfaceMetrics.far_red_executed === false
      && Array.isArray(bandOrder) && bandOrder.length === fourBandOrder.length
      && bandOrder.every((band, index) => band === fourBandOrder[index]);
    if (!farRedAvailable && !fourBandOnly) {
      throw new Error("Authenticated executed-band metadata is inconsistent.");
    }
    setMetric("fspm-plant-count", String(surfaceMetrics.counts.plants));
    setMetric("fspm-area", `${formatFixed(surfaceMetrics.modeled_physical_one_sided_leaf_area_m2, 6)} m²`);
    setMetric("fspm-par-incident-density", `${formatFixed(parExposure.incident_photon_flux_density_umol_m2_s, 3)} µmol/m²/s`);
    setMetric("fspm-par-absorbed-density", `${formatFixed(parExposure.absorbed_photon_flux_density_umol_m2_s, 3)} µmol/m²/s`);
    const absorbed = surfaceMetrics.absorbed_par_metrics;
    requireFiniteResultMetric(
      absorbed?.total_combined_absorbed_par_rate_umol_s,
      "fspm_surface_light_metrics.absorbed_par_metrics.total_combined_absorbed_par_rate_umol_s",
    );
    const captureEfficiency = requireFiniteResultMetric(
      absorbed?.absorbed_capture_efficiency_percent,
      "fspm_surface_light_metrics.absorbed_par_metrics.absorbed_capture_efficiency_percent",
    );
    const absorbedPerWatt = requireFiniteResultMetric(
      absorbed?.absorbed_par_per_electrical_watt_umol_per_j,
      "fspm_surface_light_metrics.absorbed_par_metrics.absorbed_par_per_electrical_watt_umol_per_j",
    );
    const plantCv = requireFiniteResultMetric(
      absorbed?.plant_to_plant_absorbed_exposure_cv_percent,
      "fspm_surface_light_metrics.absorbed_par_metrics.plant_to_plant_absorbed_exposure_cv_percent",
    );
    const plantMinimumMean = requireFiniteResultMetric(
      absorbed?.plant_minimum_to_mean_absorbed_exposure_ratio,
      "fspm_surface_light_metrics.absorbed_par_metrics.plant_minimum_to_mean_absorbed_exposure_ratio",
    );
    const leafCv = requireFiniteResultMetric(
      absorbed?.leaf_to_leaf_absorbed_exposure_cv_percent,
      "fspm_surface_light_metrics.absorbed_par_metrics.leaf_to_leaf_absorbed_exposure_cv_percent",
    );
    setMetric("fspm-absorbed-capture-efficiency", `${formatFixed(captureEfficiency, 3)}%`);
    setMetric("fspm-absorbed-par-per-watt", `${formatFixed(absorbedPerWatt, 3)} µmol/J`);
    setMetric("fspm-plant-absorbed-cv", `${formatFixed(plantCv, 3)}%`);
    setMetric("fspm-plant-minimum-mean", formatFixed(plantMinimumMean, 3));
    setMetric("fspm-leaf-absorbed-cv", `${formatFixed(leafCv, 3)}%`);
    if (farRedAvailable) {
      const farRedExposure = surfaceMetrics.surface_light.far_red
        .combined_exposure_per_physical_one_sided_leaf_area;
      setMetric("fspm-fr-incident-density", `${formatFixed(farRedExposure.incident_photon_flux_density_umol_m2_s, 3)} µmol/m²/s`);
      setMetric("fspm-fr-absorbed-density", `${formatFixed(farRedExposure.absorbed_photon_flux_density_umol_m2_s, 3)} µmol/m²/s`);
    }
    farRedIncidentRow.hidden = !farRedAvailable;
    farRedAbsorbedRow.hidden = !farRedAvailable;
    fspmLeafAbsorbedRow.classList.toggle("wide", !farRedAvailable);
    fspmSurfaceSection.hidden = false;
  }

  async function loadVisualizations(result, fieldIdentity) {
    const response = await fetchWithTransientRetry(
      result.visualization_metadata_url,
      {cache: "no-store"},
    );
    const metadata = await response.json();
    if (!response.ok) throw new Error(metadata.error?.message || "Visualization metadata could not be loaded.");
    if (metadata.schema_id !== "fspm-optics.ppfd-visualization"
        || metadata.run_id !== activeRun
        || metadata.field.identity_sha256 !== fieldIdentity.field_identity_sha256
        || metadata.field.sample_count !== fieldIdentity.sample_count
        || metadata.scatter.count !== fieldIdentity.sample_count) {
      throw new Error("Visualization field identity does not agree with run metrics.");
    }
    await Promise.all([
      loadImage(heatmapImage, result.ppfd_heatmap_url),
      loadImage(overlayImage, result.ppfd_heatmap_overlay_url),
    ]);
    const limits = metadata.display.color_limits_ppfd_umol_m2_s;
    const overlayLegend = metadata.overlay.style_policy.color.legend;
    visualContext.textContent = `X/Y position (m) · PPFD ${format(limits[0])}–${format(limits[1])} µmol/m²/s · ${metadata.display.colormap.name} · origin lower · equal aspect · ${metadata.field.sample_count} raw samples · ${overlayLegend}`;
    heatmapGrid.hidden = false;
    visualPlaceholder.hidden = true;
    visualStage.dataset.available = "true";
    heatmapCard.dataset.available = "true";
    overlayCard.dataset.available = "true";
    heatmapOpen.disabled = false;
    overlayOpen.disabled = false;
    const quarterTurns = metadata.requested_orientation
      ?.heatmap_counterclockwise_quarter_turns || 0;
    if (quarterTurns !== 0) {
      throw new Error("Requested heatmap orientation is incompatible.");
    }
    activateArtifactTile(
      scatterCard,
      result.ppfd_scatter_viewer_url,
      activeRun,
      artifactPathForResult(result, "scatter"),
    );
  }

  function renderSpectralBasis(basis) {
    const row = required(".spectral-control-result");
    if (!basis) {
      row.dataset.counterfactual = "false";
      setMetric("spectral-basis", "Not applicable");
      return;
    }
    const controlled = basis.id === CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS;
    row.dataset.counterfactual = String(controlled);
    setMetric(
      "spectral-basis",
      controlled
        ? "Matched with Conventional LED System's SPD"
        : (basis.applied_to_multispectral_transport
          ? "Native Proposed spectrum"
          : "Not applied — scalar baseline is SPD-independent"),
    );
  }

  function resetVisualizations() {
    releaseImageObjectUrl(heatmapImage);
    releaseImageObjectUrl(overlayImage);
    heatmapImage.removeAttribute("src");
    overlayImage.removeAttribute("src");
    heatmapDialogImage.removeAttribute("src");
    heatmapGrid.hidden = true;
    visualPlaceholder.hidden = false;
    visualStage.dataset.available = "false";
    heatmapCard.dataset.available = "false";
    overlayCard.dataset.available = "false";
    heatmapOpen.disabled = true;
    overlayOpen.disabled = true;
    disableArtifactTile(scatterCard);
    visualContext.textContent = "PPFD color mapping and units become available with validated visualization metadata.";
    if (heatmapDialog.open) heatmapDialog.close();
  }

  function openHeatmapDialog(title, source) {
    if (!source) return;
    heatmapDialogTitle.textContent = title;
    heatmapDialogImage.src = source;
    heatmapDialogContext.textContent = visualContext.textContent;
    heatmapDialog.showModal();
  }

  function appendLog(line) {
    runLog.textContent += `${line}\n`;
    runLog.scrollTop = runLog.scrollHeight;
  }

  function setStatus(state, label) {
    runtimeStatus.dataset.state = state;
    runtimeStatus.querySelector("span:last-child").textContent = label;
  }

  function setMetric(name, value) {
    required(`[data-metric="${name}"]`).textContent = value;
  }

  function renderBaselineLeafUniformity(metricGroup) {
    if (!metricGroup || typeof metricGroup !== "object"
        || !metricGroup.summary || typeof metricGroup.summary !== "object") {
      throw new Error("Authenticated baseline leaf-position metrics are missing.");
    }
    const summary = metricGroup.summary;
    if (metricGroup.available !== true || summary.available !== true) {
      for (const name of [
        "baseline-leaf-target-range",
        "baseline-leaf-under-lit",
        "baseline-leaf-over-lit",
        "baseline-leaf-mad",
        "baseline-leaf-cv",
      ]) setMetric(name, "Unavailable");
      const reason = metricGroup.unavailable_reason_code
        || summary.unavailable_reason_code || "authenticated derivative unavailable";
      baselineLeafUniformityContext.textContent = `Unavailable · ${reason}`;
      return;
    }
    setMetric("baseline-leaf-target-range", formatLeafCountPercentage(summary.target_range_leaves));
    setMetric("baseline-leaf-under-lit", formatLeafCountPercentage(summary.under_lit_leaves));
    setMetric("baseline-leaf-over-lit", formatLeafCountPercentage(summary.over_lit_leaves));
    const mad = summary.mean_absolute_deviation_from_target;
    setMetric(
      "baseline-leaf-mad",
      mad?.available === true
        ? `${formatFixed(mad.value_umol_m2_s, 3)} µmol/m²/s`
        : "Unavailable",
    );
    const cv = summary.leaf_position_ppfd_coefficient_of_variation;
    setMetric(
      "baseline-leaf-cv",
      cv?.available === true ? `${formatFixed(cv.value_percent, 3)}%` : "Unavailable",
    );
    const policy = metricGroup.target_policy;
    baselineLeafUniformityContext.textContent =
      `Equal physical-leaf weight · target ${format(policy.resolved_target_umol_m2_s)} µmol/m²/s · ${summary.denominator_leaf_count} leaves`;
  }

  return {
    appendLog,
    loadResults,
    reset,
    setActiveRun,
    setMetric,
    setStatus,
  };
}

export function artifactPathForResult(result, kind) {
  const runId = result.run_id;
  if (result.execution_mode === "precomputed") {
    const prefix = `/precomputed/${runId}`;
    if (kind === "csv") return `/api/precomputed/playbacks/${runId}/artifacts/ppfd.csv`;
    if (kind === "viewer") return `${prefix}/viewer/index.html`;
    if (kind === "scatter") return `${prefix}/scatter/index.html`;
  }
  if (kind === "csv") return `/api/runs/${runId}/artifacts/ppfd.csv`;
  if (kind === "viewer") return `/runs/${runId}/viewer/index.html`;
  if (kind === "scatter") return `/runs/${runId}/scatter/index.html`;
  throw new Error("Unsupported result artifact kind.");
}

function disableArtifactTile(tile) {
  tile.dataset.available = "false";
  tile.removeAttribute("href");
  tile.setAttribute("aria-disabled", "true");
  tile.setAttribute("tabindex", "-1");
}

function activateArtifactTile(tile, candidate, activeRun, expectedPath) {
  disableArtifactTile(tile);
  if (!RUN_ID_PATTERN.test(activeRun || "")) return;
  const artifactUrl = validateArtifactUrl(candidate, expectedPath);
  if (!artifactUrl) return;
  tile.href = artifactUrl;
  tile.dataset.available = "true";
  tile.setAttribute("aria-disabled", "false");
  tile.removeAttribute("tabindex");
}

export function validateArtifactUrl(candidate, expectedPath) {
  if (typeof candidate !== "string" || candidate.length === 0) return null;
  let artifactUrl;
  try {
    artifactUrl = new URL(candidate, window.location.href);
  } catch {
    return null;
  }
  if ((artifactUrl.protocol !== "http:" && artifactUrl.protocol !== "https:")
      || artifactUrl.origin !== window.location.origin
      || artifactUrl.username !== ""
      || artifactUrl.password !== ""
      || artifactUrl.search !== ""
      || artifactUrl.hash !== ""
      || artifactUrl.pathname !== expectedPath) {
    return null;
  }
  if (candidate !== expectedPath
      && candidate !== `${window.location.origin}${expectedPath}`) {
    return null;
  }
  return artifactUrl.href;
}

async function loadImage(image, source) {
  const response = await fetchWithTransientRetry(source, {cache: "no-store"});
  if (!response.ok) {
    throw new Error("A published heatmap could not be loaded.");
  }
  const objectUrl = URL.createObjectURL(await response.blob());
  releaseImageObjectUrl(image);
  IMAGE_OBJECT_URLS.set(image, objectUrl);
  return new Promise((resolve, reject) => {
    const complete = () => { cleanup(); resolve(); };
    const fail = () => {
      cleanup();
      releaseImageObjectUrl(image);
      reject(new Error("A published heatmap could not be loaded."));
    };
    const cleanup = () => {
      image.removeEventListener("load", complete);
      image.removeEventListener("error", fail);
    };
    image.addEventListener("load", complete, {once: true});
    image.addEventListener("error", fail, {once: true});
    image.src = objectUrl;
    if (image.complete && image.naturalWidth > 0) complete();
  });
}

function releaseImageObjectUrl(image) {
  const objectUrl = IMAGE_OBJECT_URLS.get(image);
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  IMAGE_OBJECT_URLS.delete(image);
}

async function fetchWithTransientRetry(url, options) {
  let attempt = 0;
  while (true) {
    let response;
    try {
      response = await fetch(url, options);
    } catch (_error) {
      await retryWait(attempt++);
      continue;
    }
    if (response.status !== 429 && response.status !== 503) return response;
    const retryAfterHeader = response.headers.get("Retry-After");
    const retryAfter = retryAfterHeader === null ? NaN : Number(retryAfterHeader);
    await retryWait(
      attempt++,
      Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter * 1000 : 1000,
    );
  }
}

function retryWait(attempt, baseMilliseconds = 1000) {
  const delay = Math.min(8000, baseMilliseconds * (1.45 ** Math.min(attempt, 8)));
  const jittered = delay * (0.8 + Math.random() * 0.4);
  return new Promise((resolve) => window.setTimeout(resolve, jittered));
}

function formatLeafCountPercentage(metric) {
  if (!metric || metric.available !== true
      || !Number.isInteger(metric.count) || !Number.isFinite(metric.percentage)) {
    return "Unavailable";
  }
  const rounded = Math.round(metric.percentage * 10) / 10;
  const percentage = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  return `${metric.count}/${percentage}%`;
}

function requireFiniteResultMetric(value, path) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`Result contract error: ${path} must be a finite number.`);
  }
  return value;
}

function requireResultContractText(value, path) {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`Result contract error: ${path} must be a non-empty string.`);
  }
  return value;
}

function format(value, digits = 2) {
  return Number(value).toLocaleString(undefined, {maximumFractionDigits: digits});
}

function formatFixed(value, digits) {
  return Number(value).toFixed(digits);
}

export function capitalize(value) {
  const text = String(value);
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function required(selector) {
  const element = document.querySelector(selector);
  if (!element) throw new Error(`Result view is missing ${selector}.`);
  return element;
}
