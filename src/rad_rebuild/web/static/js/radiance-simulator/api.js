import {
  appState,
  backendDefault,
  healthTimeoutMs,
  radianceSessionId,
} from "./state.js";
import { setStatusNote } from "./renderers.js";

export async function waitForHealth(url) {
  const deadline = Date.now() + healthTimeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${url}/health`);
      if (res.ok) {
        return true;
      }
    } catch (_err) {
      // Retry until the backend is ready.
    }
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
  return false;
}

export async function ensureBackend() {
  appState.backendUrl = backendDefault;
  try {
    if (!(await waitForHealth(appState.backendUrl))) {
      throw new Error("Backend did not become healthy.");
    }
    setStatusNote("Connected to the simulation backend. Cached layouts will be used automatically when available.", "success");
    return appState.backendUrl;
  } catch (_err) {
    appState.backendUrl = null;
    setStatusNote("Simulation backend unavailable. Try again in a moment or check the server logs.", "error");
    return null;
  }
}

export async function apiFetch(path, options) {
  const joiner = path.includes("?") ? "&" : "?";
  const tokenParam = appState.currentArtifactToken
    ? `&artifact_token=${encodeURIComponent(appState.currentArtifactToken)}`
    : "";
  const sessionPath = `${path}${joiner}session_id=${encodeURIComponent(radianceSessionId)}${tokenParam}`;
  if (!appState.backendUrl) {
    await ensureBackend();
  }
  if (!appState.backendUrl) {
    throw new Error("Backend unavailable.");
  }
  let res;
  try {
    res = await fetch(`${appState.backendUrl}${sessionPath}`, options);
  } catch (_err) {
    appState.backendUrl = null;
    await ensureBackend();
    if (!appState.backendUrl) {
      throw new Error("Backend unavailable.");
    }
    res = await fetch(`${appState.backendUrl}${sessionPath}`, options);
  }
  if (!res.ok) {
    const contentType = res.headers.get("content-type") || "";
    let payload = null;
    let text = "";
    if (contentType.includes("application/json")) {
      try {
        payload = await res.json();
      } catch (_err) {
        payload = null;
      }
    } else {
      text = await res.text();
    }
    const detail = payload?.detail ?? payload ?? text;
    const message = typeof detail === "string" ? detail : (detail?.message || res.statusText);
    const err = /** @type {Error & {status?: number, detail?: unknown}} */ (new Error(message || res.statusText));
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  return res;
}

export async function fetchRuntimeStatus() {
  const res = await apiFetch("/radiance/runtime/status");
  const data = await res.json();
  appState.runtimeStatus = data;
  return data;
}
