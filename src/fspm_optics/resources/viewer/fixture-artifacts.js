import {
  assertSha256,
  decodeJson,
  fetchArtifact,
  fetchBytes,
  validateMountingHeight,
  verifyHash,
} from "./artifacts.js";

export const FIXTURE_CATALOG_SCHEMA = "fspm-optics.run-fixture-catalog";
export const FIXTURE_MATRIX_STRIDE_BYTES = 64;

const APPROVED_EXTENSIONS = Object.freeze(["KHR_mesh_quantization"]);
const PROPOSED_FIXTURE_POLICIES = Object.freeze({
  standalone_modules: "proposed_standalone_module_instances_with_alignment_lattice_v2",
  linear: "proposed_linear_fixture_assemblies_v1",
  legacy: "proposed_tile_fill_connect_fixture_assemblies_v1",
});
const PROPOSED_MODULE_PATTERNS = Object.freeze({
  full: "centered_square_full_v1",
  reduced_one_ring: "centered_square_reduced_one_ring_v1",
});
export const ALIGNMENT_LATTICE_DIAMETER_M = 0.0015875;
export const ALIGNMENT_LATTICE_POLICY = Object.freeze({
  schemaId: "fspm-optics.proposed-standalone-alignment-lattice",
  schemaVersion: 1,
  topologyId: "standalone_module_integer_staggered_triangular_graph_v1",
  geometryId: "straight_taut_free_span_cylinders_v1",
  attachmentId: "proposed_led_module_authenticated_rear_plane_v1",
  materialId: "fixture_body_anodized_aluminum_v2",
  assetId: "proposed-led-module-v1",
  assetSha256: "f910a7fc512635bcb3a48d7b546175ef13623135836a76eeb625b2ca12cd5445",
  rearOffsetM: 0.034800000889971816,
});

export const APPROVED_FIXTURE_ASSETS = Object.freeze({
  "proposed-centerpiece-v1": approved(
    "proposed", "centerpiece", "fixtures/proposed/centerpiece.glb", 362744,
    "76daa0290eafb3947b30489d304287b25dab998f847ba5d5a8163775cb38c1ba",
  ),
  "proposed-linear2-v1": approved(
    "proposed", "linear2", "fixtures/proposed/linear2.glb", 165832,
    "814de19659cff0130b508f197a2194383748eee191f10927524229aabd5c288d",
  ),
  "proposed-linear3-v1": approved(
    "proposed", "linear3", "fixtures/proposed/linear3.glb", 233288,
    "e9cb018d9ce5bb6babe59044d88639c6620d6018eb22e71f6fc404c250145d93",
  ),
  "proposed-corner3-v1": approved(
    "proposed", "corner3", "fixtures/proposed/corner3.glb", 238648,
    "e30f98457b1af1725843ed0c42edfc9d22516445c602f4edcc8b8ed116c7d243",
  ),
  "proposed-linear4-v1": approved(
    "proposed", "linear4", "fixtures/proposed/linear4.glb", 353128,
    "1f77749ae1c3dda08cf8a521734f9aea5e54b64df68b963341bdca5407ca6809",
  ),
  "proposed-l-v1": approved(
    "proposed", "L", "fixtures/proposed/l.glb", 363232,
    "ea1c9ea94aa08c30a655458c0a01e74a3687e2e4333203d10961520d6634730e",
  ),
  "proposed-reverse-l-v1": approved(
    "proposed", "reverse_L", "fixtures/proposed/reverse_l.glb", 363064,
    "eb87a439e99ae95ac5ad73ea96ba4de9bbcd03609e45355d4e92a8210034badd",
  ),
  "proposed-led-module-v1": approved(
    "proposed", "standalone_module", "fixtures/proposed/led_module.glb", 88540,
    "f910a7fc512635bcb3a48d7b546175ef13623135836a76eeb625b2ca12cd5445",
  ),
  "conventional-led-8-bar-v1": approved(
    "conventional", "conventional_led_8_bar", "fixtures/conventional/conventional_led_8_bar.glb", 109392,
    "0d640d8e20bfdc213d3722dc44652979366b7c19ef153fff10372fe66051af53",
  ),
  "hps-housing-v3": approved(
    "hps", "hps_1000w_fixture", "fixtures/hps/hps.glb", 353160,
    "083df1475e84e9552d5ec548fc34669092b5ba4284fcf33ebf7dbb2f26439d7d",
    ["BLEND", "OPAQUE"], [0.349999994, 1.0],
    {
      identitySha256: "d83577f6bb7503ff8865dbe55122bfd37d5ea0e0f2c1d9dc130a7a98f9598293",
      metersPerAssetUnit: 0.001,
      dimensionCorrectionScaleXyz: [1, 1, 1],
      placementPlaneLocalYmm: -248.92,
      placementCorrectionLocalXyzMm: [0, 248.92, 0],
      pivotContract: (
        "housing_bottom_shifted_to_separate_scientific_luminous_aperture_plane"
      ),
    },
  ),
});

export async function loadValidatedFixtureArtifacts(
  scene,
  sceneUrl = "./scene.v1.json",
) {
  const sceneBase = new URL("./", new URL(sceneUrl, location.href));
  const catalogUrl = new URL(scene.fixtures.catalog, sceneBase);
  const catalogBytes = await fetchBytes(catalogUrl);
  if (catalogBytes.byteLength !== scene.fixtures.catalog_byte_length) {
    throw new Error("Fixture catalog byte length is invalid.");
  }
  await verifyHash(
    catalogBytes,
    scene.fixtures.catalog_sha256,
    "fixture catalog",
  );
  const catalog = validateFixtureCatalog(
    decodeJson(catalogBytes, "fixture catalog"),
    scene,
  );
  const catalogBase = new URL("./", catalogUrl);
  const groups = await Promise.all(catalog.asset_groups.map(async (group) => {
    const [glbBytes, matrixBytes] = await Promise.all([
      fetchArtifact(catalogBase, group.asset),
      fetchArtifact(catalogBase, {
        filename: group.instance_matrices.filename,
        byte_size: group.instance_matrices.byte_length,
        sha256: group.instance_matrices.sha256,
      }),
    ]);
    const approvedAsset = APPROVED_FIXTURE_ASSETS[group.display_asset_id];
    validateEmbeddedFixtureGlb(glbBytes, approvedAsset);
    const matrices = parseFixtureMatrices(matrixBytes, group.instance_matrices);
    return Object.freeze({
      approvedAsset,
      catalogGroup: group,
      glbBytes,
      matrices,
    });
  }));
  return Object.freeze({ catalog, groups: Object.freeze(groups) });
}

export function validateFixtureCatalog(catalog, scene) {
  assertObject(catalog, "fixture catalog");
  const hasProposedMode = Object.hasOwn(catalog, "proposed_layout_mode");
  const hasFixturePolicy = Object.hasOwn(catalog, "fixture_policy_id");
  const hasRingMode = Object.hasOwn(catalog, "proposed_ring_mode");
  const hasModulePattern = Object.hasOwn(catalog, "module_pattern_id");
  const hasAlignmentLattice = Object.hasOwn(catalog, "alignment_lattice");
  const hasAlignmentIdentity = Object.hasOwn(
    catalog, "alignment_lattice_identity_sha256",
  );
  const catalogFields = [
    "asset_group_count", "asset_groups", "authoritative_layout_sha256",
    "browser_transform_inference", "fixture_count", "fixture_plan",
    "fixture_plan_sha256", "ordering", "requested_room_ft", "run",
    "schema_id", "schema_version", "transform_authority",
  ];
  if (catalog.schema_version === 2) catalogFields.push("mounting_height");
  if (hasProposedMode || hasFixturePolicy) {
    catalogFields.push("proposed_layout_mode", "fixture_policy_id");
  }
  if (hasRingMode || hasModulePattern) {
    catalogFields.push("proposed_ring_mode", "module_pattern_id");
  }
  if (hasAlignmentLattice || hasAlignmentIdentity) {
    catalogFields.push("alignment_lattice", "alignment_lattice_identity_sha256");
  }
  assertFields(catalog, catalogFields, "fixture catalog");
  if (catalog.schema_id !== FIXTURE_CATALOG_SCHEMA
      || ![1, 2].includes(catalog.schema_version)) {
    throw new Error("Fixture catalog schema is incompatible.");
  }
  if ((catalog.schema_version === 2) !== [3, 4].includes(scene.schema_version)) {
    throw new Error("Fixture catalog and scene mounting schemas disagree.");
  }
  if (catalog.schema_version === 2) {
    validateMountingHeight(catalog.mounting_height);
    if (JSON.stringify(catalog.mounting_height)
        !== JSON.stringify(scene.mounting_height)) {
      throw new Error("Fixture catalog mounting height disagrees with the scene.");
    }
  }
  assertFields(catalog.run, ["run_id", "system_id"], "fixture catalog run");
  if (catalog.run.run_id !== scene.run.run_id
      || catalog.run.system_id !== scene.run.system_id) {
    throw new Error("Fixture catalog run or system identity is incompatible.");
  }
  const expectedFixturePolicy = PROPOSED_FIXTURE_POLICIES[catalog.proposed_layout_mode];
  const expectedModulePattern = PROPOSED_MODULE_PATTERNS[catalog.proposed_ring_mode];
  if ((hasProposedMode || hasFixturePolicy)
      && (catalog.run.system_id !== "proposed"
        || expectedFixturePolicy === undefined
        || catalog.fixture_policy_id !== expectedFixturePolicy)) {
    throw new Error("Fixture catalog Proposed layout identity is incompatible.");
  }
  if (hasRingMode !== hasModulePattern
      || (hasRingMode
        && (!hasProposedMode
          || !hasFixturePolicy
          || catalog.run.system_id !== "proposed"
          || expectedModulePattern === undefined
          || catalog.module_pattern_id !== expectedModulePattern
          || (catalog.proposed_ring_mode === "reduced_one_ring"
            && catalog.proposed_layout_mode !== "standalone_modules")))) {
    throw new Error("Fixture catalog Proposed ring identity is incompatible.");
  }
  const standalone = catalog.proposed_layout_mode === "standalone_modules";
  if (hasAlignmentLattice !== hasAlignmentIdentity
      || hasAlignmentLattice !== standalone) {
    throw new Error("Fixture catalog alignment-lattice presence is incompatible.");
  }
  if (standalone) {
    validateAlignmentLattice(catalog.alignment_lattice, catalog.fixture_count);
    assertSha256(catalog.alignment_lattice_identity_sha256, "alignment lattice");
    if (catalog.alignment_lattice.identity_sha256
        !== catalog.alignment_lattice_identity_sha256) {
      throw new Error("Fixture catalog alignment-lattice identity disagrees.");
    }
  }
  assertFields(catalog.requested_room_ft, ["length", "width"], "fixture room");
  if (catalog.requested_room_ft.length !== scene.requested_room.length_ft
      || catalog.requested_room_ft.width !== scene.requested_room.width_ft) {
    throw new Error("Fixture catalog requested room is incompatible.");
  }
  for (const [value, label] of [
    [catalog.authoritative_layout_sha256, "fixture layout"],
    [catalog.fixture_plan_sha256, "fixture plan"],
  ]) assertSha256(value, label);
  if (catalog.authoritative_layout_sha256
        !== scene.fixtures.authoritative_layout_sha256
      || catalog.fixture_plan_sha256 !== scene.fixtures.fixture_plan_sha256) {
    throw new Error("Fixture catalog layout or plan identity is incompatible.");
  }
  if (catalog.browser_transform_inference !== false
      || catalog.transform_authority
        !== "server_resolved_from_authoritative_layout_and_immutable_asset_registry"
      || catalog.ordering
        !== "authoritative fixture order within each stable asset group") {
    throw new Error("Fixture catalog transform authority is incompatible.");
  }
  if (!Number.isSafeInteger(catalog.fixture_count) || catalog.fixture_count <= 0
      || !Number.isSafeInteger(catalog.asset_group_count)
      || catalog.asset_group_count <= 0
      || catalog.fixture_count !== scene.fixtures.fixture_count
      || catalog.asset_group_count !== scene.fixtures.asset_group_count
      || !Array.isArray(catalog.asset_groups)
      || catalog.asset_groups.length !== catalog.asset_group_count) {
    throw new Error("Fixture catalog counts are incompatible.");
  }
  const plan = validateFixturePlan(catalog.fixture_plan, catalog, scene);
  const seenAssets = new Set();
  const seenAssetPaths = new Set();
  const seenMatrixPaths = new Set();
  const seenFixtures = new Set();
  for (const group of catalog.asset_groups) {
    validateFixtureGroup(group, catalog, scene);
    if (seenAssets.has(group.display_asset_id)
        || seenAssetPaths.has(group.asset.filename)
        || seenMatrixPaths.has(group.instance_matrices.filename)) {
      throw new Error("Fixture catalog repeats an asset or artifact request.");
    }
    seenAssets.add(group.display_asset_id);
    seenAssetPaths.add(group.asset.filename);
    seenMatrixPaths.add(group.instance_matrices.filename);
    for (const fixtureId of group.ordered_fixture_ids) {
      if (seenFixtures.has(fixtureId)) {
        throw new Error("Fixture catalog repeats a fixture identity.");
      }
      seenFixtures.add(fixtureId);
      const record = plan.byId.get(fixtureId);
      if (!record
          || record.display_asset_id !== group.display_asset_id
          || record.display_fixture_type !== group.display_fixture_type) {
        throw new Error("Fixture asset grouping disagrees with the server fixture plan.");
      }
    }
  }
  if (seenFixtures.size !== catalog.fixture_count
      || plan.byId.size !== catalog.fixture_count
      || [...plan.byId].some(([fixtureId]) => !seenFixtures.has(fixtureId))) {
    throw new Error("Fixture catalog fixture inventory is incomplete.");
  }
  return catalog;
}

export function parseFixtureMatrices(bytes, record) {
  if (!(bytes instanceof ArrayBuffer)) {
    throw new Error("Fixture matrix artifact must be an ArrayBuffer.");
  }
  assertFields(record, [
    "byte_length", "byte_order", "component_type", "coordinate_space", "count",
    "filename", "matrix_convention", "record_layout", "scientific_to_viewer",
    "sha256", "stride_bytes",
  ], "fixture matrix record");
  if (record.component_type !== "float32"
      || record.byte_order !== "little-endian"
      || record.record_layout !== "matrix4_column_major"
      || record.matrix_convention
        !== "column vectors; viewer_position = matrix * packaged_glb_scene_position"
      || record.coordinate_space !== "viewer right-handed meters, Y-up"
      || record.scientific_to_viewer !== "(x, y, z) -> (x, z, -y)"
      || record.stride_bytes !== FIXTURE_MATRIX_STRIDE_BYTES
      || !Number.isSafeInteger(record.count) || record.count <= 0
      || record.byte_length !== record.count * FIXTURE_MATRIX_STRIDE_BYTES
      || bytes.byteLength !== record.byte_length) {
    throw new Error("Fixture matrix length, count, type, or convention is invalid.");
  }
  const view = new DataView(bytes);
  const values = new Float32Array(record.count * 16);
  for (let index = 0; index < values.length; index += 1) {
    values[index] = view.getFloat32(index * 4, true);
    if (!Number.isFinite(values[index])) {
      throw new Error("Fixture matrices contain a non-finite value.");
    }
  }
  return Object.freeze({ count: record.count, values });
}

export function validateEmbeddedFixtureGlb(bytes, approvedAsset) {
  if (!(bytes instanceof ArrayBuffer) || bytes.byteLength < 28 || !approvedAsset) {
    throw new Error("Fixture GLB is truncated or unsupported.");
  }
  const view = new DataView(bytes);
  if (view.getUint32(0, true) !== 0x46546c67
      || view.getUint32(4, true) !== 2
      || view.getUint32(8, true) !== bytes.byteLength) {
    throw new Error("Fixture GLB header is incompatible.");
  }
  const jsonLength = view.getUint32(12, true);
  if (view.getUint32(16, true) !== 0x4e4f534a
      || 20 + jsonLength + 8 > bytes.byteLength) {
    throw new Error("Fixture GLB JSON chunk is incompatible.");
  }
  let document;
  try {
    const text = new TextDecoder("utf-8", { fatal: true })
      .decode(new Uint8Array(bytes, 20, jsonLength)).replace(/[\0 ]+$/u, "");
    document = JSON.parse(text);
  } catch (error) {
    throw new Error("Fixture GLB JSON is malformed.", { cause: error });
  }
  const binaryOffset = 20 + jsonLength;
  const binaryLength = view.getUint32(binaryOffset, true);
  if (view.getUint32(binaryOffset + 4, true) !== 0x004e4942
      || binaryOffset + 8 + binaryLength !== bytes.byteLength) {
    throw new Error("Fixture GLB binary chunk is incompatible.");
  }
  assertObject(document, "fixture GLB document");
  const used = validateStringArray(document.extensionsUsed ?? [], "used extensions");
  const required = validateStringArray(
    document.extensionsRequired ?? [], "required extensions",
  );
  const embedded = embeddedExtensionNames(document);
  if ([...used, ...required, ...embedded]
    .some((name) => !APPROVED_EXTENSIONS.includes(name))) {
    throw new Error("Fixture GLB uses an unapproved extension.");
  }
  if (document.animations || document.cameras || document.images?.length
      || embedded.has("KHR_lights_punctual")) {
    throw new Error("Fixture GLB contains prohibited scene content.");
  }
  if (findUri(document)) {
    throw new Error("Fixture GLB secondary resource URIs are prohibited.");
  }
  if (!Array.isArray(document.buffers) || document.buffers.length !== 1
      || !sameFields(document.buffers[0], ["byteLength"])
      || document.buffers[0].byteLength !== binaryLength) {
    throw new Error("Fixture GLB must contain one declared embedded buffer.");
  }
  if (!Array.isArray(document.meshes) || document.meshes.length === 0
      || !Array.isArray(document.nodes) || document.nodes.length === 0
      || !Array.isArray(document.materials) || document.materials.length === 0) {
    throw new Error("Fixture GLB render content is incomplete.");
  }
  if (document.meshes.some((mesh) => !Array.isArray(mesh?.primitives)
      || mesh.primitives.length === 0
      || mesh.primitives.some((primitive) => (
        ![undefined, 4].includes(primitive?.mode)
        || !Number.isSafeInteger(primitive.material)
        || primitive.material < 0
        || primitive.material >= document.materials.length
      )))) {
    throw new Error("Fixture GLB mesh primitives are incompatible.");
  }
  const modes = [...new Set(document.materials.map((material) => {
    assertObject(material, "fixture material");
    if (material.doubleSided !== true) {
      throw new Error("Fixture GLB material sidedness is incompatible.");
    }
    return material.alphaMode ?? "OPAQUE";
  }))].sort();
  const alphaFactors = [...new Set(document.materials.map((material) => (
    material.pbrMetallicRoughness?.baseColorFactor?.[3] ?? 1.0
  )))].sort((left, right) => left - right);
  if (JSON.stringify(modes) !== JSON.stringify([...approvedAsset.alphaModes].sort())
      || JSON.stringify(alphaFactors)
        !== JSON.stringify([...approvedAsset.alphaFactors].sort((a, b) => a - b))) {
    throw new Error("Fixture GLB material alpha contract is incompatible.");
  }
  return document;
}

export function combineAuthoritativeBounds(scene, fixtureBounds) {
  const values = [
    [scene.room_bounds.minimum_xz[0], 0, scene.room_bounds.minimum_xz[1]],
    [scene.room_bounds.maximum_xz[0], 0, scene.room_bounds.maximum_xz[1]],
    scene.plant_bounds.minimum_xyz,
    scene.plant_bounds.maximum_xyz,
    ...scene.reference_plane.vertices_xyz,
  ];
  if (fixtureBounds) {
    values.push(fixtureBounds.minimum_xyz, fixtureBounds.maximum_xyz);
  }
  if (values.some((vector) => !Array.isArray(vector) || vector.length !== 3
      || vector.some((value) => !Number.isFinite(value)))) {
    throw new Error("Combined camera bounds contain an invalid component.");
  }
  return {
    minimum_xyz: [0, 1, 2].map((axis) => Math.min(...values.map((item) => item[axis]))),
    maximum_xyz: [0, 1, 2].map((axis) => Math.max(...values.map((item) => item[axis]))),
  };
}

function validateFixturePlan(plan, catalog, scene) {
  const hasProposedMode = Object.hasOwn(plan, "proposed_layout_mode");
  const hasFixturePolicy = Object.hasOwn(plan, "fixture_policy_id");
  const hasRingMode = Object.hasOwn(plan, "proposed_ring_mode");
  const hasModulePattern = Object.hasOwn(plan, "module_pattern_id");
  const hasAlignmentIdentity = Object.hasOwn(
    plan, "alignment_lattice_identity_sha256",
  );
  const planFields = [
    "authoritative_layout_sha256", "display_classification_policy", "fixtures",
    "ordering", "placement_inference", "requested_room_ft", "run_id", "system_id",
  ];
  if (catalog.schema_version === 2) planFields.push("mounting_height_sha256");
  if (hasProposedMode || hasFixturePolicy) {
    planFields.push("proposed_layout_mode", "fixture_policy_id");
  }
  if (hasRingMode || hasModulePattern) {
    planFields.push("proposed_ring_mode", "module_pattern_id");
  }
  if (hasAlignmentIdentity) planFields.push("alignment_lattice_identity_sha256");
  assertFields(plan, planFields, "fixture plan");
  assertFields(plan.requested_room_ft, ["length", "width"], "fixture plan room");
  const expectedClassificationPolicy = scene.run.system_id === "proposed"
    ? (catalog.proposed_layout_mode === "standalone_modules"
      ? "proposed_standalone_module_with_alignment_lattice_identity_v2"
      : "proposed_member_connector_topology_v2")
    : "system_fixed_display_asset_v1";
  if (plan.run_id !== scene.run.run_id || plan.system_id !== scene.run.system_id
      || plan.authoritative_layout_sha256 !== catalog.authoritative_layout_sha256
      || (catalog.schema_version === 2
        && plan.mounting_height_sha256 !== scene.fixtures.mounting_height_sha256)
      || plan.placement_inference !== false
      || plan.ordering !== "authoritative fixture order"
      || plan.display_classification_policy !== expectedClassificationPolicy
      || hasProposedMode !== Object.hasOwn(catalog, "proposed_layout_mode")
      || hasFixturePolicy !== Object.hasOwn(catalog, "fixture_policy_id")
      || plan.proposed_layout_mode !== catalog.proposed_layout_mode
      || plan.fixture_policy_id !== catalog.fixture_policy_id
      || hasRingMode !== Object.hasOwn(catalog, "proposed_ring_mode")
      || hasModulePattern !== Object.hasOwn(catalog, "module_pattern_id")
      || plan.proposed_ring_mode !== catalog.proposed_ring_mode
      || plan.module_pattern_id !== catalog.module_pattern_id
      || hasAlignmentIdentity
        !== Object.hasOwn(catalog, "alignment_lattice_identity_sha256")
      || plan.alignment_lattice_identity_sha256
        !== catalog.alignment_lattice_identity_sha256
      || plan.requested_room_ft?.length !== catalog.requested_room_ft.length
      || plan.requested_room_ft?.width !== catalog.requested_room_ft.width
      || !Array.isArray(plan.fixtures)
      || plan.fixtures.length !== catalog.fixture_count) {
    throw new Error("Fixture plan identity or ordering is incompatible.");
  }
  const byId = new Map();
  for (const record of plan.fixtures) {
    assertObject(record, "fixture plan record");
    if (typeof record.fixture_id !== "string" || !record.fixture_id
        || typeof record.display_asset_id !== "string"
        || typeof record.display_fixture_type !== "string"
        || byId.has(record.fixture_id)) {
      throw new Error("Fixture plan identity is malformed or repeated.");
    }
    const hpsPlacement = APPROVED_FIXTURE_ASSETS[record.display_asset_id]
      ?.placementContract;
    if ((catalog.run.system_id === "hps")
        !== (record.placement_contract_sha256 !== undefined)
        || (hpsPlacement
          && (record.placement_contract_sha256 !== hpsPlacement.identitySha256
            || record.placement_plane
              !== "luminous_aperture_plane_separate_from_housing"))) {
      throw new Error("Fixture plan placement identity is incompatible.");
    }
    byId.set(record.fixture_id, record);
  }
  return { byId };
}

export function validateAlignmentLattice(lattice, fixtureCount) {
  assertObject(lattice, "alignment lattice");
  assertFields(lattice, [
    "attachment_policy", "counts", "diameter_m", "geometry_id",
    "identity_sha256", "links", "material_id", "module_count", "radius_m",
    "schema_id", "schema_version", "topology_id", "transport_role",
  ], "alignment lattice");
  const policy = ALIGNMENT_LATTICE_POLICY;
  if (lattice.schema_id !== policy.schemaId
      || lattice.schema_version !== policy.schemaVersion
      || lattice.topology_id !== policy.topologyId
      || lattice.geometry_id !== policy.geometryId
      || lattice.diameter_m !== ALIGNMENT_LATTICE_DIAMETER_M
      || lattice.radius_m !== ALIGNMENT_LATTICE_DIAMETER_M / 2
      || lattice.material_id !== policy.materialId
      || lattice.transport_role !== "non_emitting_occluding_geometry"
      || lattice.module_count !== fixtureCount
      || !Array.isArray(lattice.links) || lattice.links.length === 0) {
    throw new Error("Alignment lattice policy or inventory is incompatible.");
  }
  assertFields(lattice.attachment_policy, [
    "authenticated_asset_id", "authenticated_glb_sha256", "endpoint_contact_only",
    "id", "module_envelope_m", "rear_plane_offset_from_aperture_m", "surface",
  ], "alignment lattice attachment policy");
  const attachment = lattice.attachment_policy;
  if (attachment.id !== policy.attachmentId
      || attachment.authenticated_asset_id !== policy.assetId
      || attachment.authenticated_glb_sha256 !== policy.assetSha256
      || attachment.surface !== "rear_most_authenticated_full_glb_plane"
      || attachment.rear_plane_offset_from_aperture_m !== policy.rearOffsetM
      || JSON.stringify(attachment.module_envelope_m) !== "[0.15,0.15]"
      || attachment.endpoint_contact_only !== true) {
    throw new Error("Alignment lattice attachment policy is incompatible.");
  }
  assertFields(lattice.counts, ["diagonal", "horizontal", "total"], "alignment counts");
  const pairs = new Set();
  const neighbors = Array.from({ length: fixtureCount }, () => []);
  let horizontal = 0;
  let diagonal = 0;
  let attachmentZ;
  for (let index = 0; index < lattice.links.length; index += 1) {
    const link = lattice.links[index];
    assertObject(link, "alignment link");
    assertFields(link, [
      "end_module_index", "end_xyz_m", "kind", "link_index",
      "start_module_index", "start_xyz_m",
    ], "alignment link");
    const start = link.start_module_index;
    const end = link.end_module_index;
    if (link.link_index !== index
        || !["horizontal", "diagonal"].includes(link.kind)
        || !Number.isSafeInteger(start) || !Number.isSafeInteger(end)
        || start < 0 || start >= fixtureCount || end < 0 || end >= fixtureCount
        || start === end
        || !validVector3(link.start_xyz_m) || !validVector3(link.end_xyz_m)) {
      throw new Error("Alignment lattice link is malformed.");
    }
    const pair = start < end ? `${start}:${end}` : `${end}:${start}`;
    if (pairs.has(pair)) throw new Error("Alignment lattice repeats a module pair.");
    pairs.add(pair);
    neighbors[start].push(end);
    neighbors[end].push(start);
    if (attachmentZ === undefined) attachmentZ = link.start_xyz_m[2];
    if (link.start_xyz_m[2] !== attachmentZ || link.end_xyz_m[2] !== attachmentZ) {
      throw new Error("Alignment lattice links are not coplanar.");
    }
    if (link.kind === "horizontal") horizontal += 1;
    else diagonal += 1;
  }
  if (lattice.counts.total !== lattice.links.length
      || lattice.counts.horizontal !== horizontal
      || lattice.counts.diagonal !== diagonal) {
    throw new Error("Alignment lattice counts are incompatible.");
  }
  const visited = new Set([0]);
  const pending = [0];
  while (pending.length) {
    for (const neighbor of neighbors[pending.pop()]) {
      if (!visited.has(neighbor)) {
        visited.add(neighbor);
        pending.push(neighbor);
      }
    }
  }
  if (visited.size !== fixtureCount) throw new Error("Alignment lattice is disconnected.");
  return lattice;
}

function validVector3(value) {
  return Array.isArray(value) && value.length === 3
    && value.every((component) => Number.isFinite(component));
}

function validateFixtureGroup(group, catalog, scene) {
  assertFields(group, [
    "asset", "display_asset_id", "display_fixture_type", "instance_matrices",
    "ordered_fixture_ids", "registry",
  ], "fixture asset group");
  const approvedAsset = APPROVED_FIXTURE_ASSETS[group.display_asset_id];
  if (!approvedAsset || approvedAsset.systemId !== scene.run.system_id
      || approvedAsset.displayFixtureType !== group.display_fixture_type) {
    throw new Error("Fixture catalog declares an unsupported display asset.");
  }
  const expectedAssetFilename = `assets/${group.display_asset_id}-${approvedAsset.sha256.slice(0, 16)}.glb`;
  assertFields(group.asset, ["byte_size", "filename", "sha256"], "fixture asset");
  if (group.asset.filename !== expectedAssetFilename
      || group.asset.byte_size !== approvedAsset.byteSize
      || group.asset.sha256 !== approvedAsset.sha256) {
    throw new Error("Fixture asset identity, length, or hash is incompatible.");
  }
  validateRegistry(group.registry, approvedAsset, group.display_asset_id);
  if (!Array.isArray(group.ordered_fixture_ids)
      || group.ordered_fixture_ids.length === 0
      || group.ordered_fixture_ids.some((value) => typeof value !== "string" || !value)
      || new Set(group.ordered_fixture_ids).size !== group.ordered_fixture_ids.length) {
    throw new Error("Fixture asset-group ordering is incompatible.");
  }
  const matrices = group.instance_matrices;
  const expectedMatrixFilename = `transforms/${group.display_asset_id}-${catalog.fixture_plan_sha256.slice(0, 16)}.f32le.bin`;
  assertFields(matrices, [
    "byte_length", "byte_order", "component_type", "coordinate_space", "count",
    "filename", "matrix_convention", "record_layout", "scientific_to_viewer",
    "sha256", "stride_bytes",
  ], "fixture matrix record");
  if (matrices.filename !== expectedMatrixFilename
      || matrices.count !== group.ordered_fixture_ids.length
      || matrices.byte_length !== matrices.count * FIXTURE_MATRIX_STRIDE_BYTES
      || matrices.component_type !== "float32"
      || matrices.byte_order !== "little-endian"
      || matrices.record_layout !== "matrix4_column_major"
      || matrices.matrix_convention
        !== "column vectors; viewer_position = matrix * packaged_glb_scene_position"
      || matrices.coordinate_space !== "viewer right-handed meters, Y-up"
      || matrices.scientific_to_viewer !== "(x, y, z) -> (x, z, -y)"
      || matrices.stride_bytes !== FIXTURE_MATRIX_STRIDE_BYTES) {
    throw new Error("Fixture matrix identity or count is incompatible.");
  }
  assertSha256(matrices.sha256, "fixture matrix");
  assertSafeFixturePath(group.asset.filename, "assets", ".glb");
  assertSafeFixturePath(matrices.filename, "transforms", ".f32le.bin");
}

function validateRegistry(registry, approvedAsset, assetId) {
  assertFields(registry, [
    "approved_base_color_alpha_factors", "approved_display_fixture_type",
    "approved_glb_extensions", "approved_system_id", "asset_id", "asset_local_axes",
    "dimension_correction_scale_xyz", "expected_byte_size", "expected_sha256",
    "material_alpha_modes", "materials_double_sided", "meters_per_asset_unit",
    "packaged_resource_path", "pivot",
  ], "fixture registry record");
  if (registry.asset_id !== assetId
      || registry.approved_system_id !== approvedAsset.systemId
      || registry.approved_display_fixture_type !== approvedAsset.displayFixtureType
      || registry.packaged_resource_path !== approvedAsset.resourcePath
      || registry.expected_byte_size !== approvedAsset.byteSize
      || registry.expected_sha256 !== approvedAsset.sha256
      || registry.meters_per_asset_unit !== 0.001
      || registry.materials_double_sided !== true
      || JSON.stringify(registry.material_alpha_modes)
        !== JSON.stringify(approvedAsset.alphaModes)
      || JSON.stringify(registry.approved_base_color_alpha_factors)
        !== JSON.stringify(approvedAsset.alphaFactors)
      || JSON.stringify(registry.approved_glb_extensions)
        !== JSON.stringify(APPROVED_EXTENSIONS)
      || registry.asset_local_axes?.up
        !== "positive_y_after_authored_glb_scene_transform"
      || registry.asset_local_axes?.forward
        !== "negative_z_after_authored_glb_scene_transform"
      || !finiteVector(registry.dimension_correction_scale_xyz, 3)
      || !registry.pivot || typeof registry.pivot.contract !== "string") {
    throw new Error("Fixture registry metadata is incompatible.");
  }
  const placement = approvedAsset.placementContract;
  if (placement && (
    registry.meters_per_asset_unit !== placement.metersPerAssetUnit
      || JSON.stringify(registry.dimension_correction_scale_xyz)
        !== JSON.stringify(placement.dimensionCorrectionScaleXyz)
      || registry.pivot.contract !== placement.pivotContract
      || registry.pivot.placement_plane_local_y_mm
        !== placement.placementPlaneLocalYmm
      || JSON.stringify([
        registry.pivot.placement_correction_local_x_mm,
        registry.pivot.placement_correction_local_y_mm,
        registry.pivot.placement_correction_local_z_mm,
      ]) !== JSON.stringify(placement.placementCorrectionLocalXyzMm)
  )) {
    throw new Error("Fixture registry HPS placement contract is incompatible.");
  }
}

function approved(
  systemId,
  displayFixtureType,
  resourcePath,
  byteSize,
  sha256,
  alphaModes = ["OPAQUE"],
  alphaFactors = [1.0],
  placementContract,
) {
  const frozenPlacement = placementContract === undefined ? undefined : Object.freeze({
    ...placementContract,
    dimensionCorrectionScaleXyz: Object.freeze(
      placementContract.dimensionCorrectionScaleXyz,
    ),
    placementCorrectionLocalXyzMm: Object.freeze(
      placementContract.placementCorrectionLocalXyzMm,
    ),
  });
  return Object.freeze({
    systemId,
    displayFixtureType,
    resourcePath,
    byteSize,
    sha256,
    alphaModes: Object.freeze(alphaModes),
    alphaFactors: Object.freeze(alphaFactors),
    ...(frozenPlacement === undefined ? {} : {
      placementContract: frozenPlacement,
    }),
  });
}

function assertSafeFixturePath(value, directory, suffix) {
  const pattern = new RegExp(`^${directory}/[a-z0-9-]+(?:-[0-9a-f]{16})?\\${suffix}$`);
  if (typeof value !== "string" || !pattern.test(value)
      || value.includes("..") || value.includes("\\")
      || value.includes("?") || value.includes("#")) {
    throw new Error("Fixture artifact path is unsafe or undeclared.");
  }
}

function findUri(value) {
  if (Array.isArray(value)) return value.some(findUri);
  if (value && typeof value === "object") {
    return Object.hasOwn(value, "uri") || Object.values(value).some(findUri);
  }
  return false;
}

function embeddedExtensionNames(value, output = new Set()) {
  if (Array.isArray(value)) {
    value.forEach((item) => embeddedExtensionNames(item, output));
  } else if (value && typeof value === "object") {
    for (const [name, item] of Object.entries(value)) {
      if (name === "extensions") {
        assertObject(item, "fixture GLB extension object");
        Object.keys(item).forEach((extension) => output.add(extension));
      }
      embeddedExtensionNames(item, output);
    }
  }
  return output;
}

function validateStringArray(value, label) {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error(`Fixture GLB ${label} are malformed.`);
  }
  return value;
}

function finiteVector(value, length) {
  return Array.isArray(value) && value.length === length
    && value.every(Number.isFinite);
}

function assertObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object.`);
  }
}

function sameFields(value, fields) {
  return value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join(",") === [...fields].sort().join(",");
}

function assertFields(value, fields, label) {
  if (!sameFields(value, fields)) {
    throw new Error(`${label} field inventory is incompatible.`);
  }
}
