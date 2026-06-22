from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


TRANSFORMS_JS = Path("src/rad_rebuild/web/static/js/assembly-viewer/transforms.js")


def test_cad_anchor_transform_fitting_cases() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for assembly viewer transform tests")

    script = f"""
import assert from "node:assert/strict";
import {{
  fitCadAnchorsToLayout,
  fixtureMountOrientationCorrectionMatrix4Elements,
  transformToMatrix4Elements,
}} from {TRANSFORMS_JS.resolve().as_uri()!r};

function close(actual, expected, label) {{
  assert.ok(Math.abs(actual - expected) < 1e-9, `${{label}} expected ${{expected}}, got ${{actual}}`);
}}

function predict(fit, anchor) {{
  const t = fit.transform;
  return {{
    x: t.a * anchor.x - t.b * anchor.z + t.tx,
    y: t.b * anchor.x + t.a * anchor.z + t.ty,
    z: anchor.vertical_y + t.verticalTranslate,
  }};
}}

function elementsToRows(elements) {{
  return [
    elements.slice(0, 4),
    elements.slice(4, 8),
    elements.slice(8, 12),
    elements.slice(12, 16),
  ];
}}

function multiplyMatrix(left, right) {{
  return Array.from({{ length: 4 }}, (_row, row) =>
    Array.from({{ length: 4 }}, (_column, column) =>
      left[row][0] * right[0][column]
        + left[row][1] * right[1][column]
        + left[row][2] * right[2][column]
        + left[row][3] * right[3][column],
    ),
  );
}}

function transformPoint(matrix, point) {{
  return {{
    x: matrix[0][0] * point.x + matrix[0][1] * point.y + matrix[0][2] * point.z + matrix[0][3],
    y: matrix[1][0] * point.x + matrix[1][1] * point.y + matrix[1][2] * point.z + matrix[1][3],
    z: matrix[2][0] * point.x + matrix[2][1] * point.y + matrix[2][2] * point.z + matrix[2][3],
  }};
}}

function assertNoShear(fit, name) {{
  const t = fit.transform;
  const firstColumnLength = Math.hypot(t.a, t.b);
  const secondColumnLength = Math.hypot(-t.b, t.a);
  const dot = t.a * -t.b + t.b * t.a;
  assert.equal(fit.diagnostics.determinantSign, 1, name);
  assert.ok(Math.abs(firstColumnLength - secondColumnLength) < 1e-12, `${{name}} unequal basis lengths`);
  assert.ok(Math.abs(dot) < 1e-12, `${{name}} basis columns are not orthogonal`);
}}

function checkFit(name, anchors, points) {{
  const fit = fitCadAnchorsToLayout({{ id: name, points }}, anchors);
  assert.equal(fit.mode, "similarity", name);
  assert.equal(fit.warning, null, name);
  assertNoShear(fit, name);
  assert.ok(fit.residual.max < 1e-9, `${{name}} residual ${{fit.residual.max}}`);
  for (let index = 0; index < anchors.length; index += 1) {{
    const targetIndex = fit.diagnostics.correspondence[index];
    const actual = predict(fit, anchors[index]);
    close(actual.x, points[targetIndex].x, `${{name}} x ${{index}}`);
    close(actual.y, points[targetIndex].y, `${{name}} y ${{index}}`);
    close(actual.z, points[targetIndex].z, `${{name}} z ${{index}}`);
  }}
}}

checkFit(
  "linear2",
  [
    {{ name: "module_1", x: -0.25, z: 0, vertical_y: -0.01 }},
    {{ name: "module_2", x: 0.25, z: 0, vertical_y: -0.01 }},
  ],
  [
    {{ x: 0, y: 0, z: 0.45 }},
    {{ x: 1, y: 0, z: 0.45 }},
  ],
);

checkFit(
  "linear3-vertical",
  [
    {{ name: "module_1", x: -0.5, z: 0, vertical_y: -0.00805 }},
    {{ name: "module_2", x: 0, z: 0, vertical_y: -0.00805 }},
    {{ name: "module_3", x: 0.5, z: 0, vertical_y: -0.00805 }},
  ],
  [
    {{ x: -0.4, y: 1.2, z: 0.6 }},
    {{ x: 0.1, y: 1.2, z: 0.6 }},
    {{ x: 0.6, y: 1.2, z: 0.6 }},
  ],
);

checkFit(
  "linear4-yaw",
  [
    {{ name: "module_1", x: -0.75, z: 0, vertical_y: -0.00805 }},
    {{ name: "module_2", x: -0.25, z: 0, vertical_y: -0.00805 }},
    {{ name: "module_3", x: 0.25, z: 0, vertical_y: -0.00805 }},
    {{ name: "module_4", x: 0.75, z: 0, vertical_y: -0.00805 }},
  ],
  [
    {{ x: 0.2, y: -0.6, z: 0.4572 }},
    {{ x: 0.2, y: -0.2, z: 0.4572 }},
    {{ x: 0.2, y: 0.2, z: 0.4572 }},
    {{ x: 0.2, y: 0.6, z: 0.4572 }},
  ],
);

const l4Anchors = [
  {{ name: "module_1", x: -0.56, z: 0, vertical_y: -0.00355 }},
  {{ name: "module_2", x: 0, z: 0, vertical_y: -0.00355 }},
  {{ name: "module_3", x: 0.56, z: 0, vertical_y: -0.00355 }},
  {{ name: "module_4", x: 0.56, z: -0.56, vertical_y: -0.00355 }},
];
checkFit(
  "l4-similarity",
  l4Anchors,
  l4Anchors.map((anchor) => ({{ x: 1 + 1.5 * anchor.x, y: -2 + 1.5 * anchor.z, z: 0.5 }})),
);

const reverseL4Anchors = [
  {{ name: "module_1", x: -0.56, z: -0.56, vertical_y: -0.00355 }},
  {{ name: "module_2", x: -0.56, z: 0, vertical_y: -0.00355 }},
  {{ name: "module_3", x: 0, z: 0, vertical_y: -0.00355 }},
  {{ name: "module_4", x: 0.56, z: 0, vertical_y: -0.00355 }},
];
const reversePointsOrdered = reverseL4Anchors.map((anchor) => ({{
  x: 0.7 - anchor.z,
  y: 1.1 + anchor.x,
  z: 0.4572,
}}));
const reversePoints = [
  reversePointsOrdered[2],
  reversePointsOrdered[0],
  reversePointsOrdered[3],
  reversePointsOrdered[1],
];
const reverseFit = fitCadAnchorsToLayout({{ id: "reverse-l4", points: reversePoints }}, reverseL4Anchors);
assert.equal(reverseFit.mode, "similarity");
assertNoShear(reverseFit, "reverse-l4");
assert.ok(reverseFit.diagnostics.pointCorrespondenceInferred);
assert.ok(reverseFit.residual.max < 1e-9, `reverse residual ${{reverseFit.residual.max}}`);

const centerpiece = [
  {{ name: "module_0", x: 0, z: 0, vertical_y: -0.00355 }},
  {{ name: "module_1", x: 0.28, z: -0.28, vertical_y: -0.00355 }},
  {{ name: "module_2", x: 0.28, z: 0.28, vertical_y: -0.00355 }},
  {{ name: "module_3", x: -0.28, z: -0.28, vertical_y: -0.00355 }},
  {{ name: "module_4", x: -0.28, z: 0.28, vertical_y: -0.00355 }},
];
checkFit(
  "centerpiece",
  centerpiece,
  centerpiece.map((anchor) => ({{ x: anchor.x + 0.4, y: anchor.z - 0.6, z: 0.4572 }})),
);

const centerpieceFit = fitCadAnchorsToLayout(
  {{ id: "centerpiece-orientation", points: centerpiece.map((anchor) => ({{ x: anchor.x + 0.4, y: anchor.z - 0.6, z: 0.4572 }})) }},
  centerpiece,
);
const fittedMatrix = elementsToRows(transformToMatrix4Elements(centerpieceFit.transform));
const orientationMatrix = elementsToRows(fixtureMountOrientationCorrectionMatrix4Elements(centerpiece));
const correctedMatrix = multiplyMatrix(fittedMatrix, orientationMatrix);
for (const anchor of centerpiece) {{
  const sourceAnchor = {{ x: anchor.x, y: anchor.vertical_y, z: anchor.z }};
  const before = transformPoint(fittedMatrix, sourceAnchor);
  const after = transformPoint(correctedMatrix, sourceAnchor);
  close(after.x, before.x, `orientation preserves anchor x ${{anchor.name}}`);
  close(after.y, before.y, `orientation preserves anchor y ${{anchor.name}}`);
  close(after.z, before.z, `orientation preserves anchor z ${{anchor.name}}`);
}}
const aboveAnchorPlane = transformPoint(correctedMatrix, {{ x: 0, y: 0.05, z: 0 }});
const mirroredBelow = transformPoint(fittedMatrix, {{ x: 0, y: 2 * centerpiece[0].vertical_y - 0.05, z: 0 }});
close(aboveAnchorPlane.x, mirroredBelow.x, "orientation flips visual vertical x");
close(aboveAnchorPlane.y, mirroredBelow.y, "orientation flips visual vertical y");
close(aboveAnchorPlane.z, mirroredBelow.z, "orientation flips visual vertical z");

const malformed = fitCadAnchorsToLayout(
  {{ id: "bad", points: [{{ x: 0, y: 0, z: 0.4 }}, {{ x: 1, y: 0, z: 0.4 }}] }},
  [{{ name: "module_1", x: 0, z: 0, vertical_y: 0 }}],
);
assert.equal(malformed.ok, false);
assert.match(malformed.warning, /2 layout points but 1 CAD module anchors/);
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
