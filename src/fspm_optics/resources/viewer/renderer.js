import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { buildNormalSegments, parseReceiverBuffer } from "./receivers.js";

export const LEAF_MATERIAL_POLICY = Object.freeze({
  frontColor: "#356B42",
  undersideColor: "#3B6848",
  undersideSaturation: 0.94,
  frontRoughness: 0.82,
  undersideRoughness: 0.84,
  metalness: 0,
  leafVariationMaximum: 0.03,
  midribContrastMaximum: 0.06,
  secondaryVeinContrastMaximum: 0.03,
  fluxVeinContrastMaximum: 0.01,
  midribRoughnessOffset: -0.018,
  secondaryVeinRoughnessOffset: -0.01,
  fluxRoughnessDetailScale: 0.35,
  normalDetailHeightM: 0.00006,
  normalDetailMaximumDegrees: 2,
  fluxNormalDetailMaximumDegrees: 0.5,
});

const LEAF_VERTEX_DECLARATIONS = `
attribute vec2 _leaf_uv;
attribute float _leaf_index;
varying vec2 vLeafUv;
varying float vLeafIndex;
`;

const LEAF_VERTEX_ASSIGNMENT = `
vLeafUv = _leaf_uv;
vLeafIndex = _leaf_index;
`;

const LEAF_FRAGMENT_DECLARATIONS = `
uniform vec3 leafFrontColor;
uniform vec3 leafUndersideColor;
uniform float leafUndersideSaturation;
uniform float leafFrontRoughness;
uniform float leafUndersideRoughness;
uniform float leafVariationMaximum;
uniform float leafMidribContrastMaximum;
uniform float leafSecondaryVeinContrastMaximum;
uniform float leafFluxVeinContrastMaximum;
uniform float leafMidribRoughnessOffset;
uniform float leafSecondaryVeinRoughnessOffset;
uniform float leafFluxRoughnessDetailScale;
uniform float leafNormalDetailHeightM;
uniform float leafNeutralNormalMaximumSlope;
uniform float leafFluxNormalMaximumSlope;
uniform float leafScientificOverlayVisible;
varying vec2 vLeafUv;
varying float vLeafIndex;

struct LeafDetail {
  float midrib;
  float secondaryVeins;
  float height;
  float neutralLuminance;
  float fluxLuminance;
  float roughnessOffset;
};

float leafVeinPair(vec2 uv, float origin, float bend) {
  float lateral = abs(uv.y - 0.5) * 2.0;
  float curve = origin + (0.20 + bend) * lateral - 0.035 * lateral * lateral;
  float distanceToCurve = abs(uv.x - curve);
  float taper = 1.0 - smoothstep(0.76, 0.98, lateral);
  float width = mix(0.010, 0.0035, lateral);
  float filterWidth = max(fwidth(distanceToCurve), 0.00035);
  return (1.0 - smoothstep(width - filterWidth, width + filterWidth, distanceToCurve))
    * taper;
}

LeafDetail evaluateLeafDetail(vec2 uv) {
  float u = clamp(uv.x, 0.0, 1.0);
  float endTaper = smoothstep(0.015, 0.09, u) * (1.0 - smoothstep(0.88, 0.995, u));
  float midribDistance = abs(uv.y - 0.5);
  float midribWidth = mix(0.014, 0.005, u);
  float midribFilter = max(fwidth(midribDistance), 0.00035);
  float midrib = (1.0 - smoothstep(
    midribWidth - midribFilter, midribWidth + midribFilter, midribDistance
  )) * endTaper;
  float secondary = 0.0;
  secondary = max(secondary, leafVeinPair(uv, 0.12, 0.025));
  secondary = max(secondary, leafVeinPair(uv, 0.24, 0.010));
  secondary = max(secondary, leafVeinPair(uv, 0.36, -0.005));
  secondary = max(secondary, leafVeinPair(uv, 0.48, -0.020));
  secondary = max(secondary, leafVeinPair(uv, 0.60, -0.035));
  secondary = max(secondary, leafVeinPair(uv, 0.72, -0.050));
  secondary *= endTaper;
  LeafDetail detail;
  detail.midrib = midrib;
  detail.secondaryVeins = secondary;
  detail.height = midrib + secondary * 0.45;
  detail.neutralLuminance = 1.0 + max(
    midrib * leafMidribContrastMaximum,
    secondary * leafSecondaryVeinContrastMaximum
  );
  detail.fluxLuminance = 1.0
    + max(midrib, secondary) * leafFluxVeinContrastMaximum;
  detail.roughnessOffset = midrib * leafMidribRoughnessOffset
    + secondary * leafSecondaryVeinRoughnessOffset;
  return detail;
}

vec3 perturbLeafNormal(
  vec3 surfacePosition, vec3 surfaceNormal, float height, float maximumSlope
) {
  vec3 sigmaX = dFdx(surfacePosition);
  vec3 sigmaY = dFdy(surfacePosition);
  float heightX = dFdx(height);
  float heightY = dFdy(height);
  vec3 r1 = cross(sigmaY, surfaceNormal);
  vec3 r2 = cross(surfaceNormal, sigmaX);
  float determinant = dot(sigmaX, r1);
  vec3 gradient = sign(determinant) * (heightX * r1 + heightY * r2);
  float gradientLength = length(gradient);
  if (gradientLength <= 1e-8 || abs(determinant) <= 1e-8 || maximumSlope <= 0.0) {
    return surfaceNormal;
  }
  float slope = min(
    leafNormalDetailHeightM * gradientLength / abs(determinant), maximumSlope
  );
  return normalize(surfaceNormal - gradient / gradientLength * slope);
}
`;

const LEAF_NEUTRAL_COLORING = `
LeafDetail leafDetail = evaluateLeafDetail(vLeafUv);
bool leafFrontSide = gl_FrontFacing;
bool leafFluxColoredSide = false;
float leafSequence = fract((floor(vLeafIndex + 0.5) + 1.0) * 0.61803398875);
float leafRankVariation = (leafSequence - 0.5) * 2.0 * leafVariationMaximum;
vec3 leafNeutralColor = leafFrontSide ? leafFrontColor : leafUndersideColor;
if (!leafFrontSide) {
  float undersideLuminance = dot(leafNeutralColor, vec3(0.2126, 0.7152, 0.0722));
  leafNeutralColor = mix(
    vec3(undersideLuminance), leafNeutralColor, leafUndersideSaturation
  );
}
diffuseColor.rgb = leafNeutralColor
  * (1.0 + leafRankVariation) * leafDetail.neutralLuminance;
`;

const LEAF_ROUGHNESS_DETAIL = `
float leafBaseRoughness = leafFrontSide ? leafFrontRoughness : leafUndersideRoughness;
float leafRoughnessDetail = leafFluxColoredSide
  ? leafDetail.roughnessOffset * leafFluxRoughnessDetailScale
  : leafDetail.roughnessOffset;
roughnessFactor = clamp(leafBaseRoughness + leafRoughnessDetail, 0.76, 0.88);
`;

const LEAF_NORMAL_DETAIL = `
float leafMaximumNormalSlope = leafFluxColoredSide
  ? leafFluxNormalMaximumSlope : leafNeutralNormalMaximumSlope;
leafMaximumNormalSlope *= 1.0 - leafScientificOverlayVisible;
normal = perturbLeafNormal(
  -vViewPosition, normal, leafDetail.height, leafMaximumNormalSlope
);
`;

export function createScientificRenderer(canvas) {
  const antialias = (window.devicePixelRatio || 1) <= 1.5;
  const context = canvas.getContext("webgl2", {
    alpha: false,
    antialias,
    powerPreference: "high-performance",
  });
  if (!context) {
    throw new Error("WebGL2 is required for the scientific viewer.");
  }
  const renderer = new THREE.WebGLRenderer({ canvas, context, antialias });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
  renderer.setClearColor(0x101612, 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = false;
  return renderer;
}

export function createInspectionEnvironment(renderer, scene) {
  if (!renderer?.isWebGLRenderer || !scene?.isScene) {
    throw new Error("Inspection environment requires the active renderer and scene.");
  }
  const roomEnvironment = new RoomEnvironment();
  let pmremGenerator = null;
  let renderTarget = null;
  try {
    pmremGenerator = new THREE.PMREMGenerator(renderer);
    renderTarget = pmremGenerator.fromScene(roomEnvironment);
    scene.environment = renderTarget.texture;
    scene.environmentIntensity = 1.0;
  } catch (error) {
    renderTarget?.dispose();
    scene.environment = null;
    throw error;
  } finally {
    roomEnvironment.dispose();
    pmremGenerator?.dispose();
  }

  const texture = renderTarget.texture;
  let disposed = false;
  return Object.freeze({
    intensity: 1.0,
    renderTarget,
    texture,
    dispose() {
      if (disposed) return;
      disposed = true;
      if (scene.environment === texture) scene.environment = null;
      renderTarget.dispose();
    },
  });
}

export function createInspectionLightRig(camera) {
  if (!camera?.isCamera) {
    throw new Error("Inspection lighting requires the active viewer camera.");
  }
  const root = new THREE.Group();
  root.name = "neutral-inspection-fill-lights";
  const ambient = new THREE.AmbientLight(0xffffff, 0.18);
  ambient.name = "neutral-inspection-ambient";
  const hemisphere = new THREE.HemisphereLight(0xffffff, 0xb8b8b8, 0.28);
  hemisphere.name = "neutral-inspection-hemisphere";
  hemisphere.position.set(0, 1, 0);
  root.add(ambient, hemisphere);

  const cameraKey = new THREE.DirectionalLight(0xffffff, 0.85);
  cameraKey.name = "camera-relative-inspection-key";
  cameraKey.castShadow = false;
  cameraKey.position.set(0.45, 0.65, 0.8);
  const cameraTarget = new THREE.Object3D();
  cameraTarget.name = "camera-relative-inspection-target";
  cameraTarget.position.set(0, 0, -1);
  cameraKey.target = cameraTarget;
  camera.add(cameraKey, cameraTarget);

  let disposed = false;
  return Object.freeze({
    ambient,
    cameraKey,
    hemisphere,
    root,
    dispose() {
      if (disposed) return;
      disposed = true;
      root.removeFromParent();
      camera.remove(cameraKey, cameraTarget);
      root.clear();
    },
  });
}

export function createPlantSurface(glb, maximumInstanceCount) {
  if (!Number.isInteger(maximumInstanceCount) || maximumInstanceCount <= 0) {
    throw new Error("Maximum instance count is incompatible.");
  }
  const source = glb.attributes;
  const geometry = new THREE.BufferGeometry();
  const patchIndexAttribute = new THREE.BufferAttribute(
    Float32Array.from(source._PATCH_INDEX),
    1,
    false,
  );
  patchIndexAttribute.gpuType = THREE.FloatType;
  const leafIndexAttribute = new THREE.BufferAttribute(
    Float32Array.from(source._LEAF_INDEX),
    1,
    false,
  );
  leafIndexAttribute.gpuType = THREE.FloatType;
  geometry.setAttribute("position", new THREE.BufferAttribute(source.POSITION, 3));
  geometry.setAttribute("normal", new THREE.BufferAttribute(source.NORMAL, 3));
  geometry.setAttribute("_leaf_index", leafIndexAttribute);
  geometry.setAttribute(
    "_face_index",
    new THREE.BufferAttribute(source._FACE_INDEX, 1),
  );
  geometry.setAttribute(
    "_patch_index",
    patchIndexAttribute,
  );
  if (source.TEXCOORD_0 !== undefined) {
    if (!(source.TEXCOORD_0 instanceof Float32Array)
        || source.TEXCOORD_0.length !== source.POSITION.length / 3 * 2) {
      throw new Error("Leaf material UV inputs are incompatible.");
    }
    geometry.setAttribute(
      "_leaf_uv",
      new THREE.BufferAttribute(source.TEXCOORD_0, 2),
    );
  }
  const material = new THREE.MeshStandardMaterial({
    color: LEAF_MATERIAL_POLICY.frontColor,
    emissive: 0x000000,
    metalness: LEAF_MATERIAL_POLICY.metalness,
    opacity: 1,
    roughness: LEAF_MATERIAL_POLICY.frontRoughness,
    side: THREE.DoubleSide,
    transparent: false,
    vertexColors: false,
  });
  const surface = new THREE.InstancedMesh(
    geometry,
    material,
    maximumInstanceCount,
  );
  surface.name = "juvenile-rex-canonical-surface";
  surface.castShadow = false;
  surface.receiveShadow = false;
  surface.count = 0;
  surface.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  surface.userData.maximumInstanceCount = maximumInstanceCount;
  if (source.TEXCOORD_0 !== undefined) {
    installLeafMaterial(surface);
  }
  surface.layers.set(0);
  return surface;
}

export function setLeafScientificOverlayVisible(surface, visible) {
  surface?.userData?.leafMaterialController?.setScientificOverlayVisible(visible);
}

export function applyInstanceTranslations(surface, translations) {
  if (!(surface instanceof THREE.InstancedMesh)
      || !translations || !Number.isInteger(translations.count)
      || !(translations.values instanceof Float32Array)
      || translations.values.length !== translations.count * 3
      || translations.count > surface.userData.maximumInstanceCount) {
    throw new Error("Validated instance translations are incompatible with the surface.");
  }
  const matrix = new THREE.Matrix4();
  for (let instanceId = 0; instanceId < translations.count; instanceId += 1) {
    const offset = instanceId * 3;
    matrix.makeTranslation(
      translations.values[offset],
      translations.values[offset + 1],
      translations.values[offset + 2],
    );
    surface.setMatrixAt(instanceId, matrix);
  }
  surface.count = translations.count;
  surface.instanceMatrix.needsUpdate = true;
  surface.computeBoundingBox();
  surface.computeBoundingSphere();
  return surface;
}

export function createBoundsDisplay(bounds) {
  const minimum = new THREE.Vector3(...bounds.minimum_xyz);
  const maximum = new THREE.Vector3(...bounds.maximum_xyz);
  const box = new THREE.Box3(minimum, maximum);
  return new THREE.Box3Helper(box, 0xe7c979);
}

export function createFootprintDisplay(bounds) {
  const [minX, minZ] = bounds.minimum_xz;
  const [maxX, maxZ] = bounds.maximum_xz;
  return lineLoop([
    minX, 0, minZ,
    maxX, 0, minZ,
    maxX, 0, maxZ,
    minX, 0, maxZ,
  ], 0x66b7c7);
}

export function createReferencePlaneDisplay(referencePlane) {
  return lineLoop(referencePlane.vertices_xyz.flat(), 0x667269);
}

export function createRoomDisplay(referencePlane) {
  if (!Array.isArray(referencePlane?.vertices_xyz)
      || referencePlane.vertices_xyz.length !== 4) {
    throw new Error("Room display requires four authoritative reference vertices.");
  }
  const vertices = referencePlane.vertices_xyz.flat();
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(vertices, 3),
  );
  geometry.setIndex([0, 2, 1, 0, 3, 2]);
  const floor = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({
      color: 0x243229,
      depthWrite: false,
      opacity: 0.42,
      side: THREE.DoubleSide,
      transparent: true,
    }),
  );
  floor.name = "authoritative-room-reference-surface";
  floor.renderOrder = -10;
  const perimeter = lineLoop(vertices, 0x7f9684);
  perimeter.name = "authoritative-room-perimeter";
  const room = new THREE.Group();
  room.name = "authoritative-decimal-room";
  room.add(floor, perimeter);
  return room;
}

export function createReceiverLayer(receiverBytes) {
  const data = parseReceiverBuffer(receiverBytes);
  const pointGeometry = new THREE.BufferGeometry();
  pointGeometry.setAttribute(
    "position",
    new THREE.BufferAttribute(data.positions, 3),
  );
  const points = new THREE.Points(
    pointGeometry,
    new THREE.PointsMaterial({
      color: 0xf0b45b,
      size: 0.0022,
      sizeAttenuation: true,
      transparent: false,
    }),
  );
  points.visible = false;
  let normals = null;
  const translation = new THREE.Vector3();
  const ensureNormals = () => {
    if (normals) return normals;
    const normalGeometry = new THREE.BufferGeometry();
    normalGeometry.setAttribute(
      "position",
      new THREE.BufferAttribute(buildNormalSegments(data), 3),
    );
    normals = new THREE.LineSegments(
      normalGeometry,
      new THREE.LineBasicMaterial({ color: 0xe9e2ae }),
    );
    normals.position.copy(translation);
    normals.visible = false;
    return normals;
  };
  const setTranslation = (x, y, z) => {
    if (![x, y, z].every(Number.isFinite)) {
      throw new Error("Selected receiver translation must be finite.");
    }
    translation.set(x, y, z);
    points.position.copy(translation);
    if (normals) normals.position.copy(translation);
  };
  return Object.freeze({ count: data.count, ensureNormals, points, setTranslation });
}

export function pickSurfaceIdentity(surface, intersection) {
  if (intersection.object !== surface || !Number.isInteger(intersection.faceIndex)
      || !Number.isInteger(intersection.instanceId)) {
    throw new Error("Raycast did not hit the juvenile surface.");
  }
  const vertex = intersection.faceIndex * 3;
  return {
    instanceId: intersection.instanceId,
    leafIndex: surface.geometry.getAttribute("_leaf_index").getX(vertex),
    faceIndex: surface.geometry.getAttribute("_face_index").getX(vertex),
    patchIndex: surface.geometry.getAttribute("_patch_index").getX(vertex),
  };
}

export function disposeDisplay(display) {
  if (!display) return;
  display.geometry?.dispose();
  if (Array.isArray(display.material)) {
    display.material.forEach((material) => material.dispose());
  } else {
    display.material?.dispose();
  }
}

export function disposeObjectTree(root) {
  if (!root) return;
  const geometries = new Set();
  const materials = new Set();
  const textures = new Set();
  root.traverse((object) => {
    object.userData?.targetCoverageController?.dispose?.();
    object.userData?.surfaceFluxController?.dispose?.();
    object.userData?.leafMaterialController?.dispose?.();
    if (object.geometry) geometries.add(object.geometry);
    const objectMaterials = Array.isArray(object.material)
      ? object.material : [object.material];
    for (const material of objectMaterials) {
      if (!material) continue;
      materials.add(material);
      for (const value of Object.values(material)) {
        if (value?.isTexture) textures.add(value);
      }
    }
  });
  geometries.forEach((geometry) => geometry.dispose());
  textures.forEach((texture) => texture.dispose());
  materials.forEach((material) => material.dispose());
  root.clear();
}

function installLeafMaterial(surface) {
  const material = surface.material;
  const uniforms = {
    leafFrontColor: { value: new THREE.Color(LEAF_MATERIAL_POLICY.frontColor) },
    leafUndersideColor: { value: new THREE.Color(LEAF_MATERIAL_POLICY.undersideColor) },
    leafUndersideSaturation: { value: LEAF_MATERIAL_POLICY.undersideSaturation },
    leafFrontRoughness: { value: LEAF_MATERIAL_POLICY.frontRoughness },
    leafUndersideRoughness: { value: LEAF_MATERIAL_POLICY.undersideRoughness },
    leafVariationMaximum: { value: LEAF_MATERIAL_POLICY.leafVariationMaximum },
    leafMidribContrastMaximum: {
      value: LEAF_MATERIAL_POLICY.midribContrastMaximum,
    },
    leafSecondaryVeinContrastMaximum: {
      value: LEAF_MATERIAL_POLICY.secondaryVeinContrastMaximum,
    },
    leafFluxVeinContrastMaximum: {
      value: LEAF_MATERIAL_POLICY.fluxVeinContrastMaximum,
    },
    leafMidribRoughnessOffset: {
      value: LEAF_MATERIAL_POLICY.midribRoughnessOffset,
    },
    leafSecondaryVeinRoughnessOffset: {
      value: LEAF_MATERIAL_POLICY.secondaryVeinRoughnessOffset,
    },
    leafFluxRoughnessDetailScale: {
      value: LEAF_MATERIAL_POLICY.fluxRoughnessDetailScale,
    },
    leafNormalDetailHeightM: { value: LEAF_MATERIAL_POLICY.normalDetailHeightM },
    leafNeutralNormalMaximumSlope: {
      value: Math.tan(THREE.MathUtils.degToRad(
        LEAF_MATERIAL_POLICY.normalDetailMaximumDegrees,
      )),
    },
    leafFluxNormalMaximumSlope: {
      value: Math.tan(THREE.MathUtils.degToRad(
        LEAF_MATERIAL_POLICY.fluxNormalDetailMaximumDegrees,
      )),
    },
    leafScientificOverlayVisible: { value: 0 },
  };
  const originalCompile = material.onBeforeCompile;
  const originalCacheKey = material.customProgramCacheKey;
  const compile = (shader, renderer) => {
    originalCompile.call(material, shader, renderer);
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = injectLeafShader(
      shader.vertexShader, "#include <common>", LEAF_VERTEX_DECLARATIONS,
    );
    shader.vertexShader = injectLeafShader(
      shader.vertexShader, "#include <begin_vertex>", LEAF_VERTEX_ASSIGNMENT,
    );
    shader.fragmentShader = injectLeafShader(
      shader.fragmentShader, "#include <common>", LEAF_FRAGMENT_DECLARATIONS,
    );
    shader.fragmentShader = injectLeafShader(
      shader.fragmentShader, "#include <map_fragment>", LEAF_NEUTRAL_COLORING,
    );
    shader.fragmentShader = injectLeafShader(
      shader.fragmentShader, "#include <roughnessmap_fragment>", LEAF_ROUGHNESS_DETAIL,
    );
    shader.fragmentShader = injectLeafShader(
      shader.fragmentShader, "#include <normal_fragment_maps>", LEAF_NORMAL_DETAIL,
    );
    material.userData.leafMaterialShader = shader;
  };
  material.onBeforeCompile = compile;
  material.customProgramCacheKey = () => (
    `${originalCacheKey.call(material)}|phase27g-d4-leaf-material-v1`
  );
  material.needsUpdate = true;

  let disposed = false;
  const controller = Object.freeze({
    setScientificOverlayVisible(visible) {
      if (!disposed) uniforms.leafScientificOverlayVisible.value = visible ? 1 : 0;
    },
    getState() {
      return Object.freeze({
        disposed,
        scientificOverlayVisible: uniforms.leafScientificOverlayVisible.value === 1,
      });
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      surface.userData.targetCoverageController?.dispose?.();
      surface.userData.surfaceFluxController?.dispose?.();
      if (material.onBeforeCompile === compile) material.onBeforeCompile = originalCompile;
      material.customProgramCacheKey = originalCacheKey;
      delete material.userData.leafMaterialShader;
      if (surface.userData.leafMaterialController === controller) {
        delete surface.userData.leafMaterialController;
      }
      material.needsUpdate = true;
    },
  });
  surface.userData.leafMaterialController = controller;
}

function injectLeafShader(source, marker, addition) {
  if (typeof source !== "string" || !source.includes(marker)) {
    throw new Error(`Leaf PBR shader marker is unavailable: ${marker}`);
  }
  return source.replace(marker, `${marker}\n${addition}`);
}

function lineLoop(values, color) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(values, 3),
  );
  return new THREE.LineLoop(
    geometry,
    new THREE.LineBasicMaterial({ color }),
  );
}
