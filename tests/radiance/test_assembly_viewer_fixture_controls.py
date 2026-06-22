from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


FIXTURE_CONTROLS_JS = Path("src/rad_rebuild/web/static/js/assembly-viewer/fixture-controls.js")


def test_assembly_viewer_fixture_control_helpers() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for assembly viewer fixture control tests")

    script = f"""
import assert from "node:assert/strict";
import {{
  DEFAULT_FIXTURE_HEIGHT_LIMITS,
  clampFixtureHeightOffsetM,
  createFixtureArrayController,
  formatFixtureHeightOffsetM,
  formatVisualMountHeightM,
}} from {FIXTURE_CONTROLS_JS.resolve().as_uri()!r};

assert.deepEqual(DEFAULT_FIXTURE_HEIGHT_LIMITS, {{
  minOffsetM: -0.3,
  maxOffsetM: 1.2,
  stepM: 0.01,
}});
assert.equal(clampFixtureHeightOffsetM(-1), -0.3);
assert.equal(clampFixtureHeightOffsetM(2), 1.2);
assert.equal(clampFixtureHeightOffsetM(0.25), 0.25);
assert.equal(clampFixtureHeightOffsetM(Number.NaN), 0);

const group = {{
  visible: true,
  position: {{ y: 0.45 }},
  untouched: {{ childVisibility: "managed-by-lod" }},
}};
const controller = createFixtureArrayController(group);
assert.deepEqual(controller.getState(), {{
  visible: true,
  offsetM: 0,
  baseGroupY: 0.45,
  currentGroupY: 0.45,
  limits: {{
    minOffsetM: -0.3,
    maxOffsetM: 1.2,
    stepM: 0.01,
  }},
}});
assert.equal(group.visible, true);
assert.equal(group.position.y, 0.45);

controller.setVisible(false);
assert.equal(group.visible, false);
assert.equal(group.position.y, 0.45);
assert.equal(group.untouched.childVisibility, "managed-by-lod");

controller.setVisible(true);
assert.equal(group.visible, true);
assert.equal(group.position.y, 0.45);

controller.setHeightOffsetM(0.25);
assert.equal(controller.getState().offsetM, 0.25);
assert.equal(controller.getState().baseGroupY, 0.45);
assert.equal(group.position.y, 0.7);
assert.equal(group.visible, true);

controller.setHeightOffsetM(9);
assert.equal(controller.getState().offsetM, 1.2);
assert.equal(group.position.y, 1.65);

controller.setHeightOffsetM(-9);
assert.equal(controller.getState().offsetM, -0.3);
assert.equal(group.position.y, 0.15);

controller.resetHeightOffset();
assert.equal(controller.getState().offsetM, 0);
assert.equal(group.position.y, 0.45);

const customGroup = {{ visible: true, position: {{ y: 2 }} }};
const customController = createFixtureArrayController(customGroup, {{
  limits: {{ minOffsetM: -0.1, maxOffsetM: 0.2, stepM: 0.05 }},
}});
customController.setHeightOffsetM(0.5);
assert.equal(customController.getState().offsetM, 0.2);
assert.equal(customGroup.position.y, 2.2);

assert.equal(formatFixtureHeightOffsetM(0), "0.00 m");
assert.equal(formatFixtureHeightOffsetM(0.25), "+0.25 m");
assert.equal(formatFixtureHeightOffsetM(-0.1), "-0.10 m");
assert.equal(formatVisualMountHeightM(0.4572, 0.25), "Visual mount: 0.71 m (+0.25 m)");
assert.equal(formatVisualMountHeightM(undefined, -0.1), "-0.10 m");
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_assembly_viewer_fixture_controls_do_not_reference_simulation_state() -> None:
    source = FIXTURE_CONTROLS_JS.read_text(encoding="utf-8")
    forbidden_terms = [
        "lastCompletedRunKey",
        "scenePayload.instances",
        "mount_z_m",
        "ppfd",
        "photometric",
        "heatmap",
        "fetch(",
    ]
    for term in forbidden_terms:
        assert term not in source
