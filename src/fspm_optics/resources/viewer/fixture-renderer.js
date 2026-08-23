import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { ALIGNMENT_LATTICE_DIAMETER_M } from "./fixture-artifacts.js";

export const FIXTURE_RENDER_LAYER = 1;

export async function createFixtureDisplay(fixtureArtifacts, proposedSource = null) {
  if (!fixtureArtifacts?.catalog || !Array.isArray(fixtureArtifacts.groups)
      || fixtureArtifacts.groups.length !== fixtureArtifacts.catalog.asset_group_count) {
    throw new Error("Validated fixture artifacts are incomplete.");
  }
  const root = new THREE.Group();
  root.name = "authoritative-run-fixtures";
  root.visible = true;
  const ownedGeometries = new Set();
  const ownedMaterials = new Set();
  const ownedTextures = new Set();
  const bounds = new THREE.Box3();
  const loadingManager = new THREE.LoadingManager();
  loadingManager.setURLModifier((url) => {
    throw new Error(`Fixture GLB attempted a prohibited secondary request: ${url}`);
  });
  const loader = new GLTFLoader(loadingManager);
  let primitiveCount = 0;
  let disposed = false;

  const dispose = () => {
    if (disposed) return;
    disposed = true;
    root.removeFromParent();
    for (const geometry of ownedGeometries) geometry.dispose();
    for (const texture of ownedTextures) texture.dispose();
    for (const material of ownedMaterials) material.dispose();
    root.clear();
  };

  try {
    for (const assetGroup of fixtureArtifacts.groups) {
      const gltf = await parseGlbOnce(loader, assetGroup.glbBytes);
      if (!gltf?.scene || gltf.animations?.length) {
        throw new Error("Fixture GLB parsed to unsupported scene content.");
      }
      gltf.scene.updateMatrixWorld(true);
      const meshes = [];
      let unsupportedMesh = false;
      gltf.scene.traverse((node) => {
        if (node.isSkinnedMesh || node.isInstancedMesh) {
          unsupportedMesh = true;
        }
        if (node.geometry) ownedGeometries.add(node.geometry);
        if (node.material) {
          for (const material of materialList(node.material)) {
            ownedMaterials.add(material);
            collectMaterialTextures(material, ownedTextures);
          }
        }
        if (node.isMesh) meshes.push(node);
      });
      if (unsupportedMesh) {
        throw new Error("Fixture GLB contains unsupported animated or instanced meshes.");
      }
      if (meshes.length === 0) {
        throw new Error("Fixture GLB contains no renderable meshes.");
      }
      const parityPartitions = partitionFixtureMatrixIndices(
        assetGroup.matrices.values,
        assetGroup.matrices.count,
      );
      for (const sourceMesh of meshes) {
        const authoredPostRootWorldMatrix = sourceMesh.matrixWorld.clone();
        const primitives = sourcePrimitives(sourceMesh);
        for (let primitiveIndex = 0; primitiveIndex < primitives.length; primitiveIndex += 1) {
          const primitive = primitives[primitiveIndex];
          const rootGeometry = bakeFixturePrimitiveToAssetRoot(
            sourceMesh.geometry,
            primitive.group,
            authoredPostRootWorldMatrix,
          );
          ownedGeometries.add(rootGeometry);
          for (const partition of parityPartitions) {
            const geometry = partition.parity > 0
              ? rootGeometry
              : createReflectedFixtureRootGeometry(rootGeometry);
            if (geometry !== rootGeometry) ownedGeometries.add(geometry);
            geometry.computeBoundingBox();
            if (!geometry.boundingBox || geometry.boundingBox.isEmpty()) {
              throw new Error("Fixture primitive has no finite asset-root bounds.");
            }
            const instances = new THREE.InstancedMesh(
              geometry,
              primitive.material,
              partition.sourceInstanceIds.length,
            );
            instances.name = [
              "fixture", assetGroup.catalogGroup.display_asset_id,
              sourceMesh.name || "mesh", String(primitiveIndex),
              partition.parity > 0 ? "positive" : "reflected-root",
            ].join(":");
            instances.castShadow = false;
            instances.receiveShadow = false;
            instances.instanceMatrix.setUsage(THREE.StaticDrawUsage);
            instances.layers.set(FIXTURE_RENDER_LAYER);
            const transformedBox = new THREE.Box3();
            for (let instanceId = 0;
              instanceId < partition.sourceInstanceIds.length;
              instanceId += 1) {
              const sourceInstanceId = partition.sourceInstanceIds[instanceId];
              const serverMatrix = new THREE.Matrix4().fromArray(
                assetGroup.matrices.values, sourceInstanceId * 16,
              );
              const submittedMatrix = partition.parity > 0
                ? serverMatrix.clone()
                : composeReflectedFixtureInstanceMatrix(
                  new THREE.Matrix4(), serverMatrix,
                );
              if (fixtureMatrixParity(submittedMatrix) !== 1) {
                throw new Error("Fixture InstancedMesh matrix must have positive parity.");
              }
              instances.setMatrixAt(instanceId, submittedMatrix);
              setTransformedPrimitiveBounds(
                transformedBox, geometry, submittedMatrix,
              );
              if (transformedBox.isEmpty()
                  || [...transformedBox.min.toArray(), ...transformedBox.max.toArray()]
                    .some((value) => !Number.isFinite(value))) {
                throw new Error("Fixture transformed bounds are non-finite.");
              }
              bounds.union(transformedBox);
            }
            instances.instanceMatrix.needsUpdate = true;
            instances.computeBoundingBox();
            instances.computeBoundingSphere();
            root.add(instances);
            primitiveCount += 1;
          }
        }
      }
    }
    primitiveCount += addAlignmentLattice({
      bounds,
      lattice: fixtureArtifacts.catalog.alignment_lattice ?? null,
      ownedGeometries,
      ownedMaterials,
      root,
    });
    addActiveCobLesLayer({
      bounds,
      ownedGeometries,
      ownedMaterials,
      proposedSource,
      root,
    });
    if (primitiveCount <= 0 || bounds.isEmpty()) {
      throw new Error("Fixture display contains no authoritative primitives.");
    }
    return Object.freeze({
      assets: fixtureArtifacts.groups.map(
        (item) => item.catalogGroup.display_asset_id,
      ),
      bounds: Object.freeze({
        minimum_xyz: Object.freeze(bounds.min.toArray()),
        maximum_xyz: Object.freeze(bounds.max.toArray()),
      }),
      dispose,
      fixtureCount: fixtureArtifacts.catalog.fixture_count,
      primitiveDrawCalls: primitiveCount,
      root,
    });
  } catch (error) {
    dispose();
    throw error;
  }
}

export function addAlignmentLattice({
  bounds, lattice, ownedGeometries, ownedMaterials, root,
}) {
  if (lattice === null) return 0;
  const radius = ALIGNMENT_LATTICE_DIAMETER_M / 2;
  if (lattice.diameter_m !== ALIGNMENT_LATTICE_DIAMETER_M
      || lattice.radius_m !== radius || !Array.isArray(lattice.links)
      || lattice.links.length === 0) {
    throw new Error("Validated alignment lattice geometry is incomplete.");
  }
  // Unit-height, exact-radius cylinder; only axial scale varies per free span.
  const geometry = new THREE.CylinderGeometry(radius, radius, 1, 16, 1, false);
  const material = new THREE.MeshStandardMaterial({
    color: 0xb3b3b3,
    emissive: 0x000000,
    metalness: 0.9,
    roughness: 0.1,
  });
  material.name = lattice.material_id;
  ownedGeometries.add(geometry);
  ownedMaterials.add(material);
  const mesh = new THREE.InstancedMesh(geometry, material, lattice.links.length);
  mesh.name = "standalone-module-alignment-lattice";
  mesh.userData.geometryIdentitySha256 = lattice.identity_sha256;
  mesh.userData.materialId = lattice.material_id;
  mesh.userData.transportRole = lattice.transport_role;
  mesh.castShadow = false;
  mesh.receiveShadow = false;
  mesh.instanceMatrix.setUsage(THREE.StaticDrawUsage);
  mesh.layers.set(FIXTURE_RENDER_LAYER);
  const start = new THREE.Vector3();
  const end = new THREE.Vector3();
  const midpoint = new THREE.Vector3();
  const direction = new THREE.Vector3();
  const quaternion = new THREE.Quaternion();
  const matrix = new THREE.Matrix4();
  const scale = new THREE.Vector3(1, 1, 1);
  const yAxis = new THREE.Vector3(0, 1, 0);
  for (let index = 0; index < lattice.links.length; index += 1) {
    const link = lattice.links[index];
    const first = link.start_xyz_m;
    const last = link.end_xyz_m;
    // Exact scientific-to-viewer mapping: (x, y, z) -> (x, z, -y).
    start.set(first[0], first[2], -first[1]);
    end.set(last[0], last[2], -last[1]);
    direction.subVectors(end, start);
    const length = direction.length();
    if (!(length > 0)) throw new Error("Alignment lattice free span is invalid.");
    midpoint.addVectors(start, end).multiplyScalar(0.5);
    quaternion.setFromUnitVectors(yAxis, direction.multiplyScalar(1 / length));
    scale.set(1, length, 1);
    matrix.compose(midpoint, quaternion, scale);
    mesh.setMatrixAt(index, matrix);
    bounds.expandByPoint(start);
    bounds.expandByPoint(end);
  }
  mesh.instanceMatrix.needsUpdate = true;
  mesh.computeBoundingBox();
  mesh.computeBoundingSphere();
  if (mesh.boundingBox) bounds.union(mesh.boundingBox);
  root.add(mesh);
  return 1;
}

function addActiveCobLesLayer({
  bounds,
  ownedGeometries,
  ownedMaterials,
  proposedSource,
  root,
}) {
  if (proposedSource?.source_mode !== "cob_source_shape_surrogate") return;
  const active = proposedSource.viewer_active_emitter;
  if (!active || active.representation !== "active_centered_internal_cob_les"
      || active.smd_emitter_plane_active !== false
      || active.diameter_m !== 0.022
      || active.fixture_body_transforms_changed !== false
      || active.fixture_occlusion_classification_changed !== false
      || !Array.isArray(active.modules) || active.modules.length === 0) {
    throw new Error("COB active-emitter viewer provenance is incompatible.");
  }
  const geometry = new THREE.CircleGeometry(active.diameter_m / 2, 32);
  geometry.rotateX(Math.PI / 2);
  const material = new THREE.MeshBasicMaterial({
    color: 0xffd8a0,
    depthTest: false,
    depthWrite: false,
    side: THREE.DoubleSide,
    toneMapped: false,
  });
  ownedGeometries.add(geometry);
  ownedMaterials.add(material);
  const les = new THREE.InstancedMesh(geometry, material, active.modules.length);
  les.name = "active-centered-cob-les";
  les.renderOrder = 20;
  les.layers.set(FIXTURE_RENDER_LAYER);
  const matrix = new THREE.Matrix4();
  for (let index = 0; index < active.modules.length; index += 1) {
    const record = active.modules[index];
    const center = record?.center_scientific_xyz_m;
    if (!Number.isSafeInteger(record?.module_index)
        || record.module_index !== index
        || !Array.isArray(center) || center.length !== 3
        || center.some((value) => !Number.isFinite(value))) {
      throw new Error("COB LES module placement is malformed.");
    }
    // Exact scientific-to-viewer mapping: (x, y, z) -> (x, z, -y).
    matrix.makeTranslation(center[0], center[2], -center[1]);
    les.setMatrixAt(index, matrix);
    bounds.expandByPoint(
      new THREE.Vector3(center[0], center[2], -center[1]),
    );
  }
  les.instanceMatrix.needsUpdate = true;
  les.computeBoundingBox();
  les.computeBoundingSphere();
  root.add(les);
}

export function bakeFixturePrimitiveToAssetRoot(
  source,
  group,
  authoredPostRootWorldMatrix,
) {
  const geometry = cloneFixturePrimitiveGeometry(source, group);
  promoteTransformAttributesToFloat32(geometry);
  geometry.applyMatrix4(authoredPostRootWorldMatrix);
  return geometry;
}

export function createReflectedFixtureRootGeometry(rootGeometry) {
  const geometry = rootGeometry.clone();
  // A is already baked into rootGeometry. R is therefore asset-root relative,
  // and (S R) (R A v) preserves S A v without reflecting authored translation.
  geometry.applyMatrix4(createAssetRootReflection());
  // Reflection reverses geometric face orientation. Restore winding to agree
  // with the normal matrix, then restore tangent-space bitangent handedness.
  reverseTriangleWinding(geometry);
  flipTangentHandedness(geometry);
  return geometry;
}

export function composeReflectedFixtureInstanceMatrix(target, serverMatrix) {
  return target.multiplyMatrices(serverMatrix, createAssetRootReflection());
}

export function fixtureMatrixParity(matrix) {
  const determinant = matrix.determinant();
  const elements = matrix.elements;
  const maximumLinearComponent = Math.max(
    ...[0, 1, 2, 4, 5, 6, 8, 9, 10].map((index) => Math.abs(elements[index])),
  );
  const scaledEpsilon = Number.EPSILON * 64 * maximumLinearComponent ** 3;
  if (!Number.isFinite(determinant)
      || !Number.isFinite(maximumLinearComponent)
      || maximumLinearComponent === 0
      || Math.abs(determinant) <= scaledEpsilon) {
    throw new Error("Fixture matrix parity requires a finite nonsingular transform.");
  }
  return determinant < 0 ? -1 : 1;
}

function parseGlbOnce(loader, bytes) {
  return new Promise((resolve, reject) => {
    loader.parse(bytes, "", resolve, (error) => {
      reject(new Error("Validated fixture GLB failed official GLTFLoader parsing.", {
        cause: error,
      }));
    });
  });
}

function sourcePrimitives(mesh) {
  const groups = mesh.geometry.groups;
  const materials = materialList(mesh.material);
  if (groups.length === 0) {
    if (materials.length !== 1) {
      throw new Error("Fixture mesh material inventory lacks primitive groups.");
    }
    return [{ group: null, material: materials[0] }];
  }
  return groups.map((group) => {
    const materialIndex = Array.isArray(mesh.material) ? group.materialIndex : 0;
    const material = materials[materialIndex];
    if (!material || !Number.isInteger(group.start) || !Number.isInteger(group.count)
        || group.start < 0 || group.count <= 0) {
      throw new Error("Fixture mesh primitive group is malformed.");
    }
    return { group, material };
  });
}

export function cloneFixturePrimitiveGeometry(source, group) {
  const geometry = source.clone();
  geometry.clearGroups();
  if (group) geometry.setDrawRange(group.start, group.count);
  return geometry;
}

function partitionFixtureMatrixIndices(values, count) {
  const partitions = new Map([[1, []], [-1, []]]);
  for (let sourceInstanceId = 0; sourceInstanceId < count; sourceInstanceId += 1) {
    const matrix = new THREE.Matrix4().fromArray(values, sourceInstanceId * 16);
    partitions.get(fixtureMatrixParity(matrix)).push(sourceInstanceId);
  }
  return [1, -1]
    .filter((parity) => partitions.get(parity).length > 0)
    .map((parity) => Object.freeze({
      parity,
      sourceInstanceIds: Object.freeze(partitions.get(parity)),
    }));
}

function createAssetRootReflection() {
  return new THREE.Matrix4().makeScale(-1, 1, 1);
}

function promoteTransformAttributesToFloat32(geometry) {
  for (const name of ["position", "normal", "tangent"]) {
    const source = geometry.getAttribute(name);
    if (!source) continue;
    const expectedItemSize = name === "tangent" ? 4 : 3;
    if (source.itemSize !== expectedItemSize) {
      throw new Error(`Fixture ${name} attribute layout is unsupported.`);
    }
    const values = new Float32Array(source.count * source.itemSize);
    for (let index = 0; index < source.count; index += 1) {
      values[index * source.itemSize] = source.getX(index);
      values[index * source.itemSize + 1] = source.getY(index);
      values[index * source.itemSize + 2] = source.getZ(index);
      if (source.itemSize === 4) values[index * source.itemSize + 3] = source.getW(index);
    }
    geometry.setAttribute(name, new THREE.BufferAttribute(values, source.itemSize));
  }
}

function primitiveDrawRange(geometry) {
  const index = geometry.getIndex();
  const position = geometry.getAttribute("position");
  if (!position) throw new Error("Fixture primitive lacks position geometry.");
  const availableCount = index ? index.count : position.count;
  const start = geometry.drawRange.start;
  const count = Number.isFinite(geometry.drawRange.count)
    ? geometry.drawRange.count : availableCount - start;
  if (!Number.isInteger(start) || !Number.isInteger(count)
      || start < 0 || count <= 0 || start + count > availableCount
      || start % 3 !== 0 || count % 3 !== 0) {
    throw new Error("Fixture primitive winding requires complete triangles.");
  }
  return { count, index, position, start };
}

function setTransformedPrimitiveBounds(target, geometry, matrix) {
  const { count, index, position, start } = primitiveDrawRange(geometry);
  const point = new THREE.Vector3();
  target.makeEmpty();
  for (let offset = start; offset < start + count; offset += 1) {
    const vertexIndex = index ? index.getX(offset) : offset;
    point.fromBufferAttribute(position, vertexIndex).applyMatrix4(matrix);
    target.expandByPoint(point);
  }
  return target;
}

function reverseTriangleWinding(geometry) {
  const { count, index, start } = primitiveDrawRange(geometry);
  if (index) {
    for (let offset = start; offset < start + count; offset += 3) {
      const second = index.getX(offset + 1);
      index.setX(offset + 1, index.getX(offset + 2));
      index.setX(offset + 2, second);
    }
    index.needsUpdate = true;
    return;
  }
  const attributes = [
    ...Object.values(geometry.attributes),
    ...Object.values(geometry.morphAttributes).flat(),
  ];
  for (let offset = start; offset < start + count; offset += 3) {
    for (const attribute of attributes) swapAttributeItems(attribute, offset + 1, offset + 2);
  }
  for (const attribute of attributes) attribute.needsUpdate = true;
}

function swapAttributeItems(attribute, leftIndex, rightIndex) {
  if (attribute.isInterleavedBufferAttribute) {
    for (let component = 0; component < attribute.itemSize; component += 1) {
      const left = leftIndex * attribute.data.stride + attribute.offset + component;
      const right = rightIndex * attribute.data.stride + attribute.offset + component;
      const value = attribute.data.array[left];
      attribute.data.array[left] = attribute.data.array[right];
      attribute.data.array[right] = value;
    }
    return;
  }
  if (!attribute.isBufferAttribute) {
    throw new Error("Fixture primitive contains unsupported vertex attributes.");
  }
  for (let component = 0; component < attribute.itemSize; component += 1) {
    const left = leftIndex * attribute.itemSize + component;
    const right = rightIndex * attribute.itemSize + component;
    const value = attribute.array[left];
    attribute.array[left] = attribute.array[right];
    attribute.array[right] = value;
  }
}

function flipTangentHandedness(geometry) {
  const tangent = geometry.getAttribute("tangent");
  if (!tangent) return;
  if (tangent.itemSize !== 4) {
    throw new Error("Fixture tangent attributes must contain handedness.");
  }
  for (let index = 0; index < tangent.count; index += 1) {
    tangent.setW(index, -tangent.getW(index));
  }
  tangent.needsUpdate = true;
}

function materialList(material) {
  const output = Array.isArray(material) ? material : [material];
  if (output.length === 0 || output.some((item) => !item?.isMaterial)) {
    throw new Error("Fixture mesh material is missing or unsupported.");
  }
  return output;
}

function collectMaterialTextures(material, output) {
  for (const value of Object.values(material)) {
    if (value?.isTexture) output.add(value);
  }
}
