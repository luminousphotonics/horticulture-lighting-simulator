// @ts-check

/**
 * @typedef {object} RadianceConfig
 * @property {string=} apiBase
 * @property {string=} defaultExecutionMode
 * @property {number=} publicMinFt
 * @property {number=} publicMaxFt
 * @property {number=} defaultLengthFt
 * @property {number=} defaultWidthFt
 * @property {string=} precomputedDownloadCommand
 * @property {string=} precomputedSizeText
 * @property {boolean=} showLiveModes
 */

/**
 * @template {HTMLElement} T
 * @param {string} id
 * @returns {T}
 */
function byId(id) {
  return /** @type {T} */ (document.getElementById(id));
}

function readRadianceConfig() {
  const configEl = document.getElementById("radiance-config");
  if (!configEl?.textContent) {
    return {};
  }
  try {
    const config = JSON.parse(configEl.textContent);
    return /** @type {RadianceConfig} */ (config && typeof config === "object" ? config : {});
  } catch (_err) {
    return {};
  }
}

export const radianceConfig = readRadianceConfig();
export const backendDefault = (typeof radianceConfig.apiBase === "string" ? radianceConfig.apiBase : "/radiance-api").replace(/\/+$/, "");
export const defaultExecutionMode = radianceConfig.defaultExecutionMode || "precomputed";
export const publicMinFt = Number(radianceConfig.publicMinFt || 10);
export const publicMaxFt = Number(radianceConfig.publicMaxFt || 30);
export const defaultLengthFt = Number(radianceConfig.defaultLengthFt || 10);
export const defaultWidthFt = Number(radianceConfig.defaultWidthFt || 10);
export const precomputedDownloadCommand = radianceConfig.precomputedDownloadCommand || "python scripts/radiance/download_precomputed.py --dataset full";
export const precomputedSizeText = radianceConfig.precomputedSizeText || "55-75 MB";
export const showLiveModes = Boolean(radianceConfig.showLiveModes);
export const healthTimeoutMs = 12000;

export const LED_DEFAULT_MOUNT_Z_M = 0.4572;
export const HPS_DEFAULT_MOUNT_Z_M = 0.4572;
export const conventionalFixturePpe = 2.8;
export const conventionalFixturePpf = 800.0 * conventionalFixturePpe;
export const HPS_DEFAULT_FIXTURE_PPE = 1.72;
export const HPS_VARIANT_DEFAULTS = {
  karma: { fixturePpf: 1045 * HPS_DEFAULT_FIXTURE_PPE, inputWatts: 1045 },
};

export const radianceSessionId = (() => {
  try {
    const existing = window.localStorage.getItem("radianceSessionId");
    if (existing) {
      return existing;
    }
    const created = (window.crypto?.randomUUID?.() || `rad-${Date.now()}-${Math.random().toString(16).slice(2)}`);
    window.localStorage.setItem("radianceSessionId", created);
    return created;
  } catch (_err) {
    return `rad-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }
})();

export const appState = {
  backendUrl: null,
  progressTimer: null,
  progressValue: 0,
  currentManifestText: "Run a simulation to load the manifest.",
  lastCompletedRunKey: "",
  lastCompletedCsvHref: "",
  currentArtifactToken: "",
  runtimeStatus: null,
  pendingLiveExecutionMode: "",
};

/**
 * @typedef {object} Els
 * @property {HTMLPreElement} radLog
 * @property {HTMLPreElement} radMetrics
 * @property {HTMLElement} radStatusNote
 * @property {HTMLSelectElement} radMode
 * @property {HTMLElement} radBasisBackendField
 * @property {HTMLSelectElement} radBasisBackend
 * @property {HTMLElement} radMountHeightField
 * @property {HTMLSelectElement} radMountHeight
 * @property {HTMLElement} radSimModeField
 * @property {HTMLSelectElement} radSimMode
 * @property {HTMLElement} radQualityField
 * @property {HTMLSelectElement} radQualityPreset
 * @property {HTMLElement} radHpsCoverageField
 * @property {HTMLSelectElement} radHpsCoverage
 * @property {HTMLElement} radHpsVariantField
 * @property {HTMLSelectElement} radHpsVariant
 * @property {HTMLElement} radLedLayoutField
 * @property {HTMLSelectElement} radLedLayout
 * @property {HTMLElement} radLengthField
 * @property {HTMLInputElement} radLength
 * @property {HTMLElement} radWidthField
 * @property {HTMLInputElement} radWidth
 * @property {HTMLInputElement} radTarget
 * @property {HTMLElement} radTargetField
 * @property {HTMLElement} radPeakCappingField
 * @property {HTMLInputElement} radPeakCapping
 * @property {HTMLElement} radVisualNote
 * @property {HTMLImageElement} radImgOverlay
 * @property {HTMLImageElement} radImgAnnot
 * @property {HTMLAnchorElement} btnRadPpfdCsv
 * @property {HTMLButtonElement} btnRadAssembly
 * @property {HTMLElement} radCostCard
 * @property {HTMLElement} radCostTitle
 * @property {HTMLElement} radCostTotal
 * @property {HTMLElement} radCostSubtitle
 * @property {HTMLElement} radCostSummary
 * @property {HTMLElement} radCostBreakdown
 * @property {HTMLElement} radProgress
 * @property {HTMLElement} radProgressFill
 * @property {HTMLElement} radProgressLabel
 * @property {HTMLElement} radProgressText
 * @property {HTMLButtonElement} btnRadAll
 * @property {HTMLButtonElement} btnRadScatter
 * @property {HTMLButtonElement} btnRadMetrics
 * @property {HTMLButtonElement} btnRadManifest
 * @property {HTMLButtonElement} btnAboutOpen
 * @property {HTMLButtonElement} btnDemoGuideManual
 * @property {HTMLButtonElement} btnRadExplainMetrics
 * @property {HTMLButtonElement} btnRadElectricalCost
 * @property {HTMLElement} modal
 * @property {HTMLImageElement} modalImage
 * @property {HTMLIFrameElement} modalFrame
 * @property {HTMLPreElement} modalText
 * @property {HTMLElement} modalTitle
 * @property {HTMLButtonElement} modalClose
 * @property {HTMLElement} metricsModal
 * @property {HTMLButtonElement} metricsModalClose
 * @property {HTMLElement} aboutModal
 * @property {HTMLButtonElement} aboutModalClose
 * @property {HTMLButtonElement} aboutModalCancel
 * @property {HTMLElement} electricalModal
 * @property {HTMLButtonElement} electricalModalClose
 * @property {HTMLElement} missingBundleModal
 * @property {HTMLElement} missingBundleTitle
 * @property {HTMLElement} missingBundleMessage
 * @property {HTMLElement} missingBundleRequest
 * @property {HTMLElement} missingBundleSize
 * @property {HTMLElement} missingBundleCommand
 * @property {HTMLButtonElement} missingBundleClose
 * @property {HTMLButtonElement} missingBundleCancel
 * @property {HTMLButtonElement} missingBundleCopy
 * @property {HTMLButtonElement} missingBundleDemo
 * @property {HTMLElement} liveRuntimeModal
 * @property {HTMLElement} liveRuntimeEyebrow
 * @property {HTMLElement} liveRuntimeTitle
 * @property {HTMLElement} liveRuntimeMessage
 * @property {HTMLElement} liveRuntimeFacts
 * @property {HTMLElement} liveRuntimeDiagnostics
 * @property {HTMLElement} liveRuntimeNote
 * @property {HTMLElement} liveRuntimeCommands
 * @property {HTMLButtonElement} liveRuntimeClose
 * @property {HTMLButtonElement} liveRuntimePrecomputed
 * @property {HTMLButtonElement} liveRuntimeRecheck
 * @property {HTMLButtonElement} liveRuntimeCancel
 * @property {HTMLFormElement} electricalForm
 * @property {HTMLButtonElement} btnElecCalculate
 * @property {HTMLInputElement} elecRate
 * @property {HTMLInputElement} elecPropDays
 * @property {HTMLInputElement} elecPropHours
 * @property {HTMLInputElement} elecPropPpfd
 * @property {HTMLInputElement} elecVegDays
 * @property {HTMLInputElement} elecVegHours
 * @property {HTMLInputElement} elecVegPpfd
 * @property {HTMLInputElement} elecFlowerDays
 * @property {HTMLInputElement} elecFlowerHours
 * @property {HTMLInputElement} elecFlowerPpfd
 * @property {HTMLElement} elecTotalCost
 * @property {HTMLElement} elecTotalMeta
 * @property {HTMLElement} elecSummary
 * @property {HTMLElement} elecStageResults
 * @property {HTMLElement} elecNotes
 */

export const els = /** @type {Els} */ ({
  radLog: byId("rad-log"),
  radMetrics: byId("rad-metrics"),
  radStatusNote: byId("rad-status-note"),
  radMode: byId("rad-mode"),
  radBasisBackendField: byId("rad-basis-backend-field"),
  radBasisBackend: byId("rad-basis-backend"),
  radMountHeightField: byId("rad-mount-height-field"),
  radMountHeight: byId("rad-mount-height"),
  radSimModeField: byId("rad-sim-mode-field"),
  radSimMode: byId("rad-sim-mode"),
  radQualityField: byId("rad-quality-field"),
  radQualityPreset: byId("rad-quality-preset"),
  radHpsCoverageField: byId("rad-hps-coverage-field"),
  radHpsCoverage: byId("rad-hps-coverage"),
  radHpsVariantField: byId("rad-hps-variant-field"),
  radHpsVariant: byId("rad-hps-variant"),
  radLedLayoutField: byId("rad-led-layout-field"),
  radLedLayout: byId("rad-led-layout"),
  radLengthField: byId("rad-length-field"),
  radLength: byId("rad-length"),
  radWidthField: byId("rad-width-field"),
  radWidth: byId("rad-width"),
  radTarget: byId("rad-target"),
  radTargetField: byId("rad-target-field"),
  radPeakCappingField: byId("rad-peak-capping-field"),
  radPeakCapping: byId("rad-peak-capping"),
  radVisualNote: byId("rad-visual-note"),
  radImgOverlay: byId("rad-img-overlay"),
  radImgAnnot: byId("rad-img-annot"),
  btnRadPpfdCsv: byId("btn-rad-ppfd-csv"),
  btnRadAssembly: byId("btn-rad-assembly"),
  radCostCard: byId("rad-cost-card"),
  radCostTitle: byId("rad-cost-title"),
  radCostTotal: byId("rad-cost-total"),
  radCostSubtitle: byId("rad-cost-subtitle"),
  radCostSummary: byId("rad-cost-summary"),
  radCostBreakdown: byId("rad-cost-breakdown"),
  radProgress: byId("rad-progress"),
  radProgressFill: byId("rad-progress-fill"),
  radProgressLabel: byId("rad-progress-label"),
  radProgressText: byId("rad-progress-text"),
  btnRadAll: byId("btn-rad-all"),
  btnRadScatter: byId("btn-rad-scatter"),
  btnRadMetrics: byId("btn-rad-metrics"),
  btnRadManifest: byId("btn-rad-manifest"),
  btnAboutOpen: /** @type {HTMLButtonElement} */ (document.querySelector("[data-about-open]")),
  btnDemoGuideManual: byId("about-demo-guide"),
  btnRadExplainMetrics: byId("btn-rad-explain-metrics"),
  btnRadElectricalCost: byId("btn-rad-electrical-cost"),
  modal: byId("media-modal"),
  modalImage: byId("modal-image"),
  modalFrame: byId("modal-frame"),
  modalText: byId("modal-text"),
  modalTitle: byId("modal-title"),
  modalClose: byId("modal-close"),
  metricsModal: byId("metrics-modal"),
  metricsModalClose: byId("metrics-modal-close"),
  aboutModal: byId("about-modal"),
  aboutModalClose: byId("about-modal-close"),
  aboutModalCancel: byId("about-modal-cancel"),
  electricalModal: byId("electrical-modal"),
  electricalModalClose: byId("electrical-modal-close"),
  missingBundleModal: byId("missing-bundle-modal"),
  missingBundleTitle: byId("missing-bundle-title"),
  missingBundleMessage: byId("missing-bundle-message"),
  missingBundleRequest: byId("missing-bundle-request"),
  missingBundleSize: byId("missing-bundle-size"),
  missingBundleCommand: byId("missing-bundle-command"),
  missingBundleClose: byId("missing-bundle-close"),
  missingBundleCancel: byId("missing-bundle-cancel"),
  missingBundleCopy: byId("missing-bundle-copy"),
  missingBundleDemo: byId("missing-bundle-demo"),
  liveRuntimeModal: byId("live-runtime-modal"),
  liveRuntimeEyebrow: byId("live-runtime-eyebrow"),
  liveRuntimeTitle: byId("live-runtime-title"),
  liveRuntimeMessage: byId("live-runtime-message"),
  liveRuntimeFacts: byId("live-runtime-facts"),
  liveRuntimeDiagnostics: byId("live-runtime-diagnostics"),
  liveRuntimeNote: byId("live-runtime-note"),
  liveRuntimeCommands: byId("live-runtime-commands"),
  liveRuntimeClose: byId("live-runtime-close"),
  liveRuntimePrecomputed: byId("live-runtime-precomputed"),
  liveRuntimeRecheck: byId("live-runtime-recheck"),
  liveRuntimeCancel: byId("live-runtime-cancel"),
  electricalForm: byId("electrical-form"),
  btnElecCalculate: byId("btn-elec-calculate"),
  elecRate: byId("elec-rate"),
  elecPropDays: byId("elec-prop-days"),
  elecPropHours: byId("elec-prop-hours"),
  elecPropPpfd: byId("elec-prop-ppfd"),
  elecVegDays: byId("elec-veg-days"),
  elecVegHours: byId("elec-veg-hours"),
  elecVegPpfd: byId("elec-veg-ppfd"),
  elecFlowerDays: byId("elec-flower-days"),
  elecFlowerHours: byId("elec-flower-hours"),
  elecFlowerPpfd: byId("elec-flower-ppfd"),
  elecTotalCost: byId("elec-total-cost"),
  elecTotalMeta: byId("elec-total-meta"),
  elecSummary: byId("elec-summary"),
  elecStageResults: byId("elec-stage-results"),
  elecNotes: byId("elec-notes"),
});
