import {
  HPS_DEFAULT_MOUNT_Z_M,
  HPS_VARIANT_DEFAULTS,
  LED_DEFAULT_MOUNT_Z_M,
  appState,
  conventionalFixturePpe,
  conventionalFixturePpf,
  defaultExecutionMode,
  publicMaxFt,
  publicMinFt,
  els,
} from "./state.js";

export const DEFAULT_FSPM_LEAF_OPTICAL_PROFILE_ID = "rex_green_butterhead_mature_leaf_optics_v1";
export const DEFAULT_FSPM_LEAF_RADIANCE_MATERIAL_MODE = "rex_source_weighted_trans";
export const DEFAULT_FSPM_SPECTRAL_TRANSPORT_MODE = "banded_5";
export const SCALAR_FSPM_LEAF_RADIANCE_MATERIAL_MODE = "opaque_occluder";
export const SCALAR_FSPM_SPECTRAL_TRANSPORT_MODE = "scalar_source_weighted";

export function parsePositive(el, fallback) {
  const value = Number.parseFloat(el?.value || "");
  if (Number.isFinite(value) && value > 0) {
    return value;
  }
  return fallback;
}

function dimensionWarning(value) {
  if (!Number.isFinite(value)) {
    return "Enter a positive room dimension.";
  }
  if (value <= 0) {
    return "Room dimensions must be greater than zero.";
  }
  if (value < publicMinFt || value > publicMaxFt) {
    return `Public precomputed playback supports ${publicMinFt}-${publicMaxFt} ft rooms.`;
  }
  if (Math.abs(value - Math.round(value)) > 1e-6) {
    return "Use whole-foot dimensions for public precomputed playback.";
  }
  return "";
}

function syncDimensionField(fieldEl, inputEl) {
  if (!fieldEl || !inputEl) {
    return false;
  }
  const raw = Number.parseFloat(inputEl.value || "");
  const warning = dimensionWarning(raw);
  fieldEl.classList.toggle("radiance-field--warning", Boolean(warning));
  inputEl.title = warning;
  inputEl.setCustomValidity(warning);
  return !warning;
}

export function syncDimensionWarnings() {
  const lengthOk = syncDimensionField(els.radLengthField, els.radLength);
  const widthOk = syncDimensionField(els.radWidthField, els.radWidth);
  return lengthOk && widthOk;
}


function parseFinite(el, fallback) {
  const value = Number.parseFloat(el?.value || "");
  return Number.isFinite(value) ? value : fallback;
}

function parseInteger(el, fallback) {
  const value = Number.parseInt(el?.value || "", 10);
  return Number.isFinite(value) ? value : fallback;
}

function liveSupportedModes() {
  const modes = appState.runtimeStatus?.live_supported_modes;
  return Array.isArray(modes) && modes.length ? modes : ["SMD"];
}

function plantsAvailable(mode, executionMode) {
  return executionMode !== "precomputed" && liveSupportedModes().includes(mode);
}

export function syncFspmControls() {
  const mode = els.radMode?.value || "";
  const executionMode = (els.radSimMode?.value || defaultExecutionMode || "precomputed").trim();
  const fspmAvailable = plantsAvailable(mode, executionMode);
  const fspmControls = [
    els.radPlantsEnabled,
    els.radPlantSeed,
    els.radPlantRows,
    els.radPlantColumns,
    els.radPlantSpacingM,
    els.radPlantHeightM,
    els.radPlantCanopyRadiusM,
    els.radPlantLeafCount,
    els.radPlantGrowthStage,
    els.radFspmReceiverGranularity,
    els.radFspmMultispectralMode,
    els.radFspmTargetPpfd,
    els.radFspmTargetTolerance,
  ].filter(Boolean);

  if (els.radFspmFieldset) {
    els.radFspmFieldset.hidden = !fspmAvailable;
  }

  fspmControls.forEach((control) => {
    control.disabled = !fspmAvailable;
    control.title = fspmAvailable
      ? ""
      : "FSPM plant geometry is available only for currently live-supported lighting modes.";
  });

  if (!fspmAvailable && els.radPlantsEnabled) {
    els.radPlantsEnabled.checked = false;
  }
  if (els.radFspmMultispectralMode?.dataset.fspmMultispectralEdited !== "true") {
    syncFspmMultispectralDefault();
  }

  return fspmAvailable;
}

export function syncFspmMultispectralDefault() {
  if (!els.radFspmMultispectralMode) {
    return;
  }
  const granularity = (els.radFspmReceiverGranularity?.value || "leaf_centroid").trim();
  els.radFspmMultispectralMode.checked = granularity !== "leaf_centroid";
}

export function resetFspmMultispectralDefault() {
  if (els.radFspmMultispectralMode) {
    delete els.radFspmMultispectralMode.dataset.fspmMultispectralEdited;
  }
  syncFspmMultispectralDefault();
}

export function markFspmMultispectralEdited() {
  if (els.radFspmMultispectralMode) {
    els.radFspmMultispectralMode.dataset.fspmMultispectralEdited = "true";
  }
}

export function markFspmTargetEdited() {
  if (els.radFspmTargetPpfd) {
    els.radFspmTargetPpfd.dataset.fspmTargetEdited = "true";
  }
}

export function syncFspmTargetDefault() {
  if (!els.radFspmTargetPpfd || els.radFspmTargetPpfd.dataset.fspmTargetEdited === "true") {
    return;
  }
  const target = parsePositive(els.radTarget, 275);
  els.radFspmTargetPpfd.value = `${target}`;
}

function parsePlantPayload(_mode, executionMode) {
  const enabled = Boolean(
    executionMode !== "precomputed"
      && els.radPlantsEnabled
      && els.radPlantsEnabled.checked,
  );
  if (!enabled) {
    return { plantsEnabled: false };
  }
  const multispectralMode = Boolean(els.radFspmMultispectralMode?.checked);
  return {
    plantsEnabled: true,
    plantSeed: parseInteger(els.radPlantSeed, 42),
    plantRows: parseInteger(els.radPlantRows, 2),
    plantColumns: parseInteger(els.radPlantColumns, 2),
    plantSpacingM: parseFinite(els.radPlantSpacingM, 0.30),
    plantHeightM: parseFinite(els.radPlantHeightM, 0.16),
    plantCanopyRadiusM: parseFinite(els.radPlantCanopyRadiusM, 0.18),
    plantLeafCount: parseInteger(els.radPlantLeafCount, 12),
    plantGrowthStage: parseFinite(els.radPlantGrowthStage, 1.0),
    fspmReceiverGranularity: (els.radFspmReceiverGranularity?.value || "leaf_centroid").trim(),
    fspmLeafOpticalProfileId: DEFAULT_FSPM_LEAF_OPTICAL_PROFILE_ID,
    fspmLeafRadianceMaterialMode: multispectralMode
      ? DEFAULT_FSPM_LEAF_RADIANCE_MATERIAL_MODE
      : SCALAR_FSPM_LEAF_RADIANCE_MATERIAL_MODE,
    fspmSpectralTransportMode: multispectralMode
      ? DEFAULT_FSPM_SPECTRAL_TRANSPORT_MODE
      : SCALAR_FSPM_SPECTRAL_TRANSPORT_MODE,
    fspmTargetPpfdUmolM2S: parsePositive(els.radFspmTargetPpfd, parsePositive(els.radTarget, 275)),
    fspmTargetToleranceUmolM2S: parsePositive(els.radFspmTargetTolerance, 20),
  };
}

export function parsePayload() {
  const dimsOk = syncDimensionWarnings();
  if (!dimsOk) {
    throw new Error(`Room dimensions must be whole-foot values from ${publicMinFt} ft through ${publicMaxFt} ft.`);
  }
  const mountHeightM = Number.parseFloat(els.radMountHeight?.value || `${LED_DEFAULT_MOUNT_Z_M}`);
  const mode = els.radMode.value;
  const basisBackend = mode === "SMD" ? (els.radBasisBackend?.value || "rtrace").trim() : "rtrace";
  const executionMode = (els.radSimMode?.value || defaultExecutionMode || "precomputed").trim();
  return {
    mode,
    mountHeightM: Number.isFinite(mountHeightM) ? mountHeightM : LED_DEFAULT_MOUNT_Z_M,
    executionMode,
    qualityPreset: (els.radQualityPreset?.value || "standard").trim(),
    hpsCoverage: parsePositive(els.radHpsCoverage, 4),
    hpsVariant: (els.radHpsVariant?.value || "karma").trim().toLowerCase(),
    competitorLayout: (els.radLedLayout?.value || "practical").trim(),
    length: Number.parseFloat(els.radLength.value),
    width: Number.parseFloat(els.radWidth.value),
    target: parsePositive(els.radTarget, 1000),
    peakCappingEnabled: Boolean(els.radPeakCapping && !els.radPeakCapping.disabled && els.radPeakCapping.checked),
    matchSystemPpe: els.radMatchSystemPpe ? Boolean(els.radMatchSystemPpe.checked) : true,
    basisBackend,
    ...parsePlantPayload(mode, executionMode),
  };
}

export function radiancePayload(action) {
  const values = parsePayload();
  const isOurSystem = values.mode === "SMD";
  const isHps = values.mode === "1000W HPS";
  const hpsDefaults = HPS_VARIANT_DEFAULTS[values.hpsVariant] || HPS_VARIANT_DEFAULTS.karma;
  const hpsMountHeightM = isHps && values.executionMode === "precomputed"
    ? HPS_DEFAULT_MOUNT_Z_M
    : values.mountHeightM;
  const payload = {
    action,
    mode: values.mode,
    execution_mode: values.executionMode,
    sim_mode: values.qualityPreset,
    length_ft: values.length,
    width_ft: values.width,
    target_ppfd: values.target,
    peak_capping_enabled: values.peakCappingEnabled,
    run_basis: true,
    w_min: isOurSystem ? 0 : 10,
    w_max: 100,
    subpatch_grid: 1,
    mount_z_m: values.mountHeightM,
    overlay: "auto",
    smd_base_ring: 0,
    basis_backend: values.basisBackend,
    // Checked by default for architecture/uniformity comparisons. Uncheck in
    // Proposed mode to use the native SMD curve and thermal droop model.
    match_system_ppe: isOurSystem ? values.matchSystemPpe : false,
    sp_ppf: conventionalFixturePpf,
    sp_z_m: values.mountHeightM,
    sp_ppe: conventionalFixturePpe,
    competitor_layout: values.mode === "Competitor" ? values.competitorLayout : "full",
    hps_coverage_ft: isHps ? values.hpsCoverage : 4,
    hps_z_m: hpsMountHeightM,
    hps_fixture_ppf: hpsDefaults.fixturePpf,
    hps_input_watts: hpsDefaults.inputWatts,
    hps_ies_variant: "karma",
    plants_enabled: values.plantsEnabled,
  };
  if (values.plantsEnabled) {
    Object.assign(payload, {
      plant_seed: values.plantSeed,
      plant_rows: values.plantRows,
      plant_columns: values.plantColumns,
      plant_spacing_m: values.plantSpacingM,
      plant_height_m: values.plantHeightM,
      plant_canopy_radius_m: values.plantCanopyRadiusM,
      plant_leaf_count: values.plantLeafCount,
      plant_growth_stage: values.plantGrowthStage,
      fspm_receiver_granularity: values.fspmReceiverGranularity,
      fspm_leaf_optical_profile_id: values.fspmLeafOpticalProfileId,
      fspm_leaf_radiance_material_mode: values.fspmLeafRadianceMaterialMode,
      fspm_spectral_transport_mode: values.fspmSpectralTransportMode,
      fspm_target_ppfd_umol_m2_s: values.fspmTargetPpfdUmolM2S,
      fspm_target_tolerance_umol_m2_s: values.fspmTargetToleranceUmolM2S,
    });
  }
  return payload;
}

export function runKeyForPayload(payload) {
  return JSON.stringify({
    mode: payload.mode,
    mountHeightM: payload.mountHeightM,
    executionMode: payload.executionMode,
    qualityPreset: payload.qualityPreset,
    length: payload.length,
    width: payload.width,
    target: payload.target,
    peakCappingEnabled: payload.peakCappingEnabled,
    matchSystemPpe: payload.matchSystemPpe,
    hpsCoverage: payload.hpsCoverage,
    hpsVariant: payload.hpsVariant,
    competitorLayout: payload.competitorLayout,
    basisBackend: payload.basisBackend,
    plantsEnabled: payload.plantsEnabled,
    plantSeed: payload.plantSeed,
    plantRows: payload.plantRows,
    plantColumns: payload.plantColumns,
    plantSpacingM: payload.plantSpacingM,
    plantHeightM: payload.plantHeightM,
    plantCanopyRadiusM: payload.plantCanopyRadiusM,
    plantLeafCount: payload.plantLeafCount,
    plantGrowthStage: payload.plantGrowthStage,
    fspmReceiverGranularity: payload.fspmReceiverGranularity,
    fspmLeafOpticalProfileId: payload.fspmLeafOpticalProfileId,
    fspmLeafRadianceMaterialMode: payload.fspmLeafRadianceMaterialMode,
    fspmSpectralTransportMode: payload.fspmSpectralTransportMode,
    fspmTargetPpfdUmolM2S: payload.fspmTargetPpfdUmolM2S,
    fspmTargetToleranceUmolM2S: payload.fspmTargetToleranceUmolM2S,
  });
}

export function currentRunKey() {
  return runKeyForPayload(parsePayload());
}

export function defaultMountHeightForMode(mode) {
  return mode === "1000W HPS" ? HPS_DEFAULT_MOUNT_Z_M : LED_DEFAULT_MOUNT_Z_M;
}

function formatMountHeightLabel(mountHeightM) {
  const inches = Math.round(Number(mountHeightM || 0) / 0.0254);
  return `${inches}"`;
}

export function syncMountHeightForMode({ resetToDefault = false } = {}) {
  if (!els.radMountHeight) {
    return;
  }
  const fallback = defaultMountHeightForMode(els.radMode?.value);
  const current = Number.parseFloat(els.radMountHeight.value || "");
  const desired = resetToDefault || !Number.isFinite(current) ? fallback : current;
  els.radMountHeight.value = `${desired}`;
}

export function syncModeControls({ resetMountHeight = false } = {}) {
  const isOurSystem = els.radMode?.value === "SMD";
  const isHps = els.radMode?.value === "1000W HPS";
  const isCompetitor = els.radMode?.value === "Competitor";
  const isDimmableLed = isOurSystem || isCompetitor;
  syncFspmControls();
  syncMountHeightForMode({ resetToDefault: resetMountHeight });
  const activeMountHeightM = Number.parseFloat(els.radMountHeight?.value || `${defaultMountHeightForMode(els.radMode?.value)}`);
  const activeMountHeightLabel = formatMountHeightLabel(activeMountHeightM);
  if (els.radMountHeightField) {
    els.radMountHeightField.classList.toggle("radiance-field--inactive", false);
  }
  if (els.radMountHeight) {
    els.radMountHeight.disabled = false;
    els.radMountHeight.title = "Mounting height applies to the currently selected lighting system.";
  }
  if (els.radSimModeField) {
    els.radSimModeField.classList.toggle("radiance-field--inactive", Boolean(els.radSimModeField.hidden));
  }
  if (els.radBasisBackendField) {
    els.radBasisBackendField.classList.toggle("radiance-field--inactive", !isOurSystem);
  }
  if (els.radBasisBackend) {
    els.radBasisBackend.disabled = !isOurSystem;
    els.radBasisBackend.title = isOurSystem ? "" : "SMD Basis Backend applies only to Proposed LED System mode.";
  }
  const executionMode = (els.radSimMode?.value || defaultExecutionMode || "precomputed").trim();
  const qualityActive = executionMode !== "precomputed";
  if (els.radQualityField) {
    els.radQualityField.classList.toggle("radiance-field--inactive", !qualityActive);
  }
  if (els.radQualityPreset) {
    els.radQualityPreset.disabled = !qualityActive;
    els.radQualityPreset.title = qualityActive
      ? ""
      : "Precomputed playback uses bundled Standard outputs.";
    if (!qualityActive) {
      els.radQualityPreset.value = "standard";
    }
  }
  if (els.radHpsCoverageField) {
    els.radHpsCoverageField.classList.toggle("radiance-field--inactive", !isHps);
  }
  if (els.radHpsCoverage) {
    els.radHpsCoverage.disabled = !isHps;
    els.radHpsCoverage.title = isHps ? "" : "HPS layout applies only to 1000W HPS mode.";
  }
  if (els.radHpsVariantField) {
    els.radHpsVariantField.classList.toggle("radiance-field--inactive", !isHps);
  }
  if (els.radHpsVariant) {
    els.radHpsVariant.disabled = !isHps;
    els.radHpsVariant.title = isHps ? "" : "HPS comparator applies only to 1000W HPS mode.";
  }
  if (els.radLedLayoutField) {
    els.radLedLayoutField.classList.toggle("radiance-field--inactive", !isCompetitor);
  }
  if (els.radLedLayout) {
    els.radLedLayout.disabled = !isCompetitor;
    els.radLedLayout.title = isCompetitor ? "" : "LED Layout applies only to Conventional LED System mode.";
  }
  if (els.radTargetField) {
    els.radTargetField.classList.toggle("radiance-field--fixed-output", isHps);
  }
  if (els.radTarget) {
    els.radTarget.title = isHps
      ? "1000W HPS runs at fixed output. Target PPFD is for reference only."
      : "";
  }
  if (els.radPeakCappingField) {
    els.radPeakCappingField.hidden = true;
  }
  if (els.radPeakCapping) {
    if (!isDimmableLed) {
      els.radPeakCapping.checked = false;
      els.radPeakCapping.disabled = true;
      els.radPeakCapping.title = "Peak capping is available only for dimmable LED systems.";
    } else {
      els.radPeakCapping.disabled = false;
      els.radPeakCapping.title = "";
    }
  }
  if (els.radMatchSystemPpeField) {
    els.radMatchSystemPpeField.hidden = !isOurSystem;
    els.radMatchSystemPpeField.classList.toggle("radiance-field--inactive", !isOurSystem);
  }
  if (els.radMatchSystemPpe) {
    els.radMatchSystemPpe.disabled = !isOurSystem;
    els.radMatchSystemPpe.title = isOurSystem
      ? "Checked: match Proposed LED System source efficacy to the Conventional LED comparator. Unchecked: use native SMD curve and thermal droop model."
      : "PPE matching applies only to Proposed LED System mode.";
  }
  syncDimensionWarnings();
  if (els.radVisualNote) {
    els.radVisualNote.textContent = "";
    els.radVisualNote.hidden = true;
  }
  return activeMountHeightLabel;
}

export function parseElectricalPayload() {
  const payload = parsePayload();
  const rate = parsePositive(els.elecRate, 0.128);
  const stages = [
    {
      name: "Propagation",
      days: parsePositive(els.elecPropDays, 14),
      hours_per_day: parsePositive(els.elecPropHours, 18),
      avg_ppfd: parsePositive(els.elecPropPpfd, 250),
    },
    {
      name: "Veg",
      days: parsePositive(els.elecVegDays, 21),
      hours_per_day: parsePositive(els.elecVegHours, 18),
      avg_ppfd: parsePositive(els.elecVegPpfd, 650),
    },
    {
      name: "Flower",
      days: parsePositive(els.elecFlowerDays, 56),
      hours_per_day: parsePositive(els.elecFlowerHours, 12),
      avg_ppfd: parsePositive(els.elecFlowerPpfd, 950),
    },
  ];
  return {
    mode: payload.mode,
    execution_mode: payload.executionMode,
    sim_mode: payload.qualityPreset,
    length_ft: payload.length,
    width_ft: payload.width,
    target_ppfd: payload.target,
    peak_capping_enabled: payload.peakCappingEnabled,
    match_system_ppe: payload.mode === "SMD" ? payload.matchSystemPpe : false,
    basis_backend: payload.basisBackend,
    competitor_layout: payload.competitorLayout,
    hps_coverage_ft: payload.hpsCoverage,
    utility_rate_kwh: rate,
    stages,
  };
}

export function invalidateRenderedRunState() {
  appState.lastCompletedRunKey = "";
  appState.lastCompletedCsvHref = "";
  appState.currentArtifactToken = "";
}
