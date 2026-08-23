export const NATIVE_SMD = "native_smd";
export const COB_SOURCE_SHAPE_SURROGATE = "cob_source_shape_surrogate";
export {proposedSourceModesAgree} from "./result-contracts.js";

export function synchronizeProposedSourceMode({system, field, input}) {
  const proposed = system === "proposed";
  field.hidden = true;
  field.setAttribute("aria-hidden", "true");
  input.disabled = true;
  input.checked = false;
  return proposed;
}

export function proposedSourceModeRequestFields(system, cobEnabled) {
  if (system !== "proposed") return {};
  return {
    proposed_source_mode: cobEnabled
      ? COB_SOURCE_SHAPE_SURROGATE
      : NATIVE_SMD,
  };
}
