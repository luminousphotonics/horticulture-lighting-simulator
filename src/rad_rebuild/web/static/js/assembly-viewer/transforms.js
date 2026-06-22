// @ts-check

const EPSILON = 1.0e-9;
const RESIDUAL_WARNING_THRESHOLD_M = 0.075;
const SCALE_MIN = 0.35;
const SCALE_MAX = 2.75;
const MODULE_NODE_PATTERN = /^module_(\d+)(?:_|$)/i;

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function moduleNumber(name) {
  const match = String(name || "").match(MODULE_NODE_PATTERN);
  return match ? Number.parseInt(match[1], 10) : Number.MAX_SAFE_INTEGER;
}

function centroid2(points) {
  const total = points.reduce(
    (acc, point) => {
      acc.x += point.x;
      acc.y += point.y;
      return acc;
    },
    { x: 0, y: 0 },
  );
  const count = Math.max(1, points.length);
  return { x: total.x / count, y: total.y / count };
}

function centroid3(points) {
  const total = points.reduce(
    (acc, point) => {
      acc.x += point.x;
      acc.y += point.y;
      acc.z += point.z;
      return acc;
    },
    { x: 0, y: 0, z: 0 },
  );
  const count = Math.max(1, points.length);
  return { x: total.x / count, y: total.y / count, z: total.z / count };
}

function parseLayoutPoints(instance) {
  const rawPoints = Array.isArray(instance?.points) ? instance.points : [];
  const points = [];
  for (const point of rawPoints) {
    const x = finiteNumber(point?.x);
    const y = finiteNumber(point?.y);
    const z = finiteNumber(point?.z);
    if (x === null || y === null || z === null) {
      return [];
    }
    points.push({ x, y, z });
  }
  return points;
}

function parseCadAnchors(anchors) {
  const parsed = [];
  for (const anchor of Array.isArray(anchors) ? anchors : []) {
    const x = finiteNumber(anchor?.x);
    const z = finiteNumber(anchor?.z);
    const verticalY = finiteNumber(anchor?.vertical_y);
    if (x === null || z === null || verticalY === null) {
      return [];
    }
    parsed.push({
      name: String(anchor?.name || ""),
      moduleNumber: moduleNumber(anchor?.name),
      x,
      y: z,
      verticalY,
    });
  }
  parsed.sort((left, right) => left.moduleNumber - right.moduleNumber);
  return parsed;
}

function permutations(values) {
  if (values.length <= 1) {
    return [values.slice()];
  }
  const output = [];
  for (let index = 0; index < values.length; index += 1) {
    const head = values[index];
    const rest = [...values.slice(0, index), ...values.slice(index + 1)];
    for (const tail of permutations(rest)) {
      output.push([head, ...tail]);
    }
  }
  return output;
}

function correspondenceOrders(sourcePoints, targetPoints) {
  const targetIndexes = targetPoints.map((_point, index) => index);
  const centerSourceIndex = sourcePoints.findIndex((point) => point.moduleNumber === 0);
  if (sourcePoints.length === 5 && centerSourceIndex >= 0) {
    const layoutCenter = centroid3(targetPoints);
    let centerTargetIndex = 0;
    let centerDistance = Infinity;
    for (let index = 0; index < targetPoints.length; index += 1) {
      const point = targetPoints[index];
      const distance = Math.hypot(point.x - layoutCenter.x, point.y - layoutCenter.y);
      if (distance < centerDistance) {
        centerDistance = distance;
        centerTargetIndex = index;
      }
    }
    const remainingSourceIndexes = sourcePoints.map((_point, index) => index).filter((index) => index !== centerSourceIndex);
    const remainingTargetIndexes = targetIndexes.filter((index) => index !== centerTargetIndex);
    return permutations(remainingTargetIndexes).map((permutation) => {
      const order = new Array(sourcePoints.length);
      order[centerSourceIndex] = centerTargetIndex;
      for (let index = 0; index < remainingSourceIndexes.length; index += 1) {
        order[remainingSourceIndexes[index]] = permutation[index];
      }
      return order;
    });
  }
  return permutations(targetIndexes);
}

function similarityFit(sourcePoints, targetPoints, options = {}) {
  const reflected = options.reflected === true;
  const sourceHorizontal = sourcePoints.map((point) => ({
    x: point.x,
    y: reflected ? -point.y : point.y,
    verticalY: point.verticalY,
  }));
  const sourceCenter = centroid2(sourceHorizontal);
  const targetCenter = centroid2(targetPoints);
  let dot = 0;
  let cross = 0;
  let norm = 0;
  for (let index = 0; index < sourceHorizontal.length; index += 1) {
    const source = sourceHorizontal[index];
    const target = targetPoints[index];
    const sx = source.x - sourceCenter.x;
    const sy = source.y - sourceCenter.y;
    const tx = target.x - targetCenter.x;
    const ty = target.y - targetCenter.y;
    dot += sx * tx + sy * ty;
    cross += sx * ty - sy * tx;
    norm += sx * sx + sy * sy;
  }
  if (norm <= EPSILON) {
    return null;
  }

  const a = dot / norm;
  const b = cross / norm;
  const tx = targetCenter.x - (a * sourceCenter.x - b * sourceCenter.y);
  const ty = targetCenter.y - (b * sourceCenter.x + a * sourceCenter.y);
  const sourceVerticalCenter = sourcePoints.reduce((total, point) => total + point.verticalY, 0) / sourcePoints.length;
  const targetVerticalCenter = targetPoints.reduce((total, point) => total + point.z, 0) / targetPoints.length;
  return {
    a,
    b,
    tx,
    ty,
    reflected,
    determinantSign: reflected ? -1 : 1,
    scale: Math.hypot(a, b),
    rotationRadians: Math.atan2(b, a),
    verticalTranslate: targetVerticalCenter - sourceVerticalCenter,
  };
}

function predict(transform, point) {
  const sourceY = transform.reflected ? -point.y : point.y;
  return {
    x: transform.a * point.x - transform.b * sourceY + transform.tx,
    y: transform.b * point.x + transform.a * sourceY + transform.ty,
    z: point.verticalY + transform.verticalTranslate,
  };
}

function residuals(transform, sourcePoints, targetPoints) {
  let horizontalSum = 0;
  let verticalSum = 0;
  let max = 0;
  for (let index = 0; index < sourcePoints.length; index += 1) {
    const predicted = predict(transform, sourcePoints[index]);
    const target = targetPoints[index];
    const horizontalError = Math.hypot(predicted.x - target.x, predicted.y - target.y);
    const verticalError = Math.abs(predicted.z - target.z);
    horizontalSum += horizontalError * horizontalError;
    verticalSum += verticalError * verticalError;
    max = Math.max(max, horizontalError, verticalError);
  }
  const count = Math.max(1, sourcePoints.length);
  return {
    rms: Math.sqrt((horizontalSum + verticalSum) / count),
    horizontal_rms: Math.sqrt(horizontalSum / count),
    vertical_rms: Math.sqrt(verticalSum / count),
    max,
  };
}

function fitWithOrders(sourcePoints, targetPoints, orders, options = {}) {
  let best = null;
  for (const order of orders) {
    const orderedTargets = order.map((targetIndex) => targetPoints[targetIndex]);
    const transform = similarityFit(sourcePoints, orderedTargets, options);
    if (!transform) {
      continue;
    }
    const residual = residuals(transform, sourcePoints, orderedTargets);
    const candidate = { transform, residual, order, targetPoints: orderedTargets };
    if (!best || residual.rms < best.residual.rms) {
      best = candidate;
    }
  }
  return best;
}

function fallbackFit(instanceLabel, points) {
  const center = centroid3(points.length ? points : [{ x: 0, y: 0, z: 0 }]);
  return {
    ok: false,
    mode: "fallback",
    transform: {
      a: 1,
      b: 0,
      tx: center.x,
      ty: center.y,
      reflected: false,
      determinantSign: 1,
      scale: 1,
      rotationRadians: 0,
      verticalTranslate: center.z,
    },
    residual: { rms: 0, horizontal_rms: 0, vertical_rms: 0, max: 0 },
    warning: `${instanceLabel} could not be fitted from CAD anchors; using centroid placement.`,
    diagnostics: {
      scale: 1,
      rotationRadians: 0,
      determinantSign: 1,
      pointCorrespondenceInferred: false,
      reflectedFitResidual: null,
    },
  };
}

function warningForFit(instanceLabel, fit, reflectedFit, options) {
  const warnings = [];
  const residualThreshold = Number.isFinite(Number(options.residualWarningThresholdM))
    ? Number(options.residualWarningThresholdM)
    : RESIDUAL_WARNING_THRESHOLD_M;
  const scaleMin = Number.isFinite(Number(options.scaleMin)) ? Number(options.scaleMin) : SCALE_MIN;
  const scaleMax = Number.isFinite(Number(options.scaleMax)) ? Number(options.scaleMax) : SCALE_MAX;
  if (fit.residual.max > residualThreshold) {
    warnings.push(`${instanceLabel} CAD anchor fit residual is ${fit.residual.max.toFixed(3)} m; rendering best-effort placement.`);
  }
  if (fit.transform.scale < scaleMin || fit.transform.scale > scaleMax) {
    warnings.push(`${instanceLabel} CAD anchor fit scale is ${fit.transform.scale.toFixed(3)}, outside the expected range.`);
  }
  if (reflectedFit && reflectedFit.residual.rms + EPSILON < fit.residual.rms * 0.75) {
    warnings.push(`${instanceLabel} would fit better with a reflected transform; keeping positive-determinant placement.`);
  }
  return warnings.join(" ");
}

export function fitCadAnchorsToLayout(instance, anchors, options = {}) {
  const instanceLabel = String(instance?.id || "fixture");
  const targetPoints = parseLayoutPoints(instance);
  const sourcePoints = parseCadAnchors(anchors);
  if (!targetPoints.length) {
    return fallbackFit(instanceLabel, targetPoints);
  }
  if (sourcePoints.length !== targetPoints.length) {
    const result = fallbackFit(instanceLabel, targetPoints);
    result.warning = `${instanceLabel} has ${targetPoints.length} layout points but ${sourcePoints.length} CAD module anchors; using centroid placement.`;
    return result;
  }

  const orders = correspondenceOrders(sourcePoints, targetPoints);
  const positiveFit = fitWithOrders(sourcePoints, targetPoints, orders);
  if (!positiveFit) {
    const result = fallbackFit(instanceLabel, targetPoints);
    result.warning = `${instanceLabel} has unstable CAD/layout geometry; using centroid placement.`;
    return result;
  }
  const reflectedFit = options.allowReflectionDiagnostics === false
    ? null
    : fitWithOrders(sourcePoints, targetPoints, orders, { reflected: true });
  const identityOrder = positiveFit.order.every((targetIndex, index) => targetIndex === index);
  const warning = warningForFit(instanceLabel, positiveFit, reflectedFit, options);
  return {
    ok: true,
    mode: "similarity",
    transform: positiveFit.transform,
    residual: positiveFit.residual,
    warning: warning || null,
    diagnostics: {
      assetKey: typeof options.assetKey === "string" ? options.assetKey : null,
      selectedCandidateAssetKey: typeof options.assetKey === "string" ? options.assetKey : null,
      scale: positiveFit.transform.scale,
      rotationRadians: positiveFit.transform.rotationRadians,
      determinantSign: positiveFit.transform.determinantSign,
      pointCorrespondenceInferred: !identityOrder,
      correspondence: positiveFit.order.slice(),
      fallbackAssetUsed: Boolean(instance?.asset_fallback_key),
      reflectedFitResidual: reflectedFit ? reflectedFit.residual.rms : null,
    },
  };
}

export function chooseBestAnchorCandidate(instance, candidates, options = {}) {
  const fits = [];
  for (const candidate of Array.isArray(candidates) ? candidates : []) {
    const fit = fitCadAnchorsToLayout(instance, candidate?.anchors, {
      ...options,
      assetKey: candidate?.assetKey,
    });
    if (fit.ok) {
      fits.push({ ...candidate, fit });
    }
  }
  fits.sort((left, right) => left.fit.residual.rms - right.fit.residual.rms);
  return fits[0] || null;
}

export function transformToMatrix4Elements(transform) {
  return [
    transform.a, 0, -transform.b, transform.tx,
    0, 1, 0, transform.verticalTranslate,
    transform.b, 0, transform.a, transform.ty,
    0, 0, 0, 1,
  ];
}

export function fixtureMountOrientationCorrectionMatrix4Elements(anchors) {
  const parsedAnchors = parseCadAnchors(anchors);
  const pivotY = parsedAnchors.length
    ? parsedAnchors.reduce((total, anchor) => total + anchor.verticalY, 0) / parsedAnchors.length
    : 0;
  // The CAD GLBs place the modules on the correct X/Z footprint, but their
  // local vertical is visually inverted for the viewer: LEDs render upward and
  // heatsinks downward. A 180-degree horizontal rotation would move
  // non-collinear corner/centerpiece anchors, so we mirror only CAD Y around
  // the fitted module-anchor plane. Every anchor on that plane remains fixed.
  return [
    1, 0, 0, 0,
    0, -1, 0, 2 * pivotY,
    0, 0, 1, 0,
    0, 0, 0, 1,
  ];
}
