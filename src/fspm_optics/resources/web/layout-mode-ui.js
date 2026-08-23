export const PRACTICAL_LAYOUT_MODE = "practical";
export const ROLLING_BENCH_LAYOUT_MODE = "rolling_bench";

export function synchronizeLayoutMode({system, field, select}) {
  const conventional = system === "conventional";
  field.hidden = !conventional;
  field.setAttribute("aria-hidden", String(!conventional));
  select.disabled = !conventional;
  if (!conventional) return false;
  if (![PRACTICAL_LAYOUT_MODE, ROLLING_BENCH_LAYOUT_MODE].includes(select.value)) {
    select.value = ROLLING_BENCH_LAYOUT_MODE;
  }
  return true;
}

export function layoutModeRequestFields(system, value) {
  if (system !== "conventional") return {};
  if (![PRACTICAL_LAYOUT_MODE, ROLLING_BENCH_LAYOUT_MODE].includes(value)) {
    throw new Error("Unsupported Conventional fixture layout.");
  }
  return {layout_mode: value};
}
