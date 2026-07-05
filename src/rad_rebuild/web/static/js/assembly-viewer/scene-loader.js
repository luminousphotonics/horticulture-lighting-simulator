// @ts-check

// @ts-ignore Static vendor module is served by Flask.
import { GLTFLoader } from "/static/vendor/three/loaders/GLTFLoader.js";

const ALLOWED_SCENE_PREFIX = "/radiance-api/radiance/assembly-scene?";
const PLACEHOLDER_ASSET_KEY = "placeholder";
const LOD_LEVELS = ["high", "medium", "proxy"];
const INTERACTION_LOD_LEVELS = ["proxy", "medium", "high"];
const MODULE_NODE_PATTERN = /^module_(\d+)(?:_|$)/i;
const DIRECT_PLACEMENT_STRATEGY = "single_fixture_center";
const EXPECTED_DIRECT_MOUNT_ORIENTATION = "leds_down";

export function sceneUrlFromQuery(queryString) {
  const params = new URLSearchParams(queryString);
  const scene = params.get("scene") || "";
  if (!scene.startsWith(ALLOWED_SCENE_PREFIX)) {
    throw new Error("The assembly scene URL is missing or not allowed.");
  }
  return scene;
}

export function fspmCsvUrlFromSceneUrl(sceneUrl) {
  if (!sceneUrl.startsWith(ALLOWED_SCENE_PREFIX)) {
    throw new Error("The FSPM export URL cannot be derived from this scene URL.");
  }
  const parsed = new URL(sceneUrl, window.location.origin);
  if (parsed.origin !== window.location.origin) {
    throw new Error("The FSPM export URL must use the viewer origin.");
  }
  if (parsed.pathname !== "/radiance-api/radiance/assembly-scene") {
    throw new Error("The FSPM export URL must be derived from the assembly scene route.");
  }
  parsed.pathname = "/radiance-api/radiance/fspm-csv";
  return `${parsed.pathname}${parsed.search}`;
}

async function readJson(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return await response.json();
  }
  return null;
}

export async function loadAssemblyScene(queryString) {
  const response = await fetch(sceneUrlFromQuery(queryString), { cache: "no-store" });
  const payload = await readJson(response);
  if (!response.ok) {
    const detail = payload?.detail || payload || {};
    throw new Error(detail.message || payload?.message || response.statusText || "Scene request failed.");
  }
  if (!payload || typeof payload !== "object") {
    throw new Error("Scene response was empty.");
  }
  return payload;
}

function assetDefinitions(scene) {
  if (scene?.module_assets && typeof scene.module_assets === "object") {
    return scene.module_assets;
  }
  if (scene?.fixture_assets && typeof scene.fixture_assets === "object") {
    return scene.fixture_assets;
  }
  const fixtureAsset = scene?.assets?.fixture;
  return fixtureAsset && typeof fixtureAsset === "object" ? { fixture: fixtureAsset } : {};
}

function moduleAsset(scene, assetKey) {
  const asset = assetDefinitions(scene)[assetKey];
  return asset && typeof asset === "object" ? asset : null;
}

function lodAssetUrl(scene, assetKey, lodLevel) {
  const asset = moduleAsset(scene, assetKey);
  if (!asset) {
    return null;
  }
  const value = asset[lodLevel];
  return typeof value === "string" && value ? value : null;
}

export function effectiveAssetKey(instance) {
  const fallback = typeof instance?.asset_fallback_key === "string" ? instance.asset_fallback_key : "";
  const assetKey = typeof instance?.asset_key === "string" ? instance.asset_key : "";
  return fallback || assetKey || PLACEHOLDER_ASSET_KEY;
}

function fixtureAssetKeys(scene) {
  const instances = Array.isArray(scene?.instances) ? scene.instances : [];
  const keys = new Set();
  for (const instance of instances) {
    const assetKey = effectiveAssetKey(instance);
    if (assetKey && assetKey !== PLACEHOLDER_ASSET_KEY) {
      keys.add(assetKey);
    }
  }
  if (keys.has("l4_corner") || keys.has("l4_reverse_corner")) {
    for (const candidate of ["l4_corner", "l4_reverse_corner"]) {
      if (lodAssetUrl(scene, candidate, "high")) {
        keys.add(candidate);
      }
    }
  }
  return Array.from(keys).sort();
}

function sameOriginPath(url) {
  const parsed = new URL(url, window.location.origin);
  if (parsed.origin !== window.location.origin) {
    throw new Error(`Viewer metadata URL is not same-origin: ${url}`);
  }
  return `${parsed.pathname}${parsed.search}`;
}

function resolveSiblingUrl(basePath, relativePath) {
  const parsed = new URL(relativePath, new URL(basePath, window.location.origin));
  if (parsed.origin !== window.location.origin) {
    throw new Error(`Viewer metadata URL is not same-origin: ${relativePath}`);
  }
  return `${parsed.pathname}${parsed.search}`;
}

async function fetchJson(url, warnings, label) {
  try {
    const response = await fetch(sameOriginPath(url), { cache: "no-store" });
    const payload = await readJson(response);
    if (!response.ok || !payload || typeof payload !== "object") {
      warnings.push(`Unable to load ${label} metadata from ${url}.`);
      return null;
    }
    return payload;
  } catch (err) {
    warnings.push(`Unable to load ${label} metadata from ${url}: ${err instanceof Error ? err.message : String(err)}`);
    return null;
  }
}

function isDirectFixtureScene(scene) {
  return scene?.placement_strategy === DIRECT_PLACEMENT_STRATEGY;
}

function moduleNumber(name) {
  const match = String(name || "").match(MODULE_NODE_PATTERN);
  return match ? Number.parseInt(match[1], 10) : Number.MAX_SAFE_INTEGER;
}

function normalizeAnchors(payload) {
  const anchorsM = payload?.anchors_m && typeof payload.anchors_m === "object" ? payload.anchors_m : {};
  const anchorsByAssetKey = new Map();
  for (const [assetKey, anchors] of Object.entries(anchorsM)) {
    if (!Array.isArray(anchors)) {
      continue;
    }
    const normalized = anchors
      .filter((anchor) => anchor && typeof anchor === "object")
      .map((anchor) => ({
        name: String(anchor.name || ""),
        x: Number(anchor.x),
        z: Number(anchor.z),
        vertical_y: Number(anchor.vertical_y),
      }))
      .filter((anchor) => Number.isFinite(anchor.x) && Number.isFinite(anchor.z) && Number.isFinite(anchor.vertical_y))
      .sort((left, right) => moduleNumber(left.name) - moduleNumber(right.name));
    if (normalized.length) {
      anchorsByAssetKey.set(assetKey, normalized);
    }
  }
  return anchorsByAssetKey;
}

async function loadAnchorMetadata(scene, warnings) {
  if (scene?.placement_strategy && scene.placement_strategy !== "anchor_fit") {
    return new Map();
  }
  let anchorsUrl = typeof scene?.assets?.anchors === "string" ? scene.assets.anchors : "";
  const manifestUrl = typeof scene?.assets?.manifest === "string" ? scene.assets.manifest : "";
  if (!anchorsUrl && manifestUrl) {
    const manifest = await fetchJson(manifestUrl, warnings, "viewer manifest");
    const anchorMetadata = manifest?.anchor_metadata;
    if (typeof anchorMetadata === "string" && anchorMetadata) {
      anchorsUrl = resolveSiblingUrl(manifestUrl, anchorMetadata);
    }
  }
  if (!anchorsUrl) {
    warnings.push("No static anchor metadata URL is defined; GLB node-name fallback may be used in development.");
    return new Map();
  }
  const payload = await fetchJson(anchorsUrl, warnings, "anchor");
  return payload ? normalizeAnchors(payload) : new Map();
}

function validViewerScale(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

function axisMappingLooksLikeDirectFixture(mapping) {
  return (
    mapping
    && typeof mapping === "object"
    && Array.isArray(mapping.cad_horizontal)
    && mapping.cad_horizontal.join(",") === "x,z"
    && mapping.cad_vertical === "y"
    && Array.isArray(mapping.layout_horizontal)
    && mapping.layout_horizontal.join(",") === "x,y"
    && mapping.layout_vertical === "z"
  );
}

async function loadDirectPlacementMetadata(scene, warnings) {
  if (!isDirectFixtureScene(scene)) {
    return null;
  }
  const manifestUrl = typeof scene?.assets?.manifest === "string" ? scene.assets.manifest : "";
  const manifest = manifestUrl ? await fetchJson(manifestUrl, warnings, "viewer manifest") : null;
  const mountOrientation = String(manifest?.mount_orientation || scene?.mount_orientation || "");
  const axisMapping = manifest?.axis_mapping || scene?.axis_mapping || null;
  const viewerScale = validViewerScale(manifest?.viewer_scale) || validViewerScale(scene?.viewer_scale);

  if (!manifestUrl) {
    warnings.push("No viewer manifest URL is defined; direct fixture orientation metadata is unavailable.");
  }
  if (!mountOrientation) {
    warnings.push("Direct fixture manifest is missing mount_orientation; using identity orientation.");
  } else if (mountOrientation !== EXPECTED_DIRECT_MOUNT_ORIENTATION) {
    warnings.push(`Direct fixture mount orientation "${mountOrientation}" is not recognized; using identity orientation.`);
  }
  if (!axisMappingLooksLikeDirectFixture(axisMapping)) {
    warnings.push("Direct fixture axis mapping is missing or unsupported; using scene x/y/z placement semantics.");
  }
  if (!viewerScale) {
    warnings.push("Direct fixture viewer scale is missing or invalid; using renderer default scale.");
  }

  return {
    axisMapping,
    mountOrientation,
    viewerScale,
  };
}

async function loadGltf(loader, url) {
  const gltf = await loader.loadAsync(url);
  return gltf.scene;
}

export function bestInteractionLodLevel(bundle) {
  for (const level of INTERACTION_LOD_LEVELS) {
    if (bundle?.levels?.has(level)) {
      return level;
    }
  }
  return "high";
}

export async function loadFixtureAssets(scene) {
  const loader = new GLTFLoader();
  const assetBundles = new Map();
  const warnings = [];
  const anchorsByAssetKey = await loadAnchorMetadata(scene, warnings);
  const directPlacementMetadata = await loadDirectPlacementMetadata(scene, warnings);
  for (const assetKey of fixtureAssetKeys(scene)) {
    const levels = new Map();
    for (const lodLevel of LOD_LEVELS) {
      const url = lodAssetUrl(scene, assetKey, lodLevel);
      if (!url) {
        warnings.push(`No ${lodLevel} GLB URL is defined for fixture asset key "${assetKey}".`);
        continue;
      }
      try {
        levels.set(lodLevel, await loadGltf(loader, sameOriginPath(url)));
      } catch (err) {
        const fallbackText = lodLevel === "high" ? "fixture will render as a placeholder" : "falling back to the next available LOD";
        warnings.push(
          `Unable to load ${lodLevel} LOD for fixture asset "${assetKey}" from ${url}; ${fallbackText}: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }
    }
    if (!levels.has("high")) {
      continue;
    }
    assetBundles.set(assetKey, {
      assetKey,
      levels,
      anchors: anchorsByAssetKey.get(assetKey) || [],
      axisMapping: directPlacementMetadata?.axisMapping || scene?.axis_mapping || null,
      mountOrientation: directPlacementMetadata?.mountOrientation || scene?.mount_orientation || null,
      viewerScale: directPlacementMetadata?.viewerScale || validViewerScale(scene?.viewer_scale),
      interactionLod: bestInteractionLodLevel({ levels }),
    });
  }
  return { assetBundles, warnings };
}
