from __future__ import annotations

import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.engine.emitters import generate_emitters_smd as smd  # noqa: E402
from rad_rebuild.radiance.engine.emitters import generate_emitters_spydr3 as spydr  # noqa: E402
from rad_rebuild.radiance.engine.emitters.smd_generation import optical_stack as stack  # noqa: E402
from rad_rebuild.radiance.engine.emitters.deterministic_ids import radiance_identifier  # noqa: E402


_SUBPROCESS_SCRIPT = r"""
import io
import json
from pathlib import Path

from rad_rebuild.radiance.engine.emitters import generate_emitters_smd as smd
from rad_rebuild.radiance.engine.emitters import generate_emitters_spydr3 as spydr
from rad_rebuild.radiance.engine.emitters.smd_generation import optical_stack as stack

buf = io.StringIO()
stack.write_stack_cavity(
    buf,
    1.25,
    -0.0,
    0.15,
    ptfe_mode=True,
    aperture_m=0.1,
    liner_thk_m=0.01,
    stack_height_m=0.02,
)
smd._write_area_square(buf, "smd_light", 0.2, -0.0, 0.4572, 0.05)
smd._write_area_rect(buf, "smd_rect", 0.3, -0.4, 0.4572, 0.08, 0.04)
bars = []
spydr.write_bar(buf, "spydr_bar", 1.25, -0.0, 0.4572, 1.1, 0.08, 0.25, 1, bars)
spydr.write_bar(buf, "spydr_sub_bar", -0.5, 0.75, 0.4572, 1.1, 0.08, 0.25, 2, bars)
metrics = spydr.RelativeSPDPhotonMetrics(
    spd_path="spd.csv",
    sample_count=3,
    total_relative_power_area=1.0,
    par_relative_power_area=0.9,
    par_fraction_of_total_power=0.9,
    par_centroid_nm=550.0,
    luminous_efficacy_lm_per_radiant_w=100.0,
    par_photon_umol_per_radiant_w=4.5,
    umol_per_lumen=0.045,
)
geometry = spydr.SpydrFixtureGeometry(
    fixture_length_in=46.85,
    fixture_width_in=42.8,
    fixture_height_in=4.26,
    bar_length_in=40.0,
    bar_width_in=2.0,
    bar_height_in=1.0,
    fixture_length_m=1.18999,
    fixture_width_m=1.08712,
    fixture_height_m=0.108204,
    bar_length_m=1.016,
    bar_width_m=0.0508,
    bar_height_m=0.0254,
    spacing_m=0.1,
    rotation_rad=0.0,
    bar_offsets_m=[-0.15, 0.15],
    bar_offsets_in=[-5.9, 5.9],
    fixture_ppf_active=2240.0,
    fixture_ppe=2.8,
    fixture_input_w=800.0,
)
stage = spydr.SpydrIesStage(
    lines=[],
    lumens=1000.0,
    dims_m=None,
    spd_metrics=metrics,
    lm_to_umol=0.045,
    lm_to_umol_method="test",
    baseline_ppf=2240.0,
    baseline_radiant_w=100.0,
    target_radiant_w=100.0,
    scale=1.0,
    rad_path=Path("fixture.rad"),
    source_meta={"box_face_count": 6},
)
spydr.PROXY_MODE = True
spydr._write_spydr_fixture(buf, 1.25, -0.0, geometry, stage)
print(json.dumps({"text": buf.getvalue()}, sort_keys=True))
"""


def _run_generation_in_subprocess(hash_seed: str) -> str:
    with tempfile.TemporaryDirectory(prefix="rad_rebuild_ids_") as tmp:
        root = Path(tmp)
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hash_seed
        env["PYTHONPATH"] = "src"
        env["RADIANCE_RUNTIME_STATE_ROOT"] = str(root / "runtime_state")
        result = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_SCRIPT],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    payload = json.loads(result.stdout)
    return str(payload["text"])


def _scrub_object_names(rad_text: str) -> str:
    scrubbed = re.sub(r"(\bpolygon\s+)\S+", r"\1OBJECT_NAME", rad_text)
    return re.sub(r"(!genbox\s+spydr3_proxy\s+)\S+", r"\1OBJECT_NAME", scrubbed)


def _write_representative_output(buf: io.StringIO) -> None:
    stack.write_stack_cavity(
        buf,
        1.25,
        -0.0,
        0.15,
        ptfe_mode=True,
        aperture_m=0.1,
        liner_thk_m=0.01,
        stack_height_m=0.02,
    )
    smd._write_area_square(buf, "smd_light", 0.2, -0.0, 0.4572, 0.05)
    smd._write_area_rect(buf, "smd_rect", 0.3, -0.4, 0.4572, 0.08, 0.04)
    bars: list[dict[str, object]] = []
    spydr.write_bar(buf, "spydr_bar", 1.25, -0.0, 0.4572, 1.1, 0.08, 0.25, 1, bars)
    spydr.write_bar(buf, "spydr_sub_bar", -0.5, 0.75, 0.4572, 1.1, 0.08, 0.25, 2, bars)
    metrics = spydr.RelativeSPDPhotonMetrics(
        spd_path="spd.csv",
        sample_count=3,
        total_relative_power_area=1.0,
        par_relative_power_area=0.9,
        par_fraction_of_total_power=0.9,
        par_centroid_nm=550.0,
        luminous_efficacy_lm_per_radiant_w=100.0,
        par_photon_umol_per_radiant_w=4.5,
        umol_per_lumen=0.045,
    )
    geometry = spydr.SpydrFixtureGeometry(
        fixture_length_in=46.85,
        fixture_width_in=42.8,
        fixture_height_in=4.26,
        bar_length_in=40.0,
        bar_width_in=2.0,
        bar_height_in=1.0,
        fixture_length_m=1.18999,
        fixture_width_m=1.08712,
        fixture_height_m=0.108204,
        bar_length_m=1.016,
        bar_width_m=0.0508,
        bar_height_m=0.0254,
        spacing_m=0.1,
        rotation_rad=0.0,
        bar_offsets_m=[-0.15, 0.15],
        bar_offsets_in=[-5.9, 5.9],
        fixture_ppf_active=2240.0,
        fixture_ppe=2.8,
        fixture_input_w=800.0,
    )
    stage = spydr.SpydrIesStage(
        lines=[],
        lumens=1000.0,
        dims_m=None,
        spd_metrics=metrics,
        lm_to_umol=0.045,
        lm_to_umol_method="test",
        baseline_ppf=2240.0,
        baseline_radiant_w=100.0,
        target_radiant_w=100.0,
        scale=1.0,
        rad_path=Path("fixture.rad"),
        source_meta={"box_face_count": 6},
    )
    old_proxy_mode = spydr.PROXY_MODE
    try:
        spydr.PROXY_MODE = True
        spydr._write_spydr_fixture(buf, 1.25, -0.0, geometry, stage)
    finally:
        spydr.PROXY_MODE = old_proxy_mode


def _generate_local_output() -> str:
    buf = io.StringIO()
    _write_representative_output(buf)
    return buf.getvalue()


def test_emitter_identifiers_are_stable_across_hash_seeds() -> None:
    seed_one = _run_generation_in_subprocess("1")
    seed_two = _run_generation_in_subprocess("987654")

    assert seed_one == seed_two
    assert "poly_" in seed_one
    assert "bar_" in seed_one
    assert "ptfe_n_m_" in seed_one
    assert "fixture_proxy_" in seed_one


def test_geometry_material_content_is_unchanged_when_only_names_change() -> None:
    original = _generate_local_output()

    def alternate_name(prefix: str, *_values: object) -> str:
        return f"{prefix}_alternate"

    with (
        patch.object(stack, "radiance_identifier", alternate_name),
        patch.object(smd, "radiance_identifier", alternate_name),
        patch.object(spydr, "radiance_identifier", alternate_name),
    ):
        changed_names = io.StringIO()
        _write_representative_output(changed_names)

    assert _scrub_object_names(original) == _scrub_object_names(changed_names.getvalue())
    scrubbed = _scrub_object_names(original)
    assert "ptfe_liner polygon OBJECT_NAME" in scrubbed
    assert "smd_light polygon OBJECT_NAME" in scrubbed
    assert "spydr_bar polygon OBJECT_NAME" in scrubbed
    assert "!genbox spydr3_proxy OBJECT_NAME 1.189990 1.087120 0.108204" in scrubbed
    assert "  1.190000 0.050000 0.170000" in scrubbed


def test_identifier_helper_canonicalizes_negative_zero_and_rejects_nonfinite() -> None:
    assert radiance_identifier("poly", -0.0, 1.0) == radiance_identifier("poly", 0.0, 1.0)
    for value in (math.inf, -math.inf, math.nan):
        with pytest.raises(ValueError):
            radiance_identifier("poly", value)
