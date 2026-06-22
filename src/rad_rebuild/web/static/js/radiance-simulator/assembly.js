import { apiFetch } from "./api.js";
import { artifactQueryParams } from "./artifacts.js";
import { parsePayload } from "./forms.js";
import { appendOutput, openModal, setStatusNote, syncAssemblyButtonState } from "./renderers.js";
import { appState, els, radianceSessionId } from "./state.js";

const ASSEMBLY_SCENE_API_PATH = "/radiance/assembly-scene";
const ASSEMBLY_SCENE_PROXY_PATH = "/radiance-api/radiance/assembly-scene";

function buildAssemblySceneBackendPath(payload) {
  const params = artifactQueryParams(payload);
  return `${ASSEMBLY_SCENE_API_PATH}?${params.toString()}`;
}

function buildAssemblySceneViewerPath(payload) {
  const params = artifactQueryParams(payload);
  params.set("session_id", radianceSessionId);
  return `${ASSEMBLY_SCENE_PROXY_PATH}?${params.toString()}`;
}

export async function openAssemblyViewer() {
  syncAssemblyButtonState();
  if (!els.btnRadAssembly || els.btnRadAssembly.disabled) {
    setStatusNote("Run a matching lighting simulation before opening the 3D assembly view.", "error");
    return;
  }

  const priorLabel = els.btnRadAssembly.textContent || "View 3D Assembly";
  els.btnRadAssembly.disabled = true;
  els.btnRadAssembly.textContent = "Loading 3D Assembly...";
  openModal({ title: "3D Assembly", text: "Checking assembly scene availability..." });

  try {
    const payload = parsePayload();
    const backendPath = buildAssemblySceneBackendPath(payload);
    await apiFetch(backendPath, { method: "HEAD", cache: "no-store" });
    const scenePath = buildAssemblySceneViewerPath(payload);
    const frameSrc = `/viewer/assembly?scene=${encodeURIComponent(scenePath)}`;
    openModal({ title: "3D Assembly", frameSrc, fullscreen: true });
  } catch (err) {
    appendOutput(els.radLog, `Assembly viewer load error: ${err}`);
    openModal({ title: "3D Assembly", text: `Unable to load the 3D assembly scene.\n\n${err}` });
  } finally {
    els.btnRadAssembly.textContent = priorLabel;
    syncAssemblyButtonState();
    if (!appState.lastCompletedRunKey) {
      els.btnRadAssembly.disabled = true;
    }
  }
}
