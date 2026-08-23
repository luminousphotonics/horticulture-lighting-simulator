from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import urljoin

from fspm_optics.application.domain import (
    ProposedControlMode,
    ProposedRunRequest,
    ProposedSourceMode,
)
from fspm_optics.layout.ring import ProposedRingMode
from fspm_optics.web.public import _public_index


REPOSITORY = Path(__file__).resolve().parents[1]
WEB = REPOSITORY / "src/fspm_optics/resources/web"
VIEWER = REPOSITORY / "src/fspm_optics/resources/viewer"
SCATTER = REPOSITORY / "src/fspm_optics/resources/scatter"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _opening_tag(html: str, element_id: str) -> str:
    match = re.search(rf"<[^>]+\bid=\"{re.escape(element_id)}\"[^>]*>", html)
    assert match is not None
    return match.group(0)


def _proposed_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "system": "proposed",
        "target_ppfd": 500.0,
        "lighting_target_mode": "mean_target",
        "room_length_ft": 10.0,
        "room_width_ft": 10.0,
        "quality": "standard",
        "analysis_scope": "baseline_ppfd",
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 75.0,
        "mounting_height_in": 18.0,
        "aisle_mode": False,
    }
    payload.update(updates)
    return payload


def test_hidden_live_controls_have_exact_effective_and_submitted_defaults() -> None:
    html = _text(WEB / "index.html")
    app = _text(WEB / "app.js")
    ring_helper = _text(WEB / "proposed-ring-mode-ui.js")
    control_helper = _text(WEB / "proposed-control-mode-ui.js")
    source_helper = _text(WEB / "proposed-source-mode-ui.js")

    for field_id in (
        "proposed-ring-mode-field",
        "proposed-control-mode-field",
        "proposed-source-mode-field",
        "fspm-target-field",
    ):
        tag = _opening_tag(html, field_id)
        assert " hidden" in tag
        assert 'aria-hidden="true"' in tag

    assert '<option value="reduced_one_ring" selected>' in html
    assert 'id="disable-basis-matrix-solver" type="checkbox" checked disabled' in html
    assert 'id="cob-mode" type="checkbox" disabled' in html
    assert not re.search(r'id="cob-mode"[^>]*\bchecked\b', html)
    assert '<option value="automatic" selected>' in html
    assert 'id="fspm-target-mode" name="fspm_target_mode" disabled' in html

    assert 'select.value = REDUCED_ONE_RING;' in ring_helper
    assert 'input.checked = proposed;' in control_helper
    assert 'input.checked = false;' in source_helper
    for helper in (ring_helper, control_helper, source_helper):
        assert 'field.hidden = true;' in helper
        assert 'field.setAttribute("aria-hidden", "true")' in helper

    assert 'fspm_target_mode: "automatic"' in app
    assert "payload.fspm_target_ppfd" not in app
    assert re.search(
        r"proposedRingModeRequestFields\(\s*systemSelect\.value,\s*"
        r"REDUCED_ONE_RING,\s*\)",
        app,
    )
    assert re.search(
        r"proposedControlModeRequestFields\(\s*systemSelect\.value,\s*true,\s*\)",
        app,
    )
    assert "proposedSourceModeRequestFields(systemSelect.value, false)" in app

    request = ProposedRunRequest.from_payload(
        _proposed_payload(
            proposed_ring_mode="reduced_one_ring",
            proposed_control_mode="uniform_module_dimming",
            proposed_source_mode="native_smd",
        )
    )
    assert request.proposed_ring_mode is ProposedRingMode.REDUCED_ONE_RING
    assert request.control_mode is ProposedControlMode.UNIFORM_MODULE_DIMMING
    assert request.source_mode is ProposedSourceMode.NATIVE_SMD
    assert request.fspm_target_mode == "automatic"
    assert request.fspm_target_override_umol_m2_s is None
    serialized = request.to_dict()
    assert serialized["proposed_ring_mode"] == "reduced_one_ring"
    assert serialized["proposed_control_mode"] == "uniform_module_dimming"
    assert serialized["proposed_source_mode"] == "native_smd"
    assert serialized["fspm_target"]["mode"] == "automatic"


def test_live_system_titles_match_public_labels_without_a_baseline_suffix() -> None:
    html = _text(WEB / "index.html")
    app = _text(WEB / "app.js")
    assert '<h1 id="system-heading">Modularized LED System</h1>' in html
    assert "System Baseline" not in html
    for system in ("proposed", "conventional", "hps"):
        assert f'heading: systemDisplayLabel("{system}")' in app
    assert '} Baseline`' not in app


def test_hidden_controls_do_not_remove_supported_request_fields_or_tolerance() -> None:
    alternate = ProposedRunRequest.from_payload(
        _proposed_payload(
            proposed_ring_mode="full",
            proposed_control_mode="basis_matrix_optimized",
            proposed_source_mode="cob_source_shape_surrogate",
            fspm_target_mode="override",
            fspm_target_ppfd=625.0,
            fspm_target_tolerance=42.0,
        )
    )
    assert alternate.proposed_ring_mode is ProposedRingMode.FULL
    assert alternate.control_mode is ProposedControlMode.BASIS_MATRIX_OPTIMIZED
    assert alternate.source_mode is ProposedSourceMode.COB_SOURCE_SHAPE_SURROGATE
    assert alternate.fspm_target_mode == "override"
    assert alternate.fspm_target_override_umol_m2_s == 625.0
    assert alternate.fspm_target_tolerance_umol_m2_s == 42.0


def test_live_metrics_are_the_exact_public_fragment_and_close_each_grid() -> None:
    live_html = _text(WEB / "index.html")
    public_html = _public_index().decode("utf-8")
    renderer = _text(WEB / "result-view.js")
    styles = _text(WEB / "styles.css")
    shared = live_html.split("<!-- SHARED_RESULT_VIEW_START -->", 1)[1].split(
        "<!-- SHARED_RESULT_VIEW_END -->", 1
    )[0].strip()
    assert shared in public_html

    metrics = shared.split('<dl id="metrics-grid"', 1)[1].split("</dl>", 1)[0]
    for removed in (
        "Modularized LED control mode",
        "Module arrangement",
        "Modularized LED source mode",
        "Fixtures / modules / zones",
    ):
        assert removed not in metrics
    assert '<dt>Spectral basis</dt>' in metrics
    assert '<dt>Modules</dt><dd data-metric="modules">' in metrics
    assert 'setMetric("modules", String(metrics.counts.modules))' in renderer
    assert "metrics.counts.fixtures" not in renderer
    assert "metrics.counts.control_zones" not in renderer
    assert "Matched with Conventional LED System's SPD" in renderer
    assert "publicPresentation" not in renderer

    assert _opening_tag(shared, "fspm-leaf-absorbed-row").count('class="wide"') == 1
    assert _opening_tag(shared, "fspm-fr-absorbed-row").count('class="wide"') == 1
    assert 'fspmLeafAbsorbedRow.classList.toggle("wide", !farRedAvailable)' in renderer
    assert ".metrics-grid .wide { grid-column: 1 / -1; }" in styles
    assert ".metrics-grid .wide { grid-column: auto; }" in styles


def test_plant_viewer_return_link_is_base_path_safe_centered_and_focus_visible() -> None:
    html = _text(VIEWER / "index.html")
    styles = _text(VIEWER / "styles.css")
    assert '<a class="return-to-simulator" href="../../../">Return to Simulator</a>' in html
    assert urljoin(
        "https://example.test/runs/" + "a" * 32 + "/viewer/index.html",
        "../../../",
    ) == "https://example.test/"
    assert urljoin(
        "https://example.test/deploy/precomputed/" + "b" * 32 + "/viewer/index.html",
        "../../../",
    ) == "https://example.test/deploy/"
    assert "grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr)" in styles
    assert ".return-to-simulator" in styles
    assert "color: #fff;" in styles
    assert "background: var(--color-accent);" in styles
    assert ":where(a, button, input, select):focus-visible" in styles
    assert 'grid-template-areas: "heading status" "return return"' in styles


def test_scatter_initial_view_uses_the_remaining_mobile_and_desktop_viewport() -> None:
    html = _text(SCATTER / "index.html")
    styles = _text(SCATTER / "styles.css")
    assert "X · position (m)" in html
    assert "Y · position (m)" in html
    assert "Z · PPFD (µmol/m²/s)" in html
    assert "body { height: 100vh; height: 100dvh;" in styles
    assert "main { height: calc(100% - 76px); min-height: 0;" in styles
    assert ".viewport-shell { min-width: 0; min-height: 0;" in styles
    assert "aside { min-height: 0; overflow-y: auto;" in styles
    assert ".viewport-shell { height: calc(100svh - 84px); min-height: 0; }" in styles
    assert ".axis-context { right: 12px; bottom: 12px; left: 12px;" in styles
