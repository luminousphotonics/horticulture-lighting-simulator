from __future__ import annotations

from pathlib import Path


def _asset(name: str) -> str:
    return (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "resources"
        / "web"
        / name
    ).read_text(encoding="utf-8")


def _web_server_source() -> str:
    return (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "web"
        / "app.py"
    ).read_text(encoding="utf-8")


def _opening_tag(html: str, element_id: str) -> str:
    before_id, after_id = html.split(f'id="{element_id}"', 1)
    return (
        before_id[before_id.rfind("<") :]
        + f'id="{element_id}"'
        + after_id.split(">", 1)[0]
        + ">"
    )


def test_required_controls_panels_and_outputs_are_present() -> None:
    html = _asset("index.html")
    assert "Modularized LED System" in html
    assert "1000W HPS System" in html
    assert "Proposed LED System" not in html
    assert ">HPS System</option>" not in html
    assert 'id="layout-mode-field" aria-hidden="true" hidden' in html
    assert '<option value="practical">Practical Coverage</option>' in html
    assert '<option value="rolling_bench" selected>Rolling Bench</option>' in html
    assert 'id="target-ppfd"' in html
    target_tag = html.split('id="target-ppfd"', 1)[1].split(">", 1)[0]
    assert "disabled" not in target_tag
    assert 'id="fspm-target-mode"' in html
    assert 'id="fspm-target-ppfd"' in html
    assert 'id="fspm-tolerance"' in html
    assert 'id="fspm-tolerance"' in html and 'value="75"' in html
    assert 'id="room-length"' in html
    assert 'id="room-width"' in html
    room_length_tag = html.split('id="room-length"', 1)[1].split(">", 1)[0]
    room_width_tag = html.split('id="room-width"', 1)[1].split(">", 1)[0]
    assert 'max="' not in room_length_tag
    assert 'max="' not in room_width_tag
    assert 'id="run-log"' in html
    assert 'id="metrics-grid"' in html
    assert "Final baseline PPFD" in html
    assert "Plant layout viewer" in html
    assert "PPFD Heatmap" in html
    assert "PPFD Heatmap with authoritative overlay" in html
    assert "Validated PPFD scatter" in html
    assert "Target mean PPFD" in html
    assert "Maximum PPFD cap" in _asset("result-view.js")
    assert "Spatial uniformity" in html
    assert "Mean PPFD" in html
    assert "Minimum PPFD" in html
    assert "Maximum PPFD" in html
    assert "Population standard deviation" in html
    assert "Coefficient of variation (%)" in html
    assert "Degree of uniformity (%)" in html
    assert "Minimum / mean PPFD" in html
    assert "Minimum / maximum PPFD" in html
    assert "Sample count" in html
    assert "Achieved mean" in html
    assert "Dimming factor" in html
    assert "Target feasible" in html


def test_proposed_control_module_is_served_and_hidden_state_overrides_field_layout() -> None:
    server = _web_server_source()
    html = _asset("index.html")
    styles = _asset("styles.css")
    javascript = _asset("app.js")

    assert '"proposed-control-mode-ui.js": "text/javascript; charset=utf-8"' in server
    assert "#proposed-control-mode-field[hidden] { display: none; }" in styles
    assert 'from "./proposed-control-mode-ui.js"' in javascript
    assert "synchronizeSystemState();" in javascript
    assert '/static/app.js?v=precomputed-playback-v1' in html
    assert '/static/styles.css?v=precomputed-playback-v1' in html


def test_proposed_controlled_spectrum_selector_and_unmistakable_result_label() -> None:
    html = _asset("index.html")
    javascript = _asset("app.js")
    result_renderer = _asset("result-view.js")
    helper = _asset("spectral-basis-ui.js")

    assert 'id="spectral-basis-field" aria-hidden="true" hidden' in html
    assert 'id="spectral-basis" name="spectral_basis" disabled' in html
    assert "Native Modularized LED spectrum" in html
    assert "Conventional LED spectrum (controlled A/B)" in html
    assert 'data-metric="spectral-basis"' in html
    assert 'system === "proposed"' in helper
    assert 'scope === MULTISPECTRAL_FSPM_SCOPE' in helper
    assert "select.value = NATIVE_PROPOSED_SPECTRAL_BASIS" in helper
    assert "proposedSpectralBasisRequestFields(" in javascript
    assert "Matched with Conventional LED System's SPD" in result_renderer
    assert "Spectral control:" not in result_renderer


def test_result_artifacts_are_exactly_three_whole_tile_anchors() -> None:
    html = _asset("index.html")
    artifact_grid = html.split('<div class="artifact-grid">', 1)[1].split(
        '<section class="results-panel">', 1
    )[0]

    assert html.count('class="artifact-card"') == 3
    assert artifact_grid.count("<a ") == 3
    assert "<p" not in artifact_grid
    for element_id in ("csv-card", "viewer-card", "scatter-card"):
        tag = _opening_tag(html, element_id)
        assert tag.startswith("<a ")
        assert "href=" not in tag
        assert 'data-available="false"' in tag
        assert 'aria-disabled="true"' in tag
        assert 'tabindex="-1"' in tag
    assert "<article class=\"artifact-card\"" not in html
    assert html.count('<div class="artifact-icon">CSV</div>') == 1
    assert html.count('<div class="artifact-icon">3D</div>') == 1
    assert html.count('<div class="artifact-icon">XYZ</div>') == 1

    csv_tag = _opening_tag(html, "csv-card")
    viewer_tag = _opening_tag(html, "viewer-card")
    scatter_tag = _opening_tag(html, "scatter-card")
    assert 'target="_blank"' not in csv_tag
    for tag in (viewer_tag, scatter_tag):
        assert 'target="_blank"' in tag
        assert 'rel="noopener"' in tag

    for obsolete in (
        'id="csv-link"',
        'id="viewer-link"',
        'id="scatter-link"',
        'id="csv-state"',
        'id="viewer-state"',
        'id="scatter-state"',
        ">Download<",
        ">Open viewer<",
        ">Load 3D Scatter<",
    ):
        assert obsolete not in html


def test_result_tiles_use_native_anchor_states_and_visible_focus() -> None:
    javascript = _asset("result-view.js")
    css = _asset("styles.css")

    assert 'tile.removeAttribute("href")' in javascript
    assert 'tile.setAttribute("aria-disabled", "true")' in javascript
    assert 'tile.setAttribute("tabindex", "-1")' in javascript
    assert "tile.href = artifactUrl" in javascript
    assert 'tile.setAttribute("aria-disabled", "false")' in javascript
    assert 'tile.removeAttribute("tabindex")' in javascript
    assert "keydown" not in javascript
    assert '.artifact-card[aria-disabled="true"] { pointer-events: none; }' in css
    assert '.artifact-card[data-available="true"]:hover' in css
    assert ":focus-visible" in css
    assert "outline: var(--focus-width) solid var(--color-focus)" in css
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in css
    assert ".artifact-grid, .metrics-grid { grid-template-columns: 1fr; }" in css


def test_result_tile_urls_are_exactly_allowlisted_to_the_active_run() -> None:
    javascript = _asset("result-view.js")

    assert "const RUN_ID_PATTERN = /^[0-9a-f]{32}$/;" in javascript
    assert "`/api/runs/${runId}/artifacts/ppfd.csv`" in javascript
    assert "`/runs/${runId}/viewer/index.html`" in javascript
    assert "`/runs/${runId}/scatter/index.html`" in javascript
    assert "RUN_ID_PATTERN.test(activeRun || \"\")" in javascript
    assert 'artifactUrl.protocol !== "http:"' in javascript
    assert 'artifactUrl.protocol !== "https:"' in javascript
    assert "artifactUrl.origin !== window.location.origin" in javascript
    assert 'artifactUrl.username !== ""' in javascript
    assert 'artifactUrl.password !== ""' in javascript
    assert 'artifactUrl.search !== ""' in javascript
    assert 'artifactUrl.hash !== ""' in javascript
    assert "artifactUrl.pathname !== expectedPath" in javascript
    assert "candidate !== expectedPath" in javascript
    assert "candidate !== `${window.location.origin}${expectedPath}`" in javascript
    assert "new URL(candidate, window.location.href)" in javascript
    for kind in ("csv", "viewer", "scatter"):
        assert f'artifactPathForResult(result, "{kind}")' in javascript
    assert "`/api/precomputed/playbacks/${runId}/artifacts/ppfd.csv`" in javascript
    assert "`${prefix}/viewer/index.html`" in javascript
    assert "`${prefix}/scatter/index.html`" in javascript


def test_removed_public_artifacts_and_surface_rate_rows_leave_no_ui_bindings() -> None:
    html = _asset("index.html")
    javascript = _asset("result-view.js")
    publication = (
        Path(__file__).parents[1]
        / "src/fspm_optics/application/publication.py"
    ).read_text(encoding="utf-8")
    presentation = html + javascript

    for obsolete in (
        "Raw multispectral receivers",
        "FSPM surface-light artifacts",
        "Scientific artifacts",
        "fspm-card",
        "fspm-science-card",
        "fspm-link",
        "fspm-science-link",
        "fspm-aggregation-link",
        "fspm-room-link",
        "multispectral_artifact_urls",
        "fspm_scientific_artifact_urls",
        "PAR total incident photon rate",
        "PAR total absorbed photon rate",
        "Far-red total incident photon rate",
        "Far-red total absorbed photon rate",
        "fspm-par-incident-rate",
        "fspm-par-absorbed-rate",
        "fspm-fr-incident-rate",
        "fspm-fr-absorbed-rate",
        "photon_rates_umol_s",
    ):
        assert obsolete not in presentation

    assert "PAR combined incident exposure" in html
    assert "PAR combined absorbed exposure" in html
    assert "Far-red combined incident exposure" in html
    assert "Far-red combined absorbed exposure" in html
    for label in (
        "Absorbed capture efficiency",
        "Absorbed PAR per electrical watt",
        "Plant-to-plant absorbed-exposure CV",
        "Plant minimum / mean absorbed exposure",
        "Leaf-to-leaf absorbed-exposure CV",
    ):
        assert label in html
    assert "Reference context" not in html
    assert "crop-total photon capture" in html.lower()
    assert "plant spatial consistency" in html.lower()
    assert "leaf organ-scale variability" not in html.lower()
    assert "absorbed_capture_efficiency_percent" in javascript
    assert "absorbed_par_per_electrical_watt_umol_per_j" in javascript
    assert "plant_to_plant_absorbed_exposure_cv_percent" in javascript
    assert "plant_minimum_to_mean_absorbed_exposure_ratio" in javascript
    assert "leaf_to_leaf_absorbed_exposure_cv_percent" in javascript
    assert "µmol/J" in javascript
    assert 'setMetric("fspm-reference-context"' not in javascript
    assert '"absorbed_par_metrics": (' in publication
    assert "function renderSurfaceMetrics(surfaceMetrics)" in javascript
    surface_block = javascript.split(
        "function renderSurfaceMetrics(surfaceMetrics)", 1
    )[1].split("async function loadVisualizations", 1)[0]
    for binding in (
        "fspm-absorbed-capture-efficiency",
        "fspm-absorbed-par-per-watt",
        "fspm-plant-absorbed-cv",
        "fspm-plant-minimum-mean",
        "fspm-leaf-absorbed-cv",
    ):
        assert f'setMetric("{binding}"' in surface_block
    assert "formatFixed(captureEfficiency, 3)" in surface_block
    assert "formatFixed(absorbedPerWatt, 3)" in surface_block
    assert "formatFixed(plantCv, 3)" in surface_block
    assert "formatFixed(plantMinimumMean, 3)" in surface_block
    assert "formatFixed(leafCv, 3)" in surface_block
    assert 'id="fspm-surface-section" hidden' in html
    assert 'id="fspm-fr-incident-row" hidden' in html
    assert 'id="fspm-fr-absorbed-row" class="wide" hidden' in html
    assert "surfaceMetrics.far_red_executed === true" in javascript
    assert "surfaceMetrics.far_red_executed === false" in javascript
    assert "farRedIncidentRow.hidden = !farRedAvailable" in javascript
    assert "farRedAbsorbedRow.hidden = !farRedAvailable" in javascript
    assert '"surface_light": (' in publication
    assert '"band_order": list(' in publication
    assert '"far_red_executed": (' in publication


def test_power_and_ppf_labels_are_simplified_and_explained() -> None:
    html = _asset("index.html")
    assert "Emitted PPF" in html
    assert "Modeled electrical power" in html
    for label in (
        "Maximum-schedule PPF",
        "Target-controlled PPF",
        "Maximum-schedule power",
        "Target-controlled power",
    ):
        assert f"<dt>{label}</dt>" not in html
    assert "Full / effective PPF" not in html
    assert "Full / effective power" not in html
    assert "CV-optimal relative schedule" not in html
    assert "global dimming factor" not in html
    assert "PPF</strong> is emitted photon output" in html
    assert "Power</strong> is modeled electrical input" in html
    javascript = _asset("result-view.js")
    assert "metrics.ppf?.emitted_umol_s" in javascript
    assert "metrics.ppf.effective_umol_s" not in javascript
    assert "metrics.power?.effective_w" in javascript
    assert "metrics.ppf.full_output_umol_s" not in javascript
    assert "metrics.power.full_output_w" not in javascript


def test_emitted_ppf_fails_closed_before_ui_formatting() -> None:
    javascript = _asset("result-view.js")
    assert (
        'requireFiniteResultMetric(metrics.ppf?.emitted_umol_s, '
        '"ppf.emitted_umol_s")' in javascript
    )
    assert 'typeof value !== "number" || !Number.isFinite(value)' in javascript
    assert "Result contract error: ${path} must be a finite number." in javascript
    assert "format(emittedPpf)" in javascript
    assert "format(metrics.ppf?.emitted_umol_s)" not in javascript
    assert "format(metrics.ppf.emitted_umol_s)" not in javascript


def test_ui_renders_every_uniformity_value_from_metrics_json() -> None:
    javascript = _asset("result-view.js")
    for metric_name in (
        "mean_ppfd",
        "minimum_ppfd",
        "maximum_ppfd",
        "population_standard_deviation_ppfd",
        "coefficient_of_variation_percent",
        "degree_of_uniformity_percent",
        "minimum_to_mean_uniformity",
        "minimum_to_maximum_ppfd_ratio",
        "sample_count",
    ):
        assert f"uniformity.{metric_name}" in javascript


def test_five_baseline_leaf_tiles_are_scope_and_surface_light_independent() -> None:
    html = _asset("index.html")
    javascript = _asset("result-view.js")
    leaf_metrics = (
        "baseline-leaf-target-range",
        "baseline-leaf-under-lit",
        "baseline-leaf-over-lit",
        "baseline-leaf-mad",
        "baseline-leaf-cv",
    )
    leaf_section = html.split(
        '<section id="baseline-leaf-uniformity-section">', 1
    )[1].split('<section id="fspm-surface-section"', 1)[0]

    assert leaf_section.count("<dt>") == 5
    for metric in leaf_metrics:
        assert leaf_section.count(f'data-metric="{metric}"') == 1
        assert f'"{metric}"' in javascript
    assert "Target-range leaves" in leaf_section
    assert "Under-lit leaves" in leaf_section
    assert "Over-lit leaves" in leaf_section
    assert "Mean absolute deviation" in leaf_section
    assert "Leaf-position PPFD CV" in leaf_section
    assert 'id="baseline-leaf-uniformity-section" hidden' not in html
    assert javascript.index("renderBaselineLeafUniformity(") < javascript.index(
        "renderSurfaceMetrics(metrics.fspm_surface_light_metrics)"
    )
    assert 'return `${metric.count}/${percentage}%`' in javascript
    assert 'setMetric(name, "Unavailable")' in javascript
    assert 'cv?.available === true' in javascript
    assert "metrics.analysis_scope" not in javascript.split(
        "function renderBaselineLeafUniformity", 1
    )[1].split("function formatLeafCountPercentage", 1)[0]


def test_uniformity_ui_uses_percentage_and_decimal_ratio_units() -> None:
    html = _asset("index.html")
    javascript = _asset("result-view.js")

    assert html.count("Coefficient of variation") == 1
    assert 'data-metric="uniformity-cv"' not in html
    assert 'data-metric="uniformity-cv-percent"' in html
    assert 'data-metric="uniformity-degree-percent"' in html
    assert "uniformity.coefficient_of_variation," not in javascript
    minimum_mean_line = next(
        line for line in javascript.splitlines()
        if 'setMetric("uniformity-minimum-mean"' in line
    )
    minimum_maximum_line = next(
        line for line in javascript.splitlines()
        if 'setMetric("uniformity-minimum-maximum"' in line
    )
    assert "%" not in minimum_mean_line
    assert "%" not in minimum_maximum_line


def test_quality_choices_are_exact_and_standard_is_default() -> None:
    html = _asset("index.html")
    for value in ("direct", "standard", "quality", "rigorous"):
        assert html.count(f'name="quality" value="{value}"') == 1
    assert 'name="quality" value="standard" checked' in html


def test_ui_contains_no_out_of_scope_runtime_controls_or_fake_progress() -> None:
    html_and_js = _asset("index.html") + _asset("app.js")
    for forbidden in (
        "docker",
        "composite validation",
        "peak capping",
        "growth response",
        "cost controls",
        "progresspercent",
        "interpolation",
        "symmetrization",
        "clipping",
    ):
        assert forbidden not in html_and_js.lower()
