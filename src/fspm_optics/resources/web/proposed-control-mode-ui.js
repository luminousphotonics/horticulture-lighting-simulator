export const BASIS_MATRIX_OPTIMIZED = "basis_matrix_optimized";
export const UNIFORM_MODULE_DIMMING = "uniform_module_dimming";
export {proposedControlModesAgree} from "./result-contracts.js";

export function synchronizeProposedControlMode({system, field, input}) {
  const proposed = system === "proposed";
  field.hidden = true;
  field.setAttribute("aria-hidden", "true");
  input.disabled = true;
  input.checked = proposed;
  return proposed;
}

export function proposedControlModeRequestFields(system, disabled) {
  if (system !== "proposed") return {};
  return {
    proposed_control_mode: disabled
      ? UNIFORM_MODULE_DIMMING
      : BASIS_MATRIX_OPTIMIZED,
  };
}
