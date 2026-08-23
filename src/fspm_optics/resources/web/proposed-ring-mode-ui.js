export const FULL_RING = "full";
export const REDUCED_ONE_RING = "reduced_one_ring";
export {proposedLayoutIdentitiesAgree} from "./result-contracts.js";

const SUPPORTED_RING_MODES = Object.freeze([FULL_RING, REDUCED_ONE_RING]);

export function synchronizeProposedRingMode({system, field, select}) {
  const proposed = system === "proposed";
  field.hidden = true;
  field.setAttribute("aria-hidden", "true");
  select.disabled = true;
  select.value = REDUCED_ONE_RING;
  return proposed;
}

export function proposedRingModeRequestFields(system, value) {
  if (system !== "proposed") return {};
  if (!SUPPORTED_RING_MODES.includes(value)) {
    throw new Error("Unsupported Proposed module arrangement.");
  }
  return {proposed_ring_mode: value};
}
