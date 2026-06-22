// @ts-check

export const DEFAULT_FIXTURE_HEIGHT_LIMITS = {
  minOffsetM: -0.3,
  maxOffsetM: 1.2,
  stepM: 0.01,
};

function finiteNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function roundedMeters(value) {
  return Number(value.toFixed(12));
}

export function clampFixtureHeightOffsetM(value, limits = DEFAULT_FIXTURE_HEIGHT_LIMITS) {
  const minOffsetM = finiteNumber(limits?.minOffsetM, DEFAULT_FIXTURE_HEIGHT_LIMITS.minOffsetM);
  const maxOffsetM = finiteNumber(limits?.maxOffsetM, DEFAULT_FIXTURE_HEIGHT_LIMITS.maxOffsetM);
  const lower = Math.min(minOffsetM, maxOffsetM);
  const upper = Math.max(minOffsetM, maxOffsetM);
  const offsetM = finiteNumber(value, 0);
  return Math.min(Math.max(offsetM, lower), upper);
}

export function formatFixtureHeightOffsetM(value) {
  const offsetM = clampFixtureHeightOffsetM(value);
  const normalized = Math.abs(offsetM) < 0.005 ? 0 : offsetM;
  const prefix = normalized > 0 ? "+" : "";
  return `${prefix}${normalized.toFixed(2)} m`;
}

export function formatVisualMountHeightM(baseMountZ, offsetM) {
  const base = Number(baseMountZ);
  if (!Number.isFinite(base)) {
    return formatFixtureHeightOffsetM(offsetM);
  }
  const offset = clampFixtureHeightOffsetM(offsetM);
  const visualMount = base + offset;
  return `Visual mount: ${visualMount.toFixed(2)} m (${formatFixtureHeightOffsetM(offset)})`;
}

export function createFixtureArrayController(fixtureGroup, options = {}) {
  if (!fixtureGroup || typeof fixtureGroup !== "object") {
    throw new TypeError("A fixture group object is required.");
  }
  let position = fixtureGroup.position && typeof fixtureGroup.position === "object"
    ? fixtureGroup.position
    : null;
  if (!position) {
    position = { y: 0 };
    fixtureGroup.position = position;
  }
  const baseGroupY = finiteNumber(options.baseGroupY, finiteNumber(position.y, 0));
  const limits = options.limits || DEFAULT_FIXTURE_HEIGHT_LIMITS;
  let offsetM = 0;
  fixtureGroup.visible = fixtureGroup.visible !== false;
  position.y = baseGroupY;

  function setHeightOffsetM(value) {
    offsetM = clampFixtureHeightOffsetM(value, limits);
    position.y = roundedMeters(baseGroupY + offsetM);
    return getState();
  }

  function setVisible(value) {
    fixtureGroup.visible = Boolean(value);
    return getState();
  }

  function resetHeightOffset() {
    return setHeightOffsetM(0);
  }

  function getState() {
    return {
      visible: fixtureGroup.visible !== false,
      offsetM,
      baseGroupY,
      currentGroupY: finiteNumber(position.y, baseGroupY),
      limits: {
        minOffsetM: finiteNumber(limits?.minOffsetM, DEFAULT_FIXTURE_HEIGHT_LIMITS.minOffsetM),
        maxOffsetM: finiteNumber(limits?.maxOffsetM, DEFAULT_FIXTURE_HEIGHT_LIMITS.maxOffsetM),
        stepM: finiteNumber(limits?.stepM, DEFAULT_FIXTURE_HEIGHT_LIMITS.stepM),
      },
    };
  }

  return {
    setVisible,
    setHeightOffsetM,
    resetHeightOffset,
    getState,
  };
}
