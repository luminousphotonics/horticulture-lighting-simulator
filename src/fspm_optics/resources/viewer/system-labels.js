export const SYSTEM_DISPLAY_LABELS = Object.freeze({
  proposed: "Modularized LED System",
  conventional: "Conventional LED System",
  hps: "1000W HPS System",
});

export function systemDisplayLabel(systemId) {
  return SYSTEM_DISPLAY_LABELS[systemId] ?? "Authenticated lighting system";
}
