import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

export function createCameraSystem(camera, canvas, bounds) {
  const center = new THREE.Vector3();
  let radius = 0.1;
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.screenSpacePanning = true;

  const apply = (position, up = [0, 1, 0]) => {
    camera.up.set(...up);
    camera.position.set(...position);
    camera.near = Math.max(radius / 1000, 0.0001);
    camera.far = Math.max(radius * 30, 3);
    camera.updateProjectionMatrix();
    controls.target.copy(center);
    controls.update();
  };
  const reset = () => apply([
    center.x + radius * 1.35,
    center.y + radius * 1.05,
    center.z + radius * 1.35,
  ]);
  const top = () => apply(
    [center.x, center.y + radius * 2.2, center.z],
    [0, 0, -1],
  );
  const side = () => apply(
    [center.x + radius * 2.2, center.y, center.z],
    [0, 1, 0],
  );
  const setBounds = (nextBounds, resetView = true) => {
    const minimum = new THREE.Vector3(...nextBounds.minimum_xyz);
    const maximum = new THREE.Vector3(...nextBounds.maximum_xyz);
    if ([...minimum.toArray(), ...maximum.toArray()].some(
      (value) => !Number.isFinite(value),
    )) {
      throw new Error("Camera bounds must be finite.");
    }
    if (maximum.x < minimum.x || maximum.y < minimum.y || maximum.z < minimum.z) {
      throw new Error("Camera bounds must have ordered minimum and maximum values.");
    }
    center.copy(minimum).add(maximum).multiplyScalar(0.5);
    const span = maximum.clone().sub(minimum);
    radius = Math.max(span.x, span.y, span.z, 0.1);
    if (resetView) {
      reset();
    } else {
      camera.near = Math.max(radius / 1000, 0.0001);
      camera.far = Math.max(radius * 30, 3);
      camera.updateProjectionMatrix();
    }
  };
  setBounds(bounds);
  return Object.freeze({ controls, reset, setBounds, side, top });
}
