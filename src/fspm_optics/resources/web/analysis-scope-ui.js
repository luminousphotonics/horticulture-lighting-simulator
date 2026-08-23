export const MULTISPECTRAL_FSPM_SCOPE = "baseline_plus_multispectral_fspm";

export function includesMultispectralFspm(scope) {
  return scope === MULTISPECTRAL_FSPM_SCOPE;
}

export function synchronizeFarRedAnalysis({ scope, field, input }) {
  const visible = includesMultispectralFspm(scope);
  field.hidden = !visible;
  field.setAttribute("aria-hidden", String(!visible));
  const controls = field.querySelectorAll?.("input, select, textarea, button")
    ?? [input];
  for (const control of controls) control.disabled = !visible;
  if (!visible) input.checked = false;
  return visible;
}

export function farRedRequestFields(scope, selected) {
  return includesMultispectralFspm(scope)
    ? { include_far_red: Boolean(selected) }
    : {};
}
