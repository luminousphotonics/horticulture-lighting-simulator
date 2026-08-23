export const METERS_PER_INCH = 0.0254;

const EPSILON = 1e-9;

export function deriveFixtureHeightControl(scene, fixtureBounds) {
  const mounting = scene?.mounting_height;
  if (!mounting) {
    return unavailable(
      "Authenticated simulated mounting height is unavailable for this historical scene.",
    );
  }
  const simulatedHeightIn = finiteNumber(mounting.mounting_height_in);
  const metersPerInch = finiteNumber(mounting.meters_per_inch);
  const referencePlaneY = finiteNumber(mounting.reference_plane_z_m);
  const roomCeilingY = finiteNumber(mounting.room_ceiling_z_m);
  const fixtureMinimum = copyFiniteBoundsCoordinate(fixtureBounds?.minimum_xyz);
  const fixtureMaximum = copyFiniteBoundsCoordinate(fixtureBounds?.maximum_xyz);
  if (
    simulatedHeightIn === null
    || simulatedHeightIn < 0
    || metersPerInch === null
    || Math.abs(metersPerInch - METERS_PER_INCH) > EPSILON
    || referencePlaneY === null
    || roomCeilingY === null
    || fixtureMinimum === null
    || fixtureMaximum === null
  ) {
    return unavailable(
      "Authenticated mounting metadata or complete fixture bounds are unavailable.",
    );
  }
  if (
    referencePlaneY > roomCeilingY
    || fixtureMinimum.some((value, index) => value > fixtureMaximum[index])
  ) {
    return unavailable("Authenticated fixture-height bounds are invalid.");
  }

  const fixtureMinimumY = fixtureMinimum[1];
  const fixtureMaximumY = fixtureMaximum[1];
  const minimumOffsetM = referencePlaneY - fixtureMinimumY;
  const maximumOffsetM = roomCeilingY - fixtureMaximumY;
  if (
    minimumOffsetM > EPSILON
    || maximumOffsetM < -EPSILON
    || minimumOffsetM > maximumOffsetM
  ) {
    return unavailable(
      "The authenticated simulated fixture transform is outside the room height limits.",
    );
  }
  const minimumHeightIn = simulatedHeightIn + minimumOffsetM / METERS_PER_INCH;
  const maximumHeightIn = simulatedHeightIn + maximumOffsetM / METERS_PER_INCH;
  if (
    !Number.isFinite(minimumHeightIn)
    || !Number.isFinite(maximumHeightIn)
    || minimumHeightIn > simulatedHeightIn + EPSILON
    || maximumHeightIn < simulatedHeightIn - EPSILON
  ) {
    return unavailable("A finite safe fixture-height range could not be derived.");
  }
  const stops = absoluteHeightStops(
    minimumHeightIn,
    simulatedHeightIn,
    maximumHeightIn,
  );
  if (stops.length < 1) {
    return unavailable("A finite safe fixture-height range could not be derived.");
  }
  return Object.freeze({
    available: true,
    maximumHeightIn,
    minimumHeightIn,
    simulatedHeightIn,
    simulatedIndex: stops.findIndex(
      (height) => Math.abs(height - simulatedHeightIn) <= EPSILON,
    ),
    stops: Object.freeze(stops),
  });
}

export function translateFixtureBounds(fixtureBounds, displayOffsetIn) {
  const offsetM = displayOffsetIn * METERS_PER_INCH;
  const minimum = copyFiniteBoundsCoordinate(fixtureBounds?.minimum_xyz);
  const maximum = copyFiniteBoundsCoordinate(fixtureBounds?.maximum_xyz);
  if (!Number.isFinite(offsetM) || !minimum || !maximum) {
    throw new Error("Translated fixture bounds must be finite.");
  }
  minimum[1] += offsetM;
  maximum[1] += offsetM;
  return Object.freeze({
    minimum_xyz: Object.freeze(minimum),
    maximum_xyz: Object.freeze(maximum),
  });
}

export function createFixtureHeightController({
  scene,
  fixtureBounds,
  fixtureRoot,
  slider,
  output,
  resetButton,
  state,
  onDisplayChange,
}) {
  const contract = deriveFixtureHeightControl(scene, fixtureBounds);
  let disposed = false;

  const disable = (reason) => {
    const authenticatedHeight = finiteNumber(
      scene?.mounting_height?.mounting_height_in,
    );
    slider.disabled = true;
    resetButton.disabled = true;
    output.value = authenticatedHeight === null
      ? "Unavailable" : formatHeight(authenticatedHeight);
    output.textContent = output.value;
    state.dataset.state = "unavailable";
    state.textContent = reason;
  };
  if (!contract.available) {
    disable(contract.reason);
    return Object.freeze({ contract, dispose: () => {} });
  }
  if (
    fixtureRoot?.name !== "authoritative-run-fixtures"
    || !fixtureRoot.position
  ) {
    disable("The authoritative fixture display root is unavailable.");
    return Object.freeze({ contract, dispose: () => {} });
  }
  if (
    !Number.isFinite(fixtureRoot.position.y)
    || Math.abs(fixtureRoot.position.y) > EPSILON
  ) {
    fixtureRoot.position.y = 0;
    disable("The fixture display root is not at its authenticated simulated transform.");
    return Object.freeze({ contract, dispose: () => {} });
  }

  slider.min = "0";
  slider.max = String(contract.stops.length - 1);
  slider.step = "1";
  slider.disabled = false;
  resetButton.disabled = false;
  slider.setAttribute("aria-valuemin", formatHeight(contract.minimumHeightIn));
  slider.setAttribute("aria-valuemax", formatHeight(contract.maximumHeightIn));
  state.dataset.state = "available";

  const applyIndex = (index, notify) => {
    if (disposed) return;
    const requestedIndex = Number.isFinite(index)
      ? Math.round(index) : contract.simulatedIndex;
    const boundedIndex = Math.max(
      0,
      Math.min(contract.stops.length - 1, requestedIndex),
    );
    const displayHeightIn = contract.stops[boundedIndex];
    const displayOffsetIn = boundedIndex === contract.simulatedIndex
      ? 0 : displayHeightIn - contract.simulatedHeightIn;
    fixtureRoot.position.y = displayOffsetIn * METERS_PER_INCH;
    slider.value = String(boundedIndex);
    slider.dataset.displayHeightIn = formatHeight(displayHeightIn);
    slider.setAttribute("aria-valuenow", formatHeight(displayHeightIn));
    slider.setAttribute(
      "aria-valuetext",
      `${formatHeight(displayHeightIn)} inches display height`,
    );
    output.value = formatHeight(displayHeightIn);
    output.textContent = formatHeight(displayHeightIn);
    state.textContent = boundedIndex === contract.simulatedIndex
      ? "Showing the authenticated simulated height."
      : "Display offset only; simulation results are unchanged.";
    if (notify) {
      onDisplayChange({
        displayHeightIn,
        displayOffsetIn,
        translatedFixtureBounds: translateFixtureBounds(
          fixtureBounds,
          displayOffsetIn,
        ),
      });
    }
  };
  const handleInput = () => applyIndex(Number(slider.value), true);
  const reset = () => applyIndex(contract.simulatedIndex, true);
  slider.addEventListener("input", handleInput);
  resetButton.addEventListener("click", reset);
  applyIndex(contract.simulatedIndex, false);

  return Object.freeze({
    contract,
    dispose() {
      if (disposed) return;
      disposed = true;
      slider.removeEventListener("input", handleInput);
      resetButton.removeEventListener("click", reset);
    },
    reset,
  });
}

function absoluteHeightStops(minimum, simulated, maximum) {
  const lower = [];
  for (let height = simulated - 1; height > minimum + EPSILON; height -= 1) {
    lower.push(height);
  }
  lower.reverse();
  if (simulated - minimum > EPSILON) lower.unshift(minimum);
  const stops = [...lower, simulated];
  for (let height = simulated + 1; height < maximum - EPSILON; height += 1) {
    stops.push(height);
  }
  if (maximum - simulated > EPSILON) stops.push(maximum);
  return stops;
}

function unavailable(reason) {
  return Object.freeze({ available: false, reason });
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function copyFiniteBoundsCoordinate(values) {
  if (!Array.isArray(values) || values.length !== 3) return null;
  const copy = values.map(finiteNumber);
  return copy.some((value) => value === null) ? null : copy;
}

function formatHeight(value) {
  return Number(value.toFixed(3)).toString();
}
