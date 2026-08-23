import * as THREE from "three";
import {
  IDENTITY_SCHEMA,
  LEGACY_SAMPLING_PROFILE,
  OPTIMIZED_SAMPLING_PROFILE,
  PROFILE_SCHEMA,
  PROFILE_ID,
  SCENE_SCHEMA,
  resolveProfileSamplingProfile,
  resolveSceneSamplingProfile,
  validateSurfaceFluxSceneReference,
  verifyHash,
} from "./artifacts.js";
import { resolveIdentitySamplingProfile } from "./identity.js";

export const SURFACE_FLUX_SCHEMA = "fspm-optics.surface-flux-display";
export const PATCHES_PER_PLANT = 192;
export const MAX_DISPLAY_PLANT_COUNT = 625;
export const METRICS = Object.freeze(["incident_par", "absorbed_par"]);
export const SIDES = Object.freeze(["both", "front", "back"]);
// These exports retain the authenticated schema-v1 scalar contract.
export const FRONT_ANCHORS = Object.freeze([0, 0.10, 0.31, 0.89, 1, 1.76, 2.07, 2.18]);
export const BACK_ANCHORS = Object.freeze([0, 0.025, 0.050, 0.14, 0.42, 1, 2.75, 5.33]);
export const BACK_PALETTE = Object.freeze([
  Object.freeze({ name: "blue", srgb_hex: "#2563EB" }),
  Object.freeze({ name: "cyan", srgb_hex: "#06B6D4" }),
  Object.freeze({ name: "teal", srgb_hex: "#14B8A6" }),
  Object.freeze({ name: "green_low", srgb_hex: "#22C55E" }),
  Object.freeze({ name: "green_reference", srgb_hex: "#22C55E" }),
  Object.freeze({ name: "yellow_green", srgb_hex: "#A3E635" }),
  Object.freeze({ name: "orange", srgb_hex: "#F59E0B" }),
  Object.freeze({ name: "red", srgb_hex: "#DC2626" }),
]);
export const SCHEMA_V3_BACK_PALETTE_ID =
  "surface-flux-back-log-compressed-blue-to-red-8-v2";
export const SCHEMA_V3_BACK_ANCHORS = Object.freeze([
  0, 0.4, 0.7, 0.9, 1, 10, 100, 512,
]);
export const SCHEMA_V3_BACK_PALETTE = Object.freeze([
  Object.freeze({ name: "blue", srgb_hex: "#2563EB" }),
  Object.freeze({ name: "cyan", srgb_hex: "#06B6D4" }),
  Object.freeze({ name: "teal", srgb_hex: "#14B8A6" }),
  Object.freeze({ name: "green_low", srgb_hex: "#22C55E" }),
  Object.freeze({ name: "green_reference", srgb_hex: "#22C55E" }),
  Object.freeze({ name: "yellow_green", srgb_hex: "#A3E635" }),
  Object.freeze({ name: "orange", srgb_hex: "#F59E0B" }),
  Object.freeze({ name: "red", srgb_hex: "#DC2626" }),
]);
export const SURFACE_FLUX_PALETTE = BACK_PALETTE;
export const FRONT_LOCAL_PATCH_ANCHORS = Object.freeze([
  0, 0.59, 0.81, 0.89, 1, 1.14, 1.52, 2.84,
]);
export const FRONT_LOCAL_PATCH_PALETTE = Object.freeze([
  Object.freeze({ name: "blue", srgb_hex: "#2563EB" }),
  Object.freeze({ name: "cyan", srgb_hex: "#06B6D4" }),
  Object.freeze({ name: "teal", srgb_hex: "#14B8A6" }),
  Object.freeze({ name: "green_low", srgb_hex: "#22C55E" }),
  Object.freeze({ name: "green_reference", srgb_hex: "#22C55E" }),
  Object.freeze({ name: "green_high", srgb_hex: "#22C55E" }),
  Object.freeze({ name: "orange", srgb_hex: "#F59E0B" }),
  Object.freeze({ name: "red", srgb_hex: "#DC2626" }),
]);

const CALIBRATION = Object.freeze({
  report_schema_id: "fspm-optics.surface-flux-calibration-report",
  report_schema_version: 1,
  original_report_outcome: "complete_fail",
  original_cross_quality_convergence_rewritten: false,
  repository_revision: "3a35cc3a8d143e624a468049344ed15676d791e8",
  configuration_sha256: "d388d6293a94102232f5211dd8bb0b942b401ea30794a63c9f36d8920ae4d9b0",
  source_model_id: "neutral-uniform-upper-hemisphere-v1",
  source_definition_sha256: "cfe8b701186ef8c2cd49ae8d8e9783968353cbc9383e47e3674b8909bb3e6530",
  calibration_scene_sha256: "a3bf55f2e2755ee74a28c6285d8b2408ead18ca3c416487988b238debe784d32",
  topology_sha256: "b199a43c98aa899657aae4fbda0b04fee72290d05636aa39d51240598c95fa09",
  receivers_sha256: "e9631120fea1672e2047e0db7fdb8297ca9e4a9ba89cd27b947dd89af99038fc",
  material_plan_sha256: "c5d829ecdbde2b06602468e187fd75c6314f55e671b2d5529289d40adb4ccff6",
  source_scope: "open-boundary uniform upper-hemisphere neutral field",
});

const FRONT_LOCAL_PATCH_CALIBRATION = Object.freeze({
  schema_id: "fspm-optics.surface-flux-front-local-patch-calibration-input",
  schema_version: 1,
  resource_sha256: "6f22972a6ffe41db494f464abafdcad85bc4434bf7c6e326ebabfef6c1008bd3",
  combined_float64_little_endian_sha256:
    "b1e3cec31fc4b4e81ce03fd88291edaafd8bb6a717a4d228e57c60d61bd21a07",
});

const CALIBRATION_V2 = Object.freeze({
  report_schema_id: CALIBRATION.report_schema_id,
  report_schema_version: CALIBRATION.report_schema_version,
  original_d1_experiment_status: "complete_fail",
  original_d1_acceptance_pass: false,
  original_d1_outcome_must_not_be_rewritten: true,
  original_cross_quality_convergence_rewritten: false,
  repository_revision: CALIBRATION.repository_revision,
  configuration_sha256: CALIBRATION.configuration_sha256,
  source_model_id: CALIBRATION.source_model_id,
  source_definition_sha256: CALIBRATION.source_definition_sha256,
  calibration_scene_sha256: CALIBRATION.calibration_scene_sha256,
  topology_sha256: CALIBRATION.topology_sha256,
  receivers_sha256: CALIBRATION.receivers_sha256,
  material_plan_sha256: CALIBRATION.material_plan_sha256,
  source_scope: CALIBRATION.source_scope,
  front_local_patch_calibration: FRONT_LOCAL_PATCH_CALIBRATION,
});

const STANDARD_PROFILE = Object.freeze({
  profile_id: "neutral-upper-hemisphere-standard-v1",
  family_id: "neutral-upper-hemisphere-standard-family-v1",
  calibration_source_quality: "standard",
  coefficients: Object.freeze({
    front_incident: 0.4510905803216862,
    back_incident: 0.058044697174435056,
    front_absorbed: 0.3657821501976058,
    back_absorbed: 0.04670307023408276,
  }),
  fit_basis: "five-level through-origin neutral-reference sweep",
});
const QUALITY_PROFILE = Object.freeze({
  profile_id: "neutral-upper-hemisphere-quality-v1",
  family_id: "neutral-upper-hemisphere-quality-family-v1",
  calibration_source_quality: "quality",
  coefficients: Object.freeze({
    front_incident: 0.448166736538762,
    back_incident: 0.06098285511211222,
    front_absorbed: 0.3632938753333456,
    back_absorbed: 0.04909664459054215,
  }),
  fit_basis: "single held-quality neutral-reference verification level",
  achieved_reference_ppfd_umol_m2_s: 500.0112,
});

const STANDARD_FRONT_LOCAL_PATCH_PROFILE = Object.freeze({
  profile_id: "neutral-upper-hemisphere-standard-front-local-patch-v1",
  family_id: STANDARD_PROFILE.family_id,
  calibration_source_quality: "standard",
  scalarProfile: STANDARD_PROFILE,
  metricHashes: Object.freeze({
    incident_par: "a0216419e067bedaa20d54f3dbbb4c9ea375aa0ed5198e3e286d036144a925ec",
    absorbed_par: "f4b79e943efd58e07b847b1c3c51ecc8490c4af483c131918e6b00eb46c3a1fb",
  }),
  derivation:
    "five-level through-origin fit of plant-mean local-patch response against achieved Stage A reference",
});
const QUALITY_FRONT_LOCAL_PATCH_PROFILE = Object.freeze({
  profile_id: "neutral-upper-hemisphere-quality-front-local-patch-v1",
  family_id: QUALITY_PROFILE.family_id,
  calibration_source_quality: "quality",
  scalarProfile: QUALITY_PROFILE,
  metricHashes: Object.freeze({
    incident_par: "725d4b6ae9570fbafc4e01c745e120123325949d9c349230730149904ce34c41",
    absorbed_par: "98b5c1b07067f407e56c8ddd39015211822625fc8f522be480bcbfd47e86939f",
  }),
  derivation:
    "held Quality-500 plant-mean local-patch response divided by achieved Stage A reference",
});

const QUALITY_POLICY = Object.freeze({
  direct: Object.freeze({
    profile: STANDARD_PROFILE, proxy: true,
    proxyRationale: "Direct uses the Standard development family as an explicitly declared proxy.",
  }),
  standard: Object.freeze({ profile: STANDARD_PROFILE, proxy: false, proxyRationale: null }),
  quality: Object.freeze({ profile: QUALITY_PROFILE, proxy: false, proxyRationale: null }),
  rigorous: Object.freeze({
    profile: QUALITY_PROFILE, proxy: true,
    proxyRationale: "Rigorous uses the Quality high-fidelity family based on prior "
      + "Quality-versus-Rigorous convergence evidence.",
  }),
});
const FRONT_LOCAL_PATCH_QUALITY_POLICY = Object.freeze({
  direct: Object.freeze({
    profile: STANDARD_FRONT_LOCAL_PATCH_PROFILE, proxy: true,
    proxyRationale: QUALITY_POLICY.direct.proxyRationale,
  }),
  standard: Object.freeze({
    profile: STANDARD_FRONT_LOCAL_PATCH_PROFILE, proxy: false, proxyRationale: null,
  }),
  quality: Object.freeze({
    profile: QUALITY_FRONT_LOCAL_PATCH_PROFILE, proxy: false, proxyRationale: null,
  }),
  rigorous: Object.freeze({
    profile: QUALITY_FRONT_LOCAL_PATCH_PROFILE, proxy: true,
    proxyRationale: QUALITY_POLICY.rigorous.proxyRationale,
  }),
});
const BAND_ORDER = Object.freeze(["blue", "green", "orange", "red", "far_red"]);
const PAR_BAND_ORDER = Object.freeze(BAND_ORDER.slice(0, 4));
const PHASE_C_PATCH_STRIDE_BYTES = 232;
const PHASE_C_PAR_PATCH_STRIDE_BYTES = 136;
const AUTHENTICATED_METADATA = Symbol("authenticated surface-flux metadata");
const D5_C3 = Object.freeze({
  resourceId: "phase27g-d5-c3-authenticated-surface-flux-display-calibration-v1",
  reportSha256: "df53a6425f6a6ee17667ccce04533f09c7f16c291a8f0894e5bc6d8e5c75e3ce",
  completionSha256: "221c963fb34b86380624ee1daa88a29bdcd94f31730cb096fc2b2f24ca75e06a",
  topologySha256: "d59c27614c9b4fde3d60f8ebf090b56bbc6692d77baeed67753e8081ff70138f",
  receiversSha256: "e2f6606ba78642ffe6f9de0601d87c8f4e4197b66cb3ef9d284f8324e1ee305f",
  estimator: "pooled_four_observation_through_origin_gamma",
  coefficientCount: 1536,
  byteLength: 12288,
  valuesPerBlock: 192,
  familyOrder: Object.freeze(["standard", "quality"]),
  blockOrder: Object.freeze([
    "front_incident", "front_absorbed", "back_incident", "back_absorbed",
  ]),
});
const CALIBRATION_LIMITATIONS = Object.freeze([
  "Open-boundary uniform-upper-hemisphere calibration.",
  "Frozen juvenile Rex topology/material scope.",
  "Direct and Rigorous remain declared quality-family proxies.",
  "Local-patch normalization removes fixed morphology response but intentionally preserves plant-position, occlusion, and directional-lighting differences.",
  "Back receiver calibration and palette are intentionally deferred.",
]);

export function qualityFamilyFor(quality, schemaVersion = 1) {
  const policy = schemaVersion >= 2 ? FRONT_LOCAL_PATCH_QUALITY_POLICY : QUALITY_POLICY;
  return typeof quality === "string" ? policy[quality] || null : null;
}

export function globalPatchIndex(plantIndex, localPatchIndex, plantCount) {
  if (!Number.isSafeInteger(plantCount) || plantCount <= 0
      || plantCount > MAX_DISPLAY_PLANT_COUNT
      || !Number.isSafeInteger(plantIndex) || plantIndex < 0 || plantIndex >= plantCount
      || !Number.isSafeInteger(localPatchIndex)
      || localPatchIndex < 0 || localPatchIndex >= PATCHES_PER_PLANT) {
    throw new Error("Plant instance or canonical local-patch mapping is invalid.");
  }
  return PATCHES_PER_PLANT * plantIndex + localPatchIndex;
}

export function resolveSurfaceSide(mode, frontFacing) {
  if (!SIDES.includes(mode) || typeof frontFacing !== "boolean") {
    throw new Error("Surface side mode is invalid.");
  }
  if (mode === "front") return frontFacing ? "front" : null;
  if (mode === "back") return frontFacing ? null : "back";
  return frontFacing ? "front" : "back";
}

export function validateSurfaceFluxMetadata(metadata, { scene, profile, identity }) {
  assertObject(metadata, "surface-flux metadata");
  if (metadata.schema_id !== SURFACE_FLUX_SCHEMA
      || ![1, 2, 3].includes(metadata.schema_version)
      || metadata.availability !== "available") {
    throw new Error("Surface-flux schema or availability is incompatible.");
  }
  if (metadata.run_id !== scene?.run?.run_id || metadata.profile_id !== PROFILE_ID
      || profile?.profile_id !== PROFILE_ID || identity?.profile_id !== PROFILE_ID) {
    throw new Error("Surface-flux run or profile identity is incompatible.");
  }
  validateD2SamplingIdentity(metadata, { scene, profile, identity });
  const reference = validateReference(metadata.reference, metadata.schema_version);
  validateMetrics(metadata.metrics);
  validateTopology(metadata.topology, scene, profile, identity, metadata.schema_version);
  validateScientificBoundary(metadata.scientific_artifact_boundary, metadata.topology.counts);
  const artifact = validateDisplayArtifact(
    metadata.display_artifact, metadata.topology.counts, metadata.schema_version,
  );
  validateFailurePolicy(metadata.failure_policy);
  const context = Object.freeze({ scene, profile, identity });
  if (metadata.schema_version === 1) {
    validateCalibrationV1(metadata.calibration_provenance);
    const family = validateQualityFamilyV1(
      metadata.quality_family, metadata.selected_calibration_profile,
    );
    const palettes = validatePalettesV1(metadata.palette);
    validateLegendsV1(
      metadata.legends, family.profile, reference, metadata.topology.counts.patches,
    );
    if (metadata.display_equation?.formula !== "z = q / (beta * R)"
        || metadata.display_equation?.transport_correction !== false
        || !String(metadata.display_equation?.z_equals_one_meaning).includes("not a leaf target")) {
      throw new Error("Surface-flux schema-v1 display semantics are incompatible.");
    }
    return Object.freeze({
      artifact, context, family, metadata, palettes, reference, schemaVersion: 1,
    });
  }
  if (metadata.schema_version === 3) {
    const family = validateQualityFamilyV3(
      metadata.quality_family, metadata.selected_calibration_profile,
    );
    validateCalibrationV3(metadata.calibration_provenance);
    validateQualityFamilyMappingV3(metadata.quality_family_mapping);
    const calibrationArtifact = validateCalibrationArtifactV3(
      metadata.calibration_resource,
    );
    validateDisplayEquationV3(metadata.display_equation);
    validateAvailabilityV3(metadata.calibration_availability);
    const palettes = validatePalettesV3(metadata.palettes);
    validateLegendsV3(
      metadata.legends, reference, metadata.topology.counts.patches, palettes,
    );
    return Object.freeze({
      artifact, calibrationArtifact, context, family, metadata, palettes,
      reference, schemaVersion: 3,
    });
  }

  validateCalibrationV2(metadata.calibration_provenance);
  const family = validateQualityFamilyV2(
    metadata.quality_family, metadata.selected_calibration_profile,
  );
  validateQualityFamilyMappingV2(metadata.quality_family_mapping);
  const palettes = validatePalettesV2(metadata.palettes);
  validateDisplayEquationV2(metadata.display_equation);
  if (JSON.stringify(metadata.calibration_limitations)
      !== JSON.stringify(CALIBRATION_LIMITATIONS)) {
    throw new Error("Schema-v2 calibration limitations are incompatible.");
  }
  validateLegendsV2(
    metadata.legends, family, reference, metadata.topology.counts.patches, palettes,
  );
  return Object.freeze({
    artifact, context, family, metadata, palettes, reference, schemaVersion: 2,
  });
}

function validateD2SamplingIdentity(metadata, { scene, profile, identity }) {
  let samplingProfiles;
  try {
    samplingProfiles = [
      resolveSceneSamplingProfile(scene),
      resolveProfileSamplingProfile(profile),
      resolveIdentitySamplingProfile(identity),
    ];
  } catch {
    throw new Error(
      "Surface-flux calibration does not cover this receiver sampling profile.",
    );
  }
  const expectedSampling = metadata.schema_version === 3
    ? OPTIMIZED_SAMPLING_PROFILE : LEGACY_SAMPLING_PROFILE;
  if (samplingProfiles.some((value) => value !== expectedSampling)) {
    throw new Error(
      "Surface-flux calibration does not cover this receiver sampling profile.",
    );
  }
  const samplingWasInferred = scene.profile?.sampling_profile_id === undefined
    || profile.sampling_profile_id === undefined
    || identity.sampling_profile_id === undefined;
  if (samplingWasInferred) {
    const authenticHistoricalIdentity = scene.schema_id === SCENE_SCHEMA
      && scene.schema_version === 1
      && profile.schema_id === PROFILE_SCHEMA
      && profile.schema_version === 1
      && identity.schema_id === IDENTITY_SCHEMA
      && identity.schema_version === 1
      && metadata.profile_id === PROFILE_ID
      && metadata.topology?.canonical_profile_id === PROFILE_ID
      && metadata.topology?.topology_sha256 === CALIBRATION.topology_sha256
      && metadata.topology?.receivers_sha256 === CALIBRATION.receivers_sha256
      && metadata.calibration_provenance?.topology_sha256 === CALIBRATION.topology_sha256
      && metadata.calibration_provenance?.receivers_sha256 === CALIBRATION.receivers_sha256;
    if (!authenticHistoricalIdentity) {
      throw new Error(
        "Surface-flux calibration does not cover this receiver sampling profile.",
      );
    }
  }
}

export async function authenticateSurfaceFluxMetadata(
  metadata, context, calibrationBytes = null,
) {
  const validated = validateSurfaceFluxMetadata(metadata, context);
  freezeJsonValue(metadata);
  if (validated.schemaVersion === 1) return validated;
  if (validated.schemaVersion === 3) {
    if (!(calibrationBytes instanceof ArrayBuffer)) {
      throw new Error("Schema-v3 display-calibration coefficients are missing.");
    }
    const authenticatedBytes = calibrationBytes.slice(0);
    await verifyHash(
      authenticatedBytes,
      validated.calibrationArtifact.sha256,
      "surface-flux display-calibration coefficients",
    );
    const coefficients = parseDisplayCalibrationCoefficients(
      authenticatedBytes, validated.calibrationArtifact,
    );
    return Object.freeze({
      ...validated, coefficients, [AUTHENTICATED_METADATA]: true,
    });
  }
  for (const metric of METRICS) {
    const metricContract = validated.family.frontLocalPatchMetrics[metric];
    const bytes = float64LittleEndianBytes(metricContract.coefficients);
    await verifyHash(
      bytes,
      metricContract.float64_little_endian_sha256,
      `${metric} front local-patch coefficients`,
    );
  }
  return Object.freeze({ ...validated, [AUTHENTICATED_METADATA]: true });
}

export function parseSurfaceFluxValues(bytes, metadata) {
  if (!(bytes instanceof ArrayBuffer)) {
    throw new Error("Surface-flux patch values must be an ArrayBuffer.");
  }
  const artifact = metadata?.display_artifact;
  const patchCount = metadata?.topology?.counts?.patches;
  if (artifact?.component_type !== "float32" || artifact.byte_order !== "little-endian"
      || artifact.stride_bytes !== 16 || artifact.row_count !== patchCount
      || artifact.byte_length !== patchCount * 16 || bytes.byteLength !== artifact.byte_length) {
    throw new Error("Surface-flux patch-value type, count, length, or stride is invalid.");
  }
  const values = new Float32Array(patchCount * 4);
  const view = new DataView(bytes);
  for (let index = 0; index < values.length; index += 1) {
    values[index] = view.getFloat32(index * 4, true);
    if (!Number.isFinite(values[index]) || values[index] < 0) {
      values.fill(0);
      throw new Error("Surface-flux patch values contain a non-finite or negative value.");
    }
  }
  let rawValues = values;
  return Object.freeze({
    patchCount,
    get values() { return rawValues; },
    get rawValues() { return rawValues; },
    release() {
      rawValues?.fill(0);
      rawValues = null;
    },
  });
}

export function parseDisplayCalibrationCoefficients(bytes, artifact) {
  if (!(bytes instanceof ArrayBuffer)
      || artifact?.component_type !== "float64"
      || artifact.byte_order !== "little-endian"
      || artifact.stride_bytes !== 8
      || artifact.coefficient_count !== D5_C3.coefficientCount
      || artifact.byte_length !== D5_C3.byteLength
      || bytes.byteLength !== D5_C3.byteLength
      || JSON.stringify(artifact.family_order) !== JSON.stringify(D5_C3.familyOrder)
      || JSON.stringify(artifact.block_order) !== JSON.stringify(D5_C3.blockOrder)) {
    throw new Error("Surface-flux coefficient type, size, family, or block order is invalid.");
  }
  const view = new DataView(bytes);
  const families = {};
  let valueOffset = 0;
  for (const family of D5_C3.familyOrder) {
    const blocks = {};
    for (const block of D5_C3.blockOrder) {
      const values = new Array(PATCHES_PER_PLANT);
      for (let index = 0; index < PATCHES_PER_PLANT; index += 1) {
        const value = view.getFloat64(valueOffset * 8, true);
        valueOffset += 1;
        if (!Number.isFinite(value) || value <= 0) {
          throw new Error("Surface-flux coefficient is non-finite or nonpositive.");
        }
        values[index] = value;
      }
      blocks[block] = Object.freeze(values);
    }
    families[family] = Object.freeze(blocks);
  }
  if (valueOffset !== D5_C3.coefficientCount) {
    throw new Error("Surface-flux coefficient payload is incomplete.");
  }
  return Object.freeze(families);
}

export async function loadValidatedSurfaceFluxArtifacts(
  sceneUrl,
  { scene, profile, identity, signal },
) {
  if (scene.surface_flux === undefined) {
    return Object.freeze({
      available: false,
      diagnostic: "This run has no validated Phase 27G-C surface-light artifacts.",
    });
  }
  try {
    const sceneBase = new URL("./", new URL(sceneUrl, location.href));
    const metadataRecord = validateSurfaceFluxSceneReference(scene);
    const metadataUrl = new URL(metadataRecord.filename, sceneBase);
    const metadataBytes = await fetchBytes(metadataUrl, signal);
    if (metadataBytes.byteLength !== metadataRecord.byte_length) {
      throw new Error("surface-flux metadata byte length changed");
    }
    await verifyHash(metadataBytes, metadataRecord.sha256, "surface-flux metadata");
    const metadata = JSON.parse(new TextDecoder().decode(metadataBytes));
    const structural = validateSurfaceFluxMetadata(metadata, { scene, profile, identity });
    let calibrationBytes = null;
    if (structural.schemaVersion === 3) {
      const calibrationUrl = new URL(structural.calibrationArtifact.filename, sceneBase);
      calibrationBytes = await fetchBytes(calibrationUrl, signal);
    }
    const validated = await authenticateSurfaceFluxMetadata(
      metadata, { scene, profile, identity }, calibrationBytes,
    );
    const valuesUrl = new URL(validated.artifact.filename, sceneBase);
    const valueBytes = await fetchBytes(valuesUrl, signal);
    await verifyHash(valueBytes, validated.artifact.sha256, "surface-flux patch values");
    const payload = parseSurfaceFluxValues(valueBytes, metadata);
    return Object.freeze({ available: true, metadata, payload, validated });
  } catch (error) {
    return Object.freeze({
      available: false,
      diagnostic: `Surface coloring unavailable: ${error.message}`,
    });
  }
}

export function createSurfaceFluxColorController(
  surface, payload, metadata, validatedMetadata = null,
) {
  if (surface instanceof THREE.InstancedMesh) clearSurfaceFluxColoring(surface);
  let rawValues = payload?.rawValues ?? payload?.values;
  let displayValues = null;
  let contract;
  try {
    if (!(surface instanceof THREE.InstancedMesh)
        || !(rawValues instanceof Float32Array)
        || payload.patchCount !== surface.count * PATCHES_PER_PLANT
        || rawValues.length !== payload.patchCount * 4
        || !Number.isSafeInteger(surface.count)
        || surface.count <= 0 || surface.count > MAX_DISPLAY_PLANT_COUNT
        || metadata?.topology?.counts?.plants !== surface.count) {
      throw new Error("Surface-flux GPU inputs are incompatible or unbounded.");
    }
    contract = promotionContract(metadata, validatedMetadata);
    validateRawValues(rawValues);
    if (contract.schemaVersion === 2) {
      displayValues = deriveFrontLocalPatchDisplayValues(
        rawValues, surface.count, contract,
      );
    } else if (contract.schemaVersion === 3) {
      displayValues = deriveAllSurfaceDisplayValues(
        rawValues, surface.count, contract,
      );
    }
  } catch (error) {
    displayValues?.fill(0);
    if (rawValues instanceof Float32Array) rawValues.fill(0);
    payload?.release?.();
    rawValues = null;
    displayValues = null;
    throw error;
  }

  const material = surface.material;
  if (!(material instanceof THREE.MeshStandardMaterial)) {
    displayValues?.fill(0);
    if (rawValues instanceof Float32Array) rawValues.fill(0);
    payload?.release?.();
    rawValues = null;
    displayValues = null;
    throw new Error("Surface coloring requires the existing PBR plant material.");
  }
  let textureValues = displayValues ?? rawValues;
  const texture = new THREE.DataTexture(
    textureValues, PATCHES_PER_PLANT, surface.count, THREE.RGBAFormat, THREE.FloatType,
  );
  texture.name = "validated-surface-flux-rgba32f";
  texture.minFilter = THREE.NearestFilter;
  texture.magFilter = THREE.NearestFilter;
  texture.wrapS = THREE.ClampToEdgeWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  texture.generateMipmaps = false;
  texture.flipY = false;
  texture.needsUpdate = true;

  const coefficients = scalarBetas(contract);
  const uniforms = {
    surfaceFluxValues: { value: texture },
    surfaceFluxTextureSize: { value: new THREE.Vector2(PATCHES_PER_PLANT, surface.count) },
    surfaceFluxBetas: {
      value: new THREE.Vector4(
        coefficients.front_incident, coefficients.back_incident,
        coefficients.front_absorbed, coefficients.back_absorbed,
      ),
    },
    surfaceFluxReference: { value: contract.reference.value },
    surfaceFluxMetric: { value: 0 },
    surfaceFluxSideMode: { value: 0 },
    surfaceFluxEnabled: { value: 1 },
    surfaceFluxValuesAreRelative: { value: contract.schemaVersion >= 2 ? 1 : 0 },
    surfaceFluxFrontAnchors: { value: contract.palettes.front.anchors },
    surfaceFluxBackAnchors: { value: contract.palettes.back.anchors },
    surfaceFluxFrontPalette: {
      value: contract.palettes.front.colors.map(
        (anchor) => new THREE.Color(anchor.srgb_hex),
      ),
    },
    surfaceFluxBackPalette: {
      value: contract.palettes.back.colors.map(
        (anchor) => new THREE.Color(anchor.srgb_hex),
      ),
    },
  };
  const originalCompile = material.onBeforeCompile;
  const originalCacheKey = material.customProgramCacheKey;
  const fragmentColoring = surface.userData.leafMaterialController
    ? FRAGMENT_COLORING_D4 : FRAGMENT_COLORING;
  material.onBeforeCompile = (shader, renderer) => {
    originalCompile.call(material, shader, renderer);
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = injectAfter(shader.vertexShader, "#include <common>", VERTEX_DECLARATIONS);
    shader.vertexShader = injectAfter(shader.vertexShader, "#include <begin_vertex>", VERTEX_LOOKUP);
    shader.fragmentShader = injectAfter(
      shader.fragmentShader, "#include <common>", FRAGMENT_DECLARATIONS,
    );
    shader.fragmentShader = injectAfter(
      shader.fragmentShader, "#include <color_fragment>", fragmentColoring,
    );
    material.userData.surfaceFluxShader = shader;
  };
  material.customProgramCacheKey = () => (
    `${originalCacheKey.call(material)}|surface-flux-d2-schema-${contract.schemaVersion}`
  );
  material.needsUpdate = true;

  let disposed = false;
  const controller = Object.freeze({
    texture,
    getRawPatchValues(plantIndex, localPatchIndex) {
      if (disposed || !(rawValues instanceof Float32Array)) return null;
      const offset = globalPatchIndex(plantIndex, localPatchIndex, surface.count) * 4;
      return Object.freeze({
        front_incident_par: rawValues[offset],
        back_incident_par: rawValues[offset + 1],
        front_absorbed_par: rawValues[offset + 2],
        back_absorbed_par: rawValues[offset + 3],
      });
    },
    setEnabled(value) {
      if (!disposed) uniforms.surfaceFluxEnabled.value = value ? 1 : 0;
    },
    setMetric(value) {
      if (!METRICS.includes(value)) throw new Error("Surface-flux metric is unsupported.");
      if (!disposed) uniforms.surfaceFluxMetric.value = value === "incident_par" ? 0 : 1;
    },
    setSides(value) {
      if (!SIDES.includes(value)) throw new Error("Surface-flux side mode is unsupported.");
      if (!disposed) uniforms.surfaceFluxSideMode.value = SIDES.indexOf(value);
    },
    getState() {
      return Object.freeze({
        disposed,
        enabled: uniforms.surfaceFluxEnabled.value === 1,
        metric: METRICS[uniforms.surfaceFluxMetric.value],
        sides: SIDES[uniforms.surfaceFluxSideMode.value],
      });
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      uniforms.surfaceFluxValues.value = null;
      const shader = material.userData.surfaceFluxShader;
      if (shader?.uniforms?.surfaceFluxValues) shader.uniforms.surfaceFluxValues.value = null;
      texture.dispose();
      texture.image.data = null;
      uniforms.surfaceFluxFrontPalette.value = null;
      uniforms.surfaceFluxBackPalette.value = null;
      displayValues?.fill(0);
      if (rawValues instanceof Float32Array) rawValues.fill(0);
      payload?.release?.();
      rawValues = null;
      displayValues = null;
      textureValues = null;
      contract = null;
      if (material.onBeforeCompile === controllerCompile) material.onBeforeCompile = originalCompile;
      material.customProgramCacheKey = originalCacheKey;
      delete material.userData.surfaceFluxShader;
      if (surface.userData.surfaceFluxController === controller) {
        delete surface.userData.surfaceFluxController;
      }
      material.needsUpdate = true;
    },
  });
  const controllerCompile = material.onBeforeCompile;
  surface.userData.surfaceFluxController = controller;
  return controller;
}

export function clearSurfaceFluxColoring(surface) {
  const controller = surface?.userData?.surfaceFluxController;
  if (controller?.dispose) controller.dispose();
}

function validateCalibrationV1(value) {
  assertObject(value, "calibration provenance");
  if (Object.keys(value).sort().join(",") !== Object.keys(CALIBRATION).sort().join(",")) {
    throw new Error("Calibration provenance field inventory is incompatible.");
  }
  for (const [name, expected] of Object.entries(CALIBRATION)) {
    if (value[name] !== expected) {
      throw new Error(`Calibration provenance ${name} is incompatible.`);
    }
  }
}

function validateQualityFamilyV1(value, profile) {
  assertObject(value, "quality-family provenance");
  assertObject(profile, "selected calibration profile");
  const expected = qualityFamilyFor(value.evaluated_quality);
  if (!expected || value.selected_profile_id !== expected.profile.profile_id
      || value.calibration_source_quality !== expected.profile.calibration_source_quality
      || value.family_id !== expected.profile.family_id || value.proxy !== expected.proxy
      || profile.profile_id !== expected.profile.profile_id
      || profile.family_id !== expected.profile.family_id
      || profile.calibration_source_quality !== expected.profile.calibration_source_quality
      || profile.coefficient_equation !== "z = q / (beta * R)"
      || profile.fit_basis !== expected.profile.fit_basis
      || value.proxy_rationale !== expected.proxyRationale) {
    throw new Error("Surface-flux quality-family selection is incompatible.");
  }
  if (expected.proxy !== (typeof value.proxy_rationale === "string")) {
    throw new Error("Surface-flux quality proxy disclosure is incompatible.");
  }
  if (Object.keys(profile.coefficients || {}).sort().join(",")
      !== Object.keys(expected.profile.coefficients).sort().join(",")) {
    throw new Error("Surface-flux calibration coefficient inventory is incompatible.");
  }
  for (const [name, coefficient] of Object.entries(expected.profile.coefficients)) {
    if (profile.coefficients?.[name] !== coefficient) {
      throw new Error("Surface-flux calibration coefficient is incompatible.");
    }
  }
  if (expected.profile.achieved_reference_ppfd_umol_m2_s !== undefined
      && profile.achieved_reference_ppfd_umol_m2_s
        !== expected.profile.achieved_reference_ppfd_umol_m2_s) {
    throw new Error("Quality calibration reference is incompatible.");
  }
  if (expected.profile.achieved_reference_ppfd_umol_m2_s === undefined
      && profile.achieved_reference_ppfd_umol_m2_s !== undefined) {
    throw new Error("Standard calibration contains an undeclared held-quality reference.");
  }
  return expected;
}

function validateCalibrationV2(value) {
  assertObject(value, "schema-v2 calibration provenance");
  if (Object.keys(value).sort().join(",")
      !== Object.keys(CALIBRATION_V2).sort().join(",")) {
    throw new Error("Schema-v2 calibration provenance field inventory is incompatible.");
  }
  for (const [name, expected] of Object.entries(CALIBRATION_V2)) {
    if (name === "front_local_patch_calibration") {
      if (!recordsEqual(value[name], expected)) {
        throw new Error("Front local-patch calibration resource identity is incompatible.");
      }
    } else if (value[name] !== expected) {
      throw new Error(`Schema-v2 calibration provenance ${name} is incompatible.`);
    }
  }
}

function validateQualityFamilyV2(value, profile) {
  assertObject(value, "schema-v2 quality-family provenance");
  assertObject(profile, "schema-v2 selected calibration profile");
  const expected = qualityFamilyFor(value.evaluated_quality, 2);
  if (!expected || value.selected_profile_id !== expected.profile.profile_id
      || value.calibration_source_quality
        !== expected.profile.calibration_source_quality
      || value.family_id !== expected.profile.family_id
      || value.proxy !== expected.proxy
      || value.proxy_rationale !== expected.proxyRationale
      || profile.profile_id !== expected.profile.profile_id
      || profile.family_id !== expected.profile.family_id
      || profile.calibration_source_quality
        !== expected.profile.calibration_source_quality) {
    throw new Error("Schema-v2 surface-flux quality-family selection is incompatible.");
  }
  if (expected.proxy !== (typeof value.proxy_rationale === "string")) {
    throw new Error("Schema-v2 surface-flux quality proxy disclosure is incompatible.");
  }
  assertExactFields(profile, [
    "calibration_source_quality", "family_id", "front_local_patch_metrics",
    "profile_id", "scalar_beta_provenance",
  ], "schema-v2 selected calibration profile");
  const scalar = profile.scalar_beta_provenance;
  assertObject(scalar, "schema-v2 scalar beta provenance");
  const scalarProfile = expected.profile.scalarProfile;
  const scalarFields = [
    "back_used_for_coloring", "coefficient_equation", "coefficients",
    "fit_basis", "front_used_for_coloring",
  ];
  if (scalarProfile.achieved_reference_ppfd_umol_m2_s !== undefined) {
    scalarFields.push("achieved_reference_ppfd_umol_m2_s");
  }
  assertExactFields(scalar, scalarFields, "schema-v2 scalar beta provenance");
  if (scalar.coefficient_equation !== "z = q / (beta * R)"
      || scalar.fit_basis !== scalarProfile.fit_basis
      || scalar.front_used_for_coloring !== false
      || scalar.back_used_for_coloring !== true
      || scalar.achieved_reference_ppfd_umol_m2_s
        !== scalarProfile.achieved_reference_ppfd_umol_m2_s
      || !recordsEqual(scalar.coefficients, scalarProfile.coefficients)) {
    throw new Error("Schema-v2 scalar beta provenance is incompatible.");
  }

  assertObject(profile.front_local_patch_metrics, "front local-patch metrics");
  assertExactFields(
    profile.front_local_patch_metrics, METRICS, "front local-patch metric inventory",
  );
  const frontLocalPatchMetrics = {};
  for (const metric of METRICS) {
    const metricProfile = profile.front_local_patch_metrics[metric];
    assertObject(metricProfile, `${metric} front local-patch profile`);
    assertExactFields(metricProfile, [
      "coefficient_count", "coefficient_order", "coefficients", "derivation",
      "float64_little_endian_sha256",
    ], `${metric} front local-patch profile`);
    if (metricProfile.coefficient_count !== PATCHES_PER_PLANT
        || metricProfile.coefficient_order !== "canonical local_patch_index 0..191"
        || metricProfile.float64_little_endian_sha256
          !== expected.profile.metricHashes[metric]
        || metricProfile.derivation !== expected.profile.derivation
        || !Array.isArray(metricProfile.coefficients)
        || metricProfile.coefficients.length !== PATCHES_PER_PLANT
        || metricProfile.coefficients.some(
          (coefficient) => !Number.isFinite(coefficient) || coefficient <= 0,
        )) {
      throw new Error(`${metric} front local-patch coefficients are incompatible.`);
    }
    frontLocalPatchMetrics[metric] = Object.freeze({
      coefficients: Object.freeze([...metricProfile.coefficients]),
      derivation: metricProfile.derivation,
      float64_little_endian_sha256: metricProfile.float64_little_endian_sha256,
    });
  }
  return Object.freeze({
    ...expected,
    frontLocalPatchMetrics: Object.freeze(frontLocalPatchMetrics),
    scalarBetas: Object.freeze({ ...scalar.coefficients }),
  });
}

function validateQualityFamilyMappingV2(mapping) {
  assertObject(mapping, "schema-v2 quality-family mapping");
  const qualities = ["direct", "standard", "quality", "rigorous"];
  assertExactFields(mapping, qualities, "schema-v2 quality-family mapping");
  for (const quality of qualities) {
    const record = mapping[quality];
    const expected = qualityFamilyFor(quality, 2);
    assertObject(record, `${quality} quality-family mapping`);
    assertExactFields(record, [
      "calibration_source_quality", "family_id", "proxy", "proxy_rationale",
      "selected_profile_id",
    ], `${quality} quality-family mapping`);
    if (record.selected_profile_id !== expected.profile.profile_id
        || record.calibration_source_quality
          !== expected.profile.calibration_source_quality
        || record.family_id !== expected.profile.family_id
        || record.proxy !== expected.proxy
        || record.proxy_rationale !== expected.proxyRationale) {
      throw new Error(`${quality} quality-family mapping is incompatible.`);
    }
  }
}

function validateQualityFamilyV3(value, profile) {
  assertObject(value, "schema-v3 quality-family provenance");
  assertObject(profile, "schema-v3 selected calibration profile");
  const expectedFamily = ["direct", "standard"].includes(value.evaluated_quality)
    ? "standard" : ["quality", "rigorous"].includes(value.evaluated_quality)
      ? "quality" : null;
  const expectedProxy = ["direct", "rigorous"].includes(value.evaluated_quality);
  if (!expectedFamily || value.coefficient_family !== expectedFamily
      || value.calibration_source_quality !== expectedFamily
      || value.selected_profile_id !== D5_C3.resourceId
      || value.exact_family_calibration !== !expectedProxy
      || value.display_only_proxy !== expectedProxy
      || value.proxy !== expectedProxy
      || value.raw_transport_proxied !== false
      || value.proxy_scope !== (expectedProxy ? "display-calibration-only" : null)
      || expectedProxy !== (typeof value.proxy_rationale === "string")
      || profile.resource_id !== D5_C3.resourceId
      || profile.coefficient_family !== expectedFamily
      || profile.estimator !== D5_C3.estimator
      || profile.coefficient_count_per_block !== PATCHES_PER_PLANT
      || JSON.stringify(profile.block_order) !== JSON.stringify(D5_C3.blockOrder)
      || profile.all_cells_available !== true
      || profile.threshold_masking_applied !== false) {
    throw new Error("Schema-v3 surface-flux quality dispatch is incompatible.");
  }
  return Object.freeze({
    coefficientFamily: expectedFamily,
    displayOnlyProxy: expectedProxy,
  });
}

function validateQualityFamilyMappingV3(mapping) {
  assertObject(mapping, "schema-v3 quality-family mapping");
  const expected = {
    direct: ["standard", false, true],
    standard: ["standard", true, false],
    quality: ["quality", true, false],
    rigorous: ["quality", false, true],
  };
  assertExactFields(mapping, Object.keys(expected), "schema-v3 quality-family mapping");
  for (const [quality, [family, exact, proxy]] of Object.entries(expected)) {
    const record = mapping[quality];
    if (record?.evaluated_quality !== quality
        || record.coefficient_family !== family
        || record.exact_family_calibration !== exact
        || record.display_only_proxy !== proxy
        || record.raw_transport_proxied !== false
        || record.proxy_scope !== (proxy ? "display-calibration-only" : null)) {
      throw new Error(`Schema-v3 ${quality} display dispatch is incompatible.`);
    }
  }
}

function validateCalibrationV3(value) {
  assertObject(value, "schema-v3 calibration provenance");
  if (value.resource_schema_id
        !== "fspm-optics.authenticated-surface-flux-display-calibration-resource"
      || value.resource_schema_version !== 1
      || value.resource_id !== D5_C3.resourceId
      || value.purpose
        !== "display-only normalized PAR surface-flux coloring; not scientific transport"
      || value.estimator !== D5_C3.estimator
      || value.topology_sha256 !== D5_C3.topologySha256
      || value.receivers_sha256 !== D5_C3.receiversSha256
      || value.sampling_profile_id !== OPTIMIZED_SAMPLING_PROFILE
      || value.c2_report_sha256 !== D5_C3.reportSha256
      || value.c2_completion_sha256 !== D5_C3.completionSha256
      || value.d5_b1_modified !== false) {
    throw new Error("Schema-v3 calibration provenance is incompatible.");
  }
}

function validateCalibrationArtifactV3(artifact) {
  if (artifact?.filename
        !== "surface-flux/display-calibration-coefficients.v1.f64le.bin"
      || artifact.resource_id !== D5_C3.resourceId
      || artifact.component_type !== "float64"
      || artifact.byte_order !== "little-endian"
      || artifact.stride_bytes !== 8
      || artifact.coefficient_count !== D5_C3.coefficientCount
      || artifact.byte_length !== D5_C3.byteLength
      || artifact.values_per_block !== D5_C3.valuesPerBlock
      || JSON.stringify(artifact.family_order) !== JSON.stringify(D5_C3.familyOrder)
      || JSON.stringify(artifact.block_order) !== JSON.stringify(D5_C3.blockOrder)) {
    throw new Error("Schema-v3 coefficient artifact contract is incompatible.");
  }
  assertSha256(artifact.sha256, "schema-v3 coefficient artifact");
  return artifact;
}

function validateAvailabilityV3(value) {
  if (value?.available_cell_count !== D5_C3.coefficientCount
      || value.masked_cell_count !== 0
      || value.all_authenticated_finite_positive_cells_available !== true
      || value.repeatability_threshold_applied !== false
      || value.a2_failure_mask_applied !== false) {
    throw new Error("Schema-v3 all-cell availability policy is incompatible.");
  }
}

function validateDisplayEquationV3(value) {
  if (value?.formula
        !== "u[p,k,s,m] = q[p,k,s,m] / (gamma[coefficient_family,s,m,k] * R)"
      || value.variable !== "u"
      || value.gamma_estimator !== D5_C3.estimator
      || value.reference !== "authenticated achieved Stage A mean"
      || JSON.stringify(value.raw_rgba_order) !== JSON.stringify([
        "front_incident", "back_incident", "front_absorbed", "back_absorbed",
      ])
      || JSON.stringify(value.coefficient_block_order)
        !== JSON.stringify(D5_C3.blockOrder)
      || JSON.stringify(value.explicit_order_remapping) !== JSON.stringify([0, 2, 1, 3])
      || value.transport_correction !== false
      || value.raw_q_modified !== false) {
    throw new Error("Schema-v3 normalization/remapping equation is incompatible.");
  }
}

function validateReference(reference, schemaVersion = 1) {
  assertObject(reference, "surface-flux reference");
  if (schemaVersion === 3) {
    const source = reference.source_provenance;
    if (!Number.isFinite(reference.value) || reference.value <= 0
        || reference.units !== "umol/m^2/s"
        || !["target-controlled", "fixed-output"].includes(reference.operating_policy)
        || reference.value_kind !== "achieved"
        || source?.stage_id !== "baseline_ppfd"
        || source.artifact !== "ppfd.csv"
        || source.field !== "achieved_mean_ppfd_umol_m2_s"
        || source.authenticated !== true
        || source.plant_free_reference_plane !== true) {
      throw new Error("Schema-v3 achieved Stage A reference is incompatible.");
    }
    return reference;
  }
  if (!Number.isFinite(reference.value) || reference.value <= 0
      || reference.units !== "umol/m^2/s"
      || !["target-controlled", "fixed-output"].includes(reference.operating_policy)
      || (reference.operating_policy === "target-controlled" && reference.value_kind !== "requested")
      || (reference.operating_policy === "fixed-output" && reference.value_kind !== "achieved")) {
    throw new Error("Surface-flux Stage A reference provenance is incompatible.");
  }
  assertObject(reference.source_provenance, "reference source provenance");
  const expectedSource = reference.operating_policy === "target-controlled"
    ? { stage_id: "baseline_ppfd", artifact: "request", field: "target_ppfd_umol_m2_s" }
    : {
      stage_id: "baseline_ppfd", artifact: "ppfd.csv",
      field: "achieved_mean_ppfd_umol_m2_s", plant_free_reference_plane: true,
    };
  if (Object.keys(reference.source_provenance).sort().join(",")
        !== Object.keys(expectedSource).sort().join(",")
      || Object.entries(expectedSource).some(
        ([name, value]) => reference.source_provenance[name] !== value,
      )) {
    throw new Error("Surface-flux Stage A reference source is incompatible.");
  }
  return reference;
}

function validateMetrics(metrics) {
  if (JSON.stringify(metrics?.available) !== JSON.stringify(METRICS)
      || metrics?.far_red_excluded !== true
      || !String(metrics.incident_par).includes("blue + green + orange + red")
      || !String(metrics.absorbed_par).includes("blue + green + orange + red")) {
    throw new Error("Surface-flux metric identity or far-red exclusion is incompatible.");
  }
}

function validateDisplayEquationV2(equation) {
  assertObject(equation, "schema-v2 display equation");
  assertExactFields(
    equation, ["back", "front", "raw_q_modified", "transport_correction"],
    "schema-v2 display equation",
  );
  const front = equation.front;
  const back = equation.back;
  assertObject(front, "schema-v2 front display equation");
  assertObject(back, "schema-v2 back display equation");
  assertExactFields(front, [
    "formula", "front_beta_used_for_coloring", "gamma_index", "q_authority",
    "u_equals_one_meaning", "variable",
  ], "schema-v2 front display equation");
  assertExactFields(back, [
    "back_beta_used_for_coloring", "formula", "q_authority",
    "variable", "z_equals_one_meaning",
  ], "schema-v2 back display equation");
  const qAuthority = "Phase 27G-C raw Float64 PAR surface-light density";
  if (front.formula
        !== "u[p,k,m] = q[p,k,m] / (gamma[quality_family,m,k] * R)"
      || front.variable !== "u"
      || front.q_authority !== qAuthority
      || front.gamma_index
        !== "canonical local_patch_index k in [0, 191], reused across plants"
      || !String(front.u_equals_one_meaning).includes("not a leaf target")
      || !String(front.u_equals_one_meaning).includes("not raw-q uniformity")
      || front.front_beta_used_for_coloring !== false
      || back.formula !== "z = q / (beta * R)"
      || back.variable !== "z"
      || back.q_authority !== qAuthority
      || !String(back.z_equals_one_meaning).includes("not a leaf target")
      || back.back_beta_used_for_coloring !== true
      || equation.transport_correction !== false
      || equation.raw_q_modified !== false) {
    throw new Error("Schema-v2 front/back display equations are incompatible.");
  }
}

function validatePalettesV1(palette) {
  if (palette?.palette_id !== "surface-flux-blue-to-red-8-v1"
      || palette.shared_across_metrics_systems_and_quality_families !== true
      || palette.per_run_extrema_normalization !== false
      || JSON.stringify(palette.front_anchors_z) !== JSON.stringify(FRONT_ANCHORS)
      || JSON.stringify(palette.back_anchors_z) !== JSON.stringify(BACK_ANCHORS)
      || JSON.stringify(palette.colors) !== JSON.stringify(SURFACE_FLUX_PALETTE)) {
    throw new Error("Surface-flux fixed palette or anchors are incompatible.");
  }
  return frozenPalettes(
    FRONT_ANCHORS, SURFACE_FLUX_PALETTE, "z",
    BACK_ANCHORS, SURFACE_FLUX_PALETTE, "z",
  );
}

function validatePalettesV2(palettes) {
  assertObject(palettes, "schema-v2 side-specific palettes");
  assertExactFields(palettes, ["back", "front"], "schema-v2 palette inventory");
  const front = palettes.front;
  const back = palettes.back;
  assertObject(front, "schema-v2 front palette");
  assertObject(back, "schema-v2 back palette");
  assertExactFields(front, [
    "anchors_u", "colors", "continuous_interpolation", "green_interval_u",
    "palette_id", "per_run_extrema_normalization",
    "shared_across_metrics_systems_and_quality_families", "variable",
  ], "schema-v2 front palette");
  assertExactFields(back, [
    "anchors_z", "colors", "continuous_interpolation", "palette_id",
    "per_run_extrema_normalization",
    "shared_across_metrics_systems_and_quality_families", "variable",
  ], "schema-v2 back palette");
  if (front.palette_id
        !== "surface-flux-front-neutral-local-patch-blue-to-red-8-v1"
      || front.variable !== "u"
      || JSON.stringify(front.anchors_u) !== JSON.stringify(FRONT_LOCAL_PATCH_ANCHORS)
      || JSON.stringify(front.colors) !== JSON.stringify(FRONT_LOCAL_PATCH_PALETTE)
      || JSON.stringify(front.green_interval_u) !== JSON.stringify([0.89, 1.14])
      || front.continuous_interpolation !== true
      || front.shared_across_metrics_systems_and_quality_families !== true
      || front.per_run_extrema_normalization !== false) {
    throw new Error("Schema-v2 front local-patch palette is incompatible.");
  }
  if (back.palette_id !== "surface-flux-blue-to-red-8-v1"
      || back.variable !== "z"
      || JSON.stringify(back.anchors_z) !== JSON.stringify(BACK_ANCHORS)
      || JSON.stringify(back.colors) !== JSON.stringify(BACK_PALETTE)
      || back.continuous_interpolation !== true
      || back.shared_across_metrics_systems_and_quality_families !== true
      || back.per_run_extrema_normalization !== false) {
    throw new Error("Schema-v2 unchanged back palette is incompatible.");
  }
  return frozenPalettes(
    FRONT_LOCAL_PATCH_ANCHORS, FRONT_LOCAL_PATCH_PALETTE, "u",
    BACK_ANCHORS, BACK_PALETTE, "z",
  );
}

function validatePalettesV3(palettes) {
  assertObject(palettes, "schema-v3 side-specific palettes");
  assertExactFields(palettes, ["back", "front"], "schema-v3 palette inventory");
  const front = palettes.front;
  const back = palettes.back;
  assertObject(front, "schema-v3 front palette");
  assertObject(back, "schema-v3 back palette");
  assertExactFields(front, [
    "anchors_u", "colors", "continuous_interpolation", "green_interval_u",
    "palette_id", "per_run_extrema_normalization",
    "shared_across_metrics_systems_and_quality_families", "variable",
  ], "schema-v3 front palette");
  assertExactFields(back, [
    "anchors_u", "colors", "continuous_interpolation", "palette_id",
    "per_run_extrema_normalization",
    "shared_across_metrics_systems_and_quality_families", "variable",
  ], "schema-v3 back palette");
  if (front.palette_id
        !== "surface-flux-front-neutral-local-patch-blue-to-red-8-v1"
      || front.variable !== "u"
      || JSON.stringify(front.anchors_u) !== JSON.stringify(FRONT_LOCAL_PATCH_ANCHORS)
      || JSON.stringify(front.colors) !== JSON.stringify(FRONT_LOCAL_PATCH_PALETTE)
      || JSON.stringify(front.green_interval_u) !== JSON.stringify([0.89, 1.14])
      || front.continuous_interpolation !== true
      || front.shared_across_metrics_systems_and_quality_families !== true
      || front.per_run_extrema_normalization !== false
      || back.palette_id !== SCHEMA_V3_BACK_PALETTE_ID
      || back.variable !== "u"
      || JSON.stringify(back.anchors_u) !== JSON.stringify(SCHEMA_V3_BACK_ANCHORS)
      || JSON.stringify(back.colors) !== JSON.stringify(SCHEMA_V3_BACK_PALETTE)
      || back.continuous_interpolation !== true
      || back.shared_across_metrics_systems_and_quality_families !== true
      || back.per_run_extrema_normalization !== false) {
    throw new Error("Schema-v3 normalized-u palettes are incompatible.");
  }
  return frozenPalettes(
    FRONT_LOCAL_PATCH_ANCHORS, FRONT_LOCAL_PATCH_PALETTE, "u",
    SCHEMA_V3_BACK_ANCHORS, SCHEMA_V3_BACK_PALETTE, "u",
  );
}

function validateTopology(topology, scene, profile, identity, schemaVersion) {
  assertObject(topology, "surface-flux topology");
  const plants = scene.natural_fit.plant_count;
  const expectedCounts = {
    plants, leaves: plants * 12, faces: plants * 1920,
    patches: plants * PATCHES_PER_PLANT, receivers: plants * 384,
  };
  const expectedTopology = schemaVersion === 3
    ? D5_C3.topologySha256 : CALIBRATION.topology_sha256;
  const expectedReceivers = schemaVersion === 3
    ? D5_C3.receiversSha256 : CALIBRATION.receivers_sha256;
  if (plants > MAX_DISPLAY_PLANT_COUNT || topology.canonical_profile_id !== PROFILE_ID
      || topology.topology_sha256 !== expectedTopology
      || topology.receivers_sha256 !== expectedReceivers
      || !recordsEqual(topology.counts, expectedCounts)
      || topology.local_patches_per_plant !== PATCHES_PER_PLANT
      || topology.global_patch_equation
        !== "global_patch_index = 192 * plant_index + local_patch_index"
      || topology.instance_mapping !== "instance_id equals canonical plant_index"
      || topology.front_back_selection !== "fragment gl_FrontFacing selects front or back"
      || topology.layout_plan_hash !== scene.natural_fit.plan_hash
      || profile.counts?.patches !== PATCHES_PER_PLANT
      || identity.patch_ids?.length !== PATCHES_PER_PLANT
      || identity.patch_to_leaf?.length !== PATCHES_PER_PLANT
      || identity.patch_to_leaf.some(
        (value) => !Number.isSafeInteger(value) || value < 0 || value >= 12,
      )
      || identity.receiver_to_patch?.length !== 384
      || identity.receiver_to_patch.some((value, index) => value !== Math.floor(index / 2))
      || identity.face_to_patch?.length !== 1920
      || identity.face_to_patch?.some(
        (value) => !Number.isSafeInteger(value) || value < 0 || value >= PATCHES_PER_PLANT,
      )
      || identity.receiver_side?.join(",")
        !== Array.from({ length: 384 }, (_, index) => (index % 2 ? "back" : "front")).join(",")) {
    throw new Error("Surface-flux topology, counts, mapping, or receiver order is incompatible.");
  }
  if (schemaVersion === 2
      && (topology.front_receiver_equation
          !== "384 * plant_index + 2 * local_patch_index"
        || topology.coefficient_is_average_over_all_64_plants !== true
        || topology.coefficient_position_dependence !== false
        || topology.ordering
          !== "Y-major/X-minor plants; plant-major canonical patches")) {
    throw new Error("Front local-patch coefficient topology is incompatible.");
  }
  if (schemaVersion === 3
      && (topology.sampling_profile_id !== OPTIMIZED_SAMPLING_PROFILE
        || topology.front_receiver_equation
          !== "384 * plant_index + 2 * local_patch_index"
        || topology.back_receiver_equation
          !== "384 * plant_index + 2 * local_patch_index + 1"
        || topology.coefficient_is_average_over_all_64_plants !== true
        || topology.coefficient_position_dependence !== false
        || topology.ordering
          !== "Y-major/X-minor plants; plant-major canonical patches")) {
    throw new Error("Schema-v3 optimized coefficient topology is incompatible.");
  }
}

function validateScientificBoundary(boundary, counts) {
  assertObject(boundary, "surface-flux scientific boundary");
  const historical = boundary.aggregation_schema_version === 1
    && boundary.transport_schema_version === 2;
  const current = boundary.aggregation_schema_version === 2
    && boundary.transport_schema_version === 3;
  if (boundary.aggregation_schema_id !== "fspm-optics.fspm-surface-light-aggregation"
      || (!historical && !current)
      || boundary.transport_schema_id
        !== "fspm-optics.juvenile-multi-plant-five-band-receivers"
      || boundary.raw_float64_values_modified !== false
      || boundary.far_red_read_into_display_values !== false) {
    throw new Error("Surface-flux scientific artifact boundary is incompatible.");
  }
  const executedBandOrder = historical
    ? BAND_ORDER
    : boundary.executed_band_order;
  const fourBand = Array.isArray(executedBandOrder)
    && executedBandOrder.length === PAR_BAND_ORDER.length
    && executedBandOrder.every((band, index) => band === PAR_BAND_ORDER[index]);
  const fiveBand = Array.isArray(executedBandOrder)
    && executedBandOrder.length === BAND_ORDER.length
    && executedBandOrder.every((band, index) => band === BAND_ORDER[index]);
  if ((!fourBand && !fiveBand)
      || (current && boundary.far_red_executed !== fiveBand)
      || (historical && boundary.executed_band_order !== undefined
        && (!Array.isArray(boundary.executed_band_order)
          || boundary.executed_band_order.length !== BAND_ORDER.length
          || boundary.executed_band_order.some(
            (band, index) => band !== BAND_ORDER[index],
          )))
      || (historical && boundary.far_red_executed !== undefined
        && boundary.far_red_executed !== true)) {
    throw new Error("Surface-flux executed-band identity is incompatible.");
  }
  for (const name of ["aggregation_metadata_sha256", "transport_metadata_sha256",
    "compact_receiver_index_sha256"]) assertSha256(boundary[name], name);
  const patch = boundary.patch_float64_artifact;
  const patchPath = historical
    ? "fspm-aggregation/patch-surface-light.v1.f64le.bin"
    : "fspm-aggregation/patch-surface-light.v2.f64le.bin";
  const patchStride = fourBand
    ? PHASE_C_PAR_PATCH_STRIDE_BYTES
    : PHASE_C_PATCH_STRIDE_BYTES;
  if (patch?.role !== "fspm_patch_surface_light"
      || patch.path !== patchPath
      || patch.media_type !== "application/octet-stream"
      || patch.row_count !== counts.patches
      || patch.stride_bytes !== patchStride
      || patch.byte_length !== counts.patches * patchStride) {
    throw new Error("Surface-flux Phase 27G-C patch authority is incompatible.");
  }
  assertSha256(patch.sha256, "Phase 27G-C patch table");
  const room = boundary.room_summary_artifact;
  const roomPath = historical
    ? "fspm-aggregation/room-summary.v1.json"
    : "fspm-aggregation/room-summary.v2.json";
  if (room?.role !== "fspm_room_surface_light_summary"
      || room.path !== roomPath
      || room.media_type !== "application/json"
      || !Number.isSafeInteger(room.byte_length) || room.byte_length <= 0) {
    throw new Error("Surface-flux Phase 27G-C room authority is incompatible.");
  }
  assertSha256(room.sha256, "Phase 27G-C room summary");
  if (!Array.isArray(boundary.material_coefficient_authorities)
      || boundary.material_coefficient_authorities.length !== executedBandOrder.length) {
    throw new Error("Surface-flux material authority inventory is incomplete.");
  }
  boundary.material_coefficient_authorities.forEach((authority, index) => {
    if (authority.band_id !== executedBandOrder[index]) {
      throw new Error("Surface-flux material authority order is incompatible.");
    }
    for (const field of ["material_sha256", "material_provenance_sha256", "raw_receiver_sha256"]) {
      assertSha256(authority[field], `surface-flux ${field}`);
    }
  });
}

function validateDisplayArtifact(artifact, counts, schemaVersion) {
  if (artifact?.filename !== "surface-flux/patch-values.v1.f32le.bin"
      || artifact.component_type !== "float32" || artifact.byte_order !== "little-endian"
      || artifact.stride_bytes !== 16 || artifact.row_count !== counts.patches
      || artifact.byte_length !== counts.patches * 16
      || artifact.record_layout
        !== "front_incident_par, back_incident_par, front_absorbed_par, back_absorbed_par"
      || artifact.texture_layout?.format !== "RGBA32F"
      || artifact.texture_layout?.width !== PATCHES_PER_PLANT
      || artifact.texture_layout?.height !== counts.plants
      || artifact.texture_layout?.texel_count !== counts.patches
      || artifact.texture_layout?.bounded_maximum_plant_count !== MAX_DISPLAY_PLANT_COUNT) {
    throw new Error("Surface-flux display artifact or bounded texture layout is incompatible.");
  }
  if (schemaVersion === 1 && artifact.display_only_float32_derivative !== true) {
    throw new Error("Schema-v1 Float32 display derivative is incompatible.");
  }
  if (schemaVersion >= 2
      && (artifact.channel_semantics !== "raw q; neither front u nor back z"
        || artifact.channel_units !== "umol/m^2/s"
        || artifact.raw_q_float32_derivative !== true
        || artifact.raw_q_modified !== false
        || artifact.normalization_applied_to_artifact !== false
        || artifact.display_only_float32_derivative !== undefined)) {
    throw new Error("Schema-v2 raw-q Float32 channel semantics are incompatible.");
  }
  assertSha256(artifact.sha256, "surface-flux patch values");
  return artifact;
}

function validateLegendsV1(legends, profile, reference, patchCount) {
  for (const metric of METRICS) {
    for (const side of ["front", "back"]) {
      const legend = legends?.[metric]?.[side];
      const coefficient = profile.coefficients[`${side}_${metric.replace("_par", "")}`];
      const anchors = side === "front" ? FRONT_ANCHORS : BACK_ANCHORS;
      if (legend?.metric !== metric || legend.side !== side || legend.units !== "umol/m^2/s"
          || legend.beta !== coefficient || legend.reference?.value !== reference.value
          || legend.reference?.units !== reference.units
          || legend.reference?.value_kind !== reference.value_kind
          || legend.reference?.operating_policy !== reference.operating_policy
          || !recordsEqual(
            legend.reference?.source_provenance, reference.source_provenance,
          )
          || !Number.isFinite(legend.raw_minimum_q) || legend.raw_minimum_q < 0
          || !Number.isFinite(legend.raw_maximum_q)
          || legend.raw_maximum_q < legend.raw_minimum_q
          || !Array.isArray(legend.anchors) || legend.anchors.length !== anchors.length
          || !String(legend.z_equals_one_meaning).includes("not a leaf target")) {
        throw new Error("Surface-flux legend identity or raw range is incompatible.");
      }
      legend.anchors.forEach((anchor, index) => {
        const expectedQ = anchors[index] * coefficient * reference.value;
        if (anchor.z !== anchors[index] || anchor.color_name !== SURFACE_FLUX_PALETTE[index].name
            || anchor.srgb_hex !== SURFACE_FLUX_PALETTE[index].srgb_hex
            || Math.abs(anchor.q_umol_m2_s - expectedQ) > Math.max(1e-12, expectedQ * 1e-12)) {
          throw new Error("Surface-flux physical legend threshold is incompatible.");
        }
      });
      const clipping = legend.clipping;
      if (!Number.isSafeInteger(clipping?.below_count) || clipping.below_count < 0
          || !Number.isSafeInteger(clipping?.above_count) || clipping.above_count < 0
          || clipping.below_count + clipping.above_count > patchCount
          || clipping.lower_bound_z !== anchors[0]
          || clipping.upper_bound_z !== anchors.at(-1)
          || !finiteFraction(clipping.below_physical_area_fraction)
          || !finiteFraction(clipping.above_physical_area_fraction)
          || !Number.isFinite(clipping.below_physical_area_m2)
          || clipping.below_physical_area_m2 < 0
          || !Number.isFinite(clipping.above_physical_area_m2)
          || clipping.above_physical_area_m2 < 0
          || !Number.isFinite(clipping.total_physical_one_sided_area_m2)
          || clipping.total_physical_one_sided_area_m2 <= 0
          || clipping.below_physical_area_m2 + clipping.above_physical_area_m2
            > clipping.total_physical_one_sided_area_m2 + 1e-12
          || Math.abs(
            clipping.below_physical_area_fraction
              - clipping.below_physical_area_m2
                / clipping.total_physical_one_sided_area_m2,
          ) > 1e-12
          || Math.abs(
            clipping.above_physical_area_fraction
              - clipping.above_physical_area_m2
                / clipping.total_physical_one_sided_area_m2,
          ) > 1e-12
          || clipping.raw_values_modified_by_clipping !== false) {
        throw new Error("Surface-flux clipping statistics are incompatible.");
      }
    }
  }
}

function validateLegendsV2(legends, family, reference, patchCount, palettes) {
  const thresholdRule = "q_anchor[k] = u_anchor * gamma[k] * R";
  for (const metric of METRICS) {
    const metricSuffix = metric.replace("_par", "");
    const gamma = family.frontLocalPatchMetrics[metric];
    const front = legends?.[metric]?.front;
    const frontBeta = family.scalarBetas[`front_${metricSuffix}`];
    if (front?.metric !== metric || front.side !== "front"
        || front.units !== "umol/m^2/s" || front.variable !== "u"
        || front.gamma_profile_id !== family.profile.profile_id
        || front.coefficient_hash !== gamma.float64_little_endian_sha256
        || front.physical_threshold_rule !== thresholdRule
        || front.beta_provenance?.beta !== frontBeta
        || front.beta_provenance?.used_for_coloring !== false
        || !String(front.u_equals_one_meaning).includes("not a leaf target")
        || !String(front.u_equals_one_meaning).includes("not raw-q uniformity")
        || !validLegendCommon(front, metric, "front", reference)
        || !Array.isArray(front.anchors)
        || front.anchors.length !== FRONT_LOCAL_PATCH_ANCHORS.length) {
      throw new Error(`Schema-v2 ${metric} front legend is incompatible.`);
    }
    const gammaMinimum = Math.min(...gamma.coefficients);
    const gammaMaximum = Math.max(...gamma.coefficients);
    front.anchors.forEach((anchor, index) => {
      const u = FRONT_LOCAL_PATCH_ANCHORS[index];
      const expectedMinimum = u * gammaMinimum * reference.value;
      const expectedMaximum = u * gammaMaximum * reference.value;
      if (anchor.u !== u
          || anchor.color_name !== palettes.front.colors[index].name
          || anchor.srgb_hex !== palettes.front.colors[index].srgb_hex
          || anchor.q_threshold_rule !== thresholdRule
          || !nearlyEqual(anchor.q_minimum_umol_m2_s, expectedMinimum)
          || !nearlyEqual(anchor.q_maximum_umol_m2_s, expectedMaximum)) {
        throw new Error("Schema-v2 front patch-specific threshold is incompatible.");
      }
    });
    validateClipping(front.clipping, patchCount, "u", FRONT_LOCAL_PATCH_ANCHORS);

    const back = legends?.[metric]?.back;
    const backBeta = family.scalarBetas[`back_${metricSuffix}`];
    if (back?.metric !== metric || back.side !== "back"
        || back.units !== "umol/m^2/s" || back.beta !== backBeta
        || !String(back.z_equals_one_meaning).includes("not a leaf target")
        || !validLegendCommon(back, metric, "back", reference)
        || !Array.isArray(back.anchors) || back.anchors.length !== BACK_ANCHORS.length) {
      throw new Error(`Schema-v2 ${metric} unchanged back legend is incompatible.`);
    }
    back.anchors.forEach((anchor, index) => {
      const z = BACK_ANCHORS[index];
      const expectedQ = z * backBeta * reference.value;
      if (anchor.z !== z || anchor.color_name !== palettes.back.colors[index].name
          || anchor.srgb_hex !== palettes.back.colors[index].srgb_hex
          || !nearlyEqual(anchor.q_umol_m2_s, expectedQ)) {
        throw new Error("Schema-v2 unchanged back threshold is incompatible.");
      }
    });
    validateClipping(back.clipping, patchCount, "z", BACK_ANCHORS);
  }
}

function validateLegendsV3(legends, reference, patchCount, palettes) {
  const rule = "q_anchor[k] = u_anchor * gamma[k] * R";
  for (const metric of METRICS) {
    for (const side of ["front", "back"]) {
      const legend = legends?.[metric]?.[side];
      const anchors = side === "front"
        ? FRONT_LOCAL_PATCH_ANCHORS : SCHEMA_V3_BACK_ANCHORS;
      const colors = side === "front" ? palettes.front.colors : palettes.back.colors;
      if (legend?.metric !== metric || legend.side !== side
          || legend.variable !== "u" || legend.coefficient_estimator !== D5_C3.estimator
          || legend.coefficient_count !== PATCHES_PER_PLANT
          || legend.all_cells_available !== true
          || legend.availability_mask_applied !== false
          || legend.physical_threshold_rule !== rule
          || !String(legend.u_equals_one_meaning).includes("not a leaf target")
          || !validLegendCommon(legend, metric, side, reference)
          || !Array.isArray(legend.anchors) || legend.anchors.length !== anchors.length) {
        throw new Error(`Schema-v3 ${metric}/${side} legend is incompatible.`);
      }
      legend.anchors.forEach((anchor, index) => {
        if (anchor.u !== anchors[index]
            || anchor.color_name !== colors[index].name
            || anchor.srgb_hex !== colors[index].srgb_hex
            || anchor.q_threshold_rule !== rule
            || !Number.isFinite(anchor.q_minimum_umol_m2_s)
            || !Number.isFinite(anchor.q_maximum_umol_m2_s)
            || anchor.q_minimum_umol_m2_s < 0
            || anchor.q_maximum_umol_m2_s < anchor.q_minimum_umol_m2_s) {
          throw new Error("Schema-v3 local-patch legend anchor is incompatible.");
        }
      });
      validateClipping(legend.clipping, patchCount, "u", anchors);
    }
  }
}

function validLegendCommon(legend, metric, side, reference) {
  return legend.metric === metric && legend.side === side
    && legend.reference?.value === reference.value
    && legend.reference?.units === reference.units
    && legend.reference?.value_kind === reference.value_kind
    && legend.reference?.operating_policy === reference.operating_policy
    && recordsEqual(legend.reference?.source_provenance, reference.source_provenance)
    && Number.isFinite(legend.raw_minimum_q) && legend.raw_minimum_q >= 0
    && Number.isFinite(legend.raw_maximum_q)
    && legend.raw_maximum_q >= legend.raw_minimum_q;
}

function validateClipping(clipping, patchCount, variable, anchors) {
  if (!Number.isSafeInteger(clipping?.below_count) || clipping.below_count < 0
      || !Number.isSafeInteger(clipping?.above_count) || clipping.above_count < 0
      || clipping.below_count + clipping.above_count > patchCount
      || clipping[`lower_bound_${variable}`] !== anchors[0]
      || clipping[`upper_bound_${variable}`] !== anchors.at(-1)
      || !finiteFraction(clipping.below_physical_area_fraction)
      || !finiteFraction(clipping.above_physical_area_fraction)
      || !Number.isFinite(clipping.below_physical_area_m2)
      || clipping.below_physical_area_m2 < 0
      || !Number.isFinite(clipping.above_physical_area_m2)
      || clipping.above_physical_area_m2 < 0
      || !Number.isFinite(clipping.total_physical_one_sided_area_m2)
      || clipping.total_physical_one_sided_area_m2 <= 0
      || clipping.below_physical_area_m2 + clipping.above_physical_area_m2
        > clipping.total_physical_one_sided_area_m2 + 1e-12
      || !nearlyEqual(
        clipping.below_physical_area_fraction,
        clipping.below_physical_area_m2 / clipping.total_physical_one_sided_area_m2,
      )
      || !nearlyEqual(
        clipping.above_physical_area_fraction,
        clipping.above_physical_area_m2 / clipping.total_physical_one_sided_area_m2,
      )
      || clipping.raw_values_modified_by_clipping !== false) {
    throw new Error("Surface-flux clipping statistics are incompatible.");
  }
}

function promotionContract(metadata, validated) {
  if (validated !== null) {
    if (validated?.metadata !== metadata || !validated.context) {
      throw new Error("Surface-flux promotion metadata identity is incompatible.");
    }
    const fresh = validateSurfaceFluxMetadata(metadata, validated.context);
    if (fresh.schemaVersion !== validated.schemaVersion) {
      throw new Error("Surface-flux schema changed after authentication.");
    }
    if (fresh.schemaVersion === 2) {
      if (validated[AUTHENTICATED_METADATA] !== true) {
        throw new Error("Schema-v2 local-patch coefficients are unauthenticated.");
      }
      for (const metric of METRICS) {
        const freshMetric = fresh.family.frontLocalPatchMetrics[metric];
        const authenticatedMetric = validated.family.frontLocalPatchMetrics[metric];
        if (freshMetric.float64_little_endian_sha256
              !== authenticatedMetric.float64_little_endian_sha256
            || !arraysEqual(freshMetric.coefficients, authenticatedMetric.coefficients)) {
          throw new Error("Front local-patch coefficients changed after authentication.");
        }
      }
    }
    if (fresh.schemaVersion === 3) {
      if (validated[AUTHENTICATED_METADATA] !== true
          || !validated.coefficients
          || fresh.family.coefficientFamily !== validated.family.coefficientFamily) {
        throw new Error("Schema-v3 display-calibration coefficients are unauthenticated.");
      }
    }
    return validated;
  }
  if (metadata?.schema_version !== 1) {
    throw new Error("Surface-flux metadata promotion requires authenticated metadata.");
  }
  // Retain the existing direct schema-v1 controller API while keeping its scalar semantics.
  validateCalibrationV1(metadata.calibration_provenance);
  const family = validateQualityFamilyV1(
    metadata.quality_family, metadata.selected_calibration_profile,
  );
  const reference = validateReference(metadata.reference);
  const palettes = validatePalettesV1(metadata.palette);
  validateFailurePolicy(metadata.failure_policy);
  return Object.freeze({ family, metadata, palettes, reference, schemaVersion: 1 });
}

function scalarBetas(contract) {
  if (contract.schemaVersion === 3) {
    return {
      front_incident: 1, back_incident: 1, front_absorbed: 1, back_absorbed: 1,
    };
  }
  return contract.schemaVersion === 2
    ? contract.family.scalarBetas : contract.family.profile.coefficients;
}

function deriveFrontLocalPatchDisplayValues(rawValues, plantCount, contract) {
  const display = new Float32Array(rawValues.length);
  const incidentGamma = contract.family.frontLocalPatchMetrics.incident_par.coefficients;
  const absorbedGamma = contract.family.frontLocalPatchMetrics.absorbed_par.coefficients;
  const betas = scalarBetas(contract);
  const reference = contract.reference.value;
  try {
    for (let plantIndex = 0; plantIndex < plantCount; plantIndex += 1) {
      for (let localPatchIndex = 0;
        localPatchIndex < PATCHES_PER_PLANT; localPatchIndex += 1) {
        const offset = globalPatchIndex(plantIndex, localPatchIndex, plantCount) * 4;
        display[offset] = rawValues[offset] / (incidentGamma[localPatchIndex] * reference);
        display[offset + 1] = rawValues[offset + 1] / (betas.back_incident * reference);
        display[offset + 2] = rawValues[offset + 2]
          / (absorbedGamma[localPatchIndex] * reference);
        display[offset + 3] = rawValues[offset + 3] / (betas.back_absorbed * reference);
        for (let channel = 0; channel < 4; channel += 1) {
          if (!Number.isFinite(display[offset + channel]) || display[offset + channel] < 0) {
            throw new Error("Surface-flux CPU display normalization is non-finite.");
          }
        }
      }
    }
  } catch (error) {
    display.fill(0);
    throw error;
  }
  return display;
}

export function deriveAllSurfaceDisplayValues(rawValues, plantCount, contract) {
  if (!(rawValues instanceof Float32Array)
      || rawValues.length !== plantCount * PATCHES_PER_PLANT * 4
      || contract?.schemaVersion !== 3) {
    throw new Error("Schema-v3 raw values or normalization contract are incompatible.");
  }
  const family = contract.coefficients?.[contract.family.coefficientFamily];
  const frontIncident = family?.front_incident;
  const frontAbsorbed = family?.front_absorbed;
  const backIncident = family?.back_incident;
  const backAbsorbed = family?.back_absorbed;
  if ([frontIncident, frontAbsorbed, backIncident, backAbsorbed].some(
    (values) => !Array.isArray(values) || !Object.isFrozen(values)
      || values.length !== PATCHES_PER_PLANT,
  )) {
    throw new Error("Schema-v3 selected coefficient blocks are incomplete.");
  }
  const reference = contract.reference.value;
  const display = new Float32Array(rawValues.length);
  try {
    for (let plantIndex = 0; plantIndex < plantCount; plantIndex += 1) {
      for (let patchIndex = 0; patchIndex < PATCHES_PER_PLANT; patchIndex += 1) {
        const offset = globalPatchIndex(plantIndex, patchIndex, plantCount) * 4;
        // Raw RGBA is FI, BI, FA, BA.  Resource blocks are FI, FA, BI, BA.
        display[offset] = rawValues[offset] / (frontIncident[patchIndex] * reference);
        display[offset + 1] = rawValues[offset + 1]
          / (backIncident[patchIndex] * reference);
        display[offset + 2] = rawValues[offset + 2]
          / (frontAbsorbed[patchIndex] * reference);
        display[offset + 3] = rawValues[offset + 3]
          / (backAbsorbed[patchIndex] * reference);
        for (let channel = 0; channel < 4; channel += 1) {
          if (!Number.isFinite(display[offset + channel]) || display[offset + channel] < 0) {
            throw new Error("Schema-v3 surface-flux normalization is non-finite.");
          }
        }
      }
    }
  } catch (error) {
    display.fill(0);
    throw error;
  }
  return display;
}

function validateRawValues(values) {
  for (let index = 0; index < values.length; index += 1) {
    if (!Number.isFinite(values[index]) || values[index] < 0) {
      throw new Error("Surface-flux promotion found a non-finite or negative raw q value.");
    }
  }
}

function validateFailurePolicy(policy) {
  if (policy?.invalid_or_unavailable !== "neutral plant material"
      || policy.clear_stale_gpu_resources !== true
      || policy.partial_scientific_coloring_allowed !== false) {
    throw new Error("Surface-flux failure policy is incompatible.");
  }
}

function frozenPalettes(frontAnchors, frontColors, frontVariable,
  backAnchors, backColors, backVariable) {
  return Object.freeze({
    front: Object.freeze({
      anchors: Object.freeze([...frontAnchors]),
      colors: Object.freeze(frontColors.map((color) => Object.freeze({ ...color }))),
      variable: frontVariable,
    }),
    back: Object.freeze({
      anchors: Object.freeze([...backAnchors]),
      colors: Object.freeze(backColors.map((color) => Object.freeze({ ...color }))),
      variable: backVariable,
    }),
  });
}

function freezeJsonValue(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) freezeJsonValue(child);
    Object.freeze(value);
  }
  return value;
}

function float64LittleEndianBytes(coefficients) {
  const bytes = new ArrayBuffer(coefficients.length * 8);
  const view = new DataView(bytes);
  coefficients.forEach((coefficient, index) => view.setFloat64(index * 8, coefficient, true));
  return bytes;
}

function injectAfter(source, marker, addition) {
  if (typeof source !== "string" || !source.includes(marker)) {
    throw new Error(`Plant PBR shader marker is unavailable: ${marker}`);
  }
  return source.replace(marker, `${marker}\n${addition}`);
}

async function fetchBytes(url, signal) {
  const response = await fetch(url, {
    cache: "no-store", credentials: "same-origin", signal,
  });
  if (!response.ok) throw new Error(`${url.pathname} returned HTTP ${response.status}`);
  return response.arrayBuffer();
}

function assertObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object.`);
  }
}

function assertExactFields(value, expectedFields, label) {
  assertObject(value, label);
  if (Object.keys(value).sort().join(",") !== [...expectedFields].sort().join(",")) {
    throw new Error(`${label} field inventory is incompatible.`);
  }
}

function assertSha256(value, label) {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
    throw new Error(`${label} SHA-256 is invalid.`);
  }
}

function finiteFraction(value) {
  return Number.isFinite(value) && value >= 0 && value <= 1;
}

function recordsEqual(value, expected) {
  return value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join(",") === Object.keys(expected).sort().join(",")
    && Object.entries(expected).every(([name, item]) => value[name] === item);
}

function arraysEqual(left, right) {
  return left?.length === right?.length
    && left.every((value, index) => value === right[index]);
}

function nearlyEqual(actual, expected) {
  return Number.isFinite(actual) && Number.isFinite(expected)
    && Math.abs(actual - expected) <= Math.max(1e-12, Math.abs(expected) * 1e-12);
}

const VERTEX_DECLARATIONS = `
attribute float _patch_index;
uniform sampler2D surfaceFluxValues;
uniform vec2 surfaceFluxTextureSize;
varying vec4 vSurfaceFluxQ;
`;

const VERTEX_LOOKUP = `
float surfaceFluxLocalPatchIndex = floor(_patch_index + 0.5);
vec2 surfaceFluxUv = (vec2(surfaceFluxLocalPatchIndex, float(gl_InstanceID)) + vec2(0.5))
  / surfaceFluxTextureSize;
vSurfaceFluxQ = texture2D(surfaceFluxValues, surfaceFluxUv);
`;

const FRAGMENT_DECLARATIONS = `
uniform vec4 surfaceFluxBetas;
uniform float surfaceFluxReference;
uniform int surfaceFluxMetric;
uniform int surfaceFluxSideMode;
uniform int surfaceFluxEnabled;
uniform int surfaceFluxValuesAreRelative;
uniform float surfaceFluxFrontAnchors[8];
uniform float surfaceFluxBackAnchors[8];
uniform vec3 surfaceFluxFrontPalette[8];
uniform vec3 surfaceFluxBackPalette[8];
varying vec4 vSurfaceFluxQ;

vec3 mapSurfaceFluxColor(float relativeValue, bool frontSide) {
  float a0 = frontSide ? surfaceFluxFrontAnchors[0] : surfaceFluxBackAnchors[0];
  float a1 = frontSide ? surfaceFluxFrontAnchors[1] : surfaceFluxBackAnchors[1];
  float a2 = frontSide ? surfaceFluxFrontAnchors[2] : surfaceFluxBackAnchors[2];
  float a3 = frontSide ? surfaceFluxFrontAnchors[3] : surfaceFluxBackAnchors[3];
  float a4 = frontSide ? surfaceFluxFrontAnchors[4] : surfaceFluxBackAnchors[4];
  float a5 = frontSide ? surfaceFluxFrontAnchors[5] : surfaceFluxBackAnchors[5];
  float a6 = frontSide ? surfaceFluxFrontAnchors[6] : surfaceFluxBackAnchors[6];
  float a7 = frontSide ? surfaceFluxFrontAnchors[7] : surfaceFluxBackAnchors[7];
  vec3 c0 = frontSide ? surfaceFluxFrontPalette[0] : surfaceFluxBackPalette[0];
  vec3 c1 = frontSide ? surfaceFluxFrontPalette[1] : surfaceFluxBackPalette[1];
  vec3 c2 = frontSide ? surfaceFluxFrontPalette[2] : surfaceFluxBackPalette[2];
  vec3 c3 = frontSide ? surfaceFluxFrontPalette[3] : surfaceFluxBackPalette[3];
  vec3 c4 = frontSide ? surfaceFluxFrontPalette[4] : surfaceFluxBackPalette[4];
  vec3 c5 = frontSide ? surfaceFluxFrontPalette[5] : surfaceFluxBackPalette[5];
  vec3 c6 = frontSide ? surfaceFluxFrontPalette[6] : surfaceFluxBackPalette[6];
  vec3 c7 = frontSide ? surfaceFluxFrontPalette[7] : surfaceFluxBackPalette[7];
  if (relativeValue <= a0) return c0;
  if (relativeValue <= a1) return mix(c0, c1, (relativeValue-a0)/(a1-a0));
  if (relativeValue <= a2) return mix(c1, c2, (relativeValue-a1)/(a2-a1));
  if (relativeValue <= a3) return mix(c2, c3, (relativeValue-a2)/(a3-a2));
  if (relativeValue <= a4) return mix(c3, c4, (relativeValue-a3)/(a4-a3));
  if (relativeValue <= a5) return mix(c4, c5, (relativeValue-a4)/(a5-a4));
  if (relativeValue <= a6) return mix(c5, c6, (relativeValue-a5)/(a6-a5));
  if (relativeValue <= a7) return mix(c6, c7, (relativeValue-a6)/(a7-a6));
  return c7;
}
`;

const FRAGMENT_COLORING = `
if (surfaceFluxEnabled == 1) {
  bool frontSide = gl_FrontFacing;
  bool selectedSide = surfaceFluxSideMode == 0
    || (surfaceFluxSideMode == 1 && frontSide)
    || (surfaceFluxSideMode == 2 && !frontSide);
  if (selectedSide) {
    float relativeValue = surfaceFluxMetric == 0
      ? (frontSide ? vSurfaceFluxQ.r : vSurfaceFluxQ.g)
      : (frontSide ? vSurfaceFluxQ.b : vSurfaceFluxQ.a);
    if (surfaceFluxValuesAreRelative == 0) {
      float beta = surfaceFluxMetric == 0
        ? (frontSide ? surfaceFluxBetas.x : surfaceFluxBetas.y)
        : (frontSide ? surfaceFluxBetas.z : surfaceFluxBetas.w);
      relativeValue = relativeValue / (beta * surfaceFluxReference);
    }
    diffuseColor.rgb = mapSurfaceFluxColor(relativeValue, frontSide);
  }
}
`;

const FRAGMENT_COLORING_D4 = `
if (surfaceFluxEnabled == 1) {
  bool frontSide = gl_FrontFacing;
  bool selectedSide = surfaceFluxSideMode == 0
    || (surfaceFluxSideMode == 1 && frontSide)
    || (surfaceFluxSideMode == 2 && !frontSide);
  if (selectedSide) {
    float relativeValue = surfaceFluxMetric == 0
      ? (frontSide ? vSurfaceFluxQ.r : vSurfaceFluxQ.g)
      : (frontSide ? vSurfaceFluxQ.b : vSurfaceFluxQ.a);
    if (surfaceFluxValuesAreRelative == 0) {
      float beta = surfaceFluxMetric == 0
        ? (frontSide ? surfaceFluxBetas.x : surfaceFluxBetas.y)
        : (frontSide ? surfaceFluxBetas.z : surfaceFluxBetas.w);
      relativeValue = relativeValue / (beta * surfaceFluxReference);
    }
    diffuseColor.rgb = mapSurfaceFluxColor(relativeValue, frontSide)
      * leafDetail.fluxLuminance;
    leafFluxColoredSide = true;
  }
}
`;
