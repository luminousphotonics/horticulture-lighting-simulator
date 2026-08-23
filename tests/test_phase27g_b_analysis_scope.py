from __future__ import annotations

import ast
from pathlib import Path

import pytest
import re

from fspm_optics.application.domain import (
    ANALYSIS_SCOPE_DESCRIPTIONS,
    ANALYSIS_SCOPE_LABELS,
    AnalysisScope,
    RequestValidationError,
    parse_run_request,
)
from fspm_optics.application.multispectral import (
    BAND_ORDER,
    JuvenileBandInput,
    JuvenileSourceAdapter,
)
from fspm_optics.application.proposed import run_proposed_baseline
from fspm_optics.application.systems import (
    run_conventional_baseline,
    run_hps_baseline,
)
from fspm_optics.application.source_state import PhysicalSourceState, hash_json
from fspm_optics.geometry.room import RoomDimensions, room_radiance_text
from fspm_optics.plants.radiance_scene_export import (
    DEFAULT_LEAF_MATERIAL_MODIFIER,
)
from fspm_optics.web.jobs import JobRecord


REPOSITORY = Path(__file__).parents[1]


def _request(system: str, *, scope: object | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "system": system,
        "room_length_ft": 10.0,
        "room_width_ft": 9.0,
        "quality": "direct",
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 20.0,
    }
    if system != "hps":
        payload["target_ppfd"] = 900.0
    if scope is not None:
        payload["analysis_scope"] = scope
    return payload


@pytest.mark.parametrize("system", ["proposed", "conventional", "hps"])
def test_analysis_scope_defaults_to_baseline_for_every_system(system: str) -> None:
    request = parse_run_request(_request(system))

    assert request.analysis_scope is AnalysisScope.BASELINE_PPFD
    assert request.to_dict()["analysis_scope"] == "baseline_ppfd"
    assert request.include_far_red is None
    assert "include_far_red" not in request.to_dict()


@pytest.mark.parametrize("system", ["proposed", "conventional", "hps"])
def test_multispectral_scope_is_accepted_for_every_system(system: str) -> None:
    request = parse_run_request(
        _request(system, scope="baseline_plus_multispectral_fspm")
    )

    assert request.analysis_scope is AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM
    assert request.to_dict()["analysis_scope"] == (
        "baseline_plus_multispectral_fspm"
    )
    assert request.include_far_red is False
    assert request.to_dict()["include_far_red"] is False


@pytest.mark.parametrize("include_far_red", [False, True])
def test_multispectral_far_red_selection_is_canonical_and_identity_bearing(
    include_far_red: bool,
) -> None:
    request = parse_run_request(
        _request("proposed", scope="baseline_plus_multispectral_fspm")
        | {"include_far_red": include_far_red}
    )

    assert request.include_far_red is include_far_red
    assert request.to_dict()["include_far_red"] is include_far_red


@pytest.mark.parametrize("system", ["proposed", "conventional", "hps"])
@pytest.mark.parametrize("include_far_red", [False, True])
def test_baseline_scope_rejects_far_red_field_even_when_false(
    system: str,
    include_far_red: bool,
) -> None:
    with pytest.raises(RequestValidationError) as caught:
        parse_run_request(
            _request(system, scope="baseline_ppfd")
            | {"include_far_red": include_far_red}
        )

    assert caught.value.field == "include_far_red"


@pytest.mark.parametrize("include_far_red", [None, 0, 1, "false", [], {}])
def test_multispectral_scope_rejects_non_boolean_far_red_selection(
    include_far_red: object,
) -> None:
    with pytest.raises(RequestValidationError) as caught:
        parse_run_request(
            _request("proposed", scope="baseline_plus_multispectral_fspm")
            | {"include_far_red": include_far_red}
        )

    assert caught.value.field == "include_far_red"


def test_normalized_request_and_job_record_preserve_scope(tmp_path: Path) -> None:
    request = parse_run_request(
        _request("proposed", scope="baseline_plus_multispectral_fspm")
    )
    record = JobRecord(
        job_id="a" * 32,
        run_id="b" * 32,
        request=request,
        workspace=tmp_path,
    )

    assert record.request.analysis_scope is (
        AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM
    )
    assert record.request.to_dict()["analysis_scope"] == (
        "baseline_plus_multispectral_fspm"
    )


@pytest.mark.parametrize(
    "scope",
    ["baseline", "multispectral_fspm", "BASELINE_PPFD", "", 1, {}, []],
)
def test_unknown_or_malformed_analysis_scope_is_rejected(scope: object) -> None:
    with pytest.raises(RequestValidationError) as caught:
        parse_run_request(_request("proposed", scope=scope))

    assert caught.value.field == "analysis_scope"


@pytest.mark.parametrize(
    "scope", ["baseline_ppfd", "baseline_plus_multispectral_fspm"]
)
def test_hps_lighting_target_remains_prohibited_under_both_scopes(
    scope: str,
) -> None:
    with pytest.raises(RequestValidationError) as caught:
        parse_run_request(
            _request("hps", scope=scope) | {"target_ppfd": 1000.0}
        )

    assert caught.value.field == "target_ppfd"


def test_scope_labels_descriptions_and_browser_contract_are_exact() -> None:
    assert ANALYSIS_SCOPE_LABELS == {
        AnalysisScope.BASELINE_PPFD: "Baseline PPFD",
        AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM: (
            "Baseline + Multispectral FSPM"
        ),
    }
    assert ANALYSIS_SCOPE_DESCRIPTIONS == {
        AnalysisScope.BASELINE_PPFD: (
            "Plant-free horizontal PPFD, uniformity metrics, CSV, heatmaps, "
            "fixture overlays, and 3D scatter."
        ),
        AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM: (
            "Runs the same plant-free baseline first, then performs four-band PAR "
            "transport through the populated plant scene and publishes "
            "authoritative surface-light aggregation. Far-red is optional."
        ),
    }
    index = (
        REPOSITORY / "src/fspm_optics/resources/web/index.html"
    ).read_text(encoding="utf-8")
    script = (
        REPOSITORY / "src/fspm_optics/resources/web/app.js"
    ).read_text(encoding="utf-8")
    assert "<span>Analysis Scope</span>" in index
    assert '<option value="baseline_ppfd">Baseline PPFD</option>' in index
    assert "Baseline + Multispectral FSPM</option>" in index
    assert "analysis_scope: analysisScopeSelect.value" in script
    assert "Optional far-red analysis" in index
    assert (
        'id="far-red-analysis-field" aria-hidden="true" hidden'
        in index
    )
    assert (
        'id="include-far-red" name="include_far_red" '
        'type="checkbox" disabled'
    ) in index
    assert 'id="include-far-red" checked' not in index
    scope_ui = (
        REPOSITORY / "src/fspm_optics/resources/web/analysis-scope-ui.js"
    ).read_text(encoding="utf-8")
    assert "farRedRequestFields(analysisScopeSelect.value" in script
    assert "field.hidden = !visible" in scope_ui
    assert 'field.querySelectorAll?.("input, select, textarea, button")' in scope_ui
    assert "control.disabled = !visible" in scope_ui
    assert "if (!visible) input.checked = false" in scope_ui
    assert "window.addEventListener(\"pageshow\"" in script
    assert "window.addEventListener(\"popstate\"" in script
    assert "analysisScopeSelect.addEventListener(\"input\"" in script
    assert "form.addEventListener(\"reset\"" in script
    assignments = re.findall(
        r"\banalysisScopeSelect\.value\s*=\s*([^;]+);",
        script,
    )
    assert assignments == ["MULTISPECTRAL_FSPM_SCOPE"]
    assert "if (!playback)" in script
    assert ANALYSIS_SCOPE_DESCRIPTIONS[AnalysisScope.BASELINE_PPFD] in script
    assert (
        ANALYSIS_SCOPE_DESCRIPTIONS[
            AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM
        ]
        in script
    )
    assert "Display-only material — never FSPM flux" not in index


def test_physical_source_state_is_canonical_immutable_and_hash_linked() -> None:
    layout = {"layout_id": "layout-v1", "fixtures": 4}
    schedule = {"policy": "final-stage-a", "relative": [1.0, 0.8]}
    operating_point = {"dimming_factor": 0.75, "power": {"effective_w": 75.0}}
    state = PhysicalSourceState.create(
        system_id="proposed",
        layout_identity=layout,
        full_output_schedule=schedule,
        operating_point=operating_point,
        source_operation={
            "target_control_resolved_in_stage_a": True,
            "global_linear_dimming_factor": 0.75,
        },
    )

    first = state.payload()
    first["system_id"] = "mutated"
    assert state.payload()["system_id"] == "proposed"
    assert state.payload()["layout_identity_sha256"] == hash_json(layout)
    assert state.payload()["full_output_schedule_sha256"] == hash_json(schedule)
    assert state.payload()["operating_point_sha256"] == hash_json(operating_point)
    assert state.payload()["handoff_contract"] == {
        "target_control_recomputed_by_later_stage": False,
        "fspm_reference_controls_transport": False,
        "post_trace_reconciliation": False,
    }
    four_band_request = parse_run_request(
        _request("proposed", scope="baseline_plus_multispectral_fspm")
        | {"include_far_red": False}
    )
    five_band_request = parse_run_request(
        _request("proposed", scope="baseline_plus_multispectral_fspm")
        | {"include_far_red": True}
    )
    assert hash_json(four_band_request.to_dict()) != hash_json(
        five_band_request.to_dict()
    )
    publication_source = (
        REPOSITORY / "src/fspm_optics/application/publication.py"
    ).read_text(encoding="utf-8")
    assert 'identities["multispectral_transport_metadata_sha256"]' in (
        publication_source
    )
    assert 'identities["fspm_scientific_aggregation_metadata_sha256"]' in (
        publication_source
    )
    assert 'identities["scientific_run_sha256"] = _hash_json(identities)' in (
        publication_source
    )
    assert state.source_state_id == PhysicalSourceState.create(
        system_id="proposed",
        layout_identity=layout,
        full_output_schedule=schedule,
        operating_point=operating_point,
        source_operation={
            "target_control_resolved_in_stage_a": True,
            "global_linear_dimming_factor": 0.75,
        },
    ).source_state_id


def test_stage_b_source_planning_has_no_fspm_reference_input(
    tmp_path: Path,
) -> None:
    state = PhysicalSourceState.create(
        system_id="proposed",
        layout_identity={"layout_id": "layout-v1"},
        full_output_schedule={"policy": "final-stage-a"},
        operating_point={"dimming_factor": 0.75},
        source_operation={"global_linear_dimming_factor": 0.75},
    )
    material = (
        f"void trans {DEFAULT_LEAF_MATERIAL_MODIFIER}\n"
        "0\n0\n7 0.1 0.1 0.1 0 0 0.5 1\n"
    )
    fixture_body = tmp_path / "fixture_body_instances.rad"
    fixture_body.write_text("# authenticated fixture body\n", encoding="utf-8")
    adapter = JuvenileSourceAdapter(
        system_id="proposed",
        source_state_id=state.source_state_id,
        room_text=room_radiance_text(RoomDimensions(3.048, 3.048, 3.048)),
        bands=tuple(
            JuvenileBandInput(
                band_id=band_id,
                source_text=f"# isolated {band_id}\n",
                material_text=material,
                source_provenance={"band_id": band_id},
                material_provenance={"band_id": band_id},
            )
            for band_id in BAND_ORDER
        ),
        source_policy={"target_control_recomputed": False},
        quality_profile="direct",
        threads=1,
        oconv_bin="oconv",
        rtrace_bin="rtrace",
        fixture_body_source_path=fixture_body,
        fixture_occlusion={
            "system_id": "proposed",
            "identity_sha256": "a" * 64,
        },
    )

    planning = adapter.planning_payload()
    assert "fspm" not in repr(planning).lower()
    assert planning["source_state_id"] == state.source_state_id
    assert planning["post_trace_scaling"] is False
    assert planning["symmetry_reconstruction"] is False


@pytest.mark.parametrize(
    "orchestrator",
    [run_proposed_baseline, run_conventional_baseline, run_hps_baseline],
)
def test_stage_b_dispatch_is_guarded_after_stage_a_source_resolution(
    orchestrator: object,
) -> None:
    source = Path(orchestrator.__code__.co_filename).read_text(encoding="utf-8")
    function_source = source[source.index(f"def {orchestrator.__name__}(") :]
    source_state_index = function_source.index("source_state = PhysicalSourceState.create(")
    scope_guard_index = function_source.index(
        "if request.analysis_scope is AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM:"
    )
    stage_b_index = function_source.index(
        "execute_juvenile_multispectral_transport("
    )

    assert source_state_index < scope_guard_index < stage_b_index
    guarded_source = function_source[scope_guard_index:stage_b_index]
    assert "source_state=source_state" in function_source[
        stage_b_index : stage_b_index + 700
    ]
    assert "target_feasible" not in guarded_source


@pytest.mark.parametrize(
    ("orchestrator", "frozen_stage_a_names"),
    [
        (
            run_proposed_baseline,
            {
                "achieved_samples",
                "schedule_payload",
                "target_payload",
                "operating_point",
                "source_state",
            },
        ),
        (
            run_conventional_baseline,
            {
                "full",
                "final",
                "schedule",
                "target_control",
                "operating_point",
                "source_state",
            },
        ),
        (
            run_hps_baseline,
            {
                "result",
                "schedule",
                "target_control",
                "operating_point",
                "source_state",
            },
        ),
    ],
)
def test_stage_b_guard_cannot_reassign_baseline_scientific_state(
    orchestrator: object,
    frozen_stage_a_names: set[str],
) -> None:
    source = Path(orchestrator.__code__.co_filename).read_text(encoding="utf-8")
    module = ast.parse(source)
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == orchestrator.__name__
    )
    guard = next(
        node
        for node in function.body
        if isinstance(node, ast.If)
        and "request.analysis_scope" in ast.unparse(node.test)
    )
    assigned_inside_stage_b = {
        node.id
        for node in ast.walk(guard)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    stage_b_source = ast.unparse(guard)

    assert assigned_inside_stage_b.isdisjoint(frozen_stage_a_names)
    assert "apply_target_control" not in stage_b_source
    assert "resolve_global_source_dimming" not in stage_b_source
