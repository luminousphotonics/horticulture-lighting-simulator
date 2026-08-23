const MODULE_PATTERN_BY_RING_MODE = Object.freeze({
  full: "centered_square_full_v1",
  reduced_one_ring: "centered_square_reduced_one_ring_v1",
});
const FIXTURE_POLICY_BY_LAYOUT_MODE = Object.freeze({
  standalone_modules: "proposed_standalone_module_instances_with_alignment_lattice_v2",
  linear: "proposed_linear_fixture_assemblies_v1",
  legacy: "proposed_tile_fill_connect_fixture_assemblies_v1",
});

export function proposedControlModesAgree(metricsControl, resultControl) {
  const metricsPresent = metricsControl !== null && metricsControl !== undefined;
  const resultPresent = resultControl !== null && resultControl !== undefined;
  if (!metricsPresent || !resultPresent) return metricsPresent === resultPresent;
  return (
    metricsControl.mode === resultControl.mode
    && metricsControl.basis_matrix_solver_enabled
      === resultControl.basis_matrix_solver_enabled
  );
}

export function proposedLayoutIdentitiesAgree(metricsLayout, resultLayout) {
  const metricsPresent = metricsLayout !== null && metricsLayout !== undefined;
  const resultPresent = resultLayout !== null && resultLayout !== undefined;
  if (!metricsPresent || !resultPresent) return metricsPresent === resultPresent;
  if (
    typeof metricsLayout !== "object"
    || Array.isArray(metricsLayout)
    || typeof resultLayout !== "object"
    || Array.isArray(resultLayout)
  ) {
    return false;
  }
  const expectedFixturePolicy = FIXTURE_POLICY_BY_LAYOUT_MODE[metricsLayout.mode];
  const expectedModulePattern = MODULE_PATTERN_BY_RING_MODE[metricsLayout.ring_mode];
  return (
    expectedFixturePolicy !== undefined
    && expectedModulePattern !== undefined
    && metricsLayout.fixture_policy_id === expectedFixturePolicy
    && metricsLayout.module_pattern_id === expectedModulePattern
    && resultLayout.mode === metricsLayout.mode
    && resultLayout.fixture_policy_id === metricsLayout.fixture_policy_id
    && resultLayout.ring_mode === metricsLayout.ring_mode
    && resultLayout.module_pattern_id === metricsLayout.module_pattern_id
  );
}

export function proposedSourceModesAgree(metricsSource, resultSource) {
  if (metricsSource == null || resultSource == null) {
    return metricsSource == null && resultSource == null;
  }
  const metricsMode = typeof metricsSource === "string"
    ? metricsSource
    : metricsSource.source_mode;
  const resultMode = typeof resultSource === "string"
    ? resultSource
    : resultSource.source_mode;
  return metricsMode === resultMode;
}
