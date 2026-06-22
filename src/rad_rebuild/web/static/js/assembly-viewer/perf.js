// @ts-check

export function createPerfOverlay(el, metadata) {
  let frameCount = 0;
  let lastUpdate = window.performance.now();
  let fps = 0;
  const visible = new URLSearchParams(window.location.search).get("perf") === "1";
  if (el instanceof HTMLElement) {
    el.hidden = !visible;
  }

  function render() {
    if (!(el instanceof HTMLElement) || !visible) {
      return;
    }
    el.textContent = `FPS ${fps.toFixed(0)} | instances ${metadata.instanceCount} | ${metadata.backend}`;
  }

  render();
  return {
    frame() {
      frameCount += 1;
      const now = window.performance.now();
      const elapsed = now - lastUpdate;
      if (elapsed >= 500) {
        fps = (frameCount * 1000) / elapsed;
        frameCount = 0;
        lastUpdate = now;
        render();
      }
    },
  };
}
