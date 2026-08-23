import {
  IDENTITY_SCHEMA,
  LEGACY_SAMPLING_PROFILE,
  OPTIMIZED_SAMPLING_PROFILE,
  PROFILE_ID,
  resolveSceneSamplingProfile,
} from "./artifacts.js";

const LENGTHS = Object.freeze({
  plant_ids: 1,
  leaf_ids: 12,
  face_ids: 1920,
  patch_ids: 192,
  receiver_ids: 384,
  face_to_leaf: 1920,
  face_to_patch: 1920,
  patch_to_leaf: 192,
  receiver_to_patch: 384,
  receiver_side: 384,
});

export function validateIdentityMap(identity) {
  if (!identity || identity.schema_id !== IDENTITY_SCHEMA
      || ![1, 2].includes(identity.schema_version) || identity.profile_id !== PROFILE_ID) {
    throw new Error("Identity-map schema or profile is incompatible.");
  }
  resolveIdentitySamplingProfile(identity);
  for (const [name, length] of Object.entries(LENGTHS)) {
    if (!Array.isArray(identity[name]) || identity[name].length !== length) {
      throw new Error(`Identity-map ${name} length is incompatible.`);
    }
  }
  for (const name of [
    "plant_ids", "leaf_ids", "face_ids", "patch_ids", "receiver_ids",
  ]) {
    if (identity[name].some((value) => typeof value !== "string" || !value)
        || new Set(identity[name]).size !== identity[name].length) {
      throw new Error(`Identity-map ${name} values are incompatible.`);
    }
  }
  for (let index = 0; index < 384; index += 1) {
    const expectedSide = index % 2 === 0 ? "front" : "back";
    if (identity.receiver_side[index] !== expectedSide
        || identity.receiver_to_patch[index] !== Math.floor(index / 2)) {
      throw new Error("Receiver identity ordering is incompatible.");
    }
  }
  validateIndices(identity.face_to_leaf, 12, "face_to_leaf");
  validateIndices(identity.face_to_patch, 192, "face_to_patch");
  validateIndices(identity.patch_to_leaf, 12, "patch_to_leaf");
  validateIndices(identity.receiver_to_patch, 192, "receiver_to_patch");
  return identity;
}

export function resolveSurfaceIdentity(
  identity,
  { instanceId, leafIndex, faceIndex, patchIndex },
  plantIds = identity.plant_ids,
) {
  validateIdentityMap(identity);
  if (!Array.isArray(plantIds) || !Number.isInteger(instanceId)
      || instanceId < 0 || instanceId >= plantIds.length
      || typeof plantIds[instanceId] !== "string" || !plantIds[instanceId]
      || !Number.isInteger(faceIndex)
      || identity.face_to_leaf[faceIndex] !== leafIndex
      || identity.face_to_patch[faceIndex] !== patchIndex) {
    throw new Error("Picked compact identity is incompatible.");
  }
  return Object.freeze({
    plantId: plantIds[instanceId],
    leafId: identity.leaf_ids[leafIndex],
    faceId: identity.face_ids[faceIndex],
    patchId: identity.patch_ids[patchIndex],
  });
}

export function validateGeometryIdentity(scene, geometry, identity) {
  validateIdentityMap(identity);
  if (scene.profile?.profile_id !== identity.profile_id) {
    throw new Error("Scene and identity-map profile identities do not agree.");
  }
  if (resolveSceneSamplingProfile(scene) !== resolveIdentitySamplingProfile(identity)) {
    throw new Error("Scene and identity-map sampling identities do not agree.");
  }
  const leaf = geometry.attributes?._LEAF_INDEX;
  const face = geometry.attributes?._FACE_INDEX;
  const patch = geometry.attributes?._PATCH_INDEX;
  if (!leaf || !face || !patch || face.length !== 1920 * 3) {
    throw new Error("Geometry identity attributes are incompatible.");
  }
  for (let faceIndex = 0; faceIndex < 1920; faceIndex += 1) {
    const firstVertex = faceIndex * 3;
    for (let offset = 0; offset < 3; offset += 1) {
      const vertex = firstVertex + offset;
      if (face[vertex] !== faceIndex
          || leaf[vertex] !== identity.face_to_leaf[faceIndex]
          || patch[vertex] !== identity.face_to_patch[faceIndex]) {
        throw new Error("Geometry and identity-map compact indices do not agree.");
      }
    }
  }
  return true;
}

export function resolveIdentitySamplingProfile(identity) {
  if (identity?.schema_id !== IDENTITY_SCHEMA || ![1, 2].includes(identity.schema_version)) {
    throw new Error("Identity-map schema is incompatible.");
  }
  if (identity.schema_version === 1) {
    if (identity.sampling_profile_id !== undefined
        && identity.sampling_profile_id !== LEGACY_SAMPLING_PROFILE) {
      throw new Error("Identity-map historical sampling profile is incompatible.");
    }
    return LEGACY_SAMPLING_PROFILE;
  }
  if (![OPTIMIZED_SAMPLING_PROFILE, LEGACY_SAMPLING_PROFILE]
    .includes(identity.sampling_profile_id)) {
    throw new Error("Identity-map sampling profile is incompatible.");
  }
  return identity.sampling_profile_id;
}

function validateIndices(values, upper, label) {
  if (values.some((value) => !Number.isInteger(value) || value < 0 || value >= upper)) {
    throw new Error(`Identity-map ${label} values are incompatible.`);
  }
}
