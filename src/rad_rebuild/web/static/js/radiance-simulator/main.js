import { ensureBackend } from "./api.js";
import { initAboutActions } from "./about.js";
import { initDemoGuide } from "./demo-guide.js";
import {
  estimateElectricalCost,
  generateManifest,
  openImageModal,
  openScatter,
  refreshRadianceImages,
  refreshRadianceMetrics,
} from "./artifacts.js";
import { openAssemblyViewer } from "./assembly.js";
import {
  invalidateRenderedRunState,
  syncDimensionWarnings,
  syncFspmControls,
  syncModeControls,
} from "./forms.js";
import { runRadiance } from "./jobs.js";
import {
  closeElectricalModal,
  closeLiveRuntimeModal,
  closeMissingBundleModal,
  closeMetricsGuide,
  closeModal,
  closeTopDialog,
  copyMissingBundleCommand,
  openElectricalModal,
  openMetricsGuide,
  resetCostEstimate,
  resetElectricalEstimate,
  setStatusNote,
  syncAssemblyButtonState,
  syncElectricalEstimateButtonState,
  syncPpfdCsvButtonState,
  trapDialogFocus,
} from "./renderers.js";
import {
  ensureLiveRuntimeReady,
  recheckPendingLiveRuntime,
  switchLiveRuntimeModalToPrecomputed,
} from "./runtime-preflight.js";
import { appState, els } from "./state.js";

function clearRenderedOutputs() {
  els.radMetrics.textContent = "Run a simulation to load metrics.";
  resetCostEstimate();
  appState.currentManifestText = "Run a simulation to load the manifest.";
}

function invalidateRenderedRunAndSyncActions() {
  invalidateRenderedRunState();
  syncElectricalEstimateButtonState();
  syncPpfdCsvButtonState();
  syncAssemblyButtonState();
}

async function boot() {
  if (els.radPeakCapping) {
    els.radPeakCapping.checked = false;
  }
  syncModeControls();
  syncFspmControls();
  await ensureBackend();
  syncPpfdCsvButtonState();
  if (appState.backendUrl) {
    await refreshRadianceImages(false);
  }
}

export function initRadianceSimulator() {
  initAboutActions();
  initDemoGuide();
  els.btnRadAll.addEventListener("click", () => runRadiance("all"));
  els.btnRadScatter.addEventListener("click", openScatter);
  els.btnRadMetrics.addEventListener("click", () => refreshRadianceMetrics(true));
  els.btnRadManifest.addEventListener("click", () => generateManifest(true, true));
  if (els.btnRadAssembly) {
    els.btnRadAssembly.addEventListener("click", openAssemblyViewer);
  }
  if (els.btnRadElectricalCost) {
    els.btnRadElectricalCost.addEventListener("click", () => {
      openElectricalModal();
      if (els.electricalModal && !els.electricalModal.classList.contains("hidden")) {
        resetElectricalEstimate();
      }
    });
  }
  if (els.btnRadPpfdCsv) {
    els.btnRadPpfdCsv.addEventListener("click", (event) => {
      if (!appState.lastCompletedCsvHref) {
        event.preventDefault();
        setStatusNote("Run a simulation first, then download the latest PPFD CSV here.", "error");
        return;
      }
      els.btnRadPpfdCsv.href = appState.lastCompletedCsvHref;
    });
  }
  if (els.radLength) {
    els.radLength.addEventListener("input", () => {
      invalidateRenderedRunAndSyncActions();
      syncDimensionWarnings();
    });
    els.radLength.addEventListener("blur", () => {
      invalidateRenderedRunAndSyncActions();
      syncDimensionWarnings();
    });
  }
  if (els.radWidth) {
    els.radWidth.addEventListener("input", () => {
      invalidateRenderedRunAndSyncActions();
      syncDimensionWarnings();
    });
    els.radWidth.addEventListener("blur", () => {
      invalidateRenderedRunAndSyncActions();
      syncDimensionWarnings();
    });
  }
  if (els.radTarget) {
    els.radTarget.addEventListener("input", () => {
      invalidateRenderedRunAndSyncActions();
    });
    els.radTarget.addEventListener("blur", () => {
      invalidateRenderedRunAndSyncActions();
    });
  }
  if (els.radPeakCapping) {
    els.radPeakCapping.addEventListener("change", () => {
      invalidateRenderedRunAndSyncActions();
    });
  }
  document.querySelectorAll("[data-fspm-control]").forEach((control) => {
    control.addEventListener("input", () => {
      invalidateRenderedRunAndSyncActions();
    });
    control.addEventListener("change", () => {
      syncModeControls();
      invalidateRenderedRunAndSyncActions();
    });
  });
  if (els.btnRadExplainMetrics) {
    els.btnRadExplainMetrics.addEventListener("click", openMetricsGuide);
  }
  els.radMode.addEventListener("change", async () => {
    syncModeControls({ resetMountHeight: true });
    await ensureLiveRuntimeReady();
    invalidateRenderedRunAndSyncActions();
    await refreshRadianceImages(false);
    clearRenderedOutputs();
  });
  if (els.radSimMode) {
    els.radSimMode.addEventListener("change", async () => {
      const selectedExecutionMode = els.radSimMode.value;
      syncModeControls();
      syncFspmControls();
      invalidateRenderedRunAndSyncActions();

      const ready = await ensureLiveRuntimeReady({ executionMode: selectedExecutionMode });
      syncModeControls();
      syncFspmControls();
      invalidateRenderedRunAndSyncActions();

      if (!ready) {
        await refreshRadianceImages(false);
        clearRenderedOutputs();
        return;
      }

      await refreshRadianceImages(false);
      clearRenderedOutputs();
      const selectedMode = els.radSimMode?.selectedOptions?.[0]?.textContent || "Precomputed";
      setStatusNote(`${selectedMode} mode is active.`);
    });
  }
  if (els.radQualityPreset) {
    els.radQualityPreset.addEventListener("change", async () => {
      syncModeControls();
      invalidateRenderedRunAndSyncActions();
      await refreshRadianceImages(false);
      clearRenderedOutputs();
    });
  }
  if (els.radBasisBackend) {
    els.radBasisBackend.addEventListener("change", async () => {
      syncModeControls();
      invalidateRenderedRunAndSyncActions();
      await refreshRadianceImages(false);
      clearRenderedOutputs();
    });
  }
  if (els.radHpsCoverage) {
    els.radHpsCoverage.addEventListener("change", async () => {
      invalidateRenderedRunAndSyncActions();
      await refreshRadianceImages(false);
    });
  }
  if (els.radLedLayout) {
    els.radLedLayout.addEventListener("change", async () => {
      syncModeControls();
      invalidateRenderedRunAndSyncActions();
      await refreshRadianceImages(false);
      clearRenderedOutputs();
    });
  }
  if (els.radMountHeight) {
    els.radMountHeight.addEventListener("change", async () => {
      syncModeControls();
      invalidateRenderedRunAndSyncActions();
      await refreshRadianceImages(false);
      clearRenderedOutputs();
    });
  }

  if (els.modalClose) {
    els.modalClose.addEventListener("click", closeModal);
  }
  if (els.modal) {
    els.modal.addEventListener("click", (event) => {
      if (event.target === els.modal) {
        closeModal();
      }
    });
  }
  if (els.metricsModalClose) {
    els.metricsModalClose.addEventListener("click", closeMetricsGuide);
  }
  if (els.metricsModal) {
    els.metricsModal.addEventListener("click", (event) => {
      if (event.target === els.metricsModal) {
        closeMetricsGuide();
      }
    });
  }
  if (els.electricalModalClose) {
    els.electricalModalClose.addEventListener("click", closeElectricalModal);
  }
  if (els.electricalModal) {
    els.electricalModal.addEventListener("click", (event) => {
      if (event.target === els.electricalModal) {
        closeElectricalModal();
      }
    });
  }
  if (els.missingBundleClose) {
    els.missingBundleClose.addEventListener("click", closeMissingBundleModal);
  }
  if (els.missingBundleCancel) {
    els.missingBundleCancel.addEventListener("click", closeMissingBundleModal);
  }
  if (els.missingBundleCopy) {
    els.missingBundleCopy.addEventListener("click", copyMissingBundleCommand);
  }
  if (els.missingBundleDemo) {
    els.missingBundleDemo.addEventListener("click", () => {
      if (els.radLength) {
        els.radLength.value = "10";
      }
      if (els.radWidth) {
        els.radWidth.value = "10";
      }
      closeMissingBundleModal();
      syncDimensionWarnings();
      syncElectricalEstimateButtonState();
      syncPpfdCsvButtonState();
      syncAssemblyButtonState();
      runRadiance("all");
    });
  }
  if (els.missingBundleModal) {
    els.missingBundleModal.addEventListener("click", (event) => {
      if (event.target === els.missingBundleModal) {
        closeMissingBundleModal();
      }
    });
  }
  if (els.liveRuntimeClose) {
    els.liveRuntimeClose.addEventListener("click", closeLiveRuntimeModal);
  }
  if (els.liveRuntimeCancel) {
    els.liveRuntimeCancel.addEventListener("click", closeLiveRuntimeModal);
  }
  if (els.liveRuntimePrecomputed) {
    els.liveRuntimePrecomputed.addEventListener("click", switchLiveRuntimeModalToPrecomputed);
  }
  if (els.liveRuntimeRecheck) {
    els.liveRuntimeRecheck.addEventListener("click", recheckPendingLiveRuntime);
  }
  if (els.liveRuntimeModal) {
    els.liveRuntimeModal.addEventListener("click", (event) => {
      if (event.target === els.liveRuntimeModal) {
        closeLiveRuntimeModal();
      }
    });
  }
  if (els.electricalForm) {
    els.electricalForm.addEventListener("submit", (event) => {
      event.preventDefault();
      estimateElectricalCost();
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      trapDialogFocus(event);
      return;
    }
    if (event.key === "Escape" && closeTopDialog()) {
      event.preventDefault();
    }
  });
  if (els.radImgOverlay) {
    els.radImgOverlay.addEventListener("click", () => openImageModal(els.radImgOverlay));
  }
  if (els.radImgAnnot) {
    els.radImgAnnot.addEventListener("click", () => openImageModal(els.radImgAnnot));
  }

  syncModeControls({ resetMountHeight: true });
  resetCostEstimate();
  syncElectricalEstimateButtonState();
  syncPpfdCsvButtonState();
  syncAssemblyButtonState();
  boot();
}
