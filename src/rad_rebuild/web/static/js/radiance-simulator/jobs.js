import { apiFetch } from "./api.js";
import {
  buildPpfdCsvDownloadUrl,
  refreshRadianceImages,
  refreshRadianceMetrics,
} from "./artifacts.js";
import {
  parsePayload,
  radiancePayload,
  runKeyForPayload,
} from "./forms.js";
import {
  appendOutput,
  finishRadianceProgress,
  openMissingBundleModal,
  setRadianceButtonsEnabled,
  setRadianceProgress,
  setStatusNote,
  startRadianceProgress,
  syncAssemblyButtonState,
  syncElectricalEstimateButtonState,
  syncPpfdCsvButtonState,
} from "./renderers.js";
import { ensureLiveRuntimeReady } from "./runtime-preflight.js";
import { appState, els } from "./state.js";

function clearCompletedRunState() {
  appState.lastCompletedRunKey = "";
  appState.lastCompletedCsvHref = "";
  appState.currentArtifactToken = "";
  syncElectricalEstimateButtonState();
  syncPpfdCsvButtonState();
  syncAssemblyButtonState();
}

async function pollJobUntilDone(jobId, timeoutMs = 120000, intervalMs = 1500) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}`);
      const data = await res.json();
      if (data?.status && data.status !== "running") {
        return data.status;
      }
    } catch (_err) {
      // Keep retrying until timeout; transient backend/proxy blips should not fail the job UI immediately.
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return "failed";
}

async function streamJobLogs(jobId, outputEl, onDone) {
  appendOutput(outputEl, "Streaming logs...");
  let cursor = 0;
  let finalized = false;

  const finalize = async (status) => {
    if (finalized) {
      return status;
    }
    finalized = true;
    appendOutput(outputEl, `Job ${status}.`);
    if (onDone) {
      await onDone(status);
    }
    return status;
  };

  while (!finalized) {
    try {
      const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}/tail?cursor=${encodeURIComponent(cursor)}&limit=250`);
      const data = await res.json();
      const lines = Array.isArray(data?.lines) ? data.lines : [];
      for (const line of lines) {
        if (line) {
          appendOutput(outputEl, line);
        }
      }
      if (typeof data?.next_cursor === "number") {
        cursor = data.next_cursor;
      } else {
        cursor += lines.length;
      }
      if (data?.done) {
        return await finalize(data?.status || "completed");
      }
      if (data?.status && data.status !== "running" && lines.length === 0) {
        return await finalize(data.status);
      }
    } catch (_err) {
      const status = await pollJobUntilDone(jobId);
      return await finalize(status);
    }
    await new Promise((resolve) => setTimeout(resolve, 900));
  }
  return "failed";
}

export async function runRadiance(action) {
  const launchedPayload = parsePayload();
  if (!(await ensureLiveRuntimeReady({ executionMode: launchedPayload.executionMode }))) {
    return;
  }
  const requestBody = radiancePayload(action);
  clearCompletedRunState();
  startRadianceProgress("Starting simulation...");
  appendOutput(els.radLog, `Starting ${action}...`);
  appendOutput(
    els.radLog,
    requestBody.plants_enabled
      ? `FSPM plants enabled: seed=${requestBody.plant_seed ?? "default"} rows=${requestBody.plant_rows ?? "default"} columns=${requestBody.plant_columns ?? "default"} leaves=${requestBody.plant_leaf_count ?? "default"}`
      : "FSPM plants disabled for this run.",
  );
  setRadianceButtonsEnabled(false);
  try {
    const res = await apiFetch("/radiance/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    const result = await res.json();
    if (result.artifact_token) {
      appState.currentArtifactToken = result.artifact_token;
    }
    if (result.job_id) {
      await streamJobLogs(result.job_id, els.radLog, async (status) => {
        if (status === "succeeded" || status === "completed") {
          setRadianceProgress(96, "Loading outputs...");
          if (els.radProgressText) {
            els.radProgressText.textContent = "Loading the latest visuals and metrics...";
          }
          await Promise.all([
            refreshRadianceImages(true),
            refreshRadianceMetrics(true),
          ]);
          appState.lastCompletedRunKey = runKeyForPayload(launchedPayload);
          appState.lastCompletedCsvHref = buildPpfdCsvDownloadUrl(launchedPayload);
          syncElectricalEstimateButtonState();
          syncPpfdCsvButtonState();
          syncAssemblyButtonState();
          finishRadianceProgress(status);
          setStatusNote("Simulation complete. The visual outputs and metrics have been refreshed. Load Manifest is available on demand.", "success");
        } else {
          clearCompletedRunState();
          finishRadianceProgress(status);
          setStatusNote("Simulation failed. Check the run log for details.", "error");
        }
        setRadianceButtonsEnabled(true);
      });
    } else {
      clearCompletedRunState();
      finishRadianceProgress("failed");
      setRadianceButtonsEnabled(true);
      setStatusNote("Simulation failed to start.", "error");
    }
  } catch (err) {
    clearCompletedRunState();
    appendOutput(els.radLog, `Error: ${err?.message || err}`);
    finishRadianceProgress("failed");
    setRadianceButtonsEnabled(true);
    if (err?.detail?.error === "precomputed_bundle_missing" || err?.detail?.error === "precomputed_dimension_unsupported") {
      openMissingBundleModal(err.detail);
      setStatusNote("That precomputed bundle is not installed locally.", "error");
    } else {
      setStatusNote("Simulation request failed. Check the run log for details.", "error");
    }
  }
}
