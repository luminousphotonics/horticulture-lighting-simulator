import { apiFetch, ensureBackend } from "./api.js";
import {
  parseElectricalPayload,
  parsePayload,
  radiancePayload,
} from "./forms.js";
import {
  appendOutput,
  formatMetrics,
  openModal,
  renderCostEstimate,
  renderElectricalEstimate,
  resetCostEstimate,
  resetElectricalEstimate,
  sanitizePublicData,
} from "./renderers.js";
import {
  HPS_DEFAULT_MOUNT_Z_M,
  appState,
  backendDefault,
  els,
  radianceSessionId,
} from "./state.js";

const PLACEHOLDER_SRC = "/static/img/transparent-placeholder.svg";
const OPTIONAL_PLANT_QUERY_FIELDS = [
  ["plantsEnabled", "plants_enabled"],
  ["plantSeed", "plant_seed"],
  ["plantRows", "plant_rows"],
  ["plantColumns", "plant_columns"],
  ["plantSpacingM", "plant_spacing_m"],
  ["plantHeightM", "plant_height_m"],
  ["plantCanopyRadiusM", "plant_canopy_radius_m"],
  ["plantLeafCount", "plant_leaf_count"],
  ["plantGrowthStage", "plant_growth_stage"],
  ["fspmReceiverGranularity", "fspm_receiver_granularity"],
  ["fspmLeafOpticalProfileId", "fspm_leaf_optical_profile_id"],
  ["fspmLeafRadianceMaterialMode", "fspm_leaf_radiance_material_mode"],
  ["fspmSpectralTransportMode", "fspm_spectral_transport_mode"],
  ["fspmTargetPpfdUmolM2S", "fspm_target_ppfd_umol_m2_s"],
  ["fspmTargetToleranceUmolM2S", "fspm_target_tolerance_umol_m2_s"],
];

function hpsArtifactMountHeightM(payload) {
  return payload.mode === "1000W HPS" && payload.executionMode === "precomputed"
    ? HPS_DEFAULT_MOUNT_Z_M
    : payload.mountHeightM;
}

function artifactPowerBounds(payload) {
  return {
    wMin: payload.mode === "SMD" ? 0 : 10,
    wMax: 100,
  };
}

export function buildPpfdCsvDownloadUrl(payload) {
  const params = artifactQueryParams(payload);
  params.set("session_id", radianceSessionId);
  const base = (appState.backendUrl || backendDefault).replace(/\/+$/, "");
  return `${base}/radiance/ppfd-csv?${params.toString()}`;
}

function clearImage(img) {
  if (img) {
    img.src = PLACEHOLDER_SRC;
  }
}

function waitForImagePaint(img, src) {
  return new Promise((resolve, reject) => {
    if (!img) {
      resolve();
      return;
    }

    const cleanup = () => {
      img.removeEventListener("load", onLoad);
      img.removeEventListener("error", onError);
    };

    const onLoad = () => {
      cleanup();
      resolve();
    };

    const onError = () => {
      cleanup();
      reject(new Error(`Failed to load image: ${src}`));
    };

    img.addEventListener("load", onLoad, { once: true });
    img.addEventListener("error", onError, { once: true });
    img.src = src;

    if (img.complete && img.naturalWidth > 0) {
      cleanup();
      resolve();
    }
  });
}

export function artifactQueryParams(payload, extras = {}) {
  const hpsMountHeightM = hpsArtifactMountHeightM(payload);
  const { wMin, wMax } = artifactPowerBounds(payload);
  const params = new URLSearchParams({
    mode: payload.mode,
    execution_mode: payload.executionMode,
    sim_mode: payload.qualityPreset,
    target_ppfd: String(payload.target),
    peak_capping_enabled: String(payload.peakCappingEnabled),
    match_system_ppe: String(payload.mode === "SMD" ? payload.matchSystemPpe : false),
    length_ft: String(payload.length),
    width_ft: String(payload.width),
    w_min: String(wMin),
    w_max: String(wMax),
    hps_coverage_ft: String(payload.hpsCoverage),
    hps_ies_variant: "karma",
    basis_backend: payload.basisBackend,
    competitor_layout: String(payload.competitorLayout),
    mount_z_m: String(payload.mountHeightM),
    sp_z_m: String(payload.mountHeightM),
    hps_z_m: String(hpsMountHeightM),
    ...extras,
  });
  if (appState.currentArtifactToken) {
    params.set("artifact_token", appState.currentArtifactToken);
  }
  for (const [payloadKey, queryKey] of OPTIONAL_PLANT_QUERY_FIELDS) {
    const value = payload?.[payloadKey];
    if (value !== undefined && value !== null && value !== "") {
      params.set(queryKey, String(value));
    }
  }
  return params;
}

export async function refreshRadianceImages(logErrors = false) {
  try {
    const payload = parsePayload();
    const params = artifactQueryParams(payload);
    const res = await apiFetch(
      `/radiance/images?${params.toString()}`
    );
    const data = await res.json();
    const pending = [];
    if (data.overlay) {
      pending.push(waitForImagePaint(els.radImgOverlay, `${appState.backendUrl}${data.overlay}`));
    } else {
      clearImage(els.radImgOverlay);
    }
    if (data.annot) {
      pending.push(waitForImagePaint(els.radImgAnnot, `${appState.backendUrl}${data.annot}`));
    } else {
      clearImage(els.radImgAnnot);
    }
    await Promise.all(pending);
  } catch (err) {
    clearImage(els.radImgOverlay);
    clearImage(els.radImgAnnot);
    if (logErrors) {
      appendOutput(els.radLog, `Image refresh failed: ${err}`);
    }
  }
}

export async function refreshRadianceMetrics(logErrors = false) {
  try {
    const payload = parsePayload();
    const params = artifactQueryParams(payload);
    const res = await apiFetch(`/radiance/metrics?${params.toString()}`);
    const data = await res.json();
    if (!payload.peakCappingEnabled && data?.metrics && typeof data.metrics === "object") {
      [
        "setpoint_ppfd",
        "cap_scale",
        "dim_penalty",
        "mean_at_cap",
        "min_at_cap",
        "p05_at_cap",
        "utilization_at_cap",
        "ppf_at_cap",
        "watts_at_cap",
        "capped_deuc_elec",
        "full_run_deuc_elec",
      ].forEach((key) => {
        delete data.metrics[key];
      });
      data.metrics.mode_note = "Peak-capping is disabled. Dimmable LED systems are evaluated against the requested target PPFD without hotspot-cap post-processing.";
    }
    els.radMetrics.textContent = formatMetrics(data.metrics);
    renderCostEstimate(data.cost_estimate);
  } catch (err) {
    els.radMetrics.textContent = `Metrics unavailable: ${err}`;
    resetCostEstimate("Cost estimate unavailable.");
    if (logErrors) {
      appendOutput(els.radLog, `Metrics refresh failed: ${err}`);
    }
  }
}

export async function generateManifest(showModal = false, logErrors = false) {
  appendOutput(els.radLog, "Generating manifest...");
  try {
    const res = await apiFetch("/radiance/manifest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(radiancePayload("visualize")),
    });
    const data = await res.json();
    appState.currentManifestText = JSON.stringify(sanitizePublicData(data.manifest), null, 2);
    if (showModal) {
      openModal({ title: "Deterministic Manifest", text: appState.currentManifestText });
    }
  } catch (err) {
    appState.currentManifestText = `Manifest unavailable: ${err}`;
    if (logErrors) {
      appendOutput(els.radLog, `Manifest generation failed: ${err}`);
    }
  }
}

export async function openScatter() {
  const payload = parsePayload();
  const params = artifactQueryParams(payload);
  const path = `/radiance/scatter?${params.toString()}`;
  const priorLabel = els.btnRadScatter?.textContent || "Load 3D Scatter";
  if (els.btnRadScatter) {
    els.btnRadScatter.disabled = true;
    els.btnRadScatter.textContent = "Loading 3D Scatter...";
  }
  openModal({ title: "3D Scatter", text: "Loading the interactive 3D scatter plot..." });
  try {
    await apiFetch(path, { method: "HEAD", cache: "no-store" });
    const joiner = path.includes("?") ? "&" : "?";
    const url = `${appState.backendUrl}${path}${joiner}session_id=${encodeURIComponent(radianceSessionId)}&v=${Date.now()}`;
    openModal({ title: "3D Scatter", frameSrc: url });
  } catch (err) {
    appendOutput(els.radLog, `Scatter load error: ${err}`);
    openModal({ title: "3D Scatter", text: `Unable to load the 3D scatter plot.\n\n${err}` });
  } finally {
    if (els.btnRadScatter) {
      els.btnRadScatter.disabled = false;
      els.btnRadScatter.textContent = priorLabel;
    }
  }
}

export async function openImageModal(imgEl) {
  const name = imgEl.dataset.file;
  const srcCurrent = imgEl?.src || "";
  if (!name && !srcCurrent) {
    openModal({ title: "Visualization", text: "Run a simulation to load this visualization." });
    return;
  }
  if (!appState.backendUrl) {
    await ensureBackend();
  }
  if (!appState.backendUrl) {
    openModal({ title: "Visualization", text: "Backend unavailable." });
    return;
  }
  const payload = parsePayload();
  const params = artifactQueryParams(payload, {
    name: name || "",
    session_id: radianceSessionId,
  });
  const src = srcCurrent || `${appState.backendUrl}/radiance/image?${params.toString()}`;
  openModal({ title: name || "Visualization", imageSrc: src });
}

export async function estimateElectricalCost() {
  const priorLabel = els.btnElecCalculate?.textContent || "Calculate Cycle Cost";
  if (els.btnElecCalculate) {
    els.btnElecCalculate.disabled = true;
    els.btnElecCalculate.textContent = "Calculating...";
  }
  try {
    const res = await apiFetch("/radiance/electrical-estimate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(parseElectricalPayload()),
    });
    const data = await res.json();
    renderElectricalEstimate(data);
  } catch (err) {
    resetElectricalEstimate();
    if (els.elecSummary) {
      els.elecSummary.textContent = `Electrical estimate unavailable: ${err}`;
    }
  } finally {
    if (els.btnElecCalculate) {
      els.btnElecCalculate.disabled = false;
      els.btnElecCalculate.textContent = priorLabel;
    }
  }
}
