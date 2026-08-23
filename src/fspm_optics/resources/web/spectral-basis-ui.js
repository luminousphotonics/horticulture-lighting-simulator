import { MULTISPECTRAL_FSPM_SCOPE } from "./analysis-scope-ui.js";

export const NATIVE_PROPOSED_SPECTRAL_BASIS = "native_proposed";
export const CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS =
  "conventional_led_control";

export function includesProposedSpectralBasis(system, scope) {
  return system === "proposed" && scope === MULTISPECTRAL_FSPM_SCOPE;
}

export function synchronizeProposedSpectralBasis({
  system,
  scope,
  field,
  select,
}) {
  const visible = includesProposedSpectralBasis(system, scope);
  field.hidden = !visible;
  field.setAttribute("aria-hidden", String(!visible));
  select.disabled = !visible;
  if (!visible) select.value = NATIVE_PROPOSED_SPECTRAL_BASIS;
  return visible;
}

export function proposedSpectralBasisRequestFields(system, scope, selected) {
  if (!includesProposedSpectralBasis(system, scope)) return {};
  if (
    selected !== NATIVE_PROPOSED_SPECTRAL_BASIS &&
    selected !== CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS
  ) {
    throw new Error("Unsupported Proposed spectral basis selection.");
  }
  return { spectral_basis: selected };
}
