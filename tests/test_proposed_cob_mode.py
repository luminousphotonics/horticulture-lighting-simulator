from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
import re
from pathlib import Path

import pytest

from fspm_optics.application.domain import (
    ConventionalRunRequest,
    ProposedControlMode,
    ProposedRunRequest,
    ProposedSourceMode,
    RequestValidationError,
)
from fspm_optics.application.multispectral import (
    JuvenileMultispectralError,
    build_proposed_juvenile_source_adapter,
)
from fspm_optics.application.source_state import PhysicalSourceState
from fspm_optics.application.target_control import (
    derive_uniform_full_output_schedule,
)
from fspm_optics.fixtures.proposed_cob import source as cob_source
from fspm_optics.fixtures.proposed_cob.characterization import (
    CobCharacterizationError,
    execute_characterization,
)
from fspm_optics.fixtures.proposed_cob.source import (
    COB_ANGULAR_DAT_FILENAME,
    COB_IES_SHA256,
    COB_LES_AREA_M2,
    COB_SOURCE_MODE,
    NATIVE_SOURCE_MODE,
    CobSourceError,
    load_cob_angular_law,
    native_proposed_spd_identity_sha256,
    resolve_proposed_source_authority,
)
from fspm_optics.fixtures.occlusion import (
    materialize_fixture_occlusion,
    plan_fixture_occlusion,
    proposed_layout_transport_payload,
)
from fspm_optics.fixtures.smd.band_writer import (
    SmdBandEmitterAssumptions,
    build_smd_band_radiance_document,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.power_schedule import (
    build_uniform_module_schedule,
)
from fspm_optics.fixtures.smd.radiance_writer import (
    SmdEmitterAssumptions,
    build_smd_radiance_document,
)
from fspm_optics.radiance.options import radiance_options
from fspm_optics.sources.smd.profile import build_nominal_smd_source_model
from fspm_optics.sources.smd.spectral_control import (
    proposed_spectral_basis_payload,
)
from fspm_optics.transport.basis.workspace import (
    BasisWorkspaceConflictError,
    materialize_basis_workspace,
    plan_basis_workspace,
)
from fspm_optics.transport.basis.source_compatibility import (
    CurrentSourceCompatibilityError,
    require_current_proposed_source_manifest,
)
from fspm_optics.transport.five_band import build_five_band_source_plans
from fspm_optics.transport.proposed_uniform import (
    materialize_uniform_proposed_stage_a,
    plan_uniform_proposed_stage_a,
)


def _request_payload(system: str = "proposed") -> dict[str, object]:
    return {
        "system": system,
        "target_ppfd": 500.0,
        "room_length_ft": 10.0,
        "room_width_ft": 10.0,
        "mounting_height_in": 18.0,
        "quality": "direct",
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 20.0,
    }


def test_request_defaults_native_and_cob_is_proposed_only() -> None:
    native = ProposedRunRequest.from_payload(_request_payload())
    cob = ProposedRunRequest.from_payload(
        _request_payload()
        | {
            "proposed_source_mode": COB_SOURCE_MODE,
            "lighting_target_mode": "target_capped",
        }
    )
    assert native.source_mode is ProposedSourceMode.NATIVE_SMD
    assert native.to_dict()["proposed_source_mode"] == NATIVE_SOURCE_MODE
    assert cob.source_mode is ProposedSourceMode.COB_SOURCE_SHAPE_SURROGATE
    assert cob.to_dict()["proposed_source_mode"] == COB_SOURCE_MODE
    assert cob.to_dict()["lighting_target_mode"] == "target_capped"
    with pytest.raises(RequestValidationError, match="unsupported request fields"):
        ConventionalRunRequest.from_payload(
            _request_payload("conventional")
            | {"proposed_source_mode": COB_SOURCE_MODE}
        )
    with pytest.raises(RequestValidationError, match="fixes the native Proposed SPD"):
        ProposedRunRequest.from_payload(
            _request_payload()
            | {
                "analysis_scope": "baseline_plus_multispectral_fspm",
                "proposed_source_mode": COB_SOURCE_MODE,
                "spectral_basis": "conventional_led_control",
            }
        )


def test_ies_authentication_normalization_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    law = load_cob_angular_law()
    assert law.ies_sha256 == COB_IES_SHA256
    assert law.vertical_angles_deg == tuple(float(value) for value in range(91))
    assert law.horizontal_angles_deg == tuple(float(value) for value in range(91))
    assert law.unit_flux_integral == pytest.approx(1.0, abs=2.0e-14)
    assert hashlib.sha256(law.dat_text.encode()).hexdigest() == law.dat_sha256
    assert law.dat_text.startswith("2\n0 90 91\n0 90 91\n")

    with pytest.raises(CobSourceError, match="missing"):
        load_cob_angular_law(data_root=tmp_path / "missing")
    changed = tmp_path / "changed"
    changed.mkdir()
    resource = changed / cob_source.COB_IES_RESOURCE_NAME
    resource.write_bytes(b"IESNA:LM-63-2002\nTILT=NONE\nmalformed\n")
    with pytest.raises(CobSourceError, match="hash mismatch"):
        load_cob_angular_law(data_root=changed)
    monkeypatch.setattr(
        cob_source,
        "COB_IES_SHA256",
        hashlib.sha256(resource.read_bytes()).hexdigest(),
    )
    with pytest.raises(CobSourceError, match="malformed"):
        load_cob_angular_law(data_root=changed)


def test_forward_characterization_identity_is_complete() -> None:
    encoded = json.dumps(
        cob_source.cob_characterization_identity_payload(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == (
        cob_source.COB_FORWARD_CHARACTERIZATION_IDENTITY_SHA256
    )


def test_characterization_requires_deterministic_single_process(
    tmp_path: Path,
) -> None:
    output = tmp_path / "parallel-characterization"
    with pytest.raises(CobCharacterizationError, match="nthreads=1"):
        execute_characterization(output, nthreads=2)
    assert not output.exists()


def test_retired_cob_backward_characterization_cannot_publish(
    tmp_path: Path,
) -> None:
    output = tmp_path / "retired-characterization"
    with pytest.raises(
        CobCharacterizationError,
        match="cannot execute or publish production authority",
    ):
        execute_characterization(output)
    assert not output.exists()


def test_cob_writer_reconstructs_centered_22mm_les_inside_native_stack() -> None:
    layout = generate_proposed_led_layout(10, 10)
    schedule = build_uniform_module_schedule(layout, 1.0)
    default_native = build_smd_radiance_document(layout, schedule)
    explicit_native = build_smd_radiance_document(
        layout,
        schedule,
        assumptions=SmdEmitterAssumptions(source_mode=NATIVE_SOURCE_MODE),
    )
    cob = build_smd_radiance_document(
        layout,
        schedule,
        assumptions=SmdEmitterAssumptions(source_mode=COB_SOURCE_MODE),
    )
    assert default_native.radiance_text == explicit_native.radiance_text
    assert " ring cob_m0000_internal_les\n" in cob.radiance_text
    assert "0 0 -1 0 0.011000000" in cob.radiance_text
    assert "smd_m0000_internal_emitter" not in cob.radiance_text
    assert f"5 flatcorr {COB_ANGULAR_DAT_FILENAME} source.cal src_phi4 src_theta" in (
        cob.radiance_text
    )
    assert "proposed_m0000_pmma_bottom" in cob.radiance_text
    assert "proposed_m0000_ptfe_north" in cob.radiance_text
    assert cob.metadata.emitter_area_per_module_m2 == pytest.approx(COB_LES_AREA_M2)
    assert cob.metadata.emitter_diameter_m == 0.022
    assert cob.metadata.accepted_fixture_transmission != (
        default_native.metadata.accepted_fixture_transmission
    )
    assert cob.source_angular_data_text == load_cob_angular_law().dat_text
    for module in layout.modules:
        native_match = re.search(
            rf"polygon smd_m{module.module_index:04d}_internal_emitter\n"
            r"0\n0\n12\n((?:  [^\n]+\n){4})",
            default_native.radiance_text,
        )
        cob_match = re.search(
            rf"ring cob_m{module.module_index:04d}_internal_les\n"
            r"0\n0\n8 ([^\n]+)",
            cob.radiance_text,
        )
        assert native_match is not None and cob_match is not None
        native_vertices = tuple(
            tuple(float(value) for value in line.split())
            for line in native_match.group(1).splitlines()
        )
        cob_values = tuple(float(value) for value in cob_match.group(1).split())
        assert (
            math.fsum(vertex[0] for vertex in native_vertices) / 4.0,
            math.fsum(vertex[1] for vertex in native_vertices) / 4.0,
        ) == pytest.approx((module.x_m, module.y_m), abs=1.0e-12)
        assert cob_values[:2] == pytest.approx(
            (module.x_m, module.y_m),
            abs=1.0e-12,
        )


def test_uniform_and_basis_stage_a_use_complete_cob_source_and_incompatible_cache(
    tmp_path: Path,
) -> None:
    layout = generate_proposed_led_layout(10, 10)
    assumptions = SmdEmitterAssumptions(source_mode=COB_SOURCE_MODE)
    uniform = plan_uniform_proposed_stage_a(
        layout=layout,
        output_directory=tmp_path / "uniform",
        radiance_options=radiance_options("direct"),
        use_ambient_cache=False,
        emitter_assumptions=assumptions,
    )
    materialize_uniform_proposed_stage_a(uniform)
    assert uniform.paths.source_angular_data is not None
    assert uniform.paths.source_angular_data.read_text() == load_cob_angular_law().dat_text
    assert uniform.emitter_document.metadata.source_mode == COB_SOURCE_MODE
    assert uniform.emitter_document.metadata.controlled_spd_identity_sha256 == (
        native_proposed_spd_identity_sha256()
    )
    assert str(uniform.fixture_occlusion.instance_source_path) in (
        uniform.oconv_command.argv
    )
    uniform_manifest = json.loads(uniform.paths.manifest.read_text())
    assert uniform_manifest["source_metadata"][
        "completed_aperture_characterization_identity_sha256"
    ] == cob_source.COB_FORWARD_CHARACTERIZATION_IDENTITY_SHA256
    assert uniform_manifest["source_metadata"][
        "controlled_spd_identity_sha256"
    ] == native_proposed_spd_identity_sha256()

    basis = plan_basis_workspace(
        layout=layout,
        output_directory=tmp_path / "basis",
        radiance_options=radiance_options("direct"),
        use_ambient_cache=False,
        emitter_assumptions=assumptions,
    )
    materialized = materialize_basis_workspace(basis)
    assert materialized.source_angular_data_path is not None
    assert basis.manifest.proposed_source_mode == COB_SOURCE_MODE
    assert basis.manifest.proposed_source_identity_sha256
    basis_source = basis.manifest.to_dict()["proposed_source"]
    assert basis_source["authenticated_ies_sha256"] == COB_IES_SHA256
    assert basis_source["emitter_geometry"]["diameter_m"] == 0.022
    assert basis_source[
        "completed_aperture_characterization_identity_sha256"
    ] == cob_source.COB_FORWARD_CHARACTERIZATION_IDENTITY_SHA256
    assert basis_source["controlled_spd_identity_sha256"] == (
        native_proposed_spd_identity_sha256()
    )
    require_current_proposed_source_manifest(basis.manifest)
    historical_manifest = replace(
        basis.manifest,
        completed_aperture_characterization_identity_sha256=(
            cob_source.COB_CHARACTERIZATION_IDENTITY_SHA256
        ),
    )
    with pytest.raises(
        CurrentSourceCompatibilityError,
        match="historically authentic but incompatible",
    ):
        require_current_proposed_source_manifest(historical_manifest)
    for column in basis.generation_plan.columns:
        assert column.emitter_document.metadata.source_mode == COB_SOURCE_MODE
        assert column.emitter_document.metadata.controlled_spd_identity_sha256 == (
            native_proposed_spd_identity_sha256()
        )
        assert " ring cob_m" in column.emitter_document.radiance_text
        assert str(basis.generation_plan.fixture_occlusion.instance_source_path) in (
            column.oconv_command.argv
        )

    cache_root = tmp_path / "cache-incompatibility"
    native = plan_basis_workspace(layout=layout, output_directory=cache_root)
    materialize_basis_workspace(native)
    incompatible = plan_basis_workspace(
        layout=layout,
        output_directory=cache_root,
        emitter_assumptions=assumptions,
    )
    with pytest.raises(BasisWorkspaceConflictError, match="incompatible"):
        materialize_basis_workspace(incompatible)


def test_stage_b_reuses_exact_cob_and_spd_authorities(
    tmp_path: Path,
) -> None:
    layout = generate_proposed_led_layout(10, 10)
    schedule = build_uniform_module_schedule(layout, 100.0)
    source = resolve_proposed_source_authority(COB_SOURCE_MODE)
    source_record = source.to_dict()
    controlled_spd = proposed_spectral_basis_payload(
        build_nominal_smd_source_model()
    )
    source_record["controlled_spd"] = controlled_spd | {
        "identity_sha256": native_proposed_spd_identity_sha256(),
        "held_fixed_for_source_shape_experiment": True,
        "stage_a_scalar_transport_is_spd_independent": True,
    }
    state = PhysicalSourceState.create(
        system_id="proposed",
        layout_identity={"layout": "cob-stage-b"},
        full_output_schedule={
            "watts_by_module": list(schedule.watts_by_module)
        },
        operating_point={"proposed_source": source_record},
        source_operation={
            "proposed_source": source_record,
            "proposed_layout_mode": layout.proposed_layout_mode.value,
            "fixture_policy_id": layout.fixture_policy_id,
            "mechanical_envelope": {
                "id": layout.mechanical_envelope_id,
                "width_x_m": layout.module_footprint_x_m,
                "height_y_m": layout.module_footprint_y_m,
            },
            "fixture_asset_set_id": layout.fixture_asset_set_id,
        },
    )
    fixture_occlusion = plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=proposed_layout_transport_payload(layout),
        output_directory=tmp_path / "fixture-occlusion",
    )
    materialize_fixture_occlusion(fixture_occlusion)
    common = {
        "layout": layout,
        "full_output_schedule": schedule,
        "dimming_factor": 0.4,
        "room_text": "void plastic wall\n0\n0\n5 0 0 0 0 0\n",
        "quality_profile": "direct",
        "threads": 1,
        "oconv_bin": "/fake/oconv",
        "rtrace_bin": "/fake/rtrace",
        "control_mode": ProposedControlMode.UNIFORM_MODULE_DIMMING,
        "source_mode": COB_SOURCE_MODE,
        "fixture_occlusion": fixture_occlusion,
    }
    adapter = build_proposed_juvenile_source_adapter(
        source_state=state,
        **common,
    )
    assert adapter.spectral_basis["identity_sha256"] == (
        native_proposed_spd_identity_sha256()
    )
    assert adapter.source_auxiliary_files == {
        COB_ANGULAR_DAT_FILENAME: load_cob_angular_law().dat_text
    }
    assert all(
        band.source_provenance["proposed_source"]["source_mode"]
        == COB_SOURCE_MODE
        for band in adapter.bands
    )

    changed_record = dict(source_record)
    changed_record["controlled_spd"] = dict(
        source_record["controlled_spd"]
    ) | {"identity_sha256": "0" * 64}
    changed_state = PhysicalSourceState.create(
        system_id="proposed",
        layout_identity={"layout": "cob-stage-b"},
        full_output_schedule={
            "watts_by_module": list(schedule.watts_by_module)
        },
        operating_point={"proposed_source": changed_record},
        source_operation={
            "proposed_source": changed_record,
            "proposed_layout_mode": layout.proposed_layout_mode.value,
            "fixture_policy_id": layout.fixture_policy_id,
            "mechanical_envelope": {
                "id": layout.mechanical_envelope_id,
                "width_x_m": layout.module_footprint_x_m,
                "height_y_m": layout.module_footprint_y_m,
            },
            "fixture_asset_set_id": layout.fixture_asset_set_id,
        },
    )
    with pytest.raises(JuvenileMultispectralError, match="Stage A source state"):
        build_proposed_juvenile_source_adapter(
            source_state=changed_state,
            **common,
        )


def test_cob_ppf_closure_and_five_band_source_mode() -> None:
    source = resolve_proposed_source_authority(COB_SOURCE_MODE)
    assert source.internal_source_ppe_umol_per_j * (
        source.completed_aperture_transmission
    ) == pytest.approx(2.6, rel=1.0e-12)
    layout = generate_proposed_led_layout(10, 10)
    schedule = build_uniform_module_schedule(layout, 10.0)
    full = derive_uniform_full_output_schedule(
        [100.0, 200.0],
        layout,
        reference_watts_per_module=10.0,
        source_authority=source,
    )
    assert full.accepted_fixture_transmission == source.completed_aperture_transmission
    assert full.full_output_modeled_completed_aperture_par_ppf_umol_s == (
        pytest.approx(schedule.total_watts * 2.6)
    )
    plans = build_five_band_source_plans(
        build_nominal_smd_source_model(),
        layout,
        schedule,
        source_authority=source,
    )
    assert all(plan.source_mode == COB_SOURCE_MODE for plan in plans)
    assert all(plan.angular_model_id == source.angular_model_id for plan in plans)
    document = build_smd_band_radiance_document(
        layout,
        schedule,
        band_id=plans[0].band_id,
        internal_band_photon_yield_umol_per_j=(
            plans[0].internal_band_photon_yield_umol_per_j
        ),
        assumptions=SmdBandEmitterAssumptions(source_mode=COB_SOURCE_MODE),
    )
    assert document.metadata.source_mode == COB_SOURCE_MODE
    assert document.source_angular_data_text == load_cob_angular_law().dat_text
    assert math.isclose(
        document.metadata.modeled_completed_aperture_band_yield_umol_per_j,
        plans[0].internal_band_photon_yield_umol_per_j
        * source.completed_aperture_transmission,
        rel_tol=1.0e-12,
    )
