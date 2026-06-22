import { fetchRuntimeStatus } from "./api.js";
import { syncModeControls } from "./forms.js";
import {
  closeLiveRuntimeModal,
  openRuntimeSetupModal,
  openUnsupportedLiveModeModal,
  setStatusNote,
} from "./renderers.js";
import { appState, els, showLiveModes } from "./state.js";

const PRECOMPUTED_MODE = "precomputed";
const LIVE_MODES = new Set(["live_docker", "live_local"]);

function runtimeFor(status, executionMode) {
  return status?.modes?.[executionMode] || null;
}

function switchToPrecomputed() {
  if (els.radSimMode) {
    els.radSimMode.value = PRECOMPUTED_MODE;
  }
  syncModeControls();
  setStatusNote("Precomputed mode is active.");
}

export function switchLiveRuntimeModalToPrecomputed() {
  switchToPrecomputed();
  closeLiveRuntimeModal();
}

function supportedLiveModes(status) {
  return new Set(Array.isArray(status?.live_supported_modes) ? status.live_supported_modes : ["SMD"]);
}

/**
 * @param {{executionMode?: string, revertOnBlock?: boolean}=} options
 */
export async function ensureLiveRuntimeReady({ executionMode, revertOnBlock = true } = {}) {
  const selectedExecutionMode = executionMode || (els.radSimMode?.value || PRECOMPUTED_MODE);
  if (!showLiveModes || !LIVE_MODES.has(selectedExecutionMode)) {
    return true;
  }
  appState.pendingLiveExecutionMode = selectedExecutionMode;

  let status;
  try {
    status = await fetchRuntimeStatus();
  } catch (_err) {
    if (revertOnBlock) {
      switchToPrecomputed();
    }
    setStatusNote("Runtime status is unavailable. Check the backend and try again.", "error");
    return false;
  }

  const selectedLightingMode = els.radMode?.value || "SMD";
  if (!supportedLiveModes(status).has(selectedLightingMode)) {
    if (revertOnBlock) {
      switchToPrecomputed();
    }
    openUnsupportedLiveModeModal({ executionMode: selectedExecutionMode, status });
    setStatusNote("Use Precomputed mode for this lighting system.", "error");
    return false;
  }

  let runtime = runtimeFor(status, selectedExecutionMode);
  if (!status?.live_execution_enabled) {
    runtime = {
      ...(runtime || {}),
      available: false,
      reason: "live_execution_disabled",
      setup_commands: [],
    };
  }
  if (!runtime?.available) {
    if (revertOnBlock) {
      switchToPrecomputed();
    }
    openRuntimeSetupModal({ executionMode: selectedExecutionMode, runtime });
    setStatusNote(`${selectedExecutionMode === "live_local" ? "Live Local Radiance" : "Live Docker"} needs setup before it can run.`, "error");
    return false;
  }

  return true;
}

export async function recheckPendingLiveRuntime() {
  const executionMode = appState.pendingLiveExecutionMode || (els.radSimMode?.value || PRECOMPUTED_MODE);
  const ready = await ensureLiveRuntimeReady({ executionMode, revertOnBlock: false });
  if (ready && LIVE_MODES.has(executionMode)) {
    if (els.radSimMode) {
      els.radSimMode.value = executionMode;
    }
    syncModeControls();
    closeLiveRuntimeModal();
    setStatusNote(`${executionMode === "live_local" ? "Live Local Radiance" : "Live Docker"} mode is active.`, "success");
  }
  return ready;
}
