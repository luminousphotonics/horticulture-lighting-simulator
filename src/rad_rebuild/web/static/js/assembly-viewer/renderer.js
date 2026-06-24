// @ts-check

// @ts-ignore Static vendor module is served by Flask.
import * as THREE from "/static/vendor/three/three.module.js";
import {
  CANOPY_Z_M,
  assemblyBoundsSummary,
  boundsCorners,
  computeAssemblySceneBounds,
  isValidAssemblyBounds,
} from "./bounds.js";
import { clampHeatmapOpacity, createHeatmapDataTexture } from "./heatmap.js";
import { createDirectFixtureInstancing, createFixtureInstancing } from "./lod.js";
import { applySystemMaterialTuning, createCadMaterials, normalizeFixtureMaterials } from "./materials.js";
import { createPlantGroup } from "./plants.js";
import { effectiveAssetKey } from "./scene-loader.js";
import { chooseBestAnchorCandidate } from "./transforms.js";

const HEATMAP_PLANE_Y_M = CANOPY_Z_M + 0.005;
const PLACEHOLDER_ASSET_KEY = "placeholder";
const LOD_LEVELS = ["high", "medium", "proxy"];
const SHADOW_BOUNDS_PADDING_M = 1.25;
const SHADOW_LIGHT_DISTANCE_MULTIPLIER = 1.8;

function roomDimension(scene, key, fallback) {
  const value = Number(scene?.room?.[key]);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function createRenderer(canvas) {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: false,
    powerPreference: "high-performance",
  });
  renderer.setClearColor(0x0d1218, 1);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  return { renderer, backend: "WebGLRenderer" };
}

function resizeRendererToDisplaySize(renderer, camera) {
  const canvas = renderer.domElement;
  const width = Math.max(1, canvas.clientWidth);
  const height = Math.max(1, canvas.clientHeight);
  if (canvas.width !== Math.floor(width * renderer.getPixelRatio()) || canvas.height !== Math.floor(height * renderer.getPixelRatio())) {
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }
}

function addRoomGeometry(world, scenePayload) {
  const materials = createCadMaterials();
  const lengthM = roomDimension(scenePayload, "length_m", 3.048);
  const widthM = roomDimension(scenePayload, "width_m", 3.048);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(lengthM, widthM), materials.floor);
  floor.name = "room-floor";
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = CANOPY_Z_M;
  floor.receiveShadow = true;
  world.scene.add(floor);

  const canopy = new THREE.Mesh(new THREE.PlaneGeometry(lengthM, widthM), materials.canopy);
  canopy.name = "canopy-plane";
  canopy.rotation.x = -Math.PI / 2;
  canopy.position.y = CANOPY_Z_M + 0.004;
  world.scene.add(canopy);

  const boundaryPoints = [
    new THREE.Vector3(-lengthM / 2, CANOPY_Z_M + 0.012, -widthM / 2),
    new THREE.Vector3(lengthM / 2, CANOPY_Z_M + 0.012, -widthM / 2),
    new THREE.Vector3(lengthM / 2, CANOPY_Z_M + 0.012, widthM / 2),
    new THREE.Vector3(-lengthM / 2, CANOPY_Z_M + 0.012, widthM / 2),
  ];
  const boundary = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(boundaryPoints), materials.boundary);
  boundary.name = "room-boundary";
  world.scene.add(boundary);

  const grid = new THREE.GridHelper(Math.max(lengthM, widthM), Math.max(4, Math.ceil(Math.max(lengthM, widthM))));
  grid.name = "technical-grid";
  grid.position.y = CANOPY_Z_M + 0.006;
  grid.material = materials.grid;
  world.scene.add(grid);
}

function addLighting(scene) {
  const ambient = new THREE.HemisphereLight(0xd7f2ff, 0x17202b, 1.9);
  scene.add(ambient);
  const key = new THREE.DirectionalLight(0xffffff, 2.4);
  key.position.set(-4, 8, 5);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  scene.add(key);
  scene.add(key.target);
  return { ambient, key };
}

function lightSpaceBoundsForBox(light, bounds) {
  light.shadow.updateMatrices(light);
  const viewMatrix = light.shadow.camera.matrixWorldInverse;
  const lightBounds = new THREE.Box3();
  lightBounds.makeEmpty();
  for (const corner of boundsCorners(bounds)) {
    lightBounds.expandByPoint(corner.applyMatrix4(viewMatrix));
  }
  return lightBounds;
}

export function configureDirectionalLightShadow(light, bounds, options = {}) {
  if (!light?.shadow?.camera || !isValidAssemblyBounds(bounds)) {
    return null;
  }
  const paddingM = Number.isFinite(Number(options.paddingM))
    ? Math.max(0, Number(options.paddingM))
    : SHADOW_BOUNDS_PADDING_M;
  const center = bounds.getCenter(new THREE.Vector3());
  const size = bounds.getSize(new THREE.Vector3());
  const radius = Math.max(1, size.length() / 2);
  const currentOffset = light.position.clone().sub(light.target.position);
  const direction = currentOffset.lengthSq() > 1.0e-9
    ? currentOffset.normalize()
    : new THREE.Vector3(-0.35, 0.8, 0.5).normalize();
  light.target.position.copy(center);
  light.position.copy(center).add(direction.multiplyScalar(Math.max(8, radius * SHADOW_LIGHT_DISTANCE_MULTIPLIER)));
  light.updateMatrixWorld(true);
  light.target.updateMatrixWorld(true);

  const lightBounds = lightSpaceBoundsForBox(light, bounds);
  if (lightBounds.isEmpty()) {
    return null;
  }
  const camera = light.shadow.camera;
  camera.left = lightBounds.min.x - paddingM;
  camera.right = lightBounds.max.x + paddingM;
  camera.bottom = lightBounds.min.y - paddingM;
  camera.top = lightBounds.max.y + paddingM;
  camera.near = Math.max(0.1, -lightBounds.max.z - paddingM);
  camera.far = Math.max(camera.near + 1, -lightBounds.min.z + paddingM);
  camera.updateProjectionMatrix();
  light.shadow.needsUpdate = true;
  return {
    left: camera.left,
    right: camera.right,
    bottom: camera.bottom,
    top: camera.top,
    near: camera.near,
    far: camera.far,
  };
}

function installDebugHelpers(world, bounds) {
  if (!debugEnabled() || !isValidAssemblyBounds(bounds)) {
    return;
  }
  const boundsHelper = new THREE.Box3Helper(bounds, 0x68d4ba);
  boundsHelper.name = "assembly-bounds-debug";
  world.scene.add(boundsHelper);
  if (world.shadowLight?.shadow?.camera) {
    const shadowHelper = new THREE.CameraHelper(world.shadowLight.shadow.camera);
    shadowHelper.name = "assembly-shadow-camera-debug";
    world.scene.add(shadowHelper);
    world.shadowCameraHelper = shadowHelper;
  }
}

function pointToWorldVector(point) {
  return new THREE.Vector3(Number(point?.x || 0), Number(point?.z || 0), Number(point?.y || 0));
}

function fixturePoints(instance) {
  return Array.isArray(instance?.points) ? instance.points : [];
}

function createPlaceholderFixture(instance, material) {
  const group = new THREE.Group();
  group.name = `placeholder-${instance?.id || "fixture"}`;
  const points = fixturePoints(instance);
  const safePoints = points.length ? points : [{ x: 0, y: 0, z: 0 }];
  const geometry = new THREE.BoxGeometry(0.08, 0.035, 0.08);
  for (const point of safePoints) {
    const marker = new THREE.Mesh(geometry, material);
    marker.position.copy(pointToWorldVector(point));
    marker.castShadow = true;
    marker.receiveShadow = true;
    group.add(marker);
  }
  if (safePoints.length > 1) {
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(safePoints.map((point) => pointToWorldVector(point))),
      new THREE.LineBasicMaterial({ color: 0xffc857 }),
    );
    group.add(line);
  }
  return group;
}

export function createAssemblyScene(canvas) {
  const { renderer, backend } = createRenderer(canvas);
  const scene = new THREE.Scene();
  scene.name = "assembly-scene";
  scene.fog = new THREE.Fog(0x0d1218, 7, 28);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 200);
  const clock = new THREE.Clock();
  let animationId = 0;
  const world = {
    renderer,
    rendererBackend: backend,
    scene,
    camera,
    instanceCount: 0,
    lodController: null,
    assemblyBounds: null,
    assemblyBoundsSummary: null,
    shadowLight: null,
    shadowCameraBounds: null,
    shadowCameraHelper: null,
    start(onBeforeRender) {
      const renderFrame = () => {
        const deltaSeconds = clock.getDelta();
        resizeRendererToDisplaySize(renderer, camera);
        onBeforeRender(deltaSeconds);
        renderer.render(scene, camera);
        animationId = window.requestAnimationFrame(renderFrame);
      };
      renderFrame();
    },
    stop() {
      if (animationId) {
        window.cancelAnimationFrame(animationId);
      }
    },
  };
  const lights = addLighting(scene);
  world.shadowLight = lights.key;
  return world;
}

function debugEnabled() {
  return new URLSearchParams(window.location.search).get("debug") === "1";
}

function bestAvailableLod(levelGroups, requestedLevel) {
  const priority = requestedLevel === "proxy"
    ? ["proxy", "medium", "high"]
    : requestedLevel === "medium"
      ? ["medium", "high"]
      : ["high"];
  for (const level of priority) {
    if (levelGroups.has(level)) {
      return level;
    }
  }
  return levelGroups.has("high") ? "high" : null;
}

function createLodController(records) {
  let interactive = false;

  function setRequestedLod(record, requestedLevel) {
    const activeLevel = bestAvailableLod(record.levelGroups, requestedLevel);
    for (const [level, group] of record.levelGroups.entries()) {
      group.visible = level === activeLevel;
    }
  }

  return {
    setInteractive(value) {
      const next = Boolean(value);
      if (next === interactive) {
        return;
      }
      interactive = next;
      for (const record of records) {
        setRequestedLod(record, interactive ? record.interactionLod : "high");
      }
    },
  };
}

function isDirectFixtureScene(scenePayload) {
  return scenePayload?.placement_strategy === "single_fixture_center";
}

function createAssetInstancing(sourceRoot, assetInstances, options) {
  return options.directFixture
    ? createDirectFixtureInstancing(sourceRoot, assetInstances, options)
    : createFixtureInstancing(sourceRoot, assetInstances, options);
}

function addAssetLods(group, assetKey, assetInstances, bundle, scenePayload, warnings, diagnostics) {
  const records = [];
  const levelGroups = new Map();
  const directFixture = isDirectFixtureScene(scenePayload);
  for (const lodLevel of LOD_LEVELS) {
    const sourceRoot = bundle.levels.get(lodLevel);
    if (!sourceRoot) {
      continue;
    }
    normalizeFixtureMaterials(sourceRoot);
    applySystemMaterialTuning(scenePayload, sourceRoot);
    const instancing = createAssetInstancing(sourceRoot, assetInstances, {
      assetKey,
      anchors: bundle.anchors,
      axisMapping: bundle.axisMapping || scenePayload.axis_mapping,
      debug: debugEnabled() && lodLevel === "high",
      directFixture,
      mountOrientation: bundle.mountOrientation || scenePayload.mount_orientation,
      viewerScale: bundle.viewerScale || scenePayload.viewer_scale,
    });
    instancing.group.name = `fixture-asset-${assetKey}-${lodLevel}`;
    instancing.group.visible = lodLevel === "high";
    group.add(instancing.group);
    levelGroups.set(lodLevel, instancing.group);
    warnings.push(...instancing.warnings.map((warning) => `${assetKey}/${lodLevel}: ${warning}`));
    if (lodLevel === "high") {
      diagnostics.push(...instancing.diagnostics);
    }
  }
  if (levelGroups.size) {
    records.push({
      assetKey,
      levelGroups,
      interactionLod: bundle.interactionLod || "high",
    });
  }
  return records;
}

function instancePoints(instance) {
  return Array.isArray(instance?.points) ? instance.points : [];
}

function pointsAreCollinear(points) {
  if (points.length < 3) {
    return true;
  }
  const first = points[0];
  let farthest = points[1];
  let farthestDistance = -1;
  for (const point of points.slice(1)) {
    const distance = Math.hypot(Number(point.x) - Number(first.x), Number(point.y) - Number(first.y));
    if (distance > farthestDistance) {
      farthestDistance = distance;
      farthest = point;
    }
  }
  if (farthestDistance <= 1.0e-9) {
    return false;
  }
  const dx = Number(farthest.x) - Number(first.x);
  const dy = Number(farthest.y) - Number(first.y);
  return points.every((point) => Math.abs(dx * (Number(point.y) - Number(first.y)) - dy * (Number(point.x) - Number(first.x))) <= 1.0e-6);
}

function resolvedAssetKeyForInstance(instance, assetBundles, warnings) {
  const assetKey = effectiveAssetKey(instance);
  const points = instancePoints(instance);
  if (points.length !== 4 || pointsAreCollinear(points)) {
    return assetKey;
  }
  const candidates = ["l4_corner", "l4_reverse_corner"]
    .filter((candidateKey) => assetBundles.has(candidateKey))
    .map((candidateKey) => ({
      assetKey: candidateKey,
      anchors: assetBundles.get(candidateKey).anchors,
    }));
  if (candidates.length < 2) {
    return assetKey;
  }
  const best = chooseBestAnchorCandidate(instance, candidates);
  if (!best?.assetKey) {
    return assetKey;
  }
  if (best.assetKey !== assetKey) {
    warnings.push(
      `${instance?.id || "fixture"} selected ${best.assetKey} over ${assetKey} from CAD anchor residual comparison.`,
    );
  }
  return best.assetKey;
}

export function buildAssemblyWorld(world, scenePayload, fixtureAssets) {
  addRoomGeometry(world, scenePayload);
  const instances = Array.isArray(scenePayload.instances) ? scenePayload.instances : [];
  const assetBundles = fixtureAssets instanceof Map ? fixtureAssets : new Map();
  const group = new THREE.Group();
  group.name = "fixture-assets";
  const warnings = [];
  const diagnostics = [];
  const lodRecords = [];
  const placeholderMaterial = new THREE.MeshStandardMaterial({
    color: 0xffc857,
    emissive: 0x332100,
    roughness: 0.65,
    metalness: 0.0,
  });
  const instancesByAssetKey = new Map();

  for (const instance of instances) {
    const assetKey = resolvedAssetKeyForInstance(instance, assetBundles, warnings);
    if (assetKey === PLACEHOLDER_ASSET_KEY || !assetBundles.has(assetKey)) {
      group.add(createPlaceholderFixture(instance, placeholderMaterial));
      warnings.push(`${instance?.id || "fixture"} uses unresolved fixture asset key "${assetKey}".`);
      continue;
    }
    if (!instancesByAssetKey.has(assetKey)) {
      instancesByAssetKey.set(assetKey, []);
    }
    instancesByAssetKey.get(assetKey).push(instance);
  }

  for (const [assetKey, assetInstances] of instancesByAssetKey.entries()) {
    const bundle = assetBundles.get(assetKey);
    lodRecords.push(...addAssetLods(group, assetKey, assetInstances, bundle, scenePayload, warnings, diagnostics));
  }

  world.instanceCount = instances.length;
  world.lodController = createLodController(lodRecords);
  world.scene.add(group);
  const plantGroup = createPlantGroup(scenePayload);
  if (plantGroup.userData.renderedLeafCount > 0) {
    world.scene.add(plantGroup);
  }
  const assemblyBounds = computeAssemblySceneBounds(world, scenePayload);
  const shadowCameraBounds = configureDirectionalLightShadow(world.shadowLight, assemblyBounds);
  world.assemblyBounds = assemblyBounds;
  world.assemblyBoundsSummary = assemblyBoundsSummary(assemblyBounds);
  world.shadowCameraBounds = shadowCameraBounds;
  installDebugHelpers(world, assemblyBounds);
  return {
    group,
    instanceCount: instances.length,
    assetKeyCount: instancesByAssetKey.size,
    lodRecordCount: lodRecords.length,
    lodController: world.lodController,
    plantGroup,
    plantCount: plantGroup.userData.plantCount || 0,
    plantLeafCount: plantGroup.userData.leafCount || 0,
    renderedPlantLeafCount: plantGroup.userData.renderedLeafCount || 0,
    diagnostics,
    warnings: [...warnings, ...(plantGroup.userData.warnings || [])],
    assemblyBounds,
    assemblyBoundsSummary: world.assemblyBoundsSummary,
    shadowCameraBounds,
  };
}

export function createPhotometricHeatmapPlane(world, layer, opacity = 0.72) {
  const metadata = layer?.metadata;
  const bounds = metadata?.bounds_m;
  const width = Number(bounds?.x_max) - Number(bounds?.x_min);
  const depth = Number(bounds?.y_max) - Number(bounds?.y_min);
  if (!world?.scene || !(layer?.values instanceof Float32Array) || !Number.isFinite(width) || width <= 0 || !Number.isFinite(depth) || depth <= 0) {
    throw new Error("Photometric heatmap layer cannot be rendered with invalid bounds or values.");
  }

  const texture = createHeatmapDataTexture(layer.values, metadata, THREE);
  const geometry = new THREE.PlaneGeometry(width, depth);
  const material = new THREE.MeshBasicMaterial({
    map: texture,
    transparent: true,
    opacity: clampHeatmapOpacity(opacity),
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = "ppfd-heatmap-plane";
  mesh.rotation.x = Math.PI / 2;
  mesh.position.set(
    (Number(bounds.x_min) + Number(bounds.x_max)) / 2,
    HEATMAP_PLANE_Y_M,
    (Number(bounds.y_min) + Number(bounds.y_max)) / 2,
  );
  mesh.visible = false;
  mesh.userData.photometricLayer = layer;
  world.scene.add(mesh);
  return { mesh, material, texture };
}
