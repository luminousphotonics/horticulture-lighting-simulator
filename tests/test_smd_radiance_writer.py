from __future__ import annotations

import json
import math
from pathlib import Path
import re

import pytest

from fspm_optics.fixtures.smd.artifacts import (
    format_smd_emitter_metadata_json,
    write_smd_emitter_artifacts,
)
from fspm_optics.fixtures.smd.optical_stack import (
    ACCEPTED_FIXTURE_TRANSMISSION,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    EMITTER_Z_M,
    INTERNAL_SOURCE_PPE_UMOL_PER_J,
    PROPOSED_FIXTURE_OPTICAL_STACK_ID,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.power_schedule import build_optimized_module_schedule
from fspm_optics.fixtures.smd.radiance_writer import (
    SmdEmitterAssumptions,
    build_smd_radiance_document,
)


def _document() -> object:
    layout = generate_proposed_led_layout(10, 10)
    schedule = build_optimized_module_schedule(
        layout,
        (20.0, 25.0, 30.0, 35.0, 40.0),
    )
    return build_smd_radiance_document(layout, schedule)


def _vertices(text: str, primitive: str) -> tuple[tuple[float, float, float], ...]:
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if line.endswith(f" polygon {primitive}"))
    count = int(lines[index + 3]) // 3
    return tuple(
        tuple(float(value) for value in lines[index + 4 + i].split())  # type: ignore[misc]
        for i in range(count)
    )


def _normal(vertices: tuple[tuple[float, float, float], ...]) -> tuple[float, float, float]:
    a, b, c = vertices[:3]
    ab = tuple(b[i] - a[i] for i in range(3))
    ac = tuple(c[i] - a[i] for i in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    length = math.sqrt(sum(value**2 for value in cross))
    return tuple(value / length for value in cross)


def test_writer_is_deterministic_and_emits_one_macro_source_and_stack_per_module() -> None:
    first = _document()
    second = _document()
    assert first == second
    text = first.radiance_text  # type: ignore[attr-defined]
    metadata = first.metadata  # type: ignore[attr-defined]
    assert metadata.module_count == 61
    assert metadata.emitter_primitive_count == 61
    assert metadata.optical_stack_polygon_count == 61 * 14
    assert text.count("_internal_emitter\n") == 61
    assert text.count("# module ") == 61
    assert text.count(" light smd_control_zone_") == 5
    assert text.count("proposed_pmma polygon") == 61 * 6
    assert text.count("proposed_ptfe polygon") == 61 * 4
    assert text.count("proposed_black_bezel polygon") == 61 * 4


def test_watts_are_normalized_to_internal_ppf_before_native_transport() -> None:
    document = _document()
    metadata = document.metadata  # type: ignore[attr-defined]
    assert metadata.internal_source_ppe_umol_per_j == INTERNAL_SOURCE_PPE_UMOL_PER_J
    assert metadata.completed_aperture_fixture_ppe_umol_per_j == 2.6
    assert metadata.accepted_fixture_transmission == ACCEPTED_FIXTURE_TRANSMISSION
    assert metadata.total_internal_par_ppf_umol_s == pytest.approx(
        metadata.total_watts * INTERNAL_SOURCE_PPE_UMOL_PER_J
    )
    assert metadata.modeled_completed_aperture_par_ppf_umol_s == pytest.approx(
        metadata.total_watts * COMPLETED_APERTURE_PPE_UMOL_PER_J
    )
    assert (
        metadata.total_internal_par_ppf_umol_s
        * metadata.accepted_fixture_transmission
    ) == pytest.approx(metadata.modeled_completed_aperture_par_ppf_umol_s)
    assert metadata.optical_stack_id == PROPOSED_FIXTURE_OPTICAL_STACK_ID
    assert "source normalization occurs before transport" in document.radiance_text  # type: ignore[attr-defined]
    assert "no receiver, PPFD-map, plant-absorption" in document.radiance_text  # type: ignore[attr-defined]
    assert "2.800" not in document.radiance_text  # type: ignore[attr-defined]


def test_mount_coordinate_is_aperture_and_emitter_is_11mm_above_facing_down() -> None:
    layout = generate_proposed_led_layout(10, 10, mount_z_m=1.5)
    schedule = build_optimized_module_schedule(layout, (1, 2, 3, 4, 5))
    text = build_smd_radiance_document(layout, schedule).radiance_text
    module = layout.modules[0]
    emitter = _vertices(text, "smd_m0000_internal_emitter")
    assert {vertex[2] for vertex in emitter} == {module.z_m + EMITTER_Z_M}
    assert _normal(emitter) == pytest.approx((0.0, 0.0, -1.0))
    pmma = tuple(
        vertex
        for face in ("top", "bottom", "north", "south", "west", "east")
        for vertex in _vertices(text, f"proposed_m0000_pmma_{face}")
    )
    assert {vertex[2] for vertex in pmma} == {module.z_m, module.z_m + 0.003}


def test_artifact_fields_distinguish_internal_and_completed_aperture_flux(tmp_path: Path) -> None:
    document = _document()
    paths = write_smd_emitter_artifacts(
        document,
        radiance_path=tmp_path / "smd.rad",
        metadata_path=tmp_path / "smd.json",
    )
    assert paths.radiance_path.read_text(encoding="utf-8") == document.radiance_text  # type: ignore[attr-defined]
    assert paths.source_variant_cal_path is None
    assert paths.metadata_path.read_text(encoding="utf-8") == format_smd_emitter_metadata_json(document)  # type: ignore[arg-type]
    payload = json.loads(paths.metadata_path.read_text(encoding="utf-8"))
    inputs = payload["basis_inputs"]
    assert inputs["internal_source_ppe_umol_per_j"] == INTERNAL_SOURCE_PPE_UMOL_PER_J
    assert inputs["completed_aperture_fixture_ppe_umol_per_j"] == 2.6
    assert inputs["accepted_fixture_transmission"] == ACCEPTED_FIXTURE_TRANSMISSION
    assert inputs["optical_stack_id"] == PROPOSED_FIXTURE_OPTICAL_STACK_ID


@pytest.mark.parametrize("variant", ("cosine_100deg", "cosine_140deg"))
def test_uncalibrated_research_variants_cannot_claim_fixture_ppe(variant: str) -> None:
    layout = generate_proposed_led_layout(10, 10)
    schedule = build_optimized_module_schedule(layout, (1, 2, 3, 4, 5))
    with pytest.raises(ValueError, match="research-only.*cannot claim 2.6"):
        build_smd_radiance_document(
            layout,
            schedule,
            assumptions=SmdEmitterAssumptions(source_variant=variant),
        )


def test_active_proposed_production_paths_contain_no_2p8_ppe_authority() -> None:
    root = Path(__file__).parents[1] / "src" / "fspm_optics"
    paths = (
        root / "fixtures" / "smd" / "radiance_writer.py",
        root / "fixtures" / "smd" / "band_writer.py",
        root / "application" / "target_control.py",
        root / "application" / "multispectral.py",
        root / "transport" / "five_band.py",
    )
    exact_2p8 = re.compile(r"(?<![\d.])2\.8(?!\d)")
    assert all(
        exact_2p8.search(path.read_text(encoding="utf-8")) is None
        for path in paths
    )
    application_source = (root / "application" / "proposed.py").read_text(
        encoding="utf-8"
    )
    assert "scale_ppfd_map" not in application_source
    assert '"post_trace_scale"' not in application_source
