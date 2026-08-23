from __future__ import annotations

import copy
import hashlib
import inspect
import json
import math
from pathlib import Path
import struct

import pytest

from fspm_optics.application.surface_flux_display import (
    BACK_ANCHORS,
    BACK_PALETTE,
    CALIBRATION_PROFILES,
    FRONT_ANCHORS,
    FRONT_LOCAL_PATCH_ANCHORS,
    FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_NAME,
    FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_SHA256,
    FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_ID,
    FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_VERSION,
    FRONT_LOCAL_PATCH_COEFFICIENT_ORDER,
    FRONT_LOCAL_PATCH_COMBINED_BYTE_LENGTH,
    FRONT_LOCAL_PATCH_COMBINED_ORDER,
    FRONT_LOCAL_PATCH_COMBINED_SHA256,
    FRONT_LOCAL_PATCH_PROFILE_HASHES,
    FRONT_PALETTE,
    METRIC_ORDER,
    PALETTE,
    PATCHES_PER_PLANT,
    QUALITY_PROFILE,
    STANDARD_PROFILE,
    SURFACE_FLUX_DISPLAY_SCHEMA_ID,
    SURFACE_FLUX_DISPLAY_SCHEMA_VERSION,
    SURFACE_FLUX_METADATA_FILENAME,
    SURFACE_FLUX_VALUES_FILENAME,
    SurfaceFluxDisplayError,
    _convert_patch_values,
    _validate_scientific_contract,
    build_front_legend_contract,
    build_legend_contract,
    build_surface_flux_display_publication,
    calibration_provenance,
    palette_contract,
    quality_family_mapping_contract,
    select_quality_family,
    select_surface_flux_reference,
)
from fspm_optics.application.fspm_science import PATCH_FLOAT_FIELDS, PATCH_STRUCT
from fspm_optics.application.proposed import (
    _validate_surface_flux_display_artifacts,
)
from fspm_optics.plants import (
    REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID,
    generate_rex_juvenile_preheading_plant,
    legacy_rex_juvenile_preheading_config,
    plan_natural_fit_layout_from_feet,
)
from fspm_optics.viewer.artifacts import PROFILE_ID
from fspm_optics.viewer.models import (
    BinaryDisplayArtifact,
    SurfaceFluxViewerArtifacts,
)


REPOSITORY = Path(__file__).parents[1]


def test_packaged_front_calibration_schema_coefficients_and_hashes_are_exact() -> None:
    path = (
        REPOSITORY
        / "src"
        / "fspm_optics"
        / "resources"
        / "calibration"
        / FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_NAME
    )
    raw = path.read_bytes()
    payload = json.loads(raw)

    assert FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_SHA256 == (
        "6f22972a6ffe41db494f464abafdcad85bc4434bf7c6e326ebabfef6c1008bd3"
    )
    assert hashlib.sha256(raw).hexdigest() == (
        FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_SHA256
    )
    assert payload["schema_id"] == FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_ID
    assert payload["schema_version"] == FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_VERSION
    assert payload["scope"] == (
        "front receiver surface only; backside behavior intentionally deferred "
        "and unchanged"
    )
    assert payload["source_authority"]["original_d1_experiment_status"] == (
        "complete_fail"
    )
    assert payload["source_authority"]["original_d1_acceptance_pass"] is False
    assert payload["source_authority"][
        "original_d1_outcome_must_not_be_rewritten"
    ] is True
    assert payload["topology_contract"] == {
        "coefficient_is_average_over_all_64_plants": True,
        "coefficient_position_dependence": False,
        "front_receiver_equation": "384 * plant_index + 2 * local_patch_index",
        "global_patch_equation": "192 * plant_index + local_patch_index",
        "global_patches": 64 * PATCHES_PER_PLANT,
        "local_patches_per_plant": PATCHES_PER_PLANT,
        "plants": 64,
    }
    assert payload["display_model"] == {
        "equation": (
            "u[p,k,m] = q[p,k,m] / (gamma[quality_family,m,k] * R)"
        ),
        "per_run_fitting": False,
        "per_system_fitting": False,
        "plant_position_normalization": False,
        "raw_q_modified": False,
        "system_name_branching": False,
        "u_equals_one_meaning": (
            "Expected-equivalent exposure for the same canonical local patch "
            "under the fixed neutral upper-hemisphere calibration; not a leaf "
            "target and not raw-q uniformity."
        ),
        "variable": "u",
    }
    assert payload["held_out_acceptance_evidence"][
        "used_to_fit_profiles_or_palette"
    ] is False

    expected_hashes = {
        ("standard", "incident_par"): (
            "a0216419e067bedaa20d54f3dbbb4c9ea375aa0ed5198e3e286d036144a925ec"
        ),
        ("standard", "absorbed_par"): (
            "f4b79e943efd58e07b847b1c3c51ecc8490c4af483c131918e6b00eb46c3a1fb"
        ),
        ("quality", "incident_par"): (
            "725d4b6ae9570fbafc4e01c745e120123325949d9c349230730149904ce34c41"
        ),
        ("quality", "absorbed_par"): (
            "98b5c1b07067f407e56c8ddd39015211822625fc8f522be480bcbfd47e86939f"
        ),
    }
    assert dict(FRONT_LOCAL_PATCH_PROFILE_HASHES) == expected_hashes
    assert FRONT_LOCAL_PATCH_COEFFICIENT_ORDER == (
        "canonical local_patch_index 0..191"
    )
    assert FRONT_LOCAL_PATCH_COMBINED_ORDER == (
        "standard incident_par; standard absorbed_par; quality incident_par; "
        "quality absorbed_par"
    )
    assert FRONT_LOCAL_PATCH_COMBINED_BYTE_LENGTH == 6144
    assert FRONT_LOCAL_PATCH_COMBINED_SHA256 == (
        "b1e3cec31fc4b4e81ce03fd88291edaafd8bb6a717a4d228e57c60d61bd21a07"
    )

    combined = bytearray()
    expected_order = (
        ("standard", "incident_par", STANDARD_PROFILE),
        ("standard", "absorbed_par", STANDARD_PROFILE),
        ("quality", "incident_par", QUALITY_PROFILE),
        ("quality", "absorbed_par", QUALITY_PROFILE),
    )
    assert payload["profiles"]["standard"]["evaluated_quality_mapping"] == [
        "direct",
        "standard",
    ]
    assert payload["profiles"]["standard"]["proxy_mapping"] == {
        "direct": True,
        "standard": False,
    }
    assert payload["profiles"]["quality"]["evaluated_quality_mapping"] == [
        "quality",
        "rigorous",
    ]
    assert payload["profiles"]["quality"]["proxy_mapping"] == {
        "quality": False,
        "rigorous": True,
    }
    for family, metric, profile in expected_order:
        item = payload["profiles"][family]["metrics"][metric]
        coefficients = tuple(item["coefficients"])
        encoded = struct.pack(f"<{PATCHES_PER_PLANT}d", *coefficients)
        expected_hash = FRONT_LOCAL_PATCH_PROFILE_HASHES[(family, metric)]

        assert item["coefficient_count"] == PATCHES_PER_PLANT
        assert item["coefficient_order"] == FRONT_LOCAL_PATCH_COEFFICIENT_ORDER
        assert item["coefficient_symbol"] == "gamma_k"
        assert item["coefficient_units"] == "dimensionless q/R"
        assert len(coefficients) == PATCHES_PER_PLANT
        assert all(math.isfinite(value) and value > 0.0 for value in coefficients)
        assert item["float64_little_endian_sha256"] == expected_hash
        assert hashlib.sha256(encoded).hexdigest() == expected_hash
        assert coefficients == profile.front_metric(metric).coefficients
        assert item["minimum"] == min(coefficients)
        assert item["maximum"] == max(coefficients)
        combined.extend(encoded)

    derivation = payload["derivation_contract"]
    assert derivation["profile_arrays_binary_order"] == (
        FRONT_LOCAL_PATCH_COMBINED_ORDER
    )
    assert derivation["combined_float64_little_endian_byte_length"] == (
        FRONT_LOCAL_PATCH_COMBINED_BYTE_LENGTH
    )
    assert derivation["combined_float64_little_endian_sha256"] == (
        FRONT_LOCAL_PATCH_COMBINED_SHA256
    )
    assert len(combined) == FRONT_LOCAL_PATCH_COMBINED_BYTE_LENGTH
    assert hashlib.sha256(combined).hexdigest() == FRONT_LOCAL_PATCH_COMBINED_SHA256

    with pytest.raises(TypeError):
        STANDARD_PROFILE.front_local_patch_metrics["incident_par"] = (  # type: ignore[index]
            QUALITY_PROFILE.front_metric("incident_par")
        )
    coefficients = STANDARD_PROFILE.front_metric("incident_par").coefficients
    with pytest.raises(TypeError):
        coefficients[0] = 1.0  # type: ignore[index]


def test_scalar_beta_provenance_and_d1_failure_status_are_retained() -> None:
    assert dict(STANDARD_PROFILE.coefficients) == {
        "front_incident": 0.4510905803216862,
        "back_incident": 0.058044697174435056,
        "front_absorbed": 0.3657821501976058,
        "back_absorbed": 0.04670307023408276,
    }
    assert dict(QUALITY_PROFILE.coefficients) == {
        "front_incident": 0.448166736538762,
        "back_incident": 0.06098285511211222,
        "front_absorbed": 0.3632938753333456,
        "back_absorbed": 0.04909664459054215,
    }
    assert QUALITY_PROFILE.achieved_reference_ppfd_umol_m2_s == 500.0112
    assert STANDARD_PROFILE.profile_id == (
        "neutral-upper-hemisphere-standard-front-local-patch-v1"
    )
    assert QUALITY_PROFILE.profile_id == (
        "neutral-upper-hemisphere-quality-front-local-patch-v1"
    )
    assert set(CALIBRATION_PROFILES) == {
        STANDARD_PROFILE.profile_id,
        QUALITY_PROFILE.profile_id,
    }
    with pytest.raises(TypeError):
        STANDARD_PROFILE.coefficients["front_incident"] = 1.0  # type: ignore[index]

    provenance = calibration_provenance()
    assert provenance == {
        "report_schema_id": "fspm-optics.surface-flux-calibration-report",
        "report_schema_version": 1,
        "original_d1_experiment_status": "complete_fail",
        "original_d1_acceptance_pass": False,
        "original_d1_outcome_must_not_be_rewritten": True,
        "original_cross_quality_convergence_rewritten": False,
        "repository_revision": "3a35cc3a8d143e624a468049344ed15676d791e8",
        "configuration_sha256": "d388d6293a94102232f5211dd8bb0b942b401ea30794a63c9f36d8920ae4d9b0",
        "source_model_id": "neutral-uniform-upper-hemisphere-v1",
        "source_definition_sha256": "cfe8b701186ef8c2cd49ae8d8e9783968353cbc9383e47e3674b8909bb3e6530",
        "calibration_scene_sha256": "a3bf55f2e2755ee74a28c6285d8b2408ead18ca3c416487988b238debe784d32",
        "topology_sha256": "b199a43c98aa899657aae4fbda0b04fee72290d05636aa39d51240598c95fa09",
        "receivers_sha256": "e9631120fea1672e2047e0db7fdb8297ca9e4a9ba89cd27b947dd89af99038fc",
        "material_plan_sha256": "c5d829ecdbde2b06602468e187fd75c6314f55e671b2d5529289d40adb4ccff6",
        "source_scope": "open-boundary uniform upper-hemisphere neutral field",
        "front_local_patch_calibration": {
            "schema_id": FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_ID,
            "schema_version": FRONT_LOCAL_PATCH_CALIBRATION_SCHEMA_VERSION,
            "resource_sha256": FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_SHA256,
            "combined_float64_little_endian_sha256": (
                FRONT_LOCAL_PATCH_COMBINED_SHA256
            ),
        },
    }
    for profile in (STANDARD_PROFILE, QUALITY_PROFILE):
        profile_payload = profile.to_dict()
        scalar = profile_payload["scalar_beta_provenance"]
        assert scalar["front_used_for_coloring"] is False
        assert scalar["back_used_for_coloring"] is True
        for metric in METRIC_ORDER:
            embedded = profile_payload["front_local_patch_metrics"][metric]
            authority = profile.front_metric(metric)
            assert embedded["coefficient_count"] == PATCHES_PER_PLANT
            assert embedded["coefficient_order"] == (
                FRONT_LOCAL_PATCH_COEFFICIENT_ORDER
            )
            assert tuple(embedded["coefficients"]) == authority.coefficients
            assert embedded["float64_little_endian_sha256"] == (
                authority.float64_little_endian_sha256
            )


@pytest.mark.parametrize(
    ("quality", "profile", "proxy", "rationale_fragment"),
    (
        ("direct", STANDARD_PROFILE, True, "Standard development family"),
        ("standard", STANDARD_PROFILE, False, None),
        ("quality", QUALITY_PROFILE, False, None),
        ("rigorous", QUALITY_PROFILE, True, "Quality high-fidelity family"),
    ),
)
def test_quality_family_policy_is_exact_and_system_neutral(
    quality: str, profile, proxy: bool, rationale_fragment: str | None
) -> None:
    selected = select_quality_family(quality)
    assert selected is not None
    assert selected.profile is profile
    assert selected.proxy is proxy
    assert (selected.proxy_rationale is not None) is proxy
    if rationale_fragment is not None:
        assert rationale_fragment in selected.proxy_rationale
    expected_contract = selected.to_dict()
    del expected_contract["evaluated_quality"]
    assert quality_family_mapping_contract()[quality] == expected_contract
    assert "system" not in inspect.signature(select_quality_family).parameters
    assert "system" not in inspect.signature(
        quality_family_mapping_contract
    ).parameters
    assert quality_family_mapping_contract() == {
        "direct": {
            "selected_profile_id": STANDARD_PROFILE.profile_id,
            "calibration_source_quality": "standard",
            "family_id": "neutral-upper-hemisphere-standard-family-v1",
            "proxy": True,
            "proxy_rationale": (
                "Direct uses the Standard development family as an explicitly "
                "declared proxy."
            ),
        },
        "standard": {
            "selected_profile_id": STANDARD_PROFILE.profile_id,
            "calibration_source_quality": "standard",
            "family_id": "neutral-upper-hemisphere-standard-family-v1",
            "proxy": False,
            "proxy_rationale": None,
        },
        "quality": {
            "selected_profile_id": QUALITY_PROFILE.profile_id,
            "calibration_source_quality": "quality",
            "family_id": "neutral-upper-hemisphere-quality-family-v1",
            "proxy": False,
            "proxy_rationale": None,
        },
        "rigorous": {
            "selected_profile_id": QUALITY_PROFILE.profile_id,
            "calibration_source_quality": "quality",
            "family_id": "neutral-upper-hemisphere-quality-family-v1",
            "proxy": True,
            "proxy_rationale": (
                "Rigorous uses the Quality high-fidelity family based on prior "
                "Quality-versus-Rigorous convergence evidence."
            ),
        },
    }


def test_unknown_or_missing_quality_has_no_silent_profile_substitution() -> None:
    assert select_quality_family(None) is None
    assert select_quality_family("") is None
    assert select_quality_family("instant") is None
    assert select_quality_family("STANDARD") is None


def test_unknown_quality_fails_closed_to_neutral_before_artifact_access(
    tmp_path: Path,
) -> None:
    reference = select_surface_flux_reference(
        operating_policy="target-controlled",
        requested_stage_a_target_ppfd_umol_m2_s=500.0,
    )
    with pytest.raises(SurfaceFluxDisplayError, match="keep neutral"):
        build_surface_flux_display_publication(
            tmp_path,
            run_id="a" * 32,
            quality="instant",
            natural_fit=None,  # type: ignore[arg-type]
            reference=reference,
            multispectral=None,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("transport_version", "aggregation_version", "include_far_red"),
    [(2, 1, True), (3, 2, False), (3, 2, True)],
)
def test_d2_scientific_contract_accepts_historical_and_optional_band_shapes_before_rejecting_optimized_sampling(
    transport_version: int,
    aggregation_version: int,
    include_far_red: bool,
) -> None:
    natural_fit = plan_natural_fit_layout_from_feet(10.0, 10.0)
    counts = {
        "plants": natural_fit.total_count,
        "leaves": natural_fit.total_count * 12,
        "faces": natural_fit.total_count * 1920,
        "patches": natural_fit.total_count * 192,
        "receivers": natural_fit.total_count * 384,
    }
    par_band_order = ["blue", "green", "orange", "red"]
    band_order = (
        [*par_band_order, "far_red"] if include_far_red else par_band_order
    )
    transport = {
        "schema_id": "fspm-optics.juvenile-multi-plant-five-band-receivers",
        "schema_version": transport_version,
        "run_id": "a" * 32,
        "source_state_id": "source-state",
        "source_planning": {"quality_profile": "standard"},
        "band_order": band_order,
        "executed_band_order": band_order,
        "par_band_order": par_band_order,
        "include_far_red": include_far_red,
        "far_red_executed": include_far_red,
        "far_red_preserved_separately": True,
        "plant": {
            "profile_id": PROFILE_ID,
            "sampling_profile_id": (
                REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
            ),
            "layout_plan_hash": natural_fit.plan_hash,
            "global_counts": counts,
            "plant_count": natural_fit.total_count,
            "canonical_counts_per_plant": {
                "leaves": 12,
                "faces": 1920,
                "patches": 192,
                "receivers": 384,
            },
            "ordering": (
                "Y-major/X-minor plants; plant-major receivers; front then back"
            ),
            "geometry_included_in_scientific_transport": True,
        },
    }
    aggregation = {
        "schema_id": "fspm-optics.fspm-surface-light-aggregation",
        "schema_version": aggregation_version,
        "run_id": "a" * 32,
        "source_state_id": "source-state",
        "transport_metadata_sha256": "1" * 64,
        "band_order": band_order,
        "executed_band_order": band_order,
        "par_band_order": par_band_order,
        "include_far_red": include_far_red,
        "far_red_executed": include_far_red,
        "far_red_preserved_separately": True,
        "counts": counts,
    }
    compact = {
        "canonical_topology": {
            "sampling_profile_id": (
                REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
            )
        },
        "scene": {
            "sampling_profile_id": (
                REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
            )
        },
    }

    with pytest.raises(
        SurfaceFluxDisplayError,
        match="sampling profile is not covered",
    ):
        _validate_scientific_contract(
            run_id="a" * 32,
            quality="standard",
            natural_fit=natural_fit,
            expected_counts=counts,
            transport=transport,
            aggregation=aggregation,
            compact=compact,
            patch_record={},
        )


def test_surface_flux_viewer_artifacts_separate_v1_and_v2_schema_semantics() -> None:
    def artifact(filename: str, data: bytes) -> BinaryDisplayArtifact:
        return BinaryDisplayArtifact(filename, data, hashlib.sha256(data).hexdigest())

    def metadata(version: int) -> bytes:
        return json.dumps(
            {
                "schema_id": SURFACE_FLUX_DISPLAY_SCHEMA_ID,
                "schema_version": version,
            },
            sort_keys=True,
        ).encode("utf-8")

    values = artifact(SURFACE_FLUX_VALUES_FILENAME, b"\0" * 16)
    for version in (1, 2):
        route = f"surface-flux/metadata.v{version}.json"
        metadata_artifact = artifact(route, metadata(version))
        result = SurfaceFluxViewerArtifacts(metadata_artifact, values)
        assert result.schema_version == version
        assert result.files == (metadata_artifact, values)

    assert SURFACE_FLUX_DISPLAY_SCHEMA_VERSION == 2
    assert SURFACE_FLUX_METADATA_FILENAME == "surface-flux/metadata.v2.json"
    assert SURFACE_FLUX_VALUES_FILENAME == "surface-flux/patch-values.v1.f32le.bin"
    viewer_script = (
        Path(__file__).parents[1]
        / "src/fspm_optics/resources/viewer/surface-flux.js"
    ).read_text(encoding="utf-8")
    assert "const PHASE_C_PAR_PATCH_STRIDE_BYTES = 136" in viewer_script
    assert "boundary.aggregation_schema_version === 2" in viewer_script
    assert "boundary.transport_schema_version === 3" in viewer_script
    assert "boundary.executed_band_order" in viewer_script
    assert "boundary.far_red_executed !== fiveBand" in viewer_script

    with pytest.raises(ValueError, match="filenames"):
        SurfaceFluxViewerArtifacts(
            artifact("surface-flux/wrong.json", metadata(2)), values
        )
    with pytest.raises(ValueError, match="hash"):
        SurfaceFluxViewerArtifacts(
            BinaryDisplayArtifact(
                "surface-flux/metadata.v2.json", metadata(2), "0" * 64
            ),
            values,
        )
    for route, version in (
        ("surface-flux/metadata.v1.json", 2),
        ("surface-flux/metadata.v2.json", 1),
    ):
        with pytest.raises(ValueError, match="disagree"):
            SurfaceFluxViewerArtifacts(artifact(route, metadata(version)), values)


def test_target_controlled_and_fixed_output_reference_policies_are_generic() -> None:
    target = select_surface_flux_reference(
        operating_policy="target-controlled",
        requested_stage_a_target_ppfd_umol_m2_s=700.0,
        achieved_stage_a_mean_ppfd_umol_m2_s=699.8,
    )
    fixed = select_surface_flux_reference(
        operating_policy="fixed-output",
        achieved_stage_a_mean_ppfd_umol_m2_s=523.25,
    )

    assert target.value_umol_m2_s == 700.0
    assert target.value_kind == "requested"
    assert target.source_provenance["field"] == "target_ppfd_umol_m2_s"
    assert fixed.value_umol_m2_s == 523.25
    assert fixed.value_kind == "achieved"
    assert fixed.source_provenance["plant_free_reference_plane"] is True
    assert "system" not in inspect.signature(
        select_surface_flux_reference
    ).parameters

    for kwargs in (
        {"operating_policy": ""},
        {"operating_policy": "target-controlled"},
        {
            "operating_policy": "fixed-output",
            "requested_stage_a_target_ppfd_umol_m2_s": 500.0,
            "achieved_stage_a_mean_ppfd_umol_m2_s": 500.0,
        },
        {
            "operating_policy": "fixed-output",
            "achieved_stage_a_mean_ppfd_umol_m2_s": math.nan,
        },
    ):
        with pytest.raises(SurfaceFluxDisplayError):
            select_surface_flux_reference(**kwargs)


def test_side_specific_palettes_are_exact_immutable_and_not_shared() -> None:
    contract = palette_contract()
    assert contract["front"]["palette_id"] == (
        "surface-flux-front-neutral-local-patch-blue-to-red-8-v1"
    )
    assert contract["front"]["variable"] == "u"
    assert contract["back"]["palette_id"] == "surface-flux-blue-to-red-8-v1"
    assert contract["back"]["variable"] == "z"
    assert FRONT_ANCHORS == (0.0, 0.10, 0.31, 0.89, 1.0, 1.76, 2.07, 2.18)
    assert FRONT_LOCAL_PATCH_ANCHORS == (
        0.0,
        0.59,
        0.81,
        0.89,
        1.0,
        1.14,
        1.52,
        2.84,
    )
    assert BACK_ANCHORS == (
        0.0,
        0.025,
        0.050,
        0.14,
        0.42,
        1.0,
        2.75,
        5.33,
    )
    assert tuple(contract["front"]["anchors_u"]) == FRONT_LOCAL_PATCH_ANCHORS
    assert tuple(contract["back"]["anchors_z"]) == BACK_ANCHORS
    assert contract["front"]["green_interval_u"] == [0.89, 1.14]
    assert [item["srgb_hex"] for item in contract["front"]["colors"]] == [
        "#2563EB", "#06B6D4", "#14B8A6", "#22C55E",
        "#22C55E", "#22C55E", "#F59E0B", "#DC2626",
    ]
    assert [item["srgb_hex"] for item in contract["back"]["colors"]] == [
        "#2563EB", "#06B6D4", "#14B8A6", "#22C55E",
        "#22C55E", "#A3E635", "#F59E0B", "#DC2626",
    ]
    assert tuple(FRONT_PALETTE) != tuple(BACK_PALETTE)
    assert FRONT_PALETTE == (
        ("blue", "#2563EB"),
        ("cyan", "#06B6D4"),
        ("teal", "#14B8A6"),
        ("green_low", "#22C55E"),
        ("green_reference", "#22C55E"),
        ("green_high", "#22C55E"),
        ("orange", "#F59E0B"),
        ("red", "#DC2626"),
    )
    assert BACK_PALETTE == (
        ("blue", "#2563EB"),
        ("cyan", "#06B6D4"),
        ("teal", "#14B8A6"),
        ("green_low", "#22C55E"),
        ("green_reference", "#22C55E"),
        ("yellow_green", "#A3E635"),
        ("orange", "#F59E0B"),
        ("red", "#DC2626"),
    )
    assert PALETTE is BACK_PALETTE
    assert contract["front"]["per_run_extrema_normalization"] is False
    assert contract["back"]["per_run_extrema_normalization"] is False
    assert tuple(METRIC_ORDER) == ("incident_par", "absorbed_par")


def test_front_u_reuses_local_gamma_across_plants_and_reports_area_clipping() -> None:
    reference = select_surface_flux_reference(
        operating_policy="target-controlled",
        requested_stage_a_target_ppfd_umol_m2_s=500.0,
    )
    gamma = STANDARD_PROFILE.front_metric("incident_par").coefficients
    raw = [
        gamma[local_patch] * reference.value_umol_m2_s
        for _plant_index in range(2)
        for local_patch in range(PATCHES_PER_PLANT)
    ]
    areas = [1.0] * len(raw)
    outlier = PATCHES_PER_PLANT + 191
    raw[outlier] *= 3.0
    areas[outlier] = 7.0
    original = list(raw)
    legend = build_front_legend_contract(
        metric="incident_par",
        profile=STANDARD_PROFILE,
        reference=reference,
        raw_values=raw,
        physical_areas_m2=areas,
    )

    assert raw == original
    assert raw[0] == raw[PATCHES_PER_PLANT]
    assert raw[191] * 3.0 == pytest.approx(raw[outlier])
    assert legend["variable"] == "u"
    assert legend["physical_threshold_rule"] == (
        "q_anchor[k] = u_anchor * gamma[k] * R"
    )
    assert legend["beta_provenance"] == {
        "beta": STANDARD_PROFILE.coefficient("incident_par", "front"),
        "used_for_coloring": False,
    }
    for anchor, u_value in zip(
        legend["anchors"], FRONT_LOCAL_PATCH_ANCHORS, strict=True
    ):
        assert anchor["u"] == u_value
        assert "q_umol_m2_s" not in anchor
        assert anchor["q_threshold_rule"] == legend["physical_threshold_rule"]
        assert anchor["q_minimum_umol_m2_s"] == pytest.approx(
            u_value * min(gamma) * reference.value_umol_m2_s
        )
        assert anchor["q_maximum_umol_m2_s"] == pytest.approx(
            u_value * max(gamma) * reference.value_umol_m2_s
        )
    clipping = legend["clipping"]
    assert clipping["lower_bound_u"] == 0.0
    assert clipping["upper_bound_u"] == 2.84
    assert clipping["below_count"] == 0
    assert clipping["above_count"] == 1
    assert clipping["above_physical_area_m2"] == 7.0
    assert clipping["total_physical_one_sided_area_m2"] == 390.0
    assert clipping["above_physical_area_fraction"] == pytest.approx(7.0 / 390.0)
    assert clipping["raw_values_modified_by_clipping"] is False
    assert "same canonical local patch" in legend["u_equals_one_meaning"]
    assert "not a leaf target" in legend["u_equals_one_meaning"]
    assert "not raw-q uniformity" in legend["u_equals_one_meaning"]


def test_back_z_equation_palette_and_scalar_thresholds_are_unchanged() -> None:
    reference = select_surface_flux_reference(
        operating_policy="fixed-output",
        achieved_stage_a_mean_ppfd_umol_m2_s=500.0,
    )
    beta = STANDARD_PROFILE.coefficient("absorbed_par", "back")
    raw = [
        0.0,
        beta * reference.value_umol_m2_s,
        1.1 * BACK_ANCHORS[-1] * beta * reference.value_umol_m2_s,
    ]
    original = list(raw)
    legend = build_legend_contract(
        metric="absorbed_par",
        side="back",
        beta=beta,
        reference=reference,
        raw_values=raw,
        physical_areas_m2=[1.0, 2.0, 7.0],
    )

    assert raw == original
    assert legend["beta"] == beta
    assert [item["z"] for item in legend["anchors"]] == list(BACK_ANCHORS)
    assert [item["srgb_hex"] for item in legend["anchors"]] == [
        color for _name, color in BACK_PALETTE
    ]
    assert [item["q_umol_m2_s"] for item in legend["anchors"]] == pytest.approx(
        [z_value * beta * reference.value_umol_m2_s for z_value in BACK_ANCHORS]
    )
    assert legend["clipping"]["above_count"] == 1
    assert legend["clipping"]["above_physical_area_m2"] == 7.0
    assert legend["clipping"]["above_physical_area_fraction"] == pytest.approx(0.7)
    assert "not a leaf target" in legend["z_equals_one_meaning"]


@pytest.mark.parametrize("invalid", (-1.0, math.nan, math.inf))
def test_legends_reject_negative_and_nonfinite_scientific_values(invalid: float) -> None:
    reference = select_surface_flux_reference(
        operating_policy="fixed-output",
        achieved_stage_a_mean_ppfd_umol_m2_s=500.0,
    )
    with pytest.raises(SurfaceFluxDisplayError):
        build_legend_contract(
            metric="absorbed_par",
            side="back",
            beta=0.05,
            reference=reference,
            raw_values=[1.0, invalid],
            physical_areas_m2=[1.0, 1.0],
        )

    front_values = [1.0] * PATCHES_PER_PLANT
    front_values[191] = invalid
    with pytest.raises(SurfaceFluxDisplayError):
        build_front_legend_contract(
            metric="incident_par",
            profile=QUALITY_PROFILE,
            reference=reference,
            raw_values=front_values,
            physical_areas_m2=[1.0] * PATCHES_PER_PLANT,
        )


def test_front_legend_requires_complete_plant_major_local_patch_rows() -> None:
    reference = select_surface_flux_reference(
        operating_policy="target-controlled",
        requested_stage_a_target_ppfd_umol_m2_s=500.0,
    )
    with pytest.raises(SurfaceFluxDisplayError, match="canonical plant-major"):
        build_front_legend_contract(
            metric="incident_par",
            profile=STANDARD_PROFILE,
            reference=reference,
            raw_values=[1.0] * (PATCHES_PER_PLANT - 1),
            physical_areas_m2=[1.0] * (PATCHES_PER_PLANT - 1),
        )


def test_float64_patch_table_is_unchanged_and_float32_artifact_remains_raw_q(
    tmp_path: Path,
) -> None:
    plant = generate_rex_juvenile_preheading_plant(
        legacy_rex_juvenile_preheading_config()
    )
    leaf_by_id = {
        leaf.leaf_id: local_leaf for local_leaf, leaf in enumerate(plant.leaves)
    }
    records = bytearray()
    for local_patch, patch in enumerate(plant.patches):
        base = float(local_patch + 1)
        values = {name: 0.0 for name in PATCH_FLOAT_FIELDS}
        values["physical_one_sided_patch_area_m2"] = patch.area_m2
        values[
            "par_front_incident_photon_flux_density_umol_m2_s"
        ] = base
        values[
            "par_back_incident_photon_flux_density_umol_m2_s"
        ] = 2.0 * base
        values[
            "par_front_absorbed_photon_flux_density_umol_m2_s"
        ] = 3.0 * base
        values[
            "par_back_absorbed_photon_flux_density_umol_m2_s"
        ] = 4.0 * base
        records.extend(
            PATCH_STRUCT.pack(
                local_patch,
                0,
                leaf_by_id[patch.leaf_id],
                local_patch,
                *(values[name] for name in PATCH_FLOAT_FIELDS),
            )
        )

    path = (
        tmp_path
        / "fspm-aggregation"
        / "patch-surface-light.v1.f64le.bin"
    )
    path.parent.mkdir()
    path.write_bytes(records)
    authoritative_before = path.read_bytes()
    selection = select_quality_family("standard")
    assert selection is not None
    reference = select_surface_flux_reference(
        operating_policy="target-controlled",
        requested_stage_a_target_ppfd_umol_m2_s=500.0,
    )
    raw_float32, legends = _convert_patch_values(
        path,
        expected_counts={
            "plants": 1,
            "leaves": 12,
            "faces": 1920,
            "patches": PATCHES_PER_PLANT,
            "receivers": 384,
        },
        selection=selection,
        reference=reference,
    )

    assert path.read_bytes() == authoritative_before
    assert len(raw_float32) == PATCHES_PER_PLANT * 16
    rows = tuple(struct.iter_unpack("<4f", raw_float32))
    assert rows[0] == (1.0, 2.0, 3.0, 4.0)
    assert rows[191] == (192.0, 384.0, 576.0, 768.0)
    assert rows[0][0] != pytest.approx(
        1.0
        / (
            selection.profile.front_metric("incident_par").coefficients[0]
            * reference.value_umol_m2_s
        )
    )
    canonical_area = math.fsum(patch.area_m2 for patch in plant.patches)
    for metric in METRIC_ORDER:
        assert legends[metric]["front"]["variable"] == "u"
        assert legends[metric]["front"]["beta_provenance"][
            "used_for_coloring"
        ] is False
        assert legends[metric]["back"]["beta"] == (
            selection.profile.coefficient(metric, "back")
        )
        for side in ("front", "back"):
            assert legends[metric][side]["clipping"][
                "total_physical_one_sided_area_m2"
            ] == pytest.approx(canonical_area)


def test_promotion_revalidates_exact_float32_raw_q_channel_order(
    tmp_path: Path,
) -> None:
    manifest, metrics, metadata = _write_full_promotion_fixture(tmp_path)
    values_path = tmp_path / "plant-layout-viewer" / SURFACE_FLUX_VALUES_FILENAME
    reordered = bytearray(values_path.read_bytes())
    reordered[:16] = struct.pack("<4f", 2.0, 1.0, 3.0, 4.0)
    values_path.write_bytes(reordered)
    digest = hashlib.sha256(reordered).hexdigest()
    metadata["display_artifact"]["sha256"] = digest
    manifest["fspm_surface_flux_display"]["patch_values"]["sha256"] = digest
    manifest["viewer_publication"]["surface_flux"][
        "patch_values_sha256"
    ] = digest
    _write_promoted_metadata(
        tmp_path,
        manifest=manifest,
        metrics=metrics,
        metadata=metadata,
    )

    with pytest.raises(ValueError, match="unchanged raw-q derivative"):
        _validate_surface_flux_display_artifacts(
            tmp_path,
            manifest=manifest,
            metrics=metrics,
            expected_run_id="a" * 32,
            expected_quality="standard",
            expected_plant_count=1,
            expected_layout_plan_hash="c" * 64,
            expected_available=True,
        )


def test_full_promotion_revalidates_v2_metadata_and_raw_contract(
    tmp_path: Path,
) -> None:
    manifest, metrics, metadata = _write_full_promotion_fixture(tmp_path)
    promoted = _validate_surface_flux_display_artifacts(
        tmp_path,
        manifest=manifest,
        metrics=metrics,
        expected_run_id="a" * 32,
        expected_quality="standard",
        expected_plant_count=1,
        expected_layout_plan_hash="c" * 64,
        expected_available=True,
    )
    assert promoted is not None
    assert promoted.schema_version == 2
    assert promoted.metadata.filename == SURFACE_FLUX_METADATA_FILENAME
    assert promoted.patch_values.filename == SURFACE_FLUX_VALUES_FILENAME

    mutations = (
        lambda value: value["selected_calibration_profile"][
            "front_local_patch_metrics"
        ]["incident_par"]["coefficients"].__setitem__(
            0,
            value["selected_calibration_profile"][
                "front_local_patch_metrics"
            ]["incident_par"]["coefficients"][0]
            * 2.0,
        ),
        lambda value: value["selected_calibration_profile"][
            "front_local_patch_metrics"
        ]["absorbed_par"].__setitem__(
            "float64_little_endian_sha256", "0" * 64
        ),
        lambda value: value["topology"].__setitem__(
            "coefficient_position_dependence", True
        ),
        lambda value: value["topology"].__setitem__(
            "canonical_profile_id", "wrong-profile"
        ),
        lambda value: value["topology"].__setitem__(
            "ordering", "global-patch calibration order"
        ),
        lambda value: value["palettes"]["front"]["anchors_u"].__setitem__(
            1, 0.60
        ),
        lambda value: value["legends"]["incident_par"]["front"].__setitem__(
            "raw_minimum_q",
            value["legends"]["incident_par"]["front"]["raw_minimum_q"]
            + 1.0,
        ),
        lambda value: value["display_artifact"].__setitem__(
            "channel_semantics", "front u and back z"
        ),
        lambda value: value["display_artifact"].__setitem__(
            "display_only_float32_derivative", True
        ),
    )
    for mutate in mutations:
        changed_manifest = copy.deepcopy(manifest)
        changed_metrics = copy.deepcopy(metrics)
        changed_metadata = copy.deepcopy(metadata)
        mutate(changed_metadata)
        _write_promoted_metadata(
            tmp_path,
            manifest=changed_manifest,
            metrics=changed_metrics,
            metadata=changed_metadata,
        )
        with pytest.raises(ValueError, match="surface-flux"):
            _validate_surface_flux_display_artifacts(
                tmp_path,
                manifest=changed_manifest,
                metrics=changed_metrics,
                expected_run_id="a" * 32,
                expected_quality="standard",
                expected_plant_count=1,
                expected_layout_plan_hash="c" * 64,
                expected_available=True,
            )


def _write_full_promotion_fixture(
    root: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    aggregation_root = root / "fspm-aggregation"
    transport_root = root / "fspm-transport"
    surface_root = root / "plant-layout-viewer" / "surface-flux"
    aggregation_root.mkdir()
    transport_root.mkdir()
    surface_root.mkdir(parents=True)

    canonical = generate_rex_juvenile_preheading_plant(
        legacy_rex_juvenile_preheading_config()
    )
    leaf_by_id = {
        leaf.leaf_id: local_leaf
        for local_leaf, leaf in enumerate(canonical.leaves)
    }
    patch_bytes = bytearray()
    raw_float32 = bytearray()
    physical_areas: list[float] = []
    for local_patch, patch in enumerate(canonical.patches):
        base = float(local_patch + 1)
        values = {name: 0.0 for name in PATCH_FLOAT_FIELDS}
        values["physical_one_sided_patch_area_m2"] = patch.area_m2
        values[
            "par_front_incident_photon_flux_density_umol_m2_s"
        ] = base
        values[
            "par_back_incident_photon_flux_density_umol_m2_s"
        ] = 2.0 * base
        values[
            "par_front_absorbed_photon_flux_density_umol_m2_s"
        ] = 3.0 * base
        values[
            "par_back_absorbed_photon_flux_density_umol_m2_s"
        ] = 4.0 * base
        patch_bytes.extend(
            PATCH_STRUCT.pack(
                local_patch,
                0,
                leaf_by_id[patch.leaf_id],
                local_patch,
                *(values[name] for name in PATCH_FLOAT_FIELDS),
            )
        )
        physical_areas.append(patch.area_m2)
        raw_float32.extend(
            struct.pack("<4f", base, 2.0 * base, 3.0 * base, 4.0 * base)
        )

    patch_path = aggregation_root / "patch-surface-light.v1.f64le.bin"
    patch_path.write_bytes(patch_bytes)
    values_path = surface_root / "patch-values.v1.f32le.bin"
    values_path.write_bytes(raw_float32)
    values_record = {
        "filename": SURFACE_FLUX_VALUES_FILENAME,
        "byte_length": len(raw_float32),
        "sha256": hashlib.sha256(raw_float32).hexdigest(),
    }
    patch_record = {
        "role": "fspm_patch_surface_light",
        "path": "fspm-aggregation/patch-surface-light.v1.f64le.bin",
        "media_type": "application/octet-stream",
        "byte_length": len(patch_bytes),
        "sha256": hashlib.sha256(patch_bytes).hexdigest(),
        "row_count": PATCHES_PER_PLANT,
        "stride_bytes": PATCH_STRUCT.size,
    }
    room_bytes = b"{}\n"
    (aggregation_root / "room-summary.v1.json").write_bytes(room_bytes)
    room_record = {
        "role": "fspm_room_surface_light_summary",
        "path": "fspm-aggregation/room-summary.v1.json",
        "media_type": "application/json",
        "byte_length": len(room_bytes),
        "sha256": hashlib.sha256(room_bytes).hexdigest(),
    }
    material_authorities = [
        {
            "band_id": band_id,
            "material_sha256": "1" * 64,
            "material_provenance_sha256": "2" * 64,
            "raw_receiver_sha256": "3" * 64,
        }
        for band_id in ("blue", "green", "orange", "red", "far_red")
    ]
    aggregation_metadata = {
        "ordered_artifact_inventory": [patch_record, room_record],
        "material_coefficient_authorities": material_authorities,
    }
    aggregation_bytes = _test_json_bytes(aggregation_metadata)
    (aggregation_root / "aggregation.v1.json").write_bytes(aggregation_bytes)
    transport_metadata = {
        "compact_receiver_index": {"sha256": "4" * 64}
    }
    transport_bytes = _test_json_bytes(transport_metadata)
    (transport_root / "transport.v2.json").write_bytes(transport_bytes)
    aggregation_record = {
        "sha256": hashlib.sha256(aggregation_bytes).hexdigest()
    }
    transport_record = {
        "sha256": hashlib.sha256(transport_bytes).hexdigest()
    }

    selection = select_quality_family("standard")
    assert selection is not None
    reference_contract = select_surface_flux_reference(
        operating_policy="target-controlled",
        requested_stage_a_target_ppfd_umol_m2_s=500.0,
    )
    reference = reference_contract.to_dict()
    provenance = calibration_provenance()
    counts = {
        "plants": 1,
        "leaves": 12,
        "faces": 1920,
        "patches": PATCHES_PER_PLANT,
        "receivers": 384,
    }
    bases = [float(index + 1) for index in range(PATCHES_PER_PLANT)]
    legends = {
        metric: {
            "front": build_front_legend_contract(
                metric=metric,
                profile=selection.profile,
                reference=reference_contract,
                raw_values=(
                    bases
                    if metric == "incident_par"
                    else [3.0 * value for value in bases]
                ),
                physical_areas_m2=physical_areas,
            ),
            "back": build_legend_contract(
                metric=metric,
                side="back",
                beta=selection.profile.coefficient(metric, "back"),
                reference=reference_contract,
                raw_values=(
                    [2.0 * value for value in bases]
                    if metric == "incident_par"
                    else [4.0 * value for value in bases]
                ),
                physical_areas_m2=physical_areas,
            ),
        }
        for metric in METRIC_ORDER
    }
    metadata: dict[str, object] = {
        "schema_id": SURFACE_FLUX_DISPLAY_SCHEMA_ID,
        "schema_version": SURFACE_FLUX_DISPLAY_SCHEMA_VERSION,
        "availability": "available",
        "run_id": "a" * 32,
        "profile_id": PROFILE_ID,
        "quality_family": selection.to_dict(),
        "quality_family_mapping": quality_family_mapping_contract(),
        "selected_calibration_profile": selection.profile.to_dict(),
        "calibration_provenance": provenance,
        "reference": reference,
        "display_equation": {
            "front": {
                "formula": (
                    "u[p,k,m] = q[p,k,m] / "
                    "(gamma[quality_family,m,k] * R)"
                ),
                "variable": "u",
                "q_authority": (
                    "Phase 27G-C raw Float64 PAR surface-light density"
                ),
                "gamma_index": (
                    "canonical local_patch_index k in [0, 191], reused across plants"
                ),
                "u_equals_one_meaning": (
                    "Expected-equivalent exposure for the same canonical local "
                    "patch under the fixed neutral upper-hemisphere calibration; "
                    "not a leaf target and not raw-q uniformity."
                ),
                "front_beta_used_for_coloring": False,
            },
            "back": {
                "formula": "z = q / (beta * R)",
                "variable": "z",
                "q_authority": (
                    "Phase 27G-C raw Float64 PAR surface-light density"
                ),
                "z_equals_one_meaning": (
                    "expected-equivalent neutral-reference exposure; "
                    "not a leaf target"
                ),
                "back_beta_used_for_coloring": True,
            },
            "transport_correction": False,
            "raw_q_modified": False,
        },
        "metrics": {
            "available": ["incident_par", "absorbed_par"],
            "incident_par": (
                "blue + green + orange + red incident density"
            ),
            "absorbed_par": (
                "blue + green + orange + red absorbed density"
            ),
            "far_red_excluded": True,
        },
        "palettes": palette_contract(),
        "legends": legends,
        "calibration_limitations": [
            "Open-boundary uniform-upper-hemisphere calibration.",
            "Frozen juvenile Rex topology/material scope.",
            "Direct and Rigorous remain declared quality-family proxies.",
            (
                "Local-patch normalization removes fixed morphology response "
                "but intentionally preserves plant-position, occlusion, and "
                "directional-lighting differences."
            ),
            "Back receiver calibration and palette are intentionally deferred.",
        ],
        "topology": {
            "canonical_profile_id": PROFILE_ID,
            "topology_sha256": provenance["topology_sha256"],
            "receivers_sha256": provenance["receivers_sha256"],
            "counts": counts,
            "local_patches_per_plant": PATCHES_PER_PLANT,
            "global_patch_equation": (
                "global_patch_index = 192 * plant_index + local_patch_index"
            ),
            "front_receiver_equation": (
                "384 * plant_index + 2 * local_patch_index"
            ),
            "coefficient_is_average_over_all_64_plants": True,
            "coefficient_position_dependence": False,
            "instance_mapping": "instance_id equals canonical plant_index",
            "front_back_selection": (
                "fragment gl_FrontFacing selects front or back"
            ),
            "layout_plan_hash": "c" * 64,
            "ordering": (
                "Y-major/X-minor plants; plant-major canonical patches"
            ),
        },
        "display_artifact": {
            **values_record,
            "component_type": "float32",
            "byte_order": "little-endian",
            "stride_bytes": 16,
            "row_count": PATCHES_PER_PLANT,
            "record_layout": (
                "front_incident_par, back_incident_par, "
                "front_absorbed_par, back_absorbed_par"
            ),
            "texture_layout": {
                "format": "RGBA32F",
                "width": PATCHES_PER_PLANT,
                "height": 1,
                "texel_count": PATCHES_PER_PLANT,
                "bounded_maximum_plant_count": 625,
            },
            "channel_semantics": "raw q; neither front u nor back z",
            "channel_units": "umol/m^2/s",
            "raw_q_float32_derivative": True,
            "raw_q_modified": False,
            "normalization_applied_to_artifact": False,
        },
        "scientific_artifact_boundary": {
            "aggregation_schema_id": (
                "fspm-optics.fspm-surface-light-aggregation"
            ),
            "aggregation_schema_version": 1,
            "aggregation_metadata_sha256": aggregation_record["sha256"],
            "patch_float64_artifact": patch_record,
            "room_summary_artifact": room_record,
            "transport_schema_id": (
                "fspm-optics.juvenile-multi-plant-five-band-receivers"
            ),
            "transport_schema_version": 2,
            "transport_metadata_sha256": transport_record["sha256"],
            "compact_receiver_index_sha256": "4" * 64,
            "material_coefficient_authorities": material_authorities,
            "raw_float64_values_modified": False,
            "far_red_read_into_display_values": False,
        },
        "failure_policy": {
            "invalid_or_unavailable": "neutral plant material",
            "clear_stale_gpu_resources": True,
            "partial_scientific_coloring_allowed": False,
        },
    }
    metadata_record: dict[str, object] = {
        "filename": SURFACE_FLUX_METADATA_FILENAME,
        "byte_length": 1,
        "sha256": "0" * 64,
    }
    declaration = {
        "schema_id": SURFACE_FLUX_DISPLAY_SCHEMA_ID,
        "schema_version": SURFACE_FLUX_DISPLAY_SCHEMA_VERSION,
        "metadata": metadata_record,
        "patch_values": values_record,
        "quality_family": metadata["quality_family"],
        "reference": reference,
        "display_only": True,
    }
    manifest: dict[str, object] = {
        "request": {"target_ppfd_umol_m2_s": 500.0},
        "fspm_surface_flux_display": declaration,
        "viewer_publication": {
            "surface_flux": {
                "metadata": SURFACE_FLUX_METADATA_FILENAME,
                "metadata_sha256": "0" * 64,
                "patch_values": SURFACE_FLUX_VALUES_FILENAME,
                "patch_values_sha256": values_record["sha256"],
            }
        },
        "available_visual_outputs": {"fspm_surface_flux_coloring": True},
        "artifacts": {
            "surface_flux_display_metadata": (
                "plant-layout-viewer/" + SURFACE_FLUX_METADATA_FILENAME
            ),
            "surface_flux_display_values": (
                "plant-layout-viewer/" + SURFACE_FLUX_VALUES_FILENAME
            ),
        },
        "fspm_scientific_aggregation": {
            "metadata_artifact": aggregation_record
        },
        "multispectral_transport": {
            "metadata_artifact": transport_record
        },
    }
    metrics: dict[str, object] = {
        "achieved_mean_ppfd_umol_m2_s": 499.9,
        "fspm_surface_flux_coloring": {
            "available": True,
            "display_only": True,
            "raw_scientific_values_modified": False,
            "metadata_artifact": SURFACE_FLUX_METADATA_FILENAME,
            "metadata_sha256": "0" * 64,
            "quality_family": metadata["quality_family"],
            "reference": reference,
            "metrics": ["incident_par", "absorbed_par"],
            "far_red_excluded": True,
        },
    }
    _write_promoted_metadata(
        root,
        manifest=manifest,
        metrics=metrics,
        metadata=metadata,
    )
    return manifest, metrics, metadata


def _write_promoted_metadata(
    root: Path,
    *,
    manifest: dict[str, object],
    metrics: dict[str, object],
    metadata: dict[str, object],
) -> None:
    data = _test_json_bytes(metadata)
    (root / "plant-layout-viewer" / SURFACE_FLUX_METADATA_FILENAME).write_bytes(
        data
    )
    digest = hashlib.sha256(data).hexdigest()
    declaration = manifest["fspm_surface_flux_display"]
    declaration["metadata"] = {
        "filename": SURFACE_FLUX_METADATA_FILENAME,
        "byte_length": len(data),
        "sha256": digest,
    }
    manifest["viewer_publication"]["surface_flux"][
        "metadata_sha256"
    ] = digest
    metrics["fspm_surface_flux_coloring"]["metadata_sha256"] = digest


def _test_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
