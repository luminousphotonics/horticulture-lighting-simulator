// @ts-check

// @ts-ignore Static vendor module is served by Flask.
import * as THREE from "/static/vendor/three/three.module.js";
// @ts-ignore Static vendor module is served by Flask.
import { OrbitControls } from "/static/vendor/three/controls/OrbitControls.js";
import { boundsHorizontalSpan, boundsRadius, isValidAssemblyBounds } from "./bounds.js";

function roomDimension(scene, key, fallback) {
  const value = Number(scene?.room?.[key]);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

export function createCameraRig(world, scenePayload) {
  const lengthM = roomDimension(scenePayload, "length_m", 3.048);
  const widthM = roomDimension(scenePayload, "width_m", 3.048);
  const mountZ = roomDimension(scenePayload, "mount_z_m", 0.4572);
  const bounds = world?.assemblyBounds;
  const span = isValidAssemblyBounds(bounds) ? boundsHorizontalSpan(bounds) : Math.max(lengthM, widthM, 1);
  const radius = isValidAssemblyBounds(bounds) ? boundsRadius(bounds) : span;
  const target = isValidAssemblyBounds(bounds)
    ? bounds.getCenter(new THREE.Vector3())
    : new THREE.Vector3(0, mountZ * 0.45, 0);
  const home = target.clone().add(new THREE.Vector3(span * 0.72, span * 0.62, span * 0.92));

  const controls = new OrbitControls(world.camera, world.renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.screenSpacePanning = false;
  controls.minDistance = Math.max(0.4, span * 0.16);
  controls.maxDistance = Math.max(span * 5, radius * 4);
  controls.target.copy(target);

  function reset() {
    world.camera.position.copy(home);
    world.camera.near = 0.01;
    world.camera.far = Math.max(100, span * 20, radius * 8);
    world.camera.updateProjectionMatrix();
    controls.target.copy(target);
    controls.update();
  }

  reset();
  return { controls, reset };
}
