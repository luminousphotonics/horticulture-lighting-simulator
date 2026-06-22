// @ts-check

// @ts-ignore Static vendor module is served by Flask.
import * as THREE from "/static/vendor/three/three.module.js";

export const CANOPY_Z_M = 0.025;
export const DEFAULT_ASSEMBLY_BOUNDS_PADDING_M = 0.75;

const _box = new THREE.Box3();
const _corner = new THREE.Vector3();
const _size = new THREE.Vector3();
const _center = new THREE.Vector3();

function finitePositive(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function expandByRoom(box, scenePayload) {
  const lengthM = finitePositive(scenePayload?.room?.length_m, 3.048);
  const widthM = finitePositive(scenePayload?.room?.width_m, 3.048);
  const mountZ = finitePositive(scenePayload?.room?.mount_z_m, 0.4572);
  box.expandByPoint(new THREE.Vector3(-lengthM / 2, CANOPY_Z_M, -widthM / 2));
  box.expandByPoint(new THREE.Vector3(lengthM / 2, Math.max(CANOPY_Z_M, mountZ), widthM / 2));
}

function expandByInstancePoints(box, scenePayload) {
  const instances = Array.isArray(scenePayload?.instances) ? scenePayload.instances : [];
  for (const instance of instances) {
    const points = Array.isArray(instance?.points) ? instance.points : [];
    for (const point of points) {
      const x = finiteNumber(point?.x);
      const y = finiteNumber(point?.y);
      const z = finiteNumber(point?.z);
      if (x !== null && y !== null && z !== null) {
        box.expandByPoint(new THREE.Vector3(x, z, y));
      }
    }
  }
}

function expandByMeshObject(box, object) {
  if (object.isInstancedMesh) {
    if (typeof object.computeBoundingBox === "function") {
      object.computeBoundingBox();
    }
    if (object.boundingBox && !object.boundingBox.isEmpty()) {
      _box.copy(object.boundingBox).applyMatrix4(object.matrixWorld);
      box.union(_box);
      return;
    }
  }

  const geometry = object.geometry;
  if (!geometry) {
    return;
  }
  if (!geometry.boundingBox && typeof geometry.computeBoundingBox === "function") {
    geometry.computeBoundingBox();
  }
  if (geometry.boundingBox && !geometry.boundingBox.isEmpty()) {
    _box.copy(geometry.boundingBox).applyMatrix4(object.matrixWorld);
    box.union(_box);
  }
}

export function isValidAssemblyBounds(bounds) {
  return Boolean(bounds && bounds.isBox3 && !bounds.isEmpty());
}

export function computeAssemblySceneBounds(worldOrObject, scenePayload, options = {}) {
  const root = worldOrObject?.scene || worldOrObject;
  const paddingM = Number.isFinite(Number(options.paddingM))
    ? Math.max(0, Number(options.paddingM))
    : DEFAULT_ASSEMBLY_BOUNDS_PADDING_M;
  const bounds = new THREE.Box3();
  bounds.makeEmpty();
  expandByRoom(bounds, scenePayload);
  expandByInstancePoints(bounds, scenePayload);

  if (root && typeof root.updateWorldMatrix === "function" && typeof root.traverse === "function") {
    root.updateWorldMatrix(true, true);
    root.traverse((object) => {
      if (object?.isMesh || object?.isInstancedMesh) {
        expandByMeshObject(bounds, object);
      }
    });
  }

  if (bounds.isEmpty()) {
    bounds.expandByPoint(new THREE.Vector3(-1, CANOPY_Z_M, -1));
    bounds.expandByPoint(new THREE.Vector3(1, 1, 1));
  }
  if (paddingM > 0) {
    bounds.expandByScalar(paddingM);
  }
  return bounds;
}

export function assemblyBoundsSummary(bounds) {
  if (!isValidAssemblyBounds(bounds)) {
    return null;
  }
  bounds.getSize(_size);
  bounds.getCenter(_center);
  return {
    min: { x: bounds.min.x, y: bounds.min.y, z: bounds.min.z },
    max: { x: bounds.max.x, y: bounds.max.y, z: bounds.max.z },
    size: { x: _size.x, y: _size.y, z: _size.z },
    center: { x: _center.x, y: _center.y, z: _center.z },
  };
}

export function boundsHorizontalSpan(bounds) {
  if (!isValidAssemblyBounds(bounds)) {
    return 1;
  }
  bounds.getSize(_size);
  return Math.max(_size.x, _size.z, 1);
}

export function boundsRadius(bounds) {
  if (!isValidAssemblyBounds(bounds)) {
    return 1;
  }
  bounds.getSize(_size);
  return Math.max(1, _size.length() / 2);
}

export function boundsCorners(bounds) {
  const corners = [];
  if (!isValidAssemblyBounds(bounds)) {
    return corners;
  }
  for (const x of [bounds.min.x, bounds.max.x]) {
    for (const y of [bounds.min.y, bounds.max.y]) {
      for (const z of [bounds.min.z, bounds.max.z]) {
        corners.push(_corner.clone().set(x, y, z));
      }
    }
  }
  return corners;
}
