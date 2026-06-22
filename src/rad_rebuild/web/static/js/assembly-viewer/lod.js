// @ts-check

// @ts-ignore Static vendor module is served by Flask.
import * as THREE from "/static/vendor/three/three.module.js";
import {
  fitCadAnchorsToLayout,
  fixtureMountOrientationCorrectionMatrix4Elements,
  transformToMatrix4Elements,
} from "./transforms.js";

const MODEL_SCALE_METERS = 0.001;
const MODULE_NODE_PATTERN = /^module_(\d+)(?:_|$)/i;
const DEBUG_INSTANCE_LIMIT = 48;
const EXPECTED_DIRECT_MOUNT_ORIENTATION = "leds_down";
const DIRECT_MATRIX_RESIDUAL_THRESHOLD = 1.0e-6;

export function finalizeFixtureInstancedMesh(mesh) {
  if (!mesh || !mesh.isInstancedMesh) {
    return;
  }
  mesh.instanceMatrix.needsUpdate = true;
  if (typeof mesh.computeBoundingBox === "function") {
    mesh.computeBoundingBox();
  }
  if (typeof mesh.computeBoundingSphere === "function") {
    mesh.computeBoundingSphere();
  }
  mesh.frustumCulled = false;
}

function moduleNodeIndex(nodeName) {
  const match = String(nodeName || "").match(MODULE_NODE_PATTERN);
  return match ? Number.parseInt(match[1], 10) : null;
}

function sourceMeshEntries(sourceRoot) {
  sourceRoot.updateWorldMatrix(true, true);
  const entries = [];
  sourceRoot.traverse((node) => {
    if (node.isMesh && node.geometry && node.material) {
      entries.push({
        geometry: node.geometry,
        material: node.material,
        sourceMatrix: node.matrixWorld.clone(),
      });
    }
  });
  return entries;
}

function sourceRootBounds(sourceRoot) {
  sourceRoot.updateWorldMatrix(true, true);
  const inverseRoot = sourceRoot.matrixWorld.clone().invert();
  const bounds = new THREE.Box3();
  bounds.makeEmpty();
  sourceRoot.traverse((node) => {
    if (!node.isMesh || !node.geometry) {
      return;
    }
    const meshBounds = new THREE.Box3().setFromObject(node);
    if (!meshBounds.isEmpty()) {
      bounds.union(meshBounds.applyMatrix4(inverseRoot));
    }
  });
  return bounds.isEmpty() ? null : bounds;
}

function positionToWorldVector(position) {
  return new THREE.Vector3(Number(position?.x || 0), Number(position?.z || 0), Number(position?.y || 0));
}

function finiteMatrixElements(values) {
  if (!Array.isArray(values) || values.length !== 16) {
    return null;
  }
  const elements = values.map((value) => Number(value));
  return elements.every((value) => Number.isFinite(value)) ? elements : null;
}

function matrixCandidateFromInstance(instance) {
  const transform = instance?.transform && typeof instance.transform === "object" ? instance.transform : {};
  const columnMajor = finiteMatrixElements(transform.matrix4_column_major || instance?.matrix4_column_major);
  if (columnMajor) {
    return new THREE.Matrix4().fromArray(columnMajor);
  }
  const rowMajor = finiteMatrixElements(transform.matrix4 || instance?.matrix4);
  if (rowMajor) {
    const matrix = new THREE.Matrix4();
    matrix.set(...rowMajor);
    return matrix;
  }
  return null;
}

function matrixPlacementWarning(instanceLabel, reason) {
  return `${instanceLabel} direct transform matrix is ${reason}; using center/yaw placement.`;
}

function matrixIsRigidPlacement(matrix) {
  const elements = matrix.elements;
  if (
    Math.abs(elements[3]) > DIRECT_MATRIX_RESIDUAL_THRESHOLD
    || Math.abs(elements[7]) > DIRECT_MATRIX_RESIDUAL_THRESHOLD
    || Math.abs(elements[11]) > DIRECT_MATRIX_RESIDUAL_THRESHOLD
    || Math.abs(elements[15] - 1) > DIRECT_MATRIX_RESIDUAL_THRESHOLD
  ) {
    return { ok: false, reason: "malformed" };
  }
  const xAxis = new THREE.Vector3(elements[0], elements[1], elements[2]);
  const yAxis = new THREE.Vector3(elements[4], elements[5], elements[6]);
  const zAxis = new THREE.Vector3(elements[8], elements[9], elements[10]);
  const xLength = xAxis.length();
  const yLength = yAxis.length();
  const zLength = zAxis.length();
  if (xLength <= DIRECT_MATRIX_RESIDUAL_THRESHOLD || yLength <= DIRECT_MATRIX_RESIDUAL_THRESHOLD || zLength <= DIRECT_MATRIX_RESIDUAL_THRESHOLD) {
    return { ok: false, reason: "malformed" };
  }
  const maxDot = Math.max(
    Math.abs(xAxis.dot(yAxis)),
    Math.abs(xAxis.dot(zAxis)),
    Math.abs(yAxis.dot(zAxis)),
  );
  if (maxDot > DIRECT_MATRIX_RESIDUAL_THRESHOLD) {
    return { ok: false, reason: "sheared" };
  }
  const scale = (xLength + yLength + zLength) / 3;
  if (
    Math.abs(xLength - scale) > DIRECT_MATRIX_RESIDUAL_THRESHOLD
    || Math.abs(yLength - scale) > DIRECT_MATRIX_RESIDUAL_THRESHOLD
    || Math.abs(zLength - scale) > DIRECT_MATRIX_RESIDUAL_THRESHOLD
  ) {
    return { ok: false, reason: "non-uniformly scaled" };
  }
  const determinant = matrix.determinant();
  if (!Number.isFinite(determinant) || determinant <= DIRECT_MATRIX_RESIDUAL_THRESHOLD) {
    return { ok: false, reason: "reflected" };
  }
  return { ok: true, scale };
}

function centerYawMatrix(instance) {
  const position = positionToWorldVector(instance?.position);
  const yawDeg = Number(instance?.yaw_deg);
  const yawRadians = Number.isFinite(yawDeg) ? yawDeg * Math.PI / 180 : 0;
  return {
    matrix: new THREE.Matrix4()
      .makeTranslation(position.x, position.y, position.z)
      .multiply(new THREE.Matrix4().makeRotationY(yawRadians)),
    yawRadians,
    matrixSource: "position_yaw",
    placementScale: 1,
  };
}

export function directFixturePlacementMatrix(instance, warnings = []) {
  const instanceLabel = String(instance?.id || "fixture");
  const matrix = matrixCandidateFromInstance(instance);
  if (!matrix) {
    return centerYawMatrix(instance);
  }
  const validation = matrixIsRigidPlacement(matrix);
  if (!validation.ok) {
    warnings.push(matrixPlacementWarning(instanceLabel, validation.reason));
    return centerYawMatrix(instance);
  }
  return {
    matrix,
    yawRadians: Math.atan2(matrix.elements[8], matrix.elements[0]),
    matrixSource: "matrix",
    placementScale: validation.scale,
  };
}

function directMountOrientationMatrix(options, warnings) {
  const mountOrientation = typeof options.mountOrientation === "string" ? options.mountOrientation : "";
  if (!mountOrientation) {
    warnings.push("Direct fixture placement has no mount orientation metadata; using identity orientation.");
  } else if (mountOrientation !== EXPECTED_DIRECT_MOUNT_ORIENTATION) {
    warnings.push(`Direct fixture mount orientation "${mountOrientation}" is not recognized; using identity orientation.`);
  }
  return new THREE.Matrix4();
}

function directBottomAnchorMatrix(sourceRoot) {
  const bounds = sourceRootBounds(sourceRoot);
  if (!bounds) {
    return { matrix: new THREE.Matrix4(), offsetModelUnits: 0 };
  }
  const offsetModelUnits = -bounds.min.y;
  return {
    matrix: new THREE.Matrix4().makeTranslation(0, offsetModelUnits, 0),
    offsetModelUnits,
  };
}

function fallbackModuleAnchorsFromNodes(sourceRoot, viewerScale) {
  sourceRoot.updateWorldMatrix(true, true);
  const inverseRoot = sourceRoot.matrixWorld.clone().invert();
  const groupedBounds = new Map();
  sourceRoot.traverse((node) => {
    const index = moduleNodeIndex(node.name);
    if (!Number.isInteger(index)) {
      return;
    }
    const bounds = new THREE.Box3().setFromObject(node);
    if (bounds.isEmpty()) {
      return;
    }
    const rootBounds = bounds.clone().applyMatrix4(inverseRoot);
    const current = groupedBounds.get(index);
    if (current) {
      current.union(rootBounds);
    } else {
      groupedBounds.set(index, rootBounds);
    }
  });
  return Array.from(groupedBounds.entries())
    .sort(([left], [right]) => left - right)
    .map(([index, bounds]) => {
      const center = bounds.getCenter(new THREE.Vector3());
      return {
        name: `module_${index}`,
        index,
        x: center.x * viewerScale,
        z: center.z * viewerScale,
        vertical_y: center.y * viewerScale,
      };
    });
}

function usableAnchors(options, sourceRoot, viewerScale, warnings) {
  const metadataAnchors = Array.isArray(options.anchors) ? options.anchors : [];
  if (metadataAnchors.length) {
    return metadataAnchors;
  }
  const fallbackAnchors = fallbackModuleAnchorsFromNodes(sourceRoot, viewerScale);
  if (fallbackAnchors.length) {
    warnings.push("Static anchor metadata was missing; extracted module anchors from GLB node names as a development fallback.");
  }
  return fallbackAnchors;
}

function matrixFromFit(fitResult) {
  const matrix = new THREE.Matrix4();
  matrix.set(...transformToMatrix4Elements(fitResult.transform));
  return matrix;
}

function createDebugObjects(instances, anchors, instanceMatrices) {
  const group = new THREE.Group();
  group.name = "fixture-anchor-debug";
  const layoutMaterial = new THREE.MeshBasicMaterial({ color: 0x68d4ba });
  const anchorMaterial = new THREE.MeshBasicMaterial({ color: 0xff7ab6 });
  const xAxisMaterial = new THREE.LineBasicMaterial({ color: 0xff6b6b });
  const zAxisMaterial = new THREE.LineBasicMaterial({ color: 0x7aa8ff });
  const pointGeometry = new THREE.SphereGeometry(0.018, 8, 6);
  const anchorGeometry = new THREE.SphereGeometry(0.014, 8, 6);
  const vector = new THREE.Vector3();
  const origin = new THREE.Vector3();
  const fittedAnchor = new THREE.Vector3();

  for (let index = 0; index < Math.min(instances.length, DEBUG_INSTANCE_LIMIT); index += 1) {
    const matrix = instanceMatrices[index];
    const points = Array.isArray(instances[index]?.points) ? instances[index].points : [];
    for (const point of points) {
      const marker = new THREE.Mesh(pointGeometry, layoutMaterial);
      marker.position.set(Number(point?.x || 0), Number(point?.z || 0), Number(point?.y || 0));
      group.add(marker);
    }
    for (const anchor of anchors) {
      const marker = new THREE.Mesh(anchorGeometry, anchorMaterial);
      fittedAnchor.set(Number(anchor.x || 0), Number(anchor.vertical_y || 0), Number(anchor.z || 0)).applyMatrix4(matrix);
      marker.position.copy(fittedAnchor);
      group.add(marker);
    }
    if (anchors.length) {
      const anchor = anchors[0];
      origin.set(Number(anchor.x || 0), Number(anchor.vertical_y || 0), Number(anchor.z || 0)).applyMatrix4(matrix);
      vector.set(Number(anchor.x || 0) + 0.16, Number(anchor.vertical_y || 0), Number(anchor.z || 0)).applyMatrix4(matrix);
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([origin.clone(), vector.clone()]), xAxisMaterial));
      vector.set(Number(anchor.x || 0), Number(anchor.vertical_y || 0), Number(anchor.z || 0) + 0.16).applyMatrix4(matrix);
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([origin.clone(), vector.clone()]), zAxisMaterial));
    }
  }
  return group;
}

export function createFixtureInstancing(sourceRoot, instances, options = {}) {
  const safeInstances = Array.isArray(instances) ? instances : [];
  const entries = sourceMeshEntries(sourceRoot);
  const group = new THREE.Group();
  group.name = "fixture-instances";
  const viewerScale = Number.isFinite(Number(options.viewerScale)) && Number(options.viewerScale) > 0
    ? Number(options.viewerScale)
    : MODEL_SCALE_METERS;
  const warnings = [];
  const moduleAnchors = usableAnchors(options, sourceRoot, viewerScale, warnings);
  const modelScaleMatrix = new THREE.Matrix4().makeScale(viewerScale, viewerScale, viewerScale);
  const mountOrientationMatrix = new THREE.Matrix4();
  mountOrientationMatrix.set(...fixtureMountOrientationCorrectionMatrix4Elements(moduleAnchors));
  const workingMatrix = new THREE.Matrix4();
  const diagnostics = [];
  const instanceMatrices = safeInstances.map((instance) => {
    const fit = fitCadAnchorsToLayout(instance, moduleAnchors, {
      residualWarningThresholdM: options.residualWarningThresholdM,
    });
    diagnostics.push({
      id: String(instance?.id || "fixture"),
      assetKey: typeof options.assetKey === "string" ? options.assetKey : null,
      mode: fit.mode,
      residual: fit.residual,
      scale: fit.diagnostics?.scale ?? null,
      rotationRadians: fit.diagnostics?.rotationRadians ?? null,
      determinantSign: fit.diagnostics?.determinantSign ?? null,
      pointCorrespondenceInferred: fit.diagnostics?.pointCorrespondenceInferred ?? false,
      fallbackAssetUsed: fit.diagnostics?.fallbackAssetUsed ?? false,
      ok: fit.ok,
    });
    if (fit.warning) {
      warnings.push(fit.warning);
    }
    return matrixFromFit(fit);
  });

  for (const entry of entries) {
    const mesh = new THREE.InstancedMesh(entry.geometry, entry.material, safeInstances.length);
    mesh.name = "fixture-instanced-mesh";
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    for (let index = 0; index < safeInstances.length; index += 1) {
      workingMatrix
        .copy(instanceMatrices[index])
        .multiply(mountOrientationMatrix)
        .multiply(modelScaleMatrix)
        .multiply(entry.sourceMatrix);
      mesh.setMatrixAt(index, workingMatrix);
    }
    finalizeFixtureInstancedMesh(mesh);
    group.add(mesh);
  }

  if (options.debug === true) {
    group.add(createDebugObjects(safeInstances, moduleAnchors, instanceMatrices));
  }

  return {
    group,
    instanceCount: safeInstances.length,
    meshCount: entries.length,
    warnings,
    diagnostics,
  };
}

export function createDirectFixtureInstancing(sourceRoot, instances, options = {}) {
  const safeInstances = Array.isArray(instances) ? instances : [];
  const entries = sourceMeshEntries(sourceRoot);
  const group = new THREE.Group();
  group.name = "direct-fixture-instances";
  const viewerScale = Number.isFinite(Number(options.viewerScale)) && Number(options.viewerScale) > 0
    ? Number(options.viewerScale)
    : MODEL_SCALE_METERS;
  const warnings = [];
  const diagnostics = [];
  const modelScaleMatrix = new THREE.Matrix4().makeScale(viewerScale, viewerScale, viewerScale);
  const mountOrientationMatrix = directMountOrientationMatrix(options, warnings);
  const bottomAnchor = directBottomAnchorMatrix(sourceRoot);
  const workingMatrix = new THREE.Matrix4();
  const instanceMatrices = safeInstances.map((instance) => {
    const placement = directFixturePlacementMatrix(instance, warnings);
    diagnostics.push({
      id: String(instance?.id || "fixture"),
      assetKey: typeof options.assetKey === "string" ? options.assetKey : null,
      mode: "direct_fixture",
      residual: null,
      scale: placement.placementScale ?? viewerScale,
      rotationRadians: placement.yawRadians,
      determinantSign: 1,
      pointCorrespondenceInferred: false,
      fallbackAssetUsed: false,
      matrixSource: placement.matrixSource,
      mountOrientation: typeof options.mountOrientation === "string" ? options.mountOrientation : null,
      bottomAnchorOffsetM: bottomAnchor.offsetModelUnits * viewerScale,
      ok: true,
    });
    return placement.matrix;
  });

  if (!entries.length) {
    warnings.push("Fixture GLB contains no mesh geometry; fixture footprints remain available for bounds and diagnostics.");
  }

  for (const entry of entries) {
    const mesh = new THREE.InstancedMesh(entry.geometry, entry.material, safeInstances.length);
    mesh.name = "direct-fixture-instanced-mesh";
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    for (let index = 0; index < safeInstances.length; index += 1) {
      workingMatrix
        .copy(instanceMatrices[index])
        .multiply(mountOrientationMatrix)
        .multiply(modelScaleMatrix)
        .multiply(bottomAnchor.matrix)
        .multiply(entry.sourceMatrix);
      mesh.setMatrixAt(index, workingMatrix);
    }
    finalizeFixtureInstancedMesh(mesh);
    group.add(mesh);
  }

  return {
    group,
    instanceCount: safeInstances.length,
    meshCount: entries.length,
    warnings,
    diagnostics,
  };
}
