export const MEAN_TARGET = "mean_target";
export const TARGET_CAPPED = "target_capped";

const MODES = new Set([MEAN_TARGET, TARGET_CAPPED]);

export function lightingTargetModeRequestFields(system, mode) {
  if (system === "hps") return {};
  if (!MODES.has(mode)) throw new Error("Unsupported lighting target mode.");
  return {lighting_target_mode: mode};
}

export function synchronizeLightingTargetMode({
  system,
  field,
  select,
  targetLabel,
  targetNote,
}) {
  const supported = system === "proposed" || system === "conventional";
  field.hidden = !supported;
  field.setAttribute("aria-hidden", String(!supported));
  select.disabled = !supported;
  if (!supported) select.value = MEAN_TARGET;
  const capped = supported && select.value === TARGET_CAPPED;
  targetLabel.textContent = capped
    ? "Maximum PPFD cap"
    : "Target mean PPFD";
  targetNote.textContent = capped
    ? "A ceiling over authorized Stage A sensor-grid samples; the achieved mean may be lower."
    : "The existing requested mean Stage A PPFD policy.";
  return supported;
}
